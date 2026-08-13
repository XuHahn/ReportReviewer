import pytest

from services.evidence_graph_models import (
    EvidenceRecord,
    GraphNode,
    NodeType,
    RelationOrigin,
    RelationType,
)
from services.evidence_graph_relationships import resolve_relationship_candidates


class FakeGateway:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call_json(self, task, **kwargs):
        self.calls.append((task, kwargs["stage"]))
        return self.responses.pop(0)


def _node(node_id, node_type, label, evidence_id):
    return GraphNode(
        node_id=node_id,
        graph_id="run-1",
        node_type=node_type,
        label=label,
        properties={"evidence_ids": [evidence_id]},
    )


def _evidence(evidence_id, quote):
    return EvidenceRecord(
        evidence_id=evidence_id,
        graph_id="run-1",
        doc_id="doc-1",
        doc_type="test_plan",
        exact_quote=quote,
    )


@pytest.mark.asyncio
async def test_two_stage_relationship_gate_accepts_parent_subitem():
    nodes = [
        _node("family", NodeType.TEST_DEFINITION, "电源线瞬态传导抗扰", "ev-family"),
        _node("p2a", NodeType.TEST_SUBITEM_DEFINITION, "脉冲2a", "ev-p2a"),
    ]
    evidence = [_evidence("ev-family", "电源线瞬态传导抗扰"), _evidence("ev-p2a", "脉冲2a")]
    proposal = {"candidates": [{
        "candidate_id": "c1",
        "source_node_id": "family",
        "target_node_id": "p2a",
        "relation_type": "parent_subitem",
        "evidence_ids": ["ev-family", "ev-p2a"],
        "rationale": "子项具有独立执行行",
        "confidence": 0.96,
    }]}
    rebuttal = {"decisions": [{
        "candidate_id": "c1",
        "decision": "accepted",
        "rationale": "未发现条件项或同名异族反证",
        "confidence": 0.94,
    }]}

    gateway = FakeGateway([proposal, rebuttal])
    result = await resolve_relationship_candidates("run-1", nodes, evidence, gateway)

    assert len(result.accepted_edges) == 1
    assert result.accepted_edges[0].relation_type == RelationType.PARENT_SUBITEM
    assert result.accepted_edges[0].origin == RelationOrigin.MACHINE_INFERRED
    assert gateway.calls == [
        ("relationship_proposal", "evidence_graph_relationship_proposal"),
        ("relationship_rebuttal", "evidence_graph_relationship_rebuttal"),
    ]


@pytest.mark.asyncio
async def test_relationship_candidate_must_cite_both_endpoint_evidence():
    nodes = [
        _node("family", NodeType.TEST_DEFINITION, "瞬态抗扰", "ev-family"),
        _node("p2a", NodeType.TEST_SUBITEM_DEFINITION, "脉冲2a", "ev-p2a"),
    ]
    evidence = [_evidence("ev-family", "瞬态抗扰"), _evidence("ev-p2a", "脉冲2a")]
    gateway = FakeGateway([{"candidates": [{
        "candidate_id": "c1",
        "source_node_id": "family",
        "target_node_id": "p2a",
        "relation_type": "parent_subitem",
        "evidence_ids": ["ev-family"],
        "rationale": "只引用了父项证据",
        "confidence": 0.99,
    }]}])

    result = await resolve_relationship_candidates("run-1", nodes, evidence, gateway)

    assert result.accepted_edges == []
    assert result.rejected_candidate_ids == ["c1"]
    assert any("两个端点" in error for error in result.errors)
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_hallucinated_evidence_is_rejected_before_rebuttal():
    nodes = [
        _node("family", NodeType.TEST_DEFINITION, "瞬态抗扰", "ev-family"),
        _node("p2a", NodeType.TEST_SUBITEM_DEFINITION, "脉冲2a", "ev-p2a"),
    ]
    evidence = [_evidence("ev-family", "瞬态抗扰"), _evidence("ev-p2a", "脉冲2a")]
    proposal = {"candidates": [{
        "candidate_id": "c1",
        "source_node_id": "family",
        "target_node_id": "p2a",
        "relation_type": "parent_subitem",
        "evidence_ids": ["hallucinated"],
        "rationale": "模型猜测",
        "confidence": 0.99,
    }]}
    gateway = FakeGateway([proposal])

    result = await resolve_relationship_candidates("run-1", nodes, evidence, gateway)

    assert result.accepted_edges == []
    assert result.rejected_candidate_ids == ["c1"]
    assert len(gateway.calls) == 1


@pytest.mark.asyncio
async def test_illegal_endpoint_types_are_rejected():
    nodes = [
        _node("instrument", NodeType.INSTRUMENT, "电源", "ev-a"),
        _node("p2a", NodeType.TEST_SUBITEM_DEFINITION, "脉冲2a", "ev-b"),
    ]
    evidence = [_evidence("ev-a", "电源"), _evidence("ev-b", "脉冲2a")]
    proposal = {"candidates": [{
        "candidate_id": "c1",
        "source_node_id": "instrument",
        "target_node_id": "p2a",
        "relation_type": "parent_subitem",
        "evidence_ids": ["ev-a", "ev-b"],
        "rationale": "错误端点",
        "confidence": 0.99,
    }]}

    result = await resolve_relationship_candidates(
        "run-1", nodes, evidence, FakeGateway([proposal]),
    )

    assert result.accepted_edges == []
    assert result.rejected_candidate_ids == ["c1"]


@pytest.mark.asyncio
async def test_incomplete_rebuttal_keeps_candidate_unresolved():
    nodes = [
        _node("family", NodeType.TEST_DEFINITION, "瞬态抗扰", "ev-a"),
        _node("p2a", NodeType.TEST_SUBITEM_DEFINITION, "脉冲2a", "ev-b"),
    ]
    evidence = [_evidence("ev-a", "瞬态抗扰"), _evidence("ev-b", "脉冲2a")]
    proposal = {"candidates": [{
        "candidate_id": "c1",
        "source_node_id": "family",
        "target_node_id": "p2a",
        "relation_type": "parent_subitem",
        "evidence_ids": ["ev-a", "ev-b"],
        "rationale": "可能是父子关系",
        "confidence": 0.91,
    }]}

    result = await resolve_relationship_candidates(
        "run-1", nodes, evidence, FakeGateway([proposal, {"decisions": []}]),
    )

    assert result.accepted_edges == []
    assert result.unresolved_candidate_ids == ["c1"]
    assert any("未逐项覆盖" in error for error in result.errors)
