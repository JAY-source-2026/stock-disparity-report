"""FDR Provider 단위 테스트 — reader를 주입(monkeypatch)하여 실제 네트워크 없이 검증."""

import datetime

import pandas as pd

from quant.data.fdr_provider import FinanceDataReaderProvider


def _fake_df(dates, closes):
    idx = pd.to_datetime(dates)
    return pd.DataFrame({"Close": closes}, index=idx)


def make_provider(dates, closes):
    def fake_reader(code, start=None, end=None):
        return _fake_df(dates, closes)

    return FinanceDataReaderProvider(reader=fake_reader)


def test_get_current_price_returns_last_close():
    p = make_provider(["2026-08-05", "2026-08-06", "2026-08-07"], [100, 101, 102])
    assert p.get_current_price("005930") == 102.0


def test_get_daily_prices_excludes_today():
    dates = [f"2026-07-{d:02d}" for d in range(1, 26)]  # 25 거래일
    closes = list(range(1, 26))
    p = make_provider(dates, closes)
    # today = 2026-07-25 → 마지막 행(값 25) 제외되어야 함
    prices = p.get_daily_prices("005930", count=20, today=datetime.date(2026, 7, 25))
    assert 25.0 not in prices
    assert prices[-1] == 24.0
    assert len(prices) == 20


def test_get_daily_prices_count_limit():
    dates = [f"2026-07-{d:02d}" for d in range(1, 11)]
    closes = list(range(1, 11))
    p = make_provider(dates, closes)
    prices = p.get_daily_prices("005930", count=5, today=datetime.date(2026, 8, 1))
    assert prices == [6.0, 7.0, 8.0, 9.0, 10.0]


def test_get_ohlcv_passthrough():
    p = make_provider(["2026-08-06", "2026-08-07"], [10, 11])
    df = p.get_ohlcv("005930", "2026-08-01", "2026-08-07")
    assert list(df["Close"]) == [10, 11]
