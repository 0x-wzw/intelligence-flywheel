#!/usr/bin/env python3
"""
MUI Batch Annual Report Downloader
Uses Chrome to download all missing years.
Run with visible Chrome window to handle Cloudflare.
"""

import glob
import os
import shutil
import time
from pathlib import Path
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# ── Config ──────────────────────────────────────────

VAULT = Path.home() / "Documents/Obsidian Vault/01-Investment Research"
DL_DIR = Path.home() / "Downloads/MUI_AR"
DL_DIR.mkdir(exist_ok=True)

# MUI: ann_id → {year: bursa_ids}
# Use known ann_ids from Firecrawl session
MUI_YEARS = {
    "2020": {"ann_id": "3253519", "parts": 2},
    "2021": {"ann_id": "3448703", "parts": 2},
    "2022": {"ann_id": "3630655", "parts": 2},  # Already have Part02
}

MUIPROP_YEARS = {
    "2020": {"ann_id": "3253539", "parts": 1},
    "2021": {"ann_id": "3448715", "parts": 1},
    "2022": {"ann_id": "3630669", "parts": 1},
    "2023": {"ann_id": "5873573", "parts": 1},
}

# ── Setup ───────────────────────────────────────────

def setup_chrome():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "download.default_directory": str(DL_DIR),
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
    driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return driver

def wait_for_files(timeout=90):
    """Wait for new PDFs to appear in download dir."""
    start = time.time()
    while time.time() - start < timeout:
        files = list(DL_DIR.glob("*.pdf"))
        if files:
            return files
        time.sleep(1)
    return []

# ── Download ────────────────────────────────────────

def download_year(driver, company, short, year, ann_id):
    """Download all parts for a given year."""
    print(f"\n{'='*60}")
    print(f"📅 {company} {year}")
    print(f"{'='*60}")
    
    # Clear old downloads
    for f in DL_DIR.glob("*"):
        f.unlink()
    
    # Open announcement page
    url = f"https://www.klsescreener.com/v2/announcements/view/{ann_id}"
    print(f"🌐 {url}")
    driver.get(url)
    time.sleep(10)
    
    # Extract Bursa download links from page
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    
    import re
    bursa_links = []
    for link in soup.find_all('a', href=re.compile(r'download\?id=\d+')):
        href = link.get('href', '')
        match = re.search(r'download\?id=(\d+)', href)
        if match:
            bursa_id = match.group(1)
            full_url = href if href.startswith('http') else f"https://disclosure.bursamalaysia.com{href}"
            bursa_links.append(full_url)
    
    if not bursa_links:
        print("❌ No download links found")
        return []
    
    print(f"📎 Found {len(bursa_links)} download link(s)")
    
    saved = []
    for i, dl_url in enumerate(bursa_links):
        part = f"_Part{i+1:02d}" if len(bursa_links) > 1 else ""
        filename = f"{short}_AR{year}{part}.pdf"
        
        vault_dir = VAULT / f"{company}-Group" / "Annual-Reports" / year
        vault_dir.mkdir(parents=True, exist_ok=True)
        target = vault_dir / filename
        
        if target.exists():
            print(f"⏭ {filename} already exists")
            saved.append(target)
            continue
        
        # Trigger download via browser
        print(f"📥 Downloading part {i+1}...")
        driver.get(dl_url)
        time.sleep(20)  # Wait for Cloudflare + download
        
        # Wait for files
        files = wait_for_files(timeout=60)
        if files:
            # Move first PDF found
            src = files[0]
            shutil.move(str(src), str(target))
            print(f"✅ Saved: {filename} ({target.stat().st_size/1024:.0f} KB)")
            saved.append(target)
            
            # Clear for next part
            for f in DL_DIR.glob("*"):
                f.unlink()
        else:
            print(f"❌ Download failed for part {i+1}")
        
        time.sleep(5)
    
    return saved

# ── Main ────────────────────────────────────────────

def main():
    driver = setup_chrome()
    results = {"success": [], "failed": []}
    
    try:
        # Download MUI missing years
        for year, info in MUI_YEARS.items():
            files = download_year(driver, "MUI", "MUI", year, info["ann_id"])
            if files:
                results["success"].extend([f.name for f in files])
            else:
                results["failed"].append(f"MUI_{year}")
        
        # Download MUIPROP all years
        for year, info in MUIPROP_YEARS.items():
            files = download_year(driver, "MUIPROP", "MUIPROP", year, info["ann_id"])
            if files:
                results["success"].extend([f.name for f in files])
            else:
                results["failed"].append(f"MUIPROP_{year}")
    
    finally:
        driver.quit()
    
    # Summary
    print(f"\n{'='*60}")
    print("📊 BATCH DOWNLOAD SUMMARY")
    print(f"{'='*60}")
    print(f"✅ Success: {len(results['success'])}")
    for f in results["success"]:
        print(f"   {f}")
    print(f"\n❌ Failed: {len(results['failed'])}")
    for f in results["failed"]:
        print(f"   {f}")

if __name__ == "__main__":
    main()
