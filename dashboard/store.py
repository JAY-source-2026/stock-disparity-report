"""holdings.json 안전 편집 (추가/삭제) + 종목명 검색 필터.

대시보드는 조회 도구지만, 보유 종목은 계좌 연동 대신 로컬 파일로 관리한다(스킬 3번).
이 모듈은 그 로컬 파일을 락 + 원자적 저장으로 안전하게 편집한다. 매매/주문과 무관하다.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from typing import List, Optional

from quant.config.universe import get_target_code

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
HOLDINGS_PATH = os.path.join(_ROOT, "holdings.json")

_lock = threading.Lock()


def load_holdings(path: str = HOLDINGS_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _atomic_write(path: str, data: dict) -> None:
    """임시파일에 쓰고 교체 → 중간에 깨진 파일이 남지 않게 한다."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def add_position(
    code: str,
    name: str,
    market: str = "KRX",
    currency: str = "KRW",
    path: str = HOLDINGS_PATH,
) -> bool:
    """보유 목록에 종목 추가. 이미 있으면 False, 추가하면 True."""
    with _lock:
        data = load_holdings(path)
        positions = data.setdefault("positions", [])
        existing = next((p for p in positions if str(p.get("code")) == str(code)), None)
        if existing is not None:
            # 이미 있는데 비활성이면 다시 활성화(예: 초기 비활성 TSLA), 아니면 무시
            if not existing.get("active", True):
                existing["active"] = True
                _atomic_write(path, data)
                return True
            return False
        positions.append(
            {
                "code": code,
                "name": name,
                "market": market,
                "currency": currency,
                "quantity": None,
                "avg_price": None,
                "target_code": get_target_code(code),
                "target_weight": None,
                "active": True,
                "owned": False,  # 검색 추가는 항상 관심 종목으로
            }
        )
        _atomic_write(path, data)
        return True


def arrange(watch_codes, owned_codes, path: str = HOLDINGS_PATH) -> bool:
    """드래그앤드롭 결과 반영: positions 순서 재배치 + owned 플래그 설정.

    watch_codes/owned_codes: 각 표의 코드 순서. 목록에 없는 포지션은 뒤에 유지.
    """
    with _lock:
        data = load_holdings(path)
        positions = data.get("positions", [])
        by_code = {str(p.get("code")): p for p in positions}
        new_order, used = [], set()
        for code in watch_codes:
            p = by_code.get(str(code))
            if p is not None:
                p["owned"] = False
                new_order.append(p)
                used.add(str(code))
        for code in owned_codes:
            p = by_code.get(str(code))
            if p is not None:
                p["owned"] = True
                new_order.append(p)
                used.add(str(code))
        for p in positions:  # 목록에 없던(비활성 등) 포지션 유지
            if str(p.get("code")) not in used:
                new_order.append(p)
        data["positions"] = new_order
        _atomic_write(path, data)
        return True


def remove_position(code: str, path: str = HOLDINGS_PATH) -> bool:
    """보유 목록에서 종목 제거. 있었으면 True, 없었으면 False."""
    with _lock:
        data = load_holdings(path)
        positions = data.get("positions", [])
        kept = [p for p in positions if str(p.get("code")) != str(code)]
        if len(kept) == len(positions):
            return False
        data["positions"] = kept
        _atomic_write(path, data)
        return True


def filter_listing(rows: List[dict], query: str, limit: int = 10) -> List[dict]:
    """종목목록(rows: {code,name,market})에서 이름/코드로 검색. 순수함수(테스트 용이).

    정확·앞일치를 먼저, 그다음 포함 매칭을 반환한다.
    """
    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()  # 영문 종목명/티커 대소문자 무시 (예: 'tesla'→'Tesla', 'tsla'→'TSLA')
    starts: List[dict] = []
    contains: List[dict] = []
    for r in rows:
        name = str(r.get("name", ""))
        code = str(r.get("code", ""))
        nl = name.lower()
        cl = code.lower()
        item = {
            "code": code,
            "name": name,
            "market": r.get("market", ""),
            "currency": r.get("currency", ""),
        }
        if cl == ql or nl == ql or nl.startswith(ql):
            starts.append(item)
        elif ql in nl:
            contains.append(item)
    return (starts + contains)[:limit]
