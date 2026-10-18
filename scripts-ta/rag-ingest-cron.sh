#!/bin/bash
# Cron wrapper for the RAG ingest harness.
# Delegates to ~/rag-env (the only env with pypdf + hermes_rag + hermes_feeds importable),
# writes a health JSON every run, and appends a one-line log.
set -u
ENV_PY="$HOME/rag-env/bin/python"
HARNESS="$HOME/.hermes/scripts/rag_ingest_harness.py"
LOG="$HOME/.hermes/logs/rag-ingest-cron.log"
HEALTH="$HOME/.hermes/logs/rag-ingest-health.json"

mkdir -p "$HOME/.hermes/logs"
{
  echo "=== $(date '+%F %T') rag-ingest-cron start ==="
  "$ENV_PY" "$HARNESS" 2>&1 | grep -v "Ignoring wrong pointing"
  rc=$?
  echo "=== rag-ingest-cron exit=$rc ==="
} >> "$LOG" 2>&1

# one-line health pointer for flywheel checks
if [ -f "$HEALTH" ]; then
  status=$(/usr/bin/python3 -c "import json;print(json.load(open('$HEALTH')).get('status','?'))" 2>/dev/null || echo '?')
  echo "$(date '+%F %T') last-harness-status=$status" >> "$HOME/.hermes/logs/rag-ingest-status.log"
fi