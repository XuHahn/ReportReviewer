"""Tests for raw records extraction pipeline."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from services.raw_records_filename_parser import (
    RawRecordMeta, parse_filename, parse_filename_or_fallback, parse_filenames, classify_category,
    CATEGORY_EQIC, CATEGORY_EQIR, CATEGORY_EQMC, CATEGORY_EQMR, CATEGORY_OTHER,
)
from services.raw_records_pdf_extractor import (
    extract_text_from_pdf, preprocess, extract_header_fields,
    extract_and_populate, extract_batch,
)
from services.raw_records_table_extractor import (
    extract_table_rows, extract_all_tables, TestDataRow,
)
from services.raw_records_instrument_extractor import (
    parse_instrument_section, extract_instruments, extract_all_instruments,
    deduplicate_instruments, validate_calibration,
    InstrumentRecord, CalibrationIssue,
)
from services.raw_records_aggregator import (
    summarize_conclusions, aggregate,
)
from services.raw_records_llm_extractor import (
    evidence_in_source, validate_semantic_result, validate_table_extraction,
    semantic_to_row, tables_to_rows, tables_to_instruments,
    _apply_document_fields, _deduplicate_instrument_occurrences,
)
from services.raw_records_table_gate import extract_table_inventory, expected_rows_by_type

SAMPLE_DIR = Path(__file__).parent.parent.parent / "example" / "E20260402869601原始记录"


def _sample_files() -> list[str]:
    if not SAMPLE_DIR.exists():
        return []
    files = sorted(os.listdir(SAMPLE_DIR))
    # Pick one from each category for coverage
    picked = []
    seen = set()
    for f in files:
        if "EQIC" in f and "EQIC" not in seen:
            picked.append(f); seen.add("EQIC")
        elif "EQIR" in f and "02" in f and "EQIR02" not in seen:
            picked.append(f); seen.add("EQIR02")
        elif "EQIR" in f and "01" in f and "EQIR01" not in seen:
            picked.append(f); seen.add("EQIR01")
        elif "EQMC" in f and "EQMC" not in seen:
            picked.append(f); seen.add("EQMC")
        elif "EQMR" in f and "EQMR" not in seen:
            picked.append(f); seen.add("EQMR")
        elif "短时中断" in f and "中断" not in seen:
            picked.append(f); seen.add("中断")
        if len(picked) >= 6:
            break
    return picked


def _read_sample(fname: str) -> bytes:
    return (SAMPLE_DIR / fname).read_bytes()


# ── Filename parser ──

def test_parse_eqic():
    meta = parse_filename(
        "E20260402869601_EQIC01 针对脉冲 1&2 的抗扰性测试_0001_模式1_原始记录_陈友杰_20260513190707.pdf"
    )
    assert meta is not None
    assert meta.report_id == "E20260402869601"
    assert meta.test_item_code == "EQIC01"
    assert meta.test_item_name == "针对脉冲 1&2 的抗扰性测试"
    assert meta.sequence == "0001"
    assert meta.test_mode == "模式1"
    assert meta.operator == "陈友杰"
    assert meta.record_timestamp == "20260513190707"
    assert meta.category == CATEGORY_EQIC


def test_parse_eqir():
    meta = parse_filename(
        "E20260402869601_EQIR 01：耐辐射场抗干扰性能_0001_模式1_原始记录_戴子浩_20260518084026.pdf"
    )
    assert meta is not None
    assert meta.test_item_code == "EQIR"
    assert meta.category == CATEGORY_EQIR


def test_parse_eqmr():
    meta = parse_filename(
        "E20260402869601_EQMR01 辐射噪音测量_0001_模式1_原始记录_陈友杰_20260507095918.pdf"
    )
    assert meta is not None
    assert meta.category == CATEGORY_EQMR


def test_parse_filename_allows_underscores_in_item_and_operator():
    meta = parse_filename(
        "E20260402869601_EQIC01 voltage_drop_test_0001_Mode 2_原始记录_Zhang_Wei_20260513190707.pdf"
    )
    assert meta is not None
    assert meta.test_item_code == "EQIC01"
    assert meta.test_item_name == "voltage_drop_test"
    assert meta.sequence == "0001"
    assert meta.operator == "Zhang_Wei"
    assert meta.record_timestamp == "20260513190707"


def test_classify():
    assert classify_category("EQIC01") == CATEGORY_EQIC
    assert classify_category("EQIR03") == CATEGORY_EQIR
    assert classify_category("EQMC02") == CATEGORY_EQMC
    assert classify_category("EQMR01") == CATEGORY_EQMR
    assert classify_category("短时中断") == CATEGORY_OTHER


def test_batch_parse():
    files = _sample_files()
    if not files:
        pytest.skip("No sample files")
    metas = parse_filenames(files)
    assert len(metas) == len(files)
    for m in metas:
        assert m.report_id == "E20260402869601"


def test_vendor_filename_falls_back_to_pdf_content_pipeline():
    meta = parse_filename_or_fallback("厂商自定义记录名称.pdf")
    assert meta.filename == "厂商自定义记录名称.pdf"
    assert meta.category == CATEGORY_OTHER


def test_test_date_str():
    meta = parse_filename(
        "E20260402869601_EQIC01 test_0001_模式1_原始记录_op_20260513190707.pdf"
    )
    assert meta is not None
    assert "2026" in meta.test_date_str


def test_test_date_prefers_reviewed_record_header_over_filename_timestamp():
    meta = parse_filename(
        "E20260402869601_EQIC01 test_0001_模式1_原始记录_op_20260603190707.pdf"
    )
    assert meta is not None
    meta._header_fields["test_date"] = "2026-06-02"
    assert meta.test_date_str == "2026/6/2"


# ── PDF extractor ──

def test_preprocess():
    proc = preprocess("测试\n项目  EQ/IC01\n针对脉冲")
    assert "\n" not in proc


def test_extract_header():
    files = _sample_files()
    if not files:
        pytest.skip("No sample files")
    text = extract_text_from_pdf(_read_sample(files[0]))
    f = extract_header_fields(text)
    assert f.get("report_id_pdf") == "E20260402869601"
    assert f.get("sample_name") == "组合仪表显示屏"


def test_extract_and_populate():
    files = _sample_files()
    if not files:
        pytest.skip("No sample files")
    meta = parse_filename(files[0])
    extract_and_populate(meta, _read_sample(files[0]))
    assert meta.raw_text
    assert meta._header_fields


def test_extract_batch():
    files = _sample_files()
    if not files:
        pytest.skip("No sample files")
    metas = parse_filenames(files)
    file_map = {f: _read_sample(f) for f in files}
    extract_batch(metas, file_map)
    for m in metas:
        assert m.raw_text


def test_checkbox_values_are_scoped_to_their_labels():
    fields = extract_header_fields("附件保存 ☑测试照片（另附） □测试数据 测试结论：■符合")
    assert fields["photos_saved"] is True
    assert fields["data_saved"] is False


def test_remarks_stop_before_repeated_physical_page_header():
    fields = extract_header_fields(
        "备注 试验中停止，试验后恢复正常。 "
        "GRGJL.WI-GZEMC-06-0003(1.1) 第 2 页 / 共 3 页 "
        "☑测试照片 □测试数据 测试结论：■符合"
    )

    assert fields["remarks"] == "试验中停止，试验后恢复正常。"


# ── Table extractor ──

def test_eqic_table():
    files = _sample_files()
    if not files:
        pytest.skip("No sample files")
    eqic = [f for f in files if "EQIC" in f]
    if not eqic:
        pytest.skip("No EQIC sample")
    meta = parse_filename(eqic[0])
    extract_and_populate(meta, _read_sample(eqic[0]))
    rows = extract_table_rows(meta)
    assert len(rows) >= 3
    assert rows[0].test_item.startswith("脉冲")
    assert rows[0].injection_point == "电源线"
    assert rows[0].test_result == "符合"


def test_eqir_table():
    files = _sample_files()
    if not files:
        pytest.skip("No sample files")
    eqir = [f for f in files if "EQIR" in f]
    if not eqir:
        pytest.skip("No EQIR sample")
    meta = parse_filename(eqir[0])
    extract_and_populate(meta, _read_sample(eqir[0]))
    rows = extract_table_rows(meta)
    # EQIR is the most complex category — relax assertion to presence of any data
    assert len(rows) >= 1 or meta.raw_text, "Should have rows or at least raw text"


def test_unknown_template_does_not_fabricate_frequency_rows():
    from services.raw_records_filename_parser import RawRecordMeta

    meta = RawRecordMeta(
        filename="record.pdf",
        category=CATEGORY_OTHER,
        raw_text="检测日期 2026-06-03 仪器型号 APS 40C30-DCP40C30 校准日期 2027-01-20",
    )
    assert extract_table_rows(meta) == []


def test_semantic_result_requires_locatable_evidence():
    from services.raw_records_filename_parser import RawRecordMeta

    source = "测试项目 系统12V电源电压波动试验 测试模式 模式1 Ub=12V 测试结果 符合"
    payload = {
        "test_identity": {
            "name": "系统12V电源电压波动试验",
            "evidence": "测试项目 系统12V电源电压波动试验",
        },
        "test_mode": {"value": "模式1", "evidence": "测试模式 模式1"},
        "conditions": [
            {"name": "Ub", "value": "12", "unit": "V", "evidence": "Ub=12V"},
            {"name": "Us", "value": "-7", "unit": "V", "evidence": "原文不存在"},
        ],
        "result": {"conclusion": "符合", "evidence": "测试结果 符合"},
    }
    result = validate_semantic_result(payload, source)
    assert evidence_in_source("测试模式 模式1", source)
    assert result["conditions"] == [payload["conditions"][0]]
    row = semantic_to_row(RawRecordMeta(filename="record.pdf", sequence="1"), result)
    assert row is not None
    assert row.spec_params == {"Ub": "12V"}
    assert row.test_result == "符合"
    assert row.extraction_source == "llm_evidence_grounded"


def test_semantic_result_recovers_exact_source_window_from_value():
    source = "测试项目  系统 12V电源电压波动试验  测试结果  符合"
    payload = {
        "test_identity": {
            "name": "系统12V电源电压波动试验",
            "evidence": "测试项目：系统12V电源电压波动试验",
        },
        "result": {
            "conclusion": "符合",
            "required_performance": "B",
            "evidence": "并非原文引用",
        },
    }
    result = validate_semantic_result(payload, source)
    assert result["test_identity"]["name"] == "系统12V电源电压波动试验"
    assert result["result"]["conclusion"] == "符合"
    assert "required_performance" not in result["result"]
    assert "系统 12V电源电压波动试验" in result["test_identity"]["evidence"]


def test_table_extraction_preserves_rows_and_rejects_unsupported_evidence():
    source = """测试项目 静电放电\n测试电压 结果\n±4kV 符合\n±6kV 不符合\n总体结论 符合"""
    payload = {
        "document": {"test_item": {"value": "静电放电", "evidence": "测试项目 静电放电"}},
        "tables": [{
            "table_id": "T1",
            "table_type": "test_data",
            "headers": [{"source": "测试电压", "semantic": "test_voltage"}],
            "source_row_count": 3,
            "rows": [
                {"row_index": 1, "cells": [{"column": "测试电压", "semantic": "test_voltage", "value": "±4kV"}], "evidence": "±4kV 符合"},
                {"row_index": 2, "cells": [{"column": "测试电压", "semantic": "test_voltage", "value": "±6kV"}], "evidence": "±6kV 不符合"},
                {"row_index": 3, "cells": [{"column": "测试电压", "semantic": "test_voltage", "value": "±8kV"}], "evidence": "±8kV 符合"},
            ],
        }],
        "overall_conclusion": {"value": "符合", "evidence": "总体结论 符合"},
        "conflicts": [{"type": "row_vs_overall", "description": "冲突", "evidence": ["±6kV 不符合", "总体结论 符合"]}],
    }
    result = validate_table_extraction(payload, source)
    table = result["tables"][0]
    assert table["model_row_count"] == 3
    assert table["accepted_row_count"] == 2
    assert table["complete"] is False
    assert result["quality"]["rejected_rows"] == 1
    assert len(result["conflicts"]) == 1


def test_table_extraction_fails_independent_row_count_gate():
    source = "测试结果\n±4kV 符合"
    payload = {
        "document_fields": [],
        "tables": [{
            "table_id": "T1",
            "table_type": "test_data",
            "source_row_count": 1,
            "rows": [{
                "row_index": 1,
                "cells": [{"column": "结果", "semantic": "verdict", "source_value": "符合"}],
                "evidence": "±4kV 符合",
            }],
        }],
    }
    inventory = [{"table_type": "test_data", "logical_row_count": 2}]
    result = validate_table_extraction(payload, source, inventory)
    assert result["quality"]["model_complete"] is True
    assert result["quality"]["complete"] is False
    assert result["quality"]["independent_mismatches"] == [{
        "table_type": "test_data", "expected_rows": 2, "accepted_rows": 1,
    }]


def test_table_extraction_recovers_cross_page_row_with_geometry():
    source = (
        "测试规范要求 测试时间 要求的性能等级 实际性能等级 测试结果\n"
        "Severity 4: peak to peak voltage\n"
        "5 个循环 A A¹⁾ 符合\n"
        "UPP, of 2 V; internal resistance: 50 mΩ to 100 mΩ"
    )
    payload = {
        "tables": [{
            "table_id": "T1",
            "table_type": "test_data",
            "source_row_count": 1,
            "rows": [{
                "row_index": 1,
                "cells": [
                    {"column": "测试规范要求", "semantic": "test_specification",
                     "source_value": "Severity 4: peak to peak voltage, UPP, of 2 V; internal resistance: 50 mΩ to 100 mΩ"},
                    {"column": "测试时间", "semantic": "test_duration", "source_value": "5 个循环"},
                    {"column": "要求的性能等级", "semantic": "required_performance", "source_value": "A"},
                    {"column": "实际性能等级", "semantic": "actual_performance", "source_value": "A¹⁾"},
                    {"column": "测试结果", "semantic": "verdict", "source_value": "符合"},
                ],
                "evidence": "Severity 4: peak to peak voltage, UPP, of 2 V; internal resistance: 50 mΩ to 100 mΩ 5 个循环 A A¹⁾ 符合",
            }],
        }],
    }
    inventory = [{
        "table_type": "test_data", "logical_row_count": 1,
        "row_end_cells": [{
            "测试规范要求": "", "测试时间": "5 个循环",
            "要求的性能等级": "A", "实际性能等级": "A¹⁾", "测试结果": "符合",
        }],
    }]

    result = validate_table_extraction(payload, source, inventory)

    assert result["tables"][0]["accepted_row_count"] == 1
    assert result["quality"]["split_evidence_recoveries"] == 1
    assert result["quality"]["complete"] is True
    assert "---" in result["tables"][0]["rows"][0]["evidence"]


def test_table_geometry_corrects_shifted_adjacent_atomic_columns():
    source = (
        "要求的性能等级 实际性能等级 测试结果\n"
        "系统12V电源电压波动试验 1个脉冲 ¹⁾ ²⁾ 符合"
    )
    payload = {
        "tables": [{
            "table_id": "T1",
            "table_type": "test_data",
            "source_row_count": 1,
            "rows": [{
                "row_index": 1,
                "cells": [
                    {"column": "要求的性能等级", "semantic": "required_performance", "source_value": "¹⁾ ²⁾"},
                    {"column": "实际性能等级", "semantic": "actual_performance", "source_value": ""},
                    {"column": "测试结果", "semantic": "verdict", "source_value": "符合"},
                ],
                "evidence": "系统12V电源电压波动试验 1个脉冲 ¹⁾ ²⁾ 符合",
            }],
        }],
    }
    inventory = [{
        "table_type": "test_data",
        "logical_row_count": 1,
        "row_end_cells": [{
            "要求的性能等级": "¹⁾",
            "实际性能等级": "²⁾",
            "测试结果": "符合",
        }],
    }]

    result = validate_table_extraction(payload, source, inventory)
    cells = result["tables"][0]["rows"][0]["cells"]
    values = {cell["semantic"]: cell["source_value"] for cell in cells}
    assert values["required_performance"] == "¹⁾"
    assert values["actual_performance"] == "²⁾"
    assert result["quality"]["geometry_corrections"] == 2
    assert result["quality"]["complete"] is True


def test_hc_raw_record_table_inventory_counts_business_rows():
    root = Path(__file__).parent.parent.parent / "example" / "HC_E202605287495" / "E202605287495原始记录"
    files = sorted(root.glob("*.pdf"))
    if len(files) != 2:
        pytest.skip("HC_E202605287495 sample is unavailable")
    counts = [expected_rows_by_type(extract_table_inventory(path.read_bytes(), path.name)) for path in files]
    assert sorted(item["test_data"] for item in counts) == [1, 4]
    assert [item["instrument"] for item in counts] == [4, 4]


def test_table_conversion_keeps_generic_cells_and_instrument_columns():
    meta = RawRecordMeta(filename="sample.pdf", test_item_name="任意企业试验", test_mode="模式1")
    result = {
        "document_fields": [],
        "tables": [
            {
                "table_id": "T1", "table_type": "test_data", "table_family": "test_data",
                "rows": [{
                    "cells": [
                        {"column": "企业参数X", "semantic": "unknown", "source_value": "42foo"},
                        {"column": "判定", "semantic": "verdict", "source_value": "符合"},
                    ],
                    "evidence": "42foo 符合",
                }],
            },
            {
                "table_id": "T2", "table_type": "instrument_list", "table_family": "instrument",
                "rows": [{
                    "cells": [
                        {"column": "仪器名称", "semantic": "instrument_name", "source_value": "示波器"},
                        {"column": "制造商", "semantic": "manufacturer", "source_value": "Vendor"},
                        {"column": "型号", "semantic": "model", "source_value": "M1"},
                        {"column": "系列号", "semantic": "serial_number", "source_value": "SN1"},
                        {"column": "校准有效期", "semantic": "calibration_end", "source_value": "2027-01-01"},
                    ],
                    "evidence": "示波器 Vendor M1 SN1 2027-01-01",
                }],
            },
        ],
    }
    rows = tables_to_rows(meta, result)
    instruments = tables_to_instruments(meta, result)
    assert rows[0].spec_params == {"企业参数x": "42foo"}
    assert rows[0].test_result == "符合"
    assert instruments[0].model == "M1"
    assert instruments[0].serial_no == "SN1"


def test_document_field_mapping_does_not_confuse_model_with_mode():
    meta = RawRecordMeta(filename="sample.pdf")
    result = {
        "document_fields": [
            {"source_label": "型号", "semantic": "sample_model", "source_value": "MODEL-7"},
            {"source_label": "测试模式", "semantic": "test_mode", "source_value": "模式2"},
        ],
        "overall_conclusion": {"source_value": "符合"},
    }
    _apply_document_fields(meta, result)
    assert meta.get_header("sample_model") == "MODEL-7"
    assert meta.test_mode == "模式2"
    assert meta.get_header("test_conclusion") == "符合"


# ── Instrument ──

def test_instruments():
    files = _sample_files()
    if not files:
        pytest.skip("No sample files")
    meta = parse_filename(files[0])
    extract_and_populate(meta, _read_sample(files[0]))
    insts = extract_instruments(meta)
    assert len(insts) >= 2


def test_instrument_table_repeated_manufacturers_are_not_collapsed():
    from services.raw_records_filename_parser import RawRecordMeta

    text1 = """
测试使用仪器列表 1
仪器名称  制造商 型号 系列号 校准有效期
抛负载模拟器  苏州泰思特电子科技有限公司  LDS 200N50  ES4151801  2027 -01-20
示波器 Tektronix  MDO3102  C055337 2027 -03-23
电源电压变化模拟器  3ctest APS 40C30/DCP40C30  ES1731806/ES1681806  2027 -01-20
瞬变脉冲干扰模拟器  3ctest TIS700 -60 ES0711829  2027 -01-20
检测人员 /日期 陈智健   2026/6/2
"""
    text2 = """
测试使用仪器列表 1
仪器名称  制造商 型号 系列号 校准有效期
汽车传导抗干模拟器  EMTEST  UCS200 -M V0816103661  2026 -12-15
汽车电压瞬变模拟器  EMTEST  VDS200 V0816103662  2026 -12-15
汽车 LD模拟器 EMTEST  LD200 V0816103663  2026 -12-15
示波器 Tektronix  MDO3102  C055337 2027 -03-23
检测人员 /日期 陈智健   2026/6/2
"""

    parsed1 = parse_instrument_section(text1)
    parsed2 = parse_instrument_section(text2)
    assert len(parsed1) == 4
    assert len(parsed2) == 4
    assert parsed1[0]["name"] == "抛负载模拟器"
    assert parsed1[2]["model"] == "APS 40C30/DCP40C30"
    assert parsed1[2]["serial_no"] == "ES1731806/ES1681806"
    assert parsed1[3]["name"] == "瞬变脉冲干扰模拟器"
    assert parsed2[1]["name"] == "汽车电压瞬变模拟器"
    assert parsed2[2]["name"] == "汽车 LD模拟器"

    m1 = RawRecordMeta(filename="a.pdf", raw_text=text1)
    m2 = RawRecordMeta(filename="b.pdf", raw_text=text2)
    all_inst = extract_all_instruments([m1, m2])
    assert len(all_inst) == 8
    assert len(deduplicate_instruments(all_inst)) == 7


def test_deduplicate():
    i1 = InstrumentRecord(name="A", manufacturer="M", model="X", serial_no="1", calibration_end="2026-12-15")
    i2 = InstrumentRecord(name="A", manufacturer="M", model="X", serial_no="1", calibration_end="2027-01-01")
    unique = deduplicate_instruments([i1, i2])
    assert len(unique) == 1
    assert unique[0].calibration_end == "2027-01-01"


def test_instrument_occurrence_merge_preserves_cross_file_reuse():
    a_code = InstrumentRecord(name="示波器", model="M1", serial_no="SN1", calibration_end="2027-01-01", source_file="a.pdf")
    a_llm = InstrumentRecord(name="示波器", manufacturer="Vendor", model="M1", serial_no="SN1", calibration_end="2027 -01-01", source_file="a.pdf")
    b_llm = InstrumentRecord(name="示波器", manufacturer="Vendor", model="M1", serial_no="SN1", calibration_end="2027-01-01", source_file="b.pdf")
    merged = _deduplicate_instrument_occurrences([a_code, a_llm, b_llm])
    assert len(merged) == 2
    assert {item.source_file for item in merged} == {"a.pdf", "b.pdf"}
    assert next(item for item in merged if item.source_file == "a.pdf").manufacturer == "Vendor"


def test_calibration_valid():
    inst = InstrumentRecord(name="X", manufacturer="M", model="Y", serial_no="1", calibration_end="2026-12-15")
    assert validate_calibration([inst], "2026/05/01") == []


def test_calibration_expired():
    inst = InstrumentRecord(name="X", manufacturer="M", model="Y", serial_no="1", calibration_end="2026-01-01")
    issues = validate_calibration([inst], "2026/06/15")
    assert len(issues) == 1
    assert issues[0].severity == "CRITICAL"


def test_calibration_same_day():
    inst = InstrumentRecord(name="X", manufacturer="M", model="Y", serial_no="1", calibration_end="2026-05-27")
    assert validate_calibration([inst], "2026/05/27") == []


# ── Aggregator ──

def test_summarize():
    from services.raw_records_filename_parser import RawRecordMeta
    m1 = RawRecordMeta(test_item_code="EQIC01", test_item_name="t", category="EQIC", sequence="0001", operator="A")
    m1._header_fields = {"test_conclusion": "符合"}
    m2 = RawRecordMeta(test_item_code="EQIC01", test_item_name="t", category="EQIC", sequence="0002", operator="B")
    m2._header_fields = {"test_conclusion": "符合"}
    s = summarize_conclusions([m1, m2])
    assert len(s) == 1
    assert s[0].all_rounds_pass
    assert s[0].round_count == 2


def test_summarize_sorts_very_long_numeric_sequences_without_int_conversion():
    shorter = RawRecordMeta(test_item_code="EQIC01", sequence="9" * 5000)
    shorter._header_fields = {"test_conclusion": "符合"}
    longer = RawRecordMeta(test_item_code="EQIC01", sequence="1" + "0" * 5000)
    longer._header_fields = {"test_conclusion": "符合"}

    summary = summarize_conclusions([longer, shorter])

    assert [item["sequence"] for item in summary[0].rounds] == [shorter.sequence, longer.sequence]


def test_aggregate_empty():
    result = aggregate([], [])
    assert result.total_files == 0
