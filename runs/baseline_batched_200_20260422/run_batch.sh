#!/usr/bin/env bash
# Runs one enrichment batch with --offset/--limit, writing logs and a marker on success.
# Usage: ./run_batch.sh <offset> <limit> <batch_tag>
set -euo pipefail
cd "$(dirname "$0")/../.."
OFFSET="$1"
LIMIT="$2"
TAG="$3"
LOG="runs/batch_200/${TAG}.log"
OUT="runs/batch_200/${TAG}.json"
echo "[$(date -u +%H:%M:%SZ)] START batch ${TAG} offset=${OFFSET} limit=${LIMIT}" | tee -a "$LOG"
# Belt-and-suspenders: force JSONL tracing on, in case load_dotenv didn't apply it.
export ENRICHMENT_TRACE_JSONL=1
# Default ENRICHMENT_MAX_WORKERS=8; keep unless overridden.
python3 scripts/run_enrichment.py \
  --mode fixed \
  --merchant mocklaptops \
  --offset "$OFFSET" \
  --limit "$LIMIT" \
  --eval-output "$OUT" 2>&1 | tee -a "$LOG"
STATUS=${PIPESTATUS[0]}
if [[ "$STATUS" -eq 0 ]]; then
  echo "[$(date -u +%H:%M:%SZ)] DONE batch ${TAG} status=0" | tee -a "$LOG"
  touch "runs/batch_200/${TAG}.ok"
else
  echo "[$(date -u +%H:%M:%SZ)] FAIL batch ${TAG} status=${STATUS}" | tee -a "$LOG"
  exit "$STATUS"
fi
