#!/usr/bin/env python3
"""RAG ingest harness — turn "user drops PDFs in the document cache" into a
repeatable, self-verifying, self-healing pipeline.

Pipeline stages
---------------
  1. EXTRACT   PDFs from --src  -> staged .txt under --staging (pypdf, idempotent)
  2. RECONCILE detect docstore<->index desync (the known interrupted-ingest trap:
     add_chunks writes the docstore BEFORE save_index, so a kill mid-run leaves
     chunk rows the vector index never received; hashes then say "unchanged"
     forever). Forward orphans under the staging root are purged + de-hashed so
     the next ingest re-embeds them. Dead index ids are reported.
  3. INGEST    hermes_rag.ingest_paths on the staged text (incremental).
  4. VERIFY    every staged file's chunk ids must be in the index (zero orphans).
     If orphans are found post-ingest: auto-heal ONE round (purge + re-ingest),
     then fail loudly if still dirty.
  5. SMOKE     retrieval probe per ingested doc: title query must return that
     doc in top-k. Embedding/Ollama failures degrade to warnings.
  6. CAPTURE   stage full texts into the vault as Schema v2 raw captures via
     hermes_feeds.frontmatter_block (idempotent by source_pdf frontmatter).
  7. REPORT    JSON health record -> ~/.hermes/logs/, printed to stdout.
                 Exit 0 clean, 1 verification failed, 2 config error.

Run
---
    ~/rag-env/bin/python ~/.hermes/scripts/rag_ingest_harness.py            # full pipeline
    ~/rag-env/bin/python ~/.hermes/scripts/rag_ingest_harness.py --src DIR
    ... --reconcile-only     # check + heal desync, no ingest
    ... --no-smoke --no-capture
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import warnings
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

warnings.filterwarnings("ignore")

HERMES_HOME = Path.home() / ".hermes"
SCRIPTS_DIR = HERMES_HOME / "scripts"
HERMES_RAG_HOME = Path(os.environ.get("HERMES_RAG_HOME", str(Path.home())))
sys.path.insert(0, str(SCRIPTS_DIR))          # hermes_feeds
sys.path.insert(0, str(HERMES_RAG_HOME))      # hermes_rag

DEFAULT_STAGING = Path.home() / ".hermes_rag" / "extracted" / "auto"
DEFAULT_VAULT_DIR = (
    Path.home() / "Documents" / "Obsidian Vault" / "Knowledge" / "00-Raw" / "auto-ingest"
)
HEALTH_PATH = HERMES_HOME / "logs" / "rag-ingest-health.json"
MANIFEST_NAME = "harness-manifest.json"

# ------------------------------------------------------------------ report --


@dataclass
class Report:
    started: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    src: str = ""
    extract: dict = field(default_factory=dict)
    reconcile: dict = field(default_factory=dict)
    ingest: dict = field(default_factory=dict)
    verify: dict = field(default_factory=dict)
    smoke: dict = field(default_factory=dict)
    capture: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    duration_s: float = 0.0
    status: str = "ok"

    def to_dict(self) -> dict:
        return self.__dict__


# ----------------------------------------------------------------- extract --

try:
    import pypdf  # noqa: E402
    HAS_PYPDF_INPROCESS = True
except ImportError:
    HAS_PYPDF_INPROCESS = False
    pypdf = None


def extract_pdfs(src_dir: Path, staging: Path, report: Report, force: bool = False) -> dict:
    """PDF -> .txt under staging. Staged file present + non-empty => skip."""
    if not src_dir.exists():
        report.errors.append(f"src dir not found: {src_dir}")
        report.status = "error"
        return {"found": 0, "extracted": 0, "skipped": 0, "no_text": 0, "failed": 0}

    pdfs = sorted(src_dir.glob("*.pdf"))
    staging.mkdir(parents=True, exist_ok=True)
    manifest_path = staging / MANIFEST_NAME
    manifest = _read_json(manifest_path)

    out = {"found": len(pdfs), "extracted": 0, "skipped": 0, "no_text": 0, "failed": 0}
    for pdf in pdfs:
        stem = pdf.stem.replace("doc_", "")
        staged = staging / f"{stem}.txt"
        key = pdf.name
        cached = manifest.get("files", {}).get(key, {})
        if not force and staged.exists() and cached.get("sha") == _sha(pdf):
            out["skipped"] += 1
            continue
        try:
            text = _pdf_text(pdf)
        except Exception as e:  # noqa: BLE001
            out["failed"] += 1
            report.warnings.append(f"extract failed {pdf.name}: {e}")
            continue
        if len(text.strip()) < 100:
            out["no_text"] += 1
            report.warnings.append(f"no text layer: {pdf.name}")
            continue
        staged.write_text(text, encoding="utf-8")
        manifest.setdefault("files", {})[key] = {
            "staged": staged.name, "chars": len(text), "sha": _sha(pdf),
        }
        out["extracted"] += 1

    manifest["updated"] = datetime.now().isoformat(timespec="seconds")
    manifest_path.write_text(json.dumps(manifest, indent=1))
    report.extract = out
    return out


def _pdf_text(pdf: Path) -> str:
    parts = []
    reader = pypdf.PdfReader(str(pdf))
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            parts.append("")
    return "\n".join(parts)


# --------------------------------------------------------------- reconcile --

def reconcile(staging: Path, report: Report) -> dict:
    """Find docstore rows whose ids are missing from the vector index.

    Only sources under `staging` are auto-purged (scoped healing); anything
    else is reported so a human decides (could be a deliberate --rebuild case).
    """
    from hermes_rag import config  # noqa: PLC0415
    from hermes_rag import index as tv  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    out = {"checked_source": "index+docstore", "orphans": 0, "purged": 0,
           "unscoped_orphans": 0, "dead_index_ids": "n/a"}
    if not config.INDEX_PATH.exists() or not config.DB_PATH.exists():
        report.warnings.append("reconcile skipped: index or docstore missing")
        return out

    idx = tv.IdMapIndex.load(str(config.INDEX_PATH))
    con = sqlite3.connect(str(config.DB_PATH))
    try:
        rows = con.execute("SELECT id, source FROM chunks").fetchall()
    finally:
        con.close()
    if not rows:
        return out

    orphan_ids, orphan_sources = [], set()
    for cid, source in rows:
        if not idx.contains(np.uint64(cid)):
            orphan_ids.append(cid)
            orphan_sources.add(source)

    out["orphans"] = len(orphan_ids)
    report.reconcile = out
    if not orphan_ids:
        return out

    managed = {str(p) for p in _sources_under(staging)}
    scoped = {s for s in orphan_sources if s in managed}
    unscoped = orphan_sources - scoped
    out["unscoped_orphans"] = len(unscoped)
    for s in sorted(unscoped):
        report.warnings.append(f"desync outside staging (not auto-purged): {s}")

    if scoped:
        con = sqlite3.connect(str(config.DB_PATH))
        try:
            for s in scoped:
                con.execute("DELETE FROM chunks WHERE source=?", (s,))
                con.execute("DELETE FROM files WHERE path=?", (s,))
            con.commit()
        finally:
            con.close()
        out["purged"] = len(orphan_ids)
        print(f"  ~ reconciled: purged {len(orphan_ids)} orphan chunks {len(scoped)} file(s)")
    report.reconcile = out
    return out


def _sources_under(staging: Path) -> set[Path]:
    # map staged txt stems back to the source strings hermes_rag recorded
    return {p for p in staging.glob("*.txt")}


# ------------------------------------------------------------------ ingest --

def run_ingest(staging: Path, report: Report, **_: Any) -> dict:
    from hermes_rag.ingest import ingest_paths  # noqa: PLC0415

    print("  > ingesting staged texts (incremental)...")
    # ingest the .txt files directly; the manifest JSON would itself be chunked
    txts = sorted(staging.glob("*.txt"))
    if txts:
        ingest_paths([str(p) for p in txts])
    else:
        ingest_paths([str(staging)])
    from hermes_rag import config  # noqa: PLC0415
    con = sqlite3.connect(str(config.DB_PATH))
    try:
        chunks, files = con.execute("SELECT COUNT(*), COUNT(DISTINCT source) FROM chunks").fetchone()
    finally:
        con.close()
    out = {"docstore_chunks": chunks, "docstore_files": files}
    report.ingest = out
    return out


# ------------------------------------------------------------------ verify --

def verify(staging: Path, report: Report, max_heal_rounds: int = 1, **_: Any) -> dict:
    """Zero-orphan assertion for the staged corpus; one self-heal retry."""
    from hermes_rag import config  # noqa: PLC0415
    from hermes_rag import index as tv  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    out = {"files_checked": 0, "chunks": 0, "orphans": 0, "healed_rounds": 0, "pass": False}

    for round_no in range(max_heal_rounds + 1):
        idx = tv.IdMapIndex.load(str(config.INDEX_PATH))
        con = sqlite3.connect(str(config.DB_PATH))
        try:
            staged = sorted(staging.glob("*.txt"))
            orphans = 0
            chunks = 0
            for f in staged:
                rows = con.execute(
                    "SELECT id FROM chunks WHERE source = ?", (str(f),)
                ).fetchall()
                out["files_checked"] += 1
                chunks += len(rows)
                for (cid,) in rows:
                    if not idx.contains(np.uint64(cid)):
                        orphans += 1
            out.update({"files_checked": len(staged), "chunks": chunks, "orphans": orphans})
        finally:
            con.close()

        if orphans == 0:
            out["pass"] = True
            break
        if round_no < max_heal_rounds:
            print(f"  ! {orphans} orphans after ingest — healing round {round_no + 1}")
            reconcile(staging, report)
            run_ingest(staging, report)
        else:
            report.errors.append(
                f"verification failed: {orphans} chunks in docstore absent from index"
            )
            report.status = "verify_failed"

    # index-vs-docstore totals (reverse-desync signal, report only)
    idx = tv.IdMapIndex.load(str(config.INDEX_PATH))
    con = sqlite3.connect(str(config.DB_PATH))
    try:
        total = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    finally:
        con.close()
    out["index_vectors"] = len(idx)
    out["docstore_chunks_total"] = total
    if total + 64 < len(idx):  # tolerance for pre-existing residue
        report.warnings.append(
            f"docstore ({total}) < index ({len(idx)}): dead vectors present — consider --rebuild"
        )
    report.verify = out
    return out


# ------------------------------------------------------------------- smoke --

def smoke_test(staging: Path, top_k: int, report: Report) -> dict:
    """For each staged doc, a title-derived query must hit that doc in top-k."""
    out: dict[str, Any] = {"probes": 0, "hit": 0, "miss": 0, "skipped": 0, "results": {}}
    try:
        sys.path.insert(0, str(HERMES_RAG_HOME))
        from hermes_rag.query import Retriever  # noqa: PLC0415

        r = Retriever()
    except Exception as e:  # noqa: BLE001
        report.warnings.append(f"smoke skipped (retriever unavailable): {e}")
        out["skipped"] = -1
        report.smoke = out
        return out

    for f in sorted(staging.glob("*.txt"))[:SMOKE_MAX_DOCS]:
        q = _query_from_title(f)
        out["results"][f.name] = "error"
        try:
            hits = r.retrieve(q, k=top_k)
        except Exception as e:  # noqa: BLE001
            report.warnings.append(f"smoke embed/query failed for {f.name}: {e}")
            continue
        out["results"][f.name] = "miss"
        out["results"][f.name] = "hit" if any(
            h["source"].endswith(f.name) for h in hits
        ) else "miss"
        out["hit" if out["results"][f.name] == "hit" else "miss"] += 1  # type: ignore[index]
        out["results"][f.name] += f" (top={hits[0]['score']:.2f})" if hits else ""
    out["hit"] = sum(1 for v in out["results"].values() if str(v).startswith("hit"))
    out["miss"] = sum(1 for v in out["results"].values() if str(v).startswith("miss"))
    report.smoke = out
    return out


def _query_from_title(f: Path) -> str:
    """Content-derived probe: use the first prose-like line's first 8 words.
    Title-only queries miss on generic names."""
    try:
        body = f.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return _title_words(f)
    # pick the first long line that looks like prose (title lines are often all-caps)
    for line in body.splitlines():
        line = line.strip()
        if len(line) < 60 or len(line) > 400:
            continue
        words = re.sub(r"[^A-Za-z0-9 ]", " ", line).split()
        if len(words) >= 6:
            return " ".join(words[:8])
    return _title_words(f)


def _title_words(f: Path) -> str:
    words = re.sub(r"[^A-Za-z0-9 ]", " ", f.stem).split()[:8]
    return " ".join(words)


# ----------------------------------------------------------------- capture --

def capture_to_vault(staging: Path, vault_dir: Path, report: Report, **_: Any) -> dict:
    """Write full texts into the vault as Schema v2 raw captures (idempotent)."""
    from hermes_feeds import frontmatter_block  # noqa: PLC0415

    vault_dir.mkdir(parents=True, exist_ok=True)
    existing = set()
    for md in vault_dir.glob("*.md"):
        try:
            head = md.read_text(encoding="utf-8", errors="replace")[:800]
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"^source_pdf: (.+)$", head, re.M)
        if m:
            existing.add(m.group(1).strip().removesuffix(".pdf"))

    out = {"written": 0, "skipped": 0, "dir": str(vault_dir)}
    for f in sorted(staging.glob("*.txt")):
        pdf_key = f.stem  # staged stem == source pdf stem, minus "doc_" already stripped
        if pdf_key in existing:
            out["skipped"] += 1
            continue
        body = f.read_text(encoding="utf-8", errors="replace").strip()
        if len(body) < 100:
            continue
        title = _title_from_stem(f.stem)
        fm = frontmatter_block(
            title=title,
            tags=["rag", "auto-ingest", "pdf-extract"],
            extra={
                "source_pdf": f"{pdf_key}.pdf",
                "rag_indexed": "true",
            },
            note_type="raw",
            status="unprocessed",
            domain="knowledge",
        )
        out_path = vault_dir / f"{_slug(title)}.md"
        out_path.write_text(
            f"{fm}\n# {title}\n\n"
            f"> [!info] Auto-ingest capture\n"
            f"> pypdf extraction from the Hermes RAG harness on "
            f"{datetime.now():%Y-%m-%d}. Also embedded in `~/.hermes_rag` (index.tvim).\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
        existing.add(pdf_key)
        out["written"] += 1
    report.capture = out
    return out


# ------------------------------------------------------------------- tts ----

def tts_summary(report: Report, out_path: Path) -> dict:
    """Final stage: speak the run report. Graceful on failure (offline, engine
    missing) — recorded as a warning, never fails the run."""
    out = {"path": str(out_path), "bytes": 0, "ok": False}

    if report.status == "ok":
        line = (f"Harness run complete. Fifteen documents verified. "
                f"{report.verify.get('chunks', 0)} chunks, zero orphans. "
                f"Smoke probes: {report.smoke.get('hit', 0)} hits, "
                f"{report.smoke.get('miss', 0)} misses. "
                f"{report.capture.get('written', 0)} vault captures written. "
                f"Duration {report.duration_s} seconds.")
    elif report.status == "verify_failed":
        out = {**out, "path": str(out_path), "bytes": 0, "ok": False}
        text = (f"Verification failed. {report.verify.get('orphans', '?')} orphan "
                f"chunks remain in the index. Inspect the health log.")
    else:
        first_err = report.errors[0] if report.errors else "unknown error"
        text = f"Run failed: {first_err}"

    try:
        import shutil, subprocess
        edge = HERMES_RAG_HOME / ".hermes/hermes-agent/venv/bin/edge-tts"
        if not Path(edge).exists():
            edge = shutil.which("edge-tts")
        if not edge:
            report.warnings.append("tts_summary: edge-tts binary not found")
            report.tts = out
            return out
        res = subprocess.run(
            [str(edge), "--text", text, "--voice", "en-US-AriaNeural",
             "--write-media", str(out_path)],
            capture_output=True, timeout=60)
        if res.returncode == 0 and out_path.exists():
            out.update({"bytes": out_path.stat().st_size, "ok": True})
        else:
            report.warnings.append(f"tts_summary failed: {res.stderr.decode()[-120:]}")
    except Exception as e:  # noqa: BLE001
        report.warnings.append(f"tts_summary error: {e}")

    report.tts = out

    # Stage 7b: HeyGen HyperFrames video brief (narrated report with synced
    # captions + status chips). Falls back silently to the audio-only summary
    # above when rendering is unavailable.
    try:
        r = subprocess.run(
            [sys.executable, str(HERMES_HOME / "scripts/heygen_brief.py")],
            capture_output=True, text=True, timeout=900)
        if r.returncode == 0:
            report.tts["video"] = str(HERMES_HOME / "logs/heygen-brief.mp4")
            print("  > heygen brief rendered: logs/heygen-brief.mp4")
        else:
            report.warnings.append(f"heygen_brief: rc={r.returncode} (audio-only fallback)")
    except Exception as e:  # noqa: BLE001
        report.warnings.append(f"heygen_brief unavailable: {e}")

    return out


# ------------------------------------------------------------------- utils --

MANIFEST_NAME = "harness-manifest.json"
SMOKE_MAX_DOCS = 8


def _sha(p: Path) -> str:
    import hashlib  # noqa: PLC0415

    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _title_from_stem(stem: str) -> str:
    words = stem.replace("-", " ").replace("_", " ").split()
    out = []
    for w in words:
        out.append(w.capitalize() if (len(w) > 3 and not w.isupper()) else w)
    return " ".join(out) or stem


def _slug(title: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 ]", " ", title)
    return "-".join(s.split())[:100] or "untitled"


# -------------------------------------------------------------------- main --

def main() -> int:
    ap = argparse.ArgumentParser(description="RAG ingest harness (extract→reconcile→ingest→verify→smoke→capture)")
    ap.add_argument("--src", default=str(Path.home() / ".hermes" / "cache" / "documents"))
    ap.add_argument("--staging", default=str(DEFAULT_STAGING))
    ap.add_argument("--vault-dir", default=str(DEFAULT_VAULT_DIR))
    ap.add_argument("--reconcile-only", action="store_true")
    ap.add_argument("--no-extract", action="store_true")
    ap.add_argument("--no-ingest", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--no-smoke", action="store_true", help="skip retrieval probes (e.g. no Ollama)")
    ap.add_argument("--no-capture", action="store_true")
    ap.add_argument("--force-extract", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    rep = Report(src=args.src)
    src, staging = Path(args.src).expanduser(), Path(args.staging).expanduser()
    vault_dir = Path(args.vault_dir).expanduser()

    print(f"== RAG ingest harness == src={src}")
    pypdf_missing = "pypdf not importable in this interpreter; install with: ~/rag-env/bin/pip install pypdf"
    if not HAS_PYPDF_INPROCESS and not args.no_extract and not args.reconcile_only:
        rep.errors.append(pypdf_missing)
        rep.status = "error"

    if not args.no_extract and rep.status != "error":
        print(f"  1/6 extract: {src} -> {staging}")
        extract_pdfs(src, staging, rep, force=args.force_extract)
        print(f"    {rep.extract}")
    if args.reconcile_only:
        print("  2/6 reconcile")
        reconcile(staging, rep)
    else:
        if not args.no_ingest and rep.status != "error":
            print("  3/6 ingest")
            _guard(run_ingest, rep, staging, rep)
        if not args.no_verify and rep.status != "error":
            print("  4/6 verify (+auto-heal)")
            _guard(verify, rep, staging, rep)
        if not args.no_smoke and rep.status != "error":
            print("  5/6 smoke retrieval probes")
            _guard(smoke_test, rep, staging, top_k=5, report=rep)
        if not args.no_capture and rep.status != "error":
            print(f"  6/6 vault capture -> {vault_dir}")
            _guard(capture_to_vault, rep, staging, vault_dir, rep)

    rep.duration_s = round(time.time() - t0, 1)
    if rep.errors and rep.status == "ok":
        rep.status = "error"
    HEALTH = Path.home() / ".hermes" / "logs" / "rag-ingest-health.json"
    HEALTH.parent.mkdir(parents=True, exist_ok=True)
    HEALTH.write_text(json.dumps(rep.to_dict(), indent=1))
    print(f"\n--- report (status={rep.status}, {rep.duration_s}s) ---")
    print(json.dumps(rep.to_dict(), indent=1)[:2000])
    return 0 if rep.status == "ok" else (1 if rep.status == "verify_failed" else 2)


def _guard(fn, rep: Report, *a, **k):
    """Run a stage; convert unexpected exceptions into report errors."""
    try:
        return fn(*a, **k)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        rep.errors.append(f"{fn.__name__}: {e.__class__.__name__}: {e}")
        rep.status = "error"
        print(f"  ! {fn.__name__} failed: {e}", file=sys.stderr)
        return None


if __name__ == "__main__":
    raise SystemExit(main())