import os
import sqlite3
import uuid
import json
import asyncio
import time
import hashlib
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from models import AuditLogEntry, EmcStandard, StandardClause, User
from utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = os.getenv("DB_PATH", "review.db")

# Threshold for slow query logging (milliseconds)
SLOW_QUERY_MS = int(os.getenv("SLOW_QUERY_MS", "500"))


def _get_req_id() -> str:
    """Read the current request ID from structlog contextvars, if available."""
    try:
        import structlog
        ctx = structlog.contextvars.get_contextvars()
        return ctx.get("reqId", "")
    except Exception:
        return ""


@contextmanager
def _timed_query(label: str):
    """Context manager that logs a warning when the block exceeds SLOW_QUERY_MS."""
    t0 = time.monotonic()
    try:
        yield
    finally:
        elapsed = (time.monotonic() - t0) * 1000
        if elapsed > SLOW_QUERY_MS:
            logger.warning("slow_query", query=label, duration_ms=round(elapsed))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def _run_async(func, *args, **kwargs):
    """Run a synchronous DB function without detaching writes on cancellation.

    Python cannot stop a running worker thread.  A ``wait_for(to_thread(...))``
    timeout therefore only stopped the caller while the SQLite operation kept
    running.  Shield the worker and, when its caller is cancelled, wait for the
    current bounded SQLite operation to release its connection before
    propagating cancellation.
    """
    worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        try:
            await worker
        except Exception as exc:
            logger.error(
                "db_operation_failed_during_cancellation",
                function=func.__name__,
                error_type=type(exc).__name__,
            )
        raise


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                client_ip TEXT DEFAULT '',
                action TEXT NOT NULL,
                filename TEXT DEFAULT '',
                file_size_kb INTEGER DEFAULT 0,
                overall_result TEXT DEFAULT '',
                issue_count INTEGER DEFAULT 0,
                detail TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS emc_standards (
                id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                title TEXT NOT NULL,
                organization TEXT DEFAULT '',
                category TEXT DEFAULT '',
                version TEXT DEFAULT '',
                clauses TEXT DEFAULT '[]',
                is_builtin INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                employee_id TEXT PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'viewer',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
    _migrate_audit_log_employee_id()
    _migrate_users_name()
    _migrate_project_groups_category()
    _migrate_tags()
    _migrate_document_sets()
    _migrate_standard_knowledge_base()
    _migrate_standard_knowledge_graph()
    _migrate_standard_release_knowledge()
    _migrate_evidence_graph()


def _migrate_audit_log_employee_id():
    logger.info("migration_run", name="audit_log_employee_id")
    with _connect() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(audit_log)").fetchall()]
        if "employee_id" not in cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN employee_id TEXT DEFAULT ''")
        if "req_id" not in cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN req_id TEXT DEFAULT ''")


def _migrate_evidence_graph():
    """Create the authoritative graph and extraction-cache tables."""
    logger.info("migration_run", name="evidence_graph")
    from services.evidence_graph_store import EvidenceGraphStore
    from services.unified_extraction_cache import UnifiedExtractionCache

    EvidenceGraphStore(DB_PATH).init_schema()
    UnifiedExtractionCache(DB_PATH).init_schema()



def _migrate_users_name():
    logger.info("migration_run", name="users_name")
    with _connect() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "name" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN name TEXT DEFAULT ''")
        # project_groups table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_groups (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS project_group_members (
                group_id TEXT NOT NULL,
                employee_id TEXT NOT NULL,
                PRIMARY KEY (group_id, employee_id)
            )
        """)


def _migrate_project_groups_category():
    logger.info("migration_run", name="project_groups_category")
    with _connect() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(project_groups)").fetchall()]
        if "category" not in cols:
            conn.execute("ALTER TABLE project_groups ADD COLUMN category TEXT DEFAULT '其他'")


def _migrate_tags():
    logger.info("migration_run", name="tags")
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_by TEXT NOT NULL DEFAULT 'system',
                created_at TEXT NOT NULL
            )
        """)
    _seed_tags_from_existing_categories()


def _seed_tags_from_existing_categories():
    now = datetime.now().isoformat()
    standard = ['Q1', 'Q2', 'Q3', 'Q4', '年度', '整车', '零部件', 'EMC全项', '其他']
    with _connect() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        if existing > 0:
            return
        for cat in standard:
            tag_id = uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT OR IGNORE INTO tags (id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                (tag_id, cat, "system", now),
            )
        rows = conn.execute("SELECT DISTINCT category FROM project_groups").fetchall()
        for (cat,) in rows:
            if cat and cat.strip() and cat.strip() not in standard:
                tag_id = uuid.uuid4().hex[:12]
                conn.execute(
                    "INSERT OR IGNORE INTO tags (id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
                    (tag_id, cat.strip(), "system", now),
                )



def _migrate_document_sets():
    logger.info("migration_run", name="document_sets")
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_sets (
                set_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'incomplete',
                created_at TEXT NOT NULL,
                employee_id TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS set_documents (
                doc_id TEXT PRIMARY KEY,
                set_id TEXT NOT NULL,
                doc_type TEXT NOT NULL DEFAULT 'final_report',
                filename TEXT NOT NULL DEFAULT '',
                file_size_kb INTEGER NOT NULL DEFAULT 0,
                doc_version INTEGER NOT NULL DEFAULT 1,
                parent_doc_id TEXT NOT NULL DEFAULT '',
                upload_order INTEGER NOT NULL DEFAULT 0,
                plain_text TEXT NOT NULL DEFAULT '',
                html_content TEXT NOT NULL DEFAULT '',
                extraction_status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (set_id) REFERENCES document_sets(set_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS extracted_metadata (
                extraction_id TEXT PRIMARY KEY,
                set_id TEXT NOT NULL DEFAULT '',
                doc_id TEXT NOT NULL DEFAULT '',
                field_name TEXT NOT NULL DEFAULT '',
                field_value TEXT NOT NULL DEFAULT '',
                source_text TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (set_id) REFERENCES document_sets(set_id)
            )
        """)
        # Indexes for performance
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ds_employee ON document_sets(employee_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ds_status ON document_sets(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sd_set ON set_documents(set_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sd_set_type ON set_documents(set_id, doc_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sd_parent ON set_documents(parent_doc_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_archive_members (
                doc_id TEXT NOT NULL,
                member_index INTEGER NOT NULL,
                raw_filename TEXT NOT NULL DEFAULT '',
                display_filename TEXT NOT NULL DEFAULT '',
                content_hash TEXT NOT NULL DEFAULT '',
                file_size INTEGER NOT NULL DEFAULT 0,
                media_type TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (doc_id, member_index),
                FOREIGN KEY (doc_id) REFERENCES set_documents(doc_id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_dam_hash "
            "ON document_archive_members(doc_id, content_hash)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_em_doc ON extracted_metadata(doc_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_em_set ON extracted_metadata(set_id)")

        ds_cols = [row[1] for row in conn.execute("PRAGMA table_info(document_sets)").fetchall()]
        if "project_group_id" not in ds_cols:
            conn.execute("ALTER TABLE document_sets ADD COLUMN project_group_id TEXT NOT NULL DEFAULT ''")
            logger.info("migration_run", name="document_sets_project_group_id")
        conn.execute(
            """UPDATE document_sets SET project_group_id = ''
               WHERE project_group_id <> ''
                 AND NOT EXISTS (
                     SELECT 1 FROM project_groups pg
                     WHERE pg.id = document_sets.project_group_id
                 )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ds_project_group ON document_sets(project_group_id)")

        # Migration: add extraction_error column to set_documents (v2.1)
        cols = [row[1] for row in conn.execute("PRAGMA table_info(set_documents)").fetchall()]
        if "extraction_error" not in cols:
            conn.execute("ALTER TABLE set_documents ADD COLUMN extraction_error TEXT NOT NULL DEFAULT ''")
            logger.info("migration_run", name="set_documents_extraction_error")

        # Migration: add source_location column to extracted_metadata (v2.2)
        em_cols = [row[1] for row in conn.execute("PRAGMA table_info(extracted_metadata)").fetchall()]
        if "source_location" not in em_cols:
            conn.execute("ALTER TABLE extracted_metadata ADD COLUMN source_location TEXT NOT NULL DEFAULT ''")
            logger.info("migration_run", name="extracted_metadata_source_location")

        # Migration: add human_override columns to extracted_metadata (v2.3)
        if "human_override_value" not in em_cols:
            conn.execute("ALTER TABLE extracted_metadata ADD COLUMN human_override_value TEXT NOT NULL DEFAULT ''")
            logger.info("migration_run", name="extracted_metadata_human_override_value")
        if "human_override_comment" not in em_cols:
            conn.execute("ALTER TABLE extracted_metadata ADD COLUMN human_override_comment TEXT NOT NULL DEFAULT ''")
            logger.info("migration_run", name="extracted_metadata_human_override_comment")

        # Migration: add file_content BLOB column to set_documents (v2.4)
        sd_cols = [row[1] for row in conn.execute("PRAGMA table_info(set_documents)").fetchall()]
        if "file_content" not in sd_cols:
            conn.execute("ALTER TABLE set_documents ADD COLUMN file_content BLOB DEFAULT NULL")
            logger.info("migration_run", name="set_documents_file_content")
        if "extraction_quality" not in sd_cols:
            conn.execute("ALTER TABLE set_documents ADD COLUMN extraction_quality TEXT NOT NULL DEFAULT 'pending'")
            logger.info("migration_run", name="set_documents_extraction_quality")
        if "extraction_meta" not in sd_cols:
            conn.execute("ALTER TABLE set_documents ADD COLUMN extraction_meta TEXT NOT NULL DEFAULT '{}'")
            logger.info("migration_run", name="set_documents_extraction_meta")
        if "reviewed_by" not in sd_cols:
            conn.execute("ALTER TABLE set_documents ADD COLUMN reviewed_by TEXT NOT NULL DEFAULT ''")
            logger.info("migration_run", name="set_documents_reviewed_by")
        if "reviewed_at" not in sd_cols:
            conn.execute("ALTER TABLE set_documents ADD COLUMN reviewed_at TEXT NOT NULL DEFAULT ''")
            logger.info("migration_run", name="set_documents_reviewed_at")


def _migrate_standard_knowledge_base():
    """Add reusable standard files, knowledge chunks, and set associations."""
    logger.info("migration_run", name="standard_knowledge_base")
    with _connect() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(emc_standards)").fetchall()}
        additions = {
            "normalized_code": "TEXT NOT NULL DEFAULT ''",
            "source_filename": "TEXT NOT NULL DEFAULT ''",
            "file_content": "BLOB DEFAULT NULL",
            "file_sha256": "TEXT NOT NULL DEFAULT ''",
            "page_count": "INTEGER NOT NULL DEFAULT 0",
            "plain_text": "TEXT NOT NULL DEFAULT ''",
            "knowledge_status": "TEXT NOT NULL DEFAULT 'manual'",
            "knowledge_error": "TEXT NOT NULL DEFAULT ''",
            "knowledge_meta": "TEXT NOT NULL DEFAULT '{}'",
            "created_by": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE emc_standards ADD COLUMN {name} {ddl}")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS standard_knowledge_chunks (
                id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                clause TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                page_start INTEGER NOT NULL DEFAULT 0,
                page_end INTEGER NOT NULL DEFAULT 0,
                content TEXT NOT NULL DEFAULT '',
                structured_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (standard_id) REFERENCES emc_standards(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_skc_standard ON standard_knowledge_chunks(standard_id, chunk_index)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_set_standards (
                set_id TEXT NOT NULL,
                standard_id TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual',
                added_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (set_id, standard_id),
                FOREIGN KEY (set_id) REFERENCES document_sets(set_id) ON DELETE CASCADE,
                FOREIGN KEY (standard_id) REFERENCES emc_standards(id) ON DELETE RESTRICT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dss_set ON document_set_standards(set_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dss_standard ON document_set_standards(standard_id)")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_standard_file_sha "
            "ON emc_standards(file_sha256) WHERE file_sha256 <> ''"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_standard_normalized_code ON emc_standards(normalized_code)")
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS standard_knowledge_fts USING fts5(
                chunk_id UNINDEXED,
                standard_id UNINDEXED,
                code,
                clause,
                title,
                content
            )
        """)

        rows = conn.execute(
            "SELECT id, code, created_at, updated_at FROM emc_standards"
        ).fetchall()
        for std_id, code, created_at, updated_at in rows:
            normalized = re.sub(r"[^A-Z0-9]+", "", str(code or "").upper())
            if normalized:
                conn.execute(
                    "UPDATE emc_standards SET normalized_code = ? WHERE id = ? AND normalized_code = ''",
                    (normalized, std_id),
                )
            if not updated_at:
                conn.execute(
                    "UPDATE emc_standards SET updated_at = ? WHERE id = ?",
                    (created_at or datetime.now().isoformat(), std_id),
                )


def _migrate_standard_knowledge_graph():
    """Add the reviewable relational knowledge graph above raw evidence chunks."""
    logger.info("migration_run", name="standard_knowledge_graph")
    with _connect() as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(emc_standards)").fetchall()}
        additions = {
            "graph_status": "TEXT NOT NULL DEFAULT 'not_started'",
            "graph_error": "TEXT NOT NULL DEFAULT ''",
            "graph_meta": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, ddl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE emc_standards ADD COLUMN {name} {ddl}")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS standard_graph_clauses (
                id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                clause_number TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                parent_clause_number TEXT NOT NULL DEFAULT '',
                page_start INTEGER NOT NULL DEFAULT 0,
                page_end INTEGER NOT NULL DEFAULT 0,
                source_chunk_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (standard_id) REFERENCES emc_standards(id) ON DELETE CASCADE,
                FOREIGN KEY (source_chunk_id) REFERENCES standard_knowledge_chunks(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sgc_standard ON standard_graph_clauses(standard_id, clause_number)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS standard_graph_requirements (
                id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                clause_id TEXT NOT NULL,
                requirement_type TEXT NOT NULL DEFAULT 'other',
                test_item TEXT NOT NULL DEFAULT '',
                statement TEXT NOT NULL DEFAULT '',
                applicability TEXT NOT NULL DEFAULT '',
                evidence_quote TEXT NOT NULL DEFAULT '',
                page_start INTEGER NOT NULL DEFAULT 0,
                page_end INTEGER NOT NULL DEFAULT 0,
                source_chunk_id TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 0,
                review_status TEXT NOT NULL DEFAULT 'pending',
                review_comment TEXT NOT NULL DEFAULT '',
                reviewed_by TEXT NOT NULL DEFAULT '',
                reviewed_at TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (standard_id) REFERENCES emc_standards(id) ON DELETE CASCADE,
                FOREIGN KEY (clause_id) REFERENCES standard_graph_clauses(id) ON DELETE CASCADE,
                FOREIGN KEY (source_chunk_id) REFERENCES standard_knowledge_chunks(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sgr_standard_status ON standard_graph_requirements(standard_id, review_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sgr_clause ON standard_graph_requirements(clause_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS standard_requirement_parameters (
                id TEXT PRIMARY KEY,
                requirement_id TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL DEFAULT '',
                comparator TEXT NOT NULL DEFAULT '',
                value TEXT NOT NULL DEFAULT '',
                value_min TEXT NOT NULL DEFAULT '',
                value_max TEXT NOT NULL DEFAULT '',
                unit TEXT NOT NULL DEFAULT '',
                raw_text TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (requirement_id) REFERENCES standard_graph_requirements(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_srp_requirement ON standard_requirement_parameters(requirement_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS standard_requirement_relations (
                id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                source_requirement_id TEXT NOT NULL,
                relation_type TEXT NOT NULL DEFAULT 'references',
                target_ref TEXT NOT NULL DEFAULT '',
                target_requirement_id TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (standard_id) REFERENCES emc_standards(id) ON DELETE CASCADE,
                FOREIGN KEY (source_requirement_id) REFERENCES standard_graph_requirements(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_srr_source ON standard_requirement_relations(source_requirement_id)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS standard_requirement_revisions (
                id TEXT PRIMARY KEY,
                requirement_id TEXT NOT NULL,
                standard_id TEXT NOT NULL,
                previous_json TEXT NOT NULL DEFAULT '{}',
                updated_json TEXT NOT NULL DEFAULT '{}',
                action TEXT NOT NULL DEFAULT '',
                comment TEXT NOT NULL DEFAULT '',
                changed_by TEXT NOT NULL DEFAULT '',
                changed_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (standard_id) REFERENCES emc_standards(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_srev_requirement ON standard_requirement_revisions(requirement_id, changed_at)")


def _migrate_standard_release_knowledge():
    """Add bilingual review fields and immutable standard knowledge releases."""
    logger.info("migration_run", name="standard_release_knowledge")
    with _connect() as conn:
        requirement_cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(standard_graph_requirements)"
            ).fetchall()
        }
        additions = {
            "original_statement": "TEXT NOT NULL DEFAULT ''",
            "interpretation_zh": "TEXT NOT NULL DEFAULT ''",
            "interpretation_status": "TEXT NOT NULL DEFAULT 'pending'",
            "generation_model": "TEXT NOT NULL DEFAULT ''",
            "prompt_version": "TEXT NOT NULL DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in requirement_cols:
                conn.execute(f"ALTER TABLE standard_graph_requirements ADD COLUMN {name} {ddl}")
        conn.execute(
            "UPDATE standard_graph_requirements SET original_statement = evidence_quote "
            "WHERE original_statement = ''"
        )
        conn.execute(
            "UPDATE standard_graph_requirements SET interpretation_zh = statement "
            "WHERE interpretation_zh = ''"
        )
        conn.execute(
            "UPDATE standard_graph_requirements SET interpretation_status = "
            "CASE WHEN review_status = 'confirmed' THEN 'confirmed' ELSE 'pending' END"
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS standard_graph_releases (
                id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                release_number INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'published',
                source_file_sha256 TEXT NOT NULL DEFAULT '',
                snapshot_json TEXT NOT NULL,
                embedding_model TEXT NOT NULL DEFAULT '',
                prompt_version TEXT NOT NULL DEFAULT '',
                requirement_count INTEGER NOT NULL DEFAULT 0,
                created_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                published_by TEXT NOT NULL DEFAULT '',
                published_at TEXT NOT NULL DEFAULT '',
                UNIQUE(standard_id, release_number),
                FOREIGN KEY (standard_id) REFERENCES emc_standards(id) ON DELETE RESTRICT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_standard_release_standard "
            "ON standard_graph_releases(standard_id, release_number DESC)"
        )

        association_cols = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(document_set_standards)"
            ).fetchall()
        }
        if "release_id" not in association_cols:
            conn.execute(
                "ALTER TABLE document_set_standards ADD COLUMN release_id TEXT NOT NULL DEFAULT ''"
            )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS standard_requirement_embeddings (
                requirement_id TEXT NOT NULL,
                standard_id TEXT NOT NULL,
                release_id TEXT NOT NULL,
                model_name TEXT NOT NULL,
                dimensions INTEGER NOT NULL DEFAULT 0,
                vector BLOB NOT NULL,
                text_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(requirement_id, release_id, model_name),
                FOREIGN KEY (release_id) REFERENCES standard_graph_releases(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS standard_requirement_mappings (
                id TEXT PRIMARY KEY,
                standard_id TEXT NOT NULL,
                release_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                normalized_source_name TEXT NOT NULL,
                mapping_type TEXT NOT NULL DEFAULT 'covered',
                requirement_id TEXT NOT NULL DEFAULT '',
                scope_type TEXT NOT NULL DEFAULT 'standard',
                scope_value TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                review_status TEXT NOT NULL DEFAULT 'confirmed',
                created_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (release_id) REFERENCES standard_graph_releases(id) ON DELETE CASCADE
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_standard_mapping_lookup ON "
            "standard_requirement_mappings(standard_id, release_id, normalized_source_name, scope_type, scope_value)"
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_set_standard_skips (
                set_id TEXT NOT NULL,
                normalized_code TEXT NOT NULL,
                reference_code TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL,
                added_by TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(set_id, normalized_code),
                FOREIGN KEY (set_id) REFERENCES document_sets(set_id) ON DELETE CASCADE
            )
        """)



def save_audit_log(client_ip: str, action: str, filename: str = "",
                    file_size_kb: int = 0, overall_result: str = "",
                    issue_count: int = 0, detail: str = "",
                    duration_ms: int = 0, employee_id: str = "",
                    req_id: str = ""):
    extra: dict[str, object] = {}
    if detail:
        extra["msg"] = detail
    extra["duration_ms"] = duration_ms
    detail_json = json.dumps(extra, ensure_ascii=False)
    # Auto-detect reqId from structlog context if not explicitly provided
    if not req_id:
        req_id = _get_req_id()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, client_ip, employee_id, req_id, action, filename, "
            "file_size_kb, overall_result, issue_count, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), client_ip, employee_id, req_id, action, filename,
             file_size_kb, overall_result, issue_count, detail_json),
        )


def get_audit_logs(limit: int = 100, offset: int = 0,
                   action: str = "", ip: str = "",
                   employee_id: str = "", filename: str = "",
                   date_from: str = "",
                   date_to: str = "") -> tuple[list[AuditLogEntry], int]:
    t0 = time.monotonic()
    conditions = []
    params: list = []
    if action:
        conditions.append("action = ?")
        params.append(action)
    if filename:
        conditions.append("filename LIKE ?")
        params.append(f"%{filename}%")
    if ip:
        conditions.append("client_ip LIKE ?")
        params.append(f"%{ip}%")
    if employee_id:
        conditions.append("employee_id LIKE ?")
        params.append(f"%{employee_id}%")
    if date_from:
        conditions.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("timestamp <= ?")
        params.append(date_to + "T23:59:59")
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM audit_log{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT id, timestamp, client_ip, employee_id, req_id, action, filename, "
            f"file_size_kb, overall_result, issue_count, detail "
            f"FROM audit_log{where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    entries = []
    for row in rows:
        entries.append(AuditLogEntry(
            id=row[0],
            timestamp=row[1],
            client_ip=row[2],
            employee_id=row[3],
            req_id=row[4],
            action=row[5],
            filename=row[6],
            file_size_kb=row[7],
            overall_result=row[8],
            issue_count=row[9],
            detail=row[10],
        ))
    elapsed = (time.monotonic() - t0) * 1000
    if elapsed > SLOW_QUERY_MS:
        logger.warning("slow_query", query="get_audit_logs", duration_ms=round(elapsed),
                       total=total)
    return entries, total



def save_standard(code: str, title: str, organization: str = "",
                  category: str = "", version: str = "",
                  clauses: list[StandardClause] | None = None,
                  is_builtin: bool = False) -> str:
    std_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    clauses_json = json.dumps(
        [c.model_dump() for c in (clauses or [])], ensure_ascii=False
    )
    with _connect() as conn:
        conn.execute(
            "INSERT INTO emc_standards (id, code, title, organization, category, "
            "version, clauses, is_builtin, created_at, normalized_code, knowledge_status, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?)",
            (std_id, code, title, organization, category, version,
             clauses_json, 1 if is_builtin else 0, now,
             normalize_standard_code(code), now),
        )
    return std_id


def normalize_standard_code(code: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(code or "").upper())


_STANDARD_SELECT = """
    SELECT s.id, s.code, s.title, s.organization, s.category, s.version,
           s.clauses, s.is_builtin, s.created_at, s.normalized_code,
           s.source_filename, s.file_sha256, s.page_count,
           s.knowledge_status, s.knowledge_error, s.knowledge_meta,
           s.created_by, s.updated_at,
           (SELECT COUNT(*) FROM standard_knowledge_chunks c WHERE c.standard_id = s.id) AS chunk_count,
           s.graph_status, s.graph_error, s.graph_meta,
           (SELECT COUNT(*) FROM standard_graph_clauses gc WHERE gc.standard_id = s.id) AS graph_clause_count,
           (SELECT COUNT(*) FROM standard_graph_requirements gr WHERE gr.standard_id = s.id) AS graph_requirement_count,
           (SELECT COUNT(*) FROM standard_graph_requirements gr WHERE gr.standard_id = s.id AND gr.review_status = 'pending') AS graph_pending_count,
           (SELECT COUNT(*) FROM standard_graph_requirements gr WHERE gr.standard_id = s.id AND gr.review_status = 'confirmed') AS graph_confirmed_count,
           (SELECT COUNT(*) FROM standard_graph_requirements gr WHERE gr.standard_id = s.id AND gr.review_status = 'rejected') AS graph_rejected_count,
           COALESCE((SELECT id FROM standard_graph_releases rel WHERE rel.standard_id = s.id AND rel.status = 'published' ORDER BY rel.release_number DESC LIMIT 1), '') AS latest_release_id,
           COALESCE((SELECT MAX(release_number) FROM standard_graph_releases rel WHERE rel.standard_id = s.id AND rel.status = 'published'), 0) AS latest_release_number,
           (SELECT COUNT(*) FROM standard_graph_releases rel WHERE rel.standard_id = s.id AND rel.status = 'published') AS release_count
    FROM emc_standards s
"""


def _row_to_standard(row) -> EmcStandard:
    clauses_data = json.loads(row[6] or "[]")
    knowledge_meta = json.loads(row[15] or "{}")
    return EmcStandard(
        id=row[0], code=row[1], title=row[2], organization=row[3],
        category=row[4], version=row[5],
        clauses=[StandardClause(**c) for c in clauses_data],
        is_builtin=bool(row[7]), created_at=row[8],
        normalized_code=row[9], source_filename=row[10], file_sha256=row[11],
        page_count=row[12], knowledge_status=row[13], knowledge_error=row[14],
        knowledge_meta=knowledge_meta, created_by=row[16], updated_at=row[17],
        chunk_count=row[18], graph_status=row[19], graph_error=row[20],
        graph_meta=json.loads(row[21] or "{}"), graph_clause_count=row[22],
        graph_requirement_count=row[23], graph_pending_count=row[24],
        graph_confirmed_count=row[25], graph_rejected_count=row[26],
        latest_release_id=row[27], latest_release_number=row[28], release_count=row[29],
    )


def get_standards(organization: str = "", category: str = "",
                  keyword: str = "") -> list[EmcStandard]:
    conditions = []
    params: list = []
    if organization:
        conditions.append("organization = ?")
        params.append(organization)
    if category:
        conditions.append("category = ?")
        params.append(category)
    if keyword:
        conditions.append("(code LIKE ? OR title LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with _connect() as conn:
        rows = conn.execute(
            f"{_STANDARD_SELECT}{where} ORDER BY s.organization, s.code", params
        ).fetchall()
    return [_row_to_standard(row) for row in rows]


def get_standard(std_id: str) -> EmcStandard | None:
    with _connect() as conn:
        row = conn.execute(f"{_STANDARD_SELECT} WHERE s.id = ?", (std_id,)).fetchone()
    if row is None:
        return None
    return _row_to_standard(row)


def create_standard_asset(
    file_bytes: bytes,
    source_filename: str,
    code: str = "",
    title: str = "",
    organization: str = "",
    category: str = "",
    version: str = "",
    created_by: str = "",
) -> tuple[str, bool]:
    """Create a reusable standard file, returning (standard_id, duplicate)."""
    digest = hashlib.sha256(file_bytes).hexdigest()
    normalized = normalize_standard_code(code)
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM emc_standards WHERE file_sha256 = ?", (digest,)
        ).fetchone()
        if existing:
            logger.info("standard_upload_duplicate", standard_id=existing[0], reason="file_sha256")
            return existing[0], True
        if normalized and version:
            existing = conn.execute(
                "SELECT id FROM emc_standards WHERE normalized_code = ? AND version = ? "
                "AND knowledge_status IN ('pending', 'processing', 'ready')",
                (normalized, version),
            ).fetchone()
            if existing:
                logger.info("standard_upload_duplicate", standard_id=existing[0], reason="code_version")
                return existing[0], True

        std_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT INTO emc_standards
               (id, code, title, organization, category, version, clauses, is_builtin,
                created_at, normalized_code, source_filename, file_content, file_sha256,
                knowledge_status, created_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, '[]', 0, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (std_id, code or source_filename.rsplit('.', 1)[0], title or source_filename,
             organization, category, version, now, normalized, source_filename,
             file_bytes, digest, created_by, now),
        )
    logger.info("standard_upload_completed", standard_id=std_id,
                filename=source_filename, file_size=len(file_bytes), file_sha256=digest[:12])
    return std_id, False


def get_standard_file(std_id: str) -> tuple[str, bytes] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT source_filename, file_content FROM emc_standards WHERE id = ?", (std_id,)
        ).fetchone()
    if not row or row[1] is None:
        return None
    return row[0], bytes(row[1])


def get_stuck_standards() -> list[dict]:
    """Return standard ingestion jobs that should be resumed after restart."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, source_filename, knowledge_status
               FROM emc_standards
               WHERE knowledge_status IN ('pending', 'processing')
                 AND file_content IS NOT NULL
               ORDER BY created_at"""
        ).fetchall()
    return [
        {"id": row[0], "source_filename": row[1], "knowledge_status": row[2]}
        for row in rows
    ]


def get_stuck_standard_graphs() -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id FROM emc_standards
               WHERE graph_status = 'extracting' AND knowledge_status = 'ready'
               ORDER BY updated_at"""
        ).fetchall()
    return [row[0] for row in rows]


def update_standard_knowledge(
    std_id: str, *, status: str, code: str | None = None,
    title: str | None = None, organization: str | None = None,
    category: str | None = None, version: str | None = None,
    page_count: int | None = None, plain_text: str | None = None,
    error: str = "", meta: dict | None = None,
) -> bool:
    updates = ["knowledge_status = ?", "knowledge_error = ?", "updated_at = ?"]
    values: list = [status, error, datetime.now().isoformat()]
    optional = {
        "code": code, "title": title, "organization": organization,
        "category": category, "version": version,
        "page_count": page_count, "plain_text": plain_text,
    }
    for field, value in optional.items():
        if value is not None:
            updates.append(f"{field} = ?")
            values.append(value)
    if code is not None:
        updates.append("normalized_code = ?")
        values.append(normalize_standard_code(code))
    if meta is not None:
        updates.append("knowledge_meta = ?")
        values.append(json.dumps(meta, ensure_ascii=False))
    values.append(std_id)
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE emc_standards SET {', '.join(updates)} WHERE id = ?", values
        )
    return cur.rowcount > 0


def replace_standard_knowledge_chunks(std_id: str, chunks: list[dict]) -> int:
    now = datetime.now().isoformat()
    standard = get_standard(std_id)
    if not standard:
        return 0
    with _connect() as conn:
        old_ids = [r[0] for r in conn.execute(
            "SELECT id FROM standard_knowledge_chunks WHERE standard_id = ?", (std_id,)
        ).fetchall()]
        for chunk_id in old_ids:
            conn.execute("DELETE FROM standard_knowledge_fts WHERE chunk_id = ?", (chunk_id,))
        conn.execute("DELETE FROM standard_knowledge_chunks WHERE standard_id = ?", (std_id,))
        for index, chunk in enumerate(chunks):
            chunk_id = str(chunk.get("id") or uuid.uuid4().hex[:16])
            clause = str(chunk.get("clause") or "")
            title = str(chunk.get("title") or "")
            content = str(chunk.get("content") or "")
            structured = chunk.get("structured") if isinstance(chunk.get("structured"), dict) else {}
            conn.execute(
                """INSERT INTO standard_knowledge_chunks
                   (id, standard_id, chunk_index, clause, title, page_start, page_end,
                    content, structured_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (chunk_id, std_id, index, clause, title,
                 int(chunk.get("page_start") or 0), int(chunk.get("page_end") or 0),
                 content, json.dumps(structured, ensure_ascii=False), now),
            )
            conn.execute(
                "INSERT INTO standard_knowledge_fts "
                "(chunk_id, standard_id, code, clause, title, content) VALUES (?, ?, ?, ?, ?, ?)",
                (chunk_id, std_id, standard.code, clause, title, content),
            )
    return len(chunks)


def get_standard_knowledge_chunks(std_id: str, query: str = "", limit: int = 12) -> list[dict]:
    with _connect() as conn:
        if query.strip():
            terms = re.findall(r"[A-Za-z0-9./-]+|[\u4e00-\u9fff]{2,}", query)
            fts_query = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms[:12])
            if fts_query:
                try:
                    rows = conn.execute(
                        """SELECT c.id, c.standard_id, c.chunk_index, c.clause, c.title,
                                  c.page_start, c.page_end, c.content, c.structured_json, c.created_at,
                                  bm25(standard_knowledge_fts) AS score
                           FROM standard_knowledge_fts
                           JOIN standard_knowledge_chunks c ON c.id = standard_knowledge_fts.chunk_id
                           WHERE standard_knowledge_fts MATCH ? AND c.standard_id = ?
                           ORDER BY score LIMIT ?""",
                        (fts_query, std_id, limit),
                    ).fetchall()
                except sqlite3.OperationalError as exc:
                    logger.warning("standard_fts_query_failed", standard_id=std_id,
                                   query=fts_query[:300], error=str(exc))
                    rows = []
            else:
                rows = []
        else:
            rows = conn.execute(
                """SELECT id, standard_id, chunk_index, clause, title, page_start, page_end,
                          content, structured_json, created_at, 0 AS score
                   FROM standard_knowledge_chunks WHERE standard_id = ?
                   ORDER BY chunk_index LIMIT ?""",
                (std_id, limit),
            ).fetchall()
    return [
        {"id": r[0], "standard_id": r[1], "chunk_index": r[2], "clause": r[3],
         "title": r[4], "page_start": r[5], "page_end": r[6], "content": r[7],
         "structured": json.loads(r[8] or "{}"), "created_at": r[9], "score": r[10]}
        for r in rows
    ]


def update_standard_graph_status(
    std_id: str, status: str, error: str = "", meta: dict | None = None,
) -> bool:
    updates = ["graph_status = ?", "graph_error = ?", "updated_at = ?"]
    values: list = [status, error, datetime.now().isoformat()]
    if meta is not None:
        updates.append("graph_meta = ?")
        values.append(json.dumps(meta, ensure_ascii=False))
    values.append(std_id)
    with _connect() as conn:
        cur = conn.execute(
            f"UPDATE emc_standards SET {', '.join(updates)} WHERE id = ?", values,
        )
    return cur.rowcount > 0


def replace_standard_graph(std_id: str, clauses: list[dict], meta: dict) -> dict:
    """Atomically replace unreviewed graph candidates produced by one extraction run."""
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM standard_graph_clauses WHERE standard_id = ?", (std_id,))
        requirement_count = 0
        for clause in clauses:
            clause_id = str(clause.get("id") or uuid.uuid4().hex[:16])
            source_chunk_id = str(clause.get("source_chunk_id") or "")
            conn.execute(
                """INSERT INTO standard_graph_clauses
                   (id, standard_id, clause_number, title, parent_clause_number,
                    page_start, page_end, source_chunk_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (clause_id, std_id, str(clause.get("clause_number") or ""),
                 str(clause.get("title") or ""), str(clause.get("parent_clause_number") or ""),
                 int(clause.get("page_start") or 0), int(clause.get("page_end") or 0),
                 source_chunk_id, now, now),
            )
            for requirement in clause.get("requirements") or []:
                requirement_id = str(requirement.get("id") or uuid.uuid4().hex[:16])
                conn.execute(
                    """INSERT INTO standard_graph_requirements
                       (id, standard_id, clause_id, requirement_type, test_item, statement,
                        original_statement, interpretation_zh, interpretation_status,
                        applicability, evidence_quote, page_start, page_end, source_chunk_id,
                        confidence, review_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                    (requirement_id, std_id, clause_id,
                     str(requirement.get("requirement_type") or "other"),
                     str(requirement.get("test_item") or ""),
                     str(requirement.get("interpretation_zh") or requirement.get("statement") or ""),
                     str(requirement.get("original_statement") or requirement.get("evidence_quote") or ""),
                     str(requirement.get("interpretation_zh") or requirement.get("statement") or ""),
                     str(requirement.get("applicability") or ""),
                     str(requirement.get("evidence_quote") or ""),
                     int(requirement.get("page_start") or clause.get("page_start") or 0),
                     int(requirement.get("page_end") or clause.get("page_end") or 0),
                     source_chunk_id, float(requirement.get("confidence") or 0), now, now),
                )
                for parameter in requirement.get("parameters") or []:
                    conn.execute(
                        """INSERT INTO standard_requirement_parameters
                           (id, requirement_id, name, symbol, comparator, value,
                            value_min, value_max, unit, raw_text)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (uuid.uuid4().hex[:16], requirement_id,
                         str(parameter.get("name") or ""), str(parameter.get("symbol") or ""),
                         str(parameter.get("comparator") or ""), str(parameter.get("value") or ""),
                         str(parameter.get("value_min") or ""), str(parameter.get("value_max") or ""),
                         str(parameter.get("unit") or ""), str(parameter.get("raw_text") or "")),
                    )
                for relation in requirement.get("relations") or []:
                    conn.execute(
                        """INSERT INTO standard_requirement_relations
                           (id, standard_id, source_requirement_id, relation_type, target_ref)
                           VALUES (?, ?, ?, ?, ?)""",
                        (uuid.uuid4().hex[:16], std_id, requirement_id,
                         str(relation.get("relation_type") or "references"),
                         str(relation.get("target_ref") or "")),
                    )
                requirement_count += 1
        conn.execute(
            "UPDATE emc_standards SET graph_status = 'pending_review', graph_error = '', "
            "graph_meta = ?, updated_at = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), now, std_id),
        )
    return {"clause_count": len(clauses), "requirement_count": requirement_count}


def append_standard_graph(std_id: str, clauses: list[dict], meta: dict) -> dict:
    """Append recovered extraction units without replacing reviewed requirements."""
    now = datetime.now().isoformat()
    requirement_count = 0
    with _connect() as conn:
        for clause in clauses:
            clause_id = str(clause.get("id") or uuid.uuid4().hex[:16])
            source_chunk_id = str(clause.get("source_chunk_id") or "")
            conn.execute(
                """INSERT INTO standard_graph_clauses
                   (id, standard_id, clause_number, title, parent_clause_number,
                    page_start, page_end, source_chunk_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (clause_id, std_id, str(clause.get("clause_number") or ""),
                 str(clause.get("title") or ""), str(clause.get("parent_clause_number") or ""),
                 int(clause.get("page_start") or 0), int(clause.get("page_end") or 0),
                 source_chunk_id, now, now),
            )
            for requirement in clause.get("requirements") or []:
                requirement_id = str(requirement.get("id") or uuid.uuid4().hex[:16])
                conn.execute(
                    """INSERT INTO standard_graph_requirements
                       (id, standard_id, clause_id, requirement_type, test_item, statement,
                        original_statement, interpretation_zh, interpretation_status,
                        applicability, evidence_quote, page_start, page_end, source_chunk_id,
                        confidence, review_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                    (requirement_id, std_id, clause_id,
                     str(requirement.get("requirement_type") or "other"),
                     str(requirement.get("test_item") or ""),
                     str(requirement.get("interpretation_zh") or requirement.get("statement") or ""),
                     str(requirement.get("original_statement") or requirement.get("evidence_quote") or ""),
                     str(requirement.get("interpretation_zh") or requirement.get("statement") or ""),
                     str(requirement.get("applicability") or ""), str(requirement.get("evidence_quote") or ""),
                     int(requirement.get("page_start") or clause.get("page_start") or 0),
                     int(requirement.get("page_end") or clause.get("page_end") or 0),
                     source_chunk_id, float(requirement.get("confidence") or 0), now, now),
                )
                for parameter in requirement.get("parameters") or []:
                    conn.execute(
                        """INSERT INTO standard_requirement_parameters
                           (id, requirement_id, name, symbol, comparator, value,
                            value_min, value_max, unit, raw_text)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (uuid.uuid4().hex[:16], requirement_id,
                         str(parameter.get("name") or ""), str(parameter.get("symbol") or ""),
                         str(parameter.get("comparator") or ""), str(parameter.get("value") or ""),
                         str(parameter.get("value_min") or ""), str(parameter.get("value_max") or ""),
                         str(parameter.get("unit") or ""), str(parameter.get("raw_text") or "")),
                    )
                for relation in requirement.get("relations") or []:
                    conn.execute(
                        """INSERT INTO standard_requirement_relations
                           (id, standard_id, source_requirement_id, relation_type, target_ref)
                           VALUES (?, ?, ?, ?, ?)""",
                        (uuid.uuid4().hex[:16], std_id, requirement_id,
                         str(relation.get("relation_type") or "references"),
                         str(relation.get("target_ref") or "")),
                    )
                requirement_count += 1
        conn.execute(
            "UPDATE emc_standards SET graph_status = 'pending_review', graph_error = ?, "
            "graph_meta = ?, updated_at = ? WHERE id = ?",
            (str(meta.get("graph_error") or ""), json.dumps(meta, ensure_ascii=False), now, std_id),
        )
    return {"clause_count": len(clauses), "requirement_count": requirement_count}


def get_standard_graph(std_id: str, review_status: str = "") -> dict:
    with _connect() as conn:
        clause_rows = conn.execute(
            """SELECT id, standard_id, clause_number, title, parent_clause_number,
                      page_start, page_end, source_chunk_id
               FROM standard_graph_clauses WHERE standard_id = ?
               ORDER BY page_start, clause_number, id""", (std_id,),
        ).fetchall()
        req_sql = """SELECT r.id, r.standard_id, r.clause_id, c.clause_number, c.title,
                            r.requirement_type, r.test_item, r.statement,
                            r.original_statement, r.interpretation_zh, r.interpretation_status,
                            r.applicability,
                            r.evidence_quote, r.page_start, r.page_end, r.source_chunk_id,
                            r.confidence, r.review_status, r.review_comment, r.reviewed_by,
                            r.reviewed_at, r.created_at, r.updated_at
                     FROM standard_graph_requirements r
                     JOIN standard_graph_clauses c ON c.id = r.clause_id
                     WHERE r.standard_id = ?"""
        params: list = [std_id]
        if review_status:
            req_sql += " AND r.review_status = ?"
            params.append(review_status)
        req_sql += " ORDER BY r.page_start, c.clause_number, r.id"
        req_rows = conn.execute(req_sql, params).fetchall()
        req_ids = [row[0] for row in req_rows]
        parameters: dict[str, list[dict]] = {req_id: [] for req_id in req_ids}
        relations: dict[str, list[dict]] = {req_id: [] for req_id in req_ids}
        if req_ids:
            placeholders = ",".join("?" for _ in req_ids)
            for row in conn.execute(
                f"SELECT id, requirement_id, name, symbol, comparator, value, value_min, value_max, unit, raw_text "
                f"FROM standard_requirement_parameters WHERE requirement_id IN ({placeholders})", req_ids,
            ).fetchall():
                parameters[row[1]].append({"id": row[0], "name": row[2], "symbol": row[3],
                                           "comparator": row[4], "value": row[5], "value_min": row[6],
                                           "value_max": row[7], "unit": row[8], "raw_text": row[9]})
            for row in conn.execute(
                f"SELECT id, source_requirement_id, relation_type, target_ref, target_requirement_id "
                f"FROM standard_requirement_relations WHERE source_requirement_id IN ({placeholders})", req_ids,
            ).fetchall():
                relations[row[1]].append({"id": row[0], "relation_type": row[2],
                                          "target_ref": row[3], "target_requirement_id": row[4]})
    requirements = [{"id": r[0], "standard_id": r[1], "clause_id": r[2],
                     "clause_number": r[3], "clause_title": r[4], "requirement_type": r[5],
                     "test_item": r[6], "statement": r[7], "original_statement": r[8],
                     "interpretation_zh": r[9], "interpretation_status": r[10],
                     "applicability": r[11], "evidence_quote": r[12],
                     "page_start": r[13], "page_end": r[14], "source_chunk_id": r[15],
                     "confidence": r[16], "review_status": r[17],
                     "review_comment": r[18], "reviewed_by": r[19], "reviewed_at": r[20],
                     "created_at": r[21], "updated_at": r[22], "parameters": parameters[r[0]],
                     "relations": relations[r[0]]} for r in req_rows]
    by_clause: dict[str, list[dict]] = {}
    for requirement in requirements:
        by_clause.setdefault(requirement["clause_id"], []).append(requirement)
    clauses = [{"id": r[0], "standard_id": r[1], "clause_number": r[2], "title": r[3],
                "parent_clause_number": r[4], "page_start": r[5], "page_end": r[6],
                "source_chunk_id": r[7], "requirements": by_clause.get(r[0], [])}
               for r in clause_rows]
    return {"clauses": clauses, "requirements": requirements}


_REVIEWABLE_FIELDS = {
    "requirement_type", "test_item", "statement", "interpretation_zh", "applicability",
}
_REVIEWABLE_CLAUSE_FIELDS = {"clause_number", "clause_title"}


def review_standard_requirement(
    std_id: str, requirement_id: str, review_status: str, comment: str,
    reviewer: str, updates: dict | None = None,
) -> dict | None:
    if review_status not in {"confirmed", "rejected"}:
        raise ValueError("确认状态只能是 confirmed 或 rejected")
    if review_status == "rejected" and not comment.strip():
        raise ValueError("标记为无效要求时必须填写原因")
    now = datetime.now().isoformat()
    with _connect() as conn:
        row = conn.execute(
            """SELECT requirement_type, test_item, statement, interpretation_zh,
                      original_statement, applicability, review_status, review_comment,
                      reviewed_by, reviewed_at
               FROM standard_graph_requirements WHERE id = ? AND standard_id = ?""",
            (requirement_id, std_id),
        ).fetchone()
        if not row:
            return None
        clause_row = conn.execute(
            """SELECT c.id, c.clause_number, c.title, c.parent_clause_number,
                      c.page_start, c.page_end, c.source_chunk_id
               FROM standard_graph_requirements r
               JOIN standard_graph_clauses c ON c.id = r.clause_id
               WHERE r.id = ? AND r.standard_id = ?""",
            (requirement_id, std_id),
        ).fetchone()
        previous = {"requirement_type": row[0], "test_item": row[1], "statement": row[2],
                    "interpretation_zh": row[3], "original_statement": row[4],
                    "applicability": row[5], "review_status": row[6],
                    "review_comment": row[7], "reviewed_by": row[8],
                    "reviewed_at": row[9],
                    "clause_number": clause_row[1] if clause_row else "",
                    "clause_title": clause_row[2] if clause_row else ""}
        clean_updates = {key: str(value) for key, value in (updates or {}).items()
                         if key in _REVIEWABLE_FIELDS and value is not None}
        clause_updates = {key: str(value).strip() for key, value in (updates or {}).items()
                          if key in _REVIEWABLE_CLAUSE_FIELDS and value is not None}
        if "statement" in clean_updates and "interpretation_zh" not in clean_updates:
            clean_updates["interpretation_zh"] = clean_updates["statement"]
        if "interpretation_zh" in clean_updates:
            clean_updates["statement"] = clean_updates["interpretation_zh"]
        interpretation = clean_updates.get("interpretation_zh", previous["interpretation_zh"])
        if review_status == "confirmed" and not interpretation.strip():
            raise ValueError("确认前必须填写中文释义")
        if review_status == "confirmed" and not re.search(r"[\u4e00-\u9fff]", interpretation):
            raise ValueError("中文释义必须包含中文，不能直接复制外文原文")
        assignments = [f"{key} = ?" for key in clean_updates]
        values = list(clean_updates.values())
        assignments.extend(["review_status = ?", "interpretation_status = ?",
                            "review_comment = ?", "reviewed_by = ?",
                            "reviewed_at = ?", "updated_at = ?"])
        values.extend([review_status, "confirmed" if review_status == "confirmed" else "rejected",
                       comment, reviewer, now, now, requirement_id, std_id])
        conn.execute(
            f"UPDATE standard_graph_requirements SET {', '.join(assignments)} WHERE id = ? AND standard_id = ?",
            values,
        )
        if clause_row and clause_updates:
            target_number = clause_updates.get("clause_number", clause_row[1])
            target_title = clause_updates.get("clause_title", clause_row[2])
            if target_number != clause_row[1] or target_title != clause_row[2]:
                target = conn.execute(
                    """SELECT id FROM standard_graph_clauses
                       WHERE standard_id = ? AND clause_number = ? AND title = ?
                         AND page_start = ? AND page_end = ? AND source_chunk_id = ?
                       LIMIT 1""",
                    (std_id, target_number, target_title, clause_row[4], clause_row[5], clause_row[6]),
                ).fetchone()
                target_clause_id = target[0] if target else uuid.uuid4().hex[:16]
                if not target:
                    conn.execute(
                        """INSERT INTO standard_graph_clauses
                           (id, standard_id, clause_number, title, parent_clause_number,
                            page_start, page_end, source_chunk_id, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (target_clause_id, std_id, target_number, target_title,
                         clause_row[3], clause_row[4], clause_row[5], clause_row[6], now, now),
                    )
                conn.execute(
                    "UPDATE standard_graph_requirements SET clause_id = ?, updated_at = ? "
                    "WHERE id = ? AND standard_id = ?",
                    (target_clause_id, now, requirement_id, std_id),
                )
                conn.execute(
                    "DELETE FROM standard_graph_clauses WHERE id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM standard_graph_requirements WHERE clause_id = ?)",
                    (clause_row[0], clause_row[0]),
                )
        if updates is not None and "parameters" in updates:
            conn.execute("DELETE FROM standard_requirement_parameters WHERE requirement_id = ?", (requirement_id,))
            for parameter in updates.get("parameters") or []:
                conn.execute(
                    """INSERT INTO standard_requirement_parameters
                       (id, requirement_id, name, symbol, comparator, value, value_min, value_max, unit, raw_text)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (uuid.uuid4().hex[:16], requirement_id, str(parameter.get("name") or ""),
                     str(parameter.get("symbol") or ""), str(parameter.get("comparator") or ""),
                     str(parameter.get("value") or ""), str(parameter.get("value_min") or ""),
                     str(parameter.get("value_max") or ""), str(parameter.get("unit") or ""),
                     str(parameter.get("raw_text") or "")),
                )
        updated = {**previous, **clean_updates, **clause_updates, "review_status": review_status,
                   "review_comment": comment, "reviewed_by": reviewer, "reviewed_at": now}
        conn.execute(
            """INSERT INTO standard_requirement_revisions
               (id, requirement_id, standard_id, previous_json, updated_json,
                action, comment, changed_by, changed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex[:16], requirement_id, std_id,
             json.dumps(previous, ensure_ascii=False), json.dumps(updated, ensure_ascii=False),
             "modified_and_confirmed" if clean_updates or (updates and "parameters" in updates) else review_status,
             comment, reviewer, now),
        )
        conn.execute(
            "UPDATE emc_standards SET graph_status = 'pending_review', updated_at = ? WHERE id = ?",
            (now, std_id),
        )
    graph = get_standard_graph(std_id)
    return next((item for item in graph["requirements"] if item["id"] == requirement_id), None)


def publish_standard_graph(std_id: str, reviewer: str) -> dict:
    graph = get_standard_graph(std_id, review_status="confirmed")
    snapshot_requirements = graph["requirements"]
    with _connect() as conn:
        counts = dict(conn.execute(
            "SELECT review_status, COUNT(*) FROM standard_graph_requirements WHERE standard_id = ? GROUP BY review_status",
            (std_id,),
        ).fetchall())
        meta_row = conn.execute("SELECT graph_meta FROM emc_standards WHERE id = ?", (std_id,)).fetchone()
        if not meta_row:
            raise ValueError("标准不存在")
        meta = json.loads(meta_row[0] or "{}")
        if meta.get("failed_chunk_ids") or meta.get("failed_unit_ids"):
            raise ValueError("仍有标准内容未成功提取，请重新提取后再发布")
        if counts.get("pending", 0):
            raise ValueError(f"仍有 {counts['pending']} 条要求待确认")
        if not counts.get("confirmed", 0):
            raise ValueError("至少需要确认一条有效要求")
        missing_interpretations = conn.execute(
            "SELECT COUNT(*) FROM standard_graph_requirements WHERE standard_id = ? "
            "AND review_status = 'confirmed' AND TRIM(interpretation_zh) = ''",
            (std_id,),
        ).fetchone()[0]
        if missing_interpretations:
            raise ValueError(f"仍有 {missing_interpretations} 条有效要求缺少人工确认的中文释义")
        standard_row = conn.execute(
            "SELECT code, version, file_sha256 FROM emc_standards WHERE id = ?", (std_id,)
        ).fetchone()
        release_number = int(conn.execute(
            "SELECT COALESCE(MAX(release_number), 0) + 1 FROM standard_graph_releases WHERE standard_id = ?",
            (std_id,),
        ).fetchone()[0])
        release_id = uuid.uuid4().hex[:16]
        published_at = datetime.now().isoformat()
        snapshot = {
            "schema_version": 1,
            "standard_id": std_id,
            "standard_code": standard_row[0],
            "standard_version": standard_row[1],
            "source_file_sha256": standard_row[2],
            "release_number": release_number,
            "requirements": snapshot_requirements,
        }
        prompt_version = str(meta.get("prompt_version") or meta.get("extractor") or "")
        conn.execute(
            """INSERT INTO standard_graph_releases
               (id, standard_id, release_number, status, source_file_sha256, snapshot_json,
                embedding_model, prompt_version, requirement_count, created_by, created_at,
                published_by, published_at)
               VALUES (?, ?, ?, 'indexing', ?, ?, '', ?, ?, ?, ?, ?, ?)""",
            (release_id, std_id, release_number, standard_row[2],
             json.dumps(snapshot, ensure_ascii=False), prompt_version,
             len(snapshot_requirements), reviewer, published_at, reviewer, published_at),
        )
        meta["published_by"] = reviewer
        meta["published_at"] = published_at
        meta["latest_release_id"] = release_id
        meta["latest_release_number"] = release_number
    try:
        from services.standard_embeddings import index_standard_release
        embedding_model = index_standard_release(release_id)
    except Exception as exc:
        with _connect() as conn:
            conn.execute(
                "UPDATE standard_graph_releases SET status = 'failed' WHERE id = ?", (release_id,)
            )
            conn.execute(
                "UPDATE emc_standards SET graph_status = 'pending_review', graph_error = ?, updated_at = ? WHERE id = ?",
                (f"向量索引失败: {str(exc)[:500]}", datetime.now().isoformat(), std_id),
            )
        logger.error("standard_release_index_failed", standard_id=std_id,
                     release_id=release_id, error=str(exc)[:500])
        raise ValueError(f"标准发布失败，向量知识索引未建立: {exc}") from exc
    with _connect() as conn:
        meta["embedding_model"] = embedding_model
        conn.execute(
            "UPDATE standard_graph_releases SET status = 'published', embedding_model = ? WHERE id = ?",
            (embedding_model, release_id),
        )
        conn.execute(
            "UPDATE emc_standards SET graph_status = 'published', graph_error = '', graph_meta = ?, updated_at = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), meta["published_at"], std_id),
        )
    return {**counts, "release_id": release_id, "release_number": release_number,
            "embedding_model": embedding_model}


def get_standard_release(release_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            """SELECT id, standard_id, release_number, status, source_file_sha256,
                      snapshot_json, embedding_model, prompt_version, requirement_count,
                      published_by, published_at
               FROM standard_graph_releases WHERE id = ?""",
            (release_id,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "standard_id": row[1], "release_number": row[2],
        "status": row[3], "source_file_sha256": row[4],
        "snapshot": json.loads(row[5] or "{}"), "embedding_model": row[6],
        "prompt_version": row[7], "requirement_count": row[8],
        "published_by": row[9], "published_at": row[10],
    }


def get_standard_releases(std_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, standard_id, release_number, status, source_file_sha256,
                      embedding_model, prompt_version, requirement_count, published_by, published_at
               FROM standard_graph_releases WHERE standard_id = ?
               ORDER BY release_number DESC""",
            (std_id,),
        ).fetchall()
    return [{
        "id": row[0], "standard_id": row[1], "release_number": row[2],
        "status": row[3], "source_file_sha256": row[4], "embedding_model": row[5],
        "prompt_version": row[6], "requirement_count": row[7],
        "published_by": row[8], "published_at": row[9],
    } for row in rows]


def replace_standard_release_embeddings(release_id: str, rows: list[dict]) -> int:
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM standard_requirement_embeddings WHERE release_id = ?", (release_id,))
        for row in rows:
            conn.execute(
                """INSERT INTO standard_requirement_embeddings
                   (requirement_id, standard_id, release_id, model_name, dimensions,
                    vector, text_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (row["requirement_id"], row["standard_id"], release_id,
                 row["model_name"], int(row["dimensions"]), row["vector"],
                 row["text_hash"], now),
            )
    return len(rows)


def get_standard_release_embeddings(release_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT requirement_id, model_name, dimensions, vector, text_hash
               FROM standard_requirement_embeddings WHERE release_id = ?""",
            (release_id,),
        ).fetchall()
    return [{"requirement_id": row[0], "model_name": row[1], "dimensions": row[2],
             "vector": row[3], "text_hash": row[4]} for row in rows]


def normalize_standard_mapping_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9\u4e00-\u9fff]+", "", str(value or "").upper())


def save_standard_requirement_mapping(
    std_id: str, release_id: str, source_name: str, mapping_type: str,
    requirement_id: str, scope_type: str, scope_value: str,
    rationale: str, created_by: str,
) -> dict:
    if mapping_type not in {"covered", "not_covered"}:
        raise ValueError("映射结论只能是 covered 或 not_covered")
    if scope_type not in {"standard", "project", "set"}:
        raise ValueError("映射范围只能是 standard、project 或 set")
    if scope_type != "standard" and not scope_value.strip():
        raise ValueError("项目或当前文档集映射必须指定范围")
    normalized = normalize_standard_mapping_name(source_name)
    if not normalized:
        raise ValueError("测试项目名称不能为空")
    release = get_standard_release(release_id)
    if not release or release["standard_id"] != std_id or release["status"] != "published":
        raise ValueError("只能映射到该标准的已发布知识版本")
    requirement_ids = {
        str(item.get("id") or "")
        for item in release["snapshot"].get("requirements") or []
    }
    if mapping_type == "covered" and requirement_id not in requirement_ids:
        raise ValueError("覆盖映射必须选择该知识版本中的有效要求")
    if mapping_type == "not_covered":
        requirement_id = ""
    mapping_id = uuid.uuid4().hex[:16]
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute(
            """DELETE FROM standard_requirement_mappings
               WHERE standard_id = ? AND release_id = ? AND normalized_source_name = ?
                 AND scope_type = ? AND scope_value = ?""",
            (std_id, release_id, normalized, scope_type, scope_value),
        )
        conn.execute(
            """INSERT INTO standard_requirement_mappings
               (id, standard_id, release_id, source_name, normalized_source_name,
                mapping_type, requirement_id, scope_type, scope_value, rationale,
                review_status, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)""",
            (mapping_id, std_id, release_id, source_name.strip(), normalized,
             mapping_type, requirement_id, scope_type, scope_value,
             rationale.strip(), created_by, now),
        )
    logger.info(
        "standard_requirement_mapping_saved", standard_id=std_id,
        release_id=release_id, mapping_id=mapping_id, mapping_type=mapping_type,
        scope_type=scope_type, scope_value=scope_value, user=created_by,
    )
    return {
        "id": mapping_id, "standard_id": std_id, "release_id": release_id,
        "source_name": source_name.strip(), "normalized_source_name": normalized,
        "mapping_type": mapping_type, "requirement_id": requirement_id,
        "scope_type": scope_type, "scope_value": scope_value,
        "rationale": rationale.strip(), "review_status": "confirmed",
        "created_by": created_by, "created_at": now,
    }


def get_standard_requirement_mappings(
    std_id: str, release_id: str = "", source_name: str = "",
    set_id: str = "", project_id: str = "",
) -> list[dict]:
    conditions = ["standard_id = ?", "review_status = 'confirmed'"]
    params: list = [std_id]
    if release_id:
        conditions.append("release_id = ?")
        params.append(release_id)
    if source_name:
        conditions.append("normalized_source_name = ?")
        params.append(normalize_standard_mapping_name(source_name))
    scope_conditions = ["scope_type = 'standard'"]
    if set_id:
        scope_conditions.append("(scope_type = 'set' AND scope_value = ?)")
        params.append(set_id)
    if project_id:
        scope_conditions.append("(scope_type = 'project' AND scope_value = ?)")
        params.append(project_id)
    conditions.append(f"({' OR '.join(scope_conditions)})")
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, standard_id, release_id, source_name, normalized_source_name,
                      mapping_type, requirement_id, scope_type, scope_value, rationale,
                      review_status, created_by, created_at
               FROM standard_requirement_mappings WHERE """ + " AND ".join(conditions) +
            " ORDER BY CASE scope_type WHEN 'set' THEN 1 WHEN 'project' THEN 2 ELSE 3 END, created_at DESC",
            params,
        ).fetchall()
    return [{
        "id": row[0], "standard_id": row[1], "release_id": row[2],
        "source_name": row[3], "normalized_source_name": row[4],
        "mapping_type": row[5], "requirement_id": row[6], "scope_type": row[7],
        "scope_value": row[8], "rationale": row[9], "review_status": row[10],
        "created_by": row[11], "created_at": row[12],
    } for row in rows]


_STANDARD_REFERENCE_RE = re.compile(
    r"(?<![A-Z0-9])((?:ISO|IEC|CISPR|EN|GB(?:/T)?|GBZ|QC/T|SAE|DIN|FCC|Q/[A-Z0-9.-]+)"
    r"\s*[A-Z]*\s*\d+(?:[./:-]\d+)*(?:[-:]\d{4})?)(?![A-Z0-9])",
    re.IGNORECASE,
)


def detect_document_set_standard_references(set_id: str) -> list[dict]:
    """Find explicit standard identifiers in the current four document versions."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT doc_type, filename, plain_text, doc_version
               FROM set_documents
               WHERE set_id = ? AND extraction_status IN ('done', 'partial')
               ORDER BY doc_type, doc_version DESC, upload_order DESC""",
            (set_id,),
        ).fetchall()
    latest: dict[str, tuple] = {}
    for row in rows:
        latest.setdefault(row[0], row)
    references: dict[str, dict] = {}
    for doc_type, filename, plain_text, _version in latest.values():
        text = str(plain_text or "")
        for match in _STANDARD_REFERENCE_RE.finditer(text):
            code = re.sub(r"\s+", " ", match.group(1)).strip()
            normalized = normalize_standard_code(code)
            if len(normalized) < 5:
                continue
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 120)
            item = references.setdefault(normalized, {
                "reference_code": code, "normalized_code": normalized,
                "sources": [], "evidence": [],
            })
            if doc_type not in item["sources"]:
                item["sources"].append(doc_type)
            excerpt = re.sub(r"\s+", " ", text[start:end]).strip()
            if excerpt and excerpt not in item["evidence"] and len(item["evidence"]) < 3:
                item["evidence"].append(excerpt)
    return sorted(references.values(), key=lambda item: item["normalized_code"])


def _standard_reference_matches(reference: str, standard: EmcStandard) -> bool:
    standard_code = normalize_standard_code(standard.code)
    if reference == standard_code:
        return True
    version = normalize_standard_code(standard.version)
    return bool(version and reference in {standard_code + version, standard_code + version[-4:]})


def get_document_set_standard_resolution(set_id: str) -> dict:
    references = detect_document_set_standard_references(set_id)
    selected = get_document_set_standards(set_id)
    with _connect() as conn:
        skip_rows = conn.execute(
            "SELECT normalized_code, reference_code, reason, added_by, created_at "
            "FROM document_set_standard_skips WHERE set_id = ?", (set_id,),
        ).fetchall()
    skips = {row[0]: {"normalized_code": row[0], "reference_code": row[1],
                      "reason": row[2], "added_by": row[3], "created_at": row[4]}
             for row in skip_rows}
    resolved = []
    unresolved = []
    for reference in references:
        matched = next((standard for standard in selected if _standard_reference_matches(
            reference["normalized_code"], standard,
        )), None)
        item = dict(reference)
        if matched:
            item.update({"resolution": "selected", "standard_id": matched.id,
                         "release_id": matched.selected_release_id})
            resolved.append(item)
        elif reference["normalized_code"] in skips:
            item.update({"resolution": "skipped", "skip": skips[reference["normalized_code"]]})
            resolved.append(item)
        else:
            item["resolution"] = "unresolved"
            unresolved.append(item)
    return {"references": references, "resolved": resolved, "unresolved": unresolved,
            "skips": list(skips.values())}


def set_document_set_standard_skips(
    set_id: str, skips: list[dict[str, str]], added_by: str,
) -> list[dict]:
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute("DELETE FROM document_set_standard_skips WHERE set_id = ?", (set_id,))
        for skip in skips:
            code = str(skip.get("reference_code") or "").strip()
            normalized = normalize_standard_code(str(skip.get("normalized_code") or code))
            reason = str(skip.get("reason") or "").strip()
            if not normalized or not reason:
                raise ValueError("跳过引用标准时必须填写标准编号和原因")
            conn.execute(
                """INSERT INTO document_set_standard_skips
                   (set_id, normalized_code, reference_code, reason, added_by, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (set_id, normalized, code, reason, added_by, now),
            )
    return get_document_set_standard_resolution(set_id)["skips"]


def set_document_set_standards(
    set_id: str, standard_ids: list[str], added_by: str = "", source: str = "manual"
) -> list[EmcStandard]:
    unique_ids = list(dict.fromkeys(str(s) for s in standard_ids if s))
    with _connect() as conn:
        if unique_ids:
            placeholders = ",".join("?" for _ in unique_ids)
            rows = conn.execute(
                f"""SELECT s.id, s.knowledge_status,
                            COALESCE((SELECT id FROM standard_graph_releases rel
                                      WHERE rel.standard_id = s.id AND rel.status = 'published'
                                      ORDER BY rel.release_number DESC LIMIT 1), '')
                     FROM emc_standards s WHERE s.id IN ({placeholders})""",
                unique_ids,
            ).fetchall()
            found = {r[0]: (r[1], r[2]) for r in rows}
            missing = [std_id for std_id in unique_ids if std_id not in found]
            unavailable = [std_id for std_id in unique_ids
                           if found.get(std_id, ("", ""))[0] != "ready" or not found.get(std_id, ("", ""))[1]]
            if missing:
                raise ValueError(f"标准不存在: {', '.join(missing)}")
            if unavailable:
                raise ValueError(f"标准尚未完成人工确认并发布: {', '.join(unavailable)}")
        conn.execute("DELETE FROM document_set_standards WHERE set_id = ?", (set_id,))
        now = datetime.now().isoformat()
        for std_id in unique_ids:
            release_id = found[std_id][1]
            conn.execute(
                "INSERT INTO document_set_standards "
                "(set_id, standard_id, source, added_by, created_at, release_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (set_id, std_id, source, added_by, now, release_id),
            )
    logger.info("document_set_standards_updated", set_id=set_id,
                standard_ids=unique_ids, user=added_by)
    return get_document_set_standards(set_id)


def get_document_set_standards(set_id: str) -> list[EmcStandard]:
    with _connect() as conn:
        rows = conn.execute(
            f"{_STANDARD_SELECT} JOIN document_set_standards dss ON dss.standard_id = s.id "
            "WHERE dss.set_id = ? ORDER BY s.code, s.version",
            (set_id,),
        ).fetchall()
        release_rows = conn.execute(
            """SELECT dss.standard_id, dss.release_id, COALESCE(rel.release_number, 0)
               FROM document_set_standards dss
               LEFT JOIN standard_graph_releases rel ON rel.id = dss.release_id
               WHERE dss.set_id = ?""", (set_id,),
        ).fetchall()
    selected = {row[0]: (row[1], row[2]) for row in release_rows}
    standards = []
    for row in rows:
        standard = _row_to_standard(row)
        release_id, release_number = selected.get(standard.id, ("", 0))
        standards.append(standard.model_copy(update={
            "selected_release_id": release_id,
            "selected_release_number": release_number,
        }))
    return standards


def delete_standard(std_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM emc_standards WHERE id = ? AND is_builtin = 0", (std_id,)
        )
    return cur.rowcount > 0


def seed_builtin_standards():
    """Insert built-in EMC standards if emc_standards table is empty."""
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM emc_standards").fetchone()[0]
    if count > 0:
        return

    builtins = [
        {
            "code": "CISPR 25",
            "title": "Vehicles, boats and internal combustion engines - Radio disturbance characteristics",
            "organization": "CISPR",
            "category": "radiated",
            "version": "2021",
            "clauses": [
                StandardClause(clause="6.2.1", title="传导发射限值 — 电压法 (0.15-108 MHz)",
                    description="电源线和信号线的传导发射限值，适用于Class 3/4/5",
                    limit_table=[
                        {"frequency": "0.15-0.3 MHz", "class3_qp": "79 dBμV", "class3_avg": "66 dBμV", "class4_qp": "72 dBμV", "class4_avg": "59 dBμV", "class5_qp": "65 dBμV", "class5_avg": "52 dBμV"},
                        {"frequency": "0.3-0.53 MHz", "class3_qp": "73 dBμV", "class3_avg": "60 dBμV", "class4_qp": "66 dBμV", "class4_avg": "53 dBμV", "class5_qp": "59 dBμV", "class5_avg": "46 dBμV"},
                        {"frequency": "0.53-5 MHz", "class3_qp": "73 dBμV", "class3_avg": "60 dBμV", "class4_qp": "66 dBμV", "class4_avg": "53 dBμV", "class5_qp": "59 dBμV", "class5_avg": "46 dBμV"},
                        {"frequency": "5-30 MHz", "class3_qp": "73 dBμV", "class3_avg": "60 dBμV", "class4_qp": "66 dBμV", "class4_avg": "53 dBμV", "class5_qp": "59 dBμV", "class5_avg": "46 dBμV"},
                        {"frequency": "30-108 MHz", "class3_qp": "53 dBμV", "class3_avg": "40 dBμV", "class4_qp": "46 dBμV", "class4_avg": "33 dBμV", "class5_qp": "39 dBμV", "class5_avg": "26 dBμV"},
                    ]),
                StandardClause(clause="6.2.2", title="传导发射限值 — 电流探头法 (0.15-245 MHz)",
                    description="使用电流探头测量线束上的传导发射",
                    limit_table=[
                        {"frequency": "0.15-5 MHz", "class3": "57 dBμA", "class4": "50 dBμA", "class5": "43 dBμA"},
                        {"frequency": "5-30 MHz", "class3": "57 dBμA", "class4": "50 dBμA", "class5": "43 dBμA"},
                        {"frequency": "30-54 MHz", "class3": "47 dBμA", "class4": "40 dBμA", "class5": "33 dBμA"},
                        {"frequency": "70-108 MHz", "class3": "37 dBμA", "class4": "30 dBμA", "class5": "23 dBμA"},
                        {"frequency": "108-245 MHz", "class3": "27 dBμA", "class4": "20 dBμA", "class5": "13 dBμA"},
                    ]),
                StandardClause(clause="6.3", title="辐射发射限值 — ALSE法 (0.15-960 MHz)",
                    description="在吸波屏蔽室(ALSE)中测量的电场辐射发射限值",
                    limit_table=[
                        {"frequency": "0.15-30 MHz", "class3": "54 dBμV/m", "class4": "44 dBμV/m", "class5": "34 dBμV/m"},
                        {"frequency": "30-200 MHz", "class3": "54 dBμV/m", "class4": "44 dBμV/m", "class5": "34 dBμV/m"},
                        {"frequency": "200-960 MHz", "class3": "64 dBμV/m", "class4": "54 dBμV/m", "class5": "44 dBμV/m"},
                    ]),
            ],
        },
        {
            "code": "GB/T 18655",
            "title": "车辆、船和内燃机 无线电骚扰特性 用于保护车载接收机的限值和测量方法",
            "organization": "GB",
            "category": "radiated",
            "version": "2018",
            "clauses": [
                StandardClause(clause="6.2.1", title="传导发射限值 — 电压法",
                    description="对应CISPR 25 Class 3/4/5限值，增加了Class 1和Class 2等级",
                    limit_table=[
                        {"frequency": "0.15-0.3 MHz", "class1_qp": "89 dBμV", "class1_avg": "76 dBμV", "class3_qp": "79 dBμV", "class3_avg": "66 dBμV", "class5_qp": "65 dBμV", "class5_avg": "52 dBμV"},
                        {"frequency": "0.53-5 MHz", "class1_qp": "83 dBμV", "class1_avg": "70 dBμV", "class3_qp": "73 dBμV", "class3_avg": "60 dBμV", "class5_qp": "59 dBμV", "class5_avg": "46 dBμV"},
                        {"frequency": "5-30 MHz", "class1_qp": "83 dBμV", "class1_avg": "70 dBμV", "class3_qp": "73 dBμV", "class3_avg": "60 dBμV", "class5_qp": "59 dBμV", "class5_avg": "46 dBμV"},
                        {"frequency": "30-108 MHz", "class1_qp": "63 dBμV", "class1_avg": "50 dBμV", "class3_qp": "53 dBμV", "class3_avg": "40 dBμV", "class5_qp": "39 dBμV", "class5_avg": "26 dBμV"},
                    ]),
            ],
        },
        {
            "code": "EN 55032",
            "title": "Electromagnetic compatibility of multimedia equipment - Emission requirements",
            "organization": "EN",
            "category": "radiated",
            "version": "2015+A11:2020",
            "clauses": [
                StandardClause(clause="Table A.2", title="传导发射限值 — 电源端口 (0.15-30 MHz)",
                    description="Class A/B 设备的电源端口传导发射限值",
                    limit_table=[
                        {"frequency": "0.15-0.5 MHz", "classA_qp": "79 dBμV", "classA_avg": "66 dBμV", "classB_qp": "66-56 dBμV", "classB_avg": "56-46 dBμV"},
                        {"frequency": "0.5-5 MHz", "classA_qp": "73 dBμV", "classA_avg": "60 dBμV", "classB_qp": "56 dBμV", "classB_avg": "46 dBμV"},
                        {"frequency": "5-30 MHz", "classA_qp": "73 dBμV", "classA_avg": "60 dBμV", "classB_qp": "60 dBμV", "classB_avg": "50 dBμV"},
                    ]),
                StandardClause(clause="Table A.4", title="辐射发射限值 — 3m距离 (30-6000 MHz)",
                    description="Class A/B 设备在3m测量距离的电场辐射限值",
                    limit_table=[
                        {"frequency": "30-230 MHz", "classA_3m": "50 dBμV/m", "classB_3m": "40 dBμV/m"},
                        {"frequency": "230-1000 MHz", "classA_3m": "57 dBμV/m", "classB_3m": "47 dBμV/m"},
                        {"frequency": "1-3 GHz", "classA_3m": "56 dBμV/m (AVG)", "classB_3m": "50 dBμV/m (AVG)"},
                        {"frequency": "3-6 GHz", "classA_3m": "60 dBμV/m (AVG)", "classB_3m": "54 dBμV/m (AVG)"},
                    ]),
            ],
        },
        {
            "code": "FCC Part 15",
            "title": "Radio Frequency Devices — Subpart B: Unintentional Radiators",
            "organization": "FCC",
            "category": "radiated",
            "version": "2023",
            "clauses": [
                StandardClause(clause="§15.107(a)", title="传导发射限值 — Class A/B (0.15-30 MHz)",
                    description="AC电源线传导发射限值，使用ANSI C63.4测量",
                    limit_table=[
                        {"frequency": "0.15-0.5 MHz", "classA_qp": "79 dBμV", "classA_avg": "66 dBμV", "classB_qp": "66-56 dBμV", "classB_avg": "56-46 dBμV"},
                        {"frequency": "0.5-30 MHz", "classA_qp": "73 dBμV", "classA_avg": "60 dBμV", "classB_qp": "56 dBμV", "classB_avg": "46 dBμV"},
                    ]),
                StandardClause(clause="§15.109(a)", title="辐射发射限值 — Class A (30-40000 MHz)",
                    description="Class A设备10m距离辐射发射限值",
                    limit_table=[
                        {"frequency": "30-88 MHz", "classA_10m": "39.1 dBμV/m", "classA_3m": "49.5 dBμV/m"},
                        {"frequency": "88-216 MHz", "classA_10m": "43.5 dBμV/m", "classA_3m": "54 dBμV/m"},
                        {"frequency": "216-960 MHz", "classA_10m": "46.4 dBμV/m", "classA_3m": "56.9 dBμV/m"},
                        {"frequency": ">960 MHz", "classA_10m": "49.5 dBμV/m", "classA_3m": "60 dBμV/m"},
                    ]),
                StandardClause(clause="§15.109(b)", title="辐射发射限值 — Class B (30-40000 MHz)",
                    description="Class B设备3m距离辐射发射限值",
                    limit_table=[
                        {"frequency": "30-88 MHz", "classB_3m": "40 dBμV/m"},
                        {"frequency": "88-216 MHz", "classB_3m": "43.5 dBμV/m"},
                        {"frequency": "216-960 MHz", "classB_3m": "46 dBμV/m"},
                        {"frequency": ">960 MHz", "classB_3m": "54 dBμV/m"},
                    ]),
            ],
        },
        {
            "code": "CISPR 11",
            "title": "Industrial, scientific and medical equipment - Radio-frequency disturbance characteristics",
            "organization": "CISPR",
            "category": "radiated",
            "version": "2015+A1:2019",
            "clauses": [
                StandardClause(clause="Table 6", title="传导发射限值 — 电源端口 (0.15-30 MHz)",
                    description="Group 1/2, Class A/B 设备的电源端口限值",
                    limit_table=[
                        {"frequency": "0.15-0.5 MHz", "group1A_qp": "79 dBμV", "group1A_avg": "66 dBμV", "group1B_qp": "66-56 dBμV", "group1B_avg": "56-46 dBμV", "group2A_qp": "100 dBμV", "group2A_avg": "90 dBμV"},
                        {"frequency": "0.5-5 MHz", "group1A_qp": "73 dBμV", "group1A_avg": "60 dBμV", "group1B_qp": "56 dBμV", "group1B_avg": "46 dBμV", "group2A_qp": "86 dBμV", "group2A_avg": "76 dBμV"},
                        {"frequency": "5-30 MHz", "group1A_qp": "73 dBμV", "group1A_avg": "60 dBμV", "group1B_qp": "60 dBμV", "group1B_avg": "50 dBμV", "group2A_qp": "86-73 dBμV", "group2A_avg": "76-60 dBμV"},
                    ]),
            ],
        },
        {
            "code": "GB 9254",
            "title": "信息技术设备、多媒体设备和接收机 电磁兼容 限值和测量方法",
            "organization": "GB",
            "category": "radiated",
            "version": "2021",
            "clauses": [
                StandardClause(clause="表A.2", title="传导发射限值 — 电源端口",
                    description="对应EN 55032 Class A/B传导发射限值",
                    limit_table=[
                        {"frequency": "0.15-0.5 MHz", "classA_qp": "79 dBμV", "classA_avg": "66 dBμV", "classB_qp": "66-56 dBμV", "classB_avg": "56-46 dBμV"},
                        {"frequency": "0.5-30 MHz", "classA_qp": "73 dBμV", "classA_avg": "60 dBμV", "classB_qp": "56 dBμV", "classB_avg": "46 dBμV"},
                    ]),
                StandardClause(clause="表A.4", title="辐射发射限值 — 3m距离",
                    description="对应EN 55032 Class A/B辐射发射限值",
                    limit_table=[
                        {"frequency": "30-230 MHz", "classA_3m": "50 dBμV/m", "classB_3m": "40 dBμV/m"},
                        {"frequency": "230-1000 MHz", "classA_3m": "57 dBμV/m", "classB_3m": "47 dBμV/m"},
                        {"frequency": "1-3 GHz", "classA_3m": "56 dBμV/m", "classB_3m": "50 dBμV/m"},
                        {"frequency": "3-6 GHz", "classA_3m": "60 dBμV/m", "classB_3m": "54 dBμV/m"},
                    ]),
            ],
        },
        {
            "code": "IEC 61000-4",
            "title": "Electromagnetic compatibility (EMC) — Part 4: Testing and measurement techniques",
            "organization": "IEC",
            "category": "immunity",
            "version": "2012-2021",
            "clauses": [
                StandardClause(clause="IEC 61000-4-2", title="静电放电(ESD)抗扰度测试等级",
                    description="接触放电和空气放电的测试等级",
                    limit_table=[
                        {"level": "1", "contact_kv": "±2 kV", "air_kv": "±2 kV"},
                        {"level": "2", "contact_kv": "±4 kV", "air_kv": "±4 kV"},
                        {"level": "3", "contact_kv": "±6 kV", "air_kv": "±8 kV"},
                        {"level": "4", "contact_kv": "±8 kV", "air_kv": "±15 kV"},
                        {"level": "X", "contact_kv": "special", "air_kv": "special"},
                    ]),
                StandardClause(clause="IEC 61000-4-4", title="电快速瞬变脉冲群(EFT/B)抗扰度测试等级",
                    description="电源端口和信号端口的EFT/B测试等级",
                    limit_table=[
                        {"level": "1", "power_kv": "±0.5 kV", "signal_kv": "±0.25 kV", "repetition": "5 kHz"},
                        {"level": "2", "power_kv": "±1 kV", "signal_kv": "±0.5 kV", "repetition": "5 kHz"},
                        {"level": "3", "power_kv": "±2 kV", "signal_kv": "±1 kV", "repetition": "5 kHz"},
                        {"level": "4", "power_kv": "±4 kV", "signal_kv": "±2 kV", "repetition": "2.5 kHz"},
                    ]),
                StandardClause(clause="IEC 61000-4-5", title="浪涌(Surge)抗扰度测试等级",
                    description="电源线和长信号线的浪涌测试等级",
                    limit_table=[
                        {"level": "1", "line_to_line": "±0.5 kV", "line_to_ground": "±0.5 kV"},
                        {"level": "2", "line_to_line": "±1 kV", "line_to_ground": "±1 kV"},
                        {"level": "3", "line_to_line": "±1 kV", "line_to_ground": "±2 kV"},
                        {"level": "4", "line_to_line": "±2 kV", "line_to_ground": "±4 kV"},
                    ]),
            ],
        },
    ]

    for b in builtins:
        save_standard(
            code=b["code"], title=b["title"], organization=b["organization"],
            category=b["category"], version=b["version"], clauses=b["clauses"],
            is_builtin=True,
        )


# ── User management ─────────────────────────────────────────────────

def seed_admin():
    """Create default admin account GDJL25631 if users table is empty."""
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    if count == 0:
        create_user("GDJL25631", "admin")


def create_user(employee_id: str, role: str = "viewer", name: str = "") -> bool:
    if role not in ("admin", "reviewer", "standard_reviewer", "viewer"):
        raise ValueError(f"无效的用户角色: {role}")
    now = datetime.now().isoformat()
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO users (employee_id, role, name, created_at) VALUES (?, ?, ?, ?)",
                (employee_id, role, name, now),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_user(employee_id: str) -> User | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT employee_id, role, name, created_at FROM users WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
    if row is None:
        return None
    return User(employee_id=row[0], role=row[1], name=row[2] if len(row) > 2 else "", created_at=row[3] if len(row) > 3 else "")


def get_all_users() -> list[User]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT employee_id, role, name, created_at FROM users ORDER BY created_at"
        ).fetchall()
    return [User(employee_id=r[0], role=r[1], name=r[2] if len(r) > 2 else "", created_at=r[3] if len(r) > 3 else "") for r in rows]


def update_user_role(employee_id: str, role: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET role = ? WHERE employee_id = ?", (role, employee_id)
        )
    return cur.rowcount > 0


# ── System settings ──────────────────────────────────────────────────

_DEFAULT_SETTINGS: dict[str, str] = {
    "jwt_expire_hours": "168",
    "max_upload_bytes": "104857600",      # 100 MB
    "rate_max_upload": "5",
    "rate_max_general": "60",
    "rate_window_sec": "60",
    "batch_max_files": "10",
    "deepseek_base_url": "https://api.deepseek.com",
}


def _with_default(key: str, value: str | None) -> str:
    """Return value if non-None and non-empty, else the hardcoded default."""
    if value:
        return value
    return _DEFAULT_SETTINGS.get(key, "")


def seed_default_settings():
    """Insert default settings on first startup (does not overwrite existing)."""
    now = datetime.now().isoformat()
    with _connect() as conn:
        for key, value in _DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )


def get_all_settings() -> dict[str, str]:
    with _connect() as conn:
        rows = conn.execute("SELECT key, value FROM system_settings").fetchall()
    result: dict[str, str] = {}
    for key, value in rows:
        result[key] = value
    for key, default in _DEFAULT_SETTINGS.items():
        if key not in result:
            result[key] = default
    return result


def get_setting(key: str) -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM system_settings WHERE key = ?", (key,)
        ).fetchone()
    return _with_default(key, row[0] if row else None)


def get_setting_int(key: str) -> int:
    try:
        return int(get_setting(key))
    except (ValueError, TypeError):
        return int(_DEFAULT_SETTINGS.get(key, "0"))


def set_setting(key: str, value: str) -> None:
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, now),
        )


def set_settings_batch(updates: dict[str, str]) -> None:
    now = datetime.now().isoformat()
    with _connect() as conn:
        for key, value in updates.items():
            conn.execute(
                "INSERT OR REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now),
            )



def create_project_group(name: str, description: str, created_by: str, member_ids: list[str], category: str = "其他") -> str:
    gid = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute("INSERT INTO project_groups (id, name, description, created_by, created_at, category) VALUES (?,?,?,?,?,?)",
                     (gid, name, description, created_by, now, category))
        for mid in member_ids:
            conn.execute("INSERT OR IGNORE INTO project_group_members (group_id, employee_id) VALUES (?,?)", (gid, mid))
        conn.execute("INSERT OR IGNORE INTO project_group_members (group_id, employee_id) VALUES (?,?)", (gid, created_by))
    return gid


def update_project_group(gid: str, name: str | None = None, description: str | None = None, member_ids: list[str] | None = None, category: str | None = None) -> bool:
    with _connect() as conn:
        if not conn.execute("SELECT id FROM project_groups WHERE id=?", (gid,)).fetchone():
            return False
        if name is not None:
            conn.execute("UPDATE project_groups SET name=? WHERE id=?", (name, gid))
        if description is not None:
            conn.execute("UPDATE project_groups SET description=? WHERE id=?", (description, gid))
        if category is not None:
            conn.execute("UPDATE project_groups SET category=? WHERE id=?", (category, gid))
        if member_ids is not None:
            creator_row = conn.execute(
                "SELECT created_by FROM project_groups WHERE id = ?", (gid,),
            ).fetchone()
            conn.execute("DELETE FROM project_group_members WHERE group_id=?", (gid,))
            for mid in member_ids:
                conn.execute("INSERT OR IGNORE INTO project_group_members (group_id, employee_id) VALUES (?,?)", (gid, mid))
            if creator_row:
                conn.execute(
                    "INSERT OR IGNORE INTO project_group_members (group_id, employee_id) VALUES (?,?)",
                    (gid, creator_row[0]),
                )
        return True


def delete_project_group(gid: str) -> bool:
    with _connect() as conn:
        conn.execute("UPDATE document_sets SET project_group_id = '' WHERE project_group_id = ?", (gid,))
        conn.execute(
            "DELETE FROM standard_requirement_mappings WHERE scope_type = 'project' AND scope_value = ?",
            (gid,),
        )
        conn.execute("DELETE FROM project_group_members WHERE group_id=?", (gid,))
        cur = conn.execute("DELETE FROM project_groups WHERE id=?", (gid,))
        return cur.rowcount > 0


def get_project_groups(employee_id: str = "") -> list[dict]:
    with _connect() as conn:
        if employee_id:
            rows = conn.execute(
                """SELECT pg.id, pg.name, pg.description, pg.created_by, pg.created_at, pg.category
                   FROM project_groups pg
                   JOIN project_group_members pgm ON pgm.group_id = pg.id
                   WHERE pgm.employee_id = ? ORDER BY pg.created_at DESC""",
                (employee_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT id, name, description, created_by, created_at, category FROM project_groups ORDER BY created_at DESC").fetchall()
    # Fetch all members in a single query to avoid N+1 per-group queries.
    group_ids = [r[0] for r in rows]
    members_by_group: dict[str, list[str]] = {gid: [] for gid in group_ids}
    if group_ids:
        placeholders = ",".join("?" for _ in group_ids)
        with _connect() as conn:
            mrows = conn.execute(
                f"SELECT group_id, employee_id FROM project_group_members WHERE group_id IN ({placeholders})",
                group_ids,
            ).fetchall()
        for gid, mid in mrows:
            members_by_group[gid].append(mid)
    result = []
    for r in rows:
        result.append({
            "id": r[0], "name": r[1], "description": r[2],
            "created_by": r[3], "created_at": r[4],
            "category": r[5] if len(r) > 5 else "其他",
            "members": members_by_group.get(r[0], []),
        })
    return result


def get_project_group(gid: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT id, name, description, created_by, created_at, category FROM project_groups WHERE id=?", (gid,)).fetchone()
    if not row:
        return None
    members = []
    with _connect() as conn:
        mrows = conn.execute("SELECT employee_id FROM project_group_members WHERE group_id=?", (gid,)).fetchall()
        members = [m[0] for m in mrows]
    return {"id": row[0], "name": row[1], "description": row[2], "created_by": row[3], "created_at": row[4], "category": row[5] if len(row) > 5 else "其他", "members": members}


# ── Tags CRUD ───────────────────────────────────────────────────────

def create_tag(name: str, created_by: str) -> str:
    tag_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO tags (id, name, created_by, created_at) VALUES (?, ?, ?, ?)",
            (tag_id, name.strip(), created_by, now),
        )
    return tag_id


def get_tags() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, created_by, created_at FROM tags ORDER BY name"
        ).fetchall()
    return [{"id": r[0], "name": r[1], "created_by": r[2], "created_at": r[3]} for r in rows]


def update_tag(tag_id: str, name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE tags SET name = ? WHERE id = ?", (name.strip(), tag_id))
    return cur.rowcount > 0


def delete_tag(tag_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT name FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if not row:
            return False
        tag_name = row[0]
        conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        conn.execute("UPDATE project_groups SET category = '其他' WHERE category = ?", (tag_name,))
    return True


# ── Async wrappers (non-blocking, use in async endpoints) ────────────


async def get_audit_logs_async(limit: int = 100, offset: int = 0,
                               action: str = "", ip: str = "",
                               employee_id: str = "", filename: str = "",
                               date_from: str = "",
                               date_to: str = "") -> tuple[list[AuditLogEntry], int]:
    return await _run_async(get_audit_logs, limit, offset, action, ip,
                            employee_id, filename, date_from, date_to)



async def save_audit_log_async(client_ip: str, action: str, filename: str = "",
                                file_size_kb: int = 0, overall_result: str = "",
                                issue_count: int = 0, detail: str = "",
                                duration_ms: int = 0, employee_id: str = "",
                                req_id: str = ""):
    await _run_async(save_audit_log, client_ip, action, filename,
                     file_size_kb, overall_result, issue_count, detail, duration_ms, employee_id, req_id)


# ── Rules async wrappers ─────────────────────────────────────────────


async def save_standard_async(code: str, title: str, organization: str = "",
                              category: str = "", version: str = "",
                              clauses: list | None = None,
                              is_builtin: bool = False) -> str:
    return await _run_async(save_standard, code, title, organization, category,
                            version, clauses, is_builtin)


async def get_standards_async(organization: str = "", category: str = "",
                               keyword: str = "") -> list[EmcStandard]:
    return await _run_async(get_standards, organization, category, keyword)


async def get_standard_async(std_id: str) -> EmcStandard | None:
    return await _run_async(get_standard, std_id)


async def delete_standard_async(std_id: str) -> bool:
    return await _run_async(delete_standard, std_id)


async def create_standard_asset_async(*args, **kwargs) -> tuple[str, bool]:
    return await _run_async(create_standard_asset, *args, **kwargs)


async def get_standard_file_async(std_id: str) -> tuple[str, bytes] | None:
    return await _run_async(get_standard_file, std_id)


async def get_stuck_standards_async() -> list[dict]:
    return await _run_async(get_stuck_standards)


async def get_stuck_standard_graphs_async() -> list[str]:
    return await _run_async(get_stuck_standard_graphs)


async def update_standard_knowledge_async(std_id: str, **kwargs) -> bool:
    return await _run_async(update_standard_knowledge, std_id, **kwargs)


async def replace_standard_knowledge_chunks_async(std_id: str, chunks: list[dict]) -> int:
    return await _run_async(replace_standard_knowledge_chunks, std_id, chunks)


async def get_standard_knowledge_chunks_async(
    std_id: str, query: str = "", limit: int = 12,
) -> list[dict]:
    return await _run_async(get_standard_knowledge_chunks, std_id, query, limit)


async def update_standard_graph_status_async(std_id: str, status: str, error: str = "", meta: dict | None = None) -> bool:
    return await _run_async(update_standard_graph_status, std_id, status, error, meta)


async def replace_standard_graph_async(std_id: str, clauses: list[dict], meta: dict) -> dict:
    return await _run_async(replace_standard_graph, std_id, clauses, meta)


async def append_standard_graph_async(std_id: str, clauses: list[dict], meta: dict) -> dict:
    return await _run_async(append_standard_graph, std_id, clauses, meta)


async def get_standard_graph_async(std_id: str, review_status: str = "") -> dict:
    return await _run_async(get_standard_graph, std_id, review_status)


async def review_standard_requirement_async(
    std_id: str, requirement_id: str, review_status: str, comment: str,
    reviewer: str, updates: dict | None = None,
) -> dict | None:
    return await _run_async(
        review_standard_requirement, std_id, requirement_id, review_status,
        comment, reviewer, updates,
    )


async def publish_standard_graph_async(std_id: str, reviewer: str) -> dict:
    # First BGE-M3 model download can exceed the normal short DB-operation timeout.
    return await asyncio.to_thread(publish_standard_graph, std_id, reviewer)


async def get_standard_release_async(release_id: str) -> dict | None:
    return await _run_async(get_standard_release, release_id)


async def get_standard_releases_async(std_id: str) -> list[dict]:
    return await _run_async(get_standard_releases, std_id)


async def save_standard_requirement_mapping_async(*args, **kwargs) -> dict:
    return await _run_async(save_standard_requirement_mapping, *args, **kwargs)


async def get_standard_requirement_mappings_async(*args, **kwargs) -> list[dict]:
    return await _run_async(get_standard_requirement_mappings, *args, **kwargs)


async def get_document_set_standard_resolution_async(set_id: str) -> dict:
    return await _run_async(get_document_set_standard_resolution, set_id)


async def set_document_set_standard_skips_async(
    set_id: str, skips: list[dict[str, str]], added_by: str,
) -> list[dict]:
    return await _run_async(set_document_set_standard_skips, set_id, skips, added_by)


async def set_document_set_standards_async(
    set_id: str, standard_ids: list[str], added_by: str = "", source: str = "manual",
) -> list[EmcStandard]:
    return await _run_async(
        set_document_set_standards, set_id, standard_ids, added_by, source,
    )


async def get_document_set_standards_async(set_id: str) -> list[EmcStandard]:
    return await _run_async(get_document_set_standards, set_id)


async def seed_builtin_standards_async():
    await _run_async(seed_builtin_standards)



async def seed_admin_async():
    await _run_async(seed_admin)


async def create_user_async(employee_id: str, role: str = "viewer", name: str = "") -> bool:
    return await _run_async(create_user, employee_id, role, name)


async def get_user_async(employee_id: str) -> User | None:
    return await _run_async(get_user, employee_id)


async def get_all_users_async() -> list[User]:
    return await _run_async(get_all_users)


async def update_user_role_async(employee_id: str, role: str) -> bool:
    return await _run_async(update_user_role, employee_id, role)


def update_user_name(employee_id: str, name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("UPDATE users SET name = ? WHERE employee_id = ?", (name, employee_id))
    return cur.rowcount > 0


async def update_user_name_async(employee_id: str, name: str) -> bool:
    return await _run_async(update_user_name, employee_id, name)


def delete_user(employee_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE employee_id = ?", (employee_id,))
    return cur.rowcount > 0


async def delete_user_async(employee_id: str) -> bool:
    return await _run_async(delete_user, employee_id)


# ── Settings async wrappers ───────────────────────────────────────────

async def seed_default_settings_async():
    await _run_async(seed_default_settings)


async def get_all_settings_async() -> dict[str, str]:
    return await _run_async(get_all_settings)



async def get_setting_async(key: str) -> str:
    return await _run_async(get_setting, key)


async def get_setting_int_async(key: str) -> int:
    return await _run_async(get_setting_int, key)


async def set_setting_async(key: str, value: str):
    await _run_async(set_setting, key, value)


async def set_settings_batch_async(updates: dict[str, str]):
    await _run_async(set_settings_batch, updates)


# ── Project Groups async wrappers ───────────────────────────────────

async def create_project_group_async(name: str, description: str, created_by: str, member_ids: list[str], category: str = "其他") -> str:
    return await _run_async(create_project_group, name, description, created_by, member_ids, category)

async def update_project_group_async(gid: str, name: str | None = None, description: str | None = None, member_ids: list[str] | None = None, category: str | None = None) -> bool:
    return await _run_async(update_project_group, gid, name, description, member_ids, category)

async def delete_project_group_async(gid: str) -> bool:
    return await _run_async(delete_project_group, gid)

async def get_project_groups_async(employee_id: str = "") -> list[dict]:
    return await _run_async(get_project_groups, employee_id)

async def get_project_group_async(gid: str) -> dict | None:
    return await _run_async(get_project_group, gid)


# ── Tags async wrappers ──────────────────────────────────────────────

async def create_tag_async(name: str, created_by: str) -> str:
    return await _run_async(create_tag, name, created_by)


async def get_tags_async() -> list[dict]:
    return await _run_async(get_tags)


async def update_tag_async(tag_id: str, name: str) -> bool:
    return await _run_async(update_tag, tag_id, name)


async def delete_tag_async(tag_id: str) -> bool:
    return await _run_async(delete_tag, tag_id)



def generate_set_id(employee_id: str) -> str:
    import random, string
    date_part = datetime.now().strftime("%Y%m%d")
    suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"EMC-{date_part}-{suffix}"


def create_document_set(employee_id: str) -> str:
    now = datetime.now().isoformat()
    for attempt in range(3):
        set_id = generate_set_id(employee_id)
        try:
            with _connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO document_sets (set_id, status, created_at, employee_id) VALUES (?, ?, ?, ?)",
                    (set_id, "incomplete", now, employee_id),
                )
            return set_id
        except sqlite3.IntegrityError:
            continue
    raise RuntimeError("无法创建文档集: ID 冲突 3 次")


def add_document_to_set(
    set_id: str, doc_type: str, filename: str, file_size_kb: int = 0,
    replace_doc_id: str = "", plain_text: str = "", html_content: str = "",
) -> str:
    """Add a document to a set. If replace_doc_id is set, creates a new version."""
    import random, string
    doc_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
    now = datetime.now().isoformat()
    doc_version = 1
    parent_doc_id = ""
    upload_order = 0

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        set_row = conn.execute(
            "SELECT status FROM document_sets WHERE set_id = ?", (set_id,),
        ).fetchone()
        if not set_row:
            raise ValueError("文档集不存在")
        if set_row[0] not in {"incomplete", "revision"}:
            raise ValueError("文档集已锁定；请先创建修订版本再修改资料")
        # Determine version and parent
        if replace_doc_id:
            existing = conn.execute(
                "SELECT doc_version, doc_type FROM set_documents WHERE doc_id = ? AND set_id = ?",
                (replace_doc_id, set_id),
            ).fetchone()
            if not existing:
                raise ValueError("被替换文档不属于当前文档集")
            doc_version = existing[0] + 1
            parent_doc_id = replace_doc_id
            if existing[1]:
                doc_type = existing[1]

        # Get next upload_order for this set (serialized by BEGIN IMMEDIATE)
        max_order = conn.execute(
            "SELECT COALESCE(MAX(upload_order), -1) FROM set_documents WHERE set_id = ? AND parent_doc_id = ''",
            (set_id,),
        ).fetchone()[0]
        upload_order = max_order + 1

        conn.execute(
            """INSERT INTO set_documents
               (doc_id, set_id, doc_type, filename, file_size_kb,
                doc_version, parent_doc_id, upload_order,
                plain_text, html_content, extraction_status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (doc_id, set_id, doc_type, filename, file_size_kb,
             doc_version, parent_doc_id, upload_order,
             plain_text, html_content, now),
        )
    logger.info("document_added", set_id=set_id, doc_type=doc_type, filename=filename)
    return doc_id


def store_file_content(doc_id: str, file_bytes: bytes) -> bool:
    """Store the original file bytes for a document."""
    with _connect() as conn:
        conn.execute(
            "UPDATE set_documents SET file_content = ? WHERE doc_id = ?",
            (file_bytes, doc_id),
        )
        if conn.total_changes == 0:
            logger.warning("store_file_content_no_match", doc_id=doc_id)
            return False
        return True


async def store_file_content_async(doc_id: str, file_bytes: bytes) -> bool:
    return await _run_async(store_file_content, doc_id, file_bytes)


def get_file_content(doc_id: str) -> bytes | None:
    """Retrieve the original file bytes for a document."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT file_content FROM set_documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if not row or row[0] is None:
            return None
        return bytes(row[0])


async def get_file_content_async(doc_id: str) -> bytes | None:
    return await _run_async(get_file_content, doc_id)


def replace_document_archive_members(doc_id: str, members: list[dict]) -> None:
    """Replace one document version's ZIP manifest atomically."""
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM set_documents WHERE doc_id = ?", (doc_id,),
        ).fetchone():
            raise ValueError("文档不存在")
        conn.execute("DELETE FROM document_archive_members WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            """INSERT INTO document_archive_members
               (doc_id, member_index, raw_filename, display_filename,
                content_hash, file_size, media_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [(
                doc_id,
                int(item.get("member_index") or 0),
                str(item.get("raw_filename") or ""),
                str(item.get("display_filename") or ""),
                str(item.get("content_hash") or ""),
                int(item.get("file_size") or 0),
                str(item.get("media_type") or ""),
                now,
            ) for item in members],
        )


def get_document_archive_members(doc_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT member_index, raw_filename, display_filename,
                      content_hash, file_size, media_type
               FROM document_archive_members
               WHERE doc_id = ? ORDER BY member_index""",
            (doc_id,),
        ).fetchall()
    return [{
        "member_index": int(row[0]),
        "raw_filename": row[1],
        "display_filename": row[2],
        "content_hash": row[3],
        "file_size": int(row[4]),
        "media_type": row[5],
    } for row in rows]


async def replace_document_archive_members_async(doc_id: str, members: list[dict]) -> None:
    await _run_async(replace_document_archive_members, doc_id, members)


async def get_document_archive_members_async(doc_id: str) -> list[dict]:
    return await _run_async(get_document_archive_members, doc_id)


def get_stuck_documents() -> list[dict]:
    """Return documents whose extraction never completed — stuck in 'pending' or 'extracting'."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT doc_id, set_id, doc_type, filename, extraction_status
               FROM set_documents
               WHERE extraction_status IN ('pending', 'extracting')
               ORDER BY created_at ASC"""
        ).fetchall()
        return [
            {"doc_id": r[0], "set_id": r[1], "doc_type": r[2],
             "filename": r[3], "extraction_status": r[4]}
            for r in rows
        ]


async def get_stuck_documents_async() -> list[dict]:
    return await _run_async(get_stuck_documents)


def _row_to_doc(row) -> dict:
    """Convert a set_documents row to a dict matching SetDocument model."""
    extraction_meta: dict = {}
    if len(row) > 15 and row[15]:
        try:
            extraction_meta = json.loads(row[15])
        except Exception:
            extraction_meta = {}
    return {
        "doc_id": row[0],
        "set_id": row[1],
        "doc_type": row[2],
        "filename": row[3],
        "file_size_kb": row[4],
        "doc_version": row[5],
        "parent_doc_id": row[6],
        "upload_order": row[7],
        "plain_text": row[8],
        "html_content": row[9],
        "extraction_status": row[10],
        "created_at": row[11] if len(row) > 11 else "",
        "extraction_error": row[12] if len(row) > 12 else "",
        "extraction_quality": row[14] if len(row) > 14 else row[10],
        "extraction_meta": extraction_meta,
        "reviewed_by": row[16] if len(row) > 16 else "",
        "reviewed_at": row[17] if len(row) > 17 else "",
    }


def get_document_set(set_id: str) -> dict | None:
    """Get a document set with all its documents."""
    with _connect() as conn:
        set_row = conn.execute(
            """SELECT ds.set_id, ds.status, ds.created_at, ds.employee_id,
                      ds.project_group_id, COALESCE(pg.name, '')
               FROM document_sets ds
               LEFT JOIN project_groups pg ON pg.id = ds.project_group_id
               WHERE ds.set_id = ?""",
            (set_id,),
        ).fetchone()
        if not set_row:
            return None
        doc_rows = conn.execute(
            "SELECT * FROM set_documents WHERE set_id = ? ORDER BY upload_order, doc_version",
            (set_id,),
        ).fetchall()
        docs = [_row_to_doc(r) for r in doc_rows]

        # Compute missing required types
        # Root docs only (parent_doc_id='') for counting types present
        root_types = {
            r["doc_type"] for r in docs if not r.get("parent_doc_id", "")
        }
        from models import REQUIRED_DOC_TYPES, DOC_TYPES
        missing = [t for t in DOC_TYPES if t in REQUIRED_DOC_TYPES and t not in root_types]

        return {
            "set_id": set_row[0],
            "status": set_row[1],
            "created_at": set_row[2],
            "employee_id": set_row[3],
            "project_group_id": set_row[4] or "",
            "project_group_name": set_row[5] or "",
            "documents": docs,
            "missing_types": missing,
        }


def list_document_sets(employee_id: str = "") -> list[dict]:
    """List document sets with the latest real review summary.

    Older rows only stored the set identity and owner.  The V2 workbench needs
    a display title and review counters, so derive those values from the
    existing extracted metadata and latest evidence-graph run instead of
    inventing a second task store.
    """
    with _connect() as conn:
        if employee_id:
            rows = conn.execute(
                """SELECT ds.set_id, ds.status, ds.created_at, ds.employee_id,
                          ds.project_group_id, COALESCE(pg.name, '')
                   FROM document_sets ds
                   LEFT JOIN project_groups pg ON pg.id = ds.project_group_id
                   WHERE ds.employee_id = ? ORDER BY ds.created_at DESC""",
                (employee_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT ds.set_id, ds.status, ds.created_at, ds.employee_id,
                          ds.project_group_id, COALESCE(pg.name, '')
                   FROM document_sets ds
                   LEFT JOIN project_groups pg ON pg.id = ds.project_group_id
                   ORDER BY ds.created_at DESC""",
            ).fetchall()
        result = []
        for row in rows:
            document_rows = conn.execute(
                """SELECT doc_id, doc_type, filename, doc_version, created_at
                   FROM set_documents WHERE set_id = ?
                   ORDER BY doc_version DESC, created_at DESC, upload_order DESC""",
                (row[0],),
            ).fetchall()
            latest_documents = {}
            for document in document_rows:
                latest_documents.setdefault(document[1], document)

            title = _document_set_title(conn, latest_documents)
            latest_run = conn.execute(
                """SELECT graph_id, status, updated_at
                   FROM evidence_graph_runs
                   WHERE set_id = ? AND scope = 'run'
                   ORDER BY created_at DESC, graph_id DESC LIMIT 1""",
                (row[0],),
            ).fetchone()
            finding_count = 0
            pending_count = 0
            updated_at = max(
                [row[2], *[str(document[4] or "") for document in document_rows]],
            )
            latest_run_status = ""
            if latest_run:
                latest_run_status = latest_run[1]
                updated_at = max(updated_at, str(latest_run[2] or ""))
                finding_count = conn.execute(
                    "SELECT COUNT(*) FROM evidence_graph_findings WHERE graph_id = ?",
                    (latest_run[0],),
                ).fetchone()[0]
                pending_count = conn.execute(
                    """SELECT COUNT(*) FROM evidence_graph_findings finding
                       WHERE finding.graph_id = ?
                         AND finding.status IN (
                           'confirmed_error', 'unresolved',
                           'confirmed_advisory', 'unresolved_advisory'
                         )
                         AND NOT EXISTS (
                           SELECT 1 FROM evidence_graph_finding_decisions decision
                           WHERE decision.graph_id = finding.graph_id
                             AND decision.finding_id = finding.finding_id
                         )""",
                    (latest_run[0],),
                ).fetchone()[0]
            source_status = row[1]
            workflow_status = "reviewing" if source_status == "reviewed" and pending_count else source_status
            result.append({
                "set_id": row[0],
                "status": workflow_status,
                "source_status": source_status,
                "created_at": row[2],
                "updated_at": updated_at,
                "employee_id": row[3],
                "project_group_id": row[4] or "",
                "project_group_name": row[5] or "",
                "doc_count": len(latest_documents),
                "documents_count": len(latest_documents),
                "title": title,
                "latest_run_status": latest_run_status,
                "finding_count": finding_count,
                "pending_count": pending_count,
            })
        return result


_DISPLAY_TITLE_KEYS = (
    "product_name", "part_name", "sample_name", "device_name",
    "equipment_name", "dut_name", "product", "sample",
)


def _structured_display_title(value) -> str:
    """Find an explicit product/sample title in arbitrary extracted JSON."""
    if isinstance(value, dict):
        normalized = {str(key).strip().lower(): item for key, item in value.items()}
        for key in _DISPLAY_TITLE_KEYS:
            candidate = normalized.get(key)
            if isinstance(candidate, (str, int, float)):
                text = str(candidate).strip()
                if 1 < len(text) <= 120:
                    return text
        for child in value.values():
            candidate = _structured_display_title(child)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for child in value:
            candidate = _structured_display_title(child)
            if candidate:
                return candidate
    return ""


def _document_set_title(conn: sqlite3.Connection, latest_documents: dict) -> str:
    """Derive a stable task title from the latest versions of real documents."""
    for doc_type in ("test_plan", "final_report", "order_form", "original_records"):
        document = latest_documents.get(doc_type)
        if not document:
            continue
        metadata_rows = conn.execute(
            """SELECT field_value FROM extracted_metadata
               WHERE doc_id = ? AND field_name = '__structured__'
               ORDER BY created_at DESC""",
            (document[0],),
        ).fetchall()
        for metadata in metadata_rows:
            try:
                title = _structured_display_title(json.loads(metadata[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                title = ""
            if title:
                return title

    for doc_type in ("test_plan", "final_report", "order_form", "original_records"):
        document = latest_documents.get(doc_type)
        if not document:
            continue
        stem = os.path.splitext(str(document[2] or ""))[0].strip(" _-")
        stem = re.sub(r"^(测试计划|试验计划|检测报告|正式报告|报告书|委托单)[_\s-]*", "", stem)
        if stem:
            return stem[:120]
    return ""


def delete_document_set(set_id: str) -> bool:
    """Delete a document set and every set-scoped derived record atomically."""
    with _connect() as conn:
        if not conn.execute(
            "SELECT 1 FROM document_sets WHERE set_id = ?", (set_id,),
        ).fetchone():
            return False

        # Older installations created several of these foreign keys without
        # ON DELETE CASCADE, so keep explicit deletion for upgrade safety.
        for table in (
            "extracted_metadata",
            "document_set_standard_skips",
            "document_set_standards",
        ):
            conn.execute(f"DELETE FROM {table} WHERE set_id = ?", (set_id,))
        conn.execute(
            "DELETE FROM standard_requirement_mappings WHERE scope_type = 'set' AND scope_value = ?",
            (set_id,),
        )
        conn.execute("DELETE FROM evidence_graph_runs WHERE set_id = ?", (set_id,))
        conn.execute("DELETE FROM set_documents WHERE set_id = ?", (set_id,))
        cur = conn.execute("DELETE FROM document_sets WHERE set_id = ?", (set_id,))
        return cur.rowcount > 0


def set_document_set_project_group(set_id: str, project_group_id: str) -> dict:
    project_group_id = project_group_id.strip()
    with _connect() as conn:
        set_row = conn.execute(
            "SELECT status FROM document_sets WHERE set_id = ?", (set_id,),
        ).fetchone()
        if not set_row:
            raise ValueError("文档集不存在")
        if set_row[0] not in {"incomplete", "revision"}:
            raise ValueError("文档集锁定后不能修改项目组")
        group_name = ""
        if project_group_id:
            group_row = conn.execute(
                "SELECT name FROM project_groups WHERE id = ?", (project_group_id,),
            ).fetchone()
            if not group_row:
                raise ValueError("项目组不存在")
            group_name = group_row[0]
        conn.execute(
            "UPDATE document_sets SET project_group_id = ? WHERE set_id = ?",
            (project_group_id, set_id),
        )
    logger.info(
        "document_set_project_group_updated", set_id=set_id,
        project_group_id=project_group_id,
    )
    return {"project_group_id": project_group_id, "project_group_name": group_name}


def get_document_set_project_group_id(set_id: str) -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT project_group_id FROM document_sets WHERE set_id = ?", (set_id,),
        ).fetchone()
    return str(row[0] or "") if row else ""


def lock_document_set(set_id: str) -> bool:
    """Lock a document set for review.

    New sets start as ``incomplete``. Reviewed sets can be reopened as a
    ``revision`` draft, then locked again for a new review run.
    """
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE document_sets SET status = 'locked' WHERE set_id = ? AND status IN ('incomplete', 'revision')",
            (set_id,),
        )
        return cur.rowcount > 0


def update_set_status(set_id: str, status: str) -> bool:
    """Update the status of a document set."""
    valid = {"incomplete", "revision", "locked", "reviewing", "reviewed", "error"}
    if status not in valid:
        return False
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE document_sets SET status = ? WHERE set_id = ?",
            (status, set_id),
        )
        return cur.rowcount > 0


def transition_set_status(set_id: str, status: str,
                          allowed_from: tuple[str, ...]) -> bool:
    """Atomically move a document set from an expected state."""
    valid = {"incomplete", "revision", "locked", "reviewing", "reviewed", "error"}
    if status not in valid or not allowed_from:
        return False
    placeholders = ",".join("?" for _ in allowed_from)
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            f"UPDATE document_sets SET status = ? WHERE set_id = ? "
            f"AND status IN ({placeholders})",
            (status, set_id, *allowed_from),
        )
        return cur.rowcount > 0


def document_belongs_to_set(set_id: str, doc_id: str) -> bool:
    with _connect() as conn:
        return conn.execute(
            "SELECT 1 FROM set_documents WHERE set_id = ? AND doc_id = ?",
            (set_id, doc_id),
        ).fetchone() is not None



def update_doc_extraction_status(doc_id: str, status: str,
                                  plain_text: str = "", html_content: str = "",
                                  extraction_error: str = "",
                                  extraction_quality: str = "",
                                  extraction_meta: dict | None = None) -> bool:
    """Update extraction status and content for a document."""
    quality = extraction_quality or {
        "done": "complete",
        "partial": "partial",
        "failed": "failed",
        "extracting": "pending",
        "pending": "pending",
    }.get(status, status)
    meta_json = json.dumps(extraction_meta or {}, ensure_ascii=False)
    with _connect() as conn:
        if plain_text or html_content or extraction_error:
            conn.execute(
                "UPDATE set_documents SET extraction_status = ?, plain_text = ?, html_content = ?, "
                "extraction_error = ?, extraction_quality = ?, extraction_meta = ?, "
                "reviewed_by = '', reviewed_at = '' "
                "WHERE doc_id = ?",
                (status, plain_text, html_content, extraction_error, quality, meta_json, doc_id),
            )
        else:
            conn.execute(
                "UPDATE set_documents SET extraction_status = ?, extraction_quality = ?, extraction_meta = ?, "
                "reviewed_by = '', reviewed_at = '' "
                "WHERE doc_id = ?",
                (status, quality, meta_json, doc_id),
            )
        return True


def get_doc_versions(set_id: str, doc_type: str) -> list[dict]:
    """Get the version history for a specific document type in a set."""
    with _connect() as conn:
        # Find the root doc for this type
        root = conn.execute(
            "SELECT doc_id FROM set_documents WHERE set_id = ? AND doc_type = ? "
            "AND parent_doc_id = '' ORDER BY upload_order",
            (set_id, doc_type),
        ).fetchone()
        if not root:
            return []
        # Collect all versions
        versions = []
        current_id = root[0]
        while current_id:
            row = conn.execute(
                "SELECT * FROM set_documents WHERE doc_id = ?", (current_id,)
            ).fetchone()
            if not row:
                break
            versions.append(_row_to_doc(row))
            # Find child
            child = conn.execute(
                "SELECT doc_id FROM set_documents WHERE parent_doc_id = ?",
                (current_id,),
            ).fetchone()
            current_id = child[0] if child else None
        return versions


def save_extracted_metadata(set_id: str, doc_id: str,
                             fields: list[dict]) -> int:
    """Replace extracted metadata for a document version atomically.

    Extraction retries reuse the same ``doc_id``.  Appending rows here leaves
    duplicate ``__structured__`` payloads and makes consumers depend on SQLite's
    unspecified ordering.  Human overrides are carried onto the replacement
    rows so a retry cannot silently discard reviewed corrections.
    """
    import random, string
    now = datetime.now().isoformat()
    count = 0
    with _connect() as conn:
        existing_rows = conn.execute(
            "SELECT field_name, field_value, human_override_value, human_override_comment "
            "FROM extracted_metadata WHERE doc_id = ?",
            (doc_id,),
        ).fetchall()
        overrides = {
            row[0]: {
                "field_value": row[1],
                "human_override_value": row[2],
                "human_override_comment": row[3],
            }
            for row in existing_rows
            if row[2] or row[3]
        }
        conn.execute("DELETE FROM extracted_metadata WHERE doc_id = ?", (doc_id,))
        saved_fields: set[str] = set()
        for f in fields:
            eid = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
            field_name = f["field_name"]
            override = overrides.get(field_name, {})
            conn.execute(
                "INSERT INTO extracted_metadata "
                "(extraction_id, set_id, doc_id, field_name, field_value, source_text, confidence, source_location, "
                "human_override_value, human_override_comment, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (eid, set_id, doc_id, field_name, f.get("field_value", ""),
                 f.get("source_text", ""), f.get("confidence", 1.0),
                 f.get("source_location", ""),
                 override.get("human_override_value", ""),
                 override.get("human_override_comment", ""), now),
            )
            saved_fields.add(field_name)
            count += 1
        for field_name, override in overrides.items():
            if field_name in saved_fields:
                continue
            eid = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
            conn.execute(
                "INSERT INTO extracted_metadata "
                "(extraction_id, set_id, doc_id, field_name, field_value, source_text, confidence, source_location, "
                "human_override_value, human_override_comment, created_at) "
                "VALUES (?, ?, ?, ?, ?, '', 1.0, '', ?, ?, ?)",
                (eid, set_id, doc_id, field_name, override["field_value"],
                 override["human_override_value"], override["human_override_comment"], now),
            )
    return count


def get_extracted_metadata(doc_id: str) -> list[dict]:
    """Get all extracted metadata fields for a specific document version."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT field_name, field_value, source_text, confidence, source_location, "
            "human_override_value, human_override_comment "
            "FROM extracted_metadata WHERE doc_id = ? ORDER BY field_name",
            (doc_id,),
        ).fetchall()
    return [
        {"field_name": r[0], "field_value": r[1], "source_text": r[2], "confidence": r[3],
         "source_location": r[4], "human_override_value": r[5], "human_override_comment": r[6]}
        for r in rows
    ]


def save_human_overrides(doc_id: str, overrides: dict[str, str]) -> int:
    """Save human-edited values for extracted metadata fields.

    Uses UPSERT: updates if a metadata row for this field already exists,
    otherwise inserts a new row with the override value.
    """
    import random, string
    now = datetime.now().isoformat()
    count = 0
    with _connect() as conn:
        for field_name, new_value in overrides.items():
            cur = conn.execute(
                "UPDATE extracted_metadata SET human_override_value = ? "
                "WHERE doc_id = ? AND field_name = ?",
                (new_value, doc_id, field_name),
            )
            if cur.rowcount == 0:
                # No existing metadata row for this field — insert one
                eid = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
                # Find set_id from the document record
                set_row = conn.execute(
                    "SELECT set_id FROM set_documents WHERE doc_id = ?", (doc_id,)
                ).fetchone()
                if set_row and set_row[0]:
                    conn.execute(
                        "INSERT INTO extracted_metadata "
                        "(extraction_id, set_id, doc_id, field_name, field_value, "
                        " source_text, confidence, source_location, "
                        " human_override_value, human_override_comment, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (eid, set_row[0], doc_id, field_name, new_value,
                         "", 1.0, "", new_value, "", now),
                    )
            count += 1
    return count


def confirm_document_review(set_id: str, doc_id: str, reviewer: str) -> dict:
    """Persist human confirmation for exactly one extracted document version."""
    now = datetime.now().isoformat()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT sd.extraction_status, ds.status
               FROM set_documents sd
               JOIN document_sets ds ON ds.set_id = sd.set_id
               WHERE sd.set_id = ? AND sd.doc_id = ?""",
            (set_id, doc_id),
        ).fetchone()
        if not row:
            raise ValueError("文档不存在")
        if row[1] not in {"incomplete", "revision"}:
            raise ValueError("文档集已锁定，不能更新核查状态")
        if row[0] not in {"done", "partial"}:
            raise ValueError("文档提取尚未完成，不能确认核查")
        conn.execute(
            "UPDATE set_documents SET reviewed_by = ?, reviewed_at = ? WHERE doc_id = ?",
            (reviewer, now, doc_id),
        )
    return {"doc_id": doc_id, "reviewed_by": reviewer, "reviewed_at": now}


def clear_document_review(doc_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE set_documents SET reviewed_by = '', reviewed_at = '' WHERE doc_id = ?",
            (doc_id,),
        )


def get_human_overrides(doc_id: str) -> dict[str, str]:
    """Get all human override values for a document."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT field_name, human_override_value FROM extracted_metadata "
            "WHERE doc_id = ? AND human_override_value != ''",
            (doc_id,),
        ).fetchall()
    return {r[0]: r[1] for r in rows}


# ── Field display label mapping ──────────────────────────────────────────
# Maps internal extraction keys to human-readable Chinese labels for
# the version diff view and anywhere raw field names are displayed.

_FIELD_DISPLAY_LABELS: dict[str, str] = {
    # Special
    "__structured__": "结构化数据 (JSON)",
    "__cell_map__": "单元格坐标映射",
    "__cell_values__": "单元格数据",
    # Product
    "product.name": "产品名称",
    "product.main_test_model": "主检型号",
    "product.part_number": "料号",
    "product.trademark": "商标",
    "product.voltage": "电压",
    "product.work_frequency": "工作频率",
    # Applicant
    "applicant.name_cn": "委托方（中文）",
    "applicant.name_en": "委托方（英文）",
    "applicant.address_cn": "委托方地址（中文）",
    "applicant.address_en": "委托方地址（英文）",
    "applicant.contact.name": "委托方联系人",
    "applicant.contact.email": "委托方邮箱",
    "applicant.contact.phone": "委托方电话",
    # Manufacturer
    "manufacturer.name_cn": "制造商（中文）",
    "manufacturer.name_en": "制造商（英文）",
    "manufacturer.address_cn": "制造商地址（中文）",
    "manufacturer.address_en": "制造商地址（英文）",
    "manufacturer.contact.name": "制造商联系人",
    "manufacturer.contact.email": "制造商邮箱",
    "manufacturer.contact.phone": "制造商电话",
    # Factory
    "factory.name_cn": "生产厂（中文）",
    "factory.name_en": "生产厂（英文）",
    "factory.address_cn": "生产厂地址（中文）",
    "factory.address_en": "生产厂地址（英文）",
    "factory.contact.name": "生产厂联系人",
    "factory.contact.email": "生产厂邮箱",
    "factory.contact.phone": "生产厂电话",
    # Test requirements
    "test_requirements.test_specification": "检测标准",
    "test_requirements.decision_rule": "判定规则",
    "test_requirements.report_qualification": "报告资质",
    # Other
    "other_info.software_version": "软件版本",
    "other_info.hardware_version": "硬件版本",
    # Summary
    "meta.total_files": "总文件数",
    "meta.total_items": "测试项数",
}


def _field_display_label(raw_key: str) -> str:
    """Return a human-readable Chinese label for an internal field key.

    If no mapping exists, returns the last dotted segment (or the raw key)
    with underscores replaced by spaces.
    """
    if raw_key in _FIELD_DISPLAY_LABELS:
        return _FIELD_DISPLAY_LABELS[raw_key]
    # Fallback: last segment, underscores → spaces
    label = raw_key.rsplit(".", 1)[-1]
    label = label.replace("_", " ")
    return label


def _flatten_structured(raw: str) -> dict[str, str]:
    """Expand a structured JSON string into flat dotted-key → value pairs.

    Skips internal keys like _meta.  Nested dicts become ``parent.child``,
    lists are expanded as ``parent[index]`` with concatenated string values.
    """
    if not raw:
        return {}
    import json as _json
    try:
        obj = _json.loads(raw)
    except Exception:
        return {}
    if not isinstance(obj, dict):
        return {}

    result: dict[str, str] = {}

    def _walk(prefix: str, node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.startswith("_"):
                    continue
                _walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, dict):
                    # Concatenate key values into a compact summary per item
                    parts = [f"{sk}={sv}" for sk, sv in item.items()
                             if sv and not isinstance(sv, (dict, list))]
                    result[f"{prefix}[{i}]"] = ", ".join(parts)[:300]
                elif item is not None:
                    result[f"{prefix}[{i}]"] = str(item)[:300]
        elif node is not None:
            result[prefix] = str(node)[:300]

    _walk("", obj)
    return result


def diff_doc_versions(old_doc_id: str, new_doc_id: str) -> list[dict]:
    """Compare extracted metadata between two document versions.

    ``__structured__`` JSON blobs are expanded into individual dotted-key
    fields so that each field (e.g. 产品名称, 委托方) is compared separately.
    """
    old_raw = {r["field_name"]: r["field_value"] for r in get_extracted_metadata(old_doc_id)}
    new_raw = {r["field_name"]: r["field_value"] for r in get_extracted_metadata(new_doc_id)}

    # Expand __structured__ blobs and merge with other fields
    old_fields: dict[str, str] = {}
    new_fields: dict[str, str] = {}

    _skip = {"__cell_map__", "__cell_values__"}

    for raw_dict, out_dict in [(old_raw, old_fields), (new_raw, new_fields)]:
        for key, val in raw_dict.items():
            if key in _skip:
                continue
            if key == "__structured__":
                out_dict.update(_flatten_structured(val))
            else:
                out_dict[key] = val

    all_keys = set(old_fields.keys()) | set(new_fields.keys())
    diffs = []
    for key in sorted(all_keys):
        ov = old_fields.get(key, "")
        nv = new_fields.get(key, "")
        diffs.append({
            "field_name": key,
            "display_label": _field_display_label(key),
            "old_value": ov,
            "new_value": nv,
            "changed": ov != nv,
        })
    return diffs


# ── Document-set async wrappers ───────────────────────────────────────


async def create_document_set_async(employee_id: str) -> str:
    return await _run_async(create_document_set, employee_id)


async def add_document_to_set_async(
    set_id: str, doc_type: str, filename: str, file_size_kb: int = 0,
    replace_doc_id: str = "", plain_text: str = "", html_content: str = "",
) -> str:
    return await _run_async(
        add_document_to_set, set_id, doc_type, filename, file_size_kb,
        replace_doc_id, plain_text, html_content,
    )


async def get_document_set_async(set_id: str) -> dict | None:
    return await _run_async(get_document_set, set_id)


async def list_document_sets_async(employee_id: str = "") -> list[dict]:
    return await _run_async(list_document_sets, employee_id)


async def delete_document_set_async(set_id: str) -> bool:
    return await _run_async(delete_document_set, set_id)


async def set_document_set_project_group_async(set_id: str, project_group_id: str) -> dict:
    return await _run_async(set_document_set_project_group, set_id, project_group_id)


async def get_document_set_project_group_id_async(set_id: str) -> str:
    return await _run_async(get_document_set_project_group_id, set_id)


async def lock_document_set_async(set_id: str) -> bool:
    return await _run_async(lock_document_set, set_id)


async def update_set_status_async(set_id: str, status: str) -> bool:
    return await _run_async(update_set_status, set_id, status)


async def transition_set_status_async(set_id: str, status: str,
                                      allowed_from: tuple[str, ...]) -> bool:
    return await _run_async(transition_set_status, set_id, status, allowed_from)


async def document_belongs_to_set_async(set_id: str, doc_id: str) -> bool:
    return await _run_async(document_belongs_to_set, set_id, doc_id)



async def update_doc_extraction_status_async(
    doc_id: str, status: str, plain_text: str = "", html_content: str = "",
    extraction_error: str = "", extraction_quality: str = "",
    extraction_meta: dict | None = None,
) -> bool:
    return await _run_async(
        update_doc_extraction_status, doc_id, status, plain_text, html_content,
        extraction_error, extraction_quality, extraction_meta,
    )


async def get_doc_versions_async(set_id: str, doc_type: str) -> list[dict]:
    return await _run_async(get_doc_versions, set_id, doc_type)


async def save_extracted_metadata_async(
    set_id: str, doc_id: str, fields: list[dict],
) -> int:
    return await _run_async(save_extracted_metadata, set_id, doc_id, fields)


async def get_extracted_metadata_async(doc_id: str) -> list[dict]:
    return await _run_async(get_extracted_metadata, doc_id)


async def save_human_overrides_async(doc_id: str, overrides: dict) -> int:
    return await _run_async(save_human_overrides, doc_id, overrides)


async def confirm_document_review_async(set_id: str, doc_id: str, reviewer: str) -> dict:
    return await _run_async(confirm_document_review, set_id, doc_id, reviewer)


async def clear_document_review_async(doc_id: str) -> None:
    await _run_async(clear_document_review, doc_id)


async def get_human_overrides_async(doc_id: str) -> dict:
    return await _run_async(get_human_overrides, doc_id)


async def diff_doc_versions_async(old_doc_id: str, new_doc_id: str) -> list[dict]:
    return await _run_async(diff_doc_versions, old_doc_id, new_doc_id)


def delete_document(doc_id: str) -> bool:
    """Delete a document and its extracted metadata from a set.

    Returns True if a row was actually deleted.
    """
    with _connect() as conn:
        conn.execute("DELETE FROM extracted_metadata WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM document_archive_members WHERE doc_id = ?", (doc_id,))
        cur = conn.execute("DELETE FROM set_documents WHERE doc_id = ?", (doc_id,))
        return cur.rowcount > 0


async def delete_document_async(doc_id: str) -> bool:
    return await _run_async(delete_document, doc_id)


def get_set_stats(employee_id: str = "") -> dict:
    """Compute task statistics from each set's latest evidence-graph run."""
    with _connect() as conn:
        with _timed_query("get_set_stats"):
            scope_sql = " WHERE employee_id = ?" if employee_id else ""
            scope_params = (employee_id,) if employee_id else ()
            total_sets = conn.execute(
                f"SELECT COUNT(*) FROM document_sets{scope_sql}", scope_params,
            ).fetchone()[0]

            latest_cte = """
                WITH latest_runs AS (
                    SELECT r.* FROM evidence_graph_runs r
                    JOIN (
                        SELECT set_id, MAX(created_at) AS created_at
                        FROM evidence_graph_runs WHERE scope = 'run' GROUP BY set_id
                    ) latest ON latest.set_id = r.set_id AND latest.created_at = r.created_at
                )
            """
            attention_statuses = "('confirmed_error','unresolved','confirmed_advisory','unresolved_advisory')"
            total_issues = conn.execute(
                latest_cte + f"""
                SELECT COUNT(*) FROM evidence_graph_findings finding
                JOIN latest_runs run ON run.graph_id = finding.graph_id
                JOIN document_sets ds ON ds.set_id = run.set_id
                WHERE finding.status IN {attention_statuses}
                  AND (? = '' OR ds.employee_id = ?)
                """,
                (employee_id, employee_id),
            ).fetchone()[0]

            clean_sets = conn.execute(
                latest_cte + """
                SELECT COUNT(*) FROM document_sets ds
                JOIN latest_runs run ON run.set_id = ds.set_id
                WHERE ds.status = 'reviewed'
                  AND run.status = 'machine_complete'
                  AND (? = '' OR ds.employee_id = ?)
                  AND NOT EXISTS (
                    SELECT 1 FROM evidence_graph_findings finding
                    WHERE finding.graph_id = run.graph_id
                      AND finding.status IN ('confirmed_error','unresolved')
                  )
                """,
                (employee_id, employee_id),
            ).fetchone()[0]

            reviewed_sets = conn.execute(
                """SELECT COUNT(*) FROM document_sets
                   WHERE status = 'reviewed'
                     AND (? = '' OR employee_id = ?)""",
                (employee_id, employee_id),
            ).fetchone()[0]

            clean_rate = round(clean_sets / reviewed_sets * 100, 1) if reviewed_sets > 0 else 0.0
            avg_issues = round(total_issues / total_sets, 1) if total_sets > 0 else 0.0

            sev_rows = conn.execute(
                latest_cte + f"""
                SELECT UPPER(finding.severity), COUNT(*)
                FROM evidence_graph_findings finding
                JOIN latest_runs run ON run.graph_id = finding.graph_id
                JOIN document_sets ds ON ds.set_id = run.set_id
                WHERE finding.status IN {attention_statuses}
                  AND (? = '' OR ds.employee_id = ?)
                GROUP BY UPPER(finding.severity)
                """,
                (employee_id, employee_id),
            ).fetchall()
            severity_dist = {row[0]: row[1] for row in sev_rows}

            # Status distribution
            status_rows = conn.execute("""
                SELECT status, COUNT(*) as cnt
                FROM document_sets
                WHERE (? = '' OR employee_id = ?)
                GROUP BY status
            """, (employee_id, employee_id)).fetchall()
            status_dist = {row[0]: row[1] for row in status_rows}

            # Daily trends (last 30 days)
            trend_rows = conn.execute("""
                SELECT date(created_at) as d, COUNT(*) as cnt
                FROM document_sets
                WHERE created_at >= date('now', '-30 days')
                  AND (? = '' OR employee_id = ?)
                GROUP BY d ORDER BY d
            """, (employee_id, employee_id)).fetchall()
            trends = [{"date": row[0], "count": row[1]} for row in trend_rows]

            cat_rows = conn.execute(
                latest_cte + f"""
                SELECT finding.check_id, COUNT(*) AS cnt
                FROM evidence_graph_findings finding
                JOIN latest_runs run ON run.graph_id = finding.graph_id
                JOIN document_sets ds ON ds.set_id = run.set_id
                WHERE finding.status IN {attention_statuses}
                  AND finding.check_id != ''
                  AND (? = '' OR ds.employee_id = ?)
                GROUP BY finding.check_id ORDER BY cnt DESC LIMIT 10
                """,
                (employee_id, employee_id),
            ).fetchall()
            top_categories = [{"category": row[0], "count": row[1]} for row in cat_rows]

            return {
                "total_sets": total_sets,
                "total_issues": total_issues,
                "clean_rate": clean_rate,
                "avg_issues_per_set": avg_issues,
                "severity_dist": severity_dist,
                "status_dist": status_dist,
                "trends": trends,
                "top_categories": top_categories,
            }


async def get_set_stats_async(employee_id: str = "") -> dict:
    return await _run_async(get_set_stats, employee_id)
