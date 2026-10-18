#!/usr/bin/env python3
"""Backfill Cloudflare KV daily-insights-* keys from Notion Market Intelligence pages.

The frontend (intelligence.teoh.my) reads daily insights from Cloudflare KV, but KV
only has a subset of dates. Notion is the source of truth and has the full history.
This script reads every "Market Intelligence — YYYY-MM-DD" page from the Notion
database, reconstructs the KV JSON format, and writes it to KV so the trend chart
and historical navigation show complete data.
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime

NOTION_DB = "39e05e4a-7c41-81a3-815f-ff9c2e862cc2"
CF_ACCOUNT_ID = "fcd22318e9db7ef66952e112114cd8ae"
CF_KV_ID = "07079ee6e9d44315b117d966fb9f8477"


def _load_env_file(path=os.path.expanduser("~/.hermes/.env")):
    values = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                line = line[7:].strip() if line.startswith("export ") else line
                if "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return values


def get_notion_token():
    env = _load_env_file()
    for name in ("NOTION_API_TOKEN", "NOTION_API_KEY"):
        if env.get(name):
            return env[name]
    return ""


def get_cf_token():
    env = _load_env_file()
    for name in ("CF_API_TOKEN", "CLOUDFLARE_API_TOKEN"):
        t = env.get(name, "").strip()
        if t and not re.fullmatch(r"[0-9a-f]{32}", t):
            return t
    return ""


def notion_query(cursor=None):
    token = get_notion_token()
    body = {"page_size": 100}
    if cursor:
        body["start_cursor"] = cursor
    req = urllib.request.Request(
        "https://api.notion.com/v1/databases/%s/query" % NOTION_DB,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Notion-Version": "2022-06-28",
        },
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=20)
    return json.loads(resp.read())


def notion_blocks(page_id):
    token = get_notion_token()
    req = urllib.request.Request(
        "https://api.notion.com/v1/blocks/%s/children?page_size=100" % page_id,
        headers={"Authorization": "Bearer " + token, "Notion-Version": "2022-06-28"},
    )
    resp = urllib.request.urlopen(req, timeout=20)
    return json.loads(resp.read())


def kv_put(key, value):
    token = get_cf_token()
    url = "https://api.cloudflare.com/client/v4/accounts/%s/storage/kv/namespaces/%s/values/%s" % (
        CF_ACCOUNT_ID, CF_KV_ID, key)
    req = urllib.request.Request(
        url, data=json.dumps(value).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="PUT",
    )
    resp = urllib.request.urlopen(req, timeout=20)
    return json.loads(resp.read())


def page_to_insights(page_id, date):
    """Reconstruct KV insights JSON from a Notion Market Intelligence page."""
    data = notion_blocks(page_id)
    categories = []
    current_cat = None
    for b in data.get("results", []):
        t = b.get("type")
        if t not in ("heading_2", "bulleted_list_item", "paragraph"):
            continue
        rt = b.get(t, {}).get("rich_text", [])
        text = "".join(x.get("plain_text", "") for x in rt).strip()
        if not text:
            continue
        if t == "heading_2":
            if current_cat:
                categories.append(current_cat)
            current_cat = {"name": text, "articles": []}
        elif t == "bulleted_list_item" and current_cat:
            link = ""
            for x in rt:
                if x.get("href"):
                    link = x["href"]
            current_cat["articles"].append({
                "title": text,
                "url": link,
                "date": "",
                "source": "Notion",
            })
    if current_cat:
        categories.append(current_cat)

    total = sum(len(c.get("articles", [])) for c in categories)
    return {
        "date": date,
        "generated_at": datetime.now().isoformat(),
        "version": "2.0",
        "categories": categories,
        "summary": {
            "total_categories": len(categories),
            "total_articles": total,
            "total_skipped": 0,
            "sources": ["Notion"],
        },
    }


def merge_pages(pages, date):
    """Merge multiple Notion pages (verticals) for a date into one insights dict."""
    all_categories = []
    for pg in pages:
        try:
            insights = page_to_insights(pg["id"], date)
            all_categories.extend(insights["categories"])
        except Exception as e:
            print(f"    {date} page {pg['id']}: ERROR {e}")
    total = sum(len(c.get("articles", [])) for c in all_categories)
    return {
        "date": date,
        "generated_at": datetime.now().isoformat(),
        "version": "2.0",
        "categories": all_categories,
        "summary": {
            "total_categories": len(all_categories),
            "total_articles": total,
            "total_skipped": 0,
            "sources": ["Notion"],
        },
    }


def main():
    if not get_notion_token():
        print("No Notion token found")
        return 1
    if not get_cf_token():
        print("No Cloudflare token found")
        return 1

    # Collect all pages, split by type
    intel_pages = []
    trend_pages = []
    cursor = None
    while True:
        data = notion_query(cursor)
        for r in data.get("results", []):
            p = r.get("properties", {})
            title = "".join(t.get("plain_text", "") for t in p.get("Title", {}).get("title", []))
            date = ""
            if p.get("Date", {}).get("date"):
                date = p["Date"]["date"].get("start", "")
            if not date:
                continue
            if "Market Intelligence" in title:
                intel_pages.append({"id": r["id"], "date": date, "title": title})
            elif "Market Trends" in title:
                trend_pages.append({"id": r["id"], "date": date, "title": title})
        if data.get("has_more") and data.get("next_cursor"):
            cursor = data["next_cursor"]
        else:
            break

    # ── Market Intelligence → daily-insights-* ──
    by_date = {}
    for pg in intel_pages:
        if pg["date"] not in by_date:
            by_date[pg["date"]] = pg
    print(f"Found {len(by_date)} unique Market Intelligence dates in Notion")

    written = 0
    for date in sorted(by_date.keys()):
        pg = by_date[date]
        try:
            insights = page_to_insights(pg["id"], date)
            if not insights["categories"]:
                print(f"  {date}: no categories, skipping")
                continue
            kv_put("daily-insights-" + date, insights)
            print(f"  {date}: {insights['summary']['total_articles']} articles, {len(insights['categories'])} cats -> KV")
            written += 1
        except Exception as e:
            print(f"  {date}: ERROR {e}")

    # ── Market Trends → market-trends-* (merge all verticals per date) ──
    trends_by_date = {}
    for pg in trend_pages:
        trends_by_date.setdefault(pg["date"], []).append(pg)
    print(f"\nFound {len(trends_by_date)} Market Trends dates in Notion")

    trend_written = 0
    for date in sorted(trends_by_date.keys()):
        pages = trends_by_date[date]
        try:
            insights = merge_pages(pages, date)
            if not insights["categories"]:
                print(f"  {date}: no categories, skipping")
                continue
            kv_put("market-trends-" + date, insights)
            print(f"  {date}: {insights['summary']['total_articles']} articles, {len(insights['categories'])} cats (from {len(pages)} pages) -> KV")
            trend_written += 1
        except Exception as e:
            print(f"  {date}: ERROR {e}")

    print(f"\nDone. Wrote {written} daily-insights + {trend_written} market-trends dates to KV.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
