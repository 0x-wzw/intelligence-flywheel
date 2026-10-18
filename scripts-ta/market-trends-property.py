#!/usr/bin/env python3
"""Property, Infrastructure & Energy Market Trends — fetches latest trends in property
development, construction materials, Gen Z purchasing, asset ownership, ASEAN transactions,
data centers, infrastructure projects, renewable energy, and technologies.
Runs daily at 04:00 via cron. Output is delivered to Telegram.
"""
import json, os, re, html, subprocess, sys, urllib.parse, urllib.request
from datetime import datetime
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path: sys.path.insert(0, _SCRIPTS_DIR)
from dedup import SeenUrls
from multi_source_fetcher import fetch_with_summaries

NOTION_DATABASE_ID = "39e05e4a-7c41-818c-aadc-fbda95247997"
VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "property-infra-energy-trends-health.json")
from hermes_feeds import frontmatter_block  # Schema v2 frontmatter, one definition
from trend_synthesizer import synthesize as _synthesize
from trend_contract import validate_report as _validate_report
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_trend_save import save_report as _save_report

def _frontmatter():
    """Schema v2 frontmatter for this report (see System/SCHEMA.md)."""
    return frontmatter_block(
        title=f"Market Trends — Property, Infra & Energy — {datetime.now():%Y-%m-%d}",
        tags=['raw', 'trends', 'property', 'infra', 'energy'],
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
        "property_development": "property development residential commercial industrial trends 2026",
        "construction_materials": "building construction materials technology trends 2026",
        "gen_z_purchasing": "gen z purchasing buying trends 2026",
        "property_asset_ownership": "property asset ownership trends 2026",
        "asean_transactions": "ASEAN property real estate transactions 2026",
        "data_centers": "data center construction investment trends 2026",
        "infrastructure_projects": "infrastructure projects development trends 2026",
        "renewable_energy": "renewable energy projects investment trends 2026",
    }
    all_data = {}
    for cat, q in queries.items():
        items = fetch_with_summaries(q, category_key=cat, max_per_source=2, fetch_summaries=False, academic=False)
        new = []
        for i in items:
            if i["url"] and seen.is_new(i["url"], i.get("date",""), i.get("title","")):
                new.append(i); seen.mark(i["url"], i.get("title",""))
        all_data[cat] = new
    seen.save()
    today = datetime.now().strftime("%Y-%m-%d")
    r = f"# Property, Infrastructure & Energy Trends — {today}\n\n"

    # LLM analysis & synthesis based on today's items + recent daily reports
    _synth = _synthesize(
        "Property, Infrastructure & Energy",
        all_data,
        recent_report_glob="property-infra-energy-trends-2026-*.md",
        fallback_report="property-infra-energy-trends",
    )
    if _synth:
        r += _synth + "\n\n"

    sections = {"property_development":"## 🏗️ Property Development\n","construction_materials":"## 🧱 Construction Materials\n","gen_z_purchasing":"## 👥 Gen Z Purchasing\n","property_asset_ownership":"## 🏠 Property & Asset Ownership\n","asean_transactions":"## 🌏 ASEAN Transactions\n","data_centers":"## 🖥️ Data Centers\n","infrastructure_projects":"## 🛣️ Infrastructure Projects\n","renewable_energy":"## ☀️ Renewable Energy\n"}
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
    sections = {"property_development":"🏗️ Property Development","construction_materials":"🧱 Construction Materials","gen_z_purchasing":"👥 Gen Z Purchasing","property_asset_ownership":"🏠 Property & Asset Ownership","asean_transactions":"🌏 ASEAN Transactions","data_centers":"🖥️ Data Centers","infrastructure_projects":"🛣️ Infrastructure Projects","renewable_energy":"☀️ Renewable Energy"}
    for k, st in sections.items():
        children.append({"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":st}}]}})
        for i in all_data.get(k, []):
            src = f" [{i.get('source','')}]" if i.get("source") else ""
            children.append({"object":"block","type":"bulleted_list_item","bulleted_list_item":{"rich_text":[{"type":"text","text":{"content":f"{i['title']}{src}","link":{"url":i["url"]} if i["url"] else None}}]}})
            if i.get("summary"): children.append({"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":i["summary"]}}]}})
    page_data = {"parent":{"database_id":NOTION_DATABASE_ID},"properties":{"Title":{"title":[{"text":{"content":f"Property, Infrastructure & Energy Trends — {today}"}}]},"Date":{"date":{"start":today}},"Category":{"select":{"name":"Market Research"}}},"children":children}
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
    p = os.path.join(TRENDS_DIR, f"property-infra-energy-trends-{datetime.now().strftime('%Y-%m-%d')}.md")
    return _save_report(p, _frontmatter(), report)

def main():
    report, all_data = build_report()
    total = sum(len(v) for v in all_data.values())
    result = push_to_notion(report, all_data) if USE_NOTION else save_local(report)
    save_local(report)
    _conform = _validate_report("property-infra-energy-trends", report)
    health = {"timestamp":datetime.now().isoformat(),"total_articles":total,"categories":{k:len(v) for k,v in all_data.items()},"notion_result":result,"conformant":not _conform,"contract_issues":_conform}
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f: json.dump(health, f, indent=2)
    log_dir = os.path.expanduser("~/.hermes/logs"); os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "property-infra-energy-trends.log"), "a") as f:
        f.write(f"{datetime.now().isoformat()} | articles={total} categories={len(all_data)} notion={'ok' if '✅' in result else 'fail'}\n")
    print(f"🏗️ Property, Infrastructure & Energy Trends — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
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
