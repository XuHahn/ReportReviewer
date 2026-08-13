import os
import json
import sqlite3
import tempfile
import atexit
import shutil
from pathlib import Path

import pytest
import httpx

# ── Set env vars BEFORE importing the app ─────────────────────────────
# This ensures init_logging() picks up the right LOG_DIR and writes JSONL.

_TMP_ROOT = tempfile.mkdtemp(prefix="emc_test_")
atexit.register(shutil.rmtree, _TMP_ROOT, ignore_errors=True)

_TMP_DB = os.path.join(_TMP_ROOT, "test_review.db")
_TMP_LOGS = os.path.join(_TMP_ROOT, "logs")
os.makedirs(_TMP_LOGS, exist_ok=True)

os.environ["DB_PATH"] = _TMP_DB
os.environ["LOG_ENV"] = "prod"          # writes JSONL to files
os.environ["LOG_DIR"] = _TMP_LOGS
os.environ["DEEPSEEK_API_KEY"] = "sk-test-placeholder"
os.environ["JWT_SECRET"] = "test-only-jwt-secret-not-used-in-production"
os.environ["STANDARD_EMBEDDING_BACKEND"] = "hash-test"

# Now safe to import
from main import app as _app
from auth import create_access_token
from database import (
    init_db, seed_admin, seed_builtin_standards, create_user, get_user,
)
from models import (
    TestItemExtraction,
    TestPlanBasicInfo,
    TestPlanData,
    TestPlanDetail,
    TestPlanItem,
    TestResultsSection,
)
from services.raw_records_aggregator import TestConclusionSummary
from services.raw_records_table_extractor import TestDataRow

# These are domain models imported by test modules, not pytest test classes.
# Marking them explicitly prevents collection warnings without renaming the
# production types or hiding unrelated PytestCollectionWarning messages.
for _domain_model in (
    TestItemExtraction,
    TestPlanBasicInfo,
    TestPlanData,
    TestPlanDetail,
    TestPlanItem,
    TestResultsSection,
    TestConclusionSummary,
    TestDataRow,
):
    _domain_model.__test__ = False


# ── Session-scoped setup ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def temp_db_dir():
    return _TMP_ROOT


@pytest.fixture(scope="session")
def test_db_path():
    return _TMP_DB


@pytest.fixture(scope="session")
def test_log_dir():
    return _TMP_LOGS


@pytest.fixture(scope="session")
def app():
    return _app


# ── Seed the test DB once per session ─────────────────────────────────

_SEEDED = False


def _seed():
    global _SEEDED
    if _SEEDED:
        return
    _SEEDED = True
    init_db()
    seed_builtin_standards()
    seed_admin()       # creates GDJL25631 / admin
    # Additional test users for concurrency + permission tests
    create_user("reviewer1", "reviewer", "审核员1")
    create_user("reviewer2", "reviewer", "审核员2")
    create_user("standard1", "standard_reviewer", "标准审核员1")
    create_user("viewer1", "viewer", "观察者1")
    create_user("viewer2", "viewer", "观察者2")


@pytest.fixture(scope="session")
def _seeded_db():
    _seed()


# ── Clients ───────────────────────────────────────────────────────────

def _make_client(token: str | None = None) -> httpx.AsyncClient:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    transport = httpx.ASGITransport(app=_app)
    return httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers)


@pytest.fixture
async def client():
    async with _make_client() as c:
        yield c


@pytest.fixture
async def admin_client(_seeded_db):
    token = create_access_token("GDJL25631", "admin")
    async with _make_client(token) as c:
        yield c


@pytest.fixture
async def reviewer_client(_seeded_db):
    token = create_access_token("reviewer1", "reviewer")
    async with _make_client(token) as c:
        yield c


@pytest.fixture
async def reviewer2_client(_seeded_db):
    token = create_access_token("reviewer2", "reviewer")
    async with _make_client(token) as c:
        yield c


@pytest.fixture
async def standard_reviewer_client(_seeded_db):
    token = create_access_token("standard1", "standard_reviewer")
    async with _make_client(token) as c:
        yield c


@pytest.fixture
async def viewer_client(_seeded_db):
    token = create_access_token("viewer1", "viewer")
    async with _make_client(token) as c:
        yield c


@pytest.fixture
async def viewer2_client(_seeded_db):
    token = create_access_token("viewer2", "viewer")
    async with _make_client(token) as c:
        yield c


# ── Direct DB connection ──────────────────────────────────────────────

@pytest.fixture
def db_conn():
    """Direct sqlite3 connection for reading the real DB state."""
    conn = sqlite3.connect(_TMP_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    yield conn
    conn.close()


# ── Log readers ───────────────────────────────────────────────────────

def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    entries = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


@pytest.fixture
def read_backend_logs():
    def _read():
        return _read_jsonl(os.path.join(_TMP_LOGS, "app.jsonl"))
    return _read


@pytest.fixture
def read_frontend_logs():
    def _read():
        return _read_jsonl(os.path.join(_TMP_LOGS, "frontend.jsonl"))
    return _read
