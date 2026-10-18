#!/usr/bin/env python3
"""
VAULT RETRIEVER — Semantic search module for Hermes workflows
Importable module for automatic context retrieval.

Usage:
    from vault_retriever import retrieve_context
    context = retrieve_context("MUI revenue 2024", top_k=3)
"""

import json
import pickle
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import numpy as np

VAULT = Path.home() / "Documents/Obsidian Vault"
INDEX_DIR = Path.home() / ".hermes/vault-index"
INDEX_FILE = INDEX_DIR / "auto_index.pkl"
CHECK_FILE = INDEX_DIR / "auto_checkpoint.json"

# Always-include "read first" docs. Injected at the TOP of every retrieval,
# independent of similarity score or whether the index exists. This is what
# deterministically enforces "read System/Wiki first" for every agent/workflow
# that imports retrieve_context() — the model receives it whether or not it
# would have chosen to. Add/remove paths here to change the pinned set.
PINNED_DOCS = [
    VAULT / "System/Wiki/index.md",          # canonical wiki tier index
    VAULT / "Knowledge/Knowledge MOC.md",    # knowledge-lifecycle map
]
PIN_CHARS = 1200  # per-doc truncation to bound token cost


def _pinned_context() -> str:
    """Read the pinned docs straight from disk (no index needed) and format them
    as the priority block that leads every retrieval."""
    blocks = []
    for p in PINNED_DOCS:
        try:
            txt = p.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if not txt:
            continue
        if len(txt) > PIN_CHARS:
            txt = txt[:PIN_CHARS].rstrip() + " …"
        blocks.append(f"**PINNED — read first:** `{p.relative_to(VAULT)}`\n\n{txt}")
    if not blocks:
        return ""
    return "### Priority Context (always read first)\n\n" + "\n\n---\n\n".join(blocks)

# Ensure the scripts directory is in path for imports
sys.path.insert(0, str(Path(__file__).parent))

# The index is rebuilt daily by the `vault-semantic-index` cron job. Anything
# older than this means that job has been failing silently, so the index is
# treated as stale rather than trusted indefinitely.
MAX_INDEX_AGE_HOURS = 48


def index_age_hours() -> Optional[float]:
    """Age of the on-disk index in hours, or None if it doesn't exist."""
    if not INDEX_FILE.exists():
        return None
    mtime = datetime.fromtimestamp(INDEX_FILE.stat().st_mtime)
    return (datetime.now() - mtime).total_seconds() / 3600.0


def index_is_stale(max_age_hours: float = MAX_INDEX_AGE_HOURS) -> bool:
    """True when the index is missing or older than the rebuild schedule allows."""
    age = index_age_hours()
    return age is None or age > max_age_hours


def ensure_index(max_age_hours: float = MAX_INDEX_AGE_HOURS):
    """Ensure the index exists AND is fresh — rebuild if missing or stale.

    Checking only for existence let an existing-but-stale index be served
    forever, which is exactly how a broken rebuild cron went unnoticed.
    """
    age = index_age_hours()
    if age is None:
        print("⚠️  Building initial index...", file=sys.stderr)
    elif age > max_age_hours:
        print(
            f"⚠️  Index is {age:.1f}h old (max {max_age_hours:.0f}h) — rebuilding...",
            file=sys.stderr,
        )
    else:
        return
    import vault_search_auto
    vault_search_auto.build_index()

def retrieve_context(query: str, top_k: int = 3, threshold: float = 0.20) -> str:
    """Retrieve relevant context from vault for LLM consumption.

    Always LEADS with the pinned "read first" docs (wiki index + Knowledge MOC),
    then appends similarity hits. The pinned block is returned even when the index
    is missing or the query matches nothing — so every agent gets the wiki first.
    """
    pinned = _pinned_context()

    # Surface a stale index instead of silently serving old embeddings. Rebuilding
    # here would block the caller for minutes, so this warns and still answers —
    # call ensure_index() to actually refresh.
    age = index_age_hours()
    if age is not None and age > MAX_INDEX_AGE_HOURS:
        print(
            f"⚠️  vault index is {age:.1f}h old — results may be missing recent notes "
            f"(check the `vault-semantic-index` cron job)",
            file=sys.stderr,
        )

    sim_section = ""
    if INDEX_FILE.exists():
        try:
            from sentence_transformers import SentenceTransformer

            # Load model (cached)
            model = SentenceTransformer("all-MiniLM-L6-v2")

            with open(INDEX_FILE, 'rb') as f:
                docs = pickle.load(f)

            if docs:
                q_emb = model.encode(query, show_progress_bar=False)

                results = []
                for d in docs:
                    sim = float(np.dot(q_emb, d['emb']) / (np.linalg.norm(q_emb) * np.linalg.norm(d['emb'])))
                    if sim >= threshold:
                        results.append((sim, d))

                results.sort(key=lambda x: x[0], reverse=True)

                # Deduplicate by file
                seen = set()
                unique = []
                for sim, d in results:
                    if d['path'] not in seen:
                        seen.add(d['path'])
                        unique.append((sim, d))
                    if len(unique) >= top_k:
                        break

                if unique:
                    lines = ["### Retrieved Context", ""]
                    for i, (sim, d) in enumerate(unique, 1):
                        lines.append(f"**{i}.** [{sim*100:.0f}%] `{d['path']}`")
                        lines.append(f"> {d['text'][:200]}...")
                        lines.append("")
                    sim_section = '\n'.join(lines)
        except Exception:
            sim_section = ""

    parts = [s for s in (pinned, sim_section) if s]
    return '\n\n'.join(parts)

def search_files(query: str, top_k: int = 5) -> List[Dict]:
    """Search and return structured results."""
    if not INDEX_FILE.exists():
        return []
    
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        
        with open(INDEX_FILE, 'rb') as f:
            docs = pickle.load(f)
        
        q_emb = model.encode(query, show_progress_bar=False)
        
        results = []
        for d in docs:
            sim = float(np.dot(q_emb, d['emb']) / (np.linalg.norm(q_emb) * np.linalg.norm(d['emb'])))
            results.append({'score': sim, **d})
        
        results.sort(key=lambda x: x['score'], reverse=True)
        
        seen = set()
        unique = []
        for r in results:
            if r['path'] not in seen:
                seen.add(r['path'])
                unique.append(r)
            if len(unique) >= top_k:
                break
        
        return unique
    
    except Exception as e:
        return []

if __name__ == "__main__":
    if len(sys.argv) >= 2:
        print(retrieve_context(' '.join(sys.argv[1:])))
    else:
        print("Usage: python3 vault_retriever.py 'search query'")
