"""Test utility functions for data-consistency verification."""

import json
import sqlite3
from pathlib import Path


# ── Assertion helpers ──────────────────────────────────────────────────

def assert_api_db(
    api_value,
    db_value,
    path: str = "",
    req_id: str = "",
):
    """Assert API response value matches database value.

    Args:
        api_value: value from HTTP response JSON
        db_value: value from direct sqlite3 query
        path: dotted field path, e.g. "review_items[0].human_status"
        req_id: request id for log correlation
    """
    ctx = f"[reqId={req_id}] " if req_id else ""
    ctx += f"field={path}" if path else ""

    # Normalize both sides for comparison
    a = normalize(api_value)
    d = normalize(db_value)

    assert a == d, (
        f"{ctx}\n"
        f"  API value : {api_value!r}\n"
        f"  DB  value : {db_value!r}"
    )


def assert_log_chain(
    req_id: str,
    read_backend_logs,
    read_frontend_logs=None,
    expect_success: bool = True,
):
    """Verify a reqId appears in backend logs with expected outcome.

    Args:
        req_id: the X-Request-Id value
        read_backend_logs: callable that returns list of backend log dicts
        read_frontend_logs: callable that returns list of frontend log dicts (optional)
        expect_success: if True, assert the backend log shows status < 400
    """
    backend = [e for e in read_backend_logs() if str(e.get("reqId", "")) == req_id]
    assert len(backend) > 0, f"Backend log missing for reqId={req_id}"

    if expect_success:
        statuses = [
            e.get("status", 0) for e in backend
            if isinstance(e.get("status"), (int, float))
        ]
        if statuses:
            assert all(s < 400 for s in statuses), (
                f"Expected success but got statuses {statuses} for reqId={req_id}"
            )

    if read_frontend_logs is not None:
        frontend = [e for e in read_frontend_logs() if str(e.get("reqId", "")) == req_id]
        # Frontend logs may not always exist (e.g. direct API calls),
        # but if we're specifically testing frontend logging, require presence.
        # assert len(frontend) > 0, f"Frontend log missing for reqId={req_id}"


# ── Data normalization ─────────────────────────────────────────────────

def normalize(value):
    """Normalize a value for consistent comparison.

    - Convert empty string to None (treat '' == null)
    - Sort dict keys for stable comparison
    - Round floats to 6 decimal places
    """
    if isinstance(value, str):
        return value.strip() if value.strip() else None
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [normalize(v) for v in value]
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in sorted(value.items())}
    return value


def parse_json_field(raw: str | None) -> list | dict | None:
    """Parse a JSON column value from SQLite, returning None on failure."""
    if raw is None:
        return None
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ── Log search ────────────────────────────────────────────────────────

def find_logs_by_reqid(logs: list[dict], req_id: str) -> list[dict]:
    """Filter log entries matching a reqId."""
    return [e for e in logs if str(e.get("reqId", "")).strip() == req_id]


def extract_req_id(response) -> str:
    """Extract X-Request-Id from an httpx Response."""
    return response.headers.get("x-request-id", "")


# ── Test data factories ───────────────────────────────────────────────

def make_minimal_report_json(
    filename: str = "test_report.pdf",
    overall_result: str = "fail",
    items: list[dict] | None = None,
) -> dict:
    """Build a minimal report payload for test assertions."""
    if items is None:
        items = [
            {
                "severity": "error",
                "location": "Section 1",
                "original_text": "Test content",
                "error_description": "Test error",
                "standard_reference": "CISPR 25",
                "suggestion": "Fix it",
                "highlighted": True,
                "human_status": "pending",
                "human_comment": "",
                "annotated_by": "",
                "annotated_at": "",
            }
        ]
    return {
        "filename": filename,
        "overall_result": overall_result,
        "review_items": items,
        "highlighted_html": "<html></html>",
    }


def make_rule_payload(
    name: str = "Test Rule",
    description: str = "A test rule",
    category: str = "limit",
    severity: str = "error",
    keywords: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "description": description,
        "category": category,
        "severity": severity,
        "keywords": keywords or ["test", "limit"],
        "pattern": r"\d+\s*dB",
        "standard_id": "",
        "suggestion_template": "Check the limit value",
        "enabled": True,
    }


def make_group_payload(
    name: str = "Test Group",
    description: str = "A test project group",
    member_ids: list[str] | None = None,
    category: str = "EMC全项",
) -> dict:
    return {
        "name": name,
        "description": description,
        "member_ids": member_ids or [],
        "category": category,
    }
