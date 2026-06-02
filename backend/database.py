import os
import sqlite3
import uuid
import json
import asyncio
from contextlib import contextmanager
from datetime import datetime
from models import ReportRecord, ReviewItem, AuditLogEntry, ReviewRule, EmcStandard, StandardClause, User
from utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = os.getenv("DB_PATH", "review.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def _run_async(func, *args, **kwargs):
    """Run a sync DB function in a thread pool to avoid blocking the event loop."""
    return await asyncio.to_thread(func, *args, **kwargs)


def init_db():
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                overall_result TEXT NOT NULL,
                review_items TEXT DEFAULT '[]',
                highlighted_html TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
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
            CREATE TABLE IF NOT EXISTS review_rules (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT DEFAULT 'other',
                severity TEXT DEFAULT 'warning',
                keywords TEXT DEFAULT '[]',
                pattern TEXT DEFAULT '',
                standard_id TEXT DEFAULT '',
                suggestion_template TEXT DEFAULT '',
                enabled INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
            CREATE VIRTUAL TABLE IF NOT EXISTS review_items_fts USING fts5(
                report_id UNINDEXED,
                location,
                original_text,
                error_description,
                standard_reference,
                suggestion
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suppression_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_text TEXT NOT NULL,
                error_description TEXT NOT NULL,
                location TEXT DEFAULT '',
                severity TEXT DEFAULT '',
                suppressed_by TEXT NOT NULL,
                suppressed_at TEXT NOT NULL
            )
        """)
    _migrate_audit_log_employee_id()
    _migrate_reports_columns()
    _migrate_users_name()
    _migrate_project_groups_category()
    _migrate_tags()
    _backfill_fts()


def _migrate_audit_log_employee_id():
    with _connect() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(audit_log)").fetchall()]
        if "employee_id" not in cols:
            conn.execute("ALTER TABLE audit_log ADD COLUMN employee_id TEXT DEFAULT ''")


def _migrate_reports_columns():
    with _connect() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(reports)").fetchall()]
        additions = {
            "employee_id": "TEXT DEFAULT ''",
            "tags": "TEXT DEFAULT ''",
            "is_deleted": "INTEGER DEFAULT 0",
            "group_id": "TEXT DEFAULT ''",
            "comparison": "TEXT DEFAULT ''",
            "compared_with": "TEXT DEFAULT ''",
            "original_result": "TEXT DEFAULT ''",
        }
        for name, ddl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE reports ADD COLUMN {name} {ddl}")


def _migrate_users_name():
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
    with _connect() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(project_groups)").fetchall()]
        if "category" not in cols:
            conn.execute("ALTER TABLE project_groups ADD COLUMN category TEXT DEFAULT '其他'")


def _migrate_tags():
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


def _backfill_fts():
    with _connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM review_items_fts").fetchone()[0]
        if count > 0:
            return
        rows = conn.execute("SELECT id, review_items FROM reports").fetchall()
        for report_id, items_json in rows:
            items = json.loads(items_json)
            for item in items:
                conn.execute(
                    "INSERT INTO review_items_fts "
                    "(report_id, location, original_text, error_description, standard_reference, suggestion) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (report_id,
                     item.get("location", ""),
                     item.get("original_text", ""),
                     item.get("error_description", ""),
                     item.get("standard_reference", ""),
                     item.get("suggestion", ""))
                )
    if count == 0 and rows:
        pass  # backfill complete on first run


def save_audit_log(client_ip: str, action: str, filename: str = "",
                    file_size_kb: int = 0, overall_result: str = "",
                    issue_count: int = 0, detail: str = "",
                    duration_ms: int = 0, employee_id: str = ""):
    extra: dict[str, object] = {}
    if detail:
        extra["msg"] = detail
    extra["duration_ms"] = duration_ms
    detail_json = json.dumps(extra, ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (timestamp, client_ip, employee_id, action, filename, "
            "file_size_kb, overall_result, issue_count, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now().isoformat(), client_ip, employee_id, action, filename,
             file_size_kb, overall_result, issue_count, detail_json),
        )


def get_audit_logs(limit: int = 100, offset: int = 0,
                   action: str = "", ip: str = "",
                   employee_id: str = "", filename: str = "",
                   date_from: str = "",
                   date_to: str = "") -> tuple[list[AuditLogEntry], int]:
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
            f"SELECT id, timestamp, client_ip, employee_id, action, filename, "
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
            action=row[4],
            filename=row[5],
            file_size_kb=row[6],
            overall_result=row[7],
            issue_count=row[8],
            detail=row[9],
        ))
    return entries, total


def save_report(filename: str, overall_result: str, review_items: list[ReviewItem], highlighted_html: str, employee_id: str = "", tags: str = "", group_id: str = "", comparison: str = "", compared_with: str = "", original_result: str = "") -> str:
    report_id = uuid.uuid4().hex[:12]
    created_at = datetime.now().isoformat()
    items_json = json.dumps([item.model_dump() for item in review_items], ensure_ascii=False)
    stored_original_result = original_result or overall_result
    with _connect() as conn:
        conn.execute(
            "INSERT INTO reports (id, filename, overall_result, review_items, highlighted_html, created_at, employee_id, tags, group_id, comparison, compared_with, original_result) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (report_id, filename, overall_result, items_json, highlighted_html, created_at, employee_id, tags, group_id, comparison, compared_with, stored_original_result),
        )
        for item in review_items:
            conn.execute(
                "INSERT INTO review_items_fts "
                "(report_id, location, original_text, error_description, standard_reference, suggestion) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (report_id, item.location, item.original_text,
                 item.error_description, item.standard_reference, item.suggestion)
            )
    return report_id


def _row_to_report_record(row, has_uploader_name: bool = False) -> ReportRecord:
    """Convert a reports table row to a ReportRecord.

    Callers that join with the users table should pass has_uploader_name=True
    so that row[13] (the COALESCE(u.name, '') column) is read as uploader_name.
    """
    items_data = json.loads(row[3])
    items = [ReviewItem(**item) for item in items_data]
    return ReportRecord(
        id=row[0],
        filename=row[1],
        overall_result=row[2],
        review_items=items,
        highlighted_html=row[4],
        created_at=row[5],
        employee_id=row[6] if len(row) > 6 else "",
        tags=row[7] if len(row) > 7 else "",
        uploader_name=row[13] if has_uploader_name and len(row) > 13 else "",
        group_id=row[9] if len(row) > 9 else "",
        comparison=row[10] if len(row) > 10 else "",
        compared_with=row[11] if len(row) > 11 else "",
        original_result=row[12] if len(row) > 12 else "",
    )


def get_report(report_id: str) -> ReportRecord | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT reports.*, COALESCE(users.name, '') as uploader_name FROM reports "
            "LEFT JOIN users ON reports.employee_id = users.employee_id "
            "WHERE reports.id = ? AND reports.is_deleted = 0",
            (report_id,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_report_record(row, has_uploader_name=True)


def update_review_item_annotation(report_id: str, item_index: int,
                                   human_status: str, human_comment: str = "",
                                   annotated_by: str = "") -> bool:
    annotated_at = datetime.now().isoformat()
    # Wrap the read-modify-write cycle in BEGIN IMMEDIATE to prevent lost
    # updates from concurrent annotators editing the same report.  SQLite's
    # default deferred transaction only acquires a read lock on first read,
    # so two writers could each read the same stale review_items, modify
    # them independently, and then write back — silently dropping one update.
    # BEGIN IMMEDIATE acquires a reserved lock up front, serializing writers.
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT review_items FROM reports WHERE id = ?", (report_id,)
        ).fetchone()
        if row is None:
            conn.rollback()
            return False
        try:
            items = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            conn.rollback()
            return False
        if item_index < 0 or item_index >= len(items):
            conn.rollback()
            return False
        items[item_index]["human_status"] = human_status
        items[item_index]["human_comment"] = human_comment
        items[item_index]["annotated_by"] = annotated_by
        items[item_index]["annotated_at"] = annotated_at
        conn.execute(
            "UPDATE reports SET review_items = ? WHERE id = ?",
            (json.dumps(items, ensure_ascii=False), report_id),
        )
        if human_status in ("false_positive", "ignored"):
            item = items[item_index]
            conn.execute(
                "INSERT OR IGNORE INTO suppression_patterns (original_text, error_description, "
                "location, severity, suppressed_by, suppressed_at) VALUES (?, ?, ?, ?, ?, ?)",
                (item.get("original_text", ""), item.get("error_description", ""),
                 item.get("location", ""), item.get("severity", ""),
                 annotated_by, annotated_at),
            )
        # Recalculate report status from fresh DB state (now safely within
        # the same transaction — no other writer can interleave).
        fresh = conn.execute("SELECT review_items FROM reports WHERE id = ?", (report_id,)).fetchone()
        if fresh:
            try:
                fresh_items = json.loads(fresh[0])
            except (json.JSONDecodeError, TypeError):
                fresh_items = items
        else:
            fresh_items = items
        pending = sum(1 for it in fresh_items if it.get("human_status", "pending") in ("pending", "needs_review"))
        suppressed = sum(1 for it in fresh_items if it.get("human_status") in ("false_positive", "ignored"))
        if pending > 0:
            orig_row = conn.execute("SELECT original_result FROM reports WHERE id = ?", (report_id,)).fetchone()
            orig = orig_row[0] if orig_row and orig_row[0] else "fail"
            conn.execute("UPDATE reports SET overall_result = ? WHERE id = ?", (orig, report_id))
        elif suppressed > 0:
            conn.execute("UPDATE reports SET overall_result = 'warning' WHERE id = ?", (report_id,))
        else:
            conn.execute("UPDATE reports SET overall_result = 'pass' WHERE id = ?", (report_id,))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_suppression_pattern(original_text: str, error_description: str,
                                location: str, severity: str,
                                suppressed_by: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO suppression_patterns (original_text, error_description, "
            "location, severity, suppressed_by, suppressed_at) VALUES (?, ?, ?, ?, ?, ?)",
            (original_text, error_description, location, severity,
             suppressed_by, datetime.now().isoformat()),
        )


def get_suppression_patterns() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT original_text, error_description, location, severity "
            "FROM suppression_patterns"
        ).fetchall()
    return [{"original_text": r[0], "error_description": r[1],
             "location": r[2], "severity": r[3]} for r in rows]


def get_reports(limit: int = 20, offset: int = 0,
                keyword: str = "", overall_result: str = "",
                date_from: str = "", date_to: str = "",
                employee_id: str = "", tag: str = "",
                group_tags: list[str] | None = None,
                include_deleted: bool = False) -> tuple[list[ReportRecord], int]:
    conditions = []
    params: list = []

    if group_tags:
        like_clauses = " OR ".join(["reports.tags LIKE ?" for _ in group_tags])
        if employee_id:
            conditions.append(f"(reports.employee_id = ? OR ({like_clauses}))")
            params.extend([employee_id] + [f"%{t}%" for t in group_tags])
            employee_id = ""
        else:
            conditions.append(f"({like_clauses})")
            params.extend([f"%{t}%" for t in group_tags])
    if keyword:
        conditions.append("reports.filename LIKE ?")
        params.append(f"%{keyword}%")
    if overall_result:
        conditions.append("reports.overall_result = ?")
        params.append(overall_result)
    if date_from:
        conditions.append("reports.created_at >= ?")
        params.append(date_from)
    if date_to:
        conditions.append("reports.created_at <= ?")
        params.append(date_to + "T23:59:59")
    if employee_id:
        conditions.append("reports.employee_id = ?")
        params.append(employee_id)
    if not include_deleted:
        conditions.append("reports.is_deleted = 0")
    if tag:
        conditions.append("reports.tags LIKE ?")
        params.append(f"%{tag}%")

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM reports{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT reports.*, COALESCE(users.name, '') as uploader_name FROM reports "
            f"LEFT JOIN users ON reports.employee_id = users.employee_id "
            f"{where} ORDER BY reports.created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    reports = []
    for row in rows:
        reports.append(_row_to_report_record(row, has_uploader_name=True))
    return reports, total


def search_reports(query: str, limit: int = 20, offset: int = 0,
                   employee_id: str = "") -> tuple[list[dict], int]:
    if not query.strip():
        return [], 0
    try:
        with _connect() as conn:
            if employee_id:
                total_row = conn.execute(
                    "SELECT COUNT(DISTINCT fts.report_id) FROM review_items_fts fts "
                    "JOIN reports r ON r.id = fts.report_id "
                    "WHERE fts.review_items_fts MATCH ? AND r.employee_id = ? AND r.is_deleted = 0",
                    (query, employee_id)
                ).fetchone()
            else:
                total_row = conn.execute(
                    "SELECT COUNT(DISTINCT fts.report_id) FROM review_items_fts fts "
                    "JOIN reports r ON r.id = fts.report_id "
                    "WHERE fts.review_items_fts MATCH ? AND r.is_deleted = 0",
                    (query,)
                ).fetchone()
            total = total_row[0] if total_row else 0
            if total == 0:
                return [], 0

            if employee_id:
                grouped = conn.execute("""
                    SELECT fts.report_id, MIN(fts.rank) AS best_rank, COUNT(*) AS match_count
                    FROM review_items_fts fts
                    JOIN reports r ON r.id = fts.report_id
                    WHERE fts.review_items_fts MATCH ? AND r.employee_id = ? AND r.is_deleted = 0
                    GROUP BY fts.report_id
                    ORDER BY best_rank
                    LIMIT ? OFFSET ?
                """, (query, employee_id, limit, offset)).fetchall()
            else:
                grouped = conn.execute("""
                    SELECT fts.report_id, MIN(fts.rank) AS best_rank, COUNT(*) AS match_count
                    FROM review_items_fts fts
                    JOIN reports r ON r.id = fts.report_id
                    WHERE fts.review_items_fts MATCH ? AND r.is_deleted = 0
                    GROUP BY fts.report_id
                    ORDER BY best_rank
                    LIMIT ? OFFSET ?
                """, (query, limit, offset)).fetchall()

            results = []
            for grp in grouped:
                report_id, best_rank, match_count = grp
                snip_row = conn.execute("""
                    SELECT
                        snippet(review_items_fts, 1, '<mark>', '</mark>', '…', 40),
                        snippet(review_items_fts, 2, '<mark>', '</mark>', '…', 40),
                        snippet(review_items_fts, 3, '<mark>', '</mark>', '…', 40),
                        snippet(review_items_fts, 4, '<mark>', '</mark>', '…', 40),
                        snippet(review_items_fts, 5, '<mark>', '</mark>', '…', 40)
                    FROM review_items_fts
                    WHERE review_items_fts MATCH ? AND report_id = ?
                    ORDER BY rank LIMIT 1
                """, (query, report_id)).fetchone()

                r = conn.execute(
                    "SELECT r2.id, r2.filename, r2.overall_result, r2.created_at, r2.employee_id, "
                    "COALESCE(u.name, '') as uploader_name "
                    "FROM reports r2 LEFT JOIN users u ON r2.employee_id = u.employee_id "
                    "WHERE r2.id = ? AND r2.is_deleted = 0",
                    (report_id,)
                ).fetchone()
                results.append({
                    "report_id": report_id,
                    "filename": r[1] if r else "",
                    "overall_result": r[2] if r else "",
                    "created_at": r[3] if r else "",
                    "employee_id": r[4] if r and len(r) > 4 else "",
                    "uploader_name": r[5] if r and len(r) > 5 else "",
                    "snippets": {
                        "location": snip_row[0] if snip_row else "",
                        "original_text": snip_row[1] if snip_row else "",
                        "error_description": snip_row[2] if snip_row else "",
                        "standard_reference": snip_row[3] if snip_row else "",
                        "suggestion": snip_row[4] if snip_row else "",
                    },
                    "match_count": match_count,
                    "rank": best_rank,
                })
            return results, total
    except sqlite3.Error:
        logger.exception("FTS5_SEARCH_ERROR query=%r", query)
        raise


# ── Review Rules CRUD ───────────────────────────────────────────────

def save_rule(name: str, description: str = "", category: str = "other",
              severity: str = "warning", keywords: list[str] | None = None,
              pattern: str = "", standard_id: str = "",
              suggestion_template: str = "", enabled: bool = True) -> str:
    rule_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    kw_json = json.dumps(keywords or [], ensure_ascii=False)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO review_rules (id, name, description, category, severity, "
            "keywords, pattern, standard_id, suggestion_template, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rule_id, name, description, category, severity, kw_json, pattern,
             standard_id, suggestion_template, 1 if enabled else 0, now, now),
        )
    return rule_id


def get_rules(keyword: str = "", category: str = "") -> list[ReviewRule]:
    conditions = []
    params: list = []
    if keyword:
        conditions.append("(name LIKE ? OR description LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if category:
        conditions.append("category = ?")
        params.append(category)
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM review_rules{where} ORDER BY created_at DESC", params
        ).fetchall()
    rules = []
    for row in rows:
        rules.append(ReviewRule(
            id=row[0], name=row[1], description=row[2], category=row[3],
            severity=row[4], keywords=json.loads(row[5]), pattern=row[6],
            standard_id=row[7], suggestion_template=row[8], enabled=bool(row[9]),
            created_at=row[10], updated_at=row[11],
        ))
    return rules


def get_rule(rule_id: str) -> ReviewRule | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM review_rules WHERE id = ?", (rule_id,)).fetchone()
    if row is None:
        return None
    return ReviewRule(
        id=row[0], name=row[1], description=row[2], category=row[3],
        severity=row[4], keywords=json.loads(row[5]), pattern=row[6],
        standard_id=row[7], suggestion_template=row[8], enabled=bool(row[9]),
        created_at=row[10], updated_at=row[11],
    )


def update_rule(rule_id: str, **kwargs) -> bool:
    allowed = ["name", "description", "category", "severity", "keywords",
               "pattern", "standard_id", "suggestion_template", "enabled"]
    sets = []
    params = []
    for k in allowed:
        if k in kwargs and kwargs[k] is not None:
            val = kwargs[k]
            if k == "keywords":
                val = json.dumps(val, ensure_ascii=False)
            elif k == "enabled":
                val = 1 if val else 0
            sets.append(f"{k} = ?")
            params.append(val)
    if not sets:
        return False
    params.append(datetime.now().isoformat())
    params.append(rule_id)
    sets.append("updated_at = ?")
    with _connect() as conn:
        conn.execute(
            f"UPDATE review_rules SET {', '.join(sets)} WHERE id = ?", params
        )
    return True


def delete_rule(rule_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM review_rules WHERE id = ?", (rule_id,))
    return cur.rowcount > 0


def get_enabled_rules() -> list[ReviewRule]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM review_rules WHERE enabled = 1 ORDER BY severity, created_at"
        ).fetchall()
    rules = []
    for row in rows:
        rules.append(ReviewRule(
            id=row[0], name=row[1], description=row[2], category=row[3],
            severity=row[4], keywords=json.loads(row[5]), pattern=row[6],
            standard_id=row[7], suggestion_template=row[8], enabled=True,
            created_at=row[10], updated_at=row[11],
        ))
    return rules


# ── EMC Standards CRUD ───────────────────────────────────────────────

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
            "version, clauses, is_builtin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (std_id, code, title, organization, category, version,
             clauses_json, 1 if is_builtin else 0, now),
        )
    return std_id


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
            f"SELECT * FROM emc_standards{where} ORDER BY organization, code", params
        ).fetchall()
    standards = []
    for row in rows:
        clauses_data = json.loads(row[6])
        clauses = [StandardClause(**c) for c in clauses_data]
        standards.append(EmcStandard(
            id=row[0], code=row[1], title=row[2], organization=row[3],
            category=row[4], version=row[5], clauses=clauses,
            is_builtin=bool(row[7]), created_at=row[8],
        ))
    return standards


def get_standard(std_id: str) -> EmcStandard | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM emc_standards WHERE id = ?", (std_id,)).fetchone()
    if row is None:
        return None
    clauses_data = json.loads(row[6])
    clauses = [StandardClause(**c) for c in clauses_data]
    return EmcStandard(
        id=row[0], code=row[1], title=row[2], organization=row[3],
        category=row[4], version=row[5], clauses=clauses,
        is_builtin=bool(row[7]), created_at=row[8],
    )


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
    if role not in ("admin", "reviewer", "viewer"):
        role = "viewer"
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


def generate_group_id() -> str:
    """Generate a human-readable report ID: EMC-YYYYMMDD-NNN-XXXX.

    NOTE: This function has a TOCTOU race between reading the latest group_id
    and inserting the new one.  Under moderate concurrent load the window is
    small enough to be acceptable, but it could produce duplicate sequence
    numbers under heavy contention.  A future fix could use a locking table
    or an INSERT … RETURNING approach.
    """
    import secrets
    today = datetime.now().strftime("%Y%m%d")
    prefix = f"EMC-{today}-"
    with _connect() as conn:
        row = conn.execute(
            "SELECT group_id FROM reports WHERE group_id LIKE ? ORDER BY group_id DESC LIMIT 1",
            (f"{prefix}%",)
        ).fetchone()
        if row and row[0]:
            parts = row[0].rsplit("-", 2)
            last_num = int(parts[-2]) if len(parts) >= 3 else int(parts[-1])
            return f"{prefix}{last_num + 1:03d}-{secrets.token_hex(2)}"
        return f"{prefix}001-{secrets.token_hex(2)}"


def get_group_versions(group_id: str) -> list[ReportRecord]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM reports WHERE group_id = ? AND is_deleted = 0 ORDER BY created_at DESC",
            (group_id,)
        ).fetchall()
    reports = []
    for row in rows:
        reports.append(_row_to_report_record(row))
    return reports


# ── Project Groups ───────────────────────────────────────────────────

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
            old = conn.execute("SELECT name FROM project_groups WHERE id=?", (gid,)).fetchone()
            if old and old[0] != name:
                conn.execute("UPDATE reports SET tags = ? WHERE tags = ?", (name, old[0]))
            conn.execute("UPDATE project_groups SET name=? WHERE id=?", (name, gid))
        if description is not None:
            conn.execute("UPDATE project_groups SET description=? WHERE id=?", (description, gid))
        if category is not None:
            conn.execute("UPDATE project_groups SET category=? WHERE id=?", (category, gid))
        if member_ids is not None:
            conn.execute("DELETE FROM project_group_members WHERE group_id=?", (gid,))
            for mid in member_ids:
                conn.execute("INSERT OR IGNORE INTO project_group_members (group_id, employee_id) VALUES (?,?)", (gid, mid))
        return True


def delete_project_group(gid: str) -> bool:
    with _connect() as conn:
        name_row = conn.execute("SELECT name FROM project_groups WHERE id=?", (gid,)).fetchone()
        if name_row:
            conn.execute("UPDATE reports SET tags = '' WHERE tags = ?", (name_row[0],))
        conn.execute("DELETE FROM project_group_members WHERE group_id=?", (gid,))
        cur = conn.execute("DELETE FROM project_groups WHERE id=?", (gid,))
        return cur.rowcount > 0


def get_project_groups() -> list[dict]:
    with _connect() as conn:
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

async def save_report_async(filename: str, overall_result: str,
                             review_items: list[ReviewItem],
                             highlighted_html: str, employee_id: str = "",
                             tags: str = "", group_id: str = "",
                             comparison: str = "", compared_with: str = "", original_result: str = "") -> str:
    return await _run_async(save_report, filename, overall_result, review_items, highlighted_html, employee_id, tags, group_id, comparison, compared_with, original_result)

async def generate_group_id_async() -> str:
    return await _run_async(generate_group_id)

async def get_group_versions_async(group_id: str) -> list[ReportRecord]:
    return await _run_async(get_group_versions, group_id)


async def get_report_async(report_id: str) -> ReportRecord | None:
    return await _run_async(get_report, report_id)


async def update_review_item_annotation_async(report_id: str, item_index: int,
                                               human_status: str, human_comment: str = "",
                                               annotated_by: str = "") -> bool:
    return await _run_async(update_review_item_annotation, report_id, item_index,
                            human_status, human_comment, annotated_by)


async def create_suppression_pattern_async(original_text: str, error_description: str,
                                            location: str, severity: str,
                                            suppressed_by: str) -> None:
    return await _run_async(create_suppression_pattern, original_text,
                            error_description, location, severity, suppressed_by)


async def get_suppression_patterns_async() -> list[dict]:
    return await _run_async(get_suppression_patterns)


async def get_reports_async(limit: int = 20, offset: int = 0,
                             keyword: str = "", overall_result: str = "",
                             date_from: str = "", date_to: str = "",
                             employee_id: str = "", tag: str = "",
                             group_tags: list[str] | None = None,
                             include_deleted: bool = False) -> tuple[list[ReportRecord], int]:
    return await _run_async(get_reports, limit, offset, keyword, overall_result,
                            date_from, date_to, employee_id, tag, group_tags, include_deleted)


async def get_audit_logs_async(limit: int = 100, offset: int = 0,
                               action: str = "", ip: str = "",
                               employee_id: str = "", filename: str = "",
                               date_from: str = "",
                               date_to: str = "") -> tuple[list[AuditLogEntry], int]:
    return await _run_async(get_audit_logs, limit, offset, action, ip,
                            employee_id, filename, date_from, date_to)


def get_stats(employee_id: str = "") -> dict:
    """Aggregate statistics from reports and audit_log tables.

    When employee_id is provided, only reports/audit_log entries belonging to
    that user are counted.
    """
    report_conditions = ["is_deleted = 0"]
    report_params: list[str] = []
    audit_filter = "WHERE employee_id = ?" if employee_id else ""
    audit_params = (employee_id,) if employee_id else ()
    if employee_id:
        report_conditions.append("employee_id = ?")
        report_params.append(employee_id)
    report_filter = "WHERE " + " AND ".join(report_conditions)

    with _connect() as conn:
        rows = conn.execute(
            f"SELECT overall_result, COUNT(*) FROM reports {report_filter} GROUP BY overall_result",
            report_params,
        ).fetchall()
        pass_fail = {row[0]: row[1] for row in rows}
        total_reports = sum(pass_fail.values())

        all_rows = conn.execute(
            f"SELECT review_items FROM reports {report_filter}", report_params
        ).fetchall()

        sev_dist: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
        location_counts: dict[str, int] = {}
        total_issues = 0

        for (items_json,) in all_rows:
            items = json.loads(items_json)
            for item in items:
                total_issues += 1
                sev = item.get("severity", "info")
                sev_dist[sev] = sev_dist.get(sev, 0) + 1
                loc = item.get("location", "").strip()
                if loc:
                    location_counts[loc] = location_counts.get(loc, 0) + 1

        avg_issues = round(total_issues / total_reports, 1) if total_reports else 0
        pass_count = pass_fail.get("pass", 0)
        pass_rate = round(pass_count / total_reports * 100, 1) if total_reports else 0

        trends_rows = conn.execute(
            f"""SELECT DATE(created_at) as d,
                       COUNT(*) as total,
                       SUM(CASE WHEN overall_result = 'pass' THEN 1 ELSE 0 END) as pass_cnt,
                       SUM(CASE WHEN overall_result = 'fail' THEN 1 ELSE 0 END) as fail_cnt
                FROM reports
                {report_filter} AND created_at >= DATE('now', '-30 days')
                GROUP BY d ORDER BY d""",
            report_params,
        ).fetchall()
        trends = [
            {"date": r[0], "uploads": r[1], "pass_count": r[2], "fail_count": r[3]}
            for r in trends_rows
        ]

        top_locs = sorted(location_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        top_locations = [{"location": loc, "count": cnt} for loc, cnt in top_locs]

        action_rows = conn.execute(
            f"SELECT action, COUNT(*) FROM audit_log {audit_filter} GROUP BY action",
            audit_params,
        ).fetchall()
        top_actions = {row[0]: row[1] for row in action_rows}

        dur_rows = conn.execute(
            f"SELECT detail FROM audit_log {audit_filter + ' AND' if audit_filter else 'WHERE'} detail != ''",
            audit_params,
        ).fetchall()
        durations = []
        for (detail_json,) in dur_rows:
            try:
                d = json.loads(detail_json)
                if "duration_ms" in d:
                    durations.append(d["duration_ms"])
            except (json.JSONDecodeError, KeyError):
                pass
        avg_duration = int(sum(durations) / len(durations)) if durations else 0

        return {
            "overview": {
                "total_reports": total_reports,
                "total_issues": total_issues,
                "pass_rate": pass_rate,
                "avg_issues_per_report": avg_issues,
                "avg_duration_ms": avg_duration,
            },
            "pass_fail": pass_fail,
            "severity_dist": sev_dist,
            "trends": trends,
            "top_locations": top_locations,
            "top_actions": top_actions,
        }


async def get_stats_async(employee_id: str = "") -> dict:
    return await _run_async(get_stats, employee_id)


async def save_audit_log_async(client_ip: str, action: str, filename: str = "",
                                file_size_kb: int = 0, overall_result: str = "",
                                issue_count: int = 0, detail: str = "",
                                duration_ms: int = 0, employee_id: str = ""):
    await _run_async(save_audit_log, client_ip, action, filename,
                     file_size_kb, overall_result, issue_count, detail, duration_ms, employee_id)


# ── Rules async wrappers ─────────────────────────────────────────────

async def save_rule_async(name: str, description: str = "", category: str = "other",
                         severity: str = "warning", keywords: list[str] | None = None,
                         pattern: str = "", standard_id: str = "",
                         suggestion_template: str = "", enabled: bool = True) -> str:
    return await _run_async(save_rule, name, description, category, severity,
                            keywords, pattern, standard_id, suggestion_template, enabled)


async def get_rules_async(keyword: str = "", category: str = "") -> list[ReviewRule]:
    return await _run_async(get_rules, keyword, category)


async def get_rule_async(rule_id: str) -> ReviewRule | None:
    return await _run_async(get_rule, rule_id)


async def update_rule_async(rule_id: str, **kwargs) -> bool:
    return await _run_async(update_rule, rule_id, **kwargs)


async def delete_rule_async(rule_id: str) -> bool:
    return await _run_async(delete_rule, rule_id)


async def get_enabled_rules_async() -> list[ReviewRule]:
    return await _run_async(get_enabled_rules)


# ── Standards async wrappers ─────────────────────────────────────────

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


async def seed_builtin_standards_async():
    await _run_async(seed_builtin_standards)


async def search_reports_async(query: str, limit: int = 20, offset: int = 0,
                               employee_id: str = "") -> tuple[list[dict], int]:
    return await _run_async(search_reports, query, limit, offset, employee_id)


# ── User async wrappers ──────────────────────────────────────────────

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


def delete_report(report_id: str) -> bool:
    """Soft-delete a report. Returns True if marked, False if not found."""
    with _connect() as conn:
        cur = conn.execute("SELECT id FROM reports WHERE id = ?", (report_id,))
        if not cur.fetchone():
            return False
        conn.execute("UPDATE reports SET is_deleted = 1 WHERE id = ?", (report_id,))
        return True


def delete_report_group(group_id: str) -> int:
    """Soft-delete all reports in a group. Returns count of deleted reports."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE reports SET is_deleted = 1 WHERE group_id = ? AND is_deleted = 0",
            (group_id,),
        )
        return cur.rowcount


def update_report_tags(report_id: str, tags: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT id, group_id FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not row:
            return False
        gid = row[1]
        if gid:
            conn.execute("UPDATE reports SET tags = ? WHERE group_id = ?", (tags, gid))
        else:
            conn.execute("UPDATE reports SET tags = ? WHERE id = ?", (tags, report_id))
        return True


def restore_report(report_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("SELECT id FROM reports WHERE id = ? AND is_deleted = 1", (report_id,))
        if not cur.fetchone():
            return False
        conn.execute("UPDATE reports SET is_deleted = 0 WHERE id = ?", (report_id,))
        return True


def purge_report(report_id: str) -> bool:
    """Permanently delete a report and its FTS index entries."""
    with _connect() as conn:
        cur = conn.execute("SELECT id FROM reports WHERE id = ?", (report_id,))
        if not cur.fetchone():
            return False
        conn.execute("DELETE FROM review_items_fts WHERE report_id = ?", (report_id,))
        conn.execute("DELETE FROM reports WHERE id = ?", (report_id,))
        return True


async def delete_report_async(report_id: str) -> bool:
    return await _run_async(delete_report, report_id)


async def delete_report_group_async(group_id: str) -> int:
    return await _run_async(delete_report_group, group_id)


async def restore_report_async(report_id: str) -> bool:
    return await _run_async(restore_report, report_id)


async def update_report_tags_async(report_id: str, tags: str) -> bool:
    return await _run_async(update_report_tags, report_id, tags)


async def purge_report_async(report_id: str) -> bool:
    return await _run_async(purge_report, report_id)


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

async def get_project_groups_async() -> list[dict]:
    return await _run_async(get_project_groups)

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


def get_user_project_group_names(employee_id: str) -> list[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT pg.name FROM project_groups pg "
            "JOIN project_group_members pgm ON pg.id = pgm.group_id "
            "WHERE pgm.employee_id = ?", (employee_id,)
        ).fetchall()
    return [r[0] for r in rows]


async def get_user_project_group_names_async(employee_id: str) -> list[str]:
    return await _run_async(get_user_project_group_names, employee_id)
