"""Đọc/ghi SRT — ``srtgen/core/srt.py``.

Tầng này là cửa duy nhất giữa tool và thế giới bên ngoài: mọi file người khác gửi
sang đi vào qua ``parse_srt_document``, mọi file tool giao lại đi ra qua
``emit_srt``. Nếu cửa đó không đóng kín (đọc rồi ghi lại ra khác đi) thì lệnh
``fix`` sẽ làm hỏng file thêm một chút sau mỗi lần chạy, và không ai phát hiện ra
cho tới khi mở bằng Aegisub.

Vì vậy bài kiểm nặng nhất ở đây không phải là các ca lẻ mà là round-trip trên
toàn bộ 1351 cue của ``corpus/completed.srt``.
"""

from __future__ import annotations

import pytest

from srtgen.core.srt import (
    TIMESTAMP_RE,
    emit_srt,
    format_timestamp,
    merge_zh_py,
    parse_srt,
    parse_srt_document,
    parse_timestamp_line,
    tokenize_line,
)
from srtgen.core.token import KIND_MARKER, KIND_PUNCT, KIND_WORD, render_zh

from tests.known_corpus_errors import CUM_MISMATCH_CUES

CORPUS_CUES = 1351


# --------------------------------------------------------------------------- #
# mốc thời gian
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "seconds, expected",
    [
        (79.58, "00:01:19,580"),        # đúng cue số 1 của corpus
        (0.0, "00:00:00,000"),
        (0.9995, "00:00:01,000"),       # làm tròn ms phải tràn sang giây
        (3599.9996, "01:00:00,000"),    # ... và tràn tiếp sang giờ
        (359999.999, "99:59:59,999"),   # trần của định dạng 2 chữ số giờ
        (-3.0, "00:00:00,000"),         # thời gian âm là dữ liệu hỏng, không phải lý do để nổ
        (float("nan"), "00:00:00,000"),
    ],
)
def test_format_timestamp(seconds: float, expected: str) -> None:
    """Chuỗi sinh ra phải luôn khớp ``TIMESTAMP_RE``, kể cả với đầu vào vô lý.

    Ghi ra một mốc thời gian sai cú pháp là hỏng cả file với Aegisub: nó bỏ qua
    từ block đó trở đi. Thà kẹp giá trị vô lý về 0 rồi để validator
    ``TIMESTAMP_ORDER`` la lên, còn hơn ghi ra thứ không đọc lại được.
    """
    assert format_timestamp(seconds) == expected
    assert TIMESTAMP_RE.match(f"{expected} --> {expected}")


def test_parse_timestamp_line() -> None:
    assert parse_timestamp_line("00:01:19,580 --> 00:01:22,100") == (79.58, 82.1)


def test_timestamp_round_trip() -> None:
    """Đọc rồi ghi lại một mốc thời gian phải ra đúng chuỗi cũ."""
    line = "00:39:49,160 --> 00:39:51,640"
    start, end = parse_timestamp_line(line)
    assert f"{format_timestamp(start)} --> {format_timestamp(end)}" == line


# --------------------------------------------------------------------------- #
# tách block
# --------------------------------------------------------------------------- #

def test_parse_srt_reads_every_block_of_completed(completed_text: str) -> None:
    """1351 block, block nào cũng đúng 4 dòng, số thứ tự liên tục 1..1351."""
    blocks = parse_srt(completed_text)
    assert len(blocks) == CORPUS_CUES
    assert sorted({b.raw_line_count for b in blocks}) == [4]
    assert [b.index for b in blocks] == list(range(1, CORPUS_CUES + 1))


def test_parse_srt_reads_three_line_blocks(raw_text: str) -> None:
    """``raw.srt`` chỉ có dòng Hán — phải đọc được, không phải bị từ chối.

    Đây chính là hình dạng đầu ra của chặng ASR, nên nếu tầng đọc coi nó là file
    hỏng thì cả pipeline không chạy được.
    """
    blocks = parse_srt(raw_text)
    assert len(blocks) == CORPUS_CUES
    assert sorted({b.raw_line_count for b in blocks}) == [3]


def test_parse_srt_reads_filter(filter_text: str) -> None:
    blocks = parse_srt(filter_text)
    assert len(blocks) == CORPUS_CUES
    assert sorted({b.raw_line_count for b in blocks}) == [4]


@pytest.mark.parametrize(
    "text, expected_blocks",
    [
        ("", 0),
        ("\n\n\n", 0),
        # BOM + CRLF: đúng thứ một editor trên Windows sinh ra.
        ("﻿1\r\n00:00:01,000 --> 00:00:02,000\r\n好\r\nHǎo\r\n\r\n", 1),
        # Thiếu dòng trống ở cuối file.
        ("1\n00:00:01,000 --> 00:00:02,000\n好\nHǎo", 1),
        # Dòng trống thừa giữa các block không được đẻ ra block ma.
        (
            "1\n00:00:01,000 --> 00:00:02,000\n好\nHǎo\n\n\n\n"
            "2\n00:00:02,000 --> 00:00:03,000\n好\nHǎo\n",
            2,
        ),
    ],
)
def test_parse_srt_tolerates_messy_files(text: str, expected_blocks: int) -> None:
    """File bẩn phải đọc được rồi mới sửa; từ chối đọc thì không sửa được gì.

    Người dùng cuối nhận file từ bên dịch, không kiểm soát được cách nó được ghi
    ra. Tool mà chỉ đọc được file đã hoàn hảo thì đúng vào lúc cần nhất nó vô dụng.
    """
    assert len(parse_srt(text)) == expected_blocks


# --------------------------------------------------------------------------- #
# tách một dòng thành token
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "line, expected",
    [
        # README mục 3 — marker mở đầu và marker giữa dòng.
        (
            "- 我 没有 - 你 说 这……",
            [(KIND_MARKER, "-"), (KIND_WORD, "我"), (KIND_WORD, "没有"),
             (KIND_MARKER, "-"), (KIND_WORD, "你"), (KIND_WORD, "说"),
             (KIND_WORD, "这"), (KIND_PUNCT, "……")],
        ),
        # `-` dính giữa hai chữ là dấu ngắt lời, KHÔNG phải marker đổi người nói.
        (
            "我 说 什么-你 别 走",
            [(KIND_WORD, "我"), (KIND_WORD, "说"), (KIND_WORD, "什么"),
             (KIND_PUNCT, "-"), (KIND_WORD, "你"), (KIND_WORD, "别"),
             (KIND_WORD, "走")],
        ),
        # Sau dấu mở `‘` thì `-` vẫn là marker (ca cue 1296 của corpus).
        ("‘- 他们", [(KIND_PUNCT, "‘"), (KIND_MARKER, "-"), (KIND_WORD, "他们")]),
        # Sau dấu kết câu cũng vậy — 11 ca "corpus sai" đi vào bằng cửa này.
        (
            "起来。- 我",
            [(KIND_WORD, "起来"), (KIND_PUNCT, "。"), (KIND_MARKER, "-"),
             (KIND_WORD, "我")],
        ),
        # `-` đứng CUỐI dòng sau một chữ là dấu ngắt lời, KHÔNG phải marker:
        # README mục 3 nói thẳng "Dấu `-` ở cuối câu hoặc dùng để thể hiện lời nói
        # bị ngắt không phải marker; trường hợp đó bắt buộc đổi thành `——`".
        # Marker chỉ mở được lượt thoại ở đầu dòng, sau dấu câu, hoặc trong dòng
        # đã mở đầu bằng marker.
        ("好 -", [(KIND_WORD, "好"), (KIND_PUNCT, "-")]),
        # Chỉ có mỗi marker.
        ("- ", [(KIND_MARKER, "-")]),
        # Dấu ôm hai đầu dòng.
        (
            "《不愧 是 顶级 女 保镖》",
            [(KIND_PUNCT, "《"), (KIND_WORD, "不愧"), (KIND_WORD, "是"),
             (KIND_WORD, "顶级"), (KIND_WORD, "女"), (KIND_WORD, "保镖"),
             (KIND_PUNCT, "》")],
        ),
    ],
)
def test_tokenize_line(line: str, expected: list[tuple[str, str]]) -> None:
    assert [(t.kind, t.zh) for t in tokenize_line(line)] == expected


def test_tokenize_line_keeps_text_verbatim_by_default() -> None:
    """Không bật ``normalize`` thì không được sửa gì — kể cả dấu ASCII.

    Lệnh ``check`` dùng chế độ này. Một bộ đọc "tiện tay sửa hộ" sẽ làm validator
    mù đúng những lỗi mà nó tồn tại để bắt.
    """
    tokens = tokenize_line("好, 唱得 真好...")
    assert (KIND_PUNCT, ",") in [(t.kind, t.zh) for t in tokens]
    assert "……" not in [t.zh for t in tokens]


def test_tokenize_line_normalizes_for_fix() -> None:
    """Bật ``normalize`` thì ``,`` thành ``，`` và ``...`` thành ``……``.

    README mục 3 và 4 quy định đúng hai phép đổi này; đây là đường của lệnh ``fix``.
    """
    tokens = tokenize_line("好, 唱得 真好...", normalize=True)
    assert [(t.kind, t.zh) for t in tokens] == [
        (KIND_WORD, "好"), (KIND_PUNCT, "，"),
        (KIND_WORD, "唱得"), (KIND_WORD, "真好"), (KIND_PUNCT, "……"),
    ]


# --------------------------------------------------------------------------- #
# ghép hai dòng
# --------------------------------------------------------------------------- #

def test_merge_zh_py_pairs_by_position() -> None:
    """Số cụm bằng nhau thì ghép theo vị trí, dấu câu lấy từ dòng Hán."""
    merged, matched = merge_zh_py(
        tokenize_line("好，唱得 真好"), tokenize_line("Hǎo，chàngdé zhēnhǎo")
    )
    assert matched is True
    assert [(t.kind, t.zh, t.pinyin) for t in merged] == [
        (KIND_WORD, "好", "Hǎo"),
        (KIND_PUNCT, "，", None),
        (KIND_WORD, "唱得", "chàngdé"),
        (KIND_WORD, "真好", "zhēnhǎo"),
    ]


def test_merge_zh_py_refuses_to_guess_when_counts_differ() -> None:
    """Lệch số cụm thì MỌI cụm về ``pinyin=None``, không vá từng chỗ.

    README cấm thẳng: "Tuyệt đối không được gán lại pinyin của cụm trước cho cụm
    sau khi ghép thất bại". Vá một phần nguy hiểm hơn bỏ trắng, vì nó tạo ra một
    file trông có vẻ đúng nhưng sai âm ở giữa — thứ không ai soi lại nữa.
    """
    merged, matched = merge_zh_py(
        tokenize_line("好，唱得 真好"), tokenize_line("Hǎo，zhēnhǎo")
    )
    assert matched is False
    assert all(t.pinyin is None for t in merged)
    assert [t.zh for t in merged] == ["好", "，", "唱得", "真好"]


# --------------------------------------------------------------------------- #
# dựng Document
# --------------------------------------------------------------------------- #

def test_parse_srt_document_reports_only_known_cum_mismatch(completed_text: str) -> None:
    """8 cue lệch số cụm của corpus, không thừa không thiếu cue nào.

    Con số này là chốt chặn hai chiều: thêm một cue nghĩa là bộ đọc vừa mất khả
    năng ghép một cue vốn ghép được; bớt một cue nghĩa là nó vừa bắt đầu đoán.
    """
    doc = parse_srt_document(completed_text)
    findings = doc.meta["parse_findings"]
    assert {f["code"] for f in findings} == {"CUM_MISMATCH"}
    assert sorted(f["cue_index"] for f in findings) == list(CUM_MISMATCH_CUES)


def test_parse_srt_document_keeps_file_numbering(completed_doc) -> None:
    """``Cue.index`` giữ số ghi trong file, không phải vị trí.

    Nếu tầng đọc âm thầm đánh số lại thì ``rules.INDEX_SEQ`` vĩnh viễn không bao
    giờ bắt được lỗi nào — nó sẽ luôn thấy một dãy hoàn hảo do chính mình tạo ra.
    """
    assert [c.index for c in completed_doc.cues] == list(range(1, CORPUS_CUES + 1))


def test_raw_srt_has_no_pinyin(raw_text: str) -> None:
    """``raw.srt`` không có dòng pinyin nên mọi word phải là ``pinyin=None``."""
    doc = parse_srt_document(raw_text)
    assert len(doc.cues) == CORPUS_CUES
    words = [t for c in doc.cues for t in c.tokens if t.kind == KIND_WORD]
    assert words and all(t.pinyin is None for t in words)


# --------------------------------------------------------------------------- #
# round-trip trên toàn corpus
# --------------------------------------------------------------------------- #

def test_round_trip_every_cue_of_completed(completed_doc) -> None:
    """1351/1351 cue: đọc → render → đọc lại phải ra đúng dãy token cũ.

    Đây là bài kiểm nặng nhất của file này. Nó chạy trên câu chữ thật, có đủ
    ngoặc kép, dấu lửng, marker và tên riêng — những thứ mà ca ngẫu nhiên ở
    ``test_renderer.py`` không dựng lại được một cách thuyết phục.
    """
    lệch = []
    for cue in completed_doc.cues:
        before = [(t.kind, t.zh) for t in cue.tokens if t.zh.strip()]
        after = [(t.kind, t.zh) for t in tokenize_line(render_zh(cue.tokens))]
        if before != after:
            lệch.append((cue.index, render_zh(cue.tokens)))
    assert not lệch, f"{len(lệch)} cue không round-trip được: {lệch[:5]}"


# --------------------------------------------------------------------------- #
# ghi file
# --------------------------------------------------------------------------- #

def test_emit_srt_shape(completed_doc) -> None:
    """4 dòng/block, đúng 1 dòng trống giữa các block, không dòng rỗng bên trong.

    Build-spec mục 11: với Aegisub, một dòng rỗng nghĩa là "hết block". Một dòng
    rỗng lọt vào giữa block làm hỏng file từ chỗ đó trở đi.
    """
    body = emit_srt(completed_doc)
    assert body.endswith("\n\n")
    blocks = [b for b in body.split("\n\n") if b.strip()]
    assert len(blocks) == CORPUS_CUES
    for block in blocks:
        lines = block.split("\n")
        assert len(lines) == 4, f"block sai hình dạng: {lines}"
        assert all(line.strip() for line in lines), f"dòng rỗng trong block: {lines}"


def test_emit_srt_renumbers_from_one() -> None:
    """Số thứ tự được đánh lại từ 1, không giữ số cũ của file vào.

    File tool giao lại phải thoả ``INDEX_SEQ`` kể cả khi file đọc vào thì không.
    """
    text = (
        "7\n00:00:01,000 --> 00:00:02,000\n好\nHǎo\n\n"
        "9\n00:00:02,000 --> 00:00:03,000\n好\nHǎo\n\n"
    )
    body = emit_srt(parse_srt_document(text))
    assert [line for line in body.split("\n") if line.isdigit()] == ["1", "2"]


def test_emit_then_parse_is_stable(completed_doc) -> None:
    """Chạy ``fix`` hai lần phải ra cùng một file.

    Một phép biến đổi không ổn định (lần 2 khác lần 1) nghĩa là mỗi lần chạy lại
    làm file trôi đi thêm — và người dùng sẽ chạy lại, vì UI có nút chạy lại.
    """
    once = emit_srt(completed_doc)
    twice = emit_srt(parse_srt_document(once))
    assert once == twice


def test_emit_srt_timestamps_match_pattern(completed_doc) -> None:
    """Mọi dòng thời gian ghi ra phải khớp ``TIMESTAMP_RE`` — không có ngoại lệ."""
    body = emit_srt(completed_doc)
    stamps = [line for line in body.split("\n") if "-->" in line]
    assert len(stamps) == CORPUS_CUES
    assert all(TIMESTAMP_RE.match(line) for line in stamps)
