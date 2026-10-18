#!/usr/bin/env python3
import pickle, json
from pathlib import Path
import numpy as np
from sentence_transformers import SentenceTransformer

VAULT = Path.home() / "Documents/Obsidian Vault"
INDEX = Path.home() / ".hermes/vault-index/vault_index.pkl"
META = Path.home() / ".hermes/vault_meta.json"

def index():
    # Only TXT reports
    files = sorted((VAULT / "01-Investment Research/MUI-Group/Annual-Reports").rglob("*.txt"))
    files += sorted((VAULT / "01-Investment Research/MUIPROP/Annual-Reports").rglob("*.txt"))
    
    print(f"Files: {len(files)}")
    model = SentenceTransformer("all-MiniLM-L6-v2")
    
    docs = []
    for f in files:
        print(f"Processing {f.name}...")
        text = f.read_text(errors='ignore')
        paras = [p.strip() for p in text.split('\n\n') if len(p.strip()) > 100]
        
        for p in paras[:50]:  # Limit to first 50 paras for speed
            emb = model.encode(p, show_progress_bar=False)
            docs.append({
                'file': str(f.relative_to(VAULT)),
                'text': p[:200],
                'emb': emb.astype(np.float32)
            })
    
    INDEX.parent.mkdir(exist_ok=True)
    with open(INDEX, 'wb') as f:
        pickle.dump(docs, f)
    
    with open(META, 'w') as f:
        json.dump({'chunks': len(docs)}, f)
    
    print(f"Done! {len(docs)} chunks indexed")

def search(query):
    with open(INDEX, 'rb') as f:
        docs = pickle.load(f)
    
    model = SentenceTransformer("all-MiniLM-L6-v2")
    q = model.encode(query, show_progress_bar=False)
    
    results = []
    for d in docs:
        sim = float(np.dot(q, d['emb']) / (np.linalg.norm(q) * np.linalg.norm(d['emb'])))
        results.append((sim, d))
    
    results.sort(key=lambda x: x[0], reverse=True)
    
    seen = set()
    for sim, d in results:
        if d['file'] not in seen:
            seen.add(d['file'])
            print(f"\n[{sim*100:.0f}%] {d['file']}")
            print(f"  {d['text'][:120]}...")
        if len(seen) >= 5:
            break

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "index":
        index()
    elif len(sys.argv) > 2:
        search(sys.argv[2])
    else:
        print("Usage: python3 vs.py index")
        print("       python3 vs.py search 'query'")
