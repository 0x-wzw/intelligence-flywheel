#!/usr/bin/python3
"""Enhanced Market Intelligence Aggregator — fetches from multiple sources:
- Google News RSS (fallback)
- Thought leader feeds (Substack, blogs, Twitter via Nitter)
- Research papers (arXiv)

This wrapper script ensures the script runs with the correct interpreter.
"""

import subprocess
import sys
import os

script_dir = os.path.dirname(os.path.abspath(__file__))
script_path = os.path.join(script_dir, "market-intelligence.py")

# Ensure we use the correct interpreter regardless of how this script is invoked
subprocess.run(["/usr/bin/python3", script_path], check=True)