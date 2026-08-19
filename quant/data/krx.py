"""KRX(거래소) 확정 투자자별 순매수 — pykrx 사용.

증권사(미래에셋 등)가 보는 확정 수급과 동일한 KRX 원천 데이터.
KRX 투자자/지수 데이터는 로그인이 필요하다: 환경변수 KRX_ID / KRX_PW 를
설정하면 pykrx 가 자동 로그인한다. 미설정/실패 시 호출부가 네이버로 폴백한다.

주의: KRX 는 .env(또는 GitHub Secrets)로만 관리하고 값을 로그에 출력하지 않는다.
"""

from __future__ import annotations

import datetime
import json
import os
import time
from typing import Optional


def krx_available() -> bool:
    return bool(os.environ.get("KRX_ID") and os.environ.get("KRX_PW"))


# KRX 공용 백오프 — 실패(로그인 throttle 등) 시 15분간 KRX 재시도를 멈춰 회복을 유도.
# 수급·ADR 등 모든 KRX 호출이 공유한다(안 그러면 서로 계속 두드려 차단이 안 풀림).
_krx_fail_until = 0.0


def _krx_cooldown() -> bool:
    return time.time() < _krx_fail_until


def _krx_note_fail(seconds: int = 900) -> None:
    global _krx_fail_until
    _krx_fail_until = time.time() + seconds


def get_index_investor(market: str = "KOSPI", date: Optional[str] = None) -> Optional[dict]:
    """지수 투자자별 순매수(억원). {'individual','foreign','institution','source'} 또는 None.

    market: 'KOSPI' | 'KOSDAQ'. date: 'YYYYMMDD' (기본=오늘). 실패 시 None(→네이버 폴백).
    """
    if not krx_available() or _krx_cooldown():
        return None
    try:
        from pykrx import stock

        day = date or datetime.date.today().strftime("%Y%m%d")
        df = stock.get_market_trading_value_by_investor(day, day, market)
    except Exception:
        _krx_note_fail()          # 로그인 throttle 등 → 백오프
        return None
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


def get_stock_investor(code: str, date: Optional[str] = None) -> Optional[dict]:
    """개별 종목 투자자별 순매수(억원). {'individual','foreign','institution','source','date'} 또는 None.

    국내 6자리 종목코드만. 오늘치가 전부 0(장 초반/개장 전)이면 직전 거래일로 대체.
    지수 수급(get_index_investor)과 동일 형식.
    """
    if not krx_available() or _krx_cooldown():
        return None
    if not (code and code.isdigit() and len(code) == 6):
        return None  # 국내 종목만
    try:
        from pykrx import stock

        base = (datetime.datetime.strptime(date, "%Y%m%d").date()
                if date else datetime.date.today())
        for back in range(5):  # 오늘 → 최근 거래일(주말/휴일·전부0 건너뜀)
            day = (base - datetime.timedelta(days=back)).strftime("%Y%m%d")
            df = stock.get_market_trading_value_by_investor(day, day, code)
            if df is None or df.empty or "순매수" not in df.columns:
                continue

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
                continue
            foreign, inst = _val("외국인", "외국인합계"), _val("기관합계", "기관")
            if back < 4 and ind == 0 and (foreign or 0) == 0 and (inst or 0) == 0:
                continue  # 아직 거래 없음 → 직전 거래일
            return {"individual": ind, "foreign": foreign, "institution": inst,
                    "source": "KRX", "date": day}
    except Exception:
        _krx_note_fail()
        return None
    return None


# --------------------------------------------------------------------------- #
# ADR (등락비율) — 최근 window 거래일 상승/하락 종목수로 계산하는 시장 심리지표.
# ADR = (기간 상승종목수 합 / 기간 하락종목수 합) × 100. 120↑ 과매수, 75↓ 과매도.
# --------------------------------------------------------------------------- #
# 날짜별 상승/하락 종목수·계산결과를 디스크에 캐시(재시작·KRX 장애에도 유지).
_ADR_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".cache")
_ADR_COUNTS_FILE = os.path.join(_ADR_DIR, "adr_counts.json")
_ADR_RESULT_FILE = os.path.join(_ADR_DIR, "adr_result.json")
_adr_counts = None        # {'YYYYMMDD': [up, down]}
_adr_result_mem: dict = {}  # {'YYYY-MM-DD'(오늘): result}


def _adr_load_counts() -> dict:
    global _adr_counts
    if _adr_counts is None:
        try:
            with open(_ADR_COUNTS_FILE, encoding="utf-8") as f:
                _adr_counts = json.load(f)
        except Exception:
            _adr_counts = {}
    return _adr_counts


def _adr_save(path, obj):
    try:
        os.makedirs(_ADR_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f)
    except Exception:
        pass


def _adr_load_result():
    try:
        with open(_ADR_RESULT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _adr_daily_counts(day: str, market: str = "KOSPI"):
    counts = _adr_load_counts()
    if day in counts:
        return counts[day]
    from pykrx import stock

    df = stock.get_market_price_change_by_ticker(day, day, market=market)
    col = next((c for c in df.columns if "등락" in c), None)
    if col is None or len(df) == 0:
        raise ValueError("no change data")
    counts[day] = [int((df[col] > 0).sum()), int((df[col] < 0).sum())]
    _adr_save(_ADR_COUNTS_FILE, counts)
    return counts[day]


def get_kospi_adr(today: Optional[datetime.date] = None, window: int = 20,
                  market: str = "KOSPI") -> Optional[dict]:
    """코스피 ADR(등락비율, 20거래일). {'value','change_pct'} 또는 None(집계 전).

    날짜별 종목수를 디스크 캐시해 하루 1회만 신규 조회한다. KRX 실패 시엔
    마지막 성공값(파일)을 반환하고 15분 백오프해 KRX 재시도를 멈춘다(차단 회복 유도).
    """
    if not krx_available():
        return _adr_load_result()
    today = today or datetime.date.today()
    rkey = today.isoformat()
    if rkey in _adr_result_mem:
        return _adr_result_mem[rkey]
    if _krx_cooldown():                    # KRX 공용 백오프 중 → 마지막 값
        return _adr_load_result()
    try:
        from pykrx import stock

        start = (today - datetime.timedelta(days=window * 2 + 20)).strftime("%Y%m%d")
        idx = stock.get_index_ohlcv(start, today.strftime("%Y%m%d"), "1001")
        days = [d.strftime("%Y%m%d") for d in idx.index][-(window + 1):]
        if len(days) < window:
            raise ValueError("not enough trading days")
        ups, downs = [], []
        for d in days:
            u, dn = _adr_daily_counts(d, market)
            ups.append(u)
            downs.append(dn)

        def _adr(us, ds):
            sd = sum(ds)
            return round(sum(us) / sd * 100, 1) if sd else None

        cur = _adr(ups[-window:], downs[-window:])
        prev = _adr(ups[-window - 1:-1], downs[-window - 1:-1]) if len(ups) > window else None
        chg = (cur - prev) / prev * 100.0 if (cur is not None and prev) else None
        result = {"value": cur, "change_pct": chg, "date": rkey}
        _adr_result_mem[rkey] = result
        _adr_save(_ADR_RESULT_FILE, result)
        return result
    except Exception:
        _krx_note_fail()                       # KRX 공용 15분 백오프
        return _adr_load_result()


def get_adr_history(today: Optional[datetime.date] = None, points: int = 20,
                    window: int = 20, market: str = "KOSPI") -> list:
    """최근 points 거래일의 20일 ADR(등락비율) 시계열. [{'date','adr'}] (오래된→최신) 또는 []."""
    if not krx_available() or _krx_cooldown():
        return []
    today = today or datetime.date.today()
    try:
        from pykrx import stock

        need = points + window
        start = (today - datetime.timedelta(days=need * 2 + 20)).strftime("%Y%m%d")
        idx = stock.get_index_ohlcv(start, today.strftime("%Y%m%d"), "1001")
        days = [d.strftime("%Y%m%d") for d in idx.index][-need:]
        if len(days) < window + 1:
            return []
        cnt = []  # (day, up, down) — 디스크 캐시 재사용
        for d in days:
            try:
                u, dn = _adr_daily_counts(d, market)
                cnt.append((d, u, dn))
            except Exception:
                pass
        out = []
        for i in range(window - 1, len(cnt)):
            wnd = cnt[i - window + 1:i + 1]
            up = sum(c[1] for c in wnd)
            dn = sum(c[2] for c in wnd)
            if dn:
                d = cnt[i][0]
                out.append({"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "adr": round(up / dn * 100, 1)})
        return out
    except Exception:
        _krx_note_fail()
        return []
