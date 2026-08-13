"""Content-addressed cache for validated unified extraction results."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from services.evidence_graph_extraction import (
    EvidenceGraphDocumentUnit,
    UnifiedExtractionResult,
)


EXTRACTION_CONTRACT_VERSION = "unified-extraction-5-source-anchors"


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class UnifiedExtractionCache:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS unified_extraction_cache (
                    cache_key TEXT PRIMARY KEY,
                    contract_version TEXT NOT NULL,
                    doc_id TEXT NOT NULL,
                    unit_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    model_manifest_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    observation_count INTEGER NOT NULL,
                    evidence_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    last_hit_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_unified_cache_doc
                    ON unified_extraction_cache(doc_id, unit_id);
            """)

    @staticmethod
    def source_hash(unit: EvidenceGraphDocumentUnit) -> str:
        image_hashes = [hashlib.sha256(value.encode()).hexdigest() for value in unit.image_data_urls]
        return _digest(_json({
            "native_text": unit.native_text,
            "page_number": unit.page_number,
            "sheet_name": unit.sheet_name,
            "cell_range": unit.cell_range,
            "visual_reason": unit.visual_reason,
            "image_hashes": image_hashes,
            "source_hash": unit.source_hash,
            "rendered_pdf_hash": unit.rendered_pdf_hash,
            "page_width": unit.page_width,
            "page_height": unit.page_height,
            "page_rotation": unit.page_rotation,
            "layout_lines": unit.layout_lines,
        }))

    @classmethod
    def key(cls, unit: EvidenceGraphDocumentUnit, model_manifest: dict) -> tuple[str, str, str]:
        source_hash = cls.source_hash(unit)
        manifest_hash = hashlib.sha256(_json(model_manifest).encode()).hexdigest()
        return (
            _digest(EXTRACTION_CONTRACT_VERSION, unit.doc_id, unit.unit_id, source_hash, manifest_hash),
            source_hash,
            manifest_hash,
        )

    def get(self, unit: EvidenceGraphDocumentUnit, model_manifest: dict) -> UnifiedExtractionResult | None:
        cache_key, _, _ = self.key(unit, model_manifest)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM unified_extraction_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE unified_extraction_cache SET last_hit_at = ?, hit_count = hit_count + 1 WHERE cache_key = ?",
                (now, cache_key),
            )
        cached = UnifiedExtractionResult.model_validate_json(row["result_json"])
        return self._remap(cached, unit)

    def put(self, unit: EvidenceGraphDocumentUnit, model_manifest: dict,
            result: UnifiedExtractionResult) -> None:
        if result.status == "failed":
            return
        cache_key, source_hash, manifest_hash = self.key(unit, model_manifest)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO unified_extraction_cache
                   (cache_key, contract_version, doc_id, unit_id, source_hash,
                    model_manifest_hash, result_json, observation_count,
                    evidence_count, created_at, last_hit_at, hit_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    cache_key, EXTRACTION_CONTRACT_VERSION, unit.doc_id, unit.unit_id,
                    source_hash, manifest_hash, result.model_dump_json(),
                    len(result.observations), len(result.evidence), now, now,
                ),
            )

    @staticmethod
    def _remap(result: UnifiedExtractionResult,
               unit: EvidenceGraphDocumentUnit) -> UnifiedExtractionResult:
        evidence_map: dict[str, str] = {}
        evidence = []
        for index, item in enumerate(result.evidence):
            new_id = "evidence-" + _digest(
                unit.graph_id, unit.doc_id, unit.unit_id, item.extraction_method,
                item.exact_quote, _json(item.bbox), item.evidence_id, str(index),
            )[:20]
            evidence_map[item.evidence_id] = new_id
            evidence.append(item.model_copy(update={
                "evidence_id": new_id,
                "graph_id": unit.graph_id,
                "doc_id": unit.doc_id,
                "doc_type": unit.doc_type,
                "filename": unit.filename,
                "page_number": unit.page_number,
                "sheet_name": unit.sheet_name,
                "cell_range": unit.cell_range,
            }))
        observations = []
        for index, item in enumerate(result.observations):
            observations.append(item.model_copy(update={
                "observation_id": "observation-" + _digest(
                    unit.graph_id, unit.unit_id, item.observation_type,
                    item.entity_name, item.field_name, item.observation_id, str(index),
                )[:20],
                "evidence_ids": [
                    evidence_map[evidence_id] for evidence_id in item.evidence_ids
                    if evidence_id in evidence_map
                ],
                "metadata": {
                    **item.metadata,
                    "doc_type": unit.doc_type,
                    "doc_id": unit.doc_id,
                    "unit_id": unit.unit_id,
                },
            }))
        return result.model_copy(update={
            "unit_id": unit.unit_id,
            "observations": observations,
            "evidence": evidence,
        })
