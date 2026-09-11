#!/usr/bin/env bash
set -euo pipefail

# ==============================================================================
# Autonomous Incremental Legislation Updater
# Can be run manually or via cron/PM2
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/update_$(date +'%Y%m%d_%H%M%S').log"

echo "========================================================" | tee -a "$LOG_FILE"
echo "Starting Romanian Legislation Ingestion: $(date -u)" | tee -a "$LOG_FILE"
echo "Working directory: $SCRIPT_DIR" | tee -a "$LOG_FILE"
echo "========================================================" | tee -a "$LOG_FILE"

# 1. Ensure Python Virtual Environment
if [ ! -d ".venv" ]; then
    echo "[!] Virtual environment .venv not found. Running uv sync..." | tee -a "$LOG_FILE"
    uv sync
fi

# 2. Run Incremental Extractor & DOM Parser
# Configurable via environment variables or default arguments
BASE_DELAY="${DELAY:-0.8}"
BATCH_SIZE="${BATCH_SIZE:-50}"
MAX_PAGES="${MAX_PAGES:-250}"
FROM_DATE="${FROM_DATE:-}"
TO_DATE="${TO_DATE:-}"
LIMIT="${LIMIT:-}"

CMD="uv run python -m etl.extract_legislatie --delay $BASE_DELAY --batch-size $BATCH_SIZE --max-pages $MAX_PAGES"

if [ -n "$FROM_DATE" ]; then
    CMD="$CMD --from-date $FROM_DATE"
fi
if [ -n "$TO_DATE" ]; then
    CMD="$CMD --to-date $TO_DATE"
fi
if [ -n "$LIMIT" ]; then
    CMD="$CMD --limit $LIMIT"
fi

echo "[*] Executing: $CMD" | tee -a "$LOG_FILE"
$CMD 2>&1 | tee -a "$LOG_FILE"

# 3. Reload PM2 API if active
if command -v pm2 &> /dev/null; then
    if pm2 list | grep -q "lex-api"; then
        echo "[*] Notifying PM2 lex-api of updated datasets..." | tee -a "$LOG_FILE"
        pm2 reload lex-api --update-env || true
    fi
fi

echo "========================================================" | tee -a "$LOG_FILE"
echo "Legislation Update Finished Successfully: $(date -u)" | tee -a "$LOG_FILE"
echo "========================================================" | tee -a "$LOG_FILE"
