#!/usr/bin/env python3
"""
handoff_embed_lint.py — scan the vault for type: handoff notes and report
which ones are unembedded (no embedding_status in frontmatter).

Purpose: a lightweight, read-only lint rule that nags when a handoff is
written but never embedded. It is NOT a commit hook and does NOT auto-embed
— embedding is ratified optional augmentation; the linter just makes the
drift visible so it can be fixed on the next pass.

Intended call sites:
  - vault-schema-lint.py (fold in as a new findings bucket)
  - a standalone scan: python3 handoff_embed_lint.py
  - a cron health digest that reports "N handoffs unembedded" like the
    schema-lint health JSON does for other drift.

Does NOT:
  - emit embedding_status on the note (that is vault_embed.py's job)
  - call ilmu (no network)
  - modify the vault
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

VAULT = Path("/Users/admin/Documents/Obsidian Vault")

FM_BLOCK = re.compile(r"^---\n(.*?)\n---\n?", re.M | re.S)
KEY_VAL = re.compile(r"^([A-Za-z0-9_]+):\s*(.*)$", re.M)

# handoffs/ is the canonical handoff location; type inference in
# vault-schema-migrate.py maps it to type: handoff, and the schema linter
# accepts it as a valid type. We also accept any note that explicitly
# declares type: handoff regardless of location (defensive).
HANDBOFF_DIR = VAULT / "handoffs"

EMBEDDING_STATUS_KEY = "embedding_status"
EMBEDDING_MODEL_KEY = "embedding_model"
EMBEDDING_REF_KEY = "embedding_ref"


def parse_fm(text: str) -> dict[str, str] | None:
    m = FM_BLOCK.match(text)
    if not m:
        return None
    fm: dict[str, str] = {}
    for km in KEY_VAL.finditer(m.group(1)):
        k = km.group(1).strip()
        v = km.group(2).strip()
        if v.startswith("#"):
            v = ""
        fm[k] = v
    return fm


def is_handoff(rel: str, fm: dict[str, str] | None) -> bool:
    if fm and fm.get("type", "").strip().strip("'\"").lower() == "handoff":
        return True
    # location-based: anything directly under handoffs/ is treated as a handoff
    # even if it has no frontmatter (schema-migrate would type it on next run).
    try:
        return Path(rel).parent == HANDBOFF_DIR
    except ValueError:
        return False


def scan() -> dict:
    findings: list[str] = []
    handoffs_total = 0
    embedded = 0
    unembedded = 0
    no_fm = 0
    status_missing = 0
    status_known = Counter()  # embedding_status value -> count

    for p in sorted(HANDBOFF_DIR.rglob("*.md")):
        rel = str(p.relative_to(VAULT))
        handoffs_total += 1
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            findings.append(f"{rel} :: unreadable")
            continue

        fm = parse_fm(text)
        if fm is None:
            no_fm += 1
            findings.append(f"{rel} :: no_frontmatter (unembedded)")
            continue

        status = fm.get(EMBEDDING_STATUS_KEY, "").strip().strip("'\"").lower()
        if status:
            status_known[status] += 1
            if status == "embedded":
                embedded += 1
            else:
                unembedded += 1
                findings.append(
                    f"{rel} :: embedding_status={fm.get(EMBEDDING_STATUS_KEY)!r} "
                    f"(model={fm.get(EMBEDDING_MODEL_KEY)!r})"
                )
        else:
            status_missing += 1
            findings.append(f"{rel} :: no embedding_status (unembedded)")

    # Also catch any type: handoff notes NOT under handoffs/ (defensive).
    for p in sorted(VAULT.rglob("*.md")):
        rel = str(p.relative_to(VAULT))
        if rel.startswith("handoffs/"):
            continue  # already counted above
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        fm = parse_fm(text)
        if not fm or fm.get("type", "").strip().strip("'\"").lower() != "handoff":
            continue
        # It's a handoff by explicit type. Count it.
        handoffs_total += 1
        status = fm.get(EMBEDDING_STATUS_KEY, "").strip().strip("'\"").lower()
        if status:
            status_known[status] += 1
            if status != "embedded":
                unembedded += 1
                findings.append(
                    f"{rel} :: type=handoff, embedding_status={fm.get(EMBEDDING_STATUS_KEY)!r}"
                )
        else:
            unembedded += 1
            findings.append(f"{rel} :: type=handoff, no embedding_status (unembedded)")

    summary = {
        "handoffs_total": handoffs_total,
        "embedded": embedded,
        "unembedded": unembedded,
        "no_frontmatter": no_fm,
        "status_missing": status_missing,
        "status_values": dict(status_known),
        "unembedded_pct": round(100 * unembedded / handoffs_total, 1) if handoffs_total else 0,
    }
    return {"summary": summary, "findings": findings}


def main() -> int:
    result = scan()
    s = result["summary"]
    print(f"handoffs: {s['handoffs_total']} total, "
          f"{s['embedded']} embedded, {s['unembedded']} unembedded "
          f"({s['unembedded_pct']}%)")
    if s["no_frontmatter"]:
        print(f"  no_frontmatter: {s['no_frontmatter']}")
    if s["status_missing"]:
        print(f"  status_missing: {s['status_missing']}")
    if s["status_values"]:
        print(f"  status_values: {s['status_values']}")
    if result["findings"]:
        print()
        print("unembedded handoffs:")
        for f in result["findings"][:40]:
            print(f"  {f}")
        if len(result["findings"]) > 40:
            print(f"  ... and {len(result['findings']) - 40} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
