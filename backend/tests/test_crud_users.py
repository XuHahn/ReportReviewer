"""User CRUD data-consistency tests.

Verify: API response == database truth for user management operations.
"""

import pytest
from tests.utils import assert_api_db, assert_log_chain, extract_req_id


class TestUserRead:

    async def test_list_users_consistency(self, admin_client, db_conn):
        res = await admin_client.get("/api/users")
        assert res.status_code == 200
        body = res.json()

        for user in body["users"]:
            db_row = db_conn.execute(
                "SELECT * FROM users WHERE employee_id = ?", (user["employee_id"],)
            ).fetchone()
            assert db_row is not None, f"User {user['employee_id']} not in DB"
            assert_api_db(user["role"], db_row["role"], "role")
            assert_api_db(user.get("name", ""), db_row["name"] if db_row["name"] else "", "name")

    async def test_me_consistency(self, admin_client, db_conn):
        res = await admin_client.get("/api/auth/me")
        assert res.status_code == 200
        body = res.json()

        db_row = db_conn.execute(
            "SELECT * FROM users WHERE employee_id = ?", ("GDJL25631",)
        ).fetchone()
        assert_api_db(body["role"], db_row["role"], "role")
        assert body["employee_id"] == "GDJL25631"


class TestUserCreate:

    async def test_create_user_api_matches_db(self, admin_client, db_conn, read_backend_logs):
        res = await admin_client.post(
            "/api/users",
            json={"employee_id": "TEST001", "role": "reviewer", "name": "测试员"},
        )
        assert res.status_code == 200, res.text
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT * FROM users WHERE employee_id = ?", ("TEST001",)
        ).fetchone()
        assert db_row is not None
        assert_api_db("reviewer", db_row["role"], "role", req_id)
        assert_api_db("测试员", db_row["name"], "name", req_id)

        assert_log_chain(req_id, read_backend_logs)

    async def test_create_duplicate_rejected(self, admin_client):
        await admin_client.post("/api/users", json={"employee_id": "DUP001", "role": "viewer"})
        res = await admin_client.post("/api/users", json={"employee_id": "DUP001", "role": "viewer"})
        assert res.status_code == 409


class TestUserUpdate:

    async def test_update_role_api_matches_db(self, admin_client, db_conn, read_backend_logs):
        await admin_client.post("/api/users", json={"employee_id": "ROLE001", "role": "viewer"})

        res = await admin_client.put(
            "/api/users/ROLE001/role",
            json={"role": "admin"},
        )
        assert res.status_code == 200, res.text
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT role FROM users WHERE employee_id = ?", ("ROLE001",)
        ).fetchone()
        assert db_row["role"] == "admin"

        assert_log_chain(req_id, read_backend_logs)

    async def test_update_name_api_matches_db(self, admin_client, db_conn, read_backend_logs):
        await admin_client.post("/api/users", json={"employee_id": "NAME001", "role": "viewer", "name": "OldName"})

        res = await admin_client.put(
            "/api/users/NAME001/name",
            json={"name": "NewName"},
        )
        assert res.status_code == 200, res.text
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT name FROM users WHERE employee_id = ?", ("NAME001",)
        ).fetchone()
        assert db_row["name"] == "NewName"

        assert_log_chain(req_id, read_backend_logs)

    async def test_update_nonexistent_user(self, admin_client):
        res = await admin_client.put(
            "/api/users/NOBODY99/role",
            json={"role": "admin"},
        )
        assert res.status_code == 404


class TestUserDelete:

    async def test_delete_user_removes_from_db(self, admin_client, db_conn, read_backend_logs):
        await admin_client.post("/api/users", json={"employee_id": "DEL001", "role": "viewer"})

        res = await admin_client.delete("/api/users/DEL001")
        assert res.status_code == 200, res.text
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT * FROM users WHERE employee_id = ?", ("DEL001",)
        ).fetchone()
        assert db_row is None

        assert_log_chain(req_id, read_backend_logs)
