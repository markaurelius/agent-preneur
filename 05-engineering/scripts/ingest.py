"""CLI entry point for data ingestion.

Usage:
    python scripts/ingest.py --source metaculus
    python scripts/ingest.py --source corpus --path data/corpus/cow.csv
"""

import argparse
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _ingest_metaculus(args: argparse.Namespace) -> None:
    from src.db.session import get_session
    from src.ingestion.metaculus import fetch_resolved_questions, upsert_questions

    logger.info("Loading Metaculus binary dataset from HuggingFace ...")
    questions = fetch_resolved_questions()
    logger.info("Fetched %d questions", len(questions))

    with get_session() as session:
        count = upsert_questions(questions, session)

    logger.info("Done. %d new questions inserted.", count)


def _ingest_corpus(args: argparse.Namespace) -> None:
    if not args.path:
        logger.error("--path is required for --source corpus")
        sys.exit(1)

    from src.db.session import get_engine, get_session
    from src.ingestion.corpus import embed_and_store_events, load_cow_dataset

    import chromadb
    import os

    chroma_path = os.environ.get("CHROMA_PATH", "/app/chroma")
    chroma_client = chromadb.PersistentClient(path=chroma_path)

    logger.info("Loading corpus from %s ...", args.path)
    events = load_cow_dataset(args.path)
    logger.info("Loaded %d events from corpus", len(events))

    with get_session() as session:
        count = embed_and_store_events(events, chroma_client, session)

    logger.info("Done. %d new events ingested.", count)


def _ingest_fred(args: argparse.Namespace) -> None:
    import os
    import chromadb

    from src.db.session import get_session
    from src.ingestion.fred import load_fred_corpus
    from src.ingestion.corpus import embed_and_store_events

    api_key = os.environ.get("FRED_API_KEY", "").strip()
    if not api_key:
        logger.error("FRED_API_KEY not set. Get a free key at https://fred.stlouisfed.org/docs/api/api_key.html")
        import sys; sys.exit(1)

    chroma_path = os.environ.get("CHROMA_PATH", "/app/chroma")
    collection_name = args.collection or "financial_events"
    chroma_client = chromadb.PersistentClient(path=chroma_path)

    logger.info("Loading FRED corpus ...")
    events = load_fred_corpus(api_key)
    logger.info("Loaded %d events from FRED", len(events))

    with get_session() as session:
        count = embed_and_store_events(events, chroma_client, session, collection=collection_name)

    logger.info("Done. %d new events ingested into collection '%s'.", count, collection_name)


def _ingest_edgar(args: argparse.Namespace) -> None:
    import chromadb

    from src.db.session import get_session
    from src.ingestion.edgar import load_edgar_corpus
    from src.ingestion.corpus import embed_and_store_events

    chroma_path = os.environ.get("CHROMA_PATH", "/app/chroma")
    collection_name = args.collection or "edgar_events"
    chroma_client = chromadb.PersistentClient(path=chroma_path)

    days = args.days or 90
    logger.info("Loading EDGAR 8-K corpus: last %d days ...", days)
    events = load_edgar_corpus(days=days)
    logger.info("Loaded %d events from EDGAR", len(events))

    with get_session() as session:
        count = embed_and_store_events(events, chroma_client, session, collection=collection_name)

    logger.info("Done. %d new events ingested into collection '%s'.", count, collection_name)


def _ingest_gdelt(args: argparse.Namespace) -> None:
    import chromadb

    from src.db.session import get_session
    from src.ingestion.gdelt import load_gdelt_corpus
    from src.ingestion.corpus import embed_and_store_events

    chroma_path = os.environ.get("CHROMA_PATH", "/app/chroma")
    collection_name = args.collection or "gdelt_events"
    chroma_client = chromadb.PersistentClient(path=chroma_path)

    days = args.days or 90
    top_per_day = args.top_per_day or 200
    logger.info("Loading GDELT corpus: last %d days, top %d events/day ...", days, top_per_day)
    events = load_gdelt_corpus(days=days, top_per_day=top_per_day)
    logger.info("Loaded %d events from GDELT", len(events))

    with get_session() as session:
        count = embed_and_store_events(events, chroma_client, session, collection=collection_name)

    logger.info("Done. %d new events ingested into collection '%s'.", count, collection_name)


def _ingest_fundamentals(args: argparse.Namespace) -> None:
    import chromadb

    from src.db.session import get_session
    from src.ingestion.fundamentals import load_fundamentals_corpus, TOP_50_SP500
    from src.ingestion.corpus import embed_and_store_events

    chroma_path = os.environ.get("CHROMA_PATH", "/app/chroma")
    collection_name = args.collection or "fundamentals"
    chroma_client = chromadb.PersistentClient(path=chroma_path)

    # Allow caller to narrow the ticker list via --tickers (comma-separated)
    tickers = None
    if getattr(args, "tickers", None):
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    ticker_count = len(tickers) if tickers else len(TOP_50_SP500)
    logger.info(
        "Loading fundamentals corpus for %d tickers (collection: '%s') ...",
        ticker_count,
        collection_name,
    )
    events = load_fundamentals_corpus(tickers=tickers)
    logger.info("Loaded %d events from yfinance fundamentals", len(events))

    with get_session() as session:
        count = embed_and_store_events(events, chroma_client, session, collection=collection_name)

    logger.info("Done. %d new events ingested into collection '%s'.", count, collection_name)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest data into the engine database")
    parser.add_argument(
        "--source",
        required=True,
        choices=["metaculus", "corpus", "fred", "gdelt", "edgar", "fundamentals"],
        help="Data source to ingest",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Path to corpus file (required for --source corpus)",
    )
    parser.add_argument(
        "--collection",
        default=None,
        help="ChromaDB collection name (optional override)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Number of days to fetch (--source gdelt, default 90)",
    )
    parser.add_argument(
        "--top-per-day",
        type=int,
        default=None,
        dest="top_per_day",
        help="Max events per day by mention count (--source gdelt, default 200)",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="Comma-separated ticker list (--source fundamentals, default: top-50 S&P 500)",
    )
    args = parser.parse_args()

    if args.source == "metaculus":
        _ingest_metaculus(args)
    elif args.source == "corpus":
        _ingest_corpus(args)
    elif args.source == "fred":
        _ingest_fred(args)
    elif args.source == "gdelt":
        _ingest_gdelt(args)
    elif args.source == "edgar":
        _ingest_edgar(args)
    elif args.source == "fundamentals":
        _ingest_fundamentals(args)


if __name__ == "__main__":
    main()
