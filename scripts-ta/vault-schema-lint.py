#!/usr/bin/env python3
"""Measure vault frontmatter against the Schema v2 contract.

Read-only. Emits JSON on stdout and writes System/schema-lint-health.json so
flywheel-health-check.py can report drift the same way it reports orphans.

The vault currently carries three competing schemas at once:
  1. the designed lifecycle schema (stage/cycle/status/created/tags)
  2. Notion export residue (title/source/synced/notion_id)
  3. ad-hoc CRM values leaking into the lifecycle `stage` field
This script names which files are in which state so the drift is a number
rather than an impression.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

VAULT = Path("/Users/admin/Documents/Obsidian Vault")
HEALTH_FILE = VAULT / "System" / "schema-lint-health.json"

EXCLUDE_DIRS = {
    ".obsidian", ".trash", ".git", ".smart-env", "node_modules", ".obliviarch",
    "System/Backups", "System/_generated",
    # Mirrored from the vault-preflight git repo every 6h by
    # vault-preflight-lockstep.sh — any frontmatter added here is
    # overwritten on the next sync, and these are repo docs, not notes.
    "System/Assistant/skills",
}

# ── Schema v2 ─────────────────────────────────────────────────────────
# Required everywhere. `updated` is deliberately NOT required: the filesystem
# already tracks modification time, and backfilling it would mean inventing one.
REQUIRED = ("type", "created")

# `domain` is required except on daily notes, which span every domain — a domain
# stamp there would be noise, and `type: daily` already identifies them.
DOMAIN_EXEMPT_PREFIXES = ("Daily/",)

# `status` is only required where something actually queries it (the flywheel and
# Knowledge.base). Requiring it vault-wide would produce a field that is present
# on every note and identical on almost all of them — compliant, but useless.
STATUS_SCOPE = ("Knowledge/", "System/Wiki/")

TYPE_VOCAB = {
    "entity", "concept", "comparison", "query", "moc", "index", "report",
    "raw", "distilled", "template", "daily", "meeting-notes", "handoff",
    "system", "reference", "guide", "plan", "analysis", "log", "database",
    "project-note", "project-review", "workflow", "wiki",
}

# Canonical status values. Anything else is drift — including case and emoji
# variants, which silently break every exact-match Base/Dataview filter.
STATUS_VOCAB = {
    "active", "draft", "published", "shipped", "archived", "unprocessed",
}
STATUS_ALIASES = {
    "living": "active", "🟢 active": "active", "🟡 draft": "draft",
    "🔴 urgent": "active", "complete": "shipped", "completed": "shipped",
    "captured": "unprocessed", "in-progress": "active", "done": "shipped",
}

# `stage` is the knowledge-lifecycle axis ONLY.
STAGE_VOCAB = {
    "raw", "wiki", "report", "distilled", "template", "evergreen", "index", "moc",
}
# CRM pipeline values found squatting in `stage`. They belong in `pipeline`.
PIPELINE_VALUES = {
    "initial contact", "proposal", "kiv", "closed lost", "closed won",
    "negotiation", "qualified",
}

NOTION_KEYS = {"notion_id", "synced", "notion_url", "properties_source",
               "properties_synced"}

# ── Derived axes ──────────────────────────────────────────────────────
# Lifecycle position is NOT stored. It is a pure function of the folder: a
# measurement on 2026-07-26 found all 103 notes carrying a `stage` matched what
# their folder already implied (the 15 apparent mismatches were `evergreen` vs
# `wiki`, two names for the same position). Storing it duplicated the path and
# invited the two to drift apart, so `stage` was retired and lifecycle is
# derived here instead. `type` answers the one thing the path cannot: what KIND
# of document this is.
LIFECYCLE_BY_PREFIX = {
    "Knowledge/00-Raw/": "raw",
    "Knowledge/20-Reports/": "report",
    "Knowledge/30-Distilled/": "distilled",
    "Knowledge/40-Templates/": "template",
    "Knowledge/_Scaffolds/": "template",
    "System/Wiki/": "wiki",
}
DOMAIN_BY_PREFIX = {
    "Work/AISED/": "aised", "Work/MUI/": "mui",
    "Work/Business/Property-Infrastructure/": "property",
    "Work/Business/China-Cross-Border/": "china",
    "Work/Business/BIG-2.0/": "big", "Work/Business/BSG/": "bsg",
    "Work/Business/Food-Confectionery/": "fnb", "Work/Business/Sidec/": "sidec",
    "Work/Business/Family-Office/": "family-office", "Work/EOI/": "eoi",
    "Work/": "work", "Personal/": "personal", "Knowledge/": "knowledge",
    "System/": "system", "handoffs/": "system",
}
# `type` values that imply a location. Anything else is location-independent.
TYPE_REQUIRES_PREFIX = {
    "daily": ("Daily/",),
    "raw": ("Knowledge/00-Raw/", "System/Wiki/raw/"),
    "report": ("Knowledge/20-Reports/",),
    "distilled": ("Knowledge/30-Distilled/",),
    "template": ("Knowledge/40-Templates/", "Knowledge/_Scaffolds/"),
    "concept": ("System/Wiki/",),
    "entity": ("System/Wiki/",),
    "comparison": ("System/Wiki/",),
    "query": ("System/Wiki/",),
}


def _longest_prefix(rel, table):
    best, val = "", None
    for pre, v in table.items():
        if rel.startswith(pre) and len(pre) > len(best):
            best, val = pre, v
    return val


def derive_lifecycle(rel):
    """Lifecycle position from the folder, or None if outside the pipeline."""
    return _longest_prefix(rel, LIFECYCLE_BY_PREFIX)


def derive_domain(rel):
    """Domain implied by the folder."""
    return _longest_prefix(rel, DOMAIN_BY_PREFIX)

TEMPLATE_PREFIXES = ("Knowledge/_Scaffolds/", "Knowledge/40-Templates/")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def parse_fm(text):
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return None
    fm = {}
    for line in m.group(1).split("\n"):
        mm = re.match(r"^([A-Za-z_][\w-]*):(.*)$", line)
        if mm:
            fm[mm.group(1).strip()] = mm.group(2).strip()
    return fm


def excluded(parts):
    if any(p.startswith(".") for p in parts):
        return True
    for i in range(1, len(parts)):
        if "/".join(parts[:i]) in EXCLUDE_DIRS:
            return True
    return parts[0] in EXCLUDE_DIRS


# Notion exports come in two object classes with different recovery paths:
#   - a dated PAGE  -> has a real `created_at`; recoverable via the Notion API.
#   - a DATABASE / workspace snapshot -> no single creation date of its own;
#     recovery needs a parent-page walk. These are a bounded, known queue, so
#     they must not share the CRITICAL channel with genuinely-lost pages.
# Shape signals: explicit `type: database`, the `_all` export suffix, a bare
# Notion UUID filename, or the synthetic `database<hash>` name.
_NOTION_UUID_RE = re.compile(r"[0-9a-f]{32}")
_NOTION_DB_SUFFIX_RE = re.compile(r"(_all|^database[0-9a-f]{6,})", re.I)


def _is_notion_database_export(rel, fm):
    """True when a synced Notion export is a DATABASE/workspace object rather
    than a dated page — i.e. the undateable state is structural, not a loss."""
    t = fm.get("type", "").strip().strip("\"'").lower()
    if t == "database":
        return True
    name = Path(rel).stem
    if name.endswith("_all"):
        return True
    if _NOTION_DB_SUFFIX_RE.search(name):
        return True
    # A filename that is nothing but a 32-hex Notion id.
    if _NOTION_UUID_RE.fullmatch(name):
        return True
    return False


def main():
    findings = defaultdict(list)
    total = 0
    compliant = 0
    notion_only = 0
    type_vals, status_vals, stage_vals = Counter(), Counter(), Counter()
    tag_vocab = Counter()

    for p in sorted(VAULT.rglob("*.md")):
        rel_parts = p.relative_to(VAULT).parts
        if excluded(rel_parts):
            continue
        rel = str(p.relative_to(VAULT))
        total += 1

        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        fm = parse_fm(text)
        if fm is None:
            findings["no_frontmatter"].append(rel)
            continue

        if NOTION_KEYS & set(fm) and not ({"type", "stage"} & set(fm)):
            notion_only += 1
            findings["notion_residue_only"].append(rel)

        missing = [k for k in REQUIRED if k not in fm or not fm[k]]

        # A Notion-imported note whose export carried no creation date can never
        # get one from inside the vault — the only recovery route is matching
        # titles against the original workspace. Counting those as compliance
        # failures would peg the metric at ~21% forever, which reads as "broken"
        # rather than "here is what to fix". They are reported in their own
        # bucket instead, so the number stays actionable and the backlog stays
        # visible. (Until 2026-07-26 these carried `created` copied from the
        # export timestamp, which looked precise and was wrong.)
        #
        # Notion DATABASE / workspace exports (`type: database`, or the `_all` /
        # bare-UUID / `database<hash>` filename shapes) are a different object
        # class from a dated page: they have no single `created_at` to recover
        # without walking to a parent page, so they are counted as
        # `notion_residue` (a bounded, known queue) and kept OUT of the
        # `undateable_imports` CRITICAL channel — otherwise a known object class
        # fires CRITICAL every week forever and drowns the real signal.
        if "created" in missing and fm.get("synced"):
            missing.remove("created")
            if _is_notion_database_export(rel, fm):
                findings["notion_residue"].append(rel)
            else:
                findings["undateable_imports"].append(rel)
        if not rel.startswith(DOMAIN_EXEMPT_PREFIXES) and not fm.get("domain"):
            missing.append("domain")
        if rel.startswith(STATUS_SCOPE) and not fm.get("status"):
            missing.append("status")
        if missing:
            findings["missing_required"].append(f"{rel} :: missing {','.join(missing)}")

        t = fm.get("type", "").strip().strip("\"'")
        if t:
            type_vals[t] += 1
            if t not in TYPE_VOCAB:
                findings["bad_type"].append(f"{rel} :: type={t}")
            # Correctness, not just presence: a `type` that implies a location
            # must actually sit there. Checking only that a field EXISTS lets a
            # confidently-wrong value pass, which is how `stage` carried CRM
            # values for months without anything noticing.
            # Scaffolds are exempt: a template's frontmatter describes the note it
            # PRODUCES, not itself, so `Raw Capture.md` correctly says `type: raw`
            # while living in _Scaffolds/.
            need = TYPE_REQUIRES_PREFIX.get(t)
            if need and not rel.startswith(need) and not rel.startswith(TEMPLATE_PREFIXES):
                findings["type_path_mismatch"].append(
                    f"{rel} :: type={t} but expected under {' or '.join(need)}"
                )

        # `domain` is ~99% derivable from the folder, so it is kept but VERIFIED
        # rather than trusted: a stored copy of the path silently goes stale the
        # moment a note moves. A deliberate override is fine — it just has to be
        # a conscious one, and this surfaces it.
        # Reported as an OVERRIDE, not an error — this is the one case where
        # `domain` earns its keep. An AISED tender scan landing in the generic
        # 00-Raw sink is genuinely about `aised`, which the folder cannot say.
        # Listing them keeps each override a conscious choice rather than drift.
        d = fm.get("domain", "").strip().strip("\"'")
        implied = derive_domain(rel)
        if d and implied and d != implied and not rel.startswith(TEMPLATE_PREFIXES):
            findings["domain_override"].append(
                f"{rel} :: domain={d} (folder implies {implied})"
            )

        s = fm.get("status", "").strip().strip("\"'")
        if s:
            status_vals[s] += 1
            low = s.lower()
            if low not in STATUS_VOCAB:
                canon = STATUS_ALIASES.get(low)
                findings["bad_status"].append(
                    f"{rel} :: status={s}" + (f" -> {canon}" if canon else "")
                )

        st = fm.get("stage", "").strip().strip("\"'")
        if st:
            stage_vals[st] += 1
            low = st.lower()
            if low in PIPELINE_VALUES:
                findings["crm_in_stage"].append(f"{rel} :: stage={st}")
            elif low not in STAGE_VOCAB:
                # `stage` is deprecated in favour of `type`, so an off-vocabulary
                # value here is a leftover to migrate, not a compliance failure.
                findings["legacy_stage"].append(f"{rel} :: stage={st}")

        # Template bodies legitimately carry placeholders like {{date:YYYY-MM-DD}};
        # that is the template working correctly, not a malformed date.
        if not rel.startswith(("Knowledge/_Scaffolds/", "Knowledge/40-Templates/")):
            for key in ("created", "updated"):
                if key in fm and fm[key] and not DATE_RE.match(fm[key].strip("\"'")):
                    findings["bad_date"].append(f"{rel} :: {key}={fm[key]}")

        # Tags are recommended, not required: `type`/`domain`/`status` carry the
        # structure now, and mandating tags on every note only breeds junk tags.
        # Still measured, so coverage stays visible.
        if not fm.get("tags"):
            findings["no_tags"].append(rel)

        raw_tags = fm.get("tags", "")
        for tg in re.split(r"[,\[\]]", raw_tags):
            tg = tg.strip().strip("\"'")
            if tg:
                tag_vocab[tg] += 1

        if not missing and not (s and s.lower() not in STATUS_VOCAB) and \
           not (t and t not in TYPE_VOCAB):
            compliant += 1

    summary = {
        "total_files": total,
        "compliant": compliant,
        "compliance_pct": round(100 * compliant / total, 1) if total else 0,
        "no_frontmatter": len(findings["no_frontmatter"]),
        "no_tags": len(findings["no_tags"]),
        "undateable_imports": len(findings["undateable_imports"]),
        "notion_residue": len(findings["notion_residue"]),
        "type_path_mismatch": len(findings["type_path_mismatch"]),
        "domain_override": len(findings["domain_override"]),
        "missing_required": len(findings["missing_required"]),
        "notion_residue_only": notion_only,
        "crm_in_stage": len(findings["crm_in_stage"]),
        "bad_status": len(findings["bad_status"]),
        "bad_type": len(findings["bad_type"]),
        "bad_stage": len(findings["bad_stage"]),
        "legacy_stage": len(findings["legacy_stage"]),
        "bad_date": len(findings["bad_date"]),
        "distinct_tags": len(tag_vocab),
        "distinct_status_values": len(status_vals),
        "distinct_type_values": len(type_vals),
        "top_tags": tag_vocab.most_common(15),
        "status_values": status_vals.most_common(),
        "stage_values": stage_vals.most_common(),
    }

    try:
        HEALTH_FILE.write_text(json.dumps(
            {"timestamp": __import__("datetime").datetime.now().isoformat(), **summary},
            indent=2))
    except OSError as e:
        print(f"warning: could not write health file: {e}", file=sys.stderr)

    print(json.dumps({"summary": summary,
                      "findings": {k: v[:25] for k, v in findings.items()}}, indent=1))


if __name__ == "__main__":
    main()
