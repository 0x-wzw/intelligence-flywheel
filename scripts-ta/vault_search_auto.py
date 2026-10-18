#!/usr/bin/env python3
"""
HERMES INTEGRATED SEMANTIC SEARCH
- Fast indexing of key vault directories
- Auto-retrieval for Obliviarch + MEMORY.md workflows
- Cron-compatible for nightly updates

Usage:
    python3 vault_search_auto.py index       # Incremental update
    python3 vault_search_auto.py search "..."
    python3 vault_search_auto.py context "..."  # For Obliviarch integration
"""

import json
import pickle
import re
import sys
import time
from pathlib import Path
from datetime import datetime

import numpy as np

VAULT = Path.home() / "Documents/Obsidian Vault"
INDEX_DIR = Path.home() / ".hermes/vault-index"
INDEX_FILE = INDEX_DIR / "auto_index.pkl"
CHECK_FILE = INDEX_DIR / "auto_checkpoint.json"

# ── Fast Model Loading ─────────────────────────────────────────────────

_model = None

def load_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    return _model

# ── Text Processing ────────────────────────────────────────────────────

def clean(text):
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL, count=1)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    text = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]+`', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def sentence_chunks(text, max_chars=500, overlap_chars=100):
    """Split text into sentence-level chunks with overlap."""
    if len(text) <= max_chars:
        return [text] if text.strip() else []
    
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current = []
    current_len = 0
    
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        
        sent_len = len(sent)
        if current_len + sent_len > max_chars and current:
            # Save current chunk
            chunk_text = ' '.join(current)
            chunks.append(chunk_text)
            
            # Calculate overlap: keep sentences that fit in overlap window
            overlap = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) <= overlap_chars:
                    overlap.insert(0, s)
                    overlap_len += len(s) + 1
                else:
                    break
            current = overlap + [sent]
            current_len = sum(len(s) for s in current) + len(current) - 1
        else:
            current.append(sent)
            current_len += sent_len + 1
    
    if current:
        chunks.append(' '.join(current))
    
    return chunks

def extract_keywords(text):
    """Extract discriminative terms and domain phrases for hybrid search.

    Preserve hyphenated compounds and acronym tokens: treating ``co-living``,
    ``post-quantum``, ``F&B`` or ``CDO`` as generic fragments destroys category
    precision and makes semantically unrelated intelligence co-retrieve.
    """
    text = text.lower()
    phrases = re.findall(
        r"\b(?:ai/ml|f&b|qsr|cdo|cto|cio|agi|tcm|vc/pe|[a-z0-9]+(?:[-/][a-z0-9]+)+)\b",
        text,
    )
    words = re.findall(r'\b[a-z]{4,}\b', text)
    stop_words = {'this', 'that', 'with', 'from', 'they', 'have', 'been', 'were', 'said', 'each', 'which', 'their', 'time', 'would', 'there', 'could', 'other', 'after', 'first', 'never', 'these', 'those', 'being', 'over', 'also', 'back', 'only', 'know', 'take', 'year', 'good', 'some', 'come', 'make', 'well', 'work', 'life', 'even', 'more', 'very', 'when', 'much', 'here', 'than', 'them', 'into', 'just', 'like', 'look', 'down', 'most', 'long', 'find', 'give', 'does', 'made', 'part', 'such', 'keep', 'call', 'came', 'need', 'feel', 'seem', 'turn', 'hand', 'high', 'sure', 'upon', 'head', 'help', 'home', 'side', 'move', 'both', 'five', 'once', 'same', 'must', 'name', 'left', 'done', 'open', 'case', 'show', 'live', 'play', 'went', 'told', 'seen', 'soon', 'took', 'body', 'mind', 'land', 'line', 'kind', 'next', 'word', 'began', 'found', 'heard', 'shall', 'might', 'while', 'three', 'small', 'large', 'great', 'place', 'every', 'under', 'still', 'until', 'since', 'among', 'about', 'above', 'below', 'between', 'through', 'during', 'before', 'generated', 'trends', 'report', 'analysis', 'synthesis', 'implications'}
    ordered = []
    for token in phrases + words:
        if token not in stop_words and token not in ordered:
            ordered.append(token)
    return ordered[:30]

# ── File Discovery ────────────────────────────────────────────────────

# Directories never worth embedding. Everything else in the vault is indexed.
# This used to be an allow-list of six folders, which silently excluded 364 of
# 1026 notes (35%) — all of Work/ except AISED, all of Personal/, and handoffs/.
# The most valuable client work in the vault was unreachable by semantic search,
# and nothing reported the gap because the indexer only ever counted what it
# had been pointed at.
EXCLUDE_DIRS = {
    ".obsidian", ".trash", ".git", ".smart-env", "node_modules", ".obliviarch",
    "System/Backups",    # point-in-time copies; indexing them double-counts content
    "System/_generated", # machine-written reports; embedding them pollutes search
}


def _is_excluded(rel_parts) -> bool:
    if any(p.startswith(".") for p in rel_parts):
        return True
    for i in range(1, len(rel_parts)):
        if "/".join(rel_parts[:i]) in EXCLUDE_DIRS:
            return True
    return rel_parts[0] in EXCLUDE_DIRS


def get_files(checkpoint):
    """Get new/changed files since last index, across the whole vault."""
    files = []

    for f in VAULT.rglob("*"):
        if f.suffix not in {".md", ".txt"}:
            continue
        if not f.is_file():
            continue

        rel_path = f.relative_to(VAULT)
        if _is_excluded(rel_path.parts):
            continue
        if f.stat().st_size > 5 * 1024 * 1024:
            continue

        rel = str(rel_path)
        mtime = f.stat().st_mtime

        if rel not in checkpoint or checkpoint[rel] < mtime:
            files.append(f)

    return files

# ── Indexing ────────────────────────────────────────────────────────────

def build_index():
    INDEX_DIR.mkdir(exist_ok=True)
    
    # Load checkpoint
    checkpoint = {}
    if CHECK_FILE.exists():
        with open(CHECK_FILE) as f:
            checkpoint = json.load(f)
    
    # Load existing index
    docs = []
    if INDEX_FILE.exists():
        with open(INDEX_FILE, 'rb') as f:
            docs = pickle.load(f)
    
    # Drop anything that no longer exists on disk OR has moved into an
    # excluded directory. Only files being re-indexed had their old chunks
    # pruned (below), so a deleted or renamed note kept its chunks in the
    # index forever and went on surfacing in search results. The same thing
    # happened to files that were indexed before a directory was added to
    # EXCLUDE_DIRS — they kept their chunks even though get_files() would
    # never touch them again. Pruning by both checks (exists + not excluded)
    # catches both cases. Derived from the checkpoint + a fresh walk so it
    # can't disagree with get_files() about which paths are in scope.
    # Guard: if the vault root is missing (volume not mounted, path changed)
    # every path would look deleted and the whole index would be wiped.
    # Prune nothing.
    if not VAULT.is_dir():
        print(f"⚠️  Vault not found at {VAULT} — skipping prune to avoid wiping the index")
        removed = []
    else:
        removed = [
            rel for rel in checkpoint
            if not (VAULT / rel).exists()
            or _is_excluded(Path(rel).parts)
        ]
    if removed:
        gone = set(removed)
        docs = [d for d in docs if d['path'] not in gone]
        for rel in removed:
            del checkpoint[rel]
        print(f"🗑️  Dropped {len(removed)} deleted file(s) from the index")

    # Get files to index
    files = get_files(checkpoint)
    if not files:
        if removed:
            # Still persist the pruning — returning early here would silently
            # discard it and leave the deleted files' chunks on disk.
            with open(INDEX_FILE, 'wb') as f:
                pickle.dump(docs, f)
            with open(CHECK_FILE, 'w') as f:
                json.dump(checkpoint, f)
            print(f"✅ Indexed 0 files, {len(docs)} total chunks")
            return
        print("✅ Index is up to date")
        return

    print(f"📚 Indexing {len(files)} new/changed files...")
    
    model = load_model()
    indexed = 0
    total_chunks = 0
    
    for f in files:
        rel = str(f.relative_to(VAULT))
        
        try:
            text = f.read_text(errors='ignore')
            clean_text = clean(text)
            
            if not clean_text:
                checkpoint[rel] = f.stat().st_mtime
                continue
            
            chunks = sentence_chunks(clean_text)
            if not chunks:
                checkpoint[rel] = f.stat().st_mtime
                continue
            
            # Remove old chunks for this file
            docs = [d for d in docs if d['path'] != rel]
            
            # Encode and add new chunks
            embeddings = model.encode(chunks, show_progress_bar=False, batch_size=32)
            
            for chunk_text, emb in zip(chunks, embeddings):
                docs.append({
                    'path': rel,
                    'text': chunk_text[:250],
                    'emb': emb.astype(np.float32),
                    'keywords': extract_keywords(chunk_text),  # For hybrid search
                })
                total_chunks += 1
            
            checkpoint[rel] = f.stat().st_mtime
            indexed += 1
            
            if indexed % 50 == 0:
                print(f"  {indexed} files, {total_chunks} chunks...")
        
        except Exception as e:
            print(f"  ⚠️ {rel}: {e}")
            checkpoint[rel] = f.stat().st_mtime
    
    # Save
    with open(INDEX_FILE, 'wb') as f:
        pickle.dump(docs, f)
    
    with open(CHECK_FILE, 'w') as f:
        json.dump(checkpoint, f)
    
    print(f"✅ Indexed {indexed} files, {len(docs)} total chunks")

def search(query, top_k=5, threshold=0.20, hybrid_weight=0.3):
    """Search with hybrid semantic + keyword scoring."""
    if not INDEX_FILE.exists():
        return []
    
    model = load_model()
    
    with open(INDEX_FILE, 'rb') as f:
        docs = pickle.load(f)
    
    # Semantic encoding
    q_emb = model.encode(query, show_progress_bar=False)
    
    # Keyword extraction for hybrid boost
    query_keywords = set(extract_keywords(query))
    
    results = []
    for d in docs:
        # Semantic score
        sem_sim = float(np.dot(q_emb, d['emb']) / (np.linalg.norm(q_emb) * np.linalg.norm(d['emb'])))
        
        # Keyword score (BM25-like)
        kw_score = 0.0
        if 'keywords' in d and query_keywords:
            chunk_kw = set(d['keywords'])
            if chunk_kw:
                overlap = len(query_keywords & chunk_kw)
                kw_score = overlap / max(len(query_keywords), len(chunk_kw))
        
        # Combined score
        combined = (1 - hybrid_weight) * sem_sim + hybrid_weight * kw_score
        
        if combined >= threshold:
            results.append((combined, sem_sim, kw_score, d))
    
    results.sort(key=lambda x: x[0], reverse=True)
    
    # Deduplicate by file
    seen = set()
    unique = []
    for combined, sem_sim, kw_score, d in results:
        if d['path'] not in seen:
            seen.add(d['path'])
            unique.append({
                'score': combined,
                'semantic_score': sem_sim,
                'keyword_score': kw_score,
                **d
            })
        if len(unique) >= top_k:
            break
    
    return unique

def get_context(query, top_k=3):
    """Get formatted context for LLM integration."""
    results = search(query, top_k=top_k)
    if not results:
        return ""
    
    lines = ["### Relevant Vault Documents\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"**{i}.** [{r['score']*100:.0f}%] `{r['path']}`")
        lines.append(f"> {r['text'][:200]}...")
        lines.append("")
    
    return '\n'.join(lines)

# ── CLI ─────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 vault_search_auto.py index|search|context")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "index":
        build_index()
    elif cmd == "search" and len(sys.argv) >= 3:
        results = search(' '.join(sys.argv[2:]))
        for i, r in enumerate(results, 1):
            print(f"\n{i}. [{r['score']*100:.0f}%] {r['path']}")
            print(f"   {r['text'][:150]}...")
    elif cmd == "context" and len(sys.argv) >= 3:
        print(get_context(' '.join(sys.argv[2:])))
    else:
        print("Usage: python3 vault_search_auto.py index")
        print("       python3 vault_search_auto.py search 'query'")
        print("       python3 vault_search_auto.py context 'query'")

if __name__ == "__main__":
    main()
