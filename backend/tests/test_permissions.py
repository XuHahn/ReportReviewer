"""Role-based permission boundary tests."""

import pytest
from tests.utils import make_rule_payload


class TestViewerRestrictions:

    async def test_viewer_cannot_upload(self, viewer_client):
        res = await viewer_client.post(
            "/api/reports/upload",
            files={"file": ("test.pdf", b"dummy", "application/pdf")},
            data={"tags": ""},
        )
        assert res.status_code == 403, f"Expected 403, got {res.status_code}"

    async def test_viewer_cannot_create_rule(self, viewer_client):
        res = await viewer_client.post("/api/rules", json=make_rule_payload())
        assert res.status_code == 403

    async def test_viewer_cannot_create_users(self, viewer_client):
        res = await viewer_client.post("/api/users", json={"employee_id": "VUSER01", "role": "viewer"})
        assert res.status_code == 403

    async def test_viewer_cannot_update_settings(self, viewer_client):
        res = await viewer_client.put("/api/admin/settings", json={"settings": {}})
        assert res.status_code in (403, 404)

    async def test_viewer_can_read_own_reports(self, viewer_client):
        res = await viewer_client.get("/api/reports/history?limit=10")
        assert res.status_code == 200


class TestReviewerRestrictions:

    async def test_reviewer_cannot_create_users(self, reviewer_client):
        res = await reviewer_client.post("/api/users", json={"employee_id": "RUSER01", "role": "viewer"})
        assert res.status_code == 403

    async def test_reviewer_cannot_update_settings(self, reviewer_client):
        res = await reviewer_client.put("/api/admin/settings", json={"settings": {}})
        assert res.status_code in (403, 404)

    async def test_reviewer_can_access_rules_endpoint(self, reviewer_client):
        """Reviewer can access rule creation (positive permission check)."""
        res = await reviewer_client.get("/api/rules")
        assert res.status_code == 200

    async def test_reviewer_can_create_rules(self, reviewer_client):
        res = await reviewer_client.post("/api/rules", json=make_rule_payload(name="ReviewerRule"))
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"


class TestAdminAccess:

    async def test_admin_can_manage_users(self, admin_client):
        res = await admin_client.get("/api/users")
        assert res.status_code == 200

    async def test_admin_can_update_settings(self, admin_client):
        res = await admin_client.put(
            "/api/admin/settings",
            json={"settings": {"jwt_expire_hours": "168"}},
        )
        assert res.status_code == 200

    async def test_admin_can_view_stats(self, admin_client):
        res = await admin_client.get("/api/admin/stats")
        assert res.status_code == 200
        assert "overview" in res.json()


class TestUnauthenticated:

    async def test_no_token_returns_401(self, client):
        res = await client.get("/api/reports/history")
        assert res.status_code == 401

    async def test_invalid_token_returns_401(self, client):
        client.headers["Authorization"] = "Bearer invalid.token.here"
        res = await client.get("/api/reports/history")
        assert res.status_code == 401
