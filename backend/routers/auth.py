from fastapi import APIRouter, Depends, HTTPException
from models import User, LoginRequest, LoginResponse, UserCreateRequest, UserUpdateRoleRequest, UserListResponse, UpdateNameRequest
from auth import create_access_token, get_current_user, require_role
import database

router = APIRouter(tags=["auth"])
user_router = APIRouter(tags=["users"])


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    employee_id = body.employee_id.strip().upper()
    user = await database.get_user_async(employee_id)
    if user is None:
        raise HTTPException(status_code=401, detail="工号不存在")

    token = create_access_token(user.employee_id, user.role)
    return LoginResponse(token=token, user=user)


@router.get("/auth/me", response_model=User)
async def me(user: User = Depends(get_current_user)):
    return user


@user_router.get("/users", response_model=UserListResponse)
async def list_users(user: User = Depends(get_current_user)):
    users = await database.get_all_users_async()
    return UserListResponse(users=users, total=len(users))


@user_router.post("/users", response_model=User)
async def create_user(body: UserCreateRequest, user: User = Depends(require_role("admin"))):
    employee_id = body.employee_id.strip().upper()
    success = await database.create_user_async(employee_id, body.role, body.name)
    if not success:
        raise HTTPException(status_code=409, detail="工号已存在")
    created = await database.get_user_async(employee_id)
    return created


@user_router.put("/users/{employee_id}/role", response_model=User)
async def update_user_role(employee_id: str, body: UserUpdateRoleRequest,
                           user: User = Depends(require_role("admin"))):
    if body.role not in ("admin", "reviewer", "viewer"):
        raise HTTPException(status_code=422, detail="无效的角色，有效值: admin, reviewer, viewer")
    employee_id = employee_id.strip().upper()
    success = await database.update_user_role_async(employee_id, body.role)
    if not success:
        raise HTTPException(status_code=404, detail="用户不存在")
    updated = await database.get_user_async(employee_id)
    return updated


@user_router.put("/users/{employee_id}/name", response_model=User)
async def update_user_name_endpoint(employee_id: str, body: UpdateNameRequest,
                           user: User = Depends(require_role("admin"))):
    employee_id = employee_id.strip().upper()
    name = body.name.strip()
    success = await database.update_user_name_async(employee_id, name)
    if not success:
        raise HTTPException(status_code=404, detail="用户不存在")
    updated = await database.get_user_async(employee_id)
    return updated


@user_router.delete("/users/{employee_id}")
async def delete_user_endpoint(employee_id: str,
                                user: User = Depends(require_role("admin"))):
    employee_id = employee_id.strip().upper()
    if employee_id == user.employee_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    success = await database.delete_user_async(employee_id)
    if not success:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"ok": True}
