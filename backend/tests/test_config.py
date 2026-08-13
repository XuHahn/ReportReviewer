import config
import database


def test_db_setting_failure_is_logged_without_setting_value(monkeypatch):
    events: list[tuple[str, dict]] = []

    def fail(_key: str) -> str:
        raise RuntimeError("sensitive database detail")

    class LoggerStub:
        def warning(self, event: str, **fields) -> None:
            events.append((event, fields))

    monkeypatch.setattr(database, "get_setting", fail)
    monkeypatch.setattr(config, "logger", LoggerStub())

    assert config._db_get("deepseek_base_url", "fallback") == "fallback"
    assert events == [("config_db_fallback", {
        "setting_key": "deepseek_base_url",
        "error_type": "RuntimeError",
    })]
