"""Hàng đợi công việc chạy nền cho giao diện web — một việc chạy tại một thời điểm.

Vì sao module này tồn tại, và vì sao nó **không** biết gì về HTTP:

* **Công việc sống ở phía máy chủ, không sống trong tab trình duyệt.** Người dùng
  tắt tab giữa chừng (hoặc máy sập Wi-Fi) rồi mở lại vẫn phải thấy đúng tiến trình
  đang chạy. Nếu trạng thái nằm trong JavaScript thì đóng tab là mất, mà chặng
  "Nghe và gỡ băng" dài 20-40 phút — mất là hỏng cả buổi.
* **Đúng một việc chạy tại một thời điểm.** Chặng ASR ăn hết số nhân CPU đã cấp
  cho nó; chạy hai video song song trên iMac 2017 làm cả hai chậm hơn là chạy lần
  lượt, lại còn dễ hết RAM. Vì vậy: một luồng thợ, một hàng đợi.
* **Trạng thái được ghi ra đĩa.** Đóng hẳn ứng dụng rồi mở lại thì danh sách việc
  cũ vẫn còn, kèm đường dẫn file kết quả để bấm tải lại. Việc đang chạy dở lúc
  đóng ứng dụng được đánh dấu là lỗi kèm câu gợi ý chạy tiếp — luồng đã chết theo
  tiến trình, không có cách nào hồi sinh nó, nhưng `work/<id>/S*.json` vẫn còn nên
  chạy lại sẽ nhảy qua các chặng đã xong.

Ranh giới với `server.py`: module này không import `fastapi`, không biết SSE là gì.
Nó chỉ phát sự kiện vào một hàng đợi thuần Python; `server.py` là chỗ duy nhất
biến sự kiện đó thành `text/event-stream`. Nhờ vậy phần khó nhất (luồng, huỷ,
ghi đĩa) kiểm thử được mà không cần dựng máy chủ.

Không `print()` ở đây: mọi thứ đi qua `job.log()` để giao diện đọc được.
"""

from __future__ import annotations

import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from srtgen.core.context import (
    Context,
    load_config,
    make_video_id,
    new_context,
    user_data_dir,
)
from srtgen.io_utils import ensure_dir, read_json, safe_stem, write_json
from srtgen.stages import STAGE_LABELS, STAGE_MODULES

__all__ = [
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_DONE",
    "STATUS_ERROR",
    "STATUS_CANCELLED",
    "STATUS_LABELS",
    "ACTIVE_STATUSES",
    "MODE_RUN",
    "MODE_RESUME",
    "MODE_FIX",
    "MODE_CHECK",
    "MODE_LOCAL",
    "MODES",
    "FIRST_STEP",
    "LAST_STEP",
    "STEP_TOTAL",
    "STEP_WEIGHTS",
    "STEP_NOTES",
    "DEFAULT_RERUN_STEP",
    "FIRST_RERUN_STEP",
    "step_table",
    "human_duration",
    "rerun_step",
    "Job",
    "JobError",
    "JobConflict",
    "JobNotFound",
    "JobManager",
    "Subscription",
    "UPLOADS_DIR_NAME",
    "FIX_ACTIONS_EMITTED",
    "release_upload",
    "normalize_source",
]


# --------------------------------------------------------------------------- #
# từ vựng dùng chung với giao diện
# --------------------------------------------------------------------------- #

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"

#: Nhãn tiếng Việt của từng trạng thái. Giao diện lấy thẳng chuỗi này chứ không tự
#: dịch, để đổi cách gọi chỉ phải sửa một chỗ.
STATUS_LABELS: dict[str, str] = {
    STATUS_QUEUED: "Đang chờ tới lượt",
    STATUS_RUNNING: "Đang chạy",
    STATUS_DONE: "Đã xong",
    STATUS_ERROR: "Gặp lỗi",
    STATUS_CANCELLED: "Đã dừng",
}

#: Hai trạng thái mà công việc còn có thể đổi trạng thái tiếp.
ACTIVE_STATUSES = frozenset({STATUS_QUEUED, STATUS_RUNNING})

MODE_RUN = "run"        # chạy trọn bộ từ link/file
MODE_RESUME = "resume"  # chạy lại, bỏ qua các chặng đã có kết quả cũ
MODE_FIX = "fix"        # bản ghi lại một lần chuẩn hoá file .srt có sẵn
MODE_CHECK = "check"    # bản ghi lại một lần kiểm file .srt có sẵn
MODE_LOCAL = "local"    # cặp .srt/_vi.srt có sẵn, mở thẳng vào trình sửa

#: `fix`/`check`/`local` không bao giờ vào hàng đợi — chúng chạy ngay trong lượt
#: HTTP và chỉ mượn `Job` làm chỗ lưu kết quả để nút Tải file, Mở thư mục và
#: trình sửa dùng chung một đường dẫn với công việc chạy dài.
MODES = frozenset({MODE_RUN, MODE_RESUME, MODE_FIX, MODE_CHECK, MODE_LOCAL})

#: Thư mục con của `work/` nơi máy chủ ghi file người dùng kéo thả vào. Hằng số ở
#: đây (chứ không ở `server.py`) vì chính module này là nơi **xoá** các file ấy,
#: và phép kiểm "file này có đúng là bản tải lên không" dựa vào đúng cái tên này.
UPLOADS_DIR_NAME = "_uploads"

#: Mọi giá trị `fix_action` mà **module này** tự phát ra (các chặng phát thêm mã
#: riêng của chúng, xem `s0_fetch.FIX_ACTIONS`). Ghi thành bảng để giao diện có
#: một chỗ đối chiếu, thay vì đi đọc từng lời gọi `job.fail(...)`.
FIX_ACTIONS_EMITTED: dict[str, str] = {
    # Ứng dụng đóng giữa lúc chạy một link YouTube/đường dẫn trên máy: chạy lại
    # cùng nguồn là dùng lại được các chặng đã xong trong work/<id>/.
    "restart_job": "Chạy lại cùng nguồn; các bước đã xong được dùng lại.",
    # Thiếu thư viện cho một chặng: chỉ bộ cài (CaiDat.command) sửa được.
    "reinstall": "Chạy lại CaiDat.command để cài đủ thư viện còn thiếu.",
    # Ứng dụng đóng giữa lúc chạy một file TẢI LÊN: bản tải lên không còn dùng
    # được nữa, người dùng phải kéo thả lại file (trùng mã của s0_fetch).
    "check_file": "Chọn/kéo thả lại file âm thanh hoặc video.",
    # Ứng dụng đóng giữa lúc CHẠY LẠI một phim (`POST /api/jobs/{id}/rerun`):
    # chạy lại lần nữa bằng đúng endpoint đó, từ `rerun_from` của việc hỏng.
    # Không dùng `restart_job` vì nguồn của việc này thường là một bản tải lên đã
    # bị xoá — gửi lại nguồn đó là chắc chắn hỏng, còn chạy lại thì không cần nó.
    "rerun_job": "Chạy lại từ thư mục làm việc cũ; không cần file gốc.",
}

FIRST_STEP = 0
LAST_STEP = 9
STEP_TOTAL = LAST_STEP - FIRST_STEP + 1

#: Bước mặc định của nút "Chạy lại với bảng tên mới" (hợp đồng H3): S5 — tách cụm.
#: Bảng tên riêng chỉ có tác dụng từ chặng tách cụm trở đi; chạy lại từ đó là
#: nhanh nhất mà vẫn đưa được tên mới vào cả `.srt` lẫn `_vi.srt`.
DEFAULT_RERUN_STEP = 5

#: Bước sớm nhất được chạy lại mà không cần file gốc. Bước 0 là tải/đọc lại chính
#: video gốc — thứ mà một bản tải lên không còn nữa (bị xoá ngay sau bước 0, đúng
#: thiết kế) — nên chạy lại từ bước 0 thì phải chọn lại file hoặc dán lại link.
FIRST_RERUN_STEP = 1

#: Chốt chặn giữa một mã video và một đường dẫn thư mục thật, giống hệt
#: `server.VIDEO_ID_RE` (module này không được import `server`).
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

#: Hai chặng mà việc "dùng lại" còn đòi file âm thanh chúng trỏ tới phải còn trên
#: đĩa (xem `_reuse_previous` của s0_fetch và s1_audio). Thiếu file đó thì chặng
#: không dùng lại mà làm lại — tức là đi tìm video gốc đã bị xoá.
_AUDIO_STAGES = (0, 1)

#: Phần công sức của từng chặng trong tổng số 100, đo thô trên một video 40 phút:
#: gỡ băng chiếm quá nửa thời gian, mấy chặng xử lý chữ gộp lại chưa tới một phút.
#: Trọng số sai thì thanh tiến trình giật cục — nó không ảnh hưởng kết quả, nhưng
#: người dùng đọc thanh tiến trình để quyết định có nên đi pha cà phê hay không.
#:
#: Bảng này đã chia lại theo mười chặng của build-spec-v2: chặng dịch (8) gọi
#: mạng theo từng lô nên đáng kể chứ không tức thì, còn chặng gỡ băng nhẹ đi một
#: chút vì model mặc định đổi sang `large-v3-turbo`.
STEP_WEIGHTS: dict[int, float] = {
    0: 6.0,    # tải nguồn (phụ thuộc mạng)
    1: 4.0,    # chuẩn bị âm thanh
    2: 55.0,   # nghe và gỡ băng — chặng dài nhất, luôn là như vậy
    3: 2.0,
    4: 3.0,
    5: 8.0,    # jieba + pypinyin trên cả phim
    6: 2.0,
    7: 9.0,    # hỏi AI qua mạng, hoặc gần như tức thì khi tắt AI
    8: 8.0,    # dịch sang tiếng Việt theo lô
    9: 3.0,    # kiểm lần cuối và ghi file
}

#: Câu dặn thêm cho những chặng mà người dùng cần biết trước điều gì đó.
STEP_NOTES: dict[int, str] = {
    0: "Đang tải âm thanh về máy. Video dài thì bước này lâu theo tốc độ mạng.",
    2: (
        "Đây là bước lâu nhất — máy phải nghe hết cả video. "
        "Bạn có thể để máy chạy và làm việc khác, tiến trình vẫn được ghi lại."
    ),
    7: "Nếu bạn chưa nhập khoá API thì bước này được bỏ qua gần như tức thì.",
    8: (
        "Chưa nhập khoá API thì tool vẫn dịch được bằng bản miễn phí của Google, "
        "nhưng chất lượng thấp hơn rõ rệt."
    ),
}

#: Số lần nhiều nhất được quay lại chặng S5 vì AI tìm thêm tên riêng. Một lượt là
#: đủ (S7 tự chốt bằng `max_passes`); giới hạn ở đây chỉ để một lỗi cấu hình không
#: biến pipeline thành vòng lặp vô tận trên máy người dùng.
MAX_LAPS = 2

#: Số dòng nhật ký giữ lại cho mỗi công việc. Đủ để đọc lại toàn bộ một lần chạy
#: bình thường, mà không để một video hỏng làm phình file trạng thái.
MAX_LOG_LINES = 600

#: Số công việc cũ giữ trên đĩa.
MAX_HISTORY = 60

#: Ghi file trạng thái nhiều nhất mỗi chừng này giây khi công việc đang chạy.
#: Chặng ASR gọi `on_progress` mỗi giây; ghi JSON theo từng lần gọi là phí ổ đĩa.
SAVE_INTERVAL = 3.0

#: Khoảng cách tối thiểu giữa hai dòng nhật ký "cùng một ý" trong một chặng.
LOG_MIN_INTERVAL = 15.0

_STAGE_RE = re.compile(r"^[sS]?(\d)$")


def step_label(step: int) -> str:
    """Tên tiếng Việt của một chặng, không kèm số thứ tự."""
    return STAGE_LABELS.get(step, f"Bước {step}")


def step_text(step: int) -> str:
    """Dòng tiêu đề tiến trình: “Bước 3/10: Nghe và gỡ băng”.

    Đánh số từ 1 cho người đọc; mã chặng `S2` chỉ dành cho người hỗ trợ kỹ thuật.
    """
    return f"Bước {step + 1}/{STEP_TOTAL}: {step_label(step)}"


#: Chuỗi đã có scheme (`https://`, `file://`…): không bao giờ đụng vào.
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")

#: Một tên miền theo sau NGAY bởi `/`, `?` hoặc `#`: `www.youtube.com/watch?v=…`,
#: `youtu.be/…`, `m.youtube.com/shorts/…`. Dấu `/` (hoặc `?`, `#`) là bắt buộc:
#: thiếu nó thì `tap1.mp4` — một tên file — cũng "trông như tên miền".
_BARE_DOMAIN_RE = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}(?::\d{1,5})?[/?#]"
)


def normalize_source(source: str) -> str:
    """Nguồn người dùng dán vào, đã bỏ ngoặc kép thừa và thêm `https://` khi họ quên.

    Vì sao cần: giao diện coi `www.youtube.com/watch?v=…` là link (nó chỉ nhìn
    tiền tố `www.`), nhưng máy chủ và chặng tải S0 chỉ nhận link có `http(s)://`.
    Đoạn dán thiếu tiền tố vì thế rơi vào nhánh "file trên máy" và người dùng
    nhận câu "Không tìm thấy file … trên máy" — sai hẳn chỗ, và họ không có cách
    nào tự hiểu ra. Người dùng không phải dân IT không cần biết `https://` là gì;
    thêm hộ là việc của tool. Dán `youtube.com/…`, `m.youtube.com/…` hay
    `youtu.be/…` đều được, vì trình duyệt cũng hay chép ra đúng các dạng đó.

    Giữ nguyên: chuỗi đã có scheme, chuỗi không có dạng `tên.miền/…`, và mọi
    đường dẫn tới thứ **đang có thật trên máy** — một thư mục tên
    `phim.backup/tap1.mp4` vẫn là file của người dùng, không phải một trang web.
    """
    text = str(source or "").strip().strip('"').strip("'").strip()
    if not text or _SCHEME_RE.match(text) or not _BARE_DOMAIN_RE.match(text):
        return text
    try:
        if Path(text).expanduser().exists():
            return text
    except (OSError, ValueError):
        # Chuỗi không thể là đường dẫn trên hệ này (quá dài, ký tự cấm) thì càng
        # không phải file của người dùng: coi như link.
        pass
    return "https://" + text


def step_table() -> list[dict[str, Any]]:
    """Bảng 10 chặng cho giao diện dựng sẵn thanh tiến trình trước khi chạy."""
    return [
        {
            "step": n,
            "index": n + 1,
            "total": STEP_TOTAL,
            "label": step_label(n),
            "text": step_text(n),
            "note": STEP_NOTES.get(n, ""),
            "weight": STEP_WEIGHTS.get(n, 0.0),
        }
        for n in range(FIRST_STEP, LAST_STEP + 1)
    ]


def human_duration(seconds: float | None) -> str:
    """Đổi số giây thành câu tiếng Việt đọc được, làm tròn thô có chủ ý.

    “khoảng 12 phút” trung thực hơn “12 phút 34 giây”: sai số của ước lượng lớn
    hơn nhiều so với độ chính xác mà con số kia gợi ý.
    """
    if seconds is None or seconds != seconds or seconds < 0:  # None hoặc NaN
        return ""
    seconds = float(seconds)
    if seconds < 45:
        return "dưới 1 phút"
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return f"khoảng {minutes} phút"
    hours, minutes = divmod(minutes, 60)
    if minutes == 0:
        return f"khoảng {hours} giờ"
    return f"khoảng {hours} giờ {minutes} phút"


def _cumulative_weight(step: int) -> float:
    return sum(w for n, w in STEP_WEIGHTS.items() if n < step)


_TOTAL_WEIGHT = sum(STEP_WEIGHTS.values()) or 1.0


def _stage_number(value: Any) -> int | None:
    """Đổi `"s5"`, `"S5"`, `"5"`, `5` thành `5`; thứ khác thành `None`."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if FIRST_STEP <= value <= LAST_STEP else None
    match = _STAGE_RE.match(str(value).strip())
    if match is None:
        return None
    number = int(match.group(1))
    return number if FIRST_STEP <= number <= LAST_STEP else None


def _now() -> float:
    return time.time()


class JobError(RuntimeError):
    """Yêu cầu của người dùng không hợp lệ — thông điệp đã là tiếng Việt."""


class JobNotFound(JobError):
    """Không có công việc mang mã đó (máy chủ trả 404)."""


class JobConflict(JobError):
    """Một công việc khác đang dùng đúng thư mục làm việc này (máy chủ trả 409).

    Hai lượt chạy cùng ghi vào một `work/<video_id>/` là cách chắc chắn nhất để
    có file chặng trộn nửa cũ nửa mới — và một bản phụ đề không ai giải thích nổi.
    """


def rerun_step(value: Any = None) -> int:
    """Đọc `from_step` của yêu cầu chạy lại; không có thì là `DEFAULT_RERUN_STEP`.

    Nhận số nguyên hoặc chuỗi chữ số (giao diện có thể gửi cả hai), trong khoảng
    `FIRST_RERUN_STEP..LAST_STEP`. `True`/`False` bị từ chối dù Python coi chúng
    là số: một ô tick gửi nhầm vào đây không được biến thành "chạy lại từ bước 1".
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_RERUN_STEP
    number: int | None = None
    if isinstance(value, bool):
        number = None
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    if number is None or not FIRST_RERUN_STEP <= number <= LAST_STEP:
        raise JobError(
            f"Không hiểu chạy lại từ bước “{value}”. Chỉ chọn được một số từ "
            f"{FIRST_RERUN_STEP} đến {LAST_STEP} (từ “{step_text(FIRST_RERUN_STEP)}” "
            f"tới “{step_text(LAST_STEP)}”). Muốn làm lại cả bước tải video thì hãy "
            "chọn lại file hoặc dán lại link rồi bấm Bắt đầu."
        )
    return number


# --------------------------------------------------------------------------- #
# một công việc
# --------------------------------------------------------------------------- #

@dataclass
class Job:
    """Trạng thái của một lần chạy pipeline, đủ để dựng lại toàn bộ màn hình.

    Mọi thao tác đọc/ghi trạng thái đi qua `_lock`: luồng thợ ghi trong khi luồng
    HTTP đọc, và một `dict` đọc dở giữa chừng sẽ thành JSON sai.
    """

    id: str
    source: str
    mode: str = MODE_RUN
    options: dict[str, Any] = field(default_factory=dict)

    status: str = STATUS_QUEUED
    step: int = FIRST_STEP
    step_progress: float = 0.0
    progress: float = 0.0
    message: str = ""

    created_at: float = field(default_factory=_now)
    started_at: float | None = None
    finished_at: float | None = None

    video_id: str = ""
    title: str = ""
    out_dir: str = ""
    audio_duration: float = 0.0
    asr_factor: float = 0.8

    #: `source` là **bản sao tải lên** do máy chủ ghi vào `work/_uploads/`, và
    #: phải bị xoá khi chặng S0 xong. Chỉ máy chủ được bật cờ này (qua tham số của
    #: `submit`), không bao giờ lấy từ `options` của trình duyệt: nếu không, một
    #: yêu cầu bất kỳ có thể bảo luồng thợ xoá một file tuỳ ý trên máy.
    cleanup_source: bool = False

    #: `work/<video_id>/` của lần chạy này, ghi lại ngay khi dựng `Context`. Việc
    #: chạy lại (H3) dùng đúng thư mục này thay vì suy lại từ nguồn: nguồn của một
    #: bản tải lên đã bị xoá, và suy lại theo cấu hình hiện tại có thể trỏ sang
    #: một thư mục khác nếu người dùng vừa đổi Cài đặt.
    work_dir: str = ""

    #: Mã công việc mà việc này chạy lại (rỗng với việc bình thường), và bước bắt
    #: đầu làm lại. Chỉ máy chủ đặt hai trường này, qua `JobManager.rerun`.
    rerun_of: str = ""
    rerun_from: int | None = None

    result: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    log: list[dict[str, Any]] = field(default_factory=list)

    #: Tăng mỗi lần trạng thái đổi. Giao diện dùng để biết ảnh chụp mình đang giữ
    #: đã cũ chưa mà không phải so từng trường.
    version: int = 0

    _lock: threading.RLock = field(
        init=False, repr=False, compare=False, default_factory=threading.RLock
    )
    _cancel: threading.Event = field(
        init=False, repr=False, compare=False, default_factory=threading.Event
    )
    _eta: float | None = field(init=False, repr=False, compare=False, default=None)
    _last_log_at: float = field(init=False, repr=False, compare=False, default=0.0)
    _last_log_step: int = field(init=False, repr=False, compare=False, default=-1)
    _listener: Callable[["Job", str], None] | None = field(
        init=False, repr=False, compare=False, default=None
    )

    # -- truy vấn ----------------------------------------------------------- #

    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    def is_cancelled(self) -> bool:
        """Hàm truyền thẳng vào `Context.cancelled` — chặng dài gọi nó trong vòng lặp."""
        return self._cancel.is_set()

    def elapsed(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else _now()
        return max(0.0, end - self.started_at)

    # -- đổi trạng thái ------------------------------------------------------ #

    def _bump(self, kind: str = "state") -> None:
        """Tăng số phiên bản rồi báo cho `JobManager` phát sự kiện.

        Gọi trong khi đang giữ `_lock`; `JobManager` chỉ đọc bản sao đã chụp nên
        không có nguy cơ khoá chéo.
        """
        self.version += 1
        listener = self._listener
        if listener is not None:
            listener(self, kind)

    def log_line(self, text: str, level: str = "info") -> None:
        """Ghi một dòng nhật ký cho người dùng đọc (tiếng Việt, không thuật ngữ)."""
        text = str(text).strip()
        if not text:
            return
        with self._lock:
            entry = {"t": _now(), "step": self.step, "level": level, "text": text}
            self.log.append(entry)
            if len(self.log) > MAX_LOG_LINES:
                del self.log[: len(self.log) - MAX_LOG_LINES]
            self._last_log_at = entry["t"]
            self._last_log_step = self.step
            self._bump("log")

    def log_since(self, index: int) -> list[dict[str, Any]]:
        """Các dòng nhật ký từ vị trí `index` trở đi, cho tab vừa kết nối lại."""
        with self._lock:
            if index < 0:
                return list(self.log)
            return list(self.log[index:])

    def mark_running(self) -> None:
        with self._lock:
            self.status = STATUS_RUNNING
            self.started_at = _now()
            self.message = "Đang chuẩn bị…"
            self._bump()

    def begin_step(self, step: int) -> None:
        with self._lock:
            self.step = step
            self.step_progress = 0.0
            self.message = step_text(step)
            self._recompute_progress()
            self._bump()
        note = STEP_NOTES.get(step, "")
        self.log_line(step_text(step) + (f" — {note}" if note else ""))

    def update(self, step: int, message: str, fraction: float) -> None:
        """Callback mà mọi chặng gọi: `on_progress(message, fraction)`."""
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            fraction = 0.0
        fraction = min(1.0, max(0.0, fraction))
        text = str(message or "").strip()
        with self._lock:
            self.step = step
            # Tiến trình trong một chặng không được lùi: vài chặng báo theo nhiều
            # nguồn (ví dụ ffmpeg rồi tới whisper) nên con số có thể nhảy giật lùi.
            self.step_progress = max(self.step_progress, fraction)
            if text:
                self.message = text
            self._recompute_progress()
            self._bump()
        if text:
            self._maybe_log(text)

    def _maybe_log(self, text: str) -> None:
        """Chỉ ghi vào nhật ký khi có tin mới, không ghi từng nhịp tiến trình.

        Chặng ASR gọi `on_progress` mỗi giây với câu chỉ khác nhau ở số phút còn
        lại. Ghi hết thì nhật ký dài hàng nghìn dòng và không ai đọc nổi.
        """
        with self._lock:
            last = self.log[-1]["text"] if self.log else ""
            if text == last:
                return
            fresh_step = self._last_log_step != self.step
            recent = (_now() - self._last_log_at) < LOG_MIN_INTERVAL
            if not fresh_step and recent:
                return
        self.log_line(text)

    def _recompute_progress(self) -> None:
        done = _cumulative_weight(self.step)
        current = STEP_WEIGHTS.get(self.step, 0.0) * self.step_progress
        value = min(1.0, (done + current) / _TOTAL_WEIGHT)
        # Không cho lùi: khi AI tìm được tên riêng, pipeline quay lại chặng S5 và
        # con số tính ra sẽ nhỏ đi. Thanh tiến trình chạy ngược trông như hỏng.
        self.progress = max(self.progress, value)
        self._update_eta()

    def _update_eta(self) -> None:
        """Ước lượng thời gian còn lại, trộn giữa mô hình và số đo thật.

        Trước khi có số đo (mấy phút đầu) thì dựa vào thời lượng audio: chặng ASR
        chạy khoảng `eta_factor` lần thời lượng, mà nó chiếm phần lớn tổng công
        sức. Càng chạy lâu càng tin số đo thật hơn, vì máy của mỗi người mỗi khác.
        """
        if self.started_at is None or self.progress <= 0.0:
            self._eta = self._model_total()
            return
        model_total = self._model_total()
        elapsed = self.elapsed()
        measured: float | None = None
        if self.progress >= 0.02 and elapsed > 5.0:
            measured = elapsed * (1.0 - self.progress) / self.progress

        if measured is None:
            value = model_total * (1.0 - self.progress) if model_total else None
        elif model_total is None:
            value = measured
        else:
            trust = min(1.0, self.progress / 0.25)
            value = trust * measured + (1.0 - trust) * model_total * (1.0 - self.progress)

        if value is None:
            self._eta = None
            return
        # Làm mượt để con số không nhảy loạn giữa hai lần cập nhật liền nhau.
        self._eta = value if self._eta is None else 0.7 * self._eta + 0.3 * value

    def _model_total(self) -> float | None:
        if self.audio_duration <= 0:
            return None
        asr_share = STEP_WEIGHTS.get(2, 1.0) / _TOTAL_WEIGHT
        return (self.audio_duration * max(0.1, self.asr_factor)) / max(0.01, asr_share)

    def eta_seconds(self) -> float | None:
        with self._lock:
            if self.status != STATUS_RUNNING or self._eta is None:
                return None
            return max(0.0, self._eta)

    def estimated_total(self) -> float | None:
        """Tổng thời gian dự kiến theo thời lượng audio, chưa trừ phần đã chạy."""
        with self._lock:
            return self._model_total()

    # -- những gì luồng thợ nhặt được dọc đường ------------------------------ #

    def set_run_info(
        self,
        *,
        video_id: str,
        out_dir: str,
        asr_factor: float,
        work_dir: str = "",
    ) -> None:
        with self._lock:
            self.video_id = video_id
            self.out_dir = out_dir
            self.asr_factor = asr_factor
            if work_dir:
                self.work_dir = work_dir
            self._bump()

    def set_source_info(self, *, title: str = "", duration: float = 0.0) -> None:
        with self._lock:
            if title:
                self.title = title
            if duration > 0:
                self.audio_duration = duration
            self._bump()

    def set_result(
        self,
        result: dict[str, Any],
        findings: Sequence[dict[str, Any]],
        summary: dict[str, Any],
        *,
        out_dir: str = "",
    ) -> None:
        with self._lock:
            self.result = dict(result)
            self.findings = [dict(f) for f in findings]
            self.summary = dict(summary)
            if out_dir:
                self.out_dir = out_dir
            self._bump()

    def finish(
        self,
        *,
        result: dict[str, Any] | None = None,
        findings: Sequence[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
        message: str = "",
    ) -> None:
        with self._lock:
            self.status = STATUS_DONE
            self.step = LAST_STEP
            self.step_progress = 1.0
            self.progress = 1.0
            self.finished_at = _now()
            self._eta = 0.0
            if result is not None:
                self.result = dict(result)
            if findings is not None:
                self.findings = [dict(f) for f in findings]
            if summary is not None:
                self.summary = dict(summary)
            self.message = message or "Đã xong."
            self._bump("done")
        self.log_line(self.message, level="success")

    def fail(self, message: str, *, fix_action: str = "", detail: str = "") -> None:
        with self._lock:
            self.status = STATUS_ERROR
            self.finished_at = _now()
            self._eta = None
            self.error = {
                "message": message,
                "fix_action": fix_action,
                "detail": detail,
            }
            self.message = message
            self._bump("error")
        self.log_line(message, level="error")

    def cancel(self) -> None:
        """Bật cờ dừng. Chặng đang chạy tự thoát ở lần kiểm tra gần nhất."""
        self._cancel.set()
        with self._lock:
            if self.status == STATUS_QUEUED:
                self.status = STATUS_CANCELLED
                self.finished_at = _now()
                self.message = "Đã dừng trước khi bắt đầu."
                self._bump("cancelled")
                stopped = True
            else:
                self.message = "Đang dừng…"
                self._bump()
                stopped = False
        if stopped:
            self.log_line("Bạn đã dừng công việc này.", level="warn")

    def mark_cancelled(self, message: str = "Đã dừng theo yêu cầu của bạn.") -> None:
        with self._lock:
            self.status = STATUS_CANCELLED
            self.finished_at = _now()
            self._eta = None
            self.message = message
            self._bump("cancelled")
        self.log_line(message, level="warn")

    # -- xuất ra ngoài ------------------------------------------------------- #

    def public_dict(self, *, with_log: bool = False) -> dict[str, Any]:
        """Bản chụp cho giao diện. Không kèm nhật ký trừ khi được hỏi.

        Danh sách việc có thể có 60 mục; kéo theo nhật ký của từng mục thì mỗi lần
        làm mới trang là vài megabyte không ai đọc.
        """
        with self._lock:
            eta = self.eta_seconds()
            data: dict[str, Any] = {
                "id": self.id,
                "source": self.source,
                "mode": self.mode,
                "options": dict(self.options),
                "status": self.status,
                "status_label": STATUS_LABELS.get(self.status, self.status),
                "step": self.step,
                "step_index": self.step + 1,
                "step_total": STEP_TOTAL,
                "step_label": step_label(self.step),
                "step_text": step_text(self.step),
                "step_note": STEP_NOTES.get(self.step, ""),
                "step_progress": round(self.step_progress, 4),
                "progress": round(self.progress, 4),
                "message": self.message,
                "eta": None if eta is None else round(eta, 1),
                "eta_text": human_duration(eta),
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "elapsed": round(self.elapsed(), 1),
                "video_id": self.video_id,
                "title": self.title,
                "out_dir": self.out_dir,
                "audio_duration": self.audio_duration,
                "result": dict(self.result) if self.result else None,
                "findings": [dict(f) for f in self.findings],
                "summary": dict(self.summary),
                "error": dict(self.error) if self.error else None,
                "version": self.version,
                "log_count": len(self.log),
                "active": self.is_active(),
                "cancelling": self._cancel.is_set() and self.is_active(),
                "downloads": self.downloads(),
                "can_edit": self.can_edit(),
                "uploaded": self.cleanup_source,
                "work_dir": self.work_dir,
                "rerun_of": self.rerun_of,
                "rerun_from": self.rerun_from,
                "can_rerun": self.can_rerun(),
                # Bản cũ mà lần xuất file này đã cất đi trước khi ghi đè (H1). Nhắc
                # lại ở ngoài `result` để giao diện báo cho người dùng mà không phải
                # biết `result` còn chứa những gì.
                "backups": self.backups(),
            }
            if with_log:
                data["log"] = list(self.log)
        return data

    def downloads(self) -> dict[str, str]:
        """Đường dẫn tải cho từng loại file đã thật sự được ghi ra đĩa.

        Thứ tự trong bộ đôi này là thứ tự người dùng cần: hai file giao đi
        (`srt`, `vi_srt`) trước, rồi mới tới file để soát và file cho máy đọc.
        """
        result = self.result or {}
        out: dict[str, str] = {}
        for kind in ("srt", "vi_srt", "bundle", "report", "ass", "bilingual_ass"):
            if result.get(kind):
                out[kind] = f"/api/download/{self.id}/{kind}"
        return out

    def can_edit(self) -> bool:
        """Có mở được công việc này trong trình sửa phụ đề không.

        Điều kiện duy nhất là có một file `.srt` đã ghi ra đĩa: trình sửa đọc
        thẳng từ file chứ không đọc từ bộ nhớ, nên một công việc đã xong từ
        tuần trước vẫn mở lại được sau khi khởi động lại ứng dụng.
        """
        return bool((self.result or {}).get("srt"))

    def backups(self) -> list[str]:
        """Đường dẫn các bản cũ chặng xuất file đã cất đi ở lần chạy này (H1)."""
        raw = (self.result or {}).get("backups")
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(item) for item in raw if str(item or "").strip()]

    def can_rerun(self) -> bool:
        """Có đáng hiện nút "Chạy lại" (H3) cho công việc này không.

        Chỉ nhìn trạng thái trong bộ nhớ, không đụng đĩa: hàm này chạy ở mỗi nhịp
        tiến trình. Kiểm thật (thư mục còn không, các chặng trước còn không) là
        việc của `JobManager.rerun`, và nó nói lý do bằng tiếng Việt khi không được.
        """
        return (
            self.mode in (MODE_RUN, MODE_RESUME)
            and not self.is_active()
            and bool(_VIDEO_ID_RE.match(str(self.video_id or "")))
        )

    def to_state_dict(self) -> dict[str, Any]:
        """Dạng ghi ra đĩa — có nhật ký, không có khoá/luồng."""
        with self._lock:
            return {
                "id": self.id,
                "source": self.source,
                "mode": self.mode,
                "options": dict(self.options),
                "status": self.status,
                "step": self.step,
                "step_progress": self.step_progress,
                "progress": self.progress,
                "message": self.message,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "video_id": self.video_id,
                "title": self.title,
                "out_dir": self.out_dir,
                "audio_duration": self.audio_duration,
                "asr_factor": self.asr_factor,
                "cleanup_source": self.cleanup_source,
                "work_dir": self.work_dir,
                "rerun_of": self.rerun_of,
                "rerun_from": self.rerun_from,
                "result": dict(self.result) if self.result else None,
                "findings": [dict(f) for f in self.findings],
                "summary": dict(self.summary),
                "error": dict(self.error) if self.error else None,
                "log": list(self.log),
                "version": self.version,
            }

    @classmethod
    def from_state_dict(cls, data: dict[str, Any]) -> "Job":
        """Dựng lại từ file trên đĩa, chấp nhận file cũ thiếu khoá.

        Đọc lỏng có chủ ý: một phiên bản sau thêm trường mới không được làm hỏng
        danh sách việc cũ của người dùng.
        """
        job = cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            source=str(data.get("source") or ""),
            mode=str(data.get("mode") or MODE_RUN),
            options=dict(data.get("options") or {}),
        )
        job.status = str(data.get("status") or STATUS_DONE)
        job.step = int(data.get("step") or 0)
        job.step_progress = float(data.get("step_progress") or 0.0)
        job.progress = float(data.get("progress") or 0.0)
        job.message = str(data.get("message") or "")
        job.created_at = float(data.get("created_at") or _now())
        job.started_at = _opt_float(data.get("started_at"))
        job.finished_at = _opt_float(data.get("finished_at"))
        job.video_id = str(data.get("video_id") or "")
        job.title = str(data.get("title") or "")
        job.out_dir = str(data.get("out_dir") or "")
        job.audio_duration = float(data.get("audio_duration") or 0.0)
        job.asr_factor = float(data.get("asr_factor") or 0.8)
        job.cleanup_source = bool(data.get("cleanup_source"))
        job.work_dir = str(data.get("work_dir") or "")
        job.rerun_of = str(data.get("rerun_of") or "")
        rerun_from = data.get("rerun_from")
        job.rerun_from = (
            int(rerun_from)
            if isinstance(rerun_from, int) and not isinstance(rerun_from, bool)
            else None
        )
        result = data.get("result")
        job.result = dict(result) if isinstance(result, dict) else None
        job.findings = [dict(f) for f in (data.get("findings") or []) if isinstance(f, dict)]
        job.summary = dict(data.get("summary") or {})
        error = data.get("error")
        job.error = dict(error) if isinstance(error, dict) else None
        job.log = [dict(x) for x in (data.get("log") or []) if isinstance(x, dict)]
        job.version = int(data.get("version") or 0)
        return job


def _opt_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# đăng ký nhận sự kiện
# --------------------------------------------------------------------------- #

class Subscription:
    """Một người nghe sự kiện của một công việc (hoặc của cả danh sách).

    Dùng `queue.SimpleQueue` chứ không phải danh sách + điều kiện: `SimpleQueue`
    an toàn giữa các luồng, không cần khoá, và `get(timeout=…)` cho phép phía SSE
    vừa chờ vừa kiểm tra xem trình duyệt còn kết nối hay không.
    """

    #: Số sự kiện tồn đọng tối đa trước khi bỏ bớt. Một tab bị treo không được phép
    #: làm phình bộ nhớ của tiến trình máy chủ.
    MAX_PENDING = 2000

    def __init__(self, manager: "JobManager", job_id: str | None) -> None:
        self.job_id = job_id
        self.queue: "queue.SimpleQueue[dict[str, Any]]" = queue.SimpleQueue()
        self._manager = manager
        self._closed = False

    def put(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        if self.queue.qsize() > self.MAX_PENDING:
            return
        self.queue.put(event)

    def get_nowait(self) -> dict[str, Any] | None:
        try:
            return self.queue.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        self._closed = True
        self._manager._unsubscribe(self)

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# hàng đợi
# --------------------------------------------------------------------------- #

def _default_config(job: Job) -> dict[str, Any]:
    """Cấu hình mặc định cho một công việc: `default.yaml` + lựa chọn của người dùng.

    `server.py` thay hàm này bằng bản có gộp thêm phần Cài đặt (khoá API, thư mục
    ra). Tách ra được vì `jobs.py` không được biết Cài đặt nằm ở đâu.
    """
    options = job.options or {}
    overrides: dict[str, Any] = {}

    from_stage = _stage_number(options.get("from_stage"))
    if from_stage is not None:
        overrides["force_from"] = from_stage

    model = str(options.get("model") or "").strip()
    if model:
        overrides["asr"] = {"model": model}

    if options.get("no_ai"):
        overrides["ai"] = {"enabled": False}
    elif options.get("ai") is True:
        overrides["ai"] = {"enabled": True}

    out_dir = str(options.get("out_dir") or "").strip()
    if out_dir:
        overrides["paths"] = {"out_dir": out_dir}

    if options.get("ass_export") is not None:
        overrides["emit"] = {"ass_export": bool(options.get("ass_export"))}

    profile = str(options.get("profile") or "").strip() or None
    return load_config(profile, overrides)


class JobManager:
    """Hàng đợi một luồng: nhận việc, chạy lần lượt, phát sự kiện, ghi trạng thái.

    Một `JobManager` cho cả tiến trình. `server.py` giữ nó trong `app.state` để
    mọi tab trình duyệt nhìn thấy cùng một danh sách việc.
    """

    def __init__(
        self,
        *,
        state_dir: Path | str | None = None,
        config_factory: Callable[[Job], dict[str, Any]] | None = None,
        max_history: int = MAX_HISTORY,
        autoload: bool = True,
    ) -> None:
        self.state_dir = Path(state_dir) if state_dir else (user_data_dir() / "jobs")
        self.config_factory = config_factory or _default_config
        self.max_history = max(1, int(max_history))

        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._queue: "queue.Queue[str | None]" = queue.Queue()
        self._subs: list[Subscription] = []
        self._subs_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        self._current: Job | None = None
        self._saved_at: dict[str, float] = {}

        if autoload:
            self._load_from_disk()

    # -- vòng đời ----------------------------------------------------------- #

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._work_loop, name="srtgen-jobs", daemon=True
            )
            self._worker.start()

    def shutdown(self, *, wait: float = 5.0, cancel_running: bool = True) -> None:
        """Dừng sạch: báo việc đang chạy dừng lại, ghi trạng thái, tắt luồng thợ."""
        self._stopping.set()
        if cancel_running:
            with self._lock:
                current = self._current
            if current is not None and current.is_active():
                current.cancel()
        self._queue.put(None)
        worker = self._worker
        if worker is not None and worker.is_alive() and wait > 0:
            worker.join(timeout=wait)
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            self._save(job, force=True)

    # -- nhận việc ---------------------------------------------------------- #

    def submit(
        self,
        source: str,
        *,
        mode: str = MODE_RUN,
        options: dict[str, Any] | None = None,
        title: str = "",
        cleanup_source: bool = False,
    ) -> Job:
        """Thêm một việc vào hàng đợi và trả về ngay, không chờ chạy xong.

        `title` và `cleanup_source` chỉ dành cho file người dùng **tải lên**: trên
        đĩa file đó mang tên ngẫu nhiên (`<uuid>.mp4`), nên tên gốc phải đi riêng
        để thành tên file kết quả, và cờ dọn dẹp báo cho luồng thợ rằng bản sao ấy
        thuộc về tool, xoá được ngay sau chặng S0.
        """
        source = normalize_source(source)
        if not source:
            raise JobError("Bạn chưa dán link YouTube hoặc chọn file âm thanh.")
        mode = str(mode or MODE_RUN).strip() or MODE_RUN
        if mode not in (MODE_RUN, MODE_RESUME):
            raise JobError(
                f"Không hiểu kiểu chạy “{mode}”. Hãy chọn “run” (chạy mới) "
                "hoặc “resume” (chạy tiếp)."
            )
        if self._stopping.is_set():
            raise JobError("Ứng dụng đang tắt nên không nhận thêm việc mới.")

        job = Job(
            id=uuid.uuid4().hex[:12],
            source=source,
            mode=mode,
            options=dict(options or {}),
        )
        job.title = str(title or "").strip()
        job.cleanup_source = bool(cleanup_source)
        job._listener = self._on_job_changed
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
        if job.cleanup_source and job.title:
            # Đường dẫn trên đĩa là một mã ngẫu nhiên, vô nghĩa với người dùng.
            first_line = f"Đã nhận file: {job.title}"
        else:
            first_line = f"Đã nhận yêu cầu: {source}"
        self._enqueue(job, [first_line])
        return job

    def rerun(
        self,
        job_id: str,
        *,
        from_step: Any = None,
        work_dir: str | Path | None = None,
    ) -> Job:
        """Tạo việc MỚI chạy lại một phim từ `from_step`, dùng lại thư mục làm việc cũ (H3).

        Vì sao cần: bảng tên riêng chỉ nhập được SAU lần chạy đầu, còn file người
        dùng tải lên đã bị xoá ngay sau bước 0 (đúng thiết kế — video vài GB không
        được nằm chiếm ổ). Tải lại file thì ra một mã video mới, tức một thư mục
        làm việc mới — nên bảng tên vừa lưu không bao giờ được dùng. Việc chạy
        lại vì thế KHÔNG cần nguồn: file wav và kết quả gỡ băng vẫn nằm trong
        `work/<video_id>/`, các chặng trước `from_step` được dùng lại từ đó.

        `work_dir` là thư mục dự phòng cho công việc ghi từ bản cũ, khi `Job` chưa
        có trường `work_dir` (máy chủ tính theo cấu hình hiện tại).

        Toàn bộ phép kiểm và việc đăng ký việc mới nằm trong cùng một khoá: hai cú
        bấm liền nhau không được lọt qua cả hai rồi cùng ghi vào một thư mục.

        Ném `JobNotFound`, `JobConflict` (đang có việc khác dùng thư mục này),
        hoặc `JobError` với câu tiếng Việt nói rõ vì sao chưa chạy lại được.
        """
        step = rerun_step(from_step)
        with self._lock:
            old = self._jobs.get(str(job_id))
            if old is None:
                raise JobNotFound(
                    "Không tìm thấy công việc này. Có thể nó đã bị xoá khỏi lịch sử."
                )
            if self._stopping.is_set():
                raise JobError("Ứng dụng đang tắt nên không nhận thêm việc mới.")
            if old.mode not in (MODE_RUN, MODE_RESUME):
                raise JobError(
                    "Công việc này không phải một lần tạo phụ đề từ video, nên không có "
                    "gì để chạy lại. Nút này dành cho phim đã chạy qua các bước tạo phụ đề."
                )
            video_id = str(old.video_id or "").strip()
            if not _VIDEO_ID_RE.match(video_id):
                raise JobError(
                    "Công việc này dừng trước khi kịp tạo thư mục làm việc, nên chưa có "
                    "gì để dùng lại. Hãy chọn lại file (hoặc dán lại link) rồi bấm Bắt đầu."
                )
            busy = [j for j in self._jobs.values() if j.is_active() and _job_video_id(j) == video_id]
            if busy:
                raise JobConflict(
                    "Phim này đang được xử lý trong một công việc khác. Hãy đợi việc đó "
                    "xong (hoặc bấm Dừng) rồi mới chạy lại, để hai lần chạy không ghi "
                    "chồng lên nhau."
                )
            folder = Path(old.work_dir) if old.work_dir else (
                Path(work_dir) if work_dir else None
            )
            if folder is None or not folder.is_dir():
                raise JobError(
                    "Không còn thấy thư mục làm việc của phim này"
                    + (f" ({folder})" if folder is not None else "")
                    + ", nên không có gì để dùng lại. Hãy chọn lại file (hoặc dán lại "
                    "link) rồi bấm Bắt đầu."
                )
            _check_rerun_inputs(folder, step)

            options = dict(old.options or {})
            options["from_stage"] = step
            job = Job(
                id=uuid.uuid4().hex[:12],
                source=old.source,
                mode=MODE_RESUME,
                options=options,
            )
            job.title = old.title
            job.video_id = video_id
            job.work_dir = str(folder)
            job.out_dir = old.out_dir
            job.audio_duration = old.audio_duration
            job.asr_factor = old.asr_factor
            job.rerun_of = old.id
            job.rerun_from = step
            # Không bao giờ bật `cleanup_source` ở đây: nguồn là của việc cũ, và
            # việc chạy lại không có quyền (cũng không cần) xoá bất cứ thứ gì.
            job.cleanup_source = False
            job._listener = self._on_job_changed
            self._jobs[job.id] = job
            self._order.append(job.id)
        name = job.title or video_id
        self._enqueue(
            job,
            [
                f"Chạy lại “{name}” từ {step_text(step)}.",
                "Dùng lại kết quả đã có trong thư mục làm việc của phim (file âm thanh, "
                "kết quả gỡ băng…), không cần file gốc. Các bước trước đó xong gần như "
                "tức thì.",
            ],
        )
        return job

    def _enqueue(self, job: Job, first_lines: Sequence[str]) -> None:
        """Phần chung của `submit` và `rerun` sau khi việc đã được đăng ký."""
        with self._lock:
            waiting = sum(1 for j in self._jobs.values() if j.status == STATUS_QUEUED)
        for line in first_lines:
            job.log_line(line)
        if waiting > 1:
            job.message = f"Đang chờ tới lượt ({waiting - 1} việc đang xếp trước)."
        self._save(job, force=True)
        self._trim_history()
        self._queue.put(job.id)
        self._ensure_worker()
        self._publish({"type": "created", "job": job.public_dict()})

    def add_finished(
        self,
        *,
        source: str,
        mode: str,
        message: str,
        result: dict[str, Any] | None = None,
        findings: Sequence[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
        out_dir: str = "",
        video_id: str = "",
        log: Iterable[str] = (),
    ) -> Job:
        """Ghi lại một việc đã xong ngay lập tức (Kiểm tra / Chuẩn hoá / Mở file có sẵn).

        Ba lệnh đó chạy trong vài giây nên không cần hàng đợi, nhưng vẫn cần một
        `Job` để nút “Tải file” và “Mở thư mục” dùng chung một đường dẫn với việc
        chạy dài — và để người dùng thấy lại việc mình vừa làm sau khi tải lại trang.

        `video_id` là chỗ trình sửa đặt bản nháp tự lưu và bản gốc để hoàn nguyên
        (`work/<video_id>/`). Việc chạy từ link YouTube có sẵn mã này; việc mở
        một file trên máy thì `server.py` suy ra từ đường dẫn file, để cùng một
        file mở lại lần sau vẫn tìm về đúng thư mục cũ.
        """
        job = Job(
            id=uuid.uuid4().hex[:12],
            source=str(source or ""),
            mode=str(mode or MODE_FIX),
        )
        job._listener = self._on_job_changed
        job.out_dir = str(out_dir or "")
        job.video_id = str(video_id or "")
        job.started_at = job.created_at
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
        for line in log:
            job.log_line(line)
        job.finish(result=result, findings=findings, summary=summary, message=message)
        self._save(job, force=True)
        self._trim_history()
        self._publish({"type": "created", "job": job.public_dict()})
        return job

    # -- tra cứu ------------------------------------------------------------ #

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(str(job_id))

    def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Danh sách việc, mới nhất trước."""
        with self._lock:
            jobs = [self._jobs[i] for i in self._order if i in self._jobs]
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        return [job.public_dict() for job in jobs[: max(1, limit)]]

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise JobError("Không tìm thấy công việc này. Có thể nó đã bị xoá khỏi lịch sử.")
        if not job.is_active():
            raise JobError(f"Công việc này đã kết thúc ({STATUS_LABELS.get(job.status, job.status)}).")
        job.cancel()
        self._save(job, force=True)
        return job

    # -- sự kiện ------------------------------------------------------------ #

    def subscribe(self, job_id: str | None = None) -> Subscription:
        """Đăng ký nhận sự kiện; `job_id=None` để nghe mọi việc (dùng cho trang danh sách)."""
        sub = Subscription(self, job_id)
        with self._subs_lock:
            self._subs.append(sub)
        return sub

    def _unsubscribe(self, sub: Subscription) -> None:
        with self._subs_lock:
            if sub in self._subs:
                self._subs.remove(sub)

    def _publish(self, event: dict[str, Any]) -> None:
        job_id = (event.get("job") or {}).get("id") if isinstance(event.get("job"), dict) else None
        job_id = event.get("job_id", job_id)
        with self._subs_lock:
            targets = [s for s in self._subs if s.job_id is None or s.job_id == job_id]
        for sub in targets:
            sub.put(event)

    def _on_job_changed(self, job: Job, kind: str) -> None:
        """Được `Job._bump()` gọi mỗi lần trạng thái đổi (đang giữ khoá của job)."""
        event: dict[str, Any] = {"type": kind, "job_id": job.id}
        if kind == "log":
            event["index"] = len(job.log) - 1
            event["line"] = dict(job.log[-1]) if job.log else {}
            event["job"] = job.public_dict()
        else:
            event["job"] = job.public_dict()
        self._publish(event)
        self._save(job, force=kind != "state")

    # -- ghi đĩa ------------------------------------------------------------ #

    def _state_path(self, job_id: str) -> Path:
        return self.state_dir / f"{job_id}.json"

    def _save(self, job: Job, *, force: bool = False) -> None:
        """Ghi trạng thái ra đĩa, hạn chế nhịp ghi khi đang chạy.

        Nuốt lỗi ghi đĩa là cố ý: ổ đầy hay thư mục bị khoá không được phép giết
        một lần chạy ASR 30 phút — mất lịch sử còn hơn mất kết quả.
        """
        now = time.monotonic()
        if not force and (now - self._saved_at.get(job.id, 0.0)) < SAVE_INTERVAL:
            return
        self._saved_at[job.id] = now
        try:
            ensure_dir(self.state_dir)
            write_json(self._state_path(job.id), job.to_state_dict())
        except (OSError, TypeError, ValueError):
            pass

    def _load_from_disk(self) -> None:
        try:
            paths = sorted(self.state_dir.glob("*.json"))
        except OSError:
            return
        loaded: list[Job] = []
        for path in paths:
            try:
                data = read_json(path)
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            try:
                job = Job.from_state_dict(data)
            except (TypeError, ValueError):
                continue
            job._listener = self._on_job_changed
            if job.is_active():
                # Luồng chạy nó đã chết cùng tiến trình trước. Nói thẳng ra thay vì
                # để một thanh tiến trình đứng im mãi mãi.
                job.status = STATUS_ERROR
                job.finished_at = job.finished_at or _now()
                if job.cleanup_source:
                    # Chạy lại từ một bản tải lên là không làm được: bản đó hoặc
                    # đã bị xoá sau S0, hoặc thuộc về một lần tải dở. Hứa "Chạy
                    # lại để tiếp tục" ở đây là hứa một nút không làm gì cả.
                    # File không bị xoá ngay tại đây (có thể một tiến trình khác
                    # đang dùng chung thư mục trạng thái); máy chủ dọn bản mồ côi
                    # quá một ngày tuổi lúc khởi động.
                    job.error = {
                        "message": (
                            "Ứng dụng đã đóng khi đang xử lý file bạn tải lên, nên công "
                            "việc này dừng lại. Hãy kéo thả lại file rồi bấm Bắt đầu."
                        ),
                        "fix_action": "check_file",
                        "detail": "",
                    }
                elif job.rerun_of:
                    # Việc CHẠY LẠI không cần nguồn gốc, nên hứa "Chạy lại" ở đây là
                    # hứa thật — nhưng phải qua đúng đường chạy lại (`rerun_job`),
                    # không phải gửi lại nguồn (thường là bản tải lên đã bị xoá).
                    job.error = {
                        "message": (
                            "Ứng dụng đã đóng khi đang chạy lại phim này. Bấm “Chạy lại” "
                            "để tiếp tục — tool dùng lại thư mục làm việc cũ, không cần "
                            "file gốc."
                        ),
                        "fix_action": "rerun_job",
                        "detail": "",
                    }
                else:
                    job.error = {
                        "message": (
                            "Ứng dụng đã đóng khi công việc còn đang chạy. "
                            "Bấm “Chạy lại” để tiếp tục — các bước đã xong vẫn được dùng lại."
                        ),
                        "fix_action": "restart_job",
                        "detail": "",
                    }
                job.message = job.error["message"]
            elif job.cleanup_source:
                # Việc đã kết thúc hẳn từ phiên trước mà bản tải lên vẫn còn (lần
                # xoá trước hỏng vì file đang bị khoá chẳng hạn): dọn nốt.
                release_upload(job)
            loaded.append(job)

        loaded.sort(key=lambda j: j.created_at)
        with self._lock:
            for job in loaded:
                self._jobs[job.id] = job
                self._order.append(job.id)
        self._trim_history()

    def _trim_history(self) -> None:
        """Giữ lại `max_history` việc mới nhất; xoá file của những việc quá cũ."""
        with self._lock:
            keep = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            drop = [j for j in keep[self.max_history:] if not j.is_active()]
            for job in drop:
                self._jobs.pop(job.id, None)
                if job.id in self._order:
                    self._order.remove(job.id)
        for job in drop:
            try:
                self._state_path(job.id).unlink(missing_ok=True)
            except OSError:
                pass

    # -- luồng thợ ---------------------------------------------------------- #

    def _work_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                job_id = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if job_id is None:
                break
            job = self.get(job_id)
            if job is None or job.status != STATUS_QUEUED:
                # Bị dừng khi còn đang chờ: không bao giờ tới S0, nên đây là lần
                # cuối cùng còn ai nhớ tới bản tải lên của nó.
                if job is not None:
                    release_upload(job)
                continue
            with self._lock:
                self._current = job
            try:
                self._execute(job)
            except BaseException as err:  # không để một lỗi lạ giết luồng thợ
                self._report_failure(job, err)
            finally:
                with self._lock:
                    self._current = None
                self._save(job, force=True)

    def _execute(self, job: Job) -> None:
        """Chạy trọn mười chặng; bản tải lên (nếu có) luôn được dọn khi kết thúc.

        `finally` ở đây là lưới đỡ cho mọi đường ra sớm — cấu hình hỏng, không
        tạo được thư mục, S0 báo lỗi, người dùng bấm Dừng. Đường thành công thì
        đã xoá ngay sau S0 (xem `_run_stages`), không đợi tới cuối một lần chạy
        40 phút trong khi video vài GB vẫn nằm chiếm ổ.
        """
        try:
            self._run_stages(job)
        finally:
            release_upload(job)

    def _run_stages(self, job: Job) -> None:
        """Chạy trọn mười chặng cho một công việc."""
        job.mark_running()
        if job.is_cancelled():
            job.mark_cancelled()
            return

        try:
            cfg = self.config_factory(job)
        except Exception as err:
            job.fail(
                "Không đọc được cấu hình của tool. Hãy thử mở lại ứng dụng.",
                detail=f"{type(err).__name__}: {err}",
            )
            return

        try:
            if job.rerun_of:
                ctx = _rerun_context(job, cfg)
            else:
                ctx = new_context(job.source, cfg, out_dir=job.options.get("out_dir") or None)
        except Exception as err:
            job.fail(_user_message(err), detail=_detail(err))
            return

        ctx.cancelled = job.is_cancelled
        job.set_run_info(
            video_id=ctx.video_id,
            out_dir=str(ctx.out_dir),
            asr_factor=_asr_factor(cfg),
            work_dir=str(ctx.work_dir),
        )
        job.log_line(f"Thư mục kết quả: {ctx.out_dir}")

        laps = 0
        step = FIRST_STEP
        while step <= LAST_STEP:
            if job.is_cancelled():
                job.mark_cancelled()
                return
            job.begin_step(step)
            try:
                module = import_module(STAGE_MODULES[step])
            except ImportError as err:
                job.fail(
                    f"Thiếu thư viện cho bước “{step_label(step)}”. "
                    "Hãy chạy lại bộ cài đặt để cài đủ phần còn thiếu.",
                    fix_action="reinstall",
                    detail=_detail(err),
                )
                return

            try:
                output = module.run(ctx, _progress_for(job, step))
            except BaseException as err:
                if job.is_cancelled() or _is_cancellation(err):
                    job.mark_cancelled()
                    return
                self._report_failure(job, err)
                return

            if step == FIRST_STEP:
                output = _apply_upload_title(job, ctx, output)
                # Bản tải lên là VIDEO thì không vứt: dời vào work/<video_id>/
                # để trình sửa phát video có phụ đề đè lên. Chỉ tiếng thì wav
                # của S0 đã đủ, bản tải lên hết việc.
                output = _retain_upload(job, ctx, output)
                release_upload(job)
            self._absorb(job, ctx, step, output)

            if (
                step == 7
                and isinstance(output, dict)
                and output.get("rerun_s5")
                and laps < MAX_LAPS
            ):
                laps += 1
                _forget_stages(ctx, (5, "tokens"), (6, "norm"), (7, "ai"))
                job.log_line(
                    "Trợ lý AI tìm được tên riêng mới — tách lại cụm từ cho cả phim "
                    "để tên viết đúng ở mọi câu."
                )
                step = 5
                continue
            step += 1

        job.finish(
            result=job.result,
            findings=job.findings,
            summary=job.summary,
            message=_done_message(job),
        )

    def _absorb(self, job: Job, ctx: Context, step: int, output: Any) -> None:
        """Nhặt ra khỏi kết quả từng chặng đúng những gì giao diện cần hiển thị."""
        if step == 0 and isinstance(output, dict):
            title = str(output.get("title") or "").strip()
            duration = _as_float(output.get("duration"))
            job.set_source_info(title=title, duration=duration)
            if duration > 0:
                job.log_line(
                    f"Thời lượng: {human_duration(duration)}. "
                    f"Dự kiến chạy xong sau {human_duration(job.estimated_total())}."
                )
        elif step == 9 and isinstance(output, dict):
            findings = [_finding_dict(f) for f in (output.get("findings") or [])]
            raw_backups = output.get("backups")
            backups = [
                str(item)
                for item in (raw_backups if isinstance(raw_backups, (list, tuple)) else [])
                if str(item or "").strip()
            ]
            job.set_result(
                {
                    "srt": output.get("srt"),
                    "vi_srt": output.get("vi_srt"),
                    "bundle": output.get("bundle"),
                    "report": output.get("report"),
                    "ass": output.get("ass"),
                    "bilingual_ass": output.get("bilingual_ass"),
                    "has_errors": bool(output.get("has_errors")),
                    "translation": output.get("translation") or {},
                    "stats": output.get("stats") or {},
                    # Hợp đồng H1: bản cũ bị cất đi phải tới được tay người dùng —
                    # màn hình kết quả, `/result` và sự kiện SSE cuối đều đọc từ đây.
                    "backups": backups,
                },
                findings,
                _summarize(findings),
                out_dir=str(ctx.out_dir),
            )
            if backups:
                # Chặng xuất file đã báo từng bản qua tiến trình, nhưng nhật ký nuốt
                # các câu báo sát nhau trong cùng một bước. Một dòng tổng kết ở đây
                # chắc chắn còn lại để người dùng đọc.
                names = ", ".join(f"“{Path(p).name}”" for p in backups)
                job.log_line(
                    f"Đã giữ lại {len(backups)} bản cũ trước khi ghi đè (vì chúng khác "
                    f"bản mới — có thể là bản bạn đã sửa tay): {names}. Chúng nằm cùng "
                    "thư mục với file mới, không file nào bị xoá.",
                    level="warn",
                )

    def _report_failure(self, job: Job, err: BaseException) -> None:
        job.fail(
            _user_message(err),
            fix_action=str(getattr(err, "fix_action", "") or ""),
            detail=_detail(err),
        )


# --------------------------------------------------------------------------- #
# tiện ích cho luồng thợ
# --------------------------------------------------------------------------- #

def _progress_for(job: Job, step: int) -> Callable[..., None]:
    """Bọc `job.update` thành callback mà các chặng mong đợi.

    Nhận cả `(message, fraction)` lẫn lời gọi thiếu tham số: một chặng gọi sai
    kiểu không được phép làm hỏng cả lần chạy.
    """

    def report(message: Any = "", fraction: Any = 0.0) -> None:
        job.update(step, str(message or ""), fraction)

    return report


def _forget_stages(ctx: Context, *stages: tuple[int, str]) -> None:
    """Xoá điểm lưu của vài chặng để chúng chạy lại thật sự.

    Cần khi AI tìm được tên riêng mới: S5/S6/S7 đều có file kết quả cũ và sẽ dùng
    lại ngay nếu file còn đó, tức là vòng chạy lại sẽ không đổi được gì.
    """
    for number, name in stages:
        try:
            ctx.stage_path(number, name).unlink(missing_ok=True)
        except OSError:
            pass


def _job_video_id(job: Job) -> str:
    """Mã thư mục làm việc mà một công việc đang (hoặc sẽ) dùng.

    Việc còn đang xếp hàng chưa có `video_id` (nó chỉ được đặt khi dựng
    `Context`), nhưng mã đó suy ra được y hệt từ nguồn — phép kiểm "đang có việc
    khác dùng thư mục này" phải thấy cả việc đang chờ, không chỉ việc đang chạy.
    """
    video_id = str(job.video_id or "").strip()
    if video_id:
        return video_id
    if job.mode in (MODE_RUN, MODE_RESUME) and str(job.source or "").strip():
        try:
            return make_video_id(job.source)
        except Exception:  # noqa: BLE001 - nguồn lạ thì coi như không trùng
            return ""
    return ""


def _stage_file_keys() -> dict[int, str]:
    """`{số chặng: tên file chặng}` lấy từ bảng chặng của `pipeline` — nguồn duy nhất.

    Import lười: `pipeline` không kéo thư viện nặng, nhưng module này phải import
    được cả trên máy chưa cài gì (xem `srtgen doctor`).
    """
    from srtgen.pipeline import STAGES

    return {stage.number: stage.save_key for stage in STAGES}


def _usable_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _check_rerun_inputs(folder: Path, step: int) -> None:
    """Mọi chặng trước `step` phải còn kết quả trong `folder`, không thì `JobError`.

    Kiểm TRƯỚC khi xếp việc, không để luồng thợ tự phát hiện: chặng nào thiếu
    kết quả sẽ tự làm lại — bước 0 thì đi tìm video gốc đã bị xoá, bước 2 thì
    nghe lại cả phim 30 phút — tức là "chạy lại từ bước 6" âm thầm biến thành
    một thứ khác hẳn. Hai chặng âm thanh còn cần chính file wav chúng trỏ tới.
    """
    keys = _stage_file_keys()
    for number in range(FIRST_STEP, step):
        stage_file = folder / f"S{number}_{keys.get(number, '')}.json"
        if not _usable_file(stage_file):
            if number == FIRST_STEP:
                raise JobError(
                    "Chưa chạy lại được: thư mục làm việc của phim này không còn kết quả "
                    f"của “{step_text(number)}”, mà bước đó cần video gốc. Hãy chọn lại "
                    "file (hoặc dán lại link) rồi bấm Bắt đầu."
                )
            raise JobError(
                f"Chưa chạy lại được từ “{step_text(step)}” vì thư mục làm việc của phim "
                f"không còn kết quả của “{step_text(number)}”. Hãy chạy lại từ "
                f"“{step_text(number)}” — các bước trước đó vẫn được dùng lại, không "
                "cần file gốc."
            )
        if number in _AUDIO_STAGES:
            try:
                saved = read_json(stage_file)
            except (OSError, ValueError):
                saved = None
            audio = str(saved.get("audio_path") or "") if isinstance(saved, dict) else ""
            if not audio or not _usable_file(Path(audio)):
                raise JobError(
                    "Chưa chạy lại được vì file âm thanh đã tách của phim này không còn "
                    f"trong thư mục làm việc ({folder}). Việc chạy lại dùng file đó thay "
                    "cho video gốc. Hãy chọn lại file video (hoặc dán lại link) rồi bấm "
                    "Bắt đầu."
                )


def _rerun_context(job: Job, cfg: dict[str, Any]) -> Context:
    """`Context` cho một việc chạy lại: đúng `video_id` và thư mục làm việc của việc cũ.

    Không gọi `new_context(job.source, …)`: hàm đó SUY thư mục làm việc từ nguồn
    theo cấu hình hiện tại, mà nguồn ở đây thường là bản tải lên đã bị xoá, và
    người dùng có thể vừa đổi thư mục làm việc trong Cài đặt — suy lại là có thể
    rơi sang một thư mục trống rồi đi tìm video gốc không còn nữa.

    File kết quả ghi vào đúng thư mục kết quả của việc cũ: đó là nơi có file
    người dùng đã sửa tay, và là nơi chặng xuất file cất bản cũ (H1) ngay cạnh
    file mới. `force_from` được đặt ở đây chứ không trông vào `config_factory`:
    một cấu hình quên đọc `from_stage` không được phép biến lần chạy lại thành
    một lần "dùng lại tất cả" không đổi được gì.
    """
    work = Path(job.work_dir) if job.work_dir else None
    if work is None or not work.is_dir():
        raise JobError(
            "Không còn thấy thư mục làm việc của phim này"
            + (f" ({work})" if work is not None else "")
            + ", nên không chạy lại được. Hãy chọn lại file (hoặc dán lại link) rồi bấm "
            "Bắt đầu."
        )
    # Bản sao nông: `config_factory` có thể trả về cùng một dict cho mọi việc, và
    # `force_from` của lần chạy lại này không được lây sang việc sau.
    cfg = dict(cfg)
    if job.rerun_from is not None:
        cfg["force_from"] = job.rerun_from
    paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}
    raw_out = (
        str(job.out_dir or "").strip()
        or str(job.options.get("out_dir") or "").strip()
        or str(paths.get("out_dir") or "").strip()
    )
    out = Path(raw_out).expanduser() if raw_out else user_data_dir() / "output"
    try:
        ensure_dir(out)
    except OSError as err:
        raise JobError(
            f"Không tạo được thư mục lưu kết quả: {out}. Hãy chọn một thư mục khác "
            "trong phần Cài đặt, hoặc kiểm tra quyền ghi của ổ đĩa."
        ) from err
    meta: dict[str, Any] = {
        "video_id": job.video_id,
        "source": str(job.source),
        "profile": cfg.get("profile", ""),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    return Context(video_id=job.video_id, work_dir=work, out_dir=out, cfg=cfg, meta=meta)


def _retain_upload(job: Job, ctx: Context, output: Any) -> Any:
    """Dời bản tải lên có hình vào thư mục làm việc và ghi `video_path` vào S0_info.

    Vì sao dời chứ không chép: file có thể vài GB. Vì sao chỉ file có hình: file
    tiếng đã được S0 chuyển thành wav, giữ thêm bản gốc là tốn chỗ vô ích.
    Không dời được (ổ đầy, đang bị khoá) thì bỏ qua — `release_upload` dọn như
    cũ và người dùng chỉ mất phần xem trước, không mất phụ đề.
    """
    if not job.cleanup_source or not isinstance(output, dict):
        return output
    src = Path(str(job.source or ""))
    if src.parent.name != UPLOADS_DIR_NAME or not src.is_file():
        return output
    if src.suffix.lower() not in _VIDEO_SUFFIXES:
        return output
    target = ctx.work_dir / f"source{src.suffix.lower()}"
    try:
        import shutil

        if target.exists():
            target.unlink()
        shutil.move(str(src), str(target))
    except OSError:
        return output
    fixed = dict(output)
    fixed["video_path"] = str(target)
    ctx.meta["video_path"] = str(target)
    try:
        ctx.save_stage(FIRST_STEP, "info", fixed)
    except (OSError, TypeError, ValueError):
        pass
    return fixed


_VIDEO_SUFFIXES = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".ts", ".flv", ".wmv"})


def release_upload(job: Job) -> bool:
    """Xoá bản tải lên của một công việc; trả `True` nếu đã xoá được một file.

    Hai chốt chặn, cả hai đều phải đúng mới xoá: cờ `cleanup_source` (chỉ máy
    chủ bật được) **và** file nằm ngay trong một thư mục tên `_uploads`. Chốt
    thứ hai là để một lỗi lập trình sau này — lỡ bật cờ cho một việc chạy từ
    file gốc của người dùng — không bao giờ biến thành xoá mất video của họ.

    Gọi nhiều lần vẫn an toàn: file không còn thì thôi.
    """
    if not job.cleanup_source:
        return False
    path = Path(str(job.source or ""))
    if path.parent.name != UPLOADS_DIR_NAME:
        return False
    try:
        if not path.is_file():
            return False
        path.unlink()
    except OSError:
        return False
    return True


def _apply_upload_title(job: Job, ctx: Context, output: Any) -> Any:
    """Thay tên ngẫu nhiên của bản tải lên bằng tên file gốc của người dùng.

    S0 đặt tiêu đề theo tên file nó đọc, mà với file tải lên thì đó là
    `3f2a…9c.mp4`. Không sửa ở đây thì kết quả ra là `3f2a…9c.srt` và
    `3f2a…9c_vi.srt` — người dùng mở thư mục ra không biết file nào của phim nào.

    Ghi cả vào `ctx.meta` (chặng xuất file đọc tên từ đó) lẫn `S0_info.json`
    (lần chạy tiếp theo đọc lại từ đó thay vì chạy lại S0).
    """
    title = str(job.title or "").strip()
    if not job.cleanup_source or not title or not isinstance(output, dict):
        return output
    stem = safe_stem(title)
    ctx.meta["title"] = title
    ctx.meta["stem"] = stem
    fixed = dict(output)
    fixed["title"] = title
    fixed["stem"] = stem
    try:
        ctx.save_stage(FIRST_STEP, "info", fixed)
    except (OSError, TypeError, ValueError):
        # Không ghi được thì lần chạy này vẫn đúng tên (đã nằm trong ctx.meta);
        # chỉ lần chạy tiếp theo mới thấy lại tên ngẫu nhiên.
        pass
    return fixed


def _is_cancellation(err: BaseException) -> bool:
    """Nhận diện “người dùng bấm Dừng” qua tên lớp lỗi.

    Mỗi chặng định nghĩa lớp huỷ riêng (`StageCancelled`, `AsrCancelled`) và
    chúng không có tổ tiên chung. Bắt theo tên là cách duy nhất không phải import
    cả tám module — mà import cả tám thì kéo theo `faster_whisper` và `jieba`.
    """
    name = type(err).__name__
    return "Cancel" in name or isinstance(err, KeyboardInterrupt)


def _user_message(err: BaseException) -> str:
    """Câu tiếng Việt để hiện cho người dùng, không phải traceback."""
    message = str(getattr(err, "user_message", "") or "").strip()
    if message:
        return message
    text = str(err).strip()
    if text and not _looks_technical(text):
        return text
    return (
        "Có lỗi khi chạy. Hãy mở phần “Xem chi tiết kỹ thuật” bên dưới "
        "và gửi nội dung đó cho người hỗ trợ."
    )


#: Dấu hiệu chắc chắn là câu của thư viện, kể cả khi trong câu có lẫn tiếng Việt
#: — đường dẫn file thường mang tên phim tiếng Việt, ví dụ
#: ``[Errno 2] No such file or directory: '/Users/binh/Phim Hàn.mp4'``.
_TECHNICAL_MARKS = (
    "traceback", "exception", "error:", "errno", "0x",
    "none type", "nonetype", "attributeerror", "keyerror", "valueerror",
    "typeerror", "oserror", "runtimeerror", "indexerror",
    "no such file", "permission denied", "out of range", "not found",
    "connection refused", "connection reset", "timed out", "failed to",
    "unable to", "invalid literal", "unexpected keyword",
)

#: Một chữ cái mà chỉ tiếng Việt mới có. Câu nào mang ít nhất một chữ như vậy là
#: câu do chính dự án này viết ra.
_VIETNAMESE_RE = re.compile(
    "[ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩị"
    "óòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]"
)


def _looks_technical(text: str) -> bool:
    """Thông điệp này là câu của thư viện, hay là câu dự án tự viết?

    Ba tầng, theo thứ tự:

    1. Có dấu hiệu chắc chắn (``Traceback``, ``Errno``, ``KeyError``…) thì là câu
       của thư viện, dù bên trong có lẫn chữ tiếng Việt.
    2. Nhiều dòng và rất dài thì là một bãi log.
    3. Không có **một** chữ tiếng Việt có dấu nào thì gần như chắc chắn cũng là
       câu của thư viện. Mọi câu trong dự án này đều viết bằng tiếng Việt có dấu,
       nên tầng này bắt được ``list index out of range``, ``Read timed out`` —
       những câu mà một danh sách liệt kê không bao giờ phủ hết.

    Đoán sai theo hướng nào cũng chỉ mất một câu: nhầm thành kỹ thuật thì người
    dùng đọc câu chung chung rồi mở "Chi tiết kỹ thuật"; nhầm theo hướng kia thì
    họ đọc thêm một câu tiếng Anh.
    """
    lowered = text.lower()
    if any(mark in lowered for mark in _TECHNICAL_MARKS):
        return True
    if "\n" in text and len(text) > 300:
        return True
    return _VIETNAMESE_RE.search(text) is None


def _detail(err: BaseException) -> str:
    detail = str(getattr(err, "detail", "") or "").strip()
    head = f"{type(err).__name__}: {err}".strip()
    return f"{head}\n{detail}".strip() if detail else head


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if number != number else number


def _asr_factor(cfg: dict[str, Any]) -> float:
    asr = cfg.get("asr") if isinstance(cfg.get("asr"), dict) else {}
    return max(0.1, _as_float(asr.get("eta_factor"), 0.8) or 0.8)


def _finding_dict(finding: Any) -> dict[str, Any]:
    """Chấp nhận cả `Finding` lẫn `dict` — chặng xuất file trả object, file lưu trả dict."""
    if isinstance(finding, dict):
        return dict(finding)
    to_dict = getattr(finding, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    return {
        "code": str(getattr(finding, "code", "")),
        "severity": str(getattr(finding, "severity", "error")),
        "cue_index": getattr(finding, "cue_index", None),
        "message": str(getattr(finding, "message", finding)),
        "line": str(getattr(finding, "line", "")),
    }


def _summarize(findings: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Đếm theo mức độ và theo mã lỗi.

    Cố ý không import `srtgen.core.rules.summarize`: hàm đó nhận `Finding`, còn ở
    đây dữ liệu đã là `dict` (vì phải ghi ra đĩa được), và `rules` kéo theo
    `pypinyin` khi dùng thật.
    """
    result: dict[str, Any] = {"error": 0, "warn": 0, "info": 0, "by_code": {}}
    for item in findings:
        severity = str(item.get("severity") or "error")
        result[severity] = int(result.get(severity, 0)) + 1
        code = str(item.get("code") or "")
        by_code = result["by_code"]
        by_code[code] = by_code.get(code, 0) + 1
    return result


def _done_message(job: Job) -> str:
    """Câu kết thúc, nói ngay điều người dùng cần biết: có lỗi hay không.

    Nhắc rõ “hai file” khi có bản tiếng Việt: đó là thứ người dùng đi tìm trong
    thư mục kết quả, và một câu chỉ nói “file phụ đề” khiến họ tưởng chỉ có một.
    """
    result = job.result or {}
    errors = int(job.summary.get("error", 0) or 0)
    warns = int(job.summary.get("warn", 0) or 0)
    if not result.get("srt"):
        return "Đã chạy xong."
    what = "hai file phụ đề (tiếng Trung và tiếng Việt)" if result.get("vi_srt") else "file phụ đề"
    if errors:
        text = (
            f"Đã xuất {what}, nhưng còn {errors} lỗi định dạng cần xem lại. "
            "Bấm “Kiểm tra và sửa” để sửa ngay từng dòng, hoặc “Xem báo cáo” "
            "để biết lỗi nằm ở dòng nào."
        )
    elif warns:
        text = f"Đã xuất {what}. Có {warns} chỗ nên xem lại trong báo cáo."
    else:
        text = f"Đã xuất {what}, không có lỗi định dạng nào."
    kept = job.backups()
    if kept:
        # Người dùng vừa sửa tay rồi chạy lại mà câu kết thúc không nhắc gì thì họ
        # sẽ tưởng phần sửa đã mất — và bỏ phần mềm. Nói ngay ở câu họ chắc chắn đọc.
        text += (
            f" Bản cũ của {len(kept)} file đã được giữ lại cạnh file mới (tên có "
            "“.truoc-”), không file nào bị xoá."
        )
    return text
