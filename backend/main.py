import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from utils.logger import init_logging, get_logger
from utils.logging_middleware import LoggingMiddleware
from routers.report import router as report_router
from routers.auth import router as auth_router, user_router
from routers.admin import router as admin_router
from routers.groups import router as groups_router

init_logging(env=os.getenv("LOG_ENV", "dev"))
logger = get_logger("emc-review")

app = FastAPI(title="EMC报告审核系统", version="1.0.0")

origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:5174").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id"],
)

app.add_middleware(LoggingMiddleware)


app.include_router(report_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(user_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
app.include_router(groups_router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}
