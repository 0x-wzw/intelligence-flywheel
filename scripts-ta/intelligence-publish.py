#!/usr/bin/env python3
"""intelligence-publish.py — DISTILLED-INSIGHTS publisher for the intelligence flywheel.

Intelligence flywheel (2026-10-08 redesign):

  RAW captures        → Obsidian vault ONLY  (Knowledge/00-Raw/ via producer crons,
                        schema v2 frontmatter from hermes_feeds.frontmatter_block)
  DISTILLED insights  → Notion               (this script: ONE page per day in the
                        Market Research database — synthesis + top signals, never
                        a raw article dump)
  LINK share          → Telegram + Lark/Feishu (intelligence-notify.py, next cron slot)

Rewritten from the v2 "dual dump" design (full article lists to BOTH Cloudflare KV
and Notion). Rationale: daily raw captures belong to the vault knowledge flywheel
(Schema v2, System/SCHEMA.md); Notion is the distilled layer. A raw dump in Notion
duplicated the vault and broke the lifecycle (raw notes there never distill).

What this script does:
  1. Reads today's vault captures via the single source of truth
     (intelligence_source_of_truth.build_payload) — same collector the loop uses.
  2. Reads the day's Daily Intelligence Brief (Knowledge/20-Reports/) for the
     cross-vertical synthesis. Falls back to per-producer synth text if the
     brief LLM pass failed.
  3. Publishes ONE distilled page to Notion (Market Research DB), idempotent:
     re-running on the same day UPDATES the existing page instead of duplicating.
  4. Mirrors the distilled payload to Cloudflare KV (intelligence.teoh.my dashboard
     stays a live read-view of the distilled layer).
  5. Writes the Notion page URL to ~/.hermes/logs/notion-daily-page-url.txt —
     consumed by intelligence-notify.py for the Telegram/Lark link share.
  6. Records health to System/intelligence-publish-health.json.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from intelligence_source_of_truth import build_payload, publication_gate  # single source of truth

CF_ACCOUNT_ID = "fcd22318e9db7ef66952e112114cd8ae"
CF_KV_ID = "07079ee6e9d44315b117d966fb9f8477"
NOTION_DATABASE_ID = "39e05e4a-7c41-81a3-815f-ff9c2e862cc2"  # Market Research
VAULT = Path("/Users/admin/Documents/Obsidian Vault")
URL_FILE = Path.home() / ".hermes/logs/notion-daily-page-url.txt"
NOTION_VER = "2022-06-28"
MAX_SYNTH_BLOCKS = 6        # executive-synthesis paragraph cap
TOP_ITEMS_PER_CATEGORY = 3  # distilled = top signals, not the firehose


# ── Credentials ─────────────────────────────────────────────────────────
def _env():
    vals = {}
    try:
        for line in Path.home().joinpath(".hermes/.env").read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return vals


def _notion_token():
    env = _env()
    tok = env.get("NOTION_API_TOKEN", "") or env.get("NOTION_API_KEY", "")
    if tok and "..." not in tok and len(tok) > 40:
        return tok
    return ""


# ── Notion helpers ──────────────────────────────────────────────────────
def _notion_api(method, path, body=None, token=None):
    req = urllib.request.Request(
        f"https://api.notion.com/v1/{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "Notion-Version": NOTION_VER},
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise RuntimeError(f"Notion {method}/{path} -> {e.code}: {detail}") from None


def _rt(text, link=None, bold=False):
    ann = {"bold": True} if bold else None
    text = text[:1900]
    d = {"type": "text", "text": {"content": text}}
    if link:
        d["text"]["link"] = {"url": link}
    if ann:
        d["annotations"] = ann
    return d


def _blocks_from_text(text, cap=MAX_SYNTH_BLOCKS):
    """Split prose into Notion paragraph blocks at paragraph boundaries."""
    blocks, buf = [], ""
    for para in [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]:
        if len(buf) + len(para) + 2 > 1800 and buf:
            blocks.append({"object": "block", "type": "paragraph",
                           "paragraph": {"rich_text": [_rt(buf)]}})
            buf, cap = para, cap
            if len(blocks) >= cap:
                return blocks
        else:
            buf = f"{buf}\n{para}" if buf else para
    if buf and len(blocks) < cap:
        blocks.append({"object": "block", "type": "paragraph",
                       "paragraph": {"rich_text": [_rt(buf)]}})
    return blocks


def find_existing_page(day, token):
    """Idempotency: return the existing page id+url for this day, or None."""
    try:
        res = _notion_api("POST", f"databases/{NOTION_DATABASE_ID}/query", {
            "filter": {"and": [
                {"property": "Date", "date": {"equals": day}},
                {"property": "Category", "select": {"equals": "Daily Intelligence"}},
            ]},
            "page_size": 5,
        }, token=token)
        for r in res.get("results", []):
            return {"id": r["id"], "url": r.get("url", "")}
    except Exception as e:
        print(f"  ⚠ idempotency lookup failed (will try create): {e}")
    return None


def read_daily_brief(day):
    """The day's cross-vertical synthesis from Knowledge/20-Reports/, if good."""
    p = VAULT / "Knowledge" / "20-Reports" / f"Daily-Intelligence-Brief-{day}.md"
    if not p.exists():
        return None
    body = p.read_text()
    if body.startswith("---"):  # strip frontmatter
        body = body.split("---", 2)[2] if body.count("---") >= 2 else body
    body = re.sub(r"!\[\[[^\]]*\]\]", "", body)          # obsidian embeds
    body = re.sub(r"\[\[([^\]|]+\|)?([^\]]+)\]\]", r"\2", body)  # wikilinks → label
    if "LLM synthesis unavailable" in body or len(body.strip()) < 400:
        return None
    return body.strip()


def collect_fallback_synth(day):
    """Per-producer synth text from the collector (fallback when brief failed)."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_dil_loop", str(_SCRIPTS_DIR / "daily-intelligence-loop.py"))
        loop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loop)
        collected = loop.collect(day)
        return {label: (data or {}).get("synth") for label, data in collected.items()}
    except Exception:
        return {}


def build_distilled_blocks(payload, token):
    children = []
    brief = read_daily_brief(payload["date"])
    synth_map = {} if brief else collect_fallback_synth(payload["date"])

    if brief:
        children.append({"object": "block", "type": "heading_2",
                         "heading_2": {"rich_text": [_rt("🧭 Executive Synthesis")]}})
        children.extend(_blocks_from_text(brief))
    else:
        fs = [s for s in synth_map.values() if s]
        if fs:
            children.append({"object": "block", "type": "heading_2",
                             "heading_2": {"rich_text": [_rt("🧭 Synthesis (per-vertical)")]}})
            children.extend(_blocks_from_text("\n\n".join(fs))[:MAX_SYNTH_BLOCKS])
        else:
            children.append({"object": "block", "type": "paragraph",
                             "paragraph": {"rich_text": [
                                 _rt("⚠ LLM synthesis unavailable this run — signals only. "
                                     "Raw captures carry the detail.")]}})

    for cat in payload["categories"]:
        arts = cat.get("articles") or []
        if not arts:
            continue
        children.append({"object": "block", "type": "heading_2",
                         "heading_2": {"rich_text": [
                             _rt(f"{cat['name']} — {len(arts)} signals")]}})
        label = cat["name"].split(" ", 1)[-1]  # strip emoji prefix
        synth = synth_map.get(label)
        if synth:
            children.append({"object": "block", "type": "paragraph",
                             "paragraph": {"rich_text": [_rt(synth[:900])]}})
        for a in arts[:TOP_ITEMS_PER_CATEGORY]:
            children.append({"object": "block", "type": "bulleted_list_item",
                             "bulleted_list_item": {"rich_text": [
                                 _rt(a["title"], link=a.get("url") or None)]}})

    children.append({"object": "block", "type": "divider", "divider": {}})
    children.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
        _rt("🗂 Raw captures: Obsidian vault → Knowledge/00-Raw/ (schema v2, unprocessed layer). "),
        _rt("This page is the distilled layer of the intelligence flywheel."),
    ]}})
    return children


def replace_page_children(page_id, children, token):
    """Idempotent content update: archive existing blocks, then append new ones.

    Notion's PATCH page/children APPENDS blocks — without clearing first, every
    re-run would duplicate the whole page body. Children are fetched paginated
    and archived (soft-delete) so the page keeps its URL and history.
    """
    try:
        existing = []
        cursor = None
        while True:
            q = f"blocks/{page_id}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            res = _notion_api("GET", q, token=token)
            existing.extend(res.get("results", []))
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
        for b in existing:
            try:
                _notion_api("PATCH", f"blocks/{b['id']}", {"archived": True}, token=token)
            except Exception:
                pass  # a stuck block must never block the republish
        print(f"  ♻ cleared {len(existing)} existing blocks")
    except Exception as e:
        print(f"  ⚠ block clear failed (appending anyway): {e}")


def publish_notion(payload):
    token = _notion_token()
    if not token:
        print("  ⚠ No Notion token — skipping Notion")
        return False, ""
    day = payload["date"]
    title = f"Daily Intelligence — {day}"
    children = build_distilled_blocks(payload, token)
    properties = {
        "Title": {"title": [{"text": {"content": title}}]},
        "Date": {"date": {"start": day}},
        "Category": {"select": {"name": "Daily Intelligence"}},
        "Source": {"url": "https://intelligence.teoh.my/"},
    }
    try:
        existing = find_existing_page(day, token)
        if existing:
            replace_page_children(existing["id"], children, token)
            # Update properties on the page, then append content via the block
            # endpoint — the block-children PATCH is the reliable append route
            # (page-level PATCH with children returned 400 in practice).
            _notion_api("PATCH", f"pages/{existing['id']}",
                        {"properties": properties}, token=token)
            _notion_api("PATCH", f"blocks/{existing['id']}/children",
                        {"children": children[:100]}, token=token)
            print(f"  ♻ Updated existing distilled page (idempotent): {existing['url']}")
            return True, existing["url"]
        page = _notion_api("POST", "pages", {
            "parent": {"database_id": NOTION_DATABASE_ID},
            "properties": properties, "children": children[:100]}, token=token)
        print(f"  ✅ Distilled Notion page: {page.get('url', '')}")
        return True, page.get("url", "")
    except Exception as e:
        print(f"  ⚠ Notion push failed: {e}")
        return False, ""


# ── Cloudflare KV (dashboard mirror of the distilled payload) ───────────
def _kv_url(key):
    acct, _ = _cf_creds()
    return (f"https://api.cloudflare.com/client/v4/accounts/{acct}"
            f"/storage/kv/namespaces/{CF_KV_ID}/values/{key}")


def _cf_creds():
    env = _env()
    return env.get("CLOUDFLARE_ACCOUNT_ID", ""), (
        env.get("CLOUDFLARE_API_TOKEN") or env.get("CF_API_TOKEN", ""))


def kv_put(key, payload):
    acct, tok = _cf_creds()
    if not acct or not tok:
        return False
    req = urllib.request.Request(
        _kv_url(key), data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        method="PUT")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return bool(json.loads(resp.read()).get("success"))
    except Exception as e:
        print(f"  ⚠ KV put {key} failed: {e}")
        return False


def publish_kv(payload, day):
    print("📡 Cloudflare KV (dashboard mirror)...")
    ok = kv_put("daily-insights", payload)
    ok_dated = kv_put(f"daily-insights-{day}", payload)
    print(f"  ✅ daily-insights: {ok} | daily-insights-{day}: {ok_dated}")
    return ok and ok_dated


# ── Main ────────────────────────────────────────────────────────────────
def main():
    day = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
    print(f"📡 Intelligence distilled publish — {day}")
    payload = build_payload(day)
    print(f"  Categories: {payload['summary']['total_categories']}, "
          f"Signals: {payload['summary']['total_articles']}")
    if not payload["categories"]:
        print("  No categories found — nothing to publish.")
        return 1
    publishable, absent = publication_gate(payload)
    if not publishable:
        print("  🚨 STOP-SHIP: withholding daily publication; insufficient valid commercial coverage.")
        print("  Missing core verticals: " + ", ".join(absent))
        return 2

    kv_ok = publish_kv(payload, day)
    notion_ok, notion_url = publish_notion(payload)

    if notion_ok and notion_url:
        URL_FILE.parent.mkdir(parents=True, exist_ok=True)
        URL_FILE.write_text(notion_url)

    # Human-in-the-loop sign-off footer (delivered to bot chat via cron stdout)
    print()
    print("👁 FINAL REVIEW — distilled insights published. Sign off via:")
    print(f"🔗 Notion (distilled insights): {notion_url or '⚠ unavailable'}")
    print(f"🔗 Dashboard mirror: https://intelligence.teoh.my/  ({day})")
    print("🗂 Raw captures: Obsidian vault — Knowledge/00-Raw/ (vault-only by design)")
    top = []
    for cat in payload["categories"][:5]:
        if cat.get("articles"):
            top.append(f"• {cat['name']}: {len(cat['articles'])} — top: {cat['articles'][0]['title']}")
    for line in top:
        print("  " + line)

    health = {
        "status": "ok" if (kv_ok or notion_ok) else "failed",
        "flywheel": "vault-raw → notion-distilled → notify(tg+lark)",
        "day": day,
        "total_articles": payload["summary"]["total_articles"],
        "total_categories": payload["summary"]["total_categories"],
        "kv": kv_ok,
        "notion": notion_ok,
        "notion_url": notion_url,
        "notified": False,  # intelligence-notify.py flips this after link share
        "timestamp": datetime.now().isoformat(),
    }
    health_dir = VAULT / "System"
    health_dir.mkdir(parents=True, exist_ok=True)
    (health_dir / "intelligence-publish-health.json").write_text(json.dumps(health, indent=2))
    return 0 if (kv_ok or notion_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
