from services.evidence_graph_coverage import ObservedTestItem
from services.evidence_graph_models import EvidenceRecord, FindingStatus, RelationType
from services.evidence_graph_review_engine import EvidenceGraphReviewEngine
from services.evidence_graph_store import EvidenceGraphStore


def _item(item_id, doc_type, name, evidence_id):
    return ObservedTestItem(
        item_id=item_id,
        doc_type=doc_type,
        name=name,
        evidence_ids=[evidence_id],
    )


def _evidence(graph_id, evidence_id, doc_type, quote):
    return EvidenceRecord(
        graph_id=graph_id,
        evidence_id=evidence_id,
        doc_id=f"doc-{doc_type}",
        doc_type=doc_type,
        page_number=5,
        exact_quote=quote,
        extraction_method="test",
        confidence=1,
    )


def test_end_to_end_pulse_coverage_slice_persists_edges_findings_and_events(tmp_path):
    store = EvidenceGraphStore(tmp_path / "graph.db")
    store.init_schema()
    engine = EvidenceGraphReviewEngine(store)
    engine.create_run("run-1", "set-1")
    codes = ("P1", "P2a", "P2b", "P3a")
    requirements = [
        _item(f"plan-{code}", "test_plan", f"电源线瞬态传导抗扰{code}", f"ev-plan-{code}")
        for code in codes
    ]
    raw_items = [
        _item(f"raw-{code}", "original_records", f"脉冲{code[1:]}", f"ev-raw-{code}")
        for code in codes
    ]
    report_items = [
        _item(f"report-{code}", "final_report", f"脉冲{code[1:]}", f"ev-report-{code}")
        for code in codes
    ]
    evidence = []
    for code in codes:
        evidence.extend([
            _evidence("run-1", f"ev-plan-{code}", "test_plan", f"电源线瞬态传导抗扰{code}"),
            _evidence("run-1", f"ev-raw-{code}", "original_records", f"脉冲{code[1:]}"),
            _evidence("run-1", f"ev-report-{code}", "final_report", f"脉冲{code[1:]}"),
        ])

    engine.persist_test_item_coverage(
        "run-1", requirements, raw_items, report_items, evidence, [],
        plan_extraction_complete=True,
    )
    snapshot = store.get_snapshot("run-1")

    assert snapshot.run.status == "machine_complete"
    assert len(snapshot.findings) == 4
    assert all(finding.status == FindingStatus.CONFIRMED_PASS for finding in snapshot.findings)
    assert len(snapshot.edges) == 8
    assert {edge.relation_type for edge in snapshot.edges} == {
        RelationType.EXECUTED_AS, RelationType.REPORTED_AS,
    }
    assert any(event.event_type == "coverage_slice_completed" for event in snapshot.events)
