"""Cross-document validation engine.

Takes extraction results from all 4 documents and produces a unified
ValidationReport with discrepancies flagged by severity.

Supports partial validation — only checks documents that are provided.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from utils.logger import get_logger

logger = get_logger(__name__)

# Lazy import to avoid circular dependency at module level
_MAX_TEST_DATE_SPAN_DAYS = None


def _get_max_test_date_span() -> int:
    global _MAX_TEST_DATE_SPAN_DAYS
    if _MAX_TEST_DATE_SPAN_DAYS is None:
        try:
            from config import MAX_TEST_DATE_SPAN_DAYS as val
            _MAX_TEST_DATE_SPAN_DAYS = val
        except ImportError:
            _MAX_TEST_DATE_SPAN_DAYS = int(os.getenv("MAX_TEST_DATE_SPAN_DAYS", "365"))
    return _MAX_TEST_DATE_SPAN_DAYS


@dataclass
class ValidationIssue:
    id: str = ""
    severity: str = "CRITICAL"   # CRITICAL | WARNING | INFO
    category: str = ""           # coverage | basic_info | instrument | date | method
    field_name: str = ""
    description: str = ""
    sources: dict[str, str] = field(default_factory=dict)
    source: str = "deterministic"  # deterministic | llm


@dataclass
class ValidationReport:
    total_checks: int = 0
    passed: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    info: list[ValidationIssue] = field(default_factory=list)

    @property
    def critical_count(self) -> int: return len(self.issues)

    @property
    def warning_count(self) -> int: return len(self.warnings)

    @property
    def is_clean(self) -> bool: return len(self.issues) == 0


def _norm(c: str) -> str:
    return c.replace("/", "").replace("-", "").replace(" ", "").upper()


def _parse_date(s: str) -> date | None:
    if not s: return None
    s = s.strip()
    for fmt in ["%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"]:
        try: return datetime.strptime(s, fmt).date()
        except ValueError: pass
    m = re.match(r"(\d{4})/(\d{1,2})/(\d{1,2})", s)
    if m: return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _dedup_report_instruments(instruments: list) -> list:
    """Deduplicate report instruments by (manufacturer, model, serial_no).

    Used as a fallback when deduplicated_instruments is not pre-computed
    on the ReportData object (e.g. data extracted before this feature was added).
    """
    key_map: dict[tuple[str, str, str], any] = {}
    for inst in instruments:
        key = (
            str(getattr(inst, 'manufacturer', '')).strip().upper(),
            str(getattr(inst, 'model', '')).strip().upper(),
            str(getattr(inst, 'serial_no', '')).strip().upper(),
        )
        if key in key_map:
            existing = key_map[key]
            if (getattr(inst, 'calibration_end', '') or '') > (getattr(existing, 'calibration_end', '') or ''):
                key_map[key] = inst
        else:
            key_map[key] = inst
    return list(key_map.values())


def _fuzzy_match_name(name: str, name_to_code: dict[str, str]) -> str | None:
    """Try to match a test item name against a dictionary of normalized names→codes.

    Returns the matched code if found, or None.  Removes spaces and common
    punctuation for comparison so that "脉冲 1&2 的抗扰性测试" matches
    "抗脉冲1和2A的性能" via shared key tokens.
    """
    if not name or not name_to_code:
        return None

    def _tokenize(s: str) -> set[str]:
        """Break a test item name into meaningful tokens for comparison."""
        # Remove spaces, common connectors, and known pattern words
        cleaned = re.sub(r'[\s、，,\.。:：\(\)（）]', '', s)
        # Extract 2-4 char tokens that carry meaning
        tokens: set[str] = set()
        for tok_len in (4, 3, 2):
            for i in range(len(cleaned) - tok_len + 1):
                token = cleaned[i:i + tok_len]
                # Skip pure-numeric or single-letter tokens
                if not token.isdigit() and not token.isalpha() and len(token) >= 2:
                    tokens.add(token)
        return tokens

    name_tokens = _tokenize(name)
    if not name_tokens:
        return None

    best_match: tuple[str, int] | None = None
    for plan_name, plan_code in name_to_code.items():
        plan_tokens = _tokenize(plan_name)
        overlap = len(name_tokens & plan_tokens)
        if overlap >= 2:  # require at least 2 matching tokens
            if best_match is None or overlap > best_match[1]:
                best_match = (plan_code, overlap)

    return best_match[0] if best_match else None


def _has_toc(final_report) -> bool:
    """Check if final_report has TOC data available."""
    if final_report is None:
        return False
    return bool(getattr(final_report, 'toc', None))


class CrossValidator:

    # Minimum data-row count needed for meaningful data comparison
    _MIN_DATA_ROWS_FOR_COMPARISON = 5

    # Progress step labels — one per check in validate()
    _CHECK_LABELS: list[str] = [
        "测试项覆盖 (计划 vs 记录)",            # 0: _coverage
        "TOC 三方覆盖 (计划+目录+记录)",         # 1: _toc_coverage
        "原始记录结论扫描",                     # 2: _conclusions
        "报告结论 vs 记录结论",                  # 3: _result_consistency
        "仪器校准有效期",                       # 4: _calibration
        "仪器清单交叉比对 (报告 vs 记录)",       # 5: _instrument_consistency
        "基本信息比对 (标准编号)",               # 6: _basic_info
        "测试日期跨度",                         # 7: _dates
        "额定电压一致性",                       # 8: _voltage
        "测试模式一致性",                       # 9: _test_mode
        "数据行数对比 (报告 vs 记录)",           # 10: _data_comparison
        "方法合规检查",                         # 11: _method_compliance
        "TOC 完整性 (目录 vs 结果)",            # 12: _toc_completeness
        "多数投票 (4文档字段比对)",              # 13: _majority_vote
        "格式规则校验 (regex)",                  # 14: _format_validation
        "物理范围校验",                         # 15: _range_validation
        "余量公式验证 + 超标检测",               # 16: _margin_validation
        "总体结论 vs 单项结果",                  # 17: _overall_conclusion_check
        "签发日期逻辑",                         # 18: _issue_date_check
        "封面信息完整性",                       # 19: _cover_completeness
        "LLM 深度语义审核",                     # 20: _llm_deep_audit
        "供应商名称一致性",                     # 21: _supplier_name_check
        "样品数量一致性",                       # 22: _sample_count_check
        "测试计划编号一致性",                   # 23: _plan_number_check
    ]

    @staticmethod
    async def validate(order_form=None, test_plan=None,
                       raw_records=None, final_report=None,
                       enable_llm_audit: bool = True,
                       progress_callback=None) -> ValidationReport:
        all_issues: list[ValidationIssue] = []
        checks = passed = 0
        step_idx = 0

        _LABELS = CrossValidator._CHECK_LABELS

        async def _report(label: str, issues_found: int = 0):
            """Send progress update if callback is set."""
            nonlocal step_idx
            if progress_callback:
                try:
                    await progress_callback({
                        "step": step_idx + 1, "total": len(_LABELS),
                        "label": label, "status": "done",
                        "issues_found": issues_found,
                    })
                except Exception:
                    pass
            step_idx += 1

        # P0: Coverage (test_plan vs raw_records, with optional report TOC)
        if test_plan and raw_records:
            r = CrossValidator._coverage(test_plan, raw_records,
                                         final_report if _has_toc(final_report) else None)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[0], len(r) if test_plan and raw_records else 0)

        # P0: TOC 3-way coverage (when TOC is available)
        if test_plan and raw_records and _has_toc(final_report):
            r = CrossValidator._toc_coverage(test_plan, raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[1], len(r) if test_plan and raw_records and _has_toc(final_report) else 0)

        # P0: Conclusions
        if raw_records:
            r = CrossValidator._conclusions(raw_records)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[2], len(r) if raw_records else 0)

        # P0: Report result consistency vs raw records conclusions
        if raw_records and final_report and final_report.results:
            r = CrossValidator._result_consistency(raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[3], len(r) if raw_records and final_report and final_report.results else 0)

        # P0: Calibration
        if raw_records:
            r = CrossValidator._calibration(raw_records)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[4], len(r) if raw_records else 0)

        # P0: Instrument consistency — report vs raw records
        if raw_records and final_report:
            r = CrossValidator._instrument_consistency(raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[5], len(r) if raw_records and final_report else 0)

        # P0: Basic info (standard, part_number)
        if order_form:
            r = CrossValidator._basic_info(order_form, test_plan, raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[6], len(r) if order_form else 0)

        # NEW: Supplier name consistency (order_form vs test_plan)
        if order_form and test_plan:
            r = CrossValidator._supplier_name_check(order_form, test_plan)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[21], len(r) if order_form and test_plan else 0)

        # NEW: Sample count consistency (test_plan vs final_report)
        if test_plan and final_report:
            r = CrossValidator._sample_count_check(test_plan, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[22], len(r) if test_plan and final_report else 0)

        # NEW: Test plan number consistency (test_plan vs final_report)
        if test_plan and final_report:
            r = CrossValidator._plan_number_check(test_plan, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[23], len(r) if test_plan and final_report else 0)
        if raw_records:
            r = CrossValidator._dates(raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[7], len(r) if raw_records else 0)

        # P1: Voltage
        if order_form and raw_records:
            r = CrossValidator._voltage(order_form, raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[8], len(r) if order_form and raw_records else 0)

        # P1: Test mode
        if test_plan and raw_records:
            r = CrossValidator._test_mode(test_plan, raw_records)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[9], len(r) if test_plan and raw_records else 0)

        # P1: Data-row comparison (report vs raw records)
        if raw_records and final_report and final_report.data_rows:
            r = CrossValidator._data_comparison(raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[10], len(r) if raw_records and final_report and final_report.data_rows else 0)

        # P1: Method compliance (test_details vs raw_records)
        if test_plan and raw_records:
            r = CrossValidator._method_compliance(test_plan, raw_records)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[11], len(r) if test_plan and raw_records else 0)

        # P1: TOC completeness (report internal consistency)
        if _has_toc(final_report):
            r = CrossValidator._toc_completeness(final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[12], len(r) if _has_toc(final_report) else 0)

        # P0: Majority voting — 4-document field comparison (needs ≥2 docs)
        docs_provided = sum(1 for d in [order_form, test_plan, raw_records, final_report] if d is not None)
        if docs_provided >= 2:
            r = CrossValidator._majority_vote(order_form, test_plan, raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[13], len(r) if docs_provided >= 2 else 0)

        # P0: Format validation — field-level format rules (needs ≥1 doc)
        if docs_provided >= 1:
            r = CrossValidator._format_validation(order_form, test_plan, raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[14], len(r) if docs_provided >= 1 else 0)

        # P1: Value range validation — physical range checks (needs ≥1 doc)
        if docs_provided >= 1:
            r = CrossValidator._range_validation(order_form, test_plan, raw_records, final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
        await _report(_LABELS[15], len(r) if docs_provided >= 1 else 0)

        # ── NEW: P0 deterministic data checks (from audit script) ──────
        if final_report:
            # Margin formula validation
            if final_report.data_rows:
                r = CrossValidator._margin_validation(final_report)
                checks += 1
                if not r: passed += 1
                else: all_issues.extend(r)
            await _report(_LABELS[16], len(r) if final_report and final_report.data_rows else 0)

            # Overall conclusion vs individual results
            r = CrossValidator._overall_conclusion_check(final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
            await _report(_LABELS[17], len(r) if final_report else 0)

            # Issue date logic
            r = CrossValidator._issue_date_check(final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
            await _report(_LABELS[18], len(r) if final_report else 0)

            # Cover completeness
            r = CrossValidator._cover_completeness(final_report)
            checks += 1
            if not r: passed += 1
            else: all_issues.extend(r)
            await _report(_LABELS[19], len(r) if final_report else 0)

        # ── NEW: P0 LLM deep semantic audit ────────────────────────────
        if enable_llm_audit and final_report and raw_records:
            try:
                r = await CrossValidator._llm_deep_audit(
                    order_form, test_plan, raw_records, final_report)
                checks += 1
                if not r: passed += 1
                else: all_issues.extend(r)
            except Exception as e:
                logger.warning("llm_audit_failed", error=str(e)[:200])
        await _report(_LABELS[20], len(r) if enable_llm_audit and final_report and raw_records and 'r' in dir() else 0)

        crit = [i for i in all_issues if i.severity == "CRITICAL"]
        warn = [i for i in all_issues if i.severity == "WARNING"]
        info = [i for i in all_issues if i.severity == "INFO"]

        report = ValidationReport(total_checks=checks, passed=passed,
                                  issues=crit, warnings=warn, info=info)
        logger.info("validation_summary",
                     total_checks=checks,
                     passed=passed,
                     critical_count=report.critical_count,
                     warning_count=report.warning_count)
        return report

    # ── P0 checks ──────────────────────────────────────────────────

    @staticmethod
    def _coverage(test_plan, raw_records, final_report=None) -> list[ValidationIssue]:
        logger.info("check_start", check="coverage")
        issues = []
        _EXECUTED = {"y", "yes", "必做", "是", "●", "✓", "y "}
        # If ALL test plan items have empty is_executed, treat the plan as
        # authoritative — all items listed are required (common for template plans).
        plan_items = list(test_plan.test_items)
        all_unspecified = all(
            not p.is_executed or not p.is_executed.strip()
            for p in plan_items
        )
        plan_y = {_norm(p.code) for p in plan_items
                   if (all_unspecified or p.is_executed.strip().lower() in _EXECUTED)}
        plan_all_codes = {_norm(p.code) for p in plan_items}

        # Build name→code lookup for fuzzy matching
        plan_name_to_code: dict[str, str] = {}
        for p in plan_items:
            plan_name_to_code[_norm(p.name)] = _norm(p.code)

        rec_codes = {_norm(m.test_item_code) for m in raw_records.metas}

        # Build record code→name lookup
        rec_code_to_name: dict[str, str] = {}
        for m in raw_records.metas:
            cn = _norm(m.test_item_code)
            if cn not in rec_code_to_name:
                rec_code_to_name[cn] = m.test_item_name

        # Extract report TOC codes for 3-way comparison (when available)
        toc_codes: set[str] = set()
        if final_report is not None and _has_toc(final_report):
            toc_codes = {_norm(t.code) for t in final_report.toc}

        # ── Plan requires but record missing ──
        for code in sorted(plan_y - rec_codes):
            pi = next((p for p in plan_items if _norm(p.code) == code), None)
            description = f"计划要求执行 {code}（{pi.name if pi else ''}），原始记录未找到"
            if toc_codes and code not in toc_codes:
                description += "；报告目录中也未出现"
            elif toc_codes and code in toc_codes:
                description += "；但报告目录中列出了该项（可能原始记录丢失）"
            issues.append(ValidationIssue(
                severity="CRITICAL", category="coverage", field_name=code,
                description=description,
                sources={"计划": "Y/必做", "记录": "缺失"}))

        # ── Record has but plan doesn't list ──
        for code in sorted(rec_codes - plan_all_codes):
            rm = next((m for m in raw_records.metas if _norm(m.test_item_code) == code), None)
            rec_name = rm.test_item_name if rm else ""

            # Try name-based fuzzy matching: normalize and check if record name
            # appears in any plan item name (or vice versa)
            matched_plan_code = _fuzzy_match_name(rec_name, plan_name_to_code)
            if matched_plan_code:
                issues.append(ValidationIssue(
                    severity="INFO", category="coverage", field_name=code,
                    description=f"原始记录有 {code}（{rec_name}），计划中对应项为 {matched_plan_code}（代码不匹配但名称相近）",
                    sources={"计划": matched_plan_code, "记录": code}))
            else:
                issues.append(ValidationIssue(
                    severity="WARNING", category="coverage", field_name=code,
                    description=f"原始记录有 {code}（{rec_name}），计划未要求",
                    sources={"计划": "未列出", "记录": "已执行"}))
        logger.info("check_done", check="coverage", issues_found=len(issues))
        return issues

    @staticmethod
    def _conclusions(raw_records) -> list[ValidationIssue]:
        logger.info("check_start", check="conclusions")
        issues = []
        for s in raw_records.conclusion_summary:
            for r in s.rounds:
                if r.get("conclusion") == "不符合":
                    issues.append(ValidationIssue(
                        severity="CRITICAL", category="conclusion",
                        field_name=s.test_item_code,
                        description=f"{s.test_item_code} 轮次{r['sequence']} 结论为'不符合'"))
        logger.info("check_done", check="conclusions", issues_found=len(issues))
        return issues

    @staticmethod
    def _calibration(raw_records) -> list[ValidationIssue]:
        logger.info("check_start", check="calibration")
        issues = [ValidationIssue(
            severity="CRITICAL", category="calibration",
            field_name=ci.instrument_name,
            description=ci.error_description,
            sources={"test_date": ci.test_date, "cal_end": ci.calibration_end,
                      "days_expired": str(ci.days_expired)})
            for ci in raw_records.calibration_issues]
        logger.info("check_done", check="calibration", issues_found=len(issues))
        return issues

    @staticmethod
    def _instrument_consistency(raw_records, final_report) -> list[ValidationIssue]:
        """Compare unique instruments between report and raw records.

        Deduplicates both sides by (manufacturer, model, serial_no) and flags:
        - Instruments in report but not in raw records (possibly listed but unused)
        - Instruments in raw records but not in report (report may be incomplete)
        """
        logger.info("check_start", check="instrument_consistency")
        issues: list[ValidationIssue] = []

        def _make_key(inst) -> tuple[str, str, str]:
            """Build a dedup key from instrument fields, case-insensitive."""
            return (
                str(getattr(inst, 'manufacturer', '')).strip().upper(),
                str(getattr(inst, 'model', '')).strip().upper(),
                str(getattr(inst, 'serial_no', '')).strip().upper(),
            )

        def _inst_label(inst) -> str:
            mfr = getattr(inst, 'manufacturer', '') or ''
            model = getattr(inst, 'model', '') or ''
            sn = getattr(inst, 'serial_no', '') or ''
            return f"{mfr} {model} SN:{sn}".strip()

        # ── Raw records: deduplicated instruments (already dedup'd, but normalize keys) ──
        rec_insts = getattr(raw_records, 'deduplicated_instruments', None) or []
        if not rec_insts:
            # Fallback: dedup from raw instruments
            from services.raw_records_instrument_extractor import deduplicate_instruments
            raw_all = getattr(raw_records, 'instruments', [])
            rec_insts = deduplicate_instruments(raw_all) if raw_all else []

        rec_keys: dict[tuple[str, str, str], any] = {}
        for inst in rec_insts:
            rec_keys[_make_key(inst)] = inst

        # ── Report: use deduplicated_instruments if available, else dedup on the fly ──
        rpt_insts = getattr(final_report, 'deduplicated_instruments', None) or []
        if not rpt_insts:
            rpt_all = getattr(final_report, 'instruments', [])
            rpt_insts = _dedup_report_instruments(rpt_all)

        rpt_keys: dict[tuple[str, str, str], any] = {}
        for inst in rpt_insts:
            rpt_keys[_make_key(inst)] = inst

        # ── Compare ──
        # Report-only: listed in report but not found in raw records
        report_only = []
        for key, inst in rpt_keys.items():
            if key not in rec_keys:
                report_only.append(_inst_label(inst))

        # Records-only: used in testing but not listed in report
        records_only = []
        for key, inst in rec_keys.items():
            if key not in rpt_keys:
                records_only.append(_inst_label(inst))

        if report_only:
            issues.append(ValidationIssue(
                severity="WARNING", category="instrument",
                field_name="仪器清单",
                description=f"检测报告列出了 {len(report_only)} 台未在原始记录中使用的仪器: {', '.join(report_only[:5])}{' ...' if len(report_only) > 5 else ''}",
                sources={"报告唯一仪器": str(len(rpt_keys)),
                         "记录唯一仪器": str(len(rec_keys)),
                         "报告多出": str(len(report_only))}))

        if records_only:
            issues.append(ValidationIssue(
                severity="WARNING", category="instrument",
                field_name="仪器清单",
                description=f"原始记录使用了 {len(records_only)} 台未在检测报告仪器表中列出的仪器: {', '.join(records_only[:5])}{' ...' if len(records_only) > 5 else ''}",
                sources={"报告唯一仪器": str(len(rpt_keys)),
                         "记录唯一仪器": str(len(rec_keys)),
                         "记录多出": str(len(records_only))}))

        if not report_only and not records_only and rpt_keys and rec_keys:
            logger.info("check_done", check="instrument_consistency",
                       issues_found=0, matched=len(rpt_keys & rec_keys))
        else:
            logger.info("check_done", check="instrument_consistency",
                       issues_found=len(issues),
                       report_only=len(report_only), records_only=len(records_only),
                       matched=len(set(rpt_keys) & set(rec_keys)))

        return issues

    @staticmethod
    def _basic_info(order_form, test_plan, raw_records, final_report) -> list[ValidationIssue]:
        logger.info("check_start", check="basic_info")
        issues = []
        # Standard reference fuzzy comparison
        std_sources = {"委托单": order_form.test_requirements.test_specification}
        if test_plan and test_plan.basic_info.test_standard:
            std_sources["试验计划"] = test_plan.basic_info.test_standard
        if raw_records:
            stds = {m.get_header("std_ref") for m in raw_records.metas}
            stds.discard("")
            if stds: std_sources["原始记录"] = list(stds)[0]

        vals = list(std_sources.values())
        if len(vals) >= 2:
            def _clean(s: str) -> str:
                # Remove common prefix variants first: Q/EQCS -> EQCS
                s = re.sub(r'^[Qq]\s*/\s*', '', s)
                # Keep digits, remove separators, lowercase
                s = re.sub(r'[\s\-/]', '', s.lower())
                return s
            base = _clean(vals[0]) if vals[0] else ""
            for v in vals[1:]:
                if v and _clean(v) != base:
                    issues.append(ValidationIssue(
                        severity="WARNING", category="basic_info",
                        field_name="执行标准", description="标准编号写法不一致",
                        sources=std_sources))
                    break
        logger.info("check_done", check="basic_info", issues_found=len(issues))
        return issues

    # ── NEW: Supplier name consistency ─────────────────────────────

    @staticmethod
    def _supplier_name_check(order_form, test_plan) -> list[ValidationIssue]:
        """Check supplier name consistency between order form and test plan.

        Compares the applicant/supplier name across documents with suffix-aware
        normalization.  Example from production: order_form has "骆驼集团襄阳蓄电池
        有限公司" while test_plan has "骆驼集团蓄电池销售有限公司" — the core name
        differs, indicating the wrong entity may be referenced.
        """
        logger.info("check_start", check="supplier_name")
        issues: list[ValidationIssue] = []

        _BLANK = {"/", "-", "—", "N/A", "n/a", "\\"}

        of_name = (order_form.applicant.name_cn or "").strip()
        tp_name = (test_plan.basic_info.supplier_name or "").strip()

        # Skip if either value is blank/placeholder
        if of_name and tp_name and of_name not in _BLANK and tp_name not in _BLANK:
            # Core-name normalization: strip common legal suffixes so
            # "XX襄阳蓄电池有限公司" vs "XX蓄电池销售有限公司" reveals the
            # real difference ("襄阳" vs "销售")
            def _core_name(s: str) -> str:
                for suffix in ("股份有限公司", "有限责任公司", "有限公司", "股份"):
                    s = s.replace(suffix, "")
                return s.strip()

            of_core = _core_name(of_name)
            tp_core = _core_name(tp_name)

            if of_core != tp_core:
                issues.append(ValidationIssue(
                    severity="WARNING", category="basic_info",
                    field_name="供应商名称",
                    description=f"供应商名称不一致: 委托单'{of_name}' vs 试验计划'{tp_name}'",
                    sources={"委托单": of_name, "试验计划": tp_name}))

        logger.info("check_done", check="supplier_name", issues_found=len(issues))
        return issues

    # ── NEW: Sample count consistency ───────────────────────────────

    @staticmethod
    def _sample_count_check(test_plan, final_report) -> list[ValidationIssue]:
        """Check sample count consistency between test plan and final report.

        The test plan declares how many samples will be tested; the final report
        lists the actual serial numbers / lab IDs of samples received.  Mismatch
        may indicate untracked additional samples or an outdated test plan.
        """
        logger.info("check_start", check="sample_count")
        issues: list[ValidationIssue] = []

        tp_count_str = (test_plan.basic_info.sample_count or "").strip()
        if not tp_count_str:
            return issues

        # Extract the first integer from the plan's sample_count string
        # (e.g. "3 pcs", "2 个", "3")
        import re as _re
        m = _re.search(r'\d+', tp_count_str)
        if not m:
            return issues
        tp_count = int(m.group())

        # Count from report sample info
        si = final_report.sample if hasattr(final_report, 'sample') else None
        if si:
            rpt_count = max(
                len(si.serial_numbers or []),
                len(si.lab_sample_ids or []),
            )
            if rpt_count > 0 and tp_count != rpt_count:
                issues.append(ValidationIssue(
                    severity="WARNING", category="basic_info",
                    field_name="样品数量",
                    description=f"样品数量不一致: 试验计划 {tp_count} vs 检测报告 {rpt_count}",
                    sources={"试验计划": str(tp_count), "检测报告": str(rpt_count)}))

        logger.info("check_done", check="sample_count", issues_found=len(issues))
        return issues

    # ── NEW: Test plan number consistency ───────────────────────────

    @staticmethod
    def _plan_number_check(test_plan, final_report) -> list[ValidationIssue]:
        """Check that the test plan number referenced in the report matches the
        plan document's own plan number.

        Two scenarios are flagged:
        1. Report references a plan number but the plan document has none (可能计划未定稿)
        2. Both have values but they differ (可能版本不匹配)
        """
        logger.info("check_start", check="plan_number")
        issues: list[ValidationIssue] = []

        rpt_num = (final_report.cover.test_plan_number or "").strip()
        tp_num = (test_plan.basic_info.plan_number or "").strip()

        if rpt_num and not tp_num:
            issues.append(ValidationIssue(
                severity="INFO", category="basic_info",
                field_name="测试计划编号",
                description=f"检测报告引用了测试计划编号'{rpt_num}'，但试验计划文档中未填写计划编号（计划文档可能为客户提供，编号为实验室内部编号）",
                sources={"检测报告": rpt_num, "试验计划": "空"}))
        elif rpt_num and tp_num and rpt_num != tp_num:
            issues.append(ValidationIssue(
                severity="WARNING", category="basic_info",
                field_name="测试计划编号",
                description=f"测试计划编号不一致: 报告引用'{rpt_num}' vs 计划文档'{tp_num}'",
                sources={"检测报告": rpt_num, "试验计划": tp_num}))

        logger.info("check_done", check="plan_number", issues_found=len(issues))
        return issues

    # ── P1 checks ──────────────────────────────────────────────────

    @staticmethod
    def _dates(raw_records, final_report) -> list[ValidationIssue]:
        logger.info("check_start", check="dates")
        dates = []
        for m in raw_records.metas:
            d = _parse_date(m.get_header("test_date"))
            if d: dates.append(d)
        if not dates:
            logger.info("check_done", check="dates", issues_found=0)
            return []
        mn, mx = min(dates), max(dates)
        issues = []
        max_span = _get_max_test_date_span()
        if (mx - mn).days > max_span:
            issues.append(ValidationIssue(
                severity="WARNING", category="date",
                description=f"测试日期跨度过大: {mn} ~ {mx} ({(mx-mn).days}天, 阈值{max_span}天)"))
        logger.info("check_done", check="dates", issues_found=len(issues))
        return issues

    @staticmethod
    def _voltage(order_form, raw_records, final_report) -> list[ValidationIssue]:
        logger.info("check_start", check="voltage")
        sources = {"委托单": order_form.product.voltage}
        for m in raw_records.metas:
            v = m.get_header("power_supply")
            if v:
                sources["原始记录"] = v
                break
        issues = []
        vals = list(sources.values())
        if len(vals) >= 2 and len(set(vals)) > 1:
            issues.append(ValidationIssue(
                severity="WARNING", category="basic_info",
                field_name="额定电压", description="不同文档中的额定电压不一致",
                sources=sources))
        logger.info("check_done", check="voltage", issues_found=len(issues))
        return issues

    @staticmethod
    def _test_mode(test_plan, raw_records) -> list[ValidationIssue]:
        logger.info("check_start", check="test_mode")
        issues = []
        pmap = {_norm(p.code): p.test_mode.replace("Mode:", "").replace(" ", "")
                for p in test_plan.test_items if p.test_mode}
        rmap = {}
        for m in raw_records.metas:
            mode = m.test_mode.replace("模式", "").replace(" ", "")
            rmap[_norm(m.test_item_code)] = mode
        for code in set(pmap) & set(rmap):
            if pmap[code] and rmap[code] and pmap[code] != rmap[code]:
                issues.append(ValidationIssue(
                    severity="WARNING", category="method", field_name=code,
                    description=f"测试模式不一致: 计划 {pmap[code]} vs 记录 {rmap[code]}",
                    sources={"计划": pmap[code], "记录": rmap[code]}))
        logger.info("check_done", check="test_mode", issues_found=len(issues))
        return issues

    # ── P0: TOC 3-way coverage ─────────────────────────────────────────

    @staticmethod
    def _toc_coverage(test_plan, raw_records, final_report) -> list[ValidationIssue]:
        """3-way coverage: test_plan(required) vs raw_records(executed) vs report.toc(listed).

        Flags mismatches between what was planned, what was measured, and what
        appears in the final report's table of contents.
        """
        logger.info("check_start", check="toc_coverage")
        issues: list[ValidationIssue] = []

        _EXECUTED = {"y", "yes", "必做", "是", "●", "✓", "y "}
        plan_y = {_norm(p.code) for p in test_plan.test_items
                   if p.is_executed.strip().lower() in _EXECUTED}
        plan_all = {_norm(p.code) for p in test_plan.test_items}
        rec_codes = {_norm(m.test_item_code) for m in raw_records.metas}
        toc_codes = {_norm(t.code) for t in final_report.toc}

        # Records vs TOC: records executed but not in report TOC
        for code in sorted(rec_codes - toc_codes):
            rm = next((m for m in raw_records.metas if _norm(m.test_item_code) == code), None)
            issues.append(ValidationIssue(
                severity="WARNING", category="toc", field_name=code,
                description=f"原始记录已执行 {code}（{rm.test_item_name if rm else ''}），但报告目录中未列出",
                sources={"记录": "已执行", "报告目录": "缺失"}))

        # TOC vs records: report lists item but no raw record exists
        for code in sorted(toc_codes - rec_codes):
            tc = next((t for t in final_report.toc if _norm(t.code) == code), None)
            name = tc.name if tc else ""
            sev = "CRITICAL" if code in plan_y else "WARNING"
            issues.append(ValidationIssue(
                severity=sev, category="toc", field_name=code,
                description=f"报告目录列出了 {code}（{name}），但未找到对应的原始记录",
                sources={"报告目录": f"第{tc.page_start}页" if tc else "有", "记录": "缺失"}))

        # Plan vs TOC: plan requires it but TOC is missing it
        for code in sorted(plan_y - toc_codes):
            pi = next((p for p in test_plan.test_items if _norm(p.code) == code), None)
            issues.append(ValidationIssue(
                severity="WARNING", category="toc", field_name=code,
                description=f"计划要求执行 {code}（{pi.name if pi else ''}），但报告目录中未列出该测试项",
                sources={"计划": "Y/必做", "报告目录": "缺失"}))

        # TOC vs plan_all: report has items not in plan at all
        for code in sorted(toc_codes - plan_all):
            tc = next((t for t in final_report.toc if _norm(t.code) == code), None)
            issues.append(ValidationIssue(
                severity="INFO", category="toc", field_name=code,
                description=f"报告目录有 {code}（{tc.name if tc else ''}），但试验计划中未列出",
                sources={"计划": "未列出", "报告目录": f"第{tc.page_start}页" if tc else "有"}))

        logger.info("check_done", check="toc_coverage", issues_found=len(issues))
        return issues

    # ── P0: Report result consistency ────────────────────────────────────

    @staticmethod
    def _result_consistency(raw_records, final_report) -> list[ValidationIssue]:
        """Compare report test result conclusions with raw record conclusions.

        If raw records show '不符合' but the report says '符合', that is
        a CRITICAL issue (potentially hiding a failed test).
        """
        logger.info("check_start", check="result_consistency")
        issues: list[ValidationIssue] = []

        # Build conclusion map from raw records
        rec_conclusions: dict[str, list[str]] = {}
        for s in raw_records.conclusion_summary:
            code_norm = _norm(s.test_item_code)
            for rnd in s.rounds:
                rec_conclusions.setdefault(code_norm, []).append(
                    rnd.get("conclusion", "未知"))

        # Build result map from report results
        for rr in final_report.results:
            # Extract test code from test_item string like "EQ/IC01 针对脉冲..."
            m = re.search(r'(EQ/[A-Z]{2}\d+)', rr.test_item)
            if not m:
                continue
            code_norm = _norm(m.group(1))
            report_result = (rr.result or "").strip()

            if code_norm not in rec_conclusions:
                continue

            rec_vals = rec_conclusions[code_norm]
            any_not_pass = any(v and v != "符合" for v in rec_vals)

            if any_not_pass and report_result == "符合":
                issues.append(ValidationIssue(
                    severity="CRITICAL", category="conclusion",
                    field_name=m.group(1),
                    description=f"{m.group(1)} 原始记录有'不符合'轮次({rec_vals})，但报告结论为'符合'",
                    sources={"报告结论": report_result, "记录结论": str(rec_vals)}))
            elif not any_not_pass and report_result not in ("符合", ""):
                issues.append(ValidationIssue(
                    severity="WARNING", category="conclusion",
                    field_name=m.group(1),
                    description=f"{m.group(1)} 原始记录全部'符合'，但报告结论为'{report_result}'（可能笔误）",
                    sources={"报告结论": report_result, "记录结论": str(rec_vals)}))

        logger.info("check_done", check="result_consistency", issues_found=len(issues))
        return issues

    # ── P1: Data-row comparison ─────────────────────────────────────────

    @staticmethod
    def _data_comparison(raw_records, final_report) -> list[ValidationIssue]:
        """Compare report data rows against raw record data rows.

        Only compares rows that have meaningful frequency values (emission-type
        data: EQ/MC, EQ/MR).  Immunity-type rows (EQ/IC, EQ/IR) use parameter
        keys instead of frequencies and are excluded from this check to avoid
        false positives — they need a different comparison approach.

        The matching is liberal: if either side has fewer than
        _MIN_DATA_ROWS_FOR_COMPARISON frequency-bearing rows, the check is
        skipped entirely since the comparison would be unreliable.
        """
        logger.info("check_start", check="data_comparison")
        issues: list[ValidationIssue] = []

        # ── Build report index (only rows WITH frequency) ──
        report_index: dict[str, dict[str, any]] = {}
        for dr in final_report.data_rows:
            ti = dr.test_item
            m = re.search(r'(EQ/[A-Z]{2}\d+)', ti)
            code = _norm(m.group(1)) if m else _norm(ti)
            freq = dr.freq_mhz.strip()
            if not freq:
                continue  # immunity-type row, skip
            # Extract a single representative frequency for flexible matching
            fm = re.search(r'(\d+\.?\d*)', freq)
            match_freq = fm.group(1) if fm else freq
            key = f"{code}|{match_freq}"
            report_index[key] = dr

        # ── Build record index (only rows WITH frequency) ──
        record_index: dict[str, list[any]] = {}
        for row in raw_records.data_rows:
            code = _norm(row.test_item)
            freq = ""
            if row.spec_params:
                for k in ("频率范围", "frequency", "freq_range", "freq"):
                    if k in row.spec_params:
                        freq = str(row.spec_params[k]).strip()
                        break
            if not freq:
                freq = row.freq_range.strip()
            if freq:
                fm = re.search(r'(\d+\.?\d*)', freq)
                if fm:
                    freq = fm.group(1)
            if not freq:
                continue  # no meaningful frequency, skip
            key = f"{code}|{freq}"
            record_index.setdefault(key, []).append(row)

        # ── Skip if too few rows to compare meaningfully ──
        if len(report_index) < CrossValidator._MIN_DATA_ROWS_FOR_COMPARISON:
            logger.info("check_done", check="data_comparison",
                       skipped="too few report freq-bearing rows",
                       report_freq_rows=len(report_index),
                       record_freq_rows=len(record_index))
            return issues

        # ── Match ──
        matched = 0
        for key in report_index:
            if key in record_index:
                matched += 1

        report_only = len(report_index) - matched
        record_only = len(record_index) - matched

        if report_only > CrossValidator._MIN_DATA_ROWS_FOR_COMPARISON:
            issues.append(ValidationIssue(
                severity="WARNING", category="data",
                field_name="数据行",
                description=f"报告中有 {report_only} 个频率点未在原始记录中找到对应数据（已排除抗扰类无频率数据项，共比对 {len(report_index)} 个频率点）",
                sources={"报告": str(len(report_index)), "记录": str(len(record_index))}))

        if record_only > CrossValidator._MIN_DATA_ROWS_FOR_COMPARISON:
            issues.append(ValidationIssue(
                severity="WARNING", category="data",
                field_name="数据行",
                description=f"原始记录中有 {record_only} 个频率点未在报告中找到对应数据",
                sources={"报告": str(len(report_index)), "记录": str(len(record_index))}))

        # Overall volume check (only when both sides have enough frequency-bearing rows)
        if len(report_index) > 0 and len(record_index) > 0:
            ratio = abs(len(report_index) - len(record_index)) / max(len(report_index), len(record_index))
            if ratio > 0.2:
                issues.append(ValidationIssue(
                    severity="WARNING", category="data",
                    field_name="数据行",
                    description=f"报告数据行({len(report_index)})与原始记录数据行({len(record_index)})数量差异超过20%",
                    sources={"报告": str(len(report_index)), "记录": str(len(record_index))}))

        logger.info("check_done", check="data_comparison",
                   issues_found=len(issues), matched=matched,
                   report_only=report_only, record_only=record_only)
        return issues

    # ── P1: Method compliance ────────────────────────────────────────────

    @staticmethod
    def _method_compliance(test_plan, raw_records) -> list[ValidationIssue]:
        """Check if test execution conditions match what the test plan requires.

        Searches raw record PDF text for evidence that each test_detail
        requirement (DUT placement, harness length, ground connection, etc.)
        was documented during testing.
        """
        logger.info("check_start", check="method_compliance")
        issues: list[ValidationIssue] = []

        # Build per-code detail map
        details_by_code: dict[str, list[dict]] = {}
        for td in test_plan.test_details:
            details_by_code.setdefault(_norm(td.code), []).append(
                td.fields if hasattr(td, 'fields') else {})

        # Build per-code raw text from records
        text_by_code: dict[str, str] = {}
        for m in raw_records.metas:
            code_norm = _norm(m.test_item_code)
            text_by_code[code_norm] = m.raw_text

        # Key fields from test_details to check for in raw records
        _CHECK_FIELDS = [
            ("dut_placement", "DUT放置"),
            ("harness_length", "线束长度"),
            ("ground_connection", "接地连接"),
            ("test_position", "测试位置"),
            ("test_level", "测试等级"),
        ]

        for code, details in details_by_code.items():
            raw_text = text_by_code.get(code, "")
            if not raw_text:
                continue

            for detail in details:
                for attr, label in _CHECK_FIELDS:
                    val = detail.get(attr, "") or ""
                    if not val or val == "/":
                        continue
                    # Check if the required value is mentioned in raw records
                    if val not in raw_text:
                        # Try partial match — first 4 meaningful chars
                        short = re.sub(r'[\s\d]+$', '', val)[:4]
                        if len(short) >= 4 and short not in raw_text:
                            issues.append(ValidationIssue(
                                severity="WARNING", category="method",
                                field_name=code,
                                description=f"测试计划要求 {label}: '{val}'，原始记录中未找到相关记录",
                                sources={"计划要求": f"{label}: {val}",
                                         "原始记录": "未找到"}))

        logger.info("check_done", check="method_compliance", issues_found=len(issues))
        return issues

    # ── P1: TOC completeness ─────────────────────────────────────────────

    @staticmethod
    def _toc_completeness(final_report) -> list[ValidationIssue]:
        """Check internal consistency: report TOC entries vs report results.

        Flags items listed in TOC but missing from test results, and vice versa.
        """
        logger.info("check_start", check="toc_completeness")
        issues: list[ValidationIssue] = []

        toc_codes = {_norm(t.code) for t in final_report.toc}

        # Extract codes from report results
        result_codes: set[str] = set()
        for rr in final_report.results:
            m = re.search(r'(EQ/[A-Z]{2}\d+)', rr.test_item)
            if m:
                result_codes.add(_norm(m.group(1)))

        # TOC has items not in results
        for code in sorted(toc_codes - result_codes):
            tc = next((t for t in final_report.toc if _norm(t.code) == code), None)
            issues.append(ValidationIssue(
                severity="WARNING", category="toc", field_name=code,
                description=f"报告目录列出了 {code}（{tc.name if tc else ''}），但测试结果汇总表中未找到",
                sources={"目录": f"第{tc.page_start}页" if tc else "有",
                         "测试结果": "缺失"}))

        # Results have items not in TOC
        for code in sorted(result_codes - toc_codes):
            rr = next((r for r in final_report.results
                       if re.search(r'(EQ/[A-Z]{2}\d+)', r.test_item)
                       and _norm(re.search(r'(EQ/[A-Z]{2}\d+)', r.test_item).group(1)) == code), None)
            issues.append(ValidationIssue(
                severity="WARNING", category="toc", field_name=code,
                description=f"测试结果汇总表有 {code}（{rr.test_item if rr else ''}），但报告目录中未列出",
                sources={"目录": "缺失", "测试结果": "有"}))

        # Page range continuity check
        if toc_codes and final_report.toc:
            pages = sorted([(t.page_start, t.code) for t in final_report.toc if t.page_start > 0])
            for i in range(len(pages) - 1):
                gap = pages[i + 1][0] - pages[i][0]
                if gap > 50:  # unusually large gap
                    issues.append(ValidationIssue(
                        severity="INFO", category="toc", field_name="页码连续性",
                        description=f"目录中 {pages[i][1]}（第{pages[i][0]}页）和 {pages[i+1][1]}（第{pages[i+1][0]}页）之间间隔 {gap} 页",
                        sources={pages[i][1]: f"第{pages[i][0]}页",
                                 pages[i+1][1]: f"第{pages[i+1][0]}页"}))

        logger.info("check_done", check="toc_completeness", issues_found=len(issues))
        return issues

    # ── P0: Majority voting ──────────────────────────────────────────

    @staticmethod
    def _majority_vote(order_form, test_plan, raw_records, final_report) -> list[ValidationIssue]:
        """4-document field-level majority voting with semantic normalization.

        Collects the same named field from all provided documents, normalizes
        values, and flags discrepancies.  3:1 → WARNING on minority, 2:2 → WARNING
        with all values preserved for human decision.

        Values of "/" or "-" are treated as "not provided" and excluded from
        comparison, since many template documents use these as placeholders.
        """
        logger.info("check_start", check="majority_vote")
        issues: list[ValidationIssue] = []

        # Sentinel values that mean "field intentionally left blank"
        _BLANK = {"/", "-", "—", "N/A", "n/a", "\\"}

        # ── Collect fields from each document ──────────────────────────
        fields: dict[str, dict[str, str]] = {}

        # order_form
        if order_form:
            of = order_form
            fields["产品名称"] = {"委托单": of.product.name}
            fields["零件号"] = {"委托单": of.product.part_number}
            fields["额定电压"] = {"委托单": of.product.voltage}
            fields["工作频率"] = {"委托单": of.product.work_frequency}
            fields["制造商"] = {"委托单": of.manufacturer.name_cn}
            fields["执行标准"] = {"委托单": of.test_requirements.test_specification}
            fields["软件版本"] = {"委托单": of.other_info.software_version}
            fields["硬件版本"] = {"委托单": of.other_info.hardware_version}
            fields["样品型号"] = {"委托单": of.product.main_test_model}

        # test_plan
        if test_plan:
            tp = test_plan
            bi = tp.basic_info
            if "产品名称" not in fields: fields["产品名称"] = {}
            fields["产品名称"]["试验计划"] = bi.part_name
            if "零件号" not in fields: fields["零件号"] = {}
            fields["零件号"]["试验计划"] = bi.part_number
            if "执行标准" not in fields: fields["执行标准"] = {}
            fields["执行标准"]["试验计划"] = bi.test_standard
            if "制造商" not in fields: fields["制造商"] = {}
            fields["制造商"]["试验计划"] = bi.supplier_name
            if "软件版本" not in fields: fields["软件版本"] = {}
            fields["软件版本"]["试验计划"] = bi.software_version
            if "硬件版本" not in fields: fields["硬件版本"] = {}
            fields["硬件版本"]["试验计划"] = bi.hardware_version

        # raw_records
        if raw_records:
            rr = raw_records
            stds = {m.get_header("std_ref") for m in rr.metas}
            stds.discard("")
            if stds:
                if "执行标准" not in fields: fields["执行标准"] = {}
                fields["执行标准"]["原始记录"] = list(stds)[0]
            for m in rr.metas:
                v = m.get_header("power_supply")
                if v:
                    if "额定电压" not in fields: fields["额定电压"] = {}
                    fields["额定电压"]["原始记录"] = v
                    break

        # final_report
        if final_report:
            fr = final_report
            ci = fr.cover if hasattr(fr, 'cover') else getattr(fr, 'cover_info', None)
            si = fr.sample if hasattr(fr, 'sample') else getattr(fr, 'sample_info', None)
            if ci:
                if "执行标准" not in fields: fields["执行标准"] = {}
                std = getattr(ci, 'standard', '') or getattr(ci, '执行标准', '') or ''
                if std: fields["执行标准"]["检测报告"] = std
            if si:
                if "产品名称" not in fields: fields["产品名称"] = {}
                name = getattr(si, 'part_name', '') or getattr(si, '样品名称', '') or ''
                if name: fields["产品名称"]["检测报告"] = name
                if "零件号" not in fields: fields["零件号"] = {}
                pn = getattr(si, 'part_number', '') or getattr(si, '零件号', '') or ''
                if pn: fields["零件号"]["检测报告"] = pn
                if "额定电压" not in fields: fields["额定电压"] = {}
                voltage = getattr(si, 'rated_voltage', '') or getattr(si, '额定电压', '') or ''
                if voltage: fields["额定电压"]["检测报告"] = voltage
                model = getattr(si, 'model', '') or getattr(si, '样品型号', '') or ''
                if model:
                    if "样品型号" not in fields: fields["样品型号"] = {}
                    fields["样品型号"]["检测报告"] = model
                if "软件版本" not in fields: fields["软件版本"] = {}
                sw = getattr(si, 'sw_version', '') or ''
                if sw: fields["软件版本"]["检测报告"] = sw
                if "硬件版本" not in fields: fields["硬件版本"] = {}
                hw = getattr(si, 'hw_version', '') or ''
                if hw: fields["硬件版本"]["检测报告"] = hw

        # ── Semantic normalization ─────────────────────────────────────
        def _semantic_clean(s: str) -> str:
            if not s: return ""
            s = str(s).strip()
            # Normalize standard references: Q/EQCS → EQCS, remove separators
            s = re.sub(r'^[Qq]\s*/\s*', '', s)
            s = re.sub(r'[\s\-/]', '', s).lower()
            return s

        # ── Vote on each field ─────────────────────────────────────────
        for field_name, sources in fields.items():
            # Remove empty / blank values — "/" and "-" are placeholders
            clean_sources = {
                k: v for k, v in sources.items()
                if v and str(v).strip() and str(v).strip() not in _BLANK
            }
            if len(clean_sources) < 2:
                continue

            # Group by normalized value
            from collections import Counter
            normalized = {k: _semantic_clean(v) for k, v in clean_sources.items()}
            counts = Counter(normalized.values())
            most_common, most_count = counts.most_common(1)[0]

            if most_count >= 3 and len(clean_sources) >= 3:
                # 3+ agree → flag the minority
                minority = {k: clean_sources[k] for k in clean_sources
                           if normalized[k] != most_common}
                if minority:
                    issues.append(ValidationIssue(
                        severity="WARNING", category="basic_info",
                        field_name=field_name,
                        description=f"多数投票 {most_count}/{len(clean_sources)} 一致为 '{counts.most_common(1)[0][0]}'，少数值不一致",
                        sources={k: str(v)[:100] for k, v in clean_sources.items()}))
            elif most_count < len(clean_sources):
                # Split vote (2:2, 2:1:1, etc.)
                unique_vals = set(normalized.values())
                if len(unique_vals) >= 2:
                    issues.append(ValidationIssue(
                        severity="WARNING", category="basic_info",
                        field_name=field_name,
                        description=f"投票分裂 ({', '.join(f'{k}:{v}' for k, v in counts.items())})，需人工裁决",
                        sources={k: str(v)[:100] for k, v in clean_sources.items()}))

        logger.info("check_done", check="majority_vote", issues_found=len(issues))
        return issues

    # ── P0: Format validation ─────────────────────────────────────────

    # Field format rules (spec item 4)
    _FORMAT_RULES: list[dict] = [
        {"name": "零件号", "pattern": r"^[A-Za-z0-9\-_/.]{3,50}$",
         "desc": "字母数字 3-50 字符", "severity": "WARNING"},
        {"name": "标准编号", "pattern": r"(Q/EQCS|GB/T|CISPR|IEC|EN|ISO|SAE|UL|CSA|FCC|ETSI)",
         "desc": "标准编号需含已知前缀", "severity": "INFO"},
        {"name": "日期格式", "pattern": r"^\d{8}$|^\d{4}-\d{2}-\d{2}$|^\d{4}/\d{2}/\d{2}$",
         "desc": "YYYYMMDD/YYYY-MM-DD/YYYY/MM/DD", "severity": "WARNING"},
        {"name": "手机号", "pattern": r"^1[3-9]\d{9}$",
         "desc": "1开头11位数字", "severity": "WARNING"},
        {"name": "版本号", "pattern": r"^V?\d+\.\d+",
         "desc": "V1.0.00 或 1.0", "severity": "INFO"},
    ]

    @staticmethod
    def _format_validation(order_form, test_plan, raw_records, final_report) -> list[ValidationIssue]:
        """Validate field formats against known patterns (spec item 4)."""
        logger.info("check_start", check="format_validation")
        issues: list[ValidationIssue] = []

        # Collect string fields to check
        candidates: dict[str, list[tuple[str, str]]] = {}  # field_name → [(source, value)]

        def _offer(name: str, source: str, val: str | None):
            if val and str(val).strip():
                candidates.setdefault(name, []).append((source, str(val).strip()))

        # order_form
        if order_form:
            of = order_form
            _offer("零件号", "委托单", of.product.part_number)
            _offer("标准编号", "委托单", of.test_requirements.test_specification)
            _offer("日期格式", "委托单", of.other_info.application_date)
            _offer("手机号", "委托单", of.applicant.contact.phone)
            _offer("版本号", "委托单", of.other_info.software_version)
            _offer("版本号", "委托单", of.other_info.hardware_version)
            if of.factory.contact and of.factory.contact.phone:
                _offer("手机号", "委托单(工厂)", of.factory.contact.phone)

        # test_plan
        if test_plan:
            tp = test_plan
            _offer("零件号", "试验计划", tp.basic_info.part_number)
            _offer("标准编号", "试验计划", tp.basic_info.test_standard)

        # final_report
        if final_report:
            fr = final_report
            ci = fr.cover if hasattr(fr, 'cover') else getattr(fr, 'cover_info', None)
            si = fr.sample if hasattr(fr, 'sample') else getattr(fr, 'sample_info', None)
            if si:
                _offer("零件号", "检测报告", getattr(si, 'part_number', ''))
            if ci:
                _offer("标准编号", "检测报告", getattr(ci, 'standard', ''))

        # Check each collected value against its format rule
        for rule in CrossValidator._FORMAT_RULES:
            rule_name = rule["name"]
            if rule_name not in candidates:
                continue
            for source, val in candidates[rule_name]:
                if not re.search(rule["pattern"], val):
                    issues.append(ValidationIssue(
                        severity=rule["severity"], category="format",
                        field_name=rule_name,
                        description=f"{source}的{rule_name} '{val}' 不符合格式: {rule['desc']}",
                        sources={source: val}))

        logger.info("check_done", check="format_validation", issues_found=len(issues))
        return issues

    # ── P1: Value range validation ────────────────────────────────────

    @staticmethod
    def _range_validation(order_form, test_plan, raw_records, final_report) -> list[ValidationIssue]:
        """Validate physical value ranges (spec item 9)."""
        logger.info("check_start", check="range_validation")
        issues: list[ValidationIssue] = []

        def _check_range(field_name: str, source: str, val: str,
                         lo: float, hi: float, unit: str) -> ValidationIssue | None:
            """Try to extract a numeric value from val and check lo ≤ n ≤ hi."""
            if not val:
                return None
            # Extract numeric part: "DC 12.0V" → 12.0, "23.2℃" → 23.2
            m = re.search(r'[-+]?\d+\.?\d*', str(val))
            if not m:
                return None
            try:
                n = float(m.group())
            except ValueError:
                return None
            if n < lo or n > hi:
                return ValidationIssue(
                    severity="WARNING", category="range",
                    field_name=field_name,
                    description=f"{source}的{field_name} {val} 超出合理范围 [{lo}, {hi}] {unit}",
                    sources={source: str(val)})
            return None

        # Collect voltage values
        if order_form:
            v = order_form.product.voltage
            if v:
                issue = _check_range("电压", "委托单", v, 0, 1000, "V")
                if issue: issues.append(issue)
        if final_report:
            si = final_report.sample if hasattr(final_report, 'sample') else getattr(final_report, 'sample_info', None)
            if si:
                v = getattr(si, 'rated_voltage', '')
                if v:
                    issue = _check_range("电压", "检测报告", v, 0, 1000, "V")
                    if issue: issues.append(issue)
        if raw_records:
            for m in raw_records.metas:
                v = m.get_header("power_supply") or ""
                if v:
                    issue = _check_range("电压", "原始记录", v, 0, 1000, "V")
                    if issue: issues.append(issue)
                    break

        # Collect frequency values
        if order_form:
            f = order_form.product.work_frequency
            if f:
                issue = _check_range("频率", "委托单", f, 0, 1e12, "Hz")
                if issue: issues.append(issue)

        # Collect temp/humidity from records metadata
        if raw_records:
            for m in raw_records.metas:
                t = m.get_header("temperature") or ""
                if t:
                    issue = _check_range("温度", "原始记录", t, -40, 150, "°C")
                    if issue: issues.append(issue)
                    break
            for m in raw_records.metas:
                h = m.get_header("humidity") or ""
                if h:
                    issue = _check_range("湿度", "原始记录", h, 0, 100, "%")
                    if issue: issues.append(issue)
                    break

        logger.info("check_done", check="range_validation", issues_found=len(issues))
        return issues

    # ── P1: Regex second extraction ────────────────────────────────────

    # Regex patterns for code-level independent extraction (spec item 7)
    _REGEX_RULES: list[dict] = [
        {"name": "标准编号", "pattern": r'Q/EQCS[-\s]*\d+[-\s]*\d+',
         "source": "code_regex", "desc": "标准编号正则提取"},
        {"name": "手机号", "pattern": r'1[3-9]\d{9}',
         "source": "code_regex", "desc": "手机号正则提取"},
        {"name": "版本号", "pattern": r'V\d+\.\d+(?:\.\d+)?',
         "source": "code_regex", "desc": "版本号正则提取"},
        {"name": "测试编码", "pattern": r'EQ/[A-Z]{2}\d+',
         "source": "code_regex", "desc": "EMC测试编码正则提取"},
    ]

    @staticmethod
    def _regex_second_extraction(plain_text: str, ai_fields: dict[str, str]) -> list[dict]:
        """Run regex extraction on raw text and compare with AI values.

        Returns list of {field_name, regex_value, ai_value, match: bool}
        for fields where both regex and AI produce a value.
        """
        results: list[dict] = []
        if not plain_text:
            return results

        for rule in CrossValidator._REGEX_RULES:
            matches = re.findall(rule["pattern"], plain_text, re.IGNORECASE)
            if not matches:
                continue
            # Normalize matches
            unique_vals = list({m.strip() for m in matches})
            regex_val = unique_vals[0] if unique_vals else ""
            if not regex_val:
                continue

            # Find matching AI field (case-insensitive, normalized)
            ai_val = ai_fields.get(rule["name"], "")
            if not ai_val:
                # Try partial matching on field names
                for k, v in ai_fields.items():
                    if rule["name"] in str(k) or str(k) in rule["name"]:
                        ai_val = str(v)
                        break

            if regex_val and ai_val:
                # Compare after normalization
                norm_re = re.sub(r'[\s\-/]', '', str(regex_val)).lower()
                norm_ai = re.sub(r'[\s\-/]', '', str(ai_val)).lower()
                results.append({
                    "field_name": rule["name"],
                    "regex_value": regex_val,
                    "ai_value": ai_val,
                    "match": norm_re == norm_ai,
                })

        return results

    # ── NEW: P0 margin formula validation ────────────────────────────

    @staticmethod
    def _margin_validation(final_report) -> list[ValidationIssue]:
        """Validate margin = limit − result for every data row.

        Flags rows where |margin − (limit − result)| > 0.5 dB.
        Also flags rows where margin < −1.0 (result exceeds limit).
        """
        logger.info("check_start", check="margin_validation")
        issues: list[ValidationIssue] = []
        margin_errors = 0
        neg_margins = 0
        total = 0

        for row in final_report.data_rows[:300]:
            try:
                r = float(row.result_dbua)
                l = float(row.limit_dbua)
                m = float(row.margin_db or 0)
                total += 1
                if abs(round(l - r, 2) - round(m, 2)) > 0.5:
                    margin_errors += 1
                if m < -1.0:
                    neg_margins += 1
            except (ValueError, TypeError):
                pass

        if margin_errors > 0:
            issues.append(ValidationIssue(
                severity="CRITICAL" if margin_errors > total * 0.05 else "WARNING",
                category="logic", field_name="margin",
                description=f"余量计算错误: {margin_errors}/{total} 行 margin ≠ limit − result (差值>0.5dB)",
                sources={"检查行数": str(total), "异常行数": str(margin_errors)},
                source="deterministic"))

        if neg_margins > 0:
            issues.append(ValidationIssue(
                severity="CRITICAL", category="logic", field_name="margin",
                description=f"测试结果超标: {neg_margins} 个数据点 margin < −1.0 dB（结果超出限值）",
                sources={"超标点数": str(neg_margins), "检查行数": str(total)},
                source="deterministic"))

        logger.info("check_done", check="margin_validation",
                   issues_found=len(issues), margin_errors=margin_errors,
                   neg_margins=neg_margins, total=total)
        return issues

    # ── NEW: P0 overall conclusion consistency ───────────────────────

    @staticmethod
    def _overall_conclusion_check(final_report) -> list[ValidationIssue]:
        """Check if overall conclusion matches individual test results.

        If the cover says '符合' but individual results have non-'符合' items,
        that is a CRITICAL contradiction.
        """
        logger.info("check_start", check="overall_conclusion")
        issues: list[ValidationIssue] = []
        overall = (final_report.cover.test_conclusion or "").strip()
        if not overall:
            return issues

        non_pass = []
        for rr in final_report.results:
            r = (rr.result or "").strip()
            if r and r not in ("符合", "—", "-", "/", "N/A", ""):
                non_pass.append(f"{rr.test_item[:60]}={r}")

        if "符合" in overall and non_pass:
            issues.append(ValidationIssue(
                severity="CRITICAL", category="conclusion",
                field_name="总体结论",
                description=f"总体结论为'符合'，但 {len(non_pass)} 个单项结果非'符合': {'; '.join(non_pass[:5])}",
                sources={"报告总结论": overall, "异常单项": str(len(non_pass))},
                source="deterministic"))

        logger.info("check_done", check="overall_conclusion",
                   issues_found=len(issues), non_pass_count=len(non_pass))
        return issues

    # ── NEW: P1 issue date logic ─────────────────────────────────────

    @staticmethod
    def _issue_date_check(final_report) -> list[ValidationIssue]:
        """Validate issue date ≥ last test date.

        Also checks that start_date ≤ end_date in the test date range.
        """
        logger.info("check_start", check="issue_date")
        issues: list[ValidationIssue] = []

        rpt_dates_str = final_report.cover.test_date_range or ""
        rpt_dates = re.findall(r'(\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2})', rpt_dates_str)

        issue_str = (final_report.cover.issue_date or "").strip()
        issue_d = _parse_date(issue_str)

        if len(rpt_dates) >= 2:
            d1 = _parse_date(rpt_dates[0])
            d2 = _parse_date(rpt_dates[1])
            if d1 and d2 and d1 > d2:
                issues.append(ValidationIssue(
                    severity="WARNING", category="date", field_name="检测日期",
                    description=f"检测日期范围倒置: 开始 {d1} > 结束 {d2}",
                    sources={"报告日期范围": rpt_dates_str},
                    source="deterministic"))

        if rpt_dates and issue_d:
            end_date = _parse_date(rpt_dates[-1])
            if end_date and issue_d < end_date:
                issues.append(ValidationIssue(
                    severity="WARNING", category="date", field_name="签发日期",
                    description=f"签发日期 {issue_d} 早于检测结束日期 {end_date}",
                    sources={"签发日期": issue_str, "检测结束": str(end_date)},
                    source="deterministic"))

        logger.info("check_done", check="issue_date", issues_found=len(issues))
        return issues

    # ── NEW: P1 cover completeness ────────────────────────────────────

    @staticmethod
    def _cover_completeness(final_report) -> list[ValidationIssue]:
        """Check that required cover fields are populated."""
        logger.info("check_start", check="cover_completeness")
        issues: list[ValidationIssue] = []

        cover = final_report.cover
        fields = [
            ("报告编号", cover.report_number),
            ("委托单位", cover.client_name),
            ("样品名称", cover.sample_name),
            ("样品型号", cover.sample_model),
            ("检测日期", cover.test_date_range),
            ("检测结论", cover.test_conclusion),
            ("编制", cover.preparer),
            ("审核", cover.reviewer),
            ("批准", cover.approver),
            ("签发日期", cover.issue_date),
        ]
        missing = [name for name, val in fields if not val]
        if missing:
            issues.append(ValidationIssue(
                severity="INFO", category="format", field_name="封面",
                description=f"封面信息不完整，以下字段为空: {', '.join(missing)}",
                sources={"缺失字段": str(len(missing)), "总字段": str(len(fields))},
                source="deterministic"))

        logger.info("check_done", check="cover_completeness",
                   issues_found=len(issues), missing=len(missing))
        return issues

    # ── NEW: P0 LLM deep semantic audit ──────────────────────────────

    _LLM_AUDIT_PROMPT = """你是EMC检测报告审核专家。现在有4份关联文档需要你交叉比对:

文档1: 委托单 (测试申请)
文档2: 试验计划/大纲 (测试方案)
文档3: 全套原始记录 (实际测试数据)
文档4: 最终检测报告 (汇总输出)

这4份文档都不是"真值"——任何一份都可能存在错误。请逐一审核并输出JSON:

```json
{
  "basic_info_comparison": [
    {"field": "字段名", "report_value": "报告值", "record_value": "记录值", "match": true/false, "issue": "差异说明", "severity": "CRITICAL/WARNING/INFO"}
  ],
  "test_item_coverage": [
    {"code": "EQ/XX01", "in_report": true, "in_records": true, "name": "测试项", "issue": "", "severity": "WARNING"}
  ],
  "conclusion_comparison": [
    {"code": "EQ/XX01", "report_conclusion": "符合", "record_conclusion": "符合", "match": true, "issue": "", "severity": "WARNING"}
  ],
  "data_discrepancies": [
    {"location": "位置", "report_data": "报告数据", "record_data": "记录数据", "issue": "描述", "severity": "WARNING"}
  ],
  "logic_errors": [
    {"type": "类型", "description": "描述", "evidence": "证据", "severity": "WARNING"}
  ],
  "expert_observations": [
    {"category": "分类", "observation": "审查意见", "recommendation": "建议", "severity": "INFO"}
  ],
  "summary": {
    "total_issues": 0, "critical_count": 0, "warning_count": 0, "info_count": 0,
    "overall_assessment": "整体评价", "recommendation": "建议措施"
  }
}
```

审核要点:
1. 基本信息: 报告编号、委托单位、样品名称/型号、执行标准、额定电压、测试日期是否一致
2. 测试项覆盖: 计划要求的项目是否都执行了？报告是否列出了所有已执行项目？
3. 测试结论: 报告中的符合/不符合是否与原始记录的每轮结论一致？
4. 数据行: 频率点、读数、限值、余量是否一致？性能等级简写是否统一？
5. 仪器: 型号/系列号/校准有效期是否一致？
6. 逻辑错误: 余量计算、日期顺序、总体vs单项结论矛盾
7. 专家视角: 还有什么值得注意的问题？

注意: 只报告确实发现的问题，不要编造。字段值为空就留空。

=== 委托单 ===
{order_form_text}

=== 试验计划 ===
{test_plan_text}

=== 原始记录 ===
{records_text}

=== 检测报告 ===
{report_text}"""

    @staticmethod
    async def _llm_deep_audit(order_form, test_plan, raw_records, final_report) -> list[ValidationIssue]:
        """Run LLM-powered semantic comparison across all provided documents.

        Feeds raw text from all 4 documents to DeepSeek and parses structured
        audit findings.  Falls back gracefully on failure.
        """
        logger.info("check_start", check="llm_audit")
        issues: list[ValidationIssue] = []

        try:
            from services.deepseek_client import DeepSeekReviewer, _parse_json
            from config import DEEPSEEK_MODEL

            reviewer = DeepSeekReviewer()

            # Build raw text segments
            def _safe_text(obj, max_len: int = 20000) -> str:
                if obj is None:
                    return "(未提供)"
                txt = getattr(obj, 'raw_text', '') or ''
                if txt:
                    return txt[:max_len]
                return str(obj)[:max_len]

            prompt = CrossValidator._LLM_AUDIT_PROMPT.format(
                order_form_text=_safe_text(order_form, 15000),
                test_plan_text=_safe_text(test_plan, 15000),
                records_text=_safe_text(raw_records, 25000),
                report_text=_safe_text(final_report, 25000),
            )

            max_retries = 2
            for attempt in range(max_retries):
                try:
                    response = await reviewer.client.chat.completions.create(
                        model=DEEPSEEK_MODEL,
                        messages=[
                            {"role": "system", "content": "你是EMC检测报告审核专家，严格按JSON格式输出审核结果。不编造、不省略。"},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0, timeout=300, max_tokens=8192,
                    )
                    content = response.choices[0].message.content
                    result = _parse_json(content)
                    if not result:
                        logger.warning("llm_audit_parse_failed", attempt=attempt + 1)
                        continue

                    # Convert LLM findings to ValidationIssue list
                    llm_categories = [
                        ("basic_info_comparison", "基本信息"),
                        ("test_item_coverage", "测试项覆盖"),
                        ("conclusion_comparison", "测试结论"),
                        ("data_discrepancies", "测试数据"),
                        ("logic_errors", "数据逻辑"),
                        ("expert_observations", "专家审查"),
                    ]

                    for json_key, cat_label in llm_categories:
                        items = result.get(json_key, [])
                        if not isinstance(items, list):
                            continue
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            desc = item.get("issue") or item.get("description") or ""
                            if not desc:
                                continue
                            issues.append(ValidationIssue(
                                severity=item.get("severity", "INFO"),
                                category=cat_label,
                                field_name=str(item.get("field") or item.get("code") or item.get("type") or item.get("category") or ""),
                                description=desc,
                                sources={
                                    "报告": str(item.get("report_value") or item.get("report_data") or item.get("report_conclusion") or ""),
                                    "记录": str(item.get("record_value") or item.get("record_data") or item.get("record_conclusion") or ""),
                                },
                                source="llm"))

                    break  # success — don't retry

                except Exception as e:
                    logger.warning("llm_audit_retry", attempt=attempt + 1, error=str(e)[:200])
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2 ** attempt)
                    else:
                        logger.error("llm_audit_exhausted", retries=max_retries)

        except Exception as e:
            logger.error("llm_audit_setup_failed", error=str(e)[:200])

        logger.info("check_done", check="llm_audit", issues_found=len(issues))
        return issues
