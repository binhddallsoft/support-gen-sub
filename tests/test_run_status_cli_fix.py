"""Nhật ký chạy nói THẬT, và ``srtgen fix`` ra cùng cặp file với web mà không đè bản sửa tay.

Hai lỗi cùng một họ "người dùng bị nói sai về chính file của họ":

1. ``pipeline._run_stage`` quyết định "Dùng lại" TRƯỚC khi gọi chặng (chỉ nhìn
   file điểm lưu có trên đĩa hay không). Khi S5/S8 thấy bảng tên riêng đổi và tự
   làm lại, nhật ký vẫn ghi "dùng lại kết quả cũ" — người dùng tưởng tên vừa gõ
   không được áp và đi sửa tay từng câu. Trạng thái giờ lấy từ việc chặng THẬT
   SỰ đã làm.
2. ``srtgen fix`` gọi ``run_fix`` nên file có dòng tiếng Việt không được ghi
   ``_vi.srt``, còn bị thêm lỗi ``BLOCK_SHAPE`` và thoát mã 1 — trong khi web
   ghi kèm ``_vi.srt``. Kèm theo: chạy lại lệnh thì file đích đã có (thường là
   bản người dùng vừa sửa tay) bị ghi đè im lặng; nay bản cũ được cất thành
   ``<stem>.truoc-<thời điểm><đuôi>``.

Context trong thư mục tạm, không mạng, không model, không mở cửa sổ nào.
"""

from __future__ import annotations

import re
import types
from pathlib import Path
from typing import Any, Callable

import pytest

from srtgen import io_utils, pipeline
from srtgen.core.srt import parse_srt
from srtgen.stages import s5_tokenize, s7_ai
from tests.test_pipeline_offline import make_context


# --------------------------------------------------------------------------- #
# 1. trạng thái chặng
# --------------------------------------------------------------------------- #

def run_one(ctx: Any, number: int) -> tuple[Any, str, list[Any]]:
    """Chạy đúng một chặng qua ``_run_stage``: ``(kết quả, trạng thái, sự kiện)``."""
    stage = pipeline.stage_by_number(number)
    events: list[Any] = []
    tracker = pipeline._Tracker([stage], None, events.append)
    result, info = pipeline._run_stage(ctx, stage, tracker, lap=1)
    return result, info.status, events


@pytest.fixture
def fake_stage(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[..., Any]], None]:
    """Thay module của chặng bằng một hàm ``run`` giả — không jieba, không mạng."""

    def install(run: Callable[..., Any]) -> None:
        module = types.SimpleNamespace(run=run)
        monkeypatch.setattr(pipeline.Stage, "load", lambda self: module)

    return install


def seeded(tmp_path: Path, cfg: dict[str, Any]) -> Any:
    """Context có sẵn một kết quả S5 cũ trên đĩa."""
    ctx = make_context(tmp_path, cfg, video_id="status")
    ctx.save_stage(5, "tokens", {"cues": [], "names_hash": ""})
    return ctx


def test_untouched_saved_result_is_reported_reused(
    tmp_path: Path, cfg: dict[str, Any], fake_stage: Callable
) -> None:
    ctx = seeded(tmp_path, cfg)
    fake_stage(lambda ctx, report: {"cues": []})
    _, status, events = run_one(ctx, 5)
    assert status == "reused"
    assert events[-1].phase == "reused"


def test_stage_that_schedules_its_own_redo_is_reported_done(
    tmp_path: Path, cfg: dict[str, Any], fake_stage: Callable
) -> None:
    """Đúng cơ chế của S5/S8 khi bảng tên đổi: ``redo_from_stage`` hạ ``force_from``.

    Chặng giả cố ý KHÔNG ghi file: riêng việc tự xếp lịch làm lại đã phải đủ để
    nhật ký thôi nói "dùng lại".
    """
    ctx = seeded(tmp_path, cfg)

    def run(ctx: Any, report: Callable) -> dict[str, Any]:
        assert s5_tokenize.redo_from_stage(ctx, 5, report, s5_tokenize.NAMES_CHANGED_MESSAGE)
        return {"cues": []}

    fake_stage(run)
    _, status, events = run_one(ctx, 5)
    assert status == "done"
    assert events[-1].phase == "done"
    assert not any(e.phase == "reused" for e in events)


def test_stage_that_rewrites_its_file_is_reported_done(
    tmp_path: Path, cfg: dict[str, Any], fake_stage: Callable
) -> None:
    ctx = seeded(tmp_path, cfg)

    def run(ctx: Any, report: Callable) -> None:
        ctx.save_stage(5, "tokens", {"cues": [{"index": 1, "tokens": []}], "names_hash": "abc"})

    fake_stage(run)
    _, status, _ = run_one(ctx, 5)
    assert status == "done"


@pytest.mark.parametrize(("flag", "want"), [(True, "reused"), (False, "done")])
def test_result_that_says_whether_it_resumed_is_believed(
    tmp_path: Path, cfg: dict[str, Any], fake_stage: Callable, flag: bool, want: str
) -> None:
    """S7 và S8 tự khai ``resumed``; lời khai của chính chặng là bằng chứng mạnh nhất."""
    ctx = make_context(tmp_path, cfg, video_id="status")
    ctx.save_stage(8, "translate", {"lines": []})
    fake_stage(lambda ctx, report: {"resumed": flag})
    _, status, _ = run_one(ctx, 8)
    assert status == want


def test_no_saved_result_is_always_done(
    tmp_path: Path, cfg: dict[str, Any], fake_stage: Callable
) -> None:
    ctx = make_context(tmp_path, cfg, video_id="status")
    fake_stage(lambda ctx, report: {"resumed": True})
    _, status, _ = run_one(ctx, 5)
    assert status == "done"


CUES = [
    {"index": 1, "start": 0.0, "end": 1.5, "text": "光头强来了。"},
    {"index": 2, "start": 1.5, "end": 3.0, "text": "我们走吧。"},
]


def test_real_s5_redo_after_names_change_is_logged_as_done(
    tmp_path: Path, cfg: dict[str, Any]
) -> None:
    """S5 thật: chạy, chạy lại (dùng lại), sửa names.json, chạy lại → phải là "làm mới"."""

    def again() -> Any:
        ctx = make_context(tmp_path, cfg, video_id="names_status")
        ctx.meta["title"] = "t"
        if not ctx.has_stage(4, "cues"):
            ctx.save_stage(4, "cues", {"meta": {"title": "t"}, "cues": CUES})
        return ctx

    assert run_one(again(), 5)[1] == "done"
    assert run_one(again(), 5)[1] == "reused"

    ctx = again()
    entry = s7_ai.NameEntry(
        han="光头强", split=["光头", "强"], pinyin=["Guāngtóu", "Qiáng"], type="person"
    )
    s7_ai.save_names(s5_tokenize.names_path(ctx), {"光头": "Guāngtóu", "强": "Qiáng"}, [entry])

    _, status, events = run_one(ctx, 5)
    assert any(s5_tokenize.NAMES_CHANGED_MESSAGE in e.message for e in events)
    assert status == "done"
    assert events[-1].phase == "done"
    assert not any(e.phase == "reused" for e in events)


# --------------------------------------------------------------------------- #
# 2. srtgen fix
# --------------------------------------------------------------------------- #

typer_testing = pytest.importorskip("typer.testing")

BILINGUAL = (
    "1\n00:00:01,000 --> 00:00:02,000\n你 去 哪儿？\nnǐ qù nǎr？\nCậu đi đâu vậy?\n\n"
    "2\n00:00:03,000 --> 00:00:04,000\n我 回 家。\nwǒ huí jiā。\nTớ về nhà.\n\n"
)
ZH_ONLY = "1\n00:00:01,000 --> 00:00:02,000\n你 好。\nnǐ hǎo。\n\n"

KEPT_NAME = re.compile(r"Phim\.fixed_vi\.truoc-\d{8}-\d{6}(-\d+)?\.srt")


def cli_fix(*args: Any) -> Any:
    from srtgen import cli

    return typer_testing.CliRunner().invoke(cli.app, ["fix", *map(str, args)])


def test_cli_fix_writes_the_vi_file_like_the_web(tmp_path: Path) -> None:
    src = tmp_path / "Phim.srt"
    io_utils.write_text(src, BILINGUAL)

    res = cli_fix(src)

    assert res.exit_code == 0, res.output
    zh = io_utils.read_text(tmp_path / "Phim.fixed.srt")
    vi = io_utils.read_text(tmp_path / "Phim.fixed_vi.srt")
    assert "Cậu đi đâu vậy?" in vi and "Tớ về nhà." in vi
    assert "Cậu" not in zh and "Tớ" not in zh
    zh_blocks, vi_blocks = parse_srt(zh), parse_srt(vi)
    assert len(zh_blocks) == len(vi_blocks) == 2
    assert [b.timestamp_text for b in zh_blocks] == [b.timestamp_text for b in vi_blocks]
    assert "BLOCK_SHAPE" not in res.output


def test_cli_fix_without_vi_writes_no_vi_file(tmp_path: Path) -> None:
    src = tmp_path / "Phim.srt"
    io_utils.write_text(src, ZH_ONLY)
    res = cli_fix(src)
    assert res.exit_code == 0, res.output
    assert (tmp_path / "Phim.fixed.srt").is_file()
    assert not (tmp_path / "Phim.fixed_vi.srt").exists()


def test_cli_fix_keeps_a_hand_edited_output_before_overwriting(tmp_path: Path) -> None:
    """Chạy lại ``srtgen fix`` sau khi sửa tay file kết quả: bản sửa tay được cất, không mất."""
    src = tmp_path / "Phim.srt"
    io_utils.write_text(src, BILINGUAL)
    assert cli_fix(src).exit_code == 0

    vi_path = tmp_path / "Phim.fixed_vi.srt"
    io_utils.write_text(vi_path, io_utils.read_text(vi_path).replace("Tớ về nhà.", "Tớ về nhà đây."))
    hand_edited = vi_path.read_bytes()

    res = cli_fix(src)
    assert res.exit_code == 0, res.output
    kept = sorted(tmp_path.glob("*.truoc-*"))
    assert len(kept) == 1 and KEPT_NAME.fullmatch(kept[0].name), [k.name for k in kept]
    assert kept[0].read_bytes() == hand_edited      # nguyên từng byte
    assert "Tớ về nhà." in io_utils.read_text(vi_path)
    assert str(kept[0].name) in res.output.replace("\n", "")

    # Chạy lần ba không đổi gì: nội dung giống hệt thì không cất thêm bản nào.
    assert cli_fix(src).exit_code == 0
    assert len(list(tmp_path.glob("*.truoc-*"))) == 1
