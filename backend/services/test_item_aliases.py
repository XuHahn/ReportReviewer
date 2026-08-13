"""Confirmed test item aliases.

This module is intentionally conservative: only aliases marked as
``confirmed`` participate in deterministic matching.  Candidate categories
remain review hints and do not make two test items equal.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


_DEFAULT_ALIAS_PATH = Path(__file__).resolve().parents[1] / "data" / "test_item_aliases.json"


def _alias_path() -> Path:
    return Path(os.getenv("TEST_ITEM_ALIAS_PATH") or _DEFAULT_ALIAS_PATH)


def normalize_alias_text(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("：", ":").replace("；", ";").replace("，", ",")
    text = re.sub(r"^\s*\d+(?:\.\d+)*\s*", "", text)
    text = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", text)
    text = re.sub(r"[\s\t\r\n:：;；,，.。/／\\_\-—－·•]+", "", text)
    text = re.sub(r"[()（）\\[\\]【】{}《》<>〈〉]", "", text)
    return text.lower().strip()


@lru_cache(maxsize=1)
def load_alias_registry() -> dict[str, Any]:
    path = _alias_path()
    if not path.exists():
        return {"groups": []}
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _confirmed_alias_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for group in load_alias_registry().get("groups", []):
        if group.get("status") != "confirmed":
            continue
        canonical = str(group.get("canonical") or "").strip()
        if not canonical:
            continue
        for alias in group.get("aliases", []) or []:
            normalized = normalize_alias_text(alias)
            if normalized:
                index[normalized] = canonical
    return index


@lru_cache(maxsize=1)
def _group_by_canonical() -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for group in load_alias_registry().get("groups", []):
        canonical = str(group.get("canonical") or "").strip()
        if canonical:
            groups[canonical] = group
    return groups


def alias_key_for(value: str) -> str:
    canonical = _confirmed_alias_index().get(normalize_alias_text(value))
    return f"ALIAS:{canonical}" if canonical else ""


def alias_display_name(alias_key: str) -> str:
    canonical = str(alias_key or "").removeprefix("ALIAS:")
    group = _group_by_canonical().get(canonical, {})
    return str(group.get("display_name") or canonical or alias_key)


def alias_category(alias_key: str) -> str:
    canonical = str(alias_key or "").removeprefix("ALIAS:")
    group = _group_by_canonical().get(canonical, {})
    return str(group.get("category") or "")

