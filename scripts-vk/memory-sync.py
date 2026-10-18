#!/usr/bin/env python3
"""memory-sync.py — push live agent memory to the GitHub DR repo.

Copies ~/.hermes/memories/{MEMORY.md,USER.md} plus each project MEMORY.md into
a local clone of 0x-wzw/january-hermes-backup (memories/), commits, and pushes
only when content actually changed. Weekly cron (no-agent mode).
"""
import filecmp
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HOME = Path.home()
REPO_DIR = Path("/tmp/gh-mem/january-hermes-backup")
CENTRAL = HOME / ".hermes/memories"
PROJECTS = HOME / "Projects"

COPIES = [
    (CENTRAL / "MEMORY.md", REPO_DIR / "memories/MEMORY-central.md"),
    (CENTRAL / "USER.md", REPO_DIR / "memories/USER.md"),
]
PROJECT_SLUGS = [
    "vault-infra", "mui-intelligence", "aised-partnerships",
    "system-ops", "knowledge-skills", "fashion-portfolio", "cc-athletica",
]


def git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(REPO_DIR), *args],
                       capture_output=True, text=True)
    return r.stdout.strip() or r.stderr.strip()


def ensure_clone() -> bool:
    """Clone the DR repo if the /tmp copy is missing (tmpdirs don't survive reboots)."""
    if REPO_DIR.exists():
        return True
    REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["git", "clone", "git@github.com:0x-wzw/january-hermes-backup.git",
                        str(REPO_DIR)], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"❌ clone failed: {(r.stderr or r.stdout).strip()[:200]}")
        return False
    print(f"📥 cloned fresh copy to {REPO_DIR}")
    return True


def main() -> int:
    if not ensure_clone():
        return 1

    git("fetch", "origin")
    git("reset", "--hard", "origin/HEAD")

    changed = []
    for src, dst in COPIES:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            continue
        shutil.copy2(src, dst)
        changed.append(dst.name)

    for slug in PROJECT_SLUGS:
        src = PROJECTS / slug / "MEMORY.md"
        dst = REPO_DIR / "memories/projects" / f"{slug}.md"
        if not src.exists():
            continue
        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        changed.append(dst.name)

    # Stack is authored directly in the repo — nothing to copy; it rides along.
    if changed:
        git("add", "memories/")

    if not changed:
        print(f"✅ memory already in sync — {datetime.now():%Y-%m-%d %H:%M} (no changes)")
        return 0

    git("add", "memories/")
    git("-c", "user.name=0x-wzw",
        "-c", "user.email=zehan.teoh@muiglobal.com",
        "commit", "-m",
        f"memories: weekly sync {datetime.now():%Y-%m-%d} ({len(changed)} files changed)")
    out = git("push", "origin", "HEAD")
    print(f"✅ pushed {len(changed)} changed file(s): {', '.join(sorted(changed))}")
    print(out.splitlines()[-1] if out else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
