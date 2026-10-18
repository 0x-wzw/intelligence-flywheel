#!/usr/bin/env python3
"""loop-tuner.py — the self-improvement step for the daily intelligence loop.

Runs weekly (Sunday, after bias-balance + signal-noise, e.g. 10:00 MYT). It reads
the week's health signals and decides how to make the loop deliver better insights:

  INPUT  (health JSONs + tuning state)
    - daily-intelligence-loop-health.json   (per-day: producers, items, synthesis ok?)
    - signal-noise-health.json              (signal/medium/noise counts, signal ratio)
    - bias-balance-check-health.json        (diversity score, unclassified hosts)
    - loop-tuning.json                      (current knobs, noise-host disables, history)

  OUTPUT
    - Knowledge/20-Reports/Loop-Improvement-YYYY-Www.md   (the report)
    - loop-tuning.json updated                              (applied changes + history)
    - System/loop-tuner-health.json                         (this run's health)

SAFETY: only AUTO-APPLIES reversible, low-risk changes:
    - adding a host to `disabled_noise_hosts` (reversible by removing it)
    - tightening/loosening synthesis retries/timeout within safe bounds
    - bumping item caps within bounds
  Everything else (source additions, taxonomy additions, prompt changes) is
  written as a RECOMMENDATION in the report for a human to approve.
"""
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from hermes_feeds import CYCLE, frontmatter_block, write_health

VAULT = Path("/Users/admin/Documents/Obsidian Vault")
SYSTEM = VAULT / "System"
REPORTS = VAULT / "Knowledge" / "20-Reports"
TUNING_PATH = Path(_SCRIPTS_DIR) / "loop-tuning.json"

HEALTH_NAMES = {
    "loop": "daily-intelligence-loop-health.json",
    "signal_noise": "signal-noise-health.json",
    "bias": "bias-balance-check-health.json",
}

# ── Safe tuning bounds (tunable by editing here) ─────────────────────
SAFE = {
    "synthesis_retries_min": 1,
    "synthesis_retries_max": 4,
    "synthesis_timeout_min": 120,
    "synthesis_timeout_max": 300,
    "max_tokens_min": 800,
    "max_tokens_max": 2000,
    "item_cap_min": 3,
    "item_cap_max": 12,
}
NOISE_HOST_DISABLE_THRESHOLD = 20   # a host with this many noise items in one week
SIGNAL_RATIO_TARGET = 15.0
DIVERSITY_TARGET = 7.0


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_tuning() -> dict:
    try:
        data = json.loads(TUNING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    data.setdefault("synthesis", {})
    data.setdefault("items", {})
    data.setdefault("sources", {"disabled_noise_hosts": []})
    data.setdefault("history", {"weeks": []})
    data.setdefault("applied", {"changes": []})
    return data


def save_tuning(data: dict):
    TUNING_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def parse_noise_hosts(signal_report_text: str):
    """Pull {host: count} from the 'Noise Patterns' table in a Signal-Noise report."""
    hosts = {}
    m = re.search(r"## ⚪ Noise Patterns.*?\|\s*Host\s*\|\s*Noise items\s*\|\s*\|[-| ]+\|(.*?)(?=\n## |\Z)", signal_report_text, re.S)
    if not m:
        return hosts
    for row in m.group(1).strip().splitlines():
        rm = re.match(r"\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|", row)
        if rm:
            host = rm.group(1).lower().lstrip("www.")
            hosts[host] = int(rm.group(2))
    return hosts


def parse_unclassified_hosts(bias_report_text: str):
    """Pull host list from the 'Unclassified Sources' table in a Bias-Balance report."""
    hosts = []
    m = re.search(r"## 🏷️ Unclassified Sources.*?\|\s*Host\s*\|\s*Links\s*\|\s*\|[-| ]+\|(.*?)(?=\n## |\Z)", bias_report_text, re.S)
    if not m:
        return hosts
    for row in m.group(1).strip().splitlines():
        rm = re.match(r"\|\s*`([^`]+)`", row)
        if rm:
            hosts.append(rm.group(1).lower().lstrip("www."))
    return hosts


def compute_recommendations(loop_h, sn_h, bias_h, tuning, latest_sn_report, latest_bias_report) -> dict:
    """Return {auto: [...], recommend: [...]}."""
    auto, recommend = [], []
    noise_hosts = parse_noise_hosts(latest_sn_report)
    disabled = set(tuning["sources"]["disabled_noise_hosts"])

    # 1. Auto-disable noise-dominant hosts (reversible)
    for host, count in sorted(noise_hosts.items(), key=lambda x: -x[1]):
        if host in ("news.google.com", "openalex.org", "arxiv.org"):
            # these are core aggregators/academic — never auto-disable, only flag
            if count >= NOISE_HOST_DISABLE_THRESHOLD:
                recommend.append(f"`{host}` had {count} noise items. It's a core source — review whether it's earning its place (e.g. tighten its query).")
            continue
        if count >= NOISE_HOST_DISABLE_THRESHOLD and host not in disabled:
            auto.append(f"Disabled noise-dominant host `{host}` ({count} noise items). Reversible: remove from `loop-tuning.json` → sources.disabled_noise_hosts.")
            disabled.add(host)
            # Persist the disable so the daily loop actually drops this host.
            tuning["sources"]["disabled_noise_hosts"].append(host)

    # 2. Signal ratio — if well below target, recommend curation tightening
    if sn_h:
        ratio = sn_h.get("signal_ratio_pct")
        if ratio is not None and ratio < SIGNAL_RATIO_TARGET * 0.5:
            recommend.append(f"Signal ratio is {ratio}% vs {SIGNAL_RATIO_TARGET}% target. Curation is too loose — consider tightening per-producer item caps or removing low-signal feeds.")
        elif ratio is not None and ratio >= SIGNAL_RATIO_TARGET:
            auto.append(f"Signal ratio {ratio}% is healthy (≥{SIGNAL_RATIO_TARGET}%). No curation tightening.")

    # 3. Synthesis reliability — escalate/relax based on the week's loop health
    if loop_h:
        synth = loop_h.get("synthesis")
        if synth is False:
            # synthesis failed on the last run — escalate
            cur = tuning["synthesis"].get("retries", 2)
            new = min(cur + 1, SAFE["synthesis_retries_max"])
            if new != cur:
                auto.append(f"Last loop run had no synthesis — bumped retries {cur}→{new}.")
                tuning["synthesis"]["retries"] = new

    # 4. Bias/unclassified — recommend taxonomy additions (not auto, needs human)
    if bias_h:
        unclassified = bias_h.get("hosts_unclassified", 0)
        total = bias_h.get("hosts_total", 0)
        if total and unclassified / total > 0.5:
            recommend.append(f"{unclassified}/{total} source hosts are unclassified. Add them to `bias_taxonomy.py` SOURCE_PERSPECTIVES so bias scoring is meaningful.")
        score = bias_h.get("overall_diversity_score")
        if score is not None and score < DIVERSITY_TARGET:
            recommend.append(f"Diversity score {score}/10 is below target {DIVERSITY_TARGET}/10. Add the recommended voices from the Bias-Balance report's 'Gaps' section.")
        for host in parse_unclassified_hosts(latest_bias_report):
            recommend.append(f"Unclassified host `{host}` — add a perspective entry in `bias_taxonomy.py`.")

    return {"auto": auto, "recommend": recommend}


def build_report(week_label, recs, kpis, latest_sn, latest_bias) -> str:
    fm = frontmatter_block(
        title=f"Loop Improvement — {week_label}",
        tags=["report", "intelligence", "loop-tuning", "self-improvement"],
        note_type="report",
        status="shipped",
        extra={
            "distilled": "false",
            # Weekly self-improvement report; its insights ARE the tuning
            # (loop-tuning.json). Excluded from distillation backlogs on this key.
            "recurring": "weekly",
            "auto_changes": len(recs["auto"]),
            "recommendations": len(recs["recommend"]),
            "signal_ratio_pct": kpis.get("signal_ratio_pct"),
            "diversity_score": kpis.get("diversity_score"),
        },
    )
    lines = [fm, f"# 🔁 Loop Improvement — {week_label}",
             f"\n**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             f"**Window:** last 7 days\n", "---\n"]

    lines.append("## 📊 This Week's KPIs\n")
    lines.append("| Metric | Value | Target |")
    lines.append("|--------|-------|--------|")
    lines.append(f"| Signal ratio | {kpis.get('signal_ratio_pct', 'n/a')}% | {SIGNAL_RATIO_TARGET}% |")
    lines.append(f"| Diversity score | {kpis.get('diversity_score', 'n/a')}/10 | {DIVERSITY_TARGET}/10 |")
    lines.append(f"| Loop synthesis OK (last run) | {kpis.get('synthesis')} | ✅ |")
    lines.append(f"| Producers covered (last run) | {kpis.get('producers_covered', 'n/a')} | ≥6 |")
    lines.append("")

    lines.append("---\n\n## ✅ Auto-Applied Changes\n")
    if recs["auto"]:
        for a in recs["auto"]:
            lines.append(f"- {a}")
    else:
        lines.append("*No safe changes were needed this week.*")
    lines.append("")

    lines.append("---\n\n## 🤝 Recommended (needs your review)\n")
    if recs["recommend"]:
        for r in recs["recommend"]:
            lines.append(f"- {r}")
    else:
        lines.append("*No open recommendations.*")
    lines.append("")

    lines.append("---\n\n## 🔬 Detail\n")
    lines.append(f"- **Latest Signal-Noise report:** `{latest_sn}`")
    lines.append(f"- **Latest Bias-Balance report:** `{latest_bias}`")
    lines.append(f"- **Tuning state:** `loop-tuning.json` (history: {len(kpis.get('history_weeks', []))} weeks)")

    lines.append("\n---\n*Generated by `loop-tuner.py` — the daily loop reads `loop-tuning.json` for its knobs.*")
    return "\n".join(lines)


def main():
    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()
    week_label = f"{iso_year}-W{iso_week:02d}"

    tuning = load_tuning()
    loop_h = _read_json(SYSTEM / HEALTH_NAMES["loop"])
    sn_h = _read_json(SYSTEM / HEALTH_NAMES["signal_noise"])
    bias_h = _read_json(SYSTEM / HEALTH_NAMES["bias"])

    # Latest weekly report paths (for detail + host parsing)
    latest_sn = Path(str(sn_h.get("output", ""))) if sn_h else None
    latest_bias = Path(str(bias_h.get("output", ""))) if bias_h else None
    sn_text = latest_sn.read_text(errors="ignore") if latest_sn and latest_sn.exists() else ""
    bias_text = latest_bias.read_text(errors="ignore") if latest_bias and latest_bias.exists() else ""

    recs = compute_recommendations(loop_h, sn_h, bias_h, tuning, sn_text, bias_text)

    # Record applied changes in tuning state
    for change in recs["auto"]:
        tuning["applied"]["changes"].append({"week": week_label, "change": change, "ts": now.isoformat()})
    # Record weekly KPI in history (keep last 12)
    tuning["history"]["weeks"].append({
        "week": week_label,
        "ts": now.isoformat(),
        "signal_ratio_pct": (sn_h or {}).get("signal_ratio_pct"),
        "diversity_score": (bias_h or {}).get("overall_diversity_score"),
        "loop_synthesis": (loop_h or {}).get("synthesis"),
        "producers_covered": (loop_h or {}).get("producers_covered"),
        "auto_changes": len(recs["auto"]),
        "recommendations": len(recs["recommend"]),
    })
    tuning["history"]["weeks"] = tuning["history"]["weeks"][-12:]
    save_tuning(tuning)

    kpis = {
        "signal_ratio_pct": (sn_h or {}).get("signal_ratio_pct"),
        "diversity_score": (bias_h or {}).get("overall_diversity_score"),
        "synthesis": (loop_h or {}).get("synthesis"),
        "producers_covered": (loop_h or {}).get("producers_covered"),
        "history_weeks": tuning["history"]["weeks"],
    }

    report = build_report(week_label, recs, kpis,
                          str(latest_sn.name) if latest_sn else "n/a",
                          str(latest_bias.name) if latest_bias else "n/a")
    out = REPORTS / f"Loop-Improvement-{week_label}.md"
    REPORTS.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")

    write_health("loop-tuner", {
        "week": week_label,
        "auto_changes": len(recs["auto"]),
        "recommendations": len(recs["recommend"]),
        "output": str(out.relative_to(VAULT)),
        "disabled_noise_hosts": tuning["sources"]["disabled_noise_hosts"],
        "status": "ok",
    })

    print(f"🔁 Loop tuner — {week_label}")
    print(f"  ✅ Auto-applied: {len(recs['auto'])}")
    for a in recs["auto"]:
        print(f"     - {a}")
    print(f"  🤝 Recommended: {len(recs['recommend'])}")
    for r in recs["recommend"][:6]:
        print(f"     - {r}")
    print(f"  💾 {out.relative_to(VAULT)}")
    return 0


if __name__ == "__main__":
    main()
