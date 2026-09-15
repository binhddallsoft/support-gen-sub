"""Tone sandhi — only the part that pypinyin structurally cannot see.

Why this module is deliberately small
-------------------------------------
S5 asks pypinyin for pinyin **one token at a time**
(``lazy_pinyin(tok.zh, style=Style.TONE)``), and pypinyin ships a phrase
dictionary, so every sandhi that happens *inside* a word is already correct
before this module runs: ``不是`` -> ``búshì``, ``一个`` -> ``yígè``.
Re-deriving those here would mean second-guessing a dictionary that is better
than anything we could hand-write, and would risk undoing a correct answer.

What pypinyin cannot know is what the **next token** says, because it never
sees it.  So the only work left is at the token boundary:

* a lone ``不`` in front of a fourth-tone word          -> ``bú``
* a lone ``一`` in front of anything                    -> ``yì`` / ``yí``
* a third tone in front of another third tone           -> second tone

Nothing else is touched.  A token whose pinyin already carries the right tone
passes through unchanged, so calling this twice is safe.

Corpus calibration (``corpus/completed.srt``, 1351 cues audited by hand):
``bù``x100, ``bú``x41, ``yī``x66, ``yì``x44, ``yí``x32, ``yǐ``x20.
The 41 ``bú`` are all word-internal (``búshì``, ``búyào``); the human editor
kept ``一`` as ``yī`` across boundaries (``yī bēi``, ``yī mǐ``), which is why
``cfg["yi"]`` defaults to ``False`` while ``cfg["bu"]`` defaults to ``True``.
These are house conventions, not linguistic truth — that is exactly why they
live in ``config/default.yaml`` instead of being hard-coded here.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Mapping

from srtgen.core.token import KIND_WORD, Token

__all__ = [
    "DEFAULT_SANDHI",
    "NEUTRAL_TONE",
    "NO_TONE",
    "apply_sandhi",
    "first_tone",
    "last_tone",
    "set_tone",
    "tone_of",
]


# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

#: Config shape expected by :func:`apply_sandhi`; mirrors ``config/default.yaml``.
DEFAULT_SANDHI: dict[str, bool] = {"bu": True, "yi": False, "third_tone": False}

BU = "不"
YI = "一"

#: A syllable written without any tone mark (``de``, ``le``, ``ma``).
NEUTRAL_TONE = 5
#: Returned for an empty / non-pinyin string, so callers can tell "no data"
#: apart from "genuinely neutral tone".
NO_TONE = 0

# Tone marks are stored as *combining* characters after NFD.  Reading them in
# NFD form is the only way that works for every syllable: NFC has precomposed
# code points for ā/á/ǎ/à but there is no single code point for some of the
# ü combinations, so a table of precomposed letters would have holes.
_MARK_TO_TONE: dict[str, int] = {
    chr(0x0304): 1,  # combining macron -> "a" becomes "ā"
    chr(0x0301): 2,  # combining acute  -> "a" becomes "á"
    chr(0x030C): 3,  # combining caron  -> "a" becomes "ǎ"
    chr(0x0300): 4,  # combining grave  -> "a" becomes "à"
}
_TONE_TO_MARK: dict[int, str] = {v: k for k, v in _MARK_TO_TONE.items()}

# Vowels that can carry the mark, written bare (post-NFD, pre-recomposition).
_VOWELS = "aoeiuüv"


# --------------------------------------------------------------------------- #
# reading tones
# --------------------------------------------------------------------------- #

def tone_of(syllable: str) -> int:
    """Tone number of one pinyin syllable, read from its diacritic.

    Returns 1-4 for a marked syllable, :data:`NEUTRAL_TONE` (5) for a syllable
    written without a mark, and :data:`NO_TONE` (0) for an empty string.

    Decomposing with NFD first means the function does not care whether the
    caller handed us ``"bù"`` as one precomposed code point or as ``u`` plus a
    combining grave — both forms occur in the wild, macOS hands out the second.
    """
    if not syllable:
        return NO_TONE
    for ch in unicodedata.normalize("NFD", syllable):
        tone = _MARK_TO_TONE.get(ch)
        if tone is not None:
            return tone
    return NEUTRAL_TONE


def first_tone(pinyin: str) -> int:
    """Tone of the first syllable of a possibly multi-syllable token.

    Implemented as "the first tone mark in the string" rather than by splitting
    into syllables, because Mandarin words never *begin* with a neutral-tone
    syllable — the neutral tone only ever lands on a non-initial syllable.  So
    the first mark encountered always belongs to syllable one, and we avoid
    carrying a 400-entry syllable table just to answer this question.
    """
    return tone_of(pinyin)


def last_tone(pinyin: str) -> int:
    """Tone of the last *marked* syllable of a token.

    Caveat, stated plainly because it matters for ``third_tone``: a token that
    ends in a neutral syllable (``hǎode``) reports 3, not 5, since the last
    mark belongs to ``hǎo``.  This is why ``third_tone`` ships disabled — it
    is the one rule where token-level pinyin does not carry enough information
    to be sure, and a wrong sandhi is worse than no sandhi.
    """
    if not pinyin:
        return NO_TONE
    found = NEUTRAL_TONE
    for ch in unicodedata.normalize("NFD", pinyin):
        tone = _MARK_TO_TONE.get(ch)
        if tone is not None:
            found = tone
    return found


# --------------------------------------------------------------------------- #
# writing tones
# --------------------------------------------------------------------------- #

def _mark_vowel_index(base: str) -> int:
    """Where the tone mark goes in a bare syllable, per the standard rule.

    a > o > e > (the second letter of ``iu``/``ui``) > the last vowel.
    """
    low = base.lower()
    for vowel in ("a", "o", "e"):
        idx = low.find(vowel)
        if idx >= 0:
            return idx
    for pair in ("iu", "ui"):
        idx = low.rfind(pair)
        if idx >= 0:
            return idx + 1
    for idx in range(len(low) - 1, -1, -1):
        if low[idx] in _VOWELS:
            return idx
    return -1


def set_tone(syllable: str, tone: int) -> str:
    """Rewrite one syllable with a different tone, preserving capitalisation.

    Capitalisation survives for free: only the vowel is replaced, so ``"Bù"``
    becomes ``"Bú"`` without the caller having to think about sentence case.
    Passing :data:`NEUTRAL_TONE` (or anything outside 1-4) strips the mark.
    """
    stripped = "".join(
        ch
        for ch in unicodedata.normalize("NFD", syllable)
        if ch not in _MARK_TO_TONE
    )
    base = unicodedata.normalize("NFC", stripped)
    mark = _TONE_TO_MARK.get(tone)
    if mark is None:
        return base
    idx = _mark_vowel_index(base)
    if idx < 0:
        return base
    marked = unicodedata.normalize("NFC", base[idx] + mark)
    return base[:idx] + marked + base[idx + 1:]


def _retone_last_mark(pinyin: str, tone: int) -> str:
    """Retone the last marked syllable of a multi-syllable string in place.

    Replacing the existing combining mark sidesteps syllable segmentation
    entirely: whichever vowel already carries a mark is by definition the right
    vowel to mark again.
    """
    nfd = list(unicodedata.normalize("NFD", pinyin))
    target = -1
    for idx, ch in enumerate(nfd):
        if ch in _MARK_TO_TONE:
            target = idx
    if target < 0:
        return pinyin
    mark = _TONE_TO_MARK.get(tone)
    if mark is None:
        del nfd[target]
    else:
        nfd[target] = mark
    return unicodedata.normalize("NFC", "".join(nfd))


# --------------------------------------------------------------------------- #
# the rules
# --------------------------------------------------------------------------- #

def _is_word(tok: Token) -> bool:
    return tok.kind == KIND_WORD and bool(tok.pinyin)


def _resolve(cfg: Mapping[str, Any] | None, key: str) -> bool:
    """Read one switch, tolerating both a flat cfg and a nested ``sandhi:`` block."""
    if not cfg:
        return DEFAULT_SANDHI[key]
    nested = cfg.get("sandhi")
    if isinstance(nested, Mapping) and key in nested:
        return bool(nested[key])
    if key in cfg:
        return bool(cfg[key])
    return DEFAULT_SANDHI[key]


def apply_sandhi(tokens: list[Token], cfg: dict) -> None:
    """Apply cross-boundary tone sandhi to ``tokens``, in place.

    Only *adjacent* word tokens are paired.  A punctuation or speaker marker
    between two words ends the phonological word, so ``不。是`` must not become
    ``bú。shì`` — walking the raw list and requiring ``tokens[i + 1]`` to be a
    word is what enforces that, at no cost.
    """
    do_bu = _resolve(cfg, "bu")
    do_yi = _resolve(cfg, "yi")
    do_third = _resolve(cfg, "third_tone")
    if not (do_bu or do_yi or do_third):
        return

    for idx, tok in enumerate(tokens):
        if not _is_word(tok):
            continue
        nxt = tokens[idx + 1] if idx + 1 < len(tokens) else None
        if nxt is None or not _is_word(nxt):
            continue
        assert tok.pinyin is not None and nxt.pinyin is not None  # _is_word
        nxt_tone = first_tone(nxt.pinyin)

        if do_bu and tok.zh == BU:
            # 不 keeps its fourth tone everywhere except in front of another
            # fourth tone, where it rises to the second.
            if nxt_tone == 4:
                tok.pinyin = set_tone(tok.pinyin, 2)
            continue

        if do_yi and tok.zh == YI:
            # 一 falls to the fourth tone before tones 1-3 and rises to the
            # second before tone 4.  In front of a neutral syllable there is no
            # agreed convention, so the original reading is left alone.
            if nxt_tone == 4:
                tok.pinyin = set_tone(tok.pinyin, 2)
            elif nxt_tone in (1, 2, 3):
                tok.pinyin = set_tone(tok.pinyin, 4)
            continue

        if do_third and nxt_tone == 3 and last_tone(tok.pinyin) == 3:
            tok.pinyin = _retone_last_mark(tok.pinyin, 2)
