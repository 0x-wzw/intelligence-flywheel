#!/usr/bin/env python3
"""Self-Evolution cron job — runs the skill evolution loop on a schedule.

Wraps ~/.hermes/self-evolution/scripts/evolve_all_skills.py so it can be
registered as a Hermes cron job. Runs a bounded evolution pass over a
configurable set of skills and prints a concise summary (delivered to the
job's delivery target, e.g. Telegram).

Design notes:
- Bounded by default: only evolves a small rotating subset per run so a
  single scheduled run doesn't burn hours of LLM calls. Use --all to run
  over every skill.
- State is tracked in ~/.hermes/self-evolution/output/evolved_skills.json,
  so already-evolved skills are skipped unless --force.
- Loads Ollama cloud creds from ~/.hermes/.env and routes DSPy through
  OPENAI_API_BASE=https://ollama.com/v1.

Usage (as a cron no_agent script):
    self-evolution-loop.py [--iterations N] [--only a,b,c] [--all] [--force]
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERMES_HOME = Path(os.path.expanduser("~/.hermes"))
EVO_REPO = HERMES_HOME / "self-evolution"
LOOP_SCRIPT = EVO_REPO / "scripts" / "evolve_all_skills.py"
ENV_FILE = HERMES_HOME / ".env"
OUTPUT_DIR = EVO_REPO / "output"
STATE_FILE = OUTPUT_DIR / "evolved_skills.json"
SKILLS_ROOT = HERMES_HOME / "skills"

# Directory names that are not standalone skills (mirrors the agent's own
# skill_utils.EXCLUDED_SKILL_DIRS / SKILL_SUPPORT_DIRS). These never rotate.
NON_SKILL_DIRS = frozenset({
    ".git", ".github", ".hub", ".archive", ".venv", "venv",
    "node_modules", "site-packages", "__pycache__", ".tox", ".nox",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "_org",
    ".curator_backups",
})
SKILL_SUPPORT_DIRS = frozenset({"references", "templates", "assets", "scripts"})

# The meta-skill that drives evolution is excluded from rotation so it stays
# stable (evolving the skill that runs the loop is self-referential).
EXCLUDED_SKILL_NAMES = frozenset({"self-evolution"})

# Number of not-yet-evolved skills to evolve per cron run.
DEFAULT_BATCH_SIZE = 3


def discover_local_skills() -> list[str]:
    """Return names of all standalone local skills under ~/.hermes/skills.

    A standalone skill is a directory (not an excluded/support dir) that
    directly contains a SKILL.md. Support files nested under a skill
    (references/, scripts/, …) are not themselves skills.
    """
    if not SKILLS_ROOT.exists():
        return []
    names = []
    for skill_md in SKILLS_ROOT.rglob("SKILL.md"):
        parent = skill_md.parent
        # Skip any SKILL.md whose path passes through a non-skill or
        # support dir at any level (e.g. skills/x/references/old/SKILL.md).
        rel = parent.relative_to(SKILLS_ROOT)
        if any(part in NON_SKILL_DIRS for part in rel.parts):
            continue
        if any(part in SKILL_SUPPORT_DIRS for part in rel.parts[:-1]):
            continue
        name = parent.name
        if name in EXCLUDED_SKILL_NAMES:
            continue
        if name not in names:
            names.append(name)
    return sorted(names)


def select_batch(pending: list[str], batch_size: int) -> list[str]:
    """Pick the next batch of skills to evolve, rotating round-robin.

    Each run advances an internal pointer so the cron job works through the
    full local skill list over successive runs instead of re-touching the
    same few skills. Once all skills are done it wraps back around.
    """
    if not pending:
        return []
    try:
        with (OUTPUT_DIR / "rotation.json").open() as f:
            pointer = json.load(f).get("pointer", 0)
    except (OSError, json.JSONDecodeError):
        pointer = 0
    batch = (pending * 2)[pointer:pointer + batch_size]
    # Advance pointer for next run.
    (OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    with (OUTPUT_DIR / "rotation.json").open("w") as f:
        json.dump({"pointer": (pointer + batch_size) % len(pending)}, f)
    return batch


def _load_env() -> dict:
    """Load OLLAMA_API_KEY from ~/.hermes/.env (and any other vars)."""
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in env:
                env[key] = value
    return env


def _load_state() -> dict:
    """Return the evolution state dict (skill -> status/elapsed)."""
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _run_loop(args, only_names: str | None) -> tuple[int, str]:
    """Run the evolution loop script, returning (returncode, output).

    Uses the self-evolution venv's Python explicitly — sys.executable is the
    Hermes cron runner's interpreter, which does NOT have dspy installed. The
    venv at ~/.hermes/self-evolution/.venv has dspy/litellm/optuna.
    """
    venv_py = EVO_REPO / ".venv" / "bin" / "python"
    if not venv_py.exists():
        return 2, f"ERROR: venv python not found at {venv_py} (run: cd {EVO_REPO} && python3 -m venv .venv && .venv/bin/pip install -q dspy optuna rich click pytest litellm)"
    cmd = [
        str(venv_py), str(LOOP_SCRIPT),
        "--iterations", str(args.iterations),
        "--optimizer-model", args.optimizer_model,
        "--eval-model", args.eval_model,
    ]
    if only_names:
        cmd += ["--only", only_names]
    if args.force:
        cmd += ["--force"]

    env = _load_env()
    env["OPENAI_API_BASE"] = "https://ollama.com/v1"
    env["OPENAI_API_KEY"] = env.get("OLLAMA_API_KEY", "")

    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(EVO_REPO), env=env)
    return proc.returncode, proc.stdout + proc.stderr


def _summarize() -> str:
    """Build a short human-readable summary from the state file."""
    if not STATE_FILE.exists():
        return "No evolution state yet."
    try:
        state = json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return "Could not read evolution state."
    lines = []
    for name, info in state.items():
        status = info.get("status", "?")
        elapsed = info.get("elapsed")
        err = info.get("error", "")
        if status == "done":
            lines.append(f"  ✓ {name} ({elapsed}s)")
        else:
            lines.append(f"  ✗ {name}: {err or status}")
    return "\n".join(lines) if lines else "No skills evolved yet."


def main():
    parser = argparse.ArgumentParser(description="Self-evolution cron loop")
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--only", default=None, help="Comma-separated skill names")
    parser.add_argument("--all", action="store_true", help="Evolve every skill")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH_SIZE,
                        help="Number of local skills to evolve per run (rotation)")
    parser.add_argument("--force", action="store_true", help="Re-evolve already-done skills")
    parser.add_argument("--optimizer-model", default="openai/gpt-oss:20b")
    parser.add_argument("--eval-model", default="openai/gpt-oss:20b")
    args = parser.parse_args()

    if not LOOP_SCRIPT.exists():
        print(f"⚠️ Self-evolution repo not found at {EVO_REPO}. Clone it first.")
        return 1

    all_local = discover_local_skills()
    done = set(_load_state().keys())
    pending = [s for s in all_local if s not in done]

    # Resolve the actual skill scope (batch) so we can report it accurately.
    if args.all:
        scope_label = "ALL local skills"
        only_names = None
    elif args.only:
        only_names = args.only
        scope_names = [n.strip() for n in args.only.split(",") if n.strip()]
        scope_label = ", ".join(scope_names)
    else:
        # Rotate through local skills that haven't been done yet; wrap around
        # if everything is already done.
        rotation_pool = pending or all_local
        scope_names = select_batch(rotation_pool, args.batch)
        only_names = ",".join(scope_names)
        scope_label = ", ".join(scope_names) or "none"

    print(f"🧬 Self-Evolution — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Iterations: {args.iterations} | Model: {args.optimizer_model}")
    print(f"  Scope: {scope_label}")
    print(f"  Local skills total: {len(all_local)} | done: {len(done)} | pending: {len(pending)}")
    print()

    rc, output = _run_loop(args, only_names)

    # Print the loop's own summary table (already in output), then our state summary.
    print(output[-3000:])  # tail of the loop output (results table + report path)
    print("\n📊 Evolution state:")
    print(_summarize())

    return rc


if __name__ == "__main__":
    sys.exit(main())
