"""종목 유니버스와 목표주가 조회 매핑 (스킬 11번).

- 종목 순서는 리포트/표 출력 순서에 영향을 주므로 임의로 바꾸지 않는다.
- 삼성전자우(005935) 목표주가 컨센서스는 삼성전자 보통주(005930) 기준을 사용한다.
"""

UNIVERSE = [
    {"code": "005930", "name": "삼성전자",       "target_code": "005930"},
    {"code": "005935", "name": "삼성전자우",     "target_code": "005930"},  # 우선주 → 보통주 컨센서스
    {"code": "000660", "name": "SK하이닉스",     "target_code": "000660"},
    {"code": "005380", "name": "현대차",         "target_code": "005380"},
    {"code": "012330", "name": "현대모비스",     "target_code": "012330"},
    {"code": "329180", "name": "HD현대중공업",   "target_code": "329180"},
    {"code": "010140", "name": "삼성중공업",     "target_code": "010140"},
    {"code": "034020", "name": "두산에너빌리티", "target_code": "034020"},
    {"code": "373220", "name": "LG에너지솔루션", "target_code": "373220"},
    {"code": "006400", "name": "삼성SDI",        "target_code": "006400"},
]

_TARGET_MAP = {item["code"]: item["target_code"] for item in UNIVERSE}
_NAME_MAP = {item["code"]: item["name"] for item in UNIVERSE}


def get_target_code(code: str) -> str:
    """목표주가 컨센서스 조회에 쓸 종목코드. 매핑이 없으면 자기 자신을 반환."""
    return _TARGET_MAP.get(code, code)


def get_name(code: str) -> str:
    """유니버스에 등록된 종목명. 없으면 코드를 그대로 반환."""
    return _NAME_MAP.get(code, code)
