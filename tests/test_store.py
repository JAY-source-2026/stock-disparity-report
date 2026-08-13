"""holdings.json 편집 + 검색 필터 테스트 — 임시 파일 사용, 순수함수 검증."""

import json

from dashboard import store


def _make(tmp_path):
    p = tmp_path / "holdings.json"
    p.write_text(
        json.dumps(
            {
                "base_currency": "KRW",
                "positions": [{"code": "005930", "name": "삼성전자", "active": True}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return str(p)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def test_add_position(tmp_path):
    p = _make(tmp_path)
    assert store.add_position("000660", "SK하이닉스", path=p) is True
    codes = [x["code"] for x in _read(p)["positions"]]
    assert "000660" in codes
    added = next(x for x in _read(p)["positions"] if x["code"] == "000660")
    assert added["quantity"] is None and added["active"] is True


def test_add_dedup(tmp_path):
    p = _make(tmp_path)
    assert store.add_position("005930", "삼성전자", path=p) is False
    assert len(_read(p)["positions"]) == 1


def test_add_preferred_maps_target_code(tmp_path):
    p = _make(tmp_path)
    store.add_position("005935", "삼성전자우", path=p)
    row = next(x for x in _read(p)["positions"] if x["code"] == "005935")
    assert row["target_code"] == "005930"  # 우선주 → 보통주 목표주가


def test_add_us_stock_stores_currency(tmp_path):
    p = _make(tmp_path)
    assert store.add_position("TSLA", "Tesla Inc", market="NASDAQ", currency="USD", path=p) is True
    row = next(x for x in _read(p)["positions"] if x["code"] == "TSLA")
    assert row["currency"] == "USD" and row["market"] == "NASDAQ"


def test_add_reactivates_inactive(tmp_path):
    p = tmp_path / "h.json"
    p.write_text(json.dumps({"positions": [
        {"code": "TSLA", "name": "Tesla", "active": False}]}, ensure_ascii=False), encoding="utf-8")
    # 비활성으로 이미 존재 → 다시 활성화되어 True
    assert store.add_position("TSLA", "Tesla", currency="USD", path=str(p)) is True
    row = next(x for x in json.loads(p.read_text(encoding="utf-8"))["positions"] if x["code"] == "TSLA")
    assert row["active"] is True


def test_filter_listing_passes_currency():
    rows = [{"code": "TSLA", "name": "Tesla Inc", "market": "NASDAQ", "currency": "USD"}]
    out = store.filter_listing(rows, "Tesla")
    assert out[0]["currency"] == "USD"


def test_remove_position(tmp_path):
    p = _make(tmp_path)
    assert store.remove_position("005930", path=p) is True
    assert _read(p)["positions"] == []


def test_remove_missing(tmp_path):
    p = _make(tmp_path)
    assert store.remove_position("999999", path=p) is False


def test_filter_listing_by_name_and_code():
    rows = [
        {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
        {"code": "005935", "name": "삼성전자우", "market": "KOSPI"},
        {"code": "000660", "name": "SK하이닉스", "market": "KOSPI"},
    ]
    out = store.filter_listing(rows, "삼성전자")
    codes = [x["code"] for x in out]
    assert "005930" in codes and "005935" in codes and "000660" not in codes
    # 정확/앞일치가 먼저
    assert out[0]["code"] == "005930"
    # 코드로도 검색
    assert store.filter_listing(rows, "000660")[0]["code"] == "000660"
    # 빈 쿼리는 빈 결과
    assert store.filter_listing(rows, "  ") == []
