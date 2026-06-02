import asyncio
import json
import re
import time
from collections.abc import Iterator

from openai import (
    OpenAI,
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from bs4 import BeautifulSoup
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL
from services.prompts import (
    SCAN_PROMPT, REVIEW_PROMPT,
    MAX_CONTEXT_TOKENS, PROMPT_OVERHEAD_CHARS, estimate_tokens,
    HIGH_KEYWORDS, MEDIUM_KEYWORDS,
)
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
BASE_DELAY_SEC = 2.0
RETRYABLE = (APIConnectionError, APITimeoutError, InternalServerError, RateLimitError)


def _parse_json(content: str, max_attempts: int = 3) -> dict:
    """Parse JSON from LLM response, with aggressive tolerance for malformed output."""
    content = content.strip()
    if not content:
        return {}

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
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning("JSON_PARSE attempt=%d error=%s pos=%d", attempt + 1, e.msg, e.pos)
            if attempt == 0:
                # Fix: missing commas between adjacent values
                content = re.sub(r'"\s+"', '", "', content)
                content = re.sub(r']\s+\[', '], [', content)
                content = re.sub(r'}\s+{', '}, {', content)
                content = re.sub(r'"\s+{', '", {', content)
                content = re.sub(r']\s+"', '], "', content)
            elif attempt == 1:
                # Fix: unescaped newlines inside quoted strings
                content = re.sub(r'(?<=[^\\])\n', r'\\n', content)
            else:
                # Fix: try to parse the longest valid prefix
                for pos in range(len(content) - 1, max(e.pos - 100, 0), -1):
                    try:
                        return json.loads(content[:pos] + '}')
                    except Exception:
                        continue

    logger.error("JSON_PARSE_FAILED after %d attempts, returning {}", max_attempts)
    return {}


def _priority(heading: str, content_head: str) -> str:
    """Classify a section as high/medium/low based on keyword matching."""
    text = (heading + " " + content_head[:200]).lower()
    for kw in HIGH_KEYWORDS:
        if kw in text:
            return "high"
    for kw in MEDIUM_KEYWORDS:
        if kw in text:
            return "medium"
    return "low"


def extract_classify_sections(html_content: str, plain_text: str) -> list[dict]:
    """Split report into sections by HTML headings, classify each by priority.
    Returns [{heading, priority, text, skipped}], ordered by document position."""
    soup = BeautifulSoup(html_content, "lxml")
    headings = soup.find_all(["h1", "h2", "h3", "h4"])

    if not headings:
        return [{"heading": "全文", "priority": "high", "text": plain_text, "skipped": False}]

    sections = []
    # Extract the leading content before first heading
    first_heading = headings[0]
    lead_text = ""
    for sibling in first_heading.find_all_previous():
        if sibling.name in ("h1", "h2", "h3", "h4"):
            break
        lead_text = sibling.get_text() + "\n" + lead_text
    if lead_text.strip():
        sections.append({
            "heading": "报告首页",
            "priority": _priority("报告首页", lead_text),
            "text": lead_text.strip(),
            "skipped": False,
        })

    for h in headings:
        heading_text = h.get_text().strip()
        if not heading_text or len(heading_text) > 200:
            continue

        body_parts = []
        for sibling in h.find_next_siblings():
            if sibling.name in ("h1", "h2", "h3", "h4"):
                break
            body_parts.append(sibling.get_text())
        body = "\n".join(body_parts).strip()
        body = re.sub(r'\n{3,}', '\n\n', body)

        section_text = f"{heading_text}\n{body}" if body else heading_text

        sections.append({
            "heading": heading_text,
            "priority": _priority(heading_text, body),
            "text": section_text,
            "skipped": False,
        })

    return sections


def build_filtered_text(sections: list[dict], token_limit: int) -> tuple[str, list[str]]:
    """Build review text from sections: high=full, medium=heading+500chars, low=skip.
    Returns (filtered_text, list_of_skipped_headings)."""
    parts = []
    skipped = []

    for sec in sections:
        if sec["priority"] == "high":
            parts.append(sec["text"])
            sec["skipped"] = False
        elif sec["priority"] == "medium":
            short = sec["text"][:len(sec["heading"]) + 500]
            parts.append(short)
            sec["skipped"] = False
        else:
            sec["skipped"] = True
            skipped.append(sec["heading"])

    # Ensure combined text fits within token limit
    combined = "\n\n".join(parts)
    while estimate_tokens(combined) > token_limit and parts:
        # Drop medium sections first, then shortest high sections
        dropped = None
        for i in range(len(parts) - 1, -1, -1):
            for sec in sections:
                if sec.get("skipped"):
                    continue
                if sec["priority"] == "medium" and sec["text"] in parts[i]:
                    dropped = sec
                    break
            if dropped:
                break
        if not dropped:
            # Drop shortest high section
            high_secs = [(s, s["text"]) for s in sections if s["priority"] == "high" and not s["skipped"]]
            if high_secs:
                dropped = min(high_secs, key=lambda x: len(x[1]))
                dropped = dropped[0]
        if dropped:
            parts = [p for p in parts if dropped["text"] not in p]
            dropped["skipped"] = True
            skipped.append(dropped["heading"])
        else:
            break

    return "\n\n".join(parts), skipped


def prepare_review_text(plain_text: str, toc: str, html_content: str) -> tuple[str, list[str], int, int]:
    """Token estimation + section priority filtering (shared by blocking & streaming paths).

    Returns (review_text, skipped_sections, estimated_tokens, token_limit).
    If the report fits in context, skipped_sections is empty and review_text == plain_text.
    """
    estimated = estimate_tokens(plain_text + toc) + int(PROMPT_OVERHEAD_CHARS / 1.8)
    token_limit = MAX_CONTEXT_TOKENS
    skipped_sections: list[str] = []
    review_text = plain_text

    if estimated > token_limit:
        sections = extract_classify_sections(html_content, plain_text)
        safe_limit = token_limit - int(PROMPT_OVERHEAD_CHARS / 1.8) - 500
        review_text, skipped_sections = build_filtered_text(sections, safe_limit)
        if review_text.strip():
            estimated = estimate_tokens(review_text + toc) + int(PROMPT_OVERHEAD_CHARS / 1.8)

    return review_text, skipped_sections, estimated, token_limit


class DeepSeekReviewer:

    def __init__(self):
        self.client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )

    async def review_report(self, plain_text: str, toc: str = "",
                            html_content: str = "",
                            user_rules: str = "",
                            standard_limits: str = "") -> dict:
        if not plain_text.strip():
            return {"overall_result": "error", "review_items": [], "summary": "无内容可审核"}

        review_text, skipped_sections, estimated, token_limit = \
            prepare_review_text(plain_text, toc, html_content)

        if not review_text.strip():
            if not html_content:
                summary = "报告过长且无法按章节过滤（缺少HTML），请上传Word/PDF格式"
            else:
                summary = "报告内容无法提取有效审核章节"
            return {
                "overall_result": "error", "review_items": [], "summary": summary,
                "estimated_tokens": estimated, "token_limit": token_limit, "truncated": True,
            }

        # ── Stage 1: coarse scan ──
        scan_prompt = SCAN_PROMPT.format(report_content=review_text, toc=toc, user_rules=user_rules)

        try:
            checklist = await self._call_api(scan_prompt, "stage1", timeout=180)
        except Exception as e:
            return {"overall_result": "error", "review_items": [],
                    "summary": f"第一轮扫描失败: {str(e)}"}

        items = checklist.get("checklist", [])
        if not items:
            skip_note = ""
            if skipped_sections:
                skip_note = f"。注意：以下低优先级章节未审核——{', '.join(skipped_sections[:8])}"
            return {
                "overall_result": "pass",
                "review_items": [],
                "summary": "未发现疑似问题" + skip_note,
                "estimated_tokens": estimated,
                "token_limit": token_limit,
                "truncated": False,
            }

        # ── Stage 2: detailed review ──
        checklist_json = json.dumps(items, ensure_ascii=False, indent=2)
        review_prompt = REVIEW_PROMPT.format(
            report_content=review_text, toc=toc, checklist=checklist_json,
            user_rules=user_rules, standard_limits=standard_limits,
        )

        try:
            result = await self._call_api(review_prompt, "stage2", timeout=300)
        except Exception as e:
            return {"overall_result": "error", "review_items": [],
                    "summary": f"第二轮深度审核失败: {str(e)}"}

        if skipped_sections:
            result["summary"] = (result.get("summary", "") +
                                 f"。注意：以下低优先级章节因超限未审核——{', '.join(skipped_sections[:8])}")

        result["estimated_tokens"] = estimated
        result["token_limit"] = token_limit
        result["truncated"] = bool(skipped_sections)
        return result

    async def compare_reviews(self, old_items: list[dict], new_items: list[dict]) -> dict:
        old_summary = json.dumps([
            {"idx": i, "severity": it.get("severity",""), "location": it.get("location",""),
             "original_text": it.get("original_text","")[:200], "error_description": it.get("error_description","")[:200]}
            for i, it in enumerate(old_items)
        ], ensure_ascii=False)
        new_summary = json.dumps([
            {"idx": i, "severity": it.get("severity",""), "location": it.get("location",""),
             "original_text": it.get("original_text","")[:200], "error_description": it.get("error_description","")[:200]}
            for i, it in enumerate(new_items)
        ], ensure_ascii=False)

        prompt = f"""你是EMC检测报告审核专家。请对比同一份报告的旧版本审核结果和新版本审核结果。

旧版本审核结果（{len(old_items)}项）：
{old_summary}

新版本审核结果（{len(new_items)}项）：
{new_summary}

请判断每一项的状态：
- "fixed": 旧版本的问题在新版本中已修复
- "persistent": 旧版本的问题在新版本中仍然存在
- "new": 新版本中发现的新问题

返回JSON格式：
{{"items": [{{"old_idx": 数字或null, "new_idx": 数字或null, "status": "fixed"|"new"|"persistent"}}]}}"""
        resp = await self._call_api(prompt, "compare", 120)
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        return _parse_json(content)

    async def _call_api(self, prompt: str, stage: str, timeout: int) -> dict:
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 2):  # 4 total: 1 initial + 3 retries
            try:
                response = self.client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {"role": "system", "content": "你是EMC检测报告审核专家，严格按JSON格式输出审核结果。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    timeout=timeout,
                )
                content = response.choices[0].message.content
                return _parse_json(content)
            except RETRYABLE as e:
                last_exc = e
                if attempt <= MAX_RETRIES:
                    delay = BASE_DELAY_SEC * (2 ** (attempt - 1))  # 2, 4, 8
                    logger.warning(
                        "API_RETRY stage=%s attempt=%d/%d delay=%.0fs error=%s",
                        stage, attempt, MAX_RETRIES, delay, str(e)[:200],
                    )
                    await asyncio_sleep(delay)
            except Exception as e:
                # Non-retryable (auth, bad request) — fail immediately
                logger.error("API_FATAL stage=%s error=%s", stage, str(e))
                raise
        logger.error("API_EXHAUSTED stage=%s retries=%d", stage, MAX_RETRIES)
        # type: ignore[misc] — last_exc is guaranteed set here because the loop only
        # exits via the except branch (which assigns last_exc) or this final raise.
        raise last_exc  # type: ignore[misc]

    def stream_stage1(self, prompt: str) -> Iterator[str]:
        """Stream DeepSeek API response for Stage 1 (coarse scan).
        Yields content delta strings from the streaming response."""
        return self._stream_api(prompt, "stage1", timeout=180)

    def stream_stage2(self, prompt: str) -> Iterator[str]:
        """Stream DeepSeek API response for Stage 2 (detailed review).
        Yields content delta strings from the streaming response."""
        return self._stream_api(prompt, "stage2", timeout=300)

    def _stream_api(self, prompt: str, stage: str, timeout: int) -> Iterator[str]:
        last_exc = None
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                response = self.client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[
                        {"role": "system", "content": "你是EMC检测报告审核专家，严格按JSON格式输出审核结果。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    timeout=timeout,
                    stream=True,
                )
                for chunk in response:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
                return  # success — exit retry loop
            except RETRYABLE as e:
                last_exc = e
                if attempt <= MAX_RETRIES:
                    delay = BASE_DELAY_SEC * (2 ** (attempt - 1))
                    logger.warning(
                        "API_STREAM_RETRY stage=%s attempt=%d/%d delay=%.0fs error=%s",
                        stage, attempt, MAX_RETRIES, delay, str(e)[:200],
                    )
                    time.sleep(delay)
            except Exception as e:
                logger.error("API_STREAM_FATAL stage=%s error=%s", stage, str(e))
                raise
        logger.error("API_STREAM_EXHAUSTED stage=%s retries=%d", stage, MAX_RETRIES)
        # type: ignore[misc] — last_exc is guaranteed set here because the loop only
        # exits via the except branch (which assigns last_exc) or this final raise.
        raise last_exc  # type: ignore[misc]


async def asyncio_sleep(seconds: float):
    await asyncio.sleep(seconds)
