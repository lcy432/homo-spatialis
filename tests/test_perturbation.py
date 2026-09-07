"""问卷粒度扰动的三条约束，以及方向性推力诊断。"""

from __future__ import annotations

import numpy as np
import pytest

from common.weights import normalised_entropy
from eval.perturbation import (
    CAP_POINTS,
    caps_for,
    diagnose,
    sample_symmetric_rejected,
    sample_truncated,
)

# 一个典型的 Dirichlet(1.5, 0.5×6) 人设：几个维度分数很少，边界会咬。
POINTS = np.array([33.0, 0.4, 21.0, 2.1, 8.0, 31.0, 4.5])


@pytest.fixture
def rng():
    return np.random.default_rng(0)


# --------------------------------------------------------------------------
# 三条约束
# --------------------------------------------------------------------------


def test_truncated_draws_satisfy_all_three_constraints(rng):
    d = sample_truncated(POINTS, 300, rng)
    assert np.abs(d.sum(axis=1)).max() < 1e-9, "总和守恒"
    assert (np.abs(d) <= caps_for(POINTS) + 1e-12).all(), "逐维幅度"
    assert (POINTS + d >= -1e-12).all(), "权重非负"


def test_the_cap_never_exceeds_the_available_points():
    """非负性由构造保证，不靠事后过滤。"""
    caps = caps_for(POINTS)
    assert (caps <= POINTS + 1e-12).all()
    assert (caps <= CAP_POINTS + 1e-12).all()
    assert caps[0] == CAP_POINTS  # 分数充足的维度拿到满幅
    assert caps[1] == pytest.approx(0.4)  # 只有 0.4 分的维度只能动 0.4


def test_the_polytope_is_symmetric_so_the_mean_is_zero(rng):
    """截断是**两侧同时**做的，多面体因此在 δ → −δ 下对称，均匀分布的
    均值恰好为零。只截下侧就破坏对称，退化成方案甲。"""
    d = sample_truncated(POINTS, 4000, rng)
    se = d.std(axis=0) / np.sqrt(len(d))
    assert (np.abs(d.mean(axis=0)) < 4 * se).all(), "均值应当落在噪声之内"


def test_draws_are_near_independent(rng):
    d = sample_truncated(POINTS, 800, rng)
    lag1 = np.corrcoef(d[:-1, 0], d[1:, 0])[0, 1]
    assert abs(lag1) < 0.15, f"滞后一自相关 {lag1:+.3f} 偏高，sweeps 要加大"


# --------------------------------------------------------------------------
# 方向性推力：这是选方案的判据
# --------------------------------------------------------------------------


def test_a_zero_mean_perturbation_lowers_entropy_by_concavity(rng):
    """H_norm 对 w 是凹的，所以零均值扰动按 Jensen 应当**降低**期望熵。
    这是判断"有没有方向性推力"的理论基准，不是经验观察。"""
    even = np.full(7, 100.0 / 7)  # 各维分数充足，边界不咬
    d = sample_truncated(even, 2000, rng)
    base = normalised_entropy(even)
    mean_dh = np.mean([normalised_entropy(even + x) - base for x in d])
    assert mean_dh < 0


def test_rejection_flips_the_sign_and_that_is_why_it_is_not_used(rng):
    """方案甲的实测：拒绝在一个非对称事件上做条件（小维度不能往下走），
    条件分布因此不是零均值，净效果是推向均匀=推向中心。

    而"推向中心"正是本项目要测的东西，标尺不能带着它。这条测试固定这个
    事实，免得将来有人觉得"拒绝重抽当然保零均值"。
    """
    draws, rate = sample_symmetric_rejected(POINTS, 3000, rng)
    assert rate > 0.0
    base = normalised_entropy(POINTS)
    mean_dh = np.mean([normalised_entropy(POINTS + x) - base for x in draws])
    assert mean_dh > 0, "若这条失败，说明拒绝法不再有方向性推力，可以重新考虑它"


def test_truncation_does_not_flip_the_sign(rng):
    d = sample_truncated(POINTS, 3000, rng)
    base = normalised_entropy(POINTS)
    assert np.mean([normalised_entropy(POINTS + x) - base for x in d]) < 0


def test_diagnose_reports_both_schemes():
    # 三个都要有"分数不足五分"的维度，否则非负约束根本不咬，两个方案会
    # 给出同样的（零均值）结果，这条测试就什么都没测。
    rows = np.array(
        [
            POINTS,
            [55.0, 1.0, 18.0, 0.6, 12.0, 11.0, 2.4],
            [70.0, 0.5, 0.8, 20.0, 3.2, 4.0, 1.5],
        ]
    )
    quiet = diagnose(rows, "per_dimension_symmetric_truncation", draws_per_persona=200, seed=1)
    loud = diagnose(rows, "symmetric_with_rejection", draws_per_persona=200, seed=1)
    assert quiet.mean_delta_h_norm < 0 < loud.mean_delta_h_norm
    assert quiet.median_acceptance is None  # Gibbs 不拒绝
    assert loud.median_acceptance is not None
    assert "ΔH_norm" in str(quiet)
