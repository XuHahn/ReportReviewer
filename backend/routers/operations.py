"""Small shared APIs for project labels, audit history and client diagnostics."""

from fastapi import APIRouter, Depends, HTTPException, Request

from auth import get_current_user, require_role
from database import (
    create_tag_async,
    delete_tag_async,
    get_audit_logs_async,
    get_tags_async,
    save_audit_log_async,
    update_tag_async,
)
from models import AuditLogListResponse, FrontendLogBatchRequest, TagCreateRequest, TagUpdateRequest, User
from utils.logger import get_frontend_logger, get_logger


router = APIRouter(tags=["operations"])
logger = get_logger(__name__)


def _client_ip(request: Request | None) -> str:
    return request.client.host if request and request.client else "unknown"


@router.get("/tags")
async def list_tags(_user: User = Depends(get_current_user)):
    return await get_tags_async()


@router.post("/tags")
async def create_tag(
    body: TagCreateRequest, request: Request = None,
    user: User = Depends(require_role("admin", "reviewer")),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "标签名称不能为空")
    tag_id = await create_tag_async(name, user.employee_id)
    await save_audit_log_async(
        client_ip=_client_ip(request), action="create_tag", filename=name,
        detail=f"tag_id={tag_id}", employee_id=user.employee_id,
    )
    return {"id": tag_id, "name": name}


@router.put("/tags/{tag_id}")
async def update_tag(
    tag_id: str, body: TagUpdateRequest, request: Request = None,
    user: User = Depends(require_role("admin", "reviewer")),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "标签名称不能为空")
    if not await update_tag_async(tag_id, name):
        raise HTTPException(404, "标签不存在")
    await save_audit_log_async(
        client_ip=_client_ip(request), action="update_tag", filename=tag_id,
        detail=f"new_name={name}", employee_id=user.employee_id,
    )
    return {"updated": True}


@router.delete("/tags/{tag_id}")
async def delete_tag(
    tag_id: str, request: Request = None,
    user: User = Depends(require_role("admin", "reviewer")),
):
    if not await delete_tag_async(tag_id):
        raise HTTPException(404, "标签不存在")
    await save_audit_log_async(
        client_ip=_client_ip(request), action="delete_tag", filename=tag_id,
        employee_id=user.employee_id,
    )
    return {"deleted": True}


@router.post("/logs/batch")
async def ingest_frontend_logs(
    body: FrontendLogBatchRequest, user: User = Depends(get_current_user),
):
    output = get_frontend_logger()
    for entry in body.logs:
        output.info(
            "%s | %s | %s | reqId=%s | module=%s | userId=%s | ctx=%s | error=%s",
            entry.ts, entry.level, entry.msg, entry.reqId or "-", entry.module,
            entry.userId or user.employee_id, entry.ctx or "", entry.error or "",
        )
    return {"ingested": len(body.logs)}


@router.get("/logs", response_model=AuditLogListResponse)
async def get_audit_log(
    limit: int = 100, offset: int = 0, action: str = "", ip: str = "",
    employee_id: str = "", date_from: str = "", date_to: str = "",
    _user: User = Depends(require_role("admin")),
):
    entries, total = await get_audit_logs_async(
        limit=limit, offset=offset, action=action, ip=ip,
        employee_id=employee_id, date_from=date_from, date_to=date_to,
    )
    return AuditLogListResponse(entries=entries, total=total)
