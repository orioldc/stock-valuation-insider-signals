#!/usr/bin/env python3
"""Score every company again and rewrite output/latest_signals.csv.

refresh.py scores before the monthly job cleans corrupt prices
(scripts/cleanup_corrupt_prices.py), so a price typed wrong in a filing would
still inflate the scanner. The monthly job runs this after the cleanup so the
published scanner matches the cleaned database. Nothing else is changed.

Usage:
    python scripts/rescore_signals.py
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_ingestion.data_loader import load_universe  # noqa: E402
from signals.composite_scorer import score_universe  # noqa: E402

OUTPUT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "output", "latest_signals.csv")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    tickers = load_universe()
    df = score_universe(tickers)
    if df.empty:
        logger.error("Scoring produced no rows; latest_signals.csv left as it was")
        sys.exit(1)
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    tmp = OUTPUT_PATH + ".tmp"
    df.to_csv(tmp, index=False)
    os.replace(tmp, OUTPUT_PATH)
    logger.info(f"Saved latest_signals.csv with {len(df)} rows")


if __name__ == "__main__":
    main()
