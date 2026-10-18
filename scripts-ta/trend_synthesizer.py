#!/usr/bin/env python3
"""trend_synthesizer.py — shared LLM analysis & synthesis for the vertical market-trends jobs.

Each vertical cron currently writes a bare headline list. This helper adds a
**narrative synthesis** section: an LLM reads the day's fetched items PLUS the
recent daily reports already in the vault, and writes an analysis with themes,
key signals, and implications — so the daily report is a synthesis of the latest
news and the accumulated daily record, not just a link dump.

Usage (from a market-trends-*.py script):
    from trend_synthesizer import synthesize, llm_chat
    synthesis = synthesize(
        vertical_title="Fashion & Retail",
        today_items=all_data,          # dict of category -> [items]
        recent_report_glob="market-trends-2026-*.md",   # prior daily reports
        fallback_report="market-trends",                # current day's file stem
    )
    report = "# ...\n\n" + synthesis + "\n\n" + <existing sections>
"""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ILMU_KEY = os.environ.get("ILMU_API_KEY", "")
ILMU_BASE_URL = "https://api.ilmu.ai/v1"
ILMU_MODEL = os.environ.get("ILMU_SYNTH_MODEL", "ilmu-glm-5.3-flash")

VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")


def _load_env():
    env = {}
    try:
        with open(os.path.expanduser("~/.hermes/.env")) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                line = line[7:].strip() if line.startswith("export ") else line
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return env


def _get_ilmu_key():
    if ILMU_KEY and "..." not in ILMU_KEY and len(ILMU_KEY) > 10:
        return ILMU_KEY
    return _load_env().get("ILMU_API_KEY", "")


def llm_chat(prompt: str, max_tokens: int = 4000) -> str:
    """Call the ilmu API (YTL AI Labs, OpenAI-compatible) for synthesis.

    2026-10-08: migrated from ollama.com cloud — `deepseek-v4-flash:0731` is
    retired there (HTTP 410) and `gpt-oss:20b` was a stopgap. ilmu is the
    platform default (Hermes config.yaml: provider ilmu, base_url
    https://api.ilmu.ai/v1, key ILMU_API_KEY). Reasoning models may land the
    answer in `reasoning`/`reasoning_content` with `content` empty, so fall
    back to those fields before giving up.
    """
    key = _get_ilmu_key()
    if not key:
        return ""
    body = {
        "model": ILMU_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "stream": False,
    }
    req = urllib.request.Request(
        ILMU_BASE_URL + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=90)
        data = json.loads(resp.read())
        msg = data.get("choices", [{}])[0].get("message", {})
        content = (msg.get("content", "") or "").strip()
        if not content:
            # Reasoning models may have put the answer in `reasoning`.
            content = ((msg.get("reasoning", "") or msg.get("reasoning_content", "")) or "").strip()
        return content
    except Exception as e:
        print(f"  ⚠️  LLM synthesis failed: {e}")
        return ""


def _read_recent_reports(glob_pattern: str, fallback_stem: str = "", max_files: int = 7):
    """Read up to max_files recent report .md files matching glob, newest first.

    Returns a compact block of prior-day headlines for context.
    """
    if not os.path.isdir(TRENDS_DIR):
        return ""
    try:
        files = list(Path(TRENDS_DIR).glob(glob_pattern))
    except Exception:
        files = []
    # Include today's fallback file too if it exists
    if fallback_stem:
        try:
            fb = Path(TRENDS_DIR) / (fallback_stem + "-" + datetime.now().strftime("%Y-%m-%d") + ".md")
            if fb.exists():
                files.append(fb)
        except Exception:
            pass
    # Newest first
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    files = files[:max_files]

    chunks = []
    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except Exception:
            continue
        body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S)
        # Keep just list items (titles) to keep the prompt small
        lines = []
        for line in body.splitlines():
            s = line.strip()
            if s.startswith("- [") or s.startswith("* [") or s.startswith("###"):
                lines.append(re.sub(r"\s+", " ", s)[:160])
        if lines:
            chunks.append(f"### {f.stem}\n" + "\n".join(lines[:15]))
    return "\n\n".join(chunks[:6])


def _flatten_items(today_items: dict):
    """Flatten {category: [items]} into a compact bulleted list for the prompt."""
    out = []
    for cat, items in today_items.items():
        if not items:
            continue
        out.append(f"**{cat}:**")
        for it in items[:8]:
            title = it.get("title", "")[:160]
            if title:
                out.append(f"- {title}")
    return "\n".join(out)


def synthesize(vertical_title: str, today_items: dict,
               recent_report_glob: str = "", fallback_report: str = "",
               max_tokens: int = 1800) -> str:
    """Produce a markdown 'Analysis & Synthesis' section for the vertical.

    Returns an empty string if the LLM is unavailable (caller falls back to the
    plain headline report).
    """
    today = datetime.now().strftime("%Y-%m-%d")
    latest = _flatten_items(today_items)
    if not latest:
        return ""  # nothing to synthesize

    context = _read_recent_reports(recent_report_glob, fallback_report) if recent_report_glob else ""

    # Look up the vertical's focus areas from the contract so the analysis
    # highlights what matters for THIS vertical (e.g. fabrics & performance
    # wear for Fashion & Retail), not just generic market commentary.
    focus = ""
    try:
        from trend_contract import VERTICALS
        for _v in VERTICALS.values():
            if _v["title"] == vertical_title:
                focus = _v.get("focus", "")
                break
    except Exception:
        focus = ""

    focus_block = ""
    if focus:
        focus_block = (
            f"This vertical's priority focus areas are: {focus}. "
            "Surface signals in these areas explicitly, even if only a couple "
            "of today's items touch them.\n"
        )

    prompt = f"""You are a market analyst writing the daily {vertical_title} trends briefing.

Synthesize TODAY's {vertical_title} news items into a sharp, insight-driven analysis.
{focus_block}Each category below mixes BOTH market-size/market-forecast reports AND market-trend /
innovation news for the same vertical — treat them as ONE category: use the size/forecast
figures to anchor the scale, and the trend news to explain direction. Do not treat market
size and market trends as separate topics; they are two lenses on the same market.
Weave in relevant context from the recent daily reports where it strengthens the narrative.

## TODAY'S ITEMS ({today})
{latest}

## RECENT DAILY REPORTS (context)
{context}

Write a markdown section titled "## 🔍 Analysis & Synthesis" with exactly these sub-parts:
- **Themes & Signals** — 3-5 bullet points: what is genuinely moving in {vertical_title}, with concrete references to the items above (cite specific market-size figures where present).
- **What's Notable** — 2-3 bullets: surprising, accelerating, or fading trends worth attention.
- **Implications** — 2-3 bullets: what this means for strategy, investment, or operators.

Keep it tight and data-grounded. Do NOT invent facts not in the items. No filler, no fluff."""
    return llm_chat(prompt, max_tokens=max_tokens)
