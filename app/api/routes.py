from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.models.schemas import (
    DocumentInfo,
    ExtractResponse,
    HealthResponse,
    JobCreateResponse,
    JobStatusResponse,
    MultiExtractResponse,
    MultiSummaryResponse,
    ProviderResponse,
    SummaryResponse,
)
from app.services.file_utils import create_document_id, sanitize_filename, save_upload_file
from app.services.pdf_extractor import PdfExtractionError, extract_text_from_pdf
from app.services.pdf_generator import PdfGenerationError, generate_summary_pdf
from app.services.tex_generator import TexGenerationError, generate_summary_tex
from app.services.summarizer import SummarizationError, summarize_text
from app.services.text_polish import polish_summary_text
from app.services.job_service import (
    create_job_id,
    create_job_metadata,
    delete_job,
    job_upload_dir,
    load_job,
    normalize_output_format,
    process_summary_job,
    public_job_view,
)

router = APIRouter()


def _default_model_for_provider(provider: str) -> str:
    provider = (provider or settings.LLM_PROVIDER).strip().lower()
    if provider in {"gemini", "google", "online"}:
        return settings.GEMINI_MODEL
    if provider == "auto" and settings.GEMINI_API_KEY:
        return settings.GEMINI_MODEL
    return settings.OLLAMA_MODEL


def _validate_pdf_upload(file: UploadFile) -> None:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail=f"Only PDF files are supported for now. Invalid file: {file.filename}")

    if file.content_type and file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status_code=400, detail=f"Invalid file type for {file.filename}. Please upload a PDF file.")


def _validate_multiple_pdf_uploads(files: list[UploadFile]) -> None:
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one supported document file.")

    if len(files) > settings.MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum allowed per request is {settings.MAX_FILES_PER_REQUEST}.",
        )

    for file in files:
        _validate_pdf_upload(file)


def _uploaded_path(document_id: str, filename: str) -> Path:
    safe_filename = sanitize_filename(filename)
    return settings.upload_dir / f"{document_id}_{safe_filename}"


def _summary_path(document_id: str) -> Path:
    return settings.summary_dir / f"summary_{document_id}.pdf"


def _resolve_output_flags(
    *,
    output_format: str | None,
    generate_pdf: bool,
    generate_tex: bool,
) -> tuple[bool, bool]:
    """
    Resolve output generation flags for synchronous endpoints.

    Backward compatible behavior:
    - Old callers can still use generate_pdf=true and generate_tex=true.
    - New callers can use output_format=json|pdf|tex|both.
    - When output_format is provided, it is the source of truth.
    """
    if output_format is None or not output_format.strip():
        return generate_pdf, generate_tex

    normalized = normalize_output_format(output_format)
    return normalized in {"pdf", "both"}, normalized in {"tex", "both"}


def _tex_path(document_id: str) -> Path:
    return settings.tex_dir / f"summary_{document_id}.tex"


def _source_label(filenames: list[str]) -> str:
    if len(filenames) == 1:
        return filenames[0]
    shown = filenames[:4]
    suffix = "" if len(filenames) <= 4 else f", +{len(filenames) - 4} more"
    return f"{len(filenames)} documents: {', '.join(shown)}{suffix}"


async def _save_and_extract_one(file: UploadFile) -> tuple[DocumentInfo, str, Path, int]:
    document_id = create_document_id()
    file_path = _uploaded_path(document_id, file.filename or "document.pdf")
    size_bytes = await save_upload_file(file, file_path, settings.max_upload_size_bytes)
    extracted = extract_text_from_pdf(file_path)

    if not extracted.text.strip():
        raise HTTPException(
            status_code=400,
            detail=f"No readable text found in {file.filename}. This may be a scanned/image-based PDF. OCR is not enabled in this version.",
        )

    info = DocumentInfo(
        document_id=document_id,
        original_filename=file.filename or "document.pdf",
        page_count=extracted.page_count,
        text_length=len(extracted.text),
    )
    return info, extracted.text, file_path, size_bytes


def _combine_documents_for_summary(documents: list[DocumentInfo], texts: list[str]) -> str:
    blocks: list[str] = []
    for index, (doc, text) in enumerate(zip(documents, texts), start=1):
        blocks.append(
            "\n".join(
                [
                    f"===== DOCUMENT {index}: {doc.original_filename} =====",
                    f"Pages: {doc.page_count}",
                    f"Extracted characters: {doc.text_length}",
                    "",
                    text,
                ]
            )
        )
    return "\n\n".join(blocks)


def _unlink_paths(paths: list[Path]) -> None:
    if settings.KEEP_UPLOADED_FILES:
        return
    for path in paths:
        path.unlink(missing_ok=True)


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        default_provider=settings.LLM_PROVIDER,
        default_model=_default_model_for_provider(settings.LLM_PROVIDER),
    )


@router.get("/providers", response_model=ProviderResponse)
def providers() -> ProviderResponse:
    return ProviderResponse(
        default_provider=settings.LLM_PROVIDER,
        default_model=_default_model_for_provider(settings.LLM_PROVIDER),
        available_providers=["ollama", "gemini", "auto"],
        ollama_model=settings.OLLAMA_MODEL,
        ollama_base_url=settings.OLLAMA_BASE_URL,
        gemini_model=settings.GEMINI_MODEL,
        gemini_configured=bool(settings.GEMINI_API_KEY),
        gemini_single_call_study_pack=settings.GEMINI_SINGLE_CALL_STUDY_PACK,
        gemini_retry_attempts=settings.GEMINI_RETRY_ATTEMPTS,
        gemini_fallback_to_ollama=settings.GEMINI_FALLBACK_TO_OLLAMA,
    )


@router.post("/extract", response_model=ExtractResponse)
async def extract_pdf_text(file: UploadFile = File(...)) -> ExtractResponse:
    """
    Upload one PDF and extract its text without summarizing it.
    Useful for debugging PDF parsing.
    """
    _validate_pdf_upload(file)
    file_path: Path | None = None

    try:
        info, text, file_path, _ = await _save_and_extract_one(file)

        return ExtractResponse(
            document_id=info.document_id,
            original_filename=info.original_filename,
            page_count=info.page_count,
            text_length=info.text_length,
            preview=text[:1200],
        )

    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PdfExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        if file_path:
            _unlink_paths([file_path])


@router.post("/extract-multiple", response_model=MultiExtractResponse)
async def extract_multiple_pdf_text(files: list[UploadFile] = File(...)) -> MultiExtractResponse:
    """
    Upload multiple documents and extract text previews without summarizing them.
    """
    _validate_multiple_pdf_uploads(files)

    documents: list[DocumentInfo] = []
    texts: list[str] = []
    paths: list[Path] = []
    total_size = 0
    collection_id = create_document_id()

    try:
        for file in files:
            info, text, file_path, size_bytes = await _save_and_extract_one(file)
            total_size += size_bytes
            if total_size > settings.max_total_upload_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Combined upload is too large. Maximum total size is {settings.MAX_TOTAL_UPLOAD_SIZE_MB} MB.",
                )
            documents.append(info)
            texts.append(text)
            paths.append(file_path)

        combined = _combine_documents_for_summary(documents, texts)
        return MultiExtractResponse(
            collection_id=collection_id,
            document_count=len(documents),
            total_page_count=sum(doc.page_count for doc in documents),
            total_text_length=sum(doc.text_length for doc in documents),
            documents=documents,
            preview=combined[:1800],
        )

    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PdfExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        _unlink_paths(paths)


@router.post("/summarize", response_model=SummaryResponse)
async def summarize_pdf(
    file: UploadFile = File(...),
    generate_pdf: bool = Query(False, description="Generate a downloadable PDF summary. Backward compatible with output_format."),
    generate_tex: bool = Query(False, description="Generate a downloadable LaTeX .tex summary source. Backward compatible with output_format."),
    output_format: str | None = Query(None, description="Optional unified output format: json, pdf, tex, or both. Overrides generate_pdf/generate_tex when provided."),
    mode: str | None = Query(None, description="Summary mode: fast, study_pack, gemini_single, or detailed."),
    language: str | None = Query(None, description="Output language: fr or en."),
    provider: str | None = Query(None, description="LLM provider: ollama/local or gemini/online. Use auto to prefer Gemini if configured."),
    style: str | None = Query(None, description="Style: student_friendly, academic, concise, detailed, exam_revision, or cheatsheet."),
) -> SummaryResponse:
    """
    Upload one PDF, extract text, summarize it with Ollama or Gemini, and optionally generate a PDF summary.
    """
    _validate_pdf_upload(file)

    file_path: Path | None = None
    pdf_download_url = None
    tex_download_url = None
    generate_pdf, generate_tex = _resolve_output_flags(
        output_format=output_format,
        generate_pdf=generate_pdf,
        generate_tex=generate_tex,
    )

    try:
        info, text, file_path, _ = await _save_and_extract_one(file)
        summary_result = summarize_text(text, mode=mode, language=language, provider=provider, style=style)
        summary = polish_summary_text(summary_result.summary)

        if generate_pdf:
            summary_file_path = _summary_path(info.document_id)
            generate_summary_pdf(
                title=f"Summary of {info.original_filename}",
                summary_text=summary,
                output_path=summary_file_path,
                source_filename=info.original_filename,
                model_name=f"{summary_result.provider}:{summary_result.model}",
            )
            pdf_download_url = f"/api/v1/summaries/{summary_file_path.name}"

        if generate_tex:
            tex_file_path = _tex_path(info.document_id)
            generate_summary_tex(
                title=f"Summary of {info.original_filename}",
                summary_text=summary,
                output_path=tex_file_path,
                source_filename=info.original_filename,
                model_name=f"{summary_result.provider}:{summary_result.model}",
                language=language or settings.SUMMARY_LANGUAGE,
            )
            tex_download_url = f"/api/v1/tex/{tex_file_path.name}"

        return SummaryResponse(
            document_id=info.document_id,
            original_filename=info.original_filename,
            page_count=info.page_count,
            text_length=info.text_length,
            provider=summary_result.provider,
            model=summary_result.model,
            summary_mode=summary_result.mode,
            sections_processed=summary_result.sections_processed,
            summary=summary,
            pdf_generated=generate_pdf,
            pdf_download_url=pdf_download_url,
            tex_generated=generate_tex,
            tex_download_url=tex_download_url,
        )

    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PdfExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SummarizationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except PdfGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except TexGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if file_path:
            _unlink_paths([file_path])


@router.post("/summarize-multiple", response_model=MultiSummaryResponse)
async def summarize_multiple_pdfs(
    files: list[UploadFile] = File(...),
    generate_pdf: bool = Query(False, description="Generate one downloadable PDF summary for all uploaded documents. Backward compatible with output_format."),
    generate_tex: bool = Query(False, description="Generate one downloadable LaTeX .tex summary source for all uploaded documents. Backward compatible with output_format."),
    output_format: str | None = Query(None, description="Optional unified output format: json, pdf, tex, or both. Overrides generate_pdf/generate_tex when provided."),
    mode: str | None = Query(None, description="Summary mode: fast, study_pack, gemini_single, or detailed."),
    language: str | None = Query(None, description="Output language: fr or en."),
    provider: str | None = Query(None, description="LLM provider: ollama/local or gemini/online. Use auto to prefer Gemini if configured."),
    style: str | None = Query(None, description="Style: student_friendly, academic, concise, detailed, exam_revision, or cheatsheet."),
) -> MultiSummaryResponse:
    """
    Upload multiple documents and generate one combined study pack.

    Use this when a student uploads several course files, such as course slides,
    TD sheets, exam notes, or chapter documents.
    """
    _validate_multiple_pdf_uploads(files)

    collection_id = create_document_id()
    documents: list[DocumentInfo] = []
    texts: list[str] = []
    paths: list[Path] = []
    total_size = 0
    pdf_download_url = None
    tex_download_url = None
    generate_pdf, generate_tex = _resolve_output_flags(
        output_format=output_format,
        generate_pdf=generate_pdf,
        generate_tex=generate_tex,
    )

    try:
        for file in files:
            info, text, file_path, size_bytes = await _save_and_extract_one(file)
            total_size += size_bytes
            if total_size > settings.max_total_upload_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Combined upload is too large. Maximum total size is {settings.MAX_TOTAL_UPLOAD_SIZE_MB} MB.",
                )
            documents.append(info)
            texts.append(text)
            paths.append(file_path)

        combined_text = _combine_documents_for_summary(documents, texts)
        summary_result = summarize_text(combined_text, mode=mode, language=language, provider=provider, style=style)
        summary = polish_summary_text(summary_result.summary)

        if generate_pdf:
            summary_file_path = _summary_path(collection_id)
            generate_summary_pdf(
                title=f"Combined Summary of {len(documents)} documents",
                summary_text=summary,
                output_path=summary_file_path,
                source_filename=_source_label([doc.original_filename for doc in documents]),
                model_name=f"{summary_result.provider}:{summary_result.model}",
            )
            pdf_download_url = f"/api/v1/summaries/{summary_file_path.name}"

        if generate_tex:
            tex_file_path = _tex_path(collection_id)
            generate_summary_tex(
                title=f"Combined Summary of {len(documents)} documents",
                summary_text=summary,
                output_path=tex_file_path,
                source_filename=_source_label([doc.original_filename for doc in documents]),
                model_name=f"{summary_result.provider}:{summary_result.model}",
                language=language or settings.SUMMARY_LANGUAGE,
            )
            tex_download_url = f"/api/v1/tex/{tex_file_path.name}"

        return MultiSummaryResponse(
            collection_id=collection_id,
            document_count=len(documents),
            total_page_count=sum(doc.page_count for doc in documents),
            total_text_length=sum(doc.text_length for doc in documents),
            documents=documents,
            provider=summary_result.provider,
            model=summary_result.model,
            summary_mode=summary_result.mode,
            sections_processed=summary_result.sections_processed,
            summary=summary,
            pdf_generated=generate_pdf,
            pdf_download_url=pdf_download_url,
            tex_generated=generate_tex,
            tex_download_url=tex_download_url,
        )

    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PdfExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SummarizationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except PdfGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except TexGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        _unlink_paths(paths)


@router.post("/generate-summary-pdf")
async def generate_summary_pdf_direct(
    file: UploadFile = File(...),
    mode: str | None = Query("study_pack", description="Summary mode: fast, study_pack, gemini_single, or detailed."),
    language: str | None = Query(None, description="Output language: fr or en."),
    provider: str | None = Query(None, description="LLM provider: ollama/local or gemini/online. Use auto to prefer Gemini if configured."),
    style: str | None = Query(None, description="Style: student_friendly, academic, concise, detailed, exam_revision, or cheatsheet."),
) -> FileResponse:
    """
    Upload one supported document and directly receive the generated summary PDF as a file download.
    """
    _validate_pdf_upload(file)

    file_path: Path | None = None
    summary_file_path: Path | None = None

    try:
        info, text, file_path, _ = await _save_and_extract_one(file)
        summary_file_path = _summary_path(info.document_id)

        summary_result = summarize_text(text, mode=mode, language=language, provider=provider, style=style)
        generate_summary_pdf(
            title=f"Summary of {info.original_filename}",
            summary_text=summary_result.summary,
            output_path=summary_file_path,
            source_filename=info.original_filename,
            model_name=f"{summary_result.provider}:{summary_result.model}",
        )

        return FileResponse(
            path=summary_file_path,
            media_type="application/pdf",
            filename=summary_file_path.name,
        )

    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PdfExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SummarizationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except PdfGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if file_path:
            _unlink_paths([file_path])


@router.post("/generate-summary-pdf-multiple")
async def generate_summary_pdf_multiple_direct(
    files: list[UploadFile] = File(...),
    mode: str | None = Query("study_pack", description="Summary mode: fast, study_pack, gemini_single, or detailed."),
    language: str | None = Query(None, description="Output language: fr or en."),
    provider: str | None = Query(None, description="LLM provider: ollama/local or gemini/online. Use auto to prefer Gemini if configured."),
    style: str | None = Query(None, description="Style: student_friendly, academic, concise, detailed, exam_revision, or cheatsheet."),
) -> FileResponse:
    """
    Upload multiple supported documents and directly receive one combined summary PDF as a file download.
    """
    _validate_multiple_pdf_uploads(files)

    collection_id = create_document_id()
    documents: list[DocumentInfo] = []
    texts: list[str] = []
    paths: list[Path] = []
    total_size = 0
    summary_file_path = _summary_path(collection_id)

    try:
        for file in files:
            info, text, file_path, size_bytes = await _save_and_extract_one(file)
            total_size += size_bytes
            if total_size > settings.max_total_upload_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Combined upload is too large. Maximum total size is {settings.MAX_TOTAL_UPLOAD_SIZE_MB} MB.",
                )
            documents.append(info)
            texts.append(text)
            paths.append(file_path)

        combined_text = _combine_documents_for_summary(documents, texts)
        summary_result = summarize_text(combined_text, mode=mode, language=language, provider=provider, style=style)

        generate_summary_pdf(
            title=f"Combined Summary of {len(documents)} documents",
            summary_text=summary_result.summary,
            output_path=summary_file_path,
            source_filename=_source_label([doc.original_filename for doc in documents]),
            model_name=f"{summary_result.provider}:{summary_result.model}",
        )

        return FileResponse(
            path=summary_file_path,
            media_type="application/pdf",
            filename=summary_file_path.name,
        )

    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PdfExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SummarizationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except PdfGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        _unlink_paths(paths)


@router.post("/generate-summary-tex")
async def generate_summary_tex_direct(
    file: UploadFile = File(...),
    mode: str | None = Query("study_pack", description="Summary mode: fast, study_pack, gemini_single, or detailed."),
    language: str | None = Query(None, description="Output language: fr or en."),
    provider: str | None = Query(None, description="LLM provider: ollama/local or gemini/online. Use auto to prefer Gemini if configured."),
    style: str | None = Query(None, description="Style: student_friendly, academic, concise, detailed, exam_revision, or cheatsheet."),
) -> FileResponse:
    """
    Upload one supported document and directly receive the generated LaTeX .tex summary source.

    The service does not compile the TeX file. Compile it manually with XeLaTeX or LuaLaTeX.
    """
    _validate_pdf_upload(file)

    file_path: Path | None = None
    tex_file_path: Path | None = None

    try:
        info, text, file_path, _ = await _save_and_extract_one(file)
        tex_file_path = _tex_path(info.document_id)

        summary_result = summarize_text(text, mode=mode, language=language, provider=provider, style=style)
        summary = polish_summary_text(summary_result.summary)
        generate_summary_tex(
            title=f"Summary of {info.original_filename}",
            summary_text=summary,
            output_path=tex_file_path,
            source_filename=info.original_filename,
            model_name=f"{summary_result.provider}:{summary_result.model}",
            language=language or settings.SUMMARY_LANGUAGE,
        )

        return FileResponse(
            path=tex_file_path,
            media_type="application/x-tex",
            filename=tex_file_path.name,
        )

    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PdfExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SummarizationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except TexGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        if file_path:
            _unlink_paths([file_path])


@router.post("/generate-summary-tex-multiple")
async def generate_summary_tex_multiple_direct(
    files: list[UploadFile] = File(...),
    mode: str | None = Query("study_pack", description="Summary mode: fast, study_pack, gemini_single, or detailed."),
    language: str | None = Query(None, description="Output language: fr or en."),
    provider: str | None = Query(None, description="LLM provider: ollama/local or gemini/online. Use auto to prefer Gemini if configured."),
    style: str | None = Query(None, description="Style: student_friendly, academic, concise, detailed, exam_revision, or cheatsheet."),
) -> FileResponse:
    """
    Upload multiple supported documents and directly receive one combined LaTeX .tex summary source.

    The service does not compile the TeX file. Compile it manually with XeLaTeX or LuaLaTeX.
    """
    _validate_multiple_pdf_uploads(files)

    collection_id = create_document_id()
    documents: list[DocumentInfo] = []
    texts: list[str] = []
    paths: list[Path] = []
    total_size = 0
    tex_file_path = _tex_path(collection_id)

    try:
        for file in files:
            info, text, file_path, size_bytes = await _save_and_extract_one(file)
            total_size += size_bytes
            if total_size > settings.max_total_upload_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Combined upload is too large. Maximum total size is {settings.MAX_TOTAL_UPLOAD_SIZE_MB} MB.",
                )
            documents.append(info)
            texts.append(text)
            paths.append(file_path)

        combined_text = _combine_documents_for_summary(documents, texts)
        summary_result = summarize_text(combined_text, mode=mode, language=language, provider=provider, style=style)
        summary = polish_summary_text(summary_result.summary)

        generate_summary_tex(
            title=f"Combined Summary of {len(documents)} documents",
            summary_text=summary,
            output_path=tex_file_path,
            source_filename=_source_label([doc.original_filename for doc in documents]),
            model_name=f"{summary_result.provider}:{summary_result.model}",
            language=language or settings.SUMMARY_LANGUAGE,
        )

        return FileResponse(
            path=tex_file_path,
            media_type="application/x-tex",
            filename=tex_file_path.name,
        )

    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except PdfExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except SummarizationError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except TexGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        _unlink_paths(paths)


@router.post("/jobs", response_model=JobCreateResponse, status_code=202)
async def create_summary_job(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    language: str | None = Form(None, description="Output language: fr, en, or auto."),
    mode: str = Form("study_pack", description="Summary mode: fast, study_pack, gemini_single, detailed."),
    provider: str | None = Form(None, description="Provider: auto, gemini, or ollama."),
    output_format: str = Form("both", description="json, pdf, tex, or both."),
    style: str = Form("student_friendly", description="student_friendly, academic, concise, detailed, exam_revision, or cheatsheet."),
) -> JobCreateResponse:
    """
    Create an asynchronous summary job.

    This is the recommended endpoint for the bigger NotebookLM-like app.
    It returns immediately with a job_id. The frontend can then poll /jobs/{job_id}.
    """
    _validate_multiple_pdf_uploads(files)

    try:
        normalized_output_format = normalize_output_format(output_format)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    job_id = create_job_id()
    upload_dir = job_upload_dir(job_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[dict] = []
    total_size = 0

    try:
        for file in files:
            document_id = create_document_id()
            safe_name = sanitize_filename(file.filename or "document.pdf")
            destination = upload_dir / f"{document_id}_{safe_name}"
            size_bytes = await save_upload_file(file, destination, settings.max_upload_size_bytes)
            total_size += size_bytes

            if total_size > settings.max_total_upload_size_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Combined upload is too large. Maximum total size is {settings.MAX_TOTAL_UPLOAD_SIZE_MB} MB.",
                )

            saved_files.append(
                {
                    "document_id": document_id,
                    "original_filename": file.filename or "document.pdf",
                    "stored_path": str(destination),
                    "size_bytes": size_bytes,
                }
            )

        parameters = {
            "language": language or settings.SUMMARY_LANGUAGE,
            "mode": mode,
            "provider": provider or settings.LLM_PROVIDER,
            "output_format": normalized_output_format,
            "style": style or settings.SUMMARY_STYLE,
        }

        create_job_metadata(job_id=job_id, files=saved_files, parameters=parameters)
        background_tasks.add_task(process_summary_job, job_id)

        return JobCreateResponse(
            job_id=job_id,
            status="queued",
            progress=0,
            step="Queued",
            status_url=f"/api/v1/jobs/{job_id}",
            summary_url=f"/api/v1/jobs/{job_id}/summary",
            pdf_url=f"/api/v1/jobs/{job_id}/pdf" if normalized_output_format in {"pdf", "both"} else None,
            tex_url=f"/api/v1/jobs/{job_id}/tex" if normalized_output_format in {"tex", "both"} else None,
            parameters=parameters,
        )

    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_summary_job(job_id: str) -> JobStatusResponse:
    """Return job status, progress, parameters, and result URLs."""
    try:
        job = load_job(job_id)
        return JobStatusResponse(**public_job_view(job, include_summary=False))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.")


@router.get("/jobs/{job_id}/summary")
def get_summary_job_text(job_id: str) -> dict:
    """Return the generated summary text once the job is completed."""
    try:
        job = load_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"Job is not completed yet. Current status: {job.get('status')}")

    return {
        "job_id": job_id,
        "summary": job.get("summary"),
        "provider": job.get("provider"),
        "model": job.get("model"),
        "summary_mode": job.get("summary_mode"),
        "documents": job.get("documents", []),
        "pdf_download_url": job.get("pdf_download_url"),
        "tex_download_url": job.get("tex_download_url"),
    }


@router.get("/jobs/{job_id}/pdf")
def download_summary_job_pdf(job_id: str) -> FileResponse:
    """Download the job PDF output, if output_format was pdf or both."""
    try:
        job = load_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"Job is not completed yet. Current status: {job.get('status')}")

    pdf_path = job.get("pdf_path")
    if not pdf_path or not Path(pdf_path).exists():
        raise HTTPException(status_code=404, detail="This job has no generated PDF output.")

    return FileResponse(path=pdf_path, media_type="application/pdf", filename=Path(pdf_path).name)


@router.get("/jobs/{job_id}/tex")
def download_summary_job_tex(job_id: str) -> FileResponse:
    """Download the job TeX output, if output_format was tex or both."""
    try:
        job = load_job(job_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.")

    if job.get("status") != "completed":
        raise HTTPException(status_code=409, detail=f"Job is not completed yet. Current status: {job.get('status')}")

    tex_path = job.get("tex_path")
    if not tex_path or not Path(tex_path).exists():
        raise HTTPException(status_code=404, detail="This job has no generated TeX output.")

    return FileResponse(path=tex_path, media_type="application/x-tex", filename=Path(tex_path).name)


@router.delete("/jobs/{job_id}")
def delete_summary_job(job_id: str) -> dict:
    """Delete a job record and its generated outputs."""
    try:
        delete_job(job_id)
        return {"job_id": job_id, "deleted": True}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Job not found.")


@router.get("/tex/{filename}")
def download_summary_tex(filename: str) -> FileResponse:
    """
    Download a previously generated LaTeX .tex summary source.
    """
    safe_filename = sanitize_filename(filename)
    file_path = settings.tex_dir / safe_filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Summary TeX file not found.")

    return FileResponse(
        path=file_path,
        media_type="application/x-tex",
        filename=safe_filename,
    )


@router.get("/summaries/{filename}")
def download_summary_pdf(filename: str) -> FileResponse:
    """
    Download a previously generated PDF summary.
    """
    safe_filename = sanitize_filename(filename)
    file_path = settings.summary_dir / safe_filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Summary PDF not found.")

    return FileResponse(
        path=file_path,
        media_type="application/pdf",
        filename=safe_filename,
    )
