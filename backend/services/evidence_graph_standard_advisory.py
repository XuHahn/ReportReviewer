"""Optional standard-scope reminders; never expands the formal test plan scope."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

from services.evidence_graph_coverage import ObservedTestItem, parse_test_identity
from services.evidence_graph_models import (
    EvidenceRecord, FindingSeverity, FindingStatus, GraphEdge, GraphNode, NodeType,
    RelationOrigin, RelationStatus, RelationType, ReviewFinding,
)
from services.evidence_graph_source_locator import locate_execution_verdict
from utils.logger import get_logger


logger = get_logger(__name__)

_MANDATORY = re.compile(r"(?i)(?:必须进行|应进行|必须测试|shall\s+be\s+tested|shall\s+test)")
_STANDARD_CLAUSE_REF = re.compile(
    r"(?i)\b(ISO)\s*[- ]?\s*(\d{3,6})\s*[- ]\s*(\d+)"
    r"(?:\s*[:：-]\s*(\d+(?:\.\d+)*))?",
)
_CLASS_RE = re.compile(
    r"(?i)(?:class\s*([A-E])|([A-E])\s*(?:级|类))",
)
_NUMBER_UNIT_RE = re.compile(
    r"(?i)([-+±]?\s*\d+(?:[.,]\d+)?)\s*(mV|kV|V|mA|A|mHz|kHz|MHz|Hz|ms|s|MΩ|kΩ|Ω|%)\b",
)


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{hashlib.sha256(chr(31).join(parts).encode()).hexdigest()[:20]}"


def _same_identity(left, right) -> bool:
    if left.normalized_name == right.normalized_name:
        return True
    if left.alias_key and left.alias_key == right.alias_key:
        return True
    if left.item_code and left.item_code == right.item_code:
        return True
    return bool(
        left.subitem_code
        and left.subitem_code == right.subitem_code
        and left.family_key == right.family_key
    )


def _parse_standard_reference(value: str) -> tuple[str, str]:
    match = _STANDARD_CLAUSE_REF.search(str(value or ""))
    if not match:
        return "", ""
    code = f"{match.group(1).upper()} {match.group(2)}-{match.group(3)}"
    return code, str(match.group(4) or "")


def _clause_in_scope(requirement_clause: str, planned_clause: str) -> bool:
    requirement = str(requirement_clause or "").strip().casefold()
    planned = str(planned_clause or "").strip().casefold()
    return bool(planned) and (
        requirement == planned or requirement.startswith(f"{planned}.")
    )


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
        char for char in unicodedata.normalize("NFKC", str(quote or ""))
        if not char.isspace()
    )
    if not normalized_quote:
        return ""
    offset = "".join(normalized_source).find(normalized_quote)
    if offset < 0:
        return ""
    return source[positions[offset]:positions[offset + len(normalized_quote) - 1] + 1]


def _reviewed_json(rows: list[dict]) -> dict[str, Any]:
    for row in rows:
        if row.get("field_name") != "__structured__":
            continue
        try:
            value = json.loads(str(
                row.get("human_override_value") or row.get("field_value") or ""
            ))
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _class_tokens(value: str) -> set[str]:
    text = str(value or "").strip()
    direct = re.fullmatch(r"(?i)([A-E])(?:\s*[¹²³⁴⁵⁾)]*)?", text)
    tokens = {
        token.upper()
        for match in _CLASS_RE.findall(text)
        for token in match if token
    }
    if direct:
        tokens.add(direct.group(1).upper())
    return tokens


def _semantic_cell(row: dict, semantic: str) -> str:
    for cell in row.get("cells", []) if isinstance(row.get("cells"), list) else []:
        if isinstance(cell, dict) and cell.get("semantic") == semantic:
            return str(cell.get("source_value") or cell.get("normalized_value") or "").strip()
    return ""


def _result_comparison_row(
    row: dict, *, doc_type: str, item_name: str, sample_id: str, mode: str,
) -> dict[str, str]:
    verdict = str(row.get("verdict") or "").strip()
    compact_verdict = re.sub(r"\s+", "", verdict).casefold()
    verdict_class = (
        "fail" if any(token in compact_verdict for token in (
            "不pass", "fail", "不符合", "不合格",
        )) else "pass" if compact_verdict in {"pass", "符合", "合格", "通过"} else ""
    )
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
        "verdict_raw": verdict,
        "verdict": verdict_class,
    }


def _report_level_context(
    unit, report_name: str, level: str, parameters: list[dict] | None = None,
) -> str:
    """Anchor a class to its labelled report section, never to a cover token."""
    text = str(unit.native_text or "")
    identity_anchored = bool(_locate(text, report_name))
    if not identity_anchored:
        for parameter in parameters or []:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("name") or parameter.get("参数名") or "").strip()
            value = str(parameter.get("value") or parameter.get("参数值") or "").strip()
            if name and value and _locate(text, name) and _locate(text, value):
                identity_anchored = True
                break
    if not identity_anchored:
        return ""
    marker = re.search(
        r"(?i)(?:functional\s+(?:class(?:es)?|status)|performance\s+criteria|"
        r"test\s+results?|standard\s+requirements?|功能等级|性能等级|要求等级|"
        r"测试结果|标准要求)",
        text,
    )
    if not marker:
        return ""
    window_start = max(0, marker.start() - 100)
    window_end = min(len(text), marker.start() + 500)
    window = text[window_start:window_end]
    level_match = re.search(
        rf"(?i)(?<![A-Z]){re.escape(level)}(?![A-Z])", window,
    )
    if not level_match:
        return ""
    start = max(0, level_match.start() - 110)
    end = min(len(window), level_match.end() + 110)
    return window[start:end].strip()


def _filename_identity(value: str) -> tuple[str, str, str, str] | None:
    match = re.search(
        r"(?i)(E\d+).*?(\d{4})_\s*Mode\s*([12]).*?(20\d{12})(?:\.[^.]+)?$",
        str(value or ""),
    )
    if not match:
        return None
    return tuple(part.casefold() for part in match.groups())  # type: ignore[return-value]


def _compact_name(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def _number_unit_tokens(value: str) -> set[str]:
    text = str(value or "")
    tokens = {
        f"{match.group(1).replace(' ', '').replace(',', '.').casefold()}"
        f"{match.group(2).casefold()}"
        for match in _NUMBER_UNIT_RE.finditer(text)
    }
    grouped = re.compile(
        r"(?i)\(\s*([-+]?\d+(?:[.,]\d+)?)\s*±\s*"
        r"(\d+(?:[.,]\d+)?)\s*\)\s*"
        r"(mV|kV|V|mA|A|mHz|kHz|MHz|Hz|ms|s|MΩ|kΩ|Ω|%)\b",
    )
    for match in grouped.finditer(text):
        unit = match.group(3).casefold()
        tokens.add(f"{match.group(1).replace(',', '.')}{unit}")
        tokens.add(f"±{match.group(2).replace(',', '.')}{unit}")
    return tokens


def _named_standard_parameter_tokens(name: str, quote: str) -> set[str]:
    """Keep values belonging to the named parameter, not nominal context.

    A severity requirement commonly says ``... of 1 V, for UN = 12 V and
    24 V``.  The 12 V/24 V tokens describe applicability and must not be
    compared as the severity value itself.
    """
    name_key = _compact_name(name)
    compact_quote = _compact_name(quote)
    if not name_key or name_key not in compact_quote:
        return set()
    segment = quote
    name_match = re.search(re.escape(name), quote, re.IGNORECASE)
    if name_match:
        segment = quote[name_match.start():]
    segment = re.split(r"[;；\n]", segment, maxsplit=1)[0]
    segment = re.split(
        r"(?i)\bfor\s+U\s*[_ ]?N\b|用于\s*(?:额定|标称)",
        segment, maxsplit=1,
    )[0]
    return _number_unit_tokens(segment)


def _locate_parameter_source(unit, name: str, value: str) -> str:
    """Locate a complete parameter row even when Office line breaks differ."""
    direct = _locate(unit.native_text, value)
    name_quote = _locate(unit.native_text, name)
    if direct and name_quote:
        return direct
    name_pattern = r"\s*".join(
        re.escape(part) for part in re.split(r"\s+", name.strip()) if part
    )
    name_match = re.search(name_pattern, unit.native_text, re.IGNORECASE)
    if not name_match:
        return ""
    window = unit.native_text[name_match.start():name_match.start() + 500]
    expected_tokens = _number_unit_tokens(value)
    if expected_tokens and expected_tokens.issubset(_number_unit_tokens(window)):
        return window.strip()
    return ""


def _specification_source_unit(report_units: list, parameters: list[dict]):
    """Select the page containing the complete specification parameter set."""
    best = None
    best_score = 0
    for unit in report_units:
        text = unit.native_text
        score = 2 if re.search(r"(?i)TEST\s+SPECIFICATION|测试规范", text) else 0
        unit_tokens = _number_unit_tokens(text)
        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("name") or parameter.get("参数名") or "").strip()
            value = str(parameter.get("value") or parameter.get("参数值") or "").strip()
            value_tokens = _number_unit_tokens(value)
            if name and _locate(text, name):
                score += 2
            if value and _locate(text, value):
                score += 2
            elif value_tokens and value_tokens.issubset(unit_tokens):
                score += 1
        if score > best_score:
            best = unit
            best_score = score
    return best if best_score >= 3 else None


def build_standard_parameter_consistency(
    graph_id: str,
    plan_items: list[ObservedTestItem],
    releases: list[dict],
    documents: dict[str, dict],
    units: list,
    metadata_by_doc_type: dict[str, list[dict]],
) -> tuple[list[EvidenceRecord], list[GraphNode], list[ReviewFinding], int, int]:
    """Compare explicitly named report specification parameters to standards.

    Matching requires the report parameter name itself (for example
    ``Offset voltage`` or ``Severity 2``) to occur in a confirmed requirement
    from the exact plan clause.  This intentionally skips inferred synonyms.
    """
    report = _reviewed_json(metadata_by_doc_type.get("final_report", []))
    report_units = [unit for unit in units if unit.doc_type == "final_report"]
    evidence: list[EvidenceRecord] = []
    nodes: list[GraphNode] = []
    findings: list[ReviewFinding] = []
    expected = 0
    anchored = 0

    for plan_item in plan_items:
        standard_code, planned_clause = _parse_standard_reference(
            str(plan_item.parameters.get("standard_clause") or "")
        )
        if not standard_code or not planned_clause:
            continue
        report_item = next((
            item for item in report.get("item_extractions", [])
            if isinstance(item, dict) and _same_identity(
                parse_test_identity(plan_item.name),
                parse_test_identity(str(
                    item.get("test_item_name") or item.get("test_item_code") or ""
                )),
            )
        ), None)
        if not report_item:
            continue
        report_name = str(
            report_item.get("test_item_name")
            or report_item.get("test_item_code") or plan_item.name
        )
        scoped_requirements: list[tuple[dict, dict]] = []
        for release in releases:
            if release.get("status") != "published":
                continue
            snapshot = release.get("snapshot") or {}
            release_code, _ = _parse_standard_reference(
                str(snapshot.get("standard_code") or "")
            )
            if release_code != standard_code:
                continue
            for requirement in snapshot.get("requirements", []):
                if (
                    requirement.get("review_status") == "confirmed"
                    and str(requirement.get("requirement_type") or "") in {
                        "parameter_limit", "test_condition",
                    }
                    and _clause_in_scope(
                        str(requirement.get("clause_number") or ""), planned_clause,
                    )
                ):
                    scoped_requirements.append((requirement, release))

        spec_parameters = (
            report_item.get("spec_parameters", [])
            if isinstance(report_item.get("spec_parameters"), list) else []
        )
        specification_unit = _specification_source_unit(report_units, spec_parameters)
        for parameter_index, parameter in enumerate(spec_parameters):
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("name") or parameter.get("参数名") or "").strip()
            value = str(parameter.get("value") or parameter.get("参数值") or "").strip()
            name_key = _compact_name(name)
            report_tokens = _number_unit_tokens(value)
            if not name_key or not report_tokens:
                continue
            matches: list[tuple[dict, dict, set[str]]] = []
            for requirement, release in scoped_requirements:
                quote = str(
                    requirement.get("evidence_quote")
                    or requirement.get("original_statement") or ""
                ).strip()
                standard_tokens = _named_standard_parameter_tokens(name, quote)
                if name_key in _compact_name(quote) and standard_tokens:
                    matches.append((requirement, release, standard_tokens))
            if not matches:
                continue
            expected += 1
            standard_tokens = set().union(*(row[2] for row in matches))
            located = None
            if specification_unit is not None:
                quote = _locate(specification_unit.native_text, value)
                if not quote:
                    value_tokens = _number_unit_tokens(value)
                    if value_tokens.issubset(
                        _number_unit_tokens(specification_unit.native_text),
                    ):
                        quote = _locate_parameter_source(
                            specification_unit, name, value,
                        )
                if quote:
                    located = (specification_unit, quote)
            if located is None:
                located = next((
                    (unit, quote)
                    for unit in report_units
                    if (quote := _locate_parameter_source(unit, name, value))
                ), None)
            if located is None:
                logger.warning(
                    "standard_parameter_source_unanchored",
                    graph_id=graph_id, item_id=plan_item.item_id,
                    parameter_index=parameter_index,
                    parameter_name_hash=hashlib.sha256(name.encode()).hexdigest()[:12],
                )
                continue
            anchored += 1
            if report_tokens == standard_tokens:
                continue
            requirement, release, _ = matches[0]
            snapshot = release.get("snapshot") or {}
            requirement_id = str(requirement.get("id") or _id(
                "stdreq", str(requirement.get("evidence_quote") or ""),
            ))
            standard_evidence_id = _id(
                "evidence", graph_id, "standard_parameter", str(release.get("id") or ""),
                requirement_id, plan_item.item_id, name_key,
            )
            report_evidence_id = _id(
                "evidence", graph_id, "standard_parameter", plan_item.item_id,
                name_key, "final_report",
            )
            evidence_ids = [standard_evidence_id, report_evidence_id]
            evidence.append(EvidenceRecord(
                evidence_id=standard_evidence_id, graph_id=graph_id,
                doc_type="test_standard",
                filename=str(snapshot.get("standard_code") or "测试标准"),
                page_number=int(requirement.get("page_start") or 0),
                exact_quote=str(
                    requirement.get("evidence_quote")
                    or requirement.get("original_statement") or ""
                ).strip(),
                extraction_method="published_standard_parameter", confidence=1,
                metadata={
                    "release_id": release.get("id") or "",
                    "requirement_id": requirement_id,
                    "clause_number": requirement.get("clause_number") or "",
                    "role": "expected", "parameter_name": name,
                    "comparison_value": sorted(standard_tokens),
                },
            ))
            unit, report_quote = located
            evidence.append(EvidenceRecord(
                evidence_id=report_evidence_id, graph_id=graph_id,
                doc_id=str(documents.get("final_report", {}).get("doc_id") or ""),
                doc_type="final_report",
                filename=str(documents.get("final_report", {}).get("filename") or ""),
                page_number=unit.page_number, exact_quote=report_quote,
                extraction_method="report_specification_parameter", confidence=1,
                metadata={
                    "unit_id": unit.unit_id, "role": "observed",
                    "parameter_name": name, "comparison_value": value,
                },
            ))
            suffix = f"{plan_item.item_id}:{name_key}"
            node_id = _id("claim", graph_id, "standard_parameter", suffix)
            nodes.append(GraphNode(
                node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
                label=f"{plan_item.name} · {name}与标准不一致",
                canonical_key=f"standard_parameter:{suffix}",
                properties={
                    "claim_type": "standard_parameter_consistency",
                    "raw_value": f"standard={sorted(standard_tokens)};report={sorted(report_tokens)}",
                    "source_doc_type": "final_report",
                    "parameter_name": name,
                    "expected_tokens": sorted(standard_tokens),
                    "observed_tokens": sorted(report_tokens),
                    "evidence_ids": evidence_ids,
                },
            ))
            findings.append(ReviewFinding(
                finding_id=_id(
                    "finding", graph_id, "STANDARD-PARAMETER-CONSISTENCY-001", suffix,
                ), graph_id=graph_id,
                check_id="STANDARD-PARAMETER-CONSISTENCY-001",
                status=FindingStatus.CONFIRMED_ERROR,
                severity=FindingSeverity.ERROR,
                title=f"{plan_item.name}的{name}与已发布标准不一致",
                description=(
                    f"报告章节规范写为 {value}，已发布标准条款 "
                    f"{requirement.get('clause_number') or ''} 的对应要求为 "
                    f"{str(requirement.get('evidence_quote') or requirement.get('original_statement') or '').strip()}"
                ),
                subject_node_ids=[node_id], evidence_ids=evidence_ids,
                dedupe_key=f"STANDARD-PARAMETER-CONSISTENCY-001:{suffix}",
                rule_version="standard-parameter-consistency-1",
                metadata={
                    "comparison_kind": "published_standard_parameter",
                    "parameter_name": name, "report_value": value,
                    "standard_tokens": sorted(standard_tokens),
                    "report_tokens": sorted(report_tokens),
                    "clause_number": requirement.get("clause_number") or "",
                },
            ))
    return evidence, nodes, findings, expected, anchored


def build_standard_acceptance_consistency(
    graph_id: str,
    plan_items: list[ObservedTestItem],
    releases: list[dict],
    documents: dict[str, dict],
    units: list,
    metadata_by_doc_type: dict[str, list[dict]],
) -> tuple[list[EvidenceRecord], list[GraphNode], list[ReviewFinding], int, int]:
    """Compare source acceptance classes with one unambiguous published class.

    Broad clauses can legitimately contain several classes for conditional
    cases.  Those clauses are deliberately not auto-compared.  A comparison
    is made only when the selected, confirmed, published requirements in the
    exact plan clause resolve to one class.
    """
    structured = {
        doc_type: _reviewed_json(rows)
        for doc_type, rows in metadata_by_doc_type.items()
    }
    units_by_type: dict[str, list] = {}
    for unit in units:
        units_by_type.setdefault(str(unit.doc_type), []).append(unit)

    expected_by_item: dict[str, tuple[str, dict, dict]] = {}
    for item in plan_items:
        item_code, item_clause = _parse_standard_reference(
            str(item.parameters.get("standard_clause") or "")
        )
        if not item_code or not item_clause:
            continue
        candidates: list[tuple[str, dict, dict]] = []
        for release in releases:
            if release.get("status") != "published":
                continue
            snapshot = release.get("snapshot") or {}
            release_code, _ = _parse_standard_reference(
                str(snapshot.get("standard_code") or "")
            )
            if release_code != item_code:
                continue
            for requirement in snapshot.get("requirements", []):
                if (
                    requirement.get("review_status") != "confirmed"
                    or str(requirement.get("requirement_type") or "") != "acceptance"
                    or not _clause_in_scope(
                        str(requirement.get("clause_number") or ""), item_clause,
                    )
                ):
                    continue
                text = " ".join(str(requirement.get(field) or "") for field in (
                    "original_statement", "evidence_quote", "interpretation_zh",
                ))
                for level in _class_tokens(text):
                    candidates.append((level, requirement, release))
        levels = {row[0] for row in candidates}
        if len(levels) == 1:
            expected_by_item[item.item_id] = candidates[0]

    evidence: list[EvidenceRecord] = []
    nodes: list[GraphNode] = []
    findings: list[ReviewFinding] = []
    expected_source_count = 0
    anchored_source_count = 0

    def append_finding(
        *, item: ObservedTestItem, source_kind: str, observed_levels: set[str],
        source_evidence: list[EvidenceRecord], expected_level: str,
        requirement: dict, release: dict,
    ) -> None:
        nonlocal anchored_source_count
        if not source_evidence:
            logger.warning(
                "standard_acceptance_source_unanchored",
                graph_id=graph_id,
                item_id=item.item_id,
                source_kind=source_kind,
                observed_level_count=len(observed_levels),
                clause_number=requirement.get("clause_number") or "",
            )
            return
        anchored_source_count += 1
        snapshot = (release.get("snapshot") or {})
        requirement_id = str(requirement.get("id") or _id(
            "stdreq", str(requirement.get("evidence_quote") or ""),
        ))
        standard_evidence_id = _id(
            "evidence", graph_id, "standard_acceptance", str(release.get("id") or ""),
            requirement_id,
        )
        standard_quote = str(
            requirement.get("evidence_quote")
            or requirement.get("original_statement") or ""
        ).strip()
        evidence_ids = [standard_evidence_id]
        if not any(item.evidence_id == standard_evidence_id for item in evidence):
            evidence.append(EvidenceRecord(
                evidence_id=standard_evidence_id, graph_id=graph_id,
                doc_type="test_standard",
                filename=str(snapshot.get("standard_code") or "测试标准"),
                page_number=int(requirement.get("page_start") or 0),
                exact_quote=standard_quote,
                extraction_method="published_standard_acceptance", confidence=1,
                metadata={
                    "release_id": release.get("id") or "",
                    "requirement_id": requirement_id,
                    "clause_number": requirement.get("clause_number") or "",
                    "role": "expected", "comparison_value": expected_level,
                    "comparison_rows": [{
                        "doc_type": "test_standard",
                        "test_item": item.name,
                        "sample_id": "",
                        "mode": "",
                        "spec_requirement": (
                            f"已发布条款 {requirement.get('clause_number') or ''}"
                        ),
                        "required_level": expected_level,
                        "actual_level": "",
                        "verdict_raw": "标准要求",
                        "verdict": "",
                    }],
                },
            ))
        for record in source_evidence:
            evidence.append(record)
            evidence_ids.append(record.evidence_id)
        if observed_levels == {expected_level}:
            return
        suffix = f"{item.item_id}:{source_kind}"
        node_id = _id("claim", graph_id, "standard_acceptance", suffix)
        observed = sorted(observed_levels)
        nodes.append(GraphNode(
            node_id=node_id, graph_id=graph_id, node_type=NodeType.CLAIM,
            label=f"{item.name} · {source_kind}判定等级与标准不一致",
            canonical_key=f"standard_acceptance:{item.item_id}:{source_kind}",
            properties={
                "claim_type": "standard_acceptance_consistency",
                "raw_value": f"standard={expected_level};source={observed}",
                "source_doc_type": source_kind,
                "expected_level": expected_level,
                "observed_levels": observed,
                "evidence_ids": evidence_ids,
            },
        ))
        source_labels = {
            "test_plan": "试验计划",
            "final_report_specification": "检测报告章节规范",
            "final_report_results": "检测报告结果表",
            "original_records": "原始记录",
        }
        source_label = source_labels.get(source_kind, source_kind)
        findings.append(ReviewFinding(
            finding_id=_id(
                "finding", graph_id, "STANDARD-ACCEPTANCE-CONSISTENCY-001", suffix,
            ),
            graph_id=graph_id,
            check_id="STANDARD-ACCEPTANCE-CONSISTENCY-001",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title=f"{item.name}的{source_label}判定等级与已发布标准不一致",
            description=(
                f"已发布标准条款 {requirement.get('clause_number') or ''} 明确为 "
                f"{expected_level} 级，{source_label}写为 {'、'.join(observed)} 级。"
            ),
            subject_node_ids=[node_id], evidence_ids=evidence_ids,
            dedupe_key=(
                f"STANDARD-ACCEPTANCE-CONSISTENCY-001:{item.item_id}:{source_kind}"
            ),
            rule_version="standard-acceptance-consistency-1",
            metadata={
                "comparison_kind": "published_standard_acceptance",
                "expected_level": expected_level,
                "observed_levels": observed,
                "source_kind": source_kind,
                "clause_number": requirement.get("clause_number") or "",
            },
        ))

    for item in plan_items:
        expected = expected_by_item.get(item.item_id)
        if expected is None:
            continue
        expected_level, requirement, release = expected

        acceptance = str(item.parameters.get("acceptance") or "").strip()
        plan_levels = _class_tokens(acceptance)
        if plan_levels:
            expected_source_count += 1
            located = next((
                (unit, quote)
                for unit in units_by_type.get("test_plan", [])
                if (quote := _locate(unit.native_text, acceptance))
            ), None)
            records: list[EvidenceRecord] = []
            if located:
                unit, quote = located
                records.append(EvidenceRecord(
                    evidence_id=_id(
                        "evidence", graph_id, "standard_acceptance", item.item_id,
                        "test_plan",
                    ),
                    graph_id=graph_id,
                    doc_id=str(documents.get("test_plan", {}).get("doc_id") or ""),
                    doc_type="test_plan",
                    filename=str(documents.get("test_plan", {}).get("filename") or ""),
                    page_number=unit.page_number, sheet_name=unit.sheet_name,
                    cell_range=unit.cell_range, exact_quote=quote,
                    extraction_method="reviewed_plan_acceptance", confidence=1,
                    metadata={
                        "unit_id": unit.unit_id, "role": "observed",
                        "comparison_value": sorted(plan_levels),
                    },
                ))
            append_finding(
                item=item, source_kind="test_plan", observed_levels=plan_levels,
                source_evidence=records, expected_level=expected_level,
                requirement=requirement, release=release,
            )

        report_match = next((
            report_item
            for report_item in structured.get("final_report", {}).get(
                "item_extractions", []
            )
            if isinstance(report_item, dict) and _same_identity(
                parse_test_identity(item.name),
                parse_test_identity(str(
                    report_item.get("test_item_name")
                    or report_item.get("test_item_code") or ""
                )),
            )
        ), None)
        if report_match:
            report_name = str(
                report_match.get("test_item_name")
                or report_match.get("test_item_code") or item.name
            )
            declared_levels = _class_tokens(str(report_match.get("required_level") or ""))
            if declared_levels:
                expected_source_count += 1
                spec_parameters = (
                    report_match.get("spec_parameters", [])
                    if isinstance(report_match.get("spec_parameters"), list) else []
                )
                preferred_spec_unit = _specification_source_unit(
                    units_by_type.get("final_report", []), spec_parameters,
                )
                ordered_units = (
                    [preferred_spec_unit] + [
                        unit for unit in units_by_type.get("final_report", [])
                        if unit is not preferred_spec_unit
                    ] if preferred_spec_unit is not None
                    else units_by_type.get("final_report", [])
                )
                located = next((
                    (unit, quote)
                    for unit in ordered_units
                    if (quote := _report_level_context(
                        unit, report_name, next(iter(declared_levels)), spec_parameters,
                    ))
                ), None)
                records = []
                if located:
                    unit, quote = located
                    records.append(EvidenceRecord(
                        evidence_id=_id(
                            "evidence", graph_id, "standard_acceptance", item.item_id,
                            "final_report_specification",
                        ), graph_id=graph_id,
                        doc_id=str(documents.get("final_report", {}).get("doc_id") or ""),
                        doc_type="final_report",
                        filename=str(documents.get("final_report", {}).get("filename") or ""),
                        page_number=unit.page_number, exact_quote=quote,
                        extraction_method="report_specification_acceptance", confidence=1,
                        metadata={
                            "unit_id": unit.unit_id, "role": "observed",
                            "comparison_value": sorted(declared_levels),
                            "source_section": "test_specification",
                        },
                    ))
                append_finding(
                    item=item, source_kind="final_report_specification",
                    observed_levels=declared_levels, source_evidence=records,
                    expected_level=expected_level, requirement=requirement, release=release,
                )

            result_levels: set[str] = set()
            result_records: list[EvidenceRecord] = []
            for block_index, block in enumerate(
                (report_match.get("test_results") or {}).get("sample_data", [])
            ):
                if not isinstance(block, dict):
                    continue
                sample_id = str(block.get("sample_id") or "").strip()
                mode = str(block.get("mode") or "").strip()
                for row_index, row in enumerate(block.get("data_rows", [])):
                    if not isinstance(row, dict):
                        continue
                    levels = _class_tokens(str(row.get("required_level") or ""))
                    if not levels:
                        continue
                    result_levels.update(levels)
                    located = locate_execution_verdict(
                        units_by_type.get("final_report", []),
                        sample_id=sample_id, mode=mode,
                        verdict=str(row.get("verdict") or ""),
                    )
                    if located:
                        unit, quote = located
                        comparison_row = _result_comparison_row(
                            row, doc_type="final_report", item_name=report_name,
                            sample_id=sample_id, mode=mode,
                        )
                        result_records.append(EvidenceRecord(
                            evidence_id=_id(
                                "evidence", graph_id, "standard_acceptance", item.item_id,
                                "final_report_results", str(block_index), str(row_index),
                            ), graph_id=graph_id,
                            doc_id=str(documents.get("final_report", {}).get("doc_id") or ""),
                            doc_type="final_report",
                            filename=str(documents.get("final_report", {}).get("filename") or ""),
                            page_number=unit.page_number, exact_quote=quote,
                            extraction_method="report_result_acceptance", confidence=1,
                            metadata={
                                "unit_id": unit.unit_id, "role": "observed",
                                "comparison_value": sorted(levels),
                                "sample_id": sample_id, "mode": mode,
                                "comparison_rows": [comparison_row],
                            },
                        ))
            if result_levels:
                expected_source_count += 1
                append_finding(
                    item=item, source_kind="final_report_results",
                    observed_levels=result_levels, source_evidence=result_records,
                    expected_level=expected_level, requirement=requirement, release=release,
                )

        raw_levels: set[str] = set()
        raw_records: list[EvidenceRecord] = []
        for meta_index, meta in enumerate(
            structured.get("original_records", {}).get("metas", [])
        ):
            if not isinstance(meta, dict) or not _same_identity(
                parse_test_identity(item.name),
                parse_test_identity(str(
                    meta.get("test_item_name") or meta.get("test_item_code") or ""
                )),
            ):
                continue
            filename = str(meta.get("filename") or "")
            for table in (meta.get("semantic_data") or {}).get("tables", []):
                if not isinstance(table, dict) or table.get("table_family") != "test_data":
                    continue
                for row_index, row in enumerate(table.get("rows", [])):
                    if not isinstance(row, dict):
                        continue
                    levels = _class_tokens(_semantic_cell(
                        row, "required_performance_level",
                    ))
                    quote_value = str(row.get("evidence") or "").strip()
                    if not levels or not quote_value:
                        continue
                    raw_levels.update(levels)
                    located = next((
                        (unit, quote)
                        for unit in units_by_type.get("original_records", [])
                        if (
                            not filename or unit.filename == filename
                            or (
                                _filename_identity(filename) is not None
                                and _filename_identity(unit.filename)
                                == _filename_identity(filename)
                            )
                        )
                        if (quote := _locate(unit.native_text, quote_value))
                    ), None)
                    if located:
                        unit, quote = located
                        raw_comparison = {
                            "doc_type": "original_records",
                            "test_item": str(
                                meta.get("test_item_name")
                                or meta.get("test_item_code") or item.name
                            ).strip(),
                            "sample_id": "",
                            "mode": str(meta.get("test_mode") or "").strip(),
                            "injection_point": _semantic_cell(row, "injection_position"),
                            "spec_requirement": _semantic_cell(row, "test_specification"),
                            "test_duration": (
                                _semantic_cell(row, "test_duration")
                                or _semantic_cell(row, "test_time")
                            ),
                            "required_level": _semantic_cell(
                                row, "required_performance_level",
                            ),
                            "actual_level": _semantic_cell(
                                row, "actual_performance_level",
                            ),
                            "verdict_raw": _semantic_cell(row, "result"),
                            "verdict": "",
                        }
                        raw_records.append(EvidenceRecord(
                            evidence_id=_id(
                                "evidence", graph_id, "standard_acceptance", item.item_id,
                                "original_records", str(meta_index), str(row_index),
                            ), graph_id=graph_id,
                            doc_id=str(documents.get("original_records", {}).get("doc_id") or ""),
                            doc_type="original_records",
                            filename=str(documents.get("original_records", {}).get("filename") or ""),
                            page_number=unit.page_number, exact_quote=quote,
                            extraction_method="raw_result_acceptance", confidence=1,
                            metadata={
                                "unit_id": unit.unit_id, "role": "observed",
                                "comparison_value": sorted(levels),
                                "comparison_rows": [raw_comparison],
                            },
                        ))
        if raw_levels:
            expected_source_count += 1
            append_finding(
                item=item, source_kind="original_records", observed_levels=raw_levels,
                source_evidence=raw_records, expected_level=expected_level,
                requirement=requirement, release=release,
            )

    return evidence, nodes, findings, expected_source_count, anchored_source_count


def build_standard_provenance(
    graph_id: str,
    plan_items: list[ObservedTestItem],
    releases: list[dict],
) -> tuple[list[EvidenceRecord], list[GraphNode], list[GraphEdge]]:
    """Link planned tests to confirmed published clauses without expanding scope."""
    evidence: list[EvidenceRecord] = []
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    for release in releases:
        if release.get("status") != "published":
            continue
        snapshot = release.get("snapshot") or {}
        release_id = str(release.get("id") or "")
        release_code, _ = _parse_standard_reference(
            str(snapshot.get("standard_code") or "")
        )
        for requirement in snapshot.get("requirements", []):
            if requirement.get("review_status") != "confirmed":
                continue
            test_item = str(requirement.get("test_item") or "").strip()
            quote = str(
                requirement.get("evidence_quote")
                or requirement.get("original_statement")
                or ""
            ).strip()
            if not quote:
                continue
            standard_identity = parse_test_identity(test_item)
            matching_items = []
            for item in plan_items:
                item_standard_code, item_clause = _parse_standard_reference(
                    str(item.parameters.get("standard_clause") or "")
                )
                clause_match = bool(
                    release_code
                    and item_standard_code == release_code
                    and _clause_in_scope(
                        str(requirement.get("clause_number") or ""), item_clause,
                    )
                )
                item_identities = [parse_test_identity(item.name, item.parent_name)]
                item_identities.extend(
                    parse_test_identity(str(alias))
                    for alias in item.metadata.get("identity_aliases", [])
                    if str(alias).strip()
                )
                identity_match = bool(test_item) and any(
                    _same_identity(standard_identity, identity)
                    for identity in item_identities
                )
                if clause_match or identity_match:
                    matching_items.append(item)
            if not matching_items:
                continue
            requirement_id = str(requirement.get("id") or _id("stdreq", quote))
            evidence_id = _id("evidence", graph_id, release_id, requirement_id)
            node_id = _id("standard", graph_id, release_id, requirement_id)
            evidence.append(EvidenceRecord(
                evidence_id=evidence_id,
                graph_id=graph_id,
                doc_type="test_standard",
                filename=str(snapshot.get("standard_code") or "测试标准"),
                page_number=int(requirement.get("page_start") or 0),
                exact_quote=quote,
                extraction_method="published_standard_snapshot",
                confidence=1,
                metadata={
                    "release_id": release_id,
                    "requirement_id": requirement_id,
                    "clause_number": requirement.get("clause_number") or "",
                    "interpretation_zh": requirement.get("interpretation_zh") or "",
                },
            ))
            nodes.append(GraphNode(
                node_id=node_id,
                graph_id=graph_id,
                node_type=NodeType.STANDARD_CLAUSE,
                label=(
                    f"{requirement.get('clause_number') or ''} {test_item}"
                ).strip(),
                canonical_key=standard_identity.normalized_name,
                properties={
                    "release_id": release_id,
                    "requirement_id": requirement_id,
                    "requirement_type": requirement.get("requirement_type") or "",
                    "interpretation_zh": requirement.get("interpretation_zh") or "",
                    "evidence_ids": [evidence_id],
                },
            ))
            for item in matching_items:
                edges.append(GraphEdge(
                    edge_id=_id("edge", graph_id, item.item_id, node_id, "governed_by"),
                    graph_id=graph_id,
                    source_node_id=item.item_id,
                    target_node_id=node_id,
                    relation_type=RelationType.GOVERNED_BY,
                    status=RelationStatus.ACCEPTED,
                    origin=RelationOrigin.DETERMINISTIC,
                    confidence=1,
                    evidence_ids=[evidence_id],
                    rationale="计划测试项与已人工确认发布的标准条款身份一致",
                    metadata={"scope_expanded": False},
                ))
    return evidence, nodes, edges


def build_standard_plan_coverage(
    graph_id: str,
    plan_items: list[ObservedTestItem],
    releases: list[dict],
) -> tuple[list[ReviewFinding], int, int]:
    """Report plan rows whose cited standard has not been published/selected."""
    released_codes = {
        code
        for release in releases if release.get("status") == "published"
        if (code := _parse_standard_reference(
            str((release.get("snapshot") or {}).get("standard_code") or "")
        )[0])
    }
    cited: list[tuple[ObservedTestItem, str]] = []
    for item in plan_items:
        code, _ = _parse_standard_reference(
            str(item.parameters.get("standard_clause") or "")
        )
        if code:
            cited.append((item, code))
    uncovered: dict[str, list[ObservedTestItem]] = {}
    for item, code in cited:
        if code not in released_codes:
            uncovered.setdefault(code, []).append(item)
    findings: list[ReviewFinding] = []
    for code, items in uncovered.items():
        names = [item.name for item in items]
        findings.append(ReviewFinding(
            finding_id=_id("finding", graph_id, "STANDARD-PLAN-COVERAGE-001", code),
            graph_id=graph_id,
            check_id="STANDARD-PLAN-COVERAGE-001",
            status=FindingStatus.UNRESOLVED_ADVISORY,
            severity=FindingSeverity.WARNING,
            title=f"{code} 尚未进入本次已发布标准范围",
            description=(
                f"试验计划有 {len(items)} 项引用该标准，但本次没有选择并发布对应标准，"
                "因此这些项目的参数、方法和判定要求尚未完成标准符合性检查。"
            ),
            subject_node_ids=[item.item_id for item in items],
            evidence_ids=list(dict.fromkeys(
                evidence_id for item in items for evidence_id in item.evidence_ids
            )),
            dedupe_key=f"STANDARD-PLAN-COVERAGE-001:{code}",
            rule_version="standard-plan-coverage-1",
            metadata={"standard_code": code, "test_items": names},
        ))
    return findings, len(cited), len(cited) - sum(len(items) for items in uncovered.values())


def build_standard_scope_advisories(
    graph_id: str,
    plan_items: list[ObservedTestItem],
    releases: list[dict],
) -> tuple[list[EvidenceRecord], list[GraphNode], list[ReviewFinding]]:
    plan_identities = [parse_test_identity(item.name, item.parent_name) for item in plan_items]
    evidence: list[EvidenceRecord] = []
    nodes: list[GraphNode] = []
    findings: list[ReviewFinding] = []
    for release in releases:
        if release.get("status") != "published":
            continue
        snapshot = release.get("snapshot") or {}
        for requirement in snapshot.get("requirements", []):
            if requirement.get("review_status") != "confirmed":
                continue
            test_item = str(requirement.get("test_item") or "").strip()
            statement = str(requirement.get("interpretation_zh") or requirement.get("statement") or "").strip()
            quote = str(requirement.get("evidence_quote") or requirement.get("original_statement") or "").strip()
            if not test_item or not quote or not _MANDATORY.search(statement):
                continue
            identity = parse_test_identity(test_item)
            present = any(
                identity.normalized_name == item.normalized_name
                or (identity.item_code and identity.item_code == item.item_code)
                or (identity.subitem_code and identity.subitem_code == item.subitem_code
                    and identity.family_key == item.family_key)
                for item in plan_identities
            )
            if present:
                continue
            requirement_id = str(requirement.get("id") or _id("stdreq", quote))
            evidence_id = _id("evidence", graph_id, str(release.get("id")), requirement_id)
            node_id = _id("standard", graph_id, requirement_id)
            evidence.append(EvidenceRecord(
                evidence_id=evidence_id, graph_id=graph_id, doc_type="test_standard",
                filename=str(snapshot.get("standard_code") or "测试标准"),
                page_number=int(requirement.get("page_start") or 0), exact_quote=quote,
                extraction_method="published_standard_snapshot", confidence=1,
                metadata={"release_id": release.get("id"), "requirement_id": requirement_id},
            ))
            nodes.append(GraphNode(
                node_id=node_id, graph_id=graph_id, node_type=NodeType.STANDARD_CLAUSE,
                label=test_item, canonical_key=identity.normalized_name,
                properties={"release_id": release.get("id"), "evidence_ids": [evidence_id]},
            ))
            findings.append(ReviewFinding(
                finding_id=_id("finding", graph_id, "STANDARD-SCOPE-ADVISORY-001", requirement_id),
                graph_id=graph_id, check_id="STANDARD-SCOPE-ADVISORY-001",
                status=FindingStatus.CONFIRMED_ADVISORY, severity=FindingSeverity.WARNING,
                title=f"测试计划未列出标准明确要求的 {test_item}",
                description="仅作为范围提醒，不自动增加本次必做项目，也不判定未执行。",
                subject_node_ids=[node_id], evidence_ids=[evidence_id],
                dedupe_key=f"STANDARD-SCOPE-ADVISORY-001:{requirement_id}",
                rule_version="standard-advisory-1",
            ))
    return evidence, nodes, findings
