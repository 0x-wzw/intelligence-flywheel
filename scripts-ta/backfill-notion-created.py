#!/usr/bin/env python3
"""Backfill a REAL `created:` date into vault notes from the Notion API.

Why this exists: the schema-v2 weekly health check flags `undateable_imports`
(Notion exports with `synced:` but no `created:`). The remediation named in that
report — `backfill-kv-from-notion.py --target created` — does not exist: that
script writes Cloudflare KV, never frontmatter. This is the actual tool.

Doctrine (enforced by vault-schema-migrate.py Phase 6): `synced:` records when
the EXPORT ran, not when the note was written. Copying it into `created:` is
fabrication — "a missing value advertises its own ignorance, a wrong one does
not". So this script only ever writes a date that Notion itself asserts, via a
deterministic ladder, and classifies (never invents) the rest.

Recovery ladder, per file (deterministic, first hit wins):
  1. page   created_time        (the note's own Notion page)
  2. database created_time      (the note IS a database object)
  3. earliest child created_time (walk the database's first page of children)
  4. give up -> report as notion_residue; write NOTHING.

Usage:
  python3 backfill-notion-created.py            # dry run, report only
  python3 backfill-notion-created.py --apply    # write dates
  python3 backfill-notion-created.py --apply --vault /path/to/vault
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

DEFAULT_VAULT = "/Users/admin/Documents/Obsidian Vault"
HEALTH = "System/schema-lint-health.json"
NOTION_VER = "2022-06-28"


def _load_env(path=None):
    path = path or os.path.expanduser("~/.hermes/.env")
    vals = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                line = line[7:].strip() if line.startswith("export ") else line
                if "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return vals


def _token():
    env = _load_env()
    return env.get("NOTION_API_TOKEN") or env.get("NOTION_API_KEY") or ""


def _notion_get(path, token):
    req = urllib.request.Request(
        "https://api.notion.com/v1/" + path,
        headers={"Authorization": "Bearer " + token, "Notion-Version": NOTION_VER},
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read()), None
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except Exception as e:  # noqa: BLE001 - report, never mask as success
        return None, str(e)


def _notion_post(path, token, body):
    req = urllib.request.Request(
        "https://api.notion.com/v1/" + path,
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + token,
                 "Notion-Version": NOTION_VER,
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=20).read()), None
    except urllib.error.HTTPError as e:
        return None, "HTTP %s" % e.code
    except Exception as e:  # noqa: BLE001
        return None, str(e)


def resolve_created(nid, token):
    """Deterministic ladder. Returns (date_str, source) or (None, reason)."""
    page, perr = _notion_get("pages/%s" % nid, token)
    if page and page.get("created_time"):
        return page["created_time"][:10], "page"

    db, derr = _notion_get("databases/%s" % nid, token)
    if db and db.get("created_time"):
        return db["created_time"][:10], "database"

    # Ladder step 3: earliest child page inside the database.
    kids, kerr = _notion_post("databases/%s/query" % nid, token,
                              {"page_size": 10, "sorts": [
                                  {"timestamp": "created_time", "direction": "ascending"}]})
    if kids and kids.get("results"):
        first = kids["results"][0]
        if first.get("created_time"):
            return first["created_time"][:10], "earliest_child"

    reason = derr or perr or kerr or "unresolved"
    return None, reason


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write dates (default: dry run)")
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--health", default=HEALTH, help="lint health json, relative to vault")
    args = ap.parse_args()

    token = _token()
    if not token:
        print("No NOTION_API_TOKEN / NOTION_API_KEY in ~/.hermes/.env")
        return 1

    health_path = os.path.join(args.vault, args.health)
    if not os.path.exists(health_path):
        print("health file not found: %s" % health_path)
        return 1
    targets = json.load(open(health_path)).get("findings", {}).get("undateable_imports", [])
    if not targets:
        print("undateable_imports: 0 — nothing to do")
        return 0

    print("targets: %d%s" % (len(targets), "" if args.apply else "  (dry run)"))
    written = residue = 0
    for rel in targets:
        p = os.path.join(args.vault, rel)
        if not os.path.exists(p):
            print("  MISSING %s" % rel)
            continue
        txt = open(p, encoding="utf-8", errors="ignore").read()
        m = re.search(r'^notion_id:\s*"?([0-9a-fA-F-]{32,36})"?', txt, re.M)
        if not m:
            print("  RESIDUE (no notion_id)  %s" % rel)
            residue += 1
            continue
        date, source = resolve_created(m.group(1), token)
        if not date:
            print("  RESIDUE (%s)  %s" % (source, rel))
            residue += 1
            continue

        end = txt.find("\n---", 3)
        if end < 0:
            print("  SKIP (no frontmatter)  %s" % rel)
            continue
        fm, body = txt[:end], txt[end:]
        if re.search(r"^created:\s", fm, re.M):
            print("  HAS DATE already  %s" % rel)
            continue
        if args.apply:
            if re.search(r"^synced:\s.*$", fm, re.M):
                fm = re.sub(r"^(synced:\s.*)$", r"\1\ncreated: " + date, fm,
                            count=1, flags=re.M)
            else:
                fm = fm + "\ncreated: " + date
            open(p, "w", encoding="utf-8").write(fm + body)
            print("  %s  <-%s  %s" % (date, source, rel))
        else:
            print("  would set %s  <-%s  %s" % (date, source, rel))
        written += 1

    print("\n%s: %d, residue (needs classing as notion_residue): %d"
          % ("written" if args.apply else "would write", written, residue))
    return 0


if __name__ == "__main__":
    sys.exit(main())
