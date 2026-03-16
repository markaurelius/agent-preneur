"""Finance domain keyword filter — mirrors the geopolitics filter in metaculus.py."""

_FINANCE_HIGH_SIGNAL = [
    "federal reserve", "fed funds", "fomc", "rate cut", "rate hike", "rate increase",
    "interest rate", "monetary policy", "quantitative easing", "qe ",
    "inflation", "cpi", "pce", "deflation", "hyperinflation",
    "yield curve", "inverted yield", "treasury yield",
    "nonfarm payroll", "jobs report", "unemployment rate",
    "gdp growth", "recession", "economic contraction",
    "s&p 500", "nasdaq", "dow jones", "stock market crash", "bear market", "bull market",
    "central bank", "bank of england", "ecb ", "bank of japan",
    "bitcoin halving", "crypto market",
    "bitcoin ", "bitcoin?", "ethereum ", "will bitcoin",
]

_FINANCE_LOW_SIGNAL = [
    "interest rates", "fed ", "bls report", "payroll",
    "inflation rate", "gdp", "economic growth",
    "stock market", "equity market", "market correction",
    "treasury", "bond yield", "yield spread",
    "unemployment", "labor market",
    "bitcoin", "ethereum", "cryptocurrency",
    "earnings", "ipo", "merger", "acquisition",
    "hedge fund", "private equity", "venture capital",
    "commodity", "oil price", "gold price",
    "exchange rate", "dollar", "euro", "yen",
    "fiscal policy", "deficit", "debt ceiling",
]


def _is_finance(text: str) -> bool:
    """Return True if the question text is finance-relevant.

    Passes if any high-signal keyword matches, or two or more low-signal keywords match.
    """
    lower = text.lower()
    if any(kw in lower for kw in _FINANCE_HIGH_SIGNAL):
        return True
    return sum(1 for kw in _FINANCE_LOW_SIGNAL if kw in lower) >= 2
