"""Trình sửa nói rõ bản lưu nằm ở đâu (H6), trang hướng dẫn đọc được (H7), nút
"Sinh lại pinyin" dùng đúng bộ tách của S5 (H5), và máy chủ đọc/ghi bảng tên qua
kho an toàn (H4).

Chủ đề chung: người dùng không phải dân IT không bao giờ được mất bản sửa tay mà
không biết. Mỗi mục dưới đây từng hỏng theo kiểu IM LẶNG:

* Mở file bằng hộp chọn file (trình duyệt chỉ gửi nội dung) rồi bấm Lưu: bản lưu
  nằm trong thư mục làm việc của tool, còn file trong thư mục phim của họ vẫn là
  bản CŨ — họ mở ra, tưởng mất cả buổi sửa.
* Nút "Mở hướng dẫn sử dụng" nhờ hệ điều hành mở file `.md` thô; máy không có ứng
  dụng đọc `.md` thì không có gì hiện ra, mà tool vẫn báo "Đã mở".
* Nút "Sinh lại pinyin" tự cắt câu bằng một bản sao riêng của bộ tách, nên có thể
  tách một câu khác hẳn chặng S5.
* Máy chủ và S7 mỗi bên tự đọc/ghi `names.json` — hai ý kiến về "hỏng" và "trống".

Ngoại tuyến hoàn toàn; `work/` và thư mục kết quả trỏ vào `tmp_path`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402

from srtgen import io_utils  # noqa: E402
from srtgen.core import names_store  # noqa: E402
from srtgen.core.token import KIND_WORD  # noqa: E402
from srtgen.stages import s5_tokenize  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402
from srtgen.web.jobs import Job, JobManager  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

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

NAME_ENTRY = {"zh": "光头 强", "pinyin": "Guāngtóu Qiáng", "vi": "Cường đầu trọc"}

#: Lỗi sửa tay hay gặp nhất: thừa một dấu phẩy.
BROKEN = '{"names": {"光头强": "Guāngtóuqiáng", "熊大": "Xióngdà",}}'


class IdleManager(JobManager):
    """Hàng đợi nhận việc nhưng không bao giờ chạy — ở đây không có việc dài nào."""

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
    monkeypatch.setattr(
        web_server,
        "_download_bytes",
        lambda *a, **k: pytest.fail("Bộ test không được phép tải file từ mạng."),
    )
    return root


@pytest.fixture
def client(tmp_path: Path, workspace: Path) -> Iterator[TestClient]:
    manager = IdleManager(state_dir=tmp_path / "jobs", autoload=False)
    with TestClient(web_server.create_app(manager), base_url="http://127.0.0.1") as http:
        yield http
    manager.shutdown(wait=0.5)


def movie_folders(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_"))


def open_content(client: TestClient, *, vi: bool = True) -> dict[str, Any]:
    body = {"name": "Tập 1.srt", "content": ZH_SRT}
    if vi:
        body["vi_content"] = VI_SRT
    response = client.post("/api/open-local", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def save(client: TestClient, job_id: str, edit: Callable[[list[dict[str, Any]]], None]) -> dict[str, Any]:
    cues = client.get(f"/api/jobs/{job_id}/doc").json()["cues"]
    edit(cues)
    response = client.put(f"/api/jobs/{job_id}/doc", json={"cues": cues})
    assert response.status_code == 200, response.text
    return response.json()


def set_vi(text: str) -> Callable[[list[dict[str, Any]]], None]:
    def edit(cues: list[dict[str, Any]]) -> None:
        cues[0]["vi_line"] = text

    return edit


# --------------------------------------------------------------------------- #
# H6 — mở bằng nội dung: nói rõ đã lưu vào đâu, và cho tải về ngay
# --------------------------------------------------------------------------- #

def test_file_opened_by_content_says_where_it_was_saved_and_offers_both_downloads(
    client: TestClient, workspace: Path
) -> None:
    opened = open_content(client)
    assert opened["doc"]["origin"] == "content", "giao diện cần biết TRƯỚC khi bấm Lưu"
    (folder,) = movie_folders(workspace)

    body = save(client, opened["job"]["id"], set_vi("Xin chào thế giới."))
    assert body["origin"] == "content"
    assert Path(body["saved_to"]) == folder
    assert [Path(p).name for p in body["saved_files"]] == ["Tập 1.srt", "Tập 1_vi.srt"]
    assert body["saved_to"] in body["message"], "câu báo phải nói bản lưu nằm ở đâu"

    srt = client.get(body["downloads"]["srt"])
    assert srt.status_code == 200, srt.text
    assert "我 来 中国 只有 一个 目的。" in srt.text
    vi = client.get(body["downloads"]["vi_srt"])
    assert vi.status_code == 200, vi.text
    assert "Xin chào thế giới." in vi.text


def test_content_without_vietnamese_has_no_vietnamese_download(
    client: TestClient, workspace: Path
) -> None:
    opened = open_content(client, vi=False)

    def edit(cues: list[dict[str, Any]]) -> None:
        cues[1]["zh_line"] = "我 来 中国。"
        cues[1]["py_line"] = "Wǒ lái Zhōngguó。"

    body = save(client, opened["job"]["id"], edit)
    assert body["downloads"]["srt"]
    assert body["downloads"]["vi_srt"] is None, "không được đưa đường tải trỏ vào file không có"
    assert len(body["saved_files"]) == 1


def test_file_opened_by_path_is_saved_in_place(client: TestClient, tmp_path: Path) -> None:
    folder = tmp_path / "phim"
    folder.mkdir()
    io_utils.write_text(folder / "tap1.srt", ZH_SRT, bom=True)
    io_utils.write_text(folder / "tap1_vi.srt", VI_SRT, bom=True)

    opened = client.post("/api/open-local", json={"path": str(folder / "tap1.srt")})
    assert opened.status_code == 201, opened.text
    assert opened.json()["doc"]["origin"] == "path"

    body = save(client, opened.json()["job"]["id"], set_vi("Xin chào."))
    assert body["origin"] == "path"
    assert Path(body["saved_to"]).resolve() == folder.resolve()
    assert body["downloads"]["srt"] and body["downloads"]["vi_srt"]
    assert "thư mục của tool" not in body["message"]
    assert "Xin chào." in io_utils.read_text(folder / "tap1_vi.srt")


def test_revert_also_says_where_it_wrote(client: TestClient, workspace: Path) -> None:
    opened = open_content(client)
    (folder,) = movie_folders(workspace)
    job_id = opened["job"]["id"]
    save(client, job_id, set_vi("Bản sửa tay."))

    response = client.put(f"/api/jobs/{job_id}/doc", json={"revert": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reverted"] is True
    assert body["origin"] == "content"
    assert Path(body["saved_to"]) == folder
    assert body["downloads"]["srt"]


def test_origin_of_a_job_saved_by_an_older_version_is_inferred(
    workspace: Path, tmp_path: Path
) -> None:
    """Việc ghi từ trước khi có trường `origin` vẫn được gọi đúng tên."""

    def job(mode: str, srt: Path, **extra: Any) -> Job:
        item = Job(id="x", source=str(srt), mode=mode, status="done")
        item.result = {"srt": str(srt), **extra}
        return item

    in_work = workspace / "tap_1_1234abcd" / "Tập 1.srt"
    elsewhere = tmp_path / "phim" / "tap1.srt"
    assert web_server.editor_origin(job("local", in_work)) == "content"
    assert web_server.editor_origin(job("local", elsewhere)) == "path"
    assert web_server.editor_origin(job("run", elsewhere)) == "job"
    assert web_server.editor_origin(job("local", in_work, origin="path")) == "path"


# --------------------------------------------------------------------------- #
# H7 — GET /guide
# --------------------------------------------------------------------------- #

def test_guide_page_is_a_readable_html_page_of_the_guide(client: TestClient) -> None:
    response = client.get("/guide")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    page = response.text

    first_line = io_utils.read_text(REPO_ROOT / "HUONG-DAN.md").splitlines()[0]
    title = first_line.lstrip("#").strip()
    assert "Hướng dẫn sử dụng" in title
    assert f"<title>{title}</title>" in page
    assert f">{title}</h1>" in page
    assert '<html lang="vi">' in page

    assert "<table>" in page, "bảng markdown phải thành bảng HTML"
    assert "| --- |" not in page and "**" not in page
    # Mục lục viết tay trỏ tới đúng mã neo của tiêu đề.
    assert 'href="#1-cài-lần-đầu"' in page and 'id="1-cài-lần-đầu"' in page


def test_guide_page_explains_itself_when_the_guide_file_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_server, "_guide_file", lambda: None)
    response = client.get("/guide")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "HUONG-DAN.md" in response.text and "Chưa mở được" in response.text


def test_the_guide_button_and_health_point_to_the_guide_page(client: TestClient) -> None:
    action = client.post("/api/actions/open_guide")
    assert action.status_code == 200, action.text
    assert action.json()["url"] == web_server.GUIDE_URL == "/guide"
    assert client.get("/api/health").json()["guide_url"] == "/guide"


MARKDOWN_SAMPLE = """# Tiêu đề <b>

Đoạn có **đậm**, *nghiêng*, `a<b>` và [liên kết](https://example.com) cùng [neo](#mục-2).
Dòng nối tiếp.

## Mục 2

> Trích dẫn **quan trọng**.
> Dòng hai.

| Cột | Phải |
| :--- | ---: |
| `x \\| y` | 1 |

1. Một
2. Hai
   tiếp dòng
   - con a
   - con b

   ```
   <script>alert(1)</script>
   ```

3. Ba

Chen giữa.

10. Mười
11. Mười một

[xấu](javascript:void0) và <img src=x onerror=alert(1)>

---
"""


def test_markdown_converter_covers_the_guide_syntax_and_escapes_everything() -> None:
    out = web_server.markdown_to_html(MARKDOWN_SAMPLE)

    assert '<h1 id="tiêu-đề-b">Tiêu đề &lt;b&gt;</h1>' in out
    assert '<h2 id="mục-2">Mục 2</h2>' in out
    assert (
        "<p>Đoạn có <strong>đậm</strong>, <em>nghiêng</em>, <code>a&lt;b&gt;</code> và "
        '<a href="https://example.com" target="_blank" rel="noopener noreferrer">liên kết</a> '
        'cùng <a href="#mục-2">neo</a>. Dòng nối tiếp.</p>'
    ) in out
    assert "<blockquote><p>Trích dẫn <strong>quan trọng</strong>. Dòng hai.</p></blockquote>" in out

    assert '<th style="text-align:left">Cột</th><th style="text-align:right">Phải</th>' in out
    assert '<td style="text-align:left"><code>x | y</code></td>' in out

    assert "<ol><li><p>Một</p></li>" in out
    assert "<p>Hai tiếp dòng</p>" in out
    assert "<ul><li>con a</li>\n<li>con b</li></ul>" in out
    assert "<pre><code>&lt;script&gt;alert(1)&lt;/script&gt;</code></pre>" in out
    assert '<ol start="10"><li>Mười</li>' in out

    # Không bao giờ thành mã chạy được: liên kết lạ chỉ còn chữ, HTML bị thoát.
    assert "javascript" not in out.split("xấu")[0] and 'href="javascript' not in out
    assert "<img" not in out and "&lt;img src=x onerror=alert(1)&gt;" in out
    assert "<script>" not in out
    assert out.rstrip().endswith("<hr>")


# --------------------------------------------------------------------------- #
# H5 — /api/retokenize dùng bộ tách dùng chung của S5
# --------------------------------------------------------------------------- #

def words_of(body: dict[str, Any]) -> list[str]:
    return [t["text"] for t in body["tokens"] if t["kind"] == "word"]


def test_retokenize_calls_the_s5_segmenter_with_the_film_name_table(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = workspace / "phim_gtq"
    folder.mkdir()
    names_store.save_names_file(
        folder / "names.json",
        {
            "version": 1,
            "names": {"光头": "Guāngtóu", "强": "Qiáng"},
            "entries": [{"han": "光头强", "split": ["光头", "强"], "pinyin": ["Guāngtóu", "Qiáng"]}],
        },
    )
    real = s5_tokenize.segment_tokens
    calls: list[tuple[str, Any, dict[str, Any]]] = []

    def spy(zh: str, names: Any = None, **kwargs: Any) -> Any:
        calls.append((zh, names, kwargs))
        return real(zh, names, **kwargs)

    monkeypatch.setattr(s5_tokenize, "segment_tokens", spy)
    response = client.post("/api/retokenize", json={"zh": "光头强大喊。", "video_id": "phim_gtq"})
    assert response.status_code == 200, response.text

    assert len(calls) == 1, "đúng một lần tách, và qua đúng hàm của S5"
    zh, names, kwargs = calls[0]
    assert zh == "光头强大喊。"
    assert isinstance(names, dict) and names.get("entries"), "phải gửi bảng tên của phim"
    assert kwargs["userdict_path"] == folder / s5_tokenize.USERDICT_FILE
    expected = [
        piece for piece in s5_tokenize.segment_line("光头强大喊。", names) if piece != "。"
    ]
    assert words_of(response.json()) == expected == ["光头", "强", "大喊"]
    assert not hasattr(web_server, "_dictionary_cut"), "không còn bộ cắt riêng của máy chủ"


def test_retokenize_without_a_film_matches_segment_line(client: TestClient) -> None:
    sentence = "我来中国只有一个目的"
    body = client.post("/api/retokenize", json={"zh": sentence}).json()
    assert words_of(body) == s5_tokenize.segment_line(sentence) == [
        "我", "来", "中国", "只有", "一个", "目的",
    ]
    segmented = [t.zh for t in s5_tokenize.segment_tokens(sentence) if t.kind == KIND_WORD]
    assert words_of(body) == segmented


def test_retokenize_with_a_broken_name_table_still_works_and_says_so(
    client: TestClient, workspace: Path
) -> None:
    folder = workspace / "phim_hong"
    folder.mkdir()
    io_utils.write_text(folder / "names.json", BROKEN, bom=False)
    before = (folder / "names.json").read_bytes()

    response = client.post("/api/retokenize", json={"zh": "我们走吧。", "video_id": "phim_hong"})
    assert response.status_code == 200, response.text
    assert "lỗi cú pháp" in response.json()["names_warning"]
    assert (folder / "names.json").read_bytes() == before, "chỉ đọc — không được đụng vào file"
    assert list(folder.glob("names.json.hong-*")) == []


# --------------------------------------------------------------------------- #
# H4 — máy chủ đọc/ghi bảng tên qua srtgen.core.names_store
# --------------------------------------------------------------------------- #

def test_saving_names_goes_through_the_names_store(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = workspace / "phim_luu"
    folder.mkdir()
    real = names_store.save_names_file
    calls: list[Path] = []

    def spy(path: Any, data: Any) -> Any:
        calls.append(Path(path))
        return real(path, data)

    monkeypatch.setattr(web_server, "save_names_file", spy)
    response = client.put("/api/names/phim_luu", json={"entries": [NAME_ENTRY]})
    assert response.status_code == 200, response.text

    assert calls == [folder / "names.json"]
    loaded = names_store.load_names_file(folder / "names.json")
    assert not loaded.corrupt
    assert loaded.data["entries"][0]["vi"] == "Cường đầu trọc"
    for legacy in ("_backup_corrupt_names", "_names_file_problem"):
        assert not hasattr(web_server, legacy), f"{legacy}: máy chủ không được tự đọc/ghi names.json"


def test_a_broken_table_is_described_with_the_names_store_sentence(
    client: TestClient, workspace: Path
) -> None:
    folder = workspace / "phim_hong"
    folder.mkdir()
    io_utils.write_text(folder / "names.json", BROKEN, bom=False)
    body = client.get("/api/names/phim_hong").json()
    assert body["corrupt"] is True
    assert body["corrupt_message"] == names_store.load_names_file(folder / "names.json").message


def test_a_refused_save_shows_the_names_store_sentence_and_writes_nothing(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = workspace / "phim_hong"
    folder.mkdir()
    io_utils.write_text(folder / "names.json", BROKEN, bom=False)
    before = (folder / "names.json").read_bytes()

    def refuse(path: Any, data: Any) -> Any:
        raise names_store.NamesSaveRefused("Chưa lưu gì: câu từ chối dùng trong bộ kiểm thử.")

    monkeypatch.setattr(web_server, "save_names_file", refuse)
    response = client.put("/api/names/phim_hong", json={"entries": [NAME_ENTRY]})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["message"] == "Chưa lưu gì: câu từ chối dùng trong bộ kiểm thử."
    assert (folder / "names.json").read_bytes() == before
