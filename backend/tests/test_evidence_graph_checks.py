from datetime import date

from services.evidence_graph_checks import (
    AnomalyConclusionObservation,
    ComparableResult,
    ConclusionSetObservation,
    DateRangeObservation,
    DatedExecution,
    InstrumentObservation,
    NumericResultRow,
    NumberedSection,
    RequirementResultMatrix,
    SummaryDetailObservation,
    TypedResultObservation,
    check_anomaly_conclusions,
    check_duplicate_section_numbers,
    check_execution_dates_within_range,
    check_instrument_identity,
    check_numeric_result_rows,
    check_overall_conclusions,
    check_requirement_dimension_coverage,
    check_result_types,
    check_summary_detail_consistency,
    check_suspicious_duplicate_results,
)
from services.evidence_graph_models import FindingStatus


def test_duplicate_number_with_different_titles_is_error():
    findings = check_duplicate_section_numbers("run-1", [
        NumberedSection(node_id="a", number="1.4", title="客户影响度", evidence_ids=["ev-a"]),
        NumberedSection(node_id="b", number="1.4", title="功能状态", evidence_ids=["ev-b"]),
    ])
    assert findings[0].status == FindingStatus.CONFIRMED_ERROR
    assert findings[0].evidence_ids == ["ev-a", "ev-b"]


def test_same_section_repeated_on_continuation_page_is_not_error():
    assert check_duplicate_section_numbers("run-1", [
        NumberedSection(node_id="a", number="1.4", title="功能状态", evidence_ids=["ev-a"]),
        NumberedSection(node_id="b", number="1.4", title="功能状态", evidence_ids=["ev-b"]),
    ]) == []


def test_execution_outside_declared_range_is_error_but_short_execution_inside_is_valid():
    declared = DateRangeObservation(
        node_id="range", start_date=date(2026, 5, 30), end_date=date(2026, 7, 1),
        evidence_ids=["ev-range"],
    )
    findings = check_execution_dates_within_range("run-1", declared, [
        DatedExecution(node_id="inside", test_name="项目A", execution_date=date(2026, 6, 16), evidence_ids=["ev-in"]),
        DatedExecution(node_id="outside", test_name="项目B", execution_date=date(2026, 5, 27), evidence_ids=["ev-out"]),
    ])
    assert [finding.subject_node_ids[-1] for finding in findings] == ["outside"]


def test_missing_requirement_dimension_needs_coverage_proof_before_error():
    unresolved = check_requirement_dimension_coverage("run-1", [RequirementResultMatrix(
        node_id="eq-te04", test_name="反转电压", required_dimensions=["24V", "-14V"],
        result_dimensions=["24V"], coverage_complete=False,
        requirement_evidence_ids=["ev-req"], result_evidence_ids=["ev-result"],
    )])[0]
    confirmed = check_requirement_dimension_coverage("run-1", [RequirementResultMatrix(
        node_id="eq-te04", test_name="反转电压", required_dimensions=["24V", "-14V"],
        result_dimensions=["24V"], coverage_complete=True,
        requirement_evidence_ids=["ev-req"], result_evidence_ids=["ev-result"],
    )])[0]
    assert unresolved.status == FindingStatus.UNRESOLVED
    assert confirmed.status == FindingStatus.CONFIRMED_ERROR


def test_summary_pass_with_blank_detail_is_only_error_after_region_verification():
    base = dict(
        node_id="case-721", test_name="校验字节", summary_result="OK", detail_result="",
        summary_evidence_ids=["ev-summary"],
    )
    unresolved = check_summary_detail_consistency(
        "run-1", [SummaryDetailObservation(**base)],
    )[0]
    confirmed = check_summary_detail_consistency("run-1", [SummaryDetailObservation(
        **base, detail_region_verified=True, detail_region_evidence_ids=["ev-region"],
    )])[0]
    assert unresolved.status == FindingStatus.UNRESOLVED
    assert confirmed.status == FindingStatus.CONFIRMED_ERROR
    assert unresolved.title == "校验字节汇总为通过，但系统未确认到明细判定"
    assert "不能判断是报告未填写还是提取遗漏" in unresolved.description
    assert confirmed.title == "校验字节汇总通过但明细结果为空"


def test_result_dimension_detects_resistance_capacitance_mismatch():
    findings = check_result_types("run-1", [TypedResultObservation(
        node_id="resistance", test_name="电阻测试", expected_unit="kΩ", actual_unit="pF",
        actual_value="34", evidence_ids=["ev-req", "ev-result"],
    )])
    assert findings[0].status == FindingStatus.CONFIRMED_ERROR


def test_duplicate_equipment_id_for_distinct_devices_is_error():
    findings = check_instrument_identity("run-1", [
        InstrumentObservation(node_id="i1", equipment_name="高低温箱", equipment_model="SC7", equipment_id="ZDZ134", evidence_ids=["ev-1"]),
        InstrumentObservation(node_id="i2", equipment_name="直流电源", equipment_model="JP1530D", equipment_id="ZDZ134", evidence_ids=["ev-2"]),
    ])
    assert findings[0].status == FindingStatus.CONFIRMED_ERROR


def test_translated_instrument_names_with_same_model_are_same_identity():
    findings = check_instrument_identity("run-1", [
        InstrumentObservation(
            node_id="i1", equipment_name="示波器", equipment_model="MDO3102",
            equipment_id="C055285", evidence_ids=["ev-1"],
        ),
        InstrumentObservation(
            node_id="i2", equipment_name="Oscilloscope", equipment_model="MDO3102",
            equipment_id="C055285", evidence_ids=["ev-2"],
        ),
    ])
    assert findings == []


def test_anomaly_is_advisory_without_explicit_prohibition():
    findings = check_anomaly_conclusions("run-1", [AnomalyConclusionObservation(
        node_id="ic06", test_name="电压波动", anomaly_text="试验中有啸叫声",
        conclusion="符合", evidence_ids=["ev-note", "ev-conclusion"],
    )])
    assert findings[0].status == FindingStatus.CONFIRMED_ADVISORY


def test_identical_mutually_exclusive_results_are_advisory_not_error():
    findings = check_suspicious_duplicate_results("run-1", [
        ComparableResult(node_id="recessive", test_name="隐性输出", normalized_payload={"8V": [7.8, 1.0]}, mutually_exclusive_group="lin-level", evidence_ids=["ev-a"]),
        ComparableResult(node_id="dominant", test_name="显性输出", normalized_payload={"8V": [7.8, 1.0]}, mutually_exclusive_group="lin-level", evidence_ids=["ev-b"]),
    ])
    assert findings[0].status == FindingStatus.CONFIRMED_ADVISORY


def test_explicit_numeric_cells_are_recalculated_by_graph_rule():
    findings = check_numeric_result_rows("run-1", [NumericResultRow(
        node_id="row-1", test_name="传导发射", reading=30, correction=2,
        reported_result=35, limit=34, reported_margin=1,
        evidence_ids=["ev-reading", "ev-correction", "ev-result", "ev-limit", "ev-margin"],
    )])

    assert {finding.check_id for finding in findings} == {
        "RESULT-FORMULA-001", "RESULT-LIMIT-001",
    }
    assert all(finding.status == FindingStatus.CONFIRMED_ERROR for finding in findings)


def test_overall_pass_with_explicit_failed_item_is_error():
    findings = check_overall_conclusions("run-1", [ConclusionSetObservation(
        node_id="summary", scope_name="整份报告", overall_conclusion="符合",
        individual_conclusions=["符合", "不符合"],
        evidence_ids=["ev-overall", "ev-pass", "ev-fail"],
    )])

    assert [finding.check_id for finding in findings] == ["GRAPH-CONCLUSION-001"]
