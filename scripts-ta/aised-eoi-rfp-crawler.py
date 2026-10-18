#!/usr/bin/env python3
"""AISED / UNESCO EE Net — EOI & RFP Crawler

Daily scan of major NGOs, development banks, donor partners, and government
programmes for Expressions of Interest (EOIs) or Requests for Proposals (RFPs)
relevant to AISED / UNESCO EE Net.

Runs daily at 12:00 via cron. Output is delivered to Telegram.
"""
import json
import os
import re
import html
import sys
import urllib.parse
import urllib.request
from datetime import datetime

# Ensure Hermes scripts directory is on the path for dedup module
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ── Deduplication ──────────────────────────────────────────────────────
from dedup import SeenUrls

# ── Config ────────────────────────────────────────────────────────────
VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
HEALTH_LOG = os.path.join(VAULT_DIR, "System", "aised-eoi-rfp-health.json")

# Target organizations grouped by category
QUERIES = {
    "🏦 ASEAN Development Banks": [
        ("Asian Development Bank", "ADB Asian Development Bank tender EOI education ASEAN 2026"),
        ("ASEAN Infrastructure Fund", "ASEAN Infrastructure Fund procurement education 2026"),
        ("AIIB", "AIIB Asian Infrastructure Investment Bank ASEAN education 2026"),
        ("World Bank ASEAN", "World Bank procurement EOI RFP ASEAN education technology 2026"),
        ("Islamic Development Bank ASEAN", "IsDB Islamic Development Bank ASEAN education 2026"),
    ],
    "🌏 ASEAN Secretariat & Bodies": [
        ("ASEAN Secretariat", "ASEAN Secretariat EOI RFP tender education 2026"),
        ("ASEAN Education Ministers", "ASEAN SEAMEO education ministers meeting 2026"),
        ("ASEAN Foundation", "ASEAN Foundation grant EOI education 2026"),
        ("ASEAN Socio-Cultural Community", "ASEAN Socio-Cultural Community education programme 2026"),
        ("ASEAN Economic Community", "ASEAN Economic Community education skills development 2026"),
        ("SEAMEO", "SEAMEO Southeast Asian Ministers Education Organization tender EOI 2026"),
        ("AUN", "AUN ASEAN University Network programme grant 2026"),
    ],
    "🇲🇾 Malaysia — Government & Agencies": [
        ("MOE Malaysia", "Kementerian Pendidikan Malaysia tender procurement education technology 2026"),
        ("MOHE Malaysia", "Kementerian Pendidikan Tinggi Malaysia grant research education 2026"),
        ("MOSTI Malaysia", "MOSTI Malaysia science technology innovation grant 2026"),
        ("MDEC Malaysia", "MDEC Malaysia Digital Economy Corporation education 2026"),
        ("Malaysia Government", "Malaysia government tender education technology 2026"),
        ("Malaysia Ministry of Finance", "MOF Malaysia procurement education 2026"),
        ("Malaysia Ministry of Science", "Malaysia Ministry of Science Technology Innovation RFP 2026"),
        ("Cradle Fund Malaysia", "Cradle Fund Malaysia grant technology education 2026"),
        ("MIGHT Malaysia", "MIGHT Malaysia technology partnership education 2026"),
    ],
    "🇮🇩 Indonesia — Government & Agencies": [
        ("Indonesia Ministry Education", "Kemendikbud Indonesia tender pendidikan technology 2026"),
        ("Indonesia LPDP", "LPDP Indonesia scholarship research grant 2026"),
        ("Indonesia BRIN", "BRIN Indonesia research innovation grant education 2026"),
    ],
    "🇵🇭 Philippines — Government & Agencies": [
        ("Philippines DepEd", "DepEd Philippines Department Education tender procurement 2026"),
        ("Philippines CHED", "CHED Philippines higher education commission grant 2026"),
        ("Philippines DOST", "DOST Philippines science technology education grant 2026"),
    ],
    "🇹🇭 Thailand — Government & Agencies": [
        ("Thailand Ministry Education", "Thailand Ministry Education tender procurement technology 2026"),
        ("Thailand NRCT", "NRCT Thailand research grant education 2026"),
    ],
    "🇻🇳 Vietnam — Government & Agencies": [
        ("Vietnam Ministry Education", "Vietnam Ministry Education Training tender procurement 2026"),
        ("Vietnam MOST", "Vietnam Ministry Science Technology grant education 2026"),
        ("Vietnam MOET", "Vietnam MOET education technology RFP 2026"),
    ],
    "🇸🇬 Singapore — Government & Agencies": [
        ("Singapore MOE", "Singapore Ministry Education grant research 2026"),
        ("Singapore NRF", "Singapore National Research Foundation grant education 2026"),
        ("SkillsFuture Singapore", "SkillsFuture Singapore grant training education 2026"),
    ],
    "🤝 ASEAN Development Partners": [
        ("ASEAN-USAID", "ASEAN USAID partnership education technology 2026"),
        ("ASEAN-EU", "ASEAN European Union education programme 2026"),
        ("ASEAN-Korea", "ASEAN Korea Cooperation Fund education grant 2026"),
        ("ASEAN-Japan", "ASEAN Japan Cooperation Fund education technology 2026"),
        ("ASEAN-Australia", "ASEAN Australia education grant programme 2026"),
        ("ASEAN-India", "ASEAN India education cooperation fund 2026"),
        ("ASEAN-China", "ASEAN China education cooperation grant 2026"),
        ("ASEAN-UNDP", "ASEAN UNDP education programme 2026"),
        ("ASEAN-UNICEF", "ASEAN UNICEF education children 2026"),
        ("ASEAN-UNESCO", "ASEAN UNESCO education programme 2026"),
    ],
    "🏦 Development Banks (Global)": [
        ("World Bank", "World Bank procurement EOI RFP education technology 2026"),
        ("Islamic Development Bank", "IsDB Islamic Development Bank procurement EOI 2026"),
        ("African Development Bank", "AfDB African Development Bank tender education 2026"),
        ("European Investment Bank", "EIB European Investment Bank procurement education 2026"),
    ],
    "🇺🇳 UN Agencies": [
        ("UNESCO", "UNESCO EOI RFP education technology engineering 2026"),
        ("UNICEF", "UNICEF procurement tender education digital 2026"),
        ("UNDP", "UNDP EOI RFP education 2026"),
        ("UNHCR", "UNHCR tender education technology 2026"),
        ("UN Women", "UN Women procurement education grant 2026"),
        ("ILO", "ILO International Labour Organization tender education skills 2026"),
    ],
    "🤲 NGOs & Donors": [
        ("USAID", "USAID EOI RFP education technology 2026"),
        ("GIZ", "GIZ procurement tender education digital 2026"),
        ("FCDO", "FCDO DFID UK Aid tender education technology 2026"),
        ("Save the Children", "Save the Children EOI education 2026"),
        ("World Vision", "World Vision procurement education 2026"),
        ("Bill & Melinda Gates Foundation", "Gates Foundation RFP education 2026"),
        ("Open Society Foundations", "Open Society Foundations grant education 2026"),
        ("Mastercard Foundation", "Mastercard Foundation RFP education 2026"),
    ],
    "🏛️ Government Programmes (Global)": [
        ("Commonwealth", "Commonwealth education fund RFP 2026"),
        ("European Union", "EU European Union tender education Erasmus 2026"),
        ("Japan International Cooperation", "JICA Japan International Cooperation Agency education tender 2026"),
        ("KOICA", "KOICA Korea International Cooperation Agency education grant 2026"),
    ],
    "🎯 AISED / UNESCO EE Net": [
        ("AISED", "AISED UNESCO EE Net education engineering 2026"),
        ("UNESCO Engineering", "UNESCO engineering education network EOI 2026"),
        ("WFEO", "WFEO World Federation Engineering Organizations education 2026"),
    ],
}


def fetch_news(query, max_results=3):
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
            results.append({"title": clean_title, "url": link.strip(), "date": date.strip()})
        return results
    except Exception as e:
        return []


def build_report():
    """Fetch news for all target organizations and build a structured report."""
    seen = SeenUrls()
    seen.load()

    today = datetime.now().strftime("%Y-%m-%d")
    all_data = {}

    for category, orgs in QUERIES.items():
        cat_data = []
        for org_name, query in orgs:
            items = fetch_news(query)
            for item in items:
                if item["url"] and seen.is_new(item["url"], item.get("date", "")):
                    item["org"] = org_name
                    item["category"] = category
                    cat_data.append(item)
                    seen.mark(item["url"], item.get("title", ""))
        all_data[category] = cat_data

    seen.save()

    # Build markdown report
    lines = []
    lines.append(f"# AISED / UNESCO EE Net — EOI & RFP Scan — {today}")
    lines.append("")
    lines.append(f"> **Generated:** {datetime.now().strftime('%A, %d %B %Y — %H:%M %Z')}")
    lines.append("> **Sources:** Google News RSS — 20+ target organizations across 5 categories")
    lines.append("> **Scope:** EOIs, RFPs, tenders, grants, and procurement opportunities in education, technology, and engineering")
    lines.append("")

    total = sum(len(v) for v in all_data.values())
    lines.append(f"**Total results: {total} articles**")
    lines.append("")

    for category, items in all_data.items():
        if not items:
            continue
        lines.append(f"---")
        lines.append("")
        lines.append(f"## {category}")
        lines.append("")
        for item in items:
            lines.append(f"- **[{item['org']}]** [{item['title']}]({item['url']})")
            if item.get("date"):
                lines.append(f"  - {item['date']}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*Generated by AISED EOI/RFP Crawler | {today}*")

    report = "\n".join(lines)
    return report, all_data


def _frontmatter():
    """Schema v2 frontmatter for this report (see System/SCHEMA.md).

    This crawler previously wrote its reports with no frontmatter at all, so
    every one of them was invisible to Bases, Dataview and the schema linter.
    """
    from hermes_feeds import frontmatter_block  # one definition, all writers

    return frontmatter_block(
        title=f"AISED EoI/RFP Scan — {datetime.now():%Y-%m-%d}",
        # No `aised` tag: `domain: aised` already carries it, and a tag that
        # merely restates a structured field gets pruned by the migrator's
        # redundancy pass — which would make this writer's output non-idempotent.
        tags=["raw", "eoi", "rfp", "tenders", "intelligence"],
        note_type="raw",
        domain="aised",
    ) + "\n"


def save_local(report):
    """Save report to local markdown file."""
    os.makedirs(TRENDS_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(TRENDS_DIR, f"aised-eoi-rfp-{today}.md")
    with open(path, "w") as f:
        f.write(_frontmatter() + report)
    return f"✅ Saved locally: {path}"


def main():
    report, all_data = build_report()
    total = sum(len(v) for v in all_data.values())

    # Always save local copy
    result = save_local(report)

    # Save health log
    health = {
        "timestamp": datetime.now().isoformat(),
        "total_articles": total,
        "categories": {k: len(v) for k, v in all_data.items()},
        "result": result,
    }
    os.makedirs(os.path.dirname(HEALTH_LOG), exist_ok=True)
    with open(HEALTH_LOG, "w") as f:
        json.dump(health, f, indent=2)

    # Print summary for Telegram
    print(f"🌍 AISED / UNESCO EE Net — EOI & RFP Scan — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  📰 {total} articles across {len(all_data)} categories")
    for cat, items in all_data.items():
        if items:
            orgs = set(i["org"] for i in items)
            print(f"    {cat}: {len(items)} articles from {len(orgs)} orgs")
    print(f"  📤 {result}")

    return 0


if __name__ == "__main__":
    main()
