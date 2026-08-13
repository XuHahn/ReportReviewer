"""Evidence-gated checks for reviewed document-level facts.

These checks intentionally use the human-reviewed structured extraction and
then relocate every value in native document text. They do not ask a model to
decide whether two customer fields agree.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import date
from typing import Any

from models import DOC_TYPE_LABELS
from services.document_unitizer import locate_layout_quote
from services.evidence_graph_extraction import EvidenceGraphDocumentUnit
from services.evidence_graph_coverage import parse_test_identity
from services.evidence_graph_models import (
    EvidenceRecord,
    FindingSeverity,
    FindingStatus,
    GraphNode,
    NodeType,
    ReviewFinding,
)
from services.evidence_graph_source_locator import locate_execution_verdict
from services.evidence_graph_check_execution import build_check_execution


_FIELD_SOURCE_LABELS: dict[str, tuple[str, ...]] = {
    "client_name": ("委托单位", "客户名称", "申请人", "client", "applicant"),
    "client_address": ("委托单位地址", "客户地址", "地址", "address"),
    "sample_name": ("样品名称", "样品名", "产品名称", "sample name", "eut name"),
    "sample_model": ("型号规格", "样品型号", "型号", "model"),
    "part_number": ("零部件号", "部件号", "料号", "part number"),
    "rated_voltage": ("额定电压", "供电电压", "工作电压", "rated voltage"),
    "sample_id": ("样品编号", "样品号", "sample id", "sample no"),
    "test_plan_number": ("试验计划编号", "测试计划编号", "计划编号", "plan no"),
    "receive_date": ("接收日期", "收样日期", "receive date"),
    "test_date": ("试验日期", "测试日期", "test date"),
    "test_date_range": ("试验日期", "测试日期", "test date"),
    "issue_date": ("签发日期", "发布日期", "issue date"),
}


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _reviewed_json(rows: list[dict]) -> dict[str, Any]:
    for row in rows:
        if row.get("field_name") != "__structured__":
            continue
        raw = str(row.get("human_override_value") or row.get("field_value") or "")
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _path(value: dict[str, Any], *parts: str) -> Any:
    current: Any = value
    for part in parts:
        if not isinstance(current, dict):
            return ""
        current = current.get(part, "")
    return current


def _locate(source: str, quote: str) -> str:
    normalized_source: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(source):
        for normalized in unicodedata.normalize("NFKC", char):
            if normalized.isspace():
                continue
            normalized_source.append(normalized)
            positions.append(index)
    normalized_quote = "".join(
        char for char in unicodedata.normalize("NFKC", quote)
        if not char.isspace()
    )
    if not normalized_quote:
        return ""
    offset = "".join(normalized_source).find(normalized_quote)
    if offset < 0:
        return ""
    return source[positions[offset]:positions[offset + len(normalized_quote) - 1] + 1]


def _visible_phrase_boxes(layout_lines: list[dict], phrase: str) -> list[list[float]]:
    """Locate a visible phrase even when a table cell wraps onto several lines.

    PDF renderers split one spreadsheet cell into separate text fragments.  A
    phrase is accepted only when its fragments are vertically adjacent and
    occupy the same visual column; this is geometric reconstruction, not fuzzy
    text matching.
    """
    needle = _normalized("layout_phrase", phrase)
    if not needle:
        return []
    lines: list[tuple[str, list[float]]] = []
    direct: list[list[float]] = []
    for line in layout_lines:
        bbox = line.get("bbox") or []
        if len(bbox) != 4:
            continue
        text = _normalized("layout_phrase", str(line.get("text") or ""))
        if not text:
            continue
        box = list(map(float, bbox))
        lines.append((text, box))
        if needle in text:
            direct.append(box)
    if direct:
        return direct

    fragments = [(text, box) for text, box in lines if text in needle]
    fragments.sort(key=lambda item: ((item[1][1] + item[1][3]) / 2, item[1][0]))
    matches: list[list[float]] = []
    for start in range(len(fragments)):
        text, box = fragments[start]
        combined = text
        union = box[:]
        previous = box
        for index in range(start + 1, min(len(fragments), start + 5)):
            next_text, next_box = fragments[index]
            vertical_gap = next_box[1] - previous[3]
            previous_center = (previous[0] + previous[2]) / 2
            next_center = (next_box[0] + next_box[2]) / 2
            column_tolerance = max(
                25.0, previous[2] - previous[0], next_box[2] - next_box[0],
            )
            if vertical_gap < -1.0 or vertical_gap > 18.0:
                break
            if abs(next_center - previous_center) > column_tolerance:
                continue
            combined += next_text
            union = [
                min(union[0], next_box[0]), min(union[1], next_box[1]),
                max(union[2], next_box[2]), max(union[3], next_box[3]),
            ]
            previous = next_box
            if combined == needle:
                matches.append(union)
                break
            if not needle.startswith(combined):
                break
    unique: list[list[float]] = []
    for box in matches:
        if box not in unique:
            unique.append(box)
    return unique


def _visible_count_anchor(
    unit: EvidenceGraphDocumentUnit,
    raw_count: str,
    *,
    item_name: str,
    scope: str = "",
    scope_x_centers: tuple[float, ...] = (),
) -> tuple[str, list[float]]:
    """Locate a visible count cell in the same rendered row as the item.

    A bare digit must not be recovered from ``P4``, ``14V`` or another row.
    Spreadsheet values that only exist in a hidden column intentionally fail
    this gate because the reviewer cannot verify them in the source preview.
    """
    count_match = re.fullmatch(r"\s*(\d+)\s*(?:个|件|台|套|pcs?)?\s*", str(raw_count), re.I)
    if not count_match:
        return "", []
    count = count_match.group(1)
    item_boxes = _visible_phrase_boxes(unit.layout_lines, item_name)
    if not item_boxes:
        return "", []
    candidates: list[tuple[float, str, list[float]]] = []
    count_pattern = re.compile(
        rf"^\s*(?:样品(?:数量|数)|试样(?:数量|数)|sample\s*(?:qty|count))?"
        rf"\s*[:：]?\s*{re.escape(count)}\s*(?:个|件|台|套|pcs?)?\s*$",
        re.I,
    )
    for line in unit.layout_lines:
        text = str(line.get("text") or "").strip()
        bbox = line.get("bbox") or []
        if len(bbox) != 4 or not count_pattern.fullmatch(text):
            continue
        box = list(map(float, bbox))
        center_y = (box[1] + box[3]) / 2
        score = min(abs(center_y - (item[1] + item[3]) / 2) for item in item_boxes)
        candidates.append((score, text, box))
    if not candidates:
        return "", []
    candidates.sort(key=lambda item: item[0])
    height = max(1.0, candidates[0][2][3] - candidates[0][2][1])
    if candidates[0][0] > max(18.0, height * 1.5):
        return "", []
    same_row = [
        candidate for candidate in candidates
        if abs(candidate[0] - candidates[0][0]) < 1.0
    ]
    if len(same_row) == 1:
        return same_row[0][1], same_row[0][2]

    # Multi-model plans commonly repeat the same count under adjacent model
    # columns.  The row alone is then insufficient, but the visible model
    # header supplies a deterministic horizontal coordinate.  Do not infer a
    # column when the requested scope or its rendered header is unavailable.
    normalized_scope = str(scope or "").strip()
    if not normalized_scope or normalized_scope.casefold() == "default":
        return "", []
    scope_boxes = _visible_phrase_boxes(unit.layout_lines, normalized_scope)
    scope_centers = [(box[0] + box[2]) / 2 for box in scope_boxes]
    if not scope_centers:
        scope_centers = list(scope_x_centers)
    if not scope_centers:
        return "", []
    ranked = sorted(
        (
            min(abs((candidate[2][0] + candidate[2][2]) / 2 - center) for center in scope_centers),
            candidate,
        )
        for candidate in same_row
    )
    if len(ranked) > 1 and abs(ranked[1][0] - ranked[0][0]) < 1.0:
        return "", []
    return ranked[0][1][1], ranked[0][1][2]


def _visible_execution_sample_anchor(
    unit: EvidenceGraphDocumentUnit,
    sample_id: str,
) -> tuple[str, list[float]]:
    """Anchor a sample ID in an execution block, not an index or overview.

    A sample may legitimately appear once per mode.  Multiple valid execution
    occurrences are therefore not ambiguity for the distinct-sample count;
    the first source-order occurrence is a stable representative anchor.
    """
    needle = _normalized("sample_id", sample_id)
    if not needle:
        return "", []
    sample_labels: list[list[float]] = []
    date_labels: list[list[float]] = []
    matches: list[tuple[list[float], str]] = []
    for line in unit.layout_lines:
        bbox = line.get("bbox") or []
        if len(bbox) != 4:
            continue
        text = str(line.get("text") or "").strip()
        compact = _normalized("sample_id", text)
        box = list(map(float, bbox))
        if re.search(r"(?i)(?:sample\s*(?:no|id)|样品编号)", text):
            sample_labels.append(box)
        if re.search(r"(?i)(?:test\s*date|测试日期|试验日期)", text):
            date_labels.append(box)
        if compact == needle:
            matches.append((box, text))
    if not sample_labels or not date_labels:
        return "", []
    valid: list[tuple[float, float, list[float], str]] = []
    for box, text in matches:
        center_y = (box[1] + box[3]) / 2
        label_gap = min(abs(center_y - (label[1] + label[3]) / 2) for label in sample_labels)
        date_gap = min(abs(center_y - (label[1] + label[3]) / 2) for label in date_labels)
        height = max(1.0, box[3] - box[1])
        if label_gap <= max(18.0, height * 1.5) and date_gap <= max(18.0, height * 1.5):
            valid.append((box[1], box[0], box, text))
    if not valid:
        return "", []
    valid.sort(key=lambda item: (item[0], item[1]))
    return valid[0][3], valid[0][2]


def _anchor_metadata(
    unit: EvidenceGraphDocumentUnit,
    bbox: list[float],
    *,
    method: str,
    anchor_quote: str,
) -> dict:
    return {
        "version": 1,
        "status": "located" if len(bbox) == 4 else "text_anchored",
        "coordinate_space": "pdf_points" if len(bbox) == 4 else "",
        "method": method,
        "anchor_quote": anchor_quote,
        "rendered_pdf_hash": unit.rendered_pdf_hash,
        "page_width": unit.page_width,
        "page_height": unit.page_height,
        "page_rotation": unit.page_rotation,
    }


def _field_label_window(source: str, patterns: tuple[str, ...]) -> str:
    """Return a short native-text window proving a labelled field was inspected."""
    for pattern in patterns:
        match = re.search(pattern, source, re.IGNORECASE | re.MULTILINE)
        if not match:
            continue
        tail = source[match.start():match.start() + 240]
        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        return "\n".join(lines[:3])
    return ""


def _normalized(field: str, value: str) -> str:
    compact = "".join(
        char for char in unicodedata.normalize("NFKC", value).casefold()
        if not char.isspace()
    )
    if field == "rated_voltage":
        match = re.search(r"[-+]?\d+(?:\.\d+)?", compact)
        return f"{float(match.group()):g}v" if match else compact
    return re.sub(r"[，,。.;；:：()（）\[\]【】]", "", compact)


def _model_identity_tokens(value: str) -> set[str]:
    """Return explicit model-like tokens containing both letters and digits."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).upper()
    candidates = re.findall(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", normalized)
    return {
        token for token in candidates
        if len(token) >= 3 and re.search(r"[A-Z]", token) and re.search(r"\d", token)
    }


def _scope_matches_report_model(scope: str, report_model: str) -> bool:
    if str(scope or "").strip().casefold() == "default":
        return True
    scope_normalized = _normalized("sample_model", scope)
    report_normalized = _normalized("sample_model", report_model)
    if not scope_normalized or not report_normalized:
        return False
    if scope_normalized == report_normalized:
        return True
    return bool(_model_identity_tokens(scope) & _model_identity_tokens(report_model))


_STANDARD_CLAUSE_REFERENCE = re.compile(
    r"(?i)(?P<standard>(?:ISO|IEC|EN|GB\s*/?\s*T?|QC\s*/?\s*T?|CISPR)"
    r"\s*-?\s*\d{3,6}(?:\s*-\s*\d+)?)"
    r"\s*(?:[-:：中第]\s*)?(?P<clause>\d+(?:\.\d+){1,5})",
)


def _standard_clause_references(value: str) -> list[tuple[str, str, str]]:
    """Return source token, normalized standard identity and clause path."""
    rows: list[tuple[str, str, str]] = []
    for match in _STANDARD_CLAUSE_REFERENCE.finditer(str(value or "")):
        standard = re.sub(r"\s+", "", match.group("standard")).upper()
        standard = re.sub(r"-+", "-", standard)
        rows.append((match.group(0), standard, match.group("clause")))
    return rows


def _parse_date(value: str) -> date | None:
    match = re.search(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})", value)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _verdict_class(value: str) -> str:
    compact = re.sub(r"\s+", "", str(value or "")).casefold()
    if not compact:
        return ""
    if any(token in compact for token in ("不pass", "fail", "不符合", "不合格")):
        return "fail"
    if compact in {"pass", "符合", "合格", "通过"}:
        return "pass"
    return ""


def _result_comparison_row(
    row: dict,
    *,
    doc_type: str,
    item_name: str,
    sample_id: str,
    mode: str,
) -> dict[str, str]:
    """Return the small, source-backed field set needed by the review UI."""
    return {
        "doc_type": doc_type,
        "test_item": str(row.get("test_item") or item_name).strip(),
        "sample_id": sample_id,
        "mode": mode,
        "injection_point": str(row.get("injection_point") or "").strip(),
        "spec_requirement": str(row.get("spec_requirement") or "").strip(),
        "test_duration": str(row.get("test_duration") or "").strip(),
        "required_level": str(row.get("required_level") or "").strip(),
        "actual_level": str(row.get("actual_level") or "").strip(),
        "verdict_raw": str(row.get("verdict") or "").strip(),
        "verdict": _verdict_class(str(row.get("verdict") or "")),
    }


def _performance_level(value: str) -> str:
    """Return the auditable A-E functional class, ignoring footnote marks."""
    match = re.search(r"(?i)(?<![A-Z])([A-E])(?![A-Z])", str(value or ""))
    return match.group(1).upper() if match else ""


def _performance_level_meets(required: str, actual: str) -> bool:
    """A is the strongest EMC functional status; a stronger result is valid."""
    order = {level: index for index, level in enumerate("ABCDE")}
    return required in order and actual in order and order[actual] <= order[required]


def _raw_result_comparison_row(
    row: dict, *, item_name: str, sample_id: str, mode: str,
) -> dict[str, str]:
    return {
        "doc_type": "original_records",
        "test_item": _semantic_cell(row, "test_item") or item_name,
        "sample_id": sample_id,
        "mode": mode,
        "injection_point": _semantic_cell(row, "injection_position"),
        "spec_requirement": _semantic_cell(row, "test_specification"),
        "test_duration": (
            _semantic_cell(row, "test_duration")
            or _semantic_cell(row, "test_time")
        ),
        "required_level": _semantic_cell(row, "required_performance_level"),
        "actual_level": _semantic_cell(row, "actual_performance_level"),
        "verdict_raw": (
            _semantic_cell(row, "verdict")
            or _semantic_cell(row, "test_result")
        ),
        "verdict": _verdict_class(
            _semantic_cell(row, "verdict")
            or _semantic_cell(row, "test_result")
        ),
    }


def _execution_identity(value: str) -> str:
    identity = parse_test_identity(value)
    return identity.alias_key or identity.normalized_name


def _semantic_cell(row: dict, semantic: str) -> str:
    for cell in row.get("cells", []) if isinstance(row.get("cells"), list) else []:
        if not isinstance(cell, dict) or cell.get("semantic") != semantic:
            continue
        return str(cell.get("source_value") or cell.get("normalized_value") or "").strip()
    return ""


_NUMBER_UNIT_PATTERN = (
    r"(?:±|[<>≤≥]?[-+]?)\s*\d+(?:\.\d+)?\s*"
    r"(?:mΩ|kΩ|MΩ|mV|kV|V|mA|A|Hz|kHz|MHz|ms|min|s|%)"
)


def _number_unit_values(value: str) -> list[str]:
    return [
        re.sub(r"\s+", "", match.group(0))
        for match in re.finditer(_NUMBER_UNIT_PATTERN, str(value or ""), re.IGNORECASE)
    ]


def _number_unit_tokens(value: str) -> set[str]:
    return {item.casefold() for item in _number_unit_values(value)}


_ACCEPTANCE_LEVEL_PATTERN = re.compile(
    r"(?i)(?:\u6700\u4f4e\s*)?([A-E])\s*(?:\u7ea7|\u7c7b)"
    r"(?!\s*(?:\u96f6\u90e8\u4ef6|\u4ea7\u54c1|\u5668\u4ef6|\u90e8\u4ef6|\u6837\u54c1|DUT|components?|parts?|products?|devices?))|"
    r"(?:\u529f\u80fd\u72b6\u6001|\u529f\u80fd\u7b49\u7ea7|functional\s+status|performance\s+class)"
    r"\s*(?:\u9700?\u8fbe\u5230|\u5e94\u8fbe\u5230|\u6ee1\u8db3|\u4e3a|\u6307\u5b9a\u4e3a|\u53ef\u4ee5\u6307\u5b9a)?\s*([A-E])\s*(?:\u7ea7|\u7c7b)?|"
    r"class\s*([A-E])(?!\s*(?:components?|parts?|products?|devices?|DUT))",
)
_ACCEPTANCE_CONDITION_PATTERN = re.compile(
    r"(?i)(?:"
    r"\u8303\u56f4\u5185|\u8303\u56f4\u5916|\u8d85\u51fa|\u8d85\u8fc7|\u4f4e\u4e8e|\u9ad8\u4e8e|\u5c0f\u4e8e|\u5927\u4e8e|"
    r"\u5176\u4f59|\u5176\u4ed6|\u5bf9\u4e8e|\u9488\u5bf9|\u5f53|\u5982\u679c|\u82e5|\u65f6|\u60c5\u51b5\u4e0b|\u6761\u4ef6|\u6a21\u5f0f|\u9636\u6bb5|"
    r"\u96f6\u90e8\u4ef6|\u4ea7\u54c1|\u5668\u4ef6|\u90e8\u4ef6|DUT|Usmin|"
    r"\u8bd5\u9a8c\u524d|\u8bd5\u9a8c\u4e2d|\u8bd5\u9a8c\u540e|\u5197\u4f59\u7535\u6e90|"
    r"within|outside|exceed(?:s|ed)?|below|above|less\s+than|greater\s+than|"
    r"otherwise|other|for|when|if|under|condition|mode|stage|before|during|after|"
    r"components?|parts?|products?|devices?"
    r")",
)


def _acceptance_levels(value: str) -> list[str]:
    return sorted({
        token.upper()
        for match in _ACCEPTANCE_LEVEL_PATTERN.finditer(str(value or ""))
        for token in match.groups()
        if token
    })


def _acceptance_levels_are_conditioned(value: str) -> bool:
    """Return true only when every distinct level has an explicit condition.

    Plans legitimately assign different functional classes to voltage ranges,
    modes or stages.  Merely seeing A and C in one cell is therefore not a
    conflict.  Conversely, two bare statements such as "meets class A; meets
    class C" remain ambiguous and continue to require review.
    """
    source = str(value or "")
    matches = list(_ACCEPTANCE_LEVEL_PATTERN.finditer(source))
    levels = _acceptance_levels(source)
    if len(levels) < 2:
        return False
    qualified_levels: set[str] = set()
    for match in matches:
        level = next((token.upper() for token in match.groups() if token), "")
        clause_start = max(
            source.rfind(delimiter, 0, match.start())
            for delimiter in ("，", ",", "；", ";", "。", ".", "\n")
        ) + 1
        clause_ends = [
            index for delimiter in ("，", ",", "；", ";", "。", ".", "\n")
            if (index := source.find(delimiter, match.end())) >= 0
        ]
        clause_end = min(clause_ends) if clause_ends else len(source)
        clause = source[clause_start:clause_end]
        qualified = bool(_ACCEPTANCE_CONDITION_PATTERN.search(clause))
        if not qualified:
            # A condition is commonly followed by a comma and then the
            # assigned class ("for redundant supply, class A").  Keep the
            # preceding phrase within the same sentence/semicolon scope.
            sentence_start = max(
                source.rfind(delimiter, 0, match.start())
                for delimiter in ("；", ";", "。", ".", "\n")
            ) + 1
            qualified = bool(
                _ACCEPTANCE_CONDITION_PATTERN.search(
                    source[sentence_start:clause_end],
                )
            )
        if qualified:
            qualified_levels.add(level)
    return set(levels).issubset(qualified_levels)


def _fact_specs(structured: dict[str, dict[str, Any]]) -> list[tuple[str, str, str]]:
    facts: list[tuple[str, str, str]] = []

    order = structured.get("order_form", {})
    facts.extend([
        ("order_form", "client_name", str(_path(order, "applicant", "name_cn") or "")),
        ("order_form", "client_address", str(_path(order, "applicant", "address_cn") or "")),
        ("order_form", "sample_name", str(_path(order, "product", "name") or "")),
        ("order_form", "sample_model", str(_path(order, "product", "main_test_model") or "")),
        ("order_form", "part_number", str(_path(order, "product", "part_number") or "")),
        ("order_form", "rated_voltage", str(_path(order, "product", "voltage") or "")),
    ])

    plan = structured.get("test_plan", {})
    facts.extend([
        ("test_plan", "sample_name", str(_path(plan, "basic_info", "part_name") or "")),
        ("test_plan", "part_number", str(_path(plan, "basic_info", "part_number") or "")),
        ("test_plan", "sample_count", str(_path(plan, "basic_info", "sample_count") or "")),
        ("test_plan", "test_plan_number", str(_path(plan, "basic_info", "plan_number") or "")),
    ])

    report = structured.get("final_report", {})
    facts.extend([
        ("final_report", "client_name", str(_path(report, "cover", "client_name") or "")),
        ("final_report", "client_address", str(_path(report, "cover", "client_address") or "")),
        ("final_report", "sample_name", str(_path(report, "cover", "sample_name") or "")),
        ("final_report", "sample_model", str(_path(report, "cover", "sample_model") or "")),
        ("final_report", "part_number", str(
            _path(report, "cover", "part_number") or _path(report, "sample", "part_number") or ""
        )),
        ("final_report", "rated_voltage", str(_path(report, "sample", "rated_voltage") or "")),
        ("final_report", "test_plan_number", str(_path(report, "cover", "test_plan_number") or "")),
        ("final_report", "receive_date", str(_path(report, "cover", "receive_date") or "")),
        ("final_report", "test_date_range", str(_path(report, "cover", "test_date_range") or "")),
        ("final_report", "issue_date", str(_path(report, "cover", "issue_date") or "")),
    ])
    for sample_id in _path(report, "sample", "lab_sample_ids") or []:
        facts.append(("final_report", "sample_id", str(sample_id or "")))

    raw = structured.get("original_records", {})
    for meta in raw.get("metas", []) if isinstance(raw.get("metas"), list) else []:
        header = meta.get("_header_fields", {}) if isinstance(meta, dict) else {}
        if not isinstance(header, dict):
            continue
        for field, key in (
            ("sample_name", "sample_name"),
            ("sample_model", "sample_model"),
            ("rated_voltage", "power_supply"),
            ("sample_id", "sample_id"),
            ("test_date", "test_date"),
        ):
            facts.append(("original_records", field, str(header.get(key) or "")))
    return facts


def build_reviewed_document_checks_and_audit(
    graph_id: str,
    documents: dict[str, dict],
    units: list[EvidenceGraphDocumentUnit],
    metadata_by_doc_type: dict[str, list[dict]],
) -> tuple[list[EvidenceRecord], list[GraphNode], list[ReviewFinding], list[dict]]:
    structured = {
        doc_type: _reviewed_json(rows)
        for doc_type, rows in metadata_by_doc_type.items()
    }
    units_by_type: dict[str, list[EvidenceGraphDocumentUnit]] = defaultdict(list)
    for unit in units:
        units_by_type[unit.doc_type].append(unit)

    evidence: list[EvidenceRecord] = []
    nodes: list[GraphNode] = []
    facts: dict[str, dict[str, list[tuple[GraphNode, EvidenceRecord, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    seen: set[tuple[str, str, str]] = set()
    for doc_type, field, value in _fact_specs(structured):
        value = value.strip()
        normalized = _normalized(field, value)
        key = (doc_type, field, normalized)
        if not value or value in {"/", "-"} or not normalized or key in seen:
            continue
        located_unit = None
        exact_quote = ""
        for unit in units_by_type.get(doc_type, []):
            exact_quote = _locate(unit.native_text, value)
            if exact_quote:
                located_unit = unit
                break
        if located_unit is None:
            continue
        bbox, anchor_quote = locate_layout_quote(
            located_unit.layout_lines,
            exact_quote,
            context_terms=_FIELD_SOURCE_LABELS.get(field, ()),
        )
        seen.add(key)
        evidence_id = _id("evidence", graph_id, doc_type, field, normalized)
        node_id = _id("claim", graph_id, doc_type, field, normalized)
        record = EvidenceRecord(
            evidence_id=evidence_id,
            graph_id=graph_id,
            doc_id=str(documents[doc_type].get("doc_id") or ""),
            doc_type=doc_type,
            filename=str(documents[doc_type].get("filename") or ""),
            page_number=located_unit.page_number,
            sheet_name=located_unit.sheet_name,
            cell_range=located_unit.cell_range,
            bbox=bbox,
            exact_quote=exact_quote,
            content_hash=located_unit.source_hash,
            extraction_method="reviewed_structured_fact",
            confidence=1,
            metadata={
                "unit_id": located_unit.unit_id,
                "field_name": field,
                "source_anchor": {
                    "version": 1,
                    "status": "located" if len(bbox) == 4 else "text_anchored",
                    "coordinate_space": "pdf_points" if len(bbox) == 4 else "",
                    "method": "reviewed_field_layout_line",
                    "anchor_quote": anchor_quote,
                    "rendered_pdf_hash": located_unit.rendered_pdf_hash,
                    "page_width": located_unit.page_width,
                    "page_height": located_unit.page_height,
                    "page_rotation": located_unit.page_rotation,
                },
            },
        )
        node = GraphNode(
            node_id=node_id,
            graph_id=graph_id,
            node_type=NodeType.CLAIM,
            label=f"{doc_type} · {field}",
            canonical_key=field,
            properties={
                "claim_type": "reviewed_document_field",
                "raw_value": value,
                "normalized_value": normalized,
                "source_doc_type": doc_type,
                "field_name": field,
                "evidence_ids": [evidence_id],
                "human_reviewed": True,
            },
        )
        evidence.append(record)
        nodes.append(node)
        facts[field][doc_type].append((node, record, normalized))

    findings: list[ReviewFinding] = []
    source_missing_check_ids: set[str] = set()
    comparable_fields = {
        "client_name": "委托单位",
        "client_address": "委托单位地址",
        "sample_name": "样品名称",
        "sample_model": "样品型号",
        "part_number": "零件号",
        "rated_voltage": "额定/供电电压",
        "sample_id": "实验室样品编号",
    }
    for field, title in comparable_fields.items():
        by_doc = facts.get(field, {})
        if len(by_doc) < 2:
            continue
        # Scalar fields compare as singleton sets; repeated fields such as
        # sample_id compare the complete set per document. Looking only at the
        # global union incorrectly flags any valid multi-sample case.
        values_by_doc = {
            doc_type: frozenset(item[2] for item in rows)
            for doc_type, rows in by_doc.items()
        }
        if len(set(values_by_doc.values())) < 2:
            continue
        common_values = set.intersection(*(
            set(values) for values in values_by_doc.values()
        ))
        differing_values_by_doc = {
            doc_type: sorted(set(values) - common_values)
            for doc_type, values in values_by_doc.items()
        }
        subjects = [item[0] for rows in by_doc.values() for item in rows]
        records = [item[1] for rows in by_doc.values() for item in rows]
        semantic_identity = field in {"client_name", "client_address", "sample_name"}
        semantic_descriptions = {
            "client_name": "不同文档可能分别使用企业中文名、英文名或简称；没有统一社会信用代码等共同标识时，不能仅凭字符串差异判定为不同主体。",
            "client_address": "不同文档可能分别使用中英文地址或不同层级写法；没有共同地址标识时，不能仅凭字符串差异判定错误。",
            "sample_name": "不同文档使用了不同产品名称；名称可能是类别、简称或具体样品名，不能仅凭字符串差异判定错误。",
        }
        findings.append(ReviewFinding(
            finding_id=_id("finding", graph_id, "DOC-CROSS-FIELD-001", field),
            graph_id=graph_id,
            check_id="DOC-CROSS-FIELD-001",
            status=(FindingStatus.UNRESOLVED_ADVISORY if semantic_identity else FindingStatus.CONFIRMED_ERROR),
            severity=(FindingSeverity.WARNING if semantic_identity else FindingSeverity.ERROR),
            title=(f"{title}存在不同写法，需确认对应关系" if semantic_identity else f"{title}在文档间不一致"),
            description=(
                semantic_descriptions[field]
                if semantic_identity else "已人工确认的结构化字段在至少两类文档中出现不同值。"
            ),
            subject_node_ids=[item.node_id for item in subjects],
            evidence_ids=[item.evidence_id for item in records],
            dedupe_key=f"DOC-CROSS-FIELD-001:{field}",
            rule_version="reviewed-document-checks-2",
            metadata={
                "field_name": field,
                "values_by_doc_type": {
                    doc_type: sorted(values)
                    for doc_type, values in values_by_doc.items()
                },
                "common_values": sorted(common_values),
                "differing_values_by_doc_type": differing_values_by_doc,
            },
        ))

    plan_numbers = facts.get("test_plan_number", {})
    plan_number_rows = plan_numbers.get("test_plan", [])
    report_plan_number_rows = plan_numbers.get("final_report", [])
    if plan_number_rows and report_plan_number_rows:
        plan_values = {item[2] for item in plan_number_rows}
        report_values = {item[2] for item in report_plan_number_rows}
        if plan_values != report_values:
            rows = [*plan_number_rows, *report_plan_number_rows]
            findings.append(ReviewFinding(
                finding_id=_id("finding", graph_id, "DOC-PLAN-REF-001", "test_plan_number"),
                graph_id=graph_id, check_id="DOC-PLAN-REF-001",
                status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
                title="检测报告引用的测试计划编号不一致",
                description="测试计划自身编号与检测报告明确引用的计划编号不同。",
                subject_node_ids=[item[0].node_id for item in rows],
                evidence_ids=[item[1].evidence_id for item in rows],
                dedupe_key="DOC-PLAN-REF-001:test_plan_number",
                rule_version="reviewed-document-checks-2",
            ))

    plan_count_rows = facts.get("sample_count", {}).get("test_plan", [])
    report_sample_rows = facts.get("sample_id", {}).get("final_report", [])
    plan_item_rows = structured.get("test_plan", {}).get("test_items", [])
    report_item_rows = structured.get("final_report", {}).get("item_extractions", [])
    report_model = str(_path(structured.get("final_report", {}), "sample", "model") or "")
    per_item_sample_input_count = 0
    per_item_sample_expected_count = 0
    per_item_sample_anchored_count = 0
    per_item_sample_plan_anchor_missing_count = 0
    per_item_sample_report_anchor_missing_count = 0
    plan_sample_requirements_present = False
    plan_items_by_identity: dict[str, list[dict]] = defaultdict(list)
    for item in plan_item_rows if isinstance(plan_item_rows, list) else []:
        if not isinstance(item, dict):
            continue
        requirements = item.get("sample_requirements")
        if isinstance(requirements, dict) and requirements:
            plan_sample_requirements_present = True
            identity = _execution_identity(str(item.get("name") or item.get("code") or ""))
            if identity:
                plan_items_by_identity[identity].append(item)
    report_heading_pages: dict[str, set[int]] = defaultdict(set)
    report_names_by_identity: dict[str, set[str]] = defaultdict(set)
    for item in report_item_rows if isinstance(report_item_rows, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("test_item_name") or item.get("test_item_code") or "").strip()
        identity = _execution_identity(name)
        if identity and name:
            report_names_by_identity[identity].add(name)
    for identity, names in report_names_by_identity.items():
        for unit in units_by_type.get("final_report", []):
            if any(_locate(unit.native_text, name) for name in names):
                report_heading_pages[identity].add(unit.page_number)

    def active_report_identity(page_number: int) -> str:
        preceding = [
            (page, identity)
            for identity, pages in report_heading_pages.items()
            for page in pages if page <= page_number
        ]
        if not preceding:
            return ""
        latest_page = max(page for page, _ in preceding)
        identities = {identity for page, identity in preceding if page == latest_page}
        return next(iter(identities)) if len(identities) == 1 else ""

    for report_item in report_item_rows if isinstance(report_item_rows, list) else []:
        if not isinstance(report_item, dict):
            continue
        report_name = str(
            report_item.get("test_item_name") or report_item.get("test_item_code") or ""
        ).strip()
        candidates = plan_items_by_identity.get(_execution_identity(report_name), [])
        if len(candidates) != 1:
            continue
        requirements = candidates[0].get("sample_requirements") or {}
        numeric_requirements: list[tuple[str, str, int]] = []
        for scope, raw_count in requirements.items():
            match = re.search(r"\d+", str(raw_count))
            if match:
                numeric_requirements.append((str(scope), str(raw_count), int(match.group())))
        if not numeric_requirements:
            continue
        matching_scope = [
            item for item in numeric_requirements
            if _scope_matches_report_model(item[0], report_model)
        ]
        choices = matching_scope or numeric_requirements
        distinct_counts = {item[2] for item in choices}
        per_item_sample_expected_count += 1
        if len(distinct_counts) != 1:
            continue
        scope, raw_count, declared_count = choices[0]
        sample_blocks = _path(report_item, "test_results", "sample_data") or []
        sample_ids = sorted({
            str(block.get("sample_id") or "").strip()
            for block in sample_blocks if isinstance(block, dict)
            if str(block.get("sample_id") or "").strip()
        })
        if not sample_ids:
            continue
        plan_name = str(candidates[0].get("name") or candidates[0].get("code") or "")
        visible_scope_centers = sorted({
            round((box[0] + box[2]) / 2, 3)
            for unit in units_by_type.get("test_plan", [])
            for box in _visible_phrase_boxes(unit.layout_lines, scope)
        })
        # A continued spreadsheet table may omit its header after a page
        # break.  Reuse the visible column coordinate only when all occurrences
        # agree geometrically; multiple table layouts remain unresolved.
        inherited_scope_centers: tuple[float, ...] = ()
        if visible_scope_centers and (
            max(visible_scope_centers) - min(visible_scope_centers) < 3.0
        ):
            inherited_scope_centers = tuple(visible_scope_centers)
        plan_source: tuple[EvidenceGraphDocumentUnit, str, list[float]] | None = None
        for unit in units_by_type.get("test_plan", []):
            if not _locate(unit.native_text, plan_name):
                continue
            count_quote, count_bbox = _visible_count_anchor(
                unit,
                raw_count,
                item_name=plan_name,
                scope=scope,
                scope_x_centers=inherited_scope_centers,
            )
            if count_quote and count_bbox:
                plan_source = (unit, count_quote, count_bbox)
                break
        report_sources: list[tuple[EvidenceGraphDocumentUnit, str, list[float]] | None] = []
        for sample_id in sample_ids:
            located: tuple[EvidenceGraphDocumentUnit, str, list[float]] | None = None
            for unit in units_by_type.get("final_report", []):
                # Bind continuation pages to the most recent unambiguous
                # visible section heading.  The execution-block anchor below
                # excludes contents and global sample-overview pages.
                if active_report_identity(unit.page_number) != _execution_identity(report_name):
                    continue
                anchor_quote, sample_bbox = _visible_execution_sample_anchor(unit, sample_id)
                if sample_bbox:
                    located = (unit, anchor_quote, sample_bbox)
                    break
            report_sources.append(located)
        if plan_source is None:
            per_item_sample_plan_anchor_missing_count += 1
        if any(source is None for source in report_sources):
            per_item_sample_report_anchor_missing_count += 1
        if plan_source is None or any(source is None for source in report_sources):
            continue
        plan_unit, plan_quote, plan_bbox = plan_source
        per_item_sample_input_count += 1
        per_item_sample_anchored_count += 1
        if declared_count == len(sample_ids):
            continue
        suffix = _execution_identity(report_name)
        evidence_ids: list[str] = []
        plan_evidence_id = _id("evidence", graph_id, "GRAPH-SAMPLE-001", suffix, "plan")
        evidence_ids.append(plan_evidence_id)
        evidence.append(EvidenceRecord(
            evidence_id=plan_evidence_id, graph_id=graph_id,
            doc_id=str(documents["test_plan"].get("doc_id") or ""),
            doc_type="test_plan",
            filename=str(documents["test_plan"].get("filename") or ""),
            page_number=plan_unit.page_number, sheet_name=plan_unit.sheet_name,
            cell_range=plan_unit.cell_range,
            bbox=plan_bbox,
            exact_quote=plan_quote,
            content_hash=plan_unit.source_hash,
            extraction_method="deterministic_per_item_sample_count", confidence=1,
            metadata={
                "unit_id": plan_unit.unit_id,
                "scope": scope,
                "role": "declared_count",
                "declared_count": declared_count,
                "source_anchor": _anchor_metadata(
                    plan_unit,
                    plan_bbox,
                    method="visible_item_count_row",
                    anchor_quote=plan_quote,
                ),
            },
        ))
        for sample_index, (sample_id, report_source) in enumerate(
            zip(sample_ids, report_sources), start=1,
        ):
            assert report_source is not None
            report_unit, sample_quote, sample_bbox = report_source
            evidence_id = _id(
                "evidence", graph_id, "GRAPH-SAMPLE-001", suffix,
                "report", str(sample_index),
            )
            evidence_ids.append(evidence_id)
            evidence.append(EvidenceRecord(
                evidence_id=evidence_id, graph_id=graph_id,
                doc_id=str(documents["final_report"].get("doc_id") or ""),
                doc_type="final_report",
                filename=str(documents["final_report"].get("filename") or ""),
                page_number=report_unit.page_number,
                bbox=sample_bbox,
                exact_quote=sample_quote,
                content_hash=report_unit.source_hash,
                extraction_method="deterministic_per_item_sample_count", confidence=1,
                metadata={
                    "unit_id": report_unit.unit_id,
                    "sample_id": sample_id,
                    "role": "observed_sample_id",
                    "count_basis": "distinct_item_local_sample_ids",
                    "derived_count": len(sample_ids),
                    "source_anchor": _anchor_metadata(
                        report_unit,
                        sample_bbox,
                        method="item_local_sample_id",
                        anchor_quote=sample_quote,
                    ),
                },
            ))
        node_id = _id("claim", graph_id, "GRAPH-SAMPLE-001", suffix)
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label=f"{report_name} · 样品数量", canonical_key="per_item_sample_count",
            properties={
                "claim_type": "per_item_sample_count",
                "raw_value": {
                    "declared_count": declared_count,
                    "observed_count": len(sample_ids),
                },
                "source_doc_type": "cross_document",
                "declared_count": declared_count,
                "observed_count": len(sample_ids),
                "scope": scope,
                "evidence_ids": evidence_ids,
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id("finding", graph_id, "GRAPH-SAMPLE-001", suffix),
            graph_id=graph_id, check_id="GRAPH-SAMPLE-001",
            status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
            title=f"{report_name}的样品数量与计划不一致",
            description=(
                f"计划在“{scope}”范围要求 {declared_count} 个，"
                f"报告该测试项明确列出 {len(sample_ids)} 个不同样品编号。"
            ),
            subject_node_ids=[node_id], evidence_ids=evidence_ids,
            dedupe_key=f"GRAPH-SAMPLE-001:{suffix}",
            rule_version="reviewed-document-checks-5",
            metadata={
                "scope": scope, "declared_count": declared_count,
                "observed_count": len(sample_ids),
            },
        ))

    if not plan_sample_requirements_present and plan_count_rows and report_sample_rows:
        count_match = re.search(
            r"\d+", str(plan_count_rows[0][0].properties.get("raw_value") or ""),
        )
        distinct_report_samples = {item[2] for item in report_sample_rows}
        if count_match and int(count_match.group()) != len(distinct_report_samples):
            rows = [plan_count_rows[0], *report_sample_rows]
            findings.append(ReviewFinding(
                finding_id=_id("finding", graph_id, "GRAPH-SAMPLE-001", "declared_count"),
                graph_id=graph_id, check_id="GRAPH-SAMPLE-001",
                status=FindingStatus.CONFIRMED_ADVISORY, severity=FindingSeverity.WARNING,
                title="计划样品数量与报告可识别样品数量不一致",
                description=(
                    f"计划声明 {int(count_match.group())} 个，报告明确列出 "
                    f"{len(distinct_report_samples)} 个不同样品编号；需核实编号是否覆盖全部送试样品。"
                ),
                subject_node_ids=[item[0].node_id for item in rows],
                evidence_ids=[item[1].evidence_id for item in rows],
                dedupe_key="GRAPH-SAMPLE-001:declared_count",
                rule_version="reviewed-document-checks-2",
            ))

    report_ranges = facts.get("test_date_range", {}).get("final_report", [])
    raw_dates = facts.get("test_date", {}).get("original_records", [])
    if report_ranges and raw_dates:
        range_value = str(report_ranges[0][0].properties.get("raw_value") or "")
        dates = re.findall(r"20\d{2}\D+\d{1,2}\D+\d{1,2}", range_value)
        start = _parse_date(dates[0]) if dates else None
        end = _parse_date(dates[1]) if len(dates) > 1 else start
        if start and end:
            for node, record, _ in raw_dates:
                execution = _parse_date(str(node.properties.get("raw_value") or ""))
                if execution and not start <= execution <= end:
                    findings.append(ReviewFinding(
                        finding_id=_id("finding", graph_id, "DOC-TIMELINE-001", node.node_id),
                        graph_id=graph_id,
                        check_id="DOC-TIMELINE-001",
                        status=FindingStatus.CONFIRMED_ERROR,
                        severity=FindingSeverity.ERROR,
                        title="原始记录试验日期超出报告声明区间",
                        description=f"原始记录日期 {execution} 不在 {start} 至 {end} 内。",
                        subject_node_ids=[report_ranges[0][0].node_id, node.node_id],
                        evidence_ids=[report_ranges[0][1].evidence_id, record.evidence_id],
                        dedupe_key=f"DOC-TIMELINE-001:{node.node_id}",
                        rule_version="reviewed-document-checks-1",
                    ))

    receive_dates = facts.get("receive_date", {}).get("final_report", [])
    if receive_dates and report_ranges:
        received = _parse_date(str(receive_dates[0][0].properties.get("raw_value") or ""))
        range_value = str(report_ranges[0][0].properties.get("raw_value") or "")
        range_dates = re.findall(r"20\d{2}\D+\d{1,2}\D+\d{1,2}", range_value)
        started = _parse_date(range_dates[0]) if range_dates else None
        if received and started and received > started:
            findings.append(ReviewFinding(
                finding_id=_id("finding", graph_id, "DOC-TIMELINE-003", "receive_date"),
                graph_id=graph_id, check_id="DOC-TIMELINE-003",
                status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
                title="样品接收日期晚于试验开始日期",
                description=f"接收日期 {received}，试验开始日期 {started}。",
                subject_node_ids=[receive_dates[0][0].node_id, report_ranges[0][0].node_id],
                evidence_ids=[receive_dates[0][1].evidence_id, report_ranges[0][1].evidence_id],
                dedupe_key="DOC-TIMELINE-003:receive_date",
                rule_version="reviewed-document-checks-2",
            ))

    issue_dates = facts.get("issue_date", {}).get("final_report", [])
    if issue_dates and raw_dates:
        issue_date = _parse_date(str(issue_dates[0][0].properties.get("raw_value") or ""))
        dated_raw = [
            (parsed, node, record)
            for node, record, _ in raw_dates
            if (parsed := _parse_date(str(node.properties.get("raw_value") or ""))) is not None
        ]
        if issue_date and dated_raw:
            last_date, last_node, last_record = max(dated_raw, key=lambda item: item[0])
            if issue_date < last_date:
                findings.append(ReviewFinding(
                    finding_id=_id("finding", graph_id, "DOC-TIMELINE-002", "issue_date"),
                    graph_id=graph_id, check_id="DOC-TIMELINE-002",
                    status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
                    title="报告签发日期早于最后试验日期",
                    description=f"签发日期 {issue_date}，最后试验日期 {last_date}。",
                    subject_node_ids=[issue_dates[0][0].node_id, last_node.node_id],
                    evidence_ids=[issue_dates[0][1].evidence_id, last_record.evidence_id],
                    dedupe_key="DOC-TIMELINE-002:issue_date",
                    rule_version="reviewed-document-checks-2",
                ))

    # Missing cross-document fields are issues only when the labelled source
    # region can be anchored.  If the label cannot be found, the audit state is
    # system_incomplete instead of blaming the submitted material.
    missing_report_fields = []
    if not report_plan_number_rows:
        missing_report_fields.append((
            "DOC-PLAN-REF-001", "test_plan_number",
            "检测报告未填写测试计划编号",
            "报告的测试计划编号位置没有可用编号，无法核对计划引用。",
            (r"(?:Test\s*Plan\s*No\.?|测试计划编号|试验计划编号)\s*[:：]?",),
        ))
    if not report_ranges:
        missing_report_fields.append((
            "DOC-TIMELINE-001", "test_date_range",
            "检测报告未填写完整试验日期区间",
            "报告缺少可用的试验开始与结束日期，无法核对原始记录日期是否在区间内。",
            (r"(?:Test\s*Date|检测日期|试验日期)\s*[:：]?",),
        ))
    if not receive_dates:
        missing_report_fields.append((
            "DOC-TIMELINE-003", "receive_date",
            "检测报告未填写样品接收日期",
            "报告缺少可用的样品接收日期，无法核对接收时间是否早于试验。",
            (r"(?:Receive\s*Sample\s*Date|Sample\s*Receive\s*Date|样品接收日期|收样日期)\s*[:：]?",),
        ))
    if not issue_dates:
        missing_report_fields.append((
            "DOC-TIMELINE-002", "issue_date",
            "检测报告未填写签发日期",
            "报告缺少可用的签发日期，无法核对签发是否晚于最后试验日期。",
            (r"(?:Issued?\s*Date|签发日期|报告日期)\s*[:：]?",),
        ))
    for check_id, field_name, title, description, patterns in missing_report_fields:
        located = next((
            (unit, quote)
            for unit in units_by_type.get("final_report", [])
            if (quote := _field_label_window(unit.native_text, patterns))
        ), None)
        if located is None:
            continue
        unit, quote = located
        source_missing_check_ids.add(check_id)
        evidence_id = _id("evidence", graph_id, check_id, field_name, "missing")
        node_id = _id("claim", graph_id, check_id, field_name, "missing")
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id, graph_id=graph_id,
            doc_id=str(documents["final_report"].get("doc_id") or ""),
            doc_type="final_report",
            filename=str(documents["final_report"].get("filename") or ""),
            page_number=unit.page_number, exact_quote=quote,
            extraction_method="deterministic_missing_field_region", confidence=1,
            metadata={"unit_id": unit.unit_id, "field_name": field_name},
        ))
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label=f"检测报告 · {field_name}缺失",
            canonical_key=field_name,
            properties={
                "claim_type": "source_field_missing",
                "raw_value": quote,
                "source_doc_type": "final_report",
                "field_name": field_name,
                "evidence_ids": [evidence_id],
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id("finding", graph_id, check_id, field_name, "missing"),
            graph_id=graph_id, check_id=check_id,
            status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
            title=title, description=description,
            subject_node_ids=[node_id], evidence_ids=[evidence_id],
            dedupe_key=f"{check_id}:{field_name}:missing",
            rule_version="reviewed-document-checks-4",
            metadata={"missing_field": field_name},
        ))

    # A literal template token is positive evidence of an unfinished report
    # number; this is safer than treating an unextracted value as missing.
    for unit in units_by_type.get("final_report", []):
        match = re.search(
            r"(?:报告编号|Report\s*No\.?)\s*[：:]\s*"
            r"(Report\s*(?:报告编号|页眉编号))",
            unit.native_text,
            re.IGNORECASE,
        )
        if not match:
            continue
        quote = match.group(0)
        evidence_id = _id("evidence", graph_id, "final_report", "report_number_placeholder")
        node_id = _id("claim", graph_id, "final_report", "report_number_placeholder")
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id, graph_id=graph_id,
            doc_id=str(documents["final_report"].get("doc_id") or ""),
            doc_type="final_report",
            filename=str(documents["final_report"].get("filename") or ""),
            page_number=unit.page_number, exact_quote=quote,
            extraction_method="deterministic_report_completeness", confidence=1,
            metadata={"unit_id": unit.unit_id},
        ))
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label="检测报告 · 报告编号占位符", canonical_key="report_number",
            properties={
                "claim_type": "report_completeness",
                "raw_value": match.group(1),
                "source_doc_type": "final_report",
                "field_name": "report_number",
                "evidence_ids": [evidence_id],
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id("finding", graph_id, "DOC-REQUIRED-FIELD-001", "report_number"),
            graph_id=graph_id, check_id="DOC-REQUIRED-FIELD-001",
            status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
            title="检测报告编号仍为模板占位符",
            description="报告编号位置保留了模板字段，无法作为正式报告唯一标识。",
            subject_node_ids=[node_id], evidence_ids=[evidence_id],
            dedupe_key="DOC-REQUIRED-FIELD-001:report_number",
            rule_version="reviewed-document-checks-1",
        ))
        break

    # Mail-merge/template tokens in other cover fields are positive source
    # evidence that the draft has not been completed.  Report facts found on
    # later pages do not make an unfinished mandatory cover field disappear.
    cover_placeholder_patterns = {
        "client_name": r"Customer\s*[:：]\s*((?:Order|Customer)\s*客户名称)",
        "client_address": r"Address\s*[:：]\s*((?:Order|Customer)\s*客户地址)",
        "sample_name": r"Sample\s*Name\s*[:：]\s*(Sample\s*样品名称)",
        "sample_model": r"Sample\s*Model\s*[:：]\s*(Sample\s*(?:规格型号|样品型号))",
        "test_result": r"Test\s*Result\s*[:：]\s*(Project\s*检测结果)",
    }
    placeholder_evidence_ids: list[str] = []
    placeholder_fields: list[str] = []
    for field_name, pattern in cover_placeholder_patterns.items():
        located = next((
            (unit, match.group(0))
            for unit in units_by_type.get("final_report", [])
            if (match := re.search(pattern, unit.native_text, re.IGNORECASE))
        ), None)
        if located is None:
            continue
        unit, quote = located
        evidence_id = _id(
            "evidence", graph_id, "final_report", "cover_placeholder", field_name,
        )
        placeholder_evidence_ids.append(evidence_id)
        placeholder_fields.append(field_name)
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id, graph_id=graph_id,
            doc_id=str(documents["final_report"].get("doc_id") or ""),
            doc_type="final_report",
            filename=str(documents["final_report"].get("filename") or ""),
            page_number=unit.page_number, exact_quote=quote,
            extraction_method="deterministic_report_completeness", confidence=1,
            metadata={"unit_id": unit.unit_id, "field_name": field_name},
        ))
    if placeholder_evidence_ids:
        node_id = _id("claim", graph_id, "final_report", "cover_placeholders")
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label="检测报告 · 封面模板字段未替换",
            canonical_key="cover_placeholders",
            properties={
                "claim_type": "report_completeness",
                "raw_value": placeholder_fields,
                "missing_fields": placeholder_fields,
                "source_doc_type": "final_report",
                "evidence_ids": placeholder_evidence_ids,
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id(
                "finding", graph_id, "DOC-REQUIRED-FIELD-001", "cover_placeholders",
            ),
            graph_id=graph_id, check_id="DOC-REQUIRED-FIELD-001",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title="检测报告封面仍有模板字段未填写",
            description=(
                f"封面有 {len(placeholder_fields)} 个必填位置仍显示模板变量，"
                "包括客户、样品或检测结论信息。"
            ),
            subject_node_ids=[node_id], evidence_ids=placeholder_evidence_ids,
            dedupe_key="DOC-REQUIRED-FIELD-001:cover_placeholders",
            rule_version="reviewed-document-checks-2",
            metadata={"missing_fields": placeholder_fields},
        ))

    # The report's own three-role layout is the applicability policy. A single
    # empty label is not absence proof; all role labels must be contiguous with
    # no non-whitespace content between them.
    signature_sequences = (
        ("编制：", "审核：", "批准："),
        ("主检：", "审核：", "批准："),
        ("批准：", "审核：", "主检："),
        ("Prepared by:", "Reviewed by:", "Approved by:"),
    )
    for unit in units_by_type.get("final_report", []):
        exact_quote = ""
        for sequence in signature_sequences:
            exact_quote = _locate(unit.native_text, "".join(sequence))
            if exact_quote:
                break
        if not exact_quote:
            continue
        evidence_id = _id("evidence", graph_id, "final_report", "blank_signatures")
        node_id = _id("claim", graph_id, "final_report", "blank_signatures")
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id, graph_id=graph_id,
            doc_id=str(documents["final_report"].get("doc_id") or ""),
            doc_type="final_report",
            filename=str(documents["final_report"].get("filename") or ""),
            page_number=unit.page_number, exact_quote=exact_quote,
            extraction_method="deterministic_report_completeness", confidence=1,
            metadata={"unit_id": unit.unit_id},
        ))
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label="检测报告 · 签署信息为空", canonical_key="report_signatures",
            properties={
                "claim_type": "report_completeness",
                "raw_value": exact_quote,
                "source_doc_type": "final_report",
                "field_name": "report_signatures",
                "evidence_ids": [evidence_id],
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id("finding", graph_id, "DOC-SIGNATURE-001", "signatures"),
            graph_id=graph_id, check_id="DOC-SIGNATURE-001",
            status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
            title="检测报告编制、审核和批准信息为空",
            description="报告签署区三个角色标签连续出现，标签之间没有人员信息。",
            subject_node_ids=[node_id], evidence_ids=[evidence_id],
            dedupe_key="DOC-SIGNATURE-001:signatures",
            rule_version="reviewed-document-checks-2",
        ))
        break

    # A TOC reference beyond the document's declared/observed last page is
    # direct evidence of a missing appendix or stale pagination.
    total_report_pages = max(
        (unit.page_number for unit in units_by_type.get("final_report", [])),
        default=0,
    )
    overflow: tuple[EvidenceGraphDocumentUnit, str, int] | None = None
    toc_line_pattern = re.compile(r"(?im)^([^\n]{1,180}?\.{5,}\s*(\d{1,4}))\s*$")
    for unit in units_by_type.get("final_report", []):
        for match in toc_line_pattern.finditer(unit.native_text):
            referenced_page = int(match.group(2))
            if referenced_page > total_report_pages and (
                overflow is None or referenced_page > overflow[2]
            ):
                overflow = (unit, match.group(1), referenced_page)
    if overflow:
        unit, quote, referenced_page = overflow
        evidence_id = _id("evidence", graph_id, "final_report", "toc_page_overflow")
        node_id = _id("claim", graph_id, "final_report", "toc_page_overflow")
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id, graph_id=graph_id,
            doc_id=str(documents["final_report"].get("doc_id") or ""),
            doc_type="final_report",
            filename=str(documents["final_report"].get("filename") or ""),
            page_number=unit.page_number, exact_quote=quote,
            extraction_method="deterministic_report_pagination", confidence=1,
            metadata={"unit_id": unit.unit_id},
        ))
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label="检测报告 · 目录页码越界", canonical_key="toc_page_overflow",
            properties={
                "claim_type": "report_pagination",
                "raw_value": quote,
                "source_doc_type": "final_report",
                "field_name": "toc_page_overflow",
                "evidence_ids": [evidence_id],
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id("finding", graph_id, "DOC-PAGINATION-001", "toc_page_overflow"),
            graph_id=graph_id, check_id="DOC-PAGINATION-001",
            status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
            title="检测报告目录引用了不存在的页码",
            description=(
                f"目录引用第 {referenced_page} 页，但当前报告只有 "
                f"{total_report_pages} 页；需核实附件缺失或目录未更新。"
            ),
            subject_node_ids=[node_id], evidence_ids=[evidence_id],
            dedupe_key="DOC-PAGINATION-001:toc_page_overflow",
            rule_version="reviewed-document-checks-3",
        ))

    # Per-item extraction preserves sample/mode/result rows. Contradictory
    # verdicts are only compared inside an identical execution key so distinct
    # injection positions or test conditions are not conflated.
    report_items = structured.get("final_report", {}).get("item_extractions", [])
    report_level_comparison_inputs = 0
    for item_index, item in enumerate(report_items if isinstance(report_items, list) else []):
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("test_item_name") or item.get("test_item_code") or "").strip()
        sample_blocks = _path(item, "test_results", "sample_data") or []
        for block in sample_blocks if isinstance(sample_blocks, list) else []:
            if not isinstance(block, dict):
                continue
            sample_id = str(block.get("sample_id") or "").strip()
            mode = str(block.get("mode") or "").strip()
            grouped_rows: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
            for row in block.get("data_rows", []) if isinstance(block.get("data_rows"), list) else []:
                if not isinstance(row, dict):
                    continue
                key = tuple(_normalized("row", str(row.get(field) or "")) for field in (
                    "test_item", "injection_point", "spec_requirement", "test_duration",
                ))
                grouped_rows[key].append(row)
            for row_key, rows in grouped_rows.items():
                located_rows = [
                    (row, located)
                    for row in rows
                    if (located := locate_execution_verdict(
                        units_by_type.get("final_report", []),
                        sample_id=sample_id,
                        mode=mode,
                        verdict=str(row.get("verdict") or ""),
                    ))
                ]
                verdicts = {
                    _verdict_class(row.get("verdict", ""))
                    for row, _ in located_rows
                }
                verdicts.discard("")
                if verdicts != {"pass", "fail"}:
                    continue
                suffix = f"{item_index}:{sample_id}:{mode}:{'|'.join(row_key)}"
                node_id = _id("claim", graph_id, "final_report", "result_conflict", suffix)
                comparison_rows = [
                    _result_comparison_row(
                        row,
                        doc_type="final_report",
                        item_name=item_name,
                        sample_id=sample_id,
                        mode=mode,
                    )
                    for row, _ in located_rows
                    if _verdict_class(row.get("verdict", ""))
                ]
                required_levels = list(dict.fromkeys(
                    row["required_level"] for row in comparison_rows
                    if row["required_level"]
                ))
                actual_levels = list(dict.fromkeys(
                    row["actual_level"] for row in comparison_rows
                    if row["actual_level"]
                ))
                levels_conflict = len(required_levels) > 1
                report_evidence_ids: list[str] = []
                for verdict_class in ("pass", "fail"):
                    row, (located_unit, quote) = next(
                        (row, located) for row, located in located_rows
                        if _verdict_class(row.get("verdict", "")) == verdict_class
                    )
                    evidence_id = _id(
                        "evidence", graph_id, "final_report", "result_conflict",
                        suffix, verdict_class,
                    )
                    report_evidence_ids.append(evidence_id)
                    evidence.append(EvidenceRecord(
                        evidence_id=evidence_id, graph_id=graph_id,
                        doc_id=str(documents["final_report"].get("doc_id") or ""),
                        doc_type="final_report",
                        filename=str(documents["final_report"].get("filename") or ""),
                        page_number=located_unit.page_number, exact_quote=quote,
                        extraction_method="deterministic_report_result_conflict", confidence=1,
                        metadata={
                            "unit_id": located_unit.unit_id,
                            "verdict": verdict_class,
                            "role": "conflicting_report_result",
                            "comparison_rows": [next(
                                comparison for comparison in comparison_rows
                                if comparison["verdict"] == verdict_class
                            )],
                        },
                    ))
                nodes.append(GraphNode(
                    node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
                    label=f"检测报告 · {item_name}结果冲突",
                    canonical_key="report_result_conflict",
                    properties={
                        "claim_type": "report_result_conflict",
                        "raw_value": quote,
                        "sample_id": sample_id, "mode": mode,
                        "source_doc_type": "final_report",
                        "evidence_ids": report_evidence_ids,
                    },
                ))
                findings.append(ReviewFinding(
                    finding_id=_id("finding", graph_id, "REPORT-RESULT-CONFLICT-001", suffix),
                    graph_id=graph_id, check_id="REPORT-RESULT-CONFLICT-001",
                    status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
                    title=(
                        f"{item_name}同一次执行使用不同要求等级，导致结果相反"
                        if levels_conflict
                        else f"{item_name}同一样品和模式存在相互矛盾的结果"
                    ),
                    description=(
                        (
                            f"在样品 {sample_id or '未标识'}、{mode or '未标识模式'}的"
                            f"两条记录中，测试条件和实际等级相同，但要求等级分别为 "
                            f"{'、'.join(required_levels)}，因此分别判为通过和不通过；"
                            "需确认哪一条要求等级及记录有效。"
                        ) if levels_conflict else (
                            f"样品 {sample_id or '未标识'}、{mode or '未标识模式'}、"
                            "相同测试条件下同时出现通过和不通过结果。"
                        )
                    ),
                    subject_node_ids=[node_id], evidence_ids=report_evidence_ids,
                    dedupe_key=f"REPORT-RESULT-CONFLICT-001:{suffix}",
                    rule_version="reviewed-document-checks-3",
                    metadata={
                        "execution_identity": _execution_identity(item_name),
                        "sample_id": _normalized("sample_id", sample_id),
                        "mode": _normalized("mode", mode),
                        "required_levels": required_levels,
                        "actual_levels": actual_levels,
                    },
                ))

        # The result table has three different concepts: the test specification,
        # Performance criteria (required functional class), and Actual
        # performance.  The former implementation compared the section-summary
        # class with text inside ``Test specification requirements``.  That
        # conflated two columns and could not explain which execution failed.
        # Compare Performance criteria with Actual performance per execution,
        # then attach the corresponding raw-record row when it exists.
        for block in sample_blocks if isinstance(sample_blocks, list) else []:
            if not isinstance(block, dict):
                continue
            sample_id = str(block.get("sample_id") or "").strip()
            mode = str(block.get("mode") or "").strip()
            for row_index, row in enumerate(
                block.get("data_rows", [])
                if isinstance(block.get("data_rows"), list) else []
            ):
                if not isinstance(row, dict):
                    continue
                required_raw = str(row.get("required_level") or "").strip()
                actual_raw = str(row.get("actual_level") or "").strip()
                required_level = _performance_level(required_raw)
                actual_level = _performance_level(actual_raw)
                if not required_level or not actual_level:
                    continue
                report_level_comparison_inputs += 1
                if required_level == actual_level:
                    continue
                actual_is_better = _performance_level_meets(
                    required_level, actual_level,
                )
                located = locate_execution_verdict(
                    units_by_type.get("final_report", []),
                    sample_id=sample_id, mode=mode,
                    verdict=str(row.get("verdict") or ""),
                )
                if located is None:
                    continue
                suffix = f"{item_index}:{sample_id}:{mode}:{row_index}"
                unit, quote = located
                report_comparison = _result_comparison_row(
                    row, doc_type="final_report", item_name=item_name,
                    sample_id=sample_id, mode=mode,
                )
                evidence_ids: list[str] = []
                report_evidence_id = _id(
                    "evidence", graph_id, "final_report", "performance_level", suffix,
                )
                evidence_ids.append(report_evidence_id)
                evidence.append(EvidenceRecord(
                    evidence_id=report_evidence_id, graph_id=graph_id,
                    doc_id=str(documents["final_report"].get("doc_id") or ""),
                    doc_type="final_report",
                    filename=str(documents["final_report"].get("filename") or ""),
                    page_number=unit.page_number, exact_quote=quote,
                    extraction_method="deterministic_performance_level_check",
                    confidence=1,
                    metadata={
                        "unit_id": unit.unit_id,
                        "role": "observed",
                        "role_label": "检测报告结果行",
                        "parameter_name": "性能等级",
                        "comparison_value": actual_raw,
                        "comparison_rows": [report_comparison],
                    },
                ))

                raw_comparisons: list[dict[str, str]] = []
                sample_suffix = re.search(r"(\d{4,})$", sample_id)
                for meta_index, meta in enumerate(
                    structured.get("original_records", {}).get("metas", [])
                ):
                    if not isinstance(meta, dict):
                        continue
                    raw_name = str(
                        meta.get("test_item_name") or meta.get("test_item_code") or ""
                    ).strip()
                    if _execution_identity(raw_name) != _execution_identity(item_name):
                        continue
                    raw_mode = str(meta.get("test_mode") or "").strip()
                    raw_sequence = str(meta.get("sequence") or "").strip()
                    if mode and _normalized("mode", raw_mode) != _normalized("mode", mode):
                        continue
                    if sample_suffix and raw_sequence and not raw_sequence.endswith(sample_suffix.group(1)):
                        continue
                    for table in (meta.get("semantic_data") or {}).get("tables", []):
                        if not isinstance(table, dict) or table.get("table_family") != "test_data":
                            continue
                        for raw_row_index, raw_row in enumerate(table.get("rows", [])):
                            if not isinstance(raw_row, dict):
                                continue
                            raw_comparison = _raw_result_comparison_row(
                                raw_row, item_name=raw_name,
                                sample_id=sample_id, mode=raw_mode,
                            )
                            raw_quote_value = str(raw_row.get("evidence") or "").strip()
                            if not raw_quote_value:
                                continue
                            raw_located = next((
                                (raw_unit, raw_quote)
                                for raw_unit in units_by_type.get("original_records", [])
                                if (not raw_mode or _locate(raw_unit.native_text, raw_mode))
                                if (raw_quote := _locate(raw_unit.native_text, raw_quote_value))
                            ), None)
                            if raw_located is None:
                                continue
                            raw_unit, raw_quote = raw_located
                            raw_evidence_id = _id(
                                "evidence", graph_id, "original_records",
                                "performance_level", suffix, str(meta_index),
                                str(raw_row_index),
                            )
                            evidence_ids.append(raw_evidence_id)
                            raw_comparisons.append(raw_comparison)
                            evidence.append(EvidenceRecord(
                                evidence_id=raw_evidence_id, graph_id=graph_id,
                                doc_id=str(documents["original_records"].get("doc_id") or ""),
                                doc_type="original_records",
                                filename=str(documents["original_records"].get("filename") or ""),
                                page_number=raw_unit.page_number,
                                exact_quote=raw_quote,
                                extraction_method="deterministic_performance_level_check",
                                confidence=1,
                                metadata={
                                    "unit_id": raw_unit.unit_id,
                                    "role": "corroborating",
                                    "role_label": "对应原始记录结果行",
                                    "comparison_rows": [raw_comparison],
                                },
                            ))
                            break
                        if raw_comparisons:
                            break
                    if raw_comparisons:
                        break

                comparison_rows = [report_comparison, *raw_comparisons]
                node_id = _id("claim", graph_id, "performance_level", suffix)
                nodes.append(GraphNode(
                    node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
                    label=(
                        f"检测报告 · {item_name}实际性能满足要求"
                        if actual_is_better else
                        f"检测报告 · {item_name}实际性能未达到要求"
                    ),
                    canonical_key="performance_criteria_vs_actual_performance",
                    properties={
                        "claim_type": (
                            "performance_level_meets_requirement"
                            if actual_is_better else "performance_level_mismatch"
                        ),
                        "raw_value": f"required={required_raw};actual={actual_raw}",
                        "comparison_rows": comparison_rows,
                        "source_doc_type": "cross_document",
                        "evidence_ids": evidence_ids,
                    },
                ))
                # A better actual performance class satisfies the declared
                # requirement.  Keep the comparison node and source evidence
                # for auditability, but do not turn a passing execution into a
                # user-visible finding.  The frontend also filters historical
                # advisory findings produced by older graph versions.
                if actual_is_better:
                    continue
                findings.append(ReviewFinding(
                    finding_id=_id(
                        "finding", graph_id, "REPORT-SPEC-RESULT-001", suffix,
                    ),
                    graph_id=graph_id, check_id="REPORT-SPEC-RESULT-001",
                    status=FindingStatus.CONFIRMED_ERROR,
                    severity=FindingSeverity.ERROR,
                    title=f"{item_name}的实际性能等级未达到要求等级",
                    description=(
                        f"样品 {sample_id or '未标识'}、{mode or '未标识模式'}："
                        f"Performance criteria 为 {required_raw}，Actual performance "
                        f"为 {actual_raw}。实际等级低于要求，应判为不满足。"
                        + ("已附对应原始记录用于交叉核对。" if raw_comparisons else
                           "未找到可锚定的对应原始记录，本轮证据不完整。")
                    ),
                    subject_node_ids=[node_id], evidence_ids=evidence_ids,
                    dedupe_key=f"REPORT-SPEC-RESULT-001:{suffix}",
                    rule_version="reviewed-document-checks-5",
                    metadata={
                        "comparison_kind": "performance_criteria_vs_actual_performance",
                        "parameter_name": "性能等级",
                        "declared_value": required_raw,
                        "observed_values": [actual_raw],
                        "comparison_rows": comparison_rows,
                        "raw_record_anchored": bool(raw_comparisons),
                        "actual_is_better": False,
                    },
                ))

        # Compare a uniquely declared named parameter with result rows that
        # explicitly repeat the same parameter name. Ambiguous multi-valued
        # specifications are intentionally skipped.
        parameters_by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for parameter in item.get("spec_parameters", []) if isinstance(item.get("spec_parameters"), list) else []:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("参数名") or parameter.get("name") or "").strip()
            value = str(parameter.get("参数值") or parameter.get("value") or "").strip()
            if name and value:
                parameters_by_name[_normalized("parameter", name)].append((name, value))
        for normalized_name, declarations in parameters_by_name.items():
            if len(declarations) != 1:
                continue
            parameter_name, declared_value = declarations[0]
            declared_tokens = _number_unit_tokens(declared_value)
            if len(declared_tokens) != 1:
                continue
            observed_by_token: dict[str, tuple[str, str]] = {}
            for block in sample_blocks if isinstance(sample_blocks, list) else []:
                for row in block.get("data_rows", []) if isinstance(block, dict) else []:
                    requirement = str(row.get("spec_requirement") or "") if isinstance(row, dict) else ""
                    if normalized_name and normalized_name in _normalized("parameter", requirement):
                        tokens = _number_unit_tokens(requirement)
                        if len(tokens) == 1:
                            display_values = _number_unit_values(requirement)
                            if len(display_values) == 1:
                                observed_by_token.setdefault(
                                    next(iter(tokens)), (requirement.strip(), display_values[0]),
                                )
            observed_tokens = set(observed_by_token)
            if not observed_tokens or observed_tokens == declared_tokens:
                continue
            declared_unit = next((
                unit for unit in units_by_type.get("final_report", [])
                if _locate(unit.native_text, declared_value)
            ), None)
            observed_sources: list[tuple[str, str, EvidenceGraphDocumentUnit]] = []
            for token, (observed_text, observed_value) in observed_by_token.items():
                observed_unit = next((
                    unit for unit in units_by_type.get("final_report", [])
                    if _locate(unit.native_text, observed_text)
                ), None)
                if observed_unit is not None:
                    observed_sources.append((observed_value, observed_text, observed_unit))
            if declared_unit is None or len(observed_sources) != len(observed_by_token):
                continue
            suffix = f"{item_index}:{normalized_name}"
            evidence_ids: list[str] = []
            declared_evidence_id = _id(
                "evidence", graph_id, "final_report", "spec_result", suffix, "declared",
            )
            evidence_ids.append(declared_evidence_id)
            evidence.append(EvidenceRecord(
                evidence_id=declared_evidence_id, graph_id=graph_id,
                doc_id=str(documents["final_report"].get("doc_id") or ""),
                doc_type="final_report",
                filename=str(documents["final_report"].get("filename") or ""),
                page_number=declared_unit.page_number,
                exact_quote=_locate(declared_unit.native_text, declared_value),
                extraction_method="deterministic_report_spec_result", confidence=1,
                metadata={
                    "unit_id": declared_unit.unit_id,
                    "role": "declared",
                    "role_label": "规范要求",
                    "parameter_name": parameter_name,
                    "comparison_value": declared_value,
                    "source_section": "test_specification",
                },
            ))
            observed_evidence_ids: list[str] = []
            observed_values: list[str] = []
            for observed_index, (observed_value, observed_text, observed_unit) in enumerate(observed_sources, start=1):
                evidence_id = _id(
                    "evidence", graph_id, "final_report", "spec_result", suffix,
                    "observed", str(observed_index),
                )
                evidence_ids.append(evidence_id)
                observed_evidence_ids.append(evidence_id)
                observed_values.append(observed_value)
                evidence.append(EvidenceRecord(
                    evidence_id=evidence_id, graph_id=graph_id,
                    doc_id=str(documents["final_report"].get("doc_id") or ""),
                    doc_type="final_report",
                    filename=str(documents["final_report"].get("filename") or ""),
                    page_number=observed_unit.page_number,
                    exact_quote=_locate(observed_unit.native_text, observed_text),
                    extraction_method="deterministic_report_spec_result", confidence=1,
                    metadata={
                        "unit_id": observed_unit.unit_id,
                        "role": "observed",
                        "role_label": "结果记录实际采用",
                        "parameter_name": parameter_name,
                        "comparison_value": observed_value,
                        "source_section": "result_table",
                    },
                ))
            raw_evidence_ids: list[str] = []
            raw_values_by_token: dict[str, str] = {}
            raw_item_identity = _execution_identity(item_name)
            for meta_index, meta in enumerate(
                structured.get("original_records", {}).get("metas", [])
            ):
                if not isinstance(meta, dict):
                    continue
                raw_name = str(
                    meta.get("test_item_name") or meta.get("test_item_code") or ""
                ).strip()
                if _execution_identity(raw_name) != raw_item_identity:
                    continue
                for table in (meta.get("semantic_data") or {}).get("tables", []):
                    if not isinstance(table, dict) or table.get("table_family") != "test_data":
                        continue
                    for raw_row_index, raw_row in enumerate(table.get("rows", [])):
                        if not isinstance(raw_row, dict):
                            continue
                        raw_requirement = _semantic_cell(raw_row, "test_specification")
                        raw_tokens = _number_unit_tokens(raw_requirement)
                        matching_tokens = raw_tokens & observed_tokens
                        # Same confirmed test-item identity plus one exact
                        # number/unit token is a conservative cross-language
                        # anchor.  It does not infer which value is correct.
                        if len(raw_tokens) != 1 or len(matching_tokens) != 1:
                            continue
                        token = next(iter(matching_tokens))
                        if token in raw_values_by_token:
                            continue
                        raw_quote_value = str(raw_row.get("evidence") or "").strip()
                        if not raw_quote_value:
                            continue
                        raw_located = next((
                            (raw_unit, raw_quote)
                            for raw_unit in units_by_type.get("original_records", [])
                            if (raw_quote := _locate(raw_unit.native_text, raw_quote_value))
                        ), None)
                        if raw_located is None:
                            continue
                        raw_unit, raw_quote = raw_located
                        display_values = _number_unit_values(raw_requirement)
                        raw_value = display_values[0] if len(display_values) == 1 else raw_requirement
                        raw_evidence_id = _id(
                            "evidence", graph_id, "original_records", "spec_result",
                            suffix, str(meta_index), str(raw_row_index),
                        )
                        evidence_ids.append(raw_evidence_id)
                        raw_evidence_ids.append(raw_evidence_id)
                        raw_values_by_token[token] = raw_value
                        evidence.append(EvidenceRecord(
                            evidence_id=raw_evidence_id, graph_id=graph_id,
                            doc_id=str(documents["original_records"].get("doc_id") or ""),
                            doc_type="original_records",
                            filename=str(documents["original_records"].get("filename") or ""),
                            page_number=raw_unit.page_number,
                            exact_quote=raw_quote,
                            extraction_method="deterministic_report_spec_result",
                            confidence=1,
                            metadata={
                                "unit_id": raw_unit.unit_id,
                                "role": "corroborating",
                                "role_label": "对应原始记录实际执行值",
                                "parameter_name": parameter_name,
                                "comparison_value": raw_value,
                                "source_section": "original_record_result",
                            },
                        ))
            node_id = _id("claim", graph_id, "final_report", "spec_result", suffix)
            nodes.append(GraphNode(
                node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
                label=f"检测报告 · {parameter_name}规范与结果不一致",
                canonical_key="report_spec_result_mismatch",
                properties={
                    "claim_type": "report_spec_result_mismatch",
                    "raw_value": f"{declared_value} != {', '.join(sorted(observed_tokens))}",
                    "declared_tokens": sorted(declared_tokens),
                    "observed_tokens": sorted(observed_tokens),
                    "source_doc_type": (
                        "cross_document" if raw_evidence_ids else "final_report"
                    ),
                    "evidence_ids": evidence_ids,
                },
            ))
            findings.append(ReviewFinding(
                finding_id=_id("finding", graph_id, "REPORT-SPEC-RESULT-001", suffix),
                graph_id=graph_id, check_id="REPORT-SPEC-RESULT-001",
                status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
                title=f"{item_name}的{parameter_name}前后不一致",
                description=(
                    f"测试规范中的{parameter_name}要求为 {declared_value}，"
                    f"结果记录中的同名参数实际填写为 {'、'.join(observed_values)}。"
                    + (
                        "已附同一测试项目的原始记录执行值用于交叉核对。"
                        if raw_evidence_ids else
                        "未找到可保守锚定的对应原始记录，本轮只确认报告内部前后不一致。"
                    )
                ),
                subject_node_ids=[node_id], evidence_ids=evidence_ids,
                dedupe_key=f"REPORT-SPEC-RESULT-001:{suffix}",
                rule_version="reviewed-document-checks-3",
                metadata={
                    "comparison_kind": "specification_vs_result",
                    "parameter_name": parameter_name,
                    "declared_value": declared_value,
                    "observed_values": observed_values,
                    "declared_evidence_id": declared_evidence_id,
                    "observed_evidence_ids": observed_evidence_ids,
                    "raw_record_anchored": bool(raw_evidence_ids),
                    "raw_evidence_ids": raw_evidence_ids,
                    "raw_observed_values": list(raw_values_by_token.values()),
                },
            ))

    # The same sample/mode can accidentally be repeated as two separate DOCX
    # table blocks.  Comparing only rows inside each block misses exactly that
    # source defect, so perform one second aggregation across block boundaries.
    cross_block_rows: dict[tuple[str, str, str, str], list[tuple[str, str, dict]]] = (
        defaultdict(list)
    )
    for item in report_items if isinstance(report_items, list) else []:
        if not isinstance(item, dict):
            continue
        item_name = str(
            item.get("test_item_name") or item.get("test_item_code") or ""
        ).strip()
        for block in (_path(item, "test_results", "sample_data") or []):
            if not isinstance(block, dict):
                continue
            sample_id = str(block.get("sample_id") or "").strip()
            mode = str(block.get("mode") or "").strip()
            for row in block.get("data_rows", []) if isinstance(block.get("data_rows"), list) else []:
                if not isinstance(row, dict):
                    continue
                row_identity = "|".join(
                    _normalized("row", str(row.get(field) or ""))
                    for field in (
                        "test_item", "injection_point", "spec_requirement",
                        "test_duration",
                    )
                )
                key = (
                    _execution_identity(item_name),
                    _normalized("sample_id", sample_id),
                    _normalized("mode", mode),
                    row_identity,
                )
                cross_block_rows[key].append((sample_id, mode, row))
    for key, rows in cross_block_rows.items():
        verdicts = {_verdict_class(str(row.get("verdict") or "")) for _, _, row in rows}
        verdicts.discard("")
        if verdicts != {"pass", "fail"}:
            continue
        identity_key, sample_key, mode_key, row_identity = key
        if any(
            finding.check_id == "REPORT-RESULT-CONFLICT-001"
            and finding.metadata.get("execution_identity") == identity_key
            and finding.metadata.get("sample_id") == sample_key
            and finding.metadata.get("mode") == mode_key
            for finding in findings
        ):
            continue
        located_rows: list[tuple[dict, EvidenceGraphDocumentUnit, str]] = []
        for verdict_class in ("pass", "fail"):
            source_sample, source_mode, row = next(
                entry for entry in rows
                if _verdict_class(str(entry[2].get("verdict") or "")) == verdict_class
            )
            located = locate_execution_verdict(
                units_by_type.get("final_report", []),
                sample_id=source_sample, mode=source_mode,
                verdict=str(row.get("verdict") or ""),
            )
            if located:
                located_rows.append((row, located[0], located[1]))
        if len(located_rows) != 2:
            continue
        comparison_rows = [
            _result_comparison_row(
                row, doc_type="final_report", item_name=identity_key,
                sample_id=sample_key, mode=mode_key,
            )
            for row, _, _ in located_rows
        ]
        required_levels = sorted({
            row["required_level"] for row in comparison_rows if row["required_level"]
        })
        actual_levels = sorted({
            row["actual_level"] for row in comparison_rows if row["actual_level"]
        })
        suffix = f"{identity_key}:{sample_key}:{mode_key}:{row_identity}"
        evidence_ids: list[str] = []
        for row, unit, quote in located_rows:
            verdict_class = _verdict_class(str(row.get("verdict") or ""))
            evidence_id = _id(
                "evidence", graph_id, "final_report", "cross_block_result_conflict",
                suffix, verdict_class,
            )
            evidence_ids.append(evidence_id)
            evidence.append(EvidenceRecord(
                evidence_id=evidence_id, graph_id=graph_id,
                doc_id=str(documents["final_report"].get("doc_id") or ""),
                doc_type="final_report",
                filename=str(documents["final_report"].get("filename") or ""),
                page_number=unit.page_number, exact_quote=quote,
                extraction_method="deterministic_cross_block_result_conflict",
                confidence=1,
                metadata={
                    "unit_id": unit.unit_id, "verdict": verdict_class,
                    "role": "conflicting_report_result",
                    "comparison_rows": [next(
                        item for item in comparison_rows
                        if item["verdict"] == verdict_class
                    )],
                },
            ))
        node_id = _id("claim", graph_id, "cross_block_result_conflict", suffix)
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label=f"检测报告 · {identity_key}重复执行块结果冲突",
            canonical_key="report_cross_block_result_conflict",
            properties={
                "claim_type": "report_result_conflict",
                "raw_value": comparison_rows,
                "source_doc_type": "final_report",
                "sample_id": sample_key, "mode": mode_key,
                "evidence_ids": evidence_ids,
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id(
                "finding", graph_id, "REPORT-RESULT-CONFLICT-001", suffix,
            ),
            graph_id=graph_id, check_id="REPORT-RESULT-CONFLICT-001",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title="检测报告重复记录了同一样品和模式，且结论相反",
            description=(
                f"样品 {sample_key}、模式 {mode_key} 的相同测试条件出现两次；"
                f"要求等级为 {'、'.join(required_levels)}，实际等级为 "
                f"{'、'.join(actual_levels)}，结果同时有通过和不通过。"
            ),
            subject_node_ids=[node_id], evidence_ids=evidence_ids,
            dedupe_key=f"REPORT-RESULT-CONFLICT-001:{suffix}",
            rule_version="reviewed-document-checks-4",
            metadata={
                "execution_identity": identity_key,
                "sample_id": sample_key, "mode": mode_key,
                "required_levels": required_levels,
                "actual_levels": actual_levels,
                "comparison_rows": comparison_rows,
            },
        ))

    # Compare execution verdict sets across the original records and report at
    # the narrowest context both sources state explicitly. This catches an
    # added/removed verdict without assuming that translated condition text is
    # identical. Every compared verdict must still relocate to native text.
    execution_rows: dict[
        str, dict[tuple[str, str, str], dict[str, list[tuple[EvidenceGraphDocumentUnit, str]]]]
    ] = {
        "original_records": defaultdict(lambda: defaultdict(list)),
        "final_report": defaultdict(lambda: defaultdict(list)),
    }

    raw_metas = structured.get("original_records", {}).get("metas", [])
    for meta in raw_metas if isinstance(raw_metas, list) else []:
        if not isinstance(meta, dict):
            continue
        header = meta.get("_header_fields", {})
        header = header if isinstance(header, dict) else {}
        item_name = str(meta.get("test_item_name") or header.get("test_item_pdf") or "").strip()
        sample_id = str(header.get("sample_id") or "").strip()
        mode = str(meta.get("test_mode") or header.get("test_mode") or "").strip()
        key = (
            _execution_identity(item_name),
            _normalized("sample_id", sample_id),
            _normalized("mode", mode),
        )
        if not all(key):
            continue
        filename = str(meta.get("filename") or "")
        semantic = meta.get("semantic_data", {})
        tables = semantic.get("tables", []) if isinstance(semantic, dict) else []
        found_row = False
        for table in tables if isinstance(tables, list) else []:
            if not isinstance(table, dict) or table.get("table_family") != "test_data":
                continue
            for row in table.get("rows", []) if isinstance(table.get("rows"), list) else []:
                if not isinstance(row, dict):
                    continue
                verdict = _verdict_class(_semantic_cell(row, "verdict"))
                source_quote = str(row.get("evidence") or "").strip()
                if not verdict or not source_quote:
                    continue
                candidate_units = sorted(
                    units_by_type.get("original_records", []),
                    key=lambda unit: unit.filename != filename,
                )
                located = next((
                    (unit, quote)
                    for unit in candidate_units
                    if (not sample_id or sample_id in unit.native_text)
                    and (not mode or _locate(unit.native_text, mode))
                    if (quote := _locate(unit.native_text, source_quote))
                ), None)
                if located:
                    execution_rows["original_records"][key][verdict].append(located)
                    found_row = True
        if found_row:
            continue
        # Some simple records have no transcribed data table. Their explicit
        # header conclusion is still usable when it can be found on the same
        # sample/mode page.
        conclusion = str(header.get("test_conclusion") or "").strip()
        verdict = _verdict_class(conclusion)
        if not verdict or not conclusion:
            continue
        located = next((
            (unit, quote)
            for unit in units_by_type.get("original_records", [])
            if (not sample_id or sample_id in unit.native_text)
            and (not mode or _locate(unit.native_text, mode))
            and (quote := _locate(unit.native_text, conclusion))
        ), None)
        if located:
            execution_rows["original_records"][key][verdict].append(located)

    for item in report_items if isinstance(report_items, list) else []:
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("test_item_name") or item.get("test_item_code") or "").strip()
        sample_blocks = _path(item, "test_results", "sample_data") or []
        for block in sample_blocks if isinstance(sample_blocks, list) else []:
            if not isinstance(block, dict):
                continue
            sample_id = str(block.get("sample_id") or "").strip()
            mode = str(block.get("mode") or "").strip()
            key = (
                _execution_identity(item_name),
                _normalized("sample_id", sample_id),
                _normalized("mode", mode),
            )
            if not all(key):
                continue
            for row in block.get("data_rows", []) if isinstance(block.get("data_rows"), list) else []:
                if not isinstance(row, dict):
                    continue
                verdict = _verdict_class(str(row.get("verdict") or ""))
                if not verdict:
                    continue
                located = locate_execution_verdict(
                    units_by_type.get("final_report", []),
                    sample_id=sample_id,
                    mode=mode,
                    verdict=str(row.get("verdict") or ""),
                )
                if located:
                    execution_rows["final_report"][key][verdict].append(located)

    raw_by_key = execution_rows["original_records"]
    report_by_key = execution_rows["final_report"]
    for key in sorted(set(raw_by_key) & set(report_by_key)):
        raw_verdicts = set(raw_by_key[key])
        report_verdicts = set(report_by_key[key])
        # A cross-document consistency verdict requires one unambiguous result
        # on each side.  If either document contains both pass and fail, that
        # document's own conflict is the actionable problem; presenting it as
        # a raw-record/report mismatch obscures the actual source defect.
        if len(report_verdicts) != 1:
            # Keep a uniquely matching raw-record result as supporting context
            # on the report-conflict finding.  It helps reviewers understand
            # the three rows without manufacturing a second cross-document
            # error from an already ambiguous report result.
            if len(raw_verdicts) == 1:
                conflict = next((item for item in findings if (
                    item.check_id == "REPORT-RESULT-CONFLICT-001"
                    and item.metadata.get("execution_identity") == key[0]
                    and item.metadata.get("sample_id") == key[1]
                    and item.metadata.get("mode") == key[2]
                )), None)
                if conflict:
                    raw_verdict = next(iter(raw_verdicts))
                    raw_unit, raw_quote = raw_by_key[key][raw_verdict][0]
                    raw_evidence_id = _id(
                        "evidence", graph_id, "report_result_conflict_context",
                        ":".join(key), "original_records", raw_verdict,
                    )
                    evidence.append(EvidenceRecord(
                        evidence_id=raw_evidence_id, graph_id=graph_id,
                        doc_id=str(documents["original_records"].get("doc_id") or ""),
                        doc_type="original_records",
                        filename=str(documents["original_records"].get("filename") or ""),
                        page_number=raw_unit.page_number, sheet_name=raw_unit.sheet_name,
                        cell_range=raw_unit.cell_range, exact_quote=raw_quote,
                        extraction_method="deterministic_report_conflict_context",
                        confidence=1,
                        metadata={
                            "unit_id": raw_unit.unit_id,
                            "verdict": raw_verdict,
                            "role": "supporting_reference",
                        },
                    ))
                    conflict.evidence_ids.append(raw_evidence_id)
            continue
        if len(raw_verdicts) != 1:
            continue
        if raw_verdicts == report_verdicts:
            continue
        identity_key, sample_key, mode_key = key
        source_rows: list[tuple[str, str, EvidenceGraphDocumentUnit, str]] = []
        for doc_type, grouped in (
            ("original_records", raw_by_key[key]),
            ("final_report", report_by_key[key]),
        ):
            for verdict, located_rows in grouped.items():
                if located_rows:
                    source_rows.append((doc_type, verdict, *located_rows[0]))
        if not {row[0] for row in source_rows}.issuperset({"original_records", "final_report"}):
            continue
        suffix = ":".join(key)
        evidence_ids: list[str] = []
        for doc_type, verdict, unit, quote in source_rows:
            evidence_id = _id(
                "evidence", graph_id, "execution_verdict_consistency",
                suffix, doc_type, verdict,
            )
            evidence_ids.append(evidence_id)
            evidence.append(EvidenceRecord(
                evidence_id=evidence_id, graph_id=graph_id,
                doc_id=str(documents[doc_type].get("doc_id") or ""),
                doc_type=doc_type,
                filename=str(documents[doc_type].get("filename") or ""),
                page_number=unit.page_number, sheet_name=unit.sheet_name,
                cell_range=unit.cell_range, exact_quote=quote,
                extraction_method="deterministic_execution_verdict_consistency",
                confidence=1,
                metadata={"unit_id": unit.unit_id, "verdict": verdict},
            ))
        node_id = _id("claim", graph_id, "execution_verdict_consistency", suffix)
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label="原始记录与检测报告 · 执行结果不一致",
            canonical_key=f"execution_verdict:{identity_key}",
            properties={
                "claim_type": "execution_verdict_consistency",
                "raw_value": (
                    f"original_records={sorted(raw_verdicts)};"
                    f"final_report={sorted(report_verdicts)}"
                ),
                "source_doc_type": "cross_document",
                "sample_id": sample_key, "mode": mode_key,
                "raw_verdicts": sorted(raw_verdicts),
                "report_verdicts": sorted(report_verdicts),
                "evidence_ids": evidence_ids,
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id("finding", graph_id, "DOC-RESULT-CONSISTENCY-001", suffix),
            graph_id=graph_id, check_id="DOC-RESULT-CONSISTENCY-001",
            status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
            title="原始记录与检测报告的同次试验结果不一致",
            description=(
                f"同一测试项、样品 {sample_key}、模式 {mode_key}：原始记录结果为 "
                f"{', '.join(sorted(raw_verdicts))}，报告结果为 "
                f"{', '.join(sorted(report_verdicts))}。"
            ),
            subject_node_ids=[node_id], evidence_ids=evidence_ids,
            dedupe_key=f"DOC-RESULT-CONSISTENCY-001:{suffix}",
            rule_version="reviewed-document-checks-1",
        ))

    # A plan row containing multiple unconditional functional classes is not
    # silently reduced to the last value.  The source itself is ambiguous and
    # a reviewer must decide which class applies to which condition/mode.
    plan_acceptance_check_inputs = 0
    for item in structured.get("test_plan", {}).get("test_items", []):
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("name") or item.get("code") or "").strip()
        acceptance = str(item.get("acceptance") or "").strip()
        flattened_levels = _acceptance_levels(acceptance)
        if not item_name or not acceptance:
            continue
        located = next((
            unit for unit in units_by_type.get("test_plan", [])
            if _locate(unit.native_text, item_name)
            and _locate(unit.native_text, acceptance)
        ), None)
        if located is None:
            continue
        plan_acceptance_check_inputs += 1
        if len(flattened_levels) < 2:
            continue
        if _acceptance_levels_are_conditioned(acceptance):
            continue
        evidence_id = _id(
            "evidence", graph_id, "PLAN-ACCEPTANCE-CONFLICT-001", item_name,
        )
        node_id = _id(
            "claim", graph_id, "PLAN-ACCEPTANCE-CONFLICT-001", item_name,
        )
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id, graph_id=graph_id,
            doc_id=str(documents["test_plan"].get("doc_id") or ""),
            doc_type="test_plan",
            filename=str(documents["test_plan"].get("filename") or ""),
            page_number=located.page_number, sheet_name=located.sheet_name,
            cell_range=located.cell_range,
            exact_quote=_locate(located.native_text, acceptance),
            extraction_method="deterministic_plan_acceptance_conflict",
            confidence=1,
            metadata={"unit_id": located.unit_id, "levels": flattened_levels},
        ))
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label=f"{item_name} · 计划判定等级",
            canonical_key=f"plan_acceptance:{_execution_identity(item_name)}",
            properties={
                "claim_type": "plan_acceptance_conflict",
                "raw_value": acceptance,
                "levels": flattened_levels,
                "source_doc_type": "test_plan",
                "evidence_ids": [evidence_id],
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id(
                "finding", graph_id, "PLAN-ACCEPTANCE-CONFLICT-001", item_name,
            ),
            graph_id=graph_id, check_id="PLAN-ACCEPTANCE-CONFLICT-001",
            status=FindingStatus.UNRESOLVED_ADVISORY,
            severity=FindingSeverity.WARNING,
            title=f"{item_name}在计划中出现多个判定等级",
            description=(
                f"同一计划项目同时出现 {', '.join(flattened_levels)} 级，"
                "但原文没有给出足以自动区分适用模式或阶段的条件，需要人工确认。"
            ),
            subject_node_ids=[node_id], evidence_ids=[evidence_id],
            dedupe_key=f"PLAN-ACCEPTANCE-CONFLICT-001:{_execution_identity(item_name)}",
            rule_version="reviewed-document-checks-1",
            metadata={"levels": flattened_levels, "acceptance": acceptance},
        ))

    # A plan often declares one standard clause in its reference column and
    # repeats a more specific procedure clause in free text.  The latter may
    # equal the declaration or be its child, but it must not silently point to
    # a sibling test.  This comparison uses source tokens, not item names or a
    # template-specific row number.
    plan_clause_comparison_inputs = 0
    plan_has_explicit_clause_pairs = False
    plan_items_by_key = {
        _execution_identity(str(item.get("code") or item.get("name") or "")): item
        for item in structured.get("test_plan", {}).get("test_items", [])
        if isinstance(item, dict)
    }
    for detail_index, detail in enumerate(
        structured.get("test_plan", {}).get("test_details", []),
    ):
        if not isinstance(detail, dict):
            continue
        item_name = str(detail.get("code") or "").strip()
        item = plan_items_by_key.get(_execution_identity(item_name), {})
        declared_text = str(item.get("standard_clause") or "").strip()
        declared_refs = _standard_clause_references(declared_text)
        fields = detail.get("fields") or {}
        if not item_name or len(declared_refs) != 1 or not isinstance(fields, dict):
            continue
        declared_token, declared_standard, declared_clause = declared_refs[0]
        for field_name, field_value in fields.items():
            source_text = str(field_value or "").strip()
            if not source_text:
                continue
            source_references = _standard_clause_references(source_text)
            if source_references:
                plan_has_explicit_clause_pairs = True
            for ref_index, (source_token, source_standard, source_clause) in enumerate(
                source_references,
            ):
                declared_unit = next((
                    unit for unit in units_by_type.get("test_plan", [])
                    if _locate(unit.native_text, item_name)
                    and _locate(unit.native_text, declared_token)
                ), None)
                source_unit = next((
                    unit for unit in units_by_type.get("test_plan", [])
                    if _locate(unit.native_text, item_name)
                    and _locate(unit.native_text, source_token)
                ), None)
                if declared_unit is None or source_unit is None:
                    continue
                plan_clause_comparison_inputs += 1
                compatible = (
                    source_standard == declared_standard
                    and (
                        source_clause == declared_clause
                        or source_clause.startswith(f"{declared_clause}.")
                    )
                )
                if compatible:
                    continue
                suffix = f"{detail_index}:{field_name}:{ref_index}"
                declared_evidence_id = _id(
                    "evidence", graph_id, "PLAN-CLAUSE-CONSISTENCY-001",
                    suffix, "declared",
                )
                source_evidence_id = _id(
                    "evidence", graph_id, "PLAN-CLAUSE-CONSISTENCY-001",
                    suffix, "instruction",
                )
                evidence.extend((
                    EvidenceRecord(
                        evidence_id=declared_evidence_id, graph_id=graph_id,
                        doc_id=str(documents["test_plan"].get("doc_id") or ""),
                        doc_type="test_plan",
                        filename=str(documents["test_plan"].get("filename") or ""),
                        page_number=declared_unit.page_number,
                        sheet_name=declared_unit.sheet_name,
                        cell_range=declared_unit.cell_range,
                        exact_quote=_locate(declared_unit.native_text, declared_token),
                        extraction_method="deterministic_plan_clause_consistency",
                        confidence=1,
                        metadata={
                            "unit_id": declared_unit.unit_id,
                            "role": "declared", "role_label": "计划引用条款",
                            "comparison_value": declared_token,
                        },
                    ),
                    EvidenceRecord(
                        evidence_id=source_evidence_id, graph_id=graph_id,
                        doc_id=str(documents["test_plan"].get("doc_id") or ""),
                        doc_type="test_plan",
                        filename=str(documents["test_plan"].get("filename") or ""),
                        page_number=source_unit.page_number,
                        sheet_name=source_unit.sheet_name,
                        cell_range=source_unit.cell_range,
                        exact_quote=_locate(source_unit.native_text, source_token),
                        extraction_method="deterministic_plan_clause_consistency",
                        confidence=1,
                        metadata={
                            "unit_id": source_unit.unit_id,
                            "role": "observed", "role_label": "详细执行说明",
                            "comparison_value": source_token,
                        },
                    ),
                ))
                node_id = _id(
                    "claim", graph_id, "PLAN-CLAUSE-CONSISTENCY-001", suffix,
                )
                nodes.append(GraphNode(
                    node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
                    label=f"{item_name} · 计划条款引用不一致",
                    canonical_key=f"plan_clause_consistency:{_execution_identity(item_name)}",
                    properties={
                        "claim_type": "plan_clause_consistency",
                        "raw_value": {
                            "declared": declared_token,
                            "instruction": source_token,
                        },
                        "source_doc_type": "test_plan",
                        "evidence_ids": [declared_evidence_id, source_evidence_id],
                    },
                ))
                findings.append(ReviewFinding(
                    finding_id=_id(
                        "finding", graph_id, "PLAN-CLAUSE-CONSISTENCY-001", suffix,
                    ),
                    graph_id=graph_id,
                    check_id="PLAN-CLAUSE-CONSISTENCY-001",
                    status=FindingStatus.CONFIRMED_ERROR,
                    severity=FindingSeverity.ERROR,
                    title=f"{item_name}的计划条款与执行说明不一致",
                    description=(
                        f"计划引用列指向 {declared_token}，但详细执行说明指向 "
                        f"{source_token}；后者不是前者的同一条款或子条款。"
                    ),
                    subject_node_ids=[node_id],
                    evidence_ids=[declared_evidence_id, source_evidence_id],
                    dedupe_key=f"PLAN-CLAUSE-CONSISTENCY-001:{suffix}",
                    rule_version="reviewed-document-checks-1",
                    metadata={
                        "comparison_kind": "declared_clause_vs_instruction_clause",
                        "declared_value": declared_token,
                        "observed_values": [source_token],
                    },
                ))

    # Compare complete physical-instrument inventories by test item.  The
    # stable identity is manufacturer/model/serial, so Chinese/English display
    # names do not create false mismatches.  Software rows without a real
    # calibration date are intentionally outside this physical-instrument set.
    report_instruments_by_item: dict[str, dict[tuple[str, str, str], dict]] = defaultdict(dict)
    for row in structured.get("final_report", {}).get("instruments", []):
        if not isinstance(row, dict):
            continue
        item_name = str(row.get("test_item") or "").strip()
        calibration = str(row.get("calibration_end") or "").strip()
        identity = tuple(_normalized("instrument", str(row.get(field) or "")) for field in (
            "manufacturer", "model", "serial_no",
        ))
        if not item_name or not _parse_date(calibration) or not all(identity):
            continue
        report_instruments_by_item[_execution_identity(item_name)][identity] = row

    raw_instruments_by_item: dict[str, dict[tuple[str, str, str], dict]] = defaultdict(dict)
    for meta in structured.get("original_records", {}).get("metas", []):
        if not isinstance(meta, dict):
            continue
        item_name = str(meta.get("test_item_name") or meta.get("test_item_code") or "").strip()
        semantic = meta.get("semantic_data") or {}
        for table in semantic.get("tables", []) if isinstance(semantic, dict) else []:
            if not isinstance(table, dict) or table.get("table_family") != "instrument":
                continue
            for row in table.get("rows", []) if isinstance(table.get("rows"), list) else []:
                if not isinstance(row, dict):
                    continue
                calibration = _semantic_cell(row, "calibration_validity")
                identity = tuple(_normalized("instrument", _semantic_cell(row, field)) for field in (
                    "manufacturer", "model", "serial_number",
                ))
                if not item_name or not _parse_date(calibration) or not all(identity):
                    continue
                raw_instruments_by_item[_execution_identity(item_name)][identity] = {
                    "manufacturer": _semantic_cell(row, "manufacturer"),
                    "model": _semantic_cell(row, "model"),
                    "serial_no": _semantic_cell(row, "serial_number"),
                    "calibration_end": calibration,
                    "test_item": item_name,
                    "source_filename": str(meta.get("filename") or ""),
                    "source_quote": str(row.get("evidence") or "").strip(),
                }

    instrument_comparison_inputs = 0
    for identity_key in sorted(
        set(report_instruments_by_item) & set(raw_instruments_by_item)
    ):
        instrument_comparison_inputs += 1
        report_rows = report_instruments_by_item[identity_key]
        raw_rows = raw_instruments_by_item[identity_key]
        report_extra = sorted(set(report_rows) - set(raw_rows))
        raw_extra = sorted(set(raw_rows) - set(report_rows))
        matched = sorted(set(report_rows) & set(raw_rows))
        if not report_extra and not raw_extra:
            continue
        evidence_ids: list[str] = []
        comparison_rows: list[dict[str, str]] = []
        for role, doc_type, differing, source in (
            ("report_only", "final_report", report_extra, report_rows),
            ("raw_only", "original_records", raw_extra, raw_rows),
        ):
            for index, instrument_identity in enumerate(differing, start=1):
                row = source[instrument_identity]
                serial = str(row.get("serial_no") or "").strip()
                located = next((
                    unit for unit in units_by_type.get(doc_type, [])
                    if _locate(unit.native_text, serial)
                ), None)
                if located is None:
                    continue
                evidence_id = _id(
                    "evidence", graph_id, "DOC-INSTRUMENT-CONSISTENCY-001",
                    identity_key, role, str(index),
                )
                evidence_ids.append(evidence_id)
                evidence.append(EvidenceRecord(
                    evidence_id=evidence_id, graph_id=graph_id,
                    doc_id=str(documents[doc_type].get("doc_id") or ""),
                    doc_type=doc_type,
                    filename=str(documents[doc_type].get("filename") or ""),
                    page_number=located.page_number, sheet_name=located.sheet_name,
                    cell_range=located.cell_range,
                    exact_quote=_locate(located.native_text, serial),
                    extraction_method="deterministic_instrument_set_comparison",
                    confidence=1,
                    metadata={
                        "unit_id": located.unit_id, "role": role,
                        "comparison_rows": [{
                            "role": role,
                            "doc_type": doc_type,
                            "manufacturer": str(row.get("manufacturer") or ""),
                            "model": str(row.get("model") or ""),
                            "serial_no": serial,
                            "calibration_end": str(row.get("calibration_end") or ""),
                        }],
                    },
                ))
                comparison_rows.append({
                    "role": role,
                    "doc_type": doc_type,
                    "manufacturer": str(row.get("manufacturer") or ""),
                    "model": str(row.get("model") or ""),
                    "serial_no": serial,
                    "calibration_end": str(row.get("calibration_end") or ""),
                })
        # Show both source rows for every successful match.  Without the report
        # counterpart, the UI placed report-only rows next to matched raw rows
        # and made a correct set difference look like four unmatched devices.
        for index, instrument_identity in enumerate(matched, start=1):
            for role, doc_type, row in (
                ("matched_report", "final_report", report_rows[instrument_identity]),
                ("matched_raw", "original_records", raw_rows[instrument_identity]),
            ):
                serial = str(row.get("serial_no") or "").strip()
                located = next((
                    unit for unit in units_by_type.get(doc_type, [])
                    if _locate(unit.native_text, serial)
                ), None)
                if located is None:
                    continue
                comparison_row = {
                    "role": role,
                    "doc_type": doc_type,
                    "manufacturer": str(row.get("manufacturer") or ""),
                    "model": str(row.get("model") or ""),
                    "serial_no": serial,
                    "calibration_end": str(row.get("calibration_end") or ""),
                }
                evidence_id = _id(
                    "evidence", graph_id, "DOC-INSTRUMENT-CONSISTENCY-001",
                    identity_key, role, str(index),
                )
                evidence_ids.append(evidence_id)
                comparison_rows.append(comparison_row)
                evidence.append(EvidenceRecord(
                    evidence_id=evidence_id, graph_id=graph_id,
                    doc_id=str(documents[doc_type].get("doc_id") or ""),
                    doc_type=doc_type,
                    filename=str(documents[doc_type].get("filename") or ""),
                    page_number=located.page_number,
                    exact_quote=(
                        _locate(located.native_text, str(row.get("source_quote") or ""))
                        if doc_type == "original_records" else ""
                    ) or _locate(located.native_text, serial),
                    extraction_method="deterministic_instrument_matched_pair",
                    confidence=1,
                    metadata={
                        "unit_id": located.unit_id, "role": role,
                        "role_label": "已匹配仪器",
                        "match_key": list(instrument_identity),
                        "comparison_rows": [comparison_row],
                    },
                ))
        if not evidence_ids:
            continue
        comparison_summary = {
            "equality_required": True,
            "report_total": len(report_rows),
            "raw_total": len(raw_rows),
            "matched_count": len(matched),
            "report_only_count": len(report_extra),
            "raw_only_count": len(raw_extra),
        }
        difference_parts = [
            f"两份清单共同 {len(matched)} 台",
            f"检测报告另有 {len(report_extra)} 台未在原始记录中出现",
            (
                f"原始记录另有 {len(raw_extra)} 台未在检测报告中出现"
                if raw_extra else "原始记录没有独有仪器"
            ),
        ]
        difference_description = "；".join(difference_parts) + "。业务要求两份仪器清单完全一致。"
        display_name = str(
            next(iter(report_rows.values())).get("test_item") or identity_key
        )
        node_id = _id(
            "claim", graph_id, "DOC-INSTRUMENT-CONSISTENCY-001", identity_key,
        )
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label=f"{display_name} · 仪器清单差异",
            canonical_key=f"instrument_set:{identity_key}",
            properties={
                "claim_type": "instrument_set_comparison",
                "raw_value": comparison_rows,
                "source_doc_type": "cross_document",
                "comparison_rows": comparison_rows,
                "comparison_summary": comparison_summary,
                "evidence_ids": evidence_ids,
            },
        ))
        findings.append(ReviewFinding(
            finding_id=_id(
                "finding", graph_id, "DOC-INSTRUMENT-CONSISTENCY-001", identity_key,
            ),
            graph_id=graph_id, check_id="DOC-INSTRUMENT-CONSISTENCY-001",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title=f"{display_name}的报告仪器清单与原始记录不一致",
            description=difference_description,
            subject_node_ids=[node_id], evidence_ids=evidence_ids,
            dedupe_key=f"DOC-INSTRUMENT-CONSISTENCY-001:{identity_key}",
            rule_version="reviewed-document-checks-2",
            metadata={
                "comparison_rows": comparison_rows,
                "comparison_summary": comparison_summary,
                "test_item": display_name,
            },
        ))

    # Standard references are compared as explicit document tokens. Formatting
    # anomalies remain advisories because punctuation does not prove that the
    # wrong technical requirement was used.
    standard_rows: list[tuple[str, str, str, EvidenceRecord, GraphNode]] = []
    standard_pattern = re.compile(
        r"(?i)(?<![A-Z0-9])((?:Q/)?[A-Z][A-Z/]{1,12}-\d{3,5}-20\d{2})(?!\d)",
    )
    malformed_pattern = re.compile(
        r"(?i)(?<![A-Z0-9])((?:Q/)?[A-Z][A-Z/]{1,12}-\d{3,5}-20\d{2})(\d+(?:\.\d+)+)",
    )
    for doc_type, doc_units in units_by_type.items():
        for unit in doc_units:
            for match in standard_pattern.finditer(unit.native_text):
                quote = match.group(1)
                normalized = quote.upper().removeprefix("Q/")
                tail_match = re.search(r"(\d{3,5}-20\d{2})$", normalized)
                tail = tail_match.group(1) if tail_match else normalized
                evidence_id = _id("evidence", graph_id, doc_type, unit.unit_id, "standard_reference", quote)
                node_id = _id("claim", graph_id, doc_type, unit.unit_id, "standard_reference", quote)
                record = EvidenceRecord(
                    evidence_id=evidence_id, graph_id=graph_id,
                    doc_id=str(documents.get(doc_type, {}).get("doc_id") or ""),
                    doc_type=doc_type, filename=str(documents.get(doc_type, {}).get("filename") or ""),
                    page_number=unit.page_number, sheet_name=unit.sheet_name,
                    cell_range=unit.cell_range, exact_quote=quote,
                    extraction_method="deterministic_standard_reference", confidence=1,
                    metadata={"unit_id": unit.unit_id},
                )
                node = GraphNode(
                    node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
                    label=f"{DOC_TYPE_LABELS.get(doc_type, doc_type)} · 标准引用 {quote}",
                    canonical_key=f"standard_reference:{normalized}",
                    properties={
                        "claim_type": "standard_reference", "raw_value": quote,
                        "source_doc_type": doc_type, "field_name": "standard_reference",
                        "normalized_value": normalized, "evidence_ids": [evidence_id],
                    },
                )
                evidence.append(record)
                nodes.append(node)
                standard_rows.append((tail, normalized, quote, record, node))
            for match in malformed_pattern.finditer(unit.native_text):
                quote = match.group(0)
                evidence_id = _id("evidence", graph_id, doc_type, unit.unit_id, "malformed_standard", quote)
                node_id = _id("claim", graph_id, doc_type, unit.unit_id, "malformed_standard", quote)
                record = EvidenceRecord(
                    evidence_id=evidence_id, graph_id=graph_id,
                    doc_id=str(documents.get(doc_type, {}).get("doc_id") or ""),
                    doc_type=doc_type, filename=str(documents.get(doc_type, {}).get("filename") or ""),
                    page_number=unit.page_number, sheet_name=unit.sheet_name,
                    cell_range=unit.cell_range, exact_quote=quote,
                    extraction_method="deterministic_standard_reference", confidence=1,
                    metadata={"unit_id": unit.unit_id},
                )
                node = GraphNode(
                    node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
                    label=f"标准引用格式 · {quote}", canonical_key="malformed_standard_reference",
                    properties={
                        "claim_type": "standard_reference_format", "raw_value": quote,
                        "source_doc_type": doc_type, "field_name": "standard_reference",
                        "evidence_ids": [evidence_id],
                    },
                )
                evidence.append(record)
                nodes.append(node)
                findings.append(ReviewFinding(
                    finding_id=_id("finding", graph_id, "DOC-REFERENCE-001", node_id),
                    graph_id=graph_id, check_id="DOC-REFERENCE-001",
                    status=FindingStatus.CONFIRMED_ADVISORY, severity=FindingSeverity.WARNING,
                    title="标准编号与章节号疑似粘连",
                    description="标准年份后直接连接章节数字，建议核实章节分隔符。",
                    subject_node_ids=[node_id], evidence_ids=[evidence_id],
                    dedupe_key=f"DOC-REFERENCE-001:format:{node_id}",
                    rule_version="reviewed-document-checks-2",
                ))

    by_tail: dict[str, list[tuple[str, str, EvidenceRecord, GraphNode]]] = defaultdict(list)
    for tail, normalized, quote, record, node in standard_rows:
        by_tail[tail].append((normalized, quote, record, node))
    for tail, rows in by_tail.items():
        variants = {row[0] for row in rows}
        if len(variants) < 2:
            continue
        row_evidence = list({row[2].evidence_id: row[2] for row in rows}.values())
        row_nodes = list({row[3].node_id: row[3] for row in rows}.values())
        findings.append(ReviewFinding(
            finding_id=_id("finding", graph_id, "DOC-REFERENCE-001", tail),
            graph_id=graph_id, check_id="DOC-REFERENCE-001",
            status=FindingStatus.CONFIRMED_ADVISORY, severity=FindingSeverity.WARNING,
            title=f"标准编号 {tail} 存在不同前缀写法",
            description=f"检测到：{', '.join(sorted(variants))}。建议核实是否指向同一标准。",
            subject_node_ids=[node.node_id for node in row_nodes],
            evidence_ids=[record.evidence_id for record in row_evidence],
            dedupe_key=f"DOC-REFERENCE-001:variant:{tail}",
            rule_version="reviewed-document-checks-2",
        ))

    comparable_input_count = sum(
        1 for field in comparable_fields if len(facts.get(field, {})) >= 2
    )
    plan_reference_input_count = int(bool(plan_number_rows and report_plan_number_rows))
    if plan_sample_requirements_present:
        sample_count_input_count = per_item_sample_input_count
        sample_count_expected = per_item_sample_expected_count
        sample_count_anchored = per_item_sample_anchored_count
    else:
        sample_count_input_count = int(bool(plan_count_rows and report_sample_rows))
        sample_count_expected = 2
        sample_count_anchored = (
            int(bool(plan_count_rows)) + int(bool(report_sample_rows))
        )
    timeline_range_input_count = len(raw_dates) if report_ranges else 0
    receive_timeline_input_count = int(bool(receive_dates and report_ranges))
    issue_timeline_input_count = int(bool(issue_dates and raw_dates))

    report_result_group_count = 0
    report_spec_pair_count = 0
    for item in report_items if isinstance(report_items, list) else []:
        if not isinstance(item, dict):
            continue
        sample_blocks = _path(item, "test_results", "sample_data") or []
        for block in sample_blocks if isinstance(sample_blocks, list) else []:
            if not isinstance(block, dict):
                continue
            groups: set[tuple[str, str, str, str]] = set()
            for row in block.get("data_rows", []) if isinstance(block.get("data_rows"), list) else []:
                if not isinstance(row, dict) or not _verdict_class(str(row.get("verdict") or "")):
                    continue
                groups.add(tuple(_normalized("row", str(row.get(field) or "")) for field in (
                    "test_item", "injection_point", "spec_requirement", "test_duration",
                )))
            report_result_group_count += len(groups)
        for parameter in item.get("spec_parameters", []) if isinstance(item.get("spec_parameters"), list) else []:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("参数名") or parameter.get("name") or "").strip()
            value = str(parameter.get("参数值") or parameter.get("value") or "").strip()
            normalized_name = _normalized("parameter", name)
            if not normalized_name or len(_number_unit_tokens(value)) != 1:
                continue
            if any(
                normalized_name in _normalized(
                    "parameter", str(row.get("spec_requirement") or ""),
                )
                for block in sample_blocks if isinstance(sample_blocks, list) and isinstance(block, dict)
                for row in block.get("data_rows", []) if isinstance(block.get("data_rows"), list) and isinstance(row, dict)
            ):
                report_spec_pair_count += 1
    report_spec_pair_count += report_level_comparison_inputs

    cross_result_input_count = len(set(raw_by_key) & set(report_by_key))
    input_counts = {
        "DOC-CROSS-FIELD-001": comparable_input_count,
        "DOC-PLAN-REF-001": plan_reference_input_count,
        "GRAPH-SAMPLE-001": sample_count_input_count,
        "DOC-TIMELINE-001": timeline_range_input_count,
        "DOC-TIMELINE-003": receive_timeline_input_count,
        "DOC-TIMELINE-002": issue_timeline_input_count,
        "DOC-REQUIRED-FIELD-001": len(units_by_type.get("final_report", [])),
        "DOC-SIGNATURE-001": len(units_by_type.get("final_report", [])),
        "DOC-PAGINATION-001": len(units_by_type.get("final_report", [])),
        "REPORT-RESULT-CONFLICT-001": report_result_group_count,
        "REPORT-SPEC-RESULT-001": report_spec_pair_count,
        "DOC-RESULT-CONSISTENCY-001": cross_result_input_count,
        "PLAN-ACCEPTANCE-CONFLICT-001": plan_acceptance_check_inputs,
        "PLAN-CLAUSE-CONSISTENCY-001": plan_clause_comparison_inputs,
        "DOC-INSTRUMENT-CONSISTENCY-001": instrument_comparison_inputs,
        "DOC-REFERENCE-001": len(units),
    }
    finding_counts: dict[str, int] = defaultdict(int)
    for finding in findings:
        finding_counts[finding.check_id] += 1
    sample_count_missing_reason = "per_test_item_sample_count_not_fully_mapped"
    if per_item_sample_plan_anchor_missing_count:
        sample_count_missing_reason = "plan_item_sample_count_not_visually_anchored"
    elif per_item_sample_report_anchor_missing_count:
        sample_count_missing_reason = "report_item_sample_ids_not_locally_anchored"
    missing_input_audit = {
        "DOC-PLAN-REF-001": {
            "expected": 2,
            "anchored": int(bool(plan_number_rows)) + int(bool(report_plan_number_rows)),
            "state": "source_missing",
            "reason": "plan_or_report_plan_number_missing",
            "docs": {"test_plan", "final_report"},
        },
        "GRAPH-SAMPLE-001": {
            "expected": sample_count_expected,
            "anchored": sample_count_anchored,
            "state": "system_incomplete",
            "reason": sample_count_missing_reason,
            "docs": {"test_plan", "final_report"},
        },
        "DOC-TIMELINE-001": {
            "expected": 1 + len(raw_dates),
            "anchored": int(bool(report_ranges)) + len(raw_dates),
            "state": "source_missing",
            "reason": "report_test_date_range_missing",
            "docs": {"final_report", "original_records"},
        },
        "DOC-TIMELINE-003": {
            "expected": 2,
            "anchored": int(bool(receive_dates)) + int(bool(report_ranges)),
            "state": "source_missing",
            "reason": "report_receive_or_test_start_date_missing",
            "docs": {"final_report"},
        },
        "DOC-TIMELINE-002": {
            "expected": 1 + len(raw_dates),
            "anchored": int(bool(issue_dates)) + len(raw_dates),
            "state": "source_missing",
            "reason": "report_issue_date_missing",
            "docs": {"final_report", "original_records"},
        },
    }
    audit = []
    for check_id, input_count in input_counts.items():
        missing = missing_input_audit.get(check_id)
        forced_state = None
        expected = input_count
        anchored = input_count
        reason = ""
        docs: set[str] = set()
        applicability = "applicable"
        if (
            check_id == "PLAN-CLAUSE-CONSISTENCY-001"
            and not plan_has_explicit_clause_pairs
            and structured.get("test_plan", {}).get("extraction_quality", "complete")
                == "complete"
        ):
            applicability = "not_applicable"
            reason = "plan_has_no_explicit_clause_pair"
        if missing and missing["anchored"] < missing["expected"]:
            forced_state = missing["state"]
            if forced_state == "source_missing" and check_id not in source_missing_check_ids:
                forced_state = "system_incomplete"
            expected = int(missing["expected"])
            anchored = int(missing["anchored"])
            reason = str(missing["reason"])
            docs = set(missing["docs"])
        audit.append(build_check_execution(
            check_id,
            input_count,
            finding_counts.get(check_id, 0),
            applicability=applicability,  # type: ignore[arg-type]
            expected_input_count=expected,
            anchored_input_count=anchored,
            reason_code=reason,
            document_types=docs,
            forced_state=forced_state,  # type: ignore[arg-type]
        ))
    return evidence, nodes, findings, audit


def build_reviewed_document_checks(
    graph_id: str,
    documents: dict[str, dict],
    units: list[EvidenceGraphDocumentUnit],
    metadata_by_doc_type: dict[str, list[dict]],
) -> tuple[list[EvidenceRecord], list[GraphNode], list[ReviewFinding]]:
    evidence, nodes, findings, _ = build_reviewed_document_checks_and_audit(
        graph_id, documents, units, metadata_by_doc_type,
    )
    return evidence, nodes, findings
