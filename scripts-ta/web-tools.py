#!/usr/bin/env python3
"""
WEB TOOLS — Search, extract, and crawl web content for Hermes Agent
Routes through DuckDuckGo (search) and requests/BeautifulSoup (extract)

Usage:
    python3 web_tools.py search "Bursa Malaysia MUI annual report 2024"
    python3 web_tools.py extract "https://example.com/report.pdf"
    python3 web_tools.py crawl "https://example.com" --depth 2
"""

import argparse
import json
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from ddgs import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False
    print("⚠️  ddgs not installed. Run: pip3 install ddgs")

# ── Configuration ───────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ── Search ─────────────────────────────────────────────────────────────

def web_search(query: str, limit: int = 10) -> list[dict]:
    """Search the web using DuckDuckGo."""
    if not DDGS_AVAILABLE:
        return [{"error": "duckduckgo-search not installed"}]
    
    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=limit):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
    except Exception as e:
        results.append({"error": str(e)})
    
    return results

# ── Extract ────────────────────────────────────────────────────────────

def web_extract(url: str) -> dict:
    """Extract text content from a URL (HTML or PDF)."""
    result = {
        "url": url,
        "title": "",
        "content": "",
        "links": [],
        "error": None,
    }
    
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        resp = session.get(url, timeout=30, allow_redirects=True)
        
        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}"
            return result
        
        content_type = resp.headers.get("Content-Type", "").lower()
        
        if "pdf" in content_type or url.endswith(".pdf"):
            # Try to extract text from PDF
            result["content"] = _extract_pdf_text(resp.content)
            result["title"] = url.split("/")[-1]
        else:
            # HTML extraction
            soup = BeautifulSoup(resp.text, "html.parser")
            
            # Remove script/style/nav/footer elements
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            
            result["title"] = soup.title.string.strip() if soup.title else ""
            
            # Extract main content
            main = soup.find("main") or soup.find("article") or soup.find("div", class_="content")
            if main:
                result["content"] = _clean_text(main.get_text())
            else:
                result["content"] = _clean_text(soup.get_text())
            
            # Extract links
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if href.startswith("http"):
                    result["links"].append({
                        "url": href,
                        "text": link.get_text(strip=True)[:100],
                    })
    
    except Exception as e:
        result["error"] = str(e)
    
    return result


def _extract_pdf_text(content: bytes) -> str:
    """Extract text from PDF bytes (basic)."""
    try:
        # Try to find text in raw PDF bytes
        text = content.decode("latin-1", errors="ignore")
        # PDF text extraction heuristic
        lines = []
        for line in text.split("\n"):
            if re.match(r"^[A-Za-z0-9\s.,;:'\"()-]+$", line.strip()) and len(line.strip()) > 20:
                lines.append(line.strip())
        return "\n".join(lines[:500])  # Limit output
    except Exception:
        return "[PDF detected — text extraction requires PyPDF2 or pdfplumber]"


def _clean_text(text: str) -> str:
    """Clean extracted text."""
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if len(line) > 0:
            lines.append(line)
    # Join and deduplicate adjacent empty lines
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:50000]  # Limit to 50KB

# ── Crawl ──────────────────────────────────────────────────────────────

def web_crawl(start_url: str, depth: int = 1, max_pages: int = 10) -> list[dict]:
    """Crawl pages starting from a URL."""
    visited = set()
    to_visit = [(start_url, 0)]
    results = []
    
    while to_visit and len(results) < max_pages:
        url, current_depth = to_visit.pop(0)
        
        if url in visited or current_depth > depth:
            continue
        
        visited.add(url)
        print(f"  Crawling ({current_depth}/{depth}): {url[:80]}...", file=sys.stderr)
        
        page = web_extract(url)
        results.append(page)
        
        if current_depth < depth:
            for link in page.get("links", [])[:20]:
                next_url = link["url"]
                # Only follow same-domain links
                if urlparse(next_url).netloc == urlparse(start_url).netloc:
                    if next_url not in visited:
                        to_visit.append((next_url, current_depth + 1))
        
        time.sleep(1)  # Be polite
    
    return results

# ── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Web tools for Hermes Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Search
    search_parser = subparsers.add_parser("search", help="Search the web")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=10, help="Max results")
    
    # Extract
    extract_parser = subparsers.add_parser("extract", help="Extract content from URL")
    extract_parser.add_argument("url", help="URL to extract")
    
    # Crawl
    crawl_parser = subparsers.add_parser("crawl", help="Crawl pages from URL")
    crawl_parser.add_argument("url", help="Start URL")
    crawl_parser.add_argument("--depth", type=int, default=1, help="Crawl depth")
    crawl_parser.add_argument("--max-pages", type=int, default=10, help="Max pages")
    
    args = parser.parse_args()
    
    if args.command == "search":
        results = web_search(args.query, args.limit)
        print(json.dumps(results, indent=2))
    
    elif args.command == "extract":
        result = web_extract(args.url)
        print(json.dumps(result, indent=2))
    
    elif args.command == "crawl":
        results = web_crawl(args.url, args.depth, args.max_pages)
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
