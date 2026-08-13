"""대시보드 service 단위 테스트 — fake provider 주입, 실제 I/O 없음."""

import datetime

import pandas as pd

from dashboard import service

TODAY = datetime.date(2026, 8, 7)


class FakeProvider:
    """모든 code에 대해 동일한 합성 시계열을 반환. today 행 포함."""

    def __init__(self, closes, dates=None):
        self._closes = closes
        self._dates = dates or [
            (TODAY - datetime.timedelta(days=(len(closes) - 1 - i))).isoformat()
            for i in range(len(closes))
        ]

    def get_ohlcv(self, code, start_date=None, end_date=None):
        idx = pd.to_datetime(self._dates)
        return pd.DataFrame({"Close": self._closes}, index=idx)


def _linear_series(n=25, start=100.0):
    return [start + i for i in range(n)]


def test_position_row_computes_disparity_without_quantity():
    prov = FakeProvider(_linear_series(25))  # 100..124, today=124
    pos = {"code": "005930", "name": "삼성전자", "currency": "KRW",
           "quantity": None, "avg_price": None}
    row = service.build_position_row(prov, pos, TODAY)
    assert row["current_price"] == 124.0
    # ma20: today(124) 제외 후 최근 20개(104..123) 평균
    assert row["ma20"] is not None
    assert row["disparity"] is not None
    assert row["comment"] is not None
    # 수량 없으므로 평가/손익은 None
    assert row["eval_amount"] is None
    assert row["pnl"] is None


def test_position_row_with_quantity_computes_pnl():
    prov = FakeProvider(_linear_series(25))  # today close 124
    pos = {"code": "005930", "name": "삼성전자", "currency": "KRW",
           "quantity": 10, "avg_price": 100.0}
    row = service.build_position_row(prov, pos, TODAY)
    assert row["eval_amount"] == 1240.0
    assert row["pnl"] == 240.0
    assert row["pnl_pct"] == 24.0


def test_build_state_totals_and_weight_krw_only():
    prov = FakeProvider(_linear_series(25))  # close 124 for every code
    holdings = {
        "base_currency": "KRW",
        "positions": [
            {"code": "005930", "name": "삼성전자", "currency": "KRW",
             "quantity": 10, "avg_price": 100.0, "active": True},
            {"code": "TSLA", "name": "테슬라", "currency": "USD",
             "quantity": 5, "avg_price": 50.0, "active": True},
        ],
    }
    state = service.build_state(prov, holdings=holdings, today=TODAY, now_iso="2026-08-07T09:20:00")
    # USD 종목은 KRW 총액에서 제외 → 총 평가액 = 124*10 = 1240
    assert state["totals"]["eval_amount"] == 1240.0
    assert state["totals"]["has_positions"] is True
    krw = [r for r in state["holdings"] if r["code"] == "005930"][0]
    assert krw["weight"] == 100.0  # KRW 집계 내 유일


def test_build_state_risk_alert():
    # 급락 시계열: 최근일이 크게 하락 → 이격도 <=85
    closes = [100.0] * 24 + [70.0]  # ma20~100, current 70 → 이격도 70
    prov = FakeProvider(closes)
    holdings = {"base_currency": "KRW", "positions": [
        {"code": "005930", "name": "삼성전자", "currency": "KRW",
         "quantity": None, "avg_price": None, "active": True}]}
    state = service.build_state(prov, holdings=holdings, today=TODAY, now_iso="x")
    assert len(state["risk_alerts"]) == 1
    assert state["risk_alerts"][0]["code"] == "005930"


def test_rebalance_action():
    rows = [{
        "code": "005930", "name": "삼성전자", "eval_amount": 1000.0,
        "current_price": 100.0, "target_weight": 50.0, "weight": 100.0,
    }]
    # 목표 50% of 1000 = 500, 현재 1000 → -500 → 5주 매도
    out = service.build_rebalance(rows, total_eval=1000.0)
    assert out[0]["action"] == "5주 매도"


def test_preferred_stock_borrows_common_target_code():
    prov = FakeProvider(_linear_series(25))
    pos = {"code": "005935", "name": "삼성전자우", "currency": "KRW",
           "quantity": None, "avg_price": None}
    row = service.build_position_row(prov, pos, TODAY, target_price=493542.0)
    # 우선주는 보통주(005930) 목표주가를 빌려 쓴다 → 프론트가 '005930 기준' 라벨을 붙인다
    assert row["target_code"] == "005930"
    assert row["target_code"] != row["code"]


def test_common_stock_target_code_is_self():
    prov = FakeProvider(_linear_series(25))
    pos = {"code": "005930", "name": "삼성전자", "currency": "KRW",
           "quantity": None, "avg_price": None}
    row = service.build_position_row(prov, pos, TODAY)
    assert row["target_code"] == row["code"] == "005930"


def test_us_position_routed_to_us_provider():
    krx = FakeProvider(_linear_series(25, start=100.0))   # 국내: 종가 124
    us = FakeProvider(_linear_series(25, start=300.0))    # 해외: 종가 324
    holdings = {"base_currency": "KRW", "positions": [
        {"code": "TSLA", "name": "테슬라", "currency": "USD",
         "quantity": None, "avg_price": None, "active": True}]}
    state = service.build_state(krx, holdings=holdings, today=TODAY, now_iso="x", us_provider=us)
    row = state["holdings"][0]
    assert row["current_price"] == 324.0  # 토스(krx)가 아니라 us_provider에서 옴


def test_inactive_position_excluded():
    prov = FakeProvider(_linear_series(25))
    holdings = {"base_currency": "KRW", "positions": [
        {"code": "TSLA", "name": "테슬라", "currency": "USD",
         "quantity": None, "avg_price": None, "active": False}]}
    state = service.build_state(prov, holdings=holdings, today=TODAY, now_iso="x")
    assert state["holdings"] == []
