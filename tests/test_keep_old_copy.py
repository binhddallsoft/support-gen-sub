"""Một quy ước "cất bản cũ trước khi ghi đè" (hợp đồng H1) cho cả tool.

Người dùng không phải dân IT. File tool ghi ra (``.srt``, ``_vi.srt``, ``.ass``…)
chính là file họ mở ra sửa tay; ghi đè im lặng là mất một buổi công. Trước đợt
này quy ước đó có BA bản cài riêng — ``cli._keep_old_copy`` (đánh số ``-1``),
helper riêng của S9 (đánh số ``-2``, còn chép dự phòng khi không đổi tên được),
và ``s5_tokenize._keep_hand_edits`` chép sang thư mục ``ban-sua-tay-cu/`` — nên
một lần chạy lại vì bảng tên đổi để lại HAI bản của cùng một lần sửa tay.

Nay chỉ còn ``io_utils.keep_old_copy``. File này kiểm:

1. hàm dùng chung đủ mọi ca: chưa có file, nội dung giống hệt (so byte), khác
   nội dung, trùng giây, tên bị chiếm sẵn, không đổi tên được;
2. S9 và lệnh CLI đi qua đúng hàm đó, và các bản cài riêng đã bị xoá.

Phần máy chủ web nằm ở ``tests/test_web_keep_old_copy.py``.
Ngoại tuyến hoàn toàn: không mạng, không model, không mở cửa sổ.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from srtgen import cli, io_utils
from srtgen.stages import s5_tokenize, s9_emit
from tests.test_emit_backups import HAND_BLOCK_ZH, first_export, hand_edit, rerun_s9

MOMENT = datetime(2026, 9, 10, 18, 30, 0)
STAMP = "20260910-183000"

SRT = "1\n00:00:01,000 --> 00:00:02,000\n你 好。\nNǐ hǎo。\n"


def names_in(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


# --------------------------------------------------------------------------- #
# 1. io_utils.keep_old_copy
# --------------------------------------------------------------------------- #

def test_missing_file_is_left_alone(tmp_path: Path) -> None:
    target = tmp_path / "Phim_vi.srt"
    assert io_utils.keep_old_copy(target, "mới", now=MOMENT) is None
    assert io_utils.keep_old_copy(target) is None
    assert names_in(tmp_path) == []


def test_a_folder_is_not_a_file_to_keep(tmp_path: Path) -> None:
    (tmp_path / "Phim.srt").mkdir()
    assert io_utils.keep_old_copy(tmp_path / "Phim.srt", now=MOMENT) is None
    assert names_in(tmp_path) == ["Phim.srt"]


def test_identical_text_is_not_copied(tmp_path: Path) -> None:
    target = tmp_path / "Phim.srt"
    io_utils.write_text(target, SRT)
    before = target.read_bytes()

    assert io_utils.keep_old_copy(target, SRT, now=MOMENT) is None
    assert target.read_bytes() == before, "Không được đụng vào file."
    assert names_in(tmp_path) == ["Phim.srt"]


def test_identical_bytes_are_not_copied(tmp_path: Path) -> None:
    target = tmp_path / "Phim.bundle.json"
    io_utils.write_text(target, '{"a": 1}', bom=False)
    data = io_utils.encode_text('{"a": 1}', bom=False)

    assert io_utils.keep_old_copy(target, data, now=MOMENT) is None
    assert io_utils.keep_old_copy(target, bytearray(data), now=MOMENT) is None
    assert names_in(tmp_path) == ["Phim.bundle.json"]


def test_text_is_compared_as_the_bytes_that_would_be_written(tmp_path: Path) -> None:
    """``str`` được mã hoá như ``write_text`` mặc định (có BOM, LF) rồi mới so byte."""
    target = tmp_path / "Phim.srt"
    io_utils.write_text(target, SRT, bom=False)  # trên đĩa: không BOM
    kept = io_utils.keep_old_copy(target, SRT, now=MOMENT)  # sắp ghi: có BOM → khác byte
    assert kept is not None and kept.read_bytes() == SRT.encode("utf-8")

    # Chỗ gọi ghi với lựa chọn khác thì đưa đúng byte của nó: giống hệt → không cất.
    io_utils.write_text(target, SRT, bom=False, newline="\r\n")
    wanted = io_utils.encode_text(SRT, bom=False, newline="\r\n")
    assert io_utils.keep_old_copy(target, wanted, now=MOMENT) is None


def test_encode_text_is_exactly_what_write_text_writes(tmp_path: Path) -> None:
    """Một nguồn duy nhất cho byte trên đĩa — so sánh của keep_old_copy dựa vào đó."""
    nfd = unicodedata.normalize("NFD", "Tiếng Việt")
    text = f"1\r\n00:00:01,000 --> 00:00:02,000\r{nfd}\n你 好。"
    number = 0
    for bom in (True, False):
        for newline in ("\n", "\r\n"):
            for trailing in (True, False):
                number += 1
                path = tmp_path / f"f{number}.srt"
                io_utils.write_text(path, text, bom=bom, newline=newline, trailing_newline=trailing)
                assert path.read_bytes() == io_utils.encode_text(
                    text, bom=bom, newline=newline, trailing_newline=trailing
                ), (bom, newline, trailing)


def test_different_content_is_renamed_byte_for_byte(tmp_path: Path) -> None:
    """Đổi tên chứ không chép: giữ nguyên từng byte, kể cả BOM, CRLF, khoảng trắng thừa."""
    target = tmp_path / "Phim_vi.srt"
    raw = "﻿1\r\n00:00:01,000 --> 00:00:02,000\r\nCâu tôi tự dịch  \r\n".encode("utf-8")
    target.write_bytes(raw)

    kept = io_utils.keep_old_copy(target, "bản máy tạo", now=MOMENT)

    assert kept == tmp_path / f"Phim_vi.truoc-{STAMP}.srt"
    assert kept.read_bytes() == raw
    assert not target.exists(), "Chỗ cũ để trống cho người gọi ghi bản mới."


def test_no_new_content_means_always_keep(tmp_path: Path) -> None:
    target = tmp_path / "Phim.srt"
    io_utils.write_text(target, SRT)
    kept = io_utils.keep_old_copy(target, now=MOMENT)
    assert kept is not None and kept.name == f"Phim.truoc-{STAMP}.srt"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Phim_vi.srt", f"Phim_vi.truoc-{STAMP}.srt"),
        ("Tập 1. Phim.srt", f"Tập 1. Phim.truoc-{STAMP}.srt"),
        ("Phim_song-ngu.ass", f"Phim_song-ngu.truoc-{STAMP}.ass"),
        ("Phim.bundle.json", f"Phim.bundle.truoc-{STAMP}.json"),
        ("ghi-chu", f"ghi-chu.truoc-{STAMP}"),
    ],
)
def test_kept_name_keeps_the_last_suffix(tmp_path: Path, name: str, expected: str) -> None:
    target = tmp_path / name
    io_utils.write_text(target, "cũ")
    assert io_utils.kept_copy_path(target, MOMENT).name == expected, "Dự báo tên trước khi cất."
    kept = io_utils.keep_old_copy(target, "mới", now=MOMENT)
    assert kept is not None and kept.name == expected
    assert kept.parent == tmp_path


def test_same_second_gets_dash_one_then_dash_two(tmp_path: Path) -> None:
    target = tmp_path / "Phim.srt"
    kept: list[Path] = []
    for text in ("một", "hai", "ba"):
        io_utils.write_text(target, text)
        copy = io_utils.keep_old_copy(target, "mới", now=MOMENT)
        assert copy is not None
        kept.append(copy)

    assert [p.name for p in kept] == [
        f"Phim.truoc-{STAMP}.srt",
        f"Phim.truoc-{STAMP}-1.srt",
        f"Phim.truoc-{STAMP}-2.srt",
    ]
    assert [io_utils.read_text(p) for p in kept] == ["một\n", "hai\n", "ba\n"]


def test_an_existing_copy_is_never_replaced(tmp_path: Path) -> None:
    target = tmp_path / "Phim.srt"
    io_utils.write_text(target, "bản đang có")
    older = tmp_path / f"Phim.truoc-{STAMP}.srt"
    io_utils.write_text(older, "bản cất từ trước")

    kept = io_utils.keep_old_copy(target, now=MOMENT)

    assert kept is not None and kept.name == f"Phim.truoc-{STAMP}-1.srt"
    assert io_utils.read_text(older) == "bản cất từ trước\n"
    assert io_utils.read_text(kept) == "bản đang có\n"


def test_a_name_taken_during_the_rename_moves_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows: tên bị chiếm giữa lúc kiểm và lúc đổi tên → ``FileExistsError`` → số kế."""
    target = tmp_path / "Phim.srt"
    io_utils.write_text(target, "cũ")
    real_rename = Path.rename

    def racing(self: Path, other: Any) -> Any:
        if Path(other).name == f"Phim.truoc-{STAMP}.srt":
            raise FileExistsError("tên vừa bị chiếm")
        return real_rename(self, other)

    monkeypatch.setattr(Path, "rename", racing)
    kept = io_utils.keep_old_copy(target, now=MOMENT)
    assert kept is not None and kept.name == f"Phim.truoc-{STAMP}-1.srt"


def test_refused_rename_raises_a_vietnamese_oserror_and_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "Phim_vi.srt"
    io_utils.write_text(target, "phần tôi đã sửa tay")
    before = target.read_bytes()

    def refuse(self: Path, other: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "rename", refuse)
    with pytest.raises(OSError) as info:
        io_utils.keep_old_copy(target, "bản mới", now=MOMENT)

    err = info.value
    assert isinstance(err, io_utils.KeepCopyError)
    message = str(err)
    assert "Không cất được bản cũ" in message and "KHÔNG ghi đè" in message
    assert "Phim_vi.srt" in message and "Aegisub" in message
    assert err.path == target
    assert target.read_bytes() == before
    assert names_in(tmp_path) == ["Phim_vi.srt"]


def test_the_stamp_defaults_to_the_clock(tmp_path: Path) -> None:
    target = tmp_path / "Phim.srt"
    io_utils.write_text(target, "cũ")
    kept = io_utils.keep_old_copy(target, "mới")
    assert kept is not None
    assert re.fullmatch(r"Phim\.truoc-\d{8}-\d{6}(-\d+)?\.srt", kept.name), kept.name


def test_is_kept_copy() -> None:
    assert io_utils.is_kept_copy(f"Phim_vi.truoc-{STAMP}.srt")
    assert io_utils.is_kept_copy(Path("out") / f"Phim.truoc-{STAMP}-2.srt")
    assert not io_utils.is_kept_copy("Phim_vi.srt")
    assert not io_utils.is_kept_copy(Path("truoc-gio") / "Phim.srt")


# --------------------------------------------------------------------------- #
# 2. mọi chỗ ghi file kết quả đi qua đúng hàm đó
# --------------------------------------------------------------------------- #

def test_the_private_copies_of_the_convention_are_gone() -> None:
    assert not hasattr(cli, "_keep_old_copy")
    assert not hasattr(cli, "_same_as_on_disk")
    assert not hasattr(cli, "KEPT_COPY_TAG")
    assert not hasattr(s5_tokenize, "_keep_hand_edits")
    assert not hasattr(s5_tokenize, "BACKUP_DIR")
    assert "BACKUP_DIR" not in s5_tokenize.__all__
    assert not hasattr(s9_emit._OutputWriter, "_set_aside")
    assert not hasattr(s9_emit, "shutil"), "S9 không còn chép dự phòng — đổi tên không được thì dừng."
    assert s9_emit.BACKUP_TAG == io_utils.KEPT_COPY_WORD


class Spy:
    """Bọc ``io_utils.keep_old_copy`` thật và nhớ nó được gọi cho file nào."""

    def __init__(self) -> None:
        self.real = io_utils.keep_old_copy
        self.names: list[str] = []

    def __call__(self, path: Any, new_content: Any = None, *, now: Any = None) -> Any:
        self.names.append(Path(path).name)
        return self.real(path, new_content, now=now)


@pytest.fixture
def export_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Như cấu hình của ``tests/test_emit_backups.py``: không cache dịch, có thêm ``.ass``."""
    out = dict(cfg)
    out["translate"] = {**dict(cfg.get("translate") or {}), "cache": False}
    out["emit"] = {**dict(cfg.get("emit") or {}), "ass_export": True}
    return out


def test_s9_sets_aside_through_io_utils(
    tmp_path: Path, export_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _ctx, first = first_export(tmp_path, export_config)
    srt = Path(first["srt"])
    hand_edit(srt, HAND_BLOCK_ZH)
    hand_bytes = srt.read_bytes()

    spy = Spy()
    monkeypatch.setattr(io_utils, "keep_old_copy", spy)
    second = rerun_s9(tmp_path, export_config)

    assert srt.name in spy.names
    (copy,) = [Path(p) for p in second["backups"] if Path(p).suffix == ".srt" and "_vi" not in Path(p).name]
    assert copy.read_bytes() == hand_bytes
    assert copy.name.startswith(f"{srt.stem}.truoc-")


typer_testing = pytest.importorskip("typer.testing")

BILINGUAL = (
    "1\n00:00:01,000 --> 00:00:02,000\n你 去 哪儿？\nnǐ qù nǎr？\nCậu đi đâu vậy?\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\n我 回 家。\nwǒ huí jiā。\nTớ về nhà.\n\n"
)


def cli_fix(*args: Any) -> Any:
    return typer_testing.CliRunner().invoke(cli.app, ["fix", *map(str, args)])


def test_cli_fix_sets_aside_through_io_utils(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "Phim.srt"
    io_utils.write_text(src, BILINGUAL)
    assert cli_fix(src).exit_code == 0

    vi = tmp_path / "Phim.fixed_vi.srt"
    io_utils.write_text(vi, io_utils.read_text(vi).replace("Tớ về nhà.", "Tớ về nhà đây."))
    hand_bytes = vi.read_bytes()

    spy = Spy()
    monkeypatch.setattr(io_utils, "keep_old_copy", spy)
    res = cli_fix(src)

    assert res.exit_code == 0, res.output
    assert "Phim.fixed_vi.srt" in spy.names
    (copy,) = sorted(p for p in tmp_path.iterdir() if io_utils.is_kept_copy(p))
    assert re.fullmatch(r"Phim\.fixed_vi\.truoc-\d{8}-\d{6}\.srt", copy.name), copy.name
    assert copy.read_bytes() == hand_bytes


def test_cli_fix_stops_without_overwriting_when_the_copy_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "Phim.srt"
    io_utils.write_text(src, BILINGUAL)
    assert cli_fix(src).exit_code == 0

    vi = tmp_path / "Phim.fixed_vi.srt"
    io_utils.write_text(vi, io_utils.read_text(vi).replace("Tớ về nhà.", "Tớ về nhà đây."))
    hand_bytes = vi.read_bytes()
    real_rename = Path.rename

    def locked(self: Path, other: Any) -> Any:
        if self.name == vi.name:
            raise PermissionError("file đang mở trong Aegisub")
        return real_rename(self, other)

    monkeypatch.setattr(Path, "rename", locked)
    res = cli_fix(src)

    assert res.exit_code != 0
    assert vi.read_bytes() == hand_bytes, "Không cất được thì KHÔNG ghi đè."
    assert not [p for p in tmp_path.iterdir() if io_utils.is_kept_copy(p)]
