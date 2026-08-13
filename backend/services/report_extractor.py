"""Final test report extraction — AI-driven, 5-layer output, DeepSeek V4.

With V4's 1M context window we send the full document without truncation and
extract ALL items (instruments, data rows) with per-field confidence metadata.
"""

from __future__ import annotations

import io
import re
import time
from collections import defaultdict
from utils.logger import get_logger
from utils.pdf_utils import extract_pdf_text, extract_text as _extract_file_text
from services.prompts import (
    REPORT_PROMPT, UNIVERSAL_TEST_ITEM_PROMPT,
)
from services.deepseek_client import _is_billing_or_auth_error
from services.exceptions import ExtractionError, SizeLimitError
from models import (
    ReportData, ReportCoverInfo, ReportResultItem,
    ReportSampleInfo, ReportInstrument, ReportDataRow,
    ReportTocItem,
    TestItemExtraction, UniversalDataRow, SampleResultBlock,
    TestResultsSection, LimitEntry, ValidationIssue,
)

logger = get_logger(__name__)

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB


def _safe_float(val) -> float | None:
    """Convert value to float, returning None on failure."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


_TEST_CODE_RE = re.compile(r'EQ/[A-Z]{2}\d+')
_LAB_SAMPLE_ID_RE = re.compile(r"(?i)\bE\d{10,}-\d{4}\b")
_NUMBER_UNIT_RE = re.compile(
    r"(?:±|[<>≤≥]?[-+]?)\s*\d+(?:\.\d+)?\s*"
    r"(?:mΩ|kΩ|MΩ|mV|kV|V|mA|A|Hz|kHz|MHz|ms|min|s|%)",
    re.IGNORECASE,
)


def count_test_codes(text: str) -> set[str]:
    return set(_TEST_CODE_RE.findall(text))


def validate_report(raw_text: str, data: ReportData) -> list[str]:
    errors: list[str] = []
    codes_doc = count_test_codes(raw_text)
    codes_ai = set()
    for r in data.results:
        codes_ai.update(_TEST_CODE_RE.findall(r.test_item))
    if codes_doc and codes_ai:
        missing = codes_doc - codes_ai
        extra = codes_ai - codes_doc
        # Allow small discrepancies — the regex may miss codes due to formatting
        threshold = max(2, int(len(codes_doc) * 0.1))
        if len(missing) >= threshold:
            errors.append(f"AI 漏掉 {len(missing)} 个测试项: {sorted(missing)}")
        if len(extra) >= threshold:
            errors.append(f"AI 多出 {len(extra)} 个测试项: {sorted(extra)}")

    # Margin formula: 余量 = 限值 - 结果
    margin_bad = 0
    for row in data.data_rows[:50]:
        try:
            r, l, m = float(row.result_dbua), float(row.limit_dbua), float(row.margin_db or 0)
            if abs(round(l - r, 2) - round(m, 2)) > 0.1:
                margin_bad += 1
        except (ValueError, TypeError):
            pass
    if data.data_rows and margin_bad > len(data.data_rows) * 0.1:
        errors.append(f"余量公式异常: {margin_bad}/{len(data.data_rows)} 行")

    if margin_bad > 0:
        logger.warning("margin_mismatch", count=margin_bad, total_rows=len(data.data_rows))

    return errors


# ── Chinese → English field name translation ──────────────────────────
# The AI prompt uses Chinese field names (matching the document), but the
# Pydantic models use English field names.  This mapping bridges the gap.

_COVER_MAP = {
    "报告编号": "report_number", "测试计划编号": "test_plan_number",
    "委托单位": "client_name", "单位地址": "client_address",
    "样品名称": "sample_name", "零部件号": "part_number",
    "样品型号": "sample_model", "收样日期": "receive_date",
    "检测日期": "test_date_range", "检测结论": "test_conclusion",
    "编制": "preparer", "审核": "reviewer", "批准": "approver",
    "签发日期": "issue_date",
}
_RESULT_MAP = {
    "测试项目": "test_item", "工作模式": "test_mode",
    "标准要求": "standard_requirement", "等级要求": "grade_requirement",
    "结果": "result",
}
_SAMPLE_MAP = {
    "委托单位名称": "client_name", "生产单位名称": "manufacturer_name",
    "样品名称": "sample_name", "型号": "model",
    "零部件号": "part_number", "硬件版本": "hw_version",
    "软件版本": "sw_version", "额定电压": "rated_voltage",
}
_INSTRUMENT_MAP = {
    "测试项目": "test_item", "仪器名称": "name",
    "制造商": "manufacturer", "型号": "model",
    "系列号": "serial_no", "校准有效期": "calibration_end",
}
_DATA_ROW_MAP = {
    "测试项目": "test_item", "频率(MHz)": "freq_mhz",
    "读值(dBuA)": "reading", "修正因子(dB)": "correction_db",
    "结果(dBuA)": "result_dbua", "限值(dBuA)": "limit_dbua",
    "余量(dB)": "margin_db", "备注": "note",
}


def _translate_keys(d: dict, mapping: dict[str, str]) -> dict:
    """Rename keys in *d* according to *mapping*, keeping unmapped keys as-is."""
    return {mapping.get(k, k): v for k, v in d.items()}


def _extract_field_meta(result: dict) -> dict[str, dict]:
    """Pull per-field confidence + source_quote from the AI _meta section."""
    meta = result.pop("_meta", None)
    if not isinstance(meta, dict):
        return {}
    return {
        k: {"confidence": float(v.get("confidence", 0.8)),
            "source_quote": str(v.get("source_quote", ""))}
        for k, v in meta.items() if isinstance(v, dict)
    }


class ReportExtractor:

    # DeepSeek V4: 1M context, no text truncation needed.
    # Limit output to keep latency reasonable (prompt limits output to ~50 instruments + 30 data rows).
    MAX_OUTPUT_TOKENS = 65536
    MAX_RETRIES = 2
    TIMEOUT_SEC = 600

    # ── Pass 0: Deterministic TOC extraction (zero tokens) ──────────────

    # Patterns for TOC line parsing.  Matches lines like:
    #   EQ/MC01  电源线传导发射测量  ..... 15-25
    #   EQ/MC02  信号线传导发射测量  15
    #   EQ/IC01  针对脉冲1&2的抗扰性测试 .............. 30
    _TOC_LINE_RE = re.compile(
        r'(EQ/[A-Z]{2}\d+)\s+'          # test item code
        r'(.+?)'                          # test item name (lazy)
        r'\s*\.{2,}\s*'                   # leader dots (.. or ...)
        r'(\d+)(?:\s*[-–]\s*(\d+))?'     # page range
    )
    _TOC_LINE_SIMPLE_RE = re.compile(
        r'(EQ/[A-Z]{2}\d+)\s+'            # test item code
        r'(.+?)'                          # test item name (lazy)
        r'\s+(\d+)(?:\s*[-–]\s*(\d+))?\s*$'  # page range (no dots)
    )
    _TOC_TRAILING_NUMBER_RE = re.compile(r'\s+(\d+)\s*$')

    # Section header patterns for splitting the document
    _SECTION_HEADER_RE = re.compile(
        r'(EQ/[A-Z]{2}\d+)\s*'          # test item code
        r'([^\n]{0,80})'                 # optional name suffix (short, not entire paragraph)
    )
    _INSTRUMENT_HEADER_RE = re.compile(
        r'(测试仪器|检测设备|主要仪器|测量设备|试验设备|仪器设备)'
    )
    _RESULTS_HEADER_RE = re.compile(
        r'(测试结果|检测结果|试验结果|检测结论|判定|测试结论)'
    )

    @staticmethod
    def _extract_toc(raw_text: str) -> list[ReportTocItem]:
        """Deterministic TOC extraction from report PDF text.

        Tries three strategies in order:
        1. Classic TOC page pattern (EQ/XXnn  name  ...  page)
        2. Scan section headers throughout the document
        3. Extract all unique test codes found anywhere in the text

        Returns at least the test codes found, so Pass 3 grouping always works.
        """
        # Strategy 1: Classic TOC pattern in front matter
        items = ReportExtractor._try_toc_page_pattern(raw_text[:8000])
        if items:
            logger.info("toc_extracted", strategy="classic_toc",
                       count=len(items), codes=[i.code for i in items])
            return items

        # Strategy 2: Build virtual TOC from section headers
        items = ReportExtractor._build_virtual_toc(raw_text)
        if items:
            logger.info("toc_extracted", strategy="virtual_toc",
                       count=len(items), codes=[i.code for i in items])
            return items

        # Strategy 3: Bare codes only (no names, no pages)
        codes = sorted(set(_TEST_CODE_RE.findall(raw_text)))
        items = [ReportTocItem(code=c, name='', page_start=0, page_end=0)
                 for c in codes]
        logger.info("toc_extracted", strategy="bare_codes",
                   count=len(items), codes=codes)
        return items

    @staticmethod
    def _try_toc_page_pattern(front: str) -> list[ReportTocItem]:
        """Try classic TOC page patterns (EQ/XXnn name ... page) in front matter."""
        items: list[ReportTocItem] = []
        for line in front.split('\n'):
            line = line.strip()
            if not line or not line.upper().startswith('EQ/'):
                continue
            # Skip lines that are clearly not TOC entries
            if '：' in line or ':' in line:
                if not _TEST_CODE_RE.match(line.split()[0] if line.split() else ''):
                    continue

            m = ReportExtractor._TOC_LINE_RE.match(line)
            if not m:
                m = ReportExtractor._TOC_LINE_SIMPLE_RE.match(line)

            if m:
                code = m.group(1)
                name = m.group(2).strip().rstrip('.')
                page_start = int(m.group(3))
                page_end = int(m.group(4)) if m.group(4) else 0
                items.append(ReportTocItem(
                    code=code, name=name,
                    page_start=page_start, page_end=page_end,
                ))
            else:
                parts = line.split()
                if len(parts) >= 2 and _TEST_CODE_RE.match(parts[0]):
                    code = parts[0]
                    tm = ReportExtractor._TOC_TRAILING_NUMBER_RE.search(line)
                    if tm:
                        name_part = line[len(code):tm.start()].strip().rstrip('.')
                        items.append(ReportTocItem(
                            code=code, name=name_part or '',
                            page_start=int(tm.group(1)), page_end=0,
                        ))
        return items

    @staticmethod
    def _build_virtual_toc(raw_text: str) -> list[ReportTocItem]:
        """Build a virtual TOC by scanning the entire document for EQ/ section headers.

        Finds all ``EQ/XXnn Name...`` patterns and deduplicates by code.
        This handles non-standard report formats that lack a classic TOC page.
        """
        seen_codes: set[str] = set()
        items: list[ReportTocItem] = []

        for m in ReportExtractor._SECTION_HEADER_RE.finditer(raw_text):
            code = m.group(1)
            if code in seen_codes:
                continue
            seen_codes.add(code)
            name = m.group(2).strip() if m.group(2) else ''
            # Clean up name — remove trailing punctuation and whitespace
            name = name.rstrip('.,;:，。；：、 \t')
            items.append(ReportTocItem(
                code=code, name=name, page_start=0, page_end=0,
            ))

        return items

    # ── Multi-pass extraction helpers ────────────────────────────────────

    _STRUCTURAL_MIN_TEXT = 10000
    _STRUCTURAL_MAX_TEXT = 60000
    _INSTRUMENT_MIN_TEXT = 10000
    _INSTRUMENT_MAX_TEXT = 60000
    _MAX_ITEM_SECTION_CHARS = 60000  # per test-item section cap

    # Subsection headers to KEEP (bilingual, both emission & immunity)
    _KEEP_SUBSECTION_RE = re.compile(
        r'(限值|LIMIT|测试规范|TEST\s*SPECIFICATION|'
        r'测试程序|TEST\s*PROCEDURE|'
        r'测试要求|TEST\s*REQUIREMENT|'
        r'测试结果|TEST\s*RESULTS?|检测结果|试验结果|'
        r'背景数据|BACKGROUND)',
        re.IGNORECASE,
    )
    # Subsection headers to SKIP
    _SKIP_SUBSECTION_RE = re.compile(
        r'(测试布置|TEST\s*SETUP|'
        r'测试照片|PHOTOGRAPH|PHOTO|'
        r'附录|APPENDIX)',
        re.IGNORECASE,
    )
    # Section number pattern: "5." or "5.1" or "5.5.1.1" followed by text.
    # Must NOT match data values (e.g. "0.170" has 3-digit sub-component,
    # "30.02" is followed by another number, not text).
    _SECTION_NUM_RE = re.compile(
        r'^(\d{1,2})(?:\.\d{1,2})*\.?\s+[^\d\s]'
    )
    # Test item section header: major number + test code or test name
    _TEST_ITEM_HEADER_RE = re.compile(
        r'(?:^|\n)(\d+)[\.\s]+'
        r'((?:EQ/)?[A-Z]{2,4}\d*)\s*'
        r'([^\n]{0,80})',
        re.MULTILINE,
    )

    @staticmethod
    def _split_by_test_item(raw_text: str, toc_items: list) -> list[tuple[str, str]]:
        """Split document by test item sections using TOC + section numbering.

        For each test item found in the document, extracts the relevant
        subsections (限值/测试规范, 测试程序, 测试结果) and skips
        irrelevant ones (测试布置, 测试照片, 附录).

        Returns a list of (test_item_code, section_text) tuples.
        """
        if not toc_items:
            return []

        # Build ordered list of test item codes from TOC
        toc_codes = [t.code for t in toc_items if t.code]

        # Find section headers in the document BODY (skip TOC).
        # TOC lines contain long dotted leaders (10+ consecutive dots);
        # body section headers never do.
        _TOC_DOTS_RE = re.compile(r'\.{10,}')
        header_positions: list[tuple[int, str, str]] = []  # (pos, code, full_header)
        for code in toc_codes:
            pattern = re.compile(
                r'(?:^|\n)(\d+)[\.\s]+'
                + re.escape(code) +
                r'\s*([^\n]{0,300})',   # longer capture to include full TOC line
                re.MULTILINE,
            )
            for m in pattern.finditer(raw_text):
                full_line = m.group(0).strip()
                if _TOC_DOTS_RE.search(full_line):
                    continue  # TOC entry (has dotted leaders), skip
                header_positions.append((m.start(), code, full_line))
                break  # first non-TOC occurrence is the body section header

        # Sort by position in document
        header_positions.sort(key=lambda x: x[0])

        if not header_positions:
            logger.warning("split_by_item_no_headers",
                          toc_codes=toc_codes,
                          text_len=len(raw_text))
            return []

        # For each test item section, extract the text between its header
        # and the next test item's header, then filter subsections
        sections: list[tuple[str, str]] = []
        for i, (pos, code, header) in enumerate(header_positions):
            next_pos = header_positions[i + 1][0] if i + 1 < len(header_positions) else len(raw_text)
            section_text = raw_text[pos:next_pos]

            # Trim to reasonable size
            if len(section_text) > ReportExtractor._MAX_ITEM_SECTION_CHARS:
                section_text = section_text[:ReportExtractor._MAX_ITEM_SECTION_CHARS]

            # Filter: keep relevant subsections, skip photos/setups
            filtered = ReportExtractor._filter_subsections(section_text)
            if filtered.strip():
                sections.append((code, filtered))

        logger.info("split_by_item_done",
                   toc_count=len(toc_codes),
                   section_count=len(sections),
                   codes=[s[0] for s in sections])
        return sections

    @staticmethod
    def _split_by_result_item(raw_text: str, results: list) -> list[tuple[str, str]]:
        """Split code-less reports using reviewed result names as body headings.

        Some electrical-performance reports number their chapters only in the
        rendered TOC and repeat the item name without an ``EQ/...`` code in the
        body.  A bare name match is unsafe because the same text also occurs in
        summary and result tables.  Treat it as a chapter heading only when it
        is a standalone line immediately followed by a known specification or
        requirement subsection.
        """
        headings: list[tuple[int, str]] = []
        seen_positions: set[int] = set()
        following_section = (
            r"(?:TEST\s*SPECIFICATION|TEST\s*REQUIREMENTS?|"
            r"LIMITS?|测试规范|试验规范|测试要求|试验要求|限值)"
        )
        for index, result in enumerate(results):
            name = str(getattr(result, "test_item", "") or "").strip()
            if not name:
                continue
            flexible_name = r"\s+".join(re.escape(part) for part in name.split())
            # Code-less Chinese reports commonly use a numbered major chapter
            # (``5. 项目名称``) followed by a numbered specification section
            # (``5.1 测试规范``).  Requiring the item name and subsection label
            # to be standalone adjacent lines silently dropped every execution
            # table in that otherwise well-structured layout.
            pattern = re.compile(
                rf"(?im)^[ \t]*(?:\d{{1,3}}[.．、]\s*)?"
                rf"{flexible_name}[ \t]*\r?\n"
                rf"(?:[ \t]*\r?\n){{0,6}}[ \t]*"
                rf"(?:\d{{1,3}}(?:[.．]\d{{1,3}})+\s*)?"
                rf"{following_section}[ \t]*$",
            )
            match = pattern.search(raw_text)
            if match is None or match.start() in seen_positions:
                continue
            seen_positions.add(match.start())
            headings.append((match.start(), f"ITEM_{index + 1}"))

        headings.sort(key=lambda item: item[0])
        sections: list[tuple[str, str]] = []
        for index, (position, code) in enumerate(headings):
            next_position = headings[index + 1][0] if index + 1 < len(headings) else len(raw_text)
            section_text = raw_text[position:next_position]
            filtered = ReportExtractor._filter_subsections(
                section_text[:ReportExtractor._MAX_ITEM_SECTION_CHARS]
            )
            if filtered.strip():
                sections.append((code, filtered))
        logger.info(
            "split_by_result_item_done",
            result_count=len(results),
            section_count=len(sections),
        )
        return sections

    @staticmethod
    def _filter_subsections(section_text: str) -> str:
        """Keep 限值/测试规范 + 测试程序 + 测试结果, skip 布置/照片/附录.

        Uses hierarchy-aware logic: when inside a KEEP subsection, all
        deeper sub-subsections (e.g. 6.5.1 inside 6.5 测试结果) are also
        kept, even if their title doesn't match KEEP keywords.
        """
        lines = section_text.split('\n')
        result_parts: list[str] = []
        current_part: list[str] = []
        current_keep = True
        keep_depth: int | None = None  # depth of nearest KEEP ancestor

        header_ended = False

        for line in lines:
            m = ReportExtractor._SECTION_NUM_RE.match(line.strip())
            if m:
                # Depth = number of dot-separated components in the section number.
                # Count dots in the leading numeric prefix.
                leading = re.match(r'[\d.]+', line.strip())
                depth = leading.group(0).count('.') + 1 if leading else 99
                # Flush current part
                if current_part and current_keep:
                    result_parts.append('\n'.join(current_part))
                current_part = [line]

                if keep_depth is not None and depth > keep_depth:
                    # Inside a KEEP parent — auto-keep deeper subsections
                    current_keep = True
                else:
                    keep_depth = None  # exited the KEEP scope
                    keep_match = ReportExtractor._KEEP_SUBSECTION_RE.search(line)
                    skip_match = ReportExtractor._SKIP_SUBSECTION_RE.search(line)
                    current_keep = bool(keep_match) and not bool(skip_match)
                    if current_keep:
                        keep_depth = depth
                header_ended = True
            else:
                # Code-less report bodies often use unnumbered all-caps
                # subsection headings.  Recognize only the explicit keep/skip
                # vocabulary so ordinary result rows cannot alter the state.
                stripped = line.strip()
                looks_like_heading = (
                    len(stripped) <= 120
                    and (not any(char.isalpha() for char in stripped) or stripped == stripped.upper())
                )
                keep_match = ReportExtractor._KEEP_SUBSECTION_RE.search(stripped)
                skip_match = ReportExtractor._SKIP_SUBSECTION_RE.search(stripped)
                if stripped and looks_like_heading and (keep_match or skip_match):
                    if current_part and current_keep:
                        result_parts.append('\n'.join(current_part))
                    current_part = [line]
                    current_keep = bool(keep_match) and not bool(skip_match)
                    keep_depth = 2 if current_keep else None
                    header_ended = True
                    continue
                if not header_ended:
                    current_keep = True
                current_part.append(line)

        if current_part and current_keep:
            result_parts.append('\n'.join(current_part))

        return '\n'.join(result_parts)

    @staticmethod
    async def _call_ai_pass(reviewer, prompt: str, raw_text: str,
                            pass_name: str, max_tokens: int,
                            timeout: int | None = None,
                            max_text_len: int = 0) -> dict:
        """Make a single AI call for one extraction pass.

        Uses the shared _call_api which handles transient retries internally
        and fails fast on billing/auth errors (401/402/403).

        Args:
            max_text_len: if > 0, truncate raw_text to this many chars
                          (keeps the HEAD of text, since both structural and
                           data sections are typically at the beginning).

        Returns the parsed JSON dict, or {} on failure.
        """
        from services.llm_policy import get_llm_policy
        timeout = timeout or ReportExtractor.TIMEOUT_SEC
        model = get_llm_policy("structured_extraction").model

        # Truncate text if requested
        text = raw_text
        if max_text_len > 0 and len(text) > max_text_len:
            text = text[:max_text_len]

        try:
            result = await reviewer._call_api(
                prompt=f"文档内容：\n{text}",
                stage=pass_name,
                timeout=timeout,
                system_prompt=prompt,
                max_tokens=max_tokens,
                task_kind="structured_extraction",
                max_retries=1,
            )
            if result:
                logger.info("ai_pass_done", pass_name=pass_name,
                           model=model, text_len=len(text))
            return result
        except Exception as e:
            if _is_billing_or_auth_error(e):
                raise  # propagate billing/auth errors immediately
            logger.warning("ai_pass_failed", pass_name=pass_name, error=str(e)[:200])
            return {}

    @staticmethod
    def _parse_structural(result: dict) -> tuple[ReportCoverInfo, list[ReportResultItem],
                                                  ReportSampleInfo, dict]:
        """Parse Pass 1 structural AI result into typed models + meta."""
        cover_dict = _translate_keys(result.get("封面", {}), _COVER_MAP)
        if "检测依据" in result.get("封面", {}):
            raw_standards = result["封面"]["检测依据"]
            cover_dict["test_standards"] = (
                [raw_standards] if isinstance(raw_standards, str) else raw_standards
            )
        sample_dict = _translate_keys(result.get("样品描述", {}), _SAMPLE_MAP)
        for list_field, zh_name in [
            ("serial_numbers", "样品序列号"), ("lab_sample_ids", "实验室样品编号"),
        ]:
            val = result.get("样品描述", {}).get(zh_name, [])
            sample_dict[list_field] = [val] if isinstance(val, str) else (val or [])

        cover = ReportCoverInfo(**cover_dict)
        results = [ReportResultItem(**_translate_keys(r, _RESULT_MAP))
                   for r in result.get("测试结果", [])]
        sample = ReportSampleInfo(**sample_dict)
        meta = result.get("_meta", {}) if isinstance(result.get("_meta"), dict) else {}
        return cover, results, sample, meta

    @staticmethod
    def _parse_instruments(result: dict) -> tuple[list[ReportInstrument], dict]:
        """Parse Pass 2 instruments AI result into typed models + meta."""
        instruments = [ReportInstrument(**_translate_keys(i, _INSTRUMENT_MAP))
                       for i in result.get("测试仪器", [])]
        meta = result.get("_meta", {}) if isinstance(result.get("_meta"), dict) else {}
        return instruments, meta

    @staticmethod
    def _parse_data_rows(result: dict) -> list[ReportDataRow]:
        """Parse Pass 3 data rows AI result into typed models."""
        return [ReportDataRow(**_translate_keys(d, _DATA_ROW_MAP))
                for d in result.get("测试数据", [])]

    @staticmethod
    def _parse_item_result(result: dict, pass_name: str = "") -> TestItemExtraction | None:
        """Parse a per-item universal extraction AI result into TestItemExtraction."""
        if not result or not isinstance(result, dict):
            return None
        test_type = result.get("test_type", "").lower()
        if test_type not in ("emission", "immunity"):
            # Try to infer from available fields
            if result.get("限值") or result.get("测试结果", {}).get("背景数据"):
                test_type = "emission"
            elif result.get("测试规范"):
                test_type = "immunity"
            else:
                test_type = "immunity"  # default

        # Parse limit entries
        limit_entries: list[LimitEntry] = []
        limit_data = result.get("限值") or {}
        if isinstance(limit_data, dict):
            for entry in (limit_data.get("entries") or []):
                if isinstance(entry, dict):
                    limit_entries.append(LimitEntry(
                        freq_range=str(entry.get("频段", "")),
                        peak_limit=_safe_float(entry.get("峰值")),
                        avg_limit=_safe_float(entry.get("平均值")),
                        qp_limit=_safe_float(entry.get("准峰值")),
                        unit=str(limit_data.get("unit", "")),
                    ))

        # Parse test results section
        results_data = result.get("测试结果") or {}
        results_section = TestResultsSection()

        if isinstance(results_data, dict):
            # Background data
            for row in (results_data.get("背景数据") or []):
                if isinstance(row, dict):
                    results_section.background_data.append(UniversalDataRow(
                        freq_mhz=str(row.get("频率(MHz)", "")),
                        reading=str(row.get("读值(dBuA)", "")),
                        correction_db=str(row.get("修正因子(dB)", "")),
                        result_dbua=str(row.get("结果(dBuA)", "")),
                        limit_dbua=str(row.get("限值(dBuA)", "")),
                        margin_db=str(row.get("余量(dB)", "")),
                        note=str(row.get("备注", "")),
                    ))

            # Sample data blocks
            for block in (results_data.get("样品数据") or []):
                if not isinstance(block, dict):
                    continue
                sample_block = SampleResultBlock(
                    sample_id=str(block.get("样品编号", "")),
                    mode=str(block.get("模式", "")),
                    test_method=str(block.get("测试方法", "")),
                )
                for row in (block.get("数据行") or []):
                    if isinstance(row, dict):
                        sample_block.data_rows.append(UniversalDataRow(
                            freq_mhz=str(row.get("频率(MHz)", "")),
                            reading=str(row.get("读值(dBuA)", "")),
                            correction_db=str(row.get("修正因子(dB)", "")),
                            result_dbua=str(row.get("结果(dBuA)", "")),
                            limit_dbua=str(row.get("限值(dBuA)", "")),
                            margin_db=str(row.get("余量(dB)", "")),
                            test_item=str(row.get("测试项目", "")),
                            injection_point=str(row.get("注入位置", "")),
                            spec_requirement=str(row.get("规范要求", "")),
                            test_duration=str(row.get("测试时间", "")),
                            required_level=str(row.get("要求等级", "")),
                            actual_level=str(row.get("实际等级", "")),
                            verdict=str(row.get("结果", "")),
                            note=str(row.get("备注", "")),
                        ))
                results_section.sample_data.append(sample_block)

        code = pass_name.replace("item/", "") if pass_name.startswith("item/") else ""

        return TestItemExtraction(
            test_type=test_type,
            test_item_code=code or str(result.get("test_item_code", "")),
            test_item_name=str(result.get("test_item_name", "")),
            limit_type=str(limit_data.get("limit_type", "") if isinstance(limit_data, dict) else ""),
            limit_unit=str(limit_data.get("unit", "") if isinstance(limit_data, dict) else ""),
            limit_entries=limit_entries,
            spec_parameters=(result.get("测试规范") or {}).get("parameters", []) if isinstance(result.get("测试规范"), dict) else [],
            required_level=str((result.get("测试规范") or {}).get("required_level", "")) if isinstance(result.get("测试规范"), dict) else "",
            test_procedures=result.get("测试程序") or [],
            test_results=results_section,
        )

    @staticmethod
    def _items_to_data_rows(items: list[TestItemExtraction]) -> list[ReportDataRow]:
        """Convert v3 TestItemExtraction list to legacy ReportDataRow list.

        NOTE: ReportDataRow only has emission fields (freq_mhz, reading,
        correction_db, result_dbua, limit_dbua, margin_db).  Immunity-specific
        fields (required_level, actual_level, verdict, injection_point) are
        encoded in the ``note`` field for traceability.  The v3 validation
        pipeline uses TestItemExtraction directly, so no data is lost there.
        """
        rows: list[ReportDataRow] = []
        for item in items:
            for block in item.test_results.sample_data:
                for row in block.data_rows:
                    # Build note with immunity context if present
                    note_parts = [row.note] if row.note else []
                    if row.required_level or row.actual_level or row.verdict:
                        immunity_note = f"L:{row.required_level}/{row.actual_level}→{row.verdict}"
                        if row.injection_point:
                            immunity_note += f"@{row.injection_point}"
                        note_parts.append(immunity_note)
                    rows.append(ReportDataRow(
                        test_item=row.test_item or item.test_item_code,
                        freq_mhz=row.freq_mhz,
                        reading=row.reading,
                        correction_db=row.correction_db,
                        result_dbua=row.result_dbua,
                        limit_dbua=row.limit_dbua,
                        margin_db=row.margin_db,
                        note='; '.join(note_parts),
                    ))
            for row in item.test_results.background_data:
                rows.append(ReportDataRow(
                    test_item=f"{item.test_item_code} (背景)",
                    freq_mhz=row.freq_mhz,
                    reading=row.reading,
                    correction_db=row.correction_db,
                    result_dbua=row.result_dbua,
                    limit_dbua=row.limit_dbua,
                    margin_db=row.margin_db,
                    note=row.note,
                ))
        return rows

    @staticmethod
    def _group_test_codes(codes: list[str]) -> dict[str, list[str]]:
        """Group test item codes by their category prefix (MC/IC/IR/MR/OTHER).

        Example: ["EQ/MC01","EQ/MC03","EQ/IC01"] → {"MC":[...], "IC":[...]}
        """
        groups: dict[str, list[str]] = {}
        for code in codes:
            # Extract the 2-letter prefix after "EQ/"
            if len(code) >= 5 and code[3:5].isalpha():
                prefix = code[3:5].upper()
            else:
                prefix = "OTHER"
            groups.setdefault(prefix, []).append(code)
        return groups

    @staticmethod
    def _deduplicate_instruments(instruments: list[ReportInstrument]) -> list[ReportInstrument]:
        """Deduplicate instruments by (manufacturer, model, serial_no).

        Keeps the entry with the latest calibration_end date when duplicates
        are found.  This matches the logic in raw_records_instrument_extractor.
        """
        key_map: dict[tuple[str, str, str], ReportInstrument] = {}
        for inst in instruments:
            key = (inst.manufacturer.strip().upper(),
                   inst.model.strip().upper(),
                   inst.serial_no.strip().upper())
            if key in key_map:
                if inst.calibration_end > key_map[key].calibration_end:
                    key_map[key] = inst
            else:
                key_map[key] = inst
        if len(instruments) > len(key_map):
            logger.info("instruments_dedup", before=len(instruments),
                       after=len(key_map), dropped=len(instruments) - len(key_map))
        return list(key_map.values())

    @staticmethod
    def _deduplicate_data_rows(rows: list[ReportDataRow]) -> list[ReportDataRow]:
        """Deduplicate data rows by (test_item, freq_mhz, reading, correction_db)."""
        seen: set[tuple[str, str, str, str]] = set()
        unique: list[ReportDataRow] = []
        for row in rows:
            key = (row.test_item, row.freq_mhz, row.reading, row.correction_db)
            if key not in seen:
                seen.add(key)
                unique.append(row)
        if len(rows) > len(unique):
            logger.info("data_rows_dedup", before=len(rows), after=len(unique),
                       dropped=len(rows) - len(unique))
        return unique

    @staticmethod
    def _aggregate(cover: ReportCoverInfo, results: list[ReportResultItem],
                   sample: ReportSampleInfo, instruments: list[ReportInstrument],
                   all_data_rows: list[ReportDataRow], toc_items: list[ReportTocItem],
                   struct_meta: dict, instr_meta: dict,
                   raw_text: str) -> ReportData:
        """Merge all passes into a single ReportData, deduplicating data rows
        and instruments."""
        unique_rows = ReportExtractor._deduplicate_data_rows(all_data_rows)
        unique_instruments = ReportExtractor._deduplicate_instruments(instruments)

        # Merge metas: structural meta takes precedence for overlapping keys
        merged_meta = {**instr_meta, **struct_meta}

        return ReportData(
            cover=cover, results=results, sample=sample,
            instruments=instruments,
            deduplicated_instruments=unique_instruments,
            data_rows=unique_rows,
            toc=toc_items, meta=merged_meta, raw_text=raw_text,
        )

    # ── Code-level validation (deterministic, no AI) ────────────────────

    @staticmethod
    def _item_sample_coverage_gap(
        raw_text: str,
        items: list[TestItemExtraction],
    ) -> tuple[int, int, int]:
        """Compare explicit source sample IDs with per-item extracted blocks."""
        source_ids = {match.group(0).casefold() for match in _LAB_SAMPLE_ID_RE.finditer(raw_text)}
        extracted_ids = {
            block.sample_id.strip().casefold()
            for item in items
            for block in item.test_results.sample_data
            if block.sample_id.strip()
        }
        return len(source_ids), len(extracted_ids), len(source_ids - extracted_ids)

    @staticmethod
    def _compact_docx_cells(cells) -> list[str]:
        """Return visible cell values without merged-cell repetitions."""
        values: list[str] = []
        previous_tc = None
        for cell in cells:
            # python-docx returns the same underlying ``w:tc`` once for every
            # grid column covered by a horizontal merge.  Compare cell
            # identity instead of text: two real adjacent columns are allowed
            # to contain the same value (for example required=C, actual=C).
            if cell._tc is previous_tc:
                continue
            previous_tc = cell._tc
            value = re.sub(r"\s+", " ", str(cell.text or "")).strip()
            if value:
                values.append(value)
        return values

    @staticmethod
    def _docx_label_value(rows: list[list[str]], labels: tuple[str, ...]) -> str:
        normalized_labels = {
            re.sub(r"[\s:：.]+", "", label).casefold() for label in labels
        }
        for row in rows:
            for index, value in enumerate(row):
                normalized = re.sub(r"[\s:：.]+", "", value).casefold()
                if normalized not in normalized_labels:
                    continue
                for candidate in row[index + 1:]:
                    candidate_normalized = re.sub(
                        r"[\s:：.]+", "", candidate,
                    ).casefold()
                    if candidate_normalized not in normalized_labels:
                        return candidate
        return ""

    @staticmethod
    def _extract_docx_table_items(
        file_bytes: bytes,
        summary_results: list[ReportResultItem],
    ) -> tuple[list[TestItemExtraction], dict[str, int]]:
        """Deterministically transcribe repeated DOCX execution tables.

        Many laboratory reports repeat a small identity table followed by a
        result table.  LLM section extraction is still used for prose and test
        specifications, but it must not be the only counter of executions.
        This parser is schema-driven (header labels), so it is independent of
        a vendor's item names and preserves duplicate sample/mode blocks.
        """
        if not file_bytes.startswith(b"PK"):
            return [], {}
        try:
            from docx import Document
            document = Document(io.BytesIO(file_bytes))
        except Exception as exc:
            logger.warning(
                "docx_execution_inventory_unavailable",
                error_type=type(exc).__name__,
            )
            return [], {}

        blocks: list[tuple[str, str, str, list[UniversalDataRow], int]] = []
        for table_index, table in enumerate(document.tables[:-1]):
            identity_rows = [
                ReportExtractor._compact_docx_cells(row.cells)
                for row in table.rows
            ]
            sample_id = ReportExtractor._docx_label_value(
                identity_rows, ("Sample No.", "Sample No", "样品编号"),
            )
            mode = ReportExtractor._docx_label_value(
                identity_rows, ("Test Mode", "测试模式", "工作模式"),
            )
            if not _LAB_SAMPLE_ID_RE.fullmatch(sample_id.strip()) or not mode:
                continue

            result_table = document.tables[table_index + 1]
            result_rows = [
                ReportExtractor._compact_docx_cells(row.cells)
                for row in result_table.rows
            ]
            if not result_rows:
                continue
            header = " ".join(result_rows[0]).casefold()
            header_cells = {
                re.sub(r"\s+", "", value).casefold()
                for value in result_rows[0]
            }
            if not (
                ("test item" in header or "测试项目" in header)
                and (
                    "result" in header
                    or "测试结果" in header
                    or "试验结果" in header
                    or "结果" in header_cells
                    or "判定" in header_cells
                )
                and ("performance" in header or "性能等级" in header)
            ):
                continue

            rows: list[UniversalDataRow] = []
            row_item_names: list[str] = []
            for values in result_rows[1:]:
                if len(values) < 7:
                    continue
                verdict = values[-1].strip()
                if not re.search(
                    r"(?i)(?:不\s*)?pass|fail|符合|不符合|合格|不合格|\bng\b|\bok\b",
                    verdict,
                ):
                    continue
                item_name = values[0].strip()
                row_item_names.append(item_name)
                rows.append(UniversalDataRow(
                    test_item=item_name,
                    injection_point=values[1].strip(),
                    spec_requirement=values[2].strip(),
                    test_duration=values[3].strip(),
                    required_level=values[4].strip(),
                    actual_level=values[5].strip(),
                    verdict=verdict,
                ))
            if not rows:
                continue
            signature = "\x1f".join(dict.fromkeys(
                re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", name.casefold())
                for name in row_item_names if name
            ))
            blocks.append((sample_id.strip(), mode.strip(), signature, rows, table_index))

        if not blocks:
            return [], {}

        # Consecutive execution blocks with the same row schema belong to one
        # result chapter.  This also preserves an accidental duplicate block
        # as evidence instead of silently deduplicating it.
        groups: list[list[tuple[str, str, str, list[UniversalDataRow], int]]] = []
        for block in blocks:
            if groups and groups[-1][0][2] == block[2]:
                groups[-1].append(block)
            else:
                groups.append([block])

        items: list[TestItemExtraction] = []
        previous_result_table_index = -1
        for index, group in enumerate(groups):
            fallback_name = group[0][3][0].test_item
            summary_name = (
                summary_results[index].test_item.strip()
                if len(summary_results) == len(groups) else ""
            )
            item_name = summary_name or fallback_name
            sample_data = [
                SampleResultBlock(
                    sample_id=sample_id,
                    mode=mode,
                    data_rows=rows,
                )
                for sample_id, mode, _, rows, _ in group
            ]
            first_identity_table_index = group[0][4]
            prelude_tables = document.tables[
                previous_result_table_index + 1:first_identity_table_index
            ]
            previous_result_table_index = group[-1][4] + 1
            spec_parameters: list[dict[str, str]] = []
            required_levels: set[str] = set()
            for prelude_table in prelude_tables:
                prelude_rows = [
                    ReportExtractor._compact_docx_cells(row.cells)
                    for row in prelude_table.rows
                ]
                header_text = " ".join(prelude_rows[0] if prelude_rows else []).casefold()
                if any(token in header_text for token in (
                    "manufacturer", "serial number", "calibration", "制造商", "系列号", "校准",
                )):
                    continue
                all_table_text = " ".join(
                    value for row in prelude_rows for value in row
                ).casefold()
                is_spec_table = any(token in all_table_text for token in (
                    "test requirement", "requirement", "functional status",
                    "functional classes", "severity level", "offset voltage",
                    "start voltage", "test time", "测试要求", "性能等级",
                )) or bool(
                    prelude_rows and prelude_rows[0]
                    and prelude_rows[0][0].strip().casefold() in {"un", "ua"}
                )
                if not is_spec_table:
                    continue
                severity_table = "severity" in header_text and any(
                    "upp" in value.casefold() for value in (prelude_rows[0] if prelude_rows else [])
                )
                for values in prelude_rows[1:] if prelude_rows else []:
                    if len(values) < 2:
                        continue
                    last_value = values[-1].strip()
                    if (
                        "functional" in header_text or "性能" in header_text
                        or any(token in values[0].casefold() for token in (
                            "function status", "functional status", "性能等级",
                        ))
                    ):
                        required_levels.update(
                            value.upper() for value in re.findall(
                                r"(?i)(?<![A-Z])([A-E])(?![A-Z])", last_value,
                            )
                        )
                    if severity_table and re.fullmatch(r"\d+", values[0]):
                        numeric_values = [
                            re.sub(r"\s+", "", match.group(0))
                            for match in _NUMBER_UNIT_RE.finditer(last_value)
                        ]
                        if numeric_values:
                            spec_parameters.append({
                                "name": f"Severity {values[0]}",
                                "value": numeric_values[0],
                            })
                        continue
                    if (
                        len(values) >= 4
                        and "requirement" in header_text
                        and "functional" in header_text
                    ):
                        name = values[1].strip()
                        parameter_value = values[2].strip()
                    elif (
                        len(values) >= 3
                        and "functional" in header_text
                        and re.search(r"[:：]", values[0])
                    ):
                        name, parameter_value = re.split(
                            r"[:：]", values[0], maxsplit=1,
                        )
                        name = name.strip()
                        parameter_value = parameter_value.strip()
                    else:
                        name = values[0].strip()
                        parameter_value = last_value
                    if (
                        not name
                        or re.fullmatch(r"\d+", name)
                        or name.casefold() in {
                            "test item", "item", "test requirement", "requirement",
                            "functional status", "functional classes",
                        }
                    ):
                        continue
                    numeric_values = [
                        re.sub(r"\s+", "", match.group(0))
                        for match in _NUMBER_UNIT_RE.finditer(parameter_value)
                    ]
                    if numeric_values and len(parameter_value) <= 180:
                        spec_parameters.append({
                            "name": name,
                            "value": parameter_value,
                        })
            deduplicated_parameters: list[dict[str, str]] = []
            seen_parameters: set[tuple[str, str]] = set()
            for parameter in spec_parameters:
                key = (parameter["name"].casefold(), parameter["value"].casefold())
                if key not in seen_parameters:
                    seen_parameters.add(key)
                    deduplicated_parameters.append(parameter)
            spec_parameters = deduplicated_parameters
            items.append(TestItemExtraction(
                test_type="immunity",
                test_item_code=f"DOCX_TABLE_{index + 1}",
                test_item_name=item_name,
                spec_parameters=spec_parameters,
                required_level=(
                    next(iter(required_levels)) if len(required_levels) == 1 else ""
                ),
                test_results=TestResultsSection(sample_data=sample_data),
            ))

        pair_counts: dict[tuple[str, str], int] = defaultdict(int)
        for sample_id, mode, _, _, _ in blocks:
            pair_counts[(sample_id.casefold(), mode.casefold())] += 1
        metrics = {
            "docx_execution_block_count": len(blocks),
            "docx_execution_pair_count": len(pair_counts),
            "docx_duplicate_execution_pair_count": sum(
                1 for count in pair_counts.values() if count > 1
            ),
            "docx_execution_group_count": len(groups),
        }
        return items, metrics

    @staticmethod
    def _extract_docx_instruments(
        file_bytes: bytes,
    ) -> tuple[list[ReportInstrument], dict[str, int]]:
        """Transcribe instrument inventories from labelled DOCX tables.

        A group row containing only a test-item name applies to the physical
        instrument rows that follow it.  The parser depends on column roles,
        not on a laboratory's item names or a fixed table index.
        """
        if not file_bytes.startswith(b"PK"):
            return [], {}
        try:
            from docx import Document
            document = Document(io.BytesIO(file_bytes))
        except Exception as exc:
            logger.warning(
                "docx_instrument_inventory_unavailable",
                error_type=type(exc).__name__,
            )
            return [], {}

        instruments: list[ReportInstrument] = []
        physical_count = 0
        for table in document.tables:
            rows = [
                ReportExtractor._compact_docx_cells(row.cells)
                for row in table.rows
            ]
            if not rows:
                continue
            header = [value.casefold() for value in rows[0]]
            joined_header = " ".join(header)
            if not (
                ("equipment" in joined_header or "仪器" in joined_header)
                and ("manufacturer" in joined_header or "制造商" in joined_header)
                and (
                    "serial" in joined_header
                    or "编号" in joined_header
                    or "系列号" in joined_header
                    or "序列号" in joined_header
                )
                and ("calibration" in joined_header or "校准" in joined_header)
            ):
                continue
            current_item = ""
            for values in rows[1:]:
                if len(values) == 1:
                    current_item = values[0].strip()
                    continue
                if len(values) < 5:
                    continue
                entry = ReportInstrument(
                    test_item=current_item,
                    name=values[0].strip(),
                    manufacturer=values[1].strip(),
                    model=values[2].strip(),
                    serial_no=values[3].strip(),
                    calibration_end=values[4].strip(),
                )
                if not entry.name or not current_item:
                    continue
                instruments.append(entry)
                if all(
                    value and value not in {"/", "-"}
                    for value in (
                        entry.manufacturer, entry.model, entry.serial_no,
                        entry.calibration_end,
                    )
                ):
                    physical_count += 1
        return instruments, {
            "docx_instrument_row_count": len(instruments),
            "docx_physical_instrument_row_count": physical_count,
        }

    @staticmethod
    def _merge_docx_instruments(
        llm_instruments: list[ReportInstrument],
        table_instruments: list[ReportInstrument],
    ) -> list[ReportInstrument]:
        """Use complete source tables when available, without duplicating rows."""
        if not table_instruments:
            return llm_instruments
        table_keys = {
            tuple(str(getattr(item, field) or "").strip().casefold() for field in (
                "test_item", "name", "manufacturer", "model", "serial_no",
                "calibration_end",
            ))
            for item in table_instruments
        }
        extras = [
            item for item in llm_instruments
            if tuple(str(getattr(item, field) or "").strip().casefold() for field in (
                "test_item", "name", "manufacturer", "model", "serial_no",
                "calibration_end",
            )) not in table_keys
        ]
        return [*table_instruments, *extras]

    @staticmethod
    def _merge_docx_table_items(
        llm_items: list[TestItemExtraction],
        table_items: list[TestItemExtraction],
    ) -> list[TestItemExtraction]:
        """Keep LLM prose/spec fields but make source tables authoritative."""
        if not table_items:
            return llm_items
        merged: list[TestItemExtraction] = []
        used: set[int] = set()
        for table_item in table_items:
            table_key = re.sub(
                r"[^0-9a-z\u4e00-\u9fff]+", "",
                table_item.test_item_name.casefold(),
            )
            match_index = next((
                index for index, item in enumerate(llm_items)
                if index not in used and re.sub(
                    r"[^0-9a-z\u4e00-\u9fff]+", "",
                    item.test_item_name.casefold(),
                ) == table_key
            ), None)
            if match_index is None and len(llm_items) == len(table_items):
                match_index = len(merged)
            if match_index is None or match_index >= len(llm_items):
                merged.append(table_item)
                continue
            used.add(match_index)
            item = llm_items[match_index]
            item.test_results.sample_data = table_item.test_results.sample_data
            if table_item.spec_parameters:
                item.spec_parameters = table_item.spec_parameters
            if table_item.required_level:
                item.required_level = table_item.required_level
            if not item.test_item_name:
                item.test_item_name = table_item.test_item_name
            merged.append(item)
        merged.extend(item for index, item in enumerate(llm_items) if index not in used)
        return merged

    @staticmethod
    def _validate_emission_formula(items: list[TestItemExtraction]) -> list[ValidationIssue]:
        """Check: result = reading + correction, margin = limit - result."""
        issues: list[ValidationIssue] = []
        for item in items:
            if item.test_type != "emission":
                continue
            for block in item.test_results.sample_data:
                for i, row in enumerate(block.data_rows):
                    try:
                        reading = float(row.reading) if row.reading else None
                        correction = float(row.correction_db) if row.correction_db else 0.0
                        result = float(row.result_dbua) if row.result_dbua else None
                        limit = float(row.limit_dbua) if row.limit_dbua else None
                        margin = float(row.margin_db) if row.margin_db else None

                        # Check result = reading + correction
                        if reading is not None and result is not None:
                            expected = reading + correction
                            if abs(expected - result) > 0.15:
                                issues.append(ValidationIssue(
                                    check_id="C03",
                                    severity="WARNING",
                                    category="formula",
                                    field_name="result_dbua",
                                    test_item_code=item.test_item_code,
                                    description=f"结果公式异常: 读值+修正={expected:.2f}, 报告值={result:.2f}",
                                    expected=str(round(expected, 2)),
                                    actual=str(result),
                                    row_index=i,
                                ))

                        # Check margin = limit - result
                        if limit is not None and result is not None and margin is not None:
                            expected_margin = round(limit - result, 2)
                            if abs(expected_margin - margin) > 0.15:
                                issues.append(ValidationIssue(
                                    check_id="C03",
                                    severity="WARNING",
                                    category="formula",
                                    field_name="margin_db",
                                    test_item_code=item.test_item_code,
                                    description=f"余量公式异常: 限值-结果={expected_margin:.2f}, 报告值={margin:.2f}",
                                    expected=str(expected_margin),
                                    actual=str(margin),
                                    row_index=i,
                                ))
                    except (ValueError, TypeError):
                        pass
        return issues

    @staticmethod
    def _validate_background_noise(items: list[TestItemExtraction]) -> list[ValidationIssue]:
        """Check: background noise margin >= 6dB below limit."""
        issues: list[ValidationIssue] = []
        for item in items:
            for row in item.test_results.background_data:
                try:
                    margin = float(row.margin_db) if row.margin_db else None
                    if margin is not None and margin < 6.0:
                        issues.append(ValidationIssue(
                            check_id="C05",
                            severity="WARNING",
                            category="background_noise",
                            field_name="margin_db",
                            test_item_code=item.test_item_code,
                            description=f"背景噪声余量不足6dB: {margin}dB < 6dB",
                            expected="≥6dB",
                            actual=f"{margin}dB",
                        ))
                except (ValueError, TypeError):
                    pass
        return issues

    @staticmethod
    def _validate_level_consistency(items: list[TestItemExtraction]) -> list[ValidationIssue]:
        """Check: actual level >= required level consistent with verdict."""
        issues: list[ValidationIssue] = []
        _level_order = {'A': 5, 'B': 4, 'C': 3, 'D': 2, 'E': 1}

        for item in items:
            if item.test_type != "immunity":
                continue
            for block in item.test_results.sample_data:
                for i, row in enumerate(block.data_rows):
                    required = row.required_level.strip().upper()
                    actual = row.actual_level.strip().upper()
                    verdict = row.verdict.strip()

                    if not required or not actual:
                        continue

                    req_rank = _level_order.get(required, -1)
                    act_rank = _level_order.get(actual, -1)

                    if req_rank < 0 or act_rank < 0:
                        continue

                    # actual >= required but verdict says "不符合"
                    if act_rank >= req_rank and verdict in ('不符合', '不合格', 'FAIL', 'NG'):
                        issues.append(ValidationIssue(
                            check_id="C07",
                            severity="WARNING",
                            category="level_consistency",
                            field_name="verdict",
                            test_item_code=item.test_item_code,
                            description=f"等级一致性矛盾: 实际等级{actual}≥要求等级{required}, 但判定为{verdict}",
                            expected=f"符合（当前实际{actual}≥要求{required}）",
                            actual=f"{verdict}",
                            row_index=i,
                        ))

                    # actual < required but verdict says "符合"
                    if act_rank < req_rank and verdict in ('符合', '合格', 'PASS', 'OK'):
                        issues.append(ValidationIssue(
                            check_id="C07",
                            severity="CRITICAL" if act_rank < req_rank - 1 else "WARNING",
                            category="level_consistency",
                            field_name="verdict",
                            test_item_code=item.test_item_code,
                            description=f"等级一致性矛盾: 实际等级{actual}<要求等级{required}, 但判定为{verdict}",
                            expected=f"不符合（当前实际{actual}<要求{required}）",
                            actual=f"{verdict}",
                            row_index=i,
                        ))
        return issues

    @staticmethod
    def _run_code_validation(items: list[TestItemExtraction]) -> list[ValidationIssue]:
        """Run all deterministic code-level validations."""
        issues: list[ValidationIssue] = []
        issues.extend(ReportExtractor._validate_emission_formula(items))
        issues.extend(ReportExtractor._validate_background_noise(items))
        issues.extend(ReportExtractor._validate_level_consistency(items))
        logger.info("code_validation_done",
                   total_issues=len(issues),
                   item_count=len(items))
        return issues

    # ── Main extraction entry point ─────────────────────────────────────

    @staticmethod
    async def extract(file_bytes: bytes, filename: str = "", reviewer=None) -> ReportData:
        """Multi-pass extraction with zero data omission.

        Pass 0 (code): TOC extraction via regex.
        Pass 1 (AI):   structural — cover, results, sample, serial.
        Pass 2 (AI):   all instruments — parallel with Pass 3.
        Pass 3 (AI):   data rows — grouped by test-code prefix, N parallel calls.
        Aggregate:     merge + deduplicate, save once.
        """
        import asyncio

        if len(file_bytes) > MAX_FILE_SIZE:
            raise SizeLimitError("report.pdf", len(file_bytes))
        try:
            raw_text = _extract_file_text(file_bytes, filename) if filename else extract_pdf_text(file_bytes)
        except Exception as e:
            raise ExtractionError(f"文档文本提取失败: {e}") from e
        if not raw_text.strip():
            raise ExtractionError("文档文本提取为空")

        if reviewer is None:
            from services.deepseek_client import DeepSeekReviewer
            reviewer = DeepSeekReviewer()

        from services.llm_policy import get_llm_policy
        from services.prompts import (
            REPORT_STRUCTURAL_PROMPT, REPORT_INSTRUMENTS_PROMPT,
            build_data_rows_prompt,
        )
        t0 = time.time()
        log = logger.bind(doc_type="report",
                         model=get_llm_policy("structured_extraction").model,
                         text_length=len(raw_text))

        log.info("multi_pass_extract_start")

        # ── Pass 0: Deterministic TOC ──────────────────────────────────
        toc_items = ReportExtractor._extract_toc(raw_text)
        toc_codes = [t.code for t in toc_items] if toc_items else []

        # ── Prepare text for structural + instruments passes ──────────
        # Front matter: first 20K chars for cover/sample info
        struct_text = raw_text[:ReportExtractor._STRUCTURAL_MAX_TEXT]

        # Instrument text: find instrument section
        instr_m = ReportExtractor._INSTRUMENT_HEADER_RE.search(raw_text)
        if instr_m:
            instr_start = max(0, instr_m.start() - 500)
            instr_end = min(len(raw_text), instr_m.end() + 40000)
            instr_text = raw_text[instr_start:instr_end]
        else:
            instr_text = raw_text[:ReportExtractor._INSTRUMENT_MAX_TEXT]
        if len(instr_text) > ReportExtractor._INSTRUMENT_MAX_TEXT:
            instr_text = instr_text[:ReportExtractor._INSTRUMENT_MAX_TEXT]

        # ── Pass 1: Structural (serial, gates further processing) ─────
        p1_start = time.time()
        struct_result = await ReportExtractor._call_ai_pass(
            reviewer, REPORT_STRUCTURAL_PROMPT, struct_text,
            pass_name="structural", max_tokens=16384,
        )
        p1_dur = round((time.time() - p1_start) * 1000)

        if not struct_result or (not struct_result.get("封面") and not struct_result.get("测试结果")):
            log.error("structural_pass_failed", duration_ms=p1_dur)
            raise ExtractionError(
                "检测报告结构提取失败（封面+测试结果为空）",
                [{"errors": ["Pass 1 (structural) returned empty result"]}],
            )

        try:
            cover, results, sample, struct_meta = ReportExtractor._parse_structural(struct_result)
        except Exception as e:
            log.error("structural_parse_failed", error=str(e))
            raise ExtractionError(f"检测报告结构数据解析失败: {e}") from e

        # Draft/template reports can intentionally leave both identity fields
        # blank while still containing a complete result body.  Treat that as
        # a partial extraction so downstream document checks can report the
        # missing/placeholder cover fields with source evidence.  Rejecting the
        # whole document here made those checks unreachable.
        cover_identity_absent = not cover.report_number and not cover.sample_name
        cover_identity_missing = not cover.report_number or not cover.sample_name
        if cover_identity_absent and not results:
            raise ExtractionError(
                "封面信息和测试结果均为空（AI 可能未提取到数据）",
                [{"errors": ["封面 report_number、sample_name 和测试结果均为空"]}],
            )
        if cover_identity_missing:
            log.warning(
                "cover_identity_missing",
                duration_ms=p1_dur,
                results_count=len(results),
            )

        log.info("pass_done", pass_name="structural", duration_ms=p1_dur,
                results_count=len(results))

        if not results and any(marker in raw_text for marker in ("检测依据", "测试结果", "试验结果", "测试项目", "试验项目")):
            raise ExtractionError(
                "检测报告结构提取失败：文档包含测试/试验项目线索，但 AI 未提取任何测试结果项",
                [{"errors": ["Pass 1 (structural) returned no test result items"]}],
            )

        # ── Run Pass 2 + per-item extractions in parallel ─────────────
        from config import MAX_CONCURRENT_AI_CALLS
        sem = asyncio.Semaphore(MAX_CONCURRENT_AI_CALLS)

        async def _call_with_semaphore(pass_name: str, prompt: str,
                                       section_text: str,
                                       max_tokens: int, timeout: int | None = None):
            async with sem:
                return pass_name, await ReportExtractor._call_ai_pass(
                    reviewer, prompt, section_text,
                    pass_name=pass_name, max_tokens=max_tokens, timeout=timeout,
                )

        async def _run_pass2():
            return await _call_with_semaphore(
                "instruments", REPORT_INSTRUMENTS_PROMPT, instr_text,
                max_tokens=32768, timeout=120,
            )

        # Per-item extraction tasks
        item_sections = ReportExtractor._split_by_test_item(raw_text, toc_items)
        if not item_sections:
            item_sections = ReportExtractor._split_by_result_item(raw_text, results)

        async def _run_item_extraction(code: str, section_text: str):
            prompt = UNIVERSAL_TEST_ITEM_PROMPT
            return await _call_with_semaphore(
                f"item/{code}", prompt, section_text,
                max_tokens=16384, timeout=90,
            )

        p2_start = time.time()
        # Build tasks: 1 instruments + N per-item
        parallel_tasks = [_run_pass2()]
        for code, section_text in item_sections:
            parallel_tasks.append(_run_item_extraction(code, section_text))

        # If no item sections found, fall back to old code-group approach
        if len(item_sections) == 0:
            log.warning("no_item_sections_fallback",
                       toc_count=len(toc_items),
                       text_len=len(raw_text))
            # Legacy fallback: group by code prefix
            all_codes = toc_codes if toc_codes else sorted(count_test_codes(raw_text))
            code_groups = ReportExtractor._group_test_codes(all_codes)
            for prefix, codes in code_groups.items():
                prompt = build_data_rows_prompt(codes)
                section_text = raw_text[:ReportExtractor._MAX_ITEM_SECTION_CHARS]
                parallel_tasks.append(_call_with_semaphore(
                    f"data_rows/{prefix}", prompt, section_text,
                    max_tokens=16384,
                ))

        parallel_results = await asyncio.gather(*parallel_tasks, return_exceptions=True)
        p2_dur = round((time.time() - p2_start) * 1000)

        # ── Unpack parallel results ─────────────────────────────────────
        all_instruments: list[ReportInstrument] = []
        instr_meta: dict = {}
        all_item_extractions: list[TestItemExtraction] = []
        all_data_rows: list[ReportDataRow] = []
        failed_passes: list[str] = []
        if cover_identity_missing:
            failed_passes.append("structural/cover_identity_missing")

        for item in parallel_results:
            if isinstance(item, Exception):
                logger.error("parallel_pass_exception", error=str(item))
                failed_passes.append(str(item))
                continue
            pass_name, result = item
            if not isinstance(result, dict) or not result:
                failed_passes.append(pass_name)
                continue
            if pass_name == "instruments":
                try:
                    all_instruments, instr_meta = ReportExtractor._parse_instruments(result)
                except Exception:
                    failed_passes.append(pass_name)
            elif pass_name.startswith("item/"):
                # Parse per-item universal extraction result
                try:
                    extraction = ReportExtractor._parse_item_result(result, pass_name)
                    if extraction:
                        all_item_extractions.append(extraction)
                except Exception as e:
                    logger.warning("item_parse_failed", pass_name=pass_name, error=str(e))
                    failed_passes.append(pass_name)
            elif pass_name.startswith("data_rows/"):
                # Legacy fallback
                try:
                    rows = ReportExtractor._parse_data_rows(result)
                    all_data_rows.extend(rows)
                except Exception:
                    failed_passes.append(pass_name)

        # A deterministic DOCX table inventory is the execution-count
        # authority.  LLM output still supplies prose specifications and
        # procedures, but cannot omit repeated sample/mode tables silently.
        table_items, table_metrics = ReportExtractor._extract_docx_table_items(
            file_bytes, results,
        )
        if table_items:
            all_item_extractions = ReportExtractor._merge_docx_table_items(
                all_item_extractions, table_items,
            )
            log.info("docx_execution_inventory_merged", **table_metrics)
        table_instruments, instrument_table_metrics = (
            ReportExtractor._extract_docx_instruments(file_bytes)
        )
        if table_instruments:
            all_instruments = ReportExtractor._merge_docx_instruments(
                all_instruments, table_instruments,
            )
            log.info(
                "docx_instrument_inventory_merged", **instrument_table_metrics,
            )

        # ── Code-level validation on per-item extractions ─────────────
        source_sample_count, extracted_sample_count, missing_sample_count = (
            ReportExtractor._item_sample_coverage_gap(raw_text, all_item_extractions)
        )
        if missing_sample_count:
            failed_passes.append(
                f"item/sample_coverage_missing:{missing_sample_count}"
            )
            log.warning(
                "item_sample_coverage_incomplete",
                source_sample_count=source_sample_count,
                extracted_sample_count=extracted_sample_count,
                missing_sample_count=missing_sample_count,
            )

        code_issues = ReportExtractor._run_code_validation(all_item_extractions)

        # ── Convert item extractions to legacy data_rows for backward compat
        if all_item_extractions and not all_data_rows:
            all_data_rows = ReportExtractor._items_to_data_rows(all_item_extractions)

        if failed_passes:
            log.warning("partial_extraction", failed_passes=failed_passes,
                       instruments_count=len(all_instruments),
                       data_rows_count=len(all_data_rows))

        # ── Validate ────────────────────────────────────────────────────
        data = ReportExtractor._aggregate(
            cover=cover, results=results, sample=sample,
            instruments=all_instruments, all_data_rows=all_data_rows,
            toc_items=toc_items, struct_meta=struct_meta, instr_meta=instr_meta,
            raw_text=raw_text,
        )
        # Attach v3 fields
        data.item_extractions = all_item_extractions
        data.code_validation_issues = code_issues
        data.failed_passes = failed_passes
        data.extraction_quality = "partial" if failed_passes else "complete"
        data.extraction_metrics = {
            "text_length": len(raw_text),
            "cover_fields": sum(1 for v in [cover.report_number, cover.sample_name, cover.client_name] if v),
            "results_count": len(results),
            "instruments_count": len(all_instruments),
            "data_rows_count": len(all_data_rows),
            "item_extractions": len(all_item_extractions),
            "source_sample_count": source_sample_count,
            "extracted_sample_count": extracted_sample_count,
            "missing_sample_count": missing_sample_count,
            **table_metrics,
            **instrument_table_metrics,
            "code_issues": len(code_issues),
            "toc_entries": len(toc_items),
            "failed_passes": failed_passes,
            "p1_duration_ms": p1_dur,
            "p2_parallel_ms": p2_dur,
        }

        errors = validate_report(raw_text, data)

        total_dur = round((time.time() - t0) * 1000)
        data.extraction_metrics["total_duration_ms"] = total_dur
        if errors:
            logger.warning("validation_warnings", errors=errors,
                          total_duration_ms=total_dur, p1_ms=p1_dur, p2_parallel_ms=p2_dur)
        log.info("multi_pass_extract_done",
                cover_fields=sum(1 for v in [cover.report_number, cover.sample_name, cover.client_name] if v),
                results_count=len(results),
                instruments_count=len(all_instruments),
                data_rows_count=len(all_data_rows),
                item_extractions=len(all_item_extractions),
                code_issues=len(code_issues),
                toc_entries=len(toc_items),
                failed_passes=len(failed_passes),
                p1_duration_ms=p1_dur, p2_parallel_ms=p2_dur,
                total_duration_ms=total_dur)

        return data
