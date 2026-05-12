#!/usr/bin/env bash
set -euo pipefail

PDF_PATH="${1:-}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8001}"
MODE="${MODE:-study_pack}"
LANGUAGE="${LANGUAGE:-fr}"
PROVIDER="${PROVIDER:-auto}"
GENERATE_PDF="${GENERATE_PDF:-true}"
GENERATE_TEX="${GENERATE_TEX:-false}"
OUTPUT_FORMAT="${OUTPUT_FORMAT:-}"
STYLE="${STYLE:-student_friendly}"
CURL_MAX_TIME="${CURL_MAX_TIME:-900}"
DOWNLOAD_RESULTS="${DOWNLOAD_RESULTS:-true}"
OUTPUT_DIR="${OUTPUT_DIR:-./job_outputs}"

if [ -z "$PDF_PATH" ]; then
  echo "Usage: scripts/test_api.sh /path/to/file.pdf"
  echo "Optional: PROVIDER=ollama|gemini|auto MODE=fast|study_pack|gemini_single|detailed LANGUAGE=fr|en OUTPUT_FORMAT=json|pdf|tex|both STYLE=student_friendly|academic GENERATE_PDF=true|false GENERATE_TEX=true|false BASE_URL=http://127.0.0.1:8001 DOWNLOAD_RESULTS=true OUTPUT_DIR=./job_outputs"
  exit 1
fi

if [ ! -f "$PDF_PATH" ]; then
  echo "File not found: $PDF_PATH"
  exit 1
fi

json_get_optional() {
  local json="$1"
  local key="$2"
  python - <<PY
import json
obj = json.loads('''$json''')
print(obj.get('$key') or '')
PY
}

download_result() {
  local label="$1"
  local rel_url="$2"
  local output_path="$3"
  if [ -z "$rel_url" ]; then
    return 0
  fi
  mkdir -p "$OUTPUT_DIR"
  echo "Downloading ${label}: ${BASE_URL}${rel_url}"
  curl --fail --show-error --location --max-time 600 "${BASE_URL}${rel_url}" --output "$output_path"
  echo "Saved ${label}: ${output_path}"
}

echo "== Health check =="
curl --fail --show-error --max-time 10 "$BASE_URL/api/v1/health" | python -m json.tool

echo
echo "== Providers =="
curl --fail --show-error --max-time 10 "$BASE_URL/api/v1/providers" | python -m json.tool

echo
echo "== Extract text preview =="
curl --fail --show-error --max-time 120 -X POST "$BASE_URL/api/v1/extract" \
  -F "file=@${PDF_PATH}" | python -m json.tool

echo
echo "== Summarize and generate output URLs =="
echo "Provider: $PROVIDER | Mode: $MODE | Language: $LANGUAGE | Output format: ${OUTPUT_FORMAT:-flags} | PDF: $GENERATE_PDF | TeX: $GENERATE_TEX | Style: $STYLE"
QUERY="generate_pdf=${GENERATE_PDF}&generate_tex=${GENERATE_TEX}&provider=${PROVIDER}&mode=${MODE}&language=${LANGUAGE}&style=${STYLE}"
if [ -n "$OUTPUT_FORMAT" ]; then
  QUERY="${QUERY}&output_format=${OUTPUT_FORMAT}"
fi
SUMMARY_RESPONSE=$(curl --show-error --max-time "$CURL_MAX_TIME" -X POST "$BASE_URL/api/v1/summarize?${QUERY}" \
  -F "file=@${PDF_PATH}")

echo "$SUMMARY_RESPONSE" | python -m json.tool

if [ "$DOWNLOAD_RESULTS" = "true" ]; then
  DOCUMENT_ID=$(json_get_optional "$SUMMARY_RESPONSE" "document_id")
  PDF_URL=$(json_get_optional "$SUMMARY_RESPONSE" "pdf_download_url")
  TEX_URL=$(json_get_optional "$SUMMARY_RESPONSE" "tex_download_url")
  BASE_NAME="${DOCUMENT_ID:-summary}"
  download_result "PDF" "$PDF_URL" "$OUTPUT_DIR/${BASE_NAME}.pdf"
  download_result "TeX" "$TEX_URL" "$OUTPUT_DIR/${BASE_NAME}.tex"
  if [ -n "$PDF_URL" ] || [ -n "$TEX_URL" ]; then
    echo "Downloaded files are in: $OUTPUT_DIR"
  fi
fi
