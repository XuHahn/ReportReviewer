"""Aggregate raw records extraction results across all 46 PDFs.

Produces: test conclusion summary, deduplicated instruments, calibration issues.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from utils.logger import get_logger

logger = get_logger(__name__)

from services.raw_records_filename_parser import RawRecordMeta
from services.raw_records_table_extractor import TestDataRow
from services.raw_records_instrument_extractor import (
    InstrumentRecord, CalibrationIssue,
    extract_all_instruments, deduplicate_instruments, validate_all_calibrations,
)
from services.exceptions import ExtractionError


@dataclass
class TestConclusionSummary:
    test_item_code: str = ""
    test_item_name: str = ""
    category: str = ""
    round_count: int = 0
    operators: list[str] = field(default_factory=list)
    all_rounds_pass: bool = True
    rounds: list[dict] = field(default_factory=list)


@dataclass
class BatchResult:
    metas: list[RawRecordMeta] = field(default_factory=list)
    data_rows: list[TestDataRow] = field(default_factory=list)
    instruments: list[InstrumentRecord] = field(default_factory=list)
    deduplicated_instruments: list[InstrumentRecord] = field(default_factory=list)
    calibration_issues: list[CalibrationIssue] = field(default_factory=list)
    conclusion_summary: list[TestConclusionSummary] = field(default_factory=list)
    total_files: int = 0
    total_test_items: int = 0
    semantic_extraction_count: int = 0
    semantic_extraction_failures: list[dict] = field(default_factory=list)
    table_quality: list[dict] = field(default_factory=list)
    semantic_conflicts: list[dict] = field(default_factory=list)
    extraction_quality: str = "complete"
    failed_passes: list[str] = field(default_factory=list)
    extraction_metrics: dict = field(default_factory=dict)


def summarize_conclusions(metas: list[RawRecordMeta]) -> list[TestConclusionSummary]:
    groups: dict[str, list[RawRecordMeta]] = {}
    for m in metas:
        groups.setdefault(m.test_item_code, []).append(m)

    results = []
    for code, items in groups.items():
        rounds = []
        all_pass = True
        ops: set[str] = set()
        for item in sorted(items, key=_sequence_sort_key):
            conclusion = item.get_header("test_conclusion")
            if conclusion != "符合":
                all_pass = False
            ops.add(item.operator)
            rounds.append({
                "sequence": item.sequence, "operator": item.operator,
                "test_date": item.test_date_str, "conclusion": conclusion or "未知",
            })
        results.append(TestConclusionSummary(
            test_item_code=code,
            test_item_name=items[0].test_item_name if items else "",
            category=items[0].category if items else "",
            round_count=len(items), operators=list(ops),
            all_rounds_pass=all_pass, rounds=rounds,
        ))
    return results


def _sequence_sort_key(item: RawRecordMeta) -> tuple[int, int, str]:
    """Sort numeric sequences without converting attacker-controlled text to int."""
    sequence = str(item.sequence or "").strip()
    if not sequence.isascii() or not sequence.isdigit():
        return (1, 0, sequence)
    normalized = sequence.lstrip("0") or "0"
    return (0, len(normalized), normalized)


def validate_extraction(metas: list[RawRecordMeta],
                        data_rows: list[TestDataRow]) -> list[dict]:
    """Strict validation: every file must have complete extraction.

    Checks: raw_text, header fields, table rows (for data-heavy categories).
    Returns list of failure dicts.  Empty list = all good.
    """
    from services.raw_records_filename_parser import CATEGORY_EQMC, CATEGORY_EQMR

    # Index rows by filename
    rows_by_file: dict[str, list[TestDataRow]] = {}
    for r in data_rows:
        rows_by_file.setdefault(r.record_filename, []).append(r)

    failures: list[dict] = []
    for m in metas:
        issues = []
        # PDF text extraction
        if not m.raw_text:
            issues.append("PDF文本提取失败（文件损坏或加密？）")

        # Header fields
        hf = m._header_fields
        if not hf:
            issues.append("头信息字段全部缺失（模板不匹配？）")
        else:
            for key in ("report_id_pdf", "sample_name", "test_conclusion"):
                if not m.get_header(key):
                    issues.append(f"缺少关键头字段: {key}")

        # Table rows — data-heavy categories must have rows
        rows = rows_by_file.get(m.filename, [])
        if m.category in (CATEGORY_EQMC, CATEGORY_EQMR) and not rows:
            issues.append(f"测试数据表提取为空（类别 {m.category}，表结构不匹配？）")

        if issues:
            failures.append({"filename": m.filename, "category": m.category, "issues": issues})
            logger.warning("extraction_validation_failed", filename=m.filename, category=m.category, issues=issues)

    return failures


def aggregate(metas: list[RawRecordMeta], data_rows: list[TestDataRow]) -> BatchResult:
    # Strict validation before aggregation
    failures = validate_extraction(metas, data_rows)
    if failures:
        msg = f"原始记录提取不完整: {len(failures)}/{len(metas)} 个文件存在问题"
        raise ExtractionError(msg, failures)
    instruments = extract_all_instruments(metas)
    unique = deduplicate_instruments(instruments)
    issues = validate_all_calibrations(unique, metas)
    summary = summarize_conclusions(metas)
    codes = {m.test_item_code for m in metas}
    result = BatchResult(
        metas=metas, data_rows=data_rows,
        instruments=instruments, deduplicated_instruments=unique,
        calibration_issues=issues, conclusion_summary=summary,
        total_files=len(metas), total_test_items=len(codes),
    )
    logger.info("aggregation_done", files=result.total_files, items=result.total_test_items,
                instruments=len(unique), issues=len(issues))
    return result
