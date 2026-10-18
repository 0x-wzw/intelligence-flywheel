#!/usr/bin/env python3
"""
bias-balance-check.py

Weekly audit of the past week's intelligence captures for viewpoint diversity.

Algorithm (per the Intelligence Cronjobs Improvement Plan, P1):
  1. Collect every capture written to Knowledge/00-Raw/ in the last 7 days
  2. Extract the source URLs cited in each
  3. Map each source to perspective labels across 4 axes (bias_taxonomy.py)
  4. Cluster captures by topic keyword
  5. Score each cluster's diversity; flag clusters with <2 perspectives
  6. Recommend specific feeds that would close each gap

Schedule: Weekly Sunday 8:00 AM
Output:   Knowledge/20-Reports/Bias-Balance-YYYY-Www.md
Health:   System/bias-balance-check-health.json
"""

import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from bias_taxonomy import (
    AXIS_VALUES,
    EXPECTED_AXES,
    GAP_RECOMMENDATIONS,
    TOPIC_CLUSTERS,
    classify_source,
    classify_topics,
)
from hermes_feeds import CYCLE, frontmatter_block, parse_frontmatter, write_health

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
RAW_DIR = VAULT_PATH / "Knowledge" / "00-Raw"
OUTPUT_DIR = VAULT_PATH / "Knowledge" / "20-Reports"

LOOKBACK_DAYS = 7

# Directories under 00-Raw that hold cron-generated intelligence.
SCAN_SUBDIRS = ["thought-leaders", "research", "policy", "trends", "podcasts"]

URL_RE = re.compile(r"https?://[^\s\)\]\|>\"']+")


def recent_captures(days: int = LOOKBACK_DAYS) -> List[Path]:
    """Every markdown capture modified within the window."""
    cutoff = datetime.now() - timedelta(days=days)
    files: List[Path] = []

    candidates = list(RAW_DIR.glob("*.md"))
    for sub in SCAN_SUBDIRS:
        d = RAW_DIR / sub
        if d.exists():
            candidates.extend(d.rglob("*.md"))

    for f in candidates:
        if f.name.startswith("_"):
            continue
        try:
            fm = parse_frontmatter(f.read_text(errors="ignore"))
        except OSError:
            continue
        created = fm.get("created", "")[:10]
        dt = None
        try:
            dt = datetime.strptime(created, "%Y-%m-%d")
        except ValueError:
            dt = datetime.fromtimestamp(f.stat().st_mtime)
        if dt >= cutoff:
            files.append(f)

    return sorted(set(files))


def host_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    return m.group(1).lower() if m else ""


def analyse() -> Dict[str, Any]:
    """Build the per-cluster perspective picture for the week."""
    files = recent_captures()

    # cluster -> axis -> set(perspective values)
    cluster_axes: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
    cluster_sources: Dict[str, Set[str]] = defaultdict(set)
    cluster_counts: Dict[str, int] = defaultdict(int)

    all_hosts: Set[str] = set()
    classified_hosts: Set[str] = set()
    unclassified: Dict[str, int] = defaultdict(int)
    total_links = 0

    for f in files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue

        # Split into per-item chunks so a link is attributed to nearby text,
        # not to the entire document's topic soup.
        chunks = re.split(r"\n(?=#{2,4}\s|\- \*\*)", text)
        for chunk in chunks:
            urls = URL_RE.findall(chunk)
            if not urls:
                continue
            topics = classify_topics(chunk)
            if not topics:
                continue

            for url in urls:
                total_links += 1
                host = host_of(url)
                if not host:
                    continue
                all_hosts.add(host)
                labels = classify_source(url)
                if labels:
                    classified_hosts.add(host)
                else:
                    unclassified[host] += 1

                for topic in topics:
                    cluster_counts[topic] += 1
                    cluster_sources[topic].add(host)
                    for axis, value in labels.items():
                        cluster_axes[topic][axis].add(value)

    return {
        "files": files,
        "cluster_axes": cluster_axes,
        "cluster_sources": cluster_sources,
        "cluster_counts": cluster_counts,
        "all_hosts": all_hosts,
        "classified_hosts": classified_hosts,
        "unclassified": unclassified,
        "total_links": total_links,
    }


def score_cluster(topic: str, axes: Dict[str, Set[str]]) -> Tuple[float, List[str]]:
    """
    Score a topic cluster 0-10 on perspective diversity, and list the gaps.

    Scoring: for each EXPECTED axis, the fraction of that axis's perspectives we
    actually saw, capped so that 2 distinct perspectives already earns most of
    the credit (we want balance, not completeness for its own sake).
    """
    expected = EXPECTED_AXES.get(topic, ["tech"])
    gaps: List[str] = []
    axis_scores: List[float] = []

    for axis in expected:
        seen = axes.get(axis, set())
        possible = AXIS_VALUES.get(axis, [])
        if not possible:
            continue
        # 0 perspectives -> 0.0, 1 -> 0.45, 2 -> 0.8, 3+ -> 1.0
        n = len(seen)
        axis_scores.append({0: 0.0, 1: 0.45, 2: 0.8}.get(n, 1.0))
        if n < 2:
            for missing in possible:
                if missing not in seen:
                    gaps.append(f"{axis}:{missing}")

    score = round((sum(axis_scores) / len(axis_scores)) * 10, 1) if axis_scores else 0.0
    return score, gaps


def generate_report(data: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()

    cluster_axes = data["cluster_axes"]
    cluster_counts = data["cluster_counts"]
    cluster_sources = data["cluster_sources"]

    rows = []
    all_gaps: Dict[str, List[str]] = {}
    scores = []

    for topic in TOPIC_CLUSTERS:
        count = cluster_counts.get(topic, 0)
        if count == 0:
            continue
        axes = cluster_axes.get(topic, {})
        score, gaps = score_cluster(topic, axes)
        scores.append(score)
        all_gaps[topic] = gaps

        perspectives = sorted({f"{a}:{v}" for a, vs in axes.items() for v in vs})
        rows.append(
            {
                "topic": topic,
                "count": count,
                "score": score,
                "perspectives": perspectives,
                "n_sources": len(cluster_sources.get(topic, set())),
                "gaps": gaps,
            }
        )

    overall = round(sum(scores) / len(scores), 1) if scores else 0.0
    single_perspective = [r for r in rows if len(r["perspectives"]) < 2]

    fm = frontmatter_block(
        title=f"Bias Balance Report — {iso_year}-W{iso_week:02d}",
        tags=["report", "bias-balance", "intelligence", "source-diversity"],
        stage="report",
        status="shipped",
        extra={
            "distilled": "false",
            # Weekly epistemics check; surfaced in flywheel §0b. Excluded from
            # distillation backlogs on this key.
            "recurring": "weekly",
            "sources": "Knowledge/00-Raw weekly captures",
            "overall_diversity_score": overall,
            "clusters_analysed": len(rows),
            "single_perspective_clusters": len(single_perspective),
        },
    )

    verdict = (
        "✅ Balanced" if overall >= 7
        else "⚠️ Needs more perspectives" if overall >= 5
        else "🔴 Single-perspective risk"
    )

    report = fm + f"""
# ⚖️ Bias Balance Report — {iso_year} Week {iso_week:02d}

**Window:** {(now - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')} → {now.strftime('%Y-%m-%d')}
**Captures analysed:** {len(data['files'])}
**Source links examined:** {data['total_links']}
**Overall Diversity Score:** {overall}/10 — {verdict}

---

## Summary by Topic

| Topic | Items | Sources | Perspectives | Score | Status |
|-------|-------|---------|--------------|-------|--------|
"""
    for r in sorted(rows, key=lambda x: x["score"]):
        status = "✅" if r["score"] >= 7 else "⚠️" if r["score"] >= 5 else "🔴"
        plist = ", ".join(p.split(":")[1] for p in r["perspectives"][:4]) or "_none classified_"
        report += (
            f"| {r['topic']} | {r['count']} | {r['n_sources']} | {plist} | "
            f"{r['score']}/10 | {status} |\n"
        )

    if not rows:
        report += "| _no classified captures this week_ | — | — | — | — | 🔴 |\n"

    # ── Gaps and recommendations ──
    report += "\n---\n\n## 🎯 Gaps & Recommended Voices\n\n"
    any_gap = False
    for r in sorted(rows, key=lambda x: x["score"]):
        if not r["gaps"]:
            continue
        any_gap = True
        report += f"### {r['topic']} — {r['score']}/10\n\n"
        report += f"Currently represented: {', '.join(r['perspectives']) or '_nothing classified_'}\n\n"
        report += "**Missing:**\n\n"
        seen_recs: Set[str] = set()
        for gap in r["gaps"][:4]:
            report += f"- `{gap}`\n"
            for rec in GAP_RECOMMENDATIONS.get(gap, [])[:2]:
                if rec not in seen_recs:
                    seen_recs.add(rec)
                    report += f"    - Add: {rec}\n"
        report += "\n"

    if not any_gap:
        report += "*No perspective gaps detected this week.*\n"

    # ── Red flags ──
    report += "\n---\n\n## 🚩 Red Flags\n\n"
    flags = []
    for r in single_perspective:
        flags.append(
            f"**{r['topic']}** — {r['count']} items but only "
            f"{len(r['perspectives'])} perspective(s) classified. Single-voice risk."
        )
    geo = {v for a, vs in cluster_axes.items() for ax, s in vs.items() if ax == "geography" for v in s}
    if geo and geo <= {"western"}:
        flags.append(
            "**Geographic monoculture** — every classified source this week is Western. "
            "See `geography:global_south` recommendations above."
        )
    unclassified_share = (
        len(data["unclassified"]) / len(data["all_hosts"]) if data["all_hosts"] else 0
    )
    if unclassified_share > 0.5:
        flags.append(
            f"**{unclassified_share:.0%} of source hosts are unclassified** — the perspective "
            "map in `bias_taxonomy.py` is not keeping up with the feed list. Scores below "
            "are therefore optimistic."
        )

    if flags:
        for f in flags:
            report += f"- 🚩 {f}\n"
    else:
        report += "*No red flags.*\n"

    # ── Unclassified sources ──
    top_unclassified = sorted(data["unclassified"].items(), key=lambda x: -x[1])[:15]
    report += "\n---\n\n## 🏷️ Unclassified Sources\n\n"
    if top_unclassified:
        report += (
            "These hosts appeared this week but carry no perspective label. Add them to "
            "`SOURCE_PERSPECTIVES` in `~/.hermes/scripts/bias_taxonomy.py` so future audits "
            "can score them.\n\n| Host | Links |\n|------|-------|\n"
        )
        for host, n in top_unclassified:
            report += f"| `{host}` | {n} |\n"
    else:
        report += "*All sources classified.*\n"

    report += f"""
---

## Diversity Checklist

Before publishing any intelligence report this week:

- [ ] At least 2 opposing viewpoints represented
- [ ] At least 1 Global South perspective (if applicable)
- [ ] At least 1 non-venture-backed voice (if business topic)
- [ ] At least 1 skeptic voice (if hype-heavy topic)
- [ ] Source diversity score >6/10

---

## Related

- [[System/Wiki/bias-detection|Bias Detection Framework]]
- [[System/Wiki/source-diversity|Source Diversity]]
- [[System/Wiki/thought-leaders-index|Thought Leaders Index]]
- [[Knowledge/20-Reports/Intelligence-Cronjobs-Improvement-Plan|Intelligence Cronjobs Improvement Plan]]

---

*Generated by `bias-balance-check.py` | Next run: Sunday 8:00 AM*
"""

    stats = {
        "overall_diversity_score": overall,
        "clusters_analysed": len(rows),
        "single_perspective_clusters": len(single_perspective),
        "captures_analysed": len(data["files"]),
        "links_examined": data["total_links"],
        "hosts_total": len(data["all_hosts"]),
        "hosts_unclassified": len(data["unclassified"]),
    }
    return report, stats


def main():
    print(f"Starting bias balance check: {datetime.now().isoformat()}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    data = analyse()
    report, stats = generate_report(data)

    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()
    output_file = OUTPUT_DIR / f"Bias-Balance-{iso_year}-W{iso_week:02d}.md"
    output_file.write_text(report)

    write_health(
        "bias-balance-check",
        {**stats, "output": str(output_file),
         "status": "ok" if stats["clusters_analysed"] else "no-classified-captures"},
    )

    print(f"✅ Bias balance check complete: {output_file}")
    print(f"   - Captures analysed: {stats['captures_analysed']}")
    print(f"   - Clusters: {stats['clusters_analysed']}  Overall: {stats['overall_diversity_score']}/10")
    print(f"   - Single-perspective clusters: {stats['single_perspective_clusters']}")

    # Write to cron log file for traceability
    log_dir = Path(os.path.expanduser("~/.hermes/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bias-balance-check.log"
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | score={stats['overall_diversity_score']} clusters={stats['clusters_analysed']} single_perspective={stats['single_perspective_clusters']} captures={stats['captures_analysed']}\n")


if __name__ == "__main__":
    main()
