import io
import re
import PyPDF2
import mammoth
from dataclasses import dataclass


@dataclass
class ParsedReport:
    plain_text: str
    html_content: str


class ReportParser:

    # Block-level tags that imply paragraph breaks
    BLOCK_TAG_RE = re.compile(
        r'</?(?:p|h[1-6]|tr|table|ul|ol|li|div|br|hr|section|article|header|footer)[^>]*/?>',
        re.IGNORECASE,
    )

    # PDF heading detection: numbered sections like "1. 概述", "2.1 测试方法"
    _PDF_HEADING_RE = re.compile(
        r'^(\d+(?:\.\d+)*\.?)\s+(.{2,100})$',
    )
    # Chinese numbered: "一、简介"
    _PDF_CN_HEADING_RE = re.compile(
        r'^([一二三四五六七八九十]+)[、．.](.{2,100})$',
    )
    # Short line that looks like a title (all caps or contains keyword)
    _PDF_TITLE_RE = re.compile(
        r'^(测试报告|检测报告|检验报告|试验报告|报告编号[：:])',
    )

    @classmethod
    async def parse_pdf(cls, file_bytes: bytes) -> ParsedReport:
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        texts = []
        htmls = ['<div class="report-content">']
        for page in reader.pages:
            page_text = page.extract_text()
            if not page_text:
                continue
            texts.append(page_text)

            # Detect headings and wrap in h2/h3/h4 tags
            lines = page_text.split('\n')
            tagged_lines = []
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    tagged_lines.append('<br>')
                    continue

                tag = cls._classify_pdf_line(stripped)
                if tag:
                    tagged_lines.append(f'<{tag}>{stripped}</{tag}>')
                else:
                    tagged_lines.append(stripped)

            page_html = '<br>'.join(tagged_lines)
            htmls.append(f'<div class="pdf-page">{page_html}</div>')
        htmls.append("</div>")
        full_text = "\n\n".join(texts)
        return ParsedReport(plain_text=full_text, html_content="\n".join(htmls))

    @classmethod
    def _classify_pdf_line(cls, line: str) -> str | None:
        """Return an HTML heading tag (h1-h4) if the line looks like a heading, else None."""
        # Report title patterns
        if cls._PDF_TITLE_RE.match(line):
            return 'h1'

        # Numbered sections: depth determines heading level
        m = cls._PDF_HEADING_RE.match(line)
        if m:
            depth = m.group(1).count('.') + 1
            if depth <= 2:
                return 'h2'
            elif depth == 3:
                return 'h3'
            return 'h4'

        # Chinese numbered sections
        if cls._PDF_CN_HEADING_RE.match(line):
            return 'h2'

        return None

    @staticmethod
    async def parse_docx(file_bytes: bytes) -> ParsedReport:
        result = mammoth.convert_to_html(
            io.BytesIO(file_bytes),
            convert_image=mammoth.images.img_element(lambda image: {"src": ""}),
        )
        html_content = result.value
        html_content = re.sub(r'<img\b[^>]*>', '', html_content)

        # Preserve paragraph structure: replace block tags with newlines before stripping
        with_breaks = ReportParser.BLOCK_TAG_RE.sub('\n', html_content)
        plain_text = re.sub(r"<[^>]+>", "", with_breaks)
        # Collapse whitespace but keep paragraph breaks
        plain_text = re.sub(r"[ \t\r]+", " ", plain_text)
        plain_text = re.sub(r"\n{3,}", "\n\n", plain_text)
        plain_text = plain_text.strip()

        return ParsedReport(plain_text=plain_text, html_content=html_content)

    @classmethod
    async def parse(cls, file_bytes: bytes, filename: str) -> ParsedReport:
        lower = filename.lower()
        if lower.endswith(".pdf"):
            return await cls.parse_pdf(file_bytes)
        elif lower.endswith(".docx"):
            return await cls.parse_docx(file_bytes)
        else:
            raise ValueError(f"不支持的文件格式: {filename}")

