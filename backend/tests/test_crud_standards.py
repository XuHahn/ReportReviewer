"""EMC Standards CRUD data-consistency tests."""

import json as json_mod
import pytest
import database
from tests.utils import assert_api_db, assert_log_chain, extract_req_id


_STANDARD_PAYLOAD = {
    "code": "TEST-STD-001",
    "title": "Test EMC Standard",
    "organization": "TEST",
    "category": "radiated",
    "version": "2025",
    "clauses": [
        {
            "clause": "1.1",
            "title": "Test Clause",
            "description": "A test clause for consistency checks",
            "limit_table": [{"frequency": "1-10 MHz", "limit": "50 dBμV"}],
        }
    ],
}


class TestStandardCreate:

    async def test_create_standard_api_matches_db(self, standard_reviewer_client, db_conn, read_backend_logs):
        res = await standard_reviewer_client.post("/api/standards", json=_STANDARD_PAYLOAD)
        assert res.status_code == 200, res.text
        data = res.json()
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT * FROM emc_standards WHERE id = ?", (data["id"],)
        ).fetchone()
        assert db_row is not None
        assert_api_db(_STANDARD_PAYLOAD["code"], db_row["code"], "code", req_id)
        assert_api_db(_STANDARD_PAYLOAD["title"], db_row["title"], "title", req_id)
        assert db_row["is_builtin"] == 0

        db_clauses = json_mod.loads(db_row["clauses"])
        assert len(db_clauses) == 1
        assert db_clauses[0]["clause"] == "1.1"

        assert_log_chain(req_id, read_backend_logs)


class TestStandardRead:

    async def test_list_standards_consistency(self, reviewer_client, db_conn):
        res = await reviewer_client.get("/api/standards")
        assert res.status_code == 200
        standards = res.json()  # returns list directly

        assert len(standards) > 0  # seeded builtins

        for std in standards:
            db_row = db_conn.execute(
                "SELECT * FROM emc_standards WHERE id = ?", (std["id"],)
            ).fetchone()
            assert db_row is not None
            assert_api_db(std["code"], db_row["code"], "code")

    async def test_get_standard_detail_consistency(self, reviewer_client, db_conn):
        res = await reviewer_client.get("/api/standards")
        standards = res.json()
        if not standards:
            pytest.skip("No standards available")
        std_id = standards[0]["id"]

        detail = await reviewer_client.get(f"/api/standards/{std_id}")
        assert detail.status_code == 200
        body = detail.json()

        db_row = db_conn.execute(
            "SELECT * FROM emc_standards WHERE id = ?", (std_id,)
        ).fetchone()
        assert_api_db(body["code"], db_row["code"], "code")
        assert_api_db(body["title"], db_row["title"], "title")


class TestStandardFileResponses:

    async def test_pdf_preview_is_inline_while_original_file_is_attachment(self, reviewer_client):
        pdf_bytes = b"%PDF-1.4\n% preview response test\n"
        standard_id, _ = database.create_standard_asset(
            pdf_bytes, "ISO \u6d4b\u8bd5\u6807\u51c6.pdf", code="ISO PREVIEW TEST", version="2026",
        )

        preview = await reviewer_client.get(f"/api/standards/{standard_id}/preview")
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "application/pdf"
        assert preview.headers["content-disposition"].startswith("inline;")
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert preview.content == pdf_bytes

        download = await reviewer_client.get(f"/api/standards/{standard_id}/file")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.content == pdf_bytes

    async def test_non_pdf_standard_cannot_use_pdf_preview(self, reviewer_client):
        standard_id, _ = database.create_standard_asset(
            b"plain text standard", "plain-standard.txt",
            code="TEXT PREVIEW TEST", version="2026",
        )

        response = await reviewer_client.get(f"/api/standards/{standard_id}/preview")

        assert response.status_code == 415
        assert response.json()["detail"] == "当前标准不是可预览的 PDF 文件"


class TestStandardDelete:

    async def test_delete_custom_standard_removes_from_db(self, standard_reviewer_client, db_conn, read_backend_logs):
        res = await standard_reviewer_client.post("/api/standards", json=_STANDARD_PAYLOAD)
        std_id = res.json()["id"]

        res2 = await standard_reviewer_client.delete(f"/api/standards/{std_id}")
        assert res2.status_code == 200, res2.text
        req_id = extract_req_id(res2)

        db_row = db_conn.execute(
            "SELECT * FROM emc_standards WHERE id = ?", (std_id,)
        ).fetchone()
        assert db_row is None

        assert_log_chain(req_id, read_backend_logs)

    async def test_cannot_delete_builtin_standard(self, standard_reviewer_client):
        res = await standard_reviewer_client.get("/api/standards")
        standards = res.json()
        builtins = [s for s in standards if s.get("is_builtin")]
        if not builtins:
            pytest.skip("No builtin standards available")
        std_id = builtins[0]["id"]

        res2 = await standard_reviewer_client.delete(f"/api/standards/{std_id}")
        assert res2.status_code != 200, f"Should not delete builtin, got {res2.status_code}"
