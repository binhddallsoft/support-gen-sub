"""Mở file .srt hỏng: tool phải nói thẳng là nó đọc thiếu, không được im lặng.

Ca thật đo được trên máy: một file 1500 đoạn, 145 đoạn có mốc thời gian kiểu
``00:00:30,1000`` (mili giây bốn chữ số — vài công cụ xuất ra như vậy). Bộ tách
không nhận những dòng đó là đoạn mới nên dính chúng vào đoạn đứng trước. Kết quả
cũ: bảng hiện 1355 dòng, dòng thống kê ghi "không còn lỗi", bấm Lưu là ghi đè
mất 145 đoạn mà không một lời cảnh báo.

Luật mới, kiểm ở đây:
  * payload của trình sửa phải mang ``integrity`` với đủ ba con số: file định có
    bao nhiêu đoạn, đọc được bao nhiêu, thiếu bao nhiêu;
  * lỗi do chính lúc tách (BLOCK_SHAPE) phải đi kèm, và KHÔNG được trùng với lỗi
    mà ``rules.py`` đã báo;
  * file lành lặn thì mọi con số phải bằng 0 — cảnh báo giả còn hại hơn im lặng.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from srtgen.web.server import EditorFiles, _doc_payload

GOOD = (
    "1\n00:00:01,000 --> 00:00:02,000\n你好 世界。\nNǐhǎo shìjiè。\n\n"
    "2\n00:00:02,000 --> 00:00:03,000\n再见 了。\nZàijiàn le。\n\n"
    "3\n00:00:03,000 --> 00:00:04,500\n我 回家 。\nWǒ huíjiā 。\n"
)

#: Đoạn 2 có mili giây bốn chữ số -> bộ tách bỏ qua dòng giờ đó.
MS_BON_CHU_SO = GOOD.replace(
    "00:00:02,000 --> 00:00:03,000", "00:00:02,000 --> 00:00:03,1000"
)


def _files(tmp_path: Path) -> EditorFiles:
    return EditorFiles(
        srt=tmp_path / "phim.srt",
        vi=tmp_path / "phim_vi.srt",
        bundle=None,
        video_id="",
    )


def test_file_lanh_lan_thi_khong_bao_gi(tmp_path: Path) -> None:
    payload = _doc_payload(_files(tmp_path), GOOD, "")
    it = payload["integrity"]
    assert payload["cue_count"] == 3
    assert it["expected_cues"] == 3
    assert it["parsed_cues"] == 3
    assert it["missing_cues"] == 0
    assert it["bad_timestamp_count"] == 0
    assert payload["file_findings"] == []


def test_moc_gio_sai_thi_noi_ro_thieu_bao_nhieu_doan(tmp_path: Path) -> None:
    payload = _doc_payload(_files(tmp_path), MS_BON_CHU_SO, "")
    it = payload["integrity"]
    assert it["expected_cues"] == 3, "file vẫn định có ba đoạn"
    assert it["parsed_cues"] == 2, "đoạn 2 bị nuốt vào đoạn 1"
    assert it["missing_cues"] == 1
    assert it["bad_timestamp_count"] == 1
    assert it["bad_timestamp_lines"], "phải chỉ ra dòng nào trong file"
    assert it["problem_count"] >= 1


def test_doan_bi_nuot_duoc_bao_la_block_shape(tmp_path: Path) -> None:
    payload = _doc_payload(_files(tmp_path), MS_BON_CHU_SO, "")
    codes = [f["code"] for f in payload["findings"]]
    assert "BLOCK_SHAPE" in codes, (
        "đoạn nuốt đoạn khác thành block 8 dòng — phải có lỗi nói ra chuyện đó"
    )
    row_one = payload["cues"][0]["findings"]
    assert any(f["code"] == "BLOCK_SHAPE" for f in row_one), "lỗi phải gắn đúng dòng 1"


def test_khong_bao_hai_lan_cung_mot_loi(tmp_path: Path) -> None:
    """`rules.py` và bộ tách cùng biết dòng giờ sai. Người dùng chỉ được thấy một lần."""
    dau_cham = GOOD.replace(
        "00:00:02,000 --> 00:00:03,000", "00:00:02.000 --> 00:00:03.000"
    )
    payload = _doc_payload(_files(tmp_path), dau_cham, "")
    ts = [f for f in payload["findings"] if f["code"] == "TIMESTAMP_FORMAT"]
    assert len(ts) == 1, f"báo {len(ts)} lần cùng một dòng giờ"
    assert payload["integrity"]["missing_cues"] == 0, "dấu chấm vẫn đọc được, không mất đoạn"


@pytest.mark.parametrize(
    "hong, con_lai",
    [
        ("00:00:02,000 --> 00:00:03,1000", 2),   # mili giây bốn chữ số
        ("00:02,000 --> 00:03,000", 2),          # thiếu phần giờ
    ],
)
def test_cac_kieu_moc_gio_hong_deu_bi_bat(tmp_path: Path, hong: str, con_lai: int) -> None:
    text = GOOD.replace("00:00:02,000 --> 00:00:03,000", hong)
    payload = _doc_payload(_files(tmp_path), text, "")
    assert payload["cue_count"] == con_lai
    assert payload["integrity"]["missing_cues"] == 3 - con_lai
