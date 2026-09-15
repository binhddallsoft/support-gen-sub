"""Bảng tên riêng: lưu rồi đọc lại phải ra **đúng từng ký tự**, và mọi phim đều lập được bảng.

Hai lỗi mà bộ test này chặn tái diễn, cả hai đều hỏng trong im lặng:

* Tên tiếng Việt từng bị đưa qua `strip_tones` như pinyin: "Cường đầu trọc" lưu
  xong đọc lại thành "Cuong Đau Troc". Bảng thuật ngữ của chặng dịch đọc đúng
  trường này, nên tên nhân vật sai theo suốt 40 phút phim.
* `GET /api/names` chỉ liệt kê phim đã có `names.json`, mà file đó chỉ AI ghi
  ra — người dùng mặc định không có khoá API nên **không bao giờ** lập được bảng.

Ngoại tuyến hoàn toàn; thư mục làm việc và file cài đặt trỏ vào `tmp_path`.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402

from srtgen import io_utils  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402

ENTRY = {"zh": "光头强", "pinyin": "Guāngtóuqiáng", "vi": "Cường đầu trọc"}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.setattr(web_server, "work_root", lambda cfg=None: root)
    monkeypatch.setattr(web_server, "settings_path", lambda: tmp_path / "settings.json")
    return root


@pytest.fixture
def client(tmp_path: Path, workspace: Path) -> Iterator[TestClient]:
    from srtgen.web.jobs import JobManager

    manager = JobManager(state_dir=tmp_path / "jobs", autoload=False)
    app = web_server.create_app(manager)
    with TestClient(app, base_url="http://127.0.0.1") as http:
        yield http
    manager.shutdown(wait=1.0)


def movie(workspace: Path, video_id: str, title: str = "") -> Path:
    folder = workspace / video_id
    folder.mkdir()
    io_utils.write_json(folder / "S0_info.json", {"title": title or video_id})
    return folder


def names_on_disk(folder: Path) -> dict[str, Any]:
    return json.loads((folder / "names.json").read_text(encoding="utf-8-sig"))


# --------------------------------------------------------------------------- #
# lưu → đọc lại đúng từng ký tự
# --------------------------------------------------------------------------- #

def test_put_then_get_returns_every_character_including_vietnamese_tones(
    client: TestClient, workspace: Path
) -> None:
    """**Bài test của đề bài**: PUT {zh, pinyin, vi} rồi GET phải ra đúng từng ký tự."""
    folder = movie(workspace, "phim001")
    response = client.put("/api/names/phim001", json={"entries": [ENTRY]})
    assert response.status_code == 200, response.text

    (back,) = client.get("/api/names/phim001").json()["entries"]
    assert back["zh"] == "光头强"
    assert back["pinyin"] == "Guāngtóuqiáng"
    assert back["vi"] == "Cường đầu trọc"
    assert back["vi"].encode("utf-8") == "Cường đầu trọc".encode("utf-8")
    assert back["vi"] != "Cuong Đau Troc"

    (stored,) = names_on_disk(folder)["entries"]
    assert stored["vi"] == "Cường đầu trọc"
    assert stored["pinyin"] == ["Guāngtóuqiáng"]


def test_vietnamese_name_is_only_nfc_normalised(client: TestClient, workspace: Path) -> None:
    """macOS gửi chữ có dấu dạng NFD: lưu thành NFC, và **không** sửa gì khác."""
    movie(workspace, "phim002")
    rows = [
        {"zh": "熊大", "pinyin": "Xióngdà", "vi": unicodedata.normalize("NFD", "Gấu Đại")},
        {"zh": "熊二", "pinyin": "Xióngèr", "vi": "gấu Ú (LỒNG TIẾNG)"},
    ]
    client.put("/api/names/phim002", json={"entries": rows})
    back = client.get("/api/names/phim002").json()["entries"]
    assert [e["vi"] for e in back] == ["Gấu Đại", "gấu Ú (LỒNG TIẾNG)"]
    assert unicodedata.is_normalized("NFC", back[0]["vi"])


def test_saving_twice_keeps_the_vietnamese_name(client: TestClient, workspace: Path) -> None:
    movie(workspace, "phim003")
    client.put("/api/names/phim003", json={"entries": [ENTRY]})
    again = client.get("/api/names/phim003").json()["entries"]
    client.put("/api/names/phim003", json={"entries": again})
    (back,) = client.get("/api/names/phim003").json()["entries"]
    assert back["vi"] == "Cường đầu trọc"


def test_hand_written_nested_vietnamese_name_survives_a_save(
    client: TestClient, workspace: Path
) -> None:
    """Người dùng tự sửa `names.json` kiểu lồng: mở ra phải thấy, bấm Lưu không được xoá."""
    folder = movie(workspace, "phim004")
    io_utils.write_json(
        folder / "names.json",
        {"names": {"光头强": {"pinyin": "Guāngtóuqiáng", "vi": "Cường đầu trọc"}}},
    )
    entries = client.get("/api/names/phim004").json()["entries"]
    assert [(e["zh"], e["vi"]) for e in entries] == [("光头强", "Cường đầu trọc")]

    client.put("/api/names/phim004", json={"entries": entries})
    assert [e["vi"] for e in names_on_disk(folder)["entries"]] == ["Cường đầu trọc"]


# --------------------------------------------------------------------------- #
# danh sách phim + tạo bảng rỗng
# --------------------------------------------------------------------------- #

def test_index_lists_every_movie_even_without_a_name_table(
    client: TestClient, workspace: Path
) -> None:
    movie(workspace, "co_bang", "Phim đã có bảng")
    client.put("/api/names/co_bang", json={"entries": [ENTRY]})
    movie(workspace, "chua_bang", "Phim chưa có bảng")
    opened = workspace / "mo_tu_file"
    opened.mkdir()
    io_utils.write_text(opened / "Tập 5.srt", "1\n00:00:01,000 --> 00:00:02,000\n你好\nNǐhǎo\n")
    (workspace / "rong").mkdir()                      # thư mục trống: không phải phim
    uploads = workspace / "_uploads"                  # thư mục của tool: không phải phim
    uploads.mkdir()
    (uploads / ("a" * 32 + ".mp4")).write_bytes(b"x")

    movies = {m["video_id"]: m for m in client.get("/api/names").json()["movies"]}
    assert set(movies) == {"co_bang", "chua_bang", "mo_tu_file"}
    assert movies["chua_bang"] == {"video_id": "chua_bang", "title": "Phim chưa có bảng", "count": 0}
    assert movies["co_bang"]["count"] == 1
    assert movies["mo_tu_file"]["title"] == "Tập 5"


def test_an_empty_table_can_be_created_for_a_movie_without_one(
    client: TestClient, workspace: Path
) -> None:
    folder = movie(workspace, "phim_moi", "Phim mới")
    before = client.get("/api/names/phim_moi").json()
    assert before["exists"] is False and before["entries"] == []

    response = client.put("/api/names/phim_moi", json={"entries": []})
    assert response.status_code == 200, response.text
    assert (folder / "names.json").is_file()
    after = client.get("/api/names/phim_moi").json()
    assert after["exists"] is True and after["entries"] == []

    client.put("/api/names/phim_moi", json={"entries": [ENTRY]})
    assert [e["vi"] for e in client.get("/api/names/phim_moi").json()["entries"]] == [
        "Cường đầu trọc"
    ]


def test_saving_a_table_for_an_unknown_movie_is_refused(
    client: TestClient, workspace: Path
) -> None:
    """Mã gõ nhầm không được âm thầm đẻ ra một "phim" rỗng trong danh sách."""
    response = client.put("/api/names/khong_co_phim_nay", json={"entries": [ENTRY]})
    assert response.status_code == 404, response.text
    assert not (workspace / "khong_co_phim_nay").exists()


# --------------------------------------------------------------------------- #
# soát độc lập: hai đường mất dữ liệu trong im lặng
# --------------------------------------------------------------------------- #

def test_malformed_or_empty_body_never_wipes_the_table(
    client: TestClient, workspace: Path
) -> None:
    """Thân PUT hỏng/rỗng từng bị hiểu là "xoá hết" và trả 200."""
    folder = movie(workspace, "phim_hong")
    client.put("/api/names/phim_hong", json={"entries": [ENTRY]})
    for kwargs in (
        {"content": b"{hong", "headers": {"content-type": "application/json"}},
        {},
    ):
        response = client.put("/api/names/phim_hong", **kwargs)
        assert response.status_code >= 400 or response.json().get("ok") is False
    assert [e["vi"] for e in names_on_disk(folder)["entries"]] == ["Cường đầu trọc"]
    # Xoá hết có chủ ý vẫn phải được.
    assert client.put("/api/names/phim_hong", json={"entries": []}).status_code == 200
    assert names_on_disk(folder)["entries"] == []


def test_vietnamese_under_an_alias_key_shows_and_survives_a_save(
    client: TestClient, workspace: Path
) -> None:
    """File sửa tay ghi `"vietnamese"`: S8 dùng được thì màn hình phải thấy, Lưu không được xoá."""
    from srtgen.stages.s8_translate import build_glossary

    folder = movie(workspace, "phim_tay")
    io_utils.write_json(
        folder / "names.json",
        {
            "names": {"光头强": "Guāngtóuqiáng"},
            "entries": [
                {"han": "光头强", "split": ["光头强"], "pinyin": ["Guāngtóuqiáng"],
                 "vietnamese": "Cường đầu trọc"}
            ],
        },
    )
    assert build_glossary(folder / "names.json")[0] == {"光头强": "Cường đầu trọc"}
    entries = client.get("/api/names/phim_tay").json()["entries"]
    assert [(e["zh"], e["vi"]) for e in entries] == [("光头强", "Cường đầu trọc")]

    ui = [{"zh": e["zh"], "pinyin": e["pinyin"], "vi": e["vi"]} for e in entries]
    client.put("/api/names/phim_tay", json={"entries": ui})
    assert build_glossary(folder / "names.json")[0] == {"光头强": "Cường đầu trọc"}


def test_clusters_of_a_named_entry_are_not_reported_as_missing_names(
    client: TestClient, workspace: Path
) -> None:
    """Tên nhiều cụm đã có tên Việt: S8 không được nhắc "còn 2 tên chưa có tên tiếng Việt (光头、强)".

    Tab Tên riêng ghi âm đọc của từng cụm vào bảng phẳng (光头, 强) nhưng không
    hiện chúng thành dòng, nên câu nhắc cũ bảo người dùng đi điền hai tên họ không
    thấy ở đâu cả. Tên nhiều cụm CHƯA có tên Việt thì nhắc đúng một tên, không ba.
    """
    from srtgen.stages.s8_translate import build_glossary, glossary_warning

    folder = movie(workspace, "phim_cum")
    rows = [
        {"zh": "光头 强", "pinyin": "Guāngtóu Qiáng", "vi": "Cường đầu trọc"},
        {"zh": "陈 路周", "pinyin": "Chén Lùzhōu", "vi": ""},
    ]
    assert client.put("/api/names/phim_cum", json={"entries": rows}).status_code == 200
    assert set(names_on_disk(folder)["names"]) == {"光头", "强", "陈", "路周"}

    table, info = build_glossary(folder / "names.json")
    assert table == {"光头强": "Cường đầu trọc"}
    assert info["missing"] == ["陈路周"] and info["missing_count"] == 1
    warning = glossary_warning(info)
    assert "Còn 1 tên" in warning and "陈路周" in warning
    assert "光头" not in warning and "强" not in warning
