import sqlite3

import pytest
from pydantic import ValidationError

from services.evidence_graph_models import (
    EvidenceRecord,
    FindingStatus,
    FindingDecision,
    GraphEdge,
    GraphNode,
    GraphRun,
    GraphScope,
    NodeType,
    RelationOrigin,
    RelationStatus,
    RelationType,
    ResolutionStatus,
    ReviewFinding,
)
from services.evidence_graph_store import EvidenceGraphStore
from services.evidence_graph_review_engine import EvidenceGraphReviewEngine


@pytest.fixture
def graph_store(tmp_path):
    store = EvidenceGraphStore(tmp_path / "evidence-graph.db")
    store.init_schema()
    store.create_run(GraphRun(graph_id="run-1", scope=GraphScope.RUN, set_id="set-1"))
    return store


def _node(node_id: str, node_type: NodeType, label: str) -> GraphNode:
    return GraphNode(
        graph_id="run-1",
        node_id=node_id,
        node_type=node_type,
        label=label,
        canonical_key=label.lower(),
        resolution_status=ResolutionStatus.RESOLVED,
    )


def _evidence(evidence_id: str = "ev-1") -> EvidenceRecord:
    return EvidenceRecord(
        graph_id="run-1",
        evidence_id=evidence_id,
        doc_id="report-1",
        doc_type="final_report",
        page_number=5,
        exact_quote="脉冲2a：试验结果符合",
        extraction_method="native_pdf",
        confidence=1,
    )


def test_graph_persists_parent_subitem_with_evidence(graph_store):
    graph_store.add_evidence(_evidence())
    graph_store.add_node(_node("family", NodeType.TEST_DEFINITION, "电源线瞬态传导抗扰"))
    graph_store.add_node(_node("p2a", NodeType.TEST_SUBITEM_DEFINITION, "P2a"))
    graph_store.add_edge(GraphEdge(
        graph_id="run-1",
        edge_id="edge-parent-p2a",
        source_node_id="family",
        target_node_id="p2a",
        relation_type=RelationType.PARENT_SUBITEM,
        status=RelationStatus.ACCEPTED,
        origin=RelationOrigin.MACHINE_INFERRED,
        confidence=0.96,
        evidence_ids=["ev-1"],
        rationale="标准结构和独立执行行共同证明父子关系",
    ))

    snapshot = graph_store.get_snapshot("run-1")

    assert [node.node_id for node in snapshot.nodes] == ["family", "p2a"]
    assert snapshot.edges[0].relation_type == RelationType.PARENT_SUBITEM
    assert snapshot.edges[0].evidence_ids == ["ev-1"]


def test_standard_advisory_persists_duplicate_evidence_id_once(graph_store, monkeypatch):
    duplicate = _evidence("standard-ev")

    monkeypatch.setattr(
        "services.evidence_graph_review_engine.build_standard_provenance",
        lambda *_args: ([duplicate], [], []),
    )
    monkeypatch.setattr(
        "services.evidence_graph_review_engine.build_standard_scope_advisories",
        lambda *_args: ([duplicate], [], []),
    )
    monkeypatch.setattr(
        "services.evidence_graph_review_engine.build_standard_plan_coverage",
        lambda *_args: ([], 0, 0),
    )

    EvidenceGraphReviewEngine(graph_store).persist_standard_advisories(
        "run-1", [], [{"status": "published"}],
    )

    with sqlite3.connect(graph_store.db_path) as conn:
        assert conn.execute(
            "select count(*) from evidence_graph_evidence where graph_id=? and evidence_id=?",
            ("run-1", "standard-ev"),
        ).fetchone()[0] == 1


def test_run_metadata_updates_merge_without_erasing_existing_keys(graph_store):
    graph_store.update_run_metadata("run-1", {"first": {"value": 1}})
    graph_store.update_run_metadata("run-1", {"second": [2]})

    metadata = graph_store.get_snapshot("run-1").run.metadata
    assert metadata == {"first": {"value": 1}, "second": [2]}


def test_accepted_relation_requires_evidence():
    with pytest.raises(ValidationError, match="must reference evidence"):
        GraphEdge(
            graph_id="run-1",
            edge_id="edge-1",
            source_node_id="a",
            target_node_id="b",
            relation_type=RelationType.SAME_ENTITY,
            status=RelationStatus.ACCEPTED,
        )


def test_store_rejects_cross_graph_or_unknown_evidence(graph_store):
    graph_store.add_node(_node("family", NodeType.TEST_DEFINITION, "瞬态抗扰"))
    graph_store.add_node(_node("p2b", NodeType.TEST_SUBITEM_DEFINITION, "P2b"))

    with pytest.raises(ValueError, match="unknown evidence_id"):
        graph_store.add_edge(GraphEdge(
            graph_id="run-1",
            edge_id="edge-1",
            source_node_id="family",
            target_node_id="p2b",
            relation_type=RelationType.PARENT_SUBITEM,
            status=RelationStatus.ACCEPTED,
            origin=RelationOrigin.DETERMINISTIC,
            confidence=1,
            evidence_ids=["missing"],
        ))


def test_confirmed_finding_is_evidence_gated_and_deduplicated(graph_store):
    graph_store.add_evidence(_evidence())
    graph_store.add_node(_node("p2a", NodeType.TEST_EXECUTION, "P2a执行"))
    finding = ReviewFinding(
        graph_id="run-1",
        finding_id="finding-1",
        check_id="GRAPH-COVERAGE-001",
        status=FindingStatus.CONFIRMED_PASS,
        title="P2a执行证据完整",
        subject_node_ids=["p2a"],
        evidence_ids=["ev-1"],
        dedupe_key="coverage:p2a",
    )
    graph_store.add_finding(finding)

    with pytest.raises(sqlite3.IntegrityError):
        graph_store.add_finding(finding.model_copy(update={"finding_id": "finding-2"}))

    snapshot = graph_store.get_snapshot("run-1")
    assert snapshot.findings[0].status == FindingStatus.CONFIRMED_PASS
    assert snapshot.findings[0].evidence_ids == ["ev-1"]


def test_claim_node_requires_traceable_payload():
    with pytest.raises(ValidationError, match="claim node missing properties"):
        GraphNode(
            graph_id="run-1",
            node_id="claim-1",
            node_type=NodeType.CLAIM,
            label="电压声明",
            properties={"raw_value": "+50V"},
        )


def test_application_database_initialization_creates_graph_tables(_seeded_db, db_conn):
    tables = {
        row[0] for row in db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "evidence_graph_runs",
        "evidence_graph_evidence",
        "evidence_graph_nodes",
        "evidence_graph_edges",
        "evidence_graph_findings",
        "evidence_graph_events",
        "unified_extraction_cache",
    }.issubset(tables)


def test_list_runs_returns_graph_counts(graph_store):
    graph_store.add_evidence(_evidence())
    graph_store.add_node(_node("p2a", NodeType.TEST_EXECUTION, "P2a执行"))
    graph_store.add_finding(ReviewFinding(
        graph_id="run-1",
        finding_id="finding-list",
        check_id="GRAPH-COVERAGE-001",
        status=FindingStatus.CONFIRMED_PASS,
        title="P2a证据完整",
        subject_node_ids=["p2a"],
        evidence_ids=["ev-1"],
        dedupe_key="coverage:list:p2a",
    ))
    graph_store.add_finding(ReviewFinding(
        graph_id="run-1",
        finding_id="finding-advisory",
        check_id="GRAPH-ADVISORY-001",
        status=FindingStatus.UNRESOLVED_ADVISORY,
        title="需要人工确认",
        subject_node_ids=["p2a"],
        evidence_ids=["ev-1"],
        dedupe_key="advisory:list:p2a",
    ))

    runs = graph_store.list_runs("set-1")

    assert runs[0]["graph_id"] == "run-1"
    assert runs[0]["node_count"] == 1
    assert runs[0]["finding_count"] == 2
    assert runs[0]["unresolved_count"] == 1


def test_human_decision_is_separate_from_machine_finding(graph_store):
    graph_store.add_evidence(_evidence())
    graph_store.add_node(_node("p2a", NodeType.TEST_EXECUTION, "P2a执行"))
    graph_store.add_finding(ReviewFinding(
        graph_id="run-1", finding_id="finding-human", check_id="GRAPH-COVERAGE-001",
        status=FindingStatus.UNRESOLVED, title="待确认", subject_node_ids=["p2a"],
        evidence_ids=["ev-1"], dedupe_key="human:p2a",
    ))
    graph_store.set_finding_decision(FindingDecision(
        graph_id="run-1", finding_id="finding-human", decision="dismissed",
        resolution_code="not_applicable", resolution_status="not_applicable",
        comment="本项目不在本次委托范围", actor_id="reviewer-1",
    ))

    snapshot = graph_store.get_snapshot("run-1")

    assert snapshot.findings[0].status == FindingStatus.UNRESOLVED
    assert snapshot.decisions[0].decision == "dismissed"
    assert snapshot.decisions[0].resolution_code == "not_applicable"
    assert snapshot.decisions[0].resolution_status == "not_applicable"


def test_decision_schema_migrates_existing_database(tmp_path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """CREATE TABLE evidence_graph_finding_decisions (
               graph_id TEXT NOT NULL, finding_id TEXT NOT NULL,
               decision TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '',
               actor_id TEXT NOT NULL, created_at TEXT NOT NULL,
               PRIMARY KEY (graph_id, finding_id))"""
        )

    store = EvidenceGraphStore(db_path)
    store.init_schema()

    with sqlite3.connect(db_path) as conn:
        columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(evidence_graph_finding_decisions)"
            )
        }
    assert {"resolution_code", "resolution_status"}.issubset(columns)
