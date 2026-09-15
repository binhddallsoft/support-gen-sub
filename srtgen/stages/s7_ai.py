"""S7 - the AI layer: a proposer that is never allowed to be the author.

Rationale (plan section S7, build-spec section 7).  The architecture is
proposer / verifier / approver, and each of the three words is load-bearing:

* **AI proposes.**  It answers structured JSON and nothing else.  It never sees
  a file path, never writes ``.srt``, and cannot reach the renderer.
* **The rule engine verifies.**  Every proposed pinyin must be one of the
  readings ``pypinyin(heteronym=True)`` allows for those exact characters; every
  proposed name must literally occur in the transcript; every proposed
  punctuation mark must come from a fixed list.  Anything else is dropped and
  the reason is written down.  Verification fails *closed* - if ``pypinyin`` is
  missing there is nothing to verify against, so nothing is applied.
* **A human approves.**  Every token S7 touches gets ``FLAG_AI_APPLIED`` so the
  HTML report can list exactly what a machine decided.

Two design choices follow from the same idea - *choosing is easier than
generating*:

* T2 never asks "what is the pinyin of 得".  It sends the readings pypinyin
  already knows and asks the model to pick one.  A wrong pick is a wrong pick
  among valid readings; an invented answer would be an invalid reading, and
  those are rejected mechanically.
* T4 never asks "how should this line end".  It offers ``。？！，……`` and the
  option to do nothing at all, which is the answer for most cue breaks.

Cost control is the third concern.  Items are batched ``cfg["ai"]["batch_size"]``
at a time with neighbouring cues as context, and every *item* (not every
request) is cached under a hash of what the model was shown, in a directory
shared by every video.  A warm cache turns the second run of a similar film
into a handful of requests.

T1 is deliberately the first task and the only one that can end the stage
early: proper nouns feed ``jieba.load_userdict`` and change how S5 segments the
whole film, so when T1 finds names the honest thing is to say
``{"rerun_s5": True}`` and let the caller re-run S5/S6.  This stage never calls
another stage.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from srtgen.core import names_store
from srtgen.core.context import Context, user_cache_dir
from srtgen.core.token import (
    FLAG_AI_APPLIED,
    FLAG_CASE_AMBIG,
    FLAG_HETERONYM,
    FLAG_NAME,
    KIND_PUNCT,
    SOURCE_AI,
    Cue,
    Document,
    Token,
    is_han,
)
from srtgen.io_utils import read_json, write_json
from srtgen.providers import ProviderError, get_provider

__all__ = [
    "run",
    "STAGE_NUM",
    "STAGE_NAME",
    "TASK_T1",
    "TASK_T2",
    "TASK_T3",
    "TASK_T4",
    "TASKS",
    "TASK_LABELS",
    "END_PUNCT_CHOICES",
    "Proposal",
    "NameEntry",
    "VI_KEYS",
    "AiStats",
    "ProposalCache",
    "detect_names",
    "load_names",
    "save_names",
    "reading_options",
    "reading_candidates",
    "is_valid_reading",
]

STAGE_NUM = 7
STAGE_NAME = "ai"

# Task ids are spelled exactly as the keys under ``ai.tasks`` in default.yaml so
# that enabling a task in the config is a direct lookup, not a translation table
# somebody has to keep in sync.
TASK_T1 = "t1_names"
TASK_T2 = "t2_heteronym"
TASK_T3 = "t3_case_after_ellipsis"
TASK_T4 = "t4_end_punct"
TASKS: tuple[str, ...] = (TASK_T1, TASK_T2, TASK_T3, TASK_T4)

#: Vietnamese labels for the progress bar and the HTML report.
TASK_LABELS: dict[str, str] = {
    TASK_T1: "Tìm tên riêng trong phim",
    TASK_T2: "Chọn âm đọc cho chữ nhiều âm",
    TASK_T3: "Quyết định viết hoa sau dấu ……",
    TASK_T4: "Bổ sung dấu câu cuối câu còn thiếu",
}

#: The only punctuation T4 may add.  "none" is a first-class answer: most cue
#: breaks are mid-sentence and adding a full stop there would be damage, not a
#: fix.  ``，`` is included because the audited corpus ends 66 cues with it.
END_PUNCT_CHOICES: tuple[str, ...] = ("。", "？", "！", "，", "……")
_END_PUNCT_NONE = "none"

#: A cue whose last visible token ends with one of these already has closing
#: punctuation, so T4 never looks at it.
_CLOSERS = set("。？！…；：》”）’、，")

_CASE_UPPER = "upper"
_CASE_LOWER = "lower"

#: Defaults for keys S7 reads but ``default.yaml`` does not have to declare.
#: Every one of them exists to bound cost or to bound damage.
_DEFAULTS: dict[str, Any] = {
    "batch_size": 25,
    "context_cues": 1,          # neighbouring cues sent as read-only context
    "t1_max_cues": 200,         # plan: "toàn bộ text (hoặc 200 cue đầu)"
    "t1_max_names": 60,
    "t1_min_len": 2,            # a one-character "name" is almost always a false positive
    "t1_max_len": 8,
    "t1_rerun_s5": True,
    "max_passes": 2,            # T1 -> rerun S5 -> T2..T4.  Never a third lap.
    "t2_max_items": 120,
    "t2_max_candidates": 8,
    "t2_min_confidence": 1.0,
    "t3_max_items": 120,
    "t4_max_items": 150,
    "cache": True,
}


# --------------------------------------------------------------------------- #
# response schemas - the vendor enforces these, we re-check them anyway
# --------------------------------------------------------------------------- #

_T1_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "han": {"type": "string"},
                    "split": {"type": "array", "items": {"type": "string"}},
                    "pinyin": {"type": "array", "items": {"type": "string"}},
                    "type": {
                        "type": "string",
                        "enum": ["person", "place", "org", "work", "other"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["han", "split", "pinyin", "type"],
            },
        }
    },
    "required": ["names"],
}

_T2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "pinyin": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "pinyin"],
            },
        }
    },
    "required": ["answers"],
}

_T3_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "case": {"type": "string", "enum": [_CASE_UPPER, _CASE_LOWER]},
                    "reason": {"type": "string"},
                },
                "required": ["id", "case"],
            },
        }
    },
    "required": ["answers"],
}

_T4_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "punct": {
                        "type": "string",
                        "enum": [*END_PUNCT_CHOICES, _END_PUNCT_NONE],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["id", "punct"],
            },
        }
    },
    "required": ["answers"],
}


# --------------------------------------------------------------------------- #
# bookkeeping types
# --------------------------------------------------------------------------- #

@dataclass
class Proposal:
    """One thing the AI suggested, and what srtgen did about it.

    Rejected proposals are kept, not discarded: "the AI wanted to change this
    and we refused, here is why" is the single most useful line in the report
    when someone is deciding whether to trust the AI layer at all.
    """

    task: str
    cue_index: int
    target: str                 # human-readable position, e.g. "cụm 3 (得)"
    before: str
    after: str
    status: str = "rejected"    # applied | confirmed | rejected
    reason: str = ""            # the model's own justification, in Vietnamese
    rejected: str = ""          # our reason for refusing, in Vietnamese
    cached: bool = False

    @property
    def applied(self) -> bool:
        return self.status == "applied"

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "cue_index": self.cue_index,
            "target": self.target,
            "before": self.before,
            "after": self.after,
            "status": self.status,
            "applied": self.applied,
            "reason": self.reason,
            "rejected": self.rejected,
            "cached": self.cached,
        }


@dataclass
class NameEntry:
    """A proper noun that survived verification.

    ``vi`` is the Vietnamese spelling of the name, and it is **the only field
    the model never fills in**: it comes from a person, either by typing it in
    the web UI or by editing ``names.json`` by hand.  It has to be a real field
    rather than something a caller tacks on, because the round trip
    ``load_names -> _merge_names -> save_names`` runs after every S7 pass, and a
    field this dataclass does not know about is a field that round trip deletes.
    That is not a hypothetical: the translation glossary reads exactly this
    value (see ``s8_translate.build_glossary``), so losing it turns "Cường đầu
    trọc" back into "Guangtouqiang" one run later, with nothing on screen to say
    the name the user typed has been thrown away.
    """

    han: str
    split: list[str]
    pinyin: list[str]
    type: str = "person"
    reason: str = ""
    count: int = 0
    vi: str = ""

    def pairs(self) -> list[tuple[str, str]]:
        """``(Han cluster, capitalised pinyin)`` per cluster - the shape S6 needs."""
        return list(zip(self.split, self.pinyin))

    def to_dict(self) -> dict[str, Any]:
        return {
            "han": self.han,
            "split": list(self.split),
            "pinyin": list(self.pinyin),
            "type": self.type,
            "reason": self.reason,
            "count": self.count,
            "vi": self.vi,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "NameEntry":
        split = [str(s) for s in (d.get("split") or []) if str(s)]
        py = [str(s) for s in (d.get("pinyin") or []) if str(s)]
        han = str(d.get("han") or "".join(split))
        return cls(
            han=han,
            split=split or [han],
            pinyin=py,
            type=str(d.get("type") or "person"),
            reason=str(d.get("reason") or ""),
            count=int(d.get("count") or 0),
            vi=_vietnamese_of(d),
        )


#: Spellings of the Vietnamese field a hand-edited ``names.json`` may use.  Read
#: all of them, write only ``vi``: a user following a half-remembered example
#: should not have their work ignored because they wrote ``vietnamese``.
VI_KEYS: tuple[str, ...] = ("vi", "vietnamese", "viet", "translation")


def _vietnamese_of(data: Mapping[str, Any]) -> str:
    """The Vietnamese spelling recorded in a record, under any accepted key."""
    for key in VI_KEYS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


@dataclass
class AiStats:
    """Counters the report and the cost estimate are built from."""

    requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "cache": {"hit": self.cache_hits, "miss": self.cache_misses},
            "errors": list(self.errors),
        }


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #

class ProposalCache:
    """Per-item answer cache shared by every video.

    Keyed on *what the model was shown* - the line, the word, the candidate
    list, the task and the model name - because that is exactly the input whose
    answer we are allowed to reuse.  Change the candidate list and the key
    changes, so a stale answer can never be applied to a different question.

    Caching per item rather than per request is what makes a warm cache cheap:
    24 of 25 items hitting the cache still removes the request entirely, while a
    request-level cache would miss on any batch whose composition shifted by one
    line.
    """

    VERSION = 1

    def __init__(self, root: Path | str, *, enabled: bool = True) -> None:
        self.root = Path(root)
        self.enabled = bool(enabled)
        self.hits = 0
        self.misses = 0
        self._buckets: dict[str, dict[str, Any]] = {}
        self._dirty: set[str] = set()

    # -- keys ---------------------------------------------------------- #

    @staticmethod
    def make_key(task: str, model: str, payload: Mapping[str, Any]) -> str:
        blob = json.dumps(
            {"task": task, "model": model, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    # -- storage ------------------------------------------------------- #

    def _bucket_name(self, task: str, model: str) -> str:
        safe_model = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in model or "none")
        return f"{task}.{safe_model}"

    def _bucket_path(self, bucket: str) -> Path:
        return self.root / f"{bucket}.json"

    def _bucket(self, bucket: str) -> dict[str, Any]:
        if bucket in self._buckets:
            return self._buckets[bucket]
        data: dict[str, Any] = {}
        path = self._bucket_path(bucket)
        try:
            if path.is_file():
                raw = read_json(path)
                if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                    data = dict(raw["entries"])
        except (OSError, ValueError):
            # A corrupt cache is a cache miss, never a crash: the file is
            # regenerable and the user should not have to delete anything.
            data = {}
        self._buckets[bucket] = data
        return data

    def get(self, task: str, model: str, key: str) -> Any | None:
        if not self.enabled:
            self.misses += 1
            return None
        entry = self._bucket(self._bucket_name(task, model)).get(key)
        if isinstance(entry, dict) and "value" in entry:
            self.hits += 1
            return entry["value"]
        self.misses += 1
        return None

    def put(self, task: str, model: str, key: str, value: Any) -> None:
        if not self.enabled:
            return
        bucket = self._bucket_name(task, model)
        self._bucket(bucket)[key] = {"value": value, "at": int(time.time())}
        self._dirty.add(bucket)

    def flush(self) -> None:
        """Merge back onto disk.

        Re-reading before writing means a second SrtGen window that warmed the
        same bucket does not lose its entries.  Failure to write is ignored on
        purpose - losing a cache costs a few requests, and refusing to finish a
        subtitle file because a cache directory is read-only would be absurd.
        """
        if not self.enabled:
            return
        for bucket in sorted(self._dirty):
            merged: dict[str, Any] = {}
            path = self._bucket_path(bucket)
            try:
                if path.is_file():
                    raw = read_json(path)
                    if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                        merged.update(raw["entries"])
            except (OSError, ValueError):
                merged = {}
            merged.update(self._buckets.get(bucket, {}))
            try:
                write_json(path, {"version": self.VERSION, "entries": merged})
            except OSError:
                pass
        self._dirty.clear()


# --------------------------------------------------------------------------- #
# pinyin verification - the "verifier" half of proposer/verifier
# --------------------------------------------------------------------------- #

def reading_options(zh: str) -> list[list[str]]:
    """Every reading ``pypinyin`` allows, one list per character.

    Returns ``[]`` when pypinyin is unavailable, which makes every verification
    below fail closed.  That is the intended behaviour: with no ground truth to
    check against, applying a model's answer would be pure trust.
    """
    if not zh:
        return []
    try:
        from pypinyin import Style, pinyin
    except ImportError:  # pragma: no cover - depends on install
        return []
    try:
        return [list(opts) for opts in pinyin(zh, style=Style.TONE, heteronym=True)]
    except Exception:  # pragma: no cover - pypinyin never raises on plain text
        return []


def default_reading(zh: str) -> str:
    """What S5 itself would produce - the answer to beat."""
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:  # pragma: no cover - depends on install
        return ""
    try:
        return "".join(lazy_pinyin(zh, style=Style.TONE))
    except Exception:  # pragma: no cover
        return ""


def _norm_py(text: str) -> str:
    """Fold away the differences that are not disagreements.

    Case and the apostrophe in ``Xī'ān`` carry no phonetic information, so
    comparing them would reject correct answers.  Tone marks are *not* folded:
    a wrong tone is a wrong reading.
    """
    return "".join(ch for ch in (text or "").strip().lower() if ch not in " '’-")


def is_valid_reading(zh: str, py: str) -> bool:
    """Is ``py`` a reading pypinyin allows for ``zh``?

    This single predicate is what stops a hallucinated pinyin from reaching the
    file.  The one tolerated departure is the contracted 儿化 form the README
    mandates (``哪儿`` -> ``nǎr``): pypinyin spells it ``nǎér``, so a trailing
    ``r`` is matched against the syllable before ``儿``.
    """
    target = _norm_py(py)
    if not zh or not target:
        return False
    options = reading_options(zh)
    if not options:
        return False
    if _match_options(options, 0, target, 0):
        return True
    # Second chance only for 儿化: the uncontracted spelling is tried first
    # above, so this branch cannot turn a genuinely wrong reading into a
    # passing one - it only accepts ``nǎr`` where pypinyin says ``nǎér``.
    if zh.endswith("儿") and len(zh) > 1 and target.endswith("r"):
        head = reading_options(zh[:-1])
        return bool(head) and _match_options(head, 0, target[:-1], 0)
    return False


def _match_options(options: list[list[str]], idx: int, target: str, pos: int) -> bool:
    """Depth-first match of one reading per character against the whole string."""
    if idx == len(options):
        return pos == len(target)
    for option in options[idx]:
        syllable = _norm_py(option)
        if syllable and target.startswith(syllable, pos):
            if _match_options(options, idx + 1, target, pos + len(syllable)):
                return True
    return False


def reading_candidates(zh: str, *, limit: int = 8, current: str | None = None) -> list[str]:
    """The menu T2 asks the model to choose from, lower-case, best guess first.

    The list is a pure function of ``zh``: the pypinyin default leads, then the
    remaining combinations in pypinyin's own frequency order.  ``current`` only
    ever *appends* a reading the pure list does not already contain.

    That "append, never insert" rule is what makes the task idempotent and the
    cache useful.  The candidate list is part of the cache key, so a menu whose
    order shifted every time the file changed would miss the cache on every
    re-run, and a second pass could keep flipping a token between two readings.
    A model with no opinion picks the first item, and the first item is the
    reading S5 already produced - so indecision costs nothing.
    """
    options = reading_options(zh)
    if not options:
        return []
    cap = max(2, limit)
    out: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        cleaned = _norm_py(value)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)

    add(default_reading(zh))
    for combo in itertools.product(*options):
        if len(out) >= cap:
            break
        add("".join(combo))
    del out[cap:]
    # A reading already in the file that fell outside the cap still deserves to
    # be offerable, otherwise "leave it alone" would not be on the menu.
    if current and _norm_py(current) not in seen and is_valid_reading(zh, current):
        add(current)
    return out


# --------------------------------------------------------------------------- #
# names.json
# --------------------------------------------------------------------------- #

#: Legacy key for the rich block.  Older files written by this module put the
#: entries under ``_meta`` beside the Han keys; still read, no longer written.
NAMES_META_KEY = "_meta"


def load_names(path: Path | str) -> tuple[dict[str, str], dict[str, Any]]:
    """Read ``names.json`` leniently and return ``(flat, meta)``.

    Four shapes are accepted, because this file is written by S7, read by S5 and
    S6, exposed by the web UI, and edited by hand: the wrapper srtgen writes
    (``{"names": {...}, "entries": [...]}``), a bare ``{Han: Pinyin}`` map, a
    nested ``{Han: {"pinyin": ...}}`` map, and the older ``_meta`` layout.
    Being strict here would mean one hand-edit silently disabling proper nouns.

    The file itself is read by :mod:`srtgen.core.names_store` (H4), the only
    module allowed to touch ``names.json``.  A broken file comes back as an
    empty table *here*, which is right for S5, S6 and S8 (one missing comma
    must not stop a 40-minute run) and exactly wrong for anything about to
    write: "empty because broken" and "empty because new" are the same value
    in this return type.  Writers ask ``names_store.load_names_file(path)``
    and look at ``.corrupt`` first, as :func:`run` does.
    """
    return _names_from_payload(names_store.load_names_file(path).data)


def _names_from_payload(raw: Any) -> tuple[dict[str, str], dict[str, Any]]:
    """Parse an already-read ``names.json`` payload into ``(flat, meta)``.

    Split out of :func:`load_names` so that T1 reads the file once: whether it
    is broken and what it contains must come from the same read, or a save
    landing in between could make the two answers disagree.
    """
    if not isinstance(raw, Mapping):
        return {}, {}

    body = raw.get("names")
    if isinstance(body, dict):
        meta = {k: v for k, v in raw.items() if k != "names"}
    else:
        body = raw
        legacy = raw.get(NAMES_META_KEY)
        meta = dict(legacy) if isinstance(legacy, dict) else {}

    flat: dict[str, str] = {}
    hand_vi: dict[str, str] = {}
    for key, value in body.items():
        if key == NAMES_META_KEY or not isinstance(key, str) or not key:
            continue
        if isinstance(value, str):
            flat[key] = value
        elif isinstance(value, Mapping):
            pinyin = value.get("pinyin")
            if isinstance(pinyin, str):
                flat[key] = pinyin
            elif isinstance(pinyin, (list, tuple)):
                flat[key] = "".join(str(p) for p in pinyin)
            vietnamese = _vietnamese_of(value)
            if vietnamese:
                hand_vi[key] = vietnamese
    return flat, _fold_vietnamese(meta, hand_vi)


def _fold_vietnamese(meta: dict[str, Any], hand_vi: Mapping[str, str]) -> dict[str, Any]:
    """Move a hand-written ``vi`` out of the name map and into ``entries``.

    Why normalise here instead of teaching every reader about a second place to
    look: ``entries`` is the shape ``_merge_names`` merges and ``save_names``
    writes, so anything living outside it is deleted by the next S7 run.  A user
    who opens ``names.json`` and types ``"光头强": {"pinyin": "...", "vi": "Cường
    đầu trọc"}`` has done something entirely reasonable, and the only honest
    answers are to keep their edit or to refuse it out loud.  Keeping it costs
    these few lines.
    """
    if not hand_vi:
        return meta
    rows = [dict(r) for r in (meta.get("entries") or []) if isinstance(r, Mapping)]
    seen = {str(r.get("han") or ""): r for r in rows}
    for han, vietnamese in hand_vi.items():
        row = seen.get(han)
        if row is None:
            rows.append({"han": han, "split": [han], "pinyin": [], "vi": vietnamese})
        elif not _vietnamese_of(row):
            row["vi"] = vietnamese
    out = dict(meta)
    out["entries"] = rows
    return out


def save_names(
    path: Path | str,
    flat: Mapping[str, str],
    entries: Sequence[NameEntry],
    *,
    meta: Mapping[str, Any] | None = None,
) -> Path | None:
    """Write the table under a ``names`` key, with the rich block beside it.

    The wrapper is not decoration - it is what keeps the file readable by every
    consumer.  ``srtgen.stages.s5_tokenize.load_names`` unwraps ``names`` and
    treats *every other top-level key as a proper noun*, so putting the split
    table, the version and the provenance at the top level would invent names
    called "version" and "entries".  Inside ``names`` the value is always a
    plain pinyin string, because ``apply_casing(doc, names, cfg)`` and S6's
    ``apply_names`` both do ``names[token.zh]`` and expect exactly that.

    Returns where a broken ``names.json`` was set aside to
    (``names.json.hong-<YYYYmmdd-HHMMSS>``), or ``None`` when the file on disk
    was fine or absent.  The write belongs to
    :func:`srtgen.core.names_store.save_names_file`: it renames a broken file
    out of the way *before* writing, and raises
    :class:`~srtgen.core.names_store.NamesSaveRefused` (an ``OSError``) without
    writing anything when it cannot.  S7 used to ``write_json`` straight over
    whatever was there, which is how one missing comma in a hand edit cost a
    user every name they had typed, with nothing on screen saying so.
    """
    return names_store.save_names_file(
        path,
        {
            "version": 1,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            **(dict(meta) if meta else {}),
            "names": {k: v for k, v in sorted(flat.items())},
            "entries": [e.to_dict() for e in entries],
        },
    )


def _check_userdict(names_file: Path, userdict_path: Path) -> bool:
    """Prove the table just saved loads into S5's tokenizer; ``True`` when it does.

    S7 used to keep a dictionary writer and loader of its own, and they had
    both bugs S5's :func:`~srtgen.stages.s5_tokenize.install_userdict` was
    fixed for: they skipped one-character clusters and never loaded the whole
    name, so 光头强 (光头 | 强) still came out as 光头 | 强来 | 了; and they
    taught jieba's **global** dictionary, which in the web UI's long-lived
    process leaks one film's names into the segmentation of every film after
    it.  Here the table is read back with S5's own readers and handed to
    :func:`~srtgen.stages.s5_tokenize.make_segmenter`, which builds a private
    tokenizer and writes ``userdict_path`` in exactly the format S5 loads -
    one implementation, so the two can never disagree about a name again.

    The re-run of S5 that follows builds its own segmenter from
    ``names.json``; this call is the check that it will be able to, recorded as
    ``names.jieba_loaded`` in ``S7_ai.json``.
    """
    from srtgen.stages.s5_tokenize import load_name_splits, make_segmenter
    from srtgen.stages.s5_tokenize import load_names as s5_load_names

    try:
        _cut, count = make_segmenter(
            s5_load_names(names_file),
            splits=load_name_splits(names_file),
            userdict_path=userdict_path,
        )
    except Exception:  # jieba missing, unwritable folder: S7 must never cost the run
        return False
    return count > 0


def _names_unreadable(names_file: Path) -> bool:
    """``names.json`` exists but cannot be used - half-saved, mistyped, or not a table.

    Asked of :mod:`srtgen.core.names_store` rather than S5's ``names_hash``:
    the fingerprint files a document that parses but is not a table (a list,
    a ``null``) under "no names", and T1 used to save straight over such a
    file.  One definition of "broken" for the server and for S7.
    """
    return names_store.load_names_file(names_file).corrupt


def _backup_notice(backup: Path, count: int) -> str:
    """The sentence a user reads after T1 set their broken table aside.

    It names the copy and its folder because "đã cất" without a place is a
    promise nobody can check, and it says plainly that the new table holds
    only the AI's names, so someone who typed forty names does not assume they
    were merged in.
    """
    return (
        "Bảng tên riêng cũ (names.json) bị lỗi nên tool không đọc được. "
        f"Tool đã cất nguyên vẹn file đó thành “{backup.name}” trong thư mục "
        f"{backup.parent}, không xoá gì, rồi mới ghi bảng tên mới ({count} tên do AI "
        "vừa tìm). Các tên bạn gõ trước đây vẫn nằm trong file cất đó. Tên nào còn "
        "thiếu thì thêm lại ở tab Tên riêng."
    )


def _tell_names_problem(
    stats: AiStats,
    on_progress: Callable[[str, float], None] | None,
    *,
    kind: str,
    message: str,
    user_message: str,
) -> None:
    """Record a ``names.json`` event where every screen looks, and say it now.

    Two channels on purpose: ``errors`` stays in ``S7_ai.json`` for whatever
    reads the stage result later, and the progress line reaches the job log
    the moment it happens.  Losing a table silently is the failure this whole
    round of work exists to prevent; being told twice is cheap.
    """
    stats.errors.append({"kind": kind, "message": message, "user_message": user_message})
    _progress(on_progress, user_message, 0.3)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _cfg_ai(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    section = (cfg or {}).get("ai")
    return dict(section) if isinstance(section, Mapping) else {}


def _opt(ai: Mapping[str, Any], key: str) -> Any:
    value = ai.get(key, _DEFAULTS.get(key))
    return _DEFAULTS.get(key) if value is None else value


def _int_opt(ai: Mapping[str, Any], key: str) -> int:
    try:
        return int(_opt(ai, key))
    except (TypeError, ValueError):
        return int(_DEFAULTS.get(key, 0))


def _float_opt(ai: Mapping[str, Any], key: str) -> float:
    try:
        return float(_opt(ai, key))
    except (TypeError, ValueError):
        return float(_DEFAULTS.get(key, 0.0))


def _bool_opt(ai: Mapping[str, Any], key: str) -> bool:
    value = _opt(ai, key)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _task_enabled(ai: Mapping[str, Any], task: str) -> bool:
    tasks = ai.get("tasks")
    if not isinstance(tasks, Mapping):
        return True
    value = tasks.get(task, True)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _progress(cb: Callable[[str, float], None] | None, message: str, fraction: float) -> None:
    """Report progress without ever letting a UI callback kill the pipeline."""
    if cb is None:
        return
    try:
        cb(message, max(0.0, min(1.0, fraction)))
    except Exception:  # pragma: no cover - callback belongs to the caller
        pass


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    step = max(1, size)
    for i in range(0, len(items), step):
        yield items[i : i + step]


def _ask(provider: Any, model: str, prompt: str, schema: dict, stats: AiStats) -> dict:
    """One request, with every failure downgraded to "no proposals".

    S7 is an improvement pass on a file that is already correct.  A provider
    failure must therefore cost the user nothing but the improvement - never the
    run - so the error is recorded for the report and the batch is skipped.
    """
    stats.requests += 1
    try:
        answer = provider.complete_json(prompt, schema, model=model)
    except ProviderError as err:
        stats.errors.append(err.to_dict())
        return {}
    except Exception as err:  # a third-party provider breaking its contract
        stats.errors.append(
            {
                "kind": "unknown",
                "message": f"{type(err).__name__}: {err}",
                "user_message": "Có lỗi khi gọi AI, tool bỏ qua lô này và chạy tiếp.",
            }
        )
        return {}
    return answer if isinstance(answer, dict) else {}


def _answers_by_id(payload: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    """Index a batch answer by item id, tolerating the two shapes models emit."""
    rows = payload.get("answers")
    if not isinstance(rows, list):
        rows = payload.get("items") if isinstance(payload.get("items"), list) else []
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            key = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        out[key] = dict(row)
    return out


def _clip(text: str, limit: int = 120) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _context_lines(doc: Document, index: int, span: int) -> tuple[str, str]:
    """The cue before and after, as rendered Chinese - context, never edited."""
    before = doc.cues[index - 1].zh_text() if span > 0 and index > 0 else ""
    after = doc.cues[index + 1].zh_text() if span > 0 and index + 1 < len(doc.cues) else ""
    return before, after


def _is_upper_pinyin(pinyin: str | None) -> bool:
    for ch in pinyin or "":
        if ch.isalpha():
            return ch.isupper()
    return False


def _apply_case(pinyin: str, upper: bool) -> str:
    from srtgen.core.casing import capitalize_pinyin, lower_pinyin

    return capitalize_pinyin(pinyin) if upper else lower_pinyin(pinyin)


def _last_visible(cue: Cue) -> Token | None:
    for tok in reversed(cue.tokens):
        content = (tok.zh or "").strip()
        if content:
            return tok
    return None


# --------------------------------------------------------------------------- #
# T1 - proper nouns for the whole film
# --------------------------------------------------------------------------- #

_T1_PROMPT = """You are proof-reading Chinese subtitles for one film or episode.

Below are subtitle lines, one per line. List the PROPER NOUNS that occur in
them: person names, place names, organisations, and titles of works.

For every proper noun return:
  "han"    - the proper noun copied exactly from the text below
  "split"  - how it must be split into subtitle clusters. A Chinese surname is
             always its own cluster: 陈路周 -> ["陈", "路周"]
  "pinyin" - one tone-marked syllable group per element of "split", first letter
             capitalised: ["Chén", "Lùzhōu"]
  "type"   - person | place | org | work | other
  "reason" - at most 12 words, WRITTEN IN VIETNAMESE

Hard rules:
  * Only list strings that literally appear in the text below. Never invent one.
  * "".join(split) must equal "han" exactly.
  * len(pinyin) must equal len(split).
  * Do not list common nouns, pronouns, or generic forms of address
    (老板, 医生, 妈妈, 先生).
  * At most {max_names} entries, most frequent first.
  * If there are no proper nouns, return an empty list.

TEXT:
{text}
"""


def detect_names(
    doc: Document,
    provider: Any,
    ai: Mapping[str, Any],
    *,
    cache: ProposalCache | None = None,
    stats: AiStats | None = None,
    model: str = "",
) -> tuple[list[NameEntry], list[Proposal]]:
    """T1: ask once for the whole film, then verify every answer.

    Exposed separately from :func:`run` because ``srtgen names <video_id>`` is a
    command of its own - the user may want the proper-noun table refreshed
    without paying for a full pass.
    """
    stats = stats or AiStats()
    max_cues = _int_opt(ai, "t1_max_cues")
    max_names = _int_opt(ai, "t1_max_names")
    lines = [c.zh_text() for c in doc.cues[: max(1, max_cues)] if c.zh_text().strip()]
    if not lines:
        return [], []

    text = "\n".join(lines)
    # The transcript a name is checked against is the cues' own characters, not
    # ``zh_text()``: that one is *rendered*, with a space between clusters, so a
    # name jieba had cut apart (光头强 -> 光头 | 强来) was "not in the transcript"
    # and got rejected - exactly the names T1 exists to find.  One line per cue
    # so that the end of one cue and the start of the next never pass for a name.
    full_text = "\n".join("".join(t.zh for t in c.tokens) for c in doc.cues)
    payload_key = {"text_sha": hashlib.sha256(text.encode("utf-8")).hexdigest(), "max": max_names}
    key = ProposalCache.make_key(TASK_T1, model, payload_key)

    raw: Any = None
    cached = False
    if cache is not None:
        raw = cache.get(TASK_T1, model, key)
        cached = raw is not None
    if raw is None:
        prompt = _T1_PROMPT.format(max_names=max_names, text=text)
        answer = _ask(provider, model, prompt, _T1_SCHEMA, stats)
        raw = answer.get("names") if isinstance(answer.get("names"), list) else answer.get("items")
        if isinstance(raw, list) and raw and cache is not None:
            cache.put(TASK_T1, model, key, raw)
    if not isinstance(raw, list):
        return [], []

    entries: list[NameEntry] = []
    proposals: list[Proposal] = []
    seen: set[str] = set()
    for row in raw[: max(1, max_names) * 2]:
        if not isinstance(row, Mapping):
            continue
        entry, refusal = _verify_name(row, full_text, ai)
        han = str(row.get("han") or "")
        proposal = Proposal(
            task=TASK_T1,
            cue_index=0,
            target=_clip(han, 24),
            before="",
            after=" ".join(entry.pinyin) if entry else "",
            reason=str(row.get("reason") or "")[:120],
            cached=cached,
        )
        if entry is None:
            proposal.rejected = refusal
            proposals.append(proposal)
            continue
        if entry.han in seen:
            proposal.rejected = "Tên bị lặp trong danh sách AI trả về."
            proposals.append(proposal)
            continue
        seen.add(entry.han)
        entry.count = full_text.count(entry.han)
        proposal.status = "applied"
        proposals.append(proposal)
        entries.append(entry)
        if len(entries) >= max_names:
            break
    return entries, proposals


def _verify_name(
    row: Mapping[str, Any], full_text: str, ai: Mapping[str, Any]
) -> tuple[NameEntry | None, str]:
    """Reject anything we cannot prove.  Returns ``(entry, vietnamese_reason)``."""
    han = str(row.get("han") or "").strip()
    min_len = _int_opt(ai, "t1_min_len")
    max_len = _int_opt(ai, "t1_max_len")

    if not han or not all(is_han(ch) for ch in han):
        return None, "Không phải chuỗi chữ Hán nên bỏ qua."
    if not (min_len <= len(han) <= max_len):
        return None, f"Độ dài {len(han)} chữ nằm ngoài khoảng cho phép."
    if han not in full_text:
        # The single most valuable check: a name that is not in the transcript
        # was imagined by the model.
        return None, "Không tìm thấy tên này trong lời thoại nên bỏ qua."

    split = [str(s).strip() for s in (row.get("split") or []) if str(s).strip()]
    if not split:
        split = [han]
    if "".join(split) != han:
        return None, "Cách tách không ghép lại thành đúng tên gốc."
    if any(not all(is_han(ch) for ch in part) for part in split):
        return None, "Cách tách có phần không phải chữ Hán."

    pinyin = [str(p).strip() for p in (row.get("pinyin") or []) if str(p).strip()]
    if len(pinyin) != len(split):
        return None, "Số cụm pinyin không khớp số cụm chữ Hán."

    fixed: list[str] = []
    for cluster, reading in zip(split, pinyin):
        if not is_valid_reading(cluster, reading):
            return None, f"Pinyin “{_clip(reading, 24)}” không phải cách đọc hợp lệ của “{cluster}”."
        fixed.append(_apply_case(reading, True))

    kind = str(row.get("type") or "person").strip().lower()
    if kind not in {"person", "place", "org", "work", "other"}:
        kind = "other"
    return (
        NameEntry(
            han=han,
            split=split,
            pinyin=fixed,
            type=kind,
            reason=str(row.get("reason") or "")[:120],
        ),
        "",
    )


def _merge_names(
    existing: Mapping[str, str],
    existing_meta: Mapping[str, Any],
    entries: Sequence[NameEntry],
    *,
    ai_overrides: bool,
) -> tuple[dict[str, str], list[NameEntry], list[str]]:
    """Fold the AI's table into the one already on disk.

    ``cfg["names"]["merge_ai_result"]`` decides who wins a collision.  Hand
    edits are the reason the flag exists: a user who fixed a name once should
    not have to fix it again after every run, so with the flag off the existing
    spelling stands and the AI may only add names nobody has ruled on.

    ``vi`` is outside that argument entirely and is **always** carried over.
    The model is never asked for a Vietnamese name and so never proposes one;
    an incoming entry's ``vi`` is empty not because the AI disagrees with the
    user but because the AI has no opinion.  Letting "no opinion" overwrite the
    name a person typed is how the field disappeared before, and the user only
    found out when the subtitles came back calling the character
    "Guangtouqiang" again.
    """
    flat = dict(existing)
    previous: list[NameEntry] = [
        NameEntry.from_dict(d)
        for d in (existing_meta.get("entries") or [])
        if isinstance(d, Mapping)
    ]
    kept: dict[str, NameEntry] = {e.han: e for e in previous if e.han}
    added: list[str] = []

    for entry in entries:
        changed = False
        for cluster, reading in entry.pairs():
            if cluster in flat and not ai_overrides:
                continue
            if flat.get(cluster) != reading:
                flat[cluster] = reading
                changed = True
        known = kept.get(entry.han)
        if known is not None and known.vi and not entry.vi:
            entry = replace(entry, vi=known.vi)
        if known is None:
            changed = True
        elif ai_overrides and known.to_dict() != entry.to_dict():
            changed = True
        if known is None or ai_overrides:
            kept[entry.han] = entry
        if changed:
            added.append(entry.han)

    ordered = sorted(kept.values(), key=lambda e: (-e.count, e.han))
    # A name can be flagged twice (new spelling *and* new metadata); the caller
    # only wants to know which names moved, not how many ways they moved.
    return flat, ordered, list(dict.fromkeys(added))


# --------------------------------------------------------------------------- #
# T2 - heteronym readings
# --------------------------------------------------------------------------- #

_T2_PROMPT = """You are proof-reading pinyin under Chinese subtitles.

For each item below, one Chinese word has several possible readings. Choose the
reading that is correct IN THIS SENTENCE. Copy one string from "options"
exactly - do not invent a reading and do not change the tone marks.

Give "reason" in VIETNAMESE, at most 12 words.
If the current reading is already right, return it unchanged.

ITEMS (JSON):
{items}
"""


def _collect_t2_items(doc: Document, ai: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Pick the tokens actually worth an API call.

    Every token containing 了 would qualify on flags alone, which would turn a
    3-6 request task into fifty.  So a token only makes the list when the
    candidate menu really has more than one entry, single-character words go
    first (that is where pypinyin's word dictionary cannot help), and the whole
    list is capped.
    """
    min_conf = _float_opt(ai, "t2_min_confidence")
    limit = _int_opt(ai, "t2_max_candidates")
    max_items = _int_opt(ai, "t2_max_items")
    span = _int_opt(ai, "context_cues")

    picked: list[dict[str, Any]] = []
    for c_idx, cue in enumerate(doc.cues):
        line = cue.zh_text()
        for t_idx, tok in enumerate(cue.tokens):
            if not tok.is_word() or not tok.zh:
                continue
            if not tok.pinyin:
                # No reading yet means CUM_MISMATCH wiped it; S5 regenerates
                # those.  Letting the AI fill the gap here would also have to
                # guess the capitalisation, which is S6's decision, not its own.
                continue
            if not (tok.has_flag(FLAG_HETERONYM) or tok.confidence < min_conf):
                continue
            if tok.has_flag(FLAG_NAME):
                continue  # names.json owns these spellings, not the model
            options = reading_candidates(tok.zh, limit=limit, current=tok.pinyin)
            if len(options) < 2:
                continue
            before, after = _context_lines(doc, c_idx, span)
            picked.append(
                {
                    "cue": c_idx,
                    "tok": t_idx,
                    "word": tok.zh,
                    "current": tok.pinyin or "",
                    "options": options,
                    "line": line,
                    "prev": before,
                    "next": after,
                    "priority": (len(tok.zh), tok.confidence),
                }
            )
    picked.sort(key=lambda item: item["priority"])
    return picked[: max(0, max_items)]


def _run_t2(
    doc: Document,
    provider: Any,
    ai: Mapping[str, Any],
    model: str,
    cache: ProposalCache,
    stats: AiStats,
    on_progress: Callable[[str, float], None] | None,
    base: float,
    span_frac: float,
) -> list[Proposal]:
    items = _collect_t2_items(doc, ai)
    if not items:
        return []
    answers = _resolve_batches(
        items,
        task=TASK_T2,
        model=model,
        schema=_T2_SCHEMA,
        provider=provider,
        cache=cache,
        stats=stats,
        ai=ai,
        cache_payload=lambda it: {
            "line": it["line"],
            "word": it["word"],
            "tok": it["tok"],
            "options": it["options"],
        },
        wire_payload=lambda i, it: {
            "id": i,
            "sentence": it["line"],
            "context_before": it["prev"],
            "context_after": it["next"],
            "word": it["word"],
            "current": it["current"],
            "options": it["options"],
        },
        prompt_template=_T2_PROMPT,
        on_progress=on_progress,
        label=TASK_LABELS[TASK_T2],
        base=base,
        span_frac=span_frac,
    )

    proposals: list[Proposal] = []
    for pos, item in enumerate(items):
        answer, cached = answers.get(pos, (None, False))
        if not isinstance(answer, Mapping):
            continue
        tok = doc.cues[item["cue"]].tokens[item["tok"]]
        chosen = _norm_py(str(answer.get("pinyin") or ""))
        proposal = Proposal(
            task=TASK_T2,
            cue_index=doc.cues[item["cue"]].index,
            target=f"cụm {item['tok'] + 1} ({item['word']})",
            before=item["current"],
            after="",
            reason=str(answer.get("reason") or "")[:120],
            cached=cached,
        )
        if chosen not in item["options"]:
            # Belt and braces: the schema already restricted the shape, this
            # restricts the content.  An answer outside the menu is a made-up
            # reading and is dropped without touching the document.
            proposal.rejected = (
                f"AI trả “{_clip(str(answer.get('pinyin') or ''), 24)}”, "
                "không nằm trong các cách đọc hợp lệ."
            )
            proposals.append(proposal)
            continue
        final = _apply_case(chosen, _is_upper_pinyin(tok.pinyin))
        proposal.after = final
        if final == (tok.pinyin or ""):
            proposal.status = "confirmed"
            proposals.append(proposal)
            continue
        tok.pinyin = final
        tok.source = SOURCE_AI
        tok.add_flag(FLAG_AI_APPLIED)
        proposal.status = "applied"
        proposals.append(proposal)
    return proposals


# --------------------------------------------------------------------------- #
# T3 - capitalisation after ......
# --------------------------------------------------------------------------- #

_T3_PROMPT = """You are proof-reading pinyin under Chinese subtitles.

The previous subtitle line ended with the Chinese ellipsis "……". That can mean
the sentence continues into this line (then this line stays lower case) or that
a new sentence starts here (then its first pinyin syllable is capitalised).

For each item choose "upper" or "lower" for the FIRST pinyin group of the
current line. Give "reason" in VIETNAMESE, at most 12 words.

ITEMS (JSON):
{items}
"""


def _collect_t3_items(doc: Document, ai: Mapping[str, Any]) -> list[dict[str, Any]]:
    max_items = _int_opt(ai, "t3_max_items")
    picked: list[dict[str, Any]] = []
    for c_idx, cue in enumerate(doc.cues):
        for t_idx, tok in enumerate(cue.tokens):
            if not tok.is_word() or not tok.zh:
                continue
            if tok.has_flag(FLAG_CASE_AMBIG) and tok.pinyin:
                prev = doc.cues[c_idx - 1] if c_idx > 0 else None
                picked.append(
                    {
                        "cue": c_idx,
                        "tok": t_idx,
                        "word": tok.zh,
                        "current": _CASE_UPPER if _is_upper_pinyin(tok.pinyin) else _CASE_LOWER,
                        "line": cue.zh_text(),
                        "line_py": cue.py_text(),
                        "prev": prev.zh_text() if prev else "",
                        "prev_py": prev.py_text() if prev else "",
                    }
                )
            break  # only the first word of a cue can carry CASE_AMBIG
    return picked[: max(0, max_items)]


def _run_t3(
    doc: Document,
    provider: Any,
    ai: Mapping[str, Any],
    model: str,
    cache: ProposalCache,
    stats: AiStats,
    on_progress: Callable[[str, float], None] | None,
    base: float,
    span_frac: float,
) -> list[Proposal]:
    items = _collect_t3_items(doc, ai)
    if not items:
        return []
    answers = _resolve_batches(
        items,
        task=TASK_T3,
        model=model,
        schema=_T3_SCHEMA,
        provider=provider,
        cache=cache,
        stats=stats,
        ai=ai,
        cache_payload=lambda it: {"prev": it["prev"], "line": it["line"], "word": it["word"]},
        wire_payload=lambda i, it: {
            "id": i,
            "previous_line": it["prev"],
            "previous_pinyin": it["prev_py"],
            "current_line": it["line"],
            "current_pinyin": it["line_py"],
            "first_word": it["word"],
            "current_case": it["current"],
        },
        prompt_template=_T3_PROMPT,
        on_progress=on_progress,
        label=TASK_LABELS[TASK_T3],
        base=base,
        span_frac=span_frac,
    )

    proposals: list[Proposal] = []
    for pos, item in enumerate(items):
        answer, cached = answers.get(pos, (None, False))
        if not isinstance(answer, Mapping):
            continue
        tok = doc.cues[item["cue"]].tokens[item["tok"]]
        choice = str(answer.get("case") or "").strip().lower()
        proposal = Proposal(
            task=TASK_T3,
            cue_index=doc.cues[item["cue"]].index,
            target=f"chữ đầu câu ({item['word']})",
            before=tok.pinyin or "",
            after="",
            reason=str(answer.get("reason") or "")[:120],
            cached=cached,
        )
        if choice not in (_CASE_UPPER, _CASE_LOWER):
            proposal.rejected = "AI không trả lời rõ viết hoa hay viết thường."
            proposals.append(proposal)
            continue
        final = _apply_case(tok.pinyin or "", choice == _CASE_UPPER)
        proposal.after = final
        if final == (tok.pinyin or ""):
            # Agreement is not an edit.  Flagging it would put 17 untouched
            # tokens on the reviewer's list and teach them to ignore the flag.
            proposal.status = "confirmed"
            proposals.append(proposal)
            continue
        tok.pinyin = final
        tok.add_flag(FLAG_AI_APPLIED)
        proposal.status = "applied"
        proposals.append(proposal)
    return proposals


# --------------------------------------------------------------------------- #
# T4 - missing sentence-final punctuation
# --------------------------------------------------------------------------- #

_T4_PROMPT = """You are proof-reading Chinese subtitles.

Each item is one subtitle line that currently ends with no punctuation, plus
the line that follows it. Decide what belongs at the end of the CURRENT line:

  "。"  the sentence ends here
  "？"  it is a question
  "！"  it is an exclamation
  "，"  it pauses but continues into the next line
  "……"  it trails off
  "none" nothing at all - the sentence simply runs on into the next line

"none" is the right answer whenever the next line continues the same clause.
Do not add punctuation just to make a line look finished.
Give "reason" in VIETNAMESE, at most 12 words.

ITEMS (JSON):
{items}
"""


def _collect_t4_items(doc: Document, ai: Mapping[str, Any]) -> list[dict[str, Any]]:
    max_items = _int_opt(ai, "t4_max_items")
    picked: list[dict[str, Any]] = []
    for c_idx, cue in enumerate(doc.cues):
        last = _last_visible(cue)
        if last is None:
            continue  # empty cue: EMPTY_CUE is a validator problem, not an AI one
        if last.kind == KIND_PUNCT and last.zh and last.zh[-1] in _CLOSERS:
            continue
        if last.kind != KIND_PUNCT and not last.is_word():
            continue  # a cue ending on a marker is malformed; leave it alone
        nxt = doc.cues[c_idx + 1] if c_idx + 1 < len(doc.cues) else None
        picked.append(
            {
                "cue": c_idx,
                "line": cue.zh_text(),
                "next": nxt.zh_text() if nxt else "",
            }
        )
    return picked[: max(0, max_items)]


def _run_t4(
    doc: Document,
    provider: Any,
    ai: Mapping[str, Any],
    model: str,
    cache: ProposalCache,
    stats: AiStats,
    on_progress: Callable[[str, float], None] | None,
    base: float,
    span_frac: float,
) -> tuple[list[Proposal], list[int]]:
    items = _collect_t4_items(doc, ai)
    if not items:
        return [], []
    answers = _resolve_batches(
        items,
        task=TASK_T4,
        model=model,
        schema=_T4_SCHEMA,
        provider=provider,
        cache=cache,
        stats=stats,
        ai=ai,
        cache_payload=lambda it: {"line": it["line"], "next": it["next"]},
        wire_payload=lambda i, it: {
            "id": i,
            "current_line": it["line"],
            "next_line": it["next"],
        },
        prompt_template=_T4_PROMPT,
        on_progress=on_progress,
        label=TASK_LABELS[TASK_T4],
        base=base,
        span_frac=span_frac,
    )

    proposals: list[Proposal] = []
    changed: list[int] = []
    for pos, item in enumerate(items):
        answer, cached = answers.get(pos, (None, False))
        if not isinstance(answer, Mapping):
            continue
        cue = doc.cues[item["cue"]]
        choice = str(answer.get("punct") or "").strip()
        proposal = Proposal(
            task=TASK_T4,
            cue_index=cue.index,
            target="cuối câu",
            before=_clip(item["line"], 60),
            after="",
            reason=str(answer.get("reason") or "")[:120],
            cached=cached,
        )
        if choice in (_END_PUNCT_NONE, ""):
            proposal.status = "confirmed"
            proposal.after = _clip(item["line"], 60)
            proposals.append(proposal)
            continue
        if choice not in END_PUNCT_CHOICES:
            proposal.rejected = f"Dấu “{_clip(choice, 8)}” không nằm trong danh sách cho phép."
            proposals.append(proposal)
            continue
        cue.tokens.append(
            Token(kind=KIND_PUNCT, zh=choice, pinyin=None, source=SOURCE_AI, flags=[FLAG_AI_APPLIED])
        )
        proposal.after = _clip(cue.zh_text(), 60)
        proposal.status = "applied"
        proposals.append(proposal)
        changed.append(item["cue"])
    return proposals, changed


def _recase_after(doc: Document, changed: Sequence[int]) -> list[Proposal]:
    """Re-apply the casing rule to the cue after every line T4 just closed.

    S6 decided capitalisation from the punctuation that existed then.  Adding a
    full stop at the end of cue *i* changes the correct answer for cue *i+1*,
    and leaving it stale would mean S7 fixed one rule by breaking another.  The
    repair is deliberately narrow: only the immediately following cue, and never
    a token a human or another task already owns (a name, or a T3 decision).
    """
    from srtgen.core.casing import CASE_CONT, CASE_ENDERS

    proposals: list[Proposal] = []
    for c_idx in changed:
        nxt_idx = c_idx + 1
        if nxt_idx >= len(doc.cues):
            continue
        last = _last_visible(doc.cues[c_idx])
        if last is None:
            continue
        ender = (last.zh or "")[-1:]
        if ender in CASE_ENDERS:
            want_upper = True
        elif ender in CASE_CONT:
            want_upper = False
        else:
            continue
        cue = doc.cues[nxt_idx]
        first = next((t for t in cue.tokens if t.is_word() and t.zh), None)
        if first is None or not first.pinyin:
            continue
        if first.has_flag(FLAG_NAME) or first.has_flag(FLAG_AI_APPLIED):
            continue
        final = _apply_case(first.pinyin, want_upper)
        if final == first.pinyin:
            continue
        proposals.append(
            Proposal(
                task=TASK_T4,
                cue_index=cue.index,
                target=f"chữ đầu câu ({first.zh})",
                before=first.pinyin,
                after=final,
                status="applied",
                reason="Viết hoa lại cho khớp dấu câu vừa thêm ở câu trước.",
            )
        )
        first.pinyin = final
        first.add_flag(FLAG_AI_APPLIED)
    return proposals


# --------------------------------------------------------------------------- #
# batching + cache plumbing shared by T2/T3/T4
# --------------------------------------------------------------------------- #

def _resolve_batches(
    items: Sequence[dict[str, Any]],
    *,
    task: str,
    model: str,
    schema: dict,
    provider: Any,
    cache: ProposalCache,
    stats: AiStats,
    ai: Mapping[str, Any],
    cache_payload: Callable[[dict[str, Any]], dict[str, Any]],
    wire_payload: Callable[[int, dict[str, Any]], dict[str, Any]],
    prompt_template: str,
    on_progress: Callable[[str, float], None] | None,
    label: str,
    base: float,
    span_frac: float,
) -> dict[int, tuple[Any, bool]]:
    """Answer every item, from cache where possible and from the model otherwise.

    The cache is consulted per item *before* batches are formed, so a run where
    24 of 25 items are already known sends one request with a single item rather
    than a full request that re-asks 24 known questions.  Item ids sent on the
    wire are the item's position in ``items``, which keeps the mapping back
    unambiguous even when a model reorders or drops rows.
    """
    batch_size = _int_opt(ai, "batch_size")
    resolved: dict[int, tuple[Any, bool]] = {}
    pending: list[int] = []
    keys: dict[int, str] = {}

    for pos, item in enumerate(items):
        key = ProposalCache.make_key(task, model, cache_payload(item))
        keys[pos] = key
        hit = cache.get(task, model, key)
        if hit is not None:
            resolved[pos] = (hit, True)
        else:
            pending.append(pos)

    if not pending:
        _progress(on_progress, f"{label}: dùng lại kết quả đã lưu", base + span_frac)
        return resolved

    batches = list(_chunks(pending, batch_size))
    for n, batch in enumerate(batches, start=1):
        _progress(
            on_progress,
            f"{label}: đang hỏi AI ({n}/{len(batches)})",
            base + span_frac * (n - 1) / max(1, len(batches)),
        )
        wire = [wire_payload(pos, items[pos]) for pos in batch]
        prompt = prompt_template.format(
            items=json.dumps(wire, ensure_ascii=False, indent=1)
        )
        answer = _ask(provider, model, prompt, schema, stats)
        rows = _answers_by_id(answer)
        for pos in batch:
            row = rows.get(pos)
            if row is None:
                continue
            resolved[pos] = (row, False)
            cache.put(task, model, keys[pos], row)
    _progress(on_progress, f"{label}: xong", base + span_frac)
    return resolved


# --------------------------------------------------------------------------- #
# document loading
# --------------------------------------------------------------------------- #

def _load_document(ctx: Context) -> Document:
    """Take the document from the context, or from S6, or from S5.

    Falling back to S5 matters for ``srtgen resume ... --from s7`` on a run whose
    S6 output was thrown away: an un-normalised document is still better input
    than a crash, and S9 validates everything afterwards regardless.
    """
    if isinstance(ctx.doc, Document) and ctx.doc.cues:
        return ctx.doc
    for stage, name in ((6, "norm"), (5, "tokens")):
        raw = ctx.load_stage(stage, name)
        doc = _document_from_stage(raw)
        if doc is not None and doc.cues:
            return doc
    raise RuntimeError(
        "Chưa có dữ liệu của bước trước để chạy bước AI. "
        "Hãy chạy lại từ bước “Tách cụm và sinh pinyin”."
    )


def _document_from_stage(raw: Any) -> Document | None:
    """Accept the bare document or any stage wrapper that carries one."""
    if not isinstance(raw, Mapping):
        return None
    if isinstance(raw.get("cues"), list):
        return Document.from_stage_dict(dict(raw))
    for key in ("document", "doc"):
        inner = raw.get(key)
        if isinstance(inner, Mapping) and isinstance(inner.get("cues"), list):
            return Document.from_stage_dict(dict(inner))
    return None


def _names_path(ctx: Context) -> Path:
    """Where ``names.json`` lives - S5's rule, not a second copy of it.

    S5 fingerprints this file to decide whether a saved result is still valid
    and S8 reads its glossary from it; a second copy of the path rule drifting
    by one ``strip()`` would have T1 writing one file while the others watch
    another.
    """
    from srtgen.stages.s5_tokenize import names_path

    return names_path(ctx)


# --------------------------------------------------------------------------- #
# the stage
# --------------------------------------------------------------------------- #

def run(ctx: Context, on_progress: Callable[[str, float], None] | None = None) -> dict:
    """Run the AI layer over the document produced by S6.

    Returns the dict that is also written to ``work/<id>/S7_ai.json``.  The one
    key a caller must act on is ``rerun_s5``: when T1 added proper nouns, the
    jieba dictionary has changed and the whole film should be segmented again.
    S7 checks that the new table loads with S5's own segmenter (a private
    tokenizer, never jieba's global one) but never runs S5 - a stage that calls
    another stage turns a pipeline into a maze.
    """
    _progress(on_progress, "Bước AI: kiểm tra kết quả đã có", 0.02)

    if ctx.has_stage(STAGE_NUM, STAGE_NAME):
        cached = ctx.load_stage(STAGE_NUM, STAGE_NAME)
        if isinstance(cached, Mapping):
            doc = _document_from_stage(cached)
            if doc is not None:
                ctx.doc = doc
            result = dict(cached)
            result["resumed"] = True
            # Forced off: the saved file proves S7 already finished for this
            # document.  Handing a caller ``rerun_s5=True`` from a resume would
            # invite an endless S5 -> S7 -> S5 loop.
            result["rerun_s5"] = False
            _progress(on_progress, "Bước AI: dùng lại kết quả đã lưu", 1.0)
            return result

    doc = _load_document(ctx)
    cfg = ctx.cfg or {}
    ai = _cfg_ai(cfg)
    names_cfg = cfg.get("names") if isinstance(cfg.get("names"), Mapping) else {}

    provider = get_provider(cfg)
    model_t1 = str(ai.get("model_t1") or ai.get("model") or "")
    model_other = str(ai.get("model_other") or ai.get("model") or "")
    stats = AiStats()
    cache = ProposalCache(user_cache_dir() / "ai", enabled=_bool_opt(ai, "cache"))

    pass_no = int(ctx.meta.get("s7_pass") or 0) + 1
    ctx.meta["s7_pass"] = pass_no
    max_passes = _int_opt(ai, "max_passes")

    proposals: list[Proposal] = []
    names_path = _names_path(ctx)
    names_report: dict[str, Any] = {
        "enabled": _task_enabled(ai, TASK_T1),
        "file": str(names_path),
        "found": 0,
        "added": [],
        "total": 0,
        "userdict": "",
        "jieba_loaded": False,
        "corrupt": False,
        "backup": "",
    }
    rerun_s5 = False
    skipped: list[str] = []

    # -- T1 ------------------------------------------------------------- #
    if not names_report["enabled"] or ctx.is_cancelled():
        skipped.append(TASK_T1)
    else:
        _progress(on_progress, f"{TASK_LABELS[TASK_T1]}…", 0.05)
        entries, t1_props = detect_names(
            doc, provider, ai, cache=cache, stats=stats, model=model_t1
        )
        proposals.extend(t1_props)
        names_report["found"] = len(entries)
        # One read answers both questions - "is the file broken?" and "what is
        # in it?" - so the first answer cannot go stale before the second.
        loaded = names_store.load_names_file(names_path)
        names_report["corrupt"] = loaded.corrupt
        existing_flat, existing_meta = _names_from_payload(loaded.data)
        flat, merged, added = _merge_names(
            existing_flat,
            existing_meta,
            entries,
            ai_overrides=bool(names_cfg.get("merge_ai_result", True)),
        )
        names_report["added"] = added
        names_report["total"] = len(merged)
        if added or (entries and not existing_flat):
            # A broken table reads as empty, so it lands here whenever the AI
            # found anything.  save_names sets the broken file aside first
            # (renamed, byte for byte) and only then writes; the user is told
            # where the old names went.  Refusing to write instead, as S7 once
            # did, cost the film the AI's names AND left a file that a
            # non-programmer cannot repair by hand.
            try:
                backup = save_names(
                    names_path,
                    flat,
                    merged,
                    meta={"video_id": ctx.video_id, "source": f"ai:{model_t1 or provider.name}"},
                )
            except names_store.NamesSaveRefused as err:
                # Broken and could not be set aside: nothing was written, so S5
                # has nothing new to segment with.
                added = []
                names_report["added"] = []
                _tell_names_problem(
                    stats,
                    on_progress,
                    kind="names_refused",
                    message=str(err),
                    user_message=err.user_message,
                )
            except OSError as err:
                added = []
                names_report["added"] = []
                _tell_names_problem(
                    stats,
                    on_progress,
                    kind="io",
                    message=str(err),
                    user_message=(
                        "Không ghi được bảng tên riêng vào thư mục làm việc. "
                        "Các bước còn lại vẫn chạy bình thường."
                    ),
                )
            else:
                if backup is not None:
                    names_report["backup"] = str(backup)
                    _tell_names_problem(
                        stats,
                        on_progress,
                        kind="names_backup",
                        message=f"corrupt names.json set aside as {backup}",
                        user_message=_backup_notice(backup, len(merged)),
                    )
                userdict = names_path.with_name(names_path.stem + "_userdict.txt")
                names_report["userdict"] = str(userdict)
                names_report["jieba_loaded"] = _check_userdict(names_path, userdict)
        elif loaded.corrupt:
            # Nothing new to write, so nothing is set aside either: the broken
            # file stays exactly as the user left it.  Its names are still not
            # being used, though, and that has to be said out loud.
            _tell_names_problem(
                stats,
                on_progress,
                kind="names_unreadable",
                message=f"names.json unreadable: {names_path}",
                user_message=(
                    f"{loaded.message or ''} AI không tìm thêm được tên nào nên tool "
                    "chưa đụng tới file đó."
                ).strip(),
            )
        rerun_s5 = bool(added) and _bool_opt(ai, "t1_rerun_s5") and pass_no < max_passes

    # -- T2..T4 --------------------------------------------------------- #
    changed_cues: list[int] = []
    if rerun_s5:
        # Deliberate early exit.  New proper nouns change how jieba segments the
        # entire film, so every reading, every capitalisation and every cue this
        # pass could fix is about to be regenerated.  Spending requests on them
        # now would be paying twice for the same answers.
        skipped.extend((TASK_T2, TASK_T3, TASK_T4))
        _progress(on_progress, "Đã tìm được tên riêng mới, cần tách lại cụm từ", 0.9)
    else:
        steps = [
            (TASK_T2, _run_t2),
            (TASK_T3, _run_t3),
            (TASK_T4, _run_t4),
        ]
        base = 0.35
        span = 0.6 / len(steps)
        for task, handler in steps:
            if not _task_enabled(ai, task):
                skipped.append(task)
                base += span
                continue
            if ctx.is_cancelled():
                skipped.append(task)
                base += span
                continue
            outcome = handler(
                doc, provider, ai, model_other, cache, stats, on_progress, base, span
            )
            if task == TASK_T4:
                task_props, changed_cues = outcome  # type: ignore[misc]
            else:
                task_props = outcome  # type: ignore[assignment]
            proposals.extend(task_props)
            base += span
        if changed_cues:
            proposals.extend(_recase_after(doc, changed_cues))

    cache.flush()
    stats.cache_hits = cache.hits
    stats.cache_misses = cache.misses
    ctx.doc = doc

    payload = _build_payload(
        ctx=ctx,
        doc=doc,
        provider=provider,
        ai=ai,
        model_t1=model_t1,
        model_other=model_other,
        stats=stats,
        proposals=proposals,
        names_report=names_report,
        rerun_s5=rerun_s5,
        pass_no=pass_no,
        skipped=skipped,
    )
    ctx.save_stage(STAGE_NUM, STAGE_NAME, payload)
    _progress(on_progress, _summary_line(payload), 1.0)
    return payload


def _build_payload(
    *,
    ctx: Context,
    doc: Document,
    provider: Any,
    ai: Mapping[str, Any],
    model_t1: str,
    model_other: str,
    stats: AiStats,
    proposals: Sequence[Proposal],
    names_report: Mapping[str, Any],
    rerun_s5: bool,
    pass_no: int,
    skipped: Sequence[str],
) -> dict[str, Any]:
    """Assemble ``S7_ai.json``.

    It carries the document as well as the proposal log so that a resume has
    everything it needs from one file - a stage whose output depends on reading
    an earlier stage's file too is a stage that breaks the first time somebody
    cleans a work directory.
    """
    by_task: dict[str, dict[str, int]] = {}
    for task in TASKS:
        rows = [p for p in proposals if p.task == task]
        by_task[task] = {
            "proposed": len(rows),
            "applied": sum(1 for p in rows if p.status == "applied"),
            "confirmed": sum(1 for p in rows if p.status == "confirmed"),
            "rejected": sum(1 for p in rows if p.status == "rejected"),
            "from_cache": sum(1 for p in rows if p.cached),
            "enabled": _task_enabled(ai, task),
            "label": TASK_LABELS[task],
        }
    reason = getattr(provider, "reason", "")
    return {
        "stage": "s7_ai",
        "pass": pass_no,
        "provider": getattr(provider, "name", "null"),
        "provider_reason": reason,
        "enabled": bool(ai.get("enabled", False)),
        "model_t1": model_t1,
        "model_other": model_other,
        "batch_size": _int_opt(ai, "batch_size"),
        "rerun_s5": rerun_s5,
        "skipped_tasks": list(skipped),
        "requests": stats.requests,
        "cache": {"hit": stats.cache_hits, "miss": stats.cache_misses},
        "errors": list(stats.errors),
        "tasks": by_task,
        "names": dict(names_report),
        "proposals": [p.to_dict() for p in proposals],
        "flagged_tokens": sum(
            1 for cue in doc.cues for t in cue.tokens if t.has_flag(FLAG_AI_APPLIED)
        ),
        "document": doc.to_stage_dict(),
        "video_id": ctx.video_id,
    }


def _summary_line(payload: Mapping[str, Any]) -> str:
    """The one sentence the progress bar ends on, in plain Vietnamese.

    A broken names table set aside during this pass is repeated here because
    this is the line that stays on screen when the step ends; a notice that
    lives only in the scrolling log is a notice nobody reads.
    """
    line = _summary_counts(payload)
    names = payload.get("names")
    backup = str(names.get("backup") or "") if isinstance(names, Mapping) else ""
    if backup:
        line = f"{line} Bảng tên cũ bị lỗi đã được cất nguyên vẹn thành “{Path(backup).name}”."
    return line


def _summary_counts(payload: Mapping[str, Any]) -> str:
    """What the AI step did, counted - the body of :func:`_summary_line`."""
    if payload.get("provider") == "null":
        return f"Bỏ qua bước AI. {payload.get('provider_reason', '')}".strip()
    if payload.get("rerun_s5"):
        found = len(payload.get("names", {}).get("added", []))
        return f"AI tìm được {found} tên riêng mới, sẽ tách lại cụm từ cho cả phim."
    applied = sum(t.get("applied", 0) for t in payload.get("tasks", {}).values())
    rejected = sum(t.get("rejected", 0) for t in payload.get("tasks", {}).values())
    cache = payload.get("cache", {})
    return (
        f"AI đề xuất và đã áp {applied} chỗ, bỏ {rejected} chỗ "
        f"({payload.get('requests', 0)} lượt hỏi, {cache.get('hit', 0)} lần dùng lại kết quả cũ)."
    )
