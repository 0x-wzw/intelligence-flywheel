#!/usr/bin/env python3
"""
Push daily intelligence from the local vault to Cloudflare KV for intelligence.teoh.my.

Reads the latest trend files from Knowledge/00-Raw/trends/, assembles them into
the daily-insights JSON structure the worker expects, and writes to KV via the
Cloudflare API.

Run daily after the trend crons finish (e.g. 06:00, after the last trend at 05:00).
"""

import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

VAULT = Path(os.environ.get("OBSIDIAN_VAULT_PATH", str(Path.home() / "Documents" / "Obsidian Vault")))
TRENDS_DIR = VAULT / "Knowledge" / "00-Raw" / "trends"

CF_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
CF_API_TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "")
KV_NAMESPACE_ID = "07079ee6e9d44315b117d966fb9f8477"

CATEGORY_MAP = {
    "ai-science-investment": "🔬 Research Papers",
    "market-intelligence": "📡 Market Intelligence",
    "market-trends": "📊 Market Trends",
    "csuite-leadership": "👔 C-Suite Leadership",
    "fnb": "🍔 F&B & Consumer",
    "hospitality": "🏨 Hospitality & Healthcare",
    "property-infra-energy": "🏗️ Property & Infrastructure",
}


def find_latest_trends() -> dict[str, Path]:
    """Find the latest trend file for each category."""
    today = date.today()
    latest: dict[str, Path] = {}

    for prefix in CATEGORY_MAP:
        # Try both naming conventions: prefix-trends-YYYY-MM-DD.md and prefix-YYYY-MM-DD.md
        for pattern in [f"{prefix}-trends-{today.isoformat()}.md", f"{prefix}-{today.isoformat()}.md"]:
            fpath = TRENDS_DIR / pattern
            if fpath.exists():
                latest[prefix] = fpath
                break

    return latest


def parse_trend_file(path: Path) -> list[dict[str, str]]:
    """Parse a trend markdown file into article entries."""
    text = path.read_text(encoding="utf-8", errors="replace")

    # Strip frontmatter
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)

    articles: list[dict[str, str]] = []
    lines = text.split("\n")
    current_section = "General"

    for line in lines:
        # Section headers: ## 🏷️ Section Name
        section_match = re.match(r"^##\s+(\S.*)", line)
        if section_match:
            current_section = section_match.group(1).strip()
            continue

        # Article lines: - [Title](url) — *source*
        article_match = re.match(r"^- \[([^\]]+)\]\(([^)]+)\)", line)
        if article_match:
            title = article_match.group(1)
            url = article_match.group(2)
            articles.append({
                "title": title,
                "url": url,
                "date": today.isoformat(),
                "source": current_section,
                "summary": "",
            })
            continue

        # Summary lines (continuation after article)
        if articles and line.startswith("  > "):
            summary = line[4:].strip()
            if summary:
                if articles[-1]["summary"]:
                    articles[-1]["summary"] += " " + summary
                else:
                    articles[-1]["summary"] = summary

    return articles


def build_payload(latest_files: dict[str, Path]) -> dict[str, Any]:
    """Build the daily-insights JSON payload."""
    categories = []
    total_articles = 0

    for prefix, fpath in latest_files.items():
        articles = parse_trend_file(fpath)
        if articles:
            categories.append({
                "name": CATEGORY_MAP[prefix],
                "articles": articles,
            })
            total_articles += len(articles)

    return {
        "date": today.isoformat(),
        "generated_at": datetime.utcnow().isoformat(),
        "version": "2.0",
        "categories": categories,
        "summary": {
            "total_articles": total_articles,
            "total_categories": len(categories),
        },
    }


def push_to_kv(key: str, payload: dict[str, Any]) -> bool:
    """Write a JSON payload to Cloudflare KV."""
    if not CF_API_TOKEN or not CF_ACCOUNT_ID:
        print("ERROR: CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID must be set", file=sys.stderr)
        return False

    import urllib.request

    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{KV_NAMESPACE_ID}/values/{key}"
    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }

    req = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            if result.get("success"):
                return True
            print(f"  KV API error: {result.get('errors', result)}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"  KV push failed: {e}", file=sys.stderr)
        return False


def main() -> None:
    global today
    today = date.today()

    if not CF_API_TOKEN:
        # Try loading from .env
        env_file = Path.home() / ".hermes" / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("CLOUDFLARE_API_TOKEN="):
                    globals()["CF_API_TOKEN"] = line.split("=", 1)[1].strip().strip('"')
                elif line.startswith("CLOUDFLARE_ACCOUNT_ID="):
                    globals()["CF_ACCOUNT_ID"] = line.split("=", 1)[1].strip().strip('"')

    if not CF_API_TOKEN:
        print("ERROR: CLOUDFLARE_API_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    print(f"📡 Scanning {TRENDS_DIR} for {today}...")
    latest = find_latest_trends()

    if not latest:
        print(f"  No trend files found for {today}. Trend crons may not have run yet.")
        sys.exit(0)

    print(f"  Found {len(latest)} categories:")
    for prefix, fpath in sorted(latest.items()):
        print(f"    {CATEGORY_MAP[prefix]}: {fpath.name}")

    payload = build_payload(latest)
    print(f"\n  Total articles: {payload['summary']['total_articles']}")
    print(f"  Total categories: {payload['summary']['total_categories']}")

    # Push today's insights
    key = "daily-insights"
    print(f"\n📤 Pushing to KV key '{key}'...")
    if push_to_kv(key, payload):
        print(f"  ✅ {key} updated")
    else:
        print(f"  ❌ {key} failed")
        sys.exit(1)

    # Also push a dated copy for the daily subpage navigation
    dated_key = f"daily-insights-{today.isoformat()}"
    print(f"📤 Pushing to KV key '{dated_key}'...")
    if push_to_kv(dated_key, payload):
        print(f"  ✅ {dated_key} updated")
    else:
        print(f"  ⚠️  {dated_key} failed (non-fatal)")

    print(f"\n✅ Done. Visit https://intelligence.teoh.my/ to verify.")


if __name__ == "__main__":
    main()
