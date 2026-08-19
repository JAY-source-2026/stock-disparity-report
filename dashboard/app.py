"""대시보드 Flask 백엔드 (스킬 15번·17번).

- 접속: http://localhost:8899 (외부 공개 포트 열지 않음. 폰/외부는 Tailscale).
- 프론트(static/index.html)는 장중 30초~1분 폴링. 백엔드는 FDR 호출을 TTL 캐시로 묶어
  폴링마다 실제 조회가 나가지 않게 한다. 장 마감 후·주말에는 캐시 TTL을 늘린다.
- 대시보드는 조회·계산 도구다. 매매 실행 기능을 넣지 않는다.
"""

from __future__ import annotations

import datetime
import os
import threading

from flask import Flask, jsonify, request, send_from_directory

from dashboard import service, store
from dashboard.service import _HERE

# .env 에서 TOSS_CLIENT_ID/SECRET 로드 (값은 로그에 찍지 않는다 — 스킬 6번)
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(_HERE), ".env"))
except Exception:  # pragma: no cover
    pass

try:
    from quant.data.toss import TossProvider
except Exception:  # pragma: no cover - import 가드
    TossProvider = None

try:
    from quant.data.fdr_provider import FinanceDataReaderProvider
except Exception:  # pragma: no cover - import 가드
    FinanceDataReaderProvider = None

app = Flask(__name__, static_folder=None)

try:
    from quant.data.naver import get_target_price_consensus
except Exception:  # pragma: no cover
    get_target_price_consensus = None

from quant.config.universe import get_target_code

_lock = threading.Lock()
_cache = {"at": None, "state": None}
# 목표주가 컨센서스는 하루 1회만 조회한다 (30초 폴링마다 네이버를 때리지 않는다).
_consensus_cache = {"date": None, "map": {}}
# KRX 종목목록(이름→코드 검색용). 하루 1회 캐시.
_listing_cache = {"date": None, "rows": []}


def _get_listing(today) -> list:
    if _listing_cache["date"] == today and _listing_cache["rows"]:
        return _listing_cache["rows"]
    rows = []
    try:
        import FinanceDataReader as fdr

        # 국내(KRX)만 검색 대상. 미장(US) 검색은 추후 별도 구현 예정이라 지금은 제외.
        krx = fdr.StockListing("KRX")
        for _, r in krx.iterrows():
            rows.append(
                {
                    "code": str(r.get("Code", "")),
                    "name": str(r.get("Name", "")),
                    "market": str(r.get("Market", "")) or "KRX",
                    "currency": "KRW",
                }
            )
    except Exception:
        rows = _listing_cache["rows"]  # 실패 시 이전 캐시 유지
    if rows:
        _listing_cache["date"] = today
        _listing_cache["rows"] = rows
    return rows or _listing_cache["rows"]


_top_cache = {"rows": {"KOSPI": [], "KOSDAQ": [], "US": []}, "kr_at": None, "us_at": None}
_top_fail_until = 0.0  # 국내(KRX) 시총 조회 실패 시 재시도 쿨다운


def _get_top_marcap(today, n: int = 100) -> dict:
    """코스피/코스닥/미국 시총 상위 n종목. {'KOSPI':[],'KOSDAQ':[],'US':[]}.

    국내(KRX)는 장중 3분 / 그 외 30분마다 갱신(시세 최신 반영). 미국(나스닥)은 30분.
    국내·미국 독립 처리, KRX 실패 시 15분 백오프(throttle 회복 유도).
    """
    global _top_fail_until
    import time
    now = datetime.datetime.now()
    rows = _top_cache["rows"]

    def _build(sub):
        sub = sub.sort_values("Marcap", ascending=False).head(n)
        out = []
        for i, (_, r) in enumerate(sub.iterrows()):
            out.append({
                "rank": i + 1,
                "code": str(r.get("Code", "")),
                "name": str(r.get("Name", "")),
                "marcap": float(r.get("Marcap") or 0),
                "close": float(r.get("Close") or 0),
                "change": float(r.get("ChagesRatio") or 0),  # FDR 컬럼명 오타 그대로
            })
        return out

    # 국내(KRX): 장중 3분 / 그 외 30분 TTL, 백오프 존중
    kr_at = _top_cache["kr_at"]
    kr_ttl = 180 if _is_market_hours(now) else 1800
    kr_stale = kr_at is None or (now - kr_at).total_seconds() > kr_ttl
    if kr_stale and time.time() >= _top_fail_until:
        try:
            import FinanceDataReader as fdr

            df = fdr.StockListing("KRX").dropna(subset=["Marcap"])
            market = df["Market"].astype(str)
            rows["KOSPI"] = _build(df[market == "KOSPI"])
            rows["KOSDAQ"] = _build(df[market.str.startswith("KOSDAQ")])
            _top_cache["kr_at"] = now
        except Exception:
            _top_fail_until = time.time() + 900  # 15분 백오프

    # 미국(나스닥): 30분 TTL (KRX 무관)
    us_at = _top_cache["us_at"]
    if us_at is None or (now - us_at).total_seconds() > 1800:
        try:
            rows["US"] = _us_top(n)
            _top_cache["us_at"] = now
        except Exception:
            pass

    # 화면에 보이는 국내 상위 종목 현재가·등락률을 토스로 통일(관심종목과 동일 소스).
    # 캐시(rows)는 FDR 그대로 두고 복사본만 덮어써 매 호출(30초)마다 최신가 반영.
    result = {k: [dict(r) for r in v] for k, v in rows.items()}
    try:
        from quant.data.toss import get_current_prices

        top = result["KOSPI"][:10] + result["KOSDAQ"][:10]
        codes = [r["code"] for r in top if str(r.get("code", "")).isdigit()]
        prices = get_current_prices(codes) if codes else {}
        prevs = _toss_prev_closes(codes, today) if codes else {}
        for r in top:
            p = prices.get(r["code"])
            fc = r.get("close")
            if p and fc:
                prev = prevs.get(r["code"])
                if not (prev and prev > 0):  # 토스 전일종가 없으면 FDR 역산 폴백
                    denom = 1 + (r.get("change") or 0) / 100.0
                    prev = fc / denom if denom else None
                if prev and prev > 0:
                    r["change"] = (p / prev - 1) * 100.0  # 관심종목과 동일 기준
                r["marcap"] = r["marcap"] * p / fc  # 시총도 새 현재가에 맞춰 보정
                r["close"] = p
    except Exception:
        pass
    return result


_prev_close_cache = {"date": None, "map": {}}  # code -> 전일 종가(토스), 하루 캐시


def _toss_prev_closes(codes, today) -> dict:
    """상위 종목 전일 종가(토스 확정 종가, 하루 1회 캐시). {code: prev_close}."""
    if _prev_close_cache["date"] != today:
        _prev_close_cache["date"] = today
        _prev_close_cache["map"] = {}
    m = _prev_close_cache["map"]
    from quant.data.toss import get_daily_prices

    for c in codes:
        if c in m:
            continue
        try:
            dp = get_daily_prices(c, count=1)  # 확정 최근 종가(당일 제외)=전일 종가
            m[c] = dp[-1] if dp else None
        except Exception:
            m[c] = None
    return m


_NASDAQ_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "application/json", "Accept-Language": "en-US",
}


def _us_top(n=100):
    """나스닥 스크리너로 미국(NASDAQ+NYSE) 시가총액 상위 n. 실제 시총."""
    import requests

    rows = []
    for exch in ("NASDAQ", "NYSE"):
        try:
            resp = requests.get(
                "https://api.nasdaq.com/api/screener/stocks", headers=_NASDAQ_HEADERS,
                params={"tableonly": "true", "limit": "6000", "offset": "0",
                        "exchange": exch, "download": "true"}, timeout=25)
            if resp.status_code != 200:
                continue
            data = (resp.json() or {}).get("data") or {}
            rlist = data.get("rows") or (data.get("table") or {}).get("rows") or []
            for x in rlist:
                try:
                    mc = float(x.get("marketCap") or 0)
                    if mc <= 0:
                        continue
                    price = float(str(x.get("lastsale", "")).replace("$", "").replace(",", ""))
                    pct = str(x.get("pctchange", "") or "").replace("%", "").replace(",", "")
                    chg = float(pct) if pct not in ("", "N/A") else 0.0
                    rows.append({"code": x.get("symbol"), "name": x.get("name"),
                                 "marcap": mc, "close": price, "change": chg})
                except Exception:
                    continue
        except Exception:
            continue
    rows.sort(key=lambda r: r["marcap"], reverse=True)
    rows = rows[:n]
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


_fomc_cache = {"date": None, "events": []}  # FOMC 일정 (연준 파싱, 하루 캐시)


def _get_fomc_events(today):
    if _fomc_cache["date"] == today and _fomc_cache["events"]:
        return _fomc_cache["events"]
    events = []
    try:
        from quant.data.fed import get_fomc_dates

        events = [{"date": d.isoformat(), "title": "FOMC (미 연준 금리 결정)", "type": "policy"}
                  for d in get_fomc_dates(today)]
    except Exception:
        events = _fomc_cache["events"]
    if events:
        _fomc_cache["date"] = today
        _fomc_cache["events"] = events
    return events


# 명일 지표용 애프터마켓 ETF (야후) — 국장 다음날 예측용
# (심볼, 라벨, 애프터마켓_소스심볼) — None 이면 자기 자신, "-" 이면 애프터마켓 표시 안 함.
# ^SOX(필라델피아 반도체 지수)는 지수라 시간외가 없으므로 애프터마켓 없이 값만 SOXX 왼쪽에 둔다.
AFTER_ETFS = [
    ("EWY", "한국 ETF(EWY)", None),
    ("KORU", "한국 3배(KORU)", None),
    ("^SOX", "필라델피아 반도체(SOX)", "-"),
    ("SOXX", "반도체(SOXX)", None),
    ("SKHY", "SK하이닉스 ADR", None),
]
_after_cache = {"at": None, "rows": []}


def _get_after_etfs(now):
    # 실시간 오버나잇 반영을 위해 짧게 캐시(8초). 스트리머는 백그라운드에서 계속 갱신.
    if _after_cache["at"] and (now - _after_cache["at"]).total_seconds() < 8:
        return _after_cache["rows"]
    rows = []
    try:
        from quant.data.yahoo import get_quotes
        from quant.data import yahoo_stream

        # 값·등락용(행 심볼) + 애프터마켓 소스 심볼 모두 조회 ("-" 는 애프터 없음)
        all_syms = list(dict.fromkeys(
            [s for s, _, _ in AFTER_ETFS] + [a for _, _, a in AFTER_ETFS if a and a != "-"]))
        after_srcs = list(dict.fromkeys([(a or s) for s, _, a in AFTER_ETFS if a != "-"]))
        yahoo_stream.start(after_srcs)      # 애프터 소스만 실시간 스트리밍
        q = get_quotes(all_syms)
        live = yahoo_stream.get_live(after_srcs)  # 웹소켓 실시간(오버나잇 포함)
        for sym, label, after_sym in AFTER_ETFS:
            d = q.get(sym)
            price = d.get("price") if d else None
            reg_chg = d.get("change_pct") if d else None
            if price is None and sym in live:
                price = live[sym].get("price")
            if price is None:
                continue
            after_pct, after_kind, src = None, "post", None
            if after_sym != "-":               # "-" 이면 애프터마켓 표시 안 함(지수 등)
                asrc = after_sym or sym        # 시간외를 읽어올 심볼
                lv = live.get(asrc)
                if lv and lv.get("is_after"):
                    # 야후 앱과 동일한 실시간 오버나잇/시간외 값
                    after_pct, after_kind, src = lv["chg_pct"], lv["kind"], "live"
                else:
                    dd = q.get(asrc)
                    if dd:                      # 스트리머 값 없으면 REST 애프터/프리로 폴백
                        state = (dd.get("market_state") or "").upper()
                        pre, post = dd.get("pre_pct"), dd.get("post_pct")
                        if state in ("PRE", "PREPRE") and pre is not None:
                            after_pct, after_kind, src = pre, "pre", "rest"
                        elif post is not None:
                            after_pct, after_kind, src = post, "post", "rest"
            rows.append({"key": sym, "label": label, "value": price,
                         "change_pct": reg_chg, "after_pct": after_pct,
                         "after_kind": after_kind, "after_src": src,
                         "spark": [], "error": None})
    except Exception:
        rows = _after_cache["rows"]
    if rows:
        _after_cache["at"] = now
        _after_cache["rows"] = rows
    return rows


_investor_cache = {"at": None, "map": {}}  # 지수 투자자별 매매동향 (네이버, 60초 캐시)


def _get_investor_trend(now):
    if _investor_cache["at"] and (now - _investor_cache["at"]).total_seconds() < 60:
        return _investor_cache["map"]
    m = {}
    try:
        # KRX 확정치 우선(KRX_ID/KRX_PW 설정 시), 실패하면 네이버로 폴백
        from quant.data.krx import get_index_investor, krx_available
        from quant.data.naver import get_index_investor_trend

        for label, mkt in (("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")):
            data = None
            if krx_available():
                try:
                    data = get_index_investor(mkt)
                except Exception:
                    data = None
            if not data or data.get("individual") is None:
                data = get_index_investor_trend(mkt)  # 네이버 폴백
            m[label] = data
    except Exception:
        m = _investor_cache["map"]
    _investor_cache["at"] = now
    _investor_cache["map"] = m
    return m


_ma10_cache = {}  # code -> (date, 월봉10이평). 하루 1회 계산.


def _monthly_ma10(code, today):
    """월봉 10이동평균선 = 최근 10개월 월말 종가 평균 (FDR 장기 일봉 리샘플). 하루 캐시."""
    hit = _ma10_cache.get(code)
    if hit and hit[0] == today:
        return hit[1]
    val = None
    try:
        if FinanceDataReaderProvider is not None:
            prov = FinanceDataReaderProvider()
            start = today - datetime.timedelta(days=500)
            df = prov.get_ohlcv(code, start, today)
            closes = df["Close"].dropna()
            monthly = closes.groupby([closes.index.year, closes.index.month]).last()
            if len(monthly) >= 10:
                val = float(monthly.iloc[-10:].mean())
    except Exception:
        val = None
    _ma10_cache[code] = (today, val)
    return val


def _price_position(cur, ma):
    """현재가 vs 월봉10이평: 위=안정 / 닿음(±1.5%)=검토 / 아래=매도."""
    if cur is None or ma is None or ma == 0:
        return None
    r = cur / ma
    if r >= 1.015:
        return "안정"
    if r <= 0.985:
        return "매도"
    return "검토"


def _invalidate_caches() -> None:
    """보유 목록 변경 시: 상태 캐시 + 컨센서스 캐시를 비워 다음 조회에서 재구성."""
    with _lock:
        _cache["at"] = None
    _consensus_cache["date"] = None


def _get_consensus(holdings: dict, today) -> dict:
    if get_target_price_consensus is None:
        return {}
    if _consensus_cache["date"] == today:
        return _consensus_cache["map"]
    codes = [
        get_target_code(p["code"])
        for p in holdings.get("positions", [])
        if p.get("active", True)
    ]
    try:
        cmap = get_target_price_consensus(codes)
    except Exception:
        cmap = {}
    _consensus_cache["date"] = today
    _consensus_cache["map"] = cmap
    return cmap


def _is_market_hours(now: datetime.datetime) -> bool:
    # 한국장 대략 09:00~15:40, 평일. (KST 기준으로 서버가 돈다고 가정)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return datetime.time(9, 0) <= t <= datetime.time(15, 40)


def _ttl_seconds(now: datetime.datetime) -> int:
    # 장중 30초, 그 외에는 조회를 아끼기 위해 10분.
    return 30 if _is_market_hours(now) else 600


def get_state(force: bool = False) -> dict:
    now = datetime.datetime.now()
    with _lock:
        cached_at = _cache["at"]
        if (
            not force
            and cached_at is not None
            and (now - cached_at).total_seconds() < _ttl_seconds(now)
        ):
            return _cache["state"]

    if TossProvider is None:
        raise RuntimeError("toss.py 임포트 실패")
    # 보유 종목 시세 = 토스, 지수·환율(매크로/지수) = FinanceDataReader 보완 (스킬 17-2)
    stock_provider = TossProvider()
    macro_provider = FinanceDataReaderProvider() if FinanceDataReaderProvider else None
    holdings = service.load_holdings()
    target_prices = _get_consensus(holdings, now.date())  # 네이버 컨센서스(하루 1회 캐시)
    state = service.build_state(
        stock_provider,
        holdings=holdings,
        macro_provider=macro_provider,
        target_prices=target_prices,
        us_provider=macro_provider,  # 해외(USD) 종목은 FDR로 시세 조회
    )
    # 대시보드 카드는 상위 10만 (전체 100은 /api/top/<market>)
    state["top_marcap"] = {k: v[:10] for k, v in _get_top_marcap(now.date()).items()}
    state["top_marcap_at"] = {  # 시총 데이터 갱신 시각(국내 3분/미국 30분 주기)
        "kr": _top_cache["kr_at"].isoformat() if _top_cache["kr_at"] else None,
        "us": _top_cache["us_at"].isoformat() if _top_cache["us_at"] else None,
    }
    # 명일 지표에 애프터마켓 ETF(야후) 추가
    state["nextday"] = (state.get("nextday") or []) + _get_after_etfs(now)
    # 경제 일정: FOMC(연준 공식 파싱) + 동시만기(계산) + events.json(수동: CPI 등)
    state["events"] = service.merged_events(now.date(), extra=_get_fomc_events(now.date()))
    # 코스피/코스닥 투자자별 매매동향(네이버)을 매크로 셀에 부착
    inv = _get_investor_trend(now)
    kmap = {"KS11": "KOSPI", "KQ11": "KOSDAQ"}
    for mrow in state.get("macro", []):
        k = kmap.get(mrow.get("key"))
        mrow["investor"] = inv.get(k) if k else None
    # 보유 종목별 월봉10이평 + 현재가 위치 판단
    for row in state.get("holdings", []):
        ma = _monthly_ma10(row["code"], now.date())
        row["monthly_ma10"] = ma
        row["price_position"] = _price_position(row.get("current_price"), ma)

    with _lock:
        _cache["at"] = now
        _cache["state"] = state
    return state


@app.after_request
def _no_store(resp):
    # 프론트(HTML/JS) 최신본이 항상 로드되도록 캐시를 막는다(오래된 화면 방지).
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return send_from_directory(f"{_HERE}/static", "index.html")


@app.route("/api/state")
def api_state():
    force = request.args.get("force") in ("1", "true", "yes")
    return jsonify(get_state(force=force))


@app.route("/api/after")
def api_after():
    """애프터마켓/오버나잇 ETF만 가볍게 반환 (프런트에서 실시간 폴링용)."""
    return jsonify(_get_after_etfs(datetime.datetime.now()))


_stock_inv_cache = {}  # code -> (at, data)   종목별 투자자 수급 60초 캐시
# 지수 클릭 시 해당 시장 투자자 수급(현물). 선물(FUT)은 전용 수급 데이터가 없어 제외.
_INDEX_INV = {"KS11": "KOSPI", "KPI200": "KOSPI", "KQ11": "KOSDAQ"}


@app.route("/api/investor/<code>")
def api_investor(code: str):
    """투자자별 순매수(개인/기관/외국인). 개별 종목(국내) 또는 지수/선물(현물 기준). KRX 장애 시 {}."""
    now = datetime.datetime.now()
    hit = _stock_inv_cache.get(code)
    if hit and (now - hit[0]).total_seconds() < 60:
        return jsonify(hit[1] or {})
    data = None
    try:
        from quant.data.krx import get_stock_investor, get_index_investor

        if code in _INDEX_INV:
            data = get_index_investor(_INDEX_INV[code])
            if data:
                data["spot"] = True  # 현물 기준 표시용
        else:
            data = get_stock_investor(code)
    except Exception:
        data = None
    _stock_inv_cache[code] = (now, data)
    return jsonify(data or {})


@app.route("/api/top/<market>")
def api_top(market: str):
    data = _get_top_marcap(datetime.datetime.now().date())
    return jsonify(data.get(market.upper(), []))


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "")
    rows = _get_listing(datetime.datetime.now().date())
    return jsonify(store.filter_listing(rows, q))


@app.route("/api/holdings/add", methods=["POST"])
def api_holdings_add():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get("code", "")).strip()
    name = str(body.get("name", "")).strip()
    market = str(body.get("market", "")).strip()
    currency = str(body.get("currency", "")).strip()
    if not code:
        return jsonify({"error": "code required"}), 400
    if not name or not market or not currency:  # 미지정 항목은 종목목록에서 보완
        rows = _get_listing(datetime.datetime.now().date())
        match = next((r for r in rows if r["code"] == code), None)
        if match:
            name = name or match["name"]
            market = market or match["market"]
            currency = currency or match.get("currency", "KRW")
    market = market or "KRX"
    currency = currency or "KRW"
    added = store.add_position(code, name or code, market=market, currency=currency)
    _invalidate_caches()
    return jsonify({"added": added, "code": code, "name": name or code})


@app.route("/api/holdings/arrange", methods=["POST"])
def api_holdings_arrange():
    body = request.get_json(force=True, silent=True) or {}
    watch = [str(c) for c in (body.get("watch") or [])]
    owned = [str(c) for c in (body.get("owned") or [])]
    store.arrange(watch, owned)
    _invalidate_caches()
    return jsonify({"ok": True})


@app.route("/api/holdings/remove", methods=["POST"])
def api_holdings_remove():
    body = request.get_json(force=True, silent=True) or {}
    code = str(body.get("code", "")).strip()
    if not code:
        return jsonify({"error": "code required"}), 400
    removed = store.remove_position(code)
    _invalidate_caches()
    return jsonify({"removed": removed, "code": code})


_ohlcv_cache = {}  # code -> (datetime, payload). 차트 반복 오픈 시 재조회 방지(60초).


# 지수·선물·환율·수익률 — 원/$ 단위가 아닌 포인트
_INDEX_CODES = {
    "KS11", "KQ11", "US500", "IXIC", "DJI", "N225", "HSI", "SSEC", "^STOXX50E",
    "^GDAXI", "FUT", "KS200", "^SOX", "DX-Y.NYB", "^TNX", "ES=F", "CL=F", "GC=F",
    "USD/KRW",
}


def _currency_of(code: str) -> str:
    try:
        for p in service.load_holdings().get("positions", []):
            if str(p.get("code")) == str(code):
                return p.get("currency", "KRW")
    except Exception:
        pass
    if code in _INDEX_CODES:
        return "IDX"  # 지수/선물/포인트 → 단위 없음
    if code.isdigit():
        return "KRW"  # KRX 6자리 코드
    return "USD"  # 알파벳 티커(미국 종목/ETF)


def _df_to_candles(df) -> list:
    import pandas as pd

    out = []
    for ts, row in df.iterrows():
        d = ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]

        def g(*names):
            for n in names:
                if n in row and not pd.isna(row[n]):
                    return float(row[n])
            return None

        close = g("Close", "close")
        if close is None:
            continue
        out.append(
            {
                "time": d,
                "open": g("Open", "open") or close,
                "high": g("High", "high") or close,
                "low": g("Low", "low") or close,
                "close": close,
                "volume": g("Volume", "volume") or 0.0,
            }
        )
    return out


@app.route("/api/ohlcv/<code>")
def api_ohlcv(code: str):
    now = datetime.datetime.now()
    hit = _ohlcv_cache.get(code)
    if hit and (now - hit[0]).total_seconds() < 60:
        return jsonify(hit[1])
    code = code.replace("~", "/")  # URL 안전용 치환 복원 (예: USD~KRW → USD/KRW)
    currency = _currency_of(code)
    try:
        if code == "FUT":
            # KOSPI200 선물은 FDR에 없어 네이버 siseJson(연속 선물 15년)에서 조회
            from quant.data.naver import get_ohlcv_naver

            start_s = (now - datetime.timedelta(days=5500)).strftime("%Y%m%d")
            candles = get_ohlcv_naver("FUT", start_s, now.strftime("%Y%m%d"))
        else:
            # 그 외는 FDR 일봉(약 15년). 표의 현재가·이격도는 여전히 토스(실시간).
            if FinanceDataReaderProvider is None:
                return jsonify({"error": "no data provider", "code": code}), 500
            prov = FinanceDataReaderProvider()
            start = (now - datetime.timedelta(days=5500)).date()
            df = prov.get_ohlcv(code, start, now.date())
            candles = _df_to_candles(df)
    except Exception as exc:
        return jsonify({"error": str(exc), "code": code}), 502
    payload = {"code": code, "currency": currency, "candles": candles}
    _ohlcv_cache[code] = (now, payload)
    return jsonify(payload)


_card_cache = {}  # code -> (date, card). 자동 카드 하루 캐시.


def _get_auto_card(code, today):
    hit = _card_cache.get(code)
    if hit and hit[0] == today:
        return hit[1]
    card = None
    try:
        from quant.data.naver import get_company_card

        card = get_company_card(code)
    except Exception:
        card = None
    if card:
        _card_cache[code] = (today, card)
    return card


_fin_cache = {}  # code -> (date, financials)


def _get_financials(code, today):
    hit = _fin_cache.get(code)
    if hit and hit[0] == today:
        return hit[1]
    fin = None
    try:
        from quant.data.naver import get_company_financials

        fin = get_company_financials(code)
    except Exception:
        fin = None
    if fin:
        _fin_cache[code] = (today, fin)
    return fin


@app.route("/api/card/<code>")
def api_card(code: str):
    today = datetime.datetime.now().date()
    # 1) 직접 작성한 완전판 카드 우선, 없으면 네이버 자동 생성(국내종목)
    card = service.load_card(code) or _get_auto_card(code, today)
    if card is None:
        return jsonify({"error": "not_found", "code": code}), 404
    # 2) 연도별 재무·밸류(기업실적분석)를 어떤 카드든 붙여준다
    fin = _get_financials(code, today)
    if fin:
        card = {**card, "financials": fin}
    return jsonify(card)


@app.route("/static/<path:filename>")
def static_files(filename: str):
    return send_from_directory(f"{_HERE}/static", filename)


if __name__ == "__main__":
    # 로컬 전용. 외부 공개 포트를 열지 않는다 (스킬 15번).
    app.run(host="127.0.0.1", port=8899, debug=False)
