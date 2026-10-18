#!/usr/bin/env python3
"""Goal ticker — probe the owning profile's active goal for a no_agent cron job.

Lives in <profile-home>/scripts/goal-ticker.py and derives its home from
__file__ (parents[1]), never from HERMES_HOME (the multiplexer's process env
holds the launch profile). Prints a single GOAL_ACTIVE JSON line only when the
canonical Bot Chat session of THIS profile has an active, non-waiting goal with
turn budget left; prints NOTHING otherwise (empty stdout = silent delivery, so
an idle tick costs zero model tokens).
"""
import json
import sqlite3
import sys
from pathlib import Path

home = Path(__file__).resolve().parents[1]
db_path = home / "state.db"
if not db_path.exists():
    sys.exit(0)

try:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    row = conn.execute(
        "SELECT id FROM sessions WHERE title='Bot Chat' ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    if not row:
        conn.close()
        sys.exit(0)
    meta = conn.execute(
        "SELECT value FROM state_meta WHERE key=?", (f"goal:{row[0]}",)
    ).fetchone()
    conn.close()
    if not meta:
        sys.exit(0)
    g = json.loads(meta[0])
except Exception:
    sys.exit(0)

if g.get("status") != "active":
    sys.exit(0)
if g.get("waiting_on_pid") or g.get("waiting_on_session") or (g.get("waiting_until") or 0) > 0:
    sys.exit(0)
if (g.get("turns_used") or 0) >= (g.get("max_turns") or 500):
    sys.exit(0)

print("GOAL_ACTIVE " + json.dumps(g, ensure_ascii=False))
