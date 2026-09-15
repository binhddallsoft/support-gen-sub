"""Tab Hướng dẫn phải nói đúng tên những nút đang có thật trên màn hình.

Trang hướng dẫn viết cho người không rành máy tính, và họ làm theo từng chữ:
"bấm **＋ Thêm dòng**". Nếu một ngày nút đó đổi tên thành "Chèn dòng" mà trang
hướng dẫn vẫn ghi tên cũ, người đọc sẽ đứng nhìn màn hình tìm một nút không tồn
tại. Test này bắt đúng chuyện đó: mỗi nhãn nút mà hướng dẫn nhắc tới phải còn
xuất hiện ở ĐÂU ĐÓ khác trong giao diện (index.html ngoài tab hướng dẫn, hoặc
app.js — vài nút do app.js dựng ra lúc chạy).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "srtgen" / "web" / "static"


@pytest.fixture(scope="module")
def html() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def guide(html: str) -> str:
    start = html.index('<section class="panel" id="tab-guide"')
    end = html.index("</section>\n\n</main>", start)
    return html[start:end]


@pytest.fixture(scope="module")
def ngoai_huong_dan(html: str, guide: str, app_js: str) -> str:
    return html.replace(guide, "") + app_js


def test_co_tab_huong_dan(html: str) -> None:
    assert 'data-tab="guide"' in html
    assert 'id="tab-guide"' in html
    assert re.search(r'data-tab="guide">\s*<svg[^>]*>.*?</svg><span>Hướng dẫn</span>', html, re.S), (
        "tab phải ghi đúng chữ “Hướng dẫn”"
    )


def test_mo_lai_trang_van_nho_tab_huong_dan(app_js: str) -> None:
    assert "'settings', 'guide'].indexOf(tab)" in app_js


def test_moi_muc_luc_tro_toi_mot_muc_that(guide: str) -> None:
    hrefs = re.findall(r'class="toc-link[^"]*" href="#([a-z0-9-]+)"', guide)
    assert len(hrefs) >= 10
    for anchor in hrefs:
        assert f'id="{anchor}"' in guide, f"mục lục trỏ tới #{anchor} nhưng không có mục đó"


@pytest.mark.parametrize(
    "nhan",
    [
        "Bắt đầu",
        "Chọn file từ máy",
        "Dừng",
        "Xem trước và sửa",
        "Mở thư mục",
        "Làm video khác",
        "Chọn file .srt…",
        "＋ Thêm dòng",
        "Tách tại con trỏ",
        "Gộp với dòng sau",
        "Xoá dòng",
        "✓ Xong, dòng sau",
        "▶ Nghe dòng",
        "Bắt đầu = vị trí video",
        "Kết thúc = vị trí video",
        "Sinh lại pinyin",
        "Dịch lại câu này",
        "Hoàn nguyên",
        "Tìm chữ trong phụ đề…",
        "Có lỗi",
        "AI chưa duyệt",
        "Chưa có tiếng Việt",
        "Chưa soát",
        "Chỉ kiểm tra",
        "Sửa và tải về",
        "Thêm tên",
        "Lưu bảng tên",
        "Chạy lại với bảng tên mới",
        "Kiểm tra key",
        "Lưu cài đặt",
        "Dịch lại toàn bộ tiếng Việt",
        "Quay lại điền nốt",
        "Cập nhật yt-dlp",
    ],
)
def test_nhan_nut_trong_huong_dan_con_ton_tai_tren_man_hinh(
    nhan: str, guide: str, ngoai_huong_dan: str
) -> None:
    assert nhan in guide, f"hướng dẫn không còn nhắc tới nút “{nhan}”"
    assert nhan in ngoai_huong_dan, (
        f"hướng dẫn bảo bấm “{nhan}” nhưng giao diện không còn nút nào tên như vậy"
    )


def test_huong_dan_mo_ngay_trong_app_khong_sang_the_moi(app_js: str) -> None:
    start = app_js.index("function openGuide(")
    body = app_js[start : start + 900]
    assert "switchTab('guide')" in body
    assert "target: '_blank'" not in body


def test_hai_muc_luc_sang_rieng_khong_tat_den_cua_nhau(app_js: str) -> None:
    """Tab Cài đặt và tab Hướng dẫn dùng chung khung mục lục; phải gắn riêng từng khung."""
    assert "document.querySelectorAll('.settings-layout').forEach(wireOneToc)" in app_js
    assert "root.querySelectorAll('.toc-link')" in app_js
