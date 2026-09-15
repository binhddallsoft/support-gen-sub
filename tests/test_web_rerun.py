"""Chạy lại một phim từ thư mục làm việc cũ (hợp đồng H3) — và bản cũ không bao giờ mất (H1).

Lỗ hổng mà file này chặn tái diễn: tên riêng của nhân vật chỉ nhập được SAU lần
chạy đầu (lúc đó tab "Tên riêng" mới có gì để liệt kê). Với một phim tạo từ file
TẢI LÊN thì bản tải lên đã bị xoá ngay sau bước 1 — đúng thiết kế, video vài GB
không được nằm chiếm ổ — nên nút "Chạy lại" cũ chặn nguồn `_uploads`, còn tải
lại file thì ra một `video_id` mới, tức một thư mục làm việc mới tinh. Kết quả:
bảng tên người dùng vừa lưu KHÔNG BAO GIỜ được dùng, và không dòng nào nói ra.

`POST /api/jobs/{id}/rerun` tạo một việc mới dùng lại `video_id` và
`work/<video_id>/` của việc cũ: file wav và kết quả gỡ băng vẫn nằm đó, các chặng
trước `from_step` được dùng lại, không cần file gốc.

Bài chính chạy qua `TestClient` + hàng đợi THẬT (luồng thợ thật), với S0–S4 thay
bằng hàm giả (không ffmpeg, không whisper) — hàm giả tôn trọng việc "dùng lại"
y như chặng thật, và S0 giả nổ tung nếu phải đi tìm lại video gốc. S5–S9 là
chặng thật, S7/S8 gặp nhà cung cấp rỗng: không mạng, không model, không khoá API.
"""

from __future__ import annotations

import io
import json
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402

import srtgen.providers as providers  # noqa: E402
from srtgen import io_utils  # noqa: E402
from srtgen.core.context import deep_merge, load_config, make_video_id  # noqa: E402
from srtgen.providers.null import NullProvider  # noqa: E402
from srtgen.providers.translate import NullTranslator  # noqa: E402
from srtgen.stages import s7_ai  # noqa: E402
from srtgen.web import jobs as web_jobs  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402
from srtgen.web.jobs import Job, JobManager  # noqa: E402

#: Câu mà bảng tên đổi được cách tách: thiếu bảng thì 强大 (một từ có thật) vắt
#: qua ranh giới tên; có 光头强 = 光头 | 强 thì ra 光头 | 强 | 大喊.
CUES = [
    {"index": 1, "start": 0.0, "end": 1.5, "text": "光头强大喊。"},
    {"index": 2, "start": 1.5, "end": 3.0, "text": "我们走吧。"},
]
VI_NAME = "Cường đầu trọc"
NAME_ENTRY = {"zh": "光头 强", "pinyin": "Guāngtóu Qiáng", "vi": VI_NAME}
UPLOAD_NAME = "Gấu Boonie tập 1.wav"

#: Tên file chặng của S0..S4 (khớp `pipeline.STAGES`).
FAKE_KEYS = {0: "info", 1: "audio", 2: "asr", 3: "clean", 4: "cues"}


def make_wav(seconds: float = 0.2, rate: int = 8000) -> bytes:
    """Một file wav thật (im lặng), dựng trong bộ nhớ."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


class IdleManager(JobManager):
    """Hàng đợi nhận việc nhưng không bao giờ chạy — cho các bài kiểm từ chối/nhận yêu cầu."""

    def _ensure_worker(self) -> None:
        return None


class RecordingTranslator(NullTranslator):
    """`NullTranslator` (trả nguyên văn) nhưng nhớ bảng thuật ngữ nó được gửi."""

    def __init__(self) -> None:
        super().__init__()
        self.glossaries: list[dict[str, Any]] = []

    def translate_batch(self, items, context, *, target="vi"):  # type: ignore[override]
        self.glossaries.append(dict((context or {}).get("glossary") or {}))
        return super().translate_batch(items, context, target=target)


class _Stage:
    def __init__(self, fn: Callable[[Any], Any]) -> None:
        self._fn = fn

    def run(self, ctx: Any, on_progress: Any = None) -> Any:
        return self._fn(ctx)


class FakeStages:
    """S0–S4 giả. `computed` ghi lại chặng nào đã phải LÀM THẬT (không dùng lại được).

    Dùng lại đúng như chặng thật: có file chặng (và với S0/S1, file wav nó trỏ tới
    còn đó) thì trả kết quả cũ. S0 phải làm thật mà video gốc không còn thì nổ —
    đúng điều sẽ xảy ra với một bản tải lên đã bị xoá.
    """

    def __init__(self) -> None:
        self.computed: list[int] = []

    def module(self, step: int) -> _Stage:
        return _Stage(lambda ctx: self.run(step, ctx))

    def run(self, step: int, ctx: Any) -> Any:
        key = FAKE_KEYS[step]
        if ctx.has_stage(step, key):
            saved = ctx.load_stage(step, key)
            audio = saved.get("audio_path") if isinstance(saved, dict) else None
            if isinstance(saved, dict) and (step > 1 or (audio and Path(audio).is_file())):
                if step == 0:
                    ctx.meta.update(saved)
                return saved
        self.computed.append(step)
        if step == 0:
            source = Path(str(ctx.meta["source"]))
            if not source.is_file():
                raise RuntimeError(f"Không tìm thấy video gốc: {source}")
            wav = ctx.work_dir / "audio.wav"
            wav.write_bytes(make_wav())
            info = {"title": source.stem, "stem": source.stem, "audio_path": str(wav), "duration": 3.0}
            ctx.save_stage(0, key, info)
            ctx.meta.update(info)
            return info
        if step == 1:
            info = ctx.load_stage(0, "info") or {}
            data = {"audio_path": info.get("audio_path")}
        elif step in (2, 3):
            data = {"segments": [{"start": 0.0, "end": 3.0, "text": "光头强大喊。我们走吧。"}], "meta": {}}
        else:
            data = {"meta": {"title": ctx.meta.get("title", "")}, "cues": CUES}
        ctx.save_stage(step, key, data)
        return data


# --------------------------------------------------------------------------- #
# môi trường: máy chủ + hàng đợi thật trong thư mục tạm
# --------------------------------------------------------------------------- #

@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """`work/`, thư mục kết quả, file cài đặt đều trong `tmp_path`; không mở cửa sổ, không tải gì."""
    root = tmp_path / "work"
    root.mkdir()
    out = tmp_path / "out"
    monkeypatch.setattr(web_server, "work_root", lambda cfg=None: root)
    monkeypatch.setattr(web_server, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(web_server, "_effective_out_dir", lambda cfg, settings: str(out))
    monkeypatch.setattr(web_server, "reveal_in_file_manager", lambda path: None)
    monkeypatch.setattr(web_server, "open_document", lambda path: None)
    monkeypatch.setattr(
        web_server,
        "_download_bytes",
        lambda *a, **k: pytest.fail("Bộ test không được phép tải file từ mạng."),
    )
    return SimpleNamespace(tmp=tmp_path, root=root, out=out)


@pytest.fixture
def pipeline_env(
    workspace: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SimpleNamespace]:
    """Hàng đợi THẬT chạy S0–S4 giả + S5–S9 thật, qua đúng middleware của máy chủ."""
    fakes = FakeStages()
    translator = RecordingTranslator()
    real_import = web_jobs.import_module

    def fake_import(name: str) -> Any:
        step = next(k for k, v in web_jobs.STAGE_MODULES.items() if v == name)
        return fakes.module(step) if step in FAKE_KEYS else real_import(name)

    monkeypatch.setattr(web_jobs, "import_module", fake_import)
    monkeypatch.setattr(providers, "get_translator", lambda cfg: translator)
    monkeypatch.setattr(
        s7_ai, "get_provider", lambda cfg: NullProvider(reason="Chạy trong bộ kiểm thử.")
    )

    base = load_config()
    overrides = {
        "paths": {"work_dir": str(workspace.root), "out_dir": str(workspace.out)},
        # Không cache: cache AI/dịch nằm trong thư mục người dùng thật.
        "ai": {"enabled": False, "cache": False},
        "translate": {"cache": False},
    }
    manager = JobManager(
        state_dir=workspace.tmp / "jobs",
        autoload=False,
        config_factory=lambda job: deep_merge(base, overrides),
    )
    app = web_server.create_app(manager)
    with TestClient(app, base_url="http://127.0.0.1") as http:
        yield SimpleNamespace(
            client=http, fakes=fakes, translator=translator, manager=manager, **vars(workspace)
        )


def wait_for(client: TestClient, job_id: str, timeout: float = 120.0) -> dict[str, Any]:
    """Chờ một công việc kết thúc (qua API, như giao diện), trả bản chụp có nhật ký."""
    deadline = time.monotonic() + timeout
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()["job"]
        if not job["active"]:
            return job
        if time.monotonic() > deadline:
            pytest.fail(f"Công việc {job_id} chưa xong sau {timeout} giây: {job['message']}")
        time.sleep(0.05)


def sse_events(text: str) -> list[tuple[str, Any]]:
    """Tách luồng `text/event-stream` thành `(tên sự kiện, dữ liệu JSON)`."""
    events: list[tuple[str, Any]] = []
    for block in text.split("\n\n"):
        name, data = "", None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name:
            events.append((name, data))
    return events


def cue_lines(srt_text: str, number: int) -> tuple[str, str]:
    """(dòng Hán, dòng pinyin) của block thứ `number` (đếm từ 1)."""
    blocks = [b for b in srt_text.strip().split("\n\n") if b.strip()]
    lines = blocks[number - 1].split("\n")
    return lines[2], lines[3]


# --------------------------------------------------------------------------- #
# 1. kịch bản chính của đề bài
# --------------------------------------------------------------------------- #

def test_rerun_after_saving_names_reuses_the_folder_and_uses_the_new_names(
    pipeline_env: SimpleNamespace,
) -> None:
    """File tải lên → lưu bảng tên → chạy lại: cùng video_id, từ bước 5, dùng bảng mới."""
    env = pipeline_env
    client = env.client

    created = client.post(
        "/api/jobs",
        files={"file": (UPLOAD_NAME, make_wav(), "audio/wav")},
        data={"mode": "run", "source_kind": "file"},
    )
    assert created.status_code == 201, created.text
    first = wait_for(client, created.json()["job_id"])
    assert first["status"] == "done", first["error"]
    assert env.fakes.computed == [0, 1, 2, 3, 4]
    video_id = first["video_id"]
    work = env.root / video_id
    # Đúng thiết kế: bản tải lên bị xoá ngay sau bước 1 — nên chạy lại KHÔNG được cần nó.
    assert list((env.root / web_jobs.UPLOADS_DIR_NAME).iterdir()) == []

    srt_path = Path(first["result"]["srt"])
    old_srt = io_utils.read_text(srt_path)
    old_zh, old_py = cue_lines(old_srt, 1)
    assert "Guāngtóu Qiáng" not in old_py

    saved = client.put(f"/api/names/{video_id}", json={"entries": [NAME_ENTRY]})
    assert saved.status_code == 200, saved.text
    assert (work / "names.json").is_file()

    response = client.post(f"/api/jobs/{first['id']}/rerun", json={})
    assert response.status_code == 202, response.text
    body = response.json()
    rerun = body["job"]
    assert body["job_id"] == rerun["id"] != first["id"], "phải là một công việc MỚI"
    assert rerun["video_id"] == video_id
    assert rerun["rerun_of"] == first["id"]
    assert rerun["rerun_from"] == web_jobs.DEFAULT_RERUN_STEP == 5
    assert Path(rerun["work_dir"]) == work

    done = wait_for(client, rerun["id"])
    assert done["status"] == "done", done["error"]
    assert done["video_id"] == video_id

    # Không nghe lại (S2), không đi tìm video gốc (S0): bước 1–5 chỉ dùng lại.
    assert env.fakes.computed == [0, 1, 2, 3, 4]
    movie_dirs = [p.name for p in env.root.iterdir() if p.is_dir() and not p.name.startswith("_")]
    assert movie_dirs == [video_id], "chạy lại không được sinh thư mục làm việc mới"
    texts = [line["text"] for line in done["log"]]
    assert any("Chạy lại" in t and "Bước 6/10" in t for t in texts), texts

    # Bảng tên mới đi vào cả cách tách, pinyin, lẫn bảng thuật ngữ của chặng dịch.
    assert Path(done["result"]["srt"]) == srt_path, "ghi vào đúng file cũ (có cất bản cũ)"
    new_zh, new_py = cue_lines(io_utils.read_text(srt_path), 1)
    assert new_zh.startswith("光头 强 "), new_zh
    assert new_py.startswith("Guāngtóu Qiáng "), new_py
    assert new_zh != old_zh
    assert any(VI_NAME in json.dumps(g, ensure_ascii=False) for g in env.translator.glossaries)

    # H1 + mục 6: bản cũ được cất (không xoá), và kết quả job nói ra điều đó.
    result = client.get(f"/api/jobs/{rerun['id']}/result").json()
    backups = result["backups"]
    assert backups, "bản .srt cũ khác bản mới nên phải được cất trước khi ghi đè"
    assert result["files"]["backups"] == backups
    for raw in backups:
        path = Path(raw)
        assert path.is_file() and ".truoc-" in path.name, raw
        assert path.parent == srt_path.parent
    (srt_backup,) = [
        Path(p) for p in backups
        if Path(p).name.startswith(f"{srt_path.stem}.truoc-") and Path(p).suffix == ".srt"
    ]
    assert io_utils.read_text(srt_backup) == old_srt
    assert "không file nào bị xoá" in done["message"]
    assert done["backups"] == backups

    # Sự kiện SSE cuối cùng cũng mang `backups` — giao diện có thể chỉ nghe SSE.
    with client.stream("GET", f"/api/jobs/{rerun['id']}/events") as stream:
        events = sse_events("".join(stream.iter_text()))
    closed = [data for name, data in events if name == "closed"]
    assert closed, events
    assert closed[-1]["job"]["backups"] == backups
    assert closed[-1]["job"]["result"]["backups"] == backups

    # H6: trình sửa biết đây là file do tool xuất ra, không phải bản mở bằng nội dung.
    doc = client.get(f"/api/jobs/{rerun['id']}/doc")
    assert doc.status_code == 200, doc.text
    assert doc.json()["origin"] == "job"


# --------------------------------------------------------------------------- #
# 2. những gì bị từ chối, và vì sao
# --------------------------------------------------------------------------- #

@pytest.fixture
def legacy(workspace: SimpleNamespace) -> SimpleNamespace:
    """Một phim đã chạy xong bước 1–5 ở phiên trước, ghi bằng bản chưa có trường `work_dir`."""
    source = workspace.tmp / "phim" / "tap1.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    video_id = make_video_id(str(source))
    work = workspace.root / video_id
    work.mkdir()
    wav = work / "audio.wav"
    wav.write_bytes(make_wav())
    stages = {
        0: {"title": "tap1", "stem": "tap1", "audio_path": str(wav), "duration": 3.0},
        1: {"audio_path": str(wav)},
        2: {"segments": [{"start": 0.0, "end": 3.0, "text": "你好"}], "meta": {}},
        3: {"segments": [{"start": 0.0, "end": 3.0, "text": "你好"}]},
        4: {"meta": {"title": "tap1"}, "cues": CUES},
    }
    for number, data in stages.items():
        io_utils.write_json(work / f"S{number}_{FAKE_KEYS[number]}.json", data)

    state = workspace.tmp / "jobs"
    state.mkdir()
    old = Job(id="cu0001", source=str(source), status="done")
    old.video_id = video_id
    old.out_dir = str(workspace.out)
    saved = old.to_state_dict()
    saved.pop("work_dir", None)  # đúng hình dạng file trạng thái của phiên bản trước
    io_utils.write_json(state / "cu0001.json", saved)
    for job_id, mode, vid in (("fix0001", "fix", ""), ("early001", "run", "")):
        other = Job(id=job_id, source=str(source), mode=mode, status="error")
        other.video_id = vid
        io_utils.write_json(state / f"{job_id}.json", other.to_state_dict())
    return SimpleNamespace(source=source, video_id=video_id, work=work, wav=wav, state=state)


@pytest.fixture
def idle_client(workspace: SimpleNamespace, legacy: SimpleNamespace) -> Iterator[TestClient]:
    manager = IdleManager(state_dir=legacy.state, autoload=True)
    with TestClient(web_server.create_app(manager), base_url="http://127.0.0.1") as http:
        yield http
    manager.shutdown(wait=0.5)


def rerun(client: TestClient, job_id: str = "cu0001", **body: Any) -> Any:
    return client.post(f"/api/jobs/{job_id}/rerun", json=body)


def test_rerun_of_an_old_job_uses_the_work_folder_from_settings(
    idle_client: TestClient, legacy: SimpleNamespace
) -> None:
    response = rerun(idle_client)
    assert response.status_code == 202, response.text
    job = response.json()["job"]
    assert job["video_id"] == legacy.video_id
    assert Path(job["work_dir"]) == legacy.work
    assert job["status"] == "queued"
    assert job["options"]["from_stage"] == 5
    assert job["uploaded"] is False, "việc chạy lại không bao giờ được quyền xoá nguồn"


def test_a_second_rerun_while_the_first_still_waits_is_409(idle_client: TestClient) -> None:
    assert rerun(idle_client).status_code == 202
    again = rerun(idle_client)
    assert again.status_code == 409, again.text
    assert "đang được xử lý" in again.json()["error"]["message"]


def test_a_waiting_new_run_of_the_same_video_blocks_the_rerun(
    idle_client: TestClient, legacy: SimpleNamespace
) -> None:
    """Việc đang xếp hàng chưa có `video_id`, nhưng vẫn phải được nhận ra là cùng phim."""
    created = idle_client.post("/api/jobs", json={"source": str(legacy.source)})
    assert created.status_code == 201, created.text
    assert rerun(idle_client).status_code == 409


@pytest.mark.parametrize("value", [0, 10, -1, "abc", True, 5.5, [5]])
def test_from_step_outside_1_to_9_is_400_in_vietnamese(idle_client: TestClient, value: Any) -> None:
    response = rerun(idle_client, from_step=value)
    assert response.status_code == 400, response.text
    assert "từ 1 đến 9" in response.json()["error"]["message"]


def test_from_step_as_a_digit_string_is_accepted(idle_client: TestClient) -> None:
    """Giao diện có thể gửi chuỗi; bước 1–3 của phim mẫu vẫn còn trên đĩa."""
    response = rerun(idle_client, from_step="3")
    assert response.status_code == 202, response.text
    assert response.json()["job"]["rerun_from"] == 3


def test_from_step_past_what_is_on_disk_is_refused(idle_client: TestClient) -> None:
    """Phim mẫu chỉ có kết quả bước 1–5: chạy lại từ bước 8 thiếu kết quả bước 6."""
    response = rerun(idle_client, from_step=7)
    assert response.status_code == 400, response.text
    assert "Bước 6/10" in response.json()["error"]["message"]


def test_a_missing_earlier_stage_is_400_and_names_the_step_to_rerun_from(
    idle_client: TestClient, legacy: SimpleNamespace
) -> None:
    (legacy.work / "S3_clean.json").unlink()
    response = rerun(idle_client, from_step=5)
    assert response.status_code == 400, response.text
    message = response.json()["error"]["message"]
    assert "Bước 4/10" in message and "Bước 6/10" in message, message
    # Chạy lại từ đúng bước còn thiếu thì được: bước 1–3 vẫn còn trên đĩa.
    assert rerun(idle_client, from_step=3).status_code == 202


def test_the_extracted_audio_must_still_be_there(
    idle_client: TestClient, legacy: SimpleNamespace
) -> None:
    """Không có wav thì bước 1 không dùng lại được — nó sẽ đi tìm video gốc."""
    legacy.wav.unlink()
    response = rerun(idle_client)
    assert response.status_code == 400, response.text
    assert "file âm thanh" in response.json()["error"]["message"]


def test_unknown_job_is_404(idle_client: TestClient) -> None:
    response = rerun(idle_client, job_id="khong-co")
    assert response.status_code == 404
    assert "Không tìm thấy" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("job_id", "words"),
    [("fix0001", "không phải một lần tạo phụ đề"), ("early001", "thư mục làm việc")],
)
def test_jobs_that_have_nothing_to_rerun_are_400(
    idle_client: TestClient, job_id: str, words: str
) -> None:
    response = rerun(idle_client, job_id=job_id)
    assert response.status_code == 400, response.text
    assert words in response.json()["error"]["message"]


def test_the_rerun_endpoint_is_guarded_like_every_other_write(
    idle_client: TestClient,
) -> None:
    response = idle_client.post(
        "/api/jobs/cu0001/rerun", json={}, headers={"origin": "http://trang-la.example"}
    )
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# 3. các mảnh nhỏ của hàng đợi
# --------------------------------------------------------------------------- #

def test_an_interrupted_rerun_asks_to_rerun_not_to_resubmit_the_deleted_upload(
    tmp_path: Path,
) -> None:
    state = tmp_path / "jobs"
    state.mkdir()
    job = Job(id="rr0001", source=str(tmp_path / "_uploads" / "x.wav"), status="running")
    job.rerun_of = "cu0001"
    job.rerun_from = 5
    job.video_id = "x_12345678"
    io_utils.write_json(state / "rr0001.json", job.to_state_dict())

    loaded = JobManager(state_dir=state).get("rr0001")
    assert loaded is not None and loaded.status == "error"
    assert loaded.error["fix_action"] == "rerun_job"
    assert "rerun_job" in web_jobs.FIX_ACTIONS_EMITTED
    assert loaded.rerun_from == 5 and loaded.rerun_of == "cu0001"


def test_emit_backups_reach_the_job_result_and_the_log(tmp_path: Path) -> None:
    manager = IdleManager(state_dir=tmp_path / "jobs", autoload=False)
    job = Job(id="bk0001", source="phim.mp4")
    kept = str(tmp_path / "Phim.truoc-20260910-183000.srt")
    manager._absorb(
        job,
        SimpleNamespace(out_dir=tmp_path),
        9,
        {"srt": str(tmp_path / "Phim.srt"), "findings": [], "backups": [kept]},
    )
    assert job.result is not None and job.result["backups"] == [kept]
    assert job.public_dict()["backups"] == [kept]
    assert any("Phim.truoc-20260910-183000.srt" in line["text"] for line in job.log)


def test_rerun_step_default_and_bounds() -> None:
    assert web_jobs.rerun_step(None) == 5
    assert web_jobs.rerun_step("") == 5
    assert web_jobs.rerun_step(1) == 1 and web_jobs.rerun_step(9) == 9
    for bad in (0, 10, True, False, "s5", 2.5):
        with pytest.raises(web_jobs.JobError):
            web_jobs.rerun_step(bad)
