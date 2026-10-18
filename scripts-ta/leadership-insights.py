#!/usr/bin/env python3
"""Weekly Leadership & Consulting Insights — fetches from HBR, McKinsey, BCG, Bain,
Accenture, Gartner, Deloitte, PwC, EY, KPMG, WEF, Stanford GSB, HBS, INSEAD, LBS,
Kellogg, Yale using headless browser.

Runs weekly on Sunday at 10:00 via cron. Output is delivered to Telegram.
"""
import json, os, re, html, subprocess, sys, urllib.parse, urllib.request
from datetime import datetime
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path: sys.path.insert(0, _SCRIPTS_DIR)
from dedup import SeenUrls
from headless_fetcher import fetch_leadership_headless, fetch_academic_headless

VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
OUTPUT_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "leadership")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "leadership-insights-health.json")
from hermes_feeds import frontmatter_block  # Schema v2 frontmatter, one definition

def _frontmatter():
    """Schema v2 frontmatter for this report (see System/SCHEMA.md)."""
    return frontmatter_block(
        title=f"Leadership Insights — {datetime.now():%Y-%m-%d}",
        tags=['raw', 'leadership', 'consulting', 'business-schools'],
        note_type="raw",
        domain="knowledge",
    ) + "\n"


def build_report():
    seen = SeenUrls(); seen.load()
    
    print("  Fetching consulting firm insights (headless browser)...")
    consulting = fetch_leadership_headless(5)
    print(f"    Got {len(consulting)} items")
    
    print("  Fetching business school insights (headless browser)...")
    academic = fetch_academic_headless(5)
    print(f"    Got {len(academic)} items")
    
    all_items = consulting + academic
    new_items = []
    for i in all_items:
        if i["url"] and seen.is_new(i["url"], i.get("date",""), i.get("title","")):
            new_items.append(i)
            seen.mark(i["url"], i.get("title",""))
    seen.save()
    
    today = datetime.now().strftime("%Y-%m-%d")
    r = f"# Leadership & Consulting Insights — {today}\n\n"
    r += "## 🏢 Consulting Firms\n\n"
    for i in consulting:
        r += f"- [{i['title']}]({i['url']})  — *{i.get('source','')}*\n"
    r += "\n## 🎓 Business Schools\n\n"
    for i in academic:
        r += f"- [{i['title']}]({i['url']})  — *{i.get('source','')}*\n"
    r += f"\n---\n*Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}*"
    return r, new_items, consulting, academic

def save_local(report):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    p = os.path.join(OUTPUT_DIR, f"leadership-insights-{datetime.now().strftime('%Y-%m-%d')}.md")
    with open(p, "w") as f: f.write(_frontmatter() + report)
    return f"Saved locally: {p}"

def main():
    report, new_items, consulting, academic = build_report()
    total = len(new_items)
    result = save_local(report)
    
    health = {"timestamp":datetime.now().isoformat(),"total_articles":total,"consulting":len(consulting),"academic":len(academic),"result":result}
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f: json.dump(health, f, indent=2)
    
    log_dir = os.path.expanduser("~/.hermes/logs"); os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "leadership-insights.log"), "a") as f:
        f.write(f"{datetime.now().isoformat()} | total={total} consulting={len(consulting)} academic={len(academic)}\n")
    
    print(f"📊 Leadership & Consulting Insights — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  📰 {total} new articles ({len(consulting)} consulting, {len(academic)} business schools)")
    print(f"  📤 {result}")
    print()
    for label, items in [("Consulting Firms", consulting), ("Business Schools", academic)]:
        if items:
            print(f"── {label} ──")
            for i in items[:5]:
                print(f"  • {i['title']} [{i.get('source','')}]")
            if len(items) > 5: print(f"    … and {len(items)-5} more")
            print()
    return 0

if __name__ == "__main__": main()
