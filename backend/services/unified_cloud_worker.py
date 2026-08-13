"""One-shot cloud JSON worker used by the unified review hard-timeout boundary."""

from __future__ import annotations

import asyncio
import json
import sys

from services.deepseek_client import DeepSeekReviewer
from utils.logger import init_logging


async def _run(payload: dict) -> dict:
    reviewer = DeepSeekReviewer(
        api_key=str(payload.get("api_key") or ""),
        base_url=str(payload.get("base_url") or ""),
    )
    return await reviewer._call_api(
        prompt=payload.get("user_prompt") or "",
        system_prompt=str(payload.get("system_prompt") or ""),
        stage=str(payload.get("stage") or "unified_cloud_worker"),
        timeout=int(payload.get("timeout") or 60),
        max_tokens=int(payload.get("max_tokens") or 16000),
        model=str(payload.get("model") or ""),
        task_kind=str(payload.get("task_kind") or "structured_extraction"),
        temperature=float(payload.get("temperature") or 0),
        thinking=payload.get("thinking"),
        user_id=payload.get("user_id"),
        reasoning_effort=payload.get("reasoning_effort"),
        require_complete_json=bool(payload.get("require_complete_json", True)),
        provider_extras=bool(payload.get("provider_extras", True)),
        max_retries=0,
        isolate_cloud=False,
    )


def main() -> int:
    init_logging(env="prod")
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        result = asyncio.run(_run(payload))
        sys.stdout.write(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Do not echo prompts, responses, credentials, or customer content.
        sys.stderr.write(f"unified cloud worker failed: {type(exc).__name__}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
