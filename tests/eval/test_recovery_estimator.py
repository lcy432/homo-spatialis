"""估计器集成测试与个体后验（P9 第十一节的后两组）。

混合 logit 在小样本下有偏（README §10.3、§11.7.2：这正是 P8 要量化的东西），
所以这里**不写逐元素精确断言**。要检的是：优化收敛、方向合理、输出有限、
和为一、协方差合法、信息量增加时回收不系统性变差。
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.optimize import approx_fprime

from eval.recovery import (
    ChoiceDataset,
    RecoveryConfig,
    base_dimension_spread,
    fit_base_dimension_robustness,
    fit_conditional_logit,
    fit_recovery,
    layout,
    standard_normal_draws,
)
from eval.recovery.msl import N_RANDOM, _msl_objective, pack, posterior_at
from worlds.dimensions import ALL_DIMENSIONS, N_DIMENSIONS

from .synthetic import make_synthetic

FAST = RecoveryConfig(n_draws=60, seed=3, maxiter=200)


# --------------------------------------------------------------------------
# 似然与梯度
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["diagonal", "full"])
def test_the_analytic_gradient_matches_finite_differences(mode):
    """解析梯度是这个后端能在 bootstrap 预算内跑完的唯一原因，必须验。"""
    s = make_synthetic(n_personas=25, n_tasks=4, n_alts=5, seed=3)
    lay = layout(s.dataset, 0)
    xi = standard_normal_draws(s.dataset.n_personas, 40, N_RANDOM, seed=5)
    rng = np.random.default_rng(0)
    theta = rng.normal(0.0, 0.5, size=1 + N_RANDOM + (6 if mode == "diagonal" else 21))
    theta[0] = 4.0

    numeric = approx_fprime(theta, lambda t: _msl_objective(t, lay, xi, mode, 16)[0], 1e-6)
    analytic = _msl_objective(theta, lay, xi, mode, 16)[1]
    assert np.abs(analytic - numeric).max() < 1e-4


def test_draw_batching_does_not_change_the_likelihood():
    s = make_synthetic(n_personas=15, n_tasks=3, n_alts=4, seed=1)
    lay = layout(s.dataset, 0)
    xi = standard_normal_draws(s.dataset.n_personas, 37, N_RANDOM, seed=2)
    theta = pack(5.0, np.full(N_RANDOM, 0.3), np.diag(np.full(N_RANDOM, 0.4)), "diagonal")
    values = [_msl_objective(theta, lay, xi, "diagonal", b)[0] for b in (1, 7, 32, 64)]
    assert max(values) - min(values) == 0.0


def test_the_conditional_logit_start_is_itself_a_working_fit():
    s = make_synthetic(n_personas=40, n_tasks=4, n_alts=5, seed=8)
    lay = layout(s.dataset, 0)
    gamma_0, mu, loglik, ok = fit_conditional_logit(lay)
    assert ok and np.isfinite(loglik)
    assert gamma_0 > 0
    assert np.all(np.isfinite(mu))
    # 池化 logit 已经该把大部分权重放在物质维上（注入 α₁ = 1.5）
    assert gamma_0 > mu.max() * 0.5


# --------------------------------------------------------------------------
# 拟合
# --------------------------------------------------------------------------


def test_a_full_fit_converges_and_produces_legal_output(synthetic):
    result = fit_recovery(synthetic.dataset, FAST)
    d = result.diagnostics

    assert d.converged, d.message
    assert np.isfinite(d.loglik)
    assert d.loglik >= d.loglik_conditional_logit  # 混合 logit 嵌套了池化 logit

    assert result.gamma_0 > 0
    assert result.sigma_hat == pytest.approx(1.0 / result.gamma_0)
    assert np.all(np.isfinite(result.w_center_hat))
    assert result.w_center_hat.sum() == pytest.approx(1.0, abs=1e-12)
    assert np.all(np.isfinite(result.w_hat))
    assert np.abs(result.w_hat.sum(axis=1) - 1.0).max() < 1e-12

    assert d.covariance.rank == N_DIMENSIONS - 1
    assert d.covariance.positive_semidefinite
    assert d.covariance.max_abs_row_sum < 1e-12
    assert abs(d.covariance.quadratic_form) < 1e-12
    assert d.design_condition_number < 30.0


def test_the_recovered_scale_and_centre_point_in_the_right_direction(synthetic):
    """方向合理即可：σ 同量级、物质维仍是最大的一维、中心与注入均值同序。"""
    result = fit_recovery(synthetic.dataset, FAST)
    assert 0.3 * synthetic.sigma < result.sigma_hat < 3.0 * synthetic.sigma

    injected = synthetic.true_weights.mean(axis=0)
    assert int(np.argmax(result.w_center_hat)) == int(np.argmax(injected)) == 0
    assert np.abs(result.w_center_hat - injected).max() < 0.12


def test_individual_recovery_correlates_with_the_injected_weights(synthetic):
    result = fit_recovery(synthetic.dataset, FAST)
    corr = [
        float(np.corrcoef(result.w_hat[:, k], synthetic.true_weights[:, k])[0, 1])
        for k in range(N_DIMENSIONS)
    ]
    assert min(corr) > 0.15, corr
    assert float(np.mean(corr)) > 0.35, corr


def test_more_information_does_not_make_recovery_systematically_worse():
    """每人多做几次选择，个体回收的相关应当不下降。

    这是对整条流水线的一致性检查：若面板聚合写错（例如把每次选择当成一个
    独立个体），增加任务数不会改善个体后验，甚至可能变差。
    """

    def mean_corr(n_tasks: int) -> float:
        s = make_synthetic(n_personas=90, n_tasks=n_tasks, n_alts=6, sigma=0.1, seed=21)
        r = fit_recovery(s.dataset, RecoveryConfig(n_draws=60, seed=1, maxiter=200))
        return float(
            np.mean(
                [
                    np.corrcoef(r.w_hat[:, k], s.true_weights[:, k])[0, 1]
                    for k in range(N_DIMENSIONS)
                ]
            )
        )

    assert mean_corr(8) > mean_corr(3) - 0.02


# --------------------------------------------------------------------------
# 基准维与协方差模式
# --------------------------------------------------------------------------


@pytest.mark.parametrize("base_dim", ["material", "safety", "commute"])
def test_any_legal_base_dimension_produces_a_legal_fit(base_dim, small):
    from dataclasses import replace

    result = fit_recovery(small.dataset, replace(FAST, base_dim=base_dim))
    assert result.base_dim == base_dim
    assert result.w_center_hat.sum() == pytest.approx(1.0, abs=1e-12)
    assert result.diagnostics.covariance.rank == N_DIMENSIONS - 1


def test_base_dimension_sensitivity_is_reported_not_hidden(synthetic):
    results = fit_base_dimension_robustness(
        synthetic.dataset, FAST, base_dims=("material", "safety", "environment")
    )
    assert set(results) == {"material", "safety", "environment"}
    spread = base_dimension_spread(results)
    assert set(spread) == {
        "max_abs_center_range",
        "max_abs_variance_range",
        "sigma_hat_min",
        "sigma_hat_max",
    }
    # 差异存在是模型的性质（对角 Σ_γ 不是基准维不变的）；测的是它被报出来了
    assert spread["max_abs_center_range"] >= 0.0
    assert spread["sigma_hat_max"] >= spread["sigma_hat_min"]


def test_the_full_covariance_spec_fits_and_agrees_roughly_with_the_diagonal_one(small):
    from dataclasses import replace

    diagonal = fit_recovery(small.dataset, FAST)
    full = fit_recovery(small.dataset, replace(FAST, covariance="full"))

    assert full.diagnostics.converged, full.diagnostics.message
    assert full.sigma_gamma.shape == (N_RANDOM, N_RANDOM)
    assert np.count_nonzero(np.triu(full.sigma_gamma, 1)) > 0  # 真的估了非对角元
    assert full.diagnostics.covariance.rank == N_DIMENSIONS - 1
    # 完全协方差嵌套了对角协方差，所以似然不会更低
    assert full.diagnostics.loglik >= diagonal.diagnostics.loglik - 1e-6
    assert np.abs(full.w_center_hat - diagonal.w_center_hat).max() < 0.15


def test_out_of_simplex_weights_are_reported_and_left_alone(synthetic):
    result = fit_recovery(synthetic.dataset, FAST)
    pop = result.diagnostics.population_simplex
    ind = result.diagnostics.individual_simplex

    for report in (pop, ind):
        assert 0.0 <= report.fraction_outside <= 1.0
        assert report.max_magnitude >= 0.0
    # 报告说有越界，就必须真的能在数组里找到越界值——没有被悄悄 clip 掉
    if ind.fraction_outside > 0:
        assert (result.w_hat < 0).any() or (result.w_hat > 1).any()
    assert np.abs(result.w_hat.sum(axis=1) - 1.0).max() < 1e-12


# --------------------------------------------------------------------------
# 个体后验
# --------------------------------------------------------------------------


def _fixed_params(seed=0):
    rng = np.random.default_rng(seed)
    return 10.0, rng.uniform(0.5, 1.5, N_RANDOM), np.diag(rng.uniform(0.5, 1.5, N_RANDOM))


def test_the_posterior_uses_every_task_of_the_persona():
    """删掉一个人设的一次选择，他的后验必须变——否则就不是联合似然。"""
    s = make_synthetic(n_personas=6, n_tasks=4, n_alts=4, seed=13)
    d = s.dataset
    g0, mu, chol = _fixed_params()
    full = posterior_at(d, 0, g0, mu, chol, n_draws=80, seed=1)

    drop = d.choice_set_ids[0]
    keep = [i for i in range(d.n_rows) if d.choice_set_ids[i] != drop]
    trimmed = ChoiceDataset.build(
        persona_ids=[d.persona_ids[i] for i in keep],
        choice_set_ids=[d.choice_set_ids[i] for i in keep],
        alternative_ids=[d.alternative_ids[i] for i in keep],
        chosen=d.chosen[keep],
        features=d.features[keep],
    )
    partial = posterior_at(trimmed, 0, g0, mu, chol, n_draws=80, seed=1)

    who = trimmed.persona_order.index(d.persona_ids[0])
    assert np.abs(full.mean[who] - partial.mean[who]).max() > 1e-6


def test_one_personas_posterior_does_not_depend_on_another_personas_choices():
    """参数固定时，后验按人设分解。混在一起就说明 panel 聚合串了。"""
    s = make_synthetic(n_personas=5, n_tasks=3, n_alts=4, seed=17)
    d = s.dataset
    g0, mu, chol = _fixed_params(1)
    before = posterior_at(d, 0, g0, mu, chol, n_draws=60, seed=2)

    last = d.persona_order[-1]
    flipped = d.chosen.copy()
    rows = np.flatnonzero(np.array(d.persona_ids) == last)
    task = d.choice_set_ids[rows[0]]
    in_task = [i for i in rows if d.choice_set_ids[i] == task]
    flipped[in_task] = False
    flipped[in_task[-1]] = True

    other = ChoiceDataset.build(
        persona_ids=list(d.persona_ids),
        choice_set_ids=list(d.choice_set_ids),
        alternative_ids=list(d.alternative_ids),
        chosen=flipped,
        features=d.features,
    )
    after = posterior_at(other, 0, g0, mu, chol, n_draws=60, seed=2)

    assert np.allclose(before.mean[:-1], after.mean[:-1], atol=1e-12)
    assert not np.allclose(before.mean[-1], after.mean[-1], atol=1e-9)


def test_informative_choices_give_a_tighter_posterior_than_near_random_ones():
    """信息量由**备选之间差多少**决定，两臂的 γ 尺度保持一致。

    备选几乎一样时任何 γ 都给出同样的选择概率，似然对 draws 是平的，后验只能
    退回先验：sd → 先验 sd，有效抽样数 → n_draws。备选差得开时两者都下降。

    （不要改成"一臂 σ 小一臂 σ 大"：那会同时改变真值 γ = w/σ 的量级，两臂就
    不是在同一组参数附近比较了。）
    """
    weights = np.random.default_rng(23).dirichlet([1.5] + [0.5] * 6, size=40)
    common = dict(n_personas=40, n_tasks=10, n_alts=6, sigma=0.05, seed=23, weights=weights)
    informative = make_synthetic(**common, attribute_sd=0.6)
    flat = make_synthetic(**common, attribute_sd=0.002)

    prior_sd = 1.5
    g0, mu, chol = 20.0, np.full(N_RANDOM, 2.2), np.diag(np.full(N_RANDOM, prior_sd))
    n_draws = 200
    tight = posterior_at(informative.dataset, 0, g0, mu, chol, n_draws=n_draws, seed=4)
    loose = posterior_at(flat.dataset, 0, g0, mu, chol, n_draws=n_draws, seed=4)

    assert float(np.median(tight.sd)) < float(np.median(loose.sd))
    assert float(np.median(tight.ess)) < float(np.median(loose.ess))
    # 无信息一端应当贴着先验，而不只是"比另一端松一点"
    assert float(np.median(loose.sd)) == pytest.approx(prior_sd, rel=0.05)
    assert float(np.median(loose.ess)) > 0.9 * n_draws
    assert float(np.median(tight.ess)) < 0.6 * n_draws


def test_a_long_panel_does_not_underflow():
    """60 次选择 × 6 备选，原始似然在 e^{-100} 量级，直接乘会下溢成零。"""
    s = make_synthetic(n_personas=3, n_tasks=60, n_alts=6, sigma=0.05, seed=29)
    g0, mu, chol = _fixed_params(3)
    post = posterior_at(s.dataset, 0, g0, mu, chol, n_draws=100, seed=6)
    assert np.all(np.isfinite(post.mean))
    assert np.all(np.isfinite(post.sd))
    assert np.all(post.ess >= 1.0)
    assert np.allclose(post.weights.sum(axis=1), 1.0)


def test_the_posterior_is_reproducible_under_a_fixed_seed():
    s = make_synthetic(n_personas=10, n_tasks=4, n_alts=4, seed=31)
    g0, mu, chol = _fixed_params(4)
    a = posterior_at(s.dataset, 0, g0, mu, chol, n_draws=70, seed=9)
    b = posterior_at(s.dataset, 0, g0, mu, chol, n_draws=70, seed=9)
    c = posterior_at(s.dataset, 0, g0, mu, chol, n_draws=70, seed=10)
    assert np.array_equal(a.mean, b.mean)
    assert not np.allclose(a.mean, c.mean)


def test_fitting_twice_with_the_same_seed_gives_the_same_answer(small):
    a = fit_recovery(small.dataset, FAST)
    b = fit_recovery(small.dataset, FAST)
    assert a.gamma_0 == b.gamma_0
    assert np.array_equal(a.w_hat, b.w_hat)


def test_the_result_summary_is_json_shaped(small):
    import json

    result = fit_recovery(small.dataset, FAST)
    text = json.dumps(result.summary())
    assert "sigma_hat" in text
    assert len(json.loads(text)["w_center_hat"]) == N_DIMENSIONS
    assert json.loads(text)["dimensions"] == list(ALL_DIMENSIONS)


def test_the_result_carries_no_path_from_sigma_hat_to_a_floor_calibration(small):
    """硬规则 19、25：地板臂的 σ 由重复子集按分歧率标定，不从这里读。

    这条测试固定的是一个**缺席**：`RecoveryResult` 上不该长出任何听起来像
    "把 σ̂ 交给地板臂"的方法。长出来的时候这条会失败，那正是要被看见的时刻。
    """
    result = fit_recovery(small.dataset, FAST)
    names = [n for n in dir(result) if not n.startswith("_")]
    assert not [n for n in names if "floor" in n or "calibrat" in n]
    assert result.sigma_hat > 0  # 它只是一个诊断量
