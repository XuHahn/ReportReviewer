"""Authoritative contracts for the unified evidence graph review engine.

The graph keeps definitions, run-time observations, claims, relationships and
review findings separate.  LLM output can only enter the graph as a proposed
or evidence-gated relationship; final review states are produced by the
deterministic graph engine.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class GraphScope(StrEnum):
    DEFINITION = "definition"
    RUN = "run"


class NodeType(StrEnum):
    TEST_DEFINITION = "test_definition"
    TEST_SUBITEM_DEFINITION = "test_subitem_definition"
    TEST_REQUIREMENT = "test_requirement"
    TEST_EXECUTION = "test_execution"
    DOCUMENT_OBSERVATION = "document_observation"
    CLAIM = "claim"
    REQUIREMENT_DIMENSION = "requirement_dimension"
    COVERAGE_DIMENSION = "coverage_dimension"
    INSTRUMENT = "instrument"
    PERSON_ROLE = "person_role"
    STANDARD_CLAUSE = "standard_clause"
    STANDARD_REFERENCE = "standard_reference"
    EVIDENCE_ARTIFACT = "evidence_artifact"
    ANOMALY_OBSERVATION = "anomaly_observation"
    REVIEW_FINDING = "review_finding"
    DOCUMENT = "document"


class RelationType(StrEnum):
    PARENT_SUBITEM = "parent_subitem"
    SAME_ENTITY = "same_entity"
    CONDITION_OF = "condition_of"
    VARIANT_OF = "variant_of"
    REQUIRED_BY = "required_by"
    EXECUTED_AS = "executed_as"
    REPORTED_AS = "reported_as"
    HAS_CLAIM = "has_claim"
    HAS_RESULT = "has_result"
    COVERS = "covers"
    USES_INSTRUMENT = "uses_instrument"
    SUPPORTED_BY = "supported_by"
    GOVERNED_BY = "governed_by"
    CONTRADICTS = "contradicts"
    SUMMARIZES = "summarizes"
    SIGNED_AS = "signed_as"
    OBSERVED_DURING = "observed_during"


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "not_applicable"


class RelationStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


class RelationOrigin(StrEnum):
    DETERMINISTIC = "deterministic"
    MACHINE_INFERRED = "machine_inferred"
    HUMAN_CONFIRMED = "human_confirmed"
    PUBLIC_DEFINITION = "public_definition"


class FindingStatus(StrEnum):
    CONFIRMED_PASS = "confirmed_pass"
    CONFIRMED_ERROR = "confirmed_error"
    UNRESOLVED = "unresolved"
    CONFIRMED_ADVISORY = "confirmed_advisory"
    UNRESOLVED_ADVISORY = "unresolved_advisory"
    NOT_APPLICABLE = "not_applicable"


class FindingSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class EvidenceState(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    UNPARSED = "unparsed"
    CONFLICTING = "conflicting"


class EvidenceRecord(BaseModel):
    evidence_id: str = Field(min_length=1)
    graph_id: str = Field(min_length=1)
    doc_id: str = ""
    doc_type: str = ""
    filename: str = ""
    state: EvidenceState = EvidenceState.FOUND
    page_number: int = Field(default=0, ge=0)
    sheet_name: str = ""
    cell_range: str = ""
    table_id: str = ""
    bbox: list[float] = Field(default_factory=list)
    exact_quote: str = ""
    content_hash: str = ""
    extraction_method: str = ""
    model_version: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_locator(self):
        if self.state == EvidenceState.FOUND and not self.exact_quote.strip():
            raise ValueError("found evidence must include an exact quote")
        if self.bbox and len(self.bbox) != 4:
            raise ValueError("bbox must contain [x1, y1, x2, y2]")
        return self


class GraphNode(BaseModel):
    node_id: str = Field(min_length=1)
    graph_id: str = Field(min_length=1)
    node_type: NodeType
    label: str = Field(min_length=1)
    canonical_key: str = ""
    resolution_status: ResolutionStatus = ResolutionStatus.UNRESOLVED
    properties: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "system"

    @model_validator(mode="after")
    def validate_claim_payload(self):
        if self.node_type == NodeType.CLAIM:
            required = {"claim_type", "raw_value", "source_doc_type"}
            missing = sorted(required - set(self.properties))
            if missing:
                raise ValueError(f"claim node missing properties: {', '.join(missing)}")
        return self


class GraphEdge(BaseModel):
    edge_id: str = Field(min_length=1)
    graph_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    relation_type: RelationType
    status: RelationStatus = RelationStatus.PROPOSED
    origin: RelationOrigin = RelationOrigin.DETERMINISTIC
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence_gate(self):
        if self.source_node_id == self.target_node_id:
            raise ValueError("graph edge cannot point to the same node")
        if self.status == RelationStatus.ACCEPTED and not self.evidence_ids:
            raise ValueError("accepted relation must reference evidence")
        if (
            self.status == RelationStatus.ACCEPTED
            and self.origin == RelationOrigin.MACHINE_INFERRED
            and self.confidence < 0.8
        ):
            raise ValueError("accepted machine relation requires confidence >= 0.8")
        return self


class ReviewFinding(BaseModel):
    finding_id: str = Field(min_length=1)
    graph_id: str = Field(min_length=1)
    check_id: str = Field(min_length=1)
    status: FindingStatus
    severity: FindingSeverity = FindingSeverity.INFO
    title: str = Field(min_length=1)
    description: str = ""
    subject_node_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    dedupe_key: str = Field(min_length=1)
    rule_version: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_conclusion_gate(self):
        if self.status in {
            FindingStatus.CONFIRMED_ERROR,
            FindingStatus.CONFIRMED_PASS,
            FindingStatus.CONFIRMED_ADVISORY,
        }:
            if not self.subject_node_ids:
                raise ValueError("confirmed finding must reference a subject node")
            if not self.evidence_ids:
                raise ValueError("confirmed finding must reference evidence")
        if self.status == FindingStatus.CONFIRMED_ERROR:
            self.severity = FindingSeverity.ERROR
        return self


class DecisionEvent(BaseModel):
    event_id: str = Field(min_length=1)
    graph_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    actor_type: str = "system"
    actor_id: str = ""
    subject_type: str = ""
    subject_id: str = ""
    old_state: str = ""
    new_state: str = ""
    reason_code: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class FindingDecision(BaseModel):
    graph_id: str = Field(min_length=1)
    finding_id: str = Field(min_length=1)
    decision: str = Field(pattern="^(confirmed|dismissed|advisory|unresolved)$")
    resolution_code: str = Field(
        default="",
        pattern="^(|report_revision|raw_record_supplement|source_correction|not_applicable|false_positive|deferred|other)$",
    )
    resolution_status: str = Field(
        default="",
        pattern="^(|open|not_applicable|dismissed|deferred|resolved)$",
    )
    comment: str = ""
    actor_id: str = Field(min_length=1)
    created_at: str = ""


class GraphRun(BaseModel):
    graph_id: str = Field(min_length=1)
    scope: GraphScope
    set_id: str = ""
    status: str = "created"
    graph_version: str = "evidence-graph-1"
    rule_version: str = "review-rules-1"
    standard_release_ids: list[str] = Field(default_factory=list)
    model_manifest: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scope(self):
        if self.scope == GraphScope.RUN and not self.set_id.strip():
            raise ValueError("run graph requires set_id")
        if self.scope == GraphScope.DEFINITION and self.set_id:
            raise ValueError("definition graph cannot contain set_id")
        return self


class GraphSnapshot(BaseModel):
    run: GraphRun
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    findings: list[ReviewFinding] = Field(default_factory=list)
    events: list[DecisionEvent] = Field(default_factory=list)
    decisions: list[FindingDecision] = Field(default_factory=list)
