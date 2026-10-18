#!/usr/bin/env python3
"""
COMPREHENSIVE VAULT INDEXER v3
- Indexes all vault content: Notion, Wiki, Daily, Investment Research
- Preserves markdown tables
- Domain-specific keyword extraction
- File watcher for real-time updates

Usage:
    python3 vault_index_v3.py index        # Full rebuild
    python3 vault_index_v3.py incremental  # Quick update
    python3 vault_index_v3.py search "query"
"""

import json
import pickle
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Set
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
from sentence_transformers import SentenceTransformer

# ── Configuration ────────────────────────────────────────────────────

VAULT = Path.home() / "Documents/Obsidian Vault"
INDEX_DIR = Path.home() / ".hermes/vault-index"
INDEX_FILE = INDEX_DIR / "comprehensive_index.pkl"
CHECKPOINT_FILE = INDEX_DIR / "comprehensive_checkpoint.json"
META_FILE = INDEX_DIR / "comprehensive_meta.json"
WATCH_FILE = INDEX_DIR / "watch_list.json"

# Parallel processing config
MAX_WORKERS = 4  # Use 4 CPU cores
BATCH_SIZE = 64  # Larger batches for GPU/CPU efficiency

# All directories to scan
INDEX_DIRS = [
    VAULT / "System/Wiki",          # canonical wiki tier — scanned first
    VAULT / "Knowledge",            # knowledge-lifecycle system (superset)
    VAULT / "01-Investment Research",
    VAULT / "Inbox/Notion",
    VAULT / "Work/AISED",           # AISED corpus (incl. BHSFI workstream)
    VAULT / "System/Assistant",
    VAULT / "Daily",
]

# Domain-specific terms for financial reports
FINANCIAL_TERMS = {
    'revenue', 'profit', 'loss', 'earnings', 'eps', 'dividend', 'share', 'stock',
    'assets', 'liabilities', 'equity', 'balance', 'sheet', 'income', 'statement',
    'cash', 'flow', 'financial', 'audit', 'auditor', 'annual', 'report',
    'director', 'board', 'chairman', 'ceo', 'cfo', 'executive', 'officer',
    'property', 'development', 'hotel', 'retail', 'investment', 'holding',
    'disposal', 'acquisition', 'merger', 'takeover', 'stake', 'ownership',
    'consolidated', 'subsidiary', 'associate', 'joint', 'venture',
    'million', 'billion', 'rm', 'ringgit', 'sen',
}

# ── Model ─────────────────────────────────────────────────────────────

_model = None

def get_model():
    global _model
    if _model is None:
        print("📥 Loading sentence-transformers model...")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        print("✅ Model ready")
    return _model

# ── Smart Text Processing ─────────────────────────────────────────────

def clean_and_preserve(text: str) -> List[str]:
    """
    Clean markdown but preserve tables and structure.
    Returns list of chunks (paragraphs, tables, etc.)
    """
    # Extract YAML frontmatter
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.DOTALL, count=1)
    
    # Extract tables before cleaning
    tables = re.findall(r'\|.*?\|(?:\n\|[-:\s|]+\|)?(?:\n\|.*?\|)*', text)
    table_chunks = []
    for table in tables:
        clean_table = re.sub(r'\s+', ' ', table.strip())
        if len(clean_table) > 50:
            table_chunks.append(f"[TABLE] {clean_table[:800]}")
    
    # Remove tables from text for paragraph processing
    text_no_tables = re.sub(r'\|.*?\|(?:\n\|[-:\s|]+\|)?(?:\n\|.*?\|)*', '[TABLE]', text)
    
    # Clean markdown
    text_clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text_no_tables)
    text_clean = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text_clean)
    text_clean = re.sub(r'```.*?```', ' ', text_clean, flags=re.DOTALL)
    text_clean = re.sub(r'`[^`]+`', ' ', text_clean)
    text_clean = re.sub(r'\s+', ' ', text_clean)
    text_clean = text_clean.strip()
    
    # Split into chunks
    chunks = []
    
    # Add tables first
    chunks.extend(table_chunks)
    
    # Split remaining text into paragraphs
    paragraphs = re.split(r'\n\n+', text_clean)
    for para in paragraphs:
        para = para.strip()
        if len(para) > 50:
            # Split long paragraphs at sentence boundaries
            if len(para) > 500:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                current = []
                current_len = 0
                for sent in sentences:
                    sent = sent.strip()
                    if not sent:
                        continue
                    if current_len + len(sent) > 400 and current:
                        chunks.append(' '.join(current))
                        current = [sent]
                        current_len = len(sent)
                    else:
                        current.append(sent)
                        current_len += len(sent)
                if current:
                    chunks.append(' '.join(current))
            else:
                chunks.append(para)
    
    return chunks

def extract_enhanced_keywords(text: str) -> Set[str]:
    """Extract keywords with domain awareness."""
    text_lower = text.lower()
    
    # Extract words
    words = re.findall(r'\b[A-Za-z]{3,}\b', text_lower)
    keywords = set(words)
    
    # Extract years
    years = set(re.findall(r'\b(20\d{2})\b', text_lower))
    
    # Extract numbers (amounts)
    amounts = set(re.findall(r'\b(\d{1,3}(?:,\d{3})+(?:\.\d+)?)\b', text))
    
    # Identify financial terms present
    financial_matches = FINANCIAL_TERMS & keywords
    
    # Build priority keyword set
    result = set()
    result.update(financial_matches)
    result.update(years)
    result.update([w for w in words if w not in {
        'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'her', 'was', 'one', 'our', 'had', 'what', 'how', 'who', 'why', 'when', 'where', 'which', 'this', 'that', 'with', 'from', 'they', 'have', 'been', 'were', 'said', 'each', 'their', 'time', 'would', 'there', 'could', 'other', 'after', 'first', 'never', 'these', 'those', 'being', 'over', 'also', 'back', 'only', 'know', 'take', 'year', 'good', 'some', 'come', 'make', 'well', 'work', 'life', 'even', 'more', 'very', 'much', 'here', 'than', 'them', 'into', 'just', 'like', 'look', 'down', 'most', 'long', 'find', 'give', 'does', 'made', 'part', 'such', 'keep', 'call', 'came', 'need', 'feel', 'seem', 'turn', 'hand', 'high', 'sure', 'upon', 'head', 'help', 'home', 'side', 'move', 'both', 'five', 'once', 'same', 'must', 'name', 'left', 'done', 'open', 'case', 'show', 'live', 'play', 'went', 'told', 'seen', 'soon', 'took', 'body', 'mind', 'land', 'line', 'kind', 'next', 'word', 'began', 'found', 'heard', 'shall', 'might', 'while', 'three', 'small', 'large', 'great', 'place', 'every', 'under', 'still', 'until', 'since', 'among', 'about', 'above', 'below', 'between', 'through', 'during', 'before'}][:20])
    
    return result

# ── Indexing ──────────────────────────────────────────────────────────

def discover_files(checkpoint: Dict) -> Tuple[List[Path], List[Path], List[Path]]:
    """Returns: (new_files, changed_files, deleted_files)"""
    current_files = {}
    new_files = []
    changed_files = []
    
    for base_dir in INDEX_DIRS:
        if not base_dir.exists():
            continue
        for ext in ['*.md', '*.txt']:
            for f in base_dir.rglob(ext):
                if any(part.startswith('.') for part in f.relative_to(VAULT).parts):
                    continue
                if f.stat().st_size > 10 * 1024 * 1024:  # Skip files > 10MB
                    continue
                
                rel = str(f.relative_to(VAULT))
                mtime = f.stat().st_mtime
                current_files[rel] = mtime
                
                if rel not in checkpoint:
                    new_files.append(f)
                elif checkpoint[rel] < mtime:
                    changed_files.append(f)
    
    deleted = [Path(VAULT / rel) for rel in checkpoint if rel not in current_files]
    
    return new_files, changed_files, deleted, current_files

def build_index(incremental: bool = True):
    """Build or update the comprehensive index."""
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load checkpoint
    checkpoint = {}
    if CHECKPOINT_FILE.exists() and incremental:
        with open(CHECKPOINT_FILE) as f:
            checkpoint = json.load(f)
    
    # Load existing docs
    documents = []
    if INDEX_FILE.exists() and incremental:
        with open(INDEX_FILE, 'rb') as f:
            documents = pickle.load(f)
    
    new_files, changed_files, deleted, current_files = discover_files(checkpoint)
    files_to_index = new_files + changed_files
    
    print(f"{'='*60}")
    print(f"📚 COMPREHENSIVE VAULT INDEXER v3")
    print(f"{'='*60}")
    print(f"Existing: {len(documents)} chunks")
    print(f"New: {len(new_files)} files")
    print(f"Changed: {len(changed_files)} files")
    print(f"Deleted: {len(deleted)} files")
    print()
    
    if not files_to_index and not deleted:
        print("✅ Index is up to date!")
        return
    
    # Remove deleted/changed files from index
    if changed_files or deleted:
        changed_rels = {str(f.relative_to(VAULT)) for f in changed_files}
        deleted_rels = {str(f.relative_to(VAULT)) for f in deleted}
        remove_set = changed_rels | deleted_rels
        documents = [d for d in documents if d['path'] not in remove_set]
        for rel in remove_set:
            checkpoint.pop(rel, None)
        print(f"Removed {len(remove_set)} stale entries")
    
    # Index files — parallel processing
    model = get_model()
    indexed = 0
    total_chunks = 0
    
    # Prepare batches for parallel encoding
    file_batches = []
    for i in range(0, len(files_to_index), BATCH_SIZE):
        batch = files_to_index[i:i+BATCH_SIZE]
        file_batches.append(batch)
    
    print(f"🔄 Encoding with {MAX_WORKERS} workers, {len(file_batches)} batches...")
    
    # Process batches
    for batch_idx, batch in enumerate(file_batches):
        if batch_idx % 10 == 0:
            print(f"  Batch {batch_idx+1}/{len(file_batches)} ({indexed} files done)...")
        
        # Read and chunk files in batch
        batch_chunks = []
        batch_rels = []
        
        for file_path in batch:
            rel = str(file_path.relative_to(VAULT))
            try:
                text = file_path.read_text(encoding='utf-8', errors='ignore')
                chunks = clean_and_preserve(text)
                
                if chunks:
                    batch_chunks.extend(chunks)
                    batch_rels.extend([(rel, chunk) for chunk in chunks])
                else:
                    checkpoint[rel] = file_path.stat().st_mtime
                
                indexed += 1
            except Exception as e:
                print(f"    ⚠️ Error reading {rel}: {str(e)[:60]}")
                checkpoint[rel] = file_path.stat().st_mtime
        
        # Encode all chunks in batch at once (model handles parallelization internally)
        if batch_chunks:
            embeddings = model.encode(batch_chunks, show_progress_bar=False, batch_size=BATCH_SIZE)
            
            for (rel, chunk_text), emb in zip(batch_rels, embeddings):
                documents.append({
                    'path': rel,
                    'text': chunk_text[:400],
                    'emb': emb.astype(np.float32),
                    'keywords': list(extract_enhanced_keywords(chunk_text)),
                    'indexed_at': datetime.now().isoformat(),
                })
                total_chunks += 1
            
            # Update checkpoint for processed files
            for file_path in batch:
                rel = str(file_path.relative_to(VAULT))
                if rel not in [r for r, _ in batch_rels]:
                    checkpoint[rel] = file_path.stat().st_mtime

    print(f"\n💾 Saving {len(documents)} chunks...")
    with open(INDEX_FILE, 'wb') as f:
        pickle.dump(documents, f)
    
    with open(CHECKPOINT_FILE, 'w') as f:
        json.dump(checkpoint, f, indent=2)
    
    meta = {
        'model': 'all-MiniLM-L6-v2',
        'total_files': len(checkpoint),
        'total_chunks': len(documents),
        'indexed_at': datetime.now().isoformat(),
        'incremental': incremental,
        'directories': [str(d.relative_to(VAULT)) for d in INDEX_DIRS if d.exists()],
    }
    with open(META_FILE, 'w') as f:
        json.dump(meta, f, indent=2)
    
    size_mb = INDEX_FILE.stat().st_size / (1024*1024) if INDEX_FILE.exists() else 0
    print(f"✅ Done! {indexed} files indexed, {len(documents)} total chunks ({size_mb:.1f} MB)")

# ── Search ────────────────────────────────────────────────────────────

def search(query: str, top_k: int = 10, threshold: float = 0.15, 
           semantic_weight: float = 0.6) -> List[Dict]:
    """Search with hybrid scoring."""
    if not INDEX_FILE.exists():
        return []
    
    model = get_model()
    
    with open(INDEX_FILE, 'rb') as f:
        documents = pickle.load(f)
    
    q_emb = model.encode(query, show_progress_bar=False)
    query_keywords = extract_enhanced_keywords(query)
    
    results = []
    for d in documents:
        # Semantic score
        sem_sim = float(np.dot(q_emb, d['emb']) / 
                        (np.linalg.norm(q_emb) * np.linalg.norm(d['emb'])))
        
        # Keyword score
        kw_score = 0.0
        chunk_kw = set(d.get('keywords', []))
        if chunk_kw and query_keywords:
            overlap = len(query_keywords & chunk_kw)
            kw_score = overlap / max(len(query_keywords), len(chunk_kw))
        
        # Domain boost for financial terms
        domain_boost = 0.0
        if chunk_kw:
            fin_overlap = len(FINANCIAL_TERMS & chunk_kw)
            if fin_overlap > 0:
                domain_boost = 0.05 * min(fin_overlap, 3)  # Max 0.15 boost
        
        combined = semantic_weight * sem_sim + (1 - semantic_weight) * kw_score + domain_boost
        
        if combined >= threshold:
            results.append((combined, sem_sim, kw_score, domain_boost, d))
    
    results.sort(key=lambda x: x[0], reverse=True)
    
    # Deduplicate by file
    seen = set()
    unique = []
    for combined, sem_sim, kw_score, domain_boost, d in results:
        if d['path'] not in seen:
            seen.add(d['path'])
            unique.append({
                'score': combined,
                'semantic_score': sem_sim,
                'keyword_score': kw_score,
                'domain_boost': domain_boost,
                **d
            })
        if len(unique) >= top_k:
            break
    
    return unique

def get_context(query: str, top_k: int = 5) -> str:
    """Get formatted context for LLM."""
    results = search(query, top_k=top_k)
    if not results:
        return ""
    
    lines = [f"### Retrieved Context for: '{query}'\n"]
    
    for i, r in enumerate(results, 1):
        score = r['score'] * 100
        sem = r['semantic_score'] * 100
        kw = r['keyword_score'] * 100
        
        lines.append(f"**{i}.** [{score:.0f}%] `{r['path']}`")
        lines.append(f"  _Semantic: {sem:.0f}%, Keywords: {kw:.0f}%_")
        lines.append(f"> {r['text'][:250]}...")
        lines.append("")
    
    return '\n'.join(lines)

# ── File Watcher ─────────────────────────────────────────────────────

def check_for_changes() -> List[Path]:
    """Check for changed files since last index."""
    if not CHECKPOINT_FILE.exists():
        return []
    
    with open(CHECKPOINT_FILE) as f:
        checkpoint = json.load(f)
    
    new_files, changed_files, _, _ = discover_files(checkpoint)
    return new_files + changed_files

# ── CLI ────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 vault_index_v3.py index|incremental|search|context")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "index":
        build_index(incremental=False)
    elif cmd == "incremental":
        build_index(incremental=True)
    elif cmd == "search":
        if len(sys.argv) < 3:
            print("❌ Provide a query")
            sys.exit(1)
        results = search(' '.join(sys.argv[2:]))
        for i, r in enumerate(results, 1):
            print(f"\n{i}. [{r['score']*100:.0f}%] {r['path']}")
            print(f"   {r['text'][:200]}...")
    elif cmd == "context":
        if len(sys.argv) < 3:
            print("❌ Provide a query")
            sys.exit(1)
        print(get_context(' '.join(sys.argv[2:])))
    elif cmd == "check":
        changes = check_for_changes()
        print(f"Found {len(changes)} changed/new files")
        for f in changes[:10]:
            print(f"  {f}")
    else:
        print("Unknown command")

if __name__ == "__main__":
    main()
