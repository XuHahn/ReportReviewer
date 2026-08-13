"""PDF text extraction + header field regex for raw records.

Each PDF is 2-3 pages.  Page 1 = header info + test data table.
Pages 2-3 = instrument list. 18+ regex patterns extract header fields.

Design principle: Chinese text ALWAYS comes from PDF content (pypdf decodes
correctly), NEVER from ZIP filenames (which may be GBK-encoded and garbled
by Python's CP437 fallback). The filename is only used for ASCII/numeric
fields (report_id, test_item_code, sequence, timestamp).
"""

from __future__ import annotations

import io, re
import pypdf
from services.raw_records_filename_parser import RawRecordMeta
from utils.logger import get_logger

logger = get_logger(__name__)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    parts = [p.extract_text() for p in reader.pages]
    return "\n".join(t for t in parts if t)


def preprocess(text: str) -> str:
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _re1(pattern: str, text: str, group: int = 1) -> str | None:
    m = re.search(pattern, text)
    return m.group(group).strip() if m else None


def _checkbox_value(text: str, label: str) -> bool | None:
    """Read the checkbox directly associated with *label*.

    A page can contain several checkboxes. Looking for any checked glyph on the
    page makes every option appear selected, so the glyph and label are matched
    as one local token instead.
    """
    match = re.search(rf'([☑☒■□])\s*{re.escape(label)}', text)
    if not match:
        return None
    return match.group(1) in {"☑", "☒", "■"}


def extract_header_fields(text: str) -> dict:
    """Extract header fields from PDF raw text."""
    proc = preprocess(text)
    raw = text

    f: dict = {}
    f["template_id"] = _re1(r'(GRGJL\.WI\s*-GZEMC\s*-\s*\d{2}\s*-\s*\d{4}\s*\([\d.]+\))', proc)
    m = re.search(r'第(\d+)页\s*/\s*共(\d+)页', proc)
    f["page_info"] = m.group(0) if m else None

    f["applicant"] = _re1(r'委托单位\s*[：:]\s*(.+?)(?=\s*委托单编号)', proc) or ""
    f["report_id_pdf"] = _re1(r'委托单编号\s+(\S+)', proc)
    f["std_ref"] = _re1(r'标准依据\s*[：:]\s*(.+?)(?=\s*测试项目)', proc)
    f["test_item_pdf"] = _re1(r'测试项目\s+(.+?)(?=\s*测试地点)', proc)
    f["test_location"] = _re1(r'测试地点[：:]\s*(\S+)', proc)

    f["temperature"] = _re1(r'温度\s+([\d.]+)℃', proc)
    f["humidity"] = _re1(r'相对湿度\s+([\d]+)%', proc)
    f["pressure"] = _re1(r'大气\s*压力\s+([\d.]+)\s*kPa', proc)

    f["sample_name"] = _re1(r'样品名称\s+(.+?)(?=\s*型号)', proc)
    f["sample_model"] = _re1(r'型号\s+(\S+)', proc)
    f["power_supply"] = _re1(r'供电\s*电源\s+(\S+)', proc)
    f["sample_id"] = _re1(r'样品编号\s+(\S+\s*-\s*\S+)', proc) or _re1(r'样品编号\s+(\S+)', proc) or ""

    f["test_conclusion"] = _re1(r'测试结论\s*[：:]\s*[■□]\s*(\S+)', proc)
    f["tester"] = _re1(r'检测人员\s*/日期\s*(\S+)', proc)
    f["test_date"] = _re1(r'检测人员\s*/日期\s*\S+\s+([\d/]+)', proc)
    f["reviewer"] = _re1(r'校核人员\s*/日期\s*(\S+)', proc)
    f["review_date"] = _re1(r'校核人员\s*/日期\s*\S+\s+([\d-]+)', proc)

    # Test mode: "模式1", "模式2", "工作模式: xxx"
    f["test_mode"] = _re1(r'(模式\s*\d+)', proc) or ""

    # Some templates place the attachment checkboxes on the next physical
    # page. Stop at the repeated page header as well, otherwise page 2 header
    # text becomes part of the page 1 remarks.
    rm = re.search(
        r'备注\s+(.+?)(?=☑|测试结论|GRGJL\.WI|第\s*\d+\s*页\s*/\s*共)',
        proc,
    )
    f["remarks"] = rm.group(1).strip() if rm else ""

    f["photos_saved"] = _checkbox_value(raw, "测试照片")
    f["data_saved"] = _checkbox_value(raw, "测试数据")

    populated = sum(1 for v in f.values() if v)
    logger.info("header_fields_extracted",
                 total_fields=len(f),
                 populated=populated)

    return f


def extract_and_populate(meta: RawRecordMeta, file_bytes: bytes) -> RawRecordMeta:
    reader = pypdf.PdfReader(io.BytesIO(file_bytes))
    page_count = len(reader.pages)
    text = "\n".join(t for p in reader.pages if (t := p.extract_text()))
    text_length = len(text)
    logger.info("pdf_text_extracted",
                 filename=meta.filename,
                 page_count=page_count,
                 text_length=text_length)
    if not text.strip():
        logger.warning("empty_pdf_text", filename=meta.filename)
    meta.raw_text = text
    meta._header_fields = extract_header_fields(meta.raw_text)
    return meta


def _has_cjk(text: str) -> bool:
    """Check if text contains any CJK Unified Ideographs."""
    return any('一' <= c <= '鿿' for c in text)


def override_from_pdf_header(meta: RawRecordMeta) -> None:
    """Override filename-derived Chinese fields with PDF header values.

    ZIP filenames from Chinese Windows are often GBK-encoded but decoded
    as CP437 by Python's zipfile, producing mojibake.  The PDF text itself
    is decoded correctly by pypdf, so the header fields are the
    authoritative source for all Chinese text.

    Overrides: test_item_code, test_item_name, operator, test_mode,
               doc_type, record_timestamp, report_id.
    """
    hf = meta._header_fields
    if not hf:
        return

    overrides = 0

    # ── test_item_code + test_item_name from test_item_pdf ──────────
    item_pdf = hf.get("test_item_pdf", "")
    if item_pdf and _has_cjk(item_pdf):
        # Parse "EQ/IC01" or "EQ/IC 01" (with optional space) or
        # "EQIR01" style codes.  Normalize to e.g. "EQIC01".
        # Also handles items with no EQ code (e.g. "短时中断试验").
        code_m = re.match(r'(EQ/[A-Z]{2})\s*(\d+)', item_pdf)
        if code_m:
            code = code_m.group(1).replace("/", "") + code_m.group(2)
            name = item_pdf[code_m.end():].strip()
            # Strip leading separators/colons
            name = re.sub(r'^[：:，,\s]+', '', name)
            if code:
                meta.test_item_code = code
                from services.raw_records_filename_parser import classify_category
                meta.category = classify_category(code)
                overrides += 1
            if name and _has_cjk(name):
                meta.test_item_name = name
                overrides += 1
        else:
            # No EQ code found — use entire header as test_item_name
            # (e.g. "短时中断试验")
            name = item_pdf.strip()
            if name and _has_cjk(name):
                meta.test_item_name = name
                # Also fix the code if it's garbled (filename-derived)
                if not _has_cjk(meta.test_item_code):
                    meta.test_item_code = name
                overrides += 1

    # ── operator from tester ────────────────────────────────────────
    tester = hf.get("tester", "")
    if tester and _has_cjk(tester):
        meta.operator = tester
        overrides += 1

    # ── test_mode from PDF header ───────────────────────────────────
    mode = hf.get("test_mode", "")
    if mode:
        meta.test_mode = mode
        overrides += 1

    # ── doc_type is always "原始记录" ────────────────────────────────
    meta.doc_type = "原始记录"

    # ── record_timestamp from test_date ─────────────────────────────
    test_date = hf.get("test_date", "")
    if test_date:
        # "2026/5/13" or "2026-05-13" → "20260513"
        digits = re.sub(r'\D', '', test_date)
        if len(digits) == 8:
            meta.record_timestamp = digits
            overrides += 1

    # ── report_id from PDF (more reliable than filename) ────────────
    report_id = hf.get("report_id_pdf", "")
    if report_id:
        meta.report_id = report_id
        overrides += 1

    if overrides:
        logger.info("pdf_header_override",
                     filename=meta.filename,
                     overrides=overrides,
                     test_item_code=meta.test_item_code,
                     test_item_name=meta.test_item_name[:60],
                     operator=meta.operator)


def extract_batch(metas: list[RawRecordMeta], file_map: dict[str, bytes]) -> list[RawRecordMeta]:
    for meta in metas:
        data = file_map.get(meta.filename)
        if data:
            extract_and_populate(meta, data)
    return metas
