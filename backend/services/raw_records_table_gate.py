"""Independent PDF table inventory used to quality-gate LLM transcription.

This module deliberately does not infer test semantics.  It only uses physical
PDF table geometry and conservative row-ending markers to estimate how many
logical business rows are visible in test-result and instrument tables.
"""

from __future__ import annotations

import io
import re
from typing import Any

import pdfplumber

from utils.logger import get_logger

logger = get_logger(__name__)

_RESULT_RE = re.compile(r"(?:不符合|符合|PASS|FAIL|Passed|Failed)\s*$", re.I)
_DATE_RE = re.compile(r"(?:20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?|/)\s*$")
_INSTRUMENT_HEADER_RE = re.compile(
    r"仪器(?:名称|设备)|制造商|型号|系列号|序列号|校准有效期|calibration|serial|model",
    re.I,
)
_TEST_HEADER_RE = re.compile(
    r"测试(?:结果|结论|电压|等级|频率)|判定|要求性能|实际性能|verdict|result|frequency|level",
    re.I,
)


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _row_text(row: list[Any]) -> str:
    return " ".join(part for part in (_text(cell) for cell in row) if part)


def _classify_table(rows: list[list[Any]]) -> str:
    text = " ".join(_row_text(row) for row in rows)
    instrument_hits = len(_INSTRUMENT_HEADER_RE.findall(text))
    test_hits = len(_TEST_HEADER_RE.findall(text))
    if instrument_hits >= 2 and instrument_hits >= test_hits:
        return "instrument"
    if test_hits >= 1:
        return "test_data"
    return "other"


def _data_row_slice(rows: list[list[Any]], table_type: str) -> list[list[Any]]:
    header_re = _INSTRUMENT_HEADER_RE if table_type == "instrument" else _TEST_HEADER_RE
    required_hits = 2 if table_type == "instrument" else 1
    start = 0
    for index, row in enumerate(rows):
        if len(header_re.findall(_row_text(row))) >= required_hits:
            start = index + 1
            break
    result: list[list[Any]] = []
    for row in rows[start:]:
        text = _row_text(row)
        if table_type == "instrument" and re.search(r"检测人员|测试人员|校核人员|审核人员", text):
            break
        if table_type == "test_data" and re.match(
            r"\s*(?:备注|总体结论|测试结论|总判定|overall\s+(?:result|conclusion))",
            text,
            re.I,
        ):
            break
        result.append(row)
    return result


def _header_row(rows: list[list[Any]], table_type: str) -> tuple[int, list[tuple[int, str]]]:
    header_re = _INSTRUMENT_HEADER_RE if table_type == "instrument" else _TEST_HEADER_RE
    required_hits = 2 if table_type == "instrument" else 1
    for index, row in enumerate(rows):
        if len(header_re.findall(_row_text(row))) < required_hits:
            continue
        anchors = [
            (column_index, _text(cell))
            for column_index, cell in enumerate(row)
            if _text(cell)
        ]
        return index, anchors
    return -1, []


def _count_logical_rows(
    rows: list[list[Any]], table_type: str,
) -> tuple[int | None, list[str], list[dict[str, str]]]:
    """Count row terminators after a recognizable header.

    Chinese PDF tables often split one logical row into several physical rows.
    A verdict or calibration date normally terminates the logical row, which is
    more stable than counting extracted geometry rows.  ``None`` means that the
    table cannot be counted conservatively and must not be used as a hard gate.
    """
    if table_type not in {"test_data", "instrument"}:
        return None, [], []
    matcher = _RESULT_RE if table_type == "test_data" else _DATE_RE
    header_index, anchors = _header_row(rows, table_type)
    samples: list[str] = []
    row_end_cells: list[dict[str, str]] = []
    count = 0
    data_rows = rows[header_index + 1:] if header_index >= 0 else _data_row_slice(rows, table_type)
    for row in data_rows:
        text = _row_text(row)
        if table_type == "instrument" and re.search(r"检测人员|测试人员|校核人员|审核人员", text):
            break
        if table_type == "test_data" and re.match(
            r"\s*(?:备注|总体结论|测试结论|总判定|overall\s+(?:result|conclusion))",
            text,
            re.I,
        ):
            break
        if text and matcher.search(text):
            count += 1
            if len(samples) < 5:
                samples.append(text[:240])
            row_end_cells.append({
                header: _text(row[column_index])
                for column_index, header in anchors
                if column_index < len(row)
            })
    return (count if count else None), samples, row_end_cells


def extract_table_inventory(pdf_bytes: bytes, filename: str = "") -> list[dict]:
    inventory: list[dict] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                for table_number, rows in enumerate(page.extract_tables() or [], start=1):
                    normalized_rows = [row for row in rows if isinstance(row, list) and _row_text(row)]
                    if not normalized_rows:
                        continue
                    table_type = _classify_table(normalized_rows)
                    logical_count, samples, row_end_cells = _count_logical_rows(
                        normalized_rows, table_type,
                    )
                    inventory.append({
                        "page": page_number,
                        "table_index": table_number,
                        "table_type": table_type,
                        "physical_row_count": len(normalized_rows),
                        "logical_row_count": logical_count,
                        "count_method": "row_terminator" if logical_count is not None else "unavailable",
                        "header_text": _row_text(normalized_rows[0])[:300],
                        "row_end_samples": samples,
                        "row_end_cells": row_end_cells,
                    })
    except Exception as exc:
        logger.warning(
            "raw_record_table_inventory_failed",
            filename=filename,
            error=str(exc)[:300],
        )
        return []

    logger.info(
        "raw_record_table_inventory",
        filename=filename,
        table_count=len(inventory),
        countable_tables=sum(1 for item in inventory if item["logical_row_count"] is not None),
        test_rows=sum(item["logical_row_count"] or 0 for item in inventory if item["table_type"] == "test_data"),
        instrument_rows=sum(item["logical_row_count"] or 0 for item in inventory if item["table_type"] == "instrument"),
    )
    return inventory


def expected_rows_by_type(inventory: list[dict]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in inventory:
        table_type = str(item.get("table_type") or "")
        count = item.get("logical_row_count")
        if table_type in {"test_data", "instrument"} and isinstance(count, int):
            result[table_type] = result.get(table_type, 0) + count
    return result
