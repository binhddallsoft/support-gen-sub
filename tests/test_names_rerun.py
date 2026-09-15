"""Bảng tên riêng sửa SAU lần chạy đầu phải thật sự được dùng ở lần chạy lại.

Lỗ hổng thiết kế mà file này chặn tái diễn: tên tiếng Việt của nhân vật chỉ nhập
được **sau khi** phim đã chạy một lần (lúc đó tab tên riêng mới có gì để liệt
kê). Nhưng chạy lại cùng link thì S5..S9 dùng lại kết quả cũ, vì mọi chặng chỉ
hỏi “file của tôi còn trên đĩa không” — tên người dùng vừa gõ không bao giờ vào
được phụ đề, và không có dòng nào trên màn hình nói điều đó.

Cách sửa được kiểm ở đây:

* S5 và S8 ghi dấu vân tay ``names_hash`` của ``names.json`` vào file chặng;
  chạy lại mà dấu khác thì làm lại chặng đó.
* S5 làm lại thì S6..S9 phải làm lại theo. Cơ chế "theo sau" này **trước đây
  không có thật** — mỗi chặng chỉ nhìn file của chính nó — nên S5 dùng
  ``force_from``, đúng cơ chế vòng hai của pipeline.
* Không bao giờ im lặng làm mất dữ liệu: file phụ đề người dùng đã sửa tay
  (trình sửa, Aegisub) được S9 cất thành ``<tên>.truoc-<thời điểm><đuôi>`` qua
  ``io_utils.keep_old_copy`` trước khi ghi lại — đúng MỘT bản cất cho một lần
  sửa; không cất được thì KHÔNG ghi đè. (Trước đây S5 còn chép thêm một bản vào
  ``ban-sua-tay-cu/``, nên một lần chạy lại để lại hai bản của cùng một lần sửa;
  việc đó đã bỏ.) ``names.json`` hỏng thì giữ kết quả cũ, và AI không bao giờ
  ghi đè một bảng hỏng.

Thêm hai phần nhỏ cùng đợt sửa: S7 dùng lại bộ nạp tên của S5 (không còn nạp vào
jieba toàn cục), và câu gợi ý của “Kiểm tra máy” trên macOS không còn Homebrew.

Context thật trong thư mục tạm, ``NullTranslator`` / ``NullProvider``, không
mạng, không cài gì, không mở cửa sổ nào.
"""

from __future__ import annotations

import inspect
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest

from srtgen import io_utils
from srtgen.core.token import KIND_WORD
from srtgen.providers.translate import NullTranslator
from srtgen.stages import s5_tokenize, s6_normalize, s7_ai, s8_translate, s9_emit
from tests.test_pipeline_offline import make_context

VIDEO_ID = "names_rerun"
TITLE = "names-rerun"

CUES = [
    {"index": 1, "start": 0.0, "end": 1.5, "text": "光头强来了。"},
    {"index": 2, "start": 1.5, "end": 3.0, "text": "他是光头强吗？"},
    {"index": 3, "start": 3.0, "end": 4.5, "text": "我们走吧。"},
]

#: Hai cách tách được chấp nhận khi bảng tên riêng đã có 光头强 = 光头 | 强.
ACCEPTED = (["光头", "强", "来", "了"], ["光头", "强", "来了"])

VI_NAME = "Cường đầu trọc"

#: Câu S5 và S6 nói khi dùng lại kết quả cũ — nhìn câu này là biết chặng có làm lại hay không.
S5_REUSED = "Dùng lại kết quả tách từ đã có sẵn."
S6_REUSED = "Dùng lại kết quả chuẩn hoá đã có sẵn."


# --------------------------------------------------------------------------- #
# dựng dữ liệu
# --------------------------------------------------------------------------- #

class RecordingTranslator(NullTranslator):
    """``NullTranslator`` (trả nguyên văn) nhưng nhớ bảng thuật ngữ nó được gửi."""

    def __init__(self) -> None:
        super().__init__()
        self.glossaries: list[dict[str, str]] = []

    def translate_batch(self, items, context, *, target="vi"):
        self.glossaries.append(dict((context or {}).get("glossary") or {}))
        return super().translate_batch(items, context, target=target)


class Log:
    """Thu mọi câu tiến trình mà chặng nói với người dùng."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def __call__(self, message: str = "", fraction: float = 0.0) -> None:
        del fraction
        self.messages.append(str(message))

    def said(self, text: str) -> bool:
        return any(text in m for m in self.messages)


@pytest.fixture
def config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Cấu hình mặc định, tắt cache AI và cache dịch để không ghi vào thư mục thật của người dùng."""
    out = dict(cfg)
    out["ai"] = {**dict(cfg.get("ai") or {}), "cache": False}
    out["translate"] = {**dict(cfg.get("translate") or {}), "cache": False}
    return out


@pytest.fixture
def offline_ai(monkeypatch: pytest.MonkeyPatch, null_provider: Any) -> None:
    """S7 luôn gặp ``NullProvider``, kể cả khi máy chạy test có sẵn khoá API."""
    monkeypatch.setattr(s7_ai, "get_provider", lambda cfg: null_provider)


def new_run(tmp_path: Path, config: dict[str, Any]) -> Any:
    """Một lần “chạy lại cùng link”: Context mới, cùng thư mục làm việc."""
    ctx = make_context(tmp_path, config, video_id=VIDEO_ID)
    ctx.meta["title"] = TITLE
    if not ctx.has_stage(4, "cues"):
        ctx.save_stage(4, "cues", {"meta": {"title": TITLE}, "cues": CUES})
    return ctx


def write_names(ctx: Any, vi: str = VI_NAME) -> Path:
    """Ghi names.json bằng đúng hàm S7 dùng, để test bám định dạng thật."""
    path = s5_tokenize.names_path(ctx)
    entry = s7_ai.NameEntry(
        han="光头强",
        split=["光头", "强"],
        pinyin=["Guāngtóu", "Qiáng"],
        type="person",
        vi=vi,
    )
    s7_ai.save_names(path, {"光头": "Guāngtóu", "强": "Qiáng"}, [entry])
    return path


def run_s5_to_s8(ctx: Any, translator: NullTranslator, log: Log) -> dict[str, Any]:
    return {
        "s5": s5_tokenize.run(ctx, log),
        "s6": s6_normalize.run(ctx, log),
        "s7": s7_ai.run(ctx, log),
        "s8": s8_translate.run(ctx, log, translator=translator),
    }


def words(cue: Any) -> list[str]:
    return [t.zh for t in cue.tokens if t.kind == KIND_WORD]


def stored_hash(ctx: Any, number: int, name: str) -> Any:
    return io_utils.read_json(ctx.stage_path(number, name)).get(s5_tokenize.NAMES_HASH_KEY)


# --------------------------------------------------------------------------- #
# dấu vân tay
# --------------------------------------------------------------------------- #

def _raw_json(path: Path, data: Any) -> None:
    """Ghi thẳng byte — io_utils sẽ tự chuẩn hoá NFC, mà ở đây cần giữ NFD để kiểm."""
    path.write_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def test_names_hash_ignores_key_order_nfd_and_save_time(tmp_path: Path) -> None:
    """Chỉ một thay đổi thật mới được tính là thay đổi.

    Bấm “Lưu” trên bảng không sửa gì đổi ``updated_at``; macOS trả chuỗi NFD;
    người sửa tay đổi thứ tự khoá. Cả ba mà bị tính là “bảng đã đổi” thì cả phim
    bị làm lại — và có khoá Gemini thì trả tiền dịch lần nữa.
    """
    entry = {"han": "光头强", "split": ["光头", "强"], "pinyin": ["Guāngtóu", "Qiáng"], "vi": VI_NAME}
    a = tmp_path / "a.json"
    _raw_json(a, {"version": 1, "updated_at": "2026-01-01T00:00:00",
                  "names": {"光头": "Guāngtóu", "强": "Qiáng"}, "entries": [entry]})
    nfd = {k: unicodedata.normalize("NFD", v) if isinstance(v, str) else
           [unicodedata.normalize("NFD", x) for x in v] for k, v in entry.items()}
    b = tmp_path / "b.json"
    _raw_json(b, {"entries": [dict(reversed(list(nfd.items())))],
                  "names": {"强": "Qiáng", "光头": unicodedata.normalize("NFD", "Guāngtóu")},
                  "updated_at": "2026-09-10T12:00:00", "version": 1})
    assert s5_tokenize.names_hash(a) == s5_tokenize.names_hash(b)
    assert s5_tokenize.names_hash(a)

    c = tmp_path / "c.json"
    _raw_json(c, {"version": 1, "names": {"光头": "Guāngtóu", "强": "Qiáng"},
                  "entries": [{**entry, "vi": "Cường Trọc"}]})
    assert s5_tokenize.names_hash(c) != s5_tokenize.names_hash(a), "Đổi tên tiếng Việt là đổi bảng."

    assert s5_tokenize.names_hash(tmp_path / "khong-co.json") == s5_tokenize.NO_NAMES_HASH
    empty = tmp_path / "empty.json"
    _raw_json(empty, {"version": 1, "updated_at": "x", "names": {}, "entries": []})
    assert s5_tokenize.names_hash(empty) == s5_tokenize.NO_NAMES_HASH

    broken = tmp_path / "broken.json"
    broken.write_bytes(b'{"names": {"\xe5\x85\x89\xe5\xa4\xb4": ')
    assert s5_tokenize.names_hash(broken) is None, "File hỏng là “không biết”, không phải “không có bảng”."


# --------------------------------------------------------------------------- #
# kịch bản chính của đề bài
# --------------------------------------------------------------------------- #

def test_editing_names_after_a_run_redoes_s5_and_s8(
    tmp_path: Path, config: dict[str, Any], offline_ai: None
) -> None:
    """Chạy S5-S8, sửa names.json, chạy lại → S5 và S8 được làm lại (và S6, S7 theo sau)."""
    first = new_run(tmp_path, config)
    run_s5_to_s8(first, RecordingTranslator(), Log())
    assert stored_hash(first, 5, "tokens") == s5_tokenize.NO_NAMES_HASH
    assert stored_hash(first, 8, "translate") == s5_tokenize.NO_NAMES_HASH

    # Người dùng mở tab tên riêng sau lần chạy đầu và gõ tên tiếng Việt.
    second = new_run(tmp_path, config)
    table = write_names(second)
    fingerprint = s5_tokenize.names_hash(table)
    assert fingerprint

    log = Log()
    translator = RecordingTranslator()
    out = run_s5_to_s8(second, translator, log)

    # S5 làm lại, nói rõ bằng tiếng Việt, và lần này tên không bị cắt ngang.
    assert log.said(s5_tokenize.NAMES_CHANGED_MESSAGE), log.messages
    assert not log.said(S5_REUSED), log.messages
    assert words(out["s5"].cues[0]) in ACCEPTED
    assert stored_hash(second, 5, "tokens") == fingerprint

    # Cơ chế “S6..S9 theo sau” là force_from — trước bản sửa này nó không tồn tại.
    assert second.force_from == 5
    assert not log.said(S6_REUSED), "S6 dùng lại bản cũ thì tên mới không vào được file."
    assert out["s6"].cues[0].zh_text().startswith("光头 强 "), out["s6"].cues[0].zh_text()
    assert not out["s7"].get("resumed")

    # S8 làm lại, với bảng thuật ngữ mới, và ghi dấu của bảng mới.
    assert not out["s8"].get("resumed")
    assert translator.calls > 0
    assert any(g.get("光头强") == VI_NAME for g in translator.glossaries), translator.glossaries
    assert stored_hash(second, 8, "translate") == fingerprint

    # Chạy lại lần ba không sửa gì: dùng lại hết, không dịch lại câu nào.
    third = new_run(tmp_path, config)
    log3 = Log()
    translator3 = RecordingTranslator()
    out3 = run_s5_to_s8(third, translator3, log3)
    assert log3.said(S5_REUSED) and log3.said(S6_REUSED), log3.messages
    assert not log3.said(s5_tokenize.NAMES_CHANGED_MESSAGE)
    assert out3["s8"].get("resumed") is True
    assert translator3.calls == 0
    assert not_forced(third)


def test_saving_the_table_without_edits_does_not_redo_the_film(
    tmp_path: Path, config: dict[str, Any], offline_ai: None
) -> None:
    """Bấm Lưu mà không sửa gì chỉ đổi ``updated_at`` — không được làm lại cả phim."""
    first = new_run(tmp_path, config)
    table = write_names(first)
    run_s5_to_s8(first, RecordingTranslator(), Log())

    data = io_utils.read_json(table)
    data["updated_at"] = "2099-12-31T23:59:59"
    io_utils.write_json(table, data)

    again = new_run(tmp_path, config)
    log = Log()
    s5_tokenize.run(again, log)
    assert log.said(S5_REUSED), log.messages
    assert not_forced(again)


def test_translation_step_alone_notices_a_new_vietnamese_name(
    tmp_path: Path, config: dict[str, Any], offline_ai: None
) -> None:
    """S8 tự so dấu của mình: bản dịch cũ không được sống sót qua một tên tiếng Việt mới.

    Và nó phải kéo S9 theo (``force_from = 8``), nếu không bản dịch mới nằm trong
    ``S8_translate.json`` còn ``_vi.srt`` vẫn là bản cũ.
    """
    first = new_run(tmp_path, config)
    write_names(first, vi=VI_NAME)
    run_s5_to_s8(first, RecordingTranslator(), Log())

    later = new_run(tmp_path, config)
    write_names(later, vi="Cường Trọc")
    log = Log()
    translator = RecordingTranslator()
    result = s8_translate.run(later, log, translator=translator)

    assert not result.get("resumed")
    assert log.said(s8_translate.NAMES_CHANGED_MESSAGE), log.messages
    assert any(g.get("光头强") == "Cường Trọc" for g in translator.glossaries)
    assert later.force_from == 8


def test_stage_files_from_before_fingerprints(
    tmp_path: Path, config: dict[str, Any], offline_ai: None
) -> None:
    """File chặng cũ không có ``names_hash``: không bảng thì giữ, có bảng thì làm lại một lần."""
    first = new_run(tmp_path, config)
    run_s5_to_s8(first, RecordingTranslator(), Log())
    for number, name in ((5, "tokens"), (8, "translate")):
        payload = io_utils.read_json(first.stage_path(number, name))
        payload.pop(s5_tokenize.NAMES_HASH_KEY, None)
        io_utils.write_json(first.stage_path(number, name), payload)

    kept = new_run(tmp_path, config)
    log = Log()
    out = run_s5_to_s8(kept, RecordingTranslator(), log)
    assert log.said(S5_REUSED), log.messages
    assert out["s8"].get("resumed") is True

    redone = new_run(tmp_path, config)
    write_names(redone)
    log2 = Log()
    s5_tokenize.run(redone, log2)
    assert log2.said(s5_tokenize.NAMES_CHANGED_MESSAGE), log2.messages


def test_unreadable_names_json_keeps_previous_results(
    tmp_path: Path, config: dict[str, Any], offline_ai: None
) -> None:
    """Bảng đang sửa dở (JSON hỏng): giữ kết quả cũ và NÓI RA, không làm lại bằng bảng rỗng."""
    first = new_run(tmp_path, config)
    table = write_names(first)
    run_s5_to_s8(first, RecordingTranslator(), Log())

    table.write_bytes(b'{"names": {"')
    again = new_run(tmp_path, config)
    log = Log()
    doc = s5_tokenize.run(again, log)
    translated = s8_translate.run(again, log, translator=RecordingTranslator())

    assert log.said("Không đọc được bảng tên riêng"), log.messages
    assert log.said(S5_REUSED)
    assert words(doc.cues[0]) in ACCEPTED, "Phải giữ đúng bản đã tách với bảng tên cũ."
    assert translated.get("resumed") is True
    assert not_forced(again)


# --------------------------------------------------------------------------- #
# không bao giờ im lặng làm mất phần người dùng đã sửa tay
# --------------------------------------------------------------------------- #

def _finished_film_with_hand_edit(tmp_path: Path, config: dict[str, Any]) -> tuple[Path, str, Path | None]:
    """Chạy trọn S5..S9, rồi sửa tay file .srt như người dùng làm trong trình sửa."""
    ctx = new_run(tmp_path, config)
    run_s5_to_s8(ctx, RecordingTranslator(), Log())
    result = s9_emit.run(ctx)
    srt = Path(result["srt"])
    vi = Path(result["vi_srt"]) if result.get("vi_srt") else None

    edited = io_utils.read_text(srt).replace("我们", "咱们", 1) + "\n99\n00:00:05,000 --> 00:00:06,000\n手改的\nshǒu gǎi de\n"
    io_utils.write_text(srt, edited, bom=True)
    return srt, io_utils.read_text(srt), vi


def _kept_copies(root: Path) -> list[Path]:
    """Mọi bản cất ``.truoc-*`` nằm đâu đó dưới ``root``."""
    return sorted(p for p in root.rglob("*") if p.is_file() and io_utils.is_kept_copy(p))


def test_hand_edit_survives_the_names_redo_in_exactly_one_truoc_file(
    tmp_path: Path, config: dict[str, Any], offline_ai: None
) -> None:
    """Bảng tên đổi → chạy lại: bản sửa tay nằm nguyên trong ĐÚNG MỘT file ``.truoc-*``.

    Sửa theo ngoại lệ của nhiệm vụ: bài này trước đây kiểm thư mục
    ``ban-sua-tay-cu/`` do S5 tạo. S5 không còn tự chép nữa (S9 đã lo qua
    ``io_utils.keep_old_copy``), vì hai cơ chế cùng chạy để lại HAI bản của cùng
    một lần sửa tay ở hai chỗ khác nhau.
    """
    srt, edited, _vi = _finished_film_with_hand_edit(tmp_path, config)
    hand_bytes = srt.read_bytes()

    ctx = new_run(tmp_path, config)
    write_names(ctx)
    log = Log()
    s5_tokenize.run(ctx, log)
    assert log.said(s5_tokenize.NAMES_CHANGED_MESSAGE), log.messages
    assert _kept_copies(tmp_path) == [], "S5 không tự cất gì nữa — việc đó của S9."
    assert io_utils.read_text(srt) == edited, "Trước S9, file người dùng còn nguyên chỗ cũ."

    s6_normalize.run(ctx, log)
    s7_ai.run(ctx, log)
    s8_translate.run(ctx, log, translator=RecordingTranslator())
    result = s9_emit.run(ctx, log)

    holding = [p for p in _kept_copies(tmp_path) if "手改的" in io_utils.read_text(p)]
    assert len(holding) == 1, [p.name for p in _kept_copies(tmp_path)]
    (copy,) = holding
    assert copy.read_bytes() == hand_bytes, "Bản cất phải còn nguyên từng byte người dùng đã lưu."
    assert copy.parent == srt.parent, "Bản cất nằm ngay cạnh file kết quả."
    assert re.fullmatch(rf"{re.escape(srt.stem)}\.truoc-\d{{8}}-\d{{6}}(-\d+)?\.srt", copy.name)
    assert str(copy) in result["backups"]
    assert log.said(copy.name), "Phải nói cho người dùng biết bản cũ nằm ở đâu."
    assert not [p for p in tmp_path.rglob("ban-sua-tay-cu")], "Không còn thư mục ban-sua-tay-cu nào."

    fresh = io_utils.read_text(srt)
    assert "光头 强" in fresh and "手改的" not in fresh


def test_redo_never_overwrites_a_hand_edit_it_cannot_set_aside(
    tmp_path: Path, config: dict[str, Any], offline_ai: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Không cất được bản sửa tay (file đang bị khoá) → S9 DỪNG, file người dùng còn nguyên.

    Sửa theo ngoại lệ của nhiệm vụ: trước đây S5 từ chối làm lại khi không chép
    được sang ``ban-sua-tay-cu/``. Nay lời hứa "không ghi đè thứ chưa cất được"
    nằm ở một chỗ duy nhất — ``io_utils.keep_old_copy`` mà S9 gọi trước khi ghi.
    """
    srt, _edited, _vi = _finished_film_with_hand_edit(tmp_path, config)
    before = srt.read_bytes()

    ctx = new_run(tmp_path, config)
    write_names(ctx)
    log = Log()
    s5_tokenize.run(ctx, log)
    s6_normalize.run(ctx, log)
    s7_ai.run(ctx, log)
    s8_translate.run(ctx, log, translator=RecordingTranslator())

    real_rename = Path.rename

    def locked(self: Path, target: Any) -> Any:
        if self == srt:
            raise PermissionError("file đang mở trong Aegisub")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(s9_emit.BackupError) as info:
        s9_emit.run(ctx, log)

    assert "KHÔNG ghi đè" in str(info.value)
    assert srt.read_bytes() == before, "Phần người dùng đã sửa phải còn nguyên từng byte."
    assert not [p for p in _kept_copies(tmp_path) if p.suffix == ".srt"]


# --------------------------------------------------------------------------- #
# S7 dùng lại bộ nạp tên của S5
# --------------------------------------------------------------------------- #

class NamesProvider:
    """Nhà cung cấp giả: T1 trả đúng một tên có cụm một chữ, mọi nhiệm vụ khác trả rỗng."""

    name = "fake-names"
    reason = ""

    def complete_json(self, prompt: str, schema: dict, *, model: str = "") -> dict:
        del prompt, model
        if "names" in (schema.get("properties") or {}):
            return {"names": [{"han": "光头强", "split": ["光头", "强"],
                               "pinyin": ["guāngtóu", "qiáng"], "type": "person"}]}
        return {}


@pytest.fixture
def ai_t1_only(config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setattr(s7_ai, "get_provider", lambda cfg: NamesProvider())
    out = dict(config)
    out["ai"] = {
        **dict(config.get("ai") or {}),
        "enabled": True,
        "cache": False,
        "tasks": {"t1_names": True, "t2_heteronym": False,
                  "t3_case_after_ellipsis": False, "t4_end_punct": False},
    }
    return out


@pytest.fixture
def jieba_snapshot() -> Iterator[Callable[[], tuple[dict[str, int], int]]]:
    """Chụp từ điển chung của jieba, trả nguyên trạng sau test dù test hỏng giữa chừng."""
    jieba = pytest.importorskip("jieba")
    jieba.initialize()
    dt = jieba.dt
    freq, total, tags = dict(dt.FREQ), dt.total, dict(dt.user_word_tag_tab)
    try:
        yield lambda: (dict(dt.FREQ), dt.total)
    finally:
        dt.FREQ.clear()
        dt.FREQ.update(freq)
        dt.total = total
        dt.user_word_tag_tab.clear()
        dt.user_word_tag_tab.update(tags)


def test_s7_has_no_private_userdict_code_left() -> None:
    assert not hasattr(s7_ai, "write_userdict")
    assert not hasattr(s7_ai, "load_userdict")
    assert "write_userdict" not in s7_ai.__all__


def test_s7_loads_names_like_s5_and_leaves_global_jieba_alone(
    tmp_path: Path, ai_t1_only: dict[str, Any], jieba_snapshot: Any
) -> None:
    """Tên 光头强 = 光头 | 强: nạp CẢ TÊN và cụm một chữ, trên tokenizer riêng.

    Bản riêng cũ của S7 bỏ cụm một chữ, không nạp cả tên, và dạy jieba toàn cục
    — tên của phim này rò sang cách tách của mọi phim sau trong cùng tiến trình.
    """
    ctx = new_run(tmp_path, ai_t1_only)
    s5_tokenize.run(ctx, Log())
    s6_normalize.run(ctx, Log())
    before = jieba_snapshot()

    result = s7_ai.run(ctx, Log())

    assert jieba_snapshot() == before, "S7 không được đụng vào từ điển chung của jieba."
    report = result["names"]
    assert report["added"] == ["光头强"]
    assert report["jieba_loaded"] is True
    lines = io_utils.read_text(Path(report["userdict"])).splitlines()
    assert f"光头强 {s5_tokenize.NAME_WORD_FREQ} nz" in lines, lines
    assert "强 nz" in lines, "Cụm một chữ phải được nạp — bản cũ bỏ nó."
    assert result["rerun_s5"] is True

    # Vòng hai của pipeline: S5 chạy lại với bảng T1 vừa ghi, tên không bị cắt ngang.
    ctx.cfg = {**ctx.cfg, "force_from": 5}
    doc = s5_tokenize.run(ctx, Log())
    assert words(doc.cues[0]) in ACCEPTED


def test_t1_never_saves_over_an_unreadable_names_json(
    tmp_path: Path, ai_t1_only: dict[str, Any]
) -> None:
    """AI tìm được tên nhưng bảng của người dùng đang hỏng: CẤT nguyên vẹn rồi mới ghi (H4).

    Trước đợt H4 test này khẳng định S7 bỏ luôn tên AI tìm được và để nguyên file
    hỏng. Hợp đồng H4 đổi hành vi đó: file hỏng được đổi tên thành
    ``names.json.hong-<thời điểm>`` (không bao giờ bị ghi đè hay xoá), rồi bảng
    mới mới được ghi. Chi tiết: tests/test_s7_names_backup.py.
    """
    ctx = new_run(tmp_path, ai_t1_only)
    s5_tokenize.run(ctx, Log())
    s6_normalize.run(ctx, Log())
    table = s5_tokenize.names_path(ctx)
    broken = '{"names": {"光头": "Guāngtóu", "强": '.encode("utf-8")
    table.write_bytes(broken)

    result = s7_ai.run(ctx, Log())

    (backup,) = sorted(table.parent.glob("names.json.hong-*"))
    assert backup.read_bytes() == broken, "Phần người dùng đã gõ phải còn nguyên từng byte."
    assert result["names"]["backup"] == str(backup)
    assert any(backup.name in str(e.get("user_message")) for e in result["errors"]), result["errors"]


# --------------------------------------------------------------------------- #
# Kiểm tra máy trên macOS: không Homebrew
# --------------------------------------------------------------------------- #

def test_mac_doctor_hints_point_to_the_fix_button_or_the_installer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from srtgen import cli
    from srtgen.stages import s0_fetch

    monkeypatch.setattr(cli, "_is_mac", lambda: True)
    monkeypatch.setattr(cli, "_is_windows", lambda: False)
    monkeypatch.setattr(s0_fetch, "which_tool", lambda name, refresh=False: None)

    for name in ("ffmpeg", "ffprobe", "yt-dlp"):
        hint = cli._tool_install_hint(name)
        assert hint == "Bấm nút sửa ngay bên cạnh, hoặc chạy lại CaiDat.command."
    check = cli._check_tool("ffmpeg", "ffmpeg", required=True)
    assert check.fix == cli._MAC_TOOL_FIX
    assert "CaiDat.command" in cli._MAC_PYTHON_FIX
    assert "brew" not in cli._MAC_PYTHON_FIX.lower()
    assert "brew install" not in inspect.getsource(cli)


def test_every_tool_the_mac_hint_names_really_has_a_fix_button() -> None:
    """“Bấm nút sửa ngay bên cạnh” chỉ đúng khi màn Kiểm tra máy thật sự dựng nút đó."""
    pytest.importorskip("fastapi")
    from srtgen.web.server import _DOCTOR_FIX_ACTIONS

    for name in ("ffmpeg", "ffprobe", "yt-dlp"):
        assert _DOCTOR_FIX_ACTIONS.get(name), name


def not_forced(ctx: Any) -> bool:
    """Không chặng nào bị ép chạy lại.

    ``default.yaml`` đặt ``force_from: 99`` (lớn hơn mọi chặng) thay cho “không ép”, nên
    kiểm ``is None`` là sai: điều cần biết là S9 có bị ép ghi lại file hay không.
    """
    return ctx.force_from is None or ctx.force_from > s9_emit.STAGE_NUMBER
