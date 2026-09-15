"""Token model and the two-line renderer - the structural heart of srtgen.

Rationale (plan section 1, build-spec section 0): the Chinese line and the
pinyin line are never produced independently.  They are two renderings of one
token list, so a cluster-count mismatch between the two lines is impossible by
construction instead of being something a validator has to hunt down and a
human has to repair.  Every operation on punctuation, spacing or casing must
therefore edit tokens and re-render; nothing in srtgen is allowed to run a
regex over an already joined line.

The join rules implemented in :func:`render_tokens` were verified round-trip
against the 1351 audited cues of ``corpus/completed.srt`` (1351/1351), so they
are treated here as measured facts rather than preferences.  Two guarantees
follow from them and are relied upon by the whole pipeline:

* ``line == line.strip()`` - never a leading or trailing space
* ``"  " not in line`` - never two spaces in a row

Those guarantees are produced by the emit loop itself.  There is deliberately
no post-processing ``strip()``/``replace()`` step: a cleanup pass would hide
renderer bugs instead of making them impossible.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Final, Iterable

__all__ = [
    "KIND_WORD",
    "KIND_PUNCT",
    "KIND_MARKER",
    "KIND_MARKUP",
    "KINDS",
    "MARKER_TEXT",
    "FLAG_HETERONYM",
    "FLAG_CASE_AMBIG",
    "FLAG_AI_APPLIED",
    "FLAG_NAME",
    "SOURCE_JIEBA",
    "SOURCE_DICT",
    "SOURCE_AI",
    "SOURCE_MANUAL",
    "SOURCE_PUNCT",
    "SOURCE_ASR",
    "SOURCES",
    "FIELD_ZH",
    "FIELD_PINYIN",
    "BUNDLE_KIND",
    "Token",
    "Cue",
    "Document",
    "render_tokens",
    "render_zh",
    "render_py",
    "word_count",
    "is_han",
    "is_closing_tag",
]


# --------------------------------------------------------------------------- #
# vocabulary
# --------------------------------------------------------------------------- #

KIND_WORD: Final[str] = "word"
KIND_PUNCT: Final[str] = "punct"
KIND_MARKER: Final[str] = "marker"

#: An SRT formatting tag (``<i>``, ``</i>``, ``<b>``, ``<font color="…">``).
#:
#: A kind of its own because a tag is neither dialogue nor punctuation: it is
#: not a cluster (so it never counts towards :func:`word_count` and never gets
#: a pinyin), it is not punctuation (so S6 never "fixes" its ``<`` ``>`` or
#: ``"`` into ``《》`` / ``“”``), and it is not a speaker marker.  It lives here,
#: in the module every other module imports, so that a tag survives *every*
#: round trip - bundle, stage file, renderer.  While the constant lived only in
#: ``srt.py`` the loaders below coerced an unknown kind to ``word``, so a stage
#: file or a bundle reloaded ``<i>`` as a word cluster with a pinyin.
KIND_MARKUP: Final[str] = "markup"

KINDS: Final[tuple[str, ...]] = (KIND_WORD, KIND_PUNCT, KIND_MARKER, KIND_MARKUP)

#: The speaker-change marker stays ASCII ``-`` on purpose: the README keeps it
#: as a structural marker (not punctuation) so plain SRT players do not choke.
MARKER_TEXT: Final[str] = "-"

# Flags attached to a token so S7 and the HTML report know exactly where a
# human still has to look.  Kept as plain strings so they survive a JSON
# round-trip through the stage files without any custom encoder.
FLAG_HETERONYM: Final[str] = "HETERONYM"      # 多音字, reading not yet certain
FLAG_CASE_AMBIG: Final[str] = "CASE_AMBIG"    # capitalisation after ...... is 54/46
FLAG_AI_APPLIED: Final[str] = "AI_APPLIED"    # AI changed this, needs review
FLAG_NAME: Final[str] = "NAME"                # proper noun

SOURCE_JIEBA: Final[str] = "jieba"
SOURCE_DICT: Final[str] = "dict"
SOURCE_AI: Final[str] = "ai"
SOURCE_MANUAL: Final[str] = "manual"
SOURCE_PUNCT: Final[str] = "punct"
SOURCE_ASR: Final[str] = "asr"

SOURCES: Final[tuple[str, ...]] = (
    SOURCE_JIEBA,
    SOURCE_DICT,
    SOURCE_AI,
    SOURCE_MANUAL,
    SOURCE_PUNCT,
    SOURCE_ASR,
)

#: Which attribute a render pass reads for word tokens.
FIELD_ZH: Final[str] = "zh"
FIELD_PINYIN: Final[str] = "pinyin"

#: CJK Unified Ideographs.  Used only for counting Han characters per cue, so
#: the basic block is enough - the extension blocks never appear in this corpus
#: and counting them would silently change the S4 cue-length statistics.
_HAN_START: Final[int] = 0x4E00
_HAN_END: Final[int] = 0x9FFF

#: Internal kind -> the vocabulary the README uses in ``bundle.json``.  The
#: backend contract in ``docs/format-contract.md`` spells punctuation out in
#: full ("segment punctuation"), while the internal constant stays short.
#: Mapping here keeps both documents true at the same time.
BUNDLE_KIND: Final[dict[str, str]] = {
    KIND_WORD: "word",
    KIND_PUNCT: "punctuation",
    KIND_MARKER: "marker",
    KIND_MARKUP: "markup",
}

#: Accepts both vocabularies on the way in, because ``fix`` will be pointed at
#: bundles written by the old backend as well as at our own stage files.
_KIND_ALIASES: Final[dict[str, str]] = {
    "word": KIND_WORD,
    "punct": KIND_PUNCT,
    "punctuation": KIND_PUNCT,
    "marker": KIND_MARKER,
    "speaker": KIND_MARKER,
    "markup": KIND_MARKUP,
    "tag": KIND_MARKUP,
}


def is_han(ch: str) -> bool:
    """True for a CJK ideograph, used to measure cue length the way S4 does."""
    return len(ch) == 1 and _HAN_START <= ord(ch) <= _HAN_END


def is_closing_tag(text: str) -> bool:
    """``</i>`` closes; ``<i>`` and ``<font …>`` open.

    That one bit is all the renderer needs to know which neighbour a tag
    belongs to (contract C): an opening tag hugs the token after it, a closing
    tag hugs the token before it.
    """
    return text.lstrip().startswith("</")


def _coerce_kind(raw: Any) -> str:
    """Map any incoming kind string to an internal constant, defaulting to word.

    Unknown kinds become ``word`` rather than raising: a malformed bundle must
    still load so the validator can report a Vietnamese finding about it, which
    is far more useful to a non-technical user than a traceback.
    """
    if isinstance(raw, str):
        return _KIND_ALIASES.get(raw.strip().lower(), KIND_WORD)
    return KIND_WORD


# --------------------------------------------------------------------------- #
# Token
# --------------------------------------------------------------------------- #

@dataclass
class Token:
    """One cluster of the subtitle: a word, a punctuation mark, or a marker.

    ``zh`` and ``pinyin`` are two views of the *same* cluster, which is why
    they live on one object.  ``confidence``/``source``/``flags`` are working
    metadata for the pipeline; they travel in the intermediate ``S*.json``
    files and are dropped from ``bundle.json``, which the backend consumes.
    """

    kind: str = KIND_WORD
    zh: str = ""
    pinyin: str | None = None          # None for punct/marker
    confidence: float = 1.0
    source: str = SOURCE_JIEBA         # jieba | dict | ai | manual | punct | asr
    flags: list[str] = dataclasses.field(default_factory=list)

    # -- queries ---------------------------------------------------------- #

    def is_word(self) -> bool:
        return self.kind == KIND_WORD

    def is_punct(self) -> bool:
        return self.kind == KIND_PUNCT

    def is_marker(self) -> bool:
        return self.kind == KIND_MARKER

    def is_markup(self) -> bool:
        return self.kind == KIND_MARKUP

    def has_flag(self, flag: str) -> bool:
        return flag in self.flags

    def add_flag(self, flag: str) -> None:
        """Add a flag once.  Stages run more than once on resume, and a report
        listing the same warning three times would erode trust in the report."""
        if flag not in self.flags:
            self.flags.append(flag)

    # -- serialisation ---------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        """Shape required by the README section "Yêu cầu đối với backend".

        Note the key is ``text``, not ``zh``, and punctuation/markers carry an
        explicit ``null`` pinyin - the backend uses that null to decide what is
        vocabulary and what is not.  Pipeline metadata is intentionally absent;
        use :meth:`to_stage_dict` for the intermediate files.
        """
        return {
            "kind": BUNDLE_KIND.get(self.kind, KIND_WORD),
            "text": self.zh,
            "pinyin": self.pinyin if self.is_word() else None,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Token":
        """Read the bundle shape.  ``zh`` is accepted as an alias for ``text``
        so a stage file can be reloaded through this path without surprises."""
        kind = _coerce_kind(d.get("kind"))
        zh = d.get("text")
        if zh is None:
            zh = d.get("zh", "")
        pinyin = d.get("pinyin")
        return cls(
            kind=kind,
            zh=str(zh or ""),
            pinyin=pinyin if (kind == KIND_WORD and pinyin) else None,
            source=str(d.get("source") or _default_source(kind)),
        )

    def to_stage_dict(self) -> dict[str, Any]:
        """Full state for ``work/<id>/S*.json``.

        Keyed on ``zh`` (not ``text``) so that a stage file is visibly a
        different artefact from a bundle when someone opens it by hand, and so
        that confidence/source/flags survive a resume without being guessed.
        """
        return {
            "kind": self.kind,
            "zh": self.zh,
            "pinyin": self.pinyin,
            "confidence": self.confidence,
            "source": self.source,
            "flags": list(self.flags),
        }

    @classmethod
    def from_stage_dict(cls, d: dict[str, Any]) -> "Token":
        kind = _coerce_kind(d.get("kind"))
        zh = d.get("zh")
        if zh is None:
            zh = d.get("text", "")
        pinyin = d.get("pinyin")
        flags = d.get("flags") or []
        try:
            confidence = float(d.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        return cls(
            kind=kind,
            zh=str(zh or ""),
            pinyin=str(pinyin) if pinyin is not None else None,
            confidence=confidence,
            source=str(d.get("source") or _default_source(kind)),
            flags=[str(f) for f in flags],
        )


def _default_source(kind: str) -> str:
    """Punctuation never comes from a segmenter, so labelling it ``jieba``
    would make the report lie about where a token came from."""
    return SOURCE_PUNCT if kind in (KIND_PUNCT, KIND_MARKER, KIND_MARKUP) else SOURCE_JIEBA


# --------------------------------------------------------------------------- #
# renderer
# --------------------------------------------------------------------------- #

def _content(tok: Token, field_name: str) -> str:
    """The literal text this token contributes to the requested line.

    Markers render as ``-`` on both lines and punctuation renders from ``zh``
    on both lines, because the README requires punctuation to be identical and
    positionally synchronised between the Chinese and the pinyin line.

    The value is stripped here - on the token, never on the joined line.  A
    token carrying stray whitespace is a data defect, and letting it through
    would break the no-leading/no-double-space guarantee that the rest of the
    architecture is allowed to assume.
    """
    if tok.kind == KIND_MARKER:
        return MARKER_TEXT
    if tok.kind in (KIND_PUNCT, KIND_MARKUP):
        # A tag is formatting: byte-for-byte the same on both lines, never pinyin.
        return (tok.zh or "").strip()
    if field_name == FIELD_PINYIN:
        # Fallback to zh so a cue that has not reached S5 yet still renders two
        # lines with the same cluster count instead of collapsing to one.
        return ((tok.pinyin or "").strip()) or (tok.zh or "").strip()
    return (tok.zh or "").strip()


def render_tokens(tokens: list[Token], field: str) -> str:
    """Join tokens into one subtitle line.

    The rule table (build-spec section 1), verified 1351/1351 on the corpus:

    ==========  ====================================================
    token       what is emitted
    ==========  ====================================================
    ``word``    one space first **iff** the previous token was a word
    ``punct``   the content alone - no space before, none after
    ``marker``  one space before (skipped at line start), ``-``,
                one space after
    ``markup``  never a space of its own - see contract C below
    ==========  ====================================================

    The space that follows a marker is *owed*, not written immediately: it is
    paid by the next token that actually produces text.  That single detail is
    what makes a trailing marker, an empty token or two markers in a row unable
    to produce a stray or doubled space, so the two guarantees in the module
    docstring hold for every possible token list rather than for well-formed
    ones only.

    SRT tags (contract C) are *transparent* to the table above: every spacing
    decision is taken between the visible tokens exactly as if the tags were not
    there, and only then are the tags glued on - an opening tag to the visible
    token after it (so the space, if any, stands in front of the tag), a closing
    tag to the visible token before it.  That gives ``他 说 <i>好</i>。`` and
    ``<i>- 你 好。</i>``: a marker right after an opening tag at the start of
    the line is still "at line start".  Consequently, deleting the tags from
    the output always yields exactly the tag-free rendering - the property the
    tests pin down.  Tags keep their original order: a closing tag that arrives
    while opening tags are still waiting for their token queues up behind them
    instead of jumping ahead.

    Empty tokens are skipped entirely (no space, no state change): emitting a
    separator around nothing is the one way this loop could violate its own
    contract.
    """
    if field not in (FIELD_ZH, FIELD_PINYIN):
        raise ValueError(
            f"field phải là {FIELD_ZH!r} hoặc {FIELD_PINYIN!r}, nhận được {field!r}"
        )

    parts: list[str] = []
    waiting_tags: list[str] = []      # tags that belong to the next visible token
    prev_kind: str | None = None      # kind of the last *visible* token
    visible = False                   # has any visible token been written yet?
    owed_space = False

    for tok in tokens:
        if tok.kind == KIND_MARKUP:
            tag = _content(tok, field)
            if not tag:
                continue
            if is_closing_tag(tag) and visible and not waiting_tags:
                parts.append(tag)          # hugs the token before it
            else:
                waiting_tags.append(tag)   # hugs the token after it
            continue

        if tok.kind == KIND_MARKER:
            if visible:                    # never a space at line start
                parts.append(" ")
            parts.extend(waiting_tags)
            waiting_tags.clear()
            parts.append(MARKER_TEXT)
            owed_space = True
            prev_kind = KIND_MARKER
            visible = True
            continue

        text = _content(tok, field)
        if not text:
            continue

        if owed_space or (tok.kind == KIND_WORD and prev_kind == KIND_WORD):
            parts.append(" ")
        owed_space = False
        parts.extend(waiting_tags)
        waiting_tags.clear()
        parts.append(text)
        prev_kind = tok.kind
        visible = True

    parts.extend(waiting_tags)             # a tag with no visible token after it
    return "".join(parts)


def render_zh(tokens: list[Token]) -> str:
    """The Chinese line."""
    return render_tokens(tokens, FIELD_ZH)


def render_py(tokens: list[Token]) -> str:
    """The pinyin line - same token list, same structure, different field."""
    return render_tokens(tokens, FIELD_PINYIN)


def word_count(tokens: Iterable[Token]) -> int:
    """Number of vocabulary clusters, ignoring punctuation, markers and SRT tags.

    This is the number both rendered lines must agree on.  ``rules.py`` asserts
    it against the clusters it counts back off each line; a mismatch means the
    renderer has a bug, not that the data is bad.
    """
    return sum(1 for t in tokens if t.kind == KIND_WORD)


# --------------------------------------------------------------------------- #
# Cue
# --------------------------------------------------------------------------- #

@dataclass
class Cue:
    """One SRT block: an index, a time range, and the token list both lines
    are rendered from."""

    index: int
    start: float                       # seconds
    end: float
    tokens: list[Token] = dataclasses.field(default_factory=list)

    # -- queries ---------------------------------------------------------- #

    def words(self) -> list[Token]:
        return [t for t in self.tokens if t.kind == KIND_WORD]

    def zh_text(self) -> str:
        return render_zh(self.tokens)

    def py_text(self) -> str:
        return render_py(self.tokens)

    def word_count(self) -> int:
        return word_count(self.tokens)

    def han_count(self) -> int:
        """Han characters on the rendered Chinese line.

        Measured on the rendered line rather than by summing tokens so it
        matches what the S4 ``max_chars`` limit (18, from the corpus) actually
        governs: what the viewer reads.
        """
        return sum(1 for ch in self.zh_text() if is_han(ch))

    def duration(self) -> float:
        """Raw ``end - start``; not clamped, so ``TIMESTAMP_ORDER`` can see a
        negative value instead of a silently repaired zero."""
        return self.end - self.start

    # -- serialisation ---------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "tokens": [t.to_dict() for t in self.tokens],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Cue":
        return cls(
            index=int(d.get("index", 0)),
            start=float(d.get("start", 0.0)),
            end=float(d.get("end", 0.0)),
            tokens=[Token.from_dict(t) for t in d.get("tokens", [])],
        )

    def to_stage_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "tokens": [t.to_stage_dict() for t in self.tokens],
        }

    @classmethod
    def from_stage_dict(cls, d: dict[str, Any]) -> "Cue":
        return cls(
            index=int(d.get("index", 0)),
            start=float(d.get("start", 0.0)),
            end=float(d.get("end", 0.0)),
            tokens=[Token.from_stage_dict(t) for t in d.get("tokens", [])],
        )


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #

@dataclass
class Document:
    """A whole subtitle file plus the provenance needed to name the outputs.

    ``meta`` holds ``video_id``, ``title``, ``source_url``, ``model`` and
    whatever else a stage wants to hand forward.  It is a plain dict on purpose:
    stages are added over time and a typed schema here would force an edit to
    this module - the one module every other module imports - each time.
    """

    cues: list[Cue] = dataclasses.field(default_factory=list)
    meta: dict[str, Any] = dataclasses.field(default_factory=dict)

    # -- queries ---------------------------------------------------------- #

    def word_count(self) -> int:
        return sum(c.word_count() for c in self.cues)

    def han_count(self) -> int:
        return sum(c.han_count() for c in self.cues)

    def duration(self) -> float:
        """End of the last cue; 0.0 for an empty document."""
        return max((c.end for c in self.cues), default=0.0)

    def renumber(self) -> None:
        """Re-index cues from 1.  ``emit_srt`` never keeps the original numbers,
        so any stage that inserts or drops a cue calls this instead of trying to
        patch indices by hand."""
        for i, cue in enumerate(self.cues, start=1):
            cue.index = i

    # -- serialisation ---------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        """Bundle shape: metadata plus cues whose tokens use the README keys."""
        return {
            "meta": dict(self.meta),
            "cues": [c.to_dict() for c in self.cues],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Document":
        return cls(
            cues=[Cue.from_dict(c) for c in d.get("cues", [])],
            meta=dict(d.get("meta", {})),
        )

    def to_stage_dict(self) -> dict[str, Any]:
        return {
            "meta": dict(self.meta),
            "cues": [c.to_stage_dict() for c in self.cues],
        }

    @classmethod
    def from_stage_dict(cls, d: dict[str, Any]) -> "Document":
        return cls(
            cues=[Cue.from_stage_dict(c) for c in d.get("cues", [])],
            meta=dict(d.get("meta", {})),
        )
