from quant.strategy.disparity import (
    get_badge_class,
    is_risk,
    make_comment,
    make_opinion,
)


def test_opinion_boundaries():
    # 스킬 10번 경계값: <=85 리스크, <=95 매수관심, <110 보유, >=110 익절
    assert make_opinion(85) == "급락·추세훼손·리스크 점검"
    assert make_opinion(85.1) == "매수 관심"
    assert make_opinion(95) == "매수 관심"
    assert make_opinion(95.1) == "보유"
    assert make_opinion(109.9) == "보유"
    assert make_opinion(110) == "익절 검토"


def test_badge_class_boundaries():
    assert get_badge_class(80) == "risk"
    assert get_badge_class(90) == "buy"
    assert get_badge_class(100) == "hold"
    assert get_badge_class(115) == "take-profit"


def test_is_risk():
    assert is_risk(85) is True
    assert is_risk(85.01) is False


def test_make_comment_format():
    c = make_comment(91.2)
    assert "이격도 91.2" in c
    assert "매수 관심" in c
    assert "-8.8%" in c  # 20일선 대비
