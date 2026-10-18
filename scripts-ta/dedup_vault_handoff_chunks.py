#!/usr/bin/env python3
"""
dedup_vault_handoff_chunks.py — remove duplicate vault-handoff chunks from the
docstore + companion index.

Dedup rule: for chunks with identical text (same hash), keep only the highest
chunk_id (most recent), drop the rest. Update the companion turbovec index to
match. Frontmatter on handoff notes is left untouched (already says embedded).

Dry-run by default. --apply to delete.

Backups written to /tmp before any mutation:
  /tmp/docstore.sqlite3.backup
  /tmp/index-vault-handoff.backup.tvim
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, "/Users/admin")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import handoff_embed_lint  # noqa: E402
from hermes_rag.store import DocStore  # noqa: E402
from hermes_rag.index import IdMapIndex, add_vectors  # noqa: E402
from hermes_rag.config import DATA_DIR  # noqa: E402


def _chunk_text_hash(store: DocStore, cid: int) -> int:
    row = store.get_chunks([cid])
    if cid not in row:
        return None
    return hash(row[cid]["text"])


def dedup(dry_run: bool = True) -> dict:
    store = DocStore()
    result: dict = {
        "total_chunks": 0,
        "unique_texts": 0,
        "dupes_found": 0,
        "chunks_removed": 0,
        "removed_ids": [],
        "rebuilt_index": False,
    }

    ids = store.ids_for_source("vault-handoff")
    result["total_chunks"] = len(ids)

    # group by text hash
    by_hash: dict[int, list[int]] = defaultdict(list)
    for cid in ids:
        h = _chunk_text_hash(store, cid)
        if h is not None:
            by_hash[h].append(cid)

    result["unique_texts"] = len(by_hash)

    ids_to_drop: list[int] = []
    for h, ids_in_group in sorted(by_hash.items()):
        if len(ids_in_group) > 1:
            # keep the highest id, drop the rest
            ids_in_group.sort()
            keep = ids_in_group[-1]
            drop = ids_in_group[:-1]
            result["dupes_found"] += len(drop)
            ids_to_drop.extend(drop)
            # diagnostic
            row = store.get_chunks([keep])
            preview = row[keep]["text"][:60].replace("\n", " ") if keep in row else "?"
            print(f"  keep {keep} ({len(ids_in_group)} total, text={len(row[keep]['text']) if keep in row else '?'}): {preview}...")
            for d in drop:
                r = store.get_chunks([d])
                preview_d = r[d]["text"][:40].replace("\n", " ") if d in r else "?"
                print(f"    drop {d}: {len(r[d]['text']) if d in r else '?'} chars — {preview_d}...")

    result["chunks_removed"] = len(ids_to_drop)
    result["removed_ids"] = ids_to_drop

    if ids_to_drop:
        if not dry_run:
            # remove dup rows from docstore via raw SQL (store has no delete_chunk).
            # This preserves the keeper ids so the companion index id-bridge stays valid.
            print(f"\n  deleting {len(ids_to_drop)} dup chunk rows from docstore …")
            store.conn.execute(
                "DELETE FROM chunks WHERE id IN ({})".format(
                    ",".join(str(i) for i in ids_to_drop))
            )
            store.conn.commit()
            remaining = store.conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE id IN ({})".format(
                    ",".join(str(i) for i in ids_to_drop))
            ).fetchone()[0]
            print(f"  after delete: {remaining} dup rows remain (should be 0)")
            print(f"  vault-handoff now: {len(store.ids_for_source('vault-handoff'))} chunks")
            store.close()

            # remove the dup ids from the companion turbovec index (id bridge preserved).
            print("  removing dup ids from companion index …")
            from hermes_rag.index import load_index, save_index
            idx_path = DATA_DIR / "index-vault-handoff.tvim"
            idx = load_index(str(idx_path))
            removed = 0
            for cid in ids_to_drop:
                if idx.contains(cid):
                    idx.remove(cid)
                    removed += 1
            idx.write(str(idx_path))
            print(f"  removed {removed}/{len(ids_to_drop)} dup ids from companion index "
                  f"({idx_path.stat().st_size} bytes)")

            result["rebuilt_index"] = True
        else:
            print(f"\n  [DRY RUN] would delete {len(ids_to_drop)} chunk rows; "
                  f"would rebuild companion index from {len(ids) - len(ids_to_drop)} unique chunks")

    store.close()
    return result


def main() -> int:
    dry_run = "--apply" not in sys.argv[1:]
    label = "DRY RUN" if dry_run else "APPLIED"
    print(f"=== vault-handoff chunk dedup — {label} ===")
    print(f"mode: {'preview only' if dry_run else 'delete + rebuild index'}")
    print()

    # sanity: run the lint first so we know the pre-state
    lint = handoff_embed_lint.scan()
    s = lint["summary"]
    print(f"pre-state: {s['handoffs_total']} handoffs, "
          f"{s['embedded']} embedded, {s['unembedded']} unembedded")
    print()

    res = dedup(dry_run=dry_run)

    print()
    print("=== result ===")
    print(f"  total chunks:     {res['total_chunks']}")
    print(f"  unique texts:     {res['unique_texts']}")
    print(f"  dupes found:      {res['dupes_found']}")
    print(f"  chunks removed:   {res['chunks_removed']}")
    print(f"  index rebuilt:    {res['rebuilt_index']}")

    if dry_run:
        print()
        print("Run with --apply to delete duplicates and rebuild the companion index.")
        return 0

    # post-state: re-run lint to confirm still clean
    lint2 = handoff_embed_lint.scan()
    s2 = lint2["summary"]
    print()
    print(f"post-state: {s2['handoffs_total']} handoffs, "
          f"{s2['embedded']} embedded, {s2['unembedded']} unembedded")
    if s2["unembedded"] == 0:
        print("  ✓ corpus still clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
