from services.evidence_anchor_finalizer import (
    finalize_finding_evidence_anchors,
    locate_evidence_layout,
)
from services.evidence_graph_extraction import EvidenceGraphDocumentUnit
from services.evidence_graph_models import (
    EvidenceRecord,
    FindingSeverity,
    FindingStatus,
    GraphNode,
    GraphRun,
    GraphScope,
    NodeType,
    ResolutionStatus,
    ReviewFinding,
)
from services.evidence_graph_store import EvidenceGraphStore
from services.document_preview import render_evidence_page
import fitz


def _unit(lines, *, unit_id="original_records:member-41/page-1"):
    return EvidenceGraphDocumentUnit(
        unit_id=unit_id,
        graph_id="graph-1",
        doc_id="doc-raw",
        doc_type="original_records",
        filename="反向电压.pdf",
        native_text="\n".join(item["text"] for item in lines),
        page_number=1,
        source_hash="source-hash",
        rendered_pdf_hash="render-hash",
        page_width=595,
        page_height=842,
        layout_lines=lines,
    )


def test_semantic_structured_row_locates_multi_column_raw_record():
    lines = [
        {"text": "反向电压", "bbox": [30, 293, 70, 303]},
        {"text": "电源线", "bbox": [108, 293, 140, 303]},
        {"text": "反向电压：14V", "bbox": [165, 293, 230, 303]},
        {"text": "60s", "bbox": [263, 293, 280, 303]},
        {"text": "C¹⁾", "bbox": [412, 293, 430, 303]},
        {"text": "符合", "bbox": [508, 293, 530, 303]},
    ]
    bbox, rectangles, method, _quote = locate_evidence_layout(
        _unit(lines),
        "反向电压 / 电源线 / 反向电压：14V / 60s / C / C¹⁾ / 符合",
        {},
    )
    assert method == "semantic_structured_row"
    assert bbox == [30.0, 293.0, 530.0, 303.0]
    assert len(rectangles) == 6


def test_parameter_ordinal_row_disambiguates_short_value():
    lines = [
        {"text": "2", "bbox": [30, 350, 36, 360]},
        {"text": "2 V± 0.2 V", "bbox": [300, 350, 355, 360]},
        {"text": "3", "bbox": [30, 387, 36, 397]},
        {"text": "10 Hz to 30 kHz", "bbox": [90, 387, 180, 397]},
        {"text": "2 V± 0.1 V", "bbox": [300, 387, 355, 397]},
    ]
    bbox, rectangles, method, _quote = locate_evidence_layout(
        _unit(lines, unit_id="final_report:page-12"),
        "2 V",
        {"parameter_name": "Severity 3", "comparison_value": "2V"},
    )
    assert method == "semantic_parameter_ordinal_row"
    assert bbox == [300.0, 387.0, 355.0, 397.0]
    assert rectangles == [bbox]


def test_strict_ordered_quote_locates_unique_wrapped_source():
    lines = [
        {"text": "Frequency range:", "bbox": [90, 100, 170, 110]},
        {"text": "10 Hz to 30 kHz", "bbox": [180, 100, 260, 110]},
        {"text": "30 kHz to 200 kHz", "bbox": [180, 112, 270, 122]},
    ]
    bbox, rectangles, method, _quote = locate_evidence_layout(
        _unit(lines, unit_id="final_report:page-12"),
        "Frequency range: 10 Hz to 30 kHz\n30 kHz to 200 kHz",
        {},
    )
    assert method == "strict_ordered_quote"
    assert bbox == [90.0, 100.0, 270.0, 122.0]
    assert len(rectangles) == 3


def test_strict_ordered_quote_rejects_repeated_text():
    lines = [
        {"text": "same wrapped evidence", "bbox": [20, 100, 120, 110]},
        {"text": "same wrapped evidence", "bbox": [20, 200, 120, 210]},
    ]
    bbox, _rectangles, method, _quote = locate_evidence_layout(
        _unit(lines), "same wrapped evidence", {},
    )
    assert bbox == []
    assert method == ""


def test_structured_metadata_row_disambiguates_repeated_table_values():
    lines = [
        {"text": "Reverse voltage", "bbox": [30, 100, 100, 110]},
        {"text": "power supply", "bbox": [120, 100, 180, 110]},
        {"text": "14V", "bbox": [220, 100, 245, 110]},
        {"text": "60s", "bbox": [270, 100, 290, 110]},
        {"text": "A", "bbox": [330, 100, 337, 110]},
        {"text": "Pass", "bbox": [430, 100, 455, 110]},
        {"text": "Reverse voltage", "bbox": [30, 200, 100, 210]},
        {"text": "power supply", "bbox": [120, 200, 180, 210]},
        {"text": "14V", "bbox": [220, 200, 245, 210]},
        {"text": "60s", "bbox": [270, 200, 290, 210]},
        {"text": "C", "bbox": [330, 200, 337, 210]},
        {"text": "Fail", "bbox": [430, 200, 450, 210]},
    ]
    metadata = {"comparison_rows": [{
        "test_item": "Reverse voltage", "injection_point": "power supply",
        "spec_requirement": "14V", "test_duration": "60s",
        "required_level": "C", "verdict_raw": "Fail",
    }]}
    bbox, rectangles, method, _quote = locate_evidence_layout(
        _unit(lines), "Reverse voltage power supply 14V 60s C Fail", metadata,
    )
    assert method == "structured_metadata_row"
    assert bbox == [30.0, 200.0, 450.0, 210.0]
    assert len(rectangles) >= 4


def test_finalizer_promotes_only_direct_unambiguous_evidence(tmp_path):
    store = EvidenceGraphStore(tmp_path / "graph.db")
    store.init_schema()
    store.create_run(GraphRun(
        graph_id="graph-1", scope=GraphScope.RUN, set_id="set-1", status="building",
    ))
    store.add_evidence(EvidenceRecord(
        evidence_id="evidence-1", graph_id="graph-1", doc_id="doc-raw",
        doc_type="original_records", page_number=1,
        exact_quote="反向电压 / 电源线 / 14V / 60s",
        metadata={"unit_id": "original_records:member-41/page-1"},
    ))
    store.add_node(GraphNode(
        node_id="node-1", graph_id="graph-1", node_type=NodeType.DOCUMENT_OBSERVATION,
        label="反向电压", resolution_status=ResolutionStatus.UNRESOLVED,
    ))
    store.add_finding(ReviewFinding(
        finding_id="finding-1", graph_id="graph-1", check_id="CHECK-1",
        status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
        title="结果冲突", subject_node_ids=["node-1"], evidence_ids=["evidence-1"],
        dedupe_key="CHECK-1:one",
    ))
    unit = _unit([
        {"text": "反向电压", "bbox": [30, 293, 70, 303]},
        {"text": "电源线", "bbox": [108, 293, 140, 303]},
        {"text": "14V", "bbox": [165, 293, 190, 303]},
        {"text": "60s", "bbox": [263, 293, 280, 303]},
    ])

    stats = finalize_finding_evidence_anchors("graph-1", store, [unit])
    enriched = store.get_evidence("graph-1", "evidence-1")

    assert stats["promoted"] == 1
    assert enriched is not None
    assert enriched.bbox == [30.0, 293.0, 280.0, 303.0]
    assert enriched.content_hash == "source-hash"
    assert enriched.metadata["source_anchor"]["status"] == "located"


def test_preview_exposes_union_bbox_for_verified_disconnected_table_cells():
    pdf = fitz.open()
    page = pdf.new_page()
    for x, text in ((40, "Voltage"), (140, "Supply"), (240, "14V"), (340, "60s"), (440, "Pass")):
        page.insert_text((x, 200), text)
    source = pdf.tobytes()
    pdf.close()

    preview = render_evidence_page(
        source, "record.pdf", 1,
        quote="Voltage / Supply / 14V / 60s / Pass",
    )

    assert preview.highlight_strategy == "semantic_structured_row"
    assert preview.resolved_bboxes[0] is not None
    assert len(preview.resolved_rectangles[0]) == 5
