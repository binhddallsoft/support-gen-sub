"""Ba luật ngôn ngữ mà máy không tự đoán đúng được: 儿化音, biến điệu, viết hoa.

Vì sao ba thứ này cần một file kiểm riêng thay vì tin vào ``pypinyin``:

* **儿化音** — ``pypinyin`` đọc từng chữ, nên ``哪儿`` ra ``nǎ ér``. README mục 1
  chốt dạng rút gọn ``nǎr`` và cấm tách ``儿`` thành cụm riêng. Đây là chỗ tool
  phải *sửa* thư viện chứ không phải *dùng* thư viện.
* **Biến điệu qua ranh giới cụm** — trong một token thì ``pypinyin`` đã đúng sẵn
  (``一个`` → ``yígè``), nhưng ``不`` đứng riêng một cụm thì không ai biết chữ sau
  nó thanh mấy ngoài chính tool.
* **Viết hoa** — chữ cái đầu dòng pinyin phải hoa/thường theo dấu câu của cue
  *trước đó*, và phải hoa mà **vẫn còn dấu thanh**: ``ǎ`` → ``Ǎ``. Mất dấu thanh
  là mất nghĩa, không phải mất thẩm mỹ.

Ranh giới quan trọng nhất trong file này là ranh giới **儿 là hậu tố** và **儿 là
chữ thật**: ``儿子`` (érzi) và ``女儿`` (nǚér) không phải 儿化音. Rút gọn chúng
thành ``rzi``/``nǚr`` là sai từ, nặng hơn nhiều so với việc bỏ sót một ca 儿化音.
Vì vậy hai từ đó có test riêng, và test đó phải đỏ nếu ai nới rộng ``ERHUA_HEAD``.

Toàn bộ file chạy ngoại tuyến: chỉ cần ``jieba`` và ``pypinyin`` đã cài sẵn.
"""

from __future__ import annotations

import pytest

from srtgen.core.casing import apply_casing, capitalize_pinyin, lower_pinyin
from srtgen.core.erhua import erhua_pinyin, is_full_er_form, merge_erhua
from srtgen.core.rules import validate_text
from srtgen.core.sandhi import apply_sandhi
from srtgen.core.token import (
    FLAG_CASE_AMBIG,
    KIND_MARKER,
    KIND_PUNCT,
    KIND_WORD,
    Cue,
    Document,
    Token,
    render_py,
    render_zh,
)


# --------------------------------------------------------------------------- #
# tiện ích dựng token
# --------------------------------------------------------------------------- #

def word(zh: str, pinyin: str | None = None) -> Token:
    return Token(kind=KIND_WORD, zh=zh, pinyin=pinyin)


def punct(zh: str) -> Token:
    return Token(kind=KIND_PUNCT, zh=zh)


def marker() -> Token:
    return Token(kind=KIND_MARKER, zh="-")


def cue(index: int, tokens: list[Token]) -> Cue:
    return Cue(index=index, start=float(index), end=float(index) + 1.0, tokens=tokens)


def one_word(text: str, cfg: dict) -> list[tuple[str, str | None]]:
    """Chạy S5 cho đúng một câu và trả về ``[(zh, pinyin), ...]``.

    Đi qua ``tokenize_document`` chứ không gọi thẳng ``pypinyin`` là có chủ ý:
    thứ phải đúng là **đường mà phim thật đi qua**, gồm cả jieba, cả gộp 儿化音,
    cả biến điệu. Một test gọi thẳng hàm con sẽ vẫn xanh khi ai đó quên nối hàm
    ấy vào chặng.
    """
    from srtgen.stages.s5_tokenize import tokenize_document

    doc = Document(cues=[cue(1, [word(text)])], meta={"segmented": False})
    tokenize_document(doc, cfg, names={})
    return [(t.zh, t.pinyin) for t in doc.cues[0].tokens]


def srt_block(zh: str, py: str) -> str:
    return f"1\n00:00:01,000 --> 00:00:02,000\n{zh}\n{py}\n\n"


# --------------------------------------------------------------------------- #
# 1. 儿化音 — dạng rút gọn
# --------------------------------------------------------------------------- #

#: Ba ca bắt buộc của đặc tả, cộng những ca cùng loại mà README nêu tên.
ERHUA_CASES = [
    ("哪儿", "nǎr"),
    ("这儿", "zhèr"),
    ("一会儿", "yíhuìr"),
    ("那儿", "nàr"),
    ("点儿", "diǎnr"),
    ("一块儿", "yíkuàir"),
    ("玩儿", "wánr"),
]


@pytest.mark.parametrize("zh, expected", ERHUA_CASES)
def test_erhua_is_contracted(zh: str, expected: str, cfg: dict) -> None:
    """``哪儿`` phải ra đúng ``nǎr``, trong đúng MỘT cụm.

    Hai điều kiện, không phải một: pinyin đúng mà tách làm hai cụm thì dòng Hán
    và dòng pinyin vẫn khớp số cụm, nhưng README cấm tách ``儿`` ra, và người
    biên tập sẽ phải sửa tay từng dòng.
    """
    tokens = one_word(zh, cfg)
    assert len(tokens) == 1, f"{zh} bị tách thành {len(tokens)} cụm: {tokens}"
    assert tokens[0] == (zh, expected)


@pytest.mark.parametrize("zh, expected", ERHUA_CASES)
def test_erhua_never_keeps_the_full_er_syllable(zh: str, expected: str, cfg: dict) -> None:
    """Cấm tuyệt đối ``nǎér`` / ``zhèér`` — dạng corpus đang ghi sai.

    Kiểm riêng khỏi test trên vì đây là điều kiện *cấm*, và một điều kiện cấm
    phải đỏ vì đúng lý do của nó chứ không lẫn vào một phép so bằng.
    """
    del expected
    pinyin = one_word(zh, cfg)[0][1] or ""
    assert not is_full_er_form(pinyin), f"{zh} vẫn còn âm tiết 儿 đầy đủ: {pinyin}"
    assert pinyin.endswith("r")


@pytest.mark.parametrize(
    "head, tail, merged, pinyin",
    [
        ("哪", "nǎ", "哪儿", "nǎr"),
        ("这", "zhè", "这儿", "zhèr"),
        ("一会", "yíhuì", "一会儿", "yíhuìr"),
        ("点", "diǎn", "点儿", "diǎnr"),
    ],
)
def test_merge_erhua_joins_a_standalone_er_token(
    head: str, tail: str, merged: str, pinyin: str
) -> None:
    """``儿`` đứng riêng (file bên dịch gửi sang) phải được gộp vào cụm trước.

    Đây là đường đi của lệnh ``fix``: ranh giới cụm trong file có sẵn do người
    biên tập đặt, tool không cắt lại — nên chỗ duy nhất sửa được một ``儿`` lạc
    là ở đây.
    """
    tokens = [word(head, tail), word("儿", "ér")]
    merge_erhua(tokens)
    assert [(t.zh, t.pinyin) for t in tokens] == [(merged, pinyin)]


@pytest.mark.parametrize(
    "base, expected",
    [
        ("nǎ", "nǎr"),
        ("diǎn", "diǎnr"),   # bỏ đuôi -n
        ("wán", "wánr"),
        ("kòng", "kòngr"),   # bỏ đuôi -ng
        ("yíhuì", "yíhuìr"),
    ],
)
def test_erhua_pinyin_keeps_the_tone_mark(base: str, expected: str) -> None:
    """Bỏ đuôi ``n``/``ng`` rồi thêm ``r``, **giữ nguyên dấu thanh** của âm tiết gốc."""
    assert erhua_pinyin(base) == expected


# --------------------------------------------------------------------------- #
# 2. 儿 là chữ thật — vùng cấm
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("zh, expected", [("儿子", "érzi"), ("女儿", "nǚér")])
def test_real_er_words_are_left_alone(zh: str, expected: str, cfg: dict) -> None:
    """``儿子`` và ``女儿`` KHÔNG phải 儿化音 — cấm đụng vào.

    Đây là ranh giới đắt nhất của module: ``儿`` ở đây là một chữ có nghĩa
    (con trai / con gái), không phải hậu tố làm cong lưỡi. Rút gọn ``nǚér``
    thành ``nǚr`` là đọc sai hẳn một từ, tệ hơn nhiều so với bỏ sót một ca
    儿化音 — nên test này phải đỏ ngay khi ai đó nới ``ERHUA_HEAD`` cho rộng.
    """
    tokens = one_word(zh, cfg)
    assert len(tokens) == 1
    assert tokens[0] == (zh, expected)


@pytest.mark.parametrize(
    "tokens",
    [
        [("女", "nǚ"), ("儿", "ér")],
        [("儿", "ér"), ("子", "zi")],
        [("婴", "yīng"), ("儿", "ér")],
    ],
)
def test_merge_erhua_refuses_blocked_heads(tokens: list[tuple[str, str]]) -> None:
    """``女 + 儿``, ``婴 + 儿`` không được gộp thành 儿化音.

    ``儿`` đứng **trước** (``儿子``) cũng vậy: hậu tố thì phải đứng sau, và một
    module chỉ nhìn hai token liền nhau rất dễ gộp nhầm chiều.
    """
    items = [word(zh, py) for zh, py in tokens]
    merge_erhua(items)
    assert [(t.zh, t.pinyin) for t in items] == tokens


def test_validator_does_not_flag_real_er_words() -> None:
    """``女儿 / Nǚér`` viết liền là đúng — validator không được báo oan.

    ``ERHUA_SPLIT`` là mã mức ``error``; báo oan ở đây nghĩa là người dùng phải
    đi "sửa" một dòng vốn đã đúng, và lần sau họ sẽ bỏ qua cả báo cáo.
    """
    for zh, py in (("女儿 呢。", "Nǚér ne。"), ("儿子 呢。", "Érzi ne。")):
        codes = [f.code for f in validate_text(srt_block(zh, py))]
        assert codes == [], f"{zh} bị báo oan: {codes}"


def test_validator_catches_a_split_er() -> None:
    """Ngược lại: ``哪 儿`` tách rời đúng là lỗi mà README cấm."""
    codes = [f.code for f in validate_text(srt_block("哪 儿 呢。", "Nǎ ér ne。"))]
    assert "ERHUA_SPLIT" in codes


def test_validator_catches_the_full_er_syllable() -> None:
    """``哪儿`` viết liền nhưng pinyin ghi ``Nǎér`` vẫn là lỗi.

    Đây chính là dạng sai mà ``corpus/completed.srt`` mắc ở 19 cue; nếu validator
    bỏ qua nó thì danh sách lỗi corpus đã biết sẽ rỗng một cách vô nghĩa.
    """
    codes = [f.code for f in validate_text(srt_block("哪儿 呢。", "Nǎér ne。"))]
    assert "ERHUA_SPLIT" in codes


# --------------------------------------------------------------------------- #
# 3. Biến điệu
# --------------------------------------------------------------------------- #

def test_bu_becomes_bu_rising_before_a_fourth_tone() -> None:
    """``不`` + âm tiết thanh 4 → ``bú``. Đây là biến điệu qua ranh giới cụm."""
    tokens = [word("不", "bù"), word("是", "shì")]
    apply_sandhi(tokens, {"bu": True})
    assert [t.pinyin for t in tokens] == ["bú", "shì"]


@pytest.mark.parametrize(
    "second, tone",
    [("好", "hǎo"), ("行", "xíng"), ("说", "shuō")],
)
def test_bu_stays_falling_before_other_tones(second: str, tone: str) -> None:
    """Trước thanh 1/2/3 thì ``不`` giữ nguyên ``bù``.

    Corpus đo được ``bù``×100 so với ``bú``×41: đổi tất cả thành ``bú`` sẽ làm
    hỏng nhiều hơn là sửa, nên chiều "không đổi" cũng phải có test.
    """
    tokens = [word("不", "bù"), word(second, tone)]
    apply_sandhi(tokens, {"bu": True})
    assert tokens[0].pinyin == "bù"


def test_bu_sandhi_can_be_turned_off() -> None:
    """Tắt trong cấu hình thì không được sửa gì — cấu hình phải có tác dụng thật."""
    tokens = [word("不", "bù"), word("是", "shì")]
    apply_sandhi(tokens, {"bu": False})
    assert tokens[0].pinyin == "bù"


def test_yi_sandhi_is_off_by_default() -> None:
    """``一`` giữ ``yī`` theo mặc định vì corpus giữ như vậy (``yī``×66).

    Bật lên thì phải đổi thật; hai chiều cùng một test để mặc định và tuỳ chọn
    không thể lệch nhau mà vẫn xanh.
    """
    off = [word("一", "yī"), word("个", "gè")]
    apply_sandhi(off, {"yi": False})
    assert off[0].pinyin == "yī"

    on = [word("一", "yī"), word("个", "gè")]
    apply_sandhi(on, {"yi": True})
    assert on[0].pinyin == "yí"


@pytest.mark.parametrize("zh, expected", [("一个", "yígè"), ("不是", "búshì")])
def test_sandhi_inside_one_token_is_already_right(zh: str, expected: str, cfg: dict) -> None:
    """Biến điệu **trong** một cụm do ``pypinyin`` lo, và nó đúng sẵn.

    Ghi lại bằng test để không ai "sửa thêm cho chắc" và biến ``yígè`` thành
    ``yígé`` bằng một lượt áp luật thứ hai.
    """
    assert one_word(zh, cfg)[0] == (zh, expected)


# --------------------------------------------------------------------------- #
# 4. Viết hoa — giữ dấu thanh
# --------------------------------------------------------------------------- #

TONE_PAIRS = [
    ("ā", "Ā"), ("á", "Á"), ("ǎ", "Ǎ"), ("à", "À"),
    ("ē", "Ē"), ("ī", "Ī"), ("ō", "Ō"), ("ū", "Ū"),
    ("ǖ", "Ǖ"), ("ǘ", "Ǘ"), ("ǚ", "Ǚ"), ("ǜ", "Ǜ"),
    ("ń", "Ń"), ("ň", "Ň"), ("ǹ", "Ǹ"), ("é", "É"),
]


@pytest.mark.parametrize("lower, upper", TONE_PAIRS)
def test_capitalize_keeps_the_tone_mark(lower: str, upper: str) -> None:
    """``ǎ`` phải thành ``Ǎ``, không phải ``A``.

    Mất dấu thanh là mất nghĩa: ``mā``/``mǎ`` là "mẹ"/"ngựa". Bảng này đi hết
    bốn thanh của mỗi nguyên âm có dấu, kể cả ``ü`` và ``n`` — ba ký tự mà một
    phép chuẩn hoá "bỏ dấu rồi viết hoa" sẽ làm hỏng.
    """
    assert capitalize_pinyin(lower) == upper


@pytest.mark.parametrize("lower, upper", TONE_PAIRS)
def test_lowercase_is_the_exact_inverse(lower: str, upper: str) -> None:
    """Hạ chữ thường phải trả về đúng ký tự ban đầu — trình sửa dựa vào điều này."""
    assert lower_pinyin(upper) == lower


def test_capitalize_only_touches_the_first_letter() -> None:
    """Chỉ chữ cái **đầu tiên** đổi; phần còn lại của cụm giữ nguyên từng ký tự."""
    assert capitalize_pinyin("nǎr") == "Nǎr"
    assert capitalize_pinyin("yíhuìr") == "Yíhuìr"
    assert capitalize_pinyin("") == ""


# --------------------------------------------------------------------------- #
# 5. Viết hoa theo dấu câu của cue trước
# --------------------------------------------------------------------------- #

def _cased_document() -> Document:
    """Một tài liệu nhỏ đi hết các nhánh của bảng luật viết hoa ở build-spec mục 6."""
    doc = Document(
        cues=[
            cue(1, [word("你好", "nǐhǎo"), punct("。")]),          # cue đầu file
            cue(2, [word("我", "wǒ"), punct("，")]),               # sau 。 -> HOA
            cue(3, [word("他", "tā"), punct("……")]),              # sau ， -> thường
            cue(4, [word("她", "tā"), punct("。")]),               # sau …… -> nhập nhằng
            cue(5, [word("们", "men"), punct("？")]),              # sau 。 -> HOA
            cue(6, [word("好", "hǎo"), punct("！")]),              # sau ？ -> HOA
            cue(7, [marker(), word("走", "zǒu"), punct("。")]),    # sau ！ -> HOA
            cue(8, [word("来", "lái")]),                          # sau 。 -> HOA
        ]
    )
    apply_casing(doc, {}, {})
    return doc


@pytest.mark.parametrize(
    "index, expected",
    [
        (1, "Nǐhǎo。"),   # cue đầu file luôn viết hoa
        (2, "Wǒ，"),      # cue trước kết bằng 。
        (3, "tā……"),      # cue trước kết bằng ， -> giữ thường
        (5, "Men？"),
        (6, "Hǎo！"),
        (7, "- Zǒu。"),   # ngay sau marker luôn viết hoa
        (8, "Lái"),
    ],
)
def test_casing_follows_the_previous_cue(index: int, expected: str) -> None:
    doc = _cased_document()
    assert render_py(doc.cues[index - 1].tokens) == expected


def test_casing_flags_the_ambiguous_case_after_an_ellipsis() -> None:
    """Sau ``……`` corpus tự mâu thuẫn 54/46, nên tool **gắn cờ** thay vì đoán bừa.

    Cờ ``CASE_AMBIG`` là thứ đưa cue này lên danh sách người duyệt ở báo cáo;
    mất cờ nghĩa là 37 cue nhập nhằng biến mất khỏi tầm mắt người soát.
    """
    doc = _cased_document()
    flagged = doc.cues[3].tokens[0]
    assert FLAG_CASE_AMBIG in flagged.flags


def test_casing_does_not_disturb_the_chinese_line() -> None:
    """Viết hoa chỉ đụng dòng pinyin — dòng Hán không có chữ hoa để mà đụng."""
    doc = _cased_document()
    assert render_zh(doc.cues[0].tokens) == "你好。"
    assert render_zh(doc.cues[6].tokens) == "- 走。"
