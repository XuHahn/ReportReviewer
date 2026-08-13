"""Native-first orchestration for the unified evidence graph review."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import unicodedata
import uuid
from collections import defaultdict

import database
from services.document_unitizer import locate_layout_quote, unitize_document
from services.evidence_graph_coverage import (
    CoverageProof,
    ObservedTestItem,
    match_test_items,
)
from services.evidence_graph_extraction import (
    EvidenceGraphDocumentUnit,
    UnifiedExtractionResult,
    extract_evidence_graph_unit,
    recover_targeted_evidence,
)
from services.evidence_graph_models import (
    DecisionEvent,
    EvidenceRecord,
    GraphNode,
    NodeType,
    RelationType,
)
from services.evidence_graph_relationships import resolve_relationship_candidates
from services.evidence_graph_review_engine import EvidenceGraphReviewEngine
from services.evidence_graph_structured_observations import (
    build_structured_check_results,
)
from services.evidence_graph_store import EvidenceGraphStore
from services.test_item_aliases import alias_key_for, normalize_alias_text
from services.unified_model_gateway import UnifiedModelGateway
from services.finding_llm_explanation import enrich_finding_supplements
from services.unified_extraction_cache import UnifiedExtractionCache
from utils.logger import get_logger


logger = get_logger(__name__)
_REQUIRED_DOC_TYPES = ("order_form", "test_plan", "original_records", "final_report")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _source_anchor(
    unit: EvidenceGraphDocumentUnit,
    quote: str,
    *,
    context_terms: tuple[str, ...] = (),
) -> tuple[list[float], dict]:
    bbox, anchor_quote = locate_layout_quote(
        unit.layout_lines,
        quote,
        context_terms=context_terms,
    )
    return bbox, {
        "version": 1,
        "status": "located" if len(bbox) == 4 else "text_anchored",
        "coordinate_space": "pdf_points" if len(bbox) == 4 else "",
        "method": "reviewed_authority_layout_line",
        "anchor_quote": anchor_quote,
        "rendered_pdf_hash": unit.rendered_pdf_hash,
        "page_width": unit.page_width,
        "page_height": unit.page_height,
        "page_rotation": unit.page_rotation,
    }


def _latest_documents(documents: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for document in documents:
        doc_type = str(document.get("doc_type") or "")
        current = latest.get(doc_type)
        if current is None or int(document.get("doc_version") or 0) > int(current.get("doc_version") or 0):
            latest[doc_type] = document
    return latest


def _visual_reason(native_text: str, *, has_page_image: bool = True) -> str:
    # Office unitization can fall back to native text when LibreOffice is not
    # available.  A complex-looking table is not a valid reason to request a
    # visual pass unless the unit actually carries a rendered page image.
    if not has_page_image:
        return "none"
    if not native_text.strip():
        return "scan"
    lines = [line for line in native_text.splitlines() if line.strip()]
    tabular_lines = sum(1 for line in lines if line.count("\t") >= 2)
    if len(lines) >= 5 and tabular_lines >= 4:
        return "complex_table"
    return "none"


def _is_cacheable_extraction(result: object) -> bool:
    """Incomplete evidence must be recomputed on the next review run."""
    return isinstance(result, UnifiedExtractionResult) and result.status == "complete"


def _aggregate_test_items(
    results_by_doc_type: dict[str, list[UnifiedExtractionResult]],
) -> dict[str, list[ObservedTestItem]]:
    aggregated: dict[str, dict[tuple[str, str, str, str], ObservedTestItem]] = defaultdict(dict)
    for doc_type in ("test_plan", "original_records", "final_report"):
        for result in results_by_doc_type.get(doc_type, []):
            for observation in result.observations:
                if observation.observation_type != "test_item":
                    continue
                name = observation.entity_name.strip()
                if not name:
                    continue
                sample_id = str(observation.metadata.get("sample_id") or "").strip()
                mode = str(observation.metadata.get("mode") or "").strip()
                execution_round = str(observation.metadata.get("execution_round") or "").strip()
                # Merge spelling/spacing variants and confirmed aliases inside
                # one document before cross-document identity matching.  Two
                # observations of the same confirmed identity are cumulative
                # evidence, not competing candidates.
                identity_key = alias_key_for(name) or normalize_alias_text(name)
                key = (
                    identity_key, sample_id.casefold(), mode.casefold(),
                    execution_round.casefold(),
                )
                existing = aggregated[doc_type].get(key)
                if existing is None:
                    item_id = _stable_id(
                        "item", doc_type, name.casefold(), sample_id.casefold(),
                        mode.casefold(), execution_round.casefold(),
                    )
                    aggregated[doc_type][key] = ObservedTestItem(
                        item_id=item_id,
                        doc_type=doc_type,  # type: ignore[arg-type]
                        name=name,
                        parent_name=str(observation.metadata.get("parent_name") or ""),
                        sample_id=sample_id,
                        mode=mode,
                        execution_round=execution_round,
                        parameters=dict(observation.metadata.get("parameters") or {}),
                        result=str(observation.metadata.get("result") or ""),
                        evidence_ids=list(observation.evidence_ids),
                        metadata={"observation_ids": [observation.observation_id]},
                    )
                else:
                    existing.evidence_ids = list(dict.fromkeys([
                        *existing.evidence_ids, *observation.evidence_ids,
                    ]))
                    existing.metadata.setdefault("observation_ids", []).append(
                        observation.observation_id
                    )
    reconciled: dict[str, list[ObservedTestItem]] = {}
    for doc_type, keyed_items in aggregated.items():
        by_identity: dict[str, list[tuple[tuple[str, str, str, str], ObservedTestItem]]] = (
            defaultdict(list)
        )
        for key, item in keyed_items.items():
            by_identity[key[0]].append((key, item))
        merged_items: list[ObservedTestItem] = []
        for variants in by_identity.values():
            contextless = [
                item for key, item in variants if not any(key[1:])
            ]
            contextual = [
                item for key, item in variants if any(key[1:])
            ]
            # A summary row often omits sample/mode/round while the sole detail
            # row supplies it.  That omission is not a second execution.  If
            # multiple explicit contexts exist, keep them separate and do not
            # guess which one the summary belongs to.
            if len(contextual) == 1 and contextless:
                target = contextual[0]
                for summary in contextless:
                    target.evidence_ids = list(dict.fromkeys([
                        *target.evidence_ids, *summary.evidence_ids,
                    ]))
                    target.metadata.setdefault("observation_ids", []).extend(
                        summary.metadata.get("observation_ids", [])
                    )
                merged_items.append(target)
            else:
                merged_items.extend(item for _, item in variants)
        reconciled[doc_type] = merged_items
    return reconciled


def _locate_reviewed_quote(source: str, quote: str) -> str:
    """Return the exact source slice even when spreadsheet rendering adds wraps."""
    normalized_source: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(source):
        for normalized in unicodedata.normalize("NFKC", char).casefold():
            if normalized.isspace():
                continue
            normalized_source.append(normalized)
            positions.append(index)
    normalized_quote = "".join(
        char for char in unicodedata.normalize("NFKC", quote).casefold()
        if not char.isspace()
    )
    if not normalized_quote:
        return ""
    offset = "".join(normalized_source).find(normalized_quote)
    if offset < 0:
        return ""
    return source[positions[offset]:positions[offset + len(normalized_quote) - 1] + 1]


def _structured_payload(metadata_rows: list[dict]) -> dict:
    for row in metadata_rows:
        if row.get("field_name") != "__structured__":
            continue
        raw = str(row.get("human_override_value") or row.get("field_value") or "")
        if not raw:
            return {}
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _structured_result_identity_rows(
    doc_type: str,
    structured: dict,
) -> list[dict[str, object]]:
    """Return source-internal test identities used by coverage matching.

    File names are deliberately excluded.  The document-level test item is
    useful provenance, but an execution table's own ``测试项目`` cell is the
    identity authority for a planned subtest.  For example, a raw record headed
    ``瞬态抗扰度试验`` can contain separate result rows for pulse 1, 2a, 2b and
    3a; those four names must be offered to the coverage matcher individually.

    The parent name is retained only as ambiguity context for generic row names
    such as ``脉冲2a``.  It does not by itself prove that two differently named
    tests are the same test.
    """
    rows: list[dict[str, object]] = []
    if doc_type == "original_records":
        for row in structured.get("conclusion_summary", []) or []:
            if not isinstance(row, dict):
                continue
            rows.append({
                "name": str(
                    row.get("test_item_name") or row.get("test_item_code") or ""
                ).strip(),
                "result": (
                    "符合" if row.get("all_rounds_pass") is True
                    else "不符合" if row.get("all_rounds_pass") is False
                    else ""
                ),
                "parameters": {
                    "round_count": row.get("round_count"),
                    "operators": row.get("operators") or [],
                },
                "source_kind": "document_test_item",
            })
        for row in structured.get("data_rows", []) or []:
            if not isinstance(row, dict):
                continue
            spec_params = row.get("spec_params") or {}
            if not isinstance(spec_params, dict):
                spec_params = {}
            parent_name = str(row.get("test_item") or "").strip()
            internal_name = str(
                spec_params.get("测试项目") or row.get("test_item") or ""
            ).strip()
            rows.append({
                "name": internal_name,
                "parent_name": parent_name if parent_name != internal_name else "",
                "sample_id": str(
                    row.get("sample_id") or row.get("sample_code") or ""
                ).strip(),
                "mode": str(row.get("test_mode") or "").strip(),
                "execution_round": str(
                    row.get("execution_round") or row.get("round") or ""
                ).strip(),
                "result": str(row.get("test_result") or "").strip(),
                "source_quote": str(row.get("source_quote") or "").strip(),
                "parameters": {
                    "spec_params": spec_params,
                    "required_performance": str(
                        row.get("required_performance") or ""
                    ),
                    "actual_performance": str(
                        row.get("actual_performance") or ""
                    ),
                },
                "source_kind": "internal_result_table",
            })
        return rows

    for row in structured.get("results", []) or []:
        if not isinstance(row, dict):
            continue
        rows.append({
            "name": str(row.get("test_item") or "").strip(),
            "mode": str(row.get("test_mode") or "").strip(),
            "result": str(row.get("result") or "").strip(),
            "parameters": {
                "standard_requirement": str(
                    row.get("standard_requirement") or ""
                ),
                "grade_requirement": str(row.get("grade_requirement") or ""),
            },
            "source_kind": "document_test_item",
        })
    for item in structured.get("item_extractions", []) or []:
        if not isinstance(item, dict):
            continue
        parent_name = str(
            item.get("test_item_name") or item.get("test_item_code") or ""
        ).strip()
        test_results = item.get("test_results") or {}
        if not isinstance(test_results, dict):
            continue
        for block in test_results.get("sample_data", []) or []:
            if not isinstance(block, dict):
                continue
            sample_id = str(block.get("sample_id") or "").strip()
            mode = str(block.get("mode") or "").strip()
            for detail in block.get("data_rows", []) or []:
                if not isinstance(detail, dict):
                    continue
                internal_name = str(
                    detail.get("test_item") or parent_name
                ).strip()
                quote_parts = [
                    internal_name,
                    str(detail.get("injection_point") or "").strip(),
                    str(detail.get("spec_requirement") or "").strip(),
                    str(detail.get("test_duration") or "").strip(),
                    str(detail.get("required_level") or "").strip(),
                    str(detail.get("actual_level") or "").strip(),
                    str(detail.get("verdict") or "").strip(),
                ]
                rows.append({
                    "name": internal_name,
                    "parent_name": (
                        parent_name if parent_name != internal_name else ""
                    ),
                    "sample_id": sample_id,
                    "mode": mode,
                    "result": str(detail.get("verdict") or "").strip(),
                    "source_quote": " ".join(
                        part for part in quote_parts if part
                    ),
                    "anchor_quote": " ".join(
                        part for part in (
                            internal_name,
                            str(detail.get("injection_point") or "").strip(),
                            str(detail.get("test_duration") or "").strip(),
                            str(detail.get("verdict") or "").strip(),
                        ) if part
                    ),
                    "parameters": {
                        "injection_point": str(
                            detail.get("injection_point") or ""
                        ),
                        "spec_requirement": str(
                            detail.get("spec_requirement") or ""
                        ),
                        "test_duration": str(
                            detail.get("test_duration") or ""
                        ),
                        "required_level": str(
                            detail.get("required_level") or ""
                        ),
                        "actual_level": str(
                            detail.get("actual_level") or ""
                        ),
                    },
                    "source_kind": "internal_result_table",
                })
    return rows


def _reviewed_result_authority(
    graph_id: str,
    doc_type: str,
    document: dict,
    units: list[EvidenceGraphDocumentUnit],
    metadata_rows: list[dict],
) -> tuple[list[ObservedTestItem], list[EvidenceRecord], bool]:
    """Reuse evidence-gated document extraction for coverage identities.

    The dedicated raw-record and report extractors already perform schema,
    source-quote, and quantity gates. Re-running a generic LLM against every
    page adds cost and latency without increasing identity assurance. This
    adapter only activates when those upstream gates are complete (or, for a
    draft report, when the sole partial reason is missing cover identity).
    """
    if doc_type not in {"original_records", "final_report"}:
        return [], [], False
    structured = _structured_payload(metadata_rows)
    if not structured:
        return [], [], False

    if doc_type == "original_records":
        failures = list(structured.get("semantic_extraction_failures") or [])
        metrics = dict(structured.get("extraction_metrics") or {})
        valid = (
            structured.get("extraction_quality") == "complete"
            and not failures
            and int(structured.get("semantic_extraction_count") or 0)
            == int(metrics.get("pdf_count") or structured.get("total_files") or -1)
        )
        rows = _structured_result_identity_rows(doc_type, structured)
    else:
        failed_passes = set(structured.get("failed_passes") or [])
        identity_safe_failures = {
            "structural/cover_identity_missing", "instruments",
        }
        identity_safe = all(
            failure in identity_safe_failures
            or str(failure).startswith("item/sample_coverage_missing:")
            for failure in failed_passes
        )
        valid = (
            structured.get("extraction_quality") == "complete"
            # Cover identity and the independent instrument inventory do not
            # weaken the source-grounded test-result identity universe. A
            # missing sample execution is audited by GRAPH-SAMPLE separately;
            # it must not discard the identities of rows that were found.
            or bool(failed_passes) and identity_safe
        )
        rows = _structured_result_identity_rows(doc_type, structured)
    if not valid or not rows:
        return [], [], False

    doc_units = [unit for unit in units if unit.doc_type == doc_type]
    items: list[ObservedTestItem] = []
    evidence: list[EvidenceRecord] = []
    candidates_by_identity: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        name_key = normalize_alias_text(name) or name.casefold()
        if not name:
            continue
        candidates_by_identity[name_key].append(row)

    expected_context_count = 0
    anchored_context_count = 0
    for name_key, candidate_rows in candidates_by_identity.items():
        # A document summary/result is the base test-item identity. Internal
        # table rows are execution details: they may retain a more specific
        # identity for forward plan matching, but must never erase the base
        # item merely because the report expands it into pulse/mode rows.
        document_rows = [
            row for row in candidate_rows
            if row.get("source_kind") == "document_test_item"
        ]
        internal_rows = [
            row for row in candidate_rows
            if row.get("source_kind") == "internal_result_table"
        ]
        identity_role = "base_item" if document_rows else "execution_detail"
        # For an internal row whose identity is exactly the same as the base
        # item, keep the richer per-sample/mode/round evidence. The presence of
        # the document row still makes this a base item, not a child test item.
        selected_rows = internal_rows or candidate_rows
        expected_context_count += len(selected_rows)
        name = str(selected_rows[0].get("name") or "").strip()
        item_evidence_ids: list[str] = []
        execution_contexts: list[dict[str, object]] = []
        anchored_rows: list[dict[str, object]] = []
        for row_index, row in enumerate(selected_rows):
            located_unit = None
            exact_quote = ""
            source_quote = str(row.get("source_quote") or "").strip()
            anchor_quote = str(row.get("anchor_quote") or "").strip()
            # Exhaust the most specific row quote across every page before
            # falling back to a shorter candidate. Otherwise a summary page
            # can steal the anchor from the actual result table.
            for quote_candidate in dict.fromkeys((source_quote, anchor_quote, name)):
                if not quote_candidate:
                    continue
                for unit in doc_units:
                    exact_quote = _locate_reviewed_quote(
                        unit.native_text, quote_candidate,
                    )
                    if exact_quote:
                        located_unit = unit
                        break
                if located_unit is not None:
                    break
            if located_unit is None:
                logger.warning(
                    "unified_review_structured_authority_quote_missing",
                    graph_id=graph_id,
                    doc_type=doc_type,
                    item_name_hash=hashlib.sha256(name.encode()).hexdigest()[:16],
                )
                continue
            anchored_context_count += 1
            context_signature = json.dumps({
                "sample_id": str(row.get("sample_id") or ""),
                "mode": str(row.get("mode") or ""),
                "execution_round": str(row.get("execution_round") or ""),
                "source_quote": source_quote,
                "parameters": dict(row.get("parameters") or {}),
                "row_index": row_index,
            }, ensure_ascii=False, sort_keys=True)
            evidence_id = _stable_id(
                "evidence", graph_id, str(document.get("doc_id") or ""),
                located_unit.unit_id, "validated_structured_authority",
                name_key, context_signature,
            )
            bbox, source_anchor = _source_anchor(
                located_unit,
                exact_quote,
                context_terms=tuple(
                    value for value in (
                        name,
                        str(row.get("sample_id") or "").strip(),
                        str(row.get("mode") or "").strip(),
                    )
                    if value
                ),
            )
            evidence.append(EvidenceRecord(
                evidence_id=evidence_id,
                graph_id=graph_id,
                doc_id=str(document.get("doc_id") or ""),
                doc_type=doc_type,
                filename=str(document.get("filename") or ""),
                state="found",
                page_number=located_unit.page_number,
                sheet_name=located_unit.sheet_name,
                cell_range=located_unit.cell_range,
                bbox=bbox,
                exact_quote=exact_quote,
                content_hash=located_unit.source_hash,
                extraction_method="validated_structured_authority",
                confidence=1,
                metadata={
                    "unit_id": located_unit.unit_id,
                    "quality_gated": True,
                    "identity_source": str(row.get("source_kind") or ""),
                    "parent_context": str(row.get("parent_name") or ""),
                    "sample_id": str(row.get("sample_id") or ""),
                    "mode": str(row.get("mode") or ""),
                    "execution_round": str(row.get("execution_round") or ""),
                    "source_anchor": source_anchor,
                },
            ))
            item_evidence_ids.append(evidence_id)
            anchored_rows.append(row)
            execution_contexts.append({
                "sample_id": str(row.get("sample_id") or ""),
                "mode": str(row.get("mode") or ""),
                "execution_round": str(row.get("execution_round") or ""),
                "result": str(row.get("result") or ""),
                "parameters": dict(row.get("parameters") or {}),
                "evidence_ids": [evidence_id],
            })
        if not anchored_rows:
            continue

        def sole_value(field: str) -> str:
            values = {
                str(row.get(field) or "").strip() for row in anchored_rows
                if str(row.get(field) or "").strip()
            }
            return next(iter(values)) if len(values) == 1 else ""

        parameters = (
            dict(anchored_rows[0].get("parameters") or {})
            if len(anchored_rows) == 1 else {}
        )
        items.append(ObservedTestItem(
            item_id=_stable_id("item", doc_type, name_key, "", "", ""),
            doc_type=doc_type,  # type: ignore[arg-type]
            name=name,
            parent_name=sole_value("parent_name"),
            sample_id=sole_value("sample_id"),
            mode=sole_value("mode"),
            execution_round=sole_value("execution_round"),
            parameters=parameters,
            result=sole_value("result"),
            evidence_ids=list(dict.fromkeys(item_evidence_ids)),
            metadata={
                "source": "validated_structured_extraction",
                "identity_source": str(anchored_rows[0].get("source_kind") or ""),
                "identity_role": identity_role,
                "execution_contexts": execution_contexts,
            },
        ))
    return (
        items,
        evidence,
        bool(items) and anchored_context_count == expected_context_count,
    )


def _reviewed_plan_authority(
    graph_id: str,
    document: dict,
    units: list[EvidenceGraphDocumentUnit],
    metadata_rows: list[dict],
) -> tuple[list[ObservedTestItem], list[EvidenceRecord]]:
    """Build the requirement universe from the human-reviewed plan extraction."""
    structured_raw = ""
    for row in metadata_rows:
        if row.get("field_name") != "__structured__":
            continue
        structured_raw = str(
            row.get("human_override_value") or row.get("field_value") or ""
        )
        break
    if not structured_raw:
        return [], []
    try:
        structured = json.loads(structured_raw)
    except (TypeError, ValueError):
        logger.warning(
            "unified_review_plan_authority_invalid_json",
            graph_id=graph_id,
            doc_id=document.get("doc_id", ""),
        )
        return [], []

    details_by_code = {
        str(detail.get("code") or "").strip().casefold(): dict(detail.get("fields") or {})
        for detail in structured.get("test_details", [])
        if isinstance(detail, dict) and str(detail.get("code") or "").strip()
    }
    plan_units = [unit for unit in units if unit.doc_type == "test_plan"]
    items: list[ObservedTestItem] = []
    evidence: list[EvidenceRecord] = []
    seen_identities: set[tuple[str, str]] = set()
    for row in structured.get("test_items", []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("code") or "").strip()
        name_key = name.casefold()
        code_key = str(row.get("code") or "").strip().casefold()
        mode = str(row.get("test_mode") or "").strip()
        mode_key = mode.casefold()
        identity_key = (name_key, mode_key)
        if not name or identity_key in seen_identities:
            continue
        seen_identities.add(identity_key)
        located_unit: EvidenceGraphDocumentUnit | None = None
        exact_quote = ""
        for unit in plan_units:
            exact_quote = _locate_reviewed_quote(unit.native_text, name)
            if exact_quote:
                located_unit = unit
                break
        if located_unit is None:
            logger.warning(
                "unified_review_plan_authority_quote_missing",
                graph_id=graph_id,
                doc_id=document.get("doc_id", ""),
                item_name_hash=hashlib.sha256(name.encode()).hexdigest()[:16],
            )
            continue

        evidence_id = _stable_id(
            "evidence", graph_id, str(document.get("doc_id") or ""),
            located_unit.unit_id, "reviewed_plan_authority", name_key, mode_key,
        )
        item_id = _stable_id("item", "test_plan", name_key, "", mode_key, "")
        # Detail blocks are keyed by the plan's stable item code.  A human
        # readable test name frequently differs from that code, so looking up
        # by name alone silently drops pulse/mode aliases and method fields.
        detail_fields = details_by_code.get(code_key) or details_by_code.get(name_key, {})
        identity_text = " ".join(str(value) for value in detail_fields.values())
        identity_aliases = list(dict.fromkeys(
            f"P{match.upper()}"
            for match in re.findall(
                r"(?i)(?:试验|测试)?\s*(?:脉冲|pulse)\s*P?\s*([1-9]\d?[a-z]?)",
                identity_text,
            )
        ))
        bbox, source_anchor = _source_anchor(
            located_unit,
            exact_quote,
            context_terms=tuple(value for value in (name, mode) if value),
        )
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id,
            graph_id=graph_id,
            doc_id=str(document.get("doc_id") or ""),
            doc_type="test_plan",
            filename=str(document.get("filename") or ""),
            state="found",
            page_number=located_unit.page_number,
            sheet_name=located_unit.sheet_name,
            cell_range=located_unit.cell_range,
            bbox=bbox,
            exact_quote=exact_quote,
            content_hash=located_unit.source_hash,
            extraction_method="reviewed_plan_authority",
            confidence=1,
            metadata={
                "unit_id": located_unit.unit_id,
                "human_reviewed": True,
                "source_anchor": source_anchor,
            },
        ))
        items.append(ObservedTestItem(
            item_id=item_id,
            doc_type="test_plan",
            name=name,
            mode=mode,
            parameters={
                "acceptance": str(row.get("acceptance") or ""),
                "standard_clause": str(row.get("standard_clause") or ""),
                "sample_requirements": dict(row.get("sample_requirements") or {}),
                **detail_fields,
            },
            evidence_ids=[evidence_id],
            metadata={
                "source": "reviewed_structured_plan",
                "identity_aliases": identity_aliases,
            },
        ))
    return items, evidence


def _relationship_nodes(graph_id: str, items: list[ObservedTestItem]) -> list[GraphNode]:
    node_types = {
        "test_plan": NodeType.TEST_REQUIREMENT,
        "original_records": NodeType.TEST_EXECUTION,
        "final_report": NodeType.DOCUMENT_OBSERVATION,
    }
    return [GraphNode(
        node_id=item.item_id,
        graph_id=graph_id,
        node_type=node_types[item.doc_type],
        label=item.name,
        canonical_key=item.name.casefold(),
        properties={
            "doc_type": item.doc_type,
            "parent_name": item.parent_name,
            "sample_id": item.sample_id,
            "mode": item.mode,
            "execution_round": item.execution_round,
            "evidence_ids": item.evidence_ids,
        },
    ) for item in items]


async def _semantic_identity_overrides(
    graph_id: str,
    requirements: list[ObservedTestItem],
    observations: list[ObservedTestItem],
    evidence: list[EvidenceRecord],
    gateway: UnifiedModelGateway,
) -> dict[str, str]:
    deterministic = match_test_items(requirements, observations)
    unresolved_requirements = [
        item for item in requirements
        if deterministic[item.item_id].status != "matched"
    ]
    matched_observation_ids = {
        match.observation_id for match in deterministic.values()
        if match.status == "matched" and match.observation_id
    }
    unresolved_observations = [
        item for item in observations if item.item_id not in matched_observation_ids
    ]
    # A confirmed execution/report identity is consumed by its deterministic
    # requirement. Reusing it as a semantic candidate for another requirement
    # would both waste a model call and allow one document item to cover two
    # required tests.
    if not unresolved_requirements or not unresolved_observations:
        return {}
    relevant_evidence_ids = {
        evidence_id
        for item in [*unresolved_requirements, *unresolved_observations]
        for evidence_id in item.evidence_ids
    }
    resolution = await resolve_relationship_candidates(
        graph_id,
        _relationship_nodes(graph_id, [*unresolved_requirements, *unresolved_observations]),
        [item for item in evidence if item.evidence_id in relevant_evidence_ids],
        gateway,
    )
    requirement_ids = {item.item_id for item in unresolved_requirements}
    observation_ids = {item.item_id for item in unresolved_observations}
    accepted_relations = {
        RelationType.EXECUTED_AS,
        RelationType.REPORTED_AS,
        RelationType.SAME_ENTITY,
        RelationType.VARIANT_OF,
    }
    proposed_pairs: list[tuple[str, str]] = []
    for edge in resolution.accepted_edges:
        if edge.relation_type not in accepted_relations:
            continue
        if edge.source_node_id in requirement_ids and edge.target_node_id in observation_ids:
            proposed_pairs.append((edge.source_node_id, edge.target_node_id))
        elif edge.target_node_id in requirement_ids and edge.source_node_id in observation_ids:
            proposed_pairs.append((edge.target_node_id, edge.source_node_id))
    requirement_degree: dict[str, int] = defaultdict(int)
    observation_degree: dict[str, int] = defaultdict(int)
    for requirement_id, observation_id in set(proposed_pairs):
        requirement_degree[requirement_id] += 1
        observation_degree[observation_id] += 1
    # An accepted semantic relation is still only a proposal until the global
    # one-to-one constraint holds. Conflicting proposals remain unresolved.
    return {
        requirement_id: observation_id
        for requirement_id, observation_id in set(proposed_pairs)
        if requirement_degree[requirement_id] == 1
        and observation_degree[observation_id] == 1
    }


async def _targeted_recovery(
    *,
    doc_type: str,
    units: list[EvidenceGraphDocumentUnit],
    requirements: list[ObservedTestItem],
    observations: list[ObservedTestItem],
    gateway: UnifiedModelGateway,
) -> tuple[list[UnifiedExtractionResult], bool]:
    matches = match_test_items(requirements, observations)
    targets = [
        item.name for item in requirements
        if matches[item.item_id].status != "matched"
    ]
    if not targets:
        return [], True
    all_visual_units = [
        unit for unit in units if unit.doc_type == doc_type and unit.image_data_urls
    ]
    compact_targets = [
        re.sub(r"[\W_]+", "", target.casefold()) for target in targets
        if len(re.sub(r"[\W_]+", "", target.casefold())) >= 3
    ]
    searchable = []
    for unit in all_visual_units:
        compact_text = re.sub(r"[\W_]+", "", unit.native_text.casefold())
        if unit.visual_reason == "complex_table" or any(
            target in compact_text for target in compact_targets
        ):
            searchable.append(unit)
    max_pages = max(1, int(os.getenv("EVIDENCE_GRAPH_RECOVERY_MAX_PAGES", "80")))
    if not searchable or len(searchable) > max_pages:
        logger.info(
            "unified_review_targeted_recovery_deferred",
            doc_type=doc_type,
            target_count=len(targets),
            searchable_page_count=len(searchable),
            page_limit=max_pages,
        )
        return [], False
    recovery_concurrency = max(
        1, int(os.getenv("EVIDENCE_GRAPH_RECOVERY_CONCURRENCY", "1")),
    )
    recovered: list[UnifiedExtractionResult | BaseException] = []
    for start in range(0, len(searchable), recovery_concurrency):
        recovered.extend(await asyncio.gather(*[
            recover_targeted_evidence(unit, targets, gateway)
            for unit in searchable[start:start + recovery_concurrency]
        ], return_exceptions=True))
    results: list[UnifiedExtractionResult] = []
    complete = True
    for result in recovered:
        if isinstance(result, Exception):
            complete = False
            continue
        results.append(result)
        if result.status != "complete":
            complete = False
    # Candidate-page recovery can find positive evidence, but it is conclusive
    # absence proof only when every rendered unit was searched successfully.
    full_document_searched = bool(all_visual_units) and len(searchable) == len(all_visual_units)
    return (
        results,
        complete and len(results) == len(searchable) and full_document_searched,
    )


async def run_unified_review(
    *,
    set_id: str,
    graph_id: str | None = None,
    gateway: UnifiedModelGateway | None = None,
    store: EvidenceGraphStore | None = None,
    precreated: bool = False,
) -> str:
    """Build a new evidence graph from the latest four required documents.

    This initial vertical slice deliberately leaves missing identities
    unresolved.  Targeted recovery and semantic relationship fallback can then
    append evidence and trigger a deterministic re-evaluation.
    """
    document_set = await database.get_document_set_async(set_id)
    if not document_set:
        raise ValueError("文档集不存在")
    documents = _latest_documents(document_set.get("documents", []))
    missing = sorted(set(_REQUIRED_DOC_TYPES) - set(documents))
    if missing:
        raise ValueError(f"缺少文档: {', '.join(missing)}")
    selected_standards = await database.get_document_set_standards_async(set_id)
    release_ids = [
        standard.selected_release_id for standard in selected_standards
        if standard.selected_release_id
    ]
    standard_releases = [
        release for release in await asyncio.gather(*[
            database.get_standard_release_async(release_id) for release_id in release_ids
        ]) if release and release.get("status") == "published"
    ]

    graph_id = graph_id or f"graph-{uuid.uuid4().hex[:20]}"
    gateway = gateway or UnifiedModelGateway()
    store = store or EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    engine = EvidenceGraphReviewEngine(store)
    extraction_cache = UnifiedExtractionCache(database.DB_PATH)
    extraction_cache.init_schema()
    extraction_manifest = {
        "native_semantic_model": gateway.manifest().get("native_semantic_model"),
        "vision_model": gateway.manifest().get("vision_model"),
        "extraction_strategy": "native_plus_targeted_qwen",
    }
    if not precreated:
        engine.create_run(
            graph_id,
            set_id,
            standard_release_ids=release_ids,
            model_manifest={
                **gateway.manifest(),
                "extraction_strategy": "native_plus_targeted_qwen",
            },
        )

    results_by_doc_type: dict[str, list[UnifiedExtractionResult]] = defaultdict(list)
    all_evidence: dict[str, EvidenceRecord] = {}
    failed_units: list[str] = []
    failed_units_by_doc: dict[str, list[str]] = defaultdict(list)
    total_units = 0
    try:
        units: list[EvidenceGraphDocumentUnit] = []
        for doc_type in _REQUIRED_DOC_TYPES:
            document = documents[doc_type]
            file_bytes = await database.get_file_content_async(document["doc_id"])
            if not file_bytes:
                raise ValueError(f"{doc_type}缺少原始文件内容")
            unitized = await asyncio.to_thread(
                unitize_document,
                file_bytes,
                doc_id=document["doc_id"],
                doc_type=doc_type,
                filename=document.get("filename", ""),
            )
            if unitized.errors:
                errors = [f"{doc_type}:{error}" for error in unitized.errors]
                failed_units.extend(errors)
                failed_units_by_doc[doc_type].extend(errors)
            for old_unit in unitized.units:
                units.append(EvidenceGraphDocumentUnit(
                    unit_id=f"{doc_type}:{old_unit.unit_id}",
                    graph_id=graph_id,
                    doc_id=old_unit.doc_id,
                    doc_type=doc_type,
                    filename=old_unit.filename,
                    native_text=old_unit.native_text,
                    page_number=old_unit.page_number,
                    sheet_name=old_unit.sheet_name,
                    cell_range=old_unit.cell_range,
                    image_data_urls=old_unit.image_data_urls,
                    source_hash=old_unit.source_hash,
                    rendered_pdf_hash=old_unit.rendered_pdf_hash,
                    page_width=old_unit.page_width,
                    page_height=old_unit.page_height,
                    page_rotation=old_unit.page_rotation,
                    layout_lines=old_unit.layout_lines,
                    visual_reason=_visual_reason(
                        old_unit.native_text,
                        has_page_image=bool(old_unit.image_data_urls),
                    ),  # type: ignore[arg-type]
                ))

        total_units = len(units)
        metadata_values = await asyncio.gather(*[
            database.get_extracted_metadata_async(documents[doc_type]["doc_id"])
            for doc_type in _REQUIRED_DOC_TYPES
        ])
        metadata_by_doc_type = dict(zip(_REQUIRED_DOC_TYPES, metadata_values))
        authoritative_plan_items, authoritative_plan_evidence = _reviewed_plan_authority(
            graph_id, documents["test_plan"], units, metadata_by_doc_type["test_plan"],
        )
        plan_payload = _structured_payload(metadata_by_doc_type["test_plan"])
        expected_plan_identities = {
            (
                str(row.get("name") or row.get("code") or "").strip().casefold(),
                str(row.get("test_mode") or "").strip().casefold(),
            )
            for row in plan_payload.get("test_items", [])
            if isinstance(row, dict)
            and str(row.get("name") or row.get("code") or "").strip()
        }
        plan_extraction_complete = bool(
            authoritative_plan_items
            and plan_payload.get("extraction_quality") == "complete"
            and not plan_payload.get("failed_passes")
            and len(authoritative_plan_items) == len(expected_plan_identities)
            and not failed_units_by_doc.get("test_plan")
        )
        structured_authorities: dict[str, list[ObservedTestItem]] = {}
        authority_complete: dict[str, bool] = {}
        for doc_type in ("original_records", "final_report"):
            authority_items, authority_evidence, complete = _reviewed_result_authority(
                graph_id, doc_type, documents[doc_type], units,
                metadata_by_doc_type[doc_type],
            )
            if authority_items:
                structured_authorities[doc_type] = authority_items
                authority_complete[doc_type] = complete
                for item in authority_evidence:
                    all_evidence[item.evidence_id] = item

        # Dedicated extractors are authoritative only after their own quality
        # gates. Generic per-page extraction remains the fallback for partial,
        # stale, or unsupported structured payloads.
        reusable_doc_types = set(structured_authorities)
        if authoritative_plan_items:
            reusable_doc_types.add("test_plan")
        if _structured_payload(metadata_by_doc_type["order_form"]):
            reusable_doc_types.add("order_form")
        extraction_units = [unit for unit in units if unit.doc_type not in reusable_doc_types]
        logger.info(
            "unified_review_structured_reuse",
            graph_id=graph_id,
            reused_doc_types=sorted(reusable_doc_types),
            reused_unit_count=len(units) - len(extraction_units),
            fallback_unit_count=len(extraction_units),
            reuse_quality={
                doc_type: {
                    "quality": str(
                        _structured_payload(metadata_by_doc_type[doc_type]).get(
                            "extraction_quality", "unknown",
                        )
                    ),
                    "failed_passes": sorted(
                        str(item) for item in (
                            _structured_payload(metadata_by_doc_type[doc_type]).get(
                                "failed_passes", [],
                            ) or []
                        )
                    ),
                }
                for doc_type in sorted(reusable_doc_types)
            },
            dependent_check_families=[
                "coverage", "document_fields", "general_graph_checks",
            ],
        )
        concurrency = max(1, int(os.getenv("EVIDENCE_GRAPH_UNIT_CONCURRENCY", "2")))
        for start in range(0, len(extraction_units), concurrency):
            batch = extraction_units[start:start + concurrency]
            cached_results = await asyncio.gather(*[
                asyncio.to_thread(extraction_cache.get, unit, extraction_manifest)
                for unit in batch
            ])
            misses = [
                (index, unit) for index, (unit, cached) in enumerate(zip(batch, cached_results))
                if cached is None
            ]
            fresh_results = await asyncio.gather(*[
                extract_evidence_graph_unit(unit, gateway) for _, unit in misses
            ], return_exceptions=True)
            batch_results: list[UnifiedExtractionResult | BaseException] = list(cached_results)  # type: ignore[list-item]
            for (index, unit), result in zip(misses, fresh_results):
                batch_results[index] = result
                # Only a fully validated unit is safe to reuse.  Caching a
                # needs_review result makes a transient schema/quote failure
                # permanent across reruns and can hide recoverable evidence.
                if _is_cacheable_extraction(result):
                    await asyncio.to_thread(
                        extraction_cache.put, unit, extraction_manifest, result,
                    )
            logger.info(
                "unified_review_extraction_cache_batch",
                graph_id=graph_id,
                batch_unit_count=len(batch),
                hit_count=len(batch) - len(misses),
                miss_count=len(misses),
            )
            for unit, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    failed_units.append(f"{unit.unit_id}:提取失败")
                    failed_units_by_doc[unit.doc_type].append(unit.unit_id)
                    logger.warning(
                        "unified_review_unit_failed",
                        graph_id=graph_id,
                        unit_id=unit.unit_id,
                        doc_type=unit.doc_type,
                        error_type=type(result).__name__,
                    )
                    continue
                results_by_doc_type[unit.doc_type].append(result)
                if result.status != "complete":
                    failed_units.append(unit.unit_id)
                    failed_units_by_doc[unit.doc_type].append(unit.unit_id)
                for evidence in result.evidence:
                    all_evidence[evidence.evidence_id] = evidence

        test_items = _aggregate_test_items(results_by_doc_type)
        if authoritative_plan_items:
            test_items["test_plan"] = authoritative_plan_items
            for item in authoritative_plan_evidence:
                all_evidence[item.evidence_id] = item
            logger.info(
                "unified_review_plan_authority_applied",
                graph_id=graph_id,
                reviewed_item_count=len(authoritative_plan_items),
                evidence_count=len(authoritative_plan_evidence),
            )
        for doc_type, authority_items in structured_authorities.items():
            test_items[doc_type] = authority_items
            logger.info(
                "unified_review_result_authority_applied",
                graph_id=graph_id,
                doc_type=doc_type,
                reviewed_item_count=len(authority_items),
            )
        recovery_complete: dict[str, bool] = {}
        for doc_type in ("original_records", "final_report"):
            if authority_complete.get(doc_type):
                recovery_complete[doc_type] = True
                continue
            recovery_results, recovery_complete[doc_type] = await _targeted_recovery(
                doc_type=doc_type,
                units=units,
                requirements=test_items.get("test_plan", []),
                observations=test_items.get(doc_type, []),
                gateway=gateway,
            )
            results_by_doc_type[doc_type].extend(recovery_results)
            for result in recovery_results:
                for evidence in result.evidence:
                    all_evidence[evidence.evidence_id] = evidence

        test_items = _aggregate_test_items(results_by_doc_type)
        if authoritative_plan_items:
            test_items["test_plan"] = authoritative_plan_items
        for doc_type, authority_items in structured_authorities.items():
            test_items[doc_type] = authority_items
        identity_overrides: dict[str, dict[str, str]] = {}
        for doc_type in ("original_records", "final_report"):
            try:
                identity_overrides[doc_type] = await _semantic_identity_overrides(
                    graph_id,
                    test_items.get("test_plan", []),
                    test_items.get(doc_type, []),
                    list(all_evidence.values()),
                    gateway,
                )
            except Exception as exc:
                identity_overrides[doc_type] = {}
                logger.warning(
                    "unified_review_relationship_fallback_failed",
                    graph_id=graph_id,
                    doc_type=doc_type,
                    error_type=type(exc).__name__,
                )

        coverage_proofs = [CoverageProof(
            doc_type=doc_type,  # type: ignore[arg-type]
            extraction_complete=not failed_units_by_doc.get(doc_type),
            identity_variants_searched=True,
            parent_subitems_searched=True,
            targeted_visual_recovery_completed=recovery_complete.get(doc_type, False),
            unparsed_units=failed_units_by_doc.get(doc_type, []),
            evidence_ids=list(dict.fromkeys(
                evidence.evidence_id for evidence in all_evidence.values()
                if evidence.doc_type == doc_type
            )),
        ) for doc_type in ("original_records", "final_report")]
        engine.persist_test_item_coverage(
            graph_id,
            test_items.get("test_plan", []),
            test_items.get("original_records", []),
            test_items.get("final_report", []),
            list(all_evidence.values()),
            coverage_proofs=coverage_proofs,
            identity_overrides=identity_overrides,
            plan_extraction_complete=(
                plan_extraction_complete
                and not failed_units_by_doc.get("test_plan")
            ),
        )
        general_results = [
            result for values in results_by_doc_type.values() for result in values
        ]
        structured_check_results = build_structured_check_results(
            graph_id,
            units,
            metadata_by_doc_type,
            doc_types=reusable_doc_types & {"test_plan", "original_records", "final_report"},
        )
        general_results.extend(structured_check_results)
        structured_bridge_failures = [
            result.unit_id for result in structured_check_results
            if result.status != "complete"
        ]
        structured_bridge_coverage: dict[str, dict] = {}
        for result in structured_check_results:
            coverage = result.audit_metadata.get("check_coverage", {})
            if not isinstance(coverage, dict):
                continue
            for check_id, item in coverage.items():
                if not isinstance(item, dict):
                    continue
                target = structured_bridge_coverage.setdefault(str(check_id), {
                    "expected_input_count": 0,
                    "anchored_input_count": 0,
                    "reason_codes": [],
                    "document_types": [],
                })
                target["expected_input_count"] += int(item.get("expected_input_count") or 0)
                target["anchored_input_count"] += int(item.get("anchored_input_count") or 0)
                if item.get("reason_code"):
                    target["reason_codes"].append(str(item["reason_code"]))
                target["document_types"].extend(item.get("document_types") or [])
        for check_id, item in structured_bridge_coverage.items():
            item["reason_codes"] = sorted(set(item["reason_codes"]))
            item["document_types"] = sorted(set(item["document_types"]))
            logger.info(
                "structured_bridge_coverage",
                graph_id=graph_id,
                check_id=check_id,
                expected_input_count=item["expected_input_count"],
                anchored_input_count=item["anchored_input_count"],
                reason_codes=item["reason_codes"],
                document_types=item["document_types"],
            )
        if structured_bridge_failures:
            failed_units.extend(
                f"{unit_id}:结构化审核证据回锚不完整"
                for unit_id in structured_bridge_failures
            )
        store.update_run_metadata(graph_id, {
            "structured_check_bridge": {
                "result_count": len(structured_check_results),
                "observation_count": sum(
                    len(result.observations) for result in structured_check_results
                ),
                "evidence_count": sum(
                    len(result.evidence) for result in structured_check_results
                ),
                "failed_result_count": len(structured_bridge_failures),
                "coverage_by_check": structured_bridge_coverage,
            },
        })
        logger.info(
            "unified_review_structured_check_bridge",
            graph_id=graph_id,
            result_count=len(structured_check_results),
            observation_count=sum(
                len(result.observations) for result in structured_check_results
            ),
            failed_result_count=len(structured_bridge_failures),
        )
        engine.persist_general_observations(graph_id, general_results)
        engine.persist_reviewed_document_checks(
            graph_id, documents, units, metadata_by_doc_type,
        )
        engine.persist_standard_advisories(
            graph_id, test_items.get("test_plan", []), standard_releases,
            documents=documents, units=units,
            metadata_by_doc_type=metadata_by_doc_type,
        )
        # Keep deterministic finding copy authoritative.  This optional,
        # evidence-bound batch only adds a short explanation and never blocks
        # or changes the review result when the model is unavailable.
        await enrich_finding_supplements(graph_id, store, gateway)
        if failed_units:
            store.update_run_status(graph_id, "machine_incomplete")
            store.add_event(DecisionEvent(
                event_id=_stable_id("event", graph_id, "extraction_incomplete"),
                graph_id=graph_id,
                event_type="extraction_incomplete",
                subject_type="graph_run",
                subject_id=graph_id,
                new_state="machine_incomplete",
                reason_code="unit_extraction_not_complete",
                payload={
                    "failed_unit_count": len(failed_units),
                    "total_unit_count": total_units,
                },
            ))
    except asyncio.CancelledError:
        store.update_run_status(graph_id, "aborted")
        store.add_event(DecisionEvent(
            event_id=_stable_id("event", graph_id, "run_aborted"),
            graph_id=graph_id,
            event_type="run_aborted",
            subject_type="graph_run",
            subject_id=graph_id,
            new_state="aborted",
            reason_code="task_cancelled",
            payload={"completed_unit_count": sum(len(value) for value in results_by_doc_type.values())},
        ))
        raise
    except Exception as exc:
        store.update_run_status(graph_id, "failed")
        store.add_event(DecisionEvent(
            event_id=_stable_id("event", graph_id, "run_failed"),
            graph_id=graph_id,
            event_type="run_failed",
            subject_type="graph_run",
            subject_id=graph_id,
            new_state="failed",
            reason_code=type(exc).__name__,
            payload={"completed_unit_count": sum(len(value) for value in results_by_doc_type.values())},
        ))
        logger.error(
            "unified_review_failed",
            graph_id=graph_id,
            set_id=set_id,
            error_type=type(exc).__name__,
        )
        raise

    logger.info(
        "unified_review_completed",
        graph_id=graph_id,
        set_id=set_id,
        total_unit_count=total_units,
        failed_unit_count=len(failed_units),
        evidence_count=len(all_evidence),
    )
    return graph_id
