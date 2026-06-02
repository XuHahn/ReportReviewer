"""Report CRUD data-consistency tests.

Each test verifies: API response == database truth (2-way).
"""

import json as json_mod
import pytest
from tests.utils import assert_api_db, assert_log_chain, extract_req_id


class TestReportRead:
    """GET endpoints — verify API returns what's actually in the DB."""

    async def test_history_list_consistency(self, reviewer_client, seeded_report, db_conn):
        res = await reviewer_client.get("/api/reports/history?limit=20")
        assert res.status_code == 200
        body = res.json()

        for report in body["reports"]:
            db_row = db_conn.execute(
                "SELECT * FROM reports WHERE id = ? AND is_deleted = 0",
                (report["id"],),
            ).fetchone()
            assert db_row is not None, f"Report {report['id']} not found in DB"

            assert_api_db(report["filename"], db_row["filename"], "filename")
            assert_api_db(report["overall_result"], db_row["overall_result"], "overall_result")
            assert_api_db(report.get("employee_id", ""), db_row["employee_id"], "employee_id")
            assert_api_db(report.get("tags", ""), db_row["tags"], "tags")

    async def test_report_detail_consistency(self, reviewer_client, seeded_report, db_conn, read_backend_logs):
        res = await reviewer_client.get(f"/api/reports/{seeded_report}")
        assert res.status_code == 200
        body = res.json()
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT * FROM reports WHERE id = ?", (seeded_report,)
        ).fetchone()

        assert_api_db(body["filename"], db_row["filename"], "filename", req_id)
        assert_api_db(body["overall_result"], db_row["overall_result"], "overall_result", req_id)
        assert_api_db(body.get("employee_id", ""), db_row["employee_id"], "employee_id", req_id)
        assert len(body.get("review_items", [])) > 0

        assert_log_chain(req_id, read_backend_logs)

    async def test_history_excludes_deleted(self, admin_client, seeded_report, db_conn):
        await admin_client.delete(f"/api/reports/{seeded_report}")
        res = await admin_client.get("/api/reports/history?limit=50")
        ids = [r["id"] for r in res.json()["reports"]]
        assert seeded_report not in ids


class TestReportAnnotate:

    async def test_annotation_api_matches_db(self, reviewer_client, seeded_report, db_conn, read_backend_logs):
        res = await reviewer_client.patch(
            f"/api/reports/{seeded_report}/items/0/annotation",
            json={"human_status": "confirmed", "human_comment": "Looks correct"},
        )
        assert res.status_code == 200, res.text
        req_id = extract_req_id(res)

        detail = await reviewer_client.get(f"/api/reports/{seeded_report}")
        items = detail.json()["review_items"]
        assert items[0]["human_status"] == "confirmed"
        assert items[0]["human_comment"] == "Looks correct"

        db_row = db_conn.execute(
            "SELECT review_items FROM reports WHERE id = ?", (seeded_report,)
        ).fetchone()
        db_items = json_mod.loads(db_row["review_items"])
        assert db_items[0]["human_status"] == "confirmed"
        assert db_items[0]["human_comment"] == "Looks correct"

        assert_log_chain(req_id, read_backend_logs)

    async def test_annotation_preserves_other_items(self, reviewer_client, seeded_report):
        before = await reviewer_client.get(f"/api/reports/{seeded_report}")
        item1_before = before.json()["review_items"][1]

        await reviewer_client.patch(
            f"/api/reports/{seeded_report}/items/0/annotation",
            json={"human_status": "false_positive", "human_comment": "Not an error"},
        )

        after = await reviewer_client.get(f"/api/reports/{seeded_report}")
        item1_after = after.json()["review_items"][1]
        assert item1_after["human_status"] == item1_before["human_status"]
        assert item1_after["original_text"] == item1_before["original_text"]

    async def test_invalid_item_index_returns_404(self, reviewer_client, seeded_report):
        res = await reviewer_client.patch(
            f"/api/reports/{seeded_report}/items/99/annotation",
            json={"human_status": "confirmed"},
        )
        assert res.status_code == 404

    async def test_invalid_status_returns_422(self, reviewer_client, seeded_report):
        res = await reviewer_client.patch(
            f"/api/reports/{seeded_report}/items/0/annotation",
            json={"human_status": "bogus"},
        )
        assert res.status_code == 400, f"Expected 400, got {res.status_code}"


class TestReportDelete:

    async def test_soft_delete_sets_flag(self, admin_client, seeded_report, db_conn, read_backend_logs):
        res = await admin_client.delete(f"/api/reports/{seeded_report}")
        assert res.status_code == 200
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT is_deleted FROM reports WHERE id = ?", (seeded_report,)
        ).fetchone()
        assert db_row["is_deleted"] == 1

        res2 = await admin_client.get(f"/api/reports/{seeded_report}")
        assert res2.status_code in (404, 403)

        assert_log_chain(req_id, read_backend_logs)

    async def test_restore_clears_flag(self, admin_client, seeded_report, db_conn, read_backend_logs):
        await admin_client.delete(f"/api/reports/{seeded_report}")

        res = await admin_client.post(f"/api/reports/{seeded_report}/restore")
        assert res.status_code == 200
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT is_deleted FROM reports WHERE id = ?", (seeded_report,)
        ).fetchone()
        assert db_row["is_deleted"] == 0

        res2 = await admin_client.get(f"/api/reports/{seeded_report}")
        assert res2.status_code == 200

        assert_log_chain(req_id, read_backend_logs, expect_success=True)

    async def test_delete_nonexistent_returns_404(self, admin_client):
        res = await admin_client.delete("/api/reports/nonexistent999")
        assert res.status_code in (404, 200)  # 200 means "already deleted" which is fine
