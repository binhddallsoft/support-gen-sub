"""Khoá hành vi của ``srtgen fix`` (``pipeline.run_fix``) đối với công của người biên tập.

Vì sao có file này
==================
Trước đợt sửa này ``run_fix`` cho jieba cắt lại mọi dòng Hán và sinh đè toàn bộ
pinyin: 16/1351 cue của ``corpus/filter.srt`` bị đổi ranh giới cụm (``接住`` ->
``接 住``, ``哪种`` -> ``哪 种``, ``翻到`` -> ``翻 到``) và mọi chữ người biên tập
ghi ở dòng pinyin bị thay bằng chữ máy đoán. Bộ cổng vẫn xanh suốt thời gian đó,
vì cổng chỉ hỏi "file ra có đúng định dạng không" — một file bị cắt lại vẫn đúng
định dạng. Không test nào hỏi "file ra có còn là file của người biên tập không".

README (mục backend) là hợp đồng: "Backend phải coi khoảng trắng trong file SRT
là ranh giới cụm từ đã được biên tập; không được tách tiếp Chinese". Quyết định
đã chốt, **theo từng cue**:

* dòng Hán đã phân cụm -> giữ nguyên ranh giới;
* dòng Hán chưa phân cụm (chữ dính liền kiểu Whisper) -> jieba cắt như cũ;
* có dòng pinyin khớp số cụm -> giữ pinyin người biên tập, chỉ chuẩn hoá
  儿化音 / dấu câu / viết hoa / tên riêng;
* không có dòng pinyin hoặc lệch số cụm -> sinh lại pinyin cho cue đó.

Kèm theo: thẻ định dạng SRT (``<i>`` …) phải đi qua ``fix`` nguyên văn, không
thành ``《i》``.

Mọi test ở đây chạy ngoại tuyến: jieba và pypinyin là thư viện cục bộ, không có
mạng, không có model, không có API key.
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from srtgen.core.erhua import is_erhua_word, is_full_er_form
from srtgen.core.rules import SEVERITY_ERROR, validate_text
from srtgen.core.srt import KIND_MARKUP, parse_srt, tokenize_line

TS = "00:00:01,000 --> 00:00:02,000"


# --------------------------------------------------------------------------- #
# tiện ích
# --------------------------------------------------------------------------- #

def block(index: int, *lines: str) -> str:
    """Một block SRT: số thứ tự, mốc thời gian (tăng dần theo số), rồi các dòng chữ."""
    start = f"00:00:{index:02d},000"
    end = f"00:00:{index:02d},900"
    return "\n".join([str(index), f"{start} --> {end}", *lines]) + "\n\n"


def srt(*blocks: tuple[str, ...]) -> str:
    return "".join(block(i, *lines) for i, lines in enumerate(blocks, start=1))


def words(line: str) -> list[str]:
    """Các cụm chữ của một dòng (bỏ dấu câu, marker, thẻ) — đúng cách README đếm cụm."""
    return [t.zh for t in tokenize_line(line, normalize=True) if t.kind == "word"]


def cue_lines(text: str) -> list[tuple[str, str]]:
    """(dòng Hán, dòng pinyin) của từng cue trong một file."""
    return [tuple((b.lines + ["", ""])[:2]) for b in parse_srt(text)]  # type: ignore[misc]


def errors_of(text: str) -> list[Any]:
    return [f for f in validate_text(text) if f.severity == SEVERITY_ERROR]


@pytest.fixture(scope="module")
def fix(cfg: dict[str, Any]) -> Callable[[str], list[tuple[str, str]]]:
    """``run_fix`` rồi trả về (Hán, pinyin) từng cue — và bắt buộc file ra sạch lỗi.

    Kiểm lỗi ngay trong fixture để mọi test dưới đây đồng thời là một cổng nhỏ:
    giữ công người biên tập mà làm file ra hỏng định dạng thì cũng là hỏng.
    """
    from srtgen.pipeline import run_fix

    def _fix(text: str) -> list[tuple[str, str]]:
        fixed, _ = run_fix(text, cfg)
        bad = errors_of(fixed)
        assert not bad, [f"cue {f.cue_index}: [{f.code}] {f.message}" for f in bad]
        return cue_lines(fixed)

    return _fix


@pytest.fixture(scope="module")
def fixed_filter_via_run_fix(filter_text: str, cfg: dict[str, Any]) -> str:
    """``run_fix(corpus/filter.srt)`` — chạy một lần cho cả file test này."""
    from srtgen.pipeline import run_fix

    fixed, _ = run_fix(filter_text, cfg)
    return fixed


# --------------------------------------------------------------------------- #
# 1. Dòng đã phân cụm: giữ nguyên ranh giới
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("zh", "py"),
    [
        ("接住 了 啊。", "jiēzhù le a。"),
        ("那 是 哪种 男人？", "nà shì nǎzhǒng nánrén？"),
        ("请 同学 们 把 书 翻到 第三十五 页。", "qǐng tóngxué men bǎ shū fāndào dìsānshíwǔ yè。"),
        ("我 不来。", "wǒ bùlái。"),
    ],
)
def test_segmented_line_keeps_editor_boundaries(fix: Callable, zh: str, py: str) -> None:
    """Bốn cue thật của filter.srt mà jieba từng cắt lại (接住 -> 接 住 …)."""
    [(zh_out, py_out)] = fix(srt((zh, py)))
    assert zh_out == zh
    assert words(zh_out) == words(zh)
    assert [w.lower() for w in words(py_out)] == [w.lower() for w in words(py)]


def test_run_fix_changes_no_boundary_of_filter_srt(
    fixed_filter_via_run_fix: str, filter_text: str
) -> None:
    """Cả 1351 cue của filter.srt: không cue nào bị đổi ranh giới cụm (trước: 16)."""
    before = cue_lines(filter_text)
    after = cue_lines(fixed_filter_via_run_fix)
    assert len(after) == len(before)
    moved = [
        (i, old[0], new[0])
        for i, (old, new) in enumerate(zip(before, after), start=1)
        if words(old[0]) != words(new[0])
    ]
    assert not moved, moved[:10]


def test_edited_file_never_loads_the_segmenter(
    filter_text: str, cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """File đã biên tập xong (mọi cue có pinyin khớp) không được đưa cho jieba.

    Đây là cách chắc nhất để nói "không cue nào bị cắt lại": nếu bộ tách từ không
    hề được dựng thì không có gì để cắt. Kèm lợi ích thật trên iMac 2017 — khỏi
    mất vài giây dựng từ điển jieba cho một file không cần đến nó.
    """
    import srtgen.stages.s5_tokenize as s5
    from srtgen.pipeline import run_fix

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("jieba bị gọi cho một file đã phân cụm sẵn")

    monkeypatch.setattr(s5, "make_segmenter", boom)
    fixed, findings = run_fix(filter_text, cfg)
    assert not [f for f in findings if f.severity == SEVERITY_ERROR]
    assert len(parse_srt(fixed)) == len(parse_srt(filter_text))


def test_decision_is_per_cue_not_per_file(fix: Callable) -> None:
    """Một file nửa đã biên tập, nửa chữ thô: mỗi cue được xử theo đúng nhóm của nó."""
    out = fix(
        srt(
            ("接住 了 啊。", "jiēzhù le a。"),      # đã phân cụm + pinyin khớp
            ("我来中国只有一个目的。",),              # chữ thô, không pinyin
        )
    )
    assert out[0][0] == "接住 了 啊。"
    assert words(out[1][0]) != ["我来中国只有一个目的"]
    assert len(words(out[1][0])) > 1


# --------------------------------------------------------------------------- #
# 2. Pinyin khớp số cụm: giữ chữ của người biên tập
# --------------------------------------------------------------------------- #

def test_matching_pinyin_is_kept_not_regenerated(fix: Callable) -> None:
    """``shuí`` do người biên tập ghi được giữ (lựa chọn biên tập, chỉ là cảnh báo).

    Bảng ưu tiên đọc 谁 -> ``shéi`` chỉ áp khi tool SINH pinyin; áp lên chữ đã có
    là ghi đè người biên tập, đúng thứ quyết định này cấm.
    """
    [(zh, py)] = fix(srt(("谁 让 你 来 的？", "shuí ràng nǐ lái de？")))
    assert zh == "谁 让 你 来 的？"
    assert py == "Shuí ràng nǐ lái de？"   # chỉ đổi viết hoa đầu câu


def test_editor_tone_is_not_rewritten_by_sandhi(fix: Callable) -> None:
    """``bù`` người biên tập ghi không bị biến điệu thành ``bú``.

    Đo trên filter.srt: 15 cue có ``不`` đứng trước thanh 4; bản soát tay
    completed.srt giữ ``bù`` ở 10 cue và không cue nào viết ``bú`` như biến điệu
    sẽ ra. Biến điệu vẫn chạy cho pinyin do tool sinh (xem test kế tiếp).
    """
    [(_, py)] = fix(srt(("他 不 是。", "tā bù shì。")))
    assert py == "Tā bù shì。"


def test_generated_pinyin_still_gets_reading_prefs_and_sandhi(fix: Callable) -> None:
    """Dòng đã phân cụm nhưng KHÔNG có pinyin: giữ ranh giới, sinh pinyin mới."""
    out = fix(srt(("谁 来 了？",), ("他 不 是。",)))
    assert out[0] == ("谁 来 了？", "Shéi lái le？")
    assert out[1] == ("他 不 是。", "Tā bú shì。")


def test_mismatched_pinyin_is_regenerated_on_kept_boundaries(fix: Callable) -> None:
    """Lệch số cụm (3 cụm Hán, 2 cụm pinyin): không đoán, sinh lại cho cả cue."""
    [(zh, py)] = fix(srt(("谁 来 了？", "shuí lái？")))
    assert zh == "谁 来 了？"
    assert py == "Shéi lái le？"


@pytest.mark.parametrize(
    ("zh", "second", "joined"),
    [
        # dòng Hán dài bị ngắt làm hai dòng
        ("我 来 中国，", "只有 一个 目的。", "我 来 中国，只有 一个 目的。"),
        # hai dòng Hán khác nhau, không dấu câu giữa chừng
        ("我 来", "中国。", "我 来 中国。"),
    ],
)
def test_second_line_with_han_is_not_kept_as_pinyin(
    fix: Callable, zh: str, second: str, joined: str
) -> None:
    """Dòng Hán thứ hai KHÔNG BAO GIỜ thành dòng pinyin — nó được ghép vào dòng Hán.

    Giữ nó làm pinyin thì file ra có chữ Hán ở dòng pinyin mà cổng vẫn báo 0 lỗi.

    Đã sửa theo quyết định đợt này (test cũ khẳng định dòng Hán ra CHỈ còn dòng
    đầu, tức là khẳng định đúng hành vi mất chữ): các dòng Hán liên tiếp là MỘT
    dòng Hán — phụ đề bị xuống dòng — nối bằng một khoảng trắng. Trước đây dòng
    thứ hai bị đẩy ra ``set_aside``, nghĩa là chữ của nó biến khỏi file kết quả.
    """
    [(zh_out, py_out)] = fix(srt((zh, second)))
    assert zh_out == joined
    assert words(zh_out) == words(zh) + words(second)   # không mất một cụm nào
    assert not any("一" <= ch <= "鿿" for ch in py_out), py_out
    assert len(words(py_out)) == len(words(zh_out))


def test_han_copy_in_pinyin_slot_is_kept_once_not_doubled(
    cfg: dict[str, Any]
) -> None:
    """Hai dòng Hán GIỐNG HỆT nhau là một câu, không phải phụ đề xuống dòng.

    Đó đúng là thứ ``emit_srt`` ghi ra khi một cue chưa có pinyin: renderer chép
    chữ Hán vào chỗ dòng pinyin (cue 2 của completed.srt lệch số cụm là một ví
    dụ thật). Ghép hai dòng đó lại thì mỗi lần đọc lại lời thoại bị nhân đôi —
    ``fix`` chạy hai lần ra hai file khác nhau. Giữ một lần không mất chữ nào:
    dòng bỏ đi trùng từng chữ với dòng giữ lại. Dòng pinyin vẫn không bao giờ
    là chữ Hán sau khi sửa.
    """
    from srtgen.core.srt import emit_srt, parse_srt_document
    from srtgen.pipeline import run_fix_bilingual

    zh_line = "十 秒钟 后 建设 我 的 王国。"
    doc = parse_srt_document(srt((zh_line, "Shímiǎozhōng hòu jiànshè wǒ de wángguó。")))
    once = emit_srt(doc)
    assert once.splitlines()[2:4] == [zh_line, zh_line]          # đúng hình dạng fallback
    assert parse_srt(once)[0].lines == [zh_line]
    assert emit_srt(parse_srt_document(once)) == once       # đọc lại không trôi

    fixed = run_fix_bilingual(once, cfg)
    assert fixed.set_aside == []
    [(zh, py)] = cue_lines(fixed.text)
    assert zh == zh_line
    assert not any("一" <= ch <= "鿿" for ch in py), py
    assert len(words(py)) == len(words(zh))
    assert run_fix_bilingual(fixed.text, cfg).text == fixed.text


def test_two_speaker_lines_join_into_one_marker_line(fix: Callable) -> None:
    """``- 你 好。`` / ``- 我 好！`` (quy ước SRT hai người nói) → ``- 你 好。 - 我 好！``.

    Ghép lại ra đúng dạng một dòng ``- A - B`` mà README quy định: marker đầu
    dòng, rồi đúng 1 khoảng trắng giữa dấu câu và marker thứ hai.
    """
    [(zh, py)] = fix(srt(("- 你 好。", "- 我 好！")))
    assert zh == "- 你 好。 - 我 好！"
    assert py == "- Nǐ hǎo。 - Wǒ hǎo！"


@pytest.mark.parametrize(
    "lines",
    [
        ("我 来", "中国。"),
        ("我 来", "中国", "只有 一个 目的。"),
        ("我", "来", "中国", "了。"),
    ],
)
def test_no_han_line_is_set_aside_or_lost(cfg: dict[str, Any], lines: tuple[str, ...]) -> None:
    """Hai, ba, bốn dòng Hán: mọi chữ vào dòng Hán, không dòng nào bị để riêng.

    Ba dòng Hán trở lên trước đây làm bộ đọc nổ ``StopIteration`` — cả lệnh sửa
    file chết, người dùng không nhận được file nào; hai dòng thì dòng thứ hai bị
    đẩy ra ``set_aside``. Nay một luật cho mọi số dòng.
    """
    from srtgen.pipeline import run_fix_bilingual

    result = run_fix_bilingual(srt(lines), cfg)
    assert result.set_aside == []
    assert not errors_of(result.text)
    [(zh, py)] = cue_lines(result.text)
    assert "".join(words(zh)) == "".join("".join(words(line)) for line in lines)
    assert not any("一" <= ch <= "鿿" for ch in py), py
    assert len(words(py)) == len(words(zh))


@pytest.mark.parametrize(
    ("lines", "want"),
    [
        (["- 你 好。", "- 我 好！"], "- 你 好。 - 我 好！"),
        (["我 来", " 中国 ", "目的。"], "我 来 中国 目的。"),
    ],
)
def test_split_block_content_joins_consecutive_han_lines(lines: list[str], want: str) -> None:
    """Ở tầng bộ đọc: nối bằng đúng MỘT khoảng trắng, khoảng trắng thừa hai đầu bị cắt."""
    from srtgen.core.srt import split_block_content

    got = split_block_content(lines)
    assert got.lines == [want]
    assert got.set_aside == []
    assert got.vi_line is None


def test_unspaced_line_with_matching_pinyin_is_kept(fix: Callable) -> None:
    """Không có khoảng trắng nhưng dòng pinyin ghép khớp từng cụm: vẫn là công biên tập.

    ``美国——纽约`` / ``měiguó——niǔyuē``: dấu ngắt lời chia hai cụm, và dòng pinyin
    xác nhận đúng hai cụm ấy. Cắt lại ở đây là vứt pinyin của người biên tập.
    """
    [(zh, py)] = fix(srt(("美国——纽约", "měiguó——niǔyuē")))
    assert zh == "美国——纽约"
    assert py.lower() == "měiguó——niǔyuē"


# --------------------------------------------------------------------------- #
# 3. 儿化音 viết đầy đủ vẫn được rút gọn dù giữ pinyin người biên tập
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("zh", "py", "want"),
    [
        ("你 跑 哪儿 去 了？", "nǐ pǎo nǎér qù le？", "Nǐ pǎo nǎr qù le？"),
        ("对，就是 这儿。", "duì，jiùshì zhèér。", "Duì，jiùshì zhèr。"),
        ("我 呢 一会儿", "wǒ ne yīhuìer", "Wǒ ne yíhuìr"),
    ],
)
def test_full_erhua_is_contracted_on_kept_pinyin(
    fix: Callable, zh: str, py: str, want: str
) -> None:
    [(zh_out, py_out)] = fix(srt((zh, py)))
    assert zh_out == zh
    assert py_out == want


def test_no_full_erhua_left_in_fixed_filter_srt(fixed_filter_via_run_fix: str) -> None:
    """filter.srt có 19 cue viết 儿化音 đầy đủ (``nǎér``); không cue nào được sót lại."""
    left: list[tuple[int, str, str]] = []
    contracted = 0
    for i, (zh, py) in enumerate(cue_lines(fixed_filter_via_run_fix), start=1):
        zh_words, py_words = words(zh), words(py)
        assert len(zh_words) == len(py_words), (i, zh, py)
        for han, pin in zip(zh_words, py_words):
            if not is_erhua_word(han):
                continue
            contracted += 1
            if is_full_er_form(pin):
                left.append((i, han, pin))
    assert not left, left
    assert contracted >= 19


# --------------------------------------------------------------------------- #
# 4. Dòng chưa phân cụm vẫn được cắt
# --------------------------------------------------------------------------- #

def test_unsegmented_line_without_pinyin_is_cut(fix: Callable) -> None:
    """Block 3 dòng chữ dính liền (như raw.srt): jieba cắt, pinyin sinh mới, khớp số cụm."""
    [(zh, py)] = fix(srt(("我来中国只有一个目的。",)))
    assert len(words(zh)) > 1
    assert len(words(zh)) == len(words(py))
    assert "".join(words(zh)) == "我来中国只有一个目的"


def test_unsegmented_line_with_mismatched_pinyin_is_cut(fix: Callable) -> None:
    """Chữ dính liền + pinyin lệch số cụm: không có ranh giới nào đáng giữ -> cắt lại."""
    [(zh, py)] = fix(srt(("我来中国只有一个目的。", "wǒ lái zhōngguó。")))
    assert len(words(zh)) > 1
    assert len(words(zh)) == len(words(py))
    assert py != "Wǒ lái zhōngguó。"


def test_raw_corpus_is_still_segmented(raw_text: str, cfg: dict[str, Any]) -> None:
    """raw.srt (chữ Whisper dính liền) vẫn đi qua jieba: phần lớn cue ra nhiều cụm."""
    from srtgen.pipeline import run_fix

    fixed, findings = run_fix(raw_text, cfg)
    assert not [f for f in findings if f.severity == SEVERITY_ERROR]
    lines = cue_lines(fixed)
    multi = sum(1 for zh, _ in lines if len(words(zh)) > 1)
    assert multi > len(lines) // 2


# --------------------------------------------------------------------------- #
# 5. Thẻ định dạng SRT đi qua nguyên văn
# --------------------------------------------------------------------------- #

def test_italic_tag_survives_run_fix(fix: Callable) -> None:
    """``<i>他 说 好。</i>`` không được thành ``《i》他 说 好。《/ i》``."""
    [(zh, py)] = fix(srt(("<i>他 说 好。</i>", "<i>tā shuō hǎo。</i>")))
    assert zh == "<i>他 说 好。</i>"
    assert py == "<i>Tā shuō hǎo。</i>"


def test_italic_tag_survives_on_unsegmented_line(fix: Callable) -> None:
    """Đường jieba cũng phải giữ thẻ: thẻ không bị cắt vụn, không bị viết hoa."""
    [(zh, py)] = fix(srt(("<i>他说好。</i>",)))
    assert zh.startswith("<i>") and zh.endswith("</i>")
    assert py.startswith("<i>") and py.endswith("</i>")
    assert "《" not in zh + py and "<I>" not in py


def test_font_tag_quotes_are_not_smart_quoted(fix: Callable) -> None:
    """Dấu ``"`` TRONG thẻ giữ nguyên; dấu ``"`` trong lời thoại vẫn thành “”."""
    tag = '<font color="#ff0000">'
    [(zh, py)] = fix(srt((f'{tag}他 说："好"</font>', f'{tag}tā shuō："hǎo"</font>')))
    assert zh == f"{tag}他 说：“好”</font>"
    assert py == f"{tag}Tā shuō：“hǎo”</font>"


def test_speaker_marker_inside_italic_stays_a_marker(fix: Callable) -> None:
    """``<i>- 你 好。</i>``: ``-`` sau thẻ vẫn là marker, không bị đổi thành ``——``.

    Hợp đồng C (đợt sửa file song ngữ): marker ngay sau thẻ mở ở đầu dòng vẫn
    tính là đầu dòng, nên dạng đúng là ``<i>- 你 好。 - 再见。</i>`` — không phải
    ``<i> - 你 …`` như renderer cũ viết ra.

    Đi qua fixture ``fix``, tức là đòi validator sạch lỗi HOÀN TOÀN. Trước đây
    ``rules._check_marker_spacing`` không coi "ngay sau thẻ ở đầu dòng" là đầu
    dòng, báo ``MARKER_SPACING`` oan cho đúng dạng này (và đòi dạng sai
    ``<i> -``), nên test phải miễn trừ riêng finding đó. Đã bỏ miễn trừ: file
    ``srtgen fix`` vừa ghi phải qua được bộ kiểm của chính tool.
    """
    [(zh, py)] = fix(srt(("<i>- 你 好。- 再见。</i>", "<i>- nǐ hǎo。- zàijiàn。</i>")))
    assert zh == "<i>- 你 好。 - 再见。</i>"
    assert py == "<i>- Nǐ hǎo。 - Zàijiàn。</i>"
    assert "——" not in zh + py
    assert words(zh) == ["你", "好", "再见"]
    assert zh.count("- ") == 2 and py.count("- ") == 2


@pytest.mark.parametrize(
    ("zh", "py"),
    [
        ("<i>- 你 是 谁？</i>", "<i>- Nǐ shì shéi？</i>"),
        ("<i><b>- 你 是 谁？</b></i>", "<i><b>- Nǐ shì shéi？</b></i>"),
        ('<font color="#ff0000">- 你 是 谁？</font>', '<font color="#ff0000">- Nǐ shì shéi？</font>'),
        ("<i>- 你 好。 - 再见。</i>", "<i>- Nǐ hǎo。 - Zàijiàn。</i>"),
    ],
)
def test_marker_right_after_leading_tag_counts_as_line_start(zh: str, py: str) -> None:
    """Validator: thẻ SRT ở đầu dòng được bỏ qua trước khi xét marker — 0 lỗi."""
    bad = errors_of(srt((zh, py)))
    assert not bad, [f"[{f.code}] {f.message}" for f in bad]


def test_marker_after_leading_tag_still_needs_a_space_after_it() -> None:
    """``<i>-你``: vẫn là marker (không bị khuyên đổi thành ``——``), nhưng thiếu khoảng trắng sau.

    Lời khuyên sửa phải là dạng đúng ``<i>- 你`` — đúng thứ ``srtgen fix`` viết ra —
    chứ không bao giờ là ``<i> -``.
    """
    bad = errors_of(srt(("<i>-你 是 谁？</i>", "<i>-Nǐ shì shéi？</i>")))
    assert {f.code for f in bad} == {"MARKER_SPACING"}, [f"[{f.code}] {f.message}" for f in bad]
    assert all("<i>- " in f.message for f in bad)
    assert not any("<i> -" in f.message for f in bad)


def test_marker_mid_line_rules_are_unchanged() -> None:
    """Chỉ đầu dòng được nới: marker giữa dòng vẫn phải có khoảng trắng hai bên."""
    bad = errors_of(srt(("<i>- 你 好。-再见。</i>", "<i>- Nǐ hǎo。-Zàijiàn。</i>")))
    assert "MARKER_SPACING" in {f.code for f in bad}


def test_tokenize_line_reads_a_tag_as_one_markup_token() -> None:
    """Thẻ là một token ``markup`` nguyên văn ở cả hai chế độ, không phải cụm chữ."""
    for normalize in (False, True):
        tokens = tokenize_line("<i>他 说 好。</i>", normalize=normalize)
        assert [(t.kind, t.zh) for t in tokens] == [
            (KIND_MARKUP, "<i>"),
            ("word", "他"),
            ("word", "说"),
            ("word", "好"),
            ("punct", "。"),
            (KIND_MARKUP, "</i>"),
        ]


def test_angle_brackets_that_are_not_tags_still_become_book_marks(fix: Callable) -> None:
    """Chỉ thẻ SRT được miễn: ``<龙拳 小子>`` vẫn là tên tác phẩm và thành ``《》``."""
    [(zh, _)] = fix(srt(("<龙拳 小子>", "<lóngquán xiǎozi>")))
    assert zh == "《龙拳 小子》"
