import fitz

from utils import pdf_utils


def _pdf(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def test_docx_text_uses_rendered_pdf_instead_of_package_xml(monkeypatch):
    monkeypatch.setattr(
        "services.document_unitizer._office_to_pdf",
        lambda _source, _filename: _pdf("VISIBLE PASS"),
    )

    assert "VISIBLE PASS" in pdf_utils.extract_docx_text(b"not-a-real-docx")
