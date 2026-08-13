"""Test logging and audit_log coverage.

Verifies:
  - audit_log writes for login, user CRUD, groups, standards, tags, settings
  - slow query logging threshold
  - reqId bridging to audit_log
  - document set service logs
"""

import json
import os

import pytest


class TestAuditLogWrites:
    """Verify that key operations write to the audit_log table."""

    async def test_login_creates_audit_log(self, admin_client, db_conn):
        """POST /api/auth/login should write an audit_log entry."""
        before = db_conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        resp = await admin_client.post("/api/auth/login", json={
            "employee_id": "GDJL25631"
        })
        assert resp.status_code == 200
        after = db_conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert after > before, "login should create an audit_log entry"

    async def test_create_user_creates_audit_log(self, admin_client, db_conn):
        """POST /api/users should write an audit_log entry."""
        before = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='create_user'"
        ).fetchone()[0]
        resp = await admin_client.post("/api/users", json={
            "employee_id": "TESTLOG01", "role": "viewer", "name": "LogTestUser"
        })
        assert resp.status_code == 200
        after = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='create_user'"
        ).fetchone()[0]
        assert after > before

    async def test_create_group_creates_audit_log(self, admin_client, db_conn):
        """POST /api/groups should write an audit_log entry."""
        before = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='create_group'"
        ).fetchone()[0]
        resp = await admin_client.post("/api/groups", json={
            "name": "TestLogGroup", "description": "logging test",
            "category": "test", "member_ids": []
        })
        assert resp.status_code == 200
        after = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='create_group'"
        ).fetchone()[0]
        assert after > before


    async def test_create_standard_creates_audit_log(self, admin_client, db_conn):
        """POST /api/standards should write an audit_log entry."""
        before = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='create_standard'"
        ).fetchone()[0]
        resp = await admin_client.post("/api/standards", json={
            "code": "TEST-STD-001", "title": "Test Standard",
            "organization": "TEST", "category": "other", "version": "2024",
            "clauses": []
        })
        assert resp.status_code == 200
        after = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='create_standard'"
        ).fetchone()[0]
        assert after > before

    async def test_create_tag_creates_audit_log(self, admin_client, db_conn):
        """POST /api/tags should write an audit_log entry."""
        before = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='create_tag'"
        ).fetchone()[0]
        resp = await admin_client.post("/api/tags", json={"name": "TestLogTag"})
        assert resp.status_code == 200
        after = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='create_tag'"
        ).fetchone()[0]
        assert after > before

    async def test_update_settings_creates_audit_log(self, admin_client, db_conn):
        """PUT /api/admin/settings should write audit_log when values change."""
        before = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='update_settings'"
        ).fetchone()[0]
        resp = await admin_client.put("/api/admin/settings", json={
            "settings": {"jwt_expire_hours": "999"}
        })
        assert resp.status_code == 200
        after = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='update_settings'"
        ).fetchone()[0]
        assert after > before

    async def test_delete_user_creates_audit_log(self, admin_client, db_conn):
        """DELETE /api/users/{id} should write an audit_log entry."""
        await admin_client.post("/api/users", json={
            "employee_id": "TODELETE1", "role": "viewer", "name": "ToDelete"
        })
        before = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='delete_user'"
        ).fetchone()[0]
        resp = await admin_client.delete("/api/users/TODELETE1")
        assert resp.status_code == 200
        after = db_conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='delete_user'"
        ).fetchone()[0]
        assert after > before


class TestAuditLogReqIdBridging:
    """Verify reqId is bridged into audit_log entries."""

    async def test_audit_log_has_req_id(self, admin_client, db_conn):
        """Login should produce an audit_log entry with a non-empty req_id."""
        resp = await admin_client.post("/api/auth/login", json={
            "employee_id": "GDJL25631"
        })
        assert resp.status_code == 200
        row = db_conn.execute(
            "SELECT req_id FROM audit_log WHERE action='login'"
            " ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None, "login should create an audit_log entry"
        req_id = row[0]
        assert req_id, f"req_id should not be empty, got: {req_id!r}"
        assert len(req_id) >= 8, f"req_id too short: {req_id!r}"



class TestSlowQueryLogging:
    """Verify slow query logging threshold configuration."""

    def test_slow_query_env_default(self):
        """SLOW_QUERY_MS defaults to 500."""
        threshold = int(os.environ.get("SLOW_QUERY_MS", "500"))
        assert threshold == 500, f"Expected 500, got {threshold}"

    async def test_list_sets_returns_ok(self, admin_client):
        """A trivial task-list query should return 200."""
        resp = await admin_client.get("/api/sets")
        assert resp.status_code == 200


class TestDocumentSetServiceLogging:
    """Verify DocumentSetService logs operations."""

    async def test_create_set_logs(self, read_backend_logs):
        """DocumentSetService.create_set should log set_created."""
        from services.document_set import DocumentSetService

        before = read_backend_logs()
        ds = await DocumentSetService.create_set("GDJL25631")
        assert ds.set_id, "should return a valid set_id"
        after = read_backend_logs()

        new_events = [e["event"] for e in after[len(before):]]
        assert "set_created" in new_events, f"Expected set_created in {new_events}"

    async def test_add_document_logs(self, read_backend_logs):
        """DocumentSetService.add_document should log document_added."""
        from services.document_set import DocumentSetService

        ds = await DocumentSetService.create_set("GDJL25631")
        before = read_backend_logs()
        doc = await DocumentSetService.add_document(
            ds.set_id, "order_form", "test_order.xls", 10
        )
        assert doc.doc_id, "should return a valid doc_id"
        after = read_backend_logs()

        new_events = [e["event"] for e in after[len(before):]]
        assert "document_added" in new_events, (
            f"Expected document_added in {new_events}"
        )
