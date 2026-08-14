"""KRX(거래소) 확정 투자자별 순매수 — pykrx 사용.

증권사(미래에셋 등)가 보는 확정 수급과 동일한 KRX 원천 데이터.
KRX 투자자/지수 데이터는 로그인이 필요하다: 환경변수 KRX_ID / KRX_PW 를
설정하면 pykrx 가 자동 로그인한다. 미설정/실패 시 호출부가 네이버로 폴백한다.

주의: KRX 는 .env(또는 GitHub Secrets)로만 관리하고 값을 로그에 출력하지 않는다.
"""

from __future__ import annotations

import datetime
import os
from typing import Optional


def krx_available() -> bool:
    return bool(os.environ.get("KRX_ID") and os.environ.get("KRX_PW"))


def get_index_investor(market: str = "KOSPI", date: Optional[str] = None) -> Optional[dict]:
    """지수 투자자별 순매수(억원). {'individual','foreign','institution','source'} 또는 None.

    market: 'KOSPI' | 'KOSDAQ'. date: 'YYYYMMDD' (기본=오늘).
    """
    if not krx_available():
        return None
    from pykrx import stock

    day = date or datetime.date.today().strftime("%Y%m%d")
    df = stock.get_market_trading_value_by_investor(day, day, market)
    if df is None or df.empty or "순매수" not in df.columns:
        return None

    def _val(*names):
        for n in names:
            if n in df.index:
                try:
                    return round(float(df.loc[n, "순매수"]) / 1e8)  # 원 → 억
                except Exception:
                    pass
        return None

    ind = _val("개인")
    if ind is None:
        return None
    return {
        "individual": ind,
        "foreign": _val("외국인", "외국인합계"),
        "institution": _val("기관합계", "기관"),
        "source": "KRX",
    }


# --------------------------------------------------------------------------- #
# ADR (등락비율) — 최근 window 거래일 상승/하락 종목수로 계산하는 시장 심리지표.
# ADR = (기간 상승종목수 합 / 기간 하락종목수 합) × 100. 120↑ 과매수, 75↓ 과매도.
# --------------------------------------------------------------------------- #
_adr_counts: dict = {}   # 'YYYYMMDD' -> (up, down)   날짜별 상승/하락 종목수 캐시
_adr_result: dict = {}   # 'YYYY-MM-DD'(오늘) -> {'value','change_pct'}  일 1회 계산


def _adr_daily_counts(day: str, market: str = "KOSPI"):
    if day in _adr_counts:
        return _adr_counts[day]
    from pykrx import stock

    df = stock.get_market_price_change_by_ticker(day, day, market=market)
    col = next((c for c in df.columns if "등락" in c), None)
    if col is None or len(df) == 0:
        raise ValueError("no change data")
    up = int((df[col] > 0).sum())
    down = int((df[col] < 0).sum())
    _adr_counts[day] = (up, down)
    return up, down


def get_kospi_adr(today: Optional[datetime.date] = None, window: int = 20,
                  market: str = "KOSPI") -> Optional[dict]:
    """코스피 ADR(등락비율, 기본 20거래일). {'value','change_pct'} 또는 None.

    KRX 로그인 필요. 날짜별 상승/하락 종목수를 캐시해 하루 1회만 신규 조회한다.
    """
    if not krx_available():
        return None
    today = today or datetime.date.today()
    rkey = today.isoformat()
    if rkey in _adr_result:
        return _adr_result[rkey]
    from pykrx import stock

    # 최근 거래일 목록(window+1개) — 코스피 지수(1001) 캘린더로 거래일만 추림
    start = (today - datetime.timedelta(days=window * 2 + 20)).strftime("%Y%m%d")
    idx = stock.get_index_ohlcv(start, today.strftime("%Y%m%d"), "1001")
    days = [d.strftime("%Y%m%d") for d in idx.index][-(window + 1):]
    if len(days) < window:
        return None

    ups, downs = [], []
    for d in days:
        try:
            u, dn = _adr_daily_counts(d, market)
        except Exception:
            continue
        ups.append(u)
        downs.append(dn)
    if len(ups) < window:
        return None

    def _adr(us, ds):
        sd = sum(ds)
        return round(sum(us) / sd * 100, 1) if sd else None

    cur = _adr(ups[-window:], downs[-window:])
    prev = _adr(ups[-window - 1:-1], downs[-window - 1:-1]) if len(ups) > window else None
    chg = None
    if cur is not None and prev:
        chg = (cur - prev) / prev * 100.0
    result = {"value": cur, "change_pct": chg}
    _adr_result[rkey] = result
    return result
