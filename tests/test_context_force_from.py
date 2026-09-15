"""Chạy lại từ giữa chừng: chặng sau phải đọc được kết quả chặng trước vừa ghi.

Lỗi đã đo được: `load_stage()` đi qua `has_stage()`, mà `has_stage()` mang sẵn
luật `force_from` ("chạy lại từ chặng k thì mọi chặng từ k trở đi coi như chưa
có"). Hậu quả: bấm chạy lại từ chặng 4 thì chặng 4 chạy xong, ghi `S4_cue.json`,
rồi chặng 5 đọc chính file vừa ghi và nhận `None` — lần chạy lại chết ngay ở
chặng kế tiếp. Người dùng thấy một lỗi khó hiểu ở bước 6/10 sau khi bấm một nút
tên là "chạy lại từ bước 5".

Hai câu hỏi phải tách bạch:
  * `has_stage(n)` — "kết quả cũ của chặng n có dùng lại được không": tôn trọng
    `force_from`;
  * `load_stage(n)` — "trên đĩa có file của chặng n không": không biết gì về
    `force_from`.
"""

from __future__ import annotations

from pathlib import Path

from srtgen.core.context import Context


def _ctx(tmp_path: Path, force_from: int | None) -> Context:
    cfg = {} if force_from is None else {"force_from": force_from}
    return Context(video_id="phim", work_dir=tmp_path, out_dir=tmp_path, cfg=cfg)


def test_chang_sau_doc_duoc_ket_qua_chang_vua_chay_lai(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, 4)
    ctx.save_stage(4, "cue", {"cues": [1, 2, 3]})
    assert ctx.load_stage(4, "cue") == {"cues": [1, 2, 3]}, (
        "chặng 5 đọc đầu vào của nó ở đây; trả None là cả lần chạy lại chết"
    )


def test_chang_bi_ep_chay_lai_van_khong_duoc_dung_lai_ket_qua_cu(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, 4)
    ctx.save_stage(4, "cue", {"cues": [1]})
    ctx.save_stage(5, "tokenize", {"tokens": []})
    assert ctx.has_stage(4, "cue") is False, "ép chạy lại từ 4 thì chặng 4 phải làm lại"
    assert ctx.has_stage(5, "tokenize") is False
    assert ctx.has_stage(3, "clean") is False, "chặng 3 chưa ghi gì thì vẫn là chưa có"


def test_chang_truoc_moc_ep_van_dung_lai_binh_thuong(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, 4)
    ctx.save_stage(2, "asr", {"segments": []})
    ctx.save_stage(3, "clean", {"lines": []})
    assert ctx.has_stage(2, "asr") is True
    assert ctx.has_stage(3, "clean") is True
    assert ctx.load_stage(3, "clean") == {"lines": []}


def test_khong_ep_chay_lai_thi_moi_thu_nhu_cu(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, None)
    ctx.save_stage(4, "cue", {"cues": [7]})
    assert ctx.has_stage(4, "cue") is True
    assert ctx.load_stage(4, "cue") == {"cues": [7]}


def test_file_chua_ghi_hoac_rong_van_tra_none(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, 4)
    assert ctx.load_stage(4, "cue") is None
    ctx.stage_path(4, "cue").write_text("", encoding="utf-8")
    assert ctx.load_stage(4, "cue") is None, "file rỗng = máy tắt giữa lúc ghi"
    ctx.stage_path(4, "cue").write_text("{khong-phai-json", encoding="utf-8")
    assert ctx.load_stage(4, "cue") is None, "file hỏng thì chạy lại chặng, không ném lỗi"
