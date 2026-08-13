import uuid

import pytest

import database
import services.standard_graph as standard_graph
from services.standard_graph import (build_extraction_units, build_normative_fallback,
                                     has_normative_cues, split_failed_extraction_unit,
                                     validate_graph_chunk)


def _ready_standard():
    suffix = uuid.uuid4().hex[:8]
    std_id, _ = database.create_standard_asset(
        f"graph-standard-{suffix}".encode(), f"graph-standard-{suffix}.pdf",
        code=f"ISO TEST {suffix}", version="2026",
    )
    database.update_standard_knowledge(std_id, status="ready", page_count=1)
    database.replace_standard_knowledge_chunks(std_id, [{
        "id": f"chunk-{suffix}", "page_start": 12, "page_end": 12,
        "clause": "5.1", "title": "Test pulse",
        "content": "[第 12 页]\n5.1 Test pulse\nThe pulse amplitude shall be 12 V.",
    }])
    return std_id, f"chunk-{suffix}"


def test_graph_validation_requires_verbatim_evidence():
    chunk = {
        "id": "chunk-1", "page_start": 12, "page_end": 12,
        "content": "The pulse amplitude shall be 12 V.",
    }
    accepted, rejected = validate_graph_chunk(chunk, {"clauses": [{
        "clause_number": "5.1", "title": "Test pulse", "page_start": 12, "page_end": 12,
        "requirements": [
            {"requirement_type": "parameter_limit", "statement": "脉冲幅值为 12 V",
             "evidence_quote": "The pulse amplitude shall be 12 V.", "page_start": 12,
             "parameters": [{"name": "pulse amplitude", "value": "12", "unit": "V"}]},
            {"requirement_type": "acceptance", "statement": "产品必须正常工作",
             "evidence_quote": "The product shall operate normally.", "page_start": 12},
        ],
    }]})

    assert len(accepted) == 1
    assert len(accepted[0]["requirements"]) == 1
    assert rejected[0]["reasons"] == ["original_statement_not_found", "evidence_not_found"]
    corrected, _ = validate_graph_chunk(chunk, {"clauses": [{
        "clause_number": "5.1", "requirements": [{"statement": "脉冲幅值为 12 V",
        "evidence_quote": "The pulse amplitude shall be 12 V.", "page_start": 99}],
    }]})
    assert corrected[0]["requirements"][0]["page_start"] == 12


def test_graph_validation_rejects_english_in_chinese_interpretation():
    chunk = {
        "id": "chunk-en", "page_start": 1, "page_end": 1,
        "content": "The voltage shall be 12 V.",
    }
    accepted, rejected = validate_graph_chunk(chunk, {"clauses": [{
        "requirements": [{
            "original_statement": "The voltage shall be 12 V.",
            "interpretation_zh": "The voltage shall be 12 V.",
            "evidence_quote": "The voltage shall be 12 V.",
            "page_start": 1,
        }],
    }]})
    assert accepted == []
    assert "interpretation_not_chinese" in rejected[0]["reasons"]


def test_extraction_units_are_page_bounded():
    units = build_extraction_units([{
        "id": "source-1", "page_start": 4, "page_end": 5,
        "content": "[第 4 页 | ocr]\n4 Scope\nRequirement A.\n\nMore.\n\n[第 5 页 | ocr]\n5 Test\nRequirement B.",
    }])
    assert [(unit["page_start"], unit["page_end"]) for unit in units] == [(4, 4), (5, 5)]
    assert all(unit["id"] == "source-1" for unit in units)
    assert has_normative_cues("The ambient temperature shall be 23 C.")
    assert not has_normative_cues("Contents and copyright information.")
    fallback = build_normative_fallback({"id": "c1", "page_start": 8,
                                         "content": "The voltage shall be 12 V. Informative note."})
    assert fallback[0]["requirements"][0]["confidence"] == .1
    assert fallback[0]["requirements"][0]["evidence_quote"] == "The voltage shall be 12 V."
    assert fallback[0]["requirements"][0]["interpretation_zh"] == ""
    long_units = build_extraction_units([{
        "id": "source-2", "page_start": 8, "page_end": 8,
        "content": "[第 8 页 | ocr]\n" + ("A" * 2600),
    }])
    assert len(long_units) == 3
    assert max(len(unit["content"]) for unit in long_units) <= 1200

    recovery = split_failed_extraction_unit({
        "id": "source-3", "unit_id": "source-3:p8:w1", "page_start": 8, "page_end": 8,
        "content": "\n".join(["Table heading", "A" * 350, "B" * 350]),
    })
    assert len(recovery) == 2
    assert all(unit["page_start"] == 8 for unit in recovery)
    assert recovery[0]["unit_id"].endswith(":recovery1")


def test_manual_recovery_fallback_preserves_non_normative_table_window():
    fallback = standard_graph.build_manual_recovery_fallback({
        "id": "table-chunk", "unit_id": "table:p14:w1:recovery1",
        "page_start": 14, "page_end": 14,
        "content": "Parameter | Level I | Level II\nUS6 | 8 V | 4,5 V",
    })

    requirement = fallback[0]["requirements"][0]
    assert requirement["evidence_quote"] == (
        "Parameter | Level I | Level II\nUS6 | 8 V | 4,5 V"
    )
    assert requirement["interpretation_zh"] == ""
    assert requirement["confidence"] == 0.0


def test_recovery_evidence_coverage_detects_partial_table_extraction():
    content = "Parameter Level I Level II US6 8 V 4,5 V Duration 1000 ms"
    clauses = [{"requirements": [{"evidence_quote": "US6 8 V"}]}]

    coverage = standard_graph.recovery_evidence_coverage(content, clauses)

    assert 0 < coverage < standard_graph.STANDARD_RECOVERY_MIN_EVIDENCE_COVERAGE


def test_parameter_table_with_sparse_evidence_requires_manual_fallback():
    content = (
        "Table 3 — Starting profile values\n"
        "Level I II III IV\nUS6 8 4,5 3 6\nUS 9,5 6,5 5 6,5"
    )
    needs_fallback, coverage = standard_graph.needs_manual_table_coverage_fallback(
        content, [{"requirements": [{"evidence_quote": "US6 8"}]}],
    )

    assert needs_fallback is True
    assert coverage < standard_graph.STANDARD_RECOVERY_MIN_EVIDENCE_COVERAGE


@pytest.mark.asyncio
async def test_standard_graph_extraction_disables_thinking_and_bounds_retries():
    class Reviewer:
        kwargs = None

        async def _call_api(self, **kwargs):
            self.kwargs = kwargs
            return {"clauses": []}

    reviewer = Reviewer()
    await standard_graph._extract_chunk(reviewer, {
        "id": "chunk", "unit_id": "chunk:p1:w1", "page_start": 1,
        "page_end": 1, "content": "Informative background only.",
    })

    assert reviewer.kwargs["thinking"] is False
    assert reviewer.kwargs["max_retries"] == 1
    assert reviewer.kwargs["task_kind"] == "knowledge_reasoning"


@pytest.mark.asyncio
async def test_failed_standard_unit_checkpoints_manual_fallback(_seeded_db, monkeypatch):
    std_id, chunk_id = _ready_standard()
    unit_id = f"{chunk_id}:p12:w1"
    database.update_standard_graph_status(std_id, "pending_review", "", {
        "extraction_unit_count": 1,
        "failed_unit_ids": [unit_id],
    })
    recovery_units = [
        {"id": chunk_id, "unit_id": f"{unit_id}:recovery1", "page_start": 12,
         "page_end": 12, "content": "The pulse amplitude shall be 12 V."},
        {"id": chunk_id, "unit_id": f"{unit_id}:recovery2", "page_start": 12,
         "page_end": 12, "content": "The duration shall be 10 ms."},
    ]
    monkeypatch.setattr(
        standard_graph, "split_failed_extraction_unit", lambda _unit: recovery_units,
    )

    async def fake_extract(_reviewer, unit, **_kwargs):
        if unit["unit_id"].endswith("recovery2"):
            raise ValueError("transient empty response")
        return {
            "unit_id": unit["unit_id"], "rejected": [], "clauses": [{
                "clause_number": "5.1", "title": "Test pulse",
                "source_chunk_id": chunk_id, "page_start": 12, "page_end": 12,
                "requirements": [{
                    "requirement_type": "parameter_limit", "test_item": "Test pulse",
                    "interpretation_zh": "脉冲幅值应为 12 V。",
                    "original_statement": "The pulse amplitude shall be 12 V.",
                    "evidence_quote": "The pulse amplitude shall be 12 V.",
                    "page_start": 12, "page_end": 12, "confidence": .9,
                    "parameters": [], "relations": [],
                }],
            }],
        }

    monkeypatch.setattr(standard_graph, "_extract_chunk", fake_extract)
    result = await standard_graph.retry_failed_standard_graph_units(std_id)
    graph = database.get_standard_graph(std_id)
    saved = database.get_standard(std_id)

    assert result["requirement_count"] == 2
    assert result["remaining_failed_unit_ids"] == []
    assert len(graph["requirements"]) == 2
    assert saved.graph_status == "pending_review"
    assert saved.graph_meta["failed_unit_retry_count"] == 1
    assert saved.graph_meta["manual_fallback_unit_ids"] == [
        f"{unit_id}:recovery2",
    ]


def test_requirement_review_keeps_revision_and_publish_gate(_seeded_db):
    std_id, chunk_id = _ready_standard()
    counts = database.replace_standard_graph(std_id, [{
        "clause_number": "5.1", "title": "Test pulse", "page_start": 12, "page_end": 12,
        "source_chunk_id": chunk_id, "requirements": [{
            "requirement_type": "parameter_limit", "test_item": "Test pulse",
            "statement": "Pulse is 12 V", "evidence_quote": "The pulse amplitude shall be 12 V.",
            "page_start": 12, "page_end": 12, "confidence": .9,
            "parameters": [{"name": "amplitude", "value": "12", "unit": "V"}],
        }],
    }], {"failed_chunk_ids": []})
    assert counts == {"clause_count": 1, "requirement_count": 1}
    requirement = database.get_standard_graph(std_id)["requirements"][0]

    with pytest.raises(ValueError, match="待确认"):
        database.publish_standard_graph(std_id, "reviewer1")

    with pytest.raises(ValueError, match="必须包含中文"):
        database.review_standard_requirement(
            std_id, requirement["id"], "confirmed", "", "reviewer1",
            {"interpretation_zh": "The pulse amplitude shall be 12 V."},
        )

    reviewed = database.review_standard_requirement(
        std_id, requirement["id"], "confirmed", "按原文修正表述", "reviewer1",
        {"statement": "脉冲幅值应为 12 V", "parameters": requirement["parameters"]},
    )
    assert reviewed["statement"] == "脉冲幅值应为 12 V"
    assert reviewed["review_status"] == "confirmed"
    published = database.publish_standard_graph(std_id, "reviewer1")
    assert published["confirmed"] == 1
    assert published["release_number"] == 1
    assert published["embedding_model"] == "hash-test-v1"
    assert database.get_standard(std_id).graph_status == "published"
    release = database.get_standard_release(published["release_id"])
    assert release["snapshot"]["requirements"][0]["original_statement"] == (
        "The pulse amplitude shall be 12 V."
    )
    assert release["snapshot"]["requirements"][0]["interpretation_zh"] == "脉冲幅值应为 12 V"

    with database._connect() as conn:
        revision = conn.execute(
            "SELECT action, changed_by FROM standard_requirement_revisions WHERE requirement_id = ?",
            (requirement["id"],),
        ).fetchone()
    assert revision == ("modified_and_confirmed", "reviewer1")


def test_reject_requires_comment(_seeded_db):
    std_id, chunk_id = _ready_standard()
    database.replace_standard_graph(std_id, [{
        "clause_number": "5.1", "title": "Test pulse", "page_start": 12, "page_end": 12,
        "source_chunk_id": chunk_id, "requirements": [{
            "statement": "Not a requirement", "evidence_quote": "The pulse amplitude shall be 12 V.",
            "page_start": 12, "page_end": 12,
        }],
    }], {"failed_chunk_ids": []})
    requirement = database.get_standard_graph(std_id)["requirements"][0]
    with pytest.raises(ValueError, match="必须填写原因"):
        database.review_standard_requirement(std_id, requirement["id"], "rejected", "", "reviewer1")


def test_requirement_review_can_move_requirement_to_corrected_clause(_seeded_db):
    std_id, chunk_id = _ready_standard()
    database.replace_standard_graph(std_id, [{
        "clause_number": "4.2", "title": "Wrong inherited clause",
        "page_start": 8, "page_end": 8, "source_chunk_id": chunk_id,
        "requirements": [{
            "statement": "峰峰值电压应为 4 V。",
            "interpretation_zh": "峰峰值电压应为 4 V。",
            "original_statement": "Peak to peak voltage shall be 4 V.",
            "evidence_quote": "Peak to peak voltage shall be 4 V.",
            "page_start": 8, "page_end": 8,
        }],
    }], {"failed_unit_ids": []})
    requirement = database.get_standard_graph(std_id)["requirements"][0]

    reviewed = database.review_standard_requirement(
        std_id, requirement["id"], "confirmed", "修正跨页条款编号", "reviewer1",
        {"clause_number": "4.4.2", "clause_title": "Superimposed alternating voltage"},
    )

    assert reviewed["clause_number"] == "4.4.2"
    assert reviewed["clause_title"] == "Superimposed alternating voltage"
    with database._connect() as conn:
        revision = conn.execute(
            "SELECT updated_json FROM standard_requirement_revisions WHERE requirement_id = ?",
            (requirement["id"],),
        ).fetchone()
    assert '"clause_number": "4.4.2"' in revision[0]
