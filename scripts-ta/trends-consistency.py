#!/usr/bin/env python3
"""trends-consistency.py — daily "Today Across All Verticals" dashboard.

Reads the six vertical market-trends health files plus today's report files and
renders one markdown summary: each vertical's date, article count, synthesis
present, contract-conformance, and health state. This is the single place to
glance at whether every vertical is producing consistent, conformant daily
intelligence — without opening six separate report files.

Output: Knowledge/00-Raw/trends/TODAY.md (a rolling "latest" view, plus a dated
copy under System/_generated/trends-consistency-YYYY-MM-DD.md).
Schedule: Daily after the last generator (e.g. 06:15 MYT).

Reads only — never mutates the reports themselves.
"""
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trend_contract import VERTICALS, report_path, SYNTH_HEADING, validate_report_file

VAULT = os.path.expanduser("~/Documents/Obsidian Vault")
SYSTEM_DIR = os.path.join(VAULT, "System")
TRENDS_DIR = os.path.join(VAULT, "Knowledge", "00-Raw", "trends")
GEN_DIR = os.path.join(SYSTEM_DIR, "_generated")

# Ordered as the cron schedule runs them (early morning to late morning).
ORDER = [
    "csuite-leadership-trends",
    "fnb-trends",
    "hospitality-trends",
    "market-trends",
    "property-infra-energy-trends",
    "ai-science-investment-trends",
]


def _read_health(vertical_key: str):
    fname = VERTICALS[vertical_key]["health_file"]
    path = os.path.join(SYSTEM_DIR, fname)
    if not os.path.exists(path):
        return None
    try:
        return json.loads(open(path, encoding="utf-8", errors="ignore").read())
    except (json.JSONDecodeError, OSError):
        return None


def _report_stats(vertical_key: str, day: str):
    """Return (exists, synthesis, raw_count, valid_count) for a report file.

    Published coverage is the count of category-valid articles, never raw links.
    Otherwise a contaminated report can keep the publication gate green.
    """
    path = report_path(vertical_key, day)
    if not os.path.exists(path):
        return (False, False, 0, 0)
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return (True, False, 0, 0)
    has_synth = SYNTH_HEADING in text
    raw_count = 0
    valid_count = 0
    heading_to_category = {h.strip(): key for key, h in VERTICALS[vertical_key]["categories"].items()}
    current_category = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped in heading_to_category:
            current_category = heading_to_category[stripped]
            continue
        item = re.match(r"^\s*[-*]\s+\[([^\]]+)\]\((https?://[^\s\)]+)\)", line)
        if item:
            raw_count += 1
            # Same canonical intent test used in both retrieval and validation.
            from trend_contract import category_match
            if current_category and category_match(item.group(1), current_category):
                valid_count += 1
    return (True, has_synth, raw_count, valid_count)


def _age_hours(ts: str) -> float:
    try:
        return round((datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 3600, 1)
    except (ValueError, TypeError):
        return None


def build(day: str = None) -> str:
    day = day or datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows = []
    for key in ORDER:
        v = VERTICALS[key]
        h = _read_health(key)
        exists, has_synth, raw_count, count = _report_stats(key, day)
        # Re-validate the persisted capture. The generator's stored health only
        # reflects what it knew at write time; the capture itself is the audit
        # target and may contain historical or merge-on-write contamination.
        persisted_issues = validate_report_file(key, report_path(key, day)) if exists else ["report missing"]

        # Health state
        if persisted_issues:
            state = f"🔴 non-conformant: {'; '.join(str(i) for i in persisted_issues[:2])}"
        elif h is None:
            state = "⚫ never ran"
        else:
            total = h.get("total_articles", count)
            age = _age_hours(h.get("timestamp", ""))
            if age is not None and age > 30:
                state = f"🟠 stale ({age:.0f}h)"
            elif total == 0 and v.get("academic") is False:
                state = "🟡 0 articles (check feeds)"
            else:
                state = "✅ ok"

        synth = "✅" if has_synth else "❌"
        rows.append(
            f"| {v['title']} | {count} | {synth} | {state} |"
        )

    zero_nonacademic = 0
    for key in ORDER:
        v = VERTICALS[key]
        _exists, _synth, _raw_count, count = _report_stats(key, day)
        if v.get("academic") is False and count == 0:
            zero_nonacademic += 1
    stop_ship = zero_nonacademic >= 3
    table = "\n".join(
        ["| Vertical | Articles | Synthesis | Health |",
         "|---|---|---|---|"] + rows
    )
    gate = (
        "🚨 **STOP-SHIP: publishing is withheld.** Three or more non-academic verticals have zero valid articles; "
        "a thin AI-only briefing misrepresents coverage. Restore source yield and category validity first."
        if stop_ship else
        "✅ **Publication gate open.** Fewer than three non-academic verticals are empty."
    )

    report = f"""---
type: index
domain: knowledge
status: active
tags: [trends, consistency, dashboard, {now}]
---

# 📊 Today Across All Verticals — {day}

> Rolling consistency dashboard. Generated {now} by `~/.hermes/scripts/trends-consistency.py`.
> A red/non-conformant row means the vertical produced a malformed or miscategorized
> report — fix the generator, not this file.

{table}

{gate}

---
*Synthesis = the report contains an `## 🔍 Analysis & Synthesis` block. Health reads the
`conformant` flag each generator now writes to its health JSON.*
"""
    return report


def save(day: str = None):
    day = day or datetime.now().strftime("%Y-%m-%d")
    report = build(day)

    # Rolling "latest" view in the trends index dir.
    os.makedirs(TRENDS_DIR, exist_ok=True)
    latest = os.path.join(TRENDS_DIR, "TODAY.md")
    with open(latest, "w", encoding="utf-8") as f:
        f.write(report)

    # Dated copy for the record / flywheel visibility.
    os.makedirs(GEN_DIR, exist_ok=True)
    dated = os.path.join(GEN_DIR, f"trends-consistency-{day}.md")
    with open(dated, "w", encoding="utf-8") as f:
        f.write(report)

    return latest, dated


def main():
    latest, dated = save()
    print(f"📊 Today Across All Verticals — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  💾 {latest}")
    print(f"  📁 {dated}")
    # Print the table inline for Telegram visibility
    print()
    print(build().split("| Vertical", 1)[1])
    return 0


if __name__ == "__main__":
    main()
