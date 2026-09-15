"""Hợp đồng API của máy chủ web: nút hành động, bảng tên riêng, tab Cài đặt.

Vì sao ba nhóm này cần test riêng, tách khỏi ``test_editor_api.py``: cả ba đều
hỏng theo kiểu **im lặng**, mà với người dùng không phải dân IT thì im lặng còn
tệ hơn crash — crash thì họ biết mà gọi người giúp.

* ``PUT /api/names/{id}`` từng lấy nguyên thân yêu cầu làm bảng "chữ Hán →
  pinyin", nên ``names.json`` biến thành ``{"names": {"entries": "[{...}]"}}``:
  tên vừa gõ mất sạch, file bị bơm một khoá rác, **mà giao diện vẫn báo "Đã lưu
  N tên"**. Đây là bài test chặn đúng lỗi đó tái diễn.
* ``POST /api/actions/{key}`` là chỗ dễ thành lỗ hổng thực thi lệnh nhất trong
  cả dự án. Test ở đây khẳng định hai chuyện: khoá lạ bị từ chối bằng một câu
  tiếng Việt, và ``key`` không bao giờ trở thành tên lệnh.
* ``POST /api/settings`` từng chỉ nhận 9 khoá, nên "Tách giọng khỏi nhạc nền" và
  "Dịch tiếng Việt bằng…" bị vứt lặng lẽ dù toast báo "Đã lưu cài đặt".

Ngoại tuyến hoàn toàn: không mạng, không model, không mã API. Mọi việc chạy lâu
(cài ffmpeg, cập nhật yt-dlp, tải model) đều bị thay bằng hàm giả — bộ test
không được phép gọi ``pip`` hay tải 1.6GB về máy ai.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402

from srtgen import io_utils  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402
from srtgen.web.jobs import Job  # noqa: E402


# --------------------------------------------------------------------------- #
# dựng máy chủ trong thư mục tạm
# --------------------------------------------------------------------------- #

@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Trỏ ``work/`` và file cài đặt vào ``tmp_path``.

    File cài đặt phải được trỏ đi chỗ khác, nếu không bộ test sẽ ghi đè khoá API
    thật của người đang chạy nó — một bộ test làm mất dữ liệu là bộ test không ai
    dám chạy.
    """
    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.setattr(web_server, "work_root", lambda cfg=None: root)
    monkeypatch.setattr(web_server, "settings_path", lambda: tmp_path / "settings.json")
    return root


@pytest.fixture
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chặn mọi thứ chạm vào máy thật: mở cửa sổ, gọi pip, tải file."""
    monkeypatch.setattr(web_server, "reveal_in_file_manager", lambda path: None)
    monkeypatch.setattr(web_server, "open_document", lambda path: None)
    monkeypatch.setattr(
        web_server,
        "_download_bytes",
        lambda *a, **k: pytest.fail("Bộ test không được phép tải file từ mạng."),
    )


@pytest.fixture
def client(tmp_path: Path, workspace: Path, no_side_effects: None) -> Iterator[TestClient]:
    """Máy khách HTTP thật, đi qua đúng lớp kiểm ``Host``/``Origin`` của máy chủ."""
    from srtgen.web.jobs import JobManager

    manager = JobManager(state_dir=tmp_path / "jobs", autoload=False)
    app = web_server.create_app(manager)
    with TestClient(app, base_url="http://127.0.0.1") as http:
        yield http
    manager.shutdown(wait=1.0)


def names_json(workspace: Path, video_id: str) -> dict[str, Any]:
    return json.loads((workspace / video_id / "names.json").read_text(encoding="utf-8-sig"))


# --------------------------------------------------------------------------- #
# 1. bảng tên riêng — chặn lỗi mất dữ liệu trong im lặng
# --------------------------------------------------------------------------- #

ENTRIES = [
    {"zh": "光头强", "pinyin": "Guāngtóuqiáng", "vi": "Cường đầu trọc"},
    {"zh": "熊二", "pinyin": "Xióngèr", "vi": "Gấu Ú"},
]


def test_put_names_saves_exactly_what_the_ui_sent(
    client: TestClient, workspace: Path
) -> None:
    """**Bài test chính**: gửi ``{entries:[{zh,pinyin,vi}]}`` thì file phải có đúng thế.

    So từng trường chứ không so số lượng: bản hỏng cũ vẫn ghi ra một file
    ``names.json`` hợp lệ về cú pháp, chỉ là nội dung bên trong là rác.
    """
    (workspace / "phim001").mkdir()
    response = client.put("/api/names/phim001", json={"entries": ENTRIES})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 2

    saved = names_json(workspace, "phim001")
    assert saved["names"] == {"光头强": "Guāngtóuqiáng", "熊二": "Xióngèr"}
    assert [e["han"] for e in saved["entries"]] == ["光头强", "熊二"]
    assert [e["vi"] for e in saved["entries"]] == ["Cường đầu trọc", "Gấu Ú"]


def test_put_names_never_writes_the_wrapper_key_as_a_name(
    client: TestClient, workspace: Path
) -> None:
    """Chặn đúng lỗi cũ: khoá ``entries`` không được biến thành một "tên riêng".

    Bản hỏng ghi ra ``{"names": {"entries": "[{'zh': ...}]"}}`` — tức là S5/S6 sẽ
    đi tìm chữ Hán tên là "entries" trong phụ đề. Không dòng nào báo lỗi, người
    dùng chỉ thấy tên nhân vật của mình biến mất.
    """
    (workspace / "phim001").mkdir()
    client.put("/api/names/phim001", json={"entries": ENTRIES})

    saved = names_json(workspace, "phim001")
    for rubbish in ("entries", "names", "version", "rev", "zh", "pinyin", "vi"):
        assert rubbish not in saved["names"], f"“{rubbish}” bị ghi nhầm thành một tên riêng"


def test_get_names_returns_the_contract_shape(client: TestClient, workspace: Path) -> None:
    """Đọc lại phải ra đúng ``{video_id, entries:[{zh,pinyin,vi}]}``."""
    (workspace / "phim001").mkdir()
    client.put("/api/names/phim001", json={"entries": ENTRIES})

    body = client.get("/api/names/phim001").json()
    assert body["video_id"] == "phim001"
    assert [(e["zh"], e["pinyin"], e["vi"]) for e in body["entries"]] == [
        ("光头强", "Guāngtóuqiáng", "Cường đầu trọc"),
        ("熊二", "Xióngèr", "Gấu Ú"),
    ]


def test_vietnamese_name_survives_a_second_save(client: TestClient, workspace: Path) -> None:
    """Lưu lại lần hai không được làm rụng trường ``vi``.

    Đây là chỗ dữ liệu hay mất nhất: lần lưu đầu trông đúng, lần lưu thứ hai đi
    qua một đường chuyển đổi khác và đánh rơi trường mới thêm.
    """
    (workspace / "phim001").mkdir()
    client.put("/api/names/phim001", json={"entries": ENTRIES})
    again = client.get("/api/names/phim001").json()["entries"]

    client.put("/api/names/phim001", json={"entries": again})
    assert [e["vi"] for e in client.get("/api/names/phim001").json()["entries"]] == [
        "Cường đầu trọc",
        "Gấu Ú",
    ]


def test_old_flat_names_file_still_opens(client: TestClient, workspace: Path) -> None:
    """File ``names.json`` phẳng kiểu cũ phải mở ra thành bảng sửa được, không phải bảng trống."""
    folder = workspace / "phim002"
    folder.mkdir()
    io_utils.write_json(folder / "names.json", {"张三": "Zhāngsān", "李四": "Lǐsì"})

    entries = client.get("/api/names/phim002").json()["entries"]
    assert [(e["zh"], e["pinyin"]) for e in entries] == [
        ("张三", "Zhāngsān"),
        ("李四", "Lǐsì"),
    ]


def test_names_index_lists_every_movie_that_has_a_table(
    client: TestClient, workspace: Path
) -> None:
    """``GET /api/names`` phải liệt kê phim, kèm tên phim đọc từ ``S0_info.json``."""
    folder = workspace / "phim003"
    folder.mkdir()
    io_utils.write_json(folder / "S0_info.json", {"title": "Gấu Boonie tập 1"})
    client.put("/api/names/phim003", json={"entries": ENTRIES})

    body = client.get("/api/names").json()
    assert body["movies"] == [{"video_id": "phim003", "title": "Gấu Boonie tập 1", "count": 2}]


def test_bad_video_id_is_refused(client: TestClient) -> None:
    """Mã video không đúng khuôn thì bị chặn trước khi thành một đường dẫn thật."""
    assert client.get("/api/names/..%2F..%2Fetc").status_code in (400, 404)


# --------------------------------------------------------------------------- #
# 2. nút hành động — danh sách trắng cứng
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "key",
    ["evil", "shutdown", "__import__", "install_uv", "rm", "open_output_dir; ls"],
)
def test_unknown_action_is_refused_in_vietnamese(client: TestClient, key: str) -> None:
    """Khoá ngoài danh sách trắng: 400 và một câu tiếng Việt, không phải 405/500."""
    response = client.post(f"/api/actions/{key}")
    assert response.status_code == 400, response.text
    message = response.json()["error"]["message"]
    assert "không hiểu" in message.lower()
    assert "ffmpeg" in message  # câu từ chối có kể ra tool làm được gì


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_action_from_a_sandboxed_iframe_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    """``<iframe sandbox>`` của trang lạ gửi ``Origin: null``: không được chạy việc nào.

    ``urlparse("null").netloc`` rỗng, mà chuỗi rỗng nằm trong ``ALLOWED_HOSTS``,
    nên phép kiểm ``Origin`` một mình để lọt. ``Sec-Fetch-Site`` do trình duyệt
    tự đặt nên trang độc không giả được.
    """
    ran: list[int] = []
    monkeypatch.setitem(
        web_server._ACTION_RUNNERS,
        "open_guide",
        lambda report, cancelled: ran.append(1) or {"message": "x", "detail": ""},
    )
    response = client.post(
        "/api/actions/open_guide", headers={"origin": "null", "sec-fetch-site": site}
    )
    assert response.status_code == 403, response.text
    assert ran == []


def test_action_from_the_ui_itself_still_runs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chính giao diện (cùng nguồn) vẫn bấm được, kể cả khi trình duyệt gửi ``Origin: null``."""
    ran: list[int] = []
    monkeypatch.setitem(
        web_server._ACTION_RUNNERS,
        "open_guide",
        lambda report, cancelled: ran.append(1) or {"message": "x", "detail": ""},
    )
    for headers in (
        {"origin": "http://127.0.0.1:8756", "sec-fetch-site": "same-origin"},
        {"origin": "null", "sec-fetch-site": "same-origin"},
        {},
    ):
        response = client.post("/api/actions/open_guide", headers=headers)
        assert response.status_code == 200, (headers, response.text)
    assert len(ran) == 3


def test_action_key_never_becomes_a_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """``key`` chỉ được dùng để tra bảng, không bao giờ đi vào một lời gọi hệ thống."""
    import subprocess

    def explode(*args: Any, **kwargs: Any) -> Any:
        pytest.fail(f"Khoá lạ đã chạm tới lời gọi hệ thống: {args!r}")

    monkeypatch.setattr(subprocess, "Popen", explode)
    monkeypatch.setattr(subprocess, "run", explode)
    with pytest.raises(Exception) as err:
        web_server.run_action("update_ytdlp; rm -rf /")
    assert "không hiểu" in str(err.value).lower()


def test_whitelist_matches_the_contract() -> None:
    """Đúng sáu khoá, và bốn khoá chạy lâu — khớp bảng ``ACTIONS`` của giao diện."""
    assert set(web_server.ACTION_KEYS) == {
        "install_ffmpeg",
        "update_ytdlp",
        "download_model",
        "fetch_preview",
        "open_output_dir",
        "open_guide",
    }
    assert web_server.LONG_ACTIONS == {"install_ffmpeg", "update_ytdlp", "download_model", "fetch_preview"}
    assert web_server.action_key("install_ytdlp") == "update_ytdlp"


def test_zip_is_never_allowed_to_choose_where_it_lands(tmp_path: Path) -> None:
    """Giải nén ffmpeg: chỗ ghi do máy chủ quyết, không do file nén quyết.

    ``ZipFile.extract`` lấy đường dẫn từ **bên trong** file nén, tức là để một
    file nén độc hại tự chọn chỗ ghi (``../../…``). Bài test dựng đúng cái bẫy
    đó và khẳng định byte chỉ rơi vào đúng chỗ đã định.
    """
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../../../ffmpeg", b"noi-dung-doc-hai")
        archive.writestr("ffmpeg-7.0/ffmpeg", b"noi-dung-that")

    target = tmp_path / "bin" / "ffmpeg"
    web_server._extract_binary(buffer.getvalue(), "ffmpeg", target)

    assert target.is_file()
    assert target.read_bytes() in (b"noi-dung-doc-hai", b"noi-dung-that")
    assert not (tmp_path.parent / "ffmpeg").exists()
    assert list(p.name for p in (tmp_path / "bin").iterdir()) == ["ffmpeg"]


def test_zip_without_the_binary_says_so_in_vietnamese(tmp_path: Path) -> None:
    """File nén không chứa thứ cần thì báo bằng tiếng Việt, không ném lỗi thư viện."""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("doc-me-di.txt", b"khong phai ffmpeg")

    with pytest.raises(Exception) as err:
        web_server._extract_binary(buffer.getvalue(), "ffmpeg", tmp_path / "ffmpeg")
    assert "không chứa ffmpeg" in str(err.value)


def test_short_action_answers_right_away(client: TestClient) -> None:
    """Việc ngắn trả ``{ok, message}`` ngay, không tạo công việc nền."""
    response = client.post("/api/actions/open_output_dir")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["long"] is False
    assert body["message"].strip()


def test_long_action_returns_a_job_id_and_streams(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Việc dài trả ``{job_id}`` và tiến trình đi ra bằng đúng đường của job thường.

    Bấm xong mà màn hình im lặng là lỗi nặng nhất với người dùng không phải dân
    IT: họ bấm lại nhiều lần rồi kết luận tool hỏng. Nên bài test này kiểm cả
    ``job_id`` lẫn việc công việc đó tra cứu được qua ``/api/jobs/{id}``.
    """
    seen: list[float] = []

    def fake(report: Any, cancelled: Any) -> dict[str, Any]:
        for step in (0.0, 0.5, 1.0):
            report("Đang làm việc giả…", step)
            seen.append(step)
        return {"message": "Xong việc giả.", "detail": ""}

    monkeypatch.setitem(web_server._ACTION_RUNNERS, "install_ffmpeg", fake)

    response = client.post("/api/actions/install_ffmpeg")
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert job_id

    for _ in range(100):
        snapshot = client.get(f"/api/jobs/{job_id}").json()["job"]
        if not snapshot["active"]:
            break
    assert snapshot["status"] == "done", snapshot
    assert snapshot["message"] == "Xong việc giả."
    assert seen == [0.0, 0.5, 1.0]
    # Giao diện lọc theo `mode` để không đem một lần "cài ffmpeg" lên màn hình
    # tiến trình 10 bước của pipeline.
    assert snapshot["mode"] == "action"


def test_long_action_pushes_progress_over_sse(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tiến trình của việc dài đi ra bằng đúng đường SSE của công việc thường.

    Đây là thứ biến "bấm xong rồi im lặng" thành "có thanh chạy": giao diện chỉ
    biết đọc ``/api/jobs/{id}/events``, nên việc dài phải phát ra ở đúng chỗ đó.
    """
    import time as _time

    def fake(report: Any, cancelled: Any) -> dict[str, Any]:
        for step in (0.2, 0.6, 1.0):
            report(f"Đang chạy {int(step * 100)}%…", step)
            _time.sleep(0.05)
        return {"message": "Xong việc giả.", "detail": ""}

    monkeypatch.setitem(web_server._ACTION_RUNNERS, "download_model", fake)
    job_id = client.post("/api/actions/download_model").json()["job_id"]

    body = ""
    with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
        for chunk in stream.iter_text():
            body += chunk
            if "event: closed" in body:
                break

    assert "event: snapshot" in body
    assert "event: closed" in body
    assert "Xong việc giả." in body


def test_double_click_does_not_run_the_same_action_twice(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bấm đúp / hai tab: một việc dài chỉ chạy một bản, bấm lại là theo dõi tiếp bản đó.

    Trước đây ba cú bấm liền tay sinh ra ba luồng ``pip install -U yt-dlp`` song
    song vào cùng một môi trường — chúng có thể gỡ bản của nhau giữa chừng.
    """
    import threading
    import time as _time

    gate = threading.Event()
    started: list[int] = []

    def slow(report: Any, cancelled: Any) -> dict[str, Any]:
        started.append(1)
        gate.wait(5)
        return {"message": "Xong việc giả.", "detail": ""}

    monkeypatch.setitem(web_server._ACTION_RUNNERS, "update_ytdlp", slow)
    try:
        ids = [client.post("/api/actions/update_ytdlp").json()["job_id"] for _ in range(3)]
        assert len(set(ids)) == 1, ids
        # Tên khác của cùng một việc cũng không được mở bản thứ hai.
        assert client.post("/api/actions/install_ytdlp").json()["job_id"] == ids[0]
    finally:
        gate.set()

    for _ in range(250):
        if not client.get(f"/api/jobs/{ids[0]}").json()["job"]["active"]:
            break
        _time.sleep(0.02)
    assert len(started) == 1
    # Xong rồi thì bấm lại phải chạy một lần mới, không bám vào việc cũ.
    assert client.post("/api/actions/update_ytdlp").json()["job_id"] != ids[0]


# --------------------------------------------------------------------------- #
# 3. tab Cài đặt
# --------------------------------------------------------------------------- #

FULL_PATCH: dict[str, Any] = {
    "model": "medium",
    "profile": "drama",
    "ai_enabled": True,
    "translate_provider": "google_free",
    "demucs": True,
    "ass_export": True,
    "bilingual_ass": False,
    "bom": False,
}


def test_settings_keeps_every_key_the_ui_sends(client: TestClient) -> None:
    """Bốn khoá từng bị vứt lặng lẽ phải sống sót qua một vòng lưu-đọc-lại."""
    saved = client.post("/api/settings", json=FULL_PATCH)
    assert saved.status_code == 200, saved.text
    assert saved.json()["unknown_keys"] == []

    settings = client.get("/api/settings").json()["settings"]
    assert settings["translate_provider"] == "google_free"
    assert settings["demucs"] is True
    assert settings["bilingual_ass"] is False
    assert settings["bom"] is False
    assert settings["model"] == "medium"


def test_settings_accepts_the_nested_shape_too(client: TestClient) -> None:
    """Giao diện gửi kèm bảng lồng theo hình dạng ``default.yaml`` — không được báo lạ."""
    response = client.post(
        "/api/settings",
        json={
            "asr": {"model": "small"},
            "audio": {"demucs": True},
            "translate": {"provider": "gemini"},
            "emit": {"ass_export": True, "bilingual_ass": True, "bom": True},
            "default_profile": "drama",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["unknown_keys"] == []
    settings = response.json()["settings"]
    assert settings["model"] == "small"
    assert settings["demucs"] is True
    assert settings["translate_provider"] == "gemini"


def test_unknown_setting_is_reported_not_swallowed(client: TestClient) -> None:
    """Khoá không hiểu phải được kể tên cho client, chứ không biến mất im lặng."""
    response = client.post("/api/settings", json={**FULL_PATCH, "mau_nen": "xanh"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unknown_keys"] == ["mau_nen"]
    assert "mau_nen" in body["message"]


def test_settings_never_returns_the_api_key(client: TestClient) -> None:
    """Khoá API chỉ đi ra ở dạng che, không bao giờ nguyên văn."""
    secret = "AIzaSyTESTKEY0123456789abcd"
    client.post("/api/settings", json={"api_key": secret})

    raw = client.get("/api/settings").text
    assert secret not in raw
    body = json.loads(raw)
    assert body["api_key_set"] is True
    assert body["api_key_masked"].startswith("AIza")
    assert body["api_key_masked"].endswith(secret[-4:])


def test_settings_lists_models_with_a_downloaded_flag(client: TestClient) -> None:
    """Bảng model của build-spec-v2 mục 8 phải có mặt, kèm cờ đã-tải."""
    body = client.get("/api/settings").json()
    models = body["models"]
    assert models, "GET /api/settings phải trả bảng model"
    assert [m["id"] for m in models][:1] == ["large-v3-turbo"]
    for model in models:
        for field in ("id", "label", "eta", "size", "downloaded"):
            assert field in model, f"model {model.get('id')} thiếu trường {field}"
    assert body["settings"]["models"] == models


def test_bad_out_dir_message_has_no_english_from_the_os(
    client: TestClient, tmp_path: Path
) -> None:
    """Câu lỗi thư mục phải là tiếng Việt; nguyên văn của hệ điều hành để riêng.

    "[WinError 3] The system cannot find the path specified" giữa một câu tiếng
    Việt không giúp được người dùng, mà lại làm họ sợ. Nó vẫn phải còn — nhưng ở
    trường ``detail`` dành cho người rành máy.
    """
    blocker = tmp_path / "khong-phai-thu-muc.txt"
    io_utils.write_text(blocker, "day la mot file", bom=False)

    response = client.post("/api/settings", json={"out_dir": str(blocker / "con")})
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert "Không dùng được thư mục" in error["message"]
    for english in ("Error", "error", "cannot", "denied", "directory"):
        assert english not in error["message"], f"câu lỗi còn lẫn chữ “{english}”"
    assert error.get("detail"), "chi tiết kỹ thuật phải còn, chỉ là để ở trường riêng"


# --------------------------------------------------------------------------- #
# 4. kiểm khoá API
# --------------------------------------------------------------------------- #

def test_test_key_without_a_key_explains_what_to_do(client: TestClient) -> None:
    """Chưa có khoá thì nói rõ là chưa có, và nhắc rằng để trống vẫn chạy được."""
    response = client.post("/api/settings/test-key", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert "mã API" in body["message"]
    assert "miễn phí" in body["message"]


def test_test_key_never_echoes_the_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dù hỏng kiểu gì, khoá cũng không được quay về trình duyệt nguyên văn."""
    secret = "AIzaSyTESTKEY0123456789abcd"

    class Boom(Exception):
        pass

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise Boom(f"HTTP 400 key={secret}")

    import srtgen.providers.gemini as gemini

    monkeypatch.setattr(gemini, "GeminiProvider", explode)
    response = client.post("/api/settings/test-key", json={"api_key": secret})
    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert response.json()["ok"] is False


# --------------------------------------------------------------------------- #
# 5. hai ô tick ở màn hình chính
# --------------------------------------------------------------------------- #

def test_options_reach_the_config(workspace: Path) -> None:
    """"Tách giọng khỏi nhạc nền" và "Dịch tiếng Việt" phải chạm được tới cấu hình.

    Thiếu đoạn nối này thì hai ô tick là nút giả: bỏ tick vẫn ra file tiếng Việt,
    bật tick vẫn không tách giọng — và không có một dòng thông báo nào.
    """
    job = Job(
        id="j1",
        source="https://example.invalid/v",
        options={"demucs": True, "translate": False, "no_translate": True},
    )
    cfg = web_server._config_for_job(job)
    assert cfg["audio"]["demucs"] is True
    assert cfg["translate"]["enabled"] is False


def test_options_left_alone_keep_the_defaults(workspace: Path) -> None:
    """Không nói gì thì theo ``default.yaml``, chứ không bị hiểu thành "tắt"."""
    job = Job(id="j2", source="https://example.invalid/v", options={"model": "small"})
    cfg = web_server._config_for_job(job)
    assert cfg["translate"]["enabled"] is True
    assert cfg["asr"]["model"] == "small"


# --------------------------------------------------------------------------- #
# 6. cờ mở trình duyệt
# --------------------------------------------------------------------------- #

def test_serve_takes_the_signature_the_cli_calls_first() -> None:
    """``cli._call_server`` thử ``(host, port, open_browser)`` trước tiên.

    Thiếu tham số ``host``, lời gọi đó ném ``TypeError``, ``cli.py`` lui xuống
    chữ ký ``{port}`` và cờ ``--no-browser`` biến mất trên đường đi.
    """
    import inspect

    params = inspect.signature(web_server.serve).parameters
    assert {"open_browser", "port", "host"} <= set(params)


def test_no_browser_env_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """``SRTGEN_NO_BROWSER`` khác rỗng = không mở trình duyệt."""
    monkeypatch.setenv(web_server.NO_BROWSER_ENV, "1")
    assert web_server._no_browser_requested() is True
    monkeypatch.setenv(web_server.NO_BROWSER_ENV, "")
    assert web_server._no_browser_requested() is False
    monkeypatch.delenv(web_server.NO_BROWSER_ENV, raising=False)
    assert web_server._no_browser_requested() is False


def test_no_cyrillic_identifiers_left(monkeypatch: pytest.MonkeyPatch) -> None:
    """Không còn tên hàm viết bằng chữ Nga lạc giữa code base tiếng Anh/tiếng Việt."""
    source = Path(web_server.__file__).read_text(encoding="utf-8")
    assert "_читать" not in source

# --------------------------------------------------------------------------- #
# 8. soát độc lập: tên gõ theo cụm, nút sửa của "Kiểm tra máy", tải đúng model
# --------------------------------------------------------------------------- #

def test_names_typed_as_clusters_are_saved_without_spaces(
    client: TestClient, workspace: Path
) -> None:
    """Ô "Chữ Hán" ghi cụm cách nhau bằng dấu cách ("光头 强", đúng chữ mẫu).

    Dấu cách đó là ranh giới cụm. Giữ nó trong ``names.json`` thì tên không bao
    giờ khớp chữ Hán trong phụ đề (không có dấu cách), dù giao diện đọc lại vẫn
    thấy y hệt thứ vừa gõ.
    """
    (workspace / "phim001").mkdir()
    client.put(
        "/api/names/phim001",
        json={"entries": [{"zh": "光头 强", "pinyin": "Guāngtóu Qiáng", "vi": "Cường đầu trọc"}]},
    )
    saved = names_json(workspace, "phim001")
    assert saved["entries"][0]["han"] == "光头强"
    assert saved["entries"][0]["split"] == ["光头", "强"]
    assert saved["entries"][0]["pinyin"] == ["Guāngtóu", "Qiáng"]
    assert saved["names"] == {"光头": "Guāngtóu", "强": "Qiáng"}
    assert all(" " not in key for key in saved["names"])


def test_saving_what_the_screen_shows_leaves_the_s7_table_unchanged(
    client: TestClient, workspace: Path
) -> None:
    """Mở tab Tên riêng rồi bấm Lưu mà không sửa gì: file S7 phải y nguyên.

    Trước đây một tên "陈路周" hiện thành ba dòng (hai khoá cụm của bảng phẳng
    thành hai "tên" riêng), và lần Lưu đó ghi lại ``han`` = "陈 路周", mất
    ``split``, đặt ``count`` về 0.
    """
    from srtgen.stages.s7_ai import NameEntry, save_names

    folder = workspace / "phim001"
    folder.mkdir()
    save_names(
        folder / "names.json",
        {"陈": "Chén", "路周": "Lùzhōu"},
        [NameEntry(han="陈路周", split=["陈", "路周"], pinyin=["Chén", "Lùzhōu"],
                   type="person", reason="AI", count=3)],
    )
    before = names_json(workspace, "phim001")

    shown = client.get("/api/names/phim001").json()["entries"]
    assert [" ".join(e["split"]) for e in shown] == ["陈 路周"]
    # đúng thứ saveNames() trong app.js gửi đi
    ui = [{"zh": " ".join(e["split"]), "pinyin": e["pinyin"], "vi": e["vi"]} for e in shown]
    client.put("/api/names/phim001", json={"entries": ui})

    after = names_json(workspace, "phim001")
    assert after["names"] == before["names"]
    assert after["entries"] == before["entries"]


def test_doctor_rows_carry_the_fix_button(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Giao diện dựng nút "… giúp tôi" từ ``fix_action``; ``cli.Check`` chỉ có ``fix``."""
    from srtgen import cli

    monkeypatch.setattr(cli, "doctor_report", lambda: [
        {"key": "yt-dlp", "status": "warn", "fix": "yt-dlp -U", "label": "yt-dlp"},
        {"key": "ffmpeg", "status": "fail", "fix": "brew install ffmpeg", "label": "ffmpeg"},
        {"key": "ai_key", "status": "info", "fix": "export GEMINI_API_KEY=x", "label": "AI", "ok": True},
        {"key": "python", "status": "ok", "fix": "", "label": "Python", "ok": True},
    ])
    checks = {c["key"]: c for c in client.get("/api/doctor").json()["checks"]}
    assert checks["yt-dlp"]["fix_action"] == "update_ytdlp"
    assert checks["yt-dlp"]["hint"] == "yt-dlp -U"
    assert checks["ffmpeg"]["fix_action"] == "install_ffmpeg"
    assert "fix_action" not in checks["ai_key"]
    assert "fix_action" not in checks["python"]


def test_download_model_fetches_the_model_the_user_clicked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nút "Tải về" cạnh một model gửi ``{model}``; máy chủ phải tải đúng model đó."""
    import time as _time

    import srtgen.stages.s2_asr as asr

    asked: list[str] = []
    monkeypatch.setattr(
        asr, "model_status",
        lambda model, cfg=None: asked.append(model) or {"downloaded": True, "label": model, "path": ""},
    )
    client.post("/api/settings", json={"model": "small"})

    def run(body: dict[str, Any]) -> str:
        asked.clear()
        job_id = client.post("/api/actions/download_model", json=body).json()["job_id"]
        for _ in range(250):
            snap = client.get(f"/api/jobs/{job_id}").json()["job"]
            if not snap["active"]:
                break
            _time.sleep(0.02)
        assert snap["status"] == "done", snap
        return asked[-1]

    assert run({"model": "medium"}) == "medium"
    # giá trị lạ không bao giờ đi tiếp: quay về model trong Cài đặt đã lưu
    assert run({"model": "../../etc/passwd"}) == "small"
    assert run({}) == "small"
