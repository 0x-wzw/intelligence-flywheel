#!/usr/bin/env python3
"""
bias_taxonomy.py

Shared perspective taxonomy for bias balancing.

Two consumers: bias-balance-check.py (weekly audit) and signal-noise.py
(credibility scoring). Kept in one place so a source's perspective label cannot
drift between them.

The taxonomy follows the four axes defined in the Intelligence Cronjobs
Improvement Plan:
  - tech:      accelerationist | safety_first | skeptic | pragmatic
  - policy:    progressive | centrist | libertarian | conservative
  - economic:  keynesian | monetarist | austrian | mmt | degrowth | development
  - geography: western | global_south | china | europe

A source may carry labels on several axes. Absence of a label is meaningful —
it means we have not classified it, and unclassified sources are reported.
"""

from typing import Dict, List, Set

# ── Source → perspective registry ─────────────────────────────────────
# Keyed by domain fragment (matched as a substring of the URL host).
SOURCE_PERSPECTIVES: Dict[str, Dict[str, str]] = {
    # ── AI: safety / alignment ──
    "lesswrong.com": {"tech": "safety_first", "geography": "western"},
    "alignmentforum.org": {"tech": "safety_first", "geography": "western"},
    "intelligence.org": {"tech": "safety_first", "geography": "western"},
    "anthropic.com": {"tech": "safety_first", "geography": "western"},
    "aisi.gov.uk": {"tech": "safety_first", "policy": "centrist", "geography": "europe"},
    "futureoflife.org": {"tech": "safety_first", "geography": "western"},
    "dair-institute.org": {"tech": "safety_first", "policy": "progressive"},
    "epoch.ai": {"tech": "safety_first", "geography": "western"},
    "aisnakeoil.com": {"tech": "skeptic", "geography": "western"},

    # ── AI: acceleration / capability ──
    "openai.com": {"tech": "accelerationist", "geography": "western"},
    "deepmind.google": {"tech": "accelerationist", "geography": "western"},
    "blog.google": {"tech": "accelerationist", "geography": "western"},
    "ai.meta.com": {"tech": "accelerationist", "geography": "western"},
    "nvidia.com": {"tech": "accelerationist", "geography": "western"},
    "a16z.com": {"tech": "accelerationist", "policy": "libertarian", "geography": "western"},
    "blog.samaltman.com": {"tech": "accelerationist", "geography": "western"},
    "microsoft.com": {"tech": "accelerationist", "geography": "western"},

    # ── AI: skeptics ──
    "garymarcus.substack.com": {"tech": "skeptic", "geography": "western"},
    "rodneybrooks.com": {"tech": "skeptic", "geography": "western"},
    "erikjlarson.substack.com": {"tech": "skeptic", "geography": "western"},
    "thezvi": {"tech": "skeptic", "geography": "western"},

    # ── AI: pragmatic / adoption ──
    "deeplearning.ai": {"tech": "pragmatic", "geography": "western"},
    "oneusefulthing.org": {"tech": "pragmatic", "geography": "western"},
    "simonwillison.net": {"tech": "pragmatic", "geography": "western"},
    "interconnects.ai": {"tech": "pragmatic", "geography": "western"},
    "hai.stanford.edu": {"tech": "pragmatic", "policy": "centrist", "geography": "western"},

    # ── Tech policy / platform ──
    "platformer.news": {"policy": "progressive", "tech": "skeptic"},
    "platformer.substack.com": {"policy": "progressive", "tech": "skeptic"},
    "mattstoller.substack.com": {"policy": "progressive", "economic": "keynesian"},
    "stratechery.com": {"policy": "centrist", "tech": "pragmatic"},
    "lawfaremedia.org": {"policy": "centrist", "geography": "western"},
    "artificialintelligenceact.eu": {"policy": "progressive", "geography": "europe"},
    "technologyreview.com": {"policy": "centrist", "tech": "pragmatic"},

    # ── Think tanks / policy ──
    "brookings.edu": {"policy": "centrist", "geography": "western"},
    "csis.org": {"policy": "centrist", "geography": "western"},
    "rand.org": {"policy": "centrist", "geography": "western"},
    "cato.org": {"policy": "libertarian", "economic": "austrian"},
    "mercatus.org": {"policy": "libertarian", "economic": "austrian"},
    "heritage.org": {"policy": "conservative"},
    "aei.org": {"policy": "conservative", "economic": "monetarist"},
    "epi.org": {"policy": "progressive", "economic": "keynesian"},
    "bruegel.org": {"policy": "centrist", "geography": "europe"},
    "carnegieendowment.org": {"policy": "centrist", "geography": "western"},
    "chathamhouse.org": {"policy": "centrist", "geography": "europe"},
    "whitehouse.gov": {"policy": "centrist", "geography": "western"},

    # ── Economics ──
    "paulkrugman.substack.com": {"economic": "keynesian", "policy": "progressive"},
    "marginalrevolution.com": {"economic": "monetarist", "policy": "libertarian"},
    "imf.org": {"economic": "monetarist", "policy": "centrist"},
    "povertyactionlab.org": {"economic": "development", "geography": "global_south"},
    "worldbank.org": {"economic": "development", "geography": "global_south"},
    "moneystuff": {"economic": "monetarist", "policy": "centrist"},
    "exponentialview.co": {"tech": "pragmatic", "policy": "centrist", "geography": "europe"},

    # ── Business / venture ──
    "nfx.com": {"tech": "accelerationist", "policy": "libertarian"},
    "nav.al": {"tech": "accelerationist", "policy": "libertarian"},
    "reidhoffman.substack.com": {"tech": "accelerationist", "policy": "centrist"},
    "blog.eladgil.com": {"tech": "accelerationist"},
    "thegeneralist.com": {"tech": "pragmatic"},
    "techcrunch.com": {"tech": "accelerationist", "geography": "western"},  # VC/startup news
    "crunchbase.com": {"tech": "pragmatic", "geography": "western"},  # startup data
    "producthunt.com": {"geography": "western"},  # launch aggregator, perspective-neutral
    "baseten.co": {"tech": "pragmatic", "geography": "western"},  # AI infra vendor blog

    # ── Podcasts (perspective via host/guests) ──
    "simplecast.com": {"geography": "western"},  # podcast hosting (a16z, etc.)
    "libsyn.com": {"geography": "western"},  # podcast hosting (All-In, etc.)

    # ── Science / research media ──
    "science.org": {"tech": "pragmatic", "geography": "western"},  # AAAS journal news
    "newscientist.com": {"tech": "pragmatic", "geography": "western"},
    "openalex.org": {"tech": "pragmatic", "geography": "western"},  # scholarly index, neutral
    "latent.space": {"tech": "pragmatic", "geography": "western"},  # AI/agent commentary

    # ── Retail / consumer verticals ──
    "retaildive.com": {"policy": "centrist", "geography": "western"},  # retail trade press

    # ── Developer / community blogs (perspective-neutral tech) ──
    "dev.to": {"geography": "western"},  # dev community, perspective-neutral
    "notion.so": {"geography": "western"},  # product/docs

    # ── News / global ──
    "nytimes.com": {"policy": "centrist", "geography": "western"},
    "restofworld.org": {"geography": "global_south"},  # global tech coverage

    # ── Aggregators (perspective-neutral, but geography-skewed) ──
    "news.ycombinator.com": {"geography": "western"},
    "hnrss.org": {"geography": "western"},
    "reddit.com": {"geography": "western"},
    # news.google.com is a REDIRECT WRAPPER, not a source: the real publisher
    # is in the article destination. Stamping every wrapper 'western' poisoned
    # the axis — 2,088 of 7,455 raw-capture links (28%) are wrappers, which
    # drowned regional publishers (Bernama, Halal Times, NRN consumer verticals)
    # in false 'western' labels. FIX 2026-08-30: return {} for wrapper hosts so
    # they count as unclassified instead of fabricating geography.
    # "news.google.com": REMOVED — see note above.
    "arxiv.org": {"tech": "pragmatic", "geography": "western"},  # research preprints, neutral
    "huggingface.co": {"tech": "pragmatic", "geography": "western"},  # open-source ML community
    "thebignewsletter.com": {"policy": "progressive", "economic": "keynesian"},  # Matt Stoller's BIG
    # ── Consumer verticals (from thought-leader tracker) ──
    "nrn.com": {"geography": "western"},  # Nation's Restaurant News, QSR economics
    "modernretail.co": {"geography": "western"},  # retail operations
    "hospitalitynet.org": {"geography": "western"},  # hospitality ops
    "seniorhousingnews.com": {"geography": "western"},  # senior care
    "thewisemarketer.com": {"geography": "western"},  # loyalty industry
    "halaltimes.com": {"geography": "global_south"},  # halal industry, SEA/MENA centric
    "about.bnef.com": {"policy": "centrist", "geography": "western"},  # BloombergNEF
    # Malaysian publishers (Google News wrappers hid these)
    "bharian.com.my": {"geography": "global_south"},  # Berita Harian (MY)
    "nst.com.my": {"geography": "western"},  # New Straits Times (MY, English)
    "thestar.com.my": {"geography": "western"},  # The Star (MY, English)
    "bernama.com": {"geography": "global_south"},  # Malaysian national wire
    "malaymail.com": {"geography": "western"},  # MY English daily
}

# ── Topic clusters ────────────────────────────────────────────────────
# A capture belongs to a cluster if its text matches any keyword.
TOPIC_CLUSTERS: Dict[str, List[str]] = {
    "AI Safety & Alignment": [
        "alignment", "ai safety", "existential", "x-risk", "interpretability",
        "red team", "jailbreak", "misuse", "catastrophic", "control problem",
        "constitutional ai", "rlhf", "oversight",
    ],
    "AI Capability & Research": [
        "scaling law", "benchmark", "state of the art", "sota", "frontier model",
        "training run", "parameters", "reasoning", "multimodal", "inference",
        "fine-tuning", "pretraining", "transformer", "diffusion",
    ],
    "AI Economics & Labour": [
        "job", "labour", "labor", "productivity", "automation", "employment",
        "wages", "displacement", "gdp", "economic growth", "capex", "compute cost",
    ],
    "Tech Policy & Regulation": [
        "regulation", "ai act", "policy", "governance", "antitrust", "compliance",
        "executive order", "legislation", "privacy", "copyright", "liability",
    ],
    "Geopolitics & Chips": [
        "export control", "china", "taiwan", "semiconductor", "chip", "tsmc",
        "sanction", "national security", "sovereign", "geopolitic",
    ],
    "Business & Strategy": [
        "startup", "venture", "funding round", "acquisition", "ipo", "revenue",
        "market share", "moat", "go-to-market", "valuation",
    ],
    "Macro & Markets": [
        "inflation", "interest rate", "federal reserve", "recession", "bond",
        "equities", "monetary", "fiscal", "central bank",
    ],
    "Consumer & F&B": [
        "f&b", "fnb", "restaurant", "qsr", "quick service", "fast food",
        "franchise", "menu", "halal", "grocery", "retail media", "marketplace",
        "e-commerce", "d2c", "consumer", "loyalty", "same-store sales",
        "revpar", "occupancy", "adr", "hotel", "hospitality", "senior living",
        "singlethread", "restaurant group", "dining", "chef", "food service",
        "assisted living", "malaysian retail", "meat processing", "coffee",
    ],
    "Climate & Energy": [
        "climate", "emissions", "renewable", "solar", "nuclear", "grid",
        "data center power", "energy demand", "decarbon",
    ],
}

# Which perspectives we EXPECT to see on each cluster for it to count as balanced.
EXPECTED_AXES: Dict[str, List[str]] = {
    "AI Safety & Alignment": ["tech"],
    "AI Capability & Research": ["tech"],
    "AI Economics & Labour": ["economic", "tech"],
    "Tech Policy & Regulation": ["policy"],
    "Geopolitics & Chips": ["policy", "geography"],
    "Business & Strategy": ["tech", "policy"],
    "Macro & Markets": ["economic"],
    "Climate & Energy": ["policy", "economic"],
}

AXIS_VALUES: Dict[str, List[str]] = {
    "tech": ["accelerationist", "safety_first", "skeptic", "pragmatic"],
    "policy": ["progressive", "centrist", "libertarian", "conservative"],
    "economic": ["keynesian", "monetarist", "austrian", "mmt", "degrowth", "development"],
    "geography": ["western", "global_south", "china", "europe"],
}

# ── Recommended voices to close specific gaps ─────────────────────────
# Used to turn "you are missing X" into an actionable feed suggestion.
GAP_RECOMMENDATIONS: Dict[str, List[str]] = {
    "tech:skeptic": [
        "Gary Marcus — https://garymarcus.substack.com/feed",
        "AI Snake Oil (Narayanan & Kapoor) — https://www.aisnakeoil.com/feed",
        "Rodney Brooks — https://rodneybrooks.com/feed",
    ],
    "tech:safety_first": [
        "LessWrong — https://www.lesswrong.com/feed.xml",
        "Alignment Forum — https://www.alignmentforum.org/feed.xml",
        "MIRI — https://intelligence.org/feed/",
    ],
    "tech:accelerationist": [
        "a16z — see source-diversity wiki for the current working URL",
        "NFX — https://www.nfx.com/rss.xml",
    ],
    "tech:pragmatic": [
        "Simon Willison — https://simonwillison.net/atom/everything/",
        "Ethan Mollick — https://www.oneusefulthing.org/feed",
    ],
    "policy:libertarian": [
        "Cato Institute — https://www.cato.org/rss/blog",
        "Mercatus Center — https://www.mercatus.org/rss.xml",
    ],
    "policy:conservative": [
        "AEI — https://www.aei.org/feed/",
        "Heritage Foundation — https://www.heritage.org/rss",
    ],
    "policy:progressive": [
        "Economic Policy Institute — https://www.epi.org/feed/",
        "Matt Stoller (BIG) — https://mattstoller.substack.com/feed",
    ],
    "policy:centrist": [
        "Brookings — see source-diversity wiki",
        "Lawfare — https://www.lawfaremedia.org/feeds/articles.rss",
    ],
    "economic:degrowth": [
        "Jason Hickel — https://www.jasonhickel.org/blog?format=rss",
        "Degrowth.info — https://degrowth.info/en/blog/feed",
    ],
    "economic:mmt": [
        "New Economic Perspectives — https://neweconomicperspectives.org/feed",
        "Stephanie Kelton — https://stephaniekelton.substack.com/feed",
    ],
    "economic:keynesian": [
        "Paul Krugman — https://paulkrugman.substack.com/feed",
    ],
    "economic:austrian": [
        "Cato — https://www.cato.org/rss/blog",
        "Mises Institute — https://mises.org/rss.xml",
    ],
    "economic:development": [
        "J-PAL — https://www.povertyactionlab.org/blog.xml",
        "Center for Global Development — https://www.cgdev.org/rss/all",
    ],
    "geography:global_south": [
        "Rest of World — https://restofworld.org/feed/latest/",
        "African Arguments — https://africanarguments.org/feed/",
        "The Wire (India) — https://thewire.in/rss",
    ],
    "geography:china": [
        "ChinAI Newsletter — https://chinai.substack.com/feed",
        "Pekingnology — https://www.pekingnology.com/feed",
    ],
    "geography:europe": [
        "Bruegel — https://www.bruegel.org/rss.xml",
        "EU AI Act Newsletter — https://artificialintelligenceact.eu/feed/",
    ],
}


def classify_source(url: str) -> Dict[str, str]:
    """Return the perspective labels for a URL, or {} if unclassified."""
    if not url:
        return {}
    lowered = url.lower()
    for fragment, labels in SOURCE_PERSPECTIVES.items():
        if fragment in lowered:
            return labels
    return {}


def classify_topics(text: str) -> Set[str]:
    """Return every topic cluster whose keywords appear in the text."""
    lowered = text.lower()
    return {
        cluster
        for cluster, keywords in TOPIC_CLUSTERS.items()
        if any(kw in lowered for kw in keywords)
    }
