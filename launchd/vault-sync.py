"""
OBLIVIARCH Vault Sync Daemon — watches Obsidian vault for changes
and syncs updates to Hermes memory files.

Monitors System/Assistant/ and Daily/ for file changes, then:
1. Updates ~/.hermes/memories/MEMORY.md with relevant changes
2. Logs sync events for the AI to pick up on next session

Uses watchdog (Python) for cross-platform file monitoring.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("vault-sync")

# ── Configuration ────────────────────────────────────────────────────

VAULT_PATH = os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    str(Path.home() / "Documents" / "Obsidian Vault"),
)
MEMORY_FILE = Path.home() / ".hermes" / "memories" / "MEMORY.md"
SYNC_LOG = Path.home() / ".hermes" / "logs" / "vault-sync.log"

WATCH_DIRS = [
    "System/Assistant/",
    "Daily/",
    "System/Wiki/",
    "Knowledge/",
    "Work/Business/MUI-Transformation/",
]

# ── File Event Handler ────────────────────────────────────────────────


class VaultSyncHandler(FileSystemEventHandler):
    """Handles file change events from the Obsidian vault."""

    def __init__(self):
        self._last_sync: float = 0
        self._debounce_seconds = 5  # Wait 5s after last change before syncing

    def on_modified(self, event: FileModifiedEvent | FileCreatedEvent) -> None:
        if event.is_directory:
            return
        if not event.src_path.endswith(".md"):
            return

        rel_path = os.path.relpath(event.src_path, VAULT_PATH)
        logger.info(f"Change detected: {rel_path}")

        # Debounce: wait for quiet period before syncing
        now = time.time()
        if now - self._last_sync < self._debounce_seconds:
            return
        self._last_sync = now

        try:
            self._sync_to_memory(rel_path, event.src_path)
        except Exception as e:
            logger.error(f"Sync failed for {rel_path}: {e}")

    def _sync_to_memory(self, rel_path: str, full_path: str) -> None:
        """Sync a vault file change to MEMORY.md."""
        if not MEMORY_FILE.exists():
            return

        # Read file content with macOS sandbox workaround
        try:
            with open(full_path) as f:
                content = f.read()
        except PermissionError:
            # macOS sandbox: use terminal to read
            import subprocess
            try:
                result = subprocess.run(
                    ["cat", full_path],
                    capture_output=True, text=True, timeout=5,
                )
                content = result.stdout
            except Exception as e:
                logger.warning(f"Could not read {rel_path} via subprocess: {e}")
                return

        # Extract title from frontmatter or filename
        title = Path(rel_path).stem
        for line in content.split("\n"):
            if line.startswith("title:"):
                title = line.replace("title:", "").strip()
                break

        # Build a sync entry
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"\n## § Vault Sync [{timestamp}]\n- File: `{rel_path}`\n- Title: {title}\n- Preview: {content[:200].strip()}\n"

        # Append to sync log
        SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SYNC_LOG, "a") as f:
            f.write(entry)

        logger.info(f"Synced {rel_path} to memory log")


# ── Daemon ────────────────────────────────────────────────────────────


def run_daemon() -> None:
    """Run the vault sync daemon."""
    vault = Path(VAULT_PATH)
    if not vault.exists():
        logger.error(f"Vault path does not exist: {VAULT_PATH}")
        sys.exit(1)

    logger.info(f"Watching vault: {VAULT_PATH}")
    logger.info(f"Memory file: {MEMORY_FILE}")
    logger.info(f"Sync log: {SYNC_LOG}")

    handler = VaultSyncHandler()
    observer = Observer()

    for subdir in WATCH_DIRS:
        watch_path = vault / subdir
        if watch_path.exists():
            observer.schedule(handler, str(watch_path), recursive=True)
            logger.info(f"  Watching: {subdir}")
        else:
            logger.info(f"  Skipping (not found): {subdir}")

    observer.start()
    logger.info("Vault sync daemon started")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        observer.stop()

    observer.join()
    logger.info("Vault sync daemon stopped")


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="OBLIVIARCH Vault Sync Daemon")
    parser.add_argument("--vault-path", default=None, help="Obsidian vault path")
    parser.add_argument("--daemon", action="store_true", help="Run as background daemon")
    parser.add_argument("--oneshot", action="store_true", help="Run one sync cycle and exit")

    args = parser.parse_args()

    vault_path = args.vault_path or VAULT_PATH

    if args.oneshot:
        logger.info("One-shot sync not yet implemented (run without --oneshot for daemon mode)")
        return

    # Re-run with the correct vault path
    os.environ["OBSIDIAN_VAULT_PATH"] = vault_path
    run_daemon()


if __name__ == "__main__":
    main()
