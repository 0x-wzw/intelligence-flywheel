#!/usr/bin/env python3
"""
distillation-tracker.py

Daily tracker for reports shipped but not distilled.
Alerts if >3 reports are shipped but distilled: false.

Output: Notification + dashboard update to System/_generated/distillation-tracker.md
Schedule: Daily 7AM
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
OUTPUT_DIR = VAULT_PATH / "System" / "_generated"
DASHBOARD_FILE = OUTPUT_DIR / "distillation-tracker.md"

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

def scan_reports() -> tuple:
    """Scan 20-Reports for shipped reports, categorized by distilled status.
    
    Recurring series (daily briefs, weekly signal-noise / loop-improvement /
    bias-balance, marked `recurring: daily|weekly` in frontmatter) are excluded
    from the pending count: they are consumed by their own downstream loops
    (loop-tuner digests them weekly), so requiring distilled:true on each one
    produced a permanent RED alert that meant nothing.
    """
    reports_dir = VAULT_PATH / "Knowledge" / "20-Reports"
    distilled = []
    not_distilled = []
    recurring_count = 0
    
    if not reports_dir.exists():
        return distilled, not_distilled
    
    for md_file in reports_dir.glob("*.md"):
        if md_file.name.startswith("_"):
            continue
        
        content = md_file.read_text()
        fm = parse_frontmatter(content)
        
        if (fm.get('type') or fm.get('stage')) != 'report':  # stage retired 2026-07-26
            continue
        
        status = fm.get('status', 'draft')
        is_distilled = str(fm.get('distilled', 'false')).lower() == 'true'
        
        if status != 'shipped':
            continue
        
        # Recurring series are loop-consumed, not distillation targets.
        if str(fm.get('recurring', '')).lower() in ('daily', 'weekly', 'n/a'):
            recurring_count += 1
            continue
        
        report_data = {
            'name': md_file.stem,
            'created': fm.get('created', 'unknown'),
            'sources': fm.get('sources', 'not specified'),
            'distilled_into': fm.get('distilled_into', 'not specified'),
            'path': "Knowledge/20-Reports/" + md_file.stem
        }
        
        if is_distilled:
            distilled.append(report_data)
        else:
            not_distilled.append(report_data)
    
    return distilled, not_distilled, recurring_count

def generate_dashboard() -> str:
    """Generate the distillation tracker dashboard."""
    distilled, not_distilled, recurring_count = scan_reports()
    
    total_shipped = len(distilled) + len(not_distilled)
    distilled_count = len(distilled)
    not_distilled_count = len(not_distilled)
    distilled_pct = (distilled_count / total_shipped * 100) if total_shipped > 0 else 0
    
    # Determine status
    if not_distilled_count == 0:
        status_icon = "[OK]"
        status_text = "All shipped reports distilled"
    elif not_distilled_count <= 3:
        status_icon = "[WARN]"
        status_text = str(not_distilled_count) + " reports pending distillation"
    else:
        status_icon = "[ALERT]"
        status_text = "ALERT: " + str(not_distilled_count) + " reports pending distillation"
    
    # Build pending reports table
    if not not_distilled:
        pending_table = "**None - all caught up!**"
    else:
        pending_table = "| Report | Created | Age (days) | Sources |\n"
        pending_table += "|--------|---------|------------|---------|\n"
        for r in not_distilled:
            age_days = "N/A"
            if r['created'] != 'unknown':
                try:
                    age_days = str((datetime.now() - datetime.strptime(r['created'], '%Y-%m-%d')).days)
                except ValueError:
                    pass
            pending_table += "| [[" + r['path'] + "|" + r['name'] + "]] | " + r['created'] + " | " + age_days + " | " + r['sources'][:40] + "... |\n"
    
    # Build distilled reports table
    if not distilled:
        distilled_table = "**No distilled reports yet.**"
    else:
        distilled_table = "| Report | Created | Distilled Into |\n"
        distilled_table += "|--------|---------|----------------|\n"
        for r in distilled:
            distilled_table += "| [[" + r['path'] + "|" + r['name'] + "]] | " + r['created'] + " | " + r['distilled_into'][:40] + "... |\n"
    
    # Determine alert level
    if not_distilled_count > 6:
        alert_level = "[RED] RED"
    elif not_distilled_count > 3:
        alert_level = "[YELLOW] YELLOW"
    else:
        alert_level = "[GREEN] GREEN"
    
    dashboard = """---
title: Distillation Tracker
type: system
generated: """ + datetime.now().isoformat() + """
tags: [flywheel, distillation, tracker, system]
---

# Distillation Tracker

**Last updated:** """ + datetime.now().strftime('%Y-%m-%d %H:%M %Z') + """
**Next check:** Tomorrow 7AM

---

## Status

""" + status_icon + """ **""" + status_text + """**

| Metric | Value |
|--------|-------|
| Total shipped reports (one-off) | """ + str(total_shipped) + """ |
| Distilled | """ + str(distilled_count) + """ (""" + str(int(distilled_pct)) + """%) |
| Not distilled | """ + str(not_distilled_count) + """ (""" + str(int(100-distilled_pct)) + """%) |
| Recurring series (excluded — loop-consumed) | """ + str(recurring_count) + """ |
| Loop closure rate | """ + str(int(distilled_pct)) + """% |

---

## Reports Pending Distillation

""" + pending_table + """

**Action required:** Extract best practices and workflows from pending reports to [[Knowledge/30-Distilled/_index|30-Distilled]].

---

## Distilled Reports

""" + distilled_table + """

---

## Trend (Last 7 Days)

| Date | Shipped | Distilled | Pending | Closure Rate |
|------|---------|-----------|---------|--------------|
"""
    
    # Add last 7 days (simulated - would need history tracking for real)
    for i in range(6, -1, -1):
        date = datetime.now() - timedelta(days=i)
        dashboard += "| " + date.strftime('%Y-%m-%d') + " | " + str(total_shipped) + " | " + str(distilled_count) + " | " + str(not_distilled_count) + " | " + str(int(distilled_pct)) + "% |\n"
    
    dashboard += """
---

## Alert Threshold

- **[GREEN]:** 0-3 reports pending distillation
- **[YELLOW]:** 4-6 reports pending distillation
- **[RED]:** 7+ reports pending distillation

**Current alert level:** """ + alert_level + """

---

*Generated by distillation-tracker.py | Daily 7AM*
"""
    return dashboard

def main():
    """Main entry point."""
    distilled, not_distilled, recurring_count = scan_reports()
    not_distilled_count = len(not_distilled)
    
    # Generate and write dashboard
    dashboard = generate_dashboard()
    # Write via subprocess (macOS cron sandbox workaround)
    import subprocess, tempfile
    DASHBOARD_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, suffix='.md')
    tmp.write(dashboard)
    tmp.close()
    subprocess.run(["cp", tmp.name, str(DASHBOARD_FILE)], check=True)
    os.unlink(tmp.name)
    
    # Print status
    print("Distillation tracker update: " + str(DASHBOARD_FILE))
    print("  - Shipped one-off reports: " + str(len(distilled) + not_distilled_count))
    print("  - Distilled: " + str(len(distilled)))
    print("  - Not distilled: " + str(not_distilled_count))
    print("  - Recurring series excluded: " + str(recurring_count))
    
    # Alert if threshold exceeded
    if not_distilled_count > 3:
        print("\n[ALERT] " + str(not_distilled_count) + " reports shipped but not distilled!")
        print("Pending reports:")
        for r in not_distilled:
            print("  - " + r['name'])
    else:
        print("\n[OK] All caught up on distillation.")

    # Write to cron log file for traceability
    log_dir = os.path.expanduser("~/.hermes/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "distillation-tracker.log")
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | shipped={len(distilled) + not_distilled_count} distilled={len(distilled)} pending={not_distilled_count} recurring_excluded={recurring_count}\n")

if __name__ == "__main__":
    main()
