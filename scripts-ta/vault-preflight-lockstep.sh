#!/usr/bin/env bash
# vault-preflight-lockstep.sh
# Keeps the vault-local copy of the vault-preflight skill in lockstep with its
# canonical git repo. ONE-WAY mirror: repo (source of truth) → vault copy.
# Best-effort `git pull --ff-only` first so GitHub edits also flow down.
# Registered in the user crontab; logs to ~/.hermes/logs/preflight-lockstep.log.
set -uo pipefail

REPO="$HOME/Desktop/vault-preflight"
DST="/Users/admin/Documents/Obsidian Vault/System/Assistant/skills/vault-preflight"
STAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# Repo missing → nothing to mirror from.
if [ ! -d "$REPO/.git" ]; then
  echo "$STAMP  SKIP: repo not found at $REPO"
  exit 0
fi

cd "$REPO" || { echo "$STAMP  SKIP: cannot cd $REPO"; exit 0; }

# Pull remote changes down if reachable (never merge/conflict; never block).
git pull --ff-only --quiet origin main >/dev/null 2>&1 && PULLED="pulled" || PULLED="pull-skipped"

mkdir -p "$DST/scripts" "$DST/references"

# Mirror repo working tree → vault copy.
# Use cp -R instead of rsync because macOS cron cannot access ~/Desktop/ via rsync
# (sandbox restriction). cp -R works because it's a local file copy.
# Exclude git metadata and the vault-authored _about.md.
CHANGES="$( (cd "$REPO" && find . -not -path './.git/*' -not -name '.git' -not -name '.gitignore' -not -name '.DS_Store' -not -name '_about.md' -type f -print0 | xargs -0 -I{} cp -f "$REPO/{}" "$DST/{}" 2>&1) || true)"

chmod +x "$DST/scripts/preflight-sync.sh" 2>/dev/null || true

if [ -n "$CHANGES" ]; then
  echo "$STAMP  SYNCED ($PULLED):"
  echo "$CHANGES" | sed 's/^/    /'
else
  echo "$STAMP  ok, in sync ($PULLED)"
fi
