"""Project Group CRUD data-consistency tests."""

import pytest
from tests.utils import assert_api_db, assert_log_chain, extract_req_id, make_group_payload


class TestGroupCreate:

    async def test_create_group_api_matches_db(self, reviewer_client, db_conn, read_backend_logs):
        res = await reviewer_client.post("/api/groups", json=make_group_payload(
            name="TestGroup", description="Desc", member_ids=["reviewer2"],
        ))
        assert res.status_code == 200, res.text
        gid = res.json()["id"]
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT * FROM project_groups WHERE id = ?", (gid,)
        ).fetchone()
        assert db_row is not None
        assert_api_db("TestGroup", db_row["name"], "name", req_id)
        assert_api_db("Desc", db_row["description"], "description", req_id)

        members = db_conn.execute(
            "SELECT employee_id FROM project_group_members WHERE group_id = ?", (gid,)
        ).fetchall()
        member_ids = [m["employee_id"] for m in members]
        assert "reviewer1" in member_ids  # creator auto-added
        assert "reviewer2" in member_ids

        assert_log_chain(req_id, read_backend_logs)

    async def test_create_empty_name_rejected(self, reviewer_client):
        res = await reviewer_client.post("/api/groups", json=make_group_payload(name=""))
        assert res.status_code == 400


class TestGroupRead:

    async def test_list_groups_consistency(self, reviewer_client, db_conn):
        await reviewer_client.post("/api/groups", json=make_group_payload(name="G1"))
        await reviewer_client.post("/api/groups", json=make_group_payload(name="G2"))

        res = await reviewer_client.get("/api/groups")
        assert res.status_code == 200
        body = res.json()

        db_count = db_conn.execute(
            """SELECT COUNT(*) FROM project_groups pg
               JOIN project_group_members pgm ON pgm.group_id = pg.id
               WHERE pgm.employee_id = 'reviewer1'"""
        ).fetchone()[0]
        assert len(body) == db_count

        for g in body:
            db_row = db_conn.execute(
                "SELECT * FROM project_groups WHERE id = ?", (g["id"],)
            ).fetchone()
            assert db_row is not None
            assert_api_db(g["name"], db_row["name"], "name")

    async def test_get_group_detail_consistency(self, reviewer_client, db_conn):
        res = await reviewer_client.post("/api/groups", json=make_group_payload(
            name="DetailGroup", member_ids=["viewer1"],
        ))
        gid = res.json()["id"]

        detail = await reviewer_client.get(f"/api/groups/{gid}")
        assert detail.status_code == 200
        body = detail.json()

        db_row = db_conn.execute(
            "SELECT * FROM project_groups WHERE id = ?", (gid,)
        ).fetchone()
        assert_api_db(body["name"], db_row["name"], "name")
        assert_api_db(body["description"], db_row["description"], "description")

        db_members = [m["employee_id"] for m in db_conn.execute(
            "SELECT employee_id FROM project_group_members WHERE group_id = ?", (gid,)
        ).fetchall()]
        assert set(body.get("members", [])) == set(db_members)


class TestGroupUpdate:

    async def test_update_group_api_matches_db(self, reviewer_client, db_conn, read_backend_logs):
        res = await reviewer_client.post("/api/groups", json=make_group_payload(name="UpdateMe"))
        gid = res.json()["id"]

        res2 = await reviewer_client.put(f"/api/groups/{gid}", json={
            "name": "Renamed", "description": "New desc",
        })
        assert res2.status_code == 200, res2.text
        req_id = extract_req_id(res2)

        db_row = db_conn.execute(
            "SELECT * FROM project_groups WHERE id = ?", (gid,)
        ).fetchone()
        assert db_row["name"] == "Renamed"
        assert db_row["description"] == "New desc"

        assert_log_chain(req_id, read_backend_logs)

    async def test_update_nonexistent_returns_404(self, reviewer_client):
        res = await reviewer_client.put("/api/groups/nonexistent99", json={"name": "X"})
        assert res.status_code == 404


class TestGroupDelete:

    async def test_delete_group_removes_from_db(self, reviewer_client, db_conn, read_backend_logs):
        res = await reviewer_client.post("/api/groups", json=make_group_payload(name="DeleteMe"))
        gid = res.json()["id"]

        res2 = await reviewer_client.delete(f"/api/groups/{gid}")
        assert res2.status_code == 200, res2.text
        req_id = extract_req_id(res2)

        db_row = db_conn.execute(
            "SELECT * FROM project_groups WHERE id = ?", (gid,)
        ).fetchone()
        assert db_row is None

        member_count = db_conn.execute(
            "SELECT COUNT(*) FROM project_group_members WHERE group_id = ?", (gid,)
        ).fetchone()[0]
        assert member_count == 0

        assert_log_chain(req_id, read_backend_logs)
