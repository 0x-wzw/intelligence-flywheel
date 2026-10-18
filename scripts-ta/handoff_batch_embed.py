#!/usr/bin/env python3
"""
batch_embed_handoffs.py — embed every unembedded vault handoff with ilmu.

Reads ILMU_API_KEY from ~/.hermes/.env, scans for handoffs missing
`embedding_status` in frontmatter, and embeds each one via
hermes_rag.vault_embed.embed_handoff(). Reports per-handoff outcome.
Non-fatal per the ratified optional-augmentation stance.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

# ── environment ────────────────────────────────────────────────────────────
_env_path = Path.home() / ".hermes" / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        if line.startswith("ILMU_API_KEY="):
            v = line.split("=", 1)[1].strip().strip("'").strip('"')
            if v and not v.startswith("#"):
                os.environ["ILMU_API_KEY"] = v
                break

os.environ.setdefault("HERMES_RAG_DATA", str(Path.home() / ".hermes_rag"))
os.environ.setdefault("HERMES_EMBED_DIM", "1024")
os.environ.setdefault("ILMU_EMBED_MODEL", "bge-m3")

# ── imports ────────────────────────────────────────────────────────────────
sys.path.insert(0, "/Users/admin")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import handoff_embed_lint  # noqa: E402
from hermes_rag.vault_embed import embed_handoff  # noqa: E402
from hermes_rag.store import DocStore  # noqa: E402

VAULT_ROOT = Path("/Users/admin/Documents/Obsidian Vault")


def _dedup(docstore, keep_ids: set[int], pending_drops: list[int]) -> None:
    """Broadword: for each handoff path with multiple matching-summary chunks,
    keep the highest-ID chunk and queue the rest for removal."""
    by_note: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for cid in docstore.ids_for_source("vault-handoff"):
        row = docstore.get_chunks([cid])
        if cid in row:
            by_note[row[cid]["source"]].append((cid, row[cid]["text"]))

    for note_path, chunks in by_note.items():
        seen: dict[str, list[int]] = defaultdict(list)  # text_hash -> [ids]
        for cid, txt in chunks:
            h = txt
            seen[h].append(cid)
        for h, ids in seen.items():
            if len(ids) > 1:
                ids.sort(reverse=True)
                keep = ids[0]
                keep_ids.add(keep)
                for dup in ids[1:]:
                    pending_drops.append(dup)
                    print(f"  ~ dup: {note_path} chunk={dup} -> keep={keep} (text {len(h)} chars)")


def main() -> int:
    # ── 2a. establish docstore handle + dedup bookkeeping ──────────────────
    _vh_store = DocStore()
    keep_ids: set[int] = set()
    pending_drops: list[int] = []

    _dedup(_vh_store, keep_ids, pending_drops)

    scan = handoff_embed_lint.scan()
    findings: list[str] = scan["findings"]
    summary: dict = scan["summary"]

    if not findings:
        print(f"handoffs: {summary['handoffs_total']} total, "
              f"{summary['embedded']} embedded, {summary['unembedded']} unembedded "
              f"({summary['unembedded_pct']}%)")
        print("No unembedded handoffs — nothing to do.")
        return 0

    print(f"handoffs: {summary['handoffs_total']} total, "
          f"{summary['embedded']} already embedded, "
          f"{summary['unembedded']} to embed ({summary['unembedded_pct']}%)")
    print()

    ok = 0
    fail = 0
    skip = 0

    for finding in findings:
        rel = finding.split(" :: ", 1)[0]
        note = VAULT_ROOT / rel

        if not note.exists():
            print(f"  ✗ {rel:<60s} file gone")
            fail += 1
            continue

        try:
            res = embed_handoff(note)
        except Exception as e:
            print(f"  ✗ {rel:<60s} exception: {e}")
            fail += 1
            continue

        outcome = res["outcome"]
        if outcome == "embedded":
            ms = res.get("ilmu_call_ms", "?")
            cid = res.get("chunk_id", "?")
            print(f"  ✓ {rel:<60s} chunk={cid} {ms}ms")
            ok += 1
        elif outcome == "already_embedded":
            print(f"  − {rel:<60s} already embedded")
            skip += 1
        elif outcome == "no_summary":
            print(f"  ✗ {rel:<60s} no extractable summary")
            fail += 1
        else:  # ilmu_failed, etc.
            detail = res.get("detail", "")[:50]
            print(f"  ✗ {rel:<60s} {outcome}: {detail}")
            fail += 1

    total = ok + skip + fail
    print()
    print(f"Result: {ok} embedded, {skip} skipped (already), "
          f"{fail} failed — of {total} unembedded handoffs.")

    # ── 5. commit dedup drops to docstore + companion index ─────────────────
    if pending_drops:
        print()
        print(f"Dropping {len(pending_drops)} duplicate chunk(s) "
              f"from docstore …")
        drops_done = 0
        drops_fail = 0
        for cid in pending_drops:
            try:
                _vh_store.delete_chunk(cid)
                drops_done += 1
            except Exception as e:
                _log.warning("dedup drop failed for chunk %d: %s", cid, e)
                drops_fail += 1
        _vh_store.close()
        if drops_fail:
            print(f"  dropped {drops_done}, FAILED {drops_fail} — "
                  f"companion index may be stale; rebuild manually.")
        else:
            print(f"  dropped {drops_done} — companion index rebuild pending "
                  f"(re-run with --rebuild-index, or run vault_embed rebuild).")
    else:
        _vh_store.close()
        print("No duplicate chunks to drop.")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
