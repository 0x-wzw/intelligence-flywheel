#!/usr/bin/env python3
"""Daily Market Intelligence Publisher — fetches fresh news from expanded sources,
deduplicates against KV data, and writes only new articles to Cloudflare KV.

Runs daily at 07:00 via cron. Output delivered to Telegram + Dashboard.

Sources:
- Google News RSS (6 verticals)
- arXiv papers (AI/ML)
- Company blogs (OpenAI, Anthropic, Google AI, etc.)
- Thought leader feeds
- Community aggregators (HN, Reddit)
"""
import json
import os
import re
import html
import urllib.parse
import urllib.request
import feedparser
from datetime import datetime, timedelta

# ── Config ────────────────────────────────────────────────────────────
CF_ACCOUNT_ID = "fcd22318e9db7ef66952e112114cd8ae"
CF_KV_ID = "07079ee6e9d44315b117d966fb9f8477"
VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "daily-insights-publish.json")


def _load_env_file(path=os.path.expanduser("~/.hermes/.env")):
    """Read KEY=VALUE pairs from ~/.hermes/.env (cron does not source shell rc files)."""
    values = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                line = line[7:].strip() if line.startswith("export ") else line
                if "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip("\"'")
    except OSError:
        pass
    return values


def _resolve_cf_token():
    """
    Resolve the Cloudflare KV token, failing loudly on the known bad states.

    No hardcoded fallback: this file previously carried a literal `cfat_…` token
    that was revoked (API returns 'Invalid API Token'), so the script crashed with
    a raw 401 traceback every run while looking configured.
    """
    env_file = _load_env_file()
    for name in ("CF_API_TOKEN", "CLOUDFLARE_API_TOKEN"):
        token = os.environ.get(name) or env_file.get(name, "")
        token = token.strip()
        if not token:
            continue
        # ~/.hermes/.env currently holds the 32-char ACCOUNT ID under the
        # CLOUDFLARE_API_TOKEN name. Cloudflare rejects it with error 6111
        # ("Invalid format for Authorization header"), which reads like a
        # permissions problem rather than the misconfiguration it is.
        if re.fullmatch(r"[0-9a-f]{32}", token):
            print(
                f"⚠️  {name} looks like an ACCOUNT ID (32 hex chars), not an API token. "
                "Cloudflare API tokens are ~40 chars. Ignoring it."
            )
            continue
        return token, name
    return "", ""


CF_API_TOKEN, CF_TOKEN_SOURCE = _resolve_cf_token()


def kv_get(key):
    """Get a value from Cloudflare KV."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_ID}/values/{key}"
    headers = {"Authorization": f"Bearer {CF_API_TOKEN}"}
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def kv_put(key, value):
    """Write a value to Cloudflare KV."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/storage/kv/namespaces/{CF_KV_ID}/values/{key}"
    headers = {
        "Authorization": f"Bearer {CF_API_TOKEN}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=json.dumps(value).encode(), headers=headers, method="PUT")
    resp = urllib.request.urlopen(req, timeout=15)
    return json.loads(resp.read())


def fetch_news(query, max_results=5):
    """Fetch news headlines from Google News RSS."""
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        data = urllib.request.urlopen(url, timeout=10).read().decode("utf-8")
        items = re.findall(
            r"<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?<pubDate>(.*?)</pubDate>.*?</item>",
            data, re.DOTALL,
        )
        results = []
        for title, link, date in items[:max_results]:
            clean_title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
            results.append({"title": clean_title, "url": link.strip(), "date": date.strip(), "source": "Google News"})
        return results
    except Exception as e:
        return []


def fetch_arxiv(category, max_results=3):
    """Fetch papers from arXiv."""
    url = f"http://arxiv.org/rss/{category}"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req, timeout=10)
        content = response.read().decode('utf-8')
        feed = feedparser.parse(content)
        results = []
        for entry in feed.entries[:max_results]:
            title = html.unescape(entry.title) if hasattr(entry, 'title') else ''
            summary = html.unescape(re.sub(r'<[^>]+>', '', entry.summary[:150])) if hasattr(entry, 'summary') else ''
            results.append({
                "title": f"[arXiv] {title}",
                "url": entry.link if hasattr(entry, 'link') else '',
                "date": entry.published if hasattr(entry, 'published') else '',
                "source": f"arXiv:{category}",
                "summary": summary
            })
        return results
    except Exception as e:
        return []


def fetch_feed_items(url, source_name, max_results=3):
    """Fetch from any RSS/Atom feed."""
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        response = urllib.request.urlopen(req, timeout=10)
        content = response.read().decode('utf-8')
        feed = feedparser.parse(content)
        results = []
        for entry in feed.entries[:max_results]:
            title = html.unescape(entry.title) if hasattr(entry, 'title') else ''
            results.append({
                "title": title,
                "url": entry.link if hasattr(entry, 'link') else '',
                "date": entry.published if hasattr(entry, 'published') else '',
                "source": source_name
            })
        return results
    except Exception as e:
        return []


def fetch_sitemap_posts(sitemap_url, name, max_results=3):
    """Fetch recent blog posts from a sitemap.xml (for sites without RSS).
    Falls back to scraping the blog listing page for Next.js sites.
    """
    try:
        req = urllib.request.Request(
            sitemap_url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; IntelligenceBot/1.0)'}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        content = resp.read().decode('utf-8', errors='replace')
        import xml.etree.ElementTree as ET
        root = ET.fromstring(content)
        ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        urls = root.findall('.//sm:url', ns) or root.findall('.//url')
        blog_posts = []
        for url_elem in urls:
            loc = url_elem.find('sm:loc', ns) or url_elem.find('loc')
            lastmod = url_elem.find('sm:lastmod', ns) or url_elem.find('lastmod')
            if loc is not None and '/blog/' in loc.text:
                blog_posts.append({
                    'url': loc.text.strip(),
                    'date': lastmod.text.strip() if lastmod is not None else '',
                })
        if not blog_posts:
            return _scrape_blog_listing(name, max_results)
        blog_posts.sort(key=lambda x: x['date'], reverse=True)
        blog_posts = blog_posts[:max_results]
        results = []
        for post in blog_posts:
            try:
                req2 = urllib.request.Request(
                    post['url'],
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; IntelligenceBot/1.0)'}
                )
                resp2 = urllib.request.urlopen(req2, timeout=10)
                html2 = resp2.read().decode('utf-8', errors='replace')
                title_match = re.search(r'<title[^>]*>(.*?)</title>', html2, re.IGNORECASE | re.DOTALL)
                title = html.unescape(title_match.group(1).strip()) if title_match else post['url'].split('/')[-1].replace('-', ' ').title()
                title = re.sub(r'\s*[|–-].*$', '', title).strip()
                results.append({
                    'title': title,
                    'url': post['url'],
                    'date': post['date'][:10] if post['date'] else '',
                    'source': f'Company Blog: {name}'
                })
            except Exception:
                slug = post['url'].rstrip('/').split('/')[-1]
                results.append({
                    'title': slug.replace('-', ' ').title(),
                    'url': post['url'],
                    'date': post['date'][:10] if post['date'] else '',
                    'source': f'Company Blog: {name}'
                })
        return results
    except Exception as e:
        return _scrape_blog_listing(name, max_results)


def _scrape_blog_listing(name, max_results=3):
    """Scrape blog listing page for Next.js sites that embed post data in JSON."""
    try:
        blog_url = f"https://www.{name.lower().replace(' ', '')}.co/blog/"
        req = urllib.request.Request(
            blog_url,
            headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        html = resp.read().decode('utf-8', errors='replace')
        results = []
        json_posts = re.findall(r'"slug":"([^"]+)","title":"([^"]+)"', html)
        seen_slugs = set()
        for slug, title in json_posts[:max_results]:
            if slug not in seen_slugs and '/blog/' not in slug:
                seen_slugs.add(slug)
                clean_title = html.unescape(title)
                results.append({
                    'title': clean_title,
                    'url': f"https://www.{name.lower().replace(' ', '')}.co/blog/{slug}/",
                    'date': '',
                    'source': f'Company Blog: {name}'
                })
        if not results:
            post_patterns = re.findall(r'"/blog/([^"/]+)/"', html)
            for slug in post_patterns[:max_results]:
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    results.append({
                        'title': slug.replace('-', ' ').title(),
                        'url': f"https://www.{name.lower().replace(' ', '')}.co/blog/{slug}/",
                        'date': '',
                        'source': f'Company Blog: {name}'
                    })
        return results
    except Exception as e:
        return []


def get_seen_urls():
    """Build a set of all article URLs already stored in KV across all dates."""
    seen = set()
    # Check the last 7 days
    for i in range(7):
        d = datetime.now() - timedelta(days=i)
        key = f"daily-insights-{d.strftime('%Y-%m-%d')}"
        data = kv_get(key)
        if data:
            for cat in data.get("categories", []):
                for a in cat.get("articles", []):
                    seen.add(a["url"])
    # Also check the default key
    data = kv_get("daily-insights")
    if data:
        for cat in data.get("categories", []):
            for a in cat.get("articles", []):
                seen.add(a["url"])
    return seen


def build_insights(seen_urls):
    """Fetch from expanded sources, filtering out already-seen URLs."""
    today = datetime.now().strftime("%Y-%m-%d")
    all_articles = []
    total_new = 0
    total_skipped = 0
    
    # 1. arXiv papers (AI/ML) - highest signal
    print("   📚 Fetching arXiv papers...")
    for category in ['cs.LG', 'cs.AI', 'cs.CL']:
        items = fetch_arxiv(category, max_results=2)
        for item in items:
            if item["url"] not in seen_urls:
                all_articles.append(item)
                seen_urls.add(item["url"])
                total_new += 1
            else:
                total_skipped += 1
    
    # 2. AI Lab Blogs - primary sources
    print("   🏢 Fetching AI lab blogs...")
    ai_labs = [
        ("OpenAI", "https://openai.com/rss"),
        ("Anthropic", "https://www.anthropic.com/rss"),
        ("Google AI", "https://ai.googleblog.com/feeds/posts/default"),
        ("Meta AI", "https://ai.meta.com/blog/rss/"),
    ]
    for name, url in ai_labs:
        items = fetch_feed_items(url, f"AI Lab: {name}", max_results=2)
        for item in items:
            if item["url"] not in seen_urls:
                all_articles.append(item)
                seen_urls.add(item["url"])
                total_new += 1
            else:
                total_skipped += 1
    
    # 2b. Company blogs (sitemap-based for sites without RSS)
    print("   🏢 Fetching company blogs...")
    company_blogs = [
        ("Baseten", "https://www.baseten.co/sitemap.xml"),
    ]
    for name, sitemap_url in company_blogs:
        items = fetch_sitemap_posts(sitemap_url, name, max_results=3)
        for item in items:
            if item["url"] not in seen_urls:
                all_articles.append(item)
                seen_urls.add(item["url"])
                total_new += 1
            else:
                total_skipped += 1
    
    # 3. Thought Leaders - balanced perspectives
    print("   🧠 Fetching thought leaders...")
    thought_leaders = [
        ("Andrew Ng", "https://www.deeplearning.ai/feed/", "pragmatic"),
        ("Gary Marcus", "https://garymarcus.substack.com/feed", "skeptic"),
        ("Ben Thompson", "https://stratechery.com/feed/", "strategy"),
        ("Intuition Machine", "https://medium.com/feed/intuitionmachine", "agentic-ai, graph-engineering"),
        ("Peter Steinberger", "https://steipete.me/rss.xml", "graph-engineering, agentic-ai"),
    ]
    for name, url, perspective in thought_leaders:
        items = fetch_feed_items(url, f"Leader: {name}", max_results=2)
        for item in items:
            if item["url"] not in seen_urls:
                item["perspective"] = perspective
                all_articles.append(item)
                seen_urls.add(item["url"])
                total_new += 1
            else:
                total_skipped += 1
    
    # 4. Community Aggregators
    print("   💬 Fetching community discussions...")
    aggregators = [
        ("Hacker News AI", "https://hnrss.org/newest?q=artificial+intelligence"),
        ("LessWrong", "https://www.lesswrong.com/feed.xml"),
    ]
    for name, url in aggregators:
        items = fetch_feed_items(url, name, max_results=3)
        for item in items:
            if item["url"] not in seen_urls:
                all_articles.append(item)
                seen_urls.add(item["url"])
                total_new += 1
            else:
                total_skipped += 1
    
    # 5. Google News - broad coverage (existing queries)
    print("   📰 Fetching Google News...")
    queries = {
        "AI & Data Strategy": "chief data officer CDO AI strategy data-driven transformation executive 2026",
        "Robotics & Edge": "robotics edge computing industrial automation C-suite strategy investment 2026",
        "Leadership & Management": "leadership style management style organizational culture change transformation executive 2026",
        "Transformation & Change": "digital transformation change management business transformation operating model strategy 2026",
        "Strategy & Innovation": "corporate strategy business innovation competitive advantage growth strategy C-suite 2026",
        "VC PE & Private Markets": "venture capital private equity growth equity fundraising deal flow startup investment 2026",
        "Public Markets & Finance": "public markets IPO SPAC M&A corporate finance capital markets institutional investor 2026",
        "F&B & Consumer": "food beverage fast food halal loyalty delivery trends 2026",
        "Hospitality & Healthcare": "hotel hospitality tourism co-living retirement TCM rehab trends 2026",
        "Fashion & Retail": "fashion retail supermarket ecommerce sustainability trends 2026",
        "Property & Infrastructure": "property development construction data center renewable energy trends 2026",
        "AI & Science": "AI ML agentic AI AGI fintech quantum VC investment science 2026",
    }
    
    categories = {}
    for name, query in queries.items():
        items = fetch_news(query, max_results=3)
        cat_articles = []
        for item in items:
            # Skip articles older than 14 days
            date_str = item.get("date", "")
            if date_str:
                try:
                    from datetime import timedelta
                    pub_date = datetime.strptime(date_str[:16], "%a, %d %b %Y")
                    if datetime.now() - pub_date > timedelta(days=14):
                        total_skipped += 1
                        continue
                except:
                    pass
            if item["url"] in seen_urls:
                total_skipped += 1
            else:
                cat_articles.append(item)
                seen_urls.add(item["url"])
                total_new += 1
        categories[name] = cat_articles
    
    # Organize all articles by category for the response
    result_categories = []
    
    # Add arXiv as category
    arxiv_articles = [a for a in all_articles if a.get("source", "").startswith("arXiv")]
    if arxiv_articles:
        result_categories.append({"name": "🔬 Research Papers", "articles": arxiv_articles[:5]})
    
    # Add AI Labs as category
    lab_articles = [a for a in all_articles if a.get("source", "").startswith("AI Lab")]
    if lab_articles:
        result_categories.append({"name": "🏢 AI Labs", "articles": lab_articles[:5]})
    
    # Add Company Blogs as category
    company_articles = [a for a in all_articles if a.get("source", "").startswith("Company Blog")]
    if company_articles:
        result_categories.append({"name": "🏢 Company Blogs", "articles": company_articles[:5]})
    
    # Add Thought Leaders as category
    leader_articles = [a for a in all_articles if a.get("source", "").startswith("Leader:")]
    if leader_articles:
        result_categories.append({"name": "🧠 Thought Leaders", "articles": leader_articles[:5]})
    
    # Add Community as category
    community_articles = [a for a in all_articles if a.get("source", "") in ["Hacker News AI", "LessWrong"]]
    if community_articles:
        result_categories.append({"name": "💬 Community", "articles": community_articles[:5]})
    
    # Add Google News categories
    for name, articles in categories.items():
        if articles:
            result_categories.append({"name": name, "articles": articles})
    
    return {
        "date": today,
        "generated_at": datetime.now().isoformat(),
        "version": "2.0",  # Updated version for expanded sources
        "categories": result_categories,
        "summary": {
            "total_categories": len(result_categories),
            "total_articles": total_new,
            "total_skipped": total_skipped,
            "sources": ["arXiv", "AI Labs", "Thought Leaders", "Community", "Google News"]
        },
    }


def main():
    today = datetime.now().strftime("%Y-%m-%d")

    # 0. Credential preflight — exit cleanly rather than throwing a raw 401
    #    traceback from inside kv_get() on every scheduled run.
    if not CF_API_TOKEN:
        print("🔴 No usable Cloudflare API token found.")
        print("   Checked: $CF_API_TOKEN, $CLOUDFLARE_API_TOKEN, ~/.hermes/.env")
        print("   Create one with Account → Workers KV Storage → Edit, scoped to")
        print(f"   account {CF_ACCOUNT_ID}, then:")
        print('     echo \'CF_API_TOKEN="cfat_..."\' >> ~/.hermes/.env')
        print("   See ~/.hermes/scripts/FIX-CLOUDFLARE-TOKEN.md for the full walkthrough.")
        raise SystemExit(1)

    print(f"🔑 Using Cloudflare token from {CF_TOKEN_SOURCE}")

    # 1. Build seen URLs from existing KV data
    print("🔍 Checking existing articles in KV...")
    seen_urls = get_seen_urls()
    print(f"   Found {len(seen_urls)} existing article URLs across last 7 days")

    # 2. Fetch fresh news, deduplicated
    print("📡 Fetching fresh news...")
    insights = build_insights(seen_urls)
    total = insights["summary"]["total_articles"]
    skipped = insights["summary"]["total_skipped"]
    print(f"   {total} new articles, {skipped} skipped (already seen)")

    # 3. Write to KV
    if total > 0:
        # Write as date-specific key
        date_key = f"daily-insights-{today}"
        result = kv_put(date_key, insights)
        print(f"   ✅ Written to KV: {date_key}")

        # Also update the default key
        result = kv_put("daily-insights", insights)
        print(f"   ✅ Updated default: daily-insights")
    else:
        print("   ⏭️  No new articles to write")

    # 4. Save health log
    health = {
        "timestamp": datetime.now().isoformat(),
        "date": today,
        "total_new": total,
        "total_skipped": skipped,
        "categories": {c["name"]: len(c["articles"]) for c in insights["categories"]},
    }
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f:
        json.dump(health, f, indent=2)

    # 5. Print summary for Telegram
    print(f"\n📰 Daily Market Intelligence — {today}")
    print(f"  📊 {total} new articles across {insights['summary']['total_categories']} categories")
    if skipped > 0:
        print(f"  ⏭️  {skipped} duplicates skipped")
    for c in insights["categories"]:
        if c["articles"]:
            print(f"    {c['name']}: {len(c['articles'])} new articles")
    print(f"\n🌐 https://intelligence.teoh.my/")

    return 0


if __name__ == "__main__":
    main()
