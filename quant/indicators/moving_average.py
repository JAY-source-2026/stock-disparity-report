"""이동평균·이격도·목표주가 괴리율 계산 (스킬 5번·10번).

이 로직은 메일 리포트(전환 대상)와 대시보드가 공유한다. 대시보드용으로
중복 구현하지 않는다.
"""

from __future__ import annotations

from typing import List, Optional

MA_WINDOW = 20


def calculate_ma20(closes: List[float]) -> float:
    """확정된 최근 20거래일 종가 평균 (당일 종가는 포함하지 않는다 — 스킬 10번).

    closes: 오래된→최신 순의 확정 종가 리스트. 당일 행은 이미 제외되어 있어야 한다
    (DataProvider.get_daily_prices 가 제외 처리).
    """
    if len(closes) < MA_WINDOW:
        raise ValueError(
            f"20일 이동평균에는 확정 종가 {MA_WINDOW}개가 필요하다 (받은 개수: {len(closes)})"
        )
    window = closes[-MA_WINDOW:]
    return sum(window) / MA_WINDOW


def calculate_disparity(current_price: float, ma20: float) -> float:
    """이격도 = 당일 현재가 ÷ 전일 기준 20일 이동평균선 × 100.

    당일 현재가는 분자에만 쓰고, ma20(분모) 계산에는 포함하지 않는다.
    """
    if ma20 == 0:
        raise ValueError("ma20 이 0이라 이격도를 계산할 수 없다")
    return current_price / ma20 * 100.0


def calculate_target_ratio(
    current_price: float, target_price: Optional[float]
) -> Optional[float]:
    """목표주가 컨센서스 괴리율(상승여력) = (목표주가 − 현재가) ÷ 현재가 × 100.

    target_price 가 없으면(네이버 컨센서스 미연동 등) None 을 반환한다.
    """
    if target_price is None or current_price == 0:
        return None
    return (target_price - current_price) / current_price * 100.0
