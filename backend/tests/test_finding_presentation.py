import pytest

from services.evidence_graph_models import (
    EvidenceRecord,
    FindingStatus,
    GraphRun,
    GraphScope,
    GraphNode,
    NodeType,
    ResolutionStatus,
    ReviewFinding,
)
from services.evidence_graph_store import EvidenceGraphStore
from services.finding_llm_explanation import _payload, enrich_finding_supplements


def _seed(store: EvidenceGraphStore) -> None:
    store.init_schema()
    store.create_run(GraphRun(graph_id="run-copy", scope=GraphScope.RUN, set_id="set-copy"))
    store.add_evidence(EvidenceRecord(
        graph_id="run-copy", evidence_id="ev-plan", doc_id="plan",
        doc_type="test_plan", page_number=2,
        exact_quote="开路测试", extraction_method="native_pdf", confidence=1,
    ))
    store.add_node(GraphNode(
        graph_id="run-copy", node_id="node-open", node_type=NodeType.TEST_REQUIREMENT,
        label="开路测试", resolution_status=ResolutionStatus.RESOLVED,
    ))
    store.add_finding(ReviewFinding(
        graph_id="run-copy", finding_id="finding-open",
        check_id="GRAPH-COVERAGE-001", status=FindingStatus.UNRESOLVED,
        severity="warning", title="开路测试证据尚未闭合",
        description="计划有开路测试，候选名称尚未唯一对应。",
        subject_node_ids=["node-open"], evidence_ids=["ev-plan"],
        dedupe_key="coverage:open",
    ))


def test_store_persists_structured_copy_without_rewriting_description(tmp_path):
    store = EvidenceGraphStore(tmp_path / "copy.db")
    _seed(store)

    finding = store.get_snapshot("run-copy").findings[0]
    presentation = finding.metadata["presentation"]

    assert presentation["version"] == "finding-copy-1"
    assert presentation["status_label"] == "需要人工确认"
    assert presentation["subject"] == "开路测试"
    assert "不能直接判定" in presentation["impact"]
    assert finding.description == "计划有开路测试，候选名称尚未唯一对应。"


def test_coverage_copy_subject_does_not_include_status_suffix(tmp_path):
    store = EvidenceGraphStore(tmp_path / "copy-suffix.db")
    store.init_schema()
    store.create_run(GraphRun(graph_id="run-suffix", scope=GraphScope.RUN, set_id="set-suffix"))
    store.add_evidence(EvidenceRecord(
        graph_id="run-suffix", evidence_id="ev", doc_id="plan",
        doc_type="test_plan", page_number=2, exact_quote="燃烧防护",
    ))
    store.add_node(GraphNode(
        graph_id="run-suffix", node_id="node", node_type=NodeType.TEST_REQUIREMENT,
        label="燃烧防护", resolution_status=ResolutionStatus.RESOLVED,
    ))
    store.add_finding(ReviewFinding(
        graph_id="run-suffix", finding_id="finding", check_id="GRAPH-COVERAGE-001",
        status=FindingStatus.CONFIRMED_ERROR, title="燃烧防护未找到执行记录和报告结果",
        subject_node_ids=["node"], evidence_ids=["ev"], dedupe_key="suffix",
    ))
    assert store.get_snapshot("run-suffix").findings[0].metadata["presentation"]["subject"] == "燃烧防护"


def test_llm_payload_only_contains_findings_that_need_human_confirmation(tmp_path):
    store = EvidenceGraphStore(tmp_path / "llm-scope.db")
    _seed(store)
    store.add_finding(ReviewFinding(
        graph_id="run-copy", finding_id="finding-error",
        check_id="GRAPH-COVERAGE-001", status=FindingStatus.CONFIRMED_ERROR,
        severity="error", title="燃烧防护未找到执行记录和报告结果",
        subject_node_ids=["node-open"], evidence_ids=["ev-plan"],
        dedupe_key="coverage:error",
    ))

    payload, allowed = _payload(store.get_snapshot("run-copy"))

    assert [item["finding_id"] for item in payload] == ["finding-open"]
    assert set(allowed) == {"finding-open"}


@pytest.mark.asyncio
async def test_llm_supplement_is_evidence_bound_and_persisted(tmp_path):
    store = EvidenceGraphStore(tmp_path / "llm.db")
    _seed(store)

    class FakeGateway:
        def resolve(self, task):
            assert task == "finding_explanation"
            return object()

        async def call_json(self, task, **kwargs):
            assert task == "finding_explanation"
            return {"items": [{
                "finding_id": "finding-open",
                "evidence_ids": ["ev-plan"],
                "judgment": "可能是同一项目的不同层级写法。",
                "basis": "依据测试计划第2页的项目名称。",
                "uncertainty": "尚无唯一父子关系证据。",
                "handling": "保留为需要人工确认。",
            }]}

    assert await enrich_finding_supplements("run-copy", store, FakeGateway()) == 1
    supplement = store.get_snapshot("run-copy").findings[0].metadata["presentation"]["llm_supplement"]
    assert supplement["status"] == "ready"
    assert supplement["evidence_ids"] == ["ev-plan"]


@pytest.mark.asyncio
async def test_llm_supplement_rejects_unknown_evidence(tmp_path):
    store = EvidenceGraphStore(tmp_path / "llm-invalid.db")
    _seed(store)

    class FakeGateway:
        def resolve(self, _task):
            return object()

        async def call_json(self, *_args, **_kwargs):
            return {"items": [{
                "finding_id": "finding-open",
                "evidence_ids": ["not-in-finding"],
                "judgment": "猜测",
                "basis": "没有证据",
                "uncertainty": "无法确认",
                "handling": "待确认",
            }]}

    assert await enrich_finding_supplements("run-copy", store, FakeGateway()) == 0
    supplement = store.get_snapshot("run-copy").findings[0].metadata["presentation"]["llm_supplement"]
    assert supplement["status"] == "not_generated"
