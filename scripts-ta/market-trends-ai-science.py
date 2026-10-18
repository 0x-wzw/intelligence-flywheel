#!/usr/bin/env python3
"""AI, Science & Investment Market Trends — fetches latest trends in AI/ML, post-quantum
encryption, fintech/insurtech, data architectures, agentic AI, AGI, VC/PE investment,
science discoveries and published journals.
Runs daily at 05:00 via cron. Output is delivered to Telegram.
"""
import json, os, re, html, subprocess, sys, urllib.parse, urllib.request
from datetime import datetime
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path: sys.path.insert(0, _SCRIPTS_DIR)
from dedup import SeenUrls
from multi_source_fetcher import fetch_with_summaries

NOTION_DATABASE_ID = "39e05e4a-7c41-819a-8e4d-fae703866825"
VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "ai-science-investment-trends-health.json")
from hermes_feeds import frontmatter_block  # Schema v2 frontmatter, one definition
from trend_synthesizer import synthesize as _synthesize
from trend_contract import validate_report as _validate_report
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_trend_save import save_report as _save_report

def _frontmatter():
    """Schema v2 frontmatter for this report (see System/SCHEMA.md)."""
    return frontmatter_block(
        title=f"Market Trends — AI, Science & Investment — {datetime.now():%Y-%m-%d}",
        tags=['raw', 'trends', 'ai', 'science', 'investment'],
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
        "ai_ml": "artificial intelligence machine learning trends 2026",
        "post_quantum_encryption": "post quantum encryption cryptography trends 2026",
        "fintech_insurtech": "fintech insurtech technology trends 2026",
        "data_architectures": "data architecture lakehouse fabric trends 2026",
        "agentic_ai": "agentic AI autonomous agents trends 2026",
        "agi": "artificial general intelligence AGI research 2026",
        "vc_pe_investment": "venture capital private equity investment AI technology 2026",
        "science_discoveries": "latest science discoveries breakthroughs 2026",
    }
    all_data = {}
    for cat, q in queries.items():
        items = fetch_with_summaries(q, category_key=cat, max_per_source=2, fetch_summaries=False, academic=True)
        new = []
        for i in items:
            if i["url"] and seen.is_new(i["url"], i.get("date",""), i.get("title","")):
                new.append(i); seen.mark(i["url"], i.get("title",""))
        all_data[cat] = new
    seen.save()
    today = datetime.now().strftime("%Y-%m-%d")
    r = f"# AI, Science & Investment Trends — {today}\n\n"

    # LLM analysis & synthesis based on today's items + recent daily reports.
    # Market-size and market-trend items for the same vertical are analyzed
    # together (they are already grouped under one category per vertical).
    _synth = _synthesize(
        "AI, Science & Investment",
        all_data,
        recent_report_glob="ai-science-investment-trends-2026-*.md",
        fallback_report="ai-science-investment-trends",
    )
    if _synth:
        r += _synth + "\n\n"

    sections = {"ai_ml":"## 🤖 AI/ML\n","post_quantum_encryption":"## 🔐 Post-Quantum Encryption\n","fintech_insurtech":"## 💰 Fintech & Insurtech\n","data_architectures":"## 🗄️ Data Architectures\n","agentic_ai":"## 🧠 Agentic AI\n","agi":"## 🌌 AGI\n","vc_pe_investment":"## 📈 VC/PE Investment\n","science_discoveries":"## 🔬 Science Discoveries\n"}
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
    sections = {"ai_ml":"🤖 AI/ML","post_quantum_encryption":"🔐 Post-Quantum Encryption","fintech_insurtech":"💰 Fintech & Insurtech","data_architectures":"🗄️ Data Architectures","agentic_ai":"🧠 Agentic AI","agi":"🌌 AGI","vc_pe_investment":"📈 VC/PE Investment","science_discoveries":"🔬 Science Discoveries"}
    for k, st in sections.items():
        children.append({"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":st}}]}})
        for i in all_data.get(k, []):
            src = f" [{i.get('source','')}]" if i.get("source") else ""
            children.append({"object":"block","type":"bulleted_list_item","bulleted_list_item":{"rich_text":[{"type":"text","text":{"content":f"{i['title']}{src}","link":{"url":i["url"]} if i["url"] else None}}]}})
            if i.get("summary"): children.append({"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":i["summary"]}}]}})
    page_data = {"parent":{"database_id":NOTION_DATABASE_ID},"properties":{"Title":{"title":[{"text":{"content":f"AI, Science & Investment Trends — {today}"}}]},"Date":{"date":{"start":today}},"Category":{"select":{"name":"Market Research"}}},"children":children}
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
    p = os.path.join(TRENDS_DIR, f"ai-science-investment-trends-{datetime.now().strftime('%Y-%m-%d')}.md")
    return _save_report(p, _frontmatter(), report)

def main():
    report, all_data = build_report()
    total = sum(len(v) for v in all_data.values())
    result = push_to_notion(report, all_data) if USE_NOTION else save_local(report)
    save_local(report)
    _conform = _validate_report("ai-science-investment-trends", report)
    health = {"timestamp":datetime.now().isoformat(),"total_articles":total,"categories":{k:len(v) for k,v in all_data.items()},"notion_result":result,"conformant":not _conform,"contract_issues":_conform}
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f: json.dump(health, f, indent=2)
    log_dir = os.path.expanduser("~/.hermes/logs"); os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "ai-science-trends.log"), "a") as f:
        f.write(f"{datetime.now().isoformat()} | articles={total} categories={len(all_data)} notion={'ok' if '✅' in result else 'fail'}\n")
    print(f"🤖 AI, Science & Investment Trends — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
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
