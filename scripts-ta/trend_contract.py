#!/usr/bin/env python3
"""trend_contract.py — single source of truth for the vertical market-trends jobs.

Every vertical cron (market-trends*.py) used to hard-code its own filename stem,
section headings, and LLM synthesis prompt. That let format drift silently creep
in — a new vertical forgetting the academic-source exclusion, a section emoji
changing in one script and not another, a synthesis block missing entirely.

This module is the contract. Each script imports `VERTICALS` and `validate_report`
instead of hand-rolling those strings, so:

  - every vertical's output filename, headings, and prompt come from ONE place;
  - `validate_report()` checks a produced report against the contract and returns
    what's wrong (missing synthesis, wrong/missing sections, academic leaks);
  - the jobs call `validate_report()` and write `conformant: true/false` into their
    existing health JSON, which `flywheel-health-check.py` already reads — so a
    malformed or miscategorized report now fails the weekly health check too.
"""
import os
import re
from datetime import datetime

VAULT_DIR = os.path.expanduser("~/Documents/Obsidian Vault")
TRENDS_DIR = os.path.join(VAULT_DIR, "Knowledge", "00-Raw", "trends")
SYSTEM_DIR = os.path.join(VAULT_DIR, "System")

# Categories that are allowed to pull academic/scholarly sources (OpenAlex, arXiv,
# Crossref). Consumer/retail verticals must NOT — see multi_source_fetcher.py.
ACADEMIC_CATEGORIES = {
    "ai_ml", "post_quantum_encryption", "agentic_ai", "agi",
    "data_architectures", "science_discoveries", "renewable_energy",
    "food_security", "tcm", "rehabilitation_care",
}

# One entry per vertical. Keys mirror the cron job ids in flywheel-health-check.py.
VERTICALS = {
    "market-trends": {
        "title": "Fashion & Retail",
        "file_stem": "market-trends",
        "health_file": "market-trends-health.json",
        "focus": "fabrics & textile innovation, performance wear, athleisure/activewear, footwear, sportswear, material technology, sustainable/circular fabrics",
        "categories": {
            "fashion_fast_fashion": "## 👕 Fashion & Fast Fashion\n",
            "performance_athleisure": "## 🏃 Performance Wear & Athleisure\n",
            "retail": "## 🏪 Retail Technology\n",
            "ecommerce": "## 📦 E-Commerce\n",
            "sustainability": "## 🌱 Sustainability & Circular Economy\n",
        },
        "academic": False,  # consumer vertical — no academic sources
    },
    "fnb-trends": {
        "title": "F&B & Consumer",
        "file_stem": "fnb-trends",
        "health_file": "fnb-trends-health.json",
        "focus": "food & beverage innovation, fast food & QSR, halal, loyalty & rewards, food delivery, food security, consumption patterns, food technology",
        "categories": {
            "fnb_fast_food": "## 🍔 F&B & Fast Food\n",
            "halal_business": "## 🕌 Halal Business\n",
            "loyalty_rewards": "## 🎯 Loyalty & Rewards\n",
            "food_delivery": "## 🛵 Food Delivery\n",
            "food_security": "## 🌾 Food Security\n",
            "consumption_patterns": "## 📊 Consumption Patterns\n",
        },
        "academic": False,
    },
    "hospitality-trends": {
        "title": "Hospitality & Healthcare",
        "file_stem": "hospitality-trends",
        "health_file": "hospitality-trends-health.json",
        "focus": "hotel & hospitality technology, tourism & travel demand, co-living, experiential travel, retirement living & senior care, TCM, rehabilitation & wellness",
        "categories": {
            "hotel_hospitality": "## 🏨 Hotel & Hospitality\n",
            "tourism": "## ✈️ Tourism\n",
            "co_living": "## 🏠 Co-Living\n",
            "experiential_travel": "## 🧭 Experiential Travel\n",
            "retirement_living": "## 👴 Retirement Living & Care\n",
            "tcm": "## 🌿 Traditional Chinese Medicine (TCM)\n",
            "rehabilitation_care": "## 🏥 Rehabilitation Care\n",
        },
        "academic": False,
    },
    "property-infra-energy-trends": {
        "title": "Property, Infrastructure & Energy",
        "file_stem": "property-infra-energy-trends",
        "health_file": "property-infra-energy-trends-health.json",
        "focus": "property development, construction materials, Gen Z purchasing, asset ownership, ASEAN transactions, data centers, infrastructure projects, renewable energy",
        "categories": {
            "property_development": "## 🏗️ Property Development\n",
            "construction_materials": "## 🧱 Construction Materials\n",
            "gen_z_purchasing": "## 👥 Gen Z Purchasing\n",
            "property_asset_ownership": "## 🏠 Property & Asset Ownership\n",
            "asean_transactions": "## 🌏 ASEAN Transactions\n",
            "data_centers": "## 🖥️ Data Centers\n",
            "infrastructure_projects": "## 🛣️ Infrastructure Projects\n",
            "renewable_energy": "## ☀️ Renewable Energy\n",
        },
        "academic": False,
    },
    "ai-science-investment-trends": {
        "title": "AI, Science & Investment",
        "file_stem": "ai-science-investment-trends",
        "health_file": "ai-science-investment-trends-health.json",
        "focus": "AI/ML breakthroughs, post-quantum encryption, fintech & insurtech, data architectures, agentic AI, AGI, VC/PE investment, science discoveries",
        "categories": {
            "ai_ml": "## 🤖 AI/ML\n",
            "post_quantum_encryption": "## 🔐 Post-Quantum Encryption\n",
            "fintech_insurtech": "## 💰 Fintech & Insurtech\n",
            "data_architectures": "## 🗄️ Data Architectures\n",
            "agentic_ai": "## 🧠 Agentic AI\n",
            "agi": "## 🌌 AGI\n",
            "vc_pe_investment": "## 📈 VC/PE Investment\n",
            "science_discoveries": "## 🔬 Science Discoveries\n",
        },
        "academic": True,
    },
    "csuite-leadership-trends": {
        "title": "C-Suite Leadership & Strategy",
        "file_stem": "csuite-leadership-trends",
        "health_file": "csuite-leadership-trends-health.json",
        "focus": "CDO & AI/data strategy, robotics & edge computing, leadership & management styles, transformation & change management, strategy & innovation, VC/PE & private markets, public markets & finance",
        "categories": {
            "cdo_data_ai": "## 📊 CDO & AI/Data Strategy\n",
            "robotics_edge": "## 🤖 Robotics & Edge Computing\n",
            "leadership_management": "## 👥 Leadership & Management Styles\n",
            "transformation_change": "## 🔄 Transformation & Change Management\n",
            "strategy_innovation": "## 🎯 Strategy & Innovation\n",
            "vc_pe_markets": "## 💰 VC/PE & Private Markets\n",
            "public_finance": "## 📈 Public Markets & Finance\n",
        },
        "academic": False,
    },
}

# Canonical category intent. This is the acceptance contract for a headline
# entering a category, not an SEO list. The fetch layer imports it and the
# report validator uses it to surface contamination as non-conformance.
CATEGORY_INTENT = {
    "fashion_fast_fashion": ("fashion", "apparel", "garment", "fast fashion", "clothing", "luxury fashion"),
    "performance_athleisure": ("athleisure", "activewear", "sportswear", "performance wear", "technical apparel", "footwear"),
    "retail": ("retail technology", "retailer", "store operations", "merchandising", "point of sale", "shopper technology"),
    "supermarket": ("supermarket", "grocery", "grocer", "fresh food retail", "hypermarket"),
    "ecommerce": ("e-commerce", "ecommerce", "online retail", "retail marketplace", "digital commerce"),
    "sustainability": ("circular fashion", "textile recycling", "textile waste", "sustainable apparel", "circular textile", "fashion sustainability"),
    "fnb_fast_food": ("restaurant", "fast food", "qsr", "foodservice", "food service", "menu", "beverage", "food industry", "food brand", "dietary guideline", "dietary guidelines", "dairy industry", "beef industry"),
    "halal_business": ("halal", "muslim consumer", "islamic food", "sharia-compliant", "shari'ah-compliant"),
    "loyalty_rewards": ("loyalty program", "loyalty programme", "rewards program", "rewards programme", "points program", "customer loyalty"),
    "food_delivery": ("food delivery", "meal delivery", "restaurant delivery", "delivery app", "ubereats", "doordash", "grabfood"),
    "food_security": ("food security", "food supply", "food shortage", "food insecurity", "agricultural production", "crop yield", "farmland"),
    "consumption_patterns": ("consumer spending", "consumer confidence", "food consumption", "grocery spending", "restaurant spending", "consumer behaviour", "consumer behavior"),
    "hotel_hospitality": ("hotel", "hospitality", "resort", "hotelier", "guest experience", "lodging"),
    "tourism": ("tourism", "tourist", "travel demand", "destination", "visitor economy"),
    "co_living": ("co-living", "coliving", "shared living", "rental housing", "multifamily housing"),
    "experiential_travel": ("experiential travel", "experience-led travel", "adventure travel", "wellness travel", "travel experience"),
    "retirement_living": ("senior living", "retirement living", "elder care", "elderly care", "aged care", "nursing home", "assisted living", "older adults"),
    "tcm": ("traditional chinese medicine", "acupuncture", "chinese herbal"),
    "rehabilitation_care": ("rehabilitation", "physical therapy", "physiotherapy", "post-acute care", "rehab care"),
    "property_development": ("property development", "real estate development", "housing development", "property developer", "built environment"),
    "construction_materials": ("construction material", "cement", "concrete", "building material", "mass timber"),
    "gen_z_purchasing": ("gen z", "generation z", "young consumer", "youth consumer"),
    "property_asset_ownership": ("property ownership", "real estate ownership", "homeownership", "home ownership", "real estate investment"),
    "asean_transactions": ("asean deal", "asean m&a", "southeast asia deal", "southeast asian acquisition", "cross-border deal", "cross-border acquisition", "cross-border transaction", "regional acquisition", "sea acquisition", "asean acquisition"),
    "data_centers": ("data center", "datacentre", "data centre", "hyperscale", "colocation"),
    "infrastructure_projects": ("infrastructure project", "railway", "highway", "airport expansion", "port project", "utility project"),
    "renewable_energy": ("renewable energy", "solar", "wind power", "battery storage", "clean energy", "energy transition"),
    "ai_ml": ("artificial intelligence", "machine learning", "generative ai", "large language model", "ai model"),
    "post_quantum_encryption": ("post-quantum", "quantum encryption", "quantum cryptography", "quantum-safe"),
    "fintech_insurtech": ("fintech", "financial technology", "digital bank", "payments", "insurtech", "insurance technology"),
    "data_architectures": ("data architecture", "data platform", "data warehouse", "data lake", "database architecture", "data engineering"),
    "agentic_ai": ("agentic ai", "ai agent", "autonomous agent", "agent framework", "ai agents", "language agents"),
    "agi": ("artificial general intelligence", "frontier model"),
    "vc_pe_investment": ("venture capital", "private equity", "funding round", "fundraise", "startup investment"),
    "science_discoveries": ("research", "study", "scientists", "discovery", "scientific", "peer-reviewed"),
    "cdo_data_ai": ("chief data officer", "chief data", "data strategy", "data governance", "enterprise analytics", "enterprise ai", "ai strategy"),
    "robotics_edge": ("robotics", "robot", "edge computing", "edge ai", "industrial automation", "autonomous system"),
    "leadership_management": ("leadership", "management", "manager", "executive leadership", "organizational culture"),
    "transformation_change": ("transformation", "change management", "operating model", "organizational change", "digital transformation"),
    "strategy_innovation": ("corporate strategy", "business strategy", "competitive strategy", "innovation strategy", "strategic planning"),
    "vc_pe_markets": ("venture capital", "private equity", "fundraising", "growth equity", "buyout"),
    "public_finance": ("public markets", "capital markets", "ipo", "stock market", "equity market", "corporate finance", "institutional investor"),
}

def category_match(text: str, category_key: str) -> bool:
    """Whether a title/summary contains direct evidence for a category."""
    terms = CATEGORY_INTENT.get(category_key)
    if not terms:
        return True
    haystack = f" {text.lower()} "
    return any(term in haystack for term in terms)


# Section titles for the LLM analysis & synthesis block.
SYNTH_HEADING = "## 🔍 Analysis & Synthesis"
SYNTH_PARTS = ["Themes & Signals", "What's Notable", "Implications"]


def section_headings(vertical_key: str) -> dict:
    """Return {category_key: markdown_heading} for a vertical."""
    return dict(VERTICALS[vertical_key]["categories"])


def is_academic_vertical(vertical_key: str) -> bool:
    return bool(VERTICALS[vertical_key].get("academic"))


def report_path(vertical_key: str, day: str = None) -> str:
    """Resolve today's (or a given day's) report path for a vertical."""
    day = day or datetime.now().strftime("%Y-%m-%d")
    stem = VERTICALS[vertical_key]["file_stem"]
    return os.path.join(TRENDS_DIR, f"{stem}-{day}.md")


def validate_report(vertical_key: str, report_text: str) -> list:
    """Check a produced report against the contract.

    Returns a list of problem strings (empty == fully conformant).
    Checks:
      - the report contains the required `## 🔍 Analysis & Synthesis` block
      - every expected section heading is present
      - a consumer (non-academic) vertical does not contain openalex/arxiv/crossref
        items (academic noise leak)
    """
    problems = []
    v = VERTICALS[vertical_key]

    # 1. Synthesis block present — ONLY required when the report actually carries
    # items to synthesize. A capture with section headings but zero linked items
    # (e.g. a quiet news day where the fetcher returned nothing) must not be
    # flagged "non-conformant" purely for lacking a synthesis block. We detect real
    # content via bullet items, not just heading stubs.
    has_items = bool(re.search(r"^\s*[-*]\s+\[.+\]\(http", report_text, re.M))
    if has_items and SYNTH_HEADING not in report_text:
        problems.append(f"missing '{SYNTH_HEADING}' block")

    # 2. Required sections present
    for cat, heading in v["categories"].items():
        if heading.strip() not in report_text:
            problems.append(f"missing section {cat!r} ({heading.strip()})")

    # 3. Academic leak into consumer verticals
    if not v["academic"]:
        for domain in ("openalex.org", "arxiv.org", "api.crossref.org", "doi.org"):
            if domain in report_text:
                problems.append(f"academic source {domain} leaked into consumer vertical")

    # 4. Category contamination. Validate the actual displayed section, not
    # just the report's parent vertical. This catches the failure mode that
    # formatting checks cannot see: a valid URL under a semantically wrong H2.
    current_category = None
    heading_to_category = {h.strip(): key for key, h in v["categories"].items()}
    for line in report_text.splitlines():
        stripped = line.strip()
        if stripped in heading_to_category:
            current_category = heading_to_category[stripped]
            continue
        item = re.match(r"^\s*[-*]\s+\[([^\]]+)\]\((https?://[^\s\)]+)\)", line)
        if item and current_category and not category_match(item.group(1), current_category):
            problems.append(f"misclassified item in {current_category}: {item.group(1)[:90]}")

    return problems


def validate_report_file(vertical_key: str, path: str = None) -> list:
    """Validate a report file (defaults to today's) against the contract."""
    path = path or report_path(vertical_key)
    if not os.path.exists(path):
        return [f"report file not found: {path}"]
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError as e:
        return [f"could not read {path}: {e}"]
    return validate_report(vertical_key, text)
