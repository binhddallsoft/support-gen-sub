"""Reading and writing the 4-line SRT dialect defined in ``docs/format-contract.md``.

Two jobs live here, and they pull in opposite directions:

*   ``parse_srt`` / ``parse_srt_document`` must survive whatever a human editor
    sends us - 2, 3, 4 or 6 line blocks, doubled blank lines, a missing blank
    line at EOF.  Being strict here would only mean refusing to help exactly
    the files that need helping, so the parser is forgiving and *records* every
    deviation as a finding for ``rules.py`` instead of raising.
*   ``emit_srt`` must be ruthless: it is the only place a ``.srt`` body is
    produced, so it renumbers from 1 and delegates every spacing decision to
    the renderer in :mod:`srtgen.core.token`.  No string patching happens here.

``tokenize_line`` is the reverse direction - turning a line somebody already
rendered back into tokens.  It is the delicate one, because a bare ``-`` is
either a speaker marker (structural, keeps its ASCII form, one space each
side) or an interrupted-speech dash (punctuation, becomes ``——``), and the
only way to tell them apart is the surrounding whitespace and punctuation.

SRT formatting tags (``<i>``, ``</i>``, ``<b>``, ``<font color="…">``) are
read as one opaque token of kind :data:`KIND_MARKUP` and written back verbatim.
Translators' files carry ``<i>`` constantly, and treating its ``<`` / ``>`` as
the ASCII brackets of README section 3 turned ``<i>他 说 好。</i>`` into
``《i》他 说 好。《/ i》`` - a broken file the validator could not even see,
because the validator (rightly) ignores markup.  The tag pattern is imported
from :mod:`srtgen.core.rules` so that "what is a tag" has exactly one answer.

Findings raised while parsing are handed to the validator through
``Document.meta["parse_findings"]``: a list of dicts whose keys are exactly the
fields of ``rules.Finding`` (``code``, ``severity``, ``cue_index``, ``message``,
``line``), so ``rules.validate_document`` can do ``Finding(**d)``.  The parser
is the only layer that still sees the raw block shape, so it is the only layer
able to report ``BLOCK_SHAPE`` / ``TIMESTAMP_FORMAT`` / ``CUM_MISMATCH``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import Any, Final, Iterator, Sequence

from srtgen.core.rules import _MARKUP_RE as SRT_MARKUP_RE
from srtgen.core.token import (
    KIND_MARKER,
    KIND_MARKUP,
    KIND_PUNCT,
    KIND_WORD,
    Cue,
    Document,
    Token,
    render_py,
    render_zh,
)

__all__ = [
    "TIMESTAMP_RE",
    "SRT_MARKUP_RE",
    "KIND_MARKUP",
    "ELLIPSIS",
    "DASH",
    "MARKER",
    "SENTENCE_ENDERS",
    "CJK_PUNCT",
    "ASCII_PUNCT_MAP",
    "PINYIN_SYLLABLES",
    "SrtParseError",
    "RawBlock",
    "BlockContent",
    "SetAsideLine",
    "looks_like_pinyin",
    "split_block_content",
    "set_aside_finding",
    "parse_timestamp_line",
    "format_timestamp",
    "parse_srt",
    "parse_srt_document",
    "emit_srt",
    "tokenize_line",
    "merge_zh_py",
]


# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

#: The one true timestamp shape.  ``rules.py`` imports this to raise
#: ``TIMESTAMP_FORMAT``; parsing itself uses the looser pattern below so that a
#: sloppy file can still be read and fixed rather than rejected.
TIMESTAMP_RE: Final[re.Pattern[str]] = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})$"
)

_TIMESTAMP_LOOSE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(\d{1,3}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
    r"\s*-->\s*"
    r"(\d{1,3}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*(?:[XY]\d.*)?$"
)

ELLIPSIS: Final[str] = "……"
DASH: Final[str] = "——"
MARKER: Final[str] = "-"

#: A ``-`` sitting immediately after one of these is a new speaker's turn, even
#: when the editor forgot the space in front of it (11 such cues in the corpus).
SENTENCE_ENDERS: Final[str] = "。？！"

#: Marks that can precede the first speaker marker of a line without ending it.
_OPENING_PUNCT: Final[str] = "《（“‘ 	"

#: Every character that is punctuation on sight, per README section 3.
CJK_PUNCT: Final[str] = "。，、？！：；《》（）“”‘’…—"

#: ASCII punctuation and its Chinese counterpart.  Quotes are absent on purpose:
#: ``"`` and ``'`` need open/close state, and ``'`` may be a pinyin apostrophe.
ASCII_PUNCT_MAP: Final[dict[str, str]] = {
    ",": "，",
    ".": "。",
    "?": "？",
    "!": "！",
    ":": "：",
    ";": "；",
    "(": "（",
    ")": "）",
    "<": "《",
    ">": "》",
}

# ``KIND_MARKUP`` (an SRT tag such as ``<i>`` / ``</font>``) is defined in
# :mod:`srtgen.core.token` and re-exported here, because this is where it has
# always been imported from.  It used to be defined *here*, outside
# ``token.KINDS``, and that is exactly why a tag did not survive a bundle or a
# stage-file round trip: the loaders coerced the unknown kind back to ``word``.
# One definition, in the module every stage imports, is what keeps the tag a
# tag everywhere - no cluster, no pinyin, verbatim on both lines, never touched
# by S6's punctuation table (which only rewrites ``punct`` tokens).

_SOURCE_MANUAL: Final[str] = "manual"
_SOURCE_PUNCT: Final[str] = "punct"

_CJK_ELLIPSIS_RUN: Final[re.Pattern[str]] = re.compile(r"…+")
_CJK_ELLIPSIS_LOOSE: Final[re.Pattern[str]] = re.compile(r"…(?:[ \t]*…)*")
_ASCII_ELLIPSIS_LOOSE: Final[re.Pattern[str]] = re.compile(r"\.(?:[ \t]*\.)+")
_DOT_RUN: Final[re.Pattern[str]] = re.compile(r"\.+")
_EM_DASH_RUN: Final[re.Pattern[str]] = re.compile(r"—+")
_EM_DASH_LOOSE: Final[re.Pattern[str]] = re.compile(r"—(?:[ \t]*—)*")
_HYPHEN_RUN: Final[re.Pattern[str]] = re.compile(r"-+")

_HAN_RANGES: Final[tuple[tuple[str, str], ...]] = (
    ("㐀", "䶿"),
    ("一", "鿿"),
    ("豈", "﫿"),
)


#: Every toneless Hanyu Pinyin syllable (``ü`` spelled ``ü``), 425 of them.
#:
#: Generated once from the readings in ``pypinyin``'s own dictionary and frozen
#: here as a literal, for two reasons: ``srt.py`` must import on a bare machine
#: (``srtgen doctor``) where pypinyin may be missing, and the table is the
#: standard ~410 syllables plus the interjections the dictionary really uses
#: (``hm``, ``hng``, ``ng``, ``m``, ``n``, ``biang``...).  A test checks the
#: literal still covers every syllable pypinyin can produce.
PINYIN_SYLLABLES: Final[frozenset[str]] = frozenset(
    """
    a ai an ang ao ba bai ban bang bao bei ben beng bi bian biang biao bie bin bing bo
    bong bu ca cai can cang cao ce cei cen ceng cha chai chan chang chao che chen cheng
    chi chong chou chu chua chuai chuan chuang chui chun chuo ci cong cou cu cuan cui
    cun cuo da dai dan dang dao de dei den deng di dia dian diao die din ding diu dong
    dou du duan dui dun duo e ei en eng er fa fan fang fei fen feng fiao fo fou fu ga
    gai gan gang gao ge gei gen geng gong gou gu gua guai guan guang gui gun guo ha hai
    han hang hao he hei hen heng hm hng hong hou hu hua huai huan huang hui hun huo ji
    jia jian jiang jiao jie jin jing jiong jiu ju juan jue jun ka kai kan kang kao ke
    kei ken keng kong kou ku kua kuai kuan kuang kui kun kuo la lai lan lang lao le lei
    len leng li lia lian liang liao lie lin ling liu lo long lou lu luan lun luo lü lüe
    m ma mai man mang mao me mei men meng mi mian miao mie min ming miu mo mou mu n na
    nai nan nang nao ne nei nen neng ng ni nia nian niang niao nie nin ning niu nong nou
    nu nuan nun nuo nü nüe o ou pa pai pan pang pao pei pen peng pi pian piao pie pin
    ping po pou pu qi qia qian qiang qiao qie qin qing qiong qiu qu quan que qun ran
    rang rao re ren reng ri rong rou ru rua ruan rui run ruo sa sai san sang sao se sen
    seng sha shai shan shang shao she shei shen sheng shi shou shu shua shuai shuan
    shuang shui shun shuo si song sou su suan sui sun suo ta tai tan tang tao te tei
    teng ti tian tiao tie ting tong tou tu tuan tui tun tuo wa wai wan wang wei wen weng
    wo wong wu xi xia xian xiang xiao xie xin xing xiong xiu xu xuan xue xun ya yan yang
    yao ye yi yin ying yo yong you yu yuan yue yun za zai zan zang zao ze zei zen zeng
    zha zhai zhan zhang zhao zhe zhei zhen zheng zhi zhong zhou zhu zhua zhuai zhuan
    zhuang zhui zhun zhuo zi zong zou zu zuan zui zun zuo
    """.split()
)

#: Longest syllable (``zhuang``) plus one letter for the 儿化 ``r`` suffix.
_MAX_SYLLABLE: Final[int] = 7

#: The four tone marks of Hanyu Pinyin, and only those.  Vietnamese shares the
#: acute and grave (``á`` ``à``) but writes its other tones with hook, tilde and
#: dot below (``ả`` ``ã`` ``ạ``) and has letters pinyin never uses (``ă`` ``â``
#: ``ê`` ``ô`` ``ơ`` ``ư`` ``đ``): any of those makes a line "not pinyin" on sight.
_TONED_VOWELS: Final[dict[str, str]] = {
    **dict.fromkeys("āáǎà", "a"),
    **dict.fromkeys("ēéěè", "e"),
    **dict.fromkeys("īíǐì", "i"),
    **dict.fromkeys("ōóǒò", "o"),
    **dict.fromkeys("ūúǔù", "u"),
    **dict.fromkeys("ǖǘǚǜ", "ü"),
}

#: Tone marks on the syllabic nasals of 嗯 / 呣 (``ǹg``, ``ń``, ``ḿ``).  Not in
#: the four-vowel list above, but the reviewed corpus writes them (``ǹ`` 10x in
#: completed.srt, ``ń`` 10x in filter.srt) and the reading table makes the tool
#: itself write ``ǹg`` - so refusing them would move our own output's pinyin
#: line into the Vietnamese file.  Vietnamese never puts a mark on ``m``/``n``.
_TONED_NASALS: Final[dict[str, str]] = {"ń": "n", "ň": "n", "ǹ": "n", "ḿ": "m"}
_NASAL_SYLLABLES: Final[frozenset[str]] = frozenset({"m", "n", "ng", "hm", "hng"})

#: Combining tone marks that NFC cannot fold into a nasal (``m̀``, ``n̄``).
_COMBINING_TONES: Final[frozenset[str]] = frozenset("\u0300\u0301\u0304\u030c")

#: Marks Vietnamese never writes (macron, caron, ü, a toned nasal).  A line that
#: carries one is pinyin beyond doubt, so the pronunciation cross-check below
#: is skipped for it - which keeps pypinyin out of the hot path for ~all lines.
_PINYIN_ONLY_MARKS: Final[frozenset[str]] = frozenset("āǎēěīǐōǒūǔǖǘǚǜüńňǹḿ")

_APOSTROPHES: Final[str] = "'’"
_LATIN_RUN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class _Letters:
    """One pinyin-candidate chunk, reduced to bare letters plus where tones sit."""

    bare: str
    toned: tuple[bool, ...]
    nasal: tuple[bool, ...]


def _letters_of(chunk: str) -> _Letters | None:
    """Bare letters of ``chunk`` or ``None`` when it holds a non-pinyin letter.

    ``v`` is read as ``ü`` because that is how people type ``nǚ`` on a plain
    keyboard (``nv``); no pinyin syllable starts with ``ü``, so this cannot turn
    a Vietnamese ``v`` word into pinyin.
    """
    bare: list[str] = []
    toned: list[bool] = []
    nasal: list[bool] = []
    for ch in unicodedata.normalize("NFC", chunk).lower():
        if ch in _COMBINING_TONES:
            if not bare or bare[-1] not in "mn" or toned[-1]:
                return None
            toned[-1] = True
            nasal[-1] = True
            continue
        if ch in _TONED_VOWELS:
            bare.append(_TONED_VOWELS[ch])
            toned.append(True)
            nasal.append(False)
        elif ch in _TONED_NASALS:
            bare.append(_TONED_NASALS[ch])
            toned.append(True)
            nasal.append(True)
        elif ch == "v":
            bare.append("ü")
            toned.append(False)
            nasal.append(False)
        elif "a" <= ch <= "z" or ch == "ü":
            bare.append(ch)
            toned.append(False)
            nasal.append(False)
        else:
            return None
    if not bare:
        return None
    return _Letters("".join(bare), tuple(toned), tuple(nasal))


def _syllable_ok(letters: _Letters, i: int, j: int) -> str | None:
    """The syllable ``letters[i:j]`` stands for, or ``None`` if it is not one.

    A toned nasal only on ``m``/``n``/``ng``/``hm``/``hng``; a trailing ``r`` is
    the 儿化 suffix (``nǎr`` = ``na`` + r).  Returns the syllable *without* the
    ``r`` so it can be compared with a character's reading.

    The number of tone marks per syllable is deliberately NOT limited to one.
    The question here is "pinyin or Vietnamese?", not "is this pinyin spelled
    right?" (that is ``PAIR_MISMATCH``'s job), and editors do mistype:
    ``corpus/pairs/tintuc_ai.srt`` writes 白领 as ``báǐlǐng``.  Rejecting that
    moved a real pinyin line into the Vietnamese file.  Vietnamese never stacks
    two tone marks either, so nothing Vietnamese gets in through this door.
    """
    seg = letters.bare[i:j]
    if any(letters.nasal[i:j]):
        return seg if seg in _NASAL_SYLLABLES else None
    if seg in PINYIN_SYLLABLES:
        return seg
    if len(seg) > 1 and seg.endswith("r") and seg[:-1] in PINYIN_SYLLABLES:
        return seg[:-1]
    return None


def _segment(letters: _Letters, readings: frozenset[str] | None = None) -> tuple[int, int] | None:
    """Split ``letters`` into pinyin syllables: ``(matched, total)`` or ``None``.

    Dynamic programming over every split point, because pinyin is written with
    no separator inside a word and the greedy longest match is wrong for words
    like ``xian`` / ``xi'an`` or ``fangan``.  ``matched`` counts syllables that are
    a reading of one of the Han characters in ``readings``; among all valid
    splits the one agreeing most with the Chinese line wins (ties: fewer
    syllables), so the cross-check in :func:`looks_like_pinyin` never rejects a
    line merely because the first split found was the unlucky one.
    """
    n = len(letters.bare)
    best: list[tuple[int, int] | None] = [None] * (n + 1)
    best[0] = (0, 0)
    for i in range(n):
        here = best[i]
        if here is None:
            continue
        for j in range(i + 1, min(n, i + _MAX_SYLLABLE) + 1):
            syllable = _syllable_ok(letters, i, j)
            if syllable is None:
                continue
            gain = 1 if (readings is not None and syllable in readings) else 0
            cand = (here[0] + gain, here[1] + 1)
            old = best[j]
            if old is None or cand[0] > old[0] or (cand[0] == old[0] and cand[1] < old[1]):
                best[j] = cand
    return best[n]


def _words_of(text: str) -> list[str]:
    """Latin/digit words of a line; an apostrophe between letters stays inside."""
    words: list[str] = []
    buf: list[str] = []
    for i, ch in enumerate(text):
        if ch.isalnum() or unicodedata.category(ch) == "Mn":
            buf.append(ch)
            continue
        if (
            ch in _APOSTROPHES
            and buf
            and i + 1 < len(text)
            and text[i + 1].isalnum()
        ):
            buf.append("'")
            continue
        if buf:
            words.append("".join(buf))
            buf.clear()
    if buf:
        words.append("".join(buf))
    return words


@lru_cache(maxsize=8192)
def _char_readings(ch: str) -> frozenset[str]:
    from pypinyin import Style, pinyin

    groups = pinyin(ch, style=Style.NORMAL, heteronym=True, v_to_u=True)
    return frozenset(r.lower() for group in groups for r in group if r)


def _han_readings(han_line: str) -> frozenset[str] | None:
    """Every toneless reading of every Han character on ``han_line``.

    ``None`` when pypinyin is not installed: the cross-check is a safety net on
    top of the syllable test, and a bare machine must still be able to split a
    bilingual file - just with the syllable test alone.
    """
    try:
        found: set[str] = set()
        for ch in han_line:
            if _has_han(ch):
                found |= _char_readings(ch)
    except ImportError:
        return None
    return frozenset(found)


def looks_like_pinyin(
    line: str,
    han_line: str | None = None,
    *,
    evidence_required: bool = False,
) -> bool:
    """Is ``line`` a pinyin line (as opposed to a Vietnamese translation)?

    Decided by **spelling**, not by looking for letters "only Vietnamese has":
    punctuation is dropped, and every word must split completely into valid
    pinyin syllables (:data:`PINYIN_SYLLABLES`) with at most one of the four
    pinyin tone marks per syllable.  So ``anh``, ``bạn``, ``khỏe``, ``không``
    fail, and ``Anh là ai?`` - which the old "Vietnamese-only letters" test let
    through - fails on ``anh``.

    ``han_line`` (the Chinese line of the same block) adds two things:

    * A Latin word that appears verbatim on the Chinese line (``OK``, ``KTV``,
      ``Thanks``, ``PK``) is exempt: it is copied, not transcribed.  Digits are
      always exempt for the same reason.
    * **Pronunciation cross-check.**  A line such as ``Là ai?`` spells valid
      pinyin syllables (``la``, ``ai``) with only acute/grave marks, which
      Vietnamese uses too.  Unless the line carries a mark Vietnamese never
      writes (macron, caron, ``ü``), at least half of its syllables must be a
      reading of some Han character on the Chinese line.  ``Shì shéi？`` under
      ``是 谁？`` agrees 2/2; ``Là ai?`` agrees 0/2 and is Vietnamese.

    A line with no word left to check (only punctuation, numbers or copied
    Latin) counts as pinyin: it carries no language that could be lost.
    A line containing Han characters is never pinyin.

    ``evidence_required`` lat nguoc ket luan cuoi cung do: khong con chu nao de
    kiem thi tra ``False``. Chi dung cho DONG CUOI cua mot block ba dong tro len,
    va chi khi dong do khong phai toan dau cau. Vi sao: mot dong tieng Viet chi
    co ``♪`` (nhac), hay chi co mot con so nhu ``1994``, cung khong con chu nao
    de kiem — truoc day no bi xep la pinyin, bi dinh vao cuoi dong pinyin, va
    bien mat khoi file ``_vi.srt``. Mat han mot cau, khong mot loi bao.
    """
    text = SRT_MARKUP_RE.sub(" ", unicodedata.normalize("NFC", str(line or "")))
    if _has_han(text):
        return False
    han_text = SRT_MARKUP_RE.sub(" ", str(han_line or ""))
    copied = {w.casefold() for w in _LATIN_RUN_RE.findall(han_text)}

    checked: list[_Letters] = []
    for word in _words_of(text):
        if word.isdigit() or word.casefold() in copied:
            continue
        for chunk in word.split("'"):
            letters = _letters_of(chunk)
            if letters is None or _segment(letters) is None:
                return False
            checked.append(letters)

    if not checked:
        return not evidence_required
    if not han_text or any(ch in _PINYIN_ONLY_MARKS for ch in text.lower()):
        return True
    readings = _han_readings(han_text)
    if not readings:
        return True
    matched = total = 0
    for letters in checked:
        score = _segment(letters, readings)
        if score is None:  # pragma: no cover - already segmented above
            return False
        matched += score[0]
        total += score[1]
    return matched * 2 >= total


class SrtParseError(ValueError):
    """Raised only when a line that must be a timestamp is not one.

    Everything else the parser can survive is downgraded to a finding, because
    the person running ``srtgen check`` wants a list of problems, not a
    traceback.
    """


# --------------------------------------------------------------------------- #
# raw block
# --------------------------------------------------------------------------- #

@dataclass
class RawBlock:
    """One SRT block, still as text, before any tokenisation.

    ``build-spec`` describes this as the 4-tuple ``(index, start, end, lines)``
    and those four fields come first for exactly that reason - ``__iter__``
    yields only them, so ``index, start, end, lines = block`` keeps working.
    The trailing fields exist because the block shape is visible *here* and
    nowhere later: once a block has become a ``Cue`` there is no way to tell it
    used to have six lines or a two-digit hour.  ``vi_line`` is the Vietnamese
    translation line a bilingual block carried (see :func:`split_block_content`)
    and ``set_aside`` the lines the parser could not place - both kept verbatim
    so no caller can lose them without deciding to.
    """

    index: int
    start: float
    end: float
    lines: list[str]
    raw_line_count: int = 4
    index_text: str = ""
    index_ok: bool = True
    timestamp_text: str = ""
    line_no: int = 0
    vi_line: str | None = None
    set_aside: list["SetAsideLine"] = field(default_factory=list)

    def __iter__(self) -> Iterator[Any]:
        """Yield only the four contract fields, so tuple unpacking stays valid."""
        yield from (self.index, self.start, self.end, self.lines)


# --------------------------------------------------------------------------- #
# timestamps
# --------------------------------------------------------------------------- #

def parse_timestamp_line(line: str) -> tuple[float, float]:
    """Return ``(start, end)`` in seconds from a ``-->`` line.

    The loose pattern is tried after the strict one so that a file with
    ``0:01:02.5`` still opens; a fractional part shorter than three digits is
    padded on the right because ``,58`` means 580 ms, not 58 ms.
    """
    match = TIMESTAMP_RE.match(line.strip()) or _TIMESTAMP_LOOSE_RE.match(line)
    if match is None:
        raise SrtParseError(
            "Dòng thời gian không đúng định dạng "
            f"HH:MM:SS,mmm --> HH:MM:SS,mmm: {line.strip()!r}"
        )
    h1, m1, s1, ms1, h2, m2, s2, ms2 = match.groups()
    return (
        _to_seconds(h1, m1, s1, ms1),
        _to_seconds(h2, m2, s2, ms2),
    )


def _to_seconds(hours: str, minutes: str, seconds: str, millis: str) -> float:
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")) / 1000.0
    )


def format_timestamp(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS,mmm``.

    Rounding (not truncating) to the millisecond keeps a cue that was parsed
    and re-emitted byte-identical: ``1.58`` stored as a float is really
    ``1.5799999…`` and truncation would silently shift every timestamp down by
    one millisecond.  Negative input is clamped to zero because Aegisub refuses
    a negative timestamp outright.
    """
    value = float(seconds)
    if value != value or value < 0.0:  # NaN or negative
        value = 0.0
    total_ms = int(round(value * 1000))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

def _has_han(text: str) -> bool:
    return any(lo <= ch <= hi for ch in text for lo, hi in _HAN_RANGES)


def _split_lines(text: str) -> list[str]:
    """Normalise line endings and drop a stray BOM before splitting.

    ``io_utils.read_text`` already does both; repeating it costs nothing and
    means ``parse_srt`` is also safe on a string that came from an HTTP upload
    in the web UI, which never passes through ``io_utils``.
    """
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    if body.startswith("﻿"):
        body = body[1:]
    return body.split("\n")


@dataclass(frozen=True)
class SetAsideLine:
    """A line the parser could not place in the Chinese, pinyin or Vietnamese slot.

    It is carried to the caller verbatim, with the reason in Vietnamese, because
    the one thing a tool for non-technical users must never do is drop text they
    typed without saying so.
    """

    cue: int | None
    text: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"cue": self.cue, "text": self.text, "reason": self.reason}


def set_aside_finding(item: SetAsideLine | dict[str, Any]) -> dict[str, Any]:
    """The ``BLOCK_SHAPE`` finding (as ``rules.Finding`` kwargs) for a set-aside line.

    One wording for every caller - the parse findings of
    :func:`parse_srt_document` and the findings of ``pipeline.run_fix_bilingual``
    - and the line itself is quoted in full, so the finding *is* the backup.
    """
    data = item.to_dict() if isinstance(item, SetAsideLine) else dict(item)
    cue = data.get("cue")
    text = str(data.get("text") or "")
    reason = str(data.get("reason") or "")
    where = f"Câu {cue}: " if cue is not None else ""
    return _finding(
        "BLOCK_SHAPE",
        "error",
        cue,
        f"{where}{reason} Tool không đưa dòng này vào file đã sửa; nội dung được "
        f"giữ nguyên ở đây: “{text.strip()}”.",
        text,
    )


_WHY_HEADER: Final[str] = "dòng thừa đứng trước dòng thời gian."
_WHY_ORPHAN: Final[str] = "Dòng chữ nằm trước block phụ đề đầu tiên, không thuộc câu nào."
_WHY_STRAY_PINYIN: Final[str] = (
    "dòng này trông như pinyin nhưng lại đứng sau dòng tiếng Việt, nên tool không "
    "biết nó thuộc về đâu."
)


@dataclass
class BlockContent:
    """The content lines of one block, sorted into the slots the contract knows.

    ``lines`` is what the tokeniser reads: ``[]``, ``[Chinese]`` or
    ``[Chinese, pinyin]``.  ``vi_line`` is the Vietnamese translation, if the
    block carried one.  ``set_aside`` holds ``(text, reason)`` for lines that fit
    none of the three.
    """

    lines: list[str]
    vi_line: str | None = None
    set_aside: list[tuple[str, str]] = field(default_factory=list)


def _join(lines: Sequence[str]) -> str:
    return " ".join(ln.strip() for ln in lines if ln.strip())


#: Dấu câu kiểu ASCII, cộng vài ký hiệu hay gặp trong phụ đề. Ghép với
#: :data:`CJK_PUNCT` thành bộ "thế nào là một dòng chỉ có dấu câu".
_PUNCT_ONLY_EXTRA: Final[str] = ".,?!:;()<>\"'-—…·~/|[]{}*+=&%#@$^`\\"


def _only_punct(text: str) -> bool:
    """Dong nay chi gom dau cau va khoang trang?"""
    body = SRT_MARKUP_RE.sub(" ", str(text or "")).strip()
    if not body:
        return True
    return all(ch.isspace() or ch in CJK_PUNCT or ch in _PUNCT_ONLY_EXTRA for ch in body)


def split_block_content(content: Sequence[str]) -> BlockContent:
    """Sort a block's content lines into Chinese / pinyin / Vietnamese.

    Why this exists: translators send three-line blocks (Chinese / pinyin /
    Vietnamese) and two-line blocks (Chinese / Vietnamese).  The old folder put
    every non-Han line into the pinyin slot, so the Vietnamese line was glued
    onto the pinyin, the cluster counts stopped matching, the editor's pinyin
    was regenerated - and the translation vanished while the result reported
    zero errors.  Here every non-Han line is classified by
    :func:`looks_like_pinyin` against the block's own Chinese text.

    Rules, in order:

    * 0-1 line, or no Han anywhere (a ``_vi.srt`` block): unchanged, except that
      three or more lines are joined into one, as before.
    * Every line Chinese (two, three or more): consecutive Chinese lines are
      ONE Chinese line, joined with a single space.  They are a subtitle that
      wrapped (very common), or the SRT two-speaker layout ``- A`` / ``- B``,
      which joined gives exactly the one-line ``- A - B`` form the README
      prescribes.  A second Han line is never taken as pinyin.  The old rule
      set the second of two Han lines aside - which took those words out of
      the output file - and three or more Han lines crashed the parser with
      ``StopIteration``; one rule for every count ends both.
      One exception, the shape ``emit_srt`` itself writes: a cue with no pinyin
      yet is emitted with the Chinese copied into the pinyin slot (the
      renderer's fallback), so a two-line block whose lines are the same words
      (spacing aside) is ONE line of dialogue, kept once.  Joining it would
      double the dialogue on every re-read - ``fix`` run twice would give two
      different files - and dropping the copy loses nothing: it is the kept
      line, character for character.
    * Chinese lines first, then Latin lines (the normal layout): the leading
      run of pinyin lines is the pinyin (wrapped pinyin is re-joined); every
      other line is Vietnamese; a pinyin-looking line *after* a Vietnamese line
      belongs nowhere provable and is set aside.
    * Chinese and Latin interleaved (two speakers written Han/pinyin/Han/pinyin),
      or Latin first: Chinese lines -> Chinese slot, pinyin lines -> pinyin slot,
      the rest -> Vietnamese.

    A two-line Chinese/pinyin block comes back *unchanged, line for line*, so a
    file that already obeys the contract round-trips byte for byte.
    """
    lines = list(content)
    n = len(lines)
    if n <= 1:
        return BlockContent(lines)
    is_han = [_has_han(ln) for ln in lines]
    if not any(is_han):
        return BlockContent(lines if n == 2 else [_join(lines)])
    if all(is_han):
        if n == 2 and "".join(lines[0].split()) == "".join(lines[1].split()):
            return BlockContent([lines[0]])
        return BlockContent([_join(lines)])

    han_text = _join(ln for ln, han in zip(lines, is_han) if han)

    def kind_of(pos: int, ln: str, han: bool) -> str:
        if han:
            return "han"
        # Dong CUOI cua block ba dong tro len la cho dong tieng Viet vao. Neu no
        # khong con chu nao de kiem (chi co "♪", hay mot con so), dung mac dinh
        # "khong co gi de mat -> coi la pinyin" se nuot mat ca cau. Doi bang
        # chung o dung cho do. Ngoai le: dong chi co dau cau ("……", "——") that
        # su la phan duoi cua mot dong pinyin bi ngat, nen giu luat cu.
        strict = pos == n - 1 and n >= 3 and not _only_punct(ln)
        return "pinyin" if looks_like_pinyin(ln, han_text, evidence_required=strict) else "vi"

    kinds = [kind_of(k, ln, han) for k, (ln, han) in enumerate(zip(lines, is_han))]
    if n == 2 and kinds == ["han", "pinyin"]:
        return BlockContent(lines)

    han_rows = [k for k, kind in enumerate(kinds) if kind == "han"]
    first_latin = next(k for k, kind in enumerate(kinds) if kind != "han")
    stray: list[int] = []
    if first_latin > 0 and all(kind != "han" for kind in kinds[first_latin:]):
        rest = list(range(first_latin, n))
        lead = 0
        while lead < len(rest) and kinds[rest[lead]] == "pinyin":
            lead += 1
        py_rows = rest[:lead]
        vi_rows = [k for k in rest[lead:] if kinds[k] == "vi"]
        stray = [k for k in rest[lead:] if kinds[k] == "pinyin"]
    else:
        py_rows = [k for k, kind in enumerate(kinds) if kind == "pinyin"]
        vi_rows = [k for k, kind in enumerate(kinds) if kind == "vi"]

    kept = [_join(lines[k] for k in han_rows)]
    if py_rows:
        kept.append(_join(lines[k] for k in py_rows))
    vi = _join(lines[k] for k in vi_rows) or None
    return BlockContent(kept, vi, [(lines[k], _WHY_STRAY_PINYIN) for k in stray])


def parse_srt(text: str) -> list[RawBlock]:
    """Split an SRT body into blocks, tolerating every shape seen in the wild.

    Blocks are found by scanning for the timestamp line rather than by slicing
    on ``"\\n\\n"``: that way a doubled blank line between blocks costs nothing,
    a missing blank line at EOF costs nothing, and a blank line that fell
    *inside* a block reattaches to that block instead of inventing a new one.

    Each block's content goes through :func:`split_block_content`, so a
    bilingual block arrives with its Vietnamese line in ``vi_line`` rather than
    glued onto the pinyin.  Lines before the first block are only reported by
    :func:`parse_srt_document` (they belong to no block).
    """
    return _parse_blocks(text)[0]


def _head_start(group: list[str], ts_all: list[int], order: int) -> int:
    """Dòng đầu tiên thuộc về block thứ ``order`` trong nhóm (số thứ tự, nếu có).

    Dùng để biết nội dung của block ĐỨNG TRƯỚC kết thúc ở đâu, khi hai block dính
    vào nhau vì thiếu dòng trống.
    """
    ts_pos = ts_all[order]
    prev = ts_pos - 1
    if prev > ts_all[order - 1] and group[prev].strip().isdigit():
        return prev
    return ts_pos


def _parse_blocks(text: str) -> tuple[list[RawBlock], list[str]]:
    """:func:`parse_srt` plus the orphan lines that stand before the first block."""
    lines = _split_lines(text)
    groups: list[dict[str, Any]] = []
    orphans: list[str] = []

    i, total = 0, len(lines)
    while i < total:
        if not lines[i].strip():
            i += 1
            continue
        first_line_no = i + 1
        group: list[str] = []
        while i < total and lines[i].strip():
            group.append(lines[i])
            i += 1

        # MỌI dòng thời gian trong nhóm, không phải dòng đầu tiên.  Một nhóm ôm
        # hai dòng thời gian nghĩa là file thiếu dòng trống giữa hai block - lỗi
        # thường gặp ở file người ta sửa tay hay ghép từ hai nguồn.  Lấy đúng một
        # mốc rồi coi phần còn lại là nội dung sẽ nuốt mất hẳn một câu phụ đề,
        # và nhét cả số thứ tự lẫn dòng giờ vào giữa chữ của câu đứng trước.
        ts_all = [k for k, ln in enumerate(group) if _TIMESTAMP_LOOSE_RE.match(ln)]
        if not ts_all:
            # No timestamp: this is the tail of the previous block that a stray
            # blank line tore off.  Text before the first block belongs to no
            # block; it is returned so the caller can show it, never dropped.
            if groups:
                groups[-1]["content"].extend(group)
            else:
                orphans.extend(group)
            continue

        for order, ts_pos in enumerate(ts_all):
            # Phần đầu của block: với block đầu nhóm là mọi thứ đứng trước mốc
            # giờ; với các block sau, chỉ là dòng số thứ tự ngay sát trên nó (nếu
            # đúng là chữ số).  Hẹp như vậy để một dòng phụ đề tình cờ là chữ số
            # không bị cướp khỏi câu đứng trước.
            if order == 0:
                head_from = 0
            else:
                prev = ts_pos - 1
                head_from = prev if prev > ts_all[order - 1] and group[prev].strip().isdigit() else ts_pos
            body_to = len(group) if order + 1 >= len(ts_all) else max(ts_pos + 1, _head_start(group, ts_all, order + 1))
            groups.append(
                {
                    "line_no": first_line_no + (ts_pos if order else 0),
                    "header": group[head_from:ts_pos],
                    "timestamp": group[ts_pos],
                    "content": group[ts_pos + 1 : body_to],
                }
            )

    blocks: list[RawBlock] = []
    for position, group_data in enumerate(groups, 1):
        header: list[str] = group_data["header"]
        content: list[str] = group_data["content"]
        index_text = header[-1].strip() if header else ""
        index_ok = index_text.isdigit()
        index = int(index_text) if index_ok else position
        start, end = parse_timestamp_line(group_data["timestamp"])
        sorted_content = split_block_content(content)
        junk = header[:-1] if index_ok else header
        set_aside = [SetAsideLine(index, ln, _WHY_HEADER) for ln in junk]
        set_aside += [SetAsideLine(index, ln, why) for ln, why in sorted_content.set_aside]
        blocks.append(
            RawBlock(
                index=index,
                start=start,
                end=end,
                lines=sorted_content.lines,
                raw_line_count=len(header) + 1 + len(content),
                index_text=index_text,
                index_ok=index_ok,
                timestamp_text=group_data["timestamp"],
                line_no=group_data["line_no"],
                vi_line=sorted_content.vi_line,
                set_aside=set_aside,
            )
        )
    return blocks, orphans


def _finding(
    code: str,
    severity: str,
    cue_index: int | None,
    message: str,
    line: str = "",
) -> dict[str, Any]:
    """Shape a parse-time problem like a ``rules.Finding`` constructor call."""
    return {
        "code": code,
        "severity": severity,
        "cue_index": cue_index,
        "message": message,
        "line": line,
    }


def parse_srt_document(text: str, *, normalize: bool = False) -> Document:
    """Build a :class:`Document` from an SRT body.

    ``normalize`` is passed straight through to :func:`tokenize_line`; leave it
    off (the default) whenever the result must round-trip byte-for-byte, and
    turn it on for ``srtgen fix``, where ASCII punctuation is meant to change.

    Cue numbers keep the value written in the file - not the position - so that
    ``rules.py`` can still see a broken sequence.  ``emit_srt`` renumbers later.

    Two more entries in ``meta``, so that nothing the file carried is lost on
    the way to a ``Document`` (which has room for Chinese and pinyin only):

    * ``meta["vi_lines"]`` - one entry per cue, in cue order: the Vietnamese
      line of that block, or ``None``.  Positional on purpose, like
      ``s9_emit.build_vi_srt`` expects, so the two files stay aligned by index.
    * ``meta["set_aside"]`` - ``SetAsideLine.to_dict()`` for every line that fit
      no slot (text before the first block, junk before a cue number, a pinyin
      line after the Vietnamese one...), each also raised as a ``BLOCK_SHAPE``
      parse finding quoting the line in full.
    """
    blocks, orphans = _parse_blocks(text)
    cues: list[Cue] = []
    findings: list[dict[str, Any]] = []
    vi_lines: list[str | None] = []
    set_aside: list[SetAsideLine] = [SetAsideLine(None, ln, _WHY_ORPHAN) for ln in orphans]

    for block in blocks:
        if block.raw_line_count != 4:
            findings.append(
                _finding(
                    "BLOCK_SHAPE",
                    "error",
                    block.index,
                    f"Block ở dòng {block.line_no} có {block.raw_line_count} dòng "
                    "thay vì 4 (số thứ tự, thời gian, dòng Hán, dòng pinyin). "
                    "Các dòng đã được xếp lại: chữ Hán vào dòng Hán, pinyin vào dòng "
                    "pinyin, tiếng Việt tách riêng.",
                )
            )
        if not block.index_ok:
            findings.append(
                _finding(
                    "INDEX_SEQ",
                    "error",
                    block.index,
                    f"Block ở dòng {block.line_no} thiếu số thứ tự hoặc số thứ tự "
                    f"không phải chữ số: {block.index_text!r}.",
                )
            )
        if TIMESTAMP_RE.match(block.timestamp_text.strip()) is None:
            findings.append(
                _finding(
                    "TIMESTAMP_FORMAT",
                    "error",
                    block.index,
                    "Dòng thời gian phải đúng dạng HH:MM:SS,mmm --> HH:MM:SS,mmm.",
                    block.timestamp_text,
                )
            )

        content = block.lines
        if not content:
            tokens: list[Token] = []
        elif len(content) == 1:
            # Three-line block (corpus/raw.srt): Chinese only, no pinyin yet.
            tokens = tokenize_line(content[0], normalize=normalize)
        else:
            zh_tokens = tokenize_line(content[0], normalize=normalize)
            py_tokens = tokenize_line(content[1], normalize=normalize)
            tokens, matched = merge_zh_py(zh_tokens, py_tokens)
            if not matched:
                zh_count = sum(1 for t in zh_tokens if t.kind == KIND_WORD)
                py_count = sum(1 for t in py_tokens if t.kind == KIND_WORD)
                findings.append(
                    _finding(
                        "CUM_MISMATCH",
                        "error",
                        block.index,
                        f"Dòng {block.index}: dòng Hán có {zh_count} cụm nhưng dòng "
                        f"pinyin có {py_count} cụm nên không ghép được. Pinyin của "
                        "cue này phải sinh lại, không được đoán.",
                        content[0],
                    )
                )

        cues.append(Cue(index=block.index, start=block.start, end=block.end, tokens=tokens))
        vi_lines.append(block.vi_line)
        set_aside.extend(block.set_aside)

    findings.extend(set_aside_finding(item) for item in set_aside)
    return Document(
        cues=cues,
        meta={
            "source_format": "srt",
            "parse_findings": findings,
            "vi_lines": vi_lines,
            "set_aside": [item.to_dict() for item in set_aside],
        },
    )


# --------------------------------------------------------------------------- #
# emitting
# --------------------------------------------------------------------------- #

def emit_srt(doc: Document) -> str:
    """Render a document as an SRT body, renumbered from 1.

    Numbering is positional and never taken from ``cue.index``: a file we hand
    back must satisfy ``INDEX_SEQ`` even when the file we read did not.

    Every block is *terminated* by a blank line rather than merely separated by
    one, so the body ends with ``\\n\\n``.  That matches ``corpus/completed.srt``
    byte for byte and is what players and Aegisub expect; ``io_utils.write_text``
    leaves an existing trailing newline alone.

    A cue holding no tokens is skipped, not written as two empty lines: a blank
    line inside a block ends the block as far as Aegisub is concerned, so
    writing one would corrupt every cue after it (build-spec section 11).
    ``EMPTY_CUE`` is only a warning, which means the file still gets written -
    and a file we write has to be readable.
    """
    parts: list[str] = []
    number = 0
    for cue in doc.cues:
        zh_line = render_zh(cue.tokens)
        py_line = render_py(cue.tokens)
        if not zh_line and not py_line:
            continue
        number += 1
        parts.append(
            "\n".join(
                (
                    str(number),
                    f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}",
                    zh_line,
                    py_line,
                )
            )
        )
        parts.append("\n\n")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# reverse tokenisation
# --------------------------------------------------------------------------- #

def _is_inner_hyphen(line: str, pos: int) -> bool:
    """Gach noi nam giua hai ky tu Latin/so, khong co khoang trang hai ben.

    "Wi-Fi", "Jean-Luc", "2019-2020", "COVID-19". Nhung chuoi nay la MOT tu:
    gach noi la mot phan chinh ta cua no, khong phai dau cau. Doi no thanh "——"
    lam hong chu ma nguoi dung khong co cach nao doan ra nguyen nhan.

    Co y chi nhan chu Latin va chu so ASCII. Gach giua hai chu Han ("好-坏") van la
    gach ngang cau theo README.
    """
    if pos <= 0 or pos + 1 >= len(line):
        return False

    def latin_or_digit(ch: str) -> bool:
        return ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9")

    return latin_or_digit(line[pos - 1]) and latin_or_digit(line[pos + 1])


def _is_speaker_marker(line: str, i: int) -> bool:
    """Decide whether the single ``-`` at ``line[i]`` starts a speaker's turn.

    Two signals (README section 3, "Marker đổi người nói"): it opens the line,
    or the last non-space character before it is punctuation.  A turn of speech
    can only begin at the start of a cue or after the previous speaker's line
    has been closed off, so those are the only two places a marker can stand.

    Whitespace alone is deliberately NOT a signal.  ``美国 - 纽约`` has a space
    on both sides yet joins two place names: it is an interrupted-speech dash
    that must become ``——``, which is what the audited corpus writes for that
    very cue.  Treating any spaced hyphen as a marker turned that dash into a
    speaker change.

    A third signal is needed for the README's own example
    ``- 我 没有 - 你 说 这……``, where the second hyphen follows the word ``没有``
    and still separates two turns.  What distinguishes it from ``美国 - 纽约``
    is that its line *already opened with a marker*, so the cue is known to
    carry two speakers.  Measured over ``corpus/completed.srt``: 29 of the 31
    lines containing a hyphen open with one; the 2 that do not are both cue
    268, whose pinyin line is a known corpus defect (its ``“ ”`` pair is
    missing, which is why its hyphen looks like it follows a word).

    SRT tags are invisible to this decision: ``<i>- 你 好</i>`` opens with a
    marker just like ``- 你 好`` does.  Without that, the ``>`` of the tag
    would stand in front of the hyphen and the turn marker would be rewritten
    into an interruption dash ``——``.
    """
    before = SRT_MARKUP_RE.sub("", line[:i]).rstrip()
    if not before:
        return True
    if before[-1] in CJK_PUNCT:
        return True
    return _opens_with_marker(line)


def _opens_with_marker(line: str) -> bool:
    """True when the line's first content character is a speaker marker.

    Opening punctuation is skipped: cue 1296 is written ``‘- 他们 …`` and the
    quote does not stop the hyphen after it from opening a turn.  SRT tags are
    skipped for the same reason (``<i>- 他们 …``).
    """
    head = SRT_MARKUP_RE.sub("", line).lstrip().lstrip(_OPENING_PUNCT)
    return head.startswith(MARKER)


def tokenize_line(line: str, *, normalize: bool = False) -> list[Token]:
    """Turn one already-rendered line back into tokens.

    Used by ``srtgen fix`` and ``srtgen check`` on files somebody else wrote,
    and by the round-trip invariant test.  With ``normalize=False`` the token
    values are verbatim, so ``render(tokenize(line)) == line`` for any line that
    already obeys the contract.  With ``normalize=True`` ASCII punctuation is
    converted, ``...``/``…`` become ``……`` and interrupted-speech dashes become
    ``——``; that is the only difference between the two modes.

    Multi-character marks are matched before single ones, otherwise ``……``
    would arrive as two ``…`` tokens and the renderer could never tell a real
    ellipsis from a doubled one.  Latin words inside dialogue (``OK``,
    ``Thanks``, ``PK``) are ordinary words and stay untouched.

    An SRT tag (:data:`SRT_MARKUP_RE`) is taken whole, before any punctuation
    rule can look at its characters, and kept verbatim in both modes: it is
    formatting, not dialogue, so ``normalize`` has nothing to convert in it.
    """
    tokens: list[Token] = []
    buffer: list[str] = []
    double_open = False
    single_open = False
    total = len(line)

    def flush() -> None:
        if buffer:
            tokens.append(
                Token(kind=KIND_WORD, zh="".join(buffer), pinyin=None, source=_SOURCE_MANUAL)
            )
            buffer.clear()

    def push_punct(value: str) -> None:
        flush()
        tokens.append(Token(kind=KIND_PUNCT, zh=value, pinyin=None, source=_SOURCE_PUNCT))

    i = 0
    while i < total:
        ch = line[i]

        # A space is a phrase boundary and nothing else; the renderer puts it
        # back where it belongs, so it is never stored in a token.
        if ch.isspace():
            flush()
            i += 1
            continue

        # --- SRT formatting tag ------------------------------------------------
        # Checked before the ASCII table, which would otherwise read the tag's
        # angle brackets as 《》 (and a quoted font colour as “”).
        if ch == "<":
            tag = SRT_MARKUP_RE.match(line, i)
            if tag is not None:
                flush()
                tokens.append(
                    Token(kind=KIND_MARKUP, zh=tag.group(), pinyin=None, source=_SOURCE_PUNCT)
                )
                i = tag.end()
                continue

        # --- ellipsis family -------------------------------------------------
        if ch == "…":
            pattern = _CJK_ELLIPSIS_LOOSE if normalize else _CJK_ELLIPSIS_RUN
            match = pattern.match(line, i)
            assert match is not None
            push_punct(ELLIPSIS if normalize else match.group())
            i = match.end()
            continue

        if ch == ".":
            if normalize:
                match = _ASCII_ELLIPSIS_LOOSE.match(line, i)
                if match is not None:  # "..", "...", ". . ."
                    push_punct(ELLIPSIS)
                    i = match.end()
                    continue
                push_punct("。")
                i += 1
                continue
            match = _DOT_RUN.match(line, i)
            assert match is not None
            push_punct(match.group())
            i = match.end()
            continue

        # --- dash family -----------------------------------------------------
        if ch == "—":
            pattern = _EM_DASH_LOOSE if normalize else _EM_DASH_RUN
            match = pattern.match(line, i)
            assert match is not None
            push_punct(DASH if normalize else match.group())
            i = match.end()
            continue

        if ch == "-":
            match = _HYPHEN_RUN.match(line, i)
            assert match is not None
            run = match.group()
            if len(run) == 1 and _is_inner_hyphen(line, i):
                # Gach noi NAM TRONG mot tu Latin hay mot khoang so: "Wi-Fi",
                # "Jean-Luc", "2019-2020". Truoc day no thanh "——" nen phu de
                # ghi ra "Wi——Fi" — sai chinh ta, va nguoi dung khong the doan
                # duoc vi sao. No khong phai dau gach doi nguoi noi (hai ben
                # phai co khoang trang) va cung khong phai gach ngang cau.
                buffer.append(ch)
                i += 1
                continue
            if len(run) >= 2:
                # Two or more hyphens are never a speaker marker.
                push_punct(DASH if normalize else run)
                i = match.end()
                continue
            if _is_speaker_marker(line, i):
                flush()
                tokens.append(
                    Token(kind=KIND_MARKER, zh=MARKER, pinyin=None, source=_SOURCE_PUNCT)
                )
            else:
                push_punct(DASH if normalize else MARKER)
            i += 1
            continue

        # --- single-character punctuation ------------------------------------
        if ch in CJK_PUNCT:
            if ch == "“":
                double_open = True
            elif ch == "”":
                double_open = False
            elif ch == "‘":
                single_open = True
            elif ch == "’":
                single_open = False
            push_punct(ch)
            i += 1
            continue

        if ch == '"':
            push_punct(("”" if double_open else "“") if normalize else ch)
            double_open = not double_open
            i += 1
            continue

        if ch == "'":
            # An apostrophe between two letters is a pinyin syllable separator
            # (Xī'ān, nǚ'ér), not a quote mark.
            if 0 < i < total - 1 and line[i - 1].isalpha() and line[i + 1].isalpha():
                buffer.append(ch)
                i += 1
                continue
            push_punct(("’" if single_open else "‘") if normalize else ch)
            single_open = not single_open
            i += 1
            continue

        if ch in ASCII_PUNCT_MAP:
            push_punct(ASCII_PUNCT_MAP[ch] if normalize else ch)
            i += 1
            continue

        buffer.append(ch)
        i += 1

    flush()
    return tokens


def _rebuilt(token: Token, pinyin: str | None) -> Token:
    """Copy a token with a new pinyin, without sharing its ``flags`` list."""
    return replace(token, pinyin=pinyin, flags=list(token.flags))


def merge_zh_py(
    zh_tokens: list[Token],
    py_tokens: list[Token],
) -> tuple[list[Token], bool]:
    """Zip the two lines of an existing file into one token list.

    Returns ``(tokens, matched)``.  Punctuation and markers always come from
    the Chinese line, because that is the line the editor typed the meaning
    into; the pinyin line only contributes pronunciations.

    When the two lines disagree on how many phrases they contain there is no
    honest way to pair them - README forbids shifting one phrase's pinyin onto
    the next - so every word comes back with ``pinyin=None`` and ``matched`` is
    ``False``.  The caller raises ``CUM_MISMATCH`` and S5 regenerates the
    pinyin from scratch.  Guessing here would produce a file that looks correct
    and is silently wrong, which is the one outcome worth avoiding.
    """
    zh_words = [t for t in zh_tokens if t.kind == KIND_WORD]
    py_words = [t for t in py_tokens if t.kind == KIND_WORD]

    if len(zh_words) != len(py_words):
        return [_rebuilt(t, None) for t in zh_tokens], False

    pinyins = iter(py_words)
    merged = [
        _rebuilt(t, next(pinyins).zh) if t.kind == KIND_WORD else _rebuilt(t, None)
        for t in zh_tokens
    ]
    return merged, True
