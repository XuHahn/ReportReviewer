from fastapi import APIRouter, Depends, HTTPException, Request
from models import User, LoginRequest, LoginResponse, UserCreateRequest, UserUpdateRoleRequest, UserListResponse, UpdateNameRequest
from auth import create_access_token, get_current_user, require_role
import database
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["auth"])
user_router = APIRouter(tags=["users"])


def _client_ip(request: Request | None) -> str:
    return request.client.host if request and request.client else "unknown"


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request = None):
    employee_id = body.employee_id.strip().upper()
    client_ip = _client_ip(request)
    user = await database.get_user_async(employee_id)
    if user is None:
        logger.warning("login_failed", employee_id=employee_id, reason="工号不存在",
                       client_ip=client_ip)
        raise HTTPException(status_code=401, detail="工号不存在")

    token = create_access_token(user.employee_id, user.role)
    logger.info("login_success", employee_id=user.employee_id, role=user.role,
                client_ip=client_ip)
    await database.save_audit_log_async(
        client_ip=client_ip, action="login", filename="",
        employee_id=user.employee_id,
    )
    return LoginResponse(token=token, user=user)


@router.get("/auth/me", response_model=User)
async def me(user: User = Depends(get_current_user)):
    return user


@user_router.get("/users", response_model=UserListResponse)
async def list_users(user: User = Depends(require_role("admin", "reviewer"))):
    users = await database.get_all_users_async()
    return UserListResponse(users=users, total=len(users))


@user_router.post("/users", response_model=User)
async def create_user(body: UserCreateRequest, user: User = Depends(require_role("admin")),
                      request: Request = None):
    employee_id = body.employee_id.strip().upper()
    success = await database.create_user_async(employee_id, body.role, body.name)
    if not success:
        logger.warning("user_create_failed", employee_id=employee_id,
                       reason="工号已存在", by=user.employee_id)
        raise HTTPException(status_code=409, detail="工号已存在")
    created = await database.get_user_async(employee_id)
    client_ip = _client_ip(request)
    logger.info("user_created", employee_id=employee_id, role=body.role,
                name=body.name, by=user.employee_id)
    await database.save_audit_log_async(
        client_ip=client_ip, action="create_user", filename=employee_id,
        detail=f"role={body.role} name={body.name}", employee_id=user.employee_id,
    )
    return created


@user_router.put("/users/{employee_id}/role", response_model=User)
async def update_user_role(employee_id: str, body: UserUpdateRoleRequest,
                           user: User = Depends(require_role("admin")),
                           request: Request = None):
    employee_id = employee_id.strip().upper()
    success = await database.update_user_role_async(employee_id, body.role)
    if not success:
        raise HTTPException(status_code=404, detail="用户不存在")
    updated = await database.get_user_async(employee_id)
    client_ip = _client_ip(request)
    logger.info("user_role_updated", employee_id=employee_id, new_role=body.role,
                by=user.employee_id)
    await database.save_audit_log_async(
        client_ip=client_ip, action="update_user_role", filename=employee_id,
        detail=f"new_role={body.role}", employee_id=user.employee_id,
    )
    return updated


@user_router.put("/users/{employee_id}/name", response_model=User)
async def update_user_name_endpoint(employee_id: str, body: UpdateNameRequest,
                           user: User = Depends(require_role("admin")),
                           request: Request = None):
    employee_id = employee_id.strip().upper()
    name = body.name.strip()
    success = await database.update_user_name_async(employee_id, name)
    if not success:
        raise HTTPException(status_code=404, detail="用户不存在")
    updated = await database.get_user_async(employee_id)
    client_ip = _client_ip(request)
    logger.info("user_name_updated", employee_id=employee_id, new_name=name,
                by=user.employee_id)
    await database.save_audit_log_async(
        client_ip=client_ip, action="update_user_name", filename=employee_id,
        detail=f"new_name={name}", employee_id=user.employee_id,
    )
    return updated


@user_router.delete("/users/{employee_id}")
async def delete_user_endpoint(employee_id: str,
                                user: User = Depends(require_role("admin")),
                                request: Request = None):
    employee_id = employee_id.strip().upper()
    if employee_id == user.employee_id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    success = await database.delete_user_async(employee_id)
    if not success:
        raise HTTPException(status_code=404, detail="用户不存在")
    client_ip = _client_ip(request)
    logger.warning("user_deleted", employee_id=employee_id, by=user.employee_id)
    await database.save_audit_log_async(
        client_ip=client_ip, action="delete_user", filename=employee_id,
        employee_id=user.employee_id,
    )
    return {"ok": True}
