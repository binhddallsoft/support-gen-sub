"""Lưu và Hoàn nguyên trong trình sửa ghi file theo ĐÚNG định dạng mà Cài đặt chọn.

Lỗi mà file này chặn tái diễn: chặng xuất file ghi theo Cài đặt (tắt BOM, CRLF),
nhưng Lưu và Hoàn nguyên chỉ đọc `default.yaml`. Người đã tắt BOM thấy BOM quay
lại ngay lần Lưu đầu, và "Hoàn nguyên về bản máy tạo" ra một file khác từng byte
với bản máy tạo thật.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402

from srtgen import io_utils  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402
from srtgen.web.jobs import JobManager  # noqa: E402

ZH = (
    "1\n00:00:01,000 --> 00:00:02,500\n你好 世界。\nNǐhǎo shìjiè。\n\n"
    "2\n00:00:02,500 --> 00:00:04,000\n我们 走 吧。\nWǒmen zǒu ba。\n\n"
)
VI = (
    "1\n00:00:01,000 --> 00:00:02,500\nChào thế giới.\n\n"
    "2\n00:00:02,500 --> 00:00:04,000\nChúng ta đi thôi.\n\n"
)
BOM = b"\xef\xbb\xbf"


class IdleManager(JobManager):
    """Hàng đợi không chạy gì — bài này chỉ cần một công việc đã xong."""

    def _ensure_worker(self) -> None:
        return None


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    root = tmp_path / "work"
    root.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(web_server, "work_root", lambda cfg=None: root)
    monkeypatch.setattr(web_server, "settings_path", lambda: settings)
    monkeypatch.setattr(web_server, "_effective_out_dir", lambda cfg, s: str(out))
    manager = IdleManager(state_dir=tmp_path / "jobs", autoload=False)
    with TestClient(web_server.create_app(manager), base_url="http://127.0.0.1") as http:
        yield SimpleNamespace(client=http, manager=manager, out=out, settings=settings)
    manager.shutdown(wait=0.5)


def _assert_format(path: Path, *, bom: bool, newline: str) -> None:
    data = path.read_bytes()
    assert data.startswith(BOM) is bom, f"{path.name}: BOM phải là {bom}"
    body = data.decode("utf-8-sig")
    if newline == "\r\n":
        assert "\r\n" in body and "\n" not in body.replace("\r\n", ""), f"{path.name}: phải toàn CRLF"
    else:
        assert "\r" not in body, f"{path.name}: phải toàn LF"


@pytest.mark.parametrize(
    ("settings", "bom", "newline"),
    [
        ({"newline": "crlf", "bom": False}, False, "\r\n"),
        ({}, True, "\n"),  # chưa cài gì: như default.yaml — hành vi cũ giữ nguyên
    ],
)
def test_save_and_revert_follow_the_format_chosen_in_settings(
    env: SimpleNamespace, settings: dict[str, Any], bom: bool, newline: str
) -> None:
    io_utils.write_json(env.settings, settings)
    srt, vi = env.out / "Phim.srt", env.out / "Phim_vi.srt"
    # Đúng thứ chặng xuất file ghi ra với Cài đặt này.
    io_utils.atomic_write_text(srt, ZH, bom=bom, newline=newline)
    io_utils.atomic_write_text(vi, VI, bom=bom, newline=newline)
    machine_zh, machine_vi = srt.read_bytes(), vi.read_bytes()
    job = env.manager.add_finished(
        source="phim.mp4",
        mode="run",
        message="xong",
        result={"srt": str(srt), "vi_srt": str(vi)},
        out_dir=str(env.out),
    )
    client = env.client

    cues = client.get(f"/api/jobs/{job.id}/doc").json()["cues"]
    cues[0]["vi_line"] = "Xin chào (sửa tay)."
    saved = client.put(f"/api/jobs/{job.id}/doc", json={"cues": cues})
    assert saved.status_code == 200, saved.text
    _assert_format(srt, bom=bom, newline=newline)
    _assert_format(vi, bom=bom, newline=newline)
    assert "Xin chào (sửa tay)." in vi.read_bytes().decode("utf-8-sig")

    reverted = client.put(f"/api/jobs/{job.id}/doc", json={"revert": True})
    assert reverted.status_code == 200, reverted.text
    assert srt.read_bytes() == machine_zh, "Hoàn nguyên phải ra ĐÚNG từng byte bản máy tạo"
    assert vi.read_bytes() == machine_vi


def test_options_of_the_run_win_over_settings_like_the_export_stage(env: SimpleNamespace) -> None:
    """Cùng thứ tự ưu tiên với `_config_for_job`: lựa chọn của lần chạy > Cài đặt > default."""
    io_utils.write_json(env.settings, {"newline": "lf", "bom": True})
    job = SimpleNamespace(options={"newline": "crlf", "bom": False})
    assert web_server._write_format(job, {"bom": True, "newline": "lf"}) == (False, "\r\n")
    job = SimpleNamespace(options={})
    assert web_server._write_format(job, {"bom": False, "newline": "crlf"}) == (True, "\n")
    io_utils.write_json(env.settings, {})
    assert web_server._write_format(job, {"bom": False, "newline": "crlf"}) == (False, "\r\n")
