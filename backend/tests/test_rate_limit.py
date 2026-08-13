import asyncio
import threading

import pytest

import rate_limit


@pytest.mark.asyncio
async def test_check_async_runs_sync_limiter_off_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    limiter_threads: list[int] = []

    def fake_check(ip: str, max_req: int) -> bool:
        limiter_threads.append(threading.get_ident())
        return ip == "127.0.0.1" and max_req == 3

    monkeypatch.setattr(rate_limit, "check", fake_check)

    assert await rate_limit.check_async("127.0.0.1", 3)
    assert limiter_threads and limiter_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_check_upload_async_reads_limit_off_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    calls: list[tuple[int, str, int]] = []

    monkeypatch.setattr(rate_limit, "get_max_upload", lambda: 7)
    monkeypatch.setattr(
        rate_limit,
        "check",
        lambda ip, limit: calls.append((threading.get_ident(), ip, limit)) or True,
    )

    assert await rate_limit.check_upload_async("upload-client")
    assert calls == [(calls[0][0], "upload-client", 7)]
    assert calls[0][0] != event_loop_thread
