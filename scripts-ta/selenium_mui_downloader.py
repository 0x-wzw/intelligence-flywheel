#!/usr/bin/env python3
"""
MUI GROUP ANNUAL REPORT DOWNLOADER — Selenium Edition
Automated browser-based extraction of annual reports from KLSE Screener → Bursa Malaysia.
Handles Cloudflare challenges, downloads PDFs, and saves to Obsidian Vault.

Requires:
    pip3 install selenium webdriver-manager requests beautifulsoup4
    brew install --cask google-chrome  (macOS)

Usage:
    python3 selenium_mui_downloader.py --company MUI --years 2020,2021,2022,2023,2024
    python3 selenium_mui_downloader.py --company MUIPROP --years 2020-2024
    python3 selenium_mui_downloader.py --company MUI --years all --headless
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.safari.options import Options as SafariOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ── Configuration ───────────────────────────────────────────────────────

# Companies
COMPANIES = {
    "MUI": {
        "ticker": "3891",
        "short": "MUI",
        "name": "Malayan United Industries Berhad",
        "ann_list_url": "https://www.klsescreener.com/v2/announcements/stock/3891?category=AR",
    },
    "MUIPROP": {
        "ticker": "3913",
        "short": "MUIPROP",
        "name": "MUI Properties Berhad",
        "ann_list_url": "https://www.klsescreener.com/v2/announcements/stock/3913?category=AR",
    },
    "PMCORP": {
        "ticker": "PMCORP",
        "short": "PMCORP",
        "name": "Pan Malaysia Corporation Berhad",
        "ann_list_url": "https://www.klsescreener.com/v2/announcements/stock/PMCORP?category=AR",
    },
}

# Vault path
VAULT_PATH = Path.home() / "Documents" / "Obsidian Vault" / "01-Investment Research"

# ── Chrome Setup ─────────────────────────────────────────────────────────

def setup_driver(browser: str = "auto", headless: bool = False, download_dir: str = None) -> webdriver.Remote:
    """Setup browser driver - tries Chrome first, falls back to Safari."""
    
    # Try Chrome first
    if browser in ("auto", "chrome"):
        options = ChromeOptions()
        
        if headless:
            options.add_argument("--headless=new")
        
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        
        # Set download directory
        if download_dir:
            os.makedirs(download_dir, exist_ok=True)
            prefs = {
                "download.default_directory": os.path.abspath(download_dir),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "plugins.always_open_pdf_externally": True,
            }
            options.add_experimental_option("prefs", prefs)
        
        options.add_argument(
            "--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        try:
            service = ChromeService(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            print("✅ Using Chrome browser")
        except Exception as e:
            if browser == "chrome":
                print(f"❌ Chrome setup failed: {e}")
                sys.exit(1)
            else:
                print(f"⚠️  Chrome not available, trying Safari...")
                driver = None
        else:
            # Remove webdriver detection
            driver.execute_script("""
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """)
            return driver
    
    # Fallback to Safari (macOS built-in)
    if browser in ("auto", "safari"):
        try:
            options = SafariOptions()
            driver = webdriver.Safari(options=options)
            print("✅ Using Safari browser")
            return driver
        except Exception as e:
            print(f"❌ Safari setup failed: {e}")
    
    print("❌ No browser available!")
    print("\nTo install Chrome on macOS:")
    print("  brew install --cask google-chrome")
    print("\nOr enable Safari automation:")
    print("  Open Safari → Preferences → Advanced → Enable 'Show Develop menu'")
    print("  Then Develop → Allow Remote Automation")
    sys.exit(1)

# ── KLSE Screener Scraping ───────────────────────────────────────────────

def get_announcement_ids(driver: webdriver.Chrome, ann_list_url: str, target_years: list[str]) -> dict:
    """Scrape KLSE Screener announcement list to find annual report IDs by year."""
    print(f"📖 Loading announcement list: {ann_list_url}")
    driver.get(ann_list_url)
    
    # Wait for content to load
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except:
        print("⚠️  Page load timeout, continuing anyway...")
    
    # Scroll to bottom to load all announcements
    last_height = driver.execute_script("return document.body.scrollHeight")
    for _ in range(10):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    
    time.sleep(3)  # Extra wait for dynamic content
    
    # Extract from page HTML
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    announcements = {}
    
    # KLSE Screener announcement links look like: /v2/announcements/view/XXXXXX
    links = soup.find_all('a', href=re.compile(r'/v2/announcements/view/(\d+)'))
    
    for link in links:
        href = link.get('href', '')
        ann_id_match = re.search(r'/v2/announcements/view/(\d+)', href)
        if not ann_id_match:
            continue
        
        ann_id = ann_id_match.group(1)
        
        # Find nearby text for year
        parent = link.find_parent(['tr', 'div', 'li'])
        if parent:
            text = parent.get_text()
        else:
            text = link.get_text()
        
        # Extract year from text - KLSE Screener format: "31Oct...Annual Report"
        # Try multiple patterns
        year = None
        
        # Pattern 1: Look for year in text near the link
        year_match = re.search(r'(20\d{2}|19\d{2})', text)
        if year_match:
            year = year_match.group(1)
        
        # Pattern 2: Check URL or link text
        if not year:
            url_match = re.search(r'(20\d{2})', href)
            if url_match:
                year = url_match.group(1)
        
        # Pattern 3: Check for date format like "31Oct" followed by year
        if not year:
            date_match = re.search(r'\d{1,2}[A-Za-z]{3,}(20\d{2})', text.replace(" ", ""))
            if date_match:
                year = date_match.group(1)
        
        if year and year in target_years and year not in announcements:
            announcements[year] = {
                "ann_id": ann_id,
                "text": text.strip()[:100],
            }
            print(f"  ✅ Found {year} → ann_id={ann_id}")
    
    return announcements

# ── Bursa Download ID Extraction ─────────────────────────────────────────

def get_bursa_download_ids(driver: webdriver.Chrome, ann_id: str) -> list[dict]:
    """Scrape KLSE Screener announcement page to get Bursa download IDs."""
    url = f"https://www.klsescreener.com/v2/announcements/view/{ann_id}"
    print(f"  🌐 Opening announcement page: {url}")
    driver.get(url)
    
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except:
        print("  ⚠️  Page timeout, continuing...")
    
    time.sleep(3)
    
    html = driver.page_source
    soup = BeautifulSoup(html, 'html.parser')
    
    downloads = []
    
    # Find all links containing 'download?id='
    for link in soup.find_all('a', href=re.compile(r'download\?id=(\d+)')):
        href = link.get('href', '')
        text = link.get_text(strip=True)
        
        id_match = re.search(r'download\?id=(\d+)', href)
        if id_match:
            bursa_id = id_match.group(1)
            # Full URL
            if href.startswith('http'):
                full_url = href
            else:
                full_url = f"https://disclosure.bursamalaysia.com{href}"
            
            downloads.append({
                "bursa_id": bursa_id,
                "url": full_url,
                "text": text[:50],
            })
    
    print(f"  📎 Found {len(downloads)} download links")
    return downloads

# ── PDF Download ─────────────────────────────────────────────────────────

def download_pdf_via_browser(driver: webdriver.Chrome, url: str, output_path: Path, timeout: int = 120) -> bool:
    """Download PDF by opening URL in browser (handles Cloudflare)."""
    print(f"  📥 Downloading: {url[:80]}...")
    
    try:
        driver.get(url)
        
        # Wait for page to settle
        time.sleep(10)
        
        # Check if we got a PDF or an HTML challenge page
        current_url = driver.current_url
        if "cloudflare" in current_url.lower() or "challenge" in driver.page_source.lower():
            print("  ⚠️  Cloudflare challenge detected, waiting...")
            time.sleep(20)
        
        # The browser should have downloaded the PDF automatically
        # We need to check the download directory
        # For now, we'll use requests with the browser's cookies
        
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        
        headers = {
            "User-Agent": driver.execute_script("return navigator.userAgent;"),
            "Accept": "application/pdf,*/*;q=0.8",
        }
        
        resp = requests.get(url, headers=headers, cookies=cookies, allow_redirects=True, timeout=timeout)
        
        if resp.status_code == 200:
            content_type = resp.headers.get("Content-Type", "")
            
            # Check if it's actually a PDF
            if resp.content[:4] == b"%PDF":
                output_path.write_bytes(resp.content)
                print(f"  ✅ Saved: {output_path.name} ({len(resp.content) / 1024:.0f} KB)")
                return True
            else:
                print(f"  ⚠️  Response is not PDF (Content-Type: {content_type[:50]})")
                # Save anyway for debugging
                debug_path = output_path.with_suffix('.html')
                debug_path.write_bytes(resp.content)
                print(f"  📝 Saved debug HTML to: {debug_path}")
                return False
        else:
            print(f"  ❌ HTTP {resp.status_code}")
            return False
            
    except Exception as e:
        print(f"  ❌ Error: {str(e)[:80]}")
        return False

# ── Main Download Logic ──────────────────────────────────────────────────

def download_company_reports(company_key: str, years: list[str], headless: bool = True) -> dict:
    """Download annual reports for a company."""
    company = COMPANIES[company_key]
    download_dir = str(tempfile.mkdtemp())
    
    results = {"success": [], "failed": []}
    
    print(f"\n{'='*70}")
    print(f"🏢 {company['name']} ({company_key})")
    print(f"{'='*70}")
    
    driver = setup_driver(browser="auto", headless=headless, download_dir=download_dir)
    
    try:
        # Step 1: Get announcement IDs from KLSE Screener
        print(f"\n📋 Step 1: Finding announcements for years: {', '.join(years)}")
        announcements = get_announcement_ids(driver, company["ann_list_url"], years)
        
        if not announcements:
            print("❌ No announcements found!")
            return results
        
        print(f"\n📋 Found {len(announcements)} year(s) to download")
        
        # Step 2: For each year, get Bursa IDs and download
        for year in sorted(years, reverse=True):
            if year not in announcements:
                print(f"\n⚠️  Year {year} not found in announcements")
                results["failed"].append(f"{company_key}_{year}")
                continue
            
            ann = announcements[year]
            print(f"\n{'─'*70}")
            print(f"🗓 Year {year} (ann_id={ann['ann_id']})")
            print(f"{'─'*70}")
            
            # Get Bursa download IDs
            downloads = get_bursa_download_ids(driver, ann["ann_id"])
            
            if not downloads:
                print(f"  ❌ No download links found for {year}")
                results["failed"].append(f"{company_key}_{year}")
                continue
            
            # Download each part
            for i, dl in enumerate(downloads):
                part = f"_Part{i+1:02d}" if len(downloads) > 1 else ""
                filename = f"{company['short']}_AR{year}{part}.pdf"
                
                # Save to vault
                vault_dir = VAULT_PATH / f"{company_key}-Group" / "Annual-Reports" / year
                vault_dir.mkdir(parents=True, exist_ok=True)
                output_path = vault_dir / filename
                
                if output_path.exists():
                    print(f"  ⏭ {filename} already exists, skipping")
                    results["success"].append(str(output_path))
                    continue
                
                # Download
                success = download_pdf_via_browser(driver, dl["url"], output_path)
                
                if success:
                    results["success"].append(str(output_path))
                else:
                    results["failed"].append(f"{company_key}_{year}_part{i+1}")
                
                time.sleep(5)  # Rate limiting
    
    finally:
        driver.quit()
        # Cleanup temp directory
        shutil.rmtree(download_dir, ignore_errors=True)
    
    return results

# ── CLI ──────────────────────────────────────────────────────────────────

def parse_years(years_str: str) -> list[str]:
    """Parse year string like '2020,2021,2022' or '2020-2024' or 'all'."""
    if years_str.lower() == "all":
        return [str(y) for y in range(2000, 2026)]
    
    years = []
    for part in years_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-")
            years.extend([str(y) for y in range(int(start), int(end) + 1)])
        else:
            years.append(part)
    
    return sorted(set(years), reverse=True)

def main():
    parser = argparse.ArgumentParser(description="MUI Group Annual Report Downloader (Selenium)")
    parser.add_argument("--company", choices=["MUI", "MUIPROP", "PMCORP"], required=True,
                        help="Company to download")
    parser.add_argument("--years", default="2020-2024",
                        help="Years to download (e.g., '2020,2021,2022' or '2020-2024' or 'all')")
    parser.add_argument("--headless", action="store_true", default=False,
                        help="Run Chrome in headless mode (may fail Cloudflare)")
    parser.add_argument("--gui", action="store_true", default=True,
                        help="Show Chrome window (default, helps pass Cloudflare)")
    
    args = parser.parse_args()
    
    # GUI mode by default for Cloudflare
    headless = args.headless
    if args.gui and not args.headless:
        headless = False
    
    years = parse_years(args.years)
    print(f"📅 Target years: {', '.join(years[:10])}{'...' if len(years) > 10 else ''}")
    print(f"👁  Headless mode: {headless}")
    print(f"📁 Output: {VAULT_PATH}")
    
    results = download_company_reports(args.company, years, headless=headless)
    
    # Summary
    print(f"\n{'='*70}")
    print("📊 DOWNLOAD SUMMARY")
    print(f"{'='*70}")
    print(f"✅ Success: {len(results['success'])}")
    for f in results['success']:
        print(f"   {Path(f).name}")
    print(f"\n❌ Failed: {len(results['failed'])}")
    for f in results['failed']:
        print(f"   {f}")

if __name__ == "__main__":
    main()
