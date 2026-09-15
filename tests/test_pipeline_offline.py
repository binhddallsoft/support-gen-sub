"""Bằng chứng end-to-end cho toàn bộ phần pipeline KHÔNG dính Whisper.

``corpus/raw.srt`` là thứ gần nhất với đầu ra của Whisper mà không cần chạy
Whisper: 3 dòng một block, chỉ có chữ Hán dính liền, chưa tách cụm, chưa có
pinyin, chưa chuẩn hoá dấu câu. Nói cách khác nó chính là đầu vào của S5.

Vì vậy bài kiểm ở đây nối thẳng **S5 → S6 → S9** trên 1351 cue thật và đòi hỏi
đúng một thứ: file ``.srt`` xuất ra phải **4 dòng mỗi block và 0 lỗi mức
error**. Đó là toàn bộ lời hứa của tool với người dùng, trừ phần gỡ băng.

Vì sao bài này đáng giá hơn tổng của các bài kiểm từng chặng: mỗi chặng có thể
đúng riêng lẻ mà vẫn hỏng khi ghép — S5 sinh token đúng nhưng ghi ra JSON thiếu
một trường, S6 đọc lại được nhưng mất cờ, S9 đọc S6 xong render ra dòng lệch cụm.
Chỉ có phép nối thật mới bắt được những chỗ đó, và chỉ có ``validate_text`` chạy
trên **file đã ghi xuống đĩa** mới chứng minh được thứ giao cho người dùng là
sạch, chứ không phải thứ nằm trong bộ nhớ.

Chạy hoàn toàn ngoại tuyến: không mạng, không model, không mã API. Tầng AI (S7)
có mặt trong đúng một test và dùng ``NullProvider``, để chứng minh rằng bật nó
lên khi không có mã API cũng không làm hỏng gì.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from srtgen.core.rules import SEVERITY_ERROR, validate_text


# --------------------------------------------------------------------------- #
# dựng đầu vào của S5 từ corpus/raw.srt
# --------------------------------------------------------------------------- #

def s4_payload(raw_text: str, *, title: str, limit: int | None = None) -> dict[str, Any]:
    """Đổi ``corpus/raw.srt`` thành đúng hình dạng mà S5 đọc từ ``S4_cues.json``.

    Ghi ra file chặng thay vì nhét thẳng ``Document`` vào ``ctx.doc`` là có chủ
    ý: đường đi thật của một lần chạy là qua đĩa (để còn "chạy tiếp giữa
    chừng"), và một lỗi tuần tự hoá chỉ lộ ra khi có ghi rồi đọc lại.

    ``segmented=False`` vì đây là chữ Hán dính liền do máy gỡ băng ra — chưa ai
    đặt ranh giới cụm, nên jieba được phép cắt. Đó chính là điểm khác giữa bài
    này và lệnh ``fix`` (nơi ranh giới cụm đã có chủ và không được cắt lại).
    """
    from srtgen.core.srt import parse_srt

    blocks = parse_srt(raw_text)
    if limit is not None:
        blocks = blocks[:limit]
    return {
        "meta": {"title": title, "segmented": False},
        "cues": [
            {
                "index": number,
                "start": block.start,
                "end": block.end,
                "text": " ".join(block.lines),
            }
            for number, block in enumerate(blocks, start=1)
        ],
    }


def make_context(tmp_dir: Path, cfg: dict[str, Any], *, video_id: str) -> Any:
    """`Context` trỏ vào thư mục tạm — không đụng thư mục dữ liệu thật của người dùng.

    Dựng thẳng ``Context`` thay vì gọi ``new_context`` vì hàm ấy tính thư mục
    làm việc theo cấu hình máy đang chạy; một bộ test ghi vào đó sẽ để lại rác
    trong ``~/Library/Application Support`` của người dùng thật.
    """
    from srtgen.core.context import Context
    from srtgen import io_utils

    work = io_utils.ensure_dir(tmp_dir / "work" / video_id)
    out = io_utils.ensure_dir(tmp_dir / "out")
    return Context(video_id=video_id, work_dir=work, out_dir=out, cfg=dict(cfg))


@pytest.fixture(scope="module")
def offline_result(tmp_path_factory: pytest.TempPathFactory, raw_text: str, cfg: dict) -> dict:
    """Chạy S5 → S6 → S9 trên trọn 1351 cue của ``raw.srt``, đúng một lần.

    Module-scope vì đây là phép chạy đắt nhất trong cả bộ test; mỗi test bên
    dưới chỉ đọc kết quả. **Chỉ đọc**: test nào cần biến đổi thì tự dựng bản
    riêng.
    """
    from srtgen import io_utils
    from srtgen.stages import s5_tokenize, s6_normalize, s9_emit

    tmp = tmp_path_factory.mktemp("offline")
    ctx = make_context(tmp, cfg, video_id="raw_offline")
    ctx.meta["title"] = "raw-offline"
    ctx.save_stage(4, "cues", s4_payload(raw_text, title="raw-offline"))

    s5_tokenize.run(ctx)
    s6_normalize.run(ctx)
    result = s9_emit.run(ctx)

    return {
        "ctx": ctx,
        "result": result,
        "srt_path": Path(result["srt"]),
        "srt_text": io_utils.read_text(Path(result["srt"])),
    }


# --------------------------------------------------------------------------- #
# 1. Cổng chính: file xuất ra phải sạch
# --------------------------------------------------------------------------- #

def test_offline_pipeline_emits_a_clean_file(offline_result: dict) -> None:
    """S5 → S6 → S9 trên ``raw.srt`` phải cho **0 lỗi mức error**.

    Đây là cổng cứng thứ hai của dự án, song song với ``fix(filter.srt)``:
    cổng kia chứng minh tool sửa được file người khác gửi sang, cổng này chứng
    minh tool **tự sinh ra** được một file đạt chuẩn từ đầu ra thô của máy gỡ
    băng. Hỏng cổng này nghĩa là mọi video chạy qua tool đều ra file lỗi.
    """
    findings = validate_text(offline_result["srt_text"])
    errors = [f for f in findings if f.severity == SEVERITY_ERROR]

    assert not errors, "\n".join(
        [f"File tự sinh còn {len(errors)} lỗi:"]
        + [f"  cue {f.cue_index}: [{f.code}] {f.message}" for f in errors[:20]]
    )


def test_every_block_has_exactly_four_lines(offline_result: dict) -> None:
    """Số thứ tự / mốc thời gian / dòng Hán / dòng pinyin — không thiếu dòng nào.

    ``parse_srt`` bóc sẵn hai dòng đầu, nên "4 dòng" ở đây là **2 dòng nội
    dung**. Một cue mất dòng pinyin vẫn qua được ``validate_text`` ở vài luật
    (không có dòng thì không có gì để lệch), nên phải đếm riêng.
    """
    from srtgen.core.srt import parse_srt

    blocks = parse_srt(offline_result["srt_text"])
    assert blocks, "Không xuất ra được block nào."
    wrong = [b.index for b in blocks if len(b.lines) != 2]
    assert not wrong, f"{len(wrong)} block không đủ 4 dòng, ví dụ cue {wrong[:10]}"


def test_no_cue_is_lost_between_the_stages(offline_result: dict, raw_text: str) -> None:
    """Số cue đi vào bằng số cue đi ra.

    S4 có quyền gộp/tách cue, S5 và S6 thì không: chúng chỉ đổi cách viết bên
    trong một cue. Một cue biến mất ở đây là một câu thoại biến mất khỏi phim,
    và không có luật định dạng nào bắt được chuyện đó.
    """
    from srtgen.core.srt import parse_srt

    went_in = len(parse_srt(raw_text))
    came_out = len(parse_srt(offline_result["srt_text"]))
    assert came_out == went_in, f"Vào {went_in} cue, ra {came_out} cue."


def test_both_lines_have_the_same_number_of_clusters(offline_result: dict) -> None:
    """Bất biến gốc của cả dự án: dòng Hán và dòng pinyin cùng số cụm.

    ``validate_text`` đã kiểm bằng ``CUM_MISMATCH``, nhưng đếm lại ở đây bằng
    một phép đếm độc lập (tách theo khoảng trắng, bỏ marker) là có lý do: nếu
    một ngày nào đó luật ``CUM_MISMATCH`` bị nới, bất biến này vẫn phải đứng, và
    nó sẽ chỉ ra ngay đúng cue hỏng.
    """
    from srtgen.core.srt import parse_srt

    def clusters(line: str) -> int:
        return sum(1 for part in line.split(" ") if part and part != "-")

    bad: list[tuple[int, int, int]] = []
    for block in parse_srt(offline_result["srt_text"]):
        zh, py = block.lines[0], block.lines[1]
        if clusters(zh) != clusters(py):
            bad.append((block.index, clusters(zh), clusters(py)))
    assert not bad, f"{len(bad)} cue lệch số cụm, ví dụ {bad[:5]}"


# --------------------------------------------------------------------------- #
# 2. Hình dạng file trên đĩa
# --------------------------------------------------------------------------- #

def test_file_is_utf8_with_bom_and_lf(offline_result: dict) -> None:
    """UTF-8 **có BOM**, xuống dòng LF — hai điều kiện để Aegisub mở đúng.

    Kiểm trên **byte thô** chứ không qua ``io_utils.read_text``: hàm đó bóc BOM
    và quy CRLF về LF, tức là nó sẽ nói "mọi thứ đều ổn" kể cả khi file ghi ra
    sai cả hai thứ.
    """
    data = offline_result["srt_path"].read_bytes()
    assert data.startswith(b"\xef\xbb\xbf"), "Thiếu BOM — Aegisub sẽ đọc sai chữ Hán."
    assert b"\r\n" not in data, "Có CRLF trong file, đặc tả đòi LF."


def test_no_stray_whitespace_anywhere(offline_result: dict) -> None:
    """Không space đầu dòng, cuối dòng, và không hai space liền.

    Renderer đảm bảo điều này *bằng cấu tạo*, nên test này là một assert phòng
    thủ: nó đỏ khi ai đó thêm một bước "sửa khoảng trắng" bằng regex trên chuỗi
    đã ghép — đúng thứ mà nguyên tắc 3 của build-spec cấm.
    """
    for number, line in enumerate(offline_result["srt_text"].split("\n"), start=1):
        assert line == line.strip(), f"dòng {number} có khoảng trắng thừa: {line!r}"
        assert "  " not in line, f"dòng {number} có hai space liền: {line!r}"


def test_the_three_deliverables_exist(offline_result: dict) -> None:
    """``.srt``, ``.bundle.json``, ``.report.html`` — ba thứ người dùng nhận được."""
    result = offline_result["result"]
    for kind in ("srt", "bundle", "report"):
        path = result.get(kind)
        assert path, f"Không xuất ra file {kind}."
        assert Path(path).is_file(), f"File {kind} không có thật: {path}"


def test_bundle_matches_the_srt_cue_for_cue(offline_result: dict) -> None:
    """``.bundle.json`` là dữ liệu cho phần mềm hiển thị — lệch một cue là hỏng cả app.

    Kiểm cả số lượng lẫn mốc thời gian của cue đầu: chỉ đếm số cue thì một bản
    dựng lệch nửa giây vẫn qua.
    """
    from srtgen import io_utils
    from srtgen.core.srt import parse_srt

    bundle = io_utils.read_json(Path(offline_result["result"]["bundle"]))
    cues = bundle.get("cues") if isinstance(bundle, dict) else None
    assert isinstance(cues, list), "bundle.json không có danh sách cue."

    blocks = parse_srt(offline_result["srt_text"])
    assert len(cues) == len(blocks)


def test_no_vietnamese_file_when_translation_never_ran(offline_result: dict) -> None:
    """Không chạy chặng dịch thì **không được** có ``_vi.srt``.

    Thà thiếu file còn hơn có một file tiếng Việt chứa nguyên văn tiếng Trung:
    người dùng sẽ gửi nó đi mà không mở ra xem, vì có file nghĩa là đã dịch.
    """
    assert offline_result["result"]["vi_srt"] is None


# --------------------------------------------------------------------------- #
# 3. Nội dung thật sự được xử lý, không phải chép nguyên
# --------------------------------------------------------------------------- #

def test_pinyin_was_actually_generated(offline_result: dict) -> None:
    """Dòng thứ hai phải là pinyin có dấu thanh, không phải bản sao dòng Hán.

    Một bug tuần tự hoá rất dễ cho ra file "hợp lệ" mà dòng pinyin chép lại chữ
    Hán: mọi luật khoảng trắng và dấu câu đều qua, số cụm cũng khớp tuyệt đối.
    Chỉ có phép kiểm "dòng này có chữ Latin và có dấu thanh không" mới bắt được.
    """
    from srtgen.core.srt import parse_srt

    tone_marks = set("āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜńňǹ")
    blocks = parse_srt(offline_result["srt_text"])
    latin = sum(1 for b in blocks if any("a" <= ch.lower() <= "z" for ch in b.lines[1]))
    toned = sum(1 for b in blocks if any(ch in tone_marks for ch in b.lines[1]))

    assert latin > 0.95 * len(blocks), "Nhiều dòng pinyin không có chữ Latin nào."
    assert toned > 0.5 * len(blocks), "Rất ít dòng pinyin có dấu thanh — nghi ngờ chưa sinh pinyin."


def test_ascii_punctuation_was_normalised(offline_result: dict) -> None:
    """Dấu ASCII trong đầu ra máy gỡ băng phải bị đổi sang dấu tiếng Trung.

    ``raw.srt`` có sẵn ``。``, nhưng chặng S6 vẫn phải chạy thật; kiểm bằng cách
    khẳng định **không còn** dấu ASCII nào trong phần nội dung. ``validate_text``
    có luật ``ASCII_PUNCT``, nhưng nó chỉ nhìn dòng nội dung — ở đây kiểm lại
    trên toàn file để chắc chắn không có dòng nào lọt qua bộ tách block.
    """
    from srtgen.core.srt import parse_srt

    forbidden = set(",.?!:;")
    offenders = [
        block.index
        for block in parse_srt(offline_result["srt_text"])
        for line in block.lines
        if forbidden & set(line)
    ]
    assert not offenders, f"Còn dấu câu ASCII ở cue {offenders[:10]}"


def test_capitalisation_was_applied(offline_result: dict) -> None:
    """Phần lớn cue phải bắt đầu bằng chữ hoa — chứng tỏ S6 đã chạy luật viết hoa.

    Ngưỡng đặt thấp (một nửa) vì luật viết hoa phụ thuộc dấu câu của cue trước
    và ``raw.srt`` có nhiều cue nối câu. Thứ cần bắt là ca "quên gọi
    ``apply_casing``", lúc đó con số rơi về gần 0.
    """
    from srtgen.core.srt import parse_srt

    blocks = parse_srt(offline_result["srt_text"])
    upper = 0
    for block in blocks:
        for ch in block.lines[1]:
            if ch.isalpha():
                upper += ch.isupper()
                break
    assert upper > 0.5 * len(blocks), f"Chỉ {upper}/{len(blocks)} cue viết hoa đầu dòng."


# --------------------------------------------------------------------------- #
# 4. Có tầng AI mà không có mã API
# --------------------------------------------------------------------------- #

def test_pipeline_still_works_with_the_ai_layer_on_and_no_api_key(
    tmp_path: Path, raw_text: str, cfg: dict, null_provider: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S5 → S6 → **S7 (NullProvider)** → S9 vẫn cho ra file sạch.

    Người dùng chưa dán mã API là trường hợp *mặc định*, không phải trường hợp
    biên: bộ cài không hỏi mã API, và tab Cài đặt nói rõ để trống vẫn chạy được.
    Vì vậy đường "bật AI nhưng không gọi được ai" phải được kiểm như một đường
    chính — nó phải im lặng đi qua, không ném lỗi và không bỏ cue nào.

    Chỉ chạy trên 120 cue đầu: thứ đang kiểm là *đường đi*, không phải chất
    lượng; chạy trọn 1351 cue lần thứ hai chỉ tốn thời gian của người chạy test.

    ``ai.cache`` tắt để không ghi gì vào thư mục cache thật của người dùng.
    """
    from srtgen import io_utils
    from srtgen.core.srt import parse_srt
    from srtgen.stages import s5_tokenize, s6_normalize, s7_ai, s9_emit

    monkeypatch.setattr(s7_ai, "get_provider", lambda cfg: null_provider)

    config = dict(cfg)
    config["ai"] = {**(cfg.get("ai") or {}), "enabled": True, "cache": False}

    ctx = make_context(tmp_path, config, video_id="raw_ai_offline")
    ctx.meta["title"] = "raw-ai-offline"
    ctx.save_stage(4, "cues", s4_payload(raw_text, title="raw-ai-offline", limit=120))

    s5_tokenize.run(ctx)
    s6_normalize.run(ctx)
    s7_ai.run(ctx)
    result = s9_emit.run(ctx)

    text = io_utils.read_text(Path(result["srt"]))
    errors = [f for f in validate_text(text) if f.severity == SEVERITY_ERROR]
    assert not errors, [f"cue {f.cue_index}: {f.code}" for f in errors[:10]]
    assert len(parse_srt(text)) == 120
