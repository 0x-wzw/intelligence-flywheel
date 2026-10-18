#!/usr/bin/env python3
"""
MUI Annual Report Downloader — Browser-based PDF download
Uses Chrome to handle Cloudflare challenges natively.
"""

import glob
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

from typing import Optional, List, Dict, Any

# ── Configuration ────────────────────────────────────────────────────────

VAULT = Path.home() / "Documents/Obsidian Vault/01-Investment Research"
DOWNLOAD_DIR = Path.home() / "Downloads" / "MUI_AR"

COMPANIES = {
    "MUI": {
        "short": "MUI",
        "name": "Malayan United Industries Berhad",
        "ann_ids": {
            "2025": "10592513",
            "2024": "8321285",
            "2023": "5873551",
            "2022": "3630655",
            "2021": "3448703",
            "2020": "3253519",
        }
    },
    "MUIPROP": {
        "short": "MUIPROP",
        "name": "MUI Properties Berhad",
        "ann_ids": {
            "2025": "10592525",
            "2024": "8320641",
            "2023": "5873573",
            "2022": "3630669",
            "2021": "3448715",
            "2020": "3253539",
        }
    },
}

# ── Chrome Setup ─────────────────────────────────────────────────────────

def setup_chrome():
    """Setup Chrome with PDF auto-download."""
    options = Options()
    # GUI mode for Cloudflare handling
    # options.add_argument("--headless")  # DO NOT enable - Cloudflare needs visible browser
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    # PDF download prefs
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    prefs = {
        "download.default_directory": str(DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
    }
    options.add_experimental_option("prefs", prefs)
    
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        })
    """)
    return driver

# ── Download Logic ──────────────────────────────────────────────────────

def wait_for_download(download_dir: Path, timeout: int = 60) -> Optional[Path]:
    """Wait for a new PDF to appear in download directory."""
    start_time = time.time()
    existing = set(download_dir.glob("*.pdf"))
    
    while time.time() - start_time < timeout:
        current = set(download_dir.glob("*.pdf"))
        new_files = current - existing
        if new_files:
            return list(new_files)[0]
        
        # Check for .crdownload (partial download)
        partial = list(download_dir.glob("*.crdownload"))
        if partial:
            time.sleep(2)
            continue
        
        time.sleep(1)
    
    return None

def download_via_browser(driver: webdriver.Chrome, company: str, year: str, ann_id: str) -> list[Path]:
    """Download annual report by opening Bursa URL in Chrome."""
    print(f"\n{'─'*60}")
    print(f"🗓 {company} {year} (ann_id={ann_id})")
    print(f"{'─'*60}")
    
    comp = COMPANIES[company]
    
    # Step 1: Open KLSE Screener page
    klse_url = f"https://www.klsescreener.com/v2/announcements/view/{ann_id}"
    print(f"  🌐 Opening KLSE Screener: {klse_url}")
    driver.get(klse_url)
    time.sleep(8)
    
    # Step 2: Get page source and find Bursa links
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    bursa_links = []
    for link in soup.find_all('a', href=lambda x: x and 'download?id=' in x):
        href = link.get('href', '')
        match = re.search(r'download\?id=(\d+)', href)
        if match:
            bursa_id = match.group(1)
            full_url = href if href.startswith('http') else f"https://disclosure.bursamalaysia.com{href}"
            bursa_links.append({"id": bursa_id, "url": full_url})
    
    if not bursa_links:
        print(f"  ❌ No Bursa download links found")
        return []
    
    print(f"  📎 Found {len(bursa_links)} Bursa link(s)")
    
    # Step 3: Clear old downloads
    for f in DOWNLOAD_DIR.glob("*"):
        f.unlink()
    
    # Step 4: Download each PDF via browser
    saved_files = []
    
    for i, dl in enumerate(bursa_links):
        part = f"_Part{i+1:02d}" if len(bursa_links) > 1 else ""
        filename = f"{comp['short']}_AR{year}{part}.pdf"
        
        vault_dir = VAULT / f"{company}-Group" / "Annual-Reports" / year
        vault_dir.mkdir(parents=True, exist_ok=True)
        vault_file = vault_dir / filename
        
        if vault_file.exists():
            print(f"  ⏭ {filename} already in vault")
            saved_files.append(vault_file)
            continue
        
        # Open Bursa URL in browser (triggers Cloudflare challenge + download)
        print(f"  📥 Opening: {dl['url'][:80]}...")
        driver.get(dl["url"])
        
        # Wait for Cloudflare challenge
        time.sleep(15)
        
        # Check if we hit a challenge page
        if "cloudflare" in driver.current_url.lower() or "challenge" in driver.page_source.lower():
            print(f"  ⚠️  Cloudflare challenge detected, waiting longer...")
            time.sleep(20)
        
        # Wait for download
        print(f"  ⏳ Waiting for download...")
        downloaded = wait_for_download(DOWNLOAD_DIR, timeout=60)
        
        if downloaded and downloaded.stat().st_size > 1000:
            # Move to vault
            shutil.move(str(downloaded), str(vault_file))
            print(f"  ✅ Saved: {filename} ({vault_file.stat().st_size/1024:.0f} KB)")
            saved_files.append(vault_file)
        else:
            print(f"  ❌ Download didn't complete or file too small")
        
        time.sleep(5)
    
    return saved_files

# ── Main ─────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", choices=["MUI", "MUIPROP"], required=True)
    parser.add_argument("--years", default="2020-2024", help="Years to download")
    args = parser.parse_args()
    
    # Parse years
    years = []
    for part in args.years.split(","):
        if "-" in part:
            start, end = part.split("-")
            years.extend(str(y) for y in range(int(start), int(end)+1))
        else:
            years.append(part.strip())
    years = sorted(set(years), reverse=True)
    
    comp = COMPANIES[args.company]
    print(f"\n{'='*60}")
    print(f"🏢 {comp['name']} ({args.company})")
    print(f"   Years: {', '.join(years)}")
    print(f"{'='*60}")
    
    driver = setup_chrome()
    results = {"success": [], "failed": []}
    
    try:
        for year in years:
            ann_id = comp["ann_ids"].get(year)
            if not ann_id:
                print(f"\n⚠️ No ann_id known for {year}")
                results["failed"].append(f"{args.company}_{year}")
                continue
            
            files = download_via_browser(driver, args.company, year, ann_id)
            if files:
                results["success"].extend([f.name for f in files])
            else:
                results["failed"].append(f"{args.company}_{year}")
    finally:
        driver.quit()
    
    # Summary
    print(f"\n{'='*60}")
    print("📊 DOWNLOAD SUMMARY")
    print(f"{'='*60}")
    print(f"✅ Success: {len(results['success'])}")
    for f in results["success"]:
        print(f"   {f}")
    print(f"\n❌ Failed: {len(results['failed'])}")
    for f in results["failed"]:
        print(f"   {f}")

if __name__ == "__main__":
    main()
