"""Kho bảng tên riêng (``names.json``) an toàn — chỗ DUY NHẤT đọc/ghi file này.

Vì sao có module này
====================
``names.json`` là thứ người dùng gõ tay nhiều nhất trong cả tool: tên nhân vật,
cách tách cụm, tên tiếng Việt cố định. Họ sửa nó qua tab "Tên riêng", và cũng
hay mở thẳng bằng Notepad/TextEdit rồi quên một dấu phẩy. Trước đây máy chủ và
chặng S7 mỗi bên tự đọc/ghi file này, và cả hai cùng mắc một kiểu hỏng:

* bộ đọc cố ý dễ dãi — file hỏng đọc ra bảng RỖNG, để một lỗi cú pháp không làm
  đổ cả lượt chạy 40 phút;
* nhưng sau đó bộ ghi lại ghi đè lên đúng file hỏng ấy bằng bảng mới.

Kết quả: file chỉ sai đúng một dấu phẩy mà mọi tên cũ bên trong mất sạch, không
một dòng nào trên màn hình nói điều đó. Với người dùng không phải dân IT, mất
công gõ cả buổi là thứ khiến họ bỏ hẳn phần mềm.

Hợp đồng (H4) của module:

* :func:`load_names_file` **không bao giờ ném lỗi**. File hỏng trả về bảng rỗng
  NHƯNG kèm ``corrupt=True`` và một câu tiếng Việt — bên gọi luôn biết "rỗng vì
  hỏng" khác "rỗng vì chưa có".
* :func:`backup_corrupt_names` cất file hỏng bằng cách **đổi tên** (không chép):
  cùng thư mục nên là một thao tác nguyên tử, giữ nguyên từng byte, kể cả khi
  file không phải UTF-8, và không phải mở file ra.
* :func:`save_names_file` luôn cất file đang hỏng TRƯỚC khi ghi; không cất được
  thì từ chối ghi (:class:`NamesSaveRefused`). Ghi đè một file mà mình không cất
  được bản sao chính là làm mất dữ liệu.

Mọi đọc/ghi nội dung đi qua :mod:`srtgen.io_utils` (luật "chỉ io_utils gọi
``open()``"); ở đây chỉ có đổi tên file.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Mapping

from srtgen import io_utils

__all__ = [
    "CORRUPT_NAMES_TAG",
    "NamesLoad",
    "NamesSaveRefused",
    "load_names_file",
    "backup_corrupt_names",
    "save_names_file",
]

#: Đuôi của bản cất một bảng tên hỏng: ``names.json`` → ``names.json.hong-20260910-175012``.
#: Không kết thúc bằng ``.json`` là cố ý: không bộ đọc nào của tool (S5, S7, màn
#: hình) có thể nhầm bản cất ấy là bảng tên thật và đọc nó thay cho bảng mới.
CORRUPT_NAMES_TAG: Final[str] = ".hong-"

#: Số lần thử tên bản cất trong cùng một giây (``-1``, ``-2``…). Hai lần lưu trong
#: cùng một giây không được đè bản cất của nhau; 100 là thừa cho mọi tình huống
#: thật mà vẫn bảo đảm vòng lặp dừng.
_MAX_BACKUP_ATTEMPTS: Final[int] = 100


@dataclass
class NamesLoad:
    """Kết quả đọc ``names.json``.

    * ``data`` — nội dung file (một ``dict``); rỗng khi chưa có file HOẶC file hỏng.
    * ``corrupt`` — ``True`` khi file CÓ trên đĩa mà không dùng được: sai cú pháp
      JSON, không phải UTF-8, không phải một bảng (danh sách, ``null``…), hoặc
      không mở được (đang bị phần mềm khác khoá, không có quyền đọc).
    * ``message`` — câu tiếng Việt cho người dùng khi ``corrupt``; ``None`` khi ổn.

    Tách "hỏng" khỏi "trống" là toàn bộ lý do của lớp này: màn hình thấy bảng
    rỗng mà không biết nó hỏng thì sẽ cho bấm Lưu, và Lưu thì ghi đè.
    """

    data: dict = field(default_factory=dict)
    corrupt: bool = False
    message: str | None = None


class NamesSaveRefused(OSError):
    """Từ chối ghi ``names.json`` vì không cất được file hỏng đang nằm đó.

    Kế thừa ``OSError`` để bên gọi đang bắt lỗi ghi file (``except OSError``) vẫn
    bắt được mà không phải sửa gì; ``user_message`` là câu tiếng Việt hiện thẳng
    cho người dùng.
    """

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


# --------------------------------------------------------------------------- #
# đọc
# --------------------------------------------------------------------------- #

def load_names_file(path: str | Path) -> NamesLoad:
    """Đọc ``names.json`` mà không bao giờ ném lỗi.

    Chưa có file → ``NamesLoad({}, corrupt=False, message=None)``: phim chưa có
    bảng tên là chuyện bình thường. File có mà không dùng được → bảng rỗng kèm
    ``corrupt=True`` và câu tiếng Việt nói rõ tên cũ VẪN CÒN trong file, để
    người dùng không hoảng mà cũng không gõ đè lên.
    """
    file = Path(path)
    try:
        if not file.is_file():
            return NamesLoad({}, False, None)
    except OSError:
        return NamesLoad({}, False, None)
    try:
        raw = io_utils.read_json(file)
    except ValueError:
        # ValueError gồm cả JSONDecodeError (sai cú pháp) lẫn UnicodeDecodeError
        # (file lưu bằng bảng mã khác UTF-8) — với người dùng, cả hai là "lỗi file".
        return NamesLoad({}, True, _syntax_message(file))
    except OSError:
        return NamesLoad({}, True, _unreadable_message(file))
    if not isinstance(raw, dict):
        return NamesLoad({}, True, _shape_message(file))
    return NamesLoad(raw, False, None)


def _syntax_message(file: Path) -> str:
    return (
        f"File bảng tên riêng “{file.name}” đang bị lỗi cú pháp (thường do sửa tay "
        "thiếu một dấu phẩy hoặc dấu ngoặc), nên tool không đọc được tên nào trong "
        "đó. Các tên cũ vẫn còn nguyên trong file. Nếu bạn lưu bảng tên mới, tool sẽ "
        f"giữ file hỏng lại thành bản sao “{file.name}{CORRUPT_NAMES_TAG}…” ngay trong "
        "cùng thư mục, không xoá gì."
    )


def _unreadable_message(file: Path) -> str:
    return (
        f"Không mở được file bảng tên riêng “{file.name}” (có thể file đang bị phần "
        "mềm khác mở, hoặc không có quyền đọc). Bảng hiện ra trống nhưng các tên cũ "
        "vẫn còn trong file. Hãy đóng phần mềm đang mở file đó rồi thử lại."
    )


def _shape_message(file: Path) -> str:
    return (
        f"File bảng tên riêng “{file.name}” không đúng dạng một bảng tên, nên tool "
        "không đọc được tên nào dù file vẫn còn nội dung. Nếu bạn lưu bảng tên mới, "
        f"tool sẽ giữ file cũ lại thành bản sao “{file.name}{CORRUPT_NAMES_TAG}…” "
        "ngay trong cùng thư mục, không xoá gì."
    )


# --------------------------------------------------------------------------- #
# cất bản hỏng
# --------------------------------------------------------------------------- #

def backup_corrupt_names(path: str | Path) -> Path | None:
    """Đổi tên file ở ``path`` thành ``<tên>.hong-<YYYYmmdd-HHMMSS>``; trả đường dẫn mới.

    ``None`` khi không có file nào để cất. Đổi tên chứ không chép: giữ nguyên
    từng byte (một bản chép qua bộ đọc văn bản sẽ chuẩn hoá NFC, bỏ BOM, đổi
    CRLF — tức là không còn đúng file người dùng gõ). Tên đã có thì thêm ``-1``,
    ``-2``…; không bao giờ đè một bản cất cũ, và không bao giờ xoá bản cất nào.

    Không đổi tên được thì ném :class:`NamesSaveRefused` — bên gọi (thường là
    :func:`save_names_file`) phải dừng lại chứ không được ghi tiếp.
    """
    file = Path(path)
    try:
        if not file.is_file():
            return None
    except OSError:
        return None
    refusal = (
        f"File bảng tên riêng “{file.name}” đang hỏng và tool chưa cất được bản sao "
        "của nó, nên chưa lưu gì để khỏi mất các tên cũ. Hãy đóng mọi phần mềm đang "
        "mở file đó rồi lưu lại."
    )
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for attempt in range(_MAX_BACKUP_ATTEMPTS):
        suffix = f"{CORRUPT_NAMES_TAG}{stamp}" + (f"-{attempt}" if attempt else "")
        target = file.with_name(file.name + suffix)
        # Kiểm trước khi đổi tên: trên macOS/Linux ``rename`` lặng lẽ ĐÈ file đích,
        # tức là đè mất một bản cất cũ. Windows thì ném FileExistsError — cũng thử tiếp.
        if target.exists():
            continue
        try:
            file.rename(target)
        except FileExistsError:
            continue
        except OSError as err:
            raise NamesSaveRefused(refusal) from err
        return target
    raise NamesSaveRefused(refusal)


# --------------------------------------------------------------------------- #
# ghi
# --------------------------------------------------------------------------- #

def save_names_file(path: str | Path, data: Mapping[str, Any]) -> Path | None:
    """Ghi ``data`` vào ``names.json``; trả đường dẫn bản cất nếu phải cất file hỏng.

    Thứ tự là cả điểm mấu chốt: kiểm file hiện tại → hỏng thì cất (đổi tên) →
    rồi mới ghi. Không cất được thì :class:`NamesSaveRefused` và KHÔNG ghi gì.

    Ghi qua ``io_utils.atomic_write_text`` (file tạm rồi thay thế): một lần ghi dở
    — mất điện, máy ngủ giữa chừng — chính là cách phổ biến nhất sinh ra một
    ``names.json`` hỏng, nên bản thân lần ghi này không được phép tạo ra một cái.

    Dữ liệu được kiểm trước mọi thao tác trên đĩa: một yêu cầu hỏng không được
    làm file của người dùng đổi tên.
    """
    if not isinstance(data, Mapping):
        raise TypeError("names.json phải là một bảng (dict)")
    body = json.dumps(dict(data), ensure_ascii=False, indent=2, sort_keys=False)

    file = Path(path)
    backup: Path | None = None
    if load_names_file(file).corrupt:
        backup = backup_corrupt_names(file)
    io_utils.ensure_dir(file.parent)
    io_utils.atomic_write_text(file, body, bom=False)
    return backup
