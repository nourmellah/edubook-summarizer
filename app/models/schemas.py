from pydantic import BaseModel, Field
from typing import Optional


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str
    version: str
    default_provider: str
    default_model: str


class ProviderResponse(BaseModel):
    default_provider: str
    default_model: str
    available_providers: list[str]
    ollama_model: str
    ollama_base_url: str
    gemini_model: str
    gemini_configured: bool
    gemini_single_call_study_pack: bool
    gemini_retry_attempts: int
    gemini_fallback_to_ollama: bool


class ExtractResponse(BaseModel):
    document_id: str
    original_filename: str
    page_count: int
    text_length: int
    preview: str


class DocumentInfo(BaseModel):
    document_id: str
    original_filename: str
    page_count: int
    text_length: int


class MultiExtractResponse(BaseModel):
    collection_id: str
    document_count: int
    total_page_count: int
    total_text_length: int
    documents: list[DocumentInfo]
    preview: str


class SummaryResponse(BaseModel):
    document_id: str
    original_filename: str
    page_count: int
    text_length: int
    provider: str
    model: str
    summary_mode: str
    sections_processed: int
    summary: str
    pdf_generated: bool = False
    pdf_download_url: Optional[str] = None
    tex_generated: bool = False
    tex_download_url: Optional[str] = None


class MultiSummaryResponse(BaseModel):
    collection_id: str
    document_count: int
    total_page_count: int
    total_text_length: int
    documents: list[DocumentInfo]
    provider: str
    model: str
    summary_mode: str
    sections_processed: int
    summary: str
    pdf_generated: bool = False
    pdf_download_url: Optional[str] = None
    tex_generated: bool = False
    tex_download_url: Optional[str] = None


class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Human-readable error message")


class JobCreateResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    step: str
    status_url: str
    summary_url: Optional[str] = None
    pdf_url: Optional[str] = None
    tex_url: Optional[str] = None
    parameters: dict = Field(default_factory=dict)


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: int
    step: str
    created_at: str
    updated_at: str
    parameters: dict = Field(default_factory=dict)
    documents: list[dict] = Field(default_factory=list)
    document_count: int = 0
    total_page_count: int = 0
    total_text_length: int = 0
    provider: Optional[str] = None
    model: Optional[str] = None
    summary_mode: Optional[str] = None
    sections_processed: Optional[int] = None
    summary: Optional[str] = None
    pdf_generated: bool = False
    pdf_download_url: Optional[str] = None
    tex_generated: bool = False
    tex_download_url: Optional[str] = None
    error: Optional[str] = None
