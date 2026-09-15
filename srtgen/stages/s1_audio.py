"""S1 — tiền xử lý âm thanh: `audio.wav` → `audio_norm.wav`.

Chặng này cố tình làm **ít**. Ba lý do:

* **VAD không làm ở đây.** `docs/plan.md` mục S1 chốt rằng việc cắt khoảng lặng dùng
  `vad_filter=True` tích hợp sẵn của faster-whisper ở chặng S2. Tự cắt trước bằng
  ffmpeg sẽ làm lệch mốc thời gian, mà mốc thời gian chính là thứ S4 dựa vào để chia
  cue — hỏng ở đây thì hỏng cả file .srt.

* **`loudnorm` chạy một lượt, không hai lượt.** Bản hai lượt (đo trước, sửa sau) chính
  xác hơn về LUFS nhưng phải giải mã toàn bộ file thêm một lần: với video 40 phút trên
  iMac 2017 là mất trắng vài phút cho một khác biệt mà mô hình ASR không phân biệt nổi.
  Việc cần ở đây chỉ là kéo mức âm thanh về một dải ổn định.

* **`demucs` mặc định TẮT.** Tách giọng khỏi nhạc nền giúp ích thật với phim nhiều
  nhạc, nhưng trên CPU nó có thể lâu hơn cả chặng gỡ băng. Bật mà máy chưa cài thì
  chặng này **cảnh báo rồi đi tiếp**, không bao giờ làm hỏng lượt chạy: người dùng
  thà có phụ đề hơi kém còn hơn không có gì sau 40 phút chờ.

Khi cả hai bước đều tắt, chặng này **không chép file**: `audio_norm.wav` chỉ là một
khái niệm, còn `S1_audio.json` trỏ thẳng về `audio.wav`. Chép thừa một file 80MB
không đem lại gì ngoài việc làm đầy ổ đĩa.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from srtgen.stages.s0_fetch import (
    FIX_CHECK_FILE,
    FetchError,
    Progress,
    ProgressFn,
    StageCancelled,
    check_cancelled,
    diagnose_error,
    human_duration,
    probe_audio_info,
    probe_duration,
    run_ffmpeg,
    stream_process,
    which_tool,
)

if TYPE_CHECKING:  # chỉ để gợi ý kiểu
    from srtgen.core.context import Context

__all__ = [
    "STAGE",
    "STAGE_NAME",
    "STATUS_DONE",
    "STATUS_SKIPPED",
    "STATUS_OFF",
    "demucs_command",
    "has_demucs",
    "run",
]

STAGE = 1
STAGE_NAME = "audio"

#: Trạng thái của một bước trong `S1_audio.json` — report và UI đọc đúng ba chuỗi này.
STATUS_DONE = "done"        # đã chạy
STATUS_SKIPPED = "skipped"  # có bật nhưng không chạy được (thiếu công cụ, lỗi nhẹ)
STATUS_OFF = "off"          # người dùng tắt trong cấu hình

DEMUCS_STEM = "vocals"


# --------------------------------------------------------------------------- #
# demucs
# --------------------------------------------------------------------------- #

def demucs_command() -> list[str] | None:
    """Câu lệnh gọi demucs dạng list, hoặc `None` nếu máy chưa cài.

    Thử bản CLI trước, rồi tới `python -m demucs` của chính venv đang chạy: cài bằng
    `pip install demucs` không phải lúc nào cũng sinh ra file thực thi trên PATH.
    """
    exe = which_tool("demucs")
    if exe:
        return [exe]
    try:
        import importlib.util

        if importlib.util.find_spec("demucs") is not None:
            return [sys.executable, "-m", "demucs"]
    except (ImportError, ValueError):  # pragma: no cover - máy lạ
        pass
    return None


def has_demucs() -> bool:
    """Cho `srtgen doctor` trả lời được câu "bật tách nhạc nền có chạy được không"."""
    return demucs_command() is not None


# --------------------------------------------------------------------------- #
# Chặng S1
# --------------------------------------------------------------------------- #

def run(ctx: "Context", on_progress: ProgressFn | None = None) -> dict[str, Any]:
    """Chuẩn hoá âm thanh cho chặng gỡ băng và ghi `S1_audio.json`.

    Trả về nội dung `S1_audio.json`; chặng S2 chỉ cần đọc khoá `audio_path`.

    Chạy tiếp giữa chừng: giống S0, kết quả cũ chỉ được dùng lại khi file âm thanh nó
    trỏ tới vẫn còn trên đĩa — file JSON nhẹ nên sống sót qua mọi lần dọn ổ đĩa, còn
    file wav thì không.
    """
    report = Progress(on_progress)

    cached = _reuse_previous(ctx)
    if cached is not None:
        report("Đã có sẵn âm thanh đã xử lý từ lần chạy trước.", 1.0)
        ctx.meta["audio_path"] = cached.get("audio_path", "")
        return cached

    source = _input_audio(ctx)
    cfg = _audio_cfg(ctx)
    duration = probe_duration(source)
    steps: list[dict[str, str]] = []
    warnings: list[str] = []

    check_cancelled(ctx)

    # --- bước 1: tách giọng khỏi nhạc nền (tuỳ chọn, mặc định tắt) ---------- #
    vocals: Path | None = None
    if _truthy(cfg.get("demucs")):
        vocals = _run_demucs(ctx, source, cfg, report.sub(0.02, 0.75), steps, warnings)
    else:
        steps.append(
            {
                "name": "demucs",
                "status": STATUS_OFF,
                "detail": "Tách giọng khỏi nhạc nền đang tắt (mặc định, vì rất nặng trên CPU).",
            }
        )

    check_cancelled(ctx)
    stage_input = vocals or source

    # --- bước 2: chuẩn hoá âm lượng ---------------------------------------- #
    loudnorm = _truthy(cfg.get("loudnorm", True))
    if not loudnorm and vocals is None:
        # Không có gì phải làm: đừng chép một file 80MB chỉ để đổi tên nó.
        steps.append(
            {
                "name": "loudnorm",
                "status": STATUS_OFF,
                "detail": "Chuẩn hoá âm lượng đang tắt; dùng thẳng file gốc.",
            }
        )
        output = stage_input
        report("Không cần xử lý thêm, dùng thẳng âm thanh đã tải.", 0.98)
    else:
        output = _run_ffmpeg_stage(
            ctx,
            stage_input,
            cfg,
            duration,
            loudnorm=loudnorm,
            report=report.sub(0.75 if vocals is not None else 0.05, 0.98),
            steps=steps,
        )

    info = _describe(source, output, steps, warnings, fallback_duration=duration)
    ctx.save_stage(STAGE, STAGE_NAME, info)
    ctx.meta["audio_path"] = info["audio_path"]
    report(
        f"Âm thanh đã sẵn sàng ({human_duration(info['duration'])}).",
        1.0,
    )
    return info


# --------------------------------------------------------------------------- #
# Đầu vào / đầu ra
# --------------------------------------------------------------------------- #

def _reuse_previous(ctx: "Context") -> dict[str, Any] | None:
    if not ctx.has_stage(STAGE, STAGE_NAME):
        return None
    saved = ctx.load_stage(STAGE, STAGE_NAME)
    if not isinstance(saved, dict):
        return None
    return saved if _usable_file(saved.get("audio_path")) else None


def _usable_file(value: Any) -> bool:
    if not value:
        return False
    path = Path(str(value))
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _input_audio(ctx: "Context") -> Path:
    """Lấy file âm thanh của chặng S0.

    Ưu tiên đường dẫn ghi trong `S0_info.json` (S0 có thể đã phải cứu file bằng tên
    khác), rồi mới tới `work/<id>/audio.wav` cho trường hợp file JSON bị mất mà file
    âm thanh vẫn còn — chạy lại được thì đừng bắt tải lại.
    """
    saved = ctx.load_stage(0, "info")
    if isinstance(saved, dict) and _usable_file(saved.get("audio_path")):
        return Path(str(saved["audio_path"]))

    fallback = ctx.work_dir / "audio.wav"
    if _usable_file(fallback):
        return fallback

    raise FetchError(
        "Chưa có file âm thanh để xử lý — bước tải nguồn chưa chạy xong.\n"
        "Cách xử lý: bấm Bắt đầu lại từ đầu. Tool sẽ dùng lại mọi kết quả cũ còn dùng "
        "được nên sẽ không mất thời gian làm lại phần đã xong.",
        fix_action=FIX_CHECK_FILE,
    )


def _audio_cfg(ctx: "Context") -> dict[str, Any]:
    cfg = ctx.cfg.get("audio")
    return cfg if isinstance(cfg, dict) else {}


def _truthy(value: Any, default: bool = False) -> bool:
    """Cấu hình do người dùng sửa tay: `"false"` trong YAML lỏng vẫn phải hiểu là tắt."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on", "1"}
    return bool(value)


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _timeout(cfg: dict[str, Any]) -> float | None:
    """`audio.timeout` giây; thiếu khoá hoặc `0` = **chờ vô hạn**, và đó là mặc định.

    Cố ý không đặt hạn cho chặng này, khác hẳn S0. `demucs` trên CPU của iMac 2017 có
    thể chạy lâu hơn một tiếng cho video 40 phút: một cái hạn đặt bừa sẽ giết đúng
    những lượt chạy đang làm việc tử tế. Ở đây người dùng đã có nút Dừng, còn ở S0 thì
    tiến trình treo vì mạng chết mới là thứ không tự thoát ra được.
    """
    value = _number(cfg.get("timeout"), 0.0)
    return value if value > 0 else None


def _describe(
    source: Path,
    output: Path,
    steps: list[dict[str, str]],
    warnings: list[str],
    *,
    fallback_duration: float,
) -> dict[str, Any]:
    info = probe_audio_info(output)
    duration = float(info.get("duration") or fallback_duration or 0.0)
    return {
        "audio_path": str(output),
        "input_path": str(source),
        "duration": round(duration, 3),
        "sample_rate": int(info.get("sample_rate") or 0),
        "channels": int(info.get("channels") or 0),
        "steps": steps,
        "warnings": warnings,
        "processed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


# --------------------------------------------------------------------------- #
# Bước tách giọng
# --------------------------------------------------------------------------- #

def _run_demucs(
    ctx: "Context",
    source: Path,
    cfg: dict[str, Any],
    report: Progress,
    steps: list[dict[str, str]],
    warnings: list[str],
) -> Path | None:
    """Chạy demucs, trả về file giọng đã tách — hoặc `None` và **đi tiếp**.

    Mọi nhánh hỏng ở đây đều kết thúc bằng `None` chứ không phải ngoại lệ (trừ khi
    người dùng bấm Dừng). Tách nhạc nền là bước làm-cho-tốt-hơn; để nó đánh sập một
    lượt chạy đã tải xong 80MB audio là đổi sai thứ lấy sai thứ.
    """
    cmd = demucs_command()
    if cmd is None:
        message = (
            "Bạn đã bật “tách giọng khỏi nhạc nền” nhưng máy chưa cài demucs. "
            "Tool sẽ bỏ qua bước này và vẫn chạy tiếp bình thường.\n"
            "Muốn dùng, hãy mở Terminal và chạy: pip install demucs"
        )
        report(message, 1.0)
        warnings.append(message)
        steps.append({"name": "demucs", "status": STATUS_SKIPPED, "detail": message})
        return None

    model = str(cfg.get("demucs_model") or "htdemucs")
    out_dir = ctx.work_dir / "demucs"
    args = [
        *cmd,
        "--two-stems", DEMUCS_STEM,
        "-n", model,
        "-d", "cpu",              # máy đích không có GPU NVIDIA, đừng để demucs tự dò
        "-o", str(out_dir),
        str(source),
    ]

    report(
        "Đang tách giọng khỏi nhạc nền. Bước này rất lâu trên máy không có card đồ hoạ "
        "rời — có thể lâu hơn cả bước gỡ băng.",
        0.0,
    )

    def _on_line(line: str) -> None:
        percent = _percent_in(line)
        if percent is not None:
            report(f"Đang tách giọng khỏi nhạc nền — {int(percent)}%", percent / 100.0)

    try:
        code, output = stream_process(
            args, ctx=ctx, on_line=_on_line, timeout=_timeout(cfg)
        )
    except StageCancelled:
        raise
    except FetchError as err:
        message = (
            "Không chạy được demucs nên tool bỏ qua bước tách nhạc nền và đi tiếp.\n"
            f"Lý do: {err.user_message.splitlines()[0]}"
        )
        report(message, 1.0)
        warnings.append(message)
        steps.append({"name": "demucs", "status": STATUS_SKIPPED, "detail": message})
        return None

    if code != 0:
        detail = diagnose_error(
            output,
            default_message="demucs kết thúc với lỗi.",
            default_fix=FIX_CHECK_FILE,
        ).detail
        message = (
            "Bước tách nhạc nền chạy lỗi nên tool bỏ qua và đi tiếp với âm thanh gốc. "
            "Kết quả gỡ băng vẫn dùng được, chỉ là có thể kém chính xác hơn ở đoạn "
            "nhiều nhạc."
        )
        report(message, 1.0)
        warnings.append(message)
        steps.append(
            {"name": "demucs", "status": STATUS_SKIPPED, "detail": f"{message}\n{detail}"}
        )
        return None

    vocals = _find_vocals(out_dir)
    if vocals is None:
        message = (
            "demucs chạy xong nhưng không tìm thấy file giọng đã tách; tool đi tiếp với "
            "âm thanh gốc."
        )
        report(message, 1.0)
        warnings.append(message)
        steps.append({"name": "demucs", "status": STATUS_SKIPPED, "detail": message})
        return None

    steps.append(
        {
            "name": "demucs",
            "status": STATUS_DONE,
            "detail": f"Đã tách giọng bằng mô hình {model}.",
        }
    )
    return vocals


def _find_vocals(out_dir: Path) -> Path | None:
    """Tìm `vocals.*` trong cây thư mục demucs tạo ra.

    Không đoán đường dẫn `<out>/<model>/<tên file>/vocals.wav`: tên thư mục con lấy
    theo tên file nguồn, mà tên đó có thể chứa dấu tiếng Việt đã bị demucs chuẩn hoá
    khác đi. Quét thư mục thì đúng trong mọi trường hợp.
    """
    if not out_dir.is_dir():
        return None
    found = [p for p in out_dir.rglob(f"{DEMUCS_STEM}.*") if p.is_file() and p.stat().st_size > 0]
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_size)


def _percent_in(line: str) -> float | None:
    """Bóc số phần trăm ra khỏi thanh tiến trình kiểu tqdm của demucs (`" 42%|███…"`)."""
    index = line.find("%")
    if index <= 0:
        return None
    start = index
    while start > 0 and (line[start - 1].isdigit() or line[start - 1] == "."):
        start -= 1
    if start == index:
        return None
    try:
        value = float(line[start:index])
    except ValueError:
        return None
    return value if 0.0 <= value <= 100.0 else None


# --------------------------------------------------------------------------- #
# Bước chuẩn hoá âm lượng
# --------------------------------------------------------------------------- #

def _run_ffmpeg_stage(
    ctx: "Context",
    source: Path,
    cfg: dict[str, Any],
    duration: float,
    *,
    loudnorm: bool,
    report: Progress,
    steps: list[dict[str, str]],
) -> Path:
    """Một lượt ffmpeg duy nhất: (tuỳ chọn) loudnorm + ép lại 16kHz mono.

    Gộp hai việc vào một lượt vì đằng nào cũng phải giải mã cả file: bản demucs trả về
    là 44.1kHz stereo nên bắt buộc phải chuyển lại, còn loudnorm chỉ là một filter
    thêm vào cùng lượt đó. Chạy tách ra là giải mã file 40 phút hai lần.
    """
    fetch_cfg = ctx.cfg.get("fetch") or {}
    sample_rate = int(_number(fetch_cfg.get("sample_rate"), 16000))
    channels = int(_number(fetch_cfg.get("channels"), 1))
    target = ctx.work_dir / "audio_norm.wav"

    args: list[str] = ["-i", str(source), "-vn"]
    if loudnorm:
        target_i = _number(cfg.get("loudnorm_i"), -16.0)
        target_tp = _number(cfg.get("loudnorm_tp"), -1.5)
        target_lra = _number(cfg.get("loudnorm_lra"), 11.0)
        args += ["-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"]
        label = "Đang chuẩn hoá âm lượng"
        detail = f"loudnorm I={target_i} TP={target_tp} LRA={target_lra} (một lượt)"
    else:
        label = "Đang chuẩn hoá âm thanh"
        detail = "Chuẩn hoá âm lượng đang tắt."
    args += ["-ac", str(channels), "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(target)]

    run_ffmpeg(
        args,
        ctx=ctx,
        report=report,
        label=label,
        total=duration,
        timeout=_timeout(cfg),
    )

    if not _usable_file(target):
        raise FetchError(
            "Không tạo được file âm thanh đã chuẩn hoá.\n"
            "Cách xử lý: kiểm tra ổ đĩa còn chỗ trống rồi chạy lại. Nếu vẫn lỗi, hãy tắt "
            "mục “Chuẩn hoá âm lượng” trong Cài đặt nâng cao — bước này không bắt buộc.",
            fix_action=FIX_CHECK_FILE,
        )

    steps.append(
        {
            "name": "loudnorm",
            "status": STATUS_DONE if loudnorm else STATUS_OFF,
            "detail": detail,
        }
    )
    if not loudnorm:
        # Lượt ffmpeg này vẫn chạy thật (để ép bản demucs 44.1kHz stereo về 16kHz mono),
        # nên phải ghi thành một bước riêng — nếu không report sẽ nói dối là "không làm gì".
        steps.append(
            {
                "name": "resample",
                "status": STATUS_DONE,
                "detail": f"Ép về {sample_rate}Hz, {channels} kênh sau khi tách giọng.",
            }
        )
    _cleanup_demucs(ctx, cfg)
    return target


def _cleanup_demucs(ctx: "Context", cfg: dict[str, Any]) -> None:
    """Xoá bản tách giọng thô sau khi đã ép vào `audio_norm.wav`.

    demucs ghi ra wav 44.1kHz stereo — cỡ 400MB cho một video 40 phút — mà từ lúc này
    trở đi không ai đọc nữa: `S1_audio.json` đã trỏ sang file kết quả nên lần chạy tiếp
    theo cũng không cần. Giữ lại bằng `audio.keep_demucs: true` khi cần soi chất lượng tách.
    """
    if _truthy(cfg.get("keep_demucs")):
        return
    out_dir = ctx.work_dir / "demucs"
    if not out_dir.is_dir():
        return
    try:
        shutil.rmtree(out_dir)
    except OSError:  # dọn dẹp là việc phụ, hỏng thì thôi
        pass
