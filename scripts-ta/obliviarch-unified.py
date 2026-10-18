"""
OBLIVIARCH UNIFIED — Integrates Obliviarch compression + LLM Wiki + Hermes Memory

Three-tier memory pipeline:
  Tier 1: Hermes Memory (hot) — MEMORY.md, USER.md (per-session, <6000 chars)
  Tier 2: LLM Wiki (warm) — System/Wiki/ markdown (human-readable, cross-referenced)
  Tier 3: Obliviarch (cold) — .obliviarch/ JSON (compressed traces → schemas → archetypes)

Consolidation flow:
  1. Read Hermes Memory → extract key facts
  2. Run Obliviarch Lethe cycle → promote traces → schemas → archetypes
  3. Sync Obliviarch archetypes → LLM Wiki as entity/concept pages
  4. Update MEMORY.md with wiki index reference
  5. Log to daily note
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────

VAULT_PATH = Path(os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    str(Path.home() / "Documents" / "Obsidian Vault"),
))
HERMES_HOME = Path.home() / ".hermes"
MEMORY_FILE = HERMES_HOME / "memories" / "MEMORY.md"
USER_FILE = HERMES_HOME / "memories" / "USER.md"
WIKI_DIR = VAULT_PATH / "System" / "Wiki"
WIKI_INDEX = WIKI_DIR / "index.md"
WIKI_LOG = WIKI_DIR / "log.md"
WIKI_SCHEMA = WIKI_DIR / "SCHEMA.md"
OBLIVIARCH_DIR = VAULT_PATH / ".obliviarch"
DAILY_PATH = VAULT_PATH / "Daily" / f"{date.today()}.md"

# ── Stats ──────────────────────────────────────────────────────────────

stats = {
    "traces_recorded": 0,
    "schemas_extracted": 0,
    "archetypes_synced": 0,
    "wiki_pages_updated": 0,
    "memory_entries": 0,
}


# ── Obliviarch Engine ──────────────────────────────────────────────────

def run_obliviarch_consolidation() -> dict:
    """Run Obliviarch Lethe cycle and return results."""
    try:
        sys.path.insert(0, str(HERMES_HOME / "skills" / "obliviarch"))
        from obliviarch.engine import Obliviarch, ObliviarchConfig

        # Strip tags from traces before Lethe (Obliviarch doesn't support tags field)
        trace_file = OBLIVIARCH_DIR / "episodic" / "traces.json"
        original_traces = None
        if trace_file.exists():
            try:
                original_traces = json.loads(trace_file.read_text())
                sanitized = json.loads(trace_file.read_text())
                for trace in sanitized.get("traces", []):
                    trace.pop("tags", None)
                trace_file.write_text(json.dumps(sanitized, indent=4))
            except (json.JSONDecodeError, OSError):
                pass

        config = ObliviarchConfig(data_dir=str(OBLIVIARCH_DIR))
        engine = Obliviarch(config=config)
        engine.start()
        result = engine.consolidate()
        engine.stop()

        # Restore original traces with tags
        if original_traces is not None:
            trace_file.write_text(json.dumps(original_traces, indent=4))

        return result
    except Exception as e:
        return {"error": str(e), "new_schemas": 0, "new_archetypes": 0,
                "traces_archived": 0, "archetypes_forgotten": 0,
                "trace_count": 0, "schema_count": 0, "archetype_count": 0}


def get_obliviarch_stats() -> dict:
    """Get current Obliviarch stats without running consolidation."""
    try:
        sys.path.insert(0, str(HERMES_HOME / "skills" / "obliviarch"))
        from obliviarch.engine import Obliviarch, ObliviarchConfig

        config = ObliviarchConfig(data_dir=str(OBLIVIARCH_DIR))
        engine = Obliviarch(config=config)
        engine.start()
        s = engine.stats()
        engine.stop()
        return s
    except Exception as e:
        return {"error": str(e)}


def read_obliviarch_data() -> dict:
    """Read Obliviarch JSON files directly."""
    data = {"archetypes": [], "schemas": [], "traces": []}

    arch_file = OBLIVIARCH_DIR / "archetypal" / "archetypes.json"
    if arch_file.exists():
        try:
            data["archetypes"] = json.loads(arch_file.read_text()).get("archetypes", [])
        except (json.JSONDecodeError, OSError):
            pass

    schema_file = OBLIVIARCH_DIR / "semantic" / "schemas.json"
    if schema_file.exists():
        try:
            data["schemas"] = json.loads(schema_file.read_text()).get("schemas", [])
        except (json.JSONDecodeError, OSError):
            pass

    trace_file = OBLIVIARCH_DIR / "episodic" / "traces.json"
    if trace_file.exists():
        try:
            data["traces"] = json.loads(trace_file.read_text()).get("traces", [])
        except (json.JSONDecodeError, OSError):
            pass

    return data


# ── Hermes Memory Integration ──────────────────────────────────────────

def read_memory() -> list[dict]:
    """Parse MEMORY.md into structured entries."""
    if not MEMORY_FILE.exists():
        return []

    content = MEMORY_FILE.read_text()
    entries = []
    for section in content.split("§"):
        section = section.strip()
        if not section or section.startswith("# MEMORY"):
            continue
        lines = section.split("\n")
        entry = {"text": section, "tags": []}
        for line in lines:
            m = re.match(r'^(\w+(?:-\w+)*)\s*\|\s*(.+)$', line.strip())
            if m:
                entry["tags"].append(m.group(1))
        entries.append(entry)
    return entries


def update_memory_with_wiki_ref() -> None:
    """Ensure MEMORY.md has a reference to the LLM Wiki."""
    if not MEMORY_FILE.exists():
        return

    content = MEMORY_FILE.read_text()
    wiki_ref = "HERMES-JANUARY | LLM-WIKI: Knowledge base at System/Wiki/. Index at System/Wiki/index.md. Schema at System/Wiki/SCHEMA.md. Log at System/Wiki/log.md."

    if "LLM-WIKI" not in content:
        # Add after the vault entry
        vault_entry = "HERMES-JANUARY | VAULT-ROOT"
        if vault_entry in content:
            content = content.replace(
                vault_entry,
                f"{wiki_ref}\n\n§\n{vault_entry}",
                1
            )
            MEMORY_FILE.write_text(content)
            stats["memory_entries"] += 1


def update_memory_with_obliviarch_ref() -> None:
    """Ensure MEMORY.md has a reference to Obliviarch."""
    if not MEMORY_FILE.exists():
        return

    content = MEMORY_FILE.read_text()
    obliviarch_ref = "HERMES-JANUARY | OBLIVIARCH-UNIFIED: Memory compression at .obliviarch/. Archetypes synced to System/Wiki/entities/obliviarch/. Daily consolidation via cron."

    if "OBLIVIARCH-UNIFIED" not in content:
        content = content.replace(
            "HERMES-JANUARY | OBLIVIARCH:",
            f"{obliviarch_ref}\n\n§\nHERMES-JANUARY | OBLIVIARCH:",
            1
        )
        MEMORY_FILE.write_text(content)
        stats["memory_entries"] += 1


# ── LLM Wiki Integration ───────────────────────────────────────────────

def slugify(text: str) -> str:
    """Convert text to a safe filename."""
    text = text.strip()
    text = re.sub(r'[^\w\s\U0001F000-\U0010FFFF\u2600-\u27BF\u2B50\u2702-\u27B0\u00A9\u00AE\u2000-\u3300]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    text = text.strip('-')
    if not text:
        return "untitled"
    return text[:100]


def sync_archetypes_to_wiki(obliviarch_data: dict) -> None:
    """Sync Obliviarch archetypes to LLM Wiki entity pages."""
    archetypes = obliviarch_data.get("archetypes", [])
    if not archetypes:
        return

    entities_dir = WIKI_DIR / "entities"
    entities_dir.mkdir(parents=True, exist_ok=True)

    for arch in archetypes:
        name = arch.get("name", "unknown")
        slug = slugify(name.lower())
        filename = f"obliviarch-{slug}.md"
        filepath = entities_dir / filename

        pattern = ", ".join(arch.get("pattern", []))
        activation = arch.get("activation_count", 0)
        description = arch.get("description", "")

        content = f"""---
title: Obliviarch Archetype: {name}
created: {date.today()}
updated: {date.today()}
type: entity
tags: [obliviarch, archetype, memory, compression]
confidence: high
---

# Obliviarch Archetype: {name}

**Pattern:** `{pattern}`
**Activations:** {activation}
**Description:** {description}

## Details

- **Archetype ID:** `{arch.get('archetype_id', '?')}`
- **Created:** {datetime.fromtimestamp(arch.get('created_at', 0)).strftime('%Y-%m-%d') if arch.get('created_at') else '?'}
- **Last activated:** {datetime.fromtimestamp(arch.get('last_activated', 0)).strftime('%Y-%m-%d %H:%M') if arch.get('last_activated') else '?'}
- **Cold storage:** {arch.get('cold', False)}

## Related

- [[entities/obliviarch-overview|Obliviarch Overview]]
- [[concepts/llm-wiki-pattern|LLM Wiki Pattern]]
- [[concepts/obsidian-vault-integration|Obsidian Vault Integration]]
"""
        filepath.write_text(content)
        stats["archetypes_synced"] += 1

    # Create overview page
    overview_path = entities_dir / "obliviarch-overview.md"
    arch_count = len(archetypes)
    schema_count = len(obliviarch_data.get("schemas", []))
    trace_count = len(obliviarch_data.get("traces", []))

    overview = f"""---
title: Obliviarch Overview
created: {date.today()}
updated: {date.today()}
type: entity
tags: [obliviarch, memory, compression, system]
confidence: high
---

# Obliviarch — Architecture of Controlled Oblivion

Trace Schema Compression (TSC) pipeline integrated with the LLM Wiki and Hermes Memory.

## Current State

| Tier | Layer | Count | Location |
|------|-------|-------|----------|
| Episodic | Raw traces | {trace_count} | `.obliviarch/episodic/traces.json` |
| Semantic | Extracted schemas | {schema_count} | `.obliviarch/semantic/schemas.json` |
| Archetypal | Immortal patterns | {arch_count} | `.obliviarch/archetypal/archetypes.json` |

## Archetypes

"""
    for arch in archetypes:
        name = arch.get("name", "unknown")
        slug = slugify(name.lower())
        desc = arch.get("description", "")
        overview += f"- [[entities/obliviarch-{slug}|{name}]] — {desc}\n"

    overview += f"""
## Integration

- **Hermes Memory** (`~/.hermes/memories/MEMORY.md`) — Hot memory, read every session
- **LLM Wiki** (`System/Wiki/`) — Warm knowledge base, human-readable markdown
- **Obliviarch** (`.obliviarch/`) — Cold compression, JSON traces → schemas → archetypes

## Consolidation Cycle

Daily at 6:00 AM via cron:
1. Read Hermes Memory → extract key facts
2. Run Obliviarch Lethe cycle → promote traces → schemas → archetypes
3. Sync archetypes → LLM Wiki entity pages
4. Update MEMORY.md references
5. Log to daily note

## Related

- [[concepts/llm-wiki-pattern|LLM Wiki Pattern]]
- [[concepts/obsidian-vault-integration|Obsidian Vault Integration]]
"""
    overview_path.write_text(overview)
    stats["wiki_pages_updated"] += 1


def update_wiki_index() -> None:
    """Update the wiki index with Obliviarch entries."""
    if not WIKI_INDEX.exists():
        return

    content = WIKI_INDEX.read_text()

    # Add Obliviarch section if missing
    obliviarch_section = "## Obliviarch"
    if obliviarch_section not in content:
        # Find the Entities section
        entities_section = "## Entities"
        if entities_section in content:
            content = content.replace(
                entities_section,
                f"{obliviarch_section}\n- [[entities/obliviarch-overview|Obliviarch Overview]] — Unified memory compression pipeline\n\n{entities_section}",
                1
            )
            WIKI_INDEX.write_text(content)
            stats["wiki_pages_updated"] += 1


def append_to_wiki_log(action: str, details: str) -> None:
    """Append to the wiki log."""
    if not WIKI_LOG.exists():
        return

    content = WIKI_LOG.read_text()
    entry = f"\n## [{date.today()}] {action}\n- {details}\n"
    content += entry
    WIKI_LOG.write_text(content)


# ── Daily Note Logging ─────────────────────────────────────────────────

def append_to_daily_log() -> None:
    """Append consolidation summary to today's daily note."""
    if not DAILY_PATH.exists():
        return

    content = DAILY_PATH.read_text()
    entry = f"- {time.strftime('%I:%M %p').lstrip('0')} — Obliviarch unified: {stats['archetypes_synced']} archetypes synced, {stats['wiki_pages_updated']} wiki pages updated, {stats['memory_entries']} memory refs\n"

    if "## Log" in content:
        content = content.replace("## Log", f"## Log\n{entry}", 1)
    DAILY_PATH.write_text(content)


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    print(f"╔══════════════════════════════════════════════╗")
    print(f"║  OBLIVIARCH UNIFIED — Memory Consolidation  ║")
    print(f"╚══════════════════════════════════════════════╝")
    print()
    print(f"Vault: {VAULT_PATH}")
    print()

    # Step 1: Read current Obliviarch data
    print("📖 Reading Obliviarch data...")
    obliviarch_data = read_obliviarch_data()
    print(f"  Archetypes: {len(obliviarch_data['archetypes'])}")
    print(f"  Schemas: {len(obliviarch_data['schemas'])}")
    print(f"  Traces: {len(obliviarch_data['traces'])}")
    print()

    # Step 2: Run Obliviarch consolidation
    print("🔄 Running Obliviarch Lethe cycle...")
    result = run_obliviarch_consolidation()
    if "error" in result:
        print(f"  ⚠ {result['error']}")
    else:
        print(f"  New schemas: {result.get('new_schemas', 0)}")
        print(f"  New archetypes: {result.get('new_archetypes', 0)}")
        print(f"  Traces archived: {result.get('traces_archived', 0)}")
    print()

    # Step 3: Sync archetypes to LLM Wiki
    print("📝 Syncing archetypes to LLM Wiki...")
    sync_archetypes_to_wiki(obliviarch_data)
    update_wiki_index()
    print(f"  Archetype pages: {stats['archetypes_synced']}")
    print(f"  Wiki pages updated: {stats['wiki_pages_updated']}")
    print()

    # Step 4: Update Hermes Memory
    print("💾 Updating Hermes Memory...")
    update_memory_with_wiki_ref()
    update_memory_with_obliviarch_ref()
    print(f"  Memory entries updated: {stats['memory_entries']}")
    print()

    # Step 5: Log to wiki
    append_to_wiki_log(
        "obliviarch-consolidation",
        f"Lethe cycle: {result.get('new_schemas', 0)} schemas, "
        f"{result.get('new_archetypes', 0)} archetypes. "
        f"Synced {stats['archetypes_synced']} archetypes to wiki. "
        f"Updated {stats['wiki_pages_updated']} wiki pages."
    )

    # Step 6: Log to daily note
    append_to_daily_log()

    # Summary
    print("╔══════════════════════════════════════════════╗")
    print("║                    Summary                   ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  🔄 Lethe cycle: {result.get('new_schemas', 0)} schemas, {result.get('new_archetypes', 0)} archetypes")
    print(f"  📝 Wiki pages: {stats['wiki_pages_updated']} updated")
    print(f"  🏛️  Archetypes: {stats['archetypes_synced']} synced")
    print(f"  💾 Memory refs: {stats['memory_entries']} added")
    print(f"  📁 Output: {WIKI_DIR / 'entities/'}")
    print()


if __name__ == "__main__":
    main()
