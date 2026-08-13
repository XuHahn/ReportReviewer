"""Evidence-grounded semantic fallback for variable raw-record templates."""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict
from typing import Any

from services.deepseek_client import DeepSeekReviewer
from services.raw_records_filename_parser import RawRecordMeta
from services.raw_records_instrument_extractor import (
    InstrumentRecord,
    deduplicate_instruments,
    validate_all_calibrations,
)
from services.raw_records_table_gate import expected_rows_by_type
from services.raw_records_table_extractor import TestDataRow
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_SOURCE_CHARS = 30000
MAX_CONCURRENCY = max(1, int(os.getenv("RAW_RECORD_FLASH_CONCURRENCY", "6")))
TABLE_MODEL = os.getenv("RAW_RECORD_TABLE_MODEL", "deepseek-v4-flash")
TABLE_MAX_TOKENS = max(4000, int(os.getenv("RAW_RECORD_TABLE_MAX_TOKENS", "12000")))

SYSTEM_PROMPT = """你是检测原始记录结构化专家。原始记录模板不固定，你必须理解语义而不是依赖固定字段名。
只提取原文明确出现的信息，不推测、不补全。每个非空字段必须附带能在输入原文中找到的原文证据。
仅输出合法 JSON 对象。"""

JSON_EXAMPLE = {
    "test_identity": {"code": "", "name": "系统12V电源电压波动试验", "evidence": "测试项目 系统12V电源电压波动试验"},
    "test_mode": {"value": "模式1", "evidence": "测试模式：模式1"},
    "injection_point": {"value": "电源线", "evidence": "注入点 电源线"},
    "conditions": [
        {"name": "Ub", "value": "12", "unit": "V", "evidence": "Ub=12V"}
    ],
    "procedure_steps": [
        {"description": "施加1个脉冲", "evidence": "测试时间 1个脉冲"}
    ],
    "result": {
        "conclusion": "符合",
        "required_performance": "B",
        "actual_performance": "B",
        "evidence": "要求性能 B 实际性能 B 测试结果 符合"
    },
    "sample": {"name": "样品", "model": "型号", "id": "编号", "evidence": "样品名称 样品 型号 型号 样品编号 编号"},
    "environment": {"temperature": "23.1℃", "humidity": "51%RH", "evidence": "环境条件 23.1℃/51%RH"}
}

TABLE_JSON_EXAMPLE = {
    "document_fields": [
        {
            "source_label": "测试项目",
            "semantic": "test_item",
            "source_value": "示例试验",
            "normalized_value": "示例试验",
            "evidence": "测试项目 示例试验",
        },
        {
            "source_label": "测试模式",
            "semantic": "test_mode",
            "source_value": "模式1",
            "normalized_value": "模式1",
            "evidence": "测试模式 模式1",
        },
    ],
    "tables": [
        {
            "table_id": "T1",
            "table_type": "test_data",
            "title": "测试数据",
            "headers": [
                {"source": "测试电压", "semantic": "test_voltage"},
                {"source": "测试结果", "semantic": "verdict"},
            ],
            "source_row_count": 2,
            "rows": [
                {
                    "row_index": 1,
                    "cells": [
                        {"column": "测试电压", "semantic": "test_voltage", "source_value": "±4kV", "normalized_value": "±4 kV"},
                        {"column": "测试结果", "semantic": "verdict", "source_value": "符合", "normalized_value": "PASS"},
                    ],
                    "evidence": "±4kV 符合",
                },
                {
                    "row_index": 2,
                    "cells": [
                        {"column": "测试电压", "semantic": "test_voltage", "source_value": "±6kV", "normalized_value": "±6 kV"},
                        {"column": "测试结果", "semantic": "verdict", "source_value": "不符合", "normalized_value": "FAIL"},
                    ],
                    "evidence": "±6kV 不符合",
                },
            ],
        }
    ],
    "overall_conclusion": {"value": "符合", "evidence": "总体结论 符合"},
    "conflicts": [
        {
            "type": "row_vs_overall",
            "description": "存在不符合数据行，但总体结论为符合",
            "evidence": ["±6kV 不符合", "总体结论 符合"],
        }
    ],
}

TABLE_SYSTEM_PROMPT = """你是检测原始记录的表格清点与逐行转录专家。
原始记录可能来自任意厂商、任意语言和任意试验类型。不要依赖固定模板、EQ编号或预设测试类别。
你的任务是忠实清点文档中所有有业务数据的表格，并逐行转录；不得概括、合并、去重或补全数据行。

必须遵守：
1. 清点正文中全部业务表格，包括测试条件、测试数据、测量点、结果和仪器表。
2. 文档级非表格字段全部放入 document_fields 数组，保留原标签 source_label，并给出通用 semantic；未知语义写 unknown。
3. 常见文档字段包括但不限于报告/委托编号、测试项目及代码、模式、样品、环境、供电、地点、标准、人员、日期；不得因示例没有列出而遗漏。
4. 每张表保留原始表头；每列给出 semantic，不认识时写 unknown。
5. source_row_count 只统计数据行，不含表头。每个物理业务数据行独立输出，禁止合并或去重。
6. cells 必须使用数组。source_value 逐字保留原单元格值；normalized_value 仅作可选标准化，不能覆盖原值；空单元格也保留。
7. 仪器表严格按原列对应，禁止把型号、序列号和校准日期左移或右移；无法确定列归属时 semantic 写 unknown。
8. 每行 evidence 必须逐字引用覆盖整行的原文。保留 PASS、FAIL、符合、不符合、性能等级、单位、正负号和小数点。
   对要求性能等级、实际性能等级、测试结果等相邻短字段，必须依据表格列边界分别转录；脚注 1）和 2）位于不同列时禁止合并到同一字段。
9. overall_conclusion 只放原文明确写出的总体结论，不得从数据行生成。
10. 数据行和总体结论冲突时，在 conflicts 中附上双方原文证据。
11. 不存在的信息输出空数组或空字符串，禁止推测。
12. 仅输出合法 JSON 对象，不输出 Markdown。

JSON OUTPUT 示例：
""" + json.dumps(TABLE_JSON_EXAMPLE, ensure_ascii=False)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("：", ":")


def evidence_in_source(evidence: Any, source_text: str) -> bool:
    evidence_norm = _norm(evidence)
    return bool(evidence_norm) and evidence_norm in _norm(source_text)


def _source_window(value: Any, source_text: str, radius: int = 45) -> str:
    """Return an exact source slice around a normalized scalar match."""
    needle = _norm(value)
    if len(needle) < 2:
        return ""
    compact_chars: list[str] = []
    original_positions: list[int] = []
    for index, char in enumerate(source_text):
        if char.isspace():
            continue
        compact_chars.append(":" if char == "：" else char)
        original_positions.append(index)
    compact = "".join(compact_chars)
    start = compact.find(needle)
    if start < 0:
        return ""
    end = start + len(needle) - 1
    original_start = max(0, original_positions[start] - radius)
    original_end = min(len(source_text), original_positions[end] + radius + 1)
    return source_text[original_start:original_end].strip()


def _ground_split_cell_value(value: Any, source_text: str) -> list[str]:
    """Ground a cell whose logical text is non-contiguous after PDF pagination.

    PDF text extraction can place the row-ending columns before a long cell
    that continues on the next page.  In that case the model's logical row is
    correct, but its combined evidence cannot be one contiguous source quote.
    Every meaningful fragment still has to be independently locatable.
    """
    exact = _source_window(value, source_text)
    if exact:
        return [exact]
    fragments = [
        item.strip() for item in re.split(r"[;；,，\n]+", str(value or ""))
        if len(_norm(item)) >= 2
    ]
    if len(fragments) < 2:
        return []
    quotes: list[str] = []
    for fragment in fragments:
        quote = _source_window(fragment, source_text)
        if not quote:
            return []
        if quote not in quotes:
            quotes.append(quote)
    return quotes


def _valid_object(value: Any, source_text: str) -> dict:
    if not isinstance(value, dict):
        return {}
    if evidence_in_source(value.get("evidence"), source_text):
        return value

    # If the model changed only spacing in a combined quote, ground each scalar
    # independently and replace the quote with exact slices from the source.
    grounded: dict[str, Any] = {}
    quotes: list[str] = []
    for key, item in value.items():
        if key == "evidence" or item in (None, "") or isinstance(item, (dict, list)):
            continue
        quote = _source_window(item, source_text)
        if quote:
            grounded[key] = item
            if quote not in quotes:
                quotes.append(quote)
    if not grounded:
        return {}
    grounded["evidence"] = "\n---\n".join(quotes)
    return grounded


def validate_semantic_result(payload: Any, source_text: str) -> dict:
    """Drop every LLM claim that is not backed by a locatable source quote."""
    if not isinstance(payload, dict):
        return {}

    validated: dict[str, Any] = {}
    for key in ("test_identity", "test_mode", "injection_point", "result", "sample", "environment"):
        item = _valid_object(payload.get(key), source_text)
        if item:
            validated[key] = item

    for key in ("conditions", "procedure_steps"):
        items = payload.get(key)
        if isinstance(items, list):
            accepted = [_valid_object(item, source_text) for item in items[:100]]
            accepted = [item for item in accepted if item]
            if accepted:
                validated[key] = accepted
    return validated


def build_table_extraction_prompt(meta: RawRecordMeta) -> str:
    return (
        "请按 system 中的 JSON OUTPUT 结构完整转录这份原始记录。\n"
        f"文件名：{meta.filename}\n"
        f"原始记录文本：\n{meta.raw_text[:MAX_SOURCE_CHARS]}"
    )


def _table_family(table_type: Any) -> str:
    value = str(table_type or "").strip().lower()
    if any(token in value for token in ("instrument", "equipment", "仪器", "设备")):
        return "instrument"
    if any(token in value for token in ("test", "measurement", "result", "测试", "测量", "结果")):
        return "test_data"
    return "other"


def _source_cell_value(cell: dict) -> Any:
    if "source_value" in cell:
        return cell.get("source_value")
    return cell.get("value")


_GEOMETRY_ATOMIC_SEMANTICS = {
    "required_performance", "required_grade", "actual_performance", "actual_grade",
    "verdict", "test_result", "conclusion", "result",
}
_GEOMETRY_ATOMIC_HEADERS = {
    "要求的性能等级", "要求性能等级", "要求性能", "实际性能等级", "实际性能",
    "测试结果", "试验结果", "判定", "结论",
}


def _column_key(value: Any) -> str:
    return re.sub(r"[\s:：()（）]", "", str(value or "")).lower()


def _geometry_correct_row_cells(
    cells: list[dict], geometry: dict[str, str] | None,
) -> tuple[list[dict], int]:
    """Correct short atomic cells using independent PDF column geometry."""
    if not geometry:
        return cells, 0
    geometry_by_column = {_column_key(key): value for key, value in geometry.items()}
    corrected: list[dict] = []
    correction_count = 0
    for cell in cells:
        if not isinstance(cell, dict):
            continue
        semantic = _column_key(cell.get("semantic"))
        column = str(cell.get("column") or "").strip()
        geometry_value = geometry_by_column.get(_column_key(column), "")
        is_atomic = (
            semantic in _GEOMETRY_ATOMIC_SEMANTICS
            or _column_key(column) in {_column_key(item) for item in _GEOMETRY_ATOMIC_HEADERS}
        )
        item = dict(cell)
        if is_atomic and geometry_value and _norm(_source_cell_value(item)) != _norm(geometry_value):
            item["source_value"] = geometry_value
            item["normalized_value"] = geometry_value
            item.pop("value", None)
            correction_count += 1
        corrected.append(item)
    return corrected, correction_count


def validate_table_extraction(
    payload: Any,
    source_text: str,
    table_inventory: list[dict] | None = None,
) -> dict:
    """Validate document fields and every table row against exact source text."""
    if not isinstance(payload, dict):
        return {"document_fields": [], "tables": [], "overall_conclusion": {}, "conflicts": [], "quality": {}}

    document_fields: list[dict] = []
    for field in payload.get("document_fields") or []:
        if not isinstance(field, dict):
            continue
        source_value = _source_cell_value(field)
        evidence = field.get("evidence")
        if evidence_in_source(evidence, source_text) and (
            source_value in (None, "") or _norm(source_value) in _norm(evidence)
        ):
            document_fields.append(field)

    # Accept experimental/legacy payloads generated before document_fields was
    # introduced so cached or in-flight responses remain readable.
    for key, value in (payload.get("document") or {}).items() if isinstance(payload.get("document"), dict) else []:
        grounded = _valid_object(value, source_text)
        if grounded:
            document_fields.append({
                "source_label": str(key),
                "semantic": str(key),
                "source_value": grounded.get("value", ""),
                "normalized_value": grounded.get("value", ""),
                "evidence": grounded.get("evidence", ""),
            })

    tables: list[dict] = []
    total_reported = 0
    total_accepted = 0
    total_rejected = 0
    geometry_corrections = 0
    split_evidence_recoveries = 0
    inventory_by_family: dict[str, list[dict]] = {}
    for item in table_inventory or []:
        family = str(item.get("table_type") or "")
        inventory_by_family.setdefault(family, []).append(item)
    family_offsets: dict[str, int] = {}
    for table_index, table in enumerate(payload.get("tables") or []):
        if not isinstance(table, dict):
            continue
        raw_rows = table.get("rows") if isinstance(table.get("rows"), list) else []
        try:
            declared_count = max(0, int(table.get("source_row_count") or 0))
        except (TypeError, ValueError):
            declared_count = 0
        accepted_rows: list[dict] = []
        family = _table_family(table.get("table_type"))
        family_offset = family_offsets.get(family, 0)
        family_offsets[family] = family_offset + 1
        inventory_match = (inventory_by_family.get(family) or [])
        geometry_rows = (
            inventory_match[family_offset].get("row_end_cells", [])
            if family_offset < len(inventory_match) else []
        )
        for row_offset, row in enumerate(raw_rows[:500]):
            if not isinstance(row, dict):
                total_rejected += 1
                continue
            cells = row.get("cells") if isinstance(row.get("cells"), list) else []
            geometry = geometry_rows[row_offset] if row_offset < len(geometry_rows) else None
            cells, corrected_count = _geometry_correct_row_cells(
                cells,
                geometry,
            )
            geometry_corrections += corrected_count
            if corrected_count:
                logger.warning(
                    "raw_record_geometry_correction",
                    table_id=str(table.get("table_id") or f"T{table_index + 1}"),
                    row_index=row.get("row_index", row_offset + 1),
                    corrected_cell_count=corrected_count,
                )
            evidence = str(row.get("evidence") or "")
            split_grounded = False
            if not evidence_in_source(evidence, source_text):
                # Only recover a non-contiguous quote when an independently
                # counted physical row exists at the same table offset.  This
                # prevents a model from assembling a new row out of unrelated
                # values that happen to occur elsewhere in the document.
                if not geometry:
                    total_rejected += 1
                    continue
                quotes: list[str] = []
                recoverable = True
                geometry_by_column = {
                    _column_key(key): value for key, value in geometry.items()
                }
                for cell in cells:
                    if not isinstance(cell, dict):
                        continue
                    value = _source_cell_value(cell)
                    if value in (None, ""):
                        continue
                    grounded = _ground_split_cell_value(value, source_text)
                    if not grounded:
                        # One-character atomic grades can only be grounded by
                        # the independent PDF column geometry for this row.
                        column_value = geometry_by_column.get(
                            _column_key(cell.get("column")), "",
                        )
                        if len(_norm(value)) < 2 and _norm(column_value) == _norm(value):
                            continue
                        recoverable = False
                        break
                    for quote in grounded:
                        if quote not in quotes:
                            quotes.append(quote)
                if not recoverable or not quotes:
                    total_rejected += 1
                    continue
                evidence = "\n---\n".join(quotes)
                split_grounded = True
                split_evidence_recoveries += 1
                logger.warning(
                    "raw_record_split_evidence_recovered",
                    table_id=str(table.get("table_id") or f"T{table_index + 1}"),
                    row_index=row.get("row_index", row_offset + 1),
                    fragment_count=len(quotes),
                )
            evidence_norm = _norm(evidence)
            accepted_cells = []
            for cell in cells:
                if not isinstance(cell, dict):
                    continue
                value = _source_cell_value(cell)
                if (
                    value in (None, "")
                    or _norm(value) in evidence_norm
                    or (split_grounded and bool(_ground_split_cell_value(value, source_text)))
                    or (
                        split_grounded and len(_norm(value)) < 2
                        and _norm(geometry_by_column.get(_column_key(cell.get("column")), "")) == _norm(value)
                    )
                ):
                    accepted_cells.append(cell)
            if len(accepted_cells) != len(cells):
                total_rejected += 1
                continue
            accepted = dict(row)
            accepted["cells"] = accepted_cells
            accepted["evidence"] = evidence
            accepted_rows.append(accepted)
        total_reported += declared_count
        total_accepted += len(accepted_rows)
        tables.append({
            "table_id": str(table.get("table_id") or f"T{table_index + 1}"),
            "table_type": str(table.get("table_type") or "other"),
            "table_family": family,
            "title": str(table.get("title") or ""),
            "headers": table.get("headers") if isinstance(table.get("headers"), list) else [],
            "source_row_count": declared_count,
            "model_row_count": len(raw_rows),
            "accepted_row_count": len(accepted_rows),
            "rows": accepted_rows,
            "complete": declared_count == len(accepted_rows),
        })

    overall = _valid_object(payload.get("overall_conclusion"), source_text)
    conflicts = []
    for conflict in payload.get("conflicts") or []:
        if not isinstance(conflict, dict):
            continue
        evidence = conflict.get("evidence")
        if isinstance(evidence, list) and len(evidence) >= 2 and all(evidence_in_source(item, source_text) for item in evidence):
            conflicts.append(conflict)

    expected = expected_rows_by_type(table_inventory or [])
    accepted_by_type: dict[str, int] = {}
    for table in tables:
        family = table["table_family"]
        if family in {"test_data", "instrument"}:
            accepted_by_type[family] = accepted_by_type.get(family, 0) + table["accepted_row_count"]
    independent_mismatches = [
        {
            "table_type": family,
            "expected_rows": count,
            "accepted_rows": accepted_by_type.get(family, 0),
        }
        for family, count in expected.items()
        if accepted_by_type.get(family, 0) != count
    ]
    model_complete = bool(tables) and all(table["complete"] for table in tables)

    return {
        "document_fields": document_fields,
        "tables": tables,
        "overall_conclusion": overall,
        "conflicts": conflicts,
        "quality": {
            "declared_source_rows": total_reported,
            "accepted_rows": total_accepted,
            "rejected_rows": total_rejected,
            "expected_rows_by_type": expected,
            "accepted_rows_by_type": accepted_by_type,
            "independent_mismatches": independent_mismatches,
            "model_complete": model_complete,
            "geometry_corrections": geometry_corrections,
            "split_evidence_recoveries": split_evidence_recoveries,
            "complete": model_complete and not independent_mismatches and total_rejected == 0,
        },
    }


def semantic_to_row(meta: RawRecordMeta, semantic: dict) -> TestDataRow | None:
    identity = semantic.get("test_identity", {})
    conditions = semantic.get("conditions", [])
    steps = semantic.get("procedure_steps", [])
    result = semantic.get("result", {})
    injection = semantic.get("injection_point", {})
    mode = semantic.get("test_mode", {})

    if not any((conditions, steps, result, injection)):
        return None

    params: dict[str, str] = {}
    for item in conditions:
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "").strip()
        unit = str(item.get("unit") or "").strip()
        if name and value:
            params[name] = f"{value}{unit}"

    quotes = []
    for item in [identity, mode, injection, result, *conditions, *steps]:
        quote = str(item.get("evidence") or "").strip() if isinstance(item, dict) else ""
        if quote and quote not in quotes:
            quotes.append(quote)

    step_text = "；".join(str(item.get("description") or "").strip() for item in steps)
    conclusion = str(result.get("conclusion") or "").strip()
    return TestDataRow(
        row_id=f"{meta.test_item_code}_{meta.sequence}_semantic",
        record_filename=meta.filename,
        category=meta.category,
        test_item=str(identity.get("name") or meta.test_item_name).strip(),
        injection_point=str(injection.get("value") or "").strip(),
        spec_params=params,
        spec_params_raw="；".join(f"{k}={v}" for k, v in params.items()),
        pulse_count=step_text,
        required_performance=str(result.get("required_performance") or "").strip(),
        actual_performance=str(result.get("actual_performance") or "").strip(),
        test_result=conclusion,
        test_mode=str(mode.get("value") or meta.test_mode).strip(),
        row_type="semantic_test_record",
        extraction_source="llm_evidence_grounded",
        source_quote="\n".join(quotes)[:2000],
    )


async def extract_raw_record_semantics(
    meta: RawRecordMeta,
    reviewer: DeepSeekReviewer | None = None,
    model: str | None = None,
) -> dict:
    if not meta.raw_text.strip():
        return {}
    reviewer = reviewer or DeepSeekReviewer()
    prompt = (
        "请从以下原始记录中提取一次试验的语义结构。不同厂商字段名可能不同，请按含义归类。\n"
        "条件参数要逐项拆分；步骤保留关键动作；结论区分要求性能、实际性能和最终结果。\n"
        "不要把仪器型号、日期、文档编号误认为测试数据。没有明确内容时输出空值或空数组。\n"
        f"JSON 输出格式示例：\n{json.dumps(JSON_EXAMPLE, ensure_ascii=False)}\n\n"
        f"文件名：{meta.filename}\n原始记录文本：\n{meta.raw_text[:MAX_SOURCE_CHARS]}"
    )
    logger.info(
        "raw_record_llm_extract_start",
        filename=meta.filename,
        text_length=len(meta.raw_text),
        model=model or "configured_default",
    )
    payload = await reviewer._call_api(
        prompt=prompt,
        stage="raw_record_semantic",
        timeout=180,
        system_prompt=SYSTEM_PROMPT,
        max_tokens=5000,
        model=model,
        temperature=0,
        task_kind="fast_transcription",
    )
    result = validate_semantic_result(payload, meta.raw_text[:MAX_SOURCE_CHARS])
    logger.info(
        "raw_record_llm_extract_done",
        filename=meta.filename,
        accepted_sections=sorted(result.keys()),
        rejected_section_count=max(0, len(payload or {}) - len(result)),
    )
    return result


async def extract_raw_record_tables(
    meta: RawRecordMeta,
    reviewer: DeepSeekReviewer | None = None,
    user_id: str | None = None,
) -> dict:
    """Transcribe one raw-record PDF with the production table prompt."""
    if not meta.raw_text.strip():
        return {}
    reviewer = reviewer or DeepSeekReviewer()
    logger.info(
        "raw_record_table_extract_start",
        filename=meta.filename,
        text_length=len(meta.raw_text),
        model=TABLE_MODEL,
        inventory_tables=len(meta.table_inventory),
    )
    payload = await reviewer._call_api(
        prompt=build_table_extraction_prompt(meta),
        stage="raw_record_table_v2",
        timeout=240,
        system_prompt=TABLE_SYSTEM_PROMPT,
        max_tokens=TABLE_MAX_TOKENS,
        model=TABLE_MODEL,
        temperature=0,
        thinking=False,
        user_id=user_id,
        task_kind="fast_transcription",
    )
    result = validate_table_extraction(
        payload,
        meta.raw_text[:MAX_SOURCE_CHARS],
        meta.table_inventory,
    )
    quality = result.get("quality") or {}
    logger.info(
        "raw_record_table_extract_done",
        filename=meta.filename,
        model=TABLE_MODEL,
        accepted_rows=quality.get("accepted_rows", 0),
        rejected_rows=quality.get("rejected_rows", 0),
        expected_rows_by_type=quality.get("expected_rows_by_type", {}),
        accepted_rows_by_type=quality.get("accepted_rows_by_type", {}),
        complete=quality.get("complete", False),
    )
    return result


def _field_value(fields: list[dict], *semantics: str) -> str:
    for field in fields:
        semantic = str(field.get("semantic") or "").strip().lower()
        label = str(field.get("source_label") or "").strip().lower()
        if _matches_any(semantic, semantics) or _matches_any(label, semantics):
            return str(_source_cell_value(field) or field.get("normalized_value") or "").strip()
    return ""


def _matches_any(key: str, aliases: tuple[str, ...]) -> bool:
    key = key.strip().lower().removeprefix("column:")
    for raw_alias in aliases:
        alias = raw_alias.strip().lower()
        if not alias:
            continue
        if key == alias:
            return True
        if any("\u4e00" <= char <= "\u9fff" for char in alias):
            if alias in key:
                return True
        elif key.startswith(alias + "_") or key.endswith("_" + alias):
            return True
    return False


def _apply_document_fields(meta: RawRecordMeta, result: dict) -> None:
    fields = result.get("document_fields") or []
    mapping = {
        "report_id_pdf": ("report_id", "report_number", "order_id", "委托单编号", "报告编号"),
        "test_item_pdf": ("test_item", "test_name", "测试项目", "试验项目"),
        "test_mode": ("test_mode", "mode", "测试模式", "试验模式"),
        "sample_name": ("sample_name", "product_name", "样品名称"),
        "sample_model": ("sample_model", "model", "样品型号"),
        "sample_id": ("sample_id", "sample_number", "样品编号"),
        "power_supply": ("power_supply", "supply_voltage", "供电电源"),
        "temperature": ("temperature", "环境温度"),
        "humidity": ("humidity", "环境湿度", "相对湿度"),
        "test_location": ("test_location", "laboratory", "测试地点"),
        "std_ref": ("standard", "standard_reference", "标准依据"),
        "tester": ("tester", "operator", "检测人员", "测试人员"),
        "reviewer": ("reviewer", "审核人员", "校核人员"),
        "test_date": ("test_date", "测试日期", "检测日期"),
    }
    for target, aliases in mapping.items():
        value = _field_value(fields, *aliases)
        if value:
            meta._header_fields[target] = value

    item = meta.get_header("test_item_pdf")
    if item:
        code_match = re.search(r"(?:EQ[/\s-]?)?([A-Z]{2})[/\s-]?(\d{1,3})", item, re.I)
        if code_match:
            meta.test_item_code = f"EQ{code_match.group(1).upper()}{code_match.group(2)}"
            name = item[code_match.end():].strip(" ：:，,-")
            if name:
                meta.test_item_name = name
        elif not meta.test_item_name:
            meta.test_item_name = item
    mode = meta.get_header("test_mode")
    if mode:
        meta.test_mode = mode
    conclusion = result.get("overall_conclusion") or {}
    conclusion_value = _source_cell_value(conclusion)
    if conclusion_value:
        meta._header_fields["test_conclusion"] = str(conclusion_value).strip()


def _cells_by_semantic(row: dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for cell in row.get("cells") or []:
        if not isinstance(cell, dict):
            continue
        value = str(_source_cell_value(cell) or "").strip()
        semantic = str(cell.get("semantic") or "unknown").strip().lower()
        column = str(cell.get("column") or "").strip().lower()
        values[semantic] = value
        if column:
            values[f"column:{column}"] = value
    return values


def _cell_pick(values: dict[str, str], *tokens: str) -> str:
    for key, value in values.items():
        if _matches_any(key, tokens):
            return value
    return ""


def tables_to_rows(meta: RawRecordMeta, result: dict) -> list[TestDataRow]:
    rows: list[TestDataRow] = []
    fields = result.get("document_fields") or []
    document_item = _field_value(fields, "test_item", "test_name", "测试项目")
    document_mode = _field_value(fields, "test_mode", "mode", "测试模式")
    for table in result.get("tables") or []:
        if table.get("table_family") != "test_data":
            continue
        for index, raw_row in enumerate(table.get("rows") or [], start=1):
            values = _cells_by_semantic(raw_row)
            reserved_tokens = (
                "verdict", "result", "conclusion", "required_performance", "actual_performance",
                "test_mode", "mode", "injection", "frequency", "freq", "polarity", "margin",
                "record_number", "temperature", "cycle", "pulse_count",
                "结果", "判定", "结论", "性能", "模式", "注入", "位置", "频率", "极性", "余量",
                "记录序号", "温度", "环境", "循环", "脉冲数",
            )
            params = {
                key.removeprefix("column:"): value
                for key, value in values.items()
                if key.startswith("column:") and value and not any(token in key for token in reserved_tokens)
            }
            row = TestDataRow(
                row_id=f"{meta.test_item_code}_{meta.sequence}_{table.get('table_id', 'T')}_{index}",
                record_filename=meta.filename,
                category=meta.category,
                test_item=document_item or meta.test_item_name,
                injection_point=_cell_pick(values, "injection_point", "test_position", "注入点", "测试部位"),
                spec_params=params,
                spec_params_raw="；".join(f"{key}={value}" for key, value in params.items()),
                temp_condition=_cell_pick(values, "temperature", "environment", "温度", "环境"),
                pulse_count=_cell_pick(values, "pulse_count", "pulse", "脉冲数"),
                cycles=_cell_pick(values, "cycles", "cycle", "循环"),
                required_performance=_cell_pick(values, "required_performance", "required_grade", "要求性能"),
                actual_performance=_cell_pick(values, "actual_performance", "actual_grade", "实际性能"),
                test_result=_cell_pick(values, "verdict", "test_result", "conclusion", "测试结果", "判定"),
                freq_range=_cell_pick(values, "frequency_range", "frequency", "freq", "频率"),
                test_level=_cell_pick(values, "test_level", "level", "测试等级"),
                modulation=_cell_pick(values, "modulation", "调制"),
                step=_cell_pick(values, "step", "步长"),
                dwell_time=_cell_pick(values, "dwell_time", "dwell", "驻留"),
                antenna_polarity=_cell_pick(values, "antenna_polarity", "天线极化"),
                antenna_position=_cell_pick(values, "antenna_position", "天线位置"),
                eut_direction=_cell_pick(values, "eut_direction", "方向"),
                test_mode=_cell_pick(values, "test_mode", "mode", "模式") or document_mode or meta.test_mode,
                polarity=_cell_pick(values, "polarity", "极性"),
                margin=_cell_pick(values, "margin", "余量"),
                record_number=_cell_pick(values, "record_number", "record_id", "记录序号"),
                row_type=str(table.get("table_type") or "test_data"),
                extraction_source="llm_table_v2_evidence_grounded",
                source_quote=str(raw_row.get("evidence") or "")[:2000],
            )
            rows.append(row)
    return rows


def tables_to_instruments(meta: RawRecordMeta, result: dict) -> list[InstrumentRecord]:
    instruments: list[InstrumentRecord] = []
    for table in result.get("tables") or []:
        if table.get("table_family") != "instrument":
            continue
        for raw_row in table.get("rows") or []:
            values = _cells_by_semantic(raw_row)
            name = _cell_pick(values, "instrument_name", "equipment_name", "仪器名称", "设备名称")
            manufacturer = _cell_pick(values, "manufacturer", "maker", "制造商", "生产厂家")
            model = _cell_pick(values, "model", "model_number", "型号")
            serial = _cell_pick(values, "serial_number", "serial_no", "serial", "系列号", "序列号")
            calibration = _cell_pick(values, "calibration_end", "calibration_date", "valid_until", "校准有效期")
            if not all((name, model, serial, calibration)):
                continue
            if serial == "/" or calibration == "/" or "软件" in name.lower():
                continue
            instruments.append(InstrumentRecord(
                name=name,
                manufacturer=manufacturer,
                model=model,
                serial_no=serial,
                calibration_end=calibration,
                source_file=meta.filename,
            ))
    return instruments


def _deduplicate_instrument_occurrences(instruments: list[InstrumentRecord]) -> list[InstrumentRecord]:
    """Merge code/LLM copies of one source row, preserving cross-file reuse."""
    occurrences: dict[tuple[str, str], InstrumentRecord] = {}
    for instrument in instruments:
        identity = instrument.serial_no or f"{instrument.manufacturer}|{instrument.model}|{instrument.name}"
        identity = re.sub(r"\s+", "", identity).upper()
        key = (instrument.source_file, identity)
        current = occurrences.get(key)
        if current is None:
            occurrences[key] = instrument
            continue
        current_score = sum(bool(value) for value in asdict(current).values())
        candidate_score = sum(bool(value) for value in asdict(instrument).values())
        if candidate_score > current_score:
            occurrences[key] = instrument
    return list(occurrences.values())


async def enrich_raw_records_json(
    structured_json: str,
    user_id: str | None = None,
) -> tuple[str, list[dict]]:
    """Run Prompt v2 for every PDF and merge only evidence-gated rows."""
    obj = json.loads(structured_json)
    metas = [RawRecordMeta(**item) for item in obj.get("metas", [])]
    target_files = {meta.filename for meta in metas}
    rows = [
        TestDataRow(**item)
        for item in obj.get("data_rows", [])
        if item.get("record_filename") not in target_files
    ]
    code_instruments = [InstrumentRecord(**item) for item in obj.get("instruments", [])]
    llm_instruments: list[InstrumentRecord] = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    failures: list[dict] = []
    quality_results: list[dict] = []
    semantic_conflicts: list[dict] = []
    successful_files = 0
    reviewer = DeepSeekReviewer()

    async def process(meta: RawRecordMeta) -> None:
        nonlocal successful_files
        async with semaphore:
            try:
                semantic = await extract_raw_record_tables(meta, reviewer=reviewer, user_id=user_id)
                meta.semantic_data = semantic
                _apply_document_fields(meta, semantic)
                extracted_rows = tables_to_rows(meta, semantic)
                extracted_instruments = tables_to_instruments(meta, semantic)
                rows.extend(extracted_rows)
                llm_instruments.extend(extracted_instruments)
                if semantic:
                    successful_files += 1
                    meta.extraction_sources = ["code", "llm_table_v2"]
                quality = dict(semantic.get("quality") or {})
                quality_entry = {
                    "filename": meta.filename,
                    **quality,
                    "converted_test_rows": len(extracted_rows),
                    "converted_instruments": len(extracted_instruments),
                }
                quality_results.append(quality_entry)
                for conflict in semantic.get("conflicts") or []:
                    semantic_conflicts.append({"filename": meta.filename, **conflict})
                if not quality.get("complete"):
                    failures.append({
                        "filename": meta.filename,
                        "error": "逐表提取未通过质量门禁",
                        "quality": quality,
                    })
            except Exception as exc:
                logger.warning("raw_record_table_extract_failed", filename=meta.filename, error=str(exc)[:300])
                failures.append({"filename": meta.filename, "error": str(exc)[:300]})

    await asyncio.gather(*(process(meta) for meta in metas))
    from services.raw_records_aggregator import summarize_conclusions

    instruments = _deduplicate_instrument_occurrences(code_instruments + llm_instruments)
    deduplicated = deduplicate_instruments(instruments)
    obj["metas"] = [asdict(meta) for meta in metas]
    obj["data_rows"] = [asdict(row) for row in rows]
    obj["instruments"] = [asdict(item) for item in instruments]
    obj["deduplicated_instruments"] = [asdict(item) for item in deduplicated]
    obj["calibration_issues"] = [asdict(item) for item in validate_all_calibrations(deduplicated, metas)]
    obj["conclusion_summary"] = [asdict(item) for item in summarize_conclusions(metas)]
    obj["semantic_extraction_count"] = successful_files
    obj["semantic_extraction_failures"] = failures
    obj["table_quality"] = sorted(quality_results, key=lambda item: item["filename"])
    obj["semantic_conflicts"] = semantic_conflicts
    logger.info(
        "raw_record_hybrid_merge",
        target_files=len(metas),
        semantic_files=obj["semantic_extraction_count"],
        failure_count=len(failures),
        final_rows=len(rows),
        code_instruments=len(code_instruments),
        llm_instruments=len(llm_instruments),
        unique_instruments=len(deduplicated),
        conflict_count=len(semantic_conflicts),
        model=TABLE_MODEL,
    )
    return json.dumps(obj, ensure_ascii=False), failures
