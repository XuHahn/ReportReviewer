"""Central DeepSeek task policies.

The policy selects model and reasoning mode only.  Each business service still
owns its prompt, token budget, timeout, validation, and fallback behavior.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import DEEPSEEK_FLASH_MODEL, DEEPSEEK_PRO_MODEL


@dataclass(frozen=True)
class LLMPolicy:
    model: str
    thinking: bool
    reasoning_effort: str | None = None


LLM_POLICIES: dict[str, LLMPolicy] = {
    # Bounded, evidence-grounded transcription where extra reasoning mostly
    # increases latency and risks paraphrasing source values.
    "fast_transcription": LLMPolicy(DEEPSEEK_FLASH_MODEL, False),
    # Long, irregular vendor documents need Pro's extraction quality, but the
    # task remains transcription into a supplied JSON schema.
    "structured_extraction": LLMPolicy(DEEPSEEK_PRO_MODEL, False),
    # Metadata cataloging and candidate routing are short classification jobs.
    "classification": LLMPolicy(DEEPSEEK_FLASH_MODEL, False),
    # Bounded one-to-one proposal reconciliation is a classification task.
    # Thinking mode creates severe long-tail latency without adding evidence.
    "bounded_reconciliation": LLMPolicy(DEEPSEEK_FLASH_MODEL, False),
    # Cross-document conflicts and identity equivalence require semantic
    # reasoning and are always followed by deterministic evidence checks.
    "semantic_audit": LLMPolicy(DEEPSEEK_PRO_MODEL, True, "high"),
    # Normative standard interpretation is high-stakes and human-confirmed.
    "knowledge_reasoning": LLMPolicy(DEEPSEEK_PRO_MODEL, True, "high"),
}


def get_llm_policy(task_kind: str) -> LLMPolicy:
    try:
        return LLM_POLICIES[task_kind]
    except KeyError as exc:
        raise ValueError(f"未知 LLM 任务类型: {task_kind}") from exc
