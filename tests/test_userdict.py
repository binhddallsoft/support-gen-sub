"""Bảng tên riêng trong bộ tách từ (S5) và câu báo thiếu ffmpeg (S0).

Hai chuyện nằm chung một file vì cùng một kiểu hỏng: người dùng làm đúng điều tool
bảo mà kết quả vẫn sai, và không có gì trên màn hình nói cho họ biết vì sao.

* **S5 — tên có cụm một chữ.** ``names.json`` ghi 光头强 = [光头, 强]. Trước đây
  S5 chỉ nạp các *cụm* (光头, 强) vào jieba, mà cụm dưới 2 chữ thì bị bỏ qua ở
  ``suggest_freq``; kết quả là 光头强来了 bị cắt thành 光头 | 强来 | 了 — một cụm
  vắt ngang tên và động từ, không chặng nào sau sửa lại được. Bản sửa nạp CẢ TÊN
  làm một từ tần suất cao rồi tách lại theo ``split`` của names.json. Hợp đồng
  được giao: ra [光头, 强, 来, 了] hoặc [光头, 强, 来了], miễn KHÔNG còn 强来.
* **S0 — thiếu ffmpeg.** Câu báo lỗi từng bảo người dùng gõ ``brew install
  ffmpeg`` trong khi bộ cài cố ý bỏ Homebrew. Nay nó chỉ trỏ tới nút “Cài ffmpeg
  giúp tôi” (``fix_action = install_ffmpeg``) hoặc chạy lại CaiDat.command — và
  nút đó phải thật sự có tác dụng ngay trong phiên đang chạy.

jieba là trạng thái toàn cục của cả tiến trình pytest: mọi test chạm tới jieba ở
đây đi qua fixture ``global_jieba``, fixture này chụp lại từ điển chung trước khi
chạy và trả nguyên trạng sau đó, để một tên riêng học ở đây không lặng lẽ đổi cách
tách của test khác. Không test nào gọi mạng, pip, hay mở cửa sổ nào.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterator

import pytest

from srtgen import io_utils
from srtgen.stages import s0_fetch as s0

#: Hai cách tách được chấp nhận cho câu mẫu (đề bài cho phép cả hai).
ACCEPTED = (["光头", "强", "来", "了"], ["光头", "强", "来了"])
TRAP = "强来"
SENTENCE = "光头强来了"


# --------------------------------------------------------------------------- #
# fixture
# --------------------------------------------------------------------------- #

@pytest.fixture
def global_jieba() -> Iterator[Any]:
    """Module jieba, với từ điển chung được trả nguyên trạng sau test.

    Chụp sau ``initialize()`` vì đó là trạng thái mọi test khác cũng thấy. Trả
    lại cả ``FREQ``, ``total`` lẫn bảng nhãn: đó là toàn bộ thứ ``add_word`` /
    ``suggest_freq`` đụng tới trên tokenizer chung.
    """
    jieba = pytest.importorskip("jieba")
    jieba.initialize()
    dt = jieba.dt
    freq_before = dict(dt.FREQ)
    total_before = dt.total
    tags_before = dict(dt.user_word_tag_tab)
    try:
        yield jieba
    finally:
        if dt.FREQ != freq_before:
            dt.FREQ.clear()
            dt.FREQ.update(freq_before)
        dt.total = total_before
        if dt.user_word_tag_tab != tags_before:
            dt.user_word_tag_tab.clear()
            dt.user_word_tag_tab.update(tags_before)


def _write_s7_table(path: Path) -> dict[str, str]:
    """Ghi names.json bằng đúng hàm S7 dùng, để test bám định dạng thật chứ không tự chế."""
    from srtgen.stages.s7_ai import NameEntry, save_names

    flat = {"光头": "Guāngtóu", "强": "Qiáng"}
    entry = NameEntry(
        han="光头强",
        split=["光头", "强"],
        pinyin=["Guāngtóu", "Qiáng"],
        type="person",
        vi="Cường đầu trọc",
    )
    save_names(path, flat, [entry])
    return flat


def _words(cue: Any) -> list[str]:
    from srtgen.core.token import KIND_WORD

    return [t.zh for t in cue.tokens if t.kind == KIND_WORD]


def _assert_name_kept(pieces: list[str]) -> None:
    """光头 rồi 强 đứng liền nhau, và không có cụm nào vắt ngang tên."""
    assert TRAP not in pieces, pieces
    head = pieces.index("光头")
    assert pieces[head + 1] == "强", pieces


# --------------------------------------------------------------------------- #
# S5 — đọc names.json
# --------------------------------------------------------------------------- #

def test_load_name_splits_reads_the_file_s7_writes(tmp_path: Path) -> None:
    from srtgen.stages.s5_tokenize import load_name_splits, load_names

    path = tmp_path / "names.json"
    flat = _write_s7_table(path)

    assert load_name_splits(path) == {"光头强": ["光头", "强"]}
    # Hợp đồng cũ của load_names không đổi: vẫn là bảng {cụm: pinyin} mà S6 cần.
    assert load_names(path) == flat


def test_load_name_splits_accepts_hand_edited_layouts(tmp_path: Path) -> None:
    """Người dùng sửa tay names.json theo đủ kiểu hợp lý; kiểu nào cũng phải đọc được."""
    from srtgen.stages.s5_tokenize import load_name_splits

    layouts: dict[str, Any] = {
        "nested": {"光头强": {"pinyin": "Guāngtóu Qiáng", "split": ["光头", "强"]}},
        "wrapped_nested": {"names": {"光头强": {"pinyin": "x", "split": ["光头", "强"]}}},
        "records": [{"zh": "光头强", "split": ["光头", "强"]}],
        "legacy_meta": {
            "光头": "Guāngtóu",
            "_meta": {"entries": [{"han": "光头强", "split": ["光头", "强"]}]},
        },
    }
    for label, payload in layouts.items():
        path = tmp_path / f"{label}.json"
        io_utils.write_json(path, payload)
        assert load_name_splits(path) == {"光头强": ["光头", "强"]}, label


def test_load_name_splits_refuses_to_guess(tmp_path: Path) -> None:
    """Cách tách không ghép lại đúng tên thì bỏ, không đoán; file hỏng thì coi như không có."""
    from srtgen.stages.s5_tokenize import load_name_splits

    path = tmp_path / "names.json"
    io_utils.write_json(
        path,
        {
            "names": {},
            "entries": [
                {"han": "光头强", "split": ["光", "强"]},      # thiếu chữ 头
                {"han": "强", "split": ["强"]},                # một chữ: jieba vốn đã giữ nguyên
                {"han": "熊大", "split": "熊 大"},             # không phải danh sách
                {"han": "熊二", "split": ["熊", "二"]},        # hợp lệ
            ],
        },
    )
    assert load_name_splits(path) == {"熊二": ["熊", "二"]}

    assert load_name_splits(tmp_path / "khong-co.json") == {}
    broken = tmp_path / "broken.json"
    io_utils.write_text(broken, "{không phải json", bom=False)
    assert load_name_splits(broken) == {}


# --------------------------------------------------------------------------- #
# S5 — bộ tách từ
# --------------------------------------------------------------------------- #

def test_without_whole_names_the_trap_reproduces(global_jieba: Any) -> None:
    """Điều kiện tiên quyết: câu mẫu thật sự là cái bẫy trên bản jieba này.

    Chỉ nạp các cụm (đúng như trước bản sửa) thì vẫn ra 强来 — đúng như biên bản
    audit. Nếu một ngày test này đỏ vì jieba đổi, các test bên dưới vẫn đúng
    nhưng không còn chứng minh được gì; khi đó cần tìm câu mẫu khác.
    """
    from srtgen.stages.s5_tokenize import make_segmenter

    assert TRAP in global_jieba.lcut(SENTENCE)
    cut, _ = make_segmenter({"光头": "Guāngtóu", "强": "Qiáng"})
    assert TRAP in cut(SENTENCE)


def test_segmenter_keeps_the_whole_name_then_splits_it(
    global_jieba: Any, tmp_path: Path
) -> None:
    from srtgen.stages.s5_tokenize import NAME_WORD_FREQ, load_name_splits, make_segmenter

    table = tmp_path / "names.json"
    flat = _write_s7_table(table)
    userdict = tmp_path / "jieba_userdict.txt"

    cut, count = make_segmenter(flat, splits=load_name_splits(table), userdict_path=userdict)

    assert cut(SENTENCE) in ACCEPTED
    for sentence in ("今天光头强来了吗", "他是光头强", "光头强，你来了"):
        _assert_name_kept(cut(sentence))
    # Nút “Sinh lại pinyin” gọi kèm HMM=False; tham số phải đi thẳng tới jieba.
    _assert_name_kept(cut(SENTENCE, HMM=False))

    # Một tên (光头强), không phải ba mục 光头 / 强 / 光头强.
    assert count == 1
    # Tên đầy đủ nằm trong file từ điển với tần suất cao — file này để người tò mò mở ra xem.
    assert f"光头强 {NAME_WORD_FREQ} nz" in io_utils.read_text(userdict).splitlines()


def test_global_jieba_is_left_alone(global_jieba: Any, tmp_path: Path) -> None:
    """Tên học cho phim này không được rò sang video khác chạy cùng tiến trình."""
    from srtgen.stages.s5_tokenize import load_name_splits, make_segmenter

    before = global_jieba.lcut(SENTENCE)
    freq_before = global_jieba.dt.FREQ.get("光头强")

    table = tmp_path / "names.json"
    flat = _write_s7_table(table)
    cut, _ = make_segmenter(flat, splits=load_name_splits(table))
    assert cut(SENTENCE) in ACCEPTED

    assert global_jieba.lcut(SENTENCE) == before
    assert global_jieba.dt.FREQ.get("光头强") == freq_before


def test_tokenize_and_normalize_give_matching_name_clusters(
    global_jieba: Any, tmp_path: Path, cfg: dict[str, Any]
) -> None:
    """Qua trọn S5 + S6: tách đúng tên, và dòng pinyin đánh vần tên theo bảng."""
    from srtgen.core.token import KIND_WORD, Cue, Document, Token
    from srtgen.stages.s5_tokenize import load_name_splits, tokenize_document
    from srtgen.stages.s6_normalize import normalize_document

    table = tmp_path / "names.json"
    flat = _write_s7_table(table)
    doc = Document(
        cues=[Cue(index=1, start=0.0, end=1.2, tokens=[Token(kind=KIND_WORD, zh="光头强来了。")])]
    )

    tokenize_document(
        doc,
        cfg,
        names=flat,
        name_splits=load_name_splits(table),
        userdict_path=tmp_path / "jieba_userdict.txt",
    )
    assert _words(doc.cues[0]) in ACCEPTED

    normalize_document(doc, cfg, names=flat)
    cue = doc.cues[0]
    assert cue.zh_text().startswith("光头 强 "), cue.zh_text()
    assert cue.py_text().startswith("Guāngtóu Qiáng "), cue.py_text()
    assert len(cue.zh_text().split()) == len(cue.py_text().split())


def test_stage_run_reads_the_split_from_names_json(
    global_jieba: Any, tmp_path: Path, cfg: dict[str, Any]
) -> None:
    """Đường thật của pipeline: S5.run tự đọc names.json cạnh file làm việc."""
    from srtgen.core.context import Context
    from srtgen.stages import s5_tokenize

    ctx = Context(
        video_id="userdict_test",
        work_dir=tmp_path,
        out_dir=tmp_path / "out",
        cfg=cfg,
    )
    _write_s7_table(s5_tokenize.names_path(ctx))
    ctx.save_stage(
        4,
        "cues",
        {
            "cues": [
                {"index": 1, "start": 0.0, "end": 1.5, "text": "光头强来了。"},
                {"index": 2, "start": 1.5, "end": 3.0, "text": "他是光头强吗？"},
            ]
        },
    )

    doc = s5_tokenize.run(ctx)

    assert _words(doc.cues[0]) in ACCEPTED
    _assert_name_kept(_words(doc.cues[1]))
    assert ctx.has_stage(5, "tokens")


# --------------------------------------------------------------------------- #
# S0 — thiếu ffmpeg: chỉ nút bấm trong app hoặc CaiDat.command, không Homebrew
# --------------------------------------------------------------------------- #

def _assert_no_homebrew(message: str) -> None:
    lowered = message.lower()
    assert "brew" not in lowered, message
    assert "terminal" not in lowered, message


def test_ffmpeg_error_from_ytdlp_points_to_the_install_button() -> None:
    err = s0.diagnose_error(
        "ERROR: Postprocessing: ffprobe and ffmpeg not found.",
        default_message="không dùng tới",
        default_fix=s0.FIX_NONE,
    )
    assert err.fix_action == "install_ffmpeg"
    assert "Cài ffmpeg giúp tôi" in err.user_message
    assert "CaiDat.command" in err.user_message
    _assert_no_homebrew(err.user_message)


def test_require_ffmpeg_points_to_the_install_button(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(s0, "ffmpeg_path", lambda: None)
    with pytest.raises(s0.FetchError) as info:
        s0.require_ffmpeg()
    assert info.value.fix_action == "install_ffmpeg"
    assert "Cài ffmpeg giúp tôi" in info.value.user_message
    assert "CaiDat.command" in info.value.user_message
    _assert_no_homebrew(info.value.user_message)


def test_no_user_message_in_s0_mentions_homebrew(monkeypatch: pytest.MonkeyPatch) -> None:
    for rule in s0._ERROR_RULES:
        assert "brew" not in rule.message.lower(), rule.message

    monkeypatch.setattr(s0, "ytdlp_command", lambda: None)
    with pytest.raises(s0.FetchError) as info:
        s0._require_ytdlp()
    assert info.value.fix_action == s0.FIX_INSTALL_YTDLP
    _assert_no_homebrew(info.value.user_message)


def test_install_ffmpeg_is_a_whitelisted_action() -> None:
    """Mã UI nhận được phải là khoá POST /api/actions chấp nhận, nếu không nút sẽ bị từ chối."""
    pytest.importorskip("fastapi")
    from srtgen.web.server import ACTION_KEYS, _bin_dir

    assert s0.FIX_INSTALL_FFMPEG == "install_ffmpeg"
    assert s0.FIX_INSTALL_FFMPEG in s0.FIX_ACTIONS
    assert s0.FIX_INSTALL_FFMPEG in ACTION_KEYS
    # Nút cài ffmpeg ghi vào đúng thư mục mà S0 dò — nếu hai chỗ lệch nhau thì
    # bấm cài xong, tool vẫn báo thiếu.
    assert s0._app_bin_dirs() == (str(_bin_dir()),)


def test_tool_installed_mid_session_is_found_without_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bấm “Cài ffmpeg giúp tôi” xong, lần bấm Bắt đầu kế tiếp phải thấy ffmpeg ngay.

    Trước đây kết quả "chưa có" cũng được nhớ đệm, nên cả phiên chạy tool cứ trả
    lời "máy chưa có ffmpeg" dù file đã nằm trong thư mục ``bin`` của SrtGen.
    Dùng một tên công cụ bịa ra và thư mục tạm: không cài, không tải gì thật.
    """
    empty = tmp_path / "empty"
    app_bin = tmp_path / "SrtGen" / "bin"
    io_utils.ensure_dir(empty)
    io_utils.ensure_dir(app_bin)

    monkeypatch.setattr(s0, "_TOOL_CACHE", {})
    monkeypatch.setattr(s0, "_venv_dirs", lambda: (str(empty),))
    monkeypatch.setattr(s0, "_app_bin_dirs", lambda: (str(app_bin),))
    monkeypatch.setattr(s0, "_SYSTEM_DIRS", ())
    monkeypatch.setenv("PATH", str(empty))

    name = "srtgen-cong-cu-gia"
    assert s0.which_tool(name) is None
    assert name not in s0._TOOL_CACHE

    exe = app_bin / (name + (".exe" if os.name == "nt" else ""))
    io_utils.write_text(exe, "", bom=False)
    os.chmod(exe, 0o755)

    found = s0.which_tool(name)
    assert found is not None
    assert Path(found).resolve() == exe.resolve()
    assert s0._TOOL_CACHE[name] == found
