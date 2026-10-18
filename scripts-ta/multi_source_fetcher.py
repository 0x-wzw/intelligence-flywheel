#!/usr/bin/env python3
"""
multi_source_fetcher.py

Shared multi-source news fetcher for market-trends cron scripts.
Provides free news sources beyond just Google News RSS:

FREE (no auth):
  1. Google News RSS
  2. Hacker News (Algolia API)
  3. Reddit (JSON endpoints)
  4. RSS/Atom feeds (verified from thought-leaders.json)
  5. Lobste.rs (JSON API)
  6. DEV.to (API)
  7. GitHub Trending (scrape)
  8. Wikipedia Current Events (API)

OPTIONAL (free tier, needs API key in .env):
  9. GNews API (250 req/day free)
  10. The Guardian Open Platform (500 req/day free)
  11. NewsAPI.org (100 req/day free)

Each source returns items in a uniform format:
  {"title": str, "url": str, "date": str, "summary": str, "source": str}
"""
import json
import os
import re
import html
import socket
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

# ── Config ────────────────────────────────────────────────────────────
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 HermesIntelligence/1.0"
DEFAULT_TIMEOUT = 12
MAX_ITEMS_PER_SOURCE = 5

# Optional API keys (set in ~/.hermes/.env)
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
GNEWS_API_KEY = os.environ.get("GNEWS_API_KEY", "")
GUARDIAN_API_KEY = os.environ.get("GUARDIAN_API_KEY", "")

# ── Source definitions ─────────────────────────────────────────────────
SOURCES = {
    "google_news": True,          # Free, no auth
    "hacker_news": True,          # Free, no auth
    "reddit": True,               # Free, no auth
    "rss_feeds": True,            # Free, no auth
    "lobsters": True,             # Free, no auth
    "devto": True,                # Free, no auth
    "github_trending": True,      # Free, no auth
    "wikipedia": True,            # Free, no auth
    "arxiv": True,                # Free, no auth, fast
    "openalex": True,             # Free, no auth, fast
    "crossref": True,             # Free, no auth, fast
    "pubmed": False,              # Slow (16s), often empty — enabled via DAILY_ACADEMIC
    "core": False,                # Slow (21s), often empty — enabled via DAILY_ACADEMIC
    "semantic_scholar": False,    # Rate-limited (429) — enabled via DAILY_ACADEMIC
    "headless_consulting": False, # Headless browser — enabled via DAILY_HEADLESS
    "headless_academic": False,   # Headless browser — enabled via DAILY_HEADLESS
    "gnews": bool(GNEWS_API_KEY),       # Free tier, needs key
    "guardian": bool(GUARDIAN_API_KEY),  # Free tier, needs key
    "newsapi": bool(NEWSAPI_KEY),        # Free tier, needs key
}

# Set DAILY_HEADLESS=true in env to enable slow sources (headless browser)
# Set DAILY_ACADEMIC=true in env to enable slow academic APIs (PubMed, CORE, Semantic Scholar)
DAILY_HEADLESS = os.environ.get("DAILY_HEADLESS", "").lower() in ("1", "true", "yes")
DAILY_ACADEMIC = os.environ.get("DAILY_ACADEMIC", "").lower() in ("1", "true", "yes")

# ── Category relevance firewall ────────────────────────────────────────
# RSS feed membership is candidate retrieval, not category evidence. Canonical
# category intent belongs in trend_contract so retrieval and validation cannot
# drift into two different taxonomies.
from trend_contract import category_match


def is_category_relevant(item: Dict, category_key: str) -> bool:
    """Reject items without direct title/summary evidence for their category."""
    text = " ".join((str(item.get("title", "")), str(item.get("summary", ""))))
    return category_match(text, category_key)

# ── Expanded RSS feed registry ─────────────────────────────────────────
MARKET_RSS_FEEDS = {
    "tech_business": [
        "https://stratechery.com/feed",
        "https://www.exponentialview.co/feed",
        "https://blog.cloudflare.com/rss/",
        "https://blog.eladgil.com/feed",
        "https://www.technologyreview.com/topic/artificial-intelligence/feed",
        "https://www.theverge.com/rss/index.xml",
        "https://arstechnica.com/feed/",
        "https://www.wired.com/feed/rss",
        "https://techcrunch.com/feed/",
        "https://www.engadget.com/rss.xml",
        "https://www.zdnet.com/news/rss.xml",
        "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    ],
    "economics_policy": [
        "https://marginalrevolution.com/feed",
        "https://www.rand.org/blog.xml",
        "https://www.bruegel.org/rss.xml",
        "https://www.cato.org/rss/blog",
        "https://www.aei.org/feed/",
        "https://www.epi.org/feed/",
        "https://www.mercatus.org/rss.xml",
        "https://www.heritage.org/rss",                     # Heritage Foundation (conservative)
        "https://www.lawfaremedia.org/feeds/articles.rss",  # Lawfare (national security / centrist)
        "https://www.piie.com/rss.xml",
        "https://www.economist.com/feeds/print-sections/77/business.xml",
        "https://feeds.bbci.co.uk/news/business/rss.xml",
        "https://www.ft.com/rss/home",
        "https://www.wsj.com/xml/rss/3_7085.xml",
    ],
    "ai_tech": [
        "https://openai.com/news/rss.xml",
        "https://deepmind.google/blog/rss.xml",
        "https://blog.google/technology/ai/rss/",
        "https://blogs.nvidia.com/feed/",
        "https://blogs.microsoft.com/feed/",
        "https://simonwillison.net/atom/everything/",
        "https://www.interconnects.ai/feed",
        "https://www.lesswrong.com/feed.xml",
        "https://www.alignmentforum.org/feed.xml",
        "https://futureoflife.org/feed/",
        "https://intelligence.org/feed/",
        "https://gwern.net/index.rss",
        "https://www.nature.com/subjects/machine-learning.rss",
        "https://news.mit.edu/rss/topic/artificial-intelligence",
        "https://ai.googleblog.com/feeds/posts/default",
    ],
    "newsletters": [
        "https://platformer.substack.com/feed",
        "https://mattstoller.substack.com/feed",
        "https://moneystuff.substack.com/feed",
        "https://www.aisnakeoil.com/feed",
        "https://thezvi.substack.com/feed",
        "https://paulkrugman.substack.com/feed",
        "https://www.oneusefulthing.org/feed",
        "https://erikjlarson.substack.com/feed",
        "https://garymarcus.substack.com/feed",
        "https://reidhoffman.substack.com/feed",
        "https://stephaniekelton.substack.com/feed",
        "https://neweconomicperspectives.org/feed",
        "https://www.jasonhickel.org/blog?format=rss",
        "https://degrowth.info/en/blog/feed",
    ],
    "global_development": [
        "https://restofworld.org/feed/latest/",
        "https://africanarguments.org/feed/",
        "https://www.cgdev.org/rss/all",
        "https://www.povertyactionlab.org/blog.xml",
        "https://www.worldbank.org/en/news/rss",
        "https://www.imf.org/en/News/RSS",
        "https://www.chathamhouse.org/rss.xml",
        "https://carnegieendowment.org/rss.xml",
    ],
    "science_health": [
        "https://www.nature.com/nature.rss",
        "https://www.science.org/rss/news_current.xml",
        "https://rss.nytimes.com/services/xml/rss/nyt/Science.xml",
        "https://www.newscientist.com/feed/home",
        "https://www.sciencedaily.com/rss/all.xml",
        "https://www.statnews.com/feed/",
        "https://www.medscape.com/rss/public",
    ],
    "retail_consumer": [
        "https://www.retaildive.com/feeds/news/",
        "https://www.grocerydive.com/feeds/news/",
        "https://www.marketingdive.com/feeds/news/",
        "https://www.fooddive.com/feeds/news/",
        "https://www.restaurantdive.com/feeds/news/",
        "https://www.retailtouchpoints.com/feed/",
        "https://www.retailwire.com/feed/",
        "https://www.businessoffashion.com/feed/",          # BoF / Imran Amed
        "https://www.glossy.co/feed/",                       # fashion/retail media
        "https://www.textileworld.com/feed/",                # textiles & fabrics
        "https://www.just-style.com/feed/",                  # apparel sourcing
        "https://sgbonline.com/feed/",                       # sportswear / performance
        "https://www.modernretail.co/feed/",                 # Modern Retail
    ],
    "property_energy": [
        "https://www.globest.com/rss",
        "https://www.costar.com/rss",
        "https://www.renewableenergyworld.com/feed/",
        "https://www.greentechmedia.com/feed",
        "https://www.pv-magazine.com/feed/",
        "https://www.rechargenews.com/arc/outboundfeeds/rss/",
        "https://www.datacenterknowledge.com/feed/",
        "https://www.datacenterdynamics.com/en/rss/",        # data centers
        "https://about.bnef.com/feed/",                      # BloombergNEF / clean energy
    ],
    "startups_venture": [
        "https://techcrunch.com/feed/",
        "https://news.crunchbase.com/feed/",
        "https://pitchbook.com/news/rss",
        "https://www.siftery.com/feed",
        "https://www.producthunt.com/feed",
        "https://www.indiehackers.com/rss.xml",
        "https://www.sequoiacap.com/feed/",                  # Sequoia
        "https://www.kleinerperkins.com/feed/",              # Kleiner Perkins
    ],
    "consulting_leadership": [
        "https://www.mckinsey.com/insights/rss",
        "https://www.mckinsey.com/featured-insights/rss",
        "https://sloanreview.mit.edu/feed/",
        "https://knowledge.wharton.upenn.edu/feed/",
        "https://corpgov.law.harvard.edu/feed/",
        "https://adamgrant.net/feed/",                       # Adam Grant
        "https://hbr.org/feed",                              # Harvard Business Review
        "https://www.lennysnewsletter.com/feed",             # Lenny Rachitsky / product leadership
    ],
    "food_beverage": [
        "https://www.fooddive.com/feeds/news/",
        "https://www.restaurantdive.com/feeds/news/",
        "https://www.circana.com/feed/",                     # Circana consumer data
        "https://www.grocerydive.com/feeds/news/",
        "https://www.nrn.com/rss.xml",                       # Nation's Restaurant News / QSR
    ],
    "hospitality_travel": [
        "https://skift.com/feed/",                           # Skift / Rafat Ali
        "https://news.airbnb.com/feed/",                     # Airbnb
        "https://www.hotelmanagement.net/rss",
        "https://www.hospitalitynet.org/rss/news.xml",       # Hospitality Net
        "https://seniorhousingnews.com/feed/",               # Senior Housing News / care
    ],
    "circular_fashion": [
        "https://fashionforgood.com/feed/",                  # Fashion for Good
        "https://www.mycoworks.com/rss/",                    # MycoWorks (fabric/leather tech)
    ],
    "ai_science": [
        "https://www.nature.com/nature.rss",
        "https://www.science.org/rss/news_current.xml",
        "https://openai.com/news/rss.xml",
        "https://deepmind.google/blog/rss.xml",
    ],
}

# ── Expanded Reddit subreddits by topic ────────────────────────────────
REDDIT_SUBREDDITS = {
    "technology": ["technology", "technews", "FutureTech", "hardware", "gadgets"],
    "business": ["business", "economics", "investing", "smallbusiness", "entrepreneur"],
    "ai_ml": ["MachineLearning", "artificial", "singularity", "artificialintelligence", "LocalLLaMA"],
    "startups": ["startups", "Entrepreneur", "venturecapital", "SaaS", "sideproject"],
    "retail": ["retail", "fashion", "ecommerce", "supplychain"],
    "fnb": ["food", "foodscience", "restaurant", "KitchenConfidential"],
    "property": ["realestate", "RealEstateTechnology", "construction", "energy"],
    "science": ["science", "Futurology", "space", "biology", "physics"],
    "leadership": ["leadership", "management", "consulting", "digitalnomad", "MBA", "careerguidance"],
    "finance": ["finance", "stocks", "wallstreetbets", "cryptocurrency"],
}

# ── HN topic queries ──────────────────────────────────────────────────
HN_TOPICS = {
    "technology": "technology OR software OR hardware OR startup",
    "ai_ml": "artificial intelligence OR machine learning OR deep learning OR LLM OR GPT",
    "business": "startup OR business OR venture capital OR funding OR acquisition",
    "science": "science OR research OR breakthrough OR discovery OR physics",
    "programming": "programming OR coding OR developer OR open source OR rust OR python",
    "security": "security OR privacy OR encryption OR cyber OR hack",
}


def _http_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> Tuple[Optional[str], Optional[str]]:
    """Simple HTTP GET returning (body_text, error)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            return data, None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, f"URL error: {e.reason}"
    except (socket.timeout, TimeoutError):
        return None, "timeout"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _extract_summary(text: str, max_chars: int = 300) -> str:
    """Extract first meaningful paragraph from HTML text."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    paragraphs = re.split(r"\n\s*\n", text)
    for p in paragraphs:
        p = p.strip()
        if len(p) < 80 or "cookie" in p[:100].lower() or "subscribe" in p[:100].lower():
            continue
        return p[:max_chars].rsplit(" ", 1)[0] + "..."
    return text[:max_chars].rsplit(" ", 1)[0] + "..." if len(text) > max_chars else text


def fetch_article_summary(url: str, max_chars: int = 300) -> str:
    """Fetch an article and extract a brief summary from the first meaningful paragraph."""
    if not url:
        return ""
    body, err = _http_get(url, timeout=8)
    if err or not body:
        return ""
    return _extract_summary(body, max_chars)


# ── Source: Google News RSS ───────────────────────────────────────────
# Publishers that are content farms / press-release mills / SEO spam. When
# Google News surfaces one of these, the item is low-signal regardless of
# topic — drop it at the source so it never pollutes the raw captures or the
# signal/noise funnel. Matched case-insensitively against the <source> name.
GOOGLE_NEWS_NOISE_PUBLISHERS = [
    "trend hunter", "globenewswire", "precedence research",
    "fortune business insights", "fundsforngos", "the motley fool",
    "market.us", "market.us media", "alleywatch", "simplilearn",
    "aimultiple", "yellow.com", "firstcallgolf", "biztech magazine",
    "global data", "triblive", "laodong.vn", "the quad-city times",
    "provider magazine", "pos.toasttab.com", "textile world",
    "mass market retailers", "quality assurance & food safety",
]


# Google News recency operators. A daily-cadence vertical that keeps re-issuing the
# same evergreen query ("retail technology AI automation trends 2026") against a
# SeenUrls dedup store yields 0 new articles forever after day one: the top hits
# are the same annual-outlook pieces every single day. Appending a recency operator
# when the caller has not already set one restores genuine daily yield without
# changing any caller's query text.
_GN_RECENCY_OP = re.compile(r"\bwhen:\d+[dhm]\b", re.IGNORECASE)


def _with_recency(query: str, window: str = "7d") -> str:
    """Append a Google News recency operator unless the caller set one."""
    if _GN_RECENCY_OP.search(query):
        return query
    return f'{query} "when:{window}"'


def fetch_google_news(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch news headlines from Google News RSS.

    Drops items from known noise publishers (content farms / press-release
    mills) so they never enter the raw captures. The real publisher is read
    from the RSS <source> tag, not the news.google.com redirect URL.

    A recency operator is appended unless the caller supplied one — see
    _with_recency for why a daily vertical needs it.
    """
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(_with_recency(query))}&hl=en-US&gl=US&ceid=US:en"
    data, err = _http_get(url)
    if err or not data:
        return []
    items = re.findall(
        r"<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?<pubDate>(.*?)</pubDate>.*?</item>",
        data, re.DOTALL,
    )
    # Also capture the <source> publisher name per item (may be absent).
    sources = re.findall(r"<source[^>]*>(.*?)</source>", data, re.DOTALL)
    results = []
    for idx, (title, link, date) in enumerate(items[:max_results * 2]):
        clean_title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        publisher = html.unescape(re.sub(r"<[^>]+>", "", sources[idx])).strip() if idx < len(sources) else ""
        if publisher and publisher.lower() in GOOGLE_NEWS_NOISE_PUBLISHERS:
            continue  # content farm / press-release mill — skip
        results.append({
            "title": clean_title,
            "url": link.strip(),
            "date": date.strip(),
            "summary": "",
            "source": "Google News",
            "publisher": publisher,
        })
        if len(results) >= max_results:
            break
    return results


# ── Source: Hacker News (Algolia API) ─────────────────────────────────
def fetch_hacker_news(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch stories from Hacker News via Algolia API (free, no auth)."""
    url = f"https://hn.algolia.com/api/v1/search_by_date?query={urllib.parse.quote(query)}&tags=story&hitsPerPage={max_results}"
    data, err = _http_get(url)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for hit in parsed.get("hits", []):
        title = hit.get("title", "")
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        date = hit.get("created_at", "")
        points = hit.get("points", 0)
        author = hit.get("author", "")
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": f"▲ {points} by {author} on HN" if points else f"by {author} on HN",
            "source": "Hacker News",
        })
    return results


# ── Source: Reddit JSON ───────────────────────────────────────────────
def fetch_reddit(subreddit: str, query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch posts from a Reddit subreddit via JSON (free, no auth)."""
    url = f"https://www.reddit.com/r/{subreddit}/search.json?q={urllib.parse.quote(query)}&restrict_sr=1&sort=new&limit={max_results}"
    data, err = _http_get(url)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for child in parsed.get("data", {}).get("children", []):
        post = child.get("data", {})
        title = post.get("title", "")
        url = post.get("url", "")
        date = datetime.fromtimestamp(post.get("created_utc", 0), tz=timezone.utc).isoformat()
        score = post.get("score", 0)
        comments = post.get("num_comments", 0)
        selftext = post.get("selftext", "")
        summary = _extract_summary(selftext, 200) if selftext else f"▲ {score} · {comments} comments on r/{subreddit}"
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": summary,
            "source": f"r/{subreddit}",
        })
    return results


# ── Source: RSS feeds ─────────────────────────────────────────────────
def fetch_rss_feed(feed_url: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch items from an RSS/Atom feed using feedparser if available."""
    try:
        import feedparser
    except ImportError:
        return []
    try:
        feed = feedparser.parse(feed_url)
    except Exception:
        return []
    results = []
    for entry in feed.entries[:max_results]:
        title = entry.get("title", "")
        url = entry.get("link", "")
        date = entry.get("published", entry.get("updated", ""))
        summary = entry.get("summary", entry.get("description", ""))
        if summary:
            summary = re.sub(r"<[^>]+>", " ", summary)
            summary = html.unescape(summary)
            summary = re.sub(r"\s+", " ", summary).strip()[:300]
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": summary,
            "source": feed_url.split("/")[2] if "//" in feed_url else feed_url,
        })
    return results


# ── Source: Lobste.rs ──────────────────────────────────────────────────
def fetch_lobsters(max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch recent stories from Lobste.rs (free, no auth)."""
    url = f"https://lobste.rs/newest.json?limit={max_results}"
    data, err = _http_get(url)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for story in parsed[:max_results]:
        title = story.get("title", "")
        url = story.get("url") or f"https://lobste.rs/s/{story.get('short_id', '')}"
        date = story.get("created_at", "")
        tags = ", ".join(story.get("tags", []))
        score = story.get("score", 0)
        comments = story.get("comment_count", 0)
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": f"▲ {score} · {comments} comments [{tags}]" if tags else f"▲ {score} · {comments} comments",
            "source": "Lobste.rs",
        })
    return results


# ── Source: DEV.to ─────────────────────────────────────────────────────
def fetch_devto(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch articles from DEV.to (free, no auth)."""
    url = f"https://dev.to/api/articles?per_page={max_results}&tag={urllib.parse.quote(query)}"
    data, err = _http_get(url)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for article in parsed[:max_results]:
        title = article.get("title", "")
        url = article.get("url", "")
        date = article.get("published_at", "")
        tags = ", ".join(article.get("tag_list", []))
        comments = article.get("comments_count", 0)
        positive = article.get("positive_reactions_count", 0)
        description = article.get("description", "")
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": description or f"❤️ {positive} · {comments} comments [{tags}]" if tags else f"❤️ {positive} · {comments} comments",
            "source": "DEV.to",
        })
    return results


# ── Source: GitHub Trending ───────────────────────────────────────────
def fetch_github_trending(language: str = "", max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch trending repositories from GitHub (free, no auth)."""
    url = f"https://github.com/trending/{language}?since=daily"
    data, err = _http_get(url)
    if err or not data:
        return []
    # Parse the HTML for repo cards
    repos = re.findall(
        r'<h2[^>]*class="h3[^"]*">.*?<a[^>]*href="/([^"]+)"[^>]*>([^<]+)</a>',
        data, re.DOTALL,
    )
    results = []
    for repo_path, repo_name in repos[:max_results]:
        full_name = repo_path.strip()
        name = repo_name.strip()
        # Get description
        desc_match = re.search(
            rf'<h2[^>]*class="h3[^"]*">.*?href="/{re.escape(full_name)}"[^>]*>.*?</h2>\s*<p[^>]*class="col-9[^"]*"[^>]*>(.*?)</p>',
            data, re.DOTALL,
        )
        description = ""
        if desc_match:
            description = re.sub(r"<[^>]+>", " ", desc_match.group(1)).strip()
            description = html.unescape(description)[:200]
        # Get stars
        stars_match = re.search(
            rf'href="/{re.escape(full_name)}/stargazers"[^>]*>.*?(\d[\d,]*)\s*</a>',
            data, re.DOTALL,
        )
        stars = stars_match.group(1) if stars_match else "?"
        results.append({
            "title": f"{name} — {description}" if description else name,
            "url": f"https://github.com/{full_name}",
            "date": datetime.now().isoformat(),
            "summary": f"⭐ {stars} stars on GitHub" if stars else "GitHub trending",
            "source": "GitHub Trending",
        })
    return results


# ── Source: Wikipedia Current Events ───────────────────────────────────
def fetch_wikipedia_current(max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch current events from Wikipedia (free, no auth)."""
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://en.wikipedia.org/api/rest_v1/feed/featured/{today}"
    data, err = _http_get(url)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    # Get news stories
    for story in parsed.get("news", [])[:max_results]:
        title = story.get("story", "")
        links = story.get("links", [])
        url = links[0].get("url", "") if links else ""
        results.append({
            "title": title[:200],
            "url": url,
            "date": today,
            "summary": "",
            "source": "Wikipedia",
        })
    # Fallback: get "on this day" if no news
    if not results:
        for item in parsed.get("onthisday", [])[:max_results]:
            text = item.get("text", "")
            year = item.get("year", "")
            results.append({
                "title": f"{year}: {text[:200]}" if year else text[:200],
                "url": "",
                "date": today,
                "summary": "",
                "source": "Wikipedia",
            })
    return results


# ── Source: arXiv API ──────────────────────────────────────────────────
ARXIV_CATEGORIES = {
    "cs.LG": "Machine Learning",
    "cs.AI": "Artificial Intelligence",
    "cs.CL": "Computation and Language",
    "cs.CV": "Computer Vision",
    "cs.RO": "Robotics",
    "cs.SE": "Software Engineering",
    "cs.CR": "Cryptography and Security",
    "cs.NE": "Neural and Evolutionary Computing",
    "stat.ML": "Machine Learning (Statistics)",
    "q-fin.GN": "General Finance",
    "econ.GN": "General Economics",
    "physics.soc-ph": "Physics and Society",
}

def fetch_arxiv(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch papers from arXiv API by search query (free, no auth)."""
    # Map common category keys to arXiv categories
    cat_map = {
        "ai_ml": "cs.AI+OR+cat:cs.LG+OR+cat:cs.CL+OR+cat:stat.ML",
        "science": "cat:physics.soc-ph+OR+cat:cs.NE",
        "technology": "cat:cs.SE+OR+cat:cs.CR+OR+cat:cs.CV",
        "finance": "cat:q-fin.GN+OR+cat:econ.GN",
    }
    cat_query = cat_map.get(query, "cs.AI+OR+cat:cs.LG")
    url = f"http://export.arxiv.org/api/query?search_query=all:{urllib.parse.quote(query)}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
    data, err = _http_get(url, timeout=20)
    if err or not data:
        return []
    try:
        import feedparser
    except ImportError:
        return []
    try:
        feed = feedparser.parse(data)
    except Exception:
        return []
    results = []
    for entry in feed.entries[:max_results]:
        title = " ".join(strip_html(entry.get("title", "")).split())
        url = entry.get("link", "")
        published = entry.get("published", "")
        authors = [a.get("name", "") for a in entry.get("authors", [])]
        author_str = ", ".join(authors[:3]) + (f" +{len(authors)-3} more" if len(authors) > 3 else "")
        abstract = strip_html(entry.get("summary", ""))
        summary = abstract[:300] + "..." if len(abstract) > 300 else abstract
        cat = entry.get("arxiv_primary_category", {}).get("term", "") if isinstance(entry.get("arxiv_primary_category"), dict) else ""
        results.append({
            "title": title,
            "url": url,
            "date": published,
            "summary": f"[{cat}] {summary}" if cat else summary,
            "source": "arXiv",
            "authors": author_str,
        })
    return results


def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


# ── Source: Semantic Scholar API ──────────────────────────────────────
def fetch_semantic_scholar(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch papers from Semantic Scholar API (free, no auth, 100 req/min)."""
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={urllib.parse.quote(query)}&limit={max_results}&fields=title,url,year,authors,abstract,externalIds,openAccessPdf,publicationDate,venue"
    data, err = _http_get(url, timeout=15)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for paper in parsed.get("data", [])[:max_results]:
        title = paper.get("title", "")
        url = paper.get("url", "") or f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}"
        date = paper.get("publicationDate", str(paper.get("year", "")))
        venue = paper.get("venue", "")
        authors = [a.get("name", "") for a in paper.get("authors", [])]
        author_str = ", ".join(authors[:3]) + (f" +{len(authors)-3} more" if len(authors) > 3 else "")
        abstract = paper.get("abstract", "")
        summary = abstract[:300] + "..." if abstract and len(abstract) > 300 else (abstract or "")
        pdf = paper.get("openAccessPdf", {}).get("url", "") if isinstance(paper.get("openAccessPdf"), dict) else ""
        results.append({
            "title": title,
            "url": url,
            "date": str(date),
            "summary": f"[{venue}] {summary}" if venue else summary,
            "source": "Semantic Scholar",
            "authors": author_str,
            "pdf_url": pdf,
        })
    return results


# ── Source: OpenAlex API ──────────────────────────────────────────────
def fetch_openalex(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch scholarly works from OpenAlex API (free, no auth, no rate limit documented)."""
    url = f"https://api.openalex.org/works?search={urllib.parse.quote(query)}&per_page={max_results}&sort=publication_date:desc"
    data, err = _http_get(url, timeout=15)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for work in parsed.get("results", [])[:max_results]:
        title = work.get("title", "")
        url = work.get("id", "").replace("https://api.openalex.org", "https://openalex.org") if work.get("id") else ""
        date = work.get("publication_date", str(work.get("publication_year", "")))
        authors = [a.get("author", {}).get("display_name", "") for a in work.get("authorships", []) if isinstance(a, dict)]
        author_str = ", ".join(authors[:3]) + (f" +{len(authors)-3} more" if len(authors) > 3 else "")
        abstract = work.get("abstract_inverted_index", {})
        # Reconstruct abstract from inverted index
        if abstract and isinstance(abstract, dict):
            word_positions = []
            for word, positions in abstract.items():
                for pos in positions:
                    word_positions.append((pos, word))
            word_positions.sort()
            abstract_text = " ".join(w for _, w in word_positions)
        else:
            abstract_text = ""
        summary = abstract_text[:300] + "..." if abstract_text and len(abstract_text) > 300 else abstract_text
        venue = work.get("primary_location", {}).get("source", {}).get("display_name", "") if isinstance(work.get("primary_location"), dict) else ""
        cited_by = work.get("cited_by_count", 0)
        results.append({
            "title": title,
            "url": url,
            "date": str(date),
            "summary": f"[{venue}] {summary}" if venue else summary,
            "source": "OpenAlex",
            "authors": author_str,
            "citations": cited_by,
        })
    return results


# ── Source: Crossref API ───────────────────────────────────────────────
def fetch_crossref(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch scholarly works from Crossref API (free, no auth, polite pool)."""
    url = f"https://api.crossref.org/works?query={urllib.parse.quote(query)}&rows={max_results}&sort=published&order=desc"
    data, err = _http_get(url, timeout=15)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for item in parsed.get("message", {}).get("items", [])[:max_results]:
        title = item.get("title", [""])[0] if item.get("title") else ""
        doi = item.get("DOI", "")
        url = f"https://doi.org/{doi}" if doi else ""
        date_parts = item.get("published", {}).get("date-parts", [[0]])[0]
        date = f"{date_parts[0]}-{date_parts[1]:02d}-{date_parts[2]:02d}" if len(date_parts) >= 3 else str(date_parts[0]) if date_parts else ""
        authors = [f"{a.get('given', '')} {a.get('family', '')}" for a in item.get("author", [])]
        author_str = ", ".join(authors[:3]) + (f" +{len(authors)-3} more" if len(authors) > 3 else "")
        container = item.get("container-title", [""])[0] if item.get("container-title") else ""
        abstract = item.get("abstract", "")
        summary = strip_html(abstract)[:300] + "..." if abstract and len(strip_html(abstract)) > 300 else (strip_html(abstract) if abstract else "")
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": f"[{container}] {summary}" if container else summary,
            "source": "Crossref",
            "authors": author_str,
            "doi": doi,
        })
    return results


# ── Source: PubMed API ────────────────────────────────────────────────
def fetch_pubmed(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch biomedical literature from PubMed via E-utilities API (free, no auth)."""
    # Step 1: Search for IDs
    search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={urllib.parse.quote(query)}&retmax={max_results}&retmode=json&sort=date"
    data, err = _http_get(search_url, timeout=15)
    if err or not data:
        return []
    try:
        search = json.loads(data)
    except json.JSONDecodeError:
        return []
    ids = search.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []
    # Step 2: Fetch summaries
    fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id={','.join(ids)}&retmode=json"
    data, err = _http_get(fetch_url, timeout=15)
    if err or not data:
        return []
    try:
        summaries = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for uid in ids:
        result = summaries.get("result", {}).get(uid, {})
        title = result.get("title", "")
        url = f"https://pubmed.ncbi.nlm.nih.gov/{uid}/"
        date = result.get("pubdate", "")
        authors = result.get("authors", [])
        author_str = ", ".join([a.get("name", "") for a in authors[:3]]) + (f" +{len(authors)-3} more" if len(authors) > 3 else "")
        source_name = result.get("source", "")
        elocation = result.get("elocationid", "")
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": f"[{source_name}] {author_str}" if source_name else author_str,
            "source": "PubMed",
            "authors": author_str,
            "journal": source_name,
        })
    return results


# ── Source: CORE API ──────────────────────────────────────────────────
def fetch_core(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch open access papers from CORE API (free, no auth, 5k req/day)."""
    url = f"https://api.core.ac.uk/v3/search/works?q={urllib.parse.quote(query)}&limit={max_results}&sort=publicationDate:desc"
    data, err = _http_get(url, timeout=15)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for work in parsed.get("results", [])[:max_results]:
        title = work.get("title", "")
        url = work.get("sourceUrl", "") or work.get("identifiers", [""])[0] if work.get("identifiers") else ""
        date = work.get("publicationDate", str(work.get("yearPublished", "")))
        authors = [a.get("name", "") for a in work.get("authors", []) if isinstance(a, dict)]
        author_str = ", ".join(authors[:3]) + (f" +{len(authors)-3} more" if len(authors) > 3 else "")
        abstract = work.get("abstract", "")
        summary = abstract[:300] + "..." if abstract and len(abstract) > 300 else (abstract or "")
        journal = work.get("journalName", "")
        downloads = work.get("downloads", 0)
        results.append({
            "title": title,
            "url": url,
            "date": str(date),
            "summary": f"[{journal}] {summary}" if journal else summary,
            "source": "CORE",
            "authors": author_str,
            "downloads": downloads,
        })
    return results


# ── Source: GNews API (optional, needs key) ───────────────────────────
def fetch_gnews(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch news from GNews API (free tier: 250 req/day)."""
    if not GNEWS_API_KEY:
        return []
    url = f"https://gnews.io/api/v4/search?q={urllib.parse.quote(query)}&lang=en&max={max_results}&apikey={GNEWS_API_KEY}"
    data, err = _http_get(url)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for article in parsed.get("articles", []):
        title = article.get("title", "")
        url = article.get("url", "")
        date = article.get("publishedAt", "")
        description = article.get("description", "")
        source_name = article.get("source", {}).get("name", "GNews")
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": description or "",
            "source": source_name,
        })
    return results


# ── Source: The Guardian API (optional, needs key) ─────────────────────
def fetch_guardian(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch news from The Guardian Open Platform (free tier: 500 req/day)."""
    if not GUARDIAN_API_KEY:
        return []
    url = f"https://content.guardianapis.com/search?q={urllib.parse.quote(query)}&page-size={max_results}&show-fields=headline,trailText&api-key={GUARDIAN_API_KEY}"
    data, err = _http_get(url)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for article in parsed.get("response", {}).get("results", []):
        title = article.get("webTitle", "")
        url = article.get("webUrl", "")
        date = article.get("webPublicationDate", "")
        fields = article.get("fields", {})
        trail = fields.get("trailText", "")
        section = article.get("sectionName", "Guardian")
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": re.sub(r"<[^>]+>", " ", trail).strip() if trail else "",
            "source": f"The Guardian ({section})",
        })
    return results


# ── Source: NewsAPI (optional, needs key) ─────────────────────────────
def fetch_newsapi(query: str, max_results: int = MAX_ITEMS_PER_SOURCE) -> List[Dict]:
    """Fetch news from NewsAPI.org (free tier: 100 req/day)."""
    if not NEWSAPI_KEY:
        return []
    url = f"https://newsapi.org/v2/everything?q={urllib.parse.quote(query)}&pageSize={max_results}&language=en&sortBy=publishedAt&apiKey={NEWSAPI_KEY}"
    data, err = _http_get(url)
    if err or not data:
        return []
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return []
    results = []
    for article in parsed.get("articles", []):
        title = article.get("title", "")
        url = article.get("url", "")
        date = article.get("publishedAt", "")
        description = article.get("description", "")
        source_name = article.get("source", {}).get("name", "NewsAPI")
        results.append({
            "title": title,
            "url": url,
            "date": date,
            "summary": description or "",
            "source": source_name,
        })
    return results


# ── Unified multi-source fetch ────────────────────────────────────────
def fetch_from_all_sources(query: str, category_key: str = "",
                           max_per_source: int = MAX_ITEMS_PER_SOURCE,
                           enabled_sources: Optional[List[str]] = None,
                           academic: Optional[bool] = None) -> List[Dict]:
    """Fetch news from all enabled sources for a given query.

    Args:
        query: Search query string
        category_key: Category key for subreddit/RSS group lookup
        max_per_source: Max items per source
        enabled_sources: List of source names to use (default: all enabled)
        academic: True if this is an academic vertical (AI/science) that may pull
            scholarly sources; False otherwise. When None, falls back to the
            category-name allowlist (legacy). Vertical scripts should pass their
            contract's `academic` flag so consumer verticals (F&B, hospitality,
            property) never receive scholarly sources even for shared category
            names like `food_security`, `tcm`, or `renewable_energy`.

    Returns:
        List of deduplicated items sorted by date (newest first)
    """
    if enabled_sources is None:
        enabled_sources = [k for k, v in SOURCES.items() if v]
        # If DAILY_HEADLESS is not set, exclude slow headless sources
        if not DAILY_HEADLESS:
            enabled_sources = [s for s in enabled_sources if s not in ("headless_consulting", "headless_academic")]
        # If DAILY_ACADEMIC is not set, exclude slow academic APIs
        if not DAILY_ACADEMIC:
            enabled_sources = [s for s in enabled_sources if s not in ("pubmed", "core", "semantic_scholar")]

        # Academic/scholarly sources (OpenAlex, arXiv, Crossref) return academic
        # papers that are irrelevant to consumer/retail verticals. Restrict them
        # to academic & science categories only. Google News, HN, Reddit, RSS
        # still cover the consumer verticals, so this removes noise without
        # losing coverage.
        #
        # `academic` (the vertical's flag) is authoritative when provided. This
        # fixes a bug where shared category names (food_security, tcm,
        # rehabilitation_care, renewable_energy) appeared in the academic
        # allowlist, letting consumer verticals pull scholarly noise.
        academic_sources = {"openalex", "arxiv", "crossref"}
        academic_categories = {
            "ai_ml", "post_quantum_encryption", "agentic_ai", "agi",
            "data_architectures", "science_discoveries", "renewable_energy",
            "food_security", "tcm", "rehabilitation_care",
        }
        if academic is False:
            # Explicitly a consumer vertical: never pull scholarly sources,
            # regardless of category name.
            enabled_sources = [s for s in enabled_sources if s not in academic_sources]
        elif academic is True:
            pass  # academic vertical — keep scholarly sources
        elif category_key and category_key not in academic_categories:
            # Legacy fallback (no flag passed): use category-name allowlist.
            enabled_sources = [s for s in enabled_sources if s not in academic_sources]

        # Generic tech/community sources (Lobste.rs, DEV.to, HN, Reddit, GitHub
        # Trending, Wikipedia) return unfiltered or loosely-filtered content that
        # is irrelevant to consumer/retail verticals. Restrict them to tech, AI,
        # science, and leadership categories only. The curated RSS feeds
        # (retail_consumer, food_beverage, hospitality_travel, property_energy,
        # circular_fashion) still cover the consumer verticals, so this removes
        # noise without losing coverage.
        generic_sources = {"lobsters", "devto", "hacker_news", "reddit",
                           "github_trending", "wikipedia"}
        generic_categories = {
            # AI / science / investment
            "ai_ml", "post_quantum_encryption", "agentic_ai", "agi",
            "data_architectures", "science_discoveries", "fintech_insurtech",
            "vc_pe_investment", "vc_pe_markets",
            # C-suite / leadership / strategy
            "cdo_data_ai", "robotics_edge", "leadership_management",
            "transformation_change", "strategy_innovation", "public_finance",
            "cio_leadership", "cdo_digital_officer", "cio_investment_officer",
            "cto_transformation", "c_suite_digital", "digital_strategy",
            "leadership", "management", "strategy", "innovation",
        }
        if category_key and category_key not in generic_categories:
            enabled_sources = [s for s in enabled_sources if s not in generic_sources]

    all_items = []
    seen_urls = set()

    for source in enabled_sources:
        items = []
        try:
            if source == "google_news":
                items = fetch_google_news(query, max_per_source)
            elif source == "hacker_news":
                hn_query = HN_TOPICS.get(category_key, query)
                items = fetch_hacker_news(hn_query, max_per_source)
            elif source == "reddit":
                subreddits = REDDIT_SUBREDDITS.get(category_key, ["technology", "business"])
                for sub in subreddits[:2]:
                    items.extend(fetch_reddit(sub, query, max(1, max_per_source // 2)))
            elif source == "rss_feeds":
                # Map category to relevant RSS groups
                rss_group_map = {
                    "fashion_fast_fashion": ["retail_consumer", "tech_business", "circular_fashion"],
                    "performance_athleisure": ["retail_consumer", "tech_business"],
                    "retail": ["retail_consumer", "tech_business"],
                    "supermarket": ["retail_consumer"],
                    "ecommerce": ["retail_consumer", "tech_business"],
                    "sustainability": ["science_health", "circular_fashion"],
                    "fnb_fast_food": ["food_beverage", "retail_consumer"],
                    "halal_business": ["food_beverage", "retail_consumer"],
                    "loyalty_rewards": ["retail_consumer", "startups_venture"],
                    "food_delivery": ["retail_consumer", "tech_business"],
                    "food_security": ["global_development", "science_health"],
                    "consumption_patterns": ["food_beverage", "economics_policy"],
                    "hotel_hospitality": ["hospitality_travel", "retail_consumer"],
                    "tourism": ["hospitality_travel", "global_development"],
                    "co_living": ["hospitality_travel", "property_energy"],
                    "experiential_travel": ["hospitality_travel", "retail_consumer"],
                    "retirement_living": ["science_health"],
                    "tcm": ["science_health"],
                    "rehabilitation_care": ["science_health"],
                    "property_development": ["property_energy"],
                    "construction_materials": ["property_energy"],
                    "gen_z_purchasing": ["retail_consumer", "economics_policy"],
                    "property_asset_ownership": ["property_energy", "economics_policy"],
                    "asean_transactions": ["property_energy"],
                    "data_centers": ["property_energy", "tech_business"],
                    "infrastructure_projects": ["property_energy"],
                    "renewable_energy": ["property_energy", "science_health"],
                    "ai_ml": ["ai_tech", "tech_business"],
                    "post_quantum_encryption": ["ai_tech", "tech_business"],
                    "fintech_insurtech": ["startups_venture", "economics_policy"],
                    "data_architectures": ["ai_tech", "tech_business"],
                    "agentic_ai": ["ai_tech", "tech_business"],
                    "agi": ["ai_tech"],
                    "vc_pe_investment": ["startups_venture", "economics_policy"],
                    "science_discoveries": ["science_health", "ai_science", "ai_tech"],
                    "cio_leadership": ["tech_business", "startups_venture", "consulting_leadership"],
                    "cdo_digital_officer": ["tech_business", "consulting_leadership"],
                    "cio_investment_officer": ["economics_policy", "startups_venture", "consulting_leadership"],
                    "cto_transformation": ["tech_business", "consulting_leadership"],
                    "c_suite_digital": ["tech_business", "consulting_leadership"],
                    "digital_strategy": ["tech_business", "startups_venture", "consulting_leadership"],
                    "leadership": ["consulting_leadership", "tech_business"],
                    "management": ["consulting_leadership", "tech_business"],
                    "strategy": ["consulting_leadership", "economics_policy"],
                    "innovation": ["consulting_leadership", "ai_tech"],
                }
                groups = rss_group_map.get(category_key, ["tech_business", "economics_policy", "ai_tech", "newsletters"])
                for group in groups:
                    for feed_url in MARKET_RSS_FEEDS.get(group, []):
                        items.extend(fetch_rss_feed(feed_url, max(1, max_per_source // 2)))
                        if len(items) >= max_per_source * 2:
                            break
                    if len(items) >= max_per_source * 2:
                        break
            elif source == "lobsters":
                items = fetch_lobsters(max_per_source)
            elif source == "devto":
                devto_tag = category_key.replace("_", "-") if category_key else "technology"
                items = fetch_devto(devto_tag, max_per_source)
            elif source == "github_trending":
                lang_map = {"ai_ml": "python", "science": "python", "technology": "javascript"}
                lang = lang_map.get(category_key, "")
                items = fetch_github_trending(lang, max_per_source)
            elif source == "wikipedia":
                items = fetch_wikipedia_current(max_per_source)
            elif source == "arxiv":
                items = fetch_arxiv(query, max_per_source)
            elif source == "semantic_scholar":
                items = fetch_semantic_scholar(query, max_per_source)
            elif source == "openalex":
                items = fetch_openalex(query, max_per_source)
            elif source == "crossref":
                items = fetch_crossref(query, max_per_source)
            elif source == "pubmed":
                items = fetch_pubmed(query, max_per_source)
            elif source == "core":
                items = fetch_core(query, max_per_source)
            elif source == "headless_consulting":
                from headless_fetcher import fetch_leadership_headless
                import time as _time
                t0 = _time.time()
                items = fetch_leadership_headless(max_per_source)
                elapsed = _time.time() - t0
                if elapsed > 5:
                    print(f"  ⚠️  headless_consulting took {elapsed:.1f}s")
            elif source == "headless_academic":
                from headless_fetcher import fetch_academic_headless
                import time as _time
                t0 = _time.time()
                items = fetch_academic_headless(max_per_source)
                elapsed = _time.time() - t0
                if elapsed > 5:
                    print(f"  ⚠️  headless_academic took {elapsed:.1f}s")
            elif source == "gnews":
                items = fetch_gnews(query, max_per_source)
            elif source == "guardian":
                items = fetch_guardian(query, max_per_source)
            elif source == "newsapi":
                items = fetch_newsapi(query, max_per_source)
        except Exception:
            pass  # One source failing shouldn't kill the whole fetch

        # Deduplicate within this batch
        for item in items:
            url = item.get("url", "")
            if isinstance(url, str) and url and url not in seen_urls:
                seen_urls.add(url)
                all_items.append(item)

    # Sort by source priority (curated RSS first), then by date (newest first).
    # Curated RSS feeds are the highest-signal, most on-topic sources; Google
    # News is a solid secondary; generic/community sources rank last.
    _curated_domains = set()
    for _feeds in MARKET_RSS_FEEDS.values():
        for _u in _feeds:
            _host = _u.split("/")[2] if "//" in _u else _u
            _curated_domains.add(_host)

    def _source_priority(item):
        src = item.get("source", "")
        if src in _curated_domains:
            return 0
        if src == "Google News":
            return 1
        return 2

    def _sort_key(item):
        date_str = item.get("date", "")
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%fZ", "%a, %d %b %Y %H:%M:%S %Z", "%Y-%m-%d"]:
            try:
                # Negate priority so curated (0) sorts first under reverse=True
                return (-_source_priority(item), datetime.strptime(date_str[:25].strip(), fmt))
            except (ValueError, IndexError):
                continue
        return (-_source_priority(item), datetime.min)

    all_items.sort(key=_sort_key, reverse=True)

    # Relevance is deliberately enforced after all source adapters have produced
    # a uniform item shape. This applies equally to query-scoped Google News and
    # unscoped RSS/community feeds, and prevents a feed's recency from becoming
    # an implicit category classifier.
    if category_key:
        all_items = [item for item in all_items if is_category_relevant(item, category_key)]
    return all_items


def fetch_with_summaries(query: str, category_key: str = "",
                         max_per_source: int = MAX_ITEMS_PER_SOURCE,
                         enabled_sources: Optional[List[str]] = None,
                         fetch_summaries: bool = True,
                         academic: Optional[bool] = None) -> List[Dict]:
    """Fetch news and optionally fetch full article summaries.

    Returns items with summaries populated (fetched from article pages).
    `academic` is passed through to fetch_from_all_sources to control scholarly
    sources per-vertical.
    """
    items = fetch_from_all_sources(query, category_key, max_per_source,
                                   enabled_sources, academic=academic)
    if fetch_summaries:
        for item in items:
            if not item.get("summary") or len(item["summary"]) < 50:
                item["summary"] = fetch_article_summary(item["url"])
    return items
