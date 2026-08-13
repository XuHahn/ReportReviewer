"""Render one auditable source page for the review evidence viewer."""

from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import threading
import time
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import fitz

from services.document_unitizer import (
    _display_zip_filename,
    _is_ignored_zip_metadata,
    _office_to_pdf,
    _validate_zip,
)
from services.evidence_anchor_finalizer import (
    _parameter_ordinal_row,
    _semantic_quote_row,
    _strict_ordered_quote,
    _structured_metadata_row,
)


_SUPPORTED_DOCUMENT_SUFFIXES = (".pdf", ".docx", ".xlsx", ".xls")
PREVIEW_RENDERER_VERSION = "evidence-preview-11-strict-ordered-anchors"

_HIGHLIGHT_PALETTE = {
    "error": {"stroke": (0.78, 0.17, 0.14), "fill": (0.96, 0.28, 0.22)},
    "warning": {"stroke": (0.78, 0.48, 0.05), "fill": (1.0, 0.72, 0.16)},
    "success": {"stroke": (0.02, 0.45, 0.36), "fill": (0.10, 0.67, 0.52)},
    "reference": {"stroke": (0.08, 0.36, 0.62), "fill": (0.18, 0.55, 0.84)},
}

_EVIDENCE_ROLE_LABELS = {
    "report_only": "报告额外",
    "raw_only": "记录额外",
    "matched_report": "已匹配",
    "matched_raw": "已匹配",
    "raw_inventory": "已匹配",
    "expected": "要求依据",
    "observed": "实际内容",
    "declared": "声明内容",
    "declared_count": "计划数量",
    "observed_sample_id": "样品编号",
    "supporting_reference": "对照依据",
    "corroborating": "佐证内容",
    "conflicting_report_result": "结果冲突",
    "missing_field": "字段未填写",
}

_HIGHLIGHT_KIND_LABELS = {
    "error": "差异内容",
    "warning": "待确认",
    "success": "已匹配",
    "reference": "要求依据",
}


def _highlight_summary_label(spec: dict[str, Any]) -> str:
    """Return a compact business label instead of a positional box number."""
    explicit = " ".join(str(spec.get("label") or "").split())
    if explicit:
        return explicit[:8]
    metadata = spec.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    role = str(metadata.get("role") or "").strip().lower()
    if role in _EVIDENCE_ROLE_LABELS:
        return _EVIDENCE_ROLE_LABELS[role]
    verdict = str(metadata.get("verdict") or metadata.get("state") or "").strip().lower()
    if verdict:
        if any(token in verdict for token in ("fail", "error", "invalid", "conflict", "不通过", "不符合")):
            return "不通过"
        if any(token in verdict for token in ("pass", "match", "valid", "通过", "符合")):
            return "通过"
    return _HIGHLIGHT_KIND_LABELS.get(str(spec.get("kind") or "error"), "证据")


def _highlight_label_rectangle(
    page: fitz.Page,
    anchor: fitz.Rect,
    width: float,
    height: float,
    occupied: list[fitz.Rect],
    other_highlights: list[fitz.Rect] | None = None,
) -> fitz.Rect:
    """Choose the least obstructive nearby position for a summary label."""
    words = [fitz.Rect(*word[:4]) for word in page.get_text("words")]
    max_x = max(page.rect.x0, page.rect.x1 - width)
    x_candidates: list[tuple[float, float]] = []
    if anchor.x1 + 2.0 + width <= page.rect.x1:
        x_candidates.append((anchor.x1 + 2.0, 0.0))
    if anchor.x0 - 2.0 - width >= page.rect.x0:
        x_candidates.append((anchor.x0 - 2.0 - width, 0.03))
    x_candidates.extend([
        (max(page.rect.x0, min(max_x, anchor.x0)), 0.18),
        (max(page.rect.x0, min(max_x, anchor.x0 + (anchor.width - width) * 0.5)), 0.22),
        (max(page.rect.x0, min(max_x, anchor.x1 - width)), 0.24),
    ])
    y_candidates = [
        (anchor.y0 + max(0.0, (anchor.height - height) * 0.5), 0.0),
        (anchor.y0 - height, 0.05),
        (anchor.y0 + 0.8, 0.15),
        (anchor.y1 - height - 0.8, 0.2),
    ]
    ranked: list[tuple[float, float, float, fitz.Rect]] = []
    for y, placement_penalty in y_candidates:
        if y < page.rect.y0 or y + height > page.rect.y1:
            continue
        for x, horizontal_penalty in x_candidates:
            candidate = fitz.Rect(x, y, x + width, y + height)
            word_overlap = sum(
                candidate.intersect(word).get_area()
                for word in words if candidate.intersects(word)
            )
            tag_overlap = sum(
                candidate.intersect(tag).get_area()
                for tag in occupied if candidate.intersects(tag)
            )
            other_highlight_overlap = sum(
                candidate.intersect(highlight).get_area()
                for highlight in other_highlights or []
                if candidate.intersects(highlight)
            )
            ranked.append((
                word_overlap + tag_overlap * 8 + other_highlight_overlap * 4,
                placement_penalty + horizontal_penalty, x, candidate,
            ))
    if ranked:
        return min(ranked, key=lambda item: item[:3])[3]
    return fitz.Rect(anchor.x0, anchor.y0, min(anchor.x0 + width, page.rect.x1), anchor.y0 + height)


@dataclass(frozen=True)
class EvidencePagePreview:
    content: bytes
    page_count: int
    highlight_count: int
    highlight_strategy: str
    focus_applied: bool = False
    anchor_y_positions: tuple[float | None, ...] = ()
    resolved_bboxes: tuple[tuple[float, float, float, float] | None, ...] = ()
    resolved_rectangles: tuple[
        tuple[tuple[float, float, float, float], ...], ...
    ] = ()
    locator_strategies: tuple[str, ...] = ()
    cache_hit: bool = False
    rendered_pdf_hash: str = ""
    page_width: float = 0.0
    page_height: float = 0.0


def extract_archive_member(file_bytes: bytes, member_index: int) -> tuple[str, bytes]:
    """Read one supported ZIP member using the review unitizer's stable order."""
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        _validate_zip(infos)
        supported = [
            info for info in infos
            if not _is_ignored_zip_metadata(info)
            and info.filename.lower().endswith(_SUPPORTED_DOCUMENT_SUFFIXES)
        ]
        if member_index < 1 or member_index > len(supported):
            raise IndexError(member_index)
        info = supported[member_index - 1]
        return _display_zip_filename(info), archive.read(info)


def _cache_root() -> Path:
    configured = os.getenv("DOCUMENT_PREVIEW_CACHE_DIR", "").strip()
    root = Path(configured) if configured else Path(tempfile.gettempdir()) / "report-reviewer-preview-cache"
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    return root


def evidence_preview_cache_key(payload: dict[str, Any]) -> str:
    serialized = json.dumps(
        {"renderer_version": PREVIEW_RENDERER_VERSION, **payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()


def _preview_cache_paths(cache_key: str) -> tuple[Path, Path]:
    if len(cache_key) != 64 or any(character not in "0123456789abcdef" for character in cache_key):
        raise ValueError("预览缓存键无效")
    root = _cache_root()
    return root / f"render-{cache_key}.png", root / f"render-{cache_key}.json"


def load_evidence_preview_cache(cache_key: str) -> EvidencePagePreview | None:
    png_path, metadata_path = _preview_cache_paths(cache_key)
    if not png_path.is_file() or not metadata_path.is_file():
        return None
    ttl = max(60, int(os.getenv("DOCUMENT_PREVIEW_CACHE_TTL_SECONDS", "604800")))
    try:
        if time.time() - min(png_path.stat().st_mtime, metadata_path.stat().st_mtime) > ttl:
            png_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            return None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("renderer_version") != PREVIEW_RENDERER_VERSION:
            return None
        return EvidencePagePreview(
            content=png_path.read_bytes(),
            page_count=int(metadata["page_count"]),
            highlight_count=int(metadata["highlight_count"]),
            highlight_strategy=str(metadata["highlight_strategy"]),
            focus_applied=bool(metadata.get("focus_applied")),
            anchor_y_positions=tuple(metadata.get("anchor_y_positions") or ()),
            resolved_bboxes=tuple(
                tuple(value) if value is not None else None
                for value in metadata.get("resolved_bboxes") or ()
            ),
            resolved_rectangles=tuple(
                tuple(tuple(rectangle) for rectangle in (value or ()))
                for value in metadata.get("resolved_rectangles") or ()
            ),
            locator_strategies=tuple(metadata.get("locator_strategies") or ()),
            cache_hit=True,
            rendered_pdf_hash=str(metadata.get("rendered_pdf_hash") or ""),
            page_width=float(metadata.get("page_width") or 0),
            page_height=float(metadata.get("page_height") or 0),
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def _prune_evidence_preview_cache(root: Path) -> None:
    """Bound only generated render artifacts; Office source PDFs are separate."""
    marker = root / ".render-prune"
    now = time.time()
    try:
        if marker.exists() and now - marker.stat().st_mtime < 300:
            return
        marker.touch(exist_ok=True)
        marker.chmod(0o600)
    except OSError:
        return
    ttl = max(60, int(os.getenv("DOCUMENT_PREVIEW_CACHE_TTL_SECONDS", "604800")))
    max_bytes = max(10 * 1024 * 1024, int(os.getenv(
        "DOCUMENT_PREVIEW_CACHE_MAX_BYTES", str(2 * 1024 * 1024 * 1024),
    )))
    files = [path for path in root.glob("render-*.*") if path.suffix in {".png", ".json"}]
    stats: list[tuple[float, int, Path]] = []
    for path in files:
        try:
            stat = path.stat()
            if now - stat.st_mtime > ttl:
                path.unlink(missing_ok=True)
                continue
            stats.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            continue
    total = sum(size for _, size, _ in stats)
    for _, size, path in sorted(stats):
        if total <= max_bytes:
            break
        try:
            path.unlink(missing_ok=True)
            total -= size
        except OSError:
            continue


def store_evidence_preview_cache(cache_key: str, preview: EvidencePagePreview) -> None:
    png_path, metadata_path = _preview_cache_paths(cache_key)
    metadata = {
        "renderer_version": PREVIEW_RENDERER_VERSION,
        "page_count": preview.page_count,
        "highlight_count": preview.highlight_count,
        "highlight_strategy": preview.highlight_strategy,
        "focus_applied": preview.focus_applied,
        "anchor_y_positions": preview.anchor_y_positions,
        "resolved_bboxes": preview.resolved_bboxes,
        "resolved_rectangles": preview.resolved_rectangles,
        "locator_strategies": preview.locator_strategies,
        "rendered_pdf_hash": preview.rendered_pdf_hash,
        "page_width": preview.page_width,
        "page_height": preview.page_height,
    }
    token = f"{os.getpid()}-{uuid.uuid4().hex}"
    png_tmp = png_path.with_name(f"{png_path.name}.{token}.tmp")
    metadata_tmp = metadata_path.with_name(f"{metadata_path.name}.{token}.tmp")
    try:
        png_tmp.write_bytes(preview.content)
        metadata_tmp.write_text(
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        png_tmp.chmod(0o600)
        metadata_tmp.chmod(0o600)
        os.replace(png_tmp, png_path)
        os.replace(metadata_tmp, metadata_path)
    finally:
        png_tmp.unlink(missing_ok=True)
        metadata_tmp.unlink(missing_ok=True)
    _prune_evidence_preview_cache(png_path.parent)


def _source_pdf(file_bytes: bytes, filename: str) -> bytes:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        if not file_bytes.startswith(b"%PDF-"):
            raise ValueError("PDF 文件内容无效")
        return file_bytes
    if suffix not in {".docx", ".xlsx", ".xls"}:
        raise ValueError("当前文件类型不支持视觉预览")

    digest = hashlib.sha256(file_bytes).hexdigest()
    cache_path = _cache_root() / f"office-{digest}.pdf"
    if cache_path.is_file():
        return cache_path.read_bytes()
    pdf_bytes = _office_to_pdf(file_bytes, filename)
    temporary = cache_path.with_name(
        f"{cache_path.name}.{os.getpid()}-{uuid.uuid4().hex}.tmp"
    )
    temporary.write_bytes(pdf_bytes)
    os.replace(temporary, cache_path)
    return pdf_bytes


def _search_candidates(quote: str) -> list[str]:
    compact = " ".join(str(quote or "").split()).strip()
    # A short quote is an auditable anchor.  Picking an arbitrary line from a
    # long extraction is not: boilerplate fields are often longer and easier
    # to find than the value that actually participated in the decision.
    return [compact] if 2 <= len(compact) <= 240 else []


def _normalized_search_text(value: Any) -> str:
    """Normalize PDF text-layer spacing without weakening the text anchor."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _normalized_quote_rectangles(page: fitz.Page, quote: str) -> list[fitz.Rect]:
    """Locate a quote whose PDF text layer split or joined visual tokens.

    Standards commonly render symbols such as ``UPP`` while extraction stores
    ``U PP``. PyMuPDF's exact search cannot bridge that difference. Matching a
    normalized full quote against ordered page words keeps the anchor strict,
    then maps the matched characters back to their visual line rectangles.
    """
    needle = _normalized_search_text(quote)
    if len(needle) < 8:
        return []
    # Prefer logical text lines. Standards frequently use subscript glyphs;
    # sorting individual words by coordinates can move those glyphs ahead of
    # the surrounding sentence even though line extraction is correct.
    lines: list[tuple[fitz.Rect, int, int]] = []
    line_text_parts: list[str] = []
    cursor = 0
    for block in page.get_text("dict", sort=True).get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            fragment = _normalized_search_text("".join(
                str(span.get("text") or "") for span in spans
            ))
            if not fragment:
                continue
            # Some embedded standard fonts expose a glyph bbox nearly six
            # times the visible font height. Drawing that bbox overlaps the
            # previous/next requirement. Rebuild the vertical bounds from the
            # text baseline and font size while retaining exact horizontal
            # span coordinates.
            visible_spans = [
                span for span in spans
                if span.get("bbox") and span.get("origin") and float(span.get("size") or 0) > 0
            ]
            if visible_spans:
                rectangle = fitz.Rect(
                    min(float(span["bbox"][0]) for span in visible_spans),
                    min(float(span["origin"][1]) - float(span["size"]) * 1.10 for span in visible_spans),
                    max(float(span["bbox"][2]) for span in visible_spans),
                    max(float(span["origin"][1]) + float(span["size"]) * 0.30 for span in visible_spans),
                )
            else:
                rectangle = fitz.Rect(line["bbox"])
            start = cursor
            cursor += len(fragment)
            line_text_parts.append(fragment)
            lines.append((rectangle, start, cursor))
    line_match_start = "".join(line_text_parts).find(needle)
    if line_match_start >= 0:
        line_match_end = line_match_start + len(needle)
        return [
            rectangle for rectangle, start, end in lines
            if start < line_match_end and end > line_match_start
        ][:8]

    # Fall back to word geometry for documents whose line segmentation is
    # broken but whose reading-order word stream remains usable.
    words: list[tuple[tuple[Any, ...], str, int, int]] = []
    page_text_parts: list[str] = []
    cursor = 0
    for word in page.get_text("words", sort=True):
        fragment = _normalized_search_text(word[4])
        if not fragment:
            continue
        start = cursor
        cursor += len(fragment)
        page_text_parts.append(fragment)
        words.append((word, fragment, start, cursor))
    match_start = "".join(page_text_parts).find(needle)
    if match_start < 0:
        return []
    match_end = match_start + len(needle)
    selected = [
        word for word, _, start, end in words
        if start < match_end and end > match_start
    ]
    if not selected:
        return []
    line_rectangles: dict[tuple[int, int], fitz.Rect] = {}
    for word in selected:
        line_key = (int(word[5]), int(word[6]))
        rectangle = fitz.Rect(word[:4])
        if line_key in line_rectangles:
            line_rectangles[line_key].include_rect(rectangle)
        else:
            line_rectangles[line_key] = rectangle
    return list(line_rectangles.values())[:8]


_COMPARISON_FIELD_ORDER = (
    "test_item",
    "injection_point",
    "spec_requirement",
    "test_duration",
    "required_level",
    "actual_level",
    "verdict_raw",
)
_COMPARISON_FIELD_WEIGHTS = {
    "test_item": 1.0,
    "injection_point": 1.0,
    "spec_requirement": 1.5,
    "test_duration": 1.0,
    "required_level": 3.0,
    "actual_level": 2.0,
    "verdict_raw": 3.0,
}
_INSTRUMENT_FIELD_ORDER = (
    "manufacturer", "model", "serial_no", "calibration_end",
)
_INSTRUMENT_FIELD_WEIGHTS = {
    "manufacturer": 1.0,
    "model": 2.0,
    "serial_no": 3.0,
    "calibration_end": 2.0,
}
_STRUCTURED_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "client_name": ("委托单位", "客户名称", "申请人", "client", "applicant"),
    "client_address": ("委托单位地址", "客户地址", "地址", "address"),
    "sample_name": ("样品名称", "样品名", "产品名称", "产品名", "sample name", "eut name"),
    "sample_model": ("型号规格", "样品型号", "主测型号", "型号", "model"),
    "part_number": ("零部件号", "部件号", "料号", "part number"),
    "rated_voltage": ("额定电压", "供电电压", "工作电压", "rated voltage"),
    "sample_id": ("样品编号", "样品号", "sample id", "sample no"),
    "test_plan_number": ("试验计划编号", "测试计划编号", "计划编号", "plan no"),
    "receive_date": (
        "接收日期", "收样日期", "receive date", "receive sample date",
        "sample receive date",
    ),
    "test_date": ("试验日期", "测试日期", "test date"),
    "test_date_range": ("试验日期", "测试日期", "test date"),
    "issue_date": ("签发日期", "发布日期", "issue date", "issued date", "report date"),
}


def _search_value(page: fitz.Page, value: Any) -> list[fitz.Rect]:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return []
    variants = [text]
    # Office-to-PDF conversion can split a Chinese negation and the English
    # verdict across two lines. PyMuPDF can trace that phrase when separated
    # by whitespace, while the extracted structured value is usually compact.
    if text.startswith("不") and len(text) > 1 and not text[1].isspace():
        variants.insert(0, f"不 {text[1:]}")
    for variant in variants:
        rectangles = list(page.search_for(variant))
        if rectangles:
            return rectangles
    return []


def _atomic_token_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        character for character in normalized
        if character.isalnum() or character in ".,+-±%Ωω"
    )


def _whole_word_value_rectangles(page: fitz.Page, value: Any) -> list[fitz.Rect]:
    """Reject suffix matches such as locating ``4V`` inside ``14V``."""
    needle = _atomic_token_text(value)
    if not needle or len(needle) > 24:
        return []
    return [
        fitz.Rect(*word[:4])
        for word in page.get_text("words", sort=True)
        if _atomic_token_text(word[4]) == needle
    ]


def _ordered_row_score(
    hits: list[tuple[str, float, list[fitz.Rect]]],
    center_y: float,
    tolerance: float,
) -> tuple[float, list[fitz.Rect]]:
    """Score one visual row while respecting the known table column order."""
    states: list[tuple[float, float, list[fitz.Rect]]] = [(0.0, -1.0, [])]
    for field_name, weight, rectangles in hits:
        nearby = [
            rectangle for rectangle in rectangles
            if abs((rectangle.y0 + rectangle.y1) / 2 - center_y) <= tolerance
        ]
        next_states = list(states)
        for score, last_right, selected in states:
            for rectangle in nearby:
                if rectangle.x0 + 1.0 < last_right:
                    continue
                next_states.append((
                    score + weight,
                    rectangle.x1,
                    [*selected, rectangle],
                ))
        # The number of fields is fixed and small, but repeated single-letter
        # values can create many candidates. Keep only the strongest states.
        next_states.sort(key=lambda state: (state[0], state[1]), reverse=True)
        states = next_states[:160]
    return max(states, key=lambda state: state[0])[0::2]


def _structured_row_rectangles(
    page: fitz.Page,
    metadata: dict[str, Any],
) -> list[fitz.Rect]:
    rows = metadata.get("comparison_rows")
    if not isinstance(rows, list):
        return []
    highlighted: list[fitz.Rect] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        field_order = (
            _INSTRUMENT_FIELD_ORDER if row.get("serial_no")
            else _COMPARISON_FIELD_ORDER
        )
        field_weights = (
            _INSTRUMENT_FIELD_WEIGHTS if row.get("serial_no")
            else _COMPARISON_FIELD_WEIGHTS
        )
        hits: list[tuple[str, float, list[fitz.Rect]]] = []
        for field_name in field_order:
            rectangles = _search_value(page, row.get(field_name))
            if rectangles:
                hits.append((field_name, field_weights[field_name], rectangles))
        if len(hits) < 3:
            continue
        candidate_centers = sorted({
            round((rectangle.y0 + rectangle.y1) / 2, 1)
            for _, _, rectangles in hits
            for rectangle in rectangles
        })
        # Converted Word tables use roughly 12pt line boxes. Fourteen points
        # keeps wrapped cells in one row without leaking into the header above.
        tolerance = 14.0
        best_score = 0.0
        best_rectangles: list[fitz.Rect] = []
        for center_y in candidate_centers:
            score, rectangles = _ordered_row_score(hits, center_y, tolerance)
            if score > best_score:
                best_score = score
                best_rectangles = rectangles
        # At least three ordered fields must agree on the same visual row.
        # Otherwise a box would imply precision the evidence does not support.
        if best_score < 4.0 or len(best_rectangles) < 3:
            continue
        row_rectangle = fitz.Rect(best_rectangles[0])
        for rectangle in best_rectangles[1:]:
            row_rectangle.include_rect(rectangle)
        row_rectangle.x0 = max(page.rect.x0, row_rectangle.x0 - 3.0)
        row_rectangle.x1 = min(page.rect.x1, row_rectangle.x1 + 3.0)
        row_rectangle.y0 = max(page.rect.y0, row_rectangle.y0 - 6.0)
        row_rectangle.y1 = min(page.rect.y1, row_rectangle.y1 + 6.0)
        highlighted.append(row_rectangle)
    return highlighted[:8]


def _structured_field_rectangle(
    page: fitz.Page,
    quote: str,
    metadata: dict[str, Any],
) -> list[fitz.Rect]:
    """Disambiguate a repeated short value by its reviewed field label."""
    field_name = str(metadata.get("field_name") or "")
    labels = _STRUCTURED_FIELD_LABELS.get(field_name, ())
    values = list(page.search_for(" ".join(str(quote or "").split()).strip()))
    if not labels or not values:
        return []
    label_rectangles = [
        rectangle
        for label in labels
        for rectangle in page.search_for(label)
    ]
    if not label_rectangles:
        return []
    ranked: list[tuple[float, fitz.Rect]] = []
    for value in values:
        value_center = (value.y0 + value.y1) / 2
        best = min(
            (
                abs(value_center - (label.y0 + label.y1) / 2)
                + (25.0 if value.x1 < label.x0 else 0.0)
            )
            for label in label_rectangles
        )
        ranked.append((best, value))
    ranked.sort(key=lambda item: item[0])
    tolerance = max(18.0, ranked[0][1].height * 1.5)
    if ranked[0][0] > tolerance:
        return []
    # Equal candidates would still be ambiguous; do not select an arbitrary row.
    if len(ranked) > 1 and abs(ranked[1][0] - ranked[0][0]) < 1.0:
        return []
    return [ranked[0][1]]


def _structured_missing_field_rectangle(
    page: fitz.Page,
    metadata: dict[str, Any],
) -> list[fitz.Rect]:
    """Recover one negative-evidence region for historical graph records."""
    source_anchor = metadata.get("source_anchor")
    is_missing = (
        metadata.get("_extraction_method") == "deterministic_missing_field_region"
        or (
            isinstance(source_anchor, dict)
            and source_anchor.get("kind") == "missing_field"
        )
    )
    field_name = str(metadata.get("field_name") or "")
    labels = _STRUCTURED_FIELD_LABELS.get(field_name, ())
    if not is_missing or not labels:
        return []
    found: list[fitz.Rect] = []
    for label in labels:
        for rectangle in page.search_for(label):
            if not any(existing == rectangle for existing in found):
                found.append(rectangle)
    if len(found) != 1:
        return []
    label = found[0]
    center_y = (label.y0 + label.y1) / 2
    row_tolerance = max(8.0, label.height * 0.8)
    right_neighbors: list[tuple[fitz.Rect, str]] = []
    for block in page.get_text("dict", sort=True).get("blocks", []):
        for line in block.get("lines", []):
            bbox = line.get("bbox") or []
            if len(bbox) != 4:
                continue
            candidate = fitz.Rect(*bbox)
            line_text = "".join(
                str(span.get("text") or "") for span in line.get("spans", [])
            )
            if candidate.intersects(label):
                label_tokens = {
                    _normalized_search_text(value) for value in labels
                }
                if _normalized_search_text(line_text) not in label_tokens:
                    return []
            if candidate.x0 <= label.x1 + 2.0:
                continue
            if abs((candidate.y0 + candidate.y1) / 2 - center_y) <= row_tolerance:
                right_neighbors.append((candidate, line_text))
    occupied_neighbors = [
        (rectangle, text) for rectangle, text in right_neighbors
        if not _preview_unfilled_placeholder(text)
    ]
    placeholder_neighbors = [
        (rectangle, text) for rectangle, text in right_neighbors
        if _preview_unfilled_placeholder(text)
    ]
    if occupied_neighbors:
        return []
    x1 = page.rect.x1 - max(18.0, page.rect.width * 0.04)
    if placeholder_neighbors:
        x1 = min(x1, max(item[0].x1 for item in placeholder_neighbors) + 4.0)
    if x1 < label.x1 + 12.0:
        x1 = label.x1
    return [fitz.Rect(
        max(page.rect.x0, label.x0 - 2.0),
        max(page.rect.y0, label.y0 - 2.0),
        min(page.rect.x1, x1),
        min(page.rect.y1, label.y1 + 2.0),
    )]


def _preview_unfilled_placeholder(value: str) -> bool:
    compact = re.sub(r"[\s:：]", "", str(value or "")).casefold()
    if not compact:
        return True
    if re.fullmatch(r"(?:x{3,}|[_—–-]{2,}|\.{3,}|…{2,})", compact):
        return True
    return compact in {
        "tbd", "todo", "待定", "待填写", "待补充", "未填写",
        "请输入", "请填写", "report报告编号",
    }


def _structured_parameter_rectangle(
    page: fitz.Page,
    quote: str,
    metadata: dict[str, Any],
) -> list[fitz.Rect]:
    """Locate a parameter value without accepting a longer numeric token."""
    parameter_name = str(metadata.get("parameter_name") or "").strip()
    if not parameter_name:
        return []
    values = _whole_word_value_rectangles(page, quote)
    if len(values) == 1:
        return values
    labels = list(page.search_for(parameter_name))
    if not values or not labels:
        return []
    ranked = sorted(
        (
            min(abs((value.y0 + value.y1 - label.y0 - label.y1) / 2) for label in labels),
            value,
        )
        for value in values
    )
    if len(ranked) > 1 and abs(ranked[1][0] - ranked[0][0]) < 1.0:
        return []
    return [ranked[0][1]] if ranked[0][0] <= max(18.0, ranked[0][1].height * 1.5) else []


def _page_layout_lines(page: fitz.Page) -> list[dict]:
    lines: list[dict] = []
    for block in page.get_text("dict", sort=True).get("blocks", []):
        for line in block.get("lines", []):
            bbox = line.get("bbox") or []
            text = "".join(str(span.get("text") or "") for span in line.get("spans", []))
            if text.strip() and len(bbox) == 4:
                lines.append({"text": text, "bbox": list(map(float, bbox))})
    return lines


def _semantic_layout_rectangles(
    page: fitz.Page,
    quote: str,
    metadata: dict[str, Any],
) -> tuple[list[fitz.Rect], str]:
    """Backfill historical evidence using the same finalization rules as a run."""
    layout_lines = _page_layout_lines(page)
    _bbox, rectangles, method = _structured_metadata_row(layout_lines, metadata)
    if not rectangles:
        _bbox, rectangles, method = _semantic_quote_row(layout_lines, quote)
    if not rectangles:
        _bbox, rectangles, method = _parameter_ordinal_row(layout_lines, metadata)
    return [fitz.Rect(*rectangle) for rectangle in rectangles], method


def _valid_source_anchor_rectangles(
    page: fitz.Page,
    bbox: list[float],
    source_anchor: dict[str, Any],
    current_rendered_pdf_hash: str = "",
) -> list[fitz.Rect]:
    """Read versioned multi-rectangle anchors while keeping bbox compatibility."""
    expected_hash = str(source_anchor.get("rendered_pdf_hash") or "")
    hash_matches = not (
        expected_hash and current_rendered_pdf_hash
        and expected_hash != current_rendered_pdf_hash
    )
    candidates = source_anchor.get("rectangles")
    rectangles: list[fitz.Rect] = []
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, (list, tuple)) or len(candidate) != 4:
                return []
            rectangle = fitz.Rect(*map(float, candidate))
            if rectangle.is_empty or not page.rect.contains(rectangle):
                return []
            rectangles.append(rectangle)
    if rectangles:
        if hash_matches or _source_anchor_matches_page(page, rectangles, source_anchor):
            return rectangles
        return []
    if len(bbox) != 4:
        return []
    rectangle = fitz.Rect(*bbox)
    if rectangle.is_empty or not page.rect.contains(rectangle):
        return []
    rectangles = [rectangle]
    if hash_matches or _source_anchor_matches_page(page, rectangles, source_anchor):
        return rectangles
    return []


def _source_anchor_matches_page(
    page: fitz.Page,
    rectangles: list[fitz.Rect],
    source_anchor: dict[str, Any],
) -> bool:
    """Validate coordinates across non-deterministic DOCX-to-PDF binaries.

    Repeated conversion can change the PDF byte hash without changing page
    geometry.  Accept such coordinates only when page dimensions still match
    and the saved anchor text intersects the saved region.
    """
    expected_width = float(source_anchor.get("page_width") or 0)
    expected_height = float(source_anchor.get("page_height") or 0)
    if (
        expected_width <= 0 or expected_height <= 0
        or abs(page.rect.width - expected_width) > 0.75
        or abs(page.rect.height - expected_height) > 0.75
    ):
        return False
    anchor_quote = str(source_anchor.get("anchor_quote") or "").strip()
    if not anchor_quote:
        return False
    searched: list[fitz.Rect] = []
    for candidate in _search_candidates(anchor_quote):
        searched.extend(page.search_for(candidate))
    if not searched:
        searched = _normalized_quote_rectangles(page, anchor_quote)
    return bool(searched) and any(
        anchor.intersects(saved)
        for anchor in searched
        for saved in rectangles
    )


def _semantic_region_count(rectangles: list[fitz.Rect]) -> int:
    """Count spatially separate regions represented by text fragments.

    A wrapped sentence may have several line rectangles but still represents
    one evidence region. Distant header/footer hits or repeated occurrences
    are separate regions and must not be silently drawn as one proof.
    """
    if not rectangles:
        return 0
    remaining = [fitz.Rect(rectangle) for rectangle in rectangles]
    groups: list[list[fitz.Rect]] = []

    def connected(left: fitz.Rect, right: fitz.Rect) -> bool:
        vertical_overlap = max(
            0.0, min(left.y1, right.y1) - max(left.y0, right.y0),
        )
        min_height = max(1.0, min(left.height, right.height))
        if vertical_overlap / min_height >= 0.45:
            horizontal_gap = max(0.0, max(left.x0, right.x0) - min(left.x1, right.x1))
            return horizontal_gap <= max(18.0, min(left.height, right.height) * 1.5)
        upper, lower = (left, right) if left.y0 <= right.y0 else (right, left)
        vertical_gap = max(0.0, lower.y0 - upper.y1)
        return vertical_gap <= max(8.0, max(left.height, right.height) * 0.80)

    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for rectangle in list(remaining):
                if any(connected(rectangle, member) for member in group):
                    group.append(rectangle)
                    remaining.remove(rectangle)
                    changed = True
        groups.append(group)
    return len(groups)


def _accept_semantic_rectangles(
    rectangles: list[fitz.Rect],
    metadata: dict[str, Any],
) -> list[fitz.Rect]:
    source_anchor = metadata.get("source_anchor")
    cardinality = (
        str(source_anchor.get("cardinality") or "single_region")
        if isinstance(source_anchor, dict) else "single_region"
    )
    if cardinality == "multiple_regions":
        return rectangles[:8]
    return rectangles[:8] if _semantic_region_count(rectangles) == 1 else []


def _highlight_rectangles(
    page: fitz.Page,
    quote: str,
    bbox: list[float],
    metadata: dict[str, Any],
) -> tuple[list[fitz.Rect], str]:
    source_anchor = metadata.get("source_anchor")
    if isinstance(source_anchor, dict) and source_anchor.get("status") == "located":
        anchored = _valid_source_anchor_rectangles(
            page, bbox, source_anchor,
            str(metadata.get("_current_rendered_pdf_hash") or ""),
        )
        if anchored:
            return anchored, "source_anchor_bbox"
    missing_field = _structured_missing_field_rectangle(page, metadata)
    if missing_field:
        return missing_field, "structured_missing_field"
    structured = _structured_row_rectangles(page, metadata)
    if structured:
        return structured, "structured_comparison_row"
    structured_field = _structured_field_rectangle(page, quote, metadata)
    if structured_field:
        return structured_field, "structured_field_row"
    structured_parameter = _structured_parameter_rectangle(page, quote, metadata)
    if structured_parameter:
        return structured_parameter, "structured_parameter_value"
    semantic_layout, semantic_method = _semantic_layout_rectangles(
        page, quote, metadata,
    )
    if semantic_layout:
        return semantic_layout, semantic_method
    rectangles: list[fitz.Rect] = []
    quote_candidates = _search_candidates(quote)
    anchor_quote = str(source_anchor.get("anchor_quote") or "") if isinstance(source_anchor, dict) else ""
    if len(" ".join(str(quote or "").split())) < 3 and anchor_quote:
        quote_candidates = [*_search_candidates(anchor_quote), *quote_candidates]
    for candidate in dict.fromkeys(quote_candidates):
        rectangles = list(page.search_for(candidate))
        # A two-character value is useful only when it identifies one visual
        # occurrence. Multiple hits would create a persuasive but arbitrary box.
        if len(candidate) < 3 and len(rectangles) != 1:
            rectangles = []
        rectangles = _accept_semantic_rectangles(rectangles, metadata)
        if rectangles:
            break
    if rectangles:
        return rectangles[:8], "exact_quote"
    for candidate in dict.fromkeys(quote_candidates):
        rectangles = _normalized_quote_rectangles(page, candidate)
        if rectangles:
            rectangles = _accept_semantic_rectangles(rectangles, metadata)
        if rectangles:
            return rectangles, "normalized_quote"
    # A strict ordered-line match is the final text fallback.  Exact and
    # normalized quote strategies retain priority, and disconnected page
    # regions are rejected inside the shared locator.
    _strict_bbox, strict_rectangles, strict_method = _strict_ordered_quote(
        _page_layout_lines(page), quote,
    )
    if strict_rectangles:
        return [fitz.Rect(*rectangle) for rectangle in strict_rectangles], strict_method
    if len(bbox) == 4:
        direct = fitz.Rect(*bbox)
        if page.rect.contains(direct) and not direct.is_empty:
            rectangles = [direct]
        else:
            render_scale = float(os.getenv("UNITIZER_RENDER_SCALE", "2.0"))
            scaled = fitz.Rect(*(coordinate / render_scale for coordinate in bbox))
            if page.rect.contains(scaled) and not scaled.is_empty:
                rectangles = [scaled]
    return rectangles[:8], "bbox" if rectangles else "none"


def render_evidence_page(
    file_bytes: bytes,
    filename: str,
    page_number: int,
    *,
    quote: str = "",
    bbox: list[float] | None = None,
    metadata: dict[str, Any] | None = None,
    highlights: list[dict[str, Any]] | None = None,
    focus_index: int | None = None,
) -> EvidencePagePreview:
    """Render one source page with one or more semantically colored anchors.

    ``highlights`` is additive and keeps the original quote/bbox arguments
    backwards compatible. Each entry accepts quote, bbox, metadata and a
    constrained kind: error, warning, success or reference.
    """
    if page_number < 1:
        raise IndexError(page_number)
    pdf_bytes = _source_pdf(file_bytes, filename)
    rendered_pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()
    render_scale = float(os.getenv("UNITIZER_RENDER_SCALE", "2.0"))
    if not 1.0 <= render_scale <= 4.0:
        raise ValueError("UNITIZER_RENDER_SCALE 必须在 1.0 到 4.0 之间")
    with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
        if page_number > len(pdf):
            raise IndexError(page_number)
        page = pdf[page_number - 1]
        specs = highlights or [{
            "quote": quote, "bbox": list(bbox or []),
            "metadata": dict(metadata or {}), "kind": "error",
        }]
        strategies: list[str] = []
        locator_strategies: list[str] = ["none"] * min(len(specs), 12)
        highlight_count = 0
        rectangles_by_spec: dict[int, list[fitz.Rect]] = {}
        label_rectangles: list[fitz.Rect] = []
        for index, spec in enumerate(specs[:12], start=1):
            locator_metadata = dict(spec.get("metadata") or {})
            locator_metadata["_current_rendered_pdf_hash"] = rendered_pdf_hash
            rectangles, strategy = _highlight_rectangles(
                page,
                str(spec.get("quote") or ""),
                list(spec.get("bbox") or []),
                locator_metadata,
            )
            if not rectangles:
                continue
            kind = str(spec.get("kind") or "error")
            palette = _HIGHLIGHT_PALETTE.get(kind, _HIGHLIGHT_PALETTE["error"])
            strategies.append(strategy)
            locator_strategies[index - 1] = strategy
            highlight_count += len(rectangles)
            rectangles_by_spec[index - 1] = rectangles
            for rectangle in rectangles:
                page.draw_rect(
                    rectangle,
                    color=palette["stroke"],
                    fill=palette["fill"],
                    fill_opacity=0.16,
                    width=1.8,
                    overlay=True,
                )
            # Put a short business summary above the rectangle.  The badge is
            # translucent so it remains readable without masking source text.
            anchor = rectangles[0]
            label = _highlight_summary_label(spec)
            font_size = 6.5
            label_width = fitz.get_text_length(label, fontname="china-s", fontsize=font_size)
            tag_width = min(max(label_width + 8.0, 24.0), 62.0)
            tag_height = 10.5
            tag = _highlight_label_rectangle(
                page,
                anchor,
                tag_width,
                tag_height,
                label_rectangles,
                [
                    rectangle
                    for spec_rectangles in rectangles_by_spec.values()
                    for rectangle in spec_rectangles
                    if rectangle != anchor
                ],
            )
            label_rectangles.append(tag)
            page.draw_rect(
                tag,
                color=palette["stroke"],
                fill=palette["stroke"],
                fill_opacity=0.30,
                stroke_opacity=0.72,
                width=0.6,
                overlay=True,
            )
            page.insert_text(
                (tag.x0 + 4.0, tag.y1 - 2.4), label,
                fontname="china-s", fontsize=font_size,
                color=palette["stroke"], fill_opacity=0.96, overlay=True,
            )
        highlight_strategy = (
            strategies[0] if len(specs) == 1 and strategies
            else "multi:" + ",".join(strategies) if strategies
            else "none"
        )
        anchor_y_positions = tuple(
            (
                max(0.0, min(1.0, (
                    (min(rect.y0 for rect in rectangles_by_spec[index])
                     + max(rect.y1 for rect in rectangles_by_spec[index])) / 2
                    - page.rect.y0
                ) / page.rect.height))
                if rectangles_by_spec.get(index) else None
            )
            for index in range(min(len(specs), 12))
        )
        resolved_bboxes = tuple(
            (
                (
                    min(rect.x0 for rect in rectangles_by_spec[index]),
                    min(rect.y0 for rect in rectangles_by_spec[index]),
                    max(rect.x1 for rect in rectangles_by_spec[index]),
                    max(rect.y1 for rect in rectangles_by_spec[index]),
                )
                if rectangles_by_spec.get(index)
                and (
                    _semantic_region_count(rectangles_by_spec[index]) == 1
                    or locator_strategies[index] in {
                        "semantic_structured_row",
                        "semantic_parameter_ordinal_row",
                        "strict_ordered_quote",
                        "structured_metadata_row",
                    }
                )
                else None
            )
            for index in range(min(len(specs), 12))
        )
        resolved_rectangles = tuple(
            tuple(
                (rect.x0, rect.y0, rect.x1, rect.y1)
                for rect in rectangles_by_spec.get(index, [])
            )
            for index in range(min(len(specs), 12))
        )
        focus_rectangles = rectangles_by_spec.get(focus_index) if focus_index is not None else None
        clip: fitz.Rect | None = None
        if focus_rectangles:
            target = fitz.Rect(focus_rectangles[0])
            for rectangle in focus_rectangles[1:]:
                target.include_rect(rectangle)
            # Keep the complete horizontal document context, but reduce the
            # vertical viewport to the relevant table/paragraph neighborhood.
            # A minimum 38% page height avoids an over-tight crop that would
            # hide headers or adjacent comparison rows.
            clip_height = min(
                page.rect.height,
                max(target.height + 150.0, page.rect.height * 0.38),
            )
            center_y = (target.y0 + target.y1) / 2
            y0 = max(page.rect.y0, center_y - clip_height / 2)
            y1 = min(page.rect.y1, y0 + clip_height)
            y0 = max(page.rect.y0, y1 - clip_height)
            clip = fitz.Rect(page.rect.x0, y0, page.rect.x1, y1)
        pixmap = page.get_pixmap(
            matrix=fitz.Matrix(render_scale, render_scale), alpha=False,
            clip=clip,
        )
        return EvidencePagePreview(
            content=pixmap.tobytes("png"),
            page_count=len(pdf),
            highlight_count=highlight_count,
            highlight_strategy=highlight_strategy,
            focus_applied=clip is not None,
            anchor_y_positions=anchor_y_positions,
            resolved_bboxes=resolved_bboxes,
            resolved_rectangles=resolved_rectangles,
            locator_strategies=tuple(locator_strategies),
            rendered_pdf_hash=rendered_pdf_hash,
            page_width=float(page.rect.width),
            page_height=float(page.rect.height),
        )


@lru_cache(maxsize=4096)
def _preview_render_lock(cache_key: str) -> threading.Lock:
    return threading.Lock()


def render_evidence_page_cached(
    cache_key: str,
    file_bytes: bytes,
    filename: str,
    page_number: int,
    **kwargs: Any,
) -> EvidencePagePreview:
    """Deduplicate concurrent first renders and publish one atomic artifact."""
    lock = _preview_render_lock(cache_key)
    with lock:
        cached = load_evidence_preview_cache(cache_key)
        if cached is not None:
            return cached
        preview = render_evidence_page(
            file_bytes, filename, page_number, **kwargs,
        )
        store_evidence_preview_cache(cache_key, preview)
        return preview
