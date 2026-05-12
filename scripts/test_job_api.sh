#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8001}"
MODE="${MODE:-study_pack}"
LANGUAGE="${LANGUAGE:-fr}"
PROVIDER="${PROVIDER:-auto}"
OUTPUT_FORMAT="${OUTPUT_FORMAT:-both}"
STYLE="${STYLE:-student_friendly}"
POLL_SECONDS="${POLL_SECONDS:-5}"
MAX_POLLS="${MAX_POLLS:-120}"
DOWNLOAD_RESULTS="${DOWNLOAD_RESULTS:-true}"
OUTPUT_DIR="${OUTPUT_DIR:-./job_outputs}"

if [ "$#" -lt 1 ]; then
  echo "Usage: scripts/test_job_api.sh file1.pdf [file2.pdf ...]"
  echo "Optional: PROVIDER=ollama|gemini|auto MODE=fast|study_pack|gemini_single|detailed LANGUAGE=fr|en OUTPUT_FORMAT=json|pdf|tex|both STYLE=student_friendly|academic|concise|detailed|exam_revision BASE_URL=http://127.0.0.1:8001 DOWNLOAD_RESULTS=true OUTPUT_DIR=./job_outputs"
  exit 1
fi

FORM_ARGS=()
for PDF_PATH in "$@"; do
  if [ ! -f "$PDF_PATH" ]; then
    echo "File not found: $PDF_PATH"
    exit 1
  fi
  FORM_ARGS+=( -F "files=@${PDF_PATH}" )
done

json_get() {
  local json="$1"
  local expr="$2"
  python - <<PY
import json
obj = json.loads('''$json''')
value = obj$expr
print(value if value is not None else "")
PY
}

download_if_available() {
  local label="$1"
  local url="$2"
  local output_path="$3"

  if [ -z "$url" ]; then
    return 0
  fi

  mkdir -p "$OUTPUT_DIR"
  echo "Downloading ${label}: ${url}"
  curl --fail --show-error --location --max-time 600 "$url" --output "$output_path"
  echo "Saved ${label}: ${output_path}"
}

echo "== Health check =="
curl --fail --show-error --max-time 10 "$BASE_URL/api/v1/health" | python -m json.tool

echo
echo "== Create job =="
JOB_RESPONSE=$(curl --fail --show-error --max-time 180 -X POST "$BASE_URL/api/v1/jobs" \
  "${FORM_ARGS[@]}" \
  -F "provider=${PROVIDER}" \
  -F "mode=${MODE}" \
  -F "language=${LANGUAGE}" \
  -F "output_format=${OUTPUT_FORMAT}" \
  -F "style=${STYLE}")

echo "$JOB_RESPONSE" | python -m json.tool
JOB_ID=$(json_get "$JOB_RESPONSE" "['job_id']")

echo
echo "== Poll job: $JOB_ID =="
for i in $(seq 1 "$MAX_POLLS"); do
  STATUS_RESPONSE=$(curl --fail --show-error --max-time 20 "$BASE_URL/api/v1/jobs/$JOB_ID")
  echo "$STATUS_RESPONSE" | python -m json.tool
  STATUS=$(json_get "$STATUS_RESPONSE" "['status']")

  if [ "$STATUS" = "completed" ]; then
    SUMMARY_URL="$BASE_URL/api/v1/jobs/$JOB_ID/summary"
    PDF_URL="$BASE_URL/api/v1/jobs/$JOB_ID/pdf"
    TEX_URL="$BASE_URL/api/v1/jobs/$JOB_ID/tex"

    echo "Job completed."
    echo "Summary: $SUMMARY_URL"
    echo "PDF:     $PDF_URL"
    echo "TeX:     $TEX_URL"

    if [ "$DOWNLOAD_RESULTS" = "true" ]; then
      mkdir -p "$OUTPUT_DIR"
      download_if_available "summary JSON" "$SUMMARY_URL" "$OUTPUT_DIR/${JOB_ID}_summary.json"

      if [[ "$OUTPUT_FORMAT" == "pdf" || "$OUTPUT_FORMAT" == "both" ]]; then
        download_if_available "PDF" "$PDF_URL" "$OUTPUT_DIR/${JOB_ID}_summary.pdf"
      fi

      if [[ "$OUTPUT_FORMAT" == "tex" || "$OUTPUT_FORMAT" == "both" ]]; then
        download_if_available "TeX" "$TEX_URL" "$OUTPUT_DIR/${JOB_ID}_summary.tex"
      fi

      echo
      echo "Downloaded files are in: $OUTPUT_DIR"
    fi

    exit 0
  fi

  if [ "$STATUS" = "failed" ]; then
    echo "Job failed."
    exit 1
  fi

  sleep "$POLL_SECONDS"
done

echo "Timed out waiting for job completion."
exit 1
