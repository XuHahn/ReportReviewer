"""Adapt extracted observations into claim nodes and safe general checks."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from datetime import date
from typing import Iterable

from services.evidence_graph_checks import (
    AnomalyConclusionObservation,
    CalibrationObservation,
    ComparableResult,
    ConclusionSetObservation,
    DeclaredCountObservation,
    EvidenceProfileObservation,
    InstrumentObservation,
    NumericResultRow,
    NumberedSection,
    RequirementResultMatrix,
    SemanticVariableObservation,
    SummaryDetailObservation,
    TypedResultObservation,
    check_anomaly_conclusions,
    check_calibration_coverage,
    check_declared_counts,
    check_duplicate_section_numbers,
    check_evidence_profiles,
    check_instrument_identity,
    check_numeric_result_rows,
    check_overall_conclusions,
    check_requirement_dimension_coverage,
    check_result_types,
    check_semantic_variables,
    check_summary_detail_consistency,
    check_suspicious_duplicate_results,
)
from services.evidence_graph_extraction import UnifiedExtractionResult
from services.evidence_graph_check_execution import build_check_execution
from services.evidence_graph_models import GraphNode, NodeType, ReviewFinding


def _id(prefix: str, *parts: str) -> str:
    return f"{prefix}-{hashlib.sha256(chr(31).join(parts).encode()).hexdigest()[:20]}"


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [item.strip() for item in re.split(r"[/、,，;；]", value) if item.strip()]
    return []


def _parse_date(value: object) -> date | None:
    match = re.search(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})", str(value or ""))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _trusted_flag(metadata: dict, key: str, source_key: str) -> bool:
    return metadata.get(key) is True and metadata.get(source_key) in {"system", "human"}


def build_claim_nodes_findings_and_audit(
    graph_id: str,
    results: Iterable[UnifiedExtractionResult],
) -> tuple[list[GraphNode], list[ReviewFinding], list[dict]]:
    result_list = list(results)
    nodes: list[GraphNode] = []
    sections: list[NumberedSection] = []
    instruments: list[InstrumentObservation] = []
    typed_results: list[TypedResultObservation] = []
    anomalies_by_entity: dict[str, list[tuple[GraphNode, object]]] = defaultdict(list)
    results_by_entity: dict[str, list[tuple[GraphNode, object]]] = defaultdict(list)
    dimension_rows: dict[str, dict] = defaultdict(lambda: {
        "required": [], "reported": [], "requirement_evidence": [],
        "result_evidence": [], "coverage_complete": False,
    })
    summary_rows: dict[str, dict] = defaultdict(lambda: {
        "summary": "", "detail": "", "summary_evidence": [],
        "detail_evidence": [], "region_evidence": [], "region_verified": False,
    })
    evidence_profiles: dict[str, dict] = defaultdict(lambda: {
        "required": [], "observed": [], "requirement_evidence": [],
        "artifact_evidence": [], "coverage_complete": False,
    })
    instrument_profiles: dict[tuple[str, str, str], dict] = defaultdict(lambda: {
        "name": "", "model": "", "serial": "", "calibration": None,
        "evidence": [], "field_region_verified": False, "calibration_required": False,
    })
    execution_dates_by_unit: dict[tuple[str, str], list[date]] = defaultdict(list)
    comparable_results: list[ComparableResult] = []
    declared_counts: list[DeclaredCountObservation] = []
    semantic_variables: list[SemanticVariableObservation] = []
    numeric_rows: dict[tuple[str, str, str], dict] = defaultdict(lambda: {
        "values": {}, "evidence": [], "node_id": "", "name": "",
    })
    conclusion_groups: dict[str, dict] = defaultdict(lambda: {
        "overall": "", "individual": [], "evidence": [], "node_id": "", "name": "",
    })

    coverage_by_check: dict[str, list[dict]] = defaultdict(list)
    for result in result_list:
        coverage = result.audit_metadata.get("check_coverage", {})
        if isinstance(coverage, dict):
            for check_id, item in coverage.items():
                if isinstance(item, dict):
                    coverage_by_check[str(check_id)].append(item)
        for observation in result.observations:
            if observation.observation_type == "test_item" or not observation.evidence_ids:
                continue
            node_type = {
                "instrument": NodeType.INSTRUMENT,
                "anomaly": NodeType.ANOMALY_OBSERVATION,
            }.get(observation.observation_type, NodeType.CLAIM)
            node_id = _id("claim", graph_id, observation.observation_id)
            properties = {
                "claim_type": observation.observation_type,
                "raw_value": observation.raw_value,
                "normalized_value": observation.normalized_value,
                "unit": observation.unit,
                "source_doc_type": str(observation.metadata.get("doc_type") or "extracted_document"),
                "field_name": observation.field_name,
                "evidence_ids": observation.evidence_ids,
                **observation.metadata,
            }
            node = GraphNode(
                node_id=node_id,
                graph_id=graph_id,
                node_type=node_type,
                label=f"{observation.entity_name} · {observation.field_name}",
                canonical_key=f"{observation.entity_name.casefold()}:{observation.field_name.casefold()}",
                properties=properties,
            )
            nodes.append(node)
            field = observation.field_name.casefold()
            entity_key = observation.entity_name.strip().casefold()
            metadata = observation.metadata
            unit_key = (
                str(metadata.get("doc_type") or ""),
                str(metadata.get("execution_group") or metadata.get("unit_id") or ""),
            )

            if observation.observation_type == "date" and field in {
                "test_date", "detection_date", "execution_date", "test_end_date",
            }:
                parsed = _parse_date(observation.normalized_value or observation.raw_value)
                if parsed:
                    execution_dates_by_unit[unit_key].append(parsed)

            if field in {"section_number", "item_number", "clause_number"}:
                title = str(observation.metadata.get("section_title") or "").strip()
                if title:
                    sections.append(NumberedSection(
                        node_id=node_id, number=str(observation.raw_value), title=title,
                        evidence_ids=observation.evidence_ids,
                    ))

            if observation.observation_type == "instrument":
                explicit_equipment_id = str(metadata.get("equipment_id") or "").strip()
                if explicit_equipment_id:
                    instruments.append(InstrumentObservation(
                        node_id=node_id,
                        equipment_name=observation.entity_name,
                        equipment_model=str(metadata.get("equipment_model") or ""),
                        equipment_id=explicit_equipment_id,
                        evidence_ids=observation.evidence_ids,
                    ))
                parent = str(metadata.get("parent_name") or "").strip()
                profile_name = (
                    str(observation.raw_value).strip()
                    if field in {"instrument_name", "equipment_name"} else parent or observation.entity_name
                )
                profile_key = (*unit_key, profile_name.casefold())
                profile = instrument_profiles[profile_key]
                profile["name"] = profile["name"] or profile_name
                profile["evidence"].extend(observation.evidence_ids)
                if field in {"instrument_model", "equipment_model", "model"}:
                    profile["model"] = str(observation.raw_value).strip()
                if field in {"instrument_serial_number", "serial_number", "equipment_id", "instrument_id"}:
                    profile["serial"] = str(observation.raw_value).strip()
                if field in {"instrument_calibration_expiry", "calibration_valid_until", "calibration_end"}:
                    profile["calibration_required"] = True
                    profile["calibration"] = _parse_date(observation.normalized_value or observation.raw_value)
                    profile["field_region_verified"] = _trusted_flag(
                        metadata, "field_region_verified", "field_region_verification_source",
                    )
                if metadata.get("calibration_required") is True:
                    profile["calibration_required"] = True

            expected_unit = str(observation.metadata.get("expected_unit") or "").strip()
            if observation.observation_type == "result" and expected_unit and observation.unit:
                typed_results.append(TypedResultObservation(
                    node_id=node_id,
                    test_name=observation.entity_name,
                    expected_unit=expected_unit,
                    actual_unit=observation.unit,
                    expected_value_type=str(observation.metadata.get("expected_value_type") or ""),
                    actual_value=observation.raw_value,
                    evidence_ids=observation.evidence_ids,
                ))

            if observation.observation_type == "anomaly" and entity_key:
                anomalies_by_entity[entity_key].append((node, observation))
            if observation.observation_type == "result" and entity_key:
                results_by_entity[entity_key].append((node, observation))

            required_dimensions = _as_list(metadata.get("required_dimensions"))
            result_dimensions = _as_list(metadata.get("result_dimensions"))
            if str(metadata.get("dimension_role") or "") == "requirement":
                required_dimensions.extend(_as_list(observation.raw_value))
            if str(metadata.get("dimension_role") or "") == "result":
                result_dimensions.extend(_as_list(observation.raw_value))
            if required_dimensions or result_dimensions:
                row = dimension_rows[entity_key]
                row["node_id"] = node_id
                row["name"] = observation.entity_name
                row["required"].extend(required_dimensions)
                row["reported"].extend(result_dimensions)
                if required_dimensions:
                    row["requirement_evidence"].extend(observation.evidence_ids)
                if result_dimensions:
                    row["result_evidence"].extend(observation.evidence_ids)
                row["coverage_complete"] = row["coverage_complete"] or _trusted_flag(
                    metadata, "coverage_complete", "coverage_source",
                )

            # Explicit extraction groups take precedence over display names.
            # Aggregate result chapters can contain child rows with different
            # names (for example an open-circuit chapter containing separate
            # single-wire and multi-wire rows).
            summary_key = str(
                metadata.get("conclusion_group") or entity_key
            ).strip().casefold()
            summary_row = summary_rows[summary_key]
            if field in {"summary_result", "overall_result", "summary_conclusion"}:
                summary_row.update(node_id=node_id, name=observation.entity_name, summary=str(observation.raw_value))
                summary_row["summary_evidence"].extend(observation.evidence_ids)
            if field in {"detail_result", "actual_test_result", "measured_result"}:
                summary_row.update(node_id=node_id, name=observation.entity_name, detail=str(observation.raw_value))
                summary_row["detail_evidence"].extend(observation.evidence_ids)
            if "summary_result" in metadata:
                summary_row.update(node_id=node_id, name=observation.entity_name, summary=str(metadata.get("summary_result") or ""))
                summary_row["summary_evidence"].extend(observation.evidence_ids)
            if "detail_result" in metadata:
                summary_row.update(node_id=node_id, name=observation.entity_name, detail=str(metadata.get("detail_result") or ""))
                summary_row["detail_evidence"].extend(observation.evidence_ids)
            if _trusted_flag(metadata, "detail_region_verified", "detail_region_verification_source"):
                summary_row["region_verified"] = True
                summary_row["region_evidence"].extend(observation.evidence_ids)

            group_name = str(metadata.get("mutually_exclusive_group") or "").strip()
            if observation.observation_type == "result" and group_name:
                comparable_results.append(ComparableResult(
                    node_id=node_id, test_name=observation.entity_name,
                    normalized_payload=observation.normalized_value or observation.raw_value,
                    mutually_exclusive_group=group_name, evidence_ids=observation.evidence_ids,
                ))

            required_artifacts = _as_list(metadata.get("required_artifacts"))
            if required_artifacts:
                profile = evidence_profiles[entity_key]
                profile.update(node_id=node_id, name=observation.entity_name)
                profile["required"].extend(required_artifacts)
                profile["requirement_evidence"].extend(observation.evidence_ids)
                profile["coverage_complete"] = profile["coverage_complete"] or _trusted_flag(
                    metadata, "artifact_coverage_complete", "artifact_coverage_source",
                )
            if observation.observation_type == "evidence_artifact":
                artifact_key = str(metadata.get("artifact_type") or observation.field_name or observation.raw_value).strip()
                profile = evidence_profiles[entity_key]
                profile.update(node_id=node_id, name=observation.entity_name)
                profile["observed"].append(artifact_key)
                profile["artifact_evidence"].extend(observation.evidence_ids)

            if "declared_count" in metadata and "observed_count" in metadata:
                try:
                    declared_counts.append(DeclaredCountObservation(
                        node_id=node_id,
                        scope_name=str(metadata.get("count_scope") or observation.entity_name),
                        declared_count=int(metadata["declared_count"]),
                        observed_count=int(metadata["observed_count"]),
                        coverage_complete=_trusted_flag(
                            metadata, "coverage_complete", "coverage_source",
                        ),
                        evidence_ids=observation.evidence_ids,
                    ))
                except (TypeError, ValueError):
                    pass

            expected_name = str(metadata.get("expected_name") or metadata.get("expected_variable") or "").strip()
            actual_name = str(metadata.get("actual_name") or metadata.get("actual_variable") or "").strip()
            if expected_name and actual_name:
                semantic_variables.append(SemanticVariableObservation(
                    node_id=node_id, context_name=observation.entity_name,
                    expected_name=expected_name, actual_name=actual_name,
                    evidence_ids=observation.evidence_ids,
                ))

            row_id = str(metadata.get("row_id") or "").strip()
            numeric_field_map = {
                "reading": "reading", "measured_reading": "reading",
                "correction_db": "correction", "correction": "correction",
                "result_dbua": "reported_result", "calculated_result": "reported_result",
                "limit_dbua": "limit", "limit": "limit",
                "margin_db": "reported_margin", "margin": "reported_margin",
            }
            if row_id and field in numeric_field_map:
                match = re.search(r"[-+]?\d+(?:\.\d+)?", str(observation.raw_value))
                if match:
                    row = numeric_rows[(entity_key, row_id, str(metadata.get("sample_id") or ""))]
                    row.update(node_id=node_id, name=observation.entity_name)
                    row["values"][numeric_field_map[field]] = float(match.group())
                    row["evidence"].extend(observation.evidence_ids)

            conclusion_group = str(metadata.get("conclusion_group") or "").strip()
            conclusion_scope = str(metadata.get("conclusion_scope") or "").strip()
            if conclusion_group and conclusion_scope in {"overall", "individual"}:
                group = conclusion_groups[conclusion_group]
                group.update(node_id=node_id, name=observation.entity_name)
                group["evidence"].extend(observation.evidence_ids)
                if conclusion_scope == "overall":
                    group["overall"] = str(observation.raw_value)
                else:
                    group["individual"].append(str(observation.raw_value))

    anomaly_checks: list[AnomalyConclusionObservation] = []
    for entity_key, anomaly_rows in anomalies_by_entity.items():
        result_rows = results_by_entity.get(entity_key, [])
        if len(result_rows) != 1:
            continue
        result_node, result_observation = result_rows[0]
        for anomaly_node, anomaly_observation in anomaly_rows:
            anomaly_checks.append(AnomalyConclusionObservation(
                node_id=anomaly_node.node_id,
                test_name=anomaly_observation.entity_name,
                anomaly_text=str(anomaly_observation.raw_value),
                conclusion=str(result_observation.raw_value),
                prohibited_by_requirement=bool(
                    anomaly_observation.metadata.get("prohibited_by_requirement") is True
                ),
                evidence_ids=list(dict.fromkeys([
                    *anomaly_observation.evidence_ids, *result_observation.evidence_ids,
                ])),
            ))

    calibrations: list[CalibrationObservation] = []
    for (doc_type, unit_id, _), profile in instrument_profiles.items():
        evidence_ids = list(dict.fromkeys(profile["evidence"]))
        participates_in_cross_field_check = bool(
            profile["serial"]
            or profile["calibration_required"]
            or profile["calibration"] is not None
        )
        if not evidence_ids or not participates_in_cross_field_check:
            continue
        profile_node_id = _id("claim", graph_id, doc_type, unit_id, profile["name"], "instrument_profile")
        # Cross-field instrument checks operate on one assembled profile, not
        # on any single source cell.  Persist that aggregate subject as a real
        # graph node before findings reference it; otherwise a valid finding
        # can fail the store's referential-integrity gate.
        nodes.append(GraphNode(
            node_id=profile_node_id,
            graph_id=graph_id,
            node_type=NodeType.INSTRUMENT,
            label=f"{profile['name']} · 仪器身份与校准信息",
            canonical_key=(
                f"instrument_profile:{doc_type}:{unit_id}:"
                f"{str(profile['name']).casefold()}"
            ),
            properties={
                "claim_type": "instrument_profile",
                "raw_value": {
                    "name": profile["name"],
                    "model": profile["model"],
                    "serial": profile["serial"],
                    "calibration": (
                        profile["calibration"].isoformat()
                        if profile["calibration"] is not None else ""
                    ),
                },
                "source_doc_type": doc_type or "extracted_document",
                "field_name": "instrument_profile",
                "evidence_ids": evidence_ids,
            },
        ))
        if profile["serial"]:
            instruments.append(InstrumentObservation(
                node_id=profile_node_id,
                equipment_name=profile["name"], equipment_model=profile["model"],
                equipment_id=profile["serial"], evidence_ids=evidence_ids,
            ))
        unit_dates = execution_dates_by_unit.get((doc_type, unit_id), [])
        if profile["calibration_required"] or profile["calibration"] is not None:
            calibrations.append(CalibrationObservation(
                node_id=profile_node_id, instrument_name=profile["name"],
                calibration_end=profile["calibration"],
                execution_date=max(unit_dates) if unit_dates else None,
                required=profile["calibration_required"],
                field_region_verified=profile["field_region_verified"],
                evidence_ids=evidence_ids,
            ))

    matrices = [RequirementResultMatrix(
        node_id=row["node_id"], test_name=row["name"],
        required_dimensions=list(dict.fromkeys(row["required"])),
        result_dimensions=list(dict.fromkeys(row["reported"])),
        coverage_complete=row["coverage_complete"],
        requirement_evidence_ids=list(dict.fromkeys(row["requirement_evidence"])),
        result_evidence_ids=list(dict.fromkeys(row["result_evidence"])),
    ) for row in dimension_rows.values() if row.get("required") and row.get("requirement_evidence")]
    summary_checks = [SummaryDetailObservation(
        node_id=row["node_id"], test_name=row["name"], summary_result=row["summary"],
        detail_result=row["detail"], summary_evidence_ids=list(dict.fromkeys(row["summary_evidence"])),
        detail_evidence_ids=list(dict.fromkeys(row["detail_evidence"])),
        detail_region_verified=row["region_verified"],
        detail_region_evidence_ids=list(dict.fromkeys(row["region_evidence"])),
    ) for row in summary_rows.values() if row.get("summary") and row.get("summary_evidence")]
    artifact_checks = [EvidenceProfileObservation(
        node_id=row["node_id"], test_name=row["name"],
        required_artifacts=list(dict.fromkeys(row["required"])),
        observed_artifacts=list(dict.fromkeys(row["observed"])),
        coverage_complete=row["coverage_complete"],
        requirement_evidence_ids=list(dict.fromkeys(row["requirement_evidence"])),
        artifact_evidence_ids=list(dict.fromkeys(row["artifact_evidence"])),
    ) for row in evidence_profiles.values() if row.get("required") and row.get("requirement_evidence")]
    numeric_checks = [NumericResultRow(
        node_id=row["node_id"], test_name=row["name"], evidence_ids=list(dict.fromkeys(row["evidence"])),
        **row["values"],
    ) for row in numeric_rows.values() if row.get("evidence")]
    conclusion_checks = [ConclusionSetObservation(
        node_id=row["node_id"], scope_name=row["name"], overall_conclusion=row["overall"],
        individual_conclusions=row["individual"], evidence_ids=list(dict.fromkeys(row["evidence"])),
    ) for row in conclusion_groups.values()
        if row.get("overall") and row.get("individual") and len(set(row.get("evidence", []))) >= 2]

    numeric_findings = check_numeric_result_rows(graph_id, numeric_checks)
    batches = [
        ("DOC-STRUCTURE-001", len(sections), check_duplicate_section_numbers(graph_id, sections)),
        ("INSTRUMENT-IDENTITY-001", len(instruments), check_instrument_identity(graph_id, instruments)),
        ("RESULT-TYPE-001", len(typed_results), check_result_types(graph_id, typed_results)),
        ("ANOMALY-CONCLUSION-001", len(anomaly_checks), check_anomaly_conclusions(graph_id, anomaly_checks)),
        ("RESULT-DIMENSION-001", len(matrices), check_requirement_dimension_coverage(graph_id, matrices)),
        ("RESULT-EMPTY-001", len(summary_checks), check_summary_detail_consistency(graph_id, summary_checks)),
        ("RESULT-DUPLICATE-001", len(comparable_results), check_suspicious_duplicate_results(graph_id, comparable_results)),
        ("DOC-STRUCTURE-002", len(declared_counts), check_declared_counts(graph_id, declared_counts)),
        ("INSTRUMENT-CAL-001", len(calibrations), check_calibration_coverage(graph_id, calibrations)),
        ("EVIDENCE-PROFILE-001", len(artifact_checks), check_evidence_profiles(graph_id, artifact_checks)),
        ("SEMANTIC-VARIABLE-001", len(semantic_variables), check_semantic_variables(graph_id, semantic_variables)),
        ("RESULT-FORMULA-001", len(numeric_checks), [
            item for item in numeric_findings if item.check_id == "RESULT-FORMULA-001"
        ]),
        ("RESULT-LIMIT-001", len(numeric_checks), [
            item for item in numeric_findings if item.check_id == "RESULT-LIMIT-001"
        ]),
        ("GRAPH-CONCLUSION-001", len(conclusion_checks), check_overall_conclusions(graph_id, conclusion_checks)),
    ]
    # RESULT-EMPTY is an absence assertion.  When upstream coverage says the
    # report item/sample inventory is incomplete, an empty detail result is an
    # extraction gap rather than evidence that the submitted report is blank.
    # Keep the rule visible as system_incomplete, but do not create a false
    # user-facing document defect.
    if any(
        str(item.get("state") or "") == "system_incomplete"
        for item in coverage_by_check.get("RESULT-EMPTY-001", [])
    ):
        batches = [
            (check_id, input_count, [] if check_id == "RESULT-EMPTY-001" else batch)
            for check_id, input_count, batch in batches
        ]
    findings = [finding for _, _, batch in batches for finding in batch]
    audit: list[dict] = []
    for check_id, input_count, batch in batches:
        coverage_rows = coverage_by_check.get(check_id, [])
        expected = sum(int(item.get("expected_input_count") or 0) for item in coverage_rows)
        anchored = sum(int(item.get("anchored_input_count") or 0) for item in coverage_rows)
        states = {str(item.get("state") or "") for item in coverage_rows}
        applicability_values = {
            str(item.get("applicability") or "unknown") for item in coverage_rows
        }
        forced_state = None
        if "system_incomplete" in states:
            forced_state = "system_incomplete"
        elif "source_missing" in states:
            forced_state = "source_missing"
        applicability = (
            "not_applicable"
            if coverage_rows and applicability_values == {"not_applicable"}
            else "applicable"
        )
        reason_codes = sorted({
            str(item.get("reason_code") or "") for item in coverage_rows
            if item.get("reason_code")
        })
        document_types = {
            str(doc_type)
            for item in coverage_rows
            for doc_type in item.get("document_types", [])
            if doc_type
        }
        # A deterministic exhaustive scan can legitimately produce zero
        # positive observations (for example, all 54 remarks contain no
        # prohibited anomaly).  Complete coverage is the evidence for a pass;
        # requiring a positive anomaly node would make clean data impossible
        # to mark as checked.
        if (
            forced_state is None
            and coverage_rows
            and applicability == "applicable"
            and expected > 0
            and anchored == expected
            and not batch
        ):
            forced_state = "passed"
        audit.append(build_check_execution(
            check_id,
            input_count,
            len(batch),
            applicability=applicability,
            expected_input_count=(expected if coverage_rows else input_count),
            anchored_input_count=(anchored if coverage_rows else input_count),
            reason_code="+".join(reason_codes),
            document_types=document_types,
            forced_state=forced_state,  # type: ignore[arg-type]
        ))
    return nodes, findings, audit


def build_claim_nodes_and_findings(
    graph_id: str,
    results: Iterable[UnifiedExtractionResult],
) -> tuple[list[GraphNode], list[ReviewFinding]]:
    nodes, findings, _ = build_claim_nodes_findings_and_audit(graph_id, results)
    return nodes, findings
