"""토스 클라이언트 단위 테스트 — request_with_retry 를 monkeypatch, 실호출 없음.

스킬 7·9번: 토큰 캐시는 시간(now) 주입으로 검증하고, 실제 토스 API 는 호출하지 않는다.
"""

import datetime

import pytest

from quant.data import toss


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _clear_token():
    toss.reset_token_cache()
    yield
    toss.reset_token_cache()


def test_token_issued_and_cached(monkeypatch):
    calls = {"n": 0}

    def fake_req(method, url, **kwargs):
        calls["n"] += 1
        assert url.endswith("/oauth2/token")
        assert kwargs["data"] == {"grant_type": "client_credentials"}
        # Basic 인증 헤더 존재 (값 검증은 하지 않음)
        assert kwargs["headers"]["Authorization"].startswith("Basic ")
        return FakeResp({"access_token": "TICKET1", "expires_in": 3600})

    monkeypatch.setattr(toss, "request_with_retry", fake_req)
    t0 = datetime.datetime(2026, 8, 11, 9, 0, 0)
    tok1 = toss.get_access_token("id", "secret", now=t0)
    tok2 = toss.get_access_token("id", "secret", now=t0 + datetime.timedelta(minutes=30))
    assert tok1 == tok2 == "TICKET1"
    assert calls["n"] == 1  # 캐시 재사용 → 재발급 없음


def test_token_refreshed_after_expiry(monkeypatch):
    calls = {"n": 0}

    def fake_req(method, url, **kwargs):
        calls["n"] += 1
        return FakeResp({"access_token": f"TICKET{calls['n']}", "expires_in": 3600})

    monkeypatch.setattr(toss, "request_with_retry", fake_req)
    t0 = datetime.datetime(2026, 8, 11, 9, 0, 0)
    tok1 = toss.get_access_token("id", "secret", now=t0)
    # 만료 이후 → 재발급
    tok2 = toss.get_access_token("id", "secret", now=t0 + datetime.timedelta(hours=2))
    assert tok1 == "TICKET1"
    assert tok2 == "TICKET2"
    assert calls["n"] == 2


def test_missing_credentials_raises_without_leaking(monkeypatch):
    monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
    monkeypatch.delenv("TOSS_CLIENT_SECRET", raising=False)
    with pytest.raises(RuntimeError) as exc:
        toss.get_access_token(now=datetime.datetime(2026, 8, 11))
    # 에러 메시지에 시크릿 값이 없어야 한다
    assert "secret" not in str(exc.value).lower() or "TOSS_CLIENT_SECRET" in str(exc.value)


def _candle(date_str, close, **extra):
    row = {"timestamp": f"{date_str}T00:00:00.000+09:00", "closePrice": str(close)}
    row.update({k: str(v) for k, v in extra.items()})
    return row


def test_parse_price_picks_symbol_from_result_list():
    # 실제 응답: result 는 배열, lastPrice 는 문자열
    payload = {"result": [
        {"symbol": "000660", "lastPrice": "180000", "currency": "KRW"},
        {"symbol": "005930", "lastPrice": "71000", "currency": "KRW"},
    ]}
    assert toss._parse_price(payload, symbol="005930") == 71000.0
    assert toss._parse_price(payload) == 180000.0  # symbol 미지정 → 첫 항목


def test_parse_candles_sorted_and_typed():
    # 실제 응답: result.candles, 최신순 → 파서가 오름차순 정렬
    payload = {"result": {"candles": [
        _candle("2026-08-07", 103, openPrice=100),
        _candle("2026-08-05", 101),
        _candle("2026-08-06", 102),
    ]}}
    rows = toss._parse_candles(payload)
    assert [r["date"].isoformat() for r in rows] == ["2026-08-05", "2026-08-06", "2026-08-07"]
    assert rows[-1]["close"] == 103.0
    assert rows[-1]["open"] == 100.0


def test_get_daily_prices_excludes_today(monkeypatch):
    monkeypatch.setattr(toss, "get_access_token", lambda **k: "TICKET")
    dates = [datetime.date(2026, 7, 1) + datetime.timedelta(days=i) for i in range(25)]
    payload = {"result": {"candles": [
        _candle(d.isoformat(), float(i + 1)) for i, d in enumerate(dates)
    ]}}
    monkeypatch.setattr(toss, "request_with_retry", lambda *a, **k: FakeResp(payload))
    prices = toss.get_daily_prices("005930", count=20, today=dates[-1])  # 마지막 날 = today
    assert prices[-1] == 24.0  # 25번째(today) 제외
    assert 25.0 not in prices
    assert len(prices) == 20


def test_get_current_price_from_prices_endpoint(monkeypatch):
    monkeypatch.setattr(toss, "get_access_token", lambda **k: "TICKET")
    payload = {"result": [{"symbol": "005930", "lastPrice": "71000", "currency": "KRW"}]}
    monkeypatch.setattr(toss, "request_with_retry", lambda *a, **k: FakeResp(payload))
    assert toss.get_current_price("005930") == 71000.0


def test_fetch_candles_passes_count(monkeypatch):
    monkeypatch.setattr(toss, "get_access_token", lambda **k: "TICKET")
    seen = {}

    def fake_req(method, url, **kwargs):
        seen.update(kwargs.get("params", {}))
        return FakeResp({"result": {"candles": []}})

    monkeypatch.setattr(toss, "request_with_retry", fake_req)
    toss._fetch_candles("005930", count=200)
    assert seen.get("count") == 200
    # 상한(200) 초과 시 클램프
    toss._fetch_candles("005930", count=999)
    assert seen.get("count") == toss.CANDLE_COUNT_MAX


def test_toss_provider_get_ohlcv(monkeypatch):
    monkeypatch.setattr(toss, "get_access_token", lambda **k: "TICKET")
    payload = {"result": {"candles": [
        _candle("2026-08-05", 101, openPrice=100, highPrice=102, lowPrice=99, volume=10),
        _candle("2026-08-06", 102, openPrice=101, highPrice=103, lowPrice=100, volume=11),
    ]}}
    monkeypatch.setattr(toss, "request_with_retry", lambda *a, **k: FakeResp(payload))
    prov = toss.TossProvider()
    df = prov.get_ohlcv("005930", "2026-08-01", "2026-08-07")
    assert list(df["Close"]) == [101.0, 102.0]
    assert "Open" in df.columns
