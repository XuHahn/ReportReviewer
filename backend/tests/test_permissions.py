"""Role-based permission boundary tests."""

from auth import create_access_token

class TestViewerRestrictions:

    async def test_viewer_cannot_create_tag(self, viewer_client):
        res = await viewer_client.post("/api/tags", json={"name": "viewer-tag"})
        assert res.status_code == 403

    async def test_viewer_cannot_create_users(self, viewer_client):
        res = await viewer_client.post("/api/users", json={"employee_id": "VUSER01", "role": "viewer"})
        assert res.status_code == 403

    async def test_viewer_cannot_update_settings(self, viewer_client):
        res = await viewer_client.put("/api/admin/settings", json={"settings": {}})
        assert res.status_code in (403, 404)

    async def test_viewer_can_read_own_tasks(self, viewer_client):
        res = await viewer_client.get("/api/sets")
        assert res.status_code == 200

    async def test_viewer_cannot_list_all_users_or_audit_logs(self, viewer_client):
        assert (await viewer_client.get("/api/users")).status_code == 403
        assert (await viewer_client.get("/api/logs")).status_code == 403
class TestReviewerRestrictions:

    async def test_reviewer_cannot_create_users(self, reviewer_client):
        res = await reviewer_client.post("/api/users", json={"employee_id": "RUSER01", "role": "viewer"})
        assert res.status_code == 403

    async def test_reviewer_cannot_update_settings(self, reviewer_client):
        res = await reviewer_client.put("/api/admin/settings", json={"settings": {}})
        assert res.status_code in (403, 404)

    async def test_reviewer_can_access_tags_endpoint(self, reviewer_client):
        res = await reviewer_client.get("/api/tags")
        assert res.status_code == 200

    async def test_reviewer_can_create_tags(self, reviewer_client):
        res = await reviewer_client.post("/api/tags", json={"name": "reviewer-tag"})
        assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"

    async def test_reviewer_cannot_read_audit_logs(self, reviewer_client):
        assert (await reviewer_client.get("/api/logs")).status_code == 403
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
        assert "total_sets" in res.json()

    async def test_admin_can_read_audit_logs(self, admin_client):
        assert (await admin_client.get("/api/logs")).status_code == 200

    async def test_admin_cannot_create_unknown_role(self, admin_client):
        response = await admin_client.post(
            "/api/users", json={"employee_id": "BADROLE", "role": "superuser"},
        )
        assert response.status_code == 422
class TestUnauthenticated:

    async def test_no_token_returns_401(self, client):
        res = await client.get("/api/sets")
        assert res.status_code == 401

    async def test_invalid_token_returns_401(self, client):
        client.headers["Authorization"] = "Bearer invalid.token.here"
        res = await client.get("/api/sets")
        assert res.status_code == 401

    async def test_access_token_in_query_string_is_rejected(self, client, _seeded_db):
        token = create_access_token("reviewer1", "reviewer")
        res = await client.get("/api/sets", params={"token": token})
        assert res.status_code == 401
