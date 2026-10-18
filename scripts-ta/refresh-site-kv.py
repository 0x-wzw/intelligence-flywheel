#!/usr/bin/env python3
"""refresh-site-kv.py — push today's clean vault trend reports to Cloudflare KV.

The site (intelligence.teoh.my) serves two data sources:
  1. /daily-insights-<date>.json  → built by push-intelligence-kv.py (works)
  2. /market-trends-<date>.json   → NOT written by any script; stale since 08-07

This script assembles today's per-vertical market-trend reports (from the vault)
into the market-trends KV payload the worker expects, and pushes:
  - market-trends            (default/latest)
  - market-trends-YYYY-MM-DD (dated)

The payload shape matches the frontend loadMarketTrends(): { date, categories:
[{ name, articles: [{title,url,date,source,summary}] }] }.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

VAULT = Path("/Users/admin/Documents/Obsidian Vault")
TRENDS_DIR = VAULT / "Knowledge" / "00-Raw" / "trends"
CF_KV_ID = "07079ee6e9d44315b117d966fb9f8477"

# vertical file-stem → display label for the market-trends section
VERTICALS = {
    "market-trends": "👕 Fashion & Retail",
    "fnb-trends": "🍔 F&B & Consumer",
    "hospitality-trends": "🏨 Hospitality & Healthcare",
    "property-infra-energy-trends": "🏗️ Property/Infra/Energy",
    "csuite-leadership-trends": "💼 C-Suite Leadership",
    "ai-science-investment-trends": "🤖 AI/Science/Investment",
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
        # summary continuation
        if sections.get(current) and line.startswith("  > "):
            s = line[4:].strip()
            if sections[current][-1]["summary"]:
                sections[current][-1]["summary"] += " " + s
            else:
                sections[current][-1]["summary"] = s
    return sections


def build_payload(day: str):
    categories = []
    total = 0
    for stem, label in VERTICALS.items():
        path = TRENDS_DIR / f"{stem}-{day}.md"
        if not path.exists():
            continue
        secs = parse_report(path, day)
        for section_name, articles in secs.items():
            if not articles:
                continue
            categories.append({
                "name": f"{label} — {section_name}",
                "articles": articles,
            })
            total += len(articles)
    return {
        "date": day,
        "generated_at": __import__("datetime").datetime.utcnow().isoformat(),
        "version": "2.0",
        "categories": categories,
        "summary": {"total_articles": total, "total_categories": len(categories)},
    }


def kv_put(key, payload):
    acct, tok = _cf_creds()
    if not acct or not tok:
        print("ERROR: missing CF creds")
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


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    print(f"📡 Assembling market-trends payload for {day}...")
    payload = build_payload(day)
    print(f"  Categories: {payload['summary']['total_categories']}, "
          f"Articles: {payload['summary']['total_articles']}")
    if not payload["categories"]:
        print("  No categories found — aborting.")
        return

    ok_default = kv_put("market-trends", payload)
    ok_dated = kv_put(f"market-trends-{day}", payload)
    print(f"  ✅ market-trends (default): {ok_default}")
    print(f"  ✅ market-trends-{day}: {ok_dated}")
    print("Done. Check https://intelligence.teoh.my/ to verify.")


if __name__ == "__main__":
    main()
