from services.evidence_graph_check_adapter import (
    build_claim_nodes_and_findings,
    build_claim_nodes_findings_and_audit,
)
from services.evidence_graph_extraction import MaterializedObservation, UnifiedExtractionResult
from services.evidence_graph_models import FindingStatus, NodeType


def _observation(observation_id, observation_type, entity, field, value, evidence, **kwargs):
    return MaterializedObservation(
        observation_id=observation_id, observation_type=observation_type,
        entity_name=entity, field_name=field, raw_value=value,
        evidence_ids=[evidence], extraction_sources=["native"], **kwargs,
    )


def test_adapter_persists_claims_and_only_runs_checks_with_explicit_metadata():
    result = UnifiedExtractionResult(unit_id="page-1", status="complete", observations=[
        _observation("a", "instrument", "高低温箱", "instrument", "ZDZ134", "ev-a", metadata={"equipment_id": "ZDZ134", "equipment_model": "SC7"}),
        _observation("b", "instrument", "直流电源", "instrument", "ZDZ134", "ev-b", metadata={"equipment_id": "ZDZ134", "equipment_model": "JP1530D"}),
        _observation("c", "result", "电阻测试", "measured_value", "34", "ev-c", unit="pF", metadata={"expected_unit": "kΩ"}),
    ])

    nodes, findings = build_claim_nodes_and_findings("graph-1", [result])

    assert len(nodes) == 3
    assert sum(node.node_type == NodeType.INSTRUMENT for node in nodes) == 2
    assert {finding.check_id for finding in findings} == {
        "INSTRUMENT-IDENTITY-001", "RESULT-TYPE-001",
    }
    assert all(finding.status == FindingStatus.CONFIRMED_ERROR for finding in findings)


def test_anomaly_is_advisory_when_exact_entity_has_one_pass_result():
    result = UnifiedExtractionResult(unit_id="page-1", status="complete", observations=[
        _observation("a", "anomaly", "电压波动", "observed_anomaly", "试验中有啸叫声", "ev-a"),
        _observation("b", "result", "电压波动", "conclusion", "符合", "ev-b"),
    ])

    _, findings = build_claim_nodes_and_findings("graph-1", [result])

    assert len(findings) == 1
    assert findings[0].status == FindingStatus.CONFIRMED_ADVISORY


def test_incomplete_instrument_metadata_does_not_create_false_finding():
    result = UnifiedExtractionResult(unit_id="page-1", status="complete", observations=[
        _observation("a", "instrument", "高低温箱", "instrument", "不完整", "ev-a"),
    ])

    nodes, findings = build_claim_nodes_and_findings("graph-1", [result])

    assert len(nodes) == 1
    assert findings == []


def test_adapter_wires_vendor_learned_result_and_evidence_checks():
    result = UnifiedExtractionResult(unit_id="page-1", status="complete", observations=[
        _observation("dim", "result", "反转电压", "condition_results", "24V", "ev-dim", metadata={
            "required_dimensions": ["24V", "-14V"], "result_dimensions": ["24V"],
            "coverage_complete": True, "coverage_source": "system",
        }),
        _observation("empty", "result", "校验字节", "summary_result", "OK", "ev-empty", metadata={
            "detail_result": "", "detail_region_verified": True,
            "detail_region_verification_source": "human",
        }),
        _observation("dup-a", "result", "隐性输出", "measured_values", {"8V": [7.8, 1.0]}, "ev-a", metadata={
            "mutually_exclusive_group": "lin-level",
        }),
        _observation("dup-b", "result", "显性输出", "measured_values", {"8V": [7.8, 1.0]}, "ev-b", metadata={
            "mutually_exclusive_group": "lin-level",
        }),
        _observation("count", "document_field", "检测项目", "declared_count", "20", "ev-count", metadata={
            "declared_count": 20, "observed_count": 21,
            "coverage_complete": True, "coverage_source": "system",
        }),
        _observation("artifact", "parameter", "位速率测试", "evidence_requirement", "波形截图", "ev-art", metadata={
            "required_artifacts": ["waveform"], "artifact_coverage_complete": True,
            "artifact_coverage_source": "system",
        }),
        _observation("variable", "result", "地断路", "leakage_current", "I_BUS_NO_BAT", "ev-var", metadata={
            "expected_variable": "I_BUS_NO_GND", "actual_variable": "I_BUS_NO_BAT",
        }),
    ])

    _, findings = build_claim_nodes_and_findings("graph-1", [result])

    assert {finding.check_id for finding in findings} == {
        "RESULT-DIMENSION-001", "RESULT-EMPTY-001", "RESULT-DUPLICATE-001",
        "DOC-STRUCTURE-002", "EVIDENCE-PROFILE-001", "SEMANTIC-VARIABLE-001",
    }


def test_adapter_does_not_trust_model_claimed_absence_coverage():
    result = UnifiedExtractionResult(unit_id="page-1", status="complete", observations=[
        _observation("dim", "result", "反转电压", "condition_results", "24V", "ev-dim", metadata={
            "required_dimensions": ["24V", "-14V"], "result_dimensions": ["24V"],
            "coverage_complete": True,
        }),
    ])

    _, findings = build_claim_nodes_and_findings("graph-1", [result])

    assert len(findings) == 1
    assert findings[0].status == FindingStatus.UNRESOLVED


def test_adapter_groups_instrument_columns_and_checks_calibration_against_test_date():
    shared = {"doc_type": "original_records", "unit_id": "member-1/page-1", "parent_name": "示波器"}
    result = UnifiedExtractionResult(unit_id="member-1/page-1", status="complete", observations=[
        _observation("name", "instrument", "示波器", "instrument_name", "示波器", "ev-name", metadata=shared),
        _observation("serial", "instrument", "示波器", "serial_number", "SN-1", "ev-serial", metadata=shared),
        _observation("cal", "instrument", "示波器", "calibration_end", "2026-05-01", "ev-cal", metadata=shared),
        _observation("date", "date", "示波器测试", "test_date", "2026-06-01", "ev-date", metadata={
            "doc_type": "original_records", "unit_id": "member-1/page-1",
        }),
    ])

    nodes, findings = build_claim_nodes_and_findings("graph-1", [result])

    assert [finding.check_id for finding in findings] == ["INSTRUMENT-CAL-001"]
    assert findings[0].status == FindingStatus.CONFIRMED_ERROR
    assert set(findings[0].subject_node_ids).issubset({node.node_id for node in nodes})


def test_adapter_groups_numeric_rows_and_conclusion_scopes_without_legacy_validator():
    row = {"row_id": "r1", "sample_id": "S1"}
    result = UnifiedExtractionResult(unit_id="page-1", status="complete", observations=[
        _observation("read", "result", "传导发射", "reading", "30", "ev-read", metadata=row),
        _observation("corr", "result", "传导发射", "correction_db", "2", "ev-corr", metadata=row),
        _observation("calc", "result", "传导发射", "result_dbua", "35", "ev-calc", metadata=row),
        _observation("limit", "result", "传导发射", "limit_dbua", "34", "ev-limit", metadata=row),
        _observation("overall", "result", "检测报告", "conclusion", "符合", "ev-overall", metadata={
            "conclusion_group": "report", "conclusion_scope": "overall",
        }),
        _observation("failed", "result", "项目A", "verdict", "不符合", "ev-failed", metadata={
            "conclusion_group": "report", "conclusion_scope": "individual",
        }),
    ])

    _, findings = build_claim_nodes_and_findings("graph-1", [result])

    assert {finding.check_id for finding in findings} == {
        "RESULT-FORMULA-001", "RESULT-LIMIT-001", "GRAPH-CONCLUSION-001",
    }


def test_summary_detail_check_uses_explicit_group_for_aggregate_child_rows():
    result = UnifiedExtractionResult(unit_id="page-1", status="complete", observations=[
        _observation(
            "summary", "result", "Open circuit tests", "summary_conclusion",
            "Pass", "ev-summary", metadata={"conclusion_group": "report:7"},
        ),
        _observation(
            "detail", "result", "Single-wire open circuit", "detail_result",
            "Pass", "ev-detail", metadata={"conclusion_group": "report:7"},
        ),
    ])

    _, findings, audit = build_claim_nodes_findings_and_audit("graph-1", [result])

    assert not [item for item in findings if item.check_id == "RESULT-EMPTY-001"]
    summary_check = next(item for item in audit if item["check_id"] == "RESULT-EMPTY-001")
    assert summary_check["state"] == "passed"


def test_incomplete_result_inventory_does_not_create_empty_detail_defect():
    result = UnifiedExtractionResult(
        unit_id="page-1", status="complete",
        audit_metadata={"check_coverage": {"RESULT-EMPTY-001": {
            "state": "system_incomplete", "applicability": "applicable",
            "expected_input_count": 4, "anchored_input_count": 1,
            "reason_code": "report_item_or_sample_results_not_fully_extracted",
            "document_types": ["final_report"],
        }}},
        observations=[
            _observation(
                "summary", "result", "瞬态抗扰度试验", "summary_conclusion",
                "符合", "ev-summary", metadata={"conclusion_group": "report:1"},
            ),
        ],
    )

    _, findings, audit = build_claim_nodes_findings_and_audit("graph-1", [result])

    assert not [item for item in findings if item.check_id == "RESULT-EMPTY-001"]
    item = next(row for row in audit if row["check_id"] == "RESULT-EMPTY-001")
    assert item["state"] == "system_incomplete"
    assert item["finding_count"] == 0


def test_adapter_reports_not_applicable_passed_and_issue_execution_states():
    result = UnifiedExtractionResult(unit_id="page-1", status="complete", observations=[
        _observation("date", "date", "试验", "test_date", "2026-01-01", "ev-date", metadata={
            "doc_type": "original_records", "unit_id": "page-1",
        }),
        _observation("name", "instrument", "示波器", "instrument_name", "示波器", "ev-name", metadata={
            "doc_type": "original_records", "unit_id": "page-1", "parent_name": "示波器",
        }),
        _observation("serial", "instrument", "示波器", "serial_number", "SN-1", "ev-serial", metadata={
            "doc_type": "original_records", "unit_id": "page-1", "parent_name": "示波器",
        }),
        _observation("cal", "instrument", "示波器", "calibration_end", "2025-12-31", "ev-cal", metadata={
            "doc_type": "original_records", "unit_id": "page-1", "parent_name": "示波器",
            "calibration_required": True,
        }),
    ])

    _, _, audit = build_claim_nodes_findings_and_audit("graph-1", [result])
    by_id = {item["check_id"]: item for item in audit}

    assert by_id["INSTRUMENT-IDENTITY-001"]["state"] == "passed"
    assert by_id["INSTRUMENT-CAL-001"]["state"] == "issues_found"
    assert by_id["RESULT-FORMULA-001"]["state"] == "system_incomplete"
    assert by_id["RESULT-FORMULA-001"]["reason_code"] == "rule_input_contract_unfulfilled"
