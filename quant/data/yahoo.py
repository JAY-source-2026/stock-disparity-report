"""야후 파이낸스 실시간 시세(정규장 + 애프터마켓/프리마켓).

국장 다음날 예측용 미국 ETF(EWY·KORU 등)의 시간외(애프터마켓) 변동을 가져온다.
v7 quote 는 cookie+crumb 인증이 필요하다.
"""

from __future__ import annotations

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_sess = None
_crumb = None


def _init() -> bool:
    global _sess, _crumb
    import requests

    s = requests.Session()
    s.headers.update({"User-Agent": _UA})
    try:
        s.get("https://fc.yahoo.com/", timeout=10)
        c = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10).text.strip()
    except Exception:
        return False
    if c and "<" not in c and len(c) < 40:
        _sess, _crumb = s, c
        return True
    return False


def get_quotes(symbols) -> dict:
    """{symbol: {price, change_pct, post_pct, pre_pct}}. 실패 시 빈 dict."""
    global _sess, _crumb
    if _sess is None or not _crumb:
        if not _init():
            return {}
    for _ in range(2):
        try:
            r = _sess.get(
                "https://query1.finance.yahoo.com/v7/finance/quote",
                params={"symbols": ",".join(symbols), "crumb": _crumb}, timeout=15)
            if r.status_code in (401, 403):
                if _init():
                    continue
                return {}
            if r.status_code != 200:
                return {}
            out = {}
            for q in (r.json().get("quoteResponse", {}) or {}).get("result", []) or []:
                out[q.get("symbol")] = {
                    "price": q.get("regularMarketPrice"),
                    "change_pct": q.get("regularMarketChangePercent"),
                    "post_pct": q.get("postMarketChangePercent"),
                    "pre_pct": q.get("preMarketChangePercent"),
                    "market_state": q.get("marketState"),
                }
            return out
        except Exception:
            _sess = None
            if _init():
                continue
            return {}
    return {}
