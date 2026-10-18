#!/usr/bin/env python3
"""intel_source_of_truth.py — the ONE source of truth for intelligence publishing.

Every publisher (intelligence.teoh.my dashboard KV, Notion) reads the SAME
canonical producer taxonomy and the SAME vault-capture collection, so categories
can never diverge again.

This module is also the SINGLE definition of the producer taxonomy. daily-intelligence-loop
imports PRODUCER_LIST from here so there is exactly one place to edit categories, globs,
and emoji. It wraps `daily-intelligence-loop.collect()` — the proven collector that
already reads today's real captures from Knowledge/00-Raw/ — and exposes:

  PRODUCER_LIST  — canonical (label, glob, emoji, description) tuples (ONE definition)
  PRODUCERS      — dict label -> (emoji, description)  [kept for backward compat]
  PRODUCER_GLOBS — dict label -> glob                   [kept for backward compat]
  DISABLED_PRODUCERS — set of labels whose cron job is paused (category omitted)
  collect()      — {label: {file, items: [(title,url)], item_count, synth}}
  build_payload(day) — canonical dashboard/Notion payload

Anything that wants to publish should import THIS, not hand-roll categories.
"""
import importlib.util
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent
_LOOP = _SCRIPTS_DIR / "daily-intelligence-loop.py"
VAULT = Path("/Users/admin/Documents/Obsidian Vault")
from trend_contract import VERTICALS, validate_report_file

# ── Canonical producer taxonomy — SINGLE definition ──────────────────────────
# (label, glob_under_Knowledge/00-Raw, emoji, description). Both the collector
# (daily-intelligence-loop) and the publishers (KV, Notion) import this list, so
# adding/renaming a category happens in exactly one place.
PRODUCER_LIST = [
    ("Fashion & Retail",          "trends/market-trends-*.md",                 "👕", "Fashion, retail, supermarkets, e-commerce"),
    ("F&B & Consumer",            "trends/fnb-trends-*.md",                    "🍔", "Food & beverage, halal, loyalty, food security"),
    ("Hospitality & Healthcare",  "trends/hospitality-trends-*.md",            "🏨", "Hotels, tourism, co-living, retirement, TCM, rehab"),
    ("Property/Infra/Energy",     "trends/property-infra-energy-trends-*.md",  "🏗️", "Property, construction, data centers, energy"),
    ("C-Suite Leadership",        "trends/csuite-leadership-trends-*.md",      "💼", "CDO/AI strategy, leadership, transformation"),
    ("AI/Science/Investment",     "trends/ai-science-investment-trends-*.md",   "🤖", "AI/ML, fintech, VC/PE, science"),
    ("Market Intelligence",       "market-intelligence-*.md",                  "📡", "Cross-vertical market intelligence"),
    ("Venture News",              "venture-news-*.md",                          "📰", "VentureNews daily market intelligence feed"),
    ("Thought Leaders",           "thought-leaders/*-thought-leader-scan.md",  "🧠", "Thought-leader feeds and perspectives"),
    ("Policy Tracker",            "policy/*-policy-tracker.md",                 "🏛️", "Policy, regulatory, and think-tank tracking"),
    ("Research (arXiv)",          "research/*-arxiv.md",                       "🔬", "arXiv research papers"),
    ("Podcasts",                  "podcasts/*-podcast-insights.md",           "🎙️", "Podcast transcripts and insights"),
    ("LLM Learning",              "llm-learning-scan-*.md",                    "🧬", "LLM learning and knowledge-cluster captures"),
]

# Backward-compatible dict views derived from the single list above.
PRODUCERS = {label: (emoji, desc) for label, _glob, emoji, desc in PRODUCER_LIST}
PRODUCER_GLOBS = {label: glob for label, glob, _emoji, _desc in PRODUCER_LIST}

# Producers whose cron job is PAUSED or REMOVED. Their category is omitted from
# the payload (so a dead feed doesn't publish an empty bucket). A removed
# producer should instead be deleted from PRODUCER_LIST; this set is for
# paused-but-registered jobs only. Currently empty.
DISABLED_PRODUCERS: set[str] = set()

# A daily briefing is a cross-vertical product. Publishing an AI-heavy fragment
# as a "daily intelligence" briefing when half the commercial verticals are
# empty is false completeness, not resilience. This is deliberately a hard
# gate: three of the five non-academic commercial verticals empty => no publish.
CORE_NONACADEMIC_PRODUCERS = {
    "Fashion & Retail", "F&B & Consumer", "Hospitality & Healthcare",
    "Property/Infra/Energy", "C-Suite Leadership",
}
STOP_SHIP_EMPTY_NONACADEMIC = 3


def publication_gate(payload: dict) -> tuple[bool, list[str]]:
    """Return (publishable, absent core verticals) for a daily payload."""
    # Names in the payload include the emoji. Use exact suffix matching rather
    # than trusting a display prefix which can be revised independently.
    present = {label for label in CORE_NONACADEMIC_PRODUCERS
               if any(cat.get("name", "").endswith(label) and cat.get("articles")
                      for cat in payload.get("categories", []))}
    absent = sorted(CORE_NONACADEMIC_PRODUCERS - present)
    return len(absent) < STOP_SHIP_EMPTY_NONACADEMIC, absent


def load_collect(day: str):
    """Reuse daily-intelligence-loop.collect() so we publish the same data it does."""
    spec = importlib.util.spec_from_file_location("_dil_loop", _LOOP)
    loop = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loop)
    return loop.collect(day)


def build_payload(day: str):
    """Assemble the canonical dashboard/Notion payload from real vault captures."""
    collected = load_collect(day)
    categories = []
    total = 0
    for label, (emoji, desc) in PRODUCERS.items():
        if label in DISABLED_PRODUCERS:
            continue  # paused producer — skip empty category
        data = collected.get(label)
        items = []
        if data:
            # A producer whose persisted capture fails the category contract is
            # not evidence of coverage. Fail closed: exclude its entire output
            # from the combined briefing until the underlying capture is fixed.
            vertical_key = next((key for key, spec in VERTICALS.items()
                                 if spec["file_stem"] + "-" in Path(data["file"]).name), None)
            if vertical_key and validate_report_file(vertical_key, str(VAULT / data["file"])):
                # Validation returned issues (non-empty list), so skip this vertical
                continue
            for title, url in data["items"]:
                items.append({"title": title, "url": url, "date": day, "source": label, "summary": ""})
        if items:
            categories.append({
                "name": f"{emoji} {label}",
                "description": desc,
                "articles": items,
            })
            total += len(items)
    return {
        "date": day,
        "version": "3.0",  # canonical, vault-driven
        "generated_at": __import__("datetime").datetime.now().isoformat(),
        "categories": categories,
        "summary": {"total_categories": len(categories), "total_articles": total},
        "source_of_truth": "daily-intelligence-loop (vault captures)",
        "disabled_producers": sorted(DISABLED_PRODUCERS),
    }


if __name__ == "__main__":
    from datetime import date
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    import json
    p = build_payload(day)
    print(json.dumps(p, indent=2)[:1500])
