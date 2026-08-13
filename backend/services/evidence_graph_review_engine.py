"""First end-to-end slice of the unified evidence graph review engine."""

from __future__ import annotations

import hashlib

from services.evidence_graph_coverage import (
    CoverageProof,
    IdentityMatch,
    ObservedTestItem,
    evaluate_test_item_coverage,
)
from services.evidence_graph_models import (
    DecisionEvent,
    EvidenceRecord,
    GraphEdge,
    GraphNode,
    GraphRun,
    GraphScope,
    NodeType,
    RelationOrigin,
    RelationStatus,
    RelationType,
    ResolutionStatus,
)
from services.evidence_graph_store import EvidenceGraphStore
from services.evidence_graph_extraction import UnifiedExtractionResult
from services.evidence_graph_check_adapter import build_claim_nodes_findings_and_audit
from services.evidence_graph_standard_advisory import (
    build_standard_acceptance_consistency,
    build_standard_parameter_consistency,
    build_standard_plan_coverage,
    build_standard_provenance,
    build_standard_scope_advisories,
)
from services.evidence_graph_document_checks import (
    build_reviewed_document_checks_and_audit,
)
from services.evidence_graph_check_execution import (
    build_check_execution,
    has_blocking_check_state,
)
from utils.logger import get_logger


logger = get_logger(__name__)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _node_type(item: ObservedTestItem) -> NodeType:
    if item.doc_type == "test_plan":
        return NodeType.TEST_REQUIREMENT
    if item.doc_type == "original_records":
        return NodeType.TEST_EXECUTION
    return NodeType.DOCUMENT_OBSERVATION


class EvidenceGraphReviewEngine:
    def __init__(self, store: EvidenceGraphStore):
        self.store = store

    def create_run(self, graph_id: str, set_id: str, *,
                   standard_release_ids: list[str] | None = None,
                   model_manifest: dict | None = None) -> GraphRun:
        run = GraphRun(
            graph_id=graph_id,
            scope=GraphScope.RUN,
            set_id=set_id,
            status="building",
            standard_release_ids=standard_release_ids or [],
            model_manifest=model_manifest or {},
        )
        self.store.create_run(run)
        self.store.add_event(DecisionEvent(
            event_id=_stable_id("event", graph_id, "run_created"),
            graph_id=graph_id,
            event_type="run_created",
            subject_type="graph_run",
            subject_id=graph_id,
            new_state="building",
            reason_code="review_requested",
        ))
        return run

    def _persist_check_execution(
        self,
        graph_id: str,
        metadata_key: str,
        items: list[dict],
    ) -> None:
        self.store.update_run_metadata(graph_id, {metadata_key: items})
        for item in items:
            logger.info(
                "rule_input_audit",
                graph_id=graph_id,
                check_id=item.get("check_id"),
                state=item.get("state"),
                reason_code=item.get("reason_code"),
                applicability=item.get("applicability"),
                expected_input_count=item.get("expected_input_count"),
                anchored_input_count=item.get("anchored_input_count"),
                finding_count=item.get("finding_count"),
                document_types=item.get("document_types"),
            )
        if has_blocking_check_state(items):
            self.store.update_run_status(graph_id, "machine_incomplete")

    def persist_general_observations(
        self,
        graph_id: str,
        results: list[UnifiedExtractionResult],
    ) -> None:
        for result in results:
            for item in result.evidence:
                self.store.add_evidence(item)
        nodes, findings, check_execution = build_claim_nodes_findings_and_audit(
            graph_id, results,
        )
        for node in nodes:
            self.store.add_node(node)
        for finding in findings:
            self.store.add_finding(finding)
            self.store.add_event(DecisionEvent(
                event_id=_stable_id("event", graph_id, finding.finding_id, "general_check"),
                graph_id=graph_id,
                event_type="finding_created",
                subject_type="review_finding",
                subject_id=finding.finding_id,
                new_state=str(finding.status),
                reason_code=finding.check_id,
                evidence_ids=finding.evidence_ids,
                payload={"severity": str(finding.severity)},
            ))
        logger.info(
            "evidence_graph_general_observations_persisted",
            graph_id=graph_id,
            claim_node_count=len(nodes),
            finding_count=len(findings),
        )
        self._persist_check_execution(
            graph_id, "general_check_execution", check_execution,
        )

    def persist_reviewed_document_checks(
        self,
        graph_id: str,
        documents: dict[str, dict],
        units: list,
        metadata_by_doc_type: dict[str, list[dict]],
    ) -> None:
        evidence, nodes, findings, check_execution = build_reviewed_document_checks_and_audit(
            graph_id, documents, units, metadata_by_doc_type,
        )
        for item in evidence:
            self.store.add_evidence(item)
        for node in nodes:
            self.store.add_node(node)
        for finding in findings:
            self.store.add_finding(finding)
            self.store.add_event(DecisionEvent(
                event_id=_stable_id("event", graph_id, finding.finding_id, "document_check"),
                graph_id=graph_id,
                event_type="finding_created",
                subject_type="review_finding",
                subject_id=finding.finding_id,
                new_state=str(finding.status),
                reason_code=finding.check_id,
                evidence_ids=finding.evidence_ids,
                payload={"severity": str(finding.severity)},
            ))
        logger.info(
            "evidence_graph_reviewed_document_checks_persisted",
            graph_id=graph_id,
            fact_node_count=len(nodes),
            finding_count=len(findings),
        )
        self._persist_check_execution(
            graph_id, "document_check_execution", check_execution,
        )

    def persist_standard_advisories(
        self, graph_id: str, plan_items: list[ObservedTestItem], releases: list[dict],
        *, documents: dict[str, dict] | None = None, units: list | None = None,
        metadata_by_doc_type: dict[str, list[dict]] | None = None,
    ) -> None:
        provenance_evidence, provenance_nodes, provenance_edges = build_standard_provenance(
            graph_id, plan_items, releases,
        )
        persisted_evidence_ids: set[str] = set()
        persisted_node_ids: set[str] = set()
        persisted_edge_ids: set[str] = set()
        persisted_finding_ids: set[str] = set()
        persisted_finding_dedupe_keys: set[str] = set()
        for item in provenance_evidence:
            if item.evidence_id in persisted_evidence_ids:
                continue
            self.store.add_evidence(item)
            persisted_evidence_ids.add(item.evidence_id)
        for node in provenance_nodes:
            if node.node_id in persisted_node_ids:
                continue
            self.store.add_node(node)
            persisted_node_ids.add(node.node_id)
        for edge in provenance_edges:
            if edge.edge_id in persisted_edge_ids:
                continue
            self.store.add_edge(edge)
            persisted_edge_ids.add(edge.edge_id)
        evidence, nodes, findings = build_standard_scope_advisories(
            graph_id, plan_items, releases,
        )
        coverage_findings, cited_count, covered_count = build_standard_plan_coverage(
            graph_id, plan_items, releases,
        )
        findings.extend(coverage_findings)
        acceptance_expected = 0
        acceptance_anchored = 0
        parameter_expected = 0
        parameter_anchored = 0
        if documents is not None and units is not None and metadata_by_doc_type is not None:
            (
                acceptance_evidence, acceptance_nodes, acceptance_findings,
                acceptance_expected, acceptance_anchored,
            ) = build_standard_acceptance_consistency(
                graph_id, plan_items, releases, documents, units,
                metadata_by_doc_type,
            )
            evidence.extend(acceptance_evidence)
            nodes.extend(acceptance_nodes)
            findings.extend(acceptance_findings)
            (
                parameter_evidence, parameter_nodes, parameter_findings,
                parameter_expected, parameter_anchored,
            ) = build_standard_parameter_consistency(
                graph_id, plan_items, releases, documents, units,
                metadata_by_doc_type,
            )
            evidence.extend(parameter_evidence)
            nodes.extend(parameter_nodes)
            findings.extend(parameter_findings)
        for item in evidence:
            # A published clause can be emitted once as graph provenance and
            # again as an advisory input.  Evidence IDs are graph-scoped, so
            # retain the first canonical record and make later references
            # point to the same persisted evidence instead of violating the
            # database uniqueness constraint.
            if item.evidence_id in persisted_evidence_ids:
                continue
            self.store.add_evidence(item)
            persisted_evidence_ids.add(item.evidence_id)
        for node in nodes:
            if node.node_id in persisted_node_ids:
                continue
            self.store.add_node(node)
            persisted_node_ids.add(node.node_id)
        for finding in findings:
            if (
                finding.finding_id in persisted_finding_ids
                or finding.dedupe_key in persisted_finding_dedupe_keys
            ):
                continue
            self.store.add_finding(finding)
            persisted_finding_ids.add(finding.finding_id)
            persisted_finding_dedupe_keys.add(finding.dedupe_key)
            self.store.add_event(DecisionEvent(
                event_id=_stable_id("event", graph_id, finding.finding_id, "standard_advisory"),
                graph_id=graph_id, event_type="finding_created",
                subject_type="review_finding", subject_id=finding.finding_id,
                new_state=str(finding.status), reason_code=finding.check_id,
                evidence_ids=finding.evidence_ids,
            ))
        standard_execution = [
            build_check_execution(
                "STANDARD-SCOPE-ADVISORY-001",
                len(plan_items) if releases else 0,
                sum(1 for item in findings if item.check_id == "STANDARD-SCOPE-ADVISORY-001"),
                applicability=("not_applicable" if not releases else "applicable"),
                reason_code=("no_published_standard_selected" if not releases else ""),
                document_types={"test_plan", "standard"},
            ),
            build_check_execution(
                "STANDARD-PLAN-COVERAGE-001",
                covered_count,
                len(coverage_findings),
                expected_input_count=cited_count,
                anchored_input_count=covered_count,
                forced_state=(
                    "system_incomplete" if covered_count < cited_count else None
                ),
                reason_code=(
                    "cited_standard_not_selected_or_published"
                    if covered_count < cited_count else ""
                ),
                applicability=("applicable" if cited_count else "not_applicable"),
                document_types={"test_plan", "standard"},
            ),
            build_check_execution(
                "STANDARD-ACCEPTANCE-CONSISTENCY-001",
                acceptance_anchored,
                sum(
                    1 for item in findings
                    if item.check_id == "STANDARD-ACCEPTANCE-CONSISTENCY-001"
                ),
                expected_input_count=acceptance_expected,
                anchored_input_count=acceptance_anchored,
                forced_state=(
                    "system_incomplete"
                    if acceptance_anchored < acceptance_expected else None
                ),
                reason_code=(
                    "standard_acceptance_source_not_fully_anchored"
                    if acceptance_anchored < acceptance_expected else ""
                ),
                applicability=(
                    "applicable" if acceptance_expected else "not_applicable"
                ),
                document_types={
                    "test_plan", "original_records", "final_report", "standard",
                },
            ),
            build_check_execution(
                "STANDARD-PARAMETER-CONSISTENCY-001",
                parameter_anchored,
                sum(
                    1 for item in findings
                    if item.check_id == "STANDARD-PARAMETER-CONSISTENCY-001"
                ),
                expected_input_count=parameter_expected,
                anchored_input_count=parameter_anchored,
                forced_state=(
                    "system_incomplete"
                    if parameter_anchored < parameter_expected else None
                ),
                reason_code=(
                    "standard_parameter_source_not_fully_anchored"
                    if parameter_anchored < parameter_expected else ""
                ),
                applicability=(
                    "applicable" if parameter_expected else "not_applicable"
                ),
                document_types={"final_report", "standard", "test_plan"},
            ),
        ]
        self._persist_check_execution(
            graph_id, "standard_check_execution", standard_execution,
        )

    def persist_test_item_coverage(
        self,
        graph_id: str,
        requirements: list[ObservedTestItem],
        raw_items: list[ObservedTestItem],
        report_items: list[ObservedTestItem],
        evidence: list[EvidenceRecord],
        coverage_proofs: list[CoverageProof],
        identity_overrides: dict[str, dict[str, str]] | None = None,
        *,
        plan_extraction_complete: bool = False,
    ) -> None:
        if not requirements:
            raise ValueError("test plan produced no evidence-gated test requirements")
        all_items = [*requirements, *raw_items, *report_items]
        evidence_by_id = {item.evidence_id: item for item in evidence}
        referenced_ids = {
            evidence_id for item in all_items for evidence_id in item.evidence_ids
        }
        referenced_ids.update(
            evidence_id for proof in coverage_proofs for evidence_id in proof.evidence_ids
        )
        missing_evidence = sorted(referenced_ids - set(evidence_by_id))
        if missing_evidence:
            raise ValueError(f"test item slice references unknown evidence: {', '.join(missing_evidence)}")

        for item in evidence:
            if item.graph_id != graph_id:
                raise ValueError("evidence belongs to a different graph")
            self.store.add_evidence(item)

        for item in all_items:
            self.store.add_node(GraphNode(
                node_id=item.item_id,
                graph_id=graph_id,
                node_type=_node_type(item),
                label=item.name,
                canonical_key=item.name.casefold(),
                resolution_status=ResolutionStatus.UNRESOLVED,
                properties={
                    "doc_type": item.doc_type,
                    "parent_name": item.parent_name,
                    "sample_id": item.sample_id,
                    "mode": item.mode,
                    "execution_round": item.execution_round,
                    "parameters": item.parameters,
                    "result": item.result,
                    "evidence_ids": item.evidence_ids,
                    **item.metadata,
                },
            ))

        findings, matches = evaluate_test_item_coverage(
            graph_id,
            requirements,
            raw_items,
            report_items,
            coverage_proofs,
            identity_overrides,
            plan_extraction_complete=plan_extraction_complete,
        )
        items_by_id = {item.item_id: item for item in all_items}
        for requirement in requirements:
            pair = matches[requirement.item_id]
            raw_match = pair["original_records"]
            report_match = pair["final_report"]
            if raw_match.status == "matched":
                raw_item = items_by_id[raw_match.observation_id]
                self._add_match_edge(
                    graph_id,
                    requirement,
                    raw_item,
                    raw_match,
                    RelationType.EXECUTED_AS,
                )
            if report_match.status == "matched":
                report_item = items_by_id[report_match.observation_id]
                source_item = (
                    items_by_id[raw_match.observation_id]
                    if raw_match.status == "matched" else requirement
                )
                self._add_match_edge(
                    graph_id,
                    source_item,
                    report_item,
                    report_match,
                    RelationType.REPORTED_AS,
                )

        for finding in findings:
            self.store.add_finding(finding)
            self.store.add_event(DecisionEvent(
                event_id=_stable_id("event", graph_id, finding.finding_id, "finding_created"),
                graph_id=graph_id,
                event_type="finding_created",
                subject_type="review_finding",
                subject_id=finding.finding_id,
                new_state=str(finding.status),
                reason_code=finding.check_id,
                evidence_ids=finding.evidence_ids,
                payload={
                    "severity": str(finding.severity),
                    "subject_count": len(finding.subject_node_ids),
                },
            ))

        has_unresolved = (
            not plan_extraction_complete
            or any(str(finding.status) == "unresolved" for finding in findings)
        )
        final_status = "machine_incomplete" if has_unresolved else "machine_complete"
        self.store.update_run_status(graph_id, final_status)
        issue_counts = {
            check_id: sum(
                finding.check_id == check_id
                and str(finding.status) != "confirmed_pass"
                for finding in findings
            )
            for check_id in ("GRAPH-COVERAGE-001", "GRAPH-COVERAGE-002")
        }
        self._persist_check_execution(graph_id, "coverage_check_execution", [
            build_check_execution(
                "GRAPH-COVERAGE-001", len(requirements),
                issue_counts["GRAPH-COVERAGE-001"],
                document_types={"test_plan", "original_records", "final_report"},
            ),
            build_check_execution(
                "GRAPH-COVERAGE-002", len(raw_items) + len(report_items),
                issue_counts["GRAPH-COVERAGE-002"],
                forced_state=(None if plan_extraction_complete else "system_incomplete"),
                reason_code=("" if plan_extraction_complete else "plan_scope_not_complete"),
                document_types={"test_plan", "original_records", "final_report"},
            ),
        ])
        self.store.add_event(DecisionEvent(
            event_id=_stable_id("event", graph_id, "coverage_slice_complete"),
            graph_id=graph_id,
            event_type="coverage_slice_completed",
            subject_type="graph_run",
            subject_id=graph_id,
            old_state="building",
            new_state=final_status,
            reason_code="test_item_coverage_evaluated",
            payload={
                "requirement_count": len(requirements),
                "finding_count": len(findings),
            },
        ))
        logger.info(
            "evidence_graph_coverage_slice_persisted",
            graph_id=graph_id,
            requirement_count=len(requirements),
            raw_item_count=len(raw_items),
            report_item_count=len(report_items),
            finding_count=len(findings),
            status=final_status,
        )

    def _add_match_edge(
        self,
        graph_id: str,
        source: ObservedTestItem,
        target: ObservedTestItem,
        match: IdentityMatch,
        relation_type: RelationType,
    ) -> None:
        evidence_ids = list(dict.fromkeys([*source.evidence_ids, *target.evidence_ids]))
        self.store.add_edge(GraphEdge(
            edge_id=_stable_id(
                "edge", graph_id, source.item_id, target.item_id, str(relation_type),
            ),
            graph_id=graph_id,
            source_node_id=source.item_id,
            target_node_id=target.item_id,
            relation_type=relation_type,
            status=RelationStatus.ACCEPTED,
            origin=(
                RelationOrigin.MACHINE_INFERRED
                if match.method == "llm_evidence_rebuttal"
                else RelationOrigin.DETERMINISTIC
            ),
            confidence=1.0 if match.method in {"exact_name", "explicit_item_code"} else 0.9,
            evidence_ids=evidence_ids,
            rationale=match.rationale,
            metadata={"identity_method": match.method},
        ))
