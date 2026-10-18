#!/usr/bin/env python3
"""Rebuild System/Wiki/index.md and Knowledge/Knowledge MOC.md from what is on disk.

Both indexes were overwritten on 2026-07-26 by a remote rsync with much smaller
copies, taking the vault from 0 orphans to 107. This regenerates them by walking
the folders, so every page on disk gets an incoming link.

Usage: python3 vault-rebuild-indexes.py <outdir>   # review, then copy into the vault.
Writes to an output dir first; nothing lands in the vault until reviewed.
"""
import os
import re
import shutil
import sys
from datetime import date, datetime

VAULT = "/Users/admin/Documents/Obsidian Vault"
TODAY = date.today().isoformat()

# Where each generated index lands in --install mode.
TARGETS = {
    "Wiki-index.md": "System/Wiki/index.md",
    "Knowledge MOC.md": "Knowledge/Knowledge MOC.md",
}

FM = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def meta(relpath):
    """Return (title, description) for a note, preferring frontmatter."""
    path = os.path.join(VAULT, relpath)
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return humanize(relpath), ""
    title = desc = ""
    m = FM.match(text)
    body = text[m.end():] if m else text
    if m:
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "title" and v:
                title = v
            elif k in ("description", "summary") and v and not desc:
                desc = v
    if not title:
        h = re.search(r"^#\s+(.+)$", body, re.M)
        title = h.group(1).strip() if h else humanize(relpath)
    if not desc:
        # First prose line. Skip structure (headings, quotes, tables, lists,
        # fences) and mid-sentence fragments — a callout's continuation line or a
        # numbered list item reads as a broken description in the index.
        for line in body.splitlines():
            s = line.strip()
            if not s or s.startswith(("#", ">", "|", "-", "*", "`", "!", "[!", "---")):
                continue
            if s.startswith("**") and s.endswith("**"):
                continue
            if re.match(r"^\d+[.)]\s", s):
                continue
            if s[0].islower():
                continue
            desc = s
            break
    desc = re.sub(r"\^\S+", "", desc)  # block refs
    # Unwrap wikilinks to their label before stripping punctuation, or a link at
    # the start of the line leaves the raw target behind as the description.
    desc = re.sub(r"\[\[([^\]\|]+)\|([^\]]+)\]\]", r"\2", desc)
    desc = re.sub(r"\[\[([^\]]+)\]\]", lambda m: m.group(1).split("/")[-1], desc)
    desc = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", desc)  # markdown links
    desc = re.sub(r"[*_`\[\]]", "", desc)
    desc = re.sub(r"\s+", " ", desc).strip()
    # Prefer a complete first sentence over a line clipped mid-thought.
    first = re.match(r"^(.{40,}?[.!?])(?:\s|$)", desc)
    if first:
        desc = first.group(1)
    desc = desc.rstrip(".")
    if len(desc) > 110:
        desc = desc[:107].rsplit(" ", 1)[0] + "…"
    if len(desc) < 15:
        desc = ""
    title = re.sub(r"\s+", " ", title).strip()
    return title, desc


def humanize(relpath):
    stem = os.path.basename(relpath)[:-3]
    return re.sub(r"[-_]+", " ", stem).strip()


def entry(relpath, with_desc=True):
    title, desc = meta(relpath)
    link = relpath[:-3]  # vault-root path, no extension — resolver matches exactly
    if title.lower() == link.lower():
        line = f"- [[{link}]]"
    else:
        line = f"- [[{link}|{title}]]"
    if with_desc and desc:
        line += f" — {desc}"
    return line


def listdir_md(reldir, recursive=False):
    base = os.path.join(VAULT, reldir)
    out = []
    if recursive:
        for root, dirs, files in os.walk(base):
            dirs.sort()
            for f in sorted(files):
                if f.endswith(".md"):
                    out.append(os.path.relpath(os.path.join(root, f), VAULT))
    else:
        for f in sorted(os.listdir(base)):
            if f.endswith(".md"):
                out.append(os.path.join(reldir, f))
    return out


def section(heading, files, exclude=(), with_desc=True, note=None):
    files = [f for f in files if os.path.basename(f) not in exclude]
    if not files:
        return ""
    body = [f"### {heading} ({len(files)})", ""]
    if note:
        body += [note, ""]
    body += [entry(f, with_desc) for f in files]
    body.append("")
    return "\n".join(body)


# ─────────────────────────────────────────────────────────────── Wiki index ──

WIKI_ROOT_INTEL = {
    "thought-leaders-index.md",
    "source-diversity.md",
    "bias-detection.md",
    "vault-semantic-search.md",
}
WIKI_ROOT_META = {"index.md", "SCHEMA.md", "log.md"}


def build_wiki_index():
    root = [f for f in listdir_md("System/Wiki")]
    base = lambda f: os.path.basename(f)
    intel = [f for f in root if base(f) in WIKI_ROOT_INTEL]
    metaf = [f for f in root if base(f) in WIKI_ROOT_META and base(f) != "index.md"]
    domain = [f for f in root if base(f) not in WIKI_ROOT_INTEL | WIKI_ROOT_META]

    oct_dirs = sorted(
        d for d in os.listdir(os.path.join(VAULT, "System/Wiki/October-Archive"))
        if os.path.isdir(os.path.join(VAULT, "System/Wiki/October-Archive", d))
    )

    parts = [
        """---
cycle: 1
status: published
created: 2026-07-26
updated: __TODAY__
generated: true
tags: [wiki, index, system, claude-code]
type: reference
domain: system
---

# System Wiki Index

This is the canonical knowledge base of the vault. Authoritative facts live here.

> [!warning] Generated file — do not hand-edit
> Rebuilt daily by `~/.hermes/scripts/vault-rebuild-indexes.py` from what is on
> disk. Anything typed here is lost on the next run; put durable prose on the page
> it describes instead. Add a page to the folder and it appears here automatically.
>
> This file exists because it was overwritten on 2026-07-26 by a remote sync with a
> six-page copy, which took the vault from 0 orphans to 107. Making it generated
> means the next overwrite costs nothing. Prior version:
> `System/Backups/indexes/Wiki-index.md.bak-2026-07-26`.

## Domain References
""",
        "\n".join(entry(f) for f in domain),
        "\n\n## Intelligence & Epistemics\n",
        "\n".join(entry(f) for f in intel),
        "\n\n## Entities\n",
        "\n".join(entry(f) for f in listdir_md("System/Wiki/entities")),
        "\n\n### GitHub Repos\n",
        "\n".join(entry(f, with_desc=False) for f in listdir_md("System/Wiki/entities/github-repos")),
        "\n\n## Concepts\n",
        "\n".join(entry(f) for f in listdir_md("System/Wiki/concepts")),
        "\n\n## Comparisons\n",
        "\n".join(entry(f) for f in listdir_md("System/Wiki/comparisons")),
        "\n\n## Queries\n",
        "\n".join(entry(f) for f in listdir_md("System/Wiki/queries")),
        "\n\n## Raw Source Material\n",
        "\n".join(entry(f) for f in listdir_md("System/Wiki/raw", recursive=True)),
        "\n\n## Reports\n",
        """- [[Knowledge/20-Reports/AiSED-Kosovo-Concept-Paper-v1|ASEAN-Balkans Economic Cooperation Corridor — Kosovo Pilot]]
- [[Knowledge/20-Reports/Intelligence-Cronjobs-Improvement-Plan|Intelligence Cronjobs Improvement Plan]]
- Full report index: [[Knowledge/Knowledge MOC|Knowledge MOC]]
""",
        "\n## Meta\n",
        "\n".join(entry(f) for f in metaf),
        """

## October Archive

Frozen material from the October agent era. Kept for provenance; not maintained.
Pages here carry no `last-reviewed` date, which is why they dominate §3 of the
weekly flywheel health check (`System/_generated/flywheel-health-<date>.md`).
""",
    ]

    for d in oct_dirs:
        files = listdir_md(f"System/Wiki/October-Archive/{d}")
        if files:
            parts.append("\n" + section(d, files, with_desc=False))
    stray = [f for f in listdir_md("System/Wiki/October-Archive")]
    if stray:
        parts.append("\n" + section("Unfiled", stray, with_desc=False))

    return "".join(parts).rstrip() + "\n"


# ────────────────────────────────────────────────────────────── Knowledge MOC ──

# Raw captures at 00-Raw root fall into a few clear families. Classified by
# filename pattern so the grouping is reproducible on the next rebuild.
RAW_BUCKETS = [
    ("Vault Index", re.compile(r"^_index$")),
    ("Cycle & Pipeline Artifacts", re.compile(r"^(Cycle Kickoff|.*-cycle-|distillation)", re.I)),
    ("Meeting Notes", re.compile(r"^Meeting-Notes-", re.I)),
    ("Trend & Intelligence Captures", re.compile(
        r"(Trends-—|Insights-—|Market-Trends|CSuite|FB-Consumer|Hospitality|Property-Infrastructure|"
        r"AI-Thinkers|arXiv|daily-intelligence-briefs|intelligence-cronjobs|ai-thinkers)", re.I)),
    ("Handoffs & Project Captures", re.compile(
        r"(handoff|handover|backup|architecture|mandate|uzbekistan|metrojaya|rewoven|kairos|october|hermes|"
        r"zehan-bio|sprint-\d|futuretech)", re.I)),
    ("Reflection & Goal-Setting", re.compile(
        r"(reflective|goal-setting|reflection|reflecting|journal|memory-journals|awakening|biggest-risk|"
        r"books-for-breakthroughs|deep-dive-|diving-deeper|how-to-cultivate|top-5-|top-goals|yearly-goals|"
        r"principles-zehan|to-improve-in-these-areas|building-resilience|gratitude|building-a-meaningful)", re.I)),
    ("Software Delivery Checklist", re.compile(
        r"^(implement-feature|deploy-feature|create-automated-tests|integrate-into-existing|optimize-for-performance|"
        r"test-feature|user-feedback-on-feature|user-testing-of-feature|validate-feature|write-documentation-for|"
        r"write-technical-spec|brainstorm-feature)", re.I)),
    ("Prompt & Marketing Workflow Library", re.compile(
        r"^(generating|creating|crafting|optimizing|analyzing|developing|implementing|research|produce|monitoring|"
        r"engage|sales|content|daily-entry|gtm|kanban|device-protection|auth-error|the-variety|ai-assistant-for)", re.I)),
    ("Web Clippings — URL-Named Imports", re.compile(r"^https", re.I)),
    ("AI, Agents & Technical Research", re.compile(
        r"(generative-ai|vectors-and-embeddings|mcp-integration|ollama|cybersecurity|browser-automation|"
        r"learning-paths|key-thought-leaders|fault-lines|data-foresight|deeptech|neural-networks|"
        r"10-ways-to-get-massively-ahead|cangjie|vault-code)", re.I)),
    ("Trading & Markets", re.compile(
        r"(trading|quant|technical-analysis-reports|december-trading|kiki)", re.I)),
    ("Insurance & InsurTech", re.compile(r"(insurance|cytora|underwriting|telcoassurance)", re.I)),
    ("Business & Strategy", re.compile(
        r"(business-ideas|why-startups-fail|sustainability|ip-protection|words-business|network-effect|"
        r"customer-discovery|pretotype|leadership)", re.I)),
    ("Personal & Workspace Imports", re.compile(
        r"^(personal-|people-index|daily-scans|deep-dives|europe-trip|hawaii|health|movie-list|travel|"
        r"just-another-day|quick-note|ideas|reading-links|words|the-alchemist|the-originals|daily-bazi|"
        r"michelle|january|getting-started-on-mobile|✈️)", re.I)),
]


def bucket_raw(files):
    groups = {name: [] for name, _ in RAW_BUCKETS}
    other = []
    for f in files:
        stem = os.path.basename(f)[:-3]
        for name, pat in RAW_BUCKETS:
            if pat.search(stem):
                groups[name].append(f)
                break
        else:
            other.append(f)
    return groups, other


def build_knowledge_moc():
    raw_root = listdir_md("Knowledge/00-Raw")
    groups, other = bucket_raw(raw_root)

    parts = ["""---
cycle: 1
status: published
created: 2026-07-26
updated: __TODAY__
generated: true
type: moc
domain: knowledge
tags: [knowledge, index, claude-code]
---

# Knowledge Map of Content

> [!important] Frontmatter contract
> Every note follows [[System/SCHEMA|Schema v2]] — `type`, `created`, `domain`
> (plus `status` under `Knowledge/` and `System/Wiki/`). `type` is the lifecycle
> axis; `stage` is deprecated. Drift is measured weekly in §5 of the flywheel
> health check, written to `System/_generated/flywheel-health-<date>.md`.
>
> Start a new note from [[Knowledge/_Scaffolds/Raw Capture|Knowledge/_Scaffolds/]] —
> the scaffolds already emit conforming frontmatter.

> [!warning] Generated file — do not hand-edit
> Rebuilt daily by `~/.hermes/scripts/vault-rebuild-indexes.py` from what is on
> disk. Anything typed here is lost on the next run; put durable prose on the note
> it describes instead. Add a capture to the folder and it appears here automatically.
>
> This file exists because a remote sync replaced it on 2026-07-26 with a 39-line
> copy, orphaning 70 captures under `00-Raw/`. Making it generated means the next
> overwrite costs nothing. Prior version:
> `System/Backups/indexes/Knowledge MOC.md.bak-2026-07-26`.

## Pipeline

```mermaid
flowchart LR
    Raw[00-Raw] --> Wiki[System/Wiki]
    Raw --> Reports[20-Reports]
    Reports --> Distilled[30-Distilled]
    Distilled --> Templates[40-Templates]
```

The wiki tier is [[System/Wiki/index|System/Wiki]] itself — `10-Wikis/` holds only
pre-promotion drafts. A staging copy left beside a promoted page forks the wiki.

## 20-Reports — Dated Outputs

"""]
    parts.append("\n".join(entry(f) for f in listdir_md("Knowledge/20-Reports")))

    parts.append("\n\n## 30-Distilled — Best Practices & Workflows\n\n")
    parts.append("\n".join(entry(f) for f in listdir_md("Knowledge/30-Distilled")))

    parts.append("\n\n## 40-Templates — Reusable Formats\n\n")
    parts.append("\n".join(entry(f) for f in listdir_md("Knowledge/40-Templates")))

    parts.append("\n\n## _Scaffolds — Note Starters\n\n")
    parts.append("\n".join(entry(f) for f in listdir_md("Knowledge/_Scaffolds")))

    parts.append("\n\n## 10-Wikis — Pre-Promotion Drafts\n\n")
    parts.append("\n".join(entry(f) for f in listdir_md("Knowledge/10-Wikis")))

    parts.append("\n\n## 00-Raw — Capture\n\n")
    parts.append(
        "Every capture below is unprocessed until it is cited by a report or promoted "
        "into [[System/Wiki/index|System/Wiki]]. Grouped by family; the sub-folders "
        "carry their own indexes.\n\n"
    )

    for name, _ in RAW_BUCKETS:
        files = groups[name]
        if files:
            parts.append(section(name, files, with_desc=False))
            parts.append("\n")
    if other:
        parts.append(section("Unfiled Captures", other, with_desc=False))
        parts.append("\n")

    parts.append("### Sub-Collections\n\n")
    # Discovered, not hardcoded: other agents add sub-folders here (the Strathclyde
    # MBA set appeared mid-session), and a hardcoded list silently orphans them.
    # Known folders get a written label; unknown ones still get indexed.
    labels = {
        "trends": "Daily market-trend reports, generated by the Hermes trend crons",
        "_empty": "Notion imports that arrived with no body — triage or delete",
        "content-pipeline": "Draft content queued for publication",
        "thought-leaders": "Daily thought-leader scan output",
        "research": "arXiv digest output",
        "policy": "Policy tracker output",
        "podcasts": "Podcast transcription output",
    }
    raw_base = os.path.join(VAULT, "Knowledge/00-Raw")
    discovered = sorted(d for d in os.listdir(raw_base)
                        if os.path.isdir(os.path.join(raw_base, d)))
    for sub in discovered:
        label = labels.get(sub, "Captures filed under this collection")
        files = listdir_md(f"Knowledge/00-Raw/{sub}")
        if not files:
            continue
        idx = [f for f in files if os.path.basename(f) in ("_index.md", "README.md")]
        parts.append(f"#### {sub}/ ({len(files)})\n\n{label}.\n\n")
        if idx:
            parts.append(entry(idx[0]) + "\n")
            rest = [f for f in files if f not in idx]
            if rest:
                parts.append("\n".join(entry(f, with_desc=False) for f in rest) + "\n")
        else:
            parts.append("\n".join(entry(f, with_desc=False) for f in files) + "\n")
        parts.append("\n")

    parts.append("""## Related

- [[System/Wiki/index|System Wiki Index]] — the canonical wiki tier
- [[System/SCHEMA|Schema v2]] — the vault-wide frontmatter contract
- [[Knowledge/Knowledge.base|Knowledge.base]] — cross-stage views
- `System/_generated/flywheel-health-<date>.md` — weekly pipeline health check
""")
    return "".join(parts)


def main(argv):
    install = "--install" in argv
    rest = [a for a in argv if not a.startswith("-")]
    out = rest[0] if rest else "."
    if install:
        # Runs daily from cron and appends to one log; stamp each run or the log
        # is an undifferentiated stream.
        print(f"── {datetime.now():%Y-%m-%d %H:%M:%S} ──")

    built = [(name, text.replace("__TODAY__", TODAY)) for name, text in
             [("Wiki-index.md", build_wiki_index()),
              ("Knowledge MOC.md", build_knowledge_moc())]]

    if not install:
        os.makedirs(out, exist_ok=True)
        for name, text in built:
            p = os.path.join(out, name)
            open(p, "w", encoding="utf-8").write(text)
            print(f"{p}: {len(text.splitlines())} lines, {len(text)} bytes, "
                  f"{text.count('[[')} links")
        print("Review, then re-run with --install to write into the vault.")
        return 0

    changed = 0
    for name, text in built:
        dest = os.path.join(VAULT, TARGETS[name])
        try:
            current = open(dest, encoding="utf-8").read()
        except OSError:
            current = None
        if current == text:
            print(f"unchanged: {TARGETS[name]}")
            continue
        # Keep one rolling copy of whatever we are replacing. If a sync clobbered
        # the file, this is the clobbered version, not the good one — the point is
        # to leave evidence of what happened, not to restore from here.
        if current is not None:
            bak = os.path.join(VAULT, "System/Backups/indexes",
                               f"{name}.replaced-{TODAY}")
            os.makedirs(os.path.dirname(bak), exist_ok=True)
            shutil.copy2(dest, bak)
        open(dest, "w", encoding="utf-8").write(text)
        os.chmod(dest, 0o644)  # remote sync writes 0600; keep it readable
        delta = "" if current is None else f" (was {len(current.splitlines())} lines)"
        print(f"installed: {TARGETS[name]} — {len(text.splitlines())} lines, "
              f"{text.count('[[')} links{delta}")
        changed += 1
    print(f"{changed} file(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
