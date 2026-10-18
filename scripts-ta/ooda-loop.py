#!/usr/bin/env python3
"""ooda-loop.py — drive the OODA intelligence loop for MUI through 4 bots.

Observe → Orient → Decide → Act, each stage a Hermes bot profile that hands
its output to the next. Faithful to the MUI AI doctrine: the loop OBSERVES,
ORIENTS, and PREPARES a decision packet; a human (CTO) gives the final yes/no;
only then does Act execute. This cron never auto-acts — it delivers the packet
to Telegram and waits for the human gate.

Runs daily (e.g. 08:30, after the intelligence pipeline closes at 08:15).
Writes a dated OODA report to Knowledge/20-Reports/OODA-Brief-YYYY-MM-DD.md.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERMES = Path(os.path.expanduser("~/.hermes"))
VAULT = Path(os.path.expanduser("~/Documents/Obsidian Vault"))
REPORTS = VAULT / "Knowledge" / "20-Reports"
RAW = VAULT / "Knowledge" / "00-Raw"

# The four OODA bot profiles (each a clone of strategist, role-defined in SOUL.md)
BOTS = {
    "observe": "radar",
    "orient": "analyst",
    "decide": "warroom",
    "act": "strike",
}

# How to invoke a bot: hermes -p <bot> chat -c "Bot Chat" -Q -q "<message>"
def _load_env():
    """Load ~/.hermes/.env into os.environ (without overriding existing)."""
    env_path = HERMES / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v


def run_bot(bot, message, timeout=300):
    """Invoke a bot profile for one turn, return its reply text."""
    _load_env()  # ensure OLLAMA_API_KEY etc. are present for the child CLI
    env = dict(os.environ)
    cmd = [
        str(HERMES / "hermes-agent/venv/bin/hermes"),
        "-p", bot, "chat", "--in", str(Path.home()), "-c", "Bot Chat",
        "--create-if-missing", "-Q", "-q", message,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout, cwd=str(HERMES))
        out = (r.stdout or "") + (r.stderr or "")
        return out.strip()[-3000:]
    except subprocess.TimeoutExpired:
        return f"[{bot} timed out after {timeout}s]"
    except Exception as e:
        return f"[{bot} error: {e}]"


def collect_observations(day):
    """Gather today's intelligence captures so Observe has raw signals."""
    sections = []
    globs = [
        ("trends/market-trends-*.md", "Market Trends"),
        ("trends/fnb-trends-*.md", "F&B"),
        ("trends/hospitality-trends-*.md", "Hospitality"),
        ("trends/property-infra-energy-trends-*.md", "Property/Infra/Energy"),
        ("venture-news-*.md", "Venture News"),
        ("adb-central-asia-monitor-*.md", "ADB Central Asia"),
        ("policy/*-policy-tracker.md", "Policy"),
        ("research/*-arxiv.md", "Research"),
    ]
    for glob, label in globs:
        matches = sorted(RAW.glob(glob), key=lambda p: p.stat().st_mtime, reverse=True)
        if not matches:
            continue
        p = matches[0]
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        # extract link lines
        links = re.findall(r"^- \[([^\]]+)\]\((https?://[^\s\)]+)\)", text, re.M)
        if links:
            titles = [t for t, _ in links[:8]]
            sections.append(f"[{label}] {p.name}:\n" + "\n".join(f"  - {t}" for t in titles))
    return "\n\n".join(sections) if sections else "(no intelligence captures found today)"


def main():
    day = datetime.now().strftime("%Y-%m-%d")
    print(f"🔄 OODA Loop — {day}", flush=True)
    observations = collect_observations(day)
    print(f"  Observe: gathered signals from intelligence pipeline", flush=True)

    # Stage 1 — Observe (gather is done here; bot frames it)
    obs = run_bot(BOTS["observe"],
        "The daily intelligence pipeline captured these signals. Frame them as raw "
        "observations relevant to MUI's four platforms (Metrojaya retail, Corus hotels, "
        "Bandar Springhill property, A&W F&B) and the solvency clock. Do not analyse.\n\n"
        + observations)
    print(f"  {BOTS['observe']} done ({len(obs)} chars)", flush=True)

    # Stage 2 — Orient (interpret into options)
    ori = run_bot(BOTS["orient"],
        "Interpret these observations into 2-3 options for MUI, each with a recommendation "
        "and solvency check. Be decisive.\n\n" + obs)
    print(f"  {BOTS['orient']} done ({len(ori)} chars)", flush=True)

    # Stage 3 — Decide (frame the go/no-go for the human)
    dec = run_bot(BOTS["decide"],
        "Frame the go/no-go for these options against MUI doctrine (data first, Brain ours, "
        "tokens before platforms, checks not dates, never invest alone, reskilled not replaced) "
        "and RM 0 platform spend. State the recommendation and the pre-written kill criterion. "
        "This is for a human (the CTO) to approve — do NOT execute.\n\n" + ori)
    print(f"  {BOTS['decide']} done ({len(dec)} chars)", flush=True)

    # Stage 4 — Act is HUMAN-GATED. We prepare but do not execute.
    act_note = ("ACT is human-gated: no execution happens until the CTO approves the decision "
                "packet. On approval, the Act bot would execute the approved action and feed the "
                "outcome back to Observe to close the loop.")

    # Write the OODA brief
    REPORTS.mkdir(parents=True, exist_ok=True)
    fm = (f"---\ncreated: {day}\ntype: report\ndomain: knowledge\nstatus: draft\n"
          f"tags: [report, ooda, mui, intelligence, ai-brains]\n---\n")
    body = [fm, f"# 🔄 OODA Brief — {day}\n",
            f"**Generated:** {datetime.now():%Y-%m-%d %H:%M}",
            f"**Loop:** Observe → Orient → Decide → Act (human-gated)\n",
            "---\n", "## 👁️ Observe (Sensing)\n", obs,
            "\n---\n", "## 🧭 Orient (Interpretation)\n", ori,
            "\n---\n", "## ✅ Decide (Go/No-Go — FOR HUMAN)\n", dec,
            "\n---\n", "## 🏃 Act (HUMAN-GATED — awaiting CTO approval)\n", act_note,
            "\n---\n*Run by ooda-loop.py · human above the loop · checks, not dates*"]
    out = REPORTS / f"OODA-Brief-{day}.md"
    out.write_text("\n".join(body), encoding="utf-8")
    print(f"  💾 {out.relative_to(VAULT)}")

    # Deliver a compact decision packet to the human (Telegram, via stdout for cron)
    print(f"\n🔄 OODA Brief — {day}")
    print(f"  Observe → Orient → Decide complete. Act awaits your approval.")
    print(f"  Brief: {out.relative_to(VAULT)}")
    print(f"  📌 Decision packet — review, then approve Act to execute.")

    # Health file for flywheel-health-check
    import json as _json
    health_dir = VAULT / "System"
    health_dir.mkdir(parents=True, exist_ok=True)
    (health_dir / "ooda-loop-health.json").write_text(_json.dumps({
        "status": "ok", "day": day, "timestamp": datetime.now().isoformat(),
        "observe": len(obs), "orient": len(ori), "decide": len(dec),
        "act": "human-gated", "brief": str(out.relative_to(VAULT)),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
