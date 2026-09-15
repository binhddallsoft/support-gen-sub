"""Đóng gói SrtGen thành một file zip gửi cho người dùng máy Mac.

Chạy từ thư mục gốc dự án:

    python tools/make_release.py

Kết quả: ``dist/SrtGen-<phiên bản>-macOS.zip``. Người nhận chỉ cần giải nén rồi làm theo
``HUONG-DAN.html`` (bấm đúp là mở bằng Safari).

Vì sao cần script riêng mà không nén tay thư mục:

* **Quyền chạy của file ``.command``.** Zip tạo trên Windows ghi hệ điều hành gốc là MS-DOS,
  nên trình giải nén của macOS bỏ qua bit quyền Unix và mọi file ``.command`` mất quyền chạy.
  Người dùng bấm đúp ``CaiDat.command`` sẽ bị báo "không có quyền" ngay bước đầu tiên. Script
  này ghi ``create_system = 3`` (Unix) và ``0o755`` cho từng file ``.command``.
* **Ký tự xuống dòng.** Một file ``.command`` có CRLF chết ngay dòng shebang với lỗi
  ``/bin/bash^M: bad interpreter``. Mọi file văn bản được đưa về LF trong gói (không sửa
  file trong thư mục dự án).
* **Chỉ gửi thứ người dùng cần.** Không gửi ``tests/``, ``corpus/``, ``docs/`` hay
  ``__pycache__``. Nhưng PHẢI có ``pyproject.toml`` và ``README.md``: bộ cài chạy
  ``pip install --editable`` và ``pyproject.toml`` khai ``readme = "README.md"``, thiếu một
  trong hai là bước cài thư viện hỏng.

Đây là công cụ của người phát triển, nằm ngoài gói ``srtgen``, nên luật "chỉ io_utils được
gọi open()" của gói không áp ở đây.
"""

from __future__ import annotations

import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
TOP = "SrtGen"

INCLUDE_DIRS = ("srtgen", "installer")
INCLUDE_FILES = ("SrtGen.command", "HUONG-DAN.md", "README.md", "pyproject.toml")
SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".DS_Store"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
TEXT_SUFFIXES = {
    ".command", ".sh", ".py", ".md", ".txt", ".toml", ".yaml", ".yml",
    ".json", ".html", ".js", ".css", ".cfg", ".ini",
}
EXECUTABLE_SUFFIXES = {".command", ".sh"}

# File mà thiếu thì gói không cài được — kiểm lại sau khi đóng.
REQUIRED = (
    "SrtGen.command",
    "installer/CaiDat.command",
    "installer/KhoiDong.command",
    "installer/GoCaiDat.command",
    "installer/requirements-macos.txt",
    "pyproject.toml",
    "README.md",
    "HUONG-DAN.md",
    "srtgen/__init__.py",
    "srtgen/config/default.yaml",
    "srtgen/web/static/index.html",
    "srtgen/web/static/app.js",
)

BOM = b"\xef\xbb\xbf"


def read_version() -> str:
    """Đọc __version__ bằng regex để không phải import gói (tránh kéo theo thư viện nặng)."""
    text = (ROOT / "srtgen" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    return m.group(1) if m else "0.0.0"


def iter_sources() -> list[Path]:
    files: list[Path] = [ROOT / name for name in INCLUDE_FILES]
    for d in INCLUDE_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if not p.is_file():
                continue
            if any(part in SKIP_DIR_NAMES for part in p.relative_to(ROOT).parts):
                continue
            if p.suffix.lower() in SKIP_SUFFIXES:
                continue
            files.append(p)
    return files


def normalize(rel: str, data: bytes, warnings: list[str]) -> bytes:
    """Đưa file văn bản về LF; với file chạy được thì bỏ BOM để shebang nằm đúng byte đầu."""
    suffix = Path(rel).suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        return data
    if b"\r\n" in data:
        data = data.replace(b"\r\n", b"\n")
        warnings.append(f"đã đổi CRLF -> LF: {rel}")
    if suffix in EXECUTABLE_SUFFIXES:
        if data.startswith(BOM):
            data = data[len(BOM):]
            warnings.append(f"đã bỏ BOM (BOM làm hỏng dòng shebang): {rel}")
        if not data.startswith(b"#!"):
            raise SystemExit(f"LỖI: {rel} không bắt đầu bằng dòng shebang '#!' — macOS sẽ không chạy được.")
    return data


def zip_info(rel: str, mtime: float) -> zipfile.ZipInfo:
    t = time.localtime(max(mtime, 315532800))  # zip không ghi được ngày trước 1980
    info = zipfile.ZipInfo(f"{TOP}/{rel}", date_time=t[:6])
    info.create_system = 3  # Unix: bắt buộc để macOS đọc bit quyền bên dưới
    mode = 0o100755 if Path(rel).suffix.lower() in EXECUTABLE_SUFFIXES else 0o100644
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def render_guide_html(warnings: list[str]) -> bytes | None:
    """Dựng HUONG-DAN.html bằng đúng bộ chuyển của trang /guide, để hai nơi hiển thị như nhau."""
    try:
        sys.path.insert(0, str(ROOT))
        from srtgen.web.server import render_guide_html as _render  # noqa: PLC0415
    except Exception as err:  # pragma: no cover - chỉ để báo
        warnings.append(f"không dựng được HUONG-DAN.html ({type(err).__name__}: {err}); gói vẫn có HUONG-DAN.md")
        return None
    md = (ROOT / "HUONG-DAN.md").read_text(encoding="utf-8-sig")
    return _render(md).encode("utf-8")


def verify(path: Path) -> list[str]:
    """Mở lại gói vừa đóng và kiểm những điều mà người dùng Mac sẽ vấp phải nếu sai."""
    problems: list[str] = []
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        for rel in REQUIRED:
            if f"{TOP}/{rel}" not in names:
                problems.append(f"thiếu file bắt buộc: {rel}")
        for info in z.infolist():
            rel = info.filename[len(TOP) + 1:]
            data = z.read(info)
            if Path(rel).suffix.lower() in EXECUTABLE_SUFFIXES:
                mode = (info.external_attr >> 16) & 0o777
                if info.create_system != 3 or mode != 0o755:
                    problems.append(f"{rel}: quyền chạy sai (system={info.create_system}, mode={oct(mode)})")
                if b"\r" in data:
                    problems.append(f"{rel}: còn ký tự CR")
                if not data.startswith(b"#!"):
                    problems.append(f"{rel}: không bắt đầu bằng '#!'")
            if rel.endswith(".py"):
                try:
                    compile(data, rel, "exec")
                except SyntaxError as err:
                    problems.append(f"{rel}: lỗi cú pháp dòng {err.lineno}: {err.msg}")
    return problems


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    version = read_version()
    DIST.mkdir(exist_ok=True)
    out = DIST / f"SrtGen-{version}-macOS.zip"
    warnings: list[str] = []
    executables: list[str] = []
    count = 0

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for src in iter_sources():
            if not src.exists():
                raise SystemExit(f"LỖI: không tìm thấy {src.relative_to(ROOT)}")
            rel = src.relative_to(ROOT).as_posix()
            data = normalize(rel, src.read_bytes(), warnings)
            z.writestr(zip_info(rel, src.stat().st_mtime), data)
            count += 1
            if Path(rel).suffix.lower() in EXECUTABLE_SUFFIXES:
                executables.append(rel)
        guide = render_guide_html(warnings)
        if guide is not None:
            z.writestr(zip_info("HUONG-DAN.html", time.time()), guide)
            count += 1

    problems = verify(out)
    size_mb = out.stat().st_size / (1024 * 1024)
    print(f"Đã đóng gói: {out}")
    print(f"  {count} file, {size_mb:.2f} MB, phiên bản {version}")
    print(f"  File chạy được (0755, Unix): {', '.join(executables)}")
    for w in warnings:
        print(f"  Lưu ý: {w}")
    if problems:
        print("KIỂM TRA GÓI THẤT BẠI:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("  Kiểm tra gói: đạt (đủ file bắt buộc, quyền chạy đúng, không CR, mọi file .py biên dịch được).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
