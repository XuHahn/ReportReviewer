"""Incremental JSON parser for DeepSeek streaming Stage 2 output.

Extracts complete review_item objects as they appear in the stream,
so the SSE endpoint can push them one-by-one to the frontend.
"""

import json


class IncrementalReviewParser:
    """Character-level state machine that incrementally parses streaming JSON.

    Public interface:
        feed(chunk: str) -> list[dict]   # returns newly complete review_items
        finalize() -> dict               # returns {overall_result, summary}
    """

    def __init__(self):
        self._state = "INIT"  # INIT | IN_ARRAY | POST_ARRAY | DONE
        self._buffer = ""
        self._prefix = ""  # text before '[' (contains overall_result, summary)
        self._extracted: list[dict] = []

    # ── public API ──────────────────────────────────────────────────────

    def feed(self, chunk: str) -> list[dict]:
        """Feed new text from the stream. Returns newly complete review items."""
        self._buffer += chunk
        new_items: list[dict] = []
        if self._state in ("INIT", "IN_ARRAY"):
            new_items = self._try_extract()
        return new_items

    def finalize(self) -> dict:
        """Call when the stream ends. Returns {overall_result, summary}."""
        # Reconstruct: prefix (before '[') + ']' + post-array tail
        rest = (self._prefix.strip() + "]" + self._buffer.strip()).strip()
        result: dict = {}
        if rest:
            try:
                parsed = json.loads(rest)
                result["overall_result"] = parsed.get("overall_result", "error")
                result["summary"] = parsed.get("summary", "")
            except json.JSONDecodeError:
                result["overall_result"] = self._field_value(rest, "overall_result") or "error"
                result["summary"] = self._field_value(rest, "summary") or ""
        if "overall_result" not in result:
            result["overall_result"] = "error"
        if "summary" not in result:
            result["summary"] = ""
        return result

    @property
    def all_items(self) -> list[dict]:
        return list(self._extracted)

    # ── internal extraction ─────────────────────────────────────────────

    def _try_extract(self) -> list[dict]:
        """Scan buffer for complete JSON objects within the review_items array."""
        new_items: list[dict] = []

        # Phase 1 — find the review_items array opening
        if self._state == "INIT":
            idx = self._buffer.find('"review_items"')
            if idx == -1:
                return []
            bracket = self._buffer.find("[", idx)
            if bracket == -1:
                return []
            self._prefix = self._buffer[:bracket]
            self._buffer = self._buffer[bracket + 1:]
            self._state = "IN_ARRAY"

        # Phase 2 — extract complete objects from inside the array
        objects, consumed = self._extract_objects(self._buffer)
        for obj_str in objects:
            try:
                obj = json.loads(obj_str)
                self._extracted.append(obj)
                new_items.append(obj)
            except json.JSONDecodeError:
                pass
        self._buffer = self._buffer[consumed:]

        # Check if the array has closed
        trimmed = self._buffer.lstrip()
        if trimmed and trimmed[0] == "]":
            self._state = "POST_ARRAY"
            close_idx = self._buffer.index("]")
            self._buffer = self._buffer[close_idx + 1:]

        return new_items

    @staticmethod
    def _extract_objects(buffer: str) -> tuple[list[str], int]:
        """Scan buffer for balanced {} objects. Returns (json_strings, chars_consumed)."""
        objects: list[str] = []
        offset = 0
        n = len(buffer)

        while offset < n:
            while offset < n and buffer[offset] in " \t\n\r,":
                offset += 1
            if offset >= n:
                break
            if buffer[offset] == "]":
                break
            if buffer[offset] != "{":
                offset += 1
                continue

            end = IncrementalReviewParser._find_matching_brace(buffer, offset)
            if end == -1:
                break  # incomplete — retry on next feed

            objects.append(buffer[offset:end + 1])
            offset = end + 1

        return objects, offset

    @staticmethod
    def _find_matching_brace(text: str, start: int) -> int:
        """Find index of '}' matching '{' at position start. -1 if not found."""
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\" and in_string:
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i
        return -1

    # ── best-effort field extraction from partial JSON ──────────────────

    @staticmethod
    def _field_value(text: str, field_name: str) -> str | None:
        """Extract a string field value from possibly-malformed JSON."""
        import re
        pattern = rf'"{field_name}"\s*:\s*"((?:\\.|[^"\\])*)"'
        m = re.search(pattern, text)
        if m:
            val = m.group(1)
            val = val.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
            return val
        return None
