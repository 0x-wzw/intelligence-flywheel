#!/usr/bin/env python3
"""schema-weekly-check.py — deterministic replacement for the schema-v2
weekly-health-check agent job (Mondays 09:00).

The lint itself is already deterministic: vault-schema-lint.py emits JSON and
writes System/schema-lint-health.json. What the AGENT added was the baseline
comparison, regression flagging and the trend summary. This script does that
without an LLM:

  1. Run vault-schema-lint.py (dealcrusher profile copy = authoritative)
  2. Compare compliance_pct against the baseline (default 78.2% = 2026-09-02)
  3. Verdict: drop >2pt = CRITICAL, >1pt = WARNING, >1pt rise = IMPROVED, else OK
  4. Print the metric report (the job's deliverable)
  5. Append a dated line to System/schema-trend.log
  6. Write System/schema-delta.json — a pre-computed delta table
     (current vs previous lint) that LLM brief jobs quote verbatim,
     so no agent ever re-derives or rounds these numbers itself

The baseline ratchets upward only (a new high becomes the floor), so a good
week can never mask a later regression.
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
VAULT = HOME / "Documents" / "Obsidian Vault"
HEALTH = VAULT / "System" / "schema-lint-health.json"
TREND = VAULT / "System" / "schema-trend.log"
DELTA = VAULT / "System" / "schema-delta.json"
# Weekly-specific snapshot. NOT .schema-lint-health-prev.json — that path is
# owned by vault-learn.py (daily, different {"summary": ...} format).
PREV = VAULT / "System" / ".schema-weekly-health-prev.json"
BASELINE_FILE = VAULT / "System" / "schema-baseline.json"
DEFAULT_BASELINE = 78.2  # 2026-09-02 agent baseline

LINT_CANDIDATES = [
    HOME / ".hermes" / "profiles" / "dealcrusher" / "scripts" / "vault-schema-lint.py",
    HOME / ".hermes" / "scripts" / "vault-schema-lint.py",
]

METRICS = ["compliance_pct", "undateable_imports", "bad_status", "bad_type",
           "legacy_stage", "missing_required", "type_path_mismatch",
           "distinct_status_values", "distinct_type_values"]

# Count metrics emitted in the delta table as {current, previous, delta}.
COUNT_METRICS = ["total_files", "compliant", "no_frontmatter",
                 "undateable_imports", "notion_residue", "notion_residue_only",
                 "missing_required", "type_path_mismatch"]


def run_lint() -> dict | None:
    """Run the authoritative lint; it writes System/schema-lint-health.json."""
    script = next((s for s in LINT_CANDIDATES if s.exists()), None)
    if not script:
        print("vault-schema-lint.py not found")
        return None
    r = subprocess.run([sys.executable, str(script)],
                       capture_output=True, text=True, timeout=900)
    try:
        return json.loads(HEALTH.read_text())
    except (OSError, ValueError):
        try:
            return json.loads(r.stdout)
        except ValueError:
            print(f"lint failed: {(r.stderr or r.stdout).strip()[:300]}")
            return None


def load_baseline() -> float:
    try:
        return float(json.loads(BASELINE_FILE.read_text())["baseline_pct"])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_BASELINE


def save_baseline(v: float) -> None:
    try:
        BASELINE_FILE.write_text(json.dumps(
            {"baseline_pct": v,
             "updated": datetime.now().isoformat(timespec="seconds")},
            indent=2) + "\n")
    except OSError:
        pass


def write_delta(cur: dict, prev: dict | None) -> dict:
    """Build the pre-computed delta table that LLM brief jobs quote verbatim.

    prev is the previous run's raw lint health (snapshot taken last run);
    None (first run) leaves every previous/delta value null rather than
    guessing.
    """
    s_cur = cur.get("summary", cur)
    s_prev = (prev or {}).get("summary", prev or {})

    def _num(v):
        return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

    metrics = {}
    for m in COUNT_METRICS:
        c, p = _num(s_cur.get(m)), _num(s_prev.get(m))
        metrics[m] = {"current": c, "previous": p,
                      "delta": (c - p) if (c is not None and p is not None) else None}

    table = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "current_lint_ts": cur.get("timestamp"),
        "previous_lint_ts": (prev or {}).get("timestamp"),
        "compliance_pct": s_cur.get("compliance_pct"),
        "compliance_pct_previous": s_prev.get("compliance_pct"),
        "metrics": metrics,
        "source": "schema-weekly-check.py (deterministic — quote verbatim, do not recompute)",
    }
    tmp = DELTA.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(table, indent=2) + "\n")
    tmp.replace(DELTA)
    return table


def main() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"Schema v2 Weekly Health Check - {today}")

    data = run_lint()
    if not data:
        return 1
    s = data.get("summary", data)

    prev = None
    try:
        prev = json.loads(PREV.read_text())
    except (OSError, ValueError):
        pass

    pct = s.get("compliance_pct", 0) or 0
    base = load_baseline()
    delta = pct - base
    if delta < -2.0:
        verdict = "CRITICAL"
    elif delta < -1.0:
        verdict = "WARNING"
    elif delta > 1.0:
        verdict = "IMPROVED"
    else:
        verdict = "OK"

    print(f"\n  Compliance: {pct}%  (baseline {base}%, delta {delta:+.1f}pt) -> {verdict}")
    for m in METRICS:
        print(f"    {m:24} {s.get(m, '-')}")

    table = write_delta(data, prev)
    fm = table["metrics"]["total_files"]
    print(f"  Delta table: {DELTA.name}  (files {fm['previous']} -> "
          f"{fm['current']} [{fm['delta']}])")
    try:  # snapshot current lint for next run's delta comparison
        PREV.write_text(json.dumps(data, indent=2) + "\n")
    except OSError:
        pass

    try:
        with TREND.open("a") as f:
            f.write(f"{today} compliance={pct} baseline={base} "
                    f"delta={delta:+.1f} verdict={verdict} "
                    f"files={s.get('total_files', '-')} "
                    f"compliant={s.get('compliant', '-')}\n")
    except OSError:
        pass

    if pct > base:
        save_baseline(pct)  # ratchet: new high-water mark
        print(f"  baseline ratcheted to {pct}%")

    return 0 if verdict != "CRITICAL" else 1


if __name__ == "__main__":
    sys.exit(main())
