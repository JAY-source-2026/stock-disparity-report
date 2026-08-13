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
