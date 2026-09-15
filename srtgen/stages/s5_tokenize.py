"""S5 — segmentation and pinyin generation: the stage that decides the clusters.

Why this stage is the one that matters
--------------------------------------
Everything the README asks for about cluster alignment ("mỗi cụm Chinese phải
khớp trực tiếp với cụm pinyin ở dòng dưới") is settled here and nowhere else.
The pipeline never produces the two lines separately, so the only question left
is *where the cluster boundaries go*, and that question is answered exactly
once, in this stage, for both lines at the same time.

The six steps of build-spec section 7 (S5), in the order they must run:

1. punctuation is split off into its own tokens **first** — before jieba ever
   sees the text, so a full stop can never end up glued inside a word;
2. jieba cuts the remaining Han runs, with ``names.json`` loaded as a user
   dictionary so proper nouns survive: each *whole* name goes in as one
   high-frequency word so no cut can run through it, and the piece is then
   re-split exactly as ``names.json`` splits it (光头强 -> 光头 | 强);
3. :func:`~srtgen.core.erhua.merge_erhua` folds a stray ``儿`` back into the
   word in front of it;
4. pinyin is generated **one token at a time**;
5. :func:`~srtgen.core.sandhi.apply_sandhi` fixes the tone sandhi that only
   shows up across a token boundary;
6. tokens whose reading is genuinely undecided get ``FLAG_HETERONYM`` and a
   lowered confidence, which is the queue S7's T2 task works from.

Why pinyin is generated per token and never per line
-----------------------------------------------------
``lazy_pinyin`` uses a phrase dictionary.  Handed a whole sentence it will
happily produce the right readings — and then there is no honest way to cut the
result back into clusters, because a phrase's pinyin is not a concatenation of
its characters' pinyin (一个 is ``yígè``, not ``yīgè``).  Handed one token it
uses that token as its phrase context, which is precisely the context the
cluster represents.  So the call is made per token: the word 不是 yields
``búshì`` because pypinyin knows the word, while a lone 不 followed by a
separate word is left for :mod:`srtgen.core.sandhi` to resolve.  Calling
pypinyin on the line and slicing the output afterwards is the single design
mistake this whole rewrite exists to avoid.

Why ``merge_erhua`` is called twice
------------------------------------
Step 3 runs before pinyin exists, so it can only merge the *characters*
(点 | 儿 -> 点儿).  pypinyin then reads that merged word as two syllables
(``diǎnér``), which the README forbids.  The second call contracts the
spelling (``diǎnr``).  ``merge_erhua`` is idempotent and tolerates
``pinyin=None``, which is what makes this safe rather than clever.

Why there is a reading-preference table
----------------------------------------
pypinyin answers with the *dictionary* reading; a film subtitle wants the
*spoken* one.  The two disagree for a handful of characters, and every
disagreement costs a human the same edit on every film: 谁 comes back as
``shuí`` where the reviewed corpus writes ``shéi`` 13 times out of 13, and
``rules.py`` itself quotes "谁 đọc shéi" as an example of a contextually correct
reading.  Rather than patching one character in one place, step 4 consults
:func:`reading_prefs` — a table whose only home is ``tokenize.reading_prefs`` in
``config/default.yaml``.  The table is deliberately small and only holds
characters whose spoken reading is *not* in doubt; a genuinely context-dependent
character (得 = děi/dé, 着 = zhe/zhuó) belongs in the ``HETERONYM`` review queue,
because forcing one reading on it would be wrong a third of the time.

Why jieba runs with its HMM switched off
----------------------------------------
jieba has two layers: a dictionary, and an HMM that *invents* words out of
characters the dictionary does not join.  On film dialogue the second layer
makes up words that do not exist — 我来中国只有一个目的 comes out as
我来 | 中国 where the reviewer wrote 我 | 来, and 光头强来了 grows a 强来.
Measured over the whole corpus (``raw.srt`` through ``fix``, compared with
``completed.srt`` on the 1312 cues not listed in ``known_corpus_errors``): HMM on
matches the reviewed Han line 75.8% of the time and the pinyin line 74.4%; HMM
off matches 78.4% / 76.8%, with the format gates still at zero errors.  So the
stage cuts with ``HMM=False``, and the choice lives in ``tokenize.jieba_hmm`` in
``config/default.yaml`` (:func:`jieba_hmm`) rather than in the code.

The editor's "Sinh lại pinyin cho câu này" button must cut a line exactly the way
this stage cut it — a button that re-splits differently from the pipeline hands
the user a third segmentation to argue with.  :func:`segment_line` is that one
shared cutter: same name loading, same re-split of whole names, same HMM switch.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Mapping, Sequence

from srtgen.core.context import Context
from srtgen.core.erhua import merge_erhua
from srtgen.core.sandhi import apply_sandhi
from srtgen.core.srt import tokenize_line
from srtgen.core.token import (
    FLAG_HETERONYM,
    KIND_WORD,
    SOURCE_ASR,
    SOURCE_JIEBA,
    Cue,
    Document,
    Token,
    is_han,
    word_count,
)
from srtgen.io_utils import nfc, read_json, write_text
# The cancellation signal is shared, not re-invented: ``srtgen.stages`` re-exports
# this exact class, so it is the one the CLI and the web UI will be catching.  A
# second class with the same name would slip past their ``except`` and surface to
# a non-technical user as a crash instead of as "đã dừng".
from srtgen.stages.s0_fetch import StageCancelled

__all__ = [
    "STAGE",
    "STAGE_NAME",
    "SOURCE_STAGE",
    "SOURCE_STAGE_NAME",
    "HETERONYM_CHARS",
    "HETERONYM_CONFIDENCE",
    "HMM_KEY",
    "READING_PREFS_KEY",
    "USERDICT_FILE",
    "NAMES_HASH_KEY",
    "NO_NAMES_HASH",
    "NAMES_CHANGED_MESSAGE",
    "NAME_WORD_FREQ",
    "ReadingPrefs",
    "StageCancelled",
    "ProgressFn",
    "report_progress",
    "stop_if_cancelled",
    "cut_words",
    "default_reading_prefs",
    "fill_pinyin",
    "flag_heteronyms",
    "install_userdict",
    "jieba_hmm",
    "load_name_splits",
    "load_names",
    "make_segmenter",
    "names_hash",
    "names_path",
    "redo_from_stage",
    "stored_names_hash",
    "pinyin_of",
    "reading_prefs",
    "run",
    "segment_line",
    "segment_tokens",
    "split_punctuation",
    "tokenize_document",
]


# --------------------------------------------------------------------------- #
# stage identity
# --------------------------------------------------------------------------- #

STAGE: Final[int] = 5
STAGE_NAME: Final[str] = "tokens"          # -> work/<id>/S5_tokens.json
SOURCE_STAGE: Final[int] = 4
SOURCE_STAGE_NAME: Final[str] = "cues"     # <- work/<id>/S4_cues.json

#: Generated next to the stage files so a curious user can open it, and so the
#: file jieba reads is one we wrote through :mod:`srtgen.io_utils` rather than
#: a path we hand it blindly.
USERDICT_FILE: Final[str] = "jieba_userdict.txt"

#: Frequency a *whole* proper noun (光头强) is loaded into jieba with.  Chosen
#: to sit at the level of an everyday word — jieba's own table gives 我们
#: 98 740 out of ~60 million — so that no competing cut through the name can
#: outbid it.  The competitor is usually not even a dictionary word: 强来 in
#: 光头强来了 is invented by jieba's HMM from two leftover characters, and it
#: wins today only because the name is not in the dictionary at all.  Higher
#: would buy nothing and start to pull characters out of real words next to
#: the name; measured on jieba 0.42, a name 小明 loaded at this value still
#: leaves 从小明白 cut as 从小 | 明白.
NAME_WORD_FREQ: Final[int] = 100_000

#: Characters whose reading depends on meaning.  Baseline from build-spec
#: section 7; ``cfg["tokenize"]["heteronym_chars"]`` *extends* it rather than
#: replacing it, because this list only drives a review queue — a list that is
#: too long costs a few minutes of review, a list that is too short ships a
#: wrong reading silently.
HETERONYM_CHARS: Final[str] = "得着了长行还差数重分少相假干空乐曲便尽中"

#: Confidence left on a token whose reading is still open (build-spec S5.6).
HETERONYM_CONFIDENCE: Final[float] = 0.5

#: Config key holding the reading-preference table, under ``tokenize``.  The
#: table itself lives in ``config/default.yaml`` and nowhere else — see the
#: module docstring for why it is a table rather than a patch.
READING_PREFS_KEY: Final[str] = "reading_prefs"

#: Config key, under ``tokenize``, that switches jieba's HMM layer on or off.
#: See the module docstring for the measurement behind ``false``.
HMM_KEY: Final[str] = "jieba_hmm"

#: What :func:`jieba_hmm` answers when neither the caller's config nor the
#: packaged ``default.yaml`` says anything — the measured better setting.
_DEFAULT_HMM: Final[bool] = False

#: Han beyond the basic block.  ``token.is_han`` deliberately covers only
#: U+4E00..U+9FFF because the S4 length statistics are calibrated on it; for
#: "does this token need pypinyin at all" the wider ranges matter, since a
#: single rare ideograph would otherwise be mistaken for a Latin word and get
#: its own characters back as pinyin.
_HAN_EXTRA: Final[tuple[tuple[str, str], ...]] = (
    ("㐀", "䶿"),   # extension A
    ("豈", "﫿"),   # compatibility ideographs
)

_PROGRESS_EVERY: Final[int] = 25   # cues between two progress reports


# --------------------------------------------------------------------------- #
# progress / cancellation plumbing
# --------------------------------------------------------------------------- #

ProgressFn = Callable[[str, float], None]


def report_progress(on_progress: ProgressFn | None, message: str, fraction: float) -> None:
    """Send one progress update, and never let the UI's callback kill the run.

    The callback comes from the web layer, so an exception in it means a broken
    browser connection — losing forty minutes of ASR over that would be absurd.
    """
    if on_progress is None:
        return
    try:
        on_progress(message, min(1.0, max(0.0, float(fraction))))
    except Exception:  # pragma: no cover - defensive, callback is not ours
        pass


def stop_if_cancelled(cancelled: Callable[[], bool] | None) -> None:
    """Raise :class:`StageCancelled` if the user asked the job to stop.

    Shared with S6 so that both stages honour the Dừng button the same way,
    and raising the class ``srtgen.stages`` re-exports so the UI's ``except``
    actually catches it.
    """
    if cancelled is None:
        return
    try:
        stop = bool(cancelled())
    except Exception:  # pragma: no cover - same reasoning as report_progress
        return
    if stop:
        raise StageCancelled("Đã dừng theo yêu cầu của bạn.")


# --------------------------------------------------------------------------- #
# lazy heavy imports
# --------------------------------------------------------------------------- #
#
# jieba and pypinyin are imported inside the functions that need them so that
# ``srtgen doctor`` and the web UI still start on a machine where nothing is
# installed yet - that is the whole point of having a doctor command.

_JIEBA: dict[str, Any] = {}
_PYPINYIN: dict[str, Any] = {}


def _missing(package: str, err: Exception) -> RuntimeError:
    return RuntimeError(
        f"Máy chưa cài thư viện “{package}”, nên chưa tách từ được.\n"
        f"Hãy chạy lệnh: pip install {package}\n"
        f"(Chi tiết kỹ thuật: {err})"
    )


def _jieba(on_progress: ProgressFn | None = None) -> Any:
    """Import jieba, silence its logger, and build its prefix dictionary once.

    The dictionary build takes about a second and jieba does it lazily on the
    first cut.  Doing it here, on purpose, means the user is told what the
    pause is instead of watching a frozen progress bar.  jieba also logs
    "Building prefix dict..." to stderr, which would land in the middle of the
    UI log, hence the log level.
    """
    if _JIEBA:
        return _JIEBA["module"]
    try:
        import jieba  # type: ignore[import-untyped]
    except ImportError as err:
        raise _missing("jieba", err) from err
    try:
        jieba.setLogLevel(logging.WARNING)
    except Exception:  # pragma: no cover - older jieba without the helper
        pass
    report_progress(on_progress, "Đang chuẩn bị từ điển tách từ (mất khoảng 1 giây)…", 0.0)
    jieba.initialize()
    _JIEBA["module"] = jieba
    return jieba


def _pypinyin() -> dict[str, Any]:
    if _PYPINYIN:
        return _PYPINYIN
    try:
        from pypinyin import Style, lazy_pinyin, pinyin  # type: ignore[import-untyped]
    except ImportError as err:
        raise _missing("pypinyin", err) from err
    _PYPINYIN.update({"Style": Style, "lazy_pinyin": lazy_pinyin, "pinyin": pinyin})
    return _PYPINYIN


# --------------------------------------------------------------------------- #
# names.json
# --------------------------------------------------------------------------- #

def _section(cfg: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    """One config block, or an empty one.

    ``default.yaml`` is a plain text file a user may well open and mistype in.
    A malformed block should cost them the block's defaults, not an
    AttributeError halfway through a forty-minute run.
    """
    value = (cfg or {}).get(name)
    return value if isinstance(value, Mapping) else {}


def names_path(ctx: Context) -> Path:
    """Where this film's proper-noun table lives.

    ``paths.names_dir`` empty means "next to the working files", which is what
    a non-technical user expects: one folder per video holding everything about
    that video.
    """
    cfg = ctx.cfg or {}
    paths = _section(cfg, "paths")
    names_cfg = _section(cfg, "names")
    folder = str(paths.get("names_dir") or "").strip()
    filename = str(names_cfg.get("file") or "names.json").strip() or "names.json"
    base = Path(folder) if folder else ctx.work_dir
    return base / filename


def load_names(path: str | Path) -> dict[str, str]:
    """Read ``names.json`` into ``{chữ Hán: pinyin}``; ``{}`` when there is none.

    The file is written by S7's T1 task but is also meant to be edited by hand,
    so every shape a person might reasonably produce is accepted: a plain
    mapping, a mapping under a ``names`` key, a list of strings, or a list of
    records.  A missing or damaged file is not an error — it only means jieba
    runs without a user dictionary, which is the normal first pass.
    """
    file = Path(path)
    try:
        if not file.is_file():
            return {}
        data = read_json(file)
    except (OSError, ValueError):
        return {}
    return _names_from_payload(data)


def _names_from_payload(data: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(data, Mapping):
        inner = data.get("names")
        if isinstance(inner, (Mapping, list)):
            return _names_from_payload(inner)
        for key, value in data.items():
            zh = str(key).strip()
            if zh:
                out[zh] = _name_pinyin(value)
        return out
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                zh = item.strip()
                if zh:
                    out.setdefault(zh, "")
            elif isinstance(item, Mapping):
                zh = str(
                    item.get("zh") or item.get("text") or item.get("name") or ""
                ).strip()
                if zh:
                    out[zh] = _name_pinyin(item.get("pinyin"))
    return out


def _name_pinyin(value: Any) -> str:
    """The spelling recorded for a name, or ``""`` when only the word is known."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return str(value.get("pinyin") or "").strip()
    return ""


def load_name_splits(path: str | Path) -> dict[str, list[str]]:
    """Read ``{whole name: [cluster, ...]}`` from ``names.json``; ``{}`` when none.

    :func:`load_names` answers a different question — which *clusters* are
    names and how each one is spelled — and its keys are clusters (光头, 强)
    because that is what S6 matches tokens against.  That map alone cannot
    protect a name whose split has a one-character cluster: 强 is too short to
    be worth suggesting, 光头强 itself is not a key, and so 光头强来了 kept
    coming out as 光头 | 强来 | 了 — a cluster straddling the name and the verb,
    which no later stage can repair because the Han line is already cut.

    The whole name and its split are recorded in ``entries`` (written by S7 and
    by the web UI's name tab, also under the older ``_meta`` wrapper), or next
    to the pinyin in a hand-edited ``{"光头强": {"split": [...]}}``, so they are
    read from there.  Lenient like :func:`load_names` — a missing, damaged or
    oddly shaped file means "no whole names", never an exception forty minutes
    into a run — but strict on one point: a split whose clusters do not spell
    the name exactly is dropped, not guessed at.
    """
    file = Path(path)
    try:
        if not file.is_file():
            return {}
        data = read_json(file)
    except (OSError, ValueError):
        return {}
    return _splits_from_payload(data)


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _splits_from_payload(data: Any) -> dict[str, list[str]]:
    """Collect name records from every layout ``names.json`` is known to take."""
    records: list[Any] = []
    if isinstance(data, list):
        records.extend(data)
    elif isinstance(data, Mapping):
        records.extend(_as_list(data.get("entries")))
        legacy = data.get("_meta")
        if isinstance(legacy, Mapping):
            records.extend(_as_list(legacy.get("entries")))
        inner = data.get("names")
        body = inner if isinstance(inner, (Mapping, list)) else data
        if isinstance(body, list):
            records.extend(body)
        else:
            for key, value in body.items():
                if isinstance(value, Mapping) and "split" in value:
                    records.append({**value, "han": key})

    out: dict[str, list[str]] = {}
    for item in records:
        if not isinstance(item, Mapping):
            continue
        han = str(
            item.get("han") or item.get("zh") or item.get("text") or item.get("name") or ""
        ).strip()
        clusters = _clean_split(han, item.get("split"))
        if clusters:
            # First record wins: ``entries`` comes first and is the table S7 and
            # the UI maintain, a stray nested copy must not override it.
            out.setdefault(han or "".join(clusters), clusters)
    return out


def _clean_split(han: str, split: Any) -> list[str] | None:
    """The clusters of one name, or ``None`` when they cannot be trusted.

    A split is only usable if its clusters spell the name exactly: loading
    光头强 and then cutting it into something that is not 光头强 would change
    the subtitle text itself.  A space inside a cluster is refused too, since
    jieba's dictionary format is space-separated and would read it as a
    frequency column.
    """
    if not isinstance(split, (list, tuple)):
        return None
    clusters = [str(part).strip() for part in split if str(part).strip()]
    whole = "".join(clusters)
    if not clusters or len(whole) < 2 or any(" " in c for c in clusters):
        return None
    if han and han != whole:
        return None
    return clusters


def _usable_splits(splits: Mapping[str, Sequence[str]] | None) -> dict[str, list[str]]:
    """Validate a caller-supplied ``{name: clusters}`` map the same way the loader does."""
    out: dict[str, list[str]] = {}
    for han, parts in (splits or {}).items():
        name = str(han).strip()
        clusters = _clean_split(name, list(parts) if isinstance(parts, (list, tuple)) else parts)
        if clusters:
            out[name or "".join(clusters)] = clusters
    return out


def _resplit_names(pieces: Iterable[str], splits: Mapping[str, list[str]]) -> list[str]:
    """Replace every piece that is exactly a multi-cluster name by its clusters.

    Exact match only: a piece that merely *contains* a name is some other
    dictionary word, and cutting inside it would be guessing.
    """
    out: list[str] = []
    for piece in pieces:
        clusters = splits.get(piece)
        if clusters and len(clusters) > 1:
            out.extend(clusters)
        else:
            out.append(piece)
    return out


# --------------------------------------------------------------------------- #
# names.json fingerprint — a re-run must notice a table the user just edited
# --------------------------------------------------------------------------- #
#
# The Vietnamese name of a character can only be typed *after* a film has run
# once: that is when the name tab has something to list.  Re-running the same
# link then reused S5..S9 wholesale, because every stage asks only "is my file
# there?" — so the names just typed were never used, and nothing on screen said
# so.  S5 and S8 therefore record which table they were built from, and redo
# themselves (and every stage after them) when it has changed.

#: Key S5 and S8 write into their stage files: the fingerprint of the table used.
NAMES_HASH_KEY: Final[str] = "names_hash"

#: Fingerprint of "no proper-noun table at all" (no file, or a file naming
#: nobody).  A stage file older than fingerprints reads as this value too, see
#: :func:`stored_names_hash`.
NO_NAMES_HASH: Final[str] = ""

#: What the user reads when a re-run picks up a changed table.
NAMES_CHANGED_MESSAGE: Final[str] = "Bảng tên riêng đã đổi, làm lại bước tách cụm và dịch"

#: Top-level keys that change on every save without changing a single name.
#: S7 and the web UI's name tab both stamp ``updated_at`` on each write; hashing
#: it would make pressing "Lưu" on an untouched table redo the whole film — and,
#: with a Gemini key, pay for the translation a second time.
_VOLATILE_NAME_KEYS: Final[frozenset[str]] = frozenset({"updated_at"})


def names_hash(path: str | Path) -> str | None:
    """Fingerprint of ``names.json`` as the stages read it.

    Returns :data:`NO_NAMES_HASH` when there is no table (no file, or a file
    naming nobody), a hex digest otherwise, and ``None`` when the file is there
    but cannot be read — half-saved, or mistyped by hand.  ``None`` means
    "cannot tell", and callers keep what they have: redoing a film with a
    broken table would quietly throw away the names the old result was built
    with.

    Normalised before hashing so that only a real edit counts as one: keys
    sorted, every string NFC (macOS hands out NFD, and the same name typed on
    two machines must not look like two tables), :data:`_VOLATILE_NAME_KEYS`
    dropped.  The whole table is hashed, ``vi`` included, because the
    Vietnamese spelling is exactly what the translation step reads.
    """
    file = Path(path)
    try:
        if not file.is_file():
            return NO_NAMES_HASH
        data = read_json(file)
    except (OSError, ValueError):  # ValueError covers bad JSON and bad UTF-8 alike
        return None
    if isinstance(data, Mapping):
        data = {k: v for k, v in data.items() if k not in _VOLATILE_NAME_KEYS}
    if not _names_from_payload(data) and not _splits_from_payload(data):
        return NO_NAMES_HASH
    canonical = json.dumps(
        _canonical(data), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> Any:
    """``value`` with every string in it, keys included, in NFC."""
    if isinstance(value, str):
        return nfc(value)
    if isinstance(value, Mapping):
        return {nfc(str(k)): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    return value


def stored_names_hash(payload: Any) -> str:
    """The fingerprint a stage file was written with.

    A file from before fingerprints existed has none and reads as
    :data:`NO_NAMES_HASH`.  That is what makes the upgrade behave: a film that
    never had a table keeps its results, while a film whose table was filled in
    since gets redone once — which is precisely the film this exists for.
    """
    if isinstance(payload, Mapping):
        value = payload.get(NAMES_HASH_KEY)
        if isinstance(value, str):
            return value
    return NO_NAMES_HASH


def redo_from_stage(
    ctx: Context,
    stage: int,
    on_progress: ProgressFn | None,
    message: str,
) -> bool:
    """Make ``stage`` and every later stage run again; always returns ``True``.

    The cascade is ``cfg["force_from"]`` because it is the only one there is.
    A stage decides to reuse its result by asking ``ctx.has_stage()`` — "is my
    file on disk" — and nothing else, so a fresh S5 on its own would leave
    S6..S9 reusing results built from the old table: new clusters in
    ``S5_tokens.json``, old ones in the subtitle file.  ``Context.has_stage``
    already reports every stage ``>= force_from`` as missing, which is also how
    the pipeline's second lap works.  The config is copied rather than edited
    in place, since the dict may be shared with whoever built the context.

    S9 is about to overwrite the subtitle files, and the user may have fixed
    lines in them by hand since — in the editor tab or in Aegisub.  Protecting
    those lines is S9's job, not this function's: S9 hands every output file to
    :func:`srtgen.io_utils.keep_old_copy` before writing, which renames a
    changed file to ``<stem>.truoc-<moment><suffix>`` right beside it and
    refuses to overwrite when it cannot.  This function used to copy the same
    files into a ``ban-sua-tay-cu/`` folder as well, so one re-run left TWO
    copies of the same hand edit in two different places — one mechanism means
    one copy, found where every other kept copy is.

    Still a ``bool`` because S5 and S8 read the answer as "the redo is on".
    """
    report_progress(on_progress, message, 0.0)
    force = ctx.force_from
    if force is None or force > stage:
        ctx.cfg = {**(ctx.cfg or {}), "force_from": stage}
    return True


def install_userdict(
    tokenizer: Any,
    names: Mapping[str, str],
    path: str | Path | None,
    splits: Mapping[str, Sequence[str]] | None = None,
) -> int:
    """Teach one tokenizer the film's proper nouns; returns how many names it knows.

    Two kinds of entry go in, for two different reasons:

    * every **cluster** of the name table (光头, 强, 谈, 胥 ...), with a frequency
      jieba computes itself, then ``suggest_freq`` on the multi-character ones.
      ``load_userdict`` only adds a word with a guessed frequency, which is not
      always high enough to beat an existing segmentation (jieba keeps 林秋楠
      whole until 秋楠 is suggested);
    * every **whole name** from ``splits`` (光头强) as one word at
      :data:`NAME_WORD_FREQ`.  Clusters alone cannot protect a name whose last
      cluster is a single character: 强 is atomic already, so there is nothing
      to suggest, and jieba's HMM glues it to whatever follows (强来).  With the
      whole name in the dictionary no cut goes through it; the segmenter then
      re-splits the piece as ``names.json`` says (see :func:`make_segmenter`).

    Build-spec S5.2 asks for ``jieba.load_userdict``, so the table is written
    out as a dictionary file first — that also leaves an inspectable artefact
    next to the stage files.
    """
    whole = _usable_splits(splits)
    clusters = sorted(({str(zh).strip() for zh in (names or {})} - {""}) - set(whole))
    if not clusters and not whole:
        return 0
    # "word [freq] tag": a cluster has no frequency column, so jieba computes
    # one; "nz" is its tag for a proper noun that is not a person or a place.
    lines = [f"{zh} nz" for zh in clusters]
    lines += [f"{han} {NAME_WORD_FREQ} nz" for han in sorted(whole)]
    loaded = False
    if path is not None:
        write_text(path, "\n".join(lines), bom=False)
        try:
            tokenizer.load_userdict(str(path))
            loaded = True
        except (OSError, ValueError):  # pragma: no cover - unreadable dict file
            loaded = False
    if not loaded:
        for zh in clusters:
            tokenizer.add_word(zh)
        for han in sorted(whole):
            tokenizer.add_word(han, NAME_WORD_FREQ)
    for zh in (*clusters, *sorted(whole)):
        if len(zh) < 2:
            continue
        try:
            # tune=True never lowers a frequency, so NAME_WORD_FREQ survives.
            tokenizer.suggest_freq(zh, True)
        except Exception:  # pragma: no cover - jieba is tolerant, we are too
            pass
    covered = {part for parts in whole.values() for part in parts}
    return len(whole) + sum(1 for zh in clusters if zh not in covered)


def make_segmenter(
    names: Mapping[str, str] | None = None,
    *,
    splits: Mapping[str, Sequence[str]] | None = None,
    userdict_path: str | Path | None = None,
    on_progress: ProgressFn | None = None,
) -> tuple[Callable[..., list[str]], int]:
    """The ``cut`` function this run should use, plus the number of names in it.

    When there are proper nouns to load, a **private** ``jieba.Tokenizer`` is
    built instead of teaching the module-level one.  jieba's default tokenizer
    is global state, and the web UI runs many videos in one process: a name
    learned for video A would go on splitting video B's text for as long as the
    server is up, producing quietly wrong output that no user could diagnose.
    A private tokenizer costs about 0.4 s to build, which is the right price
    for that.  With no names there is nothing to leak, so the shared tokenizer
    is used as-is.

    ``splits`` (``{whole name: [clusters]}``, see :func:`load_name_splits`) is
    the other half of the name fix: the tokenizer learns each whole name as a
    single word, so the returned ``cut`` never cuts *across* a name, and every
    piece that is exactly such a name is then replaced by its clusters.  The
    re-split lives inside ``cut`` rather than in a later step so the guarantee
    sits in one place: whoever segments with this function gets 光头 | 强 | 来了,
    never 光头 | 强来.  Extra arguments (``HMM=False`` from the editor's
    "Sinh lại pinyin" button) are passed straight through to jieba.
    """
    jieba = _jieba(on_progress)
    entries = {str(zh).strip() for zh in (names or {})} - {""}
    whole = _usable_splits(splits)
    if not entries and not whole:
        return jieba.lcut, 0
    try:
        tokenizer = jieba.Tokenizer()
        tokenizer.initialize()
    except AttributeError:  # pragma: no cover - jieba older than 0.39
        tokenizer = jieba
    count = install_userdict(tokenizer, names or {}, userdict_path, splits=whole)
    resplit = {han: parts for han, parts in whole.items() if len(parts) > 1}
    if not resplit:
        return tokenizer.lcut, count
    lcut = tokenizer.lcut

    def cut(text: str, *args: Any, **kwargs: Any) -> list[str]:
        return _resplit_names(lcut(text, *args, **kwargs), resplit)

    return cut, count


# --------------------------------------------------------------------------- #
# the HMM switch and the shared one-line segmenter
# --------------------------------------------------------------------------- #

#: Cache for the packaged ``tokenize.jieba_hmm`` value, like ``_PACKAGED_PREFS``.
_PACKAGED_HMM: dict[str, bool] = {}


def _as_bool(value: Any, default: bool) -> bool:
    """Read a yes/no written by hand in YAML without surprises.

    ``bool("false")`` is ``True``; a user who types ``jieba_hmm: "false"`` with
    quotes must still get ``False``, not the opposite of what they wrote.
    Anything unreadable means "not said", which falls back to ``default``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "yes", "on", "có", "bật"):
            return True
        if text in ("0", "false", "no", "off", "không", "tắt"):
            return False
    return default


def _packaged_hmm() -> bool:
    """The HMM switch as shipped in ``config/default.yaml``.

    Same reasoning as :func:`default_reading_prefs`: callers that hold no config
    (the web button, several tests handing in a partial dict) must cut exactly
    like a pipeline run with the default config, or one film would get two
    segmentations depending on which button produced the line.
    """
    if "value" not in _PACKAGED_HMM:
        value: Any = None
        try:
            from srtgen.core.context import load_config

            value = _section(load_config(), "tokenize").get(HMM_KEY)
        except Exception:  # pragma: no cover - config unreadable: keep the measured default
            value = None
        _PACKAGED_HMM["value"] = _as_bool(value, _DEFAULT_HMM)
    return _PACKAGED_HMM["value"]


def jieba_hmm(cfg: Mapping[str, Any] | None = None) -> bool:
    """Whether jieba's HMM layer is on for this run (``tokenize.jieba_hmm``).

    The caller's config wins when it says something; otherwise the packaged
    ``default.yaml`` decides, and only if that is unreadable too does the
    measured default (``False``) apply.
    """
    section = _section(cfg, "tokenize")
    if HMM_KEY in section:
        return _as_bool(section.get(HMM_KEY), _packaged_hmm())
    return _packaged_hmm()


def _with_hmm(cut: Callable[..., list[str]], hmm: bool) -> Callable[[str], list[str]]:
    """Bind the HMM switch into a ``cut`` function.

    jieba's ``lcut`` default is ``HMM=True``; the switch therefore has to be
    passed on every call, and binding it once here means no call site can
    forget.  A cutter that does not accept the keyword (a test double, an older
    jieba) is called plainly rather than breaking the whole stage.
    """

    def run(text: str) -> list[str]:
        try:
            return list(cut(text, HMM=hmm))
        except TypeError:
            return list(cut(text))

    return run


def _segment_tables(
    names: Mapping[str, Any] | None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """``(clusters, whole names)`` out of whatever name table the caller holds.

    Accepted, so that no caller has to convert first:

    * the parsed ``names.json`` in any layout S5 reads (``names``/``entries``/
      ``_meta``, flat ``{Hán: pinyin}``, ``{Hán: {"split": [...]}}``);
    * the short form ``{"光头强": ["光头", "强"]}`` — a whole name mapped to its
      clusters — which is how a person states a split in one line.

    A whole name's clusters are also loaded as clusters, exactly as the
    ``names`` block of a ``names.json`` written by S7 lists them.
    """
    if not names:
        return {}, {}
    clusters = _names_from_payload(names)
    splits = _splits_from_payload(names)
    for key, value in names.items():
        if isinstance(value, (list, tuple)) and value and all(isinstance(p, str) for p in value):
            han = str(key).strip()
            parts = _clean_split(han, value)
            if parts:
                splits.setdefault(han, parts)
    for han, parts in splits.items():
        clusters.pop(han, None)
        for part in parts:
            clusters.setdefault(part, "")
    return clusters, splits


#: A private tokenizer costs about 0.4 s to build.  The editor's button cuts
#: one line per click, usually with the same film's table, so the last few
#: segmenters are kept — keyed by the table's *content*, so one film's names can
#: never leak into another film's cut (the reason the tokenizer is private).
_SEGMENTER_CACHE: dict[tuple[Any, ...], Callable[..., list[str]]] = {}
_SEGMENTER_CACHE_SIZE: Final[int] = 8


def _cached_segmenter(
    clusters: Mapping[str, str],
    splits: Mapping[str, Sequence[str]],
) -> Callable[..., list[str]]:
    key = (
        tuple(sorted(str(zh).strip() for zh in clusters)),
        tuple(sorted((han, tuple(parts)) for han, parts in splits.items())),
    )
    cut = _SEGMENTER_CACHE.get(key)
    if cut is None:
        cut, _count = make_segmenter(clusters, splits=splits)
        if len(_SEGMENTER_CACHE) >= _SEGMENTER_CACHE_SIZE:
            _SEGMENTER_CACHE.pop(next(iter(_SEGMENTER_CACHE)))
        _SEGMENTER_CACHE[key] = cut
    return cut


def segment_tokens(
    zh: str,
    names: Mapping[str, Any] | None = None,
    *,
    cfg: Mapping[str, Any] | None = None,
    userdict_path: str | Path | None = None,
) -> list[Token]:
    """Steps 1 and 2 of this stage for one line: word / punct / marker tokens.

    The tokens carry no pinyin yet — that is step 4, and the caller decides
    whether it wants it (the editor's button runs the rest of S5/S6 on them).
    Returned as tokens rather than strings for callers that need to know which
    piece is punctuation; :func:`segment_line` is the plain-string view.

    ``cfg`` is optional: without it the packaged ``default.yaml`` decides the
    HMM switch, so the result is what a default pipeline run would produce.
    ``tokenize.userdict: false`` in ``cfg`` ignores ``names``, as the stage does.
    """
    text = str(zh or "")
    if not text.strip():
        return []
    use_dict = bool(_section(cfg, "tokenize").get("userdict", True))
    clusters, splits = _segment_tables(names if use_dict else None)
    if userdict_path is not None and (clusters or splits):
        # A dictionary file was asked for (it is the inspectable artefact next to
        # the stage files), so build a fresh tokenizer that writes it.
        segmenter, _count = make_segmenter(clusters, splits=splits, userdict_path=userdict_path)
    elif clusters or splits:
        segmenter = _cached_segmenter(clusters, splits)
    else:
        segmenter, _count = make_segmenter({})
    return cut_words(split_punctuation(text), _with_hmm(segmenter, jieba_hmm(cfg)))


def segment_line(
    zh: str,
    names: Mapping[str, Any] | None = None,
    *,
    cfg: Mapping[str, Any] | None = None,
    userdict_path: str | Path | None = None,
) -> list[str]:
    """Cut one raw Han line exactly the way this stage cuts a cue.

    ``segment_line("我来中国只有一个目的")`` → ``["我", "来", "中国", "只有",
    "一个", "目的"]``; ``segment_line("光头强来了", {"光头强": ["光头", "强"]})``
    → ``["光头", "强", "来", "了"]``.

    Every piece of the line comes back, punctuation and speaker markers
    included, in order (``"你好，世界。"`` → ``["你好", "，", "世界", "。"]``), so
    nothing the user typed disappears; spaces are dropped because in this
    project a space *is* a cluster boundary.  Use :func:`segment_tokens` to know
    which pieces are words.

    This is the one segmenter the editor's "Sinh lại pinyin cho câu này" button
    may use: same user dictionary (:func:`install_userdict` via
    :func:`make_segmenter`, whole names re-split as ``names.json`` says), same
    ``tokenize.jieba_hmm`` switch as :func:`tokenize_document`.
    """
    return [
        tok.zh or ("-" if tok.kind != KIND_WORD else "")
        for tok in segment_tokens(zh, names, cfg=cfg, userdict_path=userdict_path)
    ]


# --------------------------------------------------------------------------- #
# step 1 — punctuation
# --------------------------------------------------------------------------- #

def split_punctuation(text: str) -> list[Token]:
    """Step 1: turn a raw cue string into word / punct / marker tokens.

    :func:`srtgen.core.srt.tokenize_line` already knows every rule the README
    lays down for this — multi-character marks before single ones, a bare ``-``
    read as a speaker marker or as interrupted speech depending on its
    neighbours — so this stage reuses it instead of owning a second, slightly
    different lexer that would drift.

    ``normalize=False`` on purpose: converting ASCII punctuation is S6's step
    1, and doing it here would make the S5 stage file disagree with the text
    ASR actually produced, which is the file a human looks at when the output
    seems wrong.
    """
    return tokenize_line(text, normalize=False)


# --------------------------------------------------------------------------- #
# step 2 — jieba
# --------------------------------------------------------------------------- #

def _keep_whole(text: str) -> list[str]:
    """A segmenter that segments nothing — used when the boundaries are given."""
    return [text]


def _is_han_char(ch: str) -> bool:
    return "㐀" <= ch <= "鿿" or "豈" <= ch <= "﫿" or "\U00020000" <= ch <= "\U0003134f"


def _rejoin_latin(pieces: list[str]) -> list[str]:
    """Glue back what jieba cut out of the middle of a Latin / digit run.

    Step 1 keeps ``bye-bye``, ``Wi-Fi``, ``T-shirt``, ``don't`` as ONE word, and
    the reader of the finished line counts them as one.  jieba does not know that
    and returns ``["bye", "-", "bye"]``, so a lone ``-`` became a word of its
    own; the renderer wrote ``bye - bye``, which reads back as punctuation, and
    S6's proof stopped the whole run (seen on a real video, 15-09-2026).

    Only a cut with a non-Han character on BOTH sides is undone.  Every cut next
    to a Han character stays exactly where jieba put it, so ``bye-bye原`` still
    ends as ``bye-bye`` + ``原`` and a dictionary cluster such as ``哆啦A梦`` keeps
    its own boundaries.
    """
    out: list[str] = []
    for piece in pieces:
        if out and not _is_han_char(out[-1][-1]) and not _is_han_char(piece[0]):
            out[-1] += piece
        else:
            out.append(piece)
    return out


def cut_words(tokens: Iterable[Token], cut: Callable[[str], list[str]]) -> list[Token]:
    """Step 2: replace each word token with jieba's segmentation of it.

    Punctuation and markers pass through untouched — they were separated in
    step 1 precisely so the segmenter never sees them.  ``cut`` is injected
    rather than called directly so that this function stays testable without
    jieba installed, which is also what lets ``srtgen doctor`` import the
    module on a bare machine.
    """
    out: list[Token] = []
    for tok in tokens:
        if tok.kind != KIND_WORD or not tok.zh.strip():
            out.append(tok)
            continue
        pieces = _rejoin_latin([p for p in cut(tok.zh) if p.strip()])
        if len(pieces) <= 1:
            tok.source = SOURCE_JIEBA
            out.append(tok)
            continue
        for piece in pieces:
            out.append(
                Token(
                    kind=KIND_WORD,
                    zh=piece,
                    pinyin=None,
                    confidence=tok.confidence,
                    source=SOURCE_JIEBA,
                    flags=list(tok.flags),
                )
            )
    return out


# --------------------------------------------------------------------------- #
# step 4 — bảng ưu tiên đọc
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ReadingPrefs:
    """The spoken reading this project prefers, for the few characters it matters.

    Two tables rather than one, because the two questions are genuinely
    different and merging them would make one of the two answers wrong:

    * ``always`` — the character has one spoken reading, full stop, wherever it
      appears.  谁 is ``shéi`` alone and inside 谁知道 alike.
    * ``exact`` — the *whole cluster* has to match.  A lone 地 is the adverbial
      particle ``de``, but the same character inside 地图 is ``dì``; putting 地
      in ``always`` would silently break every place name in the film.

    Frozen because it is built once per run and then read from a hot loop; a
    mutable copy handed around would be the kind of shared state that makes one
    video's table leak into the next one in a long-running web session.
    """

    always: Mapping[str, str] = field(default_factory=dict)
    exact: Mapping[str, str] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.always or self.exact)

    def merged_with(self, other: "ReadingPrefs") -> "ReadingPrefs":
        """This table with ``other`` laid on top — ``other`` wins a collision.

        An entry whose reading is empty is a *deletion*, not an entry: writing
        ``谁: ""`` in a profile is how somebody switches one preference off
        without having to copy the whole table out of ``default.yaml`` first.
        """
        return ReadingPrefs(
            always=_drop_blanks({**self.always, **other.always}),
            exact=_drop_blanks({**self.exact, **other.exact}),
        )


def _drop_blanks(table: Mapping[str, str]) -> dict[str, str]:
    return {zh: reading for zh, reading in table.items() if reading}


def _clean_table(raw: Any) -> dict[str, str]:
    """``{chữ Hán: cách đọc}`` from whatever the config actually contains.

    Blank readings survive this step on purpose — :meth:`ReadingPrefs.merged_with`
    is where they turn into a deletion, and dropping them here would make
    switching an entry off silently do nothing.
    """
    if not isinstance(raw, Mapping):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        zh = str(key).strip()
        if zh:
            out[zh] = str(value or "").strip()
    return out


def _prefs_from_section(raw: Any) -> ReadingPrefs:
    """Read one ``reading_prefs`` block, accepting the shorthand shape too.

    The documented shape has ``always`` and ``exact`` sub-tables.  A block that
    has neither is read as a flat ``{Hán: cách đọc}`` map and split by length:
    one character goes to ``always``, a longer cluster to ``exact``.  That is
    the shape a person writes when they add a line in a hurry, and refusing to
    read it would mean their edit silently does nothing — the exact failure this
    whole round is about.
    """
    if not isinstance(raw, Mapping):
        return ReadingPrefs()
    if "always" in raw or "exact" in raw:
        return ReadingPrefs(
            always=_clean_table(raw.get("always")),
            exact=_clean_table(raw.get("exact")),
        )
    flat = _clean_table(raw)
    return ReadingPrefs(
        always={k: v for k, v in flat.items() if len(k) == 1},
        exact={k: v for k, v in flat.items() if len(k) > 1},
    )


#: Cache for the packaged table.  Reading and parsing ``default.yaml`` costs a
#: few milliseconds, and :func:`pinyin_of` is called once per cluster.
_PACKAGED_PREFS: dict[str, ReadingPrefs] = {}


def default_reading_prefs() -> ReadingPrefs:
    """The table as shipped in ``config/default.yaml``.

    Why this function exists at all: not every caller has a config in hand.  The
    web UI fills a missing reading through ``fill_pinyin(cue.tokens)`` with no
    config anywhere in sight, and if that path produced ``shuí`` while the
    pipeline produced ``shéi`` the same film would spell one word two ways
    depending on which button the user pressed.  So the packaged table is the
    floor, and a config only ever adds to it.

    A missing or damaged config file yields an empty table rather than an
    exception: a reading preference is a nicety, and losing it must never stop a
    forty-minute run.
    """
    if "value" not in _PACKAGED_PREFS:
        section: Any = None
        try:
            from srtgen.core.context import load_config

            cfg = load_config()
            section = _section(_section(cfg, "tokenize"), READING_PREFS_KEY)
        except Exception:  # pragma: no cover - config unreadable, see docstring
            section = None
        _PACKAGED_PREFS["value"] = ReadingPrefs().merged_with(_prefs_from_section(section))
    return _PACKAGED_PREFS["value"]


def reading_prefs(cfg: Mapping[str, Any] | None = None) -> ReadingPrefs:
    """The table this run should use: the packaged one plus whatever ``cfg`` adds.

    Union rather than replacement, for the same reason
    :data:`HETERONYM_CHARS` is a union: a caller who passes a partial config
    (the ``fix`` command passes ``{}``, several tests pass a hand-built dict)
    must not silently lose the readings the project has already settled.
    """
    base = default_reading_prefs()
    section = _section(_section(cfg, "tokenize"), READING_PREFS_KEY)
    return base.merged_with(_prefs_from_section(section))


# --------------------------------------------------------------------------- #
# step 4 — pinyin
# --------------------------------------------------------------------------- #

def _has_han(text: str) -> bool:
    for ch in text:
        if is_han(ch):
            return True
        for low, high in _HAN_EXTRA:
            if low <= ch <= high:
                return True
    return False


def pinyin_of(text: str, prefs: ReadingPrefs | None = None) -> str:
    """Pinyin of one token, tone marks included, with the reading table applied.

    A token with no Han in it (``OK``, ``PK``, ``2024``) is its own pinyin:
    Latin words are read as they are written, and asking pypinyin about them
    only risks a surprise.  Everything else goes through ``lazy_pinyin`` with
    the whole token as context — see the module docstring for why the call is
    never made on a longer string than this.

    ``prefs=None`` means "the table the project ships with"; pass an explicit
    :class:`ReadingPrefs` (usually from :func:`reading_prefs`) to add the
    entries a config or a profile contributes.

    The per-character substitution only runs when pypinyin returned exactly one
    syllable per character.  It does not for a token that mixes Han with Latin,
    and lining a shorter list up against the characters would put a preferred
    reading on the wrong syllable — a silent corruption far worse than the
    missing preference it was trying to supply.
    """
    if not text:
        return ""
    if not _has_han(text):
        return text
    table = default_reading_prefs() if prefs is None else prefs
    fixed = table.exact.get(text)
    if fixed:
        return fixed
    api = _pypinyin()
    syllables = list(api["lazy_pinyin"](text, style=api["Style"].TONE))
    if table.always and len(syllables) == len(text):
        syllables = [table.always.get(ch, syl) for ch, syl in zip(text, syllables)]
    return "".join(syllables)


def fill_pinyin(
    tokens: Iterable[Token],
    *,
    overwrite: bool = True,
    prefs: ReadingPrefs | None = None,
) -> None:
    """Step 4: give every word token its pinyin, in place.

    ``overwrite=False`` fills only the gaps.  That is the mode used when the
    cluster boundaries came from an edited file: those tokens already carry a
    human's pinyin, and the only ones missing a reading are the ones
    ``merge_zh_py`` refused to guess at (README: never shift one cluster's
    pinyin onto the next).  Regenerating the rest would quietly overrule the
    editor — and that includes overruling a reading they chose on purpose,
    which is exactly why the reading table is applied when *generating* a
    reading and never to a reading that is already there.
    """
    table = default_reading_prefs() if prefs is None else prefs
    for tok in tokens:
        if tok.kind != KIND_WORD:
            tok.pinyin = None
            continue
        if not overwrite and tok.pinyin:
            continue
        tok.pinyin = pinyin_of(tok.zh, table)


# --------------------------------------------------------------------------- #
# step 6 — heteronyms
# --------------------------------------------------------------------------- #

def _heteronym_chars(cfg: Mapping[str, Any] | None) -> set[str]:
    """The baseline list plus whatever the config adds (never less).

    See :data:`HETERONYM_CHARS` for why the two sources are unioned instead of
    the config replacing the baseline.
    """
    chars = set(HETERONYM_CHARS)
    tokenize_cfg = _section(cfg, "tokenize")
    extra = tokenize_cfg.get("heteronym_chars")
    if isinstance(extra, str):
        chars |= set(extra)
    elif isinstance(extra, (list, tuple)):
        chars |= {str(c) for c in extra if str(c)}
    return {c for c in chars if c.strip()}


def _undecided_positions(zh: str) -> list[bool]:
    """Per character: does pypinyin still offer more than one reading here?

    ``pinyin(..., heteronym=True)`` answers with the phrase dictionary already
    applied, so 长江 comes back with a single ``cháng`` (the phrase settled it)
    while the 行 of 行不行 comes back with five candidates.  That difference is
    exactly the question S7's T2 task asks a model, so a character the phrase
    dictionary already resolved is not worth anybody's review time.
    """
    api = _pypinyin()
    try:
        readings = api["pinyin"](zh, heteronym=True)
    except Exception:  # pragma: no cover - pypinyin is stable, data is not
        return [True] * len(zh)
    if len(readings) != len(zh):
        # Non-Han characters can collapse into one entry; fall back to "the
        # whole token is uncertain" rather than lining the lists up wrongly.
        return [any(len(r) > 1 for r in readings)] * len(zh)
    return [len(r) > 1 for r in readings]


def flag_heteronyms(
    tokens: Iterable[Token],
    chars: set[str],
    *,
    confidence: float = HETERONYM_CONFIDENCE,
) -> int:
    """Step 6: mark the tokens whose reading is still an open question.

    A token qualifies when it contains one of the listed characters **and**
    pypinyin still has a choice to make at that character's position.  Both
    conditions are needed: the list alone would flag every 长 in 长江, and
    pypinyin's ambiguity alone would flag characters nobody has ever misread.
    Measured on ``corpus/completed.srt``: 351 tokens contain a listed
    character, 254 of them are genuinely undecided.

    Returns the number of tokens flagged, for the report.
    """
    if not chars:
        return 0
    flagged = 0
    for tok in tokens:
        if tok.kind != KIND_WORD or not tok.zh:
            continue
        if not any(ch in chars for ch in tok.zh):
            continue
        undecided = _undecided_positions(tok.zh)
        if not any(
            open_here and ch in chars for ch, open_here in zip(tok.zh, undecided)
        ):
            continue
        tok.add_flag(FLAG_HETERONYM)
        tok.confidence = min(tok.confidence, confidence)
        flagged += 1
    return flagged


# --------------------------------------------------------------------------- #
# the stage body
# --------------------------------------------------------------------------- #

def tokenize_document(
    doc: Document,
    cfg: Mapping[str, Any] | None = None,
    *,
    names: Mapping[str, str] | None = None,
    name_splits: Mapping[str, Sequence[str]] | None = None,
    userdict_path: str | Path | None = None,
    on_progress: ProgressFn | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Document:
    """Run the six S5 steps over every cue of ``doc``, in place, and return it.

    Two input shapes are supported, told apart by ``doc.meta["segmented"]``:

    * ``False`` (what S4 produces) — each cue carries raw ASR text and this
      stage decides the cluster boundaries;
    * ``True`` (a file somebody already edited, read back by
      ``parse_srt_document``) — the boundaries are the editor's and are kept
      verbatim, because the README calls the spaces in an existing file
      "ranh giới cụm từ đã được biên tập".  Only the missing pinyin is filled
      in, which is what a ``CUM_MISMATCH`` cue needs.

    ``names`` is the ``{cluster: pinyin}`` map of :func:`load_names`;
    ``name_splits`` is the ``{whole name: clusters}`` map of
    :func:`load_name_splits`.  Both only matter on the unsegmented path, and
    both are needed there: without the second one a name such as 光头强 (split
    光头 | 强) is cut as 光头 | 强来 by jieba — see :func:`make_segmenter`.
    """
    cfg = cfg or {}
    segmented = bool(doc.meta.get("segmented"))
    erhua_cfg = _section(cfg, "erhua")
    erhua_on = erhua_cfg.get("enabled", True)
    erhua_contract = erhua_cfg.get("contract", True)
    sandhi_cfg = dict(_section(cfg, "sandhi"))
    chars = _heteronym_chars(cfg)
    prefs = reading_prefs(cfg)

    # jieba is only touched when this stage is the one deciding the boundaries;
    # a document that arrives already segmented must not pay for a dictionary
    # build it will never use.
    cut: Callable[[str], list[str]] = _keep_whole
    if not segmented:
        use_dict = bool(_section(cfg, "tokenize").get("userdict", True))
        segmenter, installed = make_segmenter(
            names if use_dict else {},
            splits=name_splits if use_dict else None,
            userdict_path=userdict_path,
            on_progress=on_progress,
        )
        # The HMM switch is applied here, on the cut itself, and nowhere else in
        # the stage — :func:`segment_tokens` wraps its cut the same way, which is
        # what keeps the editor's button and this stage from drifting apart.
        cut = _with_hmm(segmenter, jieba_hmm(cfg))
        if installed:
            report_progress(
                on_progress,
                f"Đã nạp {installed} tên riêng vào bộ tách từ.",
                0.0,
            )

    total = max(1, len(doc.cues))
    flagged = 0
    for position, cue in enumerate(doc.cues, start=1):
        if position % _PROGRESS_EVERY == 1 or position == total:
            stop_if_cancelled(cancelled)
            report_progress(
                on_progress,
                f"Đang tách từ và ghi pinyin: câu {position}/{len(doc.cues)}",
                position / total,
            )

        if segmented:
            tokens = list(cue.tokens)
        else:
            tokens = split_punctuation(cue.zh_text())      # 1
            tokens = cut_words(tokens, cut)                # 2

        if erhua_on:
            merge_erhua(tokens)                            # 3
        fill_pinyin(tokens, overwrite=not segmented, prefs=prefs)   # 4
        if erhua_on and erhua_contract:
            merge_erhua(tokens)                            # 4b, see module docstring
        apply_sandhi(tokens, sandhi_cfg)                   # 5
        flagged += flag_heteronyms(tokens, chars)          # 6
        cue.tokens = tokens

    doc.meta["segmented"] = True
    doc.meta["s5"] = {
        "cues": len(doc.cues),
        "words": word_count(t for cue in doc.cues for t in cue.tokens),
        "heteronyms": flagged,
        "names": len(names or {}),
        "kept_boundaries": segmented,
        "reading_prefs": len(prefs.always) + len(prefs.exact),
    }
    return doc


def run(ctx: Context, on_progress: ProgressFn | None = None) -> Document:
    """Stage entry point: S4 cues in, ``S5_tokens.json`` out.

    Returns the :class:`~srtgen.core.token.Document` and parks it on
    ``ctx.doc`` so the next stage can take it from memory instead of re-reading
    the file it has just written.

    A saved result is reused only if it was cut with the ``names.json`` that is
    on disk now.  Otherwise this stage redoes itself and, through
    ``force_from``, every stage after it — see :func:`redo_from_stage`.
    """
    table = names_path(ctx)
    # Fingerprinted before the table is read, never after: if the user saves the
    # name tab while this stage runs, the hash recorded is the older one and the
    # next run redoes the film once more — the safe direction.  The other order
    # could record the new hash over clusters cut with the old table.
    fingerprint = names_hash(table)

    if ctx.has_stage(STAGE, STAGE_NAME):
        saved = ctx.load_stage(STAGE, STAGE_NAME)
        if saved and _still_valid(ctx, saved, fingerprint, table, on_progress):
            doc = Document.from_stage_dict(saved)
            ctx.doc = doc
            report_progress(on_progress, "Dùng lại kết quả tách từ đã có sẵn.", 1.0)
            return doc

    stop_if_cancelled(ctx.is_cancelled)
    if fingerprint is None:
        report_progress(on_progress, _unreadable_names_message(table, kept=False), 0.0)
    source = _load_source(ctx)
    names = load_names(table)
    name_splits = load_name_splits(table)

    doc = tokenize_document(
        source,
        ctx.cfg,
        names=names,
        name_splits=name_splits,
        userdict_path=ctx.work_dir / USERDICT_FILE,
        on_progress=on_progress,
        cancelled=ctx.is_cancelled,
    )

    payload = doc.to_stage_dict()
    # Beside the document, not inside ``doc.meta``: meta travels on into the
    # files of S6..S9, where a stale copy of this hash would only mislead.
    payload[NAMES_HASH_KEY] = fingerprint or NO_NAMES_HASH
    ctx.save_stage(STAGE, STAGE_NAME, payload)
    ctx.doc = doc
    report_progress(
        on_progress,
        f"Xong: {len(doc.cues)} câu, {doc.meta['s5']['words']} cụm từ.",
        1.0,
    )
    return doc


def _still_valid(
    ctx: Context,
    saved: Mapping[str, Any],
    fingerprint: str | None,
    table: Path,
    on_progress: ProgressFn | None,
) -> bool:
    """Whether a saved S5 result was cut with the proper-noun table on disk now.

    ``False`` means the table changed and :func:`redo_from_stage` has already
    arranged for S5..S9 to run again.  An unreadable table keeps the old
    result, with a sentence saying so rather than silence.
    """
    if fingerprint is None:
        report_progress(on_progress, _unreadable_names_message(table, kept=True), 0.0)
        return True
    if stored_names_hash(saved) == fingerprint:
        return True
    return not redo_from_stage(ctx, STAGE, on_progress, NAMES_CHANGED_MESSAGE)


def _unreadable_names_message(table: Path, *, kept: bool) -> str:
    """Say in plain Vietnamese that ``names.json`` could not be read, and what happens now."""
    outcome = (
        "Tool giữ nguyên kết quả tách cụm cũ"
        if kept
        else "Lần này tool tách cụm như chưa có tên riêng nào"
    )
    return (
        f"Không đọc được bảng tên riêng ({table}) — file có thể đang sửa dở hoặc sai "
        f"định dạng. {outcome}; sửa lại bảng tên riêng rồi chạy lại để áp tên."
    )


# --------------------------------------------------------------------------- #
# reading whatever S4 left behind
# --------------------------------------------------------------------------- #

def _load_source(ctx: Context) -> Document:
    """Fetch the cues to work on, from the S4 file or from memory.

    The file wins over ``ctx.doc``: on a resume the file is the thing that
    survived, and preferring it means "chạy tiếp giữa chừng" behaves the same
    whether the process was restarted or not.
    """
    saved = ctx.load_stage(SOURCE_STAGE, SOURCE_STAGE_NAME)
    if saved:
        return _document_from_payload(saved)
    if ctx.doc is not None and ctx.doc.cues:
        return ctx.doc
    raise RuntimeError(
        "Chưa có kết quả chia câu (bước trước) nên chưa tách từ được. "
        f"Cần có file: {ctx.stage_path(SOURCE_STAGE, SOURCE_STAGE_NAME)}"
    )


def _document_from_payload(data: Any) -> Document:
    """Build a Document out of S4's JSON, whatever shape it chose.

    S4 is written by another hand and its exact keys are not fixed by the build
    spec, so the reader is deliberately generous: a mapping with ``cues`` or
    ``segments``, or a bare list, and per cue either a text field, a token
    list, or the word-timestamp list that S4 works from.  Being strict here
    would turn a naming difference into a pipeline that cannot run at all.
    """
    meta: dict[str, Any] = {}
    raw: Any
    if isinstance(data, Document):
        return data
    if isinstance(data, Mapping):
        meta = dict(data.get("meta") or {})
        raw = data.get("cues")
        if raw is None:
            raw = data.get("segments")
    elif isinstance(data, list):
        raw = data
    else:
        raise RuntimeError(
            "File kết quả chia câu không đọc được (sai định dạng). "
            "Hãy chạy lại từ bước chia câu."
        )
    if not isinstance(raw, list):
        raise RuntimeError(
            "File kết quả chia câu không có danh sách câu nào. "
            "Hãy chạy lại từ bước chia câu."
        )

    cues: list[Cue] = []
    segmented = False
    for position, item in enumerate(raw, start=1):
        cue, has_tokens = _cue_from_payload(item, position)
        segmented = segmented or has_tokens
        cues.append(cue)

    doc = Document(cues=cues, meta=meta)
    # An explicit flag in the payload wins: only the writer knows whether those
    # tokens are an editor's clusters or a placeholder.
    doc.meta["segmented"] = bool(meta.get("segmented", segmented))
    return doc


_TEXT_KEYS: Final[tuple[str, ...]] = (
    "text", "zh", "zh_text", "content", "line", "sentence",
)


def _cue_from_payload(item: Any, position: int) -> tuple[Cue, bool]:
    """One cue, plus whether it arrived already segmented into clusters."""
    if isinstance(item, str):
        return _raw_cue(position, 0.0, 0.0, item), False
    if not isinstance(item, Mapping):
        raise RuntimeError(
            f"Câu số {position} trong file chia câu không đọc được (sai định dạng)."
        )

    index = _as_int(item.get("index", item.get("id", position)), position)
    start = _as_float(item.get("start", item.get("begin", 0.0)))
    end = _as_float(item.get("end", item.get("stop", start)))

    raw_tokens = item.get("tokens")
    if isinstance(raw_tokens, list) and raw_tokens:
        cue = Cue(
            index=index,
            start=start,
            end=end,
            tokens=[
                Token.from_stage_dict(t) if isinstance(t, Mapping) else Token(zh=str(t))
                for t in raw_tokens
            ],
        )
        return cue, True

    text = ""
    for key in _TEXT_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            text = value
            break
    if not text:
        text = _text_from_words(item.get("words"))
    return _raw_cue(index, start, end, text), False


def _raw_cue(index: int, start: float, end: float, text: str) -> Cue:
    """Carry a not-yet-segmented cue as a single word token.

    The token model has no "raw text" kind, and inventing one would leak into
    every module.  A single token whose ``zh`` is the whole line renders back
    to exactly that line, so :func:`tokenize_document` can read it with
    ``cue.zh_text()`` and re-tokenise from scratch.  The lie lives inside this
    file and is undone before any other stage sees the document.
    """
    return Cue(
        index=index,
        start=start,
        end=end,
        tokens=[Token(kind=KIND_WORD, zh=text.strip(), pinyin=None, source=SOURCE_ASR)],
    )


def _text_from_words(words: Any) -> str:
    """Rebuild a cue's text from S4's word-timestamp list, if that is all we get."""
    if not isinstance(words, list):
        return ""
    parts: list[str] = []
    for word in words:
        if isinstance(word, str):
            parts.append(word)
        elif isinstance(word, Mapping):
            parts.append(str(word.get("word") or word.get("text") or word.get("zh") or ""))
    return "".join(parts).strip()


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
