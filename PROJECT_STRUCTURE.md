# Project Structure

```text
education-ai-summary-service/
├── app/
│   ├── main.py
│   ├── api/
│   │   └── routes.py
│   ├── core/
│   │   └── config.py
│   ├── models/
│   │   └── schemas.py
│   ├── services/
│   │   ├── file_utils.py
│   │   ├── job_service.py
│   │   ├── pdf_extractor.py
│   │   ├── pdf_generator.py
│   │   ├── tex_generator.py
│   │   ├── summarizer.py
│   │   └── text_polish.py
│   └── storage/
│       ├── uploads/
│       ├── summaries/
│       ├── tex/
│       └── jobs/
├── examples/
│   └── client_example.py
├── scripts/
│   ├── test_api.sh
│   ├── test_job_api.sh
│   └── test_multiple_api.sh
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── run.py
└── README.md
```

## Important files

- `app/api/routes.py`: FastAPI endpoints for direct workflows, job-based workflows, PDF download, and TeX download.
- `app/services/pdf_extractor.py`: Extracts selectable text with PyMuPDF.
- `app/services/job_service.py`: Stores file-backed async jobs, tracks progress, and generates outputs.
- `app/services/summarizer.py`: Calls Ollama or Gemini and builds study-pack prompts.
- `app/services/text_polish.py`: Removes chatty model output and visible markdown noise.
- `app/services/pdf_generator.py`: Generates cleaner ReportLab PDF summaries.
- `app/services/tex_generator.py`: Generates standalone LaTeX `.tex` summaries for manual compilation.
- `scripts/test_api.sh`: Tests one PDF, optionally generating PDF and/or TeX.
- `scripts/test_multiple_api.sh`: Tests multiple PDFs, optionally generating PDF and/or TeX.
- `scripts/test_job_api.sh`: Tests the recommended async job workflow.
```
