"""Máy chủ web và quy ước "cất bản cũ trước khi ghi đè" (hợp đồng H1).

* ``/api/fix`` ghi ``<tên>.da-chuan-hoa.srt`` (và ``_vi.srt``). Trước đây sửa
  cùng một file hai lần là ghi đè im lặng bản lần trước — mà bản đó người dùng có
  thể đã sửa tay. Nay bản cũ khác nội dung được cất qua ``io_utils.keep_old_copy``
  và ``/api/fix`` trả ``backups`` giống kết quả S9.
* Nút Hoàn nguyên ghi bản máy tạo đè lên bản đang có → cũng cất.
* Nút Lưu là người dùng chủ động ghi đè bản của chính họ → KHÔNG cất (nếu không
  mỗi lần Lưu lại đẻ thêm một file).
* Danh sách phim bỏ mọi file có ``.truoc-`` trong tên.
* ``/api/retokenize`` cắt 20 câu đầu ``corpus/raw.srt`` giống hệt ``segment_line``.

Ngoại tuyến hoàn toàn: hàng đợi không bao giờ chạy, mọi thư mục nằm trong ``tmp_path``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402

from srtgen import io_utils  # noqa: E402
from srtgen.core.srt import parse_srt  # noqa: E402
from srtgen.core.token import KIND_WORD  # noqa: E402
from srtgen.stages.s5_tokenize import segment_line, segment_tokens  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402
from srtgen.web.jobs import JobManager  # noqa: E402


class IdleManager(JobManager):
    """Hàng đợi nhận việc nhưng không bao giờ chạy — không yt-dlp, không ffmpeg, không mạng."""

    def _ensure_worker(self) -> None:
        return None


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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

KEPT_VI = re.compile(r"tap1\.da-chuan-hoa_vi\.truoc-\d{8}-\d{6}(-\d+)?\.srt")


def post_fix(client: TestClient, text: str = TRILINGUAL, filename: str = "tap1.srt") -> dict[str, Any]:
    response = client.post("/api/fix", json={"filename": filename, "text": text})
    assert response.status_code == 200, response.text
    return response.json()


def kept_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if io_utils.is_kept_copy(p))


# --------------------------------------------------------------------------- #
# /api/fix
# --------------------------------------------------------------------------- #

def test_fix_twice_keeps_the_hand_edited_result(client: TestClient) -> None:
    first = post_fix(client)
    assert first["backups"] == [], "Lần đầu chưa có gì để cất."
    assert first["job"]["backups"] == []
    vi = Path(first["vi_path"])
    out = vi.parent

    # Người dùng mở file kết quả ra sửa tay, rồi quên và chuẩn hoá lại file gốc.
    io_utils.write_text(vi, io_utils.read_text(vi).replace("Chào thế giới.", "Xin chào cả nhà."))
    hand_bytes = vi.read_bytes()

    second = post_fix(client)

    kept = [Path(p) for p in second["backups"]]
    assert len(kept) == 1, second["backups"]
    (copy,) = kept
    assert KEPT_VI.fullmatch(copy.name), copy.name
    assert copy.parent == out
    assert copy.read_bytes() == hand_bytes, "Bản sửa tay còn nguyên từng byte."
    assert "Chào thế giới." in io_utils.read_text(vi), "File chính là bản vừa chuẩn hoá."
    assert kept_in(out) == kept
    assert second["job"]["backups"] == second["backups"]
    assert copy.name in second["job"]["message"], "Phải nói cho người dùng biết bản cũ nằm đâu."
    assert "write_error" not in second

    # Chuẩn hoá lần ba, nội dung y hệt → không cất thêm gì.
    third = post_fix(client)
    assert third["backups"] == []
    assert kept_in(out) == kept


def test_fix_never_overwrites_a_result_it_cannot_set_aside(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = post_fix(client)
    zh = Path(first["path"])
    io_utils.write_text(zh, io_utils.read_text(zh).replace("我 来", "我 就 来"))
    hand_bytes = zh.read_bytes()
    real_rename = Path.rename

    def locked(self: Path, other: Any) -> Any:
        if self == zh:
            raise PermissionError("file đang mở trong Aegisub")
        return real_rename(self, other)

    monkeypatch.setattr(Path, "rename", locked)
    body = post_fix(client)

    assert "KHÔNG ghi đè" in body["write_error"]
    assert zh.read_bytes() == hand_bytes
    assert body["path"] == "" and body["backups"] == []
    assert kept_in(zh.parent) == []


# --------------------------------------------------------------------------- #
# nút Lưu không cất, nút Hoàn nguyên thì cất
# --------------------------------------------------------------------------- #

def test_save_keeps_nothing_but_revert_keeps_the_hand_edit(client: TestClient) -> None:
    fixed = post_fix(client)
    job_id = fixed["job"]["id"]
    vi = Path(fixed["vi_path"])
    out = vi.parent

    cues = client.get(f"/api/jobs/{job_id}/doc").json()["cues"]
    for text in ("Bản sửa tay lần một.", "Bản sửa tay lần hai."):
        cues[0]["vi_line"] = text
        saved = client.put(f"/api/jobs/{job_id}/doc", json={"cues": cues})
        assert saved.status_code == 200, saved.text
    assert kept_in(out) == [], "Lưu là ghi đè bản của chính mình — không đẻ thêm file."
    hand_bytes = vi.read_bytes()
    assert "Bản sửa tay lần hai." in io_utils.read_text(vi)

    reverted = client.put(f"/api/jobs/{job_id}/doc", json={"revert": True})
    assert reverted.status_code == 200, reverted.text
    body = reverted.json()

    kept = [Path(p) for p in body["backups"]]
    assert set(kept) == set(kept_in(out)), "Trả về đúng mọi bản đã cất."
    (vi_copy,) = [p for p in kept if KEPT_VI.fullmatch(p.name)]
    assert vi_copy.read_bytes() == hand_bytes, "Bản sửa tay còn nguyên sau khi hoàn nguyên."
    assert "Bản sửa tay lần hai." not in io_utils.read_text(vi)
    assert vi_copy.name in body["message"]


# --------------------------------------------------------------------------- #
# danh sách phim bỏ mọi bản cất
# --------------------------------------------------------------------------- #

def test_film_list_skips_every_kept_copy(workspace: Path) -> None:
    films = workspace / "phim_a"
    films.mkdir()
    for name in (
        "A.truoc-20260910-183000.srt",
        "Phim_vi.truoc-20260910-183000.srt",
        "Phim.srt",
        "Phim_vi.srt",
    ):
        io_utils.write_text(films / name, "1\n00:00:01,000 --> 00:00:02,000\n你 好。\nNǐ hǎo。\n")
    assert web_server._movie_title(films, "phim_a") == "Phim"

    only_copies = workspace / "phim_b"
    only_copies.mkdir()
    for name in ("Phim.truoc-20260910-183000.srt", "Phim_vi.truoc-20260910-183000.srt"):
        io_utils.write_text(only_copies / name, "x")
    assert web_server._movie_title(only_copies, "phim_b") == "phim_b"
    assert not web_server._looks_like_movie(only_copies)

    rows = {row["video_id"]: row["title"] for row in web_server.list_name_tables()}
    assert rows.get("phim_a") == "Phim"
    assert "phim_b" not in rows


# --------------------------------------------------------------------------- #
# /api/retokenize = segment_line
# --------------------------------------------------------------------------- #

def test_retokenize_cuts_the_first_20_raw_lines_exactly_like_segment_line(
    client: TestClient, raw_text: str
) -> None:
    lines = [" ".join(block.lines) for block in parse_srt(raw_text)[:20]]
    assert len(lines) == 20
    for number, line in enumerate(lines, start=1):
        response = client.post("/api/retokenize", json={"zh": line})
        assert response.status_code == 200, (number, response.text)
        got = [t["text"] for t in response.json()["tokens"] if t["kind"] == "word"]

        pieces = segment_line(line)
        kinds = [t.kind for t in segment_tokens(line)]
        assert len(pieces) == len(kinds)
        expected = [piece for piece, kind in zip(pieces, kinds) if kind == KIND_WORD]
        assert got == expected, (number, line, got, expected)
