import pytest

from quant.indicators.moving_average import (
    calculate_disparity,
    calculate_ma20,
    calculate_target_ratio,
)


def test_ma20_uses_last_20_closes():
    closes = list(range(1, 26))  # 1..25, 최신이 뒤
    # 최근 20개(6..25) 평균 = (6+25)/2 = 15.5
    assert calculate_ma20(closes) == pytest.approx(15.5)


def test_ma20_requires_20_confirmed_closes():
    with pytest.raises(ValueError):
        calculate_ma20([100.0] * 19)


def test_disparity_formula():
    # 현재가 110, ma20 100 → 110
    assert calculate_disparity(110.0, 100.0) == pytest.approx(110.0)


def test_disparity_zero_ma_raises():
    with pytest.raises(ValueError):
        calculate_disparity(100.0, 0.0)


def test_target_ratio_upside():
    # 목표 120, 현재 100 → +20%
    assert calculate_target_ratio(100.0, 120.0) == pytest.approx(20.0)


def test_target_ratio_none_when_no_target():
    assert calculate_target_ratio(100.0, None) is None
