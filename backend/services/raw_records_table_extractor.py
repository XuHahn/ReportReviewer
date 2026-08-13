"""Test data table extraction — 5 category dispatch.

EQIC/短时中断: regex row-splitting.  EQMC/EQMR: space-split.
EQIR: row-split by freq-range boundaries (works for all EQIR sub-types).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)

from services.raw_records_filename_parser import (
    RawRecordMeta, CATEGORY_EQIC, CATEGORY_EQIR,
    CATEGORY_EQMC, CATEGORY_EQMR, CATEGORY_OTHER,
)


@dataclass
class TestDataRow:
    row_id: str = ""
    record_filename: str = ""
    category: str = ""
    test_item: str = ""
    injection_point: str = ""
    spec_params: dict = field(default_factory=dict)
    spec_params_raw: str = ""
    temp_condition: str = ""
    pulse_count: str = ""
    cycles: str = ""
    required_performance: str = ""
    actual_performance: str = ""
    test_result: str = ""
    freq_range: str = ""
    test_level: str = ""
    modulation: str = ""
    step: str = ""
    dwell_time: str = ""
    antenna_polarity: str = ""
    antenna_position: str = ""
    eut_direction: str = ""
    test_mode: str = ""
    polarity: str = ""
    margin: str = ""
    record_number: str = ""
    row_type: str = "measurement"
    extraction_source: str = "code"
    source_quote: str = ""


# ── EQIC ──────────────────────────────────────────────────────────────

_EQIC_ROW = re.compile(
    r'(脉冲\s*\S+\s+电源线\s+.*?\d+\s*脉冲\s+[ABC]\s+[ABC][¹²³⁾]*\s+(?:符合|不符合))'
)
_EQIC_COL = re.compile(
    r'脉冲\s*(\S+)\s+(电源线)\s+(.*?)\s+(\d+\s*脉冲)\s+([ABC])\s+([ABC][¹²³⁾]*)\s+(符合|不符合)'
)
_PARAM = re.compile(r"(\w+)\s*=\s*([-\d.]+)\s*(\w+)")
_TEMP = re.compile(r'[（(](T\w+=\s*[-\d.]+℃?\s*[）)])')


def _parse_eqic(proc: str, meta: RawRecordMeta) -> list[TestDataRow]:
    rows = []
    for rt in _EQIC_ROW.findall(proc):
        m = _EQIC_COL.match(rt)
        if not m:
            continue
        spec = m.group(3)
        p = {pm.group(1): pm.group(2) + pm.group(3) for pm in _PARAM.finditer(spec)}
        tm = _TEMP.search(spec)
        rows.append(TestDataRow(
            category=meta.category, record_filename=meta.filename,
            test_item="脉冲" + m.group(1), injection_point=m.group(2),
            spec_params=p, spec_params_raw=spec.strip()[:200],
            temp_condition=tm.group(1).rstrip("）)") if tm else "",
            pulse_count=re.sub(r"\s+", "", m.group(4)), required_performance=m.group(5),
            actual_performance=m.group(6), test_result=m.group(7),
        ))
    return rows


# ── 短时中断 ──────────────────────────────────────────────────────────

_INT_ROW = re.compile(
    r'(短时中断试验\s+电源线\s+.*?\d+个循环\s+[ABC]\s+[ABC][¹²³⁾]*\s+(?:符合|不符合))'
)
_INT_COL = re.compile(
    r'(短时中断试验)\s+(电源线)\s+(.*?)\s+(\d+个循环)\s+([ABC])\s+([ABC][¹²³⁾]*)\s+(符合|不符合)'
)


def _parse_interrupt(proc: str, meta: RawRecordMeta) -> list[TestDataRow]:
    rows = []
    for rt in _INT_ROW.findall(proc):
        m = _INT_COL.match(rt)
        if not m:
            continue
        rows.append(TestDataRow(
            category=meta.category, record_filename=meta.filename,
            test_item=m.group(1), injection_point=m.group(2),
            spec_params_raw=m.group(3).strip()[:200],
            cycles=m.group(4), required_performance=m.group(5),
            actual_performance=m.group(6), test_result=m.group(7),
        ))
    return rows


# ── EQIR (row-split by result markers) ──────────────────────────────

def _parse_eqir(proc: str, meta: RawRecordMeta) -> list[TestDataRow]:
    """Parse EQIR by finding "符合/不符合" markers and working backwards to
    locate the start of each row, then splitting columns from each row."""
    rows = []

    # Find all result positions
    result_positions = [(m.start(), m.group(1))
                        for m in re.finditer(r'(符合|不符合)', proc)
                        if m.start() > 100]  # skip possible header match

    for pos, result in result_positions:
        # Work backwards to find the freq_range that starts this row
        before = proc[:pos].strip()
        # Last word before result should be actual_performance
        # Before that: required_performance
        # Before that: the rest of the row
        before_parts = before.rsplit(None, 2)
        if len(before_parts) < 3:
            continue

        actual_perf = before_parts[-1]
        required_perf = before_parts[-2]
        row_body = before_parts[-3]

        # Now split row_body: last few tokens are dwell, step, mod, level
        # Find the freq range at the very beginning
        freq_m = re.search(r'(\d+\S*(?:-\d+\S*)?)$', row_body)
        if not freq_m:
            # Try more aggressively: the first token of row_body
            freq_start = row_body.split()[-1]
        else:
            freq_start = freq_m.group(1)

        # Get everything before the freq as the body of the row
        # Actually let's just take the well-known columns from the right
        body_parts = row_body.rsplit(None, 5)
        if len(body_parts) >= 5:
            freq = body_parts[-5] if len(body_parts) >= 5 else ""
            level = body_parts[-4] if len(body_parts) >= 4 else ""
            mod = body_parts[-3] if len(body_parts) >= 3 else ""
            step = body_parts[-2] if len(body_parts) >= 2 else ""
            dwell = body_parts[-1]
        else:
            continue

        rows.append(TestDataRow(
            category=meta.category, record_filename=meta.filename,
            freq_range=freq, test_level=level,
            modulation=mod, step=step,
            dwell_time=dwell,
            required_performance=required_perf,
            actual_performance=actual_perf,
            test_result=result,
        ))

    return rows


# ── EQMC / EQMR ───────────────────────────────────────────────────────

_FREQ = re.compile(r'([\d.Ee+-]+\s*-\s*[\d.Ee+-]+)')


def _parse_simple(proc: str, meta: RawRecordMeta) -> list[TestDataRow]:
    rows = []
    for m in _FREQ.finditer(proc):
        rest = proc[m.end():m.end() + 100].strip().split()
        rows.append(TestDataRow(
            category=meta.category, record_filename=meta.filename,
            freq_range=m.group(1),
            test_mode=rest[0] if rest else "/",
            polarity=rest[1] if len(rest) > 1 else "/",
            margin=rest[2] if len(rest) > 2 else "/",
            test_result=rest[3] if len(rest) > 3 else "/",
            record_number=rest[4] if len(rest) > 4 else "/",
        ))
    return rows


# ── Dispatch ──────────────────────────────────────────────────────────

def extract_table_rows(meta: RawRecordMeta) -> list[TestDataRow]:
    proc = meta.raw_text.replace("\n", " ")
    proc = re.sub(r"\s+", " ", proc).strip()
    cat = meta.category

    if cat == CATEGORY_EQIC:
        rows = _parse_eqic(proc, meta)
    elif cat == CATEGORY_EQIR:
        rows = _parse_eqir(proc, meta)
    elif cat in (CATEGORY_EQMC, CATEGORY_EQMR):
        rows = _parse_simple(proc, meta)
    elif cat == CATEGORY_OTHER:
        if "短时中断" in proc:
            rows = _parse_interrupt(proc, meta)
        else:
            # Unknown templates must not fall through to the emission-frequency
            # parser. Dates, document numbers and instrument models often contain
            # hyphenated numbers and were previously fabricated into data rows.
            rows = []
    else:
        rows = []

    if not rows:
        logger.warning("empty_table", filename=meta.filename, category=cat)
    else:
        logger.info("table_rows_extracted", filename=meta.filename, category=cat, row_count=len(rows))

    return rows


def extract_all_tables(metas: list[RawRecordMeta]) -> list[TestDataRow]:
    all_rows = []
    for meta in metas:
        if meta.raw_text:
            rows = extract_table_rows(meta)
            for i, row in enumerate(rows):
                row.row_id = f"{meta.test_item_code}_{meta.sequence}_{i}"
            all_rows.extend(rows)
    return all_rows
