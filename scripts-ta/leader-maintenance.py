#!/usr/bin/env python3
"""leader-maintenance.py — keep the thought-leader + industry-leader config fresh.

Probes every feed (blog/substack) in thought-leaders.json, re-verifies X/LinkedIn
handles, and flags dead/rotated sources so the daily thought-leader scan never
silently degrades (the 2026-07-26 lesson: 49 of 77 feeds were dead while reports
still looked healthy).

Does NOT mutate feeds blindly — it reports, and optionally marks genuinely-dead
feeds. Writing a feed to `_known_dead` is the only auto-mutation (reversible).

Run weekly (e.g. Sun 10:30) via Hermes cron. Writes System/leader-maintenance-health.json.
"""
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from hermes_feeds import fetch_rss, write_health

CONFIG = Path.home() / ".hermes" / "thought-leaders.json"
HEALTH_NAME = "leader-maintenance"
# Only move a feed to _known_dead if it fails BOTH a fresh probe and this many
# consecutive prior runs flagged it (avoid a transient outage killing a source).
FLAG_THRESHOLD = 2


def load_cfg():
    if not CONFIG.exists():
        print("🔴 thought-leaders.json missing")
        return None
    return json.loads(CONFIG.read_text())


def http_code(url):
    """Return HTTP status for a URL (0 on network error)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def probe_feed(url):
    """Return (ok: bool, detail: str) for an RSS feed."""
    items, err = fetch_rss(url, max_items=1)
    if err:
        return False, err
    return True, f"{len(items)} items" if items else "0 items (ok, quiet)"


def probe_social(url):
    """Return (ok: bool, code) for an X/LinkedIn handle. 200/999 = OK."""
    code = http_code(url)
    if code in (200, 999):
        return True, code
    return False, code


def main():
    cfg = load_cfg()
    if not cfg:
        return 1
    leaders = cfg["thought_leaders"]
    dead_flag = cfg.get("_dead_flag", {})

    # Tally failures this run
    failures = {"feeds": [], "social": []}
    total_feeds = 0
    total_social = 0
    ok_feeds = 0
    ok_social = 0
    newly_dead = []

    today = datetime.now().isoformat()

    for cat, domain in leaders.items():
        for leader in domain.get("leaders", []):
            name = leader.get("name", "?")
            # --- feeds ---
            for key in ("blog", "substack"):
                url = leader.get(key)
                if not url:
                    continue
                total_feeds += 1
                ok, detail = probe_feed(url)
                if ok:
                    ok_feeds += 1
                    dead_flag.pop(url, None)
                else:
                    failures["feeds"].append({"name": name, "url": url, "detail": detail})
                    prev = dead_flag.get(url, 0)
                    dead_flag[url] = prev + 1
                    if prev + 1 >= FLAG_THRESHOLD:
                        newly_dead.append({"name": name, "url": url, "detail": detail})
            # --- social ---
            for key in ("x", "linkedin"):
                url = leader.get(key)
                if not url:
                    continue
                total_social += 1
                ok, code = probe_social(url)
                if ok:
                    ok_social += 1
                else:
                    failures["social"].append({"name": name, "url": url, "code": code})

    # Auto-move feeds flagged N consecutive runs into _known_dead (reversible)
    known_dead = cfg.setdefault("_known_dead", {})
    moved = 0
    for item in newly_dead:
        if item["url"] not in known_dead:
            known_dead[item["url"]] = f"{item['detail']} (auto-flagged {today})"
            # remove from live config
            for cat2, dom2 in leaders.items():
                for l2 in dom2.get("leaders", []):
                    for key in ("blog", "substack"):
                        if l2.get(key) == item["url"]:
                            l2.pop(key, None)
            moved += 1

    cfg["_dead_flag"] = dead_flag
    cfg["_maintained"] = today
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))

    # Summary + health
    status = "ok" if ok_feeds == total_feeds else "degraded"
    if total_feeds and ok_feeds == 0:
        status = "all-feeds-failed"
    print(f"📡 Leader maintenance — {datetime.now():%Y-%m-%d %H:%M}")
    print(f"  Feeds: {ok_feeds}/{total_feeds} ok | Social: {ok_social}/{total_social} ok")
    if failures["feeds"]:
        print("  ⚠️ Feed failures:")
        for f in failures["feeds"]:
            print(f"    - {f['name']}: {f['url']} ({f['detail']})")
    if failures["social"]:
        print("  ⚠️ Social failures:")
        for f in failures["social"]:
            print(f"    - {f['name']}: {f['url']} (HTTP {f['code']})")
    if moved:
        print(f"  🗃️ Moved {moved} persistently-dead feed(s) to _known_dead")

    write_health(HEALTH_NAME, {
        "status": status,
        "feeds_ok": ok_feeds,
        "feeds_total": total_feeds,
        "social_ok": ok_social,
        "social_total": total_social,
        "feed_failures": failures["feeds"],
        "social_failures": failures["social"],
        "moved_to_known_dead": moved,
    })
    return 0 if status == "ok" else (1 if status == "all-feeds-failed" else 0)


if __name__ == "__main__":
    sys.exit(main())
