#!/usr/bin/env python3
"""
thought-leader-scan.py

Daily scan of thought leaders across AI, business, economics, and policy.
Aggregates tweets, blog posts, Substack essays from curated lists.
Outputs structured markdown with perspective tagging for bias detection.

Schedule: Daily 5:00 AM
Output: Knowledge/00-Raw/thought-leaders/YYYY-MM-DD-thought-leader-scan.md
"""

import os
import re
import sys
import json
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Shared hardened feed layer (see hermes_feeds.py for why this exists)
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from hermes_feeds import (
    CYCLE,
    authorship_tag,
    SeenStore,
    classify_status,
    fetch_rss as _fetch_rss,
    health_banner,
    is_recent,
    write_health,
)

# ── Config ─────────────────────────────────────────────────────────────
VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
OUTPUT_DIR = VAULT_PATH / "Knowledge" / "00-Raw" / "thought-leaders"
CONFIG_FILE = Path.home() / ".hermes" / "thought-leaders.json"
SEEN_FILE = Path.home() / ".hermes" / "thought-leaders-seen.json"

# Only surface items published in this window; prevents a feed's back catalogue
# from flooding the first successful run after an outage.
RECENCY_DAYS = 14

# Feed URLs that failed this run: {url: error}. Surfaced in the report.
FEED_ERRORS: Dict[str, str] = {}
FEEDS_ATTEMPTED: set = set()
# Total entries returned by feeds BEFORE dedup/recency filtering. Distinguishes
# "nothing new today" (healthy) from "fetched nothing at all" (broken).
RAW_FETCHED = 0

# ── Thought Leader Configuration ──────────────────────────────────────
# Loaded from CONFIG_FILE at startup (see load_config below). The dicts here are
# only the fallback used when the JSON is missing or unreadable.
#
# NOTE: every URL in the config was probe-verified on 2026-07-26. The list that
# shipped with P0 had 49 of 77 feeds dead. Probe before adding.
FALLBACK_THOUGHT_LEADERS = {
    "ai_safety": {
        "label": "AI Safety",
        "leaders": [
            {
                "name": "Eliezer Yudkowsky",
                "org": "MIRI",
                "perspective": "safety_first",
                "twitter": "@ESYudkowsky",
                "blog": "https://www.lesswrong.com/users/eliezer-yudkowsky/posts.rss",
                "substack": None,
            },
            {
                "name": "Dario Amodei",
                "org": "Anthropic",
                "perspective": "safety_first",
                "twitter": "@DarioAmodei",
                "blog": "https://www.anthropic.com/rss",
                "substack": None,
            },
            {
                "name": "Timnit Gebru",
                "org": "DAIR",
                "perspective": "safety_ethics",
                "twitter": "@timnitGebru",
                "blog": "https://www.dair-institute.org/rss",
                "substack": None,
            },
            {
                "name": "Paul Christiano",
                "org": "ARC",
                "perspective": "alignment",
                "twitter": "@paulfchristiano",
                "blog": "https://ai-alignment.com/feed",
                "substack": "https://paulfchristiano.substack.com/feed",
            },
        ]
    },
    "ai_acceleration": {
        "label": "AI Acceleration",
        "leaders": [
            {
                "name": "Sam Altman",
                "org": "OpenAI",
                "perspective": "acceleration",
                "twitter": "@sama",
                "blog": "https://openai.com/rss",
                "substack": "https://blog.samaltman.com/rss",
            },
            {
                "name": "Andrew Ng",
                "org": "DeepLearning.AI",
                "perspective": "pragmatic_adoption",
                "twitter": "@AndrewYNg",
                "blog": "https://www.deeplearning.ai/the-batch/rss",
                "substack": "https://andrewng.substack.com/feed",
            },
            {
                "name": "Fei-Fei Li",
                "org": "Stanford HAI",
                "perspective": "human_centered",
                "twitter": "@drfeifei",
                "blog": "https://hai.stanford.edu/news/rss.xml",
                "substack": None,
            },
            {
                "name": "Yann LeCun",
                "org": "Meta AI",
                "perspective": "open_source_skeptic",
                "twitter": "@ylecun",
                "blog": "https://ai.meta.com/blog/rss",
                "substack": None,
            },
        ]
    },
    "ai_skeptics": {
        "label": "AI Skeptics",
        "leaders": [
            {
                "name": "Gary Marcus",
                "org": "NYU / Author",
                "perspective": "cognitive_skeptic",
                "twitter": "@GaryMarcus",
                "blog": "https://garymarcus.substack.com/feed",
                "substack": "https://garymarcus.substack.com/feed",
            },
            {
                "name": "Rodney Brooks",
                "org": "MIT / Rethink Robotics",
                "perspective": "robotics_skeptic",
                "twitter": "@rodneyabrooks",
                "blog": "https://rodneybrooks.com/feed",
                "substack": None,
            },
            {
                "name": "Erik J. Larson",
                "org": "Author",
                "perspective": "agi_skeptic",
                "twitter": "@ErikJLarson2",
                "blog": None,
                "substack": None,
            },
        ]
    },
    "business_strategy": {
        "label": "Business & Strategy",
        "leaders": [
            {
                "name": "Ben Horowitz",
                "org": "a16z",
                "perspective": "venture_optimist",
                "twitter": "@bhorowitz",
                "blog": "https://a16z.com/feed",
                "substack": None,
            },
            {
                "name": "Naval Ravikant",
                "org": "AngelList",
                "perspective": "leverage_philosopher",
                "twitter": "@naval",
                "blog": "https://www.navalmanack.com/rss",
                "substack": None,
            },
            {
                "name": "Reid Hoffman",
                "org": "LinkedIn / Greylock",
                "perspective": "network_effects",
                "twitter": "@reidhoffman",
                "blog": "https://www.linkedin.com/blog/rss",
                "substack": "https://reidhoffman.substack.com/feed",
            },
            {
                "name": "Rita Gunther McGrath",
                "org": "Columbia Business School",
                "perspective": "strategy_realist",
                "twitter": "@RitaGMcGrath",
                "blog": "https://ritagmcgrath.com/feed",
                "substack": None,
            },
        ]
    },
    "economics_policy": {
        "label": "Economics & Policy",
        "leaders": [
            {
                "name": "Paul Krugman",
                "org": "NYT / CUNY",
                "perspective": "progressive_economics",
                "twitter": "@paulkrugman",
                "blog": "https://krugman.blogs.nytimes.com/feed",
                "substack": None,
            },
            {
                "name": "Tyler Cowen",
                "org": "GMU / Marginal Revolution",
                "perspective": "market_optimist",
                "twitter": "@tylercowen",
                "blog": "https://marginalrevolution.com/marginalrevolution/feed",
                "substack": None,
            },
            {
                "name": "Ray Dalio",
                "org": "Bridgewater",
                "perspective": "macro_investor",
                "twitter": "@RayDalio",
                "blog": "https://www.linkedin.com/in/ray-dalio/rss",
                "substack": None,
            },
            {
                "name": "Esther Duflo",
                "org": "MIT / J-PAL",
                "perspective": "development_economics",
                "twitter": "@esther_duflo",
                "blog": "https://www.povertyactionlab.org/rss",
                "substack": None,
            },
        ]
    },
    "tech_policy": {
        "label": "Tech Policy",
        "leaders": [
            {
                "name": "Casey Newton",
                "org": "Platformer",
                "perspective": "platform_accountability",
                "twitter": "@CaseyNewton",
                "blog": None,
                "substack": "https://platformer.substack.com/feed",
            },
            {
                "name": "Ben Thompson",
                "org": "Stratechery",
                "perspective": "tech_strategy",
                "twitter": "@benthompson",
                "blog": None,
                "substack": "https://stratechery.com/feed",
            },
            {
                "name": "Matt Stoller",
                "org": "American Economic Liberties Project",
                "perspective": "antitrust",
                "twitter": "@matthewstoller",
                "blog": None,
                "substack": "https://mattstoller.substack.com/feed",
            },
        ]
    },
}

# ── RSS Feed Aggregation ─────────────────────────────────────────────
FALLBACK_GENERAL_RSS_FEEDS = {
    "ai_labs": [
        "https://openai.com/rss",
        "https://www.anthropic.com/rss",
        "https://ai.googleblog.com/feeds/posts/default",
        "https://ai.meta.com/blog/rss",
        "https://deepmind.google/discover/blog/rss",
    ],
    "ai_research": [
        "https://www.lesswrong.com/feed.xml",
        "https://ai-alignment.com/feed",
        "https://gwern.net/doc/ai/index.rss",
    ],
    "business_strategy": [
        "https://stratechery.com/feed",
        "https://a16z.com/feed",
        "https://www.nfx.com/post/rss",
        "https://continuations.com/rss",
    ],
    "economics_policy": [
        "https://www.brookings.edu/feed",
        "https://www.csis.org/analysis/rss",
        "https://www.rand.org/blog/rss",
        "https://www.imf.org/en/news/rss",
    ],
    "newsletters": [
        "https://platformer.substack.com/feed",
        "https://import.substack.com/feed",
        "https://moneystuff.substack.com/feed",
        "https://garymarcus.substack.com/feed",
    ],
}


# ── Config loading ────────────────────────────────────────────────────
def load_config() -> Tuple[Dict[str, Any], Dict[str, List[str]]]:
    """
    Load the feed config from thought-leaders.json.

    The JSON is the single source of truth. Previously this file existed with a
    SECOND, divergent copy of the feed list that the script never read — both
    copies had rotted independently.
    """
    if not CONFIG_FILE.exists():
        print(f"⚠️  {CONFIG_FILE} missing — using built-in fallback feed list")
        return FALLBACK_THOUGHT_LEADERS, FALLBACK_GENERAL_RSS_FEEDS

    try:
        cfg = json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  {CONFIG_FILE} unreadable ({e}) — using built-in fallback feed list")
        return FALLBACK_THOUGHT_LEADERS, FALLBACK_GENERAL_RSS_FEEDS

    leaders = cfg.get("thought_leaders") or FALLBACK_THOUGHT_LEADERS
    general = cfg.get("general_feeds") or FALLBACK_GENERAL_RSS_FEEDS

    # Normalise: config may use "rss" as a single-feed shorthand.
    for domain in leaders.values():
        domain.setdefault("label", "Unlabelled")
        for leader in domain.get("leaders", []):
            if leader.get("rss") and not leader.get("blog"):
                leader["blog"] = leader["rss"]

    verified = cfg.get("_verified", "unknown")
    n_feeds = sum(
        1
        for d in leaders.values()
        for l in d.get("leaders", [])
        for k in ("blog", "substack")
        if l.get(k)
    )
    print(f"  Config loaded: {n_feeds} leader feeds (verified {verified})")
    return leaders, general


# ── Helper Functions ──────────────────────────────────────────────────
def fetch_rss(url: str, max_items: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch an RSS feed via the shared hardened fetcher.

    Failures are recorded in FEED_ERRORS and returned as an empty list — they are
    NOT turned into fake items with blank URLs (the old behaviour, which made
    every failure indistinguishable from "no new posts").
    """
    global RAW_FETCHED
    FEEDS_ATTEMPTED.add(url)
    items, err = _fetch_rss(url, max_items=max_items)
    if err:
        FEED_ERRORS[url] = err
        print(f"  ⚠️  {url} — {err}")
        return []
    RAW_FETCHED += len(items)
    return items


def calculate_diversity_score(items: List[Dict[str, Any]]) -> float:
    """
    Calculate viewpoint diversity score (0-10).
    Based on representation of different perspectives.
    """
    if not items:
        return 0.0
    
    perspectives = {}
    for item in items:
        perspective = item.get("perspective", "unknown")
        perspectives[perspective] = perspectives.get(perspective, 0) + 1
    
    # Score based on number of unique perspectives and balance
    unique_count = len(perspectives)
    max_count = max(perspectives.values()) if perspectives else 0
    min_count = min(perspectives.values()) if perspectives else 0
    
    # Base score for unique perspectives (max 6 domains)
    domain_score = min(unique_count / 6, 1.0) * 6
    
    # Balance score (how evenly distributed)
    if max_count > 0:
        balance_score = (1 - (max_count - min_count) / max_count) * 4
    else:
        balance_score = 0
    
    return round(domain_score + balance_score, 1)


# ── Main Scan Functions ───────────────────────────────────────────────
def scan_thought_leaders(seen: SeenStore, thought_leaders: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """Scan all thought leader RSS feeds, tagging each item with its perspective."""
    results: Dict[str, List[Dict[str, Any]]] = {}

    for domain, config in thought_leaders.items():
        domain_items: List[Dict[str, Any]] = []
        for leader in config.get("leaders", []):
            for source_type, key, cap in (("blog", "blog", 3), ("substack", "substack", 2)):
                feed_url = leader.get(key)
                if not feed_url:
                    continue
                for item in fetch_rss(feed_url, max_items=cap):
                    if not item["url"] or seen.seen(item["title"], item["url"]):
                        continue
                    if not is_recent(item, RECENCY_DAYS):
                        # Still mark seen so the back catalogue is not re-evaluated daily.
                        seen.add(item["title"], item["url"])
                        continue
                    item.update(
                        leader=leader["name"],
                        org=leader["org"],
                        perspective=leader["perspective"],
                        domain=config["label"],
                        source_type=source_type,
                    )
                    seen.add(item["title"], item["url"])
                    domain_items.append(item)

        if domain_items:
            results[domain] = domain_items

    return results


def scan_general_feeds(seen: SeenStore, general_feeds: Dict[str, List[str]]) -> Dict[str, List[Dict[str, Any]]]:
    """Scan general RSS feeds by category."""
    results: Dict[str, List[Dict[str, Any]]] = {}

    for category, feeds in general_feeds.items():
        category_items: List[Dict[str, Any]] = []
        for feed_url in feeds:
            for item in fetch_rss(feed_url, max_items=5):
                if not item["url"] or seen.seen(item["title"], item["url"]):
                    continue
                if not is_recent(item, RECENCY_DAYS):
                    seen.add(item["title"], item["url"])
                    continue
                item["category"] = category
                seen.add(item["title"], item["url"])
                category_items.append(item)

        if category_items:
            results[category] = category_items

    return results


# ── Report Generation ─────────────────────────────────────────────────
def generate_report(
    leader_scans: Dict[str, List[Dict[str, Any]]],
    general_scans: Dict[str, List[Dict[str, Any]]],
) -> str:
    """Generate markdown report."""
    today = datetime.now()
    all_items = []
    for items in leader_scans.values():
        all_items.extend(items)
    
    diversity_score = calculate_diversity_score(all_items)
    
    # Count perspectives
    perspective_counts = {}
    for item in all_items:
        p = item.get("perspective", "unknown")
        perspective_counts[p] = perspective_counts.get(p, 0) + 1
    
    report = f"""---
title: Thought Leader Scan — {today.strftime('%Y-%m-%d')}
created: {today.strftime('%Y-%m-%d')}
type: raw
domain: knowledge
status: unprocessed
cycle: {CYCLE}
tags: [raw, thought-leaders, intelligence, daily-scan, {authorship_tag()}]
sources: RSS feeds, Substack, blogs
viewpoint_diversity_score: {diversity_score}
feeds_attempted: {len(FEEDS_ATTEMPTED)}
feeds_failed: {len(FEED_ERRORS)}
items_captured: {len(all_items)}
---

# 🧠 Thought Leader Scan — {today.strftime('%A, %Y-%m-%d')}

**Generated:** {today.strftime('%H:%M')}
**Next scan:** Tomorrow 5:00 AM
**Viewpoint Diversity Score:** {diversity_score}/10 {'✅ Good' if diversity_score >= 7 else '⚠️ Needs improvement' if diversity_score >= 5 else '🔴 Poor'}

---

{health_banner(len(all_items), FEED_ERRORS, len(FEEDS_ATTEMPTED))}

---

## Summary

| Domain | Items | Perspectives |
|--------|-------|--------------|
"""
    
    for domain, items in leader_scans.items():
        domain_perspectives = set(item.get("perspective") for item in items)
        report += f"| {domain} | {len(items)} | {len(domain_perspectives)} |\n"
    
    report += f"""
**Total new items:** {len(all_items)}  
**Perspectives represented:** {', '.join(perspective_counts.keys()) or 'None'}

---

## AI Safety Camp

"""
    if leader_scans.get("ai_safety"):
        for item in leader_scans["ai_safety"]:
            report += f"""### {item['leader']} ({item['org']})
**{item['title']}**  
📎 [{item['source']}]({item['url']})  
📅 {item['published'][:10] if item['published'] else 'Unknown'}

{item['summary'][:300]}...

---

"""
    else:
        report += "*No new items today.*\n\n---\n\n"
    
    report += """## AI Acceleration Camp

"""
    if leader_scans.get("ai_acceleration"):
        for item in leader_scans["ai_acceleration"]:
            report += f"""### {item['leader']} ({item['org']})
**{item['title']}**  
📎 [{item['source']}]({item['url']})  
📅 {item['published'][:10] if item['published'] else 'Unknown'}

{item['summary'][:300]}...

---

"""
    else:
        report += "*No new items today.*\n\n---\n\n"
    
    report += """## AI Skeptics

"""
    if leader_scans.get("ai_skeptics"):
        for item in leader_scans["ai_skeptics"]:
            report += f"""### {item['leader']} ({item['org']})
**{item['title']}**  
📎 [{item['source']}]({item['url']})  
📅 {item['published'][:10] if item['published'] else 'Unknown'}

{item['summary'][:300]}...

---

"""
    else:
        report += "*No new items today.*\n\n---\n\n"
    
    report += """## Business & Strategy

"""
    if leader_scans.get("business_strategy"):
        for item in leader_scans["business_strategy"]:
            report += f"""### {item['leader']} ({item['org']})
**{item['title']}**  
📎 [{item['source']}]({item['url']})  
📅 {item['published'][:10] if item['published'] else 'Unknown'}

{item['summary'][:300]}...

---

"""
    else:
        report += "*No new items today.*\n\n---\n\n"
    
    report += """## Economics & Policy

"""
    if leader_scans.get("economics_policy"):
        for item in leader_scans["economics_policy"]:
            report += f"""### {item['leader']} ({item['org']})
**{item['title']}**  
📎 [{item['source']}]({item['url']})  
📅 {item['published'][:10] if item['published'] else 'Unknown'}

{item['summary'][:300]}...

---

"""
    else:
        report += "*No new items today.*\n\n---\n\n"
    
    report += """## Tech Policy

"""
    if leader_scans.get("tech_policy"):
        for item in leader_scans["tech_policy"]:
            report += f"""### {item['leader']} ({item['org']})
**{item['title']}**  
📎 [{item['source']}]({item['url']})  
📅 {item['published'][:10] if item['published'] else 'Unknown'}

{item['summary'][:300]}...

---

"""
    else:
        report += "*No new items today.*\n\n---\n\n"
    
    # General RSS feeds
    if general_scans:
        report += """## General RSS Feeds

"""
        for category, items in general_scans.items():
            report += f"""### {category.replace('_', ' ').title()}

"""
            for item in items[:5]:
                report += f"- **{item['title']}** — [{item['source']}]({item['url']})\n"
            report += "\n"
    
    # Bias check
    report += """---

## 🔍 Bias Balance Check

"""
    
    if len(perspective_counts) >= 4:
        report += "✅ **Good diversity:** Multiple perspectives represented\n"
    elif len(perspective_counts) >= 2:
        report += "⚠️ **Limited diversity:** Consider adding more viewpoints\n"
    else:
        report += "🔴 **Poor diversity:** Single perspective dominates\n"
    
    report += "\n### Perspective Distribution\n\n"
    for perspective, count in sorted(perspective_counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(count, 10)
        report += f"- {perspective}: {bar} ({count})\n"
    
    report += f"""
---

## 📊 Metrics

| Metric | Value | Target |
|--------|-------|--------|
| Thought leaders tracked | {sum(len(items) for items in leader_scans.values())} | 30+ |
| Viewpoint diversity score | {diversity_score}/10 | 8/10 |
| New items today | {len(all_items)} | 10-20 |
| Perspectives represented | {len(perspective_counts)} | 4+ |

---

*Generated by `thought-leader-scan.py` | Next run: Tomorrow 5:00 AM*
"""
    
    return report


# ── Main Entry Point ──────────────────────────────────────────────────
def main():
    """Main entry point."""
    print(f"Starting thought leader scan: {datetime.now().isoformat()}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    thought_leaders, general_feeds = load_config()
    seen = SeenStore(SEEN_FILE, ttl_days=120)

    print("Scanning thought leader feeds...")
    leader_scans = scan_thought_leaders(seen, thought_leaders)

    print("Scanning general RSS feeds...")
    general_scans = scan_general_feeds(seen, general_feeds)

    print("Generating report...")
    report = generate_report(leader_scans, general_scans)

    output_file = OUTPUT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-thought-leader-scan.md"
    output_file.write_text(report)

    # Commit the dedup store ONLY after the report is safely on disk. Saving it
    # earlier means a crash during report generation marks items as seen that
    # were never reported — they would be skipped forever on the next run.
    seen.save()

    total_items = sum(len(items) for items in leader_scans.values())
    general_items = sum(len(items) for items in general_scans.values())

    status = classify_status(
        total_items + general_items, RAW_FETCHED,
        len(FEEDS_ATTEMPTED), len(FEED_ERRORS),
    )
    write_health(
        "thought-leader-scan",
        {
            "leader_items": total_items,
            "general_items": general_items,
            "domains_covered": len(leader_scans),
            "feeds_attempted": len(FEEDS_ATTEMPTED),
            "feeds_failed": len(FEED_ERRORS),
            "raw_entries_fetched": RAW_FETCHED,
            "errors": FEED_ERRORS,
            "output": str(output_file),
            "status": status,
        },
    )

    print(f"\n✅ Scan complete: {output_file}")
    print(f"   - Leader items: {total_items} | General items: {general_items}")
    print(f"   - Domains covered: {len(leader_scans)}")
    print(f"   - Feeds: {len(FEEDS_ATTEMPTED)} attempted, {len(FEED_ERRORS)} failed")
    if status == "no-new-items":
        print(f"   - ℹ️  Nothing new ({RAW_FETCHED} entries fetched, all already seen).")
    elif status != "ok":
        print(f"   - 🔴 {status} — check System/thought-leader-scan-health.json")

    # Write to cron log file for traceability
    log_dir = Path(os.path.expanduser("~/.hermes/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "thought-leader-scan.log"
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | leader={total_items} general={general_items} feeds={len(FEEDS_ATTEMPTED)} failed={len(FEED_ERRORS)} status={status}\n")


if __name__ == "__main__":
    main()
