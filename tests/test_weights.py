"""P6:权重熵与到中心的距离。"""

from __future__ import annotations

import math

import pytest

from common.weights import (
    N_WEIGHTS,
    distance_from_center,
    max_distance_from_center,
    normalised_entropy,
)

UNIFORM = [1 / N_WEIGHTS] * N_WEIGHTS
CORNER = [1.0] + [0.0] * (N_WEIGHTS - 1)


def test_uniform_weights_have_entropy_one():
    assert normalised_entropy(UNIFORM) == pytest.approx(1.0)


def test_all_mass_on_one_dimension_has_entropy_zero():
    """0·ln0 = 0 是极限值,不是未定义。这一维对熵的贡献确实是零。"""
    assert normalised_entropy(CORNER) == pytest.approx(0.0)


def test_entropy_is_between_zero_and_one():
    import random

    rng = random.Random(3)
    for _ in range(200):
        w = [rng.random() for _ in range(N_WEIGHTS)]
        assert 0.0 <= normalised_entropy(w) <= 1.0


def test_entropy_is_scale_invariant():
    """量表给的是"分",不是归一化权重。两者必须给出同一个熵。"""
    points = [40, 15, 10, 12, 8, 9, 6]
    weights = [p / sum(points) for p in points]
    assert normalised_entropy(points) == pytest.approx(normalised_entropy(weights))


def test_entropy_accepts_a_mapping():
    assert normalised_entropy({f"d{i}": 1.0 for i in range(N_WEIGHTS)}) == pytest.approx(1.0)


def test_sparser_weights_have_lower_entropy():
    spread = [0.20, 0.18, 0.16, 0.14, 0.12, 0.11, 0.09]
    concentrated = [0.70, 0.10, 0.08, 0.05, 0.04, 0.02, 0.01]
    assert normalised_entropy(concentrated) < normalised_entropy(spread)


def test_distance_is_zero_at_the_center():
    assert distance_from_center(UNIFORM) == pytest.approx(0.0)


def test_distance_hits_its_bound_at_a_corner():
    assert distance_from_center(CORNER) == pytest.approx(max_distance_from_center())
    assert max_distance_from_center(7) == pytest.approx(math.sqrt(6 / 7))


def test_distance_needs_no_fitted_center():
    """硬规则 21 的要害：分层变量必须**事前可算**。

    `d_i = ‖w_i − ŵ_center‖` 依赖跑完才有的 ŵ_center，不能用来分层。这个
    函数只接受一个可选的常数中心：默认是单纯形几何中心，非对称 Dirichlet
    下应当显式传注入均值（DECISIONS/0006）。两者都在跑之前就定死，签名里
    没有任何拟合出来的东西。
    """
    import inspect

    sig = inspect.signature(distance_from_center)
    assert set(sig.parameters) == {"weights", "center", "n"}
    assert sig.parameters["center"].default is None, "中心必须可选，默认几何中心"


def test_an_explicit_center_shifts_the_distance():
    """非对称 Dirichlet 的均值不是 1/7，继续用 1/7 会给所有人加一个固定偏移。"""
    injected = [1 / 3] + [1 / 9] * 6
    w = [0.30, 0.20, 0.15, 0.12, 0.10, 0.08, 0.05]
    assert distance_from_center(w) != pytest.approx(distance_from_center(w, center=injected))
    assert distance_from_center(injected, center=injected) == pytest.approx(0.0)


def test_a_mismatched_center_is_refused():
    with pytest.raises(ValueError, match="维数"):
        distance_from_center(UNIFORM, center=[0.5, 0.5])


def test_entropy_and_distance_disagree_enough_to_matter():
    """两者都可作分层变量(C11b),但不是同一个量,选哪个是一个决定。"""
    a = [0.40, 0.40, 0.05, 0.05, 0.04, 0.03, 0.03]
    b = [0.55, 0.10, 0.09, 0.09, 0.06, 0.06, 0.05]
    assert normalised_entropy(a) < normalised_entropy(b)
    assert distance_from_center(a) < distance_from_center(b)


def test_degenerate_inputs_are_refused():
    with pytest.raises(ValueError, match="为空"):
        normalised_entropy([])
    with pytest.raises(ValueError, match="必须为正"):
        normalised_entropy([0.0] * N_WEIGHTS)
    with pytest.raises(ValueError, match="不能为负"):
        distance_from_center([-0.1, 1.1] + [0.0] * 5)
