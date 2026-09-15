"""Bối cảnh một lần chạy: thư mục làm việc, cấu hình đã gộp, và điểm lưu từng chặng.

Vì sao module này tồn tại:

* **Chặng S2 (nghe và gỡ băng) mất 20-40 phút trên iMac 2017.** Nếu người dùng lỡ
  đóng máy ở chặng S6 mà phải nghe lại từ đầu thì tool coi như hỏng. `save_stage` /
  `has_stage` / `load_stage` là toàn bộ cơ chế chạy tiếp giữa chừng: mỗi chặng ghi
  kết quả ra `work/<video_id>/S<n>_<tên>.json`, chặng sau chỉ đọc lại.
* **Tên thư mục làm việc phải suy ra được từ nguồn**, để chạy lại cùng một video là
  dùng lại đúng thư mục cũ chứ không sinh thư mục mới. Đó là việc của `make_video_id`.
* **Cấu hình chỉ có một nguồn sự thật** là `srtgen/config/default.yaml`. Module này
  nạp file đó, gộp sâu với `profiles[...]` rồi gộp tiếp với tham số dòng lệnh.

Ba ràng buộc của dự án được tôn trọng ở đây:

1. Chỉ `srtgen.io_utils` được gọi `open()` — mọi thao tác đọc/ghi bên dưới đều đi qua nó.
2. Không `print()`; hàm nào cần báo cho người dùng thì ném lỗi có câu tiếng Việt rõ ràng.
3. Thư viện nặng (`yaml`, `platformdirs`) import **lười** bên trong hàm, và có đường lui
   khi thiếu — để `srtgen doctor` và web UI vẫn khởi động được trên máy chưa cài gì.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import parse_qs, urlparse

from srtgen.io_utils import ensure_dir, nfc, read_json, read_text, write_json

if TYPE_CHECKING:  # chỉ để gợi ý kiểu; import thật sẽ tạo phụ thuộc cứng vào core.token
    from srtgen.core.token import Document

__all__ = [
    "APP_NAME",
    "CONFIG_DIR",
    "DEFAULT_CONFIG_PATH",
    "Context",
    "deep_merge",
    "extract_youtube_id",
    "load_config",
    "make_video_id",
    "new_context",
    "user_cache_dir",
    "user_data_dir",
]

APP_NAME = "SrtGen"

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "default.yaml"


# --------------------------------------------------------------------------- #
# Context
# --------------------------------------------------------------------------- #

def _never_cancelled() -> bool:
    """Mặc định của `Context.cancelled`: chạy dòng lệnh thì không có nút Dừng."""
    return False


@dataclass
class Context:
    """Mọi thứ một chặng cần biết, gói trong một tham số duy nhất.

    Giữ chữ ký `run(ctx, on_progress)` cho cả 8 chặng có nghĩa là thêm một tham số
    mới sau này (ví dụ đường dẫn `names.json`) chỉ phải sửa ở đây, không phải sửa 8 file.
    """

    video_id: str
    work_dir: Path
    out_dir: Path
    cfg: dict[str, Any]
    doc: "Document | None" = None
    meta: dict[str, Any] = field(default_factory=dict)
    cancelled: Callable[[], bool] = _never_cancelled

    # -- điểm lưu từng chặng ------------------------------------------------ #

    def stage_path(self, n: int, name: str) -> Path:
        """`work/<video_id>/S<n>_<name>.json` — số chặng nằm ở đầu tên để thư mục tự sắp xếp."""
        return self.work_dir / f"S{n}_{name}.json"

    def save_stage(self, n: int, name: str, data: Any) -> None:
        """Ghi kết quả một chặng.

        Dataclass và đối tượng có `to_dict()` được chuyển sang dạng thuần trước khi
        ghi, để chặng gọi không phải nhớ tự chuyển — quên một chỗ là mất cả chặng
        chạy lại từ đầu.
        """
        ensure_dir(self.work_dir)
        write_json(self.stage_path(n, name), _jsonable(data))

    def load_stage(self, n: int, name: str) -> Any | None:
        """Đọc lại kết quả đã lưu; `None` nghĩa là "chưa có, hãy chạy chặng này".

        File hỏng (máy tắt giữa lúc ghi) cũng trả `None` thay vì ném lỗi: chạy lại
        một chặng bao giờ cũng đỡ hơn là bắt người dùng đi xoá file JSON bằng tay.

        **Cố ý KHÔNG đi qua `has_stage()`.** Hai câu hỏi khác hẳn nhau:

        * `has_stage(n)` = "chặng n có kết quả cũ DÙNG LẠI ĐƯỢC không" — câu này
          phải tôn trọng `force_from`, vì chạy lại từ chặng k nghĩa là mọi chặng
          từ k trở đi phải làm lại.
        * `load_stage(n)` = "trên đĩa có file của chặng n không" — câu này KHÔNG
          được biết gì về `force_from`.

        Gộp hai câu lại gây một lỗi chặn đo được: chạy lại từ chặng 4 thì chặng 4
        chạy xong, ghi `S4_cue.json`, rồi chặng 5 đọc chính file vừa ghi đó và
        nhận `None` (vì `force_from` 4 <= 4), nên cả lần chạy lại chết ở chặng
        ngay sau chỗ người dùng chọn. Từng chặng vẫn an toàn vì tất cả đều hỏi
        `has_stage()` trước khi dùng lại kết quả của CHÍNH NÓ.
        """
        try:
            path = self.stage_path(n, name)
            if not (path.is_file() and path.stat().st_size > 0):
                return None
            return read_json(path)
        except (ValueError, OSError):
            return None

    def has_stage(self, n: int, name: str) -> bool:
        """Có kết quả cũ dùng lại được không.

        Đã gộp sẵn luật `force_from` của build-spec: khi người dùng yêu cầu chạy lại
        từ chặng `k`, mọi chặng `n >= k` bị coi như chưa có kết quả. Nhờ vậy từng
        chặng chỉ cần hỏi `has_stage()` mà không phải tự nhớ luật này.
        """
        force = self.force_from
        if force is not None and force <= n:
            return False
        path = self.stage_path(n, name)
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    # -- tiện ích ----------------------------------------------------------- #

    @property
    def force_from(self) -> int | None:
        """Số chặng nhỏ nhất bị ép chạy lại; `None` khi không ép.

        Giá trị mặc định trong `default.yaml` là 99 (lớn hơn mọi chặng) để biểu thức
        `cfg["force_from"] <= n` trong build-spec vẫn đúng nghĩa mà không cần kiểm `None`.
        """
        value = self.cfg.get("force_from")
        if value is None or isinstance(value, bool) or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def is_cancelled(self) -> bool:
        """Hỏi UI xem người dùng đã bấm Dừng chưa; callback lỗi thì coi như chưa."""
        try:
            return bool(self.cancelled())
        except Exception:  # pragma: no cover - callback do UI cung cấp, không tin được
            return False


def _jsonable(value: Any) -> Any:
    """Chuyển đệ quy về kiểu `json` hiểu được, giữ nguyên thứ tự khoá."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    return value  # để json.dumps tự báo lỗi nếu thật sự không tuần tự hoá được


# --------------------------------------------------------------------------- #
# cấu hình
# --------------------------------------------------------------------------- #

def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Gộp sâu `patch` lên `base`, trả về dict mới (không sửa đầu vào).

    Danh sách bị **thay thế** chứ không nối thêm: một profile ghi
    `heteronym_chars: [...]` là muốn nói "dùng đúng danh sách này", không phải
    "thêm vào danh sách mặc định".
    """
    out: dict[str, Any] = dict(base)
    for key, value in patch.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = deep_merge(current, value)
        else:
            out[key] = value
    return out


def load_config(profile: str | None = None, overrides: dict | None = None) -> dict:
    """Nạp `config/default.yaml`, gộp `profiles[profile]`, rồi gộp `overrides`.

    Thứ tự ưu tiên (sau đè lên trước): file mặc định → profile → tham số dòng lệnh.
    `profile=None` nghĩa là dùng `default_profile` ghi trong chính file cấu hình,
    nhờ vậy đổi profile mặc định không phải sửa code.
    """
    data = _load_yaml_file(DEFAULT_CONFIG_PATH)
    if not isinstance(data, dict):
        raise ValueError(
            f"File cấu hình {DEFAULT_CONFIG_PATH} không đúng định dạng. "
            "Hãy lấy lại bản gốc của tool."
        )

    profiles = data.get("profiles") or {}
    name = profile if profile is not None else str(data.get("default_profile") or "")
    name = name.strip()
    if name:
        patch = profiles.get(name) if isinstance(profiles, dict) else None
        if patch is None:
            available = ", ".join(sorted(profiles)) if isinstance(profiles, dict) else ""
            raise ValueError(
                f"Không có cấu hình mẫu tên “{name}”. "
                f"Các lựa chọn hiện có: {available or 'không có'}."
            )
        data = deep_merge(data, patch if isinstance(patch, dict) else {})
    data["profile"] = name

    if overrides:
        data = deep_merge(data, overrides)
    return data


def _load_yaml_file(path: Path) -> Any:
    """Đọc YAML bằng PyYAML nếu có, không thì bằng bộ đọc tối giản bên dưới.

    Lý do phải có đường lui: `srtgen doctor` là lệnh người dùng chạy **khi máy chưa
    cài đủ thứ**. Nếu nó cũng chết vì thiếu PyYAML thì không còn lệnh nào chỉ ra
    được là đang thiếu cái gì.
    """
    text = read_text(path)
    try:
        import yaml
    except ImportError:
        return _parse_simple_yaml(text)
    return yaml.safe_load(text) or {}


# --------------------------------------------------------------------------- #
# bộ đọc YAML tối giản (chỉ đủ dùng cho default.yaml của chính chúng ta)
# --------------------------------------------------------------------------- #
#
# Phạm vi cố tình hẹp: ánh xạ/danh sách lồng nhau theo thụt lề, danh sách và ánh xạ
# dạng gọn trên một dòng, chuỗi có nháy, số, true/false/null, chú thích `#`.
# KHÔNG hỗ trợ anchor, alias, chuỗi nhiều dòng, thẻ kiểu. Gặp cú pháp lạ thì ném lỗi
# chứ không đoán — đọc nhầm cấu hình còn tệ hơn là báo thiếu PyYAML.

_TRUE = {"true", "yes", "on"}
_FALSE = {"false", "no", "off"}
_NULL = {"", "~", "null"}
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$")


def _parse_simple_yaml(text: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw in text.split("\n"):
        line = raw.replace("\t", "  ")
        body = _strip_comment(line)
        if not body.strip():
            continue
        if body.strip() in ("---", "..."):
            continue
        lines.append((len(body) - len(body.lstrip(" ")), body.strip()))
    if not lines:
        return {}
    value, index = _parse_nodes(lines, 0, lines[0][0])
    if index != len(lines):
        raise ValueError(
            "Không đọc được file cấu hình bằng bộ đọc rút gọn "
            f"(dừng ở dòng “{lines[index][1]}”). Hãy cài PyYAML: pip install PyYAML"
        )
    return value


def _strip_comment(line: str) -> str:
    """Bỏ phần chú thích, nhưng `#` nằm trong chuỗi có nháy thì giữ nguyên."""
    out: list[str] = []
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote is not None:
            out.append(ch)
            if ch == quote and not (quote == '"' and i and line[i - 1] == "\\"):
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#" and (not out or out[-1] in " \t"):
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _parse_nodes(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[Any, int]:
    if lines[i][1] == "-" or lines[i][1].startswith("- "):
        return _parse_sequence(lines, i, indent)
    return _parse_mapping(lines, i, indent)


def _parse_sequence(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while i < len(lines) and lines[i][0] == indent:
        text = lines[i][1]
        if not (text == "-" or text.startswith("- ")):
            break
        body = text[1:].strip()
        if not body:
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                value, i = _parse_nodes(lines, i, lines[i][0])
            else:
                value = None
        elif _key_end(body) is not None:
            # "- key: value" — coi phần tử như một ánh xạ bắt đầu ngay tại đây
            virtual = [(indent + 2, body)] + lines[i + 1:]
            value, consumed = _parse_mapping(virtual, 0, indent + 2)
            i += consumed
        else:
            value = _parse_value(body)
            i += 1
        items.append(value)
    return items, i


def _parse_mapping(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while i < len(lines) and lines[i][0] == indent:
        text = lines[i][1]
        if text == "-" or text.startswith("- "):
            break
        end = _key_end(text)
        if end is None:
            raise ValueError(f"Dòng cấu hình không hiểu được: “{text}”")
        key = _unquote(text[:end].strip())
        rest = text[end + 1:].strip()
        if rest:
            out[key] = _parse_value(rest)
            i += 1
            continue
        i += 1
        if i < len(lines) and lines[i][0] > indent:
            out[key], i = _parse_nodes(lines, i, lines[i][0])
        elif i < len(lines) and lines[i][0] == indent and (
            lines[i][1] == "-" or lines[i][1].startswith("- ")
        ):
            out[key], i = _parse_sequence(lines, i, indent)
        else:
            out[key] = None
    return out, i


def _key_end(text: str) -> int | None:
    """Vị trí dấu `:` ngăn khoá với giá trị, hoặc None nếu dòng không phải cặp khoá-giá trị."""
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            return None  # đã sang vùng giá trị dạng gọn
        elif ch == ":" and (i + 1 == len(text) or text[i + 1] in " \t"):
            return i
    return None


def _parse_value(text: str) -> Any:
    text = text.strip()
    if text[:1] in ("[", "{"):
        value, pos = _parse_flow(text, 0)
        if text[pos:].strip():
            raise ValueError(f"Giá trị cấu hình dư ký tự: “{text}”")
        return value
    return _parse_scalar(text)


def _parse_flow(text: str, i: int) -> tuple[Any, int]:
    i = _skip_space(text, i)
    if i >= len(text):
        raise ValueError("Giá trị cấu hình bị cắt cụt")
    if text[i] == "[":
        items: list[Any] = []
        i = _skip_space(text, i + 1)
        if i < len(text) and text[i] == "]":
            return items, i + 1
        while True:
            value, i = _parse_flow_item(text, i, "]")
            items.append(value)
            i = _skip_space(text, i)
            if i < len(text) and text[i] == ",":
                i = _skip_space(text, i + 1)
                if i < len(text) and text[i] == "]":
                    return items, i + 1
                continue
            if i < len(text) and text[i] == "]":
                return items, i + 1
            raise ValueError(f"Danh sách cấu hình thiếu dấu “]”: “{text}”")
    if text[i] == "{":
        out: dict[str, Any] = {}
        i = _skip_space(text, i + 1)
        if i < len(text) and text[i] == "}":
            return out, i + 1
        while True:
            key_raw, i = _read_until(text, i, ":")
            i = _skip_space(text, i + 1)
            value, i = _parse_flow_item(text, i, "}")
            out[_unquote(key_raw.strip())] = value
            i = _skip_space(text, i)
            if i < len(text) and text[i] == ",":
                i = _skip_space(text, i + 1)
                if i < len(text) and text[i] == "}":
                    return out, i + 1
                continue
            if i < len(text) and text[i] == "}":
                return out, i + 1
            raise ValueError(f"Ánh xạ cấu hình thiếu dấu “}}”: “{text}”")
    raise ValueError(f"Giá trị cấu hình không hiểu được: “{text[i:]}”")


def _parse_flow_item(text: str, i: int, closer: str) -> tuple[Any, int]:
    i = _skip_space(text, i)
    if i < len(text) and text[i] in "[{":
        return _parse_flow(text, i)
    raw, i = _read_until(text, i, "," + closer)
    return _parse_scalar(raw.strip()), i


def _read_until(text: str, i: int, stoppers: str) -> tuple[str, int]:
    out: list[str] = []
    quote: str | None = None
    while i < len(text):
        ch = text[i]
        if quote is not None:
            out.append(ch)
            if ch == quote and not (quote == '"' and text[i - 1] == "\\"):
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch in stoppers:
            return "".join(out), i
        else:
            out.append(ch)
        i += 1
    raise ValueError(f"Giá trị cấu hình bị cắt cụt: “{text}”")


def _skip_space(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t":
        i += 1
    return i


def _parse_scalar(text: str) -> Any:
    text = text.strip()
    if text[:1] in ("\"", "'") and text[-1:] == text[:1] and len(text) >= 2:
        return _unquote(text)
    low = text.lower()
    if low in _NULL:
        return None
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return text


def _unquote(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        body = text[1:-1]
        if text[0] == '"':
            return (
                body.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )
        return body.replace("''", "'")
    return text


# --------------------------------------------------------------------------- #
# thư mục người dùng
# --------------------------------------------------------------------------- #

def user_data_dir() -> Path:
    """Nơi để `work/`, `output/`, `names/` — dữ liệu người dùng muốn giữ."""
    return _platform_dir("user_data_dir", Path.home() / ".srtgen")


def user_cache_dir() -> Path:
    """Nơi để model Whisper và cache câu trả lời AI — xoá đi vẫn chạy lại được."""
    return _platform_dir("user_cache_dir", Path.home() / ".srtgen" / "cache")


def _platform_dir(func_name: str, fallback: Path) -> Path:
    """Hỏi `platformdirs` cho đúng quy ước từng hệ; thiếu thư viện thì lui về `~/.srtgen`.

    Không để thiếu `platformdirs` làm chết `srtgen doctor` — đó chính là lệnh dùng
    để phát hiện ra là đang thiếu nó.
    """
    try:
        import platformdirs

        return Path(getattr(platformdirs, func_name)(APP_NAME, appauthor=False))
    except (ImportError, AttributeError, OSError, TypeError):
        return fallback


# --------------------------------------------------------------------------- #
# định danh nguồn
# --------------------------------------------------------------------------- #

_YOUTUBE_HOSTS = {
    "youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtube-nocookie.com",
    "youtu.be",
}
_YT_PATH_PREFIXES = {"shorts", "embed", "live", "v"}
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_SLUG_STRIP = "-_"


def extract_youtube_id(source: str) -> str | None:
    """Lấy id video từ mọi dạng link YouTube thường gặp; không phải link thì trả `None`.

    Đòi hỏi có `http(s)://` để một file tên `abcdefghijk.mp3` không bị nhận nhầm là link.
    """
    parsed = urlparse(str(source).strip())
    if parsed.scheme not in ("http", "https"):
        return None
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _YOUTUBE_HOSTS:
        return None

    candidate = ""
    parts = [p for p in parsed.path.split("/") if p]
    if host == "youtu.be":
        candidate = parts[0] if parts else ""
    else:
        candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        if not candidate and len(parts) >= 2 and parts[0] in _YT_PATH_PREFIXES:
            candidate = parts[1]
    candidate = candidate.strip()
    return candidate if _YT_ID_RE.match(candidate) else None


def make_video_id(source: str) -> str:
    """Tên thư mục làm việc suy ra từ nguồn, chỉ gồm `[A-Za-z0-9_-]`.

    * Link YouTube → chính id video, để chạy lại cùng link là dùng lại thư mục cũ.
    * File trên máy → slug từ tên file + 8 ký tự băm của **đường dẫn đầy đủ**, nên hai
      file trùng tên ở hai thư mục khác nhau không đè kết quả của nhau.

    Chỉ giữ `[A-Za-z0-9_-]` vì tên thư mục còn phải sống được trên cả macOS lẫn
    Windows (dấu tiếng Việt, chữ Hán, dấu hai chấm, khoảng trắng đều là mìn), và còn
    bị gõ lại làm tham số cho `srtgen resume <video_id>`.
    """
    text = nfc(str(source).strip().strip('"').strip("'"))

    youtube_id = extract_youtube_id(text)
    if youtube_id:
        # Id bắt đầu bằng "-" hợp lệ với YouTube nhưng lại giống một tuỳ chọn dòng lệnh.
        return youtube_id if not youtube_id.startswith("-") else f"yt_{youtube_id}"

    parsed = urlparse(text)
    if parsed.scheme in ("http", "https"):
        stem = Path(parsed.path).name or (parsed.hostname or "")
        digest = _hash8(text)
    else:
        path = Path(text).expanduser()
        try:
            path = path.resolve(strict=False)
        except OSError:  # pragma: no cover - đường dẫn quá dài trên Windows
            pass
        stem = path.stem or path.name
        # normcase: trên Windows cùng một file gõ khác kiểu chữ vẫn ra một id.
        digest = _hash8(os.path.normcase(str(path)))

    return f"{_slugify(stem) or 'video'}_{digest}"


def _hash8(text: str) -> str:
    return hashlib.sha1(nfc(text).encode("utf-8")).hexdigest()[:8]


def _slugify(text: str, *, max_len: int = 40) -> str:
    """Bỏ dấu tiếng Việt, thay mọi ký tự lạ bằng `_`, cắt ngắn.

    Chuỗi rỗng là kết quả hợp lệ (tên file toàn chữ Hán chẳng hạn) — chỗ gọi luôn
    ghép thêm mã băm nên id vẫn duy nhất.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    stripped = stripped.replace("đ", "d").replace("Đ", "D")
    kept = [
        ch if (ch.isascii() and (ch.isalnum() or ch in _SLUG_STRIP)) else "_"
        for ch in stripped
    ]
    slug = re.sub(r"_{2,}", "_", "".join(kept)).strip(_SLUG_STRIP)
    return slug[:max_len].strip(_SLUG_STRIP)


# --------------------------------------------------------------------------- #
# tạo Context
# --------------------------------------------------------------------------- #

def new_context(
    source: str,
    cfg: dict[str, Any],
    out_dir: str | os.PathLike[str] | None = None,
) -> Context:
    """Dựng `Context` cho một lần chạy và tạo sẵn hai thư mục cần ghi.

    Tạo thư mục ngay từ đầu là cố ý: người dùng chọn nhầm ổ đĩa chỉ đọc thì phải biết
    ngay lúc bấm Bắt đầu, chứ không phải sau 30 phút chờ chặng ASR.

    Ưu tiên đường dẫn ra: tham số `out_dir` → `cfg["paths"]["out_dir"]` → thư mục dữ
    liệu của ứng dụng.
    """
    video_id = make_video_id(source)
    paths = cfg.get("paths") or {}

    work_root = _resolve_dir(paths.get("work_dir"), user_data_dir() / "work")
    work_dir = _make_dir(work_root / video_id, "thư mục làm việc")
    out_path = _make_dir(
        _resolve_dir(out_dir or paths.get("out_dir"), user_data_dir() / "output"),
        "thư mục lưu kết quả",
    )

    meta: dict[str, Any] = {
        "video_id": video_id,
        "source": str(source),
        "profile": cfg.get("profile", ""),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return Context(
        video_id=video_id,
        work_dir=work_dir,
        out_dir=out_path,
        cfg=cfg,
        meta=meta,
    )


def _resolve_dir(value: Any, fallback: Path) -> Path:
    """Chuỗi rỗng trong cấu hình nghĩa là "tự chọn giúp tôi", không phải thư mục hiện tại."""
    if value is None or str(value).strip() == "":
        return fallback
    return Path(str(value)).expanduser()


def _make_dir(path: Path, label: str) -> Path:
    try:
        return ensure_dir(path)
    except OSError as err:
        raise RuntimeError(
            f"Không tạo được {label}: {path}\n"
            f"Lý do: {err}\n"
            "Hãy chọn một thư mục khác trong phần Cài đặt, hoặc kiểm tra quyền ghi của ổ đĩa."
        ) from err
