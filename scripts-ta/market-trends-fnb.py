#!/usr/bin/env python3
"""F&B & Consumer Market Trends — fetches latest trends in F&B, fast food, halal business,
loyalty & rewards, food delivery, food security, consumption patterns and technologies,
then pushes a structured report to a Notion database.

Runs daily at 01:00 via cron. Output is delivered to Telegram.
"""
import json
import os
import re
import html
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from dedup import SeenUrls
from multi_source_fetcher import fetch_with_summaries

NOTION_DATABASE_ID = "39e05e4a-7c41-817f-b6d7-ec9fc8b9099b"
VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "fnb-trends-health.json")
from hermes_feeds import frontmatter_block  # Schema v2 frontmatter, one definition
from trend_synthesizer import synthesize as _synthesize
from trend_contract import validate_report, report_path as _contract_report_path
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_trend_save import save_report as _save_report

def _frontmatter():
    """Schema v2 frontmatter for this report (see System/SCHEMA.md)."""
    return frontmatter_block(
        title=f"Market Trends — F&B & Consumer — {datetime.now():%Y-%m-%d}",
        tags=['raw', 'trends', 'fnb'],
        note_type="raw",
        domain="knowledge",
    ) + "\n"



def _get_notion_token():
    try:
        result = subprocess.run(["ps", "auxwww"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split("\n"):
            if "notion-mcp-server" in line and "NOTION_API_TOKEN=" in line:
                match = re.search(r"NOTION_API_TOKEN=(\S+)", line)
                if match:
                    t = match.group(1)
                    if "..." not in t and len(t) > 40:
                        return t
    except Exception:
        pass
    return ""


USE_NOTION = False  # intelligence flywheel (2026-10-08): raw captures are vault-only; Notion gets DISTILLED insights via intelligence-publish.py


def build_report():
    """Fetch all categories from multiple sources and build a structured markdown report."""
    seen = SeenUrls()
    seen.load()

    queries = {
        "fnb_fast_food": "food beverage fast food restaurant technology trends 2026",
        "halal_business": "halal business industry trends 2026 technology -restofworld.org -africanarguments.org",
        "loyalty_rewards": "loyalty rewards program technology trends 2026",
        "food_delivery": "food delivery online ordering technology trends 2026",
        "food_security": "food security supply chain technology trends 2026",
        "consumption_patterns": "food consumption consumer behavior trends 2026",
    }

    all_data = {}
    for category, query in queries.items():
        items = fetch_with_summaries(query, category_key=category, max_per_source=2, fetch_summaries=False, academic=False)
        # Additional domain filtering for noise sites
        if category == "halal_business":
            items = [item for item in items if "africanarguments.org" not in item.get("url", "") and 
                     "restofworld.org" not in item.get("url", "")]
        filtered_items = []
        for item in items:
            if item["url"] and seen.is_new(item["url"], item.get("date", ""), item.get("title", "")):
                filtered_items.append(item)
                seen.mark(item["url"], item.get("title", ""))
        all_data[category] = filtered_items
    seen.save()
    today = datetime.now().strftime("%Y-%m-%d")
    report = f"# F&B & Consumer Trends — {today}\n\n"

    # LLM analysis & synthesis based on today's items + recent daily reports
    _synth = _synthesize(
        "F&B & Consumer",
        all_data,
        recent_report_glob="fnb-trends-2026-*.md",
        fallback_report="fnb-trends",
    )
    if _synth:
        report += _synth + "\n\n"

    sections = {
        "fnb_fast_food": "## 🍔 F&B & Fast Food\n",
        "halal_business": "## 🕌 Halal Business\n",
        "loyalty_rewards": "## 🎯 Loyalty & Rewards\n",
        "food_delivery": "## 🛵 Food Delivery\n",
        "food_security": "## 🌾 Food Security\n",
        "consumption_patterns": "## 📊 Consumption Patterns\n",
    }

    for key, header in sections.items():
        report += header
        for item in all_data.get(key, []):
            report += f"- [{item['title']}]({item['url']})  — *{item.get('source', '')}*\n"
            if item.get("summary"):
                report += f"  > {item['summary']}\n"
        report += "\n"

    report += f"\n---\n*Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}*"
    return report, all_data


def push_to_notion(report, all_data):
    token = _get_notion_token()
    if not token:
        return "⚠️ No Notion token available"
    today = datetime.now().strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    children = []
    sections = {
        "fnb_fast_food": "🍔 F&B & Fast Food",
        "halal_business": "🕌 Halal Business",
        "loyalty_rewards": "🎯 Loyalty & Rewards",
        "food_delivery": "🛵 Food Delivery",
        "food_security": "🌾 Food Security",
        "consumption_patterns": "📊 Consumption Patterns",
    }
    for key, section_title in sections.items():
        children.append({"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"type": "text", "text": {"content": section_title}}]}})
        for item in all_data.get(key, []):
            src = f" [{item.get('source', '')}]" if item.get("source") else ""
            children.append({"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": f"{item['title']}{src}", "link": {"url": item["url"]} if item["url"] else None}}]}})
            if item.get("summary"):
                children.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": item["summary"]}}]}})
    page_data = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": {"Title": {"title": [{"text": {"content": f"F&B & Consumer Trends — {today}"}}]}, "Date": {"date": {"start": today}}, "Category": {"select": {"name": "Market Research"}}}, "children": children}
    try:
        req = urllib.request.Request("https://api.notion.com/v1/pages", data=json.dumps(page_data).encode(), headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        return f"✅ Notion page created: {result.get('id', 'unknown')}"
    except Exception as e:
        return f"⚠️ Notion push failed: {e}"


def save_local(report):
    """Save the report locally, MERGING with an existing same-day file.

    Was mode "w": a second run on the same day, emptied by SeenUrls dedup,
    clobbered the first run's report. See hermes_trend_save for the evidence.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(TRENDS_DIR, f"fnb-trends-{today}.md")
    return _save_report(path, _frontmatter(), report)

def main():
    report, all_data = build_report()
    total = sum(len(v) for v in all_data.values())
    if USE_NOTION:
        result = push_to_notion(report, all_data)
    else:
        result = save_local(report)
    save_local(report)
    # Contract validation — writes conformant flag into health so flywheel can see it
    _conform = validate_report("fnb-trends", report)
    health = {"timestamp": datetime.now().isoformat(), "total_articles": total, "categories": {k: len(v) for k, v in all_data.items()}, "notion_result": result, "conformant": not _conform, "contract_issues": _conform}
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f:
        json.dump(health, f, indent=2)
    log_dir = os.path.expanduser("~/.hermes/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "fnb-trends.log")
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | articles={total} categories={len(all_data)} notion={'ok' if '✅' in result else 'fail'}\n")
    print(f"🍔 F&B & Consumer Trends — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  📰 {total} articles across {len(all_data)} categories (multi-source)")
    for cat, items in all_data.items():
        print(f"    {cat}: {len(items)} articles")
    print(f"  📤 {result}")
    print()
    for cat, items in all_data.items():
        if items:
            print(f"── {cat.replace('_', ' ').title()} ──")
            for item in items[:3]:
                src = f" [{item.get('source', '')}]" if item.get("source") else ""
                print(f"  • {item['title']}{src}")
                if item.get("summary"):
                    print(f"    {item['summary'][:200].rsplit(' ', 1)[0]}...")
            if len(items) > 3:
                print(f"    … and {len(items) - 3} more")
            print()
    return 0


if __name__ == "__main__":
    main()
