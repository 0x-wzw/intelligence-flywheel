#!/usr/bin/env python3
"""
policy-tracker.py

Daily monitor for AI policy, regulation and geopolitical tech developments.

Feed selection note: the plan named White House OSTP, EU Commission, UK AISI,
China MIIT, Brookings, CSIS, RAND and Chatham House. Of those, only RAND has a
working public feed — OSTP, AISI, CSIS and Brookings were all probed on
2026-07-26 and are 404/403/malformed (recorded in thought-leaders.json
`_known_dead`). Coverage for the rest is obtained via Google News topic queries,
which is lower-fidelity but real. Swap in official feeds when they come back.

Schedule: Daily 6:00 AM
Output:   Knowledge/00-Raw/policy/YYYY-MM-DD-policy-tracker.md
Health:   System/policy-tracker-health.json
"""

import os
import re
import sys
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from hermes_feeds import (
    CYCLE,
    SeenStore,
    classify_status,
    fetch_rss,
    frontmatter_block,
    health_banner,
    is_recent,
    write_health,
)

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
OUTPUT_DIR = VAULT_PATH / "Knowledge" / "00-Raw" / "policy"
SEEN_FILE = Path.home() / ".hermes" / "policy-seen.json"

RECENCY_DAYS = 5

# ── Verified institutional feeds ──────────────────────────────────────
INSTITUTIONAL_FEEDS = {
    "Think Tanks & Research": [
        ("RAND Corporation", "https://www.rand.org/blog.xml", "policy_centrist"),
        ("Bruegel (EU)", "https://www.bruegel.org/rss.xml", "european_centrist"),
    ],
    "Regulatory & Legislative": [
        ("EU AI Act Newsletter", "https://artificialintelligenceact.eu/feed/", "eu_regulatory"),
    ],
    "Policy Journalism": [
        ("MIT Technology Review", "https://www.technologyreview.com/topic/artificial-intelligence/feed", "tech_journalism"),
        ("Platformer", "https://platformer.substack.com/feed", "platform_accountability"),
        ("Matt Stoller (BIG)", "https://mattstoller.substack.com/feed", "antitrust"),
    ],
}

# ── Google News fallback queries ──────────────────────────────────────
# Used for institutions with no working feed. Each query carries a perspective
# hint so downstream bias scoring is not blind to it.
NEWS_QUERIES = {
    "US Policy": [
        ('"White House" AI executive order OR OSTP artificial intelligence', "us_federal"),
        ("Congress AI legislation bill hearing", "us_legislative"),
        ("FTC OR DOJ antitrust artificial intelligence", "us_enforcement"),
    ],
    "EU & UK": [
        ("EU AI Act implementation enforcement", "eu_regulatory"),
        ('"AI Safety Institute" UK evaluation', "uk_safety"),
    ],
    "China & Geopolitics": [
        ("China AI regulation MIIT CAC artificial intelligence", "china_regulatory"),
        ("chip export controls semiconductor sanctions China", "geopolitical"),
        ("Taiwan TSMC semiconductor national security", "geopolitical"),
    ],
    "Global South & Multilateral": [
        ("UN OR UNESCO AI governance global south", "multilateral"),
        ("African Union OR ASEAN AI policy framework", "global_south"),
        ("India AI regulation policy framework", "india_regulatory"),
    ],
}

# Categorisation of captured items.
CATEGORY_RULES = [
    ("Regulatory Action", ["regulation", "act", "bill", "law", "rule", "compliance",
                            "enforcement", "ban", "mandate", "directive", "executive order"]),
    ("Geopolitical", ["export control", "sanction", "china", "taiwan", "chip", "semiconductor",
                       "national security", "tariff", "alliance", "treaty"]),
    ("Safety & Standards", ["safety institute", "evaluation", "standard", "audit", "red team",
                             "benchmark", "testing", "assurance", "framework"]),
    ("Antitrust & Competition", ["antitrust", "monopoly", "competition", "merger", "acquisition",
                                  "market power", "ftc", "doj"]),
]

FEED_ERRORS: Dict[str, str] = {}
FEEDS_ATTEMPTED: set = set()
# Entries returned before dedup/recency filtering — lets the health check tell
# "nothing new today" (fine) apart from "fetched nothing at all" (broken).
RAW_FETCHED = 0


def google_news(query: str, max_items: int = 6) -> List[Dict[str, Any]]:
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    global RAW_FETCHED
    FEEDS_ATTEMPTED.add(url)
    items, err = fetch_rss(url, max_items=max_items, timeout=20)
    if err:
        FEED_ERRORS[url] = err
        return []
    RAW_FETCHED += len(items)
    return items


def categorise(item: Dict[str, Any]) -> str:
    text = f"{item['title']} {item.get('summary', '')}".lower()
    for label, keywords in CATEGORY_RULES:
        if any(k in text for k in keywords):
            return label
    return "General Policy"


def collect() -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, List[Dict[str, Any]]]]:
    seen = SeenStore(SEEN_FILE, ttl_days=90)
    institutional: Dict[str, List[Dict[str, Any]]] = {}
    news: Dict[str, List[Dict[str, Any]]] = {}

    for section, feeds in INSTITUTIONAL_FEEDS.items():
        bucket = []
        for name, url, perspective in feeds:
            print(f"  {name}...")
            global RAW_FETCHED
            FEEDS_ATTEMPTED.add(url)
            items, err = fetch_rss(url, max_items=6, timeout=20)
            if err:
                FEED_ERRORS[url] = err
                print(f"  ⚠️  {name} — {err}")
                continue
            RAW_FETCHED += len(items)
            for item in items:
                if not item["url"] or seen.seen(item["title"], item["url"]):
                    continue
                if not is_recent(item, RECENCY_DAYS):
                    seen.add(item["title"], item["url"])
                    continue
                seen.add(item["title"], item["url"])
                item.update(institution=name, perspective=perspective,
                            category=categorise(item))
                bucket.append(item)
        if bucket:
            institutional[section] = bucket

    for region, queries in NEWS_QUERIES.items():
        bucket = []
        for query, perspective in queries:
            print(f"  news: {query[:50]}...")
            for item in google_news(query):
                if not item["url"] or seen.seen(item["title"], item["url"]):
                    continue
                if not is_recent(item, RECENCY_DAYS):
                    seen.add(item["title"], item["url"])
                    continue
                seen.add(item["title"], item["url"])
                item.update(institution="Google News", perspective=perspective,
                            category=categorise(item))
                bucket.append(item)
        if bucket:
            news[region] = bucket

    # Saved by the caller after the report is written (crash-safety).
    return institutional, news, seen


def generate_report(institutional, news) -> str:
    today = datetime.now()
    all_items = [i for v in institutional.values() for i in v] + \
                [i for v in news.values() for i in v]

    by_category: Dict[str, int] = {}
    for i in all_items:
        by_category[i["category"]] = by_category.get(i["category"], 0) + 1

    perspectives = sorted({i["perspective"] for i in all_items})

    fm = frontmatter_block(
        title=f"Policy Tracker — {today.strftime('%Y-%m-%d')}",
        tags=["raw", "policy", "regulation", "geopolitics", "intelligence", "daily-scan"],
        extra={
            "sources": "Institutional RSS + Google News topic queries",
            "items_captured": len(all_items),
            "feeds_failed": len(FEED_ERRORS),
            "perspectives": len(perspectives),
        },
    )

    report = fm + f"""
# 🏛️ Policy Tracker — {today.strftime('%A, %Y-%m-%d')}

**Generated:** {today.strftime('%H:%M')}
**Window:** last {RECENCY_DAYS} days
**Items captured:** {len(all_items)}

---

{health_banner(len(all_items), FEED_ERRORS, len(FEEDS_ATTEMPTED))}

---

## Summary by Category

| Category | Items |
|----------|-------|
"""
    for cat, n in sorted(by_category.items(), key=lambda x: -x[1]):
        report += f"| {cat} | {n} |\n"
    if not by_category:
        report += "| _nothing captured_ | 0 |\n"

    report += f"\n**Perspectives represented:** {', '.join(perspectives) or 'none'}\n"

    # ── Institutional sources ──
    report += "\n---\n\n## 📋 Institutional Sources\n\n"
    if institutional:
        for section, items in institutional.items():
            report += f"### {section}\n\n"
            for item in items:
                report += f"""#### {item['title']}

- **Source:** {item['institution']} · **Category:** {item['category']}
- **Published:** {(item['published'] or 'unknown')[:16]}
- **Link:** [{item['url'][:90]}]({item['url']})

"""
                if item.get("summary"):
                    report += f"> {item['summary'][:280]}…\n\n"
    else:
        report += "*No new institutional items.*\n"

    # ── News coverage ──
    report += "\n---\n\n## 📰 Policy News by Region\n\n"
    if news:
        for region, items in news.items():
            report += f"### {region}\n\n"
            for item in items[:8]:
                report += (
                    f"- **[{item['title'][:130]}]({item['url']})** "
                    f"— _{item['category']}_ · `{item['perspective']}`\n"
                )
            report += "\n"
    else:
        report += "*No news items captured.*\n"

    report += """---

## 🔍 Bias Note

Policy coverage is the axis most prone to single-perspective capture. This
scan deliberately queries US, EU/UK, China and Global South sources
separately so the weekly bias audit can see which regions are missing rather
than blending them into one undifferentiated feed.

Institutional feeds for OSTP, UK AISI, CSIS and Brookings are currently dead
(see `_known_dead` in `~/.hermes/thought-leaders.json`); those regions are
covered by news query only, at lower fidelity.

---

## Related

- [[System/Wiki/bias-detection|Bias Detection Framework]]
- [[System/Wiki/source-diversity|Source Diversity]]
- [[Knowledge/20-Reports/Intelligence-Cronjobs-Improvement-Plan|Intelligence Cronjobs Improvement Plan]]

---

*Generated by `policy-tracker.py` | Next run: Tomorrow 6:00 AM*
"""
    return report


def main():
    print(f"Starting policy tracker: {datetime.now().isoformat()}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    institutional, news, seen = collect()
    report = generate_report(institutional, news)

    output_file = OUTPUT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-policy-tracker.md"
    output_file.write_text(report)
    seen.save()  # only after the report is on disk

    total = sum(len(v) for v in institutional.values()) + sum(len(v) for v in news.values())
    status = classify_status(total, RAW_FETCHED, len(FEEDS_ATTEMPTED), len(FEED_ERRORS))
    write_health(
        "policy-tracker",
        {
            "items_captured": total,
            "institutional_sections": len(institutional),
            "news_regions": len(news),
            "feeds_attempted": len(FEEDS_ATTEMPTED),
            "feeds_failed": len(FEED_ERRORS),
            "raw_entries_fetched": RAW_FETCHED,
            "errors": FEED_ERRORS,
            "output": str(output_file),
            "status": status,
        },
    )

    print(f"✅ Policy tracker complete: {output_file}")
    print(f"   - Items: {total}")
    print(f"   - Feeds: {len(FEEDS_ATTEMPTED)} attempted, {len(FEED_ERRORS)} failed")
    if status == "no-new-items":
        print(f"   - ℹ️  Nothing new ({RAW_FETCHED} entries fetched, all already seen).")
    elif status != "ok":
        print(f"   - 🔴 {status} — check System/policy-tracker-health.json")

    # Write to cron log file for traceability
    log_dir = Path(os.path.expanduser("~/.hermes/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "policy-tracker.log"
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | items={total} feeds={len(FEEDS_ATTEMPTED)} failed={len(FEED_ERRORS)} status={status}\n")


if __name__ == "__main__":
    main()
