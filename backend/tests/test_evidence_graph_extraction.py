import pytest

from services.evidence_graph_extraction import (
    EvidenceGraphDocumentUnit,
    extract_evidence_graph_unit,
    recover_targeted_evidence,
)


class FakeGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call_json(self, task, **kwargs):
        self.calls.append((task, kwargs["stage"]))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _envelope(unit_id="page-1", *, quote="脉冲2a", bbox=None, value="脉冲2a"):
    return {
        "unit_id": unit_id,
        "observation_count": 1,
        "coverage_state": "complete",
        "failed_regions": [],
        "observations": [{
            "proposal_id": "p1",
            "observation_type": "test_item",
            "entity_name": value,
            "field_name": "test_item_presence",
            "raw_value": value,
            "normalized_value": value,
            "source_quote": quote,
            "bbox": bbox or [],
            "confidence": 0.98,
        }],
    }


def _empty_envelope(unit_id="page-1"):
    return {
        "unit_id": unit_id, "observation_count": 0,
        "coverage_state": "complete", "failed_regions": [], "observations": [],
    }


def _unit(**updates):
    data = {
        "unit_id": "page-1",
        "graph_id": "run-1",
        "doc_id": "doc-1",
        "doc_type": "final_report",
        "filename": "report.pdf",
        "native_text": "测试项目：脉冲2a",
        "page_number": 5,
        "image_data_urls": ["data:image/png;base64,AAAA"],
        "visual_reason": "none",
    }
    data.update(updates)
    return EvidenceGraphDocumentUnit(**data)


@pytest.mark.asyncio
async def test_digital_page_uses_native_only():
    gateway = FakeGateway([_envelope(quote="脉冲2a")])

    result = await extract_evidence_graph_unit(_unit(), gateway)

    assert gateway.calls == [("structured_extraction", "evidence_graph_native_extraction")]
    assert result.status == "complete"
    assert result.channels_used == ["native"]
    assert result.evidence[0].bbox == []


@pytest.mark.asyncio
async def test_native_page_persists_verified_layout_source_anchor():
    gateway = FakeGateway([_envelope(quote="脉冲2a")])
    unit = _unit(
        source_hash="a" * 64,
        rendered_pdf_hash="b" * 64,
        page_width=595,
        page_height=842,
        layout_lines=[{"text": "测试项目：脉冲2a", "bbox": [72, 100, 220, 122]}],
    )

    result = await extract_evidence_graph_unit(unit, gateway)

    record = result.evidence[0]
    assert record.bbox == [72.0, 100.0, 220.0, 122.0]
    assert record.content_hash == "a" * 64
    assert record.metadata["source_anchor"] == {
        "version": 1,
        "status": "located",
        "coordinate_space": "pdf_points",
        "method": "native_layout_line",
        "anchor_quote": "测试项目：脉冲2a",
        "rendered_pdf_hash": "b" * 64,
        "page_width": 595.0,
        "page_height": 842.0,
        "page_rotation": 0,
    }


@pytest.mark.asyncio
async def test_empty_entity_name_is_repaired_only_from_explicit_scalar_value():
    envelope = _envelope(quote="脉冲2a")
    envelope["observations"][0]["entity_name"] = ""
    gateway = FakeGateway([envelope])

    result = await extract_evidence_graph_unit(_unit(), gateway)

    assert result.status == "complete"
    assert result.observations[0].entity_name == "脉冲2a"


@pytest.mark.asyncio
async def test_standalone_pulse_rows_become_independent_test_items():
    gateway = FakeGateway([_empty_envelope()])
    unit = _unit(native_text="测试项目\nP1\nP2a\nP2b\nP3a")

    result = await extract_evidence_graph_unit(unit, gateway)

    assert {item.entity_name for item in result.observations} == {"P1", "P2A", "P2B", "P3A"}
    assert all(item.extraction_sources == ["native_identity_marker"] for item in result.observations)
    assert len(result.evidence) == 4


@pytest.mark.asyncio
async def test_duration_unit_is_not_misclassified_as_implicit_pulse_suffix():
    gateway = FakeGateway([_empty_envelope()])
    unit = _unit(native_text="试验持续时间\n1h\n30s\nP1\n2a\n2b")

    result = await extract_evidence_graph_unit(unit, gateway)

    assert {item.entity_name for item in result.observations} == {"P1", "P2A", "P2B"}
    assert "P1H" not in {item.entity_name for item in result.observations}


@pytest.mark.asyncio
async def test_explicit_pulse_enumeration_is_expanded_without_fuzzy_matching():
    gateway = FakeGateway([_empty_envelope()])
    unit = _unit(native_text="测试脉冲：1/2a/2b/3a")

    result = await extract_evidence_graph_unit(unit, gateway)

    assert {item.entity_name for item in result.observations} == {"P1", "P2A", "P2B", "P3A"}
    assert {item.raw_value for item in result.observations} == {"测试脉冲：1/2a/2b/3a"}


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [
    "试验脉冲P4：模拟启动电机通电时产生的电压降低",
    "测试脉冲：脉冲4",
])
async def test_labeled_pulse_four_becomes_shared_p4_identity(source):
    gateway = FakeGateway([_empty_envelope()])
    result = await extract_evidence_graph_unit(_unit(native_text=source), gateway)

    marker = next(item for item in result.observations if item.entity_name == "P4")
    assert marker.raw_value == source
    assert marker.extraction_sources == ["native_identity_marker"]


@pytest.mark.asyncio
async def test_identity_markers_survive_llm_schema_rejection():
    invalid = {"unit_id": "page-1", "observations": []}
    gateway = FakeGateway([invalid])
    unit = _unit(doc_type="original_records", native_text="脉冲1\n脉冲2a\n脉冲2b\n脉冲3a")

    result = await extract_evidence_graph_unit(unit, gateway)

    assert result.status == "needs_review"
    assert {item.entity_name for item in result.observations} == {"P1", "P2A", "P2B", "P3A"}
    assert result.errors


@pytest.mark.asyncio
async def test_one_invalid_observation_does_not_discard_valid_page_evidence():
    envelope = _envelope(quote="脉冲2a")
    envelope["observation_count"] = 2
    envelope["observations"].append({
        "proposal_id": "broken",
        "observation_type": "not_allowed",
        "entity_name": "坏记录",
        "field_name": "bad",
        "raw_value": "坏记录",
        "source_quote": "坏记录",
        "bbox": [],
        "confidence": 0.9,
    })
    gateway = FakeGateway([envelope])

    result = await extract_evidence_graph_unit(_unit(), gateway)

    assert result.status == "needs_review"
    assert [item.entity_name for item in result.observations] == ["脉冲2a"]
    assert len(result.evidence) == 1
    assert any("第2条" in error for error in result.errors)


@pytest.mark.asyncio
async def test_native_malformed_bbox_is_normalized_before_item_validation():
    envelope = _envelope(quote="脉冲2a", bbox=[1, 2])
    gateway = FakeGateway([envelope])

    result = await extract_evidence_graph_unit(_unit(), gateway)

    assert result.status == "complete"
    assert len(result.observations) == 1
    assert result.evidence[0].bbox == []


@pytest.mark.asyncio
async def test_scan_page_uses_qwen_vision_only():
    gateway = FakeGateway([_envelope(bbox=[10, 20, 200, 60])])
    unit = _unit(native_text="", visual_reason="scan")

    result = await extract_evidence_graph_unit(unit, gateway)

    assert gateway.calls == [("visual_extraction", "evidence_graph_visual_extraction")]
    assert result.status == "complete"
    assert result.channels_used == ["qwen_vision"]
    assert result.evidence[0].bbox == [10, 20, 200, 60]


@pytest.mark.asyncio
async def test_complex_table_uses_native_and_vision_without_duplicate_ocr_pipeline():
    native = _envelope(quote="脉冲2a")
    visual = _envelope(quote="脉冲2a", bbox=[10, 20, 200, 60])
    gateway = FakeGateway([native, visual])

    result = await extract_evidence_graph_unit(
        _unit(visual_reason="complex_table"), gateway,
    )

    assert [call[0] for call in gateway.calls] == ["structured_extraction", "visual_extraction"]
    assert len(result.observations) == 1
    assert len(result.observations[0].evidence_ids) == 2
    assert result.observations[0].extraction_sources == ["native", "qwen_vision"]


@pytest.mark.asyncio
async def test_visual_observation_without_bbox_is_rejected():
    gateway = FakeGateway([_envelope(bbox=[])])

    result = await extract_evidence_graph_unit(
        _unit(native_text="", visual_reason="scan"), gateway,
    )

    assert result.status == "needs_review"
    assert result.observations == []
    assert "缺少页面坐标" in result.errors[0]


@pytest.mark.asyncio
async def test_targeted_recovery_is_visual_only_and_appends_auditable_metadata():
    gateway = FakeGateway([_envelope(bbox=[10, 20, 200, 60])])

    result = await recover_targeted_evidence(_unit(), ["脉冲2a", "P2a"], gateway)

    assert gateway.calls == [("visual_extraction", "evidence_graph_targeted_visual_recovery")]
    assert result.status == "complete"
    assert result.evidence[0].metadata["targeted_recovery"] is True
    assert len(result.evidence[0].metadata["target_name_hashes"]) == 2
