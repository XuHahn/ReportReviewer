"""Bridge quality-gated document extraction into general graph checks.

The unified pipeline can skip generic per-page LLM extraction when a dedicated
extractor has already completed.  General checks still need source-backed
observations in that case; otherwise a fast path silently disables rules.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from typing import Any

from services.evidence_graph_extraction import (
    EvidenceGraphDocumentUnit,
    MaterializedObservation,
    UnifiedExtractionResult,
)
from services.evidence_graph_models import EvidenceRecord, EvidenceState
from services.evidence_graph_source_locator import locate_execution_verdict
from services.test_item_aliases import alias_key_for, normalize_alias_text


def _id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _test_item_identity(value: str) -> str:
    return alias_key_for(value) or normalize_alias_text(value)


def _physical_instrument_fields(
    name: str, model: str, serial: str, calibration: str,
) -> bool:
    """Calibration/identity rules apply to metrology hardware, not software rows."""
    placeholders = {"", "/", "-", "n/a", "na", "不适用"}
    return bool(
        name.strip()
        and model.strip().casefold() not in placeholders
        and serial.strip().casefold() not in placeholders
        and re.search(r"20\d{2}\D+\d{1,2}\D+\d{1,2}", calibration)
    )


def _positive_anomaly_text(value: str) -> bool:
    text = str(value or "").casefold()
    text = re.sub(
        r"(?:无异常|未见异常|工作正常|运行正常|转动正常|恢复正常|"
        r"no\s+abnormal(?:ity)?|operat(?:e|ed|ing)\s+normally|"
        r"no\s+malfunction|smoothly)",
        " ", text, flags=re.IGNORECASE,
    )
    return bool(re.search(
        r"(?:异常|失效|损坏|冒烟|烧毁|停止|啸叫|"
        r"\babnormal\b|\bmalfunction\b|\bdamag(?:e|ed)\b|"
        r"\bsmok(?:e|ed|ing)\b|\bstopp?ed\b|unusual\s+noise)",
        text, re.IGNORECASE,
    ))


def _trim_extracted_remarks(value: str) -> str:
    """Remove a following physical-page header leaked by legacy extraction."""
    return re.split(
        r"(?=GRGJL\.WI|第\s*\d+\s*页\s*/\s*共|[☑☒■□]\s*测试照片|测试结论\s*[:：])",
        str(value or "").strip(), maxsplit=1,
    )[0].strip()


def _raw_required_performance_level(meta: dict) -> str:
    semantic = meta.get("semantic_data") or {}
    for table in semantic.get("tables", []) if isinstance(semantic, dict) else []:
        if not isinstance(table, dict) or table.get("table_family") != "test_data":
            continue
        for row in table.get("rows", []) if isinstance(table.get("rows"), list) else []:
            for cell in row.get("cells", []) if isinstance(row, dict) else []:
                if not isinstance(cell, dict):
                    continue
                if cell.get("semantic") == "required_performance_level":
                    match = re.search(
                        r"(?i)(?<![A-Z])([A-E])(?![A-Z])",
                        str(cell.get("normalized_value") or cell.get("source_value") or ""),
                    )
                    if match:
                        return match.group(1).upper()
    return ""


def _expected_functional_interruption(
    remarks: str, required_level: str,
) -> bool:
    """Class C/D can explicitly permit temporary loss of function."""
    if required_level not in {"C", "D"}:
        return False
    if re.search(
        r"(?:损坏|冒烟|烧毁|啸叫|异常噪声|"
        r"\bdamag(?:e|ed)\b|\bsmok(?:e|ed|ing)\b|"
        r"\bburn(?:ed|t|ing)?\b|unusual\s+noise)",
        remarks, re.IGNORECASE,
    ):
        return False
    has_interruption = bool(re.search(
        r"(?:停止|变慢|失效|stopp?ed|slowed|loss\s+of\s+function)",
        remarks, re.IGNORECASE,
    ))
    has_recovery = bool(re.search(
        r"(?:恢复|试验后.*正常|after\s+(?:the\s+)?test.*normal)",
        remarks, re.IGNORECASE,
    ))
    return has_interruption and has_recovery


def _payload(rows: list[dict]) -> dict:
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


def _compact(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", str(value or "")).casefold()
        if not char.isspace()
    )


def _filename_identity(value: str) -> tuple[str, str, str, str] | None:
    match = re.search(
        r"(?i)(E\d+).*?(\d{4})_\s*Mode\s*([12]).*?(20\d{12})(?:\.[^.]+)?$",
        str(value or ""),
    )
    if not match:
        return None
    return tuple(part.casefold() for part in match.groups())  # type: ignore[return-value]


def _locate(source: str, value: str) -> str:
    if value and value in source:
        return value
    compact_source: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(source):
        for normalized in unicodedata.normalize("NFKC", char).casefold():
            if normalized.isspace():
                continue
            compact_source.append(normalized)
            positions.append(index)
    needle = _compact(value)
    if not needle:
        return ""
    offset = "".join(compact_source).find(needle)
    if offset < 0:
        return ""
    return source[positions[offset]:positions[offset + len(needle) - 1] + 1]


def _source_unit(
    units: list[EvidenceGraphDocumentUnit],
    *,
    doc_type: str,
    filename: str = "",
    required_values: list[str] | None = None,
) -> EvidenceGraphDocumentUnit | None:
    candidates = [unit for unit in units if unit.doc_type == doc_type]
    values = [value for value in (required_values or []) if value]
    if filename:
        filename_identity = _filename_identity(filename)
        exact = [
            unit for unit in candidates
            if unit.filename == filename or (
                filename_identity is not None
                and _filename_identity(unit.filename) == filename_identity
            )
        ]
        located = next((
            unit for unit in exact
            if all(_locate(unit.native_text, value) for value in values)
        ), None)
        if located:
            return located
        if exact and not values:
            return exact[0]
    return next((
        unit for unit in candidates
        if all(_locate(unit.native_text, value) for value in values)
    ), None)


def _append_observation(
    *,
    graph_id: str,
    unit: EvidenceGraphDocumentUnit,
    observations: list[MaterializedObservation],
    evidence: list[EvidenceRecord],
    observation_type: str,
    entity_name: str,
    field_name: str,
    raw_value: Any,
    source_value: str,
    metadata: dict[str, Any] | None = None,
    unit_value: str = "",
    identity_key: str = "",
) -> bool:
    quote = _locate(unit.native_text, source_value)
    if not quote:
        return False
    evidence_id = _id(
        "evidence", graph_id, unit.doc_id, unit.unit_id,
        "structured_check_bridge", observation_type, entity_name,
        field_name, str(raw_value), identity_key,
    )
    observation_id = _id(
        "observation", graph_id, unit.unit_id, observation_type,
        entity_name, field_name, str(raw_value), identity_key,
    )
    if any(item.evidence_id == evidence_id for item in evidence):
        return True
    item_metadata = {
        "doc_type": unit.doc_type,
        "doc_id": unit.doc_id,
        "unit_id": unit.unit_id,
        "structured_authority": True,
        **(metadata or {}),
    }
    evidence.append(EvidenceRecord(
        evidence_id=evidence_id,
        graph_id=graph_id,
        doc_id=unit.doc_id,
        doc_type=unit.doc_type,
        filename=unit.filename,
        state=EvidenceState.FOUND,
        page_number=unit.page_number,
        sheet_name=unit.sheet_name,
        cell_range=unit.cell_range,
        exact_quote=quote,
        extraction_method="structured_check_bridge",
        confidence=1,
        metadata={"unit_id": unit.unit_id, "field_name": field_name},
    ))
    observations.append(MaterializedObservation(
        observation_id=observation_id,
        observation_type=observation_type,
        entity_name=entity_name,
        field_name=field_name,
        raw_value=raw_value,
        normalized_value=raw_value,
        unit=unit_value,
        evidence_ids=[evidence_id],
        extraction_sources=["structured_check_bridge"],
        confidence=1,
        metadata=item_metadata,
    ))
    return True


def _raw_record_results(
    graph_id: str,
    units: list[EvidenceGraphDocumentUnit],
    structured: dict,
) -> list[UnifiedExtractionResult]:
    observations_by_unit: dict[str, list[MaterializedObservation]] = defaultdict(list)
    evidence_by_unit: dict[str, list[EvidenceRecord]] = defaultdict(list)
    errors_by_unit: dict[str, list[str]] = defaultdict(list)
    meta_by_filename = {
        str(meta.get("filename") or ""): meta
        for meta in structured.get("metas", [])
        if isinstance(meta, dict)
    }
    dated_units: set[str] = set()
    expected_instrument_profiles = 0
    anchored_instrument_profiles = 0
    anchored_profile_sources: list[str] = []
    expected_anomaly_notes = 0
    anchored_anomaly_notes = 0

    for meta_index, meta in enumerate(structured.get("metas", [])):
        if not isinstance(meta, dict):
            continue
        header = meta.get("_header_fields", {})
        header = header if isinstance(header, dict) else {}
        remarks = _trim_extracted_remarks(
            str(header.get("remarks") or meta.get("remarks") or ""),
        )
        conclusion = str(header.get("test_conclusion") or "").strip()
        if not remarks:
            continue
        expected_anomaly_notes += 1
        source_file = str(meta.get("filename") or "")
        unit = _source_unit(
            units, doc_type="original_records", filename=source_file,
            required_values=[remarks, conclusion] if conclusion else [remarks],
        )
        if unit is None:
            continue
        anchored_anomaly_notes += 1
        required_level = _raw_required_performance_level(meta)
        if (
            not _positive_anomaly_text(remarks)
            or _expected_functional_interruption(remarks, required_level)
        ):
            continue
        item_name = str(
            meta.get("test_item_name") or header.get("test_item_pdf") or "试验执行"
        ).strip()
        execution_name = " · ".join(filter(None, (
            item_name,
            str(header.get("sample_id") or "").strip(),
            str(meta.get("test_mode") or header.get("test_mode") or "").strip(),
        )))
        _append_observation(
            graph_id=graph_id, unit=unit,
            observations=observations_by_unit[unit.unit_id],
            evidence=evidence_by_unit[unit.unit_id],
            observation_type="anomaly", entity_name=execution_name,
            field_name="execution_anomaly", raw_value=remarks,
            source_value=remarks,
            metadata={
                "prohibited_by_requirement": required_level == "A",
                "required_performance_level": required_level,
            },
            identity_key=f"raw-anomaly:{meta_index}",
        )
        if conclusion:
            _append_observation(
                graph_id=graph_id, unit=unit,
                observations=observations_by_unit[unit.unit_id],
                evidence=evidence_by_unit[unit.unit_id],
                observation_type="result", entity_name=execution_name,
                field_name="execution_conclusion", raw_value=conclusion,
                source_value=conclusion,
                identity_key=f"raw-anomaly-conclusion:{meta_index}",
            )

    for instrument in structured.get("instruments", []):
        if not isinstance(instrument, dict):
            continue
        name = str(instrument.get("name") or "").strip()
        model = str(instrument.get("model") or "").strip()
        serial = str(instrument.get("serial_no") or "").strip()
        calibration = str(instrument.get("calibration_end") or "").strip()
        source_file = str(instrument.get("source_file") or "").strip()
        if not _physical_instrument_fields(name, model, serial, calibration):
            continue
        expected_instrument_profiles += 1
        if not all((name, model, serial, calibration)):
            continue
        unit = _source_unit(
            units, doc_type="original_records", filename=source_file,
            required_values=[serial, calibration],
        )
        if unit is None:
            errors_by_unit[
                f"structured:original_records:{source_file or 'unknown'}"
            ].append(
                "仪器记录无法回锚到原始文件",
            )
            continue
        observations = observations_by_unit[unit.unit_id]
        evidence = evidence_by_unit[unit.unit_id]
        common = {
            "parent_name": name,
            "execution_group": source_file,
            "calibration_required": True,
            "field_region_verified": True,
            "field_region_verification_source": "system",
        }
        fields = (
            ("instrument_name", name),
            ("instrument_model", model),
            ("serial_number", serial),
            ("calibration_end", calibration),
        )
        profile_complete = True
        for field_name, value in fields:
            if not _append_observation(
                graph_id=graph_id, unit=unit, observations=observations,
                evidence=evidence, observation_type="instrument",
                entity_name=name, field_name=field_name, raw_value=value,
                source_value=value, metadata=dict(common),
            ):
                profile_complete = False
                errors_by_unit[unit.unit_id].append(f"{name}.{field_name}无法回锚")
        if profile_complete:
            anchored_instrument_profiles += 1
            anchored_profile_sources.append(source_file)

        if source_file in dated_units:
            continue
        meta = meta_by_filename.get(source_file, {})
        header = meta.get("_header_fields", {}) if isinstance(meta, dict) else {}
        test_date = str(
            header.get("test_date") or meta.get("test_date") or ""
        ).strip()
        test_item = str(
            meta.get("test_item_name") or header.get("test_item_pdf") or "试验执行"
        ).strip()
        date_unit = _source_unit(
            units, doc_type="original_records", filename=source_file,
            required_values=[test_date],
        ) if test_date else None
        if date_unit and _append_observation(
            graph_id=graph_id, unit=date_unit,
            observations=observations_by_unit[date_unit.unit_id],
            evidence=evidence_by_unit[date_unit.unit_id],
            observation_type="date", entity_name=test_item,
            field_name="test_date", raw_value=test_date,
            source_value=test_date, metadata={"execution_group": source_file},
        ):
            dated_units.add(source_file)
        else:
            errors_by_unit[unit.unit_id].append("试验日期无法回锚")

    calibrated_profile_count = sum(
        source_file in dated_units for source_file in anchored_profile_sources
    )
    coverage = {
        "INSTRUMENT-IDENTITY-001": {
            "state": (
                "system_incomplete"
                if anchored_instrument_profiles < expected_instrument_profiles else "complete"
            ),
            "applicability": (
                "applicable" if expected_instrument_profiles else "not_applicable"
            ),
            "expected_input_count": expected_instrument_profiles,
            "anchored_input_count": anchored_instrument_profiles,
            "reason_code": (
                "raw_instrument_profiles_not_fully_anchored"
                if anchored_instrument_profiles < expected_instrument_profiles
                else "all_raw_instrument_profiles_anchored"
            ),
            "document_types": ["original_records"],
        },
        "INSTRUMENT-CAL-001": {
            "state": (
                "system_incomplete"
                if calibrated_profile_count < expected_instrument_profiles else "complete"
            ),
            "applicability": (
                "applicable" if expected_instrument_profiles else "not_applicable"
            ),
            "expected_input_count": expected_instrument_profiles,
            "anchored_input_count": calibrated_profile_count,
            "reason_code": (
                "instrument_or_execution_date_not_fully_anchored"
                if calibrated_profile_count < expected_instrument_profiles
                else "all_calibration_dates_checked_against_execution_dates"
            ),
            "document_types": ["original_records"],
        },
        "ANOMALY-CONCLUSION-001": {
            "state": (
                "system_incomplete"
                if anchored_anomaly_notes < expected_anomaly_notes else "complete"
            ),
            "applicability": (
                "applicable" if expected_anomaly_notes else "not_applicable"
            ),
            "expected_input_count": expected_anomaly_notes,
            "anchored_input_count": anchored_anomaly_notes,
            "reason_code": (
                "raw_execution_notes_not_fully_anchored"
                if anchored_anomaly_notes < expected_anomaly_notes else
                "raw_records_have_no_execution_notes"
                if not expected_anomaly_notes else
                "all_raw_execution_notes_checked_for_anomalies"
            ),
            "document_types": ["original_records"],
        },
    }
    unit_ids = sorted(set(observations_by_unit) | set(errors_by_unit))
    if not unit_ids:
        unit_ids = ["structured:original_records:audit"]
    results: list[UnifiedExtractionResult] = []
    for index, unit_id in enumerate(unit_ids):
        observations = observations_by_unit.get(unit_id, [])
        errors = errors_by_unit.get(unit_id, [])
        results.append(UnifiedExtractionResult(
            unit_id=unit_id,
            status="complete" if not errors else "needs_review",
            observations=observations,
            evidence=evidence_by_unit[unit_id],
            channels_used=["structured_check_bridge"],
            errors=errors,
            audit_metadata={"check_coverage": coverage} if index == 0 else {},
        ))
    return results


_NUMERIC_FIELDS = {
    "reading": "reading",
    "correction_db": "correction_db",
    "result_dbua": "result_dbua",
    "limit_dbua": "limit_dbua",
    "margin_db": "margin_db",
}


def _verdict_token(quote: str, verdict_class: str) -> str:
    if verdict_class == "fail":
        pattern = re.compile(r"(?:不\s*Pass|\bFail\b|不符合|不合格)", re.IGNORECASE)
    else:
        pattern = re.compile(
            r"(?:\bPass\b|(?<!不)符合|(?<!不)合格|通过)",
            re.IGNORECASE,
        )
    matches = list(pattern.finditer(quote))
    return matches[-1].group(0) if matches else ""


def _recover_unambiguous_execution_verdict(
    units: list[EvidenceGraphDocumentUnit],
    *,
    sample_id: str,
    mode: str,
) -> tuple[EvidenceGraphDocumentUnit, str] | None:
    """Recover a missing verdict only from one sample/mode source window.

    A positive and negative verdict in the same execution remains ambiguous;
    recovery must never hide a genuine report-internal conflict.
    """
    located: list[tuple[str, EvidenceGraphDocumentUnit, str]] = []
    for verdict_class, probe in (("pass", "Pass"), ("fail", "不Pass")):
        match = locate_execution_verdict(
            units, sample_id=sample_id, mode=mode, verdict=probe,
        )
        if match is None:
            continue
        unit, quote = match
        token = _verdict_token(quote, verdict_class)
        if token:
            located.append((verdict_class, unit, token))
    if len({item[0] for item in located}) != 1:
        return None
    _, unit, token = located[0]
    return unit, token


def _report_results(
    graph_id: str,
    units: list[EvidenceGraphDocumentUnit],
    structured: dict,
) -> list[UnifiedExtractionResult]:
    observations_by_unit: dict[str, list[MaterializedObservation]] = defaultdict(list)
    evidence_by_unit: dict[str, list[EvidenceRecord]] = defaultdict(list)
    errors_by_unit: dict[str, list[str]] = defaultdict(list)
    report_units = [unit for unit in units if unit.doc_type == "final_report"]
    expected_verdict_rows = 0
    anchored_verdict_rows = 0
    expected_summary_rows = 0
    anchored_summary_rows = 0
    expected_numeric_cells = 0
    anchored_numeric_cells = 0
    expected_sections = 0
    anchored_sections = 0
    declared_page_inputs = 0
    anchored_page_inputs = 0
    expected_report_instruments = 0
    anchored_report_instruments = 0
    report_instrument_groups: list[str] = []
    dated_report_groups: set[str] = set()

    section_pattern = re.compile(
        r"(?m)^\s*(\d+\.\d+(?:\.\d+){0,4})[\t ]+"
        r"([^\n]{2,180}?[A-Za-z\u4e00-\u9fff][^\n]*)\s*$",
    )
    unit_like_section_title = re.compile(
        r"(?i)^(?:Hz|kHz|MHz|V|mV|A|mA|s|ms|cycles?)\b",
    )
    page_pattern = re.compile(r"(?i)Page\s+\d+\s+of\s+(\d+)")
    actual_page_count = max((unit.page_number for unit in report_units), default=0)
    page_declarations: list[tuple[EvidenceGraphDocumentUnit, str, int]] = []
    for unit in report_units:
        for match_index, match in enumerate(section_pattern.finditer(unit.native_text)):
            expected_sections += 1
            number, title = match.group(1).strip(), match.group(2).strip()
            if unit_like_section_title.search(title):
                expected_sections -= 1
                continue
            if _append_observation(
                graph_id=graph_id, unit=unit,
                observations=observations_by_unit[unit.unit_id],
                evidence=evidence_by_unit[unit.unit_id],
                observation_type="document_field", entity_name="检测报告章节",
                field_name="section_number", raw_value=number,
                source_value=match.group(0),
                metadata={"section_title": title},
                identity_key=f"section:{unit.unit_id}:{match_index}",
            ):
                anchored_sections += 1
        for match in page_pattern.finditer(unit.native_text):
            page_declarations.append((unit, match.group(0), int(match.group(1))))
    if page_declarations:
        declared_page_inputs = 1
        unit, quote, declared_count = max(page_declarations, key=lambda item: item[2])
        if _append_observation(
            graph_id=graph_id, unit=unit,
            observations=observations_by_unit[unit.unit_id],
            evidence=evidence_by_unit[unit.unit_id],
            observation_type="document_field", entity_name="检测报告页数",
            field_name="declared_page_count", raw_value=declared_count,
            source_value=quote,
            metadata={
                "declared_count": declared_count,
                "observed_count": actual_page_count,
                "count_scope": "检测报告总页数",
                "coverage_complete": True,
                "coverage_source": "system",
            },
            identity_key="declared_page_count",
        ):
            anchored_page_inputs = 1

    report_items = [
        item for item in structured.get("item_extractions", [])
        if isinstance(item, dict)
    ]
    for item_index, item in enumerate(report_items):
        item_name = str(item.get("test_item_name") or "").strip()
        for block_index, block in enumerate(
            (item.get("test_results") or {}).get("sample_data", []),
        ):
            if not isinstance(block, dict):
                continue
            sample_id = str(block.get("sample_id") or "").strip()
            mode = str(block.get("mode") or "").strip()
            date_pattern = re.compile(
                r"(?i)(?:Test\s*Date|测试日期|试验日期)\s*[:：]?\s*"
                r"(20\d{2}[-/.年]\s*\d{1,2}[-/.月]\s*\d{1,2}日?)",
            )
            # The sample overview can list every sample ID and mode on an
            # early page.  It is not the execution source and contains no test
            # date.  Require all three facts in the same page so calibration
            # coverage cannot silently attach to the overview.
            located_date = next((
                (candidate, match)
                for candidate in report_units
                if (not sample_id or _locate(candidate.native_text, sample_id))
                and (not mode or _locate(candidate.native_text, mode))
                if (match := date_pattern.search(candidate.native_text))
            ), None)
            if located_date is None:
                continue
            unit, date_match = located_date
            test_date = date_match.group(1).strip()
            if _append_observation(
                graph_id=graph_id, unit=unit,
                observations=observations_by_unit[unit.unit_id],
                evidence=evidence_by_unit[unit.unit_id],
                observation_type="date", entity_name=item_name or "试验执行",
                field_name="test_date", raw_value=test_date,
                source_value=test_date,
                metadata={"execution_group": item_name},
                identity_key=f"report-date:{item_index}:{block_index}",
            ):
                dated_report_groups.add(_test_item_identity(item_name))

    for instrument_index, instrument in enumerate(structured.get("instruments", [])):
        if not isinstance(instrument, dict):
            continue
        name = str(instrument.get("name") or "").strip()
        model = str(instrument.get("model") or "").strip()
        serial = str(instrument.get("serial_no") or "").strip()
        calibration = str(instrument.get("calibration_end") or "").strip()
        item_name = str(instrument.get("test_item") or "").strip()
        if not _physical_instrument_fields(name, model, serial, calibration):
            continue
        expected_report_instruments += 1
        if not all((name, model, serial, calibration)):
            errors_by_unit["structured:final_report:instruments"].append(
                f"仪器字段不完整:{instrument_index}",
            )
            continue
        unit = _source_unit(
            report_units, doc_type="final_report", required_values=[serial, calibration],
        )
        if unit is None:
            errors_by_unit["structured:final_report:instruments"].append(
                f"仪器无法回锚:{instrument_index}",
            )
            continue
        common = {
            "parent_name": name,
            "execution_group": item_name,
            "calibration_required": True,
            "field_region_verified": True,
            "field_region_verification_source": "system",
        }
        profile_complete = True
        for field_name, value in (
            ("instrument_name", name), ("instrument_model", model),
            ("serial_number", serial), ("calibration_end", calibration),
        ):
            if not _append_observation(
                graph_id=graph_id, unit=unit,
                observations=observations_by_unit[unit.unit_id],
                evidence=evidence_by_unit[unit.unit_id],
                observation_type="instrument", entity_name=name,
                field_name=field_name, raw_value=value, source_value=value,
                metadata=common,
                identity_key=f"report-instrument:{instrument_index}:{field_name}",
            ):
                profile_complete = False
                errors_by_unit[unit.unit_id].append(
                    f"仪器字段无法回锚:{instrument_index}:{field_name}",
                )
        if profile_complete:
            anchored_report_instruments += 1
            report_instrument_groups.append(_test_item_identity(item_name))

    for item_index, item in enumerate(report_items):
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("test_item_name") or "").strip()
        for block_index, block in enumerate(
            (item.get("test_results") or {}).get("sample_data", []),
        ):
            if not isinstance(block, dict):
                continue
            sample_id = str(block.get("sample_id") or "").strip()
            mode = str(block.get("mode") or "").strip()
            for row_index, row in enumerate(block.get("data_rows", [])):
                if not isinstance(row, dict):
                    continue
                expected_verdict_rows += 1
                verdict = str(row.get("verdict") or "").strip()
                recovered_verdict = False
                if verdict:
                    located_verdict = locate_execution_verdict(
                        report_units,
                        sample_id=sample_id,
                        mode=mode,
                        verdict=verdict,
                    )
                else:
                    located_verdict = _recover_unambiguous_execution_verdict(
                        report_units,
                        sample_id=sample_id,
                        mode=mode,
                    )
                    if located_verdict is not None:
                        _, verdict = located_verdict
                        recovered_verdict = True
                if located_verdict is None:
                    errors_by_unit[f"structured:final_report:{item_index}"].append(
                        f"结果行无法回锚:{item_index}:{block_index}:{row_index}",
                    )
                    continue
                unit, _ = located_verdict
                observations = observations_by_unit[unit.unit_id]
                evidence = evidence_by_unit[unit.unit_id]
                row_id = f"report:{item_index}:{block_index}:{row_index}"
                if verdict:
                    if _append_observation(
                        graph_id=graph_id, unit=unit, observations=observations,
                        evidence=evidence, observation_type="result",
                        entity_name=item_name, field_name="detail_result",
                        raw_value=verdict, source_value=verdict,
                        metadata={
                            "sample_id": sample_id, "mode": mode, "row_id": row_id,
                            "conclusion_group": f"report:{item_index}",
                            "conclusion_scope": "individual",
                            "verdict_recovered": recovered_verdict,
                            "verdict_recovery_source": (
                                "system_execution_window" if recovered_verdict else ""
                            ),
                        },
                        identity_key=row_id,
                    ):
                        anchored_verdict_rows += 1
                    else:
                        errors_by_unit[unit.unit_id].append(
                            f"结果判定无法回锚:{item_index}:{block_index}:{row_index}",
                        )
                for source_key, field_name in _NUMERIC_FIELDS.items():
                    value = str(row.get(source_key) or "").strip()
                    if not value:
                        continue
                    expected_numeric_cells += 1
                    if _append_observation(
                        graph_id=graph_id, unit=unit, observations=observations,
                        evidence=evidence, observation_type="result",
                        entity_name=item_name, field_name=field_name,
                        raw_value=value, source_value=value,
                        metadata={"sample_id": sample_id, "mode": mode, "row_id": row_id},
                        identity_key=f"{row_id}:{field_name}",
                    ):
                        anchored_numeric_cells += 1
                    else:
                        errors_by_unit[unit.unit_id].append(
                            f"数值结果无法回锚:{item_index}:{block_index}:{row_index}:{field_name}",
                        )

    for item_index, summary in enumerate(structured.get("results", [])):
        if not isinstance(summary, dict):
            continue
        expected_summary_rows += 1
        item_name = str(summary.get("test_item") or "").strip()
        verdict = str(summary.get("result") or "").strip()
        unit = _source_unit(
            units, doc_type="final_report", required_values=[item_name, verdict],
        )
        if unit is None or not verdict:
            errors_by_unit[f"structured:final_report:summary:{item_index}"].append(
                f"汇总结论无法回锚:{item_index}",
            )
            continue
        if _append_observation(
            graph_id=graph_id, unit=unit,
            observations=observations_by_unit[unit.unit_id],
            evidence=evidence_by_unit[unit.unit_id],
            observation_type="result", entity_name=item_name,
            field_name="summary_conclusion", raw_value=verdict,
            source_value=verdict,
            metadata={
                "conclusion_group": f"report:{item_index}",
                "conclusion_scope": "overall",
            },
            identity_key=f"summary:{item_index}",
        ):
            anchored_summary_rows += 1
        else:
            errors_by_unit[unit.unit_id].append(f"汇总结论无法回锚:{item_index}")

    calibrated_report_profiles = sum(
        group in dated_report_groups for group in report_instrument_groups
    )
    extraction_metrics = structured.get("extraction_metrics") or {}
    failed_passes = structured.get("failed_passes") or []
    result_inventory_incomplete = bool(
        int(extraction_metrics.get("missing_sample_count") or 0) > 0
        or any(
            str(item).startswith("item/sample_coverage_missing")
            for item in failed_passes if item
        )
    )
    result_expected_inputs = expected_summary_rows
    result_anchored_inputs = anchored_summary_rows
    if result_inventory_incomplete:
        result_expected_inputs += max(
            expected_verdict_rows,
            int(extraction_metrics.get("source_sample_count") or 0),
        )
        result_anchored_inputs += anchored_verdict_rows
    coverage = {
        "DOC-STRUCTURE-001": {
            "state": (
                "system_incomplete" if anchored_sections < expected_sections else "complete"
            ),
            "applicability": "applicable" if expected_sections else "not_applicable",
            "expected_input_count": expected_sections,
            "anchored_input_count": anchored_sections,
            "reason_code": (
                "report_sections_not_fully_anchored"
                if anchored_sections < expected_sections else
                "report_has_no_numbered_sections" if not expected_sections else
                "all_numbered_sections_anchored"
            ),
            "document_types": ["final_report"],
        },
        "DOC-STRUCTURE-002": {
            "state": (
                "system_incomplete"
                if anchored_page_inputs < declared_page_inputs else "complete"
            ),
            "applicability": (
                "applicable" if declared_page_inputs else "not_applicable"
            ),
            "expected_input_count": declared_page_inputs,
            "anchored_input_count": anchored_page_inputs,
            "reason_code": (
                "declared_page_count_not_anchored"
                if anchored_page_inputs < declared_page_inputs else
                "report_has_no_declared_total_page_count" if not declared_page_inputs else
                "declared_page_count_checked"
            ),
            "document_types": ["final_report"],
        },
        "INSTRUMENT-IDENTITY-001": {
            "state": (
                "system_incomplete"
                if anchored_report_instruments < expected_report_instruments else "complete"
            ),
            "applicability": (
                "applicable" if expected_report_instruments else "not_applicable"
            ),
            "expected_input_count": expected_report_instruments,
            "anchored_input_count": anchored_report_instruments,
            "reason_code": (
                "report_instrument_profiles_not_fully_anchored"
                if anchored_report_instruments < expected_report_instruments else
                "report_has_no_instrument_inventory" if not expected_report_instruments else
                "all_report_instrument_profiles_anchored"
            ),
            "document_types": ["final_report"],
        },
        "INSTRUMENT-CAL-001": {
            "state": (
                "system_incomplete"
                if calibrated_report_profiles < expected_report_instruments else "complete"
            ),
            "applicability": (
                "applicable" if expected_report_instruments else "not_applicable"
            ),
            "expected_input_count": expected_report_instruments,
            "anchored_input_count": calibrated_report_profiles,
            "reason_code": (
                "report_instrument_or_execution_date_not_fully_anchored"
                if calibrated_report_profiles < expected_report_instruments else
                "report_has_no_instrument_inventory" if not expected_report_instruments else
                "all_report_calibration_dates_checked"
            ),
            "document_types": ["final_report"],
        },
        "RESULT-TYPE-001": {
            "state": (
                "system_incomplete" if expected_numeric_cells else "complete"
            ),
            "applicability": (
                "applicable" if expected_numeric_cells else "not_applicable"
            ),
            "expected_input_count": expected_numeric_cells,
            "anchored_input_count": 0,
            "reason_code": (
                "numeric_result_units_not_mapped"
                if expected_numeric_cells else "report_uses_non_numeric_result_judgements"
            ),
            "document_types": ["final_report"],
        },
        "RESULT-EMPTY-001": {
            "state": (
                "system_incomplete"
                if (
                    result_inventory_incomplete
                    or result_anchored_inputs < result_expected_inputs
                    or anchored_verdict_rows < expected_verdict_rows
                    or anchored_summary_rows < expected_summary_rows
                ) else "complete"
            ),
            "applicability": "applicable",
            "expected_input_count": result_expected_inputs,
            "anchored_input_count": result_anchored_inputs,
            "reason_code": (
                "report_item_or_sample_results_not_fully_extracted"
                if result_inventory_incomplete else
                "report_summary_or_result_rows_not_fully_anchored"
                if (
                    anchored_verdict_rows < expected_verdict_rows
                    or anchored_summary_rows < expected_summary_rows
                )
                else "all_report_result_rows_anchored"
            ),
            "document_types": ["final_report"],
        },
        "RESULT-DIMENSION-001": {
            # This rule is for result tables that declare a finite set of
            # measurement dimensions (for example +24 V and -14 V columns).
            # A report made only of per-execution functional judgements has no
            # such matrix; the plan/report/raw set coverage is audited by the
            # dedicated GRAPH-COVERAGE and GRAPH-SAMPLE rules instead.
            "state": "system_incomplete" if expected_numeric_cells else "complete",
            "applicability": "applicable" if expected_numeric_cells else "not_applicable",
            "expected_input_count": expected_numeric_cells,
            "anchored_input_count": 0,
            "reason_code": (
                "numeric_result_dimensions_not_mapped"
                if expected_numeric_cells else
                "report_has_no_explicit_multidimensional_result_matrix"
            ),
            "document_types": ["final_report"],
        },
        "RESULT-DUPLICATE-001": {
            # Suspicious-copy detection is only meaningful for two explicitly
            # mutually-exclusive numeric result payloads. Repeated Pass/Fail
            # judgements are checked by REPORT-RESULT-CONFLICT-001 and must not
            # be treated as copied measurement data.
            "state": "system_incomplete" if expected_numeric_cells else "complete",
            "applicability": "applicable" if expected_numeric_cells else "not_applicable",
            "expected_input_count": expected_numeric_cells,
            "anchored_input_count": 0,
            "reason_code": (
                "mutually_exclusive_numeric_result_groups_not_mapped"
                if expected_numeric_cells else
                "report_has_no_mutually_exclusive_numeric_result_payloads"
            ),
            "document_types": ["final_report"],
        },
        "SEMANTIC-VARIABLE-001": {
            # This rule compares explicit expected/actual variable-name pairs.
            # Required/actual performance *levels* are handled separately and
            # do not constitute a semantic-variable mapping.
            "state": "complete",
            "applicability": "not_applicable",
            "expected_input_count": 0,
            "anchored_input_count": 0,
            "reason_code": "report_has_no_explicit_expected_actual_variable_name_pairs",
            "document_types": ["final_report"],
        },
        "RESULT-FORMULA-001": {
            "state": (
                "system_incomplete"
                if anchored_numeric_cells < expected_numeric_cells else "complete"
            ),
            "applicability": (
                "applicable" if expected_numeric_cells else "not_applicable"
            ),
            "expected_input_count": expected_numeric_cells,
            "anchored_input_count": anchored_numeric_cells,
            "reason_code": (
                "report_numeric_cells_not_fully_anchored"
                if expected_numeric_cells else "no_numeric_calculation_table_in_report"
            ),
            "document_types": ["final_report"],
        },
        "RESULT-LIMIT-001": {
            "state": (
                "system_incomplete"
                if anchored_numeric_cells < expected_numeric_cells else "complete"
            ),
            "applicability": (
                "applicable" if expected_numeric_cells else "not_applicable"
            ),
            "expected_input_count": expected_numeric_cells,
            "anchored_input_count": anchored_numeric_cells,
            "reason_code": (
                "report_numeric_cells_not_fully_anchored"
                if expected_numeric_cells else "no_numeric_limit_result_table_in_report"
            ),
            "document_types": ["final_report"],
        },
    }
    unit_ids = sorted(set(observations_by_unit) | set(errors_by_unit))
    if not unit_ids:
        unit_ids = ["structured:final_report:audit"]
    results: list[UnifiedExtractionResult] = []
    for index, unit_id in enumerate(unit_ids):
        errors = errors_by_unit.get(unit_id, [])
        results.append(UnifiedExtractionResult(
            unit_id=unit_id,
            status="complete" if not errors else "needs_review",
            observations=observations_by_unit.get(unit_id, []),
            evidence=evidence_by_unit.get(unit_id, []),
            channels_used=["structured_check_bridge"],
            errors=errors,
            audit_metadata={"check_coverage": coverage} if index == 0 else {},
        ))
    return results


_ARTIFACT_REQUIREMENT_PATTERNS = (
    re.compile(
        r"(?:需要|应|须|并)?(?:提供|提交|附上|另附|保存)\s*"
        r"(?:一份|相应|相关)?\s*"
        r"(性能曲线|曲线|波形(?:截图|图)?|截图|照片|测试数据|试验数据|"
        r"数据文件|声明|证书|报告)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?i)(?:provide|submit|attach|save)\s+(?:the\s+|a\s+|an\s+)?"
        r"(performance\s+curve|waveform(?:\s+screenshot)?|screenshot|photo|"
        r"test\s+data|data\s+file|declaration|certificate|report)",
    ),
)


def _required_artifacts(value: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for pattern in _ARTIFACT_REQUIREMENT_PATTERNS:
        for match in pattern.finditer(str(value or "")):
            artifact = re.sub(r"\s+", " ", match.group(1)).strip()
            found.append((artifact, match.group(0).strip()))
    return found


def _observed_test_item_identities(raw: dict, report: dict) -> set[str]:
    names: list[str] = []
    names.extend(
        str(meta.get("test_item_name") or meta.get("test_item_code") or "")
        for meta in raw.get("metas", []) if isinstance(meta, dict)
    )
    names.extend(
        str(item.get("test_item_name") or item.get("test_item_code") or "")
        for item in report.get("item_extractions", []) if isinstance(item, dict)
    )
    return {_test_item_identity(name) for name in names if name.strip()}


def _test_plan_evidence_profiles(
    graph_id: str,
    units: list[EvidenceGraphDocumentUnit],
    structured: dict,
    observed_item_identities: set[str],
) -> list[UnifiedExtractionResult]:
    """Bridge explicit plan artifact requirements into the generic rule."""
    observations_by_unit: dict[str, list[MaterializedObservation]] = defaultdict(list)
    evidence_by_unit: dict[str, list[EvidenceRecord]] = defaultdict(list)
    errors_by_unit: dict[str, list[str]] = defaultdict(list)
    details_by_name = {
        _test_item_identity(str(detail.get("code") or "")): detail.get("fields") or {}
        for detail in structured.get("test_details", [])
        if isinstance(detail, dict)
    }
    expected = 0
    anchored = 0
    incomplete = 0
    seen: set[tuple[str, str]] = set()
    for item_index, item in enumerate(structured.get("test_items", [])):
        if not isinstance(item, dict):
            continue
        item_name = str(item.get("name") or item.get("code") or "").strip()
        identity = _test_item_identity(item_name)
        fields = details_by_name.get(identity, {})
        sources = [str(item.get("acceptance") or "")]
        if isinstance(fields, dict):
            sources.extend(str(value or "") for value in fields.values())
        for source_index, source_text in enumerate(sources):
            for artifact, requirement_quote in _required_artifacts(source_text):
                key = (identity, _compact(artifact))
                if not identity or key in seen:
                    continue
                seen.add(key)
                expected += 1
                unit = _source_unit(
                    units, doc_type="test_plan",
                    required_values=[item_name, requirement_quote],
                )
                if unit is None:
                    errors_by_unit["structured:test_plan:evidence_profiles"].append(
                        f"证据制品要求无法回锚:{item_index}:{source_index}",
                    )
                    continue
                item_executed = identity in observed_item_identities
                # Absence is conclusive when the complete raw/report item
                # inventories do not contain the planned test at all. If the
                # test exists, artifact absence can require visual or external
                # attachment inspection and is therefore not over-claimed.
                if item_executed:
                    incomplete += 1
                if _append_observation(
                    graph_id=graph_id, unit=unit,
                    observations=observations_by_unit[unit.unit_id],
                    evidence=evidence_by_unit[unit.unit_id],
                    observation_type="parameter", entity_name=item_name,
                    field_name="evidence_requirement", raw_value=artifact,
                    source_value=requirement_quote,
                    metadata={
                        "required_artifacts": [artifact],
                        "artifact_coverage_complete": not item_executed,
                        "artifact_coverage_source": "system",
                    },
                    identity_key=f"artifact-requirement:{item_index}:{source_index}:{artifact}",
                ):
                    anchored += 1

    coverage = {
        "EVIDENCE-PROFILE-001": {
            "state": (
                "system_incomplete"
                if anchored < expected or incomplete else "complete"
            ),
            "applicability": "applicable" if expected else "not_applicable",
            "expected_input_count": expected,
            "anchored_input_count": anchored,
            "reason_code": (
                "plan_artifact_requirements_not_fully_anchored"
                if anchored < expected else
                "executed_item_artifact_presence_requires_visual_or_external_source"
                if incomplete else
                "plan_has_no_explicit_artifact_requirements" if not expected else
                "all_explicit_plan_artifact_requirements_checked"
            ),
            "document_types": ["test_plan", "original_records", "final_report"],
        },
    }
    unit_ids = sorted(set(observations_by_unit) | set(errors_by_unit))
    if not unit_ids:
        unit_ids = ["structured:test_plan:evidence_profiles"]
    return [UnifiedExtractionResult(
        unit_id=unit_id,
        status="complete" if not errors_by_unit.get(unit_id) else "needs_review",
        observations=observations_by_unit.get(unit_id, []),
        evidence=evidence_by_unit.get(unit_id, []),
        channels_used=["structured_check_bridge"],
        errors=errors_by_unit.get(unit_id, []),
        audit_metadata={"check_coverage": coverage} if index == 0 else {},
    ) for index, unit_id in enumerate(unit_ids)]


def build_structured_check_results(
    graph_id: str,
    units: list[EvidenceGraphDocumentUnit],
    metadata_by_doc_type: dict[str, list[dict]],
    *,
    doc_types: set[str] | None = None,
) -> list[UnifiedExtractionResult]:
    """Create evidence-backed observations for checks bypassed by fast reuse."""
    selected = doc_types or {"test_plan", "original_records", "final_report"}
    results: list[UnifiedExtractionResult] = []
    if "test_plan" in selected:
        plan = _payload(metadata_by_doc_type.get("test_plan", []))
        raw = _payload(metadata_by_doc_type.get("original_records", []))
        report = _payload(metadata_by_doc_type.get("final_report", []))
        results.extend(_test_plan_evidence_profiles(
            graph_id, units, plan, _observed_test_item_identities(raw, report),
        ))
    if "original_records" in selected:
        raw = _payload(metadata_by_doc_type.get("original_records", []))
        results.extend(_raw_record_results(graph_id, units, raw))
    if "final_report" in selected:
        report = _payload(metadata_by_doc_type.get("final_report", []))
        results.extend(_report_results(graph_id, units, report))
    return results
