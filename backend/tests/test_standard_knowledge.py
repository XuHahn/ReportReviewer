import uuid

import fitz
import pytest

import database
import services.standard_knowledge as standard_knowledge
from services.standard_knowledge import StandardPage, build_standard_chunks


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_standard_chunks_keep_page_evidence():
    pages = [
        StandardPage(4, "4 Test pulse requirements\nThe pulse amplitude shall be 12 V."),
        StandardPage(5, "4.1 Test pulse 4\nRise time and duration are defined in Table 3."),
    ]
    chunks = build_standard_chunks(pages)

    assert len(chunks) == 1
    assert chunks[0]["page_start"] == 4
    assert chunks[0]["page_end"] == 5
    assert "[第 4 页" in chunks[0]["content"]
    assert "Test pulse 4" in chunks[0]["content"]


def test_standard_pdf_falls_back_to_renderable_pages_when_native_reader_fails(monkeypatch):
    document = fitz.open()
    document.new_page()
    document.new_page()
    pdf_bytes = document.tobytes()
    document.close()

    def fail_reader(*_args, **_kwargs):
        raise standard_knowledge.pypdf.errors.PdfStreamError("damaged EOF")

    monkeypatch.setattr(standard_knowledge.pypdf, "PdfReader", fail_reader)
    pages = standard_knowledge.extract_standard_pages(pdf_bytes, "scanner.pdf")

    assert [page.number for page in pages] == [1, 2]
    assert all(page.text == "" for page in pages)
    assert all(page.extraction == "native_failed" for page in pages)


@pytest.mark.asyncio
async def test_standard_visual_recovery_uses_bounded_concurrency(monkeypatch):
    active = 0
    peak = 0

    class Gateway:
        async def call_json(self, task, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await standard_knowledge.asyncio.sleep(0.01)
            active -= 1
            page_number = int(standard_knowledge.json.loads(
                kwargs["user_prompt"][0]["text"],
            )["page_number"])
            return {
                "page_number": page_number,
                "text": f"第 {page_number} 页完整标准文本",
                "coverage_state": "complete",
                "failed_regions": [],
            }

    monkeypatch.setattr(standard_knowledge, "STANDARD_VISION_CONCURRENCY", 2)
    monkeypatch.setattr(
        standard_knowledge,
        "_render_standard_page_data_urls",
        lambda *_args: {index: "data:image/png;base64,AA==" for index in range(1, 6)},
    )
    pages = await standard_knowledge.recover_standard_visual_pages(
        b"fake-pdf",
        [StandardPage(index, "", "native_failed") for index in range(1, 6)],
        gateway=Gateway(),
    )

    assert peak == 2
    assert len(pages) == 5


@pytest.mark.asyncio
async def test_sparse_standard_page_uses_qwen_visual_gateway(monkeypatch):
    class Gateway:
        async def call_json(self, task, **kwargs):
            assert task == "visual_extraction"
            return {
                "page_number": 2,
                "text": "5.6.2 Test pulse 2a shall be applied.",
                "coverage_state": "complete",
                "failed_regions": [],
            }

    monkeypatch.setattr(
        standard_knowledge,
        "_render_standard_page_data_urls",
        lambda *_args: {2: "data:image/png;base64,AA=="},
    )
    pages = await standard_knowledge.recover_standard_visual_pages(
        b"fake-pdf",
        [
            StandardPage(1, "A" * 100, "text"),
            StandardPage(2, "", "text"),
        ],
        gateway=Gateway(),
    )
    assert pages[0].extraction == "text"
    assert pages[1].extraction == "qwen_vision"
    assert "pulse 2a" in pages[1].text


@pytest.mark.asyncio
async def test_truly_blank_standard_page_skips_visual_gateway():
    document = fitz.open()
    document.new_page()
    pdf_bytes = document.tobytes()
    document.close()

    class Gateway:
        async def call_json(self, *_args, **_kwargs):
            raise AssertionError("blank page must not call the visual model")

    pages = await standard_knowledge.recover_standard_visual_pages(
        pdf_bytes, [StandardPage(1, "", "text")], gateway=Gateway(),
    )

    assert pages == [StandardPage(1, "", "blank")]


def test_standard_asset_deduplicates_by_file_hash(_seeded_db):
    content = b"same enterprise standard content"
    code = _unique("Q-STD")
    first_id, first_duplicate = database.create_standard_asset(
        content, "enterprise-standard.pdf", code=code, version="2026",
    )
    second_id, second_duplicate = database.create_standard_asset(
        content, "renamed-copy.pdf", code=_unique("Q-COPY"), version="2026",
    )

    assert first_duplicate is False
    assert second_duplicate is True
    assert second_id == first_id


def test_document_set_accepts_only_published_standards(_seeded_db):
    set_id = database.create_document_set("reviewer1")
    standard_id, _ = database.create_standard_asset(
        b"pending-standard", "pending.pdf", code=_unique("ISO"), version="2026",
    )

    with pytest.raises(ValueError, match="人工确认并发布"):
        database.set_document_set_standards(set_id, [standard_id], "reviewer1")

    database.update_standard_knowledge(standard_id, status="ready", page_count=1)
    with pytest.raises(ValueError, match="人工确认并发布"):
        database.set_document_set_standards(set_id, [standard_id], "reviewer1")

    chunk_id = uuid.uuid4().hex[:16]
    database.replace_standard_knowledge_chunks(standard_id, [{
        "id": chunk_id, "page_start": 1, "page_end": 1,
        "content": "The voltage shall be 12 V.",
    }])
    database.replace_standard_graph(standard_id, [{
        "clause_number": "1", "source_chunk_id": chunk_id,
        "requirements": [{
            "statement": "电压应为 12 V", "interpretation_zh": "电压应为 12 V",
            "original_statement": "The voltage shall be 12 V.",
            "evidence_quote": "The voltage shall be 12 V.",
        }],
    }], {"failed_unit_ids": []})
    requirement = database.get_standard_graph(standard_id)["requirements"][0]
    database.review_standard_requirement(
        standard_id, requirement["id"], "confirmed", "", "reviewer1",
        {"interpretation_zh": "电压应为 12 V"},
    )
    release = database.publish_standard_graph(standard_id, "reviewer1")
    selected = database.set_document_set_standards(set_id, [standard_id], "reviewer1")
    assert [item.id for item in selected] == [standard_id]
    assert selected[0].selected_release_id == release["release_id"]
    assert selected[0].selected_release_number == 1

    cleared = database.set_document_set_standards(set_id, [], "reviewer1")
    assert cleared == []
    assert database.get_document_set_standards(set_id) == []


@pytest.mark.asyncio
async def test_set_standard_api_allows_empty_selection(reviewer_client):
    created = await reviewer_client.post("/api/sets")
    assert created.status_code == 200
    set_id = created.json()["set_id"]

    response = await reviewer_client.put(
        f"/api/sets/{set_id}/standards", json={"standard_ids": []},
    )
    assert response.status_code == 200
    assert response.json()["standards"] == []
    assert response.json()["standard_review_enabled"] is False

    overview = await reviewer_client.get(f"/api/sets/{set_id}")
    assert overview.status_code == 200
    assert overview.json()["standard_review_enabled"] is False


@pytest.mark.asyncio
async def test_explicit_upload_metadata_wins_over_llm(_seeded_db, monkeypatch):
    standard_id, _ = database.create_standard_asset(
        b"fake-pdf", "ISO 7637-2 2011.pdf", code="ISO 7637-2",
        title="Authoritative title", organization="ISO", category="综合EMC",
        version="2011",
    )
    monkeypatch.setattr(
        standard_knowledge, "extract_standard_pages",
        lambda *_args: [StandardPage(
            1,
            "5.6.4 Test pulse requirements. This digitally extracted page contains "
            "enough native text to skip visual recovery.",
        )],
    )

    async def shortened_metadata(*_args):
        return {
            "code": "7637-2", "title": "AI title", "organization": "",
            "category": "其他", "version": "",
        }

    monkeypatch.setattr(standard_knowledge, "_extract_metadata_with_llm", shortened_metadata)
    await standard_knowledge.build_standard_knowledge(standard_id)
    saved = database.get_standard(standard_id)

    assert saved is not None
    assert saved.code == "ISO 7637-2"
    assert saved.title == "Authoritative title"
    assert saved.organization == "ISO"
    assert saved.category == "综合EMC"
    assert saved.version == "2011"


def test_document_set_keeps_bound_release_after_new_publish(_seeded_db):
    set_id = database.create_document_set("reviewer1")
    standard_id, _ = database.create_standard_asset(
        b"immutable-release-standard", "immutable.pdf",
        code=_unique("ISO"), version="2026",
    )
    database.update_standard_knowledge(standard_id, status="ready", page_count=1)
    chunk_id = uuid.uuid4().hex[:16]
    database.replace_standard_knowledge_chunks(standard_id, [{
        "id": chunk_id, "page_start": 1, "page_end": 1,
        "content": "The voltage shall be 12 V. The voltage shall be 24 V.",
    }])

    def publish(original: str, translation: str):
        database.replace_standard_graph(standard_id, [{
            "clause_number": "1", "source_chunk_id": chunk_id,
            "requirements": [{
                "original_statement": original, "interpretation_zh": translation,
                "evidence_quote": original,
            }],
        }], {"failed_unit_ids": []})
        requirement = database.get_standard_graph(standard_id)["requirements"][0]
        database.review_standard_requirement(
            standard_id, requirement["id"], "confirmed", "", "reviewer1",
            {"interpretation_zh": translation},
        )
        return database.publish_standard_graph(standard_id, "reviewer1")

    release1 = publish("The voltage shall be 12 V.", "电压应为 12 V")
    selected = database.set_document_set_standards(set_id, [standard_id], "reviewer1")
    assert selected[0].selected_release_id == release1["release_id"]
    release2 = publish("The voltage shall be 24 V.", "电压应为 24 V")

    still_selected = database.get_document_set_standards(set_id)[0]
    assert still_selected.selected_release_id == release1["release_id"]
    assert still_selected.selected_release_number == 1
    assert database.get_standard(standard_id).latest_release_id == release2["release_id"]
    assert database.get_standard(standard_id).latest_release_number == 2


def test_explicit_standard_references_require_selection_or_skip(_seeded_db):
    set_id = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(
        set_id, "test_plan", "plan.pdf", plain_text=(
            "系统12V电源电压波动试验参考 ISO 7637-2:2011 第 5.6.4 条执行。"
        ),
    )
    database.update_doc_extraction_status(
        doc_id, "done", "系统12V电源电压波动试验参考 ISO 7637-2:2011 第 5.6.4 条执行。",
    )
    resolution = database.get_document_set_standard_resolution(set_id)
    assert [item["reference_code"] for item in resolution["unresolved"]] == ["ISO 7637-2:2011"]

    database.set_document_set_standard_skips(set_id, [{
        "reference_code": "ISO 7637-2:2011",
        "normalized_code": "ISO763722011",
        "reason": "客户确认本轮不执行该标准条款审查",
    }], "reviewer1")
    resolved = database.get_document_set_standard_resolution(set_id)
    assert resolved["unresolved"] == []
    assert resolved["resolved"][0]["resolution"] == "skipped"


def test_human_requirement_mapping_supports_coverage_and_no_coverage(_seeded_db):
    standard_id, _ = database.create_standard_asset(
        b"mapping-standard", "mapping.pdf", code=_unique("ISO"), version="2026",
    )
    database.update_standard_knowledge(standard_id, status="ready", page_count=1)
    chunk_id = uuid.uuid4().hex[:16]
    database.replace_standard_knowledge_chunks(standard_id, [{
        "id": chunk_id, "page_start": 1, "page_end": 1,
        "content": "Test pulse 4 shall use -6 V.",
    }])
    database.replace_standard_graph(standard_id, [{
        "clause_number": "5.6.4", "source_chunk_id": chunk_id,
        "requirements": [{
            "test_item": "Test pulse 4",
            "original_statement": "Test pulse 4 shall use -6 V.",
            "interpretation_zh": "测试脉冲4应使用 -6 V。",
            "evidence_quote": "Test pulse 4 shall use -6 V.",
        }],
    }], {"failed_unit_ids": []})
    requirement = database.get_standard_graph(standard_id)["requirements"][0]
    database.review_standard_requirement(
        standard_id, requirement["id"], "confirmed", "", "reviewer1",
        {"interpretation_zh": "测试脉冲4应使用 -6 V。"},
    )
    release = database.publish_standard_graph(standard_id, "reviewer1")
    covered = database.save_standard_requirement_mapping(
        standard_id, release["release_id"], "系统12V电压波动", "covered",
        requirement["id"], "standard", "", "人工确认同一项目", "reviewer1",
    )
    assert covered["mapping_type"] == "covered"
    assert database.get_standard_requirement_mappings(
        standard_id, release["release_id"], "系统12V电压波动",
    )[0]["requirement_id"] == requirement["id"]
    no_coverage = database.save_standard_requirement_mapping(
        standard_id, release["release_id"], "盐雾试验", "not_covered",
        "", "standard", "", "该标准不包含盐雾要求", "reviewer1",
    )
    assert no_coverage["requirement_id"] == ""


def test_project_mapping_applies_only_to_document_sets_in_that_group(_seeded_db):
    set_id = database.create_document_set("reviewer1")
    group_id = database.create_project_group(
        _unique("项目"), "标准映射测试", "reviewer1", ["reviewer1"], "测试",
    )
    bound = database.set_document_set_project_group(set_id, group_id)
    assert bound["project_group_id"] == group_id
    assert database.get_document_set(set_id)["project_group_id"] == group_id

    standard_id, _ = database.create_standard_asset(
        b"project-mapping-standard", "project-mapping.pdf",
        code=_unique("ISO"), version="2026",
    )
    database.update_standard_knowledge(standard_id, status="ready", page_count=1)
    chunk_id = uuid.uuid4().hex[:16]
    database.replace_standard_knowledge_chunks(standard_id, [{
        "id": chunk_id, "page_start": 1, "page_end": 1,
        "content": "Test pulse 4 shall use -6 V.",
    }])
    database.replace_standard_graph(standard_id, [{
        "clause_number": "5.6.4", "source_chunk_id": chunk_id,
        "requirements": [{
            "test_item": "Test pulse 4",
            "original_statement": "Test pulse 4 shall use -6 V.",
            "interpretation_zh": "测试脉冲4应使用 -6 V。",
            "evidence_quote": "Test pulse 4 shall use -6 V.",
        }],
    }], {"failed_unit_ids": []})
    requirement = database.get_standard_graph(standard_id)["requirements"][0]
    database.review_standard_requirement(
        standard_id, requirement["id"], "confirmed", "", "reviewer1",
        {"interpretation_zh": "测试脉冲4应使用 -6 V。"},
    )
    release = database.publish_standard_graph(standard_id, "reviewer1")
    database.save_standard_requirement_mapping(
        standard_id, release["release_id"], "系统12V电压波动", "covered",
        requirement["id"], "project", group_id, "项目组确认", "reviewer1",
    )

    matched = database.get_standard_requirement_mappings(
        standard_id, release["release_id"], "系统12V电压波动",
        set_id=set_id, project_id=database.get_document_set_project_group_id(set_id),
    )
    unmatched = database.get_standard_requirement_mappings(
        standard_id, release["release_id"], "系统12V电压波动",
        set_id=_unique("other-set"), project_id=_unique("other-project"),
    )
    assert matched[0]["scope_type"] == "project"
    assert unmatched == []


@pytest.mark.asyncio
async def test_document_set_project_group_api_persists_selection(reviewer_client, _seeded_db):
    group_id = database.create_project_group(
        _unique("项目"), "API 绑定测试", "reviewer1", ["reviewer1"], "测试",
    )
    created = await reviewer_client.post("/api/sets")
    set_id = created.json()["set_id"]
    response = await reviewer_client.put(
        f"/api/sets/{set_id}/project-group",
        json={"project_group_id": group_id},
    )
    assert response.status_code == 200
    assert response.json()["project_group_id"] == group_id
    overview = await reviewer_client.get(f"/api/sets/{set_id}")
    assert overview.json()["project_group_id"] == group_id


def test_only_explicit_standard_codes_are_cross_standard_dependencies():
    assert standard_knowledge._is_external_standard_reference("ISO 7637-1:2015")
    assert standard_knowledge._is_external_standard_reference("GB/T 18655-2018")
    assert standard_knowledge._is_external_standard_reference("Q/ABC 123-2026")
    assert not standard_knowledge._is_external_standard_reference("Table 4")
    assert not standard_knowledge._is_external_standard_reference("this document")
    assert not standard_knowledge._is_external_standard_reference("客户试验大纲")
