"""Review Rules CRUD data-consistency tests."""

import pytest
from tests.utils import assert_api_db, assert_log_chain, extract_req_id, make_rule_payload


class TestRuleCreate:

    async def test_create_rule_api_matches_db(self, reviewer_client, db_conn, read_backend_logs):
        payload = make_rule_payload(name="Test Limit Rule")
        res = await reviewer_client.post("/api/rules", json=payload)
        assert res.status_code == 200, res.text
        data = res.json()
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT * FROM review_rules WHERE id = ?", (data["id"],)
        ).fetchone()
        assert db_row is not None
        assert_api_db(payload["name"], db_row["name"], "name", req_id)
        assert_api_db(payload["severity"], db_row["severity"], "severity", req_id)
        assert_api_db(payload["category"], db_row["category"], "category", req_id)
        assert db_row["enabled"] == 1

        assert_log_chain(req_id, read_backend_logs)


class TestRuleRead:

    async def test_list_rules_consistency(self, reviewer_client, db_conn):
        await reviewer_client.post("/api/rules", json=make_rule_payload(name="ListRule1"))
        await reviewer_client.post("/api/rules", json=make_rule_payload(name="ListRule2"))

        res = await reviewer_client.get("/api/rules")
        assert res.status_code == 200
        body = res.json()

        for rule in body:
            db_row = db_conn.execute(
                "SELECT * FROM review_rules WHERE id = ?", (rule["id"],)
            ).fetchone()
            assert db_row is not None
            assert_api_db(rule["name"], db_row["name"], "name")
            assert_api_db(rule["enabled"], bool(db_row["enabled"]), "enabled")

    async def test_get_rule_via_list_consistency(self, reviewer_client, db_conn):
        """No GET /api/rules/{id} endpoint exists — verify via list instead."""
        res = await reviewer_client.post("/api/rules", json=make_rule_payload(name="DetailRule"))
        rule_id = res.json()["id"]

        body = await reviewer_client.get("/api/rules")
        assert body.status_code == 200
        rules = body.json()
        rule = next((r for r in rules if r["id"] == rule_id), None)
        assert rule is not None, f"Rule {rule_id} not found in list"

        db_row = db_conn.execute(
            "SELECT * FROM review_rules WHERE id = ?", (rule_id,)
        ).fetchone()

        assert_api_db(rule["name"], db_row["name"], "name")
        assert_api_db(rule["description"], db_row["description"], "description")
        assert_api_db(rule["severity"], db_row["severity"], "severity")
        assert_api_db(rule["category"], db_row["category"], "category")


class TestRuleUpdate:

    async def test_update_rule_api_matches_db(self, reviewer_client, db_conn, read_backend_logs):
        res = await reviewer_client.post("/api/rules", json=make_rule_payload(name="UpdateMe"))
        rule_id = res.json()["id"]

        res2 = await reviewer_client.put(
            f"/api/rules/{rule_id}",
            json={"name": "Updated Rule", "severity": "warning", "enabled": False},
        )
        assert res2.status_code == 200, res2.text
        req_id = extract_req_id(res2)

        db_row = db_conn.execute(
            "SELECT * FROM review_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        assert db_row["name"] == "Updated Rule"
        assert db_row["severity"] == "warning"
        assert db_row["enabled"] == 0

        assert_log_chain(req_id, read_backend_logs)


class TestRuleDelete:

    async def test_delete_rule_removes_from_db(self, reviewer_client, db_conn, read_backend_logs):
        res = await reviewer_client.post("/api/rules", json=make_rule_payload(name="DeleteMe"))
        rule_id = res.json()["id"]

        res2 = await reviewer_client.delete(f"/api/rules/{rule_id}")
        assert res2.status_code == 200, res2.text
        req_id = extract_req_id(res2)

        db_row = db_conn.execute(
            "SELECT * FROM review_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        assert db_row is None

        assert_log_chain(req_id, read_backend_logs)
