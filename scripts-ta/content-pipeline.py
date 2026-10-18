#!/usr/bin/env python3
"""
content-pipeline.py

Weekly content pipeline for Zehan Teoh's personal brand. Scans the vault's
daily trend scans, LLM learning scans, and MUI transformation notes, then drafts
2-3 social media post candidates based on the brand voice and pillars.

Content pillars:
  1. Conglomerate Transformation — how to compress the conglomerate discount
  2. Corporate Venture Building — standing up CVC vehicles from zero
  3. Cross-Border Capital — China-ASEAN-GCC deal architecture
  4. Leadership & Resilience — lessons from painful failures, first principles

Brand voice: Visionary but grounded. Operator who's actually built things.
Tone: Patient, anti-hype, values-driven, direct.

Schedule: Weekly, Sundays 07:00
Output:   Knowledge/00-Raw/content-pipeline/content-drafts-{YYYY-MM-DD}.md
Health:   System/content-pipeline-health.json
"""
import json
import os
import re
import html
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_feeds import authorship_tag  # single source for the authorship tag

VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
LLM_SCANS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
MUI_DIR = os.path.join(VAULT_DIR, "Work", "Business", "MUI-Transformation")
OUTPUT_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "content-pipeline")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "content-pipeline-health.json")
CALENDAR_PATH = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "content-pipeline", "content-calendar.md")

# ── Brand Voice & Pillars ──────────────────────────────────────────────
BRAND_VOICE = """
Brand: The Operator-Builder Archetype
Voice: Visionary but grounded. McKinsey partner who's actually built things.
Tone: Patient, anti-hype, values-driven, direct.

Rules:
- No hype, FOMO, or "get rich quick" framing
- No academic/theoretical without practical grounding
- No name-dropping without substance
- No victim narrative — lessons as learnings, not complaints
- Draw from lived experience, not theory
"""

PILLARS = {
    "conglomerate_transformation": {
        "angle": "How to compress the conglomerate discount; ChannelCo/BrandCo separation; governance reform",
        "audience": "Corporate boards, family offices, C-suite",
        "format": "LinkedIn articles, speaking",
    },
    "corporate_venture_building": {
        "angle": "Standing up CVC vehicles from zero; sweat equity vs capital; when to build vs buy",
        "audience": "Corporate strategy teams, innovation officers",
        "format": "Case studies, workshops",
    },
    "cross_border_capital": {
        "angle": "China-ASEAN-GCC deal architecture; cultural fluency as advantage; regulatory complexity",
        "audience": "Family offices, sovereign funds, cross-border investors",
        "format": "Speaking, panels, briefings",
    },
    "leadership_resilience": {
        "angle": "The three-act narrative; lessons from painful failures; first principles for operators",
        "audience": "Founders, young professionals, mentees",
        "format": "Mentoring, educational platform, personal brand",
    },
}

# ── Topic keywords for scanning ────────────────────────────────────────
TOPIC_KEYWORDS = {
    "conglomerate_transformation": [
        "conglomerate", "transformation", "retail", "turnaround", "governance",
        "board", "mui", "metrojaya", "channel", "brand", "concession",
        "restructuring", "portfolio", "discount",
    ],
    "corporate_venture_building": [
        "venture", "startup", "cvc", "corporate venture", "sweat equity",
        "venture builder", "accelerator", "ecosystem", "innovation",
        "guard genius", "radikale", "intrinsic",
    ],
    "cross_border_capital": [
        "cross-border", "china", "asean", "gcc", "middle east",
        "sovereign", "family office", "investment", "deal",
        "malaysia", "asia", "capital",
    ],
    "leadership_resilience": [
        "leadership", "resilience", "mentor", "failure", "lesson",
        "values", "principles", "reflection", "goal-setting",
        "first principles", "discipline",
    ],
}

# ── News queries for fresh content angles ──────────────────────────────
NEWS_QUERIES = {
    "conglomerate_transformation": "conglomerate transformation turnaround strategy 2026",
    "corporate_venture_building": "corporate venture capital CVC building startups 2026",
    "cross_border_capital": "cross-border investment China ASEAN GCC family office 2026",
    "leadership_resilience": "leadership resilience operator builder lessons 2026",
}


def fetch_news(query, max_results=3):
    """Fetch news from Google News RSS."""
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        data = urllib.request.urlopen(url, timeout=12).read().decode("utf-8")
        items = re.findall(
            r"<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?</item>",
            data, re.DOTALL,
        )
        results = []
        for title, link in items[:max_results]:
            clean = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            results.append({"title": clean, "url": link.strip()})
        return results
    except Exception:
        return []


def scan_vault_files(days_back=7):
    """Scan vault files modified in the last N days for content angles."""
    cutoff = datetime.now() - timedelta(days=days_back)
    vault_content = []

    # Scan trends directory
    for d in [TRENDS_DIR, LLM_SCANS_DIR]:
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            if not f.endswith(".md"):
                continue
            path = os.path.join(d, f)
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if mtime > cutoff:
                try:
                    with open(path, "r") as fh:
                        content = fh.read()[:3000]
                    vault_content.append({
                        "file": f,
                        "path": path,
                        "date": mtime.strftime("%Y-%m-%d"),
                        "excerpt": content[:500],
                    })
                except Exception:
                    pass

    # Scan MUI transformation directory
    if os.path.exists(MUI_DIR):
        for f in os.listdir(MUI_DIR):
            if not f.endswith(".md"):
                continue
            path = os.path.join(MUI_DIR, f)
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if mtime > cutoff:
                try:
                    with open(path, "r") as fh:
                        content = fh.read()[:3000]
                    vault_content.append({
                        "file": f,
                        "path": path,
                        "date": mtime.strftime("%Y-%m-%d"),
                        "excerpt": content[:500],
                    })
                except Exception:
                    pass

    return vault_content


def categorize_content(content_items):
    """Match content items to brand pillars based on keywords."""
    categorized = {pillar: [] for pillar in PILLARS}

    for item in content_items:
        text = (item.get("file", "") + " " + item.get("excerpt", "")).lower()
        for pillar, keywords in TOPIC_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                categorized[pillar].append(item)
                break

    return categorized


def fetch_fresh_news():
    """Fetch fresh news for each pillar."""
    news = {}
    for pillar, query in NEWS_QUERIES.items():
        news[pillar] = fetch_news(query, max_results=3)
    return news


def draft_post(pillar, source_items, news_items, post_num=1):
    """Draft a social media post candidate for a given pillar."""
    pillar_config = PILLARS[pillar]
    angle = pillar_config["angle"]
    audience = pillar_config["audience"]

    # Gather source material
    sources = []
    for item in source_items[:2]:
        sources.append(f"- From {item['file']} ({item['date']}): {item['excerpt'][:200]}")
    for item in news_items[:2]:
        sources.append(f"- News: [{item['title']}]({item['url']})")

    source_text = "\n".join(sources) if sources else "*No fresh sources this week.*"

    # Generate post draft based on pillar
    post_templates = {
        "conglomerate_transformation": f"""**Draft Post {post_num} — Conglomerate Transformation (LinkedIn)**

Pillar: Conglomerate Transformation
Audience: {audience}
Angle: {angle}

---
In my experience leading transformation at a diversified conglomerate, the hardest part isn't the strategy — it's the execution discipline.

[KEY INSIGHT FROM SOURCES BELOW]

The conglomerate discount exists because markets don't believe the sum is greater than the parts. The fix isn't financial engineering — it's operational separation, governance reform, and portfolio focus.

What I've learned: transformation is 20% strategy, 80% tracking and accountability.

---
Sources:
{source_text}""",
        "corporate_venture_building": f"""**Draft Post {post_num} — Corporate Venture Building (LinkedIn)**

Pillar: Corporate Venture Building
Audience: {audience}
Angle: {angle}

---
I've stood up corporate venture vehicles from zero — twice. At FWD Group ($12.5M CVC) and Qatar Insurance Group. Here's what most get wrong:

[KEY INSIGHT FROM SOURCES BELOW]

The mistake: thinking CVC is about deploying capital. It's not. It's about building the capability to identify, evaluate, and support ventures — with skin in the game.

Sweat equity > capital. No more free lunch.

---
Sources:
{source_text}""",
        "cross_border_capital": f"""**Draft Post {post_num} — Cross-Border Capital (LinkedIn / X)**

Pillar: Cross-Border Capital
Audience: {audience}
Angle: {angle}

---
China → ASEAN → Central Asia → Middle East. That's not a travel itinerary — it's the deal flow map for the next decade.

[KEY INSIGHT FROM SOURCES BELOW]

Cultural fluency isn't a nice-to-have in cross-border deals. It's the difference between a signed term sheet and a polite rejection. I've operated across 15+ jurisdictions — the deals that close are built on relationships, not just financials.

---
Sources:
{source_text}""",
        "leadership_resilience": f"""**Draft Post {post_num} — Leadership & Resilience (LinkedIn / X)**

Pillar: Leadership & Resilience
Audience: {audience}
Angle: {angle}

---
20 years. 6 betrayals. 1 lesson that took me too long to learn:

Trust actions, not words. Set terms before you trust. Process the pain, don't escape it.

[KEY INSIGHT FROM SOURCES BELOW]

I spent years trusting the pitch, the warmth, the shared history — and getting burned every time. The fix isn't cynicism. It's structure: 30 days of observed action before commitment. 3 red signals = exit.

The system runs whether you feel like it or not.

---
Sources:
{source_text}""",
    }

    return post_templates.get(pillar, f"**Draft Post {post_num}** — {pillar}\n\n{source_text}")


def update_calendar(post_drafts, week_label):
    """Update or create the content calendar tracking file."""
    os.makedirs(os.path.dirname(CALENDAR_PATH), exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    entry = f"\n### Week of {week_label} (generated {today})\n"
    entry += f"| # | Pillar | Platform | Status |\n"
    entry += f"|---|--------|----------|--------|\n"

    for i, draft in enumerate(post_drafts, 1):
        pillar_name = draft.get("pillar", "unknown")
        platform = draft.get("platform", "LinkedIn")
        entry += f"| {i} | {pillar_name} | {platform} | Draft |\n"

    entry += "\n"

    if os.path.exists(CALENDAR_PATH):
        with open(CALENDAR_PATH, "a") as f:
            f.write(entry)
    else:
        header = f"""---
title: Content Calendar — Personal Brand Pipeline
created: {today}
updated: {today}
type: index
domain: knowledge
status: active
tags: [content, brand, calendar, {authorship_tag()}]
---

# Content Calendar

Automated pipeline output. Each week generates 2-3 post candidates for review.

| Week | Posts Drafted | Posts Published | Notes |
|------|---------------|-----------------|-------|

"""
        with open(CALENDAR_PATH, "w") as f:
            f.write(header + entry)


def main():
    today = datetime.now()
    week_label = today.strftime("%Y-%m-%d")

    # 1. Scan vault for recent content
    vault_content = scan_vault_files(days_back=7)
    categorized = categorize_content(vault_content)

    # 2. Fetch fresh news per pillar
    news = fetch_fresh_news()

    # 3. Select top 2-3 pillars with most source material
    pillar_scores = {}
    for pillar in PILLARS:
        score = len(categorized.get(pillar, [])) + len(news.get(pillar, []))
        pillar_scores[pillar] = score

    top_pillars = sorted(pillar_scores, key=pillar_scores.get, reverse=True)[:3]

    # 4. Draft posts
    post_drafts = []
    for i, pillar in enumerate(top_pillars, 1):
        if pillar_scores[pillar] == 0:
            continue
        draft_text = draft_post(pillar, categorized.get(pillar, []), news.get(pillar, []), post_num=i)
        platform = "LinkedIn" if pillar in ["conglomerate_transformation", "corporate_venture_building", "leadership_resilience"] else "LinkedIn / X"
        post_drafts.append({
            "pillar": pillar.replace("_", " ").title(),
            "platform": platform,
            "draft": draft_text,
        })

    # 5. Build report
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    report = f"""---
title: Content Pipeline Drafts — {week_label}
type: raw
domain: knowledge
status: draft
cycle: 2026-Q4
created: {week_label}
tags: [raw, content, brand, pipeline, draft, {authorship_tag()}]
---

# Content Pipeline Drafts — Week of {week_label}

> Auto-generated by `content-pipeline.py`. Review, edit, and post. Do not auto-post.

{BRAND_VOICE}

---

## Vault Scan Summary

- Files scanned (last 7 days): {len(vault_content)}
- Pillars with content:
"""
    for pillar, items in categorized.items():
        report += f"  - {pillar.replace('_', ' ').title()}: {len(items)} items\n"

    report += f"\n## News Scan Summary\n\n"
    for pillar, items in news.items():
        report += f"  - {pillar.replace('_', ' ').title()}: {len(items)} articles\n"

    report += f"\n---\n\n## Post Drafts ({len(post_drafts)} candidates)\n\n"

    for draft in post_drafts:
        report += draft["draft"] + "\n\n---\n\n"

    if not post_drafts:
        report += "*No drafts generated this week — insufficient source material.*\n\n"

    report += f"""
---

## Instructions

1. Review each draft above
2. Edit to add personal voice and specific examples from the week
3. Replace [KEY INSIGHT FROM SOURCES BELOW] with your actual take
4. Post to the platform indicated
5. Update status in [[content-calendar]] from "Draft" to "Published"

---

## Content Pillar Rotation

| Pillar | This Week | Last Posted |
|--------|-----------|------------|
"""
    for pillar in PILLARS:
        is_this_week = "✅" if any(pillar.replace("_", " ").title() in d["pillar"] for d in post_drafts) else "—"
        report += f"| {pillar.replace('_', ' ').title()} | {is_this_week} | — |\n"

    report += f"\n---\n*Generated {today.strftime('%Y-%m-%d %H:%M')} by content-pipeline.py*\n"

    # 6. Save report
    output_path = os.path.join(OUTPUT_DIR, f"content-drafts-{week_label}.md")
    with open(output_path, "w") as f:
        f.write(report)

    # 7. Update calendar
    update_calendar(post_drafts, week_label)

    # 8. Health log
    health = {
        "timestamp": today.isoformat(),
        "vault_files_scanned": len(vault_content),
        "news_articles_fetched": sum(len(v) for v in news.values()),
        "drafts_generated": len(post_drafts),
        "pillars_covered": [d["pillar"] for d in post_drafts],
        "report_path": output_path,
    }
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f:
        json.dump(health, f, indent=2)

    print(f"📝 Content Pipeline — {today.strftime('%Y-%m-%d %H:%M')}")
    print(f"  📁 Vault files scanned (7 days): {len(vault_content)}")
    print(f"  📰 News articles fetched: {sum(len(v) for v in news.values())}")
    print(f"  ✏️  Drafts generated: {len(post_drafts)}")
    for d in post_drafts:
        print(f"    - {d['pillar']} ({d['platform']})")
    print(f"  💾 Saved: {output_path}")
    print(f"  📅 Calendar updated: {CALENDAR_PATH}")

    return 0


if __name__ == "__main__":
    main()