"""Nút "Cập nhật yt-dlp" phải chạy được trên máy cài bằng CaiDat.command.

Bộ cài dựng môi trường riêng bằng ``uv venv``, và môi trường dựng kiểu đó KHÔNG
có pip. Câu lệnh cũ ``python -m pip install -U yt-dlp`` vì thế hỏng 100% trên
máy người dùng thật — đúng cái máy cần nút này nhất, vì YouTube đổi cách chặn thì
chỉ bản yt-dlp mới tải được, và tab Hướng dẫn chỉ người dùng bấm nút này.
"""

from __future__ import annotations

import pytest

import srtgen.cli as cli
from srtgen.web import server


def test_co_uv_thi_cai_bang_uv_vao_dung_python_dang_chay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_find_uv", lambda: "/Users/ban/Library/Application Support/SrtGen/bin/uv")
    args, _env = server._ytdlp_update_command("/venv/bin/python")
    assert args[0].endswith("/uv")
    assert args[1:3] == ["pip", "install"]
    assert args[args.index("--python") + 1] == "/venv/bin/python", (
        "phải cài vào đúng python đang chạy máy chủ, không phải một python nào khác"
    )
    assert "yt-dlp" in args
    assert "-m" not in args, "không được gọi pip của python — môi trường uv không có pip"


def test_khong_co_uv_thi_moi_dung_pip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_find_uv", lambda: "")
    args, _env = server._ytdlp_update_command("/venv/bin/python")
    assert args[:4] == ["/venv/bin/python", "-m", "pip", "install"]
    assert "yt-dlp" in args
