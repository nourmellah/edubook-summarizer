from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
import re
import unicodedata

from app.core.config import settings
from app.services.text_polish import polish_summary_text


class TexGenerationError(Exception):
    """Raised when summary TeX generation fails."""


LATEX_SPECIAL_CHARS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

MAIN_SECTION_KEYWORDS = (
    # Generic semantic section labels used only as a fallback when the model does
    # not emit Markdown headings. Keep this multilingual and domain-neutral.
    "overview", "introduction", "summary", "resume", "plan", "outline",
    "chapters", "sections", "definitions", "concepts", "methods", "algorithms",
    "examples", "applications", "advantages", "limitations", "limits", "criteria",
    "evaluation", "assessment", "questions", "revision", "review", "recap", "conclusion",
    "vue", "resume", "résumé", "plan", "chapitres", "sections", "definitions",
    "définitions", "concepts", "methodes", "méthodes", "algorithmes", "exemples",
    "applications", "avantages", "limites", "criteres", "critères", "evaluation",
    "évaluation", "questions", "revision", "révision", "recapitulatif", "récapitulatif",
    "conclusion",
    "überblick", "ueberblick", "einführung", "einfuhrung", "zusammenfassung",
    "definitionen", "methoden", "beispiele", "anwendungen", "vorteile", "grenzen",
    "bewertung", "fragen", "wiederholung", "fazit",
    "resumen", "vision", "visión", "definiciones", "metodos", "métodos",
    "ejemplos", "aplicaciones", "ventajas", "limitaciones", "evaluacion",
    "evaluación", "preguntas", "repaso", "conclusion", "conclusión",
)

MATH_BLOCK_RE = re.compile(r"\[MATH\](.*?)\[/MATH\]", flags=re.DOTALL | re.IGNORECASE)

QUESTION_RE = re.compile(
    r"^(?:Q|Question|Frage|Pregunta|Pergunta|Domanda|سؤال)\s*\d+[\.)]?\s*",
    flags=re.IGNORECASE,
)
ANSWER_RE = re.compile(
    r"\b(?P<label>Réponse|Reponse|Answer|Antwort|Respuesta|Resposta|Risposta|جواب|الإجابة|اجابة)\s*:",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class FormulaCard:
    key: str
    title_fr: str
    title_en: str
    formula: str
    description_fr: str
    description_en: str
    triggers: tuple[str, ...]


def _strip_accents(text: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )


def _normalised_search_text(text: str) -> str:
    return _strip_accents(text).lower()


def _latex_escape(text: str) -> str:
    """Escape normal prose only. Unicode math-like symbols are preserved safely by LaTeX mappings."""
    return "".join(LATEX_SPECIAL_CHARS.get(char, char) for char in text)


def _latex_command_arg(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return _latex_escape(text)


def _strip_heading_numbering(text: str) -> str:
    """Remove LLM-generated numbering so LaTeX/TOC stay clean.

    This is intentionally multilingual and domain-neutral. It handles generic
    chapter/section prefixes without assuming French course vocabulary.
    """
    text = re.sub(r"^\s*\d+(?:\.\d+)*[.)]?\s+", "", text.strip())
    text = re.sub(
        r"^\s*(?:chapter|chapitre|kapitel|cap[ií]tulo|capitulo|section|sección|seccion|abschnitt|part|partie)\s+\d+\s*[:.-]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return text.strip() or "Section"


def _format_inline_math_markers(text: str) -> str:
    """Convert explicit [MATH]...[/MATH] markers only. Never infer math from prose."""
    parts: list[str] = []
    last = 0
    for match in MATH_BLOCK_RE.finditer(text):
        parts.append(_latex_escape(text[last:match.start()]))
        math_expr = match.group(1).strip()
        parts.append(r"\(" + math_expr + r"\)")
        last = match.end()
    parts.append(_latex_escape(text[last:]))
    return "".join(parts)


def _display_math(math_expr: str) -> str:
    return "\\[\n" + math_expr.strip() + "\n\\]\n"


def _is_question_line(line: str) -> bool:
    return bool(QUESTION_RE.match(line.strip()))


def _is_probable_formula_line(line: str) -> bool:
    """Avoid turning short formulas into section headings."""
    stripped = line.strip()
    if not stripped:
        return False
    formula_marks = ("=", "∑", "Σ", "√", "^", "_", "≤", "≥", "≈", "≠", "→", "⇒", "×")
    if any(mark in stripped for mark in formula_marks) and len(stripped.split()) <= 14:
        return True
    return False


def _looks_like_heading_text(text: str) -> bool:
    """Language-neutral fallback heading detector.

    Prefer Markdown headings. This is only for model outputs that contain plain
    short heading lines such as "Moving Average (Pages 8-10)" or
    "Chapter 2: Classification". Detection is based on shape, not course words.
    """
    line = text.strip()
    if not line or len(line) > 160:
        return False
    if _is_question_line(line) or ANSWER_RE.match(line):
        return False
    if line.startswith(("- ", "* ", "• ", "— ")):
        return False
    if _is_probable_formula_line(line):
        return False

    # Chapter / section-like titles across common languages.
    if re.match(
        r"^(chapter|chapitre|kapitel|cap[ií]tulo|capitulo|section|sección|seccion|abschnitt|part|partie)\s+\d+\b",
        line,
        flags=re.IGNORECASE,
    ):
        return True

    # Any short title with a page reference, regardless of the topic language.
    if re.search(
        r"\((?:pages?|p\.?|pp\.?|seiten?|p[aá]ginas?|paginas?|page)\s*\d+",
        line,
        flags=re.IGNORECASE,
    ):
        return True

    # Numbered semantic major section, e.g. "1. Overview" / "1. Définitions".
    numbered = re.match(r"^\d+(?:\.\d+)*[.)]?\s+(.+)$", line)
    if numbered:
        title = _normalised_search_text(numbered.group(1))
        return title.startswith(MAIN_SECTION_KEYWORDS)

    # Short title-like line with no final sentence punctuation. Useful for
    # English/German/etc. headings emitted without Markdown.
    if len(line.split()) <= 9 and not line.endswith((".", ",", ";", ":", "?", "!")):
        letters = [c for c in line if c.isalpha()]
        if letters:
            uppercase_ratio = sum(1 for c in letters if c.isupper()) / max(len(letters), 1)
            # Title Case, ALL CAPS, or compact noun phrase with page/topic words.
            if uppercase_ratio > 0.18 or line.istitle():
                return True

    return False


def _is_main_numbered_section(line: str) -> bool:
    match = re.match(r"^\d+(?:\.\d+)*[.)]?\s+(.+)$", line.strip())
    if not match or _is_question_line(line):
        return False
    title = _normalised_search_text(match.group(1))
    return title.startswith(MAIN_SECTION_KEYWORDS)


def _is_subheading(line: str) -> bool:
    return _looks_like_heading_text(line) and not _is_main_numbered_section(line)


def _section_for_heading(line: str, level: int, numbered: bool = True) -> str:
    command = "section" if level <= 1 else "subsection" if level == 2 else "subsubsection"
    clean_line = _strip_heading_numbering(line) if numbered else line.strip()
    title = _latex_command_arg(clean_line)
    star = "" if numbered else "*"
    if numbered:
        return f"\\{command}{{{title}}}\n"
    return f"\\{command}*{{{title}}}\n\\addcontentsline{{toc}}{{{command}}}{{{title}}}\n"


def _flush_list(output: list[str], list_type: str | None) -> None:
    if list_type == "itemize":
        output.append("\\end{itemize}\n")
    elif list_type == "enumerate":
        output.append("\\end{enumerate}\n")


def _start_list(output: list[str], current: str | None, target: str) -> str:
    if current != target:
        _flush_list(output, current)
        if target == "itemize":
            output.append("\\begin{itemize}[leftmargin=1.45em,itemsep=0.18em,topsep=0.25em]\n")
        else:
            output.append("\\begin{enumerate}[leftmargin=1.65em,itemsep=0.18em,topsep=0.25em]\n")
    return target


def _format_question_answer(line: str) -> str | None:
    if not _is_question_line(line):
        return None
    answer_match = ANSWER_RE.search(line)
    if not answer_match:
        return (
            "\\begin{quote}\n"
            f"\\noindent\\textbf{{{_latex_command_arg(line)}}}\n"
            "\\end{quote}\n"
        )
    question = line[:answer_match.start()].strip()
    answer = line[answer_match.end():].strip()
    label = answer_match.group("label").strip()
    return (
        "\\begin{quote}\n"
        f"\\noindent\\textbf{{{_latex_command_arg(question)}}}\\\\\n"
        f"\\textbf{{{_latex_command_arg(label)} :}} {_format_inline_math_markers(answer)}\n"
        "\\end{quote}\n"
    )


def _format_standalone_answer(line: str) -> str | None:
    match = ANSWER_RE.match(line.strip())
    if not match:
        return None
    label = match.group("label").strip()
    answer = line[match.end():].strip()
    return f"\\noindent\\textbf{{{_latex_command_arg(label)} :}} {_format_inline_math_markers(answer)}\n\n"


def _summary_to_latex_body(summary_text: str) -> str:
    """
    Convert the study-pack text to LaTeX conservatively.

    Important design decision:
    - Normal prose remains normal prose.
    - We do not infer full-line math from ordinary French/English sentences.
    - Only explicit [MATH]...[/MATH] blocks are rendered as LaTeX math.
    This prevents the broken glued text seen when a heuristic converts prose into math mode.
    """
    cleaned = polish_summary_text(summary_text)
    lines = cleaned.splitlines()
    output: list[str] = []
    current_list: str | None = None

    for raw in lines:
        line = raw.strip()

        if not line:
            _flush_list(output, current_list)
            current_list = None
            output.append("\n")
            continue

        full_math = MATH_BLOCK_RE.fullmatch(line)
        if full_math:
            _flush_list(output, current_list)
            current_list = None
            output.append(_display_math(full_math.group(1)))
            continue

        if line.startswith("# "):
            _flush_list(output, current_list)
            current_list = None
            output.append(_section_for_heading(line[2:].strip(), 1, numbered=False))
            continue

        if line.startswith("## "):
            _flush_list(output, current_list)
            current_list = None
            output.append(_section_for_heading(line[3:].strip(), 1))
            continue

        if line.startswith("### "):
            _flush_list(output, current_list)
            current_list = None
            output.append(_section_for_heading(line[4:].strip(), 2))
            continue

        if _is_main_numbered_section(line):
            _flush_list(output, current_list)
            current_list = None
            output.append(_section_for_heading(line, 1))
            continue

        if _is_subheading(line):
            _flush_list(output, current_list)
            current_list = None
            output.append(_section_for_heading(line, 2))
            continue

        qa = _format_question_answer(line)
        if qa:
            _flush_list(output, current_list)
            current_list = None
            output.append(qa)
            continue

        standalone_answer = _format_standalone_answer(line)
        if standalone_answer:
            _flush_list(output, current_list)
            current_list = None
            output.append(standalone_answer)
            continue

        if line.startswith(("- ", "* ", "• ", "— ")):
            current_list = _start_list(output, current_list, "itemize")
            item = line[2:].strip()
            output.append(f"  \\item {_format_inline_math_markers(item)}\n")
            continue

        enum_match = re.match(r"^(\d+)\.\s+(.+)$", line)
        if enum_match and not _is_main_numbered_section(line):
            current_list = _start_list(output, current_list, "enumerate")
            output.append(f"  \\item {_format_inline_math_markers(enum_match.group(2).strip())}\n")
            continue

        _flush_list(output, current_list)
        current_list = None
        output.append(f"{_format_inline_math_markers(line)}\n\n")

    _flush_list(output, current_list)
    return "".join(output).strip() + "\n"


FORMULA_CATALOG: tuple[FormulaCard, ...] = (
    FormulaCard(
        "series_additive", "Décomposition additive", "Additive decomposition",
        r"Y_t = T_t + S_t + C_t + \varepsilon_t",
        "Tendance, saisonnalité, cycle et bruit s'additionnent.",
        "Trend, seasonality, cycle, and noise are added.",
        ("decomposition", "additive", "additif", "trend", "tendance", "seasonality", "saisonnalite"),
    ),
    FormulaCard(
        "series_multiplicative", "Décomposition multiplicative", "Multiplicative decomposition",
        r"Y_t = T_t \times S_t \times \varepsilon_t",
        "La saisonnalité varie proportionnellement au niveau de la série.",
        "Seasonality varies proportionally to the series level.",
        ("multiplicative", "multiplicatif", "seasonality", "saisonnalite", "trend", "tendance"),
    ),
    FormulaCard(
        "linear_trend", "Tendance linéaire", "Linear trend",
        r"Y_t = a + bt + \varepsilon_t",
        "Modèle simple pour représenter une tendance linéaire.",
        "Simple model for a linear trend.",
        ("linear regression", "regression lineaire", "trend", "tendance", "least squares", "moindres carres"),
    ),
    FormulaCard(
        "moving_average", "Moyenne mobile", "Moving average",
        r"MM_t(p)=\frac{1}{p}\sum_{i=0}^{p-1}Y_{t-i}",
        "Lisse les fluctuations aléatoires avec une fenêtre de taille p.",
        "Smooths random fluctuations using a window of size p.",
        ("moving average", "moyenne mobile", "mmt", "mm_t"),
    ),
    FormulaCard(
        "exponential_smoothing", "Lissage exponentiel simple", "Simple exponential smoothing",
        r"\hat{Y}_{t+1}=\alpha Y_t+(1-\alpha)\hat{Y}_t",
        "Plus alpha est élevé, plus la prévision réagit aux observations récentes.",
        "A larger alpha gives more weight to recent observations.",
        ("simple exponential smoothing", "exponential smoothing", "lissage exponentiel simple", "alpha", "reactivity", "reactivite"),
    ),
    FormulaCard(
        "holt_forecast", "Prévision de Holt", "Holt forecast",
        r"\hat{Y}_{t+h}=L_t+hT_t",
        "Prévision avec niveau et tendance.",
        "Forecast with level and trend.",
        ("holt", "trend", "tendance", "forecast", "prevision"),
    ),
    FormulaCard(
        "mae", "Erreur absolue moyenne", "Mean Absolute Error",
        r"MAE=\frac{1}{T}\sum_{t=1}^{T}|Y_t-\hat{Y}_t|",
        "Mesure l'erreur moyenne en valeur absolue.",
        "Measures average absolute error.",
        ("mae", "error", "erreur", "forecast", "prevision"),
    ),
    FormulaCard(
        "rmse", "Racine de l'erreur quadratique moyenne", "Root Mean Squared Error",
        r"RMSE=\sqrt{\frac{1}{T}\sum_{t=1}^{T}(Y_t-\hat{Y}_t)^2}",
        "Pénalise davantage les grandes erreurs.",
        "Penalizes large errors more strongly.",
        ("rmse", "mse", "error", "erreur"),
    ),
    FormulaCard(
        "acf", "Autocorrélation", "Autocorrelation",
        r"\rho(h)=\frac{\gamma(h)}{\gamma(0)}",
        "Mesure la corrélation entre la série et sa version décalée.",
        "Measures correlation between a series and its lagged version.",
        ("acf", "autocorrelation", "autocorrelation", "rho", "gamma"),
    ),
    FormulaCard(
        "ssl", "Stationnarité au sens large", "Weak stationarity",
        r"E[X_t]=\mu,\quad Var(X_t)=\sigma^2,\quad Cov(X_t,X_{t+h})=\gamma(h)",
        "Moyenne, variance et autocovariance stables dans le temps.",
        "Mean, variance, and autocovariance remain stable over time.",
        ("stationarity", "weak stationarity", "stationnarite", "ssl", "variance", "autocovariance"),
    ),
    FormulaCard(
        "ar_p", "Processus AR(p)", "AR(p) process",
        r"X_t=\phi_1X_{t-1}+\cdots+\phi_pX_{t-p}+\varepsilon_t",
        "La valeur actuelle dépend linéairement de ses p valeurs passées.",
        "The current value depends linearly on its p past values.",
        ("ar(p)", "autoregressive", "autoregressif", "autorégressif"),
    ),
    FormulaCard(
        "ma_q", "Processus MA(q)", "MA(q) process",
        r"X_t=\varepsilon_t+\theta_1\varepsilon_{t-1}+\cdots+\theta_q\varepsilon_{t-q}",
        "La valeur actuelle dépend des erreurs présentes et passées.",
        "The current value depends on current and past shocks.",
        ("ma(q)", "moving average process", "moyenne mobile", "past shocks", "erreurs passees"),
    ),
    FormulaCard(
        "arma", "Processus ARMA(p,q)", "ARMA(p,q) process",
        r"\Phi(L)X_t=\Theta(L)\varepsilon_t",
        "Combine une partie autorégressive et une partie moyenne mobile.",
        "Combines autoregressive and moving-average components.",
        ("arma", "box-jenkins"),
    ),
    FormulaCard(
        "aic_bic", "Critères AIC/BIC", "AIC/BIC criteria",
        r"AIC=-2\ell+2k,\qquad BIC=-2\ell+k\ln(T)",
        "Permettent de comparer les modèles en pénalisant la complexité.",
        "Compare models while penalizing complexity.",
        ("aic", "bic", "criterion", "criteria", "critere"),
    ),
    FormulaCard(
        "support", "Support d'une règle", "Rule support",
        r"Support(X\rightarrow Y)=P(X\cup Y)",
        "Fréquence des transactions contenant X et Y.",
        "Frequency of transactions containing X and Y.",
        ("support", "association rule", "regle d'association", "association"),
    ),
    FormulaCard(
        "confidence", "Confiance d'une règle", "Rule confidence",
        r"Confiance(X\rightarrow Y)=\frac{Support(X\cup Y)}{Support(X)}",
        "Probabilité d'observer Y sachant que X est observé.",
        "Probability of observing Y given X.",
        ("confidence", "confiance", "association rule", "regle d'association"),
    ),
    FormulaCard(
        "entropy", "Entropie de Shannon", "Shannon entropy",
        r"I(s)=-\sum_i p_i\log_2(p_i)",
        "Mesure l'impureté d'un nœud dans un arbre de décision.",
        "Measures impurity in a decision-tree node.",
        ("entropy", "entropie", "shannon", "decision tree", "arbre de decision"),
    ),
    FormulaCard(
        "gini", "Indice de Gini", "Gini index",
        r"Gini(s)=1-\sum_i p_i^2",
        "Autre mesure d'impureté utilisée notamment par CART.",
        "Another impurity measure, notably used by CART.",
        ("gini", "cart", "arbre de decision"),
    ),
)


def _detect_formula_cards(summary_text: str, language: str | None = "fr") -> list[FormulaCard]:
    if not settings.TEX_INCLUDE_FORMULA_BOX:
        return []
    haystack = _normalised_search_text(summary_text)
    selected: list[FormulaCard] = []
    seen: set[str] = set()
    for card in FORMULA_CATALOG:
        if card.key in seen:
            continue
        if all(trigger in haystack for trigger in card.triggers[:1]) or any(trigger in haystack for trigger in card.triggers):
            # Avoid overly generic false positives for MA(q) caused by ordinary "moyenne mobile".
            if card.key == "ma_q" and "ma(q)" not in haystack and "moving average process" not in haystack and "processus moyenne mobile" not in haystack:
                continue
            selected.append(card)
            seen.add(card.key)
    return selected[: settings.TEX_MAX_FORMULA_CARDS]


def _formula_cards_to_latex(cards: list[FormulaCard], language: str | None = "fr") -> str:
    if not cards:
        return ""
    is_fr = (language or "fr").lower().startswith("fr")
    title = "Formules clés détectées" if is_fr else "Detected key formulas"
    body = [f"\\section{{{_latex_command_arg(title)}}}\n"]
    body.append("\\begin{description}[leftmargin=2.8cm,style=nextline,itemsep=0.65em]\n")
    for card in cards:
        label = card.title_fr if is_fr else card.title_en
        desc = card.description_fr if is_fr else card.description_en
        body.append(f"\\item[{_latex_command_arg(label)}] {_latex_escape(desc)}\n")
        body.append(_display_math(card.formula))
    body.append("\\end{description}\n")
    return "".join(body)


def _source_documents_to_latex(source_filename: str | None, language: str | None = "fr") -> str:
    """Render a compact source-document section for multi-file outputs."""
    if not source_filename:
        return ""
    raw = source_filename.strip()
    if not raw:
        return ""

    is_fr = (language or "fr").lower().startswith("fr")
    title = "Documents sources" if is_fr else "Source documents"

    docs: list[str]
    # Common metadata forms:
    # "2 documents: file1.pdf, file2.pptx"
    # "2 PDFs: file1.pdf, file2.pdf" (kept for backward compatibility)
    if ":" in raw:
        after_colon = raw.split(":", 1)[1]
        docs = [part.strip() for part in after_colon.split(",") if part.strip()]
    else:
        docs = [raw]

    if not docs:
        return ""

    out = [f"\\section*{{{_latex_command_arg(title)}}}\n", f"\\addcontentsline{{toc}}{{section}}{{{_latex_command_arg(title)}}}\n"]
    out.append("\\begin{itemize}[leftmargin=1.45em,itemsep=0.18em,topsep=0.25em]\n")
    for doc in docs:
        out.append(f"  \\item {_latex_escape(doc)}\n")
    out.append("\\end{itemize}\n\n")
    return "".join(out)


def generate_summary_tex(
    title: str,
    summary_text: str,
    output_path: Path,
    source_filename: str | None = None,
    model_name: str | None = None,
    language: str | None = "fr",
) -> Path:
    """
    Generate a standalone LaTeX source file for manual compilation.

    LaTeX strategy:
    - Keep the summary body conservative and readable.
    - Preserve Unicode symbols safely with newunicodechar mappings.
    - Do not heuristically convert prose to math mode.
    - Render a separate formula box from a curated formula catalogue when formulas are detected.
    - Support explicit [MATH]...[/MATH] blocks for future model outputs.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        lang = (language or "fr").lower()
        is_fr = lang.startswith("fr")
        babel_option = "french" if is_fr else "english"

        metadata_parts = [f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
        if source_filename:
            metadata_parts.append(f"Source: {source_filename}")
        if model_name:
            metadata_parts.append(f"Model: {model_name}")

        body = _summary_to_latex_body(summary_text)
        source_section = _source_documents_to_latex(source_filename, language=language)
        formula_cards = _detect_formula_cards(summary_text, language=language)
        formula_section = _formula_cards_to_latex(formula_cards, language=language)
        toc_name = "Table des matières" if is_fr else "Table of contents"

        tex_content = rf"""% Auto-generated by Education AI Summary Service.
% Compile with XeLaTeX or LuaLaTeX.
\documentclass[11pt,a4paper]{{article}}

\usepackage[a4paper,margin=2.1cm]{{geometry}}
\usepackage{{fontspec}}
\usepackage[{babel_option}]{{babel}}
\usepackage{{amsmath,amssymb}}
\usepackage{{newunicodechar}}
\usepackage{{enumitem}}
\usepackage{{xcolor}}
\usepackage{{hyperref}}
\usepackage{{fancyhdr}}
\usepackage{{titlesec}}
\usepackage{{microtype}}
\usepackage{{longtable}}
\usepackage{{array}}
\usepackage{{booktabs}}

% Safe Unicode math-like symbols in normal text.
\newunicodechar{{α}}{{\ensuremath{{\alpha}}}}
\newunicodechar{{β}}{{\ensuremath{{\beta}}}}
\newunicodechar{{γ}}{{\ensuremath{{\gamma}}}}
\newunicodechar{{δ}}{{\ensuremath{{\delta}}}}
\newunicodechar{{ε}}{{\ensuremath{{\varepsilon}}}}
\newunicodechar{{ϵ}}{{\ensuremath{{\epsilon}}}}
\newunicodechar{{θ}}{{\ensuremath{{\theta}}}}
\newunicodechar{{ϕ}}{{\ensuremath{{\phi}}}}
\newunicodechar{{φ}}{{\ensuremath{{\phi}}}}
\newunicodechar{{μ}}{{\ensuremath{{\mu}}}}
\newunicodechar{{µ}}{{\ensuremath{{\mu}}}}
\newunicodechar{{σ}}{{\ensuremath{{\sigma}}}}
\newunicodechar{{ρ}}{{\ensuremath{{\rho}}}}
\newunicodechar{{λ}}{{\ensuremath{{\lambda}}}}
\newunicodechar{{ℓ}}{{\ensuremath{{\ell}}}}
\newunicodechar{{Φ}}{{\ensuremath{{\Phi}}}}
\newunicodechar{{Θ}}{{\ensuremath{{\Theta}}}}
\newunicodechar{{Ψ}}{{\ensuremath{{\Psi}}}}
\newunicodechar{{Π}}{{\ensuremath{{\Pi}}}}
\newunicodechar{{Σ}}{{\ensuremath{{\sum}}}}
\newunicodechar{{∑}}{{\ensuremath{{\sum}}}}
\newunicodechar{{∞}}{{\ensuremath{{\infty}}}}
\newunicodechar{{∈}}{{\ensuremath{{\in}}}}
\newunicodechar{{∉}}{{\ensuremath{{\notin}}}}
\newunicodechar{{≤}}{{\ensuremath{{\leq}}}}
\newunicodechar{{≥}}{{\ensuremath{{\geq}}}}
\newunicodechar{{≠}}{{\ensuremath{{\neq}}}}
\newunicodechar{{≈}}{{\ensuremath{{\approx}}}}
\newunicodechar{{±}}{{\ensuremath{{\pm}}}}
\newunicodechar{{→}}{{\ensuremath{{\rightarrow}}}}
\newunicodechar{{⇒}}{{\ensuremath{{\Rightarrow}}}}
\newunicodechar{{∪}}{{\ensuremath{{\cup}}}}
\newunicodechar{{∩}}{{\ensuremath{{\cap}}}}
\newunicodechar{{⊆}}{{\ensuremath{{\subseteq}}}}
\newunicodechar{{×}}{{\ensuremath{{\times}}}}
\newunicodechar{{²}}{{\ensuremath{{^2}}}}
\newunicodechar{{³}}{{\ensuremath{{^3}}}}
\newunicodechar{{−}}{{-}}
\newunicodechar{{ˆ}}{{\^{{}}}}
\newunicodechar{{¯}}{{\={{}}}}

\definecolor{{EduBlue}}{{HTML}}{{1F4E79}}
\definecolor{{EduGray}}{{HTML}}{{555555}}
\definecolor{{EduLight}}{{HTML}}{{F3F6FA}}

\hypersetup{{
  colorlinks=true,
  linkcolor=EduBlue,
  urlcolor=EduBlue,
  pdftitle={{{_latex_command_arg(title)}}},
  pdfauthor={{Education AI Center}}
}}

\setlength{{\headheight}}{{14pt}}
\pagestyle{{fancy}}
\fancyhf{{}}
\lhead{{Education AI Center}}
\rhead{{Summary}}
\cfoot{{\thepage}}
\renewcommand{{\headrulewidth}}{{0.4pt}}

\titleformat{{\section}}
  {{\Large\bfseries\color{{EduBlue}}}}
  {{}}
  {{0pt}}
  {{}}
\titleformat{{\subsection}}
  {{\large\bfseries\color{{black}}}}
  {{}}
  {{0pt}}
  {{}}
\titleformat{{\subsubsection}}
  {{\normalsize\bfseries\color{{EduGray}}}}
  {{}}
  {{0pt}}
  {{}}

\setcounter{{tocdepth}}{{1}}
\setcounter{{secnumdepth}}{{0}}
\setlength{{\parindent}}{{0pt}}
\setlength{{\parskip}}{{0.55em}}
\setlist[itemize]{{leftmargin=1.4em,label=\textbullet}}
\setlist[enumerate]{{leftmargin=1.6em}}
\emergencystretch=3em
\sloppy

\begin{{document}}

\begin{{titlepage}}
\centering
{{\Huge\bfseries {_latex_command_arg(title)}\par}}
\vspace{{1cm}}
{{\large {_latex_command_arg(' | '.join(metadata_parts))}\par}}
\vfill
{{\large Education AI Center\par}}
\end{{titlepage}}

\renewcommand{{\contentsname}}{{{_latex_command_arg(toc_name)}}}
\tableofcontents
\newpage

{source_section}
{body}

{formula_section}

\end{{document}}
"""

        output_path.write_text(tex_content, encoding="utf-8")

    except Exception as exc:
        raise TexGenerationError(f"Could not generate TeX summary: {exc}") from exc

    return output_path
