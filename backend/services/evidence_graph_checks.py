"""General evidence-graph checks learned from cross-vendor review patterns."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from services.evidence_graph_models import (
    FindingSeverity,
    FindingStatus,
    ReviewFinding,
)


def _finding_id(graph_id: str, dedupe_key: str) -> str:
    digest = hashlib.sha256(f"{graph_id}:{dedupe_key}".encode()).hexdigest()[:20]
    return f"finding-{digest}"


def _finding(*, graph_id: str, check_id: str, status: FindingStatus,
             title: str, description: str, subject_node_ids: list[str],
             evidence_ids: list[str], dedupe_key: str,
             severity: FindingSeverity = FindingSeverity.WARNING,
             metadata: dict[str, Any] | None = None) -> ReviewFinding:
    return ReviewFinding(
        finding_id=_finding_id(graph_id, dedupe_key),
        graph_id=graph_id,
        check_id=check_id,
        status=status,
        severity=severity,
        title=title,
        description=description,
        subject_node_ids=list(dict.fromkeys(subject_node_ids)),
        evidence_ids=list(dict.fromkeys(evidence_ids)),
        dedupe_key=dedupe_key,
        rule_version="general-graph-checks-1",
        metadata=metadata or {},
    )


class NumberedSection(BaseModel):
    node_id: str
    number: str
    title: str
    evidence_ids: list[str] = Field(min_length=1)


class DateRangeObservation(BaseModel):
    node_id: str
    start_date: date
    end_date: date
    evidence_ids: list[str] = Field(min_length=1)


class DatedExecution(BaseModel):
    node_id: str
    test_name: str
    execution_date: date
    evidence_ids: list[str] = Field(min_length=1)


class RequirementResultMatrix(BaseModel):
    node_id: str
    test_name: str
    required_dimensions: list[str]
    result_dimensions: list[str]
    coverage_complete: bool = False
    requirement_evidence_ids: list[str] = Field(min_length=1)
    result_evidence_ids: list[str] = Field(default_factory=list)


class SummaryDetailObservation(BaseModel):
    node_id: str
    test_name: str
    summary_result: str
    detail_result: str = ""
    summary_evidence_ids: list[str] = Field(min_length=1)
    detail_evidence_ids: list[str] = Field(default_factory=list)
    detail_region_verified: bool = False
    detail_region_evidence_ids: list[str] = Field(default_factory=list)


class TypedResultObservation(BaseModel):
    node_id: str
    test_name: str
    expected_unit: str = ""
    actual_unit: str = ""
    expected_value_type: str = ""
    actual_value: Any = ""
    evidence_ids: list[str] = Field(min_length=1)


class InstrumentObservation(BaseModel):
    node_id: str
    equipment_name: str
    equipment_model: str = ""
    equipment_id: str
    evidence_ids: list[str] = Field(min_length=1)


class AnomalyConclusionObservation(BaseModel):
    node_id: str
    test_name: str
    anomaly_text: str
    conclusion: str
    prohibited_by_requirement: bool = False
    evidence_ids: list[str] = Field(min_length=1)


class ComparableResult(BaseModel):
    node_id: str
    test_name: str
    normalized_payload: Any
    mutually_exclusive_group: str = ""
    evidence_ids: list[str] = Field(min_length=1)


class DeclaredCountObservation(BaseModel):
    node_id: str
    scope_name: str
    declared_count: int = Field(ge=0)
    observed_count: int = Field(ge=0)
    coverage_complete: bool = False
    evidence_ids: list[str] = Field(min_length=1)


class IssueTimelineObservation(BaseModel):
    node_id: str
    issue_date: date
    last_execution_date: date
    evidence_ids: list[str] = Field(min_length=2)


class CalibrationObservation(BaseModel):
    node_id: str
    instrument_name: str
    calibration_end: date | None = None
    execution_date: date | None = None
    required: bool = True
    field_region_verified: bool = False
    evidence_ids: list[str] = Field(min_length=1)


class EvidenceProfileObservation(BaseModel):
    node_id: str
    test_name: str
    required_artifacts: list[str]
    observed_artifacts: list[str]
    coverage_complete: bool = False
    requirement_evidence_ids: list[str] = Field(min_length=1)
    artifact_evidence_ids: list[str] = Field(default_factory=list)


class SemanticVariableObservation(BaseModel):
    node_id: str
    context_name: str
    expected_name: str
    actual_name: str
    evidence_ids: list[str] = Field(min_length=1)


class NumericResultRow(BaseModel):
    node_id: str
    test_name: str
    reading: float | None = None
    correction: float | None = None
    reported_result: float | None = None
    limit: float | None = None
    reported_margin: float | None = None
    tolerance: float = Field(default=0.15, ge=0)
    evidence_ids: list[str] = Field(min_length=1)


class ConclusionSetObservation(BaseModel):
    node_id: str
    scope_name: str
    overall_conclusion: str
    individual_conclusions: list[str]
    evidence_ids: list[str] = Field(min_length=2)


def check_duplicate_section_numbers(graph_id: str,
                                    sections: list[NumberedSection]) -> list[ReviewFinding]:
    by_number: dict[str, list[NumberedSection]] = {}
    for section in sections:
        normalized = re.sub(r"\s+", "", section.number).casefold()
        by_number.setdefault(normalized, []).append(section)
    findings: list[ReviewFinding] = []
    for number, group in by_number.items():
        titles = {re.sub(r"\s+", "", item.title).casefold() for item in group}
        if len(group) < 2 or len(titles) < 2:
            continue
        findings.append(_finding(
            graph_id=graph_id,
            check_id="DOC-STRUCTURE-001",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title=f"章节或项目编号 {group[0].number} 对应多个标题",
            description="同一编号在文档中被用于不同章节或测试项目。",
            subject_node_ids=[item.node_id for item in group],
            evidence_ids=[evidence for item in group for evidence in item.evidence_ids],
            dedupe_key=f"DOC-STRUCTURE-001:{number}",
            metadata={"titles": [item.title for item in group]},
        ))
    return findings


def check_execution_dates_within_range(
    graph_id: str,
    declared_range: DateRangeObservation,
    executions: list[DatedExecution],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for execution in executions:
        if declared_range.start_date <= execution.execution_date <= declared_range.end_date:
            continue
        findings.append(_finding(
            graph_id=graph_id,
            check_id="DOC-TIMELINE-001",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title=f"{execution.test_name}日期超出报告声明区间",
            description=(
                f"单项日期 {execution.execution_date.isoformat()} 不在 "
                f"{declared_range.start_date.isoformat()} 至 "
                f"{declared_range.end_date.isoformat()} 内。"
            ),
            subject_node_ids=[declared_range.node_id, execution.node_id],
            evidence_ids=[*declared_range.evidence_ids, *execution.evidence_ids],
            dedupe_key=f"DOC-TIMELINE-001:{execution.node_id}",
        ))
    return findings


def check_requirement_dimension_coverage(
    graph_id: str,
    matrices: list[RequirementResultMatrix],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for matrix in matrices:
        required = {value.strip().casefold() for value in matrix.required_dimensions if value.strip()}
        reported = {value.strip().casefold() for value in matrix.result_dimensions if value.strip()}
        missing = sorted(required - reported)
        if not missing:
            continue
        status = (
            FindingStatus.CONFIRMED_ERROR
            if matrix.coverage_complete else FindingStatus.UNRESOLVED
        )
        evidence_ids = [*matrix.requirement_evidence_ids, *matrix.result_evidence_ids]
        findings.append(_finding(
            graph_id=graph_id,
            check_id="RESULT-DIMENSION-001",
            status=status,
            severity=FindingSeverity.ERROR if status == FindingStatus.CONFIRMED_ERROR else FindingSeverity.WARNING,
            title=f"{matrix.test_name}未逐条件给出结果",
            description=(
                f"缺少结果维度：{', '.join(missing)}。"
                + ("结果区域覆盖已完成。" if matrix.coverage_complete else "结果区域尚未完成覆盖证明。")
            ),
            subject_node_ids=[matrix.node_id],
            evidence_ids=evidence_ids,
            dedupe_key=f"RESULT-DIMENSION-001:{matrix.node_id}",
            metadata={"missing_dimensions": missing},
        ))
    return findings


_PASS_TERMS = {"通过", "符合", "合格", "pass", "ok"}


def _is_pass(value: str) -> bool:
    compact = re.sub(r"\s+", "", value or "").casefold()
    return any(term in compact for term in _PASS_TERMS)


def check_summary_detail_consistency(
    graph_id: str,
    observations: list[SummaryDetailObservation],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for item in observations:
        if not _is_pass(item.summary_result) or item.detail_result.strip():
            continue
        status = (
            FindingStatus.CONFIRMED_ERROR
            if item.detail_region_verified and item.detail_region_evidence_ids
            else FindingStatus.UNRESOLVED
        )
        evidence_ids = [*item.summary_evidence_ids, *item.detail_evidence_ids]
        evidence_ids.extend(item.detail_region_evidence_ids)
        title = (
            f"{item.test_name}汇总通过但明细结果为空"
            if status == FindingStatus.CONFIRMED_ERROR
            else f"{item.test_name}汇总为通过，但系统未确认到明细判定"
        )
        findings.append(_finding(
            graph_id=graph_id,
            check_id="RESULT-EMPTY-001",
            status=status,
            severity=FindingSeverity.ERROR if status == FindingStatus.CONFIRMED_ERROR else FindingSeverity.WARNING,
            title=title,
            description=(
                "汇总结论无法由明细结果支持。"
                if status == FindingStatus.CONFIRMED_ERROR
                else "系统尚未完成明细结果区域的可见内容覆盖，当前不能判断是报告未填写还是提取遗漏。"
            ),
            subject_node_ids=[item.node_id],
            evidence_ids=evidence_ids,
            dedupe_key=f"RESULT-EMPTY-001:{item.node_id}",
        ))
    return findings


_UNIT_DIMENSIONS = {
    "ω": "resistance", "ohm": "resistance", "kohm": "resistance", "kω": "resistance",
    "mω": "resistance", "mohm": "resistance",
    "pf": "capacitance", "nf": "capacitance", "uf": "capacitance", "μf": "capacitance",
    "v": "voltage", "mv": "voltage", "kv": "voltage",
    "a": "current", "ma": "current", "ua": "current", "μa": "current",
    "s": "time", "ms": "time", "us": "time", "μs": "time",
    "hz": "frequency", "khz": "frequency", "mhz": "frequency", "ghz": "frequency",
    "°c": "temperature", "℃": "temperature",
}


def _unit_dimension(unit: str) -> str:
    normalized = re.sub(r"\s+", "", (unit or "").casefold())
    return _UNIT_DIMENSIONS.get(normalized, "")


def check_result_types(graph_id: str,
                       observations: list[TypedResultObservation]) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for item in observations:
        expected_dimension = _unit_dimension(item.expected_unit)
        actual_dimension = _unit_dimension(item.actual_unit)
        if not expected_dimension or not actual_dimension or expected_dimension == actual_dimension:
            continue
        findings.append(_finding(
            graph_id=graph_id,
            check_id="RESULT-TYPE-001",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title=f"{item.test_name}结果量纲与要求不一致",
            description=(
                f"要求单位 {item.expected_unit} 属于 {expected_dimension}，"
                f"实际结果单位 {item.actual_unit} 属于 {actual_dimension}。"
            ),
            subject_node_ids=[item.node_id],
            evidence_ids=item.evidence_ids,
            dedupe_key=f"RESULT-TYPE-001:{item.node_id}",
        ))
    return findings


def check_instrument_identity(graph_id: str,
                              instruments: list[InstrumentObservation]) -> list[ReviewFinding]:
    by_id: dict[str, list[InstrumentObservation]] = {}
    for instrument in instruments:
        normalized_id = re.sub(r"\s+", "", instrument.equipment_id).casefold()
        if normalized_id:
            by_id.setdefault(normalized_id, []).append(instrument)
    findings: list[ReviewFinding] = []
    for equipment_id, group in by_id.items():
        # Display names can legitimately be translated (示波器/Oscilloscope)
        # or use a laboratory abbreviation.  A serial-number identity conflict
        # requires different non-empty hardware models; name-only differences
        # are handled by normalisation and are not a defect.
        models = {
            re.sub(r"\s+", "", item.equipment_model).casefold()
            for item in group if item.equipment_model.strip()
        }
        if len(models) < 2:
            continue
        findings.append(_finding(
            graph_id=graph_id,
            check_id="INSTRUMENT-IDENTITY-001",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title=f"设备编号 {group[0].equipment_id} 对应多个设备",
            description="同一设备编号关联了不同硬件型号。",
            subject_node_ids=[item.node_id for item in group],
            evidence_ids=[evidence for item in group for evidence in item.evidence_ids],
            dedupe_key=f"INSTRUMENT-IDENTITY-001:{equipment_id}",
        ))
    return findings


def check_anomaly_conclusions(
    graph_id: str,
    observations: list[AnomalyConclusionObservation],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for item in observations:
        if not item.anomaly_text.strip() or not _is_pass(item.conclusion):
            continue
        confirmed = item.prohibited_by_requirement
        findings.append(_finding(
            graph_id=graph_id,
            check_id="ANOMALY-CONCLUSION-001",
            status=(FindingStatus.CONFIRMED_ERROR if confirmed else FindingStatus.CONFIRMED_ADVISORY),
            severity=(FindingSeverity.ERROR if confirmed else FindingSeverity.WARNING),
            title=f"{item.test_name}异常现象与通过结论需要核对",
            description=(
                "异常现象违反明确判据，但结论仍为通过。"
                if confirmed else "观察到异常现象，但现有证据不足以认定违反判据，作为工程关注项。"
            ),
            subject_node_ids=[item.node_id],
            evidence_ids=item.evidence_ids,
            dedupe_key=f"ANOMALY-CONCLUSION-001:{item.node_id}",
            metadata={"anomaly_text": item.anomaly_text},
        ))
    return findings


def check_suspicious_duplicate_results(
    graph_id: str,
    results: list[ComparableResult],
) -> list[ReviewFinding]:
    signatures: dict[tuple[str, str], list[ComparableResult]] = {}
    for item in results:
        if not item.mutually_exclusive_group:
            continue
        payload = json.dumps(item.normalized_payload, ensure_ascii=False, sort_keys=True, default=str)
        signatures.setdefault((item.mutually_exclusive_group, payload), []).append(item)
    findings: list[ReviewFinding] = []
    for (group_name, _), group in signatures.items():
        if len(group) < 2:
            continue
        findings.append(_finding(
            graph_id=graph_id,
            check_id="RESULT-DUPLICATE-001",
            status=FindingStatus.CONFIRMED_ADVISORY,
            severity=FindingSeverity.WARNING,
            title="互斥测试的结果数据完全相同",
            description="数据可能合法相同，也可能来自复制粘贴；需要结合测试语义和原始证据复核。",
            subject_node_ids=[item.node_id for item in group],
            evidence_ids=[evidence for item in group for evidence in item.evidence_ids],
            dedupe_key=(
                f"RESULT-DUPLICATE-001:{group_name}:"
                f"{hashlib.sha256(json.dumps(group[0].normalized_payload, sort_keys=True, default=str).encode()).hexdigest()[:12]}"
            ),
            metadata={"test_names": [item.test_name for item in group]},
        ))
    return findings


def check_declared_counts(
    graph_id: str,
    observations: list[DeclaredCountObservation],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for item in observations:
        if item.declared_count == item.observed_count:
            continue
        status = FindingStatus.CONFIRMED_ERROR if item.coverage_complete else FindingStatus.UNRESOLVED
        findings.append(_finding(
            graph_id=graph_id,
            check_id="DOC-STRUCTURE-002",
            status=status,
            severity=FindingSeverity.ERROR if status == FindingStatus.CONFIRMED_ERROR else FindingSeverity.WARNING,
            title=f"{item.scope_name}声明数量与实际条目不一致",
            description=f"声明 {item.declared_count} 项，已识别 {item.observed_count} 项。",
            subject_node_ids=[item.node_id],
            evidence_ids=item.evidence_ids,
            dedupe_key=f"DOC-STRUCTURE-002:{item.node_id}",
            metadata={"declared_count": item.declared_count, "observed_count": item.observed_count},
        ))
    return findings


def check_issue_timeline(
    graph_id: str,
    observations: list[IssueTimelineObservation],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for item in observations:
        if item.issue_date >= item.last_execution_date:
            continue
        findings.append(_finding(
            graph_id=graph_id,
            check_id="DOC-TIMELINE-002",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title="报告签发日期早于最后试验日期",
            description=f"签发日期 {item.issue_date.isoformat()}，最后试验日期 {item.last_execution_date.isoformat()}。",
            subject_node_ids=[item.node_id],
            evidence_ids=item.evidence_ids,
            dedupe_key=f"DOC-TIMELINE-002:{item.node_id}",
        ))
    return findings


def check_calibration_coverage(
    graph_id: str,
    observations: list[CalibrationObservation],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for item in observations:
        if item.calibration_end is None:
            if not item.required:
                continue
            status = (
                FindingStatus.CONFIRMED_ERROR
                if item.field_region_verified else FindingStatus.UNRESOLVED_ADVISORY
            )
            findings.append(_finding(
                graph_id=graph_id,
                check_id="INSTRUMENT-CAL-001",
                status=status,
                severity=FindingSeverity.ERROR if status == FindingStatus.CONFIRMED_ERROR else FindingSeverity.WARNING,
                title=f"{item.instrument_name}校准有效期缺失",
                description="仪器清单未提供可用的校准有效期。",
                subject_node_ids=[item.node_id],
                evidence_ids=item.evidence_ids,
                dedupe_key=f"INSTRUMENT-CAL-001:missing:{item.node_id}",
            ))
            continue
        if item.execution_date is None or item.execution_date <= item.calibration_end:
            continue
        findings.append(_finding(
            graph_id=graph_id,
            check_id="INSTRUMENT-CAL-001",
            status=FindingStatus.CONFIRMED_ERROR,
            severity=FindingSeverity.ERROR,
            title=f"{item.instrument_name}校准有效期未覆盖试验日期",
            description=(
                f"校准有效期至 {item.calibration_end.isoformat()}，"
                f"试验日期为 {item.execution_date.isoformat()}。"
            ),
            subject_node_ids=[item.node_id],
            evidence_ids=item.evidence_ids,
            dedupe_key=f"INSTRUMENT-CAL-001:expired:{item.node_id}",
        ))
    return findings


def check_evidence_profiles(
    graph_id: str,
    observations: list[EvidenceProfileObservation],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for item in observations:
        required = {value.strip().casefold() for value in item.required_artifacts if value.strip()}
        observed = {value.strip().casefold() for value in item.observed_artifacts if value.strip()}
        missing = sorted(required - observed)
        if not missing:
            continue
        status = FindingStatus.CONFIRMED_ERROR if item.coverage_complete else FindingStatus.UNRESOLVED_ADVISORY
        findings.append(_finding(
            graph_id=graph_id,
            check_id="EVIDENCE-PROFILE-001",
            status=status,
            severity=FindingSeverity.ERROR if status == FindingStatus.CONFIRMED_ERROR else FindingSeverity.WARNING,
            title=f"{item.test_name}缺少要求的证据制品",
            description=f"未找到：{', '.join(missing)}。",
            subject_node_ids=[item.node_id],
            evidence_ids=[*item.requirement_evidence_ids, *item.artifact_evidence_ids],
            dedupe_key=f"EVIDENCE-PROFILE-001:{item.node_id}",
            metadata={"missing_artifacts": missing},
        ))
    return findings


def check_semantic_variables(
    graph_id: str,
    observations: list[SemanticVariableObservation],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for item in observations:
        expected = re.sub(r"\s+", "", item.expected_name).casefold()
        actual = re.sub(r"\s+", "", item.actual_name).casefold()
        if not expected or not actual or expected == actual:
            continue
        findings.append(_finding(
            graph_id=graph_id,
            check_id="SEMANTIC-VARIABLE-001",
            status=FindingStatus.CONFIRMED_ADVISORY,
            severity=FindingSeverity.WARNING,
            title=f"{item.context_name}中的名称或变量需要核实",
            description=f"预期为“{item.expected_name}”，实际记录为“{item.actual_name}”。",
            subject_node_ids=[item.node_id],
            evidence_ids=item.evidence_ids,
            dedupe_key=f"SEMANTIC-VARIABLE-001:{item.node_id}",
        ))
    return findings


def check_numeric_result_rows(
    graph_id: str,
    observations: list[NumericResultRow],
) -> list[ReviewFinding]:
    """Recalculate explicit table cells; extraction never decides the math."""
    findings: list[ReviewFinding] = []
    for item in observations:
        if item.reading is not None and item.reported_result is not None:
            expected = item.reading + (item.correction or 0.0)
            if abs(expected - item.reported_result) > item.tolerance:
                findings.append(_finding(
                    graph_id=graph_id, check_id="RESULT-FORMULA-001",
                    status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
                    title=f"{item.test_name}结果计算不一致",
                    description=(
                        f"读值与修正量计算为 {expected:.2f}，"
                        f"表中结果为 {item.reported_result:.2f}。"
                    ),
                    subject_node_ids=[item.node_id], evidence_ids=item.evidence_ids,
                    dedupe_key=f"RESULT-FORMULA-001:result:{item.node_id}",
                ))
        if (item.limit is not None and item.reported_result is not None
                and item.reported_margin is not None):
            expected_margin = item.limit - item.reported_result
            if abs(expected_margin - item.reported_margin) > item.tolerance:
                findings.append(_finding(
                    graph_id=graph_id, check_id="RESULT-FORMULA-001",
                    status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
                    title=f"{item.test_name}余量计算不一致",
                    description=(
                        f"限值减结果为 {expected_margin:.2f}，"
                        f"表中余量为 {item.reported_margin:.2f}。"
                    ),
                    subject_node_ids=[item.node_id], evidence_ids=item.evidence_ids,
                    dedupe_key=f"RESULT-FORMULA-001:margin:{item.node_id}",
                ))
        if item.limit is not None and item.reported_result is not None and item.reported_result > item.limit:
            findings.append(_finding(
                graph_id=graph_id, check_id="RESULT-LIMIT-001",
                status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
                title=f"{item.test_name}结果超过明确限值",
                description=f"结果 {item.reported_result:.2f}，限值 {item.limit:.2f}。",
                subject_node_ids=[item.node_id], evidence_ids=item.evidence_ids,
                dedupe_key=f"RESULT-LIMIT-001:{item.node_id}",
            ))
    return findings


_FAIL_TERMS = {"不通过", "不符合", "不合格", "fail", "failed", "ng"}


def _is_fail(value: str) -> bool:
    compact = re.sub(r"\s+", "", value or "").casefold()
    return any(term in compact for term in _FAIL_TERMS)


def check_overall_conclusions(
    graph_id: str,
    observations: list[ConclusionSetObservation],
) -> list[ReviewFinding]:
    findings: list[ReviewFinding] = []
    for item in observations:
        failed = [value for value in item.individual_conclusions if _is_fail(value)]
        if not _is_pass(item.overall_conclusion) or not failed:
            continue
        findings.append(_finding(
            graph_id=graph_id, check_id="GRAPH-CONCLUSION-001",
            status=FindingStatus.CONFIRMED_ERROR, severity=FindingSeverity.ERROR,
            title=f"{item.scope_name}总体结论与单项结果矛盾",
            description=f"总体结论为“{item.overall_conclusion}”，但存在明确未通过单项。",
            subject_node_ids=[item.node_id], evidence_ids=item.evidence_ids,
            dedupe_key=f"GRAPH-CONCLUSION-001:{item.node_id}",
            metadata={"failed_conclusions": failed},
        ))
    return findings
