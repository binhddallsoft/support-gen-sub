"""Kho bảng tên riêng an toàn (``srtgen/core/names_store.py``, hợp đồng H4).

Vì sao có file này: ``names.json`` là thứ người dùng gõ tay nhiều nhất, và cũng
là thứ hay bị sửa tay sai một dấu phẩy. Bộ đọc cũ đọc file hỏng ra bảng rỗng,
rồi bộ ghi ghi đè lên đúng file đó — mọi tên cũ mất sạch mà không ai hay. Các
test dưới đây khoá ba lời hứa:

* đọc file hỏng không bao giờ ném lỗi, nhưng luôn nói rõ là file HỎNG (khác "trống");
* trước khi ghi, file hỏng được cất thành ``names.json.hong-<thời điểm>``,
  giữ nguyên TỪNG BYTE (kể cả BOM, CRLF, byte không phải UTF-8);
* không cất được thì không ghi.

Chạy trong thư mục tạm, không mạng, không cài gì.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from srtgen.core import names_store
from srtgen.core.names_store import (
    CORRUPT_NAMES_TAG,
    NamesSaveRefused,
    backup_corrupt_names,
    load_names_file,
    save_names_file,
)

#: Một names.json sửa tay hỏng thật: thừa dấu phẩy, thiếu ngoặc đóng. Kèm BOM và
#: CRLF có chủ ý — bản cất chép qua bộ đọc văn bản sẽ mất cả hai, đổi tên thì không.
BROKEN = (
    "﻿{\r\n"
    '  "names": {"光头": "Guāngtóu", "强": "Qiáng",},\r\n'
    '  "entries": [\r\n'
).encode("utf-8")

GOOD: dict[str, Any] = {
    "version": 1,
    "names": {"光头": "Guāngtóu", "强": "Qiáng"},
    "entries": [{"han": "光头强", "split": ["光头", "强"], "vi": "Cường đầu trọc"}],
}

BACKUP_NAME = re.compile(r"names\.json\.hong-\d{8}-\d{6}(-\d+)?")


def backups(folder: Path) -> list[Path]:
    return sorted(folder.glob(f"names.json{CORRUPT_NAMES_TAG}*"))


# --------------------------------------------------------------------------- #
# đọc
# --------------------------------------------------------------------------- #

def test_missing_file_is_empty_not_corrupt(tmp_path: Path) -> None:
    """Phim chưa có bảng tên là chuyện bình thường — không phải "hỏng"."""
    res = load_names_file(tmp_path / "names.json")
    assert res.data == {} and res.corrupt is False and res.message is None


def test_valid_file_loads_as_is(tmp_path: Path) -> None:
    path = tmp_path / "names.json"
    save_names_file(path, GOOD)
    res = load_names_file(path)
    assert res.corrupt is False and res.message is None
    assert res.data == GOOD


def test_syntax_error_is_reported_not_raised(tmp_path: Path) -> None:
    """File sai cú pháp: bảng rỗng, ``corrupt=True``, câu tiếng Việt — KHÔNG ném lỗi."""
    path = tmp_path / "names.json"
    path.write_bytes(BROKEN)
    res = load_names_file(path)
    assert res.corrupt is True
    assert res.data == {}
    assert res.message and "names.json" in res.message
    assert "lỗi cú pháp" in res.message and "vẫn còn nguyên" in res.message
    assert path.read_bytes() == BROKEN   # đọc không được phép đụng vào file


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff\xfe{\x00\"\x00",           # lưu bằng bảng mã khác UTF-8 (UTF-16)
        b"",                              # file rỗng — lần ghi trước bị cắt ngang
        b"[1, 2, 3]",                     # JSON hợp lệ nhưng không phải một bảng
        b"null",
    ],
)
def test_unusable_content_is_corrupt_not_raised(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "names.json"
    path.write_bytes(raw)
    res = load_names_file(path)
    assert res.corrupt is True and res.data == {} and res.message


# --------------------------------------------------------------------------- #
# cất bản hỏng rồi mới ghi
# --------------------------------------------------------------------------- #

def test_save_backs_up_corrupt_file_byte_for_byte_before_writing(tmp_path: Path) -> None:
    path = tmp_path / "names.json"
    path.write_bytes(BROKEN)

    backup = save_names_file(path, GOOD)

    assert backup is not None
    assert backup.parent == path.parent
    assert BACKUP_NAME.fullmatch(backup.name), backup.name
    assert backup.read_bytes() == BROKEN          # còn nguyên từng byte
    after = load_names_file(path)
    assert after.corrupt is False and after.data == GOOD
    assert backups(tmp_path) == [backup]


def test_backup_name_never_ends_in_json(tmp_path: Path) -> None:
    """Không bộ đọc nào (S5, S7, màn hình) được nhầm bản cất là bảng tên thật."""
    path = tmp_path / "names.json"
    path.write_bytes(BROKEN)
    backup = backup_corrupt_names(path)
    assert backup is not None and not backup.name.endswith(".json")
    assert not path.exists()
    assert load_names_file(path).corrupt is False   # chỗ cũ giờ là "chưa có bảng"


def test_save_over_healthy_file_makes_no_backup(tmp_path: Path) -> None:
    path = tmp_path / "names.json"
    save_names_file(path, {"names": {"甲": "Jiǎ"}})
    assert save_names_file(path, GOOD) is None
    assert backups(tmp_path) == []
    assert load_names_file(path).data == GOOD


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(names_store.time, "strftime", lambda *_a: "20260910-183000")
    path = tmp_path / "names.json"
    first_broken = BROKEN
    second_broken = b'{"names": {"\xe7\x94\xb2": }'

    path.write_bytes(first_broken)
    first = save_names_file(path, GOOD)
    path.write_bytes(second_broken)
    second = save_names_file(path, GOOD)

    assert first is not None and second is not None and first != second
    assert first.name == "names.json.hong-20260910-183000"
    assert second.name == "names.json.hong-20260910-183000-1"
    assert first.read_bytes() == first_broken
    assert second.read_bytes() == second_broken


def test_refuses_to_write_when_the_corrupt_file_cannot_be_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Không đổi tên được (file đang bị Notepad khoá…) thì KHÔNG ghi đè."""
    path = tmp_path / "names.json"
    path.write_bytes(BROKEN)

    def locked(self: Path, target: Any) -> Path:
        raise PermissionError("file đang được mở ở phần mềm khác")

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(NamesSaveRefused) as err:
        save_names_file(path, GOOD)
    assert isinstance(err.value, OSError)          # bên gọi đang bắt OSError vẫn bắt được
    assert "chưa lưu gì" in err.value.user_message
    assert path.read_bytes() == BROKEN
    assert backups(tmp_path) == []


def test_bad_data_is_rejected_before_the_disk_is_touched(tmp_path: Path) -> None:
    """Dữ liệu gửi lên hỏng thì file của người dùng không được đổi tên, không bị ghi."""
    path = tmp_path / "names.json"
    path.write_bytes(BROKEN)
    with pytest.raises(TypeError):
        save_names_file(path, ["không", "phải", "bảng"])  # type: ignore[arg-type]
    assert path.read_bytes() == BROKEN
    assert backups(tmp_path) == []


def test_backup_of_missing_file_is_none(tmp_path: Path) -> None:
    assert backup_corrupt_names(tmp_path / "names.json") is None


def test_save_creates_the_folder_and_writes_readable_json(tmp_path: Path) -> None:
    path = tmp_path / "work" / "vid" / "names.json"
    assert save_names_file(path, GOOD) is None
    assert load_names_file(path).data == GOOD
    assert not list(path.parent.glob("*.tmp"))    # ghi nguyên tử, không để lại file tạm
