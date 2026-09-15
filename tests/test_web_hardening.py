"""Tầng web: bảy lỗi của `server.py` / `jobs.py` / cấu hình, mỗi lỗi một nhóm test.

Người dùng cuối không phải dân IT, nên cả bảy lỗi đều là loại tệ nhất: chúng
**không báo gì**. Người dùng chỉ thấy kết quả sai, hoặc thấy dữ liệu của mình mất.

1. `/api/fix` giữ một bản sửa file riêng (`fix_srt_text` + `split_bilingual_srt`)
   nên cho ra file KHÁC lệnh `srtgen fix` trên cùng đầu vào. Nay nó gọi
   `pipeline.run_fix_bilingual` (hợp đồng A): cùng file → cùng từng byte.
2. Bấm Lưu trong trình sửa chỉ ghi `.srt`/`_vi.srt`/`.bundle.json`, bỏ quên
   `_song-ngu.ass` — đúng file mà HUONG-DAN dặn mở trong Aegisub để soát.
3. `names.json` hỏng cú pháp: GET báo "bảng đang trống", PUT ghi đè → mất sạch tên.
4. Dán `www.youtube.com/watch?v=…` (thiếu `https://`): giao diện coi là link,
   máy chủ trả "Không tìm thấy file … trên máy".
5. Hợp đồng D: `web.max_media_bytes` phải có trong `default.yaml` và `/api/health`.
6. `pyproject.toml`: extra `web` ghim khớp bộ cài Mac; thư mục dữ liệu khai báo
   đúng là package để setuptools không cảnh báo (và không bỏ file ở bản sau).
7. Nút "Sinh lại pinyin" dùng bộ tách thiếu bảng tên nguyên khối của S5, nên
   một từ trong từ điển vắt qua ranh giới tên: `光头强大喊` ra `光头 | 强大 | 喊`
   (mất luôn chữ hoa của tên) dù bảng tên có 光头强 = 光头 | 强.

Ngoại tuyến hoàn toàn: không mạng, không model, không mở cửa sổ. Hàng đợi công
việc là bản không bao giờ chạy (`IdleManager`), thư mục làm việc / kết quả / file
cài đặt đều nằm trong `tmp_path`.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402

from srtgen import io_utils  # noqa: E402
from srtgen.core.context import load_config, make_video_id  # noqa: E402
from srtgen.core.srt import parse_srt, parse_srt_document  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402
from srtgen.web.jobs import JobManager  # noqa: E402

# --------------------------------------------------------------------------- #
# dựng máy chủ trong thư mục tạm
# --------------------------------------------------------------------------- #


class IdleManager(JobManager):
    """Hàng đợi nhận việc nhưng không bao giờ chạy — không yt-dlp, không ffmpeg, không mạng."""

    def _ensure_worker(self) -> None:
        return None


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`work/`, thư mục kết quả và file cài đặt đều trỏ vào `tmp_path`."""
    root = tmp_path / "work"
    root.mkdir()
    out = tmp_path / "out"
    monkeypatch.setattr(web_server, "work_root", lambda cfg=None: root)
    monkeypatch.setattr(web_server, "settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(web_server, "_effective_out_dir", lambda cfg, settings: str(out))
    monkeypatch.setattr(web_server, "reveal_in_file_manager", lambda path: None)
    monkeypatch.setattr(web_server, "open_document", lambda path: None)
    return root


@pytest.fixture
def manager(tmp_path: Path) -> Iterator[IdleManager]:
    jobs = IdleManager(state_dir=tmp_path / "jobs", autoload=False)
    yield jobs
    jobs.shutdown(wait=0.5)


@pytest.fixture
def client(workspace: Path, manager: IdleManager) -> Iterator[TestClient]:
    app = web_server.create_app(manager)
    with TestClient(app, base_url="http://127.0.0.1") as http:
        yield http


def movie(workspace: Path, video_id: str) -> Path:
    """Một thư mục phim trong `work/`, như sau một lần chạy."""
    folder = workspace / video_id
    folder.mkdir()
    io_utils.write_json(folder / "S0_info.json", {"title": video_id})
    return folder


# --------------------------------------------------------------------------- #
# 1. /api/fix = pipeline.run_fix_bilingual
# --------------------------------------------------------------------------- #

#: Ba kiểu block mà bản riêng cũ của máy chủ xử lý khác `run_fix`:
#: block 3 dòng Hán/pinyin/Việt; block 2 dòng Hán **chưa phân cụm**/Việt (bản cũ
#: không cho jieba tách, ra một cụm dài); và `Anh là ai?` — không có chữ "riêng
#: của tiếng Việt" nên bản cũ giữ nó lại làm pinyin.
TRILINGUAL = """1
00:00:01,000 --> 00:00:02,500
你好 世界。
Nǐhǎo shìjiè。
Chào thế giới.

2
00:00:02,500 --> 00:00:04,000
我来中国只有一个目的。
Tôi đến Trung Quốc chỉ có một mục đích.

3
00:00:04,000 --> 00:00:06,000
你 是 谁？
Anh là ai?
"""


def post_fix(client: TestClient, text: str, filename: str = "tap1.srt", **params: str) -> dict[str, Any]:
    response = client.post("/api/fix", params=params, json={"filename": filename, "text": text})
    assert response.status_code == 200, response.text
    return response.json()


def test_api_fix_equals_run_fix_on_corpus_filter(
    client: TestClient, filter_text: str, fixed_filter_srt: str
) -> None:
    body = post_fix(client, filter_text, "filter.srt")
    assert body["text"] == fixed_filter_srt
    assert body["has_vi"] is False
    assert body["set_aside"] == []
    assert [f for f in body["findings"] if f["severity"] == "error"] == []


def test_api_fix_equals_run_fix_on_corpus_raw(
    client: TestClient, raw_text: str, cfg: dict[str, Any]
) -> None:
    from srtgen.pipeline import run_fix

    expected, findings = run_fix(raw_text, cfg)
    body = post_fix(client, raw_text, "raw.srt")
    assert body["text"] == expected
    assert body["findings"] == [f.to_dict() for f in findings]
    assert [f for f in body["findings"] if f["severity"] == "error"] == []


def test_api_fix_equals_run_fix_on_a_three_line_bilingual_file(
    client: TestClient, cfg: dict[str, Any]
) -> None:
    from srtgen.pipeline import run_fix, run_fix_bilingual

    expected_text, _ = run_fix(TRILINGUAL, cfg)
    expected = run_fix_bilingual(TRILINGUAL, cfg)
    body = post_fix(client, TRILINGUAL)

    assert body["text"] == expected_text == expected.text
    assert body["vi_text"] == expected.vi_text
    assert body["has_vi"] is True
    assert body["findings"] == [f.to_dict() for f in expected.findings]
    # Không chữ Việt nào lọt vào file Hán/pinyin, không câu Việt nào mất.
    assert "Anh là ai?" not in body["text"]
    assert [" ".join(b.lines) for b in parse_srt(body["vi_text"])] == [
        "Chào thế giới.",
        "Tôi đến Trung Quốc chỉ có một mục đích.",
        "Anh là ai?",
    ]
    # File ghi ra đĩa cũng đúng là văn bản đó.
    assert io_utils.read_text(body["path"]) == expected.text
    assert io_utils.read_text(body["vi_path"]) == expected.vi_text
    assert body["vi_filename"] == "tap1.da-chuan-hoa_vi.srt"


def test_api_fix_goes_through_the_one_pipeline_function(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Một lần gọi `run_fix_bilingual` mỗi file; có mã phim thì truyền bảng tên của phim đó."""
    import srtgen.pipeline as pipeline

    calls: list[Any] = []
    real = pipeline.run_fix_bilingual

    def spy(text: str, cfg: Any = None, *, names: Any = None) -> Any:
        calls.append(names)
        return real(text, cfg, names=names)

    monkeypatch.setattr(pipeline, "run_fix_bilingual", spy)

    post_fix(client, TRILINGUAL)
    assert calls == [None]  # không có phim: bảng tên của cấu hình, đúng như CLI

    folder = movie(workspace, "phim_fix")
    io_utils.write_json(folder / "names.json", {"names": {"光头强": "Guāngtóuqiáng"}})
    post_fix(client, TRILINGUAL, video_id="phim_fix")
    assert calls[-1] == {"光头强": "Guāngtóuqiáng"}


def test_the_private_copy_of_the_fix_logic_is_gone() -> None:
    for name in ("fix_srt_text", "split_bilingual_srt", "BilingualSplit", "_render_fixed_vi"):
        assert not hasattr(web_server, name), name


def test_set_aside_rows_keep_cue_and_reason(client: TestClient) -> None:
    stray = "Wǒ lái Zhōngguó"
    text = TRILINGUAL.replace("Anh là ai?\n", f"Anh là ai?\n{stray}\n")
    body = post_fix(client, text)
    (row,) = body["set_aside"]
    assert row["text"] == stray
    assert row["cue"] == 3
    assert stray in row["reason"]


# --------------------------------------------------------------------------- #
# 2. Lưu trong trình sửa dựng lại .ass và _song-ngu.ass
# --------------------------------------------------------------------------- #

ZH_SRT = """1
00:00:01,000 --> 00:00:02,500
你好 世界。
Nǐhǎo shìjiè。

2
00:00:02,500 --> 00:00:04,000
我 来 中国 只有 一个 目的。
Wǒ lái Zhōngguó zhǐyǒu yígè mùdì。

3
00:00:04,000 --> 00:00:06,000
- 你 是 谁？
- Nǐ shì shéi？
"""

VI_SRT = """1
00:00:01,000 --> 00:00:02,500
Chào thế giới.

2
00:00:02,500 --> 00:00:04,000
Tôi đến Trung Quốc chỉ có một mục đích.

3
00:00:04,000 --> 00:00:06,000
- Anh là ai?
"""

STALE = "[Script Info]\nTitle: BAN CU\n"


@pytest.fixture
def pair(tmp_path: Path) -> Path:
    """Cặp `.srt`/`_vi.srt` có sẵn trên máy, như file bên dịch gửi sang."""
    folder = tmp_path / "phim"
    folder.mkdir()
    io_utils.write_text(folder / "tap1.srt", ZH_SRT, bom=True)
    io_utils.write_text(folder / "tap1_vi.srt", VI_SRT, bom=True)
    return folder / "tap1.srt"


@pytest.fixture
def pair_with_ass(pair: Path) -> Path:
    """Như trên, kèm hai file soát đã cũ — đúng tình huống HUONG-DAN mô tả."""
    io_utils.write_text(pair.with_name("tap1.ass"), STALE, bom=True)
    io_utils.write_text(pair.with_name("tap1_song-ngu.ass"), STALE, bom=True)
    return pair


def open_pair(client: TestClient, srt: Path) -> str:
    response = client.post("/api/open-local", json={"path": str(srt)})
    assert response.status_code == 201, response.text
    return response.json()["job"]["id"]


def edit_and_save(client: TestClient, job_id: str) -> dict[str, Any]:
    doc = client.get(f"/api/jobs/{job_id}/doc").json()
    cues = [dict(cue) for cue in doc["cues"]]
    cues[0]["zh_line"] = "你们 好。"
    cues[0]["py_line"] = "Nǐmen hǎo。"
    cues[0]["vi_line"] = "Chào các bạn."
    response = client.put(f"/api/jobs/{job_id}/doc", json={"cues": cues})
    assert response.status_code == 200, response.text
    return response.json()


def dialogues(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.startswith("Dialogue:")]


def test_save_rebuilds_both_ass_files_with_the_emit_stage_functions(
    client: TestClient, pair_with_ass: Path
) -> None:
    from srtgen.stages.s9_emit import build_ass, build_bilingual_ass

    body = edit_and_save(client, open_pair(client, pair_with_ass))

    bilingual = io_utils.read_text(pair_with_ass.with_name("tap1_song-ngu.ass"))
    single = io_utils.read_text(pair_with_ass.with_name("tap1.ass"))
    assert "BAN CU" not in bilingual and "BAN CU" not in single
    assert "你们 好。" in bilingual and "Nǐmen hǎo。" in bilingual and "Chào các bạn." in bilingual
    assert "你们 好。" in single and "Chào các bạn." not in single

    # Dựng bằng đúng hai hàm của S9, từ đúng hai file vừa lưu.
    saved_zh = io_utils.read_text(pair_with_ass)
    saved_vi = io_utils.read_text(pair_with_ass.with_name("tap1_vi.srt"))
    doc = parse_srt_document(saved_zh)
    vi_lines = [" ".join(b.lines) for b in parse_srt(saved_vi)]
    emit = load_config()["emit"]
    assert dialogues(bilingual) == dialogues(build_bilingual_ass(doc, vi_lines, cfg=emit))
    assert dialogues(single) == dialogues(build_ass(doc, cfg=emit))
    assert len(dialogues(bilingual)) == 3

    assert any("tap1_song-ngu.ass" in note for note in body["notes"])
    assert body["job"]["downloads"].get("bilingual_ass")


def test_save_does_not_create_ass_files_beside_a_users_own_pair(
    client: TestClient, pair: Path
) -> None:
    edit_and_save(client, open_pair(client, pair))
    assert sorted(p.name for p in pair.parent.glob("*.ass")) == []


def test_save_creates_the_bilingual_ass_for_a_file_the_tool_wrote(
    client: TestClient, tmp_path: Path
) -> None:
    """File do tool xuất (ở đây: qua `/api/fix`) mà chưa có `_song-ngu.ass`: Lưu tạo nó."""
    fixed = post_fix(client, TRILINGUAL)
    job_id = fixed["job"]["id"]
    edit_and_save(client, job_id)

    target = tmp_path / "out" / "tap1.da-chuan-hoa_song-ngu.ass"
    assert target.is_file()
    assert "Chào các bạn." in io_utils.read_text(target)
    job = client.get(f"/api/jobs/{job_id}").json()["job"]
    assert job["downloads"].get("bilingual_ass")
    # `.ass` một ngôn ngữ không được tự đẻ ra (cấu hình mặc định tắt).
    assert not (tmp_path / "out" / "tap1.da-chuan-hoa.ass").exists()


def test_revert_rebuilds_the_bilingual_ass_too(client: TestClient, pair_with_ass: Path) -> None:
    job_id = open_pair(client, pair_with_ass)
    edit_and_save(client, job_id)
    response = client.put(f"/api/jobs/{job_id}/doc", json={"revert": True})
    assert response.status_code == 200, response.text

    bilingual = io_utils.read_text(pair_with_ass.with_name("tap1_song-ngu.ass"))
    assert "Chào thế giới." in bilingual
    assert "Chào các bạn." not in bilingual


def test_an_ass_file_that_cannot_be_written_does_not_break_the_save(
    client: TestClient, pair_with_ass: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aegisub trên Windows khoá file đang mở: `.srt` vẫn phải lưu, và phải NÓI ra."""
    real = io_utils.atomic_write_text

    def locked(path: Any, text: str, **kwargs: Any) -> None:
        if str(path).endswith(".ass"):
            raise PermissionError("file đang mở trong Aegisub")
        real(path, text, **kwargs)

    monkeypatch.setattr(io_utils, "atomic_write_text", locked)
    body = edit_and_save(client, open_pair(client, pair_with_ass))

    assert "你们 好。" in io_utils.read_text(pair_with_ass)
    assert any("Chưa cập nhật được" in note for note in body["notes"])
    assert "BAN CU" in io_utils.read_text(pair_with_ass.with_name("tap1_song-ngu.ass"))


# --------------------------------------------------------------------------- #
# 3. names.json hỏng: GET nói ra, PUT cất bản sao trước khi ghi
# --------------------------------------------------------------------------- #

ENTRY = {"zh": "光头强", "pinyin": "Guāngtóuqiáng", "vi": "Cường đầu trọc"}

#: Lỗi sửa tay hay gặp nhất: thừa một dấu phẩy.
BROKEN = '{"names": {"光头强": "Guāngtóuqiáng", "熊大": "Xióngdà",}}'


def broken_movie(workspace: Path, video_id: str, content: str = BROKEN) -> Path:
    folder = movie(workspace, video_id)
    io_utils.write_text(folder / "names.json", content, bom=False)
    return folder


def test_get_says_a_broken_table_is_broken_not_empty(client: TestClient, workspace: Path) -> None:
    broken_movie(workspace, "phim_hong")
    body = client.get("/api/names/phim_hong").json()
    assert body["exists"] is True
    assert body["corrupt"] is True
    assert body["entries"] == []
    assert "names.json" in body["corrupt_message"]
    assert "lỗi cú pháp" in body["corrupt_message"]
    assert body["message"] == body["corrupt_message"]


def test_a_healthy_table_is_not_flagged(client: TestClient, workspace: Path) -> None:
    movie(workspace, "phim_lanh")
    client.put("/api/names/phim_lanh", json={"entries": [ENTRY]})
    body = client.get("/api/names/phim_lanh").json()
    assert body["corrupt"] is False and body["corrupt_message"] == ""
    assert "message" not in body


def test_put_keeps_the_broken_file_as_a_dated_backup(client: TestClient, workspace: Path) -> None:
    folder = broken_movie(workspace, "phim_hong")
    before = (folder / "names.json").read_bytes()

    response = client.put("/api/names/phim_hong", json={"entries": [ENTRY]})
    assert response.status_code == 200, response.text

    (backup,) = sorted(folder.glob("names.json.hong-*"))
    assert re.fullmatch(r"names\.json\.hong-\d{8}-\d{6}", backup.name)
    assert backup.read_bytes() == before  # từng byte của người dùng còn nguyên
    body = response.json()
    assert body["backup_name"] == backup.name
    assert backup.name in body["message"]

    again = client.get("/api/names/phim_hong").json()
    assert again["corrupt"] is False
    assert [(e["zh"], e["vi"]) for e in again["entries"]] == [("光头强", "Cường đầu trọc")]


def test_two_saves_in_the_same_second_never_overwrite_a_backup(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = broken_movie(workspace, "phim_hong")
    monkeypatch.setattr(web_server.time, "strftime", lambda fmt, *a: "20260910-120000")
    client.put("/api/names/phim_hong", json={"entries": [ENTRY]})
    io_utils.write_text(folder / "names.json", "[1, 2", bom=False)  # hỏng lần nữa
    client.put("/api/names/phim_hong", json={"entries": [ENTRY]})
    assert sorted(p.name for p in folder.glob("names.json.hong-*")) == [
        "names.json.hong-20260910-120000",
        "names.json.hong-20260910-120000-1",
    ]


def test_a_table_that_is_not_a_mapping_is_also_kept(client: TestClient, workspace: Path) -> None:
    """Một danh sách JSON đọc được nhưng màn hình không hiện được — ghi đè cũng là mất."""
    folder = broken_movie(workspace, "phim_list", '["光头强", "熊大"]')
    assert client.get("/api/names/phim_list").json()["corrupt"] is True
    client.put("/api/names/phim_list", json={"entries": [ENTRY]})
    (backup,) = folder.glob("names.json.hong-*")
    assert backup.read_text(encoding="utf-8") == '["光头强", "熊大"]\n'


def test_put_on_a_healthy_table_makes_no_backup(client: TestClient, workspace: Path) -> None:
    folder = movie(workspace, "phim_lanh")
    client.put("/api/names/phim_lanh", json={"entries": [ENTRY]})
    client.put("/api/names/phim_lanh", json={"entries": [ENTRY]})
    assert list(folder.glob("names.json.hong-*")) == []


def test_if_the_backup_cannot_be_made_nothing_is_written(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = broken_movie(workspace, "phim_hong")
    before = (folder / "names.json").read_bytes()

    def refuse(self: Path, target: Any) -> Any:
        raise PermissionError("khoá")

    monkeypatch.setattr(Path, "rename", refuse)
    response = client.put("/api/names/phim_hong", json={"entries": [ENTRY]})
    assert response.status_code >= 400
    assert "chưa lưu gì" in response.json()["error"]["message"]
    assert (folder / "names.json").read_bytes() == before
    assert list(folder.glob("names.json.hong-*")) == []


def test_the_movie_list_marks_a_broken_table(client: TestClient, workspace: Path) -> None:
    broken_movie(workspace, "phim_hong")
    movies = {m["video_id"]: m for m in client.get("/api/names").json()["movies"]}
    assert movies["phim_hong"]["corrupt"] is True


# --------------------------------------------------------------------------- #
# 4. link dán thiếu https://
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        ("www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("youtube.com/watch?v=dQw4w9WgXcQ", "https://youtube.com/watch?v=dQw4w9WgXcQ"),
        ("youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"),
        ("m.youtube.com/shorts/dQw4w9WgXcQ", "https://m.youtube.com/shorts/dQw4w9WgXcQ"),
        ("  www.youtube.com/watch?v=dQw4w9WgXcQ \n", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ('"youtu.be/dQw4w9WgXcQ"', "https://youtu.be/dQw4w9WgXcQ"),
        # đã có scheme: không đụng
        ("https://youtu.be/dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ"),
        ("http://example.com/a.mp4", "http://example.com/a.mp4"),
        # không phải dạng tên.miền/…: không đụng
        ("tap1.mp4", "tap1.mp4"),
        ("C:\\phim\\tap1.mp4", "C:\\phim\\tap1.mp4"),
        ("/Users/an/phim/tap1.mp4", "/Users/an/phim/tap1.mp4"),
        ("~/phim/tap1.mp4", "~/phim/tap1.mp4"),
        ("localhost/x", "localhost/x"),
        ("", ""),
    ],
)
def test_normalize_source(pasted: str, expected: str) -> None:
    from srtgen.web.jobs import normalize_source

    assert normalize_source(pasted) == expected


def test_a_real_folder_that_looks_like_a_domain_stays_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from srtgen.web.jobs import normalize_source

    monkeypatch.chdir(tmp_path)
    (tmp_path / "phim.backup").mkdir()
    (tmp_path / "phim.backup" / "tap1.mp4").write_bytes(b"x")
    assert normalize_source("phim.backup/tap1.mp4") == "phim.backup/tap1.mp4"


def test_the_normalized_link_gets_the_youtube_video_id() -> None:
    from srtgen.web.jobs import normalize_source

    assert make_video_id(normalize_source("www.youtube.com/watch?v=dQw4w9WgXcQ")) == "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "pasted",
    ["www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube.com/watch?v=dQw4w9WgXcQ", "youtu.be/dQw4w9WgXcQ"],
)
def test_api_accepts_a_link_without_https(client: TestClient, pasted: str) -> None:
    response = client.post("/api/jobs", json={"source": pasted})
    assert response.status_code == 201, response.text
    assert response.json()["job"]["source"] == "https://" + pasted


def test_api_still_says_a_missing_file_is_missing(client: TestClient) -> None:
    response = client.post("/api/jobs", json={"source": "tap_khong_co.mp4"})
    assert response.status_code == 400
    assert "Không tìm thấy file" in response.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# 5. hợp đồng D: web.max_media_bytes
# --------------------------------------------------------------------------- #

def test_default_config_declares_max_media_bytes(repo_root: Path) -> None:
    assert load_config()["web"]["max_media_bytes"] == 4294967296
    text = io_utils.read_text(repo_root / "srtgen" / "config" / "default.yaml")
    section = text[text.index("\nweb:") : text.index("max_media_bytes: 4294967296")]
    assert "Dung lượng tối đa" in section  # chú thích tiếng Việt ngay trên khoá


def test_health_reports_max_media_bytes_from_the_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/api/health").json()["max_media_bytes"] == 4294967296
    real = web_server.load_config
    monkeypatch.setattr(
        web_server,
        "load_config",
        lambda profile=None: {**real(profile), "web": {"max_media_bytes": 123_456}},
    )
    assert client.get("/api/health").json()["max_media_bytes"] == 123_456


# --------------------------------------------------------------------------- #
# 6. pyproject.toml
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def pyproject(repo_root: Path) -> dict[str, Any]:
    tomllib = pytest.importorskip("tomllib")
    return tomllib.loads(io_utils.read_text(repo_root / "pyproject.toml"))


def test_web_extra_is_pinned_like_the_mac_installer(repo_root: Path, pyproject: dict[str, Any]) -> None:
    pins: dict[str, str] = {}
    for line in io_utils.read_text(repo_root / "installer" / "requirements-macos.txt").split("\n"):
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[name.strip().lower()] = version.strip()
    wanted = sorted(f"{name}=={pins[name]}" for name in ("fastapi", "uvicorn", "python-multipart"))
    assert sorted(pyproject["project"]["optional-dependencies"]["web"]) == wanted


def test_every_data_folder_is_a_declared_package(repo_root: Path, pyproject: dict[str, Any]) -> None:
    """Thư mục có file dữ liệu được ship mà không khai báo là package → setuptools cảnh báo
    "is absent from the `packages` configuration" và sẽ bỏ file đó ở bản sau.

    Luật kiểm đúng như setuptools: mọi thư mục CÓ THẬT mà `package-data` lấy file
    từ đó phải nằm trong `packages`; và mọi file giao diện phải khớp một mẫu.
    """
    setup = pyproject["tool"]["setuptools"]
    packages = set(setup["packages"])
    assert "srtgen.web.static" in packages
    for package, patterns in setup["package-data"].items():
        assert package in packages, package
        base = repo_root.joinpath(*package.split("."))
        for pattern in patterns:
            folder = base.joinpath(*pattern.split("/")[:-1])
            if folder.is_dir():
                dotted = ".".join(folder.relative_to(repo_root).parts)
                assert dotted in packages, f"{dotted} thiếu trong packages"

    static = repo_root / "srtgen" / "web" / "static"
    patterns = [
        package.replace(".", "/") + "/" + pat
        for package, pats in setup["package-data"].items()
        for pat in pats
    ]
    shipped = sorted(p.name for p in static.iterdir() if p.is_file())
    assert "index.html" in shipped
    for name in shipped:
        assert any(fnmatch.fnmatch(f"srtgen/web/static/{name}", pat) for pat in patterns), name


# --------------------------------------------------------------------------- #
# 7. /api/retokenize dùng đúng bộ tách của S5
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("sentence", "rest"),
    [
        ("光头强来了", None),
        # 强大 là một từ trong từ điển jieba: thiếu bảng tên nguyên khối thì nó
        # thắng và vắt qua ranh giới tên — bản cũ ra `光头 | 强大 | 喊`.
        ("光头强大喊", ["大喊"]),
    ],
)
def test_retokenize_keeps_a_name_whose_last_cluster_is_one_character(
    client: TestClient, workspace: Path, sentence: str, rest: list[str] | None
) -> None:
    folder = movie(workspace, "phim_gtq")
    io_utils.write_json(
        folder / "names.json",
        {
            "version": 1,
            "names": {"光头": "Guāngtóu", "强": "Qiáng"},
            "entries": [
                {"han": "光头强", "split": ["光头", "强"], "pinyin": ["Guāngtóu", "Qiáng"],
                 "vi": "Cường đầu trọc"}
            ],
        },
    )
    response = client.post("/api/retokenize", json={"zh": sentence, "video_id": "phim_gtq"})
    assert response.status_code == 200, response.text
    body = response.json()
    words = [t["text"] for t in body["tokens"] if t["kind"] == "word"]
    assert words[:2] == ["光头", "强"], words
    assert not any(w.startswith("强") and len(w) > 1 for w in words), words
    if rest is not None:
        assert words[2:] == rest
    assert body["py_line"].startswith("Guāngtóu Qiáng ")
    assert len(body["py_line"].split()) == len(body["zh_line"].split())
