"""Dòng lệnh của srtgen — tám lệnh, toàn bộ thông báo bằng tiếng Việt.

Người dùng cuối của tool này không phải dân IT: họ mở Terminal vì được hướng dẫn,
không phải vì quen. Vì vậy file này có ba quy ước, và cả ba đều là quy ước có chủ ý
chứ không phải sở thích trình bày:

* **Mỗi lỗi phải kèm việc cần làm.** Không có câu nào chỉ nói "thiếu ffmpeg" mà
  không nói ngay dòng lệnh phải chạy để có ffmpeg.
* **`doctor` là lệnh quan trọng nhất ở đây.** Phần lớn báo lỗi thực tế sẽ là thiếu
  ffmpeg hoặc `yt-dlp` quá cũ; một lệnh trả lời được điều đó tại chỗ sẽ tiết kiệm
  hơn mọi tính năng khác. Nó phải chạy được **kể cả khi máy chưa cài gì** — nên
  mọi import nặng trong file này đều nằm trong hàm, và cả `typer` lẫn `rich` đều
  có đường lui khi thiếu.
* **Logic không nằm ở đây.** Thứ tự chặng, vòng lặp chạy lại sau khi AI tìm tên
  riêng, và hai lệnh `check`/`fix` đều nằm trong `srtgen.pipeline` để web UI dùng
  chung. File này chỉ lo đọc tham số, vẽ tiến trình và in kết quả.

`doctor_report()` để ở đây (chứ không ở `pipeline.py`) vì nó là chẩn đoán **môi
trường chạy**, cùng họ với việc dựng dòng lệnh cài đặt. Web UI gọi lại chính hàm
này cho `GET /api/doctor` — không có bản chép thứ hai.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from srtgen import io_utils

try:  # typer là thư viện bắt buộc, nhưng "bắt buộc" và "đã cài" là hai chuyện khác nhau
    import typer
except ImportError:  # pragma: no cover - máy cài dở dang
    typer = None  # type: ignore[assignment]

__all__ = [
    "app",
    "main",
    "doctor_report",
    "doctor_checks",
    "Check",
    "STATUS_OK",
    "STATUS_WARN",
    "STATUS_FAIL",
    "STATUS_INFO",
]


# --------------------------------------------------------------------------- #
# In ra màn hình
# --------------------------------------------------------------------------- #

class Out:
    """Lớp in mỏng bọc quanh `rich`, có đường lui về `print` trần.

    Vì sao phải có đường lui: `srtgen doctor` tồn tại để chạy trên máy cài dở dang.
    Nếu chính nó chết vì thiếu `rich` thì người dùng mất đúng cái công cụ dùng để
    biết mình đang thiếu gì.
    """

    def __init__(self) -> None:
        self.console: Any = None
        try:
            from rich.console import Console

            self.console = Console(highlight=False, soft_wrap=False)
        except ImportError:  # pragma: no cover - máy chưa cài rich
            self.console = None

    @property
    def rich(self) -> bool:
        return self.console is not None

    def print(self, text: str = "", style: str = "") -> None:
        if self.console is not None:
            self.console.print(text, style=style or None)
            return
        print(_strip_markup(text))

    def rule(self, title: str = "") -> None:
        if self.console is not None:
            self.console.rule(title)
            return
        print("-" * 70)
        if title:
            print(title)

    def error(self, text: str) -> None:
        self.print(text, style="bold red")

    def warn(self, text: str) -> None:
        self.print(text, style="yellow")

    def good(self, text: str) -> None:
        self.print(text, style="green")

    def dim(self, text: str) -> None:
        self.print(text, style="dim")


def _strip_markup(text: str) -> str:
    """Bỏ thẻ màu kiểu `[bold]` khi in bằng `print` trần."""
    out: list[str] = []
    depth = 0
    for ch in text:
        if ch == "[":
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
        elif not depth:
            out.append(ch)
    return "".join(out)


OUT = Out()

TICK = "✓"
CROSS = "✗"
WARN_MARK = "!"
INFO_MARK = "·"


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_INFO = "info"

_MARKS = {
    STATUS_OK: (TICK, "green"),
    STATUS_WARN: (WARN_MARK, "yellow"),
    STATUS_FAIL: (CROSS, "bold red"),
    STATUS_INFO: (INFO_MARK, "dim"),
}

#: Số ngày sau đó một bản `yt-dlp` bị coi là cũ. YouTube đổi cách phát video vài
#: tuần một lần và bản cũ hỏng im lặng, nên cảnh báo sớm rẻ hơn nhiều so với việc
#: để người dùng chờ tải 20 phút rồi mới thấy lỗi.
YTDLP_MAX_AGE_DAYS = 60

#: Dung lượng trống tối thiểu: model `large-v3` khoảng 3GB, cộng audio và file tạm.
DISK_MIN_GB = 2.0
DISK_COMFORT_GB = 10.0

#: Dưới mức này thì `large-v3` chạy được nhưng rất chậm vì máy phải tráo bộ nhớ.
RAM_MIN_GB = 8.0


@dataclass
class Check:
    """Một dòng kết quả của `doctor`.

    `fix` là **việc làm được ngay**, không phải lời khuyên chung chung — đó là khác
    biệt giữa một chẩn đoán dùng được và một chẩn đoán chỉ để nhìn. Trên macOS nó
    là một câu chỉ tới nút sửa hoặc tới `CaiDat.command`, không phải dòng lệnh
    Terminal: màn Kiểm tra máy của giao diện hiện đúng câu này cạnh nút sửa, và
    người dùng không phải dân IT.
    `required` quyết định mã thoát: chỉ mục bắt buộc mới làm lệnh trả về khác 0.
    """

    key: str
    group: str
    label: str
    status: str
    detail: str = ""
    fix: str = ""
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "group": self.group,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "fix": self.fix,
            "required": self.required,
            "ok": self.status in (STATUS_OK, STATUS_INFO),
        }


def _is_mac() -> bool:
    return sys.platform == "darwin"


def _is_windows() -> bool:
    return os.name == "nt"


_WINGET = {"ffmpeg": "Gyan.FFmpeg", "ffprobe": "Gyan.FFmpeg", "yt-dlp": "yt-dlp.yt-dlp"}

#: Cách sửa trên macOS khi thiếu ffmpeg, ffprobe hoặc yt-dlp. Màn “Kiểm tra máy”
#: của giao diện hiện câu này ngay cạnh nút sửa của đúng mục đó (server gắn
#: `fix_action` cho cả ba), nên câu chỉ cần trỏ tới nút ấy — và tới bộ cài, cho
#: lúc nút báo lỗi hoặc khi đọc kết quả `srtgen doctor` ở cuối CaiDat.command.
#: Không gợi ý trình quản lý gói nào: bộ cài cố ý bỏ Homebrew (kéo theo Xcode
#: Command Line Tools ~1GB và hỏi mật khẩu quản trị), mọi công cụ đã có chỗ riêng
#: trong thư mục của app.
_MAC_TOOL_FIX = "Bấm nút sửa ngay bên cạnh, hoặc chạy lại CaiDat.command."

#: Python quá cũ trên macOS. Không có nút nào trong app sửa được việc này — chính
#: app đang chạy trên bản Python đó — nên chỉ trỏ tới bộ cài: nó tự tải Python
#: 3.12 riêng cho tool bằng `uv`, không đụng Python của máy, không cần mật khẩu.
_MAC_PYTHON_FIX = (
    "Chạy lại CaiDat.command — bộ cài tự tải Python 3.12 riêng cho tool, "
    "không đụng tới Python sẵn có của máy."
)


def _tool_install_hint(name: str) -> str:
    """Cách cài một công cụ ngoài, đúng theo hệ điều hành đang chạy."""
    if _is_mac():
        return _MAC_TOOL_FIX
    if _is_windows():
        return f"winget install {_WINGET.get(name, name)}"
    return f"sudo apt install {name}"


def _pip_install(package: str) -> str:
    """Lệnh cài gói Python, gọi đích danh trình Python đang chạy.

    Gõ `pip install` trần trên máy có nhiều bản Python sẽ cài vào đúng bản mà tool
    **không** dùng — một trong những cách hỏng khó hiểu nhất đối với người không
    rành máy tính.
    """
    return f'"{sys.executable}" -m pip install {package}'


def _human_bytes(size: float) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size) < step or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= step
    return f"{size:.1f} TB"  # pragma: no cover - không tới được


def _total_ram_bytes() -> int | None:
    """Tổng RAM của máy, `None` nếu không hỏi được.

    Không dùng `psutil` vì đó sẽ là một phụ thuộc nữa phải cài trên máy trắng, mà
    thứ cần biết chỉ là một con số để cảnh báo.
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return int(pages) * int(page_size)
    except (AttributeError, ValueError, OSError):
        pass
    if _is_windows():  # pragma: no cover - chỉ chạy trên Windows
        try:
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(_MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullTotalPhys)
        except Exception:
            return None
    return None


def _dir_size(path: Path, *, limit_files: int = 20000) -> int:
    """Tổng dung lượng một thư mục, có trần để không quét cả ổ đĩa nếu đường dẫn sai."""
    total = 0
    count = 0
    try:
        for item in path.rglob("*"):
            if count >= limit_files:
                break
            try:
                if item.is_file():
                    total += item.stat().st_size
                    count += 1
            except OSError:
                continue
    except OSError:
        return total
    return total


def _check_python() -> Check:
    version = platform.python_version()
    ok = sys.version_info >= (3, 10)
    return Check(
        key="python",
        group="Máy và hệ điều hành",
        label="Phiên bản Python",
        status=STATUS_OK if ok else STATUS_FAIL,
        detail=(
            f"Đang dùng Python {version} ({sys.executable})"
            if ok
            else f"Đang dùng Python {version}, tool cần từ 3.10 trở lên."
        ),
        fix="" if ok else (_MAC_PYTHON_FIX if _is_mac() else "Cài Python 3.12 từ python.org"),
        required=True,
    )


def _check_machine() -> list[Check]:
    logical = os.cpu_count() or 1
    threads = max(1, logical // 2)
    ram = _total_ram_bytes()
    checks = [
        Check(
            key="cpu",
            group="Máy và hệ điều hành",
            label="Số nhân CPU",
            status=STATUS_OK if logical >= 4 else STATUS_WARN,
            detail=(
                f"{logical} luồng xử lý; bước nghe băng sẽ dùng {threads} luồng."
                if logical >= 4
                else f"Chỉ có {logical} luồng — bước nghe băng sẽ rất lâu. "
                "Cân nhắc dùng mô hình “medium” cho nhanh."
            ),
            fix="" if logical >= 4 else "srtgen run <link> --model medium",
        )
    ]
    if ram is None:
        checks.append(
            Check(
                key="ram",
                group="Máy và hệ điều hành",
                label="Bộ nhớ RAM",
                status=STATUS_INFO,
                detail="Không đọc được dung lượng RAM của máy này (không sao, chỉ là thông tin).",
            )
        )
    else:
        gb = ram / (1024 ** 3)
        enough = gb >= RAM_MIN_GB
        checks.append(
            Check(
                key="ram",
                group="Máy và hệ điều hành",
                label="Bộ nhớ RAM",
                status=STATUS_OK if enough else STATUS_WARN,
                detail=(
                    f"{gb:.0f} GB — đủ để chạy mô hình large-v3."
                    if enough
                    else f"{gb:.0f} GB — hơi ít cho mô hình large-v3, máy sẽ chậm."
                ),
                fix="" if enough else "srtgen run <link> --model medium",
            )
        )
    return checks


def _find_uv() -> str:
    """Đường dẫn tới `uv`, hoặc chuỗi rỗng nếu máy chưa có.

    Không chỉ hỏi `PATH`: khi app được mở từ biểu tượng trên Desktop, macOS chỉ
    đưa cho một `PATH` tối thiểu, nên `uv` nằm nguyên trên đĩa vẫn "biến mất".
    `KhoiDong.command` biết chỗ thật và truyền qua `SRTGEN_UV`, vì vậy biến đó
    được hỏi trước; sau đó mới tới `PATH` rồi tới những chỗ cài quen thuộc.
    """
    candidates: list[str] = [os.environ.get("SRTGEN_UV", "").strip()]
    found = shutil.which("uv")
    if found:
        candidates.append(found)
    home = Path.home()
    candidates.extend(
        [
            str(home / "Library" / "Application Support" / "SrtGen" / "bin" / "uv"),
            str(home / ".local" / "bin" / "uv"),
            str(home / ".cargo" / "bin" / "uv"),
        ]
    )
    for raw in candidates:
        if not raw:
            continue
        try:
            path = Path(raw).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        except OSError:  # pragma: no cover - đường dẫn rác trong biến môi trường
            continue
    return ""


def _uv_version(path: str) -> str:
    """Số hiệu bản `uv`; rỗng nếu hỏi không được.

    Không để việc hỏi phiên bản làm hỏng cả `doctor`: một file `uv` treo hoặc
    hỏng chỉ đáng mất một dòng chữ, không đáng mất cả lệnh chẩn đoán.
    """
    import subprocess  # noqa: PLC0415 - nạp muộn để doctor chạy được trên máy cài dở

    try:
        proc = subprocess.run(  # noqa: S603 - đường dẫn do chính hàm này dò ra
            [path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    head = (proc.stdout or proc.stderr or "").strip().splitlines()
    if not head:
        return ""
    # `uv --version` in ra dạng "uv 0.5.11 (abcdef 2025-01-02)"; lấy đúng con số.
    parts = head[0].split()
    return parts[1] if len(parts) > 1 and parts[0].lower() == "uv" else head[0]


def _uv_install_hint() -> str:
    if _is_windows():  # pragma: no cover - máy đích là macOS
        return "winget install astral-sh.uv"
    if _is_mac():
        # Người dùng Mac không phải dân IT: chỉ tới bộ cài, không đưa dòng lệnh
        # Terminal (xem docstring của `Check`).
        return "Bấm đúp lại vào file CaiDat.command — nó tự tải uv về, không hỏi mật khẩu."
    return "curl -LsSf https://astral.sh/uv/install.sh | sh"


def _check_uv() -> Check:
    """`uv` — trình quản lý mà bộ cài macOS dùng cho Python, thư viện và yt-dlp.

    `build-spec-v2` mục 6 đòi `doctor` trả lời được câu "máy đã có uv chưa", vì
    mọi việc sửa chữa về sau đều đi qua nó: cài bù thư viện, cài lại Python
    riêng, và nút "Cập nhật yt-dlp" trong tab Cài đặt.

    Đây **không** phải mục bắt buộc. Máy đã cài xong rồi thì tạo phụ đề vẫn chạy
    đủ mọi bước dù xoá mất `uv`; chỉ là không tự cài bù được nữa. Báo đỏ ở đây sẽ
    dạy người dùng rằng tool đang hỏng trong khi nó vẫn làm việc bình thường.
    """
    path = _find_uv()
    if not path:
        detail = (
            "Máy chưa có uv. Tool vẫn tạo phụ đề bình thường, nhưng không tự cài bù "
            "thư viện và không dùng được nút “Cập nhật yt-dlp”."
        )
        if _is_mac():
            detail += " Cách dễ nhất: bấm đúp lại vào file CaiDat.command, nó tự tải uv về."
        return Check(
            key="uv",
            group="Công cụ ngoài",
            label="Trình quản lý cài đặt (uv)",
            status=STATUS_WARN,
            detail=detail,
            fix=_uv_install_hint(),
            required=False,
        )
    version = _uv_version(path)
    return Check(
        key="uv",
        group="Công cụ ngoài",
        label="Trình quản lý cài đặt (uv)",
        status=STATUS_OK,
        detail=f"Đã có{f' (bản {version})' if version else ''}: {path}",
        required=False,
    )


def _check_tool(name: str, label: str, *, required: bool) -> Check:
    from srtgen.stages.s0_fetch import which_tool

    path = which_tool(name, refresh=True)
    return Check(
        key=name,
        group="Công cụ ngoài",
        label=label,
        status=STATUS_OK if path else STATUS_FAIL,
        detail=f"Đã có: {path}" if path else f"Máy chưa có {name}.",
        fix="" if path else _tool_install_hint(name),
        required=required,
    )


def _check_js_runtime() -> Check:
    """yt-dlp cần deno hoặc node để đọc YouTube; thiếu thì nhiều video lỗi 403.

    Không bắt buộc (file có sẵn trên máy vẫn xử lý bình thường), nhưng là nguyên
    nhân lỗi tải video đứng thứ hai sau yt-dlp cũ, nên phải hiện rõ ở đây thay vì
    để người dùng đoán từ một câu 403.
    """
    from srtgen.stages.s0_fetch import js_runtime_path

    found = js_runtime_path()
    if found:
        name, path = found
        return Check(
            key="js-runtime",
            group="Công cụ ngoài",
            label="Bộ chạy JavaScript cho yt-dlp (deno/node)",
            status=STATUS_OK,
            detail=f"Đã có {name}: {path}",
            required=False,
        )
    if _is_mac():
        fix = "Chạy lại CaiDat.command — nó tải deno vào thư mục riêng của SrtGen."
    elif _is_windows():
        fix = "winget install DenoLand.Deno (hoặc cài Node.js), rồi mở lại app."
    else:
        fix = "Cài deno (https://deno.land) hoặc node, rồi mở lại app."
    return Check(
        key="js-runtime",
        group="Công cụ ngoài",
        label="Bộ chạy JavaScript cho yt-dlp (deno/node)",
        status=STATUS_WARN,
        detail="Máy chưa có deno hay node. YouTube bắt yt-dlp chạy JavaScript để lấy "
        "đường video; thiếu thì nhiều video báo lỗi 403 hoặc thiếu phần tiếng. "
        "File có sẵn trên máy vẫn xử lý bình thường.",
        fix=fix,
        required=False,
    )


def _check_ytdlp() -> Check:
    from srtgen.stages.s0_fetch import ytdlp_command, ytdlp_version

    command = ytdlp_command()
    if command is None:
        return Check(
            key="yt-dlp",
            group="Công cụ ngoài",
            label="Công cụ tải video (yt-dlp)",
            status=STATUS_FAIL,
            detail="Máy chưa có yt-dlp nên chưa tải được video từ YouTube. "
            "(Vẫn xử lý được file âm thanh có sẵn trên máy.)",
            fix=_tool_install_hint("yt-dlp"),
            required=True,
        )

    version = ytdlp_version() or ""
    age = _ytdlp_age_days(version)
    if _is_mac():
        # Máy cài bằng CaiDat.command có yt-dlp trong môi trường riêng do uv dựng:
        # "yt-dlp -U" bị chính yt-dlp từ chối (bản cài qua pip không tự nâng cấp),
        # còn môi trường uv không có pip nên "python -m pip" cũng hỏng. Nút
        # "Cập nhật yt-dlp" (fix_action update_ytdlp) mới là đường đúng.
        update_cmd = "Bấm nút “Cập nhật yt-dlp” trong tab Cài đặt của app."
    else:
        update_cmd = "yt-dlp -U" if len(command) == 1 else _pip_install("-U yt-dlp")
    if age is not None and age > YTDLP_MAX_AGE_DAYS:
        return Check(
            key="yt-dlp",
            group="Công cụ ngoài",
            label="Công cụ tải video (yt-dlp)",
            status=STATUS_WARN,
            detail=(
                f"Bản {version}, đã {age} ngày tuổi. YouTube hay đổi cách phát video, "
                "bản cũ thường tải hỏng mà không nói rõ lý do."
            ),
            fix=update_cmd,
        )
    detail = f"Bản {version}" if version else "Đã có"
    if age is not None:
        detail += f" ({age} ngày tuổi)"
    return Check(
        key="yt-dlp",
        group="Công cụ ngoài",
        label="Công cụ tải video (yt-dlp)",
        status=STATUS_OK,
        detail=detail,
        required=True,
    )


def _ytdlp_age_days(version: str) -> int | None:
    """Tuổi của bản yt-dlp, tính từ chuỗi phiên bản dạng ngày `2025.01.26`."""
    from datetime import date, datetime

    head = version.strip().split(" ")[0]
    parts = head.split(".")
    if len(parts) < 3:
        return None
    try:
        released = datetime.strptime(".".join(parts[:3]), "%Y.%m.%d").date()
    except ValueError:
        return None
    return max(0, (date.today() - released).days)


#: (tên import, tên gói pip, nhãn tiếng Việt, bắt buộc)
_PACKAGES: tuple[tuple[str, str, str, bool], ...] = (
    ("jieba", "jieba", "Bộ tách từ tiếng Trung (jieba)", True),
    ("pypinyin", "pypinyin", "Bộ sinh pinyin (pypinyin)", True),
    ("opencc", "opencc-python-reimplemented", "Bộ đổi phồn thể sang giản thể (opencc)", True),
    ("faster_whisper", "faster-whisper", "Bộ nghe và gỡ băng (faster-whisper)", True),
    ("yaml", "PyYAML", "Bộ đọc cấu hình (PyYAML)", False),
    ("platformdirs", "platformdirs", "Bộ dò thư mục hệ thống (platformdirs)", False),
    ("typer", "typer", "Bộ xử lý dòng lệnh (typer)", False),
    ("rich", "rich", "Bộ hiển thị màu (rich)", False),
    ("httpx", "httpx", "Bộ gọi mạng cho AI (httpx)", False),
)


def _check_packages() -> list[Check]:
    import importlib.util

    checks: list[Check] = []
    for module_name, pip_name, label, required in _PACKAGES:
        try:
            found = importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError):  # pragma: no cover - gói cài hỏng
            found = False
        version = _package_version(pip_name, module_name) if found else ""
        if found:
            status = STATUS_OK
            detail = f"Đã cài{f' (bản {version})' if version else ''}"
        elif required:
            status = STATUS_FAIL
            detail = "Chưa cài — thiếu gói này thì không chạy được."
        else:
            status = STATUS_WARN if module_name != "httpx" else STATUS_INFO
            detail = (
                "Chưa cài — chỉ cần khi bật tầng AI."
                if module_name == "httpx"
                else "Chưa cài — tool vẫn chạy được nhưng thiếu một phần tiện ích."
            )
        checks.append(
            Check(
                key=f"pkg:{module_name}",
                group="Thư viện Python",
                label=label,
                status=status,
                detail=detail,
                fix="" if found else _pip_install(pip_name),
                required=required,
            )
        )
    return checks


def _package_version(pip_name: str, module_name: str) -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version(pip_name)
        except PackageNotFoundError:
            return version(module_name)
    except Exception:  # pragma: no cover - siêu dữ liệu gói không đáng tin
        return ""


def _check_model(cfg: Mapping[str, Any]) -> Check:
    """Model Whisper đã tải chưa, và nằm ở đâu.

    Câu trả lời "chưa tải" **không phải lỗi**: lần chạy đầu tiên bao giờ cũng phải
    tải. Nói trước dung lượng và vị trí để người dùng không hoảng khi thấy tool
    đứng im 20 phút ở lần chạy đầu.
    """
    from srtgen.core.context import user_cache_dir

    asr = cfg.get("asr") if isinstance(cfg.get("asr"), Mapping) else {}
    asr = asr or {}
    model = str(asr.get("model") or "large-v3").strip() or "large-v3"

    for key in ("model_dir", "model_path", "local_model"):
        raw = str(asr.get(key) or "").strip()
        if raw:
            path = Path(raw).expanduser()
            ok = path.is_dir()
            return Check(
                key="model",
                group="Mô hình nghe",
                label=f"Mô hình “{model}”",
                status=STATUS_OK if ok else STATUS_FAIL,
                detail=(
                    f"Dùng thư mục có sẵn: {path}"
                    if ok
                    else f"Cài đặt đang trỏ tới thư mục không tồn tại: {path}"
                ),
                fix="" if ok else "Sửa lại mục asr.model_dir trong cấu hình, hoặc để trống để tool tự tải.",
                required=False,
            )

    root_raw = str(asr.get("download_root") or "").strip()
    root = Path(root_raw).expanduser() if root_raw else user_cache_dir() / "models"
    folder = _find_model_dir(root, model)
    if folder is not None:
        size = _dir_size(folder)
        return Check(
            key="model",
            group="Mô hình nghe",
            label=f"Mô hình “{model}”",
            status=STATUS_OK,
            detail=f"Đã tải về máy ({_human_bytes(size)}): {folder}",
        )
    return Check(
        key="model",
        group="Mô hình nghe",
        label=f"Mô hình “{model}”",
        status=STATUS_WARN,
        detail=(
            f"Chưa tải. Lần chạy đầu tiên tool sẽ tự tải khoảng "
            f"{_model_size_hint(model)} về {root}. Chỉ tải một lần."
        ),
        fix="",
    )


def _find_model_dir(root: Path, model: str) -> Path | None:
    """Tìm thư mục cache của model, chấp nhận cách đặt tên của Hugging Face.

    Tên thư mục thật là `models--Systran--faster-whisper-large-v3`, nên chỉ so khớp
    phần đuôi. Đòi hỏi có file đủ lớn để một lần tải dở dang không bị coi là xong.
    """
    if not root.is_dir():
        return None
    needle = model.lower()
    try:
        candidates = [p for p in root.iterdir() if p.is_dir() and p.name.lower().endswith(needle)]
    except OSError:
        return None
    for path in candidates:
        if _dir_size(path) > 50 * 1024 * 1024:
            return path
    return None


def _model_size_hint(model: str) -> str:
    try:
        from srtgen.stages.s2_asr import MODEL_SIZE_MB

        size = MODEL_SIZE_MB.get(model.lower())
    except Exception:  # pragma: no cover - chưa cài faster-whisper thì cũng không sao
        size = None
    return _human_bytes(size * 1024 * 1024) if size else "vài GB"


def _check_directories(cfg: Mapping[str, Any]) -> list[Check]:
    from srtgen.core.context import user_data_dir

    paths = cfg.get("paths") if isinstance(cfg.get("paths"), Mapping) else {}
    paths = paths or {}
    targets = [
        ("out_dir", "Thư mục lưu kết quả", str(paths.get("out_dir") or ""), user_data_dir() / "output"),
        ("work_dir", "Thư mục làm việc tạm", str(paths.get("work_dir") or ""), user_data_dir() / "work"),
    ]
    checks: list[Check] = []
    for key, label, configured, fallback in targets:
        path = Path(configured).expanduser() if configured.strip() else fallback
        ok, reason = _writable(path)
        checks.append(
            Check(
                key=key,
                group="Thư mục",
                label=label,
                status=STATUS_OK if ok else STATUS_FAIL,
                detail=f"Ghi được: {path}" if ok else f"Không ghi được vào {path}. Lý do: {reason}",
                fix="" if ok else "Chọn thư mục khác bằng tuỳ chọn --out, hoặc kiểm tra quyền của ổ đĩa.",
                required=True,
            )
        )
    checks.append(_check_disk(Path(targets[0][2]).expanduser() if targets[0][2].strip() else targets[0][3]))
    return checks


def _writable(path: Path) -> tuple[bool, str]:
    """Thử tạo thư mục và ghi một file thật — hỏi hệ điều hành còn hơn đoán qua quyền."""
    probe = path / ".srtgen_write_test"
    try:
        io_utils.ensure_dir(path)
        io_utils.write_text(probe, "ok", bom=False)
    except OSError as err:
        return False, str(err)
    try:
        probe.unlink()
    except OSError:  # pragma: no cover - ghi được mà xoá không được là chuyện lạ
        pass
    return True, ""


def _check_disk(path: Path) -> Check:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        usage = shutil.disk_usage(target)
    except OSError as err:  # pragma: no cover - ổ đĩa vừa bị rút
        return Check(
            key="disk",
            group="Thư mục",
            label="Dung lượng trống",
            status=STATUS_WARN,
            detail=f"Không đọc được dung lượng ổ đĩa: {err}",
        )
    free_gb = usage.free / (1024 ** 3)
    if free_gb < DISK_MIN_GB:
        status, note = STATUS_FAIL, "Không đủ chỗ để tải mô hình và ghi kết quả."
    elif free_gb < DISK_COMFORT_GB:
        status, note = STATUS_WARN, "Hơi ít; nên dọn bớt trước khi xử lý video dài."
    else:
        status, note = STATUS_OK, "Đủ chỗ."
    return Check(
        key="disk",
        group="Thư mục",
        label="Dung lượng trống",
        status=status,
        detail=f"Còn trống {free_gb:.1f} GB trên ổ chứa {target}. {note}",
        fix="" if status == STATUS_OK else "Dọn bớt file trong Thùng rác và thư mục Downloads.",
        required=status == STATUS_FAIL,
    )


def _check_ai(cfg: Mapping[str, Any]) -> Check:
    """Mã API của AI — luôn là mục **không bắt buộc**.

    Toàn bộ pipeline chạy được ngoại tuyến; tầng AI chỉ làm tốt thêm. Báo đỏ ở đây
    sẽ dạy người dùng rằng tool đang hỏng, trong khi thực ra nó vẫn chạy đủ.
    """
    from srtgen.providers import ai_config, mask_key, resolve_api_keys

    ai = ai_config(cfg)
    enabled = str(ai.get("enabled", False)).strip().lower() in {"1", "true", "yes", "on", "bật"} or ai.get("enabled") is True
    keys = resolve_api_keys(ai)
    env_name = str(ai.get("api_key_env") or "GEMINI_API_KEY")
    if keys:
        detail = f"Đã có mã API ({mask_key(keys[0])})"
        detail += " và tầng AI đang bật." if enabled else ", nhưng tầng AI đang tắt trong cấu hình."
        return Check(
            key="ai_key",
            group="Tầng AI (không bắt buộc)",
            label="Mã API của AI",
            status=STATUS_OK if enabled else STATUS_INFO,
            detail=detail,
        )
    return Check(
        key="ai_key",
        group="Tầng AI (không bắt buộc)",
        label="Mã API của AI",
        status=STATUS_INFO,
        detail=(
            "Chưa có mã API. Tool vẫn chạy đủ mọi bước, chỉ bỏ phần nhờ AI soát lại "
            "tên riêng và âm đọc."
        ),
        # Trên Mac, dòng này in ra ngay trong bảng tổng kết của CaiDat.command:
        # chỉ tới ô nhập khoá trong app, không bắt người dùng gõ lệnh Terminal.
        fix=(
            "Không bắt buộc. Muốn dùng AI thì dán khoá vào ô “Khoá API Gemini” ở tab Cài đặt của app."
            if _is_mac()
            else f"export {env_name}=<mã API của bạn>"
        ),
    )


def doctor_checks(cfg: Mapping[str, Any] | None = None) -> list[Check]:
    """Chạy toàn bộ chẩn đoán và trả về danh sách `Check`, không in gì.

    Tách khỏi phần in để web UI (`GET /api/doctor`) dùng lại được nguyên vẹn.
    """
    if cfg is None:
        cfg = _safe_config()

    checks: list[Check] = [_check_python()]
    checks.extend(_check_machine())
    # `uv` đứng đầu nhóm "Công cụ ngoài" vì nó là thứ cài ra mọi công cụ còn lại:
    # thấy nó thiếu thì ba dòng bên dưới thiếu theo là chuyện đương nhiên.
    checks.append(_check_uv())
    checks.append(_check_tool("ffmpeg", "Công cụ xử lý âm thanh (ffmpeg)", required=True))
    checks.append(_check_tool("ffprobe", "Công cụ đọc thông tin âm thanh (ffprobe)", required=True))
    checks.append(_check_ytdlp())
    checks.append(_check_js_runtime())
    checks.extend(_check_packages())
    checks.append(_check_model(cfg))
    checks.extend(_check_directories(cfg))
    checks.append(_check_ai(cfg))
    return checks


def doctor_report(cfg: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Kết quả `doctor` dưới dạng JSON thuần — đây là hàm web UI gọi."""
    return [check.to_dict() for check in doctor_checks(cfg)]


def _safe_config() -> dict[str, Any]:
    """Nạp cấu hình, và coi việc nạp hỏng là một chuyện có thể xảy ra.

    `doctor` phải chạy được cả khi file cấu hình bị sửa hỏng — đó chính là lúc người
    dùng cần nó nhất.
    """
    try:
        from srtgen.core.context import load_config

        return load_config()
    except Exception:
        return {}


def _print_doctor(checks: Sequence[Check]) -> int:
    """In kết quả và trả về mã thoát: 0 khi mọi mục bắt buộc đều đạt."""
    OUT.print()
    OUT.print("[bold]Kiểm tra máy trước khi chạy srtgen[/bold]")
    OUT.dim(f"Máy: {platform.platform()}")
    OUT.print()

    group = ""
    for check in checks:
        if check.group != group:
            group = check.group
            OUT.print(f"[bold]{group}[/bold]")
        mark, style = _MARKS.get(check.status, (INFO_MARK, ""))
        OUT.print(f"  [{style}]{mark}[/{style}] {check.label}: {check.detail}" if OUT.rich
                  else f"  {mark} {check.label}: {check.detail}")
        if check.fix:
            OUT.dim(f"      Cách xử lý: {check.fix}")
    OUT.print()

    failures = [c for c in checks if c.status == STATUS_FAIL and c.required]
    warnings = [c for c in checks if c.status == STATUS_WARN]
    if failures:
        OUT.error(f"{CROSS} Còn {len(failures)} mục bắt buộc chưa đạt, tool chưa chạy đủ được.")
        OUT.print("Hãy làm theo phần “Cách xử lý” của các dòng có dấu ✗ ở trên, rồi chạy lại: srtgen doctor")
        return 1
    if warnings:
        OUT.warn(f"{TICK} Đủ điều kiện chạy. Có {len(warnings)} mục nên xem lại (dấu !).")
    else:
        OUT.good(f"{TICK} Máy đã sẵn sàng. Chạy thử: srtgen run <link YouTube>")
    return 0


# --------------------------------------------------------------------------- #
# Hiển thị tiến trình khi chạy pipeline
# --------------------------------------------------------------------------- #

class ProgressView:
    """Thanh tiến trình hai tầng: tổng thể và bước đang chạy.

    Hai tầng chứ không một, vì bước nghe băng chiếm quá nửa thời gian: chỉ có thanh
    tổng thì nó gần như đứng yên, còn chỉ có thanh bước thì người dùng không biết
    còn bao lâu nữa mới xong cả bài.
    """

    def __init__(self, quiet: bool = False) -> None:
        self.quiet = quiet
        self._progress: Any = None
        self._overall: Any = None
        self._step: Any = None
        self._last_line = ""

    def __enter__(self) -> "ProgressView":
        if self.quiet or not OUT.rich:
            return self
        try:
            from rich.progress import (
                BarColumn,
                Progress,
                SpinnerColumn,
                TaskProgressColumn,
                TextColumn,
                TimeElapsedColumn,
            )
        except ImportError:  # pragma: no cover - rich thiếu một phần
            return self
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(bar_width=28),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=OUT.console,
            transient=False,
        )
        self._progress.start()
        self._overall = self._progress.add_task("Toàn bộ công việc", total=1000)
        self._step = self._progress.add_task("Đang chuẩn bị…", total=1000)
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None

    # -- nhận sự kiện từ pipeline ------------------------------------------ #

    def handle(self, event: Any) -> None:
        if self.quiet:
            return
        if self._progress is None:
            self._handle_plain(event)
            return
        self._progress.update(self._overall, completed=int(event.overall_fraction * 1000))
        self._progress.update(
            self._step,
            description=_shorten(event.message or event.stage.label, 46),
            completed=int(event.stage_fraction * 1000),
        )
        if event.phase == "start":
            self._say(f"[bold]{event.stage.title}[/bold]")
            if event.stage.note:
                self._say(f"   {event.stage.note}", dim=True)
        elif event.phase in ("done", "reused"):
            done = "dùng lại kết quả cũ" if event.phase == "reused" else f"xong sau {_duration(event.elapsed)}"
            self._say(f"   {TICK} {event.stage.label}: {done}")

    def _handle_plain(self, event: Any) -> None:
        """Đường in không màu: chỉ nói khi có chuyện đáng nói, tránh cuộn màn hình."""
        if event.phase == "start":
            OUT.print(f"{event.stage.title}")
            if event.stage.note:
                OUT.print(f"   {event.stage.note}")
        elif event.phase in ("done", "reused"):
            OUT.print(f"   {TICK} {event.stage.label}: xong ({_duration(event.elapsed)})")
        elif event.message and event.message != self._last_line:
            percent = int(event.overall_fraction * 100)
            OUT.print(f"   [{percent:3d}%] {event.message}")
        self._last_line = event.message

    def _say(self, text: str, *, dim: bool = False) -> None:
        console = self._progress.console if self._progress is not None else OUT.console
        if console is None:  # pragma: no cover - đã lọc ở trên
            print(_strip_markup(text))
            return
        console.print(text, style="dim" if dim else None)


def _shorten(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _duration(seconds: float) -> str:
    """Thời lượng bằng tiếng Việt, không dùng ký hiệu kiểu 00:12:30."""
    total = int(max(0.0, float(seconds)))
    if total < 60:
        return f"{total} giây"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} phút {secs} giây" if secs else f"{minutes} phút"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} giờ {minutes} phút"


# --------------------------------------------------------------------------- #
# In kết quả và danh sách vi phạm
# --------------------------------------------------------------------------- #

AEGISUB_NOTE = (
    "Mở file .srt bằng Aegisub để soát lại. Mỗi khối có 4 dòng: dòng thứ ba là chữ Hán, "
    "dòng thứ tư là pinyin — Aegisub hiện hai dòng đó trong cùng một sự kiện, đúng như "
    "vậy, không phải lỗi."
)


def _print_findings(findings: Sequence[Any], *, limit: int = 25, title: str = "Kết quả kiểm") -> None:
    """In danh sách vi phạm, gộp theo loại trước rồi mới liệt kê chi tiết.

    Có `limit` vì một file sai hình dạng có thể sinh 1351 dòng vi phạm cùng loại;
    đổ hết ra màn hình thì người dùng không đọc nổi dòng nào, kể cả dòng quan trọng.
    """
    from srtgen.core.rules import RULES, SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARN, summarize

    stats = summarize(findings)
    errors = stats.get(SEVERITY_ERROR, 0)
    warns = stats.get(SEVERITY_WARN, 0)
    infos = stats.get(SEVERITY_INFO, 0)

    OUT.print()
    OUT.print(f"[bold]{title}:[/bold] {errors} lỗi, {warns} cảnh báo, {infos} ghi chú.")
    if not findings:
        OUT.good(f"{TICK} File đạt toàn bộ 12 mục của quy chuẩn.")
        return

    by_code = sorted(stats.get("by_code", {}).items(), key=lambda kv: (-kv[1], kv[0]))
    for code, count in by_code:
        severity, label = RULES.get(code, (SEVERITY_ERROR, code))
        mark = CROSS if severity == SEVERITY_ERROR else WARN_MARK
        OUT.print(f"  {mark} {label}: {count} chỗ")

    order = {SEVERITY_ERROR: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}
    ordered = sorted(
        findings,
        key=lambda f: (
            order.get(f.severity, 9),
            f.cue_index if f.cue_index is not None else 10 ** 9,
            f.code,
        ),
    )
    OUT.print()
    OUT.print("Chi tiết:")
    for finding in ordered[:limit]:
        OUT.print(f"  [{finding.label()}] {_shorten(finding.message, 300)}")
    if len(ordered) > limit:
        OUT.dim(f"  … và {len(ordered) - limit} dòng nữa (xem đầy đủ bằng tuỳ chọn --all).")


def _print_result(result: Mapping[str, Any]) -> None:
    """Màn hình kết thúc của lệnh `run`/`resume`: các file, một câu kiểm định, một lời dặn."""
    OUT.print()
    OUT.rule("Xong")
    OUT.good(f"{TICK} Hoàn thành sau {_duration(result.get('elapsed', 0.0))}.")
    OUT.print()
    OUT.print(f"Thư mục kết quả: {result.get('out_dir', '')}")
    rows = (
        ("Phụ đề tiếng Trung (.srt)", result.get("srt")),
        ("Phụ đề tiếng Việt (_vi.srt)", result.get("vi_srt")),
        ("Bản song ngữ để soát (_song-ngu.ass)", result.get("bilingual_ass")),
        ("Dữ liệu cho backend (.bundle.json)", result.get("bundle")),
        ("Báo cáo (.report.html)", result.get("report")),
        ("Bản cho Aegisub (.ass)", result.get("ass")),
    )
    for label, path in rows:
        if path:
            OUT.print(f"  • {label}: {Path(str(path)).name}")

    _print_translation(result.get("translation"), skipped=bool(result.get("skip_translate")))

    findings = list(result.get("findings") or [])
    _print_findings(findings, limit=15, title="Kiểm định lần cuối")
    if result.get("has_errors"):
        OUT.print()
        OUT.warn(
            "File vẫn được ghi ra để bạn xem, nhưng còn lỗi định dạng — hãy mở file "
            "báo cáo (.report.html) để biết từng chỗ cần sửa."
        )
    OUT.print()
    OUT.dim(AEGISUB_NOTE)


#: Tên tiếng Việt của từng dịch vụ dịch, để câu tổng kết không hiện mã kỹ thuật.
_TRANSLATE_LABELS = {
    "gemini": "Gemini (dùng mã API của bạn)",
    "google_free": "Google bản miễn phí",
    "null": "không dịch",
}


def _print_translation(info: Any, *, skipped: bool = False) -> None:
    """Một khối ngắn về phần dịch: dịch bằng gì, được bao nhiêu câu, còn sót gì.

    Câu "còn N câu giữ nguyên văn" quan trọng hơn mọi con số khác ở đây: đó là
    việc người dùng phải làm tay, và nếu không nói ra thì họ sẽ chỉ phát hiện khi
    khách hàng của họ phát hiện.
    """
    if skipped:
        OUT.print()
        OUT.dim("Bạn đã chọn không dịch, nên lần này không có file _vi.srt.")
        return
    if not isinstance(info, Mapping) or not info:
        return

    counts = dict(info.get("counts") or {})
    total = int(counts.get("cues", 0) or 0)
    done = int(counts.get("translated", 0) or 0)
    kept = int(counts.get("kept_original", 0) or 0)
    name = str(info.get("provider") or "null")
    label = _TRANSLATE_LABELS.get(name, name)

    OUT.print()
    if not total:
        return
    if name == "null":
        OUT.warn(f"{WARN_MARK} Chưa dịch được: {info.get('provider_reason', '')}")
        return
    OUT.print(f"[bold]Bản dịch:[/bold] {done}/{total} câu, dịch bằng {label}.")
    if kept:
        OUT.warn(
            f"{WARN_MARK} Còn {kept} câu giữ nguyên văn tiếng Trung. Mở file _vi.srt và "
            "tìm những dòng vẫn còn chữ Hán để dịch tay — số dòng và mốc thời gian đã "
            "đúng sẵn, bạn chỉ cần thay phần chữ."
        )

    # Lý do quan trọng hơn con số: "0/12 câu" mà không nói vì sao thì người dùng
    # không biết phải làm gì tiếp.
    seen: set[str] = set()
    for row in list(info.get("errors") or [])[:4]:
        if not isinstance(row, Mapping):
            continue
        message = str(row.get("user_message") or "").strip()
        if message and message not in seen:
            seen.add(message)
            OUT.print(f"   Lý do: {message}")

    warning = str(info.get("warning") or "").strip()
    if warning and done:
        OUT.dim(warning)


# --------------------------------------------------------------------------- #
# Chạy pipeline (dùng chung cho run và resume)
# --------------------------------------------------------------------------- #

def _build_overrides(
    *,
    model: str | None = None,
    no_ai: bool = False,
    force_from: int | None = None,
    ass: bool = False,
    no_translate: bool = False,
    target_lang: str | None = None,
) -> dict[str, Any]:
    """Đổi các cờ dòng lệnh thành phần đè lên cấu hình.

    `--no-translate` vừa tắt chặng trong `run_pipeline` vừa tắt trong cấu hình:
    một cờ dòng lệnh chỉ có tác dụng ở một trong hai chỗ là cờ sẽ mất tác dụng
    ngay lần đầu ai đó gọi pipeline bằng đường khác (web UI, `srtgen names`).
    """
    overrides: dict[str, Any] = {}
    if model:
        overrides["asr"] = {"model": model}
    if no_ai:
        overrides["ai"] = {"enabled": False}
    if ass:
        overrides["emit"] = {"ass_export": True}
    translate: dict[str, Any] = {}
    if no_translate:
        translate["enabled"] = False
    if target_lang:
        translate["target"] = str(target_lang).strip().lower()
    if translate:
        overrides["translate"] = translate
    if force_from is not None:
        overrides["force_from"] = force_from
    return overrides


def _load_config(profile: str | None, overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from srtgen.core.context import load_config

    try:
        return load_config(profile, dict(overrides or {}))
    except (ValueError, OSError) as err:
        _fail(str(err))
        raise  # pragma: no cover - _fail đã thoát


def _execute(
    ctx: Any,
    *,
    start_from: int,
    skip_ai: bool,
    quiet: bool,
    skip_translate: bool = False,
) -> dict[str, Any]:
    """Chạy pipeline dưới thanh tiến trình và dịch hai loại kết thúc bất thường."""
    from srtgen.pipeline import PipelineCancelled, PipelineError, run_pipeline

    try:
        with ProgressView(quiet=quiet) as view:
            return run_pipeline(
                ctx,
                None,
                start_from,
                skip_ai,
                on_event=view.handle,
                skip_translate=skip_translate,
            )
    except PipelineCancelled as err:
        OUT.print()
        OUT.warn(f"Đã dừng: {err.user_message}")
        OUT.dim(f"Chạy tiếp bằng: srtgen resume {ctx.video_id}")
        raise SystemExit(130) from err
    except PipelineError as err:
        OUT.print()
        if err.stage_title:
            OUT.error(f"{CROSS} {err.stage_title} — không chạy tiếp được")
        for line in str(err.user_message).split("\n"):
            OUT.print(f"   {line}")
        if err.detail:
            OUT.dim(f"   (Chi tiết kỹ thuật: {_shorten(err.detail, 400)})")
        OUT.print()
        OUT.dim(f"Sửa xong thì chạy tiếp, không phải làm lại từ đầu: srtgen resume {ctx.video_id}")
        raise SystemExit(1) from err


def _fail(message: str, code: int = 1) -> None:
    OUT.error(f"{CROSS} {message}")
    raise SystemExit(code)


def _legal_notice(source: str) -> None:
    """Nhắc một lần về điều khoản khi nguồn là link — nhắc, không chặn."""
    if not str(source).lower().startswith(("http://", "https://")):
        return
    try:
        from srtgen.stages.s0_fetch import LEGAL_NOTICE
    except ImportError:  # pragma: no cover
        return
    OUT.dim(str(LEGAL_NOTICE))
    OUT.print()


# --------------------------------------------------------------------------- #
# Các lệnh
# --------------------------------------------------------------------------- #

HELP = (
    "srtgen — từ một link video hoặc file âm thanh, làm ra hai file phụ đề đi cùng nhau: "
    "bản tiếng Trung 4 dòng (chữ Hán + pinyin) và bản tiếng Việt.\n\n"
    "Chưa biết bắt đầu từ đâu? Chạy: srtgen doctor"
)

if typer is not None:
    app = typer.Typer(
        add_completion=False,
        no_args_is_help=True,
        help=HELP,
        context_settings={"help_option_names": ["-h", "--help"]},
    )
else:  # pragma: no cover - máy chưa cài typer
    app = None  # type: ignore[assignment]


def _command(name: str, help_text: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Đăng ký lệnh, và không nổ nếu máy chưa có typer (xem `main`)."""
    if app is None:  # pragma: no cover
        return lambda func: func
    return app.command(name, help=help_text)


if typer is not None:

    @_command("run", "Làm phụ đề từ một link YouTube hoặc một file trên máy.")
    def cmd_run(
        source: str = typer.Argument(
            ..., metavar="LINK_HOẶC_FILE", help="Link YouTube, hoặc đường dẫn file âm thanh/video."
        ),
        out: Optional[Path] = typer.Option(None, "--out", "-o", help="Thư mục lưu kết quả."),
        model: Optional[str] = typer.Option(
            None, "--model", help="Mô hình nghe: large-v3-turbo (mặc định), large-v3, medium, small."
        ),
        no_ai: bool = typer.Option(False, "--no-ai", help="Không dùng AI, chạy hoàn toàn ngoại tuyến."),
        no_translate: bool = typer.Option(
            False, "--no-translate", help="Không dịch sang tiếng Việt, chỉ xuất file tiếng Trung."
        ),
        target_lang: str = typer.Option(
            "vi", "--target-lang", help="Ngôn ngữ đích của file dịch. Mặc định: vi (tiếng Việt)."
        ),
        profile: Optional[str] = typer.Option(
            None, "--profile", help="Bộ tham số theo thể loại: drama (mặc định) hoặc news."
        ),
        from_step: Optional[str] = typer.Option(
            None, "--from", help="Chạy lại từ bước nào, ví dụ: s5."
        ),
        ass: bool = typer.Option(False, "--ass", help="Xuất thêm bản .ass cho Aegisub."),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Không hiện thanh tiến trình."),
    ) -> None:
        from srtgen.core.context import new_context
        from srtgen.pipeline import stage_number

        try:
            start_from = stage_number(from_step)
        except ValueError as err:
            _fail(str(err), code=2)
            return

        cfg = _load_config(
            profile,
            _build_overrides(
                model=model,
                no_ai=no_ai,
                ass=ass,
                no_translate=no_translate,
                target_lang=target_lang,
                force_from=start_from if from_step else None,
            ),
        )
        try:
            ctx = new_context(source, cfg, out)
        except RuntimeError as err:
            _fail(str(err))
            return

        OUT.print()
        OUT.print(f"[bold]Nguồn:[/bold] {source}")
        OUT.dim(f"Mã lần chạy: {ctx.video_id} — dùng mã này khi cần chạy tiếp: srtgen resume {ctx.video_id}")
        OUT.print()
        _legal_notice(source)

        result = _execute(
            ctx,
            start_from=start_from,
            skip_ai=no_ai,
            quiet=quiet,
            skip_translate=no_translate,
        )
        _print_result(result)
        raise SystemExit(1 if result.get("has_errors") else 0)

    @_command("resume", "Chạy tiếp một lần chạy dở dang (dùng mã lần chạy).")
    def cmd_resume(
        video_id: str = typer.Argument(..., metavar="MÃ_LẦN_CHẠY", help="Mã hiện ở đầu lần chạy trước."),
        from_step: Optional[str] = typer.Option(
            None, "--from", help="Ép chạy lại từ bước nào, ví dụ: s5. Bỏ trống = chạy tiếp chỗ dở."
        ),
        out: Optional[Path] = typer.Option(None, "--out", "-o", help="Thư mục lưu kết quả."),
        model: Optional[str] = typer.Option(None, "--model", help="Đổi mô hình nghe."),
        no_ai: bool = typer.Option(False, "--no-ai", help="Không dùng AI."),
        no_translate: bool = typer.Option(
            False, "--no-translate", help="Không dịch sang tiếng Việt."
        ),
        target_lang: str = typer.Option(
            "vi", "--target-lang", help="Ngôn ngữ đích của file dịch. Mặc định: vi (tiếng Việt)."
        ),
        profile: Optional[str] = typer.Option(None, "--profile", help="Bộ tham số: drama hoặc news."),
        ass: bool = typer.Option(False, "--ass", help="Xuất thêm bản .ass cho Aegisub."),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Không hiện thanh tiến trình."),
    ) -> None:
        from srtgen.pipeline import PipelineError, context_for_video_id, stage_number

        try:
            start_from = stage_number(from_step)
        except ValueError as err:
            _fail(str(err), code=2)
            return

        cfg = _load_config(
            profile,
            _build_overrides(
                model=model,
                no_ai=no_ai,
                ass=ass,
                no_translate=no_translate,
                target_lang=target_lang,
                force_from=start_from if from_step else None,
            ),
        )
        try:
            ctx = context_for_video_id(video_id, cfg, out)
        except PipelineError as err:
            _fail(err.user_message)
            return

        OUT.print()
        OUT.print(f"[bold]Chạy tiếp:[/bold] {video_id}")
        if from_step:
            OUT.dim(f"Ép làm lại từ bước {start_from + 1}.")
        OUT.print()

        result = _execute(
            ctx,
            start_from=start_from,
            skip_ai=no_ai,
            quiet=quiet,
            skip_translate=no_translate,
        )
        _print_result(result)
        raise SystemExit(1 if result.get("has_errors") else 0)

    @_command("check", "Kiểm một file .srt theo quy chuẩn, không sửa gì.")
    def cmd_check(
        file: Path = typer.Argument(..., metavar="FILE.SRT", help="File cần kiểm."),
        show_all: bool = typer.Option(False, "--all", help="Liệt kê đầy đủ, không rút gọn."),
    ) -> None:
        from srtgen.pipeline import run_check

        text = _read_srt(file)
        findings = run_check(text)
        OUT.print(f"[bold]File:[/bold] {file}")
        _print_findings(findings, limit=10 ** 6 if show_all else 25)

        from srtgen.core.rules import SEVERITY_ERROR

        errors = sum(1 for f in findings if f.severity == SEVERITY_ERROR)
        if errors:
            OUT.print()
            OUT.dim(f"Muốn tool tự sửa những lỗi này: srtgen fix \"{file}\"")
        raise SystemExit(1 if errors else 0)

    @_command("fix", "Chuẩn hoá một file .srt có sẵn (file bên dịch gửi sang).")
    def cmd_fix(
        file: Path = typer.Argument(..., metavar="FILE.SRT", help="File cần chuẩn hoá."),
        out: Optional[Path] = typer.Option(
            None,
            "--out",
            "-o",
            help="File ghi ra. Bỏ trống = tạo file mới cạnh file gốc. File có dòng tiếng "
            "Việt thì ghi kèm <tên>_vi.srt cạnh file này.",
        ),
        show_all: bool = typer.Option(False, "--all", help="Liệt kê đầy đủ, không rút gọn."),
        profile: Optional[str] = typer.Option(None, "--profile", help="Bộ tham số: drama hoặc news."),
    ) -> None:
        """Sửa file bằng đúng hàm mà trang “Kiểm tra file” của web dùng (`run_fix_bilingual`).

        Trước đây lệnh này gọi `run_fix`, chỉ trả file Hán/pinyin: file có dòng
        tiếng Việt thì dòng Việt không được ghi đi đâu cả, lệnh báo thêm một lỗi
        `BLOCK_SHAPE` và thoát mã 1 — trong khi web ghi kèm `_vi.srt` cho cùng file
        đó. Nay CLI và web ra cùng một cặp file. File đích đã có (thường là bản
        người dùng mở ra sửa tay sau lần chạy trước) thì bản cũ được cất trước
        khi ghi, xem `_write_keeping_old`.
        """
        from srtgen.core.rules import SEVERITY_ERROR
        from srtgen.pipeline import run_check, run_fix_bilingual

        text = _read_srt(file)
        cfg = _load_config(profile)
        target = Path(out) if out else file.with_name(f"{file.stem}.fixed{file.suffix or '.srt'}")
        vi_target = target.with_name(f"{target.stem}_vi{target.suffix or '.srt'}")

        before = run_check(text, cfg)
        OUT.print(f"[bold]File:[/bold] {file}")
        OUT.print("Đang chuẩn hoá…")
        try:
            result = run_fix_bilingual(text, cfg)
        except RuntimeError as err:  # thiếu jieba/pypinyin — lỗi đã có sẵn câu tiếng Việt
            _fail(str(err))
            return

        emit = cfg.get("emit") if isinstance(cfg.get("emit"), Mapping) else {}
        newline = "\r\n" if str((emit or {}).get("newline", "lf")).lower() == "crlf" else "\n"
        bom = bool((emit or {}).get("bom", True))
        outputs: list[tuple[Path, str]] = [(target, result.text)]
        if result.vi_text is not None:
            outputs.append((vi_target, result.vi_text))
        kept: list[tuple[Path, Path]] = []
        for path, body in outputs:
            old_copy = _write_keeping_old(path, body, bom=bom, newline=newline)
            if old_copy is not None:
                kept.append((path, old_copy))

        after = list(result.findings)
        errors_before = sum(1 for f in before if f.severity == SEVERITY_ERROR)
        errors_after = sum(1 for f in after if f.severity == SEVERITY_ERROR)
        OUT.print()
        OUT.good(f"{TICK} Đã ghi: {target}")
        if result.vi_text is not None:
            OUT.good(f"{TICK} Đã ghi kèm bản tiếng Việt: {vi_target}")
        _print_kept_copies(kept)
        for note in result.notes:
            OUT.print(note)
        OUT.print(f"Số lỗi trước khi sửa: {errors_before} — sau khi sửa: {errors_after}")
        _print_findings(after, limit=10 ** 6 if show_all else 15, title="Kiểm lại file vừa ghi")
        OUT.print()
        OUT.dim(AEGISUB_NOTE)
        raise SystemExit(1 if errors_after else 0)

    @_command("translate", "Dịch một file .srt có sẵn sang tiếng Việt (tạo file _vi.srt).")
    def cmd_translate(
        file: Path = typer.Argument(..., metavar="FILE.SRT", help="File phụ đề cần dịch."),
        out: Optional[Path] = typer.Option(
            None, "--out", "-o", help="File ghi ra. Bỏ trống = tạo file _vi.srt cạnh file gốc."
        ),
        target_lang: str = typer.Option(
            "vi", "--target-lang", help="Ngôn ngữ đích. Mặc định: vi (tiếng Việt)."
        ),
        profile: Optional[str] = typer.Option(None, "--profile", help="Bộ tham số: drama hoặc news."),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Không hiện tiến trình."),
    ) -> None:
        """Dịch riêng một file, không chạy Whisper và không cần thư mục làm việc.

        Dùng cho file bên dịch gửi sang, hoặc để dịch lại một file tool đã xuất từ
        trước mà không phải nghe lại cả video (bước lâu nhất).
        """
        from srtgen.pipeline import run_translate

        text = _read_srt(file)
        cfg = _load_config(profile, _build_overrides(target_lang=target_lang))
        target = Path(out) if out else file.with_name(f"{file.stem}_vi{file.suffix or '.srt'}")

        OUT.print()
        OUT.print(f"[bold]File:[/bold] {file}")

        def report(message: str = "", fraction: float = 0.0) -> None:
            if not quiet and message:
                OUT.print(f"   [{int(max(0.0, min(1.0, fraction)) * 100):3d}%] {message}")

        try:
            vi_text, info = run_translate(text, cfg, target=target_lang, on_progress=report)
        except RuntimeError as err:
            _fail(str(err))
            return

        emit = cfg.get("emit") if isinstance(cfg.get("emit"), Mapping) else {}
        newline = "\r\n" if str((emit or {}).get("newline", "lf")).lower() == "crlf" else "\n"
        old_copy = _write_keeping_old(
            target, vi_text, bom=bool((emit or {}).get("bom", True)), newline=newline
        )

        counts = dict(info.get("counts") or {})
        OUT.print()
        OUT.good(f"{TICK} Đã ghi: {target}")
        _print_kept_copies([(target, old_copy)] if old_copy is not None else [])
        OUT.print(
            f"File mới có đúng {counts.get('cues', 0)} dòng phụ đề và đúng mốc thời gian "
            "của file gốc, mở song song hai file lúc nào cũng khớp."
        )
        _print_translation(info)
        OUT.print()
        OUT.dim(
            "Muốn soát nhanh cả hai ngôn ngữ cùng lúc thì chạy “srtgen run” cho cả video — "
            "lần đó tool xuất thêm bản song ngữ _song-ngu.ass để mở bằng Aegisub."
        )
        raise SystemExit(0 if counts.get("translated") else 1)

    @_command("names", "Nhờ AI lập bảng tên riêng cho một lần chạy (nhiệm vụ T1).")
    def cmd_names(
        video_id: str = typer.Argument(..., metavar="MÃ_LẦN_CHẠY", help="Mã hiện ở đầu lần chạy."),
        profile: Optional[str] = typer.Option(None, "--profile", help="Bộ tham số: drama hoặc news."),
        quiet: bool = typer.Option(False, "--quiet", "-q", help="Không hiện thanh tiến trình."),
    ) -> None:
        from srtgen.pipeline import PipelineError, context_for_video_id

        cfg = _load_config(
            profile,
            {
                # Chỉ chạy T1. Ba nhiệm vụ kia làm việc trên tài liệu đã tách cụm xong,
                # mà tách cụm sẽ đổi ngay sau khi bảng tên riêng này được lập.
                "force_from": 7,
                "ai": {
                    "enabled": True,
                    "tasks": {
                        "t1_names": True,
                        "t2_heteronym": False,
                        "t3_case_after_ellipsis": False,
                        "t4_end_punct": False,
                    },
                },
            },
        )
        try:
            ctx = context_for_video_id(video_id, cfg)
        except PipelineError as err:
            _fail(err.user_message)
            return

        _run_names(ctx, quiet=quiet)

    @_command("doctor", "Kiểm tra máy: ffmpeg, yt-dlp, thư viện, mô hình, thư mục.")
    def cmd_doctor() -> None:
        raise SystemExit(_print_doctor(doctor_checks()))

    @_command("ui", "Mở giao diện web trên máy này.")
    def cmd_ui(
        port: Optional[int] = typer.Option(None, "--port", help="Cổng; bỏ trống = tự tìm cổng trống."),
        host: Optional[str] = typer.Option(None, "--host", help="Địa chỉ nghe, mặc định 127.0.0.1."),
        no_browser: bool = typer.Option(
            False,
            "--no-browser",
            help="Không tự mở trình duyệt (biến môi trường SRTGEN_NO_BROWSER=1 cũng có tác dụng như vậy).",
        ),
        profile: Optional[str] = typer.Option(None, "--profile", help="Bộ tham số: drama hoặc news."),
    ) -> None:
        cfg = _load_config(profile)
        _start_ui(
            cfg,
            host=host,
            port=port,
            open_browser=not (no_browser or _no_browser_env()),
        )


# --------------------------------------------------------------------------- #
# Phần thân của những lệnh dài
# --------------------------------------------------------------------------- #

def _read_srt(path: Path) -> str:
    if not Path(path).is_file():
        _fail(
            f"Không tìm thấy file: {path}\n"
            "   Kiểm tra lại đường dẫn, hoặc kéo thả file vào cửa sổ Terminal để lấy đúng đường dẫn.",
            code=2,
        )
    try:
        return io_utils.read_text(path)
    except (OSError, UnicodeDecodeError) as err:
        _fail(
            f"Không đọc được file: {path}\n"
            f"   Lý do: {err}\n"
            "   File .srt phải là văn bản mã hoá UTF-8."
        )
        return ""  # pragma: no cover - _fail đã thoát


def _write_keeping_old(path: Path, text: str, *, bom: bool, newline: str) -> Path | None:
    """Ghi `text` vào `path`; nếu đã có file KHÁC nội dung thì cất bản cũ trước. Trả bản cất.

    Vì sao: `srtgen fix` ghi mặc định ra `<tên>.fixed.srt` (và `_vi.srt` đi kèm),
    `srtgen translate` ghi ra `<tên>_vi.srt` — đúng những file người dùng hay mở
    ra sửa tay sau lần chạy trước. Chạy lại lệnh mà ghi đè im lặng là mất trắng
    phần sửa tay.

    Việc cất đi qua `io_utils.keep_old_copy` — cùng MỘT hàm với chặng xuất file và
    máy chủ web — nên người dùng chỉ phải học một kiểu tên bản cất
    (`Phim_vi.srt` → `Phim_vi.truoc-20260910-183000.srt`). Nội dung so theo đúng
    từng byte sắp ghi (`encode_text` với cùng BOM và kiểu xuống dòng): giống hệt
    thì không cất, vì không có gì để mất. Không cất được thì DỪNG, không ghi đè:
    mất một buổi sửa tay tệ hơn nhiều so với một lệnh báo lỗi.
    """
    target = Path(path)
    try:
        kept = io_utils.keep_old_copy(target, io_utils.encode_text(text, bom=bom, newline=newline))
    except io_utils.KeepCopyError as err:
        _fail(str(err))
        return None  # pragma: no cover - _fail đã thoát
    io_utils.atomic_write_text(target, text, bom=bom, newline=newline)
    return kept


def _print_kept_copies(kept: Sequence[tuple[Path, Path]]) -> None:
    """Nói rõ bản cũ đang nằm ở đâu — cất mà không nói thì người dùng vẫn tưởng đã mất."""
    for path, copy in kept:
        OUT.warn(
            f"File “{path.name}” đã có từ trước và khác bản mới, nên bản cũ được giữ "
            f"lại thành: {copy}"
        )


def _run_names(ctx: Any, *, quiet: bool) -> None:
    """Chạy riêng chặng AI với mỗi nhiệm vụ T1, rồi in bảng tên riêng thu được."""
    from srtgen.pipeline import PipelineCancelled, PipelineError, stage_by_number
    from srtgen.pipeline import _run_stage as run_stage  # noqa: PLC2701 - dùng lại đúng lớp lỗi
    from srtgen.pipeline import _Tracker as Tracker  # noqa: PLC2701

    stage = stage_by_number(7)
    OUT.print()
    OUT.print(f"[bold]Đang tìm tên riêng cho:[/bold] {ctx.video_id}")
    OUT.print()

    with ProgressView(quiet=quiet) as view:
        tracker = Tracker([stage], None, view.handle)
        try:
            payload, _info = run_stage(ctx, stage, tracker, lap=1)
        except PipelineCancelled as err:
            OUT.warn(f"Đã dừng: {err.user_message}")
            raise SystemExit(130) from err
        except PipelineError as err:
            OUT.error(f"{CROSS} {err.user_message}")
            raise SystemExit(1) from err

    report = payload.get("names") if isinstance(payload, Mapping) else {}
    report = report or {}
    if payload.get("provider") == "null":
        OUT.print()
        OUT.warn("Chưa gọi được AI nên chưa tìm được tên riêng nào.")
        OUT.print(f"   Lý do: {payload.get('provider_reason', '')}")
        raise SystemExit(1)

    path = Path(str(report.get("file") or ""))
    names = _load_names_table(path)
    OUT.print()
    OUT.good(f"{TICK} Tìm được {report.get('found', 0)} tên riêng, bảng hiện có {len(names)} mục.")
    OUT.print(f"Bảng tên riêng: {path}")
    OUT.print()
    for han, pinyin in list(names.items())[:60]:
        OUT.print(f"  {han}  →  {pinyin}")
    if len(names) > 60:
        OUT.dim(f"  … và {len(names) - 60} mục nữa, xem đầy đủ trong file trên.")
    OUT.print()
    OUT.dim(
        "Bảng này sửa tay được (mở bằng TextEdit). Sửa xong chỉ cần chạy tiếp video "
        f"này (srtgen resume {ctx.video_id}): tool tự nhận ra bảng tên riêng đã đổi "
        "và làm lại bước tách cụm và dịch."
    )


def _load_names_table(path: Path) -> dict[str, str]:
    if not path or not path.is_file():
        return {}
    try:
        from srtgen.stages.s7_ai import load_names
    except ImportError:  # pragma: no cover
        return {}
    flat, _meta = load_names(path)
    return flat


#: Những giá trị của `SRTGEN_NO_BROWSER` được hiểu là "vẫn cứ mở trình duyệt".
#: Mọi giá trị khác (kể cả chuỗi lạ) đều tính là "đừng mở" — đặt một biến môi
#: trường tên như vậy rồi lại muốn mở trình duyệt là chuyện không có thật.
_ENV_FALSE = {"", "0", "false", "no", "off", "khong", "không"}


def _no_browser_env() -> bool:
    """`SRTGEN_NO_BROWSER=1` nghĩa là "đừng tự mở trình duyệt".

    `KhoiDong.command` tự mở trình duyệt **sau khi** đã chắc chắn máy chủ lên rồi,
    nên nó đặt biến này để app khỏi mở thêm một thẻ thứ hai chồng lên. Biến môi
    trường là đường duy nhất truyền được ý đó vào, vì file `.command` gọi thẳng
    `python -m srtgen.cli ui` chứ không qua tay ai để thêm cờ.
    """
    return os.environ.get("SRTGEN_NO_BROWSER", "").strip().lower() not in _ENV_FALSE


def _start_ui(
    cfg: Mapping[str, Any],
    *,
    host: str | None,
    port: int | None,
    open_browser: bool,
) -> None:
    """Mở giao diện web.

    Dò vài tên hàm khởi động thay vì cố định một tên: `srtgen/web/server.py` là phần
    được viết sau, và người dùng gõ `srtgen ui` trên máy chưa cài `fastapi` phải nhận
    được câu hướng dẫn chứ không phải một traceback.
    """
    ui_cfg = cfg.get("ui") if isinstance(cfg.get("ui"), Mapping) else {}
    ui_cfg = ui_cfg or {}
    final_host = host or str(ui_cfg.get("host") or "127.0.0.1")
    final_port = int(port if port is not None else (ui_cfg.get("port") or 0))
    if open_browser and ui_cfg.get("open_browser") is False:
        open_browser = False

    # Ghi ý muốn ra biến môi trường **trước khi** gọi máy chủ, và chỉ ghi theo một
    # chiều: đặt "1" khi phải tắt, không bao giờ ghi đè thành "0". Đây là đường
    # thứ hai để `--no-browser` tới được đích, phòng khi hàm khởi động của bản cài
    # này không nhận tham số `open_browser`. Ghi đè thành "0" sẽ xoá mất ý của
    # `KhoiDong.command` và người dùng lại thấy hai thẻ trình duyệt giống nhau.
    if not open_browser:
        os.environ["SRTGEN_NO_BROWSER"] = "1"

    try:
        from importlib import import_module

        server = import_module("srtgen.web.server")
    except ImportError as err:
        _fail(
            "Chưa mở được giao diện web.\n"
            "   Có thể máy chưa cài phần giao diện (fastapi, uvicorn), hoặc bản cài này "
            "chưa có sẵn phần đó.\n"
            f"   Cách xử lý: {_pip_install('fastapi uvicorn')}\n"
            f"   (Chi tiết kỹ thuật: {err})"
        )
        return

    OUT.print()
    if open_browser:
        OUT.good("Đang mở giao diện web… Cửa sổ trình duyệt sẽ tự bật lên.")
    else:
        OUT.good("Đang mở giao diện web… Địa chỉ để mở sẽ hiện ngay bên dưới.")
        OUT.dim("Lần này app không tự mở trình duyệt (--no-browser hoặc SRTGEN_NO_BROWSER=1).")
    OUT.dim("Muốn tắt: quay lại cửa sổ này và bấm Ctrl+C.")
    OUT.print()

    for name in ("serve", "run_server", "start", "main"):
        func = getattr(server, name, None)
        if callable(func):
            _call_server(func, final_host, final_port, open_browser)
            return

    app_obj = getattr(server, "app", None)
    if app_obj is None:
        _fail("Phần giao diện web trong bản cài này chưa hoàn chỉnh. Hãy dùng lệnh dòng lệnh: srtgen run <link>")
        return
    try:
        import uvicorn
    except ImportError as err:
        _fail(
            "Thiếu phần chạy máy chủ web (uvicorn).\n"
            f"   Cách xử lý: {_pip_install('uvicorn')}\n"
            f"   (Chi tiết kỹ thuật: {err})"
        )
        return
    uvicorn.run(app_obj, host=final_host, port=final_port or 8000, log_level="warning")


def _call_server(func: Callable[..., Any], host: str, port: int, open_browser: bool) -> None:
    """Gọi hàm khởi động của web UI, chỉ truyền đúng những tham số nó nhận.

    Vì sao phải **soi chữ ký** thay vì thử dần rồi bắt `TypeError`: cách thử dần
    không phân biệt được "hàm không nhận tham số này" với "bên trong hàm có một
    `TypeError` khác", nên khi `serve()` không có tham số `host` thì lời gọi đầy
    đủ hỏng, lời gọi thứ hai cũng hỏng, và nó rơi xuống `serve(port=…)` — đánh
    rơi `open_browser` một cách im lặng. Đo được: `srtgen ui --no-browser` vẫn
    mở trình duyệt, nên `KhoiDong.command` mở thêm thẻ thứ hai đúng cái nó đang
    cố tránh.

    Soi chữ ký trước còn cho phép để mọi lỗi *bên trong* máy chủ nổi lên nguyên
    vẹn, thay vì bị nuốt thành một câu "không khởi động được" chung chung.
    """
    import inspect  # noqa: PLC0415 - nạp muộn cho đồng bộ với phần còn lại của file

    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):  # pragma: no cover - hàm dựng sẵn, không soi được
        params = {}
    nhan_moi_thu = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())

    def nhan(ten: str) -> bool:
        return nhan_moi_thu or ten in params

    kwargs: dict[str, Any] = {}
    if nhan("open_browser"):
        kwargs["open_browser"] = open_browser
    if port and nhan("port"):
        kwargs["port"] = port
    if nhan("host"):
        kwargs["host"] = host
    elif host and host not in ("127.0.0.1", "localhost"):
        OUT.warn(f"Bản này chỉ nghe được ở 127.0.0.1, nên bỏ qua --host {host}.")

    if not open_browser and not nhan("open_browser"):
        # Không phải ngõ cụt: `_start_ui` đã đặt SRTGEN_NO_BROWSER=1 từ trước.
        OUT.dim("Máy chủ bản này không nhận tham số open_browser; dùng SRTGEN_NO_BROWSER=1 thay thế.")

    if params:
        try:
            inspect.signature(func).bind(**kwargs)
        except TypeError as err:
            _fail(f"Không khởi động được giao diện web. (Chi tiết kỹ thuật: {err})")
            return
    func(**kwargs)


# --------------------------------------------------------------------------- #
# Điểm vào
# --------------------------------------------------------------------------- #

def _fallback_main(argv: Sequence[str]) -> int:  # pragma: no cover - máy chưa cài typer
    """Đường lui khi máy chưa có `typer`: vẫn phải chạy được `doctor`.

    Đây không phải bản CLI thứ hai — nó chỉ cứu đúng tình huống cài dở dang, tức là
    tình huống người dùng cần `doctor` nhất mà lại không chạy được lệnh nào.
    """
    command = (argv[0] if argv else "").strip().lower()
    if command == "doctor":
        return _print_doctor(doctor_checks())
    OUT.error(f"{CROSS} Bản cài này còn thiếu thư viện “typer” nên chưa dùng được lệnh “{command or ''}”.")
    OUT.print(f"   Cách xử lý: {_pip_install('typer rich')}")
    OUT.print("   Sau đó chạy lại: srtgen doctor")
    return 1


def main() -> None:
    """Điểm vào của lệnh `srtgen`.

    `configure_console()` phải là việc đầu tiên: nếu không, dòng chữ Hán đầu tiên in
    ra sẽ làm chết cả tiến trình trên console Windows mã cp1252 — và lỗi đó xảy ra
    *trước* khi người dùng kịp thấy bất cứ thông báo nào có ích.
    """
    io_utils.configure_console()
    if app is None:  # pragma: no cover - máy chưa cài typer
        raise SystemExit(_fallback_main(sys.argv[1:]))
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
