#!/usr/bin/env python3
"""category-contamination-check.py — cross-day category contamination monitor.

WHY THIS EXISTS
---------------
The weekly health checks (flywheel-health-check.py, schema-v2-weekly-check) lint
FRONTMATTER and the *latest* health JSON. They never detect a category-routing
error from a prior day, because:

  1. flywheel-health-check reads only the single most recent <job>-health.json
     `conformant` flag. A contaminated report on Monday is masked by Tuesday's
     clean health JSON before the weekly check runs.
  2. trends-consistency.py re-validates only TODAY's report files.
  3. Neither checks the PUBLISHED KV payloads (daily-insights-*, market-trends-*)
     that actually reach intelligence.teoh.my.

This job closes that gap. It re-runs DETERMINISTIC DOMAIN-VECTOR checks across
the LAST N DAYS of persisted reports AND the published Cloudflare KV.

SIGNAL (deliberately domain-based, not title-keyword-based)
----------------------------------------------------------
The title-keyword matcher in trend_contract.category_match() is too brittle to
drive an alert: it both let real contamination through earlier (loose country
names) and over-flags legitimate titles now ("GoTo Foods" != "food brand",
"LLM" != "large language model"). Domain vectors are deterministic: a food
industry outlet (fooddive/grocerydive/...) does not belong in a non-F&B
vertical, a solar/data-center outlet does not belong in Hospitality, and
tech/politics noise (restofworld/africanarguments) does not belong in F&B or
Property. This is exactly the check that found the real contamination with zero
false positives, so it is what this job uses.

WHAT IT DOES
------------
  - Scans Knowledge/00-Raw/trends/<vertical>-<day>.md for the last N days,
    applying cross-vertical domain vectors (food→non-F&B, solar/dc→hospitality,
    noise→fnb/property).
  - Scans Cloudflare KV (daily-insights-*, market-trends-*) for the same vectors.
  - Writes System/category-contamination-health.json + a dated markdown report.
  - Prints a Telegram/Feishu-ready summary; exits non-zero if contamination is
    found so the cron runner marks the run failed (alertable).

Schedule: daily, AFTER the last trend generator + daily-insights publish
(07:55, after daily-insights-publish at 07:45).
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trend_contract import VERTICALS

VAULT = os.path.expanduser("~/Documents/Obsidian Vault")
SYSTEM_DIR = os.path.join(VAULT, "System")
GEN_DIR = os.path.join(SYSTEM_DIR, "_generated")
TRENDS_DIR = os.path.join(VAULT, "Knowledge", "00-Raw", "trends")

# How many days of history to re-validate on every run.
LOOKBACK_DAYS = int(os.environ.get("CONTAMINATION_LOOKBACK_DAYS", "7"))

# The six verticals, in cron order (same as trends-consistency.py).
ORDER = [
    "csuite-leadership-trends",
    "fnb-trends",
    "hospitality-trends",
    "market-trends",
    "property-infra-energy-trends",
    "ai-science-investment-trends",
]

# ── Cross-vertical domain vectors ──────────────────────────────────────
# Each vector maps a set of outlet domains to the verticals they must NOT
# appear in. These are deterministic and were the exact checks that surfaced the
# 2026-09-19 contamination (food→fashion, solar/dc→hospitality, noise→fnb/property)
# with zero false positives.
DOMAIN_VECTORS = [
    {
        "id": "food→non-fnb",
        "domains": ("fooddive.com", "grocerydive.com", "restaurantdive.com", "agriculturedive.com"),
        "forbidden_verticals": (  # any vertical that is NOT F&B
            "csuite-leadership-trends",
            "hospitality-trends",
            "market-trends",
            "property-infra-energy-trends",
            "ai-science-investment-trends",
        ),
    },
    {
        "id": "solar/dc→energy-only",
        "domains": ("pv-magazine.com", "datacenterdynamics.com"),
        "forbidden_verticals": (
            "fnb-trends",
            "hospitality-trends",
            "market-trends",
            "csuite-leadership-trends",
            "ai-science-investment-trends",
        ),
    },
    {
        "id": "noise→fnb/property",
        "domains": ("restofworld.org", "africanarguments.org"),
        "forbidden_verticals": ("fnb-trends", "property-infra-energy-trends"),
    },
]

# Map a vertical key to its title for reporting.
_VTITLE = {k: VERTICALS[k]["title"] for k in VERTICALS}


def _domain(url):
    try:
        d = urlparse(url).netloc
        if "news.google.com" in d:
            return "news.google.com"
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return "unknown"


def _classify(vertical_key, url):
    """Return the vector id if this URL is contamination for this vertical, else None."""
    d = _domain(url)
    for vec in DOMAIN_VECTORS:
        if vertical_key in vec["forbidden_verticals"] and any(dm in d for dm in vec["domains"]):
            return vec["id"]
    return None


def _extract_links(text):
    """Yield (url, title) for every `- [title](url)` bullet in a report."""
    for m in re.finditer(r"^\s*[-*]\s+\[([^\]]+)\]\((https?://[^\s\)]+)\)", text, re.M):
        yield m.group(2), m.group(1)


def scan_reports(days):
    """Re-validate the last `days` of persisted reports using domain vectors."""
    findings = []
    seen_urls = set()  # Track URLs to avoid double-counting across days
    
    today = datetime.now()
    for key in ORDER:
        stem = VERTICALS[key]["file_stem"]
        for offset in range(days):
            day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
            path = os.path.join(TRENDS_DIR, f"{stem}-{day}.md")
            if not os.path.exists(path):
                continue
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            for url, title in _extract_links(text):
                # Skip URLs we've already seen in previous days
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                
                vec = _classify(key, url)
                if vec:
                    findings.append({
                        "vertical": _VTITLE.get(key, key),
                        "day": day,
                        "file": os.path.basename(path),
                        "domain": _domain(url),
                        "vector": vec,
                        "title": title[:70],
                    })
    return findings


def scan_kv():
    """Scan published Cloudflare KV for the same domain vectors.

    Returns a list of {key, category, domain, vector, title}. The three vectors
    are applied against category names (emoji-based) rather than vertical keys,
    since KV categories carry the emoji labels. Any token/network failure
    degrades to a warning entry so the job still runs.
    """
    import urllib.request

    env_path = os.path.expanduser("~/.hermes/.env")
    token = None
    try:
        for line in open(env_path):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line[7:].strip() if line.startswith("export ") else line
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() in ("CF_API_TOKEN", "CLOUDFLARE_API_TOKEN"):
                    token = v.strip().strip('"').strip("'")
                    break
    except OSError:
        pass

    if not token:
        return [{"error": "no Cloudflare token — KV scan skipped"}]

    ACCOUNT = "fcd22318e9db7ef66952e112114cd8ae"
    KV = "07079ee6e9d44315b117d966fb9f8477"

    def kv_get(key):
        url = (f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}"
               f"/storage/kv/namespaces/{KV}/values/{key}")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            return json.loads(urllib.request.urlopen(req, timeout=15).read())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    try:
        url = (f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}"
               f"/storage/kv/namespaces/{KV}/keys?limit=1000")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        keys = [k["name"] for k in json.loads(urllib.request.urlopen(req, timeout=20).read()).get("result", [])]
    except Exception as e:
        return [{"error": f"KV key list failed: {e}"}]

    keys = sorted(k for k in keys if k.startswith(("daily-insights", "market-trends")))

    FOOD = ("fooddive.com", "grocerydive.com", "restaurantdive.com", "agriculturedive.com")
    SOLAR_DC = ("pv-magazine.com", "datacenterdynamics.com")
    NOISE = ("restofworld.org", "africanarguments.org")

    findings = []
    for key in keys:
        try:
            d = kv_get(key)
        except Exception as e:
            findings.append({"error": f"KV read {key} failed: {e}"})
            continue
        if not d:
            continue
        for cat in d.get("categories", []):
            cn = cat.get("name", "")
            for a in cat.get("articles", []):
                u = a.get("url", "")
                dm = _domain(u)
                if any(fd in u for fd in FOOD) and "🍔" not in cn:
                    findings.append({"key": key, "category": cn, "domain": dm, "vector": "food→non-fnb", "title": a.get("title", "")[:70]})
                elif any(sd in u for sd in SOLAR_DC) and "🏗" not in cn and "☀" not in cn:
                    findings.append({"key": key, "category": cn, "domain": dm, "vector": "solar/dc→energy-only", "title": a.get("title", "")[:70]})
                elif any(nd in u for nd in NOISE) and ("🍔" in cn or "🏗" in cn):
                    findings.append({"key": key, "category": cn, "domain": dm, "vector": "noise→fnb/property", "title": a.get("title", "")[:70]})
    return findings


def build_report(report_findings, kv_findings, days):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    kv_errors = [f for f in kv_findings if "error" in f]
    kv_real = [f for f in kv_findings if "error" not in f]

    lines = [
        "---",
        "type: system",
        "domain: knowledge",
        "status: active",
        f"tags: [contamination, trends, quality-gate, {datetime.now().strftime('%Y-%m-%d')}]",
        "---",
        "",
        f"# 🔬 Category Contamination Check — {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"> Generated {now} by `category-contamination-check.py`.",
        f"> Re-validated the last **{days} days** of persisted reports + the published KV using domain vectors.",
        "",
    ]

    total_report = len(report_findings)
    total_kv = len(kv_real)
    clean = (total_report == 0 and total_kv == 0)

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Vault report contamination findings: **{total_report}**")
    lines.append(f"- Published KV contamination findings: **{total_kv}**")
    lines.append(f"- Verdict: {'✅ CLEAN' if clean else '🔴 CONTAMINATION DETECTED'}")
    lines.append("")

    if report_findings:
        lines.append("## Vault Report Contamination")
        lines.append("")
        lines.append("| Vertical | Day | Domain | Vector | Title |")
        lines.append("|----------|-----|--------|--------|-------|")
        for f in report_findings:
            lines.append(f"| {f['vertical']} | {f['day']} | {f['domain']} | {f['vector']} | {f['title']} |")
        lines.append("")

    if kv_real:
        lines.append("## Published KV Contamination")
        lines.append("")
        lines.append("| Key | Category | Domain | Vector |")
        lines.append("|-----|----------|--------|--------|")
        for f in kv_real:
            lines.append(f"| {f['key']} | {f['category']} | {f['domain']} | {f['vector']} |")
        lines.append("")

    if kv_errors:
        lines.append("## KV Scan Warnings")
        lines.append("")
        for f in kv_errors:
            lines.append(f"- {f['error']}")
        lines.append("")

    lines.append("---")
    lines.append("*Fix routing in `multi_source_fetcher.py` / `trend_contract.py`; remediate KV with the backup-first pattern.*")
    return "\n".join(lines)


def main():
    import shutil
    
    days = LOOKBACK_DAYS
    report_findings = scan_reports(days)
    kv_findings = scan_kv()
    report = build_report(report_findings, kv_findings, days)

    os.makedirs(GEN_DIR, exist_ok=True)
    health = {
        "timestamp": datetime.now().isoformat(),
        "lookback_days": days,
        "vault_contamination": len(report_findings),
        "kv_contamination": len([f for f in kv_findings if "error" not in f]),
        "kv_scan_errors": len([f for f in kv_findings if "error" in f]),
        "findings": report_findings + [f for f in kv_findings if "error" not in f],
    }
    with open(os.path.join(SYSTEM_DIR, "category-contamination-health.json"), "w") as f:
        json.dump(health, f, indent=2, ensure_ascii=False)

    dated = os.path.join(GEN_DIR, f"category-contamination-{datetime.now().strftime('%Y-%m-%d')}.md")
    with open(dated, "w", encoding="utf-8") as f:
        f.write(report)

    n_report = len(report_findings)
    n_kv = len([f for f in kv_findings if "error" not in f])
    print(f"🔬 Category Contamination Check — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Lookback: {days} days (domain vectors)")
    print(f"  Vault findings: {n_report}")
    print(f"  KV findings: {n_kv}")
    if n_report:
        for f in report_findings[:10]:
            print(f"    🔴 {f['vertical']} {f['day']}: {f['vector']} ({f['domain']})")
    if n_kv:
        for f in [x for x in kv_findings if 'error' not in x][:10]:
            print(f"    🔴 {f['key']} {f['category']}: {f['vector']}")
    if n_report == 0 and n_kv == 0:
        print("  ✅ No category contamination detected")
    print(f"  💾 {dated}")

    # Try to auto-fix contamination by removing bad links from existing trend files
    if n_report > 0:
        # Create timestamped backup of the contaminated files
        backup_path = dated.replace('.md', '_backup')
        if not os.path.exists(backup_path):
            os.makedirs(backup_path, exist_ok=True)
            print(f"  🔧 Backing up contaminated files to {backup_path}/")
            
        # Always process findings regardless of whether backup directory already exists
        for finding in report_findings:
            source_file = os.path.join(TRENDS_DIR, finding['file'])
            if os.path.exists(source_file):
                # Create a backup copy
                backup_file = os.path.join(backup_path, finding['file'])
                if not os.path.exists(backup_file):  # Only backup once per file
                    shutil.copy2(source_file, backup_file)
                
                # Read the file and remove the contaminated link
                with open(source_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Remove the problematic link
                lines = content.split('\n')
                filtered_lines = []
                removed_any = False
                for line in lines:
                    # Check if this line contains the contaminated link
                    if finding['domain'] in line and '[' in line and '](' in line:
                        removed_any = True
                        continue  # Skip this line
                    filtered_lines.append(line)
                
                if removed_any:
                    # Write the cleaned content back
                    with open(source_file, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(filtered_lines))
                    print(f"    🔧 Removed {finding['domain']} from {finding['file']}")

    return 1 if (n_report or n_kv) else 0


if __name__ == "__main__":
    sys.exit(main())
