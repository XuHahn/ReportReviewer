"""Deterministic parser for GRGT order forms (.xls).

All GRGT order forms follow the same template: GRGJL.WI-EMC-11-117.
This module extracts structured data from the Excel file using the known
template layout — no AI/LLM calls, fully deterministic.

Template sections:
  Row  0-1:  Template ID + title
  Row  2-6:  Applicant info
  Row  7-11: Manufacturer info (may be empty)
  Row 12-16: Factory info
  Row 17-22: Product info
  Row 23-30: Test requirements
  Row 31-33: Other special requirements
  Row 34-37: Legal notes (not parsed)
"""

from __future__ import annotations

import io
import re
import time
from typing import Any

import xlrd
from xlrd import XLRDError

from utils.logger import get_logger

logger = get_logger(__name__)

from models import (
    OrderFormCompany,
    OrderFormContact,
    OrderFormData,
    OrderFormOtherInfo,
    OrderFormProduct,
    OrderFormTestRequirements,
)

# ── Template detection ─────────────────────────────────────────────────

TEMPLATE_MARKER = "GRGJL.WI-EMC-11-117"


# ── Helpers ────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Strip non-breaking spaces and normalize whitespace."""
    return text.replace("\xa0", " ").replace("\r", "\n").strip()


def _cell_text(sheet: xlrd.sheet.Sheet, merge_map: dict,
               row: int, col: int) -> str:
    """Read a cell as string, resolving through the merge map."""
    r, c = merge_map.get((row, col), (row, col))
    val = sheet.cell(r, c).value
    if val is None:
        return ""
    if isinstance(val, float) and val == int(val):
        return str(int(val))
    return str(val).strip()


def _cell_raw(sheet: xlrd.sheet.Sheet, merge_map: dict,
              row: int, col: int) -> Any:
    """Read a cell raw value (for type inspection)."""
    r, c = merge_map.get((row, col), (row, col))
    return sheet.cell(r, c).value


def _build_merge_map(
    sheet: xlrd.sheet.Sheet,
) -> dict[tuple[int, int], tuple[int, int]]:
    """Map every cell coordinate in a merged range to its top-left anchor.

    xlrd reports merged cells as (rlo, rhi, clo, chi) with half-open
    intervals [rlo, rhi) x [clo, chi).  Only the top-left cell (rlo, clo)
    carries the value; all others are empty.
    """
    mm: dict[tuple[int, int], tuple[int, int]] = {}
    for rlo, rhi, clo, chi in sheet.merged_cells:
        for r in range(rlo, rhi):
            for c in range(clo, chi):
                mm[(r, c)] = (rlo, clo)
    return mm


# ── Section extractors ─────────────────────────────────────────────────

def _extract_contact(sheet: xlrd.sheet.Sheet, merge_map: dict,
                     section_start_row: int) -> OrderFormContact:
    """Extract contact person info from a company section.

    Contact info sits in columns F-G (5-6).  Some rows have explicit
    labels ("Contact Person联系人", "Email 电邮", "Tel 电话") in col F;
    others (notably in the applicant section) leave col F empty and place
    values in col G by positional convention.

    Positional offsets relative to section_start_row:
      offset 0:  contact name
      offset 1:  email
      offset 2:  phone
    """
    contact = OrderFormContact()

    for r in range(section_start_row, section_start_row + 5):
        f_text = _cell_text(sheet, merge_map, r, 5)
        g_text = _cell_text(sheet, merge_map, r, 6)
        if not f_text and not g_text:
            continue
        f_norm = _normalize(f_text)

        if "Person" in f_norm or "联系人" in f_norm:
            contact.name = g_text.strip()
        elif "Email" in f_norm or "电邮" in f_norm:
            contact.email = g_text.strip()
        elif "Tel" in f_norm or "电话" in f_norm:
            raw = _cell_raw(sheet, merge_map, r, 6)
            if isinstance(raw, float) and raw == int(raw) and raw > 0:
                contact.phone = str(int(raw))
            else:
                contact.phone = str(raw).strip()
        elif g_text.strip():
            # Col F has no label, but col G has a value.
            # Use row-position heuristics relative to section_start_row.
            offset = r - section_start_row
            if offset == 1:
                # Typically email (row after the header row)
                contact.email = g_text.strip()
            elif offset == 2:
                raw = _cell_raw(sheet, merge_map, r, 6)
                if isinstance(raw, float) and raw == int(raw) and raw > 0:
                    contact.phone = str(int(raw))
                else:
                    contact.phone = str(raw).strip()

    return contact


def _extract_company(sheet: xlrd.sheet.Sheet, merge_map: dict,
                     start_row: int) -> OrderFormCompany:
    """Extract one company section (applicant / manufacturer / factory).

    Each section spans 5 rows (start_row .. start_row+4).
    Labels in col A (often A-B merged), values in cols C-E or C-G.
    """
    company = OrderFormCompany()

    for r in range(start_row, start_row + 5):
        a_text = _cell_text(sheet, merge_map, r, 0)
        a_norm = _normalize(a_text)

        if r == start_row:
            # Section header row — skip label parsing;
            # contact person sits in cols F-G.
            company.contact = _extract_contact(sheet, merge_map, start_row)
            continue

        if a_norm.startswith("Name:") or a_norm.startswith("Name："):
            company.name_en = _cell_text(sheet, merge_map, r, 2).strip()

        elif a_norm.startswith("Add:") or a_norm.startswith("Add："):
            company.address_en = _cell_text(sheet, merge_map, r, 2).strip()

        elif a_norm.startswith("名称："):
            # Chinese name — cols C-G merged
            company.name_cn = _cell_text(sheet, merge_map, r, 2).strip()

        elif a_norm.startswith("地址："):
            # Chinese address — cols C-G merged
            company.address_cn = _cell_text(sheet, merge_map, r, 2).strip()

    return company


def _extract_product(sheet: xlrd.sheet.Sheet, merge_map: dict) -> OrderFormProduct:
    """Extract product info from rows 17-22."""
    product = OrderFormProduct()

    # Row 17: labels "Product description:" / "Main Test Model:"
    # Row 18: 产品名 in col B (cols B-C merged), 主测型号 in cols E-G merged
    product.name = _cell_text(sheet, merge_map, 18, 1).strip()
    product.main_test_model = _cell_text(sheet, merge_map, 18, 5).strip()

    # Row 20: 零件号 in col B (cols B-C merged), 商标 in cols E-G merged
    product.part_number = _cell_text(sheet, merge_map, 20, 1).strip()
    product.trademark = _cell_text(sheet, merge_map, 20, 5).strip()

    # Row 22: 电压/频率 in col B, 工作主频 in cols E-G merged
    product.voltage = _cell_text(sheet, merge_map, 22, 1).strip()
    product.work_frequency = _cell_text(sheet, merge_map, 22, 5).strip()

    # Fallback: if voltage is empty on row 22, check row 21 col B
    if not product.voltage:
        r21b = _cell_text(sheet, merge_map, 21, 1).strip()
        if r21b and not r21b.startswith("Voltage"):
            product.voltage = r21b

    return product


_PLACEHOLDER_EN = (
    "According to the items listed in quotation sheet if not filled"
)
_PLACEHOLDER_CN = "如不填写，按报价单所列项目执行"


def _clean_placeholder(raw: str) -> str:
    """Strip quotation-sheet boilerplate to reveal actual values.

    Many fields wrap the real value inside placeholder text, e.g.::

        (According to the standards listed in quotation sheet if not filled.
        EQCS-1204-2023  东风EMC标准
        如不填写，按报价单所列标准执行)

    We strip the English placeholder line entirely.  The Chinese placeholder
    may appear on its own line OR appended to the value line — in either case
    it and any trailing parenthesized notes are removed.
    """
    text = _normalize(raw)
    if not text:
        return text

    original_len = len(text)

    # Remove outer parentheses if they wrap the whole content
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]

    # Split into lines, process each
    lines = text.split("\n")
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Skip line if it is PURELY the English placeholder boilerplate
        if "According to the" in stripped and "listed in quotation sheet" in stripped:
            # Check if there's substantive content after stripping placeholder text
            after = re.sub(
                r'\(?According to the (items|standards) listed in quotation sheet if not filled\.\s*',
                '', stripped, flags=re.IGNORECASE,
            ).strip()
            if after:
                stripped = after
            else:
                continue

        # Remove the Chinese placeholder (may be inline with real content)
        stripped = re.sub(
            r'\s*如不填写[，,]?按报价单所列(项目|标准)执行\s*', '', stripped,
        )
        # Remove trailing parenthesized placeholder fragments
        stripped = re.sub(r'\s*\(?如不填写[^)]*\)?\s*$', '', stripped)

        if stripped:
            cleaned.append(stripped)

    result = "\n".join(cleaned).strip()
    if len(result) != original_len:
        logger.debug("placeholder_cleaned", original_len=original_len, cleaned_len=len(result))
    return result


def _extract_test_requirements(
    sheet: xlrd.sheet.Sheet, merge_map: dict,
) -> OrderFormTestRequirements:
    """Extract test requirements from rows 23-30."""
    tr = OrderFormTestRequirements()

    # Row 24: Test Requirements / 送检要求
    tr.requirements_desc = _clean_placeholder(_cell_text(sheet, merge_map, 24, 1))

    # Row 25: Test Specification / 测试标准
    raw = _cell_text(sheet, merge_map, 25, 1)
    tr.test_specification = _clean_placeholder(raw)

    # Row 26: Decision Rule / 判定规则
    raw = _cell_text(sheet, merge_map, 26, 1)
    raw_norm = _normalize(raw)
    if "不勾选则默认为不需要" in raw_norm:
        tr.decision_rule = ""  # not explicitly filled
    else:
        tr.decision_rule = raw_norm

    # Row 27: Report Qualification — value in col G (6), fallback to col B
    tr.report_qualification = _cell_text(sheet, merge_map, 27, 6).strip()
    if not tr.report_qualification:
        tr.report_qualification = _cell_text(sheet, merge_map, 27, 1).strip()

    # Row 28: Test Purpose / 检测目的
    tr.test_purpose = _normalize(_cell_text(sheet, merge_map, 28, 1))

    # Row 29: Report Form / 报告形式
    tr.report_form = _normalize(_cell_text(sheet, merge_map, 29, 0))

    return tr


def _extract_other_info(sheet: xlrd.sheet.Sheet, merge_map: dict) -> OrderFormOtherInfo:
    """Extract other special requirements from rows 31-33.

    These rows embed multiple key-value pairs inside single cells.
    Regex is used to extract the structured sub-fields.
    """
    oi = OrderFormOtherInfo()

    # Row 31: special requirements inline
    r31 = _cell_text(sheet, merge_map, 31, 0)
    r31n = _normalize(r31)

    sw_m = re.search(r"软件版本号[：:]\s*(\S+)", r31n)
    if sw_m:
        oi.software_version = sw_m.group(1)

    hw_m = re.search(r"硬件版本号[：:]\s*(\S+)", r31n)
    if hw_m:
        oi.hardware_version = hw_m.group(1)

    # Row 32: report delivery / sample disposal
    r32 = _cell_text(sheet, merge_map, 32, 0)
    oi.report_delivery_method = _normalize(r32)

    # Row 33: date
    r33 = _cell_text(sheet, merge_map, 33, 0)
    r33n = _normalize(r33)
    date_m = re.search(r"Date日期[：:]\s*(\d{8})", r33n)
    if date_m:
        oi.application_date = date_m.group(1)

    return oi


# ── Public API ─────────────────────────────────────────────────────────

class OrderFormExtractor:
    """Deterministic parser for GRGT order forms (.xls).

    Usage::

        data = await OrderFormExtractor.extract(file_bytes)
        print(data.applicant.name_cn)  # "新阳荣乐（上海）汽车电子有限公司"
    """

    @staticmethod
    def can_handle(file_bytes: bytes) -> bool:
        """Check whether these bytes look like a GRGT order form."""
        try:
            wb = xlrd.open_workbook(file_contents=file_bytes,
                                    formatting_info=True)
            sheet = wb.sheet_by_index(0)
            header = _normalize(str(sheet.cell(0, 0).value))
            return TEMPLATE_MARKER in header
        except Exception:
            return False

    @staticmethod
    def get_cell_map() -> dict[str, str]:
        """Return field-key → cell-address mapping for GRGT template.

        Used by the frontend to render a spreadsheet view with precise
        cell highlighting.  Keys mirror the flattened field names
        emitted in the extraction router (``product.name``, etc.).

        All addresses use Excel 1-based row numbers (B19 = col B, row 19).
        """
        return {
            # Applicant (xlrd rows 2-6 → Excel rows 3-7)
            "applicant.name_cn": "C6", "applicant.address_cn": "C7",
            "applicant.name_en": "C4", "applicant.address_en": "C5",
            "applicant.contact.name": "G3", "applicant.contact.email": "G4",
            "applicant.contact.phone": "G5",
            # Manufacturer (xlrd rows 7-11 → Excel rows 8-12)
            "manufacturer.name_cn": "C11", "manufacturer.address_cn": "C12",
            "manufacturer.name_en": "C9", "manufacturer.address_en": "C10",
            "manufacturer.contact.name": "G8", "manufacturer.contact.email": "G9",
            "manufacturer.contact.phone": "G10",
            # Factory (xlrd rows 12-16 → Excel rows 13-17)
            "factory.name_cn": "C16", "factory.address_cn": "C17",
            "factory.name_en": "C14", "factory.address_en": "C15",
            "factory.contact.name": "G13", "factory.contact.email": "G14",
            "factory.contact.phone": "G15",
            # Product (xlrd rows 18-22 → Excel rows 19-23)
            "product.name": "B19", "product.main_test_model": "F19",
            "product.part_number": "B21", "product.trademark": "F21",
            "product.voltage": "B23", "product.work_frequency": "F23",
            # Test requirements (xlrd rows 25-27 → Excel rows 26-28)
            "test_requirements.test_specification": "B26",
            "test_requirements.decision_rule": "B27",
            "test_requirements.report_qualification": "G28",
            # Other info (xlrd row 31 → Excel row 32, merged A-G, regex-extracted)
            "other_info.software_version": "A32",
            "other_info.hardware_version": "A32",
        }

    @staticmethod
    def get_all_cell_values(file_bytes: bytes) -> dict[str, str]:
        """Return ALL non-empty cell values keyed by "ColRow" address.

        Used by the frontend to render a complete spreadsheet view — not
        just the ~30 extracted fields, but every cell with content (labels,
        headers, template text, etc.).

        Only anchor (top-left) cells of merged ranges are included;
        non-anchor merged cells are skipped to avoid value duplication.
        """
        wb = xlrd.open_workbook(file_contents=file_bytes,
                                formatting_info=True)
        sheet = wb.sheet_by_index(0)
        merge_map = _build_merge_map(sheet)

        # Build set of non-anchor merged cells to skip
        non_anchor: set[tuple[int, int]] = set()
        for rlo, rhi, clo, chi in sheet.merged_cells:
            for r in range(rlo, rhi):
                for c in range(clo, chi):
                    if (r, c) != (rlo, clo):
                        non_anchor.add((r, c))

        cols = ['A', 'B', 'C', 'D', 'E', 'F', 'G']
        out: dict[str, str] = {}
        for r in range(min(sheet.nrows, 38)):
            for ci, col in enumerate(cols):
                if (r, ci) in non_anchor:
                    continue  # skip non-anchor merged cells
                val = _cell_text(sheet, merge_map, r, ci)
                if val:
                    out[f"{col}{r + 1}"] = val[:200]  # truncate long cells
        return out

    @staticmethod
    async def extract(file_bytes: bytes) -> OrderFormData:
        """Extract all fields from a GRGT order form.

        Raises ValueError if the file is not a recognized order form.
        """
        t0 = time.monotonic()
        try:
            wb = xlrd.open_workbook(file_contents=file_bytes,
                                    formatting_info=True)
        except xlrd.XLRDError as e:
            logger.error("extraction_failed", error=str(e))
            raise ValueError(f"无法解析 Excel 文件: {e}") from e

        if wb.nsheets == 0:
            raise ValueError("Excel 文件无工作表")

        sheet = wb.sheet_by_index(0)
        merge_map = _build_merge_map(sheet)

        # Detect template
        header = _normalize(str(sheet.cell(0, 0).value))
        if TEMPLATE_MARKER not in header:
            raise ValueError(
                f"不支持的委托单模板: {header[:80]}... "
                f"(需要包含 {TEMPLATE_MARKER})"
            )

        logger.info("extraction_start",
                     template=header.strip(),
                     file_size=len(file_bytes))

        # ── Extract each section with bounds checking ──
        # Each section may fail independently on non-standard layouts.
        def _safe_extract(label: str, fn, *args) -> Any:
            try:
                return fn(*args)
            except IndexError as e:
                logger.warning("extraction_bounds_error",
                               section=label, error=str(e))
                return None

        applicant = _safe_extract("applicant", _extract_company, sheet, merge_map, 2) \
            or OrderFormCompany()
        manufacturer = _safe_extract("manufacturer", _extract_company, sheet, merge_map, 7) \
            or OrderFormCompany()
        factory = _safe_extract("factory", _extract_company, sheet, merge_map, 12) \
            or OrderFormCompany()
        product = _safe_extract("product", _extract_product, sheet, merge_map) \
            or OrderFormProduct()
        test_requirements = _safe_extract(
            "test_requirements", _extract_test_requirements, sheet, merge_map,
        ) or OrderFormTestRequirements()
        other_info = _safe_extract(
            "other_info", _extract_other_info, sheet, merge_map,
        ) or OrderFormOtherInfo()

        # ── Raw notes (rows 34-37) ──
        raw_notes: list[str] = []
        for r in range(34, sheet.nrows):
            text = _cell_text(sheet, merge_map, r, 0).strip()
            if text:
                raw_notes.append(text)

        fields_extracted = 6 + (1 if raw_notes else 0)
        duration_ms = (time.monotonic() - t0) * 1000
        logger.info("extraction_complete",
                     fields_extracted=fields_extracted,
                     duration_ms=round(duration_ms, 1))

        return OrderFormData(
            template_id=header.strip(),
            applicant=applicant,
            manufacturer=manufacturer,
            factory=factory,
            product=product,
            test_requirements=test_requirements,
            other_info=other_info,
            raw_notes=raw_notes,
        )
