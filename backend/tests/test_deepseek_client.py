from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.deepseek_client import (
    DeepSeekReviewer, JSON_OUTPUT_FORMAT, _parse_complete_json_object,
)


class _FakeCompletions:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"issues": []}')
                )
            ]
        )


class _FakeClient:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()
        self.chat = SimpleNamespace(completions=self.completions)


class _SequenceCompletions:
    def __init__(self, contents: list[str | None]) -> None:
        self.contents = list(contents)
        self.calls = 0

    async def create(self, **kwargs):
        content = self.contents[self.calls]
        self.calls += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


@pytest.mark.asyncio
async def test_call_api_uses_deepseek_json_output():
    reviewer = DeepSeekReviewer()
    fake_client = _FakeClient()
    reviewer.client = fake_client

    result = await reviewer._call_api(
        prompt='请输出 JSON：{"issues": []}',
        stage="unit",
        timeout=5,
    )

    assert result == {"issues": []}
    assert fake_client.completions.kwargs["response_format"] == JSON_OUTPUT_FORMAT


@pytest.mark.asyncio
async def test_call_api_adds_json_instruction_when_missing():
    reviewer = DeepSeekReviewer()
    fake_client = _FakeClient()
    reviewer.client = fake_client

    await reviewer._call_api(
        prompt="只返回问题列表",
        stage="unit",
        timeout=5,
        system_prompt="你是审核专家。",
    )

    messages = fake_client.completions.kwargs["messages"]
    assert "JSON" in messages[0]["content"]
    assert fake_client.completions.kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_classification_policy_uses_flash_without_thinking():
    reviewer = DeepSeekReviewer()
    fake_client = _FakeClient()
    reviewer.client = fake_client

    await reviewer._call_api(
        prompt='返回 JSON：{"category": ""}',
        stage="classification",
        timeout=5,
        task_kind="classification",
    )

    kwargs = fake_client.completions.kwargs
    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    assert kwargs["temperature"] == 0
    assert "reasoning_effort" not in kwargs


@pytest.mark.asyncio
async def test_semantic_audit_policy_uses_pro_high_thinking():
    reviewer = DeepSeekReviewer()
    fake_client = _FakeClient()
    reviewer.client = fake_client

    await reviewer._call_api(
        prompt='返回 JSON：{"issues": []}',
        stage="semantic",
        timeout=5,
        task_kind="semantic_audit",
    )

    kwargs = fake_client.completions.kwargs
    assert kwargs["model"] == "deepseek-v4-pro"
    assert kwargs["extra_body"]["thinking"] == {"type": "enabled"}
    assert kwargs["reasoning_effort"] == "high"
    assert "temperature" not in kwargs


@pytest.mark.asyncio
async def test_bounded_reconciliation_uses_flash_without_thinking():
    reviewer = DeepSeekReviewer()
    fake_client = _FakeClient()
    reviewer.client = fake_client

    await reviewer._call_api(
        prompt='返回 JSON：{"decisions": []}',
        stage="reconciliation",
        timeout=5,
        task_kind="bounded_reconciliation",
    )

    kwargs = fake_client.completions.kwargs
    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in kwargs


def test_complete_json_parser_rejects_truncated_prefix():
    result, repaired = _parse_complete_json_object('{"issues": [{"description": "x"}')
    assert result == {}
    assert repaired is False


def test_complete_json_parser_accepts_fenced_complete_object():
    result, repaired = _parse_complete_json_object('```json\n{"issues": []}\n```')
    assert result == {"issues": []}
    assert repaired is True


@pytest.mark.asyncio
async def test_call_api_retries_truncated_json_instead_of_accepting_repair(monkeypatch):
    reviewer = DeepSeekReviewer()
    completions = _SequenceCompletions([
        '{"issues": [{"description": "truncated"}',
        '{"issues": []}',
    ])
    reviewer.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("services.deepseek_client.asyncio.sleep", no_sleep)

    result = await reviewer._call_api(
        prompt='返回 JSON：{"issues": []}',
        stage="retry-truncated",
        timeout=5,
    )

    assert result == {"issues": []}
    assert completions.calls == 2


@pytest.mark.asyncio
async def test_call_api_honors_per_call_retry_cap(monkeypatch):
    reviewer = DeepSeekReviewer()
    completions = _SequenceCompletions([
        '{"issues": [{"description": "truncated"}',
        '{"issues": []}',
    ])
    reviewer.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("services.deepseek_client.asyncio.sleep", no_sleep)
    result = await reviewer._call_api(
        prompt='返回 JSON：{"issues": []}', stage="bounded-retry",
        timeout=5, max_retries=0,
    )

    assert result == {}
    assert completions.calls == 1


@pytest.mark.asyncio
async def test_official_cloud_call_uses_killable_process_boundary(monkeypatch):
    reviewer = DeepSeekReviewer(api_key="test-key", base_url="https://api.deepseek.com")
    calls = []

    async def fake_process(payload, *, timeout, stage, model):
        calls.append((payload, timeout, stage, model))
        return {"issues": []}

    monkeypatch.setattr("services.deepseek_client.call_cloud_json_process", fake_process)
    result = await reviewer._call_api(
        prompt='返回 JSON：{"issues": []}', stage="isolated", timeout=7,
        task_kind="structured_extraction", max_retries=0,
    )

    assert result == {"issues": []}
    assert len(calls) == 1
    assert calls[0][1:] == (7, "isolated", "deepseek-v4-pro")
    assert calls[0][0]["api_key"] == "test-key"


@pytest.mark.asyncio
async def test_official_cloud_endpoint_requires_key_at_call_time():
    reviewer = DeepSeekReviewer(api_key="", base_url="https://api.deepseek.com")
    reviewer.client = _FakeClient()

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        await reviewer._call_api(
            prompt='返回 JSON：{"issues": []}',
            stage="missing-key",
            timeout=5,
        )


@pytest.mark.asyncio
async def test_local_endpoint_can_run_without_deepseek_key():
    reviewer = DeepSeekReviewer(api_key="", base_url="http://127.0.0.1:9001/v1")
    fake_client = _FakeClient()
    reviewer.client = fake_client

    result = await reviewer._call_api(
        prompt='返回 JSON：{"issues": []}',
        stage="local-no-key",
        timeout=5,
        provider_extras=False,
    )

    assert result == {"issues": []}
