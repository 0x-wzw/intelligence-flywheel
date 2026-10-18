#!/usr/bin/env python3
"""Market Trends Daily — fetches latest trends in fashion, retail, supermarkets & technology,
then pushes a structured report to a Notion page.

Runs daily at 03:00 via cron. Output is delivered to Telegram.
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

# Ensure Hermes scripts directory is on the path
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ── Deduplication ──────────────────────────────────────────────────────
from dedup import SeenUrls
from multi_source_fetcher import fetch_with_summaries, fetch_article_summary

# ── Config ────────────────────────────────────────────────────────────
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DB_ID", "39e05e4a-7c41-81a3-815f-ff9c2e862cc2")
VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "market-trends-health.json")
from hermes_feeds import frontmatter_block  # Schema v2 frontmatter, one definition
from trend_synthesizer import synthesize as _synthesize
from trend_contract import validate_report as _validate_report
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_trend_save import save_report as _save_report

def _frontmatter():
    """Schema v2 frontmatter for this report (see System/SCHEMA.md)."""
    return frontmatter_block(
        title=f"Market Trends — Fashion & Retail — {datetime.now():%Y-%m-%d}",
        tags=['raw', 'trends', 'market-research'],
        note_type="raw",
        domain="knowledge",
    ) + "\n"



def _get_notion_token():
    """Get the real Notion token from the running MCP server process."""
    token = NOTION_TOKEN
    if token and "..." not in token and len(token) > 40:
        return token
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
        "fashion_fast_fashion": "fashion fast fashion trends 2026 technology fabric",
        "performance_athleisure": "performance wear athleisure activewear technical fabrics sportswear 2026",
        "retail": "retail technology AI automation trends 2026",
        "ecommerce": "ecommerce retail technology trends 2026",
        "sustainability": "fashion retail sustainability circular economy 2026",
    }

    all_data = {}
    for category, query in queries.items():
        # Fetch from Google News + HN + Reddit + RSS feeds
        items = fetch_with_summaries(query, category_key=category, max_per_source=2, fetch_summaries=False, academic=False)
        new_items = []
        for item in items:
            if item["url"] and seen.is_new(item["url"], item.get("date", ""), item.get("title", "")):
                new_items.append(item)
                seen.mark(item["url"], item.get("title", ""))
        all_data[category] = new_items

    seen.save()

    today = datetime.now().strftime("%Y-%m-%d")
    report = f"# Market Trends Report — {today}\n\n"

    # LLM analysis & synthesis based on today's items + recent daily reports.
    # Market-size and market-trend items share the same category per vertical.
    _synth = _synthesize(
        "Fashion & Retail",
        all_data,
        recent_report_glob="market-trends-2026-*.md",
        fallback_report="market-trends",
    )
    if _synth:
        # Wrap the LLM output in the canonical synthesis heading from the
        # vertical contract (trend_contract.SYNTH_HEADING) so validate_report()
        # always finds the block — even if the model omits the exact heading.
        # Strip any heading the model did emit to avoid nested/duplicate headings.
        from trend_contract import SYNTH_HEADING
        _synth_body = _synth.strip()
        if _synth_body.startswith("#"):
            _synth_body = "\n".join(
                ln for ln in _synth_body.splitlines() if not ln.lstrip().startswith("#")
            ).strip()
        report += SYNTH_HEADING + "\n\n" + _synth_body + "\n\n"

    sections = {
        "fashion_fast_fashion": "## 👕 Fashion & Fast Fashion\n",
        "performance_athleisure": "## 🏃 Performance Wear & Athleisure\n",
        "retail": "## 🏪 Retail Technology\n",
        "ecommerce": "## 📦 E-Commerce\n",
        "sustainability": "## 🌱 Sustainability & Circular Economy\n",
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
    """Push the report to a Notion database page using the Notion API."""
    token = _get_notion_token()
    if not token:
        return "⚠️ No Notion token available"

    today = datetime.now().strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    page_data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Title": {"title": [{"text": {"content": f"Market Trends — {today}"}}]},
            "Date": {"date": {"start": today}},
            "Category": {"select": {"name": "Market Research"}},
        },
        "children": [],
    }

    sections = {
        "fashion_fast_fashion": "👕 Fashion & Fast Fashion",
        "performance_athleisure": "🏃 Performance Wear & Athleisure",
        "retail": "🏪 Retail Technology",
        "ecommerce": "📦 E-Commerce",
        "sustainability": "🌱 Sustainability & Circular Economy",
    }

    for key, section_title in sections.items():
        page_data["children"].append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": section_title}}]
            },
        })
        for item in all_data.get(key, []):
            source_tag = f" [{item.get('source', '')}]" if item.get("source") else ""
            page_data["children"].append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"{item['title']}{source_tag}",
                                "link": {"url": item["url"]} if item["url"] else None,
                            },
                        }
                    ]
                },
            })
            if item.get("summary"):
                page_data["children"].append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": item["summary"]}}]
                    },
                })

    try:
        req = urllib.request.Request(
            "https://api.notion.com/v1/pages",
            data=json.dumps(page_data).encode(),
            headers=headers,
            method="POST",
        )
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
    path = os.path.join(TRENDS_DIR, f"market-trends-{today}.md")
    return _save_report(path, _frontmatter(), report)

def main():
    report, all_data = build_report()

    total = sum(len(v) for v in all_data.values())

    if USE_NOTION:
        result = push_to_notion(report, all_data)
    else:
        result = save_local(report)

    local_result = save_local(report)

    _conform = _validate_report("market-trends", report)

    health = {
        "timestamp": datetime.now().isoformat(),
        "total_articles": total,
        "categories": {k: len(v) for k, v in all_data.items()},
        "notion_result": result,
        "conformant": not _conform,
        "contract_issues": _conform,
    }
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f:
        json.dump(health, f, indent=2)

    log_dir = os.path.expanduser("~/.hermes/logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "market-trends.log")
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | articles={total} categories={len(all_data)} notion={'ok' if '✅' in result else 'fail'}\n")

    # Telegram output with insights
    print(f"📊 Market Trends — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
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
                    summary = item["summary"][:200].rsplit(" ", 1)[0] + "..."
                    print(f"    {summary}")
            if len(items) > 3:
                print(f"    … and {len(items) - 3} more")
            print()

    return 0


if __name__ == "__main__":
    main()
