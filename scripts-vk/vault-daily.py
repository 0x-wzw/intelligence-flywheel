#!/usr/bin/env python3
"""Vault daily maintenance script — run by cron jobs.

Creates daily note if missing, checks vault health, runs Obliviarch consolidation.
Uses local t4-agent model for all inference to avoid cloud costs.
"""
import json
import os
import subprocess
import sys
from datetime import datetime

VAULT = os.path.expanduser("~/Documents/Obsidian Vault")
DAILY_DIR = os.path.join(VAULT, "Daily")
SYSTEM_DIR = os.path.join(VAULT, "System")
WIKI_DIR = os.path.join(SYSTEM_DIR, "Wiki")
HEALTH_LOG = os.path.join(SYSTEM_DIR, "vault-health.json")


def ensure_daily_note():
    """Create today's daily note if it doesn't exist."""
    today = datetime.now().strftime("%Y-%m-%d")
    daily_path = os.path.join(DAILY_DIR, f"{today}.md")
    if os.path.exists(daily_path):
        return f"Daily note exists: {today}"
    os.makedirs(DAILY_DIR, exist_ok=True)
    with open(daily_path, "w") as f:
        f.write(f"---\ntags: [daily]\ndate: {today}\n---\n\n# {today}\n\n## Tasks\n\n## Notes\n\n## Log\n")
    return f"Created daily note: {today}"


def check_vault_health():
    """Basic vault health check."""
    issues = []
    if not os.path.isdir(VAULT):
        issues.append("Vault root missing")
    if not os.path.isdir(DAILY_DIR):
        issues.append("Daily dir missing")
    if not os.path.isdir(SYSTEM_DIR):
        issues.append("System dir missing")
    return issues if issues else ["OK"]


def run_obliviarch():
    """Run Obliviarch consolidation if script exists.

    The unified consolidation script lives in the profile scripts dir (not the
    vault root, which never carried an obliviarch.py). Its main() takes no CLI
    args, so invoke it directly.
    """
    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "obliviarch-unified.py",
    )
    if not os.path.exists(script):
        return "Obliviarch not found"
    try:
        result = subprocess.run(
            [sys.executable, script],
            capture_output=True, text=True, timeout=300,
        )
        return result.stdout.strip()[:500] if result.returncode == 0 else f"Error: {result.stderr.strip()[:200]}"
    except Exception as e:
        return f"Failed: {e}"


def main():
    daily = ensure_daily_note()
    health = check_vault_health()
    obliviarch = run_obliviarch()

    results = {
        "timestamp": datetime.now().isoformat(),
        "daily_note": daily,
        "health": health,
        "obliviarch": obliviarch,
    }
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f:
        json.dump(results, f, indent=2)

    # Print a clean summary for Telegram delivery (no-agent mode)
    print(f"📋 Vault Daily — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  📄 {daily}")
    print(f"  🩺 Health: {'✅ OK' if health == ['OK'] else '⚠️ ' + ', '.join(health)}")
    print(f"  🤖 Obliviarch: {obliviarch}")
    return 0 if health == ["OK"] else 1


if __name__ == "__main__":
    sys.exit(main())
