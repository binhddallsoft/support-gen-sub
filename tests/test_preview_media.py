"""Xem trước có hình — video nguồn được giữ, phát được có Range, và tải lại được.

Người dùng soát phụ đề bằng cách NHÌN chữ đè lên hình đúng lúc người ta nói, rồi
mới tải file về (tuỳ chọn). Ba thứ phải đúng để luồng đó dùng được:

* ``GET /api/jobs/{id}/media`` nói thật công việc có hình hay chỉ có tiếng, và
  có tải thêm hình được không (chỉ khi nguồn là link, không phải file trên máy).
* ``GET /api/jobs/{id}/video`` phải hỗ trợ **HTTP Range** — không có thì thẻ
  ``<video>`` không tua được, mà tua tới từng dòng chính là việc soát.
* Action ``fetch_preview`` lấy link từ ``S0_info.json`` trên máy chủ chứ không
  nhận link từ trình duyệt, và ghi ``video_path`` lại để lần mở sau có hình ngay.

Ngoại tuyến hoàn toàn: không mạng, không yt-dlp thật (được thay bằng hàm giả).
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

ZH_SRT = """1
00:00:01,000 --> 00:00:02,500
你好 世界。
Nǐhǎo shìjiè。

2
00:00:02,500 --> 00:00:04,000
我 来 中国 只有 一个 目的。
Wǒ lái Zhōngguó zhǐyǒu yígè mùdì。
"""


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
    """Một công việc có thư mục làm việc thật, mở từ một file .srt trên đĩa."""
    folder = tmp_path / "phim"
    folder.mkdir()
    io_utils.write_text(folder / "tap1.srt", ZH_SRT, bom=True)
    res = client.post("/api/open-local", json={"path": str(folder / "tap1.srt")}, headers=ORIGIN)
    assert res.status_code in (200, 201), res.text
    data = res.json()
    job_id = data.get("job_id") or (data.get("job") or {}).get("id")
    assert job_id, data
    doc = client.get(f"/api/jobs/{job_id}/doc", headers=ORIGIN).json()
    video_id = doc.get("video_id") or (doc.get("doc") or {}).get("video_id")
    assert video_id, doc
    return {"id": job_id, "video_id": video_id}


def _write_info(workspace: Path, video_id: str, **fields: Any) -> Path:
    base = workspace / video_id
    base.mkdir(parents=True, exist_ok=True)
    info = {"video_id": video_id, "title": "Phim", "duration": 4.0, "is_local": False,
            "source": "https://www.youtube.com/watch?v=abc", "source_url": "https://www.youtube.com/watch?v=abc"}
    info.update(fields)
    io_utils.write_json(base / "S0_info.json", info)
    return base


def _fake_mp4(base: Path, size: int = 5000) -> Path:
    path = base / "source.mp4"
    path.write_bytes(bytes(range(256)) * (size // 256) + bytes(size % 256))
    return path


# --------------------------------------------------------------------------- #
# /media nói thật
# --------------------------------------------------------------------------- #

def test_media_reports_audio_only_and_offers_fetch_for_links(client: TestClient, job: dict, workspace: Path) -> None:
    _write_info(workspace, job["video_id"])
    info = client.get(f"/api/jobs/{job['id']}/media", headers=ORIGIN).json()
    assert info["has_video"] is False
    assert info["playable"] is False
    assert info["video_url"] == ""
    assert info["can_fetch_preview"] is True, "nguồn là link thì phải tải thêm hình được"
    assert "Tải video" in info["note"]


def test_media_never_offers_fetch_for_local_files(client: TestClient, job: dict, workspace: Path) -> None:
    _write_info(workspace, job["video_id"], is_local=True, source=r"C:\phim\a.wav", source_url=r"C:\phim\a.wav")
    info = client.get(f"/api/jobs/{job['id']}/media", headers=ORIGIN).json()
    assert info["can_fetch_preview"] is False


def test_media_reports_playable_video(client: TestClient, job: dict, workspace: Path) -> None:
    base = _write_info(workspace, job["video_id"])
    mp4 = _fake_mp4(base)
    io_utils.write_json(base / "S0_info.json", {**io_utils.read_json(base / "S0_info.json"), "video_path": str(mp4)})
    info = client.get(f"/api/jobs/{job['id']}/media", headers=ORIGIN).json()
    assert info["has_video"] is True and info["playable"] is True
    assert info["video_url"].endswith("/video")
    assert info["can_fetch_preview"] is False


def test_media_flags_container_the_browser_cannot_play(client: TestClient, job: dict, workspace: Path) -> None:
    base = _write_info(workspace, job["video_id"])
    mkv = base / "source.mkv"
    mkv.write_bytes(b"x" * 100)
    io_utils.write_json(base / "S0_info.json", {**io_utils.read_json(base / "S0_info.json"), "video_path": str(mkv)})
    info = client.get(f"/api/jobs/{job['id']}/media", headers=ORIGIN).json()
    assert info["has_video"] is True and info["playable"] is False
    assert ".mkv" in info["note"]


# --------------------------------------------------------------------------- #
# /video có Range
# --------------------------------------------------------------------------- #

def test_video_supports_http_range(client: TestClient, job: dict, workspace: Path) -> None:
    base = _write_info(workspace, job["video_id"])
    mp4 = _fake_mp4(base, size=5000)
    io_utils.write_json(base / "S0_info.json", {**io_utils.read_json(base / "S0_info.json"), "video_path": str(mp4)})

    whole = client.get(f"/api/jobs/{job['id']}/video", headers=ORIGIN)
    assert whole.status_code == 200
    assert whole.headers["accept-ranges"] == "bytes"
    assert whole.headers["content-type"].startswith("video/mp4")

    part = client.get(f"/api/jobs/{job['id']}/video", headers={**ORIGIN, "Range": "bytes=1000-1999"})
    assert part.status_code == 206
    assert part.headers["content-range"] == "bytes 1000-1999/5000"
    assert part.content == mp4.read_bytes()[1000:2000]

    tail = client.get(f"/api/jobs/{job['id']}/video", headers={**ORIGIN, "Range": "bytes=4900-"})
    assert tail.status_code == 206 and len(tail.content) == 100


def test_video_404_when_job_has_no_picture(client: TestClient, job: dict, workspace: Path) -> None:
    _write_info(workspace, job["video_id"])
    res = client.get(f"/api/jobs/{job['id']}/video", headers=ORIGIN)
    assert res.status_code == 404
    assert "Tải video" in res.json()["error"]["message"]


def test_video_path_outside_allowed_roots_is_refused(client: TestClient, job: dict, workspace: Path, tmp_path: Path) -> None:
    """Đường dẫn trong S0_info.json có thể bị sửa tay; máy chủ không phát file lạ."""
    outside = tmp_path / "ngoai" / "bi-mat.mp4"
    outside.parent.mkdir()
    outside.write_bytes(b"secret" * 100)
    _write_info(workspace, job["video_id"], video_path=str(outside))
    res = client.get(f"/api/jobs/{job['id']}/video", headers=ORIGIN)
    assert res.status_code == 404


# --------------------------------------------------------------------------- #
# action fetch_preview
# --------------------------------------------------------------------------- #

def test_fetch_preview_action_uses_server_side_link_and_records_video_path(
    client: TestClient, job: dict, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = _write_info(workspace, job["video_id"])
    seen: dict[str, Any] = {}

    def fake_fetch(work_dir: Path, url: str, cfg: Any, on_progress: Any = None, *, cancelled: Any = None) -> Path:
        seen["url"] = url
        seen["work_dir"] = work_dir
        if on_progress:
            on_progress("Đang tải video để xem trước — 50%", 0.5)
        return _fake_mp4(work_dir)

    import srtgen.stages.s0_fetch as s0

    monkeypatch.setattr(s0, "fetch_preview_video", fake_fetch)
    out = web_server.run_action("fetch_preview", params={"video_id": job["video_id"]})
    assert seen["url"] == "https://www.youtube.com/watch?v=abc", "link phải lấy từ S0_info, không từ client"
    assert Path(seen["work_dir"]) == base
    assert out["video_path"].endswith("source.mp4")
    info = io_utils.read_json(base / "S0_info.json")
    assert info["video_path"] == out["video_path"]

    media = client.get(f"/api/jobs/{job['id']}/media", headers=ORIGIN).json()
    assert media["playable"] is True


def test_fetch_preview_refuses_local_sources(client: TestClient, job: dict, workspace: Path) -> None:
    from srtgen.web.jobs import JobError

    _write_info(workspace, job["video_id"], is_local=True, source=r"C:\phim\a.mp4", source_url=r"C:\phim\a.mp4")
    with pytest.raises(JobError) as err:
        web_server.run_action("fetch_preview", params={"video_id": job["video_id"]})
    assert "file trên máy" in str(err.value)


def test_fetch_preview_is_a_long_action_in_the_whitelist() -> None:
    assert "fetch_preview" in web_server.ACTION_KEYS
    assert "fetch_preview" in web_server.LONG_ACTIONS
