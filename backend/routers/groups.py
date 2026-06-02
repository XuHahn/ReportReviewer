from fastapi import APIRouter, Depends, HTTPException
from models import User
from auth import get_current_user, require_role
from database import (create_project_group_async, update_project_group_async,
                       delete_project_group_async, get_project_groups_async,
                       get_project_group_async)

from pydantic import BaseModel

router = APIRouter(prefix="/groups", tags=["groups"])


class CreateGroupRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "其他"
    member_ids: list[str] = []


class UpdateGroupRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    member_ids: list[str] | None = None


@router.get("")
async def list_groups(user: User = Depends(get_current_user)):
    return await get_project_groups_async()


@router.get("/{gid}")
async def get_group(gid: str, user: User = Depends(get_current_user)):
    g = await get_project_group_async(gid)
    if not g:
        raise HTTPException(404, "项目组不存在")
    return g


@router.post("")
async def create_group(body: CreateGroupRequest,
                        user: User = Depends(require_role("admin", "reviewer"))):
    if not body.name.strip():
        raise HTTPException(400, "项目组名称不能为空")
    gid = await create_project_group_async(body.name.strip(), body.description, user.employee_id, body.member_ids, body.category)
    return {"id": gid}


@router.put("/{gid}")
async def update_group(gid: str, body: UpdateGroupRequest,
                        user: User = Depends(require_role("admin", "reviewer"))):
    ok = await update_project_group_async(gid, body.name, body.description, body.member_ids, body.category)
    if not ok:
        raise HTTPException(404, "项目组不存在")
    return {"ok": True}


@router.delete("/{gid}")
async def delete_group(gid: str, user: User = Depends(require_role("admin", "reviewer"))):
    ok = await delete_project_group_async(gid)
    if not ok:
        raise HTTPException(404, "项目组不存在")
    return {"ok": True}
