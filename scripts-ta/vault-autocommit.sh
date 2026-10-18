#!/bin/bash
# Commit the Obsidian vault's authored layer on a timer.
#
# The vault has several concurrent writers (local Hermes agent, the VM's rsync,
# Claude Code sessions) and no conflict detection between them. On 2026-07-26 a
# remote sync replaced two index files with stale copies and the only reason it
# was noticed was a handoff note that happened to record the old page count.
# This job makes every such change a diffable, revertible commit instead.
#
# Deliberately does NOT push: the repo has no remote, and adding one would send
# vault contents off this machine.
#
# Installed as ~/Library/LaunchAgents/ai.hermes.vault-autocommit.plist (900s).

set -uo pipefail

VAULT="/Users/admin/Documents/Obsidian Vault"
GIT=/usr/bin/git
LOG="$HOME/.hermes/logs/vault-autocommit.log"
LOG_MAX=1048576   # 1 MB, then halve it — this runs 96x/day forever

# git calls getcwd() at startup even when given -C, and under cron/launchd this
# process inherits a cwd it may not be permitted to read ("fatal: Unable to read
# current working directory: Operation not permitted"). Move somewhere always
# readable first; -C then selects the repo.
cd /tmp 2>/dev/null || cd / || exit 0

log() { printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

# Keep the log bounded rather than letting a 96/day job grow it without limit.
if [ -f "$LOG" ] && [ "$(stat -f%z "$LOG")" -gt "$LOG_MAX" ]; then
    tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    log "log truncated to last 500 lines"
fi

# A missing or unmounted vault must not be interpreted as "everything deleted".
if [ ! -d "$VAULT/.git" ]; then
    log "SKIP: no git repo at $VAULT (unmounted or re-inited?)"
    exit 0
fi

# Every git call uses -C rather than cd: under launchd this process may start
# with an unreadable cwd (~/Documents is TCC-protected on macOS, and a
# WorkingDirectory inside it makes bash fail getcwd before the script even runs).
if ! GITDIR="$($GIT -C "$VAULT" rev-parse --absolute-git-dir 2>"$LOG.err")"; then
    log "SKIP: not a work tree at $VAULT — git said: $(tr '\n' ' ' < "$LOG.err")"
    rm -f "$LOG.err"
    exit 0
fi
rm -f "$LOG.err"
for state in MERGE_HEAD REBASE_HEAD CHERRY_PICK_HEAD BISECT_LOG; do
    if [ -e "$GITDIR/$state" ]; then
        log "SKIP: $state present — leaving the working tree alone"
        exit 0
    fi
done

# Another writer may hold the index lock mid-commit. Not an error; just try later.
if [ -e "$GITDIR/index.lock" ]; then
    log "SKIP: index.lock held by another process"
    exit 0
fi

if [ -z "$($GIT -C "$VAULT" status --porcelain)" ]; then
    exit 0   # clean tree: stay silent, or the log is 96 no-op lines a day
fi

$GIT -C "$VAULT" add -A || { log "ERROR: git add failed"; exit 0; }

if $GIT -C "$VAULT" diff --cached --quiet; then
    exit 0   # everything staged was ignored
fi

SUMMARY="$($GIT -C "$VAULT" diff --cached --shortstat | sed 's/^ *//')"
COUNT="$($GIT -C "$VAULT" diff --cached --name-only | wc -l | tr -d ' ')"

# Identify these commits distinctly from the ones agents and humans make here,
# without touching the repo's config for other committers.
export GIT_AUTHOR_NAME="vault-autocommit" \
       GIT_AUTHOR_EMAIL="vault-autocommit@localhost" \
       GIT_COMMITTER_NAME="vault-autocommit" \
       GIT_COMMITTER_EMAIL="vault-autocommit@localhost"

if $GIT -C "$VAULT" commit -q -m "vault-autocommit $(date '+%Y-%m-%d %H:%M') — ${COUNT} file(s)

${SUMMARY}"; then
    log "committed ${COUNT} file(s): ${SUMMARY}"
else
    log "ERROR: commit failed (${COUNT} file(s) staged)"
fi

exit 0
