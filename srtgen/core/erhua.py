"""儿化音 — keeping the ``儿`` suffix inside the word it belongs to.

Why this lives in the tokenizer and not in a later clean-up pass
----------------------------------------------------------------
``儿`` at the end of a word is a *suffix*, not a syllable of its own: 哪儿 is
one word read ``nǎr``, one Chinese cluster paired with one pinyin cluster.  If
``儿`` is ever allowed to become its own token, the two rendered lines still
have the same number of clusters, so the structural invariant in
``core/token.py`` cannot catch it — the file looks valid and reads wrong.  The
README (section 1) makes this an outright error, and ``rules.py`` reports it as
``ERHUA_SPLIT``.  The only place to fix it cheaply is here, right after jieba.

Why the contract is the README and not the corpus
--------------------------------------------------
``pypinyin`` transcribes ``儿`` as a full syllable — 哪儿 -> ``nǎér``,
一会儿 -> ``yīhuìer`` — and ``corpus/completed.srt`` was produced with pypinyin,
so it carries those spellings in seven cues.  Those cues are **corpus errors**,
listed in ``tests/known_corpus_errors.py`` as ``ERHUA_CONTRACTION_CUES``.  The
README is the contract: ``nǎr``, ``zhèr``, ``nàr``, ``yíhuìr``, ``yíkuàir``,
``diǎnr``.  Never ``nǎér``, ``zhèér``, ``yīhuìer``.

Why ``儿`` at the *head* of a word is untouchable
-------------------------------------------------
儿子 (``érzi``), 儿童 (``értóng``): here ``儿`` is the root, carrying its own
tone and its own syllable.  Every rule below keys on ``儿`` being the **last**
character of a word with something in front of it, which excludes those by
construction.  A second guard, :data:`ERHUA_BLOCK`, covers the words where
``儿`` is final but still a full syllable — 女儿 ``nǚ'ér``, 婴儿 ``yīng'ér``.
"""

from __future__ import annotations

import unicodedata

from srtgen.core.token import KIND_WORD, Token

__all__ = [
    "ER",
    "ERHUA_BLOCK",
    "ERHUA_EXCEPTIONS",
    "ERHUA_HEAD",
    "erhua_pinyin",
    "is_erhua_word",
    "is_full_er_form",
    "merge_erhua",
]


ER = "儿"


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #

#: Stems that commonly take the ``儿`` suffix.  Both whole words and single
#: characters are listed; the lookup tries the whole stem first and then its
#: last character, so 远点 matches through 点 without needing its own entry.
#:
#: This list is *positive evidence*, used only when jieba has already produced
#: one token such as ``哪儿``.  Missing an entry costs nothing worse than an
#: uncontracted spelling that S7/the report will surface; a wrong entry would
#: silently corrupt a real ``ér`` syllable, which is why the list stays
#: conservative and only grows from observed drama dialogue.
ERHUA_HEAD: frozenset[str] = frozenset(
    {
        # pronouns and place words
        "哪", "这", "那",
        # quantity and degree
        "点", "些", "块", "个", "下", "份",
        # time
        "会", "今", "明", "昨", "早", "晚", "夜", "阵",
        # everyday colloquial nouns
        "事", "味", "花", "孩", "玩", "空", "活", "头", "面", "边", "底",
        "门", "缝", "圈", "画", "片", "皮", "刀", "台", "心", "眼", "嘴",
        "腿", "鸟", "猫", "狗", "鱼", "球", "本", "词", "曲", "号", "声",
        "样", "劲", "角", "尖", "口", "手", "脚", "肚", "脸", "座", "伙",
        # multi-character stems worth naming explicitly
        "一会", "一块", "一点", "一下", "这会", "那会", "待会", "等会",
        "好玩", "没事", "小孩", "小伙", "差点", "有点", "快点", "慢点",
        "早点", "晚点", "远点",
    }
)

#: Stems after which ``儿`` is a full syllable, not a suffix — the exception
#: table.  Checked before anything else, in both directions, so neither the
#: merge nor the contraction can touch 女儿 / 婴儿 / 孤儿.
ERHUA_BLOCK: frozenset[str] = frozenset(
    {
        "女", "男", "婴", "幼", "孤", "育", "托", "胎", "健", "宠", "少",
        "生", "弃", "侄", "孙", "干", "混", "雏",
        "女儿", "男儿", "婴儿", "幼儿", "孤儿", "育儿", "托儿", "胎儿",
        "健儿", "宠儿", "少儿", "生儿", "弃儿", "侄儿", "孙儿", "干儿",
        ER,
    }
)

#: Words whose contracted pinyin is fixed by the README and cannot be derived
#: from pypinyin's output.  Every entry here is a ``一`` compound: pypinyin
#: reads the ``一`` in isolation (``yīhuìer``) while the README spells the
#: lexicalised form (``yíhuìr``).  ``cfg["yi"]`` in ``sandhi.py`` stays off
#: because the corpus keeps ``yī`` across token boundaries — but these are not
#: boundaries, they are single words with a spelling the README dictates.
ERHUA_EXCEPTIONS: dict[str, str] = {
    "一会儿": "yíhuìr",
    "一块儿": "yíkuàir",
    "一点儿": "yìdiǎnr",
    "一下儿": "yíxiàr",
    "一半儿": "yíbànr",
}

# The ``儿`` syllable as pypinyin writes it, in every tone it can appear in.
_ER_TAILS: tuple[str, ...] = ("er", "ēr", "ér", "ěr", "èr")

# A pinyin syllable can only end in a vowel, ``n``, ``ng`` or ``r``.  This is
# what lets us tell ``nǎ|ér`` (two syllables, contract it) from ``zhèr`` (one
# syllable that merely happens to end in the letters ``èr``).
_SYLLABLE_TAIL_VOWELS = "aoeiuüv"


# --------------------------------------------------------------------------- #
# pinyin
# --------------------------------------------------------------------------- #

def _bare(text: str) -> str:
    """Drop tone marks so tails can be compared as plain letters."""
    return "".join(
        ch
        for ch in unicodedata.normalize("NFD", text)
        if not unicodedata.combining(ch)
    )


def _ends_like_syllable(text: str) -> bool:
    """True when ``text`` could be a complete pinyin syllable on its own."""
    if not text:
        return False
    bare = _bare(text).lower()
    if not bare:
        return False
    if bare[-1] in _SYLLABLE_TAIL_VOWELS:
        return True
    return bare.endswith("ng") or bare[-1] == "n"


def _drop_er_syllable(pinyin: str) -> str:
    """Remove a trailing ``ér``/``er`` syllable, but only when it really is one.

    The test that matters: what is left over must itself end like a syllable.
    ``nǎér`` -> ``nǎ`` (ends in a vowel, accept), ``diǎnér`` -> ``diǎn`` (ends
    in ``n``, accept), ``kòngér`` -> ``kòng`` (accept).  Against that,
    ``zhèr`` -> ``zh`` and ``gēr`` -> ``g`` end in a bare initial, which no
    syllable does, so those two are already contracted and are left alone.
    Without this check the idempotency of :func:`erhua_pinyin` breaks and
    ``zhèr`` would rot into ``zhr`` on a second pass.
    """
    if len(pinyin) < 3:
        return pinyin
    tail = pinyin[-2:].lower()
    if tail not in _ER_TAILS:
        return pinyin
    stem = pinyin[:-2]
    return stem if _ends_like_syllable(stem) else pinyin


def is_full_er_form(pinyin: str) -> bool:
    """True when ``儿`` is spelled as its own syllable (``nǎér``) instead of ``nǎr``.

    Delegates to :func:`_drop_er_syllable` on purpose.  The naive test
    ``pinyin.endswith("er")`` cannot tell the correct ``zhèr`` from the wrong
    ``zhèér`` once tone marks are stripped, which is exactly the false positive
    that made the validator reject valid files.
    """
    return _drop_er_syllable(pinyin) != pinyin


def is_erhua_word(zh: str) -> bool:
    """True only when ``zh`` is *certainly* an 儿化音 word.

    Deliberately conservative: an unknown word answers False rather than True.
    The validator's hard gate is "zero false positives", so a missed detection
    costs a warning nobody sees, while a wrong detection makes the tool reject
    a file that is actually correct — the failure the user would never forgive.

    ``女儿``/``婴儿``/``孤儿`` carry a full ``ér`` syllable and are listed in
    :data:`ERHUA_BLOCK`; they must never be contracted to ``nǚr``.
    """
    if len(zh) < 2 or not zh.endswith(ER):
        return False
    if zh in ERHUA_EXCEPTIONS:
        return True
    if zh in ERHUA_BLOCK:
        return False
    stem = zh[:-1]
    if stem in ERHUA_BLOCK:
        return False
    return stem in ERHUA_HEAD


def erhua_pinyin(base_pinyin: str) -> str:
    """Pinyin of ``<base>儿``: the base spelling with an ``r`` glued on.

    ``nǎ`` -> ``nǎr``, ``diǎn`` -> ``diǎnr``, ``wán`` -> ``wánr``,
    ``kòng`` -> ``kòngr``, ``huì`` -> ``huìr``.  The tone mark of the base
    syllable is never moved or rewritten — erhua colours the vowel, it does not
    change the tone.

    Note on the build spec's wording: it says to drop a final ``n``/``ng``
    before adding ``r``.  That describes the *phonetics*; Hanyu Pinyin
    orthography keeps the consonant, and all six worked examples in the spec
    and the README keep it too (``diǎnr``, not ``diǎr``).  The examples are the
    observable contract, so the spelling rule is what is implemented here.

    Accepts either the bare base (``nǎ``) or a form that still carries the
    separate ``儿`` syllable (``nǎér``, ``yīhuìer``), because pypinyin produces
    the latter whenever jieba hands it the whole word.  Already-contracted
    input is returned unchanged, so the function is safe to apply twice.
    """
    if not base_pinyin:
        return base_pinyin
    base = _drop_er_syllable(base_pinyin)
    if base[-1:] in ("r", "R"):
        return base
    return base + "r"


def _match_lead_case(pinyin: str, model: str | None) -> str:
    """Give ``pinyin`` the same leading case as ``model``.

    A fixed spelling from :data:`ERHUA_EXCEPTIONS` is written lower case, but
    the token it replaces may sit at the start of a cue.  Casing is settled for
    good in S6 by ``apply_casing``; this only avoids handing that stage an
    obviously wrong-looking intermediate value.
    """
    if not model or not pinyin:
        return pinyin
    lead = model[0]
    if lead.isupper():
        return pinyin[0].upper() + pinyin[1:]
    return pinyin


# --------------------------------------------------------------------------- #
# tokens
# --------------------------------------------------------------------------- #

def _is_blocked(stem: str) -> bool:
    return bool(stem) and (stem in ERHUA_BLOCK or stem[-1] in ERHUA_BLOCK)


def _is_head(stem: str) -> bool:
    return bool(stem) and (stem in ERHUA_HEAD or stem[-1] in ERHUA_HEAD)


def _can_take_suffix(tok: Token) -> bool:
    return (
        tok.kind == KIND_WORD
        and bool(tok.zh)
        and tok.zh != ER
        and not _is_blocked(tok.zh)
    )


def _merge_standalone(tokens: list[Token]) -> None:
    """Fold a lone ``儿`` token back into the word in front of it.

    Merging is the default rather than the exception: a standalone ``儿`` after
    a word is what over-eager segmentation produces, and leaving it standing is
    a hard error downstream.  :data:`ERHUA_BLOCK` is the escape hatch for the
    handful of words where ``儿`` genuinely stands alone.
    """
    idx = 1
    while idx < len(tokens):
        tok = tokens[idx]
        if tok.kind == KIND_WORD and tok.zh == ER and _can_take_suffix(tokens[idx - 1]):
            prev = tokens[idx - 1]
            prev.zh += ER
            if prev.pinyin:
                prev.pinyin = erhua_pinyin(prev.pinyin)
            del tokens[idx]
            continue
        idx += 1


def _contract_suffixed(tokens: list[Token]) -> None:
    """Rewrite ``nǎér`` style pinyin on words jieba already kept whole.

    Positive evidence is required here, unlike in :func:`_merge_standalone`:
    the token arrived intact, so pypinyin's ``ér`` may well be correct, and
    only a known erhua head justifies overriding it.
    """
    for tok in tokens:
        if tok.kind != KIND_WORD:
            continue
        zh = tok.zh
        if len(zh) < 2 or not zh.endswith(ER):
            continue
        stem = zh[:-1]
        if _is_blocked(stem):
            continue
        fixed = ERHUA_EXCEPTIONS.get(zh)
        if fixed is not None:
            tok.pinyin = _match_lead_case(fixed, tok.pinyin)
            continue
        if tok.pinyin and _is_head(stem):
            tok.pinyin = erhua_pinyin(tok.pinyin)


def merge_erhua(tokens: list[Token]) -> None:
    """Normalise every 儿化音 in ``tokens``, in place.

    Two passes, in this order and not the other: merging first turns a split
    ``点 | 儿`` into one ``点儿`` token, then the contraction pass gives every
    ``…儿`` word — merged just now or already whole from jieba — the same
    spelling.  One code path decides the pinyin, so a merged word and a
    pre-merged word can never disagree.
    """
    _merge_standalone(tokens)
    _contract_suffixed(tokens)
