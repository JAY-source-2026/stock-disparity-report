"""HTTP 재시도 헬퍼 (스킬 5번). 토스/네이버 등 모든 외부 호출이 이 함수를 재사용한다.

테스트에서는 이 함수(또는 주입한 session/sleep)를 monkeypatch 하여 실제 네트워크 없이
검증한다 (스킬 9번). 시크릿은 이 계층에서 절대 로깅하지 않는다.
"""

from __future__ import annotations

import time
from typing import Callable, Optional


class TransientHTTPError(Exception):
    """재시도 대상(429/5xx 등) 일시적 오류."""


# 429(요청 한도)·5xx 는 백오프 후 재시도한다.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def request_with_retry(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    data=None,
    json=None,
    timeout: float = 10.0,
    max_retries: int = 4,
    backoff: float = 0.6,
    session=None,
    sleep: Callable[[float], None] = time.sleep,
):
    """method/url 요청을 재시도와 함께 수행하고 response 를 반환.

    - 네트워크 예외, 429(rate limit), 5xx 응답은 지수 백오프로 재시도한다.
    - 그 외 4xx 응답은 그대로 반환하여 호출부가 처리한다.
    - sleep/session 은 테스트에서 주입 가능.
    - 시크릿(토큰·시크릿키)은 이 함수에서 로깅하지 않는다.
    """
    import requests  # 지연 import: requests 미설치 테스트 환경에서 import 에러 방지

    sess = session or requests
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = sess.request(
                method,
                url,
                headers=headers,
                params=params,
                data=data,
                json=json,
                timeout=timeout,
            )
            if resp.status_code in RETRYABLE_STATUS:
                raise TransientHTTPError(f"status {resp.status_code}")
            return resp
        except Exception as exc:  # requests 예외 + TransientHTTPError
            last_exc = exc
            if attempt < max_retries - 1:
                # 429 는 조금 더 길게 쉰다.
                extra = 1.0 if isinstance(exc, TransientHTTPError) and "429" in str(exc) else 0.0
                sleep(backoff * (2 ** attempt) + extra)
            else:
                raise
    raise last_exc  # 도달하지 않음(방어)
