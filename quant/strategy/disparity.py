"""이격도 기반 판단 로직 (스킬 5번·10번·17-1).

판단 기준은 quant/config/thresholds.py(85/95/110)만 사용한다. 별도 기준을 만들지 않는다.
"""

from __future__ import annotations

from quant.config.thresholds import (
    DISPARITY_BUY_MAX,
    DISPARITY_HOLD_MAX,
    DISPARITY_RISK_MAX,
)

_RISK = "급락·추세훼손·리스크 점검"
_BUY = "매수 관심"
_HOLD = "보유"
_TAKE_PROFIT = "익절 검토"


def make_opinion(disparity: float) -> str:
    """이격도 → 의견 문자열."""
    if disparity <= DISPARITY_RISK_MAX:
        return _RISK
    if disparity <= DISPARITY_BUY_MAX:
        return _BUY
    if disparity < DISPARITY_HOLD_MAX:
        return _HOLD
    return _TAKE_PROFIT


def get_badge_class(disparity: float) -> str:
    """프론트 배지 CSS 클래스 키 (색상 구분용)."""
    if disparity <= DISPARITY_RISK_MAX:
        return "risk"
    if disparity <= DISPARITY_BUY_MAX:
        return "buy"
    if disparity < DISPARITY_HOLD_MAX:
        return "hold"
    return "take-profit"


def make_comment(disparity: float) -> str:
    """보유 종목 테이블용 한 줄 코멘트 (스킬 17-1).

    예: "이격도 91.2 — 매수 관심 구간, 20일선 대비 -8.8%"
    """
    opinion = make_opinion(disparity)
    diff = disparity - 100.0
    return f"이격도 {disparity:.1f} — {opinion} 구간, 20일선 대비 {diff:+.1f}%"


def is_risk(disparity: float) -> bool:
    """리스크 구간(<=85) 여부. 대시보드 상단 경고 배너 판단에 사용 (스킬 15번·17번)."""
    return disparity <= DISPARITY_RISK_MAX
