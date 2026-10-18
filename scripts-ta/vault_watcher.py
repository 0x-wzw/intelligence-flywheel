#!/usr/bin/env python3
"""
VAULT FILE WATCHER
Monitors Obsidian Vault for changes and triggers incremental re-indexing.

Usage:
    python3 vault_watcher.py              # One-shot check
    python3 vault_watcher.py --daemon     # Run as daemon (for launchd)
    python3 vault_watcher.py --install    # Install launchd plist
"""

import json
import sys
import time
from pathlib import Path
from datetime import datetime

WATCH_STATE = Path.home() / ".hermes/vault-index/watch_state.json"
VAULT = Path.home() / "Documents/Obsidian Vault"
WATCH_DIRS = [
    VAULT / "01-Investment Research",
    VAULT / "Inbox/Notion",
    VAULT / "System/Wiki",
    VAULT / "Knowledge",            # knowledge-lifecycle system
    VAULT / "Work/AISED",           # AISED corpus (incl. BHSFI workstream)
    VAULT / "System/Assistant",
    VAULT / "Daily",
]

def get_current_state():
    """Get current file mtimes."""
    state = {}
    for base_dir in WATCH_DIRS:
        if not base_dir.exists():
            continue
        for f in base_dir.rglob("*"):
            if f.suffix not in {".md", ".txt"}:
                continue
            if any(part.startswith(".") for part in f.relative_to(VAULT).parts):
                continue
            rel = str(f.relative_to(VAULT))
            state[rel] = f.stat().st_mtime
    return state

def check_changes():
    """Check for changes since last run."""
    current = get_current_state()
    
    if not WATCH_STATE.exists():
        # First run
        with open(WATCH_STATE, 'w') as f:
            json.dump({'files': current, 'last_check': datetime.now().isoformat()}, f)
        return []
    
    with open(WATCH_STATE) as f:
        saved = json.load(f)
    
    old_files = saved.get('files', {})
    changes = []
    
    # Find new/changed files
    for rel, mtime in current.items():
        if rel not in old_files:
            changes.append(('new', rel, mtime))
        elif old_files[rel] < mtime:
            changes.append(('changed', rel, mtime))
    
    # Find deleted files
    for rel in old_files:
        if rel not in current:
            changes.append(('deleted', rel, 0))
    
    # Update state
    with open(WATCH_STATE, 'w') as f:
        json.dump({'files': current, 'last_check': datetime.now().isoformat()}, f)
    
    return changes

def trigger_reindex():
    """Trigger incremental re-indexing."""
    import subprocess
    try:
        result = subprocess.run(
            [sys.executable, str(Path.home() / ".hermes/scripts/vault_index_v3.py"), "incremental"],
            timeout=600,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except Exception as e:
        return False, str(e)

def main():
    print(f"🔍 Vault File Watcher — {datetime.now().isoformat()}")
    
    changes = check_changes()
    
    if not changes:
        print("✅ No changes detected")
        return 0
    
    print(f"📋 Detected {len(changes)} change(s):")
    for change_type, rel, mtime in changes[:10]:
        print(f"  [{change_type}] {rel}")
    
    if len(changes) > 10:
        print(f"  ... and {len(changes) - 10} more")
    
    # Trigger re-index if there are changes
    print("\n🔄 Triggering incremental re-index...")
    success, output = trigger_reindex()
    
    if success:
        print("✅ Re-index completed")
    else:
        print(f"❌ Re-index failed: {output[:200]}")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
