#!/usr/bin/env python3
"""
flywheel-health-check.py

Weekly health check for the Knowledge flywheel.
Scans for:
1. Raw items unprocessed >30 days
2. Reports shipped but distilled: false
3. Wiki pages not reviewed >90 days
4. Orphaned notes and broken wikilinks (via vault-orphan-scan.py)

Output: Markdown report to System/_generated/flywheel-health-YYYY-MM-DD.md
Schedule: Weekly Monday 6AM
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
OUTPUT_DIR = VAULT_PATH / "System"            # where the intelligence health JSONs live
# Generated reports live apart from authored notes: they were being indexed,
# embedded and counted as orphans alongside hand-written material.
REPORT_DIR = VAULT_PATH / "System" / "_generated"

# ── Intelligence cron health ──────────────────────────────────────────
# Each intelligence script writes System/<name>-health.json on every run.
# A run that captures zero items looks identical to a quiet news day unless
# something checks — that is exactly how thought-leader-scan.py sat broken for
# a day while still writing cheerful, empty reports.
INTELLIGENCE_JOBS = {
    "thought-leader-scan": {"schedule": "Daily 5:00 AM", "max_age_hours": 30},
    "arxiv-digest": {"schedule": "Daily 4:00 AM", "max_age_hours": 30},
    "policy-tracker": {"schedule": "Daily 6:00 AM", "max_age_hours": 30},
    "podcast-transcribe": {"schedule": "Weekly Mon 7:00 AM", "max_age_hours": 8 * 24},
    "bias-balance-check": {"schedule": "Weekly Sun 8:00 AM", "max_age_hours": 8 * 24},
    "signal-noise": {"schedule": "Weekly Sun 9:00 AM", "max_age_hours": 8 * 24},
    # Not a capture job, but every retrieval in the vault depends on it, and it
    # failed silently two days running before anything was watching it.
    "vault-semantic-index": {"schedule": "Daily 5:15 AM", "max_age_hours": 30},

    # The six trend jobs plus market-intelligence. Added 2026-07-30: these were
    # the ONLY capture jobs nobody watched, and they are exactly the ones that
    # silently degraded in July — output fell from ~15KB to a few hundred bytes
    # (1-2 articles against a normal 8-20) for ten days, and the only reason it
    # surfaced was a human noticing thin reports weeks later.
    #
    # They report `total_articles` rather than `items`, and write NO `status`
    # key, so before this they would have read as "ok" with 0 articles. Hence
    # `zero_is_failure`: for these jobs an empty run is a broken run, because
    # they do not dedup against previous output — a real quiet day still yields
    # articles from a 30+ feed corpus.
    "csuite-leadership-trends":     {"schedule": "Daily 12:01 AM", "max_age_hours": 30, "zero_is_failure": True},
    "fnb-trends":                   {"schedule": "Daily 1:01 AM",  "max_age_hours": 30, "zero_is_failure": True},
    "hospitality-trends":           {"schedule": "Daily 2:02 AM",  "max_age_hours": 30, "zero_is_failure": True},
    "market-trends":                {"schedule": "Daily 3:01 AM",  "max_age_hours": 30, "zero_is_failure": True},
    "market-intelligence":          {"schedule": "Daily 3:31 AM",  "max_age_hours": 30, "zero_is_failure": True},
    "property-infra-energy-trends": {"schedule": "Daily 4:01 AM",  "max_age_hours": 30, "zero_is_failure": True},
    "ai-science-investment-trends": {"schedule": "Daily 5:32 AM",  "max_age_hours": 30, "zero_is_failure": True},
    "venture-news":                 {"schedule": "Daily 6:45 AM",  "max_age_hours": 30, "zero_is_failure": True},
    "adb-central-asia-monitor":     {"schedule": "Daily 9:00 AM",  "max_age_hours": 30},
    "intelligence-publish":         {"schedule": "Daily 8:15 AM",  "max_age_hours": 30},
    "leader-maintenance":           {"schedule": "Weekly Sun 10:30 AM", "max_age_hours": 8 * 24},
    "ooda-loop":                    {"schedule": "Daily 8:30 AM",  "max_age_hours": 30},
}

def parse_frontmatter(content: str) -> Dict[str, Any]:
    """Extract YAML frontmatter as dict."""
    match = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not match:
        return {}
    
    fm: Dict[str, Any] = {}
    for line in match.group(1).split('\n'):
        if ':' in line:
            key, value = line.split(':', 1)
            fm[key.strip()] = value.strip().strip('"\'')
    return fm

def get_file_date(fm: Dict[str, Any], *keys) -> Optional[datetime]:
    """Get datetime from frontmatter, trying multiple keys."""
    for key in keys:
        if key in fm and fm[key]:
            try:
                return datetime.strptime(fm[key], '%Y-%m-%d')
            except ValueError:
                continue
    return None

def scan_raw_items() -> Tuple[List[Dict[str, Any]], List[tuple]]:
    """Find raw items unprocessed >30 days."""
    raw_dir = VAULT_PATH / "Knowledge" / "00-Raw"
    old_items: List[Dict[str, Any]] = []
    cutoff = datetime.now() - timedelta(days=30)
    
    if not raw_dir.exists():
        return old_items, []

    # rglob, not glob: the cron capture jobs write into subdirectories
    # (research/, thought-leaders/, policy/, trends/, content-pipeline/,
    # podcasts/). A non-recursive scan skipped every automated capture — ~29% of
    # 00-Raw — and so always reported a clean backlog.
    for md_file in sorted(raw_dir.rglob("*.md")):
        if md_file.name.startswith("_"):
            continue
        if any(part.startswith("_") for part in md_file.relative_to(raw_dir).parts[:-1]):
            continue
        
        content = md_file.read_text()
        fm = parse_frontmatter(content)
        
        # Skip index files and cycle kickoffs. `type` is the lifecycle axis under
        # Schema v2; `stage` is legacy and still honoured on notes that predate it.
        kind = fm.get('type') or fm.get('stage')
        if kind not in ('raw', None):
            continue
        if 'Cycle Kickoff' in md_file.name:
            continue
        
        created = get_file_date(fm, 'created')
        status = fm.get('status', 'unknown')
        
        if created and created < cutoff and status != 'processed':
            old_items.append({
                'file': str(md_file.relative_to(raw_dir)),
                'created': fm.get('created', 'unknown'),
                'status': status,
                'days_old': (datetime.now() - created).days
            })
    
    # Also check linked external raw captures. These counts are measured, not
    # estimated — the previous hardcoded "~40"/"~70" strings kept reporting a
    # Notion backlog long after Inbox/Notion/ had been emptied.
    # System/Trends/ was retired on 2026-07-26 — its reports moved into
    # Knowledge/00-Raw/trends/, which the rglob above already counts, so listing
    # it here would double-count the same files.
    external_sources = [
        ("Inbox/Notion", "Inbox/Notion/ (MUI Daily Scans)", "Unprocessed Notion backups"),
    ]
    external_raw = []
    for rel, label, note in external_sources:
        d = VAULT_PATH / rel
        count = len(list(d.rglob("*.md"))) if d.exists() else 0
        if count:
            external_raw.append((label, f"{count} files", note))
    
    return old_items, external_raw

def scan_reports() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Find reports shipped but not distilled."""
    reports_dir = VAULT_PATH / "Knowledge" / "20-Reports"
    not_distilled: List[Dict[str, Any]] = []
    
    if not reports_dir.exists():
        return not_distilled
    
    for md_file in reports_dir.glob("*.md"):
        if md_file.name.startswith("_"):
            continue
        
        content = md_file.read_text()
        fm = parse_frontmatter(content)
        
        # `type` is the form axis under Schema v2; `stage` was retired on
        # 2026-07-26 (lifecycle is derived from the folder). Reading only `stage`
        # here silently reduced this check to zero rows.
        if (fm.get('type') or fm.get('stage')) != 'report':
            continue
        
        status = fm.get('status', 'draft')
        distilled = fm.get('distilled', 'false')
        
        if status == 'shipped' and str(distilled).lower() == 'false':
            created = get_file_date(fm, 'created')
            not_distilled.append({
                'file': md_file.name,
                'created': fm.get('created', 'unknown'),
                'sources': fm.get('sources', 'not specified'),
                'days_since_shipped': (datetime.now() - created).days if created else 'unknown'
            })
    
    return not_distilled

def scan_wiki_pages() -> List[Dict[str, Any]]:
    """Find wiki pages not reviewed >90 days."""
    wiki_dir = VAULT_PATH / "System" / "Wiki"
    aging_pages: List[Dict[str, Any]] = []
    cutoff = datetime.now() - timedelta(days=90)
    
    if not wiki_dir.exists():
        return aging_pages
    
    for md_file in wiki_dir.rglob("*.md"):
        if md_file.name in ("_index.md", "SCHEMA.md", "log.md", "index.md"):
            continue
        if md_file.parent.name == "raw":
            continue
        
        content = md_file.read_text()
        fm = parse_frontmatter(content)
        
        # Try to find last-reviewed or updated date
        last_reviewed = get_file_date(fm, 'last-reviewed', 'updated')
        created = get_file_date(fm, 'created')
        
        # Use created date if no review date exists
        review_date = last_reviewed or created

        if review_date is None:
            # No last-reviewed, updated, OR created. Previously these were skipped
            # entirely, so a page with no dates could never be flagged — which made
            # the "all pages current ✅" verdict silently exclude most of the wiki.
            # Undatable is a review gap, not a pass.
            aging_pages.append({
                'file': str(md_file.relative_to(wiki_dir)),
                'last_reviewed': 'no date recorded',
                'days_since_review': None,
                'type': fm.get('type', 'unknown')
            })
        elif review_date < cutoff:
            days_since = (datetime.now() - review_date).days
            aging_pages.append({
                'file': str(md_file.relative_to(wiki_dir)),
                'last_reviewed': fm.get('last-reviewed', fm.get('created', 'unknown')),
                'days_since_review': days_since,
                'type': fm.get('type', 'unknown')
            })

    # Datable-but-overdue pages first (oldest first), then the undatable ones.
    return sorted(
        aging_pages,
        key=lambda x: (x['days_since_review'] is None, -(x['days_since_review'] or 0)),
    )

def scan_intelligence_jobs() -> List[Dict[str, Any]]:
    """
    Check each intelligence cron's health JSON for staleness and silent-zero runs.

    Three failure modes are distinguished:
      missing  — the job has never written health (never ran / crashed at import)
      stale    — health exists but is older than the schedule allows
      zero     — the job ran and reported ok-looking output but captured nothing
    """
    results: List[Dict[str, Any]] = []
    now = datetime.now()

    for name, meta in INTELLIGENCE_JOBS.items():
        health_file = OUTPUT_DIR / f"{name}-health.json"
        entry: Dict[str, Any] = {
            "job": name,
            "schedule": meta["schedule"],
            "state": "missing",
            "detail": "no health file — job has never completed a run",
            "age_hours": None,
            "items": None,
        }

        if health_file.exists():
            try:
                data = json.loads(health_file.read_text())
            except (json.JSONDecodeError, OSError) as e:
                entry.update(state="error", detail=f"unreadable health file: {e}")
                results.append(entry)
                continue

            ts = data.get("timestamp", "")
            age_hours = None
            try:
                age_hours = (now - datetime.fromisoformat(ts)).total_seconds() / 3600
            except (ValueError, TypeError):
                pass

            # Item count key varies by job; take the first that is present.
            items = next(
                (
                    data[k]
                    for k in ("items_captured", "papers_total", "episodes_captured",
                              "leader_items", "items_scored", "clusters_analysed",
                              "files_indexed",
                              # the six trend jobs
                              "total_articles",
                              # market-intelligence uses its own spelling
                              "total_items",
                              # venture-news uses its own spelling
                              "new")
                    if k in data
                ),
                None,
            )
            if name == "thought-leader-scan":
                items = (data.get("leader_items") or 0) + (data.get("general_items") or 0)

            failed = data.get("feeds_failed", 0)
            entry.update(age_hours=round(age_hours, 1) if age_hours is not None else None,
                         items=items, feeds_failed=failed)

            status = data.get("status", "")
            raw_fetched = data.get("raw_entries_fetched")

            if age_hours is not None and age_hours > meta["max_age_hours"]:
                entry.update(
                    state="stale",
                    detail=f"last run {age_hours:.0f}h ago (expected within {meta['max_age_hours']}h)",
                )
            elif status == "failed":
                # The job ran but exited non-zero. Carry the real error through so
                # the report is actionable without digging in the cron ledger.
                err = str(data.get("error", "")).strip().replace("\n", " ")
                entry.update(
                    state="failed",
                    detail=f"last run FAILED: {err[:160] or 'no error recorded'}",
                )
            elif status in ("zero-fetched", "all-feeds-failed", "zero-items"):
                entry.update(
                    state="zero",
                    detail="fetched NOTHING from any feed — feeds are broken",
                )
            elif status == "no-new-items":
                # Fetched fine, everything was already seen. Normal on a rerun or
                # a quiet weekend; NOT a failure. Reported so it stays visible.
                detail = (
                    "no new/changed files to index"
                    if "files_indexed" in data
                    else f"no new items ({raw_fetched or 0} fetched, all already seen)"
                )
                entry.update(state="ok", detail=detail)
            elif failed:
                entry.update(
                    state="degraded",
                    detail=f"{failed} feed(s) failed on the last run",
                )
            elif meta.get("zero_is_failure") and items is None:
                # No recognised item-count key. That is NOT the same as zero, and
                # reporting it as zero raises a false alarm — which is exactly what
                # happened when this rule first shipped and market-intelligence
                # turned out to use `total_items` rather than `total_articles`.
                # Name the keys so the fix is a one-line mapping, not a hunt.
                entry.update(
                    state="error",
                    detail=("cannot find an item count in the health file — add its "
                            f"key to the mapping. Keys present: {sorted(data)}"),
                )
            elif meta.get("zero_is_failure") and not items:
                # These jobs write no `status` key, so without this an empty run
                # falls through to "ok — 0 items captured". That is the exact hole
                # the July trend degradation slipped through for ten days.
                entry.update(
                    state="zero",
                    detail=("captured 0 articles — a 30+ feed corpus should never "
                            "return nothing; check feeds and the dedup filter"),
                )
            elif data.get("conformant") is False:
                # trend_contract.py now flags malformed/miscategorized reports.
                # A non-conformant report (missing synthesis, missing section,
                # academic leak into a consumer vertical) is a real quality failure,
                # even if items were captured.
                issues = data.get("contract_issues", [])
                detail = "; ".join(str(i) for i in issues[:3]) if issues else "failed contract validation"
                entry.update(state="degraded", detail=f"non-conformant report: {detail}")
            else:
                entry.update(state="ok", detail=f"{items} items captured")

        results.append(entry)

    return results


def read_latest_bias_score() -> Optional[Dict[str, Any]]:
    """Pull the most recent weekly bias-balance result, if one exists."""
    health = OUTPUT_DIR / "bias-balance-check-health.json"
    if not health.exists():
        return None
    try:
        return json.loads(health.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _state_icon(state: str) -> str:
    return {
        "ok": "✅", "degraded": "🟡", "stale": "🟠",
        "zero": "🔴", "missing": "⚫", "error": "🔴", "failed": "🔴",
    }.get(state, "❔")


def _intel_actions(jobs: List[Dict[str, Any]]) -> str:
    """Turn unhealthy job states into concrete next steps."""
    bad = [j for j in jobs if j["state"] != "ok"]
    if not bad:
        return "**All intelligence jobs healthy.** ✅"

    lines = ["**Action needed:**", ""]
    for j in bad:
        if j["state"] == "zero":
            lines.append(
                f"- 🔴 `{j['job']}` captured zero items. Probe its feeds — see "
                "`_known_dead` in `~/.hermes/thought-leaders.json` for the URLs already "
                "confirmed broken."
            )
        elif j["state"] == "missing":
            lines.append(
                f"- ⚫ `{j['job']}` has never written health. Confirm it is in cron and "
                f"run it manually once: `python3 ~/.hermes/scripts/{j['job']}.py`"
            )
        elif j["state"] == "stale":
            lines.append(f"- 🟠 `{j['job']}` is stale — {j['detail']}. Check the cron entry and its log.")
        elif j["state"] == "degraded":
            lines.append(f"- 🟡 `{j['job']}` — {j['detail']}. Replace the dead feeds.")
        elif j["state"] == "failed":
            lines.append(
                f"- 🔴 `{j['job']}` — {j['detail']}. Reproduce under the real cron "
                f"environment (`hermes cron run <job-id>`), not just by hand — a "
                f"hand-run picks up a shell PATH the cron runner does not have."
            )
        else:
            lines.append(f"- 🔴 `{j['job']}` — {j['detail']}")
    return "\n".join(lines)


ORPHAN_SCRIPT = Path.home() / ".hermes/scripts/vault-orphan-scan.py"
SCHEMA_SCRIPT = Path.home() / ".hermes/scripts/vault-schema-lint.py"


def scan_schema() -> Optional[Dict[str, Any]]:
    """Run the schema linter and return its summary, or None if it can't run."""
    if not SCHEMA_SCRIPT.exists():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(SCHEMA_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout).get("summary")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _schema_section(s: Optional[Dict[str, Any]]) -> str:
    """Render frontmatter drift against the Schema v2 contract."""
    if s is None:
        return "⚫ **Schema lint did not run.** Check `~/.hermes/scripts/vault-schema-lint.py`."

    rows = [
        ("Files fully compliant", f"{s['compliant']} / {s['total_files']} ({s['compliance_pct']}%)"),
        ("No frontmatter at all", s["no_frontmatter"]),
        ("Missing a required field", s["missing_required"]),
        ("Notion residue only (no type/stage)", s["notion_residue_only"]),
        ("Undateable imports (no recoverable `created`)", s.get("undateable_imports", "—")),
        ("CRM values squatting in `stage`", s["crm_in_stage"]),
        ("`type` contradicts its folder", s.get("type_path_mismatch", "—")),
        ("`domain` overrides its folder (confirm)", s.get("domain_override", "—")),
        ("Non-canonical `status` values", s["bad_status"]),
        ("Distinct `status` values in use", f"{s['distinct_status_values']} (target: 6)"),
        ("Distinct tags in use", f"{s['distinct_tags']} (target: ~40)"),
    ]
    out = ["| Check | Count |", "|-------|-------|"]
    out += [f"| {k} | {v} |" for k, v in rows]
    out.append("")
    out.append(
        "**Action:** Frontmatter is the query surface for Bases, Dataview and every "
        "`type`/`status` filter in these scripts. While `status` has "
        f"{s['distinct_status_values']} spellings, exact-match filters silently miss rows."
    )
    if s.get("undateable_imports"):
        out.append("")
        out.append(
            f"*The {s['undateable_imports']} undateable imports are Notion notes whose "
            "export carried no creation date. They are excluded from the compliance "
            "figure because no action inside the vault can fix them — recovery means "
            "matching titles against the original Notion workspace. Until 2026-07-26 "
            "they carried a `created` value copied from the export timestamp, which "
            "looked precise and was wrong.*"
        )
    return "\n".join(out)


def scan_orphans() -> Optional[Dict[str, Any]]:
    """Run the orphan scanner and return its counts, or None if it can't run.

    The scanner already resolves links the way Obsidian does; it just was not
    feeding this report, so a hand-written orphan summary claiming zero orphans
    sat in the vault while the scanner found over a hundred.
    """
    if not ORPHAN_SCRIPT.exists():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(ORPHAN_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            return None
        return json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _orphan_section(orphans: Optional[Dict[str, Any]]) -> str:
    """Render orphan/broken-link findings grouped by directory."""
    if orphans is None:
        return (
            "⚫ **Orphan scan did not run.** Check "
            "`~/.hermes/scripts/vault-orphan-scan.py`."
        )

    count = orphans.get("orphan_count", 0)
    total = orphans.get("total", 0)
    unresolved_total = orphans.get("unresolved_total", 0)
    orphan_list = orphans.get("orphans", [])

    lines = [
        f"**{count} orphaned notes** (no incoming links) out of {total} files. "
        f"**{unresolved_total} unresolved link targets.**",
        "",
    ]

    if count:
        by_dir: Dict[str, int] = {}
        for f in orphan_list:
            d = os.path.dirname(f) or "<vault root>"
            by_dir[d] = by_dir.get(d, 0) + 1
        lines += ["| Directory | Orphans |", "|-----------|---------|"]
        lines += [
            f"| `{d}` | {n} |"
            for d, n in sorted(by_dir.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ]
        lines.append("")

    top_unresolved = orphans.get("unresolved_top", [])[:10]
    if top_unresolved:
        lines += ["**Most-referenced missing targets:**", ""]
        lines += [f"- `{t}` — referenced {n}×" for n, t in top_unresolved]
        lines.append("")
        lines.append(
            "*Note: `.base` targets show as unresolved because the scanner only "
            "resolves `.md` files — those are false positives.*"
        )
        lines.append("")

    lines.append(
        "**Action:** Link orphans from a MOC or index, or archive them. "
        "Fix or remove the broken link targets."
    )
    return "\n".join(lines)


def _external_raw_section(external_raw: List[tuple]) -> str:
    """Render measured external raw-capture counts, or say so when there are none."""
    if not external_raw:
        return (
            "### External Raw Captures (linked, not moved)\n\n"
            "**No external raw captures pending.** ✅"
        )
    rows = "\n".join(
        f"| [[{label.split(' ')[0]}|{label}]] | {count} | {note} |"
        for label, count, note in external_raw
    )
    return (
        "### External Raw Captures (linked, not moved)\n\n"
        "| Source | Count | Notes |\n"
        "|--------|-------|-------|\n"
        f"{rows}\n\n"
        "**Action:** Review and triage. Promote evergreen signals to "
        "[[System/Wiki/index|System/Wiki]] or cite in reports."
    )


def _bias_status(bias: Optional[Dict[str, Any]]) -> str:
    if not bias:
        return "⚫ Never run"
    score = bias.get("overall_diversity_score", 0)
    if score >= 7:
        return "✅ Balanced"
    if score >= 5:
        return "🟡 Thin"
    return "🔴 Single-perspective risk"


def _bias_section(bias: Optional[Dict[str, Any]]) -> str:
    if not bias:
        return (
            "`bias-balance-check.py` has not run yet. Once it does, this section reports "
            "the weekly viewpoint-diversity score and flags single-perspective topics."
        )
    score = bias.get("overall_diversity_score", 0)
    single = bias.get("single_perspective_clusters", 0)
    unclassified = bias.get("hosts_unclassified", 0)
    total_hosts = bias.get("hosts_total", 0)

    out = f"""| Metric | Value | Target |
|--------|-------|--------|
| Overall diversity score | {score}/10 | ≥7 |
| Topic clusters analysed | {bias.get('clusters_analysed', 0)} | — |
| Single-perspective clusters | {single} | 0 |
| Unclassified source hosts | {unclassified}/{total_hosts} | <50% |

"""
    if score < 7:
        out += (
            f"🚩 **Diversity below target.** See the latest "
            f"[[Knowledge/20-Reports/Bias-Balance-{datetime.now().isocalendar()[0]}-W{datetime.now().isocalendar()[1]:02d}|Bias Balance Report]] "
            "for the specific gaps and recommended feeds.\n\n"
        )
    if total_hosts and unclassified / total_hosts > 0.5:
        out += (
            f"🚩 **{unclassified} of {total_hosts} source hosts are unclassified.** The "
            "perspective map in `~/.hermes/scripts/bias_taxonomy.py` is lagging the feed "
            "list, so the score above is optimistic.\n"
        )
    return out


def generate_report() -> str:
    """Generate the full health check report."""
    raw_items, external_raw = scan_raw_items()
    not_distilled = scan_reports()
    aging_wiki = scan_wiki_pages()
    intel_jobs = scan_intelligence_jobs()
    bias = read_latest_bias_score()
    orphans = scan_orphans()
    schema = scan_schema()
    
    report = f"""---
title: Flywheel Health Check — {datetime.now().strftime('%Y-%m-%d')}
type: system
generated: {datetime.now().isoformat()}
tags: [flywheel, health-check, system]
---

# 🛠️ Flywheel Health Check — {datetime.now().strftime('%Y-%m-%d')}

**Generated:** {datetime.now().strftime('%A, %d %B %Y — %H:%M %Z')}
**Next check:** {datetime.now() + timedelta(days=7):%A, %d %B %Y}

---

## Summary

| Check | Status | Count |
|-------|--------|-------|
| Raw items >30 days unprocessed | {'🔴 Action needed' if raw_items else '✅ Clear'} | {len(raw_items)} |
| Reports shipped but not distilled | {'🔴 Action needed' if not_distilled else '✅ Clear'} | {len(not_distilled)} |
| Wiki pages >90 days not reviewed | {'🟡 Review recommended' if aging_wiki else '✅ Current'} | {len(aging_wiki)} |
| Orphaned notes (no incoming links) | {'⚫ Scan failed' if orphans is None else ('🟡 Action needed' if orphans.get('orphan_count') else '✅ None')} | {'—' if orphans is None else orphans.get('orphan_count', 0)} |
| Frontmatter schema compliance | {'⚫ Lint failed' if schema is None else ('✅ Healthy' if schema.get('compliance_pct', 0) >= 80 else '🔴 Action needed')} | {'—' if schema is None else f"{schema.get('compliance_pct', 0)}%"} |
| Intelligence jobs unhealthy | {'🔴 Action needed' if [j for j in intel_jobs if j['state'] not in ('ok',)] else '✅ All healthy'} | {len([j for j in intel_jobs if j['state'] not in ('ok',)])} |
| Source diversity score | {_bias_status(bias)} | {bias.get('overall_diversity_score', 'n/a') if bias else 'n/a'} |

---

## 0. Intelligence Cron Health

Every intelligence job writes `System/<job>-health.json`. A job that fetches
**nothing at all** is a failure (`zero`) — that silent mode is how
`thought-leader-scan.py` produced empty reports for a day while looking healthy.
A job that fetches normally but finds nothing *new* stays `ok`, since dedup
filtering that out is correct behaviour on a rerun or a quiet weekend.

| Job | Schedule | State | Items | Last run | Detail |
|-----|----------|-------|-------|----------|--------|
""" + "\n".join(
        f"| `{j['job']}` | {j['schedule']} | {_state_icon(j['state'])} {j['state']} | "
        f"{j['items'] if j['items'] is not None else '—'} | "
        f"{str(j['age_hours']) + 'h ago' if j['age_hours'] is not None else '—'} | {j['detail']} |"
        for j in intel_jobs
    ) + f"""

{_intel_actions(intel_jobs)}

---

## 0b. Source Diversity & Bias Balance

{_bias_section(bias)}

---

## 1. Raw Items Unprocessed >30 Days

{'**No raw items older than 30 days.** ✅' if not raw_items else f'''
| File | Created | Status | Days Old |
|------|---------|--------|----------|
''' + chr(10).join(f"| [[Knowledge/00-Raw/{item['file']}|{item['file']}]] | {item['created']} | {item['status']} | {item['days_old']} |" for item in raw_items)}

{_external_raw_section(external_raw)}

---

## 2. Reports Shipped But Not Distilled

{'**All shipped reports have been distilled.** ✅' if not not_distilled else f'''
| Report | Created | Days Since Shipped | Sources |
|--------|---------|-------------------|---------|
''' + chr(10).join(f"| [[Knowledge/20-Reports/{item['file'].replace('.md', '')}|{item['file'].replace('.md', '')}]] | {item['created']} | {item['days_since_shipped']} | {item['sources'][:50]}... |" for item in not_distilled)}

**Action:** Extract best practices and workflows to [[Knowledge/30-Distilled/_index|30-Distilled]]. Set `distilled: true` when done.

---

## 3. Wiki Pages Not Reviewed >90 Days

{'**All wiki pages reviewed within 90 days.** ✅' if not aging_wiki else f'''
| Page | Last Reviewed | Days Since | Type |
|------|---------------|------------|------|
''' + chr(10).join(f"| [[System/Wiki/{item['file'].replace('.md', '')}|{item['file'].replace('.md', '')}]] | {item['last_reviewed']} | {item['days_since_review'] if item['days_since_review'] is not None else '—'} | {item['type']} |" for item in aging_wiki[:20])}

{'**Note:** Showing top 20 oldest. Full list has {} pages.'.format(len(aging_wiki)) if len(aging_wiki) > 20 else ''}

{'' if not aging_wiki else f"**Breakdown:** {len([p for p in aging_wiki if p['days_since_review'] is not None])} overdue by date, {len([p for p in aging_wiki if p['days_since_review'] is None])} with no date in frontmatter (age unknowable until backfilled)."}

**Action:** Review and update `last-reviewed:` date in frontmatter. Archive if obsolete.

---

## 4. Orphaned Notes & Broken Links

{_orphan_section(orphans)}

---

## 5. Frontmatter Schema Drift

{_schema_section(schema)}

---

## Trend History

| Date | Raw >30d | Not Distilled | Wiki >90d | Orphans |
|------|----------|---------------|-----------|---------|
| {datetime.now().strftime('%Y-%m-%d')} | {len(raw_items)} | {len(not_distilled)} | {len(aging_wiki)} | {'—' if orphans is None else orphans.get('orphan_count', 0)} |

---

*Generated by `flywheel-health-check.py` | Next run: Monday 6AM*
"""
    return report

def main():
    """Main entry point."""
    report = generate_report()
    
    # Write report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = REPORT_DIR / f"flywheel-health-{datetime.now().strftime('%Y-%m-%d')}.md"
    output_file.write_text(report)
    
    print(f"Flywheel health check complete: {output_file}")
    print(f"  - Raw items >30 days: {len(scan_raw_items()[0])}")
    print(f"  - Reports not distilled: {len(scan_reports())}")
    print(f"  - Wiki pages >90 days: {len(scan_wiki_pages())}")

if __name__ == "__main__":
    main()
