# Education AI Summary Service

Standalone FastAPI microservice for an Education AI Center / NotebookLM-like university project.

It can:

- accept one PDF or multiple PDF files in one request
- extract selectable text from PDFs using PyMuPDF
- summarize educational documents with local Ollama or online Gemini
- generate clean PDF summary documents using ReportLab
- generate downloadable LaTeX `.tex` source files for manual compilation
- return JSON summaries, downloadable PDF files, or downloadable TeX files
- run as a separate microservice that another backend can call over HTTP

---

## Architecture

```text
Frontend or Main Backend
        |
        | HTTP multipart upload: one PDF or many PDFs
        v
FastAPI Summary Service
        |
        | PDF text extraction
        v
PyMuPDF
        |
        | clean/compact educational text
        v
LLM Provider
   ├── Ollama local model
   └── Gemini online API
        |
        | educational summary / study pack
        v
JSON response, ReportLab PDF summary, and/or LaTeX .tex source
```

Recommended integration:

```text
Frontend -> Main Backend -> Summary Service -> Ollama/Gemini
```

---

## Requirements

- Python 3.10+
- Miniconda recommended
- For local mode: Ollama installed locally and a pulled model such as `llama3.2:3b`
- For online mode: a Gemini API key from Google AI Studio

---

## Local installation with Miniconda

```bash
cd education-ai-summary-service
conda create -n edu-ai-summary python=3.11 -y
conda activate edu-ai-summary
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
```

Run the service:

```bash
python -m uvicorn app.main:app --reload --port 8001
```

Open:

```text
http://127.0.0.1:8001/docs
```

---

## Provider options

```text
ollama   fully local model through Ollama
local    alias for ollama
gemini   online Google Gemini API
online   alias for gemini
auto     use Gemini if GEMINI_API_KEY exists, otherwise Ollama
```

Recommended default in `.env`:

```env
LLM_PROVIDER="auto"
```

This means:

```text
If GEMINI_API_KEY exists -> use Gemini
Else -> use Ollama
```

---

## Option A: Use local Ollama

Install Ollama and pull a model:

```bash
ollama pull llama3.2:3b
ollama serve
```

In `.env`:

```env
LLM_PROVIDER="ollama"
OLLAMA_BASE_URL="http://localhost:11434"
OLLAMA_MODEL="llama3.2:3b"
```

Test:

```bash
PROVIDER=ollama MODE=study_pack LANGUAGE=fr scripts/test_api.sh /path/to/course.pdf
```

---

## Option B: Use Gemini with rate-limit protection

Create a Gemini API key in Google AI Studio, then edit `.env`:

```env
LLM_PROVIDER="gemini"
GEMINI_API_KEY="paste-your-api-key-here"
GEMINI_MODEL="gemini-2.5-flash-lite"
GEMINI_MAX_OUTPUT_TOKENS="9000"
GEMINI_TIMEOUT_SECONDS="600"
```

Recommended rate-limit settings:

```env
GEMINI_SINGLE_CALL_STUDY_PACK="true"
GEMINI_STUDY_PACK_MAX_INPUT_CHARS="90000"
GEMINI_RETRY_ATTEMPTS="2"
GEMINI_RETRY_DEFAULT_WAIT_SECONDS="50"
GEMINI_RETRY_MAX_WAIT_SECONDS="75"
GEMINI_FALLBACK_TO_OLLAMA="true"
```

Why this matters:

```text
Old behavior:
long PDF -> one request per chapter -> easy to hit free-tier RPM limits

Current behavior:
long PDF -> one rich Gemini request -> safer for rate limits
```

Do not commit your `.env` file to Git because it can contain your Gemini API key.

---

## Summary modes

```text
study_pack      default educational summary. With Gemini, this uses one request.
gemini_single   force one-call study-pack generation.
rate_safe       alias for gemini_single.
fast            quicker preview, less detailed.
detailed        slower chunk-based map-reduce mode.
```

Recommended for your demo:

```bash
PROVIDER=gemini MODE=study_pack LANGUAGE=fr scripts/test_api.sh /path/to/course.pdf
```

Recommended fully local test:

```bash
PROVIDER=ollama MODE=study_pack LANGUAGE=fr scripts/test_api.sh /path/to/course.pdf
```

---

## API endpoints

```text
GET  /api/v1/health
GET  /api/v1/providers

POST /api/v1/extract
POST /api/v1/extract-multiple

POST /api/v1/jobs
GET  /api/v1/jobs/{job_id}
GET  /api/v1/jobs/{job_id}/summary
GET  /api/v1/jobs/{job_id}/pdf
GET  /api/v1/jobs/{job_id}/tex
DELETE /api/v1/jobs/{job_id}

POST /api/v1/summarize
POST /api/v1/summarize-multiple

POST /api/v1/generate-summary-pdf
POST /api/v1/generate-summary-pdf-multiple

POST /api/v1/generate-summary-tex
POST /api/v1/generate-summary-tex-multiple

GET  /api/v1/summaries/{filename}
GET  /api/v1/tex/{filename}
```

---

## Single PDF examples

Summarize one PDF with Gemini and generate a downloadable PDF URL:

```bash
curl -X POST "http://127.0.0.1:8001/api/v1/summarize?generate_pdf=true&generate_tex=true&provider=gemini&mode=study_pack&language=fr" \
  -F "file=@/path/to/course.pdf"
```

Directly download the generated summary PDF:

```bash
curl -X POST "http://127.0.0.1:8001/api/v1/generate-summary-pdf?provider=gemini&mode=study_pack&language=fr" \
  -F "file=@/path/to/course.pdf" \
  --output summary.pdf
```

Test script:

```bash
PROVIDER=gemini MODE=study_pack LANGUAGE=fr scripts/test_api.sh /path/to/course.pdf
```

---

## Multiple PDF examples

Use this when a student uploads several files for the same course:

```text
course_chapter_1.pdf
course_chapter_2.pdf
td_exercises.pdf
exam_correction.pdf
```

Summarize multiple PDFs and generate one combined PDF URL:

```bash
curl -X POST "http://127.0.0.1:8001/api/v1/summarize-multiple?generate_pdf=true&generate_tex=true&provider=gemini&mode=study_pack&language=fr" \
  -F "files=@/path/to/course_chapter_1.pdf" \
  -F "files=@/path/to/course_chapter_2.pdf" \
  -F "files=@/path/to/td_exercises.pdf"
```

Directly download one combined summary PDF:

```bash
curl -X POST "http://127.0.0.1:8001/api/v1/generate-summary-pdf-multiple?provider=gemini&mode=study_pack&language=fr" \
  -F "files=@/path/to/course_chapter_1.pdf" \
  -F "files=@/path/to/course_chapter_2.pdf" \
  --output combined-summary.pdf
```

Test script:

```bash
PROVIDER=gemini MODE=study_pack LANGUAGE=fr scripts/test_multiple_api.sh file1.pdf file2.pdf file3.pdf
```

---

## Multi-PDF limits

Configured in `.env`:

```env
MAX_UPLOAD_SIZE_MB="50"
MAX_TOTAL_UPLOAD_SIZE_MB="150"
MAX_FILES_PER_REQUEST="8"
```

Meaning:

```text
Each PDF can be up to 50 MB.
All PDFs together can be up to 150 MB.
A single request can include up to 8 PDFs.
```

---

## Recommended NotebookLM-style integration: async jobs

For the bigger Education AI Center app, use the job endpoint instead of making the browser wait on a long `/summarize-multiple` request.

Flow:

```text
Frontend uploads files + options
        ↓
Main backend calls POST /api/v1/jobs
        ↓
Summary service returns job_id immediately
        ↓
Frontend polls GET /api/v1/jobs/{job_id}
        ↓
When completed, frontend reads summary or downloads PDF/TeX
```

Create a job:

```bash
curl -X POST "http://127.0.0.1:8001/api/v1/jobs" \
  -F "files=@course1.pdf" \
  -F "files=@course2.pdf" \
  -F "provider=gemini" \
  -F "mode=study_pack" \
  -F "language=fr" \
  -F "output_format=both" \
  -F "style=student_friendly"
```

Poll status:

```bash
curl "http://127.0.0.1:8001/api/v1/jobs/job_xxx"
```

Get final summary JSON:

```bash
curl "http://127.0.0.1:8001/api/v1/jobs/job_xxx/summary"
```

Download outputs:

```bash
curl "http://127.0.0.1:8001/api/v1/jobs/job_xxx/pdf" --output summary.pdf
curl "http://127.0.0.1:8001/api/v1/jobs/job_xxx/tex" --output summary.tex
```

Test script:

```bash
PROVIDER=gemini MODE=study_pack LANGUAGE=fr OUTPUT_FORMAT=both STYLE=student_friendly scripts/test_job_api.sh course1.pdf course2.pdf
```

Supported job parameters:

```text
language:      fr, en, auto
mode:          fast, study_pack, gemini_single, detailed
provider:      auto, gemini, ollama
output_format: json, pdf, tex, both
style:         student_friendly, academic, concise, detailed, exam_revision, cheatsheet
```

For a real deployment, this simple file-backed job system can later be replaced by Celery/RQ + Redis/PostgreSQL + S3/MinIO without changing the external API design.

---

## Integration with the rest of the project

This service should run as its own server/microservice.

Example flow:

```text
Main backend uploads PDF(s) to Summary Service
Summary Service extracts and summarizes the PDF(s)
Summary Service returns a job_id, then JSON and optional PDF/TeX URLs after processing
Main backend stores the result in its own database
Frontend displays the summary and download links
```

Python example for multiple PDFs:

```python
import requests

files = [
    ("files", open("course_part_1.pdf", "rb")),
    ("files", open("course_part_2.pdf", "rb")),
]

try:
    response = requests.post(
        "http://127.0.0.1:8001/api/v1/summarize-multiple?generate_pdf=true&generate_tex=true&provider=gemini&mode=study_pack&language=fr",
        files=files,
        timeout=1200,
    )
    data = response.json()
    print(data["summary"])
    print(data["pdf_download_url"])
    print(data.get("tex_download_url"))
finally:
    for _, file_obj in files:
        file_obj.close()
```

---

## Limitations

- This version supports text-based PDFs.
- Scanned PDFs need OCR, which is not enabled yet.
- Gemini sends extracted document text to Google, so use Ollama for private/offline documents.
- Local Ollama quality depends heavily on the chosen local model and your machine resources.

---

## LaTeX `.tex` output

This version can generate a standalone LaTeX source file instead of, or in addition to, the ReportLab PDF.

Use this for math-heavy courses such as statistics, time series, ARMA, regression, algorithms, and courses with many formulas. The service does **not** compile LaTeX. It only returns a `.tex` file that you can upload to Overleaf or compile locally.

Recommended compiler:

```text
XeLaTeX or LuaLaTeX
```

Why XeLaTeX/LuaLaTeX: the generated summaries can contain French accents and Unicode mathematical symbols such as `ρ`, `γ`, `ε`, `Σ`, `→`, `∪`, and `∞`.

Generate JSON + PDF + TeX URL for one PDF:

```bash
curl -X POST "http://127.0.0.1:8001/api/v1/summarize?generate_pdf=true&generate_tex=true&provider=gemini&mode=study_pack&language=fr" \
  -F "file=@/path/to/course.pdf"
```

Directly download one `.tex` file:

```bash
curl -X POST "http://127.0.0.1:8001/api/v1/generate-summary-tex?provider=gemini&mode=study_pack&language=fr" \
  -F "file=@/path/to/course.pdf" \
  --output summary.tex
```

Directly download one combined `.tex` file for multiple PDFs:

```bash
curl -X POST "http://127.0.0.1:8001/api/v1/generate-summary-tex-multiple?provider=gemini&mode=study_pack&language=fr" \
  -F "files=@course1.pdf" \
  -F "files=@course2.pdf" \
  --output combined_summary.tex
```

Test scripts:

```bash
PROVIDER=gemini MODE=study_pack LANGUAGE=fr GENERATE_TEX=true scripts/test_api.sh /path/to/course.pdf
PROVIDER=gemini MODE=study_pack LANGUAGE=fr GENERATE_TEX=true scripts/test_multiple_api.sh course1.pdf course2.pdf
```

Compile manually:

```bash
xelatex summary.tex
xelatex summary.tex
```

If you use Overleaf, upload the `.tex` file and set the compiler to **XeLaTeX**.
