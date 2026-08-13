"""User-facing finding presentation contracts.

The review rules produce facts; this module turns those facts into a short,
stable explanation for reviewers.  It deliberately does not infer new facts
or decide a finding.  LLM wording, when available, is stored separately from
this deterministic presentation and must reference existing evidence IDs.
"""

from __future__ import annotations

import re
from typing import Any

from services.evidence_graph_models import ReviewFinding


PRESENTATION_VERSION = "finding-copy-1"

_STATUS_LABELS = {
    "confirmed_error": ("error", "需要处理"),
    "confirmed_pass": ("complete", "证据完整"),
    "unresolved": ("confirmation", "需要人工确认"),
    "confirmed_advisory": ("advisory", "需要关注"),
    "unresolved_advisory": ("confirmation", "需要人工确认"),
    "not_applicable": ("not_applicable", "本次不适用"),
}

_CHECK_CONTEXT: dict[str, tuple[str, str]] = {
    "GRAPH-COVERAGE-001": (
        "测试计划、原始记录和检测报告中的测试项目覆盖关系",
        "无法证明项目是否已按计划执行并在检测报告中发布结果。",
    ),
    "GRAPH-COVERAGE-002": (
        "原始记录、检测报告与测试计划的反向覆盖关系",
        "可能存在计划未要求但已执行或已发布的测试项目。",
    ),
    "GRAPH-SAMPLE-001": (
        "测试计划、原始记录和检测报告中的样品数量口径",
        "无法确认计划要求的样品数量是否被完整执行并发布。",
    ),
    "DOC-CROSS-FIELD-001": (
        "委托单、测试计划、原始记录和检测报告中的同一字段",
        "不同写法可能属于正常简称，也可能指向不同对象，需要人工确认。",
    ),
    "DOC-RESULT-CONSISTENCY-001": (
        "原始记录与检测报告中的同一次试验结果",
        "无法确认检测报告是否准确发布了原始记录的实际结果。",
    ),
    "REPORT-RESULT-CONFLICT-001": (
        "检测报告内部同一样品、模式和测试条件的重复结果",
        "报告无法给出唯一、可追溯的测试结论。",
    ),
    "REPORT-SPEC-RESULT-001": (
        "检测报告中的规范要求等级与实际结果等级",
        "报告的实际判定可能不满足引用的规范要求。",
    ),
    "DOC-INSTRUMENT-CONSISTENCY-001": (
        "原始记录与检测报告中的物理仪器清单",
        "两份仪器清单必须完全一致；任一侧独有的仪器都需要核对资料或补正记录。",
    ),
    "INSTRUMENT-CAL-001": (
        "仪器校准有效期与对应试验日期的关系",
        "无法确认试验使用的仪器在试验当日处于有效校准期内。",
    ),
    "INSTRUMENT-IDENTITY-001": (
        "原始记录中的仪器名称、编号和校准证据",
        "无法确认执行试验的仪器身份及其校准依据。",
    ),
    "DOC-TIMELINE-001": (
        "报告声明的试验日期区间与原始记录日期",
        "无法确认原始记录中的试验是否发生在报告声明的时间范围内。",
    ),
    "DOC-TIMELINE-002": (
        "检测报告签发日期与最后试验日期",
        "无法确认报告是否在全部试验完成后签发。",
    ),
    "DOC-TIMELINE-003": (
        "样品接收日期与试验开始日期",
        "无法确认样品是否在试验开始前完成接收。",
    ),
    "DOC-PAGINATION-001": (
        "检测报告目录页码与实际报告页数",
        "读者可能无法按目录准确定位报告内容。",
    ),
    "DOC-REFERENCE-001": (
        "检测报告中的外部文件、附件和引用关系",
        "报告引用的依据可能无法被完整追溯。",
    ),
    "DOC-STRUCTURE-001": (
        "检测报告必需章节和文档结构",
        "报告结构不完整，可能影响审查和后续使用。",
    ),
    "DOC-STRUCTURE-002": (
        "检测报告页眉、页脚和基本版式结构",
        "报告的页面结构或身份信息可能不完整。",
    ),
    "DOC-PLAN-REF-001": (
        "检测报告引用的测试计划编号与实际测试计划",
        "无法确认报告使用的是本次审核对应的测试计划。",
    ),
    "DOC-REQUIRED-FIELD-001": (
        "检测报告必填字段的原文内容",
        "报告身份或关键业务事实不完整，不能作为完整正式报告使用。",
    ),
    "DOC-SIGNATURE-001": (
        "检测报告编制、审核和批准签署区域",
        "无法确认报告已完成规定的审核和批准流程。",
    ),
    "RESULT-DIMENSION-001": (
        "测试要求中的条件维度与报告结果记录",
        "无法确认每个计划要求的测试条件都已经给出结果。",
    ),
    "RESULT-EMPTY-001": (
        "检测报告结果表中的实际结果字段",
        "无法据此确认该测试条件的实际结果。",
    ),
    "RESULT-TYPE-001": (
        "测试要求与报告结果的数值和单位",
        "结果的单位或数据类型不一致，不能直接比较。",
    ),
    "RESULT-DUPLICATE-001": (
        "互斥测试项目的结果数据",
        "可能存在复制粘贴或记录错误，需要结合原始记录复核。",
    ),
    "RESULT-FORMULA-001": (
        "报告结果、输入读值与计算公式",
        "报告中的计算结果无法由其列出的输入值复现。",
    ),
    "RESULT-LIMIT-001": (
        "报告实际结果与明确限值",
        "实际结果超过明确限值，当前判定可能不成立。",
    ),
    "ANOMALY-CONCLUSION-001": (
        "报告异常现象记录与总体结论",
        "异常现象与通过结论之间存在需要解释的关系。",
    ),
    "EVIDENCE-PROFILE-001": (
        "测试项目要求的证据制品与实际资料",
        "当前资料不足以完整支撑该测试项目的审核结论。",
    ),
    "SEMANTIC-VARIABLE-001": (
        "同一上下文中的名称或变量写法",
        "无法确认不同写法是否代表同一业务对象或参数。",
    ),
    "GRAPH-CONCLUSION-001": (
        "总体结论与单项测试结果",
        "总体结论不能覆盖或解释其中的未通过单项。",
    ),
    "PLAN-ACCEPTANCE-CONFLICT-001": (
        "测试计划中的判定等级与适用条件",
        "无法确认不同判定等级分别适用于哪些模式或阶段。",
    ),
    "PLAN-CLAUSE-CONSISTENCY-001": (
        "测试计划条款与执行说明",
        "无法确认执行方法是否满足计划定义的要求。",
    ),
    "STANDARD-PARAMETER-CONSISTENCY-001": (
        "已发布标准要求与资料中的测试参数",
        "当前参数可能不满足已发布标准要求。",
    ),
    "STANDARD-ACCEPTANCE-CONSISTENCY-001": (
        "已发布标准判定等级与资料中的判定等级",
        "当前判定等级可能不满足已发布标准要求。",
    ),
    "STANDARD-PLAN-COVERAGE-001": (
        "资料引用的标准与本次已发布标准范围",
        "标准要求尚未进入本次审核的可用范围。",
    ),
    "STANDARD-SCOPE-ADVISORY-001": (
        "已发布标准要求与测试计划范围",
        "这只是范围提醒，不代表项目一定未执行。",
    ),
}

_FIELD_LABELS = {
    "client_name": "客户名称",
    "client_address": "客户地址",
    "sample_name": "样品名称",
    "sample_model": "样品型号",
    "report_no": "报告编号",
    "test_plan_no": "测试计划编号",
}


def _first_sentence(value: str, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    match = re.search(r"[。！？.!?]", text[:limit])
    if match:
        return text[: match.end()]
    return text[: limit - 1].rstrip() + "…"


def _subject(finding: ReviewFinding) -> str:
    metadata = finding.metadata or {}
    for key in (
        "test_item", "item_name", "report_name", "context_name", "scope_name",
        "display_name", "field_name", "parameter_name", "instrument_name",
    ):
        value = str(metadata.get(key) or "").strip()
        if value:
            return _FIELD_LABELS.get(value, value) if key == "field_name" else value
    presentation = metadata.get("presentation")
    if isinstance(presentation, dict):
        value = str(presentation.get("subject") or "").strip()
        if value:
            return value
    title = re.sub(
        r"(执行与发布证据完整|证据尚未闭合|缺少必要执行或发布证据|"
        r"未找到执行记录和报告结果|未找到原始记录执行证据|未找到检测报告发布证据)$",
        "",
        finding.title,
    )
    return title.strip() or "当前审核对象"


def _checked(check_id: str) -> str:
    return _CHECK_CONTEXT.get(
        check_id,
        ("本次审核规则定义的原文证据和跨资料关系", "当前证据不足以自动形成可靠结论。"),
    )[0]


def _impact(check_id: str, status_key: str) -> str:
    if status_key == "complete":
        return "证据链已闭合，无需处理。"
    if status_key == "confirmation":
        return "证据尚不足以自动裁定，不能直接判定为错误或通过。"
    if status_key == "system_incomplete":
        return "审核尚未完成，不能把未定位或未提取当作资料缺失。"
    return _CHECK_CONTEXT.get(check_id, ("", "当前证据链尚未闭合。"))[1]


def build_finding_presentation(finding: ReviewFinding) -> dict[str, Any]:
    """Create a compact, deterministic presentation payload for a finding."""
    status_key, status_label = _STATUS_LABELS.get(
        str(finding.status), ("confirmation", "需要人工确认"),
    )
    metadata = finding.metadata or {}
    if str(metadata.get("system_state") or "").lower() in {
        "system_incomplete", "failed", "not_completed",
    }:
        status_key, status_label = "system_incomplete", "系统未完成"
    description = _first_sentence(finding.description or finding.title)
    if status_key == "complete":
        issue = "测试计划、原始记录和检测报告均找到可追溯的对应证据。"
    elif finding.check_id == "GRAPH-COVERAGE-001":
        issue = description
    else:
        issue = description or finding.title
    return {
        "version": PRESENTATION_VERSION,
        "status_key": status_key,
        "status_label": status_label,
        "subject": _subject(finding),
        "checked": _checked(finding.check_id),
        "issue": issue,
        "impact": _impact(finding.check_id, status_key),
        "llm_supplement": {
            "status": "not_generated",
            "judgment": "",
            "basis": "",
            "uncertainty": "",
            "handling": "",
            "evidence_ids": [],
        },
    }


def attach_finding_presentation(finding: ReviewFinding) -> ReviewFinding:
    """Persist presentation at creation time so historical snapshots stay stable."""
    metadata = dict(finding.metadata or {})
    if not isinstance(metadata.get("presentation"), dict):
        metadata["presentation"] = build_finding_presentation(finding)
    return finding.model_copy(update={"metadata": metadata})
