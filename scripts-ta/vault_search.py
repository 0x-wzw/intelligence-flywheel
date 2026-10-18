#!/usr/bin/env python3
"""
Simple vault semantic search - index and search.
Save this, then run: python3 vault_search.py index
"""
import pickle, json, sys
from pathlib import Path
import numpy as np

VAULT = Path.home() / "Documents/Obsidian Vault"
INDEX = Path.home() / ".hermes/vault-index/vault_index.pkl"
META = Path.home() / ".hermes/vault_meta.json"

def index_reports():
    from sentence_transformers import SentenceTransformer
    
    # Get MUI report files only
    files = []
    for p in [VAULT / "01-Investment Research/MUI-Group/Annual-Reports",
              VAULT / "01-Investment Research/MUIPROP/Annual-Reports"]:
        if p.exists():
            files.extend(p.rglob("*.txt"))
    
    files = sorted(set(files))
    print(f"Indexing {len(files)} files...")
    
    model = SentenceTransformer("all-MiniLM-L6-v2")
    docs = []
    
    for i, f in enumerate(files):
        text = f.read_text(errors='ignore')
        # Split into paragraphs
        paras = [p.strip() for p in text.split('\n\n') if len(p.strip()) > 100]
        
        if paras:
            emb = model.encode(paras, show_progress_bar=False)
            for p, e in zip(paras, emb):
                docs.append({
                    'file': str(f.relative_to(VAULT)),
                    'text': p[:200],
                    'emb': e.astype(np.float32)
                })
        
        if (i+1) % 5 == 0:
            print(f"  {i+1}/{len(files)} done")
    
    INDEX.parent.mkdir(exist_ok=True)
    with open(INDEX, 'wb') as f:
        pickle.dump(docs, f)
    
    with open(META, 'w') as f:
        json.dump({'files': len(files), 'chunks': len(docs)}, f)
    
    print(f"✅ Indexed {len(docs)} chunks")

def search(query, top=5):
    from sentence_transformers import SentenceTransformer
    
    with open(INDEX, 'rb') as f:
        docs = pickle.load(f)
    
    model = SentenceTransformer("all-MiniLM-L6-v2")
    q_emb = model.encode([query])[0]
    
    # Compute similarities
    scores = []
    for d in docs:
        sim = np.dot(q_emb, d['emb']) / (np.linalg.norm(q_emb) * np.linalg.norm(d['emb']))
        scores.append((sim, d))
    
    scores.sort(reverse=True)
    
    # Deduplicate by file
    seen = set()
    results = []
    for sim, d in scores:
        if d['file'] not in seen:
            seen.add(d['file'])
            results.append((sim, d))
        if len(results) >= top:
            break
    
    for i, (sim, d) in enumerate(results, 1):
        print(f"\n{i}. [{sim*100:.1f}%] {d['file']}")
        print(f"   {d['text'][:150]}...")

if __name__ == "__main__":
    if sys.argv[1] == "index":
        index_reports()
    elif sys.argv[1] == "search":
        search(sys.argv[2])
    else:
        print("Usage: python3 vault_search.py index|search 'query'")
