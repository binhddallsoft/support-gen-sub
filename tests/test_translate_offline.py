"""Chặng dịch S8 — thứ phải đúng là **cấu trúc**, không phải chất lượng bản dịch.

Build-spec-v2 mục 3 nói thẳng: *"số cue không đổi là bất khả thi về cấu trúc,
không phải thứ đi kiểm"*. File này kiểm đúng câu đó, và kiểm ở chỗ nó dễ hỏng
nhất — khi mô hình trả lời **thiếu phần tử**.

Vì sao cái thiếu ấy nguy hiểm hơn một bản dịch dở: một câu dịch sai thì người
soát đọc là thấy ngay. Một cue bị dồn lên làm lệch mọi cue sau nó thì file vẫn
mở lên bình thường, vẫn đủ số block, chữ vẫn là tiếng Việt — người dùng chỉ phát
hiện ra khi đã ghép xong cả tập phim và thấy lời thoại chạy trước hình. Đó là
lỗi tốn công nhất trong nghề làm phụ đề, nên nó phải là **bất khả thi**, không
phải "hiếm khi xảy ra".

Luật của tool khi thiếu: **giữ nguyên văn tiếng Trung cho đúng cue đó** rồi ghi
lại lý do bằng tiếng Việt. Một dòng chữ Hán lọt vào file tiếng Việt thì người
soát thấy ngay và sửa được; một cue lệch thì không.

Ba loại "nhà cung cấp hỏng" được dựng ở đây, đều là chuyện đã gặp thật với các
mô hình ngôn ngữ: trả thiếu dòng, tự đánh số lại, và ném lỗi giữa chừng. Cả ba
phải cho ra cùng một kết quả về cấu trúc.

Không mạng, không mã API: ``NullTranslator`` và mấy lớp giả ngay trong file này.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from srtgen.providers.base import ERR_AUTH, ERR_BAD_RESPONSE, ProviderError
from srtgen.providers.translate import NullTranslator
from srtgen.stages.s8_translate import (
    SRC_ORIGINAL,
    TranslateResult,
    attach_translation,
    translate_document,
    vi_lines_of,
)
from tests.test_pipeline_offline import make_context, s4_payload

#: Số cue dùng cho các bài ở đây. Đủ lớn để chia thành nhiều lô (lô mặc định 25
#: cue) nên đường "lô thứ hai nhìn thấy bản dịch của lô thứ nhất" thật sự chạy;
#: đủ nhỏ để cả file test xong trong vài giây.
CUE_COUNT = 80


# --------------------------------------------------------------------------- #
# nhà cung cấp giả
# --------------------------------------------------------------------------- #

class DroppingTranslator:
    """Trả lời thiếu: bỏ hẳn mỗi phần tử thứ ``every`` trong lô.

    Đây không phải một ca tưởng tượng. Mô hình ngôn ngữ bị ép trả JSON vẫn đều
    đặn bỏ sót dòng khi lô dài hoặc khi một câu bị bộ lọc nội dung chặn.
    """

    name = "dropping"
    needs_key = False

    def __init__(self, every: int = 3) -> None:
        self.every = max(2, int(every))
        self.calls = 0
        self.dropped: list[int] = []

    def translate_batch(self, items, context, *, target="vi"):
        del context, target
        self.calls += 1
        out: dict[int, str] = {}
        for position, item in enumerate(items):
            key = int(item["id"])
            if position % self.every == 0:
                self.dropped.append(key)
                continue
            out[key] = f"[dịch {key}]"
        return out


class StrayIdTranslator:
    """Trả về đúng số dòng nhưng **tự đánh số lại**, ra ngoài lô vừa được hỏi.

    Ca ác nhất trong ba ca: số lượng khớp nên một bộ kiểm chỉ đếm phần tử sẽ cho
    qua, rồi bản dịch của cue 40 rơi vào cue 0. Tool phải bỏ hết những chỉ số nó
    không hỏi, chứ không được ghép theo thứ tự.

    ``offset`` đủ lớn để không chỉ số nào tình cờ trùng với chỉ số thật — trùng
    một cái là test mất nghĩa mà vẫn xanh.
    """

    name = "stray-id"
    needs_key = False

    def __init__(self, offset: int = 10_000) -> None:
        self.offset = int(offset)
        self.calls = 0

    def translate_batch(self, items, context, *, target="vi"):
        del context, target
        self.calls += 1
        return {int(item["id"]) + self.offset: "[lệch]" for item in items}


class ExtraRowTranslator:
    """Trả lời đúng lô được hỏi, nhưng **kèm thêm** vài chỉ số không ai hỏi.

    Mô hình hay "giúp" như vậy khi thấy ngữ cảnh gửi kèm: nó dịch luôn cả 5 cue
    trước và 5 cue sau. Phần thừa phải bị bỏ, nếu không thì bản dịch của một cue
    đã chốt ở lô trước sẽ bị lô sau ghi đè bằng một bản khác.
    """

    name = "extra-row"
    needs_key = False

    def __init__(self) -> None:
        self.calls = 0

    def translate_batch(self, items, context, *, target="vi"):
        del context, target
        self.calls += 1
        out = {int(item["id"]): f"[dịch {item['id']}]" for item in items}
        # Năm cue ngay trước lô này — đã dịch xong ở lô trước, không được đè.
        first = min(out) if out else 0
        for back in range(1, 6):
            if first - back >= 0:
                out[first - back] = "[thừa]"
        return out


class MalformedOnceTranslator:
    """Lần đầu trả lời hỏng khuôn dạng, từ lần sau trả lời tử tế.

    Đây là ca thật nhất của Gemini: câu trả lời bị cắt ở ``maxOutputTokens``,
    hoặc JSON vỡ giữa chừng — cả hai đều nổi lên thành ``ProviderError`` mang
    ``kind="bad_response"``, và chính nhà cung cấp bảo người dùng "hãy thử lại".
    """

    name = "malformed_once"
    needs_key = False

    def __init__(self) -> None:
        self.calls = 0

    def translate_batch(self, items, context, *, target="vi"):
        del context, target
        self.calls += 1
        if self.calls == 1:
            raise ProviderError(
                "answer was cut off at maxOutputTokens",
                kind=ERR_BAD_RESPONSE,
                provider="gemini",
            )
        return {int(item["id"]): f"[dịch {item['id']}]" for item in items}


class ExplodingTranslator:
    """Ném lỗi ở lô thứ ``fail_at``, như mất mạng giữa chừng."""

    name = "exploding"
    needs_key = False

    def __init__(self, fail_at: int = 2) -> None:
        self.fail_at = int(fail_at)
        self.calls = 0

    def translate_batch(self, items, context, *, target="vi"):
        del context, target
        self.calls += 1
        if self.calls >= self.fail_at:
            raise RuntimeError("mất kết nối giữa chừng")
        return {int(item["id"]): f"[dịch {item['id']}]" for item in items}


# --------------------------------------------------------------------------- #
# tài liệu dùng chung
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def zh_document(tmp_path_factory: pytest.TempPathFactory, raw_text: str, cfg: dict) -> Any:
    """Một ``Document`` thật đã qua S5 + S6, dựng đúng một lần cho cả module.

    Dùng tài liệu thật chứ không dựng token bằng tay: chỗ dễ hỏng nằm ở những
    cue chỉ có nhạc nền, cue có marker đổi người nói và cue có dấu ngoặc — thứ
    mà một tài liệu bịa ra không có.
    """
    from srtgen.stages import s5_tokenize, s6_normalize

    tmp = tmp_path_factory.mktemp("translate_doc")
    ctx = make_context(tmp, cfg, video_id="tr_doc")
    ctx.meta["title"] = "tr-doc"
    ctx.save_stage(4, "cues", s4_payload(raw_text, title="tr-doc", limit=CUE_COUNT))
    s5_tokenize.run(ctx)
    return s6_normalize.run(ctx)


@pytest.fixture
def doc(zh_document: Any) -> Any:
    """Bản sao dùng riêng cho một test — ``translate_document`` ghi vào ``doc.meta``."""
    from srtgen.core.token import Document

    return Document.from_stage_dict(zh_document.to_stage_dict())


@pytest.fixture(scope="module")
def emitted_pair(tmp_path_factory: pytest.TempPathFactory, raw_text: str, cfg: dict) -> dict:
    """Chạy trọn S5 → S6 → S8(Null) → S9 và trả về hai file đã ghi xuống đĩa.

    Kiểm trên file thật chứ không trên chuỗi trong bộ nhớ: lời hứa của
    build-spec-v2 mục 2 là về *hai file người dùng nhận được*, và giữa
    ``build_vi_srt`` với file trên đĩa còn một bước ghi (BOM, xuống dòng) đủ chỗ
    để hỏng.
    """
    from srtgen import io_utils
    from srtgen.stages import s5_tokenize, s6_normalize, s8_translate, s9_emit

    tmp = tmp_path_factory.mktemp("translate_pair")
    ctx = make_context(tmp, cfg, video_id="tr_pair")
    ctx.meta["title"] = "tr-pair"
    ctx.save_stage(4, "cues", s4_payload(raw_text, title="tr-pair", limit=CUE_COUNT))

    s5_tokenize.run(ctx)
    s6_normalize.run(ctx)
    translator = NullTranslator()
    s8_translate.run(ctx, translator=translator)
    result = s9_emit.run(ctx)

    assert result["vi_srt"], "Chặng dịch đã chạy nhưng S9 không ghi file tiếng Việt."
    return {
        "translator": translator,
        "zh_path": Path(result["srt"]),
        "vi_path": Path(result["vi_srt"]),
        "zh_text": io_utils.read_text(Path(result["srt"])),
        "vi_text": io_utils.read_text(Path(result["vi_srt"])),
    }


def stamps(srt_text: str) -> list[str]:
    """Mọi dòng mốc thời gian, **nguyên văn từng ký tự**.

    So chuỗi thô chứ không so số giây: đặc tả đòi hai file giống nhau "từng ký
    tự một", và hai cách làm tròn khác nhau vẫn cho cùng một số giây trong khi
    ghi ra hai chuỗi khác nhau.
    """
    return [line for line in srt_text.split("\n") if "-->" in line]


# --------------------------------------------------------------------------- #
# 1. Hai file khớp tuyệt đối
# --------------------------------------------------------------------------- #

def test_vi_file_has_the_same_number_of_blocks(emitted_pair: dict) -> None:
    """Ràng buộc cứng số 1 của build-spec-v2 mục 2."""
    from srtgen.core.srt import parse_srt

    zh = parse_srt(emitted_pair["zh_text"])
    vi = parse_srt(emitted_pair["vi_text"])
    assert len(vi) == len(zh) == CUE_COUNT


def test_vi_timestamps_are_identical_character_for_character(emitted_pair: dict) -> None:
    """Ràng buộc cứng số 2: mốc thời gian giống **từng ký tự**, cue thứ i ứng cue thứ i."""
    zh_stamps = stamps(emitted_pair["zh_text"])
    vi_stamps = stamps(emitted_pair["vi_text"])
    assert len(zh_stamps) == len(vi_stamps)
    for position, (a, b) in enumerate(zip(zh_stamps, vi_stamps), start=1):
        assert a == b, f"cue {position}: '{a}' != '{b}'"


def test_vi_blocks_have_exactly_three_lines(emitted_pair: dict) -> None:
    """Ràng buộc cứng số 3: số thứ tự / mốc thời gian / **một** dòng tiếng Việt.

    ``parse_srt`` bóc sẵn hai dòng đầu, nên "3 dòng" ở đây là 1 dòng nội dung.
    """
    from srtgen.core.srt import parse_srt

    wrong = [b.index for b in parse_srt(emitted_pair["vi_text"]) if len(b.lines) != 1]
    assert not wrong, f"{len(wrong)} block tiếng Việt sai số dòng: {wrong[:10]}"


def test_vi_file_is_utf8_with_bom_and_has_no_trailing_space(emitted_pair: dict) -> None:
    """Ràng buộc cứng số 4 và 5: BOM, LF, và không khoảng trắng thừa.

    Điểm 5 đáng nhấn: file tiếng Việt người dùng đang dùng thật *có* space thừa
    cuối dòng. Đặc tả gọi đó là lỗi của file đó, nên tool phải cắt sạch chứ
    không được chép theo cho "giống bản cũ".
    """
    data = emitted_pair["vi_path"].read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in data
    for number, line in enumerate(emitted_pair["vi_text"].split("\n"), start=1):
        assert line == line.strip(), f"dòng {number} có khoảng trắng thừa: {line!r}"


def test_no_cue_is_dropped_even_when_it_has_no_words(emitted_pair: dict) -> None:
    """Ràng buộc cứng số 8: cue không có lời vẫn phải còn, không được xoá ở một file.

    Xoá một cue "trống" ở file tiếng Việt là cách nhanh nhất làm lệch mọi cue
    sau nó — và nó trông rất hợp lý lúc viết code.
    """
    from srtgen.core.srt import parse_srt

    empty = [b.index for b in parse_srt(emitted_pair["vi_text"]) if not b.lines[0].strip()]
    assert not empty, f"Có block tiếng Việt rỗng: {empty[:10]}"


def test_validate_pair_is_clean_on_the_generated_files(emitted_pair: dict) -> None:
    """Chính bộ luật của tool cũng phải nói hai file này khớp nhau."""
    from srtgen.core.rules import validate_pair

    findings = validate_pair(emitted_pair["zh_text"], emitted_pair["vi_text"])
    assert not findings, [f"{f.code} @ {f.cue_index}: {f.message[:80]}" for f in findings]


def test_null_translator_was_really_used_and_never_reached_a_network(
    emitted_pair: dict,
) -> None:
    """Bản dịch rỗng vẫn phải đi đúng đường code của bản dịch thật.

    Nếu ``calls == 0`` thì chặng dịch đã bị bỏ qua, và mọi khẳng định ở trên chỉ
    chứng minh rằng "không chạy gì thì không hỏng gì".
    """
    assert emitted_pair["translator"].calls > 0


# --------------------------------------------------------------------------- #
# 2. Lô trả về THIẾU phần tử — ca quan trọng nhất
# --------------------------------------------------------------------------- #

def test_missing_rows_keep_the_original_text(doc: Any) -> None:
    """Lô thiếu phần tử → cue đó giữ **nguyên văn tiếng Trung**, không dồn cue.

    Ba khẳng định, và cả ba đều cần:

    * độ dài danh sách dòng bằng đúng số cue — không thiếu, không thừa;
    * mỗi cue bị bỏ mang đúng chữ Hán **của chính nó** (đây là chỗ bắt được lỗi
      dồn cue: dồn lên thì cue i nhận chữ của cue i+1, độ dài vẫn đúng);
    * cue được dịch thì mang đúng bản dịch của chính nó.
    """
    from srtgen.core.token import render_zh

    translator = DroppingTranslator(every=3)
    result = translate_document(doc, translator, retries=0)

    zh_lines = [render_zh(cue.tokens) for cue in doc.cues]
    assert len(result.lines) == len(doc.cues) == CUE_COUNT
    assert translator.dropped, "Nhà cung cấp giả chưa bỏ dòng nào — test vô nghĩa."

    for position, (line, source) in enumerate(zip(result.lines, result.sources)):
        if source == SRC_ORIGINAL:
            assert line == zh_lines[position], (
                f"cue {position}: giữ nguyên văn nhưng lấy nhầm chữ của cue khác."
            )
        else:
            assert line == f"[dịch {position}]", f"cue {position}: bản dịch bị lệch cue."


def test_missing_rows_are_listed_with_a_vietnamese_reason(doc: Any) -> None:
    """Cue phải giữ nguyên văn được ghi lại kèm lý do người thường đọc hiểu.

    Danh sách này chính là danh sách việc phải làm tay của người soát; một mã
    lỗi kỹ thuật ở đây thì không ai làm gì được với nó.
    """
    result = translate_document(doc, DroppingTranslator(every=3), retries=0)
    assert result.kept, "Không ghi lại cue nào phải giữ nguyên văn."
    for item in result.kept:
        assert 0 <= item["position"] < CUE_COUNT
        assert item["reason"].strip()
        assert item["reason"] != item["reason"].encode("ascii", "ignore").decode(), (
            "Lý do phải viết bằng tiếng Việt cho người dùng cuối."
        )


def test_missing_rows_are_asked_again_before_giving_up(doc: Any) -> None:
    """Thiếu thì **hỏi lại lô đó** trước khi chịu giữ nguyên văn.

    Đặc tả đòi "thử lại lô đó; thử lại vẫn hỏng → giữ nguyên văn". Không có test
    này thì một lần trả thiếu do nghẽn mạng sẽ biến thành hàng chục dòng chữ Hán
    trong file tiếng Việt, dù chỉ cần hỏi lại là xong.
    """
    once = DroppingTranslator(every=3)
    translate_document(doc, once, retries=0)

    twice = DroppingTranslator(every=3)
    translate_document(doc, twice, retries=2)

    assert twice.calls > once.calls, "Không thấy lượt hỏi lại nào."


def test_a_malformed_answer_is_asked_again_instead_of_giving_up(doc: Any) -> None:
    """Câu trả lời hỏng khuôn dạng cũng phải được **hỏi lại lô đó**.

    Ca này khác ca "trả thiếu dòng" ở trên: ở đây nhà cung cấp không trả về một
    lô thiếu mà ném ``ProviderError(kind="bad_response")`` — JSON vỡ, hoặc câu
    trả lời bị cắt ở ``maxOutputTokens``. Đặc tả xếp cả hai vào cùng một luật
    ("thử lại lô đó"), và bỏ cuộc ngay lần đầu thì một lô 25 câu biến thành 25
    dòng chữ Hán nằm giữa file tiếng Việt — trong khi chỉ cần hỏi lại là xong.
    """
    translator = MalformedOnceTranslator()
    result = translate_document(doc, translator, retries=2)

    assert translator.calls > 1, "Lô hỏng khuôn dạng không được hỏi lại lần nào."
    assert result.retried_batches, "Không ghi nhận lượt hỏi lại nào."
    assert result.counts()["kept_original"] == 0, (
        "Một câu trả lời hỏng đã biến cả lô thành chữ Hán dù hỏi lại là được."
    )
    assert len(result.lines) == CUE_COUNT


def test_a_fatal_error_is_never_asked_again(doc: Any) -> None:
    """Sai mã API thì hỏi lại chỉ tốn thời gian: gọi một lượt rồi dừng hẳn.

    Đây là mặt kia của bài trên. Nếu "thử lại" được nới rộng cho mọi loại lỗi
    thì một mã API sai sẽ sinh ra ba lượt gọi cho **mỗi** lô của cả tập phim,
    tất cả cùng hỏng vì cùng một lý do, và người dùng ngồi xem.
    """

    class BadKeyTranslator:
        name = "bad_key"
        needs_key = True

        def __init__(self) -> None:
            self.calls = 0

        def translate_batch(self, items, context, *, target="vi"):
            del items, context, target
            self.calls += 1
            raise ProviderError("401", kind=ERR_AUTH, provider="gemini")

    translator = BadKeyTranslator()
    result = translate_document(doc, translator, retries=2)

    assert translator.calls == 1, "Mã API sai mà vẫn hỏi lại — đốt thời gian vô ích."
    assert result.stopped_early
    assert len(result.lines) == CUE_COUNT


def test_emitted_vi_file_still_lines_up_when_rows_are_missing(doc: Any) -> None:
    """Sau khi thiếu dòng, hai file **vẫn** cùng số block và cùng mốc thời gian.

    Đây là chỗ khép lại lời hứa: mọi khẳng định ở trên nói về danh sách trong bộ
    nhớ, còn thứ người dùng nhận là hai file. Dựng cả hai từ cùng một tài liệu
    rồi so lại là cách duy nhất chứng minh bước cuối không làm hỏng bước trước.

    Chỉ đòi sạch **những mã nói về cấu trúc**. ``VI_MARKER_SYNC`` là cảnh báo và
    ở đây nó đúng: "bản dịch" giả ``[dịch 4]`` không mang theo dấu ``-`` đổi
    người nói của dòng Hán. Bắt nó im lặng ở đây sẽ là bắt test nói dối về một
    luật đang chạy đúng.
    """
    from srtgen.core.rules import (
        SEVERITY_ERROR,
        VI_BLOCK_COUNT,
        VI_EMPTY_LINE,
        VI_TIMESTAMP_DRIFT,
        VI_TRAILING_SPACE,
        validate_pair,
    )
    from srtgen.core.srt import emit_srt
    from srtgen.stages.s9_emit import build_vi_srt

    result = translate_document(doc, DroppingTranslator(every=3), retries=0)
    attach_translation(doc, result, {"provider": "dropping", "target": "vi"})

    zh_text = emit_srt(doc)
    vi_text = build_vi_srt(doc, vi_lines_of(doc))
    findings = validate_pair(zh_text, vi_text)

    assert stamps(zh_text) == stamps(vi_text)
    structural = {VI_BLOCK_COUNT, VI_TIMESTAMP_DRIFT, VI_EMPTY_LINE, VI_TRAILING_SPACE}
    broken = [f for f in findings if f.code in structural or f.severity == SEVERITY_ERROR]
    assert not broken, [f"{f.code} @ {f.cue_index}" for f in broken]


# --------------------------------------------------------------------------- #
# 3. Hai kiểu trả lời hỏng khác
# --------------------------------------------------------------------------- #

def test_renumbered_answers_are_discarded_not_realigned(doc: Any) -> None:
    """Mô hình tự đánh số lại → **bỏ hết**, giữ nguyên văn, không ghép theo thứ tự.

    Ghép theo thứ tự là cái bẫy: số phần tử khớp nên nhìn thì có vẻ ổn, mà thực
    ra bản dịch của cue 40 vừa rơi xuống cue 0. Mô hình tự đánh số lại là mô
    hình đang nói rằng câu trả lời của nó không đáng tin.
    """
    from srtgen.core.token import render_zh

    result = translate_document(doc, StrayIdTranslator(), retries=0)
    zh_lines = [render_zh(cue.tokens) for cue in doc.cues]

    assert len(result.lines) == CUE_COUNT
    assert result.translated() == 0, "Có bản dịch lọt vào dù mọi chỉ số đều sai."
    for position, line in enumerate(result.lines):
        assert result.sources[position] == SRC_ORIGINAL
        assert line == zh_lines[position], f"cue {position} nhận nhầm nội dung."


def test_unrequested_rows_never_overwrite_a_finished_cue(doc: Any) -> None:
    """Chỉ số trả về ngoài lô vừa hỏi bị bỏ, kể cả khi nó là một cue có thật.

    Đây là mặt kia của bài trên: mô hình dịch luôn cả phần ngữ cảnh gửi kèm.
    Nhận phần thừa ấy nghĩa là mỗi lô sau lại sửa bản dịch của 5 cue mà lô trước
    đã chốt — file cuối cùng vẫn đủ cue, nhưng nội dung do một trận ghi đè quyết
    định chứ không do ai cả.
    """
    result = translate_document(doc, ExtraRowTranslator(), batch_size=20, retries=0)

    assert len(result.lines) == CUE_COUNT
    stray = [pos for pos, line in enumerate(result.lines) if line == "[thừa]"]
    assert not stray, f"Bản dịch thừa đã ghi đè cue {stray[:10]}."


def test_a_crashing_provider_never_shifts_a_cue(doc: Any) -> None:
    """Nhà cung cấp ném lỗi giữa chừng: dừng gọi, giữ nguyên văn phần còn lại.

    "Hỏng ở lô thứ hai" là dạng hỏng thật nhất — hết hạn mức, rớt mạng, đổi mã
    API. Phần đã dịch xong phải còn, phần chưa dịch phải là chữ Hán của **đúng
    cue đó**, và số cue không đổi.
    """
    from srtgen.core.token import render_zh

    result = translate_document(doc, ExplodingTranslator(fail_at=2), retries=0)
    zh_lines = [render_zh(cue.tokens) for cue in doc.cues]

    assert len(result.lines) == CUE_COUNT
    for position, (line, source) in enumerate(zip(result.lines, result.sources)):
        if source == SRC_ORIGINAL:
            assert line == zh_lines[position]
    assert result.errors, "Lỗi của nhà cung cấp phải được ghi lại để báo cáo nói ra."


def test_null_translator_keeps_every_cue_and_reports_honestly(doc: Any) -> None:
    """``NullTranslator`` trả lại chính câu tiếng Trung — và tool phải nói đúng như vậy.

    Con số ``translated`` ở đây dễ bị làm đẹp: nó đếm số cue "đã được nhà cung
    cấp trả lời", mà ``NullTranslator`` thì trả lời đủ. Điều phải đúng là **số
    cue**, và ``vi_lines_of`` phải nhận danh sách này là dùng được.
    """
    result = translate_document(doc, NullTranslator(), retries=0)
    attach_translation(doc, result, {"provider": "null", "target": "vi"})

    assert len(result.lines) == CUE_COUNT
    assert len(vi_lines_of(doc)) == CUE_COUNT


# --------------------------------------------------------------------------- #
# 4. Lưới an toàn cuối cùng
# --------------------------------------------------------------------------- #

def test_a_short_line_list_is_refused_outright(doc: Any) -> None:
    """Danh sách dòng lệch số cue bị **từ chối**, không bị dùng tạm.

    Đây là lưới cuối: giả sử mọi thứ ở trên đều hỏng và một danh sách ngắn lọt
    tới ``doc.meta``, ``vi_lines_of`` vẫn phải trả rỗng — nghĩa là S9 không ghi
    file tiếng Việt nào cả. Thà thiếu file còn hơn giao một file lệch dòng.
    """
    short = TranslateResult(lines=["a", "b"], sources=[SRC_ORIGINAL, SRC_ORIGINAL])
    attach_translation(doc, short, {"provider": "test", "target": "vi"})
    assert vi_lines_of(doc) == []


def test_empty_document_translates_to_an_empty_result() -> None:
    """Tài liệu rỗng không được làm chặng dịch nổ — nó chỉ là một phim không có thoại."""
    from srtgen.core.token import Document

    result = translate_document(Document(cues=[]), NullTranslator())
    assert result.lines == []
    assert result.requests == 0


def test_a_corrupt_row_on_disk_never_shifts_the_rest_of_the_film(
    tmp_path: Path, raw_text: str, cfg: dict
) -> None:
    """Một hàng hỏng trong ``S8_translate.json`` chỉ được làm hỏng **đúng cue đó**.

    Đường đi được dựng lại ở đây là đường thật của ``srtgen resume <id> --from s9``:
    tài liệu nạp lại từ đĩa nên ``doc.meta`` không còn bản dịch, và S9 phải đi đọc
    ``S8_translate.json``.  Nếu một hàng trong đó không đọc được — file bị sửa tay,
    bị cắt cụt giữa chừng — thì cue ấy quay về nguyên văn tiếng Trung và **mọi cue
    còn lại vẫn nằm đúng chỗ**.

    Vì sao ca này đáng một bài kiểm riêng: cách hỏng của nó là cách hỏng im lặng
    nhất trong cả tool.  Số block vẫn đúng, mốc thời gian vẫn khớp từng ký tự, nên
    ``validate_pair`` báo sạch — chỉ có nội dung là lệch đi một dòng từ chỗ hỏng
    tới hết phim, và người dùng chỉ thấy khi đã ghép xong cả tập.
    """
    from srtgen.core.srt import parse_srt
    from srtgen.core.token import Document, render_zh
    from srtgen import io_utils
    from srtgen.stages import s5_tokenize, s6_normalize, s8_translate, s9_emit

    class MarkerTranslator:
        """Trả về một chuỗi mang chính chỉ số của cue, để đọc file là biết lệch hay không."""

        name = "marker"
        model = ""

        def translate_batch(self, items, context, *, target="vi"):
            del context, target
            return {int(item["id"]): f"VI{int(item['id'])}" for item in items}

    broken = 5
    settings = dict(cfg)
    settings["translate"] = {**dict(cfg.get("translate") or {}), "cache": False}

    ctx = make_context(tmp_path, settings, video_id="tr_corrupt")
    ctx.meta["title"] = "tr-corrupt"
    ctx.save_stage(4, "cues", s4_payload(raw_text, title="tr-corrupt", limit=CUE_COUNT))
    s5_tokenize.run(ctx)
    s6_normalize.run(ctx)
    s8_translate.run(ctx, translator=MarkerTranslator())

    payload = ctx.load_stage(8, "translate")
    assert len(payload["lines"]) == CUE_COUNT
    payload["lines"][broken] = None          # hàng hỏng, **tổng số hàng không đổi**
    ctx.save_stage(8, "translate", payload)

    # Nạp lại tài liệu như một lần chạy tiếp: sạch meta, không mang bản dịch.
    doc = Document.from_stage_dict(ctx.load_stage(6, "norm"))
    doc.meta.pop(s8_translate.META_LINES, None)
    doc.meta.pop(s8_translate.META_INFO, None)
    ctx.doc = doc

    result = s9_emit.run(ctx)
    vi_blocks = parse_srt(io_utils.read_text(Path(result["vi_srt"])))
    zh_blocks = parse_srt(io_utils.read_text(Path(result["srt"])))
    assert len(vi_blocks) == len(zh_blocks)

    kept = [
        position
        for position, cue in enumerate(doc.cues)
        if render_zh(cue.tokens) or cue.py_text()
    ]
    assert broken in kept, "Cue bị làm hỏng phải là một cue thật sự có trong file."

    for number, position in enumerate(kept):
        line = " ".join(vi_blocks[number].lines)
        if position == broken:
            assert not line.startswith("VI"), "Hàng hỏng phải quay về nguyên văn, không mượn dòng của cue khác."
        else:
            assert line == f"VI{position}", (
                f"Block {number + 1} phải mang bản dịch của cue {position}, "
                f"nhận được “{line}” — cả phim đã bị dồn lên một dòng."
            )
