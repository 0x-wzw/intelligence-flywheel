#!/usr/bin/env python3
"""
headless_fetcher.py

Headless browser-based fetching for sources that require JavaScript rendering.
Uses Playwright (Chromium headless) with a single shared browser session.

Sources that consistently return empty are removed to avoid wasting time.
Only sources that actually produce results are kept.

Falls back gracefully to HTTP GET if Playwright is unavailable.
"""
import json
import os
import re
import html
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from multi_source_fetcher import _http_get, _extract_summary, USER_AGENT

# ── Playwright availability ───────────────────────────────────────────
_PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass

BROWSER_TIMEOUT = 10000  # 10 seconds per page


class HeadlessSession:
    """A single shared headless browser session.
    
    Usage:
        with HeadlessSession() as session:
            html = session.fetch(url)
            items = session.scrape_hbr()
    """
    
    def __init__(self):
        self._p = None
        self._browser = None
        self._context = None
        self._page = None
    
    def __enter__(self):
        if not _PLAYWRIGHT_AVAILABLE:
            return self
        try:
            self._p = sync_playwright().start()
            self._browser = self._p.chromium.launch(headless=True)
            self._context = self._browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(BROWSER_TIMEOUT)
        except Exception:
            self._cleanup()
        return self
    
    def __exit__(self, *args):
        self._cleanup()
    
    def _cleanup(self):
        try:
            if self._page:
                self._page.close()
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._p:
                self._p.stop()
        except Exception:
            pass
        self._p = None
        self._browser = None
        self._context = None
        self._page = None
    
    @property
    def available(self) -> bool:
        return _PLAYWRIGHT_AVAILABLE and self._page is not None
    
    def fetch(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """Fetch a URL using the shared browser session. Returns (html, error)."""
        if not self.available:
            return _http_get(url, timeout=BROWSER_TIMEOUT // 1000)
        try:
            response = self._page.goto(url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT)
            if response and response.ok:
                return self._page.content(), None
            elif response:
                return None, f"HTTP {response.status}"
            else:
                return None, "no response"
        except Exception as e:
            return None, f"browser error: {type(e).__name__}: {e}"
    
    def scrape_links(self, url: str, pattern: str, base_url: str = "",
                     min_title_len: int = 20, max_results: int = 5,
                     source_name: str = "") -> List[Dict]:
        """Scrape a page for links matching a regex pattern.
        
        Args:
            url: Page to scrape
            pattern: Regex with groups (url_path, title)
            base_url: Base URL to prepend to relative paths
            min_title_len: Minimum title length to include
            max_results: Max items to return
            source_name: Source label for items
        """
        html_content, err = self.fetch(url)
        if err or not html_content:
            return []
        results = []
        seen = set()
        matches = re.findall(pattern, html_content, re.DOTALL)
        for url_path, title in matches:
            clean_title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            if clean_title in seen or len(clean_title) < min_title_len:
                continue
            seen.add(clean_title)
            full_url = f"{base_url}{url_path}" if url_path.startswith("/") else url_path
            results.append({
                "title": clean_title,
                "url": full_url,
                "date": datetime.now().isoformat(),
                "summary": "",
                "source": source_name,
            })
            if len(results) >= max_results:
                break
        return results

    def scrape_sitemap(self, sitemap_url: str, source_name: str = "",
                       url_filter: str = "", max_results: int = 5,
                       min_title_len: int = 15) -> List[Dict]:
        """Scrape a sitemap.xml for article URLs, then fetch titles headlessly.

        Some sources (IEA, WGSN) block plain-HTTP RSS but serve a sitemap. We
        read the sitemap (optionally filtered by a URL substring), then visit the
        newest article URLs with the browser to grab their <title> tags.
        """
        import urllib.request
        # Fetch the sitemap via plain HTTP (it serves without a browser).
        try:
            req = urllib.request.Request(sitemap_url, headers={"User-Agent": USER_AGENT})
            resp = urllib.request.urlopen(req, timeout=BROWSER_TIMEOUT // 1000)
            xml = resp.read().decode("utf-8", "ignore")
        except Exception:
            return []
        # Collect <loc> URLs from a urlset (handle both urlset and sitemapindex).
        locs = re.findall(r"<loc>\s*(https?://[^<]+?)\s*</loc>", xml, re.DOTALL)
        if url_filter:
            locs = [l for l in locs if url_filter in l]
        results = []
        seen = set()
        for loc in locs[:max_results * 2]:
            if loc in seen:
                continue
            seen.add(loc)
            # Visit the page to extract its <title> (JS may be needed).
            html_content, err = self.fetch(loc)
            if err or not html_content:
                continue
            m = re.search(r"<title[^>]*>(.*?)</title>", html_content, re.DOTALL | re.IGNORECASE)
            title = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip() if m else ""
            if len(title) < min_title_len:
                continue
            results.append({
                "title": title,
                "url": loc,
                "date": datetime.now().isoformat(),
                "summary": "",
                "source": source_name,
            })
            if len(results) >= max_results:
                break
        return results


# ── Source: Harvard Business Review ────────────────────────────────────
def fetch_hbr(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape HBR latest articles."""
    return session.scrape_links(
        "https://hbr.org/",
        r'<a[^>]*href="(/\d{4}/\d{2}/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://hbr.org",
        min_title_len=25,
        max_results=max_results,
        source_name="Harvard Business Review",
    )


# ── Source: Accenture ──────────────────────────────────────────────────
def fetch_accenture(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape Accenture latest research."""
    return session.scrape_links(
        "https://www.accenture.com/us-en/insights",
        r'<a[^>]*href="(/us-en/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://www.accenture.com",
        min_title_len=20,
        max_results=max_results,
        source_name="Accenture",
    )


# ── Source: Deloitte ─────────────────────────────────────────────────
def fetch_deloitte(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape Deloitte latest insights."""
    return session.scrape_links(
        "https://www.deloitte.com/insights/",
        r'<a[^>]*href="(/[^"]*insights[^"]*)"[^>]*>([^<]+)</a>',
        base_url="https://www.deloitte.com",
        min_title_len=20,
        max_results=max_results,
        source_name="Deloitte",
    )


# ── Source: Stanford GSB ─────────────────────────────────────────────
def fetch_stanford_gsb(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape Stanford GSB latest insights."""
    return session.scrape_links(
        "https://www.gsb.stanford.edu/insights",
        r'<a[^>]*href="(/insights/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://www.gsb.stanford.edu",
        min_title_len=20,
        max_results=max_results,
        source_name="Stanford GSB",
    )


# ── Source: London Business School ────────────────────────────────────
def fetch_lbs(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape London Business School latest thinking."""
    return session.scrape_links(
        "https://www.london.edu/think",
        r'<a[^>]*href="(/think/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://www.london.edu",
        min_title_len=20,
        max_results=max_results,
        source_name="London Business School",
    )


# ── Source: Yale Insights ─────────────────────────────────────────────
def fetch_yale(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape Yale Insights latest articles."""
    return session.scrape_links(
        "https://insights.som.yale.edu/",
        r'<a[^>]*href="(/insights/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://insights.som.yale.edu",
        min_title_len=20,
        max_results=max_results,
        source_name="Yale Insights",
    )


# ── Source: HBS Working Knowledge ────────────────────────────────────
def fetch_hbs(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape HBS Working Knowledge latest articles."""
    return session.scrape_links(
        "https://hbswk.hbs.edu/",
        r'<a[^>]*href="(/item/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://hbswk.hbs.edu",
        min_title_len=20,
        max_results=max_results,
        source_name="HBS Working Knowledge",
    )


# ── Source: INSEAD Knowledge ─────────────────────────────────────────
def fetch_insead(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape INSEAD Knowledge latest articles."""
    return session.scrape_links(
        "https://knowledge.insead.edu/",
        r'<a[^>]*href="(/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://knowledge.insead.edu",
        min_title_len=20,
        max_results=max_results,
        source_name="INSEAD Knowledge",
    )


# ── Source: Kellogg Insight ───────────────────────────────────────────
def fetch_kellogg(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape Kellogg Insight latest articles."""
    return session.scrape_links(
        "https://insight.kellogg.northwestern.edu/",
        r'<a[^>]*href="(/article/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://insight.kellogg.northwestern.edu",
        min_title_len=20,
        max_results=max_results,
        source_name="Kellogg Insight",
    )


# ── Source: World Economic Forum ─────────────────────────────────────
def fetch_wef(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape WEF latest articles."""
    return session.scrape_links(
        "https://www.weforum.org/agenda/",
        r'<a[^>]*href="(/agenda/\d{4}/\d{2}/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://www.weforum.org",
        min_title_len=20,
        max_results=max_results,
        source_name="World Economic Forum",
    )


# ── Source: IEA (sitemap-based) ───────────────────────────────────────
def fetch_iea(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    """Scrape IEA news/reports via sitemap (blocks simple RSS but serves sitemap)."""
    return session.scrape_sitemap(
        "https://www.iea.org/sitemap.xml",
        source_name="IEA",
        url_filter="news",
        max_results=max_results,
    )


# ── Anti-bot / JS-only sources that block RSS + sitemap over plain HTTP ──
# These sites (Nike, a16z, BCG, Gartner, CBRE, Colliers, Starbucks, etc.) return
# 403 to urllib but render in a real browser. We scrape their homepage/category
# links headlessly. Patterns are best-effort; each source is wrapped in its own
# try so one broken selector never kills the batch.

def fetch_nike(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    return session.scrape_links(
        "https://about.nike.com/en/newsroom",
        r'<a[^>]*href="(/en/newsroom/[^"]+)"[^>]*>([^<]+)</a>',
        base_url="https://about.nike.com",
        min_title_len=15,
        max_results=max_results,
        source_name="Nike",
    )


def fetch_a16z(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    return session.scrape_links(
        "https://a16z.com/",
        r'<a[^>]*href="(https://a16z\.com/[a-z0-9-]+/)"[^>]*>([^<]{15,})</a>',
        base_url="https://a16z.com",
        min_title_len=15,
        max_results=max_results,
        source_name="a16z",
    )


def fetch_bcg(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    return session.scrape_links(
        "https://www.bcg.com/publications",
        r'<a[^>]*href="(/publications/[0-9a-z-]+\.aspx)"[^>]*>([^<]{15,})</a>',
        base_url="https://www.bcg.com",
        min_title_len=15,
        max_results=max_results,
        source_name="BCG",
    )


def fetch_gartner(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    return session.scrape_links(
        "https://www.gartner.com/en/newsroom",
        r'<a[^>]*href="(/en/newsroom/press-releases/[^"]+)"[^>]*>([^<]{15,})</a>',
        base_url="https://www.gartner.com",
        min_title_len=15,
        max_results=max_results,
        source_name="Gartner",
    )


def fetch_cbre(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    return session.scrape_links(
        "https://www.cbre.com/insights",
        r'<a[^>]*href="(/insights/[^"]+)"[^>]*>([^<]{15,})</a>',
        base_url="https://www.cbre.com",
        min_title_len=15,
        max_results=max_results,
        source_name="CBRE",
    )


def fetch_colliers(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    return session.scrape_links(
        "https://www.colliers.com/en/news",
        r'<a[^>]*href="(https://www\.colliers\.com/[^"]+)"[^>]*>([^<]{15,})</a>',
        base_url="https://www.colliers.com",
        min_title_len=15,
        max_results=max_results,
        source_name="Colliers",
    )


def fetch_starbucks(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    return session.scrape_links(
        "https://stories.starbucks.com/",
        r'<a[^>]*href="(/stories/[0-9-]+/[^"]+)"[^>]*>([^<]{15,})</a>',
        base_url="https://stories.starbucks.com",
        min_title_len=15,
        max_results=max_results,
        source_name="Starbucks",
    )


def fetch_doordash(session: HeadlessSession, max_results: int = 5) -> List[Dict]:
    return session.scrape_links(
        "https://about.doordash.com/en-us/newsroom",
        r'<a[^>]*href="(/en-us/newsroom/[^"]+)"[^>]*>([^<]{15,})</a>',
        base_url="https://about.doordash.com",
        min_title_len=15,
        max_results=max_results,
        source_name="DoorDash",
    )


# ── Unified headless fetch ───────────────────────────────────────────
HEADLESS_SOURCES = {
    "hbr": fetch_hbr,
    "accenture": fetch_accenture,
    "deloitte": fetch_deloitte,
    "wef": fetch_wef,
    "stanford_gsb": fetch_stanford_gsb,
    "hbs": fetch_hbs,
    "insead": fetch_insead,
    "lbs": fetch_lbs,
    "kellogg": fetch_kellogg,
    "yale": fetch_yale,
    # Anti-bot / JS-only industry sources
    "iea": fetch_iea,
    "nike": fetch_nike,
    "a16z": fetch_a16z,
    "bcg": fetch_bcg,
    "gartner": fetch_gartner,
    "cbre": fetch_cbre,
    "colliers": fetch_colliers,
    "starbucks": fetch_starbucks,
    "doordash": fetch_doordash,
}

HEADLESS_SOURCE_NAMES = {
    "hbr": "Harvard Business Review",
    "accenture": "Accenture",
    "deloitte": "Deloitte",
    "wef": "World Economic Forum",
    "stanford_gsb": "Stanford GSB",
    "hbs": "HBS Working Knowledge",
    "insead": "INSEAD Knowledge",
    "lbs": "London Business School",
    "kellogg": "Kellogg Insight",
    "yale": "Yale Insights",
    "iea": "IEA",
    "nike": "Nike",
    "a16z": "a16z",
    "bcg": "BCG",
    "gartner": "Gartner",
    "cbre": "CBRE",
    "colliers": "Colliers",
    "starbucks": "Starbucks",
    "doordash": "DoorDash",
}

HEADLESS_CONSULTING = ["hbr", "accenture", "deloitte", "wef"]
HEADLESS_ACADEMIC = ["stanford_gsb", "hbs", "insead", "lbs", "kellogg", "yale"]
# Newer anti-bot industry sources, grouped by vertical so scripts can pull
# only the ones they need without crawling everything.
HEADLESS_INDUSTRY = ["iea", "nike", "a16z", "bcg", "gartner", "cbre", "colliers", "starbucks", "doordash"]

# Vertical → headless source keys (added so market-trends scripts can opt in).
HEADLESS_FASHION = ["nike"]
HEADLESS_PROPERTY = ["cbre", "colliers"]
HEADLESS_ENERGY = ["iea"]
HEADLESS_FNB = ["starbucks", "doordash"]
HEADLESS_CONSULTING_ALL = ["bcg", "gartner"]


# ── Firecrawl-backed fetch (for anti-bot / JS-only sources) ─────────────
# Firecrawl is a hosted browser service that bypasses Cloudflare/JS challenges
# server-side. It's far more robust than local Playwright regex selectors,
# which is why the anti-bot industry sources (Nike, BCG, Gartner, CBRE, etc.)
# route through it when FIRECRAWL_API_KEY is configured.

import os as _os

def _firecrawl_key():
    key = _os.environ.get("FIRECRAWL_API_KEY", "")
    if key and "..." not in key:
        return key
    try:
        env = {}
        for line in open(_os.path.expanduser("~/.hermes/.env")):
            line = line.strip()
            if line.startswith("FIRECRAWL_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def firecrawl_scrape(url: str, timeout: int = 30) -> str:
    """Scrape a URL via Firecrawl, returning clean markdown ('' on failure).

    Requests BOTH 'markdown' (readable text) and 'links' (all rendered URLs).
    JS-heavy sites (Nike, a16z) populate the links array even when their
    markdown body only shows nav items.
    """
    key = _firecrawl_key()
    if not key:
        return ""
    import json as _json
    import urllib.request as _urlreq
    payload = _json.dumps({"url": url, "formats": ["markdown", "links"]}).encode()
    req = _urlreq.Request(
        "https://api.firecrawl.dev/v1/scrape",
        data=payload,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = _urlreq.urlopen(req, timeout=timeout)
        data = _json.loads(resp.read())
        if data.get("success"):
            d = data.get("data", {}) or {}
            return (d.get("markdown", "") or "") + "\n" + "\n".join(d.get("links", []) or [])
    except Exception:
        pass
    return ""


def _extract_articles_from_markdown(md: str, source_name: str,
                                    base_url: str, max_results: int,
                                    url_hint: str = "",
                                    domain: str = "") -> List[Dict]:
    """Parse markdown/links into article items, filtering out nav/share noise.

    Looks for absolute URLs. Keeps only URLs that:
      - live on the source's own domain (skip facebook/twitter share links, etc.)
      - are not images/uploads/mailto (skip .jpg/.png, /uploads/, mailto:)
      - contain url_hint (one or more substrings) when provided
    Derives a readable title from the URL slug when no markdown title exists.
    """
    import html as _html
    hints = [h for h in (url_hint or "").split("|") if h]
    # Noise domains/paths to exclude
    NOISE = ("facebook.com", "twitter.com", "linkedin.com", "mailto:",
             "instagram.com", "youtube.com", "reddit.com", "/uploads/",
             ".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf",
             "login", "register", "privacy", "terms", "cookie", "#")

    results = []
    seen = set()

    # 1. Prefer markdown links [Title](url) — they carry real titles.
    link_re = re.compile(r"\[([^\]]{8,})\]\((https?://[^)\s]+)\)")
    for line in md.splitlines():
        for m in link_re.finditer(line):
            title = _html.unescape(m.group(1)).strip()
            url = m.group(2).strip().rstrip(".,;:)")
            if not _keep(url, domain, hints, NOISE):
                continue
            if title in seen or url in seen:
                continue
            seen.add(title); seen.add(url)
            results.append(_item(title, url, source_name))
            if len(results) >= max_results:
                return results

    # 2. Fallback: bare URLs (Firecrawl 'links' array), title from slug.
    if not results:
        for raw in re.findall(r"(https?://[^\s]+)", md):
            url = raw.rstrip(".,;:)")
            if not _keep(url, domain, hints, NOISE):
                continue
            if url in seen:
                continue
            seen.add(url)
            slug = url.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")
            title = slug[:70].title() if len(slug) > 4 else url
            results.append(_item(title, url, source_name))
            if len(results) >= max_results:
                return results
    return results


def _keep(url: str, domain: str, hints: list, noise: tuple) -> bool:
    """True if a URL should be kept as an article."""
    if not url or not url.startswith("http"):
        return False
    if any(n in url for n in noise):
        return False
    if domain and domain not in url:
        return False
    if hints and not any(h in url for h in hints):
        return False
    return True


def _item(title: str, url: str, source_name: str) -> Dict:
    return {
        "title": title,
        "url": url,
        "date": datetime.now().isoformat(),
        "summary": "",
        "source": source_name,
    }


def firecrawl_fetch(url: str, source_name: str, url_hint: str = "",
                    max_results: int = 5, timeout: int = 30,
                    domain: str = "") -> List[Dict]:
    """Fetch articles from an anti-bot source via Firecrawl.

    Args:
        url: The page/newsroom URL to scrape.
        source_name: Label for items (e.g. 'Nike').
        url_hint: Pipe-separated substrings the article URLs must contain to
            filter noise (e.g. 'newsroom|/releases/').
        domain: The source's own domain (to exclude share/login links).
    """
    md = firecrawl_scrape(url, timeout=timeout)
    if not md:
        return []
    return _extract_articles_from_markdown(md, source_name, url, max_results, url_hint, domain)


# Per-source Firecrawl fetchers (replacing the brittle local selectors).
def fc_nike(max_results: int = 5) -> List[Dict]:
    return firecrawl_fetch("https://about.nike.com/en/newsroom", "Nike",
                           "/releases/", max_results, domain="about.nike.com")


def fc_a16z(max_results: int = 5) -> List[Dict]:
    return firecrawl_fetch("https://a16z.com/", "a16z",
                           "", max_results, domain="a16z.com")


def fc_bcg(max_results: int = 5) -> List[Dict]:
    return firecrawl_fetch("https://www.bcg.com/publications", "BCG",
                           "/publications/", max_results, domain="bcg.com")


def fc_gartner(max_results: int = 5) -> List[Dict]:
    return firecrawl_fetch("https://www.gartner.com/en/newsroom", "Gartner",
                           "press-releases", max_results, domain="gartner.com")


def fc_cbre(max_results: int = 5) -> List[Dict]:
    return firecrawl_fetch("https://www.cbre.com/insights", "CBRE",
                           "/insights/", max_results, domain="cbre.com")


def fc_colliers(max_results: int = 5) -> List[Dict]:
    return firecrawl_fetch("https://www.colliers.com/en/news", "Colliers",
                           "/news", max_results, domain="colliers.com")


def fc_starbucks(max_results: int = 5) -> List[Dict]:
    return firecrawl_fetch("https://stories.starbucks.com/", "Starbucks",
                           "/stories/|/press/", max_results, domain="about.starbucks.com")


def fc_doordash(max_results: int = 5) -> List[Dict]:
    return firecrawl_fetch("https://about.doordash.com/en-us/newsroom", "DoorDash",
                           "", max_results, domain="about.doordash.com")


def fc_iea(max_results: int = 5) -> List[Dict]:
    return firecrawl_fetch("https://www.iea.org/news", "IEA",
                           "/news/", max_results, domain="iea.org")


# Firecrawl source registry keyed by source key (same keys as HEADLESS_SOURCES).
FIRECRAWL_SOURCES = {
    "nike": fc_nike,
    "a16z": fc_a16z,
    "bcg": fc_bcg,
    "gartner": fc_gartner,
    "cbre": fc_cbre,
    "colliers": fc_colliers,
    "starbucks": fc_starbucks,
    "doordash": fc_doordash,
    "iea": fc_iea,
}


def firecrawl_batch(source_keys: List[str], max_results: int = 3) -> List[Dict]:
    """Fetch from anti-bot sources via Firecrawl. Empty if no key configured."""
    if not _firecrawl_key():
        return []
    all_items = []
    seen = set()
    for key in source_keys:
        fetcher = FIRECRAWL_SOURCES.get(key)
        if not fetcher:
            continue
        try:
            for item in fetcher(max_results):
                u = item.get("url", "")
                if u and u not in seen:
                    seen.add(u)
                    all_items.append(item)
        except Exception:
            continue
    return all_items


def fetch_headless_batch(source_keys: List[str], max_results: int = 3) -> List[Dict]:
    """Fetch from a list of headless sources using a single browser session.
    
    Args:
        source_keys: List of source keys to fetch
        max_results: Max items per source
    
    Returns:
        Deduplicated list of items
    """
    all_items = []
    seen_urls = set()
    
    if not _PLAYWRIGHT_AVAILABLE:
        return all_items
    
    with HeadlessSession() as session:
        if not session.available:
            return all_items
        for key in source_keys:
            fetcher = HEADLESS_SOURCES.get(key)
            if not fetcher:
                continue
            try:
                items = fetcher(session, max_results)
                for item in items:
                    url = item.get("url", "")
                    if isinstance(url, str) and url and url not in seen_urls:
                        seen_urls.add(url)
                        all_items.append(item)
            except Exception:
                pass
    
    return all_items


def fetch_leadership_headless(max_results: int = 3) -> List[Dict]:
    """Fetch from consulting/leadership headless sources."""
    return fetch_headless_batch(HEADLESS_CONSULTING, max_results)


def fetch_academic_headless(max_results: int = 3) -> List[Dict]:
    """Fetch from academic/business school headless sources."""
    return fetch_headless_batch(HEADLESS_ACADEMIC, max_results)


def fetch_all_headless(max_results: int = 3) -> List[Dict]:
    """Fetch from all headless sources."""
    return fetch_headless_batch(HEADLESS_CONSULTING + HEADLESS_ACADEMIC, max_results)
