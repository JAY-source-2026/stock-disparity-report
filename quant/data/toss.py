"""토스증권 Open API 클라이언트 (시세 조회 전용).

스킬 3번 금지선: Order(주문)·Account(잔고) 엔드포인트는 절대 구현하지 않는다.
스킬 6번: client_id/secret 등 시크릿 값은 print/log/에러메시지에 절대 노출하지 않는다.
references/toss-api.md 를 먼저 읽고 작성했다.

★ 확정 필요 ★  토스 Open API 는 2026년 단계적 오픈 중이라, 아래 CONFIG 블록의
엔드포인트 경로·파라미터·응답 필드명은 반드시 공식 콘솔(OpenAPI JSON)로 확정한다.
확정 값이 이 파일 기본값과 다르면 CONFIG 블록과 _parse_* 만 수정하면 되도록 격리했다.
"""

from __future__ import annotations

import base64
import datetime
import os
from typing import List, Optional

from quant.core.http import request_with_retry
from quant.data.provider import DataProvider

# ======================= CONFIG (2026-08-11 실제 응답으로 확정) ===============
BASE_URL = "https://openapi.tossinvest.com"
TOKEN_PATH = "/oauth2/token"          # OAuth2 Client Credentials (Basic 인증)

# 현재가: GET /api/v1/prices?symbols=005930  (symbols 는 콤마로 다종목 가능)
#   응답: {"result":[{"symbol","timestamp","lastPrice"(str),"currency"}, ...]}
PRICE_PATH = "/api/v1/prices"
PRICE_SYMBOLS_PARAM = "symbols"

# 일봉: GET /api/v1/candles?symbol=005930&interval=1d  (interval 허용값: "1m","1d")
#   응답: {"result":{"candles":[{"timestamp","openPrice","highPrice","lowPrice",
#          "closePrice","volume","currency"(모두 str)}, ...]}}  — 최신순(내림차순)
#   ※ 당일봉이 포함되므로 MA20 계산에서 제외한다 (스킬 10번).
CANDLE_PATH = "/api/v1/candles"
CANDLE_SYMBOL_PARAM = "symbol"
CANDLE_INTERVAL_PARAM = "interval"
CANDLE_INTERVAL_DAILY = "1d"
CANDLE_COUNT_PARAM = "count"     # 선택. 허용범위 1~200 (기본 100)
CANDLE_COUNT_MAX = 200
# =============================================================================

_TOKEN_REFRESH_MARGIN = datetime.timedelta(seconds=60)  # 만료 임박 판단 여유
# ============================================================================

_TOKEN_CACHE = {"token": None, "expires_at": None}


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def reset_token_cache() -> None:
    """테스트/재인증용. 캐시된 토큰을 비운다."""
    _TOKEN_CACHE["token"] = None
    _TOKEN_CACHE["expires_at"] = None


# --------------------------------------------------------------------------- #
# 토큰 (스킬 7번: 캐시 재사용, 요청마다 재발급 금지)
# --------------------------------------------------------------------------- #
def get_access_token(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    now: Optional[datetime.datetime] = None,
    force: bool = False,
) -> str:
    """Bearer 토큰을 반환. 만료 전이면 캐시 재사용, 만료 임박/강제 시에만 재발급.

    now 는 테스트에서 주입(clock). 시크릿 값은 절대 로깅/노출하지 않는다.
    """
    now = now or _now()
    if (
        not force
        and _TOKEN_CACHE["token"]
        and _TOKEN_CACHE["expires_at"]
        and now < _TOKEN_CACHE["expires_at"] - _TOKEN_REFRESH_MARGIN
    ):
        return _TOKEN_CACHE["token"]

    client_id = client_id or os.environ.get("TOSS_CLIENT_ID")
    client_secret = client_secret or os.environ.get("TOSS_CLIENT_SECRET")
    if not client_id or not client_secret:
        # 값은 절대 담지 않는다 (스킬 6번)
        raise RuntimeError("TOSS_CLIENT_ID/TOSS_CLIENT_SECRET 미설정 — .env 를 확인하세요.")

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = request_with_retry(
        "POST",
        BASE_URL + TOKEN_PATH,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
    )
    payload = resp.json()
    token = payload["access_token"]
    expires_in = int(payload.get("expires_in", 3600))
    _TOKEN_CACHE["token"] = token
    _TOKEN_CACHE["expires_at"] = now + datetime.timedelta(seconds=expires_in)
    return token


def _auth_headers(now: Optional[datetime.datetime] = None) -> dict:
    return {"Authorization": f"Bearer {get_access_token(now=now)}"}


def _require_ok(resp):
    """비정상 응답이면 에러 코드만 담아 예외. 응답 본문의 시크릿은 담지 않는다."""
    if getattr(resp, "status_code", 200) != 200:
        code = ""
        try:
            code = (resp.json().get("error") or {}).get("code", "")
        except Exception:
            pass
        raise RuntimeError(f"토스 응답 {resp.status_code} {code}".strip())
    return resp


def _request_auth(method: str, url: str, now: Optional[datetime.datetime] = None, **kwargs):
    """인증 요청 — 401(토큰 무효/만료) 시 토큰 재발급 후 1회 재시도(자가복구)."""
    resp = request_with_retry(method, url, headers=_auth_headers(now), **kwargs)
    if getattr(resp, "status_code", 200) == 401:
        reset_token_cache()
        resp = request_with_retry(method, url, headers=_auth_headers(now), **kwargs)
    return resp


# --------------------------------------------------------------------------- #
# 응답 파서
# --------------------------------------------------------------------------- #
def _parse_price(payload, symbol: Optional[str] = None) -> float:
    """현재가 응답 → float. result 는 종목 배열, 각 항목의 lastPrice(문자열)를 쓴다."""
    res = payload.get("result", payload) if isinstance(payload, dict) else payload
    items = res if isinstance(res, list) else [res]
    item = None
    if symbol is not None:
        item = next((x for x in items if str(x.get("symbol")) == str(symbol)), None)
    item = item or (items[0] if items else None)
    if not isinstance(item, dict) or item.get("lastPrice") is None:
        raise KeyError("현재가(lastPrice) 필드를 찾지 못함")
    return float(item["lastPrice"])


def _parse_candles(payload) -> List[dict]:
    """일봉 응답 → [{'date','close','open','high','low','volume'}...] 오름차순 정렬."""
    res = payload.get("result", payload) if isinstance(payload, dict) else payload
    rows_raw = res.get("candles", res) if isinstance(res, dict) else res
    out = []
    for r in rows_raw:
        ts = r.get("timestamp")
        if ts is None or r.get("closePrice") is None:
            continue
        d = datetime.date.fromisoformat(str(ts)[:10])
        row = {"date": d, "close": float(r["closePrice"])}
        for k_src, k_dst in (
            ("openPrice", "open"),
            ("highPrice", "high"),
            ("lowPrice", "low"),
            ("volume", "volume"),
        ):
            if r.get(k_src) is not None:
                row[k_dst] = float(r[k_src])
        out.append(row)
    out.sort(key=lambda x: x["date"])
    return out


# --------------------------------------------------------------------------- #
# 시세 조회 (kis.py 와 동일한 시그니처 스타일 — 스킬 5번)
# --------------------------------------------------------------------------- #
def get_current_price(code: str, now: Optional[datetime.datetime] = None) -> float:
    resp = _request_auth(
        "GET", BASE_URL + PRICE_PATH, now=now,
        params={PRICE_SYMBOLS_PARAM: code},
    )
    return _parse_price(_require_ok(resp).json(), symbol=code)


def get_current_prices(codes, now: Optional[datetime.datetime] = None) -> dict:
    """여러 종목 현재가를 한 번에 조회. {code: lastPrice(float)}. 실패 시 빈 dict."""
    codes = [str(c) for c in codes if c]
    if not codes:
        return {}
    resp = _request_auth(
        "GET", BASE_URL + PRICE_PATH, now=now,
        params={PRICE_SYMBOLS_PARAM: ",".join(codes)},
    )
    if getattr(resp, "status_code", 200) != 200:
        return {}
    payload = resp.json()
    res = payload.get("result", payload) if isinstance(payload, dict) else payload
    out = {}
    for it in (res if isinstance(res, list) else []):
        lp = it.get("lastPrice") if isinstance(it, dict) else None
        if lp is not None:
            try:
                out[str(it.get("symbol"))] = float(lp)
            except Exception:
                pass
    return out


def _fetch_candles(
    code: str,
    now: Optional[datetime.datetime] = None,
    count: Optional[int] = None,
) -> List[dict]:
    params = {
        CANDLE_SYMBOL_PARAM: code,
        CANDLE_INTERVAL_PARAM: CANDLE_INTERVAL_DAILY,
    }
    if count:
        params[CANDLE_COUNT_PARAM] = min(int(count), CANDLE_COUNT_MAX)
    resp = _request_auth("GET", BASE_URL + CANDLE_PATH, now=now, params=params)
    return _parse_candles(_require_ok(resp).json())


def get_daily_prices(
    code: str,
    count: int = 20,
    today: Optional[datetime.date] = None,
    now: Optional[datetime.datetime] = None,
) -> List[float]:
    """확정된 최근 count 거래일 종가(오래된→최신). 당일 행은 제외한다 (스킬 10번)."""
    if today is None:
        today = datetime.date.today()
    rows = _fetch_candles(code, now=now)
    closes = [r["close"] for r in rows if r["date"] < today]
    return closes[-count:]


# --------------------------------------------------------------------------- #
# DataProvider 구현 — 지표·전략·대시보드는 이 인터페이스만 본다
# --------------------------------------------------------------------------- #
class TossProvider(DataProvider):
    def __init__(self, now: Optional[datetime.datetime] = None):
        self._now = now  # 테스트 clock 주입용

    def get_ohlcv(self, code: str, start_date=None, end_date=None, count=None):
        import pandas as pd

        rows = _fetch_candles(code, now=self._now, count=count)
        if start_date is not None:
            sd = _as_date(start_date)
            rows = [r for r in rows if r["date"] >= sd]
        if end_date is not None:
            ed = _as_date(end_date)
            rows = [r for r in rows if r["date"] <= ed]
        idx = pd.to_datetime([r["date"] for r in rows])
        data = {"Close": [r["close"] for r in rows]}
        for col in ("open", "high", "low", "volume"):
            if rows and col in rows[0]:
                data[col.capitalize()] = [r.get(col) for r in rows]
        return pd.DataFrame(data, index=idx)

    def get_current_price(self, code: str) -> float:
        return get_current_price(code, now=self._now)

    def get_daily_prices(self, code: str, count: int = 20, today=None) -> List[float]:
        return get_daily_prices(code, count=count, today=today, now=self._now)


def _as_date(v) -> datetime.date:
    if isinstance(v, datetime.datetime):
        return v.date()
    if isinstance(v, datetime.date):
        return v
    return datetime.date.fromisoformat(str(v)[:10])
