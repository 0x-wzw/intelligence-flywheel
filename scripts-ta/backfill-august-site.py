#!/usr/bin/env python3
"""backfill-august-site.py — rebuild the entire August intelligence on the site.

For every day in August that has vault trend reports, builds and pushes to KV:
  1. market-trends-YYYY-MM-DD + market-trends      (per-vertical reports)
  2. daily-insights-YYYY-MM-DD + daily-insights    (combined cross-vertical view)

Reads from Knowledge/00-Raw/trends/<vertical>-<date>.md. Skips days with no data.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

VAULT = Path("/Users/admin/Documents/Obsidian Vault")
TRENDS_DIR = VAULT / "Knowledge" / "00-Raw" / "trends"
CF_KV_ID = "07079ee6e9d44315b117d966fb9f8477"

# vertical file-stem → display label for the market-trends section
TREND_VERTICALS = {
    "market-trends": "👕 Fashion & Retail",
    "fnb-trends": "🍔 F&B & Consumer",
    "hospitality-trends": "🏨 Hospitality & Healthcare",
    "property-infra-energy-trends": "🏗️ Property/Infra/Energy",
    "csuite-leadership-trends": "💼 C-Suite Leadership",
    "ai-science-investment-trends": "🤖 AI/Science/Investment",
}

# vertical → combined daily-insights label (matches push-intelligence-kv CATEGORY_MAP)
INSIGHTS_VERTICALS = {
    "ai-science-investment-trends": "🔬 Research Papers",
    "market-intelligence": "📡 Market Intelligence",
    "market-trends": "📊 Market Trends",
    "csuite-leadership-trends": "👔 C-Suite Leadership",
    "fnb-trends": "🍔 F&B & Consumer",
    "hospitality-trends": "🏨 Hospitality & Healthcare",
    "property-infra-energy-trends": "🏗️ Property & Infrastructure",
}


def _cf_creds():
    env = {}
    for line in Path.home().joinpath(".hermes/.env").read_text().splitlines():
        line = line.strip()
        if line.startswith("CLOUDFLARE_ACCOUNT_ID="):
            env["acct"] = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("CLOUDFLARE_API_TOKEN="):
            env["tok"] = line.split("=", 1)[1].strip().strip('"')
    return env.get("acct", ""), env.get("tok", "")


def _kv_url(key):
    acct, _ = _cf_creds()
    return (f"https://api.cloudflare.com/client/v4/accounts/{acct}"
            f"/storage/kv/namespaces/{CF_KV_ID}/values/{key}")


def parse_report(path: Path, day: str):
    """Parse a per-vertical markdown report into {section: [articles]}."""
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.S)
    sections = {}
    current = "General"
    for line in text.splitlines():
        sm = re.match(r"^##\s+(.*)", line)
        if sm:
            current = sm.group(1).strip()
            sections.setdefault(current, [])
            continue
        am = re.match(r"^- \[([^\]]+)\]\((https?://[^\s\)]+)\)", line)
        if am:
            sections.setdefault(current, []).append({
                "title": am.group(1).strip(),
                "url": am.group(2),
                "date": day,
                "source": current,
                "summary": "",
            })
            continue
        if sections.get(current) and line.startswith("  > "):
            s = line[4:].strip()
            if sections[current][-1]["summary"]:
                sections[current][-1]["summary"] += " " + s
            else:
                sections[current][-1]["summary"] = s
    return sections


def build_market_trends(day: str):
    """Per-vertical market-trends payload (matches frontend loadMarketTrends)."""
    categories = []
    total = 0
    for stem, label in TREND_VERTICALS.items():
        path = TRENDS_DIR / f"{stem}-{day}.md"
        if not path.exists():
            continue
        secs = parse_report(path, day)
        for section_name, articles in secs.items():
            if not articles:
                continue
            categories.append({"name": f"{label} — {section_name}", "articles": articles})
            total += len(articles)
    return {
        "date": day,
        "generated_at": datetime.utcnow().isoformat(),
        "version": "2.0",
        "categories": categories,
        "summary": {"total_articles": total, "total_categories": len(categories)},
    }


def build_daily_insights(day: str):
    """Combined daily-insights payload (matches worker + push-intelligence-kv)."""
    categories = []
    total = 0
    for stem, label in INSIGHTS_VERTICALS.items():
        path = TRENDS_DIR / f"{stem}-{day}.md"
        if not path.exists():
            continue
        # For daily-insights, one category per vertical with all its articles.
        secs = parse_report(path, day)
        articles = [a for lst in secs.values() for a in lst]
        if articles:
            categories.append({"name": label, "articles": articles})
            total += len(articles)
    return {
        "date": day,
        "generated_at": datetime.utcnow().isoformat(),
        "version": "2.0",
        "categories": categories,
        "summary": {"total_articles": total, "total_categories": len(categories)},
    }


def kv_put(key, payload):
    acct, tok = _cf_creds()
    if not acct or not tok:
        print("  ERROR: missing CF creds")
        return False
    req = urllib.request.Request(
        _kv_url(key),
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            d = json.loads(resp.read())
            return bool(d.get("success"))
    except Exception as e:
        print(f"  put {key} failed: {e}")
        return False


def available_days():
    """All YYYY-MM-DD in August that have at least one trend report."""
    days = set()
    for stem in list(TREND_VERTICALS) + list(INSIGHTS_VERTICALS):
        for p in TRENDS_DIR.glob(f"{stem}-2026-08-*.md"):
            m = re.search(r"2026-08-\d{2}", p.name)
            if m:
                days.add(m.group(0))
    return sorted(days)


def main():
    days = available_days()
    print(f"📅 Found {len(days)} August days with vault data: {days}")

    for day in days:
        mt = build_market_trends(day)
        di = build_daily_insights(day)
        mt_arts = mt["summary"]["total_articles"]
        di_arts = di["summary"]["total_articles"]
        print(f"\n📆 {day}: market-trends={mt_arts} arts, daily-insights={di_arts} arts")

        mt_dated = kv_put(f"market-trends-{day}", mt)
        di_dated = kv_put(f"daily-insights-{day}", di)
        print(f"   market-trends-{day}: {mt_dated} | daily-insights-{day}: {di_dated}")

        # Also refresh the default/latest keys from the most recent day
        if day == days[-1]:
            kv_put("market-trends", mt)
            kv_put("daily-insights", di)
            print(f"   → refreshed default keys from {day}")

    print("\n✅ August backfill complete. Verify at https://intelligence.teoh.my/daily/2026-08-01")


if __name__ == "__main__":
    main()
