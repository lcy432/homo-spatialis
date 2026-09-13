"""P9 的纯数学部分：差分、反解、协方差回构。不跑优化器。

这一层必须能在**不重新跑一次 MSL** 的情况下单测，所以 design/inversion 两个
模块不认识任何估计库。
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.recovery import (
    BaseDimensionError,
    DegenerateScaleError,
    base_index,
    check_gamma_0,
    design_column_names,
    design_matrix,
    differenced,
    differenced_utility,
    expansion_matrix,
    is_positive_semidefinite,
    numerical_rank,
    other_indices,
    sigma_q_from_sigma_gamma,
    sigma_w_from_sigma_gamma,
    simplex_violation,
    systematic_utility,
    weights_from_gamma,
)
from worlds.dimensions import ALL_DIMENSIONS, N_DIMENSIONS
from worlds.personas import dirichlet_alpha_vector, dirichlet_covariance, dirichlet_mean

ALL_BASES = tuple(range(N_DIMENSIONS))


# --------------------------------------------------------------------------
# 一、原始效用与差分效用在所有基准维下相等
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base", ALL_BASES)
def test_differencing_is_an_identity_not_an_approximation(base):
    rng = np.random.default_rng(base)
    x = rng.normal(size=(40, N_DIMENSIONS))
    w = rng.dirichlet(np.ones(N_DIMENSIONS))

    xb, z = differenced(x, base)
    others = list(other_indices(base))
    direct = systematic_utility(x, w)
    via_difference = differenced_utility(xb, z, w[others])

    assert np.allclose(direct, via_difference, atol=1e-13)


def test_differencing_needs_the_weights_to_sum_to_one():
    """和不为一时两式相差一个常数——这正是 w_b = 1 − Σ 那一步的内容。"""
    rng = np.random.default_rng(1)
    x = rng.normal(size=(10, N_DIMENSIONS))
    w = rng.random(N_DIMENSIONS)  # 不归一
    xb, z = differenced(x, 0)
    gap = systematic_utility(x, w) - differenced_utility(xb, z, w[1:])
    assert np.allclose(gap, (w.sum() - 1.0) * xb)


@pytest.mark.parametrize("base", ALL_BASES)
def test_design_columns_are_one_base_level_and_six_differences(base):
    names = design_column_names(base)
    assert names[0] == ALL_DIMENSIONS[base]
    assert len(names) == N_DIMENSIONS
    assert all(n.endswith(f"-{ALL_DIMENSIONS[base]}") for n in names[1:])
    x = np.random.default_rng(2).normal(size=(5, N_DIMENSIONS))
    assert design_matrix(x, base).shape == (5, N_DIMENSIONS)


def test_illegal_base_dimension_is_rejected_loudly():
    with pytest.raises(BaseDimensionError):
        base_index("not_a_dimension")
    with pytest.raises(BaseDimensionError):
        base_index(N_DIMENSIONS)
    assert base_index("material") == 0
    assert base_index(3) == 3


# --------------------------------------------------------------------------
# 二、反解是精确的
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base", ALL_BASES)
def test_known_gamma_inverts_to_the_original_weights(base):
    rng = np.random.default_rng(100 + base)
    w = rng.dirichlet(np.ones(N_DIMENSIONS))
    sigma = 0.07
    gamma_0 = 1.0 / sigma
    gamma_others = w[list(other_indices(base))] / sigma

    recovered = weights_from_gamma(gamma_0, gamma_others, base)
    assert np.allclose(recovered, w, atol=1e-13)
    assert abs(recovered.sum() - 1.0) < 1e-14


@pytest.mark.parametrize("base", ALL_BASES)
def test_recovered_weights_always_sum_to_one_even_when_absurd(base):
    """和为一是构造性质，不依赖 γ 取值是否合理。"""
    rng = np.random.default_rng(7)
    gamma = rng.normal(scale=5.0, size=(50, N_DIMENSIONS - 1))
    w = weights_from_gamma(3.0, gamma, base)
    assert w.shape == (50, N_DIMENSIONS)
    assert np.allclose(w.sum(axis=1), 1.0, atol=1e-13)


def test_degenerate_scale_fails_instead_of_taking_absolute_values():
    for bad in (0.0, -2.0, -1e-12, float("nan")):
        with pytest.raises(DegenerateScaleError):
            check_gamma_0(bad)
    assert check_gamma_0(1e-3) == 1e-3


# --------------------------------------------------------------------------
# 三、七维协方差合法且秩至多 6
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base", ALL_BASES)
def test_expansion_matrix_annihilates_the_all_ones_vector(base):
    b = expansion_matrix(base)
    assert b.shape == (N_DIMENSIONS, N_DIMENSIONS - 1)
    assert np.allclose(b.T @ np.ones(N_DIMENSIONS), 0.0)


@pytest.mark.parametrize("base", ALL_BASES)
@pytest.mark.parametrize("mode", ["diagonal", "full"])
def test_sigma_w_is_symmetric_psd_rank_six_and_row_sums_zero(base, mode):
    rng = np.random.default_rng(base * 10 + len(mode))
    if mode == "diagonal":
        sigma_gamma = np.diag(rng.uniform(0.2, 2.0, size=N_DIMENSIONS - 1))
    else:
        a = rng.normal(size=(N_DIMENSIONS - 1, N_DIMENSIONS - 1))
        sigma_gamma = a @ a.T
    gamma_0 = 9.0

    sigma_w = sigma_w_from_sigma_gamma(sigma_gamma, gamma_0, base)
    ones = np.ones(N_DIMENSIONS)

    assert np.allclose(sigma_w, sigma_w.T, atol=1e-14)
    assert is_positive_semidefinite(sigma_w)
    assert numerical_rank(sigma_w) == N_DIMENSIONS - 1
    assert np.abs(sigma_w @ ones).max() < 1e-12
    assert abs(ones @ sigma_w @ ones) < 1e-12


@pytest.mark.parametrize("base", [0, 4])
def test_the_covariance_transform_matches_monte_carlo(base):
    """Σ_w = B·(Σ_γ/γ_0²)·Bᵀ 是不是真的那个变换——用抽样验一遍。"""
    rng = np.random.default_rng(42)
    sigma_gamma = np.diag(np.array([1.3, 0.4, 0.9, 2.1, 0.6, 1.7]))
    gamma_0 = 8.0
    mu = np.array([2.0, 1.0, 1.5, 0.8, 1.2, 0.9])

    chol = np.linalg.cholesky(sigma_gamma)
    draws = mu + rng.standard_normal((400_000, N_DIMENSIONS - 1)) @ chol.T
    w = weights_from_gamma(gamma_0, draws, base)

    empirical = np.cov(w, rowvar=False)
    analytic = sigma_w_from_sigma_gamma(sigma_gamma, gamma_0, base)
    assert np.abs(empirical - analytic).max() < 5e-4

    empirical_q = np.cov(draws / gamma_0, rowvar=False)
    assert np.abs(empirical_q - sigma_q_from_sigma_gamma(sigma_gamma, gamma_0)).max() < 5e-4


# --------------------------------------------------------------------------
# 四、注入协方差的 fixture 用当前的非对称 Dirichlet
# --------------------------------------------------------------------------


def test_injected_covariance_uses_the_asymmetric_dirichlet_in_force():
    """`DECISIONS/0006`：α = (1.5, 0.5×6)，**对角线不再是常数**。"""
    alpha = np.asarray(dirichlet_alpha_vector(), dtype=float)
    assert alpha[0] == 1.5
    assert np.all(alpha[1:] == 0.5)

    sigma = dirichlet_covariance()
    a0 = alpha.sum()
    denom = a0**2 * (a0 + 1.0)
    expected = -np.outer(alpha, alpha) / denom
    np.fill_diagonal(expected, alpha * (a0 - alpha) / denom)

    assert np.allclose(sigma, expected, atol=1e-15)
    assert sigma[0, 0] != pytest.approx(sigma[1, 1])  # 非对称的直接后果
    assert np.allclose(dirichlet_mean(), alpha / a0)


def test_injected_covariance_is_rank_six_with_zero_row_sums():
    """当前的正式验收判据：Σ·1 = 0 与数值秩 6。对 α 是否对称都成立。"""
    sigma = dirichlet_covariance()
    assert np.abs(sigma @ np.ones(N_DIMENSIONS)).max() < 1e-15
    assert numerical_rank(sigma) == N_DIMENSIONS - 1
    assert is_positive_semidefinite(sigma)


def test_the_symmetric_identity_is_a_historical_fixture_only():
    """`7·Var + 42·Cov = 0` 只在对称情形成立，不能当作当前的唯一验收测试。"""
    symmetric = dirichlet_covariance([0.5] * N_DIMENSIONS)
    assert 7 * symmetric[0, 0] + 42 * symmetric[0, 1] == pytest.approx(0.0, abs=1e-15)

    current = dirichlet_covariance()
    # 物质维那一行**碰巧**仍然满足它：该行的六个非对角元都相等（六个舒适度维
    # 的 α 相同），于是 7·Var + 42·Cov 退化成 7·(行和) = 0。只看这一行会以为
    # 旧 fixture 还管用。
    assert 7 * current[0, 0] + 42 * current[0, 1] == pytest.approx(0.0, abs=1e-15)
    # 舒适度维那一行就不满足了：它的非对角元不再全相等（与物质维那个不同）。
    assert 7 * current[1, 1] + 42 * current[1, 2] != pytest.approx(0.0, abs=1e-6)
    # 一般式对每一行都成立：行和为零
    assert np.abs(current.sum(axis=1)).max() < 1e-15


# --------------------------------------------------------------------------
# 五、越界只统计，不修正
# --------------------------------------------------------------------------


def test_simplex_violation_counts_without_touching_the_values():
    w = np.array([[0.5, 0.5], [-0.2, 1.2], [0.3, 0.7]])
    before = w.copy()
    report = simplex_violation(w)
    assert np.array_equal(w, before)
    assert report["fraction_outside"] == pytest.approx(1 / 3)
    assert report["max_below_zero"] == pytest.approx(0.2)
    assert report["max_above_one"] == pytest.approx(0.2)
    assert report["max_magnitude"] == pytest.approx(0.2)
