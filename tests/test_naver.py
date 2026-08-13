"""네이버 목표주가 파서 테스트 — 실제 요청 없이 샘플 HTML + monkeypatch (스킬 9·13번)."""

from quant.data import naver

# 실제 finance.naver.com/item/main 투자의견 섹션 구조 축약본
SAMPLE_HTML = (
    '<div class="news">뉴스: "삼전 목표주가 60만원 이유 있었네"</div>'  # 뉴스 제목의 목표주가(함정)
    '<table summary="투자의견 정보"><caption>투자의견</caption>'
    '<tr><th scope="row">투자의견<span class="bar">l</span>목표주가</th>'
    '<td><span class="f_up"><em>4.04</em>매수</span><span class="bar">l</span>'
    '<em>493,542</em></td></tr>'
    '<tr><th scope="row">52주최고<span class="bar">l</span>최저</th>'
    '<td><em>374,500</em><span class="bar">l</span><em>67,500</em></td></tr>'
    "</table>"
)

NO_COVERAGE_HTML = '<table><tr><th>PER</th><td><em>12,300</em></td></tr></table>'


class FakeResp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status
        self.encoding = None


def test_extract_picks_first_candidate_not_52week():
    # candidates[0] = 493,542 (목표주가). 52주 최저(67,500)를 잡으면 안 된다.
    assert naver._extract_target_price(SAMPLE_HTML) == 493542.0


def test_extract_none_when_no_consensus():
    assert naver._extract_target_price(NO_COVERAGE_HTML) is None


def test_get_target_price_from_naver(monkeypatch):
    monkeypatch.setattr(naver, "request_with_retry", lambda *a, **k: FakeResp(SAMPLE_HTML))
    assert naver.get_target_price_from_naver("005930") == 493542.0


def test_get_target_price_non_200_returns_none(monkeypatch):
    monkeypatch.setattr(naver, "request_with_retry", lambda *a, **k: FakeResp("err", status=503))
    assert naver.get_target_price_from_naver("005930") is None


def test_consensus_dedup_preserves_and_maps(monkeypatch):
    seen = []

    def fake(code, session=None):
        seen.append(code)
        return 493542.0 if code == "005930" else None

    monkeypatch.setattr(naver, "get_target_price_from_naver", fake)
    # 삼성전자·삼성전자우 모두 005930 으로 매핑되어 중복 → 한 번만 조회
    out = naver.get_target_price_consensus(["005930", "005930", "000660"])
    assert out == {"005930": 493542.0, "000660": None}
    assert seen == ["005930", "000660"]  # 중복 제거됨
