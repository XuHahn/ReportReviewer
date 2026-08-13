"""Native-first extraction for the unified evidence graph.

Digital pages use their native structure first. Qwen vision is called only for
scanned pages, explicitly detected complex tables, or graph-driven targeted
recovery; there is no unconditional whole-document image transcription pass.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import unicodedata
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError, model_validator

from services.evidence_graph_models import EvidenceRecord, EvidenceState
from services.document_unitizer import locate_layout_quote
from utils.logger import get_logger


logger = get_logger(__name__)


def _channel_timeout(channel: str) -> int:
    if channel == "native":
        return max(10, int(os.getenv("EVIDENCE_GRAPH_NATIVE_TIMEOUT_SEC", "60")))
    return max(10, int(os.getenv("EVIDENCE_GRAPH_VISUAL_TIMEOUT_SEC", "120")))


VisualReason = Literal["none", "scan", "complex_table", "targeted_recovery"]


class ExtractionGateway(Protocol):
    async def call_json(
        self,
        task: str,
        *,
        system_prompt: str,
        user_prompt: str | list[dict],
        stage: str,
        timeout: int,
        max_tokens: int,
    ) -> dict: ...


class EvidenceGraphDocumentUnit(BaseModel):
    unit_id: str = Field(min_length=1)
    graph_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    doc_type: str = Field(min_length=1)
    filename: str = ""
    native_text: str = ""
    page_number: int = Field(default=0, ge=0)
    sheet_name: str = ""
    cell_range: str = ""
    image_data_urls: list[str] = Field(default_factory=list)
    source_hash: str = ""
    rendered_pdf_hash: str = ""
    page_width: float = Field(default=0.0, ge=0.0)
    page_height: float = Field(default=0.0, ge=0.0)
    page_rotation: int = 0
    layout_lines: list[dict] = Field(default_factory=list)
    visual_reason: VisualReason = "none"

    @model_validator(mode="after")
    def validate_source(self):
        if not self.native_text.strip() and not self.image_data_urls:
            raise ValueError("document unit requires native text or a page image")
        if self.visual_reason != "none" and not self.image_data_urls:
            raise ValueError("visual extraction requires page images")
        if self.visual_reason == "scan" and self.native_text.strip():
            raise ValueError("scan unit must not claim usable native text")
        return self


class ObservationProposal(BaseModel):
    proposal_id: str = Field(min_length=1)
    observation_type: Literal[
        "test_item", "parameter", "result", "condition", "sample", "instrument",
        "date", "standard_reference", "person_role", "evidence_artifact",
        "anomaly", "document_field", "other",
    ]
    entity_name: str = Field(min_length=1)
    field_name: str = Field(min_length=1)
    raw_value: Any
    normalized_value: Any = ""
    unit: str = ""
    source_quote: str = Field(min_length=1)
    bbox: list[float] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_bbox(self):
        if self.bbox and len(self.bbox) != 4:
            raise ValueError("bbox must contain [x1, y1, x2, y2]")
        return self


class ObservationEnvelope(BaseModel):
    unit_id: str
    observation_count: int = Field(ge=0, le=500)
    coverage_state: Literal["complete", "partial", "failed"]
    failed_regions: list[str] = Field(default_factory=list)
    observations: list[ObservationProposal] = Field(max_length=500)

    @model_validator(mode="after")
    def validate_count(self):
        if self.observation_count != len(self.observations):
            raise ValueError("observation_count does not match observations length")
        if self.coverage_state == "complete" and self.failed_regions:
            raise ValueError("complete extraction cannot contain failed regions")
        ids = [item.proposal_id for item in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("proposal_id must be unique")
        return self


class MaterializedObservation(BaseModel):
    observation_id: str
    observation_type: str
    entity_name: str
    field_name: str
    raw_value: Any
    normalized_value: Any = ""
    unit: str = ""
    evidence_ids: list[str]
    extraction_sources: list[str]
    confidence: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnifiedExtractionResult(BaseModel):
    unit_id: str
    status: Literal["complete", "needs_review", "failed"]
    observations: list[MaterializedObservation] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    channels_used: list[str] = Field(default_factory=list)
    failed_regions: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    audit_metadata: dict[str, Any] = Field(default_factory=dict)


_OBSERVATION_SYSTEM = """你是工业检测文档的证据转录员。只提取输入中明确出现的观察，不判断对错，不补写未知值。
每条观察必须提供连续原文摘录。测试项目、条件、参数、结果、样品、仪器、日期、标准引用、人员角色、证据制品和异常现象必须分开输出。
父测试与子测试不得合并；同一测试的不同样品、模式、日期或轮次不得合并。
表格中的每个数值单元格单独转录，并在 metadata.row_id 中填写该表内稳定行标识；读值、修正量、结果、限值、余量分别使用 reading、correction_db、result_dbua、limit_dbua、margin_db，禁止自行计算。
总体结论与单项结论分别转录；只有原文明确属于同一汇总范围时才填写相同 conclusion_group，并用 conclusion_scope=overall 或 individual 标记，禁止根据内容猜测归组。
metadata 只记录原文明示的结构关系，不得输出 coverage_complete、detail_region_verified、artifact_coverage_complete 或 field_region_verified，这些覆盖证明只能由系统或人工产生。
只允许 observation_type: test_item, parameter, result, condition, sample, instrument, date, standard_reference, person_role, evidence_artifact, anomaly, document_field, other。
原生文本通道 bbox 返回空数组；视觉通道必须返回页面坐标 [x1,y1,x2,y2]。
必须严格返回且只返回以下完整JSON对象，禁止省略键，禁止添加顶层键：
{"unit_id":"与输入完全一致","observation_count":1,"coverage_state":"complete|partial|failed","failed_regions":[],"observations":[{"proposal_id":"本单元唯一ID","observation_type":"test_item","entity_name":"所属实体或测试项名称","field_name":"稳定英文蛇形字段名","raw_value":"原文值，可为数字、字符串、数组或对象","normalized_value":"规范化值，无则为空字符串","unit":"单位，无则为空字符串","source_quote":"输入中连续逐字原文","bbox":[],"confidence":0.95,"metadata":{"parent_name":"无则空字符串","sample_id":"无则空字符串","mode":"无则空字符串","execution_round":"无则空字符串","row_id":"无则空字符串","conclusion_group":"无则空字符串","conclusion_scope":"overall|individual|空字符串","result":"无则空字符串","parameters":{}}}]}。
observation_count 必须等于 observations 数量。没有观察时返回 observation_count=0 和 observations=[]。完整读取则 coverage_state=complete 且 failed_regions=[]；只有确实存在不可读区域才能返回 partial/failed。仅输出合法 JSON对象，不使用Markdown。"""


_TARGETED_RECOVERY_SYSTEM = """你是工业检测文档的局部视觉补证员。只在提供的页面图像中寻找目标候选项，不扩展到其他项目，不根据期望名称猜测存在。
找到时逐项输出原文、值和 bbox；未找到时返回 complete 和空 observations；页面区域不可读时返回 partial 并说明 failed_regions。
父测试、子测试、条件和结果必须分开。必须严格使用与常规转录相同的完整结构：
{"unit_id":"与输入完全一致","observation_count":0,"coverage_state":"complete|partial|failed","failed_regions":[],"observations":[{"proposal_id":"唯一ID","observation_type":"test_item|parameter|result|condition|sample|instrument|date|standard_reference|person_role|evidence_artifact|anomaly|document_field|other","entity_name":"实体名","field_name":"英文蛇形字段名","raw_value":"原文值","normalized_value":"","unit":"","source_quote":"页面连续原文","bbox":[0,0,0,0],"confidence":0.9,"metadata":{"parent_name":"","sample_id":"","mode":"","execution_round":"","result":"","parameters":{}}}]}。
禁止省略键或添加顶层键。observation_count 必须等于 observations 数量。仅输出合法 JSON对象。"""

# These flags can turn an extraction gap into a confirmed absence. They are
# therefore system/human assertions, never model-provided observation data.
_SYSTEM_OWNED_METADATA = {
    "coverage_complete", "coverage_source",
    "detail_region_verified", "detail_region_verification_source",
    "artifact_coverage_complete", "artifact_coverage_source",
    "field_region_verified", "field_region_verification_source",
}


def _safe_proposal_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key not in _SYSTEM_OWNED_METADATA}

_STANDALONE_PULSE_RE = re.compile(
    r"(?i)^\s*(?:(?:脉冲|pulse)\s*)?P?\s*([1-9]\d?[a-z]?)\s*$",
)
_PULSE_ENUM_RE = re.compile(
    r"(?i)(?:测试脉冲|试验脉冲|test\s*pulses?)\s*[：:]\s*"
    r"((?:P?\s*[1-9]\d?[a-z]?\s*[/、,，]\s*)+P?\s*[1-9]\d?[a-z]?)",
)
_LABELED_PULSE_RE = re.compile(
    r"(?i)(?:测试|试验)脉冲\s*(?:[：:]\s*)?(?:脉冲\s*)?P?\s*([1-9]\d?[a-z]?)",
)
_IMPLICIT_PULSE_SUFFIXES = {"a", "b"}


def _compact(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", value or "")
        if not char.isspace()
    )


def _locate_native_quote(source: str, quote: str) -> str:
    if quote in source:
        return quote
    compact_source: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(source):
        for normalized in unicodedata.normalize("NFKC", char):
            if normalized.isspace():
                continue
            compact_source.append(normalized)
            positions.append(index)
    compact_quote = _compact(quote)
    if not compact_quote:
        return ""
    offset = "".join(compact_source).find(compact_quote)
    if offset < 0:
        return ""
    return source[positions[offset]:positions[offset + len(compact_quote) - 1] + 1]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]
    return f"{prefix}-{digest}"


def _source_anchor_metadata(
    unit: EvidenceGraphDocumentUnit,
    bbox: list[float],
    anchor_quote: str,
    method: str,
) -> dict[str, Any]:
    return {
        "version": 1,
        "status": "located" if len(bbox) == 4 else "text_anchored",
        "coordinate_space": "pdf_points" if len(bbox) == 4 else "",
        "method": method,
        "anchor_quote": anchor_quote,
        "rendered_pdf_hash": unit.rendered_pdf_hash,
        "page_width": unit.page_width,
        "page_height": unit.page_height,
        "page_rotation": unit.page_rotation,
    }


def _native_test_item_markers(unit: EvidenceGraphDocumentUnit) -> UnifiedExtractionResult:
    """Extract only explicit standalone/enumerated pulse identities.

    This deliberately does not infer a parent family.  Parent/subitem identity
    remains the responsibility of deterministic graph matching or the gated
    semantic relationship resolver.
    """
    markers: list[tuple[str, str]] = []
    for raw_line in unit.native_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        standalone = _STANDALONE_PULSE_RE.fullmatch(line)
        marker = standalone.group(1) if standalone else ""
        suffix = marker[-1:].casefold() if marker[-1:].isalpha() else ""
        has_explicit_prefix = line.casefold().startswith(("p", "pulse", "脉冲"))
        if standalone and (has_explicit_prefix or suffix in _IMPLICIT_PULSE_SUFFIXES):
            code = f"P{standalone.group(1).upper()}"
            markers.append((code, line))
            continue
        enumeration = _PULSE_ENUM_RE.search(line)
        if enumeration:
            for token in re.split(r"[/、,，]", enumeration.group(1)):
                normalized = re.sub(r"(?i)^\s*P\s*", "", token).strip()
                if re.fullmatch(r"(?i)[1-9]\d?[a-z]?", normalized):
                    markers.append((f"P{normalized.upper()}", line))
        for labeled in _LABELED_PULSE_RE.finditer(line):
            markers.append((f"P{labeled.group(1).upper()}", line))

    evidence: list[EvidenceRecord] = []
    observations: list[MaterializedObservation] = []
    seen: set[tuple[str, str]] = set()
    for code, quote in markers:
        signature = (code, quote)
        if signature in seen:
            continue
        seen.add(signature)
        evidence_id = _stable_id(
            "evidence", unit.graph_id, unit.doc_id, unit.unit_id,
            "native_identity_marker", code, quote,
        )
        observation_id = _stable_id(
            "observation", unit.graph_id, unit.unit_id, "test_item", code, quote,
        )
        bbox, anchor_quote = locate_layout_quote(unit.layout_lines, quote)
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id,
            graph_id=unit.graph_id,
            doc_id=unit.doc_id,
            doc_type=unit.doc_type,
            filename=unit.filename,
            state=EvidenceState.FOUND,
            page_number=unit.page_number,
            sheet_name=unit.sheet_name,
            cell_range=unit.cell_range,
            bbox=bbox,
            exact_quote=quote,
            content_hash=unit.source_hash,
            extraction_method="native_identity_marker",
            confidence=1,
            metadata={
                "unit_id": unit.unit_id,
                "identity_code": code,
                "source_anchor": _source_anchor_metadata(
                    unit, bbox, anchor_quote, "native_layout_line",
                ),
            },
        ))
        observations.append(MaterializedObservation(
            observation_id=observation_id,
            observation_type="test_item",
            entity_name=code,
            field_name="test_item_presence",
            raw_value=quote,
            normalized_value=code,
            evidence_ids=[evidence_id],
            extraction_sources=["native_identity_marker"],
            confidence=1,
            metadata={"identity_code": code},
        ))
    return UnifiedExtractionResult(
        unit_id=unit.unit_id,
        status="complete",
        observations=observations,
        evidence=evidence,
        channels_used=["native_identity_marker"] if observations else [],
    )


def _native_prompt(unit: EvidenceGraphDocumentUnit) -> str:
    return json.dumps({
        "unit_id": unit.unit_id,
        "doc_type": unit.doc_type,
        "filename": unit.filename,
        "page_number": unit.page_number,
        "sheet_name": unit.sheet_name,
        "cell_range": unit.cell_range,
        "native_text": unit.native_text,
    }, ensure_ascii=False)


def _visual_prompt(unit: EvidenceGraphDocumentUnit, *, targets: list[str] | None = None) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": json.dumps({
        "unit_id": unit.unit_id,
        "doc_type": unit.doc_type,
        "filename": unit.filename,
        "page_number": unit.page_number,
        "native_text_context": unit.native_text,
        "targets": targets or [],
        "visual_reason": unit.visual_reason,
    }, ensure_ascii=False)}]
    content.extend(
        {"type": "image_url", "image_url": {"url": image_url}}
        for image_url in unit.image_data_urls
    )
    return content


def _parse_channel(raw: dict, unit: EvidenceGraphDocumentUnit,
                   channel: Literal["native", "qwen_vision"]
                   ) -> tuple[ObservationEnvelope | None, list[str]]:
    normalized_raw = dict(raw)
    normalized_observations: list[ObservationProposal] = []
    repaired_entity_names = 0
    errors: list[str] = []
    raw_observations = raw.get("observations")
    if not isinstance(raw_observations, list):
        logger.warning(
            "evidence_graph_extraction_schema_rejected",
            unit_id=unit.unit_id,
            doc_type=unit.doc_type,
            channel=channel,
            error_count=1,
        )
        return None, [f"{channel} JSON结构校验失败: observations不是数组"]

    seen_proposal_ids: set[str] = set()
    for index, value in enumerate(raw_observations):
        if not isinstance(value, dict):
            errors.append(f"{channel}第{index + 1}条提议JSON结构校验失败")
            continue
        observation = dict(value)
        if not str(observation.get("entity_name") or "").strip():
            raw_value = observation.get("raw_value")
            fallback = (
                raw_value if isinstance(raw_value, (str, int, float))
                else observation.get("field_name")
            )
            if str(fallback or "").strip():
                observation["entity_name"] = str(fallback).strip()
                repaired_entity_names += 1
        # Native evidence never needs a model-generated bbox.  Normalize it
        # before schema validation so one malformed bbox cannot discard every
        # other valid observation on the page.
        if channel == "native":
            observation["bbox"] = []
        try:
            proposal = ObservationProposal.model_validate(observation)
        except ValidationError:
            errors.append(f"{channel}第{index + 1}条提议JSON结构校验失败")
            continue
        if proposal.proposal_id in seen_proposal_ids:
            errors.append(f"{channel}提议ID重复: {proposal.proposal_id}")
            continue
        seen_proposal_ids.add(proposal.proposal_id)
        normalized_observations.append(proposal)

    normalized_raw["observations"] = normalized_observations
    declared_count = raw.get("observation_count")
    if declared_count != len(raw_observations):
        errors.append(f"{channel} observation_count与返回数组长度不一致")
    normalized_raw["observation_count"] = len(normalized_observations)
    if normalized_raw.get("coverage_state") == "complete" and normalized_raw.get("failed_regions"):
        normalized_raw["coverage_state"] = "partial"
        errors.append(f"{channel}完整性状态与失败区域冲突，已降级为partial")
    try:
        envelope = ObservationEnvelope.model_validate(normalized_raw)
    except ValidationError as exc:
        logger.warning(
            "evidence_graph_extraction_schema_rejected",
            unit_id=unit.unit_id,
            doc_type=unit.doc_type,
            channel=channel,
            error_count=exc.error_count(),
        )
        return None, [*errors, f"{channel} JSON结构校验失败: {exc.error_count()}项"]
    if repaired_entity_names:
        logger.info(
            "evidence_graph_extraction_shape_normalized",
            unit_id=unit.unit_id,
            doc_type=unit.doc_type,
            channel=channel,
            repaired_entity_name_count=repaired_entity_names,
        )
    if envelope.unit_id != unit.unit_id:
        return None, [f"{channel}返回了错误的unit_id"]

    valid: list[ObservationProposal] = []
    for proposal in envelope.observations:
        if channel == "native":
            exact_quote = _locate_native_quote(unit.native_text, proposal.source_quote)
            if not exact_quote:
                errors.append(f"native提议{proposal.proposal_id}的原文摘录无法定位")
                continue
            proposal.source_quote = exact_quote
            proposal.bbox, _ = locate_layout_quote(unit.layout_lines, exact_quote)
        elif len(proposal.bbox) != 4 or not unit.image_data_urls:
            errors.append(f"qwen_vision提议{proposal.proposal_id}缺少页面坐标")
            continue
        valid.append(proposal)
    envelope.observations = valid
    envelope.observation_count = len(valid)
    return envelope, errors


def _materialize(unit: EvidenceGraphDocumentUnit,
                 channels: list[tuple[str, ObservationEnvelope]],
                 errors: list[str]) -> UnifiedExtractionResult:
    evidence: list[EvidenceRecord] = []
    grouped: dict[tuple[str, str, str, str, str], MaterializedObservation] = {}
    failed_regions: list[str] = []
    for channel, envelope in channels:
        failed_regions.extend(envelope.failed_regions)
        for proposal in envelope.observations:
            proposal_metadata = _safe_proposal_metadata(proposal.metadata)
            anchor_quote = ""
            if channel == "native":
                proposal.bbox, anchor_quote = locate_layout_quote(
                    unit.layout_lines, proposal.source_quote,
                )
            evidence_id = _stable_id(
                "evidence", unit.graph_id, unit.doc_id, unit.unit_id, channel,
                proposal.proposal_id, proposal.source_quote,
            )
            evidence.append(EvidenceRecord(
                evidence_id=evidence_id,
                graph_id=unit.graph_id,
                doc_id=unit.doc_id,
                doc_type=unit.doc_type,
                filename=unit.filename,
                state=EvidenceState.FOUND,
                page_number=unit.page_number,
                sheet_name=unit.sheet_name,
                cell_range=unit.cell_range,
                bbox=proposal.bbox,
                exact_quote=proposal.source_quote,
                content_hash=unit.source_hash,
                extraction_method=channel,
                confidence=proposal.confidence,
                metadata={
                    "unit_id": unit.unit_id,
                    **proposal_metadata,
                    "source_anchor": _source_anchor_metadata(
                        unit,
                        proposal.bbox,
                        anchor_quote,
                        "native_layout_line" if channel == "native" else "visual_model_bbox",
                    ),
                },
            ))
            signature = (
                proposal.observation_type,
                _compact(proposal.entity_name).casefold(),
                _compact(proposal.field_name).casefold(),
                json.dumps(proposal.normalized_value, ensure_ascii=False, sort_keys=True, default=str),
                _compact(proposal.unit).casefold(),
            )
            existing = grouped.get(signature)
            if existing is None:
                observation_id = _stable_id(
                    "observation", unit.graph_id, unit.unit_id, *signature,
                )
                grouped[signature] = MaterializedObservation(
                    observation_id=observation_id,
                    observation_type=proposal.observation_type,
                    entity_name=proposal.entity_name,
                    field_name=proposal.field_name,
                    raw_value=proposal.raw_value,
                    normalized_value=proposal.normalized_value,
                    unit=proposal.unit,
                    evidence_ids=[evidence_id],
                    extraction_sources=[channel],
                    confidence=proposal.confidence,
                    # Document provenance is system-owned. Keeping it on the
                    # observation lets deterministic checks compare like
                    # fields without trusting model-provided metadata.
                    metadata={
                        **proposal_metadata,
                        "doc_type": unit.doc_type,
                        "doc_id": unit.doc_id,
                        "unit_id": unit.unit_id,
                        "unit_extraction_complete": envelope.coverage_state == "complete",
                    },
                )
            else:
                existing.evidence_ids.append(evidence_id)
                if channel not in existing.extraction_sources:
                    existing.extraction_sources.append(channel)
                existing.confidence = max(existing.confidence, proposal.confidence)

    complete_channels = all(envelope.coverage_state == "complete" for _, envelope in channels)
    status: Literal["complete", "needs_review", "failed"] = (
        "complete" if channels and complete_channels and not errors else "needs_review"
    )
    return UnifiedExtractionResult(
        unit_id=unit.unit_id,
        status=status,
        observations=list(grouped.values()),
        evidence=evidence,
        channels_used=[channel for channel, _ in channels],
        failed_regions=list(dict.fromkeys(failed_regions)),
        errors=errors,
    )


async def extract_evidence_graph_unit(
    unit: EvidenceGraphDocumentUnit,
    gateway: ExtractionGateway,
) -> UnifiedExtractionResult:
    marker_result = (
        _native_test_item_markers(unit)
        if unit.doc_type in {"test_plan", "original_records", "final_report"}
        and unit.native_text.strip()
        else None
    )
    tasks: list[tuple[str, asyncio.Task]] = []
    if unit.native_text.strip():
        native_timeout = _channel_timeout("native")
        tasks.append(("native", asyncio.create_task(gateway.call_json(
            "structured_extraction",
            system_prompt=_OBSERVATION_SYSTEM,
            user_prompt=_native_prompt(unit),
            stage="evidence_graph_native_extraction",
            timeout=native_timeout,
            max_tokens=16000,
        ))))
    if unit.visual_reason != "none":
        visual_timeout = _channel_timeout("visual")
        tasks.append(("qwen_vision", asyncio.create_task(gateway.call_json(
            "visual_extraction",
            system_prompt=_OBSERVATION_SYSTEM,
            user_prompt=_visual_prompt(unit),
            stage="evidence_graph_visual_extraction",
            timeout=visual_timeout,
            max_tokens=16000,
        ))))

    results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
    parsed_channels: list[tuple[str, ObservationEnvelope]] = []
    errors: list[str] = []
    for (channel, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            logger.warning(
                "evidence_graph_extraction_channel_failed",
                unit_id=unit.unit_id,
                doc_type=unit.doc_type,
                channel=channel,
                error_type=type(result).__name__,
            )
            errors.append(f"{channel}提取失败或超时")
            continue
        envelope, channel_errors = _parse_channel(result, unit, channel)  # type: ignore[arg-type]
        errors.extend(channel_errors)
        if envelope is not None:
            parsed_channels.append((channel, envelope))

    if not parsed_channels:
        if marker_result and marker_result.observations:
            return marker_result.model_copy(update={
                "status": "needs_review",
                "errors": errors or ["语义提取不可用，仅保留确定性身份标记"],
            })
        return UnifiedExtractionResult(
            unit_id=unit.unit_id,
            status="failed",
            channels_used=[channel for channel, _ in tasks],
            errors=errors or ["没有可用提取通道"],
        )

    result = _materialize(unit, parsed_channels, errors)
    if marker_result and marker_result.observations:
        existing_ids = {item.observation_id for item in result.observations}
        result.observations.extend(
            item for item in marker_result.observations if item.observation_id not in existing_ids
        )
        existing_evidence_ids = {item.evidence_id for item in result.evidence}
        result.evidence.extend(
            item for item in marker_result.evidence if item.evidence_id not in existing_evidence_ids
        )
        result.channels_used = list(dict.fromkeys([
            *result.channels_used, *marker_result.channels_used,
        ]))
    logger.info(
        "evidence_graph_unit_extracted",
        unit_id=unit.unit_id,
        doc_type=unit.doc_type,
        visual_reason=unit.visual_reason,
        channels=result.channels_used,
        observation_count=len(result.observations),
        evidence_count=len(result.evidence),
        error_count=len(result.errors),
        status=result.status,
    )
    return result


async def recover_targeted_evidence(
    unit: EvidenceGraphDocumentUnit,
    target_names: list[str],
    gateway: ExtractionGateway,
) -> UnifiedExtractionResult:
    if not unit.image_data_urls:
        return UnifiedExtractionResult(
            unit_id=unit.unit_id,
            status="failed",
            errors=["局部视觉补证缺少页面图像"],
        )
    targets = [name.strip() for name in target_names if name.strip()]
    if not targets:
        return UnifiedExtractionResult(
            unit_id=unit.unit_id,
            status="failed",
            errors=["局部视觉补证缺少目标项目"],
        )

    recovery_unit = unit.model_copy(update={"visual_reason": "targeted_recovery"})
    raw = await gateway.call_json(
        "visual_extraction",
        system_prompt=_TARGETED_RECOVERY_SYSTEM,
        user_prompt=_visual_prompt(recovery_unit, targets=targets),
        stage="evidence_graph_targeted_visual_recovery",
        timeout=300,
        max_tokens=4096,
    )
    envelope, errors = _parse_channel(raw, recovery_unit, "qwen_vision")
    if envelope is None:
        return UnifiedExtractionResult(
            unit_id=unit.unit_id,
            status="failed",
            channels_used=["qwen_vision"],
            errors=errors,
        )
    result = _materialize(recovery_unit, [("qwen_vision", envelope)], errors)
    for evidence in result.evidence:
        evidence.metadata.update({
            "targeted_recovery": True,
            "target_name_hashes": [
                hashlib.sha256(name.encode()).hexdigest()[:16] for name in targets
            ],
        })
    logger.info(
        "evidence_graph_targeted_recovery",
        unit_id=unit.unit_id,
        doc_type=unit.doc_type,
        target_count=len(targets),
        observation_count=len(result.observations),
        status=result.status,
    )
    return result
