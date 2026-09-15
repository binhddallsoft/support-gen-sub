"""Validator — ``srtgen/core/rules.py``.

Validator là thứ quyết định "file này đã xong chưa", nên nó phải sai theo đúng một
chiều: **thà bỏ sót còn hơn báo oan**. Người dùng cuối không phải dân IT; một bản
báo cáo đỏ lòm vì những lỗi không có thật sẽ dạy họ thói quen bỏ qua báo cáo, và
lúc đó lỗi thật cũng chìm theo.

Vì vậy file này kiểm hai chiều, và chiều thứ hai mới là chiều khó:

* mỗi mã trong bảng 19 mã của build-spec mục 3 phải **bắt được** ca sai của nó;
* và những dạng viết **đúng theo README** phải đi qua sạch, không sinh finding nào.
"""

from __future__ import annotations

import pytest

from srtgen.core.rules import (
    RULES,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    Finding,
    format_report_lines,
    summarize,
    validate_document,
    validate_text,
)
from srtgen.core.token import Cue, Document, Token, KIND_PUNCT, KIND_WORD

#: 19 mã lỗi của build-spec mục 3, ánh xạ 1-1 với checklist 12 mục của README.
EXPECTED_CODES = {
    "BLOCK_SHAPE", "INDEX_SEQ", "TIMESTAMP_FORMAT", "TIMESTAMP_ORDER",
    "CUM_MISMATCH", "PAIR_MISMATCH", "LEADING_SPACE", "TRAILING_SPACE",
    "DOUBLE_SPACE", "ASCII_PUNCT", "SPACE_AROUND_PUNCT", "ELLIPSIS_FORM",
    "DASH_FORM", "MARKER_SPACING", "MARKER_SYNC", "PUNCT_SYNC",
    "NAME_INCONSISTENT", "ERHUA_SPLIT", "EMPTY_CUE",
}


def block(index: int, zh: str, py: str | None = None, *, start: str = "00:00:01,000",
          end: str = "00:00:02,000") -> str:
    """Dựng một block .srt nhỏ nhất đủ để kiểm một luật."""
    lines = [str(index), f"{start} --> {end}", zh]
    if py is not None:
        lines.append(py)
    return "\n".join(lines) + "\n\n"


def codes(text: str) -> list[str]:
    return [f.code for f in validate_text(text)]


# --------------------------------------------------------------------------- #
# bảng mã
# --------------------------------------------------------------------------- #

def test_rule_table_matches_build_spec() -> None:
    """Bảng mã phải đúng 19 mã, không thừa không thiếu.

    Mỗi mã là một mục trong checklist README; thêm mã mà không sửa README nghĩa
    là tool bắt đầu áp một luật không ai đồng ý.
    """
    assert set(RULES) == EXPECTED_CODES


def test_every_severity_is_valid() -> None:
    valid = {SEVERITY_ERROR, SEVERITY_WARN, SEVERITY_INFO}
    assert {severity for severity, _ in RULES.values()} <= valid


def test_every_rule_has_a_vietnamese_label() -> None:
    """Nhãn hiển thị phải có và không được là mã kỹ thuật viết lại.

    Report và UI dùng nhãn này làm tiêu đề nhóm. "ERHUA_SPLIT" không nói gì với
    người dùng cuối; "儿化音 bị tách rời" thì có.
    """
    for code, (_severity, label) in RULES.items():
        assert label.strip(), code
        assert label != code


# --------------------------------------------------------------------------- #
# chiều 1 — bắt được lỗi
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "code, text",
    [
        ("BLOCK_SHAPE", block(1, "好")),
        ("INDEX_SEQ", block(1, "好", "Hǎo") + block(5, "好", "Hǎo",
                                                   start="00:00:02,000",
                                                   end="00:00:03,000")),
        ("TIMESTAMP_FORMAT", block(1, "好", "Hǎo", start="0:00:01.0")),
        ("TIMESTAMP_ORDER", block(1, "好", "Hǎo", start="00:00:05,000")),
        ("CUM_MISMATCH", block(1, "好 唱得 真好", "Hǎo zhēnhǎo")),
        ("LEADING_SPACE", block(1, "好", " Hǎo")),
        ("TRAILING_SPACE", block(1, "好", "Hǎo ")),
        ("DOUBLE_SPACE", block(1, "好  啊", "Hǎo  a")),
        ("ASCII_PUNCT", block(1, "好, 啊", "Hǎo, a")),
        ("SPACE_AROUND_PUNCT", block(1, "好 ，啊", "Hǎo ，a")),
        ("ELLIPSIS_FORM", block(1, "好…", "Hǎo…")),
        ("ELLIPSIS_FORM", block(1, "好...", "Hǎo...")),
        ("ELLIPSIS_FORM", block(1, "好. . .", "Hǎo. . .")),
        ("DASH_FORM", block(1, "美国-纽约", "Měiguó-Niǔyuē")),
        ("MARKER_SPACING", block(1, "起来。- 我", "Qǐlái。- Wǒ")),
        ("MARKER_SYNC", block(1, "- 好 啊", "Hǎo a")),
        ("PUNCT_SYNC", block(1, "“好”", "Hǎo")),
        ("ERHUA_SPLIT", block(1, "住 哪 儿", "zhù nǎ er")),
        ("ERHUA_SPLIT", block(1, "住 哪儿", "zhù nǎér")),
        ("EMPTY_CUE", block(1, "。", "。")),
        ("PAIR_MISMATCH", block(1, "好 唱得 真好", "Hǎochàng dé zhēnhǎo")),
        ("NAME_INCONSISTENT", block(1, "龙拳 小子", "Lóngquán xiǎozi")
         + block(2, "龙 拳 小子", "Lóng quán xiǎozi",
                 start="00:00:02,000", end="00:00:03,000")),
    ],
)
def test_each_code_fires_on_its_own_bad_case(code: str, text: str) -> None:
    """Từng mã một, bắt bằng đúng ca sai mà README mô tả cho mã đó."""
    assert code in codes(text)


def test_pair_mismatch_catches_the_readme_counterexample() -> None:
    """Ví dụ sai kinh điển của README mục 1: đủ 3 cụm nhưng ghép lệch âm.

    ``好 | 唱得 | 真好`` ghép với ``Hǎochàng | dé | zhēnhǎo`` — ``chàng`` thuộc về
    ``唱`` nhưng bị kéo sang cụm của ``好``. Đếm số cụm không phát hiện được, nên
    nếu luật này gãy thì cả một loại lỗi biến mất không dấu vết.
    """
    findings = validate_text(block(1, "好 唱得 真好", "Hǎochàng dé zhēnhǎo"))
    assert [f.code for f in findings if f.severity == SEVERITY_WARN].count("PAIR_MISMATCH") >= 1


# --------------------------------------------------------------------------- #
# chiều 2 — không báo oan
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "text",
    [
        # Ví dụ đúng của README, phải sạch tuyệt đối.
        block(1, "好，唱得 真好", "Hǎo，chàngdé zhēnhǎo"),
        block(1, "陈 路周，你 闯 祸 了。", "Chén Lùzhōu，nǐ chuǎng huò le。"),
        block(1, "短剧：《不愧 是 顶级 女 保镖》", "Duǎnjù：《Bùkuì shì dǐngjí nǚ bǎobiāo》"),
        block(1, "您 好，您 拨打……", "Nín hǎo，nín bōdǎ……"),
        block(1, "- 我 没有 - 你 说 这……", "- wǒ méiyǒu - nǐ shuō zhè……"),
        block(1, "美国——纽约", "Měiguó——Niǔyuē"),
        # Ngoại lệ cấu trúc: đúng 1 space giữa dấu câu và marker.
        block(1, "起来。 - 我", "Qǐlái。 - Wǒ"),
        # Marker ngay sau dấu mở.
        block(1, "‘- 他们 好", "‘- tāmen hǎo"),
        # Dấu nháy phân âm tiết không phải dấu câu ASCII.
        block(1, "西安", "Xī'ān"),
        # Dấu phẩy trong dòng timestamp — ngoại lệ bắt buộc của README.
        block(1, "好", "Hǎo", start="00:01:48,380", end="00:01:49,380"),
        # File rỗng không phải là lỗi, chỉ là không có gì.
        "",
    ],
)
def test_correct_files_produce_no_findings(text: str) -> None:
    """Mọi ví dụ ĐÚNG trong README phải đi qua validator sạch trơn."""
    assert validate_text(text) == []


@pytest.mark.parametrize(
    "zh, py",
    [
        ("住 哪儿", "zhù nǎr"),        # README ghi thẳng cặp này
        ("坐 这儿", "zuò zhèr"),       # README ghi thẳng cặp này
        ("在 那儿", "zài nàr"),        # README ghi thẳng cặp này
        ("等 一会儿", "děng yíhuìr"),  # README ghi thẳng cặp này
        ("远 点儿", "yuǎn diǎnr"),
        ("没 事儿", "méi shìr"),
        ("唱 歌儿", "chàng gēr"),
    ],
)
def test_valid_erhua_contraction_is_not_flagged(zh: str, py: str) -> None:
    """Dạng rút gọn ĐÚNG của 儿化音 không được bị báo lỗi.

    README mục 1 liệt kê thẳng bốn cặp ``哪儿/nǎr``、``这儿/zhèr``、``那儿/nàr``、
    ``一会儿/yíhuìr``. Nếu validator báo lỗi cho chính dạng mà README yêu cầu thì
    lệnh ``fix`` rơi vào bẫy không lối ra: sửa theo README thì validator kêu, sửa
    theo validator thì vi phạm README.
    """
    assert validate_text(block(1, zh, py)) == []


def test_erhua_word_with_full_er_syllable_is_left_alone() -> None:
    """``儿子``、``女儿``、``婴儿`` — ``儿`` ở đây là âm tiết đầy đủ, không phải hậu tố.

    Rút gọn chúng sẽ ra ``érzi`` → ``rzi``. Luật 儿化音 phải biết dừng lại ở đây.
    """
    assert validate_text(block(1, "我 的 儿子", "wǒ de érzi")) == []
    assert validate_text(block(1, "我 女儿", "wǒ nǚér")) == []


# --------------------------------------------------------------------------- #
# validate_document
# --------------------------------------------------------------------------- #

def _word(zh: str, pinyin: str | None) -> Token:
    return Token(kind=KIND_WORD, zh=zh, pinyin=pinyin)


def _punct(zh: str) -> Token:
    return Token(kind=KIND_PUNCT, zh=zh)


def test_validate_document_is_quiet_on_clean_output() -> None:
    """Đầu ra sạch của pipeline phải cho 0 finding.

    ``s8_emit`` gọi hàm này ngay trước khi ghi file và tô đỏ giao diện nếu có
    ``error``. Một finding báo oan ở đây làm mọi lần chạy đúng đều hiện màu đỏ.
    """
    doc = Document(
        cues=[
            Cue(1, 0.0, 1.0, [_word("好", "Hǎo"), _punct("，"),
                              _word("唱得", "chàngdé"), _word("真好", "zhēnhǎo")]),
            Cue(2, 1.0, 2.0, [_word("住", "zhù"), _word("哪儿", "nǎr")]),
        ]
    )
    assert validate_document(doc) == []


def test_validate_document_catches_structural_damage() -> None:
    """Document hỏng phải lộ ra đủ loại lỗi, không chỉ lỗi đầu tiên."""
    doc = Document(
        cues=[
            Cue(3, 5.0, 1.0, [_word("好", None)]),   # số nhảy cóc + end < start + thiếu pinyin
            Cue(1, 1.0, 2.0, []),                    # cue không có cụm nào
        ]
    )
    found = {f.code for f in validate_document(doc)}
    assert {"INDEX_SEQ", "TIMESTAMP_ORDER", "CUM_MISMATCH", "EMPTY_CUE"} <= found


def test_validate_document_accepts_an_empty_document() -> None:
    assert validate_document(Document()) == []


# --------------------------------------------------------------------------- #
# hình dạng kết quả — thứ report/UI dựa vào
# --------------------------------------------------------------------------- #

def test_finding_shape() -> None:
    findings = validate_text(block(1, "好, 啊", "Hǎo, a"))
    assert findings
    for f in findings:
        assert isinstance(f, Finding)
        assert f.code in RULES
        assert f.severity == RULES[f.code][0]
        assert f.message.strip(), "message rỗng thì report không có gì để hiện"
        assert f.to_dict()["code"] == f.code


def test_summarize_shape() -> None:
    """``summarize`` là thứ UI đọc để quyết định tô đỏ hay tô xanh."""
    result = summarize(validate_text(block(1, "好, 啊", "Hǎo, a")))
    assert set(result) == {"error", "warn", "info", "by_code"}
    assert result["error"] == 2 and result["by_code"]["ASCII_PUNCT"] == 2
    assert summarize([]) == {"error": 0, "warn": 0, "info": 0, "by_code": {}}


def test_format_report_lines_returns_text_not_print() -> None:
    """Thư viện không được ``print()``; nó trả về dòng để người gọi tự in.

    Dòng trống ngăn cách các mục là định dạng có chủ ý cho CLI, không phải rác:
    báo cáo có phần tóm tắt, phần theo loại lỗi và phần chi tiết. Vì vậy chỉ
    khẳng định "trả về danh sách chuỗi và có nội dung thật", chứ không đòi
    mọi dòng đều khác rỗng.
    """
    lines = format_report_lines(validate_text(block(1, "好, 啊", "Hǎo, a")))
    assert lines and all(isinstance(line, str) for line in lines)
    assert any(line.strip() for line in lines)
    assert not any(line != line.rstrip() for line in lines), "không được thừa khoảng trắng cuối dòng"


# --------------------------------------------------------------------------- #
# tốc độ — validator được gọi trong vòng lặp của UI
# --------------------------------------------------------------------------- #

def test_validate_text_handles_the_whole_corpus(completed_text: str) -> None:
    """Chạy hết 1351 cue mà không nổ, và trả về đúng kiểu dữ liệu."""
    findings = validate_text(completed_text)
    assert all(isinstance(f, Finding) for f in findings)
    assert all(f.cue_index is None or 1 <= f.cue_index <= 1351 for f in findings)
