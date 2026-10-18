#!/usr/bin/env python3
"""
signal-noise.py

Weekly scoring of raw captures for actionability and novelty, and the
Raw → Reports promotion queue.

Scoring (per the Intelligence Cronjobs Improvement Plan, P3) — each 0-5:
  Novelty       new information vs. a rehash of what we already captured
  Credibility   source quality and classification
  Actionability can we actually do something with this?
  Durability    will this still matter in 6 months?

Thresholds:
  Signal  13-20  → promotion queue
  Medium  10-12  → keep in raw, tag for review
  Noise    0-9   → archive candidate

SAFETY: this script NEVER moves, edits or deletes a raw capture. By default it
only writes a report. `--promote` additionally writes a digest note into
20-Reports that CITES the high-signal items; the originals stay untouched.

Schedule: Weekly Sunday 9:00 AM
Output:   Knowledge/20-Reports/Signal-Noise-YYYY-Www.md
Health:   System/signal-noise-health.json
"""

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from bias_taxonomy import classify_source, classify_topics
from hermes_feeds import frontmatter_block, parse_frontmatter, write_health

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
RAW_DIR = VAULT_PATH / "Knowledge" / "00-Raw"
OUTPUT_DIR = VAULT_PATH / "Knowledge" / "20-Reports"

LOOKBACK_DAYS = 7
SCAN_SUBDIRS = ["thought-leaders", "research", "policy", "trends", "podcasts"]

SIGNAL_THRESHOLD = 13
MEDIUM_THRESHOLD = 10

# Markdown link with a bolded or plain title.
ITEM_RE = re.compile(r"\[([^\]]{12,200})\]\((https?://[^\s\)]+)\)")

# ── Scoring vocabularies ──────────────────────────────────────────────
ACTIONABLE_TERMS = [
    "launch", "release", "announce", "ship", "available", "open source",
    "framework", "regulation", "deadline", "funding", "acquire", "partner",
    "rfp", "tender", "grant", "standard", "guideline", "compliance",
    "roadmap", "pricing", "api", "benchmark results", "report finds",
    # decision-relevant verbs & nouns a business reader can act on
    "invest", "investment", "raise", "raises", "merger", "acquisition",
    "acquires", "acquired", "ipo", "public offering", "valuation", "revenue",
    "profit", "earnings", "forecast", "outlook", "guidance", "target",
    "strategy", "strategic", "expansion", "expands", "enters", "launches",
    "unveils", "introduces", "debuts", "wins", "wins contract", "awarded",
    "approves", "approved", "approval", "ban", "bans", "proposal", "proposes",
    "plan", "plans", "adopts", "adopted", "rolls out", "rollout", "partnership",
    "collaboration", "joint venture", "deal", "agreement", "contract",
    "hiring", "layoffs", "cuts", "closes", "opens", "new store", "new plant",
    "new factory", "capacity", "supply", "shortage", "surplus", "price",
    "prices", "cost", "costs", "margin", "margins", "demand", "sales",
    "growth", "grows", "declines", "falls", "rises", "surges", "plunges",
]

DURABLE_TERMS = [
    "law", "act", "treaty", "standard", "architecture", "framework",
    "scaling law", "theorem", "protocol", "constitution", "doctrine",
    "long-term", "structural", "foundational", "landmark",
]

EPHEMERAL_TERMS = [
    "rumor", "rumour", "reportedly", "leak", "teaser", "hints", "could",
    "might", "speculation", "sources say", "is said to", "expected to",
    "stock", "shares", "quarterly", "this week only",
]

HYPE_TERMS = [
    "game-changer", "revolutionary", "breakthrough", "insane", "mind-blowing",
    "you won't believe", "everything you need", "ultimate guide", "10x",
    "disrupt", "unprecedented",
]

# Domains whose output is primary/verifiable, versus aggregators/rewrites.
HIGH_CREDIBILITY_HOSTS = [
    "arxiv.org", "nature.com", "science.org", "acm.org", "ieee.org",
    "nber.org", "imf.org", "worldbank.org", "oecd.org", "europa.eu",
    "gov.uk", "whitehouse.gov", "congress.gov", "rand.org", "brookings.edu",
    "csis.org", "anthropic.com", "openai.com", "deepmind.google",
]
LOW_CREDIBILITY_HOSTS = [
    "news.google.com", "medium.com", "linkedin.com", "facebook.com",
    "twitter.com", "x.com", "reddit.com", "youtube.com",
]

# Publishers that, when named as the real source behind a Google News
# redirect, indicate a primary/verifiable outlet. Google News titles carry
# the publisher as a " - <Publisher>" suffix; we strip it and score on the
# real outlet instead of the news.google.com aggregator.
HIGH_CREDIBILITY_PUBLISHERS = [
    "nature", "science", "arxiv", "acm", "ieee", "nber", "imf", "world bank",
    "oecd", "europa", "gov.uk", "whitehouse", "congress", "rand", "brookings",
    "csis", "anthropic", "openai", "deepmind", "the economist", "ft.com",
    "financial times", "wall street journal", "wsj", "reuters", "bloomberg",
    "ap news", "associated press", "bbc", "the guardian", "new york times",
    "nytimes", "washington post", "mit technology review", "harvard",
    "stanford", "oxford", "cambridge", "mckinsey", "bain", "bcg",
    "deloitte", "pwc", "kpmg", "gartner", "forrester", "idc",
]
LOW_CREDIBILITY_PUBLISHERS = [
    "medium", "linkedin", "facebook", "twitter", "x.com", "reddit",
    "youtube", "quora", "substack", "wordpress", "blogspot", "tumblr",
    "buzzfeed", "dailymail", "the sun", "mirror", "express", "inquisitr",
    "zerohedge", "infowars", "breitbart", "the onion",
]

# Google News titles append the real publisher as a " - <Publisher>" suffix.
# Strip it so we can score on the actual outlet rather than the aggregator.
GOOGLE_NEWS_PUBLISHER_RE = re.compile(r"\s-\s([A-Za-z0-9][A-Za-z0-9 &.\-]{2,60})$")


def real_publisher(title: str) -> str:
    """Return the real publisher name from a Google News title, or ''."""
    m = GOOGLE_NEWS_PUBLISHER_RE.search(title or "")
    if m:
        return m.group(1).strip()
    return ""


# Content farms / press-release mills / SEO spam. Items from these are
# excluded from scoring entirely (defense-in-depth on top of the source-side
# filter in multi_source_fetcher.fetch_google_news). Matched case-insensitively
# against the real publisher name.
NOISE_PUBLISHERS = [
    "trend hunter", "globenewswire", "precedence research",
    "fortune business insights", "fundsforngos", "the motley fool",
    "market.us", "market.us media", "alleywatch", "simplilearn",
    "aimultiple", "yellow.com", "firstcallgolf", "biztech magazine",
    "global data", "triblive", "laodong.vn", "the quad-city times",
    "provider magazine", "pos.toasttab.com", "textile world",
    "mass market retailers", "quality assurance & food safety",
]


def is_noise_publisher(item: Dict[str, Any]) -> bool:
    """True if the item's real publisher is a known content farm / SEO spam."""
    pub = real_publisher(item.get("title", "")).lower()
    return bool(pub) and any(p in pub for p in NOISE_PUBLISHERS)

STOPWORDS = set(
    """the a an and or but of to in for on with at by from as is are was were be been
    this that these those it its their his her our your my we you they he she i
    new how what why when who which will can could would should may might must
    about into over under more most less least than then there here also just only""".split()
)


def tokens(text: str) -> Set[str]:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOPWORDS}


def recent_files(days: int = LOOKBACK_DAYS) -> List[Path]:
    cutoff = datetime.now() - timedelta(days=days)
    candidates = list(RAW_DIR.glob("*.md"))
    for sub in SCAN_SUBDIRS:
        d = RAW_DIR / sub
        if d.exists():
            candidates.extend(d.rglob("*.md"))

    out = []
    for f in candidates:
        if f.name.startswith("_"):
            continue
        try:
            fm = parse_frontmatter(f.read_text(errors="ignore"))
        except OSError:
            continue
        try:
            dt = datetime.strptime(fm.get("created", "")[:10], "%Y-%m-%d")
        except ValueError:
            dt = datetime.fromtimestamp(f.stat().st_mtime)
        if dt >= cutoff:
            out.append(f)
    return sorted(set(out))


def extract_items(files: List[Path]) -> List[Dict[str, Any]]:
    """Pull every linked item out of the week's captures.

    Items from known noise publishers (content farms / SEO spam) are excluded
    so they never enter the scoring funnel.
    """
    items: List[Dict[str, Any]] = []
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for title, url in ITEM_RE.findall(text):
            title = re.sub(r"[*_`]", "", title).strip()
            if title.startswith("http") or len(title) < 12:
                continue
            item = {"title": title, "url": url, "source_file": f.name}
            if is_noise_publisher(item):
                continue  # content farm / SEO spam — exclude from scoring
            items.append(item)
    return items


def score_novelty(item: Dict[str, Any], corpus: List[Set[str]]) -> int:
    """
    5 = nothing like it seen this week; 0 = near-duplicate of something else.

    Compared against every OTHER item's tokens, so a story covered by six
    outlets scores low for all six — which is the intent: repetition is noise.
    """
    t = tokens(item["title"])
    if not t:
        return 0
    best = 0.0
    for other in corpus:
        if other is t:
            continue
        if not other:
            continue
        overlap = len(t & other) / len(t | other)
        best = max(best, overlap)
    if best >= 0.6:
        return 0
    if best >= 0.45:
        return 1
    if best >= 0.3:
        return 2
    if best >= 0.2:
        return 3
    if best >= 0.1:
        return 4
    return 5


def score_credibility_with_publisher(item: Dict[str, Any]) -> int:
    """Score credibility, resolving Google News redirects to their real publisher.

    Google News links all point at news.google.com (an aggregator), so the
    host-based check would score every one of them as a low-credibility
    aggregator. But the title carries the real outlet as a " - <Publisher>"
    suffix. We strip it and score on the actual publisher, so a Reuters or
    Nature story surfaced via Google News is credited to Reuters/Nature, not
    to the aggregator.
    """
    url = item["url"].lower()
    title = item["title"] or ""
    score = 2  # unknown source baseline

    if any(h in url for h in HIGH_CREDIBILITY_HOSTS):
        score = 5
    elif any(h in url for h in LOW_CREDIBILITY_HOSTS):
        # news.google.com is an aggregator, not the source — resolve the real
        # publisher from the title before penalising.
        if "news.google.com" in url:
            pub = real_publisher(title).lower()
            if any(p in pub for p in HIGH_CREDIBILITY_PUBLISHERS):
                score = 5
            elif any(p in pub for p in LOW_CREDIBILITY_PUBLISHERS):
                score = 1
            else:
                score = 3  # unknown-but-real outlet: better than an aggregator
        else:
            score = 1

    if classify_source(url):
        score = min(5, score + 1)  # we know its perspective => known quantity
    if any(h in title.lower() for h in HYPE_TERMS):
        score = max(0, score - 2)
    return score


def score_actionability(item: Dict[str, Any]) -> int:
    text = item["title"].lower()
    hits = sum(1 for t in ACTIONABLE_TERMS if t in text)
    score = min(4, hits * 2)
    if re.search(r"\b(19|20|26)\d{2}\b|\d+%|\$\d|\bRM\d|\b\d+x\b", item["title"]):
        score += 1  # concrete numbers are actionable
    return min(5, score)


def score_durability(item: Dict[str, Any]) -> int:
    text = item["title"].lower()
    score = 2
    score += min(3, sum(1 for t in DURABLE_TERMS if t in text) * 2)
    score -= min(3, sum(1 for t in EPHEMERAL_TERMS if t in text) * 2)
    if "arxiv.org" in item["url"]:
        score += 1
    return max(0, min(5, score))


def score_all(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    corpus = [tokens(i["title"]) for i in items]
    for item, t in zip(items, corpus):
        item["_tokens"] = t
    for i, item in enumerate(items):
        others = corpus[:i] + corpus[i + 1:]
        n = score_novelty(item, others)
        c = score_credibility_with_publisher(item)
        a = score_actionability(item)
        d = score_durability(item)
        item.update(
            novelty=n, credibility=c, actionability=a, durability=d,
            total=n + c + a + d,
            topics=sorted(classify_topics(item["title"])),
        )
    return sorted(items, key=lambda x: -x["total"])


def generate_report(items: List[Dict[str, Any]], files: List[Path]) -> Tuple[str, Dict[str, Any]]:
    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()

    signal = [i for i in items if i["total"] >= SIGNAL_THRESHOLD]
    medium = [i for i in items if MEDIUM_THRESHOLD <= i["total"] < SIGNAL_THRESHOLD]
    noise = [i for i in items if i["total"] < MEDIUM_THRESHOLD]
    ratio = (len(signal) / len(items) * 100) if items else 0.0

    fm = frontmatter_block(
        title=f"Signal vs Noise — {iso_year}-W{iso_week:02d}",
        tags=["report", "signal-noise", "intelligence", "curation"],
        stage="report",
        status="shipped",
        extra={
            "distilled": "false",
            "sources": "Knowledge/00-Raw weekly captures",
            "items_scored": len(items),
            "signal_count": len(signal),
            "signal_ratio_pct": round(ratio, 1),
        },
    )

    report = fm + f"""
# 📡 Signal vs Noise — {iso_year} Week {iso_week:02d}

**Window:** {(now - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}
**Captures scanned:** {len(files)}
**Items scored:** {len(items)}
**Signal ratio:** {ratio:.1f}% (target: 15%)

---

## Scoring Model

Each item scores 0-5 on four axes (max 20):

| Axis | Question |
|------|----------|
| Novelty | New information, or a rehash of the week's other items? |
| Credibility | Primary/verifiable source, or an aggregator rewrite? |
| Actionability | Is there something concrete to do or decide? |
| Durability | Will this still matter in 6 months? |

| Tier | Range | Count |
|------|-------|-------|
| 🟢 Signal | {SIGNAL_THRESHOLD}-20 | {len(signal)} |
| 🟡 Medium | {MEDIUM_THRESHOLD}-{SIGNAL_THRESHOLD - 1} | {len(medium)} |
| ⚪ Noise | 0-{MEDIUM_THRESHOLD - 1} | {len(noise)} |

---

## 🟢 Promotion Queue (Signal)

"""
    if signal:
        report += (
            "These are the week's promotion candidates. Nothing has been moved — "
            "review, then cite them in a report or promote to "
            "[[System/Wiki/index|the wiki]].\n\n"
        )
        for i, item in enumerate(signal[:25], 1):
            topics = ", ".join(item["topics"]) or "unclassified"
            report += f"""### {i}. {item['title']}

- **Score:** {item['total']}/20 — N:{item['novelty']} C:{item['credibility']} A:{item['actionability']} D:{item['durability']}
- **Topics:** {topics}
- **Link:** [{item['url'][:90]}]({item['url']})
- **From:** `{item['source_file']}`

"""
    else:
        report += (
            f"*No items reached the signal threshold ({SIGNAL_THRESHOLD}/20) this week.*\n\n"
            "If this persists, either the feeds are degraded (check the health JSONs) "
            "or the thresholds need tuning in `signal-noise.py`.\n\n"
        )

    report += "---\n\n## 🟡 Medium Signal\n\n"
    if medium:
        report += f"{len(medium)} items scored {MEDIUM_THRESHOLD}-{SIGNAL_THRESHOLD - 1}. Top 20:\n\n"
        for item in medium[:20]:
            report += f"- **[{item['title'][:110]}]({item['url']})** — {item['total']}/20\n"
    else:
        report += "*None.*\n"

    # Noise pattern analysis — what is clogging the funnel?
    report += "\n---\n\n## ⚪ Noise Patterns\n\n"
    if noise:
        host_counts = Counter(
            re.sub(r"^https?://([^/]+).*", r"\1", i["url"]) for i in noise
        )
        report += f"{len(noise)} items scored below {MEDIUM_THRESHOLD}. Most frequent noise sources:\n\n"
        report += "| Host | Noise items |\n|------|-------------|\n"
        for host, n in host_counts.most_common(10):
            report += f"| `{host}` | {n} |\n"
        report += (
            "\n**Action:** hosts dominating this table are candidates for removal from "
            "`intelligence-sources.yaml`.\n"
        )
    else:
        report += "*None.*\n"

    report += f"""
---

## Related

- [[Knowledge/20-Reports/Bias-Balance-{iso_year}-W{iso_week:02d}|Bias Balance Report — W{iso_week:02d}]]
- [[System/Wiki/source-diversity|Source Diversity]]
- [[Knowledge/20-Reports/Intelligence-Cronjobs-Improvement-Plan|Intelligence Cronjobs Improvement Plan]]

---

*Generated by `signal-noise.py` | Next run: Sunday 9:00 AM*
"""

    stats = {
        "items_scored": len(items),
        "signal": len(signal),
        "medium": len(medium),
        "noise": len(noise),
        "signal_ratio_pct": round(ratio, 1),
        "captures_scanned": len(files),
    }
    return report, stats


def write_promotion_digest(signal: List[Dict[str, Any]]) -> Path:
    """Write a digest note citing high-signal items. Originals are never touched."""
    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()
    fm = frontmatter_block(
        title=f"Promoted Signals — {iso_year}-W{iso_week:02d}",
        tags=["report", "promotion", "intelligence"],
        stage="report",
        status="draft",
        extra={"distilled": "false", "promoted_items": len(signal)},
    )
    body = fm + f"\n# ⬆️ Promoted Signals — {iso_year} Week {iso_week:02d}\n\n"
    body += "Auto-drafted from the signal tier. Edit freely; sources stay in 00-Raw.\n\n"
    for item in signal:
        body += f"## {item['title']}\n\n"
        body += f"- Score {item['total']}/20 · Topics: {', '.join(item['topics']) or 'unclassified'}\n"
        body += f"- Source: [{item['url'][:90]}]({item['url']})\n"
        body += f"- Captured in: `{item['source_file']}`\n\n"
    out = OUTPUT_DIR / f"Promoted-Signals-{iso_year}-W{iso_week:02d}.md"
    out.write_text(body)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--promote",
        action="store_true",
        help="also write a Promoted-Signals digest note (never modifies raw captures)",
    )
    args = ap.parse_args()

    print(f"Starting signal/noise analysis: {datetime.now().isoformat()}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    files = recent_files()
    items = score_all(extract_items(files))
    report, stats = generate_report(items, files)

    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()
    output_file = OUTPUT_DIR / f"Signal-Noise-{iso_year}-W{iso_week:02d}.md"
    output_file.write_text(report)

    promoted = None
    if args.promote:
        signal = [i for i in items if i["total"] >= SIGNAL_THRESHOLD]
        if signal:
            promoted = write_promotion_digest(signal)

    write_health(
        "signal-noise",
        {**stats, "output": str(output_file),
         "promotion_digest": str(promoted) if promoted else None,
         "status": "ok" if items else "no-items"},
    )

    print(f"✅ Signal/noise complete: {output_file}")
    print(f"   - Items scored: {stats['items_scored']}")
    print(f"   - Signal {stats['signal']} / Medium {stats['medium']} / Noise {stats['noise']}")
    print(f"   - Signal ratio: {stats['signal_ratio_pct']}%")
    if promoted:
        print(f"   - Promotion digest: {promoted}")

    # Write to cron log file for traceability
    log_dir = Path(os.path.expanduser("~/.hermes/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "signal-noise.log"
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | scored={stats['items_scored']} signal={stats['signal']} medium={stats['medium']} noise={stats['noise']} ratio={stats['signal_ratio_pct']}%\n")


if __name__ == "__main__":
    main()
