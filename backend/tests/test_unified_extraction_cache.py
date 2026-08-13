from services.evidence_graph_extraction import (
    EvidenceGraphDocumentUnit,
    MaterializedObservation,
    UnifiedExtractionResult,
)
from services.evidence_graph_models import EvidenceRecord
from services.unified_extraction_cache import UnifiedExtractionCache


def _unit(graph_id="graph-1", native_text="P2a"):
    return EvidenceGraphDocumentUnit(
        unit_id="test_plan:page-1", graph_id=graph_id, doc_id="doc-1",
        doc_type="test_plan", filename="计划.pdf", native_text=native_text, page_number=1,
    )


def _result():
    return UnifiedExtractionResult(
        unit_id="test_plan:page-1", status="complete",
        evidence=[EvidenceRecord(
            evidence_id="old-evidence", graph_id="graph-1", doc_id="doc-1",
            doc_type="test_plan", filename="计划.pdf", page_number=1,
            exact_quote="P2a", extraction_method="native_identity_marker", confidence=1,
        )],
        observations=[MaterializedObservation(
            observation_id="old-observation", observation_type="test_item",
            entity_name="P2A", field_name="test_item_presence", raw_value="P2a",
            evidence_ids=["old-evidence"], extraction_sources=["native_identity_marker"],
            confidence=1,
        )],
    )


def test_cache_hit_remaps_graph_scoped_ids(tmp_path):
    cache = UnifiedExtractionCache(tmp_path / "cache.db")
    cache.init_schema()
    manifest = {"native_semantic_model": "deepseek:test", "vision_model": "qwen:test"}
    cache.put(_unit(), manifest, _result())

    hit = cache.get(_unit(graph_id="graph-2"), manifest)

    assert hit is not None
    assert hit.evidence[0].graph_id == "graph-2"
    assert hit.evidence[0].evidence_id != "old-evidence"
    assert hit.observations[0].evidence_ids == [hit.evidence[0].evidence_id]


def test_cache_invalidates_on_content_or_model_change(tmp_path):
    cache = UnifiedExtractionCache(tmp_path / "cache.db")
    cache.init_schema()
    manifest = {"native_semantic_model": "deepseek:v1"}
    cache.put(_unit(), manifest, _result())

    assert cache.get(_unit(native_text="P2b"), manifest) is None
    assert cache.get(_unit(), {"native_semantic_model": "deepseek:v2"}) is None


def test_failed_results_are_not_cached(tmp_path):
    cache = UnifiedExtractionCache(tmp_path / "cache.db")
    cache.init_schema()
    unit = _unit()
    cache.put(unit, {}, UnifiedExtractionResult(unit_id=unit.unit_id, status="failed"))

    assert cache.get(unit, {}) is None
