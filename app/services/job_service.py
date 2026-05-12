from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import shutil
import uuid
from typing import Any

from app.core.config import settings
from app.services.pdf_extractor import extract_text_from_pdf
from app.services.pdf_generator import generate_summary_pdf
from app.services.summarizer import summarize_text
from app.services.tex_generator import generate_summary_tex
from app.services.text_polish import polish_summary_text


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
OUTPUT_FORMATS = {"json", "pdf", "tex", "both"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job_id() -> str:
    return f"job_{uuid.uuid4()}"


def normalize_output_format(value: str | None) -> str:
    selected = (value or "both").strip().lower()
    aliases = {
        "summary": "json",
        "text": "json",
        "reportlab": "pdf",
        "latex": "tex",
        "tex_pdf": "tex",
        "pdf_tex": "both",
        "all": "both",
    }
    selected = aliases.get(selected, selected)
    if selected not in OUTPUT_FORMATS:
        raise ValueError("Invalid output_format. Use json, pdf, tex, or both.")
    return selected


def job_dir(job_id: str) -> Path:
    return settings.jobs_dir / job_id


def job_upload_dir(job_id: str) -> Path:
    return job_dir(job_id) / "uploads"


def job_metadata_path(job_id: str) -> Path:
    return job_dir(job_id) / "job.json"


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def load_job(job_id: str) -> dict[str, Any]:
    path = job_metadata_path(job_id)
    if not path.exists():
        raise FileNotFoundError("Job not found.")
    return json.loads(path.read_text(encoding="utf-8"))


def save_job(job: dict[str, Any]) -> None:
    _write_json_atomic(job_metadata_path(job["job_id"]), job)


def update_job(job_id: str, **fields: Any) -> dict[str, Any]:
    job = load_job(job_id)
    job.update(fields)
    job["updated_at"] = utc_now()
    save_job(job)
    return job


def create_job_metadata(
    *,
    job_id: str,
    files: list[dict[str, Any]],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    now = utc_now()
    job = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "step": "Queued",
        "created_at": now,
        "updated_at": now,
        "parameters": parameters,
        "files": files,
        "documents": [],
        "document_count": len(files),
        "total_page_count": 0,
        "total_text_length": 0,
        "provider": None,
        "model": None,
        "summary_mode": parameters.get("mode"),
        "summary": None,
        "pdf_generated": False,
        "pdf_download_url": None,
        "pdf_path": None,
        "tex_generated": False,
        "tex_download_url": None,
        "tex_path": None,
        "error": None,
    }
    save_job(job)
    return job


def public_job_view(job: dict[str, Any], include_summary: bool = True) -> dict[str, Any]:
    visible = {k: v for k, v in job.items() if k not in {"files", "pdf_path", "tex_path"}}
    if not include_summary:
        visible.pop("summary", None)
    return visible


def _source_label(filenames: list[str]) -> str:
    if len(filenames) == 1:
        return filenames[0]
    shown = filenames[:4]
    suffix = "" if len(filenames) <= 4 else f", +{len(filenames) - 4} more"
    return f"{len(filenames)} documents: {', '.join(shown)}{suffix}"


def _combine_documents_for_summary(documents: list[dict[str, Any]], texts: list[str]) -> str:
    blocks: list[str] = []
    for index, (doc, text) in enumerate(zip(documents, texts), start=1):
        blocks.append(
            "\n".join(
                [
                    f"===== DOCUMENT {index}: {doc['original_filename']} =====",
                    f"Pages: {doc['page_count']}",
                    f"Extracted characters: {doc['text_length']}",
                    "",
                    text,
                ]
            )
        )
    return "\n\n".join(blocks)


def should_generate_pdf(output_format: str) -> bool:
    return output_format in {"pdf", "both"}


def should_generate_tex(output_format: str) -> bool:
    return output_format in {"tex", "both"}


def process_summary_job(job_id: str) -> None:
    """
    Process one queued summary job.

    This is intentionally simple and file-backed for a university microservice.
    For production, replace this with Celery/RQ + PostgreSQL/S3/MinIO.
    """
    try:
        job = update_job(job_id, status="processing", progress=5, step="Starting job", error=None)
        params = job.get("parameters", {})
        output_format = normalize_output_format(params.get("output_format"))

        update_job(job_id, progress=15, step="Extracting document text")
        documents: list[dict[str, Any]] = []
        texts: list[str] = []

        for file_record in job.get("files", []):
            file_path = Path(file_record["stored_path"])
            extracted = extract_text_from_pdf(file_path)
            if not extracted.text.strip():
                raise RuntimeError(
                    f"No readable text found in {file_record['original_filename']}. "
                    "This may be a scanned/image-based PDF. OCR is not enabled in this version."
                )

            doc = {
                "document_id": file_record.get("document_id"),
                "original_filename": file_record["original_filename"],
                "page_count": extracted.page_count,
                "text_length": len(extracted.text),
            }
            documents.append(doc)
            texts.append(extracted.text)

        update_job(
            job_id,
            progress=30,
            step="Text extracted",
            documents=documents,
            document_count=len(documents),
            total_page_count=sum(doc["page_count"] for doc in documents),
            total_text_length=sum(doc["text_length"] for doc in documents),
        )

        update_job(job_id, progress=45, step="Generating summary")
        combined_text = _combine_documents_for_summary(documents, texts)
        summary_result = summarize_text(
            combined_text,
            mode=params.get("mode"),
            language=params.get("language"),
            provider=params.get("provider"),
            style=params.get("style"),
        )
        summary = polish_summary_text(summary_result.summary)

        update_job(
            job_id,
            progress=75,
            step="Summary generated",
            provider=summary_result.provider,
            model=summary_result.model,
            summary_mode=summary_result.mode,
            sections_processed=summary_result.sections_processed,
            summary=summary,
        )

        filenames = [doc["original_filename"] for doc in documents]
        title = f"Summary of {filenames[0]}" if len(filenames) == 1 else f"Combined Summary of {len(filenames)} documents"
        source = _source_label(filenames)
        model_name = f"{summary_result.provider}:{summary_result.model}"

        pdf_url = None
        pdf_path = None
        tex_url = None
        tex_path = None

        if should_generate_pdf(output_format):
            update_job(job_id, progress=82, step="Generating PDF")
            pdf_file_path = settings.summary_dir / f"summary_{job_id}.pdf"
            generate_summary_pdf(
                title=title,
                summary_text=summary,
                output_path=pdf_file_path,
                source_filename=source,
                model_name=model_name,
            )
            pdf_path = str(pdf_file_path)
            pdf_url = f"/api/v1/jobs/{job_id}/pdf"

        if should_generate_tex(output_format):
            update_job(job_id, progress=90, step="Generating TeX")
            tex_file_path = settings.tex_dir / f"summary_{job_id}.tex"
            generate_summary_tex(
                title=title,
                summary_text=summary,
                output_path=tex_file_path,
                source_filename=source,
                model_name=model_name,
                language=params.get("language") or settings.SUMMARY_LANGUAGE,
            )
            tex_path = str(tex_file_path)
            tex_url = f"/api/v1/jobs/{job_id}/tex"

        if not settings.KEEP_UPLOADED_FILES:
            for file_record in job.get("files", []):
                Path(file_record["stored_path"]).unlink(missing_ok=True)

        update_job(
            job_id,
            status="completed",
            progress=100,
            step="Completed",
            pdf_generated=bool(pdf_url),
            pdf_download_url=pdf_url,
            pdf_path=pdf_path,
            tex_generated=bool(tex_url),
            tex_download_url=tex_url,
            tex_path=tex_path,
        )

    except Exception as exc:
        try:
            update_job(
                job_id,
                status="failed",
                progress=100,
                step="Failed",
                error=str(exc),
            )
        except Exception:
            pass


def delete_job(job_id: str) -> None:
    job = load_job(job_id)

    for field in ["pdf_path", "tex_path"]:
        value = job.get(field)
        if value:
            Path(value).unlink(missing_ok=True)

    shutil.rmtree(job_dir(job_id), ignore_errors=True)
