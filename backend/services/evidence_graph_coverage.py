"""Evidence-gated test-item identity and coverage checks.

This is the first vertical slice of the unified graph engine.  It deliberately
does not use fuzzy string similarity: exact identities, explicit item codes and
unique parent/subitem context are accepted; ambiguous candidates remain
unresolved for the semantic resolver.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field

from services.evidence_graph_models import (
    FindingSeverity,
    FindingStatus,
    ReviewFinding,
)
from services.test_item_aliases import alias_key_for


_SPACE_PUNCT_RE = re.compile(r"[\s\-_/·:：()（）\[\]【】]+")
_ITEM_CODE_RE = re.compile(r"(?i)(?:eq\s*[/_-]?\s*)?([a-z]{1,4})\s*[/_-]?\s*(\d{1,3}[a-z]?)")
_PULSE_RE = re.compile(r"(?i)(?:脉冲|pulse|\bp)\s*[-_]?\s*(\d{1,2}[a-z]?)")
_P_CODE_RE = re.compile(r"(?i)(?:^|[^a-z0-9])p\s*[-_]?\s*(\d{1,2}[a-z]?)(?=$|[^a-z0-9])")
_GENERIC_PULSE_RE = re.compile(r"(?i)^\s*(?:脉冲|pulse|p)\s*[-_]?\s*\d{1,2}[a-z]?\s*$")

_COVERAGE_DOC_LABELS = {
    "original_records": "原始记录",
    "final_report": "检测报告",
}


class ObservedTestItem(BaseModel):
    item_id: str = Field(min_length=1)
    doc_type: Literal["test_plan", "original_records", "final_report"]
    name: str = Field(min_length=1)
    parent_name: str = ""
    sample_id: str = ""
    mode: str = ""
    execution_round: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    result: str = ""
    evidence_ids: list[str] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CoverageProof(BaseModel):
    doc_type: Literal["original_records", "final_report"]
    extraction_complete: bool = False
    identity_variants_searched: bool = False
    parent_subitems_searched: bool = False
    targeted_visual_recovery_completed: bool = False
    unparsed_units: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)

    @property
    def conclusive(self) -> bool:
        return (
            self.extraction_complete
            and self.identity_variants_searched
            and self.parent_subitems_searched
            and self.targeted_visual_recovery_completed
            and not self.unparsed_units
            and bool(self.evidence_ids)
        )


class ParsedIdentity(BaseModel):
    normalized_name: str
    alias_key: str = ""
    item_code: str = ""
    subitem_code: str = ""
    family_key: str = ""
    generic_subitem_label: bool = False


class IdentityMatch(BaseModel):
    requirement_id: str
    observation_id: str = ""
    status: Literal["matched", "unresolved", "missing"]
    method: str = ""
    rationale: str = ""
    candidate_ids: list[str] = Field(default_factory=list)


def _coverage_missing_title(requirement_name: str, missing_docs: list[str]) -> str:
    """Make the missing side visible in the queue title."""
    labels = [_COVERAGE_DOC_LABELS.get(doc, doc) for doc in missing_docs]
    if set(missing_docs) == {"original_records", "final_report"}:
        return f"{requirement_name}未找到执行记录和报告结果"
    if missing_docs == ["original_records"]:
        return f"{requirement_name}未找到原始记录执行证据"
    if missing_docs == ["final_report"]:
        return f"{requirement_name}未找到检测报告发布证据"
    return f"{requirement_name}缺少{'、'.join(labels)}对应证据"


def _coverage_missing_description(
    requirement_name: str,
    missing_docs: list[str],
) -> str:
    """Explain the concrete gap and its consequence in reviewer language."""
    missing_labels = [_COVERAGE_DOC_LABELS.get(doc, doc) for doc in missing_docs]
    # Only the two execution/publication sources participate in this
    # sentence.  The委托单/测试计划 may be present as scope evidence, but
    # they are not proof that execution or publication occurred.
    found_labels = [
        _COVERAGE_DOC_LABELS[doc]
        for doc in ("original_records", "final_report")
        if doc not in missing_docs
    ]
    found_text = "、".join(found_labels)
    missing_text = "和".join(missing_labels) if len(missing_labels) == 2 else "、".join(missing_labels)
    if len(missing_docs) == 2:
        return (
            f"测试计划已列出“{requirement_name}”，但在{missing_text}中都未找到可追溯的对应证据；"
            "因此无法证明该项目已经按计划执行并在报告中发布结果。"
        )
    if missing_docs == ["original_records"]:
        return (
            f"测试计划已列出“{requirement_name}”，检测报告已找到对应发布结果，"
            "但原始记录中没有找到逐项执行记录；因此无法追溯试验是如何完成的。"
        )
    if missing_docs == ["final_report"]:
        return (
            f"测试计划已列出“{requirement_name}”，{found_text}已找到对应执行记录，"
            "但检测报告中没有找到可追溯的发布结果；因此无法证明该项目已完成发布。"
        )
    return (
        f"测试计划已列出“{requirement_name}”，但{missing_text}未找到对应证据；"
        "因此当前证据链尚未闭合。"
    )


def _coverage_unresolved_description(
    requirement_name: str,
    matched: dict[str, IdentityMatch],
) -> str:
    """Explain why a candidate is not yet enough to call the item missing."""
    uncertain = [
        _COVERAGE_DOC_LABELS.get(doc, doc)
        for doc, match in matched.items()
        if match.status == "unresolved"
    ]
    absent = [
        _COVERAGE_DOC_LABELS.get(doc, doc)
        for doc, match in matched.items()
        if match.status == "missing"
    ]
    uncertain_text = "、".join(uncertain)
    absent_text = "、".join(absent)
    parts: list[str] = [f"测试计划已列出“{requirement_name}”。"]
    if uncertain:
        parts.append(f"{uncertain_text}中发现了候选记录，但身份关系仍不唯一")
    if absent:
        parts.append(f"{absent_text}暂未找到可确认的对应证据")
    parts.append("因此当前只能标记为待确认，不得判定为未执行或未发布。")
    return "；".join(parts)


def _compact(value: str) -> str:
    return _SPACE_PUNCT_RE.sub("", (value or "").strip().casefold())


def parse_test_identity(name: str, parent_name: str = "") -> ParsedIdentity:
    normalized = _compact(name)
    item_code = ""
    subitem_code = ""

    code_match = _ITEM_CODE_RE.search(name)
    if code_match:
        item_code = f"{code_match.group(1).upper()}{code_match.group(2).upper()}"

    pulse_match = _PULSE_RE.search(name)
    if pulse_match:
        subitem_code = f"P{pulse_match.group(1).upper()}"
    else:
        p_match = _P_CODE_RE.search(name)
        if p_match:
            subitem_code = f"P{p_match.group(1).upper()}"

    family_text = parent_name.strip()
    if not family_text and subitem_code:
        family_text = _PULSE_RE.sub("", name)
        family_text = re.sub(r"(?i)p\s*[-_]?\s*\d{1,2}[a-z]?\s*$", "", family_text)
    family_key = _compact(family_text)

    return ParsedIdentity(
        normalized_name=normalized,
        alias_key=alias_key_for(name),
        item_code=item_code,
        subitem_code=subitem_code,
        family_key=family_key,
        generic_subitem_label=bool(_GENERIC_PULSE_RE.fullmatch(name.strip())),
    )


def _compatible_execution_context(requirement: ObservedTestItem,
                                  observation: ObservedTestItem) -> bool:
    for field in ("sample_id", "execution_round"):
        required = _compact(getattr(requirement, field))
        observed = _compact(getattr(observation, field))
        if required and observed and required != observed:
            return False

    required_mode = _compact(requirement.mode)
    observed_mode = _compact(observation.mode)
    if required_mode and observed_mode:
        # A plan often describes the operating mode in business language
        # (for example, "正反转模式/带风叶负载"), while a report base item
        # aggregates the concrete execution modes ("Mode 1, Mode 2").
        # These are not contradictory contexts when neither side identifies a
        # particular sample or execution round.  Mode equality remains a gate
        # for row-level observations that carry an actual execution context.
        has_execution_context = bool(
            _compact(requirement.sample_id)
            or _compact(requirement.execution_round)
            or _compact(observation.sample_id)
            or _compact(observation.execution_round)
        )
        if has_execution_context and required_mode != observed_mode:
            return False
    return True


def _same_base_identity(left: str, right: str) -> bool:
    """Compare two explicit base identities without fuzzy similarity."""
    left_identity = parse_test_identity(left)
    right_identity = parse_test_identity(right)
    if left_identity.normalized_name == right_identity.normalized_name:
        return True
    if left_identity.alias_key and left_identity.alias_key == right_identity.alias_key:
        return True
    return bool(
        left_identity.item_code
        and left_identity.item_code == right_identity.item_code
    )


def match_test_items(requirements: list[ObservedTestItem],
                     observations: list[ObservedTestItem]) -> dict[str, IdentityMatch]:
    parsed_requirements = {
        item.item_id: parse_test_identity(item.name, item.parent_name)
        for item in requirements
    }
    parsed_observations = {
        item.item_id: parse_test_identity(item.name, item.parent_name)
        for item in observations
    }
    matches: dict[str, IdentityMatch] = {}

    for requirement in requirements:
        req_identity = parsed_requirements[requirement.item_id]
        ranked: dict[int, list[tuple[ObservedTestItem, str]]] = defaultdict(list)
        for observation in observations:
            if not _compatible_execution_context(requirement, observation):
                continue
            obs_identity = parsed_observations[observation.item_id]
            if req_identity.normalized_name == obs_identity.normalized_name:
                ranked[100].append((observation, "exact_name"))
                continue
            if req_identity.alias_key and req_identity.alias_key == obs_identity.alias_key:
                ranked[95].append((observation, "confirmed_alias"))
                continue
            if req_identity.item_code and req_identity.item_code == obs_identity.item_code:
                ranked[90].append((observation, "explicit_item_code"))
                continue
            if req_identity.subitem_code and req_identity.subitem_code == obs_identity.subitem_code:
                if req_identity.family_key and req_identity.family_key == obs_identity.family_key:
                    ranked[85].append((observation, "parent_subitem"))
                elif obs_identity.generic_subitem_label:
                    ranked[70].append((observation, "unique_subitem_context"))

        if not ranked:
            matches[requirement.item_id] = IdentityMatch(
                requirement_id=requirement.item_id,
                status="missing",
                rationale="未发现确定性身份候选",
            )
            continue

        best_rank = max(ranked)
        best = ranked[best_rank]
        if len(best) == 1:
            observation, method = best[0]
            # A generic label such as "脉冲2a" is only safe when this document
            # has exactly one compatible requirement for that subitem token.
            if method == "unique_subitem_context":
                same_subitem_requirements = [
                    item for item in requirements
                    if parsed_requirements[item.item_id].subitem_code == req_identity.subitem_code
                    and _compatible_execution_context(item, observation)
                ]
                if len(same_subitem_requirements) != 1:
                    matches[requirement.item_id] = IdentityMatch(
                        requirement_id=requirement.item_id,
                        status="unresolved",
                        method=method,
                        rationale="通用子项名称对应多个测试族，必须语义复核",
                        candidate_ids=[observation.item_id],
                    )
                    continue
            matches[requirement.item_id] = IdentityMatch(
                requirement_id=requirement.item_id,
                observation_id=observation.item_id,
                status="matched",
                method=method,
                rationale="确定性身份门禁通过",
                candidate_ids=[observation.item_id],
            )
            continue

        matches[requirement.item_id] = IdentityMatch(
            requirement_id=requirement.item_id,
            status="unresolved",
            rationale="存在多个同等级身份候选",
            candidate_ids=[item.item_id for item, _ in best],
        )

    # Identity coverage is one-to-one.  A single raw-record/report identity
    # cannot prove two independent plan requirements, even when both names are
    # identical.  Keep the conflict visible for semantic/manual resolution.
    requirement_ids_by_observation: dict[str, list[str]] = defaultdict(list)
    for requirement_id, match in matches.items():
        if match.status == "matched" and match.observation_id:
            requirement_ids_by_observation[match.observation_id].append(requirement_id)
    for observation_id, requirement_ids in requirement_ids_by_observation.items():
        if len(requirement_ids) == 1:
            continue
        for requirement_id in requirement_ids:
            matches[requirement_id] = IdentityMatch(
                requirement_id=requirement_id,
                status="unresolved",
                rationale="同一文档项目命中多个独立要求，禁止重复覆盖",
                candidate_ids=[observation_id],
            )

    return matches


def _finding_id(graph_id: str, dedupe_key: str) -> str:
    digest = hashlib.sha256(f"{graph_id}:{dedupe_key}".encode()).hexdigest()[:20]
    return f"finding-{digest}"


def evaluate_test_item_coverage(
    graph_id: str,
    requirements: list[ObservedTestItem],
    raw_items: list[ObservedTestItem],
    report_items: list[ObservedTestItem],
    coverage_proofs: list[CoverageProof],
    identity_overrides: dict[str, dict[str, str]] | None = None,
    *,
    plan_extraction_complete: bool = False,
) -> tuple[list[ReviewFinding], dict[str, dict[str, IdentityMatch]]]:
    """Evaluate plan scope, execution and publication without treating absence as proof."""
    raw_matches = match_test_items(requirements, raw_items)
    report_matches = match_test_items(requirements, report_items)
    identity_overrides = identity_overrides or {}
    observations_by_doc = {
        "original_records": {item.item_id: item for item in raw_items},
        "final_report": {item.item_id: item for item in report_items},
    }
    for doc_type, matches in (
        ("original_records", raw_matches),
        ("final_report", report_matches),
    ):
        proposed_overrides = identity_overrides.get(doc_type, {})
        override_requirements_by_observation: dict[str, list[str]] = defaultdict(list)
        for requirement_id, observation_id in proposed_overrides.items():
            override_requirements_by_observation[observation_id].append(requirement_id)
        deterministically_consumed = {
            match.observation_id: requirement_id
            for requirement_id, match in matches.items()
            if match.status == "matched" and match.observation_id
        }
        for requirement_id, observation_id in proposed_overrides.items():
            # Apply only a globally unique semantic proposal.  This closes the
            # same reuse hole for LLM-confirmed identities as for exact ones.
            if len(override_requirements_by_observation[observation_id]) != 1:
                continue
            owner = deterministically_consumed.get(observation_id)
            if owner is not None and owner != requirement_id:
                continue
            requirement = next(
                (item for item in requirements if item.item_id == requirement_id), None,
            )
            observation = observations_by_doc[doc_type].get(observation_id)
            if requirement is None or observation is None:
                continue
            if not _compatible_execution_context(requirement, observation):
                continue
            matches[requirement_id] = IdentityMatch(
                requirement_id=requirement_id,
                observation_id=observation_id,
                status="matched",
                method="llm_evidence_rebuttal",
                rationale="语义关系候选经独立反证与原文证据门禁确认",
                candidate_ids=[observation_id],
            )
    items_by_id = {
        item.item_id: item
        for item in [*requirements, *raw_items, *report_items]
    }
    proofs = {proof.doc_type: proof for proof in coverage_proofs}
    findings: list[ReviewFinding] = []

    for requirement in requirements:
        raw_match = raw_matches[requirement.item_id]
        report_match = report_matches[requirement.item_id]
        matched = {
            "original_records": raw_match,
            "final_report": report_match,
        }
        missing_docs = [doc for doc, match in matched.items() if match.status != "matched"]
        evidence_ids = list(dict.fromkeys(requirement.evidence_ids))
        for match in matched.values():
            if match.status == "matched":
                evidence_ids.extend(items_by_id[match.observation_id].evidence_ids)
        evidence_ids = list(dict.fromkeys(evidence_ids))
        dedupe_key = f"GRAPH-COVERAGE-001:{requirement.item_id}"

        if not missing_docs:
            status = FindingStatus.CONFIRMED_PASS
            severity = FindingSeverity.INFO
            title = f"{requirement.name}执行与发布证据完整"
            description = "测试计划、原始记录和检测报告均存在可追溯的对应证据。"
        else:
            conclusive_missing = all(
                matched[doc].status == "missing"
                and proofs.get(doc) is not None
                and proofs[doc].conclusive
                for doc in missing_docs
            )
            if conclusive_missing:
                status = FindingStatus.CONFIRMED_ERROR
                severity = FindingSeverity.ERROR
                for doc in missing_docs:
                    evidence_ids.extend(proofs[doc].evidence_ids)
                evidence_ids = list(dict.fromkeys(evidence_ids))
                title = _coverage_missing_title(requirement.name, missing_docs)
                description = _coverage_missing_description(requirement.name, missing_docs)
            else:
                status = FindingStatus.UNRESOLVED
                severity = FindingSeverity.WARNING
                title = f"{requirement.name}证据尚未闭合"
                description = _coverage_unresolved_description(requirement.name, matched)

        findings.append(ReviewFinding(
            graph_id=graph_id,
            finding_id=_finding_id(graph_id, dedupe_key),
            check_id="GRAPH-COVERAGE-001",
            status=status,
            severity=severity,
            title=title,
            description=description,
            subject_node_ids=[requirement.item_id],
            evidence_ids=evidence_ids,
            dedupe_key=dedupe_key,
            rule_version="coverage-graph-1",
            metadata={
                "raw_match": raw_match.model_dump(mode="json"),
                "report_match": report_match.model_dump(mode="json"),
                "missing_docs": missing_docs,
            },
        ))

    # Reverse direction: positive raw/report identities not consumed by the
    # plan-driven pass are also material.  This detects unplanned execution and
    # unsupported publication instead of silently dropping document extras.
    used_raw_ids = {
        match.observation_id for match in raw_matches.values()
        if match.status == "matched" and match.observation_id
    }
    used_report_ids = {
        match.observation_id for match in report_matches.values()
        if match.status == "matched" and match.observation_id
    }
    uncertain_raw_ids = {
        candidate_id for match in raw_matches.values()
        if match.status == "unresolved" for candidate_id in match.candidate_ids
    }
    uncertain_report_ids = {
        candidate_id for match in report_matches.values()
        if match.status == "unresolved" for candidate_id in match.candidate_ids
    }

    # Some sources publish both an aggregate heading and the individual rows
    # that the plan explicitly names (for example, one transient-immunity
    # heading followed by P1/P2a/P2b/P3a). The heading is not a fifth test
    # item. Suppress it only under a strict, evidence-preserving gate: every
    # child row in raw records was consumed by the plan, the same child
    # identities were consumed in the report, and the aggregate base identity
    # itself is present in both result documents.
    aggregate_consumed_ids: set[str] = set()
    raw_details = [
        item for item in raw_items
        if item.metadata.get("identity_role") == "execution_detail"
    ]
    report_details = [
        item for item in report_items
        if item.metadata.get("identity_role") == "execution_detail"
    ]
    unused_report_bases = [
        item for item in report_items
        if item.item_id not in used_report_ids
        and item.metadata.get("identity_role") != "execution_detail"
    ]
    for raw_base in raw_items:
        if (
            raw_base.item_id in used_raw_ids
            or raw_base.metadata.get("identity_role") == "execution_detail"
        ):
            continue
        child_rows = [
            item for item in raw_details
            if item.parent_name and _same_base_identity(raw_base.name, item.parent_name)
        ]
        if not child_rows or any(item.item_id not in used_raw_ids for item in child_rows):
            continue
        paired_base_matches = match_test_items([raw_base], unused_report_bases)
        paired_base_match = paired_base_matches[raw_base.item_id]
        if paired_base_match.status != "matched":
            continue
        child_report_matches = match_test_items(child_rows, report_details)
        if any(
            match.status != "matched"
            or match.observation_id not in used_report_ids
            for match in child_report_matches.values()
        ):
            continue
        aggregate_consumed_ids.add(raw_base.item_id)
        aggregate_consumed_ids.add(paired_base_match.observation_id)

    def is_reverse_scope_item(item: ObservedTestItem) -> bool:
        # Reverse coverage compares the plan with the source document's base
        # test-item universe. Pulse/mode/sample rows are execution details,
        # not additional test items, so an unconsumed detail must not become a
        # false "plan-extra" issue. They remain available to forward matching
        # when the plan itself explicitly names that detail as a test item.
        return item.metadata.get("identity_role") != "execution_detail"

    extra_raw = [
        item for item in raw_items
        if item.item_id not in used_raw_ids
        and item.item_id not in aggregate_consumed_ids
        and is_reverse_scope_item(item)
    ]
    extra_report = [
        item for item in report_items
        if item.item_id not in used_report_ids
        and item.item_id not in aggregate_consumed_ids
        and is_reverse_scope_item(item)
    ]
    extra_report_by_id = {item.item_id: item for item in extra_report}
    paired_report_ids: set[str] = set()
    reverse_matches = match_test_items(extra_raw, extra_report)
    plan_evidence_ids = list(dict.fromkeys(
        evidence_id for item in requirements for evidence_id in item.evidence_ids
    ))

    def add_reverse_finding(
        *,
        raw_item: ObservedTestItem | None,
        report_candidates: list[ObservedTestItem],
        ambiguous: bool = False,
    ) -> None:
        subject_items = [item for item in [raw_item, *report_candidates] if item is not None]
        subject_ids = [item.item_id for item in subject_items]
        evidence_ids = list(dict.fromkeys([
            *plan_evidence_ids,
            *(evidence_id for item in subject_items for evidence_id in item.evidence_ids),
        ]))
        identity_label = raw_item.name if raw_item is not None else report_candidates[0].name
        dedupe_identity = ":".join(sorted(subject_ids))
        dedupe_key = f"GRAPH-COVERAGE-002:{dedupe_identity}"
        if ambiguous:
            status = FindingStatus.UNRESOLVED
            severity = FindingSeverity.WARNING
            title = f"{identity_label}的执行与发布身份关系不唯一"
            description = "原始记录与检测报告存在多个身份候选，需完成语义或人工复核。"
        elif not plan_extraction_complete:
            status = FindingStatus.UNRESOLVED
            severity = FindingSeverity.WARNING
            title = f"{identity_label}尚未闭合到测试计划"
            description = "发现执行或发布证据，但计划范围尚未完整，不能判定为计划外项目。"
        else:
            status = FindingStatus.CONFIRMED_ERROR
            severity = FindingSeverity.ERROR
            title = f"{identity_label}不在测试计划要求范围内"
            if raw_item is not None and report_candidates:
                description = "原始记录和检测报告均存在该项目，但完整测试计划中没有对应要求。"
            elif raw_item is not None:
                description = "原始记录存在该执行项目，但完整测试计划中没有对应要求。"
            else:
                description = "检测报告发布了该项目，但完整测试计划中没有对应要求。"
        findings.append(ReviewFinding(
            graph_id=graph_id,
            finding_id=_finding_id(graph_id, dedupe_key),
            check_id="GRAPH-COVERAGE-002",
            status=status,
            severity=severity,
            title=title,
            description=description,
            subject_node_ids=subject_ids,
            evidence_ids=evidence_ids,
            dedupe_key=dedupe_key,
            rule_version="coverage-graph-2",
            metadata={
                "plan_extraction_complete": plan_extraction_complete,
                "raw_item_id": raw_item.item_id if raw_item is not None else "",
                "report_item_ids": [item.item_id for item in report_candidates],
                "ambiguous_identity": ambiguous,
            },
        ))

    for raw_item in extra_raw:
        match = reverse_matches[raw_item.item_id]
        if match.status == "matched":
            report_item = extra_report_by_id[match.observation_id]
            paired_report_ids.add(report_item.item_id)
            add_reverse_finding(
                raw_item=raw_item,
                report_candidates=[report_item],
                ambiguous=(
                    raw_item.item_id in uncertain_raw_ids
                    or report_item.item_id in uncertain_report_ids
                ),
            )
        elif match.status == "unresolved":
            candidates = [
                extra_report_by_id[item_id] for item_id in match.candidate_ids
                if item_id in extra_report_by_id
            ]
            paired_report_ids.update(item.item_id for item in candidates)
            add_reverse_finding(
                raw_item=raw_item, report_candidates=candidates, ambiguous=True,
            )
        else:
            add_reverse_finding(
                raw_item=raw_item,
                report_candidates=[],
                ambiguous=raw_item.item_id in uncertain_raw_ids,
            )

    for report_item in extra_report:
        if report_item.item_id not in paired_report_ids:
            add_reverse_finding(
                raw_item=None,
                report_candidates=[report_item],
                ambiguous=report_item.item_id in uncertain_report_ids,
            )

    return findings, {
        requirement.item_id: {
            "original_records": raw_matches[requirement.item_id],
            "final_report": report_matches[requirement.item_id],
        }
        for requirement in requirements
    }
