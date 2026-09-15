"""Một hàm sửa file duy nhất (hợp đồng A) và file song ngữ Hán / pinyin / Việt.

Vì sao có file này
==================
Trước đợt này có **ba** bản của lệnh fix cho ba kết quả khác nhau: ``run_fix``
(CLI), ``server.fix_srt_text`` (web — không chạy jieba, biến điệu cả pinyin người
biên tập) và ``conftest.fix_srt_text`` (bộ test — lệch ``run_fix`` ở 15 cue
``bù``/``bú``). Cộng thêm: bộ đọc ghép dòng tiếng Việt vào dòng pinyin, cụm lệch
nhau, pinyin bị sinh lại, và câu tiếng Việt của bên dịch biến mất trong khi kết
quả báo 0 lỗi.

Các test dưới đây khoá:

1. ``run_fix_bilingual`` trả ``FixResult`` và ``run_fix`` chỉ là lớp vỏ của nó;
   fixture của bộ test ra đúng từng byte như ``run_fix``.
2. Dòng tiếng Việt đi ra ``vi_text`` (cùng số block, cùng mốc thời gian), dòng
   không xếp được đi ra ``set_aside`` kèm finding trích nguyên văn.
3. ``looks_like_pinyin`` kiểm bằng ÂM TIẾT, không bằng "chữ riêng của tiếng
   Việt": ``Bạn khỏe không?`` và ``Anh là ai?`` không bao giờ bị giữ làm pinyin,
   còn toàn bộ dòng pinyin của corpus thì được nhận.
4. Dòng Hán chỉ tính là đã phân cụm khi có khoảng trắng GIỮA HAI CHỮ HÁN.

Mọi test chạy ngoại tuyến (jieba, pypinyin là thư viện cục bộ).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from srtgen import io_utils
from srtgen.core.rules import SEVERITY_ERROR, validate_pair, validate_text
from srtgen.core.srt import (
    PINYIN_SYLLABLES,
    looks_like_pinyin,
    parse_srt,
    parse_srt_document,
    split_block_content,
    tokenize_line,
)

TRILINGUAL = """1
00:00:01,000 --> 00:00:02,500
你好 世界。
Nǐhǎo shìjiè。
Chào thế giới.

2
00:00:02,500 --> 00:00:04,000
我 来 中国 只有 一个 目的。
Wǒ lái Zhōngguó zhǐyǒu yígè mùdì。
Tôi đến Trung Quốc chỉ có một mục đích.
"""


def block(index: int, *lines: str) -> str:
    start = f"00:00:{index:02d},000"
    end = f"00:00:{index:02d},900"
    return "\n".join([str(index), f"{start} --> {end}", *lines]) + "\n\n"


def srt(*blocks: tuple[str, ...]) -> str:
    return "".join(block(i, *lines) for i, lines in enumerate(blocks, start=1))


def contents(text: str) -> list[list[str]]:
    return [b.lines for b in parse_srt(text)]


def words(line: str) -> list[str]:
    return [t.zh for t in tokenize_line(line, normalize=True) if t.kind == "word"]


def raw_content_lines(text: str) -> list[list[str]]:
    """Các dòng chữ của từng block, đọc thẳng từ văn bản — không qua ``parse_srt``.

    Cố ý không dùng bộ đọc của thư viện: phép đo nhận diện pinyin trên corpus
    phải độc lập với chính thứ đang được đo.
    """
    out: list[list[str]] = []
    for chunk in re.split(r"\n\s*\n", text):
        rows = [row for row in chunk.split("\n") if row.strip()]
        ts = next((k for k, row in enumerate(rows) if "-->" in row), None)
        if ts is not None:
            out.append(rows[ts + 1 :])
    return out


@pytest.fixture(scope="module")
def fix_bi(cfg: dict[str, Any]) -> Any:
    from srtgen.pipeline import run_fix_bilingual

    def _run(text: str) -> Any:
        return run_fix_bilingual(text, cfg)

    return _run


# --------------------------------------------------------------------------- #
# 1. Một hàm, một logic
# --------------------------------------------------------------------------- #

def test_fix_result_shape(fix_bi: Any) -> None:
    from srtgen.pipeline import FixResult

    result = fix_bi(TRILINGUAL)
    assert isinstance(result, FixResult)
    assert isinstance(result.text, str)
    assert isinstance(result.vi_text, str)
    assert isinstance(result.findings, list)
    assert isinstance(result.set_aside, list)
    assert result.has_vi is True


def test_run_fix_is_run_fix_bilingual(filter_text: str, cfg: dict[str, Any]) -> None:
    """``run_fix`` = ``run_fix_bilingual`` rút gọn, không có logic thứ hai."""
    from srtgen.pipeline import run_fix, run_fix_bilingual

    text, findings = run_fix(filter_text, cfg)
    result = run_fix_bilingual(filter_text, cfg)
    assert text == result.text
    assert [f.to_dict() for f in findings] == [f.to_dict() for f in result.findings]
    assert result.vi_text is None
    assert result.set_aside == []


def test_test_fixture_is_byte_identical_to_run_fix(
    fixed_filter_srt: str, fix_srt: Any, filter_text: str, cfg: dict[str, Any]
) -> None:
    """Fixture của bộ test phải đo đúng thứ người dùng nhận được.

    Bản chép tay cũ đặt ``segmented=True`` cho cả file và biến điệu cả pinyin
    người biên tập, nên lệch ``run_fix`` ở 15 cue ``bù``/``bú``.
    """
    from srtgen.core.srt import emit_srt
    from srtgen.pipeline import run_fix

    expected, _ = run_fix(filter_text, cfg)
    assert fixed_filter_srt == expected
    assert emit_srt(fix_srt(filter_text, cfg)) == expected


def test_editor_bu_is_not_sandhied_in_the_fixed_filter(
    fixed_filter_srt: str, filter_text: str
) -> None:
    """15 cue của filter.srt có ``不`` trước thanh 4 mà người biên tập ghi ``bù``.

    ``run_fix`` giữ ``bù`` (quyết định đã chốt); bản chép trong conftest từng đổi
    thành ``bú``. Nay fixture gọi ``run_fix`` nên số ``bú`` không được tăng.
    """
    def count_bu_rising(text: str) -> int:
        return len(re.findall(r"\b[Bb]ú", text))

    assert count_bu_rising(fixed_filter_srt) <= count_bu_rising(filter_text)


# --------------------------------------------------------------------------- #
# 2. File song ngữ
# --------------------------------------------------------------------------- #

def test_trilingual_block_keeps_the_vietnamese_line(fix_bi: Any) -> None:
    result = fix_bi(TRILINGUAL)

    zh_blocks = parse_srt(result.text)
    assert [b.raw_line_count for b in zh_blocks] == [4, 4]
    assert "Chào" not in result.text and "Tôi" not in result.text
    # Pinyin người biên tập không bị sinh lại, không bị biến điệu.
    assert "Nǐhǎo shìjiè。" in result.text
    assert "Wǒ lái Zhōngguó zhǐyǒu yígè mùdì。" in result.text

    vi_blocks = parse_srt(result.vi_text)
    assert [b.raw_line_count for b in vi_blocks] == [3, 3]
    assert [b.lines for b in vi_blocks] == [
        ["Chào thế giới."],
        ["Tôi đến Trung Quốc chỉ có một mục đích."],
    ]
    # Cùng số block, cùng mốc thời gian từng ký tự.
    assert [b.timestamp_text for b in vi_blocks] == [b.timestamp_text for b in zh_blocks]
    assert [f for f in validate_pair(result.text, result.vi_text) if f.severity == "error"] == []
    assert result.set_aside == []
    assert [f for f in result.findings if f.severity == SEVERITY_ERROR] == []
    assert result.stats["vi_cues"] == 2


@pytest.mark.parametrize(
    ("zh", "second", "pinyin"),
    [
        # Số từ tiếng Việt TÌNH CỜ bằng số cụm Hán: trước đây bị giữ làm pinyin
        # và ra "Bạn khỏe không？" với 0 lỗi.
        ("你 好 吗？", "Bạn khỏe không?", "Nǐ hǎo ma？"),
        # Không có chữ "riêng của tiếng Việt" nào đủ mạnh: trước đây lọt.
        ("你 是 谁？", "Anh là ai?", "Nǐ shì shéi？"),
        # Mọi từ đều đánh vần được như pinyin (la, ai) — chỉ phép so phát âm bắt được.
        ("是 谁？", "Là ai?", "Shì shéi？"),
    ],
)
def test_two_line_chinese_vietnamese_block(fix_bi: Any, zh: str, second: str, pinyin: str) -> None:
    result = fix_bi(srt((zh, second)))
    assert contents(result.text) == [[zh, pinyin]]
    assert contents(result.vi_text) == [[second]]
    assert second not in result.text


def test_plain_four_line_file_has_no_vietnamese(fix_bi: Any) -> None:
    result = fix_bi(srt(("你好 世界。", "Nǐhǎo shìjiè。")))
    assert result.vi_text is None
    assert result.has_vi is False
    assert result.set_aside == []


def test_block_without_vietnamese_keeps_both_files_aligned(fix_bi: Any) -> None:
    result = fix_bi(TRILINGUAL.replace("Chào thế giới.\n", ""))
    assert len(parse_srt(result.vi_text)) == len(parse_srt(result.text)) == 2
    empty = [f for f in result.findings if f.code == "VI_EMPTY_LINE"]
    assert [f.cue_index for f in empty] == [1]
    assert empty[0].severity == "warn"


def test_pinyin_line_after_vietnamese_is_set_aside_not_dropped(fix_bi: Any) -> None:
    stray = "Wǒ lái Zhōngguó"
    text = TRILINGUAL.replace(
        "Tôi đến Trung Quốc chỉ có một mục đích.\n",
        f"Tôi đến Trung Quốc chỉ có một mục đích.\n{stray}\n",
    )
    result = fix_bi(text)
    assert result.set_aside == [stray]
    kept = [f for f in result.findings if f.code == "BLOCK_SHAPE" and f.line == stray]
    assert kept and kept[0].severity == SEVERITY_ERROR and stray in kept[0].message
    assert "Tôi đến Trung Quốc chỉ có một mục đích." in result.vi_text


def test_text_before_the_first_block_is_set_aside(fix_bi: Any) -> None:
    result = fix_bi("Phụ đề bởi nhóm ABC\n\n" + srt(("你好。", "Nǐhǎo。")))
    assert result.set_aside == ["Phụ đề bởi nhóm ABC"]
    (finding,) = [f for f in result.findings if f.code == "BLOCK_SHAPE"]
    assert finding.cue_index is None and "Phụ đề bởi nhóm ABC" in finding.message


def test_junk_line_before_the_cue_number_is_set_aside(fix_bi: Any) -> None:
    text = "ghi chú của người dịch\n1\n00:00:01,000 --> 00:00:02,000\n你好。\nNǐhǎo。\n"
    result = fix_bi(text)
    assert result.set_aside == ["ghi chú của người dịch"]
    assert contents(result.text) == [["你好。", "Nǐhǎo。"]]


def test_second_chinese_line_is_joined_not_lost(fix_bi: Any) -> None:
    """Hai dòng Hán, không có pinyin: dòng thứ hai được ghép vào dòng Hán.

    Trước đây dòng thứ hai biến mất không lời nào; bản sửa kế đó đẩy nó ra
    ``set_aside`` — có báo, nhưng chữ vẫn không có trong file kết quả. Quyết định
    đợt "không mất bản sửa tay": các dòng Hán liên tiếp là MỘT dòng Hán, nối bằng
    một khoảng trắng (xem ``srt.split_block_content``).
    """
    result = fix_bi(srt(("我 来 中国，", "只有 一个 目的。")))
    assert contents(result.text)[0][0] == "我 来 中国，只有 一个 目的。"
    assert result.set_aside == []
    assert not [f for f in result.findings if f.code == "BLOCK_SHAPE"]


def test_run_fix_says_the_vietnamese_lines_are_not_in_its_text(cfg: dict[str, Any]) -> None:
    """Chữ ký cũ không mang được ``_vi.srt``: phải NÓI RA, không được im lặng."""
    from srtgen.pipeline import run_fix, run_fix_bilingual

    text, findings = run_fix(TRILINGUAL, cfg)
    assert text == run_fix_bilingual(TRILINGUAL, cfg).text
    warned = [f for f in findings if f.code == "BLOCK_SHAPE" and "tiếng Việt" in f.message]
    assert warned and warned[0].severity == SEVERITY_ERROR


def test_parse_document_carries_vi_lines_by_position() -> None:
    doc = parse_srt_document(TRILINGUAL.replace("Chào thế giới.\n", ""))
    assert doc.meta["vi_lines"] == [None, "Tôi đến Trung Quốc chỉ có một mục đích."]
    assert doc.meta["set_aside"] == []


@pytest.mark.parametrize(
    ("content", "lines", "vi", "aside"),
    [
        (["好", "Hǎo"], ["好", "Hǎo"], None, []),
        (["好", "Tốt"], ["好"], "Tốt", []),
        (["好", "Hǎo", "Tốt"], ["好", "Hǎo"], "Tốt", []),
        # pinyin bị ngắt làm hai dòng, rồi tiếng Việt cũng hai dòng
        (["我 来", "Wǒ", "lái", "Tôi", "đến"], ["我 来", "Wǒ lái"], "Tôi đến", []),
        # pinyin đứng trên dòng Hán
        (["Hǎo", "好"], ["好", "Hǎo"], None, []),
        # hai người nói viết xen kẽ Hán / pinyin / Việt
        (["好", "Hǎo", "Tốt", "走", "Zǒu", "Đi"], ["好 走", "Hǎo Zǒu"], "Tốt Đi", []),
        # không có chữ Hán (một block của file _vi.srt): không đụng
        (["Tôi đến", "Trung Quốc"], ["Tôi đến", "Trung Quốc"], None, []),
    ],
)
def test_split_block_content(
    content: list[str], lines: list[str], vi: str | None, aside: list[str]
) -> None:
    result = split_block_content(content)
    assert result.lines == lines
    assert result.vi_line == vi
    assert [text for text, _why in result.set_aside] == aside


# --------------------------------------------------------------------------- #
# 3. looks_like_pinyin — kiểm âm tiết
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("line", "han"),
    [
        ("Nǐ hǎo ma？", "你 好 吗？"),
        ("nǚ péngyou", "女 朋友"),
        ("nv pengyou", "女 朋友"),          # ü gõ bằng v
        ("Xī'ān", "西安"),
        ("zhù nǎr", "住 哪儿"),
        ("Wǒ ne yíhuìr", "我 呢 一会儿"),
        ("nǎér", "哪儿"),                     # 儿化音 viết đầy đủ vẫn là pinyin
        ("ǹg", "嗯"),
        ("Ń？", "嗯？"),
        ("Wǒmen qù KTV chànggē ba。", "我们 去 KTV 唱歌 吧。"),
        ("OK", "OK 吧"),
        ("thanks", "Thanks"),
        ("Shì shéi？", "是 谁？"),
        ("de", "的"),
        ("Dāng báǐlǐng zài suànfǎ", "当 白领 在 算法"),  # lỗi gõ thật của corpus/pairs
        ("2024 nián", "2024 年"),
        ("……", "……"),
        ("<i>Tā shuō hǎo。</i>", "<i>他 说 好。</i>"),
    ],
)
def test_pinyin_lines_are_recognised(line: str, han: str) -> None:
    assert looks_like_pinyin(line, han) is True


@pytest.mark.parametrize(
    ("line", "han"),
    [
        ("Bạn khỏe không?", "你 好 吗？"),
        ("Anh là ai?", "你 是 谁？"),
        ("Là ai?", "是 谁？"),
        ("Ai?", "谁？"),
        ("Chào thế giới.", "你好 世界。"),
        ("Thanks", None),                    # chữ Latin không có mặt ở dòng Hán
        ("你 好", None),                     # chữ Hán không bao giờ là pinyin
        ("Tập 1: Hàng xóm mới", "第一集：新 邻居"),
    ],
)
def test_non_pinyin_lines_are_rejected(line: str, han: str | None) -> None:
    assert looks_like_pinyin(line, han) is False


@pytest.mark.parametrize("word", ["anh", "bạn", "khỏe", "không", "đi", "được", "người"])
def test_vietnamese_words_are_not_pinyin(word: str) -> None:
    assert looks_like_pinyin(word) is False


def test_syllable_table_covers_everything_pypinyin_can_write() -> None:
    """Bảng âm tiết đóng băng trong srt.py không được thiếu âm nào pypinyin sinh ra."""
    pinyin_dict = pytest.importorskip("pypinyin.pinyin_dict").pinyin_dict
    import unicodedata

    def bare(reading: str) -> str:
        nfd = unicodedata.normalize("NFD", reading)
        kept = "".join(c for c in nfd if unicodedata.category(c) != "Mn" or c == "̈")
        return unicodedata.normalize("NFC", kept)

    produced = {bare(r) for value in pinyin_dict.values() for r in value.split(",")}
    assert produced <= PINYIN_SYLLABLES, sorted(produced - PINYIN_SYLLABLES)[:20]
    assert 400 <= len(PINYIN_SYLLABLES) <= 440


@pytest.mark.parametrize("name", ["completed", "filter"])
def test_every_corpus_pinyin_line_is_recognised(corpus_dir: Path, name: str) -> None:
    """1351/1351 dòng pinyin của completed.srt và của filter.srt được nhận là pinyin."""
    rows = raw_content_lines(io_utils.read_text(corpus_dir / f"{name}.srt"))
    assert len(rows) == 1351
    rejected = [(zh, py) for zh, py in rows if not looks_like_pinyin(py, zh)]
    assert rejected == []


def test_corpus_pairs_pinyin_lines_are_recognised(corpus_dir: Path) -> None:
    for name in ("BonnieBears_tap1", "news_myiran", "tintuc_ai"):
        rows = raw_content_lines(io_utils.read_text(corpus_dir / "pairs" / f"{name}.srt"))
        rejected = [r for r in rows if len(r) > 1 and not looks_like_pinyin(r[1], r[0])]
        assert rejected == [], (name, rejected)


def test_corpus_vietnamese_files_are_not_pinyin(corpus_dir: Path) -> None:
    """3 file ``_vi.srt`` thật: không dòng nào được nhận là pinyin khi đặt cạnh dòng Hán.

    Không có dòng Hán làm đối chiếu thì vẫn còn đúng 3/603 dòng đánh vần được như
    pinyin (``Ai?``, ``Làn sóng AI,`` x2) — lý do phép so phát âm tồn tại.
    """
    total = accepted = accepted_blind = 0
    for name in ("BonnieBears_tap1", "news_myiran", "tintuc_ai"):
        zh_rows = raw_content_lines(io_utils.read_text(corpus_dir / "pairs" / f"{name}.srt"))
        vi_rows = raw_content_lines(io_utils.read_text(corpus_dir / "pairs" / f"{name}_vi.srt"))
        assert len(zh_rows) == len(vi_rows)
        for zh, vi in zip(zh_rows, vi_rows):
            for line in vi:
                total += 1
                accepted += looks_like_pinyin(line, zh[0])
                accepted_blind += looks_like_pinyin(line)
    assert total == 603
    assert accepted == 0
    assert accepted_blind <= 3


def test_real_pair_merged_into_one_trilingual_file_splits_back(
    corpus_dir: Path, cfg: dict[str, Any]
) -> None:
    """Ghép cặp file thật (news_myiran) thành file ba dòng rồi sửa: tách lại đủ 187 câu."""
    from srtgen.pipeline import run_fix_bilingual

    zh_text = io_utils.read_text(corpus_dir / "pairs" / "news_myiran.srt")
    vi_text = io_utils.read_text(corpus_dir / "pairs" / "news_myiran_vi.srt")
    zh_blocks, vi_blocks = parse_srt(zh_text), parse_srt(vi_text)
    merged = "".join(
        f"{i}\n{z.timestamp_text}\n" + "\n".join(z.lines + v.lines) + "\n\n"
        for i, (z, v) in enumerate(zip(zh_blocks, vi_blocks), start=1)
    )
    result = run_fix_bilingual(merged, cfg)
    assert result.set_aside == []
    assert len(parse_srt(result.vi_text)) == len(parse_srt(result.text)) == 187
    assert [b.lines[0] for b in parse_srt(result.vi_text)] == [
        " ".join(line.strip() for line in v.lines) for v in vi_blocks
    ]
    assert [f for f in validate_text(result.text) if f.severity == SEVERITY_ERROR] == []


# --------------------------------------------------------------------------- #
# 4. Khoảng trắng quanh chữ Latin không phải dấu vết phân cụm
# --------------------------------------------------------------------------- #

def test_has_editor_spaces_only_counts_space_between_two_han() -> None:
    from srtgen.pipeline import _has_editor_spaces

    def spaced(line: str) -> bool:
        return _has_editor_spaces(tokenize_line(line))

    assert spaced("我们 去 KTV 唱歌吧。")
    assert spaced("好 啊")
    assert not spaced("我们去 KTV 唱歌吧。")
    assert not spaced("OK 吧")
    assert not spaced("好，走吧。")
    assert not spaced("说<i>好</i>")


def test_whisper_line_with_latin_is_segmented_and_keeps_the_latin_block(fix_bi: Any) -> None:
    """``我们去 KTV 唱歌吧。`` (Whisper) trước đây ra ``Wǒmenqù KTV chànggēba``."""
    result = fix_bi(srt(("我们去 KTV 唱歌吧。",)))
    [[zh, py]] = contents(result.text)
    assert "KTV" in words(zh) and "KTV" in words(py)
    assert len(words(zh)) > 3
    assert "".join(words(zh)) == "我们去KTV唱歌吧"
    assert "Wǒmenqù" not in py and "chànggēba" not in py
    assert len(words(zh)) == len(words(py))
    assert [f for f in validate_text(result.text) if f.severity == SEVERITY_ERROR] == []


def test_line_with_space_between_han_keeps_its_boundaries(fix_bi: Any) -> None:
    result = fix_bi(srt(("我们 去 KTV 唱歌吧。",)))
    [[zh, _py]] = contents(result.text)
    assert words(zh) == ["我们", "去", "KTV", "唱歌吧"]
