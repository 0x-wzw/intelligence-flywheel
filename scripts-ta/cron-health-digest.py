#!/usr/bin/env python3
"""cron-health-digest.py — aggregate every *-health.json in the vault System/
folder into one concise Telegram report.

Design notes:
- Tolerant of heterogeneous health schemas (each job writes different keys).
- Flags STALE files: a job whose health file is older than STALE_HOURS likely
  missed its last scheduled run (silent failure detection).
- Flags NON-CONFORMANT / error statuses where the job reports one.
- Prints a summary (delivered to the cron job's Telegram target by Hermes).

Run daily (e.g. 05:30) via Hermes cron as a no-agent script.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

VAULT = Path.home() / "Documents/Obsidian Vault"
SYSTEM = VAULT / "System"
STALE_HOURS = 26  # longest DAILY cron interval is 24h; >26h since last write = missed
# Jobs that run WEEKLY (Sundays) — their health files legitimately go 150h+
# between writes, so they must not trip the daily stale threshold.
WEEKLY_JOBS = frozenset({
    "loop-tuner", "signal-noise", "bias-balance-check",
    "leadership-insights",  # weekly Mon 06:00
})


def _job_key(name: str) -> str:
    """Health files are named '<job>-health.json'; strip the suffix so the
    key matches the cron job name used in WEEKLY_JOBS."""
    return name[:-len("-health")] if name.endswith("-health") else name
WEEKLY_STALE_HOURS = 168 + 12  # >7.5 days since last write = weekly job missed

# Keys that, if present and falsy/non-ok, indicate a problem worth surfacing.
STATUS_KEYS = ("status", "conformant", "ok")


def _age_hours(ts_iso: str) -> float | None:
    try:
        # Timestamps are naive local ISO (machine TZ = +08). Compare against
        # local now so future-dated files (written in +08) don't show negative age.
        s = ts_iso.replace("Z", "")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return (datetime.now() - dt).total_seconds() / 3600.0
    except Exception:
        return None


def _summarize(p: Path) -> dict:
    try:
        data = json.loads(p.read_text())
    except Exception as e:
        return {"name": p.stem, "error": f"unreadable: {e}"}

    name = p.stem
    ts = data.get("timestamp")
    age = _age_hours(ts) if ts else None

    # Pull a couple of human-meaningful signals if present.
    signals = []
    for k in ("total_articles", "items_captured", "producers_covered",
              "compliance_pct", "total_files", "notion_result"):
        if k in data:
            signals.append(f"{k}={data[k]}")

    # Status / conformance detection.
    problems = []
    if "status" in data and str(data["status"]).lower() not in ("ok", "completed", "done"):
        problems.append(f"status={data['status']}")
    if data.get("conformant") is False:
        issues = data.get("contract_issues", [])
        problems.append("non-conformant: " + "; ".join(issues) if issues else "non-conformant")
    if "error" in data:
        problems.append(str(data["error"]))

    return {
        "name": name,
        "ts": ts,
        "age": age,
        "signals": signals,
        "problems": problems,
    }


def main() -> int:
    if not SYSTEM.exists():
        print(f"🔴 System folder missing: {SYSTEM}")
        return 1

    files = sorted(SYSTEM.glob("*-health.json"))
    if not files:
        print("⚠️ No *-health.json files found.")
        return 0

    rows = [_summarize(f) for f in files]
    now = datetime.now()

    def _stale_threshold(r: dict) -> float:
        return WEEKLY_STALE_HOURS if _job_key(r["name"]) in WEEKLY_JOBS else STALE_HOURS

    stale = [r for r in rows if r.get("age") is not None
             and r["age"] > _stale_threshold(r)]
    problem_rows = [r for r in rows if r.get("problems")]
    healthy = [r for r in rows if r not in stale and r not in problem_rows
               and "error" not in r]

    lines = []
    lines.append(f"🩺 **Cron Health Digest** — {now.strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"Jobs tracked: {len(rows)} · healthy: {len(healthy)} · "
                 f"issues: {len(problem_rows)} · stale: {len(stale)}")
    lines.append("")

    if problem_rows:
        lines.append("🔴 **Issues**")
        for r in problem_rows:
            sig = " · ".join(r["signals"]) if r["signals"] else ""
            lines.append(f"• `{r['name']}`: {'; '.join(r['problems'])}"
                         + (f" ({sig})" if sig else ""))
        lines.append("")

    if stale:
        lines.append("⏰ **Stale (missed scheduled run)**")
        for r in stale:
            age = r["age"]
            age_s = f"{age:.1f}h" if age else "unknown"
            thr = _stale_threshold(r)
            lines.append(f"• `{r['name']}`: last write {age_s} ago (threshold {thr:.0f}h)")
        lines.append("")

    if healthy:
        lines.append("✅ **Healthy** (last %d)" % min(len(healthy), 12))
        for r in healthy[:12]:
            age = f"{r['age']:.1f}h" if r.get("age") is not None else "?"
            extra = (" — " + " · ".join(r["signals"])) if r["signals"] else ""
            lines.append(f"• `{r['name']}` ({age} ago){extra}")
        if len(healthy) > 12:
            lines.append(f"• …and {len(healthy) - 12} more")

    print("\n".join(lines))
    # Non-zero exit only on hard failure (missing folder); issues/stale are
    # reported in-band so the Telegram delivery still succeeds.
    return 0


if __name__ == "__main__":
    sys.exit(main())
