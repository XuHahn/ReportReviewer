import json

import pytest

from services.evidence_graph_coverage import ObservedTestItem
from services.evidence_graph_extraction import (
    EvidenceGraphDocumentUnit,
    MaterializedObservation,
    UnifiedExtractionResult,
)
from services.unified_review_pipeline import (
    _aggregate_test_items,
    _is_cacheable_extraction,
    _reviewed_plan_authority,
    _reviewed_result_authority,
    _semantic_identity_overrides,
    _visual_reason,
)


def test_visual_reason_uses_native_for_digital_page_and_qwen_for_scan():
    assert _visual_reason("测试项目：脉冲2a") == "none"
    assert _visual_reason("   ") == "scan"


def test_visual_reason_detects_complex_native_table():
    text = "\n".join([
        "项目\t条件\t结果",
        "P1\t12V\t符合",
        "P2a\t12V\t符合",
        "P2b\t12V\t符合",
        "P3a\t12V\t符合",
    ])
    assert _visual_reason(text) == "complex_table"
    assert _visual_reason(text, has_page_image=False) == "none"


def test_only_complete_extractions_are_cached():
    assert _is_cacheable_extraction(UnifiedExtractionResult(
        unit_id="complete", status="complete",
    )) is True
    assert _is_cacheable_extraction(UnifiedExtractionResult(
        unit_id="review", status="needs_review",
    )) is False
    assert _is_cacheable_extraction(UnifiedExtractionResult(
        unit_id="failed", status="failed",
    )) is False


def test_test_item_aggregation_merges_continuation_evidence_but_not_samples():
    def result(observation_id, evidence_id, sample_id):
        return UnifiedExtractionResult(
            unit_id=observation_id,
            status="complete",
            observations=[MaterializedObservation(
                observation_id=observation_id,
                observation_type="test_item",
                entity_name="脉冲2a",
                field_name="test_item_presence",
                raw_value="脉冲2a",
                evidence_ids=[evidence_id],
                extraction_sources=["native"],
                metadata={"sample_id": sample_id},
            )],
        )

    aggregated = _aggregate_test_items({
        "original_records": [
            result("obs-1", "ev-1", "0001"),
            result("obs-2", "ev-2", "0001"),
            result("obs-3", "ev-3", "0002"),
        ],
    })

    assert len(aggregated["original_records"]) == 2
    sample_1 = next(item for item in aggregated["original_records"] if item.sample_id == "0001")
    assert sample_1.evidence_ids == ["ev-1", "ev-2"]


def test_test_item_aggregation_merges_confirmed_aliases_as_cumulative_evidence():
    def result(observation_id, evidence_id, name, mode=""):
        return UnifiedExtractionResult(
            unit_id=observation_id,
            status="complete",
            observations=[MaterializedObservation(
                observation_id=observation_id,
                observation_type="test_item",
                entity_name=name,
                field_name="test_item_presence",
                raw_value=name,
                evidence_ids=[evidence_id],
                extraction_sources=["native"],
                metadata={"mode": mode},
            )],
        )

    aggregated = _aggregate_test_items({
        "final_report": [
            result("obs-p4", "ev-p4", "P4"),
            result("obs-name", "ev-name", "系统 12V 电源电压波动试验", "模式1"),
        ],
    })

    assert len(aggregated["final_report"]) == 1
    assert aggregated["final_report"][0].mode == "模式1"
    assert aggregated["final_report"][0].evidence_ids == ["ev-name", "ev-p4"]


def test_test_item_aggregation_keeps_two_explicit_modes_separate():
    def result(observation_id, mode):
        return UnifiedExtractionResult(
            unit_id=observation_id,
            status="complete",
            observations=[MaterializedObservation(
                observation_id=observation_id,
                observation_type="test_item",
                entity_name="系统12V电源电压波动试验",
                field_name="test_item_presence",
                raw_value="系统12V电源电压波动试验",
                evidence_ids=[f"ev-{observation_id}"],
                extraction_sources=["native"],
                metadata={"mode": mode},
            )],
        )

    aggregated = _aggregate_test_items({
        "final_report": [result("obs-1", "模式1"), result("obs-2", "模式2")],
    })

    assert {item.mode for item in aggregated["final_report"]} == {"模式1", "模式2"}


def test_reviewed_plan_is_authoritative_and_each_item_keeps_native_evidence():
    units = [EvidenceGraphDocumentUnit(
        unit_id="test_plan:page-1",
        graph_id="graph-1",
        doc_id="plan-1",
        doc_type="test_plan",
        filename="plan.xlsx",
        native_text="灯具的光电基本性能试\n验\n电压波动测试\n试验脉冲P4",
        page_number=1,
    )]
    metadata = [{
        "field_name": "__structured__",
        "field_value": (
            '{"test_items":['
            '{"code":"灯具的光电基本性能试验","name":"灯具的光电基本性能试验"},'
            '{"code":"电压波动测试","name":"电压波动测试","acceptance":"正常工作"}],'
            '"test_details":[{"code":"电压波动测试","fields":'
            '{"test_level":"试验脉冲P4"}}]}'
        ),
    }]

    items, evidence = _reviewed_plan_authority(
        "graph-1", {"doc_id": "plan-1", "filename": "plan.xlsx"}, units, metadata,
    )

    assert [item.name for item in items] == ["灯具的光电基本性能试验", "电压波动测试"]
    assert len(evidence) == 2
    assert "\n" in evidence[0].exact_quote
    assert items[1].metadata["identity_aliases"] == ["P4"]


def test_reviewed_plan_keeps_same_test_name_in_two_explicit_modes():
    units = [EvidenceGraphDocumentUnit(
        unit_id="test_plan:page-1", graph_id="graph-1", doc_id="plan-1",
        doc_type="test_plan", filename="plan.pdf",
        native_text="电压波动测试 模式1 模式2", page_number=1,
    )]
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({"test_items": [
            {"name": "电压波动测试", "test_mode": "模式1"},
            {"name": "电压波动测试", "test_mode": "模式2"},
        ]}, ensure_ascii=False),
    }]

    items, evidence = _reviewed_plan_authority(
        "graph-1", {"doc_id": "plan-1", "filename": "plan.pdf"}, units, metadata,
    )

    assert len(items) == 2
    assert {item.mode for item in items} == {"模式1", "模式2"}
    assert len({item.item_id for item in items}) == 2
    assert len({item.evidence_ids[0] for item in items}) == 2


def test_reviewed_plan_joins_detail_fields_by_item_code_when_name_differs():
    units = [EvidenceGraphDocumentUnit(
        unit_id="test_plan:page-1", graph_id="graph-1", doc_id="plan-1",
        doc_type="test_plan", filename="plan.xlsx",
        native_text="电压波动测试", page_number=1,
    )]
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({
            "test_items": [{"code": "T-04", "name": "电压波动测试"}],
            "test_details": [{
                "code": "T-04",
                "fields": {"test_level": "试验脉冲 P4"},
            }],
        }, ensure_ascii=False),
    }]

    items, _ = _reviewed_plan_authority(
        "graph-1", {"doc_id": "plan-1", "filename": "plan.xlsx"}, units, metadata,
    )

    assert items[0].parameters["test_level"] == "试验脉冲 P4"
    assert items[0].metadata["identity_aliases"] == ["P4"]


def test_complete_raw_structured_extraction_is_reused_as_authority():
    units = [EvidenceGraphDocumentUnit(
        unit_id="original_records:page-1", graph_id="graph-1",
        doc_id="raw-1", doc_type="original_records", filename="raw.zip",
        native_text="测试项目 供电电压瞬时下\n降 测试结论：符合", page_number=1,
    )]
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({
            "extraction_quality": "complete",
            "semantic_extraction_count": 1,
            "semantic_extraction_failures": [],
            "extraction_metrics": {"pdf_count": 1},
            "conclusion_summary": [{
                "test_item_name": "供电电压瞬时下降",
                "round_count": 1,
                "all_rounds_pass": True,
            }],
        }, ensure_ascii=False),
    }]

    items, evidence, complete = _reviewed_result_authority(
        "graph-1", "original_records",
        {"doc_id": "raw-1", "filename": "raw.zip"}, units, metadata,
    )

    assert complete is True
    assert [item.name for item in items] == ["供电电压瞬时下降"]
    assert items[0].result == "符合"
    assert "\n" in evidence[0].exact_quote


def test_raw_authority_uses_internal_result_table_item_names_not_archive_filename():
    units = [EvidenceGraphDocumentUnit(
        unit_id="original_records:member-1/page-1", graph_id="graph-1",
        doc_id="raw-1", doc_type="original_records",
        filename="opaque-upload.zip",
        native_text=(
            "测试项目 瞬态抗扰度试验\n"
            "脉冲 1 电源线 500个脉冲 符合\n"
            "脉冲 2a 电源线 500个脉冲 符合\n"
            "脉冲 2b 电源线 10个脉冲 符合\n"
            "脉冲 3a 电源线 1h 符合"
        ),
        page_number=1,
    )]
    data_rows = [
        {
            "test_item": "瞬态抗扰度试验",
            "spec_params": {"测试项目": f"脉冲 {pulse}"},
            "test_result": "符合",
            "source_quote": f"脉冲 {pulse}",
        }
        for pulse in ("1", "2a", "2b", "3a")
    ]
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({
            "extraction_quality": "complete",
            "semantic_extraction_count": 1,
            "semantic_extraction_failures": [],
            "extraction_metrics": {"pdf_count": 1},
            "conclusion_summary": [{
                "test_item_name": "瞬态抗扰度试验",
                "all_rounds_pass": True,
            }],
            "data_rows": data_rows,
        }, ensure_ascii=False),
    }]

    items, evidence, complete = _reviewed_result_authority(
        "graph-1", "original_records",
        {"doc_id": "raw-1", "filename": "opaque-upload.zip"},
        units, metadata,
    )

    assert complete is True
    assert {item.name for item in items} == {
        "瞬态抗扰度试验", "脉冲 1", "脉冲 2a", "脉冲 2b", "脉冲 3a",
    }
    base_item = next(item for item in items if item.name == "瞬态抗扰度试验")
    assert base_item.metadata["identity_role"] == "base_item"
    pulse_2a = next(item for item in items if item.name == "脉冲 2a")
    assert pulse_2a.parent_name == "瞬态抗扰度试验"
    assert pulse_2a.metadata["identity_source"] == "internal_result_table"
    assert pulse_2a.metadata["identity_role"] == "execution_detail"
    assert all(item.filename == "opaque-upload.zip" for item in evidence)


def test_report_authority_uses_internal_result_rows_and_collapses_summary_duplicate():
    units = [EvidenceGraphDocumentUnit(
        unit_id="final_report:page-9", graph_id="graph-1",
        doc_id="report-1", doc_type="final_report", filename="report.docx",
        native_text=(
            "瞬态抗扰度试验 模式1 符合\n"
            "脉冲1 电源线 500个脉冲 符合\n"
            "脉冲2a 电源线 500个脉冲 符合\n"
            "脉冲2b 电源线 10个脉冲 符合\n"
            "脉冲3a 电源线 1h 符合"
        ),
        page_number=9,
    )]
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({
            "extraction_quality": "complete",
            "failed_passes": [],
            "results": [{
                "test_item": "瞬态抗扰度试验", "result": "符合",
            }],
            "item_extractions": [{
                "test_item_name": "瞬态抗扰度试验",
                "test_results": {"sample_data": [{
                    "sample_id": "E202605287495-0001",
                    "mode": "模式1",
                    "data_rows": [
                        {
                            "test_item": f"脉冲{pulse}",
                            "injection_point": "电源线",
                            "test_duration": duration,
                            "verdict": "符合",
                        }
                        for pulse, duration in (
                            ("1", "500个脉冲"), ("2a", "500个脉冲"),
                            ("2b", "10个脉冲"), ("3a", "1h"),
                        )
                    ],
                }]},
            }],
        }, ensure_ascii=False),
    }]

    items, _, complete = _reviewed_result_authority(
        "graph-1", "final_report",
        {"doc_id": "report-1", "filename": "report.docx"},
        units, metadata,
    )

    assert complete is True
    assert {item.name for item in items} == {
        "瞬态抗扰度试验", "脉冲1", "脉冲2a", "脉冲2b", "脉冲3a",
    }
    base_item = next(item for item in items if item.name == "瞬态抗扰度试验")
    assert base_item.metadata["identity_role"] == "base_item"
    pulse_3a = next(item for item in items if item.name == "脉冲3a")
    assert pulse_3a.sample_id == "E202605287495-0001"
    assert pulse_3a.mode == "模式1"
    assert pulse_3a.metadata["identity_role"] == "execution_detail"


def test_report_authority_preserves_all_execution_contexts_for_one_identity():
    units = [EvidenceGraphDocumentUnit(
        unit_id="final_report:page-9", graph_id="graph-1",
        doc_id="report-1", doc_type="final_report", filename="report.docx",
        native_text="反向电压 0001 模式1 符合\n反向电压 0002 模式2 符合",
        page_number=9,
    )]
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({
            "extraction_quality": "complete",
            "results": [{"test_item": "反向电压", "result": "符合"}],
            "item_extractions": [{
                "test_item_name": "反向电压",
                "test_results": {"sample_data": [
                    {"sample_id": "0001", "mode": "模式1", "data_rows": [
                        {"test_item": "反向电压", "verdict": "符合"},
                    ]},
                    {"sample_id": "0002", "mode": "模式2", "data_rows": [
                        {"test_item": "反向电压", "verdict": "符合"},
                    ]},
                ]},
            }],
        }, ensure_ascii=False),
    }]

    items, evidence, complete = _reviewed_result_authority(
        "graph-1", "final_report",
        {"doc_id": "report-1", "filename": "report.docx"}, units, metadata,
    )

    assert complete is True
    assert len(items) == 1
    assert len(evidence) == 2
    assert {
        (context["sample_id"], context["mode"])
        for context in items[0].metadata["execution_contexts"]
    } == {("0001", "模式1"), ("0002", "模式2")}


def test_report_internal_row_anchor_wins_over_same_name_on_summary_page():
    units = [
        EvidenceGraphDocumentUnit(
            unit_id="final_report:page-1", graph_id="graph-1",
            doc_id="report-1", doc_type="final_report", filename="report.docx",
            native_text="系统12V电源电压波动试验 模式1 符合", page_number=1,
        ),
        EvidenceGraphDocumentUnit(
            unit_id="final_report:page-15", graph_id="graph-1",
            doc_id="report-1", doc_type="final_report", filename="report.docx",
            native_text=(
                "系统12V电源电压波动试验 电源线 "
                "U=12V 1个脉冲 符合"
            ),
            page_number=15,
        ),
    ]
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({
            "extraction_quality": "complete",
            "results": [{
                "test_item": "系统12V电源电压波动试验", "result": "符合",
            }],
            "item_extractions": [{
                "test_item_name": "系统12V电源电压波动试验",
                "test_results": {"sample_data": [{
                    "data_rows": [{
                        "test_item": "系统12V电源电压波动试验",
                        "injection_point": "电源线",
                        "spec_requirement": "U=12V",
                        "test_duration": "1个脉冲",
                        "verdict": "符合",
                    }],
                }]},
            }],
        }, ensure_ascii=False),
    }]

    _, evidence, complete = _reviewed_result_authority(
        "graph-1", "final_report",
        {"doc_id": "report-1", "filename": "report.docx"}, units, metadata,
    )

    assert complete is True
    assert len(evidence) == 1
    assert evidence[0].page_number == 15
    assert "电源线" in evidence[0].exact_quote


def test_partial_raw_structured_extraction_does_not_bypass_page_fallback():
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({
            "extraction_quality": "partial",
            "semantic_extraction_count": 0,
            "semantic_extraction_failures": [{"error": "timeout"}],
            "extraction_metrics": {"pdf_count": 1},
            "conclusion_summary": [{"test_item_name": "反向电压"}],
        }, ensure_ascii=False),
    }]

    items, evidence, complete = _reviewed_result_authority(
        "graph-1", "original_records",
        {"doc_id": "raw-1", "filename": "raw.zip"}, [], metadata,
    )

    assert items == []
    assert evidence == []
    assert complete is False


def test_draft_report_results_can_be_reused_when_only_cover_identity_is_missing():
    units = [EvidenceGraphDocumentUnit(
        unit_id="final_report:page-10", graph_id="graph-1",
        doc_id="report-1", doc_type="final_report", filename="report.docx",
        native_text="REVERSED VOLTAGE\nTEST RESULTS\nFail", page_number=10,
    )]
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({
            "extraction_quality": "partial",
            "failed_passes": ["structural/cover_identity_missing", "instruments"],
            "results": [{"test_item": "Reversed voltage", "result": "Fail"}],
        }),
    }]

    items, evidence, complete = _reviewed_result_authority(
        "graph-1", "final_report",
        {"doc_id": "report-1", "filename": "report.docx"}, units, metadata,
    )

    assert complete is True
    assert items[0].result == "Fail"
    assert evidence[0].exact_quote == "REVERSED VOLTAGE"


def test_report_identity_rows_remain_authoritative_when_another_sample_is_missing():
    units = [EvidenceGraphDocumentUnit(
        unit_id="final_report:page-9", graph_id="graph-1",
        doc_id="report-1", doc_type="final_report", filename="report.docx",
        native_text="脉冲2a 电源线 500个脉冲 符合",
        page_number=9,
    )]
    metadata = [{
        "field_name": "__structured__",
        "field_value": json.dumps({
            "extraction_quality": "partial",
            "failed_passes": [
                "structural/cover_identity_missing",
                "item/sample_coverage_missing:1",
            ],
            "results": [],
            "item_extractions": [{
                "test_item_name": "瞬态抗扰度试验",
                "test_results": {"sample_data": [{
                    "sample_id": "E202605287495-0001", "mode": "模式1",
                    "data_rows": [{
                        "test_item": "脉冲2a", "injection_point": "电源线",
                        "test_duration": "500个脉冲", "verdict": "符合",
                    }],
                }]},
            }],
        }, ensure_ascii=False),
    }]

    items, _, complete = _reviewed_result_authority(
        "graph-1", "final_report",
        {"doc_id": "report-1", "filename": "report.docx"}, units, metadata,
    )

    assert complete is True
    assert [item.name for item in items] == ["脉冲2a"]


@pytest.mark.asyncio
async def test_semantic_identity_does_not_reuse_already_matched_observation():
    requirements = [
        ObservedTestItem(
            item_id="req-1", doc_type="test_plan", name="反向电压",
            evidence_ids=["ev-req-1"],
        ),
        ObservedTestItem(
            item_id="req-2", doc_type="test_plan", name="开路试验",
            evidence_ids=["ev-req-2"],
        ),
    ]
    observations = [ObservedTestItem(
        item_id="obs-1", doc_type="original_records", name="反向电压",
        evidence_ids=["ev-obs-1"],
    )]

    class MustNotBeCalled:
        async def call_json(self, *args, **kwargs):
            raise AssertionError("matched observation must not be reused")

    result = await _semantic_identity_overrides(
        "graph-1", requirements, observations, [], MustNotBeCalled(),
    )

    assert result == {}
