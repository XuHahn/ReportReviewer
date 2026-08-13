from services.evidence_graph_coverage import (
    CoverageProof,
    ObservedTestItem,
    evaluate_test_item_coverage,
    match_test_items,
)
from services.evidence_graph_models import FindingStatus


def _item(item_id: str, doc_type: str, name: str, evidence_id: str, **kwargs):
    return ObservedTestItem(
        item_id=item_id,
        doc_type=doc_type,
        name=name,
        evidence_ids=[evidence_id],
        **kwargs,
    )


def test_four_pulse_rows_match_plan_without_false_missing():
    requirements = [
        _item(f"plan-{code}", "test_plan", f"电源线瞬态传导抗扰{code}", f"ev-plan-{code}")
        for code in ("P1", "P2a", "P2b", "P3a")
    ]
    raw_items = [
        _item(f"raw-{code}", "original_records", f"脉冲{code[1:]}", f"ev-raw-{code}")
        for code in ("P1", "P2a", "P2b", "P3a")
    ]
    report_items = [
        _item(f"report-{code}", "final_report", f"脉冲{code[1:]}", f"ev-report-{code}")
        for code in ("P1", "P2a", "P2b", "P3a")
    ]

    findings, matches = evaluate_test_item_coverage(
        "run-1", requirements, raw_items, report_items, [],
    )

    assert all(finding.status == FindingStatus.CONFIRMED_PASS for finding in findings)
    assert matches["plan-P2a"]["original_records"].observation_id == "raw-P2a"
    assert matches["plan-P2b"]["final_report"].observation_id == "report-P2b"


def test_missing_item_stays_unresolved_without_complete_absence_proof():
    requirement = _item("plan-p2b", "test_plan", "电源线瞬态传导抗扰P2b", "ev-plan")

    findings, _ = evaluate_test_item_coverage(
        "run-1", [requirement], [], [], [
            CoverageProof(
                doc_type="original_records",
                extraction_complete=True,
                identity_variants_searched=True,
                parent_subitems_searched=True,
                targeted_visual_recovery_completed=False,
                evidence_ids=["ev-coverage-raw"],
            ),
            CoverageProof(
                doc_type="final_report",
                extraction_complete=True,
                identity_variants_searched=True,
                parent_subitems_searched=True,
                targeted_visual_recovery_completed=False,
                evidence_ids=["ev-coverage-report"],
            ),
        ],
    )

    assert findings[0].status == FindingStatus.UNRESOLVED
    assert "不得判定为未执行" in findings[0].description


def test_missing_item_becomes_error_only_after_both_absence_proofs():
    requirement = _item("plan-p2b", "test_plan", "电源线瞬态传导抗扰P2b", "ev-plan")
    proofs = [CoverageProof(
        doc_type=doc_type,
        extraction_complete=True,
        identity_variants_searched=True,
        parent_subitems_searched=True,
        targeted_visual_recovery_completed=True,
        evidence_ids=[f"ev-coverage-{doc_type}"],
    ) for doc_type in ("original_records", "final_report")]

    findings, _ = evaluate_test_item_coverage("run-1", [requirement], [], [], proofs)

    assert findings[0].status == FindingStatus.CONFIRMED_ERROR
    assert set(findings[0].evidence_ids) == {
        "ev-plan", "ev-coverage-original_records", "ev-coverage-final_report",
    }
    assert findings[0].title == "电源线瞬态传导抗扰P2b未找到执行记录和报告结果"
    assert "原始记录和检测报告中都未找到" in findings[0].description
    assert "无法证明该项目已经按计划执行并在报告中发布结果" in findings[0].description


def test_single_missing_report_explains_found_execution_and_missing_publication():
    requirement = _item("plan-open", "test_plan", "开路测试", "ev-plan")
    raw = _item("raw-open", "original_records", "开路试验", "ev-raw")
    report_proof = CoverageProof(
        doc_type="final_report",
        extraction_complete=True,
        identity_variants_searched=True,
        parent_subitems_searched=True,
        targeted_visual_recovery_completed=True,
        evidence_ids=["ev-coverage-report"],
    )

    findings, _ = evaluate_test_item_coverage(
        "run-1", [requirement], [raw], [], [report_proof],
    )

    assert findings[0].status == FindingStatus.CONFIRMED_ERROR
    assert findings[0].title == "开路测试未找到检测报告发布证据"
    assert "原始记录已找到对应执行记录" in findings[0].description
    assert "检测报告中没有找到可追溯的发布结果" in findings[0].description
    assert "委托单" not in findings[0].description


def test_generic_subitem_is_unresolved_when_two_families_require_same_code():
    requirements = [
        _item("family-a", "test_plan", "电源线瞬态传导抗扰P2a", "ev-a"),
        _item("family-b", "test_plan", "信号线瞬态传导抗扰P2a", "ev-b"),
    ]
    observation = _item("raw-p2a", "original_records", "脉冲2a", "ev-raw")

    matches = match_test_items(requirements, [observation])

    assert matches["family-a"].status == "unresolved"
    assert matches["family-b"].status == "unresolved"


def test_execution_context_prevents_cross_sample_merge():
    requirement = _item(
        "plan-p2a-s1", "test_plan", "电源线瞬态传导抗扰P2a", "ev-plan",
        sample_id="0001",
    )
    wrong_sample = _item(
        "raw-p2a-s2", "original_records", "脉冲2a", "ev-raw",
        sample_id="0002",
    )

    matches = match_test_items([requirement], [wrong_sample])

    assert matches["plan-p2a-s1"].status == "missing"


def test_base_item_allows_plan_description_and_report_mode_aggregate():
    requirement = _item(
        "plan-open", "test_plan", "开路测试", "ev-plan-open",
        mode="正反转模式\n带风叶负载",
    )
    report = _item(
        "report-open", "final_report", "Open circuit tests", "ev-report-open",
        mode="Mode 1, Mode 2",
    )

    matches = match_test_items([requirement], [report])

    assert matches["plan-open"].status == "matched"
    assert matches["plan-open"].method == "confirmed_alias"


def test_confirmed_alias_closes_p4_descriptive_name_without_fuzzy_match():
    requirement = _item("plan-p4", "test_plan", "电压波动测试", "ev-plan")
    raw = _item(
        "raw-p4", "original_records", "系统 12V 电源电压波动试验", "ev-raw",
    )
    report = _item("report-p4", "final_report", "P4", "ev-report")

    findings, matches = evaluate_test_item_coverage(
        "run-1", [requirement], [raw], [report], [],
    )

    assert findings[0].status == FindingStatus.CONFIRMED_PASS
    assert matches["plan-p4"]["original_records"].method == "confirmed_alias"
    assert matches["plan-p4"]["final_report"].method == "confirmed_alias"


def test_evidence_gated_llm_override_can_close_identity_without_fuzzy_match():
    requirement = _item("plan-item", "test_plan", "电源线瞬态传导抗扰P2a", "ev-plan")
    raw = _item("raw-item", "original_records", "ISO 7637-2 pulse 2a", "ev-raw")
    report = _item("report-item", "final_report", "P2a transient immunity", "ev-report")

    findings, matches = evaluate_test_item_coverage(
        "run-1", [requirement], [raw], [report], [],
        {
            "original_records": {"plan-item": "raw-item"},
            "final_report": {"plan-item": "report-item"},
        },
    )

    assert findings[0].status == FindingStatus.CONFIRMED_PASS
    assert matches["plan-item"]["original_records"].method == "llm_evidence_rebuttal"


def test_one_observation_cannot_cover_two_plan_requirements():
    requirements = [
        _item("plan-a", "test_plan", "反向电压", "ev-plan-a"),
        _item("plan-b", "test_plan", "反向电压", "ev-plan-b"),
    ]
    observation = _item("raw-one", "original_records", "反向电压", "ev-raw")

    matches = match_test_items(requirements, [observation])

    assert matches["plan-a"].status == "unresolved"
    assert matches["plan-b"].status == "unresolved"
    assert matches["plan-a"].observation_id == ""
    assert "同一文档项目" in matches["plan-a"].rationale


def test_reverse_coverage_reports_item_outside_complete_plan_scope():
    requirement = _item("plan-a", "test_plan", "反向电压", "ev-plan-a")
    raw_items = [
        _item("raw-a", "original_records", "反向电压", "ev-raw-a"),
        _item("raw-extra", "original_records", "开路试验", "ev-raw-extra"),
    ]
    report_items = [
        _item("report-a", "final_report", "反向电压", "ev-report-a"),
        _item("report-extra", "final_report", "开路试验", "ev-report-extra"),
    ]

    findings, _ = evaluate_test_item_coverage(
        "run-1", [requirement], raw_items, report_items, [],
        plan_extraction_complete=True,
    )

    reverse = [item for item in findings if item.check_id == "GRAPH-COVERAGE-002"]
    assert len(reverse) == 1
    assert reverse[0].status == FindingStatus.CONFIRMED_ERROR
    assert set(reverse[0].subject_node_ids) == {"raw-extra", "report-extra"}
    assert set(reverse[0].evidence_ids) == {
        "ev-plan-a", "ev-raw-extra", "ev-report-extra",
    }


def test_reverse_coverage_stays_unresolved_when_plan_scope_is_not_complete():
    requirement = _item("plan-a", "test_plan", "反向电压", "ev-plan-a")
    report_extra = _item(
        "report-extra", "final_report", "开路试验", "ev-report-extra",
    )

    findings, _ = evaluate_test_item_coverage(
        "run-1", [requirement], [], [report_extra], [],
        plan_extraction_complete=False,
    )

    reverse = [item for item in findings if item.check_id == "GRAPH-COVERAGE-002"]
    assert len(reverse) == 1
    assert reverse[0].status == FindingStatus.UNRESOLVED
    assert "计划范围尚未完整" in reverse[0].description


def test_reverse_coverage_ignores_unconsumed_execution_details():
    requirement = _item("plan-a", "test_plan", "瞬态抗扰度试验", "ev-plan-a")
    raw_base = _item(
        "raw-base", "original_records", "瞬态抗扰度试验", "ev-raw-base",
    )
    report_base = _item(
        "report-base", "final_report", "瞬态抗扰度试验", "ev-report-base",
    )
    raw_detail = _item(
        "raw-detail", "original_records", "脉冲2a", "ev-raw-detail",
    )
    report_detail = _item(
        "report-detail", "final_report", "脉冲2a", "ev-report-detail",
    )
    raw_detail.metadata["identity_role"] = "execution_detail"
    report_detail.metadata["identity_role"] = "execution_detail"

    findings, _ = evaluate_test_item_coverage(
        "run-1", [requirement], [raw_base, raw_detail],
        [report_base, report_detail], [], plan_extraction_complete=True,
    )

    assert not [item for item in findings if item.check_id == "GRAPH-COVERAGE-002"]


def test_reverse_coverage_consumes_aggregate_when_all_planned_details_match():
    requirements = [
        _item(f"plan-{pulse}", "test_plan", f"电源线瞬态传导抗扰P{pulse}", f"ev-plan-{pulse}")
        for pulse in ("1", "2a", "2b", "3a")
    ]
    raw_base = _item(
        "raw-base", "original_records", "瞬态抗扰度试验", "ev-raw-base",
    )
    report_base = _item(
        "report-base", "final_report", "瞬态抗扰度试验", "ev-report-base",
    )
    raw_details = []
    report_details = []
    for pulse in ("1", "2a", "2b", "3a"):
        raw = _item(
            f"raw-{pulse}", "original_records", f"脉冲{pulse}", f"ev-raw-{pulse}",
            parent_name="瞬态抗扰度试验",
        )
        report = _item(
            f"report-{pulse}", "final_report", f"脉冲{pulse}", f"ev-report-{pulse}",
            parent_name="脉冲测试",
        )
        raw.metadata["identity_role"] = "execution_detail"
        report.metadata["identity_role"] = "execution_detail"
        raw_details.append(raw)
        report_details.append(report)

    findings, _ = evaluate_test_item_coverage(
        "run-1", requirements, [raw_base, *raw_details],
        [report_base, *report_details], [], plan_extraction_complete=True,
    )

    assert not [item for item in findings if item.check_id == "GRAPH-COVERAGE-002"]
    assert all(
        item.status == FindingStatus.CONFIRMED_PASS
        for item in findings if item.check_id == "GRAPH-COVERAGE-001"
    )


def test_ambiguous_forward_candidates_are_not_misreported_as_plan_extras():
    requirement = _item("plan-a", "test_plan", "反向电压", "ev-plan-a")
    raw_items = [
        _item("raw-a-1", "original_records", "反向电压", "ev-raw-1"),
        _item("raw-a-2", "original_records", "反向电压", "ev-raw-2"),
    ]
    report = _item("report-a", "final_report", "反向电压", "ev-report")

    findings, _ = evaluate_test_item_coverage(
        "run-1", [requirement], raw_items, [report], [],
        plan_extraction_complete=True,
    )

    reverse = [item for item in findings if item.check_id == "GRAPH-COVERAGE-002"]
    assert reverse
    assert all(item.status == FindingStatus.UNRESOLVED for item in reverse)
    assert not any(
        item.status == FindingStatus.CONFIRMED_ERROR
        and "不在测试计划要求范围内" in item.title
        for item in reverse
    )
