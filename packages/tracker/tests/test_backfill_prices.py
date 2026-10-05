"""Telling a throttled price download apart from a symbol with no data."""

import pandas as pd

from scripts import backfill_prices


def test_throttled_symbols_are_asked_for_again_and_never_called_no_data(monkeypatch):
    calls = []

    def fake_download(tickers, **kwargs):
        calls.append(list(tickers))
        log = backfill_prices.logging.getLogger("yfinance")
        if len(calls) == 1:
            log.error("['NYT', 'OLDCO']: YFRateLimitError('Too Many Requests. Rate limited.')")
            return pd.DataFrame()
        if len(calls) == 2:
            log.error("['OLDCO']: YFRateLimitError('Too Many Requests. Rate limited.')")
        index = pd.to_datetime(["2026-10-01"])
        return pd.concat({"Close": pd.DataFrame({"NYT": [50.0]}, index=index)}, axis=1)

    monkeypatch.setattr(backfill_prices.yf, "download", fake_download)
    monkeypatch.setattr(backfill_prices.time, "sleep", lambda s: None)
    monkeypatch.setattr(backfill_prices, "THROTTLE_WAITS", (1,))
    monkeypatch.setattr(backfill_prices, "throttled_symbols", set())

    results = backfill_prices.fetch_prices_batch(["NYT", "OLDCO"], "2026-09-01", "2026-10-03")

    assert calls == [["NYT", "OLDCO"], ["NYT", "OLDCO"]]
    assert list(results) == ["NYT"]
    assert backfill_prices.throttled_symbols == {"OLDCO"}
