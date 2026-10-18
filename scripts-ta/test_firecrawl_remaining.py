#!/usr/bin/env python3
"""Test individual Firecrawl sources one at a time to avoid rate limits."""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from headless_fetcher import FIRECRAWL_SOURCES

# Test the ones not yet confirmed (doordash, iea, starbucks, a16z)
for k in ["doordash", "iea", "starbucks", "a16z"]:
    try:
        items = FIRECRAWL_SOURCES[k](max_results=5)
        print(f"=== {k}: {len(items)} articles ===")
        for i in items[:5]:
            print(f"  [{i['source']}] {i['title'][:55]}")
            print(f"    {i['url'][:75]}")
    except Exception as e:
        print(f"=== {k}: ERROR {e} ===")
    print()
    time.sleep(3)
