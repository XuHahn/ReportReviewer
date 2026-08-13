"""Model routing for the single evidence-graph review engine."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_FLASH_MODEL,
    DEEPSEEK_PRO_MODEL,
    get_deepseek_base_url,
)
from services.deepseek_client import DeepSeekReviewer
from services.cloud_json_process import call_cloud_json_process
from utils.logger import get_logger


logger = get_logger(__name__)

UnifiedTask = Literal[
    "structured_extraction",
    "visual_extraction",
    "identity_proposal",
    "identity_rebuttal",
    "relationship_proposal",
    "relationship_rebuttal",
    "standard_advisory",
    "semantic_check",
    "finding_explanation",
]


class UnifiedModelUnavailable(RuntimeError):
    pass


def _consume_detached_task(task: asyncio.Task) -> None:
    """Consume a timed-out transport task once its cancellation settles."""
    try:
        task.exception()
    except (asyncio.CancelledError, Exception):
        pass


async def _call_cloud_json_subprocess(
    route: "UnifiedModelRoute",
    *,
    system_prompt: str,
    user_prompt: str | list[dict],
    stage: str,
    timeout: int,
    max_tokens: int,
) -> dict:
    """Run a cloud model call behind an OS-killable wall-clock boundary."""
    payload = {
        "api_key": route.api_key,
        "base_url": route.base_url,
        "model": route.model,
        "task_kind": route.task_kind,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "stage": stage,
        "timeout": timeout,
        "max_tokens": max_tokens,
    }
    return await call_cloud_json_process(
        payload, timeout=timeout, stage=stage, model=route.model,
    )


@dataclass(frozen=True)
class UnifiedModelRoute:
    task: str
    provider: Literal["qwen_vision", "deepseek_text", "local_text", "test"]
    model: str
    base_url: str
    api_key: str
    task_kind: str
    supports_vision: bool = False
    cloud: bool = False


@dataclass(frozen=True)
class UnifiedGatewaySettings:
    mode: Literal["hybrid", "local_only", "test"] = "hybrid"
    vision_base_url: str = ""
    vision_model: str = "mlx-community/Qwen3-VL-4B-Instruct-3bit"
    vision_api_key: str = "local-no-key"
    vision_max_tokens: int = 4096
    vision_context_tokens: int = 8192
    vision_prompt_reserve_tokens: int = 4096
    local_text_base_url: str = ""
    local_text_model: str = ""
    local_text_api_key: str = "local-no-key"
    deepseek_base_url: str = ""
    deepseek_api_key: str = ""
    deepseek_flash_model: str = ""
    deepseek_pro_model: str = ""

    @classmethod
    def from_env(cls) -> "UnifiedGatewaySettings":
        mode = os.getenv("UNIFIED_REVIEW_PROVIDER_MODE", "hybrid").strip().lower()
        if mode not in {"hybrid", "local_only", "test"}:
            raise ValueError(f"无效的 UNIFIED_REVIEW_PROVIDER_MODE: {mode}")
        return cls(
            mode=mode,  # type: ignore[arg-type]
            vision_base_url=os.getenv("UNIFIED_REVIEW_VISION_BASE_URL", "").rstrip("/"),
            vision_model=os.getenv(
                "UNIFIED_REVIEW_VISION_MODEL", "mlx-community/Qwen3-VL-4B-Instruct-3bit",
            ),
            vision_api_key=os.getenv("UNIFIED_REVIEW_VISION_API_KEY", "local-no-key"),
            vision_max_tokens=max(1, int(os.getenv("UNIFIED_REVIEW_VISION_MAX_TOKENS", "4096"))),
            vision_context_tokens=max(
                2, int(os.getenv("UNIFIED_REVIEW_VISION_MAX_KV_SIZE", "8192")),
            ),
            vision_prompt_reserve_tokens=max(
                1, int(os.getenv("UNIFIED_REVIEW_VISION_PROMPT_RESERVE_TOKENS", "4096")),
            ),
            local_text_base_url=os.getenv("UNIFIED_REVIEW_TEXT_BASE_URL", "").rstrip("/"),
            local_text_model=os.getenv("UNIFIED_REVIEW_TEXT_MODEL", ""),
            local_text_api_key=os.getenv("UNIFIED_REVIEW_TEXT_API_KEY", "local-no-key"),
            deepseek_base_url=get_deepseek_base_url().rstrip("/"),
            deepseek_api_key=DEEPSEEK_API_KEY,
            deepseek_flash_model=DEEPSEEK_FLASH_MODEL,
            deepseek_pro_model=DEEPSEEK_PRO_MODEL,
        )


_TASK_KINDS = {
    "structured_extraction": "structured_extraction",
    "visual_extraction": "structured_extraction",
    "identity_proposal": "semantic_audit",
    "identity_rebuttal": "semantic_audit",
    "relationship_proposal": "semantic_audit",
    "relationship_rebuttal": "semantic_audit",
    "standard_advisory": "knowledge_reasoning",
    "semantic_check": "semantic_audit",
    # This is non-authoritative, evidence-bound copy.  Keep it on the short
    # classification policy so a slow reasoning request cannot hold up the
    # actual audit result.
    "finding_explanation": "classification",
}


class UnifiedModelGateway:
    def __init__(self, settings: UnifiedGatewaySettings | None = None):
        self.settings = settings or UnifiedGatewaySettings.from_env()
        self._clients: dict[tuple[str, str], DeepSeekReviewer] = {}

    def resolve(self, task: str) -> UnifiedModelRoute:
        if task not in _TASK_KINDS:
            raise UnifiedModelUnavailable(f"未知模型任务: {task}")
        settings = self.settings
        if settings.mode == "test":
            return UnifiedModelRoute(
                task=task, provider="test", model="unified-test-double", base_url="",
                api_key="", task_kind=_TASK_KINDS[task], supports_vision=task == "visual_extraction",
            )
        if task == "visual_extraction":
            if not settings.vision_base_url or not settings.vision_model:
                raise UnifiedModelUnavailable("千问视觉模型端点尚未配置")
            return UnifiedModelRoute(
                task=task,
                provider="qwen_vision",
                model=settings.vision_model,
                base_url=settings.vision_base_url,
                api_key=settings.vision_api_key,
                task_kind=_TASK_KINDS[task],
                supports_vision=True,
            )
        if settings.local_text_base_url and settings.local_text_model:
            return UnifiedModelRoute(
                task=task,
                provider="local_text",
                model=settings.local_text_model,
                base_url=settings.local_text_base_url,
                api_key=settings.local_text_api_key,
                task_kind=_TASK_KINDS[task],
            )
        if settings.mode == "local_only":
            raise UnifiedModelUnavailable(f"LOCAL_ONLY模式未配置文本任务 {task} 的模型端点")
        if not settings.deepseek_api_key:
            raise UnifiedModelUnavailable("DeepSeek API Key尚未配置")
        model = (
            settings.deepseek_flash_model
            if task in {"structured_extraction", "finding_explanation"}
            else settings.deepseek_pro_model
        )
        return UnifiedModelRoute(
            task=task,
            provider="deepseek_text",
            model=model,
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            task_kind=_TASK_KINDS[task],
            cloud=True,
        )

    def manifest(self) -> dict:
        def selected(task: str) -> str:
            try:
                route = self.resolve(task)
                return f"{route.provider}:{route.model}"
            except UnifiedModelUnavailable:
                return "unconfigured"

        return {
            "provider_mode": self.settings.mode,
            "extraction_strategy": "native_plus_targeted_qwen",
            "native_semantic_model": selected("structured_extraction"),
            "vision_model": selected("visual_extraction"),
            "relationship_model": selected("relationship_proposal"),
            "rebuttal_model": selected("relationship_rebuttal"),
            "vision_fallback_enabled": bool(self.settings.vision_base_url),
        }

    def _client(self, route: UnifiedModelRoute) -> DeepSeekReviewer:
        key = (route.provider, route.base_url)
        client = self._clients.get(key)
        if client is None:
            client = DeepSeekReviewer(api_key=route.api_key, base_url=route.base_url)
            self._clients[key] = client
        return client

    async def call_json(
        self,
        task: str,
        *,
        system_prompt: str,
        user_prompt: str | list[dict],
        stage: str,
        timeout: int,
        max_tokens: int,
    ) -> dict:
        route = self.resolve(task)
        if route.provider == "test":
            raise UnifiedModelUnavailable("测试路由必须由测试替身注入")
        visual_generation_limit = max(
            1,
            self.settings.vision_context_tokens
            - self.settings.vision_prompt_reserve_tokens,
        )
        bounded_max_tokens = (
            min(max_tokens, self.settings.vision_max_tokens, visual_generation_limit)
            if route.supports_vision else max_tokens
        )
        if route.provider == "deepseek_text":
            return await _call_cloud_json_subprocess(
                route,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                stage=stage,
                timeout=timeout,
                max_tokens=bounded_max_tokens,
            )
        # The OpenAI-compatible client's socket timeout is not a sufficient
        # wall-clock bound for every failure mode (for example a half-open
        # connection can outlive it).  Unified review has hundreds of bounded
        # units, so one unit must fail fast and become needs_review instead of
        # stalling the whole run.  Business-level recovery handles the gap;
        # transport retries here would multiply that delay.
        client = self._client(route)
        call_task = asyncio.create_task(
            client._call_api(
                prompt=user_prompt,
                system_prompt=system_prompt,
                stage=stage,
                timeout=timeout,
                max_tokens=bounded_max_tokens,
                model=route.model,
                task_kind=route.task_kind,
                provider_extras=route.provider == "deepseek_text",
                max_retries=0,
            )
        )
        done, _ = await asyncio.wait({call_task}, timeout=timeout)
        if call_task not in done:
            # asyncio.wait_for waits for cancellation cleanup, which can itself
            # remain stuck on a half-open HTTP connection.  Detach cleanup so
            # the review loop observes the promised wall-clock deadline.
            call_task.cancel()
            call_task.add_done_callback(_consume_detached_task)
            # Do not reuse a pool that produced a half-open request.  The
            # detached task keeps its own client reference until cleanup.
            self._clients.pop((route.provider, route.base_url), None)
            logger.warning(
                "unified_model_hard_timeout",
                stage=stage,
                provider=route.provider,
                model=route.model,
                timeout_sec=timeout,
            )
            raise asyncio.TimeoutError(f"{stage} exceeded {timeout}s wall-clock limit")
        return call_task.result()

    async def preflight(self, tasks: tuple[str, ...]) -> list[str]:
        routes: dict[tuple[str, str, str], UnifiedModelRoute] = {}
        errors: list[str] = []
        for task in tasks:
            try:
                route = self.resolve(task)
            except UnifiedModelUnavailable as exc:
                errors.append(str(exc))
                continue
            if route.provider in {"test", "deepseek_text"}:
                continue
            routes[(route.provider, route.base_url, route.model)] = route

        async def probe(route: UnifiedModelRoute) -> str:
            headers = {}
            if route.api_key and route.api_key != "local-no-key":
                headers["Authorization"] = f"Bearer {route.api_key}"
            try:
                timeout = max(1, int(os.getenv("UNIFIED_REVIEW_PREFLIGHT_TIMEOUT", "5")))
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    response = await client.get(route.base_url.rstrip("/") + "/models", headers=headers)
                    response.raise_for_status()
                    payload = response.json()
                model_ids = {
                    str(item.get("id") or "") for item in payload.get("data", [])
                    if isinstance(item, dict)
                }
                if model_ids and route.model not in model_ids:
                    raise ValueError("configured model not reported by endpoint")
                logger.info(
                    "unified_model_preflight",
                    provider=route.provider,
                    model=route.model,
                    endpoint_host=urlparse(route.base_url).netloc,
                    ready=True,
                )
                return ""
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "unified_model_preflight",
                    provider=route.provider,
                    model=route.model,
                    endpoint_host=urlparse(route.base_url).netloc,
                    ready=False,
                    error_type=type(exc).__name__,
                )
                return f"{route.provider}模型{route.model}未就绪"

        results = await asyncio.gather(*(probe(route) for route in routes.values()))
        errors.extend(item for item in results if item)
        return errors
