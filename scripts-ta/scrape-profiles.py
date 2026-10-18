#!/usr/bin/env python3
"""Scrape public profiles using Selenium + Chrome."""
import json
import os
import re
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

OUTPUT_FILE = os.path.expanduser("~/.hermes/data/profiles-scrape.json")

def setup_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,800")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument("--disable-blink-features=AutomationControlled")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=opts)
    return driver


def scrape_linkedin(driver):
    """Try to scrape LinkedIn public profile."""
    url = "https://www.linkedin.com/in/zehant/"
    print(f"\n🔍 LinkedIn: {url}")
    driver.get(url)
    time.sleep(5)

    # Check if we hit a login wall or CAPTCHA
    page_text = driver.find_element(By.TAG_NAME, "body").text
    title = driver.title

    result = {
        "url": url,
        "title": title,
        "status": "unknown",
        "data": {},
        "raw_snippet": page_text[:2000],
    }

    if "Sign in" in page_text or "Join now" in page_text:
        result["status"] = "login_wall"
        print("  → Login wall (public profile not accessible without auth)")
    elif "captcha" in page_text.lower() or "verify" in page_text.lower():
        result["status"] = "captcha"
        print("  → CAPTCHA challenge")
    elif "zehant" in page_text.lower() or "Zehan" in page_text:
        result["status"] = "profile_found"
        print("  → Profile content found!")
        # Try to extract structured data
        try:
            # Look for the main profile section
            h1s = driver.find_elements(By.TAG_NAME, "h1")
            for h in h1s:
                result["data"]["headline"] = h.text
                print(f"    Headline: {h.text}")
        except:
            pass
        try:
            # Look for profile sections
            sections = driver.find_elements(By.CSS_SELECTOR, "section")
            for s in sections:
                cls = s.get_attribute("class") or ""
                if "experience" in cls.lower():
                    result["data"]["experience_section"] = s.text[:500]
                elif "education" in cls.lower():
                    result["data"]["education_section"] = s.text[:500]
        except:
            pass
    else:
        result["status"] = "no_profile_data"
        print(f"  → No profile data (title: {title[:80]})")

    return result


def scrape_crunchbase(driver):
    """Try to scrape Crunchbase person profile."""
    url = "https://www.crunchbase.com/person/zehan-teoh"
    print(f"\n🔍 Crunchbase: {url}")
    driver.get(url)
    time.sleep(8)

    page_text = driver.find_element(By.TAG_NAME, "body").text
    title = driver.title

    result = {
        "url": url,
        "title": title,
        "status": "unknown",
        "data": {},
        "raw_snippet": page_text[:2000],
    }

    if "Attention Required" in page_text or "Cloudflare" in page_text:
        result["status"] = "cloudflare_blocked"
        print("  → Blocked by Cloudflare")
    elif "captcha" in page_text.lower() or "verify" in page_text.lower():
        result["status"] = "captcha"
        print("  → CAPTCHA challenge")
    elif "Zehan" in page_text or "Teoh" in page_text:
        result["status"] = "profile_found"
        print("  → Profile content found!")
        # Extract structured data
        try:
            # Look for name, title, etc.
            for selector in ["h1", "h2", ".field-label", ".identifier"]:
                els = driver.find_elements(By.CSS_SELECTOR, selector)
                for el in els:
                    txt = el.text.strip()
                    if txt and len(txt) > 3:
                        result["data"][selector] = txt
                        print(f"    {selector}: {txt[:100]}")
        except:
            pass
    else:
        result["status"] = "no_profile_data"
        print(f"  → No profile data (title: {title[:80]})")

    return result


def main():
    print("=" * 60)
    print("Scraping Public Profiles for Zehan Teoh")
    print("=" * 60)

    driver = setup_driver()
    results = {}

    try:
        results["linkedin"] = scrape_linkedin(driver)
        results["crunchbase"] = scrape_crunchbase(driver)
    finally:
        driver.quit()

    # Save results
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\n✅ Results saved to {OUTPUT_FILE}")

    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for site, r in results.items():
        print(f"\n{site.upper()}:")
        print(f"  Status: {r['status']}")
        if r.get("data"):
            for k, v in r["data"].items():
                print(f"  {k}: {v[:100]}")
        print(f"  Title: {r.get('title', 'N/A')[:100]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
