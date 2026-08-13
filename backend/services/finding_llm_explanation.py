"""Evidence-bound LLM supplements for reviewer-facing findings.

The deterministic presentation remains authoritative.  This module may add a
short explanation, but it cannot change a finding status, title, evidence set,
or business conclusion.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from services.evidence_graph_models import GraphSnapshot
from services.evidence_graph_store import EvidenceGraphStore
from services.unified_model_gateway import UnifiedModelGateway, UnifiedModelUnavailable
from utils.logger import get_logger


logger = get_logger(__name__)
_MAX_FINDINGS = 24
_MAX_EVIDENCE_PER_FINDING = 6
_MAX_TEXT = 240
_BATCH_SIZE = 3
_MAX_CONCURRENT_BATCHES = 2
_EXPLANATION_TIMEOUT = max(3, int(os.getenv("UNIFIED_REVIEW_FINDING_EXPLANATION_TIMEOUT", "10")))


class FindingSupplement(BaseModel):
    finding_id: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    judgment: str = Field(min_length=1, max_length=_MAX_TEXT)
    basis: str = Field(min_length=1, max_length=_MAX_TEXT)
    uncertainty: str = Field(min_length=1, max_length=_MAX_TEXT)
    handling: str = Field(min_length=1, max_length=_MAX_TEXT)


def _compact(text: Any, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _payload(snapshot: GraphSnapshot) -> tuple[list[dict[str, Any]], dict[str, set[str]]]:
    evidence_by_id = {item.evidence_id: item for item in snapshot.evidence}
    finding_payload: list[dict[str, Any]] = []
    allowed: dict[str, set[str]] = {}
    for finding in snapshot.findings:
        presentation = finding.metadata.get("presentation", {})
        if not isinstance(presentation, dict):
            presentation = {}
        # LLM prose adds value for ambiguity, not for deterministic passes or
        # confirmed errors whose facts are already rendered by fixed copy.
        if str(finding.status) not in {"unresolved", "unresolved_advisory"}:
            continue
        ids = [item for item in finding.evidence_ids if item in evidence_by_id]
        ids = ids[:_MAX_EVIDENCE_PER_FINDING]
        if not ids:
            continue
        allowed[finding.finding_id] = set(ids)
        finding_payload.append({
            "finding_id": finding.finding_id,
            "check_id": finding.check_id,
            "status": str(finding.status),
            "subject": presentation.get("subject") or finding.title,
            "checked": presentation.get("checked") or "",
            "issue": presentation.get("issue") or finding.description,
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "doc_type": evidence_by_id[evidence_id].doc_type,
                    "page_number": evidence_by_id[evidence_id].page_number,
                    "member_index": evidence_by_id[evidence_id].metadata.get("member_index"),
                    "quote": _compact(evidence_by_id[evidence_id].exact_quote),
                }
                for evidence_id in ids
            ],
        })
        if len(finding_payload) >= _MAX_FINDINGS:
            break
    return finding_payload, allowed


_SYSTEM_PROMPT = """你是检测报告审核系统的证据解释助手。
只根据用户 JSON 中已有的 finding、原文摘录和 evidence_id 生成简短补充说明。
不要修改状态，不要宣称新的事实，不要把候选关系写成已确认关系。
必须输出合法 JSON：{"items":[{"finding_id":"...","evidence_ids":["..."],"judgment":"...","basis":"...","uncertainty":"...","handling":"..."}]}。
每个字段用简短中文，单字段不超过 240 个字符：
judgment=可能的业务解释；basis=依据了哪些已有原文；uncertainty=仍不能确认的部分；handling=系统当前如何处理。
evidence_ids 必须来自该 finding 的输入证据，不能编造或跨 finding 借用。"""


async def enrich_finding_supplements(
    graph_id: str,
    store: EvidenceGraphStore,
    gateway: UnifiedModelGateway,
) -> int:
    """Best-effort batch enrichment; deterministic copy always survives failure."""
    try:
        snapshot = store.get_snapshot(graph_id)
        if snapshot is None:
            return 0
        payload, allowed = _payload(snapshot)
        if not payload:
            return 0
        # Resolve first so an unconfigured deployment does not make a review
        # wait for a failed network call. Test routes intentionally skip this.
        gateway.resolve("finding_explanation")
        batches = [
            payload[index:index + _BATCH_SIZE]
            for index in range(0, len(payload), _BATCH_SIZE)
        ]
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BATCHES)

        async def call_batch(index: int, batch: list[dict[str, Any]]) -> list[Any]:
            try:
                async with semaphore:
                    result = await gateway.call_json(
                        "finding_explanation",
                        system_prompt=_SYSTEM_PROMPT,
                        user_prompt=json.dumps({"findings": batch}, ensure_ascii=False),
                        stage=f"finding-explanation-{index + 1}",
                        timeout=_EXPLANATION_TIMEOUT,
                        max_tokens=1600,
                    )
                raw_items = result.get("items") if isinstance(result, dict) else None
                return raw_items if isinstance(raw_items, list) else []
            except Exception as exc:
                logger.warning(
                    "finding_llm_supplement_batch_failed",
                    graph_id=graph_id,
                    batch_index=index,
                    batch_size=len(batch),
                    error_type=type(exc).__name__,
                )
                return []

        batch_results = await asyncio.gather(*(
            call_batch(index, batch) for index, batch in enumerate(batches)
        ))
        raw_items = [item for batch in batch_results for item in batch]
        updated = 0
        for raw in raw_items:
            try:
                supplement = FindingSupplement.model_validate(raw)
            except ValidationError:
                continue
            permitted = allowed.get(supplement.finding_id, set())
            ids = list(dict.fromkeys(supplement.evidence_ids))
            if not ids or not set(ids).issubset(permitted):
                continue
            presentation = next(
                (
                    finding.metadata.get("presentation")
                    for finding in snapshot.findings
                    if finding.finding_id == supplement.finding_id
                    and isinstance(finding.metadata.get("presentation"), dict)
                ),
                {},
            )
            presentation = dict(presentation)
            presentation["llm_supplement"] = {
                "status": "ready",
                "judgment": supplement.judgment,
                "basis": supplement.basis,
                "uncertainty": supplement.uncertainty,
                "handling": supplement.handling,
                "evidence_ids": ids,
            }
            if store.update_finding_presentation(
                graph_id, supplement.finding_id, presentation,
            ):
                updated += 1
        logger.info(
            "finding_llm_supplements_persisted",
            graph_id=graph_id,
            candidate_count=len(payload),
            batch_count=len(batches),
            updated_count=updated,
        )
        return updated
    except (UnifiedModelUnavailable, ValidationError) as exc:
        logger.info(
            "finding_llm_supplements_skipped",
            graph_id=graph_id,
            reason_code=type(exc).__name__,
        )
        return 0
    except Exception as exc:
        logger.warning(
            "finding_llm_supplements_failed",
            graph_id=graph_id,
            error_type=type(exc).__name__,
        )
        return 0
