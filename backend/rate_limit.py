"""In-memory rate limiting for FastAPI endpoints."""

import asyncio
import time
import threading
from collections import defaultdict

# Default values (fallback when DB is unavailable)
_DEFAULT_WINDOW_SEC = 60
_DEFAULT_MAX_UPLOAD = 5
_DEFAULT_MAX_GENERAL = 60


def _get_int(key: str, default: int) -> int:
    try:
        from database import get_setting_int
        return get_setting_int(key)
    except Exception:
        return default


def get_window_sec() -> int:
    return _get_int("rate_window_sec", _DEFAULT_WINDOW_SEC)


def get_max_upload() -> int:
    return _get_int("rate_max_upload", _DEFAULT_MAX_UPLOAD)


def get_max_general() -> int:
    return _get_int("rate_max_general", _DEFAULT_MAX_GENERAL)


# Module-level aliases for backward compatibility
WINDOW_SEC = _DEFAULT_WINDOW_SEC
MAX_UPLOAD = _DEFAULT_MAX_UPLOAD
MAX_GENERAL = _DEFAULT_MAX_GENERAL

_store: dict[str, list[float]] = defaultdict(list)
_lock = threading.Lock()
_call_count = 0
_CLEANUP_EVERY = 500  # sweep stale IPs every N calls


def _cleanup_stale_unlocked() -> None:
    now = time.time()
    cutoff = now - get_window_sec()
    stale = [ip for ip, times in _store.items() if not any(t > cutoff for t in times)]
    for ip in stale:
        del _store[ip]


def check(ip: str, max_req: int) -> bool:
    """Returns True if request is allowed, False if rate-limited."""
    global _call_count
    with _lock:
        _call_count += 1
        if _call_count % _CLEANUP_EVERY == 0:
            _cleanup_stale_unlocked()

        now = time.time()
        cutoff = now - get_window_sec()
        _store[ip] = [t for t in _store[ip] if t > cutoff]
        if len(_store[ip]) >= max_req:
            return False
        _store[ip].append(now)
        return True


async def check_async(ip: str, max_req: int) -> bool:
    """Run the synchronous, cross-thread-safe limiter away from the event loop.

    ``check`` reads live settings from SQLite before entering its very short
    in-memory critical section.  Keeping the threading lock is intentional:
    the store can be shared by request threads, while the async wrapper avoids
    blocking FastAPI's event loop on that settings read.
    """
    return await asyncio.to_thread(check, ip, max_req)


async def check_upload_async(ip: str) -> bool:
    """Apply the live upload limit without doing any SQLite work on the event loop."""
    return await asyncio.to_thread(lambda: check(ip, get_max_upload()))
