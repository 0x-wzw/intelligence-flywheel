#!/usr/bin/env python3
"""
cycle-kickoff-reminder.py

Quarterly reminder to run Cycle Kickoff.
Auto-generates draft Cycle Kickoff note with carry-forward table pre-populated.

Output: Draft kickoff note to Knowledge/00-Raw/Cycle Kickoff — YYYY-QQ.md
Schedule: Quarterly (first day of quarter) 8AM
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
OUTPUT_DIR = VAULT_PATH / "Knowledge" / "00-Raw"

def get_quarter(dt: datetime) -> tuple[int, int]:
    """Return (year, quarter) for given datetime."""
    quarter = (dt.month - 1) // 3 + 1
    return dt.year, quarter

def get_previous_quarter(year: int, quarter: int) -> tuple[int, int]:
    """Return previous quarter as (year, quarter)."""
    if quarter == 1:
        return year - 1, 4
    return year, quarter - 1

def parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter as dict."""
    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return {}
    
    fm = {}
    for line in match.group(1).split('\n'):
        if ':' in line:
            key, value = line.split(':', 1)
            fm[key.strip()] = value.strip().strip('"\'')
    return fm

def scan_distilled_content() -> list[dict]:
    """Scan 30-Distilled for content to carry forward."""
    distilled_dir = VAULT_PATH / "Knowledge" / "30-Distilled"
    items = []
    
    if not distilled_dir.exists():
        return items
    
    # Scan best-practices
    bp_dir = distilled_dir / "best-practices"
    if bp_dir.exists():
        for md_file in bp_dir.glob("*.md"):
            content = md_file.read_text()
            fm = parse_frontmatter(content)
            items.append({
                'name': md_file.stem,
                'kind': 'Best Practice',
                'cycle': fm.get('cycle', 'unknown'),
                'derived_from': fm.get('derived_from', 'not specified'),
                'path': f"Knowledge/30-Distilled/best-practices/{md_file.stem}"
            })
    
    # Scan workflows
    wf_dir = distilled_dir / "workflows"
    if wf_dir.exists():
        for md_file in wf_dir.glob("*.md"):
            content = md_file.read_text()
            fm = parse_frontmatter(content)
            items.append({
                'name': md_file.stem,
                'kind': 'Workflow',
                'cycle': fm.get('cycle', 'unknown'),
                'owner': fm.get('owner', 'not specified'),
                'path': f"Knowledge/30-Distilled/workflows/{md_file.stem}"
            })
    
    return items

def scan_shipped_reports() -> list[dict]:
    """Scan 20-Reports for shipped reports."""
    reports_dir = VAULT_PATH / "Knowledge" / "20-Reports"
    reports = []
    
    if not reports_dir.exists():
        return reports
    
    for md_file in reports_dir.glob("*.md"):
        if md_file.name.startswith("_"):
            continue
        
        content = md_file.read_text()
        fm = parse_frontmatter(content)
        
        if (fm.get('type') or fm.get('stage')) == 'report' and fm.get('status') == 'shipped':  # stage retired 2026-07-26
            reports.append({
                'name': md_file.stem,
                'cycle': fm.get('cycle', 'unknown'),
                'distilled': fm.get('distilled', 'false'),
                'path': f"Knowledge/20-Reports/{md_file.stem}"
            })
    
    return reports

def generate_cycle_kickoff(year: int, quarter: int) -> str:
    """Generate Cycle Kickoff note for given quarter."""
    prev_year, prev_quarter = get_previous_quarter(year, quarter)
    cycle_name = f"{year}-Q{quarter}"
    prev_cycle = f"{prev_year}-Q{prev_quarter}"
    
    distilled_items = scan_distilled_content()
    shipped_reports = scan_shipped_reports()
    
    # Build carry-forward table
    carry_forward_rows = []
    for item in distilled_items:
        carry_forward_rows.append(f"| [[{item['path']}|{item['name']}]] | {item['kind']} | {item['cycle']} | Proven format/lesson |")
    
    # If no distilled content, add placeholder
    if not carry_forward_rows:
        carry_forward_rows.append("| | | | No distilled content yet — inaugural cycle |")
    
    # Build shipped reports table
    reports_rows = []
    for report in shipped_reports:
        distilled_status = "✅" if report['distilled'].lower() == 'true' else "⚠️"
        reports_rows.append(f"| [[{report['path']}|{report['name']}]] | {report['cycle']} | {distilled_status} |")
    
    if not reports_rows:
        reports_rows.append("| | | | No shipped reports yet |")
    
    kickoff = f"""---
title: Cycle Kickoff — {cycle_name}
type: template
domain: knowledge
kind: cycle-kickoff
cycle: {cycle_name}
status: draft
created: {datetime.now().strftime('%Y-%m-%d')}
tags: [knowledge, cycle, loop, {authorship_tag()}]
---

# Cycle Kickoff — {cycle_name}

The mechanism that **closes the loop**. Run this at the start of every cycle so
the new cycle inherits the last cycle's distilled lessons and formats instead of
starting from scratch.

> **New cycle:** {cycle_name} · **Prior cycle:** {prev_cycle}
> **Generated:** {datetime.now().strftime('%A, %d %B %Y — %H:%M %Z')}
> **Auto-generated by:** `cycle-kickoff-reminder.py`

---

## 1. Harvest the prior cycle ({prev_cycle})

### Distilled content to carry forward

| Item | Type | From Cycle | Why it carries |
|------|------|------------|----------------|
{chr(10).join(carry_forward_rows)}

### Shipped reports from prior cycle

| Report | Cycle | Distilled |
|--------|-------|-----------|
{chr(10).join(reports_rows)}

---

## 2. Seed the new cycle ({cycle_name})

- [ ] Review [[Knowledge/30-Distilled/_index|30-Distilled]] — which best-practices/workflows apply now?
- [ ] Review [[Knowledge/40-Templates/_index|40-Templates]] — which formats carry forward?
- [ ] Confirm every shipped report in {prev_cycle} has `distilled: true` (loop closed).
- [ ] Create the new cycle's raw working set in [[Knowledge/00-Raw/_index|00-Raw]].
- [ ] Instantiate the carried-forward templates as starting scaffolds.
- [ ] Set `cycle:` on new notes to {cycle_name}.

---

## 3. Carry-forward log

| Item | From cycle | Why it carries |
|------|------------|----------------|
| [[Knowledge/30-Distilled/best-practices/P0-Dependency-Model|P0 Dependency Model]] | {prev_cycle} | Universal decision-making pattern |
| [[Knowledge/30-Distilled/workflows/Four-Part-Teardown|Four-Part Teardown]] | {prev_cycle} | Adversarial stress-test for any major decision |
| [[Knowledge/30-Distilled/workflows/Multi-Region-Competition-Cycle|Multi-Region Competition Cycle]] | {prev_cycle} | Reusable for any global competition |

---

## 4. Retire

- [ ] Archive/close raw items that never got promoted (decide: promote or drop).
- [ ] Flag any wiki not reviewed in 90+ days for refresh.
- [ ] Review [[System/flywheel-health-*|flywheel health checks]] from {prev_cycle} for patterns.

---

## 5. {cycle_name} Goals

| Goal | Metric | Target |
|------|--------|--------|
| Raw items processed | Count | 20+ |
| Reports shipped | Count | 3-5 |
| Best practices extracted | Count | 5+ |
| Workflows extracted | Count | 3+ |
| Loop closed | % shipped reports distilled | 100% |

---

## 6. Cycle timeline

| Milestone | Target Date | Status |
|-----------|-------------|--------|
| Cycle Kickoff complete | {datetime.now().strftime('%Y-%m-%d')} | ⏳ Not started |
| Mid-cycle review | {(datetime.now() + timedelta(days=45)).strftime('%Y-%m-%d')} | ⏳ Not started |
| Cycle close | {(datetime.now() + timedelta(days=90)).strftime('%Y-%m-%d')} | ⏳ Not started |

---

*This is the flywheel. Each turn, the vault gets smarter than it was.*

**Cycle status:** Draft — ready for review and activation

---

*Generated by `cycle-kickoff-reminder.py` on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
    return kickoff

def main():
    """Main entry point."""
    # Determine current quarter
    year, quarter = get_quarter(datetime.now())
    cycle_name = f"{year}-Q{quarter}"
    
    # Generate kickoff note
    kickoff = generate_cycle_kickoff(year, quarter)
    
    # Write to output directory
    output_file = OUTPUT_DIR / f"Cycle Kickoff — {cycle_name}.md"
    output_file.write_text(kickoff)
    
    print(f"Cycle Kickoff generated: {output_file}")
    print(f"  - Cycle: {cycle_name}")
    print(f"  - Distilled items to carry forward: {len(scan_distilled_content())}")
    print(f"  - Shipped reports from prior cycle: {len(scan_shipped_reports())}")
    print(f"\nNext reminder: {(datetime.now().replace(month=((datetime.now().month) % 12) + 1, day=1)).strftime('%Y-%m-%d')} 8AM")

    # Write to cron log file for traceability
    log_dir = os.path.expanduser("~/.hermes/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "cycle-kickoff.log")
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | cycle={cycle_name} output={output_file}\n")

if __name__ == "__main__":
    main()
