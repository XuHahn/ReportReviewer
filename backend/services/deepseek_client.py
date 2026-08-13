import asyncio
import hashlib
from urllib.parse import urlparse
import json
import re
import time

from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
    APIStatusError,
)
from config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL, get_deepseek_base_url
from services.cloud_json_process import call_cloud_json_process
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
BASE_DELAY_SEC = 2.0
RETRYABLE = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)
JSON_OUTPUT_FORMAT = {"type": "json_object"}

# HTTP status codes that indicate permanent (non-retryable) errors
NON_RETRYABLE_STATUS = {401, 402, 403}


def _ensure_json_instruction(system_prompt: str, user_prompt: str) -> str:
    """Ensure DeepSeek JSON Output requirements are satisfied.

    DeepSeek requires either the system or user prompt to contain "json" when
    response_format={"type": "json_object"} is used. Most project prompts
    already include JSON examples; this fallback protects smaller focused calls.
    """
    combined = f"{system_prompt}\n{user_prompt}".lower()
    if "json" in combined:
        return system_prompt
    return (
        f"{system_prompt}\n\n"
        "请仅输出合法 JSON 对象，不要输出 Markdown 或解释文本。"
        "示例 JSON 输出：{\"issues\": []}"
    )


def _is_billing_or_auth_error(exc: Exception) -> str | None:
    """Return a Chinese description if *exc* is a billing/auth error, else None."""
    if isinstance(exc, APIStatusError):
        if exc.status_code == 401:
            return f"API 认证失败 (401) —— 请检查 DEEPSEEK_API_KEY 是否正确"
        if exc.status_code == 402:
            return f"API 余额不足 (402) —— 请充值 DeepSeek 账户或切换到免费模型"
        if exc.status_code == 403:
            return f"API 权限不足 (403) —— 请检查 API Key 权限"
    # Also check for status_code attribute on other exception types
    sc = getattr(exc, 'status_code', None)
    if sc == 401:
        return f"API 认证失败 (401)"
    if sc == 402:
        return f"API 余额不足 (402)"
    if sc == 403:
        return f"API 权限不足 (403)"
    return None


def _parse_json(content: str, max_attempts: int = 4, context: dict | None = None) -> dict:
    """Parse JSON from LLM response, with aggressive tolerance for malformed output.

    Repair attempts are ordered from least-destructive to most-destructive:
    1. Balance braces/brackets (structural fix, no content change)
    2. Close unterminated strings at end (truncation fix)
    3. Fix missing commas between adjacent tokens (content-safe)
    4. Backward truncation scan (last resort, may lose data)

    Args:
        context: optional dict with stage/pass_name for log correlation.
    """
    ctx = context or {}
    content = content.strip()
    if not content:
        logger.warning("JSON_PARSE empty input", **ctx)
        return {}

    original_len = len(content)

    # Strip markdown fences
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:])
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3].rstrip()

    # Strip JS-style line comments
    content = re.sub(r'(?m)^(\s*//.*)$', '', content)
    # Strip block comments
    content = re.sub(r'/\*.*?\*/', '', content, flags=re.DOTALL)

    # Convert single-quoted keys/values to double quotes
    content = re.sub(r"(?<!\w)'(\w+)'(?=\s*:)", r'"\1"', content)
    content = re.sub(r":\s*'([^']*)'", r': "\1"', content)

    # Remove trailing commas before ] or }
    content = re.sub(r',(\s*[}\]])', r'\1', content)

    # Remove Unicode BOM and zero-width spaces
    content = content.replace('﻿', '').replace('​', '')

    # Find the outermost { } or [ ] block
    start = content.find('{')
    if start == -1:
        start = content.find('[')
    end = content.rfind('}') if start >= 0 and content[start] == '{' else content.rfind(']')
    if start >= 0 and end > start:
        content = content[start:end + 1]

    # Try parsing with increasing levels of repair
    for attempt in range(max_attempts):
        try:
            result = json.loads(content)
            logger.debug("JSON_PARSE success attempt=%d result_keys=%d",
                        attempt + 1, len(result) if isinstance(result, dict) else len(result))
            return result
        except json.JSONDecodeError as e:
            logger.warning("JSON_PARSE attempt=%d error=%s pos=%d len=%d",
                          attempt + 1, e.msg, e.pos, len(content))
            if attempt == 0:
                # Level 1: Balance braces/brackets only (non-destructive)
                open_braces = content.count('{') - content.count('}')
                open_brackets = content.count('[') - content.count(']')
                if open_braces > 0 or open_brackets > 0:
                    closers = ']' * max(0, open_brackets) + '}' * max(0, open_braces)
                    content = content + closers
                    logger.debug("JSON_PARSE balanced braces=%d brackets=%d",
                                open_braces, open_brackets)
            elif attempt == 1:
                # Level 2: Close unterminated string at end (truncation)
                # Pattern: "key": "value  →  "key": "value"
                content = re.sub(r'(:\s*"[^"]*)$', r'\1"', content)
                # Also fix dangling string values: "val"\n"key" → "val",\n"key"
                content = re.sub(r'"\s*\n\s*"', '",\n"', content)
                # Balance braces again after string fix
                ob = content.count('{') - content.count('}')
                obr = content.count('[') - content.count(']')
                if ob > 0 or obr > 0:
                    content = content + ']' * max(0, obr) + '}' * max(0, ob)
            elif attempt == 2:
                # Level 3: Fix missing commas between adjacent JSON tokens
                content = re.sub(r'"\s+(?=")', '", "', content)
                content = re.sub(r']\s+\[', '], [', content)
                content = re.sub(r'}\s+{', '}, {', content)
                content = re.sub(r'"\s+{', '", {', content)
                content = re.sub(r']\s+"', '], "', content)
                content = re.sub(r'(\d)\s+(\d)', r'\1, \2', content)  # adjacent numbers
                content = re.sub(r'(\d)\s+(?=")', r'\1, ', content)  # number before string
                # Fix trailing commas we may have introduced
                content = re.sub(r',(\s*[}\]])', r'\1', content)
                # Balance braces again
                ob = content.count('{') - content.count('}')
                obr = content.count('[') - content.count(']')
                if ob > 0 or obr > 0:
                    content = content + ']' * max(0, obr) + '}' * max(0, ob)
            else:
                # Level 4: Backward truncation scan — find longest valid JSON prefix
                scan_start = min(len(content) - 1, e.pos + 500)
                for pos in range(scan_start, max(e.pos - 200, 0), -1):
                    prefix = content[:pos].rstrip()
                    if prefix.endswith('"'):
                        pass  # string naturally ended
                    elif prefix.endswith(',') or prefix.endswith(':') or prefix.endswith('[') or prefix.endswith('{'):
                        prefix = prefix.rstrip(',: \t')
                    else:
                        last_quote = prefix.rfind('"')
                        second_last = prefix.rfind('"', 0, last_quote) if last_quote > 0 else -1
                        if last_quote > second_last:
                            prefix = prefix + '"'
                    ob = prefix.count('{') - prefix.count('}')
                    obr = prefix.count('[') - prefix.count(']')
                    prefix = prefix + ']' * max(0, obr) + '}' * max(0, ob)
                    try:
                        result = json.loads(prefix)
                        logger.warning("JSON_PARSE recovered via truncation pos=%d prefix_len=%d",
                                      pos, len(prefix))
                        return result
                    except Exception:
                        continue

    logger.error(
        "JSON_PARSE_FAILED attempts=%d original_len=%d final_len=%d content_sha256=%s",
        max_attempts, original_len, len(content),
        hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:16], **ctx,
    )
    return {}


def _parse_complete_json_object(content: str) -> tuple[dict, bool]:
    """Parse one complete JSON object without structural repair.

    Markdown fences are tolerated for compatibility, but truncated objects,
    trailing prose, and top-level arrays are rejected so callers never accept a
    repaired prefix as a complete extraction/audit result.
    """
    text = (content or "").strip()
    fenced = False
    if text.startswith("```"):
        fenced = True
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {}, fenced
    return (result, fenced) if isinstance(result, dict) else ({}, fenced)


class DeepSeekReviewer:

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None):
        """Create one JSON-output model client.

        The default remains the runtime-configured DeepSeek endpoint. The
        unified engine may provide an explicit OpenAI-compatible local endpoint; explicit clients
        are pinned and are never silently reconfigured to the cloud endpoint.
        """
        self._runtime_configured = base_url is None
        self._api_key = DEEPSEEK_API_KEY if api_key is None else api_key
        self._base_url = base_url or get_deepseek_base_url()
        self.client = AsyncOpenAI(
            api_key=self._api_key or "local-no-key",
            base_url=self._base_url,
            max_retries=0,
        )

    def _ensure_endpoint_credentials(self) -> None:
        """Require credentials only when the official cloud endpoint is used.

        Application startup and explicit local OpenAI-compatible endpoints must
        work without a DeepSeek key.  The legacy/cloud path still fails before
        any request leaves the process and returns an actionable error.
        """
        hostname = (urlparse(self._base_url).hostname or "").lower()
        if hostname in {"api.deepseek.com", "api-docs.deepseek.com"} and not self._api_key:
            raise RuntimeError(
                "DeepSeek 云端模型未配置 DEEPSEEK_API_KEY；"
                "请配置密钥，或为统一审核引擎配置本地文本模型端点。"
            )

    def _refresh_client_config(self) -> None:
        """Apply runtime API endpoint changes without restarting the service."""
        if not self._runtime_configured:
            return
        base_url = get_deepseek_base_url()
        if base_url == self._base_url:
            return
        self._base_url = base_url
        self.client = AsyncOpenAI(
            api_key=self._api_key or "local-no-key",
            base_url=base_url,
            max_retries=0,
        )
        logger.info("deepseek_client_reconfigured", base_url_host=urlparse(base_url).netloc)

    async def _call_api(self, prompt: str | list[dict], stage: str, timeout: int,
                        system_prompt: str | None = None,
                        max_tokens: int | None = None,
                        model: str | None = None,
                        temperature: float = 0,
                        thinking: bool | None = None,
                        user_id: str | None = None,
                        reasoning_effort: str | None = None,
                        task_kind: str | None = None,
                        require_complete_json: bool = True,
                        provider_extras: bool = True,
                        max_retries: int | None = None,
                        isolate_cloud: bool = True) -> dict:
        """Shared AI API call with retry, JSON parse, and error classification.

        Args:
            prompt: User message content (required).
            stage: Logging identifier (required).
            timeout: Request timeout in seconds (required).
            system_prompt: System message. Defaults to EMC reviewer persona.
            max_tokens: Max output tokens. None = model default.
            model: Model override. None = DEEPSEEK_MODEL from config.
            temperature: Sampling temperature (default 0 = deterministic).
            thinking: Explicitly enable/disable DeepSeek thinking mode.  None
                preserves the provider/model default.
            user_id: Optional stable, non-sensitive DeepSeek isolation key.
            reasoning_effort: Thinking strength (currently high/max).
            task_kind: Central policy key.  Explicit model/thinking arguments
                override the policy when a call has a documented exception.
            require_complete_json: Reject truncated/repaired JSON by default.
            max_retries: Optional per-call retry cap. None uses the global
                default; bounded batch jobs can fail fast and recover at the
                business-unit level instead of multiplying long requests.

        Returns:
            Parsed JSON dict. Empty dict if all retries exhausted on transient errors.
            Raises immediately on billing/auth errors (401/402/403).
        """
        if system_prompt is None:
            system_prompt = "你是EMC检测报告审核专家，严格按JSON格式输出审核结果。"
        prompt_text = prompt if isinstance(prompt, str) else "\n".join(
            str(part.get("text", "")) for part in prompt if part.get("type") == "text"
        )
        system_prompt = _ensure_json_instruction(system_prompt, prompt_text)
        if task_kind:
            from services.llm_policy import get_llm_policy
            policy = get_llm_policy(task_kind)
            if model is None:
                model = policy.model
            if thinking is None:
                thinking = policy.thinking
            if reasoning_effort is None:
                reasoning_effort = policy.reasoning_effort
        if model is None:
            model = DEEPSEEK_MODEL

        self._refresh_client_config()
        self._ensure_endpoint_credentials()

        retry_limit = MAX_RETRIES if max_retries is None else max(
            0, min(MAX_RETRIES, int(max_retries)),
        )
        endpoint_host = (urlparse(self._base_url).hostname or "").lower()
        use_process_boundary = (
            isolate_cloud
            and endpoint_host in {"api.deepseek.com", "api-docs.deepseek.com"}
            and isinstance(self.client, AsyncOpenAI)
        )
        if use_process_boundary:
            last_process_exc: Exception | None = None
            for attempt in range(1, retry_limit + 2):
                call_t0 = time.time()
                try:
                    result = await call_cloud_json_process({
                        "api_key": self._api_key,
                        "base_url": self._base_url,
                        "model": model,
                        "task_kind": task_kind or "structured_extraction",
                        "system_prompt": system_prompt,
                        "user_prompt": prompt,
                        "stage": stage,
                        "timeout": timeout,
                        "max_tokens": max_tokens or 16000,
                        "temperature": temperature,
                        "thinking": thinking,
                        "user_id": user_id,
                        "reasoning_effort": reasoning_effort,
                        "require_complete_json": require_complete_json,
                        "provider_extras": provider_extras,
                    }, timeout=timeout, stage=stage, model=model)
                    logger.info(
                        "deepseek_process_response_summary",
                        stage=stage,
                        model=model,
                        attempt=attempt,
                        elapsed_ms=round((time.time() - call_t0) * 1000),
                        parse_status="ok" if result else "empty",
                        top_level_keys=sorted(result.keys())[:20],
                        task_kind=task_kind,
                    )
                    if result or attempt > retry_limit:
                        return result
                except Exception as exc:
                    last_process_exc = exc
                    logger.warning(
                        "deepseek_process_attempt_failed",
                        stage=stage,
                        model=model,
                        attempt=attempt,
                        retry_limit=retry_limit,
                        error_type=type(exc).__name__,
                    )
                    if attempt > retry_limit:
                        raise
                await asyncio.sleep(BASE_DELAY_SEC * (2 ** (attempt - 1)))
            if last_process_exc:
                raise last_process_exc
            return {}
        last_exc = None
        for attempt in range(1, retry_limit + 2):
            call_t0 = time.time()
            try:
                kwargs: dict = dict(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    timeout=timeout,
                    response_format=JSON_OUTPUT_FORMAT,
                )
                if thinking is not True:
                    kwargs["temperature"] = temperature
                if provider_extras and thinking is True and reasoning_effort:
                    kwargs["reasoning_effort"] = reasoning_effort
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                extra_body: dict = {}
                if provider_extras and thinking is not None:
                    extra_body["thinking"] = {"type": "enabled" if thinking else "disabled"}
                if provider_extras and user_id:
                    extra_body["user_id"] = user_id
                if extra_body:
                    kwargs["extra_body"] = extra_body
                response = await self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content
                if not content:
                    logger.info(
                        "llm_extract_response_summary",
                        stage=stage,
                        model=model,
                        max_tokens=max_tokens,
                        elapsed_ms=round((time.time() - call_t0) * 1000),
                        empty_content=True,
                        parse_repair=False,
                        top_level_keys=[],
                        attempt=attempt,
                        thinking=thinking,
                        reasoning_effort=reasoning_effort,
                        task_kind=task_kind,
                    )
                    logger.warning("ai_empty_response", stage=stage, attempt=attempt)
                    if attempt <= retry_limit:
                        await asyncio.sleep(BASE_DELAY_SEC * (2 ** (attempt - 1)))
                        continue
                    return {}
                # Log response metadata, never customer or copyrighted source text.
                content_len = len(content)
                usage = getattr(response, "usage", None)
                cache_hit_tokens = getattr(usage, "prompt_cache_hit_tokens", None)
                cache_miss_tokens = getattr(usage, "prompt_cache_miss_tokens", None)
                logger.info(
                    "ai_call",
                    stage=stage,
                    attempt=attempt,
                    content_len=content_len,
                    model=model,
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                    task_kind=task_kind,
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                    prompt_cache_hit_tokens=cache_hit_tokens,
                    prompt_cache_miss_tokens=cache_miss_tokens,
                )
                logger.debug("ai_raw_response", stage=stage, attempt=attempt,
                           content_len=content_len,
                           content_sha256=hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:16])
                if require_complete_json:
                    result, parse_repair = _parse_complete_json_object(content)
                else:
                    result = _parse_json(content, context={"stage": stage, "attempt": attempt})
                    parse_repair = not content.strip().startswith("{")
                parse_success = bool(result)
                logger.info(
                    "llm_extract_response_summary",
                    stage=stage,
                    model=model,
                    max_tokens=max_tokens,
                    elapsed_ms=round((time.time() - call_t0) * 1000),
                    empty_content=False,
                    parse_repair=parse_repair,
                    top_level_keys=sorted(result.keys())[:20] if isinstance(result, dict) else [],
                    attempt=attempt,
                    parse_status="ok" if parse_success else "failed",
                    content_len=content_len,
                    thinking=thinking,
                    reasoning_effort=reasoning_effort,
                    task_kind=task_kind,
                    prompt_cache_hit_tokens=cache_hit_tokens,
                    prompt_cache_miss_tokens=cache_miss_tokens,
                )
                if not parse_success and attempt <= retry_limit:
                    logger.warning("ai_empty_parse_retry", stage=stage, attempt=attempt,
                                  content_len=content_len,
                                  content_sha256=hashlib.sha256(content.encode("utf-8", errors="ignore")).hexdigest()[:16])
                    await asyncio.sleep(BASE_DELAY_SEC * (2 ** (attempt - 1)))
                    continue
                if not parse_success:
                    logger.error("ai_parse_failed_final", stage=stage, attempt=attempt,
                               content_len=content_len)
                return result
            except RETRYABLE as e:
                last_exc = e
                if attempt <= retry_limit:
                    delay = BASE_DELAY_SEC * (2 ** (attempt - 1))  # 2, 4, 8
                    logger.warning(
                        "API_RETRY stage=%s attempt=%d/%d delay=%.0fs error=%s",
                        stage, attempt, retry_limit, delay, str(e)[:200],
                    )
                    await asyncio.sleep(delay)
            except Exception as e:
                billing_msg = _is_billing_or_auth_error(e)
                if billing_msg:
                    logger.error("API_FATAL stage=%s error=%s", stage, billing_msg)
                else:
                    logger.error("API_FATAL stage=%s error=%s", stage, str(e)[:300])
                raise
        logger.error("API_EXHAUSTED stage=%s retries=%d", stage, retry_limit)
        raise last_exc  # type: ignore[misc]
