import os
import asyncio
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from utils.logger import init_logging, get_logger
from utils.logging_middleware import LoggingMiddleware
from routers.operations import router as operations_router
from routers.standards import router as standards_router
from routers.auth import router as auth_router, user_router
from routers.admin import router as admin_router
from routers.groups import router as groups_router
from routers.document_set import router as docset_router

init_logging(env=os.getenv("LOG_ENV", "dev"))
logger = get_logger("emc-review")

_VISION_HEALTH_CACHE: tuple[float, dict] | None = None
_VISION_HEALTH_LOCK = asyncio.Lock()


async def _vision_health() -> dict:
    """Return a fast, non-blocking status for the optional local vision server.

    The API must remain healthy while Qwen is loading.  This probe therefore
    reports ``preparing`` on connection failures instead of making the whole
    backend startup fail, and caches the result briefly so a polling UI does
    not create a request storm during model warm-up.
    """
    global _VISION_HEALTH_CACHE
    now = time.monotonic()
    if _VISION_HEALTH_CACHE and now - _VISION_HEALTH_CACHE[0] < 1.5:
        return _VISION_HEALTH_CACHE[1]

    async with _VISION_HEALTH_LOCK:
        now = time.monotonic()
        if _VISION_HEALTH_CACHE and now - _VISION_HEALTH_CACHE[0] < 1.5:
            return _VISION_HEALTH_CACHE[1]
        from services.unified_model_gateway import UnifiedModelGateway, UnifiedModelUnavailable

        gateway = UnifiedModelGateway()
        try:
            route = gateway.resolve("visual_extraction")
        except UnifiedModelUnavailable as exc:
            result = {
                "status": "unavailable",
                "configured": False,
                "model": "",
                "reason": str(exc),
            }
            _VISION_HEALTH_CACHE = (time.monotonic(), result)
            return result

        if route.provider == "test":
            result = {"status": "ready", "configured": True, "model": route.model}
            _VISION_HEALTH_CACHE = (time.monotonic(), result)
            return result

        headers = {}
        if route.api_key and route.api_key != "local-no-key":
            headers["Authorization"] = f"Bearer {route.api_key}"
        try:
            async with httpx.AsyncClient(timeout=1.0, follow_redirects=False) as client:
                response = await client.get(
                    route.base_url.rstrip("/") + "/models", headers=headers,
                )
                response.raise_for_status()
            result = {
                "status": "ready",
                "configured": True,
                "model": route.model,
            }
        except (httpx.HTTPError, ValueError) as exc:
            # A 503 is the normal warm-up response for a model server that is
            # alive but still loading. Authentication/configuration errors are
            # actionable outages and should not be presented as an endless
            # loading state.
            status = "preparing"
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code != 503:
                status = "unavailable"
            elif isinstance(exc, ValueError):
                status = "unavailable"
            result = {
                "status": status,
                "configured": True,
                "model": route.model,
                "reason": f"{type(exc).__name__}",
            }
        _VISION_HEALTH_CACHE = (time.monotonic(), result)
        return result


async def recover_stuck_extractions():
    """Restart recoverable extraction jobs after a service restart."""
    from utils.background_tasks import start_background_task

    try:
        from database import get_stuck_documents, get_file_content
        stuck = await asyncio.to_thread(get_stuck_documents)
        if stuck:
            logger.info("startup_recovery", stuck_count=len(stuck))
            for doc in stuck:
                doc_id = doc["doc_id"]
                set_id = doc["set_id"]
                doc_type = doc["doc_type"]
                filename = doc.get("filename", "")
                file_bytes = await asyncio.to_thread(get_file_content, doc_id)
                if file_bytes:
                    from routers.document_set import _extract_with_timeout
                    start_background_task(
                        _extract_with_timeout(set_id, doc_id, doc_type, file_bytes, filename),
                        name=f"recover-document-{doc_id}", key=f"document-extraction:{doc_id}",
                    )
                    logger.info("startup_recovery_retriggered", doc_id=doc_id,
                               set_id=set_id, doc_type=doc_type)
                else:
                    logger.warning("startup_recovery_no_content", doc_id=doc_id,
                                  doc_type=doc_type,
                                  extra={"action": "marking_as_failed"})
                    # Mark as failed — no file content to recover with
                    from database import update_doc_extraction_status
                    await asyncio.to_thread(
                        update_doc_extraction_status, doc_id, "failed",
                        "", "", "提取任务丢失且无备份文件可恢复（可能服务器重启导致）"
                    )
    except Exception as e:
        logger.error("startup_recovery_failed", error=str(e))

    try:
        from database import get_stuck_standards_async
        stuck_standards = await get_stuck_standards_async()
        if stuck_standards:
            from services.standard_knowledge import build_standard_knowledge_background
            logger.info("standard_startup_recovery", stuck_count=len(stuck_standards))
            for standard in stuck_standards:
                start_background_task(
                    build_standard_knowledge_background(standard["id"]),
                    name=f"recover-standard-knowledge-{standard['id']}",
                    key=f"standard-knowledge:{standard['id']}",
                )
                logger.info(
                    "standard_startup_recovery_retriggered",
                    standard_id=standard["id"],
                    filename=standard["source_filename"],
                    previous_status=standard["knowledge_status"],
                )
    except Exception as e:
        logger.error("standard_startup_recovery_failed", error=str(e))

    try:
        from database import get_stuck_standard_graphs_async
        stuck_graphs = await get_stuck_standard_graphs_async()
        if stuck_graphs:
            from services.standard_graph import build_standard_graph_background
            logger.info("standard_graph_startup_recovery", stuck_count=len(stuck_graphs))
            for standard_id in stuck_graphs:
                start_background_task(
                    build_standard_graph_background(standard_id),
                    name=f"recover-standard-graph-{standard_id}",
                    key=f"standard-graph:{standard_id}",
                )
    except Exception as e:
        logger.error("standard_graph_startup_recovery_failed", error=str(e))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from database import init_db, seed_admin, seed_builtin_standards, seed_default_settings
    from utils.background_tasks import shutdown_background_tasks

    await asyncio.to_thread(init_db)
    await asyncio.to_thread(seed_admin)
    if os.getenv("SEED_DEMO_STANDARDS", "false").lower() == "true":
        await asyncio.to_thread(seed_builtin_standards)
    await asyncio.to_thread(seed_default_settings)
    await recover_stuck_extractions()
    try:
        yield
    finally:
        from routers.document_set import shutdown_extraction_tasks
        await shutdown_extraction_tasks()
        await shutdown_background_tasks()


app = FastAPI(title="EMC报告审核系统", version="1.0.0", lifespan=lifespan)

origins = os.getenv(
    "CORS_ORIGINS", "http://localhost:5174,http://127.0.0.1:5174",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)
app.add_middleware(LoggingMiddleware)

app.include_router(operations_router, prefix="/api")
app.include_router(standards_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(user_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(groups_router, prefix="/api")
app.include_router(docset_router, prefix="/api")


@app.get("/api/health")
async def health():
    # Keep the API itself healthy while the optional local vision model warms
    # up. Clients can use the nested status to gate visual-review actions.
    return {"status": "ok", "vision": await _vision_health()}
