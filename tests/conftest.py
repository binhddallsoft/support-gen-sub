"""Fixture dùng chung cho toàn bộ bộ kiểm thử.

Nguyên tắc bao trùm (build-spec mục 13): **mọi test ở đây chạy được khi máy không
có mạng, không có model Whisper, không có mã API**. Bất cứ thứ gì cần ba điều đó
phải mang dấu ``@pytest.mark.slow`` và bị bỏ qua trừ khi chạy với ``--runslow``.
Lý do không phải là sự sạch sẽ hình thức: một bộ test cần mạng sẽ đỏ vì lý do
không liên quan gì đến code, và một bộ test hay đỏ vô cớ là bộ test không ai chạy.

Ba corpus có sẵn, và chúng đóng ba vai khác nhau:

* ``corpus/raw.srt``       — 3 dòng/block, chỉ có chữ Hán dính liền. Đây là thứ
  gần nhất với đầu ra của Whisper mà không cần chạy Whisper, nên nó là đầu vào
  của bài kiểm end-to-end ngoại tuyến.
* ``corpus/filter.srt``    — 4 dòng/block, đã có pinyin nhưng chưa chuẩn hoá
  (toàn chữ thường, tách cụm khác, 儿化音 viết dạng đầy đủ). Đây là "file bên
  dịch gửi sang" mà lệnh ``fix`` phải xử lý được.
* ``corpus/completed.srt`` — bản người biên tập đã soát tay, dùng làm thước đo
  chất lượng. Lưu ý: nó KHÔNG phải chuẩn tuyệt đối, xem ``known_corpus_errors``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

# Cho phép chạy `pytest` từ bất kỳ thư mục nào mà vẫn `import srtgen` được.
# pytest đã tự chèn thư mục gốc khi `tests/` là package, nhưng người dùng cuối
# hay chạy bằng đủ kiểu lệnh khác nhau nên rẻ hơn là tự bảo đảm ở đây.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CORPUS_DIR = REPO_ROOT / "corpus"
COMPLETED_SRT = CORPUS_DIR / "completed.srt"
FILTER_SRT = CORPUS_DIR / "filter.srt"
RAW_SRT = CORPUS_DIR / "raw.srt"


# --------------------------------------------------------------------------- #
# tuỳ chọn dòng lệnh
# --------------------------------------------------------------------------- #

def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="Chạy cả những test cần mạng / model Whisper / mã API (mặc định bỏ qua).",
    )


def pytest_configure(config: pytest.Config) -> None:
    """Khai báo marker tại đây thay vì trong ``pyproject.toml``.

    ``pyproject.toml`` là file chung của cả dự án; khai báo ở conftest giữ cho
    mọi thứ thuộc về bộ test nằm trong ``tests/``.
    """
    config.addinivalue_line(
        "markers",
        "slow: cần mạng, model Whisper hoặc mã API — chỉ chạy khi có --runslow.",
    )
    config.addinivalue_line(
        "markers",
        "corpus: đọc file trong corpus/ — bỏ qua khi thư mục corpus vắng mặt.",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--runslow"):
        return
    skip = pytest.mark.skip(reason="cần mạng/model/API key — thêm --runslow để chạy")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


# --------------------------------------------------------------------------- #
# đường dẫn corpus
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def corpus_dir() -> Path:
    if not CORPUS_DIR.is_dir():
        pytest.skip(f"Không tìm thấy thư mục corpus: {CORPUS_DIR}")
    return CORPUS_DIR


def _read(path: Path) -> str:
    """Đọc qua ``io_utils`` chứ không qua ``open()``.

    Không phải để tuân thủ hình thức: ``io_utils.read_text`` bóc BOM, quy CRLF về
    LF và chuẩn hoá NFC. Test đọc bằng ``open()`` sẽ so sánh một chuỗi NFD với
    một chuỗi NFC và thất bại vì lý do không liên quan tới thứ đang kiểm.
    """
    from srtgen import io_utils

    if not path.is_file():
        pytest.skip(f"Không tìm thấy file corpus: {path}")
    return io_utils.read_text(path)


@pytest.fixture(scope="session")
def completed_text(corpus_dir: Path) -> str:
    """Bản đã soát tay — thước đo, không phải chuẩn tuyệt đối."""
    return _read(COMPLETED_SRT)


@pytest.fixture(scope="session")
def filter_text(corpus_dir: Path) -> str:
    """Bản chưa chuẩn hoá — đầu vào của lệnh ``fix``."""
    return _read(FILTER_SRT)


@pytest.fixture(scope="session")
def raw_text(corpus_dir: Path) -> str:
    """Bản 3 dòng, chỉ chữ Hán — đầu vào của bài end-to-end ngoại tuyến."""
    return _read(RAW_SRT)


# --------------------------------------------------------------------------- #
# cấu hình và nhà cung cấp AI
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def cfg() -> dict[str, Any]:
    """Cấu hình mặc định (profile ``drama``), đọc từ ``srtgen/config/default.yaml``.

    Session-scope vì việc đọc + merge cấu hình là thuần tuý đọc; test nào cần đổi
    giá trị thì tự ``dict(cfg)`` một bản riêng thay vì sửa bản dùng chung.
    """
    from srtgen.core.context import load_config

    return load_config()


@pytest.fixture
def null_provider() -> Any:
    """Nhà cung cấp AI mặc định: trả ``{}``, không chạm vào mạng.

    Đây là thứ làm cho toàn bộ pipeline chạy được ngoại tuyến mà vẫn đi đúng
    nhánh code như khi có AI thật — chỉ khác ở chỗ danh sách đề xuất rỗng.
    """
    from srtgen.providers.null import NullProvider

    return NullProvider(reason="Chạy trong bộ kiểm thử, không gọi mạng.")


# --------------------------------------------------------------------------- #
# lệnh `fix`, dựng từ các mảnh có sẵn
# --------------------------------------------------------------------------- #

def fix_srt_text(
    text: str,
    cfg: Mapping[str, Any] | None = None,
    *,
    names: Mapping[str, str] | None = None,
) -> Any:
    """Chuẩn hoá một file .srt người khác gửi sang, trả về ``Document`` của file kết quả.

    Hàm này **chỉ gọi** ``pipeline.run_fix`` — đúng hàm mà lệnh ``srtgen fix`` và
    ``/api/fix`` gọi. Trước đây nó là một bản chép tay các bước của lệnh fix (đặt
    ``meta["segmented"] = True`` cho cả file, biến điệu cả pinyin người biên tập),
    nên cho ra file khác với lệnh thật ở 15 cue ``bù``/``bú`` của filter.srt: bộ test
    đo một thứ mà người dùng không bao giờ nhận được. Một hàm, một logic.

    ``Document`` trả về là file kết quả đọc lại nguyên văn (``normalize=False``),
    để test nào cần soi token vẫn có, mà không có con đường xử lý thứ hai nào.
    """
    from srtgen.core.srt import parse_srt_document
    from srtgen.pipeline import run_fix

    fixed, _findings = run_fix(text, dict(cfg) if cfg is not None else None, names=names)
    return parse_srt_document(fixed)


@pytest.fixture
def fix_srt() -> Callable[..., Any]:
    """Cho test gọi ``fix_srt(text, cfg)`` mà không phải tự import."""
    return fix_srt_text


@pytest.fixture(scope="session")
def fixed_filter_srt(filter_text: str, cfg: dict[str, Any]) -> str:
    """``run_fix(corpus/filter.srt)`` chạy đúng một lần cho cả phiên — đầu vào của cổng cứng.

    Lấy thẳng văn bản ``run_fix`` trả về, không đọc rồi ghi lại: cổng phải đo
    đúng từng byte người dùng nhận được.
    """
    from srtgen.pipeline import run_fix

    fixed, _findings = run_fix(filter_text, cfg)
    return fixed


@pytest.fixture(scope="session")
def fixed_filter_doc(fixed_filter_srt: str) -> Any:
    """``Document`` của file ``fix(corpus/filter.srt)`` — **chỉ đọc**.

    Dựng từ chính văn bản của ``fixed_filter_srt`` nên hai fixture không thể lệch
    nhau. Test nào cần biến đổi thì tự dựng bản riêng bằng fixture ``fix_srt``.
    """
    from srtgen.core.srt import parse_srt_document

    return parse_srt_document(fixed_filter_srt)


# --------------------------------------------------------------------------- #
# tài liệu đã dựng sẵn, dùng lại cho nhiều test
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="session")
def completed_doc(completed_text: str) -> Any:
    """``completed.srt`` đọc nguyên văn (``normalize=False``) — chỉ đọc.

    Không bật ``normalize`` vì mọi phép round-trip đều cần đọc lại đúng từng ký
    tự; bật lên là tự sửa dữ liệu rồi so với chính bản đã sửa.
    """
    from srtgen.core.srt import parse_srt_document

    return parse_srt_document(completed_text)


@pytest.fixture(scope="session")
def completed_findings(completed_text: str) -> list[Any]:
    """Kết quả ``validate_text`` trên bản đã soát tay — chỉ đọc."""
    from srtgen.core.rules import validate_text

    return validate_text(completed_text)
