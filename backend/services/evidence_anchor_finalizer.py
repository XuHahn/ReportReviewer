"""Promote verified in-run page coordinates into finding evidence records.

Every rule may describe evidence differently, but no rule should decide
whether source coordinates are persisted.  This finalizer runs after findings
exist, considers only their direct evidence, and promotes unambiguous layout
matches from the temporary unitization data.  Unused candidate coordinates
disappear with the run process.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from typing import Any

from services.document_unitizer import locate_layout_quote
from services.evidence_graph_extraction import EvidenceGraphDocumentUnit
from services.evidence_graph_store import EvidenceGraphStore
from utils.logger import get_logger


logger = get_logger(__name__)

_ROW_FIELDS = (
    "test_item", "injection_point", "spec_requirement", "test_duration",
    "required_level", "actual_level", "verdict_raw",
)
_ROW_WEIGHTS = {
    "test_item": 1.0, "injection_point": 1.0, "spec_requirement": 1.5,
    "test_duration": 1.0, "required_level": 3.0,
    "actual_level": 2.0, "verdict_raw": 3.0,
}


def _compact(value: Any) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", str(value or "")).casefold()
        if not character.isspace()
    )


def _valid_lines(layout_lines: list[dict]) -> list[tuple[str, list[float]]]:
    result: list[tuple[str, list[float]]] = []
    for line in layout_lines:
        bbox = line.get("bbox") or []
        text = str(line.get("text") or "").strip()
        if text and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            result.append((text, list(map(float, bbox))))
    return result


def _row_groups(
    lines: list[tuple[str, list[float]]],
) -> list[list[tuple[str, list[float]]]]:
    groups: list[list[tuple[str, list[float]]]] = []
    for line in sorted(lines, key=lambda item: ((item[1][1] + item[1][3]) / 2, item[1][0])):
        center = (line[1][1] + line[1][3]) / 2
        target = next((
            group for group in reversed(groups[-4:])
            if abs(center - sum(
                (item[1][1] + item[1][3]) / 2 for item in group
            ) / len(group)) <= max(2.5, (line[1][3] - line[1][1]) * 0.35)
        ), None)
        if target is None:
            groups.append([line])
        else:
            target.append(line)
    return groups


def _phrase_boxes(layout_lines: list[dict], phrase: str) -> list[list[float]]:
    needle = _compact(phrase)
    if not needle:
        return []
    lines = [(_compact(text), box) for text, box in _valid_lines(layout_lines)]
    direct = [box for text, box in lines if needle in text]
    if direct:
        return direct
    fragments = [(text, box) for text, box in lines if text and text in needle]
    fragments.sort(key=lambda item: ((item[1][1] + item[1][3]) / 2, item[1][0]))
    matches: list[list[float]] = []
    for start in range(len(fragments)):
        combined, first = fragments[start]
        union = first[:]
        previous = first
        for text, box in fragments[start + 1:start + 9]:
            vertical_gap = box[1] - previous[3]
            previous_center = (previous[0] + previous[2]) / 2
            center = (box[0] + box[2]) / 2
            tolerance = max(25.0, previous[2] - previous[0], box[2] - box[0])
            if vertical_gap < -1.0 or vertical_gap > 18.0:
                break
            if abs(center - previous_center) > tolerance:
                continue
            combined += text
            union = _union([union, box])
            previous = box
            if combined == needle:
                matches.append(union)
                break
            if not needle.startswith(combined):
                break
    return [list(item) for item in dict.fromkeys(tuple(item) for item in matches)]


def _structured_metadata_row(
    layout_lines: list[dict], metadata: dict,
) -> tuple[list[float], list[list[float]], str]:
    rows = metadata.get("comparison_rows")
    if not isinstance(rows, list):
        return [], [], ""
    resolved: list[tuple[float, list[list[float]]]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        hits = [
            (field, _ROW_WEIGHTS[field], _phrase_boxes(layout_lines, str(row.get(field) or "")))
            for field in _ROW_FIELDS
            if str(row.get(field) or "").strip()
        ]
        hits = [item for item in hits if item[2]]
        if len(hits) < 3:
            continue
        centers = sorted({
            round((box[1] + box[3]) / 2, 1)
            for _field, _weight, boxes in hits for box in boxes
        })
        best_score = 0.0
        best_boxes: list[list[float]] = []
        tied = False
        for center in centers:
            states: list[tuple[float, float, list[list[float]]]] = [(0.0, -1.0, [])]
            for _field, weight, boxes in hits:
                nearby = [
                    box for box in boxes
                    if abs((box[1] + box[3]) / 2 - center) <= 14.0
                ]
                next_states = list(states)
                for score, right, selected in states:
                    for box in nearby:
                        if box[0] + 1.0 < right:
                            continue
                        next_states.append((score + weight, box[2], [*selected, box]))
                states = sorted(next_states, key=lambda item: (item[0], item[1]), reverse=True)[:160]
            score, _right, boxes = max(states, key=lambda item: item[0])
            if score > best_score + 0.01:
                best_score, best_boxes, tied = score, boxes, False
            elif score >= 4.0 and abs(score - best_score) <= 0.01 and boxes != best_boxes:
                tied = True
        if best_score >= 4.0 and len(best_boxes) >= 3 and not tied:
            resolved.append((best_score, best_boxes))
    if len(resolved) != 1:
        return [], [], ""
    rectangles = resolved[0][1]
    return _union(rectangles), rectangles, "structured_metadata_row"


def _union(rectangles: list[list[float]]) -> list[float]:
    return [
        min(item[0] for item in rectangles),
        min(item[1] for item in rectangles),
        max(item[2] for item in rectangles),
        max(item[3] for item in rectangles),
    ]


def _region_count(rectangles: list[list[float]]) -> int:
    if not rectangles:
        return 0
    remaining = [item[:] for item in rectangles]
    groups = 0
    while remaining:
        group = [remaining.pop(0)]
        changed = True
        while changed:
            changed = False
            for candidate in list(remaining):
                connected = False
                for member in group:
                    vertical_overlap = max(
                        0.0, min(member[3], candidate[3]) - max(member[1], candidate[1]),
                    )
                    min_height = max(1.0, min(member[3] - member[1], candidate[3] - candidate[1]))
                    if vertical_overlap / min_height >= 0.45:
                        gap = max(0.0, max(member[0], candidate[0]) - min(member[2], candidate[2]))
                        connected = gap <= max(18.0, min_height * 1.5)
                    else:
                        upper, lower = (
                            (member, candidate) if member[1] <= candidate[1]
                            else (candidate, member)
                        )
                        gap = max(0.0, lower[1] - upper[3])
                        connected = gap <= max(
                            8.0,
                            max(member[3] - member[1], candidate[3] - candidate[1]) * 0.8,
                        )
                    if connected:
                        break
                if connected:
                    group.append(candidate)
                    remaining.remove(candidate)
                    changed = True
        groups += 1
    return groups


def _quote_fragments(quote: str) -> list[str]:
    fragments = [
        _compact(item)
        for item in re.split(r"(?:\s*/\s*|\s*\|\s*|[\r\n]+)", quote)
    ]
    return list(dict.fromkeys(
        item for item in fragments
        if len(item) >= 2 and item not in {"符合", "不符合", "pass", "fail"}
    ))


def _stream_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _strict_ordered_quote(
    layout_lines: list[dict], quote: str,
) -> tuple[list[float], list[list[float]], str]:
    """Map one unique, exact normalized quote across ordered visual lines."""
    needle = _stream_text(quote)
    if len(needle) < 6:
        return [], [], ""
    indexed: list[tuple[list[float], int, int]] = []
    fragments: list[str] = []
    cursor = 0
    for text, bbox in _valid_lines(layout_lines):
        fragment = _stream_text(text)
        if not fragment:
            continue
        start = cursor
        cursor += len(fragment)
        fragments.append(fragment)
        indexed.append((bbox, start, cursor))
    source = "".join(fragments)
    start = source.find(needle)
    if start < 0 or source.find(needle, start + 1) >= 0:
        return [], [], ""
    end = start + len(needle)
    rectangles = [
        bbox for bbox, line_start, line_end in indexed
        if line_start < end and line_end > start
    ]
    if (
        not rectangles or len(rectangles) > 32
        or _region_count(rectangles) != 1
    ):
        return [], [], ""
    return _union(rectangles), rectangles, "strict_ordered_quote"


def _semantic_quote_row(
    layout_lines: list[dict], quote: str,
) -> tuple[list[float], list[list[float]], str]:
    """Resolve a slash-joined structured row without fuzzy text matching."""
    fragments = _quote_fragments(quote)
    if len(fragments) < 3:
        return [], [], ""
    all_parts = [
        _compact(item)
        for item in re.split(r"(?:\s*/\s*|\s*\|\s*|[\r\n]+)", quote)
        if _compact(item)
    ]
    candidates: list[tuple[int, list[list[float]]]] = []
    for group in _row_groups(_valid_lines(layout_lines)):
        matched_boxes: list[list[float]] = []
        matched_fragments: set[str] = set()
        for text, box in group:
            compact_text = _compact(text)
            for fragment in fragments:
                if fragment in compact_text or compact_text in fragment:
                    matched_fragments.add(fragment)
                    matched_boxes.append(box)
                    break
        if len(matched_fragments) >= 3:
            # The three-or-more meaningful fragments establish the unique
            # row.  Once established, retain every exact row cell represented
            # in the quote, including short values such as C and the verdict.
            complete_boxes = [
                box for text, box in group
                if any(
                    part == _compact(text)
                    or (len(_compact(text)) >= 2 and _compact(text) in part)
                    for part in all_parts
                )
            ]
            candidates.append((len(matched_fragments), complete_boxes or matched_boxes))
    if not candidates:
        return [], [], ""
    best_score = max(item[0] for item in candidates)
    best = [boxes for score, boxes in candidates if score == best_score]
    if len(best) != 1:
        return [], [], ""
    rectangles = sorted(best[0], key=lambda item: item[0])
    return _union(rectangles), rectangles, "semantic_structured_row"


def _parameter_ordinal_row(
    layout_lines: list[dict], metadata: dict,
) -> tuple[list[float], list[list[float]], str]:
    """Resolve a parameter value by a visible ordinal row when its label is absent.

    Example: structured metadata says ``Severity 3`` while the source table
    renders the first cell as only ``3``.  Both ordinal and value are matched
    atomically on the same row, so a different ``2 V`` elsewhere is rejected.
    """
    parameter_name = str(metadata.get("parameter_name") or "").strip()
    ordinal_match = re.search(r"(?<!\d)(\d{1,3})\s*$", parameter_name)
    value = metadata.get("comparison_value")
    if isinstance(value, list):
        value = " ".join(map(str, value))
    value_key = _compact(value)
    if not ordinal_match or len(value_key) < 2:
        return [], [], ""
    ordinal = ordinal_match.group(1)
    candidates: list[list[list[float]]] = []
    for group in _row_groups(_valid_lines(layout_lines)):
        has_ordinal = any(_compact(text) == ordinal for text, _box in group)
        value_boxes = [
            box for text, box in group
            if _compact(text).startswith(value_key)
        ]
        if has_ordinal and value_boxes:
            candidates.append(value_boxes)
    if len(candidates) != 1:
        return [], [], ""
    rectangles = candidates[0]
    return _union(rectangles), rectangles, "semantic_parameter_ordinal_row"


def locate_evidence_layout(
    unit: EvidenceGraphDocumentUnit,
    quote: str,
    metadata: dict,
) -> tuple[list[float], list[list[float]], str, str]:
    """Return one verified locator and its strategy for an evidence record."""
    context_terms = tuple(
        str(item).strip() for item in (
            metadata.get("field_name"), metadata.get("parameter_name"),
        ) if str(item or "").strip()
    )
    bbox, anchor_quote = locate_layout_quote(
        unit.layout_lines, quote, context_terms=context_terms,
    )
    if bbox:
        return bbox, [bbox], "unique_layout_quote", anchor_quote or quote
    bbox, rectangles, method = _structured_metadata_row(unit.layout_lines, metadata)
    if bbox:
        return bbox, rectangles, method, quote
    bbox, rectangles, method = _semantic_quote_row(unit.layout_lines, quote)
    if bbox:
        return bbox, rectangles, method, quote
    bbox, rectangles, method = _strict_ordered_quote(unit.layout_lines, quote)
    if bbox:
        return bbox, rectangles, method, quote
    bbox, rectangles, method = _parameter_ordinal_row(unit.layout_lines, metadata)
    if bbox:
        return bbox, rectangles, method, str(metadata.get("comparison_value") or quote)
    return [], [], "", ""


def finalize_finding_evidence_anchors(
    graph_id: str,
    store: EvidenceGraphStore,
    units: list[EvidenceGraphDocumentUnit],
) -> dict[str, Any]:
    snapshot = store.get_snapshot(graph_id)
    direct_ids = {
        evidence_id
        for finding in snapshot.findings
        for evidence_id in finding.evidence_ids
    }
    evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
    units_by_id = {unit.unit_id: unit for unit in units}
    units_by_page: dict[tuple[str, str, int, str, str], list[EvidenceGraphDocumentUnit]] = defaultdict(list)
    for unit in units:
        units_by_page[(
            unit.doc_id, unit.doc_type, unit.page_number,
            unit.sheet_name, unit.cell_range,
        )].append(unit)

    stats: dict[str, Any] = {
        "expected": len(direct_ids), "already_located": 0,
        "promoted": 0, "unresolved": 0, "strategies": {},
    }
    for evidence_id in sorted(direct_ids):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            stats["unresolved"] += 1
            continue
        if evidence.bbox:
            stats["already_located"] += 1
            continue
        unit_id = str(evidence.metadata.get("unit_id") or "")
        candidates = [units_by_id[unit_id]] if unit_id in units_by_id else units_by_page.get((
            evidence.doc_id, evidence.doc_type, evidence.page_number,
            evidence.sheet_name, evidence.cell_range,
        ), [])
        located: list[tuple[EvidenceGraphDocumentUnit, list[float], list[list[float]], str, str]] = []
        for unit in candidates:
            bbox, rectangles, method, anchor_quote = locate_evidence_layout(
                unit, evidence.exact_quote, evidence.metadata,
            )
            if bbox:
                located.append((unit, bbox, rectangles, method, anchor_quote))
        if len(located) != 1:
            stats["unresolved"] += 1
            continue
        unit, bbox, rectangles, method, anchor_quote = located[0]
        if store.enrich_evidence_locator(
            graph_id, evidence_id, bbox,
            method=f"audit_finalize_{method}", rectangles=rectangles,
            rendered_pdf_hash=unit.rendered_pdf_hash,
            page_width=unit.page_width, page_height=unit.page_height,
            content_hash=unit.source_hash, anchor_quote=anchor_quote,
        ):
            stats["promoted"] += 1
            strategies = stats["strategies"]
            strategies[method] = int(strategies.get(method) or 0) + 1
        else:
            stats["unresolved"] += 1
    stats["located"] = stats["already_located"] + stats["promoted"]
    logger.info(
        "evidence_anchor_finalized", graph_id=graph_id,
        expected_count=stats["expected"], located_count=stats["located"],
        promoted_count=stats["promoted"], unresolved_count=stats["unresolved"],
        strategies=stats["strategies"],
    )
    return stats
