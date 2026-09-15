"""Sentence case for the pinyin line, decided from punctuation context.

Why a rule and not a guess
--------------------------
Pinyin has no capitals of its own; the capital in ``Wǒ jiāng huǐmiè shìjiè。``
is an editorial convention that says "a sentence starts here".  The signal for
that is entirely in the punctuation, and it is far more regular than it looks.
Measured over ``corpus/completed.srt``: the rule below agrees with the human
editor on **1302 of 1313** decisions, 99.16%, once the genuinely ambiguous
``……`` cases are set aside.

Three signals, in order of how much they are trusted:

* :data:`CASE_ENDERS` — the sentence is over, the next word is capitalised.
* :data:`CASE_CONT` — the sentence continues, the next word stays lower case.
* a word with no punctuation after it — the sentence was cut mid-air by the cue
  split, so it continues: lower case.

``……`` is the one case where the corpus itself is split roughly 54/46, so
guessing is not honest.  Those words get :data:`~srtgen.core.token.FLAG_CASE_AMBIG`
so S7 and the report can put them in front of a human, and meanwhile take the
default from ``cfg["ellipsis_default"]``.

Why the walk crosses cue boundaries
------------------------------------
The build spec states the rule cue-to-cue, but punctuation does not care where
a cue happens to end: ``Shénme guǐ？Zhème lǎo。`` is one cue containing two
sentences.  Measured inside cues, ``。？！`` is followed by a capital 22 times
out of 22, and a speaker marker by a capital 15 times out of 16.  So the walk
treats the document as one token stream and applies one rule everywhere, which
is both simpler and measurably right.

What is deliberately *not* touched
-----------------------------------
A mid-cue word that is not preceded by a sentence ender or a marker is left
exactly as it was.  Forcing those to lower case would destroy the capital on
any proper name that is not yet in ``names.json`` — and 15 of the corpus's
mid-cue capitals are precisely that.  Lower case is only ever *forced* on the
first word of a cue, which is the position the rule was measured on.
"""

from __future__ import annotations

from typing import Any, Mapping

from srtgen.core.token import (
    FLAG_CASE_AMBIG,
    FLAG_NAME,
    KIND_MARKER,
    KIND_PUNCT,
    KIND_WORD,
    Document,
    Token,
)

__all__ = [
    "CASE_AMBIG",
    "CASE_CONT",
    "CASE_ENDERS",
    "ELLIPSIS_DEFAULT",
    "apply_casing",
    "capitalize_pinyin",
    "lower_pinyin",
]


#: Closing punctuation that ends a sentence -> the next word is capitalised.
CASE_ENDERS: set[str] = set("。？！”》）")

#: Punctuation that continues a sentence -> the next word stays lower case.
CASE_CONT: set[str] = set("，、：；’")

#: Punctuation the corpus itself does not decide consistently.
CASE_AMBIG: tuple[str, ...] = ("……",)

#: Used when ``cfg`` says nothing.  "upper" wins the corpus coin-flip 54/46.
ELLIPSIS_DEFAULT = "upper"

_PENDING_UPPER = "upper"
_PENDING_LOWER = "lower"
_PENDING_AMBIG = "ambig"


# --------------------------------------------------------------------------- #
# case on a single token, tone marks intact
# --------------------------------------------------------------------------- #

# Guard rail rather than documentation: capitalising a marked vowel must not
# lose the mark.  CPython's str.upper() maps each of these to a single
# precomposed capital, but the whole module rests on that, so it is checked at
# import time instead of being assumed.  A plain assert would vanish under
# ``python -O``, hence the explicit raise.
_UPPER_SPOT_CHECK: dict[str, str] = {
    "ā": "Ā",
    "ǎ": "Ǎ",
    "é": "É",
    "ǹ": "Ǹ",
    "ǚ": "Ǚ",
    "ü": "Ü",
}


def _verify_upper_mapping() -> None:
    broken = {k: k.upper() for k, v in _UPPER_SPOT_CHECK.items() if k.upper() != v}
    if broken:
        raise RuntimeError(
            "Phiên bản Python này viết hoa sai chữ pinyin có dấu thanh: "
            f"{broken!r}. Không thể tiếp tục vì dòng pinyin sẽ mất dấu."
        )


_verify_upper_mapping()


def capitalize_pinyin(pinyin: str) -> str:
    """Upper-case the first alphabetic character only, keeping its tone mark.

    Only the first *letter* is touched, not ``pinyin[0]``: a token can start
    with something else once markers and quotes are in play, and ``ǎ`` must
    come back as ``Ǎ``, not as a bare ``A`` with the mark dropped.
    """
    for idx, ch in enumerate(pinyin):
        if ch.isalpha():
            return pinyin[:idx] + ch.upper() + pinyin[idx + 1:]
    return pinyin


def lower_pinyin(pinyin: str) -> str:
    """Lower-case the first alphabetic character, leaving acronyms alone.

    ``PK`` appears in the corpus as a word token.  Lower-casing only the first
    letter would turn it into ``pK``, so a second capital immediately after the
    first is read as "this is an acronym, hands off".
    """
    for idx, ch in enumerate(pinyin):
        if ch.isalpha():
            rest = pinyin[idx + 1:]
            if rest[:1].isupper():
                return pinyin
            return pinyin[:idx] + ch.lower() + rest
    return pinyin


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _add_flag(tok: Token, flag: str) -> None:
    if flag not in tok.flags:
        tok.flags.append(flag)


def _is_ellipsis(zh: str) -> bool:
    """True for ``……`` and for the not-yet-normalised ``…`` spelling.

    S6 runs punctuation normalisation before casing, so ``……`` is the expected
    form; accepting a bare ``…`` as well means the ``fix`` command still reads
    the ambiguity correctly if the two stages are ever reordered.
    """
    if not zh:
        return False
    if any(zh.endswith(mark) for mark in CASE_AMBIG):
        return True
    return set(zh) == {"…"}


def _pending_after_punct(zh: str, pending: str) -> str:
    """New pending state after a punctuation token, or the old one unchanged.

    Opening brackets and quotes (``《 “ ‘ （``) and the interruption dash
    ``——`` fall through deliberately: they carry no sentence boundary of their
    own, so ``。《`` must still capitalise the word after the bracket, exactly
    as cue 4 of the corpus does with ``《Lóng quán xiǎozi》``.
    """
    if _is_ellipsis(zh):
        return _PENDING_AMBIG
    last = zh[-1:]
    if last in CASE_ENDERS:
        return _PENDING_UPPER
    if last in CASE_CONT:
        return _PENDING_LOWER
    return pending


def _ellipsis_default(cfg: Mapping[str, Any] | None) -> str:
    """Read ``ellipsis_default`` from a flat cfg or from a nested ``casing:`` block."""
    value: Any = None
    if cfg:
        nested = cfg.get("casing")
        if isinstance(nested, Mapping):
            value = nested.get("ellipsis_default")
        if value is None:
            value = cfg.get("ellipsis_default")
    if value not in (_PENDING_UPPER, _PENDING_LOWER):
        return ELLIPSIS_DEFAULT
    return str(value)


# --------------------------------------------------------------------------- #
# the stage entry point
# --------------------------------------------------------------------------- #

def apply_casing(doc: Document, names: dict, cfg: dict) -> None:
    """Set sentence case on every pinyin token of ``doc``, in place.

    ``names`` is consulted by token text only: a token whose ``zh`` is a known
    proper name is capitalised wherever it stands and flagged
    :data:`~srtgen.core.token.FLAG_NAME`.  Rewriting a name's *spelling* is the
    job of the names stage, not of this one — mixing the two would make the
    99% measurement above impossible to reproduce.
    """
    ellipsis_default = _ellipsis_default(cfg)
    known_names = set(names or ())

    # The first word of the file starts a sentence by definition.
    pending = _PENDING_UPPER

    for cue in doc.cues:
        at_cue_start = True
        for tok in cue.tokens:
            if tok.kind == KIND_MARKER:
                # A marker opens a new speaker turn, which always starts a
                # sentence.  Corpus: capital after a marker 15 times out of 16.
                pending = _PENDING_UPPER
                continue

            if tok.kind == KIND_PUNCT:
                pending = _pending_after_punct(tok.zh, pending)
                continue

            if tok.kind != KIND_WORD or not tok.pinyin:
                continue

            if tok.zh in known_names:
                tok.pinyin = capitalize_pinyin(tok.pinyin)
                _add_flag(tok, FLAG_NAME)
            elif pending == _PENDING_AMBIG:
                # Nobody can tell from the text alone; record that and apply the
                # house default so the file is still consistent on its own.
                _add_flag(tok, FLAG_CASE_AMBIG)
                if ellipsis_default == _PENDING_UPPER:
                    tok.pinyin = capitalize_pinyin(tok.pinyin)
                elif at_cue_start:
                    tok.pinyin = lower_pinyin(tok.pinyin)
            elif pending == _PENDING_UPPER:
                tok.pinyin = capitalize_pinyin(tok.pinyin)
            elif at_cue_start:
                # Forcing lower case is confined to this position; see the
                # module docstring for why mid-cue words are left alone.
                tok.pinyin = lower_pinyin(tok.pinyin)

            pending = _PENDING_LOWER
            at_cue_start = False
