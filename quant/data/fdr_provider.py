"""임시 DataProvider 구현: FinanceDataReader.

★ 임시(temporary) ★
토스 Open API 키 대기 중이라, 스킬 12번 Step T-1 뼈대를 먼저 세우기 위해
FinanceDataReader를 데이터 소스로 쓴다. 키 확보 후 quant/data/toss.py 가
동일한 DataProvider 인터페이스를 구현하면 이 파일을 그대로 교체한다.

- 이 파일은 시세 조회 전용이다. 주문/계좌 기능 없음 (스킬 3번 금지선).
- FinanceDataReader는 스킬 17-2가 명시적으로 허용한 보완 소스(지수·환율 등)이며,
  뼈대 단계에서는 개별 종목 시세도 여기서 가져온다.
- 실제 네트워크 호출은 대시보드 실구동 시에만 발생한다. 단위 테스트에서는
  reader를 monkeypatch(주입)하여 실제 I/O 없이 검증한다 (스킬 9번).
"""

from __future__ import annotations

import datetime
from typing import Callable, List, Optional

from quant.data.provider import DataProvider


def _default_reader():
    # import를 함수 안에 두어, 테스트가 reader를 주입하는 경우
    # FinanceDataReader 미설치 환경에서도 import 에러가 나지 않게 한다.
    import FinanceDataReader as fdr

    return fdr.DataReader


class FinanceDataReaderProvider(DataProvider):
    def __init__(self, reader: Optional[Callable] = None):
        """reader: DataReader(code, start=None, end=None) -> DataFrame.

        기본값은 FinanceDataReader.DataReader. 테스트에서는 fake reader를 주입한다.
        """
        self._reader = reader if reader is not None else _default_reader()

    # ---- DataProvider 구현 ---------------------------------------------------
    def get_ohlcv(self, code: str, start_date=None, end_date=None):
        return self._reader(code, start_date, end_date)

    def get_current_price(self, code: str) -> float:
        df = self._reader(code, None, None)
        close = df["Close"].dropna()
        return float(close.iloc[-1])

    def get_daily_prices(
        self,
        code: str,
        count: int = 20,
        today: Optional[datetime.date] = None,
    ) -> List[float]:
        df = self._reader(code, None, None)
        close = df["Close"].dropna()
        if today is None:
            today = datetime.date.today()
        kept = [
            float(value)
            for ts, value in close.items()
            if _to_date(ts) < today
        ]
        return kept[-count:]


def _to_date(ts) -> datetime.date:
    """DatetimeIndex 항목/문자열을 date로 변환."""
    if isinstance(ts, datetime.datetime):
        return ts.date()
    if isinstance(ts, datetime.date):
        return ts
    # pandas.Timestamp 는 .date() 를 가진다.
    to_date = getattr(ts, "date", None)
    if callable(to_date):
        return to_date()
    return datetime.date.fromisoformat(str(ts)[:10])
