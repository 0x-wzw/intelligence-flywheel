"""
Notion → Obsidian Vault Sync (Incremental)
Only syncs new or changed pages since last run using a local index.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Optional

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
if not NOTION_API_KEY:
    env_file = Path.home() / ".hermes" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("NOTION_API_KEY="):
                NOTION_API_KEY = line.split("=", 1)[1].strip()
                break

VAULT_PATH = os.environ.get(
    "OBSIDIAN_VAULT_PATH",
    str(Path.home() / "Documents" / "Obsidian Vault"),
)
NOTION_DIR = Path(VAULT_PATH) / "Inbox" / "Notion"
SYNC_LOG = Path(VAULT_PATH) / "System" / "Wiki" / "log.md"
DAILY_PATH = Path(VAULT_PATH) / "Daily" / f"{date.today()}.md"
INDEX_PATH = NOTION_DIR / ".sync-index.json"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

API_BASE = "https://api.notion.com/v1"

# Stats
stats = {"saved": 0, "skipped": 0, "errors": 0, "databases_queried": 0, "unchanged": 0,
         "skipped_own_output": 0}

# Databases this vault WRITES to. Pulling them back is a round-trip that produces a
# second, lossier copy of a report the vault already holds.
#
# The six trend jobs plus market-intelligence push their reports into these databases
# AND write markdown straight to Knowledge/00-Raw/trends/ via their own save_local().
# The local copy is canonical: more complete (14-15 reports per family against 9-12
# that survived the round-trip), longer bodies, and a real `created` date rather than an
# export timestamp. Re-importing produced 63 near-duplicate files sitting beside the
# originals — near-duplicate, so no byte-identical dedupe could catch them.
#
# Added 2026-07-30. Env overrides are included so a re-pointed database stays excluded.
def _norm_db_id(value: str) -> str:
    return (value or "").replace("-", "").strip().lower()


SKIP_DATABASE_IDS = {
    _norm_db_id(x) for x in (
        "39e05e4a-7c41-8110-8379-e089027e0678",  # csuite-leadership-trends
        "39e05e4a-7c41-817f-b6d7-ec9fc8b9099b",  # fnb-trends
        "39e05e4a-7c41-8197-b832-d2569e0599ad",  # hospitality-trends
        "39e05e4a-7c41-818c-aadc-fbda95247997",  # property-infra-energy-trends
        "39e05e4a-7c41-819a-8e4d-fae703866825",  # ai-science-investment-trends
        "39e05e4a-7c41-81a3-815f-ff9c2e862cc2",  # market-trends + market-intelligence
        os.environ.get("NOTION_DB_ID", ""),
        os.environ.get("NOTION_INTELLIGENCE_DB", ""),
    ) if x
}


def load_index() -> dict[str, str]:
    """Load the sync index: notion_id -> last_edited_time."""
    if INDEX_PATH.exists():
        try:
            return json.loads(INDEX_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_index(index: dict[str, str]) -> None:
    """Save the sync index."""
    NOTION_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index, indent=2))


def notion_api(method: str, endpoint: str, body: Optional[dict] = None, timeout: int = 30) -> dict:
    """Call the Notion REST API."""
    url = f"{API_BASE}{endpoint}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()[:300]
        return {"error": str(e), "status": e.code, "body": body_text}
    except Exception as e:
        return {"error": str(e)}


def notion_api_paginated(endpoint: str, body: dict) -> list[dict]:
    """Call a paginated Notion API endpoint, following all pages."""
    all_results = []
    cursor = None
    while True:
        payload = dict(body)
        if cursor:
            payload["start_cursor"] = cursor
        result = notion_api("POST", endpoint, payload, timeout=30)
        if "error" in result:
            print(f"  ⚠ API error: {result['error']}")
            break
        results = result.get("results", [])
        all_results.extend(results)
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return all_results


def slugify(text: str) -> str:
    """Convert text to a safe filename, preserving emojis for Obsidian wikilinks."""
    text = text.strip()
    # Replace spaces and special chars with hyphens
    text = re.sub(r'[^\w\s\U0001F000-\U0010FFFF\u2600-\u27BF\u2B50\u2702-\u27B0\u00A9\u00AE\u2000-\u3300]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    # Remove leading/trailing hyphens
    text = text.strip('-')
    if not text:
        return "untitled"
    return text[:100]

def notion_markdown_to_obsidian(md: str, title: str, source_id: str = "") -> str:
    """Convert Notion-flavored markdown to Obsidian markdown."""
    # Convert Notion page links to Obsidian wikilinks using slugified titles
    def page_link_to_wikilink(match):
        text = match.group(1)
        slug = slugify(text)
        return f"[[{slug}]]"

    md = re.sub(r'<page url="[^"]+">([^<]+)</page>', page_link_to_wikilink, md)
    md = re.sub(r'<callout[^>]*>', '> ', md)
    md = md.replace('</callout>', '')
    md = re.sub(r'</?columns?>', '', md)
    md = re.sub(r'</?column>', '', md)
    md = md.replace('<empty-block/>', '')
    md = re.sub(r'<database[^>]*>([^<]*)</database>', r'[[\1]]', md)
    md = re.sub(r'\{color="[^"]*"\}', '', md)
    md = re.sub(r'<br\s*/?>', '\n', md)
    md = re.sub(r'\n{3,}', '\n\n', md)
    md = md.strip()

    frontmatter = f"""---
title: {title}
source: notion
synced: {date.today()}
notion_id: {source_id}
---

"""
    return frontmatter + md + "\n"


def get_page_title(page: dict) -> str:
    """Extract title from a Notion page or database object."""
    if "properties" in page:
        for k, v in page["properties"].items():
            if v.get("type") == "title":
                return "".join(t.get("plain_text", "") for t in v.get("title", []))
    if "title" in page:
        return "".join(t.get("plain_text", "") for t in page.get("title", []))
    if page.get("object") == "database" and "title" in page:
        return "".join(t.get("plain_text", "") for t in page["title"])
    return "untitled"


def get_page_icon(page: dict) -> str:
    """Extract emoji icon from a page."""
    icon = page.get("icon", {})
    if icon and icon.get("type") == "emoji":
        return icon.get("emoji", "")
    return ""


def get_last_edited(page: dict) -> str:
    """Get last_edited_time from a page object."""
    return page.get("last_edited_time", "")


def read_page_markdown(page_id: str) -> Optional[str]:
    """Read a Notion page as markdown."""
    result = notion_api("GET", f"/pages/{page_id}/markdown", timeout=15)
    if "error" in result:
        return None
    return result.get("markdown", "")


def query_database(database_id: str) -> list[dict]:
    """Query all entries in a Notion database."""
    return notion_api_paginated(f"/databases/{database_id}/query", {})


def get_database_title(database: dict) -> str:
    """Get the title of a database."""
    if "title" in database:
        return "".join(t.get("plain_text", "") for t in database["title"])
    return "untitled-database"


def is_changed(page: dict, index: dict[str, str]) -> bool:
    """Check if a page has changed since last sync."""
    page_id = page["id"]
    last_edited = get_last_edited(page)
    cached_time = index.get(page_id)
    if cached_time is None:
        return True  # new page
    if last_edited and last_edited != cached_time:
        return True  # changed
    return False  # unchanged


def sync_page(page: dict, index: dict[str, str]) -> tuple[str, str]:
    """Sync a single Notion page to the vault. Returns (title, status)."""
    page_id = page["id"]
    title = get_page_title(page)
    icon = get_page_icon(page)

    if not title.strip():
        return (page_id, "skipped (no title)")

    # Skip database rows — they are synced by sync_database with full properties
    if page.get("parent", {}).get("type") == "database_id":
        return (title, "skipped (database row — handled by database sync)")

    # Check if unchanged
    if not is_changed(page, index):
        return (title, "unchanged (skipped)")

    slug = slugify(title)
    filename = f"{slug}.md"
    filepath = NOTION_DIR / filename

    # Read markdown
    md = read_page_markdown(page_id)
    if md is None:
        return (title, "skipped (no access)")

    if md:
        # Has body content — convert and save
        obsidian_md = notion_markdown_to_obsidian(md, title, page_id)
        NOTION_DIR.mkdir(parents=True, exist_ok=True)
        filepath.write_text(obsidian_md)
    else:
        # Empty body (database row with no body content) — write properties instead
        props_lines = [
            "---",
            f"title: {title}",
            "source: notion",
            f"synced: {date.today()}",
            f"notion_id: {page_id}",
            "---",
            "",
            f"# {title}",
            "",
        ]
        for prop_name, prop_value in page.get("properties", {}).items():
            ptype = prop_value.get("type", "")
            if ptype == "title":
                continue
            val = format_property_value(prop_value)
            if val:
                props_lines.append(f"**{prop_name}:** {val}")
        NOTION_DIR.mkdir(parents=True, exist_ok=True)
        filepath.write_text("\n".join(props_lines) + "\n")

    # Update index
    last_edited = get_last_edited(page)
    if last_edited:
        index[page_id] = last_edited

    return (title, f"saved as Inbox/Notion/{filename}")


def sync_database(database: dict, index: dict[str, str]) -> tuple[str, str]:
    """Sync a Notion database: query all entries and save as markdown."""
    global stats
    db_id = database["id"]
    db_title = get_database_title(database)
    icon = get_page_icon(database)

    if not db_title.strip():
        db_title = f"database-{db_id[:8]}"

    slug = slugify(db_title)
    filename = f"{slug}.md"
    filepath = NOTION_DIR / filename

    # Query all entries
    entries = query_database(db_id)
    stats["databases_queried"] += 1

    if not entries:
        frontmatter = f"""---
title: "{icon} {db_title}"
source: notion
synced: {date.today()}
notion_id: {db_id}
type: database
entries: 0
---

# {icon} {db_title}

*Empty database — no entries found.*
"""
        filepath.write_text(frontmatter)
        return (db_title, f"saved (empty database, 0 entries)")

    # Build database index page
    lines = [f"# {icon} {db_title}", "", f"*Notion database — {len(entries)} entries*", ""]

    saved_entries = 0
    seen_slugs: set[str] = set()
    for entry in entries:
        entry_id = entry["id"]
        entry_title = get_page_title(entry)
        if not entry_title.strip():
            entry_title = f"entry-{entry_id[:8]}"

        entry_slug = slugify(entry_title)

        # Deduplicate: skip if we've already listed this slug
        if entry_slug in seen_slugs:
            continue
        seen_slugs.add(entry_slug)

        entry_filename = f"{entry_slug}.md"
        entry_filepath = NOTION_DIR / entry_filename

        # Check if unchanged
        if is_changed(entry, index):
            entry_md = read_page_markdown(entry_id)
            if entry_md:
                entry_obsidian = notion_markdown_to_obsidian(entry_md, entry_title, entry_id)
                entry_filepath.write_text(entry_obsidian)
            else:
                props_lines = [f"---", f"title: {entry_title}", f"source: notion", f"synced: {date.today()}", f"notion_id: {entry_id}", f"---", "", f"# {entry_title}", ""]
                for prop_name, prop_value in entry.get("properties", {}).items():
                    ptype = prop_value.get("type", "")
                    if ptype == "title":
                        continue
                    val = format_property_value(prop_value)
                    if val:
                        props_lines.append(f"**{prop_name}:** {val}")
                entry_filepath.write_text("\n".join(props_lines) + "\n")

            # Update index
            last_edited = get_last_edited(entry)
            if last_edited:
                index[entry_id] = last_edited

        lines.append(f"- [[{entry_slug}]]")
        saved_entries += 1

    lines.append("")
    lines.append(f"---")
    lines.append(f"*Synced {date.today()} — {saved_entries} entries*")

    frontmatter = f"""---
title: "{icon} {db_title}"
source: notion
synced: {date.today()}
notion_id: {db_id}
type: database
entries: {len(entries)}
---

"""
    filepath.write_text(frontmatter + "\n".join(lines) + "\n")
    return (db_title, f"saved (database: {len(entries)} entries → {saved_entries} individual pages)")


def safe_str(val, default="") -> str:
    if val is None:
        return default
    return str(val)


def safe_get(obj, key, default=""):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def format_property_value(prop: dict) -> str:
    ptype = prop.get("type", "")
    value = prop.get(ptype)
    if value is None:
        return ""

    if ptype == "rich_text":
        if isinstance(value, list):
            return "".join(t.get("plain_text", "") for t in value)
        return safe_str(value)
    elif ptype == "select":
        return safe_get(value, "name") if value else ""
    elif ptype == "multi_select":
        if isinstance(value, list):
            return ", ".join(s.get("name", "") for s in value if isinstance(s, dict))
        return safe_str(value)
    elif ptype == "date":
        if isinstance(value, dict):
            start = value.get("start", "")
            end = value.get("end", "")
            return f"{start} → {end}" if end else start
        return safe_str(value)
    elif ptype == "number":
        return safe_str(value)
    elif ptype == "checkbox":
        return "✅ Yes" if value else "❌ No"
    elif ptype == "url":
        return safe_str(value)
    elif ptype == "email":
        return safe_str(value)
    elif ptype == "phone_number":
        return safe_str(value)
    elif ptype == "status":
        return safe_get(value, "name") if value else ""
    elif ptype == "people":
        if isinstance(value, list):
            names = [p.get("name", "") for p in value if isinstance(p, dict) and p.get("name")]
            return ", ".join(names) if names else ""
        return safe_str(value)
    elif ptype == "files":
        if isinstance(value, list):
            files = [f.get("name", "") for f in value if isinstance(f, dict) and f.get("name")]
            return ", ".join(files) if files else ""
        return safe_str(value)
    elif ptype == "relation":
        if isinstance(value, list):
            return ", ".join(r.get("id", "")[:8] for r in value if isinstance(r, dict))
        return safe_str(value)
    elif ptype == "rollup":
        return safe_str(value)
    elif ptype == "formula":
        return safe_str(value)
    elif ptype == "created_time":
        return safe_str(value)
    elif ptype == "created_by":
        return safe_get(value, "name") if value else ""
    elif ptype == "last_edited_time":
        return safe_str(value)
    elif ptype == "last_edited_by":
        return safe_get(value, "name") if value else ""
    return safe_str(value)


def append_to_daily_log(entries: list[str]) -> None:
    if not DAILY_PATH.exists():
        return
    content = DAILY_PATH.read_text()
    entry = f"- {time.strftime('%I:%M %p').lstrip('0')} — Notion sync: {stats['saved']} new/changed, {stats['unchanged']} unchanged\n"
    if "## Log" in content:
        content = content.replace("## Log", f"## Log\n{entry}", 1)
    DAILY_PATH.write_text(content)


def main() -> None:
    global stats

    if not NOTION_API_KEY:
        print("ERROR: NOTION_API_KEY not set")
        sys.exit(1)

    # Load sync index
    index = load_index()
    print(f"╔══════════════════════════════════════════════╗")
    print(f"║  Notion → Obsidian Vault — Incremental Sync  ║")
    print(f"╚══════════════════════════════════════════════╝")
    print()
    print(f"Vault: {VAULT_PATH}")
    print(f"Output: {NOTION_DIR}")
    print(f"Index: {len(index)} cached items")
    print()

    # Step 1: Search ALL pages and databases
    print("📡 Searching Notion workspace...")
    all_items = notion_api_paginated("/search", {
        "page_size": 100,
        "sort": {"direction": "descending", "timestamp": "last_edited_time"},
    })
    print(f"  Found {len(all_items)} total items")
    print()

    pages = [i for i in all_items if i.get("object") == "page"]
    databases = [i for i in all_items if i.get("object") == "database"]

    # Drop the databases this vault writes to, before anything is fetched or written.
    # Filtering pages too is belt-and-braces: sync_page already skips database rows,
    # but a trend report created as a standalone page would otherwise slip through.
    skipped_dbs = [d for d in databases if _norm_db_id(d.get("id")) in SKIP_DATABASE_IDS]
    databases = [d for d in databases if _norm_db_id(d.get("id")) not in SKIP_DATABASE_IDS]
    before_pages = len(pages)
    pages = [
        p for p in pages
        if _norm_db_id(p.get("parent", {}).get("database_id")) not in SKIP_DATABASE_IDS
    ]
    stats["skipped_own_output"] = len(skipped_dbs) + (before_pages - len(pages))

    print(f"  📄 Pages: {len(pages)}")
    print(f"  🗄️  Databases: {len(databases)}")
    if stats["skipped_own_output"]:
        print(f"  ⏭️  Skipped {stats['skipped_own_output']} item(s) from "
              f"{len(skipped_dbs)} vault-authored database(s) — the vault already "
              f"writes these to Knowledge/00-Raw/trends/")
        for d in skipped_dbs:
            print(f"        · {get_database_title(d) or d.get('id')}")
    print()

    # Step 2: Sync pages (incremental)
    if pages:
        print("📄 Syncing pages...")
        for i, page in enumerate(pages, 1):
            title, status = sync_page(page, index)
            if status.startswith("saved"):
                stats["saved"] += 1
            elif status.startswith("unchanged"):
                stats["unchanged"] += 1
            elif status.startswith("skipped"):
                stats["skipped"] += 1
            else:
                stats["errors"] += 1
            print(f"  [{i}/{len(pages)}] {status}")
        print()

    # Step 3: Sync databases (incremental)
    if databases:
        print("🗄️  Syncing databases...")
        for i, db in enumerate(databases, 1):
            title, status = sync_database(db, index)
            if status.startswith("saved"):
                stats["saved"] += 1
            else:
                stats["errors"] += 1
            print(f"  [{i}/{len(databases)}] {status}")
        print()

    # Save updated index
    save_index(index)

    # Summary
    print("╔══════════════════════════════════════════════╗")
    print("║                    Summary                   ║")
    print("╚══════════════════════════════════════════════╝")
    print(f"  ✅ New/Changed: {stats['saved']} items")
    print(f"  ⏭️  Unchanged:   {stats['unchanged']} items")
    print(f"  ⏭️  Skipped:     {stats['skipped']} items")
    print(f"  ❌ Errors:      {stats['errors']} items")
    print(f"  🗄️  Databases:   {stats['databases_queried']} queried")
    print(f"  📁 Output:      {NOTION_DIR}")
    print()

    if stats["saved"] > 0:
        append_to_daily_log([f"Notion sync: {stats['saved']} new/changed items"])
        print(f"📝 Logged to {DAILY_PATH}")


if __name__ == "__main__":
    main()
