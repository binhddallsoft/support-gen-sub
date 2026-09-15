"""S8 — dịch phụ đề sang tiếng Việt, **không được xê dịch một cue nào**.

Vì sao chặng này có hình dạng như vậy (build-spec-v2 mục 2 và 3):

* Hai file giao cho người dùng — ``<title>.srt`` và ``<title>_vi.srt`` — phải có
  **cùng số block và cùng mốc thời gian đến từng ký tự**.  Đó là ràng buộc cấu
  trúc, không phải thứ đi kiểm rồi sửa sau.  Nên ở đây không có chỗ nào cho phép
  gộp, tách hay bỏ cue: chặng này chỉ sinh ra **một danh sách chuỗi dài đúng bằng
  ``len(doc.cues)``**.  Cue thứ i lấy phần tử thứ i, hết.
* Cùng triết lý với token list ở S5: mô hình được hỏi theo **lô có đánh chỉ số**,
  và ``responseSchema`` ép đúng N phần tử.  Thiếu chỉ số nào thì hỏi lại đúng
  những chỉ số đó; hỏi lại vẫn không có thì cue đó **giữ nguyên văn bản gốc** và
  được ghi vào ``S8_translate.json`` để người soát biết chỗ nào cần dịch tay.
  Tuyệt đối không dồn dòng — dồn một dòng là lệch cả phim từ chỗ đó trở đi.
* Chất lượng dịch nằm ở **ngữ cảnh**, không ở model: mỗi lô gửi kèm 5 cue trước
  (kèm bản dịch vừa xong, để giữ xưng hô) và 5 cue sau, cộng bảng thuật ngữ lấy
  từ ``names.json``.  Bảng thuật ngữ là thứ giữ tên nhân vật viết giống nhau từ
  phút 1 đến phút 40 — việc mà bản dịch từng dòng không bao giờ làm được.
* ``temperature = 0`` và cache theo hash ``(zh, target, model, glossary, phiên
  bản prompt)`` dùng chung mọi video: chạy lại cùng một tập phim phải ra đúng
  cùng một file, còn sửa chỉ dẫn văn phong thì phải thấy được kết quả ngay cả
  trên máy đã có cache ấm.

Chặng này **không ghi file phụ đề**; nó chỉ gắn kết quả vào ``doc.meta`` và lưu
``S8_translate.json``.  S9 là chỗ duy nhất token biến thành byte trên đĩa.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Final, Iterable, Mapping, Sequence

from srtgen.core.context import Context
from srtgen.core.token import Document, render_zh

__all__ = [
    "run",
    "translate_document",
    "TranslateResult",
    "build_glossary",
    "glossary_warning",
    "attach_translation",
    "vi_lines_of",
    "translation_info",
    "PROMPT_VERSION",
    "STAGE_NUM",
    "STAGE_NAME",
    "NAMES_CHANGED_MESSAGE",
]

STAGE_NUM = 8
STAGE_NAME = "translate"

#: Câu báo khi riêng chặng dịch phải làm lại vì bảng tên riêng đã đổi. Khi S5
#: cũng thấy bảng đổi thì S5 đã báo câu đầy đủ và ép chặng này chạy lại từ đầu,
#: nên câu này chỉ hiện khi chỉ có bản dịch là cũ.
NAMES_CHANGED_MESSAGE: Final[str] = "Bảng tên riêng đã đổi, làm lại bước dịch"

#: Khoá trong ``doc.meta`` mang danh sách dòng tiếng Việt.  Danh sách này **luôn**
#: dài đúng bằng ``len(doc.cues)``; S9 kiểm lại điều đó trước khi ghi file và bỏ
#: qua toàn bộ bản dịch nếu độ dài lệch — thà không có file tiếng Việt còn hơn có
#: một file lệch dòng.
META_LINES = "vi_lines"

#: Khoá trong ``doc.meta`` mang phần mô tả (nhà cung cấp, số cue giữ nguyên…).
META_INFO = "translation"

#: Nguồn của một dòng dịch, ghi trong ``S8_translate.json`` để đọc báo cáo hiểu
#: được vì sao một dòng trông như chưa dịch.
SRC_MODEL = "ai"          # vừa hỏi mô hình
SRC_CACHE = "cache"       # dùng lại kết quả đã lưu
SRC_ORIGINAL = "original" # giữ nguyên văn tiếng Trung

#: Các loại lỗi mà thử tiếp cũng vô ích: sai mã API, hết hạn mức, thiếu thư viện,
#: cấu hình sai.  Gặp một trong số đó thì dừng gọi mạng cho toàn bộ phần còn lại
#: thay vì để người dùng ngồi xem 40 lô cùng hỏng vì cùng một lý do.
_FATAL_KINDS = frozenset({"auth", "quota", "missing_dependency", "config"})

#: Trần số cue gửi kèm làm ngữ cảnh, kể cả khi cấu hình đặt lớn hơn.  Ngữ cảnh
#: dài không làm bản dịch tốt thêm nhưng làm mỗi request đắt lên trông thấy.
_MAX_CONTEXT = 10

#: Trần kích thước lô.  Đặc tả nói 20–30; quá 40 thì câu trả lời hay bị cắt ở
#: ``maxOutputTokens`` và cả lô phải hỏi lại — đắt gấp đôi.
_MIN_BATCH = 1
_MAX_BATCH = 40

#: Phiên bản của bộ chỉ dẫn văn phong trong prompt (``srtgen/providers/translate.py``).
#: Nó nằm trong khoá cache vì cache **dùng chung cho mọi video** và sống ở
#: ``user_cache_dir()``: sửa prompt cho bản dịch tự nhiên hơn mà không đổi số này
#: thì người đã chạy vài tập phim sẽ không bao giờ thấy phần cải tiến — họ nhận
#: lại đúng những câu dịch cũ và kết luận rằng bản cập nhật chẳng thay đổi gì.
#: Tăng số này mỗi khi sửa ``_PROMPT``; cache cũ không bị xoá, chỉ ngừng được
#: dùng, nên hạ số lại là lấy lại được.
PROMPT_VERSION: Final[int] = 1


# --------------------------------------------------------------------------- #
# kiểu dữ liệu
# --------------------------------------------------------------------------- #

@dataclass
class TranslateResult:
    """Kết quả dịch của cả tài liệu.

    ``lines`` là thứ duy nhất S9 cần, và nó là một **danh sách theo vị trí** chứ
    không phải map theo ``cue.index``: chỉ số cue trong file nguồn có thể trùng
    hoặc nhảy cóc (file người khác gửi sang), còn vị trí trong ``doc.cues`` thì
    không bao giờ.
    """

    lines: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    kept: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    retried_batches: int = 0
    stopped_early: bool = False

    def translated(self) -> int:
        return sum(1 for s in self.sources if s in (SRC_MODEL, SRC_CACHE))

    def kept_count(self) -> int:
        """Số cue có lời nhưng dòng tiếng Việt vẫn là văn bản gốc.

        Đếm từ ``sources`` chứ không từ ``kept``: khi gặp lỗi nặng (sai mã API,
        hết hạn mức) tool dừng gọi mạng và hàng trăm cue còn lại giữ nguyên văn
        mà không có mục nào trong ``kept`` — liệt kê từng cue một trong file JSON
        chỉ tạo ra rác, còn con số tổng thì người dùng phải thấy đúng.
        """
        return sum(
            1
            for pos, src in enumerate(self.sources)
            if src == SRC_ORIGINAL and pos < len(self.lines) and self.lines[pos].strip()
        )

    def counts(self) -> dict[str, int]:
        return {
            "cues": len(self.lines),
            "translated": self.translated(),
            "kept_original": self.kept_count(),
            "listed_kept": len(self.kept),
            "from_cache": sum(1 for s in self.sources if s == SRC_CACHE),
            "requests": self.requests,
            "retried_batches": self.retried_batches,
        }


# --------------------------------------------------------------------------- #
# bảng thuật ngữ
# --------------------------------------------------------------------------- #

def build_glossary(
    names_path: Path | str | None,
    *,
    limit: int = 120,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Đọc ``names.json`` và dựng bảng ``{tên chữ Hán: tên tiếng Việt cố định}``.

    Bảng này **chỉ chứa những tên đã có bản tiếng Việt do người điền** — trường
    ``vi`` của một mục trong ``names.json``, viết bởi người dùng qua giao diện
    hoặc bằng tay.  Đúng như build-spec-v2 mục 3: *"tên nhân vật/địa danh có bản
    tiếng Việt cố định, ép model dùng đúng"*.

    Vì sao KHÔNG còn tự chế bản dịch từ pinyin
    ------------------------------------------
    Bản cũ, khi một tên chưa có ``vi``, tự sinh pinyin đã bỏ dấu thanh
    (``光头强`` → ``Guangtouqiang``) rồi nộp cho mô hình dưới một dòng lệnh nói
    nguyên văn: *"these names have a fixed Vietnamese spelling. Use it exactly,
    every single time, and never invent a different one"*.  Nghĩa là phần có đòn
    bẩy lớn nhất của prompt đang **ép mô hình viết sai**: file thật của người
    dùng gọi nhân vật đó là "Cường đầu trọc", "Gấu Ú", "sếp Lý" — không ai viết
    "Guangtouqiang" trong một câu tiếng Việt.  Một bảng sai được tuân thủ tuyệt
    đối còn tệ hơn không có bảng, vì nó sai giống hệt nhau ở cả 1351 dòng và
    người soát phải sửa từng dòng một.

    Đổi lại, những tên chưa có bản tiếng Việt không còn được "giữ nhất quán" hộ
    nữa.  Đó là một mất mát thật, nên nó không bị giấu đi: ``info["missing"]``
    liệt kê đúng những tên ấy và :func:`run` nói thành lời cho người dùng biết
    cần điền vào đâu.  Điền một lần là mọi lần chạy sau đều đúng.
    """
    info: dict[str, Any] = {
        "file": str(names_path or ""),
        "size": 0,
        "manual": 0,
        "missing": [],
        "missing_count": 0,
    }
    if not names_path:
        return {}, info
    path = Path(names_path)
    if not path.is_file():
        return {}, info

    from srtgen.stages.s7_ai import load_names

    flat, meta = load_names(path)
    table: dict[str, str] = {}
    missing: list[str] = []
    # Cụm của một tên nhiều cụm (光头强 = 光头 | 强). S7 và tab Tên riêng ghi âm
    # đọc của TỪNG CỤM vào bảng phẳng, nhưng đó không phải tên riêng thứ hai:
    # tab Tên riêng cố ý không hiện chúng thành dòng (`_api_entries` của
    # server.py). Đếm chúng là "thiếu tên tiếng Việt" thì câu nhắc bảo người dùng
    # đi điền hai tên họ không nhìn thấy đâu, cho một nhân vật đã có tên Việt.
    clusters: set[str] = set()

    for entry in _iter_entries(meta):
        han = str(entry.get("han") or "").strip()
        if not han:
            continue
        split = [str(s).strip() for s in (entry.get("split") or []) if str(s).strip()]
        if len(split) > 1:
            clusters.update(split)
        explicit = _explicit_vietnamese(entry)
        if explicit:
            table[han] = explicit
        elif han not in missing:
            missing.append(han)

    # ``flat`` là dạng tối thiểu mà mọi file names.json đều có (``{Hán: pinyin}``).
    # Nó không mang bản tiếng Việt nên không đóng góp dòng nào cho bảng; nhưng nó
    # cho biết còn tên nào chưa ai đặt tên tiếng Việt, và đó là thứ người dùng
    # cần được nhắc.
    for han in (flat or {}):
        key = str(han).strip()
        if key and key not in table and key not in missing and key not in clusters:
            missing.append(key)

    trimmed = dict(sorted(table.items(), key=lambda kv: (-len(kv[0]), kv[0]))[:limit])
    info["size"] = len(trimmed)
    info["manual"] = len(trimmed)
    info["missing"] = sorted(missing, key=lambda han: (-len(han), han))[:limit]
    info["missing_count"] = len(missing)
    return trimmed, info


def glossary_warning(info: Mapping[str, Any]) -> str:
    """Câu nhắc tiếng Việt khi còn tên riêng chưa có bản tiếng Việt, hoặc ``""``.

    Viết cho người không phải dân IT: nói **hậu quả** trước ("mỗi lúc gọi một
    kiểu"), rồi nói **làm gì** ở đâu.  Không có câu này thì thứ duy nhất người
    dùng thấy là một tập phim mà nhân vật đổi tên giữa chừng, và họ sẽ đổ cho
    bản dịch chứ không biết là mình còn một ô chưa điền.
    """
    count = int(info.get("missing_count") or 0)
    if count <= 0:
        return ""
    names = [str(h) for h in (info.get("missing") or [])][:5]
    shown = "、".join(names)
    tail = f" (ví dụ: {shown})" if shown else ""
    return (
        f"Còn {count} tên riêng chưa có tên tiếng Việt{tail}. "
        "Bản dịch có thể gọi mỗi lúc một kiểu. Vào tab “Tên riêng”, điền tên "
        "tiếng Việt cho từng nhân vật rồi dịch lại là xong."
    )


def _iter_entries(meta: Mapping[str, Any] | None) -> Iterable[Mapping[str, Any]]:
    rows = (meta or {}).get("entries")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _explicit_vietnamese(entry: Mapping[str, Any]) -> str:
    """Bản tiếng Việt người dùng tự điền, nếu có.

    Đọc mọi cách viết khoá mà ``s7_ai`` chấp nhận, không tự chế danh sách thứ
    hai: hai danh sách sẽ lệch nhau vào đúng ngày ai đó thêm một khoá.
    """
    from srtgen.stages.s7_ai import VI_KEYS

    for key in VI_KEYS:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# --------------------------------------------------------------------------- #
# dịch cả tài liệu
# --------------------------------------------------------------------------- #

def translate_document(
    doc: Document,
    translator: Any,
    *,
    target: str = "vi",
    glossary: Mapping[str, str] | None = None,
    title: str = "",
    batch_size: int = 25,
    context_window: int = 5,
    retries: int = 2,
    cache: Any | None = None,
    model: str = "",
    on_progress: Callable[[str, float], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    base: float = 0.0,
    span: float = 1.0,
) -> TranslateResult:
    """Dịch từng cue của ``doc`` và trả về danh sách dòng dài đúng bằng số cue.

    Hàm này cố ý **không** đụng tới ``ctx``, tới file hay tới cấu hình: lệnh
    ``srtgen translate <file.srt>`` dùng lại đúng nó cho một file rời không thuộc
    lần chạy nào, và bộ kiểm thử gọi nó với ``NullTranslator`` mà không cần dựng
    thư mục làm việc.
    """
    from srtgen.providers.translate import glossary_hash

    zh_lines = [render_zh(cue.tokens) for cue in doc.cues]
    result = TranslateResult(
        lines=list(zh_lines),
        sources=[SRC_ORIGINAL] * len(zh_lines),
    )
    if not zh_lines:
        return result

    table = dict(glossary or {})
    digest = glossary_hash(table)
    size = max(_MIN_BATCH, min(int(batch_size or 25), _MAX_BATCH))
    window = max(0, min(int(context_window or 0), _MAX_CONTEXT))
    attempts = max(1, int(retries or 0) + 1)

    todo = [i for i, text in enumerate(zh_lines) if text.strip()]
    _from_cache(todo, zh_lines, result, cache=cache, target=target, model=model, digest=digest)

    pending = [i for i in todo if result.sources[i] == SRC_ORIGINAL]
    batches = [pending[n : n + size] for n in range(0, len(pending), size)]
    total = len(batches)
    if not total:
        _say(on_progress, "Đã có sẵn bản dịch cho mọi câu, không phải hỏi lại.", base + span)
        return result

    for number, batch in enumerate(batches, start=1):
        if is_cancelled is not None and is_cancelled():
            result.stopped_early = True
            break
        _say(
            on_progress,
            f"Đang dịch sang tiếng Việt ({number}/{total} lô, {len(batch)} câu)…",
            base + span * (number - 1) / total,
        )
        stop = _run_batch(
            batch,
            zh_lines=zh_lines,
            result=result,
            translator=translator,
            target=target,
            table=table,
            title=title,
            window=window,
            attempts=attempts,
            cache=cache,
            model=model,
            digest=digest,
        )
        if stop:
            result.stopped_early = True
            break

    _flush(cache)
    _say(on_progress, _summary(result), base + span)
    return result


def _run_batch(
    batch: Sequence[int],
    *,
    zh_lines: Sequence[str],
    result: TranslateResult,
    translator: Any,
    target: str,
    table: Mapping[str, str],
    title: str,
    window: int,
    attempts: int,
    cache: Any,
    model: str,
    digest: str,
) -> bool:
    """Dịch một lô, hỏi lại phần thiếu, và trả về ``True`` nếu phải dừng hẳn.

    Vì sao lần hỏi lại chỉ gửi **những chỉ số còn thiếu** chứ không gửi lại cả
    lô: mô hình vừa trả lời đúng cho phần lớn lô, hỏi lại toàn bộ vừa tốn tiền
    vừa cho nó cơ hội đổi ý ở những dòng đã tốt.  Hỏi lại đúng phần hỏng cũng
    chính là "thử lại lô đó" theo nghĩa có ích.

    Một câu trả lời **hỏng khuôn dạng** — JSON vỡ, mảng ``lines`` không có, hay
    bị cắt ngang ở ``maxOutputTokens`` — cũng là "lô đó hỏng" và cũng phải hỏi
    lại.  Bỏ cuộc ngay lần đầu ở đây biến một lô 25 câu thành 25 dòng chữ Hán
    nằm giữa file tiếng Việt, trong khi chính nhà cung cấp vừa bảo người dùng
    "hãy thử lại".  Lỗi nặng (sai mã API, hết hạn mức) thì ngược lại: hỏi lại
    chỉ tốn thêm thời gian chờ, nên dừng ngay.
    """
    from srtgen.providers.base import ERR_BAD_RESPONSE, ProviderError
    from srtgen.providers.translate import clean_line

    missing = list(batch)
    for attempt in range(attempts):
        if not missing:
            break
        if attempt:
            result.retried_batches += 1
        items = [{"id": i, "zh": zh_lines[i]} for i in missing]
        context = _context_for(batch, zh_lines=zh_lines, result=result, window=window)
        context["glossary"] = table
        context["title"] = title

        # Đếm trước khi gọi: một lượt gọi hỏng vẫn là một lượt đã gọi, và báo cáo
        # nói "0 lượt gọi" cho một lần chạy vừa đốt hết hạn mức là nói dối.
        result.requests += 1
        try:
            answer = translator.translate_batch(items, context, target=target)
        except ProviderError as err:
            result.errors.append(err.to_dict())
            kind = str(getattr(err, "kind", ""))
            if kind == ERR_BAD_RESPONSE and attempt + 1 < attempts:
                continue
            _keep(result, zh_lines, missing, err.user_message)
            return kind in _FATAL_KINDS
        except Exception as err:  # nhà cung cấp ngoài luồng — không được làm chết cả phim
            message = (
                "Có lỗi khi gọi dịch vụ dịch. Những câu chưa dịch được vẫn giữ "
                "nguyên văn tiếng Trung, file không bị lệch dòng."
            )
            result.errors.append(
                {
                    "kind": "unknown",
                    "message": f"{type(err).__name__}: {err}",
                    "user_message": message,
                }
            )
            _keep(result, zh_lines, missing, message)
            return True

        asked = set(missing)
        got: dict[int, str] = {}
        if isinstance(answer, Mapping):
            for raw_key, raw_text in answer.items():
                try:
                    key = int(raw_key)
                except (TypeError, ValueError):
                    continue
                # Chỉ số không nằm trong lô vừa hỏi bị bỏ, không đoán: mô hình
                # vừa tự đánh số lại là mô hình đang nói rằng câu trả lời của nó
                # không tin được, và tin nó là cách làm lệch cả phim.
                text = clean_line(raw_text)
                if key in asked and text:
                    got[key] = text

        for key, text in got.items():
            result.lines[key] = text
            result.sources[key] = SRC_MODEL
            _cache_put(cache, zh_lines[key], text, target=target, model=model, digest=digest)
        missing = [i for i in missing if i not in got]

    _keep(
        result,
        zh_lines,
        missing,
        "Dịch vụ dịch không trả về dòng này sau nhiều lần hỏi, nên tool giữ "
        "nguyên văn tiếng Trung để hai file không lệch dòng.",
    )
    return False


def _keep(
    result: TranslateResult,
    zh_lines: Sequence[str],
    positions: Iterable[int],
    reason: str,
) -> None:
    """Ghi nhận những cue phải giữ nguyên văn, kèm lý do người dùng đọc được.

    Danh sách này chính là danh sách việc phải làm tay của người soát, nên lý do
    phải là câu tiếng Việt cụ thể ("mã API sai") chứ không phải một mã lỗi.
    """
    for pos in positions:
        result.kept.append(
            {"position": pos, "zh": zh_lines[pos], "reason": reason}
        )


def _context_for(
    batch: Sequence[int],
    *,
    zh_lines: Sequence[str],
    result: TranslateResult,
    window: int,
) -> dict[str, Any]:
    """5 cue trước và 5 cue sau, chỉ để đọc.

    Cue phía trước gửi kèm cả bản dịch vừa xong (``Hán => Việt``) — đó là cách
    duy nhất để lô sau biết lô trước đã chọn cách xưng hô nào.
    """
    if not batch or window <= 0:
        return {"before": [], "after": []}
    first, last = min(batch), max(batch)

    before: list[str] = []
    for i in range(max(0, first - window), first):
        text = zh_lines[i]
        if not text.strip():
            continue
        if result.sources[i] in (SRC_MODEL, SRC_CACHE):
            before.append(f"{text}  =>  {result.lines[i]}")
        else:
            before.append(text)

    after = [
        zh_lines[i]
        for i in range(last + 1, min(len(zh_lines), last + 1 + window))
        if zh_lines[i].strip()
    ]
    return {"before": before, "after": after}


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #

def _cache_key(zh: str, *, target: str, digest: str) -> dict[str, Any]:
    """Payload của khoá cache — đúng những thứ quyết định một bản dịch.

    ``model`` không nằm ở đây vì ``ProposalCache.make_key`` đã trộn nó vào rồi;
    lặp lại sẽ khiến cùng một câu có hai khoá khác nhau tuỳ nơi gọi.

    ``prompt`` là :data:`PROMPT_VERSION` — xem chú thích ở hằng số đó: bản dịch
    là hàm của cả câu chỉ dẫn văn phong, nên câu chỉ dẫn phải nằm trong khoá.
    """
    return {
        "zh": zh,
        "target": str(target),
        "glossary": digest,
        "prompt": PROMPT_VERSION,
    }


def _from_cache(
    todo: Sequence[int],
    zh_lines: Sequence[str],
    result: TranslateResult,
    *,
    cache: Any,
    target: str,
    model: str,
    digest: str,
) -> None:
    """Lấy trước mọi câu đã có trong cache, **trước khi** chia lô.

    Chia lô trước rồi mới tra cache sẽ tạo ra những lô chỉ còn một câu chưa
    biết mà vẫn phải gửi đủ 25 câu — đúng cái làm cache trở nên vô dụng.
    """
    if cache is None:
        return
    for i in todo:
        try:
            key = cache.make_key(STAGE_NAME, model, _cache_key(zh_lines[i], target=target, digest=digest))
            hit = cache.get(STAGE_NAME, model, key)
        except Exception:  # pragma: no cover - cache hỏng là cache trượt, không phải lỗi
            return
        if isinstance(hit, str) and hit.strip():
            result.lines[i] = hit
            result.sources[i] = SRC_CACHE
    result.cache_hits = int(getattr(cache, "hits", 0) or 0)
    result.cache_misses = int(getattr(cache, "misses", 0) or 0)


def _cache_put(
    cache: Any,
    zh: str,
    text: str,
    *,
    target: str,
    model: str,
    digest: str,
) -> None:
    if cache is None:
        return
    try:
        key = cache.make_key(STAGE_NAME, model, _cache_key(zh, target=target, digest=digest))
        cache.put(STAGE_NAME, model, key, text)
    except Exception:  # pragma: no cover - xem _from_cache
        pass


def _flush(cache: Any) -> None:
    if cache is None:
        return
    try:
        cache.flush()
    except Exception:  # pragma: no cover - mất cache thì chỉ tốn thêm vài request
        pass


# --------------------------------------------------------------------------- #
# gắn kết quả vào tài liệu
# --------------------------------------------------------------------------- #

def attach_translation(doc: Document, result: TranslateResult, info: Mapping[str, Any]) -> None:
    """Đặt bản dịch vào ``doc.meta`` để S9 và trình sửa dùng chung một nguồn."""
    doc.meta[META_LINES] = list(result.lines)
    doc.meta[META_INFO] = dict(info)


def vi_lines_of(doc: Document) -> list[str]:
    """Danh sách dòng tiếng Việt của tài liệu, hoặc ``[]`` nếu không dùng được.

    Trả về rỗng khi độ dài lệch số cue: một danh sách lệch còn nguy hiểm hơn
    không có danh sách nào, vì nó sinh ra một file ``_vi.srt`` trông bình thường
    mà nội dung lệch dòng — lỗi khó phát hiện nhất trong nghề làm phụ đề.
    """
    raw = doc.meta.get(META_LINES)
    if not isinstance(raw, list) or len(raw) != len(doc.cues):
        return []
    return [str(item or "") for item in raw]


def translation_info(doc: Document) -> dict[str, Any]:
    info = doc.meta.get(META_INFO)
    return dict(info) if isinstance(info, Mapping) else {}


# --------------------------------------------------------------------------- #
# chặng
# --------------------------------------------------------------------------- #

def run(
    ctx: Context,
    on_progress: Callable[[str, float], None] | None = None,
    *,
    translator: Any | None = None,
) -> dict[str, Any]:
    """Dịch tài liệu của S7 sang tiếng Việt và lưu ``S8_translate.json``.

    ``translator`` là tham số cho bộ kiểm thử tiêm sẵn một ``NullTranslator``;
    khi chạy thật nó là ``None`` và nhà cung cấp được chọn theo cấu hình ở đúng
    một chỗ duy nhất (:func:`srtgen.providers.get_translator`).
    """
    started = time.perf_counter()
    _say(on_progress, "Đang chuẩn bị dịch sang tiếng Việt…", 0.02)

    table = _names_path(ctx)
    # Lấy dấu vân tay TRƯỚC khi đọc bảng: người dùng lưu bảng tên giữa chừng thì
    # dấu ghi lại là của bản cũ, và lần sau dịch lại thêm một lần — hướng an toàn.
    fingerprint = _names_fingerprint(table)
    cached = _resume(ctx, names_fingerprint=fingerprint, on_progress=on_progress)
    if cached is not None:
        _say(on_progress, "Đã có sẵn bản dịch của lần chạy trước, dùng lại.", 1.0)
        return cached

    from srtgen.providers import get_translator, translate_config

    doc = _load_document(ctx)
    cfg = ctx.cfg or {}
    settings = translate_config(cfg)
    target = str(settings.get("target") or "vi").strip() or "vi"

    if translator is None:
        translator = get_translator(cfg)
    name = str(getattr(translator, "name", "null"))
    reason = str(getattr(translator, "reason", "") or "")
    warning = str(getattr(translator, "warning", "") or "")
    # Chỉ nhà cung cấp nào THẬT SỰ chạy trên một model mới có tên model. Bản cũ
    # rơi xuống ``settings["model"]`` khi thuộc tính rỗng, nên bản dịch miễn phí
    # của Google — thứ không dùng model ngôn ngữ nào cả — vẫn được báo cáo là
    # "gemini-2.5-flash" và ngăn cache của nó cũng mang tên đó. Người dùng đọc
    # S8_translate.json sẽ tin là mình vừa gọi Gemini, còn cache thì lẫn hai
    # nguồn chất lượng khác hẳn nhau vào chung một chỗ.
    model = str(getattr(translator, "model", "") or "")

    glossary, glossary_info = build_glossary(table)
    cache = _open_cache(settings, name)
    cache_key_model = f"{name}.{model}" if model else name

    if warning:
        _say(on_progress, warning, 0.05)
    glossary_note = glossary_warning(glossary_info)
    if glossary_note:
        _say(on_progress, glossary_note, 0.05)

    result = translate_document(
        doc,
        translator,
        target=target,
        glossary=glossary,
        title=str(ctx.meta.get("title") or doc.meta.get("title") or ""),
        batch_size=int(_number(settings.get("batch_size"), 25)),
        context_window=int(_number(settings.get("context_window"), 5)),
        retries=int(_number(settings.get("retries"), 2)),
        cache=cache,
        model=cache_key_model,
        on_progress=on_progress,
        is_cancelled=ctx.is_cancelled,
        base=0.06,
        span=0.9,
    )

    info: dict[str, Any] = {
        "target": target,
        "provider": name,
        "provider_reason": reason,
        "warning": warning,
        "glossary_warning": glossary_note,
        "model": model,
        "counts": result.counts(),
    }
    attach_translation(doc, result, info)
    ctx.doc = doc

    payload = _build_payload(
        ctx=ctx,
        doc=doc,
        result=result,
        info=info,
        glossary_info=glossary_info,
        settings=settings,
        elapsed=time.perf_counter() - started,
    )
    from srtgen.stages.s5_tokenize import NAMES_HASH_KEY, NO_NAMES_HASH

    # Bảng không đọc được thì ghi như chưa có bảng — y hệt S5 — để sửa xong bảng
    # là lần chạy sau nhận ra ngay.
    payload[NAMES_HASH_KEY] = fingerprint or NO_NAMES_HASH
    try:
        ctx.save_stage(STAGE_NUM, STAGE_NAME, payload)
    except (OSError, TypeError, ValueError):
        # Không lưu được điểm dừng thì cũng đừng vứt bỏ bản dịch vừa làm xong;
        # S9 vẫn lấy được từ ``doc.meta`` trong cùng lần chạy này.
        pass

    _say(on_progress, _summary(result), 1.0)
    return payload


def _build_payload(
    *,
    ctx: Context,
    doc: Document,
    result: TranslateResult,
    info: Mapping[str, Any],
    glossary_info: Mapping[str, Any],
    settings: Mapping[str, Any],
    elapsed: float,
) -> dict[str, Any]:
    """Nội dung ``S8_translate.json``.

    Mang theo cả tài liệu như S7 làm: một chặng phải đọc thêm file của chặng khác
    là một chặng sẽ hỏng lần đầu tiên ai đó dọn thư mục làm việc.
    """
    # Tài liệu lưu kèm bị gỡ hai khoá bản dịch: chúng đã nằm ở `lines` ngay bên
    # dưới. Lưu hai lần vừa làm file to gấp đôi, vừa mở đường cho một bản dịch cũ
    # lẻn ngược vào qua `meta` của tài liệu ở lần chạy sau — kể cả lần chạy mà
    # người dùng đã tắt hẳn phần dịch.
    document = doc.to_stage_dict()
    doc_meta = dict(document.get("meta") or {})
    doc_meta.pop(META_LINES, None)
    doc_meta.pop(META_INFO, None)
    document["meta"] = doc_meta

    lines: list[dict[str, Any]] = []
    blocks: dict[int, int] = {}
    number = 0
    for pos, cue in enumerate(doc.cues):
        zh = render_zh(cue.tokens)
        # Số block **thật sự nằm trong file** — S9 bỏ qua cue không có chữ nào và
        # đánh số lại theo vị trí. Người dùng mở `_vi.srt` ra tìm theo con số đó,
        # nên báo cáo phải nói đúng con số đó chứ không phải chỉ số nội bộ.
        if zh or cue.py_text():
            number += 1
            blocks[pos] = number
        lines.append(
            {
                "position": pos,
                "index": cue.index,
                "block": blocks.get(pos),
                "zh": zh,
                "vi": result.lines[pos] if pos < len(result.lines) else "",
                "source": result.sources[pos] if pos < len(result.sources) else SRC_ORIGINAL,
            }
        )

    kept: list[dict[str, Any]] = []
    for row in result.kept:
        pos = row.get("position")
        enriched = dict(row)
        if isinstance(pos, int) and 0 <= pos < len(doc.cues):
            enriched["index"] = doc.cues[pos].index
            enriched["block"] = blocks.get(pos)
        kept.append(enriched)

    return {
        "stage": "s8_translate",
        "video_id": ctx.video_id,
        "target": info.get("target"),
        "provider": info.get("provider"),
        "provider_reason": info.get("provider_reason"),
        "warning": info.get("warning"),
        "glossary_warning": info.get("glossary_warning"),
        "model": info.get("model"),
        "enabled": bool(settings.get("enabled", True)),
        "batch_size": int(_number(settings.get("batch_size"), 25)),
        "context_window": int(_number(settings.get("context_window"), 5)),
        "counts": result.counts(),
        "cache": {"hit": result.cache_hits, "miss": result.cache_misses},
        "glossary": dict(glossary_info),
        "kept_original": kept,
        "errors": list(result.errors),
        "stopped_early": result.stopped_early,
        "elapsed": round(float(elapsed), 3),
        "lines": lines,
        "document": document,
    }


def _resume(
    ctx: Context,
    *,
    names_fingerprint: str | None = None,
    on_progress: Callable[[str, float], None] | None = None,
) -> dict[str, Any] | None:
    """Dùng lại kết quả cũ khi chạy tiếp, và gắn lại bản dịch vào tài liệu.

    Bản dịch cũ chỉ được dùng lại khi nó dịch với đúng bảng tên riêng đang nằm
    trên đĩa (``names_fingerprint``). Tên tiếng Việt chỉ nhập được SAU khi phim
    đã chạy một lần, nên không so thì tên người dùng vừa gõ không bao giờ vào
    được file ``_vi.srt``. Bảng đổi thì làm lại chặng này và, qua ``force_from``,
    cả chặng xuất file sau nó — xem ``s5_tokenize.redo_from_stage``. ``None``
    nghĩa là không đọc được bảng: giữ bản dịch cũ, không dịch lại bằng bảng hỏng.
    """
    if not ctx.has_stage(STAGE_NUM, STAGE_NAME):
        return None
    payload = ctx.load_stage(STAGE_NUM, STAGE_NAME)
    if not isinstance(payload, Mapping) or not isinstance(payload.get("lines"), list):
        return None
    if _names_changed(ctx, payload, names_fingerprint, on_progress):
        return None

    doc = _document_from_payload(payload.get("document"))
    if doc is None:
        doc = ctx.doc if isinstance(ctx.doc, Document) else None
    if doc is None or not doc.cues:
        return None

    lines = [str(row.get("vi") or "") for row in payload["lines"] if isinstance(row, Mapping)]
    if len(lines) != len(doc.cues):
        return None

    doc.meta[META_LINES] = lines
    doc.meta[META_INFO] = {
        "target": payload.get("target"),
        "provider": payload.get("provider"),
        "provider_reason": payload.get("provider_reason"),
        "warning": payload.get("warning"),
        "glossary_warning": payload.get("glossary_warning"),
        "model": payload.get("model"),
        "counts": dict(payload.get("counts") or {}),
    }
    ctx.doc = doc
    out = dict(payload)
    out["resumed"] = True
    return out


def _load_document(ctx: Context) -> Document:
    """Tài liệu từ ``ctx.doc``, nếu chưa có thì đọc ngược S7 → S6 → S5."""
    if isinstance(ctx.doc, Document) and ctx.doc.cues:
        return ctx.doc
    for number, name in ((7, "ai"), (6, "norm"), (5, "tokens")):
        doc = _document_from_payload(ctx.load_stage(number, name))
        if doc is not None and doc.cues:
            ctx.doc = doc
            return doc
    raise RuntimeError(
        "Chưa có dòng phụ đề nào để dịch. Hãy chạy lại từ bước “Tách cụm và sinh pinyin”, "
        "hoặc kiểm tra lại file âm thanh nguồn nếu máy không nghe được câu thoại nào."
    )


def _document_from_payload(payload: Any) -> Document | None:
    """Dựng ``Document`` từ nội dung một file chặng, chấp nhận vài kiểu bọc."""
    if isinstance(payload, Document):
        return payload
    if not isinstance(payload, Mapping):
        return None
    if "cues" in payload:
        try:
            return Document.from_stage_dict(dict(payload))
        except (TypeError, ValueError, AttributeError):
            return None
    for key in ("document", "doc", "result", "data"):
        inner = payload.get(key)
        if isinstance(inner, Mapping):
            built = _document_from_payload(inner)
            if built is not None:
                return built
    return None


def _names_path(ctx: Context) -> Path:
    """Đường dẫn ``names.json`` — đúng luật S7 dùng, không chép lại luật thứ hai."""
    from srtgen.stages.s7_ai import _names_path as names_path

    return names_path(ctx)


def _names_fingerprint(path: Path) -> str | None:
    """Dấu vân tay của ``names.json`` — dùng đúng hàm của S5.

    Hai chặng dùng chung một hàm để không bao giờ có chuyện S5 thấy bảng đã đổi
    còn S8 thì không (hoặc ngược lại) chỉ vì hai cách chuẩn hoá lệch nhau.
    """
    from srtgen.stages.s5_tokenize import names_hash

    return names_hash(path)


def _names_changed(
    ctx: Context,
    payload: Mapping[str, Any],
    fingerprint: str | None,
    on_progress: Callable[[str, float], None] | None,
) -> bool:
    """Bảng tên riêng đã đổi kể từ lần dịch trước — và đã xếp lịch làm lại chưa.

    ``False`` khi bảng không đổi, khi không đọc được bảng (``None``: không dịch
    lại bằng một bảng hỏng), hoặc khi việc làm lại bị từ chối vì không cất được
    bản phụ đề người dùng đã sửa tay — khi đó bản dịch cũ được giữ nguyên.
    """
    from srtgen.stages.s5_tokenize import redo_from_stage, stored_names_hash

    if fingerprint is None or stored_names_hash(payload) == fingerprint:
        return False
    return redo_from_stage(ctx, STAGE_NUM, on_progress, NAMES_CHANGED_MESSAGE)


def _open_cache(settings: Mapping[str, Any], provider: str) -> Any | None:
    """Cache dùng chung mọi video, hoặc ``None`` khi không nên cache.

    Dùng lại ``ProposalCache`` của S7 thay vì viết một lớp cache thứ hai: nó đã
    xử lý đúng những chuyện khó (file hỏng là cache trượt chứ không phải lỗi,
    hai cửa sổ srtgen cùng ghi thì gộp chứ không đè).

    ``NullTranslator`` **không bao giờ** được cache. Kết quả của nó là chính câu
    tiếng Trung; để nó lọt vào cache thì lần chạy thật sau đó sẽ "dùng lại kết
    quả cũ" và nhận về nguyên văn tiếng Trung — một cách hỏng vừa im lặng vừa
    khó lần ra, vì cache dùng chung cho mọi video.
    """
    if not _truthy(settings.get("cache", True)) or provider == "null":
        return None
    from srtgen.core.context import user_cache_dir
    from srtgen.stages.s7_ai import ProposalCache

    return ProposalCache(user_cache_dir() / "translate", enabled=True)


# --------------------------------------------------------------------------- #
# tiện ích nhỏ
# --------------------------------------------------------------------------- #

def _summary(result: TranslateResult) -> str:
    """Câu kết của chặng, viết cho người không rành máy tính."""
    counts = result.counts()
    if not counts["cues"]:
        return "Không có câu nào để dịch."
    if not counts["translated"]:
        return (
            "Chưa dịch được câu nào nên dòng tiếng Việt tạm giữ nguyên văn tiếng Trung. "
            "File vẫn đủ số dòng và đúng mốc thời gian."
        )
    text = f"Đã dịch {counts['translated']}/{counts['cues']} câu"
    if counts["from_cache"]:
        text += f" ({counts['from_cache']} câu dùng lại kết quả đã lưu)"
    if counts["kept_original"]:
        text += f", {counts['kept_original']} câu giữ nguyên văn vì chưa dịch được"
    return text + "."


def _say(callback: Callable[[str, float], None] | None, message: str, fraction: float) -> None:
    """Báo tiến trình mà không để callback của giao diện làm chết chặng."""
    if callback is None:
        return
    try:
        callback(message, max(0.0, min(1.0, float(fraction))))
    except Exception:  # pragma: no cover - callback do phía gọi cung cấp
        pass


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "bật"}
    return bool(value)


def _number(value: Any, default: float) -> float:
    try:
        if value is None or isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
