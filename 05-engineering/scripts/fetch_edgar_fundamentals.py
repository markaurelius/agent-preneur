"""
Fetch SEC EDGAR XBRL financial data for all tickers and store in edgar_fundamentals table.

Uses:
- Company tickers JSON: https://www.sec.gov/files/company_tickers.json (ticker → CIK mapping)
- Company facts API: https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json

SEC fair-use: must include User-Agent header with contact info.
Rate limit: ≤10 req/s; use 0.15s sleep between requests.

Usage:
    python scripts/fetch_edgar_fundamentals.py
    python scripts/fetch_edgar_fundamentals.py --tickers AAPL,MSFT,NVDA
    python scripts/fetch_edgar_fundamentals.py --refresh  # re-fetch even if cached
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

_SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_SEC_HEADERS = {"User-Agent": "StockPredictionEngine research@example.com"}
_SLEEP = 0.15  # seconds between requests — stay well under 10 req/s SEC limit

# XBRL concept names to try (in order) for each financial metric
_CONCEPT_MAP = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "gross_profit": ["GrossProfit"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "total_assets": ["Assets"],
    "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
}

_VALID_FORMS = {"10-Q", "10-K"}
_VALID_PERIODS = {"Q1", "Q2", "Q3", "Q4", "FY"}


def _load_cik_map() -> dict[str, str]:
    """Fetch SEC company_tickers.json and build ticker → zero-padded CIK map.

    Returns: {"AAPL": "0000320193", "MSFT": "0000789019", ...}
    """
    import requests

    log.info("Loading CIK map from SEC …")
    resp = requests.get(_SEC_TICKERS_URL, headers=_SEC_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    cik_map: dict[str, str] = {}
    for entry in data.values():
        ticker = str(entry.get("ticker", "")).upper().strip()
        cik_int = entry.get("cik_str")
        if ticker and cik_int is not None:
            cik_map[ticker] = str(int(cik_int)).zfill(10)

    log.info("CIK map loaded: %d tickers", len(cik_map))
    return cik_map


def _fetch_company_facts(cik_padded: str) -> dict | None:
    """Fetch company facts JSON from SEC EDGAR for a given zero-padded CIK."""
    import requests

    url = _SEC_FACTS_URL.format(cik=cik_padded)
    resp = requests.get(url, headers=_SEC_HEADERS, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.json()


def _extract_observations(facts_data: dict, field: str, concepts: list[str]) -> dict[tuple[int, str], dict]:
    """Extract (fy, fp) → best observation for a given field from XBRL facts.

    Returns dict keyed by (fiscal_year, fiscal_period) → {"val": float, "filed": str, "end": str}
    Uses latest filed_date when duplicates exist for the same (fy, fp).

    Strategy: merge observations from ALL listed concepts. This handles companies like AAPL
    that changed XBRL concept names over time (e.g. SalesRevenueNet 2009-2018, then
    RevenueFromContractWithCustomerExcludingAssessedTax 2019+). When two concepts both provide
    a value for the same (fy, fp), the one with the later filed_date wins (latest amendment).
    """
    us_gaap = facts_data.get("facts", {}).get("us-gaap", {})
    result: dict[tuple[int, str], dict] = {}

    for concept_name in concepts:
        concept = us_gaap.get(concept_name)
        if concept is None:
            continue

        units = concept.get("units", {})
        usd_obs = units.get("USD", [])
        if not usd_obs:
            continue

        for obs in usd_obs:
            form = obs.get("form", "")
            fp = obs.get("fp", "")
            fy = obs.get("fy")
            filed = obs.get("filed", "")
            end = obs.get("end", "")
            val = obs.get("val")

            if form not in _VALID_FORMS:
                continue
            if fp not in _VALID_PERIODS:
                continue
            if fy is None or val is None:
                continue

            key = (int(fy), fp)
            existing = result.get(key)
            # Keep latest filed_date to handle amendments and concept transitions
            if existing is None or filed > existing["filed"]:
                result[key] = {"val": float(val), "filed": filed, "end": end}

    return result


def _build_edgar_rows(ticker: str, facts_data: dict) -> list[dict]:
    """Parse XBRL facts into a list of EdgarFundamentals-ready dicts."""
    # Extract observations for each field
    field_obs: dict[str, dict[tuple[int, str], dict]] = {}
    for field, concepts in _CONCEPT_MAP.items():
        field_obs[field] = _extract_observations(facts_data, field, concepts)

    # Collect all (fy, fp) keys across all fields
    all_keys: set[tuple[int, str]] = set()
    for obs_dict in field_obs.values():
        all_keys.update(obs_dict.keys())

    rows = []
    for (fy, fp) in all_keys:
        # Determine filed_date and period_end from whatever fields have this (fy, fp)
        filed_date = None
        period_end = None
        for obs_dict in field_obs.values():
            obs = obs_dict.get((fy, fp))
            if obs:
                if filed_date is None or obs["filed"] > filed_date:
                    filed_date = obs["filed"]
                if period_end is None and obs["end"]:
                    period_end = obs["end"]

        if not filed_date:
            continue

        rows.append({
            "id": f"{ticker}-{fy}-{fp}",
            "ticker": ticker,
            "fiscal_year": fy,
            "fiscal_period": fp,
            "period_end": period_end,
            "filed_date": filed_date,
            "revenue": (field_obs["revenue"].get((fy, fp)) or {}).get("val"),
            "net_income": (field_obs["net_income"].get((fy, fp)) or {}).get("val"),
            "gross_profit": (field_obs["gross_profit"].get((fy, fp)) or {}).get("val"),
            "operating_income": (field_obs["operating_income"].get((fy, fp)) or {}).get("val"),
            "total_assets": (field_obs["total_assets"].get((fy, fp)) or {}).get("val"),
            "long_term_debt": (field_obs["long_term_debt"].get((fy, fp)) or {}).get("val"),
            "fetched_at": datetime.utcnow(),
        })

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch SEC EDGAR XBRL fundamentals for all tickers")
    parser.add_argument("--tickers", default=None, help="Comma-separated tickers (default: SP500_EXTENDED)")
    parser.add_argument("--refresh", action="store_true", help="Re-fetch even if already cached")
    args = parser.parse_args()

    from src.db.models import EdgarFundamentals
    from src.db.session import get_session
    from src.ingestion.fundamentals import SP500_EXTENDED

    tickers: list[str] = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else SP500_EXTENDED
    )

    log.info("Universe: %d tickers", len(tickers))

    # -----------------------------------------------------------------------
    # Step 1: Determine already-fetched tickers (for idempotency)
    # -----------------------------------------------------------------------
    already_fetched: set[str] = set()
    if not args.refresh:
        with get_session() as session:
            rows = session.query(EdgarFundamentals.ticker).distinct().all()
            already_fetched = {r.ticker for r in rows}
        if already_fetched:
            log.info("Skipping %d tickers already in DB (use --refresh to re-fetch)", len(already_fetched))

    # -----------------------------------------------------------------------
    # Step 2: Load CIK map
    # -----------------------------------------------------------------------
    cik_map = _load_cik_map()
    time.sleep(_SLEEP)

    # -----------------------------------------------------------------------
    # Step 3: Fetch and store per ticker
    # -----------------------------------------------------------------------
    failed_cik: list[str] = []
    failed_fetch: list[str] = []
    total = len(tickers)
    stored_total = 0

    with get_session() as session:
        for n, ticker in enumerate(tickers, start=1):
            # Skip if already in DB (unless --refresh)
            if ticker in already_fetched and not args.refresh:
                continue

            # Resolve CIK
            cik = cik_map.get(ticker)
            if cik is None:
                # Try common aliases (e.g. BRK-B → BRKB)
                alt = ticker.replace("-", "")
                cik = cik_map.get(alt)
                if cik is None:
                    log.warning("No CIK found for ticker: %s — skipping", ticker)
                    failed_cik.append(ticker)
                    continue

            # Fetch company facts
            try:
                facts_data = _fetch_company_facts(cik)
                time.sleep(_SLEEP)
            except Exception as exc:
                log.warning("EDGAR fetch failed for %s (CIK %s): %s", ticker, cik, exc)
                failed_fetch.append(ticker)
                continue

            if facts_data is None:
                log.warning("EDGAR 404 for %s (CIK %s) — skipping", ticker, cik)
                failed_cik.append(ticker)
                continue

            # Parse into rows
            try:
                rows = _build_edgar_rows(ticker, facts_data)
            except Exception as exc:
                log.warning("Parse error for %s: %s — skipping", ticker, exc)
                failed_fetch.append(ticker)
                continue

            # Upsert rows
            try:
                for row_dict in rows:
                    obj = EdgarFundamentals(**row_dict)
                    session.merge(obj)
                session.commit()
                stored_total += len(rows)
            except Exception as exc:
                session.rollback()
                log.warning("DB error for %s: %s — skipping", ticker, exc)
                failed_fetch.append(ticker)
                continue

            if n % 10 == 0 or n == total:
                log.info("  Fundamentals: %d/%d tickers done (rows stored so far: %d)", n, total, stored_total)

    log.info("")
    log.info("=== fetch_edgar_fundamentals complete ===")
    log.info("  Tickers processed  : %d", total - len(already_fetched) if not args.refresh else total)
    log.info("  Total rows stored  : %d", stored_total)
    log.info("  Failed CIK lookup  : %d %s", len(failed_cik), failed_cik if failed_cik else "")
    log.info("  Failed fetch/parse : %d %s", len(failed_fetch), failed_fetch if failed_fetch else "")


if __name__ == "__main__":
    main()
