"""Tests for the order form extractor (GRGT .xls parser).

Uses the real sample file: S596B(Z400259466) PV EMC测试申请表(20260420).xls
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import xlrd

from models import OrderFormData
from services.order_form_extractor import (
    OrderFormExtractor,
    _build_merge_map,
    _clean_placeholder,
    _normalize,
    TEMPLATE_MARKER,
)


# ── Fixtures ───────────────────────────────────────────────────────────

SAMPLE_PATH = Path(__file__).parent / "data" / "S596B_sample.xls"


@pytest.fixture(scope="module")
def sample_bytes() -> bytes:
    if not SAMPLE_PATH.exists():
        pytest.skip(f"Sample file not found: {SAMPLE_PATH}")
    return SAMPLE_PATH.read_bytes()


@pytest.fixture(scope="module")
def extracted(sample_bytes: bytes) -> OrderFormData:
    return asyncio.run(OrderFormExtractor.extract(sample_bytes))


# ── Unit: helpers ──────────────────────────────────────────────────────

def test_normalize_nbsp():
    assert _normalize("foo\xa0bar") == "foo bar"


def test_phone_float_to_string(sample_bytes: bytes):
    """Phone stored as float → string without decimal."""
    wb = xlrd.open_workbook(file_contents=sample_bytes)
    sheet = wb.sheet_by_index(0)
    # Row 14 col 6 = phone 18027082627.0
    val = sheet.cell(14, 6).value
    assert isinstance(val, float)
    assert str(int(val)) == "18027082627"


def test_phone_float_to_string(sample_bytes: bytes):
    """Phone stored as float -> string without decimal."""
    wb = xlrd.open_workbook(file_contents=sample_bytes)
    sheet = wb.sheet_by_index(0)
    # Row 14 col 6 = phone 18027082627.0
    val = sheet.cell(14, 6).value
    assert isinstance(val, float)
    assert str(int(val)) == "18027082627"


def test_merge_map_resolves(sample_bytes: bytes):
    """Merged cells only available with formatting_info=True."""
    wb = xlrd.open_workbook(file_contents=sample_bytes, formatting_info=True)
    sheet = wb.sheet_by_index(0)
    mm = _build_merge_map(sheet)
    # Row 1 is a full-width merge cols 0-6
    assert len(mm) > 0, "Expected merged cells in this workbook"
    assert (1, 0) in mm
    rlo, clo = mm[(1, 3)]
    assert rlo == 1 and clo == 0


def test_clean_placeholder_with_value():
    raw = ("(According to the standards listed in quotation sheet if not filled.\n"
           "EQCS-1204-2023  东风EMC标准  如不填写，按报价单所列标准执行)")
    result = _clean_placeholder(raw)
    assert "EQCS-1204-2023" in result
    assert "东风EMC标准" in result
    assert "According to" not in result
    assert "如不填写" not in result


def test_clean_placeholder_pure_boilerplate():
    raw = ("(According to the items listed in quotation sheet if not filled.\n"
           "如不填写，按报价单所列项目执行)")
    assert _clean_placeholder(raw) == ""


def test_clean_placeholder_no_placeholder():
    assert _clean_placeholder("GB/T 18655-2018") == "GB/T 18655-2018"


# ── Unit: can_handle / extract invalid ─────────────────────────────────

def test_can_handle_valid(sample_bytes: bytes):
    assert OrderFormExtractor.can_handle(sample_bytes) is True


def test_can_handle_invalid():
    assert OrderFormExtractor.can_handle(b"not an excel file") is False


def test_extract_invalid_raises():
    with pytest.raises(ValueError, match="无法解析 Excel 文件|不支持的委托单模板"):
        asyncio.run(OrderFormExtractor.extract(b"\x00" * 100))


# ── Integration: applicant ─────────────────────────────────────────────

def test_applicant_name_cn(extracted: OrderFormData):
    assert "新阳荣乐" in extracted.applicant.name_cn
    assert "上海" in extracted.applicant.address_cn


def test_applicant_contact(extracted: OrderFormData):
    c = extracted.applicant.contact
    assert c.name == "孙维亮"
    assert "sunweiliang" in c.email
    assert c.phone == "18027082627"


# ── Integration: manufacturer is empty ─────────────────────────────────

def test_manufacturer_empty(extracted: OrderFormData):
    m = extracted.manufacturer
    assert m.name_cn == ""
    assert m.contact.name == ""


# ── Integration: factory ───────────────────────────────────────────────

def test_factory_name_cn(extracted: OrderFormData):
    assert "东莞技研新阳电子" in extracted.factory.name_cn
    assert "东莞市桥头镇" in extracted.factory.address_cn


def test_factory_contact(extracted: OrderFormData):
    c = extracted.factory.contact
    assert c.name == "孙维亮"
    assert c.phone == "18027082627"


# ── Integration: product ───────────────────────────────────────────────

def test_product_name(extracted: OrderFormData):
    assert extracted.product.name == "组合仪表显示屏"


def test_product_part_number(extracted: OrderFormData):
    assert extracted.product.part_number == "Z400259466"


def test_product_voltage(extracted: OrderFormData):
    assert "DC 12.0V" in extracted.product.voltage


# ── Integration: test requirements ─────────────────────────────────────

def test_test_specification_cleaned(extracted: OrderFormData):
    spec = extracted.test_requirements.test_specification
    assert "EQCS-1204-2023" in spec
    assert "东风EMC" in spec
    assert "According to" not in spec
    assert "如不填写" not in spec


def test_report_qualification(extracted: OrderFormData):
    assert extracted.test_requirements.report_qualification == "东风EMC资质"


# ── Integration: other info ────────────────────────────────────────────

def test_software_version(extracted: OrderFormData):
    assert extracted.other_info.software_version == "V1.0.00"


def test_hardware_version(extracted: OrderFormData):
    assert extracted.other_info.hardware_version == "V1.0.00"


def test_application_date(extracted: OrderFormData):
    assert extracted.other_info.application_date == "20260420"


# ── Integration: template ──────────────────────────────────────────────

def test_template_id(extracted: OrderFormData):
    assert TEMPLATE_MARKER in extracted.template_id


# ── Integration: serialization ─────────────────────────────────────────

def test_model_dump_structure(extracted: OrderFormData):
    d = extracted.model_dump()
    assert isinstance(d, dict)
    for key in ["applicant", "manufacturer", "factory", "product",
                "test_requirements", "other_info", "raw_notes"]:
        assert key in d, f"Missing key: {key}"
    assert len(d["raw_notes"]) >= 3


def test_all_phones_are_strings(extracted: OrderFormData):
    for cname in ["applicant", "manufacturer", "factory"]:
        c = getattr(extracted, cname).contact
        assert isinstance(c.phone, str), f"{cname}.phone is not str"
