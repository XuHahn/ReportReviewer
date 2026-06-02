import re
import html as html_mod
from bs4 import BeautifulSoup, NavigableString


class TextHighlighter:

    # Search at increasing scope: cell → row → table body → table
    BLOCK_TAGS = ['td', 'th', 'tr', 'tbody', 'table']

    @staticmethod
    def highlight_text(full_text: str, review_items: list) -> tuple[str, list[int]]:
        """Highlight plain text (fallback). Returns (html, matched_indices)."""
        result = full_text
        sorted_items = sorted(
            enumerate(review_items),
            key=lambda x: len(x[1].get("original_text", "")),
            reverse=True,
        )
        matched = set()
        for orig_idx, item in sorted_items:
            original = item.get("original_text", "")
            severity = item.get("severity", "error")
            description = item.get("error_description", "")
            suggestion = item.get("suggestion", "")
            if not original:
                matched.add(orig_idx)
                continue
            reason = html_mod.escape(description)
            if suggestion:
                reason += f" → {suggestion}"
            escaped = re.escape(original)
            replacement = (
                f'<mark class="error-highlight error-{severity}" '
                f'data-error-idx="{orig_idx}" '
                f'title="{reason}">'
                f"{original}"
                f'<sup class="error-idx-badge">{orig_idx + 1}</sup>'
                f"</mark>"
            )
            new_result = re.sub(escaped, replacement, result)
            if new_result != result:
                matched.add(orig_idx)
                result = new_result
        return result.replace("\n", "<br>"), list(matched)

    @staticmethod
    def highlight_html(html_content: str, review_items: list) -> tuple[str, list[int]]:
        """Highlight HTML while preserving formatting. Returns (html, matched_indices)."""
        wrapped = f'<div id="report-root">{html_content}</div>'
        soup = BeautifulSoup(wrapped, "lxml")

        sorted_items = sorted(
            enumerate(review_items),
            key=lambda x: len(x[1].get("original_text", "")),
            reverse=True,
        )
        matched = set()

        for orig_idx, item in sorted_items:
            original = item.get("original_text", "")
            severity = item.get("severity", "error")
            description = html_mod.escape(item.get("error_description", ""))
            suggestion = html_mod.escape(item.get("suggestion", ""))
            reason = description
            if suggestion:
                reason += f" → {suggestion}"
            if not original:
                matched.add(orig_idx)
                continue

            badge = f'<sup class="error-idx-badge">{orig_idx + 1}</sup>'

            # Pass 1: exact substring match in a single text node
            if TextHighlighter._try_exact_match(soup, original, severity, orig_idx, reason, badge):
                matched.add(orig_idx)
                continue

            # Pass 2: flexible whitespace match in a single text node
            if TextHighlighter._try_flexible_match(soup, original, severity, orig_idx, reason, badge):
                matched.add(orig_idx)
                continue

            # Pass 3: word-order match across HTML tags within containing elements
            if TextHighlighter._try_block_match(soup, original, severity, orig_idx, reason, badge):
                matched.add(orig_idx)
                continue

            # Pass 4: body-level match across all HTML boundaries
            if TextHighlighter._try_body_match(soup, original, severity, orig_idx, reason, badge):
                matched.add(orig_idx)
                continue

            # Pass 5: split by \n and match the longest segment
            if TextHighlighter._try_newline_split_match(soup, original, severity, orig_idx, reason, badge):
                matched.add(orig_idx)
                continue

            # Pass 6: fuzzy match — drop one word at a time
            if TextHighlighter._try_fuzzy_match(soup, original, severity, orig_idx, reason, badge):
                matched.add(orig_idx)
                continue

        root = soup.find("div", id="report-root")
        html_result = "".join(str(c) for c in root.contents) if root else html_content
        return html_result, list(matched)

    @staticmethod
    def _make_mark(display: str, severity: str, idx: int, reason: str, badge: str) -> str:
        """Build the <mark> HTML with the actual matched text."""
        return (
            f'<mark class="error-highlight error-{severity}" '
            f'data-error-idx="{idx}" title="{reason}">'
            f"{display}{badge}</mark>"
        )

    @staticmethod
    def _try_exact_match(soup, original: str, sev: str, idx: int,
                         reason: str, badge: str) -> bool:
        for text_node in soup.find_all(string=True):
            if not isinstance(text_node, NavigableString):
                continue
            parent = text_node.parent
            if parent and parent.name == "mark":
                continue
            if original in text_node:
                mark_html = TextHighlighter._make_mark(original, sev, idx, reason, badge)
                new_str = str(text_node).replace(original, mark_html)
                text_node.replace_with(BeautifulSoup(new_str, "html.parser"))
                return True
        return False

    @staticmethod
    def _get_words(text: str) -> list[str]:
        """Split text into words, treating | as whitespace (AI sometimes uses | as column separator)."""
        return [w for w in re.split(r'[\s|]+', text) if w]

    @staticmethod
    def _try_flexible_match(soup, original: str, sev: str, idx: int,
                             reason: str, badge: str) -> bool:
        """Match with whitespace tolerance — collapse multiple spaces for comparison."""
        words = TextHighlighter._get_words(original)
        if len(words) < 2:
            return False
        pattern = re.compile(r'\s+'.join(re.escape(w) for w in words))

        for text_node in soup.find_all(string=True):
            if not isinstance(text_node, NavigableString):
                continue
            parent = text_node.parent
            if parent and parent.name == "mark":
                continue
            node_text = str(text_node)
            m = pattern.search(node_text)
            if m:
                actual = m.group()
                mark_html = TextHighlighter._make_mark(actual, sev, idx, reason, badge)
                new_str = node_text.replace(actual, mark_html)
                text_node.replace_with(BeautifulSoup(new_str, "html.parser"))
                return True
        return False

    @staticmethod
    def _try_block_match(soup, original: str, sev: str, idx: int,
                          reason: str, badge: str) -> bool:
        """Match text that spans across HTML tags within a containing element.

        Uses a regex built from the original's words with flexible tag/whitespace
        separators. This handles:
        - \\n in original (words split by \\n match across tag boundaries)
        - Mammoth's concatenated cell text (no spaces between adjacent <td> cells)
        - AI whitespace differences (extra/missing spaces around punctuation)
        """
        words = TextHighlighter._get_words(original)
        if len(words) < 2:
            return False

        tagsep = r'(?:<[^>]*>|\s)*'
        pattern = re.compile(tagsep.join(re.escape(w) for w in words))

        for tag_name in TextHighlighter.BLOCK_TAGS:
            for tag in soup.find_all(tag_name):
                if tag.find("mark"):
                    continue

                inner = "".join(str(c) for c in tag.contents)
                m = pattern.search(inner)
                if m:
                    actual = m.group()
                    mark_html = TextHighlighter._make_mark(actual, sev, idx, reason, badge)
                    new_inner = inner[:m.start()] + mark_html + inner[m.end():]
                    tag.clear()
                    tag.append(BeautifulSoup(new_inner, "html.parser"))
                    return True

        return False

    @staticmethod
    def _try_body_match(soup, original: str, sev: str, idx: int,
                         reason: str, badge: str) -> bool:
        """Match text that spans across different tables/sections at the body level.

        This is a last-resort exact match that searches the entire document body
        for text that crosses major structural boundaries (separate tables, etc).
        """
        words = TextHighlighter._get_words(original)
        if len(words) < 2:
            return False

        root = soup.find("div", id="report-root")
        if not root or root.find("mark"):
            return False

        inner = "".join(str(c) for c in root.contents)
        tagsep = r'(?:<[^>]*>|\s)*'
        pattern = re.compile(tagsep.join(re.escape(w) for w in words))
        m = pattern.search(inner)
        if m:
            actual = m.group()
            mark_html = TextHighlighter._make_mark(actual, sev, idx, reason, badge)
            new_inner = inner[:m.start()] + mark_html + inner[m.end():]
            root.clear()
            root.append(BeautifulSoup(new_inner, "html.parser"))
            return True
        return False

    @staticmethod
    def _try_newline_split_match(soup, original: str, sev: str, idx: int,
                                  reason: str, badge: str) -> bool:
        """Split original by \\n and try to match the longest segment.

        When the AI extracts text spanning multiple table rows or sections,
        the \\n in original_text represents structural boundaries. We split
        by \\n and try to match each segment independently. If any segment
        representing at least 40% of the original text matches, we highlight it.
        """
        if '\n' not in original:
            return False

        segments = [s.strip() for s in original.split('\n') if s.strip()]
        if len(segments) < 2:
            return False

        # Try each segment — find the first one that can be matched
        for segment in sorted(segments, key=len, reverse=True):
            if len(segment) < 10:
                continue
            if TextHighlighter._try_exact_match(soup, segment, sev, idx, reason, badge):
                return True
            if TextHighlighter._try_flexible_match(soup, segment, sev, idx, reason, badge):
                return True
            if TextHighlighter._try_block_match(soup, segment, sev, idx, reason, badge):
                return True

        return False

    @staticmethod
    def _try_fuzzy_match(soup, original: str, sev: str, idx: int,
                          reason: str, badge: str) -> bool:
        """Fuzzy match by skipping words that may be AI hallucinations.

        The AI sometimes duplicates words (e.g. '-- --' when the doc has one '--')
        or includes words from column headers combined with values from different rows.
        Try: (1) dropping from end, (2) skipping individual words.
        """
        words = TextHighlighter._get_words(original)
        if len(words) < 4:
            return False

        tagsep = r'(?:<[^>]*>|\s)*'
        root = soup.find("div", id="report-root")
        if not root:
            return False

        # Strategy 1: drop words from the end
        for drop_count in range(1, min(len(words) // 2, 6) + 1):
            sub_words = words[:len(words) - drop_count]
            if len(sub_words) < 3:
                continue
            pattern = re.compile(tagsep.join(re.escape(w) for w in sub_words))
            inner = "".join(str(c) for c in root.contents)
            m = pattern.search(inner)
            if m:
                actual = m.group()
                mark_html = TextHighlighter._make_mark(actual, sev, idx, reason, badge)
                new_inner = inner[:m.start()] + mark_html + inner[m.end():]
                root.clear()
                root.append(BeautifulSoup(new_inner, "html.parser"))
                return True

        # Strategy 2: skip one word at a time (handles duplicated words in the middle)
        for skip_pos in range(len(words)):
            sub_words = words[:skip_pos] + words[skip_pos+1:]
            if len(sub_words) < 3:
                continue
            pattern = re.compile(tagsep.join(re.escape(w) for w in sub_words))
            inner = "".join(str(c) for c in root.contents)
            m = pattern.search(inner)
            if m:
                actual = m.group()
                mark_html = TextHighlighter._make_mark(actual, sev, idx, reason, badge)
                new_inner = inner[:m.start()] + mark_html + inner[m.end():]
                root.clear()
                root.append(BeautifulSoup(new_inner, "html.parser"))
                return True

        return False
