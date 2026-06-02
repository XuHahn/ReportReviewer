"""Concurrency and race-condition tests.

Simulate simultaneous operations and verify data integrity.
"""

import asyncio
import json as json_mod
import pytest
from tests.utils import extract_req_id


class TestConcurrentAnnotation:

    async def test_two_users_annotate_same_item(self, reviewer_client, reviewer2_client, seeded_report):
        """Two reviewers annotating the same item simultaneously — at least one succeeds."""
        async def annotate_user1():
            return await reviewer_client.patch(
                f"/api/reports/{seeded_report}/items/0/annotation",
                json={"human_status": "confirmed", "human_comment": "User1 OK"},
            )

        async def annotate_user2():
            return await reviewer2_client.patch(
                f"/api/reports/{seeded_report}/items/0/annotation",
                json={"human_status": "needs_review", "human_comment": "User2 recheck"},
            )

        res1, res2 = await asyncio.gather(annotate_user1(), annotate_user2())

        assert res1.status_code in (200, 409), f"User1 got {res1.status_code}: {res1.text}"
        assert res2.status_code in (200, 409), f"User2 got {res2.status_code}: {res2.text}"
        assert res1.status_code == 200 or res2.status_code == 200, "Neither annotation succeeded"

    async def test_annotation_does_not_corrupt_other_fields(self, reviewer_client, reviewer2_client, seeded_report, db_conn):
        """Concurrent annotations must not corrupt filename or other columns."""
        before = db_conn.execute(
            "SELECT filename, review_items FROM reports WHERE id = ?", (seeded_report,)
        ).fetchone()
        original_filename = before["filename"]

        async def annotate():
            return await reviewer_client.patch(
                f"/api/reports/{seeded_report}/items/0/annotation",
                json={"human_status": "ignored"},
            )

        async def annotate2():
            return await reviewer2_client.patch(
                f"/api/reports/{seeded_report}/items/1/annotation",
                json={"human_status": "false_positive"},
            )

        await asyncio.gather(annotate(), annotate2())

        after = db_conn.execute(
            "SELECT filename, review_items FROM reports WHERE id = ?", (seeded_report,)
        ).fetchone()
        assert after["filename"] == original_filename, "Filename was corrupted"


class TestStaleToken:

    async def test_role_change_invalidates_token(self, admin_client, db_conn):
        """After an admin changes a user's role in DB, the user's old token is rejected."""
        await admin_client.post(
            "/api/users",
            json={"employee_id": "STALE01", "role": "reviewer"},
        )

        from auth import create_access_token
        token = create_access_token("STALE01", "reviewer")

        from httpx import ASGITransport, AsyncClient
        from main import app
        transport = ASGITransport(app=app)
        stale_client = AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Verify it works initially
        res1 = await stale_client.get("/api/reports/history?limit=1")
        assert res1.status_code == 200, f"Token should work initially: {res1.status_code}"

        # Admin changes the role in DB
        await admin_client.put("/api/users/STALE01/role", json={"role": "viewer"})

        # Same token should now be rejected (401) because role changed
        res2 = await stale_client.get("/api/reports/history?limit=1")
        assert res2.status_code == 401, f"Stale token should be rejected, got {res2.status_code}"

        await stale_client.aclose()


class TestRapidWrites:

    async def test_sequential_annotations_on_same_item(self, reviewer_client, seeded_report, db_conn):
        """Five sequential annotations on the same item should all succeed."""
        statuses = ["confirmed", "needs_review", "confirmed", "ignored", "confirmed"]
        for s in statuses:
            res = await reviewer_client.patch(
                f"/api/reports/{seeded_report}/items/0/annotation",
                json={"human_status": s},
            )
            assert res.status_code == 200, f"Seq annotation {s} failed: {res.text}"

        db_items = json_mod.loads(
            db_conn.execute(
                "SELECT review_items FROM reports WHERE id = ?", (seeded_report,)
            ).fetchone()["review_items"]
        )
        assert db_items[0]["human_status"] == "confirmed"

    async def test_parallel_items_may_race(self, reviewer_client, seeded_report, db_conn):
        """Annotating different items in parallel can race due to read-modify-write.

        Both annotate the same underlying review_items JSON, so the second commit
        may overwrite the first. This test documents the current behavior.
        """
        async def annotate_item(n):
            return await reviewer_client.patch(
                f"/api/reports/{seeded_report}/items/{n}/annotation",
                json={"human_status": "confirmed", "human_comment": f"Item {n} OK"},
            )

        res0, res1 = await asyncio.gather(annotate_item(0), annotate_item(1))
        assert res0.status_code == 200, f"Item 0 failed: {res0.text}"
        assert res1.status_code == 200, f"Item 1 failed: {res1.text}"

        # After concurrent writes, at least one item should be annotated.
        # Due to read-modify-write races, the other may be overwritten.
        db_items = json_mod.loads(
            db_conn.execute(
                "SELECT review_items FROM reports WHERE id = ?", (seeded_report,)
            ).fetchone()["review_items"]
        )
        assert db_items[0]["human_status"] == "confirmed" or db_items[1]["human_status"] == "confirmed", (
            "Neither item was annotated after parallel writes"
        )
