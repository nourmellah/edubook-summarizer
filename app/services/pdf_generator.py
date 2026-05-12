from __future__ import annotations

from pathlib import Path
from datetime import datetime
from html import escape
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.services.text_polish import polish_summary_text


class PdfGenerationError(Exception):
    """Raised when summary PDF generation fails."""


QUESTION_RE = re.compile(
    r"^(?:Q|Question|Frage|Pregunta|Pergunta|Domanda|سؤال)\s*\d+[\.)]?\s*",
    flags=re.IGNORECASE,
)
ANSWER_RE = re.compile(
    r"^(?:Réponse|Reponse|Answer|Antwort|Respuesta|Resposta|Risposta|جواب|الإجابة|اجابة)\s*:",
    flags=re.IGNORECASE,
)


def _paragraph_text(text: str) -> str:
    """Escape text for ReportLab Paragraph while supporting a small safe subset of inline styling."""
    clean = escape(text)
    clean = clean.replace("&amp;nbsp;", " ")
    return clean


def _is_top_level_numbered_heading(line: str) -> bool:
    """Language-neutral fallback for numbered headings when Markdown is missing."""
    clean = line.strip()
    if QUESTION_RE.match(clean):
        return False
    match = re.match(r"^\d+(?:\.\d+)*[.)]?\s+(.+)$", clean)
    if not match:
        return False
    title = match.group(1).strip()
    if len(title) > 120 or title.endswith((".", ",", ";", "?", "!")):
        return False
    letters = [c for c in title if c.isalpha()]
    if not letters:
        return False
    uppercase_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
    return title.istitle() or uppercase_ratio > 0.12


def _markdown_line_to_paragraph(line: str, styles: dict) -> Paragraph | Spacer:
    clean_line = line.strip()

    if not clean_line:
        return Spacer(1, 7)

    if clean_line.startswith("# "):
        return Paragraph(_paragraph_text(clean_line[2:].strip()), styles["title"])

    if clean_line.startswith("## "):
        return Paragraph(_paragraph_text(clean_line[3:].strip()), styles["heading"])

    if clean_line.startswith("### "):
        return Paragraph(_paragraph_text(clean_line[4:].strip()), styles["subheading"])

    # Common LLM output sometimes omits markdown heading markers.
    if _is_top_level_numbered_heading(clean_line):
        return Paragraph(_paragraph_text(clean_line), styles["heading"])

    if re.match(r"^(chapter|chapitre|kapitel|cap[ií]tulo|capitulo|section|part|partie)\s+\d+\s*:", clean_line, flags=re.IGNORECASE):
        return Paragraph(_paragraph_text(clean_line), styles["subheading"])

    if QUESTION_RE.match(clean_line):
        return Paragraph(_paragraph_text(clean_line), styles["question"])

    if ANSWER_RE.match(clean_line):
        return Paragraph(_paragraph_text(clean_line), styles["answer"])

    if clean_line.startswith(("- ", "* ", "• ")):
        item = clean_line[2:].strip()
        return Paragraph(_paragraph_text(item), styles["bullet"], bulletText="•")

    return Paragraph(_paragraph_text(clean_line), styles["normal"])


def _draw_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#6b7280"))
    page_number = f"Page {doc.page}"
    canvas.drawRightString(A4[0] - 2 * cm, 1.05 * cm, page_number)
    canvas.drawString(2 * cm, 1.05 * cm, "Education AI Center - Summary")
    canvas.restoreState()


def generate_summary_pdf(
    title: str,
    summary_text: str,
    output_path: Path,
    source_filename: str | None = None,
    model_name: str | None = None,
) -> Path:
    """
    Generate a clean A4 PDF summary using ReportLab.

    The function supports simple Markdown-style headings and bullets,
    removes chatty LLM preambles, and renders a professional page footer.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        cleaned_summary = polish_summary_text(summary_text)

        doc = SimpleDocTemplate(
            str(output_path),
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=1.7 * cm,
            bottomMargin=1.8 * cm,
            title=title,
            author="Education AI Center",
        )

        base_styles = getSampleStyleSheet()

        styles = {
            "cover_title": ParagraphStyle(
                "CoverTitle",
                parent=base_styles["Title"],
                fontSize=21,
                leading=28,
                alignment=TA_CENTER,
                spaceAfter=14,
                textColor=colors.HexColor("#111827"),
            ),
            "metadata": ParagraphStyle(
                "Metadata",
                parent=base_styles["Normal"],
                fontSize=9,
                leading=13,
                textColor=colors.HexColor("#555555"),
                alignment=TA_CENTER,
                spaceAfter=16,
            ),
            "title": ParagraphStyle(
                "MainTitle",
                parent=base_styles["Heading1"],
                fontSize=18,
                leading=24,
                spaceBefore=14,
                spaceAfter=10,
                textColor=colors.HexColor("#111827"),
            ),
            "heading": ParagraphStyle(
                "SectionHeading",
                parent=base_styles["Heading2"],
                fontSize=14,
                leading=19,
                spaceBefore=13,
                spaceAfter=7,
                textColor=colors.HexColor("#1f2937"),
            ),
            "subheading": ParagraphStyle(
                "SubHeading",
                parent=base_styles["Heading3"],
                fontSize=12,
                leading=16,
                spaceBefore=10,
                spaceAfter=6,
                textColor=colors.HexColor("#374151"),
            ),
            "normal": ParagraphStyle(
                "BodyText",
                parent=base_styles["Normal"],
                fontSize=10,
                leading=15,
                alignment=TA_LEFT,
                spaceAfter=6,
            ),
            "bullet": ParagraphStyle(
                "BulletText",
                parent=base_styles["Normal"],
                fontSize=10,
                leading=15,
                leftIndent=18,
                bulletIndent=5,
                spaceAfter=5,
            ),
            "question": ParagraphStyle(
                "QuestionText",
                parent=base_styles["Normal"],
                fontSize=10.5,
                leading=15,
                spaceBefore=8,
                spaceAfter=4,
                textColor=colors.HexColor("#111827"),
            ),
            "answer": ParagraphStyle(
                "AnswerText",
                parent=base_styles["Normal"],
                fontSize=10,
                leading=15,
                leftIndent=10,
                spaceAfter=7,
                textColor=colors.HexColor("#374151"),
            ),
        }

        story = []
        story.append(Paragraph(_paragraph_text(title), styles["cover_title"]))

        metadata_parts = [f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
        if source_filename:
            metadata_parts.append(f"Source: {source_filename}")
        if model_name:
            metadata_parts.append(f"Model: {model_name}")

        story.append(Paragraph(_paragraph_text(" | ".join(metadata_parts)), styles["metadata"]))
        story.append(Spacer(1, 10))

        for line in cleaned_summary.splitlines():
            story.append(_markdown_line_to_paragraph(line, styles))

        doc.build(story, onFirstPage=_draw_footer, onLaterPages=_draw_footer)

    except Exception as exc:
        raise PdfGenerationError(f"Could not generate summary PDF: {exc}") from exc

    return output_path
