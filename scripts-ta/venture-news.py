#!/usr/bin/env python3
"""Venture News producer — fetches the VentureNews RSS feed and writes a dated
capture to Knowledge/00-Raw/ so the daily-intelligence-loop can synthesize it.

VentureNews (https://venturenews.ai) is an AI-powered market-intelligence
newsletter covering business news, market insights, emerging opportunities,
industry trends and strategic developments for founders and investors.

The site is a Bubble.io app — the article_page HTML is JS-rendered and empty,
but it exposes a working RSS endpoint at /api/1.1/wf/rss (the /rss.xml link
404s). This script reads that feed.

Runs daily via Hermes cron. Output: Vault capture + health JSON + cron log.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from hermes_feeds import fetch_rss, frontmatter_block, write_health
from dedup import SeenUrls

# Feed sources for VC/PE market intelligence, tried in order. The original
# VentureNews (Bubble.io) endpoint went permanently dead (HTTP 400 / Bubble
# plan quota exceeded -> bubble.io/limit_exceeded), so the primary is now
# TechCrunch's Venture feed. Add alternates here if the primary fails.
FEED_URLS = [
    "https://techcrunch.com/category/venture/feed/",   # primary: VC/startup news
    "https://news.crunchbase.com/feed/",                # fallback (may 403; tried next)
]
MAX_ITEMS = 30  # the feed holds ~50; take the freshest 30

VAULT = Path("/Users/admin/Documents/Obsidian Vault")
RAW = VAULT / "Knowledge" / "00-Raw"
HEALTH_NAME = "venture-news"


def _frontmatter():
    """Schema v2 frontmatter for this capture (see System/SCHEMA.md)."""
    return frontmatter_block(
        title=f"Venture News — {datetime.now():%Y-%m-%d}",
        tags=["raw", "venture-news", "market-intelligence", "news"],
        note_type="raw",
        domain="knowledge",
    ) + "\n"


def build_report() -> tuple[str, list, dict]:
    """Fetch the feed, dedup, and build the markdown capture.

    Tries each source in FEED_URLS in order; uses the first that returns items.

    Returns (report_markdown, new_items, stats).
    """
    seen = SeenUrls()
    seen.load()

    items, err, used_url = [], None, None
    for url in FEED_URLS:
        items, err = fetch_rss(url, max_items=MAX_ITEMS)
        if items:
            used_url = url
            break
    if err and not items:
        return "", [], {"error": err, "fetched": 0, "new": 0, "feed": used_url or FEED_URLS[0]}

    new_items = []
    for it in items:
        url = it.get("url", "")
        title = it.get("title", "")
        if not url:
            continue
        if seen.is_new(url, it.get("published", ""), title):
            new_items.append(it)
            seen.mark(url, title)
    seen.save()

    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"# 📰 Venture News — {today}\n"]
    if new_items:
        lines.append("## 📰 Venture News\n")
        for it in new_items:
            title = it.get("title", "(untitled)")
            url = it.get("url", "")
            published = it.get("published", "")
            summary = it.get("summary", "")
            lines.append(f"- [{title}]({url})")
            if published:
                lines.append(f"  - *{published}*")
            if summary:
                lines.append(f"  - {summary}")
        lines.append("")
    else:
        lines.append("_No new items (all already captured or feed empty)._\n")

    lines.append(f"\n---\n*Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append(f"*Source: [Venture News]({used_url or FEED_URLS[0]}) RSS*")
    return "\n".join(lines), new_items, {"fetched": len(items), "new": len(new_items), "feed": used_url or FEED_URLS[0]}


def save_local(report: str) -> Path:
    """Write the dated capture to Knowledge/00-Raw/ (merge-safe, idempotent)."""
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"venture-news-{datetime.now():%Y-%m-%d}.md"
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="ignore")
        # Keep the existing capture; a same-day rerun after dedup would only
        # shrink it. Merge new item lines in if any appeared.
        if report and "## 📰 Venture News" in report:
            # Append any item lines not already present.
            new_lines = [
                l for l in report.splitlines()
                if l.startswith("- [") and l not in existing
            ]
            if new_lines:
                existing = existing.rstrip() + "\n" + "\n".join(new_lines) + "\n"
                path.write_text(existing, encoding="utf-8")
        return path
    path.write_text(_frontmatter() + report, encoding="utf-8")
    return path


def main() -> int:
    report, new_items, stats = build_report()

    if stats.get("error"):
        print(f"📰 Venture News — {datetime.now():%Y-%m-%d}")
        print(f"  🔴 Feed error: {stats['error']}")
        write_health(HEALTH_NAME, {
            "status": "all-feeds-failed",
            "feed": stats.get("feed", FEED_URLS[0]),
            "error": stats["error"],
            "fetched": 0,
            "new": 0,
        })
        return 1

    path = save_local(report)
    write_health(HEALTH_NAME, {
        "status": "ok" if stats["new"] else "no-new-items",
        "feed": stats.get("feed", FEED_URLS[0]),
        "fetched": stats["fetched"],
        "new": stats["new"],
        "capture": str(path.relative_to(VAULT)),
    })

    # Cron log for traceability (matches market-trends convention).
    log_dir = Path(os.path.expanduser("~/.hermes/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "venture-news.log", "a") as f:
        f.write(f"{datetime.now().isoformat()} | fetched={stats['fetched']} "
                f"new={stats['new']} status={'ok' if stats['new'] else 'no-new-items'}\n")

    print(f"📰 Venture News — {datetime.now():%Y-%m-%d}")
    print(f"  Fetched {stats['fetched']} items, {stats['new']} new")
    print(f"  💾 {path.relative_to(VAULT)}")
    for it in new_items[:5]:
        print(f"  • {it.get('title', '')[:80]}")
    if len(new_items) > 5:
        print(f"  … and {len(new_items) - 5} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
