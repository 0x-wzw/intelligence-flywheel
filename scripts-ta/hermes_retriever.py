#!/usr/bin/env python3
"""
HERMES AUTO-RETRIEVER — DEPRECATED SHIM

This module used to maintain its own retrieval stack on top of
``comprehensive_index.pkl`` (built by ``vault_index_v3.py``). That index had no
scheduled rebuild — the only thing that refreshed it was ``vault_watcher.py``,
which is not running — so it silently served embeddings that got older every
day. ``ensure_index()`` only rebuilt when the file was *missing*, which it never
was, so nothing ever corrected the drift.

Retrieval is now consolidated on ``vault_retriever``, which reads
``auto_index.pkl`` (rebuilt daily by the ``vault-semantic-index`` cron job) and
leads every result with the pinned wiki-first docs. Both stacks used the same
embedding model (all-MiniLM-L6-v2), so results stay comparable.

The public API below is preserved so any existing importer keeps working; each
call now transparently uses the fresh index.

Usage:
    from hermes_retriever import auto_context
    context = auto_context("What is MUI's revenue?")
"""

import re
import sys
from pathlib import Path

# Ensure the scripts directory is importable when run as a script.
sys.path.insert(0, str(Path(__file__).parent))

from vault_retriever import retrieve_context, search_files  # noqa: E402,F401

# Domain-specific financial terms, kept for callers that import them.
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

_STOPWORDS = {
    'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'her', 'was',
    'one', 'our', 'had', 'what', 'how', 'who', 'why', 'when', 'where', 'which',
    'this', 'that', 'with', 'from', 'they', 'have', 'been', 'were', 'said',
    'each', 'their', 'time', 'would', 'there', 'could', 'other', 'after',
    'first', 'never', 'these', 'those', 'being', 'over', 'also', 'back', 'only',
    'know', 'take', 'year', 'good', 'some', 'come', 'make', 'well', 'work',
    'life', 'even', 'more', 'very', 'here', 'than', 'them', 'into', 'just',
    'like', 'look', 'down', 'most', 'long', 'find', 'give', 'does', 'made',
    'part', 'such', 'keep', 'call', 'came', 'need', 'feel', 'seem', 'turn',
    'hand', 'high', 'sure', 'upon', 'head', 'help', 'home', 'side', 'move',
    'both', 'five', 'once', 'same', 'must', 'name', 'left', 'done', 'open',
    'case', 'show', 'live', 'play', 'went', 'told', 'seen', 'soon', 'took',
    'body', 'mind', 'land', 'line', 'kind', 'next', 'word', 'began', 'found',
    'heard', 'shall', 'might', 'while', 'three', 'small', 'large', 'great',
    'place', 'every', 'under', 'still', 'until', 'since', 'among', 'about',
    'above', 'below', 'between', 'through', 'during', 'before',
}


def extract_keywords(text):
    """Pull salient keywords out of a query (financial terms + years + content words)."""
    text_lower = text.lower()
    words = re.findall(r'\b[A-Za-z]{3,}\b', text_lower)
    years = re.findall(r'\b(20\d{2})\b', text_lower)

    result = set(years[:3])
    result.update(FINANCIAL_TERMS & set(words))
    result.update([w for w in words if w not in _STOPWORDS][:15])
    return result


def auto_context(query: str, top_k: int = 5) -> str:
    """Retrieve relevant vault context for a query, from the freshly-built index."""
    return retrieve_context(query, top_k=top_k)


def auto_retrieve_for_prompt(query: str) -> str:
    """Format context for injection into a Hermes prompt."""
    context = auto_context(query, top_k=3)
    if context:
        return f"\n---\n{context}\n---\n"
    return ""


if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(auto_context(' '.join(sys.argv[1:])))
    else:
        print("Usage: python3 hermes_retriever.py 'query'")
