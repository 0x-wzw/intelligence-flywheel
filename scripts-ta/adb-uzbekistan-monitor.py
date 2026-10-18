#!/usr/bin/env python3
"""ADB Central Asia Semiconductor / E&E — RFI/EOI/RFP Monitor.

Watches the Asian Development Bank's tenders & consulting-opportunities pages
for new RFIs / EOIs / RFPs relevant to Central Asia's semiconductor and
electronics (E&E) sector — the Phase 2 national strategy work (Uzbekistan) and
the wider Central Asia corridor (Kazakhstan, Kyrgyzstan, Tajikistan,
Turkmenistan, Uzbekistan).

The ADB tenders page (https://www.adb.org/projects/tenders) is Cloudflare-
protected and JS-rendered via SearchStax, so a plain HTTP fetch returns 403 or
an empty shell. We render it through Firecrawl with a JS wait, which returns the
actual result items (Status, Deadline, Title, Country, Sector, Posting Date,
Notice Type).

Runs daily via Hermes cron. When a NEW relevant notice appears it:
  - prints an alert to stdout (delivered to Telegram by the cron runner), and
  - appends the notice to a dated capture in Knowledge/00-Raw/.
"""
import json
import os
import re
import sys
import html as _html
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from hermes_feeds import frontmatter_block, write_health
from dedup import SeenUrls

VAULT = Path("/Users/admin/Documents/Obsidian Vault")
RAW = VAULT / "Knowledge" / "00-Raw"
HEALTH_NAME = "adb-central-asia-monitor"

# Central Asia economies to watch (ADB member economies).
CENTRAL_ASIA = [
    "Kazakhstan",
    "Kyrgyz Republic",   # ADB's name for Kyrgyzstan
    "Kyrgyzstan",
    "Tajikistan",
    "Turkmenistan",
    "Uzbekistan",
]

# ADB tenders page, newest-closing first. We query per-country below.
TENDERS_URL = (
    "https://www.adb.org/projects/tenders?searchstax[query]={country}"
    "&searchstax[page]=1&searchstax[order]=ds_date_closing%20desc"
)

# E&E / semiconductor relevance keywords (case-insensitive substring match on
# the notice title + sector). A notice is "relevant" if it matches any of these.
# These are STRICT — the user wants semiconductor / electronics (E&E) work, not
# generic "infrastructure", "digital", "energy" or "policy" notices (which match
# almost every ADB tender and would flood the alert with false positives).
RELEVANCE = [
    "semiconductor", "electronics", "e&e", "e/e", "chip", "wafer", "fab",
    "integrated circuit", "ic design", "osat", "assembly and test",
    "electrical", "electronic", "mineral", "supply chain", "value chain",
    "manufacturing", "industrial",
]

# Notice types that indicate a solicitation (vs. an award or archive).
SOLICITATION_TYPES = {"firm", "individual", "advance notice", "general procurement notice"}


def _firecrawl_key():
    """Resolve the Firecrawl API key (env, then ~/.hermes/.env)."""
    key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if key:
        return key
    env = Path.home() / ".hermes" / ".env"
    if env.exists():
        for line in env.read_text(errors="ignore").splitlines():
            if line.startswith("FIRECRAWL_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _render_tenders(url: str) -> str:
    """Render the ADB tenders page via Firecrawl (JS wait) and return raw HTML."""
    key = _firecrawl_key()
    if not key:
        return ""
    payload = json.dumps({
        "url": url,
        "formats": ["rawHtml"],
        "actions": [{"type": "wait", "milliseconds": 8000}],
    }).encode()
    req = urllib.request.Request(
        "https://api.firecrawl.dev/v1/scrape",
        data=payload,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=70)
        data = json.loads(resp.read())
        return (data.get("data", {}) or {}).get("rawHtml", "") or ""
    except Exception:
        return ""


def _parse_results(html: str):
    """Parse ADB tender result items from rendered HTML.

    Each result is wrapped in a
    <div data-searchstax-unique-result-id="..." class="searchstax-result-item-link-wrapping">
    containing the title link plus Status, Deadline, Country/Economy, Sector,
    Posting Date, and Notice Type spans.
    """
    results = []
    # Split on each result-item wrapper.
    for m in re.finditer(
        r'<div[^>]*data-searchstax-unique-result-id="[^"]*"[^>]*class="searchstax-result-item-link-wrapping"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        html, re.S,
    ):
        block = m.group(1)

        tm = re.search(
            r'<a href="(https://selfservice\.adb\.org/[^"]+)"[^>]*class="searchstax-search-result-title">(.*?)</a>',
            block, re.S,
        )
        if not tm:
            continue
        url = _html.unescape(tm.group(1))
        title = _html.unescape(re.sub(r"<[^>]+>", "", tm.group(2))).strip()

        def _field(label):
            # Match the label (handling &nbsp; between words), then capture
            # everything up to the next <strong> or end of block, strip tags
            # and unescape entities. Values may sit inside a nested <span>.
            # Between words allow either a space or the &nbsp; entity.
            spaced = re.escape(label).replace(r"\ ", r"(?:\s|&nbsp;)*")
            fm = re.search(
                r"<strong>\s*" + spaced + r"\s*:?\s*</strong>(.*?)(?=<strong>|</div>|\Z)",
                block, re.I | re.S,
            )
            if not fm:
                return ""
            val = re.sub(r"<[^>]+>", "", fm.group(1))
            return _html.unescape(val).strip()

        results.append({
            "title": title,
            "url": url,
            "status": _field("Status"),
            "deadline": _field("Deadline"),
            "country": _field("Country/Economy"),
            "sector": _field("Sector"),
            "posting_date": _field("Posting Date"),
            "notice_type": _field("Notice Type"),
        })
    return results


def _is_relevant(item: dict) -> bool:
    """True if the notice is a solicitation relevant to Central Asia E&E/semiconductor.

    A notice is relevant if:
      - its country is a Central Asia economy (or "Regional", which often spans
        multiple countries including Central Asia), AND
      - its title/sector matches an E&E/semiconductor keyword, AND
      - it is a solicitation (not an award/archive).
    """
    country = item.get("country", "").lower()
    in_region = any(c.lower() in country for c in CENTRAL_ASIA) or "regional" in country
    if not in_region:
        return False

    text = " ".join([
        item.get("title", ""),
        item.get("sector", ""),
        item.get("notice_type", ""),
    ]).lower()
    if not any(k in text for k in RELEVANCE):
        return False
    # Only surface solicitations, not awards/archives.
    ntype = item.get("notice_type", "").lower()
    if ntype and ntype not in SOLICITATION_TYPES:
        return False
    return True


def _frontmatter():
    return frontmatter_block(
        title=f"ADB Central Asia E&E Monitor — {datetime.now():%Y-%m-%d}",
        tags=["raw", "adb", "central-asia", "uzbekistan", "semiconductor", "eoi", "rfp", "tenders"],
        note_type="raw",
        domain="knowledge",
    ) + "\n"


def main() -> int:
    print(f"🔍 ADB Central Asia E&E Monitor — {datetime.now():%Y-%m-%d}")

    # Render each Central Asia country's tenders page and merge results.
    all_results = []
    country_counts = {}
    for country in CENTRAL_ASIA:
        url = TENDERS_URL.format(country=urllib.parse.quote(country))
        html = _render_tenders(url)
        if not html:
            print(f"  ⚠️  Failed to render {country} tenders page")
            continue
        results = _parse_results(html)
        country_counts[country] = len(results)
        all_results.extend(results)

    if not all_results:
        print("  🔴 Failed to render any ADB tenders page (Firecrawl unavailable/error)")
        write_health(HEALTH_NAME, {
            "status": "all-feeds-failed",
            "error": "firecrawl render failed",
            "new": 0,
        })
        return 1

    # Dedup across countries (a "Regional" notice may appear under several).
    seen_urls = set()
    unique = []
    for r in all_results:
        if r["url"] and r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            unique.append(r)

    relevant = [r for r in unique if _is_relevant(r)]
    print(f"  Parsed {len(unique)} unique Central Asia notices "
          f"({', '.join(f'{c}:{n}' for c, n in country_counts.items())}), "
          f"{len(relevant)} E&E-relevant")

    seen = SeenUrls()
    seen.load()
    new_items = []
    for r in relevant:
        if r["url"] and seen.is_new(r["url"], r.get("posting_date", ""), r.get("title", "")):
            new_items.append(r)
            seen.mark(r["url"], r.get("title", ""))
    seen.save()

    print(f"  {len(new_items)} NEW relevant notices")

    # Save a dated capture (merge-safe) so the daily loop can see it.
    today = datetime.now().strftime("%Y-%m-%d")
    path = RAW / f"adb-central-asia-monitor-{today}.md"
    lines = [f"# 🔍 ADB Central Asia E&E Monitor — {today}\n"]
    if new_items:
        lines.append("## 🆕 New Relevant Notices\n")
        for it in new_items:
            lines.append(f"- [{it['title']}]({it['url']})")
            lines.append(f"  - Country: {it['country']} · Status: {it['status']} · Deadline: {it['deadline']}")
            lines.append(f"  - Sector: {it['sector']} · Type: {it['notice_type']}")
            lines.append(f"  - Posting: {it['posting_date']}")
        lines.append("")
    else:
        lines.append("_No new E&E-relevant notices today._\n")
    lines.append(f"\n---\n*Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append(f"*Source: [ADB Tenders](https://www.adb.org/projects/tenders)*")
    report = "\n".join(lines)

    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="ignore")
        new_lines = [l for l in report.splitlines() if l.startswith("- [") and l not in existing]
        if new_lines:
            existing = existing.rstrip() + "\n" + "\n".join(new_lines) + "\n"
            path.write_text(existing, encoding="utf-8")
    else:
        path.write_text(_frontmatter() + report, encoding="utf-8")

    write_health(HEALTH_NAME, {
        "status": "ok" if new_items else "no-new-items",
        "parsed": len(unique),
        "by_country": country_counts,
        "relevant": len(relevant),
        "new": len(new_items),
        "capture": str(path.relative_to(VAULT)),
    })

    # Cron log for traceability.
    log_dir = Path(os.path.expanduser("~/.hermes/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "adb-central-asia-monitor.log", "a") as f:
        f.write(f"{datetime.now().isoformat()} | parsed={len(unique)} "
                f"relevant={len(relevant)} new={len(new_items)}\n")

    # Telegram-friendly alert (stdout is delivered verbatim by the cron runner).
    if new_items:
        print(f"\n🚨 ADB Central Asia E&E — {len(new_items)} new solicitation(s)")
        for it in new_items:
            print(f"  • {it['title']}")
            print(f"    {it['url']}")
            print(f"    Country: {it['country']} · Deadline: {it['deadline']} · Sector: {it['sector']}")
    else:
        print("  ✅ No new E&E-relevant ADB notices for Central Asia.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
