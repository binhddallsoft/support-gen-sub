"""Tải file âm thanh/video lên, `/api/fix` với file ba dòng, mở `.srt` theo nội dung,
Origin khớp cả cổng, khoá hành động hỏng, và gói cài bằng pip.

Vì sao những thứ này cần test riêng: cả năm đều từng hỏng theo kiểu **im lặng**,
mà với người dùng không phải dân IT thì im lặng còn tệ hơn crash:

* Kéo thả video rồi bấm Bắt đầu thì máy chủ đọc nhầm thân yêu cầu thành JSON
  rỗng và trả "Bạn chưa dán link YouTube" — luồng tạo phụ đề từ file có sẵn hỏng
  hoàn toàn. Đây là bài test chặn đúng lỗi đó.
* `/api/fix` gặp block Hán / pinyin / Việt thì vứt dòng tiếng Việt mà vẫn báo 0 lỗi.
* "Mở file có sẵn" từ hộp chọn file của trình duyệt chỉ có nội dung, không có
  đường dẫn, và máy chủ từ chối.

Ngoại tuyến hoàn toàn: không mạng, không model, không ffmpeg. Hàng đợi công việc
được thay bằng một bản **không bao giờ chạy việc**, và các chặng pipeline được
thay bằng hàm giả — bộ test không được phép gọi ffmpeg hay chạm vào thư mục dữ
liệu thật của người dùng.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import subprocess
import sys
import time
import wave
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import ClientDisconnect  # noqa: E402

from srtgen import io_utils  # noqa: E402
from srtgen.core.rules import validate_pair  # noqa: E402
from srtgen.core.srt import parse_srt  # noqa: E402
from srtgen.web import jobs as web_jobs  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402
from srtgen.web.jobs import JobManager  # noqa: E402

#: Tên file trên đĩa phải là đúng một mã uuid4 (32 chữ hex) cộng đuôi viết thường.
UUID_NAME = re.compile(r"^[0-9a-f]{32}\.(wav|mp4)$")

MB = 1024 * 1024


def make_wav(seconds: float = 0.2, rate: int = 8000) -> bytes:
    """Một file wav thật (im lặng), dựng trong bộ nhớ — không cần file mẫu nào."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class IdleManager(JobManager):
    """Hàng đợi nhận việc nhưng **không bao giờ chạy**.

    Hàng đợi thật sẽ mở luồng thợ và gọi ffmpeg lên file vừa tải — tức là test
    phụ thuộc vào máy có ffmpeg, và file tải lên bị xoá trước khi kịp kiểm tra
    nó nằm đúng chỗ chưa. Chạy việc được kiểm riêng bằng `_execute` với chặng giả.
    """

    def _ensure_worker(self) -> None:
        return None


# --------------------------------------------------------------------------- #
# dựng máy chủ trong thư mục tạm
# --------------------------------------------------------------------------- #

@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Trỏ `work/`, file cài đặt và thư mục kết quả vào `tmp_path`."""
    root = tmp_path / "work"
    root.mkdir()
    out = tmp_path / "out"
    monkeypatch.setattr(web_server, "work_root", lambda cfg=None: root)
    monkeypatch.setattr(web_server, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(web_server, "_effective_out_dir", lambda cfg, settings: str(out))
    return root


@pytest.fixture
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chặn mọi thứ chạm vào máy thật: mở cửa sổ, tải file."""
    monkeypatch.setattr(web_server, "reveal_in_file_manager", lambda path: None)
    monkeypatch.setattr(web_server, "open_document", lambda path: None)
    monkeypatch.setattr(
        web_server,
        "_download_bytes",
        lambda *a, **k: pytest.fail("Bộ test không được phép tải file từ mạng."),
    )


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[IdleManager]:
    jobs = IdleManager(state_dir=tmp_path / "jobs", autoload=False)
    yield jobs
    jobs.shutdown(wait=0.5)


@pytest.fixture
def client(workspace: Path, manager: IdleManager, no_side_effects: None) -> Iterator[TestClient]:
    app = web_server.create_app(manager)
    with TestClient(app, base_url="http://127.0.0.1") as http:
        yield http


def uploads(workspace: Path) -> list[Path]:
    folder = workspace / "_uploads"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def post_media(
    client: TestClient,
    filename: str,
    payload: bytes,
    *,
    options: Any = None,
    mime: str = "audio/wav",
) -> Any:
    """Gửi đúng hình dạng mà `uploadAndCreate` của app.js gửi."""
    data = {"mode": "run", "source_kind": "file"}
    if options is not None:
        data["options"] = options if isinstance(options, str) else json.dumps(options)
    return client.post("/api/jobs", files={"file": (filename, payload, mime)}, data=data)


# --------------------------------------------------------------------------- #
# 1. POST /api/jobs dạng multipart
# --------------------------------------------------------------------------- #

def test_upload_creates_job_and_stores_file_under_uuid_name(
    client: TestClient, workspace: Path, manager: IdleManager
) -> None:
    """**Bài test chính**: kéo thả một file wav → có công việc, file nằm đúng chỗ, tên đĩa là uuid."""
    payload = make_wav()
    opts = {"model": "small", "translate": False, "no_translate": True, "demucs": False}
    response = post_media(client, "Gấu Boonie tập 1.wav", payload, options=opts)
    assert response.status_code == 201, response.text
    body = response.json()

    job_id = body["job_id"]
    assert body["job"]["id"] == job_id
    job = manager.get(job_id)
    assert job is not None

    stored = Path(job.source)
    assert stored.parent == workspace / "_uploads"
    assert UUID_NAME.match(stored.name), stored.name
    assert stored.read_bytes() == payload, "file trên đĩa phải đúng từng byte file đã gửi"
    assert uploads(workspace) == [stored]

    # Tên gốc chỉ là tiêu đề hiển thị, và lựa chọn của người dùng không bị rơi.
    assert job.title == "Gấu Boonie tập 1"
    assert body["upload"]["title"] == "Gấu Boonie tập 1"
    assert job.options == opts
    assert job.cleanup_source is True


def test_upload_extension_check_ignores_case(client: TestClient, workspace: Path) -> None:
    response = post_media(client, "PHIM.MP4", b"\x00" * 2048, mime="video/mp4")
    assert response.status_code == 201, response.text
    (stored,) = uploads(workspace)
    assert stored.suffix == ".mp4"
    assert UUID_NAME.match(stored.name)


def test_upload_of_unknown_type_is_refused_in_vietnamese(
    client: TestClient, workspace: Path, manager: IdleManager
) -> None:
    """Đuôi `.exe` → 400, câu tiếng Việt kể ra các loại được nhận, không ghi byte nào."""
    response = post_media(client, "virus.exe", b"MZ" + b"\x00" * 4096, mime="application/octet-stream")
    assert response.status_code == 400, response.text
    message = response.json()["error"]["message"]
    assert "chưa nhận loại file" in message
    for ext in (".mp3", ".mp4", ".wav", ".mkv", ".avi"):
        assert ext in message
    assert uploads(workspace) == []
    assert manager.list() == []


def test_upload_over_limit_is_refused_and_leaves_no_partial_file(
    client: TestClient, workspace: Path, manager: IdleManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vượt trần giữa luồng → 413 và **không còn file dở** trong `_uploads/`."""
    monkeypatch.setattr(web_server, "max_media_bytes", lambda cfg=None: 1000)
    response = post_media(client, "dai.wav", make_wav(seconds=3.0))
    assert response.status_code == 413, response.text
    assert "quá lớn" in response.json()["error"]["message"]
    assert uploads(workspace) == []
    assert manager.list() == []


def test_declared_size_over_limit_is_refused_before_reading(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Content-Length` đã vượt trần thì từ chối ngay, không ghi gì xuống đĩa."""
    monkeypatch.setattr(web_server, "max_media_bytes", lambda cfg=None: 10)
    response = post_media(client, "rat_dai.wav", b"\x00" * (512 * 1024))
    assert response.status_code == 413, response.text
    assert uploads(workspace) == []


def test_client_filename_is_never_used_as_a_path(
    client: TestClient, workspace: Path, manager: IdleManager
) -> None:
    response = post_media(client, "../../../evil.wav", make_wav())
    assert response.status_code == 201, response.text
    (stored,) = uploads(workspace)
    assert UUID_NAME.match(stored.name)
    assert not (workspace.parent / "evil.wav").exists()
    assert not (workspace / "evil.wav").exists()
    assert manager.get(response.json()["job_id"]).title == "evil"


def test_upload_with_broken_options_is_refused_and_cleaned(
    client: TestClient, workspace: Path, manager: IdleManager
) -> None:
    """`options` hỏng không được lặng lẽ thành `{}` — đó là bỏ lựa chọn của người dùng."""
    response = post_media(client, "phim.wav", make_wav(), options="{không phải json")
    assert response.status_code == 400, response.text
    assert uploads(workspace) == []
    assert manager.list() == []


def test_multipart_without_a_file_says_so(client: TestClient, workspace: Path) -> None:
    response = client.post("/api/jobs", files={"options": (None, "{}")})
    assert response.status_code == 400, response.text
    assert "chưa chọn file" in response.json()["error"]["message"]
    assert uploads(workspace) == []


def test_health_reports_the_media_limit_the_upload_enforces(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`loadHealth` của app.js lấy trần file video từ `/api/health` để chặn sớm ở trình duyệt.

    Thiếu khoá này thì giao diện luôn dùng 4 GiB mặc định: nâng `web.max_media_bytes`
    lên thì trình duyệt vẫn từ chối file mà máy chủ sẵn sàng nhận, hạ xuống thì
    người dùng phải chờ đưa lên cả file rồi mới bị từ chối.
    """
    monkeypatch.setattr(web_server, "max_media_bytes", lambda cfg=None: 123_456_789)
    body = client.get("/api/health").json()
    assert body["max_media_bytes"] == 123_456_789
    assert body["max_upload_bytes"] == web_server.MAX_UPLOAD_BYTES


def test_json_link_still_works_and_returns_job_id(
    client: TestClient, manager: IdleManager
) -> None:
    """Dạng (a) giữ nguyên, và trả cùng hình dạng `{job_id, job}` như dạng (b)."""
    response = client.post(
        "/api/jobs",
        json={"source": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "mode": "run", "options": {}},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_id"] == body["job"]["id"]
    assert manager.get(body["job_id"]).cleanup_source is False


def test_client_options_cannot_ask_for_a_file_to_be_deleted(
    client: TestClient, manager: IdleManager, tmp_path: Path
) -> None:
    """Cờ xoá file chỉ máy chủ bật được; trình duyệt gửi gì trong `options` cũng vô ích."""
    original = tmp_path / "video_cua_toi.wav"
    original.write_bytes(make_wav())
    response = client.post(
        "/api/jobs",
        json={"source": str(original), "options": {"cleanup_source": True}},
    )
    assert response.status_code == 201, response.text
    job = manager.get(response.json()["job_id"])
    assert job.cleanup_source is False
    assert web_jobs.release_upload(job) is False
    assert original.is_file()


# --------------------------------------------------------------------------- #
# 1b. ghi luồng — gọi thẳng receive_media_upload để giả lập mạng
# --------------------------------------------------------------------------- #

BOUNDARY = "----srtgenTestBoundary7MA4YWxkTrZu0gW"
CTYPE = f"multipart/form-data; boundary={BOUNDARY}"


def multipart_head(filename: str) -> bytes:
    return (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="options"\r\n\r\n'
        "{}\r\n"
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8")


MULTIPART_TAIL = f"\r\n--{BOUNDARY}--\r\n".encode("ascii")


def disk_bytes(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.glob("*")) if folder.is_dir() else 0


def test_upload_is_written_to_disk_while_it_is_still_arriving(tmp_path: Path) -> None:
    """Bằng chứng không nạp cả file vào RAM: dữ liệu đã nằm trên đĩa **trước** khi thân yêu cầu hết."""
    dest = tmp_path / "_uploads"
    seen: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        yield multipart_head("phim.wav")
        for _ in range(3):
            yield b"\x01" * MB
            seen.append(disk_bytes(dest))
        yield MULTIPART_TAIL

    upload = asyncio.run(
        web_server.receive_media_upload(body(), CTYPE, dest_dir=dest, limit=10 * MB)
    )
    assert upload.size == 3 * MB
    assert upload.path.stat().st_size == 3 * MB
    assert max(seen) >= MB, f"chưa có byte nào xuống đĩa trong lúc tải: {seen}"
    assert upload.fields.get("options") == "{}"


def test_client_disconnect_mid_upload_removes_the_partial_file(tmp_path: Path) -> None:
    dest = tmp_path / "_uploads"
    during: list[int] = []

    async def body() -> AsyncIterator[bytes]:
        yield multipart_head("phim.wav")
        yield b"\x01" * (2 * MB)
        during.append(disk_bytes(dest))
        raise ClientDisconnect()

    with pytest.raises(ClientDisconnect):
        asyncio.run(web_server.receive_media_upload(body(), CTYPE, dest_dir=dest, limit=10 * MB))
    assert during and during[0] > 0, "phải có file dở trên đĩa thì phép xoá mới có ý nghĩa"
    assert list(dest.glob("*")) == []


def test_truncated_body_removes_the_partial_file(tmp_path: Path) -> None:
    """Thân yêu cầu cụt (không có ranh giới kết thúc) = tải dở → xoá, báo 400."""
    dest = tmp_path / "_uploads"

    async def body() -> AsyncIterator[bytes]:
        yield multipart_head("phim.wav")
        yield b"\x01" * (2 * MB)

    with pytest.raises(web_server._UploadError) as info:
        asyncio.run(web_server.receive_media_upload(body(), CTYPE, dest_dir=dest, limit=10 * MB))
    assert info.value.status == 400
    assert list(dest.glob("*")) == []


def test_stream_writer_writes_whole_blocks_and_discards_cleanly(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b.bin"
    writer = io_utils.BinaryStreamWriter(target, max_bytes=10, block_bytes=4)
    writer.write(b"abc")
    assert target.stat().st_size == 0  # chưa đủ một khối
    writer.write(b"defgh")
    assert target.stat().st_size == 8  # hai khối đầy đã xuống đĩa
    with pytest.raises(io_utils.StreamTooLarge):
        writer.write(b"xyz")  # 8 + 3 > 10: từ chối TRƯỚC khi ghi
    writer.discard()
    assert not target.exists()

    with io_utils.BinaryStreamWriter(target, block_bytes=4) as ok:
        ok.write(b"123456")
    assert target.read_bytes() == b"123456"


# --------------------------------------------------------------------------- #
# 1c. dọn bản tải lên sau S0 — chạy luồng thợ với chặng giả
# --------------------------------------------------------------------------- #

class _FakeStage:
    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def run(self, ctx: Any, on_progress: Any) -> Any:
        return self._fn(ctx)


def _fake_pipeline(monkeypatch: pytest.MonkeyPatch, s0: Any, seen: dict[str, Any]) -> None:
    """Thay mười chặng bằng hàm giả; S1 ghi lại lúc nó chạy thì bản tải lên còn hay mất."""

    def s1(ctx: Any) -> dict[str, Any]:
        seen["upload_alive_at_s1"] = Path(str(seen["upload"])).exists()
        return {}

    def fake_import(name: str) -> Any:
        mapping = web_jobs.STAGE_MODULES
        step = next(k for k in range(10) if mapping[k] == name)
        if step == 0:
            return _FakeStage(s0)
        if step == 1:
            return _FakeStage(s1)
        if step == 9:
            return _FakeStage(lambda ctx: {"srt": None, "findings": []})
        return _FakeStage(lambda ctx: {})

    monkeypatch.setattr(web_jobs, "import_module", fake_import)


def _s0_like_the_real_one(ctx: Any) -> dict[str, Any]:
    """Như `s0_fetch._fetch_local`: ghi wav vào work/<id>/, tiêu đề = tên file nó đọc."""
    wav = ctx.work_dir / "audio.wav"
    wav.write_bytes(make_wav())
    source = Path(str(ctx.meta["source"]))
    info = {"title": source.stem, "stem": source.stem, "audio_path": str(wav), "duration": 1.0}
    ctx.save_stage(0, "info", info)
    ctx.meta.update(info)
    return info


@pytest.fixture
def worker_env(tmp_path: Path) -> tuple[IdleManager, Path]:
    work = tmp_path / "work"
    cfg = {"paths": {"work_dir": str(work), "out_dir": str(tmp_path / "out")}}
    jobs = IdleManager(state_dir=tmp_path / "jobs", autoload=False, config_factory=lambda job: cfg)
    upload = work / web_jobs.UPLOADS_DIR_NAME / ("0123456789abcdef0123456789abcdef.wav")
    upload.parent.mkdir(parents=True)
    upload.write_bytes(make_wav())
    return jobs, upload


def test_upload_is_deleted_right_after_s0_and_the_wav_is_kept(
    worker_env: tuple[IdleManager, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, upload = worker_env
    seen: dict[str, Any] = {"upload": upload}
    _fake_pipeline(monkeypatch, _s0_like_the_real_one, seen)

    job = jobs.submit(str(upload), title="Gấu Boonie tập 1", cleanup_source=True)
    jobs._execute(job)

    assert job.status == "done", job.error
    assert not upload.exists()
    assert seen["upload_alive_at_s1"] is False, "phải xoá ngay sau S0, không đợi hết pipeline"

    work_dir = upload.parent.parent / job.video_id
    assert (work_dir / "audio.wav").is_file(), "file wav trình sửa cần để nghe lại phải được giữ"
    # Tên ngẫu nhiên của bản tải lên không được lọt thành tên phim / tên file kết quả.
    assert job.title == "Gấu Boonie tập 1"
    info = io_utils.read_json(work_dir / "S0_info.json")
    assert info["title"] == "Gấu Boonie tập 1"
    assert info["stem"] == "Gấu Boonie tập 1"


def test_upload_is_deleted_when_s0_fails(
    worker_env: tuple[IdleManager, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs, upload = worker_env

    def broken(ctx: Any) -> Any:
        raise RuntimeError("ffmpeg không đọc được file này")

    _fake_pipeline(monkeypatch, broken, {"upload": upload})
    job = jobs.submit(str(upload), title="phim hỏng", cleanup_source=True)
    jobs._execute(job)

    assert job.status == "error"
    assert not upload.exists()


def test_upload_of_a_job_cancelled_while_queued_is_deleted(
    worker_env: tuple[IdleManager, Path],
) -> None:
    jobs, upload = worker_env
    job = jobs.submit(str(upload), title="phim", cleanup_source=True)
    jobs.cancel(job.id)
    jobs._queue.put(None)
    jobs._work_loop()
    assert job.status == "cancelled"
    assert not upload.exists()


def test_release_refuses_files_outside_the_uploads_folder(tmp_path: Path) -> None:
    """Chốt thứ hai: kể cả khi cờ bị bật nhầm, file gốc của người dùng không bao giờ bị xoá."""
    original = tmp_path / "Phim" / "tap1.wav"
    original.parent.mkdir()
    original.write_bytes(make_wav())
    job = web_jobs.Job(id="x", source=str(original))
    job.cleanup_source = True
    assert web_jobs.release_upload(job) is False
    assert original.is_file()


def test_interrupted_upload_job_asks_for_the_file_again(tmp_path: Path) -> None:
    """Ứng dụng đóng giữa lúc chạy một file tải lên: không hứa "Chạy lại", bảo kéo thả lại."""
    state = tmp_path / "jobs"
    state.mkdir()
    job = web_jobs.Job(id="abc123", source=str(tmp_path / "_uploads" / "f.wav"), status="running")
    job.cleanup_source = True
    io_utils.write_json(state / "abc123.json", job.to_state_dict())

    loaded = JobManager(state_dir=state).get("abc123")
    assert loaded is not None and loaded.status == "error"
    assert loaded.error["fix_action"] == "check_file"
    assert "kéo thả lại" in loaded.error["message"]


def test_stale_orphan_uploads_are_swept_at_startup(
    workspace: Path, manager: IdleManager, no_side_effects: None
) -> None:
    folder = workspace / "_uploads"
    folder.mkdir()
    old = folder / ("a" * 32 + ".mp4")
    fresh = folder / ("b" * 32 + ".mp4")
    old.write_bytes(b"x")
    fresh.write_bytes(b"y")
    two_days_ago = time.time() - 2 * 24 * 3600
    os.utime(old, (two_days_ago, two_days_ago))

    web_server.create_app(manager)
    assert not old.exists()
    assert fresh.exists(), "bản tải lên mới có thể đang được một công việc dùng"


# --------------------------------------------------------------------------- #
# 2. /api/fix với block ba dòng Hán / pinyin / Việt
# --------------------------------------------------------------------------- #

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


def post_fix(client: TestClient, text: str, filename: str = "tap1.srt") -> dict[str, Any]:
    response = client.post("/api/fix", json={"filename": filename, "text": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_fix_keeps_the_vietnamese_line_and_returns_it(client: TestClient, tmp_path: Path) -> None:
    """**Bài test chính của mục 2**: dòng thứ ba không được biến mất trong im lặng."""
    body = post_fix(client, TRILINGUAL)

    assert body["has_vi"] is True
    assert body["vi_cues"] == 2
    vi_blocks = parse_srt(body["vi_text"])
    assert [" ".join(b.lines) for b in vi_blocks] == [
        "Chào thế giới.",
        "Tôi đến Trung Quốc chỉ có một mục đích.",
    ]

    # File Hán/pinyin đúng bốn dòng mỗi block, không lẫn chữ Việt, pinyin người
    # biên tập viết không bị sinh lại.
    zh_blocks = parse_srt(body["text"])
    assert [b.raw_line_count for b in zh_blocks] == [4, 4]
    assert "Chào" not in body["text"]
    assert "Nǐhǎo shìjiè。" in body["text"]

    # Hai file khớp số block và mốc thời gian từng ký tự.
    pair = validate_pair(body["text"], body["vi_text"])
    assert [f.code for f in pair if f.severity == "error"] == []
    assert not [f for f in body["findings"] if f["code"] == "BLOCK_SHAPE"]

    # Tải về: file _vi.srt đi kèm, và nút tải của công việc có nó.
    assert body["vi_filename"] == "tap1.da-chuan-hoa_vi.srt"
    assert Path(body["vi_path"]).is_file()
    assert "Tôi đến Trung Quốc" in io_utils.read_text(body["vi_path"])
    assert "vi_srt" in body["job"]["downloads"]
    download = client.get(body["job"]["downloads"]["vi_srt"])
    assert download.status_code == 200
    assert "Chào thế giới." in download.content.decode("utf-8-sig")


def test_fix_file_with_a_block_missing_vietnamese_keeps_both_files_aligned(
    client: TestClient,
) -> None:
    text = TRILINGUAL.replace("Chào thế giới.\n", "")
    body = post_fix(client, text)
    assert len(parse_srt(body["vi_text"])) == len(parse_srt(body["text"])) == 2
    empty = [f for f in body["findings"] if f["code"] == "VI_EMPTY_LINE"]
    assert empty and empty[0]["cue_index"] == 1


def test_fix_sets_aside_a_line_it_cannot_place_and_says_so(client: TestClient) -> None:
    """Dòng không biết xếp vào đâu: giữ nguyên văn + finding, tuyệt đối không lặng lẽ bỏ."""
    stray = "Wǒ lái Zhōngguó"
    text = TRILINGUAL.replace(
        "Tôi đến Trung Quốc chỉ có một mục đích.\n",
        f"Tôi đến Trung Quốc chỉ có một mục đích.\n{stray}\n",
    )
    body = post_fix(client, text)
    assert [item["text"] for item in body["set_aside"]] == [stray]
    kept = [f for f in body["findings"] if f["code"] == "BLOCK_SHAPE" and f["line"] == stray]
    assert kept and kept[0]["severity"] == "error"
    assert stray in kept[0]["message"]
    # Phần còn lại của block vẫn được xử lý bình thường.
    assert "Tôi đến Trung Quốc chỉ có một mục đích." in body["vi_text"]


def test_fix_two_line_block_of_chinese_and_vietnamese(client: TestClient) -> None:
    text = "1\n00:00:01,000 --> 00:00:02,000\n你 是 谁？\nAnh là người Việt à?\n"
    body = post_fix(client, text)
    assert body["has_vi"] is True
    assert "Anh là người Việt à?" in body["vi_text"]
    (block,) = parse_srt(body["text"])
    assert block.raw_line_count == 4
    assert "người" not in body["text"]


def test_fix_plain_four_line_file_has_no_vietnamese_file(client: TestClient) -> None:
    text = "1\n00:00:01,000 --> 00:00:02,000\n你好 世界。\nNǐhǎo shìjiè。\n"
    body = post_fix(client, text)
    assert body["has_vi"] is False
    assert body["vi_text"] == ""
    assert body["vi_path"] == ""
    assert body["set_aside"] == []


# --------------------------------------------------------------------------- #
# 4. POST /api/open-local dạng {name, content, vi_content}
# --------------------------------------------------------------------------- #

ZH_SRT = """1
00:00:01,000 --> 00:00:02,500
你好 世界。
Nǐhǎo shìjiè。

2
00:00:02,500 --> 00:00:04,000
我 来 中国 只有 一个 目的。
Wǒ lái Zhōngguó zhǐyǒu yígè mùdì。
"""

VI_SRT = """1
00:00:01,000 --> 00:00:02,500
Chào thế giới.

2
00:00:02,500 --> 00:00:04,000
Tôi đến Trung Quốc chỉ có một mục đích.
"""


def movie_folders(workspace: Path) -> list[Path]:
    return sorted(p for p in workspace.iterdir() if p.is_dir() and not p.name.startswith("_"))


def test_open_local_by_content_saves_into_a_new_work_folder_and_opens(
    client: TestClient, workspace: Path
) -> None:
    response = client.post(
        "/api/open-local",
        json={"name": "Tập 1.srt", "content": ZH_SRT, "vi_content": VI_SRT},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    doc = body["doc"]
    assert doc["cue_count"] == 2
    assert doc["has_vi"] is True
    assert doc["cues"][1]["vi_line"] == "Tôi đến Trung Quốc chỉ có một mục đích."

    (folder,) = movie_folders(workspace)
    assert doc["video_id"] == folder.name
    assert Path(doc["srt_path"]) == folder / "Tập 1.srt"
    assert (folder / "Tập 1_vi.srt").is_file()

    # Mở như thường: đọc lại được, và bấm Lưu ghi vào đúng thư mục đó.
    job_id = body["job"]["id"]
    cues = client.get(f"/api/jobs/{job_id}/doc").json()["cues"]
    cues[0]["vi_line"] = "Xin chào thế giới."
    saved = client.put(f"/api/jobs/{job_id}/doc", json={"cues": cues})
    assert saved.status_code == 200, saved.text
    assert "Xin chào thế giới." in io_utils.read_text(folder / "Tập 1_vi.srt")


def test_open_local_by_content_over_limit_is_413_and_leaves_nothing(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_server, "MAX_UPLOAD_BYTES", 50)
    response = client.post("/api/open-local", json={"name": "to.srt", "content": ZH_SRT})
    assert response.status_code == 413, response.text
    assert movie_folders(workspace) == []


def test_open_local_by_content_that_is_not_srt_leaves_nothing(
    client: TestClient, workspace: Path
) -> None:
    for content in ("", "đây không phải phụ đề"):
        response = client.post("/api/open-local", json={"name": "x.srt", "content": content})
        assert response.status_code == 400, response.text
    assert movie_folders(workspace) == []


# --------------------------------------------------------------------------- #
# 5. Origin phải khớp cả host lẫn cổng
# --------------------------------------------------------------------------- #

@pytest.fixture
def ported_app(workspace: Path, manager: IdleManager, no_side_effects: None) -> Any:
    return web_server.create_app(manager, port=8756)


def _record_open_guide(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    ran: list[int] = []
    monkeypatch.setitem(
        web_server._ACTION_RUNNERS,
        "open_guide",
        lambda report, cancelled: ran.append(1) or {"message": "x", "detail": ""},
    )
    return ran


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:9999",   # ứng dụng khác trên cùng máy, cổng khác
        "http://localhost:3000",
        "http://127.0.0.1",        # không ghi cổng = cổng 80, không phải 8756
    ],
)
def test_origin_from_another_port_is_refused(
    ported_app: Any, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    ran = _record_open_guide(monkeypatch)
    with TestClient(ported_app, base_url="http://127.0.0.1:8756") as http:
        response = http.post("/api/actions/open_guide", headers={"origin": origin})
    assert response.status_code == 403, response.text
    assert ran == []


@pytest.mark.parametrize(
    "origin",
    ["http://127.0.0.1:8756", "http://localhost:8756", "http://[::1]:8756", None],
)
def test_origin_on_our_own_port_is_accepted(
    ported_app: Any, monkeypatch: pytest.MonkeyPatch, origin: str | None
) -> None:
    ran = _record_open_guide(monkeypatch)
    headers = {"origin": origin} if origin else {}
    with TestClient(ported_app, base_url="http://127.0.0.1:8756") as http:
        response = http.post("/api/actions/open_guide", headers=headers)
    assert response.status_code == 200, response.text
    assert ran == [1]


def test_host_header_with_another_port_is_refused(ported_app: Any) -> None:
    with TestClient(ported_app, base_url="http://127.0.0.1:9999") as http:
        assert http.get("/api/health").status_code == 403


def test_serve_records_the_real_port_before_listening(
    tmp_path: Path, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`serve()` phải ghi cổng thật vào `app.state.port` — không có thì phép kiểm cổng vô dụng."""
    uvicorn = pytest.importorskip("uvicorn")
    recorded: list[Any] = []

    class FakeConfig:
        def __init__(self, app: Any, **kwargs: Any) -> None:
            self.app = app

    class FakeServer:
        def __init__(self, config: FakeConfig) -> None:
            self.config = config
            self.started = False
            self.should_exit = False

        def run(self) -> None:
            recorded.append(self.config.app.state.port)

    idle = IdleManager(state_dir=tmp_path / "jobs", autoload=False)
    monkeypatch.setattr(web_server, "get_manager", lambda: idle)
    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setenv(web_server.NO_BROWSER_ENV, "1")
    web_server.serve(open_browser=False, port=8799)
    assert recorded == [8799]


# --------------------------------------------------------------------------- #
# 6. /api/actions với khoá rỗng hoặc có dấu /
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "path",
    [
        "/api/actions",
        "/api/actions/",
        "/api/actions/a/b",
        "/api/actions/install_ffmpeg;%20rm%20-rf%20/",
        "/api/actions/..%2F",
        "/api/actions/%2E%2E%2Fetc%2Fpasswd",
    ],
)
def test_malformed_action_path_is_a_400_in_vietnamese(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    def explode(*args: Any, **kwargs: Any) -> Any:
        pytest.fail(f"Đường dẫn hỏng đã chạm tới lời gọi hệ thống: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", explode)
    monkeypatch.setattr(subprocess, "run", explode)
    for key in list(web_server._ACTION_RUNNERS):
        monkeypatch.setitem(web_server._ACTION_RUNNERS, key, explode)

    response = client.post(path)
    assert response.status_code == 400, (path, response.status_code, response.text)
    message = response.json()["error"]["message"]
    assert "không hiểu" in message.lower()
    assert "ffmpeg" in message


# --------------------------------------------------------------------------- #
# 7. gói cài bằng pip có đủ giao diện
# --------------------------------------------------------------------------- #

def test_pyproject_ships_the_web_package_and_its_static_files(repo_root: Path) -> None:
    tomllib = pytest.importorskip("tomllib")
    data = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    setuptools_cfg = data["tool"]["setuptools"]
    assert "srtgen.web" in setuptools_cfg["packages"]
    assert "web/static/*" in setuptools_cfg["package-data"]["srtgen"]
    assert (repo_root / "srtgen" / "web" / "__init__.py").is_file()
    assert (repo_root / "srtgen" / "web" / "static" / "index.html").is_file()


def test_importing_the_web_package_does_not_pull_in_fastapi(repo_root: Path) -> None:
    """`srtgen doctor` phải chạy được trên máy chưa cài fastapi."""
    probe = subprocess.run(
        [sys.executable, "-c", "import sys, srtgen.web; print('fastapi' in sys.modules)"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert probe.stdout.strip() == "False"
