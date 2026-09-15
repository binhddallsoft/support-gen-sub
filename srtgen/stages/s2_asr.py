"""S2 — nghe và gỡ băng bằng faster-whisper (chặng lâu nhất của cả pipeline).

Vì sao chặng này được viết cẩn thận hơn các chặng khác:

* **Nó chạy 10-40 phút trên máy đích** (iMac 2017, Core i7 4 nhân, không có GPU) —
  10-15 phút với `large-v3-turbo` (mặc định), 30-40 phút với `large-v3`.
  Người dùng không phải dân IT nhìn một cửa sổ đứng im 30 phút sẽ tưởng tool treo,
  nên mọi thứ ở đây xoay quanh hai việc: *báo còn bao lâu* và *đừng bắt làm lại*.
* **Tải mô hình phải nối lại được.** Mô hình nặng 1.6-3GB; đứt mạng giữa chừng mà
  bắt tải lại từ đầu là hỏng cả buổi. Phần đã tải luôn được giữ nguyên, lần chạy sau
  tải tiếp; và trước khi dùng thì kiểm toàn vẹn, hỏng mới tải lại (xem
  `_ensure_model_available`).
* **Phải chạy tiếp được giữa chừng.** Có sẵn `S2_asr.json` là trả về ngay
  (`ctx.has_stage` đã gộp sẵn luật `force_from`). Bị bấm Dừng hoặc gặp lỗi giữa chừng
  thì phần đã nghe được ghi ra `S2_asr.partial.json` để không mất trắng — nhưng **không**
  ghi vào đúng tên file của chặng, vì một bản gỡ băng dở dang mà bị lần chạy sau tưởng
  là bản đầy đủ thì hỏng toàn bộ phần phụ đề phía sau.
* **Máy đích không có GPU NVIDIA.** `device="auto"` của CTranslate2 sẽ chọn sai hoặc
  ném lỗi khó hiểu, nên ở đây ép `cpu` + `int8`; đó cũng là cấu hình nhanh nhất trên
  CPU Intel và chất lượng gần như không đổi.

Ba ràng buộc chung của dự án được tôn trọng: không `print()` (mọi thứ đi qua
`on_progress`), không `open()` (mọi đọc/ghi đi qua `srtgen.io_utils`), và `faster_whisper`
được import **lười** bên trong hàm để `srtgen doctor` cùng web UI vẫn khởi động được
trên máy chưa cài gì.

Đầu ra `S2_asr.json`::

    {
      "segments": [{"start": 1.2, "end": 3.4, "text": "...",
                    "words": [{"start": .., "end": .., "word": "..", "probability": ..}]}],
      "meta": {"model": "large-v3-turbo", "device": "cpu", "compute_type": "int8",
               "threads": 4, "elapsed": 712.5, "rtf": 0.30, ...}
    }
"""

from __future__ import annotations

import math
import os
import platform
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from srtgen.core.context import Context, user_cache_dir
from srtgen.io_utils import read_json, write_json

__all__ = [
    "STAGE_NUMBER",
    "STAGE_NAME",
    "StageError",
    "AsrError",
    "AsrCancelled",
    "StageCancelled",
    "run",
    "model_status",
]

# --------------------------------------------------------------------------- #
# Hằng số kỹ thuật (KHÔNG phải tham số người dùng — tham số nằm ở config/default.yaml)
# --------------------------------------------------------------------------- #

STAGE_NUMBER = 2
STAGE_NAME = "asr"

#: Tên file kết quả dở dang. Cố ý KHÁC `S2_asr.json` để `has_stage()` không nhận nhầm.
PARTIAL_FILENAME = "S2_asr.partial.json"

#: Thứ tự dò file âm thanh trong `work/<video_id>/` khi S0/S1 không ghi rõ đường dẫn.
#: Bản đã chuẩn hoá âm lượng (S1) được ưu tiên hơn bản thô (S0).
AUDIO_FILENAMES = ("audio_norm.wav", "audio.wav", "audio.flac", "audio.m4a", "audio.mp3")

#: Các khoá mà S0/S1 có thể dùng để ghi đường dẫn âm thanh. Hai chặng đó do người khác
#: viết nên ở đây chấp nhận nhiều tên gọi thay vì ép một tên duy nhất rồi vỡ khi lệch.
AUDIO_KEYS = ("audio_norm", "audio_path", "audio", "wav", "wav_path", "output", "path", "file")
DURATION_KEYS = ("duration", "duration_sec", "duration_seconds", "seconds", "length")

#: Model dùng khi cấu hình không nói gì. Đây cũng là giá trị `asr.model` của
#: `config/default.yaml`; lặp lại ở đây chỉ để chặng không chết khi bị gọi với cấu hình rỗng.
DEFAULT_MODEL = "large-v3-turbo"

#: Dung lượng tải về xấp xỉ (MB) cho từng model, CHỈ dùng để vẽ phần trăm khi tải.
#: Sai vài phần trăm không sao; thiếu tên model thì báo theo số MB đã tải, không báo %.
#: Bảng `asr.models` trong cấu hình (nếu có `size_mb`) được ưu tiên hơn bảng này.
MODEL_SIZE_MB: dict[str, int] = {
    "tiny": 75, "tiny.en": 75,
    "base": 145, "base.en": 145,
    "small": 484, "small.en": 484,
    "medium": 1530, "medium.en": 1530,
    "large": 3090, "large-v1": 3090, "large-v2": 3090, "large-v3": 3090,
    "large-v3-turbo": 1620, "turbo": 1620,
    "distil-small.en": 340, "distil-medium.en": 790,
    "distil-large-v2": 1510, "distil-large-v3": 1510,
}

#: Hệ số ước lượng thời gian theo model: một giây tiếng nói tốn ngần này giây máy chạy
#: (CPU int8, iMac 2017 Core i7 4 nhân). Chỉ là con số KHỞI TẠO — nghe được
#: `MEASURE_AFTER` giây audio là tool thay bằng hệ số đo trên chính máy đang chạy.
#:
#: Số đo thật cho 5 phút audio: `large-v3-turbo` 19.6 giây, `large-v3` 52.6 giây
#: (turbo nhanh ~2.7 lần) -> 0.065 và 0.175 nếu quy đổi thẳng. Giá trị dưới đây cao hơn
#: nhiều lần vì máy đích chậm hơn máy đo và vì thà nói lâu rồi xong sớm còn hơn ngược lại;
#: chúng khớp với bảng thời gian hiện trong tab Cài đặt (video 40 phút: turbo ~10-15 phút,
#: large-v3 ~30-40 phút). Bảng `asr.models` trong cấu hình đè lên bảng này.
DEFAULT_ETA_FACTOR = 0.3
MODEL_ETA_FACTOR: dict[str, float] = {
    "tiny": 0.04, "tiny.en": 0.04,
    "base": 0.07, "base.en": 0.07,
    "small": 0.13, "small.en": 0.13,
    "medium": 0.25, "medium.en": 0.25,
    "large": 0.8, "large-v1": 0.8, "large-v2": 0.8, "large-v3": 0.8,
    "large-v3-turbo": 0.3, "turbo": 0.3,
    "distil-small.en": 0.07, "distil-medium.en": 0.12,
    "distil-large-v2": 0.3, "distil-large-v3": 0.3,
}

#: Tải mô hình: số lần thử và thời gian chờ giữa hai lần (giây, tăng gấp đôi mỗi lần).
#: Chờ tăng dần chứ không thử liên tục — máy chủ mô hình chặn theo tốc độ, dội vào nó
#: 5 lần trong 5 giây chỉ làm bị chặn lâu hơn.
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_BACKOFF_SEC = 5.0
DOWNLOAD_BACKOFF_MAX_SEC = 60.0

#: File trọng số nhỏ hơn mức này chắc chắn là bản tải dở, không phải mô hình thật.
MIN_WEIGHTS_BYTES = 10 * 1024 * 1024

#: Số luồng tối đa. Trên CPU, CTranslate2 hết cải thiện quanh số nhân vật lý;
#: đẩy cao hơn chỉ làm máy nóng và giật giao diện.
MAX_THREADS = 8

#: Whisper chỉ nhét được ~224 token vào ngữ cảnh mồi. Prompt dài hơn bị cắt cụt
#: ở chỗ không kiểm soát được, nên tự cắt trước cho biết đường mà cắt.
PROMPT_MAX_CHARS = 200

#: Nhịp báo tiến trình và nhịp ghi bản dở dang (giây).
REPORT_EVERY = 1.0
AUTOSAVE_EVERY = 120.0

#: Đã nghe được ngần này giây audio thì hệ số ước lượng đo thật mới đáng tin;
#: trước đó dùng `asr.eta_factor` trong cấu hình.
MEASURE_AFTER = 20.0

# Trọng số các pha trong thanh tiến trình của chặng (tổng = 1.0).
_P_PREPARE = (0.00, 0.02)
_P_DOWNLOAD = (0.02, 0.10)
_P_LOAD = (0.10, 0.14)
_P_WORK = (0.14, 0.99)


# --------------------------------------------------------------------------- #
# Lỗi
# --------------------------------------------------------------------------- #

class StageError(RuntimeError):
    """Lỗi của một chặng, kèm sẵn câu tiếng Việt để UI hiển thị thẳng.

    UI và CLI in `err.user_message`; `str(err)` có thêm phần kỹ thuật cho log.
    Tách hai thứ này ra vì người dùng cuối không đọc được traceback, còn người sửa
    lỗi thì cần đúng cái traceback đó.
    """

    def __init__(self, user_message: str, *, detail: str = "") -> None:
        super().__init__(user_message if not detail else f"{user_message} [{detail}]")
        self.user_message = user_message
        self.detail = detail


class AsrError(StageError):
    """Chặng gỡ băng không chạy được (thiếu thư viện, thiếu file, tải model hỏng…)."""


class AsrCancelled(StageError):
    """Người dùng bấm Dừng. Không phải lỗi — tách riêng để runner đừng báo đỏ."""


#: `s0_fetch.py` đặt tên lớp huỷ của nó là `StageCancelled`. Bí danh này để nơi gọi
#: (CLI, web UI) bắt được cùng một tên ở cả hai chặng; cả hai lớp đều có `.user_message`.
StageCancelled = AsrCancelled


# --------------------------------------------------------------------------- #
# Điểm vào
# --------------------------------------------------------------------------- #

def run(
    ctx: Context,
    on_progress: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Nghe file âm thanh của `ctx` và trả về danh sách segment kèm mốc thời gian từng từ.

    `on_progress(message, fraction)` được gọi liên tục với câu tiếng Việt đã có sẵn
    ước lượng thời gian còn lại; `fraction` trong khoảng 0..1 cho thanh tiến trình.
    Cho phép `None` để gọi được từ test và từ script mà không phải bịa ra callback rỗng.
    """
    report = _reporter(on_progress)
    cfg: dict[str, Any] = ctx.cfg or {}
    asr_cfg: dict[str, Any] = dict(cfg.get("asr") or {})

    cached = _load_finished_stage(ctx)
    if cached is not None:
        count = len(cached.get("segments") or [])
        report(f"Dùng lại bản gỡ băng của lần chạy trước ({count} đoạn), không phải nghe lại.", 1.0)
        _remember(ctx, cached.get("meta") or {})
        return cached

    _raise_if_cancelled(ctx)

    audio_path = _resolve_audio(ctx)
    threads = _thread_count(asr_cfg.get("cpu_threads"))
    device, compute_type = _device_and_compute(asr_cfg)
    duration_hint = _duration_hint(ctx, audio_path)

    # Chốt tên mô hình TRƯỚC khi báo cáo: câu "ước tính bao lâu" chỉ đúng khi biết đang
    # chạy mô hình nào (turbo nhanh gấp ~2.7 lần large-v3), và người dùng cần thấy ngay
    # từ dòng đầu là tool đang dùng mô hình gì.
    model_spec, download_root = _resolve_model_location(asr_cfg)
    eta_factor = _eta_factor(asr_cfg, model_spec)

    report(
        f"Chuẩn bị gỡ băng bằng mô hình {_model_phrase(asr_cfg, model_spec)}: "
        f"{threads} luồng CPU, chế độ {compute_type}, không dùng card đồ hoạ.",
        _at(_P_PREPARE, 0.3),
    )
    if duration_hint > 0:
        report(
            f"Đoạn tiếng dài khoảng {_clock(duration_hint)}. Đây là bước lâu nhất, "
            f"ước tính khoảng {_clock(duration_hint * eta_factor)}. "
            "Bạn có thể để máy chạy và làm việc khác.",
            _at(_P_PREPARE, 0.9),
        )

    whisper = _import_faster_whisper()
    _ensure_model_available(ctx, whisper, asr_cfg, model_spec, download_root, report)
    model = _load_model(whisper, model_spec, device, compute_type, threads, download_root, report)

    prompt = _build_prompt(ctx, asr_cfg)
    started = time.monotonic()
    segments, duration, cancelled = _transcribe(
        ctx, model, audio_path, asr_cfg, prompt, duration_hint, eta_factor, report
    )
    elapsed = time.monotonic() - started

    payload: dict[str, Any] = {
        "segments": segments,
        "meta": _build_meta(
            ctx=ctx,
            asr_cfg=asr_cfg,
            model_spec=model_spec,
            device=device,
            compute_type=compute_type,
            threads=threads,
            elapsed=elapsed,
            duration=duration,
            eta_factor=eta_factor,
            prompt=prompt,
            audio_path=audio_path,
            segments=segments,
            partial=cancelled,
        ),
    }

    if cancelled:
        saved = _save_partial(ctx, payload)
        raise AsrCancelled(
            "Bạn đã bấm Dừng. Phần đã nghe được giữ lại ở:\n"
            f"{saved}\n"
            "Bấm Bắt đầu lại với cùng đường dẫn để nghe lại từ đầu — các bước trước đó "
            "(tải và xử lý âm thanh) thì không phải làm lại."
        )

    ctx.save_stage(STAGE_NUMBER, STAGE_NAME, payload)
    _remove_partial(ctx)
    _remember(ctx, payload["meta"])
    report(
        f"Đã gỡ băng xong {len(segments)} đoạn "
        f"({_clock(duration)} tiếng nói, mô hình {_model_key(model_spec)} chạy mất "
        f"{_clock(elapsed)} trên {threads} luồng CPU).",
        1.0,
    )
    return payload


# --------------------------------------------------------------------------- #
# Tiến trình
# --------------------------------------------------------------------------- #

def _reporter(on_progress: Callable[[str, float], None] | None) -> Callable[[str, float], None]:
    """Bọc callback của UI lại: kẹp `fraction` về 0..1 và nuốt mọi lỗi của nó.

    Một callback UI ném lỗi không được phép làm hỏng chặng chạy 30 phút — đó là cái
    giá hoàn toàn không đáng.
    """

    def report(message: str, fraction: float) -> None:
        if on_progress is None:
            return
        try:
            value = float(fraction)
        except (TypeError, ValueError):
            value = 0.0
        if not math.isfinite(value):
            value = 0.0
        try:
            on_progress(str(message), max(0.0, min(1.0, value)))
        except Exception:  # pragma: no cover - callback do UI cung cấp, không tin được
            pass

    return report


def _at(phase: tuple[float, float], ratio: float) -> float:
    """Đổi tiến độ trong một pha (0..1) thành tiến độ của cả chặng."""
    low, high = phase
    return low + (high - low) * max(0.0, min(1.0, ratio))


def _raise_if_cancelled(ctx: Context) -> None:
    if ctx.is_cancelled():
        raise AsrCancelled("Bạn đã bấm Dừng, tool đã ngừng lại.")


# --------------------------------------------------------------------------- #
# Chạy tiếp giữa chừng
# --------------------------------------------------------------------------- #

def _load_finished_stage(ctx: Context) -> dict[str, Any] | None:
    """Trả về kết quả cũ nếu có và còn dùng được, ngược lại `None`.

    `has_stage()` đã tôn trọng `cfg["force_from"]`, nên `srtgen resume <id> --from s2`
    tự động bỏ qua nhánh này mà chặng không phải tự nhớ luật.
    """
    if not ctx.has_stage(STAGE_NUMBER, STAGE_NAME):
        return None
    data = ctx.load_stage(STAGE_NUMBER, STAGE_NAME)
    if not isinstance(data, dict):
        return None
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        return None  # file rỗng/hỏng: nghe lại còn hơn đi tiếp với dữ liệu cụt
    if (data.get("meta") or {}).get("partial"):
        return None
    return data


def _partial_path(ctx: Context) -> Path:
    return ctx.work_dir / PARTIAL_FILENAME


def _save_partial(ctx: Context, payload: dict[str, Any]) -> Path:
    """Ghi phần đã nghe được ra file riêng, không đụng tới file chính thức của chặng."""
    path = _partial_path(ctx)
    body = dict(payload)
    meta = dict(body.get("meta") or {})
    meta["partial"] = True
    body["meta"] = meta
    try:
        write_json(path, body)
    except OSError:
        pass  # hết đĩa thì cũng không còn gì để cứu, đừng nuốt mất lỗi gốc bằng lỗi này
    return path


def _remove_partial(ctx: Context) -> None:
    """Xoá bản dở dang khi đã có bản đầy đủ, để lần sau không ai nhặt nhầm."""
    try:
        _partial_path(ctx).unlink(missing_ok=True)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Tìm file âm thanh và thời lượng
# --------------------------------------------------------------------------- #

def _read_stage_json(ctx: Context, n: int, name: str) -> dict[str, Any]:
    """Đọc kết quả của một chặng trước, kể cả khi `force_from` che nó khỏi `has_stage`.

    S2 chỉ cần *đường dẫn file âm thanh* từ S0/S1, không cần biết chúng có phải chạy
    lại hay không, nên ở đây đọc thẳng file nếu nó tồn tại.
    """
    path = ctx.stage_path(n, name)
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return {}
        data = read_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _candidate_dicts(ctx: Context) -> Iterable[dict[str, Any]]:
    """Các nơi có thể chứa đường dẫn/thời lượng, xếp theo mức độ mới nhất trước."""
    for source in (ctx.meta, _read_stage_json(ctx, 1, "audio"), _read_stage_json(ctx, 0, "info")):
        if isinstance(source, dict) and source:
            yield source
            nested = source.get("meta")
            if isinstance(nested, dict):
                yield nested


def _resolve_audio(ctx: Context) -> Path:
    """Tìm file âm thanh do S0/S1 để lại.

    Chấp nhận nhiều tên khoá và cả đường dẫn tương đối vì S0/S1 do người khác viết:
    thà dò rộng một chút còn hơn để cả pipeline chết ở chỗ tên khoá lệch một chữ.
    """
    seen: set[str] = set()
    for source in _candidate_dicts(ctx):
        for key in AUDIO_KEYS:
            value = source.get(key)
            if not isinstance(value, str) or not value.strip() or value in seen:
                continue
            seen.add(value)
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = ctx.work_dir / path
            if _is_file(path):
                return path

    for name in AUDIO_FILENAMES:
        path = ctx.work_dir / name
        if _is_file(path):
            return path

    raise AsrError(
        "Không tìm thấy file âm thanh để gỡ băng.\n"
        f"Tool đã tìm trong thư mục: {ctx.work_dir}\n"
        "Hãy chạy lại từ đầu (bước tải và xử lý âm thanh) rồi thử lại."
    )


def _is_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _duration_hint(ctx: Context, audio_path: Path) -> float:
    """Thời lượng gần đúng, chỉ để nói trước "video dài khoảng bao nhiêu".

    Con số chuẩn lấy từ `info.duration` của faster-whisper sau khi bắt đầu nghe; ở đây
    chỉ cần đủ đúng để câu ước lượng đầu tiên không sai lệch quá đáng.
    """
    for source in _candidate_dicts(ctx):
        for key in DURATION_KEYS:
            value = _num(source.get(key))
            if value > 0:
                return value

    # WAV 16kHz mono 16-bit (đúng thứ S0 tạo ra): mỗi giây = 32000 byte.
    # Đọc header sẽ phải open() — mà chỉ io_utils được phép, nên ước theo dung lượng.
    if audio_path.suffix.lower() == ".wav":
        try:
            size = audio_path.stat().st_size
        except OSError:
            return 0.0
        return max(0.0, (size - 44) / 32000.0)
    return 0.0


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


# --------------------------------------------------------------------------- #
# Phần cứng
# --------------------------------------------------------------------------- #

def _thread_count(configured: Any) -> int:
    """Số luồng cho CTranslate2. `0` trong cấu hình nghĩa là "tự dò"."""
    try:
        value = int(configured)
    except (TypeError, ValueError):
        value = 0
    if value > 0:
        return max(1, min(value, MAX_THREADS))
    return _physical_cores()


def _physical_cores() -> int:
    """Số nhân **vật lý**, tối thiểu 1, tối đa `MAX_THREADS`.

    Dùng nhân vật lý chứ không phải số luồng logic: hai siêu luồng của cùng một nhân
    chia nhau một khối tính toán, giao cả 8 cho CTranslate2 trên i7 4 nhân không nhanh
    hơn mà còn làm máy đơ khi người dùng vẫn đang dùng máy.

    macOS trả lời chính xác qua `sysctl hw.physicalcpu`; nơi khác dùng luật ngón tay
    cái "máy từ 4 luồng trở lên thì coi như có siêu phân luồng" đúng như build-spec.
    """
    detected = _sysctl_physical_cores()
    if detected is None:
        logical = os.cpu_count() or 2
        detected = logical // 2 if logical >= 4 else logical
    return max(1, min(int(detected), MAX_THREADS))


def _sysctl_physical_cores() -> int | None:
    """Hỏi macOS số nhân vật lý; trả `None` ở mọi hệ khác hoặc khi hỏi không được."""
    if platform.system() != "Darwin":
        return None
    try:
        proc = subprocess.run(
            ["sysctl", "-n", "hw.physicalcpu"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        value = int((proc.stdout or "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _device_and_compute(asr_cfg: dict[str, Any]) -> tuple[str, str]:
    """Chốt `device` và `compute_type`, ưu tiên chạy được hơn là chiều theo cấu hình.

    `"auto"` bị loại thẳng: trên iMac Intel không có GPU NVIDIA, CTranslate2 sẽ chọn
    sai rồi ném lỗi mà người dùng không đọc nổi. Kiểu tính toán dạng float16 cũng bị
    hạ về `int8` khi chạy CPU, vì CPU không chạy được float16 và lỗi đó chỉ nổ ra
    *sau khi* đã tải xong 3GB model.
    """
    device = str(asr_cfg.get("device") or "cpu").strip().lower()
    if device != "cuda":
        device = "cpu"

    compute_type = str(asr_cfg.get("compute_type") or "").strip().lower()
    if device == "cpu":
        if compute_type not in ("int8", "int8_float32", "int16", "float32"):
            compute_type = "int8"
    elif compute_type not in ("float16", "int8_float16", "int8", "float32"):
        compute_type = "float16"
    return device, compute_type


def _eta_factor(asr_cfg: dict[str, Any], model_spec: str = "") -> float:
    """Hệ số ước lượng ban đầu: 1 giây audio tốn ngần này giây máy chạy.

    Hệ số **phụ thuộc mô hình**, không phải một con số chung: `large-v3-turbo` nhanh
    hơn `large-v3` khoảng 2.7 lần trên CPU int8 (5 phút audio hết 19.6s so với 52.6s),
    nên dùng chung một hệ số thì một trong hai bên sẽ báo sai gấp mấy lần — mà câu
    "còn khoảng bao lâu" sai gấp ba lần thì thà đừng nói.

    Thứ tự tìm: bảng `asr.models` trong cấu hình (người dùng sửa được) → bảng
    `MODEL_ETA_FACTOR` dựng sẵn → `asr.eta_factor` (đường lui cho mô hình lạ,
    ví dụ thư mục mô hình tự tải).
    """
    entry = _model_entry(asr_cfg, model_spec)
    value = _num(entry.get("eta_factor")) if entry else 0.0
    if value <= 0:
        value = MODEL_ETA_FACTOR.get(_model_key(model_spec), 0.0)
    if value <= 0:
        value = _num(asr_cfg.get("eta_factor"), DEFAULT_ETA_FACTOR)
    return min(10.0, max(0.05, value or DEFAULT_ETA_FACTOR))


def _model_key(model_spec: str) -> str:
    """Tên model dùng để tra bảng, từ một `asr.model` có thể là cả đường dẫn thư mục.

    Thư mục mô hình tải sẵn thường mang tên dài (`models--Systran--faster-whisper-large-v3`)
    và tên thư mục cuối cùng có khi chỉ là mã phiên bản (`…/snapshots/9f3c2a…`), nên dò
    tên đã biết ở phần cuối đường dẫn trước, không thấy thì dò trong cả đường dẫn.
    Dò **tên dài trước**, nếu không thì `large-v3-turbo` sẽ khớp nhầm vào `large-v3`
    và bị ước lượng chậm hơn thực tế gần ba lần.
    """
    name = str(model_spec or "").strip().replace("\\", "/").rstrip("/")
    if not name:
        return DEFAULT_MODEL
    whole = name.lower()
    base = whole.rsplit("/", 1)[-1]
    if base in MODEL_ETA_FACTOR or base in MODEL_SIZE_MB:
        return base
    for haystack in (base, whole):
        for known in sorted(MODEL_SIZE_MB, key=len, reverse=True):
            if known in haystack:
                return known
    return base or DEFAULT_MODEL


def _model_table(asr_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Bảng `asr.models` của cấu hình, đã lọc bỏ những mục viết sai."""
    raw = asr_cfg.get("models")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and item.get("id")]


def _model_entry(asr_cfg: dict[str, Any], model_spec: str) -> dict[str, Any]:
    """Dòng mô tả model trong `asr.models`, hoặc dict rỗng nếu model không có trong bảng."""
    key = _model_key(model_spec)
    for item in _model_table(asr_cfg):
        if str(item.get("id") or "").strip().lower() == key:
            return item
    return {}


def _model_phrase(asr_cfg: dict[str, Any], model_spec: str) -> str:
    """Cách gọi tên mô hình cho người dùng: “Cân bằng (khuyên dùng)” (large-v3-turbo)."""
    key = _model_key(model_spec)
    label = str(_model_entry(asr_cfg, model_spec).get("label") or "").strip()
    return f"“{label}” ({key})" if label else f"“{key}”"


def _model_size_mb(asr_cfg: dict[str, Any], model_spec: str) -> int:
    """Dung lượng tải về (MB) để vẽ phần trăm; 0 = không biết, khi đó chỉ báo số MB đã tải."""
    value = int(_num(_model_entry(asr_cfg, model_spec).get("size_mb")))
    if value > 0:
        return value
    return MODEL_SIZE_MB.get(_model_key(model_spec), 0)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #

def _import_faster_whisper() -> Any:
    """Import `faster_whisper` ngay lúc cần, và dịch lỗi thiếu thư viện sang tiếng Việt.

    Import lười là bắt buộc: `srtgen doctor` tồn tại để nói cho người dùng biết đang
    thiếu gì, nên chính nó không được chết vì thiếu thư viện.
    """
    try:
        import faster_whisper  # noqa: PLC0415 - cố ý import lười
    except ImportError as err:
        raise AsrError(
            "Máy chưa cài phần nghe-hiểu giọng nói (faster-whisper).\n"
            "Cách sửa: bấm đúp vào file CaiDat.command trong thư mục cài đặt để cài lại, "
            "hoặc mở Terminal và chạy: pip install faster-whisper",
            detail=str(err),
        ) from err
    return faster_whisper


def _resolve_model_location(asr_cfg: dict[str, Any]) -> tuple[str, Path | None]:
    """Trả về (tên model hoặc thư mục model có sẵn, thư mục cache để tải về).

    Ba cách chỉ định, xét theo thứ tự:

    1. `asr.model_dir` / `asr.model_path` / `asr.local_model` — thư mục model đã tải sẵn
       (bộ cài chép vào máy, hoặc người dùng tải bằng tay). Dùng thẳng, không hỏi mạng.
    2. `asr.model` trỏ tới một thư mục có thật — cũng coi là model có sẵn.
    3. `asr.model` là tên ("large-v3-turbo") — tải về `asr.download_root`, rỗng thì dùng
       thư mục cache chuẩn của hệ điều hành để lần sau khỏi tải lại 1.6GB.
    """
    for key in ("model_dir", "model_path", "local_model"):
        raw = str(asr_cfg.get(key) or "").strip()
        if raw:
            path = Path(raw).expanduser()
            if path.is_dir():
                return str(path), None
            raise AsrError(
                f"Không tìm thấy thư mục mô hình đã chỉ định trong phần Cài đặt:\n{path}\n"
                "Hãy chọn lại thư mục, hoặc để trống để tool tự tải mô hình về."
            )

    model = str(asr_cfg.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    as_path = Path(model).expanduser()
    if os.sep in model or "/" in model:
        if as_path.is_dir():
            return str(as_path), None

    root_raw = str(asr_cfg.get("download_root") or "").strip()
    root = Path(root_raw).expanduser() if root_raw else user_cache_dir() / "models"
    return model, root


def _ensure_model_available(
    ctx: Context,
    whisper: Any,
    asr_cfg: dict[str, Any],
    model_spec: str,
    download_root: Path | None,
    report: Callable[[str, float], None],
) -> None:
    """Bảo đảm mô hình có mặt trên đĩa **và còn nguyên vẹn** trước khi nạp.

    Ba việc, theo đúng thứ tự:

    1. **Có sẵn và lành lặn thì thôi.** Hỏi cache bằng `local_files_only=True` rồi tự
       kiểm toàn vẹn (xem `_integrity_problem`). Chỉ khi cả hai đều đạt mới bỏ qua mạng.
    2. **Tải là tải tiếp.** `huggingface_hub` để phần tải dở ở `*.incomplete` ngay trong
       cache và lần gọi sau nối tiếp từ đó, nên ở đây tuyệt đối KHÔNG xoá cache trước khi
       tải: xoá đi là bắt người dùng tải lại 1.6GB chỉ vì rớt mạng một lần. Đứt giữa chừng
       thì chờ tăng dần rồi thử lại — máy chủ mô hình chặn theo tốc độ, dội liên tục vào
       nó chỉ làm bị chặn lâu hơn.
    3. **Hỏng thật thì mới tải lại.** Tải xong mà kiểm toàn vẹn vẫn không đạt nghĩa là bản
       trong cache hỏng (đĩa lỗi, hoặc bản tải dở từ một phiên bản thư viện cũ). Lúc đó
       mới xoá đúng thư mục của mô hình đó, tải lại một lần, và nói rõ đang làm gì.

    Vì sao đo tiến trình bằng dung lượng thư mục cache chứ không móc vào thanh tiến trình
    của `huggingface_hub`: thanh đó là `tqdm` in thẳng ra terminal, muốn chuyển hướng phải
    vá lớp `tqdm` của thư viện ngoài — thứ đổi tên giữa các phiên bản và sẽ im lặng
    ngừng chạy sau một lần `pip install -U`. Đếm byte trong thư mục thì phiên bản nào
    cũng đúng, và cũng chính là con số người dùng quan tâm.
    """
    if download_root is None:
        return  # đã là thư mục model có sẵn trên đĩa

    download_model = getattr(whisper, "download_model", None)
    if download_model is None:  # phiên bản lạ: để WhisperModel tự lo
        report("Đang chuẩn bị mô hình nhận dạng…", _at(_P_DOWNLOAD, 0.5))
        return

    name = _model_phrase(asr_cfg, model_spec)
    cached = _cached_snapshot(download_model, model_spec, download_root)
    problem = _integrity_problem(cached)
    if problem is None:
        report(f"Mô hình {name} đã có sẵn trên máy, không phải tải lại.", _at(_P_DOWNLOAD, 1.0))
        return

    total_mb = _model_size_mb(asr_cfg, model_spec)
    total_bytes = total_mb * 1024 * 1024
    size_phrase = f" (khoảng {_size(total_bytes)})" if total_mb else ""
    if cached is None:
        report(
            f"Lần đầu chạy nên phải tải mô hình {name}{size_phrase}. Chỉ tải một lần, "
            "những lần sau dùng lại ngay. Đang tải mà mất mạng cũng không sao: lần sau "
            "tải tiếp chứ không tải lại từ đầu.",
            _at(_P_DOWNLOAD, 0.0),
        )
    else:
        report(
            f"Bản mô hình {name} đang có trên máy chưa dùng được ({problem}). "
            "Tool tải nốt phần còn thiếu.",
            _at(_P_DOWNLOAD, 0.0),
        )

    purged = False
    wait = DOWNLOAD_BACKOFF_SEC
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        error = _download_once(ctx, download_model, model_spec, download_root, total_bytes, report)

        if error is None:
            path = _cached_snapshot(download_model, model_spec, download_root)
            problem = _integrity_problem(path)
            if problem == "tải chưa xong" and path is not None:
                # `snapshot_download` chỉ trả về khi mọi file đã tải xong, nên dấu
                # `*.incomplete` còn sót lại đây là rác của một lần tải cũ (mô hình đã
                # đổi phiên bản). Dọn rác rẻ hơn bắt người dùng tải lại 1.6GB rất nhiều.
                _clear_incomplete(path)
                problem = _integrity_problem(path)
            if problem is None:
                report("Tải mô hình xong.", _at(_P_DOWNLOAD, 1.0))
                return
            if not purged and path is not None and attempt < DOWNLOAD_ATTEMPTS:
                purged = True
                report(
                    f"Bản mô hình vừa tải bị lỗi ({problem}). Tool xoá bản hỏng rồi tải lại "
                    "từ đầu — lần này sẽ lâu hơn bình thường.",
                    _at(_P_DOWNLOAD, 0.0),
                )
                _purge_model(path)
                continue
            raise AsrError(
                f"Mô hình {name} tải về nhưng dùng không được ({problem}).\n"
                "Cách sửa: kiểm tra ổ đĩa còn trống ít nhất "
                f"{_size(total_bytes * 2) if total_mb else '5 GB'}, rồi bấm Bắt đầu lại. "
                "Nếu vẫn hỏng, vào Cài đặt chọn mô hình nhẹ hơn (“Thử nhanh”).",
                detail=f"model={model_spec} problem={problem}",
            )

        if attempt >= DOWNLOAD_ATTEMPTS:
            # Ca thật trên iMac: mạng vẫn tải được video YouTube và thư viện Python,
            # riêng huggingface.co (kho chứa mô hình) thì không vào được. Câu "kiểm
            # tra kết nối Internet" chung chung khiến người dùng nhìn thấy mạng vẫn
            # chạy mà không biết phải làm gì. Nói đúng tên trang và cách thử.
            raise AsrError(
                f"Tải mô hình {name} không xong sau {DOWNLOAD_ATTEMPTS} lần thử: máy không "
                "kết nối được tới huggingface.co, trang chứa mô hình nhận dạng giọng nói.\n"
                "Cách xử lý: mở https://huggingface.co bằng Safari trên chính máy này. "
                "Nếu trang không mở được thì mạng nơi đặt máy đang chặn trang đó — hãy thử "
                "mạng khác (Wi-Fi khác hoặc phát từ điện thoại) rồi bấm Bắt đầu lại. Phần "
                "đã tải vẫn được giữ, không phải tải lại từ đầu.",
                detail=f"{type(error).__name__}: {error}",
            ) from error

        report(
            f"Tải mô hình bị gián đoạn (lần {attempt}/{DOWNLOAD_ATTEMPTS}). "
            f"Chờ {int(round(wait))} giây rồi tải tiếp từ chỗ đang dở…",
            _at(_P_DOWNLOAD, 0.0),
        )
        _sleep_with_cancel(
            ctx,
            wait,
            "Bạn đã bấm Dừng trong lúc chờ tải lại mô hình. "
            "Phần đã tải được giữ lại, lần sau sẽ tải tiếp chứ không tải lại từ đầu.",
        )
        wait = min(wait * 2, DOWNLOAD_BACKOFF_MAX_SEC)

    # Không bao giờ tới đây (mọi nhánh trong vòng lặp đều return hoặc raise), nhưng nếu
    # có ai sửa vòng lặp thì thà báo lỗi rõ ràng còn hơn lặng lẽ đi nạp một mô hình hỏng.
    raise AsrError(f"Không chuẩn bị được mô hình {name}. Hãy bấm Bắt đầu lại.")


def _download_once(
    ctx: Context,
    download_model: Callable[..., Any],
    model_spec: str,
    download_root: Path,
    total_bytes: int,
    report: Callable[[str, float], None],
) -> BaseException | None:
    """Chạy một lượt tải, vừa tải vừa báo tiến trình. Trả về lỗi (nếu có) thay vì ném.

    Trả lỗi về cho nơi gọi quyết định là vì chỗ này không biết còn được thử lại mấy lần;
    chỉ có `_ensure_model_available` biết, và chỉ nó mới nói đúng câu cho người dùng.
    """
    outcome: dict[str, Any] = {}

    def worker() -> None:
        try:
            outcome["path"] = download_model(
                model_spec, cache_dir=str(download_root), local_files_only=False
            )
        except BaseException as err:  # noqa: BLE001 - phải mang được mọi lỗi về luồng chính
            outcome["error"] = err

    baseline = _dir_size(download_root)
    thread = threading.Thread(target=worker, name="srtgen-model-download", daemon=True)
    thread.start()

    while True:
        thread.join(timeout=REPORT_EVERY)
        downloaded = max(0, _dir_size(download_root) - baseline)
        if not thread.is_alive():
            break
        if ctx.is_cancelled():
            # Không giết được luồng tải, nhưng nó là daemon nên tắt tool là xong.
            # Phần đã tải nằm lại trong cache và lần sau tải tiếp từ đó.
            raise AsrCancelled(
                "Bạn đã bấm Dừng trong lúc tải mô hình. "
                "Phần đã tải được giữ lại, lần sau sẽ tải tiếp chứ không tải lại từ đầu."
            )
        if total_bytes > 0:
            ratio = min(0.99, downloaded / total_bytes)
            report(
                f"Đang tải mô hình: {int(ratio * 100)}% "
                f"({_size(downloaded)} / {_size(total_bytes)})",
                _at(_P_DOWNLOAD, ratio),
            )
        else:
            report(f"Đang tải mô hình: {_size(downloaded)}", _at(_P_DOWNLOAD, 0.5))

    error = outcome.get("error")
    return error if isinstance(error, BaseException) else None


def _sleep_with_cancel(ctx: Context, seconds: float, message: str = "") -> None:
    """Chờ, nhưng vẫn bấm Dừng được: chia nhỏ giấc ngủ và hỏi lại sau mỗi lát.

    `time.sleep(60)` một phát thì nút Dừng đứng hình một phút — với người dùng đó là
    "tool treo", và họ sẽ tắt cứng ứng dụng.
    """
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if ctx.is_cancelled():
            raise AsrCancelled(message or "Bạn đã bấm Dừng, tool đã ngừng lại.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


# --------------------------------------------------------------------------- #
# Kiểm toàn vẹn mô hình
# --------------------------------------------------------------------------- #

#: File trọng số: CTranslate2 dùng `model.bin`, vài repo mới dùng `model.safetensors`.
WEIGHT_FILENAMES = ("model.bin", "model.safetensors")

#: Bảng từ vựng: repo cũ có `vocabulary.*`, repo mới có `tokenizer.json`. Cần ÍT NHẤT một.
VOCAB_FILENAMES = ("tokenizer.json", "vocabulary.json", "vocabulary.txt")


def _cached_snapshot(
    download_model: Callable[..., Any], model_spec: str, download_root: Path
) -> Path | None:
    """Thư mục mô hình trong cache, hoặc `None` nếu chưa có. Không gọi mạng."""
    try:
        path = download_model(model_spec, cache_dir=str(download_root), local_files_only=True)
    except Exception:
        return None  # chưa có trong cache — đường đi bình thường của lần chạy đầu
    try:
        folder = Path(str(path))
        return folder if folder.is_dir() else None
    except (OSError, TypeError, ValueError):
        return None


def _integrity_problem(path: Path | None) -> str | None:
    """Mô tả ngắn (tiếng Việt) chỗ hỏng của thư mục mô hình, `None` nghĩa là dùng được.

    Kiểm ở đây rẻ hơn kiểm bằng cách nạp thử rất nhiều: nạp một mô hình 1.6GB rồi mới
    biết nó cụt thì người dùng đã chờ vô ích, còn thông báo lỗi của CTranslate2 thì
    không ai ngoài lập trình viên đọc được.

    Cố ý **không** đòi `config.json` phải có mặt: những repo CTranslate2 tự dựng đôi khi
    thiếu file đó mà vẫn chạy. Nhưng nếu file đó có mà đọc không ra JSON thì chắc chắn
    là bản tải hỏng.
    """
    if path is None:
        return "chưa tải về máy"
    try:
        if not path.is_dir():
            return "chưa tải về máy"
    except OSError:
        return "không đọc được thư mục mô hình"

    if _has_incomplete(path):
        return "tải chưa xong"

    weights = _first_existing(path, WEIGHT_FILENAMES)
    if weights is None:
        return "thiếu file trọng số"
    try:
        if weights.stat().st_size < MIN_WEIGHTS_BYTES:
            return "file trọng số mới tải được một phần"
    except OSError:
        return "không đọc được file trọng số"

    if _first_existing(path, VOCAB_FILENAMES) is None:
        return "thiếu bảng từ vựng"

    config = path / "config.json"
    try:
        if config.is_file() and not isinstance(read_json(config), dict):
            return "file cấu hình của mô hình bị hỏng"
    except (OSError, ValueError):
        return "file cấu hình của mô hình bị hỏng"
    return None


def _first_existing(folder: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = folder / name
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            continue
    return None


def _hf_repo_dir(snapshot: Path) -> Path | None:
    """Thư mục `models--org--repo` chứa cả `blobs/` lẫn `snapshots/`, nếu nhận ra được.

    Bản tải dở nằm ở `blobs/*.incomplete`, tức là NGOÀI thư mục snapshot; muốn biết
    "tải xong chưa" thì phải nhìn lên một tầng.
    """
    try:
        if snapshot.parent.name == "snapshots":
            return snapshot.parent.parent
    except OSError:
        return None
    return None


def _has_incomplete(snapshot: Path) -> bool:
    """Còn file `*.incomplete` nghĩa là lần tải trước đứt giữa chừng."""
    root = _hf_repo_dir(snapshot) or snapshot
    try:
        return next(root.rglob("*.incomplete"), None) is not None
    except OSError:
        return False


def _clear_incomplete(snapshot: Path) -> None:
    """Xoá dấu vết `*.incomplete` còn sót, KHÔNG đụng tới file mô hình đã tải xong."""
    root = _hf_repo_dir(snapshot) or snapshot
    try:
        leftovers = list(root.rglob("*.incomplete"))
    except OSError:
        return
    for item in leftovers:
        try:
            item.unlink(missing_ok=True)
        except OSError:
            continue


def _purge_model(snapshot: Path) -> None:
    """Xoá bản mô hình hỏng để lần tải sau bắt đầu sạch sẽ.

    Chỉ đụng vào thư mục cache của **đúng** mô hình đó, không đụng thư mục cha chứa các
    mô hình khác — người dùng có thể đã tải sẵn mô hình khác và không đáng mất chúng.
    """
    target = _hf_repo_dir(snapshot) or snapshot
    try:
        shutil.rmtree(target, ignore_errors=True)
    except OSError:
        pass  # xoá không được thì lượt tải sau vẫn thử tải tiếp, không tệ hơn hiện tại


def model_status(model: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tình trạng một mô hình trên máy này — cho tab Cài đặt hiện “đã tải rồi / chưa tải”.

    Không gọi mạng, không nạp mô hình, nên gọi được thẳng từ web UI mà không làm treo
    giao diện. Trả về::

        {"id": "large-v3-turbo", "label": "Cân bằng (khuyên dùng)",
         "eta_40min": "~10-15 phút", "download_size": "~1.6 GB", "size_mb": 1620,
         "downloaded": True, "problem": None, "path": "/…/snapshots/…"}

    `problem` là câu tiếng Việt ngắn nói vì sao chưa dùng được (`None` = dùng được).
    """
    asr_cfg: dict[str, Any] = dict((cfg or {}).get("asr") or {})
    # Hỏi về CHÍNH mô hình được nêu tên, nên bỏ qua thư mục mô hình mà cấu hình đang ép.
    for key in ("model_dir", "model_path", "local_model"):
        asr_cfg.pop(key, None)
    asr_cfg["model"] = str(model or asr_cfg.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL

    entry = _model_entry(asr_cfg, asr_cfg["model"])
    info: dict[str, Any] = {
        "id": _model_key(asr_cfg["model"]),
        "label": str(entry.get("label") or ""),
        "eta_40min": str(entry.get("eta_40min") or ""),
        "download_size": str(entry.get("download_size") or ""),
        "size_mb": _model_size_mb(asr_cfg, asr_cfg["model"]),
        "downloaded": False,
        "problem": "chưa tải về máy",
        "path": None,
    }
    try:
        whisper = _import_faster_whisper()
    except AsrError as err:
        info["problem"] = err.user_message.split("\n", 1)[0]
        return info

    model_spec, download_root = _resolve_model_location(asr_cfg)
    if download_root is None:
        path: Path | None = Path(model_spec)
    else:
        download_model = getattr(whisper, "download_model", None)
        path = (
            _cached_snapshot(download_model, model_spec, download_root)
            if download_model is not None
            else None
        )

    problem = _integrity_problem(path)
    info["path"] = str(path) if path is not None else None
    info["problem"] = problem
    info["downloaded"] = problem is None
    return info


def _dir_size(path: Path) -> int:
    """Tổng dung lượng các file trong thư mục, bỏ qua file vừa bị xoá giữa chừng.

    Bỏ qua liên kết tượng trưng: cache của `huggingface_hub` trên macOS để file thật ở
    `blobs/` rồi trỏ `snapshots/` vào đó bằng symlink, đếm cả hai thì phần trăm tải
    nhảy lên gấp đôi và người dùng thấy "đang tải 190%".
    """
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_symlink() or not entry.is_file():
                    continue
                total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _load_model(
    whisper: Any,
    model_spec: str,
    device: str,
    compute_type: str,
    threads: int,
    download_root: Path | None,
    report: Callable[[str, float], None],
) -> Any:
    """Nạp model vào bộ nhớ.

    RAM đỉnh đo thật với int8: `large-v3-turbo` ~1545MB, `large-v3` ~2953MB. Máy đích có
    32GB nên cả hai đều thoải mái; con số này chỉ đáng nhớ khi ai đó định chạy trên máy 8GB.
    """
    report(
        f"Đang nạp mô hình “{_model_key(model_spec)}” vào bộ nhớ "
        f"({threads} luồng CPU, chế độ {compute_type})…",
        _at(_P_LOAD, 0.2),
    )
    kwargs: dict[str, Any] = {
        "device": device,
        "compute_type": compute_type,
        "cpu_threads": threads,
        "num_workers": 1,
    }
    if download_root is not None:
        kwargs["download_root"] = str(download_root)
    try:
        model = whisper.WhisperModel(model_spec, **kwargs)
    except MemoryError as err:
        raise AsrError(
            "Máy không đủ bộ nhớ để nạp mô hình.\n"
            "Cách sửa: đóng bớt ứng dụng đang mở, hoặc vào Cài đặt chọn mô hình nhẹ hơn "
            "(“Nhanh, độ chính xác vừa” hoặc “Thử nhanh”).",
            detail=str(err),
        ) from err
    except Exception as err:
        raise AsrError(
            f"Không nạp được mô hình “{model_spec}”.\n"
            "Cách sửa: bấm đúp CaiDat.command để cài lại, hoặc xoá thư mục mô hình đã tải "
            "rồi bấm Bắt đầu để tải lại từ đầu.",
            detail=f"{type(err).__name__}: {err}",
        ) from err
    report("Đã nạp xong mô hình.", _at(_P_LOAD, 1.0))
    return model


# --------------------------------------------------------------------------- #
# Prompt mồi
# --------------------------------------------------------------------------- #

def _build_prompt(ctx: Context, asr_cfg: dict[str, Any]) -> str:
    """Prompt mồi = đoạn mẫu trong cấu hình + tên riêng của phim (nếu đã có `names.json`).

    Đây là đòn bẩy rẻ nhất để cải thiện chất lượng: Whisper bám theo phong cách, dấu câu
    và **chính tả tên riêng** trong đoạn mồi. Vòng chạy thứ hai (sau khi S7/T1 đã tìm ra
    tên nhân vật) vì thế nghe đúng tên hơn hẳn vòng đầu.

    Prompt bị cắt cho vừa ngữ cảnh mồi của Whisper (~224 token); cắt ở đây để biết chỗ
    mà cắt, thay vì để thư viện cắt cụt giữa một cái tên.
    """
    base = str(asr_cfg.get("initial_prompt") or "").strip()
    limit = int(_num(asr_cfg.get("prompt_max_chars"), PROMPT_MAX_CHARS)) or PROMPT_MAX_CHARS

    names = _load_names(ctx)
    if not names:
        return base[:limit]

    room = limit - len(base) - 1
    if room <= 2:
        return base[:limit]

    kept: list[str] = []
    used = 0
    for name in names:
        cost = len(name) + (1 if kept else 0)
        if used + cost > room:
            break
        kept.append(name)
        used += cost
    if not kept:
        return base[:limit]
    return f"{base}{'、'.join(kept)}。"[:limit]


def _load_names(ctx: Context) -> list[str]:
    """Đọc bảng tên riêng của phim; thiếu file là chuyện bình thường, trả danh sách rỗng.

    Chấp nhận nhiều hình dạng JSON vì bảng này vừa do S7/T1 sinh ra vừa do người dùng
    sửa tay trong web UI: `{"秋楠": "Qiūnán"}`, `["秋楠"]`, `{"names": [...]}`,
    hoặc danh sách các đối tượng có khoá `zh`.
    """
    cfg = ctx.cfg or {}
    paths = cfg.get("paths") or {}
    names_cfg = cfg.get("names") or {}
    base = str(paths.get("names_dir") or "").strip()
    folder = Path(base).expanduser() if base else ctx.work_dir
    path = folder / str(names_cfg.get("file") or "names.json")
    try:
        if not path.is_file():
            return []
        data = read_json(path)
    except (OSError, ValueError):
        return []
    return _extract_names(data)


def _extract_names(data: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen and not text.isascii():
            seen.add(text)
            out.append(text)

    if isinstance(data, dict):
        inner = data.get("names")
        if isinstance(inner, (dict, list)):
            return _extract_names(inner)
        for key in data:
            add(key)
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                add(item)
            elif isinstance(item, dict):
                for key in ("zh", "han", "name", "word", "text"):
                    if item.get(key):
                        add(item[key])
                        break
    return out


# --------------------------------------------------------------------------- #
# Gỡ băng
# --------------------------------------------------------------------------- #

def _transcribe(
    ctx: Context,
    model: Any,
    audio_path: Path,
    asr_cfg: dict[str, Any],
    prompt: str,
    duration_hint: float,
    eta_factor: float,
    report: Callable[[str, float], None],
) -> tuple[list[dict[str, Any]], float, bool]:
    """Chạy nhận dạng và trả về (danh sách segment, thời lượng audio, có bị huỷ không).

    `model.transcribe()` trả về một generator: công việc nặng chỉ xảy ra khi lặp qua nó.
    Nhờ vậy vừa lặp vừa báo tiến trình được, và bấm Dừng thì dừng ngay ở segment kế tiếp
    chứ không phải chờ hết bài.
    """
    kwargs = _transcribe_kwargs(asr_cfg, prompt)
    try:
        try:
            segments_iter, info = model.transcribe(str(audio_path), **kwargs)
        except TypeError:
            # Một phiên bản faster-whisper khác đổi tên tham số: bỏ những tham số nó
            # không nhận rồi chạy lại, thay vì báo cho người dùng một lỗi mà họ không
            # sửa được. Các tham số sống còn (ngôn ngữ, mốc thời gian từng chữ) luôn có.
            segments_iter, info = model.transcribe(str(audio_path), **_supported(model, kwargs))
    except Exception as err:
        raise AsrError(
            "Không đọc được file âm thanh để gỡ băng.\n"
            "Cách sửa: chạy lại từ đầu để tool tạo lại file âm thanh, hoặc bấm “Kiểm tra máy” "
            "trong tab Cài đặt để xem ffmpeg đã có chưa.",
            detail=f"{type(err).__name__}: {err}",
        ) from err

    duration = _num(getattr(info, "duration", 0.0)) or duration_hint
    started = time.monotonic()
    last_report = 0.0
    last_autosave = started
    processed = 0.0

    segments: list[dict[str, Any]] = []
    cancelled = False
    try:
        for segment in segments_iter:
            segments.append(_segment_dict(segment))
            processed = max(processed, _num(getattr(segment, "end", 0.0)))
            now = time.monotonic()

            # Kiểm huỷ SAU MỖI segment: đây là chỗ duy nhất trong chặng mà quyền điều
            # khiển quay về tay ta, mỗi vòng lặp cách nhau vài giây.
            if ctx.is_cancelled():
                cancelled = True
                break

            if now - last_report >= REPORT_EVERY:
                last_report = now
                report(
                    _progress_message(processed, duration, now - started, eta_factor),
                    _at(_P_WORK, processed / duration if duration > 0 else 0.0),
                )
            if now - last_autosave >= AUTOSAVE_EVERY:
                last_autosave = now
                _save_partial(ctx, {"segments": segments, "meta": {"duration": duration}})
    except AsrCancelled:
        raise
    except Exception as err:
        _save_partial(ctx, {"segments": segments, "meta": {"duration": duration}})
        raise AsrError(
            "Việc gỡ băng dừng giữa chừng vì một lỗi kỹ thuật.\n"
            f"Phần đã nghe được ({len(segments)} đoạn) đã được giữ lại tại:\n"
            f"{_partial_path(ctx)}\n"
            "Hãy bấm Bắt đầu để chạy lại; nếu vẫn lỗi, thử chọn mô hình nhẹ hơn "
            "(“Nhanh, độ chính xác vừa”) trong tab Cài đặt.",
            detail=f"{type(err).__name__}: {err}",
        ) from err

    return segments, duration, cancelled


def _transcribe_kwargs(asr_cfg: dict[str, Any], prompt: str) -> dict[str, Any]:
    """Tham số cho `transcribe()`, lấy từ cấu hình nhưng ép cứng vài thứ sống còn.

    `word_timestamps` bị ép `True` bất kể cấu hình: chặng S4 chia cue hoàn toàn dựa vào
    mốc thời gian từng chữ, tắt nó đi thì cả pipeline phía sau vô nghĩa mà lỗi lại chỉ
    lộ ra sau nửa tiếng.
    """
    kwargs: dict[str, Any] = {
        "language": str(asr_cfg.get("language") or "zh"),
        "task": "transcribe",
        "beam_size": max(1, int(_num(asr_cfg.get("beam_size"), 5))),
        "word_timestamps": True,
        "vad_filter": bool(asr_cfg.get("vad_filter", True)),
        "condition_on_previous_text": bool(asr_cfg.get("condition_on_previous_text", False)),
        "temperature": _num(asr_cfg.get("temperature"), 0.0),
        "no_speech_threshold": _num(asr_cfg.get("no_speech_threshold"), 0.6),
    }
    if prompt:
        kwargs["initial_prompt"] = prompt
    silence_ms = int(_num(asr_cfg.get("vad_min_silence_ms"), 0))
    if kwargs["vad_filter"] and silence_ms > 0:
        kwargs["vad_parameters"] = {"min_silence_duration_ms": silence_ms}
    return kwargs


def _supported(model: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Lọc bỏ tham số mà `transcribe()` của phiên bản đang cài không nhận."""
    import inspect  # noqa: PLC0415 - chỉ cần khi đã có sự cố, đừng bắt mọi lần chạy trả giá

    try:
        params = inspect.signature(model.transcribe).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in params}


def _segment_dict(segment: Any) -> dict[str, Any]:
    """Đổi segment của faster-whisper thành dict thuần để ghi JSON.

    Làm tròn về mili giây vì `.srt` chỉ có mili giây — giữ 6 chữ số thập phân chỉ làm
    file phình to mà không thêm thông tin nào.
    """
    words: list[dict[str, Any]] = []
    for word in getattr(segment, "words", None) or []:
        words.append(
            {
                "start": _ms(getattr(word, "start", 0.0)),
                "end": _ms(getattr(word, "end", 0.0)),
                # Giữ nguyên văn, không strip: mốc thời gian gắn với đúng chuỗi này.
                "word": str(getattr(word, "word", "") or ""),
                "probability": round(_num(getattr(word, "probability", 0.0)), 4),
            }
        )

    out: dict[str, Any] = {
        "start": _ms(getattr(segment, "start", 0.0)),
        "end": _ms(getattr(segment, "end", 0.0)),
        "text": str(getattr(segment, "text", "") or "").strip(),
        "words": words,
    }
    # Ba chỉ số chẩn đoán để S3 nhận ra đoạn ảo giác (whisper "nghe" ra chữ ở đoạn nhạc):
    # đoạn ảo giác thường có avg_logprob rất thấp hoặc no_speech_prob rất cao.
    for key in ("avg_logprob", "no_speech_prob", "compression_ratio"):
        value = getattr(segment, key, None)
        if isinstance(value, (int, float)) and math.isfinite(float(value)):
            out[key] = round(float(value), 4)
    return out


def _ms(value: Any) -> float:
    return round(_num(value), 3)


def _build_meta(
    *,
    ctx: Context,
    asr_cfg: dict[str, Any],
    model_spec: str,
    device: str,
    compute_type: str,
    threads: int,
    elapsed: float,
    duration: float,
    eta_factor: float,
    prompt: str,
    audio_path: Path,
    segments: list[dict[str, Any]],
    partial: bool,
) -> dict[str, Any]:
    """Phần `meta` của `S2_asr.json` — vừa để báo cáo, vừa để hiệu chỉnh ETA lần sau."""
    return {
        "model": model_spec,
        "model_id": _model_key(model_spec),
        "device": device,
        "compute_type": compute_type,
        "threads": threads,
        "elapsed": round(elapsed, 1),
        # rtf = số giây máy chạy cho mỗi giây audio. Trên iMac 2017 int8: large-v3 ~0.8,
        # large-v3-turbo ~0.3. Đây là con số nên dùng để hiệu chỉnh lại bảng eta_factor
        # trong `config/default.yaml` khi có số đo thật từ máy người dùng.
        "rtf": round(elapsed / duration, 3) if duration > 0 else None,
        "eta_factor": round(eta_factor, 3),
        "duration": round(duration, 3),
        "language": str(asr_cfg.get("language") or "zh"),
        "beam_size": max(1, int(_num(asr_cfg.get("beam_size"), 5))),
        "vad_filter": bool(asr_cfg.get("vad_filter", True)),
        "word_timestamps": True,
        "initial_prompt": prompt,
        "audio": str(audio_path),
        "segments": len(segments),
        "words": sum(len(s.get("words") or []) for s in segments),
        "video_id": ctx.video_id,
        "partial": partial,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _remember(ctx: Context, meta: dict[str, Any]) -> None:
    """Đẩy vài con số sang `ctx.meta` để chặng sau và bản báo cáo khỏi mở lại file.

    `duration` của S0 (thời lượng video) **không bị ghi đè**: S8 dùng nó để đặt tên và
    báo cáo, còn con số đo được ở đây là thời lượng file âm thanh, hai thứ có thể lệch
    vài giây và không nên âm thầm hoán đổi cho nhau.
    """
    for key in ("model", "device", "compute_type", "threads", "elapsed", "rtf", "duration"):
        value = meta.get(key)
        if value is not None:
            ctx.meta[f"asr_{key}"] = value
    if not _num(ctx.meta.get("duration")) and _num(meta.get("duration")) > 0:
        ctx.meta["duration"] = meta["duration"]


# --------------------------------------------------------------------------- #
# Câu chữ tiếng Việt
# --------------------------------------------------------------------------- #

def _progress_message(processed: float, duration: float, elapsed: float, eta_factor: float) -> str:
    """Câu báo tiến trình: "Đang gỡ băng phút 12/40 — còn khoảng 21 phút".

    Hệ số dùng để ước lượng được **đo từ chính lần chạy này** ngay khi đã nghe đủ
    `MEASURE_AFTER` giây audio; trước đó dùng hệ số khởi tạo của mô hình đang chạy
    (xem `_eta_factor`). Máy mỗi người một khác, nên con số đo thật bao giờ cũng đúng
    hơn con số ghi sẵn — đây cũng là lý do đổi mô hình mặc định không làm hỏng ước lượng.
    """
    minute = int(processed // 60) + 1
    if duration <= 0:
        return f"Đang gỡ băng phút thứ {minute}…"

    total = max(1, int(round(duration / 60)))
    factor = elapsed / processed if processed >= MEASURE_AFTER and processed > 0 else eta_factor
    factor = min(10.0, max(0.05, factor))
    remaining = max(0.0, duration - processed) * factor
    return f"Đang gỡ băng phút {min(minute, total)}/{total} — {_eta_phrase(remaining)}"


def _eta_phrase(seconds: float) -> str:
    if seconds < 30:
        return "sắp xong"
    return f"còn khoảng {_clock(seconds)}"


def _clock(seconds: float) -> str:
    """Thời lượng bằng lời: "45 giây", "21 phút", "1 giờ 5 phút"."""
    seconds = max(0.0, _num(seconds))
    if seconds < 60:
        return f"{int(round(seconds))} giây"
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"{max(1, minutes)} phút"
    hours, rest = divmod(minutes, 60)
    return f"{hours} giờ" if rest == 0 else f"{hours} giờ {rest} phút"


def _size(num_bytes: float) -> str:
    """Dung lượng bằng lời, ưu tiên GB vì model tính bằng GB."""
    mb = max(0.0, _num(num_bytes)) / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"
