#!/bin/bash
# intelligence-prune.sh — weekly prune runner (APPLY mode).
#
# Why this wrapper: Hermes no-agent cron jobs execute the script as
# [python, script] with NO extra CLI arguments (cron/scheduler_script.py),
# so a `--apply` flag cannot be passed through the job registry. This wrapper
# pins the flag at the shell level. Manual rehearsals still use:
#   python3 intelligence-prune.py          (dry-run)
#   python3 intelligence-prune.py --apply  (explicit apply)
#
# Registered as: [bot:vaultkeeper] intelligence-prune, Sundays 07:30.
exec /usr/bin/python3 "$HOME/.hermes/profiles/transformation-architect/scripts/intelligence-prune.py" --apply
