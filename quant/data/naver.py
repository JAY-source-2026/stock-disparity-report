"""네이버증권 목표주가 컨센서스 파싱 (스킬 2번·13번).

토스 API 에 없는 '증권사 목표주가 컨센서스' 전용 소스. 시세는 여기서 가져오지 않는다.

스킬 13번(파싱 예민 — 임의 개선 금지)에 따라 반드시 유지할 핵심값:
  - response.encoding = "utf-8"
  - User-Agent / Accept-Language 헤더
  - '목표주가' 뒤 첫 번째 쉼표 포함 금액 = candidates[0] 을 선택
    (마지막 후보를 쓰면 바로 아래 행의 52주 최고/최저가 잡힌다)

실제 페이지(finance.naver.com/item/main)의 투자의견 섹션 구조:
  ...목표주가</th> <td>...매수... <em>493,542</em></td>
  <tr> 52주최고 l 최저 <em>374,500</em> l <em>67,500</em> </tr>
뉴스 제목에도 '목표주가' 문자열이 있으므로, 표 헤더 '목표주가</th>' 를 앵커로 삼아
컨센서스 값만 정확히 집는다.
"""

from __future__ import annotations

import datetime
import re
from typing import Dict, Iterable, Optional

from quant.core.http import request_with_retry

NAVER_MAIN_URL = "https://finance.naver.com/item/main.naver"

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
HEADERS = {"User-Agent": _USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}

# 투자의견 표의 목표주가 헤더 (뉴스 제목의 '목표주가'와 구분하는 앵커)
_TARGET_ANCHOR = "목표주가</th>"
_AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})+")


def _extract_target_price(html: str) -> Optional[float]:
    """투자의견 섹션에서 목표주가 컨센서스 추출. 없으면 None."""
    idx = html.find(_TARGET_ANCHOR)
    if idx == -1:
        return None
    after = html[idx: idx + 400]
    candidates = _AMOUNT_RE.findall(after)
    if not candidates:
        return None
    # candidates[0] 이 목표주가. 마지막 후보(=52주 최고/최저)는 절대 쓰지 않는다 (스킬 13번).
    return float(candidates[0].replace(",", ""))


def get_target_price_from_naver(code: str, session=None) -> Optional[float]:
    """종목코드의 목표주가 컨센서스(원). 애널리스트 커버리지가 없으면 None."""
    resp = request_with_retry(
        "GET", NAVER_MAIN_URL, headers=HEADERS, params={"code": code}, session=session
    )
    if getattr(resp, "status_code", 200) != 200:
        return None
    resp.encoding = "utf-8"  # 스킬 13번 핵심값
    price = _extract_target_price(resp.text)
    print(f"[naver] 목표주가 컨센서스 {code}: {price}")  # 스킬 13번: 로그 문구 유지
    return price


NAVER_INDEX_URL = "https://finance.naver.com/sise/sise_index.naver"
# "투자자별 매매동향 개인 -724 억 외국인 +446 억 기관 +319 억"  (단위: 억원)
_INVESTOR_RE = re.compile(
    r"개인\s*([+\-]?[\d,]+)\s*억\s*외국인\s*([+\-]?[\d,]+)\s*억\s*기관\s*([+\-]?[\d,]+)\s*억"
)


def get_index_investor_trend(code: str, session=None):
    """지수(KOSPI/KOSDAQ) 투자자별 순매수(억원). {'individual','foreign','institution'} 또는 None.

    네이버 지수 페이지(EUC-KR)의 '투자자별 매매동향' 블록을 파싱한다.
    """
    resp = request_with_retry(
        "GET", NAVER_INDEX_URL, headers=HEADERS, params={"code": code}, session=session
    )
    if getattr(resp, "status_code", 200) != 200:
        return None
    resp.encoding = "euc-kr"  # 지수 페이지는 EUC-KR
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", resp.text))
    m = _INVESTOR_RE.search(text)
    if not m:
        return None

    def _num(s):
        return int(s.replace(",", "").replace("+", ""))

    return {
        "individual": _num(m.group(1)),
        "foreign": _num(m.group(2)),
        "institution": _num(m.group(3)),
    }


_NOW_VALUE_RE = re.compile(r'id="now_value">\s*<strong[^>]*>([\d,\.]+)</strong>')
_CHANGE_RE = re.compile(r"전일대비\s*([▲▼])\s*([\d,\.]+)")


NAVER_SISE_JSON = "https://api.finance.naver.com/siseJson.naver"
# ["20260803", 1010.4, 1018.3, 983.05, 992.35, 138722, 0.0]
_SISE_ROW_RE = re.compile(
    r'\["(\d{8})",\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*([\d.]+),\s*(\d+)'
)


def get_ohlcv_naver(symbol: str, start: str, end: str, session=None) -> list:
    """네이버 siseJson으로 지수/선물 일봉 OHLCV. symbol='FUT'(코스피200 선물) 등.

    start/end: 'YYYYMMDD'. 반환: [{'time','open','high','low','close','volume'}...] 오름차순.
    """
    resp = request_with_retry(
        "GET",
        NAVER_SISE_JSON,
        headers=HEADERS,
        params={"symbol": symbol, "requestType": 1, "startTime": start,
                "endTime": end, "timeframe": "day"},
        session=session,
    )
    if getattr(resp, "status_code", 200) != 200:
        return []
    out = []
    for m in _SISE_ROW_RE.finditer(resp.text):
        d = m.group(1)
        out.append({
            "time": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
            "open": float(m.group(2)), "high": float(m.group(3)),
            "low": float(m.group(4)), "close": float(m.group(5)),
            "volume": float(m.group(6)),
        })
    out.sort(key=lambda x: x["time"])
    return out


def get_index_quote(code: str, session=None):
    """네이버 지수 페이지(EUC-KR)에서 현재값·등락률. 선물(code=FUT) 등 FDR 미제공 지수용.

    반환: {"value": float, "change_pct": float|None} 또는 None
    """
    resp = request_with_retry(
        "GET", NAVER_INDEX_URL, headers=HEADERS, params={"code": code}, session=session
    )
    if getattr(resp, "status_code", 200) != 200:
        return None
    resp.encoding = "euc-kr"
    html = resp.text
    mv = _NOW_VALUE_RE.search(html)
    if not mv:
        return None
    value = float(mv.group(1).replace(",", ""))
    seg = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html[max(0, html.find("now_value") - 80): html.find("now_value") + 400]))
    change_pct = None
    mc = _CHANGE_RE.search(seg)
    if mc:
        cv = float(mc.group(2).replace(",", ""))
        if mc.group(1) == "▼":
            cv = -cv
        prev = value - cv
        if prev:
            change_pct = cv / prev * 100.0
    return {"value": value, "change_pct": change_pct}


_MOBILE_HEADERS = {"User-Agent": _USER_AGENT, "Referer": "https://m.stock.naver.com/",
                   "Accept": "application/json"}


def get_company_card(code: str, session=None) -> Optional[dict]:
    """네이버 모바일 API로 종목 기업 해독 카드 자동 생성 (국내종목).

    실적·밸류에이션·수급·경쟁구도는 실데이터로 채우고, 정성 섹션(전방산업·리스크)은
    완전판 카드에서 보완하도록 안내한다.
    """
    resp = request_with_retry(
        "GET", f"https://m.stock.naver.com/api/stock/{code}/integration",
        headers=_MOBILE_HEADERS, session=session,
    )
    if getattr(resp, "status_code", 200) != 200:
        return None
    try:
        d = resp.json()
    except Exception:
        return None
    if not d.get("stockName"):
        return None

    ti = {x.get("code"): x.get("value") for x in (d.get("totalInfos") or [])}
    cons = d.get("consensusInfo") or {}
    peers = [p.get("stockName") for p in (d.get("industryCompareInfo") or []) if p.get("stockName")][:6]
    dt = (d.get("dealTrendInfos") or [{}])[0]

    def g(*keys):
        for k in keys:
            if ti.get(k):
                return ti[k]
        return "-"

    tgt = cons.get("priceTargetMean") or "-"
    recomm = cons.get("recommMean") or "-"
    sections = {
        "1_business": f"업종코드 {d.get('industryCode','-')}. 상세 사업 내용은 완전판 카드에서 보완.",
        "2_customers": "전방산업·주요 고객사 — 정성 분석(완전판 카드)에서 정리.",
        "3_competition": f"동일업종 주요 종목: {', '.join(peers) if peers else '-'}",
        "4_earnings": f"EPS {g('eps')} → 추정 EPS {g('cnsEps')} · PER {g('per')} vs 추정 PER {g('cnsPer')} (컨센서스 이익 방향 참고)",
        "5_valuation": f"PER {g('per')} · PBR {g('pbr')} · 시총 {g('marketValue')} · 배당수익률 {g('dividendYieldRatio')} · "
                       f"52주 {g('lowPriceOf52Weeks')}~{g('highPriceOf52Weeks')} · 목표주가 {tgt} (투자의견 {recomm}/5)",
        "6_bear_case": "망하는 시나리오 — 정성 리스크 분석(완전판 카드)에서 정리.",
        "7_flow_note": f"외인소진율 {g('foreignRate')} · 최근 순매수(수량) 외국인 {dt.get('foreignerPureBuyQuant','-')} / "
                       f"기관 {dt.get('organPureBuyQuant','-')} / 개인 {dt.get('individualPureBuyQuant','-')}",
    }
    today = datetime.date.today().isoformat()
    return {
        "code": code, "name": d.get("stockName"), "generated_at": today, "auto": True,
        "source_note": "네이버 자동 생성 — 실적·밸류·수급·경쟁은 실데이터, 정성 섹션(1·2·6)은 완전판 카드에서 보완.",
        "sections": sections,
    }


_FIN_METRICS = ["매출액", "영업이익", "영업이익률", "순이익률", "ROE",
                "EPS", "PER", "BPS", "PBR", "주당배당금"]


def get_company_financials(code: str, session=None) -> Optional[dict]:
    """네이버 연도별 재무·밸류에이션(기업실적분석). {'years':[...], 'metrics':[...]} 또는 None."""
    resp = request_with_retry(
        "GET", f"https://m.stock.naver.com/api/stock/{code}/finance/annual",
        headers=_MOBILE_HEADERS, session=session,
    )
    if getattr(resp, "status_code", 200) != 200:
        return None
    try:
        fi = (resp.json() or {}).get("financeInfo") or {}
    except Exception:
        return None
    periods = fi.get("trTitleList") or []
    if not periods:
        return None
    keys = [p.get("key") for p in periods]
    years = [{"title": str(p.get("title", ""))[:4], "est": p.get("isConsensus") == "Y"}
             for p in periods]
    rowmap = {r.get("title"): r for r in (fi.get("rowList") or [])}
    metrics = []
    for name in _FIN_METRICS:
        r = rowmap.get(name)
        if not r:
            continue
        cols = r.get("columns") or {}
        metrics.append({"title": name,
                        "values": [(cols.get(k) or {}).get("value") for k in keys]})
    return {"years": years, "metrics": metrics}


def get_target_price_consensus(
    codes: Iterable[str], session=None
) -> Dict[str, Optional[float]]:
    """여러 종목의 목표주가 컨센서스 맵. 하루 1회 캐시하여 호출한다(호출부 책임).

    개별 종목 조회 실패는 None 으로 두고 전체를 막지 않는다.
    """
    out: Dict[str, Optional[float]] = {}
    for code in dict.fromkeys(codes):  # 순서 유지 + 중복 제거
        try:
            out[code] = get_target_price_from_naver(code, session=session)
        except Exception:
            out[code] = None
    return out
