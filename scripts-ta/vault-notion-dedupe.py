#!/usr/bin/env python3
"""Vault-wide cross-folder dedupe for the Notion landing zone.

Deletes a file from Inbox/Notion/ only when a body-identical copy already exists
somewhere else in the vault. The filed copy is always the survivor.

Why this exists separately from vault-aised-cleanup.py: that engine's cross-folder
dedupe compares only against Work/AISED, so it clears 1 of ~373 duplicates. The
re-imports are spread across Work/, Knowledge/, System/, Personal/ and Daily/.

Why it is durable rather than a treadmill: notion-sync.py decides what to fetch from
its own cache (notion_id -> last_edited_time in Inbox/Notion/.sync-index.json) and
never checks whether the file is on disk. A deleted duplicate is NOT re-downloaded.
It does come back if the source page is later edited in Notion, which is why this is
idempotent and safe to re-run.

    vault-notion-dedupe.py                # dry-run (default)
    vault-notion-dedupe.py --apply
    vault-notion-dedupe.py --apply --json

Audit trail: ~/.hermes/logs/notion-dedupe.log, plus a full deleted->survivor
manifest so any deletion can be traced back.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

VAULT = "/Users/admin/Documents/Obsidian Vault"
INBOX = "Inbox/Notion"
AUDIT_LOG = os.path.expanduser("~/.hermes/logs/notion-dedupe.log")
MANIFEST = os.path.expanduser("~/.hermes/logs/notion-dedupe-manifest.json")

# Bodies shorter than this are too generic to match safely — several Notion stubs
# share boilerplate, and matching them would delete distinct notes.
MIN_BODY_CHARS = 200

# A counterpart in these trees does not count as "already filed": deleting the inbox
# copy would leave only a backup or a machine-generated artefact.
NOT_A_HOME = ("System/Backups", "System/_generated", ".git", ".obsidian", ".obliviarch")

FM_RE = re.compile(r"\A---\n.*?\n---\n", re.S)


def body_hash(path):
    """Hash the body with frontmatter stripped — the Notion export and the filed copy
    carry different frontmatter (sync ids, tags) for identical content."""
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return None
    body = FM_RE.sub("", text)
    body = re.sub(r"\s+", " ", body).strip().lower()
    if len(body) < MIN_BODY_CHARS:
        return None
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def is_valid_home(rel):
    if rel.startswith("Inbox/"):          # one inbox copy cannot justify deleting another
        return False
    return not any(rel.startswith(p) for p in NOT_A_HOME)


def build_pipeline_map():
    """rel-path -> body hash for every candidate survivor outside the inbox."""
    out = {}
    for root, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.endswith(".md"):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, VAULT)
            if not is_valid_home(rel):
                continue
            h = body_hash(full)
            if h:
                # Deterministic winner if the content is filed in several places.
                prev = out.get(h)
                if prev is None or rel < prev:
                    out[h] = rel
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="delete (default: dry-run)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="cap deletions (testing)")
    args = ap.parse_args()

    os.chdir(VAULT)
    inbox_dir = os.path.join(VAULT, INBOX)
    if not os.path.isdir(inbox_dir):
        sys.exit(f"not found: {inbox_dir}")

    # Record the undo point. git is the safety net for these deletions.
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip() or "unknown"
    dirty = bool(subprocess.run(["git", "status", "--porcelain"],
                                capture_output=True, text=True).stdout.strip())

    pipeline = build_pipeline_map()
    dupes, skipped_short, no_match = [], 0, 0

    for f in sorted(os.listdir(inbox_dir)):
        if not f.endswith(".md"):
            continue
        rel = f"{INBOX}/{f}"
        h = body_hash(os.path.join(VAULT, rel))
        if h is None:
            skipped_short += 1
            continue
        survivor = pipeline.get(h)
        if survivor:
            dupes.append({"delete": rel, "survivor": survivor,
                          "bytes": os.path.getsize(os.path.join(VAULT, rel))})
        else:
            no_match += 1

    if args.limit:
        dupes = dupes[:args.limit]

    deleted = []
    if args.apply:
        for d in dupes:
            src = os.path.join(VAULT, d["delete"])
            sur = os.path.join(VAULT, d["survivor"])
            # Re-verify at delete time: the survivor must still exist and still match.
            # A concurrent writer could have changed either file since the scan.
            if not os.path.exists(sur):
                d["result"] = "SKIPPED survivor vanished"
                continue
            if body_hash(src) != body_hash(sur):
                d["result"] = "SKIPPED no longer identical"
                continue
            try:
                os.remove(src)
                d["result"] = "deleted"
                deleted.append(d)
            except OSError as e:
                d["result"] = f"ERROR {e}"

    by_home = {}
    for d in dupes:
        top = d["survivor"].split("/")[0]
        by_home[top] = by_home.get(top, 0) + 1

    report = {
        "mode": "apply" if args.apply else "dry-run",
        "git_head_before": head,
        "tree_was_dirty": dirty,
        "inbox_files_scanned": len(dupes) + no_match + skipped_short,
        "duplicates_found": len(dupes),
        "deleted": len(deleted),
        "kept_no_match": no_match,
        "skipped_body_too_short": skipped_short,
        "bytes_reclaimed": sum(d["bytes"] for d in deleted),
        "survivors_by_tree": by_home,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Notion cross-folder dedupe [{report['mode']}]")
        print(f"  inbox files scanned      {report['inbox_files_scanned']}")
        print(f"  duplicates found         {report['duplicates_found']}")
        print(f"  kept (no match)          {report['kept_no_match']}")
        print(f"  skipped (body <{MIN_BODY_CHARS} chars) {report['skipped_body_too_short']}")
        if args.apply:
            print(f"  DELETED                  {report['deleted']}"
                  f"  ({report['bytes_reclaimed']/1024:.0f} KB)")
            odd = [d for d in dupes if d.get("result", "deleted") != "deleted"]
            for d in odd:
                print(f"    ! {d['result']}: {d['delete']}")
        print(f"  survivors live in        {report['survivors_by_tree']}")
        if not args.apply and dupes:
            print("\n  first 10 that would go:")
            for d in dupes[:10]:
                print(f"    - {d['delete'].split('/')[-1][:56]}")
                print(f"        survivor: {d['survivor']}")
            print(f"\n  undo point: git {head}"
                  f"{'  (tree already dirty — commit first for a clean undo)' if dirty else ''}")
            print("  re-run with --apply to delete")

    if args.apply:
        with open(MANIFEST, "w") as fh:
            json.dump({"when": datetime.datetime.now().isoformat(),
                       "git_head_before": head, "deletions": dupes}, fh, indent=1)
        with open(AUDIT_LOG, "a") as fh:
            fh.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} [apply] "
                     f"deleted={len(deleted)} found={len(dupes)} "
                     f"head_before={head} manifest={MANIFEST}\n")
        print(f"\n  manifest: {MANIFEST}")
        print(f"  undo:     git -C '{VAULT}' checkout {head} -- '{INBOX}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
