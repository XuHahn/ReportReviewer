"""Tests for DocumentSet CRUD, versioning, locking, diff."""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import fitz
import pytest

import database
from services.document_set import DocumentSetService
from services.evidence_graph_models import EvidenceRecord, GraphRun, GraphScope, ReviewFinding
from services.evidence_graph_store import EvidenceGraphStore
from routers.document_set import _evidence_anchor_signature


# ── Fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def set_id(_seeded_db):
    return database.create_document_set("test_user")


# ── Set CRUD ───────────────────────────────────────────────────────────

def test_create_set(_seeded_db):
    sid = database.create_document_set("user1")
    assert sid.startswith("EMC-")
    assert len(sid) > 10


def test_get_set_empty(set_id: str):
    ds = database.get_document_set(set_id)
    assert ds is not None
    assert ds["status"] == "incomplete"
    assert len(ds["documents"]) == 0
    assert len(ds["missing_types"]) == 4


def test_list_sets(set_id: str):
    sets = database.list_document_sets()
    assert any(s["set_id"] == set_id for s in sets)


def test_list_sets_enriches_legacy_data_from_real_metadata_and_latest_run(_seeded_db):
    sid = database.create_document_set("legacy_user")
    doc_id = database.add_document_to_set(sid, "test_plan", "plan.pdf", 10)
    database.save_extracted_metadata(sid, doc_id, [{
        "field_name": "__structured__",
        "field_value": json.dumps({"basic_info": {"part_name": "车身控制器"}}),
        "confidence": 1,
    }])
    graph_id = "graph-legacy-list"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_finding(ReviewFinding(
        graph_id=graph_id, finding_id="finding-pending", check_id="GRAPH-COVERAGE-001",
        status="unresolved", title="证据缺失", dedupe_key="legacy:pending",
    ))
    database.update_set_status(sid, "reviewed")

    item = next(row for row in database.list_document_sets() if row["set_id"] == sid)

    assert item["title"] == "车身控制器"
    assert item["documents_count"] == 1
    assert item["latest_run_status"] == "created"
    assert item["source_status"] == "reviewed"
    assert item["status"] == "reviewing"
    assert item["finding_count"] == 1
    assert item["pending_count"] == 1
    assert item["updated_at"]


# ── Documents ──────────────────────────────────────────────────────────

def test_add_document_reduces_missing(set_id: str):
    doc_id = database.add_document_to_set(set_id, "order_form", "o.xls", 34)
    assert len(doc_id) == 16
    ds = database.get_document_set(set_id)
    assert len(ds["documents"]) == 1
    assert ds["missing_types"] == ["test_plan", "original_records", "final_report"]


def test_all_required_types_ready(set_id: str):
    for dtype in ["order_form", "test_plan", "original_records", "final_report"]:
        database.add_document_to_set(set_id, dtype, f"{dtype}.bin", 10)
    ds = database.get_document_set(set_id)
    assert ds["missing_types"] == []


@pytest.mark.anyio
async def test_raw_archive_inventory_maps_sorted_extraction_to_real_member(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "original_records", "records.zip", 1)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("z-mode-2.pdf", b"mode-2")
        archive.writestr("a-mode-1.pdf", b"mode-1")
    database.store_file_content(doc_id, buffer.getvalue())
    database.save_extracted_metadata(sid, doc_id, [{
        "field_name": "__structured__",
        "field_value": json.dumps({"metas": [
            {"filename": "a-mode-1.pdf", "test_item_name": "反向电压", "test_mode": "Mode 1", "_header_fields": {"sample_id": "S-1"}},
            {"filename": "z-mode-2.pdf", "test_item_name": "反向电压", "test_mode": "Mode 2", "_header_fields": {"sample_id": "S-1"}},
        ]}, ensure_ascii=False),
        "confidence": 1,
    }])

    response = await reviewer_client.get(f"/api/sets/{sid}/documents/{doc_id}/archive-members")

    assert response.status_code == 200
    by_mode = {item["test_mode"]: item for item in response.json()["members"]}
    assert by_mode["Mode 2"]["member_index"] == 1
    assert by_mode["Mode 1"]["member_index"] == 2
    assert by_mode["Mode 2"]["filename"] == "z-mode-2.pdf"
    assert by_mode["Mode 1"]["filename"] == "a-mode-1.pdf"


@pytest.mark.anyio
async def test_raw_archive_inventory_recovers_legacy_chinese_filename(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "original_records", "records.zip", 1)
    expected = "供电电压瞬时下降_0007_Mode 2_原始记录.pdf"
    mojibake = expected.encode("gbk").decode("cp437")
    info = zipfile.ZipInfo(mojibake)
    info.flag_bits = 0
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, b"record")
    database.store_file_content(doc_id, buffer.getvalue())

    response = await reviewer_client.get(f"/api/sets/{sid}/documents/{doc_id}/archive-members")

    assert response.status_code == 200
    member = response.json()["members"][0]
    assert member["source_filename"] == mojibake
    assert member["filename"] == expected


@pytest.mark.anyio
async def test_raw_archive_manifest_is_scoped_to_document_version(reviewer_client):
    sid = database.create_document_set("reviewer1")
    old_doc_id = database.add_document_to_set(sid, "original_records", "old.zip", 1)
    old_buffer = io.BytesIO()
    with zipfile.ZipFile(old_buffer, "w") as archive:
        archive.writestr("old-record.pdf", b"old")
    database.store_file_content(old_doc_id, old_buffer.getvalue())

    old_response = await reviewer_client.get(
        f"/api/sets/{sid}/documents/{old_doc_id}/archive-members",
    )
    assert old_response.status_code == 200

    new_doc_id = database.add_document_to_set(
        sid, "original_records", "new.zip", 1, replace_doc_id=old_doc_id,
    )
    new_buffer = io.BytesIO()
    with zipfile.ZipFile(new_buffer, "w") as archive:
        archive.writestr("new-record.pdf", b"new")
    database.store_file_content(new_doc_id, new_buffer.getvalue())
    new_response = await reviewer_client.get(
        f"/api/sets/{sid}/documents/{new_doc_id}/archive-members",
    )
    assert new_response.status_code == 200

    assert database.get_document_archive_members(old_doc_id)[0]["display_filename"] == "old-record.pdf"
    assert database.get_document_archive_members(new_doc_id)[0]["display_filename"] == "new-record.pdf"


def test_raw_archive_extraction_recovers_legacy_filename_before_structuring(_seeded_db):
    from routers.document_set import _extract_raw_records_sync

    expected = (
        "E202500000001_EQIC01 供电电压瞬时下降_0007_Mode 2_"
        "原始记录_operator_20250101010101.pdf"
    )
    mojibake = expected.encode("gbk").decode("cp437")
    info = zipfile.ZipInfo(mojibake)
    info.flag_bits = 0
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(info, _preview_pdf("raw record"))

    plain_text, structured_json = _extract_raw_records_sync(buffer.getvalue())

    assert expected in plain_text
    assert json.loads(structured_json)["metas"][0]["filename"] == expected


@pytest.mark.anyio
async def test_extraction_endpoints_return_saved_plain_text(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "test_plan", "plan.pdf", 10)
    database.update_doc_extraction_status(
        doc_id, "done", plain_text="第一行\n需要高亮的原文",
    )

    all_response = await reviewer_client.get(f"/api/sets/{sid}/extractions")
    one_response = await reviewer_client.get(
        f"/api/sets/{sid}/documents/{doc_id}/extraction",
    )

    assert all_response.status_code == 200
    assert all_response.json()["extractions"]["test_plan"]["plain_text"] == "第一行\n需要高亮的原文"
    assert one_response.status_code == 200
    assert one_response.json()["plain_text"] == "第一行\n需要高亮的原文"


@pytest.mark.anyio
async def test_raw_archive_member_preview_rejects_unsafe_zip(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "original_records", "records.zip", 1)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../outside.pdf", b"unsafe")
    database.store_file_content(doc_id, buffer.getvalue())

    response = await reviewer_client.get(
        f"/api/sets/{sid}/documents/{doc_id}/archive-members/1/file",
    )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_raw_archive_member_indices_skip_macos_metadata(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "original_records", "records.zip", 1)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("record-1.pdf", _preview_pdf("record one"))
        archive.writestr("__MACOSX/._record-1.pdf", b"Mac OS X resource fork")
        archive.writestr("record-2.pdf", _preview_pdf("record two"))
    database.store_file_content(doc_id, buffer.getvalue())

    inventory = await reviewer_client.get(
        f"/api/sets/{sid}/documents/{doc_id}/archive-members",
    )
    assert inventory.status_code == 200
    payload = inventory.json()
    assert payload["total"] == 2
    assert [item["member_index"] for item in payload["members"]] == [1, 2]
    assert all("._" not in item["filename"] for item in payload["members"])

    preview = await reviewer_client.get(
        f"/api/sets/{sid}/documents/{doc_id}/archive-members/2/file",
    )
    assert preview.status_code == 200
    assert preview.content.startswith(b"%PDF")


@pytest.mark.anyio
async def test_reviewer_can_preview_zip_evidence_after_metadata_entries(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "original_records", "records.zip", 1)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("record-1.pdf", _preview_pdf("record one"))
        archive.writestr("__MACOSX/._record-1.pdf", b"Mac OS X resource fork")
        archive.writestr("record-2.pdf", _preview_pdf("record two"))
    database.store_file_content(doc_id, buffer.getvalue())

    graph_id = "graph-preview-zip-metadata"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_evidence(EvidenceRecord(
        graph_id=graph_id, evidence_id="evidence-zip-metadata", doc_id=doc_id,
        doc_type="original_records", filename="records.zip", page_number=1,
        exact_quote="record two", extraction_method="test", confidence=1,
        metadata={"unit_id": "original_records:member-2/page-1"},
    ))

    response = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-zip-metadata/preview",
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-evidence-highlight-count"] == "1"
    assert response.content.startswith(b"\x89PNG")


def _preview_pdf(text: str) -> bytes:
    import fitz
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 120), text, fontsize=14)
    content = document.tobytes()
    document.close()
    return content


def _preview_pdf_lines(*lines: str) -> bytes:
    import fitz
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    for index, line in enumerate(lines):
        page.insert_text((72, 120 + index * 36), line, fontsize=14)
    content = document.tobytes()
    document.close()
    return content


@pytest.mark.anyio
async def test_reviewer_can_preview_highlighted_evidence_page(reviewer_client, monkeypatch):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "final_report", "report.pdf", 1)
    database.store_file_content(doc_id, _preview_pdf("Offset voltage 1.5V"))
    graph_id = "graph-preview-page"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_evidence(EvidenceRecord(
        graph_id=graph_id, evidence_id="evidence-preview", doc_id=doc_id,
        doc_type="final_report", filename="report.pdf", page_number=1,
        exact_quote="Offset voltage 1.5V", extraction_method="test", confidence=1,
    ))

    response = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-preview/preview",
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["x-preview-page-count"] == "1"
    assert response.headers["x-evidence-highlight-count"] == "1"
    assert response.headers["x-evidence-highlight-strategy"] == "exact_quote"
    assert response.headers["cache-control"] == "private, no-cache, max-age=0, must-revalidate"
    assert response.headers["x-evidence-preview-cache"] == "miss"
    assert response.headers["x-evidence-locator-enriched"] == "1"
    assert response.headers["etag"]
    assert response.content.startswith(b"\x89PNG")

    persisted = store.get_evidence(graph_id, "evidence-preview")
    assert persisted is not None
    assert len(persisted.bbox) == 4
    assert persisted.metadata["source_anchor"]["status"] == "located"

    async def fail_source_read(_doc_id):
        raise AssertionError("server render cache hit must not read the source blob")

    monkeypatch.setattr(database, "get_file_content_async", fail_source_read)
    cached = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-preview/preview",
    )
    assert cached.status_code == 200
    assert cached.headers["x-evidence-preview-cache"] == "hit"
    assert cached.content == response.content

    not_modified = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-preview/preview",
        headers={"If-None-Match": response.headers["etag"]},
    )
    assert not_modified.status_code == 304
    assert not_modified.content == b""


@pytest.mark.anyio
async def test_preview_rejects_one_quote_spread_across_distant_regions(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "final_report", "report.pdf", 1)
    database.store_file_content(
        doc_id,
        _preview_pdf_lines("Issued Date:", "Company footer", "Address footer"),
    )
    graph_id = "graph-preview-disconnected"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_evidence(EvidenceRecord(
        graph_id=graph_id, evidence_id="evidence-disconnected", doc_id=doc_id,
        doc_type="final_report", filename="report.pdf", page_number=1,
        exact_quote="Issued Date: Company footer Address footer",
        extraction_method="test", confidence=1,
        metadata={"source_anchor": {
            "status": "text_anchored", "cardinality": "single_region",
        }},
    ))

    response = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-disconnected/preview",
    )

    assert response.status_code == 200
    assert response.headers["x-evidence-highlight-count"] == "0"
    assert response.headers["x-evidence-highlight-strategy"] == "none"


@pytest.mark.anyio
async def test_preview_recovers_historical_missing_field_as_one_region(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "final_report", "report.pdf", 1)
    database.store_file_content(
        doc_id,
        _preview_pdf_lines("Issued Date:", "Company footer", "Address footer"),
    )
    graph_id = "graph-preview-historical-missing"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_evidence(EvidenceRecord(
        graph_id=graph_id, evidence_id="evidence-historical-missing", doc_id=doc_id,
        doc_type="final_report", filename="report.pdf", page_number=1,
        exact_quote="Issued Date: Company footer Address footer",
        extraction_method="deterministic_missing_field_region", confidence=1,
        metadata={"field_name": "issue_date"},
    ))

    response = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-historical-missing/preview",
    )

    assert response.status_code == 200
    assert response.headers["x-evidence-highlight-count"] == "1"
    assert response.headers["x-evidence-highlight-strategy"] == "structured_missing_field"
    persisted = store.get_evidence(graph_id, "evidence-historical-missing")
    assert persisted is not None
    assert len(persisted.bbox) == 4
    assert len(persisted.metadata["source_anchor"]["rectangles"]) == 1


@pytest.mark.anyio
async def test_preview_reuses_verified_coordinates_when_pdf_bytes_change(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "final_report", "report.pdf", 1)
    pdf_bytes = _preview_pdf_lines("Test Plan No.: xxxxxx", "Customer: Example")
    database.store_file_content(doc_id, pdf_bytes)
    with fitz.open(stream=pdf_bytes, filetype="pdf") as document:
        page = document[0]
        label = page.search_for("Test Plan No.:")[0]
        placeholder = page.search_for("xxxxxx")[0]
        region = fitz.Rect(label)
        region.include_rect(placeholder)
        coordinates = [region.x0, region.y0, region.x1, region.y1]
        page_width, page_height = page.rect.width, page.rect.height

    graph_id = "graph-preview-stable-geometry"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_evidence(EvidenceRecord(
        graph_id=graph_id, evidence_id="evidence-stable-geometry", doc_id=doc_id,
        doc_type="final_report", filename="report.pdf", page_number=1,
        exact_quote="Test Plan No.:", bbox=coordinates,
        extraction_method="deterministic_missing_field_region", confidence=1,
        metadata={"field_name": "test_plan_number", "role": "missing_field", "source_anchor": {
            "version": 2, "status": "located", "kind": "missing_field",
            "cardinality": "single_region", "coordinate_space": "pdf_points",
            "anchor_quote": "Test Plan No.:", "rectangles": [coordinates],
            "page_width": page_width, "page_height": page_height,
            "rendered_pdf_hash": "different-docx-conversion-hash",
        }},
    ))

    response = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-stable-geometry/preview",
    )

    assert response.status_code == 200
    assert response.headers["x-evidence-highlight-count"] == "1"
    assert response.headers["x-evidence-highlight-strategy"] == "source_anchor_bbox"


@pytest.mark.anyio
async def test_preview_does_not_locate_short_value_inside_longer_value(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "final_report", "report.pdf", 1)
    database.store_file_content(
        doc_id,
        _preview_pdf_lines("Reversed voltage 4V", "Reversed voltage 14V"),
    )
    graph_id = "graph-preview-atomic-parameter"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_evidence(EvidenceRecord(
        graph_id=graph_id, evidence_id="evidence-atomic", doc_id=doc_id,
        doc_type="final_report", filename="report.pdf", page_number=1,
        exact_quote="4V", extraction_method="test", confidence=1,
        metadata={"parameter_name": "Reversed voltage"},
    ))

    response = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-atomic/preview",
    )

    assert response.status_code == 200
    assert response.headers["x-evidence-highlight-count"] == "1"
    assert response.headers["x-evidence-highlight-strategy"] == "structured_parameter_value"


def test_preview_anchor_signature_tracks_corrections_but_not_lazy_enrichment():
    original = SimpleNamespace(exact_quote="4V", bbox=[], metadata={"parameter_name": "Voltage"})
    corrected_quote = SimpleNamespace(
        exact_quote="14V", bbox=[], metadata={"parameter_name": "Voltage"},
    )
    corrected_bbox = SimpleNamespace(
        exact_quote="4V", bbox=[10, 20, 30, 40],
        metadata={"parameter_name": "Voltage", "source_anchor": {
            "method": "manual_layout_anchor", "status": "located",
        }},
    )
    lazy = SimpleNamespace(
        exact_quote="4V", bbox=[10, 20, 30, 40], metadata={
            "parameter_name": "Voltage", "source_anchor": {
                "method": "preview_exact_quote", "status": "located",
                "rectangles": [[10, 20, 30, 40]],
            },
        },
    )

    original_signature = _evidence_anchor_signature(original)
    assert _evidence_anchor_signature(corrected_quote) != original_signature
    assert _evidence_anchor_signature(corrected_bbox) != original_signature
    assert _evidence_anchor_signature(lazy) == original_signature


@pytest.mark.anyio
async def test_reviewer_disambiguates_repeated_short_field_value(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "order_form", "order.pdf", 1)
    database.store_file_content(
        doc_id,
        _preview_pdf_lines("Sample name   AB", "Status AB normal"),
    )
    graph_id = "graph-preview-short-field"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_evidence(EvidenceRecord(
        graph_id=graph_id, evidence_id="evidence-short-field", doc_id=doc_id,
        doc_type="order_form", filename="order.pdf", page_number=1,
        exact_quote="AB", extraction_method="test", confidence=1,
        metadata={"field_name": "sample_name"},
    ))

    response = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-short-field/preview",
    )

    assert response.status_code == 200
    assert response.headers["x-evidence-highlight-count"] == "1"
    assert response.headers["x-evidence-highlight-strategy"] == "structured_field_row"
    assert response.headers["x-evidence-locator-enriched"] == "1"
    persisted = store.get_evidence(graph_id, "evidence-short-field")
    assert persisted is not None
    assert len(persisted.bbox) == 4
    assert persisted.metadata["source_anchor"]["method"] == "preview_structured_field_row"


@pytest.mark.anyio
async def test_reviewer_can_combine_semantic_highlights_on_one_page(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "final_report", "report.pdf", 1)
    database.store_file_content(doc_id, _preview_pdf_lines(
        "Required status C", "Reported status A",
    ))
    graph_id = "graph-preview-multi"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    for evidence_id, quote in (
        ("evidence-required", "Required status C"),
        ("evidence-error", "Reported status A"),
    ):
        store.add_evidence(EvidenceRecord(
            graph_id=graph_id, evidence_id=evidence_id, doc_id=doc_id,
            doc_type="final_report", filename="report.pdf", page_number=1,
            exact_quote=quote, extraction_method="test", confidence=1,
        ))

    response = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-required/preview",
        params={
            "related_evidence_ids": "evidence-error",
            "highlight_kinds": "reference,error",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-evidence-record-count"] == "2"
    assert response.headers["x-evidence-highlight-count"] == "2"
    assert response.headers["x-evidence-highlight-strategy"].startswith("multi:")
    anchor_positions = [float(item) for item in response.headers["x-evidence-anchor-y"].split(",")]
    assert len(anchor_positions) == 2
    assert all(0 <= position <= 1 for position in anchor_positions)
    assert response.content.startswith(b"\x89PNG")


@pytest.mark.anyio
async def test_reviewer_can_focus_preview_on_one_highlight(reviewer_client):
    import fitz

    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "final_report", "report.pdf", 1)
    database.store_file_content(doc_id, _preview_pdf_lines(
        "Required status C", "Reported status A",
    ))
    graph_id = "graph-preview-focus"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    for evidence_id, quote in (
        ("evidence-required", "Required status C"),
        ("evidence-error", "Reported status A"),
    ):
        store.add_evidence(EvidenceRecord(
            graph_id=graph_id, evidence_id=evidence_id, doc_id=doc_id,
            doc_type="final_report", filename="report.pdf", page_number=1,
            exact_quote=quote, extraction_method="test", confidence=1,
        ))
    url = f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-required/preview"
    params = {
        "related_evidence_ids": "evidence-error",
        "highlight_kinds": "reference,error",
    }

    full = await reviewer_client.get(url, params=params)
    focused = await reviewer_client.get(url, params={**params, "focus_index": 1})

    assert full.status_code == focused.status_code == 200
    assert full.headers["x-evidence-focus-applied"] == "0"
    assert focused.headers["x-evidence-focus-applied"] == "1"
    full_pixmap = fitz.Pixmap(full.content)
    focused_pixmap = fitz.Pixmap(focused.content)
    assert focused_pixmap.width == full_pixmap.width
    assert focused_pixmap.height < full_pixmap.height * 0.6


@pytest.mark.anyio
async def test_reviewer_can_preview_page_inside_original_record_archive(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "original_records", "records.zip", 1)
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("record.pdf", _preview_pdf("Original record result PASS"))
    database.store_file_content(doc_id, archive_bytes.getvalue())
    graph_id = "graph-preview-archive"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_evidence(EvidenceRecord(
        graph_id=graph_id, evidence_id="evidence-archive", doc_id=doc_id,
        doc_type="original_records", filename="records.zip", page_number=1,
        exact_quote="Original record result PASS", extraction_method="test", confidence=1,
        metadata={"unit_id": "original_records:member-1/page-1"},
    ))

    response = await reviewer_client.get(
        f"/api/sets/{sid}/review-runs/{graph_id}/evidence/evidence-archive/preview",
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-evidence-highlight-count"] == "1"


def test_structured_preview_highlights_the_matching_comparison_row():
    import fitz
    from services.document_preview import _highlight_rectangles

    document = fitz.open()
    page = document.new_page(width=595, height=300)
    columns = [50, 135, 220, 345, 390, 445, 510]
    for y, required, verdict in [(105, "C", "Pass"), (175, "A", "Fail")]:
        values = ["Reverse voltage", "power supply", "14V", "60s", required, "C1", verdict]
        for x, value in zip(columns, values):
            page.insert_text((x, y), value, fontsize=9)

    rectangles, strategy = _highlight_rectangles(page, "long extraction", [], {
        "comparison_rows": [{
            "test_item": "Reverse voltage",
            "injection_point": "power supply",
            "spec_requirement": "14V",
            "test_duration": "60s",
            "required_level": "A",
            "actual_level": "C1",
            "verdict_raw": "Fail",
        }],
    })

    assert strategy == "structured_comparison_row"
    assert len(rectangles) == 1
    assert 150 < rectangles[0].y0 < 180
    assert rectangles[0].y1 < 190
    document.close()


def test_preview_highlight_uses_business_summary_instead_of_box_number():
    from services.document_preview import _highlight_summary_label

    assert _highlight_summary_label({
        "kind": "error", "metadata": {"role": "report_only"},
    }) == "报告额外"
    assert _highlight_summary_label({
        "kind": "success", "metadata": {"role": "matched_raw"},
    }) == "已匹配"
    assert _highlight_summary_label({
        "kind": "error", "metadata": {"verdict": "fail"},
    }) == "不通过"
    assert _highlight_summary_label({"kind": "reference", "metadata": {}}) == "要求依据"


def test_preview_label_position_avoids_source_words_when_space_exists():
    import fitz
    from services.document_preview import _highlight_label_rectangle

    document = fitz.open()
    page = document.new_page(width=300, height=180)
    page.insert_text((60, 94), "instrument serial number", fontsize=9)
    anchor = fitz.Rect(50, 80, 250, 108)

    label = _highlight_label_rectangle(page, anchor, 36, 10, [])
    words = [fitz.Rect(*word[:4]) for word in page.get_text("words")]

    assert not any(label.intersects(word) for word in words)
    document.close()


def test_long_unstructured_quote_does_not_guess_a_highlight_from_boilerplate():
    import fitz
    from services.document_preview import _highlight_rectangles

    document = fitz.open()
    page = document.new_page(width=595, height=300)
    page.insert_text((50, 105), "Electrical performance room 407", fontsize=9)
    quote = "Electrical performance room 407\n" + ("unstructured extracted content " * 20)

    rectangles, strategy = _highlight_rectangles(page, quote, [], {})

    assert rectangles == []
    assert strategy == "none"
    document.close()


def test_preview_normalizes_pdf_text_layer_spacing_for_full_quote():
    import fitz
    from services.document_preview import _highlight_rectangles

    document = fitz.open()
    page = document.new_page(width=595, height=300)
    page.insert_text(
        (50, 105),
        "severity 1 peak to peak voltage UPP of 1 V for UN 12 V",
        fontsize=9,
    )

    rectangles, strategy = _highlight_rectangles(
        page,
        "severity 1: peak to peak voltage, U PP, of 1 V, for UN = 12 V",
        [],
        {},
    )

    assert strategy == "normalized_quote"
    assert len(rectangles) == 1
    assert rectangles[0].x0 < rectangles[0].x1
    document.close()


@pytest.mark.anyio
async def test_reviewer_can_mark_finding_not_applicable_with_audited_reason(reviewer_client):
    sid = database.create_document_set("reviewer1")
    graph_id = "graph-reviewer-not-applicable"
    store = EvidenceGraphStore(database.DB_PATH)
    store.init_schema()
    store.create_run(GraphRun(graph_id=graph_id, scope=GraphScope.RUN, set_id=sid))
    store.add_finding(ReviewFinding(
        graph_id=graph_id, finding_id="finding-na", check_id="GRAPH-COVERAGE-001",
        status="unresolved", title="范围待确认", dedupe_key="na:test",
    ))

    response = await reviewer_client.put(
        f"/api/sets/{sid}/review-runs/{graph_id}/findings/finding-na/decision",
        json={
            "decision": "dismissed", "resolution_code": "not_applicable",
            "resolution_status": "not_applicable", "comment": "委托范围不包含该项目",
        },
    )

    assert response.status_code == 200
    assert response.json()["resolution_code"] == "not_applicable"
    saved = store.get_snapshot(graph_id).decisions[0]
    assert saved.actor_id == "reviewer1"
    assert saved.comment == "委托范围不包含该项目"


# ── Versioning ─────────────────────────────────────────────────────────

def test_create_new_version(set_id: str):
    root_id = database.add_document_to_set(set_id, "order_form", "v1.xls", 10)
    v2_id = database.add_document_to_set(
        set_id, "order_form", "v2.xls", 12, replace_doc_id=root_id,
    )
    assert v2_id != root_id

    versions = database.get_doc_versions(set_id, "order_form")
    assert len(versions) == 2
    assert versions[0]["doc_version"] == 1
    assert versions[1]["doc_version"] == 2
    assert versions[1]["parent_doc_id"] == root_id


def test_locked_set_rejects_new_document_and_replacement(set_id: str):
    root_id = database.add_document_to_set(set_id, "order_form", "v1.xls", 10)
    assert database.lock_document_set(set_id) is True

    with pytest.raises(ValueError, match="已锁定"):
        database.add_document_to_set(set_id, "test_plan", "plan.pdf", 10)
    with pytest.raises(ValueError, match="已锁定"):
        database.add_document_to_set(
            set_id, "order_form", "v2.xls", 12, replace_doc_id=root_id,
        )


@pytest.mark.anyio
async def test_locked_set_rejects_source_mutation_routes(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "test_plan", "plan.pdf", 10)
    database.save_extracted_metadata(sid, doc_id, [{
        "field_name": "name", "field_value": "原值", "confidence": 1,
    }])
    assert database.lock_document_set(sid) is True

    override = await reviewer_client.patch(
        f"/api/sets/{sid}/documents/{doc_id}/overrides",
        json={"overrides": {"name": "修改值"}},
    )
    retry = await reviewer_client.post(
        f"/api/sets/{sid}/documents/{doc_id}/retry-extraction",
    )
    cancel = await reviewer_client.post(
        f"/api/sets/{sid}/documents/{doc_id}/cancel-extraction",
    )
    delete = await reviewer_client.delete(f"/api/sets/{sid}/documents/{doc_id}")

    assert {override.status_code, retry.status_code, cancel.status_code, delete.status_code} == {409}
    assert database.get_human_overrides(doc_id) == {}
    assert database.document_belongs_to_set(sid, doc_id) is True


@pytest.mark.anyio
async def test_lock_requires_persisted_review_of_each_latest_document(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_ids = {}
    for doc_type in ("order_form", "test_plan", "original_records", "final_report"):
        doc_id = database.add_document_to_set(sid, doc_type, f"{doc_type}.pdf", 1)
        database.update_doc_extraction_status(doc_id, "done", plain_text="已提取")
        doc_ids[doc_type] = doc_id

    before = await reviewer_client.post(f"/api/sets/{sid}/lock")
    assert before.status_code == 409
    assert "尚未完成核查" in before.json()["detail"]

    for doc_id in doc_ids.values():
        confirmed = await reviewer_client.post(
            f"/api/sets/{sid}/documents/{doc_id}/review-confirmation",
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["reviewed_by"] == "reviewer1"

    overview = (await reviewer_client.get(f"/api/sets/{sid}")).json()
    assert all(item["reviewed_at"] for item in overview["files"] if item["required"])
    locked = await reviewer_client.post(f"/api/sets/{sid}/lock")
    assert locked.status_code == 200


@pytest.mark.anyio
async def test_auto_quality_gate_skips_full_extraction_review_when_complete(reviewer_client):
    sid = database.create_document_set("reviewer1")
    for doc_type in ("order_form", "test_plan", "original_records", "final_report"):
        doc_id = database.add_document_to_set(sid, doc_type, f"{doc_type}.pdf", 1)
        database.update_doc_extraction_status(
            doc_id, "done", plain_text="已提取", extraction_quality="complete",
        )

    locked = await reviewer_client.post(
        f"/api/sets/{sid}/lock", json={"auto_gate": True},
    )

    assert locked.status_code == 200
    assert locked.json()["extraction_review_skipped"] is True
    assert locked.json()["quality_gate"]["status"] == "pass"


@pytest.mark.anyio
async def test_auto_quality_gate_blocks_partial_source_snapshot(reviewer_client):
    sid = database.create_document_set("reviewer1")
    for doc_type in ("order_form", "test_plan", "original_records", "final_report"):
        doc_id = database.add_document_to_set(sid, doc_type, f"{doc_type}.pdf", 1)
        status = "partial" if doc_type == "final_report" else "done"
        quality = "partial" if doc_type == "final_report" else "complete"
        database.update_doc_extraction_status(
            doc_id, status, plain_text="已提取", extraction_quality=quality,
        )

    blocked = await reviewer_client.post(
        f"/api/sets/{sid}/lock", json={"auto_gate": True},
    )

    assert blocked.status_code == 409
    payload = blocked.json()["detail"]
    assert payload["quality_gate"]["status"] == "block"
    assert any(item["doc_type"] == "final_report" for item in payload["quality_gate"]["blockers"])


@pytest.mark.anyio
async def test_exception_only_lock_requires_only_gate_blocking_document(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_ids = {}
    for doc_type in ("order_form", "test_plan", "original_records", "final_report"):
        doc_id = database.add_document_to_set(sid, doc_type, f"{doc_type}.pdf", 1)
        status = "partial" if doc_type == "final_report" else "done"
        quality = "partial" if doc_type == "final_report" else "complete"
        database.update_doc_extraction_status(
            doc_id, status, plain_text="已提取", extraction_quality=quality,
        )
        doc_ids[doc_type] = doc_id

    confirmed = await reviewer_client.post(
        f"/api/sets/{sid}/documents/{doc_ids['final_report']}/review-confirmation",
    )
    assert confirmed.status_code == 200
    locked = await reviewer_client.post(
        f"/api/sets/{sid}/lock", json={"exception_only": True},
    )

    assert locked.status_code == 200
    assert locked.json()["extraction_review_mode"] == "exception_only"


@pytest.mark.anyio
async def test_editing_reviewed_extraction_invalidates_confirmation(reviewer_client):
    sid = database.create_document_set("reviewer1")
    doc_id = database.add_document_to_set(sid, "test_plan", "plan.pdf", 1)
    database.update_doc_extraction_status(doc_id, "done", plain_text="已提取")
    database.save_extracted_metadata(sid, doc_id, [{
        "field_name": "name", "field_value": "原值", "confidence": 1,
    }])
    assert (await reviewer_client.post(
        f"/api/sets/{sid}/documents/{doc_id}/review-confirmation",
    )).status_code == 200

    edited = await reviewer_client.patch(
        f"/api/sets/{sid}/documents/{doc_id}/overrides",
        json={"overrides": {"name": "修改值"}},
    )

    assert edited.status_code == 200
    current = database.get_document_set(sid)
    assert current["documents"][0]["reviewed_at"] == ""


def test_no_versions_for_missing_type(set_id: str):
    assert database.get_doc_versions(set_id, "order_form") == []


# ── Locking ────────────────────────────────────────────────────────────

def test_lock_set(set_id: str):
    assert database.lock_document_set(set_id) is True
    assert database.get_document_set(set_id)["status"] == "locked"


def test_lock_already_locked(_seeded_db):
    sid = database.create_document_set("ltest")
    database.lock_document_set(sid)
    assert database.lock_document_set(sid) is False


# ── Status ─────────────────────────────────────────────────────────────

def test_status_transitions(set_id: str):
    for s in ["incomplete", "revision", "locked", "reviewing", "reviewed"]:
        assert database.update_set_status(set_id, s) is True
        assert database.get_document_set(set_id)["status"] == s


def test_status_invalid(set_id: str):
    assert database.update_set_status(set_id, "bogus") is False


# ── Metadata + Diff ────────────────────────────────────────────────────

def test_metadata_and_diff(set_id: str):
    root = database.add_document_to_set(set_id, "order_form", "v1.xls", 10)
    database.save_extracted_metadata(set_id, root, [
        {"field_name": "零件号", "field_value": "OLD", "source_text": "", "confidence": 1.0},
        {"field_name": "产品名", "field_value": "旧", "source_text": "", "confidence": 1.0},
    ])
    v2 = database.add_document_to_set(
        set_id, "order_form", "v2.xls", 12, replace_doc_id=root,
    )
    database.save_extracted_metadata(set_id, v2, [
        {"field_name": "零件号", "field_value": "NEW", "source_text": "", "confidence": 1.0},
        {"field_name": "产品名", "field_value": "旧", "source_text": "", "confidence": 1.0},
    ])

    diffs = database.diff_doc_versions(root, v2)
    changed = [d for d in diffs if d["changed"]]
    assert len(changed) == 1
    assert changed[0]["field_name"] == "零件号"
    assert changed[0]["old_value"] == "OLD"


def test_metadata_retry_replaces_rows_and_preserves_human_override(set_id: str):
    doc_id = database.add_document_to_set(set_id, "test_plan", "plan.xlsx", 10)
    database.save_extracted_metadata(set_id, doc_id, [
        {"field_name": "__structured__", "field_value": "old", "confidence": 0.8},
        {"field_name": "项目名称", "field_value": "旧名称", "confidence": 0.8},
    ])
    database.save_human_overrides(doc_id, {"项目名称": "人工名称"})

    database.save_extracted_metadata(set_id, doc_id, [
        {"field_name": "__structured__", "field_value": "new", "confidence": 0.9},
        {"field_name": "项目名称", "field_value": "新名称", "confidence": 0.9},
    ])

    rows = database.get_extracted_metadata(doc_id)
    assert len(rows) == 2
    structured = next(row for row in rows if row["field_name"] == "__structured__")
    name = next(row for row in rows if row["field_name"] == "项目名称")
    assert structured["field_value"] == "new"
    assert name["field_value"] == "新名称"
    assert name["human_override_value"] == "人工名称"


# ── Service integration ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_service_create_and_get(_seeded_db):
    ds = await DocumentSetService.create_set("svc")
    assert ds.set_id.startswith("EMC-")
    overview = await DocumentSetService.get_set_overview(ds.set_id)
    assert overview is not None
    assert overview["is_ready"] is False


@pytest.mark.asyncio
async def test_service_readiness(_seeded_db):
    ds = await DocumentSetService.create_set("rdy")
    sid = ds.set_id
    for dt in ["order_form", "test_plan", "original_records", "final_report"]:
        await DocumentSetService.add_document(sid, dt, f"{dt}.bin", 10)
    # Mark all docs as extracted — service layer now requires status=="done"
    import database
    for d in (await database.get_document_set_async(sid))["documents"]:
        await database.update_doc_extraction_status_async(d["doc_id"], "done", "")
    overview = await DocumentSetService.get_set_overview(sid)
    assert overview["is_ready"] is True
    assert overview["missing_types"] == []


@pytest.mark.asyncio
async def test_service_readiness_allows_partial_extraction(_seeded_db):
    ds = await DocumentSetService.create_set("partial")
    sid = ds.set_id
    for dt in ["order_form", "test_plan", "original_records", "final_report"]:
        await DocumentSetService.add_document(sid, dt, f"{dt}.bin", 10)

    docs = (await database.get_document_set_async(sid))["documents"]
    for d in docs:
        status = "partial" if d["doc_type"] == "final_report" else "done"
        await database.update_doc_extraction_status_async(
            d["doc_id"], status, "", extraction_quality=status if status == "partial" else "complete",
            extraction_meta={"quality": status},
        )

    overview = await DocumentSetService.get_set_overview(sid)
    assert overview["is_ready"] is True
    final_doc = next(f for f in overview["files"] if f["doc_type"] == "final_report")
    assert final_doc["extraction_status"] == "partial"
    assert final_doc["extraction_quality"] == "partial"


@pytest.mark.asyncio
async def test_service_create_revision_from_reviewed(_seeded_db):
    ds = await DocumentSetService.create_set("rev")
    sid = ds.set_id
    assert await database.update_set_status_async(sid, "reviewed") is True

    assert await DocumentSetService.create_revision(sid) is True
    overview = await DocumentSetService.get_set_overview(sid)
    assert overview["status"] == "revision"


def test_lock_revision_set(_seeded_db):
    sid = database.create_document_set("revlock")
    assert database.update_set_status(sid, "revision") is True
    assert database.lock_document_set(sid) is True
    assert database.get_document_set(sid)["status"] == "locked"


@pytest.mark.asyncio
async def test_failed_graph_run_returns_set_to_retryable_locked_state(
    _seeded_db, monkeypatch,
):
    from routers.document_set import _run_unified_review_background
    import services.unified_review_pipeline as review_pipeline

    sid = database.create_document_set("retryable")
    assert database.update_set_status(sid, "reviewing") is True

    async def fail_review(**_kwargs):
        raise RuntimeError("synthetic review failure")

    monkeypatch.setattr(review_pipeline, "run_unified_review", fail_review)
    with pytest.raises(RuntimeError, match="synthetic review failure"):
        await _run_unified_review_background(
            set_id=sid, graph_id="graph-failed", gateway=object(), store=object(),
        )
    assert database.get_document_set(sid)["status"] == "locked"
