"""Hợp đồng cặp file `<title>.srt` + `<title>_vi.srt` — build-spec-v2 mục 2.

Cả file này chạy trên **file thật của người dùng** ở ``corpus/pairs/``, không
phải trên dữ liệu bịa. Ba cặp ấy là ba cách dùng khác nhau: phim hoạt hình
(``BonnieBears_tap1``), bản tin dài (``tintuc_ai``), phóng sự (``news_myiran``).

Điều đo được từ chúng, và là lý do hợp đồng viết như hiện nay: **cả ba cặp đều
cùng số block và cùng timestamp tuyệt đối**. Đó không phải một quy ước ai đó
nghĩ ra cho gọn — đó là cách người dùng đang làm việc, và tool phải khớp vào chứ
không được bắt họ đổi.

Một chỗ hai bên khác nhau: ``BonnieBears_tap1_vi.srt`` **có khoảng trắng thừa
cuối dòng**, và đặc tả gọi đó là lỗi của file ấy, không phải chuẩn cần theo. Vì
vậy ``VI_TRAILING_SPACE`` phải bắt được đúng những dòng đó — nếu không thì luật
ấy chưa bao giờ được thử trên dữ liệu thật, và nó sẽ im lặng đúng vào ngày người
dùng cần nó nhất.

Hai chiều luôn được kiểm cùng nhau: luật phải **bắt** file có lỗi, và phải **im
lặng** trên hai cặp file sạch. Một validator kêu ở mọi file cũng vô dụng như một
validator không bao giờ kêu.

Không mạng, không model, không mã API — chỉ đọc file và chạy ``validate_pair``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from srtgen.core.rules import (
    SEVERITY_ERROR,
    SEVERITY_WARN,
    VI_BLOCK_COUNT,
    VI_EMPTY_LINE,
    VI_MARKER_SYNC,
    VI_TIMESTAMP_DRIFT,
    VI_TRAILING_SPACE,
    validate_pair,
    validate_vi_document,
)

#: Ba cặp file thật. Tên đúng như người dùng đang đặt: `<title>.srt` và
#: `<title>_vi.srt` cạnh nhau trong cùng thư mục.
PAIR_NAMES = ("BonnieBears_tap1", "tintuc_ai", "news_myiran")

#: Hai cặp không có lỗi nào — chúng là bằng chứng "validator không kêu bừa".
CLEAN_PAIRS = ("tintuc_ai", "news_myiran")

#: Cue có khoảng trắng thừa trong ``BonnieBears_tap1_vi.srt``, đo trực tiếp từ
#: file. Ghi ra số cue chứ không chỉ ghi "có ít nhất một": một luật bắt nhầm cue
#: khác vẫn thoả điều kiện "có ít nhất một".
BONNIE_TRAILING_SPACE_CUES = frozenset({1, 2, 16, 32, 64})

#: Cue 16 của file ấy còn bị ngắt lời dịch thành hai dòng — sai ràng buộc "đúng
#: 3 dòng mỗi block".
BONNIE_BLOCK_SHAPE_CUES = frozenset({16})


# --------------------------------------------------------------------------- #
# đọc cặp file
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def pairs_dir(corpus_dir: Path) -> Path:
    folder = corpus_dir / "pairs"
    if not folder.is_dir():
        pytest.skip(f"Không tìm thấy thư mục cặp file thật: {folder}")
    return folder


def read_pair(folder: Path, name: str) -> tuple[str, str]:
    """Đọc một cặp, **giữ nguyên văn** kể cả khoảng trắng thừa.

    ``io_utils.read_text`` bóc BOM và quy CRLF về LF nhưng không đụng tới khoảng
    trắng cuối dòng — đúng thứ cần ở đây. Nếu nó dọn hộ thì bài kiểm
    ``VI_TRAILING_SPACE`` sẽ luôn xanh vì lý do sai.
    """
    from srtgen import io_utils

    zh_path = folder / f"{name}.srt"
    vi_path = folder / f"{name}_vi.srt"
    for path in (zh_path, vi_path):
        if not path.is_file():
            pytest.skip(f"Thiếu file cặp: {path}")
    return io_utils.read_text(zh_path), io_utils.read_text(vi_path)


@pytest.fixture(scope="session")
def pairs(pairs_dir: Path) -> dict[str, tuple[str, str]]:
    return {name: read_pair(pairs_dir, name) for name in PAIR_NAMES}


def stamps(srt_text: str) -> list[str]:
    """Mọi dòng mốc thời gian, nguyên văn từng ký tự."""
    return [line for line in srt_text.split("\n") if "-->" in line]


def codes_at(findings, code: str) -> set[int | None]:
    return {f.cue_index for f in findings if f.code == code}


# --------------------------------------------------------------------------- #
# 1. Điều đo được từ ba cặp file thật
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", PAIR_NAMES)
def test_real_pairs_have_the_same_block_count(name: str, pairs: dict) -> None:
    """Ràng buộc cứng số 1, đo trên chính file người dùng đang dùng."""
    from srtgen.core.srt import parse_srt

    zh_text, vi_text = pairs[name]
    zh_blocks = parse_srt(zh_text)
    vi_blocks = parse_srt(vi_text)
    assert vi_blocks, f"{name}: file tiếng Việt không đọc được block nào."
    assert len(vi_blocks) == len(zh_blocks), (
        f"{name}: {len(zh_blocks)} block tiếng Trung nhưng {len(vi_blocks)} block tiếng Việt."
    )


@pytest.mark.parametrize("name", PAIR_NAMES)
def test_real_pairs_share_the_exact_same_timestamps(name: str, pairs: dict) -> None:
    """Ràng buộc cứng số 2: **giống từng ký tự**, cue thứ i ứng cue thứ i.

    So chuỗi thô chứ không so số giây: đặc tả nói "giống từng ký tự", và đó là
    thứ khiến hai file mở song song trong Aegisub không bao giờ lệch một khung
    hình nào.
    """
    zh_text, vi_text = pairs[name]
    zh_stamps, vi_stamps = stamps(zh_text), stamps(vi_text)
    assert len(zh_stamps) == len(vi_stamps)
    for position, (a, b) in enumerate(zip(zh_stamps, vi_stamps), start=1):
        assert a == b, f"{name} cue {position}: '{a}' != '{b}'"


@pytest.mark.parametrize("name", PAIR_NAMES)
def test_validate_pair_finds_no_structural_drift(name: str, pairs: dict) -> None:
    """Không cặp thật nào bị báo lệch số block hay lệch mốc thời gian.

    Đây là chiều "không báo oan" của hai mã nặng nhất trong bảng: chúng ở mức
    ``error``, tức là chúng chặn. Báo oan một lần là người dùng mất niềm tin vào
    cả tab kiểm tra.
    """
    findings = validate_pair(*pairs[name])
    drift = [f for f in findings if f.code in (VI_BLOCK_COUNT, VI_TIMESTAMP_DRIFT)]
    assert not drift, [f"{f.code} @ {f.cue_index}" for f in drift]


@pytest.mark.parametrize("name", CLEAN_PAIRS)
def test_clean_pairs_produce_no_findings_at_all(name: str, pairs: dict) -> None:
    """``tintuc_ai`` và ``news_myiran`` phải đi qua **sạch bong**.

    Hai cặp này là thước đo false positive của cả bộ luật tiếng Việt, giống vai
    của ``completed.srt`` với bộ luật Hán–pinyin. Một finding mới ở đây nghĩa là
    một luật vừa được viết rộng quá tay.
    """
    findings = validate_pair(*pairs[name])
    assert not findings, [f"{f.code} @ {f.cue_index}: {f.message[:90]}" for f in findings]


# --------------------------------------------------------------------------- #
# 2. Khoảng trắng thừa của BonnieBears — ca đặc tả gọi tên
# --------------------------------------------------------------------------- #

def test_trailing_space_is_caught_in_the_real_file(pairs: dict) -> None:
    """``BonnieBears_tap1_vi.srt`` có space thừa cuối dòng — phải bắt đúng những cue đó.

    Đặc tả nêu đích danh file này. Kiểm bằng **tập số cue** chứ không bằng phép
    đếm: một luật bắt nhầm 5 cue khác vẫn cho ra con số 5.
    """
    findings = validate_pair(*pairs["BonnieBears_tap1"])
    caught = codes_at(findings, VI_TRAILING_SPACE)
    assert caught == BONNIE_TRAILING_SPACE_CUES, (
        f"Bắt được cue {sorted(caught)}, mong đợi {sorted(BONNIE_TRAILING_SPACE_CUES)}"
    )


def test_trailing_space_is_an_error_not_a_warning(pairs: dict) -> None:
    """Mức ``error`` theo bảng của build-spec-v2 mục 2.

    Mức quyết định việc file có bị đánh dấu đỏ trên giao diện hay không; hạ
    xuống ``warn`` là lặng lẽ chấp nhận chép lại lỗi của file cũ.
    """
    findings = validate_pair(*pairs["BonnieBears_tap1"])
    trailing = [f for f in findings if f.code == VI_TRAILING_SPACE]
    assert trailing
    assert all(f.severity == SEVERITY_ERROR for f in trailing)


def test_trailing_space_message_names_the_offending_line(pairs: dict) -> None:
    """Thông báo phải bằng tiếng Việt và trích đúng dòng vi phạm.

    Người dùng cuối không phải dân IT: "VI_TRAILING_SPACE at cue 16" không giúp
    họ tìm ra chỗ nào cần sửa, còn một câu tiếng Việt kèm đoạn trích thì có.
    """
    findings = validate_pair(*pairs["BonnieBears_tap1"])
    sample = next(f for f in findings if f.code == VI_TRAILING_SPACE)
    assert "khoảng trắng" in sample.message
    assert sample.line and sample.line != sample.line.strip()


def test_the_same_file_is_clean_once_the_spaces_are_trimmed(pairs: dict) -> None:
    """Cắt sạch khoảng trắng thừa thì cảnh báo phải biến mất — và chỉ nó biến mất.

    Đây là chiều còn lại của bài trên, và nó chứng minh luật đang nhìn đúng thứ
    nó nói: nếu ``VI_TRAILING_SPACE`` thật ra đang bắt một thứ khác thì cắt
    khoảng trắng sẽ không làm nó im.
    """
    zh_text, vi_text = pairs["BonnieBears_tap1"]
    trimmed = "\n".join(line.strip() for line in vi_text.split("\n"))
    findings = validate_pair(zh_text, trimmed)
    assert not codes_at(findings, VI_TRAILING_SPACE)
    # Cue 16 vẫn còn lỗi ngắt dòng — cắt khoảng trắng không sửa hộ chuyện đó.
    assert codes_at(findings, "BLOCK_SHAPE") == BONNIE_BLOCK_SHAPE_CUES


def test_a_multi_line_vietnamese_block_is_reported(pairs: dict) -> None:
    """Ràng buộc cứng số 3: đúng 3 dòng mỗi block, lời dịch không được ngắt đôi.

    Aegisub coi mỗi dòng trong block là một dòng hiển thị, nên một lời dịch bị
    ngắt làm hai sẽ hiện thành hai dòng chồng lên hình — đúng thứ người dùng
    không hề chọn.
    """
    findings = validate_pair(*pairs["BonnieBears_tap1"])
    assert codes_at(findings, "BLOCK_SHAPE") == BONNIE_BLOCK_SHAPE_CUES


# --------------------------------------------------------------------------- #
# 3. Chiều ngược lại: luật phải bắt được lỗi cố tình gây ra
# --------------------------------------------------------------------------- #

def drop_last_block(srt_text: str) -> str:
    """Bỏ block cuối cùng của một file .srt, giữ nguyên phần còn lại."""
    parts = [p for p in srt_text.strip().split("\n\n") if p.strip()]
    return "\n\n".join(parts[:-1]) + "\n\n"


def test_block_count_mismatch_is_caught(pairs: dict) -> None:
    """Thiếu một block ở file tiếng Việt → ``VI_BLOCK_COUNT``.

    Dùng cặp sạch làm nền: sửa đúng một thứ rồi xem đúng một mã nổi lên. Bắt đầu
    từ một file đã có lỗi thì không phân biệt được lỗi mới với lỗi cũ.
    """
    zh_text, vi_text = pairs["tintuc_ai"]
    findings = validate_pair(zh_text, drop_last_block(vi_text))
    assert VI_BLOCK_COUNT in {f.code for f in findings}


def test_block_count_mismatch_does_not_bury_the_user_in_noise(pairs: dict) -> None:
    """Lệch số block chỉ báo **một** lần, không kéo theo hàng trăm dòng lệch mốc.

    Nguyên nhân thật chỉ có một; in ra 299 dòng "lệch mốc thời gian" sẽ chôn nó
    ở giữa, và người dùng cuối sẽ không tìm ra. ``validate_pair`` cố ý chỉ so
    phần đầu chung nhau của hai file.
    """
    zh_text, vi_text = pairs["tintuc_ai"]
    findings = validate_pair(zh_text, drop_last_block(vi_text))
    assert len([f for f in findings if f.code == VI_BLOCK_COUNT]) == 1
    assert not codes_at(findings, VI_TIMESTAMP_DRIFT)


def test_timestamp_drift_is_caught(pairs: dict) -> None:
    """Đổi một mốc thời gian ở file tiếng Việt → ``VI_TIMESTAMP_DRIFT`` đúng cue đó."""
    zh_text, vi_text = pairs["news_myiran"]
    original = stamps(vi_text)[1]
    # Đổi đúng một chữ số mili-giây của mốc kết thúc: lệch nhỏ nhất có thể, và
    # cũng là kiểu lệch dễ lọt nhất khi ai đó sửa tay trong Notepad.
    changed = original[:-1] + ("0" if original[-1] != "0" else "1")
    broken = vi_text.replace(original, changed, 1)
    assert broken != vi_text, "Không thay được mốc thời gian nào — bài kiểm vô nghĩa."

    findings = validate_pair(zh_text, broken)
    drift = [f for f in findings if f.code == VI_TIMESTAMP_DRIFT]
    assert len(drift) == 1
    assert drift[0].severity == SEVERITY_ERROR


def test_an_empty_vietnamese_line_is_a_warning_not_a_deletion(pairs: dict) -> None:
    """Ràng buộc cứng số 8: cue chưa dịch thì **cảnh báo**, không được xoá cue.

    Mức ``warn`` là có chủ ý: một cue chỉ có nhạc nền hoàn toàn có thể để trống
    trong lúc đang dịch dở. Thứ bị cấm là xoá nó đi — và cái bị cấm ấy sẽ hiện
    ra dưới dạng ``VI_BLOCK_COUNT`` mức ``error``, chứ không phải ở đây.
    """
    zh_text, vi_text = pairs["news_myiran"]
    blocks = [p for p in vi_text.strip().split("\n\n") if p.strip()]
    head, stamp, *_ = blocks[2].split("\n")
    blocks[2] = "\n".join([head, stamp, " "])
    findings = validate_pair(zh_text, "\n\n".join(blocks) + "\n\n")

    empty = [f for f in findings if f.code == VI_EMPTY_LINE]
    assert len(empty) == 1
    assert empty[0].severity == SEVERITY_WARN
    assert not codes_at(findings, VI_BLOCK_COUNT), "Cue trống không được biến mất."


def test_marker_out_of_sync_is_a_warning(pairs: dict) -> None:
    """Ràng buộc cứng số 7: marker ``-`` lệch giữa hai file → cảnh báo.

    Chỉ ``warn`` vì chỗ đặt marker giữa dòng phụ thuộc vào cách ngắt câu tiếng
    Việt, mà chỉ người dịch mới biết. Nhưng vẫn phải nói ra: mất marker là mất
    dấu hiệu đổi người nói, và người xem sẽ tưởng cả hai câu là của một người.
    """
    zh_text, vi_text = pairs["news_myiran"]
    blocks = [p for p in vi_text.strip().split("\n\n") if p.strip()]
    head, stamp, text, *rest = blocks[0].split("\n")
    blocks[0] = "\n".join([head, stamp, f"- {text}", *rest])
    findings = validate_pair(zh_text, "\n\n".join(blocks) + "\n\n")

    marker = [f for f in findings if f.code == VI_MARKER_SYNC]
    assert len(marker) == 1
    assert marker[0].severity == SEVERITY_WARN


# --------------------------------------------------------------------------- #
# 4. Kiểm file tiếng Việt một mình
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", CLEAN_PAIRS)
def test_a_clean_vi_file_passes_on_its_own(name: str, pairs: dict) -> None:
    """``validate_vi_document`` dùng khi chỉ có file tiếng Việt trong tay.

    Người dùng mở một file ``_vi.srt`` bên dịch gửi sang mà chưa có file tiếng
    Trung tương ứng là chuyện thường; đường đó phải cho cùng kết luận với đường
    kiểm theo cặp, chỉ thiếu ba mã cần đến file kia.
    """
    findings = validate_vi_document(pairs[name][1])
    assert not findings, [f"{f.code} @ {f.cue_index}" for f in findings]


def test_vi_only_check_still_sees_the_trailing_spaces(pairs: dict) -> None:
    """Không có file tiếng Trung vẫn phải bắt được khoảng trắng thừa.

    ``VI_TRAILING_SPACE`` là luật nội tại của một dòng, không cần đối chiếu; nếu
    nó chỉ nổi lên ở đường kiểm theo cặp thì tab "Kiểm tra file" sẽ bỏ sót đúng
    thứ nó sinh ra để bắt.
    """
    findings = validate_vi_document(pairs["BonnieBears_tap1"][1])
    assert codes_at(findings, VI_TRAILING_SPACE) == BONNIE_TRAILING_SPACE_CUES


def test_vietnamese_punctuation_is_left_alone(pairs: dict) -> None:
    """Ràng buộc cứng số 6: dòng tiếng Việt dùng dấu ASCII, **không** áp luật tiếng Trung.

    Ba cặp file thật đầy dấu ``,`` ``.`` ``?`` — nếu luật ``ASCII_PUNCT`` của
    file Hán–pinyin lỡ chạy sang đây thì mỗi câu tiếng Việt sẽ thành một lỗi, và
    tính năng này vô dụng ngay từ ngày đầu.
    """
    for name in PAIR_NAMES:
        findings = validate_vi_document(pairs[name][1])
        assert "ASCII_PUNCT" not in {f.code for f in findings}, name
