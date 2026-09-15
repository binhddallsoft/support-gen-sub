"""Mười chặng của pipeline, mỗi chặng một file, cùng chữ ký `run(ctx, on_progress)`.

Re-export ở đây được nạp **lười** (PEP 562), giống `srtgen.core`. Lý do bắt buộc:
`s2_asr` kéo theo `faster_whisper` và `s5_tokenize` kéo theo `jieba`/`pypinyin`. Nếu
`import srtgen.stages` nạp cả mười module thì một thư viện còn thiếu sẽ làm chết
`srtgen doctor` và web UI — đúng hai thứ mà người dùng cần dùng để biết mình đang
thiếu thư viện gì.

Cách dùng vẫn như re-export tĩnh:

    from srtgen.stages import s0_fetch, FetchError
"""

from __future__ import annotations

from typing import Any

#: Tên module theo số chặng — dùng cho `srtgen resume <id> --from s5` và cho web UI.
STAGE_MODULES: dict[int, str] = {
    0: "srtgen.stages.s0_fetch",
    1: "srtgen.stages.s1_audio",
    2: "srtgen.stages.s2_asr",
    3: "srtgen.stages.s3_cleanup",
    4: "srtgen.stages.s4_cue",
    5: "srtgen.stages.s5_tokenize",
    6: "srtgen.stages.s6_normalize",
    7: "srtgen.stages.s7_ai",
    8: "srtgen.stages.s8_translate",
    9: "srtgen.stages.s9_emit",
}

#: Tên tiếng Việt của từng chặng, chép nguyên văn bảng ở build-spec-v2 mục 1.
#: Đây là **nguồn duy nhất** của tên bước: `pipeline.Stage` và web UI đều lấy từ
#: đây, nên sửa tên ở một chỗ là đủ. Chữ “(lâu nhất)” không nằm trong bảng vì
#: `Stage.title` tự dán nó vào theo cờ `slow`.
STAGE_LABELS: dict[int, str] = {
    0: "Tải video và tách âm thanh",
    1: "Xử lý âm thanh",
    2: "Nghe và gỡ băng",
    3: "Dọn kết quả gỡ băng",
    4: "Chia câu theo nhịp thoại",
    5: "Tách cụm và sinh pinyin",
    6: "Chuẩn hoá định dạng",
    7: "Nhờ AI soát tên riêng và chữ khó",
    8: "Dịch sang tiếng Việt",
    9: "Xuất file",
}

_EXPORTS: dict[str, str] = {
    # tên module, để `from srtgen.stages import s0_fetch` vẫn chạy
    "s0_fetch": "srtgen.stages.s0_fetch",
    "s1_audio": "srtgen.stages.s1_audio",
    # lỗi và tiện ích dùng chung, đặt ở S0 (xem docstring của module đó)
    "FetchError": "srtgen.stages.s0_fetch",
    "StageCancelled": "srtgen.stages.s0_fetch",
    "FIX_ACTIONS": "srtgen.stages.s0_fetch",
    "LEGAL_NOTICE": "srtgen.stages.s0_fetch",
    "which_tool": "srtgen.stages.s0_fetch",
    "ffmpeg_path": "srtgen.stages.s0_fetch",
    "ffprobe_path": "srtgen.stages.s0_fetch",
    "ytdlp_path": "srtgen.stages.s0_fetch",
    "ytdlp_command": "srtgen.stages.s0_fetch",
    "ytdlp_version": "srtgen.stages.s0_fetch",
    "probe_duration": "srtgen.stages.s0_fetch",
    "probe_audio_info": "srtgen.stages.s0_fetch",
    "run_ffmpeg": "srtgen.stages.s0_fetch",
    "human_duration": "srtgen.stages.s0_fetch",
    "Progress": "srtgen.stages.s0_fetch",
    "has_demucs": "srtgen.stages.s1_audio",
}

__all__ = [*sorted(_EXPORTS), "STAGE_MODULES", "STAGE_LABELS"]


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(module_name)
    return module if module_name.endswith(name) else getattr(module, name)


def __dir__() -> list[str]:
    return __all__
