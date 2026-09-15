"""S9 — kiểm lần cuối rồi xuất file cho người dùng.

Vì sao chặng này tồn tại tách khỏi S6/S7: mọi chặng trước đều làm việc trên
**token**, còn đây là chỗ duy nhất token biến thành byte trên đĩa.  Gom việc ghi
file vào một chỗ nghĩa là quy ước xuất bản (BOM, newline, tên file, thứ tự các
file) chỉ khai báo một lần, và cũng là chỗ duy nhất phải sửa khi Aegisub hay
backend đổi yêu cầu.

Mọi file luôn cùng một gốc tên (lấy từ tiêu đề video) để người dùng nhìn thư mục
là biết chúng đi với nhau::

    <tiêu đề>.srt            bản phụ đề 4 dòng, UTF-8 có BOM
    <tiêu đề>_vi.srt         bản tiếng Việt 3 dòng, cùng số block và cùng timestamp
    <tiêu đề>.bundle.json    danh sách cụm cho backend, đúng shape README
    <tiêu đề>.report.html    báo cáo tiếng Việt, tự chứa, mở offline được
    <tiêu đề>.ass            tuỳ chọn, cho người soát bằng Aegisub
    <tiêu đề>_song-ngu.ass   mặc định BẬT, ba dòng một sự kiện để soát song ngữ

Nguyên tắc quan trọng nhất của chặng này: **có lỗi vẫn ghi file**.  Người dùng
cuối không phải dân IT; giữ lại kết quả 30 phút chạy máy rồi bắt họ đọc một
thông báo lỗi là cách chắc chắn nhất để họ bỏ tool.  Thay vào đó file vẫn được
ghi, `has_errors=True` để giao diện báo đỏ, và báo cáo HTML nói rõ từng lỗi nằm
ở dòng phụ đề số mấy.

Hai mã lỗi ``CUM_MISMATCH`` và ``PUNCT_SYNC`` ở đây chỉ còn là **assert phòng
thủ**: hai dòng render ra từ cùng một token list nên chúng không thể sai vì nội
dung.  Nếu chúng nổ thì bộ render có bug, và thông điệp gửi ra ngoài phải nói
đúng như vậy chứ không được đổ cho người biên tập (build-spec mục 0 và mục 7).

Nguyên tắc thứ hai, ngang hàng: **không bao giờ ghi đè mất bản người dùng đã sửa
tay mà họ không biết**.  Chạy lại một phim (vì bảng tên đổi, vì bấm "chạy lại")
là ghi lại đúng những file người dùng có thể đã sửa cả buổi trong trình sửa hay
trong Aegisub.  Vì vậy trước khi ghi BẤT KỲ file kết quả nào, nếu file đó đã có và
nội dung khác cái sắp ghi, file cũ được đổi tên thành
``<tên>.truoc-<YYYYmmdd-HHMMSS><đuôi>`` ngay trong cùng thư mục (``Phim_vi.srt`` →
``Phim_vi.truoc-20260910-183000.srt``), đường dẫn bản cất nằm trong khoá
``backups`` của kết quả, và bản cất không bao giờ bị xoá.  Nội dung giống hệt thì
không cất — một thư mục đầy bản trùng dạy người ta lờ nó đi.  Việc đổi tên đi qua
``io_utils.keep_old_copy``, hàm dùng chung với lệnh CLI và máy chủ web, để cả tool
chỉ có MỘT kiểu tên bản cất và một luật khi nào phải cất.  Chặng tách cụm không
còn tự chép bản sửa tay sang thư mục riêng khi bảng tên đổi: chính chặng này lo,
nên một lần chạy lại chỉ để lại đúng một bản cất cho mỗi file đã sửa.

Mỗi lần xuất cũng làm mới ``work/<video_id>/edit_original.json`` — bản "máy tạo"
mà nút "Hoàn nguyên" của trình sửa quay về — để sau một lần chạy lại, Hoàn nguyên
về bản máy tạo MỚI chứ không về bản của lần chạy trước.
"""

from __future__ import annotations

import html
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from srtgen import io_utils
from srtgen.core.context import Context
from srtgen.core.rules import (
    CUM_MISMATCH,
    MARKER_SYNC,
    PUNCT_SYNC,
    RULES,
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARN,
    Finding,
    summarize,
    validate_document,
)
from srtgen.core.srt import emit_srt, format_timestamp
from srtgen.core.token import (
    FLAG_AI_APPLIED,
    FLAG_CASE_AMBIG,
    FLAG_HETERONYM,
    FLAG_NAME,
    Cue,
    Document,
    render_py,
    render_zh,
)

__all__ = [
    "run",
    "BACKUP_TAG",
    "ORIGINAL_FILE",
    "BackupError",
    "backup_path_for",
    "backup_stamp",
    "build_bundle",
    "build_report_html",
    "build_ass",
    "build_vi_srt",
    "build_bilingual_ass",
    "validate_vi_text",
    "collect_stats",
    "StatBlock",
    "ReviewItem",
    "STAGE_NAMES",
    "TARGETS",
    "VI_BLOCK_COUNT",
    "VI_TIMESTAMP_DRIFT",
    "VI_EMPTY_LINE",
    "VI_TRAILING_SPACE",
    "VI_MARKER_SYNC",
]

STAGE_NUMBER = 9
STAGE_KEY = "emit"


# --------------------------------------------------------------------------- #
# hằng số hiển thị
# --------------------------------------------------------------------------- #

#: (số chặng, tên file chặng, tên tiếng Việt).  Tên tiếng Việt là thứ người dùng
#: đọc trong báo cáo; mã "S2" chỉ đứng làm nhãn phụ cho người hỗ trợ kỹ thuật.
STAGE_NAMES: tuple[tuple[int, str, str], ...] = (
    (0, "info", "Tải nguồn"),
    (1, "audio", "Xử lý âm thanh"),
    (2, "asr", "Nghe và gỡ băng"),
    (3, "clean", "Dọn kết quả nghe"),
    (4, "cues", "Chia dòng phụ đề"),
    (5, "tokens", "Tách cụm và ghi pinyin"),
    (6, "norm", "Chuẩn hoá dấu câu và viết hoa"),
    (7, "ai", "Trợ lý AI"),
    (8, "translate", "Dịch sang tiếng Việt"),
    (9, "emit", "Kiểm và xuất file"),
)

#: Phân bố của bản chuẩn đã audit (build-spec mục 7, đo trên 1351 cue của
#: ``corpus/completed.srt``).  Đây không phải ngưỡng chặn — chúng chỉ trả lời câu
#: hỏi "cách chia dòng của lần chạy này có giống bản người làm tay không".
TARGETS: dict[str, tuple[float, float]] = {
    "duration": (1.32, 2.92),   # giây / mỗi dòng phụ đề
    "han": (5.0, 12.0),         # số chữ Hán mỗi dòng
    "clusters": (3.0, 8.0),     # số cụm mỗi dòng
}

#: Ngưỡng coi là "lệch đáng kể" so với bản chuẩn.  ±30% nghe rộng, nhưng phân bố
#: đo được trên corpus lệch tới ±25% giữa các tập phim cùng thể loại, nên siết
#: hơn nữa chỉ tạo báo động giả.
_TARGET_TOLERANCE = 0.30

#: Tỉ lệ đoạn bị cắt vì máy nghe nhầm (ảo giác) mà trên mức này thì nên khuyên
#: bật demucs.  Đo trên các lần chạy thử: phim ít nhạc nền ~1-2%, phim nhiều
#: nhạc nền vọt lên trên 8%.
_DEMUCS_HINT_RATIO = 0.05

_FLAG_LABELS: dict[str, str] = {
    FLAG_AI_APPLIED: "Trợ lý AI đã sửa",
    FLAG_CASE_AMBIG: "Viết hoa sau “……”",
    FLAG_HETERONYM: "Chữ nhiều cách đọc",
    FLAG_NAME: "Tên riêng",
}

_FLAG_WHY: dict[str, str] = {
    FLAG_AI_APPLIED: (
        "Trợ lý AI đề xuất và tool đã áp dụng. AI chỉ được chọn giữa các phương án "
        "có sẵn, không tự bịa, nhưng vẫn cần một người đọc lại câu để xác nhận."
    ),
    FLAG_CASE_AMBIG: (
        "Dòng trước kết thúc bằng dấu lửng “……”. Trong bản chuẩn, chỗ này lúc viết "
        "hoa lúc viết thường (tỉ lệ 54/46) nên không có luật nào đúng cho mọi câu; "
        "tool chọn theo cấu hình và đánh dấu lại để người soát quyết định."
    ),
    FLAG_HETERONYM: (
        "Chữ Hán này có nhiều cách đọc và chỉ ngữ cảnh mới quyết định được âm đúng "
        "(ví dụ 得 đọc dé / de / děi). Hãy đọc cả câu rồi xác nhận pinyin."
    ),
    FLAG_NAME: (
        "Tool nhận đây là tên riêng nên luôn viết hoa. Kiểm lại chính tả pinyin và "
        "cách tách cụm của tên trong toàn bộ file."
    ),
}

#: Mỗi mã lỗi được giải thích bằng câu người thường hiểu, kèm cách sửa.  Bảng
#: `RULES` của `rules.py` chỉ có nhãn ngắn — đủ cho CLI, không đủ cho một người
#: mở báo cáo lần đầu và không biết "cụm" hay "marker" là gì.
_EXPLAIN: dict[str, str] = {
    "BLOCK_SHAPE": (
        "Một khối phụ đề phải có đúng 4 dòng: số thứ tự, mốc thời gian, dòng chữ Hán, "
        "dòng pinyin. Thiếu hoặc thừa dòng sẽ làm Aegisub đọc lệch từ chỗ đó trở đi."
    ),
    "INDEX_SEQ": (
        "Số thứ tự của các dòng phụ đề phải chạy liên tục 1, 2, 3… Tool luôn đánh số lại "
        "khi ghi file, nên nếu lỗi này còn xuất hiện thì hãy báo cho người phát triển."
    ),
    "TIMESTAMP_FORMAT": (
        "Mốc thời gian phải viết đúng dạng 00:01:19,580 --> 00:01:22,100 (dấu phẩy trước "
        "phần mili giây, không phải dấu chấm)."
    ),
    "TIMESTAMP_ORDER": (
        "Thời điểm bắt đầu phải nhỏ hơn thời điểm kết thúc, và hai dòng phụ đề không được "
        "chồng lấn thời gian. Nếu chồng lấn, Aegisub sẽ cảnh báo ngay khi mở file."
    ),
    "CUM_MISMATCH": (
        "Mã này bắt hai chuyện: số cụm ở dòng chữ Hán không bằng số cụm ở dòng pinyin, "
        "hoặc có cụm chữ Hán chưa được ghi pinyin. Trong tool này hai dòng được dựng từ "
        "cùng một danh sách cụm nên trường hợp lệch số cụm gần như không thể xảy ra vì nội "
        "dung — mỗi mục bên dưới có ghi rõ nó thuộc trường hợp nào."
    ),
    "PAIR_MISMATCH": (
        "Cụm chữ Hán và cụm pinyin ở cùng vị trí đọc không khớp nhau. Tên riêng và từ mượn "
        "có thể lệch hợp lệ, nên đây chỉ là cảnh báo để người soát liếc qua."
    ),
    "LEADING_SPACE": "Đầu dòng không được có khoảng trắng.",
    "TRAILING_SPACE": "Cuối dòng không được có khoảng trắng.",
    "DOUBLE_SPACE": (
        "Không được có hai khoảng trắng liền nhau. Giữa hai cụm chỉ dùng đúng một khoảng trắng."
    ),
    "ASCII_PUNCT": (
        "Trong nội dung phụ đề phải dùng dấu câu tiếng Trung （，。？！：；） thay cho dấu "
        "kiểu Latin （, . ? ! : ;）. Riêng dấu phẩy trong mốc thời gian là bắt buộc phải giữ."
    ),
    "SPACE_AROUND_PUNCT": (
        "Dấu câu tiếng Trung phải viết dính vào chữ, không có khoảng trắng trước hay sau. "
        "Ngoại lệ duy nhất là đúng một khoảng trắng trước dấu “-” đổi người nói."
    ),
    "ELLIPSIS_FORM": (
        "Dấu lửng phải viết đúng là “……” (hai ký tự), không phải “...”, “…” hay “. . .”."
    ),
    "DASH_FORM": (
        "Dấu ngắt lời phải viết đúng là “——” (hai ký tự). Dấu “-” chỉ được dùng để đánh "
        "dấu đổi người nói."
    ),
    "MARKER_SPACING": (
        "Dấu “-” đổi người nói phải có đúng một khoảng trắng mỗi bên; nếu nó đứng đầu dòng "
        "thì chỉ cần khoảng trắng bên phải."
    ),
    "MARKER_SYNC": (
        "Dấu “-” đổi người nói phải nằm ở cùng vị trí trên cả dòng chữ Hán lẫn dòng pinyin."
    ),
    "PUNCT_SYNC": (
        "Loại dấu câu và vị trí dấu câu phải giống hệt nhau giữa dòng chữ Hán và dòng pinyin."
    ),
    "NAME_INCONSISTENT": (
        "Cùng một tên riêng mà chỗ thì viết liền, chỗ thì tách rời trong cùng một file. "
        "Hãy chọn một cách rồi giữ nguyên cho cả file."
    ),
    "ERHUA_SPLIT": (
        "Chữ 儿 của 儿化音 phải nằm chung cụm với chữ đứng trước và ghi pinyin rút gọn "
        "（哪儿 → nǎr）, không được tách thành một cụm riêng đọc là “er”."
    ),
    "EMPTY_CUE": (
        "Dòng phụ đề này không có chữ nào. Tool bỏ qua nó khi ghi file để không tạo dòng "
        "trống bên trong khối — dòng trống sẽ làm Aegisub hiểu nhầm là hết khối."
    ),
}

#: Ba mã lỗi mà, trong đường chạy của tool, chỉ có thể do bug nội bộ.
_INTERNAL_CODES: tuple[str, ...] = (CUM_MISMATCH, PUNCT_SYNC, MARKER_SYNC)

# --------------------------------------------------------------------------- #
# mã lỗi của cặp file zh / vi (build-spec-v2 mục 2)
# --------------------------------------------------------------------------- #
# Mã và luật thuộc về ``rules.py``; ở đây chỉ tra lại bằng ``getattr`` để chặng
# xuất file vẫn nạp được nếu bảng luật đổi tên hằng số. Không định nghĩa lại bảng
# luật thứ hai — một mã lỗi có hai định nghĩa là một mã lỗi sẽ lệch nhau.

def _rule_attr(name: str, fallback: Any) -> Any:
    from srtgen.core import rules as _rules

    return getattr(_rules, name, fallback)


VI_BLOCK_COUNT = str(_rule_attr("VI_BLOCK_COUNT", "VI_BLOCK_COUNT"))
VI_TIMESTAMP_DRIFT = str(_rule_attr("VI_TIMESTAMP_DRIFT", "VI_TIMESTAMP_DRIFT"))
VI_EMPTY_LINE = str(_rule_attr("VI_EMPTY_LINE", "VI_EMPTY_LINE"))
VI_TRAILING_SPACE = str(_rule_attr("VI_TRAILING_SPACE", "VI_TRAILING_SPACE"))
VI_MARKER_SYNC = str(_rule_attr("VI_MARKER_SYNC", "VI_MARKER_SYNC"))

#: Bảng tra nhãn dùng cho báo cáo: gộp cả luật Hán–pinyin lẫn luật file tiếng Việt.
_ALL_RULES: dict[str, tuple[str, str]] = dict(_rule_attr("ALL_RULES", RULES))

_VI_EXPLAIN: dict[str, str] = {
    VI_BLOCK_COUNT: (
        "File tiếng Việt phải có đúng bằng số dòng phụ đề của file tiếng Trung. "
        "Lệch số dòng nghĩa là hai file không còn khớp nhau, mở song song sẽ sai từ chỗ đó."
    ),
    VI_TIMESTAMP_DRIFT: (
        "Mốc thời gian của dòng thứ i ở file tiếng Việt phải giống hệt file tiếng Trung."
    ),
    VI_EMPTY_LINE: (
        "Dòng tiếng Việt bị trống. Tool luôn điền lại bằng văn bản gốc để hai file không "
        "lệch dòng, nên nếu vẫn thấy lỗi này thì hãy báo cho người phát triển."
    ),
    VI_TRAILING_SPACE: "Dòng tiếng Việt còn khoảng trắng thừa ở đầu hoặc cuối.",
    VI_MARKER_SYNC: (
        "Dòng tiếng Trung có dấu “-” đổi người nói mà dòng tiếng Việt không có "
        "(hoặc ngược lại). Người xem sẽ không biết câu nào của ai."
    ),
}

_EXPLAIN.update(_VI_EXPLAIN)

_REPORT_BUG_NOTE = "Vui lòng gửi báo cáo này cho người phát triển."

_INTERNAL_NOTE = (
    "Ghi chú kỹ thuật: dòng chữ Hán và dòng pinyin được dựng ra từ cùng một danh sách cụm, "
    "nên lỗi này KHÔNG thể đến từ nội dung phụ đề — nó là dấu hiệu bộ dựng dòng (renderer) "
    f"của tool bị sai. {_REPORT_BUG_NOTE}"
)

#: `rules.py` dùng chung mã CUM_MISMATCH cho hai chuyện khác hẳn nhau; câu này là
#: cách nhận ra biến thể "cụm chưa có pinyin".
_MISSING_PINYIN_MARK = "chưa có pinyin"

_MISSING_PINYIN_NOTE = (
    "Ghi chú kỹ thuật: chỗ này KHÔNG phải bộ dựng dòng sai. Khi hai dòng của file gốc lệch "
    "số cụm, bước ghép cố ý bỏ trống pinyin thay vì đoán bừa — đoán sai một cụm sẽ kéo lệch "
    "cả câu. Hãy chạy lại bước “Tách cụm và ghi pinyin” cho dòng này, hoặc điền pinyin bằng tay."
)

#: Tên khoá thường gặp trong `S3_clean.json` → nhãn tiếng Việt.  S3 do người khác
#: viết nên đây cố ý là một bảng dò rộng: thiếu số liệu thì báo cáo lặng lẽ bỏ
#: qua mục đó, chứ không được vì thế mà hỏng cả báo cáo.
_CLEANUP_LABELS: dict[str, str] = {
    "segments_in": "Số đoạn máy nghe được",
    "total_in": "Số đoạn máy nghe được",
    "input_segments": "Số đoạn máy nghe được",
    "total": "Số đoạn máy nghe được",
    "segments_out": "Số đoạn giữ lại",
    "total_out": "Số đoạn giữ lại",
    "kept": "Số đoạn giữ lại",
    "removed": "Tổng số đoạn đã bỏ",
    "dropped": "Tổng số đoạn đã bỏ",
    "repeats_removed": "Đoạn lặp do nghe nhầm (ảo giác) đã cắt",
    "repeat_removed": "Đoạn lặp do nghe nhầm (ảo giác) đã cắt",
    "dropped_repeat": "Đoạn lặp do nghe nhầm (ảo giác) đã cắt",
    "hallucination_removed": "Đoạn lặp do nghe nhầm (ảo giác) đã cắt",
    "hallucinations": "Đoạn lặp do nghe nhầm (ảo giác) đã cắt",
    "empty_removed": "Đoạn rỗng đã bỏ",
    "dropped_empty": "Đoạn rỗng đã bỏ",
    "punct_only_removed": "Đoạn chỉ có dấu câu đã bỏ",
    "dropped_punct_only": "Đoạn chỉ có dấu câu đã bỏ",
    "converted": "Đoạn đổi phồn thể sang giản thể",
    "simplified": "Đoạn đổi phồn thể sang giản thể",
    "to_simplified": "Đoạn đổi phồn thể sang giản thể",
}

_CLEANUP_TOTAL_KEYS = ("segments_in", "total_in", "input_segments", "total")
_CLEANUP_REMOVED_KEYS = (
    "removed",
    "dropped",
    "repeats_removed",
    "repeat_removed",
    "dropped_repeat",
    "hallucination_removed",
    "hallucinations",
    "empty_removed",
    "dropped_empty",
    "punct_only_removed",
    "dropped_punct_only",
)
_CLEANUP_HALLUCINATION_KEYS = (
    "repeats_removed",
    "repeat_removed",
    "dropped_repeat",
    "hallucination_removed",
    "hallucinations",
)


# --------------------------------------------------------------------------- #
# kiểu dữ liệu dùng trong báo cáo
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class StatBlock:
    """Một chỉ số phân bố, đo được đặt cạnh giá trị của bản chuẩn.

    Giữ cả `target_*` ngay trong đối tượng (thay vì tra bảng lúc dựng HTML) để
    con số đo và con số đối chiếu không bao giờ đi lạc nhau khi thêm chỉ số mới.
    """

    key: str
    label: str
    unit: str
    median: float
    p95: float
    minimum: float
    maximum: float
    target_median: float
    target_p95: float

    def verdict(self) -> tuple[str, str]:
        """(lớp CSS, câu nhận xét tiếng Việt) khi so trung vị với bản chuẩn."""
        if self.target_median <= 0:
            return "ok", "Không có mốc đối chiếu"
        ratio = self.median / self.target_median
        if abs(ratio - 1.0) <= _TARGET_TOLERANCE:
            return "ok", "Sát bản chuẩn"
        if ratio > 1.0:
            return "warn", "Cao hơn bản chuẩn"
        return "warn", "Thấp hơn bản chuẩn"

    def to_dict(self) -> dict[str, Any]:
        css, text = self.verdict()
        return {
            "label": self.label,
            "unit": self.unit,
            "median": round(self.median, 3),
            "p95": round(self.p95, 3),
            "min": round(self.minimum, 3),
            "max": round(self.maximum, 3),
            "target_median": self.target_median,
            "target_p95": self.target_p95,
            "verdict": text,
            "ok": css == "ok",
        }


@dataclass(frozen=True)
class ReviewItem:
    """Một cụm còn chờ người duyệt, đủ ngữ cảnh để duyệt mà không cần mở file gốc."""

    cue_index: int
    start: float
    flag: str
    zh: str
    pinyin: str
    zh_line: str
    py_line: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cue_index": self.cue_index,
            "start": round(self.start, 3),
            "flag": self.flag,
            "zh": self.zh,
            "pinyin": self.pinyin,
            "zh_line": self.zh_line,
            "py_line": self.py_line,
        }


@dataclass
class _Section:
    """Một khối trong báo cáo HTML — gom lại để thứ tự mục nằm ở một chỗ duy nhất."""

    anchor: str
    title: str
    body: str
    subtitle: str = ""


@dataclass
class _StageTiming:
    number: int
    label: str
    seconds: float | None = None


@dataclass
class _CleanupInfo:
    """Số liệu chặng S3, đã quy về dạng báo cáo hiểu được."""

    rows: list[tuple[str, float]] = field(default_factory=list)
    total: float | None = None
    hallucination: float | None = None
    ratio: float | None = None

    def has_data(self) -> bool:
        return bool(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": [{"label": lb, "value": v} for lb, v in self.rows],
            "total": self.total,
            "hallucination": self.hallucination,
            "ratio": None if self.ratio is None else round(self.ratio, 4),
            "suggest_demucs": self.suggest_demucs(),
        }

    def suggest_demucs(self) -> bool:
        return self.ratio is not None and self.ratio >= _DEMUCS_HINT_RATIO


# --------------------------------------------------------------------------- #
# điểm vào của chặng
# --------------------------------------------------------------------------- #

def run(
    ctx: Context,
    on_progress: Callable[[str, float], None] | None = None,
) -> dict[str, Any]:
    """Kiểm `Document` lần cuối rồi ghi các file kết quả.

    Trả về ``{"srt", "vi_srt", "bundle", "report", "ass", "bilingual_ass",
    "findings", "has_errors", "translation", "stats", "backups"}``.  Đường dẫn trả
    về dạng chuỗi vì nơi nhận là giao diện web và file JSON tóm tắt của job — cả
    hai đều cần chuỗi, còn phía gọi muốn `Path` thì bọc lại một dòng.

    ``backups`` là đường dẫn các bản cũ **lần chạy này** đã cất đi trước khi ghi
    đè (xem docstring của module); dùng lại kết quả cũ thì danh sách rỗng, vì
    không file nào bị ghi.

    `has_errors=True` **không** ngăn việc ghi file: xem docstring của module.

    File tiếng Việt chỉ được ghi khi chặng dịch để lại đủ số dòng cho **mọi** cue.
    Thiếu một dòng thì thà không có file còn hơn có một file lệch dòng — lệch dòng
    là lỗi khó phát hiện nhất và cũng là lỗi tốn công sửa nhất trong nghề phụ đề.
    """
    started = time.perf_counter()
    cached = _cached_result(ctx)
    if cached is not None:
        _notify(on_progress, "Đã có sẵn kết quả của lần chạy trước, dùng lại.", 1.0)
        return cached

    emit_cfg: dict[str, Any] = dict(ctx.cfg.get("emit") or {})

    _notify(on_progress, "Đang chuẩn bị dữ liệu để xuất file…", 0.03)
    doc = _load_document(ctx)
    meta = _merged_meta(ctx, doc)
    stem = _output_stem(ctx, meta)
    out_dir = io_utils.ensure_dir(ctx.out_dir)

    _notify(on_progress, "Đang kiểm lại toàn bộ quy chuẩn định dạng…", 0.15)
    findings = _annotate_internal(validate_document(doc))
    has_errors = any(f.severity == SEVERITY_ERROR for f in findings)

    cleanup = _cleanup_info(ctx)
    timings = _stage_timings(ctx)
    reviews = collect_reviews(doc)
    stats = collect_stats(doc, findings, reviews=reviews, cleanup=cleanup)

    newline = _newline(emit_cfg)
    bom = bool(emit_cfg.get("bom", True))
    writer = _OutputWriter(on_progress)

    _notify(on_progress, "Đang ghi file phụ đề (.srt)…", 0.4)
    srt_path = out_dir / f"{stem}.srt"
    srt_text = emit_srt(doc)
    writer.write(srt_path, srt_text, kind="srt", bom=bom, newline=newline, fraction=0.4)

    vi_path: Path | None = None
    vi_lines = _translated_lines(ctx, doc)
    translation = _translation_info(ctx, doc)
    if vi_lines:
        _notify(on_progress, "Đang ghi file phụ đề tiếng Việt (_vi.srt)…", 0.5)
        vi_path = out_dir / f"{stem}_vi.srt"
        vi_text = build_vi_srt(doc, vi_lines)
        writer.write(vi_path, vi_text, kind="vi_srt", bom=bom, newline=newline, fraction=0.5)
        pair_findings = validate_vi_text(srt_text, vi_text)
        findings = list(findings) + pair_findings
        has_errors = has_errors or any(f.severity == SEVERITY_ERROR for f in pair_findings)
        stats = collect_stats(doc, findings, reviews=reviews, cleanup=cleanup)
        stats["translation"] = dict(translation)

    bundle_path: Path | None = None
    if _flag(emit_cfg, "bundle", True):
        _notify(on_progress, "Đang ghi dữ liệu cho phần mềm hiển thị (.bundle.json)…", 0.6)
        bundle_path = out_dir / f"{stem}.bundle.json"
        payload = build_bundle(doc, meta=meta, stats=stats, vi_lines=vi_lines)
        writer.write(
            bundle_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
            kind="bundle",
            bom=False,
            newline="\n",
            fraction=0.6,
        )

    ass_path: Path | None = None
    if _flag(emit_cfg, "ass_export", False):
        _notify(on_progress, "Đang ghi bản dành cho Aegisub (.ass)…", 0.7)
        ass_path = out_dir / f"{stem}.ass"
        writer.write(
            ass_path,
            build_ass(doc, title=str(meta.get("title") or stem), cfg=emit_cfg),
            kind="ass",
            bom=True,
            newline=newline,
            fraction=0.7,
        )

    bilingual_path: Path | None = None
    if vi_lines and _flag(emit_cfg, "bilingual_ass", True):
        _notify(on_progress, "Đang ghi bản song ngữ cho Aegisub (_song-ngu.ass)…", 0.78)
        bilingual_path = out_dir / f"{stem}_song-ngu.ass"
        writer.write(
            bilingual_path,
            build_bilingual_ass(
                doc, vi_lines, title=str(meta.get("title") or stem), cfg=emit_cfg
            ),
            kind="bilingual_ass",
            bom=True,
            newline=newline,
            fraction=0.78,
        )

    report_path: Path | None = None
    if _flag(emit_cfg, "report", True):
        _notify(on_progress, "Đang dựng báo cáo để bạn đọc lại…", 0.85)
        timings.append(_StageTiming(STAGE_NUMBER, "Kiểm và xuất file", time.perf_counter() - started))
        report_path = out_dir / f"{stem}.report.html"
        files = _file_rows(
            srt_path, vi_path, bundle_path, report_path, ass_path, bilingual_path, out_dir
        )
        html_text = build_report_html(
            doc,
            findings=findings,
            stats=stats,
            meta=meta,
            timings=timings,
            reviews=reviews,
            cleanup=cleanup,
            files=files,
            has_errors=has_errors,
            ass_exported=ass_path is not None,
            translation=translation,
            bilingual_exported=bilingual_path is not None,
        )
        writer.write(
            report_path, html_text, kind="report", bom=False, newline="\n", fraction=0.9
        )

    stats["elapsed"] = round(time.perf_counter() - started, 3)
    stats["files"] = {
        "srt": str(srt_path),
        "vi_srt": str(vi_path) if vi_path else None,
        "bundle": str(bundle_path) if bundle_path else None,
        "report": str(report_path) if report_path else None,
        "ass": str(ass_path) if ass_path else None,
        "bilingual_ass": str(bilingual_path) if bilingual_path else None,
    }

    result: dict[str, Any] = {
        "srt": str(srt_path),
        "vi_srt": str(vi_path) if vi_path else None,
        "bundle": str(bundle_path) if bundle_path else None,
        "report": str(report_path) if report_path else None,
        "ass": str(ass_path) if ass_path else None,
        "bilingual_ass": str(bilingual_path) if bilingual_path else None,
        "findings": findings,
        "has_errors": has_errors,
        "translation": dict(translation),
        "stats": stats,
        "backups": [str(path) for path in writer.backups],
    }

    _refresh_original(ctx, srt_path, vi_path, on_progress)

    try:
        ctx.save_stage(STAGE_NUMBER, STAGE_KEY, result)
    except (OSError, TypeError, ValueError):
        # Không ghi được điểm lưu thì cũng đừng vứt bỏ ba file vừa xuất thành công.
        pass

    _notify(on_progress, _done_message(result, out_dir), 1.0)
    return result


# --------------------------------------------------------------------------- #
# cất bản cũ trước khi ghi đè (hợp đồng H1) và bản máy tạo cho nút Hoàn nguyên (H2)
# --------------------------------------------------------------------------- #

#: Chữ chèn vào tên bản cũ: ``Phim_vi.srt`` → ``Phim_vi.truoc-<thời điểm>.srt``.
#: Lấy từ ``io_utils`` — nơi duy nhất đặt tên bản cất — để không có chữ thứ hai.
BACKUP_TAG = io_utils.KEPT_COPY_WORD

#: Bản "máy tạo" của trình sửa, trong ``work/<video_id>/``.  Tên và định dạng
#: thuộc về ``srtgen/web/server.py`` (hằng ``ORIGINAL_FILE``, hàm
#: ``_snapshot_original`` ghi và ``revert_editor_document`` đọc); ở đây chỉ nhắc
#: lại tên để chặng xuất file không phải nạp cả máy chủ web.  Một bài kiểm giữ
#: hai hằng số này bằng nhau.
ORIGINAL_FILE = "edit_original.json"
_ORIGINAL_VERSION = 1

#: Bọc những mẩu báo cáo đổi theo mỗi lần chạy (giờ tạo, số giây từng bước).
#: Là chú thích HTML nên người đọc không thấy; nhờ nó, hai báo cáo chỉ khác nhau
#: ở giờ chạy được coi là cùng nội dung và không bị cất thành bản trùng.
_VOLATILE_OPEN = "<!--thay-moi-lan-->"
_VOLATILE_CLOSE = "<!--/thay-moi-lan-->"
_VOLATILE_RE = re.compile(re.escape(_VOLATILE_OPEN) + ".*?" + re.escape(_VOLATILE_CLOSE), re.S)

#: Khoá JSON đổi theo mỗi lần chạy mà không đổi nội dung phụ đề: bỏ qua khi so
#: ``.bundle.json`` cũ với bản sắp ghi.
_VOLATILE_JSON_KEYS = frozenset({"generated_at", "created_at", "elapsed", "saved_at"})


class BackupError(RuntimeError):
    """Không cất được bản cũ, nên chặng xuất file dừng lại thay vì ghi đè.

    Ghi tiếp lúc này là đổi một buổi sửa tay của người dùng lấy một file mới mà
    lần chạy sau vẫn sinh lại được — một cuộc đổi chác không bao giờ đáng.
    Câu thông báo là câu tiếng Việt của ``io_utils.keep_old_copy``: nó nói file
    nào, vì sao, và rằng file đó KHÔNG bị ghi đè.
    """


def backup_stamp() -> str:
    """Thời điểm dùng trong tên bản cất, ``YYYYmmdd-HHMMSS`` theo giờ máy.

    Một lần xuất dùng chung một thời điểm cho mọi bản cất, để người dùng nhìn
    thư mục là thấy những bản nào đi cùng nhau.  Tách thành hàm riêng để bài
    kiểm cố định được thời điểm mà không phải chờ đồng hồ.
    """
    return datetime.now().strftime(io_utils.KEPT_COPY_STAMP)


def _stamp_moment(stamp: str) -> datetime:
    """Chuỗi của :func:`backup_stamp` trở lại thành ``datetime`` cho ``keep_old_copy``.

    Chuỗi không đọc được thì dùng giờ hiện tại: tên bản cất lệch vài giây còn
    hơn là không cất.
    """
    try:
        return datetime.strptime(str(stamp), io_utils.KEPT_COPY_STAMP)
    except ValueError:
        return datetime.now()


def backup_path_for(path: Path, stamp: str) -> Path:
    """Tên bản cất mà ``path`` sẽ nhận ở thời điểm ``stamp``.

    Chỉ là cửa sổ nhìn vào ``io_utils.kept_copy_path`` — nơi duy nhất đặt tên bản
    cất: ``.truoc-<stamp>`` chèn NGAY TRƯỚC đuôi cuối, giữ nguyên hậu tố ``_vi`` và
    ``_song-ngu`` (``Phim_vi.srt`` → ``Phim_vi.truoc-….srt``, còn
    ``Phim.bundle.json`` → ``Phim.bundle.truoc-….json``); trùng tên thì thêm
    ``-1``, ``-2``… — không bao giờ ghi đè một bản cất đã có.
    """
    return io_utils.kept_copy_path(path, _stamp_moment(stamp))


def _volatile(fragment: str) -> str:
    """Đánh dấu một mẩu HTML là "đổi theo mỗi lần chạy" (xem ``_VOLATILE_OPEN``)."""
    return f"{_VOLATILE_OPEN}{fragment}{_VOLATILE_CLOSE}"


def _drop_volatile_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _drop_volatile_keys(v) for k, v in value.items() if k not in _VOLATILE_JSON_KEYS
        }
    if isinstance(value, list):
        return [_drop_volatile_keys(v) for v in value]
    return value


def _comparable(kind: str, text: str) -> str:
    """Phần nội dung thật của một file kết quả, để so bản cũ với bản sắp ghi.

    Phụ đề và file Aegisub so nguyên văn: mỗi ký tự trong đó có thể là do người
    gõ.  Bundle và báo cáo do máy dựng lại từ phụ đề, chỉ khác nhau ở giờ chạy
    thì coi là giống — nếu không, lần chạy lại nào cũng đẻ thêm hai bản cất vô
    nghĩa và người dùng sẽ thôi để ý tới thư mục bản cất.
    """
    if kind == "bundle":
        try:
            data = json.loads(text)
        except ValueError:
            return text
        return json.dumps(_drop_volatile_keys(data), ensure_ascii=False, sort_keys=True)
    if kind == "report":
        return _VOLATILE_RE.sub("", text)
    return text


def _as_written(text: str) -> str:
    """``text`` như ``io_utils.read_text`` sẽ đọc lại sau khi ``write_text`` ghi nó.

    So ở dạng đó chứ không ở dạng byte: khác nhau chỉ ở BOM hay kiểu xuống dòng
    (Aegisub lưu CRLF) thì không có chữ nào của người dùng bị mất, nên không cất.
    """
    body = io_utils.nfc(text).replace("\r\n", "\n").replace("\r", "\n")
    return body if body.endswith("\n") else body + "\n"


def _same_as_disk(path: Path, text: str, kind: str) -> bool:
    """File trên đĩa có đúng nội dung sắp ghi không; đọc không được thì coi là KHÁC.

    "Không đọc được" (file lưu bằng bảng mã khác, file đang bị khoá) mà coi là
    giống thì sẽ ghi đè thứ mình chưa hề nhìn thấy — cất đi mới là hướng an toàn.
    """
    try:
        on_disk = io_utils.read_text(path)
    except (OSError, ValueError):  # ValueError gồm cả lỗi giải mã UTF-8
        return False
    return _comparable(kind, on_disk) == _comparable(kind, _as_written(text))


class _OutputWriter:
    """Ghi các file kết quả của một lần xuất, cất bản cũ trước khi ghi đè.

    Mọi file kết quả đi qua đúng một chỗ này, nên không file nào — kể cả file
    thêm vào sau này — lọt được qua mà không có bản cất.  Bản thân việc cất (đổi
    tên, đặt tên, trùng giây, không đổi tên được thì từ chối) là của
    ``io_utils.keep_old_copy``; ở đây chỉ còn hai phần riêng của chặng xuất file:
    coi bundle/báo cáo chỉ khác giờ chạy là "giống" (:func:`_same_as_disk`), và
    trả bản cũ về đúng tên cũ nếu ghi bản mới hỏng giữa chừng.
    """

    def __init__(self, on_progress: Callable[[str, float], None] | None) -> None:
        self.on_progress = on_progress
        self.moment = _stamp_moment(backup_stamp())
        self.backups: list[Path] = []

    def write(
        self,
        path: Path,
        text: str,
        *,
        kind: str,
        bom: bool,
        newline: str,
        fraction: float,
    ) -> None:
        backup: Path | None = None
        if path.is_file() and not _same_as_disk(path, text, kind):
            try:
                backup = io_utils.keep_old_copy(
                    path,
                    io_utils.encode_text(text, bom=bom, newline=newline),
                    now=self.moment,
                )
            except io_utils.KeepCopyError as err:
                raise BackupError(str(err)) from err
        try:
            io_utils.atomic_write_text(path, text, bom=bom, newline=newline)
        except BaseException:
            # Ghi hỏng sau khi đã dời bản cũ đi: trả bản cũ về chỗ, để người dùng
            # mở thư mục ra vẫn thấy file của mình đúng tên như trước.
            if backup is not None and not self._put_back(backup, path):
                self.backups.append(backup)
            raise
        if backup is not None:
            self.backups.append(backup)
            _notify(
                self.on_progress,
                f"Đã cất bản cũ của “{path.name}” thành “{backup.name}” (cùng thư mục) "
                "trước khi ghi file mới, vì bản cũ khác bản sắp ghi — có thể là bản bạn "
                "đã sửa tay.",
                fraction,
            )

    @staticmethod
    def _put_back(backup: Path, path: Path) -> bool:
        if path.exists():
            return False
        try:
            backup.rename(path)
            return True
        except OSError:
            return False


def _refresh_original(
    ctx: Context,
    srt_path: Path,
    vi_path: Path | None,
    on_progress: Callable[[str, float], None] | None,
) -> None:
    """Ghi lại ``work/<video_id>/edit_original.json`` bằng bản vừa xuất (hợp đồng H2).

    Máy chủ chỉ chụp bản này trước lần lưu tay đầu tiên rồi giữ mãi.  Sau một lần
    chạy lại (bảng tên đổi chẳng hạn), nút "Hoàn nguyên về bản máy tạo" sẽ quay
    về bản máy tạo CŨ — tức là vứt luôn tên mới — nếu chặng xuất không làm mới nó.

    Định dạng giống hệt ``_snapshot_original`` của máy chủ.  Nội dung đọc lại từ
    file vừa ghi, đúng cách máy chủ đọc, để Hoàn nguyên ghi ra đúng từng ký tự.
    Hỏng thì chỉ báo: mất nút hoàn nguyên còn hơn mất cả lần xuất file.
    """
    path = ctx.work_dir / ORIGINAL_FILE
    try:
        payload = {
            "version": _ORIGINAL_VERSION,
            "saved_at": time.time(),
            "srt_path": str(srt_path),
            "srt": io_utils.read_text(srt_path),
            "vi_path": str(vi_path) if vi_path else "",
            "vi": io_utils.read_text(vi_path) if vi_path else "",
        }
        io_utils.ensure_dir(path.parent)
        io_utils.atomic_write_text(
            path, json.dumps(payload, ensure_ascii=False, indent=2), bom=False, newline="\n"
        )
    except (OSError, ValueError) as err:
        _notify(
            on_progress,
            "Đã xuất file xong nhưng chưa cập nhật được bản máy tạo cho nút “Hoàn nguyên” "
            f"của trình sửa ({type(err).__name__}: {err}). File phụ đề không bị ảnh hưởng.",
            0.95,
        )


# --------------------------------------------------------------------------- #
# nạp dữ liệu vào
# --------------------------------------------------------------------------- #

def _load_document(ctx: Context) -> Document:
    """Lấy `Document` từ `ctx.doc`, nếu chưa có thì đọc ngược S7 → S6 → S5.

    Đọc ngược chứ không đọc xuôi vì chặng càng muộn thì dữ liệu càng hoàn chỉnh;
    chỉ khi chặng muộn chưa chạy mới lùi về chặng sớm hơn.
    """
    if isinstance(ctx.doc, Document) and ctx.doc.cues:
        return ctx.doc

    # Đọc từ S8 trước vì đó là chặng muộn nhất, và vì `S8_translate.json` mang
    # theo cả bản dịch — nạp từ S7 rồi mới đi tìm bản dịch ở chỗ khác thì file
    # tiếng Việt sẽ lặng lẽ biến mất khi chạy tiếp bằng `--from s9`.
    for number, name in ((8, "translate"), (7, "ai"), (6, "norm"), (5, "tokens")):
        payload = ctx.load_stage(number, name)
        doc = _document_from_payload(payload)
        if doc is not None and doc.cues:
            if number == 8 and isinstance(payload, dict) and _translate_enabled(ctx):
                _attach_saved_lines(doc, payload)
            ctx.doc = doc
            return doc

    raise RuntimeError(
        "Không có dòng phụ đề nào để xuất file. Có hai khả năng: các bước trước (tách cụm, "
        "chuẩn hoá) chưa chạy xong hoặc kết quả đã bị xoá — hãy chạy lại từ bước “Tách cụm "
        "và ghi pinyin”; hoặc máy không nghe được câu thoại nào trong video — hãy kiểm tra "
        "lại file âm thanh nguồn."
    )


def _translate_enabled(ctx: Context) -> bool:
    """Phần dịch có đang bật trong cấu hình không.

    Chỉ dùng để quyết định có **đọc lại bản dịch cũ trên đĩa** hay không. Bản dịch
    vừa sinh ra trong cùng lần chạy (nằm ở `doc.meta`) luôn được dùng bất kể cờ
    này: chặng dịch đã chạy rồi thì kết quả của nó không được biến mất giữa đường.
    Người dùng vừa gõ `--no-translate` mà vẫn nhận một `_vi.srt` dịch từ lần chạy
    trước — có thể đã cũ so với bản phụ đề vừa sửa — là điều họ không hề yêu cầu.
    """
    from srtgen.providers import translate_config

    return _flag(translate_config(ctx.cfg), "enabled", True)


def _attach_saved_lines(doc: Document, payload: dict[str, Any]) -> None:
    """Gắn lại bản dịch đã lưu vào tài liệu vừa nạp từ `S8_translate.json`."""
    from srtgen.stages.s8_translate import META_INFO, META_LINES

    rows = payload.get("lines")
    if not isinstance(rows, list) or len(rows) != len(doc.cues):
        return
    doc.meta[META_LINES] = [
        str(row.get("vi") or "") if isinstance(row, dict) else "" for row in rows
    ]
    doc.meta[META_INFO] = {
        key: payload.get(key)
        for key in ("target", "provider", "provider_reason", "warning", "model")
        if payload.get(key)
    }


def _translated_lines(ctx: Context, doc: Document) -> list[str]:
    """Dòng tiếng Việt của từng cue, hoặc `[]` khi không dùng được.

    Ưu tiên `doc.meta` (chặng dịch vừa chạy trong cùng lần này), rồi mới đọc
    `S8_translate.json` — đường thứ hai là cho `srtgen resume <id> --from s9`,
    khi tài liệu được nạp lại từ đĩa và `doc.meta` không còn bản dịch.

    Trả về `[]` nếu độ dài lệch số cue. Đó là quy tắc **thà thiếu file còn hơn
    sai file**: một `_vi.srt` lệch dòng trông vẫn bình thường khi mở lên, và
    người dùng chỉ phát hiện ra sau khi đã ghép xong cả tập phim.
    """
    from srtgen.stages.s8_translate import vi_lines_of

    lines = vi_lines_of(doc)
    if lines:
        return lines

    if not _translate_enabled(ctx):
        return []

    payload = ctx.load_stage(8, "translate")
    if not isinstance(payload, dict):
        return []
    rows = payload.get("lines")
    if not isinstance(rows, list) or len(rows) != len(doc.cues):
        return []
    # Hàng hỏng phải thành **một dòng rỗng tại đúng vị trí đó**, không được lọc
    # ra khỏi danh sách: lọc làm danh sách ngắn đi mà phép so độ dài ở trên đã
    # chạy xong rồi, nên mọi cue sau hàng hỏng bị dồn lên đúng một dòng — chính
    # cái lệch dòng mà cả chặng này sinh ra để chống, lại còn lệch im lặng vì số
    # block và mốc thời gian vẫn khớp tuyệt đối. Dòng rỗng thì `build_vi_srt` tự
    # lấy nguyên văn tiếng Trung cho cue đó, giống hệt `_attach_saved_lines`.
    return [str(row.get("vi") or "") if isinstance(row, dict) else "" for row in rows]


def _translation_info(ctx: Context, doc: Document) -> dict[str, Any]:
    """Mô tả chặng dịch cho báo cáo: nhà cung cấp, số câu, cảnh báo, cue giữ nguyên."""
    from srtgen.stages.s8_translate import translation_info

    info = translation_info(doc)
    payload = ctx.load_stage(8, "translate")
    if isinstance(payload, dict):
        for key in ("target", "provider", "provider_reason", "warning", "model"):
            if not info.get(key) and payload.get(key):
                info[key] = payload[key]
        if not info.get("counts") and isinstance(payload.get("counts"), dict):
            info["counts"] = dict(payload["counts"])
        if isinstance(payload.get("kept_original"), list):
            info["kept_original"] = list(payload["kept_original"])
        if isinstance(payload.get("glossary"), dict):
            info["glossary"] = dict(payload["glossary"])
        if isinstance(payload.get("errors"), list):
            info["errors"] = list(payload["errors"])
    return info


def _document_from_payload(payload: Any) -> Document | None:
    """Dựng `Document` từ nội dung một file chặng, chấp nhận vài kiểu bọc khác nhau.

    Các chặng trước có thể lưu thẳng `doc.to_stage_dict()` hoặc bọc thêm một lớp
    (`{"doc": ..., "log": ...}`).  Chấp nhận cả hai ở đây rẻ hơn nhiều so với việc
    bắt tám chặng phải thống nhất một lớp bọc mà không ai kiểm được.
    """
    if isinstance(payload, Document):
        return payload
    if isinstance(payload, list):
        cues = [Cue.from_stage_dict(c) for c in payload if isinstance(c, dict)]
        return Document(cues=cues) if cues else None
    if not isinstance(payload, dict):
        return None

    if "cues" not in payload:
        for key in ("doc", "document", "result", "data"):
            inner = payload.get(key)
            if isinstance(inner, (dict, list)):
                return _document_from_payload(inner)
        return None

    try:
        return Document.from_stage_dict(payload)
    except (TypeError, ValueError, AttributeError):
        return None


def _cached_result(ctx: Context) -> dict[str, Any] | None:
    """Dùng lại kết quả cũ khi resume — nhưng chỉ khi file thật sự còn trên đĩa.

    Điểm lưu của S8 chỉ là một bản tóm tắt; nếu người dùng đã xoá hoặc di chuyển
    file kết quả thì bản tóm tắt đó thành lời nói dối. Kiểm sự tồn tại của file
    rẻ hơn nhiều so với việc để giao diện hiện nút "Mở thư mục" trỏ vào hư không.
    """
    if not ctx.has_stage(STAGE_NUMBER, STAGE_KEY):
        return None
    payload = ctx.load_stage(STAGE_NUMBER, STAGE_KEY)
    if not isinstance(payload, dict) or not payload.get("srt"):
        return None

    for key in ("srt", "vi_srt", "bundle", "report", "ass", "bilingual_ass"):
        value = payload.get(key)
        if value and not Path(str(value)).is_file():
            return None

    findings: list[Finding] = []
    for item in payload.get("findings") or []:
        if isinstance(item, dict):
            try:
                findings.append(
                    Finding(
                        code=str(item.get("code", "")),
                        severity=str(item.get("severity", SEVERITY_ERROR)),
                        cue_index=item.get("cue_index"),
                        message=str(item.get("message", "")),
                        line=str(item.get("line", "")),
                    )
                )
            except (TypeError, ValueError):
                return None
    payload["findings"] = findings
    payload["has_errors"] = bool(payload.get("has_errors"))
    payload["stats"] = payload.get("stats") or {}
    payload["translation"] = payload.get("translation") or {}
    # Dùng lại kết quả cũ nghĩa là lần này không ghi, nên cũng không cất gì.
    # Để nguyên danh sách của lần xuất trước thì giao diện sẽ báo "đã cất bản cũ"
    # một lần nữa cho một việc không hề xảy ra.
    payload["backups"] = []
    return payload


def _merged_meta(ctx: Context, doc: Document) -> dict[str, Any]:
    """Gộp meta của tài liệu với meta của lần chạy, bỏ những khoá không để đọc.

    `parse_findings` bị loại vì nó là nhật ký của lúc *đọc* file nguồn, không mô
    tả file *xuất ra*; để lọt vào bundle sẽ khiến backend tưởng file mới có lỗi.
    `vi_lines` và `translation` bị loại vì chúng là **dữ liệu**, không phải meta:
    dòng tiếng Việt đi vào bundle theo từng cue (xem `build_bundle`), còn ở đây
    chúng chỉ tạo ra một mảng nghìn phần tử bị cắt cụt nằm giữa phần thông tin
    video — vừa vô dụng vừa dễ bị đọc nhầm là danh sách đầy đủ.
    """
    merged: dict[str, Any] = {}
    for source in (doc.meta, ctx.meta):
        for key, value in (source or {}).items():
            if key in ("parse_findings", "vi_lines", "translation"):
                continue
            if value is None or value == "":
                continue
            merged[key] = value

    merged.setdefault("video_id", ctx.video_id)
    profile = ctx.cfg.get("profile")
    if profile:
        merged.setdefault("profile", profile)
    asr_cfg = ctx.cfg.get("asr") or {}
    if asr_cfg.get("model"):
        merged.setdefault("model", asr_cfg["model"])
    merged.setdefault("generated_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    return merged


def _output_stem(ctx: Context, meta: dict[str, Any]) -> str:
    """Tên gốc của ba file: ưu tiên tiêu đề video vì người dùng tìm file theo tên phim.

    `video_id` chỉ là phương án cuối — nó an toàn cho hệ thống file nhưng vô nghĩa
    với người mở thư mục Downloads sau ba ngày.
    """
    for candidate in (meta.get("title"), meta.get("name"), ctx.video_id):
        text = str(candidate or "").strip()
        if text:
            stem = io_utils.safe_stem(text, fallback=ctx.video_id or "phu-de")
            if stem:
                return stem
    return "phu-de"


# --------------------------------------------------------------------------- #
# kiểm định
# --------------------------------------------------------------------------- #

def _annotate_internal(findings: Iterable[Finding]) -> list[Finding]:
    """Đính kèm lời giải thích "lỗi này đến từ đâu" cho các mã lỗi phòng thủ.

    Không sửa `rules.py` vì cùng những mã đó, khi chạy từ lệnh `check` trên file
    người khác gửi, lại là lỗi dữ liệu thật. Chỉ ở đường xuất file — nơi hai dòng
    chắc chắn cùng một token list — chúng mới đổi nghĩa.
    """
    out: list[Finding] = []
    for f in findings:
        note = _internal_note_for(f)
        if not note or note in f.message:
            out.append(f)
            continue
        out.append(
            Finding(
                code=f.code,
                severity=f.severity,
                cue_index=f.cue_index,
                message=f"{f.message} {note}",
                line=f.line,
            )
        )
    return out


def _internal_note_for(finding: Finding) -> str:
    """Chọn ghi chú đúng cho một finding, hoặc chuỗi rỗng nếu không cần ghi chú.

    `rules.py` gộp hai tình huống rất khác nhau vào cùng mã `CUM_MISMATCH`: lệch
    số cụm giữa hai dòng (chỉ có thể là bug renderer) và cụm chưa có pinyin (là
    hành vi ĐÚNG của bước ghép — nó từ chối đoán).  Dán nhãn "bug của tool" lên
    cái thứ hai sẽ khiến người dùng đi báo lỗi nhầm chỗ, nên phải tách ra.
    """
    if finding.code == CUM_MISMATCH and _MISSING_PINYIN_MARK in finding.message:
        return _MISSING_PINYIN_NOTE
    if finding.code not in _INTERNAL_CODES:
        return ""
    if "bộ render" in finding.message:
        return _REPORT_BUG_NOTE
    return _INTERNAL_NOTE


# --------------------------------------------------------------------------- #
# thống kê
# --------------------------------------------------------------------------- #

def collect_reviews(doc: Document) -> list[ReviewItem]:
    """Hàng đợi "cần người duyệt": mọi token còn mang cờ AI/nhập nhằng.

    Dựng một lần rồi dùng cho cả `stats` lẫn bảng trong báo cáo, để hai con số
    không thể lệch nhau.
    """
    wanted = (FLAG_AI_APPLIED, FLAG_CASE_AMBIG, FLAG_HETERONYM)
    items: list[ReviewItem] = []
    for cue in doc.cues:
        zh_line = ""
        py_line = ""
        for tok in cue.tokens:
            flags = [f for f in wanted if f in tok.flags]
            if not flags:
                continue
            if not zh_line:
                zh_line = render_zh(cue.tokens)
                py_line = render_py(cue.tokens)
            for flag in flags:
                items.append(
                    ReviewItem(
                        cue_index=cue.index,
                        start=float(cue.start),
                        flag=flag,
                        zh=tok.zh,
                        pinyin=tok.pinyin or "",
                        zh_line=zh_line,
                        py_line=py_line,
                    )
                )
    return items


def collect_stats(
    doc: Document,
    findings: Sequence[Finding],
    *,
    reviews: Sequence[ReviewItem] | None = None,
    cleanup: _CleanupInfo | None = None,
) -> dict[str, Any]:
    """Số liệu tóm tắt cho báo cáo, cho bundle và cho giao diện — một nguồn duy nhất."""
    reviews = list(reviews if reviews is not None else collect_reviews(doc))
    durations = [max(0.0, c.duration()) for c in doc.cues]
    hans = [c.han_count() for c in doc.cues]
    clusters = [c.word_count() for c in doc.cues]

    blocks = [
        _stat_block("duration", "Thời lượng mỗi dòng", "giây", durations),
        _stat_block("han", "Số chữ Hán mỗi dòng", "chữ", [float(v) for v in hans]),
        _stat_block("clusters", "Số cụm mỗi dòng", "cụm", [float(v) for v in clusters]),
    ]

    counts = summarize(findings)
    flag_counts: dict[str, int] = {}
    for item in reviews:
        flag_counts[item.flag] = flag_counts.get(item.flag, 0) + 1
    name_count = sum(
        1 for cue in doc.cues for tok in cue.tokens if FLAG_NAME in tok.flags
    )

    stats: dict[str, Any] = {
        "cue_count": len(doc.cues),
        "word_count": sum(clusters),
        "han_count": sum(hans),
        "total_duration": round(doc.duration(), 3),
        "distribution": {b.key: b.to_dict() for b in blocks},
        "findings": counts,
        "errors": counts.get(SEVERITY_ERROR, 0),
        "warnings": counts.get(SEVERITY_WARN, 0),
        "infos": counts.get(SEVERITY_INFO, 0),
        "review_count": len(reviews),
        "flags": flag_counts,
        "name_tokens": name_count,
        "empty_cues": sum(1 for c in clusters if c == 0),
    }
    if cleanup is not None and cleanup.has_data():
        stats["cleanup"] = cleanup.to_dict()
    return stats


def _stat_block(key: str, label: str, unit: str, values: Sequence[float]) -> StatBlock:
    target_median, target_p95 = TARGETS.get(key, (0.0, 0.0))
    return StatBlock(
        key=key,
        label=label,
        unit=unit,
        median=_percentile(values, 0.5),
        p95=_percentile(values, 0.95),
        minimum=min(values) if values else 0.0,
        maximum=max(values) if values else 0.0,
        target_median=target_median,
        target_p95=target_p95,
    )


def _percentile(values: Sequence[float], q: float) -> float:
    """Phân vị nội suy tuyến tính.

    Tự cài thay vì dùng `statistics.quantiles` để con số đo được so sánh trực tiếp
    với các số trong build-spec (chúng đo bằng đúng công thức này), và để không
    phụ thuộc vào cách chia nhóm của `statistics` giữa các bản Python.
    """
    if not values:
        return 0.0
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _stage_timings(ctx: Context) -> list[_StageTiming]:
    """Thời gian từng chặng, lấy từ `ctx.meta` chứ không đi đọc lại file chặng.

    `S2_asr.json` của một video 40 phút có thể tới vài chục MB vì chứa mốc thời
    gian từng chữ; mở nó ra chỉ để lấy một con số giây là cái giá vô lý cho một
    dòng trong bảng.  Chặng nào không báo thời gian thì báo cáo ghi "—".
    """
    raw: dict[str, Any] = {}
    for key in ("timings", "stage_times", "elapsed"):
        value = ctx.meta.get(key)
        if isinstance(value, dict):
            raw.update(value)

    lookup: dict[str, float] = {}
    for key, value in raw.items():
        seconds = _as_float(value)
        if seconds is None:
            continue
        lookup[str(key).strip().lower()] = seconds

    out: list[_StageTiming] = []
    for number, name, label in STAGE_NAMES:
        if number == STAGE_NUMBER:
            continue  # S8 tự đo, thêm vào sau khi ghi xong file
        candidates = (f"s{number}", str(number), name, f"s{number}_{name}", label.lower())
        seconds = next((lookup[c] for c in candidates if c in lookup), None)
        out.append(_StageTiming(number, label, seconds))
    return out


def _cleanup_info(ctx: Context) -> _CleanupInfo:
    """Số liệu chặng dọn ASR, dò trong `ctx.meta` trước rồi mới đọc `S3_clean.json`."""
    payload: Any = None
    for key in ("cleanup", "clean_stats", "s3"):
        value = ctx.meta.get(key)
        if isinstance(value, dict):
            payload = value
            break
    if payload is None:
        payload = ctx.load_stage(3, "clean")

    found = _find_stats_dict(payload)
    info = _CleanupInfo()
    if not found:
        return info

    seen: set[str] = set()
    for key, value in found.items():
        label = _CLEANUP_LABELS.get(str(key).strip().lower())
        number = _as_float(value)
        if label is None or number is None or label in seen:
            continue
        seen.add(label)
        info.rows.append((label, number))

    info.total = _first_number(found, _CLEANUP_TOTAL_KEYS)
    info.hallucination = _sum_numbers(found, _CLEANUP_HALLUCINATION_KEYS)
    removed = _first_number(found, ("removed", "dropped"))
    if removed is None:
        removed = _sum_numbers(found, _CLEANUP_REMOVED_KEYS)
    basis = info.hallucination if info.hallucination is not None else removed
    if info.total and info.total > 0 and basis is not None:
        info.ratio = basis / info.total
    return info


def _find_stats_dict(payload: Any) -> dict[str, Any]:
    """Tìm cái dict chứa số liệu, dù S3 để nó ở gốc hay bọc trong "stats"."""
    if not isinstance(payload, dict):
        return {}
    for key in ("stats", "cleanup", "summary", "report"):
        inner = payload.get(key)
        if isinstance(inner, dict) and any(
            str(k).strip().lower() in _CLEANUP_LABELS for k in inner
        ):
            return inner
    if any(str(k).strip().lower() in _CLEANUP_LABELS for k in payload):
        return payload
    return {}


def _first_number(data: dict[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        for raw_key, value in data.items():
            if str(raw_key).strip().lower() == key:
                number = _as_float(value)
                if number is not None:
                    return number
    return None


def _sum_numbers(data: dict[str, Any], keys: Sequence[str]) -> float | None:
    """Cộng các khoá đồng nghĩa nhưng chỉ tính mỗi khoá thật một lần.

    Bảng nhãn cố tình cho nhiều tên khoá cùng trỏ về một nhãn; nếu cộng cả nhóm
    đồng nghĩa thì một chặng ghi đủ hai tên sẽ làm số liệu gấp đôi.
    """
    total: float | None = None
    counted: set[str] = set()
    for raw_key, value in data.items():
        name = str(raw_key).strip().lower()
        if name not in keys:
            continue
        label = _CLEANUP_LABELS.get(name, name)
        if label in counted:
            continue
        number = _as_float(value)
        if number is None:
            continue
        counted.add(label)
        total = number if total is None else total + number
    return total


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# bundle.json
# --------------------------------------------------------------------------- #

def build_bundle(
    doc: Document,
    *,
    meta: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
    vi_lines: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Dựng nội dung `<title>.bundle.json` cho backend.

    Shape của từng segment do `Token.to_dict()` quyết định và **không được dựng
    lại ở đây**: đó là hợp đồng với backend (`kind` là ``word``/``punctuation``,
    khoá chữ là ``text``, dấu câu và marker mang ``pinyin: null``).  Chặng này chỉ
    bọc thêm mốc thời gian, các dòng đã render và meta của video — những thứ
    backend cần để hiển thị mà không phải đọc lại file `.srt`.

    Khoá ``vi`` là phần thêm mới: trình sửa trong app hiện ba cột Hán / pinyin /
    Việt cạnh nhau, và bắt nó ghép hai file `.srt` lại theo chỉ số là mời gọi
    đúng cái lệch dòng mà cả chặng này sinh ra để chống.
    """
    cues: list[dict[str, Any]] = []
    for pos, cue in enumerate(doc.cues):
        payload = cue.to_dict()
        payload["start_time"] = format_timestamp(cue.start)
        payload["end_time"] = format_timestamp(cue.end)
        payload["zh"] = render_zh(cue.tokens)
        payload["pinyin"] = render_py(cue.tokens)
        if vi_lines is not None and pos < len(vi_lines):
            payload["vi"] = vi_line(vi_lines[pos], payload["zh"])
        cues.append(payload)

    bundle: dict[str, Any] = {
        "version": 1,
        "generator": "srtgen",
        "format": "srt-4line",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "meta": _public_meta(meta if meta is not None else doc.meta),
        "cues": cues,
    }
    if stats:
        bundle["stats"] = {
            "cue_count": stats.get("cue_count"),
            "word_count": stats.get("word_count"),
            "han_count": stats.get("han_count"),
            "total_duration": stats.get("total_duration"),
            "errors": stats.get("errors"),
            "warnings": stats.get("warnings"),
        }
    return bundle


def _public_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Chỉ đưa ra ngoài những giá trị vô hại và tuần tự hoá được.

    Meta nội bộ có thể chứa `Path`, danh sách finding hoặc cả cấu hình; bundle là
    file gửi cho hệ thống khác nên phải là dữ liệu sạch, không phải bãi rác nhật ký.
    """
    out: dict[str, Any] = {}
    for key, value in (meta or {}).items():
        name = str(key)
        if name.startswith("_") or name in ("parse_findings", "cfg", "config"):
            continue
        if isinstance(value, (str, int, float, bool)):
            out[name] = value
        elif isinstance(value, Path):
            out[name] = str(value)
        elif isinstance(value, (list, tuple)) and all(
            isinstance(v, (str, int, float, bool)) for v in value
        ):
            out[name] = list(value)[:50]
    return out


# --------------------------------------------------------------------------- #
# <title>_vi.srt — bản tiếng Việt
# --------------------------------------------------------------------------- #

#: Dấu câu tiếng Trung → dấu ASCII, cho **riêng dòng tiếng Việt**.  Đây là chỗ
#: duy nhất trong tool đi ngược luật dấu câu của README, và đi ngược có chủ ý:
#: README nói về dòng chữ Hán, còn đây là tiếng Việt, viết “Chào anh，” là sai.
#:
#: Ba nhóm cố ý **không** đổi:
#: * ngoặc kép “ ” ‘ ’ — tiếng Việt vẫn dùng đúng những dấu này, đổi sang " ' là
#:   làm xấu đi chứ không phải chuẩn hoá; đổi còn làm lệch phép so marker đổi
#:   người nói giữa hai dòng vì bộ tách coi ' là chữ chứ không phải dấu câu;
#: * 《》 vì prompt yêu cầu giữ nguyên tên tác phẩm;
#: * dấu … vì đặc tả liệt kê nó trong nhóm dấu được dùng.
_VI_PUNCT: dict[str, str] = {
    "，": ", ",
    "、": ", ",
    "。": ". ",
    "？": "? ",
    "！": "! ",
    "：": ": ",
    "；": "; ",
    "（": " (",
    "）": ") ",
    "　": " ",
}

#: Không được có khoảng trắng **trước** những dấu này sau khi đã đổi.
_VI_TIGHT_LEFT = ",.?!:;)…"


def build_vi_srt(doc: Document, lines: Sequence[str]) -> str:
    """Dựng nội dung ``<title>_vi.srt`` từ cùng một `Document`.

    Toàn bộ hợp đồng ở build-spec-v2 mục 2 được giữ **bằng cấu tạo**, không phải
    bằng cách kiểm rồi sửa:

    * Vòng lặp bỏ qua cue đúng theo luật của :func:`~srtgen.core.srt.emit_srt`
      (không có chữ Hán lẫn pinyin thì bỏ), nên hai file luôn có cùng số block.
    * Số thứ tự đếm theo vị trí, mốc thời gian lấy từ **chính cue đó** qua đúng
      hàm ``format_timestamp`` mà file tiếng Trung dùng — nên hai mốc giống nhau
      đến từng ký tự, không phải "gần giống".
    * Dòng tiếng Việt trống được thay bằng văn bản gốc chứ không làm block rỗng:
      bỏ một cue ở một file là làm lệch mọi cue sau nó ở cả hai file.

    ``lines`` là danh sách **theo vị trí** trong ``doc.cues``.  Danh sách ngắn hơn
    số cue không phải lỗi (chặng dịch có thể dừng giữa chừng): phần thiếu lấy văn
    bản gốc.
    """
    parts: list[str] = []
    number = 0
    for pos, cue in enumerate(doc.cues):
        zh_line = render_zh(cue.tokens)
        py_line = render_py(cue.tokens)
        if not zh_line and not py_line:
            continue
        number += 1
        raw = lines[pos] if pos < len(lines) else ""
        parts.append(
            "\n".join(
                (
                    str(number),
                    f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}",
                    vi_line(raw, zh_line),
                )
            )
        )
        parts.append("\n\n")
    return "".join(parts)


def vi_line(text: str, fallback: str) -> str:
    """Một dòng tiếng Việt đã sạch, không bao giờ rỗng.

    Ba việc, theo đúng thứ tự đó: đổi dấu câu tiếng Trung sang ASCII, dọn khoảng
    trắng, rồi đồng bộ marker đổi người nói với dòng chữ Hán.  Làm marker sau
    cùng vì bước dọn khoảng trắng có thể ăn mất dấu cách sau dấu ``-``.
    """
    cleaned = _vi_spacing(_vi_punct(str(text or "")))
    if not cleaned:
        cleaned = _vi_spacing(str(fallback or "").strip())
    if not cleaned:
        # Cue chỉ có nhạc nền mà cũng không có chữ Hán: giữ block bằng ký hiệu
        # nhạc thay vì để dòng trống, vì dòng trống kết thúc block trong Aegisub.
        return "♪"
    return _sync_marker(cleaned, str(fallback or ""))


def _vi_punct(text: str) -> str:
    """Đổi dấu câu tiếng Trung sang ASCII, để nguyên chữ."""
    out = text.replace("……", "…").replace("——", " — ")
    for src, dst in _VI_PUNCT.items():
        if len(src) == 1:
            out = out.replace(src, dst)
    return out


def _vi_spacing(text: str) -> str:
    """Bỏ khoảng trắng thừa — thứ mà `VI_TRAILING_SPACE` coi là lỗi.

    File tiếng Việt người dùng đang dùng thật có space thừa cuối dòng; đặc tả
    gọi đó là lỗi của file đó, nên tool phải cắt sạch chứ không được chép theo.
    """
    cleaned = str(text or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    cleaned = "".join(ch for ch in cleaned if ch >= " " or ch == " ")
    for mark in _VI_TIGHT_LEFT:
        cleaned = cleaned.replace(f" {mark}", mark)
    cleaned = cleaned.replace("( ", "(")
    while "  " in cleaned:
        cleaned = cleaned.replace("  ", " ")
    return cleaned.strip()


def _sync_marker(text: str, zh_line: str) -> str:
    """Giữ dấu ``-`` đổi người nói đúng chỗ nó đứng ở dòng chữ Hán.

    Chỉ xử lý marker **đầu dòng**, là trường hợp duy nhất suy ra được chắc chắn:
    marker giữa dòng phụ thuộc vào chỗ ngắt câu của tiếng Việt, mà chỗ đó chỉ
    người dịch mới biết.  ``VI_MARKER_SYNC`` là cảnh báo chứ không phải lỗi cũng
    vì lý do đó.
    """
    zh_marked = zh_line.lstrip().startswith("- ")
    vi_marked = text.startswith("- ")
    if zh_marked and not vi_marked:
        return f"- {text}"
    if vi_marked and not zh_marked:
        return text[2:].lstrip()
    return text


def validate_vi_text(zh_text: str, vi_text: str) -> list[Finding]:
    """Kiểm cặp file vừa ghi bằng đúng bộ luật của `rules.validate_pair`.

    Đây là **assert phòng thủ**, giống `CUM_MISMATCH` ở đường xuất file: hai file
    được dựng ra từ cùng một danh sách cue nên chúng không thể lệch vì nội dung.
    Nếu mục nào ở đây nổ thì bộ dựng file có bug, và báo cáo phải nói đúng như
    vậy chứ không được đổ cho người dịch.

    Bộ luật nằm ở `rules.py` chứ không ở đây, nhưng chặng xuất file **không được
    phép chết** vì bảng luật đổi chữ ký: mất phép kiểm này thì file vẫn ghi ra
    đúng, còn ném traceback sau ba mươi phút chạy máy thì mất cả buổi của người
    dùng. Nên có một phép so tối thiểu (số block và mốc thời gian) làm lưới cuối.
    """
    checker = _rule_attr("validate_pair", None)
    if callable(checker):
        try:
            return list(checker(zh_text, vi_text))
        except Exception:  # pragma: no cover - lưới cuối, xem docstring
            pass
    return _minimal_pair_check(zh_text, vi_text)


def _minimal_pair_check(zh_text: str, vi_text: str) -> list[Finding]:
    """Hai ràng buộc cứng nhất của mục 2: cùng số block, cùng mốc thời gian."""
    zh_stamps = _stamps_of(zh_text)
    vi_stamps = _stamps_of(vi_text)
    findings: list[Finding] = []
    if len(zh_stamps) != len(vi_stamps):
        findings.append(
            Finding(
                code=VI_BLOCK_COUNT,
                severity=SEVERITY_ERROR,
                cue_index=None,
                message=(
                    f"File tiếng Việt có {len(vi_stamps)} dòng phụ đề trong khi file tiếng "
                    f"Trung có {len(zh_stamps)}. Hai file phải bằng nhau. {_REPORT_BUG_NOTE}"
                ),
            )
        )
    for n, (left, right) in enumerate(zip(zh_stamps, vi_stamps), start=1):
        if left != right:
            findings.append(
                Finding(
                    code=VI_TIMESTAMP_DRIFT,
                    severity=SEVERITY_ERROR,
                    cue_index=n,
                    message=(
                        f"Mốc thời gian lệch: file tiếng Trung ghi “{left}”, "
                        f"file tiếng Việt ghi “{right}”. {_REPORT_BUG_NOTE}"
                    ),
                    line=right,
                )
            )
    return findings


def _stamps_of(text: str) -> list[str]:
    """Danh sách dòng mốc thời gian, đọc thô.

    Đọc thô chứ không qua bộ phân tích của `srtgen.core.srt`: bộ đó dựng
    `Document` 4 dòng, còn file tiếng Việt chỉ có 3 dòng — đưa nó vào đó sẽ nhận
    lại một thông báo lỗi vô nghĩa thay vì phép so sánh cần làm ở đây.
    """
    return [
        row.strip()
        for row in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if "-->" in row
    ]


# --------------------------------------------------------------------------- #
# .ass cho Aegisub
# --------------------------------------------------------------------------- #

def build_ass(doc: Document, *, title: str = "", cfg: dict[str, Any] | None = None) -> str:
    """Xuất cùng nội dung sang Advanced SubStation Alpha để soát trong Aegisub.

    Aegisub coi mỗi sự kiện là một dòng thoại, nên hai dòng của chúng ta phải nối
    bằng ``\\N`` trong **một** sự kiện — tách thành hai sự kiện sẽ làm mất liên
    kết chữ Hán ↔ pinyin, đúng thứ toàn bộ kiến trúc này sinh ra để bảo vệ.

    Hai style được khai báo sẵn: chữ Hán dùng font macOS "PingFang SC", dòng
    pinyin ép sang "Arial Unicode MS" bằng thẻ nội dòng vì không phải font Hán nào
    cũng có đủ nguyên âm mang dấu thanh (ǎ ě ǚ ǹ).  Ép bằng thẻ nội dòng thay vì
    `\\r<style>` để không đụng tới canh lề của sự kiện.
    """
    cfg = cfg or {}
    han_font = str(cfg.get("ass_font") or "PingFang SC")
    pinyin_font = str(cfg.get("ass_font_pinyin") or "Arial Unicode MS")
    size = _as_float(cfg.get("ass_font_size")) or 42.0
    pinyin_size = max(12.0, round(size * 0.8))

    style_fields = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    tail = "0,0,0,0,100,100,0,0,1,2.5,1,2,60,60,50,1"
    colours = "&H00FFFFFF,&H000000FF,&H00000000,&H80000000"

    lines: list[str] = [
        "[Script Info]",
        f"Title: {_ass_text(title or 'srtgen')}",
        "Original Script: srtgen",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "",
        "[V4+ Styles]",
        style_fields,
        f"Style: Default,{han_font},{int(size)},{colours},{tail}",
        f"Style: Pinyin,{pinyin_font},{int(pinyin_size)},{colours},{tail}",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    override = "{\\fn" + pinyin_font + "\\fs" + str(int(pinyin_size)) + "}"
    for cue in doc.cues:
        zh_line = _ass_text(render_zh(cue.tokens))
        py_line = _ass_text(render_py(cue.tokens))
        if not zh_line and not py_line:
            continue  # dòng rỗng trong .ass cũng vô nghĩa như trong .srt
        text = zh_line + "\\N" + override + py_line if py_line else zh_line
        lines.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Default,,0,0,0,,{text}"
        )
    return "\n".join(lines) + "\n"


def build_bilingual_ass(
    doc: Document,
    lines: Sequence[str],
    *,
    title: str = "",
    cfg: dict[str, Any] | None = None,
) -> str:
    """Xuất bản **song ngữ** cho Aegisub: ba dòng trong một sự kiện.

    Đây là file người dùng mở để soát cả hai ngôn ngữ cùng lúc, nên ba dòng phải
    nằm trong **một** sự kiện nối bằng ``\\N``: tách thành ba sự kiện là mất liên
    kết Hán ↔ pinyin ↔ Việt, đúng thứ cả kiến trúc này sinh ra để giữ.

    Ba style được khai báo trong ``[V4+ Styles]`` để người dùng đổi font ngay
    trong Aegisub, nhưng ba dòng vẫn phải phân biệt bằng **thẻ nội dòng**: ASS
    chỉ cho một sự kiện mang một style.  Font Hán mặc định là ``PingFang SC``
    (macOS), dòng pinyin và dòng tiếng Việt lùi về ``Arial Unicode MS`` vì không
    phải font Hán nào cũng có đủ nguyên âm mang dấu thanh lẫn dấu tiếng Việt.
    """
    cfg = cfg or {}
    han_font = str(cfg.get("ass_font") or "PingFang SC")
    alt_font = str(cfg.get("ass_font_pinyin") or "Arial Unicode MS")
    size = _as_float(cfg.get("ass_font_size")) or 42.0
    py_size = max(12.0, round(size * 0.72))
    vi_size = max(12.0, round(size * 0.82))

    style_fields = (
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
    )
    tail = "0,0,0,0,100,100,0,0,1,2.5,1,2,60,60,50,1"
    # Màu ASS viết ngược: &HBBGGRR. Trắng cho chữ Hán, xám nhạt cho pinyin,
    # vàng cho tiếng Việt — ba mức tương phản khác nhau để mắt bắt được dòng nào
    # là dòng nào mà không phải đọc chữ.
    han_colour = "&H00FFFFFF"
    py_colour = "&H00C8C8C8"
    vi_colour = "&H0000E5FF"

    lines_out: list[str] = [
        "[Script Info]",
        f"Title: {_ass_text(title or 'srtgen')} (song ngữ)",
        "Original Script: srtgen",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "",
        "[V4+ Styles]",
        style_fields,
        f"Style: Han,{han_font},{int(size)},{han_colour},&H000000FF,&H00000000,&H80000000,{tail}",
        f"Style: Pinyin,{alt_font},{int(py_size)},{py_colour},&H000000FF,&H00000000,&H80000000,{tail}",
        f"Style: Viet,{alt_font},{int(vi_size)},{vi_colour},&H000000FF,&H00000000,&H80000000,{tail}",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    py_tag = "{\\fn" + alt_font + "\\fs" + str(int(py_size)) + "\\c" + py_colour + "}"
    vi_tag = "{\\fn" + alt_font + "\\fs" + str(int(vi_size)) + "\\c" + vi_colour + "}"

    for pos, cue in enumerate(doc.cues):
        zh_line = render_zh(cue.tokens)
        py_line = render_py(cue.tokens)
        if not zh_line and not py_line:
            continue
        raw = lines[pos] if pos < len(lines) else ""
        parts = [_ass_text(zh_line)]
        if py_line:
            parts.append(py_tag + _ass_text(py_line))
        vi_text = _ass_text(vi_line(raw, zh_line))
        if vi_text:
            parts.append(vi_tag + vi_text)
        lines_out.append(
            f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Han,,0,0,0,,"
            + "\\N".join(parts)
        )
    return "\n".join(lines_out) + "\n"


def _ass_text(text: str) -> str:
    """Làm sạch một dòng trước khi đưa vào trường Text của ASS.

    Dấu ngoặc nhọn mở đầu một khối lệnh trong ASS, nên một dấu `{` lọt từ phụ đề
    vào sẽ nuốt mất phần chữ đứng sau nó. Đổi sang dạng toàn chiều rộng giữ được
    ý nghĩa hiển thị mà không còn là ký tự điều khiển.
    """
    cleaned = (text or "").replace("{", "｛").replace("}", "｝")
    cleaned = cleaned.replace("\r", "").replace("\n", "\\N")
    return "".join(ch for ch in cleaned if ch >= " " or ch == "\t")


def _ass_time(seconds: float) -> str:
    """`H:MM:SS.cc` — ASS chỉ có một chữ số giờ và hai chữ số phần trăm giây."""
    value = 0.0 if not math.isfinite(seconds) or seconds < 0 else float(seconds)
    centis = int(round(value * 100))
    centis = min(centis, 9 * 360000 + 59 * 6000 + 59 * 100 + 99)
    hours, rest = divmod(centis, 360000)
    minutes, rest = divmod(rest, 6000)
    secs, cs = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


# --------------------------------------------------------------------------- #
# báo cáo HTML
# --------------------------------------------------------------------------- #

_REPORT_CSS = """
:root{
  --bg:#f6f7f9; --card:#ffffff; --ink:#1c2024; --muted:#5f6b76; --line:#e2e6ea;
  --accent:#1f5fa8; --ok:#1e7b3c; --ok-bg:#e8f6ec; --warn:#8a5a00; --warn-bg:#fdf3e0;
  --err:#b3261e; --err-bg:#fdecea; --info:#2a6f97; --info-bg:#e9f2f8;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#14171a; --card:#1c2024; --ink:#e8ebee; --muted:#a3adb8; --line:#2c3238;
    --accent:#7fb2ea; --ok:#7fd39b; --ok-bg:#183024; --warn:#e6bd6a; --warn-bg:#332a16;
    --err:#f29b95; --err-bg:#3a1f1d; --info:#8ec6e6; --info-bg:#172c38;
  }
}
*{box-sizing:border-box}
body{
  margin:0; padding:32px 20px 72px; background:var(--bg); color:var(--ink);
  font:16px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Helvetica Neue",Arial,sans-serif;
}
.wrap{max-width:1040px;margin:0 auto}
h1{font-size:26px;margin:0 0 6px}
h2{font-size:19px;margin:0 0 4px}
h3{font-size:16px;margin:22px 0 8px}
p{margin:0 0 12px}
.lead{color:var(--muted);margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin:0 0 20px}
.badge{display:inline-block;padding:6px 14px;border-radius:999px;font-weight:600;font-size:14px}
.badge.ok{background:var(--ok-bg);color:var(--ok)}
.badge.err{background:var(--err-bg);color:var(--err)}
.badge.warn{background:var(--warn-bg);color:var(--warn)}
.tiles{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 0}
.tile{flex:1 1 150px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.tile .n{font-size:24px;font-weight:650;line-height:1.2}
.tile .l{color:var(--muted);font-size:13px}
.tile.err .n{color:var(--err)} .tile.warn .n{color:var(--warn)} .tile.ok .n{color:var(--ok)}
table{width:100%;border-collapse:collapse;margin:6px 0 4px;font-size:15px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.03em}
tr:last-child td{border-bottom:none}
td.num,th.num{text-align:right;white-space:nowrap}
.scroll{overflow-x:auto}
.zh{font-size:16px;font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif}
.py{color:var(--muted);font-size:14px}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;word-break:break-all}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;font-weight:600;white-space:nowrap}
.tag.err{background:var(--err-bg);color:var(--err)}
.tag.warn{background:var(--warn-bg);color:var(--warn)}
.tag.info{background:var(--info-bg);color:var(--info)}
.tag.ok{background:var(--ok-bg);color:var(--ok)}
.group{border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:0 0 14px}
.group>.head{display:flex;flex-wrap:wrap;align-items:center;gap:10px;margin-bottom:6px}
.group .why{color:var(--muted);font-size:14px;margin:2px 0 10px}
.item{border-top:1px solid var(--line);padding:10px 0}
.item:first-of-type{border-top:none}
.item .where{font-size:13px;color:var(--muted);margin-bottom:2px}
.bar{height:7px;border-radius:4px;background:var(--accent);opacity:.75;min-width:2px}
.note{background:var(--info-bg);border-left:4px solid var(--info);border-radius:8px;padding:12px 16px;margin:12px 0}
.note.warn{background:var(--warn-bg);border-left-color:var(--warn)}
.note.err{background:var(--err-bg);border-left-color:var(--err)}
.note.ok{background:var(--ok-bg);border-left-color:var(--ok)}
ol,ul{margin:0 0 12px;padding-left:22px}
li{margin:0 0 7px}
.muted{color:var(--muted)}
.small{font-size:13px}
footer{color:var(--muted);font-size:13px;text-align:center;margin-top:28px}
@media print{
  body{background:#fff;padding:0}
  .card,.group{break-inside:avoid;border-color:#ccc}
}
"""


def build_report_html(
    doc: Document,
    *,
    findings: Sequence[Finding],
    stats: dict[str, Any],
    meta: dict[str, Any],
    timings: Sequence[_StageTiming] = (),
    reviews: Sequence[ReviewItem] | None = None,
    cleanup: _CleanupInfo | None = None,
    files: Sequence[tuple[str, str]] = (),
    has_errors: bool = False,
    ass_exported: bool = False,
    translation: Mapping[str, Any] | None = None,
    bilingual_exported: bool = False,
) -> str:
    """Dựng toàn bộ `<title>.report.html`.

    Tự chứa hoàn toàn: CSS nội tuyến, không ảnh, không CDN, không JavaScript — mở
    được trên máy không có mạng và gửi kèm email vẫn xem được.  Người đọc mặc định
    là người biên tập phụ đề, không phải lập trình viên, nên mọi mã lỗi đều đi kèm
    một câu giải thích và mọi con số đều có đơn vị.
    """
    reviews = list(reviews if reviews is not None else collect_reviews(doc))
    sections: list[_Section] = [
        _section_summary(stats, has_errors, len(reviews)),
        _section_video(meta, stats),
        _section_timings(timings),
        _section_distribution(stats),
        _section_translate(translation),
        _section_findings(findings),
        _section_reviews(reviews),
        _section_cleanup(cleanup),
        _section_parse_findings(doc),
        _section_files(files),
        _section_aegisub(ass_exported, bilingual_exported),
    ]

    title = str(meta.get("title") or meta.get("video_id") or "Phụ đề")
    generated = _format_when(meta.get("generated_at"))
    badge = (
        '<span class="badge err">Có lỗi cần sửa</span>'
        if has_errors
        else '<span class="badge ok">Đạt quy chuẩn định dạng</span>'
    )

    nav = " · ".join(
        f'<a href="#{s.anchor}" style="color:var(--accent);text-decoration:none">{_h(s.title)}</a>'
        for s in sections
        if s.body
    )

    body: list[str] = [
        '<div class="wrap">',
        f"<h1>Báo cáo phụ đề — {_h(title)}</h1>",
        f'<p class="lead">{badge} &nbsp; <span class="muted">Tạo lúc '
        f"{_volatile(_h(generated))}</span></p>",
        f'<p class="small muted">{nav}</p>',
    ]
    for section in sections:
        if not section.body:
            continue
        body.append(f'<section class="card" id="{section.anchor}">')
        body.append(f"<h2>{_h(section.title)}</h2>")
        if section.subtitle:
            body.append(f'<p class="muted small">{_h(section.subtitle)}</p>')
        # Số giây từng bước đổi theo mỗi lần chạy; đánh dấu để phép so "nội dung
        # giống hệt" khi cất bản cũ không coi đó là một báo cáo khác.
        body.append(_volatile(section.body) if section.anchor == "thoi-gian" else section.body)
        body.append("</section>")
    body.append(
        "<footer>Báo cáo do srtgen tạo tự động. File này xem được khi không có mạng; "
        "bạn có thể gửi kèm cho người soát.</footer>"
    )
    body.append("</div>")

    return (
        "<!DOCTYPE html>\n"
        '<html lang="vi">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Báo cáo phụ đề — {_h(title)}</title>\n"
        f"<style>{_REPORT_CSS}</style>\n</head>\n<body>\n"
        + "\n".join(body)
        + "\n</body>\n</html>\n"
    )


def _section_summary(stats: dict[str, Any], has_errors: bool, reviews: int) -> _Section:
    errors = int(stats.get("errors", 0) or 0)
    warnings = int(stats.get("warnings", 0) or 0)
    tiles = [
        ("ok", stats.get("cue_count", 0), "dòng phụ đề"),
        ("ok", _fmt_duration(float(stats.get("total_duration", 0) or 0)), "tổng thời lượng"),
        ("err" if errors else "ok", errors, "lỗi phải sửa"),
        ("warn" if warnings else "ok", warnings, "cảnh báo nên xem"),
        ("warn" if reviews else "ok", reviews, "mục chờ người duyệt"),
    ]
    html_tiles = "".join(
        f'<div class="tile {css}"><div class="n">{_h(value)}</div>'
        f'<div class="l">{_h(label)}</div></div>'
        for css, value, label in tiles
    )
    if has_errors:
        note = (
            '<div class="note err"><strong>File vẫn được ghi ra đĩa bình thường.</strong> '
            "Tool không giữ lại kết quả chỉ vì có lỗi — bạn mở file lên sửa tay được ngay. "
            "Danh sách lỗi kèm số dòng nằm ở mục “Kết quả kiểm định dạng” bên dưới.</div>"
        )
    else:
        note = (
            '<div class="note ok">Không còn lỗi định dạng nào. File đạt cả 12 mục của quy '
            "chuẩn và import thẳng vào Aegisub được.</div>"
        )
    return _Section("tong-quan", "Tổng quan", f'<div class="tiles">{html_tiles}</div>{note}')


def _section_video(meta: dict[str, Any], stats: dict[str, Any]) -> _Section:
    labels = (
        ("title", "Tên video"),
        ("video_id", "Mã lưu trữ"),
        ("source", "Nguồn"),
        ("source_url", "Đường dẫn nguồn"),
        ("duration", "Thời lượng video"),
        ("model", "Mô hình nghe (ASR)"),
        ("profile", "Bộ tham số"),
        ("language", "Ngôn ngữ"),
        ("created_at", "Bắt đầu chạy"),
        ("generated_at", "Xuất file lúc"),
    )
    rows: list[str] = []
    for key, label in labels:
        value = meta.get(key)
        if value in (None, ""):
            continue
        if key == "duration":
            number = _as_float(value)
            shown = _fmt_duration(number) if number is not None else str(value)
        elif key in ("created_at", "generated_at"):
            shown = _format_when(value)
        else:
            shown = str(value)
        css = ' class="mono"' if key in ("source", "source_url", "video_id") else ""
        cell = _h(shown)
        if key in ("created_at", "generated_at"):
            cell = _volatile(cell)
        rows.append(f"<tr><th>{_h(label)}</th><td{css}>{cell}</td></tr>")

    rows.append(
        f'<tr><th>Nội dung</th><td>{stats.get("cue_count", 0)} dòng phụ đề · '
        f'{stats.get("word_count", 0)} cụm · {stats.get("han_count", 0)} chữ Hán</td></tr>'
    )
    return _Section("thong-tin", "Thông tin video", f'<div class="scroll"><table>{"".join(rows)}</table></div>')


def _section_timings(timings: Sequence[_StageTiming]) -> _Section:
    known = [t for t in timings if t.seconds is not None]
    if not known:
        return _Section("thoi-gian", "Thời gian chạy từng bước", "")
    longest = max(t.seconds or 0.0 for t in known) or 1.0
    total = sum(t.seconds or 0.0 for t in known)

    rows: list[str] = []
    for timing in timings:
        if timing.seconds is None:
            rows.append(
                f'<tr><td>{_h(timing.label)} <span class="muted small">S{timing.number}</span></td>'
                f'<td class="num muted">—</td><td></td></tr>'
            )
            continue
        width = max(2.0, (timing.seconds / longest) * 100.0)
        rows.append(
            f'<tr><td>{_h(timing.label)} <span class="muted small">S{timing.number}</span></td>'
            f'<td class="num">{_h(_fmt_duration(timing.seconds))}</td>'
            f'<td style="width:45%"><div class="bar" style="width:{width:.1f}%"></div></td></tr>'
        )
    rows.append(
        f'<tr><td><strong>Tổng cộng</strong></td>'
        f'<td class="num"><strong>{_h(_fmt_duration(total))}</strong></td><td></td></tr>'
    )
    return _Section(
        "thoi-gian",
        "Thời gian chạy từng bước",
        f'<div class="scroll"><table>{"".join(rows)}</table></div>',
        "Bước nghe và gỡ băng luôn lâu nhất; các bước còn lại thường chỉ vài giây.",
    )


def _section_distribution(stats: dict[str, Any]) -> _Section:
    dist = stats.get("distribution") or {}
    if not dist:
        return _Section("thong-ke", "Cách chia dòng so với bản chuẩn", "")

    rows = [
        "<tr><th>Chỉ số</th><th class='num'>Trung vị</th><th class='num'>Bản chuẩn</th>"
        "<th class='num'>95% dòng dưới</th><th class='num'>Bản chuẩn</th>"
        "<th class='num'>Thấp nhất</th><th class='num'>Cao nhất</th><th>Nhận xét</th></tr>"
    ]
    for item in dist.values():
        css = "ok" if item.get("ok") else "warn"
        unit = item.get("unit", "")
        rows.append(
            "<tr>"
            f"<td>{_h(item.get('label', ''))}</td>"
            f"<td class='num'>{_h(_fmt_number(item.get('median')))} {_h(unit)}</td>"
            f"<td class='num muted'>{_h(_fmt_number(item.get('target_median')))}</td>"
            f"<td class='num'>{_h(_fmt_number(item.get('p95')))} {_h(unit)}</td>"
            f"<td class='num muted'>{_h(_fmt_number(item.get('target_p95')))}</td>"
            f"<td class='num muted'>{_h(_fmt_number(item.get('min')))}</td>"
            f"<td class='num muted'>{_h(_fmt_number(item.get('max')))}</td>"
            f"<td><span class='tag {css}'>{_h(item.get('verdict', ''))}</span></td>"
            "</tr>"
        )
    body = f'<div class="scroll"><table>{"".join(rows)}</table></div>'
    body += (
        '<p class="small muted">“Bản chuẩn” là bộ phụ đề 1351 dòng đã có người soát tay, '
        "dùng làm mốc đối chiếu. Lệch nhiều không có nghĩa là sai — phim thoại nhanh hay "
        "chậm hơn thì con số khác đi — nhưng lệch nhiều mà bạn thấy phụ đề khó đọc thì đây "
        "là chỗ để chỉnh: mở phần Cài đặt nâng cao và sửa giới hạn số chữ hoặc thời lượng "
        "mỗi dòng.</p>"
    )
    empty = int(stats.get("empty_cues", 0) or 0)
    if empty:
        body += (
            f'<div class="note warn">Có {empty} dòng không chứa chữ nào. Tool đã bỏ qua chúng '
            "khi ghi file để không tạo dòng trống bên trong khối phụ đề.</div>"
        )
    return _Section("thong-ke", "Cách chia dòng so với bản chuẩn", body)


#: Tên tiếng Việt của nhà cung cấp dịch, kèm câu nói rõ nên trông đợi gì.
_TRANSLATE_PROVIDERS: dict[str, tuple[str, str]] = {
    "gemini": (
        "Gemini (dùng mã API của bạn)",
        "Dịch theo lô có ngữ cảnh và bảng tên riêng, nên xưng hô và tên nhân vật "
        "nhất quán trong cả tập phim.",
    ),
    "google_free": (
        "Google bản miễn phí",
        "Dịch từng câu rời, không biết câu trước câu sau, nên xưng hô và tên nhân "
        "vật thường không nhất quán.",
    ),
    "null": (
        "Không dịch",
        "Dòng tiếng Việt giữ nguyên văn bản tiếng Trung để hai file vẫn khớp nhau.",
    ),
}


def _section_translate(translation: Mapping[str, Any] | None) -> _Section:
    """Mục "Dịch sang tiếng Việt": số liệu, cảnh báo, và các cue giữ nguyên văn.

    Danh sách cue giữ nguyên văn là phần có giá trị nhất của mục này: đó chính là
    danh sách việc người soát phải làm tay, kèm số dòng phụ đề để tìm được ngay.
    """
    info = dict(translation or {})
    if not info:
        return _Section("dich", "Dịch sang tiếng Việt", "")

    counts = dict(info.get("counts") or {})
    total = int(counts.get("cues", 0) or 0)
    done = int(counts.get("translated", 0) or 0)
    kept = int(counts.get("kept_original", 0) or 0)
    cached = int(counts.get("from_cache", 0) or 0)
    requests = int(counts.get("requests", 0) or 0)

    name = str(info.get("provider") or "null")
    label, note = _TRANSLATE_PROVIDERS.get(name, (name, ""))

    tiles = [
        ("ok" if done else "warn", done, "câu đã dịch"),
        ("warn" if kept else "ok", kept, "câu giữ nguyên văn"),
        ("ok", cached, "câu dùng lại bản cũ"),
        ("ok", requests, "lượt gọi dịch vụ dịch"),
    ]
    body = '<div class="tiles">' + "".join(
        f'<div class="tile {css}"><div class="n">{_h(value)}</div>'
        f'<div class="l">{_h(text)}</div></div>'
        for css, value, text in tiles
    ) + "</div>"

    rows = [f"<tr><th>Dịch vụ dùng</th><td>{_h(label)}</td></tr>"]
    if note:
        rows.append(f'<tr><th>Nghĩa là</th><td>{_h(note)}</td></tr>')
    if info.get("model"):
        rows.append(f"<tr><th>Mô hình</th><td>{_h(info['model'])}</td></tr>")
    glossary = dict(info.get("glossary") or {})
    if glossary.get("size"):
        rows.append(
            f"<tr><th>Bảng tên riêng</th><td>{int(glossary['size'])} tên được ép dùng "
            f"đúng một cách viết trong cả phim</td></tr>"
        )
    if total:
        rows.append(f"<tr><th>Tổng số dòng</th><td>{total} dòng phụ đề</td></tr>")
    body += f'<div class="scroll"><table>{"".join(rows)}</table></div>'

    reason = str(info.get("provider_reason") or "").strip()
    if reason:
        body += f'<div class="note warn">{_h(reason)}</div>'
    warning = str(info.get("warning") or "").strip()
    if warning:
        body += f'<div class="note warn">{_h(warning)}</div>'

    # Lý do dịch hỏng, viết một lần cho mỗi lý do khác nhau: một mã API sai sẽ
    # sinh ra hàng chục lỗi giống hệt, in đủ cả chỉ làm người đọc bỏ qua chúng.
    seen: set[str] = set()
    for row in list(info.get("errors") or [])[:6]:
        if not isinstance(row, Mapping):
            continue
        message = str(row.get("user_message") or "").strip()
        if message and message not in seen:
            seen.add(message)
            body += f'<div class="note err">{_h(message)}</div>'

    kept_rows = [row for row in (info.get("kept_original") or []) if isinstance(row, dict)]
    if kept_rows:
        items = [
            "<tr><th class='num'>Dòng</th><th>Câu gốc</th><th>Vì sao</th></tr>"
        ]
        for row in kept_rows[:80]:
            # "block" là số thứ tự người dùng thấy trong file; hai khoá kia chỉ là
            # phương án lùi khi bản dịch đến từ đường khác (lệnh `srtgen translate`).
            number = row.get("block") or row.get("index")
            if number is None:
                position = row.get("position")
                number = (int(position) + 1) if isinstance(position, int) else "—"
            items.append(
                "<tr>"
                f"<td class='num'>{_h(number)}</td>"
                f"<td class='zh'>{_h(_shorten(str(row.get('zh') or ''), 90))}</td>"
                f"<td class='small muted'>{_h(_shorten(str(row.get('reason') or ''), 160))}</td>"
                "</tr>"
            )
        more = (
            f'<p class="small muted">… và {len(kept_rows) - 80} dòng nữa cùng loại.</p>'
            if len(kept_rows) > 80
            else ""
        )
        body += (
            '<div class="group"><div class="head">'
            '<span class="tag warn">Cần dịch tay</span>'
            f'<span class="muted small">{len(kept_rows)} dòng</span></div>'
            '<p class="why">Những dòng này giữ nguyên văn bản tiếng Trung trong file '
            "tiếng Việt. Tool cố ý không bỏ chúng đi: bỏ một dòng là làm lệch toàn bộ "
            "phần sau của cả hai file. Hãy mở file _vi.srt và dịch tay đúng những dòng "
            "có số thứ tự dưới đây.</p>"
            f'<div class="scroll"><table>{"".join(items)}</table></div>{more}</div>'
        )
    elif done and not kept:
        body += '<div class="note ok">Mọi dòng đều đã có bản dịch tiếng Việt.</div>'

    return _Section(
        "dich",
        "Dịch sang tiếng Việt",
        body,
        "File tiếng Việt có đúng số dòng và đúng mốc thời gian của file tiếng Trung, "
        "nên mở song song hai file lúc nào cũng khớp.",
    )


def _section_findings(findings: Sequence[Finding]) -> _Section:
    if not findings:
        return _Section(
            "loi",
            "Kết quả kiểm định dạng",
            '<div class="note ok">Đã kiểm đủ 19 luật của quy chuẩn và không phát hiện vi phạm nào.</div>',
        )

    grouped: dict[str, list[Finding]] = {}
    for f in findings:
        grouped.setdefault(f.code, []).append(f)

    order = {SEVERITY_ERROR: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}
    codes = sorted(
        grouped,
        key=lambda c: (order.get(grouped[c][0].severity, 9), -len(grouped[c]), c),
    )

    parts: list[str] = []
    for code in codes:
        items = grouped[code]
        severity = items[0].severity
        label = _ALL_RULES.get(code, (severity, code))[1]
        css = {SEVERITY_ERROR: "err", SEVERITY_WARN: "warn"}.get(severity, "info")
        severity_text = {
            SEVERITY_ERROR: "Phải sửa",
            SEVERITY_WARN: "Nên xem lại",
        }.get(severity, "Ghi chú")

        rows: list[str] = []
        for f in items[:40]:
            where = f"Dòng phụ đề số {f.cue_index}" if f.cue_index else "Toàn file"
            excerpt = (
                f'<div class="zh">{_h(_shorten(f.line, 160))}</div>' if f.line.strip() else ""
            )
            rows.append(
                f'<div class="item"><div class="where">{_h(where)}</div>'
                f"<div>{_h(f.message)}</div>{excerpt}</div>"
            )
        if len(items) > 40:
            rows.append(
                f'<div class="item muted small">… và {len(items) - 40} mục nữa cùng loại. '
                "Sửa xong nhóm này rồi chạy lại phần Kiểm tra file để xem danh sách còn lại.</div>"
            )

        parts.append(
            f'<div class="group"><div class="head">'
            f'<span class="tag {css}">{_h(severity_text)}</span>'
            f"<h3 style='margin:0'>{_h(label)}</h3>"
            f'<span class="muted small">{len(items)} mục · mã {_h(code)}</span></div>'
            f'<p class="why">{_h(_EXPLAIN.get(code, ""))}</p>'
            f'{"".join(rows)}</div>'
        )

    counts = summarize(findings)
    head = (
        f'<p>Tìm thấy <strong>{counts.get(SEVERITY_ERROR, 0)}</strong> lỗi phải sửa, '
        f'<strong>{counts.get(SEVERITY_WARN, 0)}</strong> cảnh báo nên xem lại.</p>'
    )
    return _Section("loi", "Kết quả kiểm định dạng", head + "".join(parts))


def _section_reviews(reviews: Sequence[ReviewItem]) -> _Section:
    if not reviews:
        return _Section(
            "duyet",
            "Các mục chờ người duyệt",
            '<div class="note ok">Không có mục nào đang chờ duyệt.</div>',
        )

    by_flag: dict[str, list[ReviewItem]] = {}
    for item in reviews:
        by_flag.setdefault(item.flag, []).append(item)

    parts: list[str] = [
        f"<p>Có <strong>{len(reviews)}</strong> cụm tool muốn bạn xác nhận. "
        "Chúng đã nằm sẵn trong file phụ đề — đây không phải lỗi, chỉ là những chỗ "
        "máy không đủ căn cứ để chắc chắn.</p>"
    ]
    for flag in (FLAG_AI_APPLIED, FLAG_CASE_AMBIG, FLAG_HETERONYM):
        items = by_flag.get(flag)
        if not items:
            continue
        rows = [
            "<tr><th class='num'>Dòng</th><th>Thời điểm</th><th>Cụm</th><th>Pinyin</th>"
            "<th>Câu chứa cụm</th></tr>"
        ]
        for item in items[:150]:
            rows.append(
                "<tr>"
                f"<td class='num'>{item.cue_index}</td>"
                f"<td class='mono'>{_h(format_timestamp(item.start))}</td>"
                f"<td class='zh'>{_h(item.zh)}</td>"
                f"<td>{_h(item.pinyin)}</td>"
                f"<td><div class='zh'>{_h(_shorten(item.zh_line, 90))}</div>"
                f"<div class='py'>{_h(_shorten(item.py_line, 110))}</div></td>"
                "</tr>"
            )
        more = (
            f'<p class="small muted">… và {len(items) - 150} mục nữa cùng loại.</p>'
            if len(items) > 150
            else ""
        )
        parts.append(
            f'<div class="group"><div class="head">'
            f'<span class="tag warn">{_h(_FLAG_LABELS.get(flag, flag))}</span>'
            f'<span class="muted small">{len(items)} mục</span></div>'
            f'<p class="why">{_h(_FLAG_WHY.get(flag, ""))}</p>'
            f'<div class="scroll"><table>{"".join(rows)}</table></div>{more}</div>'
        )
    return _Section("duyet", "Các mục chờ người duyệt", "".join(parts))


def _section_cleanup(cleanup: _CleanupInfo | None) -> _Section:
    if cleanup is None or not cleanup.has_data():
        return _Section("nghe", "Chất lượng phần nghe", "")

    rows = "".join(
        f"<tr><th>{_h(label)}</th><td class='num'>{_h(_fmt_number(value, decimals=0))}</td></tr>"
        for label, value in cleanup.rows
    )
    body = f'<div class="scroll"><table>{rows}</table></div>'
    if cleanup.ratio is not None:
        percent = f"{cleanup.ratio * 100:.1f}%".replace(".", ",")
        if cleanup.suggest_demucs():
            body += (
                f'<div class="note warn"><strong>{percent} số đoạn phải cắt bỏ vì máy nghe '
                "nhầm.</strong> Tỉ lệ này cao, thường gặp ở phim có nhạc nền lớn. Lần sau hãy "
                "bật tuỳ chọn <em>Tách giọng khỏi nhạc nền</em> trong phần Cài đặt nâng cao "
                "(trong file cấu hình là <span class='mono'>audio.demucs: true</span>). Bước đó "
                "làm máy chạy lâu hơn đáng kể nhưng phần chữ sẽ sạch hơn nhiều.</div>"
            )
        else:
            body += (
                f'<div class="note ok">Chỉ {percent} số đoạn phải cắt bỏ vì máy nghe nhầm. '
                "Mức này bình thường, chưa cần bật tách nhạc nền.</div>"
            )
    return _Section(
        "nghe",
        "Chất lượng phần nghe",
        body,
        "Số liệu của bước dọn kết quả nghe: máy tự cắt những đoạn lặp vô nghĩa "
        "thường xuất hiện ở khúc chỉ có nhạc.",
    )


def _section_parse_findings(doc: Document) -> _Section:
    """Nhật ký lúc đọc file nguồn (chỉ có ở lệnh `fix`).

    Cố ý KHÔNG gộp vào danh sách lỗi chính: đó là lỗi của file người khác gửi
    sang và tool vừa sửa xong, tính vào `has_errors` sẽ khiến giao diện báo đỏ cho
    một thứ đã được xử lý.
    """
    raw = doc.meta.get("parse_findings")
    if not isinstance(raw, list) or not raw:
        return _Section("nguon", "Ghi nhận khi đọc file nguồn", "")

    counts: dict[str, int] = {}
    for item in raw:
        if isinstance(item, dict):
            code = str(item.get("code", "?"))
            counts[code] = counts.get(code, 0) + 1
    if not counts:
        return _Section("nguon", "Ghi nhận khi đọc file nguồn", "")

    rows = "".join(
        f"<tr><th>{_h(_ALL_RULES.get(code, ('', code))[1])} "
        f"<span class='muted small'>{_h(code)}</span></th>"
        f"<td class='num'>{count}</td></tr>"
        for code, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    return _Section(
        "nguon",
        "Ghi nhận khi đọc file nguồn",
        f'<div class="scroll"><table>{rows}</table></div>'
        '<p class="small muted">Đây là những chỗ chưa chuẩn trong file gốc bạn đưa vào. '
        "Chúng đã được sửa khi ghi file mới, liệt kê ở đây chỉ để bạn biết file gốc có gì.</p>",
    )


def _section_files(files: Sequence[tuple[str, str]]) -> _Section:
    if not files:
        return _Section("file", "File đã tạo", "")
    rows = "".join(
        f"<tr><th>{_h(label)}</th><td class='mono'>{_h(path)}</td></tr>" for label, path in files
    )
    return _Section("file", "File đã tạo", f'<div class="scroll"><table>{rows}</table></div>')


def _section_aegisub(ass_exported: bool, bilingual_exported: bool = False) -> _Section:
    steps = [
        "Mở Aegisub, chọn <strong>File → Open Subtitles…</strong> rồi chọn file "
        "<span class='mono'>.srt</span> ở bảng trên.",
        "Nếu Aegisub hỏi bảng mã, chọn <strong>UTF-8</strong>. File đã có sẵn dấu nhận dạng "
        "UTF-8 nên thường Aegisub tự nhận đúng, không hỏi gì.",
        "Mỗi dòng phụ đề gồm hai dòng chữ: chữ Hán ở trên, pinyin ở dưới. Trong Aegisub cả "
        "hai nằm chung <strong>một</strong> sự kiện và hiện thành ký hiệu "
        "<span class='mono'>\\N</span> ở giữa. <strong>Đây là đúng</strong>, không phải lỗi — "
        "chữ Hán và pinyin phải đi liền nhau thì mới khớp cụm được.",
        "Muốn nhìn rõ chữ: <strong>Subtitle → Styles Manager</strong>, chọn font đọc được cả "
        "chữ Hán lẫn dấu thanh pinyin. Trên macOS nên dùng <em>PingFang SC</em>, nếu thiếu "
        "nguyên âm có dấu thì đổi sang <em>Arial Unicode MS</em>.",
        # KHÔNG khuyên xuất ngược từ Aegisub. Với file song ngữ, mỗi câu trong
        # Aegisub có ba dòng chữ; xuất ra .srt là ra một file sai quy chuẩn mà
        # chính màn hình kết quả của tool đang cảnh báo. Và từ khi có tab "Sửa
        # phụ đề", sửa trong app là đường duy nhất giữ được hai file khớp nhau.
        "Aegisub ở đây chỉ để <strong>xem và soát</strong>. Thấy câu nào sai thì ghi lại số "
        "thứ tự của câu đó, rồi mở tab <strong>Sửa phụ đề</strong> trong SrtGen, sửa đúng câu "
        "đó và bấm <strong>Lưu</strong>. Đừng xuất ngược từ Aegisub ra "
        "<span class='mono'>.srt</span>: bản xuất ra không còn đúng quy chuẩn bốn dòng, và "
        "bản tiếng Việt sẽ lệch khỏi bản tiếng Trung.",
        "Đừng dùng chức năng tự động dọn khoảng trắng của phần mềm khác: quy chuẩn này yêu cầu "
        "đúng một khoảng trắng ở hai bên dấu “-” đổi người nói, các công cụ khác hay xoá mất.",
    ]
    if ass_exported:
        steps.insert(
            1,
            "Hoặc mở thẳng file <span class='mono'>.ass</span> đi kèm — bản này đã cài sẵn font "
            "cho chữ Hán và pinyin, mở ra là xem được ngay.",
        )
    if bilingual_exported:
        steps.insert(
            0,
            "<strong>Để soát cả hai ngôn ngữ cùng lúc, hãy mở file "
            "<span class='mono'>_song-ngu.ass</span></strong>. Mỗi dòng phụ đề trong đó có "
            "ba dòng chữ: Hán, pinyin, tiếng Việt — nhìn một lần là đối chiếu được. Hai file "
            "<span class='mono'>.srt</span> và <span class='mono'>_vi.srt</span> mới là file "
            "giao đi, đừng gửi file song ngữ cho khách.",
        )
    body = "<ol>" + "".join(f"<li>{s}</li>" for s in steps) + "</ol>"
    body += (
        '<div class="note">Nếu Aegisub báo hai dòng phụ đề chồng lấn thời gian, hãy quay lại '
        "mục “Kết quả kiểm định dạng” — lỗi đó đã được liệt kê kèm số dòng cụ thể.</div>"
    )
    return _Section("aegisub", "Cách mở file trong Aegisub", body)


# --------------------------------------------------------------------------- #
# tiện ích nhỏ
# --------------------------------------------------------------------------- #

def _h(value: Any) -> str:
    """Thoát HTML. Mọi chuỗi đi vào báo cáo đều qua đây — nội dung phụ đề là dữ
    liệu người khác viết, không phải mã do tool viết."""
    return html.escape(str(value), quote=True)


def _shorten(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fmt_number(value: Any, *, decimals: int = 2) -> str:
    """Số thập phân viết theo kiểu Việt Nam (dấu phẩy), số nguyên thì bỏ phần lẻ."""
    number = _as_float(value)
    if number is None:
        return "—"
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.{decimals}f}".replace(".", ",")


def _fmt_duration(seconds: float | None) -> str:
    """Thời lượng bằng câu tiếng Việt, không dùng dạng 00:03:12 để đỡ phải giải thích."""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "—"
    if seconds < 1:
        return "dưới 1 giây"
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} giờ {minutes} phút" if minutes else f"{hours} giờ"
    if minutes:
        return f"{minutes} phút {secs} giây" if secs else f"{minutes} phút"
    return f"{secs} giây"


def _format_when(value: Any) -> str:
    """ISO 8601 → `dd/mm/yyyy HH:MM`; không đọc được thì trả nguyên văn."""
    text = str(value or "").strip()
    if not text:
        return "—"
    try:
        return datetime.fromisoformat(text).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return text


def _newline(emit_cfg: dict[str, Any]) -> str:
    """`cfg["emit"]["newline"]` là chữ ("lf"/"crlf"), không phải ký tự thật."""
    return {"lf": "\n", "crlf": "\r\n"}.get(str(emit_cfg.get("newline", "lf")).strip().lower(), "\n")


def _flag(cfg: dict[str, Any], key: str, default: bool) -> bool:
    value = cfg.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _file_rows(
    srt_path: Path,
    vi_path: Path | None,
    bundle_path: Path | None,
    report_path: Path | None,
    ass_path: Path | None,
    bilingual_path: Path | None,
    out_dir: Path,
) -> list[tuple[str, str]]:
    """Bảng "file đã tạo" trong báo cáo.

    Báo cáo tự liệt kê cả chính nó: người nhận file qua email cần biết các file
    kia tên gì và nằm cạnh nhau ở đâu, nếu không họ chỉ có mỗi trang HTML này.
    """
    rows: list[tuple[str, str]] = [("Phụ đề tiếng Trung (file giao đi)", str(srt_path))]
    if vi_path:
        rows.append(("Phụ đề tiếng Việt (file giao đi)", str(vi_path)))
    if bilingual_path:
        rows.append(("Bản song ngữ để soát trong Aegisub", str(bilingual_path)))
    if bundle_path:
        rows.append(("Dữ liệu cho phần mềm hiển thị", str(bundle_path)))
    if ass_path:
        rows.append(("Bản cho Aegisub (một ngôn ngữ)", str(ass_path)))
    if report_path:
        rows.append(("Báo cáo này", str(report_path)))
    rows.append(("Thư mục chứa tất cả", str(out_dir)))
    return rows


def _done_message(result: dict[str, Any], out_dir: Path) -> str:
    made = sum(
        1
        for key in ("srt", "vi_srt", "bundle", "report", "ass", "bilingual_ass")
        if result.get(key)
    )
    kept = len(result.get("backups") or [])
    note = (
        f" Bản cũ của {kept} file (khác bản mới) đã được cất lại cùng thư mục, "
        "tên có chữ “.truoc-”."
        if kept
        else ""
    )
    if result.get("has_errors"):
        return (
            f"Đã ghi {made} file vào thư mục {out_dir}, nhưng còn lỗi định dạng cần sửa — "
            f"xem báo cáo để biết ở dòng nào.{note}"
        )
    return f"Xong. Đã ghi {made} file vào thư mục {out_dir}.{note}"


def _notify(callback: Callable[[str, float], None] | None, message: str, fraction: float) -> None:
    """Báo tiến trình mà không để callback của giao diện làm hỏng chặng xuất file.

    Ở đây file đã (hoặc sắp) nằm trên đĩa; một cái `on_progress` viết lỗi của web
    UI không có quyền huỷ kết quả 30 phút chạy máy.
    """
    if callback is None:
        return
    try:
        callback(message, max(0.0, min(1.0, float(fraction))))
    except Exception:  # pragma: no cover - callback do phía gọi cung cấp
        pass
