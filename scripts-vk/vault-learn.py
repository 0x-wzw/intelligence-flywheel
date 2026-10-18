#!/usr/bin/env python3
"""vault-learn.py — deterministic replacement for the vault-learn agent job.

Replicates every step the agent job performed, without an LLM:
  1. Run vault-daily.py (daily note + health + obliviarch)          [was: agent runs script]
  2. Scan Daily/ notes from today + past 2 days, extract entries    [was: LLM extraction]
     -> heuristic extraction: headings, checkboxes, dated log lines
  3. Append dated entries to vaultkeeper MEMORY.md (deduped)        [was: LLM editing]
  4. Compare schema-lint-health.json vs previous snapshot           [was: LLM compares]
     -> flags >1% compliance regressions
  5. Print "Learned today:" summary (the job's deliverable)         [was: LLM reply]

Output goes to the job's deliver target (feishu home channel).
"""
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
VAULT = HOME / "Documents" / "Obsidian Vault"
DAILY = VAULT / "Daily"
SYSTEM = VAULT / "System"
HERMES = HOME / ".hermes"
# Profile copy first: it is authoritative for vaultkeeper and has
# obliviarch-unified.py beside it (vault-daily invokes it from its own dir).
SCRIPTS = [HERMES / "profiles" / "vaultkeeper" / "scripts" / "vault-daily.py",
           HERMES / "scripts" / "vault-daily.py"]
MEMORY = HERMES / "profiles" / "vaultkeeper" / "memories" / "MEMORY.md"
HEALTH = SYSTEM / "schema-lint-health.json"
HEALTH_PREV = SYSTEM / ".schema-lint-health-prev.json"

MAX_MEMORY_CHARS = 12000  # keep MEMORY.md compact like the agent kept it


def run_vault_daily() -> str:
    """Step 1 — run the deterministic daily script."""
    script = next((s for s in SCRIPTS if s.exists()), None)
    if not script:
        return "vault-daily.py not found"
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=600)
    out = (r.stdout or r.stderr).strip()
    return "\n".join(out.splitlines()[:8]) or f"exit={r.returncode}"


def scan_daily_notes(days: int = 3) -> list[str]:
    """Step 2 — heuristic extraction from recent Daily notes."""
    facts: list[str] = []
    today = datetime.now().date()
    for offset in range(days):
        day = today - timedelta(days=offset)
        p = DAILY / f"{day.isoformat()}.md"
        if not p.exists():
            continue
        try:
            lines = p.read_text().splitlines()
        except OSError:
            continue
        for ln in lines:
            s = ln.strip()
            # completed tasks and log bullets are the durable signal
            if s.startswith("- [x]") or s.startswith("- [X]"):
                facts.append(f"{day.isoformat()}: done — {s[5:].strip()}")
            elif s.startswith(("- ", "* ")) and len(s) > 12 and not s.startswith(("- [ ]", "* [ ]")):
                facts.append(f"{day.isoformat()}: {s[2:].strip()}")
    return facts


def update_memory(facts: list[str]) -> int:
    """Step 3 — append new dated facts to MEMORY.md (skip existing lines)."""
    if not facts or not MEMORY.exists():
        return 0
    text = MEMORY.read_text()
    existing = set(text.splitlines())
    stamp = datetime.now().strftime("%Y-%m-%d")
    fresh = [f for f in facts if f"{f[:10]}" in stamp and f not in existing]
    if not fresh:
        return 0
    block = "\n## § VAULT-LEARN-{stamp}\n".replace("{stamp}", stamp)
    block += "Automated capture (vault-learn.py). Kept compact; prune stale entries periodically.\n\n"
    block += "\n".join(f"- {f}" for f in fresh[:20]) + "\n"
    new_text = text.rstrip() + "\n" + block
    # compact: if grown past the cap, drop the oldest third of § sections
    if len(new_text) > MAX_MEMORY_CHARS:
        sections = re.split(r"(?m)^(?=## § )", new_text)
        header, rest = sections[0], sections[1:]
        keep = max(4, len(rest) - len(rest) // 3)
        new_text = header + "".join(rest[-keep:])
    MEMORY.write_text(new_text)
    return len(fresh[:20])


def schema_regression() -> str:
    """Step 4 — compare current schema-lint-health.json vs previous snapshot."""
    if not HEALTH.exists():
        return "schema-lint-health.json not present yet"
    try:
        cur = json.loads(HEALTH.read_text()).get("summary", {})
    except (OSError, ValueError):
        return "schema-lint-health.json unreadable"
    line = (f"compliance {cur.get('compliance_pct', '?')}% "
            f"(undateable={cur.get('undateable_imports', '?')}, "
            f"missing_required={cur.get('missing_required', '?')})")
    if HEALTH_PREV.exists():
        try:
            prev = json.loads(HEALTH_PREV.read_text()).get("summary", {})
            delta = (cur.get("compliance_pct", 0) or 0) - (prev.get("compliance_pct", 0) or 0)
            if delta < -1.0:
                line += f" — REGRESSION {delta:+.1f}% (CRITICAL)"
            elif delta < 0:
                line += f" — drift {delta:+.1f}% (warning)"
            else:
                line += f" — {delta:+.1f}% vs previous (ok)"
        except (OSError, ValueError):
            pass
    # rotate snapshot for next comparison
    try:
        HEALTH_PREV.write_text(json.dumps({"summary": cur}, indent=2))
    except OSError:
        pass
    return line


def main() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"📖 Vault Learn — {today}")

    daily = run_vault_daily()
    print(f"  1️⃣ vault-daily: {daily.splitlines()[0] if daily else 'no output'}")

    facts = scan_daily_notes()
    added = update_memory(facts)
    print(f"  2️⃣ Daily scan: {len(facts)} durable items, {added} new → MEMORY.md")

    print(f"  3️⃣ Schema: {schema_regression()}")

    print("\nLearned today:")
    for f in facts[:6]:
        print(f"  • {f[:110]}")
    if not facts:
        print("  • (no new durable items in Daily/ from the past 3 days)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
