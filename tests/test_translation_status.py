"""Băng báo "còn dòng nguyên chữ Hán": máy chủ phải nói thật bao nhiêu dòng chưa dịch và vì sao.

Ca thật đã xảy ra: máy thiếu deep-translator, 336/336 dòng giữ nguyên chữ Hán, và
người dùng chỉ thấy cột tiếng Việt toàn chữ Hán mà không có một lời giải thích.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402

from srtgen import io_utils  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402

ORIGIN = {"Origin": "http://127.0.0.1"}
ZH_SRT = "1\n00:00:01,000 --> 00:00:02,500\n你好 世界。\nNǐhǎo shìjiè。\n"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.setattr(web_server, "work_root", lambda cfg=None: root)
    return root


@pytest.fixture
def client(tmp_path: Path, workspace: Path) -> Iterator[TestClient]:
    from srtgen.web.jobs import JobManager

    manager = JobManager(state_dir=tmp_path / "jobs", autoload=False)
    app = web_server.create_app(manager)
    with TestClient(app, base_url="http://127.0.0.1") as http:
        yield http
    manager.shutdown(wait=1.0)


@pytest.fixture
def job(client: TestClient, tmp_path: Path) -> dict[str, Any]:
    folder = tmp_path / "phim"
    folder.mkdir()
    io_utils.write_text(folder / "tap1.srt", ZH_SRT, bom=True)
    res = client.post("/api/open-local", json={"path": str(folder / "tap1.srt")}, headers=ORIGIN)
    data = res.json()
    job_id = data.get("job_id") or (data.get("job") or {}).get("id")
    doc = client.get(f"/api/jobs/{job_id}/doc", headers=ORIGIN).json()
    return {"id": job_id, "video_id": doc.get("video_id") or doc["doc"]["video_id"]}


def test_status_unavailable_without_s8(client: TestClient, job: dict) -> None:
    st = client.get(f"/api/jobs/{job['id']}/translation", headers=ORIGIN).json()
    assert st["available"] is False


def test_status_reports_kept_lines_and_the_reason(client: TestClient, job: dict, workspace: Path) -> None:
    base = workspace / job["video_id"]
    base.mkdir(parents=True, exist_ok=True)
    io_utils.write_json(base / "S8_translate.json", {
        "provider": "google_free",
        "counts": {"cues": 336, "translated": 0, "kept_original": 336},
        "errors": [{"kind": "missing_dependency", "user_message": "Máy chưa cài phần dịch miễn phí (deep-translator)."}],
        "warning": "Đang dịch bằng dịch vụ miễn phí của Google.",
    })
    st = client.get(f"/api/jobs/{job['id']}/translation", headers=ORIGIN).json()
    assert st["available"] is True
    assert st["provider"] == "google_free"
    assert st["total"] == 336 and st["kept_original"] == 336 and st["translated"] == 0
    assert "deep-translator" in st["reason"]


def test_status_is_quiet_when_everything_was_translated(client: TestClient, job: dict, workspace: Path) -> None:
    base = workspace / job["video_id"]
    base.mkdir(parents=True, exist_ok=True)
    io_utils.write_json(base / "S8_translate.json", {
        "provider": "gemini", "model": "gemini-2.5-flash",
        "counts": {"cues": 10, "translated": 10, "kept_original": 0}, "errors": [],
    })
    st = client.get(f"/api/jobs/{job['id']}/translation", headers=ORIGIN).json()
    assert st["kept_original"] == 0 and st["reason"] == ""
