import os
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
_DEEPSEEK_BASE_URL_ENV = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

if not DEEPSEEK_API_KEY:
    raise RuntimeError("DEEPSEEK_API_KEY 环境变量未设置，请在 .env 文件中配置")

JWT_SECRET = os.getenv("JWT_SECRET", "emc-review-dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS_ENV = int(os.getenv("JWT_EXPIRE_HOURS", "168"))  # 7 days


def _db_get(key: str, fallback: str = "") -> str:
    """Lazy-load a setting from the DB, falling back to the given value."""
    try:
        from database import get_setting
        val = get_setting(key)
        if val:
            return val
    except Exception:
        pass
    return fallback


def get_jwt_expire_hours() -> int:
    try:
        return int(_db_get("jwt_expire_hours", str(_JWT_EXPIRE_HOURS_ENV)))
    except (ValueError, TypeError):
        return _JWT_EXPIRE_HOURS_ENV


def get_deepseek_base_url() -> str:
    return _db_get("deepseek_base_url", _DEEPSEEK_BASE_URL_ENV)


# Module-level alias for backward compatibility (auth.py uses at import time)
JWT_EXPIRE_HOURS = _JWT_EXPIRE_HOURS_ENV
DEEPSEEK_BASE_URL = _DEEPSEEK_BASE_URL_ENV
