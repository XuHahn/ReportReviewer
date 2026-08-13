"""OS-killable boundary for cloud JSON model requests."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys

from utils.logger import get_logger


logger = get_logger(__name__)


async def call_cloud_json_process(
    payload: dict,
    *,
    timeout: int,
    stage: str,
    model: str,
) -> dict:
    """Execute one request in a disposable process with a hard deadline."""
    encoded = json.dumps(payload, ensure_ascii=False).encode()

    def _run() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-m", "services.unified_cloud_worker"],
            input=encoded,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )

    try:
        completed = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired as exc:
        logger.warning(
            "cloud_json_process_timeout",
            stage=stage,
            model=model,
            timeout_sec=timeout,
        )
        raise TimeoutError(f"{stage} exceeded {timeout}s process limit") from exc
    if completed.returncode != 0:
        raise RuntimeError(f"{stage} cloud worker exited with code {completed.returncode}")
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{stage} cloud worker returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"{stage} cloud worker returned a non-object result")
    return result
