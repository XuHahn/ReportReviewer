"""Build auditable rule-execution states without treating missing input as pass."""

from __future__ import annotations

from typing import Any, Iterable, Literal


CheckExecutionState = Literal[
    "passed",
    "issues_found",
    "not_applicable",
    "source_missing",
    "system_incomplete",
]


def build_check_execution(
    check_id: str,
    input_count: int,
    finding_count: int,
    *,
    applicability: Literal["applicable", "not_applicable", "unknown"] = "applicable",
    expected_input_count: int | None = None,
    anchored_input_count: int | None = None,
    reason_code: str = "",
    document_types: Iterable[str] = (),
    forced_state: CheckExecutionState | None = None,
) -> dict[str, Any]:
    """Return one persisted rule audit item.

    Zero input is deliberately unsafe by default.  A rule is only marked not
    applicable when an upstream applicability gate says so explicitly.
    """
    expected = input_count if expected_input_count is None else max(0, expected_input_count)
    anchored = input_count if anchored_input_count is None else max(0, anchored_input_count)
    if forced_state is not None:
        state: CheckExecutionState = forced_state
    elif applicability == "not_applicable":
        state = "not_applicable"
        reason_code = reason_code or "scope_proven_not_applicable"
    elif expected > anchored:
        state = "system_incomplete"
        reason_code = reason_code or "required_evidence_not_fully_anchored"
    elif input_count == 0:
        state = "system_incomplete"
        reason_code = reason_code or "rule_input_contract_unfulfilled"
    elif finding_count:
        state = "issues_found"
        reason_code = reason_code or "rule_findings_created"
    else:
        state = "passed"
        reason_code = reason_code or "all_anchored_inputs_checked"
    return {
        "check_id": check_id,
        "input_count": input_count,
        "finding_count": finding_count,
        "state": state,
        "reason_code": reason_code,
        "applicability": applicability,
        "expected_input_count": expected,
        "anchored_input_count": anchored,
        "document_types": sorted(set(document_types)),
    }


def has_blocking_check_state(items: Iterable[dict[str, Any]]) -> bool:
    return any(
        item.get("state") in {"source_missing", "system_incomplete"}
        for item in items
    )
