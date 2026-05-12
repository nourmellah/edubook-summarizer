"""
Small example showing how another backend can call the summary service.

Single PDF:
python examples/client_example.py course.pdf

Multiple PDFs:
python examples/client_example.py course1.pdf course2.pdf td.pdf

Optional environment variables:
PROVIDER=ollama|gemini|auto
MODE=study_pack|fast|gemini_single|detailed
LANGUAGE=fr|en
GENERATE_PDF=true|false
GENERATE_TEX=true|false
SUMMARY_SERVICE_URL=http://localhost:8001
"""

from pathlib import Path
import os
import sys
import requests


BASE_URL = os.getenv("SUMMARY_SERVICE_URL", "http://localhost:8001")
PROVIDER = os.getenv("PROVIDER", "auto")
MODE = os.getenv("MODE", "study_pack")
LANGUAGE = os.getenv("LANGUAGE", "fr")
GENERATE_PDF = os.getenv("GENERATE_PDF", "true")
GENERATE_TEX = os.getenv("GENERATE_TEX", "false")


def _query_url(endpoint: str) -> str:
    return (
        f"{BASE_URL}/api/v1/{endpoint}"
        f"?generate_pdf={GENERATE_PDF}&generate_tex={GENERATE_TEX}&provider={PROVIDER}&mode={MODE}&language={LANGUAGE}"
    )


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python examples/client_example.py file1.pdf [file2.pdf ...]")
        raise SystemExit(1)

    pdf_paths = [Path(arg) for arg in sys.argv[1:]]

    for pdf_path in pdf_paths:
        if not pdf_path.exists():
            print(f"File not found: {pdf_path}")
            raise SystemExit(1)

    if len(pdf_paths) == 1:
        pdf_path = pdf_paths[0]
        with pdf_path.open("rb") as pdf_file:
            response = requests.post(
                _query_url("summarize"),
                files={"file": (pdf_path.name, pdf_file, "application/pdf")},
                timeout=1200,
            )
    else:
        opened_files = []
        try:
            files = []
            for pdf_path in pdf_paths:
                file_obj = pdf_path.open("rb")
                opened_files.append(file_obj)
                files.append(("files", (pdf_path.name, file_obj, "application/pdf")))

            response = requests.post(
                _query_url("summarize-multiple"),
                files=files,
                timeout=1200,
            )
        finally:
            for file_obj in opened_files:
                file_obj.close()

    response.raise_for_status()
    data = response.json()

    if "documents" in data:
        print("Documents:", ", ".join(doc["original_filename"] for doc in data["documents"]))
        print("Total pages:", data["total_page_count"])
    else:
        print("Document:", data["original_filename"])
        print("Pages:", data["page_count"])

    print("Provider:", data["provider"])
    print("Model:", data["model"])
    print("PDF generated:", data["pdf_generated"])
    print("PDF URL:", data["pdf_download_url"])
    print("TeX generated:", data.get("tex_generated"))
    print("TeX URL:", data.get("tex_download_url"))
    print("\nSummary:\n")
    print(data["summary"])


if __name__ == "__main__":
    main()
