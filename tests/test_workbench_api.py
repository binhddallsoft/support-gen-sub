"""Bàn làm việc: dạng sóng, dấu đã soát, dịch lại một câu.

Ba tiện ích khiến việc vừa xem vừa sửa nhanh hơn hẳn, và cả ba đều phải chạy
ngoại tuyến (không mạng, không model): dạng sóng tính từ chính file wav của S1,
dấu ✓ đã soát nằm trong thư mục làm việc để mở lại vẫn còn, và nút dịch lại đi
qua đúng bộ dịch của S8 (được thay bằng hàm giả ở đây).
"""

from __future__ import annotations

import base64
import struct
import wave
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
    folder = tmp_path / "phim"
    folder.mkdir()
    io_utils.write_text(folder / "tap1.srt", ZH_SRT, bom=True)
    res = client.post("/api/open-local", json={"path": str(folder / "tap1.srt")}, headers=ORIGIN)
    assert res.status_code in (200, 201), res.text
    data = res.json()
    job_id = data.get("job_id") or (data.get("job") or {}).get("id")
    doc = client.get(f"/api/jobs/{job_id}/doc", headers=ORIGIN).json()
    video_id = doc.get("video_id") or (doc.get("doc") or {}).get("video_id")
    assert job_id and video_id
    return {"id": job_id, "video_id": video_id}


def _write_wav(path: Path, seconds: float = 2.0, rate: int = 16000) -> None:
    """Wav 16kHz mono: nửa đầu im lặng, nửa sau to hết cỡ — để dạng sóng phân biệt được."""
    n = int(seconds * rate)
    frames = bytearray()
    for k in range(n):
        v = 0 if k < n // 2 else 30000
        frames += struct.pack("<h", v)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))


def _point_audio_at(workspace: Path, video_id: str, wav: Path) -> None:
    base = workspace / video_id
    base.mkdir(parents=True, exist_ok=True)
    io_utils.write_json(base / "S1_audio.json", {"audio_path": str(wav)})


# --------------------------------------------------------------------------- #
# dạng sóng
# --------------------------------------------------------------------------- #

def test_peaks_come_from_the_stage_wav_and_are_cached(client: TestClient, job: dict, workspace: Path) -> None:
    base = workspace / job["video_id"]
    base.mkdir(parents=True, exist_ok=True)
    wav = base / "audio.wav"
    _write_wav(wav, seconds=2.0)
    _point_audio_at(workspace, job["video_id"], wav)

    res = client.get(f"/api/jobs/{job['id']}/peaks", headers=ORIGIN)
    assert res.status_code == 200, res.text
    data = res.json()
    assert abs(data["rate"] - 50) < 0.01
    assert abs(data["duration"] - 2.0) < 0.01
    peaks = base64.b64decode(data["peaks_b64"])
    assert len(peaks) == data["count"] == 100
    assert max(peaks[:45]) == 0, "nửa đầu im lặng phải ra 0"
    assert min(peaks[55:]) > 200, "nửa sau to hết cỡ phải gần 255"
    assert (base / "peaks.json").is_file(), "phải cất lại để lần sau khỏi tính"


def test_peaks_404_without_audio(client: TestClient, job: dict) -> None:
    res = client.get(f"/api/jobs/{job['id']}/peaks", headers=ORIGIN)
    assert res.status_code == 404


# --------------------------------------------------------------------------- #
# dấu đã soát
# --------------------------------------------------------------------------- #

def test_review_marks_round_trip_and_survive_reopen(client: TestClient, job: dict, workspace: Path) -> None:
    (workspace / job["video_id"]).mkdir(parents=True, exist_ok=True)
    assert client.get(f"/api/jobs/{job['id']}/review", headers=ORIGIN).json()["reviewed"] == []
    res = client.put(f"/api/jobs/{job['id']}/review", json={"reviewed": [2, 1, 1, "x", -3]}, headers=ORIGIN)
    assert res.status_code == 200, res.text
    assert res.json()["reviewed"] == [1, 2]
    again = client.get(f"/api/jobs/{job['id']}/review", headers=ORIGIN).json()
    assert again["reviewed"] == [1, 2]
    assert (workspace / job["video_id"] / "review.json").is_file()


# --------------------------------------------------------------------------- #
# dịch lại một câu
# --------------------------------------------------------------------------- #

class _FakeTranslator:
    name = "giả"
    needs_key = False

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    def translate_batch(self, items: list[dict[str, Any]], context: dict[str, Any], *, target: str) -> dict[int, str]:
        self.seen = {"items": items, "context": context, "target": target}
        return {0: "Tôi đến Trung Quốc chỉ có một mục đích."}


def test_translate_line_uses_context_and_returns_vietnamese(
    client: TestClient, job: dict, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import srtgen.providers as providers

    fake = _FakeTranslator()
    monkeypatch.setattr(providers, "get_translator", lambda cfg: fake)
    res = client.post(
        "/api/translate-line",
        json={"job_id": job["id"], "zh": "我 来 中国 只有 一个 目的。", "before": ["你好 世界。  =>  Xin chào thế giới."], "after": []},
        headers=ORIGIN,
    )
    assert res.status_code == 200, res.text
    assert res.json()["vi"].startswith("Tôi đến")
    assert fake.seen["items"] == [{"id": 0, "zh": "我 来 中国 只有 一个 目的。"}]
    assert fake.seen["context"]["before"] == ["你好 世界。  =>  Xin chào thế giới."]
    assert fake.seen["target"] == "vi"


def test_translate_line_explains_when_no_translator_is_available(
    client: TestClient, job: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import srtgen.providers as providers
    from srtgen.providers.translate import NullTranslator

    monkeypatch.setattr(providers, "get_translator", lambda cfg: NullTranslator(reason="Chưa có mã API nên chưa dịch được."))
    res = client.post("/api/translate-line", json={"job_id": job["id"], "zh": "你好"}, headers=ORIGIN)
    assert res.status_code == 400
    assert "Chưa có mã API" in res.json()["error"]["message"]
