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

# Now safe to import
from main import app as _app
from auth import create_access_token
from database import (
    init_db, seed_admin, create_user, get_user,
    save_report, update_review_item_annotation,
    save_rule, save_standard, create_project_group,
    create_tag,
)
from models import ReviewItem


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
    init_db()          # creates tables + runs migrations + seeds builtin standards
    seed_admin()       # creates GDJL25631 / admin
    # Additional test users for concurrency + permission tests
    create_user("reviewer1", "reviewer", "审核员1")
    create_user("reviewer2", "reviewer", "审核员2")
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


# ── Convenience: seed a report for annotation / delete tests ──────────

@pytest.fixture
def seeded_report():
    """Create a report with 2 review items, return its id."""
    items = [
        ReviewItem(
            severity="error", location="Section 1",
            original_text="Test emission value exceeds limit",
            error_description="Exceeds CISPR 25 Class 3 limit",
            standard_reference="CISPR 25 §6.3",
            suggestion="Reduce gain",
        ),
        ReviewItem(
            severity="warning", location="Section 2",
            original_text="Missing calibration date",
            error_description="Calibration date not documented",
            standard_reference="ISO 17025 §5.6",
            suggestion="Add calibration date",
        ),
    ]
    rid = save_report("test_report.pdf", "fail", items, "<html></html>", employee_id="reviewer1")
    return rid
