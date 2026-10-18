#!/usr/bin/env python3
"""
drop_vh_dupes.py — drop the 7 known duplicate vault-handoff chunks and
rebuild the companion index from the surviving 41.

Dupes identified by handoff_batch_embed.py dedup pass: 26291/26292/26293
(ILMU integration note, 342-char summary, keep 26294), 26297 (Grok-Bot
243-char summary, keep 26330), 26317 (MUIB Annual Report 21-char frontmatter
fallback, keep 26318), plus one more if the docstore now has 48.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, "/Users/admin")

os.environ.setdefault("HERMES_RAG_DATA", str(Path.home() / ".hermes_rag"))
os.environ.setdefault("HERMES_EMBED_DIM", "1024")
os.environ.setdefault("ILMU_EMBED_MODEL", "bge-m3")

from hermes_rag.store import DocStore  # noqa: E402
from hermes_rag.index import new_index, save_index  # noqa: E402

INDEX_PATH = Path.home() / ".hermes_rag" / "index-vault-handoff.tvim"
DIM = 1024


def main() -> int:
    s = DocStore()
    ids = s.ids_for_source("vault-handoff")
    print(f"vault-handoff chunks before drop: {len(ids)}")

    # ── group by (source, text) and find dupes ──────────────────────────────
    by_note: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for cid in ids:
        row = s.get_chunks([cid])
        if cid in row:
            by_note[row[cid]["source"]].append((cid, row[cid]["text"]))

    keep: set[int] = set()
    drop: list[int] = []
    groups = 0
    for note_path, chunks in by_note.items():
        by_text: dict[str, list[int]] = defaultdict(list)
        for cid, txt in chunks:
            by_text[txt].append(cid)
        for txt, cids in by_text.items():
            if len(cids) > 1:
                groups += 1
                cids.sort(reverse=True)
                keeper = cids[0]
                keep.add(keeper)
                for dup in cids[1:]:
                    drop.append(dup)
                print(f"  group {groups}: {note_path} "
                      f"keep={keeper} drop={cids[1:]} "
                      f"(text {len(txt)} chars)")

    if not drop:
        print("No dupes to drop — nothing to do.")
        s.close()
        return 0

    print()
    print(f"Dropping {len(drop)} duplicate chunk(s) from docstore …")
    done = 0
    failed = 0
    for cid in drop:
        try:
            s.delete_chunk(cid)
            done += 1
        except Exception as e:
            print(f"  FAIL drop chunk {cid}: {e}")
            failed += 1
    s.close()

    if failed:
        print(f"  dropped {done}, FAILED {failed} — aborting rebuild.")
        return 1

    print(f"  dropped {done}.")
    print()

    # ── rebuild companion index from surviving chunks ───────────────────────
    s2 = DocStore()
    surviving = [cid for cid in s2.ids_for_source("vault-handoff")
                 if cid not in drop]  # drop list may reference already-deleted
    rows = s2.get_chunks(surviving)
    to_index: list[tuple[int, list[float], str]] = []
    for cid in surviving:
        if cid in rows:
            vec = rows[cid].get("vector")
            txt = rows[cid].get("text", "")
            if vec and len(vec) == DIM:
                to_index.append((cid, vec, txt))
    s2.close()

    print(f"Rebuilding companion index from {len(to_index)} surviving chunks …")
    idx = new_index(DIM)
    vectors = [v for _, v, _ in to_index]
    ids_list = [cid for cid, _, _ in to_index]
    if vectors:
        idx.add_vectors(ids_list, vectors)
    save_index(idx, INDEX_PATH)
    print(f"  companion index: {len(to_index)} chunks / "
          f"{INDEX_PATH.stat().st_size} bytes at {INDEX_PATH}")

    # ── sanity: verify docstore now has no dupes ────────────────────────────
    s3 = DocStore()
    after_ids = s3.ids_for_source("vault-handoff")
    by_note2: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for cid in after_ids:
        row = s3.get_chunks([cid])
        if cid in row:
            by_note2[row[cid]["source"]].append((cid, row[cid]["text"]))
    remaining_dupes = 0
    for note_path, chunks in by_note2.items():
        by_text: dict[str, list[int]] = defaultdict(list)
        for cid, txt in chunks:
            by_text[txt].append(cid)
        for txt, cids in by_text.items():
            if len(cids) > 1:
                remaining_dupes += 1
                print(f"  STILL DUPED: {note_path} {cids} ({len(txt)} chars)")
    s3.close()

    if remaining_dupes:
        print(f"\nFAILED: {remaining_dupes} duplicate groups remain after drop.")
        return 1
    print(f"\nDone: {len(after_ids)} vault-handoff chunks, "
          f"0 duplicate groups, companion index rebuilt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
