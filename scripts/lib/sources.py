"""Trusted news source allowlists.

TRUSTED_SOURCES       — core set used by the trading pipeline (high signal-to-noise).
MACRO_TRUSTED_SOURCES — extended set for the macro pipeline; includes official data
                        sources and major research houses that the trading pipeline
                        doesn't need but macro analysis depends on.
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
}


def is_trusted_source(source: str, macro: bool = False) -> bool:
    allowlist = MACRO_TRUSTED_SOURCES if macro else TRUSTED_SOURCES
    src = source.lower()
    return any(trusted in src for trusted in allowlist)
