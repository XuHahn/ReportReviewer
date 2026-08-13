import json

from services.evidence_graph_document_checks import (
    _visible_count_anchor,
    _visible_phrase_boxes,
    build_reviewed_document_checks,
    build_reviewed_document_checks_and_audit,
)
from services.evidence_graph_extraction import EvidenceGraphDocumentUnit


def _rows(value):
    return [{"field_name": "__structured__", "field_value": json.dumps(value, ensure_ascii=False)}]


def _unit(doc_type, text):
    return EvidenceGraphDocumentUnit(
        unit_id=f"{doc_type}:1", graph_id="graph-1", doc_id=f"doc-{doc_type}",
        doc_type=doc_type, filename=f"{doc_type}.pdf", native_text=text,
    )


def _documents():
    return {
        doc_type: {"doc_id": f"doc-{doc_type}", "filename": f"{doc_type}.pdf"}
        for doc_type in ("order_form", "test_plan", "original_records", "final_report")
    }


def _metadata(report_sample="雾灯", raw_date="2026/6/2", issue_date=""):
    return {
        "order_form": _rows({
            "applicant": {"name_cn": "甲公司", "address_cn": "甲地址"},
            "product": {"name": "雾灯", "main_test_model": "703F", "voltage": "14V"},
        }),
        "test_plan": _rows({"test_items": []}),
        "original_records": _rows({"metas": [{"_header_fields": {
            "sample_name": "雾灯", "sample_model": "703F", "power_supply": "DC14.0V",
            "sample_id": "S-001", "test_date": raw_date,
        }}]}),
        "final_report": _rows({
            "cover": {
                "client_name": "甲公司", "client_address": "甲地址",
                "sample_name": report_sample, "sample_model": "703F",
                "test_date_range": "2026/6/2~2026/6/2",
                "issue_date": issue_date,
            },
            "sample": {"rated_voltage": "DC 14V", "lab_sample_ids": ["S-001"]},
        }),
    }


def _units(report_sample="雾灯", raw_date="2026/6/2", placeholder=False,
           blank_signatures=False, extra_report_text=""):
    report_number = "\n报告编号：Report 报告编号" if placeholder else ""
    signatures = "\n编制：\n\n审核：\n\n批准：" if blank_signatures else ""
    return [
        _unit("order_form", "甲公司 甲地址 雾灯 703F 14V"),
        _unit("test_plan", "计划"),
        _unit("original_records", f"雾灯 703F DC14.0V S-001 {raw_date}"),
        _unit(
            "final_report",
            f"甲公司 甲地址 {report_sample} 703F DC 14V S-001 2026/6/2~2026/6/2{report_number}{signatures}{extra_report_text}",
        ),
    ]


def test_reviewed_document_checks_accept_consistent_cross_document_facts():
    evidence, nodes, findings = build_reviewed_document_checks(
        "graph-1", _documents(), _units(), _metadata(),
    )

    assert evidence
    assert nodes
    assert findings == []


def test_reviewed_short_field_value_keeps_unique_labeled_layout_anchor():
    units = _units()
    target = units[0]
    target.source_hash = "c" * 64
    target.rendered_pdf_hash = "d" * 64
    target.page_width = 595
    target.page_height = 842
    target.layout_lines = [
        {"text": "样品名称：雾灯", "bbox": [70, 110, 210, 132]},
        {"text": "其他字段：雾灯", "bbox": [70, 150, 210, 172]},
    ]

    evidence, _, _ = build_reviewed_document_checks(
        "graph-1", _documents(), units, _metadata(),
    )

    record = next(
        item for item in evidence
        if item.doc_type == "order_form"
        and item.metadata.get("field_name") == "sample_name"
    )
    assert record.exact_quote == "雾灯"
    assert record.bbox == [70.0, 110.0, 210.0, 132.0]
    assert record.content_hash == "c" * 64
    assert record.metadata["source_anchor"]["anchor_quote"] == "样品名称：雾灯"


def test_missing_field_anchors_label_and_blank_region_without_following_lines():
    units = _units(extra_report_text=(
        "\nIssued Date:\nGRG METROLOGY & TEST GROUP CO., LTD.\nAddress: No.8 Road"
    ))
    report = units[3]
    report.page_number = 1
    report.source_hash = "a" * 64
    report.rendered_pdf_hash = "b" * 64
    report.page_width = 595
    report.page_height = 842
    report.layout_lines = [
        {"text": "Issued Date:", "bbox": [397, 665, 449, 677]},
        {"text": "GRG METROLOGY & TEST GROUP CO., LTD.", "bbox": [175, 726, 421, 739]},
        {"text": "Address: No.8 Road", "bbox": [136, 740, 260, 752]},
    ]

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, _metadata(),
    )

    finding = next(item for item in findings if item.check_id == "DOC-TIMELINE-002")
    record = next(item for item in evidence if item.evidence_id in finding.evidence_ids)
    assert record.exact_quote == "Issued Date:"
    assert record.bbox == [395.0, 663.0, 571.2, 679.0]
    assert record.content_hash == "a" * 64
    assert record.metadata["role"] == "missing_field"
    assert record.metadata["source_anchor"]["kind"] == "missing_field"
    assert record.metadata["source_anchor"]["rectangles"] == [record.bbox]


def test_missing_field_does_not_turn_visually_present_value_into_error():
    units = _units(extra_report_text="\nIssued Date: 2026-06-02")
    report = units[3]
    report.page_number = 1
    report.page_width = 595
    report.page_height = 842
    report.layout_lines = [
        {"text": "Issued Date: 2026-06-02", "bbox": [397, 665, 535, 677]},
    ]

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, _metadata(),
    )

    assert not [item for item in findings if item.check_id == "DOC-TIMELINE-002"]


def test_missing_field_treats_visible_template_token_as_unfilled_value():
    units = _units(extra_report_text="\nTest Plan No.:\nxxxxxx")
    report = units[3]
    report.page_number = 1
    report.page_width = 595
    report.page_height = 842
    report.layout_lines = [
        {"text": "Test Plan No.:", "bbox": [90, 179, 151, 194]},
        {"text": "xxxxxx", "bbox": [175, 179, 206, 194]},
    ]

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, _metadata(),
    )

    finding = next(item for item in findings if item.check_id == "DOC-PLAN-REF-001")
    record = next(item for item in evidence if item.evidence_id in finding.evidence_ids)
    assert record.exact_quote == "Test Plan No.:"
    assert record.bbox == [88.0, 177.0, 210.0, 196.0]
    assert record.metadata["source_anchor"]["kind"] == "missing_field"


def test_plan_declared_clause_must_match_detailed_instruction_clause():
    metadata = _metadata()
    metadata["test_plan"] = _rows({
        "test_items": [{
            "code": "启动实验", "name": "启动实验",
            "standard_clause": "ISO-16750-2-4.6.3",
        }],
        "test_details": [{
            "code": "启动实验",
            "fields": {
                "ref_doc": "ISO-16750-2-4.6.3",
                "test_level": "按照ISO-16750-2-4.6.2.3执行",
            },
        }],
    })
    units = _units()
    units[1].native_text = (
        "启动实验 ISO-16750-2-4.6.3 "
        "按照ISO-16750-2-4.6.2.3执行"
    )

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    finding = next(
        item for item in findings
        if item.check_id == "PLAN-CLAUSE-CONSISTENCY-001"
    )
    assert finding.status.value == "confirmed_error"
    assert finding.metadata["declared_value"] == "ISO-16750-2-4.6.3"
    assert finding.metadata["observed_values"] == ["ISO-16750-2-4.6.2.3"]


def test_reviewed_document_checks_compare_repeated_sample_ids_as_sets():
    metadata = _metadata()
    raw = json.loads(metadata["original_records"][0]["field_value"])
    raw["metas"].append({"_header_fields": {
        "sample_name": "雾灯", "sample_model": "703F", "power_supply": "DC14.0V",
        "sample_id": "S-002", "test_date": "2026/6/2",
    }})
    metadata["original_records"] = _rows(raw)
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["sample"]["lab_sample_ids"] = ["S-001", "S-002"]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text=" S-002")
    units[2].native_text += " S-002"

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    assert findings == []


def test_repeated_sample_id_mismatch_exposes_compact_set_difference_metadata():
    metadata = _metadata()
    raw = json.loads(metadata["original_records"][0]["field_value"])
    raw["metas"].append({"_header_fields": {
        "sample_name": "雾灯", "sample_model": "703F", "power_supply": "DC14.0V",
        "sample_id": "S-002", "test_date": "2026/6/2",
    }})
    metadata["original_records"] = _rows(raw)
    units = _units()
    units[2].native_text += " S-002"

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    finding = next(
        item for item in findings
        if item.check_id == "DOC-CROSS-FIELD-001"
        and item.metadata["field_name"] == "sample_id"
    )
    assert finding.metadata["values_by_doc_type"] == {
        "original_records": ["s-001", "s-002"],
        "final_report": ["s-001"],
    }
    assert finding.metadata["common_values"] == ["s-001"]
    assert finding.metadata["differing_values_by_doc_type"] == {
        "original_records": ["s-002"],
        "final_report": [],
    }


def test_reviewed_document_checks_find_conflict_and_report_placeholder():
    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), _units("尾灯", placeholder=True), _metadata("尾灯"),
    )

    assert {finding.check_id for finding in findings} == {
        "DOC-CROSS-FIELD-001", "DOC-REQUIRED-FIELD-001",
    }
    assert all(finding.evidence_ids for finding in findings)
    name_finding = next(item for item in findings if item.check_id == "DOC-CROSS-FIELD-001")
    assert name_finding.status.value == "unresolved_advisory"


def test_reviewed_document_checks_do_not_confirm_cross_language_client_identity_as_error():
    metadata = _metadata()
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["cover"]["client_name"] = "Company A Ltd."
    report["cover"]["client_address"] = "No. 1 Road"
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text=" Company A Ltd. No. 1 Road")

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    client_findings = [
        item for item in findings
        if item.check_id == "DOC-CROSS-FIELD-001" and "委托单位" in item.title
    ]
    assert len(client_findings) == 2
    assert all(item.status.value == "unresolved_advisory" for item in client_findings)


def test_reviewed_document_checks_find_execution_outside_report_range():
    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), _units(raw_date="2026/6/3"), _metadata(raw_date="2026/6/3"),
    )

    assert [finding.check_id for finding in findings] == ["DOC-TIMELINE-001"]


def test_reviewed_document_checks_require_contiguous_blank_signature_labels():
    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), _units(blank_signatures=True), _metadata(),
    )

    assert [finding.title for finding in findings] == ["检测报告编制、审核和批准信息为空"]


def test_reviewed_document_checks_support_english_report_placeholders_and_signatures():
    units = _units(extra_report_text=(
        "\nReport No.: Report 报告编号\n"
        "Prepared by:\nReviewed by:\nApproved by:"
    ))
    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, _metadata(),
    )

    assert {finding.check_id for finding in findings} == {
        "DOC-REQUIRED-FIELD-001", "DOC-SIGNATURE-001",
    }


def test_reviewed_document_checks_flag_toc_page_beyond_document_end():
    units = _units(extra_report_text=(
        "\nAPPENDIX A: PHOTOGRAPH OF THE DUT"
        "................................................85"
    ))
    for unit in units:
        unit.page_number = 1
    units[-1].page_number = 84

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, _metadata(),
    )

    pagination = [item for item in findings if item.check_id == "DOC-PAGINATION-001"]
    assert len(pagination) == 1
    assert "只有 84 页" in pagination[0].description


def test_reviewed_document_checks_flag_same_execution_pass_fail_conflict():
    metadata = _metadata()
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["item_extractions"] = [{
        "test_item_name": "Reversed voltage",
        "spec_parameters": [],
        "test_results": {"sample_data": [{
            "sample_id": "S-001", "mode": "Mode 2", "data_rows": [
                {"test_item": "Reversed voltage", "injection_point": "power supply",
                 "spec_requirement": "14V", "test_duration": "60s",
                 "required_level": "C", "actual_level": "C1", "verdict": "Pass"},
                {"test_item": "Reversed voltage", "injection_point": "power supply",
                 "spec_requirement": "14V", "test_duration": "60s",
                 "required_level": "A", "actual_level": "C1", "verdict": "不Pass"},
            ],
        }]},
    }]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text=(
        "\nTest Mode\nMode 2\nSample No.\nS-001\nReversed voltage\nPass\n"
        "Test Mode\nMode 2\nSample No.\nS-001\nReversed voltage\n不\nPass"
    ))

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    conflicts = [item for item in findings if item.check_id == "REPORT-RESULT-CONFLICT-001"]
    assert len(conflicts) == 1
    conflict_evidence = [
        item for item in evidence if item.evidence_id in conflicts[0].evidence_ids
    ]
    assert len(conflict_evidence) == 2
    assert [
        row
        for item in conflict_evidence
        for row in item.metadata["comparison_rows"]
    ] == [
        {
            "doc_type": "final_report", "test_item": "Reversed voltage",
            "sample_id": "S-001", "mode": "Mode 2", "injection_point": "power supply",
            "spec_requirement": "14V", "test_duration": "60s",
            "required_level": "C", "actual_level": "C1", "verdict_raw": "Pass",
            "verdict": "pass",
        },
        {
            "doc_type": "final_report", "test_item": "Reversed voltage",
            "sample_id": "S-001", "mode": "Mode 2", "injection_point": "power supply",
            "spec_requirement": "14V", "test_duration": "60s",
            "required_level": "A", "actual_level": "C1", "verdict_raw": "不Pass",
            "verdict": "fail",
        },
    ]
    assert conflicts[0].title == "Reversed voltage同一次执行使用不同要求等级，导致结果相反"
    assert "要求等级分别为 C、A" in conflicts[0].description


def test_reviewed_document_checks_compare_duplicate_execution_blocks():
    metadata = _metadata()
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["item_extractions"] = [{
        "test_item_name": "Reversed voltage", "required_level": "A",
        "test_results": {"sample_data": [
            {"sample_id": "S-001", "mode": "Mode 2", "data_rows": [{
                "test_item": "Reversed voltage", "injection_point": "power supply",
                "spec_requirement": "14V", "test_duration": "60s",
                "required_level": "C", "actual_level": "C1", "verdict": "Pass",
            }]},
            {"sample_id": "S-001", "mode": "Mode 2", "data_rows": [{
                "test_item": "Reversed voltage", "injection_point": "power supply",
                "spec_requirement": "14V", "test_duration": "60s",
                "required_level": "A", "actual_level": "C1", "verdict": "不Pass",
            }]},
        ]},
    }]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text=(
        "\nTest Mode\nMode 2\nSample No.\nS-001\n"
        "Reversed voltage\npower supply\n14V\n60s\nC\nC1\nPass\n"
        "Test Mode\nMode 2\nSample No.\nS-001\n"
        "Reversed voltage\npower supply\n14V\n60s\nA\nC1\n不\nPass"
    ))

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    conflicts = [
        item for item in findings if item.check_id == "REPORT-RESULT-CONFLICT-001"
    ]
    assert len(conflicts) == 1
    assert conflicts[0].metadata["required_levels"] == ["A", "C"]
    assert len(conflicts[0].metadata["comparison_rows"]) == 2


def test_performance_level_better_than_required_is_audited_without_finding():
    metadata = _metadata()
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["item_extractions"] = [{
        "test_item_name": "Voltage dip",
        "test_results": {"sample_data": [{
            "sample_id": "S-001", "mode": "Mode 1", "data_rows": [{
                "test_item": "Voltage dip", "spec_requirement": "12V",
                "test_duration": "10s", "required_level": "C",
                "actual_level": "A", "verdict": "Pass",
            }],
        }]},
    }]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text=(
        "\nSample No. S-001\nMode 1\nVoltage dip\n12V\n10s\nC\nA\nPass"
    ))

    evidence, nodes, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    assert not any(
        item.check_id == "REPORT-SPEC-RESULT-001" for item in findings
    )
    comparison = next(
        item for item in nodes
        if item.canonical_key == "performance_criteria_vs_actual_performance"
    )
    assert comparison.properties["claim_type"] == "performance_level_meets_requirement"
    assert comparison.properties["raw_value"] == "required=C;actual=A"
    assert any(
        item.extraction_method == "deterministic_performance_level_check"
        for item in evidence
    )


def test_cross_document_comparison_skips_ambiguous_report_verdict_set():
    metadata = _metadata()
    raw = json.loads(metadata["original_records"][0]["field_value"])
    raw["metas"][0].update({
        "test_item_name": "反向电压",
        "test_mode": "Mode 2",
        "semantic_data": {"tables": [{
            "table_family": "test_data",
            "rows": [{
                "cells": [{"semantic": "verdict", "source_value": "符合"}],
                "evidence": "反向电压 电源线 14V 60s C C 符合",
            }],
        }]},
    })
    raw["metas"][0]["_header_fields"]["test_item_pdf"] = "反向电压"
    raw["metas"][0]["_header_fields"]["test_mode"] = "Mode 2"
    metadata["original_records"] = _rows(raw)
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["item_extractions"] = [{
        "test_item_name": "Reversed voltage",
        "spec_parameters": [],
        "test_results": {"sample_data": [{
            "sample_id": "S-001", "mode": "Mode 2", "data_rows": [
                {"verdict": "Pass"}, {"verdict": "不Pass"},
            ],
        }]},
    }]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text=(
        "\nTest Mode\nMode 2\nSample No.\nS-001\nReversed voltage\nPass\n不Pass"
    ))
    units[2].native_text += " Mode 2 反向电压 电源线 14V 60s C C 符合"

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    consistency = [item for item in findings if item.check_id == "DOC-RESULT-CONSISTENCY-001"]
    conflicts = [item for item in findings if item.check_id == "REPORT-RESULT-CONFLICT-001"]
    assert consistency == []
    assert len(conflicts) == 1
    assert len(conflicts[0].evidence_ids) == 3


def test_execution_verdict_evidence_uses_matching_raw_sample_and_mode_unit():
    metadata = _metadata()
    raw = json.loads(metadata["original_records"][0]["field_value"])
    raw["metas"][0].update({
        "test_item_name": "反向电压", "test_mode": "Mode 2",
        "semantic_data": {"tables": [{
            "table_family": "test_data",
            "rows": [{
                "cells": [{"semantic": "verdict", "source_value": "符合"}],
                "evidence": "反向电压 14V 60s 符合",
            }],
        }]},
    })
    raw["metas"][0]["_header_fields"].update({
        "test_item_pdf": "反向电压", "test_mode": "Mode 2",
    })
    metadata["original_records"] = _rows(raw)
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["item_extractions"] = [{
        "test_item_name": "Reversed voltage", "spec_parameters": [],
        "test_results": {"sample_data": [{
            "sample_id": "S-001", "mode": "Mode 2",
            "data_rows": [{"verdict": "不Pass"}],
        }]},
    }]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text=(
        "\nTest Mode\nMode 2\nSample No.\nS-001\nReversed voltage\n不Pass"
    ))
    units[2].unit_id = "original_records:member-1/page-1"
    units[2].native_text = "S-001 Mode 1 反向电压 14V 60s 符合"
    units.insert(3, EvidenceGraphDocumentUnit(
        unit_id="original_records:member-2/page-1", graph_id="graph-1",
        doc_id="doc-original_records", doc_type="original_records",
        filename="mode-2.pdf",
        native_text="S-001 Mode 2 反向电压 14V 60s 符合",
    ))

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    consistency = next(item for item in findings if item.check_id == "DOC-RESULT-CONSISTENCY-001")
    raw_evidence = next(
        item for item in evidence
        if item.evidence_id in consistency.evidence_ids and item.doc_type == "original_records"
    )
    assert raw_evidence.metadata["unit_id"] == "original_records:member-2/page-1"


def test_reviewed_document_checks_flag_named_spec_result_value_mismatch():
    metadata = _metadata()
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["item_extractions"] = [{
        "test_item_name": "Ground reference and supply offset",
        "spec_parameters": [{"参数名": "Offset voltage", "参数值": "±1.5V"}],
        "test_results": {"sample_data": [{
            "sample_id": "S-001", "mode": "Mode 1", "data_rows": [{
                "test_item": "Ground reference and supply offset",
                "spec_requirement": "Offset voltage ±1V", "verdict": "Pass",
            }],
        }]},
    }]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text="\nOffset voltage ±1.5V\nOffset voltage ±1V")

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    mismatches = [item for item in findings if item.check_id == "REPORT-SPEC-RESULT-001"]
    assert len(mismatches) == 1
    assert "±1.5V" in mismatches[0].description
    assert mismatches[0].metadata == {
        "comparison_kind": "specification_vs_result",
        "parameter_name": "Offset voltage",
        "declared_value": "±1.5V",
        "observed_values": ["±1V"],
        "declared_evidence_id": mismatches[0].evidence_ids[0],
        "observed_evidence_ids": [mismatches[0].evidence_ids[1]],
        "raw_record_anchored": False,
        "raw_evidence_ids": [],
        "raw_observed_values": [],
    }
    compared = [item for item in evidence if item.evidence_id in mismatches[0].evidence_ids]
    assert [item.metadata["role"] for item in compared] == ["declared", "observed"]
    assert [item.metadata["role_label"] for item in compared] == [
        "规范要求", "结果记录实际采用",
    ]
    assert all(item.metadata["parameter_name"] == "Offset voltage" for item in compared)


def test_named_spec_result_mismatch_adds_same_item_raw_record_evidence():
    metadata = _metadata()
    raw = json.loads(metadata["original_records"][0]["field_value"])
    raw["metas"][0].update({
        "test_item_name": "Ground reference and supply offset",
        "semantic_data": {"tables": [{
            "table_family": "test_data",
            "rows": [{
                "cells": [
                    {"semantic": "test_specification", "source_value": "Offset voltage ±1V"},
                ],
                "evidence": "Ground reference and supply offset Offset voltage ±1V Pass",
            }],
        }]},
    })
    metadata["original_records"] = _rows(raw)
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["item_extractions"] = [{
        "test_item_name": "Ground reference and supply offset",
        "spec_parameters": [{"参数名": "Offset voltage", "参数值": "±1.5V"}],
        "test_results": {"sample_data": [{
            "sample_id": "S-001", "mode": "Mode 1", "data_rows": [{
                "spec_requirement": "Offset voltage ±1V", "verdict": "Pass",
            }],
        }]},
    }]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text="\nOffset voltage ±1.5V\nOffset voltage ±1V")
    units[2].native_text += (
        " Ground reference and supply offset Offset voltage ±1V Pass"
    )

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    mismatch = next(item for item in findings if item.check_id == "REPORT-SPEC-RESULT-001")
    assert mismatch.metadata["raw_record_anchored"] is True
    assert mismatch.metadata["raw_observed_values"] == ["±1V"]
    raw_evidence = [
        item for item in evidence
        if item.evidence_id in mismatch.evidence_ids
        and item.doc_type == "original_records"
    ]
    assert len(raw_evidence) == 1
    assert raw_evidence[0].metadata["role"] == "corroborating"


def test_reviewed_document_checks_preserve_each_distinct_result_value_in_comparison():
    metadata = _metadata()
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["item_extractions"] = [{
        "test_item_name": "Supply offset",
        "spec_parameters": [{"参数名": "Offset voltage", "参数值": "±1.5V"}],
        "test_results": {"sample_data": [{
            "sample_id": "S-001", "data_rows": [
                {"spec_requirement": "Offset voltage ±1V", "verdict": "Pass"},
                {"spec_requirement": "Offset voltage ±2V", "verdict": "Pass"},
            ],
        }]},
    }]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text="\nOffset voltage ±1.5V\nOffset voltage ±1V\nOffset voltage ±2V")

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    mismatch = next(item for item in findings if item.check_id == "REPORT-SPEC-RESULT-001")
    assert mismatch.metadata["observed_values"] == ["±1V", "±2V"]
    assert len(mismatch.evidence_ids) == 3
    assert [
        item.metadata["role"] for item in evidence if item.evidence_id in mismatch.evidence_ids
    ] == ["declared", "observed", "observed"]


def test_reviewed_document_checks_find_issue_date_before_last_execution():
    units = _units(raw_date="2026/6/3", extra_report_text=" 签发日期 2026/6/2")
    metadata = _metadata(raw_date="2026/6/3", issue_date="2026/6/2")
    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    assert {finding.check_id for finding in findings} == {
        "DOC-TIMELINE-001", "DOC-TIMELINE-002",
    }


def test_reviewed_document_checks_flag_malformed_standard_reference():
    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(),
        _units(extra_report_text=" 依据 EQCS-1296-20236.7.7 执行"), _metadata(),
    )

    assert [finding.check_id for finding in findings] == ["DOC-REFERENCE-001"]
    assert findings[0].status.value == "confirmed_advisory"


def test_reviewed_document_checks_flag_standard_prefix_variants_but_normalize_q_prefix():
    units = _units(extra_report_text=" EQC-1296-2023 EQCS-1296-2023 Q/EQCS-1296-2023")
    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, _metadata(),
    )

    reference_findings = [item for item in findings if item.check_id == "DOC-REFERENCE-001"]
    assert len(reference_findings) == 1
    assert "EQC-1296-2023" in reference_findings[0].description
    assert "EQCS-1296-2023" in reference_findings[0].description


def test_reviewed_document_checks_compare_explicit_plan_reference_and_sample_count():
    metadata = _metadata()
    plan = json.loads(metadata["test_plan"][0]["field_value"])
    plan["basic_info"] = {"plan_number": "PLAN-A", "sample_count": "2个"}
    metadata["test_plan"] = _rows(plan)
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["cover"]["test_plan_number"] = "PLAN-B"
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text=" PLAN-B")
    units[1].native_text = "PLAN-A 2个"

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    assert {finding.check_id for finding in findings} == {
        "DOC-PLAN-REF-001", "GRAPH-SAMPLE-001",
    }


def _per_item_sample_metadata():
    metadata = _metadata()
    plan = json.loads(metadata["test_plan"][0]["field_value"])
    plan["test_items"] = [{
        "code": "电压波动测试",
        "name": "电压波动测试",
        "sample_requirements": {"DV": "4"},
    }]
    metadata["test_plan"] = _rows(plan)
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["item_extractions"] = [{
        "test_item_code": "电压波动测试",
        "test_item_name": "电压波动测试",
        "test_results": {
            "sample_data": [{"sample_id": "S-001", "data_rows": []}],
        },
    }]
    metadata["final_report"] = _rows(report)
    return metadata


def test_per_item_sample_count_rejects_digit_inside_pulse_or_voltage():
    units = _units()
    plan_unit = units[1]
    plan_unit.page_number = 5
    plan_unit.native_text = "电压波动测试 P4 12V DV"
    plan_unit.layout_lines = [
        {"text": "电压波动测试", "bbox": [80, 320, 125, 332]},
        {"text": "试验脉冲P4", "bbox": [140, 320, 220, 332]},
        {"text": "12V系统", "bbox": [140, 334, 190, 346]},
        {"text": "DV", "bbox": [430, 320, 445, 332]},
    ]
    report_unit = units[3]
    report_unit.page_number = 15
    report_unit.native_text += " 电压波动测试 S-001"
    report_unit.layout_lines = [
        {"text": "电压波动测试", "bbox": [60, 330, 130, 345]},
        {"text": "Test Date", "bbox": [60, 200, 120, 215]},
        {"text": "样品编号", "bbox": [310, 200, 365, 215]},
        {"text": "S-001", "bbox": [380, 200, 430, 215]},
    ]

    _, _, findings, audit = build_reviewed_document_checks_and_audit(
        "graph-1", _documents(), units, _per_item_sample_metadata(),
    )

    assert not [item for item in findings if item.check_id == "GRAPH-SAMPLE-001"]
    execution = next(item for item in audit if item["check_id"] == "GRAPH-SAMPLE-001")
    assert execution["state"] == "system_incomplete"
    assert execution["anchored_input_count"] == 0
    assert execution["reason_code"] == "plan_item_sample_count_not_visually_anchored"


def test_per_item_sample_count_uses_visible_plan_count_and_item_local_report_id():
    units = _units()
    plan_unit = units[1]
    plan_unit.page_number = 5
    plan_unit.source_hash = "a" * 64
    plan_unit.rendered_pdf_hash = "b" * 64
    plan_unit.native_text = "电压波动测试 4 DV"
    plan_unit.layout_lines = [
        {"text": "电压波动测试", "bbox": [80, 320, 125, 332]},
        {"text": "4", "bbox": [650, 320, 660, 332]},
        {"text": "DV", "bbox": [430, 320, 445, 332]},
    ]
    global_report = units[3]
    global_report.page_number = 6
    global_report.native_text += " S-001"
    global_report.layout_lines = [
        {"text": "样品编号", "bbox": [310, 200, 365, 215]},
        {"text": "S-001", "bbox": [380, 200, 430, 215]},
    ]
    local_report = EvidenceGraphDocumentUnit(
        unit_id="final_report:15", graph_id="graph-1", doc_id="doc-final_report",
        doc_type="final_report", filename="final_report.pdf", page_number=15,
        native_text="电压波动测试 Test Date 2026-01-01 样品编号 S-001",
        source_hash="c" * 64, rendered_pdf_hash="d" * 64,
        layout_lines=[
            {"text": "电压波动测试", "bbox": [60, 330, 130, 345]},
            {"text": "Test Date", "bbox": [60, 200, 120, 215]},
            {"text": "样品编号", "bbox": [310, 200, 365, 215]},
            {"text": "S-001", "bbox": [380, 200, 430, 215]},
        ],
    )
    units.append(local_report)

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, _per_item_sample_metadata(),
    )

    finding = next(item for item in findings if item.check_id == "GRAPH-SAMPLE-001")
    records = [item for item in evidence if item.evidence_id in finding.evidence_ids]
    assert [(item.doc_type, item.page_number) for item in records] == [
        ("test_plan", 5), ("final_report", 15),
    ]
    assert records[0].exact_quote == "4"
    assert records[0].bbox == [650.0, 320.0, 660.0, 332.0]
    assert records[1].metadata["count_basis"] == "distinct_item_local_sample_ids"
    assert records[1].metadata["derived_count"] == 1


def test_per_item_sample_count_uses_visible_model_column_for_repeated_counts():
    units = _units()
    plan_unit = units[1]
    plan_unit.page_number = 5
    plan_unit.source_hash = "a" * 64
    plan_unit.rendered_pdf_hash = "b" * 64
    plan_unit.native_text = "电压波动测试 RD20容量5000 RD30-C容量9000 4 5"
    plan_unit.layout_lines = [
        {"text": "RD20容量5000", "bbox": [450, 280, 510, 295]},
        {"text": "RD30-C容量9000", "bbox": [540, 280, 610, 295]},
        {"text": "电压波动测试", "bbox": [80, 320, 180, 332]},
        {"text": "4", "bbox": [475, 320, 485, 332]},
        {"text": "5", "bbox": [565, 320, 575, 332]},
    ]
    metadata = _per_item_sample_metadata()
    plan = json.loads(metadata["test_plan"][0]["field_value"])
    plan["test_items"][0]["sample_requirements"] = {
        "RD20容量5000": "4",
        "RD30-C容量9000": "5",
    }
    metadata["test_plan"] = _rows(plan)
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["cover"]["sample_model"] = "RD20"
    report["sample"] = {"model": "RD20 (KFP28E-11-W)"}
    metadata["final_report"] = _rows(report)
    report_unit = units[3]
    report_unit.page_number = 15
    report_unit.native_text += " 电压波动测试 S-001"
    report_unit.layout_lines = [
        {"text": "电压波动测试", "bbox": [60, 330, 130, 345]},
        {"text": "Test Date", "bbox": [60, 200, 120, 215]},
        {"text": "样品编号", "bbox": [310, 200, 365, 215]},
        {"text": "S-001", "bbox": [380, 200, 430, 215]},
    ]

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    finding = next(item for item in findings if item.check_id == "GRAPH-SAMPLE-001")
    records = [item for item in evidence if item.evidence_id in finding.evidence_ids]
    assert records[0].bbox == [475.0, 320.0, 485.0, 332.0]


def test_visible_count_anchor_rejects_repeated_counts_without_column_scope():
    unit = _units()[1]
    unit.layout_lines = [
        {"text": "电压波动测试", "bbox": [80, 320, 180, 332]},
        {"text": "4", "bbox": [475, 320, 485, 332]},
        {"text": "4", "bbox": [565, 320, 575, 332]},
    ]

    assert _visible_count_anchor(unit, "4", item_name="电压波动测试") == ("", [])


def test_visible_count_anchor_reconstructs_wrapped_item_and_scope_cells():
    unit = _units()[1]
    unit.layout_lines = [
        {"text": "RD20容量", "bbox": [450, 280, 510, 292]},
        {"text": "5000", "bbox": [475, 294, 495, 306]},
        {"text": "过电压实验", "bbox": [80, 320, 145, 332]},
        {"text": "（高温条件）", "bbox": [78, 334, 147, 346]},
        {"text": "4", "bbox": [475, 334, 485, 346]},
        {"text": "4", "bbox": [565, 334, 575, 346]},
    ]

    quote, bbox = _visible_count_anchor(
        unit, "4", item_name="过电压实验（高温条件）", scope="RD20容量5000",
    )

    assert quote == "4"
    assert bbox == [475.0, 334.0, 485.0, 346.0]
    assert _visible_phrase_boxes(
        unit.layout_lines, "过电压实验（高温条件）",
    ) == [[78.0, 320.0, 147.0, 346.0]]


def test_visible_count_anchor_uses_unique_inherited_column_on_continuation_page():
    unit = _units()[1]
    unit.layout_lines = [
        {"text": "开路测试", "bbox": [80, 320, 145, 332]},
        {"text": "3pcs", "bbox": [475, 320, 495, 332]},
        {"text": "3pcs", "bbox": [565, 320, 585, 332]},
    ]

    quote, bbox = _visible_count_anchor(
        unit, "3pcs", item_name="开路测试", scope="RD20容量5000",
        scope_x_centers=(485.0,),
    )

    assert quote == "3pcs"
    assert bbox == [475.0, 320.0, 495.0, 332.0]


def test_reviewed_document_checks_validate_receive_before_test_start():
    metadata = _metadata()
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["cover"]["receive_date"] = "2026/6/3"
    metadata["final_report"] = _rows(report)

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(),
        _units(extra_report_text=" 接收日期 2026/6/3"), metadata,
    )

    assert [finding.check_id for finding in findings] == ["DOC-TIMELINE-003"]


def test_reviewed_document_checks_find_plan_level_and_instrument_set_conflicts():
    metadata = _metadata()
    plan = json.loads(metadata["test_plan"][0]["field_value"])
    plan["test_items"] = [{
        "name": "反向电压",
        "acceptance": "符合 ISO 16750-1 中定义 B 级要求；符合 A 级要求",
    }]
    metadata["test_plan"] = _rows(plan)
    raw = json.loads(metadata["original_records"][0]["field_value"])
    raw["metas"][0].update({
        "test_item_name": "反向电压",
        "semantic_data": {"tables": [{
            "table_family": "instrument",
            "rows": [{"cells": [
                {"semantic": "manufacturer", "source_value": "KIKUSUI"},
                {"semantic": "model", "source_value": "PBZ40-10"},
                {"semantic": "serial_number", "source_value": "RAW-001"},
                {"semantic": "calibration_validity", "source_value": "2027-01-01"},
            ]}],
        }]},
    })
    metadata["original_records"] = _rows(raw)
    report = json.loads(metadata["final_report"][0]["field_value"])
    report["instruments"] = [
        {
            "test_item": "Reversed voltage", "manufacturer": "KIKUSUI",
            "model": "PBZ40-10", "serial_no": "RAW-001",
            "calibration_end": "2027-01-01",
        },
        {
            "test_item": "Reversed voltage", "manufacturer": "KIKUSUI",
            "model": "PBZ40-10", "serial_no": "REPORT-002",
            "calibration_end": "2027-01-01",
        },
    ]
    metadata["final_report"] = _rows(report)
    units = _units(extra_report_text=" RAW-001 REPORT-002")
    units[1].native_text = (
        "反向电压 符合 ISO 16750-1 中定义 B 级要求；符合 A 级要求"
    )
    units[2].native_text += " RAW-001"

    evidence, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    check_ids = {finding.check_id for finding in findings}
    assert "PLAN-ACCEPTANCE-CONFLICT-001" in check_ids
    assert "DOC-INSTRUMENT-CONSISTENCY-001" in check_ids
    instrument = next(
        item for item in findings if item.check_id == "DOC-INSTRUMENT-CONSISTENCY-001"
    )
    roles = {
        item.metadata.get("role")
        for item in evidence if item.evidence_id in instrument.evidence_ids
    }
    assert roles == {"report_only", "matched_report", "matched_raw"}
    assert instrument.metadata["comparison_summary"] == {
        "equality_required": True,
        "report_total": 2,
        "raw_total": 1,
        "matched_count": 1,
        "report_only_count": 1,
        "raw_only_count": 0,
    }
    assert instrument.description == (
        "两份清单共同 1 台；检测报告另有 1 台未在原始记录中出现；"
        "原始记录没有独有仪器。业务要求两份仪器清单完全一致。"
    )


def test_conditioned_plan_acceptance_levels_are_not_reported_as_conflict():
    metadata = _metadata()
    plan = json.loads(metadata["test_plan"][0]["field_value"])
    plan["test_items"] = [
        {
            "name": "供电电压范围",
            "acceptance": "有效电压范围内为A级，超出电压范围为C级；有冗余电源时可指定A级",
        },
        {
            "name": "瞬间降低",
            "acceptance": (
                "A级零部件（Usmin=6V）功能状态需达到B级，其余产品满足功能等级C，"
                "对于有冗余电源的DUT，可以指定功能状态A级"
            ),
        },
    ]
    metadata["test_plan"] = _rows(plan)
    units = _units()
    units[1].native_text = (
        "供电电压范围 有效电压范围内为A级，超出电压范围为C级；"
        "有冗余电源时可指定A级 "
        "瞬间降低 A级零部件（Usmin=6V）功能状态需达到B级，其余产品满足功能等级C，"
        "对于有冗余电源的DUT，可以指定功能状态A级"
    )

    _, _, findings = build_reviewed_document_checks(
        "graph-1", _documents(), units, metadata,
    )

    assert not [
        item for item in findings
        if item.check_id == "PLAN-ACCEPTANCE-CONFLICT-001"
    ]


def test_plan_clause_rule_is_not_applicable_without_explicit_clause_pair():
    _, _, _, audit = build_reviewed_document_checks_and_audit(
        "graph-1", _documents(), _units(), _metadata(),
    )

    item = next(
        row for row in audit
        if row["check_id"] == "PLAN-CLAUSE-CONSISTENCY-001"
    )
    assert item["state"] == "not_applicable"
    assert item["reason_code"] == "plan_has_no_explicit_clause_pair"
