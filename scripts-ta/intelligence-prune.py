#!/usr/bin/env python3
"""intelligence-prune.py — weekly pruning of consumed intelligence raw captures.

Intelligence flywheel retention policy (added 2026-10-08):

  Raw captures are deleted from the vault only AFTER they have been ingested
  into the distilled layer (the daily brief), and only after 30 days.

Tiers:
  TIER 1 (>30d, ingested):   capture is >30 days old AND a Daily Intelligence
                             Brief exists for its date WITH `synthesis: true`
                             → safe: content already lives in the insights layer.
  TIER 2 (>60d, force):      capture is >60 days old regardless of brief state
                             → absolute retention ceiling so the raw layer can
                             never grow unbounded. The brief for that date is
                             the permanent record.

NEVER pruned (dedup memory must outlive the captures it guards):
  - ~/.hermes/data/seen-urls.json        (URL/title dedup store — layers 1-4)
  - ~/.hermes/arxiv-seen.json            (arXiv paper hashes)
  - Knowledge/20-Reports/ briefs         (the insights record)
  - Anything that does not match a producer glob from intelligence_source_of_truth

Safety design:
  - Matches ONLY producer globs (canonical PRODUCER_LIST) under Knowledge/00-Raw/.
  - Moves files to the vault's `.trash/` (Obsidian-recoverable), never `rm`.
  - DRY-RUN by default; pass --apply to execute.
  - Every pruned file is recorded in System/pruned-captures-index.json with the
    URLs it contained (second dedup guard besides seen-urls.json).
  - Regenerates System/raw-captures-index.json — the live inventory of raw
    captures (the anti-duplicate naming registry).
  - Deletes KV dated keys (daily-insights-YYYY-MM-DD) older than 30 days so the
    dashboard keeps only the current view + 30d history.
  - Health: System/intelligence-prune-health.json via hermes_feeds.write_health.

Schedule: Sundays 07:30 (slot vacated by the paused daily-insights-publish),
after the last morning producer, independent of the 08:00 loop.
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from hermes_feeds import write_health
from intelligence_source_of_truth import PRODUCER_LIST

VAULT = Path("/Users/admin/Documents/Obsidian Vault")
RAW = VAULT / "Knowledge" / "00-Raw"
REPORTS = VAULT / "Knowledge" / "20-Reports"
SYSTEM = VAULT / "System"
TRASH = VAULT / ".trash"
PRUNED_INDEX = SYSTEM / "pruned-captures-index.json"
CAPTURES_INDEX = SYSTEM / "raw-captures-index.json"
CF_ACCOUNT_ID = "fcd22318e9db7ef66952e112114cd8ae"
CF_KV_ID = "07079ee6e9d44315b117d966fb9f8477"
TIER1_DAYS = 30   # prune if ingested (brief with synthesis)
TIER2_DAYS = 60   # absolute ceiling — prune regardless
URL_RE = re.compile(r"\]\((https?://[^)\s]+)\)")


def _env():
    vals = {}
    try:
        for line in (Path.home() / ".hermes/.env").read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return vals


def capture_date(path: Path):
    """Capture date: frontmatter `created:` → filename YYYY-MM-DD → mtime."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")[:1200]
        m = re.search(r"^created:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
        if m:
            return date.fromisoformat(m.group(1))
    except OSError:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def brief_synthesis(day: date):
    """True iff Daily-Intelligence-Brief-<day>.md exists with synthesis: true."""
    p = REPORTS / f"Daily-Intelligence-Brief-{day.isoformat()}.md"
    if not p.exists():
        return False
    try:
        head = p.read_text(encoding="utf-8", errors="ignore")[:900]
        return bool(re.search(r"^synthesis:\s*true\s*$", head, re.M | re.I))
    except OSError:
        return False


def producer_match(path: Path):
    """Return the producer label if path matches a canonical producer glob."""
    try:
        rel = path.relative_to(RAW).as_posix()
    except ValueError:
        return None
    for label, glob, _emoji, _desc in PRODUCER_LIST:
        # fnmatch-style: try both against the relative path
        import fnmatch
        if fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(path.name, os.path.basename(glob)):
            return label
    return None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def prune_captures(today: date, apply: bool):
    """Tier-scan producer captures; move eligible ones to .trash; index them."""
    pruned_index = load_json(PRUNED_INDEX, {"pruned": [], "updated_at": None})
    already = {e["file"] for e in pruned_index.get("pruned", [])}
    results = {"tier1": [], "tier2": [], "skipped_not_ingested": [],
               "skipped_anomaly": [], "non_producer_ignored": 0}
    candidates = []
    for label, glob, _emoji, _desc in PRODUCER_LIST:
        candidates.extend(RAW.glob(glob))
    seen_paths = set()
    for path in candidates:
        if path in seen_paths or not path.is_file():
            continue
        seen_paths.add(path)
        cdate = capture_date(path)
        if cdate is None:
            results["skipped_anomaly"].append({"file": path.name, "reason": "no date"})
            continue
        age_days = (today - cdate).days
        rel = path.relative_to(VAULT).as_posix()
        if rel in already:
            continue  # indexed in a previous run but still on disk (unlikely)
        if age_days <= TIER1_DAYS:
            continue
        label = producer_match(path)
        if not label:
            results["non_producer_ignored"] += 1
            continue
        if age_days <= TIER2_DAYS and not brief_synthesis(cdate):
            # >30d but not provably ingested — keep while <60d
            results["skipped_not_ingested"].append(
                {"file": rel, "date": cdate.isoformat(), "age_days": age_days})
            continue
        entry = {
            "file": rel,
            "producer": label,
            "capture_date": cdate.isoformat(),
            "age_days": age_days,
            "tier": 1 if age_days <= TIER2_DAYS else 2,
            "brief": f"Daily-Intelligence-Brief-{cdate.isoformat()}",
            "urls": sorted(set(URL_RE.findall(
                path.read_text(encoding='utf-8', errors='ignore'))))[:80],
            "pruned_at": datetime.now().isoformat(),
        }
        results["tier1" if entry["tier"] == 1 else "tier2"].append((path, entry))

    if apply:
        TRASH.mkdir(exist_ok=True)
        for path, entry in results["tier1"] + results["tier2"]:
            dest = TRASH / path.name
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            if dest.exists():
                dest = TRASH / f"{stamp}-{path.name}"
            os.replace(path, dest)  # vault .trash — recoverable, never rm
            pruned_index["pruned"].append(entry)
    pruned_index["updated_at"] = datetime.now().isoformat()
    return results, pruned_index


def rebuild_captures_index():
    """Live inventory of remaining raw captures — the anti-duplicate registry."""
    inv = []
    for label, glob, _emoji, _desc in PRODUCER_LIST:
        for path in RAW.glob(glob):
            if not path.is_file():
                continue
            cdate = capture_date(path)
            try:
                head = path.read_text(encoding="utf-8", errors="ignore")[:900]
                st = re.search(r"^status:\s*(\S+)", head, re.M)
                status = st.group(1) if st else "?"
            except OSError:
                status = "?"
            inv.append({"file": path.relative_to(VAULT).as_posix(), "producer": label,
                        "date": cdate.isoformat() if cdate else None, "status": status})
    inv.sort(key=lambda e: (e["producer"], e["date"] or ""))
    return {"generated_at": datetime.now().isoformat(), "count": len(inv), "captures": inv}


def prune_kv_dated_keys(today: date, apply: bool):
    """Delete daily-insights-YYYY-MM-DD KV keys older than 30 days."""
    env = _env()
    acct, tok = env.get("CLOUDFLARE_ACCOUNT_ID", ""), (
        env.get("CLOUDFLARE_API_TOKEN") or env.get("CF_API_TOKEN", ""))
    if not acct or not tok:
        return {"skipped": "no CF credentials", "deleted": []}
    cutoff = today - timedelta(days=TIER1_DAYS)
    base = f"https://api.cloudflare.com/client/v4/accounts/{acct}/storage/kv/namespaces/{CF_KV_ID}"
    deleted, scanned = [], 0
    try:
        cursor = None
        while True:
            url = f"{base}/keys?limit=1000" + (f"&cursor={cursor}" if cursor else "")
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {tok}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read().decode())
            for k in d.get("result", []):
                name = k.get("name", "")
                m = re.fullmatch(r"daily-insights-(\d{4}-\d{2}-\d{2})", name)
                if not m:
                    continue
                scanned += 1
                if date.fromisoformat(m.group(1)) < cutoff and apply:
                    dr = urllib.request.Request(f"{base}/values/{name}",
                                                headers={"Authorization": f"Bearer {tok}"},
                                                method="DELETE")
                    with urllib.request.urlopen(dr, timeout=30) as r:
                        ok = json.loads(r.read().decode()).get("success")
                    if ok:
                        deleted.append(name)
            cursor = (d.get("result_info") or {}).get("cursor")
            if not cursor:
                break
    except Exception as e:
        return {"error": str(e)[:200], "scanned": scanned, "deleted": deleted}
    return {"scanned": scanned, "deleted_count": len(deleted),
            "deleted": deleted if not apply else deleted[:10], "applied": apply}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="execute pruning (default: dry-run)")
    ap.add_argument("--skip-kv", action="store_true", help="skip Cloudflare KV dated-key pruning")
    args = ap.parse_args()
    today = date.today()

    print(f"🧹 Intelligence prune — {today} ({'APPLY' if args.apply else 'DRY-RUN'})")
    results, pruned_index = prune_captures(today, args.apply)
    t1, t2 = results["tier1"], results["tier2"]
    print(f"  Tier 1 (>30d, ingested): {len(t1)} file(s)")
    for _p, e in t1[:6]:
        print(f"    - {e['file']} ({e['capture_date']}, {e['age_days']}d)")
    if len(t1) > 6:
        print(f"    … and {len(t1) - 6} more")
    print(f"  Tier 2 (>60d force):      {len(t2)} file(s)")
    for _p, e in t2[:6]:
        print(f"    - {e['file']} ({e['capture_date']}, {e['age_days']}d)")
    if len(t2) > 6:
        print(f"    … and {len(t2) - 6} more")
    print(f"  Kept (not provably ingested, <{TIER2_DAYS}d): {len(results['skipped_not_ingested'])}")
    print(f"  Anomalies: {len(results['skipped_anomaly'])} | non-producer paths ignored: "
          f"{results['non_producer_ignored']}")

    if args.apply and (t1 or t2):
        SYSTEM.mkdir(parents=True, exist_ok=True)
        PRUNED_INDEX.write_text(json.dumps(pruned_index, indent=2))
        print(f"  🗂 pruned-captures-index.json: {len(pruned_index['pruned'])} total entries")

    idx = rebuild_captures_index()
    if args.apply:
        CAPTURES_INDEX.write_text(json.dumps(idx, indent=2))
    print(f"  📇 raw-captures-index.json: {idx['count']} live captures"
          f"{' (regenerated)' if args.apply else ' (dry-run: not written)'}")

    kv = {"skipped": True} if args.skip_kv else prune_kv_dated_keys(today, args.apply)
    if isinstance(kv, dict) and kv.get("deleted_count") is not None:
        print(f"  ☁️ KV dated keys: scanned {kv['scanned']}, deleted {kv['deleted_count']}")
    elif isinstance(kv, dict) and kv.get("error"):
        print(f"  ⚠️ KV prune error: {kv['error']}")

    write_health("intelligence-prune", {
        "day": today.isoformat(),
        "mode": "apply" if args.apply else "dry-run",
        "tier1": len(t1), "tier2": len(t2),
        "kept_not_ingested": len(results["skipped_not_ingested"]),
        "anomalies": len(results["skipped_anomaly"]),
        "live_captures": idx["count"],
        "kv": {k: v for k, v in kv.items() if k != "deleted"} if isinstance(kv, dict) else {},
        "index_files": {"pruned": str(PRUNED_INDEX), "captures": str(CAPTURES_INDEX)},
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
