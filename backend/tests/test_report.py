"""Tests for report extraction."""

from __future__ import annotations

import pytest
from pathlib import Path
from io import BytesIO
from services.report_extractor import (
    count_test_codes, validate_report, ExtractionError, ReportExtractor,
)
from utils.pdf_utils import extract_pdf_text as _extract_text
from models import (
    ReportData, ReportCoverInfo, ReportResultItem, ReportDataRow,
    ReportTocItem, ReportSampleInfo, ReportInstrument, TestItemExtraction,
    TestResultsSection, SampleResultBlock,
)

SAMPLE = Path(__file__).parent.parent.parent / "example" / "E20260402869601-1正式报告.pdf"



@pytest.fixture
def sample_bytes() -> bytes:
    if not SAMPLE.exists():
        pytest.skip("Sample not found")
    return SAMPLE.read_bytes()


def test_extract_text(sample_bytes: bytes):
    text = _extract_text(sample_bytes)
    assert len(text) > 50000
    assert "E20260402869601" in text


def test_count_codes(sample_bytes: bytes):
    text = _extract_text(sample_bytes)
    codes = count_test_codes(text)
    assert len(codes) >= 13  # 17 test items, some without EQ prefix


def test_validate_ok():
    text = "EQ/IC01 EQ/MC03"
    data = ReportData(
        cover=ReportCoverInfo(report_number="X"),
        results=[ReportResultItem(test_item="EQ/IC01 x"), ReportResultItem(test_item="EQ/MC03 x")],
    )
    assert validate_report(text, data) == []


def test_validate_missing():
    text = "EQ/IC01 EQ/MC03 EQ/MR01"
    data = ReportData(results=[ReportResultItem(test_item="EQ/IC01 x")])
    assert len(validate_report(text, data)) >= 1


def test_margin_ok():
    data = ReportData(cover=ReportCoverInfo(report_number="X"),
                      data_rows=[ReportDataRow(result_dbua="38.52", limit_dbua="66", margin_db="27.48")])
    assert validate_report("", data) == []


def test_margin_bad():
    data = ReportData(cover=ReportCoverInfo(report_number="X"),
                      data_rows=[ReportDataRow(result_dbua="38.52", limit_dbua="66", margin_db="99.99")])
    assert len(validate_report("", data)) >= 1


def test_extraction_error():
    e = ExtractionError("msg", [{"errors": ["x"]}])
    assert "msg" in str(e)


def test_item_sample_coverage_gap_detects_omitted_sample_blocks():
    text = "Sample No. E202508277046-0001\nSample No. E202508277046-0002"
    items = [TestItemExtraction(
        test_item_name="Voltage test",
        test_results=TestResultsSection(sample_data=[
            SampleResultBlock(sample_id="E202508277046-0001", mode="Mode 1"),
        ]),
    )]

    assert ReportExtractor._item_sample_coverage_gap(text, items) == (2, 1, 1)


def test_models():
    c = ReportCoverInfo(report_number="R1", client_name="C")
    assert c.report_number == "R1"
    t = ReportTocItem(code="EQ/MC01", name="test", page_start=3, page_end=25)
    assert t.code == "EQ/MC01"
    assert t.page_start == 3
    assert t.page_end == 25


def test_split_code_less_report_by_result_heading_and_excludes_setup():
    text = """RESULT SUMMARY
Reverse voltage\tFail

5.\tREVERSE VOLTAGE\t11
5.1\tTEST SPECIFICATION\t11

REVERSE VOLTAGE

TEST SPECIFICATION
Supply voltage: -14 V

TEST PROCEDURE
Apply the voltage.

TEST SETUP
Diagram-only setup description

PHOTOGRAPH OF THE TEST ARRANGEMENT
Photo caption

TEST RESULTS
Sample 001\nResult: Pass

OPEN CIRCUIT TESTS

TEST SPECIFICATION
Open each terminal.

TEST RESULTS
Sample 002\nResult: Pass
"""
    results = [
        ReportResultItem(test_item="Reverse voltage"),
        ReportResultItem(test_item="Open circuit tests"),
    ]

    sections = ReportExtractor._split_by_result_item(text, results)

    assert [code for code, _ in sections] == ["ITEM_1", "ITEM_2"]
    assert "Supply voltage: -14 V" in sections[0][1]
    assert "Result: Pass" in sections[0][1]
    assert "Diagram-only setup description" not in sections[0][1]
    assert "Photo caption" not in sections[0][1]


def test_split_numbered_chinese_report_by_result_heading():
    text = """第 4 页
1. 测试结果
供电电压缓升缓降试验 符合
电源电压瞬间降低试验 符合
第 9 页
5. 供电电压缓升缓降试验
5.1 测试规范
电压从 16V 下降到 0V
5.2 测试程序
执行一个循环
5.5 测试结果
样品编号 E202603209520-0001
结果 符合
第 14 页
6. 电源电压瞬间降低试验
6.1 测试规范
电压下降到 4.5V
6.5 测试结果
样品编号 E202603209520-0002
结果 符合
"""
    results = [
        ReportResultItem(test_item="供电电压缓升缓降试验"),
        ReportResultItem(test_item="电源电压瞬间降低试验"),
    ]

    sections = ReportExtractor._split_by_result_item(text, results)

    assert [code for code, _ in sections] == ["ITEM_1", "ITEM_2"]
    assert "E202603209520-0001" in sections[0][1]
    assert "E202603209520-0002" in sections[1][1]


@pytest.mark.asyncio
async def test_report_ai_pass_uses_structured_extraction_policy():
    class FakeReviewer:
        def __init__(self):
            self.kwargs = None

        async def _call_api(self, **kwargs):
            self.kwargs = kwargs
            return {"测试仪器": []}

    reviewer = FakeReviewer()
    result = await ReportExtractor._call_ai_pass(
        reviewer,
        '返回 JSON：{"测试仪器": []}',
        "动态文档内容",
        pass_name="instruments",
        max_tokens=32768,
    )

    assert result == {"测试仪器": []}
    assert reviewer.kwargs["task_kind"] == "structured_extraction"
    assert "model" not in reviewer.kwargs
    assert reviewer.kwargs["system_prompt"].startswith("返回 JSON")
    assert reviewer.kwargs["prompt"] == "文档内容：\n动态文档内容"


@pytest.mark.asyncio
async def test_draft_report_with_blank_cover_keeps_result_body_as_partial(monkeypatch):
    """Blank template fields must not discard an otherwise reviewable report."""

    class FakeReviewer:
        async def _call_api(self, **kwargs):
            stage = kwargs["stage"]
            if stage == "structural":
                return {
                    "封面": {"报告编号": "", "样品名称": ""},
                    "样品描述": {},
                    "测试结果": [{"测试项目": "反向电压", "结果": "不符合"}],
                }
            if stage == "instruments":
                return {"测试仪器": []}
            return {}

    monkeypatch.setattr(
        "services.report_extractor._extract_file_text",
        lambda _content, _filename: "Test Report\nReport No.: Report报告编号\n测试结果\n反向电压 不符合",
    )

    data = await ReportExtractor.extract(
        b"draft report", "draft-report.docx", reviewer=FakeReviewer(),
    )

    assert data.cover.report_number == ""
    assert len(data.results) == 1
    assert data.extraction_quality == "partial"
    assert "structural/cover_identity_missing" in data.failed_passes


@pytest.mark.asyncio
async def test_report_without_report_number_stays_partial_even_if_sample_is_found(monkeypatch):
    class FakeReviewer:
        async def _call_api(self, **kwargs):
            if kwargs["stage"] == "structural":
                return {
                    "封面": {"报告编号": "", "样品名称": "Motor"},
                    "样品描述": {"样品名称": "Motor"},
                    "测试结果": [{"测试项目": "反向电压", "结果": "不符合"}],
                }
            if kwargs["stage"] == "instruments":
                return {"测试仪器": []}
            return {}

    monkeypatch.setattr(
        "services.report_extractor._extract_file_text",
        lambda _content, _filename: "Test Report\nReport No.: Report报告编号\nMotor\n反向电压 不符合",
    )

    data = await ReportExtractor.extract(
        b"draft report", "draft-report.docx", reviewer=FakeReviewer(),
    )

    assert data.extraction_quality == "partial"
    assert "structural/cover_identity_missing" in data.failed_passes


# ── TOC extraction tests ──────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_count,first_code", [
    # Standard dots-leader format
    ("EQ/MC01  电源线传导发射测量 ....................... 3\n"
     "EQ/MC02  信号线传导发射测量 ....................... 12\n"
     "EQ/IC01  针对脉冲测试 ............................ 25",
     3, "EQ/MC01"),
    # Page range format (15-25)
    ("EQ/MC01  电源线测试 .............. 15-25\n"
     "EQ/MC03  信号线测试 .............. 26-30",
     2, "EQ/MC01"),
    # Mixed format
    ("EQ/MC01 电源线测试 .............. 15-25\n"
     "EQ/IC01 脉冲测试 30\n"
     "EQ/MR01 磁场测试  .....  42",
     3, "EQ/MC01"),
    # No EQ/ codes
    ("目录\n1. 概述\n2. 方法", 0, None),
    # Prefix but colon in line (filtered out)
    ("EQ/MC01 电源线 15", 1, "EQ/MC01"),
])
def test_extract_toc(text, expected_count, first_code):
    items = ReportExtractor._extract_toc(text)
    assert len(items) == expected_count
    if first_code:
        assert items[0].code == first_code
        assert items[0].page_start > 0


def test_extract_toc_page_range():
    """TOC with page range should capture both start and end pages."""
    text = "EQ/MC01  电源线测试 .............. 15-25"
    items = ReportExtractor._extract_toc(text)
    assert len(items) == 1
    assert items[0].page_start == 15
    assert items[0].page_end == 25


def test_extract_toc_empty():
    assert ReportExtractor._extract_toc("") == []
    assert ReportExtractor._extract_toc("Some random text without EQ codes.") == []


def test_extract_toc_from_front_matter():
    """TOC extraction only searches first 8000 chars."""
    # Code at position 9000 should NOT be found
    front = "x" * 5000 + "\nEQ/MC01  test .............. 3\n" + "y" * 5000
    items = ReportExtractor._extract_toc(front)
    assert len(items) >= 1
    assert items[0].code == "EQ/MC01"


# ── Grouping & deduplication tests ─────────────────────────────────────

def test_group_test_codes():
    codes = ["EQ/MC01", "EQ/MC03", "EQ/IC01", "EQ/IC05", "EQ/IR02"]
    groups = ReportExtractor._group_test_codes(codes)
    assert "MC" in groups
    assert "IC" in groups
    assert "IR" in groups
    assert groups["MC"] == ["EQ/MC01", "EQ/MC03"]
    assert groups["IC"] == ["EQ/IC01", "EQ/IC05"]
    assert groups["IR"] == ["EQ/IR02"]


def test_group_test_codes_other():
    """Codes without a known prefix go to OTHER."""
    codes = ["XYZ001", "ABC002"]
    groups = ReportExtractor._group_test_codes(codes)
    assert "OTHER" in groups
    assert groups["OTHER"] == codes


def test_deduplicate_data_rows():
    rows = [
        ReportDataRow(test_item="EQ/MC01", freq_mhz="0.15", reading="30.0", correction_db="8.5"),
        ReportDataRow(test_item="EQ/MC01", freq_mhz="0.15", reading="30.0", correction_db="8.5"),  # dup
        ReportDataRow(test_item="EQ/MC01", freq_mhz="0.20", reading="28.0", correction_db="8.5"),  # diff freq
        ReportDataRow(test_item="EQ/MC02", freq_mhz="0.15", reading="30.0", correction_db="8.5"),  # diff item
    ]
    unique = ReportExtractor._deduplicate_data_rows(rows)
    assert len(unique) == 3  # first duplicate removed


def test_deduplicate_data_rows_empty():
    assert ReportExtractor._deduplicate_data_rows([]) == []


def test_deduplicate_data_rows_no_dups():
    rows = [
        ReportDataRow(test_item="EQ/MC01", freq_mhz="0.15", reading="30.0", correction_db="8.5"),
        ReportDataRow(test_item="EQ/MC01", freq_mhz="0.20", reading="28.0", correction_db="8.5"),
    ]
    unique = ReportExtractor._deduplicate_data_rows(rows)
    assert len(unique) == 2


# ── Structural parsing tests ───────────────────────────────────────────

def test_parse_structural_minimal():
    result = {
        "封面": {"报告编号": "R001", "样品名称": "Test"},
        "测试结果": [{"测试项目": "EQ/MC01 test", "结果": "符合"}],
        "样品描述": {},
        "_meta": {"报告编号": {"confidence": 0.99, "source_quote": "..."}},
    }
    cover, results, sample, meta = ReportExtractor._parse_structural(result)
    assert cover.report_number == "R001"
    assert cover.sample_name == "Test"
    assert len(results) == 1
    assert results[0].test_item == "EQ/MC01 test"
    assert results[0].result == "符合"
    assert "报告编号" in meta


def test_parse_instruments():
    result = {
        "测试仪器": [
            {"测试项目": "EQ/MC01", "仪器名称": "Spectrum Analyzer",
             "制造商": "R&S", "型号": "FSV", "系列号": "123", "校准有效期": "2026-12-31"},
        ],
        "_meta": {"仪器名称": {"confidence": 0.95, "source_quote": "..."}},
    }
    instruments, meta = ReportExtractor._parse_instruments(result)
    assert len(instruments) == 1
    assert instruments[0].name == "Spectrum Analyzer"
    assert instruments[0].manufacturer == "R&S"


def test_parse_data_rows():
    result = {
        "测试数据": [
            {"测试项目": "EQ/MC01", "频率(MHz)": "0.15", "读值(dBuA)": "30.0",
             "修正因子(dB)": "8.5", "结果(dBuA)": "38.5", "限值(dBuA)": "66",
             "余量(dB)": "27.5", "备注": ""},
        ]
    }
    rows = ReportExtractor._parse_data_rows(result)
    assert len(rows) == 1
    assert rows[0].test_item == "EQ/MC01"
    assert rows[0].freq_mhz == "0.15"
    assert rows[0].reading == "30.0"


# ── Aggregation tests ──────────────────────────────────────────────────

def test_aggregate_combines_all_passes():
    cover = ReportCoverInfo(report_number="R1")
    results = [ReportResultItem(test_item="EQ/MC01")]
    sample = ReportSampleInfo(sample_name="S1")
    instruments = [ReportInstrument(name="Inst1")]
    data_rows = [ReportDataRow(test_item="EQ/MC01", freq_mhz="0.15")]
    toc = [ReportTocItem(code="EQ/MC01", name="test", page_start=3)]
    struct_meta = {"key1": {"confidence": 0.9}}
    instr_meta = {"key2": {"confidence": 0.8}}

    data = ReportExtractor._aggregate(
        cover, results, sample, instruments, data_rows, toc,
        struct_meta, instr_meta, raw_text="raw",
    )
    assert data.cover.report_number == "R1"
    assert len(data.results) == 1
    assert len(data.instruments) == 1
    assert len(data.data_rows) == 1
    assert len(data.toc) == 1
    # merged meta: struct_meta takes precedence
    assert "key1" in data.meta
    assert "key2" in data.meta
    assert data.raw_text == "raw"


def test_aggregate_deduplicates():
    """Aggregation should deduplicate data rows."""
    rows = [
        ReportDataRow(test_item="EQ/MC01", freq_mhz="0.15", reading="30.0", correction_db="8.5"),
        ReportDataRow(test_item="EQ/MC01", freq_mhz="0.15", reading="30.0", correction_db="8.5"),  # dup
    ]
    data = ReportExtractor._aggregate(
        cover=ReportCoverInfo(), results=[], sample=ReportSampleInfo(),
        instruments=[], all_data_rows=rows, toc_items=[],
        struct_meta={}, instr_meta={}, raw_text="",
    )
    assert len(data.data_rows) == 1  # deduplicated


def test_docx_table_inventory_preserves_duplicate_execution_pairs():
    from docx import Document

    document = Document()
    for required, verdict in (("C", "Pass"), ("A", "不Pass")):
        identity = document.add_table(rows=2, cols=4)
        identity.rows[0].cells[0].text = "Test Mode"
        identity.rows[0].cells[1].text = "Mode 2"
        identity.rows[1].cells[0].text = "Sample No."
        identity.rows[1].cells[1].text = "E202508277046-0016"
        result = document.add_table(rows=2, cols=7)
        headers = [
            "Test item", "Injection position", "Test specification requirements",
            "Time", "Performance criteria", "Actual performance", "Result",
        ]
        values = ["Reversed voltage", "power supply", "14V", "60s", required, "C", verdict]
        for index, value in enumerate(headers):
            result.rows[0].cells[index].text = value
        for index, value in enumerate(values):
            result.rows[1].cells[index].text = value
    stream = BytesIO()
    document.save(stream)

    items, metrics = ReportExtractor._extract_docx_table_items(
        stream.getvalue(), [ReportResultItem(test_item="Reversed voltage")],
    )

    assert metrics["docx_execution_block_count"] == 2
    assert metrics["docx_execution_pair_count"] == 1
    assert metrics["docx_duplicate_execution_pair_count"] == 1
    assert len(items) == 1
    assert [block.data_rows[0].verdict for block in items[0].test_results.sample_data] == [
        "Pass", "不Pass",
    ]


def test_docx_table_inventory_accepts_plain_chinese_result_header():
    from docx import Document

    document = Document()
    identity = document.add_table(rows=2, cols=4)
    identity.rows[0].cells[0].text = "测试模式"
    identity.rows[0].cells[1].text = "模式 3.2"
    identity.rows[1].cells[0].text = "样品编号"
    identity.rows[1].cells[1].text = "E202603209520-0001"
    result = document.add_table(rows=2, cols=7)
    headers = [
        "测试项目", "注入位置", "测试规范", "测试时间",
        "要求的性能等级", "实际性能等级", "结果",
    ]
    values = [
        "电源电压瞬间降低试验", "电源线", "下降到 4.5V", "1 个循环",
        "A", "A", "符合",
    ]
    for index, value in enumerate(headers):
        result.rows[0].cells[index].text = value
    for index, value in enumerate(values):
        result.rows[1].cells[index].text = value
    stream = BytesIO()
    document.save(stream)

    items, metrics = ReportExtractor._extract_docx_table_items(
        stream.getvalue(), [ReportResultItem(test_item="电源电压瞬间降低试验")],
    )

    assert metrics["docx_execution_block_count"] == 1
    assert items[0].test_results.sample_data[0].data_rows[0].verdict == "符合"


def test_docx_instrument_inventory_preserves_group_and_equal_adjacent_values():
    from docx import Document

    document = Document()
    table = document.add_table(rows=4, cols=5)
    for index, value in enumerate([
        "Name of Equipment", "Manufacturer", "Model", "Serial Number",
        "Calibration Due",
    ]):
        table.rows[0].cells[index].text = value
    table.rows[1].cells[0].merge(table.rows[1].cells[-1]).text = "Reversed voltage"
    for index, value in enumerate([
        "Power Supply", "KIKUSUI", "PBZ40-10", "YL003705", "2026-10-22",
    ]):
        table.rows[2].cells[index].text = value
    for index, value in enumerate([
        "WavyPBZ", "KIKUSUI", "/", "6.0.0", "/",
    ]):
        table.rows[3].cells[index].text = value
    stream = BytesIO()
    document.save(stream)

    instruments, metrics = ReportExtractor._extract_docx_instruments(
        stream.getvalue(),
    )

    assert len(instruments) == 2
    assert instruments[0].test_item == "Reversed voltage"
    assert instruments[0].serial_no == "YL003705"
    assert metrics == {
        "docx_instrument_row_count": 2,
        "docx_physical_instrument_row_count": 1,
    }


def test_docx_instrument_inventory_accepts_chinese_series_number_header():
    from docx import Document

    document = Document()
    table = document.add_table(rows=3, cols=5)
    for index, value in enumerate([
        "仪器名称", "制造商", "型号", "系列号", "校准有效期",
    ]):
        table.rows[0].cells[index].text = value
    table.rows[1].cells[0].merge(table.rows[1].cells[-1]).text = "电压波动试验"
    for index, value in enumerate([
        "双极性电源", "KIKUSUI", "PBZ40-10", "VM001868", "2026-10-22",
    ]):
        table.rows[2].cells[index].text = value
    stream = BytesIO()
    document.save(stream)

    instruments, metrics = ReportExtractor._extract_docx_instruments(stream.getvalue())

    assert len(instruments) == 1
    assert instruments[0].serial_no == "VM001868"
    assert metrics["docx_physical_instrument_row_count"] == 1


# ── build_data_rows_prompt tests ───────────────────────────────────────

def test_build_data_rows_prompt():
    from services.prompts import build_data_rows_prompt
    prompt = build_data_rows_prompt(["EQ/MC01", "EQ/MC03", "EQ/IC01"])
    assert "EQ/MC01" in prompt
    assert "EQ/MC03" in prompt
    assert "EQ/IC01" in prompt
    assert "★ 目标测试项编码" in prompt
    assert "不要提取封面" in prompt
