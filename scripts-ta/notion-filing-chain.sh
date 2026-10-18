#!/bin/bash
# Notion -> Obsidian filing chain: stages 2 and 3.
#
# The pipeline is three scripts, but only stage 1 (notion-sync.py) was ever
# scheduled. Stages 2 and 3 were referenced by nothing — verified across the
# system crontab, every LaunchAgent, and all 24 Hermes job stores — so
# Inbox/Notion/ accumulated 336 files aged 7-30 days with nothing filing them.
#
# Runs under the Hermes gateway cron ticker (which has vault access), NOT
# launchd: a launchd-spawned process is TCC-blocked from ~/Documents and dies
# with PermissionError on os.listdir(vault). The gateway path writes to the
# vault daily, so it is the supported route here.
#
# Order matters: dedupe first (removes inbox copies that duplicate a filed
# note), then file (moves what is left into its proper home). Filing after
# dedupe means nothing gets re-filed as a duplicate.
#
# Both stages are idempotent and git-backed: each records an undo point, and
# the vault autocommit captures the result as a revertible commit.

set -uo pipefail

PY=/usr/bin/python3
SCRIPTS="/Users/admin/.hermes/scripts"
VAULT="/Users/admin/Documents/Obsidian Vault"

# A missing or unmounted vault must never read as "everything gone".
if [ ! -d "$VAULT/Inbox/Notion" ]; then
    echo "SKIP: $VAULT/Inbox/Notion not present (vault unmounted?)"
    exit 0
fi

echo "=== Notion filing chain — $(date '+%Y-%m-%d %H:%M') ==="

echo
echo "--- stage 2: dedupe ---"
"$PY" "$SCRIPTS/vault-notion-dedupe.py" --apply
d=$?

echo
echo "--- stage 3: file ---"
"$PY" "$SCRIPTS/vault-notion-file.py" --apply
f=$?

echo
echo "=== chain done (dedupe rc=$d, file rc=$f) ==="
[ "$d" -eq 0 ] && [ "$f" -eq 0 ] || exit 1
