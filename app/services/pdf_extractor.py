from dataclasses import dataclass
from pathlib import Path
import fitz  # PyMuPDF


@dataclass
class ExtractedPdf:
    text: str
    page_count: int


class PdfExtractionError(Exception):
    """Raised when PDF extraction fails."""


def extract_text_from_pdf(pdf_path: Path) -> ExtractedPdf:
    """
    Extract selectable text from a PDF file.

    This works for normal PDFs such as lecture notes, reports, and exported slides.
    It does not perform OCR, so scanned/image-only PDFs will return little or no text.
    """
    if not pdf_path.exists():
        raise PdfExtractionError(f"PDF file not found: {pdf_path}")

    pages: list[str] = []

    try:
        with fitz.open(pdf_path) as document:
            page_count = document.page_count

            for page_index, page in enumerate(document, start=1):
                text = page.get_text("text").strip()
                if text:
                    pages.append(f"--- Page {page_index} ---\n{text}")

    except Exception as exc:
        raise PdfExtractionError(f"Could not read PDF file: {exc}") from exc

    return ExtractedPdf(text="\n\n".join(pages), page_count=page_count)
