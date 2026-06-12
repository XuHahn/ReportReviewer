"""Tests for cross-document validation."""

from __future__ import annotations

import pytest
from services.cross_validator import (
    CrossValidator, ValidationReport, ValidationIssue, _norm, _parse_date, _has_toc,
)
from models import (
    OrderFormData, OrderFormProduct, OrderFormTestRequirements,
    OrderFormCompany, OrderFormContact,
    TestPlanData, TestPlanBasicInfo, TestPlanItem, TestPlanDetail,
    ReportData, ReportCoverInfo, ReportResultItem, ReportDataRow, ReportTocItem,
    ReportSampleInfo,
)
from services.raw_records_aggregator import BatchResult, TestConclusionSummary
from services.raw_records_filename_parser import RawRecordMeta
from services.raw_records_instrument_extractor import CalibrationIssue


def _make_order(part_number="Z400259466", voltage="DC 12.0V", standard="EQCS-1204-2023"):
    return OrderFormData(
        product=OrderFormProduct(part_number=part_number, voltage=voltage),
        test_requirements=OrderFormTestRequirements(test_specification=standard))


def _make_plan(codes_y=None, codes_n=None, test_mode="Mode:2"):
    items = [TestPlanItem(code=c, name=c, is_executed="Y", test_mode=test_mode)
             for c in (codes_y or ["EQ/MC03", "EQ/IC01"])]
    items += [TestPlanItem(code=c, name=c, is_executed="N")
              for c in (codes_n or [])]
    return TestPlanData(
        basic_info=TestPlanBasicInfo(part_number="Z200129866",
                                      test_standard="Q/EQCS-1204-2023"),
        test_items=items)


def _make_records(codes=None, conclusions=None, test_mode="模式1"):
    metas = []
    for i, c in enumerate(codes or ["EQIC01", "EQIC03"]):
        conc = conclusions[i] if conclusions else "符合"
        m = RawRecordMeta(test_item_code=c, test_item_name=c,
                          sequence=f"000{i+1}", test_mode=test_mode,
                          operator="x")
        m._header_fields = {
            "test_conclusion": conc,
            "test_date": "2026/5/1", "power_supply": "DC 13.5V",
            "std_ref": "Q/EQCS-1204-2023",
        }
        metas.append(m)
    summaries = [TestConclusionSummary(test_item_code=c,
                  all_rounds_pass=(conclusions[i] if conclusions else "符合") == "符合",
                  round_count=1, rounds=[{"sequence": "0001", "operator": "x",
                  "conclusion": conclusions[i] if conclusions else "符合", "test_date": "2026/5/1"}])
                 for i, c in enumerate(codes or [])]
    return BatchResult(metas=metas, conclusion_summary=summaries,
                       total_files=len(metas), total_test_items=len(metas))


def test_norm():
    assert _norm("EQ/IC01") == "EQIC01"
    assert _norm("EQIC01") == "EQIC01"


def test_parse_date():
    assert _parse_date("20260420") == _parse_date("2026-04-20")


def test_coverage_missing():
    plan = _make_plan(codes_y=["EQ/MC03", "EQ/IC01", "EQ/MR01"])
    records = _make_records(["EQIC01"])
    issues = CrossValidator._coverage(plan, records)
    assert any(i.severity == "CRITICAL" for i in issues)


def test_coverage_extra():
    plan = _make_plan(codes_y=["EQ/IC01"])
    records = _make_records(["EQIC01", "EQMR01"])
    assert any(i.severity == "WARNING" for i in CrossValidator._coverage(plan, records))


def test_coverage_perfect():
    plan = _make_plan(codes_y=["EQ/IC01", "EQ/MC03"])
    records = _make_records(["EQIC01", "EQMC03"])
    assert CrossValidator._coverage(plan, records) == []


def test_conclusion_fail():
    records = _make_records(["EQIC01"], conclusions=["不符合"])
    assert any(i.severity == "CRITICAL" for i in CrossValidator._conclusions(records))


def test_calibration():
    ci = [CalibrationIssue(instrument_name="天线", serial_no="S1",
                            test_date="2026-05-01", calibration_end="2026-01-01",
                            days_expired=120, error_description="过期")]
    issues = CrossValidator._calibration(BatchResult(calibration_issues=ci))
    assert len(issues) >= 1


def test_voltage_mismatch():
    assert len(CrossValidator._voltage(
        _make_order(voltage="DC 12.0V"), _make_records(), None)) >= 1


def test_voltage_match():
    assert CrossValidator._voltage(
        _make_order(voltage="DC 13.5V"), _make_records(), None) == []


def test_test_mode_mismatch():
    plan = _make_plan(codes_y=["EQ/IC01"], test_mode="Mode:2")
    records = _make_records(["EQIC01"], test_mode="模式1")
    assert len(CrossValidator._test_mode(plan, records)) >= 1


def test_test_mode_match():
    plan = _make_plan(codes_y=["EQ/IC01"], test_mode="Mode:2")
    records = _make_records(["EQIC01"], test_mode="模式2")
    assert CrossValidator._test_mode(plan, records) == []


def test_standard_fuzzy_match():
    order = _make_order(standard="EQCS-1204-2023")
    issues = CrossValidator._basic_info(order, _make_plan(), _make_records(), None)
    assert issues == []  # EQCS vs Q/EQCS should fuzzy match


def test_standard_different():
    order = _make_order(standard="ISO-11452")
    assert len(CrossValidator._basic_info(order, _make_plan(), _make_records(), None)) >= 1


@pytest.mark.asyncio
async def test_full_validate_clean():
    order = _make_order()
    plan = _make_plan(codes_y=["EQ/IC01", "EQ/MC03"])
    records = _make_records(["EQIC01", "EQMC03"], conclusions=["符合", "符合"])
    report = await CrossValidator.validate(order, plan, records, enable_llm_audit=False)
    assert report.total_checks >= 5
    assert report.is_clean


@pytest.mark.asyncio
async def test_full_validate_with_issues():
    order = _make_order(voltage="DC 12.0V")
    plan = _make_plan(codes_y=["EQ/IC01", "EQ/MC03", "EQ/MR01"], test_mode="Mode:2")
    records = _make_records(["EQIC01", "EQMC03"], test_mode="模式1")
    report = await CrossValidator.validate(order, plan, records, enable_llm_audit=False)
    assert report.critical_count >= 1
    assert report.warning_count >= 1


def test_report_properties():
    r = ValidationReport(total_checks=5, passed=3,
                         issues=[ValidationIssue(severity="CRITICAL")],
                         warnings=[ValidationIssue(severity="WARNING")])
    assert r.critical_count == 1 and r.warning_count == 1 and not r.is_clean


@pytest.mark.asyncio
async def test_validate_empty():
    r = await CrossValidator.validate(enable_llm_audit=False)
    assert r.total_checks == 0 and r.is_clean


# ── _has_toc helper ────────────────────────────────────────────────────

def test_has_toc_none():
    assert not _has_toc(None)


def test_has_toc_no_attr():
    assert not _has_toc(object())


def test_has_toc_empty():
    report = ReportData()
    assert not _has_toc(report)


def test_has_toc_with_items():
    report = ReportData(toc=[ReportTocItem(code="EQ/MC01", name="t", page_start=3)])
    assert _has_toc(report)


# ── TOC coverage tests ─────────────────────────────────────────────────

def _make_report_with_toc(toc_codes=None, result_codes=None):
    """Build a ReportData with TOC items + matching results."""
    toc = [ReportTocItem(code=c, name=c, page_start=i * 10 + 3)
           for i, c in enumerate(toc_codes or [])]
    results = [ReportResultItem(test_item=f"{c} test", result="符合")
               for c in (result_codes or toc_codes or [])]
    return ReportData(toc=toc, results=results,
                      cover=ReportCoverInfo(report_number="R1"))


def test_toc_coverage_all_match():
    plan = _make_plan(codes_y=["EQ/IC01", "EQ/MC03"])
    records = _make_records(["EQIC01", "EQMC03"])
    report = _make_report_with_toc(["EQ/IC01", "EQ/MC03"])
    issues = CrossValidator._toc_coverage(plan, records, report)
    assert issues == []


def test_toc_coverage_record_not_in_toc():
    """Raw record executed but TOC missing it."""
    plan = _make_plan(codes_y=["EQ/IC01", "EQ/MC03"])
    records = _make_records(["EQIC01", "EQMC03"])
    report = _make_report_with_toc(["EQ/IC01"])  # missing EQ/MC03
    issues = CrossValidator._toc_coverage(plan, records, report)
    assert any("EQMC03" in i.field_name for i in issues)


def test_toc_coverage_toc_not_in_record():
    """TOC lists item but no raw record exists."""
    plan = _make_plan(codes_y=["EQ/IC01"])
    records = _make_records(["EQIC01"])
    report = _make_report_with_toc(["EQ/IC01", "EQ/MC03"])  # extra EQ/MC03
    issues = CrossValidator._toc_coverage(plan, records, report)
    assert any("EQMC03" in i.field_name for i in issues)


def test_toc_coverage_plan_required_not_in_toc():
    """Plan required but TOC doesn't list it."""
    plan = _make_plan(codes_y=["EQ/IC01", "EQ/MC03"])
    records = _make_records(["EQIC01", "EQMC03"])
    report = _make_report_with_toc(["EQ/IC01"])  # missing EQ/MC03 from TOC
    issues = CrossValidator._toc_coverage(plan, records, report)
    assert any("EQMC03" in i.field_name for i in issues)


def test_toc_coverage_extra_in_toc_not_in_plan():
    """TOC has code not in plan at all."""
    plan = _make_plan(codes_y=["EQ/IC01"])
    records = _make_records(["EQIC01", "EQMR01"])
    report = _make_report_with_toc(["EQ/IC01", "EQ/MR01"])
    issues = CrossValidator._toc_coverage(plan, records, report)
    assert any("EQMR01" in i.field_name for i in issues)


# ── Result consistency tests ───────────────────────────────────────────

def test_result_consistency_critical():
    """Raw records say '不符合' but report says '符合' → CRITICAL."""
    records = _make_records(["EQIC01"], conclusions=["不符合"])
    report = ReportData(
        cover=ReportCoverInfo(report_number="R1"),
        results=[ReportResultItem(test_item="EQ/IC01 test", result="符合")],
    )
    issues = CrossValidator._result_consistency(records, report)
    assert any(i.severity == "CRITICAL" for i in issues)


def test_result_consistency_warning():
    """Raw records all '符合' but report says '不符合' → WARNING."""
    records = _make_records(["EQIC01"], conclusions=["符合"])
    report = ReportData(
        cover=ReportCoverInfo(report_number="R1"),
        results=[ReportResultItem(test_item="EQ/IC01 test", result="不符合")],
    )
    issues = CrossValidator._result_consistency(records, report)
    assert any(i.severity == "WARNING" for i in issues)


def test_result_consistency_match():
    """Both say '符合' → no issues."""
    records = _make_records(["EQIC01"], conclusions=["符合"])
    report = ReportData(
        cover=ReportCoverInfo(report_number="R1"),
        results=[ReportResultItem(test_item="EQ/IC01 test", result="符合")],
    )
    issues = CrossValidator._result_consistency(records, report)
    assert issues == []


def test_result_consistency_no_match():
    """Report has test item not in records → skip."""
    records = _make_records(["EQIC01"])
    report = ReportData(
        cover=ReportCoverInfo(report_number="R1"),
        results=[ReportResultItem(test_item="XYZ001 test", result="符合")],
    )
    issues = CrossValidator._result_consistency(records, report)
    assert issues == []


# ── Data comparison tests ──────────────────────────────────────────────

def test_data_comparison_empty():
    records = _make_records()
    records.data_rows = []
    report = ReportData(cover=ReportCoverInfo(report_number="R1"), data_rows=[])
    issues = CrossValidator._data_comparison(records, report)
    assert issues == []


def test_data_comparison_volume_mismatch():
    """Large difference in data row count should be flagged."""
    from services.raw_records_table_extractor import TestDataRow
    records = _make_records(["EQIC01"])
    # Only 1 record data row
    records.data_rows = [TestDataRow(test_item="EQIC01", freq_range="0.15")]
    report = ReportData(
        cover=ReportCoverInfo(report_number="R1"),
        # 10 report data rows with distinct frequencies (different keys)
        data_rows=[
            ReportDataRow(test_item="EQ/IC01", freq_mhz=str(0.1 + i * 0.05)[:5])
            for i in range(10)
        ],
    )
    issues = CrossValidator._data_comparison(records, report)
    # Either report-only or volume mismatch > 20% should produce issues
    assert len(issues) >= 1


# ── Method compliance tests ────────────────────────────────────────────

def test_method_compliance_no_details():
    plan = _make_plan(codes_y=["EQ/IC01"])
    records = _make_records(["EQIC01"])
    issues = CrossValidator._method_compliance(plan, records)
    assert issues == []  # no test_details to check


def test_method_compliance_with_details():
    plan = _make_plan(codes_y=["EQ/IC01"])
    plan.test_details = [
        TestPlanDetail(code="EQ/IC01", fields={
            "dut_placement": "50mm绝缘支撑上",
            "harness_length": "",
            "ground_connection": "",
        }),
    ]
    records = _make_records(["EQIC01"])
    # Set raw_text on the meta so method_compliance can search it
    records.metas[0].raw_text = "DUT放置在金属板上"  # different from required
    issues = CrossValidator._method_compliance(plan, records)
    # Should flag that "50mm绝缘支撑上" is not found in raw text
    assert any("DUT放置" in i.description for i in issues)


# ── TOC completeness tests ─────────────────────────────────────────────

def test_toc_completeness_match():
    report = _make_report_with_toc(["EQ/IC01", "EQ/MC03"])
    issues = CrossValidator._toc_completeness(report)
    assert issues == []


def test_toc_completeness_toc_missing_result():
    """TOC has item not in results."""
    report = _make_report_with_toc(
        toc_codes=["EQ/IC01", "EQ/MC03"],
        result_codes=["EQ/IC01"],  # missing EQ/MC03
    )
    issues = CrossValidator._toc_completeness(report)
    assert any("EQMC03" in i.field_name for i in issues)


def test_toc_completeness_result_missing_toc():
    """Results have item not in TOC."""
    report = _make_report_with_toc(
        toc_codes=["EQ/IC01"],
        result_codes=["EQ/IC01", "EQ/MC03"],  # extra EQ/MC03 in results
    )
    issues = CrossValidator._toc_completeness(report)
    assert any("EQMC03" in i.field_name for i in issues)


def test_toc_completeness_page_gap():
    """Large page gap should produce INFO."""
    report = ReportData(
        cover=ReportCoverInfo(report_number="R1"),
        toc=[
            ReportTocItem(code="EQ/IC01", name="t1", page_start=3),
            ReportTocItem(code="EQ/MC03", name="t2", page_start=200),  # big gap
        ],
        results=[
            ReportResultItem(test_item="EQ/IC01 test"),
            ReportResultItem(test_item="EQ/MC03 test"),
        ],
    )
    issues = CrossValidator._toc_completeness(report)
    assert any(i.severity == "INFO" for i in issues)


# ── Coverage with TOC ──────────────────────────────────────────────────

def test_coverage_with_toc_missing_in_both():
    """Plan requires, record missing, TOC also missing."""
    plan = _make_plan(codes_y=["EQ/IC01", "EQ/MC03"])
    records = _make_records(["EQIC01"])  # missing EQ/MC03
    report = _make_report_with_toc(["EQ/IC01"])  # TOC also missing EQ/MC03
    issues = CrossValidator._coverage(plan, records, report)
    assert any("报告目录中也未出现" in i.description for i in issues)


def test_coverage_with_toc_missing_but_in_toc():
    """Plan requires, record missing, but TOC has it."""
    plan = _make_plan(codes_y=["EQ/IC01", "EQ/MC03"])
    records = _make_records(["EQIC01"])  # missing EQ/MC03
    report = _make_report_with_toc(["EQ/IC01", "EQ/MC03"])  # TOC has it
    issues = CrossValidator._coverage(plan, records, report)
    assert any("可能原始记录丢失" in i.description for i in issues)


# ── Full validate with new checks ──────────────────────────────────────

@pytest.mark.asyncio
async def test_full_validate_with_report():
    """Full validation including report with TOC should include new checks."""
    order = _make_order()
    plan = _make_plan(codes_y=["EQ/IC01", "EQ/MC03"])
    records = _make_records(["EQIC01", "EQMC03"])
    report = _make_report_with_toc(["EQ/IC01", "EQ/MC03"])
    report.cover = ReportCoverInfo(report_number="R1")
    result = await CrossValidator.validate(order, plan, records, report, enable_llm_audit=False)
    assert result.total_checks >= 10  # old + new checks
    assert result.is_clean


# ── Helper: build a ReportData with full cover + data_rows for new checks ──

def _make_full_report(
    data_rows=None,
    test_conclusion: str = "",
    issue_date: str = "",
    test_date_range: str = "",
    report_number: str = "R2024001",
    client_name: str = "测试委托单位",
    sample_name: str = "测试样品",
    sample_model: str = "MODEL-X",
    preparer: str = "编制人",
    reviewer: str = "审核人",
    approver: str = "批准人",
    results=None,
):
    return ReportData(
        cover=ReportCoverInfo(
            report_number=report_number,
            client_name=client_name,
            sample_name=sample_name,
            sample_model=sample_model,
            test_date_range=test_date_range,
            test_conclusion=test_conclusion,
            preparer=preparer,
            reviewer=reviewer,
            approver=approver,
            issue_date=issue_date,
        ),
        data_rows=data_rows or [],
        results=results or [],
    )


# ── _margin_validation tests ────────────────────────────────────────────

def test_margin_validation_clean():
    """All data rows have correct margin = limit - result."""
    rows = [
        ReportDataRow(test_item="EQ/IC01", freq_mhz="0.15",
                      result_dbua="20.0", limit_dbua="40.0", margin_db="20.0"),
        ReportDataRow(test_item="EQ/IC01", freq_mhz="0.50",
                      result_dbua="30.0", limit_dbua="45.0", margin_db="15.0"),
    ]
    report = _make_full_report(data_rows=rows)
    issues = CrossValidator._margin_validation(report)
    assert issues == []


def test_margin_validation_miscalc():
    """margin != limit - result triggers issue."""
    rows = [
        ReportDataRow(test_item="EQ/IC01", freq_mhz="0.15",
                      result_dbua="20.0", limit_dbua="40.0", margin_db="10.0"),
    ]
    report = _make_full_report(data_rows=rows)
    issues = CrossValidator._margin_validation(report)
    assert len(issues) >= 1
    assert any("余量计算错误" in i.description for i in issues)


def test_margin_validation_negative():
    """margin < -1.0 triggers CRITICAL for exceeding limit."""
    rows = [
        ReportDataRow(test_item="EQ/IC01", freq_mhz="0.15",
                      result_dbua="52.0", limit_dbua="50.0", margin_db="-2.0"),
    ]
    report = _make_full_report(data_rows=rows)
    issues = CrossValidator._margin_validation(report)
    assert any("超标" in i.description for i in issues)
    assert any(i.severity == "CRITICAL" for i in issues)


def test_margin_validation_empty():
    """No data rows — no issues."""
    report = _make_full_report(data_rows=[])
    issues = CrossValidator._margin_validation(report)
    assert issues == []


# ── _overall_conclusion_check tests ─────────────────────────────────────

def test_overall_conclusion_contradiction():
    """Overall '符合' but individual result is not '符合'."""
    results = [
        ReportResultItem(test_item="EQ/IC01 test", result="不符合"),
    ]
    report = _make_full_report(test_conclusion="符合", results=results)
    issues = CrossValidator._overall_conclusion_check(report)
    assert len(issues) >= 1
    assert any(i.severity == "CRITICAL" for i in issues)
    assert any("总体结论" in i.field_name for i in issues)


def test_overall_conclusion_clean():
    """All results '符合' and overall '符合' — no issue."""
    results = [
        ReportResultItem(test_item="EQ/IC01 test", result="符合"),
        ReportResultItem(test_item="EQ/MC03 test", result="符合"),
    ]
    report = _make_full_report(test_conclusion="符合", results=results)
    issues = CrossValidator._overall_conclusion_check(report)
    assert issues == []


def test_overall_conclusion_empty():
    """No overall conclusion — skip check."""
    report = _make_full_report(
        test_conclusion="",
        results=[ReportResultItem(test_item="EQ/IC01 test", result="不符合")],
    )
    issues = CrossValidator._overall_conclusion_check(report)
    assert issues == []


# ── _issue_date_check tests ─────────────────────────────────────────────

def test_issue_date_ok():
    """Issue date after test end date — no issue."""
    report = _make_full_report(
        test_date_range="2024/03/01~2024/03/05",
        issue_date="2024/03/10",
    )
    issues = CrossValidator._issue_date_check(report)
    assert issues == []


def test_issue_date_before_test_end():
    """Issue date before test end date — WARNING."""
    report = _make_full_report(
        test_date_range="2024/03/01~2024/03/15",
        issue_date="2024/03/10",
    )
    issues = CrossValidator._issue_date_check(report)
    assert len(issues) >= 1
    assert any("签发日期" in i.field_name for i in issues)
    assert any(i.severity == "WARNING" for i in issues)


def test_issue_date_range_reversed():
    """Test start > test end — WARNING."""
    report = _make_full_report(
        test_date_range="2024/03/10~2024/03/05",
        issue_date="2024/03/15",
    )
    issues = CrossValidator._issue_date_check(report)
    assert len(issues) >= 1
    assert any("倒置" in i.description for i in issues)


def test_issue_date_no_data():
    """No dates at all — no issues."""
    report = _make_full_report()
    issues = CrossValidator._issue_date_check(report)
    assert issues == []


# ── _cover_completeness tests ───────────────────────────────────────────

def test_cover_completeness_full():
    """All cover fields populated — no issue."""
    report = _make_full_report(
        test_conclusion="符合",
        test_date_range="2024/03/01~2024/03/05",
        issue_date="2024/03/10",
    )
    issues = CrossValidator._cover_completeness(report)
    assert issues == []


def test_cover_completeness_missing():
    """Missing cover fields — INFO issue."""
    report = ReportData(
        cover=ReportCoverInfo(),
        data_rows=[],
        results=[],
    )
    issues = CrossValidator._cover_completeness(report)
    assert len(issues) >= 1
    assert any("封面" in i.field_name for i in issues)
    assert any(i.severity == "INFO" for i in issues)


# ── Supplier name check tests ──────────────────────────────────────


def test_supplier_name_match():
    """Same supplier name → no issues."""
    order = OrderFormData(
        applicant=OrderFormCompany(name_cn="骆驼集团襄阳蓄电池有限公司"),
        product=OrderFormProduct(),
        test_requirements=OrderFormTestRequirements(),
    )
    plan = TestPlanData(
        basic_info=TestPlanBasicInfo(supplier_name="骆驼集团襄阳蓄电池有限公司"),
        test_items=[],
    )
    issues = CrossValidator._supplier_name_check(order, plan)
    assert issues == []


def test_supplier_name_mismatch():
    """Different core name after suffix removal → WARNING."""
    order = OrderFormData(
        applicant=OrderFormCompany(name_cn="骆驼集团襄阳蓄电池有限公司"),
        product=OrderFormProduct(),
        test_requirements=OrderFormTestRequirements(),
    )
    plan = TestPlanData(
        basic_info=TestPlanBasicInfo(supplier_name="骆驼集团蓄电池销售有限公司"),
        test_items=[],
    )
    issues = CrossValidator._supplier_name_check(order, plan)
    assert len(issues) == 1
    assert issues[0].severity == "WARNING"
    assert "供应商名称" in issues[0].field_name


def test_supplier_name_suffix_only_diff():
    """Only suffix differs (e.g. 有限公司 vs 股份有限公司) — core still matches."""
    order = OrderFormData(
        applicant=OrderFormCompany(name_cn="骆驼集团襄阳蓄电池有限公司"),
        product=OrderFormProduct(),
        test_requirements=OrderFormTestRequirements(),
    )
    plan = TestPlanData(
        basic_info=TestPlanBasicInfo(
            supplier_name="骆驼集团襄阳蓄电池股份有限公司"),
        test_items=[],
    )
    issues = CrossValidator._supplier_name_check(order, plan)
    assert issues == []  # core name matches after suffix removal


# ── Sample count check tests ───────────────────────────────────────


def test_sample_count_match():
    """Same sample count → no issues."""
    plan = TestPlanData(
        basic_info=TestPlanBasicInfo(sample_count="3"),
        test_items=[],
    )
    report = ReportData(
        cover=ReportCoverInfo(),
        sample=ReportSampleInfo(lab_sample_ids=["S001", "S002", "S003"]),
        data_rows=[], results=[],
    )
    issues = CrossValidator._sample_count_check(plan, report)
    assert issues == []


def test_sample_count_mismatch():
    """Test plan says 3, report has 6 → WARNING."""
    plan = TestPlanData(
        basic_info=TestPlanBasicInfo(sample_count="3"),
        test_items=[],
    )
    report = ReportData(
        cover=ReportCoverInfo(),
        sample=ReportSampleInfo(lab_sample_ids=[
            "S001", "S002", "S003", "S004", "S005", "S006",
        ]),
        data_rows=[], results=[],
    )
    issues = CrossValidator._sample_count_check(plan, report)
    assert len(issues) == 1
    assert issues[0].severity == "WARNING"
    assert "样品数量" in issues[0].field_name


def test_sample_count_missing_in_plan():
    """Plan has no sample_count → skipped gracefully."""
    plan = TestPlanData(
        basic_info=TestPlanBasicInfo(sample_count=""),
        test_items=[],
    )
    report = ReportData(
        cover=ReportCoverInfo(),
        sample=ReportSampleInfo(lab_sample_ids=["S001"]),
        data_rows=[], results=[],
    )
    issues = CrossValidator._sample_count_check(plan, report)
    assert issues == []


# ── Plan number check tests ────────────────────────────────────────


def test_plan_number_match():
    """Same plan number → no issues."""
    plan = TestPlanData(
        basic_info=TestPlanBasicInfo(plan_number="H56C_EMC_2026-03-200651"),
        test_items=[],
    )
    report = ReportData(
        cover=ReportCoverInfo(test_plan_number="H56C_EMC_2026-03-200651"),
        data_rows=[], results=[],
    )
    issues = CrossValidator._plan_number_check(plan, report)
    assert issues == []


def test_plan_number_missing_in_plan():
    """Report references a plan number but plan has it empty → INFO (plan may be customer-provided)."""
    plan = TestPlanData(
        basic_info=TestPlanBasicInfo(plan_number=""),
        test_items=[],
    )
    report = ReportData(
        cover=ReportCoverInfo(test_plan_number="H56C/D/E_EMC_2026-03-200651"),
        data_rows=[], results=[],
    )
    issues = CrossValidator._plan_number_check(plan, report)
    assert len(issues) == 1
    assert issues[0].severity == "INFO"
    assert "未填写" in issues[0].description


def test_plan_number_mismatch():
    """Different plan numbers → WARNING."""
    plan = TestPlanData(
        basic_info=TestPlanBasicInfo(plan_number="OLD_PLAN_V1"),
        test_items=[],
    )
    report = ReportData(
        cover=ReportCoverInfo(test_plan_number="NEW_PLAN_V2"),
        data_rows=[], results=[],
    )
    issues = CrossValidator._plan_number_check(plan, report)
    assert len(issues) == 1
    assert issues[0].severity == "WARNING"
    assert "不一致" in issues[0].description
