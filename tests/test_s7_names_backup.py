"""S7 không bao giờ làm mất bảng tên riêng người dùng đã gõ (hợp đồng H4).

Chuyện đã xảy ra: người dùng mở ``names.json`` bằng Notepad/TextEdit và quên một
dấu phẩy. S7 đọc file hỏng thành bảng RỖNG, AI tìm được vài tên, rồi S7 ghi
bảng mới lên đúng chỗ đó: mọi tên cũ mất sạch, không một dòng nào nói ra. Chốt
chặn cũ (``names_hash``) chỉ bắt được lỗi cú pháp; một file JSON đúng cú pháp
nhưng không phải một bảng (ví dụ một danh sách) vẫn bị ghi đè.

Hợp đồng kiểm ở đây:

* file hỏng + AI tìm được tên → file hỏng được CẤT (đổi tên) thành
  ``names.json.hong-<thời điểm>``, còn nguyên từng byte, RỒI MỚI ghi bảng mới;
  người dùng được báo tên file cất ở nhật ký, trong ``errors`` và ở câu tổng kết;
* không cất được → không ghi gì, báo bằng câu tiếng Việt;
* AI không tìm được gì → không đụng vào file hỏng, nhưng vẫn nói bảng đang hỏng;
* bảng lành → không bao giờ sinh bản cất;
* S7 đọc/ghi ``names.json`` chỉ qua :mod:`srtgen.core.names_store`.

Context thật trong thư mục tạm, nhà cung cấp AI giả; không mạng, không cài gì.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Sequence

import pytest

from srtgen import io_utils
from srtgen.core import names_store
from srtgen.stages import s5_tokenize, s6_normalize, s7_ai
from tests.test_pipeline_offline import make_context

VIDEO_ID = "s7_names_backup"
TITLE = "names-backup"

CUES = [
    {"index": 1, "start": 0.0, "end": 1.5, "text": "光头强来了。"},
    {"index": 2, "start": 1.5, "end": 3.0, "text": "熊大，快跑！"},
    {"index": 3, "start": 3.0, "end": 4.5, "text": "他是光头强吗？"},
]

#: Vài tên nhà cung cấp giả trả về — đều có thật trong lời thoại, pinyin hợp lệ,
#: để T1 nhận chứ không loại.
AI_NAMES: tuple[dict[str, Any], ...] = (
    {"han": "光头强", "split": ["光头", "强"], "pinyin": ["guāngtóu", "qiáng"], "type": "person"},
    {"han": "熊大", "split": ["熊大"], "pinyin": ["xióngdà"], "type": "person"},
)

#: Bảng người dùng sửa tay dở: thiếu ngoặc đóng. Có cả tên tiếng Việt họ đã gõ —
#: đúng thứ không được mất.
BROKEN = (
    '{"names": {"光头": "Guāngtóu", "强": "Qiáng",\n'
    '  "陈路周": {"pinyin": "Chén Lùzhōu", "vi": "Trần Lộ Chu"}\n'
).encode("utf-8")

BACKUP_NAME = re.compile(r"names\.json\.hong-\d{8}-\d{6}(-\d+)?")


class Log:
    """Thu mọi câu tiến trình mà chặng nói với người dùng."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str = "", fraction: float = 0.0) -> None:
        del fraction
        self.messages.append(str(message))

    def said(self, text: str) -> bool:
        return any(text in m for m in self.messages)


class NamesProvider:
    """Nhà cung cấp giả: T1 trả ``rows``, mọi nhiệm vụ khác trả rỗng."""

    name = "fake-names"
    reason = ""

    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self.rows = [dict(r) for r in rows]

    def complete_json(self, prompt: str, schema: dict, *, model: str = "") -> dict:
        del prompt, model
        if "names" in (schema.get("properties") or {}):
            return {"names": [dict(r) for r in self.rows]}
        return {}


def ai_config(
    cfg: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    rows: Sequence[dict[str, Any]] = AI_NAMES,
) -> dict[str, Any]:
    """Cấu hình mặc định, chỉ bật T1, tắt mọi cache ghi ra thư mục thật của người dùng."""
    monkeypatch.setattr(s7_ai, "get_provider", lambda _cfg: NamesProvider(rows))
    out = dict(cfg)
    out["ai"] = {
        **dict(cfg.get("ai") or {}),
        "enabled": True,
        "cache": False,
        "tasks": {
            s7_ai.TASK_T1: True,
            s7_ai.TASK_T2: False,
            s7_ai.TASK_T3: False,
            s7_ai.TASK_T4: False,
        },
    }
    out["translate"] = {**dict(cfg.get("translate") or {}), "cache": False}
    return out


def film_ready_for_s7(tmp_path: Path, config: dict[str, Any]) -> Any:
    """Phim đã qua S5, S6 — đúng chỗ S7 bắt đầu trong pipeline thật."""
    ctx = make_context(tmp_path, config, video_id=VIDEO_ID)
    ctx.meta["title"] = TITLE
    ctx.save_stage(4, "cues", {"meta": {"title": TITLE}, "cues": CUES})
    s5_tokenize.run(ctx, Log())
    s6_normalize.run(ctx, Log())
    return ctx


def backups_of(table: Path) -> list[Path]:
    return sorted(table.parent.glob(f"{table.name}{names_store.CORRUPT_NAMES_TAG}*"))


def notices(result: dict[str, Any]) -> list[str]:
    return [str(e.get("user_message") or "") for e in result.get("errors") or []]


# --------------------------------------------------------------------------- #
# file hỏng + AI tìm được tên: cất nguyên vẹn, rồi mới ghi
# --------------------------------------------------------------------------- #

def test_broken_names_json_is_set_aside_intact_before_ai_names_are_written(
    tmp_path: Path, cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = film_ready_for_s7(tmp_path, ai_config(cfg, monkeypatch))
    table = s5_tokenize.names_path(ctx)
    table.write_bytes(BROKEN)

    # Ghi lại tình trạng thư mục ĐÚNG lúc bảng mới được ghi: bản cất phải có trước.
    backups_when_written: list[list[str]] = []
    real_write = io_utils.atomic_write_text

    def spy(path: Any, text: str, **kwargs: Any) -> None:
        if Path(path) == table:
            backups_when_written.append([p.name for p in backups_of(table)])
        real_write(path, text, **kwargs)

    monkeypatch.setattr(io_utils, "atomic_write_text", spy)
    log = Log()

    result = s7_ai.run(ctx, log)

    (backup,) = backups_of(table)
    assert BACKUP_NAME.fullmatch(backup.name), backup.name
    assert backup.read_bytes() == BROKEN, "Bản cất phải còn nguyên từng byte người dùng đã gõ."
    assert backups_when_written == [[backup.name]], "Phải cất file hỏng TRƯỚC rồi mới ghi bảng mới."

    # rồi mới ghi: bảng mới đọc được và có đúng những tên AI vừa tìm
    assert names_store.load_names_file(table).corrupt is False
    flat, _meta = s7_ai.load_names(table)
    assert {"光头", "强", "熊大"} <= set(flat), flat

    names = result["names"]
    assert names["corrupt"] is True
    assert names["backup"] == str(backup)
    assert sorted(names["added"]) == sorted(["光头强", "熊大"])
    assert result["rerun_s5"] is True, "Bảng mới đã ghi thì phải tách lại cụm từ với nó."

    # Người dùng được báo: tên file cất, ở cả ba chỗ họ có thể nhìn thấy.
    assert any(backup.name in m and "không xoá gì" in m for m in notices(result)), notices(result)
    assert log.said(backup.name), log.messages
    assert backup.name in log.messages[-1], "Câu tổng kết cuối bước phải nhắc bản cất."


def test_valid_json_that_is_not_a_table_is_also_set_aside(
    tmp_path: Path, cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chốt chặn cũ coi một danh sách là “bảng không có tên” và ghi đè lên nó."""
    ctx = film_ready_for_s7(tmp_path, ai_config(cfg, monkeypatch))
    table = s5_tokenize.names_path(ctx)
    listed = '["光头强", "熊大", "陈路周"]'.encode("utf-8")
    table.write_bytes(listed)

    result = s7_ai.run(ctx, Log())

    (backup,) = backups_of(table)
    assert backup.read_bytes() == listed
    assert names_store.load_names_file(table).corrupt is False
    assert result["names"]["backup"] == str(backup)


# --------------------------------------------------------------------------- #
# không cất được thì không ghi
# --------------------------------------------------------------------------- #

def test_nothing_is_written_when_the_broken_table_cannot_be_set_aside(
    tmp_path: Path, cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = film_ready_for_s7(tmp_path, ai_config(cfg, monkeypatch))
    table = s5_tokenize.names_path(ctx)
    table.write_bytes(BROKEN)

    real_rename = Path.rename

    def locked(self: Path, target: Any) -> Any:
        if self == table:
            raise PermissionError("file đang bị phần mềm khác khoá")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", locked)
    log = Log()

    result = s7_ai.run(ctx, log)

    assert table.read_bytes() == BROKEN, "Không cất được thì tuyệt đối không ghi đè."
    assert backups_of(table) == []
    assert result["names"]["added"] == []
    assert result["names"]["backup"] == ""
    assert result["rerun_s5"] is False, "Không có bảng mới thì không tách lại cụm từ."
    refusal = [m for m in notices(result) if "chưa lưu gì" in m]
    assert refusal and table.name in refusal[0], notices(result)
    assert log.said("chưa lưu gì"), log.messages


# --------------------------------------------------------------------------- #
# AI không tìm được gì: không đụng file hỏng, nhưng vẫn nói
# --------------------------------------------------------------------------- #

def test_broken_table_is_left_alone_but_reported_when_ai_finds_nothing(
    tmp_path: Path, cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = film_ready_for_s7(tmp_path, ai_config(cfg, monkeypatch, rows=()))
    table = s5_tokenize.names_path(ctx)
    table.write_bytes(BROKEN)
    log = Log()

    result = s7_ai.run(ctx, log)

    assert table.read_bytes() == BROKEN
    assert backups_of(table) == []
    assert result["names"]["corrupt"] is True
    assert result["rerun_s5"] is False
    said = [m for m in notices(result) if "chưa đụng tới file đó" in m]
    assert said and "lỗi cú pháp" in said[0], notices(result)
    assert log.said("chưa đụng tới file đó"), log.messages


# --------------------------------------------------------------------------- #
# bảng lành: không bao giờ sinh bản cất, tên tiếng Việt đã gõ vẫn còn
# --------------------------------------------------------------------------- #

def test_healthy_table_is_never_backed_up_and_keeps_hand_typed_vietnamese(
    tmp_path: Path, cfg: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = film_ready_for_s7(tmp_path, ai_config(cfg, monkeypatch))
    table = s5_tokenize.names_path(ctx)
    entry = s7_ai.NameEntry(
        han="光头强", split=["光头", "强"], pinyin=["Guāngtóu", "Qiáng"], type="person",
        vi="Cường đầu trọc",
    )
    assert s7_ai.save_names(table, {"光头": "Guāngtóu", "强": "Qiáng"}, [entry]) is None

    result = s7_ai.run(ctx, Log())

    assert backups_of(table) == []
    assert result["names"]["backup"] == ""
    assert result["names"]["corrupt"] is False
    assert not any("hong-" in m for m in notices(result))
    _flat, meta = s7_ai.load_names(table)
    vi = {row["han"]: row.get("vi") for row in meta["entries"]}
    assert vi["光头强"] == "Cường đầu trọc"
    assert "熊大" in vi


# --------------------------------------------------------------------------- #
# các hàm công khai của S7 đi qua names_store
# --------------------------------------------------------------------------- #

def test_save_names_returns_the_backup_of_a_broken_table(tmp_path: Path) -> None:
    table = tmp_path / "names.json"
    table.write_bytes(BROKEN)

    assert s7_ai.load_names(table) == ({}, {})
    assert s7_ai._names_unreadable(table) is True

    backup = s7_ai.save_names(table, {"强": "Qiáng"}, [])

    assert backup is not None and backup.read_bytes() == BROKEN
    assert s7_ai.load_names(table)[0] == {"强": "Qiáng"}
    assert s7_ai._names_unreadable(table) is False


def test_names_unreadable_catches_a_table_that_is_not_a_table(tmp_path: Path) -> None:
    table = tmp_path / "names.json"
    assert s7_ai._names_unreadable(table) is False, "Chưa có file là bình thường, không phải hỏng."
    table.write_text("[1, 2, 3]", encoding="utf-8")
    assert s7_ai._names_unreadable(table) is True


def test_s7_reads_and_writes_names_only_through_names_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    table = tmp_path / "names.json"
    calls: list[tuple[str, Path]] = []

    def fake_load(path: Any) -> names_store.NamesLoad:
        calls.append(("load", Path(path)))
        return names_store.NamesLoad({"names": {"强": "Qiáng"}}, False, None)

    def fake_save(path: Any, data: Any) -> None:
        calls.append(("save", Path(path)))
        assert data["names"] == {"强": "Qiáng"}

    monkeypatch.setattr(names_store, "load_names_file", fake_load)
    monkeypatch.setattr(names_store, "save_names_file", fake_save)

    assert s7_ai.load_names(table)[0] == {"强": "Qiáng"}
    s7_ai.save_names(table, {"强": "Qiáng"}, [])

    assert calls == [("load", table), ("save", table)]
    assert not table.exists(), "S7 không được tự ghi names.json ngoài names_store."
