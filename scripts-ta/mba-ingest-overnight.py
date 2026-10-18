#!/usr/bin/env python3
"""Phase 2b — embed the staged MBA text overnight, in crash-safe batches.

Reads ONLY /Users/admin/.hermes_rag/extracted/ — never the vault. That is
deliberate: ~/Documents is TCC-protected and a detached process cannot be relied
on to inherit the grant, so the long run has no protected-path dependency.

Crash safety. `hermes_rag.ingest.ingest_paths` calls `store.record_file()` per
file but `save_index()` only once at the end. A crash mid-call therefore leaves the
docstore asserting a file is ingested while its vectors were never persisted — and
on the next run that file is skipped as "unchanged", so the gap is silent and
permanent. Two mitigations here:

  1. Ingest in small batches, so each batch persists the index before moving on.
  2. Record the in-flight batch. On restart, delete those sources from the docstore
     first so they are genuinely re-embedded rather than skipped.

Run detached:
    nohup /Users/admin/rag-env312/bin/python \\
        /Users/admin/.hermes/scripts/mba-ingest-overnight.py \\
        >> /Users/admin/.hermes/logs/mba-ingest.log 2>&1 &
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/Users/admin")

from hermes_rag import config                      # noqa: E402
from hermes_rag.index import load_index, remove_ids, save_index  # noqa: E402
from hermes_rag.ingest import ingest_paths          # noqa: E402
from hermes_rag.store import DocStore               # noqa: E402

MANIFEST = Path("/Users/admin/.hermes_rag/extracted/mba-manifest.json")
PROGRESS = Path("/Users/admin/.hermes_rag/extracted/mba-progress.json")
LOCK = Path("/Users/admin/.hermes_rag/extracted/mba-ingest.lock")
BATCH = 25          # ~25 files ≈ 340 chunks ≈ 6 min: bounds any loss to one batch


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}", flush=True)


def index_count() -> int:
    try:
        return len(load_index())
    except Exception:
        try:
            return load_index().ntotal
        except Exception:
            return -1


def main() -> int:
    if LOCK.exists():
        log(f"ABORT: lock present at {LOCK} — another run is active (or died; "
            f"delete the lock to override)")
        return 1
    LOCK.write_text(str(os.getpid()), encoding="utf-8")

    try:
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        files = [m["staged"] for m in man["files"]]
        log(f"manifest: {len(files)} staged files, priority-ordered "
            f"(own work first, generic readings last)")

        prog = {"done": 0, "in_flight": []}
        if PROGRESS.exists():
            prog = json.loads(PROGRESS.read_text(encoding="utf-8"))
            log(f"resuming: {prog['done']} files already completed")
            if prog.get("in_flight"):
                # Repair: these were recorded in the docstore but their vectors may
                # never have been saved. Drop them so they are truly re-embedded.
                store = DocStore()
                index = load_index()
                removed = 0
                for src in prog["in_flight"]:
                    ids = store.delete_source(src)
                    if ids:
                        remove_ids(index, ids)
                        removed += len(ids)
                save_index(index)
                store.close()
                log(f"repaired interrupted batch: dropped {removed} chunk(s) from "
                    f"{len(prog['in_flight'])} source(s) for clean re-ingest")
                prog["in_flight"] = []

        todo = files[prog["done"]:]
        log(f"to process: {len(todo)} files in batches of {BATCH}")
        t_start = time.time()
        chunks_at_start = DocStore().stats()["chunks"]

        for i in range(0, len(todo), BATCH):
            batch = todo[i:i + BATCH]
            PROGRESS.write_text(json.dumps({"done": prog["done"],
                                            "in_flight": batch}), encoding="utf-8")
            b_no = i // BATCH + 1
            total_b = (len(todo) + BATCH - 1) // BATCH
            t0 = time.time()
            try:
                ingest_paths(batch)
            except Exception as e:
                log(f"BATCH {b_no} FAILED: {type(e).__name__}: {e} — stopping so the "
                    f"in-flight marker survives for repair on restart")
                return 2

            st = DocStore(); s = st.stats(); st.close()
            idx = index_count()
            drift = "" if idx in (-1, s["chunks"]) else \
                    f"  ⚠️ DRIFT docstore={s['chunks']} index={idx}"
            prog["done"] += len(batch)
            PROGRESS.write_text(json.dumps({"done": prog["done"], "in_flight": []}),
                                encoding="utf-8")

            done_chunks = s["chunks"] - chunks_at_start
            el = time.time() - t_start
            rate = done_chunks / el if el else 0
            remaining_files = len(todo) - prog["done"]
            eta_h = (remaining_files / max(len(batch), 1)) * (time.time() - t0) / 3600
            log(f"batch {b_no}/{total_b} ok in {time.time()-t0:.0f}s | "
                f"files {prog['done']}/{len(files)} | chunks {s['chunks']} "
                f"(+{done_chunks} this run) | {rate:.2f} chunks/s | "
                f"ETA {eta_h:.1f}h{drift}")

        log(f"COMPLETE: {prog['done']} files, docstore now "
            f"{DocStore().stats()['chunks']} chunks, {(time.time()-t_start)/3600:.2f}h")
        return 0
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
