"""미 연준(FOMC) 회의 일정 파싱 — federalreserve.gov 공식 캘린더.

경제 캘린더 자동화. 규칙/하드코딩 대신 공식 페이지에서 실제 일정을 가져온다.
(CPI(BLS)·investing·nasdaq은 이 환경에서 차단되어 FOMC만 자동 파싱 가능.)
"""

from __future__ import annotations

import datetime
import re
from typing import List

from quant.core.http import request_with_retry

FED_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}
_MON_ALT = "January|February|March|April|May|June|July|August|September|October|November|December"
# 같은 달(Sep 15-16) + 월 걸침(April 30-May 1) 둘 다 처리. 하이픈/엔대시 허용.
_MEETING_RE = re.compile(
    rf"({_MON_ALT})\s+(\d{{1,2}})\s*[-–]\s*(?:({_MON_ALT})\s+)?(\d{{1,2}})"
)


def get_fomc_dates(today: datetime.date = None, ahead: int = 6) -> List[datetime.date]:
    """오늘 이후 FOMC 결정일(회의 둘째 날) 목록. 실패 시 빈 리스트."""
    if today is None:
        today = datetime.date.today()
    resp = request_with_retry("GET", FED_URL, headers={"User-Agent": _UA})
    if getattr(resp, "status_code", 200) != 200:
        return []
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", resp.text))
    out = set()
    for yr in (today.year, today.year + 1):
        marker = f"{yr} FOMC Meetings"
        i = text.find(marker)
        if i < 0:
            continue
        seg = text[i + len(marker): i + 1500]
        nxt = seg.find("FOMC Meetings")  # 다음 연도 섹션 전까지
        if nxt > 0:
            seg = seg[:nxt]
        for m in _MEETING_RE.finditer(seg):
            # 결정일=둘째 날. 월 걸침이면 둘째 날의 월(group3)을 쓴다.
            month = _MONTHS[m.group(3) or m.group(1)]
            try:
                out.add(datetime.date(yr, month, int(m.group(4))))
            except ValueError:
                pass
    return sorted(d for d in out if d >= today)[:ahead]
