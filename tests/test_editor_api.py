"""Trình sửa phụ đề trực tiếp — các API của build-spec-v2 mục 4.

Trình sửa là yêu cầu mới đứng ngang hàng với việc tạo file: *"có thể kiểm tra và
chỉnh sửa trực tiếp hoặc import vào Aegisub"*. Ba đường dưới đây là ba thứ khiến
nó dùng được thật, và cả ba đều hỏng theo kiểu **im lặng**, nên chúng cần test
hơn phần còn lại:

* ``PUT /api/jobs/{id}/doc`` rồi ``GET`` lại phải ra **đúng** thứ vừa lưu. Sai ở
  đây nghĩa là người dùng sửa xong, đóng tab, mở lại và thấy bản cũ — công sức
  một buổi tối biến mất mà không có thông báo lỗi nào.
* ``POST /api/retokenize`` phải tách cụm **theo cách của người kiểm duyệt**.
  ``我来中国只有一个目的`` phải ra 6 cụm ``我 / 来 / 中国 / 只有 / 一个 / 目的``.
  Nút này là chỗ người dùng bấm khi máy tách sai; trả về đúng cách tách vừa làm
  họ phải bấm thì nút ấy vô nghĩa.
* ``GET /api/jobs/{id}/audio`` phải hỗ trợ **HTTP Range**. Không có Range thì thẻ
  ``<audio>`` không tua được, mà cột "nghe lại từng câu" chính là thứ khiến trình
  sửa có ích: soát phụ đề mà không nghe lại được thì chỉ là đọc chữ.

Mọi thứ chạy qua ``fastapi.testclient``, tức là đi qua **cả tầng middleware** chứ
không gọi thẳng hàm bên trong: lớp kiểm ``Host``/``Origin`` là một phần của hợp
đồng, và một endpoint đúng nhưng bị middleware chặn thì vẫn là một tính năng
hỏng.

Ngoại tuyến hoàn toàn: không mạng, không model, không mã API. Thư mục làm việc
được trỏ vào ``tmp_path``, nên bộ test không để lại gì trong thư mục dữ liệu thật
của người dùng.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

pytest.importorskip("fastapi", reason="Chưa cài fastapi — phần giao diện web không kiểm được.")

from fastapi.testclient import TestClient  # noqa: E402

from srtgen.core.rules import VI_BLOCK_COUNT, VI_TIMESTAMP_DRIFT, validate_pair  # noqa: E402
from srtgen.web import server as web_server  # noqa: E402

#: Câu mẫu của đặc tả và cách tách mà người kiểm duyệt đã chốt.
#: jieba với tầng HMM bật sẽ gộp ``我来`` thành một cụm — đó chính là lỗi mà nút
#: "Sinh lại pinyin" phải sửa, không phải lỗi nó được phép lặp lại.
RETOKENIZE_SENTENCE = "我来中国只有一个目的"
RETOKENIZE_CLUSTERS = ["我", "来", "中国", "只有", "一个", "目的"]

#: File .srt bốn dòng nhỏ nhất đủ để mở trình sửa.
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


# --------------------------------------------------------------------------- #
# dựng máy chủ trong thư mục tạm
# --------------------------------------------------------------------------- #

@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Trỏ thư mục ``work/`` của máy chủ vào ``tmp_path``.

    Bốn thứ đi qua ``work_root``: bảng tên riêng, bản nháp tự lưu, bản gốc để
    hoàn nguyên, và file âm thanh của chặng S1 — cộng cả danh sách thư mục được
    phép đọc. Vá đúng một hàm là đủ cho cả năm, và quan trọng hơn: bộ test không
    ghi một byte nào vào ``~/Library/Application Support`` của người dùng thật.
    """
    root = tmp_path / "work"
    root.mkdir()
    monkeypatch.setattr(web_server, "work_root", lambda cfg=None: root)
    return root


@pytest.fixture
def pair_on_disk(tmp_path: Path) -> Path:
    """Một cặp ``.srt`` / ``_vi.srt`` có sẵn, như file bên dịch gửi sang."""
    from srtgen import io_utils

    folder = tmp_path / "phim"
    folder.mkdir()
    io_utils.write_text(folder / "tap1.srt", ZH_SRT, bom=True)
    io_utils.write_text(folder / "tap1_vi.srt", VI_SRT, bom=True)
    return folder / "tap1.srt"


@pytest.fixture
def client(tmp_path: Path, workspace: Path) -> Iterator[TestClient]:
    """Máy khách HTTP thật, gọi qua đúng middleware của máy chủ.

    ``base_url`` phải là ``127.0.0.1``: mặc định của ``TestClient`` là
    ``testserver``, và lớp kiểm ``Host`` sẽ trả 403 — đúng như nó phải làm với
    một yêu cầu không đến từ chính máy này.
    """
    from srtgen.web.jobs import JobManager

    manager = JobManager(state_dir=tmp_path / "jobs", autoload=False)
    app = web_server.create_app(manager)
    with TestClient(app, base_url="http://127.0.0.1") as http:
        yield http
    manager.shutdown(wait=1.0)


@pytest.fixture
def job(client: TestClient, pair_on_disk: Path) -> dict[str, Any]:
    """Mở cặp file có sẵn vào trình sửa — đường không cần pipeline, không cần model."""
    response = client.post("/api/open-local", json={"path": str(pair_on_disk)})
    assert response.status_code == 201, response.text
    body = response.json()
    return {"id": body["job"]["id"], "video_id": body["doc"]["video_id"], "doc": body["doc"]}


def get_doc(client: TestClient, job_id: str) -> dict[str, Any]:
    response = client.get(f"/api/jobs/{job_id}/doc")
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 1. GET / PUT /api/jobs/{id}/doc
# --------------------------------------------------------------------------- #

def test_get_doc_returns_one_row_per_cue(client: TestClient, job: dict) -> None:
    """Bảng của trình sửa: mỗi cue một dòng, đủ bốn cột chữ và mốc thời gian."""
    doc = get_doc(client, job["id"])
    assert doc["cue_count"] == 3
    assert len(doc["cues"]) == 3
    first = doc["cues"][0]
    for field in ("zh_line", "py_line", "vi_line", "start_text", "end_text", "findings"):
        assert field in first, f"Thiếu cột {field}"
    assert first["zh_line"] == "你好 世界。"
    assert first["vi_line"] == "Chào thế giới."


def test_put_then_get_returns_exactly_what_was_saved(client: TestClient, job: dict) -> None:
    """**Yêu cầu chính của đặc tả**: lưu rồi đọc lại phải ra đúng nội dung đã lưu.

    So từng ô của từng cue, không so số lượng: mất một dòng tiếng Việt hay lấy
    nhầm pinyin của cue bên cạnh đều giữ nguyên số cue. Và so cả ba cột vì mỗi
    cột đi một đường ghi khác nhau (``.srt`` giữ hai cột đầu, ``_vi.srt`` giữ
    cột thứ ba) — một cột hỏng thì hai cột kia vẫn đúng.
    """
    doc = get_doc(client, job["id"])
    edited = [dict(cue) for cue in doc["cues"]]
    edited[0]["vi_line"] = "Xin chào thế giới."
    edited[1]["zh_line"] = "我 来 中国 只有 一个 目的。"
    edited[1]["py_line"] = "Wǒ lái Zhōngguó zhǐyǒu yígè mùdì。"
    edited[2]["vi_line"] = "- Anh là ai vậy?"

    saved = client.put(f"/api/jobs/{job['id']}/doc", json={"cues": edited})
    assert saved.status_code == 200, saved.text
    assert saved.json()["saved"] is True

    again = get_doc(client, job["id"])
    assert again["cue_count"] == len(edited)
    for position, (before, after) in enumerate(zip(edited, again["cues"]), start=1):
        for field in ("zh_line", "py_line", "vi_line"):
            assert after[field] == before[field], f"cue {position}, cột {field}"


def test_put_preserves_the_timestamps(client: TestClient, job: dict) -> None:
    """Sửa chữ không được làm xê dịch mốc thời gian.

    Mốc thời gian đi vòng qua chuỗi → giây → chuỗi trong lúc lưu; một phép làm
    tròn sai ở đó dịch cả phim đi vài mili-giây, và không ai nhận ra cho tới lúc
    ghép với video.
    """
    doc = get_doc(client, job["id"])
    edited = [dict(cue) for cue in doc["cues"]]
    edited[0]["vi_line"] = "Đã sửa."

    client.put(f"/api/jobs/{job['id']}/doc", json={"cues": edited})
    again = get_doc(client, job["id"])

    before = [(c["start_text"], c["end_text"]) for c in doc["cues"]]
    after = [(c["start_text"], c["end_text"]) for c in again["cues"]]
    assert after == before


def test_put_writes_both_files_and_keeps_them_in_lockstep(
    client: TestClient, job: dict, pair_on_disk: Path
) -> None:
    """Nút Lưu ghi cả ``.srt`` lẫn ``_vi.srt``, và hai file phải vẫn khớp nhau.

    Kiểm trên **file trên đĩa**, không trên phần trả về của API: thứ người dùng
    mang sang Aegisub là file, còn phần trả về chỉ là thứ vẽ lại màn hình.
    """
    from srtgen import io_utils

    doc = get_doc(client, job["id"])
    edited = [dict(cue) for cue in doc["cues"]]
    edited[0]["vi_line"] = "Xin chào thế giới."
    client.put(f"/api/jobs/{job['id']}/doc", json={"cues": edited})

    zh_text = io_utils.read_text(pair_on_disk)
    vi_text = io_utils.read_text(pair_on_disk.with_name("tap1_vi.srt"))
    assert "Xin chào thế giới." in vi_text

    drift = [f for f in validate_pair(zh_text, vi_text) if f.code in (VI_BLOCK_COUNT, VI_TIMESTAMP_DRIFT)]
    assert not drift, [f.code for f in drift]


def test_put_revalidates_and_reports_the_new_findings(client: TestClient, job: dict) -> None:
    """Lưu xong phải chạy lại validator — người dùng cần biết mình vừa sửa hết lỗi chưa.

    Cố ý gây một lỗi lệch số cụm rồi xem nó nổi lên: sửa dòng Hán thành 3 cụm
    trong khi dòng pinyin vẫn 2 cụm là lỗi hay gặp nhất khi sửa tay.
    """
    doc = get_doc(client, job["id"])
    edited = [dict(cue) for cue in doc["cues"]]
    edited[0]["zh_line"] = "你 好 世界。"      # 3 cụm
    edited[0]["py_line"] = "Nǐhǎo shìjiè。"    # 2 cụm

    saved = client.put(f"/api/jobs/{job['id']}/doc", json={"cues": edited})
    assert saved.status_code == 200
    assert "CUM_MISMATCH" in {f["code"] for f in saved.json()["findings"]}
    assert saved.json()["summary"]["error"] >= 1


def test_put_refuses_an_empty_edit(client: TestClient, job: dict) -> None:
    """Bản sửa trống thì **không ghi đè** file cũ.

    Một tab treo hay một lỗi giao diện có thể gửi lên danh sách rỗng; ghi đè
    theo nó là xoá sạch file phụ đề của người dùng bằng đúng một cú bấm.
    """
    response = client.put(f"/api/jobs/{job['id']}/doc", json={"cues": []})
    assert response.status_code == 400
    assert response.json()["error"]["message"]


def test_put_refuses_a_row_without_timestamps(
    client: TestClient, job: dict, pair_on_disk: Path
) -> None:
    """Thiếu mốc thời gian → **từ chối cả lần lưu**, không âm thầm lấy 0.

    "Sửa hộ" thành 0 sẽ dồn cả phim về giây thứ không — hỏng theo kiểu chỉ phát
    hiện ra khi đã quá muộn. Ba điều phải đúng: yêu cầu bị từ chối, câu từ chối
    đọc được, và **file cũ trên đĩa còn nguyên từng ký tự**.

    Không ghim mã trạng thái cụ thể: đặc tả mục 4 chỉ liệt kê đường dẫn và việc
    của từng API, không chốt mã lỗi. Máy chủ hiện trả 409 cho nhánh này (nó gói
    mọi ``JobError`` thành 409); 400 sẽ đúng nghĩa hơn cho một thân yêu cầu sai
    hình dạng, nhưng đó là chuyện đặt tên, không phải chuyện hành vi — và ghim
    một con số mà đặc tả không nói sẽ biến việc dọn tên gọi thành một test đỏ.
    """
    from srtgen import io_utils

    before = io_utils.read_text(pair_on_disk)
    doc = get_doc(client, job["id"])
    edited = [dict(cue) for cue in doc["cues"]]
    edited[1].pop("start")
    edited[1].pop("start_text")

    response = client.put(f"/api/jobs/{job['id']}/doc", json={"cues": edited})
    assert 400 <= response.status_code < 500, response.text
    assert response.json()["error"]["message"].strip()
    assert io_utils.read_text(pair_on_disk) == before


def test_unknown_job_is_a_clear_404(client: TestClient) -> None:
    """Mã công việc lạ trả 404 kèm câu tiếng Việt, không phải một vết lỗi Python."""
    response = client.get("/api/jobs/khong-co-that/doc")
    assert response.status_code == 404
    assert "Không tìm thấy" in response.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# 2. POST /api/retokenize
# --------------------------------------------------------------------------- #

def test_retokenize_matches_the_human_split(client: TestClient) -> None:
    """``我来中国只有一个目的`` → **6 cụm** đúng cách người kiểm duyệt tách.

    Đây là con số đặc tả nêu đích danh. jieba bật HMM sẽ gộp ``我来`` thành một
    cụm; nút "Sinh lại pinyin" tồn tại đúng để sửa chỗ máy tách sai, nên nó
    không được phép lặp lại chính lỗi ấy.
    """
    response = client.post("/api/retokenize", json={"zh": RETOKENIZE_SENTENCE})
    assert response.status_code == 200, response.text
    data = response.json()

    assert data["clusters"] == 6
    words = [t["text"] for t in data["tokens"] if t["kind"] == "word"]
    assert words == RETOKENIZE_CLUSTERS


def test_retokenize_returns_two_lines_with_the_same_cluster_count(client: TestClient) -> None:
    """Dòng Hán và dòng pinyin trả về phải cùng số cụm — bất biến gốc của dự án.

    Trình sửa dán thẳng hai dòng này vào bảng, nên một câu trả lời lệch cụm sẽ
    tạo ra đúng cái lỗi mà người dùng vừa bấm nút để sửa.
    """
    data = client.post("/api/retokenize", json={"zh": RETOKENIZE_SENTENCE}).json()

    assert data["zh_line"].split(" ") == RETOKENIZE_CLUSTERS
    assert len(data["py_line"].split(" ")) == len(RETOKENIZE_CLUSTERS)


def test_retokenize_generates_real_pinyin(client: TestClient) -> None:
    """Dòng pinyin phải là pinyin có dấu thanh, không phải bản sao dòng Hán."""
    py_line = client.post("/api/retokenize", json={"zh": RETOKENIZE_SENTENCE}).json()["py_line"]
    assert any(ch in "āáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜ" for ch in py_line)
    assert not any("一" <= ch <= "鿿" for ch in py_line)


def test_retokenize_can_keep_the_first_letter_lowercase(client: TestClient) -> None:
    """Cue nằm giữa câu không được viết hoa lây.

    Máy chủ chạy S6 cho **một câu**, mà S6 luôn viết hoa cue đầu tài liệu — nên
    nếu không có tuỳ chọn này thì mọi lần bấm "Sinh lại pinyin" đều biến một cue
    giữa câu thành đầu câu.
    """
    upper = client.post("/api/retokenize", json={"zh": RETOKENIZE_SENTENCE}).json()
    lower = client.post(
        "/api/retokenize", json={"zh": RETOKENIZE_SENTENCE, "capitalize": False}
    ).json()

    assert upper["py_line"][0].isupper()
    assert lower["py_line"][0].islower()
    assert lower["py_line"][1:] == upper["py_line"][1:]


def test_retokenize_refuses_an_empty_line(client: TestClient) -> None:
    """Không có chữ Hán thì báo bằng tiếng Việt, không trả 500."""
    response = client.post("/api/retokenize", json={"zh": "   "})
    assert response.status_code == 400
    assert response.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# 3. GET /api/jobs/{id}/audio — HTTP Range
# --------------------------------------------------------------------------- #

@pytest.fixture
def audio_bytes() -> bytes:
    """2052 byte giả làm file wav — đủ để cắt khoảng, không cần thư viện âm thanh."""
    return b"RIFF" + bytes(range(256)) * 8


@pytest.fixture
def job_with_audio(client: TestClient, job: dict, workspace: Path, audio_bytes: bytes) -> dict:
    """Gắn file âm thanh của chặng S1 vào công việc, đúng cách pipeline thật làm.

    Máy chủ tìm file qua ``work/<video_id>/S1_audio.json`` chứ không đoán theo
    tên, nên bài kiểm phải dựng đúng con đường ấy — dựng tắt sẽ kiểm một đường
    code không tồn tại.
    """
    from srtgen import io_utils

    folder = io_utils.ensure_dir(workspace / job["video_id"])
    wav = folder / "audio_norm.wav"
    wav.write_bytes(audio_bytes)
    io_utils.write_json(folder / "S1_audio.json", {"audio_path": str(wav)})
    return {**job, "audio_path": wav, "size": len(audio_bytes)}


def test_audio_with_a_range_header_returns_206_and_content_range(
    client: TestClient, job_with_audio: dict, audio_bytes: bytes
) -> None:
    """**Yêu cầu chính của đặc tả**: có Range → 206 kèm ``Content-Range``.

    Không có phần này thì thẻ ``<audio>`` của trình duyệt không tua được, và cột
    "nghe lại từng câu" — tính năng quan trọng nhất của trình sửa — chỉ phát
    được từ đầu file.
    """
    response = client.get(
        f"/api/jobs/{job_with_audio['id']}/audio", headers={"Range": "bytes=0-99"}
    )
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-99/{job_with_audio['size']}"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == "100"
    assert response.content == audio_bytes[:100]


def test_audio_range_returns_the_bytes_actually_asked_for(
    client: TestClient, job_with_audio: dict, audio_bytes: bytes
) -> None:
    """Đoạn giữa file: nội dung phải đúng đoạn ấy, không phải 100 byte đầu.

    Một máy chủ trả 206 với header đúng nhưng nội dung là đầu file sẽ làm người
    dùng bấm nghe cue thứ 300 và nghe thấy cue thứ nhất — chuyện chỉ tai người
    phát hiện được, nên phải có test.
    """
    response = client.get(
        f"/api/jobs/{job_with_audio['id']}/audio", headers={"Range": "bytes=500-599"}
    )
    assert response.status_code == 206
    assert response.content == audio_bytes[500:600]


def test_audio_open_ended_range_reaches_the_end_of_the_file(
    client: TestClient, job_with_audio: dict, audio_bytes: bytes
) -> None:
    """``bytes=1000-`` là dạng thẻ ``<audio>`` dùng nhiều nhất khi kéo thanh tua."""
    size = job_with_audio["size"]
    response = client.get(
        f"/api/jobs/{job_with_audio['id']}/audio", headers={"Range": "bytes=1000-"}
    )
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 1000-{size - 1}/{size}"
    assert response.content == audio_bytes[1000:]


def test_audio_without_a_range_header_returns_the_whole_file(
    client: TestClient, job_with_audio: dict
) -> None:
    """Không có Range thì 200 và cả file — nhưng vẫn phải quảng cáo ``Accept-Ranges``.

    Header ấy là cách trình duyệt biết nó **được phép** tua; thiếu nó thì nhiều
    trình duyệt vô hiệu hoá thanh tua dù máy chủ có hỗ trợ.
    """
    response = client.get(f"/api/jobs/{job_with_audio['id']}/audio")
    assert response.status_code == 200
    assert response.headers.get("accept-ranges") == "bytes"
    assert len(response.content) == job_with_audio["size"]


def test_audio_range_past_the_end_returns_416(client: TestClient, job_with_audio: dict) -> None:
    """Xin đoạn nằm ngoài file → 416 kèm ``Content-Range: bytes */<size>``.

    Trả 200 kèm cả file ở đây sẽ làm trình phát tưởng nó đã tua thành công rồi
    phát lại từ đầu — im lặng và khó hiểu hơn hẳn một mã lỗi đúng.
    """
    size = job_with_audio["size"]
    response = client.get(
        f"/api/jobs/{job_with_audio['id']}/audio", headers={"Range": f"bytes={size + 10}-"}
    )
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{size}"


def test_audio_says_so_in_vietnamese_when_there_is_none(client: TestClient, job: dict) -> None:
    """Công việc mở từ file có sẵn thì chưa có âm thanh — phải nói rõ, không trả 500."""
    response = client.get(f"/api/jobs/{job['id']}/audio")
    assert response.status_code == 404
    assert "âm thanh" in response.json()["error"]["message"]


# --------------------------------------------------------------------------- #
# 4. Bản nháp tự lưu và lớp chặn trang web lạ
# --------------------------------------------------------------------------- #

def test_draft_round_trip(client: TestClient, job: dict) -> None:
    """Bản nháp tự lưu mỗi 5 giây — ghi rồi đọc lại phải ra đúng thứ đã ghi.

    Đây là lưới an toàn cho người đóng nhầm tab; một bản nháp ghi được mà đọc
    không ra thì lưới ấy không tồn tại, và không có gì báo cho ai biết.
    """
    draft = {"cues": [{"index": 1, "vi_line": "đang gõ dở"}], "at": 123}
    saved = client.put(f"/api/jobs/{job['id']}/draft", json={"draft": draft})
    assert saved.status_code == 200, saved.text

    loaded = client.get(f"/api/jobs/{job['id']}/draft")
    assert loaded.status_code == 200
    assert loaded.json()["draft"] == draft


def test_draft_reports_when_the_file_was_last_written(
    client: TestClient, job: dict, pair_on_disk: Path
) -> None:
    """Bản nháp cũ không mang ``base_hash`` thì giao diện chỉ còn cách so mốc giờ.

    ``GET .../draft`` phải trả ``srt_mtime`` để giao diện biết file trên đĩa bị
    ghi lại **sau** lúc ghi nháp; thiếu nó thì khôi phục sẽ lặng lẽ đè lên bản
    mới hơn mà không có một lời cảnh báo nào.
    """
    import os

    client.put(f"/api/jobs/{job['id']}/draft", json={"cues": [{"index": 1, "vi_line": "x"}]})
    loaded = client.get(f"/api/jobs/{job['id']}/draft").json()
    assert loaded["srt_mtime"] == pytest.approx(pair_on_disk.stat().st_mtime)

    later = loaded["saved_at"] + 60
    os.utime(pair_on_disk, (later, later))
    again = client.get(f"/api/jobs/{job['id']}/draft").json()
    assert again["srt_mtime"] > again["saved_at"] + 2


def test_a_request_from_another_website_is_refused(client: TestClient, job: dict) -> None:
    """Trang web lạ trong tab khác không được gọi vào máy chủ này.

    Máy chủ nghe ở ``127.0.0.1``, nhưng một trang đang mở ở tab khác vẫn gửi
    được yêu cầu tới đó. Lớp kiểm ``Origin`` là thứ chặn chuyện đó, và nó phải
    được kiểm qua HTTP thật chứ không qua lời hứa trong docstring.
    """
    response = client.get(
        f"/api/jobs/{job['id']}/doc", headers={"Origin": "https://trang-la.example"}
    )
    assert response.status_code == 403


def test_a_local_page_is_still_allowed(client: TestClient, job: dict) -> None:
    """Chiều ngược lại: chính giao diện của tool phải gọi được, nếu không thì chặn nhầm."""
    response = client.get(
        f"/api/jobs/{job['id']}/doc", headers={"Origin": "http://127.0.0.1:8756"}
    )
    assert response.status_code == 200
