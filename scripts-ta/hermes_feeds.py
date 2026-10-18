#!/usr/bin/env python3
"""
hermes_feeds.py

Shared feed/IO helpers for the intelligence cron scripts.

Exists because `feedparser.parse(url, timeout=10)` is NOT a valid call —
feedparser has no `timeout` kwarg, so it raises TypeError. When that is
swallowed by a bare `except`, every feed silently returns zero items and the
report still gets written, looking healthy. That is exactly what happened to
thought-leader-scan.py between 2026-07-25 and 2026-07-26.

Rules enforced here:
1. Network timeouts are applied at the urllib layer, where they actually work.
2. Fetch failures are RETURNED, not swallowed — callers must surface them.
3. Every script writes a health JSON so a silent-zero run is visible.

Used by: thought-leader-scan, arxiv-digest, policy-tracker,
         podcast-transcribe, bias-balance-check, signal-noise
"""

import gzip
import json
import os
import platform
import re
import socket
import ssl
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import feedparser
except ImportError:  # pragma: no cover - feedparser is a hard dependency
    feedparser = None

VAULT_PATH = Path("/Users/admin/Documents/Obsidian Vault")
HEALTH_DIR = VAULT_PATH / "System"
HERMES_HOME = Path.home() / ".hermes"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 HermesIntelligence/1.0"
)
DEFAULT_TIMEOUT = 15
CYCLE = "2026-Q4"


# ── Networking ────────────────────────────────────────────────────────
class _Redirect308Handler(urllib.request.HTTPRedirectHandler):
    """
    Python 3.9's urllib does not follow HTTP 308 Permanent Redirect (added in
    3.11). Several feeds in this corpus (Vercel, Continuations, DAIR,
    DeepLearning.AI) answer 308, so without this they read as hard failures.
    """

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_301(req, fp, 301, msg, headers)

    https_error_308 = http_error_308


# Some hosts reject the browser UA but serve a plain fetcher, and vice versa.
# We try each in turn before declaring a feed dead.
_UA_FALLBACKS = [
    USER_AGENT,
    "feedparser/6.0 +https://github.com/kurtmckee/feedparser",
    "curl/8.4.0",
]

_OPENER = urllib.request.build_opener(_Redirect308Handler)


def http_get(url: str, timeout: int = DEFAULT_TIMEOUT, retries: int = 1) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Fetch a URL. Returns (body_bytes, error_string). Exactly one is non-None.

    The timeout is passed to urlopen, which is the only layer that honours it.
    On 403/406 we retry with alternate User-Agents before giving up.
    """
    last_err = None
    for ua in _UA_FALLBACKS:
        ua_rejected = False
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        "User-Agent": ua,
                        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                        "Accept-Encoding": "gzip, identity",
                    },
                )
                with _OPENER.open(req, timeout=timeout) as resp:
                    body = resp.read()
                    if resp.headers.get("Content-Encoding") == "gzip":
                        body = gzip.decompress(body)
                    return body, None
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code}"
                if e.code in (403, 406, 429):
                    ua_rejected = True
                    break  # a different User-Agent may well be accepted
                if e.code in (404, 410, 401):
                    return None, last_err  # permanently gone; no UA will help
            except urllib.error.URLError as e:
                last_err = f"URL error: {e.reason}"
                if "nodename nor servname" in str(e.reason):
                    return None, "DNS failure (host does not resolve)"
            except (socket.timeout, TimeoutError):
                last_err = f"timeout after {timeout}s"
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"

        # Only cycle to another User-Agent when the server actually rejected
        # THIS one. Retrying a timeout or a DNS failure under a new UA just
        # multiplies the wait: 3 UAs x 2 tries x 15s = 90s per dead feed, which
        # would make a 25-feed scan take half an hour.
        if not ua_rejected:
            break
    return None, last_err


def fetch_rss(url: str, max_items: int = 10, timeout: int = DEFAULT_TIMEOUT) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Fetch and parse an RSS/Atom feed.

    Returns (items, error). On failure items is empty and error explains why —
    callers MUST record the error rather than treating it as "no news today".
    """
    if feedparser is None:
        return [], "feedparser not installed"

    body, err = http_get(url, timeout=timeout)
    if body is None:
        return [], err

    try:
        feed = feedparser.parse(body)
    except Exception as e:
        return [], f"parse failed: {type(e).__name__}: {e}"

    if getattr(feed, "bozo", 0) and not feed.entries:
        return [], f"malformed feed: {getattr(feed, 'bozo_exception', 'unknown')}"

    feed_title = feed.feed.get("title", url) if hasattr(feed, "feed") else url
    items: List[Dict[str, Any]] = []
    for entry in feed.entries[:max_items]:
        summary = entry.get("summary", "") or entry.get("description", "")
        items.append(
            {
                "title": strip_html(entry.get("title", "(untitled)")),
                "url": entry.get("link", ""),
                "published": entry.get("published", entry.get("updated", "")),
                "published_parsed": entry.get("published_parsed", entry.get("updated_parsed")),
                "summary": strip_html(summary)[:800],
                "source": feed_title,
                "author": entry.get("author", ""),
                "enclosures": [e.get("href", "") for e in entry.get("enclosures", [])],
                "duration": entry.get("itunes_duration", ""),
            }
        )
    return items, None


def strip_html(text: str) -> str:
    """Remove tags and collapse whitespace/entities from feed HTML."""
    if not text:
        return ""
    import html as _html

    text = re.sub(r"<script.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def entry_datetime(item: Dict[str, Any]) -> Optional[datetime]:
    """Best-effort UTC datetime for a feed item."""
    parsed = item.get("published_parsed")
    if parsed:
        try:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    raw = (item.get("published") or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def is_recent(item: Dict[str, Any], days: int) -> bool:
    """True if the item is within `days` (unknown dates are kept)."""
    dt = entry_datetime(item)
    if dt is None:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(days=days)


# ── Deduplication ─────────────────────────────────────────────────────
class SeenStore:
    """
    URL/title dedup store with TTL pruning so the file cannot grow forever.

    Stored as {hash: iso_first_seen}.
    """

    def __init__(self, path: Path, ttl_days: int = 120):
        self.path = Path(path)
        self.ttl_days = ttl_days
        self.data: Dict[str, str] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text() or "{}")
            except (json.JSONDecodeError, OSError):
                self.data = {}

    @staticmethod
    def key(title: str, url: str) -> str:
        import hashlib

        basis = (url or "").strip().lower() or (title or "").strip().lower()
        return hashlib.md5(basis.encode()).hexdigest()

    def seen(self, title: str, url: str) -> bool:
        return self.key(title, url) in self.data

    def add(self, title: str, url: str):
        self.data[self.key(title, url)] = datetime.now().isoformat()

    def prune(self):
        cutoff = datetime.now() - timedelta(days=self.ttl_days)
        kept = {}
        for k, v in self.data.items():
            try:
                if datetime.fromisoformat(v) >= cutoff:
                    kept[k] = v
            except (ValueError, TypeError):
                kept[k] = datetime.now().isoformat()
        self.data = kept

    def save(self):
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2))


# ── Health reporting ──────────────────────────────────────────────────
def classify_status(new_items: int, raw_fetched: int, feeds_attempted: int,
                    feeds_failed: int) -> str:
    """
    Classify a run's outcome.

    Critical distinction: a scan that fetches plenty of entries but finds nothing
    NEW is healthy (dedup doing its job — normal on a second same-day run, or a
    weekend for arXiv). A scan that fetches NOTHING AT ALL is broken. Collapsing
    both into "zero items" makes the health check cry wolf and trains everyone to
    ignore it — which is how the original silent failure survived.
    """
    if feeds_attempted and feeds_failed >= feeds_attempted:
        return "all-feeds-failed"
    if raw_fetched == 0:
        return "zero-fetched"
    if new_items == 0:
        return "no-new-items"
    return "ok"


def write_health(name: str, payload: Dict[str, Any]) -> Path:
    """
    Write System/<name>-health.json, matching the market-trends convention.

    Always include feed_errors and item counts so a zero-item run is loud.
    """
    payload = {"timestamp": datetime.now().isoformat(), **payload}
    HEALTH_DIR.mkdir(parents=True, exist_ok=True)
    out = HEALTH_DIR / f"{name}-health.json"
    out.write_text(json.dumps(payload, indent=2))
    return out


def health_banner(total_items: int, errors: Dict[str, str], feeds_total: int) -> str:
    """Markdown block that makes fetch failures visible inside the note itself."""
    ok = feeds_total - len(errors)
    if total_items == 0:
        status = "🔴 **ZERO items captured** — treat this as a failure, not a quiet news day."
    elif errors:
        status = f"🟡 {len(errors)} of {feeds_total} feeds failed."
    else:
        status = "✅ All feeds responded."

    block = f"""## 🩺 Fetch Health

{status}

| Metric | Value |
|--------|-------|
| Feeds attempted | {feeds_total} |
| Feeds OK | {ok} |
| Feeds failed | {len(errors)} |
| Items captured | {total_items} |
"""
    if errors:
        block += "\n### Failed Feeds\n\n| Feed | Error |\n|------|-------|\n"
        for url, err in sorted(errors.items()):
            block += f"| `{url[:70]}` | {err} |\n"
    return block


# ── Vault helpers ─────────────────────────────────────────────────────
def parse_frontmatter(content: str) -> Dict[str, Any]:
    """Extract flat YAML frontmatter as a dict (same parser as flywheel-health-check)."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}
    fm: Dict[str, Any] = {}
    for line in match.group(1).split("\n"):
        if ":" in line and not line.startswith((" ", "-")):
            key, value = line.split(":", 1)
            fm[key.strip()] = value.strip().strip("\"'")
    return fm


# ── Authorship ────────────────────────────────────────────────────────
# One definition, resolved from the host. Hand-written literals had already
# fragmented into four spellings across the vault and seven scripts —
# `hermes-agent`, `ubuntu-server-agent-hermes`, `Mackbook-pro-hermes-agent`
# (typo) and `claude-code` — which is the same drift that gave `status` twenty
# spellings and silently split every filter that touched it.
MAC_AUTHOR = "macbook-pro-hermes-agent"
VM_AUTHOR = "ubuntu-server-agent-hermes"
LEGACY_AUTHOR = "hermes-agent"  # pre-split notes; host is unknowable, never guessed


def authorship_tag() -> str:
    """The authorship tag for the machine this is running on.

    `HERMES_AUTHORSHIP_TAG` overrides, for containers or a host not covered here.
    An unrecognised platform falls back to the generic legacy tag rather than
    claiming a machine it cannot verify.

    Note `claude-code` is NOT returned here — that marks a macOS Claude Code
    session, which is a different author from a Hermes cron on the same box, and
    is applied by that agent's own convention.
    """
    override = os.environ.get("HERMES_AUTHORSHIP_TAG", "").strip()
    if override:
        return override
    system = platform.system()
    if system == "Darwin":
        return MAC_AUTHOR
    if system == "Linux":
        return VM_AUTHOR
    return LEGACY_AUTHOR


def frontmatter_block(title: str, tags: List[str], extra: Optional[Dict[str, Any]] = None,
                      note_type: str = "raw", status: str = "unprocessed",
                      domain: str = "knowledge", stage: Optional[str] = None) -> str:
    """Standard vault frontmatter, conforming to Schema v2 (`System/SCHEMA.md`).

    Emits `type` (the lifecycle axis) rather than the deprecated `stage`. Every
    cron that writes notes goes through here, so this function — not the weekly
    linter — is what actually keeps the vault schema-compliant: the linter can
    only report drift a week later, whereas conforming at write time means the
    drift never happens.

    `stage=` is accepted as a deprecated alias for `note_type=` so existing
    callers keep working; it is never emitted.

    Authorship is stamped by `authorship_tag()`, resolved from the host.
    """
    if stage is not None and note_type == "raw":
        note_type = stage
    now = datetime.now()
    tag_list = list(dict.fromkeys(tags + [authorship_tag()]))
    lines = [
        "---",
        f'title: "{title}"' if ": " in title or title.endswith(":") else f"title: {title}",
        f"created: {now.strftime('%Y-%m-%d')}",
        f"type: {note_type}",
        f"domain: {domain}",
        f"status: {status}",
        f"cycle: {CYCLE}",
        f"tags: [{', '.join(tag_list)}]",
    ]
    for k, v in (extra or {}).items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"
