#!/usr/bin/env python3
"""File substantive Inbox/Notion captures into their proper vault homes.

Runs AFTER vault-notion-dedupe.py. Only touches files that have no body-identical
copy elsewhere, so nothing here is a re-import.

Frontmatter: adds the Schema v2 keys a note needs to be queryable (`type`, `domain`,
`status`) and preserves the Notion provenance (`source`, `notion_id`, `synced`).

It deliberately does NOT set `created`. Notion's `synced` is when the EXPORT ran, not
when the note was authored — a backfill on 2026-07-26 derived `created` from it and
gave 663 notes a fabricated date that looked precise and was wrong. The linter has an
"undateable imports" bucket for exactly this case; leaving the key absent is correct.

    vault-notion-file.py                     # dry-run
    vault-notion-file.py --apply
    vault-notion-file.py --only trends,prompts

Destination collisions are never overwritten: identical body -> delete the inbox copy,
different body -> leave it and report an ANOMALY for review.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

VAULT = "/Users/admin/Documents/Obsidian Vault"
INBOX = "Inbox/Notion"
AUDIT = os.path.expanduser("~/.hermes/logs/notion-file.log")
MIN_BODY = 200          # below this a note is a stub, not content worth filing
FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)

# (bucket, destination, filename pattern, frontmatter type/domain)
RULES = [
    ("trends",  "Knowledge/00-Raw/trends",
     r"(Trends-—|Market-Trends|Market-Intelligence|CSuite-Leadership|FB-Consumer|"
     r"Hospitality-Healthcare|Property-Infrastructure|AI-Science-Investment|Insights-—)",
     ("raw", "knowledge")),
    ("prompts", "Knowledge/00-Raw/prompt-library",
     r"^(Generating|Creating|Writing|Optimizing|Analyzing|Developing|Crafting|"
     r"Researching|Implementing|Monitoring|Produce|Brainstorm|Engaging)",
     ("raw", "knowledge")),
    ("october", "Knowledge/00-Raw/october-archive",
     r"^(October|Swarm|Obliviarch|Moltbook)", ("raw", "knowledge")),
    ("hermes",  "System/Assistant/notion-snapshots",
     r"^\d+-(Config|Memories|Skills|Cron|Plugins|Providers|Tools)", ("reference", "system")),
]


def read(path):
    try:
        return open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return None


def split_fm(text):
    m = FM_RE.match(text)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        if k.strip():
            fm[k.strip()] = v.strip()
    return fm, text[m.end():]


def body_key(text):
    _, body = split_fm(text)
    b = re.sub(r"\s+", " ", body).strip().lower()
    return hashlib.sha256(b.encode()).hexdigest() if len(b) >= MIN_BODY else None


def rebuild_fm(fm, typ, domain):
    """Schema v2 keys first, Notion provenance kept, `created` deliberately omitted."""
    out = ["---"]
    title = fm.get("title", "").strip()
    if title:
        if ":" in title or title.endswith(":"):
            title = '"%s"' % title.replace('"', "'")
        out.append(f"title: {title}")
    out += [f"type: {typ}", f"domain: {domain}", "status: unprocessed"]
    for k in ("source", "notion_id", "synced"):
        if k in fm:
            out.append(f"{k}: {fm[k]}")
    tags = fm.get("tags")
    out.append(f"tags: {tags}" if tags else "tags: [notion-import, claude-code]")
    out.append("---")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default="", help="comma-separated bucket names")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    wanted = {s.strip() for s in args.only.split(",") if s.strip()}

    os.chdir(VAULT)
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip() or "unknown"

    # Everything already filed elsewhere, so we never re-file a duplicate.
    filed = set()
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.endswith(".md"):
                continue
            rel = os.path.relpath(os.path.join(root, f), VAULT)
            if rel.startswith("Inbox/") or rel.startswith("System/Backups"):
                continue
            t = read(os.path.join(root, f))
            if t:
                k = body_key(t)
                if k:
                    filed.add(k)

    plan, stubs, unmatched_rule, dupes = [], [], [], []
    for f in sorted(os.listdir(INBOX)):
        if not f.endswith(".md"):
            continue
        src = os.path.join(INBOX, f)
        text = read(src)
        if text is None:
            continue
        key = body_key(text)
        if key is None:
            stubs.append(f)
            continue
        if key in filed:
            dupes.append(f)
            continue
        for bucket, dest, pat, (typ, dom) in RULES:
            if re.search(pat, f, re.I):
                if wanted and bucket not in wanted:
                    break
                plan.append({"bucket": bucket, "src": src,
                             "dst": os.path.join(dest, f), "type": typ, "domain": dom})
                break
        else:
            unmatched_rule.append(f)

    moved = anomalies = 0
    if args.apply:
        for p in plan:
            dst = p["dst"]
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            text = read(p["src"])
            fm, body = split_fm(text)
            new = rebuild_fm(fm, p["type"], p["domain"]) + body.lstrip("\n")
            if os.path.exists(dst):
                if body_key(read(dst)) == body_key(text):
                    os.remove(p["src"])          # already filed identically
                    p["result"] = "duplicate at destination — inbox copy removed"
                else:
                    p["result"] = "ANOMALY destination differs — left in inbox"
                    anomalies += 1
                continue
            with open(dst, "w", encoding="utf-8") as fh:
                fh.write(new)
            os.remove(p["src"])
            p["result"] = "filed"
            moved += 1

    by_bucket = {}
    for p in plan:
        by_bucket[p["bucket"]] = by_bucket.get(p["bucket"], 0) + 1

    report = {"mode": "apply" if args.apply else "dry-run", "git_head_before": head,
              "planned": len(plan), "by_bucket": by_bucket, "filed": moved,
              "anomalies": anomalies, "stubs_left": len(stubs),
              "already_filed_elsewhere": len(dupes),
              "no_rule_matched": len(unmatched_rule)}

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Notion filing [{report['mode']}]  undo point git {head}")
        for b, n in by_bucket.items():
            dest = next(d for name, d, _, _ in RULES if name == b)
            print(f"  {b:9} {n:4} -> {dest}")
        print(f"  {'stubs':9} {len(stubs):4}    left in inbox (body <{MIN_BODY} chars)")
        print(f"  {'no rule':9} {len(unmatched_rule):4}    left in inbox, needs a decision")
        if dupes:
            print(f"  {'dupes':9} {len(dupes):4}    body-identical to a filed note (run the dedupe pass)")
        if args.apply:
            print(f"\n  FILED {moved}   ANOMALIES {anomalies}")
            for p in plan:
                if p.get("result", "").startswith("ANOMALY"):
                    print(f"    ! {p['dst']}")
        else:
            print("\n  sample:")
            for p in plan[:8]:
                print(f"    {os.path.basename(p['src'])[:52]}")
                print(f"      -> {p['dst']}")
            print("\n  re-run with --apply")

    if args.apply:
        with open(AUDIT, "a") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [apply] "
                     f"filed={moved} anomalies={anomalies} head_before={head} "
                     f"buckets={by_bucket}\n")
        print(f"  undo: git -C '{VAULT}' checkout {head} -- '{INBOX}' && "
              f"git -C '{VAULT}' clean -fd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
