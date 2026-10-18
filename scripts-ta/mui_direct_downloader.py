#!/usr/bin/env python3
"""
MUI Annual Report Downloader — Using Known Bursa IDs
Direct download using pre-discovered announcement IDs from Firecrawl session.
"""

import json
import os
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager

# ── Configuration ────────────────────────────────────────────────────────

VAULT = Path.home() / "Documents/Obsidian Vault/01-Investment Research"

COMPANIES = {
    "MUI": {
        "ticker": "3891",
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
        "ticker": "3913",
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
    """Setup Chrome with download support."""
    from selenium.webdriver.chrome.options import Options
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    
    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        })
    """)
    return driver

# ── Download Logic ──────────────────────────────────────────────────────

def download_annual_report(driver: webdriver.Chrome, company: str, year: str, ann_id: str) -> list[Path]:
    """Download annual report for given company/year."""
    print(f"\n{'─'*60}")
    print(f"🗓 {company} {year} (ann_id={ann_id})")
    print(f"{'─'*60}")
    
    # Open announcement page
    url = f"https://www.klsescreener.com/v2/announcements/view/{ann_id}"
    driver.get(url)
    time.sleep(5)
    
    # Parse for download links
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    downloads = []
    
    for link in soup.find_all('a', href=lambda x: x and 'download?id=' in x):
        href = link.get('href', '')
        text = link.get_text(strip=True)
        
        # Extract Bursa ID
        import re
        match = re.search(r'download\?id=(\d+)', href)
        if match:
            bursa_id = match.group(1)
            full_url = href if href.startswith('http') else f"https://disclosure.bursamalaysia.com{href}"
            downloads.append({"bursa_id": bursa_id, "url": full_url, "text": text[:30]})
    
    if not downloads:
        print(f"  ❌ No download links found")
        return []
    
    print(f"  📎 Found {len(downloads)} download(s)")
    
    # Download each
    saved_files = []
    for i, dl in enumerate(downloads):
        part = f"_Part{i+1:02d}" if len(downloads) > 1 else ""
        filename = f"{company}_AR{year}{part}.pdf"
        
        # Vault target
        vault_dir = VAULT / f"{company}-Group" / "Annual-Reports" / year
        vault_dir.mkdir(parents=True, exist_ok=True)
        output_path = vault_dir / filename
        
        if output_path.exists():
            print(f"  ⏭ {filename} already exists")
            saved_files.append(output_path)
            continue
        
        # Download using requests with browser cookies
        cookies = {c['name']: c['value'] for c in driver.get_cookies()}
        headers = {
            "User-Agent": driver.execute_script("return navigator.userAgent;"),
            "Accept": "application/pdf,*/*;q=0.8",
            "Referer": "https://www.klsescreener.com/",
        }
        
        try:
            import requests
            resp = requests.get(dl["url"], headers=headers, cookies=cookies, allow_redirects=True, timeout=120)
            
            if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                output_path.write_bytes(resp.content)
                print(f"  ✅ Saved: {filename} ({len(resp.content)/1024:.0f} KB)")
                saved_files.append(output_path)
            else:
                print(f"  ❌ Download failed: HTTP {resp.status_code}")
        except Exception as e:
            print(f"  ❌ Error: {str(e)[:80]}")
        
        time.sleep(3)
    
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
            
            files = download_annual_report(driver, comp["short"], year, ann_id)
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
