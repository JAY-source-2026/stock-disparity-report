"""야후 파이낸스 실시간 웹소켓 스트리머 (오버나잇/시간외 세션 포함).

야후 앱과 동일하게 정규장 종료 후 오버나잇(Blue Ocean ATS, 미국 밤 8시~새벽 4시 ET)
세션의 실시간 체결가를 받는다. v7/v8 REST API 는 공식 애프터마켓(8pm ET)에서 멈추므로
오버나잇 값을 얻으려면 앱이 쓰는 streamer.finance.yahoo.com 웹소켓이 유일한 경로다.

백그라운드 데몬 스레드가 웹소켓을 계속 열어두고, 체결이 올 때마다 모듈 전역 캐시를
갱신한다. Flask 요청 스레드는 get_live() 로 즉시(논블로킹) 최신값을 읽는다.
연결이 끊기면 자동 재접속한다.
"""

from __future__ import annotations

import base64
import json
import struct
import threading
import time

_WS_URL = "wss://streamer.finance.yahoo.com/?version=2"

# MarketHoursType enum (protobuf field 7)
_PRE, _REGULAR, _POST, _EXTENDED = 0, 1, 2, 3
# 관측상 4 = 오버나잇 세션. 정규장(1)만 시간외가 아니고 나머지는 모두 시간외로 취급.

_lock = threading.Lock()
_live: dict[str, dict] = {}      # {symbol: {price, chg_pct, hours, ts}}
_started = False
_symbols: list[str] = []
_ws = None


# --------------------------------------------------------------------------- #
# protobuf 수동 디코더 (yaticker / PricingData 메시지)
# 필요한 필드만: 1=id(str) 2=price(f32) 7=marketHours(varint) 8=changePercent(f32)
# --------------------------------------------------------------------------- #
def _read_varint(b: bytes, i: int):
    shift = 0
    res = 0
    while True:
        x = b[i]
        i += 1
        res |= (x & 0x7F) << shift
        if not (x & 0x80):
            break
        shift += 7
    return res, i


def _decode(msg: str) -> dict:
    b = base64.b64decode(msg)
    i = 0
    out: dict = {}
    n = len(b)
    while i < n:
        tag, i = _read_varint(b, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:                       # varint
            v, i = _read_varint(b, i)
            out[field] = v
        elif wt == 5:                     # 32-bit float
            out[field] = struct.unpack("<f", b[i:i + 4])[0]
            i += 4
        elif wt == 1:                     # 64-bit
            i += 8
        elif wt == 2:                     # length-delimited
            ln, i = _read_varint(b, i)
            if field in (1, 4, 5, 13):    # id, currency, exchange, shortName
                out[field] = b[i:i + ln].decode("utf-8", "ignore")
            i += ln
        else:
            break
    return out


# --------------------------------------------------------------------------- #
# 웹소켓 콜백
# --------------------------------------------------------------------------- #
def _on_open(ws):
    try:
        ws.send(json.dumps({"subscribe": _symbols}))
    except Exception:
        pass


def _on_message(ws, message):
    try:
        j = json.loads(message)
        raw = j.get("message") if isinstance(j, dict) else message
    except Exception:
        raw = message
    if not raw:
        return
    try:
        d = _decode(raw)
    except Exception:
        return
    sid = d.get(1)
    if not sid or sid not in _symbols:
        return
    # 부분 틱(가격만/호가만 갱신 등)은 없는 필드로 기존 값을 덮어쓰지 않도록 병합한다.
    with _lock:
        cur = _live.get(sid, {})
        if d.get(2) is not None:
            cur["price"] = d.get(2)
        if d.get(8) is not None:
            cur["chg_pct"] = d.get(8)
        if d.get(7) is not None:
            cur["hours"] = d.get(7)
        cur["ts"] = int(time.time())
        _live[sid] = cur


def _run():
    import websocket  # websocket-client
    global _ws
    while True:
        try:
            _ws = websocket.WebSocketApp(
                _WS_URL, on_open=_on_open, on_message=_on_message)
            _ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception:
            pass
        time.sleep(3)  # 끊기면 재접속


def start(symbols) -> None:
    """백그라운드 스트리머 시작(한 번만). 이후 get_live() 로 최신값 조회."""
    global _started, _symbols
    if _started:
        return
    _symbols = list(symbols)
    _started = True
    t = threading.Thread(target=_run, name="yahoo-streamer", daemon=True)
    t.start()


def get_live(symbols, max_age: int = 900) -> dict:
    """{symbol: {price, chg_pct, is_after, kind}} — 최근 체결이 있는 심볼만.

    max_age(초) 보다 오래된 값은 제외. kind: 'pre'(장전 ☀️) / 'post'(시간외 🌙).
    정규장(hours==REGULAR) 체결은 시간외가 아니므로 is_after=False.
    """
    now = int(time.time())
    out: dict = {}
    with _lock:
        for s in symbols:
            d = _live.get(s)
            if not d or d.get("chg_pct") is None:
                continue
            if now - d.get("ts", 0) > max_age:
                continue
            hours = d.get("hours")
            is_after = hours != _REGULAR
            kind = "pre" if hours == _PRE else "post"
            out[s] = {
                "price": d.get("price"),
                "chg_pct": d.get("chg_pct"),
                "is_after": is_after,
                "kind": kind,
                "hours": hours,
                "age": now - d.get("ts", 0),
            }
    return out
