from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


class Settings:
    APP_NAME: str = os.getenv("APP_NAME", "Education AI Summary Service")
    APP_VERSION: str = os.getenv("APP_VERSION", "2.2.0")

    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = _get_int("PORT", 8001)

    STORAGE_DIR: Path = Path(os.getenv("STORAGE_DIR", "app/storage"))
    KEEP_UPLOADED_FILES: bool = _get_bool("KEEP_UPLOADED_FILES", True)
    MAX_UPLOAD_SIZE_MB: int = _get_int("MAX_UPLOAD_SIZE_MB", 50)
    MAX_TOTAL_UPLOAD_SIZE_MB: int = _get_int("MAX_TOTAL_UPLOAD_SIZE_MB", 150)
    MAX_FILES_PER_REQUEST: int = _get_int("MAX_FILES_PER_REQUEST", 8)
    SUMMARY_LANGUAGE: str = os.getenv("SUMMARY_LANGUAGE", "fr")
    SUMMARY_STYLE: str = os.getenv("SUMMARY_STYLE", "student_friendly")
    DEFAULT_OUTPUT_FORMAT: str = os.getenv("DEFAULT_OUTPUT_FORMAT", "both")

    # TeX output
    # Safer default: preserve prose and add a curated formula section instead of guessing math inside sentences.
    TEX_INCLUDE_FORMULA_BOX: bool = _get_bool("TEX_INCLUDE_FORMULA_BOX", True)
    TEX_MAX_FORMULA_CARDS: int = _get_int("TEX_MAX_FORMULA_CARDS", 16)

    ALLOWED_ORIGINS_RAW: str = os.getenv("ALLOWED_ORIGINS", "*")

    # LLM provider
    # ollama = local model through Ollama
    # gemini = online Google Gemini API
    # auto = use Gemini if GEMINI_API_KEY is set, otherwise Ollama
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "auto")

    # Local Ollama provider
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
    OLLAMA_TIMEOUT_SECONDS: int = _get_int("OLLAMA_TIMEOUT_SECONDS", 300)
    OLLAMA_TEMPERATURE: float = _get_float("OLLAMA_TEMPERATURE", 0.2)
    OLLAMA_NUM_PREDICT: int = _get_int("OLLAMA_NUM_PREDICT", 1800)

    # Online Gemini provider
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_BASE_URL: str = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    GEMINI_TIMEOUT_SECONDS: int = _get_int("GEMINI_TIMEOUT_SECONDS", 300)
    GEMINI_TEMPERATURE: float = _get_float("GEMINI_TEMPERATURE", 0.2)
    GEMINI_MAX_OUTPUT_TOKENS: int = _get_int("GEMINI_MAX_OUTPUT_TOKENS", 9000)

    # Gemini rate-limit protection
    # Free-tier Gemini requests are usually limited by requests per minute.
    # For long documents, use a single Gemini request whenever possible instead of chapter-by-chapter calls.
    GEMINI_SINGLE_CALL_STUDY_PACK: bool = _get_bool("GEMINI_SINGLE_CALL_STUDY_PACK", True)
    GEMINI_STUDY_PACK_MAX_INPUT_CHARS: int = _get_int("GEMINI_STUDY_PACK_MAX_INPUT_CHARS", 90000)
    GEMINI_RETRY_ATTEMPTS: int = _get_int("GEMINI_RETRY_ATTEMPTS", 2)
    GEMINI_RETRY_DEFAULT_WAIT_SECONDS: int = _get_int("GEMINI_RETRY_DEFAULT_WAIT_SECONDS", 50)
    GEMINI_RETRY_MAX_WAIT_SECONDS: int = _get_int("GEMINI_RETRY_MAX_WAIT_SECONDS", 75)
    GEMINI_FALLBACK_TO_OLLAMA: bool = _get_bool("GEMINI_FALLBACK_TO_OLLAMA", True)

    MAX_CHARS_PER_CHUNK: int = _get_int("MAX_CHARS_PER_CHUNK", 9000)
    MAX_CHUNKS: int = _get_int("MAX_CHUNKS", 40)

    @property
    def upload_dir(self) -> Path:
        return self.STORAGE_DIR / "uploads"

    @property
    def summary_dir(self) -> Path:
        return self.STORAGE_DIR / "summaries"

    @property
    def tex_dir(self) -> Path:
        return self.STORAGE_DIR / "tex"

    @property
    def jobs_dir(self) -> Path:
        return self.STORAGE_DIR / "jobs"

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def max_total_upload_size_bytes(self) -> int:
        return self.MAX_TOTAL_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def allowed_origins(self) -> list[str]:
        raw = self.ALLOWED_ORIGINS_RAW.strip()
        if raw == "*":
            return ["*"]
        return [origin.strip() for origin in raw.split(",") if origin.strip()]


settings = Settings()
