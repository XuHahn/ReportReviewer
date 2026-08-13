"""Evidence-gated DeepSeek fallback for graph relationships."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from services.evidence_graph_models import (
    EvidenceRecord,
    GraphEdge,
    GraphNode,
    NodeType,
    RelationOrigin,
    RelationStatus,
    RelationType,
)
from utils.logger import get_logger


logger = get_logger(__name__)


class RelationshipGateway(Protocol):
    async def call_json(
        self,
        task: str,
        *,
        system_prompt: str,
        user_prompt: str | list[dict],
        stage: str,
        timeout: int,
        max_tokens: int,
    ) -> dict: ...


class RelationshipCandidate(BaseModel):
    candidate_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    relation_type: RelationType
    evidence_ids: list[str] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class RelationshipEnvelope(BaseModel):
    candidates: list[RelationshipCandidate] = Field(max_length=500)


class RelationshipDecision(BaseModel):
    candidate_id: str
    decision: Literal["accepted", "rejected", "unresolved"]
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class RelationshipRebuttalEnvelope(BaseModel):
    decisions: list[RelationshipDecision] = Field(max_length=500)


class RelationshipResolution(BaseModel):
    accepted_edges: list[GraphEdge] = Field(default_factory=list)
    unresolved_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidate_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


_PROPOSAL_SYSTEM = """你是工业检测文档关系候选生成器。只使用输入已有节点和证据，判断明确的关系候选。
允许关系仅为 parent_subitem, same_entity, condition_of, variant_of, required_by, executed_as, reported_as, has_claim, has_result, covers, uses_instrument, supported_by, governed_by, contradicts, summarizes, signed_as, observed_during。
父测试与子测试使用 parent_subitem，不得标成 same_entity；测试条件使用 condition_of；名称不同但确为同一对象才使用 same_entity。
不得根据常识补充文档中不存在的执行事实。evidence_ids只能引用输入证据。拿不准时不要生成候选。
返回严格JSON：{"candidates":[{"candidate_id":"c1","source_node_id":"n1","target_node_id":"n2","relation_type":"parent_subitem","evidence_ids":["e1"],"rationale":"理由","confidence":0.95}]}。"""


_REBUTTAL_SYSTEM = """你是工业检测文档关系反证审核员。输入为已提出的关系候选、节点和原文证据。
逐项寻找反证：父子项是否被误当同一实体，条件是否被误当测试，执行事实是否仅由计划或标准推断，名称是否可能属于不同测试族、样品、模式或轮次。
只能审核输入候选，不得新增候选。每个candidate_id必须且只能返回一次。证据不能确定时返回unresolved。
返回严格JSON：{"decisions":[{"candidate_id":"c1","decision":"accepted","rationale":"反证后仍成立","confidence":0.94}]}。"""


_RELATION_ENDPOINTS: dict[RelationType, tuple[set[NodeType], set[NodeType]]] = {
    RelationType.PARENT_SUBITEM: (
        {NodeType.TEST_DEFINITION, NodeType.TEST_REQUIREMENT},
        {NodeType.TEST_SUBITEM_DEFINITION, NodeType.TEST_REQUIREMENT},
    ),
    RelationType.CONDITION_OF: (
        {NodeType.REQUIREMENT_DIMENSION, NodeType.COVERAGE_DIMENSION, NodeType.CLAIM},
        {NodeType.TEST_REQUIREMENT, NodeType.TEST_EXECUTION, NodeType.TEST_DEFINITION},
    ),
    RelationType.REQUIRED_BY: (
        {NodeType.TEST_REQUIREMENT, NodeType.REQUIREMENT_DIMENSION},
        {NodeType.DOCUMENT_OBSERVATION, NodeType.STANDARD_CLAUSE},
    ),
    RelationType.EXECUTED_AS: (
        {NodeType.TEST_REQUIREMENT, NodeType.TEST_DEFINITION, NodeType.TEST_SUBITEM_DEFINITION},
        {NodeType.TEST_EXECUTION},
    ),
    RelationType.REPORTED_AS: (
        {NodeType.TEST_EXECUTION, NodeType.TEST_REQUIREMENT},
        {NodeType.DOCUMENT_OBSERVATION},
    ),
    RelationType.HAS_CLAIM: (
        {NodeType.TEST_REQUIREMENT, NodeType.TEST_EXECUTION, NodeType.DOCUMENT_OBSERVATION,
         NodeType.INSTRUMENT, NodeType.PERSON_ROLE},
        {NodeType.CLAIM},
    ),
    RelationType.HAS_RESULT: (
        {NodeType.TEST_EXECUTION, NodeType.TEST_REQUIREMENT},
        {NodeType.CLAIM},
    ),
    RelationType.COVERS: (
        {NodeType.TEST_REQUIREMENT, NodeType.TEST_EXECUTION, NodeType.DOCUMENT_OBSERVATION},
        {NodeType.COVERAGE_DIMENSION},
    ),
    RelationType.USES_INSTRUMENT: (
        {NodeType.TEST_EXECUTION},
        {NodeType.INSTRUMENT},
    ),
    RelationType.SUPPORTED_BY: (
        set(NodeType),
        {NodeType.DOCUMENT_OBSERVATION, NodeType.EVIDENCE_ARTIFACT},
    ),
    RelationType.GOVERNED_BY: (
        {NodeType.TEST_DEFINITION, NodeType.TEST_SUBITEM_DEFINITION,
         NodeType.TEST_REQUIREMENT, NodeType.REQUIREMENT_DIMENSION},
        {NodeType.STANDARD_CLAUSE, NodeType.STANDARD_REFERENCE},
    ),
    RelationType.SUMMARIZES: (
        {NodeType.CLAIM, NodeType.DOCUMENT_OBSERVATION},
        {NodeType.CLAIM, NodeType.TEST_EXECUTION},
    ),
    RelationType.SIGNED_AS: (
        {NodeType.PERSON_ROLE},
        {NodeType.DOCUMENT, NodeType.DOCUMENT_OBSERVATION},
    ),
    RelationType.OBSERVED_DURING: (
        {NodeType.ANOMALY_OBSERVATION},
        {NodeType.TEST_EXECUTION},
    ),
}


def relation_endpoints_allowed(relation_type: RelationType,
                               source_type: NodeType,
                               target_type: NodeType) -> bool:
    if relation_type in {RelationType.SAME_ENTITY, RelationType.VARIANT_OF}:
        if source_type == target_type:
            return True
        test_types = {
            NodeType.TEST_DEFINITION, NodeType.TEST_SUBITEM_DEFINITION,
            NodeType.TEST_REQUIREMENT, NodeType.TEST_EXECUTION,
            NodeType.DOCUMENT_OBSERVATION,
        }
        return source_type in test_types and target_type in test_types
    if relation_type == RelationType.CONTRADICTS:
        return source_type in {NodeType.CLAIM, NodeType.DOCUMENT_OBSERVATION} and target_type in {
            NodeType.CLAIM, NodeType.DOCUMENT_OBSERVATION,
        }
    allowed = _RELATION_ENDPOINTS.get(relation_type)
    return bool(allowed and source_type in allowed[0] and target_type in allowed[1])


def _context(nodes: list[GraphNode], evidence: list[EvidenceRecord]) -> dict:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    node_evidence: dict[str, list[str]] = defaultdict(list)
    for node in nodes:
        for evidence_id in node.properties.get("evidence_ids", []):
            if evidence_id in evidence_by_id:
                node_evidence[node.node_id].append(evidence_id)
    return {
        "nodes": [{
            "node_id": node.node_id,
            "node_type": str(node.node_type),
            "label": node.label,
            "canonical_key": node.canonical_key,
            "properties": {
                key: value for key, value in node.properties.items()
                if key not in {"raw_content", "full_text", "prompt"}
            },
            "evidence_ids": node_evidence[node.node_id],
        } for node in nodes],
        "evidence": [{
            "evidence_id": item.evidence_id,
            "doc_type": item.doc_type,
            "page_number": item.page_number,
            "exact_quote": item.exact_quote,
        } for item in evidence],
    }


def _validate_candidates(envelope: RelationshipEnvelope,
                         nodes: list[GraphNode], evidence: list[EvidenceRecord]
                         ) -> tuple[list[RelationshipCandidate], list[str], list[str]]:
    nodes_by_id = {node.node_id: node for node in nodes}
    evidence_ids = {item.evidence_id for item in evidence}
    valid: list[RelationshipCandidate] = []
    rejected: list[str] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for candidate in envelope.candidates:
        if candidate.candidate_id in seen_ids:
            rejected.append(candidate.candidate_id)
            errors.append("关系候选ID重复")
            continue
        seen_ids.add(candidate.candidate_id)
        source = nodes_by_id.get(candidate.source_node_id)
        target = nodes_by_id.get(candidate.target_node_id)
        if source is None or target is None:
            rejected.append(candidate.candidate_id)
            errors.append("关系候选引用未知节点")
            continue
        if source.graph_id != target.graph_id:
            rejected.append(candidate.candidate_id)
            errors.append("关系候选跨图引用节点")
            continue
        if not set(candidate.evidence_ids).issubset(evidence_ids):
            rejected.append(candidate.candidate_id)
            errors.append("关系候选引用未知证据")
            continue
        source_evidence = set(source.properties.get("evidence_ids", []))
        target_evidence = set(target.properties.get("evidence_ids", []))
        cited_evidence = set(candidate.evidence_ids)
        if not (cited_evidence & source_evidence) or not (cited_evidence & target_evidence):
            rejected.append(candidate.candidate_id)
            errors.append("关系候选必须同时引用两个端点的原文证据")
            continue
        if not relation_endpoints_allowed(
            candidate.relation_type, source.node_type, target.node_type,
        ):
            rejected.append(candidate.candidate_id)
            errors.append("关系候选端点类型不合法")
            continue
        if candidate.confidence < 0.8:
            rejected.append(candidate.candidate_id)
            errors.append("关系候选置信度未达到门禁")
            continue
        valid.append(candidate)
    return valid, rejected, errors


async def resolve_relationship_candidates(
    graph_id: str,
    nodes: list[GraphNode],
    evidence: list[EvidenceRecord],
    gateway: RelationshipGateway,
) -> RelationshipResolution:
    if not nodes or not evidence:
        return RelationshipResolution(errors=["关系推断缺少节点或证据"])
    context = _context(nodes, evidence)
    raw_proposals = await gateway.call_json(
        "relationship_proposal",
        system_prompt=_PROPOSAL_SYSTEM,
        user_prompt=json.dumps(context, ensure_ascii=False, default=str),
        stage="evidence_graph_relationship_proposal",
        timeout=180,
        max_tokens=12000,
    )
    try:
        proposal_envelope = RelationshipEnvelope.model_validate(raw_proposals)
    except ValidationError as exc:
        return RelationshipResolution(errors=[f"关系候选JSON校验失败: {exc.error_count()}项"])

    valid, rejected, errors = _validate_candidates(proposal_envelope, nodes, evidence)
    if not valid:
        return RelationshipResolution(rejected_candidate_ids=rejected, errors=errors)

    rebuttal_payload = {
        **context,
        "candidates": [item.model_dump(mode="json") for item in valid],
    }
    raw_rebuttal = await gateway.call_json(
        "relationship_rebuttal",
        system_prompt=_REBUTTAL_SYSTEM,
        user_prompt=json.dumps(rebuttal_payload, ensure_ascii=False, default=str),
        stage="evidence_graph_relationship_rebuttal",
        timeout=180,
        max_tokens=12000,
    )
    try:
        rebuttal = RelationshipRebuttalEnvelope.model_validate(raw_rebuttal)
    except ValidationError as exc:
        return RelationshipResolution(
            unresolved_candidate_ids=[item.candidate_id for item in valid],
            rejected_candidate_ids=rejected,
            errors=[*errors, f"关系反证JSON校验失败: {exc.error_count()}项"],
        )

    decisions = {item.candidate_id: item for item in rebuttal.decisions}
    if len(decisions) != len(rebuttal.decisions):
        errors.append("关系反证包含重复candidate_id")
    valid_ids = {item.candidate_id for item in valid}
    if set(decisions) != valid_ids:
        errors.append("关系反证未逐项覆盖候选")

    accepted_edges: list[GraphEdge] = []
    unresolved: list[str] = []
    for candidate in valid:
        decision = decisions.get(candidate.candidate_id)
        if decision is None or decision.decision == "unresolved":
            unresolved.append(candidate.candidate_id)
            continue
        if decision.decision == "rejected" or decision.confidence < 0.8:
            rejected.append(candidate.candidate_id)
            continue
        accepted_edges.append(GraphEdge(
            graph_id=graph_id,
            edge_id=f"edge-{candidate.candidate_id}",
            source_node_id=candidate.source_node_id,
            target_node_id=candidate.target_node_id,
            relation_type=candidate.relation_type,
            status=RelationStatus.ACCEPTED,
            origin=RelationOrigin.MACHINE_INFERRED,
            confidence=min(candidate.confidence, decision.confidence),
            evidence_ids=candidate.evidence_ids,
            rationale=f"候选：{candidate.rationale}；反证：{decision.rationale}",
            metadata={"candidate_id": candidate.candidate_id},
        ))

    logger.info(
        "evidence_graph_relationship_resolution",
        graph_id=graph_id,
        proposed_count=len(proposal_envelope.candidates),
        gated_count=len(valid),
        accepted_count=len(accepted_edges),
        unresolved_count=len(unresolved),
        rejected_count=len(set(rejected)),
        error_count=len(errors),
    )
    return RelationshipResolution(
        accepted_edges=accepted_edges,
        unresolved_candidate_ids=unresolved,
        rejected_candidate_ids=list(dict.fromkeys(rejected)),
        errors=errors,
    )
