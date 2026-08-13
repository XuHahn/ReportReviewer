import pytest

from services.order_form_visual_extractor import OrderFormVisualExtractor


def _complete_payload() -> dict:
    empty_company = {
        "name_en": "", "name_cn": "", "address_en": "", "address_cn": "",
        "contact": {"name": "", "email": "", "phone": ""},
    }
    return {
        "coverage_state": "complete",
        "failed_regions": [],
        "data": {
            "template_id": "GRGJL.WI-EMC-11-117",
            "applicant": {**empty_company, "name_cn": "测试公司"},
            "manufacturer": empty_company,
            "factory": empty_company,
            "product": {
                "name": "车灯", "part_number": "", "main_test_model": "P59A",
                "trademark": "", "voltage": "12 V", "work_frequency": "",
            },
            "test_requirements": {
                "requirements_desc": "", "test_specification": "ISO 7637-2",
                "decision_rule": "", "report_qualification": "", "test_purpose": "",
                "report_form": "中文", "no_subcontract_acceptance": "",
            },
            "other_info": {
                "software_version": "", "hardware_version": "",
                "report_delivery_method": "", "sample_disposal_method": "",
                "application_date": "20260716",
            },
            "raw_notes": [],
        },
        "field_evidence": {"product.name": "产品名：车灯"},
    }


class FakeGateway:
    def __init__(self, result: dict):
        self.result = result
        self.calls = []

    async def call_json(self, task: str, **kwargs):
        self.calls.append((task, kwargs))
        return self.result


def test_image_detection():
    assert OrderFormVisualExtractor.can_handle(b"\x89PNG\r\n\x1a\n" + b"x")
    assert OrderFormVisualExtractor.can_handle(b"\xff\xd8\xff" + b"x")
    assert not OrderFormVisualExtractor.can_handle(b"\xd0\xcf\x11\xe0")
    assert OrderFormVisualExtractor.is_image_extension("form.PNG")
    assert not OrderFormVisualExtractor.is_image_extension("form.xls")


@pytest.mark.asyncio
async def test_visual_extraction_uses_unified_gateway():
    gateway = FakeGateway(_complete_payload())
    data = await OrderFormVisualExtractor.extract(
        b"\x89PNG\r\n\x1a\n" + b"image", gateway=gateway,
    )
    assert data.product.name == "车灯"
    assert data.product.main_test_model == "P59A"
    assert gateway.calls[0][0] == "visual_extraction"


@pytest.mark.asyncio
async def test_visual_extraction_rejects_incomplete_json():
    payload = _complete_payload()
    del payload["data"]["product"]["voltage"]
    with pytest.raises(ValueError, match="缺少字段"):
        await OrderFormVisualExtractor.extract(
            b"\x89PNG\r\n\x1a\n" + b"image", gateway=FakeGateway(payload),
        )


@pytest.mark.asyncio
async def test_visual_extraction_rejects_partial_coverage():
    payload = _complete_payload()
    payload["coverage_state"] = "partial"
    payload["failed_regions"] = ["右下角"]
    with pytest.raises(ValueError, match="未被千问完整覆盖"):
        await OrderFormVisualExtractor.extract(
            b"\x89PNG\r\n\x1a\n" + b"image", gateway=FakeGateway(payload),
        )
