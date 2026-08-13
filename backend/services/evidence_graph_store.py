"""SQLite persistence for the unified evidence graph.

The schema intentionally uses portable relational tables and JSON payloads;
it does not rely on SQLite JSON queries and can later move to PostgreSQL.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from services.evidence_graph_models import (
    DecisionEvent,
    EvidenceRecord,
    GraphEdge,
    GraphNode,
    GraphRun,
    GraphSnapshot,
    FindingDecision,
    ReviewFinding,
)
from services.finding_presentation import attach_finding_presentation


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: str | None, fallback):
    if not value:
        return fallback
    return json.loads(value)


class EvidenceGraphStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence_graph_runs (
                    graph_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL CHECK(scope IN ('definition', 'run')),
                    set_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    graph_version TEXT NOT NULL,
                    rule_version TEXT NOT NULL,
                    standard_release_ids_json TEXT NOT NULL DEFAULT '[]',
                    model_manifest_json TEXT NOT NULL DEFAULT '{}',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK((scope = 'run' AND set_id <> '') OR (scope = 'definition' AND set_id = ''))
                );

                CREATE TABLE IF NOT EXISTS evidence_graph_evidence (
                    graph_id TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    doc_id TEXT NOT NULL DEFAULT '',
                    doc_type TEXT NOT NULL DEFAULT '',
                    filename TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    page_number INTEGER NOT NULL DEFAULT 0,
                    sheet_name TEXT NOT NULL DEFAULT '',
                    cell_range TEXT NOT NULL DEFAULT '',
                    table_id TEXT NOT NULL DEFAULT '',
                    bbox_json TEXT NOT NULL DEFAULT '[]',
                    exact_quote TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    extraction_method TEXT NOT NULL DEFAULT '',
                    model_version TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (graph_id, evidence_id),
                    FOREIGN KEY (graph_id) REFERENCES evidence_graph_runs(graph_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS evidence_graph_nodes (
                    graph_id TEXT NOT NULL,
                    node_id TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    canonical_key TEXT NOT NULL DEFAULT '',
                    resolution_status TEXT NOT NULL,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL DEFAULT 'system',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (graph_id, node_id),
                    FOREIGN KEY (graph_id) REFERENCES evidence_graph_runs(graph_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS evidence_graph_edges (
                    graph_id TEXT NOT NULL,
                    edge_id TEXT NOT NULL,
                    source_node_id TEXT NOT NULL,
                    target_node_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                    rationale TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (graph_id, edge_id),
                    FOREIGN KEY (graph_id, source_node_id)
                        REFERENCES evidence_graph_nodes(graph_id, node_id) ON DELETE CASCADE,
                    FOREIGN KEY (graph_id, target_node_id)
                        REFERENCES evidence_graph_nodes(graph_id, node_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS evidence_graph_findings (
                    graph_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    check_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    subject_node_ids_json TEXT NOT NULL DEFAULT '[]',
                    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                    dedupe_key TEXT NOT NULL,
                    rule_version TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (graph_id, finding_id),
                    UNIQUE (graph_id, dedupe_key),
                    FOREIGN KEY (graph_id) REFERENCES evidence_graph_runs(graph_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS evidence_graph_events (
                    graph_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL DEFAULT '',
                    subject_type TEXT NOT NULL DEFAULT '',
                    subject_id TEXT NOT NULL DEFAULT '',
                    old_state TEXT NOT NULL DEFAULT '',
                    new_state TEXT NOT NULL DEFAULT '',
                    reason_code TEXT NOT NULL DEFAULT '',
                    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (graph_id, event_id),
                    FOREIGN KEY (graph_id) REFERENCES evidence_graph_runs(graph_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS evidence_graph_finding_decisions (
                    graph_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('confirmed','dismissed','advisory','unresolved')),
                    resolution_code TEXT NOT NULL DEFAULT '',
                    resolution_status TEXT NOT NULL DEFAULT '',
                    comment TEXT NOT NULL DEFAULT '',
                    actor_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (graph_id, finding_id),
                    FOREIGN KEY (graph_id, finding_id)
                        REFERENCES evidence_graph_findings(graph_id, finding_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_egraph_runs_set
                    ON evidence_graph_runs(set_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_egraph_nodes_type_key
                    ON evidence_graph_nodes(graph_id, node_type, canonical_key);
                CREATE INDEX IF NOT EXISTS idx_egraph_edges_source
                    ON evidence_graph_edges(graph_id, source_node_id, relation_type);
                CREATE INDEX IF NOT EXISTS idx_egraph_edges_target
                    ON evidence_graph_edges(graph_id, target_node_id, relation_type);
                CREATE INDEX IF NOT EXISTS idx_egraph_findings_status
                    ON evidence_graph_findings(graph_id, status, severity);
                CREATE INDEX IF NOT EXISTS idx_egraph_events_subject
                    ON evidence_graph_events(graph_id, subject_type, subject_id, created_at);
                """
            )
            # Existing installations predate the plain-language remediation
            # fields. Keep the migration additive so historical decisions and
            # audit timestamps remain intact.
            columns = {
                row["name"] for row in conn.execute(
                    "PRAGMA table_info(evidence_graph_finding_decisions)"
                )
            }
            if "resolution_code" not in columns:
                conn.execute(
                    "ALTER TABLE evidence_graph_finding_decisions "
                    "ADD COLUMN resolution_code TEXT NOT NULL DEFAULT ''"
                )
            if "resolution_status" not in columns:
                conn.execute(
                    "ALTER TABLE evidence_graph_finding_decisions "
                    "ADD COLUMN resolution_status TEXT NOT NULL DEFAULT ''"
                )

    def set_finding_decision(self, decision: FindingDecision) -> FindingDecision:
        now = _utc_now()
        with self._connect() as conn:
            self._require_ids(
                conn, "evidence_graph_findings", decision.graph_id,
                "finding_id", [decision.finding_id],
            )
            conn.execute(
                """INSERT INTO evidence_graph_finding_decisions
                   (graph_id, finding_id, decision, resolution_code,
                    resolution_status, comment, actor_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(graph_id, finding_id) DO UPDATE SET
                     decision=excluded.decision,
                     resolution_code=excluded.resolution_code,
                     resolution_status=excluded.resolution_status,
                     comment=excluded.comment,
                     actor_id=excluded.actor_id, created_at=excluded.created_at""",
                (decision.graph_id, decision.finding_id, decision.decision,
                 decision.resolution_code, decision.resolution_status,
                 decision.comment, decision.actor_id, now),
            )
        return decision.model_copy(update={"created_at": now})

    def create_run(self, run: GraphRun) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO evidence_graph_runs
                   (graph_id, scope, set_id, status, graph_version, rule_version,
                    standard_release_ids_json, model_manifest_json, metadata_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run.graph_id,
                    str(run.scope),
                    run.set_id,
                    run.status,
                    run.graph_version,
                    run.rule_version,
                    _json(run.standard_release_ids),
                    _json(run.model_manifest),
                    _json(run.metadata),
                    now,
                    now,
                ),
            )

    def update_run_status(self, graph_id: str, status: str) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                """UPDATE evidence_graph_runs
                   SET status = ?, updated_at = ? WHERE graph_id = ?""",
                (status, _utc_now(), graph_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(graph_id)

    def update_run_metadata(self, graph_id: str, updates: dict) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM evidence_graph_runs WHERE graph_id = ?",
                (graph_id,),
            ).fetchone()
            if row is None:
                raise KeyError(graph_id)
            metadata = _loads(row["metadata_json"], {})
            metadata.update(updates)
            conn.execute(
                """UPDATE evidence_graph_runs
                   SET metadata_json = ?, updated_at = ? WHERE graph_id = ?""",
                (_json(metadata), _utc_now(), graph_id),
            )

    def list_runs(self, set_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT r.*,
                          (SELECT COUNT(*) FROM evidence_graph_nodes n
                           WHERE n.graph_id = r.graph_id) AS node_count,
                          (SELECT COUNT(*) FROM evidence_graph_edges e
                           WHERE e.graph_id = r.graph_id) AS edge_count,
                          (SELECT COUNT(*) FROM evidence_graph_findings f
                           WHERE f.graph_id = r.graph_id) AS finding_count,
                          (SELECT COUNT(*) FROM evidence_graph_findings f
                           WHERE f.graph_id = r.graph_id
                             AND f.status = 'confirmed_error') AS error_count,
                          (SELECT COUNT(*) FROM evidence_graph_findings f
                           WHERE f.graph_id = r.graph_id
                             AND f.status IN ('unresolved', 'unresolved_advisory')) AS unresolved_count
                          ,(SELECT COUNT(*) FROM evidence_graph_findings f
                            WHERE f.graph_id = r.graph_id
                              AND f.status IN ('confirmed_error', 'unresolved',
                                               'confirmed_advisory', 'unresolved_advisory')
                              AND NOT EXISTS (
                                SELECT 1 FROM evidence_graph_finding_decisions d
                                WHERE d.graph_id = f.graph_id
                                  AND d.finding_id = f.finding_id
                              )) AS pending_count
                   FROM evidence_graph_runs r
                   WHERE r.set_id = ?
                   ORDER BY r.created_at DESC""",
                (set_id,),
            ).fetchall()
        return [{
            "graph_id": row["graph_id"],
            "set_id": row["set_id"],
            "scope": row["scope"],
            "status": row["status"],
            "graph_version": row["graph_version"],
            "rule_version": row["rule_version"],
            "standard_release_ids": _loads(row["standard_release_ids_json"], []),
            "model_manifest": _loads(row["model_manifest_json"], {}),
            "metadata": _loads(row["metadata_json"], {}),
            "node_count": row["node_count"],
            "edge_count": row["edge_count"],
            "finding_count": row["finding_count"],
            "error_count": row["error_count"],
            "unresolved_count": row["unresolved_count"],
            "pending_count": row["pending_count"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        } for row in rows]

    def get_evidence(self, graph_id: str, evidence_id: str) -> EvidenceRecord | None:
        """Fetch one evidence record without materializing the full graph snapshot."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT e.* FROM evidence_graph_evidence e
                   JOIN evidence_graph_runs r ON r.graph_id = e.graph_id
                   WHERE e.graph_id = ? AND e.evidence_id = ?""",
                (graph_id, evidence_id),
            ).fetchone()
        if row is None:
            return None
        return EvidenceRecord(
            evidence_id=row["evidence_id"], graph_id=row["graph_id"],
            doc_id=row["doc_id"], doc_type=row["doc_type"],
            filename=row["filename"], state=row["state"],
            page_number=row["page_number"], sheet_name=row["sheet_name"],
            cell_range=row["cell_range"], table_id=row["table_id"],
            bbox=_loads(row["bbox_json"], []), exact_quote=row["exact_quote"],
            content_hash=row["content_hash"], extraction_method=row["extraction_method"],
            model_version=row["model_version"], confidence=row["confidence"],
            metadata=_loads(row["metadata_json"], {}),
        )

    def enrich_evidence_locator(
        self,
        graph_id: str,
        evidence_id: str,
        bbox: list[float],
        *,
        method: str,
        rectangles: list[list[float]] | None = None,
        rendered_pdf_hash: str = "",
        page_width: float = 0,
        page_height: float = 0,
        content_hash: str = "",
        anchor_quote: str = "",
    ) -> bool:
        """Persist a verified lazy locator without rewriting existing anchors."""
        if len(bbox) != 4:
            return False
        verified_rectangles = [
            list(map(float, rectangle))
            for rectangle in (rectangles or [])
            if isinstance(rectangle, (list, tuple)) and len(rectangle) == 4
        ]
        with self._connect() as conn:
            row = conn.execute(
                """SELECT bbox_json, metadata_json
                   FROM evidence_graph_evidence
                   WHERE graph_id = ? AND evidence_id = ?""",
                (graph_id, evidence_id),
            ).fetchone()
            if row is None or _loads(row["bbox_json"], []):
                return False
            metadata = _loads(row["metadata_json"], {})
            metadata["source_anchor"] = {
                **(
                    metadata.get("source_anchor", {})
                    if isinstance(metadata.get("source_anchor"), dict) else {}
                ),
                "version": 2,
                "status": "located",
                "coordinate_space": "pdf_points",
                "method": method,
                "kind": str(
                    (metadata.get("source_anchor") or {}).get("kind")
                    if isinstance(metadata.get("source_anchor"), dict) else ""
                ) or "quote",
                "cardinality": "single_region",
                "rectangles": verified_rectangles or [list(map(float, bbox))],
                "rendered_pdf_hash": rendered_pdf_hash,
                "page_width": page_width,
                "page_height": page_height,
                "anchor_quote": anchor_quote or str(
                    (metadata.get("source_anchor") or {}).get("anchor_quote")
                    if isinstance(metadata.get("source_anchor"), dict) else ""
                ),
            }
            cursor = conn.execute(
                """UPDATE evidence_graph_evidence
                   SET bbox_json = ?, metadata_json = ?,
                       content_hash = CASE WHEN content_hash = '' THEN ? ELSE content_hash END
                   WHERE graph_id = ? AND evidence_id = ? AND bbox_json = '[]'""",
                (_json(bbox), _json(metadata), content_hash, graph_id, evidence_id),
            )
            return cursor.rowcount == 1

    def add_evidence(self, evidence: EvidenceRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO evidence_graph_evidence
                   (graph_id, evidence_id, doc_id, doc_type, filename, state,
                    page_number, sheet_name, cell_range, table_id, bbox_json,
                    exact_quote, content_hash, extraction_method, model_version,
                    confidence, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    evidence.graph_id,
                    evidence.evidence_id,
                    evidence.doc_id,
                    evidence.doc_type,
                    evidence.filename,
                    str(evidence.state),
                    evidence.page_number,
                    evidence.sheet_name,
                    evidence.cell_range,
                    evidence.table_id,
                    _json(evidence.bbox),
                    evidence.exact_quote,
                    evidence.content_hash,
                    evidence.extraction_method,
                    evidence.model_version,
                    evidence.confidence,
                    _json(evidence.metadata),
                    _utc_now(),
                ),
            )

    def add_node(self, node: GraphNode) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO evidence_graph_nodes
                   (graph_id, node_id, node_type, label, canonical_key,
                    resolution_status, properties_json, created_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    node.graph_id,
                    node.node_id,
                    str(node.node_type),
                    node.label,
                    node.canonical_key,
                    str(node.resolution_status),
                    _json(node.properties),
                    node.created_by,
                    _utc_now(),
                ),
            )

    @staticmethod
    def _require_ids(conn: sqlite3.Connection, table: str, graph_id: str,
                     id_column: str, ids: Iterable[str]) -> None:
        requested = set(ids)
        if not requested:
            return
        placeholders = ",".join("?" for _ in requested)
        rows = conn.execute(
            f"SELECT {id_column} FROM {table} "
            f"WHERE graph_id = ? AND {id_column} IN ({placeholders})",
            (graph_id, *sorted(requested)),
        ).fetchall()
        found = {row[0] for row in rows}
        missing = sorted(requested - found)
        if missing:
            raise ValueError(f"unknown {id_column}: {', '.join(missing)}")

    def add_edge(self, edge: GraphEdge) -> None:
        with self._connect() as conn:
            self._require_ids(
                conn,
                "evidence_graph_nodes",
                edge.graph_id,
                "node_id",
                [edge.source_node_id, edge.target_node_id],
            )
            self._require_ids(
                conn,
                "evidence_graph_evidence",
                edge.graph_id,
                "evidence_id",
                edge.evidence_ids,
            )
            conn.execute(
                """INSERT INTO evidence_graph_edges
                   (graph_id, edge_id, source_node_id, target_node_id,
                    relation_type, status, origin, confidence, evidence_ids_json,
                    rationale, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    edge.graph_id,
                    edge.edge_id,
                    edge.source_node_id,
                    edge.target_node_id,
                    str(edge.relation_type),
                    str(edge.status),
                    str(edge.origin),
                    edge.confidence,
                    _json(edge.evidence_ids),
                    edge.rationale,
                    _json(edge.metadata),
                    _utc_now(),
                ),
            )

    def add_finding(self, finding: ReviewFinding) -> None:
        # The deterministic presentation is part of the finding snapshot. It
        # is written once so later copy-template changes do not rewrite history.
        finding = attach_finding_presentation(finding)
        with self._connect() as conn:
            self._require_ids(
                conn,
                "evidence_graph_nodes",
                finding.graph_id,
                "node_id",
                finding.subject_node_ids,
            )
            self._require_ids(
                conn,
                "evidence_graph_evidence",
                finding.graph_id,
                "evidence_id",
                finding.evidence_ids,
            )
            conn.execute(
                """INSERT INTO evidence_graph_findings
                   (graph_id, finding_id, check_id, status, severity, title,
                    description, subject_node_ids_json, evidence_ids_json,
                    dedupe_key, rule_version, metadata_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    finding.graph_id,
                    finding.finding_id,
                    finding.check_id,
                    str(finding.status),
                    str(finding.severity),
                    finding.title,
                    finding.description,
                    _json(finding.subject_node_ids),
                    _json(finding.evidence_ids),
                    finding.dedupe_key,
                    finding.rule_version,
                    _json(finding.metadata),
                    _utc_now(),
                ),
            )

    def update_finding_presentation(
        self,
        graph_id: str,
        finding_id: str,
        presentation: dict,
    ) -> bool:
        """Update only the optional explanation payload of a finding.

        The deterministic fields remain unchanged; this is used for the
        evidence-bound LLM supplement after the finding itself is persisted.
        """
        with self._connect() as conn:
            row = conn.execute(
                """SELECT metadata_json FROM evidence_graph_findings
                   WHERE graph_id = ? AND finding_id = ?""",
                (graph_id, finding_id),
            ).fetchone()
            if row is None:
                return False
            metadata = _loads(row["metadata_json"], {})
            existing = metadata.get("presentation")
            if not isinstance(existing, dict):
                existing = {}
            metadata["presentation"] = {**existing, **presentation}
            cursor = conn.execute(
                """UPDATE evidence_graph_findings
                   SET metadata_json = ?
                   WHERE graph_id = ? AND finding_id = ?""",
                (_json(metadata), graph_id, finding_id),
            )
            return cursor.rowcount == 1

    def add_event(self, event: DecisionEvent) -> None:
        with self._connect() as conn:
            self._require_ids(
                conn,
                "evidence_graph_evidence",
                event.graph_id,
                "evidence_id",
                event.evidence_ids,
            )
            conn.execute(
                """INSERT INTO evidence_graph_events
                   (graph_id, event_id, event_type, actor_type, actor_id,
                    subject_type, subject_id, old_state, new_state, reason_code,
                    evidence_ids_json, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.graph_id,
                    event.event_id,
                    event.event_type,
                    event.actor_type,
                    event.actor_id,
                    event.subject_type,
                    event.subject_id,
                    event.old_state,
                    event.new_state,
                    event.reason_code,
                    _json(event.evidence_ids),
                    _json(event.payload),
                    _utc_now(),
                ),
            )

    def record_event(self, graph_id: str, event_type: str, **kwargs) -> DecisionEvent:
        event = DecisionEvent(
            event_id=f"evt-{uuid.uuid4().hex[:20]}",
            graph_id=graph_id,
            event_type=event_type,
            **kwargs,
        )
        self.add_event(event)
        return event

    def get_snapshot(self, graph_id: str) -> GraphSnapshot:
        with self._connect() as conn:
            run_row = conn.execute(
                "SELECT * FROM evidence_graph_runs WHERE graph_id = ?", (graph_id,)
            ).fetchone()
            if run_row is None:
                raise KeyError(graph_id)

            run = GraphRun(
                graph_id=run_row["graph_id"],
                scope=run_row["scope"],
                set_id=run_row["set_id"],
                status=run_row["status"],
                graph_version=run_row["graph_version"],
                rule_version=run_row["rule_version"],
                standard_release_ids=_loads(run_row["standard_release_ids_json"], []),
                model_manifest=_loads(run_row["model_manifest_json"], {}),
                metadata=_loads(run_row["metadata_json"], {}),
            )

            evidence = [EvidenceRecord(
                evidence_id=row["evidence_id"], graph_id=row["graph_id"],
                doc_id=row["doc_id"], doc_type=row["doc_type"],
                filename=row["filename"], state=row["state"],
                page_number=row["page_number"], sheet_name=row["sheet_name"],
                cell_range=row["cell_range"], table_id=row["table_id"],
                bbox=_loads(row["bbox_json"], []), exact_quote=row["exact_quote"],
                content_hash=row["content_hash"], extraction_method=row["extraction_method"],
                model_version=row["model_version"], confidence=row["confidence"],
                metadata=_loads(row["metadata_json"], {}),
            ) for row in conn.execute(
                "SELECT * FROM evidence_graph_evidence WHERE graph_id = ? ORDER BY evidence_id",
                (graph_id,),
            )]

            nodes = [GraphNode(
                node_id=row["node_id"], graph_id=row["graph_id"],
                node_type=row["node_type"], label=row["label"],
                canonical_key=row["canonical_key"],
                resolution_status=row["resolution_status"],
                properties=_loads(row["properties_json"], {}),
                created_by=row["created_by"],
            ) for row in conn.execute(
                "SELECT * FROM evidence_graph_nodes WHERE graph_id = ? ORDER BY node_id",
                (graph_id,),
            )]

            edges = [GraphEdge(
                edge_id=row["edge_id"], graph_id=row["graph_id"],
                source_node_id=row["source_node_id"], target_node_id=row["target_node_id"],
                relation_type=row["relation_type"], status=row["status"],
                origin=row["origin"], confidence=row["confidence"],
                evidence_ids=_loads(row["evidence_ids_json"], []),
                rationale=row["rationale"], metadata=_loads(row["metadata_json"], {}),
            ) for row in conn.execute(
                "SELECT * FROM evidence_graph_edges WHERE graph_id = ? ORDER BY edge_id",
                (graph_id,),
            )]

            findings = [ReviewFinding(
                finding_id=row["finding_id"], graph_id=row["graph_id"],
                check_id=row["check_id"], status=row["status"],
                severity=row["severity"], title=row["title"],
                description=row["description"],
                subject_node_ids=_loads(row["subject_node_ids_json"], []),
                evidence_ids=_loads(row["evidence_ids_json"], []),
                dedupe_key=row["dedupe_key"], rule_version=row["rule_version"],
                metadata=_loads(row["metadata_json"], {}),
            ) for row in conn.execute(
                "SELECT * FROM evidence_graph_findings WHERE graph_id = ? ORDER BY finding_id",
                (graph_id,),
            )]

            events = [DecisionEvent(
                event_id=row["event_id"], graph_id=row["graph_id"],
                event_type=row["event_type"], actor_type=row["actor_type"],
                actor_id=row["actor_id"], subject_type=row["subject_type"],
                subject_id=row["subject_id"], old_state=row["old_state"],
                new_state=row["new_state"], reason_code=row["reason_code"],
                evidence_ids=_loads(row["evidence_ids_json"], []),
                payload=_loads(row["payload_json"], {}),
            ) for row in conn.execute(
                "SELECT * FROM evidence_graph_events WHERE graph_id = ? ORDER BY created_at, event_id",
                (graph_id,),
            )]

            decisions = [FindingDecision(
                graph_id=row["graph_id"], finding_id=row["finding_id"],
                decision=row["decision"],
                resolution_code=row["resolution_code"],
                resolution_status=row["resolution_status"],
                comment=row["comment"],
                actor_id=row["actor_id"], created_at=row["created_at"],
            ) for row in conn.execute(
                "SELECT * FROM evidence_graph_finding_decisions WHERE graph_id = ? ORDER BY created_at",
                (graph_id,),
            )]

        return GraphSnapshot(
            run=run,
            evidence=evidence,
            nodes=nodes,
            edges=edges,
            findings=findings,
            events=events,
            decisions=decisions,
        )
