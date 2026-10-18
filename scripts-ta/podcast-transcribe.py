#!/usr/bin/env python3
"""
podcast-transcribe.py

Weekly capture of new episodes from key podcasts, with insight extraction.

TRANSCRIPTION IS OPTIONAL AND OFF BY DEFAULT. The plan specified Whisper via
the Groq API, but no GROQ_API_KEY is configured on this host. Rather than fail
weekly, this script degrades to metadata + show-notes extraction, which already
yields the guest, topics and episode summary. If a key is later added, set
GROQ_API_KEY (or pass --transcribe) and full transcription switches on.

Schedule: Weekly Monday 7:00 AM
Output:   Knowledge/00-Raw/podcasts/YYYY-MM-DD-podcast-insights.md
Health:   System/podcast-transcribe-health.json
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from hermes_feeds import (
    SeenStore,
    entry_datetime,
    fetch_rss,
    frontmatter_block,
    health_banner,
    write_health,
)

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
OUTPUT_DIR = VAULT_PATH / "Knowledge" / "00-Raw" / "podcasts"
SEEN_FILE = Path.home() / ".hermes" / "podcast-seen.json"

LOOKBACK_DAYS = 8  # a little over a week, so a late-Sunday drop is not missed

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_WHISPER_URL = "https://api.groq.com/openai/v1/audio/transcriptions"

# Perspective labels mirror bias_taxonomy's axes so podcast coverage counts
# toward the same diversity audit.
PODCASTS = [
    {"name": "Lex Fridman Podcast", "feed": "https://lexfridman.com/feed/podcast/",
     "perspective": "long_form_mixed"},
    {"name": "Dwarkesh Podcast", "feed": "https://www.dwarkeshpatel.com/feed",
     "perspective": "ai_research_deep"},
    {"name": "a16z Podcast", "feed": "https://feeds.simplecast.com/JGE3yC0V",
     "perspective": "venture_optimist"},
    {"name": "All-In Podcast", "feed": "https://allinchamathjason.libsyn.com/rss",
     "perspective": "venture_libertarian"},
    {"name": "Invest Like the Best", "feed": "https://feeds.megaphone.fm/investlikethebest",
     "perspective": "capital_allocation"},
    {"name": "The Ezra Klein Show", "feed": "https://feeds.simplecast.com/82FI35Px",
     "perspective": "progressive"},
    {"name": "Hard Fork", "feed": "https://feeds.simplecast.com/l2i9YnTd",
     "perspective": "tech_journalism"},
    {"name": "Latent Space", "feed": "https://api.substack.com/feed/podcast/1084089.rss",
     "perspective": "ai_engineering"},
]

# Sentences containing these are likely to carry the episode's thesis.
INSIGHT_MARKERS = [
    "argues", "explains", "predicts", "believes", "claims", "warns",
    "the key", "the reason", "the problem", "why", "how", "thesis",
    "discusses", "breaks down", "unpacks",
]

GUEST_RE = re.compile(
    r"(?:with|feat\.?|featuring|guest:?)\s+([A-Z][a-zA-Z.\-']+(?:\s+[A-Z][a-zA-Z.\-']+){1,2})"
)


def extract_guest(title: str, summary: str) -> str:
    for text in (title, summary[:300]):
        m = GUEST_RE.search(text)
        if m:
            return m.group(1)
    # "#412 – Jane Doe: Topic" pattern
    m = re.search(r"#\d+\s*[–—-]\s*([A-Z][a-zA-Z.\-']+(?:\s+[A-Z][a-zA-Z.\-']+){1,2})", title)
    return m.group(1) if m else ""


def extract_insights(summary: str, limit: int = 4) -> List[str]:
    """Pull thesis-bearing sentences out of the show notes."""
    sentences = re.split(r"(?<=[.!?])\s+", summary)
    scored = []
    for s in sentences:
        s = s.strip()
        if not (40 <= len(s) <= 320):
            continue
        score = sum(1 for m in INSIGHT_MARKERS if m in s.lower())
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored[:limit]]


def transcribe(audio_url: str) -> Optional[str]:
    """
    Transcribe an episode with Groq Whisper. Requires GROQ_API_KEY.

    Episodes are typically 1-3 hours / 50-150MB, which exceeds Groq's 25MB
    upload limit, so this needs chunking to be production-ready. It is left
    unimplemented rather than silently truncating a transcript.
    """
    if not GROQ_API_KEY:
        return None
    raise NotImplementedError(
        "Groq Whisper transcription needs audio chunking (25MB upload cap) before it "
        "can run on full episodes. Implement chunked upload before enabling --transcribe."
    )


def collect(transcribe_enabled: bool) -> Tuple[List[Dict[str, Any]], Dict[str, str], int]:
    seen = SeenStore(SEEN_FILE, ttl_days=365)
    errors: Dict[str, str] = {}
    episodes: List[Dict[str, Any]] = []
    attempted = 0

    for pod in PODCASTS:
        attempted += 1
        print(f"  Fetching {pod['name']}...")
        items, err = fetch_rss(pod["feed"], max_items=6, timeout=25)
        if err:
            errors[pod["feed"]] = err
            print(f"  ⚠️  {pod['name']} — {err}")
            continue

        for item in items:
            if not item["url"] or seen.seen(item["title"], item["url"]):
                continue
            dt = entry_datetime(item)
            if dt and dt < datetime.now(dt.tzinfo) - timedelta(days=LOOKBACK_DAYS):
                seen.add(item["title"], item["url"])
                continue

            seen.add(item["title"], item["url"])
            summary = item["summary"]
            ep = {
                "podcast": pod["name"],
                "perspective": pod["perspective"],
                "title": item["title"],
                "url": item["url"],
                "published": (item["published"] or "")[:16],
                "duration": item.get("duration", ""),
                "guest": extract_guest(item["title"], summary),
                "insights": extract_insights(summary),
                "summary": summary,
                "audio": item.get("enclosures", [""])[0] if item.get("enclosures") else "",
                "transcript": None,
            }
            if transcribe_enabled and ep["audio"]:
                ep["transcript"] = transcribe(ep["audio"])
            episodes.append(ep)

    seen.save()
    return episodes, errors, attempted


def generate_report(episodes: List[Dict[str, Any]], errors: Dict[str, str], attempted: int,
                    transcribe_enabled: bool) -> str:
    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()

    fm = frontmatter_block(
        title=f"Podcast Insights — {iso_year}-W{iso_week:02d}",
        tags=["raw", "podcasts", "intelligence", "weekly-scan"],
        extra={
            "sources": "Podcast RSS feeds",
            "episodes_captured": len(episodes),
            "feeds_failed": len(errors),
            "transcription": "enabled" if transcribe_enabled else "disabled (no GROQ_API_KEY)",
        },
    )

    by_pod: Dict[str, List[Dict[str, Any]]] = {}
    for ep in episodes:
        by_pod.setdefault(ep["podcast"], []).append(ep)

    report = fm + f"""
# 🎙️ Podcast Insights — {iso_year} Week {iso_week:02d}

**Generated:** {now.strftime('%Y-%m-%d %H:%M')}
**Window:** last {LOOKBACK_DAYS} days
**Episodes captured:** {len(episodes)} across {len(by_pod)} shows

---

{health_banner(len(episodes), errors, attempted)}

"""
    if not transcribe_enabled:
        report += """
> [!note] Transcription disabled
> No `GROQ_API_KEY` is configured, so this run captured episode metadata and
> show-note insights only. Set the key and pass `--transcribe` to enable full
> Whisper transcription (needs audio chunking first — see the script docstring).

"""

    report += "---\n\n## Episodes\n\n"

    if not episodes:
        report += "*No new episodes in this window.*\n"

    for pod_name, eps in by_pod.items():
        report += f"### {pod_name}\n\n"
        for ep in eps:
            report += f"""#### {ep['title']}

- **Guest:** {ep['guest'] or '_not detected_'}
- **Published:** {ep['published'] or 'unknown'}{'  ·  **Duration:** ' + ep['duration'] if ep['duration'] else ''}
- **Link:** [{ep['url'][:90]}]({ep['url']})
- **Perspective tag:** `{ep['perspective']}`

"""
            if ep["insights"]:
                report += "**Key points from show notes:**\n\n"
                for ins in ep["insights"]:
                    report += f"- {ins}\n"
                report += "\n"
            elif ep["summary"]:
                report += f"> {ep['summary'][:320]}…\n\n"

            if ep["transcript"]:
                report += "**Transcript excerpt:**\n\n"
                report += f"> {ep['transcript'][:600]}…\n\n"

    report += """---

## Related

- [[System/Wiki/thought-leaders-index|Thought Leaders Index]]
- [[System/Wiki/source-diversity|Source Diversity]]
- [[Knowledge/20-Reports/Intelligence-Cronjobs-Improvement-Plan|Intelligence Cronjobs Improvement Plan]]

---

*Generated by `podcast-transcribe.py` | Next run: Monday 7:00 AM*
"""
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--transcribe", action="store_true",
                    help="enable Groq Whisper transcription (requires GROQ_API_KEY)")
    args = ap.parse_args()

    transcribe_enabled = args.transcribe and bool(GROQ_API_KEY)
    if args.transcribe and not GROQ_API_KEY:
        print("⚠️  --transcribe requested but GROQ_API_KEY is not set; metadata only.")

    print(f"Starting podcast scan: {datetime.now().isoformat()}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    episodes, errors, attempted = collect(transcribe_enabled)
    report = generate_report(episodes, errors, attempted, transcribe_enabled)

    output_file = OUTPUT_DIR / f"{datetime.now().strftime('%Y-%m-%d')}-podcast-insights.md"
    output_file.write_text(report)

    write_health(
        "podcast-transcribe",
        {
            "episodes_captured": len(episodes),
            "feeds_attempted": attempted,
            "feeds_failed": len(errors),
            "errors": errors,
            "transcription_enabled": transcribe_enabled,
            "output": str(output_file),
            "status": "ok" if episodes else "zero-items",
        },
    )

    print(f"✅ Podcast scan complete: {output_file}")
    print(f"   - Episodes: {len(episodes)}")
    if errors:
        print(f"   - ⚠️  Failed feeds: {len(errors)}")

    # Write to cron log file for traceability
    log_dir = Path(os.path.expanduser("~/.hermes/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "podcast-transcribe.log"
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | episodes={len(episodes)} feeds={attempted} failed={len(errors)} transcribe={transcribe_enabled}\n")


if __name__ == "__main__":
    main()
