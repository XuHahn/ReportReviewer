"""Test plan extraction — 100% AI, 3-layer output, DeepSeek V4.

With V4's 1M context window we send the full document without truncation.
"""

from __future__ import annotations

import io
import re
import time
import json
import unicodedata
import pypdf
from utils.logger import get_logger
from utils.pdf_utils import extract_text as _extract_file_text
from services.prompts import TEST_PLAN_PROMPT
from services.exceptions import ExtractionError, SizeLimitError
from models import TestPlanData, TestPlanBasicInfo, TestPlanItem, TestPlanDetail

logger = get_logger(__name__)

MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB


def _page_aware_text(file_bytes: bytes, filename: str) -> tuple[str, list[tuple[int, str]]]:
    """Return LLM input with explicit physical-page boundaries.

    Test plans have no stable template.  Page markers are therefore semantic
    evidence, not layout rules: they let the model join a table continued on
    the next page and let the evidence gate trace every extracted item back to
    the uploaded file.  Pages without native text are returned for targeted
    visual transcription.
    """
    lower = filename.lower()
    if lower.endswith(".pdf"):
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        chunks: list[str] = []
        scan_pages: list[tuple[int, str]] = []
        for page_number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            chunks.append(f"\n--- 文件第 {page_number} 页 ---\n{text}")
            if len(re.sub(r"\s+", "", text)) < 40:
                scan_pages.append((page_number, text))
        return "".join(chunks).strip(), scan_pages
    return extract_text(file_bytes, filename), []


def _source_anchor(source: str, quote: str) -> bool:
    normalized_source = re.sub(
        r"\s+", "", unicodedata.normalize("NFKC", str(source or "")),
    ).casefold()
    normalized_quote = re.sub(
        r"\s+", "", unicodedata.normalize("NFKC", str(quote or "")),
    ).casefold()
    return bool(normalized_quote) and normalized_quote in normalized_source


async def _transcribe_scan_pages(
    file_bytes: bytes,
    filename: str,
    scan_pages: list[tuple[int, str]],
    gateway=None,
) -> dict[int, str]:
    """Use the configured vision LLM only for pages native parsing cannot read."""
    if not scan_pages or not filename.lower().endswith(".pdf"):
        return {}
    import base64
    import fitz
    from services.unified_model_gateway import UnifiedModelGateway

    model_gateway = gateway or UnifiedModelGateway()
    requested_pages = {page_number for page_number, _ in scan_pages}
    result: dict[int, str] = {}
    render_scale = 2.0
    with fitz.open(stream=file_bytes, filetype="pdf") as pdf:
        for page_number in sorted(requested_pages):
            page = pdf[page_number - 1]
            # Dense scanned tables can exceed a small local model's JSON
            # generation budget.  Two overlapping vertical tiles preserve
            # rows at the split while keeping every response bounded.
            overlap = page.rect.height * 0.05
            midpoint = page.rect.height / 2
            clips = [
                fitz.Rect(page.rect.x0, page.rect.y0, page.rect.x1, midpoint + overlap),
                fitz.Rect(page.rect.x0, midpoint - overlap, page.rect.x1, page.rect.y1),
            ]
            transcriptions: list[str] = []
            for segment_index, clip in enumerate(clips, 1):
                png = page.get_pixmap(
                    matrix=fitz.Matrix(render_scale, render_scale),
                    clip=clip, alpha=False,
                ).tobytes("png")
                image_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                raw = await model_gateway.call_json(
                    "visual_extraction",
                    system_prompt=(
                        "你是测试计划页面的视觉转录员。只逐字转录可见文字和表格，"
                        "保留项目名称、编号、执行状态、条件、次数、判据与跨页表头。"
                        "禁止解释、补写或套用固定模板。只返回JSON："
                        '{"page_text":"完整转录","coverage_state":"complete|partial|failed"}'
                    ),
                    user_prompt=[
                        {"type": "text", "text": (
                            f"转录测试计划文件第 {page_number} 页的第 "
                            f"{segment_index}/2 个重叠区域。"
                        )},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ],
                    stage="test_plan_visual_page",
                    timeout=240,
                    max_tokens=6000,
                )
                segment_text = str(raw.get("page_text") or "").strip()
                if segment_text:
                    transcriptions.append(segment_text)
            if transcriptions:
                result[page_number] = "\n".join(transcriptions)
    return result


def _merge_visual_pages(raw_text: str, visual_pages: dict[int, str]) -> str:
    if not visual_pages:
        return raw_text
    additions = [
        f"\n--- 文件第 {page_number} 页（视觉转录）---\n{text}"
        for page_number, text in sorted(visual_pages.items())
    ]
    return raw_text + "".join(additions)


def _page_segment(raw_text: str, source_location: str) -> str:
    page_match = re.search(r"(?:文件第\s*)?(\d+)\s*页", source_location)
    if not page_match:
        return raw_text
    page_number = int(page_match.group(1))
    markers = list(re.finditer(
        r"(?m)^--- 文件第\s*(\d+)\s*页[^\n]*---\s*$", raw_text,
    ))
    for index, marker in enumerate(markers):
        if int(marker.group(1)) != page_number:
            continue
        end = markers[index + 1].start() if index + 1 < len(markers) else len(raw_text)
        return raw_text[marker.end():end]
    return raw_text


def _located_source_window(source: str, anchors: list[str]) -> str:
    """Return an exact source window around the strongest locatable anchor."""
    best: tuple[int, int] | None = None
    for anchor in anchors:
        normalized_anchor = re.sub(
            r"\s+", "", unicodedata.normalize("NFKC", str(anchor or "")),
        ).casefold()
        if len(normalized_anchor) < 2:
            continue
        normalized_chars: list[str] = []
        positions: list[int] = []
        for position, char in enumerate(source):
            for normalized in unicodedata.normalize("NFKC", char):
                if normalized.isspace():
                    continue
                normalized_chars.append(normalized.casefold())
                positions.append(position)
        offset = "".join(normalized_chars).find(normalized_anchor)
        if offset < 0:
            continue
        start = positions[offset]
        end = positions[offset + len(normalized_anchor) - 1] + 1
        if best is None or end - start > best[1] - best[0]:
            best = (start, end)
    if best is None:
        return ""
    return source[max(0, best[0] - 140):min(len(source), best[1] + 220)].strip()


def _repair_source_evidence(
    raw_text: str, items: list[TestPlanItem], details: list[TestPlanDetail],
) -> int:
    """Replace non-verbatim model citations with exact uploaded-file windows."""
    repaired = 0
    for item in items:
        if item.source_quote and _source_anchor(raw_text, item.source_quote):
            continue
        segment = _page_segment(raw_text, item.source_location)
        quote = _located_source_window(segment, [item.name, item.code])
        if quote:
            item.source_quote = quote
            repaired += 1
    for detail in details:
        if detail.source_quote and _source_anchor(raw_text, detail.source_quote):
            continue
        segment = _page_segment(raw_text, detail.source_location)
        field_values = sorted(
            (value for value in detail.fields.values() if len(str(value).strip()) >= 3),
            key=len, reverse=True,
        )
        quote = _located_source_window(segment, [detail.code, *field_values])
        if quote:
            detail.source_quote = quote
            repaired += 1
    if repaired:
        logger.info("test_plan_source_evidence_repaired", repaired=repaired)
    return repaired


def extract_text(file_bytes: bytes, filename: str) -> str:
    return _extract_file_text(file_bytes, filename)


_TEST_CODE_RE = re.compile(r'EQ/[A-Z]{2}\d+')
_KNOWN_EXECUTION_FLAGS = {
    "y", "yes", "true", "是", "必做", "●", "✓",
    "n", "no", "false", "否", "不适用", "na", "n/a",
}
_ROW_EXECUTION_EVIDENCE_RE = re.compile(
    r"(?im)(?:^|[\t|,，;；])\s*(?:y|yes|true|是|必做|●|✓|n|no|false|否|不适用|n/?a)\s*(?=$|[\t|,，;；])"
)
_GLOBAL_EXECUTION_EVIDENCE_RE = re.compile(
    r"(?i)(?:全部|所有|全项)[^\n\r]{0,20}(?:执行|必做|不适用)|"
    r"(?:执行|必做|不适用)[^\n\r]{0,20}(?:全部|所有|全项)"
)


def count_test_codes(text: str) -> set[str]:
    return set(_TEST_CODE_RE.findall(text))


def validate_extraction(
    raw_text: str,
    data: TestPlanData,
    *,
    require_source_evidence: bool = False,
) -> list[str]:
    errors: list[str] = []
    codes_in_doc = count_test_codes(raw_text)
    codes_from_ai = {
        code
        for item in data.test_items
        for code in count_test_codes(f"{item.code} {item.name}")
    }

    has_plan_table = any(
        marker in raw_text
        for marker in ("测试项目", "试验项目", "试验描述", "验证计划", "测试细则", "接受标准")
    )
    if has_plan_table and not data.test_items:
        errors.append("文档包含测试项目/试验项目表，但 AI 未提取任何测试项")

    if codes_in_doc and codes_in_doc != codes_from_ai:
        missing = codes_in_doc - codes_from_ai
        extra = codes_from_ai - codes_in_doc
        if missing:
            errors.append(f"AI 漏掉 {len(missing)} 个测试项: {sorted(missing)}")
        if extra:
            errors.append(f"AI 多出 {len(extra)} 个测试项: {sorted(extra)}")

    detail_codes = {d.code for d in data.test_details if d.code}
    missing_details = codes_in_doc - detail_codes
    if missing_details:
        # Not every vendor plan has a per-item detail section. Missing details
        # become a quality-gate failure only when the source explicitly contains
        # such a section; otherwise this remains an observable warning.
        if any(marker in raw_text for marker in ("测试细则", "试验细则", "测试详情", "试验详情")):
            errors.append(f"AI 漏掉 {len(missing_details)} 项测试细则: {sorted(missing_details)}")
        logger.warning(
            "test_details_incomplete",
            missing=sorted(missing_details),
            total_in_doc=len(codes_in_doc),
            total_in_details=len(detail_codes),
        )

    if require_source_evidence:
        for index, item in enumerate(data.test_items, 1):
            identity = item.name or item.code
            if not item.source_quote:
                errors.append(f"第 {index} 个测试项“{identity or '未命名'}”缺少原文证据")
            elif not _source_anchor(raw_text, item.source_quote):
                errors.append(f"第 {index} 个测试项“{identity or '未命名'}”的原文证据无法回查")
        for index, detail in enumerate(data.test_details, 1):
            if not detail.source_quote:
                errors.append(f"第 {index} 个测试细则“{detail.code or '未命名'}”缺少原文证据")
            elif not _source_anchor(raw_text, detail.source_quote):
                errors.append(f"第 {index} 个测试细则“{detail.code or '未命名'}”的原文证据无法回查")

    return errors


def _basic_info_filled_count(data: TestPlanData) -> int:
    base = data.basic_info
    return sum(
        1
        for value in [
            base.part_name,
            base.part_number,
            base.supplier_name,
            base.test_standard,
            base.sample_count,
        ]
        if value
    )


def _has_plan_content(data: TestPlanData) -> bool:
    return bool(data.test_items or data.test_details)


def enforce_minimum_content(data: TestPlanData) -> int:
    """Reject only truly empty AI extraction, not sparse vendor templates."""
    filled = _basic_info_filled_count(data)
    if filled < 2 and not _has_plan_content(data):
        raise ExtractionError(f"基本信息字段不足且未提取到测试项目（仅 {filled} 个非空）")
    return filled


# ── Chinese → English field name translation ──────────────────────────

_BASIC_INFO_MAP = {
    "零件名称": "part_name", "零件号": "part_number",
    "供应商名称": "supplier_name", "执行标准": "test_standard",
    "样品数量": "sample_count", "软件版本号": "sw_version",
    "硬件版本号": "hw_version", "测试计划编号": "plan_number",
    "测试地点": "test_location",
}
_ITEM_MAP = {
    "编号": "code", "名称": "name", "是否执行": "is_executed",
    "测试模式": "test_mode", "认可依据": "standard_clause",
    "验收标准": "acceptance",
    "样品要求": "sample_requirements",
    "executed": "is_executed", "mode": "test_mode",
    "standard_ref": "standard_clause", "acceptance_criteria": "acceptance",
    "原文证据": "source_quote", "source_quote": "source_quote",
    "原文位置": "source_location", "source_location": "source_location",
}
_DETAIL_MAP = {
    "编号": "code", "参考文件": "ref_doc", "工作模式": "operating_mode",
    "测试时间": "test_duration", "测试位置": "test_position",
    "测试等级": "test_level", "线束长度": "harness_length",
    "DUT放置": "dut_placement", "接地连接": "ground_connection",
    "原文证据": "source_quote", "source_quote": "source_quote",
    "原文位置": "source_location", "source_location": "source_location",
}


def _translate_keys(d: dict, mapping: dict[str, str]) -> dict:
    """Rename keys in *d* according to *mapping*, keeping unmapped keys as-is."""
    return {mapping.get(k, k): v for k, v in d.items()}


def _stringify(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).strip()


def _stringify_fields(payload: dict) -> dict[str, str]:
    return {key: _stringify(value) for key, value in payload.items()}


def _normalize_plan_item_payload(item: dict) -> dict:
    translated = _translate_keys(item, _ITEM_MAP)
    raw_requirements = translated.pop("sample_requirements", {})
    payload: dict = _stringify_fields(translated)
    if isinstance(raw_requirements, dict):
        payload["sample_requirements"] = {
            _stringify(key): _stringify(value)
            for key, value in raw_requirements.items()
            if _stringify(key) and _stringify(value)
        }
    elif _stringify(raw_requirements):
        payload["sample_requirements"] = {
            "default": _stringify(raw_requirements),
        }
    code = payload.get("code", "")
    name = payload.get("name", "")
    # Vendor spreadsheets often put the row sequence number under "编号".
    # Use the semantic item name as the identifier so cross-document matching
    # has a stable text anchor instead of meaningless "1", "2", ...
    if code.isdigit() and name:
        payload["code"] = name
    return payload


def _recover_tabular_sample_requirements(
    raw_text: str,
    items: list[TestPlanItem],
) -> int:
    """Recover per-row sample counts from tabular plans without template IDs."""
    lines = raw_text.splitlines()
    header_index = next((
        index for index, line in enumerate(lines)
        if "\t" in line and re.search(r"(?:试验|测试)项目", line)
    ), -1)
    if header_index < 0:
        return 0
    headers = [cell.strip() for cell in lines[header_index].split("\t")]
    count_columns = [
        index for index, header in enumerate(headers)
        if re.search(r"(?i)(?:样品|数量|容量|sample|qty|pcs)", header)
    ]
    if not count_columns:
        return 0
    blocks: list[str] = []
    current: list[str] = []
    for line in lines[header_index + 1:]:
        if re.match(r"^\s*\d+\s*\t", line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    by_name = {
        re.sub(r"\s+", "", item.name).casefold(): item
        for item in items if item.name
    }
    recovered = 0
    for block in blocks:
        cells = [cell.strip() for cell in block.split("\t")]
        if len(cells) < 2:
            continue
        item = by_name.get(re.sub(r"\s+", "", cells[1]).casefold())
        if item is None:
            continue
        requirements = dict(item.sample_requirements)
        for column in count_columns:
            if column >= len(cells):
                continue
            value = cells[column].splitlines()[0].strip()
            if not re.search(r"\d", value):
                continue
            requirements[headers[column] or f"column_{column + 1}"] = value
        if requirements != item.sample_requirements:
            item.sample_requirements = requirements
            recovered += 1
    if recovered:
        logger.info(
            "test_plan_sample_requirements_recovered",
            recovered_item_count=recovered,
            count_column_count=len(count_columns),
        )
    return recovered


def _remove_unsupported_execution_flags(
    raw_text: str, items: list[TestPlanItem],
) -> int:
    """Remove row execution states that outnumber their source evidence.

    A plan table defines the requirement universe, but ``is_executed`` must
    still be an exact transcription.  LLMs sometimes copy the schema example
    value ``Y`` into every row even when the source execution columns are blank.
    In that case keeping the value would create fabricated evidence in the UI.
    """
    flagged = [
        item for item in items
        if item.is_executed.strip().lower() in _KNOWN_EXECUTION_FLAGS
    ]
    if not flagged or _GLOBAL_EXECUTION_EVIDENCE_RE.search(raw_text):
        return 0
    evidence_count = len(_ROW_EXECUTION_EVIDENCE_RE.findall(raw_text))
    if evidence_count >= len(flagged):
        return 0
    for item in flagged:
        item.is_executed = ""
    logger.warning(
        "test_plan_execution_flags_removed",
        removed=len(flagged),
        explicit_markers=evidence_count,
        reason="insufficient_source_evidence",
    )
    return len(flagged)


def _link_test_details_to_items(
    details: list[TestPlanDetail], items: list[TestPlanItem],
) -> list[TestPlanDetail]:
    """Canonicalize detail identifiers without semantic or fuzzy guessing.

    Explicit detail identifiers are matched against exact normalized item codes
    or names.  Positional recovery is allowed only for the common spreadsheet
    case where both lists have the same length and every detail identifier is
    the exact continuous row sequence 1..N.
    """
    def identity(value: str) -> str:
        return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())

    canonical: dict[str, str] = {}
    for item in items:
        target = item.code or item.name
        if not target:
            continue
        for candidate in (item.code, item.name):
            key = identity(candidate)
            if key:
                canonical.setdefault(key, target)

    for detail in details:
        matched = canonical.get(identity(detail.code))
        if matched:
            detail.code = matched

    expected_ordinals = [str(index) for index in range(1, len(details) + 1)]
    actual_ordinals = [detail.code.strip() for detail in details]
    if (
        details
        and len(details) == len(items)
        and actual_ordinals == expected_ordinals
    ):
        for detail, item in zip(details, items):
            detail.code = item.code or item.name
        logger.info(
            "test_plan_detail_ids_recovered",
            strategy="continuous_row_order",
            recovered=len(details),
        )

    return details


def _item_identity(item: TestPlanItem) -> str:
    explicit_codes = count_test_codes(f"{item.code} {item.name}")
    if len(explicit_codes) == 1:
        return next(iter(explicit_codes)).casefold()
    return re.sub(
        r"[^0-9a-z\u4e00-\u9fff]+", "",
        str(item.name or item.code or "").casefold(),
    )


def _merge_inventory_items(
    primary: list[TestPlanItem], audited: list[TestPlanItem],
) -> tuple[list[TestPlanItem], int]:
    """Union two independent LLM readings using explicit source identities."""
    merged = list(primary)
    identities = {_item_identity(item) for item in merged if _item_identity(item)}
    added = 0
    for item in audited:
        identity = _item_identity(item)
        if not identity or identity in identities:
            continue
        merged.append(item)
        identities.add(identity)
        added += 1
    return merged, added


_INVENTORY_AUDIT_PROMPT = """你是测试计划完整性复核员。文档没有固定模板，可能是表格、段落、跨页表格或逐项章节。
独立通读全文，重新清点全部测试项目（含不适用、待确认和未填写执行状态的项目）。不要参考固定列名，不要仅依赖编号或目录。
只返回JSON：{"测试项目":[{"编号":"原文编号；没有则用项目名","名称":"原文名称","是否执行":"仅抄明确值","测试模式":"原文","认可依据":"原文","验收标准":"原文","样品要求":{},"原文证据":"可逐字回查的连续原文","原文位置":"文件第N页或工作表"}]}。
禁止编造；每一项必须有可在输入全文逐字回查的原文证据。"""


class TestPlanExtractor:

    # DeepSeek V4: 1M context, 384K max output → no truncation needed.
    # Test plans are shorter than reports, so 32K output is ample.
    MAX_OUTPUT_TOKENS = 32768
    MAX_RETRIES = 2
    TIMEOUT_SEC = 300

    @staticmethod
    async def extract(file_bytes: bytes, filename: str,
                      reviewer=None, gateway=None) -> TestPlanData:
        if len(file_bytes) > MAX_FILE_SIZE:
            raise SizeLimitError(filename, len(file_bytes))
        reviewer_was_supplied = reviewer is not None
        visual_failures: list[str] = []
        visual_page_count = 0
        try:
            raw_text, scan_pages = _page_aware_text(file_bytes, filename)
            if scan_pages:
                try:
                    visual_text = await _transcribe_scan_pages(
                        file_bytes, filename, scan_pages, gateway=gateway,
                    )
                    visual_page_count = len(visual_text)
                    raw_text = _merge_visual_pages(raw_text, visual_text)
                    missing_visual = sorted(
                        page_number for page_number, _ in scan_pages
                        if page_number not in visual_text
                    )
                    if missing_visual:
                        visual_failures.append(
                            f"扫描页视觉转录未完成: {missing_visual}"
                        )
                except Exception as exc:
                    visual_failures.append(
                        f"扫描页视觉转录失败: {type(exc).__name__}"
                    )
                    logger.warning(
                        "test_plan_visual_transcription_failed",
                        filename=filename,
                        scan_page_count=len(scan_pages),
                        error_type=type(exc).__name__,
                    )
        except Exception as e:
            raise ExtractionError(f"文档文本提取失败: {e}") from e
        if not raw_text.strip():
            raise ExtractionError("文档文本提取为空")

        if reviewer is None:
            from services.deepseek_client import DeepSeekReviewer
            reviewer = DeepSeekReviewer()

        from services.llm_policy import get_llm_policy
        model = get_llm_policy("structured_extraction").model
        t0 = time.time()

        logger.info("ai_extract_start", doc_type="test_plan", text_length=len(raw_text),
                    model=model)

        # ── Single-shot AI extraction ──────────────────────────────────
        # _call_api handles transient errors (network/timeout/rate-limit)
        # with up to 3 retries.  Billing/auth errors (401/402/403) raise
        # immediately with a clear Chinese message.
        ai_result = await reviewer._call_api(
            prompt=f"文档全文：\n{raw_text}",
            stage="test_plan",
            timeout=TestPlanExtractor.TIMEOUT_SEC,
            system_prompt=TEST_PLAN_PROMPT,
            max_tokens=TestPlanExtractor.MAX_OUTPUT_TOKENS,
            task_kind="structured_extraction",
        )

        if not ai_result:
            raise ExtractionError("试验计划 AI 提取失败：AI 返回空结果")

        # ── Structural validation ──────────────────────────────────────
        try:
            basic = TestPlanBasicInfo(**_stringify_fields(_translate_keys(
                ai_result.get("基本信息", {}), _BASIC_INFO_MAP)))
            items = [TestPlanItem(**_normalize_plan_item_payload(it))
                     for it in ai_result.get("测试项目", []) if isinstance(it, dict)]
            inventory_added = 0
            # Production uses a second independent semantic reading to catch
            # items hidden in prose, continuation pages or detail chapters.
            # Explicit test doubles stay single-pass so unit tests never make
            # paid calls implicitly.
            if not reviewer_was_supplied:
                audited_result = await reviewer._call_api(
                    prompt=f"带物理页码的文档全文：\n{raw_text}",
                    stage="test_plan_inventory_audit",
                    timeout=TestPlanExtractor.TIMEOUT_SEC,
                    system_prompt=_INVENTORY_AUDIT_PROMPT,
                    max_tokens=TestPlanExtractor.MAX_OUTPUT_TOKENS,
                    task_kind="structured_extraction",
                )
                audited_items = [
                    TestPlanItem(**_normalize_plan_item_payload(it))
                    for it in (audited_result or {}).get("测试项目", [])
                    if isinstance(it, dict)
                ]
                items, inventory_added = _merge_inventory_items(items, audited_items)
            _remove_unsupported_execution_flags(raw_text, items)
            # Sample requirements are semantic and may be expressed in prose,
            # merged cells or arbitrary model columns.  Keep the old generic
            # table helper available for migration diagnostics, but do not let
            # it mutate production extraction: the LLM output plus source gate
            # is authoritative for every layout.
            recovered_sample_requirements = 0
            # Build detail models: separate "code" from the remaining
            # translated keys → put them in the "fields" dict so they
            # aren't silently dropped by Pydantic's extra='ignore'.
            detail_models: list[TestPlanDetail] = []
            for d in ai_result.get("测试细则", []):
                if not isinstance(d, dict):
                    continue
                translated = _translate_keys(d, _DETAIL_MAP)
                code = _stringify(translated.pop("code", ""))
                source_quote = _stringify(translated.pop("source_quote", ""))
                source_location = _stringify(translated.pop("source_location", ""))
                detail_models.append(TestPlanDetail(
                    code=code,
                    fields=_stringify_fields(translated),
                    source_quote=source_quote,
                    source_location=source_location,
                ))
            details = _link_test_details_to_items(detail_models, items)
            repaired_source_evidence = _repair_source_evidence(
                raw_text, items, details,
            )
            data = TestPlanData(basic_info=basic, test_items=items,
                                test_details=details, raw_text=raw_text)
        except Exception as e:
            raise ExtractionError(f"试验计划 JSON 结构验证失败: {e}") from e

        # ── Minimum field check ────────────────────────────────────────
        filled = enforce_minimum_content(data)
        if filled < 2:
            logger.warning(
                "test_plan_basic_info_sparse",
                filled=filled,
                items=len(data.test_items),
                details=len(data.test_details),
                filename=filename,
            )

        # ── Completeness validation ────────────────────────────────────
        errors = [*visual_failures, *validate_extraction(
            raw_text, data, require_source_evidence=not reviewer_was_supplied,
        )]
        duration_ms = round((time.time() - t0) * 1000)
        data.extraction_quality = "partial" if errors else "complete"
        data.failed_passes = list(errors)
        data.extraction_metrics = {
            "duration_ms": duration_ms,
            "text_length": len(raw_text),
            "test_item_count": len(data.test_items),
            "test_detail_count": len(data.test_details),
            "sample_requirement_item_count": sum(
                bool(item.sample_requirements) for item in data.test_items
            ),
            "recovered_sample_requirement_item_count": recovered_sample_requirements,
            "validation_error_count": len(errors),
            "scan_page_count": len(scan_pages),
            "visual_page_count": visual_page_count,
            "inventory_audit_added_count": inventory_added,
            "repaired_source_evidence_count": repaired_source_evidence,
        }
        if errors:
            logger.error("validation_failed", errors=[str(e) for e in errors],
                        model=model)
            # Still return the data — validation warnings are non-fatal;
            # the user can review in the UI.
            logger.info("ai_extract_done_with_warnings",
                       items=len(data.test_items),
                       details=len(data.test_details),
                       errors=errors,
                       duration_ms=duration_ms,
                       model=model)
        else:
            logger.info("ai_extract_done",
                       items=len(data.test_items),
                       details=len(data.test_details),
                       duration_ms=duration_ms,
                       model=model)
        return data
