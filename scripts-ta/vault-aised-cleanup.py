#!/usr/bin/env python3
"""
vault-aised-cleanup.py — deterministic, idempotent AISED vault maintenance.

Operates ONLY on:
  <vault>/Inbox/Notion/     (the Notion sync landing zone)
  <vault>/Work/AISED/       (the filed AISED corpus)
  <vault>/*.md              (top-level root notes)

Never touches .obsidian, .hermes, .trash, or any other folder.

Pipeline (all gated behind --apply; default is dry-run):
  1. Intra-folder dedupe of Inbox/Notion  — drop byte-identical slug/Title twins,
     keep the more readable variant.
  2. Tag AISED docs                        — add `project: aised` to inbox/root notes
     whose frontmatter title matches the AISED keyword set and lack a project key.
  3. Cross-folder dedupe                    — delete inbox copies byte-identical to a
     file already in Work/AISED (re-synced duplicate of an already-filed doc).
  4. Move tagged docs into Work/AISED       — on basename collision: identical -> delete
     source; different bytes -> leave + emit ANOMALY (a genuine Notion edit to review).

Prints a human summary (or JSON with --json) and appends an audit line to
~/.hermes/logs/aised-cleanup.log.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys

DEFAULT_VAULT = "/Users/admin/Documents/Obsidian Vault"
AUDIT_LOG = os.path.expanduser("~/.hermes/logs/aised-cleanup.log")

AISED_TITLE_RE = re.compile(
    r"title:.*(AISED|AiSED|DAULAT|MAHKAMAH|LAKSAMANA|TUAH|RIMBA|PENDETA|PERWIRA|Istana.?Pitch)",
    re.IGNORECASE,
)


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def readability_score(name):
    """Higher = more human-readable variant to keep among byte-identical twins."""
    body = name[:-3] if name.endswith(".md") else name
    s = 0
    if "—" in body:            # em dash — Title-Case Notion export
        s += 1000
    s += body.count("-") * 10       # more word separation
    if any(c.isupper() for c in body):
        s += 5
    return (s, -len(name))          # tie-break: shorter name


def frontmatter_block(text):
    """Return (fm_text, full_match) for the leading --- ... --- block, or (None, None)."""
    m = re.match(r"---\n(.*?\n)---\n", text, re.S)
    if not m:
        return None, None
    return m.group(1), m.group(0)


def list_md(directory, recursive=False):
    out = []
    if not os.path.isdir(directory):
        return out
    if recursive:
        for root, _dirs, files in os.walk(directory):
            for f in files:
                if f.endswith(".md"):
                    out.append(os.path.join(root, f))
    else:
        for f in os.listdir(directory):
            p = os.path.join(directory, f)
            if f.endswith(".md") and os.path.isfile(p):
                out.append(p)
    return out


def run(vault, apply):
    inbox = os.path.join(vault, "Inbox", "Notion")
    aised = os.path.join(vault, "Work", "AISED")
    os.makedirs(aised, exist_ok=True)

    report = {"deduped": [], "tagged": [], "moved": [], "anomalies": []}

    # ---- 1. Intra-folder dedupe of Inbox/Notion -------------------------------
    groups = {}
    for p in list_md(inbox):
        groups.setdefault(md5(p), []).append(p)
    for _h, paths in groups.items():
        if len(paths) < 2:
            continue
        keep = sorted(paths, key=lambda p: readability_score(os.path.basename(p)), reverse=True)[0]
        for p in paths:
            if p == keep:
                continue
            report["deduped"].append(os.path.basename(p))
            if apply:
                os.remove(p)

    # ---- 2. Tag AISED docs (inbox + top-level root) ---------------------------
    for p in list_md(inbox) + list_md(vault):
        try:
            text = open(p, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        fm, full = frontmatter_block(text)
        if fm is None or not AISED_TITLE_RE.search(fm):
            continue
        if re.search(r"^project:", fm, re.M):
            continue
        new_fm = re.sub(r"(title:.*\n)", r"\1project: aised\n", fm, count=1)
        if new_fm == fm:                       # no title line — prepend
            new_fm = "project: aised\n" + fm
        report["tagged"].append(os.path.basename(p))
        if apply:
            open(p, "w", encoding="utf-8").write(
                text.replace(full, "---\n" + new_fm + "---\n", 1)
            )

    # ---- 3. Cross-folder dedupe (inbox byte-identical to a filed AISED doc) ---
    aised_hashes = {md5(p) for p in list_md(aised)}
    for p in list_md(inbox):
        if md5(p) in aised_hashes:
            report["deduped"].append(os.path.basename(p) + " (==Work/AISED)")
            if apply:
                os.remove(p)

    # ---- 4. Move project:aised docs into Work/AISED ---------------------------
    for p in list_md(inbox) + list_md(vault):
        try:
            head = open(p, encoding="utf-8").read(400)
        except (OSError, UnicodeDecodeError):
            continue
        if not re.search(r"^project: aised", head, re.M):
            continue
        base = os.path.basename(p)
        target = os.path.join(aised, base)
        if os.path.exists(target):
            if md5(p) == md5(target):
                report["deduped"].append(base + " (dup of filed)")
                if apply:
                    os.remove(p)
            else:
                report["anomalies"].append(
                    "COLLISION: %s differs from Work/AISED copy — left in place for review" % base
                )
            continue
        report["moved"].append(base)
        if apply:
            shutil.move(p, target)

    return report


def summarize(report):
    lines = [
        "AISED cleanup summary:",
        "  deduped: %d" % len(report["deduped"]),
        "  tagged:  %d" % len(report["tagged"]),
        "  moved:   %d" % len(report["moved"]),
        "  anomalies: %d" % len(report["anomalies"]),
    ]
    for a in report["anomalies"]:
        lines.append("  ! " + a)
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Deterministic AISED vault cleanup.")
    ap.add_argument("--vault-path", default=DEFAULT_VAULT)
    ap.add_argument("--apply", action="store_true", help="perform changes (default: dry-run)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    if not os.path.isdir(args.vault_path):
        sys.exit("vault not found: %s" % args.vault_path)

    report = run(args.vault_path, args.apply)
    report["mode"] = "apply" if args.apply else "dry-run"
    report["counts"] = {k: len(report[k]) for k in ("deduped", "tagged", "moved", "anomalies")}

    # audit log (best-effort)
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG), exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(AUDIT_LOG, "a", encoding="utf-8") as fh:
            fh.write("%s [%s] deduped=%d tagged=%d moved=%d anomalies=%d\n" % (
                stamp, report["mode"], report["counts"]["deduped"],
                report["counts"]["tagged"], report["counts"]["moved"],
                report["counts"]["anomalies"]))
    except OSError:
        pass

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(summarize(report))


if __name__ == "__main__":
    main()
