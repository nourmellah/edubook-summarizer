from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Iterable

import requests

from app.core.config import settings
from app.services.text_polish import polish_summary_text


class SummarizationError(Exception):
    """Raised when summarization fails."""


class GeminiQuotaError(SummarizationError):
    """Raised when Gemini rejects a request because of quota/rate limits."""

    def __init__(self, message: str, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


PAGE_RE = re.compile(r"--- Page (\d+) ---")
CHAPTER_RE = re.compile(r"^\s*(?:chapitre|chapter|kapitel|cap[ií]tulo|capitulo|section|part|partie)\s+(\d+)\s*:?\s*(.*)$", re.IGNORECASE)
TITLE_HINTS = (
    # Generic academic headings across languages. These are only used as a weak
    # fallback for chapter splitting when the PDF has no explicit "Chapter N" markers.
    "introduction", "overview", "objectives", "goals", "definition", "definitions",
    "method", "methods", "algorithm", "algorithms", "model", "models", "evaluation",
    "validation", "examples", "applications", "conclusion", "summary", "recap",
    "objectif", "objectifs", "définition", "definition", "définitions", "definitions",
    "méthode", "methodes", "méthodes", "algorithme", "algorithmes", "modèle",
    "modele", "modèles", "modeles", "évaluation", "evaluation", "exemples",
    "applications", "conclusion", "résumé", "resume", "récapitulatif",
    "einführung", "einfuhrung", "überblick", "ueberblick", "definitionen",
    "methoden", "algorithmen", "modelle", "bewertung", "beispiele", "fazit",
    "introducción", "introduccion", "resumen", "definiciones", "métodos", "metodos",
    "algoritmos", "modelos", "evaluación", "evaluacion", "ejemplos", "conclusión",
    # Existing domain hints kept as non-exclusive examples.
    "processus", "techniques", "classification", "arbres", "règles", "regles",
    "data mining", "fouille", "ecd", "kdd", "k-means", "cah", "cart", "id3", "c4.5", "sipina",
)
PRIORITY_KEYWORDS = (
    # Language-neutral / multilingual educational cues used by the compactors.
    "definition", "definitions", "objective", "objectives", "goal", "goals",
    "principle", "example", "algorithm", "method", "methods", "advantage", "advantages",
    "limitation", "limitations", "property", "properties", "process", "phase",
    "technique", "techniques", "validation", "evaluation", "metric", "metrics",
    "formula", "formulas", "model", "models", "training", "test", "error", "accuracy",
    "définition", "definition", "définitions", "definitions", "objectif", "objectifs",
    "principe", "exemple", "algorithme", "méthode", "méthodes", "methodes",
    "avantages", "inconvénients", "inconvenients", "propriétés", "proprietes",
    "processus", "phase", "techniques", "validation", "évaluation", "evaluation",
    "formule", "formules", "modèle", "modele", "modèles", "modeles", "erreur",
    "genauigkeit", "definitionen", "ziel", "ziele", "beispiel", "algorithmus",
    "methode", "methoden", "vorteile", "nachteile", "eigenschaften", "prozess",
    "bewertung", "validierung", "formel", "modell",
    "definición", "definicion", "definiciones", "objetivo", "objetivos", "principio",
    "ejemplo", "algoritmo", "método", "metodo", "métodos", "metodos", "ventajas",
    "limitaciones", "propiedades", "proceso", "evaluación", "evaluacion", "validación",
    "validacion", "fórmula", "formula", "modelo",
    # Existing data-mining/time-series cues kept as domain examples, not language assumptions.
    "classification", "clustering", "k-means", "cah", "hiérarchique", "hierarchique",
    "decision", "décision", "association", "support", "confiance", "confidence",
    "entropie", "entropy", "gini", "supervisé", "supervise", "supervised",
    "non supervisé", "non supervise", "unsupervised", "prédiction", "prediction",
    "data mining", "ecd", "kdd", "matrice de confusion", "confusion matrix",
    "taux d'erreur", "error rate", "apriori", "a_priori", "apprentissage", "learning",
    "préparation", "preparation", "acquisition",
    "stationarity", "stationnarité", "acf", "pacf", "arma", "ar(p)", "ma(q)", "box-jenkins",
)


@dataclass
class PageBlock:
    page_number: int
    text: str


@dataclass
class ChapterBlock:
    title: str
    start_page: int
    end_page: int
    pages: list[PageBlock]


@dataclass
class SummaryResult:
    summary: str
    mode: str
    sections_processed: int
    provider: str
    model: str


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _target_language(language: str | None = None) -> str:
    language = (language or os.getenv("SUMMARY_LANGUAGE", "fr")).strip().lower()
    if language in {"fr", "french", "français", "francais"}:
        return "French"
    if language in {"en", "english"}:
        return "English"
    if language in {"de", "german", "deutsch"}:
        return "German"
    if language in {"es", "spanish", "español", "espanol"}:
        return "Spanish"
    if language in {"ar", "arabic", "العربية"}:
        return "Arabic"
    return language




def _language_code(language: str | None = None) -> str:
    selected = (language or os.getenv("SUMMARY_LANGUAGE", "fr")).strip().lower()
    if selected in {"fr", "french", "français", "francais"}:
        return "fr"
    if selected in {"en", "english"}:
        return "en"
    if selected in {"de", "german", "deutsch"}:
        return "de"
    if selected in {"es", "spanish", "español", "espanol"}:
        return "es"
    if selected in {"ar", "arabic", "العربية"}:
        return "ar"
    return selected[:2] if selected else "en"


def _answer_label(language: str | None = None) -> str:
    code = _language_code(language)
    return {
        "fr": "Réponse",
        "en": "Answer",
        "de": "Antwort",
        "es": "Respuesta",
        "ar": "الإجابة",
    }.get(code, "Answer")


def _markdown_structure_instruction(kind: str, language: str | None = None) -> str:
    """Return language-neutral structure instructions.

    The model is told to translate section titles into the target language and keep
    Markdown markers stable. Renderers should rely on #/##/###, not French words.
    """
    answer = _answer_label(language)
    if kind == "fast":
        semantic_sections = (
            "Document summary",
            "Overview",
            "Course structure",
            "Key concepts explained",
            "Important methods and algorithms",
            "Examples and applications",
            "Revision questions with answers",
        )
    elif kind == "chapter":
        semantic_sections = (
            "Main idea",
            "Important concepts",
            "Methods / algorithms / formulas",
            "Examples from the course",
            "What to remember",
            "Corrected questions",
        )
    elif kind == "combined":
        semantic_sections = (
            "Educational summary",
            "Course overview",
            "Detailed chapter plan",
            "Essential concepts to memorize",
            "Algorithms and methods to know",
            "Important applications and examples",
            "Corrected revision questions",
            "Final recap",
        )
    else:
        semantic_sections = (
            "Educational summary",
            "Course overview",
            "Detailed course plan",
            "Detailed chapter-by-chapter summary",
            "Important definitions",
            "Algorithms and methods to know",
            "Course examples and applications",
            "Advantages, limitations, and evaluation criteria",
            "Corrected revision questions",
            "Final recap",
        )
    return (
        "Use Markdown heading markers exactly (#, ##, ###). "
        "Translate the section titles naturally into the requested output language; "
        "do not keep French section titles unless the requested language is French. "
        "Do not rely on plain unmarked headings.\n"
        "Required semantic sections, in order:\n- "
        + "\n- ".join(semantic_sections)
        + f"\nFor answered questions, use this format: Q1. ...\n{answer}: ..."
    )

def _style_instruction(style: str | None = None) -> str:
    selected = (style or os.getenv("SUMMARY_STYLE", "student_friendly")).strip().lower()
    mapping = {
        "student": "Write clear student-friendly study notes with explanations, examples, and revision focus.",
        "student_friendly": "Write clear student-friendly study notes with explanations, examples, and revision focus.",
        "academic": "Write in a clean academic handout style with precise terminology and structured sections.",
        "concise": "Be concise while preserving the main definitions, formulas, methods, and answered revision questions.",
        "detailed": "Be detailed and explanatory. Include concrete examples, formulas, limitations, and exam-revision notes.",
        "exam_revision": "Focus on exam revision: definitions, formulas, method steps, common questions, and corrected answers.",
        "cheatsheet": "Use a compact cheat-sheet style with formulas, key rules, and short explanations.",
    }
    return mapping.get(selected, f"Follow this style preference: {selected}.")


def _resolve_provider(provider: str | None = None) -> str:
    selected = (provider or settings.LLM_PROVIDER or "ollama").strip().lower()

    aliases = {
        "local": "ollama",
        "offline": "ollama",
        "ollama": "ollama",
        "google": "gemini",
        "online": "gemini",
        "gemini": "gemini",
    }

    if selected == "auto":
        return "gemini" if settings.GEMINI_API_KEY else "ollama"

    if selected in aliases:
        return aliases[selected]

    raise SummarizationError(
        f"Unknown LLM provider '{selected}'. Use 'ollama', 'local', 'gemini', 'online', or 'auto'."
    )


def _model_for_provider(provider: str) -> str:
    if provider == "gemini":
        return settings.GEMINI_MODEL
    return settings.OLLAMA_MODEL


def _clean_line(line: str) -> str:
    replacements = {
        "\uf0d8": "•",
        "\uf0a8": "•",
        "\uf0fc": "•",
        "": "•",
        "": "•",
        "": "→",
        "": "→",
        "": "→",
        "≤": "<=",
        "≥": ">=",
    }
    for old, new in replacements.items():
        line = line.replace(old, new)
    line = re.sub(r"\s+", " ", line).strip()
    return line


def _remove_noise_lines(lines: list[str]) -> list[str]:
    cleaned = [_clean_line(line) for line in lines]
    cleaned = [line for line in cleaned if line]

    frequency: dict[str, int] = {}
    for line in cleaned:
        key = line.lower()
        frequency[key] = frequency.get(key, 0) + 1

    result: list[str] = []
    for line in cleaned:
        low = line.lower()
        if len(line) <= 2 or line.isdigit():
            continue
        if low in {"i 2", "mastre mri", "mastère mri"}:
            continue
        if "mohamed hammami" in low:
            continue
        if frequency.get(low, 0) > 8 and len(line) < 70:
            continue
        result.append(line)

    return result


def parse_pages(text: str) -> list[PageBlock]:
    parts = PAGE_RE.split(text)
    if len(parts) < 3:
        return [PageBlock(1, text.strip())] if text.strip() else []

    pages: list[PageBlock] = []
    for index in range(1, len(parts), 2):
        page_number = int(parts[index])
        page_text = parts[index + 1].strip()
        pages.append(PageBlock(page_number, page_text))
    return pages


def _page_title(lines: list[str]) -> str | None:
    joined_head = " ".join(lines[:5]).lower()
    looks_like_plan_page = any(marker in joined_head for marker in ("plan du cours", "course outline", "course plan", "syllabus", "table of contents", "sommaire", "inhalt", "contenido"))

    for index, line in enumerate(lines[:8]):
        match = CHAPTER_RE.match(line)
        if match:
            if looks_like_plan_page and index > 0:
                continue
            number, title = match.groups()
            title = title.strip()
            if not title and index + 1 < len(lines):
                next_line = lines[index + 1].strip()
                if next_line and not CHAPTER_RE.match(next_line):
                    title = next_line
            title = title or f"Chapter {number}"
            return f"Chapter {number}: {title}"

    if looks_like_plan_page:
        return "Course outline"

    for line in lines[:4]:
        low = line.lower()
        if 4 <= len(line) <= 90 and any(hint in low for hint in TITLE_HINTS):
            return line
    return None


def split_into_chapters(text: str) -> list[ChapterBlock]:
    pages = parse_pages(text)
    if not pages:
        return []

    chapters: list[ChapterBlock] = []
    current_title = "Introduction / Foreword"
    current_pages: list[PageBlock] = []
    current_start = pages[0].page_number

    for page in pages:
        lines = _remove_noise_lines(page.text.splitlines())
        detected_title = _page_title(lines)
        is_new_chapter = detected_title and CHAPTER_RE.match(detected_title)

        if is_new_chapter and current_pages:
            chapters.append(
                ChapterBlock(
                    title=current_title,
                    start_page=current_start,
                    end_page=current_pages[-1].page_number,
                    pages=current_pages,
                )
            )
            current_title = detected_title
            current_pages = [page]
            current_start = page.page_number
        else:
            if detected_title and current_title == "Introduction / Avant-propos":
                current_title = detected_title
            current_pages.append(page)

    if current_pages:
        chapters.append(
            ChapterBlock(
                title=current_title,
                start_page=current_start,
                end_page=current_pages[-1].page_number,
                pages=current_pages,
            )
        )

    if len(chapters) <= 1 and len(pages) > 30:
        section_size = _env_int("STUDY_PACK_PAGES_PER_SECTION", 35)
        generated: list[ChapterBlock] = []
        for idx in range(0, len(pages), section_size):
            section_pages = pages[idx : idx + section_size]
            generated.append(
                ChapterBlock(
                    title=f"Section {len(generated) + 1}",
                    start_page=section_pages[0].page_number,
                    end_page=section_pages[-1].page_number,
                    pages=section_pages,
                )
            )
        return generated

    return chapters


def compact_pages(pages: list[PageBlock], max_chars: int) -> str:
    blocks: list[str] = []

    for page in pages:
        lines = _remove_noise_lines(page.text.splitlines())
        if not lines:
            continue

        kept: list[str] = []
        for line in lines:
            low = line.lower()
            is_bullet = line.startswith(("•", "-", "*"))
            is_title = len(line) <= 85 and not line.endswith(".")
            is_step = bool(re.match(r"^\d+[.)]\s+", line))
            has_keyword = any(keyword in low for keyword in PRIORITY_KEYWORDS)
            has_formula = any(symbol in line for symbol in ["=", "→", ">=", "<=", "%", "log", "Supp", "Conf"])

            if is_bullet or is_title or is_step or has_keyword or has_formula:
                kept.append(line)

        if not kept:
            kept = lines[:5]

        blocks.append(f"[Page {page.page_number}]\n" + "\n".join(kept[:18]))

    compact = "\n\n".join(blocks)
    if len(compact) <= max_chars:
        return compact

    head_budget = int(max_chars * 0.30)
    tail_budget = int(max_chars * 0.25)
    middle_budget = max_chars - head_budget - tail_budget

    head = compact[:head_budget]
    tail = compact[-tail_budget:]
    middle_blocks = [b for b in compact[head_budget:-tail_budget].split("\n\n") if b.strip()]

    selected: list[str] = []
    used = 0
    if middle_blocks:
        step = max(1, len(middle_blocks) // 10)
        for block in middle_blocks[::step]:
            if used + len(block) > middle_budget:
                break
            selected.append(block)
            used += len(block)

    return (
        head
        + "\n\n[... contenu intermédiaire compressé ...]\n\n"
        + "\n\n".join(selected)
        + "\n\n[... fin de la section ...]\n\n"
        + tail
    )[:max_chars]


def split_text_into_chunks(text: str, max_chars: int | None = None, max_chunks: int | None = None) -> list[str]:
    max_chars = max_chars or settings.MAX_CHARS_PER_CHUNK
    max_chunks = max_chunks or settings.MAX_CHUNKS

    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0

    for paragraph in paragraphs:
        paragraph_size = len(paragraph)
        if current and current_size + paragraph_size > max_chars:
            chunks.append("\n".join(current))
            current = [paragraph]
            current_size = paragraph_size
        else:
            current.append(paragraph)
            current_size += paragraph_size

    if current:
        chunks.append("\n".join(current))

    if len(chunks) > max_chunks:
        raise SummarizationError(
            f"Document is too large for detailed mode. Created {len(chunks)} chunks, but MAX_CHUNKS={max_chunks}."
        )

    return chunks


def _ollama_generate(prompt: str, num_predict: int | None = None, timeout: int | None = None) -> str:
    url = f"{settings.OLLAMA_BASE_URL.rstrip('/')}/api/generate"
    payload = {
        "model": settings.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": settings.OLLAMA_TEMPERATURE,
            "num_predict": num_predict or settings.OLLAMA_NUM_PREDICT,
            "num_ctx": _env_int("OLLAMA_NUM_CTX", 8192),
        },
    }

    try:
        response = requests.post(url, json=payload, timeout=timeout or settings.OLLAMA_TIMEOUT_SECONDS)
    except requests.exceptions.ConnectionError as exc:
        raise SummarizationError(
            "Could not connect to Ollama. Make sure Ollama is installed and running. "
            f"Expected URL: {settings.OLLAMA_BASE_URL}"
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise SummarizationError(
            "Ollama took too long to respond. Try provider=gemini, SUMMARY_MODE=fast, a smaller model, or increase the timeout."
        ) from exc

    if response.status_code != 200:
        raise SummarizationError(f"Ollama returned an error: {response.text}")

    result = response.json().get("response", "").strip()
    if not result:
        raise SummarizationError("Ollama returned an empty summary.")

    return result


def _extract_gemini_retry_delay(response: requests.Response) -> int | None:
    """Extract retry delay from Gemini 429 responses when available."""
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return int(float(retry_after))
        except ValueError:
            pass

    try:
        data = response.json()
    except ValueError:
        return None

    error = data.get("error", {})
    for detail in error.get("details", []) or []:
        retry_delay = detail.get("retryDelay")
        if isinstance(retry_delay, str):
            match = re.match(r"^(\d+(?:\.\d+)?)s$", retry_delay.strip())
            if match:
                return int(float(match.group(1)))

    return None


def _gemini_generate(prompt: str, num_predict: int | None = None, timeout: int | None = None) -> str:
    if not settings.GEMINI_API_KEY:
        raise SummarizationError(
            "Gemini provider selected but GEMINI_API_KEY is missing. "
            "Create an API key in Google AI Studio and add it to your .env file."
        )

    model = settings.GEMINI_MODEL
    url = f"{settings.GEMINI_BASE_URL.rstrip('/')}/models/{model}:generateContent"
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "temperature": settings.GEMINI_TEMPERATURE,
            "maxOutputTokens": num_predict or settings.GEMINI_MAX_OUTPUT_TOKENS,
        },
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": settings.GEMINI_API_KEY,
    }

    max_attempts = max(1, settings.GEMINI_RETRY_ATTEMPTS)

    for attempt in range(1, max_attempts + 1):
        try:
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout or settings.GEMINI_TIMEOUT_SECONDS,
            )
        except requests.exceptions.ConnectionError as exc:
            raise SummarizationError("Could not connect to the Gemini API. Check your internet connection.") from exc
        except requests.exceptions.Timeout as exc:
            raise SummarizationError("Gemini took too long to respond. Increase GEMINI_TIMEOUT_SECONDS or use mode=fast.") from exc

        if response.status_code == 200:
            data = response.json()
            prompt_feedback = data.get("promptFeedback") or {}
            if prompt_feedback.get("blockReason"):
                raise SummarizationError(f"Gemini blocked the prompt: {prompt_feedback.get('blockReason')}")

            candidates = data.get("candidates") or []
            if not candidates:
                raise SummarizationError(f"Gemini returned no candidates: {data}")

            parts = candidates[0].get("content", {}).get("parts", [])
            result = "\n".join(part.get("text", "") for part in parts if part.get("text")).strip()
            if not result:
                finish_reason = candidates[0].get("finishReason", "unknown")
                raise SummarizationError(f"Gemini returned an empty summary. Finish reason: {finish_reason}")

            return result

        if response.status_code == 429:
            retry_after = _extract_gemini_retry_delay(response) or settings.GEMINI_RETRY_DEFAULT_WAIT_SECONDS
            retry_after = min(retry_after, settings.GEMINI_RETRY_MAX_WAIT_SECONDS)

            if attempt < max_attempts:
                time.sleep(retry_after)
                continue

            raise GeminiQuotaError(
                "Gemini quota/rate limit exceeded after retrying. "
                "Try GEMINI_MODEL=gemini-2.5-flash-lite, mode=gemini_single, wait a minute, or use provider=ollama.",
                retry_after_seconds=retry_after,
            )

        raise SummarizationError(f"Gemini returned an error: {response.text}")

    raise SummarizationError("Gemini request failed unexpectedly.")

def _generate(prompt: str, provider: str | None = None, num_predict: int | None = None, timeout: int | None = None) -> tuple[str, str, str]:
    resolved_provider = _resolve_provider(provider)
    model = _model_for_provider(resolved_provider)

    if resolved_provider == "gemini":
        return _gemini_generate(prompt, num_predict=num_predict, timeout=timeout), resolved_provider, model

    return _ollama_generate(prompt, num_predict=num_predict, timeout=timeout), resolved_provider, model


def summarize_fast(text: str, language: str | None = None, provider: str | None = None, style: str | None = None) -> SummaryResult:
    target_language = _target_language(language)
    style_instruction = _style_instruction(style)
    pages = parse_pages(text)
    compact_text = compact_pages(pages, max_chars=_env_int("FAST_SUMMARY_MAX_INPUT_CHARS", 18000))

    prompt = f"""
You are an educational AI assistant.

Create a useful but concise study summary from the extracted course text.
Write in {target_language}.
Style preference: {style_instruction}

Style rules:
- Start directly with the title. Do not write conversational phrases like "Absolument", "Voici", or "Bien sûr".
- Do not use Markdown bold markers like **text**.
- Use clean headings, bullet points, and plain readable formulas.

{_markdown_structure_instruction("fast", language)}

Important rules:
- Do not only list questions. For every study question, include a clear answer.
- Use course content, definitions, examples, and algorithms from the source.
- Avoid empty generic phrases.
- Do not invent information that is not supported by the text.

Extracted course text:
--- START ---
{compact_text}
--- END ---
""".strip()

    summary, used_provider, used_model = _generate(
        prompt,
        provider=provider,
        num_predict=_env_int("FAST_SUMMARY_NUM_PREDICT", 1800),
        timeout=_env_int("FAST_SUMMARY_TIMEOUT_SECONDS", 300),
    )
    return SummaryResult(summary=polish_summary_text(summary), mode="fast", sections_processed=1, provider=used_provider, model=used_model)


def summarize_chapter(chapter: ChapterBlock, index: int, total: int, language: str | None = None, provider: str | None = None, style: str | None = None) -> tuple[str, str, str]:
    target_language = _target_language(language)
    style_instruction = _style_instruction(style)
    max_chars = _env_int("STUDY_PACK_CHAPTER_MAX_CHARS", 9000)
    compact = compact_pages(chapter.pages, max_chars=max_chars)
    num_predict = _env_int("STUDY_PACK_CHAPTER_NUM_PREDICT", 1400)

    prompt = f"""
You are an educational AI assistant preparing course notes for students.

Write in {target_language}.
Style preference: {style_instruction}
Style rules:
- Start directly with the section title.
- Do not write conversational phrases.
- Do not use Markdown bold markers like **text**.
- Use plain readable formulas.

Summarize this chapter/section with real useful information from the source.

Chapter {index}/{total}: {chapter.title}
Pages: {chapter.start_page}-{chapter.end_page}

Required structure:
### {chapter.title}
- Pages: {chapter.start_page}-{chapter.end_page}

{_markdown_structure_instruction("chapter", language)}

If no methods, algorithms, formulas, or evaluation metrics are present, say so in the requested language.

Rules:
- Use only the provided source.
- Do not produce vague summaries.
- Do not invent missing details.
- Prefer clear student notes over academic style.

Source text:
--- START ---
{compact}
--- END ---
""".strip()

    return _generate(
        prompt,
        provider=provider,
        num_predict=num_predict,
        timeout=_env_int("STUDY_PACK_CHAPTER_TIMEOUT_SECONDS", 300),
    )


def combine_study_pack(chapter_summaries: Iterable[str], language: str | None = None, provider: str | None = None, style: str | None = None) -> tuple[str, str, str]:
    target_language = _target_language(language)
    style_instruction = _style_instruction(style)
    joined = "\n\n".join(chapter_summaries)
    num_predict = _env_int("STUDY_PACK_FINAL_NUM_PREDICT", 2200)

    prompt = f"""
You are an educational AI assistant.

Write in {target_language}.
Style preference: {style_instruction}
Style rules:
- Start directly with the title.
- Do not write conversational phrases.
- Do not use Markdown bold markers like **text**.
- Use plain readable formulas.

Create a final study pack from these chapter notes.
Keep the chapter summaries detailed, but remove obvious repetition.

{_markdown_structure_instruction("combined", language)}

For each chapter, keep concrete explanations, methods, examples, advantages, limitations, and formulas when present.
Create 10 useful study questions and give the answer immediately after each question.

Rules:
- Questions must be answered.
- Avoid vague wording.

Technical accuracy rules for time-series courses:
- For AR(p): the ACF decays gradually; the PACF cuts off after lag p.
- For MA(q): the ACF cuts off after lag q; the PACF decays gradually.
- For ARMA(p,q): both ACF and PACF decay gradually; neither cuts off cleanly.
- Do not write contradictory AR/MA/ARMA identification rules.
- Keep enough detail for exam revision.
- Do not invent content.

Chapter notes:
--- START ---
{joined}
--- END ---
""".strip()

    return _generate(
        prompt,
        provider=provider,
        num_predict=num_predict,
        timeout=_env_int("STUDY_PACK_FINAL_TIMEOUT_SECONDS", 420),
    )


def build_gemini_study_context(text: str, max_chars: int) -> str:
    """
    Build a rich single-request context for Gemini.
    Gemini can handle larger inputs than local models, so we keep more course content
    and avoid chapter-by-chapter API calls that quickly hit RPM quotas.
    """
    pages = parse_pages(text)
    if not pages:
        return text[:max_chars]

    blocks: list[str] = []
    for page in pages:
        lines = _remove_noise_lines(page.text.splitlines())
        if not lines:
            continue
        page_text = "\n".join(lines)
        blocks.append(f"[Page {page.page_number}]\n{page_text}")

    rich_context = "\n\n".join(blocks)
    if len(rich_context) <= max_chars:
        return rich_context

    # If the document is too large, fall back to the priority-line compressor.
    return compact_pages(pages, max_chars=max_chars)


def summarize_study_pack_single_call(text: str, language: str | None = None, provider: str | None = None, style: str | None = None) -> SummaryResult:
    target_language = _target_language(language)
    style_instruction = _style_instruction(style)
    max_input_chars = settings.GEMINI_STUDY_PACK_MAX_INPUT_CHARS
    course_context = build_gemini_study_context(text, max_chars=max_input_chars)

    prompt = f"""
You are an educational AI assistant integrated in a university Education AI Center.

Task: create a useful study pack from the uploaded educational document(s).
Write in {target_language}.
Style preference: {style_instruction}

Style rules:
- Start directly with the title. Do not write conversational phrases like "Absolument", "Voici", or "Bien sûr".
- Do not use Markdown bold markers like **text**.
- Use clean section titles and bullet points.
- Write formulas in simple readable plain text, not raw LaTeX.

Very important:
- This must be a real course summary, not a generic outline.
- Use concrete information from the source text.
- Do not invent unsupported information.
- Include answered revision questions, not only questions.
- Preserve important definitions, algorithms, formulas, examples, advantages, and limitations.
- When possible, mention the chapter/page range where the topic appears.

Technical accuracy rules for time-series courses:
- For AR(p): the ACF decays gradually; the PACF cuts off after lag p.
- For MA(q): the ACF cuts off after lag q; the PACF decays gradually.
- For ARMA(p,q): both ACF and PACF decay gradually; neither cuts off cleanly.
- Do not write contradictory AR/MA/ARMA identification rules.

Required output structure:
{_markdown_structure_instruction("study_pack", language)}

For every chapter/large section, explain:
- the main idea,
- important concepts,
- methods/algorithms/formulas,
- concrete examples from the course,
- what a student should remember.

Explain each method/algorithm that appears in the source, for example ECD/KDD process, classification, clustering, CAH, k-means, decision trees, entropy/Gini if present, association rules, support/confidence, Apriori if present.
Only include items actually present in the source.
Create 12 useful exam-style questions and answer each one immediately.
Give a short final recap for last-minute revision.

Source text from the uploaded document(s):
--- START ---
{course_context}
--- END ---
""".strip()

    summary, used_provider, used_model = _generate(
        prompt,
        provider=provider,
        num_predict=settings.GEMINI_MAX_OUTPUT_TOKENS,
        timeout=settings.GEMINI_TIMEOUT_SECONDS,
    )
    return SummaryResult(
        summary=polish_summary_text(summary),
        mode="study_pack_single_call",
        sections_processed=1,
        provider=used_provider,
        model=used_model,
    )


def summarize_study_pack_multi_call(text: str, language: str | None = None, provider: str | None = None, style: str | None = None) -> SummaryResult:
    chapters = split_into_chapters(text)
    if not chapters:
        raise SummarizationError("No readable text found for summarization.")

    max_sections = _env_int("STUDY_PACK_MAX_SECTIONS", 8)
    if len(chapters) > max_sections:
        chapters = chapters[:max_sections]

    used_provider = _resolve_provider(provider)
    used_model = _model_for_provider(used_provider)
    chapter_summaries: list[str] = []

    for index, chapter in enumerate(chapters, start=1):
        chapter_summary, used_provider, used_model = summarize_chapter(
            chapter,
            index,
            len(chapters),
            language=language,
            provider=used_provider,
            style=style,
        )
        chapter_summaries.append(chapter_summary)

    final_summary, used_provider, used_model = combine_study_pack(
        chapter_summaries,
        language=language,
        provider=used_provider,
        style=style,
    )
    return SummaryResult(
        summary=polish_summary_text(final_summary),
        mode="study_pack",
        sections_processed=len(chapters),
        provider=used_provider,
        model=used_model,
    )


def summarize_study_pack(text: str, language: str | None = None, provider: str | None = None, style: str | None = None) -> SummaryResult:
    used_provider = _resolve_provider(provider)

    # Important rate-limit optimization:
    # Gemini free tier is request-count limited. For long documents, we prefer one rich request.
    if used_provider == "gemini" and settings.GEMINI_SINGLE_CALL_STUDY_PACK:
        try:
            return summarize_study_pack_single_call(text, language=language, provider=used_provider, style=style)
        except GeminiQuotaError:
            if settings.GEMINI_FALLBACK_TO_OLLAMA:
                return summarize_study_pack_multi_call(text, language=language, provider="ollama", style=style)
            raise

    return summarize_study_pack_multi_call(text, language=language, provider=used_provider, style=style)

def summarize_chunk(chunk: str, chunk_number: int, total_chunks: int, language: str | None = None, provider: str | None = None, style: str | None = None) -> tuple[str, str, str]:
    target_language = _target_language(language)
    style_instruction = _style_instruction(style)
    prompt = f"""
You are an educational AI assistant.
Write in {target_language}.
Style preference: {style_instruction}
Summarize this part of an educational document as useful study notes.
Keep definitions, examples, algorithms, formulas, and evaluation criteria.

Part {chunk_number}/{total_chunks}:
--- START ---
{chunk}
--- END ---
""".strip()
    return _generate(prompt, provider=provider, num_predict=_env_int("CHUNK_SUMMARY_NUM_PREDICT", 700))


def combine_partial_summaries(partial_summaries: Iterable[str], language: str | None = None, provider: str | None = None, style: str | None = None) -> tuple[str, str, str]:
    target_language = _target_language(language)
    style_instruction = _style_instruction(style)
    joined = "\n\n".join(
        f"Partial Summary {index + 1}:\n{summary}"
        for index, summary in enumerate(partial_summaries)
    )
    prompt = f"""
You are an educational AI assistant.
Write in {target_language}.
Style preference: {style_instruction}
Combine these partial summaries into one detailed study summary.
Include answered revision questions.

Partial summaries:
--- START ---
{joined}
--- END ---
""".strip()
    return _generate(prompt, provider=provider, num_predict=_env_int("FINAL_SUMMARY_NUM_PREDICT", 1800))


def summarize_detailed(text: str, language: str | None = None, provider: str | None = None, style: str | None = None) -> SummaryResult:
    chunks = split_text_into_chunks(text)
    if not chunks:
        raise SummarizationError("No valid text chunks were created from the document.")

    used_provider = _resolve_provider(provider)
    used_model = _model_for_provider(used_provider)
    partials: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        partial, used_provider, used_model = summarize_chunk(
            chunk,
            index,
            len(chunks),
            language=language,
            provider=used_provider,
            style=style,
        )
        partials.append(partial)

    final, used_provider, used_model = combine_partial_summaries(partials, language=language, provider=used_provider, style=style)
    return SummaryResult(
        summary=polish_summary_text(final),
        mode="detailed",
        sections_processed=len(chunks),
        provider=used_provider,
        model=used_model,
    )


def summarize_text(text: str, mode: str | None = None, language: str | None = None, provider: str | None = None, style: str | None = None) -> SummaryResult:
    cleaned_text = text.strip()
    if not cleaned_text:
        raise SummarizationError("Cannot summarize empty text.")

    selected_mode = (mode or os.getenv("SUMMARY_MODE", "study_pack")).strip().lower()

    if selected_mode == "fast":
        return summarize_fast(cleaned_text, language=language, provider=provider, style=style)
    if selected_mode == "detailed":
        return summarize_detailed(cleaned_text, language=language, provider=provider, style=style)
    if selected_mode in {"gemini_single", "single", "rate_safe", "single_call"}:
        return summarize_study_pack_single_call(cleaned_text, language=language, provider=provider, style=style)
    if selected_mode in {"study", "study_pack", "course", "chapter"}:
        return summarize_study_pack(cleaned_text, language=language, provider=provider, style=style)

    raise SummarizationError(f"Unknown SUMMARY_MODE '{selected_mode}'. Use fast, study_pack, detailed, or gemini_single.")
