"""Deterministic source windows for execution-level evidence.

A report page can contain several samples or modes.  A token found anywhere on
the page is not evidence for every execution block on that page.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable

from services.evidence_graph_extraction import EvidenceGraphDocumentUnit


def _compact(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKC", str(value or "")).casefold()
        if not char.isspace()
    )


def _contains(source: str, value: str) -> bool:
    return bool(value) and _compact(value) in _compact(source)


def execution_windows(source: str, sample_id: str, mode: str) -> list[tuple[int, int]]:
    """Return page-local ranges belonging to one explicit sample/mode."""
    marker = re.compile(r"(?i)(?:Test\s*Mode|测试模式|工作模式)")
    markers = list(marker.finditer(source))
    windows: list[tuple[int, int]] = []
    for index, match in enumerate(markers):
        start = match.start()
        end = markers[index + 1].start() if index + 1 < len(markers) else len(source)
        segment = source[start:end]
        if (not sample_id or _contains(segment, sample_id)) and (
            not mode or _contains(segment, mode)
        ):
            windows.append((start, end))
    if windows or not sample_id:
        return windows

    # Fallback for reports whose mode label is not recognizable.  Bound the
    # search by adjacent sample identifiers instead of using the whole page.
    sample_pattern = re.compile(re.escape(sample_id), re.IGNORECASE)
    samples = list(sample_pattern.finditer(source))
    for index, match in enumerate(samples):
        start = samples[index - 1].end() if index else 0
        end = samples[index + 1].start() if index + 1 < len(samples) else len(source)
        segment = source[start:end]
        if not mode or _contains(segment, mode):
            windows.append((start, end))
    return windows


def locate_execution_verdict(
    units: Iterable[EvidenceGraphDocumentUnit],
    *,
    sample_id: str,
    mode: str,
    verdict: str,
) -> tuple[EvidenceGraphDocumentUnit, str] | None:
    compact = _compact(verdict)
    if any(token in compact for token in ("不pass", "fail", "不符合", "不合格")):
        pattern = re.compile(r"(?:不\s*Pass|\bFail\b|不符合|不合格)", re.IGNORECASE)
        positive = False
    elif compact in {"pass", "符合", "合格", "通过"}:
        pattern = re.compile(r"\bPass\b|符合|合格|通过", re.IGNORECASE)
        positive = True
    else:
        return None
    for unit in units:
        if sample_id and not _contains(unit.native_text, sample_id):
            continue
        for start, end in execution_windows(unit.native_text, sample_id, mode):
            segment = unit.native_text[start:end]
            matches = pattern.finditer(segment)
            match = next((
                candidate for candidate in matches
                if not positive or not _compact(segment[:candidate.start()]).endswith("不")
            ), None)
            if not match:
                continue
            quote_start = max(0, start)
            quote_end = min(end, start + match.end())
            return unit, unit.native_text[quote_start:quote_end].strip()
    return None
