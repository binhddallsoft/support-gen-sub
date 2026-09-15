"""S6 — normalisation: punctuation, sentence case, proper names, and the proof.

What this stage is for
----------------------
S5 decided *where the clusters are*.  S6 decides *how they are written*: which
punctuation marks are used, which words start with a capital, and which words
are names.  Everything the README's checklist asks for that is not a spacing
question is settled here — and the spacing questions are settled by nobody,
because the renderer in :mod:`srtgen.core.token` makes them unaskable.

The order in build-spec section 7 (S6) is not negotiable:

1. ASCII punctuation becomes Chinese punctuation, with quote marks decided by
   an open/close state that is reset for every cue;
2. ``...`` / ``…`` / ``. . .`` become ``……``; an interruption dash becomes
   ``——`` while a speaker marker keeps its ASCII ``-``;
3. :func:`~srtgen.core.casing.apply_casing` sets sentence case;
4. ``names.json`` is applied — proper nouns get their agreed spelling and
   ``FLAG_NAME``;
5. both lines are rendered and the structural invariant is proved.

Steps 3 and 4 are in that order for a reason: casing decides capitals from
punctuation context, and the names table is allowed to overrule it, never the
other way round.

Why there is no whitespace step
-------------------------------
There used to be one, in the old hand-run process, and it was the source of
most of the errors this rewrite exists to remove.  Every operation below edits
tokens; the two lines are then produced by :func:`~srtgen.core.token.render_zh`
and :func:`~srtgen.core.token.render_py`, which cannot emit a leading space, a
trailing space or a double space.  A regex over a joined line is forbidden
here (build-spec section 0, rule 3) — not discouraged, forbidden — because it
is the one technique that can put the two lines out of step with each other.

Why step 5 raises instead of reporting
--------------------------------------
Every other problem in this pipeline is a *finding*: something a human should
look at.  A cluster-count mismatch between the two lines is different in kind.
It cannot be caused by bad input — both lines come from one token list — so if
it ever happens the renderer is broken, and every file written from that point
on would be silently wrong.  That is worth stopping the run for.
"""

from __future__ import annotations

from typing import Any, Callable, Final, Mapping

from srtgen.core.casing import apply_casing, capitalize_pinyin
from srtgen.core.context import Context
from srtgen.core.srt import ASCII_PUNCT_MAP, DASH, ELLIPSIS, tokenize_line
from srtgen.core.token import (
    FLAG_NAME,
    KIND_PUNCT,
    KIND_WORD,
    SOURCE_DICT,
    Document,
    Token,
    render_py,
    render_zh,
    word_count,
)
from srtgen.stages.s5_tokenize import (
    ProgressFn,
    StageCancelled,
    load_names,
    names_path,
    report_progress,
    stop_if_cancelled,
)

__all__ = [
    "STAGE",
    "STAGE_NAME",
    "SOURCE_STAGE",
    "SOURCE_STAGE_NAME",
    "StageCancelled",
    "apply_names",
    "normalize_cue_tokens",
    "normalize_document",
    "normalize_punctuation",
    "prune_empty_tokens",
    "run",
    "verify_invariant",
]


# --------------------------------------------------------------------------- #
# stage identity
# --------------------------------------------------------------------------- #

STAGE: Final[int] = 6
STAGE_NAME: Final[str] = "norm"            # -> work/<id>/S6_norm.json
SOURCE_STAGE: Final[int] = 5
SOURCE_STAGE_NAME: Final[str] = "tokens"   # <- work/<id>/S5_tokens.json

#: Characters that only ever belong to the ellipsis family.  ``。`` is not one
#: of them: two full stops in a row are two full stops, not a dropped ellipsis.
_DOT_CHARS: Final[frozenset[str]] = frozenset({".", "…", "．", "｡"})

#: Every dash a human or an ASR engine might type for interrupted speech.  The
#: speaker marker is not here — it is a different token *kind*, so it can never
#: be caught by this table by accident.
_DASH_CHARS: Final[frozenset[str]] = frozenset({"-", "—", "–", "−", "－", "‐", "‑"})

#: The Chinese quote marks, which already say which half of the pair they are.
#: They are kept exactly as they stand and only *update* the state - rewriting a
#: correct ” into a “ because its opening quote happened to be in the previous cue
#: is a real bug this module used to have.
_DOUBLE_OPEN: Final[str] = "“"
_DOUBLE_CLOSE: Final[str] = "”"
_SINGLE_OPEN: Final[str] = "‘"
_SINGLE_CLOSE: Final[str] = "’"

#: Straight quotes, which do not: these are the ones the open/close state is for.
_STRAIGHT_DOUBLE: Final[frozenset[str]] = frozenset({'"', "″", "＂"})
_STRAIGHT_SINGLE: Final[frozenset[str]] = frozenset({"'", "′", "＇"})


# --------------------------------------------------------------------------- #
# steps 1 and 2 — punctuation
# --------------------------------------------------------------------------- #

class _QuoteState:
    """Which half of a quote pair comes next, per cue.

    Reset per cue, as build-spec S6.1 requires.  That is only safe because the
    state is consulted for *straight* quotes alone: a quote that is already
    “ or ” keeps its own direction whatever the state says, so a pair split
    across two cues cannot be turned inside out.  The state exists for the
    ASCII ``"`` an ASR engine emits, where nothing but the running count can
    say which half it is.
    """

    __slots__ = ("double_open", "single_open")

    def __init__(self) -> None:
        self.double_open = False
        self.single_open = False


def _section(cfg: Mapping[str, Any] | None, name: str) -> Mapping[str, Any]:
    """One config block, or an empty one — see the twin in ``s5_tokenize``."""
    value = (cfg or {}).get(name)
    return value if isinstance(value, Mapping) else {}


def _is_family(text: str, family: frozenset[str]) -> bool:
    return bool(text) and all(ch in family for ch in text)


def _map_char(ch: str, state: _QuoteState, *, ascii_punct: bool, quotes: bool) -> str:
    """Translate one punctuation character to its Chinese form.

    A quote mark that is already directional is authoritative: it tells us
    which half of the pair it is, and the state is updated from it rather than
    the other way round.  Only a straight quote is ambiguous enough to need the
    state, and then it alternates - first one opens, next one closes.
    """
    if ch == _DOUBLE_OPEN:
        state.double_open = True
        return ch
    if ch == _DOUBLE_CLOSE:
        state.double_open = False
        return ch
    if ch == _SINGLE_OPEN:
        state.single_open = True
        return ch
    if ch == _SINGLE_CLOSE:
        state.single_open = False
        return ch
    if ch in _STRAIGHT_DOUBLE:
        if not quotes:
            return ch
        out = _DOUBLE_CLOSE if state.double_open else _DOUBLE_OPEN
        state.double_open = not state.double_open
        return out
    if ch in _STRAIGHT_SINGLE:
        if not quotes:
            return ch
        out = _SINGLE_CLOSE if state.single_open else _SINGLE_OPEN
        state.single_open = not state.single_open
        return out
    if ascii_punct:
        return ASCII_PUNCT_MAP.get(ch, ch)
    return ch


def normalize_cue_tokens(
    tokens: list[Token],
    *,
    ascii_punct: bool = True,
    quotes: bool = True,
    ellipsis: str = ELLIPSIS,
    dash: str = DASH,
) -> tuple[list[Token], int]:
    """Steps 1 and 2 for one cue.  Returns ``(tokens, số dấu đã sửa)``.

    Runs of punctuation tokens are collapsed before anything is translated,
    and that is the whole trick.  ``. . .`` reaches this stage as three
    separate ``.`` tokens, because the space between them was a cluster
    boundary when the line was read; translating each one on its own would
    produce ``。。。``.  Grouping first means the run is recognised for what it
    is — one ellipsis — exactly as the README requires.

    A single ``.`` stays a single full stop, and a single ``-`` that reached
    this point as *punctuation* (rather than as a marker token) is interrupted
    speech and becomes ``——``, per README section 3.
    """
    state = _QuoteState()
    out: list[Token] = []
    changed = 0
    index = 0
    total = len(tokens)

    while index < total:
        tok = tokens[index]
        if tok.kind != KIND_PUNCT:
            out.append(tok)
            index += 1
            continue

        family = _family_of(tok.zh)
        if family is None:
            value = "".join(
                _map_char(ch, state, ascii_punct=ascii_punct, quotes=quotes)
                for ch in tok.zh
            )
            end = index + 1
        else:
            run, end = _collect_run(tokens, index, family)
            if family is _DASH_CHARS:
                value = dash
            else:
                # Dot family: two or more dots, or any ``…``, is an ellipsis;
                # one lone ASCII dot is just a full stop.
                value = ellipsis if ("…" in run or len(run) > 1) else "。"

        if tok.zh != value:
            changed += 1
        tok.zh = value          # the run collapses into its first token
        tok.pinyin = None
        out.append(tok)
        index = end

    return out, changed


def _family_of(text: str) -> frozenset[str] | None:
    """Which collapsible family this punctuation belongs to, if any."""
    for family in (_DOT_CHARS, _DASH_CHARS):
        if _is_family(text, family):
            return family
    return None


def _collect_run(
    tokens: list[Token], index: int, family: frozenset[str]
) -> tuple[str, int]:
    """The text of the punctuation run starting at ``index``, and where it ends."""
    run = ""
    end = index
    while (
        end < len(tokens)
        and tokens[end].kind == KIND_PUNCT
        and _is_family(tokens[end].zh, family)
    ):
        run += tokens[end].zh
        end += 1
    return run, end


def normalize_punctuation(doc: Document, cfg: Mapping[str, Any] | None = None) -> int:
    """Steps 1 and 2 over the whole document, in place.  Returns marks changed."""
    settings = _section(cfg, "normalize")
    ascii_punct = bool(settings.get("ascii_to_chinese", True))
    quotes = bool(settings.get("smart_quotes", True))
    ellipsis = str(settings.get("ellipsis") or ELLIPSIS)
    dash = str(settings.get("dash") or DASH)

    changed = 0
    for cue in doc.cues:
        cue.tokens, count = normalize_cue_tokens(
            cue.tokens,
            ascii_punct=ascii_punct,
            quotes=quotes,
            ellipsis=ellipsis,
            dash=dash,
        )
        changed += count
    return changed


# --------------------------------------------------------------------------- #
# step 4 — names
# --------------------------------------------------------------------------- #

def apply_names(doc: Document, names: Mapping[str, str] | None) -> tuple[int, list[str]]:
    """Step 4: give proper nouns their agreed spelling.  Returns ``(số token, cảnh báo)``.

    ``apply_casing`` has already capitalised these tokens and flagged them; what
    is left for this step is the *spelling*, which casing deliberately refuses
    to touch.  A name is matched on the whole token, never as a substring: the
    user dictionary loaded into jieba in S5 is what makes 谈胥 a single token in
    the first place, and matching substrings would rewrite 谈话 as well.

    A table entry whose pinyin contains a space is rejected rather than applied.
    Two pinyin clusters against one Chinese cluster is precisely the defect the
    README forbids ("nếu pinyin tách thành 2 cụm thì Chinese cũng phải tách"),
    and applying it would break the invariant proved in :func:`verify_invariant`
    a few lines later.  The name still gets its capital and its flag, and the
    rejection is returned so the report can ask a human to split the entry.
    """
    applied = 0
    warnings: list[str] = []
    if not names:
        return 0, warnings

    rejected: set[str] = set()
    for cue in doc.cues:
        for tok in cue.tokens:
            if tok.kind != KIND_WORD or tok.zh not in names:
                continue
            tok.add_flag(FLAG_NAME)
            spelling = " ".join(str(names[tok.zh] or "").split())
            if not spelling:
                continue
            if " " in spelling:
                if tok.zh not in rejected:
                    rejected.add(tok.zh)
                    warnings.append(
                        f"Tên riêng “{tok.zh}” trong bảng tên đang ghi pinyin "
                        f"“{spelling}” gồm nhiều cụm, trong khi chữ Hán chỉ là một "
                        "cụm. Tool giữ nguyên cách đọc cũ; hãy tách tên này thành "
                        "nhiều mục trong bảng tên."
                    )
                continue
            if tok.pinyin != spelling:
                tok.pinyin = spelling
                applied += 1
            tok.source = SOURCE_DICT
            tok.confidence = 1.0
            tok.pinyin = capitalize_pinyin(tok.pinyin or spelling)
    return applied, warnings


# --------------------------------------------------------------------------- #
# housekeeping before the proof
# --------------------------------------------------------------------------- #

def prune_empty_tokens(doc: Document) -> int:
    """Drop tokens that render to nothing, and say how many there were.

    An empty token contributes no text to either line, so the renderer skips
    it on both — but it still counts towards ``word_count``, which would make
    :func:`verify_invariant` report a renderer bug for what is really a data
    defect from an earlier stage.  Removing it here keeps the proof measuring
    the renderer, and the count is recorded in ``meta`` so the defect is
    reported rather than hidden.
    """
    removed = 0
    for cue in doc.cues:
        kept = [t for t in cue.tokens if t.zh.strip() or t.kind not in (KIND_WORD, KIND_PUNCT)]
        removed += len(cue.tokens) - len(kept)
        cue.tokens = kept
    return removed


def _cluster_count(line: str) -> int:
    """How many word clusters a reader would count on ``line``.

    Measured by running the line back through
    :func:`srtgen.core.srt.tokenize_line`, which is the exact inverse of the
    renderer and was verified round-trip on all 1351 corpus cues.  Counting by
    hand here — splitting on spaces, subtracting punctuation — would be a second
    implementation of the same rules, and the day the two disagreed the proof
    below would be proving the wrong thing.
    """
    return sum(1 for tok in tokenize_line(line) if tok.kind == KIND_WORD)


def verify_invariant(doc: Document) -> None:
    """Step 5: prove that both lines carry the same clusters, or stop the run.

    Raised as :class:`AssertionError` deliberately, and raised explicitly rather
    than with an ``assert`` statement, which ``python -O`` would delete — this
    check must survive every way the tool can be started.
    """
    for cue in doc.cues:
        zh_line = render_zh(cue.tokens)
        py_line = render_py(cue.tokens)
        expected = word_count(cue.tokens)
        zh_count = _cluster_count(zh_line)
        py_count = _cluster_count(py_line)
        if expected == zh_count == py_count:
            continue
        raise AssertionError(
            "Lỗi nội bộ của tool: hai dòng của cùng một câu không còn khớp số cụm. "
            "Đây là lỗi lập trình, không phải lỗi của file bạn đưa vào.\n"
            f"  Câu số {cue.index} ({cue.start:.3f} -> {cue.end:.3f})\n"
            f"  Số cụm theo dữ liệu: {expected}\n"
            f"  Dòng Hán   ({zh_count} cụm): {zh_line}\n"
            f"  Dòng pinyin ({py_count} cụm): {py_line}"
        )


# --------------------------------------------------------------------------- #
# the stage body
# --------------------------------------------------------------------------- #

def normalize_document(
    doc: Document,
    cfg: Mapping[str, Any] | None = None,
    *,
    names: Mapping[str, str] | None = None,
    on_progress: ProgressFn | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> Document:
    """Run the five S6 steps over ``doc``, in place, and return it.

    Cancellation is checked between steps rather than inside them: the whole
    stage takes well under a second on a 1351-cue file, so a finer grain would
    only add noise to a loop that is already fast.
    """
    cfg = cfg or {}
    names = names or {}

    report_progress(on_progress, "Đang chuẩn hoá dấu câu…", 0.1)
    stop_if_cancelled(cancelled)
    marks = normalize_punctuation(doc, cfg)                       # 1 + 2

    report_progress(on_progress, "Đang chỉnh viết hoa đầu câu…", 0.4)
    stop_if_cancelled(cancelled)
    apply_casing(doc, dict(names), dict(_section(cfg, "casing")))  # 3

    report_progress(on_progress, "Đang áp bảng tên riêng…", 0.6)
    stop_if_cancelled(cancelled)
    named, warnings = apply_names(doc, names)                      # 4

    report_progress(on_progress, "Đang kiểm tra lại hai dòng…", 0.8)
    stop_if_cancelled(cancelled)
    removed = prune_empty_tokens(doc)
    verify_invariant(doc)                                          # 5

    doc.meta["s6"] = {
        "cues": len(doc.cues),
        "punctuation_fixed": marks,
        "names_respelled": named,
        "empty_tokens_removed": removed,
        "warnings": warnings,
    }
    return doc


def run(ctx: Context, on_progress: ProgressFn | None = None) -> Document:
    """Stage entry point: ``S5_tokens.json`` in, ``S6_norm.json`` out."""
    if ctx.has_stage(STAGE, STAGE_NAME):
        saved = ctx.load_stage(STAGE, STAGE_NAME)
        if saved:
            doc = Document.from_stage_dict(saved)
            ctx.doc = doc
            report_progress(on_progress, "Dùng lại kết quả chuẩn hoá đã có sẵn.", 1.0)
            return doc

    stop_if_cancelled(ctx.is_cancelled)
    source = _load_source(ctx)
    names = load_names(names_path(ctx))

    doc = normalize_document(
        source,
        ctx.cfg,
        names=names,
        on_progress=on_progress,
        cancelled=ctx.is_cancelled,
    )

    ctx.save_stage(STAGE, STAGE_NAME, doc.to_stage_dict())
    ctx.doc = doc
    stats = doc.meta.get("s6", {})
    report_progress(
        on_progress,
        f"Xong: đã chỉnh {stats.get('punctuation_fixed', 0)} dấu câu "
        f"trên {len(doc.cues)} câu.",
        1.0,
    )
    return doc


def _load_source(ctx: Context) -> Document:
    """The S5 result, from disk if it is there and from memory otherwise."""
    saved = ctx.load_stage(SOURCE_STAGE, SOURCE_STAGE_NAME)
    if saved:
        return Document.from_stage_dict(saved)
    if ctx.doc is not None and ctx.doc.cues:
        return ctx.doc
    raise RuntimeError(
        "Chưa có kết quả tách từ (bước trước) nên chưa chuẩn hoá được. "
        f"Cần có file: {ctx.stage_path(SOURCE_STAGE, SOURCE_STAGE_NAME)}"
    )
