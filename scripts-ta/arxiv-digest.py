#!/usr/bin/env python3
"""
arxiv-digest.py

Daily digest of new ML/AI papers from arXiv, with priority scoring and
controversy flagging.

Uses the official arXiv Atom API (export.arxiv.org/api/query). The legacy
`arxiv.org/rss/<cat>` endpoints referenced in intelligence-sources.yaml return
zero items and must not be used.

Schedule: Daily 4:00 AM
Output:   Knowledge/00-Raw/research/YYYY-MM-DD-arxiv.md
Health:   System/arxiv-digest-health.json

Part of the Intelligence Cronjobs Improvement Plan (P1).
"""

import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from hermes_feeds import (
    CYCLE,
    SeenStore,
    classify_status,
    entry_datetime,
    frontmatter_block,
    health_banner,
    http_get,
    strip_html,
    write_health,
)

try:
    import feedparser
except ImportError:
    feedparser = None

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
OUTPUT_DIR = VAULT_PATH / "Knowledge" / "00-Raw" / "research"
LOG_DIR = Path(os.path.expanduser("~/.hermes/logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
SEEN_FILE = Path.home() / ".hermes" / "arxiv-seen.json"

API = "http://export.arxiv.org/api/query"

CATEGORIES = {
    "cs.LG": "Machine Learning",
    "cs.AI": "Artificial Intelligence",
    "cs.CL": "Computation and Language",
    "cs.RO": "Robotics",
}

# How far back to accept papers, and how many to pull per category.
# 4 days, not 2: arXiv does not announce on weekends, and this host runs at
# UTC+8, so a Monday 4AM run must still reach back past Friday's batch.
# Dedup via SeenStore means the overlap never produces repeats.
LOOKBACK_DAYS = 4
PER_CATEGORY = 60

# arXiv asks for ~3s between API calls. Be a good citizen.
API_DELAY_SECONDS = 3

# ── Priority scoring ──────────────────────────────────────────────────
# Deterministic keyword heuristics — no LLM call, so this stays cheap and
# reproducible. Weights are additive; see score_paper().
HIGH_SIGNAL_TERMS = {
    "scaling law": 3, "emergent": 2, "interpretability": 3, "mechanistic": 3,
    "alignment": 3, "rlhf": 2, "reward model": 2, "constitutional": 2,
    "chain-of-thought": 2, "reasoning": 2, "agent": 2, "tool use": 2,
    "benchmark": 1, "evaluation": 1, "distillation": 2, "mixture-of-experts": 2,
    "long context": 2, "retrieval-augmented": 2, "state space model": 2,
    "sample efficiency": 2, "generalization": 1, "robustness": 1,
    "jailbreak": 3, "red team": 3, "sandbagging": 3, "deceptive": 3,
}

# Claims that deserve a skeptic's eye before being cited anywhere.
CONTROVERSY_TERMS = [
    "agi", "artificial general intelligence", "human-level", "superhuman",
    "consciousness", "sentient", "solves", "definitive", "proves that",
    "unprecedented", "revolutionary", "first ever",
]

# Papers from these groups get a credibility bump (affiliation appears in the
# abstract/author block often enough to be a useful weak signal).
MAJOR_LABS = [
    "deepmind", "openai", "anthropic", "meta ai", "fair", "google research",
    "microsoft research", "nvidia", "allen institute", "ai2", "stanford",
    "mit", "berkeley", "cmu", "carnegie mellon", "oxford", "cambridge",
    "tsinghua", "eth zurich", "mila", "inria",
]


def fetch_category(cat: str, per_category: int = PER_CATEGORY) -> Tuple[List[Dict[str, Any]], str]:
    """Query the arXiv API for the newest submissions in one category."""
    params = urllib.parse.urlencode(
        {
            "search_query": f"cat:{cat}",
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": per_category,
        }
    )
    body, err = http_get(f"{API}?{params}", timeout=30)
    if body is None:
        return [], err or "unknown error"
    if feedparser is None:
        return [], "feedparser not installed"

    feed = feedparser.parse(body)
    papers = []
    for entry in feed.entries:
        authors = [a.get("name", "") for a in entry.get("authors", [])]
        papers.append(
            {
                "title": " ".join(strip_html(entry.get("title", "")).split()),
                "url": entry.get("link", ""),
                "abstract": " ".join(strip_html(entry.get("summary", "")).split()),
                "authors": authors,
                "published": entry.get("published", ""),
                "published_parsed": entry.get("published_parsed"),
                "category": cat,
                "category_label": CATEGORIES.get(cat, cat),
                "primary_category": entry.get("arxiv_primary_category", {}).get("term", cat)
                if isinstance(entry.get("arxiv_primary_category"), dict)
                else cat,
            }
        )
    return papers, ""


def score_paper(paper: Dict[str, Any]) -> Dict[str, Any]:
    """
    Score a paper 0-10 for priority, and flag controversial claims.

    Signal comes from the title+abstract only; this is a triage filter, not a
    quality judgement.
    """
    haystack = f"{paper['title']} {paper['abstract']}".lower()
    author_blob = " ".join(paper["authors"]).lower() + " " + haystack

    score = 0
    matched = []
    for term, weight in HIGH_SIGNAL_TERMS.items():
        if term in haystack:
            score += weight
            matched.append(term)

    # Word-boundary match, NOT substring: bare `in` makes "mit" fire on
    # "limitations"/"submitted" and "fair" fire on "fairness" — both ubiquitous
    # in ML abstracts, which would mark almost every paper as major-lab work.
    lab_hits = [
        lab for lab in MAJOR_LABS
        if re.search(rf"\b{re.escape(lab)}\b", author_blob)
    ]
    if lab_hits:
        score += 2

    controversy = [t for t in CONTROVERSY_TERMS if t in haystack]
    if controversy:
        score += 1  # controversial claims are worth SEEING, flagged as such

    paper["score"] = min(score, 10)
    paper["signals"] = matched[:6]
    paper["labs"] = lab_hits[:3]
    paper["controversy"] = controversy[:4]
    return paper


def is_fresh(paper: Dict[str, Any], days: int = LOOKBACK_DAYS) -> bool:
    dt = entry_datetime(paper)
    if dt is None:
        return False
    return dt >= datetime.now(timezone.utc) - timedelta(days=days)


def collect() -> Tuple[List[Dict[str, Any]], Dict[str, str], int]:
    """Fetch, filter, dedupe and score papers across all categories."""
    seen = SeenStore(SEEN_FILE, ttl_days=180)
    errors: Dict[str, str] = {}
    papers: List[Dict[str, Any]] = []
    attempted = 0
    raw_fetched = 0

    for i, cat in enumerate(CATEGORIES):
        attempted += 1
        if i:
            time.sleep(API_DELAY_SECONDS)
        print(f"  Fetching {cat}...")
        found, err = fetch_category(cat)
        if err:
            errors[cat] = err
            print(f"  ⚠️  {cat} — {err}")
            continue
        raw_fetched += len(found)

        for p in found:
            if not p["url"] or seen.seen(p["title"], p["url"]):
                continue
            if not is_fresh(p):
                continue
            seen.add(p["title"], p["url"])
            papers.append(score_paper(p))

    seen.save()
    # Highest score first; ties broken by recency.
    papers.sort(key=lambda p: (-p["score"], p["published"]), reverse=False)
    papers.sort(key=lambda p: -p["score"])
    return papers, errors, attempted, raw_fetched


def render_paper(p: Dict[str, Any], index: int) -> str:
    authors = ", ".join(p["authors"][:4])
    if len(p["authors"]) > 4:
        authors += f" +{len(p['authors']) - 4} more"

    block = f"""#### {index}. {p['title']}

- **Score:** {p['score']}/10 · **Category:** {p['category']} ({p['category_label']})
- **Authors:** {authors or 'unknown'}
- **Link:** [{p['url']}]({p['url']})
"""
    if p["signals"]:
        block += f"- **Signals:** {', '.join(p['signals'])}\n"
    if p["labs"]:
        block += f"- **Affiliation hits:** {', '.join(p['labs'])}\n"
    if p["controversy"]:
        block += (
            f"- 🚩 **Strong-claim language:** {', '.join(p['controversy'])} "
            "— seek a skeptic's read before citing\n"
        )

    abstract = p["abstract"]
    block += f"\n> {abstract[:420]}{'…' if len(abstract) > 420 else ''}\n\n"
    return block


def generate_report(papers: List[Dict[str, Any]], errors: Dict[str, str], attempted: int) -> str:
    today = datetime.now()
    high = [p for p in papers if p["score"] >= 6]
    medium = [p for p in papers if 3 <= p["score"] < 6]
    low = [p for p in papers if p["score"] < 3]
    flagged = [p for p in papers if p["controversy"]]

    fm = frontmatter_block(
        title=f"arXiv Digest — {today.strftime('%Y-%m-%d')}",
        tags=["raw", "research", "arxiv", "intelligence", "daily-scan"],
        extra={
            "sources": "arXiv API (cs.LG, cs.AI, cs.CL, cs.RO)",
            "papers_total": len(papers),
            "papers_high_priority": len(high),
            "categories_failed": len(errors),
        },
    )

    report = fm + f"""
# 📚 arXiv Digest — {today.strftime('%A, %Y-%m-%d')}

**Generated:** {today.strftime('%H:%M')}
**Window:** submissions from the last {LOOKBACK_DAYS} days
**Categories:** {', '.join(f'`{c}`' for c in CATEGORIES)}

---

{health_banner(len(papers), errors, attempted)}

---

## Summary

| Tier | Count | Meaning |
|------|-------|---------|
| 🔴 High priority | {len(high)} | Score ≥6 — read these |
| 🟡 Medium | {len(medium)} | Score 3-5 — skim titles |
| ⚪ Low | {len(low)} | Score <3 — archive |
| 🚩 Strong-claim | {len(flagged)} | Contains AGI/superhuman/"solves" language |

**Total new papers:** {len(papers)}

---

## 🔴 High Priority

"""
    if high:
        for i, p in enumerate(high[:15], 1):
            report += render_paper(p, i)
        if len(high) > 15:
            report += f"*…and {len(high) - 15} more high-priority papers omitted for length.*\n\n"
    else:
        report += "*No high-priority papers in this window.*\n\n"

    report += "---\n\n## 🟡 Medium Priority\n\n"
    if medium:
        for p in medium[:20]:
            sig = f" — _{', '.join(p['signals'][:3])}_" if p["signals"] else ""
            report += f"- **[{p['title']}]({p['url']})** ({p['category']}, {p['score']}/10){sig}\n"
        if len(medium) > 20:
            report += f"\n*…and {len(medium) - 20} more.*\n"
    else:
        report += "*None.*\n"

    report += "\n---\n\n## 🚩 Strong-Claim Papers\n\n"
    if flagged:
        report += (
            "These use language that historically correlates with overclaiming. "
            "Per the bias framework, seek a skeptic voice (e.g. Gary Marcus, "
            "Rodney Brooks) before citing them.\n\n"
        )
        for p in flagged[:10]:
            report += f"- **[{p['title']}]({p['url']})** — flags: {', '.join(p['controversy'])}\n"
    else:
        report += "*None flagged.*\n"

    report += f"""
---

## ⚪ Low Priority

{len(low)} papers scored below 3 and are omitted. Adjust `HIGH_SIGNAL_TERMS` in
`arxiv-digest.py` if a topic you care about is being filtered out.

---

## Related

- [[Knowledge/20-Reports/Intelligence-Cronjobs-Improvement-Plan|Intelligence Cronjobs Improvement Plan]]
- [[System/Wiki/source-diversity|Source Diversity]]
- [[System/Wiki/bias-detection|Bias Detection]]

---

*Generated by `arxiv-digest.py` | Next run: Tomorrow 4:00 AM*
"""
    return report


def main():
    print(f"Starting arXiv digest: {datetime.now().isoformat()}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    papers, errors, attempted, raw_fetched = collect()
    report = generate_report(papers, errors, attempted)

    output_file = OUTPUT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-arxiv.md"
    output_file.write_text(report)

    high = sum(1 for p in papers if p["score"] >= 6)
    status = classify_status(len(papers), raw_fetched, attempted, len(errors))
    write_health(
        "arxiv-digest",
        {
            "papers_total": len(papers),
            "papers_high_priority": high,
            "categories_attempted": attempted,
            "categories_failed": len(errors),
            "raw_entries_fetched": raw_fetched,
            "errors": errors,
            "output": str(output_file),
            "status": status,
        },
    )

    # Write to cron log file for traceability
    log_file = LOG_DIR / "arxiv-digest.log"
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | papers={len(papers)} high={high} status={status}\n")

    print(f"\n✅ arXiv digest complete: {output_file}")
    print(f"   - Papers captured: {len(papers)} ({high} high priority)")
    if errors:
        print(f"   - ⚠️  Failed categories: {', '.join(errors)}")
    if status == "no-new-items":
        print(f"   - ℹ️  Nothing new ({raw_fetched} entries fetched, all already seen).")
    elif status != "ok":
        print(f"   - 🔴 {status} — check System/arxiv-digest-health.json")


if __name__ == "__main__":
    main()
