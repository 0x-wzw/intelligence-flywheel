#!/usr/bin/env python3
"""Reconcile Notion round-trip trend reports into the local ones. MERGE, then delete.

History worth knowing before you touch this (2026-07-30):

An earlier version deleted a Notion copy whenever the local file of the same day was
at least as LONG. That was wrong. Re-checked afterwards on subset grounds, 35 of 55
deletions had discarded items that existed nowhere else — up to 23 in a single file.
Length is not containment: the two copies come from different runs of the same day, so
each holds items the other lacks. Neither file is a superset of the other.

There is therefore no safe "just delete the duplicate" operation on this data, and no
legitimate override — which is why this script has no force flag. The only correct
operation is a UNION merge into the local file, after which the Notion copy really is
redundant and can be removed.

Order of operations, per pair:
  1. union the Notion items into the local file (hermes_trend_save.merge_bodies)
  2. re-read and verify the Notion item set is now a strict subset of the local one
  3. only then delete the Notion copy

Step 2 is not decoration: it is the check whose absence caused the data loss above.

    vault-trends-dedupe.py            # dry-run
    vault-trends-dedupe.py --apply
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_trend_save import merge_bodies  # noqa: E402

VAULT = "/Users/admin/Documents/Obsidian Vault"
TRENDS = "Knowledge/00-Raw/trends"
MANIFEST = os.path.expanduser("~/.hermes/logs/trends-merge-manifest.json")
AUDIT = os.path.expanduser("~/.hermes/logs/trends-dedupe.log")

FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)
ITEM_RE = re.compile(r"^\s*[-*]\s+\[", re.M)

# Read off disk 2026-07-30, never guessed: a guessed map previously invented 32 phantom
# "orphans" out of naming mismatches.
FAMILY_MAP = {
    "AI-Science-Investment-Trends":          "ai-science-investment-trends",
    "CSuite-Leadership-Strategy-Insights":   "csuite-leadership-trends",
    "FB-Consumer-Trends":                    "fnb-trends",
    "Hospitality-Healthcare-Trends":         "hospitality-trends",
    "Market-Trends":                         "market-trends",
    "Market-Intelligence":                   "market-intelligence",
    "Property-Infrastructure-Energy-Trends": "property-infra-energy-trends",
}
NOTION_NAME = re.compile(r"^(.+?)-—-(\d{8})\.md$")
LOCAL_NAME = re.compile(r"^([a-z0-9-]+)-(\d{4}-\d{2}-\d{2})\.md$")


def read(p):
    try:
        return open(p, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""


def frontmatter_of(text):
    m = FM_RE.match(text)
    return m.group(0) if m else ""


def body_of(text):
    return FM_RE.sub("", text)


def item_set(text):
    return {l.strip() for l in body_of(text).splitlines() if ITEM_RE.match(l)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="perform the merge and deletion")
    args = ap.parse_args()
    os.chdir(VAULT)

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip() or "unknown"

    files = os.listdir(TRENDS)
    local = {}
    for f in files:
        m = LOCAL_NAME.match(f)
        if m and "source: notion" not in frontmatter_of(read(os.path.join(TRENDS, f))):
            local[(m.group(1), m.group(2).replace("-", ""))] = f

    pairs, sole = [], []
    for f in sorted(files):
        m = NOTION_NAME.match(f)
        if not m:
            continue
        slug = FAMILY_MAP.get(m.group(1))
        counterpart = local.get((slug, m.group(2))) if slug else None
        if counterpart:
            pairs.append((f, counterpart))
        else:
            sole.append(f)

    print(f"Trend reconciliation [{'apply' if args.apply else 'dry-run'}]  undo point git {head}")
    print(f"  pairs to merge:              {len(pairs)}")
    print(f"  notion-only (no local file): {len(sole)}  — left untouched")

    results, gained_total, deleted = [], 0, 0
    for nf, lf in pairs:
        np_, lp = os.path.join(TRENDS, nf), os.path.join(TRENDS, lf)
        ntext, ltext = read(np_), read(lp)
        n_items, l_items = item_set(ntext), item_set(ltext)
        gain = len(n_items - l_items)
        loss_if_naive_delete = gain
        merged_body = merge_bodies(body_of(ltext).strip(), body_of(ntext).strip())
        rec = {"notion": nf, "local": lf, "notion_items": len(n_items),
               "local_items": len(l_items), "unique_to_notion": gain,
               "merged_items": len(ITEM_RE.findall(merged_body))}

        if args.apply:
            with open(lp, "w", encoding="utf-8") as fh:
                fh.write(frontmatter_of(ltext) + merged_body)
            # Verify containment on the file as it now stands, before removing anything.
            if item_set(read(lp)) >= n_items:
                os.remove(np_)
                rec["result"] = f"merged (+{gain}) then deleted notion copy"
                deleted += 1
            else:
                rec["result"] = "MERGE INCOMPLETE — notion copy kept"
        else:
            rec["result"] = (f"would merge +{gain} item(s), then delete"
                             if gain else "would delete (already contained)")
        gained_total += loss_if_naive_delete
        results.append(rec)

    print(f"  items recovered by merging:  {gained_total}"
          f"  <- would have been LOST by a naive delete")
    if args.apply:
        print(f"  notion copies removed:       {deleted}/{len(pairs)}")
        incomplete = [r for r in results if "INCOMPLETE" in r["result"]]
        for r in incomplete:
            print(f"    ! {r['notion']}")
    else:
        worst = sorted(results, key=lambda r: -r["unique_to_notion"])[:8]
        for r in worst:
            print(f"    {r['notion'][:50]:<50} +{r['unique_to_notion']:3} "
                  f"({r['local_items']}->{r['merged_items']})")
        print("\n  re-run with --apply")

    if args.apply:
        json.dump({"when": datetime.datetime.now().isoformat(),
                   "git_head_before": head, "pairs": results},
                  open(MANIFEST, "w"), indent=1)
        with open(AUDIT, "a") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [merge+apply] "
                     f"pairs={len(pairs)} merged_items_recovered={gained_total} "
                     f"deleted={deleted} head_before={head}\n")
        print(f"  manifest: {MANIFEST}")
        print(f"  undo: git -C '{VAULT}' checkout {head} -- '{TRENDS}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
