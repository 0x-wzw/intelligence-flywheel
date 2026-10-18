"""Shared deduplication module for all intelligence cron scripts.

Multi-layered deduplication:
1. URL exact match — Google News RSS URL
2. URL normalized — strip tracking params, compare base article URL
3. Title fingerprint — domain + normalized title hash
4. Title fuzzy match — Levenshtein similarity for near-duplicate titles
5. Vault cross-reference — check Obsidian vault for existing articles
6. Date freshness — reject articles older than MAX_AGE_DAYS

Usage:
    from dedup import SeenUrls
    seen = SeenUrls()
    seen.load()
    for item in items:
        if seen.is_new(item['url'], item.get('date', ''), item.get('title', '')):
            process(item)
            seen.mark(item['url'], item.get('title', ''))
    seen.save()
"""
import hashlib
import json
import os
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

DATA_DIR = os.path.expanduser("~/.hermes/data")
SEEN_FILE = os.path.join(DATA_DIR, "seen-urls.json")
VAULT_TRENDS_DIR = os.path.expanduser("~/Documents/Obsidian Vault/Knowledge/00-Raw/trends")
MAX_AGE_DAYS = 7
TITLE_SIMILARITY_THRESHOLD = 0.92  # 92% similarity = same article (tightened to avoid false positives)
WORD_OVERLAP_THRESHOLD = 0.70  # 70% word overlap = same article


def _parse_date(date_str):
    """Parse a date string from Google News RSS into a datetime object."""
    if not date_str:
        return None
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    try:
        cleaned = re.sub(r'\s[A-Z]{2,5}$', '', date_str.strip())
        return datetime.strptime(cleaned, "%a, %d %b %Y %H:%M:%S")
    except ValueError:
        pass
    return None


def _normalize_url(url):
    """Normalize a Google News RSS URL to extract the underlying article URL.
    
    Google News RSS wraps article URLs in redirects. This extracts the actual
    source URL when possible, and normalizes the wrapper URL otherwise.
    """
    if not url:
        return ""
    
    # Google News RSS URLs contain the real URL in the path
    # e.g. https://news.google.com/rss/articles/CBM...?oc=5
    # The real article URL is not directly extractable from the RSS wrapper,
    # but we can normalize the wrapper itself
    
    # Strip tracking parameters
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    
    # Keep only essential query params
    clean_query = {}
    for key in ['oc', 'hl', 'ceid']:
        if key in query:
            clean_query[key] = query[key][0]
    
    # Rebuild URL without fragment
    clean = urllib.parse.urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        urllib.parse.urlencode(clean_query) if clean_query else '',
        ''
    ))
    return clean


def _normalize_title(title):
    """Normalize a title for comparison: lowercase, strip punctuation, remove common prefixes."""
    if not title:
        return ""
    t = title.lower().strip()
    # Remove common prefixes like "Top 10", "2026", "Latest:"
    t = re.sub(r'^(top\s+\d+|latest:?\s*|\d{4}[:.\s]*)', '', t)
    # Remove source attribution suffixes like " - Source Name" or " | Source"
    t = re.sub(r'\s*[-–—|]\s*[A-Z][a-zA-Z0-9 .&]+$', '', t)
    # Remove non-alphanumeric characters (keep spaces and letters)
    t = re.sub(r'[^a-z0-9\s]', '', t)
    # Collapse whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _title_fingerprint(title):
    """Create a stable fingerprint from a title: sorted set of significant words.
    
    Strips source names, removes stop words, sorts alphabetically.
    Used for word-overlap comparison between titles.
    """
    if not title:
        return ""
    normalized = _normalize_title(title)
    stop_words = {'the', 'a', 'an', 'in', 'of', 'for', 'to', 'and', 'by', 'is', 'on', 'at', 'its', 'it', 'with', 'from', 'as', 'are', 'was', 'be', 'has', 'have', 'had', 'not', 'but', 'or', 'yet', 'so', 'no', 'all', 'each', 'can', 'will', 'may', 'new', 'top', 'hit', 'usd'}
    words = [w for w in normalized.split() if w not in stop_words and len(w) > 2]
    return sorted(set(words))


def _word_overlap(words_a, words_b):
    """Calculate word overlap ratio between two title word sets.
    Returns 0.0 to 1.0 where 1.0 = identical significant words."""
    if not words_a or not words_b:
        return 0.0
    set_a = set(words_a)
    set_b = set(words_b)
    intersection = set_a & set_b
    union = set_a | set_b
    if not union:
        return 0.0
    return len(intersection) / len(union)


def _extract_domain(url):
    """Extract the source domain from a URL."""
    try:
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix
        domain = re.sub(r'^www\.', '', domain)
        return domain
    except Exception:
        return ""


def _scan_vault_for_urls():
    """Scan the Obsidian vault for existing article URLs and title word sets.
    Returns (url_set, list_of_title_word_sets)."""
    vault_urls = set()
    vault_title_words = []
    
    if not os.path.isdir(VAULT_TRENDS_DIR):
        return vault_urls, vault_title_words

    for fname in os.listdir(VAULT_TRENDS_DIR):
        if not fname.endswith(".md"):
            continue
        fpath = os.path.join(VAULT_TRENDS_DIR, fname)
        try:
            with open(fpath) as f:
                content = f.read()
            # Extract markdown links: [title](url)
            links = re.findall(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', content)
            for title, url in links:
                vault_urls.add(url)
                # Also add the title word set
                words = _title_fingerprint(title)
                if words and words not in vault_title_words:
                    vault_title_words.append(words)
        except (OSError, UnicodeDecodeError):
            continue

    return vault_urls, vault_title_words


class SeenUrls:
    """Multi-layered article deduplication.
    
    Layers:
    1. URL exact match
    2. URL normalized (stripped tracking params)
    3. Title fingerprint (domain + normalized title hash)
    4. Title fuzzy match (Levenshtein similarity)
    5. Vault cross-reference
    6. Date freshness
    """

    def __init__(self):
        self._urls = set()           # Exact URLs
        self._norm_urls = set()      # Normalized URLs
        self._title_words = []       # List of word sets from seen titles (for word overlap)
        self._titles = []            # All seen titles (for fuzzy matching)
        self._new_urls = set()
        self._loaded = False
        self._cutoff_date = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    def load(self):
        """Load seen data from disk and scan vault."""
        if self._loaded:
            return

        # 1. Load from seen-urls.json
        if os.path.exists(SEEN_FILE):
            try:
                with open(SEEN_FILE) as f:
                    data = json.load(f)
                self._urls = set(data.get("urls", []))
                self._norm_urls = set(data.get("norm_urls", []))
                # Restore title word sets from stored fingerprints
                stored_words = data.get("title_words", [])
                self._title_words = [set(w) for w in stored_words if w]
                self._titles = data.get("titles", [])
            except (json.JSONDecodeError, OSError):
                pass

        # 2. Scan vault for existing articles — add URLs only, NOT title
        #    fingerprints. The vault holds 1900+ notes; adding every title's
        #    word-set here floods _title_words (50k+) and makes the 70% word-
        #    overlap matcher false-match almost every fresh item → zero capture
        #    on a 30+ feed corpus. URLs alone still dedupe exact duplicates.
        vault_urls, vault_title_words = _scan_vault_for_urls()
        if vault_urls:
            before_urls = len(self._urls)
            self._urls.update(vault_urls)
            print(f"  📖 Vault scan: {len(vault_urls)} articles, "
                  f"{len(self._urls) - before_urls} new URLs "
                  f"(title fingerprints NOT imported)")

        self._loaded = True

    def is_new(self, url, date_str="", title=""):
        """Check if an article is new using multi-layered dedup.
        
        Args:
            url: The article URL
            date_str: Publication date string
            title: Article title
            
        Returns:
            True if the article is new and unique
        """
        # Layer 1: URL exact match
        if url in self._urls:
            return False

        # Layer 2: URL normalized match
        norm_url = _normalize_url(url)
        if norm_url and norm_url in self._norm_urls:
            return False

        # Layer 3: Title word overlap match
        if title:
            new_words = _title_fingerprint(title)
            if new_words:
                for seen_words in self._title_words:
                    overlap = _word_overlap(new_words, seen_words)
                    if overlap >= WORD_OVERLAP_THRESHOLD:
                        return False

            # Layer 4: Title fuzzy match against all seen titles
            norm_title = _normalize_title(title)
            if norm_title:
                for seen_title in self._titles:
                    similarity = SequenceMatcher(None, norm_title, seen_title).ratio()
                    if similarity >= TITLE_SIMILARITY_THRESHOLD:
                        return False

        # Layer 5: Date freshness
        if date_str:
            pub_date = _parse_date(date_str)
            if pub_date:
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
                if pub_date < self._cutoff_date:
                    return False

        return True

    def mark(self, url, title=""):
        """Mark an article as seen across all layers."""
        self._urls.add(url)
        
        norm_url = _normalize_url(url)
        if norm_url:
            self._norm_urls.add(norm_url)
        
        if title:
            words = _title_fingerprint(title)
            if words and words not in self._title_words:
                self._title_words.append(words)
            norm_title = _normalize_title(title)
            if norm_title and norm_title not in self._titles:
                self._titles.append(norm_title)
        
        self._new_urls.add(url)

    def save(self):
        """Save all seen data to disk."""
        os.makedirs(DATA_DIR, exist_ok=True)
        # Cap the title-fingerprint store. It grew unbounded to 350k+ entries
        # (58MB) because every vault note's title was added each run, which made
        # the 70% word-overlap matcher false-match nearly every fresh feed item
        # → zero captures on healthy corpora. Keep a bounded recent window.
        MAX_TITLE_WORDS = 20000
        MAX_TITLES = 10000
        if len(self._title_words) > MAX_TITLE_WORDS:
            self._title_words = self._title_words[-MAX_TITLE_WORDS:]
        if len(self._titles) > MAX_TITLES:
            self._titles = self._titles[-MAX_TITLES:]
        # Convert word sets to lists for JSON serialization
        title_words_list = [list(w) for w in self._title_words]
        data = {
            "updated_at": datetime.now().isoformat(),
            "total_urls": len(self._urls),
            "total_title_words": len(self._title_words),
            "total_titles": len(self._titles),
            "urls": sorted(self._urls),
            "norm_urls": sorted(self._norm_urls),
            "title_words": title_words_list,
            "titles": self._titles,
        }
        with open(SEEN_FILE, "w") as f:
            json.dump(data, f, indent=2)

    @property
    def total(self):
        return len(self._urls)

    @property
    def new_count(self):
        return len(self._new_urls)
