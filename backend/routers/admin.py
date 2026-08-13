import json
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Depends, Query, Request

from auth import require_role
from database import (
    get_all_settings_async, set_settings_batch_async,
    get_set_stats_async,
    save_audit_log_async,
)
from models import SystemSettings, SettingsUpdateRequest, User
from utils.logger import LOG_DIR, get_logger

logger = get_logger(__name__)

router = APIRouter()


def _parse_app_log_line(line: str) -> dict | None:
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _parse_frontend_log_line(line: str) -> dict | None:
    """Parse frontend.jsonl: ts | level | msg | key=value ..."""
    try:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            return None
        entry: dict = {
            "source": "frontend",
            "timestamp": parts[0],
            "level": parts[1],
            "event": parts[2],
        }
        for p in parts[3:]:
            if "=" not in p:
                continue
            k, v = p.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "reqId":
                entry["reqId"] = v
            elif k == "module":
                entry["module"] = v
            elif k == "userId":
                entry["userId"] = v
            elif k == "ctx":
                entry["ctx"] = v
            elif k == "error":
                entry["error"] = v
        return entry
    except Exception:
        return None


def _read_logs(
    source: str, level: str | None, req_id: str | None,
    keyword: str | None, limit: int, offset: int,
) -> tuple[list[dict], int]:
    files: list[tuple[str, str]] = []
    if source in ("backend", "all"):
        f = LOG_DIR / "app.jsonl"
        if f.exists():
            files.append((str(f), "backend"))
    if source in ("frontend", "all"):
        f = LOG_DIR / "frontend.jsonl"
        if f.exists():
            files.append((str(f), "frontend"))

    results: list[dict] = []
    for fpath, ftype in files:
        try:
            with open(fpath, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    entry = _parse_app_log_line(line) if ftype == "backend" else _parse_frontend_log_line(line)
                    if entry is None:
                        continue
                    entry["source"] = ftype

                    if level and entry.get("level", "").upper() != level.upper():
                        continue
                    if req_id and req_id not in entry.get("reqId", ""):
                        continue
                    if keyword:
                        if keyword.lower() not in json.dumps(entry).lower():
                            continue

                    results.append(entry)
        except Exception:
            continue

    results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    total = len(results)
    return results[offset:offset + limit], total


@router.get("/admin/settings", response_model=SystemSettings)
async def get_settings(_user: User = Depends(require_role("admin"))):
    settings = await get_all_settings_async()
    return SystemSettings(settings=settings)


@router.put("/admin/settings", response_model=SystemSettings)
async def update_settings(
    body: SettingsUpdateRequest, request: Request = None,
    _user: User = Depends(require_role("admin"))
):
    allowed = {
        "jwt_expire_hours", "max_upload_bytes", "rate_max_upload",
        "rate_max_general", "rate_window_sec", "batch_max_files",
        "deepseek_base_url",
    }
    unknown = sorted(set(body.settings) - allowed)
    if unknown:
        raise HTTPException(422, f"不支持的配置项: {', '.join(unknown)}")
    numeric_bounds = {
        "jwt_expire_hours": (1, 24 * 365),
        "max_upload_bytes": (1024, 2 * 1024 * 1024 * 1024),
        "rate_max_upload": (1, 10000),
        "rate_max_general": (1, 100000),
        "rate_window_sec": (1, 86400),
        "batch_max_files": (1, 500),
    }
    for key, (minimum, maximum) in numeric_bounds.items():
        if key not in body.settings:
            continue
        try:
            value = int(body.settings[key])
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"{key} 必须是整数") from exc
        if not minimum <= value <= maximum:
            raise HTTPException(422, f"{key} 必须在 {minimum} 到 {maximum} 之间")
    if "deepseek_base_url" in body.settings:
        parsed = urlparse(body.settings["deepseek_base_url"].strip())
        if parsed.scheme != "https" or not parsed.netloc:
            raise HTTPException(422, "DeepSeek API 地址必须是有效的 HTTPS 地址")

    client_ip = request.client.host if request and request.client else "unknown"
    old_settings = await get_all_settings_async()
    await set_settings_batch_async(body.settings)
    settings = await get_all_settings_async()

    # Log changed keys
    changed_keys = [k for k, v in body.settings.items()
                    if old_settings.get(k) != v]
    if changed_keys:
        logger.warning("settings_updated", changed_keys=changed_keys,
                       by=_user.employee_id, client_ip=client_ip)
        await save_audit_log_async(
            client_ip=client_ip, action="update_settings",
            filename="system_settings",
            detail=json.dumps({"changed_keys": changed_keys}),
            employee_id=_user.employee_id,
        )
    return SystemSettings(settings=settings)


@router.get("/admin/stats")
async def get_admin_stats(_user: User = Depends(require_role("admin"))):
    """Administrative aggregate statistics for the unified task model."""
    return await get_set_stats_async()


@router.get("/admin/logs/view")
async def view_logs(
    source: str = Query("all", description="backend | frontend | all"),
    level: str | None = Query(None, description="DEBUG | INFO | WARN | ERROR"),
    reqId: str | None = Query(None),
    keyword: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    _user: User = Depends(require_role("admin")),
):
    rows, total = _read_logs(
        source=source, level=level, req_id=reqId,
        keyword=keyword, limit=limit, offset=offset,
    )
    return {"logs": rows, "total": total, "offset": offset, "limit": limit}
