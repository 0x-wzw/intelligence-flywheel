#!/usr/bin/env python3
"""
llm-learning-scan.py

Daily continuous-learning scan for LLM-related topics. Fetches from multiple
sources (arXiv, Hugging Face papers, AI lab blogs, thought-leader feeds) and
saves a structured report to the vault's Knowledge/00-Raw/trends/ directory.

Covers:
  - LLM architecture & scaling (MoE, attention, scaling laws)
  - Prompt engineering & techniques
  - Agentic AI & multi-agent systems
  - Open vs closed model dynamics
  - AI research breakthroughs (arXiv)
  - Model releases & capability updates

Schedule: Daily 06:00 via cron (runs after the ai-science-investment-trends job)
Output:   Knowledge/00-Raw/trends/llm-learning-scan-{YYYY-MM-DD}.md
Health:   System/llm-learning-scan-health.json
"""
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime

# Ensure scripts dir on path for dedup
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from dedup import SeenUrls

VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
from hermes_feeds import authorship_tag  # single source for the authorship tag

TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "llm-learning-scan-health.json")
MOC_PATH = os.path.join(VAULT_DIR, "Personal", "Projects", "🧠 LLM Learning & Research.md")

FRONTMATTER = """---
title: LLM Learning & Research Scan — {date}
type: raw
domain: knowledge
status: unprocessed
cycle: 2026-Q4
created: {date}
tags: [raw, trends, llm, learning, research, {author}]
---
"""

# ── Query Categories ─────────────────────────────────────────────────
QUERIES = {
    "architecture": "LLM architecture scaling laws MoE mixture of experts attention 2026",
    "prompt_eng": "prompt engineering techniques chain of thought reasoning LLM 2026",
    "agentic": "autonomous AI agents multi-agent orchestration tool use 2026",
    "open_closed": "open weights vs closed models DeepSeek Llama Qwen frontier 2026",
    "research": "arXiv LLM breakthrough reasoning alignment training 2026",
    "models": "new model release GPT Claude Gemini DeepSeek Qwen Llama 2026",
    "infra": "LLM inference optimization serving vLLM TensorRT deployment 2026",
    "fine_tuning": "fine-tuning LoRA RLHF DPO preference optimization LLM 2026",
}

SECTION_TITLES = {
    "architecture": "## 🏗 Architecture & Scaling",
    "prompt_eng": "## 💬 Prompt Engineering",
    "agentic": "## 🤖 Agentic AI & Multi-Agent",
    "open_closed": "## 🔓 Open vs Closed Models",
    "research": "## 🔬 Research Breakthroughs",
    "models": "## 🚀 Model Releases",
    "infra": "## ⚡ Inference & Serving",
    "fine_tuning": "## 🎯 Fine-Tuning & Alignment",
}

# ── RSS Feeds ──────────────────────────────────────────────────────────
RSS_FEEDS = {
    "ai_labs": [
        "https://openai.com/rss",
        "https://www.anthropic.com/rss",
        "https://ai.meta.com/blog/rss",
        "https://deepmind.google/discover/blog/rss",
    ],
    "research": [
        "https://huggingface.co/papers",
        "https://www.lesswrong.com/feed.xml",
    ],
    "newsletters": [
        "https://stratechery.com/feed",
        "https://www.interconnects.ai/feed",
        "https://import.substack.com/feed",
    ],
}


def fetch_news(query, max_results=5):
    """Fetch news from Google News RSS."""
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        data = urllib.request.urlopen(url, timeout=12).read().decode("utf-8")
        items = re.findall(
            r"<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?<pubDate>(.*?)</pubDate>.*?</item>",
            data, re.DOTALL,
        )
        results = []
        for title, link, date in items[:max_results]:
            clean = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            results.append({"title": clean, "url": link.strip(), "date": date.strip()})
        return results
    except Exception as e:
        return [{"title": f"Error: {e}", "url": "", "date": ""}]


def fetch_rss(url, max_items=3):
    """Fetch and parse RSS feed (lightweight, no external deps)."""
    try:
        data = urllib.request.urlopen(url, timeout=10).read().decode("utf-8", "utf-8")
        items = re.findall(
            r"<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?(?:<pubDate>(.*?)</pubDate>)?.*?</item>",
            data, re.DOTALL,
        )
        results = []
        for title, link, date in items[:max_items]:
            clean = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            d = date.strip() if date else ""
            results.append({"title": clean, "url": link.strip(), "date": d, "source": "rss"})
        return results
    except Exception:
        return []


def build_report():
    """Fetch all categories and build structured markdown report."""
    seen = SeenUrls()
    seen.load()

    # ── News queries ──
    all_data = {}
    for category, query in QUERIES.items():
        items = fetch_news(query, max_results=4)
        new_items = []
        for item in items:
            if item["url"] and seen.is_new(item["url"], item.get("date", "")):
                new_items.append(item)
                seen.mark(item["url"], item.get("title", ""))
        all_data[category] = new_items

    # ── RSS feeds ──
    rss_items = []
    for group, feeds in RSS_FEEDS.items():
        for feed_url in feeds:
            items = fetch_rss(feed_url, max_items=3)
            for item in items:
                if item["url"] and seen.is_new(item["url"], item.get("date", "")):
                    item["group"] = group
                    rss_items.append(item)
                    seen.mark(item["url"], item.get("title", ""))

    seen.save()

    today = datetime.now().strftime("%Y-%m-%d")
    report = FRONTMATTER.format(date=today, author=authorship_tag())
    report += f"# 🧠 LLM Learning & Research Scan — {today}\n\n"
    report += f"> Daily continuous-learning scan for LLM topics. Feeds the [[🧠 LLM Learning & Research]] MOC.\n\n"

    # ── News sections ──
    for key, header in SECTION_TITLES.items():
        report += header + "\n"
        for item in all_data.get(key, []):
            report += f"- [{item['title']}]({item['url']})\n"
        if not all_data.get(key):
            report += "*No new items.*\n"
        report += "\n"

    # ── RSS section ──
    report += "## 📡 From AI Lab Blogs & Newsletters\n\n"
    if rss_items:
        for item in rss_items:
            report += f"- [{item['title']}]({item['url']}) ({item.get('group', 'rss')})\n"
    else:
        report += "*No new items.*\n"
    report += "\n"

    # ── Observations ──
    total_news = sum(len(v) for v in all_data.values())
    total = total_news + len(rss_items)
    report += "---\n"
    report += f"## 📊 Summary\n\n"
    report += f"- **Total new items:** {total}\n"
    report += f"- **News queries:** {total_news} across {len(QUERIES)} categories\n"
    report += f"- **RSS items:** {len(rss_items)}\n"
    report += f"- **Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
    report += f"---\n*Auto-generated by `llm-learning-scan.py` cron job.*\n"

    return report, all_data, rss_items, total


def save_report(report):
    """Save report to vault trends directory."""
    os.makedirs(TRENDS_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(TRENDS_DIR, f"llm-learning-scan-{today}.md")
    with open(path, "w") as f:
        f.write(report)
    return path


def check_moc_links():
    """Check if the latest scan is linked in the MOC."""
    today = datetime.now().strftime("%Y-%m-%d")
    scan_name = f"llm-learning-scan-{today}"
    try:
        with open(MOC_PATH, "r") as f:
            moc_content = f.read()
        if scan_name not in moc_content:
            # Append a link under the AI Research section
            link_line = f"- [[Knowledge/00-Raw/trends/{scan_name}|LLM Scan — {today}]]\n"
            # Find the AI Research section and append
            if "## 🔬 AI Research & Breakthroughs" in moc_content:
                moc_content = moc_content.replace(
                    "## 🔬 AI Research & Breakthroughs\n",
                    f"## 🔬 AI Research & Breakthroughs\n{link_line}\n",
                )
            else:
                # Append at end
                moc_content += f"\n## Daily Scans\n{link_line}\n"
            with open(MOC_PATH, "w") as f:
                f.write(moc_content)
            return f"Added link to MOC"
    except Exception as e:
        return f"MOC update skipped: {e}"
    return "MOC already has link"


def main():
    report, all_data, rss_items, total = build_report()
    saved = save_report(report)
    moc_status = check_moc_links()

    health = {
        "timestamp": datetime.now().isoformat(),
        "total_items": total,
        "categories": {k: len(v) for k, v in all_data.items()},
        "rss_items": len(rss_items),
        "report_path": saved,
        "moc_status": moc_status,
    }
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f:
        json.dump(health, f, indent=2)

    print(f"🧠 LLM Learning & Research Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  📰 {total} new items")
    for cat, items in all_data.items():
        print(f"    {cat}: {len(items)} items")
    print(f"  📡 RSS: {len(rss_items)} items")
    print(f"  💾 Saved: {saved}")
    print(f"  🔗 {moc_status}")

    return 0


if __name__ == "__main__":
    main()