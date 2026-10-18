#!/usr/bin/env python3
"""
MUI-FOCUSED SEMANTIC SEARCH
Indexes only Investment Research + key vault docs (fast).
"""

import json
import pickle
import re
import time
from pathlib import Path
from typing import List, Dict, Tuple

import numpy as np

VAULT = Path.home() / "Documents/Obsidian Vault"
INDEX_DIR = Path.home() / ".hermes/vault-index"
INDEX_DIR.mkdir(exist_ok=True)
INDEX_FILE = INDEX_DIR / "vault_index.pkl"
META_FILE = INDEX_DIR / "vault_meta.json"

def get_files():
    """Get only key files."""
    files = []
    
    # Investment Research only
    ir_dir = VAULT / "01-Investment Research"
    if ir_dir.exists():
        for ext in ['*.md', '*.txt']:
            files.extend(ir_dir.rglob(ext))
    
    # System context
    for f in (VAULT / "System/Assistant").glob("*.md"):
        files.append(f)
    for f in (VAULT / "System/Wiki").glob("*.md"):
        files.append(f)
    
    # Daily notes (last 30)
    daily_files = sorted((VAULT / "Daily").glob("*.md"), reverse=True)[:30]
    files.extend(daily_files)
    
    return sorted(set(files))

def clean(text: str) -> str:
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL, count=1)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)
    text = re.sub(r'```.*?```', ' ', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]+`', ' ', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[#*_~|]', '', text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def chunk(text: str, size=512, overlap=128):
    if len(text) <= size:
        return [text] if text.strip() else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            sb = text.rfind('. ', start, end)
            if sb > start + size // 2:
                end = sb + 1
            else:
                wb = text.rfind(' ', start, end)
                if wb > start:
                    end = wb
        c = text[start:end].strip()
        if c:
            chunks.append(c)
        start = end - overlap
    return chunks

def main():
    print("🔍 MUI-Focused Semantic Indexer")
    
    from sentence_transformers import SentenceTransformer
    print("📥 Loading model...")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    print("✅ Ready")
    
    files = get_files()
    print(f"Found {len(files)} files")
    
    docs = []
    meta = {}
    
    for i, f in enumerate(files):
        if i % 10 == 0:
            print(f"  {i+1}/{len(files)}: {f.name}")
        
        try:
            text = f.read_text(encoding='utf-8', errors='ignore')
            clean_text = clean(text)
            if not clean_text:
                continue
            
            chunks_list = chunk(clean_text)
            if not chunks_list:
                continue
            
            rel = str(f.relative_to(VAULT))
            embeddings = model.encode(chunks_list, show_progress_bar=False)
            
            for idx, (ct, emb) in enumerate(zip(chunks_list, embeddings)):
                docs.append({
                    'path': rel,
                    'chunk_index': idx,
                    'text': ct[:200],
                    'embedding': emb.astype(np.float32),
                })
            
            meta[rel] = {'chunks': len(chunks_list), 'size': f.stat().st_size}
        except Exception as e:
            print(f"    ⚠️ {e}")
    
    print(f"\n✅ Indexed {len(docs)} chunks from {len(meta)} files")
    
    with open(INDEX_FILE, 'wb') as f:
        pickle.dump(docs, f)
    
    with open(META_FILE, 'w') as f:
        json.dump({
            'files': len(meta),
            'chunks': len(docs),
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
        }, f, indent=2)
    
    size_mb = INDEX_FILE.stat().st_size / (1024*1024)
    print(f"💾 Saved ({size_mb:.1f} MB)")
    print(f"\nNext: python3 vault_semantic_search.py search \"MUI revenue\"")

if __name__ == "__main__":
    main()
