#!/bin/bash
# Cron wrapper for the RAG ingest harness + local hermes_rag health monitor.
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

  # Phase 10: route outputs to delivery channels (Lark full report + video, Telegram links-only)
  # Only route if the harness reported ok — skip on failures so we don't spam bad data.
  if [ "$status" = "ok" ]; then
    /usr/bin/python3 "$HOME/.hermes/scripts/rag_delivery_router.py" --health "$HEALTH" >> "$LOG" 2>&1
    delivery_rc=$?
    echo "=== delivery-router exit=$delivery_rc ===" >> "$LOG"
  else
    echo "=== delivery-router skipped (harness status=$status) ===" >> "$LOG"
  fi
fi

# ---------------------------------------------------------------------------
# hermes_rag health monitor (Item 2 local-infrastructure watchdog).
# Runs after the ingest harness, under the same rag-env python that has turbovec.
# Independent exit code: a failed harness does NOT suppress a failed health check.
# Writes a one-line status to the same status log plus a JSON blob for dashboards.
# ---------------------------------------------------------------------------
HEALTH_PY="$HOME/hermes_rag/health_check.py"
HEALTH_JSON="$HOME/.hermes/logs/hermes-rag-health.json"
HEALTH_STATUS_LOG="$HOME/.hermes/logs/hermes-rag-health-status.log"

mkdir -p "$HOME/.hermes/logs"
{
  echo "=== $(date '+%F %T') hermes-rag-health start ==="
  "$ENV_PY" "$HEALTH_PY" 2>&1
  rc=$?
  echo "=== hermes-rag-health exit=$rc ==="
} >> "$LOG" 2>&1

# capture the one-line stdout summary into the JSON blob (success or failure)
if [ -f "$HEALTH_PY" ]; then
  # re-run once more to produce a clean JSON blob — the monitor is cheap and idempotent.
  # On failure the script still writes its failure summary to stderr; capture both.
  "$ENV_PY" "$HEALTH_PY" > "$HOME/.hermes/logs/hermes-rag-health-lastrun.txt" 2>"$HOME/.hermes/logs/hermes-rag-health-lastrun-err.txt"
  rc2=$?
  /usr/bin/python3 - "$HEALTH_JSON" "$HOME/.hermes/logs/hermes-rag-health-lastrun.txt" "$HOME/.hermes/logs/hermes-rag-health-lastrun-err.txt" "$rc2" << 'PYEOF' 2>/dev/null || true
import json, sys, time
from pathlib import Path

out_path = Path(sys.argv[1])
ok_line = Path(sys.argv[2]).read_text(encoding="utf-8", errors="replace").strip()
err_lines = Path(sys.argv[3]).read_text(encoding="utf-8", errors="replace").strip()
exit_code = int(sys.argv[4])

blob = {
    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "exit_code": exit_code,
    "exit_text": "ok" if exit_code == 0 else "fail",
    "summary": ok_line or "(no stdout — see err)",
    "errors": [l for l in err_lines.splitlines() if l.strip()] if err_lines else [],
}
out_path.write_text(json.dumps(blob, indent=2) + "\n", encoding="utf-8")

# one-line status log (machine-parseable, same shape as the harness status log)
status_line = f"{time.strftime('%F %T')} hermes-rag-health={blob['exit_text']}"
Path(sys.argv[1].parent / "hermes-rag-health-status.log").write_text(
    status_line + "\n", encoding="utf-8", append=True
) if False else None
with open(Path(sys.argv[1]).parent / "hermes-rag-health-status.log", "a", encoding="utf-8") as f:
    f.write(status_line + "\n")
PYEOF

  echo "$(date '+%F %T') hermes-rag-health-status=$(/usr/bin/python3 -c "import json;print(json.load(open('$HEALTH_JSON')).get('exit_text','?'))" 2>/dev/null || echo '?')" >> "$HOME/.hermes/logs/hermes-rag-health-status.log"
fi