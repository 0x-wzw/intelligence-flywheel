#!/usr/bin/env bash
# Weekly AISED vault cleanup, driven by headless Claude Code on the Haiku model.
# Registered as a Hermes cron job (no_agent:true) → this script's stdout is the
# Telegram report. The deterministic mutations live in the .py engine; Haiku only
# runs it, writes the daily-note line, and summarizes.
set -euo pipefail

VAULT="/Users/admin/Documents/Obsidian Vault"
CLAUDE="/Users/admin/.local/bin/claude"
ENGINE="$HOME/.hermes/scripts/vault-aised-cleanup.py"

cd "$VAULT"

read -r -d '' PROMPT <<'EOF' || true
You are running an unattended weekly vault-maintenance job. Do exactly this:

1. Run: python3 ~/.hermes/scripts/vault-aised-cleanup.py --apply --json
2. Parse the JSON it prints (fields: counts.deduped, counts.tagged, counts.moved,
   counts.anomalies, and an "anomalies" list of strings).
3. Append ONE line to today's daily note. The file is Daily/<TODAY>.md where <TODAY>
   is today's date in YYYY-MM-DD (get it with: date +%F). Insert the line directly
   under the "## Log" header, in this exact format:
   - H:MM AM/PM — [AISED-CLEANUP] deduped N, tagged N, moved N, anomalies N
   (use the current local time from: date "+%-I:%M %p")
4. Print a short plain-text report to stdout for Telegram: the four counts on one
   line, then — only if anomalies > 0 — each anomaly string on its own line prefixed
   with "⚠ ". Keep it under 6 lines. Do not print anything else.
EOF

exec "$CLAUDE" -p "$PROMPT" \
  --model haiku \
  --allowedTools 'Bash,Read,Edit' \
  --permission-mode acceptEdits \
  --max-turns 12
