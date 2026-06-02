import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query

from auth import require_role
from database import (
    get_all_settings_async, set_settings_batch_async,
    get_stats_async, get_audit_logs_async, get_all_users_async,
)
from models import SystemSettings, SettingsUpdateRequest, AdminStatsResponse, User
from utils.logger import LOG_DIR

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
    body: SettingsUpdateRequest, _user: User = Depends(require_role("admin"))
):
    await set_settings_batch_async(body.settings)
    settings = await get_all_settings_async()
    return SystemSettings(settings=settings)


@router.get("/admin/stats", response_model=AdminStatsResponse)
async def get_admin_stats(_user: User = Depends(require_role("admin"))):
    base = await get_stats_async()

    users = await get_all_users_async()
    total_users = len(users)

    today = datetime.now().strftime("%Y-%m-%d")
    today_uploads = 0
    for t in base.get("trends", []):
        if t.get("date") == today:
            today_uploads = t.get("uploads", 0)
            break

    recent_entries, _ = await get_audit_logs_async(limit=10)

    return AdminStatsResponse(
        overview=base["overview"],
        pass_fail=base["pass_fail"],
        severity_dist=base["severity_dist"],
        trends=base["trends"],
        top_locations=base["top_locations"],
        top_actions=base["top_actions"],
        total_users=total_users,
        today_uploads=today_uploads,
        recent_activity=recent_entries,
    )


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
