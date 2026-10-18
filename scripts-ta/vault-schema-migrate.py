#!/usr/bin/env python3
"""Migrate vault frontmatter to Schema v2.

Dry-run by default. Nothing is written without --apply.

    python3 vault-schema-migrate.py                 # dry run, all phases
    python3 vault-schema-migrate.py --phases 1,2    # dry run, selected phases
    python3 vault-schema-migrate.py --apply         # write changes

Phases
  1  Normalise `status` to the canonical six; evict CRM values out of `stage`
     into a new `pipeline` field (one meaning per field).
  2  Backfill `created` from Notion `synced` / `date` provenance. Files with no
     reliable date source are LEFT BLANK — an invented timestamp is worse than a
     missing one, and the linter will keep reporting them.
  3  Infer `type` and `domain` from path, and add minimal frontmatter to files
     that have none. Existing values are never overwritten.
  4  Tag hygiene: merge case and plural variants, then drop tags that merely
     restate a structured field (dropping `raw` only when `stage: raw` is
     actually set, so no information is lost — it moved into a queryable field).

Edits are line-based on the frontmatter block so both inline (`tags: [a, b]`)
and block-style (`tags:\\n  - a`) YAML survive untouched.
"""
import argparse
import re
import sys
from collections import Counter
from pathlib import Path

VAULT = Path("/Users/admin/Documents/Obsidian Vault")

EXCLUDE_DIRS = {".obsidian", ".trash", ".git", ".smart-env", "node_modules",
                ".obliviarch", "System/Backups", "System/_generated",
    # Mirrored from the vault-preflight git repo every 6h by
    # vault-preflight-lockstep.sh — any frontmatter added here is
    # overwritten on the next sync, and these are repo docs, not notes.
    "System/Assistant/skills",
}
# Scaffolds hold template bodies whose frontmatter is the *output* of the
# template; generated indexes get rewritten by their generator on the next run.
NO_INJECT = ("Knowledge/_Scaffolds/", "Knowledge/40-Templates/")
SKIP_FILES = {"Knowledge/00-Raw/trends/README.md"}

# ── Phase 1 vocabularies ──────────────────────────────────────────────
STATUS_CANON = {"active", "draft", "published", "shipped", "archived", "unprocessed"}
STATUS_ALIASES = {
    "living": "active", "🟢 active": "active", "🟡 draft": "draft",
    "🔴 urgent": "active", "complete": "shipped", "completed": "shipped",
    "captured": "unprocessed", "in-progress": "active", "done": "shipped",
    "superseded": "archived", "in progress": "active", "wip": "active",
    "new": "unprocessed", "final": "published",
    "processed": "shipped", "filed": "archived", "draft for review": "draft",
    "🟡 under review": "draft", "draft for action": "draft",
    "action plan — ready for outreach": "active", "under review": "draft",
}
PIPELINE_VALUES = {
    "initial contact", "proposal", "kiv", "closed lost", "closed won",
    "negotiation", "qualified", "qualification", "rfp",
}
# Wrong `type` spellings seen in the wild -> canonical vocabulary.
TYPE_ALIASES = {
    "handover": "handoff", "meeting": "meeting-notes", "notes": "reference",
    "wiki-article": "wiki", "best-practice": "distilled", "sop": "workflow",
    "project": "project-note", "cycle-report": "report", "skill-copy": "reference",
    # System/Assistant/ grew a private type per file (system-context,
    # system-preflight, …). The filename already says which one it is; the type
    # field only needs to say what kind of thing it is.
    "system-preflight": "system", "system-context": "system",
    "system-environment": "system", "system-preferences": "system",
    "system-log": "log",
}

# ── Phase 3 inference ─────────────────────────────────────────────────
# Longest prefix wins, so order does not matter.
TYPE_BY_PREFIX = {
    "System/Wiki/concepts/": "concept",
    "System/Wiki/entities/": "entity",
    "System/Wiki/comparisons/": "comparison",
    "System/Wiki/queries/": "query",
    "System/Wiki/October-Archive/": "reference",
    "System/Wiki/raw/": "raw",
    "System/Assistant/logs/": "log",
    "System/Assistant/": "system",
    "System/Wiki/": "reference",
    "Knowledge/00-Raw/": "raw",
    "Knowledge/10-Wikis/": "wiki",
    "Knowledge/20-Reports/": "report",
    "Knowledge/30-Distilled/": "distilled",
    "Knowledge/40-Templates/": "template",
    "Knowledge/_Scaffolds/": "template",
    "Daily/": "daily",
    "handoffs/": "handoff",
    "Personal/Projects/": "project-note",
    "Work/MUI/Analysis/": "analysis",
    "Work/MUI/Deep-Dives/": "analysis",
    "Work/MUI/Investment-Research/": "analysis",
    # Catch-alls, so no note is left untyped. Longest prefix wins, so the
    # specific rules above still take precedence.
    "Work/": "reference",
    "Personal/": "reference",
    "System/": "system",
}
DOMAIN_BY_PREFIX = {
    "Work/AISED/": "aised",
    "Work/MUI/": "mui",
    "Work/Business/Property-Infrastructure/": "property",
    "Work/Business/China-Cross-Border/": "china",
    "Work/Business/BIG-2.0/": "big",
    "Work/Business/BSG/": "bsg",
    "Work/Business/Food-Confectionery/": "fnb",
    "Work/Business/Sidec/": "sidec",
    "Work/Business/Family-Office/": "family-office",
    "Work/EOI/": "eoi",
    "Work/": "work",
    "Personal/": "personal",
    "Knowledge/": "knowledge",
    "System/": "system",
    "handoffs/": "system",
    # Daily/ is deliberately absent: a daily note spans every domain, so
    # stamping one on it would be noise. `type: daily` already identifies it.
}

# `status` is only meaningful where something actually queries it — the flywheel
# and Knowledge.base read it under these roots. Stamping `status: active` on all
# 865 remaining files would produce a universally-present, universally-identical
# field: compliant on paper, useless as a filter.
STATUS_SCOPE = ("Knowledge/", "System/Wiki/")

# ── Phase 4 tag maps ──────────────────────────────────────────────────
TAG_MERGES = {
    "aised": "aised", "AiSED": "aised", "AISED": "aised",
    "MUI": "mui", "AI": "ai", "Kosovo": "kosovo", "UNESCO": "unesco",
    "projects": "project", "handoffs": "handoff", "workflows": "workflow",
    "agents": "agent", "repos": "repo", "partnerships": "partnership",
    "playbooks": "playbook",
    # Authorship: fix the misspelling only. The generic legacy `hermes-agent`
    # is deliberately NOT remapped — those notes predate the host split and
    # which machine wrote them is unknowable, so assigning one would be
    # fabricated provenance. Going forward `authorship_tag()` resolves it.
    "Mackbook-pro-hermes-agent": "macbook-pro-hermes-agent",
    "MacBook-pro-hermes-agent": "macbook-pro-hermes-agent",
}
# tag -> (field, value it restates). Dropped ONLY when that field holds that value.
TAG_REDUNDANT = {
    "raw": ("stage", "raw"), "wiki": ("stage", "wiki"),
    "report": ("stage", "report"), "distilled": ("stage", "distilled"),
    "template": ("stage", "template"),
    "daily": ("type", "daily"), "moc": ("type", "moc"),
    "index": ("type", "index"), "entity": ("type", "entity"),
    "concept": ("type", "concept"), "handoff": ("type", "handoff"),
    "aised": ("domain", "aised"), "mui": ("domain", "mui"),
}

DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def excluded(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p.startswith(".") for p in parts):
        return True
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in EXCLUDE_DIRS:
            return True
    return parts[0] in EXCLUDE_DIRS


def split_fm(text):
    """Return (fm_lines, body, had_fm)."""
    m = re.match(r"^---\n(.*?)\n---\n?", text, re.S)
    if not m:
        return [], text, False
    return m.group(1).split("\n"), text[m.end():], True


def join_fm(fm_lines, body):
    return "---\n" + "\n".join(fm_lines) + "\n---\n" + body


def get_key(fm_lines, key):
    for line in fm_lines:
        m = re.match(rf"^{re.escape(key)}:(.*)$", line)
        if m:
            return m.group(1).strip().strip("\"'")
    return None


def set_key(fm_lines, key, value):
    for i, line in enumerate(fm_lines):
        if re.match(rf"^{re.escape(key)}:", line):
            fm_lines[i] = f"{key}: {value}"
            return fm_lines
    fm_lines.append(f"{key}: {value}")
    return fm_lines


def remove_key(fm_lines, key):
    """Remove a scalar key and any block-list continuation lines under it."""
    out, skipping = [], False
    for line in fm_lines:
        if re.match(rf"^{re.escape(key)}:", line):
            skipping = True
            continue
        if skipping:
            if re.match(r"^\s+-\s+", line) or not line.strip():
                continue
            skipping = False
        out.append(line)
    return out


def read_tags(fm_lines):
    """Return (tags, style) where style is 'inline' | 'block' | None."""
    text = "\n".join(fm_lines)
    m = re.search(r"^tags:\s*\[(.*?)\]", text, re.M | re.S)
    if m:
        return [x.strip().strip("\"'") for x in m.group(1).split(",") if x.strip()], "inline"
    m = re.search(r"^tags:\s*\n((?:[ \t]+-[ \t]+.*\n?)+)", text, re.M)
    if m:
        return [l.strip().lstrip("-").strip().strip("\"'")
                for l in m.group(1).splitlines() if l.strip()], "block"
    return [], None


def write_tags(fm_lines, tags, style):
    fm_lines = remove_key(fm_lines, "tags")
    if not tags:
        return fm_lines
    if style == "block":
        fm_lines.append("tags:")
        fm_lines.extend(f"  - {t}" for t in tags)
    else:
        fm_lines.append(f"tags: [{', '.join(tags)}]")
    return fm_lines


def longest_prefix(rel, table):
    best, val = "", None
    for pre, v in table.items():
        if rel.startswith(pre) and len(pre) > len(best):
            best, val = pre, v
    return val


def migrate(rel, text, phases, stats):
    fm_lines, body, had_fm = split_fm(text)
    changed = []

    if not had_fm:
        if 3 not in phases or any(rel.startswith(p) for p in NO_INJECT):
            return text, []
        fm_lines = []

    # ── Phase 1 ──
    if 1 in phases:
        st = get_key(fm_lines, "status")
        if st and st.lower() not in STATUS_CANON:
            canon = STATUS_ALIASES.get(st.lower())
            if canon:
                fm_lines = set_key(fm_lines, "status", canon)
                changed.append(f"status {st!r}->{canon!r}")
                stats["status_normalised"] += 1

        ty = get_key(fm_lines, "type")
        if ty and ty.lower() in TYPE_ALIASES:
            canon = TYPE_ALIASES[ty.lower()]
            fm_lines = set_key(fm_lines, "type", canon)
            changed.append(f"type {ty!r}->{canon!r}")
            stats["type_normalised"] += 1

        stage = get_key(fm_lines, "stage")
        if stage and stage.lower() in PIPELINE_VALUES:
            fm_lines = remove_key(fm_lines, "stage")
            fm_lines = set_key(fm_lines, "pipeline", stage)
            changed.append(f"stage {stage!r}->pipeline")
            stats["crm_evicted"] += 1

    # ── Phase 2 ──
    if 2 in phases and not get_key(fm_lines, "created"):
        for src in ("synced", "date", "updated"):
            v = get_key(fm_lines, src) or ""
            m = DATE_RE.search(v)
            if m:
                fm_lines = set_key(fm_lines, "created", m.group(1))
                changed.append(f"created<-{src}")
                stats["created_backfilled"] += 1
                break
        else:
            stats["created_unknown"] += 1

    # ── Phase 3 ──
    # Templates are skipped entirely: their frontmatter describes the note they
    # PRODUCE, not themselves. Injecting `domain` into the Daily Note scaffold
    # fought its deliberate omission (daily notes are domain-exempt) and left the
    # migration non-idempotent — every run re-added what the design removed.
    if 3 in phases and not any(rel.startswith(p) for p in NO_INJECT):
        if not get_key(fm_lines, "type"):
            t = longest_prefix(rel, TYPE_BY_PREFIX)
            if Path(rel).name.startswith("_index") or Path(rel).name == "README.md":
                t = "index"
            elif "MOC" in Path(rel).name:
                t = "moc"
            if t:
                fm_lines = set_key(fm_lines, "type", t)
                changed.append(f"type={t}")
                stats["type_added"] += 1
        if not get_key(fm_lines, "domain"):
            d = longest_prefix(rel, DOMAIN_BY_PREFIX)
            if d:
                fm_lines = set_key(fm_lines, "domain", d)
                changed.append(f"domain={d}")
                stats["domain_added"] += 1
        if not get_key(fm_lines, "status") and rel.startswith(STATUS_SCOPE):
            inferred = "unprocessed" if get_key(fm_lines, "type") == "raw" else "active"
            fm_lines = set_key(fm_lines, "status", inferred)
            changed.append(f"status={inferred}")
            stats["status_added"] += 1
        if not had_fm:
            stats["frontmatter_injected"] += 1

    # ── Phase 7 ──
    # Retire `stage`. Lifecycle position is a pure function of the folder — all
    # 103 notes carrying a `stage` agreed with what their folder already implied
    # (the 15 apparent mismatches were `evergreen` vs `wiki`, two names for the
    # same position). A stored copy of the path carries no information and only
    # creates something to drift; `type` keeps the one axis the path cannot
    # express, namely what KIND of document this is.
    if 7 in phases:
        if get_key(fm_lines, "stage") is not None:
            fm_lines = remove_key(fm_lines, "stage")
            changed.append("stage retired (derived from folder)")
            stats["stage_retired"] += 1

    # ── Phase 6 ──
    # Undo the Phase 2 date fabrication. Phase 2 backfilled `created` from the
    # Notion `synced` field, but `synced` records when the EXPORT ran, not when
    # the note was written — so 663 notes ended up claiming a creation date they
    # never had, 625 of them the same day. That is worse than a blank field: a
    # missing value advertises its own ignorance, a wrong one does not, and
    # `scan_raw_items()` computes note age from `created`, so the "raw items >30
    # days" check was being fed synthetic ages that would all flip overdue on the
    # same day.
    #
    # `synced` already holds the import timestamp, so `created` is simply
    # removed rather than renamed — adding an `imported` field would duplicate
    # `synced` for no gain. Dates that came from a real `date:` field are left
    # alone; those are genuine.
    if 6 in phases:
        created = get_key(fm_lines, "created")
        synced = get_key(fm_lines, "synced") or ""
        sd = DATE_RE.search(synced)
        if created and sd and created.strip() == sd.group(1):
            fm_lines = remove_key(fm_lines, "created")
            changed.append("created (export date) removed")
            stats["fabricated_dates_removed"] += 1

    # ── Phase 5 ──
    # Quote scalar values containing ": ", which makes YAML unparseable
    # ("mapping values are not allowed here"). Obsidian Properties, Bases and
    # Dataview all silently ignore a note whose frontmatter will not parse, so
    # these notes were invisible to every structured query regardless of how
    # good their fields were. Pre-existing: 132 such files before this migration.
    # A URL like https://x is safe — YAML only breaks on colon-SPACE.
    if 5 in phases:
        for i, line in enumerate(fm_lines):
            m = re.match(r"^([A-Za-z_][\w-]*):\s+(.*)$", line)
            if not m:
                continue
            key, val = m.group(1), m.group(2).rstrip()
            # Colon-space breaks YAML mid-value; a trailing colon breaks it at
            # the end of the line ("title: Top 5 things I got right in 2024:").
            if not val or (": " not in val and not val.endswith(":")):
                continue
            if val[0] in "\"'[{|>" or val.endswith(("|", ">")):
                continue
            escaped = val.replace('"', '\\"')
            fm_lines[i] = f'{key}: "{escaped}"'
            changed.append(f"quoted {key}")
            stats["values_quoted"] += 1

    # ── Phase 4 ──
    if 4 in phases:
        tags, style = read_tags(fm_lines)
        if tags:
            merged, seen = [], set()
            for i, t in enumerate(tags):
                t2 = TAG_MERGES.get(t, t)
                if t2 != t:
                    stats["tags_merged"] += 1
                # Never let redundancy-pruning empty the tag list: `tags` is a
                # required field, and a daily note tagged only `daily` would
                # otherwise end up with none at all.
                last_chance = not merged and i == len(tags) - 1
                red = TAG_REDUNDANT.get(t2)
                if red and not last_chance and \
                        (get_key(fm_lines, red[0]) or "").lower() == red[1]:
                    stats["tags_dropped_redundant"] += 1
                    continue
                if t2.lower() in seen:
                    stats["tags_deduped"] += 1
                    continue
                seen.add(t2.lower())
                merged.append(t2)
            if merged != tags:
                fm_lines = write_tags(fm_lines, merged, style)
                changed.append(f"tags {len(tags)}->{len(merged)}")

    if not changed:
        return text, []
    return join_fm(fm_lines, body), changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--phases", default="1,3,4,5,6")  # 2 is the date backfill, superseded by 6
    ap.add_argument("--show", type=int, default=15)
    args = ap.parse_args()
    phases = {int(x) for x in args.phases.split(",") if x.strip()}

    stats = Counter()
    edited, samples = 0, []

    for p in sorted(VAULT.rglob("*.md")):
        rel = str(p.relative_to(VAULT))
        if excluded(rel) or rel in SKIP_FILES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new, changed = migrate(rel, text, phases, stats)
        if changed:
            edited += 1
            if len(samples) < args.show:
                samples.append(f"  {rel}\n      {'; '.join(changed)}")
            if args.apply:
                p.write_text(new, encoding="utf-8")

    mode = "APPLIED" if args.apply else "DRY RUN (no files written)"
    print(f"=== Schema v2 migration — {mode} ===")
    print(f"phases: {sorted(phases)}")
    print(f"files changed: {edited}")
    print()
    for k in ("status_normalised", "type_normalised", "crm_evicted", "created_backfilled",
              "created_unknown", "type_added", "domain_added", "status_added",
              "frontmatter_injected", "tags_merged", "tags_dropped_redundant",
              "tags_deduped", "values_quoted", "fabricated_dates_removed",
              "stage_retired"):
        print(f"  {k:26s} {stats[k]}")
    print()
    print("sample changes:")
    print("\n".join(samples))


if __name__ == "__main__":
    main()
