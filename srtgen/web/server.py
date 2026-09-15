"""Máy chủ web nội bộ của srtgen — chạy trên chính máy người dùng, không ra Internet.

Vì sao có một máy chủ web trong một tool chạy tại chỗ:

* Người dùng cuối không phải dân IT. Một cửa sổ Terminal in log tiếng Anh là thứ
  chắc chắn làm họ bỏ cuộc; một trang web có ô dán link, một nút Bắt đầu và một
  thanh tiến trình thì không.
* Công việc chạy **phía máy chủ** (xem `srtgen/web/jobs.py`). Trang web chỉ là cửa
  sổ nhìn vào đó, nên đóng tab, mở tab mới, hay mở thêm tab thứ hai đều thấy cùng
  một tiến trình.

Ba ràng buộc an toàn, cố ý ghi ngay đầu file vì chúng dễ bị nới lỏng lúc sửa vội:

1. **Chỉ nghe ở `127.0.0.1`.** Không bao giờ `0.0.0.0`. Máy này nằm trong nhà/văn
   phòng người dùng, mở ra mạng LAN là mời người khác chạy lệnh trên máy họ.
2. **Kiểm `Host` và `Origin` ở mọi yêu cầu.** Một trang web độc hại người dùng
   đang mở ở tab khác vẫn có thể gửi yêu cầu tới `127.0.0.1`; chặn theo `Origin`
   là cách rẻ nhất để việc đó vô hiệu. Khi máy chủ biết cổng của chính nó (luôn
   biết khi chạy bằng `serve()`), `Origin` phải khớp **cả host lẫn cổng**: một
   ứng dụng khác chạy trên `127.0.0.1` ở cổng khác cũng là "trang web lạ".
3. **Mọi đường dẫn file phải nằm trong thư mục kết quả đã biết trước.** Không
   ghép chuỗi từ tham số người dùng rồi mở thẳng — xem `_safe_path`.
4. **`POST /api/actions/{key}` chỉ nhận một danh sách trắng cứng.** `key` từ
   trình duyệt chỉ dùng để tra bảng `_ACTION_RUNNERS`; nó không bao giờ trở
   thành tên lệnh, tên file hay tham số, và không chỗ nào ở đây dùng
   `shell=True`. Đây là bề mặt dễ thành lỗ hổng thực thi lệnh nhất của cả dự án.

`fastapi`/`uvicorn` được import **lười** bên trong hàm: `srtgen doctor` và lệnh
`srtgen check` phải chạy được trên máy chưa cài đủ thư viện, mà cả hai đều import
được module này qua `srtgen.web`.

Đây là module duy nhất trong dự án được phép `print()`, và chỉ trong `serve()` —
đó là điểm vào của người dùng, không phải mã thư viện.
"""

from __future__ import annotations

import asyncio
import html
import json
import math
import os
import re
import socket
import subprocess
import sys
import shutil
import threading
import time
import unicodedata
import uuid
import webbrowser
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Iterator, Mapping, Sequence
from urllib.parse import urlparse

from srtgen import io_utils
from srtgen.core.context import load_config, make_video_id, user_cache_dir, user_data_dir
from srtgen.core.names_store import (
    CORRUPT_NAMES_TAG,
    NamesSaveRefused,
    load_names_file,
    save_names_file,
)
from srtgen.web.jobs import (
    DEFAULT_RERUN_STEP,
    MODE_CHECK,
    MODE_FIX,
    MODE_LOCAL,
    STATUS_LABELS,
    UPLOADS_DIR_NAME,
    Job,
    JobConflict,
    JobError,
    JobManager,
    JobNotFound,
    normalize_source,
    step_table,
)

__all__ = [
    "HOST",
    "DEFAULT_PORT",
    "MAX_UPLOAD_BYTES",
    "MAX_MEDIA_BYTES",
    "MEDIA_EXTENSIONS",
    "MediaUpload",
    "max_media_bytes",
    "open_local_content",
    "receive_media_upload",
    "uploads_dir",
    "STATIC_DIR",
    "MISSING_WEB_HINT",
    "ACTION_KEYS",
    "LONG_ACTIONS",
    "MODE_ACTION",
    "NO_BROWSER_ENV",
    "ActionError",
    "DetailedError",
    "EditorFiles",
    "SettingsError",
    "action_key",
    "check_srt_text",
    "create_app",
    "decode_srt_bytes",
    "draft_file",
    "editor_document",
    "editor_files",
    "find_free_port",
    "fix_srt_payload",
    "get_manager",
    "job_audio_file",
    "list_name_tables",
    "load_settings",
    "model_choices",
    "normalize_settings_patch",
    "open_local_pair",
    "original_file",
    "parse_byte_range",
    "public_settings",
    "read_draft",
    "read_names",
    "retokenize_text",
    "revert_editor_document",
    "run_action",
    "save_editor_document",
    "save_settings",
    "serve",
    "start_action_job",
    "test_api_key",
    "web_dependencies_missing",
    "work_dir_for",
    "work_root",
    "write_draft",
    "write_names",
    "CORRUPT_NAMES_TAG",
    "GUIDE_URL",
    "ORIGIN_CONTENT",
    "ORIGIN_JOB",
    "ORIGIN_PATH",
    "editor_downloads",
    "editor_origin",
    "guide_missing_html",
    "markdown_to_html",
    "render_guide_html",
]


# --------------------------------------------------------------------------- #
# hằng số
# --------------------------------------------------------------------------- #

#: Không bao giờ đổi thành "0.0.0.0". Xem điểm 1 trong docstring của module.
HOST = "127.0.0.1"

#: Cổng bắt đầu dò. 8756 nằm ngoài dải cổng phổ biến nên hiếm khi đụng ai.
DEFAULT_PORT = 8756

#: Số cổng thử liên tiếp trước khi chịu thua.
PORT_SEARCH_COUNT = 40

#: 20MB. Một file .srt bốn dòng của phim 40 phút nặng chừng 200KB, nên hạn mức này
#: đã rộng gấp trăm lần nhu cầu — nó chỉ để một cú kéo nhầm file video vào ô tải
#: lên không nuốt hết RAM của máy chủ.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

#: Trần mặc định cho file âm thanh/video kéo thả vào ô chính (`web.max_media_bytes`
#: trong cấu hình đè được). 4 GiB đủ cho một tập phim 1080p dài; khác hẳn trần
#: 20MB của file `.srt` vì hai thứ này khác nhau cả nghìn lần về kích thước.
MAX_MEDIA_BYTES = 4 * 1024 * 1024 * 1024

#: Đuôi file âm thanh/video nhận qua ô tải lên (so không phân biệt hoa thường).
#: Danh sách trắng chứ không đen: ffmpeg đọc được gần như mọi thứ, kể cả những
#: thứ không nên đưa vào một máy chủ nhận file từ trình duyệt.
MEDIA_EXTENSIONS: tuple[str, ...] = (
    ".mp3", ".mp4", ".m4a", ".wav", ".mkv", ".mov",
    ".webm", ".flac", ".aac", ".ogg", ".avi",
)

#: Tổng dung lượng các trường chữ đi kèm file tải lên (`options`, `mode`…). Chúng
#: là vài trăm byte JSON; trần này chỉ để chúng không thành đường nạp RAM thứ hai.
MAX_FORM_FIELDS_BYTES = 256 * 1024

#: Phần thừa của khung multipart (ranh giới, tiêu đề từng phần) cộng thêm vào trần
#: khi soát `Content-Length` trước lúc đọc — để một file đúng bằng trần vẫn lọt.
_MULTIPART_SLACK = 64 * 1024

#: Bản tải lên mồ côi (ứng dụng sập giữa chừng) già hơn chừng này thì bị dọn lúc
#: khởi động. Một ngày: dài hơn mọi hàng đợi thực tế, ngắn hơn lúc người dùng kịp
#: thấy ổ đĩa đầy mà không biết vì sao.
UPLOAD_SWEEP_AGE = 24 * 3600.0

#: Thân yêu cầu `POST /api/open-local` dạng nội dung: hai file `.srt`, mỗi file
#: tối đa `MAX_UPLOAD_BYTES`, cộng phần JSON thoát ký tự.
OPEN_LOCAL_MAX_BODY = 3 * MAX_UPLOAD_BYTES

STATIC_DIR = Path(__file__).resolve().parent / "static"

#: Tên file cài đặt trong thư mục dữ liệu người dùng.
SETTINGS_FILE = "settings.json"

#: Chỉ chấp nhận các host này; chặn cả tấn công đổi DNS trỏ tên miền lạ về 127.0.0.1.
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", ""})

#: Video id do `make_video_id` sinh ra chỉ gồm chừng này ký tự. Kiểm ở đây là chốt
#: chặn duy nhất giữa tham số URL và một đường dẫn thư mục thật.
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

MISSING_WEB_HINT = (
    "Chưa cài được phần giao diện web.\n"
    "Hãy mở Terminal và chạy đúng dòng sau:\n\n"
    "    pip install fastapi uvicorn\n\n"
    "Nếu bạn cài srtgen bằng file CaiDat.command thì chạy lại file đó là đủ."
)

#: Loại nội dung và cách mở của từng loại file tải về.
_DOWNLOAD_KINDS: dict[str, tuple[str, str]] = {
    "srt": ("application/x-subrip; charset=utf-8", "attachment"),
    "vi_srt": ("application/x-subrip; charset=utf-8", "attachment"),
    "bundle": ("application/json; charset=utf-8", "attachment"),
    # Báo cáo là thứ để đọc ngay trong trình duyệt, không phải để tải về rồi đi tìm.
    "report": ("text/html; charset=utf-8", "inline"),
    "ass": ("text/x-ssa; charset=utf-8", "attachment"),
    "bilingual_ass": ("text/x-ssa; charset=utf-8", "attachment"),
}

#: Bản nháp tự lưu của trình sửa, đặt cạnh các file chặng trong `work/<video_id>/`.
DRAFT_FILE = "edit_draft.json"

#: Bản do máy tạo ra, chụp lại **trước lần lưu tay đầu tiên**, để nút “Hoàn nguyên
#: về bản máy tạo” còn có cái mà quay về.
ORIGINAL_FILE = "edit_original.json"

#: Bản nháp là JSON của một bảng vài nghìn dòng; 8MB đã rộng gấp nhiều lần một
#: phim 40 phút, và vẫn đủ nhỏ để không ai làm phình thư mục dữ liệu bằng nó.
MAX_DRAFT_BYTES = 8 * 1024 * 1024

#: Trần số dòng phụ đề nhận trong một lần lưu. Một phim 40 phút có chừng 1400
#: dòng; con số này chỉ để một yêu cầu hỏng không giữ luồng máy chủ hàng phút.
MAX_EDIT_CUES = 20000

#: Trần độ dài một dòng gửi sang `/api/retokenize`.
MAX_EDIT_LINE = 2000

#: Kích thước mỗi mảnh khi phát file âm thanh. 512KB là chỗ cân bằng: đủ lớn để
#: không gọi hệ điều hành hàng nghìn lần cho một lần tua, đủ nhỏ để bộ nhớ máy
#: chủ không nhảy vọt khi trình duyệt xin nguyên file.
AUDIO_CHUNK_BYTES = 512 * 1024

#: Loại nội dung theo đuôi file âm thanh. Chặng S1 luôn ghi ra `.wav`; các đuôi
#: còn lại là cho trường hợp người dùng chỉ thẳng vào file gốc của họ.
AUDIO_MEDIA_TYPES: dict[str, str] = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".webm": "audio/webm",
}

#: Các chặng có thể để lại file âm thanh, theo thứ tự ưu tiên: bản đã xử lý ở S1
#: trước (đúng thứ mà máy đã nghe để gỡ băng, nên nghe lại mới khớp với phụ đề),
#: bản tải thô ở S0 sau.
_AUDIO_STAGES: tuple[tuple[int, str], ...] = ((1, "audio"), (0, "info"))

#: Thứ tự đi tìm tài liệu đã lưu của các chặng, muộn nhất trước — giống hệt luật
#: của chặng xuất file, và vì cùng một lý do: chặng càng muộn dữ liệu càng đủ.
_STAGE_DOCS: tuple[tuple[int, str], ...] = (
    (8, "translate"),
    (7, "ai"),
    (6, "norm"),
    (5, "tokens"),
)


# --------------------------------------------------------------------------- #
# hàng đợi dùng chung
# --------------------------------------------------------------------------- #

_manager: JobManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> JobManager:
    """Hàng đợi dùng chung cho cả tiến trình.

    Một tiến trình một hàng đợi: nếu mỗi lần tạo `app` lại dựng một hàng đợi mới
    thì hai tab trình duyệt sẽ nhìn thấy hai danh sách việc khác nhau.
    """
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = JobManager(config_factory=_config_for_job)
        return _manager


def _config_for_job(job: Job) -> dict[str, Any]:
    """Cấu hình cho một công việc: `default.yaml` + Cài đặt + lựa chọn của lần chạy.

    Thứ tự ưu tiên (sau đè lên trước): file mặc định → profile → Cài đặt đã lưu →
    lựa chọn gõ trong ô "Nâng cao" của lần chạy này.
    """
    settings = load_settings()
    options = dict(job.options or {})
    profile = str(options.get("profile") or settings.get("profile") or "").strip() or None

    overrides: dict[str, Any] = {}
    asr: dict[str, Any] = {}
    ai: dict[str, Any] = {}
    audio: dict[str, Any] = {}
    translate: dict[str, Any] = {}
    paths: dict[str, Any] = {}
    emit: dict[str, Any] = {}

    model = str(options.get("model") or settings.get("model") or "").strip()
    if model:
        asr["model"] = model

    api_key = str(settings.get("api_key") or "").strip()
    if api_key:
        ai["api_key"] = api_key
    ai_model = str(settings.get("ai_model") or "").strip()
    if ai_model:
        ai["model_other"] = ai_model
    ai_enabled = options.get("ai")
    if options.get("no_ai"):
        ai_enabled = False
    if ai_enabled is None:
        ai_enabled = settings.get("ai_enabled")
    if ai_enabled is not None:
        ai["enabled"] = bool(ai_enabled)
        if ai["enabled"] and api_key:
            ai["provider"] = str(settings.get("ai_provider") or "gemini")

    # Hai ô tick ở màn hình chính. Không có đoạn nối này thì chúng là nút giả:
    # người dùng bỏ tick "Dịch tiếng Việt" xong vẫn thấy file _vi.srt hiện ra, và
    # bật "Tách giọng khỏi nhạc nền" xong vẫn không có gì chậm đi — cả hai đều là
    # kiểu hỏng im lặng, tệ hơn một thông báo lỗi.
    demucs = _tri_state(options.get("demucs"), settings.get("demucs"))
    if demucs is not None:
        audio["demucs"] = demucs

    translate_on = options.get("translate")
    if options.get("no_translate"):
        translate_on = False
    translate_on = _tri_state(translate_on, settings.get("translate_enabled"))
    if translate_on is not None:
        translate["enabled"] = translate_on

    provider = str(
        options.get("translate_provider") or settings.get("translate_provider") or ""
    ).strip()
    if provider:
        translate["provider"] = provider

    out_dir = str(options.get("out_dir") or settings.get("out_dir") or "").strip()
    if out_dir:
        paths["out_dir"] = out_dir

    ass = _tri_state(options.get("ass_export"), settings.get("ass_export"))
    if ass is not None:
        emit["ass_export"] = ass
    bilingual = _tri_state(options.get("bilingual_ass"), settings.get("bilingual_ass"))
    if bilingual is not None:
        emit["bilingual_ass"] = bilingual
    bom = _tri_state(options.get("bom"), settings.get("bom"))
    if bom is not None:
        emit["bom"] = bom
    newline = str(options.get("newline") or settings.get("newline") or "").strip().lower()
    if newline in ("lf", "crlf"):
        emit["newline"] = newline

    from_stage = options.get("from_stage")
    if from_stage is not None:
        number = _stage_number(from_stage)
        if number is not None:
            overrides["force_from"] = number

    for key, block in (
        ("asr", asr),
        ("ai", ai),
        ("audio", audio),
        ("translate", translate),
        ("paths", paths),
        ("emit", emit),
    ):
        if block:
            overrides[key] = block
    return load_config(profile, overrides)


def _tri_state(first: Any, second: Any = None) -> bool | None:
    """`True`/`False` khi có người nói rõ, `None` khi không ai nói gì.

    Ba trạng thái chứ không phải hai là điểm mấu chốt: `False` nghĩa là *người
    dùng đã tắt*, còn `None` nghĩa là *không ai nhắc tới, cứ theo `default.yaml`*.
    Gộp hai thứ đó làm một sẽ biến mọi mặc định `true` trong cấu hình thành `false`
    ngay lần chạy đầu tiên.
    """
    for value in (first, second):
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip().lower()
            if not text:
                continue
            return text not in ("0", "false", "no", "off", "không", "khong")
        return bool(value)
    return None


def _stage_number(value: Any) -> int | None:
    """`"s5"` / `"5"` → `5`. Mười chặng nên chỉ nhận đúng một chữ số 0–9."""
    match = re.match(r"^[sS]?(\d)$", str(value).strip())
    if match is None:
        return None
    number = int(match.group(1))
    return number if 0 <= number <= 9 else None


# --------------------------------------------------------------------------- #
# Cài đặt (khoá API, model, profile, thư mục ra)
# --------------------------------------------------------------------------- #

def settings_path() -> Path:
    return user_data_dir() / SETTINGS_FILE


def load_settings() -> dict[str, Any]:
    """Đọc file cài đặt; thiếu hoặc hỏng thì coi như chưa cài gì.

    Không ném lỗi: người dùng sửa tay file này rồi gõ nhầm dấu phẩy không được
    phép làm cả ứng dụng không mở lên được.
    """
    try:
        data = io_utils.read_json(settings_path())
    except (OSError, ValueError):
        return {}
    return dict(data) if isinstance(data, dict) else {}


class DetailedError(JobError):
    """Câu tiếng Việt cho người dùng + chi tiết kỹ thuật để **riêng một trường**.

    Tách hai thứ ra là có lý do cụ thể (audit mục H): khi hệ điều hành từ chối một
    thư mục, nó trả về nguyên văn tiếng Anh (`[WinError 3] The system cannot find
    the path specified`). Nhét câu đó vào giữa lời nhắn tiếng Việt thì người dùng
    không phải dân IT đọc xong chỉ thấy sợ, còn người rành máy lại vẫn cần đúng
    câu ấy — nên nó đi ra bằng `detail`, chỗ giao diện giấu sau nút "Xem chi tiết".
    """

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class SettingsError(DetailedError):
    """Không lưu được cài đặt (thư mục không ghi được, ổ đầy…)."""


class ActionError(DetailedError):
    """Một việc trong danh sách trắng chạy không xong."""


#: Khoá cài đặt dạng chuỗi mà giao diện được phép gửi lên.
_SETTING_TEXT_KEYS: tuple[str, ...] = (
    "profile",
    "model",
    "ai_model",
    "ai_provider",
    "translate_provider",
    "out_dir",
    "newline",
)

#: Khoá cài đặt dạng bật/tắt.
_SETTING_FLAG_KEYS: tuple[str, ...] = (
    "ai_enabled",
    "translate_enabled",
    "ass_export",
    "bilingual_ass",
    "bom",
    "demucs",
    "open_browser",
)

#: Giao diện gửi lên **cả** bảng phẳng lẫn bảng lồng theo hình dạng `default.yaml`
#: (`{"asr": {"model": …}}`). Bảng này nối chỗ lồng về đúng khoá phẳng, để một
#: người sửa `default.yaml` không phải sửa cả hai đầu.
_SETTING_NESTED: dict[str, dict[str, str]] = {
    "ai": {"api_key": "api_key", "enabled": "ai_enabled", "model_other": "ai_model",
           "model": "ai_model", "provider": "ai_provider"},
    "asr": {"model": "model"},
    "audio": {"demucs": "demucs"},
    "translate": {"provider": "translate_provider", "enabled": "translate_enabled"},
    "emit": {"ass_export": "ass_export", "bilingual_ass": "bilingual_ass",
             "bom": "bom", "newline": "newline"},
    "paths": {"out_dir": "out_dir"},
}

#: Khoá giao diện gửi kèm mà máy chủ hiểu nhưng không cất riêng (nó là bản sao của
#: một khoá khác). Nhận rồi bỏ qua, chứ không báo là "không hiểu".
_SETTING_ALIASES: dict[str, str] = {"default_profile": "profile"}


def normalize_settings_patch(patch: Any) -> tuple[dict[str, Any], list[str]]:
    """Đổi thứ giao diện gửi lên thành bảng cài đặt phẳng; trả kèm khoá không hiểu.

    Vì sao phải trả danh sách khoá không hiểu thay vì lặng lẽ bỏ: đúng cái lỗi mà
    audit mục C tìm ra. Trước đây máy chủ chỉ nhận 9 khoá, nên "Dịch tiếng Việt
    bằng…" và "Tách giọng khỏi nhạc nền" bị vứt trong im lặng **trong khi giao
    diện vẫn báo “Đã lưu cài đặt”**. Người dùng tải lại trang thấy về mặc định và
    không hiểu mình đã làm sai ở đâu. Thà nói thẳng "tool chưa hiểu khoá này".
    """
    if not isinstance(patch, dict):
        return {}, []

    flat: dict[str, Any] = {}
    unknown: list[str] = []

    def take(key: str, value: Any) -> None:
        if key in _SETTING_TEXT_KEYS:
            flat[key] = str(value or "").strip()
        elif key in _SETTING_FLAG_KEYS:
            flat[key] = bool(value)
        elif key == "api_key":
            # Khoá hay được dán kèm dấu nháy hoặc xuống dòng từ trang AI Studio.
            text = "" if value is None else str(value)
            text = "".join(text.split()).strip("\"'`\u201c\u201d\u2018\u2019")
            flat[key] = text

    for key, value in patch.items():
        name = str(key)
        target = _SETTING_ALIASES.get(name, name)
        if target in _SETTING_TEXT_KEYS or target in _SETTING_FLAG_KEYS or target == "api_key":
            take(target, value)
            continue
        nested = _SETTING_NESTED.get(name)
        if nested is not None and isinstance(value, dict):
            for inner, outer in nested.items():
                if inner in value:
                    take(outer, value.get(inner))
            continue
        if name.startswith("_"):  # cờ nội bộ của giao diện (__loaded…)
            continue
        unknown.append(name)

    return flat, sorted(set(unknown))


def save_settings(patch: dict[str, Any]) -> dict[str, Any]:
    """Gộp `patch` vào cài đặt cũ rồi ghi lại; trả về bản đã lưu.

    Nhận **mọi** khoá giao diện gửi lên (kể cả bảng lồng theo hình dạng
    `default.yaml`) — xem `normalize_settings_patch`. Khoá lạ không làm hỏng lần
    lưu, nhưng nó được trả về cho giao diện nói cho người dùng biết.

    Khoá API được xử lý riêng: giao diện nhận về bản che (`AIza••••••1234`), nên
    khi người dùng bấm Lưu mà không sửa gì thì chuỗi che đó quay lại đây. Nhận
    nhầm nó làm khoá mới sẽ phá hỏng khoá thật, nên chuỗi trùng bản che bị bỏ qua.
    Xoá hẳn khoá = gửi chuỗi rỗng.
    """
    current = load_settings()
    merged = dict(current)
    clean, _unknown = normalize_settings_patch(patch)

    for key, value in clean.items():
        if key == "api_key":
            if str(value) != mask_api_key(str(current.get("api_key") or "")):
                merged["api_key"] = str(value)
            continue
        merged[key] = value

    out_dir = str(merged.get("out_dir") or "").strip()
    if out_dir:
        try:
            io_utils.ensure_dir(Path(out_dir).expanduser())
        except OSError as err:
            raise SettingsError(
                f"Không dùng được thư mục “{out_dir}”. "
                "Hãy chọn một thư mục khác mà bạn có quyền ghi, hoặc để trống ô này "
                "để tool tự dùng thư mục kết quả mặc định.",
                detail=f"{type(err).__name__}: {err}",
            ) from err

    path = settings_path()
    try:
        io_utils.write_json(path, merged)
    except OSError as err:
        raise SettingsError(
            "Không ghi được file cài đặt. Có thể ổ đĩa đã đầy, hoặc thư mục dữ liệu "
            "của tool đang bị khoá quyền ghi.",
            detail=f"{type(err).__name__}: {err}",
        ) from err
    _restrict_permissions(path)
    return merged


def _restrict_permissions(path: Path) -> None:
    """Chỉ chủ máy đọc được file cài đặt, vì trong đó có khoá API.

    Trên Windows `chmod` gần như không có tác dụng; bỏ qua chứ không báo lỗi.
    """
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def mask_api_key(key: str) -> str:
    """`AIzaSyD…9f1234` → `AIza••••••1234`; khoá quá ngắn thì che sạch.

    Khoá API **không bao giờ** rời máy chủ ở dạng nguyên văn, kể cả cho chính
    trang web của mình: một tiện ích mở rộng của trình duyệt đọc được DOM là đọc
    được nó.
    """
    key = str(key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}••••••{key[-4:]}"


def public_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bản cài đặt an toàn để gửi cho trình duyệt.

    "An toàn" ở đây có đúng một nghĩa cứng: **khoá API không bao giờ đi ra nguyên
    văn**, chỉ có bản che và cờ "đã có khoá hay chưa".
    """
    data = load_settings() if settings is None else dict(settings)
    key = str(data.get("api_key") or "")
    try:
        cfg = load_config()
    except Exception:  # cấu hình hỏng vẫn phải mở được màn hình Cài đặt
        cfg = {}
    ai_cfg = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    asr_cfg = cfg.get("asr") if isinstance(cfg.get("asr"), dict) else {}
    audio_cfg = cfg.get("audio") if isinstance(cfg.get("audio"), dict) else {}
    emit_cfg = cfg.get("emit") if isinstance(cfg.get("emit"), dict) else {}
    tr_cfg = cfg.get("translate") if isinstance(cfg.get("translate"), dict) else {}
    profiles = cfg.get("profiles") if isinstance(cfg.get("profiles"), dict) else {}
    model = str(data.get("model") or asr_cfg.get("model") or "large-v3-turbo")
    return {
        "api_key_masked": mask_api_key(key),
        "api_key_set": bool(key),
        "api_key_env": str(ai_cfg.get("api_key_env") or "GEMINI_API_KEY"),
        "api_key_from_env": bool(os.environ.get(str(ai_cfg.get("api_key_env") or "GEMINI_API_KEY"))),
        "profile": str(data.get("profile") or cfg.get("default_profile") or "drama"),
        "profiles": sorted(profiles),
        "model": model,
        "models": model_choices(cfg),
        "ai_enabled": bool(data.get("ai_enabled", ai_cfg.get("enabled", False))),
        "ai_model": str(data.get("ai_model") or ai_cfg.get("model_other") or ""),
        "ai_provider": str(data.get("ai_provider") or ai_cfg.get("provider") or "null"),
        "translate_enabled": bool(data.get("translate_enabled", tr_cfg.get("enabled", True))),
        "translate_provider": str(
            data.get("translate_provider") or tr_cfg.get("provider") or "auto"
        ),
        "demucs": bool(data.get("demucs", audio_cfg.get("demucs", False))),
        "out_dir": str(data.get("out_dir") or ""),
        "out_dir_effective": _effective_out_dir(cfg, data),
        "work_dir": str(work_root(cfg)),
        "ass_export": bool(data.get("ass_export", emit_cfg.get("ass_export", False))),
        "bilingual_ass": bool(data.get("bilingual_ass", emit_cfg.get("bilingual_ass", True))),
        "bom": bool(data.get("bom", emit_cfg.get("bom", True))),
        "open_browser": bool(data.get("open_browser", True)),
        "settings_file": str(settings_path()),
    }


def model_choices(cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Bảng model gỡ băng cho tab Cài đặt, kèm cờ **model nào đã tải rồi**.

    Bảng lấy thẳng từ `asr.models` trong cấu hình chứ không viết cứng ở đây: thêm
    một model mới thì sửa `default.yaml` là xong, không phải sửa cả máy chủ lẫn
    giao diện.

    `downloaded` là thứ build-spec-v2 mục 8 hứa với người dùng ("đã tải rồi").
    Thiếu nó, giao diện phải nói "Chương trình chưa cho biết model nào đã tải" —
    và người dùng không đoán được bấm Bắt đầu là chạy ngay hay phải chờ tải 1.6GB.

    Hỏng ở đây (chưa cài `faster-whisper`, cache không đọc được) **không** được
    làm hỏng cả màn hình Cài đặt: khi đó `downloaded` là `None` = "không biết", và
    giao diện có luật riêng cho ba trạng thái đó.
    """
    cfg = cfg if cfg is not None else _safe_cfg()
    asr_cfg = cfg.get("asr") if isinstance(cfg.get("asr"), dict) else {}
    table = asr_cfg.get("models") if isinstance(asr_cfg.get("models"), list) else []

    status_of: Callable[[str], dict[str, Any]] | None
    try:
        from srtgen.stages.s2_asr import model_status as status_of  # type: ignore[no-redef]
    except Exception:  # noqa: BLE001 - xem docstring: không được làm hỏng màn hình
        status_of = None

    out: list[dict[str, Any]] = []
    for raw in table:
        if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
            continue
        model_id = str(raw["id"]).strip()
        eta = str(raw.get("eta_40min") or "")
        size = str(raw.get("download_size") or "")
        downloaded: bool | None = None
        problem = ""
        if status_of is not None:
            try:
                info = status_of(model_id, cfg)
                downloaded = bool(info.get("downloaded"))
                problem = str(info.get("problem") or "")
            except Exception:  # noqa: BLE001
                downloaded = None
        out.append(
            {
                "id": model_id,
                "label": str(raw.get("label") or model_id),
                # `eta`/`size` là tên trong hợp đồng API; hai tên dài giữ nguyên
                # chữ của `default.yaml` để giao diện đọc bằng tên nào cũng được.
                "eta": eta,
                "eta_40min": eta,
                "size": size,
                "download_size": size,
                "size_mb": int(_num(raw.get("size_mb"))),
                "recommended": bool(raw.get("recommended")),
                "downloaded": downloaded,
                "problem": problem,
            }
        )
    return out


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _effective_out_dir(cfg: dict[str, Any], settings: dict[str, Any]) -> str:
    """Thư mục kết quả thật sự đang dùng, để giao diện hiện ra thay vì để trống."""
    chosen = str(settings.get("out_dir") or "").strip()
    if chosen:
        return str(Path(chosen).expanduser())
    paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}
    configured = str(paths.get("out_dir") or "").strip()
    if configured:
        return str(Path(configured).expanduser())
    return str(user_data_dir() / "output")


# --------------------------------------------------------------------------- #
# đọc file .srt người dùng gửi lên
# --------------------------------------------------------------------------- #

def decode_srt_bytes(data: bytes) -> str:
    """Đoán bảng mã của một file .srt rồi trả về text đã chuẩn hoá NFC, xuống dòng LF.

    Thứ tự thử là có chủ ý: UTF-8 trước vì nó tự kiểm lỗi (byte sai là biết ngay),
    còn GB18030 nhận gần như mọi chuỗi byte nên đặt trước sẽ "đọc được" cả file
    UTF-8 thành chữ rác. File do bên dịch ở Trung Quốc gửi sang thường là GB18030,
    file từ Windows tiếng Việt thường là UTF-8 có BOM — cả hai đều phải mở được.
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        order = ("utf-16", "utf-8-sig", "gb18030")
    else:
        order = ("utf-8-sig", "gb18030", "big5", "cp1252")
    for encoding in order:
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        text = data.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return io_utils.nfc(text)


def _srt_stem(filename: str) -> str:
    """Tên gốc để đặt cho file kết quả, đã bỏ ký tự mà hệ thống file không nhận."""
    name = Path(str(filename or "").replace("\\", "/")).name
    stem = name[:-4] if name.lower().endswith(".srt") else name
    return io_utils.safe_stem(stem, fallback="phu-de")


# --------------------------------------------------------------------------- #
# hai việc chạy ngay: kiểm và chuẩn hoá
# --------------------------------------------------------------------------- #

def check_srt_text(text: str) -> dict[str, Any]:
    """Chạy toàn bộ 12 mục quy chuẩn trên một file .srt, không sửa gì."""
    from srtgen.core.rules import format_report_lines, summarize, validate_text

    findings = validate_text(text)
    return {
        "findings": [f.to_dict() for f in findings],
        "summary": summarize(findings),
        "report_lines": format_report_lines(findings),
        "blocks": _count_blocks(text),
    }


def _count_blocks(text: str) -> int:
    """Đếm số block bằng số dòng mốc thời gian — cách duy nhất không sợ dòng trống thừa."""
    return sum(1 for line in text.split("\n") if "-->" in line)


def fix_srt_payload(
    text: str,
    *,
    names: Mapping[str, str] | None = None,
    profile: str | None = None,
) -> dict[str, Any]:
    """Chuẩn hoá một file `.srt` có sẵn cho `/api/fix` — qua ĐÚNG hàm của lệnh `srtgen fix`.

    Hợp đồng A: dự án chỉ có **một** hàm sửa file, `pipeline.run_fix_bilingual`.
    Trước đây máy chủ giữ bản riêng (`fix_srt_text` + `split_bilingual_srt`): nó
    không cho jieba tách những câu còn dính liền, biến điệu cả pinyin người biên
    tập đã ghi, và tự đoán dòng tiếng Việt bằng "chữ cái riêng của tiếng Việt" —
    nên cùng một file, trang "Kiểm tra file" và lệnh `srtgen fix` cho ra hai kết
    quả khác nhau, và câu như "Anh là ai?" bị giữ lại làm pinyin. Hàm này không
    còn logic sửa nào: nó gọi pipeline rồi đổi `FixResult` sang hình dạng JSON mà
    giao diện đang đọc.

    `names=None` nghĩa là "dùng bảng tên khai báo trong cấu hình", đúng như CLI;
    có mã phim thì chỗ gọi truyền bảng của phim đó (`read_names(video_id)`).

    `findings_before`/`summary_before` là lỗi của file GỐC, để câu báo nói được
    "đã sửa N lỗi"; `findings` là lỗi còn lại của file KẾT QUẢ.
    """
    from srtgen.core.rules import summarize, validate_text
    from srtgen.pipeline import run_fix_bilingual

    cfg = load_config(profile)
    before = validate_text(text)
    result = run_fix_bilingual(text, cfg, names=names)

    emit_cfg = cfg.get("emit") if isinstance(cfg.get("emit"), dict) else {}
    stats = dict(result.stats)
    return {
        "text": result.text,
        "cues": int(stats.get("cues") or 0),
        "blocks": int(stats.get("blocks") or 0),
        # Số câu mà tool phải tự sinh pinyin (không có pinyin dùng được, hoặc phải
        # tách cụm lại). Câu giữ nguyên pinyin người biên tập không tính vào đây.
        "pinyin_filled": int(stats.get("new_pinyin") or 0) + int(stats.get("resegmented") or 0),
        "stats": stats,
        "findings": [f.to_dict() for f in result.findings],
        "findings_before": [f.to_dict() for f in before],
        "summary": summarize(result.findings),
        "summary_before": summarize(before),
        "newline": str(emit_cfg.get("newline") or "lf"),
        "bom": bool(emit_cfg.get("bom", True)),
        "has_vi": result.has_vi,
        # Chuỗi rỗng chứ không phải `null` khi không có dòng tiếng Việt: giữ đúng
        # hình dạng mà giao diện và bộ test đang dựa vào.
        "vi_text": result.vi_text or "",
        "vi_cues": int(stats.get("vi_cues") or 0),
        "set_aside": _set_aside_rows(result.set_aside, result.findings),
        "notes": list(result.notes),
    }


def _set_aside_rows(texts: Sequence[str], findings: Sequence[Any]) -> list[dict[str, Any]]:
    """`[{cue, text, reason}]` cho từng dòng tool không xếp được vào file.

    `FixResult.set_aside` chỉ giữ nguyên văn dòng; câu số mấy và vì sao thì nằm ở
    finding `BLOCK_SHAPE` trích đúng dòng đó (`srt.set_aside_finding`). Ghép lại ở
    đây để giao diện hiện được "câu 12: …" cạnh chữ của người dùng — một dòng biến
    khỏi file mà không nói nó thuộc câu nào thì người dùng không tự lần ra được.
    Mỗi finding chỉ ghép một lần, nên hai dòng giống hệt nhau vẫn ra hai mục.
    """
    pool = [f for f in findings if getattr(f, "code", "") == "BLOCK_SHAPE"]
    used: set[int] = set()
    rows: list[dict[str, Any]] = []
    for text in texts:
        match = next(
            (k for k, f in enumerate(pool) if k not in used and getattr(f, "line", None) == text),
            None,
        )
        if match is None:
            rows.append({"cue": None, "text": text, "reason": ""})
            continue
        used.add(match)
        rows.append({"cue": pool[match].cue_index, "text": text, "reason": pool[match].message})
    return rows


# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# bảng tên riêng
# --------------------------------------------------------------------------- #

def work_root(cfg: dict[str, Any] | None = None) -> Path:
    """Thư mục chứa toàn bộ thư mục làm việc của từng video.

    Một chỗ duy nhất tính ra đường dẫn này, vì cả `names.json`, bản nháp của
    trình sửa, bản gốc để hoàn nguyên và file âm thanh của chặng S1 đều nằm dưới
    đó — bốn chỗ tự tính lấy là bốn chỗ sẽ lệch nhau khi ai đó đổi `paths.work_dir`.
    """
    cfg = cfg if cfg is not None else _safe_cfg()
    paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}
    folder = str(paths.get("work_dir") or "").strip()
    return Path(folder).expanduser() if folder else user_data_dir() / "work"


def work_dir_for(video_id: str, cfg: dict[str, Any] | None = None) -> Path:
    """`work/<video_id>/` — nơi cất mọi thứ chỉ thuộc về một video.

    `video_id` phải đã qua `VIDEO_ID_RE` trước khi tới đây; đó là chốt chặn duy
    nhất giữa một tham số URL và một đường dẫn thư mục thật.
    """
    return work_root(cfg) / str(video_id)


def names_file(video_id: str, cfg: dict[str, Any] | None = None) -> Path:
    """Đường dẫn `names.json` của một phim, theo đúng quy tắc của S5/S7."""
    cfg = cfg if cfg is not None else load_config()
    paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}
    names_cfg = cfg.get("names") if isinstance(cfg.get("names"), dict) else {}
    filename = str(names_cfg.get("file") or "names.json").strip() or "names.json"
    folder = str(paths.get("names_dir") or "").strip()
    base = Path(folder).expanduser() if folder else work_dir_for(video_id, cfg)
    return base / filename


def draft_file(video_id: str) -> Path:
    """Bản nháp tự lưu của trình sửa cho một video."""
    return work_dir_for(video_id) / DRAFT_FILE


def original_file(video_id: str) -> Path:
    """Bản do máy tạo, chụp trước lần lưu tay đầu tiên."""
    return work_dir_for(video_id) / ORIGINAL_FILE


def read_names(video_id: str) -> dict[str, Any]:
    """Bảng tên riêng của một phim, ở hình dạng hợp đồng `{zh, pinyin, vi}`.

    Đọc được **cả hai** thế hệ file: bản có khối `entries` giàu thông tin mà S7
    ghi ra, và bản `names.json` phẳng kiểu cũ (`{chữ Hán: pinyin}`) mà người dùng
    đang có sẵn trên máy. File cũ không được phép mở ra thành bảng trống — với
    người dùng, "bảng trống" và "mất dữ liệu" là cùng một thứ.

    Mỗi entry mang **hai bộ tên** cho cùng một dữ liệu: `zh`/`pinyin`/`vi` là hợp
    đồng API, còn `han`/`split`/`pinyin_list` là hình dạng trong `names.json`.
    Trả cả hai rẻ hơn nhiều so với việc hai bên phải đổi tên cùng lúc.

    File **có mà hỏng** (lỗi cú pháp, sai dạng) cũng đọc ra bảng rỗng — nhưng
    kèm `corrupt=True` và câu tiếng Việt ở `corrupt_message` (và `message`). Thiếu
    hai trường này thì màn hình báo "bảng đang trống", người dùng gõ lại vài tên,
    bấm Lưu, và bảng cũ bị ghi đè mất hẳn.

    Đọc qua `srtgen.core.names_store` (hợp đồng H4) — chỗ DUY NHẤT đọc/ghi
    `names.json`, dùng chung với chặng S7 — chứ không tự đọc: "hỏng" hay "trống"
    phải được phán ở một nơi, nếu không màn hình và chặng chạy sẽ có hai ý kiến.
    Bảng hỏng thì không đưa cho bộ đọc dễ dãi của S7 nữa: nó sẽ đọc ra rỗng y như vậy.
    """
    path = names_file(video_id)
    loaded = load_names_file(path)
    problem = (loaded.message or "") if loaded.corrupt else ""
    if loaded.corrupt:
        flat, meta = {}, {}
    else:
        try:
            from srtgen.stages.s7_ai import load_names

            flat, meta = load_names(path)
        except ImportError:
            flat, meta = _load_names_fallback(loaded.data)
    raw = loaded.data

    # `meta["entries"]` của `load_names` là bản đã gộp cả tên tiếng Việt viết tay
    # trong bảng lồng (`"光头强": {"pinyin": …, "vi": …}`). Đọc thẳng `raw` thì tên
    # Việt đó không hiện ra, và lần bấm Lưu kế tiếp xoá mất nó.
    folded = (meta or {}).get("entries")
    stored = folded if isinstance(folded, list) else (
        raw.get("entries") if isinstance(raw, dict) else None
    )
    entries = _api_entries(stored if isinstance(stored, list) else [], flat)
    return {
        "video_id": video_id,
        "path": str(path),
        "exists": path.is_file(),
        "corrupt": bool(problem),
        "corrupt_message": problem,
        **({"message": problem} if problem else {}),
        "names": flat,
        "entries": entries,
        "count": len(entries),
        "meta": {k: v for k, v in (meta or {}).items() if k != "entries"},
    }


def _load_names_fallback(raw: Any) -> tuple[dict[str, str], dict[str, Any]]:
    """Đọc `names.json` khi chưa cài được S7 — chỉ đủ để màn hình không trống."""
    if not isinstance(raw, dict):
        return {}, {}
    body = raw.get("names")
    if not isinstance(body, dict):
        body = {k: v for k, v in raw.items() if isinstance(v, str)}
    flat = {str(k): str(v) for k, v in body.items() if str(k) and isinstance(v, str)}
    return flat, {k: v for k, v in raw.items() if k != "names"}


def _stored_vi(item: dict[str, Any]) -> str:
    """Tên tiếng Việt của một entry trong file, dưới **mọi** khoá S7/S8 chấp nhận.

    S8 dựng bảng thuật ngữ bằng `s7_ai.VI_KEYS` (`vi`, `vietnamese`, `viet`,
    `translation`). Nếu màn hình chỉ đọc `vi` thì một file sửa tay ghi
    `"vietnamese"` được S8 dùng nhưng hiện ô trống, và lần Lưu kế tiếp ghi `vi`
    rỗng — tên mất trong im lặng. Đọc cùng danh sách khoá, lưu lại thành `vi`.
    """
    try:
        from srtgen.stages.s7_ai import VI_KEYS
    except ImportError:
        VI_KEYS = ("vi", "vietnamese", "viet", "translation")
    for key in VI_KEYS:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return _name_vi(value)
    return ""


def _api_entries(stored: Sequence[Any], flat: dict[str, str]) -> list[dict[str, Any]]:
    """Gộp khối `entries` với bảng phẳng thành một danh sách dòng sửa được."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Cụm của một tên nhiều cụm. S7 ghi âm đọc của **từng cụm** vào bảng phẳng
    # (陈 → Chén, 路周 → Lùzhōu), nên không lọc thì một tên "陈路周" hiện thành ba
    # dòng trên màn hình và phim bị đếm là có ba tên. Lưu lại vẫn không mất gì:
    # `write_names` dựng lại đúng các khoá cụm ấy từ `split` + `pinyin`.
    clusters: set[str] = set()
    for item in stored:
        if not isinstance(item, dict):
            continue
        split = [str(s) for s in (item.get("split") or []) if str(s).strip()]
        han = str(item.get("han") or item.get("zh") or "".join(split)).strip()
        if not han or han in seen:
            continue
        seen.add(han)
        if len(split) > 1:
            clusters.update(split)
        readings = [str(p) for p in (item.get("pinyin") or []) if str(p).strip()] \
            if isinstance(item.get("pinyin"), (list, tuple)) \
            else [str(item.get("pinyin") or "").strip()]
        readings = [p for p in readings if p]
        if not readings and flat.get(han):
            readings = [flat[han]]
        out.append(
            {
                "zh": han,
                "han": han,
                "split": split or [han],
                "pinyin": " ".join(readings),
                "pinyin_list": readings,
                "vi": _stored_vi(item),
                "type": str(item.get("type") or "person"),
                "reason": str(item.get("reason") or ""),
                "count": int(_num(item.get("count"))),
            }
        )

    # Tên chỉ có trong bảng phẳng (file kiểu cũ, hoặc người dùng sửa tay) vẫn phải
    # hiện ra — nếu không, mở tab lên là thấy mất tên.
    for han, reading in sorted(flat.items()):
        if han in seen or han in clusters:
            continue
        seen.add(han)
        out.append(
            {
                "zh": han,
                "han": han,
                "split": [han],
                "pinyin": str(reading or ""),
                "pinyin_list": [str(reading)] if reading else [],
                "vi": "",
                "type": "person",
                "reason": "",
                "count": 0,
            }
        )
    return out


def write_names(video_id: str, entries: Any) -> dict[str, Any]:
    """Ghi lại bảng tên riêng người dùng vừa sửa; trả về bảng đã lưu.

    Đây là chỗ audit tìm ra lỗi **mất dữ liệu trong im lặng**: giao diện gửi
    `{"entries": [...]}`, máy chủ lại lấy nguyên thân yêu cầu làm bảng
    "chữ Hán → pinyin", nên `names.json` biến thành
    `{"names": {"entries": "[{...}]"}}` — tên vừa gõ mất sạch, file bị bơm một
    khoá rác, **mà giao diện vẫn toast "Đã lưu N tên"**. Với người dùng không
    phải dân IT thì đó là kiểu hỏng tệ nhất: họ tin là đã xong.

    Vì vậy hàm này:

    * nhận đúng danh sách entry (`{zh, pinyin, vi}`, và cả tên cũ `han`/`split`);
    * ghi qua `names_store.save_names_file` (kho an toàn dùng chung với S7,
      hợp đồng H4) chứ không qua `s7_ai.save_names`, vì `NameEntry.to_dict()`
      chưa có trường `vi` và sẽ làm rụng đúng cái tên tiếng Việt vừa gõ;
    * giữ nguyên các khoá phụ đã có trong file cũ (nguồn gốc, thời điểm chạy AI…),
      để một lần sửa tay không xoá lịch sử của S7;
    * **không bao giờ ghi đè một `names.json` đang hỏng.** File hỏng đọc ra bảng
      rỗng, màn hình hiện "đang trống" và vẫn cho bấm Lưu; ghi đè lúc đó là mất
      sạch tên cũ (mà thường file chỉ sai đúng một dấu phẩy). Trước khi ghi, file
      hỏng được đổi tên thành `names.json.hong-<thời điểm>` ngay cạnh đó; không
      đổi tên được thì từ chối lưu. Kết quả trả về có `backup` / `backup_name` để
      giao diện nói cho người dùng biết bản cũ đang nằm ở đâu.
    """
    rows = _clean_name_entries(entries)
    path = names_file(video_id)
    # Bảng hỏng đọc ra `{}`: không có khoá phụ nào giữ lại được từ nó, và
    # `save_names_file` cất nguyên file hỏng (từng byte) trước khi ghi.
    old: Any = load_names_file(path).data
    # Giao diện chỉ gửi {zh, pinyin, vi}. Loại tên, lý do và số lần xuất hiện mà
    # S7 đã ghi phải được giữ lại, nếu không mỗi lần bấm Lưu lại lặng lẽ đặt mọi
    # tên về "person", count 0. Khoá so khớp bỏ dấu cách để chữa luôn những file
    # đã bị các lần lưu trước ghi "陈 路周".
    before: dict[str, dict[str, Any]] = {}
    if isinstance(old, dict) and isinstance(old.get("entries"), list):
        for item in old["entries"]:
            if not isinstance(item, dict):
                continue
            key = "".join(
                str(item.get("han") or "".join(str(s) for s in item.get("split") or [])).split()
            )
            if key:
                before.setdefault(key, item)
    flat: dict[str, str] = {}
    stored: list[dict[str, Any]] = []
    for row in rows:
        for cluster, reading in zip(row["split"], row["pinyin_list"]):
            if cluster and reading:
                flat[cluster] = reading
        # Tên nhiều cụm chỉ ghi khoá theo từng cụm, đúng như S7 (`_merge_names`):
        # thêm cả khoá "陈路周" thì jieba giữ nguyên cả tên thành một token, lệch
        # với `split` hai cụm của chính entry đó.
        if len(row["split"]) == 1 and row["zh"] and row["pinyin"] and row["zh"] not in flat:
            flat[row["zh"]] = row["pinyin"]
        prev = before.get(row["zh"], {})
        stored.append(
            {
                "han": row["zh"],
                "split": row["split"],
                "pinyin": row["pinyin_list"],
                "type": row["type"] or str(prev.get("type") or "person"),
                "reason": row["reason"] or str(prev.get("reason") or ""),
                "count": row["count"] if row["count"] is not None else int(_num(prev.get("count"))),
                "vi": row["vi"],
            }
        )

    keep = (
        {k: v for k, v in old.items() if k not in ("version", "names", "entries", "updated_at")}
        if isinstance(old, dict)
        else {}
    )
    payload = {
        "version": 1,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **keep,
        "names": {k: v for k, v in sorted(flat.items())},
        "entries": stored,
    }
    try:
        # Chỉ tới đây — khi dữ liệu gửi lên đã qua kiểm tra — mới đụng tới file:
        # một yêu cầu hỏng không được làm file của người dùng đổi tên.
        backup = save_names_file(path, payload)
    except NamesSaveRefused as err:
        # File hỏng mà không cất được: `names_store` đã KHÔNG ghi gì. Nói đúng câu
        # của nó cho người dùng (bắt trước `OSError` chung của tầng HTTP).
        raise JobError(err.user_message) from err
    data = read_names(video_id)
    if backup is not None:
        data["backup"] = str(backup)
        data["backup_name"] = backup.name
    return data


def _clean_name_entries(entries: Any) -> list[dict[str, Any]]:
    """Dọn danh sách entry giao diện gửi lên; bỏ dòng trống, giữ thứ tự người gõ."""
    if isinstance(entries, dict):
        # Bảng phẳng kiểu cũ `{chữ Hán: pinyin}` — vẫn nhận, vẫn lưu đúng.
        entries = [{"zh": k, "pinyin": v} for k, v in entries.items()]
    if not isinstance(entries, (list, tuple)):
        raise JobError(
            "Dữ liệu bảng tên riêng gửi lên không đúng dạng. "
            "Hãy tải lại trang rồi thử lưu lại."
        )

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        zh = io_utils.nfc(str(item.get("zh") or item.get("han") or "").strip())
        split_raw = item.get("split")
        split = [io_utils.nfc(str(s).strip()) for s in split_raw] if isinstance(split_raw, (list, tuple)) else []
        split = [s for s in split if s]
        # Ô "Chữ Hán" của giao diện ghi các cụm cách nhau bằng dấu cách ("陈 路周")
        # — đúng chữ mẫu trong ô, và đúng cách một entry có `split` hiện lại trên
        # màn hình. Dấu cách ấy là ranh giới cụm, không phải một phần của tên:
        # phụ đề chữ Hán không bao giờ có dấu cách, nên khoá "陈 路周" trong
        # names.json là một tên không bao giờ khớp (S5 không tách theo nó, bảng
        # thuật ngữ của S8 không bao giờ gặp nó), và lần Lưu đầu tiên còn biến
        # entry S7 đã ghi ("陈路周", split ["陈","路周"]) thành đúng dạng hỏng đó.
        if not split:
            split = zh.split()
        zh = "".join(zh.split()) or "".join(split)
        if not zh or zh in seen:
            continue
        seen.add(zh)

        raw_py = item.get("pinyin")
        readings = (
            [str(p).strip() for p in raw_py if str(p).strip()]
            if isinstance(raw_py, (list, tuple))
            else [p for p in str(raw_py or "").split() if p]
        )
        if not split:
            split = [zh]
        # Số cụm Hán phải khớp số cụm pinyin, nếu không `NameEntry.pairs()` sẽ
        # cắt cụt bằng `zip` và mất âm đọc mà không báo gì.
        if len(readings) != len(split):
            split = [zh]
            readings = [" ".join(readings)] if readings else []

        rows.append(
            {
                "zh": zh,
                "split": split,
                "pinyin": " ".join(readings),
                "pinyin_list": readings,
                "vi": _name_vi(item.get("vi")),
                # Rỗng / None = giao diện không gửi (nó chỉ gửi zh, pinyin, vi);
                # `write_names` lấy lại giá trị cũ trong file chứ không ghi đè
                # bằng mặc định.
                "type": str(item.get("type") or "").strip(),
                "reason": str(item.get("reason") or "").strip(),
                "count": int(_num(item.get("count"))) if item.get("count") is not None else None,
            }
        )
    return rows


def _name_vi(value: Any) -> str:
    """Tên tiếng Việt đúng như người dùng gõ — **chỉ** chuẩn hoá NFC.

    Không `strip_tones`, không viết hoa lại, không cắt chữ: "Cường đầu trọc" phải
    quay về đúng từng ký tự. Luật bỏ dấu thanh chỉ dành cho pinyin; áp nó lên
    dòng này từng biến tên nhân vật thành "Cuong Đau Troc" (audit đợt 3). NFC là
    phép duy nhất được làm, vì macOS gửi chữ có dấu ở dạng NFD và hai dạng ấy so
    với nhau thì khác dù nhìn giống hệt. Chuỗi toàn khoảng trắng coi là trống.
    """
    if value is None:
        return ""
    text = io_utils.nfc(str(value))
    return text if text.strip() else ""


#: File cho biết một thư mục trong `work/` là một phim đã từng chạy (hoặc một cặp
#: file đã mở vào trình sửa), dù nó chưa có bảng tên riêng.
_MOVIE_MARKERS: tuple[str, ...] = ("S0_info.json", ORIGINAL_FILE, DRAFT_FILE)


def _looks_like_movie(folder: Path) -> bool:
    """Thư mục này có dấu vết của một lần chạy/mở phim không."""
    try:
        if any((folder / name).is_file() for name in _MOVIE_MARKERS):
            return True
        if next(folder.glob("S[0-9]_*.json"), None) is not None:
            return True
        # Bản cất `.truoc-…` không phải dấu vết của một lần chạy: nó chỉ là bản cũ
        # của một file kết quả, cùng luật với `_movie_title`.
        return any(not io_utils.is_kept_copy(p) for p in folder.glob("*.srt"))
    except OSError:
        return False


def _movie_title(folder: Path, video_id: str) -> str:
    """Tên phim để hiện trong ô chọn: tiêu đề S0 → tên file .srt → mã video.

    Bỏ file `_vi` và MỌI bản cất (`io_utils.is_kept_copy`, tên có `.truoc-`):
    `Phim_vi.truoc-20260910-183000.srt` không kết thúc bằng `_vi`, nên trước đây
    nó lọt vào và ô chọn phim hiện một cái tên mà người dùng không hề đặt.
    """
    info = _read_json_quiet(folder / "S0_info.json")
    title = str(info.get("title") or "").strip() if isinstance(info, dict) else ""
    if title:
        return title
    try:
        srt = sorted(
            p
            for p in folder.glob("*.srt")
            if not p.stem.endswith("_vi") and not io_utils.is_kept_copy(p)
        )
    except OSError:
        srt = []
    return srt[0].stem if srt else video_id


def list_name_tables() -> list[dict[str, Any]]:
    """Danh sách **mọi phim đã từng chạy**, cho ô chọn phim ở tab "Tên riêng".

    Trước đây chỉ liệt kê thư mục đã có `names.json`. Nhưng `names.json` chỉ do
    AI ghi ra, mà người dùng mặc định không có khoá API — nên ô chọn phim luôn
    trống và họ **không bao giờ lập được bảng tên** (audit đợt 3). Nay mọi thư
    mục trong `work/` có dấu vết một lần chạy (`S0_info.json`, file chặng, file
    `.srt`, bản nháp) đều hiện ra; phim chưa có bảng thì `count` = 0 và bấm "Thêm
    tên" rồi Lưu là tạo bảng.

    Không hỏi `JobManager` vì lịch sử công việc chỉ giữ 60 mục gần nhất, còn thư
    mục làm việc thì nằm lại trên đĩa mãi. Phim dùng gần nhất đứng đầu.
    Thư mục bắt đầu bằng `_` (như `_uploads`) là của tool, không phải phim.
    """
    cfg = _safe_cfg()
    root = work_root(cfg)
    rows: list[tuple[float, dict[str, Any]]] = []
    try:
        folders = list(root.iterdir())
    except OSError:
        return []
    for folder in folders:
        video_id = folder.name
        if video_id.startswith(("_", ".")) or not VIDEO_ID_RE.match(video_id):
            continue
        try:
            if not folder.is_dir():
                continue
            stamp = folder.stat().st_mtime
        except OSError:
            continue
        has_table = names_file(video_id, cfg).is_file()
        if not has_table and not _looks_like_movie(folder):
            continue
        table = read_names(video_id) if has_table else {}
        rows.append(
            (
                stamp,
                {
                    "video_id": video_id,
                    "title": _movie_title(folder, video_id),
                    "count": int(table.get("count") or 0),
                    # Bảng hỏng đếm ra 0 tên — phải phân biệt được với bảng trống thật.
                    # Chỉ gắn khi hỏng: dòng của phim bình thường giữ nguyên hình dạng cũ.
                    **({"corrupt": True} if table.get("corrupt") else {}),
                },
            )
        )
    rows.sort(key=lambda row: (-row[0], row[1]["video_id"]))
    return [row for _stamp, row in rows]


# --------------------------------------------------------------------------- #
# đọc byte thô — ngoại lệ duy nhất của luật “chỉ io_utils được gọi open()”
# --------------------------------------------------------------------------- #
#
# Luật đó có lý do rất cụ thể (xem docstring của `io_utils`): bốn cái bẫy bảng mã,
# ký tự xuống dòng, codepage của Terminal Windows và chuẩn hoá NFC. Cả bốn đều là
# bẫy của **văn bản**. Hai chỗ dưới đây không đọc văn bản:
#
# * phát một đoạn byte của file `.wav` cho thẻ `<audio>` — không có ký tự nào để
#   giải mã, và nếu giải mã thì mới là hỏng;
# * đọc thô một file `.srt` mà `io_utils.read_text` từ chối vì nó không phải
#   UTF-8 — file GB18030 của bên dịch gửi sang. Byte đọc được đi thẳng vào
#   `decode_srt_bytes` ngay bên dưới, tức là vẫn đúng một cửa giải mã.
#
# `io_utils` chưa có API nhị phân và thêm vào đó là việc của người giữ module ấy,
# nên hai hàm này là chỗ duy nhất trong tầng web chạm tới byte thô.

def _read_all_bytes(path: Path) -> bytes:
    """Đọc trọn một file nhỏ ở dạng byte (chỉ dùng cho file `.srt` đã kiểm cỡ)."""
    return path.read_bytes()


def _iter_byte_range(
    path: Path,
    start: int,
    length: int,
    chunk: int = AUDIO_CHUNK_BYTES,
) -> Iterator[bytes]:
    """Phát ra từng mảnh của khoảng `[start, start+length)` trong một file.

    Đọc theo mảnh chứ không nạp cả file: một phim 40 phút là chừng 77MB âm thanh
    16kHz, mà trình duyệt tua đi tua lại thì mỗi lần tua là một yêu cầu mới.

    `os.O_BINARY` chỉ có trên Windows; trên macOS `getattr` trả 0 nên biểu thức
    vẫn đúng mà không phải rẽ nhánh theo hệ điều hành.
    """
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        os.lseek(fd, max(0, int(start)), os.SEEK_SET)
        remaining = max(0, int(length))
        while remaining > 0:
            data = os.read(fd, min(chunk, remaining))
            if not data:
                return
            remaining -= len(data)
            yield data
    finally:
        os.close(fd)


class _RangeNotSatisfiable(ValueError):
    """Khoảng byte trình duyệt xin nằm ngoài file — phải trả 416, không phải 200."""

    def __init__(self, size: int) -> None:
        super().__init__("Khoảng byte yêu cầu nằm ngoài kích thước file.")
        self.size = int(size)


_RANGE_RE = re.compile(r"^\s*(\d*)\s*-\s*(\d*)\s*$")


def parse_byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Đọc `Range: bytes=…` thành cặp `(đầu, cuối)` **bao gồm cả hai đầu**.

    Trả `None` nghĩa là “cứ trả nguyên file, mã 200”. Đó là hành vi RFC 7233 yêu
    cầu cho một `Range` không hiểu được: máy chủ **phải bỏ qua** chứ không được
    báo lỗi, vì một trình duyệt cũ gửi header lạ mà bị từ chối thì người dùng
    mất luôn tiếng, không hiểu vì sao.

    Ném `_RangeNotSatisfiable` khi cú pháp đúng nhưng khoảng nằm ngoài file —
    đó mới là 416.

    Ba dạng phải xử lý được, và dạng thứ hai là dạng thẻ `<audio>` dùng nhiều
    nhất khi người dùng kéo thanh tua:

    * `bytes=0-1023`   — xin đúng một đoạn;
    * `bytes=1000-`    — **đuôi mở**, xin từ đây tới hết file;
    * `bytes=-500`     — 500 byte cuối.

    Nhiều khoảng trong một yêu cầu (`bytes=0-9,20-29`) bị bỏ qua có chủ ý: câu
    trả lời đúng cho nó là `multipart/byteranges`, không trình phát nào cần, và
    làm dở dang còn tệ hơn là không làm.
    """
    text = str(header or "").strip()
    if not text or size <= 0:
        return None
    unit, _, spec = text.partition("=")
    if unit.strip().lower() != "bytes" or not spec.strip():
        return None
    if "," in spec:
        return None

    match = _RANGE_RE.match(spec)
    if match is None:
        return None
    first, last = match.group(1), match.group(2)

    if not first and not last:
        return None
    if not first:
        # "-500": 500 byte cuối. Xin nhiều hơn cả file thì trả cả file.
        length = int(last)
        if length <= 0:
            raise _RangeNotSatisfiable(size)
        return (max(0, size - length), size - 1)

    start = int(first)
    if start >= size:
        raise _RangeNotSatisfiable(size)
    if not last:
        return (start, size - 1)
    end = min(int(last), size - 1)
    if end < start:
        raise _RangeNotSatisfiable(size)
    return (start, end)


def audio_media_type(path: Path) -> str:
    """Loại nội dung theo đuôi file; đuôi lạ thì để trình duyệt tự đoán."""
    return AUDIO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def job_audio_file(job: Job) -> Path | None:
    """File âm thanh để nghe lại từng câu, hoặc `None` khi công việc chưa có.

    Ưu tiên bản đã xử lý ở chặng S1: đó đúng là thứ máy đã nghe để gỡ băng, nên
    mốc thời gian của phụ đề khớp với nó từng phần nghìn giây. Bản tải thô ở S0
    chỉ là lưới đỡ cho công việc dừng giữa chừng.

    Đường dẫn lấy từ file JSON do chính tool ghi, nhưng vẫn phải qua `_safe_path`:
    file đó nằm trong thư mục người dùng và có thể bị sửa tay.
    """
    video_id = str(job.video_id or "").strip()
    if not VIDEO_ID_RE.match(video_id):
        return None
    base = work_dir_for(video_id)
    roots = _allowed_roots(job)
    for number, name in _AUDIO_STAGES:
        data = _read_json_quiet(base / f"S{number}_{name}.json")
        raw = data.get("audio_path") if isinstance(data, dict) else None
        if not raw:
            continue
        target = _safe_path(str(raw), roots)
        if target is not None and target.is_file():
            return target
    return None


def job_video_file(job: Job) -> Path | None:
    """File video để xem trước có phụ đề, hoặc `None` khi công việc không có.

    Đường dẫn lấy từ `S0_info.json` (`video_path`). Ngoài các thư mục được phép,
    còn chấp nhận đúng file mà người dùng đã tự chỉ làm nguồn (`source`): đó là
    file của họ, họ đưa vào, không phải đường dẫn do ai đó nhét thêm.
    """
    video_id = str(job.video_id or "").strip()
    if not VIDEO_ID_RE.match(video_id):
        return None
    base = work_dir_for(video_id)
    data = _read_json_quiet(base / "S0_info.json")
    if not isinstance(data, dict):
        return None
    raw = str(data.get("video_path") or "").strip()
    if not raw:
        return None
    roots = _allowed_roots(job)
    target = _safe_path(raw, roots)
    if target is None:
        own_source = str(data.get("source") or "").strip()
        if own_source and data.get("is_local") and Path(raw) == Path(own_source):
            try:
                target = Path(raw).expanduser().resolve()
            except OSError:
                target = None
    if target is not None and target.is_file():
        return target
    return None


_VIDEO_MEDIA_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".webm": "video/webm",
    ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".ts": "video/mp2t", ".flv": "video/x-flv", ".wmv": "video/x-ms-wmv",
}
_BROWSER_PLAYABLE = frozenset({".mp4", ".m4v", ".webm"})


def video_media_type(path: Path) -> str:
    return _VIDEO_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def job_media_info(job: Job) -> dict[str, Any]:
    """Trình sửa hỏi một lần: có hình không, phát thẳng được không, lấy ở đâu."""
    video = job_video_file(job)
    audio = job_audio_file(job)
    playable = bool(video and video.suffix.lower() in _BROWSER_PLAYABLE)
    base = f"/api/jobs/{job.id}"
    source_url = ""
    data = _read_json_quiet(work_dir_for(str(job.video_id or "")) / "S0_info.json") if VIDEO_ID_RE.match(str(job.video_id or "")) else None
    if isinstance(data, dict):
        source_url = str(data.get("source_url") or "") if not data.get("is_local") else ""
    note = ""
    if video and not playable:
        note = (
            f"Video nguồn là định dạng {video.suffix.lower()} mà trình duyệt không phát thẳng được, "
            "nên phần xem trước chỉ có tiếng. Muốn có hình, hãy đổi video sang .mp4 rồi chạy lại."
        )
    elif not video and source_url:
        note = "Lần chạy này mới tải phần tiếng. Bấm “Tải video để xem trước” để lấy thêm hình."
    elif not video:
        note = "Nguồn không có hình, phần xem trước chỉ có tiếng."
    return {
        "has_video": bool(video),
        "has_audio": bool(audio),
        "playable": playable,
        "video_url": f"{base}/video" if playable else "",
        "audio_url": f"{base}/audio" if audio else "",
        "video_ext": video.suffix.lower() if video else "",
        "can_fetch_preview": bool(not video and source_url),
        "note": note,
    }


def job_peaks(job: Job) -> dict[str, Any] | None:
    """Dạng sóng thu gọn của file tiếng: 50 mốc mỗi giây, mỗi mốc là biên độ đỉnh 0..255.

    Người soát đặt mốc thời gian bằng MẮT trên dạng sóng nhanh và chính xác hơn
    nhiều so với nghe đi nghe lại — đó là lý do Aegisub có khung audio. Tính một
    lần rồi cất `peaks.json` cạnh file tiếng; 34 phút audio thành ~100KB base64.
    """
    import base64
    import wave

    video_id = str(job.video_id or "").strip()
    if not VIDEO_ID_RE.match(video_id):
        return None
    audio = job_audio_file(job)
    if audio is None or audio.suffix.lower() != ".wav":
        return None
    base = work_dir_for(video_id)
    cache = base / "peaks.json"
    try:
        if cache.is_file() and cache.stat().st_mtime >= audio.stat().st_mtime:
            data = _read_json_quiet(cache)
            if isinstance(data, dict) and data.get("peaks_b64"):
                return data
    except OSError:
        pass
    try:
        import numpy as np

        with wave.open(str(audio), "rb") as wf:
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            rate = wf.getframerate()
            frames = wf.getnframes()
            raw = wf.readframes(frames)
        if width != 2 or rate <= 0:
            return None
        samples = np.frombuffer(raw, dtype="<i2")
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1)
        per_sec = 50
        win = max(1, rate // per_sec)
        n = len(samples) // win
        if n <= 0:
            return None
        block = np.abs(samples[: n * win].astype("float32")).reshape(n, win).max(axis=1)
        top = float(block.max()) or 1.0
        peaks = np.clip(block / top * 255.0, 0, 255).astype("uint8").tobytes()
        data = {
            "rate": rate / win,
            "count": int(n),
            "duration": len(samples) / rate,
            "peaks_b64": base64.b64encode(peaks).decode("ascii"),
        }
    except Exception:  # noqa: BLE001 - dạng sóng là tiện ích, không được làm hỏng trình sửa
        return None
    try:
        io_utils.write_json(cache, data)
    except OSError:
        pass
    return data


def _review_path(job: Job) -> Path | None:
    video_id = str(job.video_id or "").strip()
    if not VIDEO_ID_RE.match(video_id):
        return None
    return work_dir_for(video_id) / "review.json"


def read_review(job: Job) -> list[int]:
    """Danh sách số thứ tự dòng đã được người soát đánh dấu ✓."""
    path = _review_path(job)
    data = _read_json_quiet(path) if path else None
    if not isinstance(data, dict):
        return []
    out: list[int] = []
    for item in data.get("reviewed") or []:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return sorted(set(out))


def write_review(job: Job, reviewed: Any) -> list[int]:
    path = _review_path(job)
    if path is None:
        raise JobError("Công việc này không có thư mục làm việc để ghi dấu đã soát.")
    clean: list[int] = []
    for item in reviewed if isinstance(reviewed, list) else []:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if 0 < n < 1_000_000:
            clean.append(n)
    clean = sorted(set(clean))
    io_utils.write_json(path, {"reviewed": clean, "updated_at": time.time()})
    return clean


def translate_one_line(job: Job, zh: str, before: list[str], after: list[str]) -> dict[str, Any]:
    """Dịch lại MỘT câu với ngữ cảnh vài câu quanh nó — nút "Dịch lại câu này".

    Đi qua đúng bộ dịch mà chặng S8 dùng (Gemini nếu có khoá, không thì Google
    miễn phí) và bảng tên riêng của phim, để câu dịch lại nhất quán với cả file.
    """
    from srtgen.providers import get_translator
    from srtgen.providers.base import ProviderError

    text = str(zh or "").strip()
    if not text:
        raise JobError("Dòng chữ Hán đang trống, không có gì để dịch.")
    cfg = _config_for_job(job)
    translator = get_translator(cfg)
    context: dict[str, Any] = {
        "before": [str(x) for x in (before or [])][-5:],
        "after": [str(x) for x in (after or [])][:5],
        "title": str(job.title or ""),
    }
    try:
        from srtgen.stages.s8_translate import build_glossary

        video_id = str(job.video_id or "").strip()
        if VIDEO_ID_RE.match(video_id):
            table, _meta = build_glossary(work_dir_for(video_id) / "names.json")
            if table:
                context["glossary"] = table
    except Exception:  # noqa: BLE001 - không có bảng tên thì dịch không bảng
        pass
    try:
        answer = translator.translate_batch([{"id": 0, "zh": text}], context, target="vi")
    except ProviderError as err:
        raise JobError(str(getattr(err, "user_message", "") or err), detail=str(err)) from err
    vi = ""
    if isinstance(answer, dict):
        for key, value in answer.items():
            try:
                if int(key) == 0:
                    vi = str(value or "").strip()
            except (TypeError, ValueError):
                continue
    reason = str(getattr(translator, "reason", "") or "")
    if not vi or vi == text:
        raise JobError(reason or "Dịch vụ dịch không trả về câu tiếng Việt cho dòng này. Hãy thử lại sau ít phút.")
    return {"vi": vi, "provider": str(getattr(translator, "name", "") or ""), "note": reason}


def translation_status(job: Job) -> dict[str, Any]:
    """Chặng dịch đã dịch được bao nhiêu dòng, bằng gì, và vì sao có dòng còn nguyên chữ Hán.

    Đọc từ `S8_translate.json`. Trước đây con số này chỉ nằm trong báo cáo HTML —
    người dùng mở trình sửa thấy cột tiếng Việt toàn chữ Hán mà không có một lời
    giải thích nào (đã xảy ra: máy thiếu deep-translator, 336/336 dòng giữ nguyên).
    """
    video_id = str(job.video_id or "").strip()
    if not VIDEO_ID_RE.match(video_id):
        return {"available": False}
    data = _read_json_quiet(work_dir_for(video_id) / "S8_translate.json")
    if not isinstance(data, dict):
        return {"available": False}
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    errors = data.get("errors") if isinstance(data.get("errors"), list) else []
    reason = ""
    for err in errors:
        if isinstance(err, dict) and err.get("user_message"):
            reason = str(err["user_message"])
            break
    if not reason:
        reason = str(data.get("provider_reason") or "")
    total = int(counts.get("cues") or 0)
    kept = int(counts.get("kept_original") or 0)
    return {
        "available": True,
        "provider": str(data.get("provider") or ""),
        "model": str(data.get("model") or ""),
        "total": total,
        "translated": int(counts.get("translated") or 0),
        "kept_original": kept,
        "reason": reason,
        "warning": str(data.get("warning") or ""),
        "can_retranslate": bool(job.can_rerun) if hasattr(job, "can_rerun") else True,
    }


def _read_json_quiet(path: Path) -> Any:
    """Đọc JSON, coi mọi trục trặc là “không có file” — đây là dữ liệu phụ trợ."""
    try:
        return io_utils.read_json(path)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# trình sửa phụ đề trực tiếp
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class EditorFiles:
    """Bộ file mà một lần mở trình sửa làm việc trên.

    `vi` và `bundle` là đường dẫn **dự kiến**, không phải đường dẫn chắc chắn có
    thật: lúc mở thì dùng để đọc (kiểm `is_file()` trước), lúc lưu thì dùng để
    ghi. `None` nghĩa là đường dẫn đó nằm ngoài thư mục cho phép, tức là không
    được đụng vào.
    """

    srt: Path
    vi: Path | None
    bundle: Path | None
    video_id: str
    #: Hai file để soát trong Aegisub: `<tên>.ass` và `<tên>_song-ngu.ass`. Mặc
    #: định `None` để chỗ chỉ dựng bảng cue (mở file có sẵn) khỏi phải biết tới.
    ass: Path | None = None
    bilingual_ass: Path | None = None


def editor_files(job: Job) -> EditorFiles:
    """Tìm bộ file của một công việc, hoặc nói bằng tiếng Việt vì sao không mở được."""
    raw = str((job.result or {}).get("srt") or "").strip()
    if not raw:
        raise JobError(
            "Công việc này chưa có file phụ đề nên chưa mở được trong trình sửa. "
            "Hãy chạy xong một video, hoặc dùng nút “Mở file có sẵn”."
        )
    roots = _allowed_roots(job)
    srt = _safe_path(raw, roots)
    if srt is None or not srt.is_file():
        raise JobError(
            "Không còn thấy file phụ đề của công việc này ở chỗ cũ. "
            "Có thể nó đã bị di chuyển, đổi tên hoặc xoá."
        )
    return EditorFiles(
        srt=srt,
        vi=_side_file(job, "vi_srt", srt, f"{srt.stem}_vi.srt", roots),
        bundle=_side_file(job, "bundle", srt, f"{srt.stem}.bundle.json", roots),
        video_id=_editor_video_id(job, srt),
        ass=_side_file(job, "ass", srt, f"{srt.stem}.ass", roots),
        bilingual_ass=_side_file(job, "bilingual_ass", srt, f"{srt.stem}_song-ngu.ass", roots),
    )


def _side_file(
    job: Job,
    kind: str,
    srt: Path,
    fallback_name: str,
    roots: Sequence[Path],
) -> Path | None:
    """Đường dẫn file đi kèm: lấy theo kết quả đã ghi, không có thì đoán theo tên."""
    raw = str((job.result or {}).get(kind) or "").strip()
    if raw:
        target = _safe_path(raw, roots)
        if target is not None:
            return target
    return _safe_path(str(srt.with_name(fallback_name)), roots)


def _editor_video_id(job: Job, srt: Path) -> str:
    """Mã video để đặt bản nháp và bản gốc.

    Công việc chạy từ link YouTube đã có sẵn mã. Việc mở một file trên máy thì
    suy ra từ **đường dẫn file**, nên mở lại đúng file đó ngày mai vẫn tìm về
    đúng bản nháp cũ thay vì sinh một thư mục mới mỗi lần.
    """
    video_id = str(job.video_id or "").strip()
    if VIDEO_ID_RE.match(video_id):
        return video_id
    guess = make_video_id(str(srt))
    return guess if VIDEO_ID_RE.match(guess) else ""


def _read_srt_file(path: Path) -> str:
    """Đọc một file `.srt`, chịu được cả bảng mã của bên dịch gửi sang."""
    try:
        return io_utils.read_text(path)
    except UnicodeDecodeError:
        return decode_srt_bytes(_read_all_bytes(path))


def editor_document(job: Job) -> dict[str, Any]:
    """Toàn bộ nội dung một công việc, ở dạng bảng cho trình sửa.

    Đọc thẳng từ file `.srt` trên đĩa chứ không từ bộ nhớ: người dùng có thể vừa
    sửa file bằng Aegisub xong mới mở trình sửa, và bản trên đĩa mới là bản thật.
    """
    files = editor_files(job)
    zh_text = _read_srt_file(files.srt)
    vi_text = _read_srt_file(files.vi) if (files.vi and files.vi.is_file()) else ""
    return _attach_job(_doc_payload(files, zh_text, vi_text), job)


def _attach_job(payload: dict[str, Any], job: Job) -> dict[str, Any]:
    """Gắn phần phụ thuộc vào công việc (âm thanh, bản nháp) vào một bảng cue."""
    audio = job_audio_file(job)
    payload["job"] = job.public_dict()
    payload["job_id"] = job.id
    payload["title"] = job.title or Path(payload["srt_path"]).stem
    payload["has_audio"] = audio is not None
    payload["audio_url"] = f"/api/jobs/{job.id}/audio" if audio is not None else ""
    payload["audio_path"] = str(audio) if audio is not None else ""
    payload["draft_url"] = f"/api/jobs/{job.id}/draft"
    video_id = str(payload.get("video_id") or "")
    payload["can_revert"] = bool(video_id) and original_file(video_id).is_file()
    # Có ngay từ lúc mở (không đợi tới lúc lưu): giao diện cần biết trước để đặt
    # tên nút Lưu là “Lưu và tải về” cho file mở bằng nội dung (H6).
    payload["origin"] = editor_origin(job)
    payload["downloads"] = editor_downloads(job)
    return payload


#: Ba nguồn gốc của một bảng đang mở trong trình sửa (hợp đồng H6):
#:
#: * `content` — trình duyệt chỉ gửi NỘI DUNG file (hộp chọn file, kéo thả), nên
#:   bản lưu nằm trong thư mục làm việc của tool chứ không phải chỗ file gốc;
#: * `path` — mở theo đường dẫn thật, Lưu ghi thẳng vào file gốc;
#: * `job` — file do chính tool xuất ra ở thư mục kết quả.
ORIGIN_CONTENT = "content"
ORIGIN_PATH = "path"
ORIGIN_JOB = "job"
_ORIGINS = frozenset({ORIGIN_CONTENT, ORIGIN_PATH, ORIGIN_JOB})


def editor_origin(job: Job) -> str:
    """Bảng này mở từ đâu: `content`, `path` hay `job` (xem `ORIGIN_*`).

    Vì sao máy chủ phải nói ra: mở bằng nội dung thì trình duyệt không bao giờ
    biết đường dẫn thật, nên bản lưu nằm trong `work/…` của tool. Người dùng
    bấm Lưu xong đi tìm trong thư mục phim của họ sẽ chỉ thấy file CŨ và tưởng
    mất cả buổi sửa. Biết là `content` thì giao diện đổi nút thành “Lưu và tải
    về”, tải ngay hai file và nói rõ bản lưu nằm ở đâu.

    Việc mở từ phiên bản trước (chưa có trường `origin`) được suy ra: bản sao
    theo nội dung luôn nằm trong `work_root()`.
    """
    result = job.result or {}
    stored = str(result.get("origin") or "")
    if stored in _ORIGINS:
        return stored
    if job.mode != MODE_LOCAL:
        return ORIGIN_JOB
    raw = str(result.get("srt") or "").strip()
    if raw:
        try:
            srt = Path(raw).expanduser().resolve()
            root = work_root().expanduser().resolve()
            if srt.is_relative_to(root):
                return ORIGIN_CONTENT
        except (OSError, ValueError):
            pass
    return ORIGIN_PATH


def editor_downloads(job: Job) -> dict[str, str | None]:
    """`{srt: url, vi_srt: url | None}` — chỉ cho file ĐANG CÓ THẬT trên đĩa.

    Hỏi đĩa chứ không tin `job.result`: nút “Lưu và tải về” tải ngay sau khi
    lưu, và một đường tải trỏ vào file không có sẽ trả 410 — người dùng thấy
    “lỗi” đúng lúc vừa lưu xong, tức là lúc dễ hoảng nhất.
    """
    result = job.result or {}
    roots = _allowed_roots(job)

    def url(kind: str) -> str | None:
        raw = str(result.get(kind) or "").strip()
        if not raw:
            return None
        target = _safe_path(raw, roots)
        if target is None or not target.is_file():
            return None
        return f"/api/download/{job.id}/{kind}"

    return {"srt": url("srt"), "vi_srt": url("vi_srt")}


def _mark_saved(payload: dict[str, Any], job: Job, files: EditorFiles) -> None:
    """Gắn vào kết quả một lần ghi (Lưu / Hoàn nguyên) chỗ file vừa được ghi (H6).

    `saved_to` là THƯ MỤC (nút “Mở thư mục” mở thẳng chỗ đó), `saved_files` là
    các file vừa ghi trong đó. `downloads` tính lại SAU khi ghi: file `_vi.srt`
    có thể vừa được tạo lần đầu.
    """
    payload["origin"] = editor_origin(job)
    payload["saved_to"] = str(files.srt.parent)
    saved = [str(files.srt)]
    if files.vi is not None and files.vi.is_file():
        saved.append(str(files.vi))
    payload["saved_files"] = saved
    payload["downloads"] = editor_downloads(job)


def _doc_payload(
    files: EditorFiles,
    zh_text: str,
    vi_text: str,
    *,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """Dựng bảng cue + findings từ nội dung hai file, không đụng tới đĩa.

    Dòng Hán và dòng pinyin lấy **nguyên văn từ file**, không phải render lại từ
    token. Khác biệt đó quan trọng: khi hai dòng lệch số cụm, bộ ghép token từ
    chối đoán và trả về pinyin rỗng, nên render lại sẽ hiện chữ Hán ở cột pinyin
    — đúng cái ô mà người dùng mở trình sửa lên để sửa. Token vẫn được dựng song
    song, nhưng chỉ để lấy cờ và cho cột “xem cụm”.
    """
    from srtgen.core.rules import summarize, validate_pair, validate_text
    from srtgen.core.srt import format_timestamp, parse_srt, parse_srt_document

    blocks = parse_srt(zh_text)
    doc = parse_srt_document(zh_text)
    vi_lines = _vi_lines_of(vi_text)

    findings = list(validate_text(zh_text))
    has_vi = bool(vi_text.strip())
    if has_vi:
        findings.extend(validate_pair(zh_text, vi_text))

    # Lỗi do CHÍNH lúc tách file thành block — ví dụ một block có 8 dòng vì block
    # sau nó có mốc thời gian sai nên bị nuốt vào. `validate_text` không thấy loại
    # này (nó nhìn nội dung đã tách rồi), nên phải lấy thẳng từ bộ tách.
    seen = {(f.code, f.cue_index) for f in findings}
    parse_findings: list[dict[str, Any]] = []
    for item in doc.meta.get("parse_findings") or []:
        if not isinstance(item, dict):
            continue
        key = (item.get("code"), item.get("cue_index"))
        if key in seen:
            # `rules.py` đã báo đúng chuyện này rồi. Hiện hai lần cùng một câu
            # khiến người dùng tưởng dòng đó hỏng gấp đôi.
            continue
        seen.add(key)
        parse_findings.append(dict(item))

    by_cue: dict[Any, list[dict[str, Any]]] = {}
    for finding in findings:
        by_cue.setdefault(finding.cue_index, []).append(finding.to_dict())
    for item in parse_findings:
        by_cue.setdefault(item.get("cue_index"), []).append(item)

    stage_flags = _stage_flags(files.video_id, len(blocks))
    cues: list[dict[str, Any]] = []
    for position, block in enumerate(blocks, start=1):
        cue = doc.cues[position - 1] if position <= len(doc.cues) else None
        tokens = list(cue.tokens) if cue is not None else []
        lines = list(block.lines) + ["", ""]
        key = block.index if block.index_ok else position
        cues.append(
            {
                "index": position,
                "file_index": block.index,
                "start": round(float(block.start), 3),
                "end": round(float(block.end), 3),
                "start_text": format_timestamp(block.start),
                "end_text": format_timestamp(block.end),
                "zh_line": lines[0],
                "py_line": lines[1],
                "vi_line": vi_lines[position - 1] if position <= len(vi_lines) else "",
                "tokens": [_token_payload(t) for t in tokens],
                "clusters": sum(1 for t in tokens if t.is_word()),
                "findings": by_cue.get(key, []),
                "flags": sorted(
                    {flag for t in tokens for flag in t.flags}
                    | set(stage_flags.get(position, ()))
                ),
            }
        )

    row_keys = {cue["file_index"] if cue["file_index"] else cue["index"] for cue in cues}
    row_keys |= {cue["index"] for cue in cues}
    all_findings = [f.to_dict() for f in findings] + parse_findings
    file_findings = [
        item for item in all_findings
        if item.get("cue_index") is not None and item.get("cue_index") not in row_keys
    ]
    integrity = _file_integrity(zh_text, len(blocks), file_findings)

    return {
        "video_id": files.video_id,
        "srt_path": str(files.srt),
        "vi_path": str(files.vi) if (files.vi and files.vi.is_file()) else "",
        "bundle_path": str(files.bundle) if (files.bundle and files.bundle.is_file()) else "",
        "has_vi": has_vi,
        "cues": cues,
        "cue_count": len(cues),
        "findings": all_findings,
        "summary": summarize(findings),
        "file_findings": file_findings,
        "integrity": integrity,
        "notes": list(notes),
    }


#: Một dòng "trông như mốc thời gian": có hai cụm giờ và mũi tên ở giữa. Cố ý
#: rộng hơn `_TIMESTAMP_LOOSE_RE` của bộ tách, vì việc ở đây là ĐẾM xem file
#: định có bao nhiêu đoạn, kể cả những đoạn viết sai mà bộ tách không nhận.
_LOOKS_LIKE_TIMESTAMP = re.compile(
    r"^\s*\d{1,3}:\d{1,2}(:\d{1,2})?[,.]?\d{0,4}\s*-{1,2}>\s*"
    r"\d{1,3}:\d{1,2}(:\d{1,2})?[,.]?\d{0,4}"
)


def _file_integrity(
    zh_text: str,
    parsed: int,
    file_findings: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Đối chiếu “file định có bao nhiêu đoạn” với “tool đọc được bao nhiêu”.

    Vì sao cần: một đoạn có mốc thời gian viết sai (ví dụ ``00:00:30,1000`` —
    mili giây bốn chữ số) không được bộ tách nhận là đoạn mới, nên nó bị dính
    vào đoạn ngay trước. Bảng sửa vẫn hiện ra bình thường, chỉ là thiếu đoạn và
    đoạn trước thì lẫn cả số thứ tự lẫn dòng giờ vào chữ. Trước đây không có gì
    báo, người dùng bấm Lưu là ghi đè mất. Giờ con số này đi thẳng lên băng cảnh
    báo đỏ ở đầu trình sửa.
    """
    expected = sum(1 for line in zh_text.splitlines() if _LOOKS_LIKE_TIMESTAMP.match(line))
    missing = max(0, expected - parsed)
    lines: list[int] = []
    from srtgen.core.srt import TIMESTAMP_RE

    for position, line in enumerate(zh_text.splitlines(), start=1):
        if _LOOKS_LIKE_TIMESTAMP.match(line) and TIMESTAMP_RE.match(line.strip()) is None:
            lines.append(position)
    return {
        "expected_cues": expected,
        "parsed_cues": parsed,
        "missing_cues": missing,
        "bad_timestamp_lines": lines[:50],
        "bad_timestamp_count": len(lines),
        "problem_count": len(file_findings) + missing,
    }


def _token_payload(token: Any) -> dict[str, Any]:
    """Một token cho trình sửa: hình dạng của bundle, thêm cờ cần người duyệt."""
    data = dict(token.to_dict())
    data["flags"] = list(token.flags)
    return data


def _vi_lines_of(vi_text: str) -> list[str]:
    """Dòng tiếng Việt của từng block, giữ nguyên văn kể cả khoảng trắng thừa.

    Giữ nguyên văn là có chủ ý: `VI_TRAILING_SPACE` sẽ báo lỗi khoảng trắng thừa
    và người dùng phải nhìn thấy đúng thứ bị báo, chứ không phải một bản đã được
    dọn ngầm rồi tự hỏi vì sao máy báo lỗi ở một dòng trông rất sạch.
    """
    from srtgen.core.srt import parse_srt

    if not str(vi_text or "").strip():
        return []
    return [" ".join(block.lines) for block in parse_srt(vi_text)]


def _stage_flags(video_id: str, cue_count: int) -> dict[int, list[str]]:
    """Cờ “AI đã sửa / đa âm tự / tên riêng” của từng cue, lấy từ file chặng.

    File `.srt` không mang được cờ, và `.bundle.json` cũng không (hợp đồng với
    backend chỉ có `kind`/`text`/`pinyin`). Chúng chỉ còn ở `work/<id>/S*.json`.
    Chỉ dùng khi số cue khớp: lệch một cue nghĩa là file đã được sửa từ lần chạy
    trước, và bôi vàng nhầm dòng còn tệ hơn không bôi.
    """
    if not VIDEO_ID_RE.match(str(video_id or "")):
        return {}
    base = work_dir_for(video_id)
    for number, name in _STAGE_DOCS:
        doc = _stage_document(_read_json_quiet(base / f"S{number}_{name}.json"))
        if doc is None or len(doc.cues) != cue_count:
            continue
        out: dict[int, list[str]] = {}
        for position, cue in enumerate(doc.cues, start=1):
            flags = sorted({flag for t in cue.tokens for flag in t.flags})
            if flags:
                out[position] = flags
        return out
    return {}


def _stage_document(payload: Any) -> Any:
    """Dựng `Document` từ nội dung một file chặng, chấp nhận cả lớp bọc ngoài."""
    from srtgen.core.token import Document

    if not isinstance(payload, dict):
        return None
    if "cues" not in payload:
        for key in ("document", "doc", "result", "data"):
            inner = payload.get(key)
            if isinstance(inner, dict):
                return _stage_document(inner)
        return None
    try:
        return Document.from_stage_dict(payload)
    except (TypeError, ValueError, AttributeError):
        return None


# --------------------------------------------------------------------------- #
# lưu bản đã sửa
# --------------------------------------------------------------------------- #

@dataclass
class _EditRow:
    """Một dòng phụ đề người dùng vừa sửa, đã dọn sạch nhưng chưa kiểm luật."""

    start: float
    end: float
    zh: str
    py: str
    vi: str

    def is_empty(self) -> bool:
        return not self.zh and not self.py


def _write_format(job: Job, emit_cfg: Mapping[str, Any]) -> tuple[bool, str]:
    """`(bom, ký tự xuống dòng)` để Lưu / Hoàn nguyên ghi file — đúng như chặng xuất file đã ghi.

    Chặng xuất file nhận cấu hình qua `_config_for_job`: `default.yaml` → Cài đặt →
    lựa chọn của lần chạy. Trước đây Lưu và Hoàn nguyên chỉ đọc `default.yaml`, nên
    ai đã tắt BOM hoặc chọn CRLF trong Cài đặt thì thấy file đổi định dạng ngay lần
    Lưu đầu (BOM quay lại, thứ HUONG-DAN nói là do Cài đặt quyết định), và “Hoàn
    nguyên về bản máy tạo” ghi ra một file khác từng byte với bản máy tạo thật.
    Thứ tự ưu tiên ở đây phải khớp với `_config_for_job`.
    """
    options = dict(job.options or {})
    settings = load_settings()
    bom = _tri_state(options.get("bom"), settings.get("bom"))
    if bom is None:
        bom = bool(emit_cfg.get("bom", True))
    choice = str(options.get("newline") or settings.get("newline") or "").strip().lower()
    if choice not in ("lf", "crlf"):
        choice = str(emit_cfg.get("newline") or "lf").strip().lower()
    return bom, ("\r\n" if choice == "crlf" else "\n")


def save_editor_document(
    job: Job,
    rows: Sequence[Any],
    *,
    profile: str | None = None,
) -> dict[str, Any]:
    """Ghi lại bản người dùng vừa sửa: `.srt`, `_vi.srt`, `.bundle.json`, hai file `.ass`, rồi kiểm lại.

    Ba quyết định đáng ghi lại vì chúng đi ngược trực giác:

    1. **File `.srt` được dựng thẳng từ chữ người dùng gõ**, không đi vòng qua
       token rồi render lại. Đi vòng sẽ “sửa hộ” dòng pinyin ở đúng những cue
       đang lệch số cụm — tức là xoá mất chỗ người dùng vào đây để sửa.
    2. **Đánh số lại từ 1 và bỏ cue rỗng**, đúng luật của `emit_srt`, nên hai
       file luôn cùng số block và cùng mốc thời gian. Đây là ràng buộc cứng của
       build-spec-v2 mục 2, và giữ nó bằng cấu tạo rẻ hơn nhiều so với đi kiểm.
    3. **Dựng lại `.ass` và `_song-ngu.ass`** bằng đúng hai hàm của chặng xuất
       file (`s9_emit.build_ass`, `s9_emit.build_bilingual_ass`). HUONG-DAN dặn
       người dùng soát bằng `_song-ngu.ass` trong Aegisub, sửa trong app rồi bấm
       Lưu. Nếu Lưu chỉ ghi `.srt` thì lần mở Aegisub sau họ soát lại đúng bản cũ,
       tưởng chỗ vừa sửa đã mất — hoặc tệ hơn, tin vào bản cũ. Luật khi nào ghi
       nằm ở `_rewrite_ass_files`.
    """
    files = editor_files(job)
    edits = [_edit_row(item) for item in rows]
    if not any(not row.is_empty() for row in edits):
        raise JobError(
            "Không có dòng phụ đề nào để lưu. Bản sửa đang trống, "
            "nên tool không ghi đè file cũ."
        )

    cfg = load_config(profile) if profile else _safe_cfg()
    emit_cfg = cfg.get("emit") if isinstance(cfg.get("emit"), dict) else {}
    bom, newline = _write_format(job, emit_cfg)

    zh_text = _render_zh_srt(edits)
    write_vi = files.vi is not None and (
        files.vi.is_file() or any(row.vi.strip() for row in edits)
    )
    vi_text = _render_vi_srt(edits) if write_vi else ""

    _snapshot_original(files)
    io_utils.atomic_write_text(files.srt, zh_text, bom=bom, newline=newline)
    notes: list[str] = []
    if write_vi and files.vi is not None:
        io_utils.atomic_write_text(files.vi, vi_text, bom=bom, newline=newline)
    elif files.vi is None:
        notes.append(
            "Chưa ghi được file tiếng Việt vì nó nằm ngoài thư mục kết quả cho phép."
        )

    note = _rewrite_bundle(files, zh_text, vi_text)
    if note:
        notes.append(note)

    ass_paths, ass_notes = _rewrite_ass_files(
        job,
        files,
        edits,
        emit_cfg=emit_cfg,
        newline=newline,
        # Tạo mới `_song-ngu.ass` chỉ cho file do chính tool xuất ra, và chỉ khi
        # vừa có `_vi.srt` — đúng điều kiện chặng xuất file dùng.
        create_bilingual=write_vi and job.mode != MODE_LOCAL,
    )
    notes.extend(ass_notes)

    payload = _attach_job(_doc_payload(files, zh_text, vi_text, notes=notes), job)
    _forget_draft(files.video_id)
    _apply_to_job(job, files, payload, write_vi, extra=ass_paths)
    # Chụp lại công việc SAU khi đã cập nhật kết quả: bản chụp trong `_attach_job`
    # còn là kết quả cũ, nên nút tải file soát vừa dựng lại sẽ không hiện ra.
    payload["job"] = job.public_dict()
    payload["saved"] = True
    _mark_saved(payload, job, files)
    payload["message"] = _save_message(payload, write_vi)
    if payload["origin"] == ORIGIN_CONTENT:
        payload["message"] += (
            f" Bản đã lưu nằm trong thư mục của tool ({payload['saved_to']}); file gốc "
            "trên máy bạn không bị đổi — hãy tải về để dùng bản mới."
        )
    return payload


def _apply_to_job(
    job: Job,
    files: EditorFiles,
    payload: dict[str, Any],
    wrote_vi: bool,
    *,
    extra: Mapping[str, str] | None = None,
) -> None:
    """Cập nhật lại kết quả của công việc sau khi người dùng sửa tay.

    Không làm việc này thì màn hình kết quả vẫn hiện số lỗi của bản máy tạo, và
    người dùng vừa sửa hết lỗi xong lại thấy báo đỏ y như cũ. `extra` là đường
    dẫn các file vừa dựng lại (`ass`, `bilingual_ass`), để nút tải về có chúng.
    """
    result = dict(job.result or {})
    result["srt"] = str(files.srt)
    if wrote_vi and files.vi is not None:
        result["vi_srt"] = str(files.vi)
    result.update(extra or {})
    result["has_errors"] = bool(payload["summary"].get("error"))
    result["edited_at"] = time.time()
    job.set_result(result, payload["findings"], payload["summary"])


def _save_message(payload: dict[str, Any], wrote_vi: bool) -> str:
    errors = int(payload["summary"].get("error", 0) or 0)
    warns = int(payload["summary"].get("warn", 0) or 0)
    what = "hai file phụ đề" if wrote_vi else "file phụ đề"
    if errors:
        return f"Đã lưu {what}. Còn {errors} lỗi cần sửa và {warns} chỗ nên xem lại."
    if warns:
        return f"Đã lưu {what}, không còn lỗi nào; có {warns} chỗ nên xem lại."
    return f"Đã lưu {what}, không còn lỗi nào."


def _edit_row(item: Any) -> _EditRow:
    """Đọc một dòng từ trình duyệt gửi lên, chấp nhận cả hai cách ghi thời gian.

    Thiếu hẳn mốc thời gian thì **từ chối cả lần lưu**, không âm thầm lấy 0. Một
    bản gửi lên thiếu trường thời gian là dấu hiệu của lỗi phía giao diện, và
    “sửa hộ” thành 0 sẽ dồn cả phim về giây thứ không — hỏng theo kiểu chỉ phát
    hiện ra khi đã quá muộn.
    """
    if not isinstance(item, dict):
        raise JobError("Dữ liệu dòng phụ đề gửi lên không đúng dạng. Hãy tải lại trang rồi thử lại.")
    keys = set(item)
    if not (keys & {"start", "start_text"}) or not (keys & {"end", "end_text"}):
        raise JobError(
            "Có dòng phụ đề gửi lên thiếu mốc thời gian nên tool không lưu gì cả, "
            "để không làm hỏng file đang có. Hãy tải lại trang rồi sửa lại."
        )
    start = _seconds_of(item.get("start"), item.get("start_text"))
    end = _seconds_of(item.get("end"), item.get("end_text"))
    return _EditRow(
        start=start,
        end=end,
        zh=_clean_line(item.get("zh_line")),
        py=_clean_line(item.get("py_line")),
        vi=_clean_line(item.get("vi_line")),
    )


_STAMP_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[,.](\d{1,3}))?$")


def _seconds_of(value: Any, text: Any = None) -> float:
    """Số giây của một mốc thời gian, nhận cả `12.34` lẫn `"00:00:12,340"`."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        return 0.0 if number != number or number < 0 else number
    for raw in (value, text):
        candidate = str(raw or "").strip()
        if not candidate:
            continue
        match = _STAMP_RE.match(candidate)
        if match is not None:
            hours, minutes, seconds, millis = match.groups()
            return (
                int(hours or 0) * 3600
                + int(minutes) * 60
                + int(seconds)
                + int((millis or "0").ljust(3, "0")) / 1000.0
            )
        try:
            number = float(candidate)
        except ValueError:
            continue
        return 0.0 if number != number or number < 0 else number
    return 0.0


def _clean_line(value: Any) -> str:
    """Một dòng an toàn để ghi vào file `.srt`.

    Ký tự xuống dòng bị đổi thành khoảng trắng chứ không bị xoá: một dòng trống
    lọt vào giữa block sẽ cắt đôi block đó trong Aegisub và làm lệch mọi cue phía
    sau (build-spec mục 11). Khoảng trắng hai đầu bị cắt vì quy chuẩn cấm hẳn nó
    — đây là chỗ tool sửa hộ, không phải chỗ báo lỗi cho người dùng tự sửa.
    """
    text = io_utils.nfc(str(value if value is not None else ""))
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = "".join(ch for ch in text if ch >= " " or ch == " ")
    return text.strip()


def _render_zh_srt(rows: Sequence[_EditRow]) -> str:
    """Dựng nội dung `.srt` bốn dòng, đánh số lại từ 1, bỏ cue rỗng."""
    from srtgen.core.srt import format_timestamp

    parts: list[str] = []
    number = 0
    for row in rows:
        if row.is_empty():
            continue
        number += 1
        parts.append(
            "\n".join(
                (
                    str(number),
                    f"{format_timestamp(row.start)} --> {format_timestamp(row.end)}",
                    row.zh,
                    row.py,
                )
            )
        )
        parts.append("\n\n")
    return "".join(parts)


def _render_vi_srt(rows: Sequence[_EditRow]) -> str:
    """Dựng nội dung `_vi.srt` ba dòng, cùng số block và cùng mốc thời gian.

    Dòng tiếng Việt đi qua `s9_emit.vi_line`, tức là **cùng một hàm** mà chặng
    xuất file dùng: dấu câu ASCII, không khoảng trắng thừa, marker đổi người nói
    khớp dòng Hán, và không bao giờ để trống (trống thì block đứt).
    """
    from srtgen.core.srt import format_timestamp
    from srtgen.stages.s9_emit import vi_line

    parts: list[str] = []
    number = 0
    for row in rows:
        if row.is_empty():
            continue
        number += 1
        parts.append(
            "\n".join(
                (
                    str(number),
                    f"{format_timestamp(row.start)} --> {format_timestamp(row.end)}",
                    vi_line(row.vi, row.zh),
                )
            )
        )
        parts.append("\n\n")
    return "".join(parts)


def _rewrite_bundle(files: EditorFiles, zh_text: str, vi_text: str) -> str:
    """Ghi lại `.bundle.json` cho khớp bản vừa sửa; trả về câu cảnh báo nếu hỏng.

    Chỉ ghi đè khi file đã có sẵn: trình sửa không tự sinh thêm file mà người
    dùng chưa từng có. Hỏng ở đây **không** làm hỏng lần lưu — hai file `.srt`
    mới là thứ người dùng giao đi, `bundle` chỉ để phần mềm khác đọc.
    """
    if files.bundle is None or not files.bundle.is_file():
        return ""
    try:
        from srtgen.core.srt import parse_srt_document
        from srtgen.stages.s9_emit import build_bundle

        old = _read_json_quiet(files.bundle)
        meta = dict(old.get("meta") or {}) if isinstance(old, dict) else {}
        doc = parse_srt_document(zh_text)
        payload = build_bundle(doc, meta=meta, vi_lines=_vi_lines_of(vi_text) or None)
        io_utils.write_json(files.bundle, payload)
    except Exception:  # noqa: BLE001 - xem docstring: không được làm hỏng lần lưu
        return (
            "Đã lưu hai file phụ đề, nhưng chưa cập nhật được file dữ liệu "
            f"“{files.bundle.name}”. File phụ đề vẫn dùng được bình thường."
        )
    return ""


def _rewrite_ass_files(
    job: Job,
    files: EditorFiles,
    rows: Sequence[_EditRow],
    *,
    emit_cfg: Mapping[str, Any],
    newline: str,
    create_bilingual: bool,
    backups: list[str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Dựng lại `<tên>.ass` và `<tên>_song-ngu.ass` cho khớp bản vừa lưu.

    Trả `({loại: đường dẫn đã ghi}, [câu báo tiếng Việt])`.

    `backups=None` là nút Lưu: người dùng chủ động ghi đè bản của chính họ, không
    cất gì. Truyền một danh sách (nút Hoàn nguyên) thì bản cũ khác nội dung được cất
    qua `_write_result_file` trước khi ghi, và đường dẫn bản cất được thêm vào đó —
    `.ass` người dùng sửa trong Aegisub cũng là công sức của họ.

    Khi nào ghi:

    * file **đã có** trên đĩa → luôn dựng lại. File soát cũ hơn file phụ đề là thứ
      nguy hiểm nhất, vì nó trông hoàn toàn bình thường.
    * `_song-ngu.ass` **chưa có** → chỉ tạo khi `create_bilingual` (chỗ gọi đặt
      cho file do chính tool xuất ra, vừa ghi `_vi.srt`) và `emit.bilingual_ass`
      đang bật — đúng luật của chặng xuất file. File mở từ máy người dùng thì
      không: trình sửa không đẻ thêm file vào thư mục của họ, cùng lý do với
      `.bundle.json`.
    * `.ass` một ngôn ngữ chưa có → không tạo (cấu hình mặc định là tắt).

    Hỏng ở bước này (file đang mở trong Aegisub trên Windows, ổ đầy…) **không**
    làm hỏng lần lưu: file `.srt` đã ghi xong, `.ass` chỉ để soát. Nhưng phải nói
    ra kèm cách xử lý — im lặng thì người dùng lại soát trên bản cũ.
    """
    kept = [row for row in rows if not row.is_empty()]
    targets: list[tuple[str, Path]] = []
    if files.ass is not None and files.ass.is_file():
        targets.append(("ass", files.ass))
    if files.bilingual_ass is not None and (
        files.bilingual_ass.is_file()
        or (create_bilingual and bool(emit_cfg.get("bilingual_ass", True)))
    ):
        targets.append(("bilingual_ass", files.bilingual_ass))
    if not targets or not kept:
        return {}, []

    from srtgen.stages.s9_emit import build_ass, build_bilingual_ass

    doc = _ass_document(kept)
    title = str(job.title or "").strip() or files.srt.stem
    style = dict(emit_cfg)
    written: dict[str, str] = {}
    failed: list[str] = []
    for kind, path in targets:
        try:
            if kind == "ass":
                body = build_ass(doc, title=title, cfg=style)
            else:
                body = build_bilingual_ass(doc, [row.vi for row in kept], title=title, cfg=style)
            if backups is None:
                io_utils.atomic_write_text(path, body, bom=True, newline=newline)
            else:
                _write_result_file(path, body, bom=True, newline=newline, backups=backups)
        except Exception:  # noqa: BLE001 - xem docstring: không được làm hỏng lần lưu
            _discard_temp(path)
            failed.append(path.name)
            continue
        written[kind] = str(path)

    notes: list[str] = []
    if written:
        shown = ", ".join(f"“{Path(p).name}”" for p in written.values())
        notes.append(
            f"Đã dựng lại {shown} theo bản vừa lưu. Nếu Aegisub đang mở file này, hãy "
            "mở lại nó (File > Open Subtitles…) để thấy bản mới."
        )
    if failed:
        shown = ", ".join(f"“{name}”" for name in failed)
        notes.append(
            f"Chưa cập nhật được {shown}, có thể vì file đang mở trong Aegisub hoặc phần "
            "mềm khác. File phụ đề .srt vẫn đã lưu bình thường. Hãy đóng Aegisub rồi bấm "
            "Lưu lại để file soát khớp với bản mới."
        )
    return written, notes


def _discard_temp(path: Path) -> None:
    """Xoá file tạm `<tên>.tmp` mà `atomic_write_text` để lại khi bước thay file thất bại."""
    try:
        path.with_name(path.name + ".tmp").unlink(missing_ok=True)
    except OSError:
        pass


def _write_result_file(
    path: Path,
    text: str,
    *,
    bom: bool,
    newline: str,
    backups: list[str],
) -> None:
    """Ghi một file kết quả mà người dùng KHÔNG bấm Lưu để ghi — cất bản cũ trước (H1).

    `/api/fix` ghi `<tên>.da-chuan-hoa.srt`, nút Hoàn nguyên ghi bản máy tạo đè lên
    file đang có: cả hai đều có thể đè đúng bản người dùng đã sửa tay từ lần trước.
    Bản cũ khác nội dung sắp ghi được đổi tên thành `<tên>.truoc-<thời điểm><đuôi>`
    qua `io_utils.keep_old_copy` (cùng MỘT hàm với chặng xuất file và CLI), và
    đường dẫn bản cất được thêm vào `backups`. Không cất được thì
    `io_utils.KeepCopyError` bay lên và file cũ KHÔNG bị ghi đè.

    Nút Lưu của trình sửa KHÔNG đi qua đây: đó là người dùng chủ động ghi đè bản
    của chính họ, cất thì mỗi lần bấm Lưu lại đẻ thêm một file.

    Ghi hỏng sau khi đã dời bản cũ đi thì trả bản cũ về đúng tên cũ, để người dùng
    mở thư mục ra vẫn thấy file của mình như trước.
    """
    kept = io_utils.keep_old_copy(path, io_utils.encode_text(text, bom=bom, newline=newline))
    try:
        io_utils.atomic_write_text(path, text, bom=bom, newline=newline)
    except BaseException:
        _discard_temp(path)
        if kept is not None and not path.exists():
            try:
                kept.rename(path)
                kept = None
            except OSError:
                pass
        if kept is not None:
            backups.append(str(kept))
        raise
    if kept is not None:
        backups.append(str(kept))


def _ass_document(rows: Sequence[_EditRow]) -> Any:
    """`Document` cho hai hàm dựng `.ass` của S9, mang ĐÚNG chữ vừa ghi vào `.srt`.

    Mỗi cue là một token duy nhất chứa nguyên văn dòng Hán và dòng pinyin; render
    lại cho ra đúng từng ký tự đó, nên `.ass` hiện đúng thứ `.srt` chứa — cũng là
    điều chặng xuất file bảo đảm, vì ở đó cả hai file render từ cùng một token
    list. Không tách lại thành token thật (`parse_srt_document`): ở cue đang lệch
    số cụm, bộ ghép trả pinyin rỗng và `.ass` sẽ hiện chữ Hán ở dòng pinyin — tức
    Aegisub cho người dùng soát một thứ không có trong file của họ.
    """
    from srtgen.core.token import KIND_WORD, Cue, Document, Token

    cues = [
        Cue(
            index=number,
            start=row.start,
            end=row.end,
            tokens=[Token(kind=KIND_WORD, zh=row.zh, pinyin=row.py or None, source="manual")],
        )
        for number, row in enumerate(rows, start=1)
    ]
    return Document(cues=cues, meta={"segmented": True})


def _rows_from_texts(zh_text: str, vi_text: str) -> list[_EditRow]:
    """Các dòng sửa dựng từ nội dung hai file — cho lúc hoàn nguyên, khi không có bảng từ trình duyệt."""
    from srtgen.core.srt import parse_srt

    vi_lines = _vi_lines_of(vi_text)
    rows: list[_EditRow] = []
    for position, block in enumerate(parse_srt(zh_text)):
        lines = list(block.lines) + ["", ""]
        rows.append(
            _EditRow(
                start=float(block.start or 0.0),
                end=float(block.end or 0.0),
                zh=lines[0],
                py=lines[1],
                vi=vi_lines[position] if position < len(vi_lines) else "",
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# bản gốc do máy tạo + bản nháp tự lưu
# --------------------------------------------------------------------------- #

def _snapshot_original(files: EditorFiles) -> None:
    """Chụp lại bản đang có **trước lần ghi đè đầu tiên**.

    Chỉ chụp một lần. Chụp lại ở lần lưu thứ hai thì nút “Hoàn nguyên về bản máy
    tạo” sẽ quay về bản người dùng đã sửa dở — tức là không hoàn nguyên gì cả.
    """
    if not files.video_id:
        return
    path = original_file(files.video_id)
    if path.is_file():
        return
    try:
        payload = {
            "version": 1,
            "saved_at": time.time(),
            "srt_path": str(files.srt),
            "srt": _read_srt_file(files.srt) if files.srt.is_file() else "",
            "vi_path": str(files.vi) if files.vi else "",
            "vi": _read_srt_file(files.vi) if (files.vi and files.vi.is_file()) else "",
        }
        io_utils.ensure_dir(path.parent)
        io_utils.write_json(path, payload)
    except (OSError, ValueError, UnicodeError):
        # Không chụp được thì thôi: mất nút hoàn nguyên còn hơn mất lần lưu.
        pass


def revert_editor_document(job: Job) -> dict[str, Any]:
    """Ghi lại bản do máy tạo, xoá bản nháp, trả về bảng cue mới."""
    files = editor_files(job)
    payload = _read_json_quiet(original_file(files.video_id)) if files.video_id else None
    if not isinstance(payload, dict) or not str(payload.get("srt") or "").strip():
        raise JobError(
            "Chưa có bản do máy tạo để quay về: bạn chưa lưu lần sửa nào cho file này."
        )

    cfg = _safe_cfg()
    emit_cfg = cfg.get("emit") if isinstance(cfg.get("emit"), dict) else {}
    bom, newline = _write_format(job, emit_cfg)

    zh_text = str(payload.get("srt") or "")
    vi_text = str(payload.get("vi") or "")
    # Hoàn nguyên KHÔNG phải nút Lưu: nó ghi bản máy tạo đè lên bản đang có, mà bản
    # đang có chính là phần người dùng đã sửa tay. Có hỏi xác nhận, nhưng một cú
    # bấm nhầm không được xoá cả buổi công — nên bản đang có được cất thành
    # `<tên>.truoc-…` trước (H1), y như khi chạy lại một phim.
    backups: list[str] = []
    _write_result_file(files.srt, zh_text, bom=bom, newline=newline, backups=backups)
    notes: list[str] = []
    if vi_text.strip() and files.vi is not None:
        _write_result_file(files.vi, vi_text, bom=bom, newline=newline, backups=backups)
    elif files.vi is not None and files.vi.is_file():
        # Bản gốc chưa từng có file tiếng Việt, còn bây giờ thì có — tức là chính
        # người dùng đã dịch tay. Xoá hộ là xoá mất công của họ, nên chỉ nói ra.
        notes.append(
            f"File tiếng Việt “{files.vi.name}” được giữ nguyên vì bản do máy tạo "
            "không có file đó. Hãy kiểm lại xem nó còn khớp với bản vừa hoàn nguyên không."
        )

    # Đọc lại bản tiếng Việt từ đĩa chứ không dùng chuỗi vừa ghi: hai thứ chỉ khác
    # nhau ở đúng trường hợp trên (file người dùng tự dịch), và bảng hiện ra phải
    # là thứ đang thật sự nằm trong file.
    has_vi = files.vi is not None and files.vi.is_file()
    vi_now = _read_srt_file(files.vi) if (has_vi and files.vi is not None) else ""
    # File soát trong Aegisub phải quay về theo, nếu không nó giữ bản đã sửa tay
    # trong khi `.srt` đã là bản máy tạo. Chỉ dựng lại file đang có, không tạo mới.
    ass_paths, ass_notes = _rewrite_ass_files(
        job,
        files,
        _rows_from_texts(zh_text, vi_now),
        emit_cfg=emit_cfg,
        newline=newline,
        create_bilingual=False,
        backups=backups,
    )
    notes.extend(ass_notes)

    _forget_draft(files.video_id)
    data = _attach_job(_doc_payload(files, zh_text, vi_now, notes=notes), job)
    _apply_to_job(job, files, data, has_vi, extra=ass_paths)
    data["job"] = job.public_dict()
    data["reverted"] = True
    data["backups"] = backups
    _mark_saved(data, job, files)
    if backups:
        shown = ", ".join(f"“{Path(p).name}”" for p in backups)
        data["message"] = (
            "Đã quay về bản do máy tạo. Bản đang có trước đó (gồm cả phần bạn đã sửa "
            f"tay) được cất lại thành {shown} trong cùng thư mục, không mất gì."
        )
    else:
        data["message"] = "Đã quay về bản do máy tạo. Mọi chỉnh sửa tay đã bị bỏ."
    return data


def draft_video_id(job: Job) -> str:
    """Mã video để đặt bản nháp, kể cả với công việc mở từ một file trên máy."""
    video_id = str(job.video_id or "").strip()
    if VIDEO_ID_RE.match(video_id):
        return video_id
    raw = str((job.result or {}).get("srt") or "").strip()
    if raw:
        guess = make_video_id(raw)
        if VIDEO_ID_RE.match(guess):
            return guess
    raise JobError(
        "Công việc này chưa có chỗ để lưu bản nháp. Hãy mở lại file phụ đề rồi thử lại."
    )


def read_draft(job: Job) -> dict[str, Any]:
    """Bản nháp đang có: `{exists, saved_at, rev, doc}` theo hợp đồng với giao diện.

    `doc` và `draft` là **cùng một thứ**, trả về dưới hai cái tên: `doc` là tên
    trong hợp đồng, `draft` là tên bản cũ đang dùng. Trả cả hai để hai bên không
    phải đổi tên cùng một lúc mới chạy được.

    `rev` là số lần sửa mà trình sửa đếm được lúc ghi nháp. Giao diện cần nó để
    biết bản nháp trên đĩa mới hơn hay cũ hơn bảng đang mở, trước khi hỏi người
    dùng có muốn khôi phục không.
    """
    video_id = draft_video_id(job)
    path = draft_file(video_id)
    data = _read_json_quiet(path)
    if not isinstance(data, dict):
        return {
            "video_id": video_id,
            "path": str(path),
            "exists": False,
            "draft": None,
            "doc": None,
            "rev": None,
            "saved_at": None,
        }
    draft = data.get("draft")
    # Mốc sửa của file phụ đề trên đĩa. Giao diện so nó với `saved_at` để cảnh
    # báo "file đã được ghi lại sau lúc bạn sửa dở" khi bản nháp không mang vân
    # tay bản gốc (`base_hash`) — tức mọi bản nháp ghi bởi bản chương trình cũ.
    try:
        srt_mtime: float | None = editor_files(job).srt.stat().st_mtime
    except (JobError, OSError):
        srt_mtime = None
    return {
        "video_id": video_id,
        "path": str(path),
        "exists": draft is not None,
        "draft": draft,
        "doc": draft,
        "rev": _draft_rev(draft, data),
        "saved_at": data.get("saved_at"),
        "srt_mtime": srt_mtime,
    }


def _draft_rev(draft: Any, wrapper: dict[str, Any]) -> int | None:
    """Số hiệu bản sửa của một bản nháp, `None` khi bản nháp không mang số nào."""
    for source in (draft, wrapper):
        if isinstance(source, dict) and source.get("rev") is not None:
            try:
                return int(source["rev"])
            except (TypeError, ValueError):
                continue
    return None


def write_draft(job: Job, draft: Any) -> dict[str, Any]:
    """Ghi bản nháp tự lưu; `draft=None` nghĩa là xoá nháp.

    Nháp là thứ ghi mỗi năm giây nên nó phải **rẻ và không bao giờ chặn**: không
    kiểm luật, không đụng tới file `.srt`, chỉ đổ nguyên trạng thái bảng xuống
    một file JSON trong thư mục làm việc của video.
    """
    video_id = draft_video_id(job)
    path = draft_file(video_id)
    if draft is None:
        _forget_draft(video_id)
        return {"video_id": video_id, "path": str(path), "saved": False, "cleared": True}

    payload = {
        "version": 1,
        "job_id": job.id,
        "saved_at": time.time(),
        "rev": _draft_rev(draft, {}),
        "srt_path": str((job.result or {}).get("srt") or ""),
        "draft": draft,
    }
    body = json.dumps(payload, ensure_ascii=False)
    if len(body.encode("utf-8")) > MAX_DRAFT_BYTES:
        raise JobError(
            f"Bản nháp lớn hơn mức cho phép ({MAX_DRAFT_BYTES // (1024 * 1024)}MB) "
            "nên chưa tự lưu được. Hãy bấm Lưu để ghi thẳng ra file phụ đề."
        )
    io_utils.ensure_dir(path.parent)
    io_utils.write_json(path, payload)
    return {
        "video_id": video_id,
        "path": str(path),
        "saved": True,
        "exists": True,
        "rev": payload["rev"],
        "saved_at": payload["saved_at"],
    }


def _forget_draft(video_id: str) -> None:
    """Xoá bản nháp sau khi đã lưu thật — nháp cũ hơn file là nháp gây hiểu nhầm."""
    if not video_id:
        return
    try:
        draft_file(video_id).unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# tách lại cụm cho một câu
# --------------------------------------------------------------------------- #

def retokenize_text(
    zh: str,
    *,
    video_id: str = "",
    profile: str | None = None,
    capitalize: bool = True,
) -> dict[str, Any]:
    """Chạy S5 + S6 cho **một câu** và trả về cách tách cụm kèm pinyin.

    Đây là nút “Sinh lại pinyin cho câu này” của trình sửa. Nó cố ý **không** tự
    chạy khi người dùng gõ: chạy tự động sẽ đè lên phần pinyin họ vừa sửa tay,
    mà sửa tay chính là lý do trình sửa tồn tại.

    `capitalize=False` cho những cue nằm giữa câu: S6 luôn viết hoa cue đầu tài
    liệu, và ở đây tài liệu chỉ có đúng một câu nên mặc định của nó là viết hoa.

    Tách cụm bằng `s5_tokenize.segment_tokens` — bộ tách dùng chung của chặng S5
    (hợp đồng H5, `segment_line` là dạng chuỗi của nó) — rồi mới chạy phần sinh
    pinyin/chuẩn hoá của S5 + S6 trên các cụm đó.
    """
    from srtgen.core.token import Cue, Document, render_py, render_zh
    from srtgen.stages.s5_tokenize import (
        USERDICT_FILE,
        load_names as load_name_table,
        segment_tokens,
        tokenize_document,
    )
    from srtgen.stages.s6_normalize import normalize_document

    text = _clean_line(zh)
    if not text:
        raise JobError("Chưa có dòng chữ Hán nào để tách cụm.")

    cfg = load_config(profile) if profile else _safe_cfg()
    table_payload: dict[str, Any] = {}
    names: dict[str, str] = {}
    userdict: Path | None = None
    names_problem = ""
    if video_id and VIDEO_ID_RE.match(video_id):
        table = names_file(video_id, cfg)
        # Bảng tên đọc qua kho an toàn (H4): bảng hỏng thì tách như chưa có bảng —
        # y như chặng S5 — và câu cảnh báo đi kèm kết quả thay vì bị nuốt mất.
        loaded = load_names_file(table)
        table_payload = loaded.data
        names_problem = loaded.message or ""
        # Bảng cụm {光头: …, 强: …} cho bước sinh pinyin: đúng bộ đọc mà
        # `s5_tokenize.run` dùng, nên pinyin và chữ hoa của tên ra y như chặng S5.
        names = {} if loaded.corrupt else load_name_table(table)
        userdict = work_dir_for(video_id, cfg) / USERDICT_FILE

    # ĐÚNG bộ tách của chặng S5 (hợp đồng H5): cùng từ điển người dùng (kể cả tên
    # nguyên khối 光头强 → 光头 | 强), cùng công tắc `tokenize.userdict`, cùng
    # `tokenize.jieba_hmm`. Trước đây nút này tự cắt bằng một bản sao riêng của bộ
    # tách — và hai bản sao của một bộ tách sớm muộn cũng tách một câu hai kiểu.
    tokens = segment_tokens(text, table_payload or None, cfg=cfg, userdict_path=userdict)
    # `segmented=True` báo cho S5 rằng ranh giới cụm đã có chủ, và chỉ phần pinyin
    # còn thiếu mới phải sinh — đúng con đường mà một file đã được biên tập đi qua.
    doc = Document(
        cues=[Cue(index=1, start=0.0, end=1.0, tokens=tokens)],
        meta={"segmented": True},
    )
    tokenize_document(doc, cfg, names=names, userdict_path=userdict)
    normalize_document(doc, cfg, names=names)

    tokens = list(doc.cues[0].tokens)
    if not capitalize:
        _lowercase_first(tokens)
    return {
        "zh_line": render_zh(tokens),
        "py_line": render_py(tokens),
        "tokens": [_token_payload(t) for t in tokens],
        "clusters": sum(1 for t in tokens if t.is_word()),
        "names": len(names),
        **({"names_warning": names_problem} if names_problem else {}),
    }


def _lowercase_first(tokens: Sequence[Any]) -> None:
    """Trả chữ cái đầu của dòng pinyin về chữ thường, giữ nguyên dấu thanh."""
    for token in tokens:
        if not token.is_word() or not token.pinyin:
            continue
        token.pinyin = token.pinyin[0].lower() + token.pinyin[1:]
        return


# --------------------------------------------------------------------------- #
# mở cặp file có sẵn trên máy
# --------------------------------------------------------------------------- #

def open_local_pair(
    manager: JobManager,
    path: str,
    vi_path: str = "",
) -> tuple[Job, dict[str, Any]]:
    """Mở một cặp `.srt` / `_vi.srt` có sẵn thẳng vào trình sửa.

    Không chạy pipeline, không cần model, không cần mạng — đây là đường dùng
    được ngay cho file bên dịch gửi sang.

    Về an toàn: đường dẫn ở đây **không** bị bó vào thư mục kết quả, vì file bên
    dịch gửi sang nằm trong Thư mục tải về chứ không nằm trong thư mục của tool.
    Thứ chặn lạm dụng là lớp kiểm `Host`/`Origin` ở giữa (một trang web khác
    không gọi được vào đây), cộng ba ràng buộc hẹp ngay dưới: phải là file thật,
    phải đuôi `.srt`, phải nhỏ hơn hạn mức tải lên. Thư mục chứa file được ghi
    vào `job.out_dir`, nên quyền đọc/ghi mở thêm **chỉ áp cho đúng công việc này**.
    """
    srt = _local_srt_path(path)
    if vi_path:
        vi = _local_srt_path(vi_path, beside=srt)
    else:
        vi = _guess_vi_file(srt)
    return _open_pair(manager, srt, vi, video_id=_local_video_id(srt), origin=ORIGIN_PATH)


def _open_pair(
    manager: JobManager,
    srt: Path,
    vi: Path | None,
    *,
    video_id: str,
    origin: str = ORIGIN_PATH,
) -> tuple[Job, dict[str, Any]]:
    """Mở một cặp file đã kiểm đường dẫn vào trình sửa — dùng chung cho cả hai cách mở.

    `origin` (`path`/`content`) được ghi vào kết quả của công việc để trình sửa
    biết bản lưu có nằm ở chỗ file gốc hay không (xem `editor_origin`).

    `video_id` do chỗ gọi quyết định: mở theo đường dẫn thì suy từ đường dẫn (mở
    lại ngày mai vẫn về đúng bản nháp cũ); mở theo nội dung thì là tên thư mục
    làm việc vừa tạo, để bản nháp và bản gốc nằm cạnh chính file đó.
    """
    zh_text = _read_srt_file(srt)
    vi_text = _read_srt_file(vi) if vi is not None else ""
    files = EditorFiles(
        srt=srt,
        vi=vi if vi is not None else srt.with_name(f"{srt.stem}_vi.srt"),
        bundle=_existing(srt.with_name(f"{srt.stem}.bundle.json")),
        video_id=video_id,
    )
    payload = _doc_payload(files, zh_text, vi_text)
    if payload["cue_count"] == 0:
        raise JobError(
            f"“{srt.name}” không có dòng phụ đề nào đọc được. Có thể đây không phải "
            "file .srt, hoặc file đã hỏng. Hãy thử mở nó bằng tab “Kiểm tra file”."
        )

    job = manager.add_finished(
        source=str(srt),
        mode=MODE_LOCAL,
        message=_open_message(payload, vi is not None),
        result={
            "srt": str(srt),
            "vi_srt": str(vi) if vi is not None else None,
            "bundle": str(files.bundle) if files.bundle else None,
            "has_errors": bool(payload["summary"].get("error")),
            "origin": origin,
        },
        findings=payload["findings"],
        summary=payload["summary"],
        out_dir=str(srt.parent),
        video_id=files.video_id,
        log=[
            f"Đã mở “{srt.name}” ({payload['cue_count']} dòng phụ đề) vào trình sửa."
            + (f" Kèm bản tiếng Việt “{vi.name}”." if vi is not None else "")
        ],
    )
    return job, _attach_job(payload, job)


def _local_video_id(srt: Path) -> str:
    guess = make_video_id(str(srt))
    return guess if VIDEO_ID_RE.match(guess) else ""


def _existing(path: Path) -> Path | None:
    return path if path.is_file() else None


def _local_srt_path(raw: str, *, beside: Path | None = None) -> Path:
    """Kiểm một đường dẫn người dùng chọn, hoặc nói rõ vì sao không mở được."""
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise JobError("Bạn chưa chọn file .srt nào để mở.")
    try:
        path = Path(text).expanduser().resolve()
    except (OSError, ValueError) as err:
        raise JobError(f"Đường dẫn “{text}” không hợp lệ.") from err
    if not path.is_file():
        raise JobError(
            f"Không tìm thấy file “{text}” trên máy. Hãy kiểm tra lại đường dẫn, "
            "hoặc kéo thả file vào cửa sổ tool."
        )
    if path.suffix.lower() != ".srt":
        raise JobError(
            f"“{path.name}” không phải file .srt. Trình sửa chỉ mở được file phụ đề .srt."
        )
    try:
        if path.stat().st_size > MAX_UPLOAD_BYTES:
            raise JobError(_TOO_BIG)
    except OSError as err:
        raise DetailedError(
            f"Không đọc được file “{path.name}”. Có thể file đang bị khoá quyền đọc, "
            "hoặc ổ đĩa chứa nó vừa bị rút ra.",
            detail=f"{type(err).__name__}: {err}",
        ) from err
    if beside is not None and path.parent != beside.parent:
        raise JobError(
            "File tiếng Việt phải nằm cùng thư mục với file tiếng Trung. "
            "Hãy chép nó về cùng chỗ rồi thử lại."
        )
    return path


def _guess_vi_file(srt: Path) -> Path | None:
    """Tìm bản tiếng Việt đi kèm theo đúng thói quen đặt tên của người dùng."""
    for name in (f"{srt.stem}_vi.srt", f"{srt.stem}.vi.srt", f"{srt.stem}_vn.srt"):
        candidate = srt.with_name(name)
        if candidate.is_file():
            return candidate
    return None


class ContentTooLarge(JobError):
    """Nội dung gửi lên vượt trần — tầng HTTP trả 413 thay vì 400."""


def _content_text(value: Any, what: str, *, required: bool) -> str:
    """Kiểm một nội dung `.srt` gửi thẳng từ trình duyệt; trả chuỗi đã dọn xuống dòng."""
    if value is None or (not required and isinstance(value, str) and not value.strip()):
        if required:
            raise JobError(f"Chưa nhận được nội dung file {what} nào để mở.")
        return ""
    if not isinstance(value, str):
        raise JobError(
            f"Nội dung file {what} gửi lên không đúng dạng. Hãy tải lại trang rồi thử lại."
        )
    if len(value.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise ContentTooLarge(_TOO_BIG)
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("﻿"):
        text = text[1:]
    if required and not text.strip():
        raise JobError(f"File {what} bạn chọn đang trống, không có dòng phụ đề nào để mở.")
    return text


def _ascii_slug(text: str, *, max_len: int = 40) -> str:
    """Tên thư mục an toàn trên cả macOS lẫn Windows: bỏ dấu, chỉ giữ `[A-Za-z0-9_-]`."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    plain = plain.replace("đ", "d").replace("Đ", "D")
    slug = re.sub(r"[^A-Za-z0-9-]+", "_", plain)
    slug = re.sub(r"_{2,}", "_", slug).strip("_-")
    return slug[:max_len].strip("_-")


def open_local_content(
    manager: JobManager,
    name: str,
    content: Any,
    vi_content: Any = None,
) -> tuple[Job, dict[str, Any]]:
    """Mở vào trình sửa một file `.srt` mà trình duyệt chỉ gửi **nội dung**, không có đường dẫn.

    Người dùng chọn file bằng hộp thoại hoặc kéo thả thì trình duyệt không bao
    giờ cho biết file nằm ở đâu — chỉ có tên và nội dung. Trình sửa lại cần một
    file thật để ghi đè khi bấm Lưu, nên nội dung được chép vào **một thư mục làm
    việc mới** trong `work/` (mỗi lần mở một thư mục, không đè lên lần trước) rồi
    mở như một cặp file thường. File gốc của người dùng không bị đụng tới; bản
    đã sửa lấy về bằng nút tải file.

    `name` chỉ để đặt tên hiển thị, qua `safe_stem` — không bao giờ thành đường dẫn.
    """
    zh_text = _content_text(content, "tiếng Trung", required=True)
    vi_text = _content_text(vi_content, "tiếng Việt", required=False)

    stem = _srt_stem(name)
    folder = work_root() / f"{_ascii_slug(stem) or 'phu-de'}_{uuid.uuid4().hex[:8]}"
    srt = folder / f"{stem}.srt"
    vi = folder / f"{stem}_vi.srt" if vi_text else None
    try:
        io_utils.ensure_dir(folder)
        io_utils.write_text(srt, zh_text, bom=True)
        if vi is not None:
            io_utils.write_text(vi, vi_text, bom=True)
        return _open_pair(manager, srt, vi, video_id=folder.name, origin=ORIGIN_CONTENT)
    except BaseException:
        # Thư mục vừa tạo, chỉ chứa đúng những file vừa ghi: mở hỏng thì dọn đi,
        # để tab "Tên riêng" không liệt kê một "phim" không mở được.
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _open_message(payload: dict[str, Any], has_vi: bool) -> str:
    errors = int(payload["summary"].get("error", 0) or 0)
    count = payload["cue_count"]
    pair = " kèm bản tiếng Việt" if has_vi else ""
    if errors:
        return f"Đã mở {count} dòng phụ đề{pair}. Tìm thấy {errors} lỗi cần sửa."
    return f"Đã mở {count} dòng phụ đề{pair}, không có lỗi định dạng nào."


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #

def doctor_payload() -> dict[str, Any]:
    """Gọi `srtgen.cli.doctor_report()` và bọc kết quả về một hình dạng ổn định.

    `cli.py` do người khác viết và có thể chưa có mặt lúc này; thiếu nó thì màn
    hình "Kiểm tra máy" phải báo bằng tiếng Việt chứ không được trả về lỗi 500.
    """
    try:
        from srtgen import cli
    except Exception as err:
        return {
            "available": False,
            "message": "Chưa dùng được chức năng kiểm tra máy trên bản cài này.",
            "detail": f"{type(err).__name__}: {err}",
            "checks": [],
        }

    report = getattr(cli, "doctor_report", None)
    if not callable(report):
        return {
            "available": False,
            "message": "Chưa dùng được chức năng kiểm tra máy trên bản cài này.",
            "detail": "srtgen.cli.doctor_report chưa có.",
            "checks": [],
        }

    try:
        data = report()
    except Exception as err:
        return {
            "available": False,
            "message": "Không chạy được phần kiểm tra máy.",
            "detail": f"{type(err).__name__}: {err}",
            "checks": [],
        }

    if isinstance(data, dict):
        out = dict(data)
        out.setdefault("available", True)
        out["checks"] = _with_fix_actions(out.get("checks") or [])
        return out
    if isinstance(data, (list, tuple)):
        return {"available": True, "checks": _with_fix_actions(data)}
    return {"available": True, "checks": [], "text": str(data)}


#: Mục "Kiểm tra máy" → việc sửa được nó (khoá trong `FIX_ACTIONS` của app.js).
#: `cli.Check` chỉ mang `fix` là một **dòng lệnh** để dán vào Terminal, còn giao
#: diện dựng nút "… giúp tôi" từ `fix_action`. Thiếu bảng nối này thì nút sửa
#: trong kết quả Kiểm tra máy không bao giờ hiện ra, dù việc tương ứng chạy được.
_DOCTOR_FIX_ACTIONS: dict[str, str] = {
    "yt-dlp": "update_ytdlp",
    "ffmpeg": "install_ffmpeg",
    "ffprobe": "install_ffmpeg",
    "model": "download_model",
    "uv": "install_uv",
}


def _with_fix_actions(checks: Sequence[Any]) -> list[Any]:
    """Gắn `fix_action` cho mục chưa đạt, và `hint` (= dòng lệnh `fix`) cho mọi mục."""
    out: list[Any] = []
    for raw in checks:
        if not isinstance(raw, dict):
            out.append(raw)
            continue
        item = dict(raw)
        if str(item.get("fix") or "").strip():
            item.setdefault("hint", item["fix"])
        if str(item.get("status") or "").lower() in ("warn", "fail") and not item.get("fix_action"):
            action = _DOCTOR_FIX_ACTIONS.get(str(item.get("key") or ""))
            if action:
                item["fix_action"] = action
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# đường dẫn an toàn + mở thư mục
# --------------------------------------------------------------------------- #

def _allowed_roots(job: Job | None = None) -> list[Path]:
    """Những thư mục mà máy chủ được phép đọc/mở file bên trong.

    Danh sách trắng chứ không phải danh sách đen: thêm một thư mục vào đây là một
    quyết định có ý thức, còn quên chặn một dạng đường dẫn lạ thì không.
    """
    cfg = _safe_cfg()
    roots: list[Path] = []
    for candidate in (
        # Thư mục kết quả của chính công việc này. Với một cặp file mở từ máy
        # (`/api/open-local`) đây chính là thư mục chứa file đó — nên trình sửa
        # ghi lại được vào đúng chỗ cũ mà không mở rộng quyền cho công việc khác.
        job.out_dir if job else "",
        str(user_data_dir()),
        # `work/` mặc định nằm trong thư mục dữ liệu người dùng, nhưng khi cấu
        # hình trỏ nó đi nơi khác thì file âm thanh của S1 cũng đi theo.
        str(work_root(cfg)),
        _effective_out_dir(cfg, load_settings()),
    ):
        text = str(candidate or "").strip()
        if not text:
            continue
        try:
            roots.append(Path(text).expanduser().resolve())
        except OSError:
            continue
    return roots


def _safe_cfg() -> dict[str, Any]:
    try:
        return load_config()
    except Exception:
        return {}


def _safe_path(raw: str, roots: Sequence[Path]) -> Path | None:
    """Trả về đường dẫn tuyệt đối nếu nó nằm trong một thư mục cho phép, không thì `None`.

    `resolve()` trước khi so là điểm mấu chốt: nó khử `..`, khử liên kết tượng
    trưng và khử đường dẫn tương đối, nên `out/../../.ssh/id_rsa` không lọt qua.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        target = Path(text).expanduser().resolve()
    except (OSError, ValueError):
        return None
    for root in roots:
        try:
            if target == root or target.is_relative_to(root):
                return target
        except (OSError, ValueError):
            continue
    return None


def reveal_in_file_manager(path: Path) -> None:
    """Mở thư mục chứa `path` bằng trình quản lý file của hệ điều hành.

    Luôn truyền tham số dạng danh sách, không bao giờ ghép chuỗi shell: đường dẫn
    kết quả có dấu tiếng Việt, khoảng trắng và cả chữ Hán trong tên phim.
    """
    target = path if path.is_dir() else path.parent
    if sys.platform == "darwin":
        args = ["open", "-R", str(path)] if path.is_file() else ["open", str(target)]
    elif os.name == "nt":
        # explorer muốn "/select,<đường dẫn>" dính liền thành MỘT tham số, và trả
        # về mã thoát khác 0 ngay cả khi mở thành công — nên đừng kiểm mã thoát.
        args = ["explorer", f"/select,{path}"] if path.is_file() else ["explorer", str(target)]
    else:
        args = ["xdg-open", str(target)]
    subprocess.Popen(args, close_fds=True)  # noqa: S603 - tham số dạng list, không qua shell


def open_document(path: Path) -> None:
    """Mở một file bằng ứng dụng mặc định của hệ điều hành.

    Khác `reveal_in_file_manager`: cái kia *chỉ ra chỗ* file nằm, cái này *mở nó
    ra đọc*. Nút "Mở hướng dẫn sử dụng" cần cái sau — chỉ ra chỗ file HUONG-DAN.md
    rồi để người dùng tự bấm đúp là thêm một bước không cần thiết.

    Đường dẫn ở đây **luôn** do chính máy chủ tính ra (xem `_guide_file`), không
    bao giờ đến từ tham số của trình duyệt.
    """
    if os.name == "nt":
        os.startfile(str(path))  # noqa: S606 - đường dẫn do máy chủ tự tính
        return
    args = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(args, close_fds=True)  # noqa: S603 - tham số dạng list, không qua shell


# --------------------------------------------------------------------------- #
# nút hành động — POST /api/actions/{key}
# --------------------------------------------------------------------------- #
#
# ĐÂY LÀ CHỖ DỄ THÀNH LỖ HỔNG THỰC THI LỆNH NHẤT TRONG CẢ DỰ ÁN, nên ba luật
# dưới đây không được nới, kể cả khi thêm một nút mới:
#
# 1. **Danh sách trắng cứng.** `key` từ trình duyệt chỉ dùng để *tra bảng*
#    `_ACTION_RUNNERS`; nó không bao giờ trở thành tên lệnh, tên file hay tham số.
#    Khoá lạ bị từ chối bằng một câu tiếng Việt, không phải bằng một lần thử chạy.
# 2. **Không `shell=True`, không `eval`, không ghép chuỗi lệnh.** Mọi lời gọi hệ
#    thống truyền tham số dạng danh sách.
# 3. **Không nhận đường dẫn từ client.** Mọi thư mục đích đều do máy chủ tự tính
#    từ cấu hình.

#: Đúng năm việc, khớp với bảng `ACTIONS` trong `static/app.js`.
ACTION_KEYS: tuple[str, ...] = (
    "install_ffmpeg",
    "update_ytdlp",
    "download_model",
    "fetch_preview",
    "open_output_dir",
    "open_guide",
)

#: Ba việc chạy vài phút: trả `{job_id}` và đẩy tiến trình qua SSE như một công
#: việc thường. Bấm xong mà màn hình im lặng là kiểu hỏng tệ nhất với người dùng
#: không phải dân IT — họ bấm lại nhiều lần rồi kết luận tool hỏng.
LONG_ACTIONS = frozenset({"install_ffmpeg", "update_ytdlp", "download_model", "fetch_preview"})

#: Tên khác của cùng một việc. `install_ytdlp` và `update_ytdlp` chạy đúng một
#: lệnh (`pip install -U yt-dlp`) — cài mới hay nâng cấp đều là nó — nên chấp
#: nhận cả hai tên thay vì bắt người dùng nhìn câu từ chối vì một chữ.
_ACTION_ALIASES: dict[str, str] = {"install_ytdlp": "update_ytdlp"}

#: Câu hiện trên thanh tiến trình lúc việc bắt đầu.
_ACTION_TITLES: dict[str, str] = {
    "install_ffmpeg": "Đang cài ffmpeg",
    "update_ytdlp": "Đang cập nhật yt-dlp",
    "download_model": "Đang tải model gỡ băng",
    "fetch_preview": "Đang tải video để xem trước",
    "open_output_dir": "Đang mở thư mục kết quả",
    "open_guide": "Đang mở hướng dẫn sử dụng",
}

#: Địa chỉ bản ffmpeg/ffprobe tĩnh cho macOS — đúng nguồn mà `CaiDat.command`
#: bước 5 dùng, để hai đường cài ra cùng một thứ.
_FFMPEG_URLS: dict[str, str] = {
    "ffmpeg": "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip",
    "ffprobe": "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip",
}

#: Trần dung lượng một file tải về trong lúc cài ffmpeg. Bản nén của evermeet
#: chừng 20-40MB; 400MB là rộng gấp mười lần, và vẫn đủ chặt để một địa chỉ trả
#: về thứ khác không làm đầy ổ đĩa người dùng.
_MAX_DOWNLOAD_BYTES = 400 * 1024 * 1024

#: Thời gian chờ nhiều nhất cho một lần tải/cài.
_ACTION_TIMEOUT = 900.0

#: `Job.mode` của một việc trong danh sách trắng. Cố ý khác mọi mode của
#: `jobs.py`: giao diện lọc theo nó để **không** đem một lần "cài ffmpeg" lên
#: màn hình tiến trình 10 bước của pipeline.
MODE_ACTION = "action"


def action_key(raw: str) -> str:
    """Khoá thật của một việc, hoặc chuỗi rỗng nếu nó không nằm trong danh sách trắng."""
    name = str(raw or "").strip().lower()
    name = _ACTION_ALIASES.get(name, name)
    return name if name in ACTION_KEYS else ""


def unknown_action_message(raw: str) -> str:
    """Câu tiếng Việt từ chối một khoá lạ, có kể ra tool làm được những gì."""
    return (
        f"Tool không hiểu việc “{str(raw or '').strip()[:40]}”. "
        "Chương trình chỉ tự làm được năm việc: cài ffmpeg, cập nhật yt-dlp, "
        "tải model gỡ băng, mở thư mục kết quả và mở hướng dẫn sử dụng."
    )


def action_params(name: str, body: Any) -> dict[str, Any]:
    """Tham số đi kèm một việc — chỉ nhận giá trị có trong danh sách biết trước.

    Hiện chỉ `download_model` có tham số: model mà người dùng vừa bấm "Tải về"
    (hoặc đang chọn). Không nhận thì máy chủ tải model trong **Cài đặt đã lưu**,
    tức là bấm "Tải về" cạnh large-v3 lại được báo "model small đã có sẵn" —
    hoặc tệ hơn, tải 3GB của một model khác. Giá trị lạ bị bỏ qua (quay về model
    đã lưu), không bao giờ đi thẳng vào một đường dẫn hay một lệnh.
    """
    if not isinstance(body, dict):
        return {}
    if name == "fetch_preview":
        # Chỉ nhận MÃ phim (chữ, số, gạch): máy chủ tự tra link trong S0_info.json
        # của phim đó, trình duyệt không bao giờ được chỉ định địa chỉ tải.
        vid = str(body.get("video_id") or "").strip()
        return {"video_id": vid} if VIDEO_ID_RE.match(vid) else {}
    if name != "download_model":
        return {}
    wanted = str(body.get("model") or "").strip()
    if not wanted:
        return {}
    cfg = _safe_cfg()
    asr_cfg = cfg.get("asr") if isinstance(cfg.get("asr"), dict) else {}
    table = asr_cfg.get("models") if isinstance(asr_cfg.get("models"), list) else []
    known = {str(m.get("id") or "").strip() for m in table if isinstance(m, dict)}
    return {"model": wanted} if wanted in known else {}


def run_action(
    key: str,
    *,
    report: Callable[[str, float], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Chạy một việc trong danh sách trắng; trả `{message, detail, …}`.

    Tách khỏi tầng HTTP để chạy được cả từ luồng thợ (việc dài) lẫn từ chính
    yêu cầu (việc ngắn), và để kiểm thử được mà không cần dựng máy chủ.
    """
    name = action_key(key)
    if not name:
        raise JobError(unknown_action_message(key))
    say = report or (lambda message, fraction: None)
    stop = cancelled or (lambda: False)
    return _ACTION_RUNNERS[name](say, stop, **dict(params or {}))


#: Khoá cho đoạn "việc này đang chạy chưa → chưa thì đăng ký". Hai yêu cầu tới
#: cùng lúc (bấm đúp, hai tab) mà không có khoá thì cả hai cùng thấy "chưa".
_ACTION_START_LOCK = threading.Lock()


def start_action_job(
    manager: JobManager, key: str, params: dict[str, Any] | None = None
) -> Job:
    """Như `_start_action_job_locked`, nhưng mỗi việc chỉ chạy **một** bản một lúc.

    Bấm đúp, hay mở hai tab rồi cùng bấm, trước đây sinh ra hai-ba luồng chạy
    song song cùng một việc: hai `pip install -U yt-dlp` vào cùng một môi trường
    có thể gỡ bản của nhau giữa chừng, hai lần cài ffmpeg cùng ghi đè một file.
    Việc đó đang chạy thì trả lại đúng công việc ấy để giao diện theo dõi tiếp.

    Không sợ bám vào một công việc đã chết: `JobManager._load_from_disk` đổi mọi
    việc còn "đang chạy" của phiên trước thành lỗi ngay lúc nạp.
    """
    name = action_key(key)
    if not name:
        raise JobError(unknown_action_message(key))
    title = _ACTION_TITLES.get(name, "Đang chạy")
    with _ACTION_START_LOCK:
        for item in manager.list(limit=10_000):
            if item.get("mode") != MODE_ACTION or not item.get("active"):
                continue
            running = manager.get(str(item.get("id") or ""))
            if running is not None and running.source == title and running.is_active():
                return running
        return _start_action_job_locked(manager, name, params)


def _start_action_job_locked(
    manager: JobManager, key: str, params: dict[str, Any] | None = None
) -> Job:
    """Nhận một việc dài vào hàng đợi hiển thị và chạy nó ở luồng riêng.

    Vì sao dùng lại `Job` chứ không tự dựng một cơ chế tiến trình thứ hai: giao
    diện đã có sẵn màn hình thanh chạy + nhật ký + nút Dừng, và nó chỉ biết đọc
    `/api/jobs/{id}/events`. Một cơ chế thứ hai nghĩa là hai chỗ để hỏng.

    Việc này chạy ở luồng **riêng** chứ không xếp vào hàng đợi pipeline: người
    dùng bấm "Cài ffmpeg giúp tôi" đúng lúc đang cần chạy tiếp, bắt họ chờ hết
    một video 40 phút mới cài được là vô lý.
    """
    name = action_key(key)
    if not name:
        raise JobError(unknown_action_message(key))

    title = _ACTION_TITLES.get(name, "Đang chạy")
    job = manager.add_finished(
        source=title,
        mode=MODE_ACTION,
        message=f"{title}…",
        log=[f"{title}…"],
    )
    # `add_finished` sinh ra một công việc ĐÃ XONG (nó vốn dành cho việc chạy
    # trong một nhịp). Kéo nó về trạng thái đang chạy trước khi trả job_id cho
    # trình duyệt, nếu không tab vừa mở đã thấy "Đã xong" và đóng luôn luồng SSE.
    job.progress = 0.0
    job.step_progress = 0.0
    job.step = 0
    job.finished_at = None
    job.result = None
    # `mode` và `title` là hai thứ giao diện dùng để **không** nhầm việc này với
    # một lần chạy pipeline: một việc như "cài ffmpeg" không có "Bước 3/10" nào.
    job.title = title
    job.mark_running()
    job.message = title

    def worker() -> None:
        try:
            result = run_action(
                name,
                report=lambda message, fraction: _action_progress(job, message, fraction),
                cancelled=job.is_cancelled,
                params=params,
            )
        except JobError as err:
            job.fail(str(err), fix_action=str(getattr(err, "fix_action", "") or ""),
                     detail=str(getattr(err, "detail", "") or ""))
            return
        except Exception as err:  # noqa: BLE001 - luồng nền: lỗi lạ vẫn phải hiện ra
            job.fail(
                "Việc này không chạy xong được. Bạn có thể thử lại, hoặc chạy lại "
                "file CaiDat.command trong thư mục cài đặt.",
                detail=f"{type(err).__name__}: {err}",
            )
            return
        if job.is_cancelled():
            job.mark_cancelled()
            return
        job.finish(
            result={"action": name, "detail": str(result.get("detail") or "")},
            message=str(result.get("message") or "Đã xong."),
        )

    threading.Thread(target=worker, name=f"srtgen-action-{name}", daemon=True).start()
    return job


def _action_progress(job: Job, message: str, fraction: float) -> None:
    """Đẩy tiến trình của một việc lên thanh chạy của `Job`.

    `Job.progress` bình thường được tính từ trọng số 10 chặng của pipeline, mà
    một việc như "cài ffmpeg" thì không có chặng nào. Đặt thẳng `progress` trước
    khi gọi `update()` là cách giữ đúng con số: `update()` chỉ lấy giá trị lớn
    hơn, nên nó không kéo tụt con số vừa đặt.
    """
    try:
        value = min(1.0, max(0.0, float(fraction)))
    except (TypeError, ValueError):
        value = 0.0
    job.progress = value
    job.step_progress = value
    job.update(job.step, str(message or ""), value)


# -- từng việc một ---------------------------------------------------------- #

def _bin_dir() -> Path:
    """`~/Library/Application Support/SrtGen/bin` — đúng chỗ `CaiDat.command` để."""
    return user_data_dir() / "bin"


def _act_install_ffmpeg(
    report: Callable[[str, float], None], cancelled: Callable[[], bool]
) -> dict[str, Any]:
    """Tải ffmpeg + ffprobe bản tĩnh về thư mục của chính tool (bước 5 của bộ cài).

    Không cài vào hệ thống, không cần mật khẩu quản trị, không đụng tới phần còn
    lại của máy — đó là cả lý do bộ cài chọn đường này thay vì Homebrew.
    """
    if sys.platform != "darwin":
        raise JobError(
            "Nút tự cài ffmpeg chỉ dùng được trên máy Mac. Trên máy này, hãy cài "
            "ffmpeg theo cách của hệ điều hành rồi mở lại tool."
        )

    target_dir = _bin_dir()
    io_utils.ensure_dir(target_dir)
    done: list[str] = []
    for position, (name, url) in enumerate(_FFMPEG_URLS.items()):
        if cancelled():
            raise JobError("Bạn đã bấm Dừng nên tool không cài tiếp.")
        base = position / len(_FFMPEG_URLS)
        span = 1.0 / len(_FFMPEG_URLS)
        report(f"Đang tải {name}…", base)
        blob = _download_bytes(
            url,
            on_progress=lambda got, total: report(
                f"Đang tải {name}… {_size_text(got)}"
                + (f" / {_size_text(total)}" if total else ""),
                base + span * (0.8 * (got / total) if total else 0.4),
            ),
            cancelled=cancelled,
        )
        report(f"Đang giải nén {name}…", base + span * 0.85)
        _extract_binary(blob, name, target_dir / name)
        done.append(str(target_dir / name))

    report("Đã cài xong ffmpeg.", 1.0)
    return {
        "message": (
            "Đã cài xong ffmpeg vào thư mục riêng của tool. "
            "Máy đọc được file video rồi, bạn thử chạy lại video vừa nãy."
        ),
        "detail": "\n".join(done),
        "paths": done,
    }


def _ytdlp_update_command(python: str) -> tuple[list[str], dict[str, str]]:
    """Câu lệnh cập nhật yt-dlp VÀO ĐÚNG môi trường đang chạy, kèm biến môi trường.

    Ưu tiên `uv pip install --python <python-này>`. Lý do: bộ cài
    `CaiDat.command` dựng môi trường riêng bằng `uv venv`, và môi trường dựng
    kiểu đó **không có pip**. Câu lệnh cũ `python -m pip install -U yt-dlp` vì
    thế hỏng 100% trên máy người dùng thật — đúng cái máy cần nút này nhất, vì
    YouTube đổi cách chặn thì chỉ bản yt-dlp mới tải được.

    Không tìm thấy uv (máy của người phát triển, chạy trong venv thường) thì mới
    rơi về pip.
    """
    from srtgen.cli import _find_uv

    env = dict(os.environ)
    uv = _find_uv()
    if uv:
        # Dùng đúng kho gói riêng của SrtGen nếu có, để gỡ cài đặt còn dọn sạch.
        cache = Path.home() / "Library" / "Application Support" / "SrtGen" / "uv-cache"
        if cache.is_dir():
            env.setdefault("UV_CACHE_DIR", str(cache))
        return [uv, "pip", "install", "--python", python, "--upgrade", "yt-dlp"], env
    return [python, "-m", "pip", "install", "-U", "--disable-pip-version-check", "yt-dlp"], env


def _act_update_ytdlp(
    report: Callable[[str, float], None], cancelled: Callable[[], bool]
) -> dict[str, Any]:
    """`<venv>/bin/python -m pip install -U yt-dlp` trong chính môi trường đang chạy.

    Dùng `sys.executable` chứ không dò tìm một Python nào khác: đó **là** trình
    thông dịch đang chạy máy chủ này, nên gói cài vào đó chắc chắn là gói mà
    chặng S0 sẽ import. Dò tìm thì có ngày cài vào một venv không ai dùng và
    người dùng thấy "đã cập nhật" mà YouTube vẫn tải hỏng.
    """
    python = str(sys.executable or "").strip()
    if not python:
        raise JobError(
            "Tool không tìm thấy trình Python của chính nó nên chưa tự cập nhật "
            "yt-dlp được. Hãy chạy lại file CaiDat.command trong thư mục cài đặt."
        )

    report("Đang gọi trình cài gói…", 0.05)
    args, env = _ytdlp_update_command(python)
    lines: list[str] = []
    try:
        process = subprocess.Popen(  # noqa: S603 - tham số dạng list, không qua shell
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            close_fds=True,
            env=env,
        )
    except OSError as err:
        raise ActionError(
            "Không gọi được trình cài gói của Python nên chưa cập nhật yt-dlp được. "
            "Hãy chạy lại file CaiDat.command trong thư mục cài đặt.",
            detail=f"{type(err).__name__}: {err}",
        ) from err
    deadline = time.monotonic() + _ACTION_TIMEOUT
    fraction = 0.05
    for line in process.stdout or ():
        lines.append(line.rstrip())
        del lines[:-60]
        fraction = min(0.9, fraction + 0.02)
        # Cố ý KHÔNG in nguyên văn dòng của pip: nó là tiếng Anh và đầy thuật ngữ.
        report("Đang tải và cài yt-dlp…", fraction)
        if cancelled() or time.monotonic() > deadline:
            process.kill()
            raise JobError(
                "Bạn đã bấm Dừng nên tool không cập nhật tiếp."
                if cancelled()
                else "Việc cập nhật yt-dlp chạy quá lâu nên tool đã dừng lại. "
                "Hãy kiểm tra kết nối mạng rồi thử lại."
            )
    code = process.wait()
    detail = "\n".join(lines)
    if code != 0:
        raise ActionError(
            "Không cập nhật được yt-dlp. Thường là do mất mạng hoặc mạng chặn. "
            "Hãy kiểm tra kết nối Internet rồi thử lại.",
            detail=detail,
        )

    report("Đã cập nhật xong yt-dlp.", 1.0)
    return {
        "message": "Đã cập nhật xong yt-dlp. Bạn dán lại link và bấm Bắt đầu.",
        "detail": detail,
    }


def _act_download_model(
    report: Callable[[str, float], None], cancelled: Callable[[], bool], model: str = ""
) -> dict[str, Any]:
    """Tải sẵn model gỡ băng đang chọn, có thanh tiến trình.

    Cùng một model, cùng một thư mục cache với chặng S2 — nút này chỉ *dời* việc
    tải ra khỏi lần chạy đầu tiên, để người dùng không ngồi nhìn "Bước 3/10" đứng
    im 20 phút mà không hiểu máy đang tải hay đang treo.

    Đứt mạng giữa chừng không mất công: `huggingface_hub` giữ phần đã tải trong
    cache và lần sau nối tiếp.
    """
    from srtgen.stages.s2_asr import model_status

    cfg = _safe_cfg()
    settings = load_settings()
    asr_cfg = cfg.get("asr") if isinstance(cfg.get("asr"), dict) else {}
    model = str(
        model or settings.get("model") or asr_cfg.get("model") or "large-v3-turbo"
    ).strip()

    before = model_status(model, cfg)
    label = str(before.get("label") or "") or model
    if before.get("downloaded"):
        report(f"Model “{label}” đã có sẵn trên máy.", 1.0)
        return {
            "message": f"Model “{label}” đã có sẵn trên máy, không phải tải lại.",
            "detail": str(before.get("path") or ""),
        }

    try:
        import faster_whisper
    except ImportError as err:
        raise JobError(
            "Máy chưa cài phần nghe-hiểu giọng nói (faster-whisper) nên chưa tải "
            "model được. Hãy chạy lại file CaiDat.command trong thư mục cài đặt."
        ) from err

    downloader = getattr(faster_whisper, "download_model", None)
    if downloader is None:
        raise JobError(
            "Bản thư viện nghe-hiểu trên máy này chưa cho tải model riêng. "
            "Cứ bấm Bắt đầu như bình thường, tool sẽ tự tải trong lúc chạy."
        )

    root_raw = str(asr_cfg.get("download_root") or "").strip()
    root = Path(root_raw).expanduser() if root_raw else user_cache_dir() / "models"
    io_utils.ensure_dir(root)

    total = int(_num(before.get("size_mb"))) * 1024 * 1024
    baseline = _dir_bytes(root)
    stop_poll = threading.Event()

    def poll() -> None:
        while not stop_poll.wait(1.5):
            got = max(0, _dir_bytes(root) - baseline)
            fraction = min(0.97, got / total) if total else 0.5
            report(
                f"Đang tải model “{label}”… {_size_text(got)}"
                + (f" / {_size_text(total)}" if total else ""),
                fraction,
            )

    report(
        f"Bắt đầu tải model “{label}”"
        + (f" (khoảng {_size_text(total)})" if total else "")
        + ". Chỉ tải một lần, những lần sau dùng lại ngay.",
        0.01,
    )
    watcher = threading.Thread(target=poll, name="srtgen-model-progress", daemon=True)
    watcher.start()
    try:
        downloader(model, cache_dir=str(root), local_files_only=False)
    except Exception as err:  # noqa: BLE001 - mọi lỗi mạng đều phải thành câu tiếng Việt
        raise ActionError(
            f"Chưa tải xong model “{label}”. Thường là do mất mạng hoặc mạng chặn. "
            "Phần đã tải vẫn được giữ lại, lần sau tool tải tiếp chứ không tải lại từ đầu.",
            detail=f"{type(err).__name__}: {err}",
        ) from err
    finally:
        stop_poll.set()
        watcher.join(timeout=2.0)

    after = model_status(model, cfg)
    if not after.get("downloaded"):
        raise JobError(
            f"Model “{label}” tải về nhưng chưa dùng được ({after.get('problem') or 'chưa rõ'}). "
            "Hãy kiểm tra ổ đĩa còn trống rồi thử lại, hoặc vào Cài đặt chọn model nhẹ hơn."
        )
    report(f"Đã tải xong model “{label}”.", 1.0)
    return {
        "message": (
            f"Đã tải xong model “{label}”. Model nằm sẵn trên máy rồi, "
            "lần chạy tới sẽ vào việc ngay."
        ),
        "detail": str(after.get("path") or ""),
    }


def _act_open_output_dir(
    report: Callable[[str, float], None], cancelled: Callable[[], bool]
) -> dict[str, Any]:
    """Mở thư mục kết quả bằng trình quản lý file của hệ điều hành."""
    target = Path(_effective_out_dir(_safe_cfg(), load_settings())).expanduser()
    try:
        io_utils.ensure_dir(target)
    except OSError as err:
        raise JobError(
            f"Không mở được thư mục kết quả “{target}”. Hãy vào tab Cài đặt chọn "
            "một thư mục khác mà bạn có quyền ghi."
        ) from err
    try:
        reveal_in_file_manager(target)
    except OSError as err:
        raise JobError(
            "Không mở được cửa sổ thư mục. Bạn có thể mở tay theo đường dẫn ở phần chi tiết."
        ) from err
    return {"message": f"Đã mở thư mục kết quả: {target}", "detail": str(target)}


def _act_open_guide(
    report: Callable[[str, float], None], cancelled: Callable[[], bool]
) -> dict[str, Any]:
    """Mở file HUONG-DAN.md bằng ứng dụng mặc định; trả kèm `url` của trang `/guide`.

    Máy không có ứng dụng đọc `.md` thì hệ điều hành mở… không gì cả, mà tool
    vẫn báo “Đã mở”. Giao diện nên mở thẳng `/guide` (H7) ở tab mới; `url` ở đây
    là để một giao diện cũ gọi hành động này vẫn có đường mở trang đọc được.
    """
    guide = _guide_file()
    if guide is None:
        raise JobError(
            "Không tìm thấy file hướng dẫn HUONG-DAN.md trên máy. Nó nằm cùng chỗ "
            "với file CaiDat.command mà bạn đã bấm đúp lúc cài đặt."
        )
    try:
        open_document(guide)
    except OSError as err:
        raise JobError(
            f"Không mở được file hướng dẫn. Bạn có thể mở tay file này: {guide}"
        ) from err
    return {"message": "Đã mở file hướng dẫn sử dụng.", "detail": str(guide), "url": GUIDE_URL}


#: Bảng tra duy nhất. `key` của client chỉ được dùng để tìm trong bảng này.
def _act_fetch_preview(
    report: Callable[[str, float], None], cancelled: Callable[[], bool], video_id: str = "", **_: Any
) -> dict[str, Any]:
    """Tải bản có hình cho một phim đã chạy xong mà mới chỉ có tiếng.

    Link lấy từ `S0_info.json` trên máy chủ chứ không nhận từ trình duyệt: nút này
    chỉ được tải lại đúng video của phim đó, không phải bất kỳ địa chỉ nào.
    """
    from srtgen.stages.s0_fetch import fetch_preview_video

    vid = str(video_id or "").strip()
    if not VIDEO_ID_RE.match(vid):
        raise JobError("Chưa biết phim nào cần tải video. Hãy mở lại phim từ tab Sửa phụ đề rồi bấm lại.")
    base = work_dir_for(vid)
    info_path = base / "S0_info.json"
    data = _read_json_quiet(info_path)
    if not isinstance(data, dict):
        raise JobError("Không thấy dữ liệu của phim này trong thư mục làm việc, nên không biết tải video từ đâu.")
    if data.get("is_local"):
        raise JobError("Phim này chạy từ file trên máy nên không có gì để tải thêm. Muốn xem trước có hình, hãy chạy lại bằng file video (.mp4).")
    url = str(data.get("source_url") or data.get("source") or "").strip()
    if not url:
        raise JobError("Không thấy link video của phim này để tải lại.")
    existing = str(data.get("video_path") or "")
    if existing and Path(existing).is_file():
        report("Video đã có sẵn.", 1.0)
        return {"message": "Video xem trước đã có sẵn trên máy.", "detail": existing, "video_path": existing}
    report("Đang chuẩn bị tải video…", 0.02)
    path = fetch_preview_video(base, url, _safe_cfg(), report, cancelled=cancelled)
    data["video_path"] = str(path)
    try:
        io_utils.write_json(info_path, data)
    except OSError as err:
        raise JobError("Tải xong video nhưng không ghi được thông tin vào thư mục làm việc.", detail=str(err)) from err
    return {
        "message": "Đã tải video để xem trước. Mở lại phim trong tab Sửa phụ đề để xem.",
        "detail": str(path),
        "video_path": str(path),
    }


_ACTION_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "install_ffmpeg": _act_install_ffmpeg,
    "update_ytdlp": _act_update_ytdlp,
    "download_model": _act_download_model,
    "fetch_preview": _act_fetch_preview,
    "open_output_dir": _act_open_output_dir,
    "open_guide": _act_open_guide,
}


def _guide_file() -> Path | None:
    """Đi tìm `HUONG-DAN.md` ở những chỗ nó thật sự có thể nằm.

    Ba chỗ, theo thứ tự hay gặp: thư mục mã nguồn (chạy từ repo), thư mục dữ liệu
    của tool, và thư mục cha của môi trường Python (bộ cài đặt venv trong
    `~/Library/Application Support/SrtGen/venv`).
    """
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "HUONG-DAN.md",
        user_data_dir() / "HUONG-DAN.md",
        Path(sys.prefix).parent / "HUONG-DAN.md",
        Path(sys.prefix).parent / "installer" / "HUONG-DAN.md",
    ]
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


# --------------------------------------------------------------------------- #
# trang hướng dẫn sử dụng (H7): HUONG-DAN.md → một trang HTML đọc được
# --------------------------------------------------------------------------- #
#
# Vì sao tự viết bộ chuyển markdown thay vì thêm thư viện: bộ cài của người dùng
# là một danh sách thư viện đã ghim sẵn, và mỗi thư viện thêm vào là một chỗ có
# thể hỏng trên một máy iMac 2017 không có Internet ổn định. HUONG-DAN.md chỉ
# dùng một tập nhỏ cú pháp — tiêu đề, đoạn, danh sách có/không số (lồng nhau),
# bảng, khối code, trích dẫn `>`, in đậm/nghiêng, code trong dòng, liên kết —
# và vài chục dòng dưới đây làm đủ đúng tập đó. Mọi chữ đều được thoát HTML
# trước khi dựng thẻ; liên kết chỉ nhận `http(s)`, `mailto` và đường dẫn nội bộ.

#: Đường dẫn trang hướng dẫn trên máy chủ này. Giao diện mở nó ở tab mới.
GUIDE_URL = "/guide"

_MD_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_MD_HEADING_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$")
_MD_RULE_RE = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_MD_ITEM_RE = re.compile(r"^( *)(?:([-*+])|(\d{1,9})[.)])[ \t]+(.*)$")
_MD_QUOTE_RE = re.compile(r"^ {0,3}>[ ]?(.*)$")
_MD_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^()\s]*)\)")
_MD_BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_MD_ITALIC_RE = re.compile(r"(?<![*\w])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![*\w])")
_MD_TAG_RE = re.compile(r"<[^>]+>")
_MD_STASH_RE = re.compile("\x00(\\d+)\x00")

#: Chỉ những loại liên kết này được thành thẻ `<a>`. `javascript:`, `data:`,
#: `file:`… chỉ còn là chữ: trang này chạy trên cùng nguồn với giao diện điều
#: khiển tool, một liên kết chạy mã ở đây là chạy mã với quyền của giao diện.
_MD_LINK_SCHEMES = frozenset({"http", "https", "mailto"})


@dataclass(frozen=True)
class _MdItem:
    """Một dòng mở đầu mục danh sách: thụt lề, loại, số, nội dung, cột nội dung."""

    indent: int
    ordered: bool
    number: int
    content: str
    content_col: int


class _Slugger:
    """Mã neo cho tiêu đề theo kiểu GitHub — khớp mục lục viết tay trong HUONG-DAN.md.

    `## 1. Cài lần đầu` → `1-cài-lần-đầu`, đúng như `[Cài lần đầu](#1-cài-lần-đầu)`
    ở mục lục. Trùng thì thêm `-1`, `-2` như GitHub.
    """

    def __init__(self) -> None:
        self._seen: dict[str, int] = {}

    def make(self, text: str) -> str:
        base = unicodedata.normalize("NFC", text).strip().lower()
        base = re.sub(r"[^\w\- ]", "", base).replace(" ", "-") or "muc"
        count = self._seen.get(base, 0)
        self._seen[base] = count + 1
        return base if count == 0 else f"{base}-{count}"


def markdown_to_html(text: str) -> str:
    """Đổi một văn bản markdown (tập cú pháp của HUONG-DAN.md) thành đoạn HTML an toàn."""
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return "\n".join(_md_blocks([line.expandtabs(4) for line in lines.split("\n")], _Slugger()))


def _md_blocks(lines: Sequence[str], slugs: _Slugger) -> list[str]:
    """Dựng các khối (tiêu đề, bảng, danh sách, đoạn…) từ một dãy dòng."""
    out: list[str] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            out.append("<p>" + _md_inline(" ".join(part.strip() for part in para)) + "</p>")
            para.clear()

    i = 0
    total = len(lines)
    while i < total:
        line = lines[i]
        if not line.strip():
            flush()
            i += 1
            continue
        fence = _MD_FENCE_RE.match(line)
        if fence:
            flush()
            i = _md_fence(lines, i, fence.group(1), out)
            continue
        heading = _MD_HEADING_RE.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            inner = _md_inline(heading.group(2))
            out.append(f'<h{level} id="{slugs.make(_md_plain(inner))}">{inner}</h{level}>')
            i += 1
            continue
        if _MD_RULE_RE.match(line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if line.lstrip().startswith("|") and i + 1 < total and _MD_TABLE_SEP_RE.match(lines[i + 1]):
            flush()
            i = _md_table(lines, i, out)
            continue
        if _MD_QUOTE_RE.match(line):
            flush()
            quoted: list[str] = []
            while i < total:
                match = _MD_QUOTE_RE.match(lines[i])
                if match is None:
                    break
                quoted.append(match.group(1))
                i += 1
            out.append("<blockquote>" + "\n".join(_md_blocks(quoted, slugs)) + "</blockquote>")
            continue
        if _md_item(line) is not None:
            flush()
            i = _md_list(lines, i, slugs, out)
            continue
        para.append(line)
        i += 1
    flush()
    return out


def _md_fence(lines: Sequence[str], i: int, marker: str, out: list[str]) -> int:
    """Khối code rào bằng ``` — giữ nguyên từng ký tự, chỉ thoát HTML."""
    opening = lines[i]
    indent = len(opening) - len(opening.lstrip(" "))
    char = marker[0]
    body: list[str] = []
    i += 1
    while i < len(lines):
        current = lines[i]
        bare = current.strip()
        if bare and set(bare) == {char} and len(bare) >= len(marker):
            i += 1
            break
        body.append(current[indent:] if not current[:indent].strip() else current.lstrip())
        i += 1
    out.append("<pre><code>" + html.escape("\n".join(body), quote=False) + "</code></pre>")
    return i


def _md_cells(line: str) -> list[str]:
    """Tách một dòng bảng thành ô; `|` trong `code` hoặc viết `\\|` không cắt ô."""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    cells: list[str] = []
    buf: list[str] = []
    in_code = False
    k = 0
    while k < len(text):
        ch = text[k]
        if ch == "\\" and k + 1 < len(text) and text[k + 1] == "|":
            buf.append("|")
            k += 2
            continue
        if ch == "`":
            in_code = not in_code
        if ch == "|" and not in_code:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        k += 1
    cells.append("".join(buf).strip())
    return cells


def _md_align(cell: str) -> str:
    bare = cell.strip()
    left, right = bare.startswith(":"), bare.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    return "left" if left else ""


def _md_table(lines: Sequence[str], i: int, out: list[str]) -> int:
    """Bảng markdown → `<table>`, bọc trong khung cuộn ngang cho màn hình hẹp."""
    header = _md_cells(lines[i])
    aligns = [_md_align(cell) for cell in _md_cells(lines[i + 1])]
    i += 2
    rows: list[list[str]] = []
    while i < len(lines) and lines[i].strip().startswith("|"):
        rows.append(_md_cells(lines[i]))
        i += 1
    width = len(header)

    def cell(tag: str, text: str, k: int) -> str:
        align = aligns[k] if k < len(aligns) else ""
        style = f' style="text-align:{align}"' if align else ""
        return f"<{tag}{style}>{_md_inline(text)}</{tag}>"

    head = "".join(cell("th", header[k], k) for k in range(width))
    body = "".join(
        "<tr>" + "".join(cell("td", (row + [""] * width)[k], k) for k in range(width)) + "</tr>"
        for row in rows
    )
    out.append(
        f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )
    return i


def _md_item(line: str) -> _MdItem | None:
    match = _MD_ITEM_RE.match(line)
    if match is None or _MD_RULE_RE.match(line):
        return None
    ordered = match.group(3) is not None
    return _MdItem(
        indent=len(match.group(1)),
        ordered=ordered,
        number=int(match.group(3)) if ordered else 1,
        content=match.group(4),
        content_col=match.start(4),
    )


def _md_starts_block(line: str) -> bool:
    """Dòng này mở một khối mới (không thể là phần nối tiếp của một mục danh sách)."""
    return bool(
        _MD_FENCE_RE.match(line)
        or _MD_HEADING_RE.match(line)
        or _MD_RULE_RE.match(line)
        or _MD_QUOTE_RE.match(line)
        or line.lstrip().startswith("|")
        or _md_item(line) is not None
    )


def _md_list(lines: Sequence[str], i: int, slugs: _Slugger, out: list[str]) -> int:
    """Một danh sách (có thể lồng nhau) bắt đầu ở dòng `i`; trả chỉ số dòng kế tiếp.

    Nội dung mỗi mục (các dòng thụt vào tới cột nội dung) được dựng lại bằng
    `_md_blocks`, nên danh sách con, khối code và đoạn thứ hai trong một mục đều
    đi đúng một đường với phần còn lại của trang.
    """
    first = _md_item(lines[i])
    assert first is not None
    base, ordered, start = first.indent, first.ordered, first.number
    content_col = first.content_col
    items: list[list[str]] = []
    loose = False
    total = len(lines)
    while i < total:
        line = lines[i]
        if not line.strip():
            j = i + 1
            while j < total and not lines[j].strip():
                j += 1
            if j >= total or not items:
                break
            following = lines[j]
            item = _md_item(following)
            indent = len(following) - len(following.lstrip(" "))
            if item is not None and item.indent == base and item.ordered == ordered:
                loose = True
                i = j
                continue
            if indent >= content_col:
                items[-1].extend([""] * (j - i))
                i = j
                continue
            break
        item = _md_item(line)
        indent = len(line) - len(line.lstrip(" "))
        if item is not None and item.indent < content_col:
            if item.ordered != ordered or item.indent < base:
                break
            items.append([item.content])
            content_col = item.content_col
            i += 1
            continue
        if items and indent >= content_col:
            items[-1].append(line[content_col:])
            i += 1
            continue
        if items and items[-1] and items[-1][-1].strip() and not _md_starts_block(line):
            # Dòng nối tiếp "lười": đoạn văn của mục trước chảy sang dòng sau mà
            # không thụt lề đủ — markdown coi vẫn là cùng một đoạn.
            items[-1].append(line.strip())
            i += 1
            continue
        break

    tag = "ol" if ordered else "ul"
    attrs = f' start="{start}"' if ordered and start != 1 else ""
    parts: list[str] = []
    for item_lines in items:
        blocks = _md_blocks(item_lines, slugs)
        tight = not loose and "" not in item_lines
        if tight and blocks and blocks[0].startswith("<p>") and blocks[0].endswith("</p>"):
            blocks[0] = blocks[0][3:-4]
        parts.append("<li>" + "\n".join(blocks) + "</li>")
    out.append(f"<{tag}{attrs}>" + "\n".join(parts) + f"</{tag}>")
    return i


def _md_inline(text: str) -> str:
    """Định dạng trong dòng: `code`, liên kết, **đậm**, *nghiêng* — sau khi đã thoát HTML."""
    codes: list[str] = []

    def stash(match: re.Match[str]) -> str:
        codes.append("<code>" + html.escape(match.group(2), quote=False) + "</code>")
        return f"\x00{len(codes) - 1}\x00"

    work = _MD_CODE_SPAN_RE.sub(stash, text)
    work = html.escape(work, quote=False)
    work = _MD_LINK_RE.sub(_md_link, work)
    work = _MD_BOLD_RE.sub(r"<strong>\1</strong>", work)
    work = _MD_ITALIC_RE.sub(r"<em>\1</em>", work)
    return _MD_STASH_RE.sub(lambda match: codes[int(match.group(1))], work)


def _md_link(match: re.Match[str]) -> str:
    label, target = match.group(1), match.group(2)
    raw = html.unescape(target).strip()
    if not raw:
        return label
    scheme = urlparse(raw).scheme.lower()
    if scheme and scheme not in _MD_LINK_SCHEMES:
        return label
    href = html.escape(raw, quote=True)
    extra = ' target="_blank" rel="noopener noreferrer"' if scheme in ("http", "https") else ""
    return f'<a href="{href}"{extra}>{label}</a>'


def _md_plain(fragment: str) -> str:
    """Chữ trần của một đoạn HTML nhỏ (cho mã neo và tiêu đề trang)."""
    return html.unescape(_MD_TAG_RE.sub("", fragment)).strip()


#: CSS nội tuyến (không file ngoài, không phông tải từ mạng): trang phải mở được
#: cả khi máy không có Internet. Có sẵn giao diện tối theo cài đặt của máy.
_GUIDE_CSS = """
:root{color-scheme:light dark;--bg:#fbfaf7;--fg:#1f2328;--muted:#5b6470;--line:#d8dde3;
--soft:#f0efea;--accent:#0b62b8}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e6e8eb;--muted:#a3abb5;
--line:#353b44;--soft:#22262d;--accent:#6cb6ff}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:17px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}
.bar{position:sticky;top:0;z-index:1;background:var(--bg);border-bottom:1px solid var(--line);
padding:.55rem 1.2rem;font-size:15px}
.bar a{color:var(--accent);text-decoration:none}
main{max-width:46rem;margin:0 auto;padding:1.2rem 1.2rem 4rem}
h1{font-size:1.9rem;line-height:1.25;margin:1rem 0 .8rem}
h2{font-size:1.45rem;line-height:1.3;margin:2rem 0 .7rem}
h3{font-size:1.15rem;margin:1.6rem 0 .5rem}
h4,h5,h6{font-size:1rem;margin:1.2rem 0 .4rem}
p,ul,ol,blockquote,pre,.table-wrap{margin:0 0 1rem}
ul,ol{padding-left:1.6rem}
li{margin:.25rem 0}
li>ul,li>ol,li>pre,li>p{margin:.35rem 0}
a{color:var(--accent)}
code{font:.9em/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--soft);
padding:.1em .35em;border-radius:4px}
pre{background:var(--soft);padding:.8rem 1rem;border-radius:8px;overflow-x:auto}
pre code{background:none;padding:0;font-size:.88em}
blockquote{margin-left:0;padding:.6rem 1rem;border-left:4px solid var(--accent);
background:var(--soft);border-radius:0 8px 8px 0}
blockquote>:last-child{margin-bottom:0}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.95em}
th,td{border:1px solid var(--line);padding:.45rem .7rem;text-align:left;vertical-align:top}
th{background:var(--soft)}
hr{border:0;border-top:1px solid var(--line);margin:2rem 0}
.note{color:var(--muted)}
@media print{.bar{display:none}body{background:#fff;color:#000}}
"""


def _guide_page(title: str, body: str) -> str:
    return (
        '<!doctype html>\n<html lang="vi"><head><meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>{_GUIDE_CSS}</style></head>\n"
        '<body><header class="bar"><a href="/">← Quay lại SrtGen</a></header>\n'
        f'<main class="guide">\n{body}\n</main></body></html>\n'
    )


def render_guide_html(markdown_text: str) -> str:
    """Trang HTML hoàn chỉnh của HUONG-DAN.md: tiêu đề trang lấy từ dòng `#` đầu tiên."""
    body = markdown_to_html(markdown_text)
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    title = _md_plain(heading.group(1)) if heading else "Hướng dẫn sử dụng SrtGen"
    return _guide_page(title or "Hướng dẫn sử dụng SrtGen", body)


def guide_missing_html(detail: str = "") -> str:
    """Trang báo không mở được hướng dẫn — vẫn là một trang đọc được, không phải JSON lỗi."""
    extra = f'<p class="note">Chi tiết kỹ thuật: {html.escape(detail)}</p>' if detail else ""
    body = (
        "<h1>Chưa mở được hướng dẫn sử dụng</h1>\n"
        "<p>Không tìm thấy (hoặc không đọc được) file hướng dẫn <code>HUONG-DAN.md</code> "
        "trên máy. File này nằm cùng chỗ với file <code>CaiDat.command</code> mà bạn đã "
        "bấm lúc cài đặt; chạy lại <code>CaiDat.command</code> sẽ chép lại nó.</p>\n"
        + extra
    )
    return _guide_page("Chưa mở được hướng dẫn sử dụng", body)


def _download_bytes(
    url: str,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> bytes:
    """Tải một file về bộ nhớ, có trần dung lượng và có chỗ để bấm Dừng.

    `urllib` của thư viện chuẩn chứ không phải một thư viện ngoài: nút này tồn
    tại đúng cho lúc máy đang thiếu thứ gì đó, nên bản thân nó không được phụ
    thuộc vào một gói có thể cũng đang thiếu.

    Chỉ nhận `https://` — địa chỉ nằm trong hằng số của file này, kiểm lại ở đây
    là để một lần sửa cẩu thả sau này không biến nó thành `file://`.
    """
    import urllib.request

    if not str(url).lower().startswith("https://"):
        raise JobError("Địa chỉ tải về không hợp lệ nên tool đã dừng lại.")

    chunks: list[bytes] = []
    total = 0
    try:
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - đã kiểm https
            declared = int(response.headers.get("Content-Length") or 0)
            while True:
                if cancelled is not None and cancelled():
                    raise JobError("Bạn đã bấm Dừng nên tool không tải tiếp.")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_DOWNLOAD_BYTES:
                    raise JobError(
                        "File tải về lớn bất thường nên tool đã dừng lại để khỏi "
                        "làm đầy ổ đĩa. Hãy thử lại sau."
                    )
                chunks.append(chunk)
                if on_progress is not None:
                    on_progress(total, declared)
    except JobError:
        raise
    except Exception as err:  # noqa: BLE001 - mọi lỗi mạng thành câu tiếng Việt
        raise ActionError(
            "Không tải được file về máy. Thường là do mất mạng hoặc mạng chặn "
            "địa chỉ này. Hãy kiểm tra kết nối Internet rồi thử lại.",
            detail=f"{type(err).__name__}: {err}",
        ) from err
    return b"".join(chunks)


def _extract_binary(blob: bytes, member_name: str, target: Path) -> None:
    """Lấy đúng một file thực thi ra khỏi file nén, rồi cho nó quyền chạy.

    Cố ý **không** dùng `ZipFile.extract`: hàm đó lấy đường dẫn từ bên trong file
    nén, tức là để một file nén độc hại tự chọn chỗ ghi (`../../…`). Ở đây chỗ
    ghi do máy chủ quyết, còn trong file nén chỉ chọn đúng một mục có tên khớp.
    """
    import io
    import zipfile

    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as err:
        raise JobError(
            f"File {member_name} tải về bị hỏng nên tool không cài được. Hãy thử lại."
        ) from err

    with archive:
        wanted = None
        for info in archive.infolist():
            if info.is_dir():
                continue
            if Path(info.filename).name in (member_name, f"{member_name}.exe"):
                wanted = info
                break
        if wanted is None:
            raise JobError(
                f"File tải về không chứa {member_name}. Có thể trang tải đang bận, "
                "hãy thử lại sau ít phút."
            )
        io_utils.ensure_dir(target.parent)
        with archive.open(wanted) as source:
            fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                         | getattr(os, "O_BINARY", 0), 0o755)
            try:
                while True:
                    piece = source.read(1024 * 1024)
                    if not piece:
                        break
                    os.write(fd, piece)
            finally:
                os.close(fd)
    try:
        os.chmod(target, 0o755)
    except (OSError, NotImplementedError):
        pass
    if sys.platform == "darwin":
        # Bỏ nhãn "tải từ Internet" của macOS; còn nhãn thì Gatekeeper chặn và
        # người dùng gặp một hộp thoại khó hiểu ngay giữa lúc tạo phụ đề.
        try:
            subprocess.run(  # noqa: S603 - tham số dạng list, không qua shell
                ["xattr", "-d", "com.apple.quarantine", str(target)],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass


def _dir_bytes(path: Path) -> int:
    """Tổng dung lượng file thật trong một thư mục, bỏ qua liên kết tượng trưng.

    Bỏ qua symlink là bắt buộc: cache của `huggingface_hub` để file thật ở
    `blobs/` rồi trỏ `snapshots/` vào đó, đếm cả hai thì phần trăm nhảy lên gấp
    đôi và người dùng thấy "đang tải 190%".
    """
    total = 0
    try:
        for item in path.rglob("*"):
            try:
                if item.is_symlink() or not item.is_file():
                    continue
                total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _size_text(size: float) -> str:
    """`1620000000` → `1,6 GB`. Dấu phẩy thập phân theo cách viết tiếng Việt."""
    value = float(size or 0)
    for unit, step in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if value >= step:
            return f"{value / step:.1f}".replace(".", ",") + f" {unit}"
    return f"{int(value)} B"


# --------------------------------------------------------------------------- #
# thử khoá API
# --------------------------------------------------------------------------- #

def test_api_key(api_key: str = "") -> dict[str, Any]:
    """Gọi thử một request nhỏ nhất có thể để biết khoá API còn dùng được không.

    Trả `{ok, message, detail}` — và **không bao giờ** trả khoá nguyên văn, kể cả
    trong `detail`: người dùng hay chụp màn hình phần chi tiết gửi cho người hỗ
    trợ, nên một khoá lọt vào đó là lọt ra ngoài thật.

    Không truyền khoá thì thử khoá đã lưu (giao diện chỉ giữ bản che, nên nó
    không gửi lại được khoá thật).

    Mỗi ca hỏng có một câu riêng, vì bốn ca ấy cần bốn hành động khác nhau: gõ
    lại khoá / chờ sang ngày mới / chờ vài phút / kiểm tra mạng. Một câu chung
    "không gọi được AI" thì người dùng không biết phải làm gì tiếp.
    """
    cfg = _safe_cfg()
    ai_cfg = cfg.get("ai") if isinstance(cfg.get("ai"), dict) else {}
    settings = load_settings()

    key = str(api_key or "").strip()
    source = "vừa nhập"
    if not key or key == mask_api_key(str(settings.get("api_key") or "")):
        key = str(settings.get("api_key") or "").strip()
        source = "đã lưu"
    if not key:
        env_name = str(ai_cfg.get("api_key_env") or "GEMINI_API_KEY")
        key = str(os.environ.get(env_name) or "").strip()
        source = f"trong biến môi trường {env_name}"
    if not key:
        return {
            "ok": False,
            "message": (
                "Chưa có mã API nào để thử. Hãy dán mã API của Gemini vào ô phía trên "
                "rồi bấm lại — hoặc cứ để trống, tool vẫn chạy được và dịch bằng bản "
                "miễn phí của Google."
            ),
            "detail": "",
        }

    try:
        from srtgen.providers.base import (
            ERR_AUTH,
            ERR_MISSING_DEP,
            ERR_NETWORK,
            ERR_QUOTA,
            ERR_RATE_LIMIT,
            ERR_TIMEOUT,
            ProviderError,
        )
        from srtgen.providers.gemini import GeminiProvider
    except ImportError as err:
        return {
            "ok": False,
            "message": (
                "Bản cài này chưa có phần gọi AI nên chưa thử được mã API. "
                "Hãy chạy lại file CaiDat.command trong thư mục cài đặt."
            ),
            "detail": f"{type(err).__name__}: {err}",
        }

    model = str(settings.get("ai_model") or ai_cfg.get("model_other") or "gemini-2.5-flash")
    try:
        provider = GeminiProvider(
            key,
            model=model,
            # Một lần gọi, không thử lại: đây là phép thử, không phải việc thật.
            # Người dùng đang đứng chờ trước màn hình.
            retries=0,
            timeout=25.0,
            connect_timeout=10.0,
            # Đủ chỗ cho cả phần "suy nghĩ" của Gemini 2.5, và tắt suy nghĩ khi model
            # cho phép (Flash: 0; Pro không tắt được, tối thiểu 128).
            max_output_tokens=512,
            thinking_budget=(128 if "pro" in model.lower() else 0) if "2.5" in model else None,
        )
        provider.complete_json(
            "Trả lời đúng một đối tượng JSON: {\"ok\": true}",
            {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            model=model,
        )
    except ProviderError as err:
        return {
            "ok": False,
            "message": _key_test_message(
                err.kind,
                {
                    "auth": ERR_AUTH,
                    "quota": ERR_QUOTA,
                    "rate": ERR_RATE_LIMIT,
                    "timeout": ERR_TIMEOUT,
                    "network": ERR_NETWORK,
                    "missing": ERR_MISSING_DEP,
                },
                err.user_message,
            ),
            "detail": _scrub_key(str(err), key),
        }
    except Exception as err:  # noqa: BLE001 - phép thử không được ném lỗi 500
        return {
            "ok": False,
            "message": (
                "Không thử được mã API lúc này. Hãy kiểm tra kết nối Internet rồi bấm lại."
            ),
            "detail": _scrub_key(f"{type(err).__name__}: {err}", key),
        }

    saved_note = ""
    if source == "vừa nhập":
        # Khoá vừa thử được thì lưu luôn. Nút "Lưu cài đặt" nằm tít dưới cùng
        # trang; người dùng thấy "dùng được" là tưởng xong, đóng tab, và mất khoá.
        try:
            save_settings({"api_key": key})
            saved_note = " Khoá đã được lưu, không cần bấm Lưu cài đặt nữa."
        except Exception as err:  # noqa: BLE001 - phép thử vẫn là thành công
            saved_note = f" (Chưa lưu được khoá: {err}. Hãy bấm Lưu cài đặt.)"
    return {
        "ok": True,
        "saved": bool(saved_note.startswith(" Khoá")),
        "message": (
            f"Mã API ({source}) dùng được. Tool sẽ dùng nó cho cả phần soát tên riêng "
            "lẫn phần dịch tiếng Việt." + saved_note
        ),
        "detail": f"model: {model}",
    }


def _key_test_message(kind: str, kinds: dict[str, str], fallback: str) -> str:
    """Câu tiếng Việt cho từng ca hỏng của phép thử khoá."""
    if kind == kinds["auth"]:
        return (
            "Mã API không đúng hoặc đã bị thu hồi. Hãy mở lại trang lấy mã của Google "
            "và dán lại cho đủ, không thừa khoảng trắng."
        )
    if kind == kinds["quota"]:
        return (
            "Mã API đúng, nhưng tài khoản đã dùng hết hạn mức miễn phí của hôm nay. "
            "Hãy đợi sang ngày mới, hoặc dùng một mã API khác."
        )
    if kind == kinds["rate"]:
        return (
            "Mã API đúng, nhưng máy chủ AI đang bận vì có quá nhiều yêu cầu. "
            "Hãy thử lại sau vài phút."
        )
    if kind in (kinds["network"], kinds["timeout"]):
        return (
            "Không kết nối được tới máy chủ AI. Hãy kiểm tra kết nối Internet "
            "rồi bấm thử lại."
        )
    if kind == kinds["missing"]:
        return (
            "Bản cài này còn thiếu phần gọi mạng nên chưa thử được mã API. "
            "Hãy chạy lại file CaiDat.command trong thư mục cài đặt."
        )
    return fallback or "Chưa thử được mã API. Hãy kiểm tra kết nối mạng rồi bấm lại."


def _scrub_key(text: str, key: str) -> str:
    """Xoá mọi vết của khoá API trong một chuỗi trước khi nó đi ra khỏi máy chủ."""
    cleaned = str(text or "")
    secret = str(key or "").strip()
    if secret:
        cleaned = cleaned.replace(secret, mask_api_key(secret))
    return cleaned[:600]


# --------------------------------------------------------------------------- #
# cổng mạng
# --------------------------------------------------------------------------- #

def find_free_port(start: int = DEFAULT_PORT, count: int = PORT_SEARCH_COUNT) -> int:
    """Tìm cổng trống đầu tiên từ `start`.

    Không đặt `SO_REUSEADDR`: trên Windows tuỳ chọn đó cho phép chiếm cổng mà tiến
    trình khác đang dùng, tức là biến phép thử này thành vô nghĩa.
    """
    for port in range(start, start + max(1, count)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((HOST, port))
            except OSError:
                continue
            return port
    raise RuntimeError(
        f"Không tìm được cổng trống nào trong khoảng {start}-{start + count - 1}. "
        "Hãy tắt bớt ứng dụng đang chạy rồi thử lại."
    )


def web_dependencies_missing() -> str:
    """Chuỗi rỗng nếu chạy được; ngược lại là câu hướng dẫn cài bằng tiếng Việt."""
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        return MISSING_WEB_HINT
    return ""


# --------------------------------------------------------------------------- #
# tiện ích JSON
# --------------------------------------------------------------------------- #

def _jsonable(value: Any) -> Any:
    """Khử `NaN`/`Infinity` trước khi trả về cho trình duyệt.

    Bộ mã hoá JSON của Starlette đặt `allow_nan=False`, nên một con số thống kê
    lạ từ chặng S8 sẽ làm hỏng nguyên lời gọi API thay vì chỉ hiện sai một ô.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    return value


# --------------------------------------------------------------------------- #
# ứng dụng web
# --------------------------------------------------------------------------- #

def create_app(manager: JobManager | None = None, *, port: int | None = None) -> Any:
    """Dựng ứng dụng FastAPI. Import `fastapi` ở trong hàm, cố ý — xem docstring module.

    `port` là cổng máy chủ đang (sẽ) nghe. Có nó thì `Origin` phải khớp cả host
    lẫn cổng; `serve()` luôn ghi nó vào `app.state.port` ngay trước khi mở cổng.
    Không có (bộ kiểm thử dựng app không cần cổng thật) thì chỉ so tên host.
    """
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import (
            FileResponse,
            HTMLResponse,
            JSONResponse,
            Response,
            StreamingResponse,
        )
        from fastapi.staticfiles import StaticFiles
        from starlette.exceptions import HTTPException as StarletteHTTPException
        from starlette.requests import ClientDisconnect
    except ImportError as err:  # pragma: no cover - phụ thuộc môi trường
        raise RuntimeError(MISSING_WEB_HINT) from err

    # `from __future__ import annotations` ở đầu file biến MỌI chú thích kiểu
    # thành chuỗi, và FastAPI phân giải chuỗi đó bằng **globals của module**, chứ
    # không phải biến cục bộ của hàm này. Vì `fastapi` được import lười ở trong
    # hàm (xem docstring module), cái tên `Request` không nằm trong globals — mà
    # khi không phân giải được, FastAPI lại coi tham số đó là một tham số query
    # bắt buộc, nên mọi endpoint nhận `request` trả về 422 “Field required”.
    # Đăng ký tên vào globals ngay trước khi định nghĩa route là cách rẻ nhất giữ
    # được cả hai thứ: import lười và chú thích kiểu đọc được.
    globals()["Request"] = Request

    jobs = manager or get_manager()

    @asynccontextmanager
    async def lifespan(app: Any):
        yield
        # Tắt máy chủ = dừng việc đang chạy và ghi trạng thái xuống đĩa, để lần mở
        # sau còn thấy lịch sử thay vì một công việc treo vĩnh viễn.
        jobs.shutdown(wait=3.0)

    app = FastAPI(
        title="srtgen",
        description="Tạo phụ đề .srt bốn dòng (chữ Hán + pinyin) chạy ngay trên máy bạn.",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.jobs = jobs
    app.state.port = int(port) if port else None
    try:
        _sweep_stale_uploads(jobs)
    except Exception:  # noqa: BLE001 - dọn rác không được làm hỏng lúc khởi động
        pass

    def ok(data: Any, status: int = 200) -> Any:
        return JSONResponse(_jsonable(data), status_code=status)

    def fail(message: str, status: int = 400, **extra: Any) -> Any:
        body = {"error": {"message": message, **extra}}
        return JSONResponse(_jsonable(body), status_code=status)

    # -- chặn yêu cầu từ trang web lạ ------------------------------------- #

    @app.middleware("http")
    async def guard_local_only(request: Request, call_next: Callable) -> Any:
        own_port = getattr(app.state, "port", None)
        if not _host_allowed(request.headers.get("host", ""), own_port):
            return fail(
                "Yêu cầu bị từ chối vì không đến từ chính máy này.",
                status=403,
            )
        origin = request.headers.get("origin")
        if origin and not _origin_allowed(origin, own_port):
            return fail(
                "Một trang web khác đang cố gửi lệnh tới srtgen. Yêu cầu đã bị chặn.",
                status=403,
            )
        # `Origin: null` lọt qua phép kiểm ở trên (`urlparse("null").netloc` rỗng,
        # mà "" nằm trong ALLOWED_HOSTS), và một trang độc chỉ cần một
        # `<iframe sandbox>` là gửi được đúng cái Origin đó tới `/api/actions/…`.
        # Không chặn thẳng "null" được: theo đặc tả Fetch, trình duyệt được phép
        # gửi `Origin: null` cho cả POST cùng nguồn của chính giao diện này, vì mọi
        # phản hồi ở đây mang `Referrer-Policy: no-referrer`. `Sec-Fetch-Site` thì
        # do trình duyệt tự đặt, trang web không sửa được, và vẫn nói thật khi
        # Origin là null — nên yêu cầu làm thay đổi trạng thái chỉ được nhận khi
        # nó là "same-origin" (hoặc "none" = người dùng tự gõ địa chỉ). Không có
        # header này (curl, TestClient, trình duyệt cũ) thì giữ nguyên như trước.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            fetch_site = str(request.headers.get("sec-fetch-site") or "").strip().lower()
            if fetch_site and fetch_site not in ("same-origin", "none"):
                return fail(
                    "Một trang web khác đang cố gửi lệnh tới srtgen. Yêu cầu đã bị chặn.",
                    status=403,
                )
        response = await call_next(request)
        # Không cho trang khác nhúng giao diện này vào iframe của họ.
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    #: Lỗi do bộ định tuyến tự sinh (404 sai đường dẫn, 405 sai phương thức,
    #: 404 của `StaticFiles`) mang nguyên văn tiếng Anh của Starlette. Người dùng
    #: không phải dân IT đọc “Method Not Allowed” thì chỉ biết là hỏng, không biết
    #: hỏng gì — nên dịch sẵn ở đây thay vì để lọt lên giao diện.
    _ROUTING_MESSAGES: dict[int, str] = {
        404: "Không có chức năng này.",
        405: "Bản chương trình đang chạy chưa làm được việc này.",
        422: "Dữ liệu gửi lên không đúng dạng chương trình chờ đợi.",
    }

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> Any:
        detail = exc.detail
        message = detail if isinstance(detail, str) else str(detail)
        return fail(message, status=exc.status_code)

    # `HTTPException` của FastAPI là lớp con của bản Starlette, nên handler ở trên
    # KHÔNG bắt được lỗi do chính bộ định tuyến ném ra. Thiếu handler này thì mọi
    # 404/405 của router trả về `{"detail": "Not Found"}` — sai cả hình dạng lẫn
    # ngôn ngữ so với phần còn lại của API.
    @app.exception_handler(StarletteHTTPException)
    async def routing_error(request: Request, exc: StarletteHTTPException) -> Any:
        detail = exc.detail
        text = detail if isinstance(detail, str) else str(detail)
        message = _ROUTING_MESSAGES.get(
            exc.status_code,
            "Chương trình chưa xử lý được yêu cầu này.",
        )
        return fail(message, status=exc.status_code, detail_text=text)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> Any:
        return fail(
            "Tool gặp lỗi ngoài dự tính. Hãy thử lại; nếu vẫn vậy, gửi phần chi tiết "
            "bên dưới cho người hỗ trợ.",
            status=500,
            detail=f"{type(exc).__name__}: {exc}",
        )

    # -- trang chính và file tĩnh ------------------------------------------ #

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def _ui_files_are_never_stale(request: Request, call_next: Any) -> Any:
        # Trình duyệt mặc định cache file tĩnh theo heuristic, nên sau khi app cập
        # nhật (hoặc khởi động lại với code mới) người dùng vẫn chạy app.js CŨ và
        # thấy lỗi "đã sửa rồi" — đã xảy ra thật khi thử. `no-cache` không cấm
        # cache, chỉ bắt trình duyệt hỏi lại bằng ETag; file không đổi thì 304, rẻ.
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/") or path.endswith((".js", ".css", ".html")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/", include_in_schema=False)
    def index() -> Any:
        page = STATIC_DIR / "index.html"
        if page.is_file():
            # Gắn dấu phiên bản vào app.js/style.css: F5 thường của Chrome chỉ tải
            # lại trang chính, còn file .js lấy từ cache theo heuristic — người
            # dùng thấy giao diện mới chạy bằng mã cũ (đã xảy ra thật). Đổi tên
            # đường dẫn theo mtime là cách chắc chắn duy nhất.
            try:
                html = io_utils.read_text(page)
                stamp = str(int(max(
                    (STATIC_DIR / name).stat().st_mtime for name in ("app.js", "style.css", "index.html")
                    if (STATIC_DIR / name).is_file()
                )))
                html = html.replace('src="app.js"', f'src="app.js?v={stamp}"').replace(
                    'href="style.css"', f'href="style.css?v={stamp}"'
                )
                return HTMLResponse(html, headers={"Cache-Control": "no-cache"})
            except OSError:
                return FileResponse(page, media_type="text/html; charset=utf-8")
        return HTMLResponse(_PLACEHOLDER_HTML, status_code=200)

    @app.get("/api/health")
    def health() -> Any:
        from srtgen import __version__

        return ok(
            {
                "ok": True,
                "app": "srtgen",
                "version": __version__,
                "steps": step_table(),
                "statuses": STATUS_LABELS,
                "max_upload_bytes": MAX_UPLOAD_BYTES,
                # app.js (`loadHealth`) đọc khoá này để chặn sớm file video quá nặng
                # ngay ở trình duyệt; thiếu nó thì giao diện luôn dùng 4 GiB mặc định
                # dù `web.max_media_bytes` đã được đổi.
                "max_media_bytes": max_media_bytes(_safe_cfg()),
                "static_ready": (STATIC_DIR / "index.html").is_file(),
                "guide_url": GUIDE_URL,
                "can_shutdown": getattr(app.state, "uvicorn_server", None) is not None,
            }
        )

    # -- công việc ---------------------------------------------------------- #

    @app.get("/api/jobs")
    def list_jobs(limit: int = 50) -> Any:
        return ok({"jobs": jobs.list(limit=limit)})

    @app.post("/api/jobs")
    async def create_job(request: Request) -> Any:
        """Tạo công việc từ **một trong hai dạng**, trả cùng một hình dạng `{job_id, job}`.

        * JSON `{source, mode, options}` — dán link YouTube (hoặc đường dẫn file).
        * `multipart/form-data` với `file` (nhị phân) + `options` (chuỗi JSON) —
          kéo thả hoặc chọn file âm thanh/video. Trước đây dạng này rơi vào nhánh
          JSON, nhận `{}` và trả "Bạn chưa dán link YouTube" — luồng tạo phụ đề
          từ file có sẵn hỏng hoàn toàn mà không ai biết vì sao.
        """
        raw_ctype = str(request.headers.get("content-type") or "")
        if raw_ctype.lower().startswith("multipart/form-data"):
            # Truyền nguyên văn: `boundary` phân biệt hoa thường.
            return await create_job_from_upload(request, raw_ctype)

        body = await _json_body(request)
        # Người dùng hay dán `www.youtube.com/watch?v=…` (thiếu `https://`). Giao
        # diện hiểu đó là link, nên máy chủ cũng phải hiểu — xem `normalize_source`.
        source = normalize_source(str(body.get("source") or ""))
        mode = str(body.get("mode") or "run").strip() or "run"
        options = body.get("options")
        options = dict(options) if isinstance(options, dict) else {}

        problem = _source_problem(source)
        if problem:
            return fail(problem)
        try:
            job = jobs.submit(source, mode=mode, options=options)
        except JobError as err:
            return fail(str(err))
        return ok({"job_id": job.id, "job": job.public_dict()}, status=201)

    async def create_job_from_upload(request: Request, content_type: str) -> Any:
        """Dạng (b): ghi luồng file ra `work/_uploads/<uuid><đuôi>` rồi mới xếp việc.

        Không dùng `request.form()`: nó trải file ra một file tạm của hệ thống rồi
        mới đưa lại cho ta, tức là ghi đĩa hai lần và không cho dừng giữa chừng khi
        vượt trần. Ở đây từng khối đi thẳng tới chỗ cuối cùng, vượt trần là dừng.
        """
        cfg = _safe_cfg()
        limit = max_media_bytes(cfg)
        declared = str(request.headers.get("content-length") or "").strip()
        if declared.isdigit() and int(declared) > limit + MAX_FORM_FIELDS_BYTES + _MULTIPART_SLACK:
            return fail(_media_too_big(limit), status=413)
        try:
            upload = await receive_media_upload(
                request.stream(), content_type, dest_dir=uploads_dir(cfg), limit=limit
            )
        except _UploadError as err:
            return fail(str(err), status=err.status)
        except ClientDisconnect:
            return fail(
                "Kết nối bị ngắt khi đang đưa file vào nên tool đã bỏ phần dở dang. "
                "Hãy kéo thả lại file rồi bấm Bắt đầu.",
                status=400,
            )
        except OSError as err:
            return fail(
                "Không ghi được file vào ổ đĩa. Có thể ổ đĩa đã đầy, hoặc thư mục làm "
                "việc của tool đang bị khoá quyền ghi.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )

        options, problem = _upload_options(upload.fields.get("options", ""))
        if problem:
            _discard_upload_file(upload.path)
            return fail(problem)
        mode = str(upload.fields.get("mode") or "").strip() or "run"
        try:
            job = jobs.submit(
                str(upload.path),
                mode=mode,
                options=options,
                title=upload.title,
                cleanup_source=True,
            )
        except JobError as err:
            _discard_upload_file(upload.path)
            return fail(str(err))
        return ok(
            {
                "job_id": job.id,
                "job": job.public_dict(),
                "upload": {
                    "filename": upload.filename,
                    "title": upload.title,
                    "size": upload.size,
                },
            },
            status=201,
        )

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> Any:
        job = jobs.get(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        return ok({"job": job.public_dict(with_log=True)})

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> Any:
        try:
            job = jobs.cancel(job_id)
        except JobError as err:
            return fail(str(err), status=404 if "Không tìm thấy" in str(err) else 409)
        return ok({"job": job.public_dict()})

    @app.post("/api/jobs/{job_id}/rerun")
    async def rerun_job(job_id: str, request: Request) -> Any:
        """Chạy lại một phim từ `from_step` (mặc định 5 — tách cụm), KHÔNG cần file gốc (H3).

        Tạo một công việc MỚI dùng lại `video_id` và `work/<video_id>/` của việc
        cũ. Đây là đường duy nhất để bảng tên riêng vừa lưu đi được vào phụ đề
        của một phim tạo từ file tải lên: bản tải lên đã bị xoá ngay sau bước 1
        (đúng thiết kế), và tải lại file thì ra một thư mục làm việc mới tinh.

        202 `{job_id, job}`; 404 không có việc; 409 đang có việc khác chạy cùng
        phim; 400 (câu tiếng Việt) khi `from_step` sai hoặc thiếu kết quả của các
        bước trước nó trên đĩa.
        """
        old = jobs.get(job_id)
        if old is None:
            return fail("Không tìm thấy công việc này.", status=404)
        body = await _json_body(request)
        video_id = str(old.video_id or "").strip()
        # Việc ghi từ phiên bản trước chưa nhớ thư mục làm việc: tính theo cấu hình.
        fallback = work_dir_for(video_id) if VIDEO_ID_RE.match(video_id) else None
        try:
            job = await asyncio.to_thread(
                jobs.rerun,
                job_id,
                from_step=body.get("from_step", DEFAULT_RERUN_STEP),
                work_dir=fallback,
            )
        except JobNotFound as err:
            return fail(str(err), status=404)
        except JobConflict as err:
            return fail(str(err), status=409)
        except JobError as err:
            return fail(str(err), status=400)
        return ok({"job_id": job.id, "job": job.public_dict()}, status=202)

    @app.get("/api/jobs/{job_id}/result")
    def job_result(job_id: str) -> Any:
        job = jobs.get(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        data = job.public_dict()
        return ok(
            {
                "id": job.id,
                "status": job.status,
                "status_label": data["status_label"],
                "message": job.message,
                "files": job.result or {},
                "downloads": data["downloads"],
                "findings": data["findings"],
                "summary": data["summary"],
                "out_dir": job.out_dir,
                "title": job.title,
                "video_id": job.video_id,
                "error": data["error"],
                # Bản cũ mà chặng xuất file đã cất đi trước khi ghi đè (H1): giao
                # diện phải nói cho người dùng biết phần sửa tay của họ nằm ở đâu.
                "backups": data["backups"],
                "rerun_of": job.rerun_of,
                "rerun_from": job.rerun_from,
                "can_rerun": data["can_rerun"],
            }
        )

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str, request: Request, since: int = -1) -> Any:
        job = jobs.get(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)

        start_index = since
        if start_index < 0:
            start_index = _last_event_id(request)

        async def stream():
            sub = jobs.subscribe(job_id)
            try:
                # `retry` bảo trình duyệt tự kết nối lại sau 3 giây nếu đứt mạng.
                yield "retry: 3000\n\n"
                yield _sse(
                    "snapshot",
                    {
                        "job": _jsonable(job.public_dict()),
                        "log": _jsonable(job.log_since(start_index)),
                        "log_from": max(0, start_index),
                    },
                )
                idle = 0.0
                while True:
                    if await request.is_disconnected():
                        return
                    event = sub.get_nowait()
                    if event is None:
                        if not job.is_active():
                            yield _sse("closed", {"job": _jsonable(job.public_dict())})
                            return
                        await asyncio.sleep(0.15)
                        idle += 0.15
                        if idle >= 15.0:
                            # Nhịp tim giữ kết nối sống qua proxy và bộ tiết kiệm pin.
                            yield ": ping\n\n"
                            idle = 0.0
                        continue
                    idle = 0.0
                    payload = dict(event)
                    kind = str(payload.pop("type", "state"))
                    yield _sse(kind, _jsonable(payload), event_id=payload.get("index"))
            finally:
                sub.close()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # -- kiểm và chuẩn hoá file có sẵn -------------------------------------- #

    @app.post("/api/check")
    async def check_file(request: Request) -> Any:
        try:
            filename, data = await _read_upload(request)
        except _UploadError as err:
            return fail(str(err), status=err.status)
        text = decode_srt_bytes(data)
        try:
            result = await asyncio.to_thread(check_srt_text, text)
        except Exception as err:
            return fail(
                _unreadable_srt_message(err),
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )
        result["filename"] = filename
        jobs.add_finished(
            source=filename or "file .srt",
            mode=MODE_CHECK,
            message=_check_message(result["summary"]),
            findings=result["findings"],
            summary=result["summary"],
            log=[f"Đã kiểm {result['blocks']} dòng phụ đề trong “{filename}”."],
        )
        return ok(result)

    @app.post("/api/fix")
    async def fix_file(request: Request, video_id: str = "", profile: str = "") -> Any:
        """Sửa một file `.srt` có sẵn bằng ĐÚNG hàm của lệnh `srtgen fix` (hợp đồng A)."""
        try:
            filename, data = await _read_upload(request)
        except _UploadError as err:
            return fail(str(err), status=err.status)
        text = decode_srt_bytes(data)

        # `None` = bảng tên khai báo trong cấu hình, đúng như CLI. Có mã phim thì
        # dùng bảng tên riêng của phim đó.
        names: dict[str, str] | None = None
        if video_id:
            if not VIDEO_ID_RE.match(video_id):
                return fail("Mã video không hợp lệ.", status=400)
            names = dict(read_names(video_id)["names"])

        try:
            result = await asyncio.to_thread(
                fix_srt_payload, text, names=names, profile=profile or None
            )
        except Exception as err:
            return fail(
                "Không chuẩn hoá được file này. Có thể nó không đúng định dạng .srt "
                "bốn dòng, hoặc bị hỏng ở một dòng nào đó.",
                status=422,
                detail=f"{type(err).__name__}: {err}",
            )

        out_dir = Path(_effective_out_dir(_safe_cfg(), load_settings()))
        stem = f"{_srt_stem(filename)}.da-chuan-hoa"
        target = out_dir / f"{stem}.srt"
        vi_target = out_dir / f"{stem}_vi.srt"
        newline = "\r\n" if str(result["newline"]).lower() == "crlf" else "\n"
        bom = bool(result["bom"])
        written = ""
        written_vi = ""
        # Sửa cùng một file hai lần là ghi lại đúng `<tên>.da-chuan-hoa.srt` của lần
        # trước — mà bản đó người dùng có thể đã sửa tay. Bản cũ khác bản mới được
        # cất thành `<tên>.da-chuan-hoa.truoc-….srt` (H1, `_write_result_file`);
        # danh sách trả về giống khoá `backups` của chặng xuất file.
        backups: list[str] = []
        try:
            _write_result_file(target, result["text"], bom=bom, newline=newline, backups=backups)
            written = str(target)
            if result.get("has_vi"):
                # Dòng tiếng Việt của file ba dòng đi ra file riêng, đúng thói quen
                # `<tên>.srt` + `<tên>_vi.srt`. Thiếu bước này thì "tải về" chỉ
                # được nửa công sức của bên dịch.
                _write_result_file(
                    vi_target, result["vi_text"], bom=bom, newline=newline, backups=backups
                )
                written_vi = str(vi_target)
        except io_utils.KeepCopyError as err:
            # Câu của `keep_old_copy` đã nói đủ: file nào, vì sao, và rằng file cũ
            # KHÔNG bị ghi đè.
            result["write_error"] = str(err)
            cause = err.__cause__
            result["write_error_detail"] = (
                f"{type(cause).__name__}: {cause}" if cause is not None else ""
            )
        except OSError as err:
            result["write_error"] = (
                "Đã chuẩn hoá xong nhưng chưa ghi được ra thư mục kết quả. "
                "Hãy kiểm tra quyền ghi của thư mục đó trong tab Cài đặt."
            )
            result["write_error_detail"] = f"{type(err).__name__}: {err}"
        result["backups"] = backups

        log = [f"Đã chuẩn hoá “{filename}” ({result['cues']} dòng phụ đề)."]
        log.extend(result.get("notes") or [])
        log.extend(
            f"Đã cất bản cũ thành “{Path(p).name}” (cùng thư mục) trước khi ghi file mới, "
            "vì bản cũ khác bản mới — có thể là bản bạn đã sửa tay."
            for p in backups
        )
        job = jobs.add_finished(
            source=filename or "file .srt",
            mode=MODE_FIX,
            message=_fix_message(result),
            result={
                "srt": written or None,
                "vi_srt": written_vi or None,
                "has_errors": bool(result["summary"].get("error")),
                "backups": list(backups),
            },
            findings=result["findings"],
            summary=result["summary"],
            out_dir=str(out_dir),
            log=log,
        )
        result["filename"] = f"{stem}.srt"
        result["path"] = written
        result["vi_filename"] = f"{stem}_vi.srt" if result.get("has_vi") else ""
        result["vi_path"] = written_vi
        result["job"] = job.public_dict()
        return ok(result)

    # -- trình sửa phụ đề trực tiếp ----------------------------------------- #

    def _job_or_none(job_id: str) -> Job | None:
        return jobs.get(job_id)

    @app.get("/api/jobs/{job_id}/doc")
    async def get_doc(job_id: str) -> Any:
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        try:
            data = await asyncio.to_thread(editor_document, job)
        except JobError as err:
            return fail(str(err), status=409)
        except OSError as err:
            return fail(
                "Không đọc được file phụ đề. Có thể file đang mở trong phần mềm khác, "
                "hoặc bạn không có quyền đọc thư mục chứa nó.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )
        except Exception as err:
            return fail(
                "Không mở được file phụ đề này trong trình sửa. Có thể nó không đúng "
                "định dạng .srt bốn dòng, hoặc bị hỏng ở một dòng nào đó.",
                status=422,
                detail=f"{type(err).__name__}: {err}",
            )
        return ok(data)

    @app.put("/api/jobs/{job_id}/doc")
    async def put_doc(job_id: str, request: Request) -> Any:
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        body = await _json_body(request)
        profile = str(body.get("profile") or "").strip() or None

        if body.get("revert"):
            try:
                data = await asyncio.to_thread(revert_editor_document, job)
            except JobError as err:
                return fail(str(err), status=409)
            except io_utils.KeepCopyError as err:
                # Câu của `keep_old_copy` đã nói đủ: file nào, vì sao, và rằng nó
                # KHÔNG bị ghi đè.
                return fail(str(err), status=409)
            except OSError as err:
                return fail(
                    "Không ghi lại được file phụ đề. Hãy kiểm tra xem file có đang mở "
                    "trong phần mềm khác không.",
                    status=500,
                    detail=f"{type(err).__name__}: {err}",
                )
            return ok(data)

        cues = body.get("cues")
        if isinstance(body.get("doc"), dict):
            cues = body["doc"].get("cues", cues)
        if not isinstance(cues, list) or not cues:
            return fail(
                "Chưa nhận được dòng phụ đề nào để lưu. Hãy tải lại trang rồi thử lại."
            )
        if len(cues) > MAX_EDIT_CUES:
            return fail(
                f"File này có tới {len(cues)} dòng phụ đề, vượt mức tool xử lý được "
                f"({MAX_EDIT_CUES}). Hãy chia nhỏ file rồi sửa từng phần.",
                status=413,
            )
        try:
            data = await asyncio.to_thread(
                save_editor_document, job, cues, profile=profile
            )
        except JobError as err:
            return fail(str(err), status=409)
        except OSError as err:
            return fail(
                "Không ghi được file phụ đề. Hãy kiểm tra xem file có đang mở trong "
                "phần mềm khác không, hoặc bạn có quyền ghi vào thư mục đó không. "
                "Bản cũ trên đĩa vẫn còn nguyên.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )
        except Exception as err:
            return fail(
                "Không lưu được bản sửa này. Bản cũ trên đĩa vẫn còn nguyên.",
                status=422,
                detail=f"{type(err).__name__}: {err}",
            )
        return ok(data)

    @app.get("/api/jobs/{job_id}/draft")
    def get_draft(job_id: str) -> Any:
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        try:
            return ok(read_draft(job))
        except JobError as err:
            return fail(str(err), status=409)

    @app.put("/api/jobs/{job_id}/draft")
    async def put_draft(job_id: str, request: Request) -> Any:
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        body = await _json_body(request)
        draft = body.get("draft", None) if "draft" in body else body or None
        try:
            return ok(write_draft(job, draft))
        except JobError as err:
            return fail(str(err), status=409)
        except (OSError, TypeError, ValueError) as err:
            return fail(
                "Chưa giữ được bản nháp lần này. Bản sửa trên màn hình vẫn còn — "
                "hãy bấm Lưu để ghi thẳng ra file phụ đề.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )

    @app.delete("/api/jobs/{job_id}/draft")
    def delete_draft(job_id: str) -> Any:
        """Xoá bản nháp. Giao diện gọi đường này làm nước hai.

        Nút "Bỏ bản nháp" gọi `PUT {draft: null}` trước; chỉ khi lời gọi đó hỏng
        nó mới thử `DELETE`. Trước đây máy chủ không có route DELETE nào nên nước
        hai trả 405 kèm câu "Bản chương trình đang chạy chưa làm được việc này" —
        một câu sai, vì việc này làm được, chỉ là gọi nhầm cửa.
        """
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        try:
            return ok(write_draft(job, None))
        except JobError as err:
            return fail(str(err), status=409)
        except (OSError, TypeError, ValueError) as err:
            return fail(
                "Chưa bỏ được bản nháp. Bản sửa trên màn hình vẫn còn nguyên.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )

    @app.post("/api/retokenize")
    async def post_retokenize(request: Request) -> Any:
        body = await _json_body(request)
        zh = str(body.get("zh") or body.get("text") or "")
        if not zh.strip():
            return fail("Chưa có dòng chữ Hán nào để tách cụm.")
        if len(zh) > MAX_EDIT_LINE:
            return fail(
                f"Dòng này dài {len(zh)} ký tự, vượt mức {MAX_EDIT_LINE} mà một dòng "
                "phụ đề được phép có. Hãy tách thành nhiều dòng.",
                status=413,
            )
        video_id = str(body.get("video_id") or "").strip()
        if video_id and not VIDEO_ID_RE.match(video_id):
            return fail("Mã video không hợp lệ.", status=400)
        capitalize = body.get("capitalize")
        try:
            data = await asyncio.to_thread(
                retokenize_text,
                zh,
                video_id=video_id,
                profile=str(body.get("profile") or "").strip() or None,
                capitalize=True if capitalize is None else bool(capitalize),
            )
        except JobError as err:
            return fail(str(err))
        except ImportError as err:
            return fail(
                "Chưa cài đủ thư viện tách từ tiếng Trung nên chưa sinh lại pinyin được. "
                "Hãy chạy lại bộ cài đặt.",
                status=503,
                detail=f"{type(err).__name__}: {err}",
            )
        except RuntimeError as err:
            return fail(str(err), status=503)
        except Exception as err:
            return fail(
                "Không tách được cụm cho câu này. Hãy kiểm tra lại dòng chữ Hán.",
                status=422,
                detail=f"{type(err).__name__}: {err}",
            )
        return ok(data)

    @app.post("/api/open-local")
    async def open_local(request: Request) -> Any:
        """Mở `.srt` vào trình sửa: `{path[, vi_path]}` **hoặc** `{name, content[, vi_content]}`.

        Dạng thứ hai là thứ trình duyệt gửi khi người dùng chọn file bằng hộp
        thoại hay kéo thả — trình duyệt không bao giờ lộ đường dẫn thật, chỉ có
        tên và nội dung. Có `path` thì ưu tiên `path` (ghi thẳng vào file gốc).
        """
        try:
            body = await _json_body_capped(request, OPEN_LOCAL_MAX_BODY)
        except _UploadError as err:
            return fail(str(err), status=err.status)
        path = str(body.get("path") or "").strip()
        try:
            if not path and "content" in body:
                job, data = await asyncio.to_thread(
                    open_local_content,
                    jobs,
                    str(body.get("name") or body.get("filename") or ""),
                    body.get("content"),
                    body.get("vi_content"),
                )
            else:
                job, data = await asyncio.to_thread(
                    open_local_pair, jobs, path, str(body.get("vi_path") or "")
                )
        except ContentTooLarge as err:
            return fail(str(err), status=413)
        except JobError as err:
            return fail(str(err), detail=str(getattr(err, "detail", "") or ""))
        except OSError as err:
            return fail(
                "Không đọc được file phụ đề. Có thể file đang bị khoá quyền đọc, "
                "hoặc ổ đĩa chứa nó vừa bị rút ra.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )
        except Exception as err:
            return fail(
                "Không mở được file này. Có thể nó không đúng định dạng .srt bốn dòng.",
                status=422,
                detail=f"{type(err).__name__}: {err}",
            )
        return ok({"job": job.public_dict(), "doc": data}, status=201)

    def _serve_media(job_id: str, request: Request, kind: str) -> Any:
        """Phát một file media của công việc, **có hỗ trợ HTTP Range**.

        Không có Range thì thẻ <audio>/<video> của trình duyệt không tua được,
        mà “nghe lại từng câu” và “xem trước có phụ đề” đều sống bằng việc tua.

        Phần Range được tự làm chứ không giao cho thư viện: `FileResponse` chỉ
        biết Range từ Starlette 0.45 trở đi, còn bản ghim trong
        `requirements-macos.txt` cũ hơn thế. Một tính năng bắt buộc không được
        phụ thuộc vào việc máy người dùng tình cờ cài bản thư viện nào.
        """
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        if kind == "video":
            path = job_video_file(job)
            missing = (
                "Công việc này chưa có video để xem trước. Bấm “Tải video để xem trước” "
                "trong trình sửa, hoặc chạy lại video từ tab Tạo phụ đề."
            )
            media = video_media_type(path) if path else ""
        else:
            path = job_audio_file(job)
            missing = (
                "Chưa có file âm thanh cho công việc này nên không nghe lại được. "
                "File âm thanh chỉ có sau khi tool đã tải và xử lý xong video."
            )
            media = audio_media_type(path) if path else ""
        if path is None:
            return fail(missing, status=404)
        try:
            size = path.stat().st_size
        except OSError as err:
            return fail(
                "Không đọc được file của công việc này nên chưa phát lại được.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )

        raw_range = request.headers.get("range")
        if not raw_range or size <= 0:
            return FileResponse(
                path,
                media_type=media,
                headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
            )

        try:
            span = parse_byte_range(raw_range, size)
        except _RangeNotSatisfiable:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
            )

        # `span is None` = header Range không hiểu được. RFC bảo bỏ qua nó và trả
        # nguyên file, nên vẫn 200 chứ không phải 206.
        start, end = span if span is not None else (0, size - 1)
        status = 206 if span is not None else 200
        length = end - start + 1
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Cache-Control": "no-store",
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(
            _iter_byte_range(path, start, length),
            status_code=status,
            media_type=media,
            headers=headers,
        )

    @app.get("/api/jobs/{job_id}/audio")
    def job_audio(job_id: str, request: Request) -> Any:
        return _serve_media(job_id, request, "audio")

    @app.get("/api/jobs/{job_id}/video")
    def job_video(job_id: str, request: Request) -> Any:
        return _serve_media(job_id, request, "video")

    @app.get("/api/jobs/{job_id}/media")
    def job_media(job_id: str) -> Any:
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        return ok(job_media_info(job))

    @app.get("/api/jobs/{job_id}/translation")
    def job_translation(job_id: str) -> Any:
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        return ok(translation_status(job))

    @app.get("/api/jobs/{job_id}/peaks")
    def job_peaks_route(job_id: str) -> Any:
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        data = job_peaks(job)
        if data is None:
            return fail("Công việc này chưa có file tiếng để vẽ dạng sóng.", status=404)
        return ok(data)

    @app.get("/api/jobs/{job_id}/review")
    def get_review(job_id: str) -> Any:
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        return ok({"reviewed": read_review(job)})

    @app.put("/api/jobs/{job_id}/review")
    async def put_review(job_id: str, request: Request) -> Any:
        job = _job_or_none(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        body = await _json_body(request)
        try:
            saved = write_review(job, (body or {}).get("reviewed"))
        except JobError as err:
            return fail(str(err), status=400)
        except OSError as err:
            return fail("Không ghi được dấu đã soát vào thư mục làm việc.", status=500, detail=str(err))
        return ok({"reviewed": saved})

    @app.post("/api/translate-line")
    async def translate_line(request: Request) -> Any:
        body = await _json_body(request)
        if not isinstance(body, dict):
            return fail("Thiếu dữ liệu.", status=400)
        job = _job_or_none(str(body.get("job_id") or ""))
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        try:
            result = await asyncio.to_thread(
                translate_one_line,
                job,
                str(body.get("zh") or ""),
                list(body.get("before") or []),
                list(body.get("after") or []),
            )
        except JobError as err:
            return fail(str(err), status=400, detail=str(getattr(err, "detail", "") or ""))
        return ok(result)

    # -- cài đặt, kiểm tra máy, tên riêng ----------------------------------- #

    @app.get("/api/settings")
    def get_settings() -> Any:
        settings = public_settings()
        # `models`, `api_key_masked`, `api_key_set` và `work_dir` được trả **cả**
        # ở ngoài lẫn trong `settings`: hợp đồng với giao diện nêu chúng ở ngoài,
        # còn phần còn lại của giao diện đọc mọi thứ qua một bảng cài đặt duy
        # nhất. Nhân đôi bốn khoá rẻ hơn nhiều so với một màn hình trống.
        return ok(
            {
                "settings": settings,
                "models": settings["models"],
                "api_key_masked": settings["api_key_masked"],
                "api_key_set": settings["api_key_set"],
                "work_dir": settings["work_dir"],
            }
        )

    @app.post("/api/settings")
    async def post_settings(request: Request) -> Any:
        body = await _json_body(request)
        patch = body.get("settings") if isinstance(body.get("settings"), dict) else body
        if not isinstance(patch, dict):
            return fail("Dữ liệu cài đặt gửi lên không đúng dạng. Hãy tải lại trang rồi thử lại.")
        _clean, unknown = normalize_settings_patch(patch)
        try:
            saved = await asyncio.to_thread(save_settings, dict(patch))
        except SettingsError as err:
            return fail(str(err), detail=err.detail)
        except JobError as err:
            return fail(str(err))
        except OSError as err:
            return fail(
                "Không ghi được file cài đặt. Có thể ổ đĩa đã đầy hoặc thư mục dữ liệu "
                "của tool đang bị khoá quyền ghi.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )
        settings = public_settings(saved)
        payload: dict[str, Any] = {
            "settings": settings,
            "models": settings["models"],
            "saved": True,
            "unknown_keys": unknown,
        }
        if unknown:
            payload["message"] = (
                "Đã lưu cài đặt, nhưng bản chương trình này chưa hiểu "
                f"{len(unknown)} mục nên chúng không được lưu: "
                + ", ".join(unknown)
                + "."
            )
        else:
            payload["message"] = "Đã lưu cài đặt."
        return ok(payload)

    @app.post("/api/settings/test-key")
    async def post_test_key(request: Request) -> Any:
        body = await _json_body(request)
        raw = body.get("api_key")
        if raw is None:
            raw = body.get("key")
        try:
            result = await asyncio.to_thread(test_api_key, str(raw or ""))
        except Exception as err:  # noqa: BLE001 - phép thử không được trả 500
            return ok(
                {
                    "ok": False,
                    "message": "Không thử được mã API lúc này. Hãy thử lại sau ít phút.",
                    "detail": f"{type(err).__name__}: {err}",
                }
            )
        return ok(result)

    @app.get("/api/doctor")
    def get_doctor() -> Any:
        return ok(doctor_payload())

    @app.post("/api/actions/{key}")
    async def post_action(key: str, request: Request) -> Any:
        """Chạy một việc trong **danh sách trắng cứng**; khoá lạ bị từ chối ngay.

        `key` không bao giờ trở thành tên lệnh — nó chỉ dùng để tra bảng
        `_ACTION_RUNNERS`. Xem chú thích dài ở phần "nút hành động".
        """
        name = action_key(key)
        if not name:
            return fail(unknown_action_message(key), status=400)
        params = action_params(name, await _json_body(request))

        if name in LONG_ACTIONS:
            try:
                job = start_action_job(jobs, name, params)
            except JobError as err:
                return fail(str(err))
            data = job.public_dict()
            return ok(
                {
                    "ok": True,
                    "long": True,
                    "job_id": job.id,
                    "job": data,
                    "action": name,
                    "message": data["message"] or _ACTION_TITLES.get(name, "Đang chạy…"),
                },
                status=202,
            )

        try:
            result = await asyncio.to_thread(run_action, name)
        except JobError as err:
            return fail(str(err), detail=str(getattr(err, "detail", "") or ""))
        except OSError as err:
            return fail(
                "Không chạy được việc này trên máy của bạn.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )
        return ok({"ok": True, "long": False, "action": name, **result})

    @app.post("/api/actions")
    @app.post("/api/actions/{key:path}")
    async def post_action_malformed(key: str = "") -> Any:
        """Khoá rỗng hoặc có `/` (`../`, `install_ffmpeg; rm -rf /`): 400, không chạy gì.

        Route `{key}` ở trên không khớp những đường dẫn này, nên trước đây chúng
        rơi xuống route tĩnh (chỉ nhận GET) và nhận 405 "chưa làm được việc này"
        — một câu gợi ý sai rằng việc ấy có tồn tại. Handler này cố ý không đọc
        thân yêu cầu và không tra bảng: không có gì để chạy cả.
        """
        return fail(unknown_action_message(key), status=400)

    @app.get("/api/names")
    def get_names_index() -> Any:
        movies = list_name_tables()
        # `movies` là tên trong hợp đồng; `items` là tên bản giao diện cũ đang
        # đọc. Cùng một danh sách, hai cái tên — để hai bên đổi tên không cần
        # cùng một lúc.
        return ok({"movies": movies, "items": movies, "count": len(movies)})

    @app.get("/api/names/{video_id}")
    def get_names(video_id: str) -> Any:
        if not VIDEO_ID_RE.match(video_id):
            return fail("Mã video không hợp lệ.", status=400)
        return ok(read_names(video_id))

    @app.put("/api/names/{video_id}")
    async def put_names(video_id: str, request: Request) -> Any:
        """Lưu bảng tên riêng người dùng vừa sửa.

        Nhận đúng `{"entries": [{zh, pinyin, vi}]}` mà giao diện gửi. Trước đây
        route này lấy nguyên thân yêu cầu làm bảng "chữ Hán → pinyin", nên tên
        vừa gõ bị vứt sạch **trong khi giao diện vẫn báo "Đã lưu N tên"** — xem
        docstring của `write_names`.
        """
        if not VIDEO_ID_RE.match(video_id):
            return fail("Mã video không hợp lệ.", status=400)
        if not work_dir_for(video_id).is_dir() and not names_file(video_id).is_file():
            # Chỉ lập bảng cho phim có thật trong work/: một mã gõ nhầm không được
            # âm thầm đẻ ra một thư mục "phim" rỗng trong danh sách.
            return fail(
                "Không tìm thấy phim này trong thư mục làm việc. Hãy tải lại trang "
                "rồi chọn lại phim ở ô “Phim”.",
                status=404,
            )
        body = await _json_body(request)

        entries: Any = None
        if isinstance(body.get("entries"), (list, tuple)):
            entries = body["entries"]
        elif isinstance(body.get("names"), (list, tuple, dict)):
            entries = body["names"]
        # Thân rỗng hoặc JSON hỏng (`_json_body` trả `{}` cho cả hai) KHÔNG phải
        # lệnh "xoá hết": trước đây nhánh `not body -> []` biến một yêu cầu gửi
        # hỏng thành một lần xoá sạch bảng, trả 200. Xoá hết có chủ ý thì giao
        # diện gửi `{"entries": []}` và đi qua nhánh đầu tiên.
        if entries is None:
            return fail(
                "Chưa nhận được bảng tên riêng nào để lưu. Hãy tải lại trang rồi thử lại."
            )
        if isinstance(entries, (list, tuple)) and len(entries) > MAX_EDIT_CUES:
            return fail(
                f"Bảng tên có tới {len(entries)} dòng, vượt mức tool xử lý được.",
                status=413,
            )

        try:
            data = await asyncio.to_thread(write_names, video_id, entries)
        except JobError as err:
            return fail(str(err))
        except OSError as err:
            return fail(
                "Không ghi được bảng tên riêng. Hãy kiểm tra xem file names.json có "
                "đang mở trong phần mềm khác không.",
                status=500,
                detail=f"{type(err).__name__}: {err}",
            )
        count = int(data.get("count") or 0)
        data["ok"] = True
        data["saved"] = True
        data["message"] = f"Đã lưu {count} tên riêng. Lần chạy sau sẽ dùng bảng này."
        backup_name = str(data.get("backup_name") or "")
        if backup_name:
            data["message"] += (
                " File bảng tên cũ bị hỏng không bị xoá: tool đã giữ nó lại với tên "
                f"“{backup_name}” trong cùng thư mục."
            )
        return ok(data)

    # -- tải file và mở thư mục --------------------------------------------- #

    @app.get("/api/download/{job_id}/{kind}")
    def download(job_id: str, kind: str) -> Any:
        job = jobs.get(job_id)
        if job is None:
            return fail("Không tìm thấy công việc này.", status=404)
        if kind not in _DOWNLOAD_KINDS:
            return fail("Loại file không hợp lệ.", status=400)
        raw = (job.result or {}).get(kind)
        if not raw:
            return fail(
                "Công việc này chưa có file loại đó. Hãy chạy lại hoặc xem báo cáo.",
                status=404,
            )
        target = _safe_path(str(raw), _allowed_roots(job))
        if target is None or not target.is_file():
            return fail(
                "File kết quả không còn ở chỗ cũ. Có thể nó đã bị di chuyển hoặc xoá.",
                status=410,
            )
        media, disposition = _DOWNLOAD_KINDS[kind]
        return FileResponse(
            target,
            media_type=media,
            filename=target.name,
            content_disposition_type=disposition,
        )

    @app.post("/api/reveal")
    async def reveal(request: Request) -> Any:
        body = await _json_body(request)
        job = jobs.get(str(body.get("job") or body.get("job_id") or ""))
        raw = str(body.get("path") or "").strip()
        if not raw and job is not None:
            raw = str((job.result or {}).get("srt") or job.out_dir or "")
        if not raw:
            raw = _effective_out_dir(_safe_cfg(), load_settings())

        target = _safe_path(raw, _allowed_roots(job))
        if target is None:
            return fail("Không mở được thư mục này vì nó nằm ngoài thư mục kết quả.", status=403)
        if not target.exists():
            target = target.parent
        if not target.exists():
            return fail("Thư mục kết quả chưa tồn tại. Hãy chạy xong một video trước.", status=404)
        try:
            await asyncio.to_thread(reveal_in_file_manager, target)
        except OSError as err:
            return fail(
                "Không mở được cửa sổ thư mục. Bạn có thể mở tay theo đường dẫn bên dưới.",
                status=500,
                path=str(target),
                detail=f"{type(err).__name__}: {err}",
            )
        return ok({"opened": True, "path": str(target)})

    @app.post("/api/shutdown")
    def shutdown() -> Any:
        """Tắt máy chủ sạch sẽ từ trong giao diện.

        Dừng việc đang chạy trước, ghi trạng thái, rồi mới bảo uvicorn thoát —
        thứ tự đó là điều khác nhau giữa "tắt" và "giết".
        """
        server = getattr(app.state, "uvicorn_server", None)
        if server is None:
            return fail(
                "Bản đang chạy không tự tắt được. Hãy đóng cửa sổ Terminal của srtgen.",
                status=409,
            )
        jobs.shutdown(wait=2.0)

        def stop() -> None:
            time.sleep(0.4)  # đủ để trả lời xong cho trình duyệt rồi mới thoát
            server.should_exit = True

        threading.Thread(target=stop, name="srtgen-shutdown", daemon=True).start()
        return ok({"stopping": True, "message": "Đang tắt srtgen. Bạn có thể đóng tab này."})

    # -- trang hướng dẫn sử dụng ------------------------------------------- #

    @app.get("/guide", include_in_schema=False)
    def guide_page() -> Any:
        """HUONG-DAN.md dạng trang web đọc được (H7), để mở ở tab mới.

        Trước đây nút “Mở hướng dẫn sử dụng” nhờ hệ điều hành mở file `.md` thô:
        máy không có ứng dụng đọc `.md` thì không có gì hiện ra, mà tool vẫn báo
        “Đã mở”. Trình duyệt thì chắc chắn có — người dùng đang đứng trong nó.
        """
        guide = _guide_file()
        if guide is None:
            return HTMLResponse(guide_missing_html(), status_code=404)
        try:
            text = io_utils.read_text(guide)
        except (OSError, ValueError) as err:
            return HTMLResponse(
                guide_missing_html(f"{type(err).__name__}: {err}"), status_code=500
            )
        return HTMLResponse(render_guide_html(text), headers={"Cache-Control": "no-store"})

    # -- file tĩnh còn lại (đăng ký CUỐI CÙNG để không che các route /api) --- #

    @app.get("/{path:path}", include_in_schema=False)
    def static_fallback(path: str) -> Any:
        if path.startswith("api/"):
            return fail("Không có chức năng này.", status=404)
        target = _static_file(path)
        if target is None:
            return fail("Không tìm thấy trang này.", status=404)
        return FileResponse(target)

    return app


def _hostname(netloc: str) -> str:
    """Lấy phần host của `Host:`/`Origin:`, xử lý đúng cả dạng IPv6 `[::1]:8756`."""
    text = str(netloc or "").strip().lower()
    if text.startswith("["):
        end = text.find("]")
        return text[: end + 1] if end > 0 else text
    return text.split(":", 1)[0]


def _split_netloc(netloc: str) -> tuple[str, int | None]:
    """`127.0.0.1:8756` → `("127.0.0.1", 8756)`; không ghi cổng → `None`; cổng hỏng → `-1`.

    `-1` chứ không phải `None` cho cổng hỏng: `None` nghĩa là "theo mặc định của
    giao thức", còn một cổng viết sai thì không được coi là khớp với cái gì cả.
    """
    text = str(netloc or "").strip().lower()
    host = _hostname(text)
    rest = text[len(host):]
    if not rest:
        return host, None
    if rest.startswith(":") and rest[1:].isdigit():
        return host, int(rest[1:])
    return host, -1


def _host_allowed(netloc: str, own_port: int | None) -> bool:
    """`Host:` phải là tên của chính máy này; có ghi cổng thì cổng phải là cổng của ta."""
    host, port = _split_netloc(netloc)
    if host not in ALLOWED_HOSTS:
        return False
    return not (own_port and port is not None and port != own_port)


def _origin_allowed(origin: str, own_port: int | None) -> bool:
    """`Origin:` phải khớp **cả host lẫn cổng** của chính máy chủ này.

    So cả cổng là điểm mấu chốt (audit đợt 3): chỉ so tên host thì một trang web
    do *ứng dụng khác* phục vụ ở `127.0.0.1:3000` vẫn gửi được lệnh tới
    `/api/actions/*`. Origin không ghi cổng thì lấy cổng mặc định của giao thức
    (http → 80), đúng như trình duyệt hiểu.

    `Origin` không có phần host (`null` của iframe sandbox) được để qua ở đây
    như trước — `Sec-Fetch-Site` trong middleware là thứ quyết định ca đó.
    `own_port=None` (dựng app không gắn cổng, chỉ trong kiểm thử) thì chỉ so host.
    """
    parsed = urlparse(str(origin or "").strip())
    if not parsed.netloc:
        return True
    host, port = _split_netloc(parsed.netloc)
    if host not in ALLOWED_HOSTS:
        return False
    if own_port:
        if port is None:
            port = {"http": 80, "https": 443}.get(parsed.scheme.lower(), -1)
        if port != own_port:
            return False
    return True


def _static_file(rel: str) -> Path | None:
    """Đường dẫn file tĩnh nếu nó thật sự nằm trong `static/`, không thì `None`."""
    if not rel or rel.endswith("/"):
        return None
    root = STATIC_DIR.resolve()
    try:
        target = (STATIC_DIR / rel).resolve()
    except (OSError, ValueError):
        return None
    if target != root and not target.is_relative_to(root):
        return None
    return target if target.is_file() else None


def _sse(event: str, data: Any, event_id: Any = None) -> str:
    """Một khung `text/event-stream`.

    JSON được ép về một dòng (`ensure_ascii=False`, không xuống dòng) vì SSE dùng
    ký tự xuống dòng làm ranh giới bản ghi — một dấu `\\n` lọt vào là hỏng khung.
    """
    body = json.dumps(data, ensure_ascii=False, allow_nan=False)
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {body}\n\n"


def _last_event_id(request: Any) -> int:
    """Vị trí dòng nhật ký cuối cùng trình duyệt đã nhận, khi nó tự kết nối lại."""
    raw = request.headers.get("last-event-id")
    try:
        return int(raw) + 1
    except (TypeError, ValueError):
        return -1


async def _json_body(request: Any) -> dict[str, Any]:
    """Đọc thân yêu cầu dạng JSON, coi thân rỗng hoặc sai cú pháp là `{}`.

    Trả `{}` thay vì ném lỗi để mỗi endpoint tự nói câu tiếng Việt của nó về cái
    còn thiếu, thay vì trình duyệt nhận một lỗi 422 khó hiểu của thư viện.
    """
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class _UploadError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


async def _read_upload(request: Any) -> tuple[str, bytes]:
    """Đọc file .srt người dùng gửi lên, theo cả ba cách gửi thường gặp.

    * `multipart/form-data` — ô chọn file kiểu cổ điển. Cần thư viện
      `python-multipart`; thiếu nó thì hướng dẫn cách gửi khác chứ không sập.
    * `application/json` với khoá `text` — tiện cho việc dán thẳng nội dung.
    * Thân yêu cầu thô — `fetch(url, {method: "POST", body: file})`. Đây là cách
      **được khuyến nghị**: không cần thư viện phụ nào, và đọc theo luồng nên chặn
      được file quá lớn trước khi nó chiếm hết bộ nhớ.
    """
    ctype = str(request.headers.get("content-type") or "").lower()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise _UploadError(_TOO_BIG, status=413)

    if ctype.startswith("multipart/form-data"):
        try:
            import multipart  # noqa: F401
        except ImportError:
            raise _UploadError(
                "Bản cài này chưa hỗ trợ tải file theo kiểu biểu mẫu. "
                "Hãy cập nhật giao diện, hoặc dán trực tiếp nội dung file .srt."
            ) from None
        form = await request.form()
        upload = form.get("file") or form.get("srt") or form.get("upload")
        if upload is None or not hasattr(upload, "read"):
            raise _UploadError("Bạn chưa chọn file .srt nào.")
        data = await upload.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise _UploadError(_TOO_BIG, status=413)
        return str(getattr(upload, "filename", "") or "phu-de.srt"), bytes(data)

    if ctype.startswith("application/json"):
        body = await _json_body(request)
        text = str(body.get("text") or "")
        if not text.strip():
            raise _UploadError("Không có nội dung file .srt nào để xử lý.")
        data = text.encode("utf-8")
        if len(data) > MAX_UPLOAD_BYTES:
            raise _UploadError(_TOO_BIG, status=413)
        return str(body.get("filename") or "phu-de.srt"), data

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise _UploadError(_TOO_BIG, status=413)
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data.strip():
        raise _UploadError("Không có nội dung file .srt nào để xử lý.")
    filename = request.query_params.get("filename") or "phu-de.srt"
    return str(filename), data


_TOO_BIG = (
    f"File quá lớn (tối đa {MAX_UPLOAD_BYTES // (1024 * 1024)}MB). "
    "Bạn có chắc đã chọn đúng file .srt chứ không phải file video?"
)


async def _json_body_capped(request: Any, cap: int) -> dict[str, Any]:
    """Như `_json_body`, nhưng đọc theo luồng và dừng ngay khi thân vượt `cap` byte.

    `request.json()` nạp nguyên thân vào RAM trước rồi mới cho xem; với một yêu
    cầu gửi kèm nội dung file thì trần phải được soát **trong lúc** đọc.
    """
    declared = str(request.headers.get("content-length") or "").strip()
    if declared.isdigit() and int(declared) > cap:
        raise _UploadError(_TOO_BIG, status=413)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise _UploadError(_TOO_BIG, status=413)
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- #
# tải file âm thanh/video lên — POST /api/jobs dạng multipart
# --------------------------------------------------------------------------- #

def max_media_bytes(cfg: dict[str, Any] | None = None) -> int:
    """Trần dung lượng file âm thanh/video: `web.max_media_bytes`, mặc định 4 GiB."""
    cfg = cfg if cfg is not None else _safe_cfg()
    web = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
    value = _num(web.get("max_media_bytes"))
    return int(value) if value > 0 else MAX_MEDIA_BYTES


def uploads_dir(cfg: dict[str, Any] | None = None) -> Path:
    """`<work_dir>/_uploads/` — chỗ duy nhất máy chủ ghi file người dùng tải lên.

    Nằm trong `work/` (không phải thư mục tạm của hệ thống) vì hai lẽ: cùng ổ
    đĩa với file wav mà S0 sắp ghi ra, và thư mục tạm của macOS có thể bị dọn
    giữa lúc một video 40 phút còn đang xếp hàng.
    """
    return work_root(cfg) / UPLOADS_DIR_NAME


def _media_too_big(limit: int) -> str:
    return (
        f"File quá lớn: tool nhận file âm thanh hoặc video tối đa {_size_text(limit)}. "
        "Hãy cắt video thành các đoạn ngắn hơn, hoặc chỉ lấy phần âm thanh (mp3, m4a) "
        "rồi kéo thả lại."
    )


def _media_type_problem(filename: str) -> str:
    shown = str(filename or "").strip()[:80] or "không có tên"
    return (
        f"Tool chưa nhận loại file “{shown}”. Chỉ nhận file âm thanh hoặc video có "
        f"đuôi: {', '.join(MEDIA_EXTENSIONS)}."
    )


@dataclass
class MediaUpload:
    """Một file đã ghi xong xuống `work/_uploads/`, kèm các trường chữ đi cùng."""

    path: Path
    filename: str
    title: str
    size: int
    fields: dict[str, str]


def _multipart_module() -> Any:
    """`python_multipart.multipart` (bản mới) hoặc `multipart.multipart` (bản cũ); thiếu → `None`."""
    try:
        from python_multipart import multipart as module
    except ImportError:
        try:
            from multipart import multipart as module  # type: ignore[no-redef]
        except ImportError:
            return None
    return module


class _MultipartReceiver:
    """Bộ gom sự kiện của trình phân tích multipart dạng luồng.

    Trình phân tích gọi lại **đồng bộ**, ngay trong lúc nó nhai một mảnh byte;
    việc ghi đĩa thì phải đi ra luồng phụ để không chặn vòng lặp sự kiện. Nên
    lớp này chỉ gom byte của file vào `pending`, còn `receive_media_upload` đẩy
    từng khối đầy xuống đĩa bằng `asyncio.to_thread(flush)`.

    Tên file gốc chỉ được đọc để lấy **đuôi** (kiểm danh sách trắng) và **tiêu
    đề** (qua `safe_stem`). Tên file trên đĩa luôn là `uuid4().hex + đuôi`.
    """

    def __init__(self, module: Any, dest_dir: Path, limit: int) -> None:
        self.module = module
        self.dest_dir = dest_dir
        self.limit = int(limit)
        self.writer: io_utils.BinaryStreamWriter | None = None
        self.filename = ""
        self.received = 0
        self.size = 0
        self.fields: dict[str, str] = {}
        self.finished = False
        self.pending = bytearray()
        self._field_bytes = 0
        self._part = ""
        self._name = ""
        self._value = bytearray()
        self._header_name = bytearray()
        self._header_value = bytearray()
        self._headers: dict[str, bytes] = {}

    def callbacks(self) -> dict[str, Callable[..., None]]:
        return {
            "on_part_begin": self._on_part_begin,
            "on_part_data": self._on_part_data,
            "on_part_end": self._on_part_end,
            "on_header_field": self._on_header_field,
            "on_header_value": self._on_header_value,
            "on_header_end": self._on_header_end,
            "on_headers_finished": self._on_headers_finished,
            "on_end": self._on_end,
        }

    # -- sự kiện của trình phân tích --------------------------------------- #

    def _on_part_begin(self) -> None:
        self._part = ""
        self._name = ""
        self._value = bytearray()
        self._headers = {}

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def _on_header_end(self) -> None:
        name = bytes(self._header_name).decode("latin-1").strip().lower()
        self._headers[name] = bytes(self._header_value)
        self._header_name = bytearray()
        self._header_value = bytearray()

    def _on_headers_finished(self) -> None:
        _kind, params = self.module.parse_options_header(
            self._headers.get("content-disposition", b"")
        )
        self._name = params.get(b"name", b"").decode("utf-8", "replace")
        raw_filename = params.get(b"filename")
        if raw_filename is None:
            self._part = "field"
            return
        if self.writer is not None:
            raise _UploadError("Mỗi lần chỉ nhận một file. Hãy chọn một file rồi thử lại.")
        # Trình duyệt cũ gửi cả đường dẫn "C:\\…\\phim.mp4": chỉ lấy phần tên.
        name = Path(raw_filename.decode("utf-8", "replace").replace("\\", "/")).name
        ext = Path(name).suffix.lower()
        if ext not in MEDIA_EXTENSIONS:
            raise _UploadError(_media_type_problem(name))
        self.filename = io_utils.nfc(name)
        self.writer = io_utils.BinaryStreamWriter(
            self.dest_dir / f"{uuid.uuid4().hex}{ext}", max_bytes=self.limit
        )
        self._part = "file"

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        size = end - start
        if self._part == "file":
            self.received += size
            if self.received > self.limit:
                raise _UploadError(_media_too_big(self.limit), status=413)
            self.pending += data[start:end]
        elif self._part == "field":
            self._field_bytes += size
            if self._field_bytes > MAX_FORM_FIELDS_BYTES:
                raise _UploadError(
                    "Phần thông tin gửi kèm file lớn bất thường nên tool không nhận. "
                    "Hãy tải lại trang rồi thử lại.",
                    status=413,
                )
            self._value += data[start:end]

    def _on_part_end(self) -> None:
        if self._part == "field" and self._name:
            self.fields[self._name] = io_utils.nfc(bytes(self._value).decode("utf-8", "replace"))
        self._part = ""

    def _on_end(self) -> None:
        self.finished = True

    # -- ghi đĩa (chạy ở luồng phụ) ------------------------------------------ #

    def flush(self) -> None:
        if self.writer is None or not self.pending:
            return
        data = bytes(self.pending)
        self.pending.clear()
        self.writer.write(data)

    def close(self) -> None:
        self.flush()
        if self.writer is not None:
            self.size = self.writer.close()

    def discard(self) -> None:
        self.pending.clear()
        if self.writer is not None:
            self.writer.discard()


async def receive_media_upload(
    chunks: AsyncIterator[bytes],
    content_type: str,
    *,
    dest_dir: Path,
    limit: int,
) -> MediaUpload:
    """Ghi luồng một yêu cầu `multipart/form-data` ra đĩa; trả file đã ghi + các trường chữ.

    Hợp đồng, và lý do của từng điều:

    * **Ghi theo khối 1MB, không bao giờ nạp cả file vào RAM** — video có thể
      nhiều GB, còn máy đích là iMac 2017 đang cần RAM cho chặng gỡ băng.
    * **Vượt `limit` là dừng ngay** và xoá phần đã ghi (413), không đọc tiếp.
    * **Đuôi file lạ bị từ chối trước khi ghi byte nào** (400, câu kể ra các
      đuôi được nhận).
    * **Luồng đứt giữa chừng** (`ClientDisconnect`, huỷ, thân yêu cầu cụt) →
      xoá phần dở rồi ném lại lỗi. Một file 3GB dở dang không ai trỏ tới là ổ đĩa
      người dùng mất đi mà không bao giờ biết vì sao.

    Nhận `chunks` là một async iterator bất kỳ (không phải `Request`) để kiểm thử
    được ca đứt kết nối mà không cần dựng máy chủ thật.
    """
    module = _multipart_module()
    if module is None:
        raise _UploadError(
            "Bản cài này thiếu thư viện nhận file tải lên (python-multipart). "
            "Hãy chạy lại file CaiDat.command trong thư mục cài đặt.",
            status=500,
        )
    _kind, params = module.parse_options_header(content_type)
    boundary = params.get(b"boundary")
    if not boundary:
        raise _UploadError(
            "Yêu cầu tải file lên bị thiếu phần phân cách nên tool không đọc được. "
            "Hãy tải lại trang rồi thử lại."
        )

    receiver = _MultipartReceiver(module, dest_dir, limit)
    parser = module.MultipartParser(boundary, receiver.callbacks())
    try:
        async for chunk in chunks:
            if not chunk:
                continue
            parser.write(chunk)
            if len(receiver.pending) >= io_utils.STREAM_BLOCK_BYTES:
                await asyncio.to_thread(receiver.flush)
        parser.finalize()
        if receiver.writer is None:
            raise _UploadError("Bạn chưa chọn file âm thanh hoặc video nào.")
        if not receiver.finished:
            raise _UploadError(
                "File chưa được đưa vào trọn vẹn (kết nối bị ngắt giữa chừng). "
                "Hãy kéo thả lại file rồi bấm Bắt đầu."
            )
        await asyncio.to_thread(receiver.close)
        if receiver.size <= 0:
            raise _UploadError(f"File “{receiver.filename}” trống (0 byte), không có gì để nghe.")
    except _UploadError:
        receiver.discard()
        raise
    except io_utils.StreamTooLarge:
        receiver.discard()
        raise _UploadError(_media_too_big(limit), status=413) from None
    except BaseException as err:
        receiver.discard()
        if type(err).__module__.split(".")[0] in ("python_multipart", "multipart"):
            raise _UploadError(
                "Dữ liệu tải lên bị hỏng nên tool không đọc được. Hãy thử lại."
            ) from err
        raise

    assert receiver.writer is not None
    return MediaUpload(
        path=receiver.writer.path,
        filename=receiver.filename,
        title=io_utils.safe_stem(Path(receiver.filename).stem, fallback="video"),
        size=receiver.size,
        fields=dict(receiver.fields),
    )


def _upload_options(raw: str) -> tuple[dict[str, Any], str]:
    """Đọc trường `options` (chuỗi JSON) của yêu cầu tải lên; trả `(options, câu lỗi)`.

    Chuỗi hỏng bị **từ chối** chứ không lặng lẽ coi là `{}`: bỏ qua nó nghĩa là
    bỏ qua lựa chọn của người dùng (tắt dịch, đổi model) mà vẫn chạy như thể
    mọi thứ bình thường — đúng kiểu hỏng im lặng mà tool này cấm.
    """
    text = str(raw or "").strip()
    if not text:
        return {}, ""
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {}, (
            "Các lựa chọn gửi kèm file không đọc được nên tool chưa chạy, để khỏi chạy "
            "sai ý bạn. Hãy tải lại trang rồi thử lại."
        )
    return data, ""


def _discard_upload_file(path: Path) -> None:
    """Xoá một bản tải lên chưa kịp thành công việc — chỉ bên trong `_uploads/`."""
    if path.parent.name != UPLOADS_DIR_NAME:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _sweep_stale_uploads(manager: JobManager, *, max_age: float = UPLOAD_SWEEP_AGE) -> int:
    """Dọn bản tải lên mồ côi (ứng dụng sập giữa lúc tải/chờ) đã quá `max_age` giây.

    Luồng bình thường tự xoá bản tải lên sau S0 (`jobs.release_upload`). Chỗ
    này chỉ nhặt phần còn sót khi tiến trình chết giữa chừng — video vài GB mà
    không ai trỏ tới thì chỉ có tool biết để dọn. Không bao giờ đụng file của
    một công việc còn đang chờ/chạy, và không đụng file mới (có thể một bản
    srtgen thứ hai đang ghi dở nó).
    """
    folder = uploads_dir()
    try:
        items = list(folder.iterdir())
    except OSError:
        return 0
    busy = {
        Path(str(item.get("source") or "")).name
        for item in manager.list(limit=10_000)
        if item.get("active")
    }
    now = time.time()
    removed = 0
    for item in items:
        try:
            if not item.is_file() or item.name in busy:
                continue
            if now - item.stat().st_mtime < max_age:
                continue
            item.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _source_problem(source: str) -> str:
    """Câu tiếng Việt mô tả vấn đề của nguồn, hoặc chuỗi rỗng nếu dùng được."""
    if not source:
        return "Bạn chưa dán link YouTube hoặc chọn file âm thanh."
    parsed = urlparse(source)
    if parsed.scheme in ("http", "https"):
        return ""
    if parsed.scheme and len(parsed.scheme) > 1:
        return f"Không mở được nguồn dạng “{parsed.scheme}://”. Hãy dán link YouTube hoặc chọn file trên máy."
    path = Path(source).expanduser()
    if path.exists():
        return ""
    return (
        f"Không tìm thấy file “{source}” trên máy. "
        "Hãy kiểm tra lại đường dẫn, hoặc kéo thả file vào ô phía trên."
    )


def _check_message(summary: dict[str, Any]) -> str:
    errors = int(summary.get("error", 0) or 0)
    warns = int(summary.get("warn", 0) or 0)
    if not errors and not warns:
        return "File đạt toàn bộ quy chuẩn, không có lỗi nào."
    if errors:
        return f"Tìm thấy {errors} lỗi cần sửa và {warns} chỗ nên xem lại."
    return f"Không có lỗi nào; có {warns} chỗ nên xem lại."


def _fix_message(result: dict[str, Any]) -> str:
    before = int(result.get("summary_before", {}).get("error", 0) or 0)
    after = int(result.get("summary", {}).get("error", 0) or 0)
    if after == 0:
        text = f"Đã chuẩn hoá xong: sửa {before} lỗi, file hiện đạt toàn bộ quy chuẩn."
    else:
        text = (
            f"Đã chuẩn hoá xong, còn {after} lỗi cần người xem lại "
            f"(trước khi sửa có {before} lỗi)."
        )
    vi_count = int(result.get("vi_cues") or 0)
    if vi_count:
        text += f" Đã giữ nguyên {vi_count} dòng tiếng Việt trong file _vi.srt đi kèm."
    kept = len(result.get("set_aside") or [])
    if kept:
        text += f" Có {kept} dòng không xếp được vào file, xem trong danh sách lỗi."
    copies = [Path(str(p)).name for p in (result.get("backups") or [])]
    if copies:
        shown = ", ".join(f"“{name}”" for name in copies)
        text += (
            f" Bản cũ của file kết quả khác bản mới nên đã được cất lại thành {shown} "
            "(cùng thư mục), không mất gì."
        )
    return text


def _unreadable_srt_message(err: BaseException) -> str:
    """Câu tiếng Việt cho một file `.srt` mà bộ kiểm không đọc nổi.

    `err` không đi vào câu trả lời: nguyên văn lỗi của Python là tiếng Anh và
    không nói cho người dùng biết phải làm gì. Chi tiết kỹ thuật vẫn được gửi
    riêng ở trường `detail` cho người hỗ trợ đọc.
    """
    return "Không kiểm được file này. Có thể nó không phải file .srt."


_PLACEHOLDER_HTML = """<!doctype html>
<html lang="vi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>srtgen</title>
<style>
 body{font-family:-apple-system,"Segoe UI",sans-serif;max-width:40rem;margin:4rem auto;
      padding:0 1.5rem;line-height:1.6;color:#1c1c1e}
 code{background:#f1f1f4;padding:.15rem .4rem;border-radius:.25rem}
</style></head><body>
<h1>srtgen đang chạy</h1>
<p>Máy chủ đã sẵn sàng, nhưng phần giao diện chưa được cài đặt
(thiếu file <code>srtgen/web/static/index.html</code>).</p>
<p>Bạn vẫn dùng được các chức năng qua đường dẫn
<code>/api/health</code>, <code>/api/doctor</code>, <code>/api/jobs</code>.</p>
</body></html>
"""


# --------------------------------------------------------------------------- #
# điểm vào
# --------------------------------------------------------------------------- #

#: Đặt biến này (bất kỳ giá trị nào khác rỗng) = **không** mở trình duyệt.
#: `KhoiDong.command` đặt nó vì chính script đó đã `open` trang rồi; thiếu đường
#: đọc biến này thì người dùng nhận hai tab cho một lần bấm.
NO_BROWSER_ENV = "SRTGEN_NO_BROWSER"


def _no_browser_requested() -> bool:
    """Biến môi trường `SRTGEN_NO_BROWSER` có đang bảo đừng mở trình duyệt không.

    Luật cố ý thô: **có đặt và khác rỗng là không mở**. Người viết script gõ
    `SRTGEN_NO_BROWSER=0` với ý "tắt cái cờ này" là chuyện hiếm, còn gõ
    `SRTGEN_NO_BROWSER=1` rồi vẫn thấy tab bật lên là chuyện làm người ta mất
    buổi chiều đi tìm nguyên nhân.
    """
    return bool(str(os.environ.get(NO_BROWSER_ENV) or "").strip())


def serve(
    open_browser: bool = True,
    port: int | None = None,
    host: str | None = None,
) -> None:
    """Chạy giao diện web trên máy này và (tuỳ chọn) mở trình duyệt.

    Đây là điểm vào của người dùng nên nó **được phép `print()`** — và phải in,
    vì nếu thiếu thư viện thì màn hình Terminal là nơi duy nhất còn nói được với
    người dùng. Mọi câu in ra đều bằng tiếng Việt và kèm việc cần làm.

    Ba thứ quyết định có mở trình duyệt hay không, xét theo thứ tự **cấm thắng
    cho phép**: biến môi trường `SRTGEN_NO_BROWSER` → tham số `open_browser`
    (tức cờ `--no-browser`) → ô "tự mở trình duyệt" trong tab Cài đặt. Bất cứ ai
    nói "đừng mở" là không mở.

    `host` có mặt **chỉ để nhận đúng chữ ký mà `srtgen.cli._call_server` thử gọi
    đầu tiên** (`host=…, port=…, open_browser=…`). Không có tham số này thì lời
    gọi đó ném `TypeError`, `cli.py` lui xuống chữ ký `{port}` và cờ
    `--no-browser` biến mất trên đường đi — đúng lỗi audit mục J tìm ra.
    Giá trị của nó **không** được dùng để mở cổng: máy chủ này chỉ nghe ở
    `127.0.0.1`, và đó là ràng buộc an toàn số 1 của cả module (xem docstring
    đầu file). Một host khác chỉ được cảnh báo rồi bỏ qua.
    """
    io_utils.configure_console()

    missing = web_dependencies_missing()
    if missing:
        print(missing)
        return

    import uvicorn

    requested_host = str(host or "").strip()
    if requested_host and _hostname(requested_host) not in ALLOWED_HOSTS:
        print(
            f"Bỏ qua yêu cầu chạy ở địa chỉ “{requested_host}”: srtgen chỉ chạy trên\n"
            "chính máy bạn (127.0.0.1) để không ai trong mạng gửi lệnh vào máy này được."
        )

    cfg = _safe_cfg()
    ui = cfg.get("ui") if isinstance(cfg.get("ui"), dict) else {}
    settings = load_settings()
    if port is None:
        configured = int(ui.get("port") or 0)
        port = configured if configured > 0 else None
    if open_browser and "open_browser" in settings:
        open_browser = bool(settings.get("open_browser"))
    if _no_browser_requested():
        open_browser = False

    app = create_app()

    last_error: OSError | None = None
    for candidate in _port_candidates(port):
        url = f"http://{HOST}:{candidate}/"
        # Cổng thật mà middleware so với `Origin`. Ghi trước khi mở cổng, và ghi
        # lại ở mỗi lần thử: cổng đầu tiên có thể đã bị ứng dụng khác chiếm.
        app.state.port = candidate
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=HOST,
                port=candidate,
                log_level="warning",
                access_log=False,
                # Người dùng bấm Dừng ở tab khác trong lúc một luồng SSE đang mở;
                # đừng bắt họ chờ hết thời gian chờ mặc định khi tắt máy chủ.
                timeout_graceful_shutdown=5,
            )
        )
        app.state.uvicorn_server = server
        _print_banner(url)
        if open_browser:
            _open_browser_when_ready(server, url)
        try:
            server.run()
        except OSError as err:
            last_error = err
            continue
        except KeyboardInterrupt:  # pragma: no cover - phụ thuộc người dùng
            pass
        print("\nĐã tắt srtgen. Hẹn gặp lại.")
        return

    print(
        "Không mở được cổng mạng nào để chạy giao diện.\n"
        f"Lý do: {last_error}\n"
        "Hãy tắt bớt ứng dụng đang chạy rồi thử lại."
    )


def _port_candidates(preferred: int | None) -> Iterable[int]:
    """Cổng để thử lần lượt: cổng người dùng chọn trước, rồi dò từ 8756.

    Vẫn phải thử lại dù đã dò cổng trống, vì giữa lúc dò và lúc uvicorn mở cổng
    có một khoảng thời gian mà một ứng dụng khác có thể chen vào.
    """
    seen: set[int] = set()
    if preferred:
        seen.add(int(preferred))
        yield int(preferred)
    try:
        start = find_free_port()
    except RuntimeError:
        start = DEFAULT_PORT
    for candidate in range(start, start + 10):
        if candidate not in seen:
            seen.add(candidate)
            yield candidate


def _print_banner(url: str) -> None:
    print("")
    print("  srtgen đã sẵn sàng.")
    print(f"  Hãy mở trình duyệt và vào địa chỉ: {url}")
    print("  Cửa sổ này cần được để yên trong lúc bạn dùng tool.")
    print("  Muốn tắt: đóng cửa sổ này, hoặc nhấn Ctrl + C ở đây.")
    print("")


def _open_browser_when_ready(server: Any, url: str, timeout: float = 20.0) -> None:
    """Mở trình duyệt sau khi máy chủ thật sự nghe được, không phải trước đó.

    Mở sớm là kiểu lỗi khó chịu nhất: người dùng thấy trang "không kết nối được"
    rồi kết luận tool hỏng, dù chỉ cần bấm tải lại. `server.started` là cờ uvicorn
    bật đúng lúc cổng đã mở.
    """

    def waiter() -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(server, "started", False):
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
                return
            time.sleep(0.15)

    threading.Thread(target=waiter, name="srtgen-open-browser", daemon=True).start()
