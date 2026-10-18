#!/usr/bin/env python3
"""Phase 2a — extract text from the Strathclyde MBA PDF archive to a staging dir.

Why stage outside the vault: the overnight embedding run must not depend on read
access to ~/Documents. That path is TCC-protected and a detached/scheduled process
does not reliably inherit the grant (see the 2026-07-26 cron blackout). Extraction
runs here, in a session that demonstrably has access; the long job then reads only
~/.hermes_rag/extracted/, which is not protected.

Run with the SYSTEM python (3.9) — it is the one with PyMuPDF installed:
    /usr/bin/python3 ~/.hermes/scripts/mba-extract.py
"""
import hashlib
import json
import os
import re
import time
from pathlib import Path

import fitz  # PyMuPDF — present in system python 3.9, no install needed

SRC = Path("/Users/admin/Documents/Obsidian Vault/Knowledge/00-Raw/University of Strathclyde MBA")
OUT = Path("/Users/admin/.hermes_rag/extracted/mba")
MANIFEST = Path("/Users/admin/.hermes_rag/extracted/mba-manifest.json")

# A single 193-page reading otherwise costs as much to embed as 20 assignments.
CHAR_CAP = 30_000
# Below this, a "text layer" is really just page furniture — treat as image-only.
MIN_CHARS_PER_PAGE = 20

# The job may be cut short. Order matters: the user's own work first, generic
# lecture readings last, so a truncated run still leaves the valuable half done.
PRIORITY = [
    (re.compile(r"/Thesis/", re.I), 0),
    (re.compile(r"/(MBA SAE|Oct11 SAE)/", re.I), 1),
    (re.compile(r"/Assignments - MBA/", re.I), 2),
    (re.compile(r"/(MBA GOS|TSS|IAF|IB|IS)/", re.I), 3),
]


def priority(path: str) -> int:
    for pat, rank in PRIORITY:
        if pat.search(path):
            return rank
    return 9


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(str(p) for p in SRC.rglob("*") if p.suffix.lower() == ".pdf")
    print(f"PDFs found: {len(pdfs)}")

    seen: dict[str, str] = {}
    stats = dict(total=len(pdfs), duplicate=0, no_text=0, error=0, written=0,
                 chars=0, capped=0)
    manifest = []
    t0 = time.time()

    for i, p in enumerate(pdfs, 1):
        try:
            raw = Path(p).read_bytes()
        except OSError as e:
            stats["error"] += 1
            continue
        h = hashlib.sha256(raw).hexdigest()
        if h in seen:                      # the FINAL/ tree exists in triplicate
            stats["duplicate"] += 1
            continue
        seen[h] = p

        try:
            doc = fitz.open(p)
            text = "".join(page.get_text() for page in doc)
            pages = doc.page_count
            doc.close()
        except Exception:
            stats["error"] += 1
            continue

        text = text.strip()
        if pages and len(text) < MIN_CHARS_PER_PAGE * pages:
            stats["no_text"] += 1          # image-only: an OCR job, out of scope
            continue
        if not text:
            stats["no_text"] += 1
            continue

        if len(text) > CHAR_CAP:
            text = text[:CHAR_CAP]
            stats["capped"] += 1

        rel = Path(p).relative_to(SRC)
        # Flatten into one directory but keep the full path in the name, so a
        # search hit still says which module and folder it came from.
        flat = re.sub(r"[^A-Za-z0-9._-]+", "_", str(rel.with_suffix("")))[:180]
        dest = OUT / (flat + ".txt")
        # Distinct sources can flatten to the same name ("Nation branding.pdf" vs
        # "Nation_branding.pdf"). Without this, one silently overwrites the other.
        if dest.exists():
            dest = OUT / f"{flat}-{h[:8]}.txt"
        header = f"# Source: Knowledge/00-Raw/University of Strathclyde MBA/{rel}\n\n"
        dest.write_text(header + text, encoding="utf-8")

        stats["written"] += 1
        stats["chars"] += len(text)
        manifest.append({"staged": str(dest), "source": str(rel),
                         "chars": len(text), "pages": pages,
                         "priority": priority(p)})
        if i % 200 == 0:
            print(f"  {i}/{len(pdfs)} scanned, {stats['written']} written "
                  f"({time.time()-t0:.0f}s)", flush=True)

    manifest.sort(key=lambda m: (m["priority"], -m["chars"]))
    MANIFEST.write_text(json.dumps({"stats": stats, "files": manifest}, indent=1),
                        encoding="utf-8")

    chunks = stats["chars"] / 1600
    print(f"\n--- extraction complete in {time.time()-t0:.0f}s ---")
    for k, v in stats.items():
        print(f"  {k:<10} {v:,}")
    print(f"\n  staged dir: {OUT}")
    print(f"  manifest:   {MANIFEST}")
    print(f"  est chunks: {chunks:,.0f}")
    print(f"  est embed:  {chunks*1.083/3600:.1f}h best / {chunks*2.1/3600:.1f}h mid")


if __name__ == "__main__":
    main()
