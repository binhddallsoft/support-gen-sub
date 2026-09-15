"""TẦNG 1 — bất biến của renderer. Đây là tầng quan trọng nhất của bộ kiểm thử.

Vì sao quan trọng nhất: nguyên tắc số 1 của build-spec là "lệch số cụm giữa dòng
Hán và dòng pinyin phải là **bất khả thi về cấu trúc**, không phải thứ đi kiểm rồi
sửa". Một bất biến cấu trúc chỉ đáng tin khi có ai đó thực sự đi thử phá nó — nên
ở đây ta sinh vài nghìn token list ngẫu nhiên, cố tình đặt ``marker``/``punct`` ở
mọi vị trí kể cả đầu và cuối dòng, rồi đòi ba điều luôn đúng:

1. số cụm đếm được trên dòng Hán == số cụm trên dòng pinyin == ``word_count``;
2. dòng render không có space thừa (đầu dòng, cuối dòng, hai space liền);
3. ``tokenize_line(render_zh(t))`` trả lại đúng dãy ``(kind, zh)`` ban đầu.

Dùng ``random.Random(seed)`` chứ không dùng ``hypothesis``: bộ test phải chạy
được trên máy iMac của người dùng cuối chỉ với ``pip install pytest``, và một ca
lỗi tái hiện được bằng cách gõ lại đúng seed thì dễ đem đi báo lỗi hơn nhiều so
với một ca sinh ngẫu nhiên mỗi lần một khác.
"""

from __future__ import annotations

import random

import pytest

from srtgen.core.srt import tokenize_line
from srtgen.core.token import (
    KIND_MARKER,
    KIND_PUNCT,
    KIND_WORD,
    Token,
    render_py,
    render_tokens,
    render_zh,
    word_count,
)

# Seed cố định: mọi lần chạy sinh đúng cùng một bộ ca, nên khi test đỏ thì con số
# trong thông báo lỗi tái hiện được y nguyên trên máy người khác.
SEED = 20260910

#: Số token list sinh ra cho mỗi bài fuzz ("vài nghìn trường hợp").
CASES = 4000

HAN = "好我你他她们的了是不很大小天地人山水火木金土学中国话说走来去看听吃喝箱子扶起"

#: Pinyin thật, cố tình gồm cả những ca khó: 儿化音 rút gọn, dấu nháy phân âm
#: tiết (``Xī'ān``), và từ mượn viết hoa toàn bộ (``PK``) — cả ba đều là thứ đã
#: từng làm gãy các bộ tách chuỗi ngây thơ.
PINYIN = [
    "hǎo", "wǒ", "nǐ", "tā", "men", "de", "le", "shì", "bù", "hěn", "dà",
    "xiǎo", "Nǎr", "zhèr", "yíhuìr", "Xī'ān", "OK", "PK", "xiāngzi", "qǐlái",
]

#: Dấu câu một ký tự. Cố ý KHÔNG có ``…`` và ``—`` lẻ: README cấm chúng (phải là
#: ``……`` và ``——``), nên chúng không được phép tồn tại trong một token list hợp lệ.
SINGLE_PUNCT = ["。", "，", "、", "？", "！", "：", "；", "《", "》", "（", "）", "“", "”", "‘", "’"]

#: Dấu nhiều ký tự. Hai dấu này gộp khi đứng liền nhau (``……`` + ``……`` đọc lại
#: thành một ``…………``), nên bộ sinh không đặt hai dấu cùng họ cạnh nhau — xem
#: ``_random_tokens``.
RUN_PUNCT = ["……", "——"]

ALL_PUNCT = SINGLE_PUNCT + RUN_PUNCT


def _random_tokens(rng: random.Random, *, allow_empty_word: bool = False) -> list[Token]:
    """Sinh một token list ngẫu nhiên, có chủ ý nghiêng về các ca biên.

    Một ràng buộc duy nhất được áp: không đặt hai dấu ``……`` (hoặc hai ``——``)
    cạnh nhau. Không phải để né lỗi — mà vì hai token như vậy render ra
    ``…………``, và khi đọc lại thì ``tokenize_line`` gộp chúng thành **một** dấu.
    Việc gộp đó là hành vi đúng (một file thật không bao giờ chứa bốn chấm lửng
    liền nhau như hai dấu riêng biệt), nên ép round-trip qua ca đó chỉ là tự bịa
    ra một yêu cầu mà README không hề đặt ra.
    """
    tokens: list[Token] = []
    previous_run = ""
    opens_with_marker = False   # dòng có MỞ ĐẦU bằng marker không
    only_opening_so_far = True  # tới giờ mới chỉ gặp dấu mở `《（“‘`
    last_kind = ""
    for _ in range(rng.randint(0, 14)):
        roll = rng.random()
        if roll < 0.58:
            zh = "".join(rng.choice(HAN) for _ in range(rng.randint(1, 3)))
            if allow_empty_word and rng.random() < 0.08:
                zh = ""
            tokens.append(
                Token(kind=KIND_WORD, zh=zh, pinyin="" if not zh else rng.choice(PINYIN))
            )
            # Một token rỗng không phát ra ký tự nào, nên nó KHÔNG chia tách được
            # hai dấu cùng họ đứng hai bên nó: phải giữ nguyên `previous_run`.
            if zh.strip():
                previous_run = ""
                last_kind = KIND_WORD
                only_opening_so_far = False
        elif roll < 0.85:
            value = rng.choice(ALL_PUNCT)
            if value in RUN_PUNCT and value == previous_run:
                value = rng.choice(SINGLE_PUNCT)
            previous_run = value if value in RUN_PUNCT else ""
            tokens.append(Token(kind=KIND_PUNCT, zh=value))
            last_kind = KIND_PUNCT
            if value not in "《（“‘":
                only_opening_so_far = False
        else:
            # Marker chỉ được đặt ở nơi hợp đồng cho phép: đầu dòng, ngay sau một
            # dấu câu, hoặc trong dòng ĐÃ mở đầu bằng marker (ca `- A - B` mà
            # README nêu làm ví dụ đúng). Đặt marker sau một CHỮ ở dòng chưa có
            # marker nào thì theo README đó là dấu ngắt lời `——`, nên `tokenize_line`
            # đọc lại sẽ ra `punct` — ép round-trip qua ca đó là tự bịa ra một yêu
            # cầu trái với README, chứ không phải bắt được lỗi của renderer.
            if not tokens or last_kind == KIND_PUNCT or opens_with_marker:
                tokens.append(Token(kind=KIND_MARKER, zh="-"))
                if only_opening_so_far:
                    opens_with_marker = True
                only_opening_so_far = False
                previous_run = ""
                last_kind = KIND_MARKER
    return tokens


def _clusters(line: str) -> int:
    """Đếm cụm từ vựng của một dòng đã render, bằng chính bộ tách của tool.

    Đếm bằng ``line.split()`` sẽ sai ngay ở ca đầu tiên có dấu câu dính vào chữ
    (``好，唱得`` là hai cụm nhưng ``split`` thấy một), mà đó lại chính là hình
    dạng bình thường nhất của tiếng Trung.
    """
    return sum(1 for t in tokenize_line(line) if t.kind == KIND_WORD)


# --------------------------------------------------------------------------- #
# bất biến 1 — hai dòng luôn cùng số cụm
# --------------------------------------------------------------------------- #

def test_cluster_count_matches_on_both_lines() -> None:
    """README mục 1: bỏ dấu câu và marker ra thì hai dòng phải cùng số cụm.

    Đây là bất biến mà toàn bộ kiến trúc "một token list, hai dòng render" tồn
    tại để bảo đảm. Nếu nó gãy dù chỉ một lần trên 4000 ca thì lỗi nằm ở
    renderer, không phải ở dữ liệu — và mọi thứ dựng trên nó đều mất giá trị.
    """
    rng = random.Random(SEED)
    for case in range(CASES):
        tokens = _random_tokens(rng)
        zh_line = render_zh(tokens)
        py_line = render_py(tokens)
        expected = word_count(tokens)
        assert _clusters(zh_line) == _clusters(py_line) == expected, (
            f"ca #{case} (seed={SEED}) lệch số cụm:\n"
            f"  mong đợi {expected}\n"
            f"  dòng Hán   ({_clusters(zh_line)}): {zh_line!r}\n"
            f"  dòng pinyin ({_clusters(py_line)}): {py_line!r}"
        )


# --------------------------------------------------------------------------- #
# bất biến 2 — không có khoảng trắng thừa
# --------------------------------------------------------------------------- #

def test_no_stray_whitespace() -> None:
    """README mục 2 và 5 — không space đầu/cuối dòng, không hai space liền.

    Kiểm cả với token mang nội dung rỗng (``allow_empty_word``): một token rỗng
    là khiếm khuyết dữ liệu, nhưng nó KHÔNG được phép biến thành khoảng trắng
    thừa trong file xuất ra. Bảo đảm phải đúng với mọi token list, không chỉ với
    token list hợp lệ — nếu không thì nó là ước nguyện chứ không phải bất biến.
    """
    rng = random.Random(SEED + 1)
    for case in range(CASES):
        tokens = _random_tokens(rng, allow_empty_word=True)
        for field, line in (("zh", render_zh(tokens)), ("pinyin", render_py(tokens))):
            assert line == line.strip(), (
                f"ca #{case} ({field}) có khoảng trắng ở đầu hoặc cuối dòng: {line!r}"
            )
            assert "  " not in line, (
                f"ca #{case} ({field}) có hai khoảng trắng liền: {line!r}"
            )
            assert "\t" not in line and "\n" not in line, (
                f"ca #{case} ({field}) chứa ký tự điều khiển: {line!r}"
            )


# --------------------------------------------------------------------------- #
# bất biến 3 — round-trip
# --------------------------------------------------------------------------- #

def test_round_trip_through_tokenize_line() -> None:
    """``tokenize_line(render_zh(t))`` phải trả lại đúng dãy ``(kind, zh)``.

    Đây là điều kiện để lệnh ``fix`` không phá file: nó đọc file người khác gửi
    bằng ``tokenize_line``, sửa trên token, rồi render lại. Nếu vòng đọc–ghi
    không đóng kín thì mỗi lần chạy ``fix`` lại làm file lệch đi thêm một chút.

    Token có nội dung rỗng bị loại khỏi phép so vì renderer bỏ qua hẳn chúng
    (xem ``test_no_stray_whitespace``) — không phát ra ký tự nào thì đọc lại
    cũng không thể thấy lại chúng.
    """
    rng = random.Random(SEED + 2)
    for case in range(CASES):
        tokens = _random_tokens(rng, allow_empty_word=True)
        line = render_zh(tokens)
        before = [(t.kind, t.zh) for t in tokens if t.zh.strip()]
        after = [(t.kind, t.zh) for t in tokenize_line(line)]
        assert before == after, (
            f"ca #{case} (seed={SEED + 2}) round-trip lệch:\n"
            f"  dòng : {line!r}\n"
            f"  vào  : {before}\n"
            f"  ra   : {after}"
        )


# --------------------------------------------------------------------------- #
# các ca cụ thể lấy thẳng từ README
# --------------------------------------------------------------------------- #

def _tokens(*spec: tuple[str, str, str | None]) -> list[Token]:
    return [Token(kind=k, zh=z, pinyin=p) for k, z, p in spec]


W, P, M = KIND_WORD, KIND_PUNCT, KIND_MARKER


@pytest.mark.parametrize(
    "tokens, zh_line, py_line",
    [
        # README "Yêu cầu đối với backend" — ví dụ bundle mẫu.
        (
            _tokens((W, "好", "Hǎo"), (P, "，", None), (W, "唱得", "chàngdé"),
                    (W, "真好", "zhēnhǎo")),
            "好，唱得 真好",
            "Hǎo，chàngdé zhēnhǎo",
        ),
        # README mục 3 — marker giữa hai phần thoại, đúng 1 space mỗi bên.
        (
            _tokens((M, "-", None), (W, "我", "wǒ"), (W, "没有", "méiyǒu"),
                    (M, "-", None), (W, "你", "nǐ"), (W, "说", "shuō"),
                    (W, "这", "zhè"), (P, "……", None)),
            "- 我 没有 - 你 说 这……",
            "- wǒ méiyǒu - nǐ shuō zhè……",
        ),
        # README mục 1 — 儿化音 nằm trong cùng một cụm.
        (
            _tokens((W, "住", "zhù"), (W, "哪儿", "nǎr")),
            "住 哪儿",
            "zhù nǎr",
        ),
        # README mục 3 — dấu tên tác phẩm ôm cả hai đầu dòng.
        (
            _tokens((P, "《", None), (W, "不愧", "Bùkuì"), (W, "是", "shì"),
                    (W, "顶级", "dǐngjí"), (W, "女", "nǚ"), (W, "保镖", "bǎobiāo"),
                    (P, "》", None)),
            "《不愧 是 顶级 女 保镖》",
            "《Bùkuì shì dǐngjí nǚ bǎobiāo》",
        ),
        # Dấu ngắt lời ``——`` dính vào cả hai bên, không phải marker.
        (
            _tokens((W, "美国", "Měiguó"), (P, "——", None), (W, "纽约", "Niǔyuē")),
            "美国——纽约",
            "Měiguó——Niǔyuē",
        ),
    ],
)
def test_readme_examples(tokens: list[Token], zh_line: str, py_line: str) -> None:
    """Các ví dụ viết thẳng trong README phải render ra đúng từng ký tự."""
    assert render_zh(tokens) == zh_line
    assert render_py(tokens) == py_line


def test_marker_after_sentence_ender_keeps_one_space() -> None:
    """Ngoại lệ cấu trúc của README mục 3, và là nguồn của 11 ca "corpus sai".

    Luật chung cấm space sau ``。``; luật marker đòi 1 space trước ``-``. README
    nói rõ marker thắng. Ghi lại thành test riêng để không ai "tối ưu" mất nó khi
    thấy corpus viết ``。-``.
    """
    tokens = _tokens(
        (W, "起来", "qǐlái"), (P, "。", None), (M, "-", None), (W, "我", "Wǒ")
    )
    assert render_zh(tokens) == "起来。 - 我"
    assert render_py(tokens) == "qǐlái。 - Wǒ"


def test_marker_at_line_start_has_no_leading_space() -> None:
    """Marker mở đầu dòng chỉ có space bên phải — nếu không sẽ vi phạm README mục 2."""
    tokens = _tokens((M, "-", None), (W, "好", "Hǎo"))
    line = render_zh(tokens)
    assert line == "- 好"
    assert line == line.strip()


@pytest.mark.parametrize(
    "tokens, expected",
    [
        # Marker đứng cuối dòng: space bên phải không được phép rơi ra ngoài.
        (_tokens((W, "好", "hǎo"), (M, "-", None)), "好 -"),
        # Hai marker liền nhau: vẫn không sinh ra hai space liền.
        (_tokens((M, "-", None), (M, "-", None), (W, "好", "hǎo")), "- - 好"),
        # Chỉ có mỗi marker.
        (_tokens((M, "-", None)), "-"),
        # Token rỗng giữa câu bị bỏ qua hẳn, không để lại khoảng trắng.
        (_tokens((W, "好", "hǎo"), (W, "", ""), (W, "啊", "a")), "好 啊"),
        # Marker rồi tới dấu câu.
        (_tokens((M, "-", None), (P, "。", None)), "- 。"),
        # Danh sách rỗng.
        ([], ""),
    ],
)
def test_degenerate_token_lists(tokens: list[Token], expected: str) -> None:
    """Các ca bệnh hoạn: dữ liệu hỏng vẫn không được sinh ra file hỏng.

    Những token list này không xuất hiện trong file thật, nhưng chúng xuất hiện
    khi một chặng có bug. Renderer phải giữ hai bảo đảm khoảng trắng kể cả lúc
    đó, để lỗi lộ ra ở chỗ nó sinh ra thay vì lộ ra ở Aegisub của người dùng.
    """
    line = render_zh(tokens)
    assert line == expected
    assert line == line.strip()
    assert "  " not in line


def test_pinyin_falls_back_to_hanzi_when_missing() -> None:
    """``pinyin=None`` phải đổ về chữ Hán, không được thành chuỗi rỗng.

    Đây là hành vi mà các cue ``CUM_MISMATCH`` dựa vào: chưa biết pinyin thì thà
    hiện chữ Hán ở dòng 4 để người soát nhìn thấy ngay, còn hơn để mất cụm khiến
    hai dòng lệch nhau — mà lệch nhau thì mọi phép kiểm phía sau đều vô nghĩa.
    """
    tokens = _tokens((W, "好", None), (P, "，", None), (W, "唱得", "chàngdé"))
    assert render_py(tokens) == "好，chàngdé"
    assert _clusters(render_py(tokens)) == word_count(tokens)


def test_render_tokens_rejects_unknown_field() -> None:
    """Gọi sai tên field phải nổ ngay, kèm câu tiếng Việt cho người dùng cuối."""
    with pytest.raises(ValueError) as err:
        render_tokens([Token(kind=KIND_WORD, zh="好", pinyin="hǎo")], "hanzi")
    assert str(err.value).strip()
