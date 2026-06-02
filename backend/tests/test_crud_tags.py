"""Tag CRUD data-consistency tests."""

import pytest
from tests.utils import assert_api_db, assert_log_chain, extract_req_id


class TestTagCreate:

    async def test_create_tag_api_matches_db(self, admin_client, db_conn, read_backend_logs):
        res = await admin_client.post("/api/tags", json={"name": "TestTag"})
        assert res.status_code == 200, res.text
        data = res.json()
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT * FROM tags WHERE id = ?", (data["id"],)
        ).fetchone()
        assert db_row is not None
        assert_api_db("TestTag", db_row["name"], "name", req_id)

        assert_log_chain(req_id, read_backend_logs)

    async def test_create_tag_trims_whitespace(self, admin_client, db_conn):
        res = await admin_client.post("/api/tags", json={"name": "  Trimmed  "})
        assert res.status_code == 200
        tag_id = res.json()["id"]

        db_row = db_conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()
        assert db_row["name"] == "Trimmed"


class TestTagRead:

    async def test_list_tags_consistency(self, reviewer_client, db_conn):
        res = await reviewer_client.get("/api/tags")
        assert res.status_code == 200
        body = res.json()

        db_tags = db_conn.execute("SELECT id, name FROM tags ORDER BY name").fetchall()
        assert len(body) == len(db_tags)

        for tag in body:
            db_row = db_conn.execute(
                "SELECT * FROM tags WHERE id = ?", (tag["id"],)
            ).fetchone()
            assert db_row is not None
            assert_api_db(tag["name"], db_row["name"], "name")


class TestTagUpdate:

    async def test_update_tag_api_matches_db(self, admin_client, db_conn, read_backend_logs):
        res = await admin_client.post("/api/tags", json={"name": "OldName"})
        tag_id = res.json()["id"]

        res2 = await admin_client.put(f"/api/tags/{tag_id}", json={"name": "NewName"})
        assert res2.status_code == 200, res2.text
        req_id = extract_req_id(res2)

        db_row = db_conn.execute(
            "SELECT name FROM tags WHERE id = ?", (tag_id,)
        ).fetchone()
        assert db_row["name"] == "NewName"

        assert_log_chain(req_id, read_backend_logs)


class TestTagDelete:

    async def test_delete_tag_removes_from_db(self, admin_client, db_conn, read_backend_logs):
        res = await admin_client.post("/api/tags", json={"name": "DeleteTag"})
        tag_id = res.json()["id"]

        res2 = await admin_client.delete(f"/api/tags/{tag_id}")
        assert res2.status_code == 200, res2.text
        req_id = extract_req_id(res2)

        db_row = db_conn.execute(
            "SELECT * FROM tags WHERE id = ?", (tag_id,)
        ).fetchone()
        assert db_row is None

        assert_log_chain(req_id, read_backend_logs)
