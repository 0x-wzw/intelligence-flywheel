#!/usr/bin/env python3
"""Find orphaned notes (no incoming wikilinks/embeds/md-links) in an Obsidian vault."""
import os, re, json, collections

VAULT = "/Users/admin/Documents/Obsidian Vault"
SKIP_DIRS = {".obsidian", ".trash", ".git", ".smart-env", "node_modules",
             "_generated"}  # machine-written reports are not orphans

# ── Skip rules (2026-10-08 orphan-metric calibration) ────────────────────────
# The raw scanner counted machine-managed material as orphans, inflating the
# flywheel's orphan row (249 → 503 in one week purely from cron-output
# accumulation). Skipped classes, each with its reason:
SKIP_TREE_PREFIXES = (
    "System/Backups/",                      # machine archive snapshots (cron output, dated backups)
    "Daily/",                               # calendar-navigated journal; consumed by vault-learn, never backlinked
    "Inbox/",                               # intake staging — unprocessed IS the expected state there
    "handoffs/",                            # deliverables, consumed via the vault-handoff embedding workflow
    "Knowledge/30-Distilled/gartner-docs/", # pre-promotion staging; each has a System/Wiki twin
    "System/Assistant/logs/",               # append-only machine logs
    "System/Assistant/skills/",             # agent infrastructure, consumed by the skill loader
)
SKIP_DIR_NAMES = {"_Scaffolds"}             # templates, consumed by the creation flow
SKIP_NAME_CONTAINS = ("handoff",)           # deliverable handoffs at any depth (RAG-embedded)
NOTION_ID = re.compile(r"^notion_id:", re.M)  # Notion-synced mirrors: their hub is upstream Notion

def _skipped(rel, text_head):
    parts = rel.split("/")
    if any(part in SKIP_DIR_NAMES for part in parts[:-1]):
        return True
    if rel.startswith(SKIP_TREE_PREFIXES):
        return True
    base = parts[-1]
    if base.startswith("_"):                # machine index files (_INDEX.md etc.)
        return True
    if any(s in base.lower() for s in SKIP_NAME_CONTAINS):
        return True
    if NOTION_ID.search(text_head):
        return True
    return False

files = []
for root, dirs, fnames in os.walk(VAULT):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for f in fnames:
        if f.endswith(".md"):
            files.append(os.path.relpath(os.path.join(root, f), VAULT))

# resolution maps
by_rel = {f[:-3].lower(): f for f in files}
by_base = collections.defaultdict(list)                  # basename w/o .md
for f in files:
    by_base[os.path.basename(f)[:-3].lower()].append(f)

WIKI = re.compile(r'!?\[\[([^\]\|#\^]+)(?:[#\^][^\]\|]*)?(?:\|[^\]]*)?\]\]')
MDLINK = re.compile(r'!?\[[^\]]*\]\(([^)]+)\)')
# frontmatter/inline unlinked references e.g. "source: Some Note"
CODEBLOCK = re.compile(r'```.*?```|`[^`\n]+`', re.S)  # fenced blocks AND inline code spans

incoming = collections.defaultdict(set)
unresolved = collections.defaultdict(list)
outgoing_count = {}

def resolve(target, src):
    """Mimic Obsidian: match the shortest file path that ENDS WITH the linkpath.

    A bare name matches any basename; a link containing '/' must match a real path
    suffix at a segment boundary. Same-folder candidates win ties, then shortest path.
    """
    t = target.strip().rstrip(chr(92)).strip().rstrip("/").lower()
    if t.endswith('.md'):
        t = t[:-3]
    if not t:
        return None
    if t in by_rel:                      # exact vault-root path
        return by_rel[t]
    # relative to the linking note
    cand = os.path.normpath(os.path.join(os.path.dirname(src).lower(), t))
    if cand in by_rel:
        return by_rel[cand]
    # path-suffix match at a segment boundary
    hits = [f for k, f in by_rel.items() if k == t or k.endswith("/" + t)]
    if not hits:
        return None
    same = [h for h in hits if os.path.dirname(h) == os.path.dirname(src)]
    pool = same or hits
    return min(pool, key=lambda h: (h.count("/"), len(h), h))


for f in files:
    p = os.path.join(VAULT, f)
    try:
        text = open(p, encoding="utf-8", errors="ignore").read()
    except Exception:
        continue
    text_nc = CODEBLOCK.sub("", text)
    targets = [m.group(1) for m in WIKI.finditer(text_nc)]
    for m in MDLINK.finditer(text_nc):
        u = m.group(1)
        if "://" in u or u.startswith("#") or u.startswith("mailto:"):
            continue
        targets.append(u.split("#")[0].replace("%20", " "))
    outgoing_count[f] = len(targets)
    for t in targets:
        r = resolve(t, f)
        if r and r != f:
            incoming[r].add(f)
        elif not r:
            unresolved[t].append(f)

skipped_count = 0
orphans = []
for f in sorted(files):
    p = os.path.join(VAULT, f)
    try:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            text_head = fh.read(800)
    except Exception:
        text_head = ""
    if _skipped(f, text_head):
        skipped_count += 1
        continue
    if not incoming[f]:
        orphans.append(f)
print(json.dumps({
    "total": len(files),
    "skipped_machine_managed": skipped_count,
    "orphans": orphans,
    "orphan_count": len(orphans),
    "unresolved_top": sorted(((len(v), k) for k, v in unresolved.items()), reverse=True)[:40],
    "unresolved_total": len(unresolved),
}, indent=1))
