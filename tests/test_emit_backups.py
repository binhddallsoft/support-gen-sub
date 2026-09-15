"""Chạy lại không bao giờ ghi đè mất bản người dùng đã sửa tay (hợp đồng H1, H2).

Người dùng không phải dân IT. Họ sửa phụ đề cả buổi trong trình sửa hoặc trong
Aegisub, rồi bấm chạy lại vì vừa điền tên nhân vật — và S9 ghi đè đúng những file
đó, không một lời báo. Mất một buổi công như thế là lý do để bỏ hẳn phần mềm.

Mọi bài ở đây dùng ``Context`` thật trong thư mục tạm và chạy thật S5 → S6 → S8
(dịch giả, không mạng) → S9, rồi sửa tay file trên đĩa như người dùng làm.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from srtgen import io_utils
from srtgen.providers.translate import NullTranslator
from srtgen.stages import s5_tokenize, s6_normalize, s8_translate, s9_emit
from tests.test_pipeline_offline import make_context

VIDEO_ID = "emit_backups"
TITLE = "Phim"

CUES = [
    {"index": 1, "start": 0.0, "end": 1.5, "text": "我来中国只有一个目的。"},
    {"index": 2, "start": 1.5, "end": 3.0, "text": "光头强来了！"},
    {"index": 3, "start": 3.0, "end": 4.5, "text": "我们走吧。"},
]

#: Thời điểm trong tên bản cất: ``YYYYmmdd-HHMMSS``, thêm ``-2``… khi trùng giây.
STAMP = r"\d{8}-\d{6}(?:-\d+)?"

#: Một khối người dùng tự gõ thêm — thứ phải còn nguyên sau khi chạy lại.
HAND_BLOCK_ZH = "\n99\n00:00:05,000 --> 00:00:06,000\n手改的\nshǒu gǎi de\n"
HAND_BLOCK_VI = "\n99\n00:00:05,000 --> 00:00:06,000\nCâu tôi tự dịch cả buổi\n"


@pytest.fixture
def config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Cấu hình mặc định, tắt cache dịch, bật thêm file .ass để kiểm đủ mọi loại file."""
    out = dict(cfg)
    out["translate"] = {**dict(cfg.get("translate") or {}), "cache": False}
    out["emit"] = {**dict(cfg.get("emit") or {}), "ass_export": True}
    return out


def new_run(tmp_path: Path, config: dict[str, Any], *, force: bool = False, created: str = "") -> Any:
    """Một lần chạy cùng phim: Context mới, cùng thư mục làm việc và thư mục kết quả.

    ``force=True`` là "chạy lại" thật: S9 bị ép ghi file (như khi bảng tên đổi
    hoặc bấm chạy lại), không dùng lại kết quả cũ. ``created`` giả giờ bắt đầu
    chạy, thứ luôn khác nhau giữa hai lần chạy thật.
    """
    settings = dict(config)
    if force:
        settings["force_from"] = s9_emit.STAGE_NUMBER
    ctx = make_context(tmp_path, settings, video_id=VIDEO_ID)
    ctx.meta["title"] = TITLE
    if created:
        ctx.meta["created_at"] = created
    if not ctx.has_stage(4, "cues"):
        ctx.save_stage(4, "cues", {"meta": {"title": TITLE}, "cues": CUES})
    return ctx


def first_export(tmp_path: Path, config: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    ctx = new_run(tmp_path, config, created="2026-09-10T18:00:00+07:00")
    s5_tokenize.run(ctx)
    s6_normalize.run(ctx)
    s8_translate.run(ctx, translator=NullTranslator())
    result = s9_emit.run(ctx)
    assert result["vi_srt"], "Cần có file tiếng Việt để kiểm cả hai file giao đi."
    return ctx, result


def rerun_s9(tmp_path: Path, config: dict[str, Any], created: str = "2026-09-10T19:30:00+07:00") -> dict[str, Any]:
    return s9_emit.run(new_run(tmp_path, config, force=True, created=created))


def hand_edit(path: Path, extra: str) -> str:
    """Sửa tay như trong trình sửa: đổi một chữ và gõ thêm một khối. Trả nội dung trên đĩa."""
    text = io_utils.read_text(path)
    edited = text.replace("我们", "咱们", 1) + extra
    io_utils.write_text(path, edited, bom=True)
    return io_utils.read_text(path)


def backups_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if f".{s9_emit.BACKUP_TAG}-" in p.name)


# --------------------------------------------------------------------------- #
# H1 — cất bản cũ trước khi ghi đè
# --------------------------------------------------------------------------- #

def test_hand_edits_survive_a_rerun_in_truoc_files(tmp_path: Path, config: dict[str, Any]) -> None:
    _ctx, first = first_export(tmp_path, config)
    srt, vi = Path(first["srt"]), Path(first["vi_srt"])
    assert first["backups"] == [], "Lần xuất đầu tiên không có gì để cất."
    machine_srt, machine_vi = io_utils.read_text(srt), io_utils.read_text(vi)

    edited_srt = hand_edit(srt, HAND_BLOCK_ZH)
    edited_vi = hand_edit(vi, HAND_BLOCK_VI)

    second = rerun_s9(tmp_path, config)

    kept = [Path(p) for p in second["backups"]]
    names = sorted(p.name for p in kept)
    assert len(kept) == 2, f"Đúng hai file bị sửa tay thì cất đúng hai bản, được: {names}"
    (srt_copy,) = [p for p in kept if re.fullmatch(rf"{TITLE}\.truoc-{STAMP}\.srt", p.name)]
    (vi_copy,) = [p for p in kept if re.fullmatch(rf"{TITLE}_vi\.truoc-{STAMP}\.srt", p.name)]
    assert not any(p.name.endswith("_vi.srt") for p in kept), (
        "Giữ nguyên hậu tố: Phim_vi.truoc-….srt, KHÔNG phải Phim.truoc-…_vi.srt."
    )
    assert srt_copy.parent == srt.parent and vi_copy.parent == vi.parent, "Bản cất nằm cùng thư mục."

    assert io_utils.read_text(srt_copy) == edited_srt, "Bản cất phải là đúng chữ người dùng đã gõ."
    assert io_utils.read_text(vi_copy) == edited_vi
    assert io_utils.read_text(srt) == machine_srt, "File chính là bản máy vừa tạo."
    assert io_utils.read_text(vi) == machine_vi
    assert set(backups_in(srt.parent)) == set(kept), "Kết quả phải liệt kê mọi bản đã cất."


def test_identical_rerun_sets_nothing_aside(tmp_path: Path, config: dict[str, Any]) -> None:
    """Nội dung y hệt → không cất gì, kể cả bundle và báo cáo vốn chứa giờ chạy."""
    _ctx, first = first_export(tmp_path, config)
    folder = Path(first["srt"]).parent

    again = rerun_s9(tmp_path, config)
    assert again["backups"] == []
    assert backups_in(folder) == []

    hand_edit(Path(first["srt"]), HAND_BLOCK_ZH)
    after_edit = rerun_s9(tmp_path, config, created="2026-09-10T20:00:00+07:00")
    assert len(after_edit["backups"]) == 1
    before = backups_in(folder)

    third = rerun_s9(tmp_path, config, created="2026-09-10T21:00:00+07:00")
    assert third["backups"] == [], "Chạy lần nữa với nội dung y hệt thì không được sinh thêm bản cất."
    assert backups_in(folder) == before


def test_every_kind_of_output_is_protected(tmp_path: Path, config: dict[str, Any]) -> None:
    """Không riêng .srt: .ass, _song-ngu.ass, bundle, báo cáo cũng được cất khi khác."""
    _ctx, first = first_export(tmp_path, config)
    for key in ("ass", "bilingual_ass", "bundle", "report"):
        assert first[key], key
        path = Path(first[key])
        io_utils.write_text(path, io_utils.read_text(path) + "\n; sửa tay\n", bom=False)

    second = rerun_s9(tmp_path, config)
    names = sorted(Path(p).name for p in second["backups"])
    expected = {
        rf"{TITLE}\.truoc-{STAMP}\.ass",
        rf"{TITLE}_song-ngu\.truoc-{STAMP}\.ass",
        rf"{TITLE}\.bundle\.truoc-{STAMP}\.json",
        rf"{TITLE}\.report\.truoc-{STAMP}\.html",
    }
    for pattern in expected:
        assert any(re.fullmatch(pattern, n) for n in names), (pattern, names)
    assert len(names) == len(expected)


def test_backups_in_the_same_second_never_overwrite_each_other(
    tmp_path: Path, config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(s9_emit, "backup_stamp", lambda: "20260910-183000")
    _ctx, first = first_export(tmp_path, config)
    srt = Path(first["srt"])

    first_edit = hand_edit(srt, HAND_BLOCK_ZH)
    rerun_s9(tmp_path, config)
    second_edit = hand_edit(srt, "\n100\n00:00:07,000 --> 00:00:08,000\n又改\nyòu gǎi\n")
    rerun_s9(tmp_path, config)

    one = srt.parent / f"{TITLE}.truoc-20260910-183000.srt"
    two = srt.parent / f"{TITLE}.truoc-20260910-183000-1.srt"
    assert io_utils.read_text(one) == first_edit, "Bản cất đầu tiên không được bị ghi đè."
    assert io_utils.read_text(two) == second_edit


def test_backup_impossible_means_no_overwrite(
    tmp_path: Path, config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Không đổi tên, không chép được bản cũ → DỪNG, file người dùng giữ nguyên."""
    _ctx, first = first_export(tmp_path, config)
    srt = Path(first["srt"])
    edited = hand_edit(srt, HAND_BLOCK_ZH)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError("file đang mở trong Aegisub")

    monkeypatch.setattr(Path, "rename", refuse)
    with pytest.raises(s9_emit.BackupError) as info:
        rerun_s9(tmp_path, config)

    assert "KHÔNG ghi đè" in str(info.value)
    assert io_utils.read_text(srt) == edited


def test_failed_write_puts_the_old_file_back(
    tmp_path: Path, config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Đã dời bản cũ đi mà ghi bản mới hỏng → bản cũ về lại đúng tên cũ."""
    _ctx, first = first_export(tmp_path, config)
    srt = Path(first["srt"])
    edited = hand_edit(srt, HAND_BLOCK_ZH)

    real_write = io_utils.atomic_write_text

    def disk_full(path: Any, text: str, **kwargs: Any) -> None:
        if Path(path) == srt:
            raise OSError("ổ đĩa đầy")
        real_write(path, text, **kwargs)

    monkeypatch.setattr(io_utils, "atomic_write_text", disk_full)
    with pytest.raises(OSError):
        rerun_s9(tmp_path, config)

    assert io_utils.read_text(srt) == edited
    assert not list(srt.parent.glob(f"{TITLE}.truoc-*.srt"))


def test_reusing_the_old_result_never_touches_a_hand_edit(tmp_path: Path, config: dict[str, Any]) -> None:
    """Chạy lại KHÔNG ép (dùng kết quả cũ) thì không ghi gì, và không báo cất gì."""
    _ctx, first = first_export(tmp_path, config)
    srt = Path(first["srt"])
    edited = hand_edit(srt, HAND_BLOCK_ZH)

    again = s9_emit.run(new_run(tmp_path, config))
    assert again["backups"] == []
    assert io_utils.read_text(srt) == edited


def test_backup_name_keeps_the_suffix() -> None:
    folder = Path("out")
    assert s9_emit.backup_path_for(folder / "Phim_vi.srt", "20260910-183000").name == (
        "Phim_vi.truoc-20260910-183000.srt"
    )
    assert s9_emit.backup_path_for(folder / "Tập 1. Phim.srt", "20260910-183000").name == (
        "Tập 1. Phim.truoc-20260910-183000.srt"
    )


# --------------------------------------------------------------------------- #
# H2 — edit_original.json làm mới mỗi lần xuất
# --------------------------------------------------------------------------- #

def test_export_refreshes_the_machine_version_for_revert(tmp_path: Path, config: dict[str, Any]) -> None:
    ctx, first = first_export(tmp_path, config)
    srt, vi = Path(first["srt"]), Path(first["vi_srt"])
    original = ctx.work_dir / s9_emit.ORIGINAL_FILE

    payload = io_utils.read_json(original)
    assert {"version", "saved_at", "srt_path", "srt", "vi_path", "vi"} <= set(payload)
    assert payload["srt_path"] == str(srt) and payload["vi_path"] == str(vi)
    assert payload["srt"] == io_utils.read_text(srt)
    assert payload["vi"] == io_utils.read_text(vi)

    # Máy chủ đã chụp một bản máy tạo CŨ hơn, rồi người dùng sửa tay.
    io_utils.write_json(original, {**payload, "srt": "BẢN MÁY TẠO CŨ", "vi": "CŨ"})
    edited = hand_edit(srt, HAND_BLOCK_ZH)

    rerun_s9(tmp_path, config)
    fresh = io_utils.read_json(original)
    assert fresh["srt"] == io_utils.read_text(srt), "Hoàn nguyên phải về bản máy tạo MỚI."
    assert fresh["srt"] not in ("BẢN MÁY TẠO CŨ", edited)
    assert fresh["vi"] == io_utils.read_text(vi)


def test_original_file_name_matches_the_server() -> None:
    pytest.importorskip("fastapi", reason="Chưa cài fastapi — không nạp được máy chủ web.")
    from srtgen.web import server

    assert server.ORIGINAL_FILE == s9_emit.ORIGINAL_FILE
