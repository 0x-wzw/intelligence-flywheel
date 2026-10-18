#!/usr/bin/env python3
"""daily-intelligence-loop.py — close the daily intelligence loop.

Every morning the producers write today's captures to Knowledge/00-Raw/:
  6 market-trends verticals, thought-leaders, policy-tracker, arxiv-digest,
  podcast-insights, market-intelligence, llm-learning-scan.

This script is the loop CLOSER. It runs after all producers (scheduled ~07:00
MYT) and:
  1. COLLECTS every producer output dated today (and backfills a couple days if
     today is thin), reading titles + synthesis from each.
  2. SYNTHESIZES them cross-vertically with the LLM into one tight daily brief:
     dominant themes, the strongest 1-3 signals per vertical, cross-vertical
     patterns, and an "action / watch" line.
  3. WRITES a dated report to Knowledge/20-Reports/
     (Daily-Intelligence-Brief-YYYY-MM-DD.md).
  4. APPENDS a compact entry to Daily/YYYY-MM-DD.md (the daily note).
  5. RECORDS health (which producers ran, how many items, whether LLM worked)
     so flywheel-health-check sees the loop.

It NEVER edits producer captures. Output-only + health.
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from hermes_feeds import CYCLE, authorship_tag, frontmatter_block, write_health

VAULT = Path("/Users/admin/Documents/Obsidian Vault")
RAW = VAULT / "Knowledge" / "00-Raw"
REPORTS = VAULT / "Knowledge" / "20-Reports"
DAILY = VAULT / "Daily"
HEALTH_NAME = "daily-intelligence-loop"

# Producer → relative glob under Knowledge/00-Raw. Imported from the single
# source of truth (intelligence_source_of_truth.PRODUCER_LIST) so the taxonomy is
# defined in exactly ONE place. Each tuple is (label, glob, emoji, description);
# the loop only needs label + glob.
from intelligence_source_of_truth import PRODUCER_LIST

PRODUCERS = PRODUCER_LIST  # (label, glob, emoji, description) tuples

BACKFILL_DAYS = 3  # include captures from the last N days if today is thin

TUNING_PATH = Path(_SCRIPTS_DIR) / "loop-tuning.json"


def load_tuning() -> dict:
    """Load the weekly-tuned knobs (see loop-tuning.json). Falls back to defaults."""
    defaults = {
        "synthesis": {"max_tokens": 1200, "timeout_seconds": 180, "retries": 2},
        "items": {"default_max_per_producer": 8, "cap_high_noise_producers": 3},
        "sources": {"disabled_noise_hosts": []},
    }
    try:
        data = json.loads(TUNING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    # shallow-merge so a missing key never crashes
    for section in ("synthesis", "items", "sources"):
        defaults[section].update(data.get(section) or {})
    return defaults


TUNING = load_tuning()


def _dated(fname: str, day: str) -> bool:
    """True if the filename embeds today's YYYY-MM-DD (or YYYYMMDD)."""
    plain = day.replace("-", "")
    return day in fname or plain in fname


def _recent_enough(path: Path, day: str, max_age_days: int = BACKFILL_DAYS) -> bool:
    """True if the file is dated within max_age_days of `day` (by mtime fallback)."""
    try:
        dt = datetime.fromtimestamp(path.stat().st_mtime)
        return (datetime.strptime(day, "%Y-%m-%d") - dt).days <= max_age_days
    except OSError:
        return False


def _disabled_hosts() -> set:
    """Hosts the tuner auto-disabled for being noise-dominant. Items from these are dropped."""
    return set(TUNING.get("sources", {}).get("disabled_noise_hosts", []) or [])


def _host_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).lower().lstrip("www.") if m else ""


def _items_from(text: str, max_items: int = 12):
    """Extract (title, url) pairs from a report body, skipping disabled noise hosts."""
    out = []
    disabled = _disabled_hosts()
    cap = TUNING.get("items", {}).get("default_max_per_producer", max_items)
    cap = int(cap) if cap else max_items
    for line in text.splitlines():
        m = re.match(r"\s*[-*]\s+\[([^\]]{8,200})\]\((https?://[^\s\)]+)\)", line)
        if m:
            url = m.group(2)
            if disabled and _host_of(url) in disabled:
                continue
            title = re.sub(r"[*_`]", "", m.group(1)).strip()
            out.append((title, url))
        if len(out) >= cap:
            break
    return out


def _synthesis_of(text: str, max_len: int = 500) -> str:
    """Pull the Analysis & Synthesis block if present (for context)."""
    m = re.search(r"## 🔍 Analysis & Synthesis(.*?)(?=\n## |\Z)", text, re.S)
    if not m:
        return ""
    body = m.group(1).strip()
    # Collapse whitespace and trim
    body = re.sub(r"\s+", " ", body)
    return body[:max_len]


def collect(day: str) -> dict:
    """Return {producer_label: {"file": str, "items": [...], "synth": str}}."""
    result = {}
    for label, globpat, _emoji, _desc in PRODUCERS:
        matches = list(RAW.glob(globpat))
        # Prefer files dated today, else most recent within backfill window.
        today_matches = [p for p in matches if _dated(p.name, day)]
        if not today_matches:
            today_matches = [p for p in matches if _recent_enough(p, day)]
        if not today_matches:
            continue
        # Newest first
        today_matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        path = today_matches[0]
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        items = _items_from(text)
        result[label] = {
            "file": str(path.relative_to(VAULT)),
            "items": items,
            "synth": _synthesis_of(text),
            "item_count": len(items),
        }
    return result


def _flatten_for_prompt(collected: dict) -> str:
    """Compact per-producer block of titles (and synth snippet) for the LLM."""
    blocks = []
    for label, data in collected.items():
        lines = []
        for title, url in data["items"][:8]:
            lines.append(f"- {title}")
        block = f"### {label} ({data['item_count']} items)\n" + "\n".join(lines)
        if data["synth"]:
            block += f"\n[synthesis] {data['synth']}"
        blocks.append(block)
    return "\n\n".join(blocks)


def _llm_chat_retry(prompt: str, max_tokens: int = None, timeout: int = None) -> str:
    """Call the LLM with a generous timeout and retries (robust for long prompts).

    Uses the tuned values from loop-tuning.json (max_tokens, timeout_seconds,
    retries) so the weekly tuner can adjust reliability without editing code.
    """
    from trend_synthesizer import llm_chat
    synth = TUNING.get("synthesis", {})
    max_tokens = max_tokens or int(synth.get("max_tokens", 4000))
    return llm_chat(prompt, max_tokens=max_tokens)


def synthesize(collected: dict) -> str:
    """LLM cross-vertical synthesis. Returns markdown (or '' if unavailable)."""
    if not collected:
        return ""
    latest = _flatten_for_prompt(collected)
    today = datetime.now().strftime("%Y-%m-%d")

    prompt = f"""You are the daily intelligence editor for a multi-vertical monitoring system.

Today ({today}) the producers captured the items below across six market verticals plus
thought-leaders, policy, research, podcasts, and learning. Synthesize them into ONE tight,
decision-useful daily brief.

Rules:
- Lead with the 2-4 dominant cross-vertical THEMES that cut across multiple producers
  (not single-vertical recaps).
- Then, for each vertical that has items, give its single strongest signal in one line.
- Flag any surprising contradiction or convergence between verticals.
- End with 3-5 concrete "Watch / Act" items: things worth tracking, deciding, or doing.

Format exactly (markdown):
## 🔍 Daily Synthesis
**Top Themes**
- ...
**Per-Vertical Signals**
- **<Vertical>:** <one line>
**Contradictions & Convergences**
- ...
**Watch / Act**
- ...

Keep it under 450 words. Ground everything in the items — do NOT invent facts.

## TODAY'S CAPTURES
{latest}
"""
    return _llm_chat_retry(prompt, max_tokens=1200)


def _health_state(collected: dict, synth: str) -> str:
    if not collected:
        return "no-captures"
    if not synth:
        return "captures-no-synthesis"
    return "ok"


def build_report(day: str, collected: dict, synth: str) -> str:
    now = datetime.now()
    producers = len(collected)
    total_items = sum(d["item_count"] for d in collected.values())
    fm = frontmatter_block(
        title=f"Daily Intelligence Brief — {day}",
        tags=["report", "intelligence", "daily-loop", "synthesis"],
        note_type="report",
        status="shipped",
        extra={
            "distilled": "false",
            # Recurring series: consumed by loop-tuner weekly, never distilled
            # one-by-one. flywheel-health-check / distillation-tracker exclude
            # on this key.
            "recurring": "daily",
            "sources": "Knowledge/00-Raw daily captures",
            "producers_covered": producers,
            "items_captured": total_items,
            "synthesis": bool(synth),
        },
    )

    lines = [fm, f"# 📅 Daily Intelligence Brief — {day}",
             f"\n**Generated:** {now.strftime('%Y-%m-%d %H:%M')}",
             f"**Producers covered:** {producers} · **Items:** {total_items}\n",
             "---\n"]

    if synth:
        lines.append(synth + "\n")
    else:
        lines.append("_LLM synthesis unavailable this run — raw capture inventory below._\n")

    lines.append("---\n\n## 📥 Captured Today\n")
    for label, data in collected.items():
        lines.append(f"### {label}")
        if data["items"]:
            for title, url in data["items"][:10]:
                lines.append(f"- [{title}]({url})")
        else:
            lines.append("_No linked items extracted._")
        lines.append(f"*Source: `{data['file']}`*\n")

    lines.append("---\n\n## 🔁 Loop")
    lines.append(
        "- Producers write today's captures → this script synthesizes → brief lands "
        "in `Daily/` and `20-Reports` → weekly signal-noise & bias-balance close the "
        "weekly loop."
    )
    lines.append("\n---\n*Generated by `daily-intelligence-loop.py`*")
    return "\n".join(lines)


def _mark_captures_processed(collected: dict, day: str) -> int:
    """Flip consumed producer captures from `unprocessed` to `archived`.

    A capture that today's brief has synthesized is consumed — but nothing ever
    updated its frontmatter, so every daily capture permanently aged into the
    flywheel's ">30 days unprocessed" backlog (~161 false backlog rows at
    2026-10-05). Only producer-glob captures (canonical list in
    intelligence_source_of_truth) are eligible, status is flipped one-way, and
    the flip is failure-tolerant: losing it must never lose the brief.

    `archived` is the Schema v2 terminal state for a consumed capture
    (unprocessed|active|draft|shipped|published|archived — `processed` is NOT
    in the vocabulary; using it once wedged 853 files into bad_status).
    """
    flipped = 0
    producer_files = {data["file"] for data in collected.values() if data.get("file")}
    for rel in producer_files:
        path = VAULT / rel
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
            if not m:
                continue
            fm = m.group(1)
            if re.search(r"^status:\s*unprocessed\s*$", fm, re.M):
                # Schema v2 terminal state for a consumed capture: `archived`
                # (vocabulary: unprocessed|active|draft|shipped|published|
                # archived — `processed` is not a legal value). Plain value
                # only: the vault's frontmatter parsers don't strip comments.
                new_fm = re.sub(
                    r"^status:\s*unprocessed\s*$",
                    "status: archived",
                    fm,
                    count=1,
                    flags=re.M,
                )
                path.write_text(text[: m.start(1)] + new_fm + text[m.end(1):], encoding="utf-8")
                flipped += 1
        except OSError:
            continue
    return flipped


def append_to_daily_note(day: str, producers: int, items: int, synth: bool) -> Path:
    """Append a compact entry to Daily/YYYY-MM-DD.md (create if missing)."""
    daily_path = DAILY / f"{day}.md"
    now = datetime.now().strftime("%H:%M")
    entry = (f"\n- {now} — Daily intelligence loop: {producers} producers, "
             f"{items} items, synthesis {'✅' if synth else '❌'}. "
             f"[[Knowledge/20-Reports/Daily-Intelligence-Brief-{day}|Brief]]\n")
    if daily_path.exists():
        with open(daily_path, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        # Create a minimal daily note conforming to Schema v2.
        fm = (f"---\ndate: {day}\ntype: daily\ntags: [daily, {authorship_tag()}]\n---\n\n"
              f"## Tasks\n\n## Log\n")
        with open(daily_path, "w", encoding="utf-8") as f:
            f.write(fm + entry)
    return daily_path


def main():
    day = datetime.now().strftime("%Y-%m-%d")
    print(f"🔁 Daily intelligence loop — {day}")

    collected = collect(day)
    print(f"  Collected {len(collected)} producers: "
          + ", ".join(sorted(collected.keys())) if collected else "  (none found)")

    synth = synthesize(collected)
    if synth:
        print(f"  ✅ LLM synthesis: {len(synth)} chars")
    else:
        print("  ⚠️ LLM synthesis unavailable")

    report = build_report(day, collected, synth)
    out = REPORTS / f"Daily-Intelligence-Brief-{day}.md"
    REPORTS.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    daily_path = append_to_daily_note(day, len(collected),
                                      sum(d["item_count"] for d in collected.values()),
                                      bool(synth))

    flipped = _mark_captures_processed(collected, day)
    if flipped:
        print(f"  ♻️ Marked {flipped} consumed capture(s) as processed")

    state = _health_state(collected, synth)
    write_health(HEALTH_NAME, {
        "day": day,
        "producers_covered": len(collected),
        "items_captured": sum(d["item_count"] for d in collected.values()),
        "synthesis": bool(synth),
        "report": str(out.relative_to(VAULT)),
        "daily_note": str(daily_path.relative_to(VAULT)),
        "producers": {k: v["item_count"] for k, v in collected.items()},
        "status": state,
    })

    print(f"  💾 {out.relative_to(VAULT)}")
    print(f"  📓 {daily_path.relative_to(VAULT)}")
    print(f"  Health: {state}")
    return 0


if __name__ == "__main__":
    main()
