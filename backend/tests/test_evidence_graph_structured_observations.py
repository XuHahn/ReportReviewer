import json

from services.evidence_graph_check_adapter import (
    build_claim_nodes_findings_and_audit,
)
from services.evidence_graph_extraction import EvidenceGraphDocumentUnit
from services.evidence_graph_structured_observations import (
    build_structured_check_results,
)


def _unit(doc_type: str, unit_id: str, filename: str, text: str):
    return EvidenceGraphDocumentUnit(
        unit_id=unit_id,
        graph_id="graph-1",
        doc_id=f"doc-{doc_type}",
        doc_type=doc_type,
        filename=filename,
        native_text=text,
        page_number=1,
    )


def _metadata(payload: dict):
    return [{
        "field_name": "__structured__",
        "field_value": json.dumps(payload, ensure_ascii=False),
    }]


def test_structured_raw_record_bridge_runs_calibration_check_with_source_evidence():
    raw_text = (
        "Test Date 2025/11/20\n"
        "设备名称 示波器\n型号 DPO2014B\n编号 SN-001\n校准有效期 2026/02/06"
    )
    units = [_unit("original_records", "original_records:member-1/page-1", "raw-1.pdf", raw_text)]
    metadata = {
        "original_records": _metadata({
            "metas": [{
                "filename": "raw-1.pdf",
                "test_item_name": "反向电压",
                "_header_fields": {"test_date": "2025/11/20"},
            }],
            "instruments": [{
                "source_file": "raw-1.pdf",
                "name": "示波器",
                "model": "DPO2014B",
                "serial_no": "SN-001",
                "calibration_end": "2026/02/06",
            }],
        }),
    }

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"original_records"},
    )
    nodes, findings, audit = build_claim_nodes_findings_and_audit("graph-1", results)

    assert len(results) == 1
    assert results[0].status == "complete"
    assert len(results[0].evidence) == 5
    assert len(nodes) == 6
    assert findings == []
    calibration = next(item for item in audit if item["check_id"] == "INSTRUMENT-CAL-001")
    assert calibration["check_id"] == "INSTRUMENT-CAL-001"
    assert calibration["input_count"] == 1
    assert calibration["finding_count"] == 0
    assert calibration["state"] == "passed"
    assert calibration["expected_input_count"] == 1
    assert calibration["anchored_input_count"] == 1


def test_raw_execution_notes_are_fully_scanned_for_anomaly_conclusion_conflicts():
    units = [_unit(
        "original_records", "original_records:member-1/page-1", "raw-1.pdf",
        "样品编号 S-1 备注 试验中出现异常啸叫 测试结论 符合",
    )]
    metadata = {"original_records": _metadata({
        "metas": [{
            "filename": "raw-1.pdf", "test_item_name": "反向电压",
            "test_mode": "Mode 1",
            "_header_fields": {
                "sample_id": "S-1", "remarks": "试验中出现异常啸叫",
                "test_conclusion": "符合",
            },
        }],
        "instruments": [],
    })}

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"original_records"},
    )
    _, findings, audit = build_claim_nodes_findings_and_audit("graph-1", results)

    anomaly = next(item for item in audit if item["check_id"] == "ANOMALY-CONCLUSION-001")
    assert anomaly["expected_input_count"] == anomaly["anchored_input_count"] == 1
    assert anomaly["state"] == "issues_found"
    assert [item.check_id for item in findings] == ["ANOMALY-CONCLUSION-001"]


def test_class_c_temporary_stop_with_recovery_is_not_an_anomaly_conflict():
    remarks = "试验中电机停止转动；试验后样品自动恢复工作正常。"
    units = [_unit(
        "original_records", "original_records:member-1/page-1", "raw-1.pdf",
        f"样品编号 S-1 备注 {remarks} 测试结论 符合",
    )]
    metadata = {"original_records": _metadata({
        "metas": [{
            "filename": "raw-1.pdf", "test_item_name": "开路试验",
            "test_mode": "Mode 1",
            "_header_fields": {
                "sample_id": "S-1", "remarks": remarks,
                "test_conclusion": "符合",
            },
            "semantic_data": {"tables": [{
                "table_family": "test_data", "rows": [{"cells": [{
                    "semantic": "required_performance_level",
                    "source_value": "C", "normalized_value": "C",
                }]}],
            }]},
        }],
        "instruments": [],
    })}

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"original_records"},
    )
    _, findings, audit = build_claim_nodes_findings_and_audit("graph-1", results)

    anomaly = next(item for item in audit if item["check_id"] == "ANOMALY-CONCLUSION-001")
    assert anomaly["state"] == "passed"
    assert findings == []


def test_judgement_only_report_marks_numeric_matrix_rules_not_applicable():
    units = [_unit(
        "final_report", "final_report:page-1", "report.docx",
        "Test item Reversed voltage Sample No. S-1 Test Mode Mode 1 Result Pass",
    )]
    metadata = {"final_report": _metadata({
        "item_extractions": [{
            "test_item_name": "Reversed voltage",
            "test_results": {"sample_data": [{
                "sample_id": "S-1", "mode": "Mode 1",
                "data_rows": [{"verdict": "Pass"}],
            }]},
        }],
        "results": [], "instruments": [],
    })}

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"final_report"},
    )
    _, _, audit = build_claim_nodes_findings_and_audit("graph-1", results)
    by_id = {item["check_id"]: item for item in audit}

    for check_id in (
        "RESULT-DIMENSION-001", "RESULT-DUPLICATE-001", "SEMANTIC-VARIABLE-001",
    ):
        assert by_id[check_id]["state"] == "not_applicable"


def test_missing_planned_test_also_checks_its_explicit_required_artifact():
    units = [_unit(
        "test_plan", "test_plan:sheet-1", "plan.xlsx",
        "9点性能曲线 在三个温度分别测试，并提供性能曲线",
    )]
    metadata = {
        "test_plan": _metadata({
            "test_items": [{"code": "9点性能曲线", "name": "9点性能曲线"}],
            "test_details": [{
                "code": "9点性能曲线",
                "fields": {"test_level": "在三个温度分别测试，并提供性能曲线"},
            }],
        }),
        "original_records": _metadata({"metas": []}),
        "final_report": _metadata({"item_extractions": []}),
    }

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"test_plan"},
    )
    _, findings, audit = build_claim_nodes_findings_and_audit("graph-1", results)

    evidence_profile = next(
        item for item in audit if item["check_id"] == "EVIDENCE-PROFILE-001"
    )
    assert evidence_profile["state"] == "issues_found"
    assert [item.check_id for item in findings] == ["EVIDENCE-PROFILE-001"]
    assert findings[0].metadata["missing_artifacts"] == ["性能曲线"]


def test_instrument_page_and_test_date_page_are_joined_by_source_file():
    units = [
        _unit(
            "original_records", "original_records:member-1/page-1",
            "raw-1.pdf", "Test Date 2025/11/20",
        ),
        _unit(
            "original_records", "original_records:member-1/page-2",
            "raw-1.pdf",
            "设备名称 示波器 型号 DPO2014B 编号 SN-001 校准有效期 2026/02/06",
        ),
    ]
    metadata = {
        "original_records": _metadata({
            "metas": [{
                "filename": "raw-1.pdf",
                "test_item_name": "反向电压",
                "_header_fields": {"test_date": "2025/11/20"},
            }],
            "instruments": [{
                "source_file": "raw-1.pdf",
                "name": "示波器",
                "model": "DPO2014B",
                "serial_no": "SN-001",
                "calibration_end": "2026/02/06",
            }],
        }),
    }

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"original_records"},
    )
    _, findings, audit = build_claim_nodes_findings_and_audit("graph-1", results)

    assert all(result.status == "complete" for result in results)
    assert findings == []
    calibration = next(item for item in audit if item["check_id"] == "INSTRUMENT-CAL-001")
    assert calibration["input_count"] == 1
    assert calibration["state"] == "passed"


def test_report_instrument_and_execution_method_names_use_confirmed_identity_aliases():
    text = (
        "Single line interruption S-OPEN Mode 1 Test Date 2025/11/20 "
        "Short circuit protection - Signal circuits S-SHORT Mode 1 Test Date 2025/11/21 "
        "Bipolar Power Supply PBZ40-10 SN-OPEN 2026/10/22 "
        "DC Power Supply APS-100 SN-SHORT 2026/10/23"
    )
    units = [_unit("final_report", "final_report:page-1", "report.pdf", text)]
    metadata = {"final_report": _metadata({
        "item_extractions": [
            {
                "test_item_name": "Single line interruption",
                "test_results": {"sample_data": [{
                    "sample_id": "S-OPEN", "mode": "Mode 1", "data_rows": [],
                }]},
            },
            {
                "test_item_name": "Short circuit protection - Signal circuits",
                "test_results": {"sample_data": [{
                    "sample_id": "S-SHORT", "mode": "Mode 1", "data_rows": [],
                }]},
            },
        ],
        "instruments": [
            {
                "test_item": "Open circuit tests", "name": "Bipolar Power Supply",
                "model": "PBZ40-10", "serial_no": "SN-OPEN",
                "calibration_end": "2026/10/22",
            },
            {
                "test_item": "Short circuit pretection", "name": "DC Power Supply",
                "model": "APS-100", "serial_no": "SN-SHORT",
                "calibration_end": "2026/10/23",
            },
        ],
    })}

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"final_report"},
    )
    _, _, audit = build_claim_nodes_findings_and_audit("graph-1", results)

    calibration = next(item for item in audit if item["check_id"] == "INSTRUMENT-CAL-001")
    assert calibration["expected_input_count"] == 2
    assert calibration["anchored_input_count"] == 2
    assert calibration["state"] == "passed"


def test_raw_archive_member_matches_by_stable_filename_identity_after_mojibake():
    structured_name = (
        "E202508277046_ Σ╛¢τö╡_0007_Mode 1_σÄƒσºï_20251203020027.pdf"
    )
    rendered_name = (
        "E202508277046_ 渚涚數_0007_Mode 1_鍘熷_20251203020027.pdf"
    )
    units = [
        _unit(
            "original_records", "original_records:member-1/page-1",
            rendered_name, "Test Date 2025/11/20",
        ),
        _unit(
            "original_records", "original_records:member-1/page-2",
            rendered_name,
            "设备名称 示波器 型号 DPO2014B 编号 SN-001 校准有效期 2026/02/06",
        ),
    ]
    metadata = {
        "original_records": _metadata({
            "metas": [{
                "filename": structured_name,
                "_header_fields": {"test_date": "2025/11/20"},
            }],
            "instruments": [{
                "source_file": structured_name,
                "name": "示波器",
                "model": "DPO2014B",
                "serial_no": "SN-001",
                "calibration_end": "2026/02/06",
            }],
        }),
    }

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"original_records"},
    )

    assert all(result.status == "complete" for result in results)
    assert {result.unit_id for result in results} == {
        "original_records:member-1/page-1",
        "original_records:member-1/page-2",
    }


def test_structured_bridge_marks_unanchored_required_instrument_evidence_incomplete():
    units = [_unit(
        "original_records", "original_records:member-1/page-1",
        "raw-1.pdf", "设备名称 示波器",
    )]
    metadata = {
        "original_records": _metadata({
            "metas": [{"filename": "raw-1.pdf", "_header_fields": {}}],
            "instruments": [{
                "source_file": "raw-1.pdf",
                "name": "示波器",
                "model": "DPO2014B",
                "serial_no": "SN-001",
                "calibration_end": "2026/02/06",
            }],
        }),
    }

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"original_records"},
    )

    assert len(results) == 1
    assert results[0].status == "needs_review"
    assert results[0].observations == []
    assert results[0].errors == ["仪器记录无法回锚到原始文件"]


def test_structured_bridge_only_builds_selected_document_types():
    units = [
        _unit("original_records", "original_records:member-1/page-1", "raw.pdf", "原始记录"),
        _unit("final_report", "final_report:page-1", "report.docx", "项目A S1 Mode 1 Pass"),
    ]
    metadata = {
        "original_records": _metadata({"instruments": []}),
        "final_report": _metadata({
            "item_extractions": [{
                "test_item_name": "项目A",
                "test_results": {"sample_data": [{
                    "sample_id": "S1",
                    "mode": "Mode 1",
                    "data_rows": [{"verdict": "Pass"}],
                }]},
            }],
        }),
    }

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"final_report"},
    )

    assert len(results) == 1
    assert {item.metadata["doc_type"] for item in results[0].observations} == {"final_report"}


def test_report_bridge_recovers_blank_verdict_from_same_sample_mode_window():
    units = [_unit(
        "final_report", "final_report:page-48", "report.docx",
        "Test Mode Mode 1\nSample No. S-001\nStartup feature 10 cycles A A Pass",
    )]
    metadata = {"final_report": _metadata({
        "results": [{"test_item": "Startup feature", "result": "Pass"}],
        "item_extractions": [{
            "test_item_name": "STARTUP FEATURE",
            "test_results": {"sample_data": [{
                "sample_id": "S-001", "mode": "Mode 1",
                "data_rows": [{"verdict": ""}],
            }]},
        }],
    })}

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"final_report"},
    )
    _, findings, audit = build_claim_nodes_findings_and_audit("graph-1", results)

    recovered = next(
        observation for result in results for observation in result.observations
        if observation.field_name == "detail_result"
    )
    assert recovered.raw_value == "Pass"
    assert recovered.metadata["verdict_recovered"] is True
    assert not [item for item in findings if item.check_id == "RESULT-EMPTY-001"]
    summary_check = next(item for item in audit if item["check_id"] == "RESULT-EMPTY-001")
    assert summary_check["check_id"] == "RESULT-EMPTY-001"
    assert summary_check["input_count"] == 1
    assert summary_check["finding_count"] == 0
    assert summary_check["state"] == "passed"
    assert summary_check["expected_input_count"] == 1
    assert summary_check["anchored_input_count"] == 1


def test_report_bridge_does_not_recover_ambiguous_pass_fail_window():
    units = [_unit(
        "final_report", "final_report:page-57", "report.docx",
        "Test Mode Mode 2\nSample No. S-001\nPass\n不 Pass",
    )]
    metadata = {"final_report": _metadata({
        "results": [{"test_item": "Reversed voltage", "result": "Fail"}],
        "item_extractions": [{
            "test_item_name": "REVERSED VOLTAGE",
            "test_results": {"sample_data": [{
                "sample_id": "S-001", "mode": "Mode 2",
                "data_rows": [{"verdict": ""}],
            }]},
        }],
    })}

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"final_report"},
    )

    assert not [
        observation for result in results for observation in result.observations
        if observation.field_name == "detail_result"
    ]


def test_report_bridge_preserves_repeated_verdict_rows_on_the_same_page():
    units = [_unit(
        "final_report", "final_report:page-14", "report.docx",
        "Voltage test\nTest Mode Mode 1\nSample No. S-001\nCondition A Pass\nCondition B Pass",
    )]
    metadata = {"final_report": _metadata({
        "results": [{"test_item": "Voltage test", "result": "Pass"}],
        "item_extractions": [{
            "test_item_name": "Voltage test",
            "test_results": {"sample_data": [{
                "sample_id": "S-001", "mode": "Mode 1",
                "data_rows": [
                    {"verdict": "Pass", "spec_requirement": "Condition A"},
                    {"verdict": "Pass", "spec_requirement": "Condition B"},
                ],
            }]},
        }],
    })}

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"final_report"},
    )
    details = [
        observation for result in results for observation in result.observations
        if observation.field_name == "detail_result"
    ]
    _, _, audit = build_claim_nodes_findings_and_audit("graph-1", results)
    result_check = next(item for item in audit if item["check_id"] == "RESULT-EMPTY-001")

    assert len(details) == 2
    assert len({item.observation_id for item in details}) == 2
    assert result_check["expected_input_count"] == 1
    assert result_check["anchored_input_count"] == 1
    assert result_check["state"] == "passed"


def test_report_bridge_marks_unanchored_result_row_system_incomplete():
    units = [_unit(
        "final_report", "final_report:page-14", "report.docx",
        "Test Mode Mode 1\nSample No. S-001\nCondition A Pass",
    )]
    metadata = {"final_report": _metadata({
        "results": [{"test_item": "Voltage test", "result": "Pass"}],
        "item_extractions": [{
            "test_item_name": "Voltage test",
            "test_results": {"sample_data": [{
                "sample_id": "S-002", "mode": "Mode 2",
                "data_rows": [{"verdict": "Pass"}],
            }]},
        }],
    })}

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"final_report"},
    )
    _, _, audit = build_claim_nodes_findings_and_audit("graph-1", results)
    result_check = next(item for item in audit if item["check_id"] == "RESULT-EMPTY-001")

    assert any(result.status == "needs_review" for result in results)
    assert result_check["expected_input_count"] == 1
    assert result_check["anchored_input_count"] == 0
    assert result_check["state"] == "system_incomplete"


def test_report_bridge_treats_missing_item_inventory_as_extraction_gap_not_empty_report():
    units = [_unit(
        "final_report", "final_report:page-4", "report.docx",
        "电源电压瞬间降低试验 符合",
    )]
    metadata = {"final_report": _metadata({
        "results": [{"test_item": "电源电压瞬间降低试验", "result": "符合"}],
        "item_extractions": [],
        "failed_passes": ["item/sample_coverage_missing:3"],
        "extraction_metrics": {
            "source_sample_count": 3,
            "extracted_sample_count": 0,
            "missing_sample_count": 3,
        },
    })}

    results = build_structured_check_results(
        "graph-1", units, metadata, doc_types={"final_report"},
    )
    _, findings, audit = build_claim_nodes_findings_and_audit("graph-1", results)

    assert not [item for item in findings if item.check_id == "RESULT-EMPTY-001"]
    result_check = next(item for item in audit if item["check_id"] == "RESULT-EMPTY-001")
    assert result_check["state"] == "system_incomplete"
    assert result_check["expected_input_count"] == 4
    assert result_check["anchored_input_count"] == 1
    assert result_check["reason_code"] == "report_item_or_sample_results_not_fully_extracted"
