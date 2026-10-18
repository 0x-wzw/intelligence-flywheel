#!/usr/bin/env python3
"""
VAULT SEMANTIC SEARCH ENGINE — MUI-FOCUSED INDEX
Lightweight indexer targeting Investment Research and key docs.

Usage:
    python3 vault_semantic_search.py index
    python3 vault_semantic_search.py search "MUI revenue 2024"
    python3 vault_semantic_search.py stats
"""

import argparse
import json
import os
import pickle
import re
import sys
import time
from pathlib import Path
from typing import List, Dict, Tuple

import numpy as np

# ── Configuration ────────────────────────────────────────────────────

VAULT_PATH = Path.home() / "Documents/Obsidian Vault"
INDEX_DIR = Path.home() / ".hermes/vault-index"
INDEX_FILE = INDEX_DIR / "vault_index.pkl"
META_FILE = INDEX_DIR / "vault_meta.json"

EMBEDDING_MODEL = "all-MiniLM-L6-v2"
CHUNK_SIZE = 512
CHUNK_OVERLAP = 128

# ── Model Loading ──────────────────────────────────────────────────────

_model = None

def get_model():
    """Lazy-load sentence-transformers model."""
    global _model
    if _model is None:
        print(f"📥 Loading {EMBEDDING_MODEL}...")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(EMBEDDING_MODEL)
        print(f"✅ Model ready")
    return _model

# ── Text Processing ──────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Split text into overlapping chunks."""
    if len(text) <= chunk_size:
        return [text] if text.strip() else []
    
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            sentence_break = text.rfind('. ', start, end)
            if sentence_break > start + chunk_size // 2:
                end = sentence_break + 1
            else:
                word_break = text.rfind(' ', start, end)
                if word_break > start:
                    end = word_break
        
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
    
    return chunks

def clean_markdown(text: str) -> str:
    """Remove markdown formatting for cleaner embeddings."""
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL, count=1)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    text = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]+`', ' ', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[#*_~|]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

# ── Indexing ───────────────────────────────────────────────────────────

def get_indexable_files() -> List[Path]:
    """Get priority files from vault."""
    files = []
    
    # Priority directories
    for dir_name in ["01-Investment Research", "Inbox", "System"]:
        dir_path = VAULT_PATH / dir_name
        if dir_path.exists():
            for ext in ['*.md', '*.txt']:
                for f in dir_path.rglob(ext):
                    if any(part.startswith('.') for part in f.relative_to(VAULT_PATH).parts):
                        continue
                    if f.stat().st_size > 5 * 1024 * 1024:
                        continue
                    files.append(f)
    
    # Daily notes
    daily_dir = VAULT_PATH / "Daily"
    if daily_dir.exists():
        for f in daily_dir.glob("*.md"):
            files.append(f)
    
    return sorted(set(files))

def build_index():
    """Build or rebuild the semantic index."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    model = get_model()
    
    print(f"\n{'='*60}")
    print("🔍 VAULT SEMANTIC INDEXER")
    print(f"{'='*60}")
    
    files = get_indexable_files()
    print(f"Found {len(files)} files to index")
    
    documents = []
    file_meta = {}
    
    for i, file_path in enumerate(files):
        rel_path = str(file_path.relative_to(VAULT_PATH))
        
        if i % 100 == 0:
            print(f"  {i+1}/{len(files)}...")
        
        try:
            text = file_path.read_text(encoding='utf-8', errors='ignore')
            clean_text = clean_markdown(text)
            
            if not clean_text.strip():
                continue
            
            chunks = chunk_text(clean_text)
            if not chunks:
                continue
            
            # Encode in batches for speed
            embeddings = model.encode(chunks, show_progress_bar=False, batch_size=32)
            
            for chunk_idx, (chunk_text_item, embedding) in enumerate(zip(chunks, embeddings)):
                documents.append({
                    'path': rel_path,
                    'chunk_index': chunk_idx,
                    'text': chunk_text_item[:200],
                    'embedding': embedding.astype(np.float32),
                })
            
            file_meta[rel_path] = {
                'mtime': file_path.stat().st_mtime,
                'size': file_path.stat().st_size,
                'chunks': len(chunks),
            }
        
        except Exception as e:
            print(f"  ⚠️ Error in {rel_path}: {str(e)[:60]}")
            continue
    
    print(f"\n✅ Indexed {len(documents)} chunks from {len(file_meta)} files")
    
    # Save
    print(f"💾 Saving...")
    with open(INDEX_FILE, 'wb') as f:
        pickle.dump(documents, f)
    
    with open(META_FILE, 'w') as f:
        json.dump({
            'model': EMBEDDING_MODEL,
            'total_files': len(file_meta),
            'total_chunks': len(documents),
            'indexed_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }, f, indent=2)
    
    size_mb = INDEX_FILE.stat().st_size / (1024*1024) if INDEX_FILE.exists() else 0
    print(f"✅ Done ({size_mb:.1f} MB)")
    
    return len(documents)

def load_index() -> Tuple[List[Dict], Dict]:
    """Load index."""
    if not INDEX_FILE.exists():
        print("❌ No index. Run: python3 vault_semantic_search.py index")
        sys.exit(1)
    
    with open(INDEX_FILE, 'rb') as f:
        documents = pickle.load(f)
    
    with open(META_FILE, 'r') as f:
        meta = json.load(f)
    
    return documents, meta

# ── Search ─────────────────────────────────────────────────────────────

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

def search_vault(query: str, top_k: int = 10) -> List[Dict]:
    model = get_model()
    documents, meta = load_index()
    
    print(f"\n{'='*60}")
    print(f"🔍 \"{query}\"")
    print(f"{'='*60}")
    print(f"Index: {meta['total_files']} files, {meta['total_chunks']} chunks\n")
    
    query_embedding = model.encode([query])[0]
    
    results = []
    for doc in documents:
        sim = cosine_similarity(query_embedding, doc['embedding'])
        results.append({
            'path': doc['path'],
            'chunk_index': doc['chunk_index'],
            'text': doc['text'],
            'score': sim,
        })
    
    results.sort(key=lambda x: x['score'], reverse=True)
    
    # Deduplicate by file
    seen_files = set()
    unique_results = []
    for r in results:
        if r['path'] not in seen_files:
            seen_files.add(r['path'])
            unique_results.append(r)
        if len(unique_results) >= top_k:
            break
    
    return unique_results

def show_results(results: List[Dict]):
    if not results:
        print("❌ No results found")
        return
    
    for i, r in enumerate(results, 1):
        score = r['score'] * 100
        bar = '█' * int(score / 5) + '░' * max(0, 20 - int(score / 5))
        print(f"\n{i}. [{score:.1f}%] {bar}")
        print(f"   📄 {r['path']}")
        print(f"   💬 {r['text'][:150]}...")

# ── Stats ────────────────────────────────────────────────────────────

def show_stats():
    if not INDEX_FILE.exists():
        print("❌ No index found")
        return
    
    _, meta = load_index()
    print(f"\n{'='*60}")
    print("📊 INDEX STATISTICS")
    print(f"{'='*60}")
    print(f"Model: {meta['model']}")
    print(f"Files: {meta['total_files']:,}")
    print(f"Chunks: {meta['total_chunks']:,}")
    print(f"Indexed: {meta['indexed_at']}")
    if INDEX_FILE.exists():
        print(f"Size: {INDEX_FILE.stat().st_size / (1024*1024):.1f} MB")

# ── CLI ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Vault Semantic Search")
    parser.add_argument("command", choices=["index", "search", "stats"])
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    
    if args.command == "index":
        build_index()
    elif args.command == "search":
        if not args.query:
            print("❌ Provide a query")
            sys.exit(1)
        results = search_vault(args.query, top_k=args.top)
        show_results(results)
    elif args.command == "stats":
        show_stats()

if __name__ == "__main__":
    main()
