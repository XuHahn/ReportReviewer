from fastapi import APIRouter, Depends, HTTPException, Request
from models import User
from auth import get_current_user, require_role
from database import (create_project_group_async, update_project_group_async,
                       delete_project_group_async, get_project_groups_async,
                       get_project_group_async, save_audit_log_async)
from utils.logger import get_logger

from pydantic import BaseModel, Field
import database

logger = get_logger(__name__)

router = APIRouter(prefix="/groups", tags=["groups"])


def _client_ip(request: Request | None) -> str:
    return request.client.host if request and request.client else "unknown"


async def _require_group_access(gid: str, user: User, *, manage: bool = False) -> dict:
    group = await get_project_group_async(gid)
    if not group:
        raise HTTPException(404, "项目组不存在")
    if user.role == "admin":
        return group
    if user.employee_id not in group.get("members", []):
        raise HTTPException(403, "无权访问此项目组")
    if manage and group.get("created_by") != user.employee_id:
        raise HTTPException(403, "仅项目组创建者或管理员可以修改项目组")
    return group


async def _validate_member_ids(member_ids: list[str] | None) -> None:
    if member_ids is None:
        return
    invalid = []
    for employee_id in set(member_ids):
        if not await database.get_user_async(employee_id):
            invalid.append(employee_id)
    if invalid:
        raise HTTPException(422, f"项目组成员不存在: {', '.join(sorted(invalid))}")


class CreateGroupRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "其他"
    member_ids: list[str] = Field(default_factory=list)


class UpdateGroupRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    member_ids: list[str] | None = None


@router.get("")
async def list_groups(user: User = Depends(get_current_user)):
    return await get_project_groups_async("" if user.role == "admin" else user.employee_id)


@router.get("/{gid}")
async def get_group(gid: str, user: User = Depends(get_current_user)):
    return await _require_group_access(gid, user)


@router.post("")
async def create_group(body: CreateGroupRequest,
                        user: User = Depends(require_role("admin", "reviewer")),
                        request: Request = None):
    if not body.name.strip():
        raise HTTPException(400, "项目组名称不能为空")
    await _validate_member_ids(body.member_ids)
    gid = await create_project_group_async(body.name.strip(), body.description, user.employee_id, body.member_ids, body.category)
    client_ip = _client_ip(request)
    logger.info("group_created", gid=gid, name=body.name, member_count=len(body.member_ids),
                by=user.employee_id)
    await save_audit_log_async(
        client_ip=client_ip, action="create_group", filename=body.name,
        detail=f"gid={gid} members={len(body.member_ids)} category={body.category}",
        employee_id=user.employee_id,
    )
    return {"id": gid}


@router.put("/{gid}")
async def update_group(gid: str, body: UpdateGroupRequest,
                        user: User = Depends(require_role("admin", "reviewer")),
                        request: Request = None):
    await _require_group_access(gid, user, manage=True)
    await _validate_member_ids(body.member_ids)
    ok = await update_project_group_async(gid, body.name, body.description, body.member_ids, body.category)
    if not ok:
        raise HTTPException(404, "项目组不存在")
    client_ip = _client_ip(request)
    changes = [k for k, v in body.model_dump().items() if v is not None]
    logger.info("group_updated", gid=gid, changes=changes, by=user.employee_id)
    await save_audit_log_async(
        client_ip=client_ip, action="update_group", filename=gid,
        detail=f"changes={changes}", employee_id=user.employee_id,
    )
    return {"ok": True}


@router.delete("/{gid}")
async def delete_group(gid: str, user: User = Depends(require_role("admin", "reviewer")),
                       request: Request = None):
    await _require_group_access(gid, user, manage=True)
    ok = await delete_project_group_async(gid)
    if not ok:
        raise HTTPException(404, "项目组不存在")
    client_ip = _client_ip(request)
    logger.warning("group_deleted", gid=gid, by=user.employee_id)
    await save_audit_log_async(
        client_ip=client_ip, action="delete_group", filename=gid,
        employee_id=user.employee_id,
    )
    return {"ok": True}
