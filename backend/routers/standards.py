"""Standard library, knowledge graph review and immutable release APIs."""

import io
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

import database
from auth import get_current_user, require_role
from config import get_max_upload_bytes
from models import (
    StandardCreateRequest,
    StandardRequirementMappingRequest,
    StandardRequirementReviewRequest,
    User,
)
from services.standard_graph import (
    build_standard_graph_background,
    retry_failed_standard_graph_units_background,
)
from services.standard_knowledge import build_standard_knowledge_background
from utils.background_tasks import background_task_running, start_background_task
from utils.upload_utils import read_upload_limited


router = APIRouter(prefix="/standards", tags=["standards"])


def _client_ip(request: Request | None) -> str:
    return request.client.host if request and request.client else "unknown"


async def _standard_or_404(standard_id: str):
    standard = await database.get_standard_async(standard_id)
    if not standard:
        raise HTTPException(404, "标准不存在")
    return standard


@router.get("")
async def list_standards(
    organization: str = "", category: str = "", keyword: str = "",
    _user: User = Depends(get_current_user),
):
    return await database.get_standards_async(organization, category, keyword)


@router.get("/{standard_id}")
async def get_standard(standard_id: str, _user: User = Depends(get_current_user)):
    return await _standard_or_404(standard_id)


@router.post("")
async def create_standard(
    body: StandardCreateRequest, request: Request = None,
    user: User = Depends(require_role("admin", "standard_reviewer")),
):
    standard_id = await database.save_standard_async(
        body.code, body.title, body.organization, body.category,
        body.version, body.clauses,
    )
    await database.save_audit_log_async(
        client_ip=_client_ip(request), action="create_standard",
        filename=body.code, employee_id=user.employee_id,
        detail=json.dumps({"standard_id": standard_id}, ensure_ascii=False),
    )
    return {"id": standard_id}


@router.delete("/{standard_id}")
async def delete_standard(
    standard_id: str, request: Request = None,
    user: User = Depends(require_role("admin", "standard_reviewer")),
):
    if not await database.delete_standard_async(standard_id):
        raise HTTPException(400, "内置标准不能删除，或标准不存在")
    await database.save_audit_log_async(
        client_ip=_client_ip(request), action="delete_standard",
        filename=standard_id, employee_id=user.employee_id,
    )
    return {"deleted": True}


@router.post("/upload")
async def upload_standard(
    file: UploadFile = File(...), code: str = Form(""), title: str = Form(""),
    organization: str = Form(""), category: str = Form(""), version: str = Form(""),
    request: Request = None,
    user: User = Depends(require_role("admin", "standard_reviewer")),
):
    filename = file.filename or "standard.pdf"
    if not filename.lower().endswith((".pdf", ".docx", ".txt")):
        raise HTTPException(415, "标准仅支持 PDF、DOCX 或 TXT")
    content = await read_upload_limited(file, get_max_upload_bytes())
    if not content:
        raise HTTPException(400, "标准文件为空")
    standard_id, duplicate = await database.create_standard_asset_async(
        content, filename, code=code, title=title, organization=organization,
        category=category, version=version, created_by=user.employee_id,
    )
    if not duplicate:
        start_background_task(
            build_standard_knowledge_background(standard_id),
            name=f"standard-knowledge-{standard_id}", key=f"standard-knowledge:{standard_id}",
        )
    await database.save_audit_log_async(
        client_ip=_client_ip(request), action="upload_standard", filename=filename,
        file_size_kb=len(content) // 1024, employee_id=user.employee_id,
        detail=json.dumps({"standard_id": standard_id, "duplicate": duplicate}),
    )
    standard = await _standard_or_404(standard_id)
    return {"id": standard_id, "duplicate": duplicate, "knowledge_status": standard.knowledge_status}


@router.get("/{standard_id}/file")
async def download_standard_file(
    standard_id: str, _user: User = Depends(get_current_user),
):
    record = await database.get_standard_file_async(standard_id)
    if not record:
        raise HTTPException(404, "标准原文件不存在")
    filename, content = record
    encoded = quote(filename, safe="")
    return StreamingResponse(
        io.BytesIO(content), media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded}"},
    )


@router.get("/{standard_id}/preview")
async def preview_standard_pdf(
    standard_id: str, _user: User = Depends(get_current_user),
):
    """Return a PDF for in-browser viewing without triggering a download."""
    record = await database.get_standard_file_async(standard_id)
    if not record:
        raise HTTPException(404, "标准原文件不存在")
    filename, content = record
    if not filename.lower().endswith(".pdf") or not content.startswith(b"%PDF-"):
        raise HTTPException(415, "当前标准不是可预览的 PDF 文件")
    encoded = quote(filename, safe="")
    return StreamingResponse(
        io.BytesIO(content), media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{encoded}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/{standard_id}/knowledge")
async def get_standard_knowledge(
    standard_id: str, query: str = "", limit: int = Query(50, ge=1, le=1000),
    _user: User = Depends(get_current_user),
):
    standard = await _standard_or_404(standard_id)
    chunks = await database.get_standard_knowledge_chunks_async(standard_id, query, limit)
    return {"standard": standard, "chunks": chunks, "total": len(chunks)}


@router.post("/{standard_id}/knowledge/retry")
async def retry_standard_knowledge(
    standard_id: str, user: User = Depends(require_role("admin", "standard_reviewer")),
):
    await _standard_or_404(standard_id)
    await database.update_standard_knowledge_async(standard_id, status="pending", error="")
    start_background_task(
        build_standard_knowledge_background(standard_id),
        name=f"standard-knowledge-{standard_id}", key=f"standard-knowledge:{standard_id}",
    )
    return {"id": standard_id, "knowledge_status": "pending"}


@router.post("/{standard_id}/graph/rebuild")
async def rebuild_standard_graph(
    standard_id: str, user: User = Depends(require_role("admin", "standard_reviewer")),
):
    standard = await _standard_or_404(standard_id)
    if standard.knowledge_status != "ready":
        raise HTTPException(409, "标准原文知识化尚未完成")
    task_key = f"standard-graph:{standard_id}"
    if standard.graph_status == "extracting" and background_task_running(task_key):
        raise HTTPException(409, "该标准的要求识别任务正在运行")
    await database.update_standard_graph_status_async(
        standard_id, "extracting", "", standard.graph_meta,
    )
    start_background_task(
        build_standard_graph_background(standard_id),
        name=f"standard-graph-{standard_id}", key=task_key,
    )
    return {"id": standard_id, "graph_status": "extracting"}


@router.post("/{standard_id}/graph/retry-failed")
async def retry_failed_graph_units(
    standard_id: str, user: User = Depends(require_role("admin", "standard_reviewer")),
):
    standard = await _standard_or_404(standard_id)
    failed_ids = [
        str(item) for item in (standard.graph_meta or {}).get("failed_unit_ids", [])
    ]
    if not failed_ids:
        raise HTTPException(409, "没有需要重试的失败原文单元")
    task_key = f"standard-graph:{standard_id}"
    if standard.graph_status == "extracting" and background_task_running(task_key):
        raise HTTPException(409, "该标准的失败单元恢复任务正在运行")
    await database.update_standard_graph_status_async(
        standard_id, "extracting", "", standard.graph_meta,
    )
    start_background_task(
        retry_failed_standard_graph_units_background(standard_id),
        name=f"standard-graph-retry-{standard_id}", key=task_key,
    )
    return {
        "id": standard_id, "graph_status": "extracting",
        "remaining_failed_unit_ids": failed_ids,
    }


@router.get("/{standard_id}/graph")
async def get_standard_graph(
    standard_id: str, _user: User = Depends(get_current_user),
):
    standard = await _standard_or_404(standard_id)
    graph = await database.get_standard_graph_async(standard_id)
    return {"standard": standard, **graph}


@router.patch("/{standard_id}/graph/requirements/{requirement_id}")
async def review_requirement(
    standard_id: str, requirement_id: str, body: StandardRequirementReviewRequest,
    user: User = Depends(require_role("admin", "standard_reviewer")),
):
    updates = body.model_dump(exclude={"review_status", "comment"}, exclude_none=True)
    if "parameters" in updates:
        updates["parameters"] = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in (body.parameters or [])
        ]
    try:
        result = await database.review_standard_requirement_async(
            standard_id, requirement_id, body.review_status, body.comment,
            user.employee_id, updates,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not result:
        raise HTTPException(404, "标准要求不存在")
    return result


@router.post("/{standard_id}/graph/publish")
async def publish_graph(
    standard_id: str, user: User = Depends(require_role("admin", "standard_reviewer")),
):
    try:
        counts = await database.publish_standard_graph_async(standard_id, user.employee_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": standard_id, "graph_status": "published", "counts": counts}


@router.get("/{standard_id}/releases")
async def list_releases(
    standard_id: str, _user: User = Depends(get_current_user),
):
    await _standard_or_404(standard_id)
    return {"standard_id": standard_id, "releases": await database.get_standard_releases_async(standard_id)}


@router.get("/{standard_id}/releases/{release_id}")
async def get_release(
    standard_id: str, release_id: str, _user: User = Depends(get_current_user),
):
    release = await database.get_standard_release_async(release_id)
    if not release or release["standard_id"] != standard_id:
        raise HTTPException(404, "标准发布版本不存在")
    return release


@router.post("/{standard_id}/mappings")
async def create_mapping(
    standard_id: str, body: StandardRequirementMappingRequest,
    user: User = Depends(require_role("admin", "standard_reviewer")),
):
    try:
        return await database.save_standard_requirement_mapping_async(
            standard_id, body.release_id, body.source_name, body.mapping_type,
            body.requirement_id, body.scope_type, body.scope_value,
            body.rationale, user.employee_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
