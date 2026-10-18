#!/usr/bin/env python3
"""Hospitality & Healthcare Market Trends — fetches latest trends in hotel, hospitality,
tourism, co-living, experiential travel, retirement living + care, TCM, rehabilitation care
and technologies, then pushes a structured report to a Notion database.

Runs daily at 02:00 via cron. Output is delivered to Telegram.
"""
import json, os, re, html, subprocess, sys, urllib.parse, urllib.request
from datetime import datetime
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path: sys.path.insert(0, _SCRIPTS_DIR)
from dedup import SeenUrls
from multi_source_fetcher import fetch_with_summaries

NOTION_DATABASE_ID = "39e05e4a-7c41-8197-b832-d2569e0599ad"
VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "hospitality-trends-health.json")
from hermes_feeds import frontmatter_block  # Schema v2 frontmatter, one definition
from trend_synthesizer import synthesize as _synthesize
from trend_contract import validate_report as _validate_report
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_trend_save import save_report as _save_report

def _frontmatter():
    """Schema v2 frontmatter for this report (see System/SCHEMA.md)."""
    return frontmatter_block(
        title=f"Market Trends — Hospitality & Healthcare — {datetime.now():%Y-%m-%d}",
        tags=['raw', 'trends', 'hospitality'],
        note_type="raw",
        domain="knowledge",
    ) + "\n"


def _get_notion_token():
    try:
        r = subprocess.run(["ps", "auxwww"], capture_output=True, text=True, timeout=5)
        for l in r.stdout.split("\n"):
            if "notion-mcp-server" in l and "NOTION_API_TOKEN=" in l:
                m = re.search(r"NOTION_API_TOKEN=(\S+)", l)
                if m and "..." not in m.group(1) and len(m.group(1)) > 40: return m.group(1)
    except: pass
    return ""
USE_NOTION = False  # intelligence flywheel (2026-10-08): raw captures are vault-only; Notion gets DISTILLED insights via intelligence-publish.py

def build_report():
    seen = SeenUrls(); seen.load()
    queries = {
        "hotel_hospitality": "hotel hospitality technology trends 2026 innovation -pv-magazine.com",
        "tourism": "tourism travel trends 2026 technology",
        "co_living": "co-living coliving trends 2026",
        "experiential_travel": "experiential travel trends 2026",
        "retirement_living": "retirement living senior care trends 2026",
        "tcm": "traditional Chinese medicine TCM trends 2026",
        "rehabilitation_care": "rehabilitation care physical therapy trends 2026",
    }
    all_data = {}
    for cat, q in queries.items():
        items = fetch_with_summaries(q, category_key=cat, max_per_source=2, fetch_summaries=False, academic=False)
        # Additional domain filtering for hospitality - exclude solar energy sites
        if cat == "hotel_hospitality" or cat == "co_living":
            items = [item for item in items if "pv-magazine.com" not in item.get("url", "") and 
                     "datacenterdynamics.com" not in item.get("url", "")]
        new = []
        for i in items:
            if i["url"] and seen.is_new(i["url"], i.get("date",""), i.get("title","")):
                new.append(i); seen.mark(i["url"], i.get("title",""))
        all_data[cat] = new
    seen.save()
    today = datetime.now().strftime("%Y-%m-%d")
    r = f"# Hospitality & Healthcare Trends — {today}\n\n"

    # LLM analysis & synthesis based on today's items + recent daily reports
    _synth = _synthesize(
        "Hospitality & Healthcare",
        all_data,
        recent_report_glob="hospitality-trends-2026-*.md",
        fallback_report="hospitality-trends",
    )
    if _synth:
        r += _synth + "\n\n"

    sections = {"hotel_hospitality":"## 🏨 Hotel & Hospitality\n","tourism":"## ✈️ Tourism\n","co_living":"## 🏠 Co-Living\n","experiential_travel":"## 🧭 Experiential Travel\n","retirement_living":"## 👴 Retirement Living & Care\n","tcm":"## 🌿 Traditional Chinese Medicine (TCM)\n","rehabilitation_care":"## 🏥 Rehabilitation Care\n"}
    for k, h in sections.items():
        r += h
        for i in all_data.get(k, []):
            r += f"- [{i['title']}]({i['url']})  — *{i.get('source','')}*\n"
            if i.get("summary"): r += f"  > {i['summary']}\n"
        r += "\n"
    r += f"\n---\n*Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}*"
    return r, all_data

def push_to_notion(report, all_data):
    token = _get_notion_token()
    if not token: return "⚠️ No Notion token available"
    today = datetime.now().strftime("%Y-%m-%d")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Notion-Version": "2022-06-28"}
    children = []
    sections = {"hotel_hospitality":"🏨 Hotel & Hospitality","tourism":"✈️ Tourism","co_living":"🏠 Co-Living","experiential_travel":"🧭 Experiential Travel","retirement_living":"👴 Retirement Living & Care","tcm":"🌿 Traditional Chinese Medicine (TCM)","rehabilitation_care":"🏥 Rehabilitation Care"}
    for k, st in sections.items():
        children.append({"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":st}}]}})
        for i in all_data.get(k, []):
            src = f" [{i.get('source','')}]" if i.get("source") else ""
            children.append({"object":"block","type":"bulleted_list_item","bulleted_list_item":{"rich_text":[{"type":"text","text":{"content":f"{i['title']}{src}","link":{"url":i["url"]} if i["url"] else None}}]}})
            if i.get("summary"): children.append({"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":i["summary"]}}]}})
    page_data = {"parent":{"database_id":NOTION_DATABASE_ID},"properties":{"Title":{"title":[{"text":{"content":f"Hospitality & Healthcare Trends — {today}"}}]},"Date":{"date":{"start":today}},"Category":{"select":{"name":"Market Research"}}},"children":children}
    try:
        req = urllib.request.Request("https://api.notion.com/v1/pages", data=json.dumps(page_data).encode(), headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=15)
        return f"✅ Notion page created: {json.loads(resp.read()).get('id','unknown')}"
    except Exception as e: return f"⚠️ Notion push failed: {e}"

def save_local(report):
    """Save the report locally, MERGING with an existing same-day file.

    Was mode "w": a second run on the same day, emptied by SeenUrls dedup,
    clobbered the first run's report. See hermes_trend_save for the evidence.
    """
    p = os.path.join(TRENDS_DIR, f"hospitality-trends-{datetime.now().strftime('%Y-%m-%d')}.md")
    return _save_report(p, _frontmatter(), report)

def main():
    report, all_data = build_report()
    total = sum(len(v) for v in all_data.values())
    result = push_to_notion(report, all_data) if USE_NOTION else save_local(report)
    save_local(report)
    _conform = _validate_report("hospitality-trends", report)
    health = {"timestamp":datetime.now().isoformat(),"total_articles":total,"categories":{k:len(v) for k,v in all_data.items()},"notion_result":result,"conformant":not _conform,"contract_issues":_conform}
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f: json.dump(health, f, indent=2)
    log_dir = os.path.expanduser("~/.hermes/logs"); os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "hospitality-trends.log"), "a") as f:
        f.write(f"{datetime.now().isoformat()} | articles={total} categories={len(all_data)} notion={'ok' if '✅' in result else 'fail'}\n")
    print(f"🏨 Hospitality & Healthcare Trends — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  📰 {total} articles across {len(all_data)} categories (multi-source)")
    for cat, items in all_data.items(): print(f"    {cat}: {len(items)} articles")
    print(f"  📤 {result}\n")
    for cat, items in all_data.items():
        if items:
            print(f"── {cat.replace('_',' ').title()} ──")
            for i in items[:3]:
                src = f" [{i.get('source','')}]" if i.get("source") else ""
                print(f"  • {i['title']}{src}")
                if i.get("summary"): print(f"    {i['summary'][:200].rsplit(' ',1)[0]}...")
            if len(items) > 3: print(f"    … and {len(items)-3} more")
            print()
    return 0

if __name__ == "__main__": main()
