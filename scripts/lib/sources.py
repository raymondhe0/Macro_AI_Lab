"""Trusted news source allowlists.

TRUSTED_SOURCES       — core set used by the trading pipeline (high signal-to-noise).
MACRO_TRUSTED_SOURCES — extended set for the macro pipeline; includes official data
                        sources and major research houses that the trading pipeline
                        doesn't need but macro analysis depends on.
TECH_TRUSTED_SOURCES  — extended set for the tech pipeline; adds premium tech journalism
                        and industry publications covering AI, semiconductors, and Big Tech.
"""

TRUSTED_SOURCES: set[str] = {
    # Wire services
    "reuters", "associated press", " ap ", "dow jones", "mt newswires",
    # TV / web financial media
    "cnbc", "bloomberg", "marketwatch", "barron",
    # Major press
    "wall street journal", "wsj", "financial times", "ft.com",
    "new york times", "nytimes", "cnn", "washington post",
    # Market data / FX platforms
    "yahoo finance", "investing.com", "forex.com",
}

MACRO_TRUSTED_SOURCES: set[str] = TRUSTED_SOURCES | {
    # Official US government / central bank sources
    "federalreserve", "federal reserve", "bls.gov", "bureau of labor statistics",
    "bea.gov", "census.gov", "treasury.gov", "whitehouse.gov",
    # International institutions
    "imf.org", "international monetary fund", "worldbank", "world bank",
    "bis.org", "ecb.europa", "european central bank", "bankofengland",
    # Exchange / derivatives data
    "cmegroup", "cme group", "cboe",
    # Major buy-side / sell-side research
    "blackrock", "pimco", "vanguard", "fidelity",
    "goldmansachs", "goldman sachs", "morganstanley", "morgan stanley",
    "jpmorgan", "j.p. morgan", "citigroup", "citi", "bankofamerica", "bank of america",
    "ubs", "barclays", "deutsche bank",
    # Specialist macro / policy
    "the economist", "project syndicate", "brookings", "peterson institute",
    "council on foreign relations", "cfr",
    # Business press covering macro
    "forbes", "fortune", "thestreet", "seeking alpha", "axios", "business insider",
    "yahoo finance", "investing.com",
}


TECH_TRUSTED_SOURCES: set[str] = TRUSTED_SOURCES | {
    # Premium tech journalism
    "the information", "semafor", "platformer", "stratechery",
    # General tech media (high editorial standard)
    "wired", "ars technica", "techcrunch", "the verge", "mit technology review",
    # Business press with strong tech desks
    "axios", "fortune", "forbes", "business insider", "venturebeat", "zdnet",
    "the register", "protocol", "quartz",
    # Science / research coverage (full names to avoid substring false positives)
    "nature.com", "science.org", "ieee spectrum", "ieee",
    # Industry-specific
    "semiconductor industry association", "semianalysis", "chips and cheese",
    "tom's hardware", "anandtech", "electronicsweekly",
    # Company IR / official blogs (press releases)
    "nvidia", "google", "alphabet", "microsoft", "meta", "amazon", "apple",
    "anthropic", "openai", "xai", "deepmind",
}


def is_trusted_source(source: str, macro: bool = False, tech: bool = False) -> bool:
    if tech:
        allowlist = TECH_TRUSTED_SOURCES
    elif macro:
        allowlist = MACRO_TRUSTED_SOURCES
    else:
        allowlist = TRUSTED_SOURCES
    src = source.lower()
    return any(trusted in src for trusted in allowlist)
