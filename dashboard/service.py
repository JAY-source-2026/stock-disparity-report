"""대시보드 상태 조립 (스킬 17번).

모든 화면 데이터는 백엔드가 JSON으로 만들고 프론트는 렌더링만 한다.
이 모듈은 DataProvider 인터페이스만 사용하므로 데이터 소스(FDR/토스)를 모른다.

수량(quantity)/평단(avg_price)이 아직 없는(null) 종목도 현재가·MA20·이격도·코멘트는
계산해 보여준다. 평가액/손익/비중 등 수량 의존 컬럼만 None으로 둔다.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Callable, List, Optional

from quant.config.universe import get_target_code
from quant.indicators.moving_average import (
    MA_WINDOW,
    calculate_disparity,
    calculate_ma20,
    calculate_target_ratio,
)
from quant.strategy.disparity import (
    get_badge_class,
    is_risk,
    make_comment,
    make_opinion,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

# 국내 지수·환율(매크로 스트립, 스킬 17-2)과 하단 참고 지표(스킬 17번 하단 티커).
# FinanceDataReader 심볼.
MACRO_SYMBOLS = [
    {"key": "KS11", "label": "KOSPI"},
    {"key": "KQ11", "label": "KOSDAQ"},
]
# 상단 3번째 박스: 미국 지수 3종
US_INDEX_SYMBOLS = [
    {"key": "US500", "label": "S&P500"},
    {"key": "IXIC", "label": "나스닥"},
    {"key": "DJI", "label": "다우"},
]
# 명일 지표 박스: 국장 다음날 예측용 (코스피 + 미 선물=애프터마켓 반영)
NEXTDAY_SYMBOLS = [
    {"key": "FUT", "label": "코스피200 선물", "source": "naver"},
    {"key": "ES=F", "label": "S&P500 선물"},
    {"key": "NQ=F", "label": "나스닥 선물"},
]
# 리스크 배너 아래 지표 박스 (순서 고정)
INDEX_SYMBOLS = [
    {"key": "USD/KRW", "label": "원-달러 환율", "is_fx": True},
    {"key": "JPY/KRW", "label": "원-엔 환율(100엔)", "is_fx": True, "scale": 100},
    {"key": "DX-Y.NYB", "label": "달러인덱스"},
    {"key": "CL=F", "label": "유가(WTI)"},
    {"key": "GC=F", "label": "금"},
    {"key": "^TNX", "label": "미10년물(%)"},
    {"key": "N225", "label": "닛케이225"},        # 일본
    {"key": "HSI", "label": "항셍"},              # 홍콩
    {"key": "SSEC", "label": "상해종합"},          # 중국
    {"key": "^STOXX50E", "label": "유로스톡스50"},  # 유럽
    {"key": "BTC/USD", "label": "비트코인($)"},     # 비트코인 (미국 달러)
    {"key": "VIX", "label": "VIX(공포지수)"},        # 미국 변동성지수
    {"key": "ADR", "label": "ADR(등락비율)", "source": "adr"},  # 코스피 등락비율 심리지표
]


# --------------------------------------------------------------------------- #
# 파일 로더
# --------------------------------------------------------------------------- #
def load_holdings(path: Optional[str] = None) -> dict:
    path = path or os.path.join(_ROOT, "holdings.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def load_events() -> list:
    return _load_json(os.path.join(_HERE, "config", "events.json"), [])


def _nth_weekday(year, month, weekday, n):
    """해당 월의 n번째 요일(weekday: 월0..일6) 날짜."""
    first = datetime.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + datetime.timedelta(days=offset + 7 * (n - 1))


def expiry_days(today, count=6):
    """만기일 = 매월 둘째 목요일(옵션 만기). 3·6·9·12월은 선물옵션 동시만기(네 마녀).

    반환: [(date, is_quarterly)...] 오늘 이후.
    """
    out = []
    y, m = today.year, today.month
    for _ in range(15):  # 약 15개월 앞까지
        d = _nth_weekday(y, m, 3, 2)  # 목요일(3)의 두 번째
        if d >= today:
            out.append((d, m in (3, 6, 9, 12)))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out[:count]


def merged_events(today, limit=10, extra=None):
    """수동(events.json) + 자동(동시만기) + extra(FOMC 등)를 합쳐 미래 순으로 정렬."""
    manual = load_events()
    auto = [{"date": d.isoformat(),
             "title": "선물옵션 동시만기일" if q else "옵션 만기일", "type": "expiry"}
            for d, q in expiry_days(today)]
    seen, out = set(), []
    for e in sorted(manual + auto + (extra or []), key=lambda x: x.get("date", "")):
        d = e.get("date", "")
        if d < today.isoformat():
            continue
        key = (d, e.get("title"))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out[:limit]


def load_memo() -> dict:
    return _load_json(
        os.path.join(_HERE, "config", "memo.json"), {"title": "오늘 할 일", "items": []}
    )


def load_card(code: str) -> Optional[dict]:
    return _load_json(os.path.join(_HERE, "cards", f"{code}.json"), None)


# --------------------------------------------------------------------------- #
# 종목 한 줄 계산
# --------------------------------------------------------------------------- #
def _series_from_ohlcv(df):
    """DataFrame → (dates, closes) 오름차순. 결측 종가는 버린다."""
    close = df["Close"].dropna()
    dates, closes = [], []
    for ts, value in close.items():
        d = ts.date() if hasattr(ts, "date") else datetime.date.fromisoformat(str(ts)[:10])
        dates.append(d)
        closes.append(float(value))
    return dates, closes


def build_position_row(
    provider,
    position: dict,
    today: datetime.date,
    lookback_days: int = 60,
    target_price: Optional[float] = None,
) -> dict:
    """보유 종목 한 줄. 데이터 조회 실패 시 error 필드를 담아 반환(전체 실패 방지)."""
    code = position["code"]
    row = {
        "code": code,
        "name": position.get("name", code),
        "market": position.get("market", "KRX"),
        "currency": position.get("currency", "KRW"),
        "quantity": position.get("quantity"),
        "avg_price": position.get("avg_price"),
        "target_weight": position.get("target_weight"),
        "owned": bool(position.get("owned", False)),
        "current_price": None,
        "change_pct": None,
        "ma20": None,
        "disparity": None,
        "opinion": None,
        "badge": None,
        "comment": None,
        "target_price": target_price,
        "target_code": get_target_code(code),  # 목표주가 출처 코드(우선주는 보통주 코드)
        "target_ratio": None,
        "eval_amount": None,
        "pnl": None,
        "pnl_pct": None,
        "weight": None,
        "error": None,
    }
    try:
        start = today - datetime.timedelta(days=lookback_days)
        df = provider.get_ohlcv(code, start, today)
        dates, closes = _series_from_ohlcv(df)
        if not closes:
            row["error"] = "no_data"
            return row

        current_price = closes[-1]
        row["current_price"] = current_price
        if len(closes) >= 2:
            prev = closes[-2]
            if prev:
                row["change_pct"] = (current_price - prev) / prev * 100.0

        # MA20: 당일(today) 행 제외 후 최근 20 확정 종가 (스킬 10번)
        confirmed = [c for d, c in zip(dates, closes) if d < today]
        if len(confirmed) >= MA_WINDOW:
            ma20 = calculate_ma20(confirmed)
            disparity = calculate_disparity(current_price, ma20)
            row["ma20"] = ma20
            row["disparity"] = disparity
            row["opinion"] = make_opinion(disparity)
            row["badge"] = get_badge_class(disparity)
            row["comment"] = make_comment(disparity)
        else:
            row["comment"] = f"확정 종가 부족 ({len(confirmed)}/{MA_WINDOW}) — 이격도 대기"

        row["target_ratio"] = calculate_target_ratio(current_price, target_price)

        # 수량·평단이 있을 때만 평가/손익 계산
        qty, avg = position.get("quantity"), position.get("avg_price")
        if qty is not None and avg is not None:
            row["eval_amount"] = current_price * qty
            row["pnl"] = (current_price - avg) * qty
            if avg:
                row["pnl_pct"] = (current_price - avg) / avg * 100.0
    except Exception as exc:  # 개별 종목 실패가 대시보드 전체를 막지 않게 한다
        row["error"] = str(exc)
    return row


# --------------------------------------------------------------------------- #
# 매크로 / 지수
# --------------------------------------------------------------------------- #
def build_market_row(provider, symbol: dict, today: datetime.date, spark_len: int = 20) -> dict:
    row = {
        "key": symbol["key"],
        "label": symbol["label"],
        "value": None,
        "change_pct": None,
        "spark": [],
        "error": None,
    }
    try:
        start = today - datetime.timedelta(days=spark_len * 2 + 10)
        df = provider.get_ohlcv(symbol["key"], start, today)
        _, closes = _series_from_ohlcv(df)
        if not closes:
            row["error"] = "no_data"
            return row
        scale = symbol.get("scale", 1)
        row["value"] = closes[-1] * scale
        if len(closes) >= 2 and closes[-2]:
            row["change_pct"] = (closes[-1] - closes[-2]) / closes[-2] * 100.0
        row["spark"] = [c * scale for c in closes[-spark_len:]]
    except Exception as exc:
        row["error"] = str(exc)
    return row


def _build_index_or_naver(provider, symbol: dict, today: datetime.date) -> dict:
    """source=='naver' 지표는 네이버 지수페이지에서, 그 외는 provider(FDR)에서 조회."""
    if symbol.get("source") == "naver":
        row = {"key": symbol["key"], "label": symbol["label"], "value": None,
               "change_pct": None, "spark": [], "error": None}
        try:
            from quant.data.naver import get_index_quote

            q = get_index_quote(symbol["key"])
            if q:
                row["value"] = q["value"]
                row["change_pct"] = q["change_pct"]
            else:
                row["error"] = "no_data"
        except Exception as exc:
            row["error"] = str(exc)
        return row
    if symbol.get("source") == "adr":
        row = {"key": symbol["key"], "label": symbol["label"], "value": None,
               "change_pct": None, "spark": [], "error": None}
        try:
            from quant.data.krx import get_kospi_adr

            q = get_kospi_adr(today)
            if q and q.get("value") is not None:
                row["value"] = q["value"]
                row["change_pct"] = q.get("change_pct")
            # 값 없으면(집계 전/KRX 일시장애) error 대신 값만 비워 "-"로 표시
        except Exception:
            pass
        return row
    return build_market_row(provider, symbol, today)


# --------------------------------------------------------------------------- #
# 전체 상태
# --------------------------------------------------------------------------- #
def build_state(
    provider,
    holdings: Optional[dict] = None,
    today: Optional[datetime.date] = None,
    now_iso: Optional[str] = None,
    target_prices: Optional[dict] = None,
    macro_provider=None,
    us_provider=None,
) -> dict:
    """대시보드 전체 상태 dict.

    provider: 국내(KRW) 보유 종목 시세 소스(토스). macro_provider: 지수·환율 소스.
      토스에 지수·환율이 없으면 FinanceDataReader로 보완한다(스킬 17-2).
      macro_provider 미지정 시 provider 를 그대로 사용한다.
    us_provider: 해외(USD) 종목 시세 소스. 토스는 국내 전용이라 미국 종목은
      FinanceDataReader로 조회한다. 미지정 시 macro_provider 를 사용한다.
    today/now_iso 는 테스트에서 주입 가능(미주입 시 시스템 시각 사용).
    target_prices: {code: 목표주가} — 네이버 컨센서스 미연동 단계에서는 비어 있음(None).
    """
    if today is None:
        today = datetime.date.today()
    if now_iso is None:
        now_iso = datetime.datetime.now().isoformat(timespec="seconds")
    holdings = holdings if holdings is not None else load_holdings()
    target_prices = target_prices or {}
    macro_provider = macro_provider or provider
    us_provider = us_provider or macro_provider

    base_currency = holdings.get("base_currency", "KRW")
    positions = [p for p in holdings.get("positions", []) if p.get("active", True)]

    rows: List[dict] = []
    for pos in positions:
        tcode = get_target_code(pos["code"])
        # 해외(USD) 종목은 토스가 아니라 FinanceDataReader(us_provider)로 조회
        prov = us_provider if pos.get("currency") == "USD" else provider
        rows.append(
            build_position_row(
                prov, pos, today, target_price=target_prices.get(tcode)
            )
        )

    # 총 평가액/손익: base_currency 포지션만 집계 (USD 등 이종통화 제외 — v1)
    eval_rows = [
        r for r in rows if r["eval_amount"] is not None and r["currency"] == base_currency
    ]
    total_eval = sum(r["eval_amount"] for r in eval_rows)
    total_cost = sum(
        r["avg_price"] * r["quantity"] for r in eval_rows
    )
    total_pnl = total_eval - total_cost if eval_rows else None
    total_pnl_pct = (total_pnl / total_cost * 100.0) if total_cost else None

    # 포트폴리오 비중
    for r in eval_rows:
        r["weight"] = (r["eval_amount"] / total_eval * 100.0) if total_eval else None

    rebalance = build_rebalance(eval_rows, total_eval)
    risk_alerts = [
        {"code": r["code"], "name": r["name"], "disparity": r["disparity"]}
        for r in rows
        if r["disparity"] is not None and is_risk(r["disparity"])
    ]

    return {
        "generated_at": now_iso,
        "base_currency": base_currency,
        "holdings": rows,
        "totals": {
            "eval_amount": total_eval if eval_rows else None,
            "pnl": total_pnl,
            "pnl_pct": total_pnl_pct,
            "has_positions": bool(eval_rows),
        },
        "macro": [build_market_row(macro_provider, s, today) for s in MACRO_SYMBOLS],
        "us_index": [build_market_row(macro_provider, s, today) for s in US_INDEX_SYMBOLS],
        "indices": [_build_index_or_naver(macro_provider, s, today) for s in INDEX_SYMBOLS],
        "nextday": [_build_index_or_naver(macro_provider, s, today) for s in NEXTDAY_SYMBOLS],
        "events": merged_events(today),
        "memo": load_memo(),
        "rebalance": rebalance,
        "risk_alerts": risk_alerts,
        "notice": "보유 종목 시세=토스 Open API(/api/v1/prices·/api/v1/candles), 지수·환율=FinanceDataReader 보완.",
    }


def build_rebalance(eval_rows: List[dict], total_eval: float) -> List[dict]:
    """리밸런싱 계산기 (스킬 17-6). 계산·표시까지만. 주문 실행은 구현하지 않는다.

    최소 거래 단위(1주) 미만 차이는 '조정 불필요'로 표시한다.
    """
    result = []
    for r in eval_rows:
        tw = r.get("target_weight")
        if tw is None or not total_eval or not r.get("current_price"):
            continue
        target_value = total_eval * (tw / 100.0)
        diff_value = target_value - r["eval_amount"]
        shares = diff_value / r["current_price"]
        whole = int(shares) if shares >= 0 else -int(-shares)  # 버림(절대값)
        if abs(shares) < 1:
            action = "조정 불필요"
        elif whole > 0:
            action = f"{whole}주 매수"
        else:
            action = f"{abs(whole)}주 매도"
        result.append(
            {
                "code": r["code"],
                "name": r["name"],
                "current_weight": r["weight"],
                "target_weight": tw,
                "action": action,
            }
        )
    return result
