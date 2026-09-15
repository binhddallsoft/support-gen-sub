"""Tầng lõi: mô hình dữ liệu, renderer, validator, các luật tiếng Trung, bối cảnh chạy.

Re-export ở đây được nạp **lười** (PEP 562). Lý do: `srtgen doctor` và web UI phải
khởi động được trên máy chưa cài đủ thư viện, mà `rules.py` cần `pypinyin`, `sandhi`
cần bảng âm… Nếu `import srtgen.core` kéo theo cả tầng lõi thì một thư viện thiếu là
chết luôn đúng cái lệnh dùng để phát hiện ra thiếu thư viện.

Dùng bình thường như re-export tĩnh:

    from srtgen.core import Token, render_zh, load_config

Module không tự import gì; mỗi tên chỉ nạp module chứa nó, đúng lúc được gọi tới.
"""

from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, str] = {
    # token.py — mô hình dữ liệu và renderer
    "KIND_WORD": "srtgen.core.token",
    "KIND_PUNCT": "srtgen.core.token",
    "KIND_MARKER": "srtgen.core.token",
    "FLAG_HETERONYM": "srtgen.core.token",
    "FLAG_CASE_AMBIG": "srtgen.core.token",
    "FLAG_AI_APPLIED": "srtgen.core.token",
    "FLAG_NAME": "srtgen.core.token",
    "Token": "srtgen.core.token",
    "Cue": "srtgen.core.token",
    "Document": "srtgen.core.token",
    "render_tokens": "srtgen.core.token",
    "render_zh": "srtgen.core.token",
    "render_py": "srtgen.core.token",
    # srt.py — đọc/ghi SRT
    "parse_timestamp_line": "srtgen.core.srt",
    "format_timestamp": "srtgen.core.srt",
    "parse_srt": "srtgen.core.srt",
    "parse_srt_document": "srtgen.core.srt",
    "emit_srt": "srtgen.core.srt",
    "tokenize_line": "srtgen.core.srt",
    "merge_zh_py": "srtgen.core.srt",
    # rules.py — validator theo README
    "Finding": "srtgen.core.rules",
    "validate_document": "srtgen.core.rules",
    "validate_text": "srtgen.core.rules",
    # các luật tiếng Trung
    "apply_sandhi": "srtgen.core.sandhi",
    "merge_erhua": "srtgen.core.erhua",
    "erhua_pinyin": "srtgen.core.erhua",
    "ERHUA_HEAD": "srtgen.core.erhua",
    "apply_casing": "srtgen.core.casing",
    "CASE_ENDERS": "srtgen.core.casing",
    "CASE_CONT": "srtgen.core.casing",
    "CASE_AMBIG": "srtgen.core.casing",
    # context.py — bối cảnh chạy và cấu hình
    "Context": "srtgen.core.context",
    "new_context": "srtgen.core.context",
    "load_config": "srtgen.core.context",
    "deep_merge": "srtgen.core.context",
    "make_video_id": "srtgen.core.context",
    "extract_youtube_id": "srtgen.core.context",
    "user_data_dir": "srtgen.core.context",
    "user_cache_dir": "srtgen.core.context",
    "DEFAULT_CONFIG_PATH": "srtgen.core.context",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_name), name)


def __dir__() -> list[str]:
    return __all__
