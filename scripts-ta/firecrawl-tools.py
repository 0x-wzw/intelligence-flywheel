#!/usr/bin/env python3
"""
FIRECRAWL WEB TOOLS — Search, extract, and crawl web content via Firecrawl API
Routes through Firecrawl backend which handles Cloudflare/JavaScript challenges.

Usage:
    python3 firecrawl-tools.py scrape "https://example.com"
    python3 firecrawl-tools.py scrape "https://example.com/pdf" --format markdown
    python3 firecrawl-tools.py batch urls.json

Environment:
    FIRECRAWL_API_KEY — API key for Firecrawl (set in ~/.hermes/.env)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

# ── Configuration ───────────────────────────────────────────────────────

API_KEY = os.environ.get("FIRECRAWL_API_KEY", "")
if not API_KEY:
    # Try to load from .env
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                if line.startswith("FIRECRAWL_API_KEY="):
                    API_KEY = line.split("=", 1)[1].strip().strip('"')
                    break

BASE_URL = "https://api.firecrawl.dev/v1"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
}

# ── Core Functions ───────────────────────────────────────────────────────

def scrape_url(url: str, page_options: dict = None, timeout: int = 60) -> dict:
    """Scrape a single URL via Firecrawl."""
    if not API_KEY:
        return {"error": "FIRECRAWL_API_KEY not set"}
    
    payload = {"url": url}
    if page_options:
        payload["pageOptions"] = page_options
    
    try:
        resp = requests.post(
            f"{BASE_URL}/scrape",
            headers=HEADERS,
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
    except Exception as e:
        return {"error": str(e)}


def batch_scrape(urls: list[str], timeout: int = 120) -> list[dict]:
    """Scrape multiple URLs in batch."""
    results = []
    for url in urls:
        print(f"Scraping: {url[:80]}...", file=sys.stderr)
        result = scrape_url(url, timeout=timeout)
        results.append(result)
        time.sleep(1)
    return results


def extract_pdf_links(url: str) -> list[dict]:
    """Extract PDF download links from a page."""
    result = scrape_url(url)
    if result.get("success"):
        links = result.get("data", {}).get("linksOnPage", [])
        pdf_links = []
        for link in links:
            if link.endswith(".pdf") or "download" in link.lower() or "bursa" in link.lower() or "attachment" in link.lower():
                pdf_links.append({
                    "url": link,
                    "text": "PDF download",
                })
        return pdf_links
    return []


def download_pdf(url: str, output_path: Path, timeout: int = 120) -> bool:
    """Download a PDF file directly."""
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })
        if resp.status_code == 200 and resp.content[:4] == b"%PDF":
            output_path.write_bytes(resp.content)
            return True
        return False
    except Exception:
        return False


# ── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Firecrawl web tools")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    scrape_parser = subparsers.add_parser("scrape", help="Scrape a URL")
    scrape_parser.add_argument("url", help="URL to scrape")
    scrape_parser.add_argument("--format", choices=["markdown", "html", "text"], default="markdown")
    
    extract_parser = subparsers.add_parser("extract", help="Extract PDF links from a page")
    extract_parser.add_argument("url", help="URL to extract from")
    
    download_parser = subparsers.add_parser("download", help="Download a PDF")
    download_parser.add_argument("url", help="PDF URL")
    download_parser.add_argument("--output", "-o", required=True, help="Output path")
    
    batch_parser = subparsers.add_parser("batch", help="Batch scrape URLs from JSON file")
    batch_parser.add_argument("file", help="JSON file with list of URLs")
    
    args = parser.parse_args()
    
    if args.command == "scrape":
        result = scrape_url(args.url, page_options={"onlyMainContent": True})
        print(json.dumps(result, indent=2))
    
    elif args.command == "extract":
        links = extract_pdf_links(args.url)
        print(json.dumps(links, indent=2))
    
    elif args.command == "download":
        success = download_pdf(args.url, Path(args.output))
        print(json.dumps({"success": success, "path": str(args.output)}))
    
    elif args.command == "batch":
        with open(args.file) as f:
            urls = json.load(f)
        results = batch_scrape(urls)
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
