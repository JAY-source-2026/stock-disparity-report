"""데이터 소스 추상화 (스킬 2번·16번).

전략·지표·대시보드 코드는 데이터 소스를 몰라야 한다. 모든 소스(토스 라이브 API,
FinanceDataReader 임시 구현, 과거 캐시)는 이 DataProvider 인터페이스 뒤에 둔다.

토스 키 확보 후에는 quant/data/toss.py 가 이 인터페이스를 동일 시그니처로 구현하고,
fdr_provider.FinanceDataReaderProvider 를 대체한다. (스킬 12번 Step T-1)
"""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod
from typing import List, Optional


class DataProvider(ABC):
    """시세 조회 전용 인터페이스. 주문·계좌 기능은 절대 포함하지 않는다 (스킬 3번 금지선)."""

    @abstractmethod
    def get_ohlcv(self, code: str, start_date, end_date):
        """[code]의 일봉 DataFrame을 반환.

        반환: pandas.DataFrame
          - index: 거래일 (DatetimeIndex, 오름차순)
          - columns: 최소 'Close' 포함 (가능하면 Open/High/Low/Close/Volume)
        """
        raise NotImplementedError

    @abstractmethod
    def get_current_price(self, code: str) -> float:
        """[code]의 가장 최근 체결가(근사). 토스 전환 시 실시간 현재가로 대체된다."""
        raise NotImplementedError

    @abstractmethod
    def get_daily_prices(
        self,
        code: str,
        count: int = 20,
        today: Optional[datetime.date] = None,
    ) -> List[float]:
        """확정된 최근 [count] 거래일 종가 리스트 (오래된→최신 순).

        스킬 10번: 당일(today) 행은 20일 이동평균선 계산에서 제외해야 하므로,
        이 메서드는 today 이후(당일 포함) 행을 제거한 뒤 최근 count개를 돌려준다.
        today=None이면 시스템 오늘 날짜를 사용한다(테스트에서는 명시적으로 주입).
        """
        raise NotImplementedError
