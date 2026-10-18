#!/usr/bin/env python3
"""Test the 9 Firecrawl anti-bot sources with the refined noise filter."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from headless_fetcher import FIRECRAWL_SOURCES

SOURCES = ["nike", "a16z", "bcg", "gartner", "cbre", "colliers", "starbucks", "doordash", "iea"]

for k in SOURCES:
    try:
        items = FIRECRAWL_SOURCES[k](max_results=5)
        print(f"=== {k}: {len(items)} articles ===")
        for i in items[:5]:
            print(f"  [{i['source']}] {i['title'][:55]}")
            print(f"    {i['url'][:75]}")
    except Exception as e:
        print(f"=== {k}: ERROR {e} ===")
    print()
