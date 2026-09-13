"""ALR / logistic-normal 分支（P9 第四至七节）。

这个分支存在的理由是**构造上**保证 `0 < w_k < 1` 与 `Σw = 1`，而不是靠事后
修正。所以测试的重点是那两条在极端情形下也不破，以及权重空间的矩确实是在
权重空间实算的。

**它不解决可识别性。** 某一维在选择集内部几乎不动时，似然在该方向仍然是平的；
softmax 只是把那个任意值压回 `(0,1)` 里，不是把它测出来了。见
``test_alr_bounds_the_damage_from_an_unidentified_dimension_but_does_not_identify_it``。
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.optimize import approx_fprime

from eval.recovery import (
    ALR_COORD_CAP,
    RecoveryConfig,
    alr_from_weights,
    fit_recovery,
    population_moments,
    weights_from_alr,
)
from eval.recovery.alr import pack_alr, unpack_alr
from eval.recovery.msl import N_RANDOM, layout, standard_normal_draws
from eval.recovery.msl_alr import alr_objective, alr_posterior_at
from worlds.dimensions import ALL_DIMENSIONS, N_DIMENSIONS

from .synthetic import make_synthetic

ALL_BASES = tuple(range(N_DIMENSIONS))
FAST = RecoveryConfig(parameterization="alr_logistic_normal", n_draws=60, seed=3, maxiter=200)


# --------------------------------------------------------------------------
# 14.2 变换
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base", ALL_BASES)
def test_alr_round_trip_is_exact(base):
    rng = np.random.default_rng(base)
    w = rng.dirichlet(np.ones(N_DIMENSIONS), size=400)
    assert np.abs(weights_from_alr(alr_from_weights(w, base), base) - w).max() < 1e-12


@pytest.mark.parametrize("base", ALL_BASES)
def test_every_weight_is_strictly_inside_the_simplex_by_construction(base):
    rng = np.random.default_rng(100 + base)
    a = rng.normal(scale=8.0, size=(2000, N_RANDOM))
    w = weights_from_alr(a, base)
    assert np.all(w > 0.0)
    assert np.all(w < 1.0)
    assert np.abs(w.sum(axis=1) - 1.0).max() < 1e-12


@pytest.mark.parametrize("base", [0, 3, 6])
def test_extreme_coordinates_neither_overflow_nor_produce_nan(base):
    a = np.array(
        [
            [0.0] * N_RANDOM,
            [1e4] * N_RANDOM,
            [-1e4] * N_RANDOM,
            [1e4, -1e4, 0.0, 700.0, -700.0, 50.0],
        ]
    )
    w = weights_from_alr(a, base)
    assert np.all(np.isfinite(w))
    assert np.all(w >= 0.0)
    assert np.abs(w.sum(axis=1) - 1.0).max() < 1e-12
    # 软截断之后仍然严格为正，不会退化成恰好 0（那会让 ALR 的逆没有定义）
    assert np.all(weights_from_alr(np.full((1, N_RANDOM), -ALR_COORD_CAP), base) > 0.0)


def test_the_same_weights_are_representable_from_any_base_dimension():
    rng = np.random.default_rng(7)
    w = rng.dirichlet(np.ones(N_DIMENSIONS), size=50)
    recovered = [weights_from_alr(alr_from_weights(w, b), b) for b in ALL_BASES]
    for r in recovered:
        assert np.abs(r - w).max() < 1e-12


def test_utility_is_the_same_under_every_base_dimension():
    """同一组 (w, x, σ)，换基准维不改变系统效用——ALR 只是换坐标。"""
    rng = np.random.default_rng(11)
    w = rng.dirichlet(np.ones(N_DIMENSIONS))
    x = rng.normal(size=(20, N_DIMENSIONS))
    gamma_0 = 9.0
    reference = gamma_0 * (x @ w)
    for b in ALL_BASES:
        back = weights_from_alr(alr_from_weights(w[None, :], b), b)[0]
        assert np.abs(gamma_0 * (x @ back) - reference).max() < 1e-11


def test_alr_is_undefined_for_non_positive_weights():
    """这正是它与差分正态分支的分界，不该被悄悄绕过。"""
    with pytest.raises(ValueError, match="严格为正"):
        alr_from_weights(np.array([0.5, -0.1, 0.2, 0.1, 0.1, 0.1, 0.1]), 0)


def test_the_cholesky_diagonal_is_positive_by_log_parameterization():
    """按规格用 log-diagonal，不取绝对值；差分分支的打包一字未动。"""
    chol = np.diag([0.3, 1.2, 0.7, 2.0, 0.1, 0.9])
    for cov in ("diagonal", "full"):
        theta = pack_alr(1.5, np.zeros(N_RANDOM), chol, cov)
        _, _, back = unpack_alr(theta, cov)
        assert np.all(np.diag(back) > 0.0)
        assert np.allclose(np.diag(back), np.diag(chol))
    # 任意实数参数都给出正对角线
    wild = np.concatenate([[2.0], np.zeros(N_RANDOM), np.full(N_RANDOM, -30.0)])
    assert np.all(np.diag(unpack_alr(wild, "diagonal")[2]) > 0.0)


# --------------------------------------------------------------------------
# 14.3 梯度：四类参数分别核验
# --------------------------------------------------------------------------


@pytest.mark.parametrize("covariance", ["diagonal", "full"])
@pytest.mark.parametrize("seed", [5, 11])
def test_the_analytic_gradient_matches_finite_differences(covariance, seed):
    s = make_synthetic(n_personas=25, n_tasks=4, n_alts=5, seed=3)
    lay = layout(s.dataset, 0)
    xi = standard_normal_draws(s.dataset.n_personas, 40, N_RANDOM, seed=seed)
    n_par = 1 + N_RANDOM + (N_RANDOM if covariance == "diagonal" else 21)
    rng = np.random.default_rng(seed)

    for _ in range(2):
        theta = rng.normal(0.0, 0.6, size=n_par)
        theta[0] = rng.uniform(1.5, 3.0)  # ρ = log γ_0，非零点
        numeric = approx_fprime(
            theta, lambda t: alr_objective(t, lay, xi, 0, covariance, 16)[0], 1e-6
        )
        analytic = alr_objective(theta, lay, xi, 0, covariance, 16)[1]
        assert abs(analytic[0] - numeric[0]) < 1e-3, "ρ"
        assert np.abs(analytic[1 : 1 + N_RANDOM] - numeric[1 : 1 + N_RANDOM]).max() < 1e-3, "μ_a"
        assert np.abs(analytic[1 + N_RANDOM :] - numeric[1 + N_RANDOM :]).max() < 1e-3, "L"


# --------------------------------------------------------------------------
# 14.2 协方差与总体矩
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base", [0, 2, 6])
@pytest.mark.parametrize("covariance", ["diagonal", "full"])
def test_population_moments_are_computed_in_weight_space(base, covariance):
    rng = np.random.default_rng(base)
    mu = rng.normal(size=N_RANDOM)
    chol = np.diag(rng.uniform(0.3, 1.2, N_RANDOM))
    if covariance == "full":
        chol = np.tril(rng.normal(size=(N_RANDOM, N_RANDOM)) * 0.3) + chol

    center, cov, draws = population_moments(mu, chol, base, n=60_000, seed=1)

    assert np.all(draws > 0.0) and np.all(draws < 1.0)
    assert np.abs(draws.sum(axis=1) - 1.0).max() < 1e-12
    assert center.sum() == pytest.approx(1.0, abs=1e-12)
    assert np.allclose(cov, cov.T, atol=1e-14)
    assert np.abs(cov @ np.ones(N_DIMENSIONS)).max() < 1e-12
    assert np.linalg.matrix_rank(cov, tol=1e-10 * np.abs(cov).max()) <= N_DIMENSIONS - 1
    eig = np.linalg.eigvalsh(cov)
    assert eig.min() > -1e-12 * max(1.0, float(np.abs(eig).max()))
    # 与直接从抽样点算的矩一致：说明没有偷偷用 delta 近似
    assert np.abs(center - draws.mean(axis=0)).max() < 1e-14


# --------------------------------------------------------------------------
# 拟合与 14.4 后验
# --------------------------------------------------------------------------


def test_an_alr_fit_converges_and_produces_legal_output(synthetic):
    result = fit_recovery(synthetic.dataset, FAST)
    d = result.diagnostics

    assert result.parameterization == "alr_logistic_normal"
    assert d.converged, d.message
    assert result.gamma_0 > 0 and np.isfinite(result.sigma_hat)
    assert d.covariance.rank == N_DIMENSIONS - 1
    assert d.covariance.positive_semidefinite
    assert d.covariance.max_abs_row_sum < 1e-12

    assert np.all(result.w_hat > 0.0) and np.all(result.w_hat < 1.0)
    assert np.abs(result.w_hat.sum(axis=1) - 1.0).max() < 1e-12
    assert d.individual_simplex.fraction_outside == 0.0
    assert d.population_simplex.fraction_outside == 0.0
    assert result.w_center_hat.sum() == pytest.approx(1.0, abs=1e-12)


def test_the_posterior_averages_weights_not_coordinates():
    """E[w(a)|y] ≠ w(E[a|y])。softmax 非线性，两者必须能被区分开。"""
    s = make_synthetic(n_personas=20, n_tasks=5, n_alts=5, seed=13)
    mu = np.linspace(-1.0, 1.0, N_RANDOM)
    chol = np.diag(np.full(N_RANDOM, 1.1))
    post = alr_posterior_at(s.dataset, 0, 2.4, mu, chol, n_draws=150, seed=2)

    assert np.all(post.weight_mean > 0.0)
    assert np.abs(post.weight_mean.sum(axis=1) - 1.0).max() < 1e-12

    naive = weights_from_alr(post.latent_mean, 0)  # w(E[a|y])，**不该**是产出
    assert np.abs(post.weight_mean - naive).max() > 1e-4
    assert not np.allclose(post.weight_mean, naive)


def test_the_posterior_uses_every_task_of_the_persona():
    from eval.recovery import ChoiceDataset

    s = make_synthetic(n_personas=6, n_tasks=4, n_alts=4, seed=17)
    d = s.dataset
    mu, chol = np.zeros(N_RANDOM), np.diag(np.full(N_RANDOM, 1.0))
    full = alr_posterior_at(d, 0, 2.3, mu, chol, n_draws=80, seed=1)

    drop = d.choice_set_ids[0]
    keep = [i for i in range(d.n_rows) if d.choice_set_ids[i] != drop]
    trimmed = ChoiceDataset.build(
        persona_ids=[d.persona_ids[i] for i in keep],
        choice_set_ids=[d.choice_set_ids[i] for i in keep],
        alternative_ids=[d.alternative_ids[i] for i in keep],
        chosen=d.chosen[keep],
        features=d.features[keep],
    )
    partial = alr_posterior_at(trimmed, 0, 2.3, mu, chol, n_draws=80, seed=1)
    who = trimmed.persona_order.index(d.persona_ids[0])
    assert np.abs(full.weight_mean[who] - partial.weight_mean[who]).max() > 1e-6


def test_the_alr_posterior_is_reproducible_under_a_fixed_seed():
    s = make_synthetic(n_personas=10, n_tasks=4, n_alts=4, seed=31)
    mu, chol = np.zeros(N_RANDOM), np.diag(np.full(N_RANDOM, 0.9))
    a = alr_posterior_at(s.dataset, 0, 2.5, mu, chol, n_draws=70, seed=9)
    b = alr_posterior_at(s.dataset, 0, 2.5, mu, chol, n_draws=70, seed=9)
    c = alr_posterior_at(s.dataset, 0, 2.5, mu, chol, n_draws=70, seed=10)
    assert np.array_equal(a.weight_mean, b.weight_mean)
    assert not np.allclose(a.weight_mean, c.weight_mean)


@pytest.mark.parametrize("base_dim", ["material", "leisure"])
def test_any_legal_base_dimension_produces_a_legal_alr_fit(base_dim, small):
    from dataclasses import replace

    result = fit_recovery(small.dataset, replace(FAST, base_dim=base_dim))
    assert result.base_dim == base_dim
    assert np.all(result.w_hat > 0.0)
    # 规格要求的是 rank ≤ 6，不是 == 6。小样本上优化器可以把 Σ_a 的某几个对角
    # 元推到边界（见下一条测试），那时 Σ_w 的秩更低——那是合法的退化解，不是
    # 非法的协方差。样本够大时秩就是 6，由 `test_an_alr_fit_converges_...` 盯着。
    report = result.diagnostics.covariance
    assert 1 <= report.rank <= N_DIMENSIONS - 1
    assert report.positive_semidefinite
    assert report.max_abs_row_sum < 1e-12


def test_the_full_covariance_spec_fits_in_the_alr_branch(small):
    from dataclasses import replace

    full = fit_recovery(small.dataset, replace(FAST, covariance="full"))
    assert full.diagnostics.converged, full.diagnostics.message
    assert np.count_nonzero(np.triu(full.sigma_gamma, 1)) > 0
    assert np.all(np.diag(full.sigma_gamma) > 0.0)
    assert full.diagnostics.covariance.rank == N_DIMENSIONS - 1


def test_an_unknown_parameterization_is_rejected(small):
    from dataclasses import replace

    with pytest.raises(ValueError, match="未知参数化"):
        fit_recovery(small.dataset, replace(FAST, parameterization="something_else"))


def test_alr_bounds_the_damage_from_an_unidentified_dimension_but_does_not_identify_it():
    """**这一条钉住的是一句不能写错的话。**

    把 commute 压成组内几乎不动（M3 真实世界里的情形），ALR 的权重仍然全部
    合法，而且不会像差分分支那样把大块权重甩到那一维上——但它对该维的回收
    相关**依然接近零**。合法 ≠ 可识别。
    """
    s = make_synthetic(n_personas=60, n_tasks=6, n_alts=6, sigma=0.08, seed=211)
    dead = ALL_DIMENSIONS.index("commute")
    crippled = s.dataset.features.copy()
    crippled[:, dead] = 3.0 + 1e-3 * crippled[:, dead]

    from eval.recovery import ChoiceDataset

    ds = ChoiceDataset.build(
        persona_ids=list(s.dataset.persona_ids),
        choice_set_ids=list(s.dataset.choice_set_ids),
        alternative_ids=list(s.dataset.alternative_ids),
        chosen=s.dataset.chosen,
        features=crippled,
    )
    result = fit_recovery(ds, FAST)

    assert np.all(result.w_hat > 0.0) and np.all(result.w_hat < 1.0)
    assert result.diagnostics.individual_simplex.fraction_outside == 0.0
    assert result.diagnostics.weakly_identified_dimensions == ("commute",)

    corr = abs(float(np.corrcoef(result.w_hat[:, dead], s.true_weights[:, dead])[0, 1]))
    assert corr < 0.25, f"commute 不该被回收出来，实测相关 {corr:.3f}"


def test_a_boundary_solution_lowers_the_rank_instead_of_producing_an_illegal_matrix():
    """log-diagonal 参数化在边界上的已知行为，写下来免得下次当成 bug。

    样本小时优化器可以把 ``Σ_a`` 的某几个对角元推到数值上可忽略的量级
    （``L_mm = exp(p)``，p 很负）。那一维在总体上就几乎没有异质性了，于是
    ``Σ_w`` 的数值秩低于 6。矩阵**仍然合法**——对称、半正定、行和为零——秩由
    ``CovarianceReport`` 如实报出来。

    附带一条必须知道的性质：``∂/∂p = (∂/∂L)·L``，所以 ``L`` 趋零时该坐标的
    梯度被同比例压扁，优化器实际上停在那里。这是边界解，不是收敛失败。
    """
    from dataclasses import replace

    s = make_synthetic(n_personas=30, n_tasks=4, n_alts=5, sigma=0.1, seed=5)
    result = fit_recovery(s.dataset, replace(FAST, base_dim="leisure"))

    report = result.diagnostics.covariance
    assert report.rank < N_DIMENSIONS - 1  # 确实退化了
    assert report.positive_semidefinite
    assert report.max_abs_row_sum < 1e-12
    diag = np.diag(result.sigma_gamma)
    assert np.all(diag >= 0.0)
    # 不是恰好零，是被压到可忽略：与仍在起作用的那些差十个数量级以上
    assert diag.min() < 1e-8 * diag.max()
    assert result.diagnostics.converged
    assert np.all(result.w_hat > 0.0)  # 权重照样合法
