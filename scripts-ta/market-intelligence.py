#!/usr/bin/env python3
"""Enhanced Market Intelligence Aggregator — fetches from multiple sources:
- Google News RSS (fallback)
- Thought leader feeds (Substack, blogs, Twitter via Nitter)
- Research papers (arXiv)
- Company blogs (OpenAI, Anthropic, Google AI, etc.)
- Policy think tanks
- Community aggregators (HN, Reddit, LessWrong)

Runs daily at 03:00 via cron. Output to Telegram + Vault + optional Notion.
"""
import json
import os
import re
import html
import sys
import hashlib
import urllib.parse
import urllib.request
import feedparser
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────
SCRIPTS_DIR = Path(__file__).parent
VAULT_DIR = Path.home() / "Documents" / "Obsidian Vault"
TRENDS_DIR = VAULT_DIR / "Knowledge" / "00-Raw" / "trends"
SOURCES_FILE = SCRIPTS_DIR / "intelligence-sources.yaml"
HEALTH_LOG = VAULT_DIR / "System" / "market-intelligence-health.json"
SEEN_FILE = Path.home() / ".hermes" / "data" / "seen-urls.json"

# Notion config
NOTION_DATABASE_ID = os.environ.get(
    "NOTION_INTELLIGENCE_DB", 
    "39e05e4a-7c41-81a3-815f-ff9c2e862cc2"
)
NOTION_TOKEN = os.environ.get("NOTION_API_TOKEN", "")

from hermes_feeds import frontmatter_block  # Schema v2 frontmatter, one definition
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hermes_trend_save import save_report as _save_report

def _frontmatter():
    """Schema v2 frontmatter for this report (see System/SCHEMA.md)."""
    return frontmatter_block(
        title=f"Market Intelligence — Cross-Vertical Synthesis — {datetime.now():%Y-%m-%d}",
        tags=['raw', 'trends', 'market-intelligence'],
        note_type="raw",
        domain="knowledge",
    ) + "\n"


# ── Simple YAML Parser (no external deps) ───────────────────────────
def parse_simple_yaml(content):
    """Parse our simplified YAML structure into nested dicts."""
    result = {}
    current_section = None
    current_subsection = None
    current_list = None
    current_item = {}
    
    for line in content.split('\n'):
        if not line.strip() or line.strip().startswith('#'):
            continue
        
        # Count indentation
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        
        if stripped.endswith(':') and not stripped.startswith('-'):
            # Section header
            key = stripped[:-1]
            if indent == 0:
                current_section = key
                result[key] = {}
                current_subsection = None
            elif indent == 2 and current_section:
                current_subsection = key
                if isinstance(result[current_section], dict):
                    result[current_section][key] = []
                current_list = result[current_section][key] if isinstance(result[current_section], dict) else None
        elif stripped.startswith('- '):
            # List item
            item_content = stripped[2:]
            if ':' in item_content:
                # Inline dict start: - name: "Value"
                key, val = item_content.split(':', 1)
                current_item = {key.strip(): val.strip().strip('"')}
            else:
                current_item = {'value': item_content}
            
            if current_list is not None:
                current_list.append(current_item)
        elif ':' in stripped and current_item:
            # Continuation of list item dict
            key, val = stripped.split(':', 1)
            current_item[key.strip()] = val.strip().strip('"')
    
    return result


# ── Deduplication ────────────────────────────────────────────────────
class SeenUrls:
    """Multi-layered deduplication with URL normalization and title fingerprinting."""
    
    def __init__(self, max_age_days=7):
        self.urls = set()
        self.fingerprints = set()
        self.max_age = timedelta(days=max_age_days)
        self.data = {'urls': [], 'fingerprints': [], 'last_updated': None}
        
    def load(self):
        if SEEN_FILE.exists():
            try:
                with open(SEEN_FILE) as f:
                    self.data = json.load(f)
                self.urls = set(self.data.get('urls', []))
                self.fingerprints = set(self.data.get('fingerprints', []))
            except:
                pass
    
    def save(self):
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.data['urls'] = list(self.urls)[-2000:]  # Keep last 2000
        self.data['fingerprints'] = list(self.fingerprints)[-2000:]
        self.data['last_updated'] = datetime.now().isoformat()
        with open(SEEN_FILE, 'w') as f:
            json.dump(self.data, f, indent=2)
    
    def _normalize_url(self, url):
        """Strip tracking params and normalize."""
        if not url:
            return ""
        parsed = urllib.parse.urlparse(url)
        # Remove common tracking params
        query = urllib.parse.parse_qs(parsed.query)
        clean_params = {k: v for k, v in query.items() 
                       if k not in ['utm_source', 'utm_medium', 'utm_campaign', 
                                   'utm_content', 'utm_term', 'fbclid', 'gclid']}
        clean_query = urllib.parse.urlencode(clean_params, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=clean_query))
    
    def _make_fingerprint(self, title, domain):
        """Create fingerprint from title + domain."""
        normalized = re.sub(r'[^\w\s]', '', title.lower())
        normalized = ' '.join(normalized.split())
        return hashlib.md5(f"{domain}:{normalized}".encode()).hexdigest()[:16]
    
    def is_new(self, url, title="", date_str=None):
        """Check if article is new (not seen and not too old)."""
        if not url:
            return False
        
        # Check URL
        normalized = self._normalize_url(url)
        if normalized in self.urls:
            return False
        
        # Check fingerprint
        domain = urllib.parse.urlparse(url).netloc
        fp = self._make_fingerprint(title, domain)
        if fp in self.fingerprints:
            return False
        
        # Check date freshness
        if date_str:
            try:
                pub_date = datetime.strptime(date_str[:10], "%Y-%m-%d")
                if datetime.now() - pub_date > self.max_age:
                    return False
            except:
                pass
        
        return True
    
    def mark(self, url, title=""):
        """Mark URL and fingerprint as seen."""
        normalized = self._normalize_url(url)
        self.urls.add(normalized)
        domain = urllib.parse.urlparse(url).netloc
        fp = self._make_fingerprint(title, domain)
        self.fingerprints.add(fp)


# ── Feed Fetching ────────────────────────────────────────────────────
def fetch_feed(url, timeout=10):
    """Fetch and parse RSS/Atom feed."""
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; IntelligenceBot/1.0)'}
        )
        response = urllib.request.urlopen(req, timeout=timeout)
        content = response.read().decode('utf-8')
        return feedparser.parse(content)
    except Exception as e:
        return None


def fetch_google_news(query, max_results=5):
    """Fetch from Google News RSS."""
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
    feed = fetch_feed(url)
    if not feed or not feed.entries:
        return []
    
    results = []
    for entry in feed.entries[:max_results]:
        results.append({
            'title': html.unescape(entry.title) if hasattr(entry, 'title') else '',
            'url': entry.link if hasattr(entry, 'link') else '',
            'date': entry.published if hasattr(entry, 'published') else '',
            'source': 'Google News'
        })
    return results


def fetch_arxiv(category, max_results=5):
    """Fetch papers from arXiv."""
    url = f"http://arxiv.org/rss/{category}"
    feed = fetch_feed(url)
    if not feed or not feed.entries:
        return []
    
    results = []
    for entry in feed.entries[:max_results]:
        # Extract arXiv ID and abstract
        arxiv_id = entry.id.split('/')[-1] if hasattr(entry, 'id') else ''
        summary = entry.summary if hasattr(entry, 'summary') else ''
        # Clean summary (arXiv has LaTeX)
        summary = re.sub(r'\$.+?\$', '', summary)  # Remove inline math
        summary = html.unescape(re.sub(r'<[^>]+>', '', summary))
        
        results.append({
            'title': html.unescape(entry.title) if hasattr(entry, 'title') else '',
            'url': entry.id if hasattr(entry, 'id') else '',
            'date': entry.published if hasattr(entry, 'published') else '',
            'source': f'arXiv:{category}',
            'summary': summary[:200] + '...' if len(summary) > 200 else summary,
            'arxiv_id': arxiv_id
        })
    return results


def fetch_thought_leader_feed(feed_url, name, max_results=3):
    """Fetch from a thought leader's feed."""
    feed = fetch_feed(feed_url)
    if not feed or not feed.entries:
        return []
    
    results = []
    for entry in feed.entries[:max_results]:
        results.append({
            'title': html.unescape(entry.title) if hasattr(entry, 'title') else '',
            'url': entry.link if hasattr(entry, 'link') else '',
            'date': entry.published if hasattr(entry, 'published') else '',
            'source': f'Thought Leader: {name}'
        })
    return results


def fetch_sitemap_posts(sitemap_url, name, max_results=5):
    """Fetch recent blog posts from a sitemap.xml (for sites without RSS).
    
    Falls back to scraping the blog listing page for Next.js sites
    that embed post data in the HTML.
    """
    try:
        req = urllib.request.Request(
            sitemap_url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; IntelligenceBot/1.0)'}
        )
        resp = urllib.request.urlopen(req, timeout=15)
        content = resp.read().decode('utf-8', errors='replace')
        
        # Parse sitemap XML for blog post URLs
        import xml.etree.ElementTree as ET
        root = ET.fromstring(content)
        
        # Handle both sitemap index and regular sitemaps
        ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        urls = root.findall('.//sm:url', ns) or root.findall('.//url')
        
        # Filter to blog posts and sort by lastmod descending
        blog_posts = []
        for url_elem in urls:
            loc = url_elem.find('sm:loc', ns) or url_elem.find('loc')
            lastmod = url_elem.find('sm:lastmod', ns) or url_elem.find('lastmod')
            if loc is not None and '/blog/' in loc.text:
                blog_posts.append({
                    'url': loc.text.strip(),
                    'date': lastmod.text.strip() if lastmod is not None else '',
                })
        
        # If sitemap has no individual blog posts, try scraping the blog listing page
        if not blog_posts:
            return _scrape_blog_listing(name, max_results)
        
        # Sort by lastmod descending, take most recent
        blog_posts.sort(key=lambda x: x['date'], reverse=True)
        blog_posts = blog_posts[:max_results]
        
        # Fetch titles from each blog post page
        results = []
        for post in blog_posts:
            try:
                req2 = urllib.request.Request(
                    post['url'],
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; IntelligenceBot/1.0)'}
                )
                resp2 = urllib.request.urlopen(req2, timeout=10)
                html = resp2.read().decode('utf-8', errors='replace')
                
                # Extract title from <title> tag
                title_match = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
                title = html.unescape(title_match.group(1).strip()) if title_match else post['url'].split('/')[-1].replace('-', ' ').title()
                # Clean title (remove site name suffix like " | Baseten")
                title = re.sub(r'\s*[|–-].*$', '', title).strip()
                
                results.append({
                    'title': title,
                    'url': post['url'],
                    'date': post['date'][:10] if post['date'] else '',
                    'source': f'Company Blog: {name}'
                })
            except Exception:
                # If we can't fetch the page, still include with URL-derived title
                slug = post['url'].rstrip('/').split('/')[-1]
                title = slug.replace('-', ' ').title()
                results.append({
                    'title': title,
                    'url': post['url'],
                    'date': post['date'][:10] if post['date'] else '',
                    'source': f'Company Blog: {name}'
                })
        
        return results
    except Exception as e:
        return _scrape_blog_listing(name, max_results)


def _scrape_blog_listing(name, max_results=5):
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
        
        # Try to extract blog posts from Next.js __NEXT_DATA__ or embedded JSON
        # Pattern: find blog post slugs and titles in the page source
        # Baseten embeds post data in __NEXT_DATA__ or as JSON in script tags
        
        # Method 1: Look for blog post slugs in the HTML
        post_patterns = re.findall(
            r'"/blog/([^"/]+)/"',
            html
        )
        
        # Method 2: Look for title patterns near blog links
        title_patterns = re.findall(
            r'<h4[^>]*>([^<]+)</h4>',
            html
        )
        
        # Method 3: Extract from JSON-like structures
        # Look for "slug":"...","title":"..." patterns
        json_posts = re.findall(
            r'"slug":"([^"]+)","title":"([^"]+)"',
            html
        )
        
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
        
        # Fallback: use slug-only extraction
        if not results:
            for slug in post_patterns[:max_results]:
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    title = slug.replace('-', ' ').title()
                    results.append({
                        'title': title,
                        'url': f"https://www.{name.lower().replace(' ', '')}.co/blog/{slug}/",
                        'date': '',
                        'source': f'Company Blog: {name}'
                    })
        
        return results
    except Exception as e:
        return []


# ── Main Intelligence Builder ────────────────────────────────────────
def build_intelligence():
    """Fetch intelligence from all configured sources."""
    seen = SeenUrls()
    seen.load()
    
    # Load sources config
    sources = {}
    if SOURCES_FILE.exists():
        with open(SOURCES_FILE) as f:
            sources = parse_simple_yaml(f.read())
    
    today = datetime.now().strftime("%Y-%m-%d")
    all_data = {}
    
    # 1. arXiv papers (high signal)
    print("📚 Fetching research papers...")
    arxiv_categories = ['cs.LG', 'cs.AI', 'cs.CL', 'quant-ph']
    arxiv_items = []
    for cat in arxiv_categories:
        items = fetch_arxiv(cat, max_results=3)
        for item in items:
            if seen.is_new(item['url'], item['title'], item.get('date')):
                arxiv_items.append(item)
                seen.mark(item['url'], item['title'])
    all_data['research_papers'] = arxiv_items[:5]
    
    # 2. AI Lab Blogs + Company Blogs (primary sources)
    print("🔬 Fetching AI lab announcements...")
    ai_labs = [
        ('OpenAI', 'https://openai.com/rss'),
        ('Anthropic', 'https://www.anthropic.com/rss'),
        ('Google AI', 'https://ai.googleblog.com/feeds/posts/default'),
        ('Meta AI', 'https://ai.meta.com/blog/rss/'),
        ('DeepMind', 'https://deepmind.google/discover/blog/rss/'),
    ]
    ai_lab_items = []
    for name, url in ai_labs:
        items = fetch_thought_leader_feed(url, name, max_results=2)
        for item in items:
            if seen.is_new(item['url'], item['title'], item.get('date')):
                ai_lab_items.append(item)
                seen.mark(item['url'], item['title'])
    all_data['ai_lab_news'] = ai_lab_items[:5]

    # 2b. Company blogs (some use sitemap instead of RSS)
    print("🏢 Fetching company blogs...")
    company_blogs = [
        ('Baseten', 'https://www.baseten.co/sitemap.xml'),
    ]
    company_items = []
    for name, sitemap_url in company_blogs:
        items = fetch_sitemap_posts(sitemap_url, name, max_results=3)
        for item in items:
            if seen.is_new(item['url'], item['title'], item.get('date')):
                company_items.append(item)
                seen.mark(item['url'], item['title'])
    all_data['company_blogs'] = company_items[:5]
    
    # 3. Thought Leaders (balanced perspectives)
    print("🧠 Fetching thought leader insights...")
    thought_leaders = [
        ('Yann LeCun', 'https://www.facebook.com/yann.lecun/rss', 'pro-open-source'),
        ('Andrew Ng', 'https://www.deeplearning.ai/feed/', 'pragmatic'),
        ('Gary Marcus', 'https://garymarcus.substack.com/feed', 'skeptic'),
        ('Ben Thompson', 'https://stratechery.com/feed/', 'strategy'),
        ('Ben Horowitz', 'https://a16z.com/feed/', 'vc-perspective'),
        ('Intuition Machine', 'https://medium.com/feed/intuitionmachine', 'agentic-ai, graph-engineering'),
        ('Peter Steinberger', 'https://steipete.me/rss.xml', 'graph-engineering, agentic-ai'),
    ]
    leader_items = []
    for name, url, perspective in thought_leaders:
        items = fetch_thought_leader_feed(url, name, max_results=2)
        for item in items:
            if seen.is_new(item['url'], item['title'], item.get('date')):
                item['perspective'] = perspective
                leader_items.append(item)
                seen.mark(item['url'], item['title'])
    all_data['thought_leaders'] = leader_items[:8]
    
    # 4. Aggregators (HN, Reddit, LessWrong)
    print("📰 Fetching community discussions...")
    aggregators = [
        ('Hacker News AI', 'https://hnrss.org/newest?q=artificial+intelligence'),
        ('LessWrong', 'https://www.lesswrong.com/feed.xml'),
    ]
    agg_items = []
    for name, url in aggregators:
        items = fetch_thought_leader_feed(url, name, max_results=3)
        for item in items:
            if seen.is_new(item['url'], item['title'], item.get('date')):
                agg_items.append(item)
                seen.mark(item['url'], item['title'])
    all_data['community'] = agg_items[:5]
    
    # 5. Google News (fallback for broad coverage)
    print("🔍 Fetching Google News...")
    google_queries = {
        'ai_breakthroughs': 'artificial intelligence breakthrough AGI 2026',
        'agentic_ai': 'agentic AI autonomous agents workflow',
        'ai_investment': 'AI investment venture capital funding 2026',
        'ai_policy': 'AI regulation policy government 2026',
    }
    google_items = []
    for category, query in google_queries.items():
        items = fetch_google_news(query, max_results=3)
        for item in items:
            if seen.is_new(item['url'], item['title'], item.get('date')):
                google_items.append(item)
                seen.mark(item['url'], item['title'])
    all_data['google_news'] = google_items[:8]
    
    seen.save()
    return all_data


def format_report(all_data):
    """Format intelligence data as markdown report."""
    today = datetime.now().strftime("%Y-%m-%d")
    report = f"# Market Intelligence Brief — {today}\n\n"
    
    # Research Papers
    if all_data.get('research_papers'):
        report += "## 🔬 Research Papers (arXiv)\n\n"
        for item in all_data['research_papers']:
            report += f"- **[{item['title']}]({item['url']})**\n"
            if item.get('summary'):
                report += f"  - {item['summary']}\n"
        report += "\n"
    
    # AI Lab News
    if all_data.get('ai_lab_news'):
        report += "## 🏢 AI Lab Announcements\n\n"
        for item in all_data['ai_lab_news']:
            report += f"- [{item['title']}]({item['url']}) *({item['source']})*\n"
        report += "\n"
    
    # Company Blogs
    if all_data.get('company_blogs'):
        report += "## 🏢 Company Blogs\n\n"
        for item in all_data['company_blogs']:
            report += f"- [{item['title']}]({item['url']}) *({item['source']})*\n"
        report += "\n"
    
    # Thought Leaders
    if all_data.get('thought_leaders'):
        report += "## 🧠 Thought Leader Insights\n\n"
        perspectives = {}
        for item in all_data['thought_leaders']:
            p = item.get('perspective', 'general')
            if p not in perspectives:
                perspectives[p] = []
            perspectives[p].append(item)
        
        for perspective, items in perspectives.items():
            report += f"### {perspective.replace('-', ' ').title()}\n"
            for item in items:
                report += f"- [{item['title']}]({item['url']})\n"
            report += "\n"
    
    # Community
    if all_data.get('community'):
        report += "## 💬 Community Discussions\n\n"
        for item in all_data['community']:
            report += f"- [{item['title']}]({item['url']}) *({item['source']})*\n"
        report += "\n"
    
    # Google News
    if all_data.get('google_news'):
        report += "## 📰 Industry News\n\n"
        for item in all_data['google_news']:
            report += f"- [{item['title']}]({item['url']})\n"
        report += "\n"
    
    report += f"\n---\n*Generated {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}*\n"
    report += f"*Sources: arXiv, AI Labs, Thought Leaders, Community Aggregators, Google News*\n"
    
    return report


def save_local(report):
    """Save the report locally, MERGING with an existing same-day file.

    Was mode "w": a second run on the same day, emptied by SeenUrls dedup,
    clobbered the first run's report. See hermes_trend_save for the evidence.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    path = TRENDS_DIR / f"market-intelligence-{today}.md"
    return _save_report(path, _frontmatter(), report)

def push_to_notion(report, all_data):
    """Push to Notion database."""
    if not NOTION_TOKEN:
        return "⚠️ No Notion token"
    
    today = datetime.now().strftime("%Y-%m-%d")
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    
    # Build blocks
    children = []
    for category, items in all_data.items():
        if not items:
            continue
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [{"type": "text", "text": {"content": category.replace('_', ' ').title()}}]
            }
        })
        for item in items:
            children.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {
                    "rich_text": [{
                        "type": "text",
                        "text": {
                            "content": item['title'],
                            "link": {"url": item['url']}
                        }
                    }]
                }
            })
    
    page_data = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Title": {"title": [{"text": {"content": f"Market Intelligence — {today}"}}]},
            "Date": {"date": {"start": today}},
        },
        "children": children,
    }
    
    try:
        req = urllib.request.Request(
            "https://api.notion.com/v1/pages",
            data=json.dumps(page_data).encode(),
            headers=headers,
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        return f"✅ Notion: {result.get('id', 'unknown')[:8]}"
    except Exception as e:
        return f"⚠️ Notion failed: {e}"


def main():
    print("🚀 Building Market Intelligence Brief...\n")
    
    all_data = build_intelligence()
    
    # Count totals
    total = sum(len(v) for v in all_data.values())
    print(f"\n📊 Collected {total} items:")
    for cat, items in all_data.items():
        print(f"   {cat}: {len(items)}")
    
    if total == 0:
        print("\n⚠️ No new intelligence found (all deduplicated or sources unavailable)")
        return 0
    
    # Format and save
    report = format_report(all_data)
    local_path = save_local(report)
    print(f"\n💾 Saved: {local_path}")
    
    # Try Notion
    notion_result = push_to_notion(report, all_data)
    print(f"📤 {notion_result}")
    
    # Save health log
    health = {
        "timestamp": datetime.now().isoformat(),
        "total_items": total,
        "by_category": {k: len(v) for k, v in all_data.items()},
    }
    (VAULT_DIR / "System").mkdir(parents=True, exist_ok=True)
    (VAULT_DIR / "System" / "market-intelligence-health.json").write_text(
        json.dumps(health, indent=2)
    )

    # Write to cron log file for traceability
    log_dir = Path(os.path.expanduser("~/.hermes/logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "market-intelligence.log"
    with open(log_file, "a") as f:
        f.write(f"{datetime.now().isoformat()} | items={total} categories={len(all_data)} status=ok\n")
    
    # Telegram-friendly summary
    print(f"\n📰 Market Intelligence — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"   {total} new items across {len(all_data)} categories")
    if all_data.get('research_papers'):
        print(f"   📚 Top paper: {all_data['research_papers'][0]['title'][:60]}...")
    if all_data.get('thought_leaders'):
        print(f"   🧠 Top insight: {all_data['thought_leaders'][0]['title'][:60]}...")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
