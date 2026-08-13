"""System Settings data-consistency tests."""

import pytest
from tests.utils import assert_api_db, assert_log_chain, extract_req_id


class TestSettingsRead:

    async def test_get_settings_returns_all_keys(self, admin_client, db_conn):
        res = await admin_client.get("/api/admin/settings")
        assert res.status_code == 200
        body = res.json()

        for key in ("jwt_expire_hours", "max_upload_bytes", "rate_max_upload"):
            assert key in body["settings"], f"Missing key: {key}"

        db_settings = dict(db_conn.execute(
            "SELECT key, value FROM system_settings"
        ).fetchall())

        for key, value in db_settings.items():
            if key in body["settings"]:
                assert_api_db(body["settings"][key], value, f"settings.{key}")


class TestSettingsUpdate:

    async def test_update_setting_api_matches_db(self, admin_client, db_conn, read_backend_logs):
        res = await admin_client.put(
            "/api/admin/settings",
            json={"settings": {"jwt_expire_hours": "72", "max_upload_bytes": "52428800"}},
        )
        assert res.status_code == 200, res.text
        req_id = extract_req_id(res)

        db_row = db_conn.execute(
            "SELECT value FROM system_settings WHERE key = ?", ("jwt_expire_hours",)
        ).fetchone()
        assert db_row["value"] == "72"

        db_row2 = db_conn.execute(
            "SELECT value FROM system_settings WHERE key = ?", ("max_upload_bytes",)
        ).fetchone()
        assert db_row2["value"] == "52428800"

        assert_log_chain(req_id, read_backend_logs)

    async def test_update_single_preserves_others(self, admin_client, db_conn):
        await admin_client.put("/api/admin/settings", json={
            "settings": {"max_upload_bytes": "99999999", "rate_max_upload": "42"},
        })
        await admin_client.put("/api/admin/settings", json={
            "settings": {"max_upload_bytes": "88888888"},
        })

        db_row = db_conn.execute(
            "SELECT value FROM system_settings WHERE key = ?", ("rate_max_upload",)
        ).fetchone()
        assert db_row["value"] == "42"

    async def test_rejects_unknown_invalid_and_insecure_settings(self, admin_client):
        assert (await admin_client.put(
            "/api/admin/settings", json={"settings": {"unknown": "1"}},
        )).status_code == 422
        assert (await admin_client.put(
            "/api/admin/settings", json={"settings": {"rate_window_sec": "0"}},
        )).status_code == 422
        assert (await admin_client.put(
            "/api/admin/settings", json={"settings": {"deepseek_base_url": "http://example.com"}},
        )).status_code == 422
