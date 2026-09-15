"""Every filesystem and console byte in srtgen goes through this module.

Rationale (plan section 4): the four cross-platform traps are encoding, newline,
Windows console codepage and Unicode normalisation of filenames.  Centralising
``open()`` here is what makes those traps fixable in one place instead of in
forty call sites.

Emit contract reproduced from the reference corpus:
  * UTF-8 **with** BOM
  * ``\n`` newlines (configurable)
  * one blank line between blocks, trailing newline at EOF
  * text content normalised to NFC

"Keep the old copy before overwriting" (contract H1) also lives here, in
:func:`keep_old_copy`: every writer of a result file - the export stage, the
CLI, the web server - asks this one function, so there is exactly one naming
scheme for kept copies and one rule for when a copy is due.  Three private
copies of that rule used to disagree (``-1`` here, ``-2`` there, a copy folder
somewhere else), and one re-run could leave two copies of the same hand edit.
"""

from __future__ import annotations

import json
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

__all__ = [
    "configure_console",
    "nfc",
    "read_text",
    "write_text",
    "read_json",
    "write_json",
    "read_yaml",
    "write_yaml",
    "ensure_dir",
    "safe_stem",
    "atomic_write_text",
    "encode_text",
    "keep_old_copy",
    "kept_copy_path",
    "is_kept_copy",
    "KeepCopyError",
    "KEPT_COPY_WORD",
    "KEPT_COPY_TAG",
    "KEPT_COPY_STAMP",
    "STREAM_BLOCK_BYTES",
    "StreamTooLarge",
    "BinaryStreamWriter",
]


# --------------------------------------------------------------------------- #
# console
# --------------------------------------------------------------------------- #

def configure_console() -> None:
    """Make stdout/stderr able to print Chinese on a cp1252 Windows console.

    Called once from the CLI entry point.  Safe to call more than once.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - detached stream
            pass


# --------------------------------------------------------------------------- #
# unicode
# --------------------------------------------------------------------------- #

def nfc(text: str) -> str:
    """Normalise to NFC.

    macOS hands out NFD filenames and NFD text; Windows and the corpus use NFC.
    Comparing an NFD string with an NFC string silently fails, so every string
    that enters srtgen from the outside world is normalised here.
    """
    return unicodedata.normalize("NFC", text)


# --------------------------------------------------------------------------- #
# text files
# --------------------------------------------------------------------------- #

def read_text(path: str | os.PathLike[str]) -> str:
    """Read a text file as UTF-8, tolerating a BOM, returning NFC text with \n.

    ``utf-8-sig`` strips the BOM when present and is a no-op when absent, so a
    file produced by us and a file produced by a Windows editor read the same.
    ``newline=None`` (universal newlines) collapses CRLF to LF on the way in.
    """
    with open(path, "r", encoding="utf-8-sig", newline=None) as fh:
        return nfc(fh.read())


def write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    bom: bool = True,
    newline: str = "\n",
    trailing_newline: bool = True,
) -> None:
    """Write text under the emit contract.

    The bytes come from :func:`encode_text` and are written in binary mode, so
    Python's own newline translation never touches them: the exact line
    terminator requested here is what lands on disk, on every platform.
    """
    ensure_dir(Path(path).parent)
    data = encode_text(text, bom=bom, newline=newline, trailing_newline=trailing_newline)
    with open(path, "wb") as fh:
        fh.write(data)


def encode_text(
    text: str,
    *,
    bom: bool = True,
    newline: str = "\n",
    trailing_newline: bool = True,
) -> bytes:
    """The exact bytes :func:`write_text` puts on disk for ``text``.

    A separate function because "is the file on disk already what we are about
    to write?" (:func:`keep_old_copy`) has to compare against the real bytes.
    A second, hand-written guess of the emit contract would drift from
    ``write_text`` the day someone changes it, and then every run would either
    set aside identical files or - worse - stop setting aside edited ones.
    """
    body = nfc(text).replace("\r\n", "\n").replace("\r", "\n")
    if trailing_newline and not body.endswith("\n"):
        body += "\n"
    if newline != "\n":
        body = body.replace("\n", newline)
    return body.encode("utf-8-sig" if bom else "utf-8")


def atomic_write_text(path: str | os.PathLike[str], text: str, **kwargs: Any) -> None:
    """Write via a temp file + replace, so an interrupted run never truncates output."""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    write_text(tmp, text, **kwargs)
    os.replace(tmp, target)


# --------------------------------------------------------------------------- #
# keeping the old copy before an overwrite (contract H1)
# --------------------------------------------------------------------------- #

#: Word put into the name of a kept copy: ``Phim_vi.srt`` ->
#: ``Phim_vi.truoc-20260910-183000.srt``.  ASCII so it survives every disk the
#: output folder may live on (a FAT32 USB stick included) and still reads as
#: Vietnamese "trước" (= "before").
KEPT_COPY_WORD = "truoc"

#: What marks a file name as a kept copy.  Anything that lists result files
#: (the web server's film list, its ``_vi`` pairing) skips names containing it,
#: see :func:`is_kept_copy`.
KEPT_COPY_TAG = f".{KEPT_COPY_WORD}-"

#: Moment in the name, local time, sortable as text.
KEPT_COPY_STAMP = "%Y%m%d-%H%M%S"

#: ``-1`` ... ``-999`` after the stamp.  A thousand copies of one file inside
#: one second means a loop gone wrong, not a person at work - stop there.
_KEPT_COPY_ATTEMPTS = 1000


class KeepCopyError(OSError):
    """The old file could not be set aside, so it must not be overwritten.

    An ``OSError`` because that is what it is (a rename the disk refused) and
    because every caller already treats ``OSError`` as "writing failed"; a
    subclass so a caller that wants to can tell "nothing was touched, the old
    file is still in place" apart from a failure halfway through a write.
    The message is Vietnamese and meant to be shown to the user as it is.
    """

    def __init__(self, message: str, *, path: Path, copy: Path | None = None) -> None:
        super().__init__(message)
        self.path = path
        self.copy = copy


def is_kept_copy(path: str | os.PathLike[str]) -> bool:
    """True for a name made by :func:`keep_old_copy` (``Phim.truoc-....srt``).

    Kept copies sit next to the results on purpose - that is where people look
    for them - so everything that lists result files by pattern (``*.srt``) has
    to skip them, or ``Phim_vi.truoc-....srt`` turns up as a film of its own.
    """
    return KEPT_COPY_TAG in Path(path).name


def _kept_copy_name(target: Path, stamp: str, number: int) -> Path:
    """``<stem>.truoc-<stamp>[-<number>]<suffix>`` next to ``target``.

    The tag goes just before the LAST suffix so the name keeps its ending:
    ``Phim_vi.truoc-....srt`` sorts right beside ``Phim_vi.srt`` and still
    opens as a subtitle file, while ``Phim.truoc-..._vi.srt`` would look like
    the Vietnamese file of another film.  ``Phim.bundle.json`` becomes
    ``Phim.bundle.truoc-....json``.
    """
    extra = f"-{number}" if number else ""
    return target.with_name(f"{target.stem}{KEPT_COPY_TAG}{stamp}{extra}{target.suffix}")


def kept_copy_path(path: str | os.PathLike[str], now: datetime | None = None) -> Path:
    """Where :func:`keep_old_copy` would put the copy of ``path`` right now.

    First free name: the plain stamp, then ``-1``, ``-2``...  Only a forecast -
    another program may take the name before the rename happens, which is why
    :func:`keep_old_copy` checks again while renaming.
    """
    target = Path(path)
    stamp = (now or datetime.now()).strftime(KEPT_COPY_STAMP)
    for number in range(_KEPT_COPY_ATTEMPTS):
        candidate = _kept_copy_name(target, stamp, number)
        if not (candidate.exists() or candidate.is_symlink()):
            return candidate
    return _kept_copy_name(target, stamp, _KEPT_COPY_ATTEMPTS)


def _same_bytes(path: Path, wanted: bytes) -> bool:
    """Does the file hold exactly ``wanted``?  Unreadable counts as "no".

    "Cannot tell" must lead to the safe side, which is keeping a copy: calling
    an unreadable file "the same" would overwrite something nobody has seen.
    The size is checked first so a changed file is rarely read at all.
    """
    try:
        if path.stat().st_size != len(wanted):
            return False
        with open(path, "rb") as fh:
            return fh.read() == wanted
    except OSError:
        return False


def keep_old_copy(
    path: str | os.PathLike[str],
    new_content: str | bytes | None = None,
    *,
    now: datetime | None = None,
) -> Path | None:
    """Set the existing file at ``path`` aside before it is overwritten.

    Why: the files the tool writes (``.srt``, ``_vi.srt``, ``.ass``...) are the
    very files people open and correct by hand, in the editor tab or in
    Aegisub.  The user is not technical; a re-run that silently overwrites an
    evening of corrections is the fastest way to lose them for good.

    * ``path`` does not exist (or is not a file) -> ``None``, nothing done.
    * ``new_content`` given and the file already holds exactly it -> ``None``.
      Compared as bytes: a ``str`` is first encoded the way :func:`write_text`
      writes it by default (NFC, LF, trailing newline, BOM); a caller writing
      with other options passes ``encode_text(..., bom=..., newline=...)``.
      Identical content is not copied because there is nothing to lose, and a
      folder full of duplicates teaches people to ignore the copies.
    * Otherwise the file is RENAMED - every byte kept, no re-encoding - to
      ``<stem>.truoc-<YYYYmmdd-HHMMSS><suffix>`` in the same folder
      (``Phim_vi.srt`` -> ``Phim_vi.truoc-20260910-183000.srt``); a name
      already taken gets ``-1``, ``-2``..., an existing copy is never replaced.
      Returns the path of the copy.

    ``now`` fixes the stamp, so that one run sets all its files aside under one
    moment and a test does not wait for the clock.

    When the rename is refused (file locked by Aegisub on Windows, read-only
    folder...) this raises :class:`KeepCopyError` - an ``OSError`` carrying a
    Vietnamese sentence - and the caller MUST NOT overwrite: losing someone's
    corrections is far worse than an error message.
    """
    target = Path(path)
    if not target.is_file():
        return None
    if new_content is not None:
        wanted = (
            bytes(new_content)
            if isinstance(new_content, (bytes, bytearray))
            else encode_text(str(new_content))
        )
        if _same_bytes(target, wanted):
            return None

    stamp = (now or datetime.now()).strftime(KEPT_COPY_STAMP)
    for number in range(_KEPT_COPY_ATTEMPTS):
        copy = _kept_copy_name(target, stamp, number)
        # Checked before renaming because on macOS/Linux a rename silently
        # replaces an existing file - that would destroy an older kept copy.
        if copy.exists() or copy.is_symlink():
            continue
        try:
            target.rename(copy)
        except FileExistsError:  # Windows: taken between the check and the rename
            continue
        except OSError as err:
            raise KeepCopyError(
                f"Không cất được bản cũ của “{target.name}” (đổi tên thành "
                f"“{copy.name}” bị từ chối), nên tool KHÔNG ghi đè file đó — phần bạn "
                "đã sửa trong file vẫn còn nguyên. Hãy đóng chương trình đang mở file "
                "này (ví dụ Aegisub) hoặc kiểm tra quyền ghi của thư mục "
                f"{target.parent}, rồi làm lại. "
                f"(Chi tiết kỹ thuật: {type(err).__name__}: {err})",
                path=target,
                copy=copy,
            ) from err
        return copy
    raise KeepCopyError(
        f"Đã có quá nhiều bản cất của “{target.name}” trong cùng một giây nên tool "
        "không đặt được tên cho bản cất mới, và KHÔNG ghi đè file đó. Hãy đợi một "
        "giây rồi làm lại.",
        path=target,
    )


# --------------------------------------------------------------------------- #
# structured files
# --------------------------------------------------------------------------- #

def read_json(path: str | os.PathLike[str]) -> Any:
    return json.loads(read_text(path))


def write_json(path: str | os.PathLike[str], data: Any, *, indent: int = 2) -> None:
    """JSON is written without a BOM - it is consumed by machines, not editors."""
    write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=False),
        bom=False,
    )


def read_yaml(path: str | os.PathLike[str]) -> Any:
    import yaml

    return yaml.safe_load(read_text(path))


def write_yaml(path: str | os.PathLike[str], data: Any) -> None:
    import yaml

    write_text(
        path,
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        bom=False,
    )


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #

def ensure_dir(path: str | os.PathLike[str]) -> Path:
    p = Path(path)
    if str(p):
        p.mkdir(parents=True, exist_ok=True)
    return p


_ILLEGAL = r'<>:"/\|?*'


def _is_control(ch: str) -> bool:
    """C0 controls (``\\x00``-``\\x1f``), DEL (``\\x7f``) and the C1 block.

    None of them can be typed on purpose into a film title, but they do arrive:
    a title scraped from a web page, a filename dragged in from another OS.
    ``\\x00`` is the dangerous one - ``open()`` refuses a path containing it
    with ``ValueError: embedded null character``, and that happens in the very
    last stage, after the user has already waited half an hour for Whisper.
    The C1 block (``\\x80``-``\\x9f``) is filtered for the same reason: it is
    never meaningful in a name and some filesystems reject it.
    """
    code = ord(ch)
    return code < 0x20 or 0x7F <= code <= 0x9F


#: Hai trần độ dài của tên file, xem giải thích trong :func:`safe_stem`.
_MAX_STEM_CHARS = 120
_MAX_STEM_BYTES = 200


def safe_stem(name: str, *, fallback: str = "output") -> str:
    """Turn a video title into a filename stem that survives both filesystems.

    NFC first (so Vietnamese titles compare equal later), then replace the
    characters Windows refuses and every control character (see
    :func:`_is_control`) with a space, then collapse whitespace.  Replacing
    rather than deleting keeps ``a\\x00b`` readable as two words.

    Độ dài bị chặn HAI lần, và cả hai đều cần:

    * **120 ký tự** — Windows vẫn mặc định giới hạn đường dẫn 260 ký tự.
    * **200 byte** — macOS (APFS/HFS+) giới hạn mỗi phần tên là 255 *byte*, mà
      một chữ Hán tốn 3 byte. Tên phim 120 chữ Hán thành 360 byte; cộng thêm
      đuôi dài nhất mà tool tự thêm (``_song-ngu.ass``, hoặc
      ``.truoc-<15 ký tự>.srt`` của bản cất đi) là vượt 255 byte, và mọi lần ghi
      đều hỏng với ``ENAMETOOLONG`` — đúng ở bước cuối, sau khi máy đã gỡ băng
      xong cả phim. 200 byte chừa đủ chỗ cho những đuôi đó.
    """
    cleaned = "".join(
        " " if (ch in _ILLEGAL or _is_control(ch)) else ch for ch in nfc(str(name or ""))
    )
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not cleaned:
        return fallback
    cleaned = cleaned[:_MAX_STEM_CHARS]
    raw = cleaned.encode("utf-8")
    if len(raw) > _MAX_STEM_BYTES:
        # ``errors="ignore"`` bỏ nốt ký tự bị cắt đôi ở mép: không bao giờ sinh
        # chuỗi hỏng, chỉ ngắn hơn đúng một chữ.
        cleaned = raw[:_MAX_STEM_BYTES].decode("utf-8", "ignore")
    # Cắt xong có thể còn khoảng trắng hoặc dấu chấm ở cuối — Windows từ chối cả hai.
    cleaned = cleaned.strip(" .")
    return cleaned or fallback


def iter_lines(text: str) -> Iterable[str]:
    """Split on \n after the reader has already normalised CRLF away."""
    return text.split("\n")


# --------------------------------------------------------------------------- #
# binary streams (uploaded media)
# --------------------------------------------------------------------------- #

#: One disk write per megabyte.  Small enough that memory stays flat while a
#: multi-gigabyte video arrives, large enough that a 4GB upload is ~4000 writes
#: rather than the ~65000 an ASGI server's 64KB chunks would cost.
STREAM_BLOCK_BYTES = 1024 * 1024


class StreamTooLarge(ValueError):
    """Raised *before* a write that would push a stream past its byte limit.

    A dedicated type so the caller can answer "413, too big" without parsing an
    error message, and so the partial file is known to be garbage.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(f"stream exceeds {limit} bytes")
        self.limit = int(limit)


class BinaryStreamWriter:
    """Write a byte stream to disk in fixed-size blocks, never holding it all in RAM.

    This is the one binary counterpart of ``write_text``: uploaded video is not
    text (none of the four text traps apply) but it still has to go through this
    module so the "only io_utils calls open()" rule keeps its single choke point.

    Three guarantees the web upload relies on:

    * **Bounded memory** - at most one block (plus the chunk just fed in) is
      buffered; everything else is already on disk.
    * **The limit is checked before bytes land** - ``write`` raises
      ``StreamTooLarge`` without writing, so no oversized file ever exists.
    * **A failed stream leaves nothing behind** - ``discard`` (and leaving a
      ``with`` block on an exception) closes and deletes the partial file.  A
      half-written 3GB file that no job points to is disk space the user never
      gets back and never learns about.

    The file is created with ``"xb"``: callers name it with a fresh random id,
    so an existing file at that path means something is badly wrong and must
    not be overwritten.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        max_bytes: int | None = None,
        block_bytes: int = STREAM_BLOCK_BYTES,
    ) -> None:
        self.path = Path(path)
        self.max_bytes = int(max_bytes) if max_bytes else None
        self.block_bytes = max(1, int(block_bytes))
        self.written = 0
        self._buffer = bytearray()
        ensure_dir(self.path.parent)
        # Unbuffered: the block size above *is* the buffer.  Python's own 8KB
        # buffer on top would make "a full block is on disk" true only for
        # blocks larger than 8KB, a guarantee that silently depends on a number.
        self._fh: Any = open(self.path, "xb", buffering=0)

    @property
    def total(self) -> int:
        """Bytes accepted so far (on disk plus still buffered)."""
        return self.written + len(self._buffer)

    @property
    def buffered(self) -> int:
        """Bytes waiting for the next full block."""
        return len(self._buffer)

    def write(self, data: bytes) -> None:
        """Accept ``data``; every complete block goes to disk immediately."""
        if self._fh is None:
            raise ValueError("stream already closed")
        if self.max_bytes is not None and self.total + len(data) > self.max_bytes:
            raise StreamTooLarge(self.max_bytes)
        self._buffer += data
        while len(self._buffer) >= self.block_bytes:
            self._write_all(self._buffer[: self.block_bytes])
            del self._buffer[: self.block_bytes]
            self.written += self.block_bytes

    def _write_all(self, data: bytes | bytearray) -> None:
        """An unbuffered ``write`` may accept fewer bytes than offered; loop until done."""
        view = memoryview(data)
        while view:
            count = self._fh.write(view)
            if not count:
                raise OSError("disk accepted no bytes (disk full?)")
            view = view[count:]

    def close(self) -> int:
        """Flush the last partial block, close, and return the final size."""
        if self._fh is None:
            return self.written
        if self._buffer:
            self._write_all(self._buffer)
            self.written += len(self._buffer)
            self._buffer.clear()
        self._fh.close()
        self._fh = None
        return self.written

    def discard(self) -> None:
        """Close without flushing and delete the file.  Safe to call twice."""
        self._buffer.clear()
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> "BinaryStreamWriter":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if exc_type is not None:
            self.discard()
        else:
            self.close()
