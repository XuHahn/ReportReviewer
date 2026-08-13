"""Parse original record filenames into structured metadata.

Filename format (7 segments separated by _):
  {report_id}_{test_item_code} {test_item_name}_{sequence}_{mode}_{type}_{operator}_{timestamp}.pdf

All extraction is deterministic — no AI, no PDF access required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re

CATEGORY_EQIC = "EQIC"
CATEGORY_EQIR = "EQIR"
CATEGORY_EQMC = "EQMC"
CATEGORY_EQMR = "EQMR"
CATEGORY_OTHER = "OTHER"

_CATEGORY_PREFIXES: dict[str, str] = {
    "EQIC": CATEGORY_EQIC,
    "EQIR": CATEGORY_EQIR,
    "EQMC": CATEGORY_EQMC,
    "EQMR": CATEGORY_EQMR,
}


@dataclass
class RawRecordMeta:
    """Metadata from a single raw record filename."""
    filename: str = ""
    report_id: str = ""
    test_item_code: str = ""
    test_item_name: str = ""
    sequence: str = ""
    test_mode: str = ""
    doc_type: str = "原始记录"
    operator: str = ""
    record_timestamp: str = ""
    category: str = ""
    # Stable identity inside the uploaded ZIP document version.  Filenames are
    # presentation metadata and may be repaired for display.
    archive_member_index: int = 0
    archive_member_hash: str = ""

    # Populated later by pdf_extractor
    raw_text: str = ""
    _header_fields: dict = field(default_factory=dict)
    semantic_data: dict = field(default_factory=dict)
    table_inventory: list[dict] = field(default_factory=list)
    extraction_sources: list[str] = field(default_factory=lambda: ["code"])

    def get_header(self, key: str, default: str = "") -> str:
        """Safe accessor for header fields extracted from PDF."""
        return self._header_fields.get(key, default)

    @property
    def test_date_str(self) -> str:
        # The date printed in the reviewed record is authoritative. A vendor
        # filename often contains the PDF generation/review timestamp instead
        # of the execution date.
        header_date = str(self.get_header("test_date") or "").strip()
        match = re.search(r"(20\d{2})\D+(\d{1,2})\D+(\d{1,2})", header_date)
        if match:
            return f"{match.group(1)}/{int(match.group(2))}/{int(match.group(3))}"
        ts = self.record_timestamp
        if len(ts) >= 8:
            return f"{ts[:4]}/{int(ts[4:6])}/{int(ts[6:8])}"
        return ""


def classify_category(code: str) -> str:
    for prefix, cat in _CATEGORY_PREFIXES.items():
        if code.upper().startswith(prefix):
            return cat
    return CATEGORY_OTHER


def parse_filename(filename: str) -> RawRecordMeta | None:
    stem = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE)
    # Anchor the stable structural fields instead of counting underscores.
    # Test-item names and operator identifiers are free text and may themselves
    # contain underscores; sequence, mode, document type and timestamp delimit
    # those free-text regions without relying on a vendor-specific segment count.
    match = re.fullmatch(
        r"(?P<report_id>[^_]+)_(?P<item>.+)_(?P<sequence>\d+)_"
        r"(?P<mode>[^_]+)_(?P<doc_type>[^_]+)_(?P<operator>.+)_"
        r"(?P<timestamp>\d{8,20})",
        stem,
    )
    if match is None:
        return None

    item_parts = match.group("item").strip().split(None, 1)
    test_item_code = item_parts[0] if item_parts else ""
    test_item_name = item_parts[1] if len(item_parts) > 1 else ""

    return RawRecordMeta(
        filename=filename,
        report_id=match.group("report_id"),
        test_item_code=test_item_code,
        test_item_name=test_item_name,
        sequence=match.group("sequence"),
        test_mode=match.group("mode"),
        doc_type=match.group("doc_type"),
        operator=match.group("operator"),
        record_timestamp=match.group("timestamp"),
        category=classify_category(test_item_code),
    )


def parse_filename_or_fallback(filename: str) -> RawRecordMeta:
    """Return metadata for every PDF, even when a vendor renames the file."""
    parsed = parse_filename(filename)
    if parsed is not None:
        return parsed
    return RawRecordMeta(filename=filename, category=CATEGORY_OTHER)


def parse_filenames(filenames: list[str]) -> list[RawRecordMeta]:
    results: list[RawRecordMeta] = []
    for fname in filenames:
        meta = parse_filename(fname)
        if meta:
            results.append(meta)
    return results
