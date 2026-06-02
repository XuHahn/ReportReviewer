# Note: `request: Request = None` is the FastAPI convention for optional dependencies.
# Using `Request | None` breaks FastAPI's dependency injection (it interprets the union
# as a response model). This is safe at runtime — the `= None` signals optionality to FastAPI.
import asyncio
import json as json_mod
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, UploadFile, File, HTTPException, Request, Form, Depends
from fastapi.responses import Response, StreamingResponse
from bs4 import BeautifulSoup
from services.deepseek_client import (
    DeepSeekReviewer, SCAN_PROMPT, REVIEW_PROMPT,
    prepare_review_text, _parse_json,
)
from services.parser import ReportParser
from services.exporter import generate_pdf, generate_docx, generate_batch_excel
from services.streaming_parser import IncrementalReviewParser
from services.suppression_filter import filter_review_items
from utils.highlighter import TextHighlighter
from database import (save_report_async, get_report_async, get_reports_async,
                       init_db, save_audit_log_async, get_audit_logs_async,
                       get_stats_async, search_reports_async,
                       save_rule_async, get_rules_async, get_rule_async,
                       update_rule_async, delete_rule_async, get_enabled_rules_async,
                       save_standard_async, get_standards_async, get_standard_async,
                       delete_standard_async, seed_admin,
                       update_review_item_annotation_async,
                       get_suppression_patterns_async,
                       delete_report_async, delete_report_group_async,
                       restore_report_async, update_report_tags_async,
                       generate_group_id_async,
                       get_user_async, get_user_project_group_names_async,
                       create_tag_async, get_tags_async, update_tag_async, delete_tag_async)
from models import (ReviewItem, UploadResponse, ReportListResponse,
                     AuditLogListResponse, StatsResponse,
                     SearchResponse, SearchResult,
                     ReviewRule, RuleCreateRequest, RuleUpdateRequest,
                     EmcStandard, StandardCreateRequest, StandardListResponse,
                     User, AnnotationUpdateRequest,
                     TagCreateRequest, TagUpdateRequest,
                     FrontendLogBatchRequest)
from auth import get_current_user, require_role
from rate_limit import check as _rate_check, MAX_UPLOAD as _rate_max_upload, MAX_GENERAL as _rate_max_general
from utils.logger import get_logger, get_frontend_logger

logger = get_logger(__name__)

MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


def _client_ip(request: Request | None) -> str:
    return request.client.host if request and request.client else "unknown"


async def _ensure_report_access(record, user: User) -> None:
    """Verify the user has permission to access this report.

    Note: tag-based access control uses a simple ``record.tags in group_names``
    check, which compares the full tags string against each group name.  This
    works correctly for single-tag reports but does NOT handle comma-separated
    multi-tag reports (e.g. "groupA,groupB" will never match "groupA" alone).
    If multi-tag support is added, this comparison must be split on commas.
    """
    if user.role == "viewer":
        if record.employee_id == user.employee_id:
            return
        group_names = await get_user_project_group_names_async(user.employee_id)
        if record.tags and record.tags in group_names:
            return
        raise HTTPException(403, "无权访问该报告")


def _encoded_filename(name: str, ext: str) -> str:
    encoded = quote(f"{name}.{ext}")
    return f"attachment; filename=\"{encoded}\"; filename*=UTF-8''{encoded}"


router = APIRouter()
reviewer = DeepSeekReviewer()

SEV_LABEL = {"error": "错误", "warning": "警告", "info": "提示"}
HUMAN_STATUS_LABEL = {
    "pending": "待复核", "confirmed": "已确认",
    "false_positive": "非错误", "needs_review": "需复核", "ignored": "已忽略",
}
HUMAN_STATUS_VALID = set(HUMAN_STATUS_LABEL.keys())


def _format_rules_for_prompt(rules: list[ReviewRule]) -> str:
    if not rules:
        return ""
    lines = ["", "## 用户自定义审核规则", ""]
    for r in rules:
        sev = SEV_LABEL.get(r.severity, r.severity)
        lines.append(f"- [{sev}] {r.name}: {r.description}")
    return "\n".join(lines)


async def _validate_and_parse(file: UploadFile, client_ip: str, employee_id: str = ""):
    if file.filename is None:
        raise HTTPException(400, "未选择文件")
    fname = file.filename
    filename_lower = fname.lower()
    if not (filename_lower.endswith(".pdf") or filename_lower.endswith(".docx")):
        await save_audit_log_async(client_ip=client_ip, action="upload_rejected",
                                    employee_id=employee_id, filename=fname,
                                    detail="不支持的文件格式")
        raise HTTPException(400, "仅支持PDF和Word(.docx)格式")
    file_bytes = await file.read()
    file_size_kb = len(file_bytes) // 1024
    if len(file_bytes) > MAX_UPLOAD_BYTES:
        await save_audit_log_async(client_ip=client_ip, action="upload_rejected",
                                    employee_id=employee_id, filename=fname,
                                    file_size_kb=file_size_kb, detail=f"文件过大: {file_size_kb}KB")
        raise HTTPException(400, f"文件过大（{file_size_kb // 1024}MB），上限 100MB")
    parsed = await ReportParser.parse(file_bytes, fname)
    if not parsed.plain_text.strip():
        await save_audit_log_async(client_ip=client_ip, action="upload_rejected",
                                    employee_id=employee_id, filename=fname,
                                    file_size_kb=file_size_kb, detail="未能提取文本内容")
        raise HTTPException(400, "未能从文件中提取到文本内容")
    return parsed, file_size_kb


def extract_toc(html_content: str) -> str:
    soup = BeautifulSoup(html_content, "lxml")
    headings = soup.find_all(["h1", "h2", "h3", "h4"])
    lines = []
    for h in headings:
        text = h.get_text().strip()
        if not text or len(text) > 200:
            continue
        level = int(h.name[1])
        indent = "  " * (level - 1)
        lines.append(f"{indent}- {text}")
    return "\n".join(lines) if lines else "（未检测到章节标题）"


def _pass_summary(review_items: list[ReviewItem]) -> str:
    er = sum(1 for i in review_items if i.severity == "error")
    wr = sum(1 for i in review_items if i.severity == "warning")
    inf = sum(1 for i in review_items if i.severity == "info")
    parts = []
    if er: parts.append(f"{er} 错误")
    if wr: parts.append(f"{wr} 警告")
    if inf: parts.append(f"{inf} 提示")
    return "、".join(parts) if parts else "无问题"


def _highlight_and_match(html_content: str, plain_text: str,
                          review_items: list[ReviewItem], items_data: list[dict],
                          fallback_tag: str, filename: str) -> str:
    highlighter = TextHighlighter()
    highlighted_html, matched_indices = highlighter.highlight_html(
        html_content, items_data)
    # Update highlighted flag on items
    for i, item in enumerate(review_items):
        item.highlighted = i in matched_indices
    if len(matched_indices) < len(review_items):
        logger.warning("%s file=%s matched=%d/%d", fallback_tag, filename, len(matched_indices), len(review_items))
    return highlighted_html


# ═══════════════════════════════════════════════════════════════════════════
# Upload endpoints
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/reports/upload", response_model=UploadResponse)
async def upload_and_review(file: UploadFile = File(...), tags: str = Form(""),
                             compare_with: str = Form(""),
                             request: Request = None,
                             user: User = Depends(require_role("admin", "reviewer"))):
    import time as _time
    start_time = _time.time()
    client_ip = _client_ip(request)
    if not _rate_check(client_ip, _rate_max_upload):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    parsed, file_size_kb = await _validate_and_parse(file, client_ip, user.employee_id)
    try:
        toc = extract_toc(parsed.html_content)
        enabled_rules = await get_enabled_rules_async()
        rules_text = _format_rules_for_prompt(enabled_rules)
        review_result = await reviewer.review_report(
            parsed.plain_text, toc, parsed.html_content, rules_text)
        review_items_data = review_result.get("review_items", [])
        review_items = [ReviewItem(**item) for item in review_items_data]
        patterns = await get_suppression_patterns_async()
        if patterns:
            review_items = filter_review_items(review_items, patterns)
        highlighted_html = _highlight_and_match(
            parsed.html_content, parsed.plain_text,
            review_items, review_items_data,
            "HIGHLIGHT_FALLBACK", file.filename)
        overall = review_result.get("overall_result", "error")
        group_id = await generate_group_id_async()
        comparison = ""
        if compare_with:
            old_record = await get_report_async(compare_with)
            if old_record and old_record.group_id:
                group_id = old_record.group_id
            if old_record and old_record.review_items:
                old_items = [it.model_dump() for it in old_record.review_items]
                new_items = [it.model_dump() for it in review_items]
                try:
                    comp_result = await reviewer.compare_reviews(old_items, new_items)
                    comparison = json_mod.dumps(comp_result.get("items", []), ensure_ascii=False)
                    # Apply comparison status to new review_items
                    for m in comp_result.get("items", []):
                        ni = m.get("new_idx")
                        if ni is not None and 0 <= ni < len(review_items):
                            if not review_items[ni].human_comment:
                                review_items[ni].human_comment = m.get("status", "")
                except Exception:
                    logger.exception("COMPARE_FAILED")
        report_id = await save_report_async(
            filename=file.filename, overall_result=overall,
            review_items=review_items, highlighted_html=highlighted_html,
            employee_id=user.employee_id, tags=tags, group_id=group_id, comparison=comparison, compared_with=compare_with, original_result=overall)
        duration_ms = int((_time.time() - start_time) * 1000)
        logger.info("UPLOAD client=%s file=%s result=%s issues=%d duration_ms=%d report_id=%s employee=%s",
                    client_ip, file.filename, overall, len(review_items), duration_ms, report_id, user.employee_id)
        await save_audit_log_async(client_ip=client_ip, action="upload",
                                    filename=file.filename, file_size_kb=file_size_kb,
                                    issue_count=len(review_items), detail=f"report_id={report_id}",
                                    duration_ms=duration_ms, employee_id=user.employee_id)
        return UploadResponse(
            report_id=report_id, filename=file.filename, overall_result=overall,
            review_items=review_items, highlighted_html=highlighted_html,
            created_at=_time.strftime("%Y-%m-%dT%H:%M:%S"),
            employee_id=user.employee_id, uploader_name=user.name, tags=tags, original_result=overall,
            group_id=group_id, comparison=comparison, compared_with=compare_with,
        )
    except Exception:
        logger.exception("UPLOAD_ERROR client=%s file=%s", client_ip, file.filename)
        await save_audit_log_async(client_ip=client_ip, action="upload_error",
                                    filename=file.filename, employee_id=user.employee_id)
        raise HTTPException(500, "AI审核失败，请稍后重试")


@router.post("/reports/upload/stream")
async def upload_and_review_stream(file: UploadFile = File(...), tags: str = Form(""),
                                    compare_with: str = Form(""),
                                    request: Request = None,
                                    user: User = Depends(require_role("admin", "reviewer"))):
    client_ip = _client_ip(request)
    if not _rate_check(client_ip, _rate_max_upload):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    parsed, file_size_kb = await _validate_and_parse(file, client_ip, user.employee_id)

    async def _event_stream():
        import time as _time
        start_time = _time.time()
        report_id = ""
        filename = file.filename
        group_id = ""
        comparison = ""
        try:
            toc = extract_toc(parsed.html_content)
            group_id = await generate_group_id_async()
            if compare_with:
                old_record = await get_report_async(compare_with)
                if old_record and old_record.group_id:
                    group_id = old_record.group_id
                if old_record and old_record.review_items:
                    old_items = [it.model_dump() for it in old_record.review_items]
                    new_items_data = []  # will be filled after review
                    # Comparison will be done after review
            enabled_rules = await get_enabled_rules_async()
            rules_text = _format_rules_for_prompt(enabled_rules)
            review_text, skipped, _, _ = prepare_review_text(parsed.plain_text, toc, parsed.html_content)
            yield f"data: {json_mod.dumps({'type': 'progress', 'data': {'phase': 'stage1_scanning', 'message': 'Stage 1 粗扫描中...', 'elapsed_seconds': 0}})}\n\n"
            t1 = _time.time()
            checklist = await asyncio.to_thread(lambda: list(reviewer.stream_stage1(
                SCAN_PROMPT.format(toc=toc, report_content=review_text, user_rules=rules_text))))
            t1_elapsed = int(_time.time() - t1)
            sp = IncrementalReviewParser()
            for chunk in checklist:
                sp.feed(chunk)
            checklist_data = sp.finalize() or {}
            flags = checklist_data.get("checklist", [])
            yield f"data: {json_mod.dumps({'type': 'checklist_summary', 'data': {'total_flags': len(flags), 'severities': {}, 'sample_flags': [f.get('flag', '') for f in flags[:5]]}})}\n\n"
            yield f"data: {json_mod.dumps({'type': 'progress', 'data': {'phase': 'stage1_done', 'message': f'Stage 1 完成（{t1_elapsed}s），发现 {len(flags)} 处疑似问题', 'elapsed_seconds': t1_elapsed}})}\n\n"
            yield f"data: {json_mod.dumps({'type': 'progress', 'data': {'phase': 'stage2_reviewing', 'message': 'Stage 2 详细审核中...', 'elapsed_seconds': t1_elapsed}})}\n\n"
            t2 = _time.time()
            review_prompt = REVIEW_PROMPT.format(
                toc=toc, checklist=json_mod.dumps(checklist_data, ensure_ascii=False),
                report_content=review_text, user_rules=rules_text, standard_limits="")
            st2_chunks = await asyncio.to_thread(lambda: list(reviewer.stream_stage2(review_prompt)))
            sp2 = IncrementalReviewParser()
            last_yielded = 0
            for chunk in st2_chunks:
                sp2.feed(chunk)
                # Yield newly parsed items as they arrive
                current = sp2.all_items
                for i in range(last_yielded, len(current)):
                    it = current[i]
                    yield f"data: {json_mod.dumps({'type': 'review_item', 'data': it}, ensure_ascii=False)}\n\n"
                last_yielded = len(current)
            final = sp2.finalize() or {}
            all_items = sp2.all_items
            review_items = [ReviewItem(**it) for it in all_items]
            patterns = await get_suppression_patterns_async()
            if patterns:
                review_items = filter_review_items(review_items, patterns)
            if compare_with and old_record and old_record.review_items:
                try:
                    old_items = [it.model_dump() for it in old_record.review_items]
                    new_items = [it.model_dump() for it in review_items]
                    comp_result = await reviewer.compare_reviews(old_items, new_items)
                    comparison = json_mod.dumps(comp_result.get("items", []), ensure_ascii=False)
                    for m in comp_result.get("items", []):
                        ni = m.get("new_idx")
                        if ni is not None and 0 <= ni < len(review_items):
                            if not review_items[ni].human_comment:
                                review_items[ni].human_comment = m.get("status", "")
                except Exception:
                    logger.exception("COMPARE_FAILED_STREAM")
            items_data = [it.model_dump() for it in review_items]
            highlighted_html = _highlight_and_match(
                parsed.html_content, parsed.plain_text,
                review_items, items_data, "STREAM_HIGHLIGHT_FALLBACK", filename)
            overall_result = final.get("overall_result") or final.get("result") or ""
            if not overall_result:
                er = sum(1 for it in review_items if it.severity == "error")
                wr = sum(1 for it in review_items if it.severity == "warning")
                if er > 0: overall_result = "fail"
                elif wr > 0: overall_result = "warning"
                else: overall_result = "pass"
            elif overall_result not in ("pass", "fail", "warning"):
                overall_result = "fail"
            yield f"data: {json_mod.dumps({'type': 'progress', 'data': {'phase': 'highlighting', 'message': '标红匹配中...', 'elapsed_seconds': int(_time.time() - start_time)}})}\n\n"
            report_id = await save_report_async(
                filename=filename, overall_result=overall_result,
                review_items=review_items, highlighted_html=highlighted_html,
                employee_id=user.employee_id, tags=tags, group_id=group_id, comparison=comparison, compared_with=compare_with, original_result=overall_result)
            duration_ms = int((_time.time() - start_time) * 1000)
            result_data = {
                "report_id": report_id, "filename": filename,
                "overall_result": overall_result,
                "original_result": overall_result,
                "review_items": [it.model_dump() for it in review_items],
                "highlighted_html": highlighted_html,
                "created_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
                "estimated_tokens": 0, "token_limit": 0, "truncated": False,
                "employee_id": user.employee_id,
                "uploader_name": user.name,
                "tags": tags,
                "group_id": group_id,
                "comparison": comparison,
                "compared_with": compare_with,
            }
            yield f"data: {json_mod.dumps({'type': 'result', 'data': result_data})}\n\n"
            logger.info("UPLOAD_STREAM client=%s file=%s result=%s issues=%d duration_ms=%d report_id=%s employee=%s",
                        client_ip, filename, overall_result, len(review_items), duration_ms, report_id, user.employee_id)
            await save_audit_log_async(client_ip=client_ip, action="upload_stream",
                                        filename=filename, issue_count=len(review_items),
                                        detail=f"report_id={report_id}", duration_ms=duration_ms,
                                        employee_id=user.employee_id)
        except Exception:
            logger.exception("UPLOAD_STREAM_ERROR client=%s file=%s", client_ip, filename)
            yield f"data: {json_mod.dumps({'type': 'error', 'data': {'message': 'AI审核失败，请稍后重试'}})}\n\n"
    return StreamingResponse(_event_stream(), media_type="text/event-stream")


@router.post("/reports/upload/batch")
async def upload_batch(request: Request, files: list[UploadFile] = File(...),
                        tags: str = Form(""), compare_with: str = Form(""),
                        user: User = Depends(require_role("admin", "reviewer"))):
    client_ip = _client_ip(request)
    if not _rate_check(client_ip, _rate_max_upload):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    if len(files) > 10:
        raise HTTPException(400, "单次最多上传10份报告")
    if len(files) == 0:
        raise HTTPException(400, "请选择文件")
    parsed_list = []
    parse_errors = []
    for i, f in enumerate(files):
        try:
            p, sz = await _validate_and_parse(f, client_ip, user.employee_id)
            parsed_list.append((i, f.filename, p, sz))
        except HTTPException as e:
            parse_errors.append({"index": i, "filename": f.filename or f"file_{i}", "error": e.detail})

    async def _event_stream():
        import time as _time
        start_time = _time.time()
        yield f"data: {json_mod.dumps({'type': 'batch_start', 'data': {'total': len(files), 'filenames': [f.filename or '' for f in files], 'errors': parse_errors}})}\n\n"
        sem = asyncio.Semaphore(3)
        results = []
        async def process_one(idx: int, fname: str, parsed, file_sz: int):
            async with sem:
                group_id = ""
                comparison = ""
                try:
                    group_id = await generate_group_id_async()
                    if compare_with:
                        old_record = await get_report_async(compare_with)
                        if old_record and old_record.group_id:
                            group_id = old_record.group_id
                    toc = extract_toc(parsed.html_content)
                    enabled_rules = await get_enabled_rules_async()
                    rules_text = _format_rules_for_prompt(enabled_rules)
                    review_text, skipped, _, _ = prepare_review_text(parsed.plain_text, toc, parsed.html_content)
                    checklist = await asyncio.to_thread(lambda: list(reviewer.stream_stage1(
                        SCAN_PROMPT.format(toc=toc, report_content=review_text, user_rules=rules_text))))
                    sp = IncrementalReviewParser()
                    for chunk in checklist:
                        sp.feed(chunk)
                    checklist_data = sp.finalize() or {}
                    review_prompt = REVIEW_PROMPT.format(
                        toc=toc, checklist=json_mod.dumps(checklist_data, ensure_ascii=False),
                        report_content=review_text, user_rules=rules_text, standard_limits="")
                    st2_chunks = await asyncio.to_thread(lambda: list(reviewer.stream_stage2(review_prompt)))
                    sp2 = IncrementalReviewParser()
                    for chunk in st2_chunks:
                        sp2.feed(chunk)
                    final = sp2.finalize() or {}
                    all_items = sp2.all_items
                    review_items = [ReviewItem(**it) for it in all_items]
                    patterns = await get_suppression_patterns_async()
                    if patterns:
                        review_items = filter_review_items(review_items, patterns)
                    items_data = [it.model_dump() for it in review_items]
                    highlighted_html = _highlight_and_match(
                        parsed.html_content, parsed.plain_text,
                        review_items, items_data, "BATCH_HIGHLIGHT_FALLBACK", fname)
                    overall = final.get("overall_result") or final.get("result") or ""
                    if not overall:
                        er = sum(1 for it in review_items if it.severity == "error")
                        wr = sum(1 for it in review_items if it.severity == "warning")
                        overall = "fail" if er > 0 else ("warning" if wr > 0 else "pass")
                    elif overall not in ("pass", "fail", "warning"):
                        overall = "fail"
                    report_id = await save_report_async(
                        filename=fname, overall_result=overall,
                        review_items=review_items, highlighted_html=highlighted_html,
                        employee_id=user.employee_id, tags=tags, group_id=group_id, comparison=comparison, compared_with=compare_with, original_result=overall)
                    items_data = [it.model_dump() for it in review_items]
                    logger.info("BATCH_FILE_DONE client=%s file=%s result=%s issues=%d report_id=%s employee=%s",
                                client_ip, fname, overall, len(review_items), report_id, user.employee_id)
                    await save_audit_log_async(client_ip=client_ip, action="upload_batch",
                                                filename=fname, issue_count=len(review_items),
                                                detail=f"report_id={report_id}", employee_id=user.employee_id)
                    return {"index": idx, "filename": fname, "report_id": report_id,
                            "overall_result": overall, "review_items": items_data,
                            "highlighted_html": highlighted_html,
                            "created_at": _time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "employee_id": user.employee_id, "uploader_name": user.name, "tags": tags,
                            "group_id": group_id, "comparison": comparison,
                            "compared_with": compare_with, "original_result": overall}
                except Exception:
                    logger.exception("BATCH_FILE_ERROR file=%s", fname)
                    return {"index": idx, "filename": fname, "error": "AI审核失败"}
        tasks = [process_one(idx, fname, parsed, sz) for idx, fname, parsed, sz in parsed_list]
        completed = 0
        for coro in asyncio.as_completed(tasks):
            result = await coro
            completed += 1
            if result.get("error"):
                yield f"data: {json_mod.dumps({'type': 'batch_progress', 'data': {'file_index': result['index'], 'filename': result['filename'], 'phase': 'error', 'error': result['error']}})}\n\n"
            else:
                yield f"data: {json_mod.dumps({'type': 'batch_progress', 'data': {'file_index': result['index'], 'filename': result['filename'], 'phase': 'done', 'overall_result': result['overall_result'], 'issues': len(result.get('review_items', []))}})}\n\n"
            results.append(result)
        duration_ms = int((_time.time() - start_time) * 1000)
        yield f"data: {json_mod.dumps({'type': 'batch_done', 'data': {'total_files': len(files), 'total_issues': sum(len(r.get('review_items', [])) for r in results), 'duration_ms': duration_ms, 'results': results}})}\n\n"
    return StreamingResponse(_event_stream(), media_type="text/event-stream")


# ═══════════════════════════════════════════════════════════════════════════
# History / Search / Stats
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/reports/history", response_model=ReportListResponse)
async def get_history(limit: int = 20, offset: int = 0,
                      keyword: str = "", overall_result: str = "",
                      date_from: str = "", date_to: str = "",
                      employee_id: str = "", tag: str = "",
                      include_deleted: int = 0,
                      request: Request = None,
                      user: User = Depends(get_current_user)):
    if user.role == "viewer":
        employee_id = user.employee_id
        include_deleted = 0
    elif user.role != "admin":
        include_deleted = 0
    limit = max(1, min(limit, 200))
    group_tags = None
    if employee_id:
        group_tags = await get_user_project_group_names_async(employee_id)
        if not group_tags:
            group_tags = None
    reports, total = await get_reports_async(
        limit=limit, offset=offset, keyword=keyword,
        overall_result=overall_result,
        date_from=date_from, date_to=date_to,
        employee_id=employee_id, tag=tag,
        group_tags=group_tags,
        include_deleted=bool(include_deleted))
    return ReportListResponse(reports=reports, total=total)


@router.get("/reports/search", response_model=SearchResponse)
async def search_reports(q: str = "", limit: int = 20, offset: int = 0,
                          request: Request = None,
                          user: User = Depends(get_current_user)):
    employee_id = user.employee_id if user.role == "viewer" else ""
    results, total = await search_reports_async(q, limit=min(limit, 50), offset=offset,
                                                 employee_id=employee_id)
    return SearchResponse(results=results, total=total, query=q)


@router.get("/reports/stats", response_model=StatsResponse)
async def get_stats(request: Request = None, user: User = Depends(get_current_user)):
    employee_id = user.employee_id if user.role == "viewer" else ""
    data = await get_stats_async(employee_id)
    return StatsResponse(**data)


# ═══════════════════════════════════════════════════════════════════════════
# Review Rules CRUD
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/rules")
async def get_rules(keyword: str = "", category: str = "",
                     user: User = Depends(get_current_user)):
    return await get_rules_async(keyword, category)


@router.post("/rules")
async def create_rule(body: RuleCreateRequest,
                      user: User = Depends(require_role("admin", "reviewer"))):
    rule_id = await save_rule_async(
        body.name, body.description, body.category, body.severity,
        body.keywords, body.pattern, body.standard_id,
        body.suggestion_template, body.enabled)
    return {"id": rule_id}


@router.put("/rules/{rule_id}")
async def update_rule_endpoint(rule_id: str, body: RuleUpdateRequest,
                                user: User = Depends(require_role("admin", "reviewer"))):
    existing = await get_rule_async(rule_id)
    if existing is None:
        raise HTTPException(404, "规则不存在")
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    ok = await update_rule_async(rule_id, **updates)
    if not ok:
        raise HTTPException(400, "没有需要更新的字段")
    return {"id": rule_id, "updated": True}


@router.delete("/rules/{rule_id}")
async def delete_rule_endpoint(rule_id: str,
                                user: User = Depends(require_role("admin", "reviewer"))):
    ok = await delete_rule_async(rule_id)
    if not ok:
        raise HTTPException(404, "规则不存在")
    return {"deleted": True}


# ═══════════════════════════════════════════════════════════════════════════
# EMC Standards CRUD
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/standards")
async def get_standards(organization: str = "", category: str = "", keyword: str = "",
                         user: User = Depends(get_current_user)):
    return await get_standards_async(organization, category, keyword)


@router.get("/standards/{std_id}")
async def get_standard_detail(std_id: str, user: User = Depends(get_current_user)):
    std = await get_standard_async(std_id)
    if std is None:
        raise HTTPException(404, "标准不存在")
    return std


@router.post("/standards")
async def create_standard(body: StandardCreateRequest,
                           user: User = Depends(require_role("admin", "reviewer"))):
    std_id = await save_standard_async(
        body.code, body.title, body.organization, body.category,
        body.version, body.clauses)
    return {"id": std_id}


@router.delete("/standards/{std_id}")
async def delete_standard_endpoint(std_id: str,
                                    user: User = Depends(require_role("admin", "reviewer"))):
    ok = await delete_standard_async(std_id)
    if not ok:
        raise HTTPException(404, "标准不存在或为内置标准无法删除")
    return {"deleted": True}


# ═══════════════════════════════════════════════════════════════════════════
# Report detail / annotation / delete
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/reports/{report_id}")
async def get_report_detail(report_id: str, request: Request = None,
                             user: User = Depends(get_current_user)):
    client_ip = _client_ip(request)
    record = await get_report_async(report_id)
    if record is None:
        logger.warning("REPORT_NOT_FOUND client=%s report_id=%s", client_ip, report_id)
        raise HTTPException(404, "报告不存在")
    await _ensure_report_access(record, user)
    logger.info("REPORT_DETAIL client=%s report_id=%s file=%s", client_ip, report_id, record.filename)
    return record


@router.patch("/reports/{report_id}/items/{item_index}/annotation")
async def update_item_annotation(report_id: str, item_index: int,
                                  body: AnnotationUpdateRequest,
                                  request: Request = None,
                                  user: User = Depends(get_current_user)):
    if body.human_status not in HUMAN_STATUS_VALID:
        raise HTTPException(400, f"无效状态，合法值: {', '.join(sorted(HUMAN_STATUS_VALID))}")
    record = await get_report_async(report_id)
    if record is None:
        raise HTTPException(404, "报告不存在")
    await _ensure_report_access(record, user)
    prev_status = record.review_items[item_index].human_status or "pending" if item_index < len(record.review_items) else "pending"
    ok = await update_review_item_annotation_async(
        report_id, item_index,
        human_status=body.human_status,
        human_comment=body.human_comment,
        annotated_by=user.employee_id)
    if not ok:
        raise HTTPException(404, "报告或条目不存在")
    client_ip = _client_ip(request)
    logger.info("ANNOTATE client=%s report_id=%s item=%d status=%s by=%s",
                client_ip, report_id, item_index, body.human_status, user.employee_id)
    await save_audit_log_async(
        client_ip=client_ip, action="annotate", filename=report_id,
        detail=json_mod.dumps({"item_index": item_index, "previous_status": prev_status, "status": body.human_status}),
        employee_id=user.employee_id)
    # Re-fetch to get updated overall_result
    updated = await get_report_async(report_id)
    new_result = updated.overall_result if updated else ""
    return {"ok": True, "new_overall_result": new_result}

@router.get("/reports/{report_id}/check-updates")
async def check_report_updates(report_id: str, since: str = "", by: str = "",
                                user: User = Depends(get_current_user)):
    record = await get_report_async(report_id)
    if record is None:
        raise HTTPException(404, "报告不存在")
    await _ensure_report_access(record, user)
    has_update = False
    updated_result = record.overall_result
    for item in record.review_items:
        at = item.annotated_at or ""
        annotator = item.annotated_by or ""
        if at and since and annotator and annotator != by:
            try:
                if datetime.fromisoformat(at) > datetime.fromisoformat(since):
                    has_update = True
                    break
            except ValueError:
                pass
    return {"has_update": has_update, "overall_result": updated_result}


@router.delete("/reports/{report_id}")
async def delete_report_endpoint(report_id: str, cascade: bool = False,
                                  request: Request = None,
                                  user: User = Depends(get_current_user)):
    record = await get_report_async(report_id)
    if record is None:
        raise HTTPException(404, "报告不存在")
    if user.role != "admin" and record.employee_id != user.employee_id:
        raise HTTPException(403, "仅可删除自己上传的报告")
    client_ip = _client_ip(request)
    if cascade and record.group_id:
        count = await delete_report_group_async(record.group_id)
        logger.warning("DELETE_GROUP client=%s group_id=%s count=%d by=%s",
                       client_ip, record.group_id, count, user.employee_id)
        await save_audit_log_async(
            client_ip=client_ip, action="delete_report_group", filename=record.filename,
            detail=json_mod.dumps({"group_id": record.group_id, "count": count}), employee_id=user.employee_id)
        return {"ok": True, "deleted": count}
    ok = await delete_report_async(report_id)
    if not ok:
        raise HTTPException(404, "报告不存在")
    logger.warning("DELETE client=%s report_id=%s file=%s by=%s",
                   client_ip, report_id, record.filename, user.employee_id)
    await save_audit_log_async(
        client_ip=client_ip, action="delete_report", filename=record.filename,
        detail=json_mod.dumps({"report_id": report_id}), employee_id=user.employee_id)
    return {"ok": True, "deleted": report_id}


@router.patch("/reports/{report_id}/tags")
async def update_report_tags_endpoint(report_id: str, body: dict,
                                       request: Request = None,
                                       user: User = Depends(require_role("admin", "reviewer"))):
    record = await get_report_async(report_id)
    if record is None:
        raise HTTPException(404, "报告不存在")
    if user.role != "admin" and record.employee_id != user.employee_id:
        raise HTTPException(403, "仅可修改自己上传的报告")
    tags = (body.get("tags") or "").strip()
    ok = await update_report_tags_async(report_id, tags)
    if not ok:
        raise HTTPException(404, "报告不存在")
    client_ip = _client_ip(request)
    await save_audit_log_async(client_ip=client_ip, action="update_tags",
                               filename=report_id, detail=json_mod.dumps({"tags": tags}),
                               employee_id=user.employee_id)
    return {"ok": True, "tags": tags}


@router.post("/reports/{report_id}/restore")
async def restore_report_endpoint(report_id: str, request: Request = None,
                                   user: User = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可恢复报告")
    ok = await restore_report_async(report_id)
    if not ok:
        raise HTTPException(404, "报告不存在或未被删除")
    client_ip = _client_ip(request)
    await save_audit_log_async(client_ip=client_ip, action="restore_report",
                               filename=report_id, employee_id=user.employee_id)
    return {"ok": True, "restored": report_id}

@router.get("/reports/{report_id}/timeline")
async def get_report_timeline(report_id: str, request: Request = None,
                               user: User = Depends(get_current_user)):
    record = await get_report_async(report_id)
    if record is None:
        raise HTTPException(404, "报告不存在")
    await _ensure_report_access(record, user)
    all_entries, _ = await get_audit_logs_async(limit=500)
    # Collect all report IDs in the same version group (including soft-deleted)
    group_ids = {report_id}
    if record.group_id:
        mates, _ = await get_reports_async(limit=200, include_deleted=True)
        for r in mates:
            if r.group_id == record.group_id:
                group_ids.add(r.id)
    rid = report_id
    fname = record.filename
    entries = []
    for e in all_entries:
        fn = e.filename or ''
        detail = e.detail or ''
        if any(gid in fn or gid in detail for gid in group_ids) or fname in fn:
            entries.append(e)
    entries.sort(key=lambda e: e.timestamp or '', reverse=True)
    # Attach user names
    result = []
    for e in entries[:100]:
        d = e.model_dump()
        if e.employee_id:
            u = await get_user_async(e.employee_id)
            if u:
                d["name"] = u.name
        result.append(d)
    return {"entries": result, "total": len(entries)}


# ═══════════════════════════════════════════════════════════════════════════
# Export
# ═══════════════════════════════════════════════════════════════════════════


def _record_to_dict(record) -> dict:
    return {
        "filename": record.filename,
        "overall_result": record.overall_result,
        "review_items": [item.model_dump() for item in record.review_items],
        "highlighted_html": record.highlighted_html,
        "created_at": record.created_at,
        "employee_id": record.employee_id,
        "tags": getattr(record, "tags", ""),
    }


@router.get("/reports/{report_id}/export/pdf")
async def export_pdf(report_id: str, request: Request = None,
                    user: User = Depends(get_current_user)):
    client_ip = _client_ip(request)
    if not _rate_check(client_ip, _rate_max_general):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    record = await get_report_async(report_id)
    if record is None:
        raise HTTPException(404, "报告不存在")
    await _ensure_report_access(record, user)
    logger.info("EXPORT client=%s report_id=%s file=%s format=pdf", client_ip, report_id, record.filename)
    await save_audit_log_async(client_ip=client_ip, action="export_pdf", filename=record.filename,
                                detail=f"report_id={report_id}", employee_id=user.employee_id)
    pdf_bytes = await asyncio.to_thread(generate_pdf, _record_to_dict(record))
    base = record.filename.rsplit(".", 1)[0]
    return Response(content=pdf_bytes, media_type="application/pdf",
                    headers={"Content-Disposition": _encoded_filename(f"{base}_审核报告", "pdf")})


@router.get("/reports/{report_id}/export/word")
async def export_word(report_id: str, request: Request = None,
                     user: User = Depends(get_current_user)):
    client_ip = _client_ip(request)
    if not _rate_check(client_ip, _rate_max_general):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    record = await get_report_async(report_id)
    if record is None:
        raise HTTPException(404, "报告不存在")
    await _ensure_report_access(record, user)
    logger.info("EXPORT client=%s report_id=%s file=%s format=word", client_ip, report_id, record.filename)
    await save_audit_log_async(client_ip=client_ip, action="export_word", filename=record.filename,
                                detail=f"report_id={report_id}", employee_id=user.employee_id)
    docx_bytes = await asyncio.to_thread(generate_docx, _record_to_dict(record))
    base = record.filename.rsplit(".", 1)[0]
    return Response(content=docx_bytes,
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": _encoded_filename(f"{base}_审核意见", "docx")})


@router.get("/reports/export/batch")
async def export_batch_excel(ids: str, request: Request = None,
                              user: User = Depends(get_current_user)):
    client_ip = _client_ip(request)
    report_ids = [rid.strip() for rid in ids.split(",") if rid.strip()]
    if not report_ids:
        raise HTTPException(400, "至少需要1个报告ID")
    if len(report_ids) > 50:
        raise HTTPException(400, "单次最多导出50份报告")
    reports = []
    for rid in report_ids:
        record = await get_report_async(rid)
        if record is None:
            continue
        if user.role == "viewer" and record.employee_id != user.employee_id:
            continue
        d = _record_to_dict(record)
        d["report_id"] = rid
        reports.append(d)
    if not reports:
        raise HTTPException(404, "未找到可导出的报告")
    excel_bytes = await asyncio.to_thread(generate_batch_excel, reports)
    logger.info("BATCH_EXPORT client=%s count=%d by=%s", client_ip, len(reports), user.employee_id)
    await save_audit_log_async(client_ip=client_ip, action="batch_export_excel",
                               filename=f"{len(reports)} reports",
                               detail=f"ids={ids}", employee_id=user.employee_id)
    filename = f"batch_export_{len(reports)}.xlsx"
    return Response(content=excel_bytes,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


# ═══════════════════════════════════════════════════════════════════════════
# Tags CRUD
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/tags")
async def list_tags(user: User = Depends(get_current_user)):
    return await get_tags_async()


@router.post("/tags")
async def create_tag(body: TagCreateRequest, user: User = Depends(get_current_user)):
    if not body.name.strip():
        raise HTTPException(400, "标签名称不能为空")
    tag_id = await create_tag_async(body.name.strip(), user.employee_id)
    return {"id": tag_id, "name": body.name.strip()}


@router.put("/tags/{tag_id}")
async def update_tag_endpoint(tag_id: str, body: TagUpdateRequest,
                               user: User = Depends(get_current_user)):
    if not body.name.strip():
        raise HTTPException(400, "标签名称不能为空")
    ok = await update_tag_async(tag_id, body.name.strip())
    if not ok:
        raise HTTPException(404, "标签不存在")
    return {"updated": True}


@router.delete("/tags/{tag_id}")
async def delete_tag_endpoint(tag_id: str, user: User = Depends(get_current_user)):
    ok = await delete_tag_async(tag_id)
    if not ok:
        raise HTTPException(404, "标签不存在")
    return {"deleted": True}


# ═══════════════════════════════════════════════════════════════════════════
# Frontend log ingestion
# ═══════════════════════════════════════════════════════════════════════════


@router.post("/logs/batch")
async def ingest_frontend_logs(body: FrontendLogBatchRequest,
                                user: User = Depends(require_role("admin", "reviewer"))):
    flog = get_frontend_logger()
    for entry in body.logs:
        flog.info(
            "{ts} | {level} | {msg} | reqId={reqId} | module={module} | userId={userId} | ctx={ctx} | error={error}".format(
                ts=entry.ts,
                level=entry.level,
                msg=entry.msg,
                reqId=entry.reqId or "-",
                module=entry.module,
                userId=entry.userId or "-",
                ctx=entry.ctx or "",
                error=entry.error or "",
            )
        )
    return {"ingested": len(body.logs)}


# ═══════════════════════════════════════════════════════════════════════════
# Audit logs
# ═══════════════════════════════════════════════════════════════════════════


@router.get("/logs", response_model=AuditLogListResponse)
async def get_audit_log(limit: int = 100, offset: int = 0,
                         action: str = "", ip: str = "",
                         employee_id: str = "",
                         date_from: str = "", date_to: str = "",
                         user: User = Depends(get_current_user)):
    entries, total = await get_audit_logs_async(limit=limit, offset=offset,
                                    action=action, ip=ip,
                                    employee_id=employee_id,
                                    date_from=date_from, date_to=date_to)
    return AuditLogListResponse(entries=entries, total=total)


@router.on_event("startup")
async def startup():
    init_db()
    seed_admin()
    from database import seed_builtin_standards, seed_default_settings
    seed_builtin_standards()
    seed_default_settings()
