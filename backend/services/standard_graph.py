"""Extract a reviewable relational knowledge graph from standard evidence chunks."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import database
from services.deepseek_client import DeepSeekReviewer
from utils.logger import get_logger

logger = get_logger(__name__)

STANDARD_GRAPH_MAX_TOKENS = 6000
STANDARD_GRAPH_CONCURRENCY = 4
STANDARD_GRAPH_UNIT_CHARS = 1200
STANDARD_RECOVERY_MIN_EVIDENCE_COVERAGE = 0.6
_VALID_TYPES = {
    "applicability", "test_method", "test_condition", "parameter_limit",
    "acceptance", "instrument", "reference", "other",
}

STANDARD_GRAPH_PROMPT_VERSION = "standard_graph_bilingual_v3"

_GRAPH_PROMPT = """你是测试标准结构化专家。请从给出的单个标准原文知识块中提取可用于审核的要求。

只允许提取原文明示内容，不使用常识补充。目录、页眉、版权说明、术语定义如果没有形成可执行要求，不要提取。
每一条要求必须同时包含：
1. original_statement：逐字来自输入的完整规范性原文，不翻译、不改写；
2. interpretation_zh：对该原文的忠实中文释义，供非技术审核人员理解；
3. evidence_quote：逐字来自输入、足以反查 original_statement 的证据片段。
如果输入本身是中文，original_statement 保持中文，interpretation_zh 可以忠实复述。较长句子应拆成适用条件、方法、条件、参数限值、判定规则等独立要求。
参数中的数字、比较符和单位必须保持原文，不换算、不推断。标准或条款引用放入 relations。

严格输出以下 JSON 对象，即使没有要求也必须输出 {"clauses": []}：
{
  "clauses": [
    {
      "clause_number": "5.6.4",
      "title": "Test pulse 4",
      "parent_clause_number": "5.6",
      "page_start": 20,
      "page_end": 20,
      "requirements": [
        {
          "requirement_type": "applicability|test_method|test_condition|parameter_limit|acceptance|instrument|reference|other",
          "test_item": "原文中的测试项目名称；没有则为空",
          "original_statement": "The pulse amplitude shall be -6 V.",
          "interpretation_zh": "脉冲幅值应为 -6 V。",
          "applicability": "该要求的适用前提；没有则为空",
          "evidence_quote": "逐字原文证据",
          "page_start": 20,
          "page_end": 20,
          "confidence": 0.95,
          "parameters": [
            {"name":"脉冲幅值","symbol":"Ua","comparator":"=","value":"-6","value_min":"","value_max":"","unit":"V","raw_text":"Ua = -6 V"}
          ],
          "relations": [
            {"relation_type":"references|applies_to|depends_on|replaces","target_ref":"ISO 7637-3 或条款号"}
          ]
        }
      ]
    }
  ]
}

输入知识块：
"""


def _normalize_evidence(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "", value or "").lower()


_NORMATIVE_CUE_RE = re.compile(
    r"\b(shall|must|required|necessary|applicable|indispensable|is to be|are to be)\b|"
    r"(应当|必须|适用于|不得|需要|要求)", re.IGNORECASE,
)


def has_normative_cues(content: str) -> bool:
    return bool(_NORMATIVE_CUE_RE.search(content or ""))


def build_normative_fallback(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """Expose uncovered normative sentences for mandatory human classification."""
    candidates = []
    for sentence in re.split(r"(?<=[.;。；])\s+|\n+", str(chunk.get("content") or "")):
        sentence = sentence.strip()
        if len(sentence) < 12 or not has_normative_cues(sentence):
            continue
        candidates.append({
            "requirement_type": "other", "test_item": "",
            "statement": "", "original_statement": sentence,
            "interpretation_zh": "", "applicability": "", "evidence_quote": sentence,
            "page_start": int(chunk.get("page_start") or 0),
            "page_end": int(chunk.get("page_end") or chunk.get("page_start") or 0),
            "confidence": 0.1, "parameters": [], "relations": [],
        })
    if not candidates:
        return []
    return [{
        "clause_number": str(chunk.get("clause") or ""),
        "title": "需人工判断的规范性内容", "parent_clause_number": "",
        "page_start": int(chunk.get("page_start") or 0),
        "page_end": int(chunk.get("page_end") or chunk.get("page_start") or 0),
        "source_chunk_id": str(chunk.get("id") or ""), "requirements": candidates,
    }]


def build_manual_recovery_fallback(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """Preserve a failed recovery window as mandatory human-review evidence."""
    content = str(chunk.get("content") or "").strip()
    if not content:
        return []
    page_start = int(chunk.get("page_start") or 0)
    page_end = int(chunk.get("page_end") or page_start)
    return [{
        "clause_number": str(chunk.get("clause") or ""),
        "title": "模型转录失败，需人工确认的原文窗口",
        "parent_clause_number": "", "page_start": page_start,
        "page_end": page_end, "source_chunk_id": str(chunk.get("id") or ""),
        "requirements": [{
            "requirement_type": "other", "test_item": "",
            "statement": "", "original_statement": content,
            "interpretation_zh": "", "applicability": "",
            "evidence_quote": content, "page_start": page_start,
            "page_end": page_end, "confidence": 0.0,
            "parameters": [], "relations": [],
        }],
    }]


def recovery_evidence_coverage(content: str, clauses: list[dict[str, Any]]) -> float:
    """Estimate how much normalized source text is represented by evidence quotes."""
    normalized = _normalize_evidence(content)
    if not normalized:
        return 1.0
    covered = [False] * len(normalized)
    for clause in clauses:
        for requirement in clause.get("requirements") or []:
            quote = _normalize_evidence(str(requirement.get("evidence_quote") or ""))
            if not quote:
                continue
            start = normalized.find(quote)
            if start < 0:
                continue
            for index in range(start, min(len(normalized), start + len(quote))):
                covered[index] = True
    return sum(covered) / len(covered)


def needs_manual_table_coverage_fallback(
    content: str, clauses: list[dict[str, Any]],
) -> tuple[bool, float]:
    """Flag parameter-heavy table windows whose extracted evidence is sparse."""
    table_like = bool(re.search(r"\bTable\s+[A-Z]?\.?\d+\b", content, re.IGNORECASE))
    numeric_tokens = re.findall(r"(?<![A-Za-z])[-+±]?[0-9]+(?:[,.][0-9]+)?", content)
    coverage = recovery_evidence_coverage(content, clauses)
    return (
        table_like and len(numeric_tokens) >= 6
        and coverage < STANDARD_RECOVERY_MIN_EVIDENCE_COVERAGE,
        coverage,
    )


def build_extraction_units(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split retrieval chunks into page-bounded units while retaining source IDs."""
    units: list[dict[str, Any]] = []
    marker = re.compile(r"(?=\[第\s+\d+\s+页\s*\|)")
    for chunk in chunks:
        blocks = [part.strip() for part in marker.split(str(chunk.get("content") or "")) if part.strip()]
        if not blocks:
            blocks = [str(chunk.get("content") or "")]
        for block in blocks:
            page_match = re.match(r"\[第\s+(\d+)\s+页", block)
            page = int(page_match.group(1)) if page_match else int(chunk.get("page_start") or 0)
            front_matter = bool(re.search(
                r"\bContents\s+Page\b|\bForeword\b|COPYRIGHT PROTECTED DOCUMENT|PDF disclaimer",
                block, re.IGNORECASE,
            )) and not bool(re.search(r"(?m)^\s*1\s+Scope\b", block))
            raw_paragraphs = [part.strip() for part in re.split(r"\n\s*\n", block) if part.strip()]
            paragraphs: list[str] = []
            for paragraph in raw_paragraphs:
                if len(paragraph) <= STANDARD_GRAPH_UNIT_CHARS:
                    paragraphs.append(paragraph)
                    continue
                sentences = [part.strip() for part in re.split(r"(?<=[.;:。；：])\s+", paragraph) if part.strip()]
                if len(sentences) == 1:
                    paragraphs.extend(
                        paragraph[start:start + STANDARD_GRAPH_UNIT_CHARS]
                        for start in range(0, len(paragraph), STANDARD_GRAPH_UNIT_CHARS)
                    )
                else:
                    paragraphs.extend(sentences)
            windows: list[str] = []
            current: list[str] = []
            for paragraph in paragraphs:
                if current and len("\n\n".join(current + [paragraph])) > STANDARD_GRAPH_UNIT_CHARS:
                    windows.append("\n\n".join(current))
                    current = [current[-1], paragraph] if len(current[-1]) < 500 else [paragraph]
                else:
                    current.append(paragraph)
            if current:
                windows.append("\n\n".join(current))
            for index, content in enumerate(windows):
                units.append({**chunk, "content": content, "page_start": page, "page_end": page,
                              "unit_id": f"{chunk['id']}:p{page}:w{index + 1}",
                              "skip_graph": front_matter})
    return units


def split_failed_extraction_unit(unit: dict[str, Any], max_chars: int = 600) -> list[dict[str, Any]]:
    """Split a repeatedly failing extraction unit into page-stable recovery units."""
    lines = [line.strip() for line in str(unit.get("content") or "").splitlines() if line.strip()]
    parts: list[str] = []
    current: list[str] = []
    for line in lines:
        if current and len("\n".join(current + [line])) > max_chars:
            parts.append("\n".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        parts.append("\n".join(current))
    return [
        {**unit, "content": content, "unit_id": f"{unit.get('unit_id')}:recovery{index + 1}"}
        for index, content in enumerate(parts) if content.strip()
    ]


def validate_graph_chunk(chunk: dict[str, Any], result: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """Return accepted clauses and rejected requirement diagnostics."""
    content_norm = _normalize_evidence(str(chunk.get("content") or ""))
    accepted: list[dict] = []
    rejected: list[dict] = []
    clauses = result.get("clauses")
    if not isinstance(clauses, list):
        raise ValueError("LLM JSON 缺少 clauses 数组")
    chunk_start = int(chunk.get("page_start") or 0)
    chunk_end = int(chunk.get("page_end") or chunk_start)
    for clause in clauses:
        if not isinstance(clause, dict):
            continue
        valid_requirements = []
        for requirement in clause.get("requirements") or []:
            if not isinstance(requirement, dict):
                continue
            original_statement = str(
                requirement.get("original_statement") or requirement.get("evidence_quote") or ""
            ).strip()
            interpretation_zh = str(
                requirement.get("interpretation_zh") or requirement.get("statement") or ""
            ).strip()
            quote = str(requirement.get("evidence_quote") or "").strip()
            quote_norm = _normalize_evidence(quote)
            original_norm = _normalize_evidence(original_statement)
            reasons = []
            if not interpretation_zh:
                reasons.append("empty_interpretation_zh")
            elif not re.search(r"[\u4e00-\u9fff]", interpretation_zh):
                reasons.append("interpretation_not_chinese")
            if len(original_norm) < 8:
                reasons.append("original_statement_too_short")
            elif original_norm not in content_norm:
                reasons.append("original_statement_not_found")
            if len(quote_norm) < 8:
                reasons.append("evidence_too_short")
            elif quote_norm not in content_norm:
                reasons.append("evidence_not_found")
            page_start = int(requirement.get("page_start") or clause.get("page_start") or chunk_start)
            page_end = int(requirement.get("page_end") or clause.get("page_end") or page_start)
            if page_start < chunk_start or page_end > chunk_end or page_end < page_start:
                logger.warning(
                    "standard_graph_page_corrected", unit_id=chunk.get("unit_id"),
                    model_page_start=page_start, model_page_end=page_end,
                    evidence_page_start=chunk_start, evidence_page_end=chunk_end,
                )
                page_start, page_end = chunk_start, chunk_end
            if reasons:
                rejected.append({"statement": interpretation_zh[:200], "reasons": reasons})
                continue
            requirement_type = str(requirement.get("requirement_type") or "other")
            if requirement_type not in _VALID_TYPES:
                requirement_type = "other"
            valid_requirements.append({
                "requirement_type": requirement_type,
                "test_item": str(requirement.get("test_item") or "").strip(),
                "statement": interpretation_zh,
                "original_statement": original_statement,
                "interpretation_zh": interpretation_zh,
                "applicability": str(requirement.get("applicability") or "").strip(),
                "evidence_quote": quote,
                "page_start": page_start,
                "page_end": page_end,
                "confidence": max(0.0, min(1.0, float(requirement.get("confidence") or 0))),
                "parameters": [p for p in (requirement.get("parameters") or []) if isinstance(p, dict)],
                "relations": [r for r in (requirement.get("relations") or []) if isinstance(r, dict)],
            })
        if valid_requirements:
            accepted.append({
                "clause_number": str(clause.get("clause_number") or chunk.get("clause") or "").strip(),
                "title": str(clause.get("title") or chunk.get("title") or "").strip(),
                "parent_clause_number": str(clause.get("parent_clause_number") or "").strip(),
                "page_start": max(chunk_start, int(clause.get("page_start") or chunk_start)),
                "page_end": min(chunk_end, int(clause.get("page_end") or chunk_end)),
                "source_chunk_id": str(chunk.get("id") or ""),
                "requirements": valid_requirements,
            })
    return accepted, rejected


async def _extract_chunk(
    reviewer: DeepSeekReviewer, chunk: dict[str, Any], *, max_retries: int = 1,
    timeout: int = 180, allow_completeness_retry: bool = True,
) -> dict[str, Any]:
    result = await reviewer._call_api(
        prompt=f"输入知识块：\n{chunk['content']}",
        stage="standard_graph",
        timeout=timeout,
        system_prompt=_GRAPH_PROMPT,
        max_tokens=STANDARD_GRAPH_MAX_TOKENS,
        # This is evidence-bounded JSON extraction. Thinking mode was consuming
        # the output budget and producing minutes-long empty responses; human
        # review and deterministic evidence gates remain authoritative.
        thinking=False,
        task_kind="knowledge_reasoning",
        max_retries=max_retries,
    )
    if not isinstance(result, dict) or "clauses" not in result:
        raise ValueError("LLM 未返回完整的 clauses JSON")
    accepted, rejected = validate_graph_chunk(chunk, result)
    if not accepted and has_normative_cues(chunk["content"]) and allow_completeness_retry:
        logger.warning("standard_graph_empty_with_normative_cues", standard_id=chunk.get("standard_id"),
                       unit_id=chunk.get("unit_id"))
        result = await reviewer._call_api(
            prompt=(
                f"输入知识块：\n{chunk['content']}\n\n"
                "复核提示：上一次返回了空数组，但代码检测到规范性措辞。请重点检查适用范围、"
                "shall/must/required/necessary/applicable/indispensable 句、测试条件和报告义务。"
                "不要遗漏，仍必须逐条提供输入中能反查的 evidence_quote。"
            ),
            stage="standard_graph_completeness_retry", timeout=timeout,
            system_prompt=_GRAPH_PROMPT,
            max_tokens=STANDARD_GRAPH_MAX_TOKENS,
            thinking=False,
            task_kind="knowledge_reasoning",
            max_retries=max_retries,
        )
        if not isinstance(result, dict) or "clauses" not in result:
            raise ValueError("规范性措辞复核未返回完整 JSON")
        accepted, second_rejected = validate_graph_chunk(chunk, result)
        rejected.extend(second_rejected)
    if not accepted and has_normative_cues(chunk["content"]):
        accepted = build_normative_fallback(chunk)
        logger.warning(
            "standard_graph_normative_fallback_created",
            standard_id=chunk.get("standard_id"), unit_id=chunk.get("unit_id"),
            candidate_count=sum(len(clause["requirements"]) for clause in accepted),
        )
        if not accepted:
            raise ValueError("normative_cues_uncovered")
    logger.info(
        "standard_graph_chunk_validated", standard_id=chunk.get("standard_id"),
        chunk_id=chunk.get("id"), unit_id=chunk.get("unit_id"),
        accepted_requirements=sum(len(c["requirements"]) for c in accepted),
        rejected_requirements=len(rejected), rejected_reasons=[r["reasons"] for r in rejected[:20]],
    )
    return {"chunk_id": chunk["id"], "unit_id": chunk.get("unit_id", chunk["id"]),
            "clauses": accepted, "rejected": rejected}


async def build_standard_graph(std_id: str) -> dict[str, Any]:
    started = time.time()
    standard = await database.get_standard_async(std_id)
    if not standard:
        raise ValueError("标准不存在")
    if standard.knowledge_status != "ready":
        raise ValueError("标准原文知识化尚未完成")
    chunks = await database.get_standard_knowledge_chunks_async(std_id, limit=1000)
    if not chunks:
        raise ValueError("标准没有可用原文知识块")
    all_units = build_extraction_units(chunks)
    units = [unit for unit in all_units if not unit.get("skip_graph")]
    await database.update_standard_graph_status_async(std_id, "extracting")
    logger.info("standard_graph_build_started", standard_id=std_id, chunk_count=len(chunks),
                extraction_unit_count=len(units), skipped_front_matter_units=len(all_units) - len(units))
    reviewer = DeepSeekReviewer()
    semaphore = asyncio.Semaphore(STANDARD_GRAPH_CONCURRENCY)
    progress_lock = asyncio.Lock()
    processed_units = 0
    failed_units = 0
    manual_fallback_unit_ids: list[str] = []

    async def run(chunk: dict[str, Any]) -> dict[str, Any]:
        nonlocal processed_units, failed_units
        async with semaphore:
            try:
                result = await _extract_chunk(reviewer, chunk)
                needs_fallback, coverage = needs_manual_table_coverage_fallback(
                    str(chunk.get("content") or ""), result["clauses"],
                )
                if needs_fallback:
                    fallback = build_manual_recovery_fallback(chunk)
                    if fallback:
                        result["clauses"].extend(fallback)
                        manual_fallback_unit_ids.append(str(chunk.get("unit_id") or ""))
                        logger.warning(
                            "standard_graph_table_low_coverage_fallback_created",
                            standard_id=std_id, unit_id=chunk.get("unit_id"),
                            evidence_coverage=round(coverage, 4),
                        )
            except Exception as exc:
                logger.error("standard_graph_chunk_failed", standard_id=std_id,
                             chunk_id=chunk.get("id"), unit_id=chunk.get("unit_id"), error=str(exc)[:500])
                result = {"chunk_id": chunk.get("id"), "unit_id": chunk.get("unit_id"),
                          "clauses": [], "rejected": [], "error": str(exc)}
            async with progress_lock:
                processed_units += 1
                if result.get("error"):
                    failed_units += 1
                await database.update_standard_graph_status_async(std_id, "extracting", "", {
                    "source_chunk_count": len(chunks), "extraction_unit_count": len(units),
                    "processed_unit_count": processed_units, "failed_unit_count": failed_units,
                    "skipped_front_matter_units": len(all_units) - len(units),
                })
            return result

    results = await asyncio.gather(*(run(unit) for unit in units))
    raw_clauses = [clause for result in results for clause in result["clauses"]]
    clauses: list[dict[str, Any]] = []
    seen_requirements: set[tuple[str, str]] = set()
    for clause in raw_clauses:
        unique_requirements = []
        for requirement in clause["requirements"]:
            key = (str(clause.get("clause_number") or ""), _normalize_evidence(requirement["evidence_quote"]))
            if key in seen_requirements:
                continue
            seen_requirements.add(key)
            unique_requirements.append(requirement)
        if unique_requirements:
            clauses.append({**clause, "requirements": unique_requirements})
    failed_ids = [str(result["unit_id"]) for result in results if result.get("error")]
    rejected_count = sum(len(result["rejected"]) for result in results)
    requirement_count = sum(len(clause["requirements"]) for clause in clauses)
    meta = {
        "source_chunk_count": len(chunks),
        "extraction_unit_count": len(units),
        "skipped_front_matter_units": len(all_units) - len(units),
        "processed_unit_count": len(units) - len(failed_ids),
        "failed_unit_ids": failed_ids,
        "manual_fallback_unit_ids": manual_fallback_unit_ids,
        "evidence_rejected_count": rejected_count,
        "duration_ms": round((time.time() - started) * 1000),
        "extractor": "deepseek_json_page_units_with_evidence_validation_v3",
        "prompt_version": STANDARD_GRAPH_PROMPT_VERSION,
    }
    if not clauses or not requirement_count:
        error = "未提取到可核对的结构化要求"
        await database.update_standard_graph_status_async(std_id, "failed", error, meta)
        logger.error("standard_graph_build_failed", standard_id=std_id, error=error, **meta)
        raise ValueError(error)
    counts = await database.replace_standard_graph_async(std_id, clauses, meta)
    if failed_ids:
        await database.update_standard_graph_status_async(
            std_id, "pending_review", f"{len(failed_ids)} 个原文单元提取失败，发布前必须重新提取", meta,
        )
    logger.info("standard_graph_build_completed", standard_id=std_id, **counts, **meta)
    return {**counts, **meta, "graph_status": "pending_review"}


async def retry_failed_standard_graph_units(std_id: str) -> dict[str, Any]:
    """Recover failed units in small windows and checkpoint every source unit.

    A unit has already exhausted the normal extraction path, so retrying the
    whole unit four more times only amplifies tail latency. Recovery starts with
    smaller page-stable windows, uses a bounded per-call retry budget, and saves
    partial successes before moving to the next failed source unit.
    """
    standard = await database.get_standard_async(std_id)
    if not standard:
        raise ValueError("标准不存在")
    failed_ids = [str(item) for item in (standard.graph_meta or {}).get("failed_unit_ids", [])]
    if not failed_ids:
        raise ValueError("没有需要重试的失败原文单元")
    chunks = await database.get_standard_knowledge_chunks_async(std_id, limit=1000)
    units_by_id = {str(unit.get("unit_id")): unit for unit in build_extraction_units(chunks)}
    missing_ids = [unit_id for unit_id in failed_ids if unit_id not in units_by_id]
    if missing_ids:
        raise ValueError(f"{len(missing_ids)} 个失败单元已无法从当前原文恢复，请重新识别整份标准")

    await database.update_standard_graph_status_async(std_id, "extracting", "", standard.graph_meta)
    reviewer = DeepSeekReviewer()
    retry_count = int((standard.graph_meta or {}).get("failed_unit_retry_count") or 0) + 1
    remaining_ids = list(failed_ids)
    manual_fallback_unit_ids = list(
        (standard.graph_meta or {}).get("manual_fallback_unit_ids", [])
    )
    recovered_requirement_count = 0
    recovered_clause_count = 0

    for unit_index, unit_id in enumerate(failed_ids):
        unit = units_by_id[unit_id]
        unit["standard_id"] = std_id
        recovery_units = split_failed_extraction_unit(unit)
        logger.info(
            "standard_graph_failed_unit_recovery_started", standard_id=std_id,
            unit_id=unit_id, recovery_unit_count=len(recovery_units),
            retry_count=retry_count,
        )
        recovered_results: list[dict[str, Any]] = []
        recovery_errors: list[str] = []
        for recovery_unit in recovery_units:
            try:
                result = await _extract_chunk(
                    reviewer, recovery_unit, max_retries=0, timeout=60,
                    allow_completeness_retry=False,
                )
                recovered_results.append(result)
                coverage = recovery_evidence_coverage(
                    str(recovery_unit.get("content") or ""), result["clauses"],
                )
                if coverage < STANDARD_RECOVERY_MIN_EVIDENCE_COVERAGE:
                    fallback = build_manual_recovery_fallback(recovery_unit)
                    if fallback:
                        recovered_results.append({
                            "unit_id": recovery_unit.get("unit_id"),
                            "clauses": fallback, "rejected": [],
                        })
                        fallback_id = str(recovery_unit.get("unit_id") or "")
                        if fallback_id and fallback_id not in manual_fallback_unit_ids:
                            manual_fallback_unit_ids.append(fallback_id)
                        logger.warning(
                            "standard_graph_recovery_low_coverage_fallback_created",
                            standard_id=std_id, unit_id=fallback_id,
                            evidence_coverage=round(coverage, 4),
                        )
            except Exception as recovery_exc:
                recovery_error = str(recovery_exc)
                fallback = build_manual_recovery_fallback(recovery_unit)
                if fallback:
                    recovered_results.append({
                        "unit_id": recovery_unit.get("unit_id"),
                        "clauses": fallback, "rejected": [],
                    })
                    fallback_id = str(recovery_unit.get("unit_id") or "")
                    if fallback_id and fallback_id not in manual_fallback_unit_ids:
                        manual_fallback_unit_ids.append(fallback_id)
                    logger.warning(
                        "standard_graph_recovery_manual_fallback_created",
                        standard_id=std_id, unit_id=fallback_id,
                        error=recovery_error[:500],
                    )
                else:
                    recovery_errors.append(recovery_error)
                    logger.error(
                        "standard_graph_recovery_unit_failed", standard_id=std_id,
                        unit_id=recovery_unit.get("unit_id"), error=recovery_error[:500],
                    )

        graph = await database.get_standard_graph_async(std_id)
        existing_keys = {
            (str(item.get("clause_number") or ""),
             _normalize_evidence(str(item.get("evidence_quote") or "")))
            for item in graph.get("requirements", [])
        }
        recovered_clauses: list[dict[str, Any]] = []
        for result in recovered_results:
            for clause in result["clauses"]:
                unique = []
                for requirement in clause.get("requirements", []):
                    key = (str(clause.get("clause_number") or ""),
                           _normalize_evidence(requirement.get("evidence_quote") or ""))
                    if key not in existing_keys:
                        existing_keys.add(key)
                        unique.append(requirement)
                if unique:
                    recovered_clauses.append({**clause, "requirements": unique})

        if not recovery_errors:
            remaining_ids.remove(unit_id)
        meta = dict(standard.graph_meta or {})
        meta["failed_unit_ids"] = list(remaining_ids)
        total = int(meta.get("extraction_unit_count") or 0)
        meta["processed_unit_count"] = max(0, total - len(remaining_ids))
        meta["failed_unit_retry_count"] = retry_count
        meta["manual_fallback_unit_ids"] = list(manual_fallback_unit_ids)
        meta["graph_error"] = (
            f"{len(remaining_ids)} 个原文单元提取失败，发布前必须重试"
            if remaining_ids else ""
        )
        counts = await database.append_standard_graph_async(std_id, recovered_clauses, meta)
        recovered_clause_count += counts["clause_count"]
        recovered_requirement_count += counts["requirement_count"]
        logger.info(
            "standard_graph_failed_unit_checkpointed", standard_id=std_id,
            unit_id=unit_id, recovered_clause_count=counts["clause_count"],
            recovered_requirement_count=counts["requirement_count"],
            recovery_error_count=len(recovery_errors), remaining=len(remaining_ids),
        )
        if unit_index + 1 < len(failed_ids):
            await database.update_standard_graph_status_async(std_id, "extracting", "", meta)

    final_meta = dict(standard.graph_meta or {})
    final_meta["failed_unit_ids"] = list(remaining_ids)
    total = int(final_meta.get("extraction_unit_count") or 0)
    final_meta["processed_unit_count"] = max(0, total - len(remaining_ids))
    final_meta["failed_unit_retry_count"] = retry_count
    final_meta["manual_fallback_unit_ids"] = list(manual_fallback_unit_ids)
    final_meta["graph_error"] = (
        f"{len(remaining_ids)} 个原文单元提取失败，发布前必须重试"
        if remaining_ids else ""
    )
    await database.update_standard_graph_status_async(
        std_id, "pending_review", final_meta["graph_error"], final_meta,
    )
    logger.info(
        "standard_graph_failed_units_retried", standard_id=std_id,
        requested=len(failed_ids), remaining=len(remaining_ids),
        clause_count=recovered_clause_count,
        requirement_count=recovered_requirement_count,
    )
    return {
        "clause_count": recovered_clause_count,
        "requirement_count": recovered_requirement_count,
        "retried_unit_count": len(failed_ids),
        "remaining_failed_unit_ids": remaining_ids,
        "graph_status": "pending_review",
    }


async def retry_failed_standard_graph_units_background(std_id: str) -> None:
    """Background wrapper that always leaves a retryable persisted state."""
    try:
        await retry_failed_standard_graph_units(std_id)
    except asyncio.CancelledError:
        standard = await database.get_standard_async(std_id)
        meta = dict((standard.graph_meta if standard else {}) or {})
        await database.update_standard_graph_status_async(
            std_id, "pending_review", "失败单元恢复任务已取消，可重新发起", meta,
        )
        logger.warning("standard_graph_failed_units_retry_cancelled", standard_id=std_id)
        raise
    except Exception as exc:
        standard = await database.get_standard_async(std_id)
        meta = dict((standard.graph_meta if standard else {}) or {})
        error = f"失败单元恢复异常：{str(exc)[:400]}"
        await database.update_standard_graph_status_async(
            std_id, "pending_review", error, meta,
        )
        logger.error(
            "standard_graph_failed_units_retry_failed", standard_id=std_id,
            error=str(exc)[:500],
        )


async def build_standard_graph_background(std_id: str) -> None:
    try:
        await build_standard_graph(std_id)
    except Exception:
        return
