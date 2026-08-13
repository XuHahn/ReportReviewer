import asyncio
import json

import pytest

from services.unified_model_gateway import (
    UnifiedGatewaySettings,
    UnifiedModelGateway,
    UnifiedModelUnavailable,
    _call_cloud_json_subprocess,
)


def _settings(**updates):
    values = dict(
        mode="hybrid",
        vision_base_url="http://127.0.0.1:8081/v1",
        vision_model="Qwen3-VL",
        vision_api_key="local-no-key",
        local_text_base_url="",
        local_text_model="",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_api_key="test-key",
        deepseek_flash_model="deepseek-flash",
        deepseek_pro_model="deepseek-pro",
    )
    values.update(updates)
    return UnifiedGatewaySettings(**values)


def test_manifest_contains_only_native_qwen_and_deepseek_roles():
    manifest = UnifiedModelGateway(_settings()).manifest()

    assert manifest["vision_model"] == "qwen_vision:Qwen3-VL"
    assert manifest["native_semantic_model"] == "deepseek_text:deepseek-flash"
    assert manifest["relationship_model"] == "deepseek_text:deepseek-pro"
    assert manifest["vision_fallback_enabled"] is True
    assert "layout_model" not in manifest


def test_visual_task_never_falls_back_to_text_model():
    gateway = UnifiedModelGateway(_settings(vision_base_url=""))

    with pytest.raises(UnifiedModelUnavailable, match="千问视觉"):
        gateway.resolve("visual_extraction")


def test_local_only_requires_local_text_endpoint():
    gateway = UnifiedModelGateway(_settings(mode="local_only"))

    with pytest.raises(UnifiedModelUnavailable, match="LOCAL_ONLY"):
        gateway.resolve("identity_proposal")


def test_local_text_route_is_used_when_configured():
    gateway = UnifiedModelGateway(_settings(
        mode="local_only",
        local_text_base_url="http://127.0.0.1:8082/v1",
        local_text_model="local-text",
    ))

    route = gateway.resolve("identity_rebuttal")

    assert route.provider == "local_text"
    assert route.model == "local-text"


def test_finding_explanation_uses_bounded_flash_classification_route():
    route = UnifiedModelGateway(_settings()).resolve("finding_explanation")

    assert route.provider == "deepseek_text"
    assert route.model == "deepseek-flash"
    assert route.task_kind == "classification"


@pytest.mark.asyncio
async def test_visual_generation_is_capped_below_runtime_context(monkeypatch):
    captured = {}

    class Client:
        async def _call_api(self, **kwargs):
            captured.update(kwargs)
            return {"ok": True}

    gateway = UnifiedModelGateway(_settings(
        vision_max_tokens=16000,
        vision_context_tokens=8192,
        vision_prompt_reserve_tokens=4096,
    ))
    monkeypatch.setattr(gateway, "_client", lambda _route: Client())

    result = await gateway.call_json(
        "visual_extraction",
        system_prompt="system",
        user_prompt=[{"type": "text", "text": "page"}],
        stage="vision-test",
        timeout=30,
        max_tokens=12000,
    )

    assert result == {"ok": True}
    assert captured["max_tokens"] == 4096
    assert captured["max_retries"] == 0


@pytest.mark.asyncio
async def test_call_json_has_hard_wall_clock_timeout(monkeypatch):
    class HangingClient:
        async def _call_api(self, **_kwargs):
            await asyncio.sleep(1)
            return {"late": True}

    gateway = UnifiedModelGateway(_settings(
        mode="local_only",
        local_text_base_url="http://127.0.0.1:8082/v1",
        local_text_model="local-text",
    ))
    monkeypatch.setattr(gateway, "_client", lambda _route: HangingClient())

    with pytest.raises(asyncio.TimeoutError):
        await gateway.call_json(
            "structured_extraction",
            system_prompt="system",
            user_prompt="page",
            stage="bounded-test",
            timeout=0.01,
            max_tokens=100,
        )


@pytest.mark.asyncio
async def test_hard_timeout_does_not_wait_for_transport_cancellation(monkeypatch):
    finished = asyncio.Event()

    class CancellationResistantClient:
        async def _call_api(self, **_kwargs):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)
                finished.set()
                return {"cancelled_late": True}

    gateway = UnifiedModelGateway(_settings(
        mode="local_only",
        local_text_base_url="http://127.0.0.1:8082/v1",
        local_text_model="local-text",
    ))
    monkeypatch.setattr(
        gateway, "_client", lambda _route: CancellationResistantClient(),
    )
    loop = asyncio.get_running_loop()
    started = loop.time()

    with pytest.raises(asyncio.TimeoutError):
        await gateway.call_json(
            "structured_extraction",
            system_prompt="system",
            user_prompt="page",
            stage="resistant-timeout-test",
            timeout=0.01,
            max_tokens=100,
        )

    assert loop.time() - started < 0.1
    await asyncio.wait_for(finished.wait(), timeout=0.5)


@pytest.mark.asyncio
async def test_cloud_call_uses_one_shot_worker_without_exposing_secret_in_args(monkeypatch):
    captured = {}

    class Completed:
        returncode = 0
        stdout = b'{"ok": true}'
        stderr = b""

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["payload"] = kwargs["input"]
        return Completed()

    monkeypatch.setattr("services.cloud_json_process.subprocess.run", fake_run)
    route = UnifiedModelGateway(_settings()).resolve("structured_extraction")

    result = await _call_cloud_json_subprocess(
        route,
        system_prompt="system",
        user_prompt="customer text",
        stage="cloud-test",
        timeout=1,
        max_tokens=100,
    )

    assert result == {"ok": True}
    assert "test-key" not in " ".join(str(item) for item in captured["args"])
    assert json.loads(captured["payload"])["api_key"] == "test-key"
