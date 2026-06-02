import jwt
from datetime import datetime, timedelta, timezone
from fastapi import Header, HTTPException, Depends
from config import JWT_SECRET, JWT_ALGORITHM, get_jwt_expire_hours
from models import User
import database


def create_access_token(employee_id: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=get_jwt_expire_hours())
    payload = {"employee_id": employee_id, "role": role, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


async def get_current_user(
    authorization: str = Header(None),
) -> User:
    raw = None
    if authorization and authorization.startswith("Bearer "):
        raw = authorization[7:]

    if not raw:
        raise HTTPException(status_code=401, detail="未提供认证令牌")

    payload = decode_token(raw)
    if payload is None:
        raise HTTPException(status_code=401, detail="认证令牌无效或已过期")

    employee_id = payload.get("employee_id")
    if not employee_id:
        raise HTTPException(status_code=401, detail="认证令牌无效")

    user = await database.get_user_async(employee_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")

    token_role = payload.get("role")
    if token_role and token_role != user.role:
        raise HTTPException(status_code=401, detail="角色已变更，请重新登录")

    return user


def require_role(*roles: str):
    """Dependency factory: only allow users with one of the given roles."""
    async def role_checker(user: User = Depends(get_current_user)):
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="权限不足")
        return user
    return role_checker
