from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "A local FastAPI microservice for extracting text from educational documents, "
            "summarizing them with Ollama or Gemini, and generating clean PDF or LaTeX summaries."
        ),
        version=settings.APP_VERSION,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=settings.allowed_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    settings.summary_dir.mkdir(parents=True, exist_ok=True)
    settings.tex_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)

    app.include_router(router, prefix="/api/v1", tags=["documents"])

    @app.get("/", tags=["root"])
    def root():
        return {
            "message": f"{settings.APP_NAME} is running",
            "version": settings.APP_VERSION,
            "docs": "/docs",
            "health": "/api/v1/health",
        }

    return app


app = create_app()
