import os
from dotenv import load_dotenv
from utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
_DEEPSEEK_BASE_URL_ENV = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# DeepSeek V4 — 1M context, 384K max output.  Flash is the cost-effective default.
# V3 model "deepseek-chat" will be deprecated 2026-07-24.
# Set DEEPSEEK_MODEL=deepseek-v4-pro for high-accuracy extraction.
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
DEEPSEEK_FLASH_MODEL = os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash")
DEEPSEEK_PRO_MODEL = os.getenv("DEEPSEEK_PRO_MODEL", "deepseek-v4-pro")

JWT_SECRET = os.getenv("JWT_SECRET", "emc-review-dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
_JWT_EXPIRE_HOURS_ENV = int(os.getenv("JWT_EXPIRE_HOURS", "168"))  # 7 days

LOG_ENV = os.getenv("LOG_ENV", "dev").lower()
if LOG_ENV == "prod" and JWT_SECRET == "emc-review-dev-secret-change-in-production":
    raise RuntimeError("生产环境必须配置独立的 JWT_SECRET")


def _db_get(key: str, fallback: str = "") -> str:
    """Lazy-load a setting from the DB, falling back to the given value."""
    try:
        from database import get_setting
        val = get_setting(key)
        if val:
            return val
    except Exception as exc:
        logger.warning(
            "config_db_fallback",
            setting_key=key,
            error_type=type(exc).__name__,
        )
    return fallback


def get_jwt_expire_hours() -> int:
    try:
        return int(_db_get("jwt_expire_hours", str(_JWT_EXPIRE_HOURS_ENV)))
    except (ValueError, TypeError):
        return _JWT_EXPIRE_HOURS_ENV


def get_deepseek_base_url() -> str:
    return _db_get("deepseek_base_url", _DEEPSEEK_BASE_URL_ENV)


def _get_positive_int(key: str, fallback: int) -> int:
    try:
        value = int(_db_get(key, str(fallback)))
        return value if value > 0 else fallback
    except (ValueError, TypeError):
        return fallback


def get_max_upload_bytes() -> int:
    return _get_positive_int("max_upload_bytes", MAX_UPLOAD_BYTES)


def get_batch_max_files() -> int:
    return _get_positive_int("batch_max_files", 10)


# Module-level alias for backward compatibility (auth.py uses at import time)
JWT_EXPIRE_HOURS = _JWT_EXPIRE_HOURS_ENV
DEEPSEEK_BASE_URL = _DEEPSEEK_BASE_URL_ENV

# ── AI Extraction settings ──────────────────────────────────────────────
# Maximum total time for a single document extraction (seconds)
EXTRACTION_TOTAL_TIMEOUT_SEC = int(os.getenv("EXTRACTION_TOTAL_TIMEOUT_SEC", "900"))  # 15 min
# Maximum concurrent AI extraction tasks (prevents API overload)
MAX_CONCURRENT_EXTRACTIONS = int(os.getenv("MAX_CONCURRENT_EXTRACTIONS", "3"))
# Maximum concurrent AI API calls within a single multi-pass extraction
MAX_CONCURRENT_AI_CALLS = int(os.getenv("MAX_CONCURRENT_AI_CALLS", "4"))

# Upload/archive resource limits. These are enforced before persistence or
# decompression so an authenticated user cannot exhaust RAM, disk, or SQLite.
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
MAX_ZIP_FILES = int(os.getenv("MAX_ZIP_FILES", "2000"))
MAX_ZIP_UNCOMPRESSED_BYTES = int(
    os.getenv("MAX_ZIP_UNCOMPRESSED_BYTES", str(1024 * 1024 * 1024))
)
MAX_ZIP_COMPRESSION_RATIO = float(os.getenv("MAX_ZIP_COMPRESSION_RATIO", "200"))

# ── Cross-validation settings ───────────────────────────────────────────
# Enable LLM-powered deep semantic audit during cross-validation.
# Set to "false" to disable (reduces API cost and latency).
ENABLE_LLM_AUDIT = os.getenv("ENABLE_LLM_AUDIT", "true").lower() == "true"
# Maximum allowed span between first and last test date (days).
MAX_TEST_DATE_SPAN_DAYS = int(os.getenv("MAX_TEST_DATE_SPAN_DAYS", "365"))
