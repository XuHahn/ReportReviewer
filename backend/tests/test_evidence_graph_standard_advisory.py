import json

from services.evidence_graph_coverage import ObservedTestItem
from services.evidence_graph_extraction import EvidenceGraphDocumentUnit
from services.evidence_graph_standard_advisory import (
    build_standard_acceptance_consistency,
    build_standard_parameter_consistency,
    build_standard_plan_coverage,
    build_standard_provenance,
    build_standard_scope_advisories,
)


def _release(statement="必须进行脉冲3a测试"):
    return {"id": "rel-1", "status": "published", "snapshot": {
        "standard_code": "ISO TEST", "requirements": [{
            "id": "req-1", "review_status": "confirmed", "test_item": "脉冲3a",
            "interpretation_zh": statement, "evidence_quote": "Pulse 3a shall be tested.",
        }],
    }}


def test_explicit_mandatory_standard_gap_is_advisory_only():
    evidence, nodes, findings = build_standard_scope_advisories("g", [], [_release()])
    assert len(evidence) == len(nodes) == len(findings) == 1
    assert str(findings[0].status) == "confirmed_advisory"
    assert "不自动增加" in findings[0].description


def test_non_explicit_or_already_planned_requirement_does_not_warn():
    plan = [ObservedTestItem(item_id="p", doc_type="test_plan", name="P3a", evidence_ids=["e"])]
    assert build_standard_scope_advisories("g", [], [_release("可进行脉冲3a测试")])[-1] == []
    assert build_standard_scope_advisories("g", plan, [_release()])[-1] == []


def test_planned_item_links_to_confirmed_published_standard_without_expanding_scope():
    plan = [ObservedTestItem(
        item_id="p", doc_type="test_plan", name="P3a", evidence_ids=["plan-e"],
    )]

    evidence, nodes, edges = build_standard_provenance("g", plan, [_release()])

    assert len(evidence) == len(nodes) == len(edges) == 1
    assert str(edges[0].relation_type) == "governed_by"
    assert edges[0].source_node_id == "p"
    assert edges[0].metadata["scope_expanded"] is False


def test_plan_detail_identity_alias_can_link_standard_scope_note():
    plan = [ObservedTestItem(
        item_id="p4",
        doc_type="test_plan",
        name="电压波动测试",
        evidence_ids=["plan-e"],
        metadata={"identity_aliases": ["P4"]},
    )]
    release = _release()
    release["snapshot"]["requirements"][0]["test_item"] = "系统12V电源电压波动试验P4"
    release["snapshot"]["requirements"][0]["interpretation_zh"] = (
        "ISO 7637-2:2011 已移除脉冲4，按用户要求执行。"
    )

    evidence, nodes, edges = build_standard_provenance("g", plan, [release])

    assert len(evidence) == len(nodes) == len(edges) == 1
    assert edges[0].source_node_id == "p4"


def test_standard_provenance_links_blank_test_item_by_explicit_plan_clause():
    plan = [ObservedTestItem(
        item_id="p-offset", doc_type="test_plan",
        name="Ground reference and supply offset", evidence_ids=["plan-e"],
        parameters={"standard_clause": "ISO-16750-2-4.8"},
    )]
    release = {
        "id": "rel-iso", "status": "published", "snapshot": {
            "standard_code": "ISO 16750-2:2012",
            "requirements": [{
                "id": "req-offset", "review_status": "confirmed",
                "clause_number": "4.8.2", "test_item": "",
                "evidence_quote": "offset voltage shall be (1.0 ± 0.1) V",
            }],
        },
    }

    evidence, nodes, edges = build_standard_provenance("g", plan, [release])

    assert len(evidence) == len(nodes) == len(edges) == 1
    assert edges[0].source_node_id == "p-offset"


def test_standard_plan_coverage_reports_unpublished_cited_standard():
    plan = [
        ObservedTestItem(
            item_id="p2", doc_type="test_plan", name="反向电压",
            evidence_ids=["e2"], parameters={"standard_clause": "ISO-16750-2-4.7"},
        ),
        ObservedTestItem(
            item_id="p4", doc_type="test_plan", name="高温存储",
            evidence_ids=["e4"], parameters={"standard_clause": "ISO-16750-4-5.1.2.1"},
        ),
    ]
    release = {
        "id": "rel-2", "status": "published",
        "snapshot": {"standard_code": "ISO 16750-2:2012", "requirements": []},
    }

    findings, cited, covered = build_standard_plan_coverage("g", plan, [release])

    assert cited == 2
    assert covered == 1
    assert len(findings) == 1
    assert findings[0].metadata["standard_code"] == "ISO 16750-4"


def test_published_acceptance_is_compared_with_plan_report_and_raw_sources():
    plan = [ObservedTestItem(
        item_id="p-reverse", doc_type="test_plan", name="反向电压",
        evidence_ids=["plan-e"],
        parameters={
            "standard_clause": "ISO-16750-2-4.7",
            "acceptance": "符合 B 级要求；符合 A 级要求",
        },
    )]
    release = {
        "id": "rel-iso", "status": "published", "snapshot": {
            "standard_code": "ISO 16750-2:2012",
            "requirements": [{
                "id": "req-accept", "review_status": "confirmed",
                "clause_number": "4.7.3", "requirement_type": "acceptance",
                "evidence_quote": "functional status shall be class A",
            }],
        },
    }
    report = {
        "item_extractions": [{
            "test_item_name": "Reversed voltage", "required_level": "A",
            "test_results": {"sample_data": [{
                "sample_id": "S-1", "mode": "Mode 1", "data_rows": [{
                    "test_item": "Reversed voltage", "injection_point": "power supply",
                    "spec_requirement": "14V", "test_duration": "60s",
                    "required_level": "C", "actual_level": "C", "verdict": "Pass",
                }],
            }]},
        }],
    }
    raw = {"metas": [{
        "filename": "raw-1.pdf", "test_item_name": "反向电压",
        "semantic_data": {"tables": [{
            "table_family": "test_data", "rows": [{
                "evidence": "反向电压 电源线 14V 60s C C 符合",
                "cells": [{
                    "semantic": "required_performance_level", "source_value": "C",
                }],
            }],
        }]},
    }]}
    units = [
        EvidenceGraphDocumentUnit(
            unit_id="plan:1", graph_id="g", doc_id="plan-doc",
            doc_type="test_plan", filename="plan.xlsx",
            native_text="反向电压 符合 B 级要求；符合 A 级要求",
        ),
        EvidenceGraphDocumentUnit(
            unit_id="report:1", graph_id="g", doc_id="report-doc",
            doc_type="final_report", filename="report.docx",
            native_text=(
                "Reversed voltage TEST SPECIFICATION Functional classes A S-1 Mode 1 "
                "Reversed voltage power supply 14V 60s C C Pass"
            ),
        ),
        EvidenceGraphDocumentUnit(
            unit_id="raw:1", graph_id="g", doc_id="raw-doc",
            doc_type="original_records", filename="raw-1.pdf",
            native_text="反向电压 电源线 14V 60s C C 符合",
        ),
    ]
    documents = {
        "test_plan": {"doc_id": "plan-doc", "filename": "plan.xlsx"},
        "final_report": {"doc_id": "report-doc", "filename": "report.docx"},
        "original_records": {"doc_id": "raw-doc", "filename": "raw.zip"},
    }
    metadata = {
        "test_plan": [{
            "field_name": "__structured__", "field_value": "{}",
        }],
        "final_report": [{
            "field_name": "__structured__", "field_value": json.dumps(report),
        }],
        "original_records": [{
            "field_name": "__structured__", "field_value": json.dumps(raw),
        }],
    }

    evidence, nodes, findings, expected, anchored = (
        build_standard_acceptance_consistency(
            "g", plan, [release], documents, units, metadata,
        )
    )

    assert expected == anchored == 4
    assert len(findings) == 3
    assert {item.metadata["source_kind"] for item in findings} == {
        "test_plan", "final_report_results", "original_records",
    }
    assert len(nodes) == 3
    assert any(item.doc_type == "test_standard" for item in evidence)
    report_result = next(
        item for item in evidence
        if item.extraction_method == "report_result_acceptance"
    )
    assert report_result.metadata["comparison_rows"][0]["required_level"] == "C"


def test_report_acceptance_level_does_not_anchor_to_cover_page_token():
    from services.evidence_graph_standard_advisory import _report_level_context

    cover = EvidenceGraphDocumentUnit(
        unit_id="report:cover", graph_id="g", doc_id="report-doc",
        doc_type="final_report", filename="report.docx",
        native_text="Reversed voltage Report cover revision C",
    )
    detail = EvidenceGraphDocumentUnit(
        unit_id="report:detail", graph_id="g", doc_id="report-doc",
        doc_type="final_report", filename="report.docx",
        native_text="Reversed voltage TEST SPECIFICATION Functional classes C",
    )

    assert _report_level_context(cover, "Reversed voltage", "C") == ""
    assert "Functional classes C" in _report_level_context(
        detail, "Reversed voltage", "C",
    )


def test_published_named_parameter_is_compared_with_report_specification():
    plan = [ObservedTestItem(
        item_id="p-offset", doc_type="test_plan",
        name="Ground reference and supply offset", evidence_ids=["plan-e"],
        parameters={"standard_clause": "ISO-16750-2-4.8"},
    )]
    release = {
        "id": "rel-iso", "status": "published", "snapshot": {
            "standard_code": "ISO 16750-2:2012",
            "requirements": [{
                "id": "req-offset", "review_status": "confirmed",
                "clause_number": "4.8.2", "requirement_type": "parameter_limit",
                "evidence_quote": "offset voltage shall be (1.0 ± 0.1) V",
            }],
        },
    }
    report = {"item_extractions": [{
        "test_item_name": "Ground reference and supply offset",
        "spec_parameters": [{"name": "Offset voltage", "value": "±1.5V"}],
    }]}
    units = [EvidenceGraphDocumentUnit(
        unit_id="report:1", graph_id="g", doc_id="report-doc",
        doc_type="final_report", filename="report.docx",
        native_text="Ground reference and supply offset Offset voltage ±1.5V",
    )]
    metadata = {"final_report": [{
        "field_name": "__structured__", "field_value": json.dumps(report),
    }]}

    evidence, nodes, findings, expected, anchored = (
        build_standard_parameter_consistency(
            "g", plan, [release],
            {"final_report": {"doc_id": "report-doc", "filename": "report.docx"}},
            units, metadata,
        )
    )

    assert expected == anchored == 1
    assert len(evidence) == 2
    assert len(nodes) == len(findings) == 1
    assert findings[0].check_id == "STANDARD-PARAMETER-CONSISTENCY-001"
    assert findings[0].metadata["report_tokens"] == ["±1.5v"]


def test_published_structured_parameters_are_compared_individually_not_as_clause_union():
    plan = [ObservedTestItem(
        item_id="p-reverse", doc_type="test_plan", name="Reversed voltage",
        evidence_ids=["plan-e"], parameters={"standard_clause": "ISO-16750-2-4.7"},
    )]
    requirement_quote = (
        "If the DUT withstands a reversed voltage, apply a test voltage of 4 V "
        "for a duration of (60 ± 6) s."
    )
    release = {
        "id": "rel-iso", "status": "published", "snapshot": {
            "standard_code": "ISO 16750-2:2012", "source_file_sha256": "f" * 64,
            "requirements": [{
                "id": "req-reverse", "review_status": "confirmed",
                "clause_number": "4.7.2", "requirement_type": "test_condition",
                "evidence_quote": requirement_quote, "page_start": 17,
                "parameters": [
                    {"name": "测试电压", "value": "4", "unit": "V", "raw_text": "4 V"},
                    {"name": "持续时间", "value": "60", "unit": "s", "raw_text": "(60 ± 6) s"},
                ],
            }],
        },
    }
    report = {"item_extractions": [{
        "test_item_name": "Reversed voltage",
        "spec_parameters": [
            {"name": "Reversed voltage", "value": "4V"},
            {"name": "Reversed voltage", "value": "14V"},
        ],
    }]}
    unit = EvidenceGraphDocumentUnit(
        unit_id="report:54", graph_id="g", doc_id="report-doc",
        doc_type="final_report", filename="report.docx", page_number=54,
        native_text="TEST SPECIFICATION Reversed voltage 4V Reversed voltage 14V",
        source_hash="e" * 64, rendered_pdf_hash="d" * 64,
        page_width=595, page_height=842,
        layout_lines=[
            {"text": "Reversed voltage 4V", "bbox": [60, 140, 285, 163]},
            {"text": "Reversed voltage 14V", "bbox": [60, 202, 286, 225]},
        ],
    )

    evidence, _, findings, expected, anchored = build_standard_parameter_consistency(
        "g", plan, [release],
        {"final_report": {"doc_id": "report-doc", "filename": "report.docx"}},
        [unit], {"final_report": [{
            "field_name": "__structured__", "field_value": json.dumps(report),
        }]},
    )

    assert expected == anchored == 2
    assert len(findings) == 1
    assert findings[0].metadata["report_value"] == "14V"
    records = [item for item in evidence if item.evidence_id in findings[0].evidence_ids]
    assert records[0].exact_quote != requirement_quote
    assert "test voltage of 4 V" in records[0].exact_quote
    assert records[1].exact_quote == "14V"
    assert records[1].bbox == [60.0, 202.0, 286.0, 225.0]


def test_severity_comparison_excludes_nominal_voltage_context():
    plan = [ObservedTestItem(
        item_id="p-ac", doc_type="test_plan", name="交变电压叠加实验",
        evidence_ids=["plan-e"],
        parameters={"standard_clause": "ISO-16750-2-4.4"},
    )]
    release = {
        "id": "rel-iso", "status": "published", "snapshot": {
            "standard_code": "ISO 16750-2:2012",
            "requirements": [{
                "id": "req-severity", "review_status": "confirmed",
                "clause_number": "4.4.2", "requirement_type": "test_condition",
                "evidence_quote": (
                    "severity 1: peak to peak voltage of 1 V, "
                    "for UN = 12 V and UN = 24 V;"
                ),
            }],
        },
    }
    report = {"item_extractions": [{
        "test_item_name": "Superimposed alternating voltage",
        "spec_parameters": [{"name": "Severity 1", "value": "10V"}],
    }]}
    units = [EvidenceGraphDocumentUnit(
        unit_id="report:1", graph_id="g", doc_id="report-doc",
        doc_type="final_report", filename="report.docx",
        native_text="TEST SPECIFICATION\nSeverity level Upp\n1\n10 V ± 0.2 V",
    )]
    metadata = {"final_report": [{
        "field_name": "__structured__", "field_value": json.dumps(report),
    }]}

    _, _, findings, expected, anchored = build_standard_parameter_consistency(
        "g", plan, [release],
        {"final_report": {"doc_id": "report-doc", "filename": "report.docx"}},
        units, metadata,
    )

    assert expected == anchored == 1
    assert findings[0].metadata["standard_tokens"] == ["1v"]
    assert findings[0].metadata["report_tokens"] == ["10v"]
