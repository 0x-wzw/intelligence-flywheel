#!/usr/bin/env python3
"""Wrapper for vault-semantic-index cron job — calls vault_search_auto.py index.

Uses the system Python (not the Hermes venv) because vault_search_auto.py
depends on numpy and sentence-transformers which are installed in the system
Python, not the Hermes venv. The Hermes cron runner executes this wrapper with
sys.executable (Hermes venv Python), but the actual indexing work is delegated
to the system Python which has the required ML dependencies.

Writes System/vault-semantic-index-health.json on every run — success or
failure — so flywheel-health-check.py can see this job. Without it, two
consecutive silent failures (bad args, then a missing numpy) went unnoticed
because nothing was watching the indexer that all vault retrieval depends on.
"""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT = Path(__file__).parent / "vault_search_auto.py"
HEALTH_FILE = (
    Path.home()
    / "Documents/Obsidian Vault/System/vault-semantic-index-health.json"
)

# System Python has numpy + sentence-transformers pre-installed.
# The Hermes venv (Python 3.11) does not have these ML packages.
SYSTEM_PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3"


def write_health(**fields) -> None:
    """Persist the health record, never letting a write error mask the real result."""
    payload = {"timestamp": datetime.now().isoformat(), **fields}
    try:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        HEALTH_FILE.write_text(json.dumps(payload, indent=2))
    except OSError as e:
        print(f"warning: could not write health file: {e}", file=sys.stderr)


result = subprocess.run(
    [SYSTEM_PYTHON, str(SCRIPT), "index"],
    capture_output=True,
    text=True,
)
print(result.stdout, end="")
if result.stderr:
    print(result.stderr, end="", file=sys.stderr)

if result.returncode != 0:
    # Surface the actual failure rather than just an exit code — the last two
    # breakages were only diagnosable from the traceback.
    write_health(
        status="failed",
        items_captured=0,
        files_indexed=0,
        error=(result.stderr or "").strip()[-500:] or f"exit code {result.returncode}",
    )
    sys.exit(result.returncode)

# Total files currently covered by the index, read from the checkpoint (plain
# JSON, so this works without numpy). Used to tell "nothing changed" apart from
# "the index is empty" — the run prints no counts at all when it no-ops, and
# reading that as zero would raise a false alarm on a perfectly healthy day.
CHECKPOINT_FILE = Path.home() / ".hermes/vault-index/auto_checkpoint.json"
try:
    indexed_files_total = len(json.loads(CHECKPOINT_FILE.read_text()))
except (OSError, ValueError):
    indexed_files_total = None

# "✅ Indexed 23 files, 14741 total chunks"
m = re.search(r"Indexed\s+(\d+)\s+files,\s+(\d+)\s+total chunks", result.stdout)
up_to_date = "Index is up to date" in result.stdout

if m:
    files_indexed = int(m.group(1))
    total_chunks = int(m.group(2))
    # An empty index is a real failure — retrieval would return nothing.
    status = "zero-items" if total_chunks == 0 else "ok"
elif up_to_date:
    # No new or changed files. Normal and correct, not a failure.
    files_indexed, total_chunks = 0, None
    status = "zero-items" if indexed_files_total == 0 else "no-new-items"
else:
    # Exited 0 but printed nothing recognisable — treat as suspect rather than ok.
    files_indexed, total_chunks = 0, None
    status = "zero-items"

write_health(
    status=status,
    items_captured=files_indexed,
    files_indexed=files_indexed,
    total_chunks=total_chunks,
    indexed_files_total=indexed_files_total,
)
sys.exit(0)
