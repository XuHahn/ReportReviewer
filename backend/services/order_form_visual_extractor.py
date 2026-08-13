"""Qwen-vision extraction for image-only order forms.

Digital Excel order forms stay on the deterministic native parser. This module
is used only when the uploaded order form itself is an image.
"""

from __future__ import annotations

import base64
from typing import Literal, Protocol

from pydantic import BaseModel, Field, ValidationError

from models import OrderFormData
from services.unified_model_gateway import UnifiedModelGateway
from utils.logger import get_logger


logger = get_logger(__name__)

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SIG = b"\xff\xd8\xff"
_SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif"}


class OrderFormVisualGateway(Protocol):
    async def call_json(self, task: str, **kwargs) -> dict: ...


class OrderFormVisualEnvelope(BaseModel):
    coverage_state: Literal["complete", "partial", "failed"]
    failed_regions: list[str] = Field(default_factory=list)
    data: OrderFormData
    field_evidence: dict[str, str]


_SYSTEM_PROMPT = """你是工业检测委托单的视觉转录员。只转录图片中明确可见的值，不推断、不补写、不翻译。
必须完整返回且只返回以下 JSON 结构，所有键都必须存在：
{"coverage_state":"complete|partial|failed","failed_regions":[],"data":{"template_id":"","applicant":{"name_en":"","name_cn":"","address_en":"","address_cn":"","contact":{"name":"","email":"","phone":""}},"manufacturer":{"name_en":"","name_cn":"","address_en":"","address_cn":"","contact":{"name":"","email":"","phone":""}},"factory":{"name_en":"","name_cn":"","address_en":"","address_cn":"","contact":{"name":"","email":"","phone":""}},"product":{"name":"","part_number":"","main_test_model":"","trademark":"","voltage":"","work_frequency":""},"test_requirements":{"requirements_desc":"","test_specification":"","decision_rule":"","report_qualification":"","test_purpose":"","report_form":"","no_subcontract_acceptance":""},"other_info":{"software_version":"","hardware_version":"","report_delivery_method":"","sample_disposal_method":"","application_date":""},"raw_notes":[]},"field_evidence":{}}。
field_evidence 对每个非空字段保存图片中的连续原文，键使用点路径，例如 product.name。无法辨认的区域写入 failed_regions，并将 coverage_state 标为 partial 或 failed。"""


_REQUIRED_DATA_KEYS = {
    "template_id", "applicant", "manufacturer", "factory", "product",
    "test_requirements", "other_info", "raw_notes",
}
_REQUIRED_COMPANY_KEYS = {"name_en", "name_cn", "address_en", "address_cn", "contact"}
_REQUIRED_CONTACT_KEYS = {"name", "email", "phone"}
_REQUIRED_PRODUCT_KEYS = {
    "name", "part_number", "main_test_model", "trademark", "voltage", "work_frequency",
}
_REQUIRED_REQUIREMENT_KEYS = {
    "requirements_desc", "test_specification", "decision_rule", "report_qualification",
    "test_purpose", "report_form", "no_subcontract_acceptance",
}
_REQUIRED_OTHER_KEYS = {
    "software_version", "hardware_version", "report_delivery_method",
    "sample_disposal_method", "application_date",
}


def _require_keys(value: object, keys: set[str], path: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} 必须是对象")
    missing = sorted(keys - set(value))
    if missing:
        raise ValueError(f"{path} 缺少字段: {', '.join(missing)}")
    return value


def _validate_complete_shape(raw: dict) -> None:
    _require_keys(raw, {"coverage_state", "failed_regions", "data", "field_evidence"}, "root")
    data = _require_keys(raw["data"], _REQUIRED_DATA_KEYS, "data")
    for company_name in ("applicant", "manufacturer", "factory"):
        company = _require_keys(data[company_name], _REQUIRED_COMPANY_KEYS, f"data.{company_name}")
        _require_keys(
            company["contact"], _REQUIRED_CONTACT_KEYS, f"data.{company_name}.contact",
        )
    _require_keys(data["product"], _REQUIRED_PRODUCT_KEYS, "data.product")
    _require_keys(
        data["test_requirements"], _REQUIRED_REQUIREMENT_KEYS, "data.test_requirements",
    )
    _require_keys(data["other_info"], _REQUIRED_OTHER_KEYS, "data.other_info")


def _image_mime(file_bytes: bytes) -> str:
    if file_bytes.startswith(_PNG_SIG):
        return "image/png"
    if file_bytes.startswith(_JPEG_SIG):
        return "image/jpeg"
    if file_bytes.startswith(b"BM"):
        return "image/bmp"
    if file_bytes.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    raise ValueError("无法识别委托单图片格式")


class OrderFormVisualExtractor:
    @staticmethod
    def can_handle(file_bytes: bytes) -> bool:
        try:
            _image_mime(file_bytes)
            return True
        except ValueError:
            return False

    @staticmethod
    def is_image_extension(filename: str) -> bool:
        lower = filename.lower()
        return any(lower.endswith(extension) for extension in _SUPPORTED_EXTENSIONS)

    @staticmethod
    async def extract(
        file_bytes: bytes,
        gateway: OrderFormVisualGateway | None = None,
    ) -> OrderFormData:
        mime = _image_mime(file_bytes)
        image_url = f"data:{mime};base64,{base64.b64encode(file_bytes).decode('ascii')}"
        model_gateway = gateway or UnifiedModelGateway()
        logger.info("order_form_visual_extraction_started", size_bytes=len(file_bytes))
        raw = await model_gateway.call_json(
            "visual_extraction",
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=[
                {"type": "text", "text": "请逐字段转录这份委托单图片。"},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
            stage="order_form_visual_extraction",
            timeout=240,
            max_tokens=8000,
        )
        _validate_complete_shape(raw)
        try:
            envelope = OrderFormVisualEnvelope.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"委托单千问转录结构校验失败: {exc.error_count()}项") from exc
        if envelope.coverage_state != "complete" or envelope.failed_regions:
            raise ValueError("委托单图片未被千问完整覆盖，请人工核查或上传原始 Excel")
        logger.info(
            "order_form_visual_extraction_completed",
            evidence_field_count=len(envelope.field_evidence),
        )
        return envelope.data
