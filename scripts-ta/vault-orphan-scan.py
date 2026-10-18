#!/usr/bin/env python3
"""Find orphaned notes (no incoming wikilinks/embeds/md-links) in an Obsidian vault."""
import os, re, json, collections

VAULT = "/Users/admin/Documents/Obsidian Vault"
SKIP_DIRS = {".obsidian", ".trash", ".git", ".smart-env", "node_modules",
             "_generated"}  # machine-written reports are not orphans

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

orphans = sorted(f for f in files if not incoming[f])
print(json.dumps({
    "total": len(files),
    "orphans": orphans,
    "orphan_count": len(orphans),
    "unresolved_top": sorted(((len(v), k) for k, v in unresolved.items()), reverse=True)[:40],
    "unresolved_total": len(unresolved),
}, indent=1))
