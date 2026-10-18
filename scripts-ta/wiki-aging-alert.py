#!/usr/bin/env python3
"""
wiki-aging-alert.py

Weekly alert for wiki pages not reviewed >90 days.
Appends aging list to System/Wiki/log.md.

Output: List appended to System/Wiki/log.md
Schedule: Weekly Sunday 6AM
"""

import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
WIKI_DIR = VAULT_PATH / "System" / "Wiki"
LOG_FILE = WIKI_DIR / "log.md"

def parse_frontmatter(content: str) -> Dict[str, Any]:
    """Extract YAML frontmatter as dict."""
    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return {}
    
    fm: Dict[str, Any] = {}
    for line in match.group(1).split('\n'):
        if ':' in line:
            key, value = line.split(':', 1)
            fm[key.strip()] = value.strip().strip('"\'')
    return fm

def get_file_date(fm: Dict[str, Any], *keys) -> Optional[datetime]:
    """Get datetime from frontmatter, trying multiple keys."""
    for key in keys:
        if key in fm and fm[key]:
            try:
                return datetime.strptime(fm[key], '%Y-%m-%d')
            except ValueError:
                continue
    return None

def scan_wiki_pages() -> List[Dict[str, Any]]:
    """Find wiki pages not reviewed >90 days."""
    aging_pages: List[Dict[str, Any]] = []
    cutoff = datetime.now() - timedelta(days=90)
    
    if not WIKI_DIR.exists():
        return aging_pages
    
    for md_file in WIKI_DIR.rglob("*.md"):
        # Skip index and schema files
        if md_file.name in ("_index.md", "SCHEMA.md", "log.md", "index.md"):
            continue
        if md_file.parent.name == "raw":
            continue
        
        content = md_file.read_text()
        fm = parse_frontmatter(content)
        
        # Try to find last-reviewed or updated date
        last_reviewed = get_file_date(fm, 'last-reviewed', 'updated')
        created = get_file_date(fm, 'created')
        
        # Use created date if no review date exists
        review_date = last_reviewed or created
        
        if review_date and review_date < cutoff:
            days_since = (datetime.now() - review_date).days
            aging_pages.append({
                'file': str(md_file.relative_to(WIKI_DIR)),
                'title': fm.get('title', md_file.stem),
                'last_reviewed': fm.get('last-reviewed', fm.get('created', 'unknown')),
                'days_since_review': days_since,
                'type': fm.get('type', 'unknown'),
                'tags': fm.get('tags', '')
            })
    
    return sorted(aging_pages, key=lambda x: x['days_since_review'], reverse=True)

def generate_log_entry(aging_pages: List[Dict[str, Any]]) -> str:
    """Generate log entry for aging wiki pages."""
    today = datetime.now().strftime('%Y-%m-%d')
    day_name = datetime.now().strftime('%A')
    
    entry = f"""
## {today} — Wiki Aging Alert ({day_name} 6AM)

**Scan complete:** {datetime.now().strftime('%Y-%m-%d %H:%M %Z')}
**Threshold:** 90 days without review
**Aging pages found:** {len(aging_pages)}

"""
    
    if not aging_pages:
        entry += "**✅ All wiki pages are current** — no pages older than 90 days without review.\n"
    else:
        entry += f"""### Pages needing review ({len(aging_pages)} total)

| Page | Title | Last Reviewed | Days Since | Type |
|------|-------|---------------|------------|------|
"""
        for page in aging_pages[:30]:  # Limit to top 30
            entry += f"| [[{page['file'].replace('.md', '')}|{page['title'][:30]}]] | {page['last_reviewed']} | {page['days_since_review']} | {page['type']} |\n"
        
        if len(aging_pages) > 30:
            entry += f"\n*...and {len(aging_pages) - 30} more. See full scan output.*\n"
        
        entry += f"""
### Action items

- [ ] Review top {min(10, len(aging_pages))} oldest pages
- [ ] Update `last-reviewed:` date in frontmatter for pages that are still current
- [ ] Archive pages that are obsolete (move to `_archive/`, remove from [[System/Wiki/index|index]])
- [ ] Schedule remaining pages for review over next {max(1, len(aging_pages) // 3)} weeks

"""
    
    entry += f"""---
*Logged by `wiki-aging-alert.py` | Next scan: Sunday 6AM*
"""
    
    return entry

def append_to_log(entry: str):
    """Append entry to log file."""
    import subprocess, tempfile
    if LOG_FILE.exists():
        content = LOG_FILE.read_text()
        # Add newline if file doesn't end with one
        if not content.endswith('\n'):
            content += '\n'
        content += entry
        # Write via subprocess (macOS cron sandbox workaround)
        tmp = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, suffix='.md')
        tmp.write(content)
        tmp.close()
        subprocess.run(["cp", tmp.name, str(LOG_FILE)], check=True)
        os.unlink(tmp.name)
    else:
        # Create new log file with header
        header = f"""---
title: System/Wiki Log
type: system-log
tags: [wiki, log, system]
---

# System/Wiki Change Log

This log tracks all changes, updates, and alerts for the System/Wiki knowledge base.

---

"""
        # Write via subprocess (macOS cron sandbox workaround)
        tmp = tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', delete=False, suffix='.md')
        tmp.write(header + entry)
        tmp.close()
        subprocess.run(["cp", tmp.name, str(LOG_FILE)], check=True)
        os.unlink(tmp.name)

def main():
    """Main entry point."""
    aging_pages = scan_wiki_pages()
    
    # Generate and append log entry
    entry = generate_log_entry(aging_pages)
    append_to_log(entry)
    
    # Print status
    print(f"Wiki aging alert: {LOG_FILE}")
    print(f"  - Aging pages (>90 days): {len(aging_pages)}")
    
    if aging_pages:
        print(f"\n⚠️  Top 5 oldest pages:")
        for page in aging_pages[:5]:
            print(f"  - {page['days_since_review']} days: {page['file']}")
    else:
        print("\n✅ All wiki pages are current.")

    # Write to cron log file for traceability
    log_dir = os.path.expanduser("~/.hermes/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "wiki-aging-alert.log")
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | aging_pages={len(aging_pages)}\n")

if __name__ == "__main__":
    main()
