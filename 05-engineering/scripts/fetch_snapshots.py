"""One-time historical snapshot fetcher — populates the stock_snapshots cache.

Run this once (or after adding new tickers/years). All subsequent training
and backtesting reads from the DB cache — zero yfinance calls needed.

Usage:
    python scripts/fetch_snapshots.py
    python scripts/fetch_snapshots.py --tickers AAPL,MSFT --years 2021,2022,2023,2024
    python scripts/fetch_snapshots.py --refresh  # re-fetch even if already cached
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch and cache historical stock snapshots in SQLite"
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated tickers (default: TOP_50_SP500)",
    )
    parser.add_argument(
        "--years",
        default="2020,2021,2022,2023,2024",
        help="Calendar years to fetch (comma-separated)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch and overwrite existing cached snapshots",
    )
    args = parser.parse_args()

    import yfinance as yf

    from src.db.models import StockSnapshot
    from src.db.session import get_session
    from src.ingestion.fundamentals import TOP_50_SP500
    from src.synthesis.stock_features import extract_stock_features

    # Import backtest helpers — avoids reimplementing the same logic
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from backtest_stocks import (
        _build_historical_snapshot,
        _fetch_annual_return,
        _fetch_spy_annual_return,
    )

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else TOP_50_SP500
    )
    years = [int(y.strip()) for y in args.years.split(",") if y.strip()]

    total_pairs = len(tickers) * len(years)
    logger.info(
        "Fetching %d tickers × %d years = up to %d snapshots",
        len(tickers), len(years), total_pairs,
    )

    # Pre-fetch SPY returns for all years (one batch, then cached locally)
    logger.info("Fetching SPY annual returns for years: %s …", years)
    spy = yf.Ticker("SPY")
    spy_returns: dict[int, float | None] = {
        yr: _fetch_spy_annual_return(spy, yr) for yr in years
    }
    for yr, ret in spy_returns.items():
        logger.info("  SPY %d: %s", yr, f"{ret:+.1%}" if ret is not None else "N/A")

    fetched = skipped = errors = 0

    with get_session() as session:
        for i, ticker in enumerate(tickers):
            logger.info("[%d/%d] %s", i + 1, len(tickers), ticker)

            try:
                t = yf.Ticker(ticker)
                info = t.info or {}
            except Exception as exc:
                logger.warning("  Cannot fetch info for %s: %s — skipping ticker", ticker, exc)
                errors += len(years)
                continue

            for year in years:
                snap_id = f"snapshot-{ticker}-{year}"

                # Idempotency check
                if not args.refresh:
                    existing = session.query(StockSnapshot).filter_by(id=snap_id).first()
                    if existing is not None:
                        skipped += 1
                        continue

                spy_return = spy_returns.get(year)
                stock_return = _fetch_annual_return(t, year)

                if stock_return is None or spy_return is None:
                    logger.debug(
                        "  %s %d — missing return data (stock=%s, spy=%s)",
                        ticker, year,
                        f"{stock_return:.1%}" if stock_return is not None else "N/A",
                        f"{spy_return:.1%}" if spy_return is not None else "N/A",
                    )
                    errors += 1
                    continue

                label = 1.0 if stock_return > spy_return else 0.0

                try:
                    snap = _build_historical_snapshot(t, ticker, year, info)
                except Exception as exc:
                    logger.warning("  %s %d snapshot error: %s — skipping", ticker, year, exc)
                    errors += 1
                    continue

                features = extract_stock_features(snap)

                row = StockSnapshot(
                    id=snap_id,
                    ticker=ticker,
                    year=year,
                    snapshot_json=snap,
                    features_json=features,
                    label=label,
                    stock_return=round(stock_return * 100, 4),
                    spy_return=round(spy_return * 100, 4),
                    fetched_at=datetime.now(timezone.utc),
                )

                # Upsert: delete old row if refreshing
                if args.refresh:
                    session.query(StockSnapshot).filter_by(id=snap_id).delete()

                session.add(row)
                fetched += 1

                outcome = "BEAT" if label == 1.0 else "MISS"
                logger.debug(
                    "  %s %d  stock=%+.1f%%  SPY=%+.1f%%  → %s",
                    ticker, year,
                    stock_return * 100, spy_return * 100, outcome,
                )

            # Commit per ticker to avoid one giant transaction
            session.commit()
            # Brief pause to respect Yahoo Finance rate limits
            time.sleep(0.3)

    total_stored = fetched + skipped
    pct_outperform = None
    if fetched > 0:
        with get_session() as session:
            all_rows = (
                session.query(StockSnapshot)
                .filter(StockSnapshot.label.isnot(None))
                .all()
            )
            if all_rows:
                pct_outperform = sum(r.label for r in all_rows) / len(all_rows) * 100

    logger.info("")
    logger.info("=== fetch_snapshots complete ===")
    logger.info("  Fetched (new)  : %d", fetched)
    logger.info("  Already cached : %d", skipped)
    logger.info("  Errors/missing : %d", errors)
    logger.info("  Total in cache : ~%d", total_stored)
    if pct_outperform is not None:
        logger.info("  Label balance  : %.1f%% outperformed SPY", pct_outperform)
        if pct_outperform > 60:
            logger.warning(
                "  Label imbalance detected (%.1f%% outperform). "
                "train_stocks.py uses scale_pos_weight to correct for this.",
                pct_outperform,
            )
    logger.info("")
    logger.info("Run 'make train-stocks' to train the model on this cached data.")


if __name__ == "__main__":
    main()
