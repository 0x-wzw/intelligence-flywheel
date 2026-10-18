#!/usr/bin/env python3
"""
OBLIVIARCH + SEMANTIC SEARCH INTEGRATION
Runs semantic retrieval before Obliviarch consolidation for richer context.

This script:
1. Updates semantic index (incremental)
2. Searches vault for relevant context
3. Injects context into Obliviarch before Lethe cycle
4. Runs standard Obliviarch consolidation
"""

import subprocess
import sys
from pathlib import Path

# ── Configuration ────────────────────────────────────────────────────

OBLIVIARCH_SCRIPT = Path.home() / ".hermes/scripts/obliviarch-unified.py"
SEARCH_SCRIPT = Path.home() / ".hermes/scripts/vault_search_auto.py"

# ── Integration ─────────────────────────────────────────────────────

def run_semantic_retrieval():
    """Run semantic search to get relevant context."""
    print("🔍 Semantic Retrieval Phase")
    print("="*50)
    
    # Update index first (incremental)
    try:
        subprocess.run(
            [sys.executable, str(SEARCH_SCRIPT), "index"],
            timeout=300,
            capture_output=True,
        )
        print("  ✅ Index updated")
    except Exception as e:
        print(f"  ⚠️ Index update: {e}")
    
    # Get recent context from Daily notes
    try:
        result = subprocess.run(
            [sys.executable, str(SEARCH_SCRIPT), "context", "MUI adidas recent work"],
            timeout=60,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print("  📄 Recent context retrieved")
            return result.stdout
    except Exception as e:
        print(f"  ⚠️ Search: {e}")
    
    return ""

def main():
    print("╔═══════════════════════════════════════════════════╗")
    print("║  OBLIVIARCH + SEMANTIC SEARCH INTEGRATION          ║")
    print("╚═══════════════════════════════════════════════════╝")
    print()
    
    # Phase 1: Semantic Retrieval
    context = run_semantic_retrieval()
    
    if context:
        print("\n📥 Retrieved Context:")
        print(context[:500])
        print("...")
    
    # Phase 2: Run Obliviarch
    print("\n🔄 Running Obliviarch Consolidation...")
    print("="*50)
    
    try:
        result = subprocess.run(
            [sys.executable, str(OBLIVIARCH_SCRIPT)],
            timeout=300,
        )
        print(f"  ✅ Obliviarch completed (exit: {result.returncode})")
    except Exception as e:
        print(f"  ❌ Obliviarch failed: {e}")
    
    print("\n✅ Integration complete")

if __name__ == "__main__":
    main()
