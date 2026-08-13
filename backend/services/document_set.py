"""Document set service — CRUD, versioning, locking, and diff.

A DocumentSet groups up to 5 document types (order_form, test_plan,
original_records, final_report, test_standard) with file-level versioning.

Status flow: incomplete -> locked -> reviewing -> reviewed
Reviewed sets can be reopened as revision -> locked -> reviewing -> reviewed.
"""

from __future__ import annotations

import time
import re

import database
from models import (
    DocType,
    DocumentSet,
    DOC_TYPE_LABELS,
    REQUIRED_DOC_TYPES,
    SetDocument,
    VersionDiff,
)
from utils.logger import get_logger

logger = get_logger(__name__)


def _archive_member_count(plain_text: str) -> int:
    match = re.search(r"(?:ZIP\s*)?包含\s*(\d+)\s*个文件", plain_text or "", re.IGNORECASE)
    return int(match.group(1)) if match else 0


def extraction_quality_gate(files: list[dict]) -> dict:
    """Decide whether full extraction review can be skipped safely.

    This gate only evaluates whether the machine has a complete, usable source
    snapshot. It deliberately does not decide any business finding. Partial or
    failed extraction must remain an exception path so a missing value cannot be
    mistaken for a clean result.
    """
    required = set(REQUIRED_DOC_TYPES)
    present = {str(item.get("doc_type") or "") for item in files}
    blockers: list[dict[str, str]] = []
    for doc_type in sorted(required - present):
        blockers.append({
            "doc_type": doc_type,
            "label": DOC_TYPE_LABELS.get(doc_type, doc_type),
            "reason": "资料未上传",
        })
    for item in files:
        if item.get("doc_type") not in required:
            continue
        status = str(item.get("extraction_status") or "pending")
        quality = str(item.get("extraction_quality") or "pending")
        label = str(item.get("label") or DOC_TYPE_LABELS.get(item.get("doc_type"), item.get("doc_type", "资料")))
        if status in {"failed", "cancelled"} or quality == "failed":
            reason = "提取失败，需要重试"
        elif status in {"pending", "queued", "extracting", "processing"}:
            reason = "提取尚未完成"
        elif status == "partial" or quality == "partial":
            reason = "提取不完整，不能自动跳过资料核查"
        elif status != "done":
            reason = f"提取状态为 {status}，尚未形成完整快照"
        elif quality not in {"", "complete"}:
            reason = f"提取质量为 {quality}，需要确认"
        else:
            continue
        blockers.append({"doc_type": str(item.get("doc_type")), "label": label, "reason": reason})
    return {
        "status": "block" if blockers else "pass",
        "title": "需要处理提取异常" if blockers else "资料已通过自动质量门禁",
        "note": (
            "只显示异常资料，处理后才能锁定审核快照。"
            if blockers else "四份资料均形成完整、可追溯的提取快照，可直接进入审核结果。"
        ),
        "blockers": blockers,
        "manual_review_required": bool(blockers),
        "auto_gate_allowed": not bool(blockers),
    }


class DocumentSetService:

    @staticmethod
    async def create_set(employee_id: str) -> DocumentSet:
        t0 = time.monotonic()
        set_id = await database.create_document_set_async(employee_id)
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.info("set_created", set_id=set_id, employee_id=employee_id,
                    duration_ms=duration_ms)
        return DocumentSet(set_id=set_id, status="incomplete", employee_id=employee_id)

    @staticmethod
    async def add_document(
        set_id: str, doc_type: DocType, filename: str,
        file_size_kb: int = 0, replace_doc_id: str = "",
        plain_text: str = "", html_content: str = "",
    ) -> SetDocument:
        t0 = time.monotonic()
        doc_id = await database.add_document_to_set_async(
            set_id=set_id, doc_type=doc_type, filename=filename,
            file_size_kb=file_size_kb, replace_doc_id=replace_doc_id,
            plain_text=plain_text, html_content=html_content,
        )
        duration_ms = round((time.monotonic() - t0) * 1000)
        logger.info("document_added", set_id=set_id, doc_type=doc_type,
                    filename=filename, doc_id=doc_id, file_size_kb=file_size_kb,
                    is_replacement=bool(replace_doc_id), duration_ms=duration_ms)
        return SetDocument(doc_id=doc_id, set_id=set_id, doc_type=doc_type,
                           filename=filename, file_size_kb=file_size_kb,
                           doc_version=1, extraction_status="pending")

    @staticmethod
    async def delete_document(set_id: str, doc_id: str, employee_id: str) -> bool:
        """Delete a document and all related versions (children + parent) from a set."""
        from fastapi import HTTPException
        ds = await DocumentSetService.get_set(set_id)
        if not ds:
            raise HTTPException(404, f"文档集不存在: {set_id}")

        # Build full picture: find target, its parent, and its children
        target = None
        parent_id = ""
        children: list[str] = []
        siblings: list[str] = []
        for d in ds.documents:
            if d.doc_id == doc_id:
                target = d
                parent_id = d.parent_doc_id
            elif d.parent_doc_id == doc_id:
                children.append(d.doc_id)
        if not target:
            raise HTTPException(404, f"文档不存在: {doc_id}")

        # Find siblings (other children of the same parent)
        if parent_id:
            for d in ds.documents:
                if d.parent_doc_id == parent_id and d.doc_id != doc_id:
                    siblings.append(d.doc_id)

        deleted_count = 0
        # Delete children of target
        for child_id in children:
            await database.delete_document_async(child_id)
            deleted_count += 1
        # Delete target
        await database.delete_document_async(doc_id)
        deleted_count += 1
        # If target was a child (has parent) and no siblings remain, delete parent too
        if parent_id and not siblings:
            await database.delete_document_async(parent_id)
            deleted_count += 1

        logger.info("document_deleted", set_id=set_id, doc_id=doc_id,
                    doc_type=target.doc_type, filename=target.filename,
                    total_deleted=deleted_count, user=employee_id)
        return True

    @staticmethod
    async def get_set(set_id: str) -> DocumentSet | None:
        raw = await database.get_document_set_async(set_id)
        if not raw:
            return None
        docs = [SetDocument(**d) for d in raw["documents"]]
        return DocumentSet(
            set_id=raw["set_id"], status=raw["status"],
            documents=docs, created_at=raw["created_at"],
            employee_id=raw["employee_id"],
            project_group_id=raw.get("project_group_id", ""),
            project_group_name=raw.get("project_group_name", ""),
            missing_types=raw["missing_types"],
        )

    @staticmethod
    async def get_set_overview(set_id: str) -> dict | None:
        """Get lightweight summary with readiness info and version counts."""
        ds = await DocumentSetService.get_set(set_id)
        if not ds:
            return None

        root_docs: dict[str, dict] = {}
        for d in ds.documents:
            if not d.parent_doc_id:
                root_docs[d.doc_type] = {
                    "doc_id": d.doc_id, "doc_type": d.doc_type,
                    "label": DOC_TYPE_LABELS.get(d.doc_type, d.doc_type),
                    "filename": d.filename, "file_size_kb": d.file_size_kb,
                    "latest_version": d.doc_version,
                    "extraction_status": d.extraction_status,
                    "extraction_quality": d.extraction_quality,
                    "extraction_meta": d.extraction_meta,
                    "extraction_error": d.extraction_error,
                    "reviewed_by": d.reviewed_by,
                    "reviewed_at": d.reviewed_at,
                    "member_count": _archive_member_count(d.plain_text) if d.doc_type == "original_records" else 0,
                    "required": d.doc_type in REQUIRED_DOC_TYPES,
                    "versions": [],
                }

        for d in ds.documents:
            if d.parent_doc_id and d.doc_type in root_docs:
                root_docs[d.doc_type]["versions"].append({
                    "doc_id": d.doc_id, "doc_version": d.doc_version,
                    "filename": d.filename, "created_at": d.created_at,
                })

        # Update root entries with latest version's info
        for rd in root_docs.values():
            if rd["versions"]:
                rd["latest_version"] = max(v["doc_version"] for v in rd["versions"])
                # Find the latest version and use its doc_id / filename / extraction_status
                latest = max(rd["versions"], key=lambda v: v["doc_version"])
                for d in ds.documents:
                    if d.doc_id == latest["doc_id"]:
                        rd["doc_id"] = d.doc_id
                        rd["filename"] = d.filename
                        rd["extraction_status"] = d.extraction_status
                        rd["extraction_quality"] = d.extraction_quality
                        rd["extraction_meta"] = d.extraction_meta
                        rd["extraction_error"] = d.extraction_error
                        rd["reviewed_by"] = d.reviewed_by
                        rd["reviewed_at"] = d.reviewed_at
                        rd["file_size_kb"] = d.file_size_kb
                        rd["member_count"] = _archive_member_count(d.plain_text) if d.doc_type == "original_records" else 0
                        break

        present = set(root_docs.keys())
        missing = [t for t in REQUIRED_DOC_TYPES if t not in present]
        # Ready when every required document has usable extraction output.
        # "partial" is reviewable but will be surfaced as a system issue.
        is_ready = len(missing) == 0 and all(
            rd["extraction_status"] in {"done", "partial"}
            for rd in root_docs.values()
            if rd["required"]
        )
        selected_standards = await database.get_document_set_standards_async(set_id)
        quality_gate = extraction_quality_gate(list(root_docs.values()))
        list_item = next(
            (item for item in await database.list_document_sets_async(ds.employee_id)
             if item["set_id"] == set_id),
            {},
        )

        return {
            "set_id": ds.set_id, "status": ds.status,
            "source_status": ds.status,
            "workflow_status": list_item.get("status", ds.status),
            "employee_id": ds.employee_id, "created_at": ds.created_at,
            "updated_at": list_item.get("updated_at", ds.created_at),
            "title": list_item.get("title", ""),
            "latest_run_status": list_item.get("latest_run_status", ""),
            "finding_count": list_item.get("finding_count", 0),
            "pending_count": list_item.get("pending_count", 0),
            "project_group_id": ds.project_group_id,
            "project_group_name": ds.project_group_name,
            "files": list(root_docs.values()),
            "missing_types": missing, "is_ready": is_ready,
            "extraction_gate": quality_gate,
            "standards": [standard.model_dump() for standard in selected_standards],
            "standard_review_enabled": bool(selected_standards),
        }

    @staticmethod
    async def list_sets(employee_id: str = "") -> list[dict]:
        return await database.list_document_sets_async(employee_id)

    @staticmethod
    async def lock_set(set_id: str) -> bool:
        ok = await database.lock_document_set_async(set_id)
        if ok:
            logger.info("set_locked", set_id=set_id)
        else:
            logger.warning("set_lock_failed", set_id=set_id,
                          reason="status not incomplete/revision or set not found")
        return ok

    @staticmethod
    async def create_revision(set_id: str) -> bool:
        ds = await DocumentSetService.get_set(set_id)
        if not ds:
            return False
        if ds.status == "revision":
            return True
        if ds.status != "reviewed":
            return False
        ok = await database.transition_set_status_async(
            set_id, "revision", ("reviewed",),
        )
        if ok:
            logger.info("set_revision_created", set_id=set_id)
        return ok

    @staticmethod
    async def get_version_history(set_id: str, doc_type: DocType) -> list[SetDocument]:
        versions = await database.get_doc_versions_async(set_id, doc_type)
        return [SetDocument(**v) for v in versions]

    @staticmethod
    async def diff_versions(old_doc_id: str, new_doc_id: str) -> list[VersionDiff]:
        t0 = time.monotonic()
        diffs = await database.diff_doc_versions_async(old_doc_id, new_doc_id)
        duration_ms = round((time.monotonic() - t0) * 1000)
        changed = sum(1 for d in diffs if d.get("changed"))
        logger.info("version_diff", old_doc_id=old_doc_id, new_doc_id=new_doc_id,
                    total_fields=len(diffs), changed_fields=changed,
                    duration_ms=duration_ms)
        return [VersionDiff(**d) for d in diffs]

    @staticmethod
    async def save_metadata(set_id: str, doc_id: str, fields: list[dict]) -> int:
        count = await database.save_extracted_metadata_async(set_id, doc_id, fields)
        logger.info("metadata_saved", set_id=set_id, doc_id=doc_id,
                    field_count=count)
        return count

    @staticmethod
    async def get_metadata(doc_id: str) -> list[dict]:
        return await database.get_extracted_metadata_async(doc_id)
