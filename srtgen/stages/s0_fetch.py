"""S0 — lấy nguồn: từ link YouTube hoặc file có sẵn ra `audio.wav` 16kHz mono.

Vì sao chặng này được viết kỹ đến mức này, dù nó "chỉ gọi yt-dlp":

* **Đây là nơi người dùng gặp lỗi nhiều nhất.** Trong thực tế, gần như mọi báo lỗi
  của người dùng cuối đều rơi vào bốn nhóm: yt-dlp quá cũ, thiếu ffmpeg, mất mạng,
  video riêng tư/bị chặn. Người dùng của tool này không phải dân IT, nên một dòng
  `ERROR: unable to extract player response` là vô nghĩa với họ. Toàn bộ phần
  `_ERROR_RULES` bên dưới tồn tại để đổi thứ đó thành một câu tiếng Việt kèm **một
  việc cụ thể phải làm**, và một mã `fix_action` để web UI dựng đúng cái nút bấm.

* **Đường dẫn trên máy người dùng có dấu tiếng Việt và khoảng trắng.** Vì vậy mọi
  lệnh ngoài đều gọi bằng `subprocess` với **danh sách tham số**, không bao giờ nối
  chuỗi shell và không bao giờ `shell=True`. Đây là ràng buộc trong `docs/plan.md`
  mục 4, không phải sở thích.

* **macOS mở app từ Finder cho PATH rất nghèo.** Thư mục `bin` riêng của SrtGen
  (nơi bộ cài và nút “Cài ffmpeg giúp tôi” đặt ffmpeg), `/usr/local/bin`,
  `/opt/homebrew/bin` thường KHÔNG có trong PATH đó, nên `shutil.which("ffmpeg")`
  trả `None` dù ffmpeg đang nằm sẵn trên đĩa. `which_tool()` vì thế dò thẳng các
  thư mục đó, và đường dẫn ffmpeg tìm được còn được truyền thẳng cho yt-dlp bằng
  `--ffmpeg-location`.

* **Không bao giờ bảo người dùng cài Homebrew.** Bộ cài cố ý bỏ Homebrew (kéo theo
  Xcode ~1GB, hỏi mật khẩu quản trị, 10–20 phút — build-spec-v2 mục 6). Mọi câu
  "cách xử lý" ở đây chỉ trỏ tới nút bấm trong app hoặc tới việc chạy lại
  `CaiDat.command`, tức đúng hai đường mà người dùng không phải dân IT làm được.

* **16kHz mono ép ngay từ đầu.** Whisper chỉ dùng đúng định dạng đó; ép ở khâu
  postprocessor của yt-dlp thì cả pipeline không phải convert lần hai một file
  40 phút.

Chặng này cũng là nơi đặt các tiện ích dùng chung cho S1/S2 và cho `srtgen doctor`:
`which_tool`, `ffmpeg_path`, `ffprobe_path`, `ytdlp_path`, `probe_duration`,
`run_ffmpeg`. Đặt ở đây thay vì một `utils.py` riêng là theo đúng phân công của
build-spec; các chặng sau chỉ việc `from srtgen.stages.s0_fetch import ...`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Sequence

from srtgen.io_utils import nfc, safe_stem

if TYPE_CHECKING:  # chỉ dùng cho gợi ý kiểu, tránh phụ thuộc cứng lúc chạy
    from srtgen.core.context import Context

__all__ = [
    "STAGE",
    "STAGE_NAME",
    "LEGAL_NOTICE",
    "AUDIO_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "BROWSER_VIDEO_EXTENSIONS",
    "looks_like_video",
    "fetch_preview_video",
    "FetchError",
    "StageCancelled",
    "FIX_ACTIONS",
    "FIX_UPDATE_YTDLP",
    "FIX_INSTALL_YTDLP",
    "FIX_INSTALL_FFMPEG",
    "FIX_CHECK_NETWORK",
    "FIX_CHECK_LINK",
    "FIX_CHECK_FILE",
    "FIX_NEED_LOGIN",
    "FIX_GEO_BLOCKED",
    "FIX_VIDEO_TOO_LONG",
    "FIX_FREE_SPACE",
    "FIX_NONE",
    "which_tool",
    "clear_tool_cache",
    "ffmpeg_path",
    "ffprobe_path",
    "ytdlp_path",
    "ytdlp_command",
    "ytdlp_version",
    "js_runtime_path",
    "js_runtime_args",
    "require_ffmpeg",
    "probe_duration",
    "probe_audio_info",
    "run_ffmpeg",
    "human_duration",
    # tiện ích dùng chung cho các chặng chạy lâu (S1, S2) và cho web UI
    "Progress",
    "ProgressFn",
    "stream_process",
    "diagnose_error",
    "check_cancelled",
    "sleep_cancellable",
    "run",
]

ProgressFn = Callable[[str, float], None]

STAGE = 0
STAGE_NAME = "info"

#: Câu cảnh báo pháp lý, UI hiển thị **một lần**, không chặn (build-spec mục 7).
LEGAL_NOTICE = (
    "Lưu ý: tải nội dung từ YouTube có thể vi phạm điều khoản dịch vụ, tuỳ nội dung "
    "và mục đích sử dụng. Bạn tự cân nhắc trách nhiệm này; tool chỉ hỗ trợ thao tác."
)

#: Đuôi file được coi là "nguồn có sẵn trên máy" khi người dùng kéo thả vào UI.
AUDIO_EXTENSIONS = frozenset(
    {
        ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus", ".wma",
        ".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".m4v", ".ts", ".3gp",
    }
)

#: Đuôi file có hình. Người dùng soát phụ đề bằng cách NHÌN video có chữ đè lên,
#: nên file nguồn có hình thì phải giữ lại và đưa cho trình sửa phát.
VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".ts", ".flv", ".wmv"})

#: Đuôi mà thẻ <video> của Safari/Chrome phát thẳng được. Ngoài danh sách này
#: vẫn giữ file (không mất gì) nhưng trình sửa chỉ phát được tiếng.
BROWSER_VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".webm"})


def looks_like_video(path: "str | Path") -> bool:
    return Path(str(path)).suffix.lower() in VIDEO_EXTENSIONS


# Giới hạn dự phòng khi `cfg["fetch"]["max_duration_min"]` không có trong cấu hình.
# 6 tiếng đủ rộng cho mọi phim/tập, nhưng vẫn chặn được ca người dùng dán nhầm
# link livestream 24/7 rồi chờ cả ngày mà không hiểu chuyện gì đang xảy ra.
DEFAULT_MAX_DURATION_MIN = 360


# --------------------------------------------------------------------------- #
# Lỗi có thể giải thích cho người thường
# --------------------------------------------------------------------------- #

#: Cách xử lý khi thiếu ffmpeg — dùng chung cho câu dịch lỗi của yt-dlp và cho
#: `require_ffmpeg`, để hai chỗ không bao giờ nói hai kiểu khác nhau. Chỉ trỏ tới
#: nút bấm trong app (`fix_action = "install_ffmpeg"`, khoá có trong danh sách
#: trắng của POST /api/actions) và tới việc chạy lại bộ cài — KHÔNG Homebrew, không
#: Terminal: bộ cài cố ý bỏ Homebrew, và người dùng không phải dân IT không gõ lệnh.
_FFMPEG_HOW_TO = (
    "Cách xử lý: bấm nút “Cài ffmpeg giúp tôi” ngay trên màn hình này — tool tự tải "
    "ffmpeg về thư mục riêng của nó, không cần gõ lệnh, không cần mật khẩu máy. "
    "Nếu nút báo lỗi, hãy chạy lại bộ cài: bấm chuột phải vào file CaiDat.command → "
    "chọn Open (Mở), chờ cài xong rồi mở lại tool."
)

FIX_UPDATE_YTDLP = "update_ytdlp"
FIX_INSTALL_YTDLP = "install_ytdlp"
FIX_INSTALL_FFMPEG = "install_ffmpeg"
FIX_CHECK_NETWORK = "check_network"
FIX_CHECK_LINK = "check_link"
FIX_CHECK_FILE = "check_file"
FIX_NEED_LOGIN = "need_login"
FIX_GEO_BLOCKED = "geo_blocked"
FIX_VIDEO_TOO_LONG = "video_too_long"
FIX_FREE_SPACE = "free_space"
FIX_NONE = "none"

#: Tập mã hành động UI phải biết dựng nút cho. Thêm mã mới thì thêm cả ở đây.
FIX_ACTIONS = (
    FIX_UPDATE_YTDLP,
    FIX_INSTALL_YTDLP,
    FIX_INSTALL_FFMPEG,
    FIX_CHECK_NETWORK,
    FIX_CHECK_LINK,
    FIX_CHECK_FILE,
    FIX_NEED_LOGIN,
    FIX_GEO_BLOCKED,
    FIX_VIDEO_TOO_LONG,
    FIX_FREE_SPACE,
    FIX_NONE,
)


class FetchError(RuntimeError):
    """Lỗi đã được dịch sang ngôn ngữ người dùng hiểu được.

    Tách `user_message` khỏi `str(err)` là cố ý: `args[0]` vẫn là câu tiếng Việt để
    log cho gọn, còn `detail` giữ nguyên văn output của yt-dlp/ffmpeg cho phần
    "Xem chi tiết kỹ thuật" gập lại trong UI. `fix_action` là thứ UI dùng để quyết
    định hiện nút gì — một chuỗi ổn định, không phải câu chữ có thể sửa lúc nào cũng được.
    """

    def __init__(
        self,
        user_message: str,
        *,
        fix_action: str = FIX_NONE,
        detail: str = "",
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.fix_action = fix_action
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        """Dạng JSON cho `/api/jobs/{id}/events` — UI chỉ đọc đúng ba khoá này."""
        return {
            "message": self.user_message,
            "fix_action": self.fix_action,
            "detail": self.detail,
        }


class StageCancelled(RuntimeError):
    """Người dùng bấm Dừng — không phải lỗi, nên không dùng chung lớp với `FetchError`.

    UI phải phân biệt được hai thứ này: hỏng thì hiện đỏ kèm nút sửa, còn dừng theo
    ý người dùng thì chỉ cần một dòng xám. Mọi chặng chạy lâu nên ném đúng lớp này.
    """

    def __init__(self, user_message: str = "Đã dừng theo yêu cầu của bạn.") -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.fix_action = FIX_NONE
        self.detail = ""


@dataclass(frozen=True)
class _ErrorRule:
    """Một dấu hiệu trong output của công cụ ngoài → một câu tiếng Việt + một hành động."""

    patterns: tuple[str, ...]
    fix_action: str
    message: str


# Thứ tự có ý nghĩa: quét từ trên xuống, ca cụ thể phải đứng trước ca chung chung.
# Ví dụ "unable to download webpage" xuất hiện cả khi mất mạng lẫn khi yt-dlp cũ,
# nên các dấu hiệu mạng thật sự (DNS, timeout, connection reset) phải được xét trước.
_ERROR_RULES: tuple[_ErrorRule, ...] = (
    _ErrorRule(
        ("private video", "video is private", "this video is private"),
        FIX_CHECK_LINK,
        "Video này đang để chế độ riêng tư nên không tải được.\n"
        "Cách xử lý: nhờ chủ kênh chuyển video sang công khai hoặc “không công khai” "
        "(unlisted), hoặc nếu bạn xem được video khi đã đăng nhập thì hãy tải file về "
        "máy trước rồi kéo thả file đó vào ô nhập của tool.",
    ),
    _ErrorRule(
        (
            "video unavailable",
            "has been removed",
            "removed by the uploader",
            "no longer available",
            "account associated with this video has been terminated",
            "this video has been removed",
        ),
        FIX_CHECK_LINK,
        "Video không còn tồn tại (đã bị xoá hoặc kênh đã bị khoá).\n"
        "Cách xử lý: kiểm tra lại link bằng cách mở thử trong trình duyệt. Nếu trình "
        "duyệt cũng không mở được thì video đã mất, hãy tìm một link khác.",
    ),
    _ErrorRule(
        (
            # YouTube nói câu này theo vài kiểu: "not available in your country",
            # "has not made this video available in your country" — bắt phần chung.
            "available in your country",
            "blocked it in your country",
            "not available from your location",
            "geo restrict",
            "geo-restrict",
            "who has blocked it on copyright grounds",
        ),
        FIX_GEO_BLOCKED,
        "Video bị chặn ở khu vực của bạn nên không tải được.\n"
        "Cách xử lý: dùng một link khác của cùng nội dung, hoặc tải file về máy bằng "
        "một mạng khác rồi kéo thả file vào tool.",
    ),
    _ErrorRule(
        (
            "confirm you're not a bot",
            "confirm you are not a bot",
            "sign in to confirm your age",
            "age-restricted",
            "age restricted",
            "members-only",
            "join this channel",
            "requires authentication",
            "login required",
            "please sign in",
            "use --cookies",
            "--cookies-from-browser",
        ),
        FIX_NEED_LOGIN,
        "YouTube yêu cầu đăng nhập mới cho tải video này (video giới hạn độ tuổi, "
        "dành riêng cho hội viên, hoặc YouTube đang nghi ngờ máy tự động).\n"
        "Cách xử lý: bấm nút Cập nhật yt-dlp rồi thử lại một lần nữa. Nếu vẫn không "
        "được, hãy mở video trong trình duyệt, tải file về máy, rồi kéo thả file đó "
        "vào ô nhập của tool.",
    ),
    _ErrorRule(
        (
            "this live event will begin",
            "premieres in",
            "is live and",
            "live stream has not started",
        ),
        FIX_CHECK_LINK,
        "Video này đang phát trực tiếp hoặc chưa tới giờ công chiếu, chưa có bản hoàn "
        "chỉnh để tải.\n"
        "Cách xử lý: chờ buổi phát kết thúc rồi chạy lại, lúc đó YouTube mới có bản "
        "ghi đầy đủ.",
    ),
    _ErrorRule(
        (
            "ffmpeg not found",
            "ffprobe not found",
            "ffprobe and ffmpeg not found",
            "ffmpeg or avconv not found",
            "please install ffmpeg",
            "you have requested merging of multiple formats but ffmpeg is not installed",
        ),
        FIX_INSTALL_FFMPEG,
        "Máy chưa có ffmpeg — đây là công cụ dùng để chuyển đổi âm thanh, bắt buộc phải có.\n"
        + _FFMPEG_HOW_TO,
    ),
    _ErrorRule(
        ("no space left on device", "not enough space", "disk full"),
        FIX_FREE_SPACE,
        "Ổ đĩa đã đầy nên không ghi được file âm thanh.\n"
        "Cách xử lý: dọn bớt dung lượng (một video 40 phút cần khoảng 1–2 GB chỗ "
        "trống), hoặc đổi thư mục làm việc sang ổ khác trong phần Cài đặt.",
    ),
    _ErrorRule(
        (
            "getaddrinfo",
            "name or service not known",
            "temporary failure in name resolution",
            "nodename nor servname provided",
            "network is unreachable",
            "no route to host",
            "connection refused",
            "connection reset",
            "connection aborted",
            "read timed out",
            "timed out",
            "urlopen error",
            "ssl: certificate_verify_failed",
            "unable to connect to proxy",
            "remote end closed connection",
        ),
        FIX_CHECK_NETWORK,
        "Không kết nối được tới YouTube — nhiều khả năng máy đang mất mạng hoặc mạng "
        "chập chờn.\n"
        "Cách xử lý: kiểm tra Wi-Fi, thử mở youtube.com trong trình duyệt. Mạng ổn "
        "định rồi thì bấm Bắt đầu lại; tool sẽ dùng lại phần đã tải xong, không phải "
        "làm lại từ đầu.",
    ),
    _ErrorRule(
        (
            "is not a valid url",
            "unsupported url",
            "unable to extract webpage",
            "not a valid url",
        ),
        FIX_CHECK_LINK,
        "Link bạn dán không phải là một link video hợp lệ.\n"
        "Cách xử lý: mở video trên YouTube, bấm Chia sẻ → Sao chép liên kết, rồi dán "
        "lại. Link đúng có dạng https://www.youtube.com/watch?v=... hoặc https://youtu.be/...",
    ),
    _ErrorRule(
        (
            "nsig extraction failed",
            "signature extraction failed",
            "unable to extract player",
            "unable to extract yt initial data",
            "unable to extract uploader id",
            "player response",
            "confirm you are on the latest version",
            "please report this issue",
            "this version of yt-dlp is deprecated",
            "requested format is not available",
            "http error 403",
            "unable to extract",
            "some formats may be missing",
            "failed to extract any player response",
        ),
        FIX_UPDATE_YTDLP,
        "yt-dlp trên máy đã cũ so với thay đổi mới nhất của YouTube — đây là nguyên "
        "nhân phổ biến nhất và sửa rất nhanh.\n"
        "Cách xử lý: bấm nút “Cập nhật yt-dlp giúp tôi” rồi chạy lại. Việc cập nhật "
        "mất khoảng một phút và không cần gõ lệnh nào.",
    ),
    # --- các ca thuộc về ffmpeg khi xử lý file trên máy ---------------------- #
    _ErrorRule(
        (
            "invalid data found when processing input",
            "does not contain any stream",
            "moov atom not found",
            "invalid argument",
            "decoder not found",
            "unknown format",
        ),
        FIX_CHECK_FILE,
        "File âm thanh/video này bị hỏng hoặc ở định dạng tool không đọc được.\n"
        "Cách xử lý: mở thử file bằng trình phát nhạc trên máy. Nếu nó cũng không "
        "phát được thì file đã hỏng — hãy tải lại. Nếu phát được, thử chuyển file sang "
        "định dạng .mp3 hoặc .wav rồi kéo thả lại.",
    ),
    _ErrorRule(
        ("permission denied", "operation not permitted"),
        FIX_CHECK_FILE,
        "Máy không cho tool đọc/ghi ở vị trí này.\n"
        "Cách xử lý: chép file vào thư mục Tài liệu (Documents) rồi thử lại; hoặc đổi "
        "thư mục làm việc trong phần Cài đặt sang một thư mục bạn có quyền ghi.",
    ),
)

_GENERIC_FETCH_MESSAGE = (
    "Không tải được âm thanh từ link này.\n"
    "Nguyên nhân hay gặp nhất là yt-dlp đã cũ, nên hãy bấm nút “Cập nhật yt-dlp” và "
    "thử lại một lần. Nếu vẫn không được, hãy mở phần chi tiết kỹ thuật bên dưới và "
    "gửi nội dung đó cho người hỗ trợ."
)


def diagnose_error(
    output: str,
    *,
    default_message: str,
    default_fix: str,
) -> FetchError:
    """Đổi nguyên văn output của công cụ ngoài thành một `FetchError` nói tiếng Việt.

    So khớp bằng chuỗi con trên bản đã hạ chữ thường thay vì regex: thông điệp của
    yt-dlp thay đổi câu chữ theo từng bản, nhưng phần lõi ("private video",
    "nsig extraction failed") thì ổn định qua nhiều năm.
    """
    haystack = output.lower()
    for rule in _ERROR_RULES:
        if any(pattern in haystack for pattern in rule.patterns):
            return FetchError(rule.message, fix_action=rule.fix_action, detail=_tail(output))
    return FetchError(default_message, fix_action=default_fix, detail=_tail(output))


def _tail(text: str, *, lines: int = 12, limit: int = 2000) -> str:
    """Giữ phần cuối output — chỗ ffmpeg/yt-dlp đặt thông báo lỗi thật sự."""
    kept = [ln for ln in text.replace("\r", "\n").split("\n") if ln.strip()][-lines:]
    return "\n".join(kept)[-limit:]


# --------------------------------------------------------------------------- #
# Báo tiến trình
# --------------------------------------------------------------------------- #

class Progress:
    """Bọc `on_progress` để chia được thành các khoảng con và không bao giờ làm chết chặng.

    Hai lý do:

    1. Một chặng gồm nhiều bước (dò thông tin → tải → chuyển đổi). Mỗi bước chỉ biết
       tiến độ 0..1 *của riêng nó*; `sub()` ánh xạ khoảng đó vào tiến độ chung để chỗ
       gọi không phải tự nhân chia và không bao giờ báo lùi.
    2. `on_progress` do web UI cung cấp. Một ngoại lệ từ đó (mất kết nối SSE chẳng hạn)
       mà làm hỏng cả lượt chạy 30 phút thì vô lý, nên nó bị nuốt tại đây.
    """

    __slots__ = ("_fn", "_lo", "_hi")

    def __init__(self, fn: ProgressFn | None, lo: float = 0.0, hi: float = 1.0) -> None:
        self._fn = fn
        self._lo = lo
        self._hi = hi

    def sub(self, lo: float, hi: float) -> "Progress":
        span = self._hi - self._lo
        return Progress(self._fn, self._lo + span * lo, self._lo + span * hi)

    def __call__(self, message: str, fraction: float = 0.0) -> None:
        if self._fn is None:
            return
        clamped = 0.0 if fraction < 0.0 else (1.0 if fraction > 1.0 else float(fraction))
        value = self._lo + (self._hi - self._lo) * clamped
        try:
            self._fn(str(message), value)
        except Exception:  # pragma: no cover - callback của UI, không tin được
            pass


# --------------------------------------------------------------------------- #
# Tìm công cụ ngoài
# --------------------------------------------------------------------------- #

def _venv_dirs() -> tuple[str, ...]:
    """Thư mục thực thi của môi trường Python đang chạy.

    Ưu tiên cao nhất vì nút “Cập nhật yt-dlp” trong app sẽ cài vào đúng venv này;
    nếu PATH lại trỏ tới một bản brew cũ hơn thì người dùng bấm cập nhật mãi mà
    không thấy gì thay đổi.
    """
    prefix = Path(sys.prefix)
    return (
        str(prefix / "bin"),
        str(prefix / "Scripts"),
        str(Path(sys.executable).resolve().parent),
    )


def _app_bin_dirs() -> tuple[str, ...]:
    """Thư mục `bin` riêng của SrtGen (`~/Library/Application Support/SrtGen/bin`).

    Đây là nơi cả nút “Cài ffmpeg giúp tôi” lẫn `CaiDat.command` đặt ffmpeg/ffprobe
    bản tĩnh. Phải dò thẳng thư mục này chứ không trông vào PATH: chỉ
    `KhoiDong.command` chèn nó vào PATH, còn mở tool bằng đường khác thì không. Thiếu
    bước dò này, câu báo lỗi bảo người dùng "bấm nút cài" thành lời hứa suông —
    bấm xong, ffmpeg nằm ngay trên đĩa mà tool vẫn báo thiếu.

    Nhập muộn `user_data_dir` để chặng này vẫn nạp được trên máy thiếu thư viện
    (doctor phải chạy được đúng lúc máy thiếu đồ).
    """
    try:
        from srtgen.core.context import user_data_dir

        return (str(user_data_dir() / "bin"),)
    except Exception:  # pragma: no cover - không để việc dò thư mục làm chết doctor
        return ()


#: Nơi các trình quản lý gói (Homebrew/MacPorts) đặt file thực thi. Bộ cài KHÔNG
#: dùng chúng, nhưng máy nào đã có sẵn ffmpeg từ đó thì vẫn dùng luôn (build-spec-v2
#: mục 6: "máy đã có ffmpeg hệ thống thì dùng luôn"). Cần dò tay vì app mở từ
#: Finder trên macOS chỉ nhận được PATH tối thiểu (`/usr/bin:/bin:/usr/sbin:/sbin`).
_SYSTEM_DIRS: tuple[str, ...] = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/local/bin",
    "/usr/bin",
    "/bin",
)

_TOOL_CACHE: dict[str, str | None] = {}


def which_tool(name: str, *, refresh: bool = False) -> str | None:
    """Tìm đường dẫn tuyệt đối của một công cụ ngoài, hoặc `None` nếu máy chưa có.

    Thứ tự tìm: venv của app → thư mục `bin` riêng của SrtGen → PATH → thư mục
    Homebrew/MacPorts có sẵn trên máy. Lý do thứ tự này nằm ở docstring của
    `_venv_dirs`, `_app_bin_dirs` và `_SYSTEM_DIRS`.

    Chỉ nhớ đệm khi ĐÃ tìm thấy, để không phải quét đĩa lại trong vòng lặp tiến
    trình. Kết quả "chưa có" thì không bao giờ nhớ: người dùng vừa bấm “Cài ffmpeg
    giúp tôi” xong trong lúc app vẫn chạy, bấm Bắt đầu lại mà tool vẫn trả lời
    "máy chưa có ffmpeg" từ bộ nhớ cũ thì với họ cái nút đó không làm gì cả. Một lần
    dò hụt chỉ tốn vài lời gọi `shutil.which`, và chỉ xảy ra khi job đằng nào cũng
    sắp báo lỗi. `refresh=True` vẫn xoá đệm cho đúng một tên (vd. tool vừa bị gỡ).
    """
    if refresh:
        _TOOL_CACHE.pop(name, None)
    elif name in _TOOL_CACHE:
        return _TOOL_CACHE[name]

    found: str | None = None
    for directory in (*_venv_dirs(), *_app_bin_dirs()):
        found = shutil.which(name, path=directory)
        if found:
            break
    if not found:
        found = shutil.which(name)
    if not found:
        for directory in _SYSTEM_DIRS:
            found = shutil.which(name, path=directory)
            if found:
                break
    if found:
        _TOOL_CACHE[name] = found
    return found


def clear_tool_cache() -> None:
    """Quên toàn bộ đường dẫn đã dò — gọi sau khi cài đặt thêm công cụ."""
    _TOOL_CACHE.clear()


def ffmpeg_path() -> str | None:
    """Đường dẫn `ffmpeg`, hoặc `None`. Dùng cho `srtgen doctor` và cho `--ffmpeg-location`."""
    return which_tool("ffmpeg")


def ffprobe_path() -> str | None:
    """Đường dẫn `ffprobe`. Luôn đi kèm ffmpeg, nhưng vẫn dò riêng phòng bản cài thiếu."""
    return which_tool("ffprobe")


def ytdlp_path() -> str | None:
    """Đường dẫn `yt-dlp`. `None` không có nghĩa là không chạy được — xem `ytdlp_command`."""
    return which_tool("yt-dlp") or which_tool("yt_dlp")


def js_runtime_path() -> tuple[str, str] | None:
    """`(tên, đường dẫn)` của deno hoặc node, hoặc `None`.

    Từ giữa 2025 yt-dlp cần một JavaScript runtime để giải mã YouTube; không có thì
    nó chỉ cảnh báo "deprecated, some formats may be missing" nhưng thực tế trên
    nhiều video là lỗi 403 hoặc thiếu hẳn định dạng audio. yt-dlp chỉ tự dò deno,
    nên node có sẵn trên máy cũng phải được chỉ đích danh bằng `--js-runtimes`.
    Thứ tự tìm: thư mục bin của app (bộ cài macOS tải deno vào đó, máy trắng không
    có gì khác), rồi các chỗ Homebrew/MacPorts hay đặt, rồi PATH.
    """
    dirs: list[Path] = []
    try:
        from srtgen.core.context import user_data_dir

        dirs.append(user_data_dir() / "bin")
    except Exception:  # pragma: no cover - thiếu platformdirs
        pass
    dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/opt/local/bin")]
    for name in ("deno", "node"):
        for d in dirs:
            for suffix in ("", ".exe"):
                candidate = d / f"{name}{suffix}"
                if candidate.is_file():
                    return name, str(candidate)
        found = shutil.which(name)
        if found:
            return name, found
    return None


def js_runtime_args() -> list[str]:
    """Tham số `--js-runtimes` cho yt-dlp, rỗng nếu máy không có runtime nào."""
    runtime = js_runtime_path()
    if runtime is None:
        return []
    name, path = runtime
    return ["--js-runtimes", f"{name}:{path}"]


def ytdlp_command() -> list[str] | None:
    """Câu lệnh gọi yt-dlp dưới dạng list, hoặc `None` nếu máy hoàn toàn chưa có.

    Có bản CLI thì dùng bản CLI. Không có thì thử `python -m yt_dlp` của chính venv
    đang chạy: `pip install yt-dlp` không phải lúc nào cũng tạo được file thực thi
    trên PATH, mà module thì luôn nạp được.
    """
    exe = ytdlp_path()
    if exe:
        return [exe]
    try:
        import importlib.util

        if importlib.util.find_spec("yt_dlp") is not None:
            return [sys.executable, "-m", "yt_dlp"]
    except (ImportError, ValueError):  # pragma: no cover - máy lạ
        pass
    return None


def ytdlp_version() -> str | None:
    """Chuỗi phiên bản yt-dlp (dạng ngày `2025.01.26`), hoặc `None`.

    `srtgen doctor` dùng để cảnh báo sớm "bản này đã cũ N ngày" — rẻ hơn nhiều so với
    việc để người dùng chờ tải 20 phút rồi mới gặp lỗi trích xuất.
    """
    cmd = ytdlp_command()
    if cmd is None:
        return None
    try:
        proc = subprocess.run(
            [*cmd, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env=_child_env(),
            **_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    version = (proc.stdout or "").strip().splitlines()
    return version[0].strip() if version and version[0].strip() else None


def require_ffmpeg() -> str:
    """Trả đường dẫn ffmpeg, hoặc ném lỗi đã dịch sẵn kèm nút “Cài ffmpeg giúp tôi”."""
    exe = ffmpeg_path()
    if exe:
        return exe
    raise FetchError(
        "Máy chưa có ffmpeg — đây là công cụ xử lý âm thanh mà tool bắt buộc phải dùng.\n"
        + _FFMPEG_HOW_TO,
        fix_action=FIX_INSTALL_FFMPEG,
    )


def _require_ytdlp() -> list[str]:
    cmd = ytdlp_command()
    if cmd:
        return cmd
    raise FetchError(
        "Máy chưa có yt-dlp — đây là công cụ dùng để tải âm thanh từ YouTube.\n"
        "Cách xử lý: bấm nút “Cài yt-dlp giúp tôi”. Nếu nút báo lỗi, hãy chạy lại bộ "
        "cài: bấm chuột phải vào file CaiDat.command → chọn Open (Mở), chờ cài xong "
        "rồi mở lại tool.",
        fix_action=FIX_INSTALL_YTDLP,
    )


# --------------------------------------------------------------------------- #
# Chạy tiến trình con
# --------------------------------------------------------------------------- #

def _no_window() -> dict[str, Any]:
    """Trên Windows, chặn cửa sổ console đen nhấp nháy khi app chạy nền."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def _child_env() -> dict[str, str]:
    """Môi trường cho tiến trình con.

    Ép `PYTHONIOENCODING=utf-8` vì yt-dlp in tiêu đề video bằng tiếng Trung; để mặc
    định thì trên Windows nó vỡ thành `?????` và phần dò lỗi bên dưới mất manh mối.
    Đồng thời chèn thư mục ffmpeg vào PATH của con, để postprocessor của yt-dlp tìm
    thấy ffmpeg ngay cả khi app được mở từ Finder với PATH nghèo nàn.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("PYTHONUTF8", "1")
    # yt-dlp cất cache ở ~/.cache/yt-dlp theo mặc định — nằm ngoài mọi thư mục mà
    # GoCaiDat.command dọn, nên lời hứa "gỡ sạch như chưa từng cài" sẽ sai. yt-dlp
    # đọc XDG_CACHE_HOME trước, nên trỏ nó vào thư mục cache của app để cache rơi vào
    # ~/Library/Caches/SrtGen/yt-dlp, chỗ bộ gỡ cài đã biết. ffmpeg không dùng biến này.
    try:
        from srtgen.core.context import user_cache_dir

        env["XDG_CACHE_HOME"] = str(user_cache_dir())
    except Exception:  # pragma: no cover - platformdirs thiếu thì chịu cache mặc định
        pass
    exe = ffmpeg_path()
    if exe:
        directory = str(Path(exe).parent)
        path = env.get("PATH", "")
        if directory not in path.split(os.pathsep):
            env["PATH"] = f"{directory}{os.pathsep}{path}" if path else directory
    return env


def stream_process(
    cmd: Sequence[str],
    *,
    ctx: "Context | None" = None,
    on_line: Callable[[str], None] | None = None,
    timeout: float | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> tuple[int, str]:
    """Chạy một lệnh, đọc output *theo dòng ngay khi có*, trả `(mã thoát, toàn bộ output)`.

    Vì sao không dùng `subprocess.run`: chặng này chạy 5–30 phút, người dùng phải
    thấy phần trăm nhích lên và phải bấm Dừng được. `run()` chỉ trả về khi xong.

    Vì sao đọc bằng `os.read` chứ không lặp `for line in proc.stdout`:
    * ffmpeg và yt-dlp báo tiến trình bằng ký tự `\\r` (ghi đè cùng một dòng), nên
      trình lặp theo dòng của Python sẽ ngồi chờ tới tận khi tải xong mới nhả dữ liệu.
    * `os.read` trả về ngay khi có byte đầu tiên, nhờ vậy `ctx.is_cancelled()` được
      hỏi vài lần mỗi giây thay vì vài lần mỗi phút.

    `timeout` được canh bằng một `Timer` giết tiến trình: nếu mạng treo hẳn thì tiến
    trình im lặng, mà vòng đọc đang chờ dữ liệu sẽ không tự thoát ra được.
    """
    try:
        proc = subprocess.Popen(  # noqa: S603 - cmd luôn là list, không có shell
            list(cmd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            bufsize=0,
            cwd=str(cwd) if cwd else None,
            env=_child_env(),
            **_no_window(),
        )
    except FileNotFoundError as err:
        raise FetchError(
            f"Không tìm thấy chương trình “{cmd[0]}” trên máy.\n"
            "Cách xử lý: chạy “Kiểm tra hệ thống” trong phần Cài đặt để tool chỉ ra "
            "đúng công cụ còn thiếu và cách cài.",
            fix_action=FIX_INSTALL_FFMPEG if "ffmpeg" in str(cmd[0]) else FIX_INSTALL_YTDLP,
            detail=str(err),
        ) from err
    except OSError as err:
        raise FetchError(
            "Không khởi chạy được công cụ xử lý âm thanh.\n"
            f"Cách xử lý: khởi động lại tool. Nếu vẫn lỗi, gửi dòng sau cho người hỗ trợ: {err}",
            fix_action=FIX_NONE,
            detail=str(err),
        ) from err

    timed_out = threading.Event()
    watchdog: threading.Timer | None = None
    if timeout and timeout > 0:

        def _kill_on_timeout() -> None:
            timed_out.set()
            _terminate(proc)

        watchdog = threading.Timer(timeout, _kill_on_timeout)
        watchdog.daemon = True
        watchdog.start()

    collected: list[str] = []
    cancelled = False
    buffer = b""
    stream = proc.stdout
    assert stream is not None  # stdout=PIPE ở trên nên luôn có
    fd = stream.fileno()
    try:
        while True:
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # ống bị đóng khi tiến trình bị giết
                break
            if not chunk:
                break
            buffer += chunk
            *pieces, buffer = re.split(rb"[\r\n]", buffer)
            for piece in pieces:
                line = piece.decode("utf-8", "replace").strip()
                if not line:
                    continue
                collected.append(line)
                if on_line is not None:
                    on_line(line)
            if ctx is not None and ctx.is_cancelled():
                cancelled = True
                _terminate(proc)
                break
        if buffer.strip():
            line = buffer.decode("utf-8", "replace").strip()
            collected.append(line)
            if on_line is not None and not cancelled:
                on_line(line)
    finally:
        if watchdog is not None:
            watchdog.cancel()
        try:
            stream.close()
        except OSError:  # pragma: no cover
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            _terminate(proc)

    output = "\n".join(collected)
    if cancelled:
        raise StageCancelled()
    if timed_out.is_set():
        raise FetchError(
            "Quá thời gian chờ — công cụ tải/xử lý không phản hồi nữa.\n"
            "Cách xử lý: kiểm tra kết nối mạng rồi bấm Bắt đầu lại; phần đã tải xong "
            "vẫn được dùng lại. Nếu mạng chậm, tăng “Thời gian chờ” trong Cài đặt nâng cao.",
            fix_action=FIX_CHECK_NETWORK,
            detail=_tail(output),
        )
    return proc.returncode, output


def _terminate(proc: "subprocess.Popen[bytes]") -> None:
    """Dừng tiến trình con tử tế trước, cứng rắn sau — ffmpeg cần kịp đóng file đang ghi."""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
        except OSError:  # pragma: no cover
            pass


def sleep_cancellable(seconds: float, ctx: "Context | None") -> None:
    """Nghỉ giữa hai lần thử lại, nhưng vẫn bấm Dừng được trong lúc nghỉ."""
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if ctx is not None and ctx.is_cancelled():
            raise StageCancelled()
        time.sleep(0.2)


def check_cancelled(ctx: "Context | None") -> None:
    if ctx is not None and ctx.is_cancelled():
        raise StageCancelled()


# --------------------------------------------------------------------------- #
# ffprobe / ffmpeg
# --------------------------------------------------------------------------- #

def probe_duration(path: str | os.PathLike[str]) -> float:
    """Thời lượng file tính bằng giây; `0.0` nghĩa là **không xác định được**.

    Trả 0 thay vì ném lỗi là có chủ ý: thời lượng ở đây dùng để tính phần trăm tiến
    trình và để cảnh báo video quá dài. Không đo được thì hai thứ đó tắt đi, chứ
    không có lý do gì để chặn cả lượt chạy — file vẫn có thể gỡ băng bình thường.
    """
    info = probe_audio_info(path)
    return float(info.get("duration") or 0.0)


def probe_audio_info(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Hỏi ffprobe các thông số cần cho pipeline: thời lượng, tần số mẫu, số kênh.

    Dùng `-of default=...` thay vì `-of json` để không phải nạp `json` cho một câu
    trả lời ba dòng, và để thiếu khoá nào cũng không làm vỡ cả hàm.
    """
    target = Path(path)
    result: dict[str, Any] = {"duration": 0.0, "sample_rate": 0, "channels": 0}
    exe = ffprobe_path()
    if not exe or not target.is_file():
        return result
    cmd = [
        exe,
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels:format=duration",
        "-of", "default=noprint_wrappers=1",
        str(target),
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=_child_env(),
            **_no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return result
    for line in (proc.stdout or "").splitlines():
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not value or value == "N/A":
            continue
        try:
            if key == "duration":
                result["duration"] = max(0.0, float(value))
            elif key == "sample_rate":
                result["sample_rate"] = int(float(value))
            elif key == "channels":
                result["channels"] = int(float(value))
        except ValueError:
            continue
    return result


_FFMPEG_TIME_RE = re.compile(r"^out_time_(?P<unit>ms|us)=(?P<value>-?\d+)$")


def run_ffmpeg(
    args: Sequence[str],
    *,
    ctx: "Context | None" = None,
    report: Progress | ProgressFn | None = None,
    label: str = "Đang xử lý âm thanh",
    total: float = 0.0,
    timeout: float | None = None,
) -> str:
    """Chạy ffmpeg với tiến trình đọc được và huỷ được; trả về output để chẩn đoán.

    `-progress pipe:1` là điểm mấu chốt: nó in `out_time_us=<micro giây>` mỗi khoảng
    nửa giây theo dạng khoá=giá trị, ổn định qua mọi bản ffmpeg — khác hẳn dòng
    `size=... time=...` dành cho người đọc, vốn hay đổi và khó tách.

    `args` chỉ chứa phần riêng của lệnh (input, filter, output); các tuỳ chọn toàn
    cục do hàm này thêm để mọi chỗ gọi có cùng hành vi: không đọc stdin (`-nostdin`,
    nếu không ffmpeg nuốt mất phím bấm của tiến trình cha), ghi đè không hỏi (`-y`),
    và chỉ in lỗi thật.
    """
    exe = require_ffmpeg()
    progress = report if isinstance(report, Progress) else Progress(report)
    cmd = [
        exe,
        "-hide_banner",
        "-nostdin",
        "-loglevel", "error",
        "-progress", "pipe:1",
        "-nostats",
        "-y",
        *[str(a) for a in args],
    ]

    state = {"fraction": 0.0}

    def _on_line(line: str) -> None:
        match = _FFMPEG_TIME_RE.match(line)
        if match is None:
            return
        micro = float(match.group("value"))
        if match.group("unit") == "ms":
            micro *= 1000.0
        seconds = micro / 1_000_000.0
        if total > 0:
            fraction = min(0.99, max(state["fraction"], seconds / total))
            state["fraction"] = fraction
            progress(f"{label} — {int(fraction * 100)}%", fraction)
        else:
            progress(f"{label} — {human_duration(seconds)}", 0.5)

    progress(f"{label}…", 0.0)
    code, output = stream_process(cmd, ctx=ctx, on_line=_on_line, timeout=timeout)
    if code != 0:
        raise diagnose_error(
            output,
            default_message=(
                "Chuyển đổi âm thanh thất bại.\n"
                "Cách xử lý: kiểm tra file nguồn có mở được bằng trình phát nhạc không. "
                "Nếu file bình thường, hãy gửi phần chi tiết kỹ thuật bên dưới cho người hỗ trợ."
            ),
            default_fix=FIX_CHECK_FILE,
        )
    progress(f"{label} — xong", 1.0)
    return output


# --------------------------------------------------------------------------- #
# Trình bày cho người đọc
# --------------------------------------------------------------------------- #

def human_duration(seconds: float) -> str:
    """"3730" → "1 giờ 2 phút". Dùng trong mọi câu thông báo, kể cả câu báo lỗi."""
    total = int(max(0.0, seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} giờ {minutes} phút"
    if minutes:
        return f"{minutes} phút {secs} giây"
    return f"{secs} giây"


# --------------------------------------------------------------------------- #
# Nhận dạng nguồn
# --------------------------------------------------------------------------- #

_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def _as_local_path(source: str) -> Path | None:
    """Nguồn có phải file trên máy không? Trả `Path` khi file tồn tại, `None` khi là link.

    Chấp nhận cả `file:///Users/...` vì trình duyệt và Finder hay đưa ra dạng đó khi
    kéo thả, và người dùng thì không phân biệt được hai kiểu đường dẫn này.
    """
    text = nfc(source.strip().strip('"').strip("'"))
    if not text:
        return None
    if text.lower().startswith("file://"):
        from urllib.parse import unquote, urlparse

        parsed = urlparse(text)
        raw = unquote(parsed.path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", raw):
            raw = raw[1:]
        candidate = Path(raw)
        return candidate if candidate.is_file() else None
    if _URL_RE.match(text) or text.lower().startswith("www."):
        return None
    candidate = Path(text).expanduser()
    return candidate if candidate.is_file() else None


def _looks_like_path(source: str) -> bool:
    """Người dùng gõ nhầm đường dẫn hay dán nhầm link? Câu báo lỗi phải khác nhau."""
    text = source.strip()
    if _URL_RE.match(text):
        return False
    return os.sep in text or "/" in text or Path(text).suffix.lower() in AUDIO_EXTENSIONS


# --------------------------------------------------------------------------- #
# Chặng S0
# --------------------------------------------------------------------------- #

def run(ctx: "Context", on_progress: ProgressFn | None = None) -> dict[str, Any]:
    """Lấy nguồn về thành `work/<video_id>/audio.wav` 16kHz mono và ghi `S0_info.json`.

    Trả về chính nội dung `S0_info.json` (chặng sau đọc `audio_path` và `duration` từ đây).

    Chạy tiếp giữa chừng: có sẵn kết quả cũ **và** file wav vẫn còn trên đĩa thì trả
    về ngay. Kiểm cả file chứ không chỉ kiểm file JSON là cố ý — người dùng hay dọn ổ
    đĩa bằng cách xoá file nặng, mà JSON thì nhẹ nên sống sót; tin mỗi JSON sẽ khiến
    chặng ASR chết vì thiếu audio sau 20 phút.
    """
    report = Progress(on_progress)

    cached = _reuse_previous(ctx)
    if cached is not None:
        report("Đã có sẵn âm thanh từ lần chạy trước, bỏ qua bước tải.", 1.0)
        _publish_meta(ctx, cached)
        return cached

    source = str(ctx.meta.get("source") or "").strip()
    if not source:
        raise FetchError(
            "Chưa có nguồn để xử lý.\n"
            "Cách xử lý: dán link YouTube vào ô nhập, hoặc kéo thả một file âm thanh/"
            "video từ máy vào đó.",
            fix_action=FIX_CHECK_LINK,
        )

    check_cancelled(ctx)
    local = _as_local_path(source)
    if local is not None:
        info = _fetch_local(ctx, local, report)
    elif _looks_like_path(source):
        raise FetchError(
            f"Không tìm thấy file: {source}\n"
            "Cách xử lý: file có thể đã bị di chuyển hoặc đổi tên. Hãy kéo thả lại file "
            "từ Finder vào ô nhập của tool.",
            fix_action=FIX_CHECK_FILE,
        )
    else:
        info = _fetch_url(ctx, source, report)

    ctx.save_stage(STAGE, STAGE_NAME, info)
    _publish_meta(ctx, info)
    report("Đã có âm thanh 16kHz mono, sẵn sàng cho bước tiếp theo.", 1.0)
    return info


def _reuse_previous(ctx: "Context") -> dict[str, Any] | None:
    """Kết quả cũ chỉ dùng lại được khi file wav nó trỏ tới vẫn còn và không rỗng."""
    if not ctx.has_stage(STAGE, STAGE_NAME):
        return None
    saved = ctx.load_stage(STAGE, STAGE_NAME)
    if not isinstance(saved, dict):
        return None
    audio = saved.get("audio_path")
    if not audio:
        return None
    path = Path(str(audio))
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    return saved


def _publish_meta(ctx: "Context", info: dict[str, Any]) -> None:
    """Đưa tiêu đề/thời lượng vào `ctx.meta` để S2 mồi prompt và S8 đặt tên file ra."""
    for key in ("title", "duration", "uploader", "source_url", "stem", "audio_path", "video_path"):
        if key in info:
            ctx.meta[key] = info[key]


def _target_wav(ctx: "Context") -> Path:
    return ctx.work_dir / "audio.wav"


def _fetch_cfg(ctx: "Context") -> dict[str, Any]:
    cfg = ctx.cfg.get("fetch")
    return cfg if isinstance(cfg, dict) else {}


def _max_duration_seconds(fetch_cfg: dict[str, Any]) -> float:
    minutes = fetch_cfg.get("max_duration_min", DEFAULT_MAX_DURATION_MIN)
    try:
        value = float(minutes)
    except (TypeError, ValueError):
        value = float(DEFAULT_MAX_DURATION_MIN)
    return value * 60.0 if value > 0 else 0.0


def _check_duration(duration: float, limit: float) -> None:
    """Chặn trước khi tải, không phải sau — 4 tiếng audio là gần 1GB và 3 tiếng gỡ băng."""
    if limit <= 0 or duration <= 0 or duration <= limit:
        return
    raise FetchError(
        f"Video dài {human_duration(duration)}, vượt giới hạn hiện tại là "
        f"{human_duration(limit)}.\n"
        "Cách xử lý: cắt bớt video rồi kéo thả file vào tool, hoặc mở Cài đặt nâng cao "
        "và tăng mục “Thời lượng tối đa”. Lưu ý bước gỡ băng mất khoảng bằng thời lượng "
        "video, nên một video quá dài sẽ chạy rất lâu.",
        fix_action=FIX_VIDEO_TOO_LONG,
    )


# --- nguồn là file có sẵn trên máy ----------------------------------------- #

def _fetch_local(ctx: "Context", path: Path, report: Progress) -> dict[str, Any]:
    """File trên máy thì không tải gì cả, chỉ chuyển sang 16kHz mono bằng ffmpeg."""
    fetch_cfg = _fetch_cfg(ctx)
    report(f"Đang đọc file: {path.name}", 0.02)

    info = probe_audio_info(path)
    duration = float(info.get("duration") or 0.0)
    if duration <= 0 and not ffprobe_path():
        # Không có ffprobe thì không đo được, nhưng ffmpeg vẫn chuyển đổi được:
        # mất thanh phần trăm còn hơn là chặn người dùng lại.
        report("Không đo được thời lượng file (thiếu ffprobe), vẫn tiếp tục xử lý.", 0.03)
    _check_duration(duration, _max_duration_seconds(fetch_cfg))
    check_cancelled(ctx)

    target = _target_wav(ctx)
    sample_rate = int(fetch_cfg.get("sample_rate") or 16000)
    channels = int(fetch_cfg.get("channels") or 1)
    run_ffmpeg(
        [
            "-i", str(path),
            "-vn",
            "-ac", str(channels),
            "-ar", str(sample_rate),
            "-c:a", "pcm_s16le",
            str(target),
        ],
        ctx=ctx,
        report=report.sub(0.05, 0.95),
        label="Đang chuyển âm thanh sang 16kHz mono",
        total=duration,
        timeout=_timeout(fetch_cfg),
    )

    final = probe_audio_info(target)
    final_duration = float(final.get("duration") or duration)
    _assert_produced(target, final_duration)

    title = nfc(path.stem)
    return {
        "video_id": ctx.video_id,
        "id": ctx.video_id,
        "title": title,
        "stem": safe_stem(title),
        "duration": round(final_duration, 3),
        "uploader": "",
        "source": str(path),
        "source_url": str(path),
        "is_local": True,
        "extractor": "local",
        "audio_path": str(target),
        # File nguồn có hình thì giữ đường dẫn để trình sửa phát video có phụ đề đè lên.
        "video_path": str(path) if looks_like_video(path) else "",
        "sample_rate": int(final.get("sample_rate") or sample_rate),
        "channels": int(final.get("channels") or channels),
        "tool": "ffmpeg",
        "fetched_at": _now(),
    }


# --- nguồn là link ---------------------------------------------------------- #

_YTDLP_PERCENT_RE = re.compile(r"\[download\]\s+(\d{1,3}(?:\.\d+)?)%")


def _fetch_url(ctx: "Context", url: str, report: Progress) -> dict[str, Any]:
    """Dò thông tin trước, tải sau.

    Một lần gọi `-J` mất vài giây nhưng đổi lại: biết ngay video riêng tư / bị chặn /
    quá dài **trước khi** tải, và có `duration` để hiển thị "còn khoảng bao lâu" ngay
    từ phần trăm đầu tiên. Với người dùng không rành máy, biết sớm quan trọng hơn
    nhanh vài giây.
    """
    cmd = _require_ytdlp()
    require_ffmpeg()  # postprocessor của yt-dlp cần, hỏi trước cho lỗi sạch sẽ
    fetch_cfg = _fetch_cfg(ctx)
    report(LEGAL_NOTICE, 0.0)

    meta = _probe_url(ctx, cmd, url, fetch_cfg, report.sub(0.0, 0.08))
    duration = float(meta.get("duration") or 0.0)
    title = nfc(str(meta.get("title") or "")) or ctx.video_id
    if duration > 0:
        report(
            f"“{title}” — dài {human_duration(duration)}. Bắt đầu tải âm thanh…",
            0.08,
        )
    _check_duration(duration, _max_duration_seconds(fetch_cfg))
    check_cancelled(ctx)

    video_path = ""
    preview_note = ""
    if _wants_preview_video(fetch_cfg):
        # Tải bản có hình (mp4, giới hạn chiều cao) rồi trích wav từ chính nó: một
        # lần tải cho cả hai việc, và người dùng soát phụ đề được bằng mắt trên đúng
        # video. Tải hình hỏng thì không dừng cả pipeline — lùi về tải tiếng.
        try:
            video = _download_video(ctx, cmd, url, fetch_cfg, report.sub(0.08, 0.80))
            check_cancelled(ctx)
            _extract_wav(ctx, video, duration, fetch_cfg, report.sub(0.80, 0.97))
            video_path = str(video)
            target = _target_wav(ctx)
        except FetchError as err:
            if _is_cancel(err):
                raise
            preview_note = (
                "Không tải được bản có hình để xem trước, đã tải phần tiếng thay thế. "
                + str(getattr(err, "user_message", "") or err).splitlines()[0]
            )
            report(preview_note, 0.08)
            target = _download_audio(ctx, cmd, url, fetch_cfg, report.sub(0.10, 0.97))
    else:
        target = _download_audio(ctx, cmd, url, fetch_cfg, report.sub(0.08, 0.97))

    final = probe_audio_info(target)
    final_duration = float(final.get("duration") or duration)
    _assert_produced(target, final_duration)

    return {
        "video_id": ctx.video_id,
        "id": str(meta.get("id") or ctx.video_id),
        "title": title,
        "stem": safe_stem(title),
        "duration": round(final_duration, 3),
        "uploader": nfc(str(meta.get("uploader") or "")),
        "source": url,
        "source_url": str(meta.get("webpage_url") or url),
        "is_local": False,
        "extractor": str(meta.get("extractor_key") or meta.get("extractor") or ""),
        "audio_path": str(target),
        "video_path": video_path,
        "preview_note": preview_note,
        "sample_rate": int(final.get("sample_rate") or fetch_cfg.get("sample_rate") or 16000),
        "channels": int(final.get("channels") or fetch_cfg.get("channels") or 1),
        "tool": f"yt-dlp {ytdlp_version() or '?'}",
        "fetched_at": _now(),
    }


def _probe_url(
    ctx: "Context",
    cmd: Sequence[str],
    url: str,
    fetch_cfg: dict[str, Any],
    report: Progress,
) -> dict[str, Any]:
    """`yt-dlp -J` → tiêu đề, thời lượng, id, người đăng."""
    report("Đang đọc thông tin video…", 0.1)
    args = [*cmd, "-J", "--no-playlist", "--no-warnings", "--no-color", "--skip-download", url]

    def _attempt(_: int) -> dict[str, Any]:
        code, output = stream_process(
            args, ctx=ctx, timeout=_timeout(fetch_cfg, short=True)
        )
        if code != 0:
            raise diagnose_error(
                output,
                default_message=_GENERIC_FETCH_MESSAGE,
                default_fix=FIX_UPDATE_YTDLP,
            )
        return _parse_info_json(output, url)

    meta = _with_retries(_attempt, ctx=ctx, fetch_cfg=fetch_cfg, report=report)
    report("Đã đọc xong thông tin video.", 1.0)
    return meta


def _parse_info_json(output: str, url: str) -> dict[str, Any]:
    """Bóc khối JSON trong output của `-J`.

    yt-dlp thỉnh thoảng chen dòng cảnh báo trước khối JSON, nên không thể
    `json.loads(output)` thẳng — phải tìm dòng bắt đầu bằng `{`.
    """
    import json

    for line in output.splitlines():
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("_type") == "playlist":
            entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
            if not entries:
                raise FetchError(
                    "Link bạn dán là một danh sách phát (playlist) trống hoặc không tải được.\n"
                    "Cách xử lý: mở danh sách phát, bấm vào đúng video muốn làm phụ đề, "
                    "rồi sao chép link của video đó.",
                    fix_action=FIX_CHECK_LINK,
                )
            raise FetchError(
                "Link bạn dán là một danh sách phát (playlist), không phải một video.\n"
                "Cách xử lý: mở danh sách phát, bấm vào đúng video muốn làm phụ đề, rồi "
                "sao chép link của riêng video đó (link có dạng .../watch?v=...).",
                fix_action=FIX_CHECK_LINK,
            )
        if data.get("is_live"):
            raise FetchError(
                "Video này đang phát trực tiếp nên chưa có bản ghi hoàn chỉnh để tải.\n"
                "Cách xử lý: chờ buổi phát kết thúc rồi chạy lại.",
                fix_action=FIX_CHECK_LINK,
            )
        return data
    raise FetchError(
        _GENERIC_FETCH_MESSAGE,
        fix_action=FIX_UPDATE_YTDLP,
        detail=_tail(output) or f"Không đọc được thông tin cho: {url}",
    )


def _wants_preview_video(fetch_cfg: dict[str, Any]) -> bool:
    value = fetch_cfg.get("preview_video", True)
    return True if value is None else bool(value)


def _is_cancel(err: BaseException) -> bool:
    return "Cancel" in type(err).__name__


def _video_format(fetch_cfg: dict[str, Any]) -> str:
    """Chuỗi -f cho yt-dlp: mp4 để Safari/Chrome phát thẳng, giới hạn chiều cao.

    Bản xem trước không cần 1080p: 720p đủ nhìn chữ, nhẹ hơn 2-3 lần, và Safari
    phát mp4/h264 không cần chuyển mã. Thứ tự ưu tiên: hình mp4 + tiếng m4a
    (ghép không mã hoá lại) → bản mp4 sẵn có → bất kỳ hình nào ≤ chiều cao + tiếng.
    """
    try:
        height = int(fetch_cfg.get("preview_max_height") or 720)
    except (TypeError, ValueError):
        height = 720
    h = f"[height<={height}]"
    return f"bv*{h}[ext=mp4]+ba[ext=m4a]/b{h}[ext=mp4]/bv*{h}+ba/b{h}/best"


def _download_video(
    ctx: "Context | None",
    cmd: Sequence[str],
    url: str,
    fetch_cfg: dict[str, Any],
    report: Progress,
    work: Path | None = None,
) -> Path:
    """Tải bản có hình về `work/source.mp4` để trình sửa phát lại có phụ đề."""
    work = work or (ctx.work_dir if ctx is not None else None)
    if work is None:
        raise FetchError("Không có thư mục làm việc để tải video.", fix_action=FIX_CHECK_FILE)
    target = work / "source.mp4"
    args = [
        *cmd,
        "--newline",
        "--no-color",
        "--no-playlist",
        "--no-warnings",
        "-f", _video_format(fetch_cfg),
        "--merge-output-format", "mp4",
        "-o", str(work / "source.%(ext)s"),
    ]
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        args += ["--ffmpeg-location", str(Path(ffmpeg).parent)]
    args += js_runtime_args()
    args.append(url)

    def _on_line(line: str) -> None:
        match = _YTDLP_PERCENT_RE.search(line)
        if match is not None:
            try:
                percent = float(match.group(1))
            except ValueError:
                return
            report(f"Đang tải video để xem trước — {int(percent)}%", min(1.0, percent / 100.0) * 0.9)
            return
        if "[merger]" in line.lower():
            report("Đang ghép hình và tiếng…", 0.95)

    def _attempt(_: int) -> None:
        code, output = stream_process(args, ctx=ctx, on_line=_on_line, timeout=_timeout(fetch_cfg))
        if code != 0:
            raise diagnose_error(output, default_message=_GENERIC_FETCH_MESSAGE, default_fix=FIX_UPDATE_YTDLP)

    _with_retries(_attempt, ctx=ctx, fetch_cfg=fetch_cfg, report=report)
    if target.is_file() and target.stat().st_size > 0:
        report("Đã tải xong video.", 1.0)
        return target
    # yt-dlp có thể giữ đuôi khác khi không ghép được sang mp4
    for candidate in sorted(work.glob("source.*")):
        if candidate.suffix.lower() in VIDEO_EXTENSIONS and candidate.stat().st_size > 0:
            report("Đã tải xong video.", 1.0)
            return candidate
    raise FetchError(
        "Tải xong nhưng không thấy file video trong thư mục làm việc.",
        fix_action=FIX_UPDATE_YTDLP,
    )


def _extract_wav(
    ctx: "Context",
    source: Path,
    duration: float,
    fetch_cfg: dict[str, Any],
    report: Progress,
) -> Path:
    """Trích 16kHz mono từ file nguồn — cùng lệnh ffmpeg mà nhánh file-trên-máy dùng."""
    target = _target_wav(ctx)
    sample_rate = int(fetch_cfg.get("sample_rate") or 16000)
    channels = int(fetch_cfg.get("channels") or 1)
    run_ffmpeg(
        ["-i", str(source), "-vn", "-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(target)],
        ctx=ctx,
        report=report,
        label="Đang tách tiếng 16kHz mono từ video",
        total=duration,
        timeout=_timeout(fetch_cfg),
    )
    return target


def fetch_preview_video(
    work_dir: Path,
    url: str,
    cfg: dict[str, Any] | None,
    on_progress: ProgressFn | None = None,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> Path:
    """Tải riêng bản có hình cho một công việc ĐÃ chạy xong mà mới chỉ có tiếng.

    Dành cho nút "Tải video để xem trước": không chạy lại gỡ băng, chỉ lấy hình.
    Không cần `Context` đầy đủ — chỉ cần thư mục làm việc và cờ huỷ.
    """
    from srtgen.core.context import Context

    cmd = _require_ytdlp()
    fetch_cfg = dict((cfg or {}).get("fetch") or {}) if isinstance(cfg, dict) else {}
    ctx = Context(
        video_id=work_dir.name,
        work_dir=work_dir,
        out_dir=work_dir,
        cfg=cfg or {},
        cancelled=cancelled or (lambda: False),
    )
    report = Progress(on_progress)
    return _download_video(ctx, cmd, url, fetch_cfg, report, work=work_dir)


def _download_audio(
    ctx: "Context",
    cmd: Sequence[str],
    url: str,
    fetch_cfg: dict[str, Any],
    report: Progress,
) -> Path:
    """Tải và ép sẵn 16kHz mono trong cùng một lần gọi yt-dlp.

    `--postprocessor-args "ExtractAudio:-ar 16000 -ac 1"` gắn tham số vào đúng bước
    trích âm thanh. Nhờ vậy file ra đã là thứ Whisper cần và pipeline không phải
    decode lại một file 40 phút lần thứ hai.
    """
    work = ctx.work_dir
    target = _target_wav(ctx)
    sample_rate = int(fetch_cfg.get("sample_rate") or 16000)
    channels = int(fetch_cfg.get("channels") or 1)
    audio_format = str(fetch_cfg.get("audio_format") or "wav")
    ytdlp_format = str(fetch_cfg.get("ytdlp_format") or "bestaudio")

    args = [
        *cmd,
        "--newline",            # tiến trình xuống dòng thay vì ghi đè, để đọc được từng mốc
        "--no-color",
        "--no-playlist",
        "--no-warnings",
        "-f", f"{ytdlp_format}/best",
        "--extract-audio",
        "--audio-format", audio_format,
        "--postprocessor-args", f"ExtractAudio:-ar {sample_rate} -ac {channels}",
        "-o", str(work / "audio.%(ext)s"),
    ]
    ffmpeg = ffmpeg_path()
    if ffmpeg:
        args += ["--ffmpeg-location", str(Path(ffmpeg).parent)]
    args += js_runtime_args()
    if fetch_cfg.get("keep_source"):
        args.append("--keep-video")
    args.append(url)

    def _on_line(line: str) -> None:
        match = _YTDLP_PERCENT_RE.search(line)
        if match is not None:
            try:
                percent = float(match.group(1))
            except ValueError:
                return
            fraction = min(1.0, percent / 100.0)
            report(f"Đang tải âm thanh — {int(percent)}%", fraction * 0.85)
            return
        lowered = line.lower()
        if "[extractaudio]" in lowered or ("destination:" in lowered and "audio." in lowered):
            report("Đang chuyển sang định dạng 16kHz mono…", 0.9)

    def _attempt(_: int) -> None:
        code, output = stream_process(
            args, ctx=ctx, on_line=_on_line, timeout=_timeout(fetch_cfg)
        )
        if code != 0:
            raise diagnose_error(
                output,
                default_message=_GENERIC_FETCH_MESSAGE,
                default_fix=FIX_UPDATE_YTDLP,
            )

    _with_retries(_attempt, ctx=ctx, fetch_cfg=fetch_cfg, report=report)

    if target.is_file() and target.stat().st_size > 0:
        report("Đã tải xong.", 1.0)
        return target
    return _rescue_output(ctx, work, target, report)


def _rescue_output(
    ctx: "Context",
    work: Path,
    target: Path,
    report: Progress,
) -> Path:
    """yt-dlp chạy xong nhưng không có `audio.wav` — tìm file nó thực sự đã ghi.

    Xảy ra khi postprocessor bị bỏ qua (định dạng nguồn lạ) hoặc khi bản yt-dlp đặt
    tên khác. Có file thì chuyển tay bằng ffmpeg còn hơn bắt người dùng tải lại.
    """
    candidates = sorted(
        (
            p
            for p in work.glob("audio.*")
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
        ),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not candidates:
        raise FetchError(
            "Tải xong nhưng không tìm thấy file âm thanh nào được tạo ra.\n"
            "Cách xử lý: bấm nút “Cập nhật yt-dlp” rồi chạy lại. Nếu vẫn vậy, hãy tải "
            "video về máy bằng trình duyệt rồi kéo thả file vào tool.",
            fix_action=FIX_UPDATE_YTDLP,
        )
    source = candidates[0]
    report("Đang chuyển âm thanh sang 16kHz mono…", 0.9)
    fetch_cfg = _fetch_cfg(ctx)
    rescued = work / "audio_16k.wav"
    run_ffmpeg(
        [
            "-i", str(source),
            "-vn",
            "-ac", str(int(fetch_cfg.get("channels") or 1)),
            "-ar", str(int(fetch_cfg.get("sample_rate") or 16000)),
            "-c:a", "pcm_s16le",
            str(rescued),
        ],
        ctx=ctx,
        report=report.sub(0.9, 1.0),
        label="Đang chuyển âm thanh sang 16kHz mono",
        total=probe_duration(source),
        timeout=_timeout(fetch_cfg),
    )
    if rescued.is_file() and rescued.stat().st_size > 0:
        return rescued
    return target  # để `_assert_produced` báo lỗi thống nhất một chỗ


def _assert_produced(path: Path, duration: float) -> None:
    """Chốt chặn cuối: có file, file không rỗng, và trong file có tiếng."""
    try:
        size = path.stat().st_size if path.is_file() else 0
    except OSError:
        size = 0
    if size <= 0:
        raise FetchError(
            "Không tạo được file âm thanh.\n"
            "Cách xử lý: kiểm tra ổ đĩa còn trống không (cần khoảng 1–2 GB cho video "
            "40 phút), rồi chạy lại.",
            fix_action=FIX_FREE_SPACE,
        )
    if duration <= 0 and ffprobe_path():
        raise FetchError(
            "File âm thanh tạo ra bị rỗng (không có tiếng).\n"
            "Cách xử lý: nếu nguồn là link, bấm “Cập nhật yt-dlp” rồi chạy lại. Nếu "
            "nguồn là file trên máy, mở thử file bằng trình phát nhạc để chắc chắn nó "
            "có tiếng.",
            fix_action=FIX_UPDATE_YTDLP,
        )


def _timeout(fetch_cfg: dict[str, Any], *, short: bool = False) -> float | None:
    """Thời gian chờ tối đa cho một lần gọi công cụ ngoài; `None` = chờ vô hạn.

    Bước dò thông tin dùng hạn ngắn hơn hẳn (tối đa 2 phút): nó chỉ tải một trang web,
    treo ở đó gần như chắc chắn là mạng hỏng chứ không phải "đang chạy lâu".
    """
    try:
        value = float(fetch_cfg.get("timeout", 900))
    except (TypeError, ValueError):
        value = 900.0
    if value <= 0:
        return None
    return min(value, 120.0) if short else value


def _with_retries(
    attempt: Callable[[int], Any],
    *,
    ctx: "Context",
    fetch_cfg: dict[str, Any],
    report: Progress,
) -> Any:
    """Thử lại **chỉ** khi lỗi là lỗi mạng.

    Thử lại một video riêng tư ba lần thì vẫn riêng tư, chỉ tổ làm người dùng chờ
    thêm. Ngược lại, mạng nhà chập chờn là ca đáng thử lại nhất và người dùng không
    nên phải bấm Bắt đầu lại bằng tay.
    """
    try:
        retries = max(1, int(fetch_cfg.get("retries", 3)))
    except (TypeError, ValueError):
        retries = 3
    try:
        wait = max(0.0, float(fetch_cfg.get("retry_wait", 5)))
    except (TypeError, ValueError):
        wait = 5.0

    last: FetchError | None = None
    for number in range(1, retries + 1):
        try:
            return attempt(number)
        except FetchError as err:
            if err.fix_action != FIX_CHECK_NETWORK or number >= retries:
                raise
            last = err
            report(
                f"Mạng đang chập chờn. Thử lại lần {number + 1}/{retries} sau "
                f"{int(wait)} giây…",
                0.0,
            )
            sleep_cancellable(wait, ctx)
    raise last if last is not None else FetchError(_GENERIC_FETCH_MESSAGE, fix_action=FIX_UPDATE_YTDLP)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
