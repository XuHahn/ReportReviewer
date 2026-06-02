"""Suppression filter: matches new review items against previously suppressed
patterns using difflib text similarity."""

from datetime import datetime
from difflib import SequenceMatcher

THRESHOLD = 0.8


def _normalize(s: str) -> str:
    return ' '.join(s.lower().split())


def text_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def filter_review_items(review_items, patterns):
    """Auto-mark review_items as false_positive when they match a suppressed pattern.

    Modifies items in-place. Only affects items whose human_status is still
    ``"pending"`` (the default for fresh review results).
    """
    if not patterns:
        return review_items
    now = datetime.now().isoformat()
    for item in review_items:
        if getattr(item, 'human_status', 'pending') != 'pending':
            continue
        item_text = getattr(item, 'original_text', '')
        for p in patterns:
            if text_similarity(item_text, p['original_text']) > THRESHOLD:
                item.human_status = 'false_positive'
                item.human_comment = '自动抑制：匹配历史误报模式'
                item.annotated_by = 'system'
                item.annotated_at = now
                break
    return review_items
