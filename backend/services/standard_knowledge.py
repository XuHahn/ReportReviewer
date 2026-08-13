"""Reusable test-standard knowledge base ingestion and retrieval.

Standards are uploaded once in the standard library. The ingestion path keeps
page-level evidence, uses Qwen vision only for pages without usable native text,
and stores searchable chunks. Review-time retrieval selects relevant chunks
module and hands those excerpts to the LLM with stable evidence identifiers.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pypdf
import mammoth
import fitz
from pydantic import BaseModel, Field, ValidationError

import database
from services.deepseek_client import DeepSeekReviewer
from services.unified_model_gateway import UnifiedModelGateway
from utils.logger import get_logger

logger = get_logger(__name__)

STANDARD_NATIVE_TEXT_THRESHOLD = int(os.getenv("STANDARD_NATIVE_TEXT_THRESHOLD", "80"))
STANDARD_CHUNK_CHARS = int(os.getenv("STANDARD_CHUNK_CHARS", "4500"))
STANDARD_RETRIEVAL_LIMIT = int(os.getenv("STANDARD_RETRIEVAL_LIMIT", "6"))
STANDARD_CONTEXT_MAX_CHARS = int(os.getenv("STANDARD_CONTEXT_MAX_CHARS", "30000"))
STANDARD_CONTEXT_CHUNK_CHARS = int(os.getenv("STANDARD_CONTEXT_CHUNK_CHARS", "7000"))
STANDARD_LLM_CANDIDATE_LIMIT = int(os.getenv("STANDARD_LLM_CANDIDATE_LIMIT", "40"))
STANDARD_VECTOR_MIN_SCORE = float(os.getenv("STANDARD_VECTOR_MIN_SCORE", "0.34"))
STANDARD_VISION_CONCURRENCY = max(1, min(
    4, int(os.getenv("STANDARD_VISION_CONCURRENCY", "2")),
))
STANDARD_BLANK_PAGE_DARK_PIXEL_RATIO = float(
    os.getenv("STANDARD_BLANK_PAGE_DARK_PIXEL_RATIO", "0.001")
)


@dataclass
class StandardPage:
    number: int
    text: str
    extraction: str = "text"


class StandardVisualPageResult(BaseModel):
    page_number: int = Field(ge=1)
    text: str
    coverage_state: str
    failed_regions: list[str] = Field(default_factory=list)


_STANDARD_VISUAL_PROMPT = """你是测试标准页面的逐字转录员。请完整转录页面中可见的中英文、编号、表格、符号和单位，保持阅读顺序，不翻译、不解释、不总结。无法辨认的局部不要猜测，并写入 failed_regions。
只返回完整 JSON：{"page_number":1,"text":"页面逐字文本","coverage_state":"complete|partial|failed","failed_regions":[]}。page_number 必须与输入一致。"""


def _render_standard_page_data_urls(file_bytes: bytes, page_numbers: set[int]) -> dict[int, str]:
    rendered: dict[int, str] = {}
    scale = float(os.getenv("STANDARD_VISION_RENDER_SCALE", "2.0"))
    if not 1.0 <= scale <= 4.0:
        raise ValueError("STANDARD_VISION_RENDER_SCALE 必须在 1.0 到 4.0 之间")
    with fitz.open(stream=file_bytes, filetype="pdf") as document:
        for page_number in sorted(page_numbers):
            page = document[page_number - 1]
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            png = pixmap.tobytes("png")
            rendered[page_number] = (
                "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            )
    return rendered


def _find_visually_blank_pdf_pages(file_bytes: bytes, page_numbers: set[int]) -> set[int]:
    """Return sparse PDF pages that contain no meaningful visible marks.

    Native text extraction cannot distinguish a scanned page from an intentional
    blank page.  A cheap grayscale render avoids sending truly blank pages to the
    vision model while retaining scanned pages with even a small amount of ink.
    """
    if not page_numbers:
        return set()
    if not 0 <= STANDARD_BLANK_PAGE_DARK_PIXEL_RATIO <= 0.05:
        raise ValueError("STANDARD_BLANK_PAGE_DARK_PIXEL_RATIO 必须在 0 到 0.05 之间")
    blank: set[int] = set()
    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as document:
            for page_number in sorted(page_numbers):
                page = document[page_number - 1]
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY, alpha=False,
                )
                samples = pixmap.samples
                if not samples:
                    blank.add(page_number)
                    continue
                dark_pixels = sum(value < 245 for value in samples)
                if dark_pixels / len(samples) <= STANDARD_BLANK_PAGE_DARK_PIXEL_RATIO:
                    blank.add(page_number)
    except Exception as exc:
        # Rendering failure must not turn a potentially meaningful sparse page
        # into a blank page; the normal visual-transcription path remains safer.
        logger.warning(
            "standard_blank_page_detection_failed", error_type=type(exc).__name__,
        )
        return set()
    return blank


async def recover_standard_visual_pages(
    file_bytes: bytes,
    pages: list[StandardPage],
    gateway: UnifiedModelGateway | None = None,
) -> list[StandardPage]:
    """Transcribe only PDF pages whose native text is too sparse to be reliable."""
    sparse_targets = {
        page.number for page in pages
        if len(page.text.strip()) < STANDARD_NATIVE_TEXT_THRESHOLD
    }
    blank_pages = await asyncio.to_thread(
        _find_visually_blank_pdf_pages, file_bytes, sparse_targets,
    )
    if blank_pages:
        logger.info(
            "standard_blank_pages_skipped",
            page_count=len(blank_pages), pages=sorted(blank_pages),
        )
    targets = sparse_targets - blank_pages
    if not targets:
        return [
            StandardPage(page.number, "", "blank") if page.number in blank_pages else page
            for page in pages
        ]
    rendered = await asyncio.to_thread(_render_standard_page_data_urls, file_bytes, targets)
    model_gateway = gateway or UnifiedModelGateway()
    semaphore = asyncio.Semaphore(STANDARD_VISION_CONCURRENCY)

    async def recover(page: StandardPage) -> StandardPage:
        if page.number in blank_pages:
            return StandardPage(page.number, "", "blank")
        if page.number not in targets:
            return page
        content = [
            {"type": "text", "text": json.dumps({
                "page_number": page.number,
                "native_text": page.text,
                "instruction": "转录图像中的全部可见正文与表格",
            }, ensure_ascii=False)},
            {"type": "image_url", "image_url": {"url": rendered[page.number]}},
        ]
        async with semaphore:
            raw = await model_gateway.call_json(
                "visual_extraction",
                system_prompt=_STANDARD_VISUAL_PROMPT,
                user_prompt=content,
                stage="standard_visual_transcription",
                timeout=240,
                max_tokens=12000,
            )
        try:
            result = StandardVisualPageResult.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(
                f"标准第 {page.number} 页千问转录结构校验失败: {exc.error_count()}项"
            ) from exc
        if result.page_number != page.number:
            raise ValueError(f"标准第 {page.number} 页千问返回了错误页码")
        if result.coverage_state != "complete" or result.failed_regions:
            raise ValueError(f"标准第 {page.number} 页千问转录未完整覆盖")
        text = result.text.strip()
        if not text:
            raise ValueError(f"标准第 {page.number} 页千问转录为空")
        logger.info(
            "standard_visual_page_recovered",
            page=page.number,
            native_text_length=len(page.text),
            visual_text_length=len(text),
        )
        return StandardPage(page.number, text, "qwen_vision")

    return list(await asyncio.gather(*(recover(page) for page in pages)))


def extract_standard_pages(file_bytes: bytes, filename: str) -> list[StandardPage]:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes), strict=False)
            return [
                StandardPage(index + 1, (page.extract_text() or "").strip(), "text")
                for index, page in enumerate(reader.pages)
            ]
        except Exception as exc:
            # Scanner/printer generated PDFs occasionally contain a damaged EOF
            # marker or trailing binary data.  PyMuPDF can still enumerate and
            # render those pages, which is sufficient for the targeted Qwen
            # visual transcription path below.  Preserve page boundaries and
            # leave text empty so every page is recovered visually.
            try:
                with fitz.open(stream=file_bytes, filetype="pdf") as document:
                    page_count = len(document)
            except Exception:
                raise exc
            if page_count <= 0:
                raise exc
            logger.warning(
                "standard_pdf_native_extract_failed_visual_fallback",
                filename=filename,
                page_count=page_count,
                error_type=type(exc).__name__,
            )
            return [
                StandardPage(index + 1, "", "native_failed")
                for index in range(page_count)
            ]
    if lower.endswith(".docx"):
        text = mammoth.extract_raw_text(io.BytesIO(file_bytes)).value.strip()
        return [StandardPage(1, text, "text")]
    raise ValueError(f"标准库仅支持 PDF / DOCX: {filename}")


def _infer_metadata(filename: str, text: str) -> dict[str, str]:
    stem = Path(filename).stem.strip()
    combined = f"{stem}\n{text[:8000]}"
    patterns = [
        r"\b(ISO\s*\d+(?:-\d+){0,3})\s*[:\- ]\s*((?:19|20)\d{2})\b",
        r"\b(IEC\s*\d+(?:-\d+){0,3})\s*[:\- ]\s*((?:19|20)\d{2})\b",
        r"\b(CISPR\s*\d+(?:-\d+)?)\s*[:\- ]\s*((?:19|20)\d{2})\b",
        r"\b(GB(?:/T)?\s*\d+(?:\.\d+)?)\s*[-: ]\s*((?:19|20)\d{2})\b",
    ]
    code = ""
    version = ""
    for pattern in patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            code = re.sub(r"\s+", " ", match.group(1).upper()).strip()
            version = match.group(2)
            break
    if not code:
        match = re.search(r"\b(ISO|IEC|CISPR|GB(?:/T)?|EN|SAE)\s*[A-Z0-9.-]+", combined, re.IGNORECASE)
        if match:
            code = re.sub(r"\s+", " ", match.group(0).upper()).strip(" -")
    organization = code.split(" ", 1)[0].replace("/T", "") if code else ""
    return {
        "code": code or stem,
        "version": version,
        "title": stem,
        "organization": organization,
        "category": "",
    }


_HEADING_RE = re.compile(
    r"^\s*((?:\d+(?:\.\d+){0,4})|(?:Annex|Appendix)\s+[A-Z])\s+(.{2,160})$",
    re.IGNORECASE,
)


def build_standard_chunks(pages: list[StandardPage]) -> list[dict[str, Any]]:
    """Build bounded page-aware chunks without separating a page from its evidence."""
    chunks: list[dict[str, Any]] = []
    buffer: list[str] = []
    page_start = 0
    page_end = 0

    def flush() -> None:
        nonlocal buffer, page_start, page_end
        content = "\n\n".join(part for part in buffer if part.strip()).strip()
        if not content:
            buffer = []
            return
        clause = ""
        title = ""
        for line in content.splitlines()[:30]:
            match = _HEADING_RE.match(line.strip())
            if match:
                clause, title = match.group(1), match.group(2).strip()
                break
        if not title:
            title = next(
                (line.strip() for line in content.splitlines()
                 if len(line.strip()) > 3 and not line.strip().startswith("[第 ")),
                "标准原文",
            )[:160]
        chunks.append({
            "chunk_index": len(chunks),
            "clause": clause,
            "title": title,
            "page_start": page_start,
            "page_end": page_end,
            "content": content,
            "structured": {
                "evidence_type": "standard_original",
                "page_start": page_start,
                "page_end": page_end,
            },
        })
        buffer = []
        page_start = 0
        page_end = 0

    for page in pages:
        if not page.text.strip():
            continue
        page_block = f"[第 {page.number} 页 | {page.extraction}]\n{page.text.strip()}"
        if buffer and sum(len(part) for part in buffer) + len(page_block) > STANDARD_CHUNK_CHARS:
            flush()
        if not buffer:
            page_start = page.number
        buffer.append(page_block)
        page_end = page.number
    flush()
    return chunks


_METADATA_PROMPT = """你是测试标准文档编目专家。根据标准首页与前言文本，返回严格 JSON：
{
  "code": "标准编号，不含版本年份",
  "version": "版本或年份",
  "title": "标准完整名称",
  "organization": "ISO/IEC/CISPR/GB/EN/企业名称",
  "category": "辐射发射|传导发射|抗扰度|综合EMC|其他"
}
只能提取原文明确存在的信息，无法确定的字段返回空字符串。不要输出解释。"""


async def _extract_metadata_with_llm(text: str, fallback: dict[str, str]) -> dict[str, str]:
    reviewer = DeepSeekReviewer()
    try:
        result = await reviewer._call_api(
            prompt=f"标准文本：\n{text[:16000]}",
            stage="standard_metadata",
            timeout=120,
            system_prompt=_METADATA_PROMPT,
            max_tokens=1800,
            task_kind="classification",
        )
    except Exception as exc:
        logger.warning("standard_metadata_llm_failed", error=str(exc)[:300])
        return fallback
    if not isinstance(result, dict):
        return fallback
    merged = dict(fallback)
    for key in ("code", "version", "title", "organization", "category"):
        value = result.get(key)
        if value is not None and str(value).strip():
            merged[key] = str(value).strip()
    return merged


async def build_standard_knowledge(std_id: str) -> dict[str, Any]:
    """Extract and index one uploaded standard. Safe to call again for retry."""
    started = time.time()
    file_record = await database.get_standard_file_async(std_id)
    if not file_record:
        raise ValueError("标准原文件不存在")
    filename, file_bytes = file_record
    await database.update_standard_knowledge_async(std_id, status="processing", error="")
    logger.info("standard_knowledge_build_started", standard_id=std_id, filename=filename)
    try:
        pages = await asyncio.to_thread(extract_standard_pages, file_bytes, filename)
        if filename.lower().endswith(".pdf"):
            sparse_pages = sum(
                len(page.text.strip()) < STANDARD_NATIVE_TEXT_THRESHOLD for page in pages
            )
            if sparse_pages:
                logger.info(
                    "standard_visual_recovery_started",
                    standard_id=std_id,
                    page_count=len(pages),
                    sparse_page_count=sparse_pages,
                )
                pages = await recover_standard_visual_pages(file_bytes, pages)
        plain_text = "\n\n".join(
            f"[第 {page.number} 页]\n{page.text}" for page in pages if page.text.strip()
        )
        if not plain_text.strip():
            raise ValueError("标准全文提取为空")
        chunks = build_standard_chunks(pages)
        if not chunks:
            raise ValueError("标准知识块为空")
        current = await database.get_standard_async(std_id)
        fallback = _infer_metadata(filename, plain_text)
        if current:
            if current.code and current.code != Path(filename).stem:
                fallback["code"] = current.code
            if current.title and current.title != filename:
                fallback["title"] = current.title
            for key in ("organization", "category", "version"):
                value = getattr(current, key, "")
                if value:
                    fallback[key] = value
        metadata = await _extract_metadata_with_llm(plain_text, fallback)
        # Metadata explicitly supplied at upload time is authoritative. The LLM
        # fills missing catalog fields but must not shorten or rewrite them.
        if current:
            if current.code and current.code != Path(filename).stem:
                metadata["code"] = current.code
            if current.title and current.title != filename:
                metadata["title"] = current.title
            for key in ("organization", "category", "version"):
                value = getattr(current, key, "")
                if value:
                    metadata[key] = value
        count = await database.replace_standard_knowledge_chunks_async(std_id, chunks)
        visual_pages = sum(1 for page in pages if page.extraction == "qwen_vision")
        meta = {
            "pages": len(pages),
            "visual_pages": visual_pages,
            "text_length": len(plain_text),
            "chunk_count": count,
            "duration_ms": round((time.time() - started) * 1000),
            "metadata_source": "llm_with_deterministic_fallback",
        }
        await database.update_standard_knowledge_async(
            std_id,
            status="ready",
            code=metadata["code"],
            title=metadata["title"],
            organization=metadata["organization"],
            category=metadata["category"],
            version=metadata["version"],
            page_count=len(pages),
            plain_text=plain_text,
            meta=meta,
        )
        logger.info("standard_knowledge_build_completed", standard_id=std_id, **meta)
        return meta
    except Exception as exc:
        await database.update_standard_knowledge_async(
            std_id, status="failed", error=str(exc)[:1000],
            meta={"duration_ms": round((time.time() - started) * 1000)},
        )
        logger.error("standard_knowledge_build_failed", standard_id=std_id, error=str(exc)[:500])
        raise


async def build_standard_knowledge_background(std_id: str) -> None:
    """Background-task wrapper that leaves failures in DB/logs without unhandled task errors."""
    try:
        await build_standard_knowledge(std_id)
        from services.standard_graph import build_standard_graph_background
        from utils.background_tasks import start_background_task
        start_background_task(
            build_standard_graph_background(std_id),
            name=f"standard-graph-{std_id}", key=f"standard-graph:{std_id}",
        )
    except Exception:
        # build_standard_knowledge already persisted the failure and logged context.
        return


_SELECT_CHUNKS_PROMPT = """你是测试标准知识库检索器。用户会提供一个测试模块查询和同一标准的知识块目录。
请选择最可能包含该测试项目适用范围、测试方法、测试条件、参数、等级、限值或判定规则的知识块。
允许中英文语义对应，例如“脉冲4”与“test pulse 4”。不得选择无关章节。
返回严格 JSON：{"chunk_ids": ["知识块ID"], "reason": "简短理由"}。
最多选择 6 个；无法判断返回空数组。"""


_EXTERNAL_STANDARD_REFERENCE_RE = re.compile(
    r"^\s*(?:ISO|IEC|CISPR|EN|GB(?:/T)?|GBZ|QC/T|SAE|DIN|FCC|Q/[A-Z0-9.-]+)(?:\s|\d)",
    re.IGNORECASE,
)


def _is_external_standard_reference(value: str) -> bool:
    return bool(_EXTERNAL_STANDARD_REFERENCE_RE.search(value.strip()))


async def retrieve_standard_context(
    standards: list[Any], query: str, module_key: str = "", set_id: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """Retrieve evidence chunks from selected standards for one review module."""
    if not standards:
        logger.info("standard_review_skipped", module_key=module_key, reason="no_standard_selected")
        return "", []
    reviewer = DeepSeekReviewer()
    selected_all: list[dict[str, Any]] = []
    project_id = (
        await database.get_document_set_project_group_id_async(set_id)
        if set_id else ""
    )

    def field(obj: Any, name: str) -> str:
        if isinstance(obj, dict):
            return str(obj.get(name, "") or "")
        return str(getattr(obj, name, "") or "")

    selected_codes = {
        database.normalize_standard_code(field(item, "code")) for item in standards
        if field(item, "code")
    }

    for standard in standards:
        std_id = field(standard, "id")
        if not std_id:
            continue
        release_id = field(standard, "selected_release_id") or field(standard, "latest_release_id")
        release = await database.get_standard_release_async(release_id) if release_id else None
        if not release or release.get("status") != "published":
            logger.warning("standard_retrieval_unresolved", standard_id=std_id,
                           module_key=module_key, reason="release_not_published",
                           release_id=release_id)
            continue
        snapshot = release.get("snapshot") or {}
        requirements = list(snapshot.get("requirements") or [])
        all_chunks = []
        for requirement in requirements:
            parameter_text = "; ".join(
                " ".join(str(p.get(key) or "") for key in
                         ("name", "symbol", "comparator", "value", "value_min", "value_max", "unit")).strip()
                for p in requirement.get("parameters") or []
            )
            all_chunks.append({
                "id": requirement["id"],
                "standard_id": std_id,
                "clause": requirement["clause_number"],
                "title": requirement["test_item"] or requirement["clause_title"],
                "page_start": requirement["page_start"],
                "page_end": requirement["page_end"],
                "content": (
                    f"要求类型：{requirement['requirement_type']}\n"
                    f"英文/原文要求：{requirement.get('original_statement') or requirement.get('evidence_quote') or '-'}\n"
                    f"人工确认中文释义：{requirement.get('interpretation_zh') or requirement.get('statement') or '-'}\n"
                    f"适用条件：{requirement['applicability'] or '-'}\n"
                    f"参数：{parameter_text or '-'}\n"
                    f"确认过的原文证据：{requirement['evidence_quote']}"
                ),
                "relations": requirement.get("relations") or [],
                "structured": {
                    "evidence_type": "confirmed_standard_requirement",
                    "release_id": release_id,
                    "release_number": release.get("release_number", 0),
                },
            })
        if not all_chunks:
            logger.warning("standard_retrieval_unresolved", standard_id=std_id,
                           module_key=module_key, reason="no_confirmed_requirements")
            continue
        by_id = {item["id"]: item for item in all_chunks}
        mappings = await database.get_standard_requirement_mappings_async(
            std_id, release_id, module_key, set_id, project_id,
        )
        human_mapping = mappings[0] if mappings else None
        if human_mapping and human_mapping.get("mapping_type") == "not_covered":
            selected_all.append({
                "id": human_mapping["id"], "standard_id": std_id,
                "standard_code": field(standard, "code"),
                "standard_version": field(standard, "version"),
                "release_id": release_id,
                "release_number": release.get("release_number", 0),
                "clause": "人工映射", "title": module_key,
                "page_start": 0, "page_end": 0,
                "content": (
                    f"人工确认：测试项目“{module_key}”不属于该标准发布版本的覆盖范围。\n"
                    f"依据：{human_mapping.get('rationale') or '人工审核结论'}"
                ),
                "structured": {"evidence_type": "human_no_coverage_mapping"},
            })
            logger.info(
                "standard_retrieval_human_mapping", standard_id=std_id,
                release_id=release_id, module_key=module_key,
                mapping_id=human_mapping["id"], mapping_type="not_covered",
            )
            continue

        mapped_requirement = (
            by_id.get(str(human_mapping.get("requirement_id") or ""))
            if human_mapping else None
        )
        latin_terms = {
            term.lower() for term in re.findall(r"[A-Za-z0-9][A-Za-z0-9./-]+", query)
            if len(term) >= 2
        }
        chinese_terms: set[str] = set()
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", query):
            for size in (2, 3, 4):
                chinese_terms.update(run[index:index + size] for index in range(len(run) - size + 1))
        terms = latin_terms | chinese_terms
        lexical_scores = {
            item["id"]: sum(1 for term in terms if term in item["content"].lower())
            for item in all_chunks
        }

        from services.standard_embeddings import deserialize_vector, encode_texts, cosine_similarity
        embedding_rows = await asyncio.to_thread(database.get_standard_release_embeddings, release_id)
        vector_scores: dict[str, float] = {}
        if embedding_rows:
            try:
                query_model, query_vectors = await asyncio.to_thread(encode_texts, [query])
                stored_model = str(embedding_rows[0].get("model_name") or "")
                if query_model != stored_model:
                    raise ValueError(f"向量模型版本不一致: {stored_model} != {query_model}")
                query_vector = query_vectors[0]
                vector_scores = {
                    row["requirement_id"]: cosine_similarity(
                        query_vector, deserialize_vector(row["vector"]),
                    )
                    for row in embedding_rows
                }
            except Exception as exc:
                logger.warning("standard_vector_query_failed", standard_id=std_id,
                               release_id=release_id, module_key=module_key,
                               error=str(exc)[:300])

        lexical_ranked = sorted(all_chunks, key=lambda item: lexical_scores[item["id"]], reverse=True)
        vector_ranked = sorted(all_chunks, key=lambda item: vector_scores.get(item["id"], 0.0), reverse=True)
        candidate_ids = [item["id"] for item in lexical_ranked[:20]]
        candidate_ids.extend(item["id"] for item in vector_ranked[:20] if item["id"] not in candidate_ids)
        llm_candidates = [by_id[item_id] for item_id in candidate_ids[:STANDARD_LLM_CANDIDATE_LIMIT]]
        catalog = [
            {"id": item["id"], "clause": item["clause"], "title": item["title"],
             "pages": f'{item["page_start"]}-{item["page_end"]}',
             "lexical_score": lexical_scores.get(item["id"], 0),
             "vector_score": round(vector_scores.get(item["id"], 0.0), 4),
             "excerpt": item["content"][:1000]}
            for item in llm_candidates
        ]
        llm_ids: list[str] = []
        if mapped_requirement:
            llm_ids = [mapped_requirement["id"]]
            logger.info(
                "standard_retrieval_human_mapping", standard_id=std_id,
                release_id=release_id, module_key=module_key,
                mapping_id=human_mapping["id"], mapping_type="covered",
                requirement_id=mapped_requirement["id"],
            )
        else:
            try:
                result = await reviewer._call_api(
                    prompt=(
                        f"测试模块：{module_key}\n查询：{query[:6000]}\n\n"
                        f"知识块目录：{json.dumps(catalog, ensure_ascii=False)}"
                    ),
                    stage="standard_retrieval",
                    timeout=120,
                    system_prompt=_SELECT_CHUNKS_PROMPT,
                    max_tokens=1800,
                    task_kind="classification",
                )
                if isinstance(result, dict):
                    llm_ids = [str(value) for value in result.get("chunk_ids", [])[:STANDARD_RETRIEVAL_LIMIT]]
            except Exception as exc:
                logger.warning("standard_retrieval_llm_failed", standard_id=std_id,
                               module_key=module_key, error=str(exc)[:300])
        selected: list[dict[str, Any]] = []
        for chunk_id in llm_ids:
            has_retrieval_support = bool(mapped_requirement) or (
                lexical_scores.get(chunk_id, 0) > 0
                or vector_scores.get(chunk_id, 0.0) >= STANDARD_VECTOR_MIN_SCORE
            )
            if chunk_id in by_id and has_retrieval_support and by_id[chunk_id] not in selected:
                selected.append(by_id[chunk_id])
        selected = selected[:STANDARD_RETRIEVAL_LIMIT]
        if not selected:
            logger.warning(
                "standard_retrieval_unresolved", standard_id=std_id, release_id=release_id,
                module_key=module_key, reason="insufficient_hybrid_confidence",
                lexical_top=max(lexical_scores.values(), default=0),
                vector_top=round(max(vector_scores.values(), default=0.0), 4),
                llm_selected_ids=llm_ids,
            )
            continue

        # Expand direct references within the same immutable release by one level.
        selected_ids = {item["id"] for item in selected}
        referenced_clauses = {
            str(relation.get("target_ref") or "").strip()
            for item in selected for relation in item.get("relations") or []
            if re.fullmatch(r"\d+(?:\.\d+)*", str(relation.get("target_ref") or "").strip())
        }
        for item in all_chunks:
            if len(selected) >= STANDARD_RETRIEVAL_LIMIT:
                break
            if item["id"] not in selected_ids and item.get("clause") in referenced_clauses:
                selected.append(item)
                selected_ids.add(item["id"])
        code = field(standard, "code")
        version = field(standard, "version")
        for item in selected:
            item = dict(item)
            item["standard_code"] = code
            item["standard_version"] = version
            item["release_id"] = release_id
            item["release_number"] = release.get("release_number", 0)
            selected_all.append(item)
        dependency_targets = {
            str(relation.get("target_ref") or "").strip()
            for item in selected for relation in item.get("relations") or []
            if str(relation.get("target_ref") or "").strip()
            and not re.fullmatch(r"\d+(?:\.\d+)*", str(relation.get("target_ref") or "").strip())
            and _is_external_standard_reference(str(relation.get("target_ref") or ""))
        }
        for target in sorted(dependency_targets):
            normalized_target = database.normalize_standard_code(target)
            if any(code and code in normalized_target for code in selected_codes):
                continue
            dependency_id = "dependency-" + hashlib.sha256(
                f"{release_id}:{target}".encode("utf-8")
            ).hexdigest()[:12]
            selected_all.append({
                "id": dependency_id, "standard_id": std_id,
                "standard_code": code, "standard_version": version,
                "release_id": release_id,
                "release_number": release.get("release_number", 0),
                "clause": "引用关系", "title": target,
                "page_start": 0, "page_end": 0,
                "content": f"当前命中的标准要求引用了其他标准或外部文件：{target}。该依赖未在本次审核中选择，禁止自动补充其内容。",
                "structured": {
                    "evidence_type": "missing_cross_standard_dependency",
                    "target_ref": target,
                },
            })
            logger.warning(
                "standard_cross_dependency_unresolved", standard_id=std_id,
                release_id=release_id, module_key=module_key, target_ref=target,
            )
        logger.info(
            "standard_retrieval", standard_id=std_id, standard_code=code,
            module_key=module_key, query_length=len(query), candidates=len(all_chunks),
            llm_candidates=len(llm_candidates),
            selected_chunk_ids=[item["id"] for item in selected],
            release_id=release_id,
        )

    blocks = []
    included: list[dict[str, Any]] = []
    for item in selected_all:
        block = (
            f"[标准证据 {item['standard_code']}:{item['id']} | 文件版本 {item['standard_version']} | "
            f"知识版本 R{item.get('release_number', 0)} | "
            f"章节 {item['clause'] or '-'} | 第 {item['page_start']}-{item['page_end']} 页]\n"
            f"{item['content'][:STANDARD_CONTEXT_CHUNK_CHARS]}"
        )
        if blocks and sum(len(existing) for existing in blocks) + len(block) > STANDARD_CONTEXT_MAX_CHARS:
            break
        blocks.append(block)
        included.append(item)
    context = "\n\n".join(blocks)
    logger.info("standard_context_injected", module_key=module_key,
                chunks=len(included), context_length=len(context))
    return context, included
