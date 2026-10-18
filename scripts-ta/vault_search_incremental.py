#!/usr/bin/env python3
"""
INCREMENTAL VAULT SEMANTIC SEARCH ENGINE
- Tracks file mtimes to only re-index changed files
- Integrates with Hermes workflow for automatic retrieval
- Supports full rebuilds and incremental updates

Usage:
    python3 vault_search_incremental.py index          # Incremental update
    python3 vault_search_incremental.py full         # Full rebuild
    python3 vault_search_incremental.py search "query"
    python3 vault_search_incremental.py retrieve "query" --limit 10
"""

import argparse
import hashlib
import json
import pickle
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

# ── Configuration ────────────────────────────────────────────────────

VAULT = Path.home() / "Documents/Obsidian Vault"
INDEX_DIR = Path.home() / ".hermes/vault-index"
INDEX_FILE = INDEX_DIR / "vault_index_v2.pkl"
META_FILE = INDEX_DIR / "vault_meta_v2.json"
CHECKPOINT_FILE = INDEX_DIR / "index_checkpoint.json"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"

# Directories to index (priority order)
INDEX_DIRS = [
    VAULT / "01-Investment Research",
    VAULT / "Inbox",
    VAULT / "System",
    VAULT / "Knowledge",
    VAULT / "Work/AISED",
    VAULT / "Daily",
]

# File extensions
INDEX_EXTS = {".md", ".txt"}

# ── Model ────────────────────────────────────────────────────────────

_model = None

def get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model

# ── Text Processing ──────────────────────────────────────────────────

def clean_markdown(text: str) -> str:
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL, count=1)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    text = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]+`', ' ', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[#*_~|]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def sentence_chunks(text: str, max_chars: int = 512) -> List[str]:
    """Split text into sentence-level chunks with overlap."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current = []
    current_len = 0
    
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        
        if current_len + len(sent) > max_chars and current:
            chunks.append(' '.join(current))
            # Keep overlap
            overlap = current[-2:] if len(current) >= 2 else current
            current = overlap + [sent]
            current_len = sum(len(s) for s in current)
        else:
            current.append(sent)
            current_len += len(sent)
    
    if current:
        chunks.append(' '.join(current))
    
    return chunks

# ── File Discovery ───────────────────────────────────────────────────

def discover_files(checkpoint: Dict) -> Tuple[List[Path], List[Path], List[Path]]:
    """
    Returns: (new_files, changed_files, deleted_files)
    """
    current_files = set()
    new_files = []
    changed_files = []
    
    for base_dir in INDEX_DIRS:
        if not base_dir.exists():
            continue
        for ext in INDEX_EXTS:
            for f in base_dir.rglob(f"*{ext}"):
                if any(part.startswith('.') for part in f.relative_to(VAULT).parts):
                    continue
                if f.stat().st_size > 5 * 1024 * 1024:
                    continue
                
                rel = str(f.relative_to(VAULT))
                current_files.add(rel)
                mtime = f.stat().st_mtime
                
                if rel not in checkpoint:
                    new_files.append(f)
                elif checkpoint[rel] < mtime:
                    changed_files.append(f)
    
    deleted_files = [Path(VAULT / rel) for rel in checkpoint if rel not in current_files]
    
    return new_files, changed_files, deleted_files

# ── Indexing ──────────────────────────────────────────────────────────

def build_index(full: bool = False) -> Dict:
    """Build or incrementally update the index."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    model = get_model()
    
    # Load existing checkpoint
    checkpoint = {}
    if CHECKPOINT_FILE.exists() and not full:
        with open(CHECKPOINT_FILE, 'r') as f:
            checkpoint = json.load(f)
    
    # Load existing index
    documents = []
    existing_paths = set()
    if INDEX_FILE.exists() and not full:
        with open(INDEX_FILE, 'rb') as f:
            documents = pickle.load(f)
        existing_paths = {d['path'] for d in documents}
    
    new_files, changed_files, deleted = discover_files(checkpoint)
    files_to_index = new_files + changed_files
    
    print(f"{'='*60}")
    print(f"🔍 INCREMENTAL INDEX")
    print(f"{'='*60}")
    print(f"Existing chunks: {len(documents)}")
    print(f"New files: {len(new_files)}")
    print(f"Changed files: {len(changed_files)}")
    print(f"Deleted files: {len(deleted)}")
    print()
    
    # Remove deleted/changed files from existing index
    if changed_files or deleted:
        changed_rels = {str(f.relative_to(VAULT)) for f in changed_files}
        deleted_rels = {str(f.relative_to(VAULT)) for f in deleted}
        remove_set = changed_rels | deleted_rels
        documents = [d for d in documents if d['path'] not in remove_set]
        for rel in remove_set:
            checkpoint.pop(rel, None)
        print(f"Removed {len(remove_set)} stale file(s)")
    
    # Index new/changed files
    indexed_count = 0
    for i, file_path in enumerate(files_to_index):
        rel = str(file_path.relative_to(VAULT))
        if i % 10 == 0:
            print(f"  {i+1}/{len(files_to_index)}: {rel[:50]}...")
        
        try:
            text = file_path.read_text(encoding='utf-8', errors='ignore')
            clean_text = clean_markdown(text)
            
            if not clean_text.strip():
                checkpoint[rel] = file_path.stat().st_mtime
                continue
            
            chunks = sentence_chunks(clean_text)
            if not chunks:
                checkpoint[rel] = file_path.stat().st_mtime
                continue
            
            embeddings = model.encode(chunks, show_progress_bar=False, batch_size=32)
            
            for idx, (chunk_text, emb) in enumerate(zip(chunks, embeddings)):
                documents.append({
                    'path': rel,
                    'chunk_index': idx,
                    'text': chunk_text[:300],
                    'embedding': emb.astype(np.float32),
                    'indexed_at': datetime.now().isoformat(),
                })
            
            checkpoint[rel] = file_path.stat().st_mtime
            indexed_count += 1
        
        except Exception as e:
            print(f"    ⚠️ Error: {str(e)[:60]}")
            checkpoint[rel] = file_path.stat().st_mtime  # Mark as processed anyway
    
    # Save
    print(f"\n💾 Saving {len(documents)} chunks...")
    with open(INDEX_FILE, 'wb') as f:
        pickle.dump(documents, f)
    
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(checkpoint, f, indent=2)
    
    meta = {
        'model': EMBEDDING_MODEL,
        'total_files': len(checkpoint),
        'total_chunks': len(documents),
        'indexed_at': datetime.now().isoformat(),
        'last_incremental': not full,
    }
    with open(META_FILE, 'w') as f:
        json.dump(meta, f, indent=2)
    
    size_mb = INDEX_FILE.stat().st_size / (1024*1024) if INDEX_FILE.exists() else 0
    print(f"✅ Done! {indexed_count} files indexed, {len(documents)} total chunks ({size_mb:.1f} MB)")
    
    return meta

# ── Search ────────────────────────────────────────────────────────────

def search(query: str, top_k: int = 10, threshold: float = 0.25) -> List[Dict]:
    """Search vault for semantically relevant chunks."""
    if not INDEX_FILE.exists():
        return []
    
    model = get_model()
    
    with open(INDEX_FILE, 'rb') as f:
        documents = pickle.load(f)
    
    q_emb = model.encode(query, show_progress_bar=False)
    
    results = []
    for doc in documents:
        sim = float(np.dot(q_emb, doc['embedding']) / 
                    (np.linalg.norm(q_emb) * np.linalg.norm(doc['embedding'])))
        if sim >= threshold:
            results.append({
                'score': sim,
                'path': doc['path'],
                'text': doc['text'],
            })
    
    # Sort by score
    results.sort(key=lambda x: x['score'], reverse=True)
    
    # Deduplicate by file
    seen = set()
    unique = []
    for r in results:
        if r['path'] not in seen:
            seen.add(r['path'])
            unique.append(r)
        if len(unique) >= top_k:
            break
    
    return unique

def retrieve_context(query: str, top_k: int = 5) -> str:
    """Retrieve context as formatted markdown for LLM consumption."""
    results = search(query, top_k=top_k)
    
    if not results:
        return ""
    
    lines = ["## Semantic Search Results", ""]
    
    for i, r in enumerate(results, 1):
        score = r['score'] * 100
        lines.append(f"### Result {i} ({score:.0f}% relevance)")
        lines.append(f"**Source:** `{r['path']}`")
        lines.append(f"> {r['text'][:300]}...")
        lines.append("")
    
    return '\n'.join(lines)

def print_results(results: List[Dict]):
    if not results:
        print("❌ No results found")
        return
    
    for i, r in enumerate(results, 1):
        score = r['score'] * 100
        bar = '█' * int(score / 5) + '░' * max(0, 20 - int(score / 5))
        print(f"\n{i}. [{score:.0f}%] {bar}")
        print(f"   📄 {r['path']}")
        print(f"   💬 {r['text'][:200]}...")

# ── Stats ────────────────────────────────────────────────────────────

def show_stats():
    if not META_FILE.exists():
        print("❌ No index found")
        return
    
    with open(META_FILE, 'r') as f:
        meta = json.load(f)
    
    with open(CHECKPOINT_FILE, 'r') as f:
        checkpoint = json.load(f)
    
    print(f"\n{'='*60}")
    print("📊 INDEX STATISTICS")
    print(f"{'='*60}")
    print(f"Model: {meta['model']}")
    print(f"Files: {meta['total_files']:,}")
    print(f"Chunks: {meta['total_chunks']:,}")
    print(f"Indexed: {meta['indexed_at']}")
    print(f"Incremental: {meta.get('last_incremental', False)}")
    if INDEX_FILE.exists():
        print(f"Size: {INDEX_FILE.stat().st_size / (1024*1024):.1f} MB")
    
    print(f"\n📁 Directories:")
    dir_counts = {}
    for path in checkpoint:
        dir_name = str(Path(path).parent)
        dir_counts[dir_name] = dir_counts.get(dir_name, 0) + 1
    
    for d, count in sorted(dir_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"   {d}: {count} files")

# ── Integration Helper ──────────────────────────────────────────────

def auto_retrieve(query: str) -> str:
    """For use by Hermes — automatically retrieve relevant context."""
    # Quick check if index exists
    if not INDEX_FILE.exists():
        return "[No semantic index available — run vault_search_incremental.py index]"
    
    return retrieve_context(query)

# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Incremental Vault Semantic Search")
    parser.add_argument("command", choices=["index", "full", "search", "retrieve", "stats"])
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--threshold", type=float, default=0.25)
    
    args = parser.parse_args()
    
    if args.command == "index":
        build_index(full=False)
    elif args.command == "full":
        build_index(full=True)
    elif args.command == "search":
        if not args.query:
            print("❌ Provide a query")
            sys.exit(1)
        results = search(args.query, top_k=args.top, threshold=args.threshold)
        print_results(results)
    elif args.command == "retrieve":
        if not args.query:
            print("❌ Provide a query")
            sys.exit(1)
        print(retrieve_context(args.query, top_k=args.top))
    elif args.command == "stats":
        show_stats()

if __name__ == "__main__":
    main()
