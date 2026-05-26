"""Quality filters: market cap, volume, etc."""

import logging

logger = logging.getLogger(__name__)


def apply_filters(df, min_market_cap=300_000_000, min_volume=100_000):
    """
    Filter out micro-caps and low-volume stocks.
    Uses yfinance to fetch market cap data.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not available, skipping filters")
        return df
    
    market_caps = {}
    volumes = {}
    
    for ticker in df["ticker"].tolist():
        try:
            info = yf.Ticker(ticker).info
            market_caps[ticker] = info.get("marketCap", None)
            volumes[ticker] = info.get("averageVolume", None)
        except Exception:
            market_caps[ticker] = None
            volumes[ticker] = None
    
    df = df.copy()
    df["market_cap"] = df["ticker"].map(market_caps)
    df["avg_volume"] = df["ticker"].map(volumes)
    
    before = len(df)
    # Filter micro-caps
    df = df[(df["market_cap"].isna()) | (df["market_cap"] >= min_market_cap)]
    # Filter low volume
    df = df[(df["avg_volume"].isna()) | (df["avg_volume"] >= min_volume)]
    
    logger.info(f"Filters removed {before - len(df)} tickers")
    return df.reset_index(drop=True)
