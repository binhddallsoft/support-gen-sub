"""Bộ điều phối mười chặng — dùng chung cho dòng lệnh và giao diện web.

Vì sao logic này nằm ở đây chứ không nằm trong `cli.py`:

* Thứ tự chặng, luật chạy lại vòng hai sau khi AI tìm được tên riêng, và cách
  gói lỗi thành câu tiếng Việt đều là **luật nghiệp vụ**, không phải chuyện hiển
  thị. Viết trong CLI thì web UI phải chép lại, và bản chép sẽ lệch khỏi bản gốc
  ngay lần sửa thứ nhất.
* `run_fix` và `run_check` cố ý **không đụng tới Whisper**: đây là giá trị dùng
  được ngay của tool (chuẩn hoá file bên dịch gửi sang), nên chúng phải chạy được
  trên máy chưa cài `faster-whisper`, chưa tải model, chưa có mạng.

Ba ràng buộc của dự án được tôn trọng:

1. Chỉ `srtgen.io_utils` được gọi `open()` — module này không đọc ghi file trực tiếp.
2. Không `print()`; mọi thứ cần nói với người dùng đi qua `on_progress` / `on_event`,
   hoặc nằm trong `user_message` của lỗi.
3. Mọi import nặng (các chặng, `faster_whisper`, `jieba`, `pypinyin`) đều **lười**,
   bên trong hàm — để `srtgen doctor` và web UI khởi động được trên máy trắng.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Sequence

from srtgen.stages import STAGE_LABELS, STAGE_MODULES

if TYPE_CHECKING:  # chỉ để gợi ý kiểu — import thật sẽ kéo theo pypinyin
    from srtgen.core.context import Context
    from srtgen.core.rules import Finding

__all__ = [
    "STAGES",
    "STEP_TOTAL",
    "MAX_LAPS",
    "EMIT_STAGE",
    "TRANSLATE_STAGE",
    "Stage",
    "StageEvent",
    "PipelineError",
    "PipelineCancelled",
    "ProgressFn",
    "EventFn",
    "is_cancellation",
    "stage_by_number",
    "stage_number",
    "list_video_ids",
    "context_for_video_id",
    "run_pipeline",
    "FixResult",
    "run_fix",
    "run_fix_bilingual",
    "run_check",
    "run_translate",
]

#: Chữ ký callback tiến trình, giống hệt chữ ký mà tám chặng đã dùng.
ProgressFn = Callable[[str, float], None]

#: Callback giàu thông tin hơn cho CLI (thanh tiến trình hai tầng) và cho SSE của web UI.
EventFn = Callable[["StageEvent"], None]


# --------------------------------------------------------------------------- #
# Mô tả một chặng
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Stage:
    """Một chặng của pipeline, kèm mọi thứ cần để hiển thị cho người không rành máy.

    `weight` là phần thời gian ước lượng của chặng trong tổng số, đo thô trên một
    video 40 phút của máy đích (iMac 2017, CPU): chặng nghe băng chiếm quá nửa, nên
    nếu chia đều mười phần thì thanh tiến trình sẽ đứng im ở 20% suốt nửa tiếng —
    đúng cái làm người dùng tưởng tool bị treo và tắt máy giữa chừng.
    """

    number: int
    key: str          # định danh chặng: fetch, audio, asr, ... (dùng cho `--from`)
    save_key: str     # tên điểm lưu: work/<id>/S<n>_<save_key>.json
    label: str        # tên tiếng Việt ngắn, hiển thị cho người dùng
    module: str       # đường dẫn module chứa hàm run(ctx, on_progress)
    weight: float
    note: str = ""    # câu giải thích thêm, UI hiện dưới tên bước
    slow: bool = False
    optional: bool = False   # bỏ qua được (S7 khi bật --no-ai)
    aliases: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[Any]:
        """Cho phép `number, key, label = stage` như đặc tả viết `(0, "fetch", ...)`."""
        yield from (self.number, self.key, self.label)

    @property
    def step(self) -> int:
        """Số thứ tự hiển thị, đếm từ 1 vì người dùng không đếm từ 0."""
        return self.number + 1

    @property
    def title(self) -> str:
        """Dòng tiêu đề đầy đủ: “Bước 3/9: Nghe và gỡ băng (lâu nhất)”.

        Chữ “(lâu nhất)” dán ngay vào tên bước chứ không để trong ghi chú, vì đó là
        thông tin người dùng cần thấy trước khi kịp lo lắng, kể cả ở những chỗ chật
        chỉ hiện được một dòng.
        """
        suffix = " (lâu nhất)" if self.slow else ""
        return f"Bước {self.step}/{STEP_TOTAL}: {self.label}{suffix}"

    def load(self) -> Any:
        """Nạp module của chặng — chỉ đúng lúc sắp chạy nó.

        Nạp sớm sẽ kéo `faster_whisper` và `jieba` vào mọi lệnh, kể cả `doctor`.
        """
        return import_module(self.module)


def _stage(
    number: int,
    key: str,
    save_key: str,
    weight: float,
    note: str,
    *,
    slow: bool = False,
    optional: bool = False,
    aliases: tuple[str, ...] = (),
) -> Stage:
    """Dựng một `Stage`, lấy tên module và nhãn tiếng Việt từ `srtgen.stages`.

    Không chép lại hai bảng đó ở đây là cố ý: nhãn hiển thị chỉ nên có **một** nguồn,
    nếu không sửa tên bước ở một chỗ sẽ để lại tên cũ ở chỗ kia.
    """
    return Stage(
        number=number,
        key=key,
        save_key=save_key,
        label=STAGE_LABELS[number],
        module=STAGE_MODULES[number],
        weight=weight,
        note=note,
        slow=slow,
        optional=optional,
        aliases=aliases,
    )


STAGES: tuple[Stage, ...] = (
    _stage(
        0, "fetch", "info", 0.08,
        "Tải âm thanh từ link về máy, hoặc chuyển đổi file bạn đã có sẵn.",
        aliases=("tai", "download", "s0"),
    ),
    _stage(
        1, "audio", "audio", 0.05,
        "Chuẩn hoá âm lượng để bước nghe băng đỡ nhầm.",
        aliases=("amthanh", "s1"),
    ),
    _stage(
        2, "asr", "asr", 0.50,
        "Bước này lâu nhất — thường bằng khoảng 0,6 đến 1 lần thời lượng video. "
        "Bạn có thể để máy chạy và làm việc khác.",
        slow=True,
        aliases=("whisper", "nghe", "s2"),
    ),
    _stage(
        3, "cleanup", "clean", 0.02,
        "Bỏ những đoạn máy nghe nhầm hoặc lặp đi lặp lại.",
        aliases=("clean", "don", "s3"),
    ),
    _stage(
        4, "cue", "cues", 0.03,
        "Cắt lời thoại thành từng dòng phụ đề ngắn theo nhịp nói.",
        aliases=("cues", "chia", "s4"),
    ),
    _stage(
        5, "tokenize", "tokens", 0.09,
        "Tách cụm từ và ghi pinyin cho từng cụm.",
        aliases=("tokens", "token", "pinyin", "s5"),
    ),
    _stage(
        6, "normalize", "norm", 0.03,
        "Sửa dấu câu, viết hoa đầu câu, áp bảng tên riêng.",
        aliases=("norm", "chuanhoa", "s6"),
    ),
    _stage(
        7, "ai", "ai", 0.08,
        "Nhờ AI soát lại tên riêng, âm đọc và dấu câu. Bỏ qua được, không bắt buộc.",
        optional=True,
        aliases=("s7",),
    ),
    _stage(
        8, "translate", "translate", 0.08,
        "Dịch từng câu sang tiếng Việt, gửi theo lô kèm ngữ cảnh và bảng tên riêng "
        "để xưng hô và tên nhân vật nhất quán cả phim.",
        aliases=("dich", "vi"),
    ),
    _stage(
        9, "emit", "emit", 0.04,
        "Kiểm lại toàn bộ quy chuẩn rồi ghi file .srt, _vi.srt, .bundle.json và báo cáo.",
        aliases=("export", "xuat"),
    ),
)

#: Tổng số bước hiển thị cho người dùng. Lấy từ chính `STAGES` để không bao giờ
#: in ra được câu vô nghĩa kiểu “Bước 9/8”.
STEP_TOTAL: int = len(STAGES)

#: Số vòng tối đa của pipeline. Vòng hai chỉ xảy ra khi AI (T1) tìm được tên riêng
#: mới: từ điển tách từ đổi thì cả phim phải tách lại. Không bao giờ có vòng ba —
#: vòng hai đã dùng đúng bảng tên riêng mà vòng một tìm ra.
MAX_LAPS: int = 2

#: Các chặng phải chạy lại ở vòng hai.
_RERUN_STAGES: tuple[int, ...] = (5, 6, 7)

#: Số chặng xuất file, và khoá của chặng dịch. Đặt tên thay vì viết số trần ở
#: giữa vòng lặp: lần đánh số lại vừa rồi (chèn chặng dịch vào giữa) sai đúng ở
#: những chỗ có con số trần, nên chúng không được phép quay lại.
EMIT_STAGE: int = 9
TRANSLATE_STAGE: int = 8
TRANSLATE_KEY: str = "translate"

_BY_NUMBER: dict[int, Stage] = {s.number: s for s in STAGES}


def stage_by_number(number: int) -> Stage:
    try:
        return _BY_NUMBER[int(number)]
    except (KeyError, TypeError, ValueError) as err:
        raise ValueError(
            f"Không có bước số {number}. Các bước hợp lệ là 0 đến {STEP_TOTAL - 1}."
        ) from err


def stage_number(value: str | int | None) -> int:
    """Đổi thứ người dùng gõ (`s5`, `5`, `tokenize`, `pinyin`) thành số chặng.

    Nhận nhiều cách gọi vì `--from` là tham số người dùng gõ tay, mà đây là lệnh
    người ta chỉ dùng khi đang bực (chạy lại sau khi có gì đó hỏng).
    """
    if value is None or str(value).strip() == "":
        return 0
    text = str(value).strip().lower().replace("-", "").replace("_", "")
    if text.isdigit():
        return stage_by_number(int(text)).number
    for stage in STAGES:
        if text in {stage.key, f"s{stage.number}", stage.save_key, *stage.aliases}:
            return stage.number
    options = ", ".join(f"s{s.number} ({s.label.lower()})" for s in STAGES)
    raise ValueError(
        f"Không hiểu bước “{value}”.\n"
        f"Hãy dùng một trong: {options}."
    )


# --------------------------------------------------------------------------- #
# Lỗi và huỷ
# --------------------------------------------------------------------------- #

class PipelineError(RuntimeError):
    """Một chặng hỏng, đã dịch sẵn sang câu người dùng đọc được.

    `user_message` là câu cho người dùng, `detail` là phần kỹ thuật gập lại trong
    UI, `fix_action` là mã ổn định để UI quyết định hiện nút gì ("Cài ffmpeg giúp
    tôi", "Cập nhật yt-dlp"…). Giữ nguyên ba trường này của `FetchError` để tầng
    trên chỉ phải biết một hình dạng lỗi duy nhất.
    """

    def __init__(
        self,
        user_message: str,
        *,
        stage: Stage | None = None,
        detail: str = "",
        fix_action: str = "none",
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.stage = stage
        self.stage_number = stage.number if stage else None
        self.stage_title = stage.title if stage else ""
        self.detail = detail
        self.fix_action = fix_action

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.user_message,
            "stage": self.stage_number,
            "stage_title": self.stage_title,
            "detail": self.detail,
            "fix_action": self.fix_action,
        }


class PipelineCancelled(RuntimeError):
    """Người dùng bấm Dừng (hoặc Ctrl+C). Không phải lỗi — UI hiện dòng xám, không báo đỏ."""

    def __init__(
        self,
        user_message: str = "Đã dừng theo yêu cầu của bạn.",
        *,
        stage: Stage | None = None,
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.stage = stage
        self.stage_number = stage.number if stage else None
        self.stage_title = stage.title if stage else ""
        self.detail = ""
        self.fix_action = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.user_message,
            "stage": self.stage_number,
            "stage_title": self.stage_title,
            "cancelled": True,
        }


def is_cancellation(err: BaseException) -> bool:
    """Lỗi này có phải “người dùng bấm Dừng” không?

    Nhận diện theo **tên lớp** chứ không bằng `isinstance`, vì các chặng dùng ba lớp
    huỷ khác nhau (`s0_fetch.StageCancelled` cho S0/S5/S6, `s3_cleanup.StageCancelled`
    cho S3/S4, `s2_asr.AsrCancelled` cho S2) và import đủ ba lớp ở đây sẽ kéo theo
    `faster_whisper` vào mọi lệnh — kể cả `srtgen check` trên máy chưa cài gì.
    """
    if isinstance(err, (KeyboardInterrupt, PipelineCancelled)):
        return True
    names = {cls.__name__ for cls in type(err).__mro__}
    return bool(names & {"StageCancelled", "AsrCancelled", "PipelineCancelled"})


# --------------------------------------------------------------------------- #
# Sự kiện tiến trình
# --------------------------------------------------------------------------- #

@dataclass
class StageEvent:
    """Một lần báo tiến trình, đủ để vẽ thanh hai tầng mà không phải đoán gì thêm."""

    stage: Stage
    phase: str            # "start" | "progress" | "done" | "skipped" | "reused"
    message: str
    stage_fraction: float
    overall_fraction: float
    lap: int = 1
    elapsed: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Dạng JSON cho `/api/jobs/{id}/events` (SSE)."""
        return {
            "stage": self.stage.number,
            "step": self.stage.step,
            "step_total": STEP_TOTAL,
            "key": self.stage.key,
            "label": self.stage.label,
            "title": self.stage.title,
            "note": self.stage.note,
            "slow": self.stage.slow,
            "phase": self.phase,
            "message": self.message,
            "stage_fraction": round(self.stage_fraction, 4),
            "overall_fraction": round(self.overall_fraction, 4),
            "lap": self.lap,
            "elapsed": round(self.elapsed, 2),
        }


class _Tracker:
    """Quy đổi tiến trình trong một chặng thành tiến trình của cả pipeline.

    Hai quyết định đáng ghi lại:

    * Mỗi chặng chiếm một khoảng tỉ lệ với `Stage.weight`, không chia đều — lý do
      xem docstring của `Stage`.
    * Con số tổng **không bao giờ giảm**. Vòng hai (chạy lại S5–S7 sau khi AI tìm
      được tên riêng) về mặt toán học là lùi lại, nhưng một thanh tiến trình tụt
      ngược làm người dùng tưởng tool hỏng và tắt máy. Thanh sẽ đứng yên trong lúc
      chạy lại, còn dòng chữ nói rõ đang làm gì.
    """

    def __init__(
        self,
        stages: Sequence[Stage],
        on_progress: ProgressFn | None,
        on_event: EventFn | None,
    ) -> None:
        total = sum(s.weight for s in stages) or 1.0
        self._span: dict[int, tuple[float, float]] = {}
        base = 0.0
        for stage in stages:
            width = stage.weight / total
            self._span[stage.number] = (base, width)
            base += width
        self._on_progress = on_progress
        self._on_event = on_event
        self._last = 0.0

    def overall(self, stage: Stage, fraction: float) -> float:
        base, width = self._span.get(stage.number, (self._last, 0.0))
        value = base + width * _clamp(fraction)
        self._last = max(self._last, min(1.0, value))
        return self._last

    def emit(
        self,
        stage: Stage,
        phase: str,
        message: str,
        fraction: float,
        *,
        lap: int = 1,
        elapsed: float = 0.0,
    ) -> None:
        """Gửi một cập nhật; callback của tầng trên hỏng cũng không được làm chết pipeline."""
        overall = self.overall(stage, fraction)
        if self._on_progress is not None:
            try:
                self._on_progress(f"{stage.title} — {message}" if message else stage.title, overall)
            except Exception:  # pragma: no cover - callback của UI, không tin được
                pass
        if self._on_event is not None:
            try:
                self._on_event(
                    StageEvent(
                        stage=stage,
                        phase=phase,
                        message=message,
                        stage_fraction=_clamp(fraction),
                        overall_fraction=overall,
                        lap=lap,
                        elapsed=elapsed,
                    )
                )
            except Exception:  # pragma: no cover - như trên
                pass


def _clamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


# --------------------------------------------------------------------------- #
# Thư mục làm việc
# --------------------------------------------------------------------------- #

def _work_root(cfg: Mapping[str, Any]) -> Path:
    from srtgen.core.context import user_data_dir

    paths = cfg.get("paths") if isinstance(cfg.get("paths"), Mapping) else {}
    raw = str((paths or {}).get("work_dir") or "").strip()
    return Path(raw).expanduser() if raw else user_data_dir() / "work"


def list_video_ids(cfg: Mapping[str, Any]) -> list[str]:
    """Các mã video đã từng chạy, để gợi ý khi người dùng gõ sai mã cho `resume`."""
    root = _work_root(cfg)
    try:
        return sorted(p.name for p in root.iterdir() if p.is_dir())
    except OSError:
        return []


def context_for_video_id(
    video_id: str,
    cfg: dict[str, Any],
    out_dir: str | Path | None = None,
) -> "Context":
    """Dựng `Context` cho một lần chạy tiếp, khi chỉ biết mã video chứ không biết nguồn.

    `new_context` suy mã video **từ nguồn**, nên nó không dùng được cho `resume`.
    Ở đây làm ngược lại: mã video là thứ đã biết, còn nguồn thì đọc lại từ
    `S0_info.json` để các chặng sau vẫn có tiêu đề phim mà đặt tên file kết quả.
    """
    from srtgen.core.context import Context, user_data_dir
    from srtgen.io_utils import ensure_dir, read_json

    work_dir = _work_root(cfg) / str(video_id).strip()
    if not work_dir.is_dir():
        known = list_video_ids(cfg)
        hint = ("\nCác lần chạy đang có: " + ", ".join(known[:20])) if known else (
            "\nChưa có lần chạy nào được lưu lại. Hãy dùng lệnh “srtgen run <link>” trước."
        )
        raise PipelineError(
            f"Không tìm thấy thư mục làm việc của “{video_id}”.{hint}"
        )

    paths = cfg.get("paths") if isinstance(cfg.get("paths"), Mapping) else {}
    raw_out = str(out_dir or (paths or {}).get("out_dir") or "").strip()
    out_path = Path(raw_out).expanduser() if raw_out else user_data_dir() / "output"
    try:
        out_path = ensure_dir(out_path)
    except OSError as err:
        raise PipelineError(
            f"Không ghi được vào thư mục lưu kết quả: {out_path}\n"
            f"Lý do: {err}\n"
            "Hãy chọn thư mục khác bằng tuỳ chọn --out."
        ) from err

    meta: dict[str, Any] = {"video_id": video_id, "resumed_at": _now()}
    info_path = work_dir / "S0_info.json"
    if info_path.is_file():
        try:
            info = read_json(info_path)
        except (OSError, ValueError):
            info = None
        if isinstance(info, Mapping):
            for key in ("source", "source_url", "title", "duration", "audio_path"):
                value = info.get(key)
                if value not in (None, ""):
                    meta.setdefault("source" if key == "source_url" else key, value)

    return Context(
        video_id=str(video_id).strip(),
        work_dir=work_dir,
        out_dir=out_path,
        cfg=cfg,
        meta=meta,
    )


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Chạy cả pipeline
# --------------------------------------------------------------------------- #

@dataclass
class _StageRun:
    """Nhật ký một lần chạy chặng, để báo cáo và để web UI hiện lại sau khi mở lại tab."""

    stage: Stage
    status: str          # "done" | "reused" | "skipped"
    elapsed: float = 0.0
    lap: int = 1
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.number,
            "step": self.stage.step,
            "key": self.stage.key,
            "label": self.stage.label,
            "title": self.stage.title,
            "status": self.status,
            "elapsed": round(self.elapsed, 2),
            "lap": self.lap,
            "message": self.message,
        }


def run_pipeline(
    ctx: "Context",
    on_progress: ProgressFn | None = None,
    start_from: int = 0,
    skip_ai: bool = False,
    *,
    on_event: EventFn | None = None,
    skip_translate: bool = False,
) -> dict[str, Any]:
    """Chạy các chặng từ `start_from` đến hết và trả về tóm tắt của cả lần chạy.

    Giá trị trả về gồm đường dẫn các file kết quả, danh sách vi phạm, thống kê, và
    nhật ký từng chặng — vừa đủ để CLI in ra và để web UI dựng lại màn hình kết quả
    mà không phải đọc thêm file nào.

    `skip_ai` và `skip_translate` là hai công tắc **riêng biệt**, không gộp làm một:
    tầng AI soát tên riêng và tầng dịch dùng chung ô mã API nhưng là hai việc khác
    hẳn nhau. Người dùng thường muốn tắt AI cho nhanh mà vẫn muốn có file tiếng
    Việt, và gộp hai cờ sẽ lặng lẽ lấy mất file đó của họ.

    Vòng hai (`rerun_s5`): khi nhiệm vụ T1 của chặng AI tìm được tên riêng mới, từ
    điển tách từ đã đổi nên **cả phim** phải tách lại — S5 → S6 → S7 chạy lần nữa,
    lần này T1 bị tắt vì bảng tên riêng vừa lập xong không có gì để tìm thêm. Việc
    ép chạy lại làm bằng `force_from` trong cấu hình chứ không xoá file: `has_stage`
    của `Context` đã hiểu luật đó, nên không có chỗ nào phải nhớ luật hai lần.

    Ném `PipelineCancelled` khi người dùng bấm Dừng, `PipelineError` khi một chặng
    hỏng. Cả hai đều mang sẵn câu tiếng Việt trong `user_message`.
    """
    start = max(0, min(int(start_from or 0), STAGES[-1].number))
    planned = [s for s in STAGES if s.number >= start]
    running = [s for s in planned if not _is_skipped(s, skip_ai, skip_translate)[0]]
    tracker = _Tracker(running, on_progress, on_event)

    started_at = time.perf_counter()
    log: list[_StageRun] = []
    emit_result: dict[str, Any] = {}
    laps = 1

    for stage in planned:
        skipped, why = _is_skipped(stage, skip_ai, skip_translate)
        if skipped:
            log.append(_StageRun(stage, "skipped", message=why))
            continue

        _stop_if_cancelled(ctx, stage)
        result, run_info = _run_stage(ctx, stage, tracker, lap=1)
        log.append(run_info)

        if stage.number == 7 and _wants_rerun(result):
            laps = _rerun_after_names(ctx, tracker, log)

        if stage.number == EMIT_STAGE and isinstance(result, Mapping):
            emit_result = dict(result)

    elapsed = time.perf_counter() - started_at
    return _summarise(
        ctx,
        log,
        emit_result,
        elapsed=elapsed,
        laps=laps,
        skip_ai=skip_ai,
        skip_translate=skip_translate,
    )


def _is_skipped(stage: Stage, skip_ai: bool, skip_translate: bool) -> tuple[bool, str]:
    """Chặng này có bị bỏ qua không, và nói với người dùng thế nào.

    Trả về luôn câu giải thích cùng chỗ với quyết định: một bước hiện chữ "bỏ qua"
    mà không nói vì sao là chỗ người dùng dừng lại và mất niềm tin vào tool.
    """
    if skip_ai and stage.optional:
        return True, "Bạn đã chọn không dùng AI."
    if skip_translate and stage.key == TRANSLATE_KEY:
        return True, "Bạn đã chọn không dịch sang tiếng Việt."
    return False, ""


def _run_stage(
    ctx: "Context",
    stage: Stage,
    tracker: _Tracker,
    *,
    lap: int,
) -> tuple[Any, _StageRun]:
    """Chạy đúng một chặng và dịch mọi lỗi của nó sang ngôn ngữ người dùng.

    Trạng thái "dùng lại" / "làm mới" được xác định SAU khi chặng chạy xong, từ
    chính việc chặng đã làm (`_stage_was_reused`), không đoán trước bằng
    `has_stage()`. Đoán trước thì sai đúng ở ca quan trọng nhất: S5 hoặc S8 thấy
    bảng tên riêng đã đổi và tự làm lại, mà nhật ký vẫn ghi "Dùng lại" — người
    dùng tưởng tên vừa gõ không được áp và mất công đi sửa tay từng câu.
    """
    had_result = ctx.has_stage(stage.number, stage.save_key)
    before = _stage_file_state(ctx, stage)
    force_before = ctx.force_from
    opening = (
        "Đang kiểm tra kết quả có sẵn…" if had_result else stage.note or "Đang chạy…"
    )
    tracker.emit(stage, "start", opening, 0.0, lap=lap)

    started = time.perf_counter()
    try:
        module = stage.load()
    except ImportError as err:
        raise PipelineError(
            _missing_stage_message(stage, err),
            stage=stage,
            detail=f"{type(err).__name__}: {err}",
        ) from err

    def report(message: str = "", fraction: float = 0.0) -> None:
        tracker.emit(stage, "progress", str(message), fraction, lap=lap)

    try:
        result = module.run(ctx, report)
    except KeyboardInterrupt as err:
        raise PipelineCancelled(
            "Bạn đã bấm Ctrl+C. Phần đã làm xong vẫn được giữ lại, "
            "lần sau chạy tiếp sẽ không phải làm lại từ đầu.",
            stage=stage,
        ) from err
    except Exception as err:
        if is_cancellation(err):
            raise PipelineCancelled(
                str(getattr(err, "user_message", "") or "Đã dừng theo yêu cầu của bạn."),
                stage=stage,
            ) from err
        raise PipelineError(
            _error_message(stage, err),
            stage=stage,
            detail=str(getattr(err, "detail", "") or f"{type(err).__name__}: {err}"),
            fix_action=str(getattr(err, "fix_action", "none") or "none"),
        ) from err

    elapsed = time.perf_counter() - started
    reused = had_result and _stage_was_reused(
        ctx, stage, result, before=before, force_before=force_before
    )
    status = "reused" if reused else "done"
    tracker.emit(stage, status, "Xong.", 1.0, lap=lap, elapsed=elapsed)
    return result, _StageRun(stage, status, elapsed=elapsed, lap=lap)


def _stage_file_state(ctx: "Context", stage: Stage) -> tuple[int, int] | None:
    """`(mtime_ns, size)` của file điểm lưu của chặng; `None` khi chưa có hoặc không đọc được.

    Đọc thẳng trên đĩa chứ không qua `has_stage`, vì `has_stage` trả "chưa có" cho
    mọi chặng từ `force_from` trở đi — còn câu cần hỏi ở đây là "chặng có vừa ghi
    lại file của nó không".
    """
    try:
        info = ctx.stage_path(stage.number, stage.save_key).stat()
    except (OSError, ValueError):
        return None
    return (info.st_mtime_ns, info.st_size)


def _stage_was_reused(
    ctx: "Context",
    stage: Stage,
    result: Any,
    *,
    before: tuple[int, int] | None,
    force_before: int | None,
) -> bool:
    """Chặng vừa chạy có THẬT SỰ dùng lại kết quả cũ không (chỉ hỏi khi đã có kết quả cũ).

    Ba bằng chứng, theo thứ tự tin cậy:

    1. Kết quả chặng tự khai: S7 và S8 gắn `resumed=True` khi dùng lại; một dict
       chặng trả về có khoá `resumed`/`reused` kiểu bool thì tin đúng khoá đó.
    2. Chặng tự xếp lịch làm lại: `s5_tokenize.redo_from_stage` hạ `force_from`
       xuống đúng số của chặng khi bảng tên riêng đổi — đó là "làm mới".
    3. File điểm lưu của chặng vừa được ghi lại (thời điểm sửa hoặc kích thước
       đổi) — chặng dùng lại kết quả cũ thì không ghi gì.

    Không có bằng chứng làm mới nào thì mới là "dùng lại".
    """
    if isinstance(result, Mapping):
        for key in ("resumed", "reused"):
            flag = result.get(key)
            if isinstance(flag, bool):
                return flag
    force_after = ctx.force_from
    if (
        force_after is not None
        and force_after <= stage.number
        and (force_before is None or force_before > stage.number)
    ):
        return False
    after = _stage_file_state(ctx, stage)
    return after is not None and after == before


def _wants_rerun(result: Any) -> bool:
    """Chặng AI có yêu cầu tách lại cụm từ cho cả phim không."""
    return bool(isinstance(result, Mapping) and result.get("rerun_s5"))


def _rerun_after_names(
    ctx: "Context",
    tracker: _Tracker,
    log: list[_StageRun],
) -> int:
    """Vòng hai: tách lại cụm từ bằng bảng tên riêng vừa lập, rồi chuẩn hoá lại.

    `force_from = 5` để `has_stage` coi S5, S6, S7 là chưa chạy; tắt T1 để không hỏi
    lại AI đúng câu vừa hỏi. Cấu hình vá xong **không trả lại như cũ**: S8 (dịch) và
    S9 (xuất file) chạy ngay sau đó cũng phải bỏ qua kết quả cũ, vì tài liệu vừa đổi
    — tách lại cụm từ có thể đổi cả cách chia câu, nên bản dịch cũ không còn khớp.
    """
    from srtgen.core.context import deep_merge

    ctx.cfg = deep_merge(
        ctx.cfg,
        {"force_from": _RERUN_STAGES[0], "ai": {"tasks": {"t1_names": False}}},
    )
    for number in _RERUN_STAGES:
        stage = stage_by_number(number)
        _stop_if_cancelled(ctx, stage)
        _, info = _run_stage(ctx, stage, tracker, lap=2)
        info.message = "Chạy lại vì AI vừa tìm được tên riêng mới."
        log.append(info)
    return MAX_LAPS


def _stop_if_cancelled(ctx: "Context", stage: Stage) -> None:
    if ctx.is_cancelled():
        raise PipelineCancelled(stage=stage)


def _missing_stage_message(stage: Stage, err: ImportError) -> str:
    return (
        f"Chưa chạy được bước “{stage.label}” vì máy thiếu một thư viện.\n"
        f"Cách xử lý: chạy lệnh “srtgen doctor” để xem thiếu gì và cách cài.\n"
        f"(Chi tiết kỹ thuật: {err})"
    )


def _error_message(stage: Stage, err: Exception) -> str:
    """Câu để hiển thị: ưu tiên câu chặng đã tự dịch, chỉ tự viết khi chặng không có."""
    ready = str(getattr(err, "user_message", "") or "").strip()
    if ready:
        return ready
    return (
        f"Bước “{stage.label}” gặp lỗi không lường trước nên phải dừng lại.\n"
        f"Cách xử lý: chạy “srtgen doctor” để kiểm tra máy. Nếu vẫn lỗi, gửi dòng "
        f"dưới đây cho người phát triển.\n"
        f"(Chi tiết kỹ thuật: {type(err).__name__}: {err})"
    )


def _summarise(
    ctx: "Context",
    log: Sequence[_StageRun],
    emit_result: Mapping[str, Any],
    *,
    elapsed: float,
    laps: int,
    skip_ai: bool,
    skip_translate: bool = False,
) -> dict[str, Any]:
    """Gộp kết quả chặng cuối với nhật ký các chặng thành một dict duy nhất."""
    findings = list(emit_result.get("findings") or [])
    summary: dict[str, Any] = {
        "video_id": ctx.video_id,
        "work_dir": str(ctx.work_dir),
        "out_dir": str(ctx.out_dir),
        "title": str(ctx.meta.get("title") or ""),
        "elapsed": round(elapsed, 2),
        "finished_at": _now(),
        "laps": laps,
        "skip_ai": bool(skip_ai),
        "skip_translate": bool(skip_translate),
        "stages": [item.to_dict() for item in log],
        "srt": emit_result.get("srt"),
        "vi_srt": emit_result.get("vi_srt"),
        "bundle": emit_result.get("bundle"),
        "report": emit_result.get("report"),
        "ass": emit_result.get("ass"),
        "bilingual_ass": emit_result.get("bilingual_ass"),
        "findings": findings,
        "has_errors": bool(emit_result.get("has_errors")),
        "translation": dict(emit_result.get("translation") or {}),
        "stats": dict(emit_result.get("stats") or {}),
    }
    return summary


# --------------------------------------------------------------------------- #
# Ba lệnh không dính tới Whisper
# --------------------------------------------------------------------------- #

@dataclass
class FixResult:
    """Kết quả của lệnh sửa file — một hình dạng duy nhất cho CLI, web và bộ test.

    * `text` — file Hán + pinyin, 4 dòng mỗi block, đã chuẩn hoá.
    * `vi_text` — file `_vi.srt` (3 dòng mỗi block), **cùng số block và cùng mốc
      thời gian** với `text`; `None` khi file đưa vào không có dòng tiếng Việt nào.
    * `findings` — vi phạm của file KẾT QUẢ (không phải file gốc), cộng một
      `BLOCK_SHAPE` cho mỗi dòng tool không xếp được, trích nguyên văn dòng đó, và
      các cảnh báo của cặp file khi có `vi_text`.
    * `set_aside` — nguyên văn những dòng không xếp được vào đâu. Người dùng không
      rành máy phải thấy được chữ của họ đi đâu; một dòng biến mất không lời giải
      thích là thứ làm họ mất niềm tin vào cả file.
    * `notes` — câu tiếng Việt tóm tắt cho người dùng (UI hiện trong nhật ký).
    * `stats` — số cue, số cue giữ pinyin / sinh pinyin / cắt lại, số câu có tiếng Việt.
    """

    text: str
    vi_text: str | None
    findings: list["Finding"]
    set_aside: list[str]
    notes: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def has_vi(self) -> bool:
        return self.vi_text is not None


def run_fix(
    srt_text: str,
    cfg: dict[str, Any] | None = None,
    *,
    names: Mapping[str, str] | None = None,
) -> tuple[str, list["Finding"]]:
    """Chuẩn hoá một file `.srt` có sẵn và trả về `(nội dung mới, danh sách vi phạm)`.

    Chỉ là lớp vỏ của `run_fix_bilingual` — **không có logic thứ hai**. Hai bản
    logic là đúng thứ đã làm CLI, web và bộ test cho ra ba file khác nhau.

    Chữ ký cũ không có chỗ cho file `_vi.srt`, nên khi file đưa vào có dòng tiếng
    Việt, danh sách trả về có thêm một `BLOCK_SHAPE` nói rõ các dòng đó KHÔNG nằm
    trong nội dung trả về, để không bên gọi nào làm mất chúng trong im lặng. Bên
    gọi muốn giữ chúng thì gọi thẳng `run_fix_bilingual`.
    """
    from srtgen.core.rules import Finding

    result = run_fix_bilingual(srt_text, cfg, names=names)
    findings = list(result.findings)
    if result.vi_text is not None:
        count = int(result.stats.get("vi_cues") or 0)
        findings.append(
            Finding(
                "BLOCK_SHAPE",
                "error",
                None,
                f"File có {count} câu kèm dòng tiếng Việt. Lệnh này chỉ trả về file "
                "Hán/pinyin, nên các dòng tiếng Việt KHÔNG nằm trong file đó (file gốc "
                "của bạn vẫn còn nguyên). Hãy dùng trang “Kiểm tra file” trên giao diện "
                "web để nhận kèm file _vi.srt.",
                "",
            )
        )
    return result.text, findings


def run_fix_bilingual(
    srt_text: str,
    cfg: dict[str, Any] | None = None,
    *,
    names: Mapping[str, str] | None = None,
) -> FixResult:
    """Chuẩn hoá một file `.srt` có sẵn — kể cả file song ngữ — và trả `FixResult`.

    Đây là **hàm sửa file duy nhất** của dự án: `run_fix` (CLI), `/api/fix` (web)
    và fixture của bộ test đều đi qua đây. Đường này **không** đi qua Whisper:
    đọc file, dựng lại token list từ các dòng đã có, rồi chạy đúng những bước
    S5–S6 mà một file do tool sinh ra cũng phải qua.

    Những điểm dễ hiểu nhầm:

    * **Dòng tiếng Việt** (block Hán/pinyin/Việt, hoặc Hán/Việt) được bộ đọc
      `srt.parse_srt` tách riêng bằng cách kiểm âm tiết pinyin
      (`srt.looks_like_pinyin`), không bao giờ bị ghép vào dòng pinyin. Chúng đi
      ra `vi_text`, cùng số block và cùng mốc thời gian với `text`. Block không có
      dòng Việt thì `_vi.srt` tạm để chữ Hán và có cảnh báo `VI_EMPTY_LINE`.
    * Ranh giới cụm và pinyin của người biên tập **được giữ nguyên**, quyết định
      theo từng cue — xem `_tokenize_edited_file`. Pinyin chỉ sinh lại cho cue
      không có dòng pinyin hoặc hai dòng lệch số cụm; tuyệt đối không kéo pinyin
      của cụm trước sang cụm sau, và không biến điệu pinyin người biên tập ghi.
    * `findings` là vi phạm của **file kết quả**. Muốn biết file gốc sai gì thì
      gọi `run_check` trước khi sửa.
    * Kiểm trên **văn bản đã ghép** chứ không trên `Document`, vì đó mới đúng là
      thứ người dùng mở bằng Aegisub, và đó cũng là cổng cứng trong bộ kiểm thử.

    `names` (bảng `{Hán: pinyin}` của một phim, web lấy theo mã video) thay cho
    bảng khai báo trong cấu hình; bỏ trống thì dùng bảng của cấu hình, nếu có.
    """
    from srtgen.core.rules import Finding, validate_pair, validate_text
    from srtgen.core.srt import emit_srt, parse_srt_document, set_aside_finding
    from srtgen.stages.s6_normalize import normalize_document

    cfg = cfg if cfg is not None else _default_config()
    table = dict(names) if names is not None else _names_from_cfg(cfg)

    doc = parse_srt_document(srt_text, normalize=True)
    vi_lines: list[str | None] = list(doc.meta.get("vi_lines") or [])
    aside: list[dict[str, Any]] = [dict(item) for item in doc.meta.get("set_aside") or []]
    layout = _tokenize_edited_file(doc, cfg, table)
    normalize_document(doc, cfg, names=table)

    text = emit_srt(doc)
    findings: list[Finding] = list(validate_text(text))
    notes: list[str] = []

    vi_text: str | None = None
    vi_count = sum(1 for line in vi_lines if line is not None)
    if vi_count:
        vi_text, vi_findings, dropped = _fixed_vi_file(doc, vi_lines)
        aside.extend(dropped)
        findings.extend(vi_findings)
        findings.extend(validate_pair(text, vi_text))
        notes.append(
            f"File gốc có {vi_count} câu kèm dòng tiếng Việt. Tool giữ nguyên các dòng "
            "đó và đưa sang file _vi.srt đi kèm (cùng số câu, cùng mốc thời gian)."
        )

    findings.extend(Finding(**set_aside_finding(item)) for item in aside)
    if aside:
        notes.append(
            f"Có {len(aside)} dòng tool không biết xếp vào đâu nên không đưa vào file "
            "đã sửa. Nội dung từng dòng được giữ nguyên trong danh sách lỗi (mục "
            "“BLOCK_SHAPE”) để bạn chép lại vào đúng chỗ."
        )

    stats: dict[str, Any] = {
        "cues": len(doc.cues),
        "blocks": text.count(" --> "),
        "vi_cues": vi_count,
        **layout,
    }
    return FixResult(
        text=text,
        vi_text=vi_text,
        findings=findings,
        set_aside=[str(item.get("text") or "") for item in aside],
        notes=notes,
        stats=stats,
    )


def _fixed_vi_file(
    doc: Any, vi_lines: Sequence[str | None]
) -> tuple[str, list["Finding"], list[dict[str, Any]]]:
    """Dựng `_vi.srt` khớp từng block với file vừa sửa: `(nội dung, cảnh báo, dòng để riêng)`.

    Nội dung do chính `s9_emit.build_vi_srt` dựng — cùng hàm mà pipeline đầy đủ
    dùng — nên hai file luôn cùng số block và cùng mốc thời gian **bằng cấu tạo**:
    cue bị `emit_srt` bỏ (không còn chữ nào) cũng bị bỏ ở đây.

    Hai trường hợp không được im lặng:

    * cue có dòng Việt nhưng không có dòng Việt → `VI_EMPTY_LINE` (file `_vi.srt`
      tạm để chữ Hán để hai file khớp dòng);
    * cue bị bỏ vì không còn chữ Hán mà lại có dòng Việt → dòng Việt đó được trả
      về để đưa vào `set_aside`, kèm finding trích nguyên văn.
    """
    from srtgen.core.rules import Finding
    from srtgen.core.token import render_py, render_zh
    from srtgen.stages.s9_emit import build_vi_srt

    findings: list[Finding] = []
    dropped: list[dict[str, Any]] = []
    number = 0
    for position, cue in enumerate(doc.cues):
        vi = vi_lines[position] if position < len(vi_lines) else None
        zh = render_zh(cue.tokens)
        if not zh and not render_py(cue.tokens):
            if vi is not None:
                dropped.append(
                    {
                        "cue": cue.index,
                        "text": vi,
                        "reason": "câu này không còn chữ Hán nào nên không có trong file "
                        "đã sửa; đây là dòng tiếng Việt của nó.",
                    }
                )
            continue
        number += 1
        if vi is None or not vi.strip():
            findings.append(
                Finding(
                    "VI_EMPTY_LINE",
                    "warn",
                    number,
                    f"Dòng {number}: file gốc không có câu tiếng Việt cho câu này; file "
                    "_vi.srt tạm để chữ Hán để hai file khớp dòng.",
                    zh,
                )
            )
    text = build_vi_srt(doc, [line or "" for line in vi_lines])
    return text, findings, dropped


#: Công tắc biến điệu dùng cho những cue GIỮ pinyin người biên tập: tắt hết.
#:
#: Chủ dự án đã chốt danh sách những gì `fix` được đụng vào pinyin có sẵn: rút gọn
#: 儿化音, dấu câu, viết hoa, tên riêng — và chỉ vậy. Biến điệu 不 (`bù` -> `bú`)
#: không nằm trong danh sách: đó là đổi thanh điệu người biên tập đã tự ghi, cùng
#: loại với việc đổi `shuí` thành `shéi` mà quyết định kia cố ý không làm. Biến điệu
#: vẫn chạy bình thường cho mọi cụm mà tool tự SINH pinyin.
_EDITOR_PINYIN_SANDHI: dict[str, bool] = {"bu": False, "yi": False, "third_tone": False}


def _has_editor_spaces(tokens: Sequence[Any]) -> bool:
    """Dòng Hán có khoảng trắng nằm GIỮA HAI CHỮ HÁN không?

    Đọc trên token chứ không trên chuỗi thô: `tokenize_line` chỉ tách hai cụm chữ
    liền nhau khi giữa chúng có khoảng trắng (dấu câu, marker và thẻ SRT tự thành
    token riêng), nên "hai token chữ đứng sát nhau" đúng bằng "người viết đã gõ
    khoảng trắng giữa hai cụm". `好，走吧。` vì thế là CHƯA phân cụm — dấu phẩy
    không phải lựa chọn cách tách từ của ai cả.

    Chỉ khoảng trắng giữa **chữ Hán cuối cụm trái và chữ Hán đầu cụm phải** mới
    là dấu vết phân cụm. Whisper tự chèn khoảng trắng quanh chữ Latin
    (`我们去 KTV 唱歌吧。`); coi đó là "đã phân cụm" thì cả dòng bị giữ nguyên
    thành `Wǒmenqù KTV chànggēba` — ba cụm dài vô nghĩa mà cổng vẫn báo 0 lỗi.
    Dòng như thế đi cho jieba cắt; jieba giữ nguyên khối Latin (`KTV`, `OK`)
    như một cụm, còn `cut_words` không bao giờ ghép hai token đã tách sẵn.
    """
    from srtgen.core.token import KIND_WORD
    from srtgen.stages.s5_tokenize import _has_han

    return any(
        left.kind == KIND_WORD
        and right.kind == KIND_WORD
        and bool(left.zh)
        and bool(right.zh)
        and _has_han(left.zh[-1])
        and _has_han(right.zh[0])
        for left, right in zip(tokens, tokens[1:])
    )


def _tokenize_edited_file(
    doc: Any, cfg: Mapping[str, Any], names: Mapping[str, str]
) -> dict[str, int]:
    """Chạy S5 trên một file có sẵn, quyết định **theo từng cue** giữ gì, sinh gì.

    Vì sao không theo cả file: một file bên dịch gửi sang thường là nửa nọ nửa kia —
    phần lớn cue đã được phân cụm và có pinyin, vài cue mới chép thêm từ bản gỡ băng
    thì chữ Hán còn dính liền. Một công tắc cho cả file buộc phải chọn giữa hai cái
    sai: cho jieba cắt lại tất cả (xoá công người biên tập — đo được 16/1351 cue của
    `corpus/filter.srt` bị cắt lại, `接住`->`接 住`, và toàn bộ pinyin bị sinh đè),
    hoặc giữ nguyên tất cả (cue dính liền ra một cụm dài vô nghĩa).

    Ba nhóm, xét theo thứ tự:

    1. **Có dòng pinyin khớp số cụm** -> giữ nguyên ranh giới VÀ pinyin. Kể cả khi
       dòng Hán không có khoảng trắng (`美国——纽约` / `měiguó——niǔyuē`): dòng pinyin
       ghép khớp từng cụm chính là lời người biên tập nói cụm nằm ở đâu — README:
       "lấy pinyin làm chuẩn để chia cụm từ". Cắt lại ở đây là vứt pinyin của họ.
       Sau đó chỉ còn các bước chuẩn hoá: 儿化音 (`nǎér` -> `nǎr`, S5), dấu câu,
       viết hoa, tên riêng (S6). Bảng ưu tiên đọc (`谁` -> `shéi`) không áp lên
       pinyin có sẵn — đó là lựa chọn biên tập, `PAIR_MISMATCH` chỉ cảnh báo.
    2. **Không có pinyin dùng được, nhưng dòng Hán đã phân cụm** (có khoảng trắng
       giữa hai cụm) -> giữ ranh giới, sinh pinyin mới cho từng cụm.
    3. **Không có pinyin dùng được và dòng Hán chưa phân cụm** -> jieba cắt, sinh
       pinyin mới — đúng như một cue Whisper vừa gỡ băng (`corpus/raw.srt`).

    "Không có pinyin dùng được" gồm block 3 dòng (không có dòng pinyin), cue mà
    `merge_zh_py` từ chối ghép vì lệch số cụm — nó đã trả `pinyin=None` cho mọi
    cụm của cue đó, nên ở đây không cần đoán lại — và cue mà "dòng pinyin" có chữ
    Hán (dòng Hán dài bị ngắt làm hai dòng, hoặc hai dòng Hán song song): chữ Hán
    không bao giờ là pinyin, nên giữ nó là ghi chữ Hán ra dòng pinyin.

    Mỗi nhóm đi qua `tokenize_document` như một tài liệu con **dùng chung đối
    tượng Cue** với `doc`, nên kết quả nằm sẵn trong `doc` theo đúng thứ tự cũ.
    Nhóm 3 rỗng thì jieba không được nạp — file đã biên tập xong chạy nhanh hơn
    hẳn trên iMac 2017.

    Trả về số cue của từng nhóm (cũng ghi vào `doc.meta["fix_layout"]`).
    """
    from srtgen.core.token import KIND_WORD, Document
    from srtgen.stages.s5_tokenize import _has_han, tokenize_document

    kept_pinyin: list[Any] = []
    new_pinyin: list[Any] = []
    resegment: list[Any] = []
    for cue in doc.cues:
        words = [t for t in cue.tokens if t.kind == KIND_WORD]
        if any(t.pinyin and _has_han(t.pinyin) for t in words):
            for t in words:  # cả dòng không phải pinyin: bỏ hết, như cue lệch số cụm
                t.pinyin = None
        if not words or all(t.pinyin for t in words):
            kept_pinyin.append(cue)
        elif _has_editor_spaces(cue.tokens):
            new_pinyin.append(cue)
        else:
            resegment.append(cue)

    kept_cfg = {**dict(cfg), "sandhi": dict(_EDITOR_PINYIN_SANDHI)}
    groups = (
        (kept_pinyin, True, kept_cfg),
        (new_pinyin, True, cfg),
        (resegment, False, cfg),
    )
    words_total = 0
    heteronyms = 0
    for cues, segmented, group_cfg in groups:
        if not cues:
            continue
        part = Document(cues=cues, meta={"segmented": segmented})
        tokenize_document(part, group_cfg, names=names)
        stats = part.meta.get("s5") or {}
        words_total += int(stats.get("words", 0))
        heteronyms += int(stats.get("heteronyms", 0))

    layout = {
        "kept_pinyin": len(kept_pinyin),
        "new_pinyin": len(new_pinyin),
        "resegmented": len(resegment),
    }
    doc.meta["segmented"] = True
    doc.meta["fix_layout"] = dict(layout)
    doc.meta["s5"] = {
        "cues": len(doc.cues),
        "words": words_total,
        "heteronyms": heteronyms,
        "names": len(names),
        "kept_boundaries": not resegment,
    }
    return layout


def run_check(srt_text: str, cfg: dict[str, Any] | None = None) -> list["Finding"]:
    """Kiểm một file `.srt` theo đủ 12 mục của quy chuẩn, không sửa gì.

    `cfg` chưa dùng tới nhưng vẫn nhận, để `check` và `fix` có cùng chữ ký ở cả CLI
    lẫn web UI — hai đường gọi này luôn đi cùng nhau và sẽ cùng cần cấu hình khi có
    thêm tuỳ chọn "bỏ qua cảnh báo loại này".
    """
    from srtgen.core.rules import validate_text

    del cfg  # xem docstring
    return validate_text(srt_text)


def run_translate(
    srt_text: str,
    cfg: dict[str, Any] | None = None,
    *,
    target: str | None = None,
    translator: Any | None = None,
    on_progress: ProgressFn | None = None,
) -> tuple[str, dict[str, Any]]:
    """Dịch một file `.srt` có sẵn và trả về `(nội dung _vi.srt, thông tin)`.

    Đây là thân của lệnh `srtgen translate <file.srt>`: dùng cho file bên dịch gửi
    sang hoặc file do tool xuất ra từ trước, **không** đi qua Whisper và không cần
    thư mục làm việc.

    Hai điểm bảo đảm, giống hệt đường chạy đầy đủ:

    * File trả về có **đúng số block** và **đúng mốc thời gian** của file đưa vào.
      Câu nào chưa dịch được thì giữ nguyên văn bản gốc, không bao giờ bỏ cue.
    * `normalize=False` khi đọc: đây là lệnh *dịch*, không phải lệnh *sửa*. Người
      dùng muốn chuẩn hoá file tiếng Trung thì có `srtgen fix` riêng, và lặng lẽ
      sửa dòng chữ Hán trong lúc dịch sẽ làm hai file họ đang có lệch nhau.
    """
    from srtgen.core.srt import parse_srt_document
    from srtgen.providers import get_translator, translate_config
    from srtgen.stages.s8_translate import build_glossary, translate_document
    from srtgen.stages.s9_emit import build_vi_srt

    cfg = cfg if cfg is not None else _default_config()
    settings = translate_config(cfg)
    language = str(target or settings.get("target") or "vi").strip() or "vi"

    doc = parse_srt_document(srt_text)
    if translator is None:
        translator = get_translator(cfg)

    glossary, glossary_info = build_glossary(_names_file(cfg))
    result = translate_document(
        doc,
        translator,
        target=language,
        glossary=glossary,
        batch_size=int(float(settings.get("batch_size") or 25)),
        context_window=int(float(settings.get("context_window") or 5)),
        retries=int(float(settings.get("retries") or 2)),
        on_progress=on_progress,
    )
    info: dict[str, Any] = {
        "target": language,
        "provider": str(getattr(translator, "name", "null")),
        "provider_reason": str(getattr(translator, "reason", "") or ""),
        "warning": str(getattr(translator, "warning", "") or ""),
        "counts": result.counts(),
        "glossary": dict(glossary_info),
        "errors": list(result.errors),
        "kept_original": list(result.kept),
    }
    return build_vi_srt(doc, result.lines), info


def _default_config() -> dict[str, Any]:
    from srtgen.core.context import load_config

    return load_config()


def _names_file(cfg: Mapping[str, Any]) -> Path | None:
    """Đường dẫn `names.json` cho một file rời, hoặc `None` nếu chưa khai báo.

    File rời không thuộc phim nào nên không có `work/<id>/names.json`; chỉ khi người
    dùng đã chỉ `paths.names_dir` trong cấu hình thì mới có bảng để áp.
    """
    paths = cfg.get("paths") if isinstance(cfg.get("paths"), Mapping) else {}
    names_cfg = cfg.get("names") if isinstance(cfg.get("names"), Mapping) else {}
    directory = str((paths or {}).get("names_dir") or "").strip()
    if not directory:
        return None
    path = Path(directory).expanduser() / str((names_cfg or {}).get("file") or "names.json")
    return path if path.is_file() else None


def _names_from_cfg(cfg: Mapping[str, Any]) -> dict[str, str]:
    """Bảng `{Hán: pinyin}` dùng khi sửa một file rời — rỗng nếu chưa khai báo."""
    from srtgen.stages.s7_ai import load_names

    path = _names_file(cfg)
    if path is None:
        return {}
    flat, _meta = load_names(path)
    return flat
