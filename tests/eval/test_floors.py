"""P8：地板、几何、σ 标定、Σ_w(t) 路径与门二/门三。

测试针对**科学含义与真实风险**，不是镜像实现：能返回数组不算通过，指定的矩
真的被达到才算。
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from eval.floors import (
    D_NEAR_ZERO,
    MOMENT_RTOL,
    ReferenceDesign,
    RepeatObservation,
    arm_slope,
    build_floor_arm,
    build_shuffled_arm,
    calibrate_logistic_normal,
    calibrate_sigma,
    calibration_alphas,
    collapse_ratio,
    covariance_path,
    gate_three,
    gate_two,
    measured_disagreement,
    persona_with_weights,
    projections,
    run_case,
    shared_geometry,
    simplex_moment_feasibility,
    simulate_repeats,
    source_persona_ids,
    theoretical_disagreement,
    weights_of,
)
from eval.recovery import (
    RESEARCH_PROFILE_ALR_FULL_MATERIAL,
    RecoveryConfig,
    block_bootstrap,
    fit_research,
)
from worlds.dimensions import ALL_DIMENSIONS, N_DIMENSIONS
from worlds.personas import dirichlet_covariance, dirichlet_mean

SMALL = ReferenceDesign(n_personas=24, n_choice_sets=3, n_cities=12)
FAST = RESEARCH_PROFILE_ALR_FULL_MATERIAL


# --------------------------------------------------------------------------
# 研究 profile 与兼容默认值
# --------------------------------------------------------------------------


def test_the_library_default_is_untouched_by_the_research_profile():
    """换默认是独立的工程兼容决定；本次通过 profile 选规格，旧调用者一字不动。"""
    assert RecoveryConfig().parameterization == "difference_normal"
    assert RecoveryConfig().covariance == "diagonal"
    assert FAST.config.parameterization == "alr_logistic_normal"
    assert FAST.config.covariance == "full"
    assert FAST.config.base_dim == "material"
    assert FAST.start_sd0 == (0.1, 0.5, 1.0, 2.0)


def test_the_profile_fingerprint_carries_everything_that_must_match():
    fp = FAST.fingerprint()
    for key in (
        "parameterization",
        "covariance",
        "base_dim",
        "n_draws",
        "draw_method",
        "draw_seed",
        "maxiter",
        "tol",
        "population_moment_draws",
        "start_sd0",
        "selection_rule",
    ):
        assert key in fp, key
    assert fp["selection_rule"] == "max_simulated_loglik_over_starts"


def test_the_research_fit_selects_by_likelihood_not_by_recovery():
    arm = build_floor_arm(SMALL, sigma=0.05)
    fit = fit_research(arm.dataset, FAST)
    good = [s for s in fit.starts if s.ok]
    assert len(good) == len(FAST.start_sd0)
    assert fit.result.diagnostics.loglik == pytest.approx(max(s.loglik for s in good))
    assert fit.chosen_sd0 == next(s.sd0 for s in good if s.loglik == max(g.loglik for g in good))


# --------------------------------------------------------------------------
# 参考设计与注入权重
# --------------------------------------------------------------------------


def test_the_design_distinguishes_choice_sets_from_repetitions():
    fp = SMALL.fingerprint()
    assert fp["n_choice_sets_per_persona"] == 3
    assert fp["choice_sets_are_distinct_worlds"] is True
    assert fp["repeat_subset_is_separate"] is True


def test_two_designs_that_differ_anywhere_get_different_digests():
    from dataclasses import replace

    assert SMALL.digest() != replace(SMALL, n_choice_sets=6).digest()
    assert SMALL.digest() != replace(SMALL, world_calibration="PROVISIONAL_US_2024").digest()
    assert SMALL.digest() == replace(SMALL).digest()


def test_injecting_weights_keeps_the_m2_features_and_only_changes_the_weights():
    from worlds.personas import PersonaGenerator

    p = PersonaGenerator().generate(3, seed=1)[0]
    w = np.full(N_DIMENSIONS, 1.0 / N_DIMENSIONS)
    q = persona_with_weights(p, w)
    assert np.allclose(weights_of(q), w)
    assert q.shares == p.shares  # 份额不动 ⟹ ln_p 不动
    assert q.ability_quantile == p.ability_quantile  # 分位不动 ⟹ ln_y 不动


def test_illegal_injected_weights_are_rejected():
    from worlds.personas import PersonaGenerator

    p = PersonaGenerator().generate(1, seed=1)[0]
    with pytest.raises(ValueError, match="非负且和为一"):
        persona_with_weights(p, np.full(N_DIMENSIONS, 0.5))


def test_the_calibration_cases_change_dispersion_but_not_the_theoretical_mean():
    """α 整体缩放：Dirichlet 均值只依赖 α 的比例，所以理论均值不变。"""
    alphas = calibration_alphas((0.5, 1.0, 2.0))
    assert set(alphas) == {"wide_variance", "baseline", "narrow_variance"}
    base_mean = dirichlet_mean()
    for name, a in alphas.items():
        assert np.allclose(dirichlet_mean(list(a)), base_mean, atol=1e-12), name
    wide = np.diag(dirichlet_covariance(list(alphas["wide_variance"])))
    narrow = np.diag(dirichlet_covariance(list(alphas["narrow_variance"])))
    assert np.all(wide > narrow)  # α 小 ⟹ 方差大


def test_the_asymmetric_dirichlet_mean_is_not_one_seventh():
    """`DECISIONS/0006`：均值是 (1/3, 1/9×6)，旧的 1/7 已作废。"""
    m = dirichlet_mean()
    assert m[0] == pytest.approx(1 / 3, abs=1e-12)
    assert np.allclose(m[1:], 1 / 9, atol=1e-12)
    assert not np.allclose(m, 1 / 7)


# --------------------------------------------------------------------------
# 5.2 共用中心、d/u、斜率、r
# --------------------------------------------------------------------------


def test_all_arms_share_one_centre_and_one_d_and_u():
    """硬规则 24：中心、d、u 只算一次，三臂原样共用。"""
    rng = np.random.default_rng(0)
    truth = rng.dirichlet(np.ones(N_DIMENSIONS), size=50)
    centre = truth.mean(axis=0)
    geom = shared_geometry(truth, centre, centre_source="test")

    arm_a = truth + 0.3 * (centre[None, :] - truth)
    arm_b = truth + 0.6 * (centre[None, :] - truth)
    pa, pb = projections(arm_a, truth, geom), projections(arm_b, truth, geom)

    # 两个臂的 proj 用的是同一个 u，所以纯收缩下比值就是收缩强度之比
    assert np.allclose(pb / pa, 2.0, rtol=1e-9)
    assert np.allclose(np.linalg.norm(geom.u, axis=1), 1.0, atol=1e-12)


def test_d_near_zero_is_flagged_and_excluded_rather_than_epsilon_patched():
    truth = np.tile(np.full(N_DIMENSIONS, 1.0 / N_DIMENSIONS), (6, 1))
    truth[0] = np.array([0.4, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1])
    truth[1] = np.array([0.3, 0.2, 0.1, 0.1, 0.1, 0.1, 0.1])
    centre = np.full(N_DIMENSIONS, 1.0 / N_DIMENSIONS)
    geom = shared_geometry(truth, centre, centre_source="test")
    assert geom.n_near_zero == 4  # 四个人恰好落在中心上
    assert geom.as_dict()["d_near_zero_threshold"] == D_NEAR_ZERO

    s = arm_slope(np.ones(6), geom, arm="x", source="synthetic_truth_softmax")
    assert s.n_used == 2
    assert s.n_excluded_near_zero == 4


def test_the_collapse_ratio_uses_the_measured_shuffled_slope():
    """`DECISIONS/0002`：分母是 ŝ_shuffled，不是理论值 1。"""
    r = collapse_ratio(s_matched=0.72, s_shuffled=0.90, lam=0.45)
    assert r.status == "ok"
    assert r.value == pytest.approx((0.72 - 0.45) / (0.90 - 0.45))
    # 旧式 (s−λ)/(1−λ) 会给出别的数；这里确认没有回流
    assert r.value != pytest.approx((0.72 - 0.45) / (1.0 - 0.45))


def test_the_collapse_ratio_is_not_clipped_to_the_unit_interval():
    """r > 1 是一个结果，不是越界（§1.2.2）。"""
    r = collapse_ratio(s_matched=0.95, s_shuffled=0.60, lam=0.10)
    assert r.value > 1.0


@pytest.mark.parametrize(
    ("s_shuffled", "lam", "status"),
    [(0.40, 0.40, "denominator_non_positive"), (0.30, 0.50, "denominator_non_positive")],
)
def test_a_non_positive_denominator_returns_a_status_not_a_number(s_shuffled, lam, status):
    r = collapse_ratio(0.6, s_shuffled, lam)
    assert r.value is None
    assert r.status == status


def test_shuffling_breaks_only_the_pairing():
    """打乱臂不重排属性列或城市标签——那会伪造一个更强的对照。"""
    matched = build_floor_arm(SMALL, sigma=0.05)
    shuffled = build_shuffled_arm(SMALL, sigma=0.05, permutation_seed=7)
    assert shuffled.source == "synthetic_shuffled"
    assert matched.source == "synthetic_truth_softmax"
    assert shuffled.dataset.persona_order == matched.dataset.persona_order
    assert shuffled.dataset.alternative_ids == matched.dataset.alternative_ids
    # 特征逐位相同：份额与分位没动，只有生成选择用的权重被打乱
    assert np.array_equal(shuffled.dataset.features, matched.dataset.features)
    # proj 用的真值是**未打乱**的
    assert np.allclose(shuffled.injected_weights, matched.injected_weights)


# --------------------------------------------------------------------------
# 5.4 σ 标定
# --------------------------------------------------------------------------


def _fake_ln_utilities(n_crossings=60, n_alt=12, seed=0):
    return np.random.default_rng(seed).normal(scale=0.2, size=(n_crossings, n_alt))


def test_disagreement_increases_with_sigma():
    """方向不能写反：分歧率对 σ 单调递增。"""
    lnv = _fake_ln_utilities()
    rates = [theoretical_disagreement(lnv, s) for s in (0.01, 0.05, 0.2, 1.0, 5.0)]
    assert all(a < b for a, b in itertools.pairwise(rates))
    assert rates[-1] < 1.0 - 1.0 / 12 + 1e-6  # 上界是 1 − 1/n


def test_sigma_calibration_recovers_a_known_sigma_from_synthetic_repeats():
    lnv = _fake_ln_utilities(n_crossings=200, seed=3)
    crossings = {(f"p{i}", "w0"): lnv[i] for i in range(len(lnv))}
    true_sigma = 0.08
    repeats = simulate_repeats(
        crossings, [[f"c{j}" for j in range(12)]], sigma=true_sigma, n_repeats=8, seed=1
    )
    m = measured_disagreement(repeats, n_alternatives=12, source="synthetic_validation")
    cal = calibrate_sigma(lnv, m)
    assert cal.status == "calibrated"
    assert cal.sigma == pytest.approx(true_sigma, rel=0.25)
    assert cal.achieved_disagreement == pytest.approx(m.rate, abs=1e-4)
    assert "启发式" in cal.note  # 不是恒等式，注释里必须写明


def test_without_real_repeats_the_status_is_pending_input_not_a_default():
    cal = calibrate_sigma(_fake_ln_utilities(), None)
    assert cal.status == "pending_input"
    assert cal.sigma is None
    assert "待输入" in cal.note


def test_a_disagreement_above_the_random_ceiling_has_no_solution():
    lnv = _fake_ln_utilities()
    from eval.floors.sigma import DisagreementMeasurement

    bogus = DisagreementMeasurement(
        rate=0.99,
        n_pairs=100,
        n_crossings=10,
        n_alternatives=12,
        random_ceiling=1.0 - 1.0 / 12,
        source="synthetic_validation",
    )
    cal = calibrate_sigma(lnv, bogus)
    assert cal.status == "target_above_random_ceiling"
    assert cal.sigma is None


def test_repeat_pairs_are_counted_within_crossings_only():
    reps = [
        RepeatObservation("p1", "s1", 0, "a"),
        RepeatObservation("p1", "s1", 1, "b"),
        RepeatObservation("p1", "s2", 0, "a"),
        RepeatObservation("p1", "s2", 1, "a"),
    ]
    m = measured_disagreement(reps, n_alternatives=12, source="synthetic_validation")
    assert m.n_pairs == 2 and m.n_crossings == 2
    assert m.rate == pytest.approx(0.5)


# --------------------------------------------------------------------------
# 5.3 Σ_w(t)
# --------------------------------------------------------------------------


def test_psd_alone_does_not_establish_simplex_feasibility():
    """本仓库实测过的真实情形：PSD 通过，但方差超过伯努利上界。"""
    mean = np.array([0.0937, 0.15, 0.15, 0.15, 0.15, 0.15, 0.1563])
    cov = np.diag([0.1334, 0.02, 0.02, 0.02, 0.02, 0.02, 0.02])
    cov = (
        cov
        - cov @ np.ones((7, 7)) / 7
        - np.ones((7, 7)) @ cov / 7
        + (np.ones((7, 7)) * (np.ones(7) @ cov @ np.ones(7)) / 49)
    )
    f = simplex_moment_feasibility(mean, cov)
    assert f.psd
    assert not f.variance_within_bernoulli_bound
    assert not f.feasible
    assert f.worst_variance_slack < 0


def test_the_covariance_path_is_a_convex_combination_preserving_row_sums():
    a = dirichlet_covariance()
    b = 0.5 * a
    grid = np.array([0.0, 0.5, 1.0])
    path = covariance_path(a, b, grid)
    assert np.allclose(path[0], a)
    assert np.allclose(path[-1], b)
    for cov in path:
        assert np.abs(cov @ np.ones(7)).max() < 1e-12


def test_the_moment_calibration_actually_hits_the_target_not_just_returns_an_array():
    """指定的权重空间矩必须真的被达到；只测"能返回数组"等于没测。"""
    target_mean = dirichlet_mean()
    target_cov = dirichlet_covariance()
    cal = calibrate_logistic_normal(target_mean, target_cov, base=0, t=0.0, n_draws=20_000)
    assert cal.status in {"ok", "residual_above_tolerance"}
    assert cal.mu_a is not None and cal.chol is not None
    # 达到的矩必须被报出来，并与目标一起可核对
    assert cal.achieved_mean is not None and cal.achieved_cov is not None
    assert cal.mean_rel_error < 0.05
    if cal.status == "ok":
        assert cal.cov_rel_frobenius <= MOMENT_RTOL


def test_an_infeasible_target_is_refused_rather_than_approximated():
    mean = np.full(7, 1 / 7)
    cov = np.eye(7)  # 行和非零、方差远超伯努利上界
    cal = calibrate_logistic_normal(mean, cov, base=0, t=0.5)
    assert cal.status == "target_infeasible"
    assert cal.mu_a is None


# --------------------------------------------------------------------------
# 5.5 门二、门三
# --------------------------------------------------------------------------


def _gate_two(**kw):
    base = dict(
        lam=0.30,
        s_shuffled=0.90,
        window_se=0.02,
        n_bootstrap=300,
        threshold_2a=0.8,
        threshold_2b=3.0,
        threshold_2c=0.5,
        min_bootstrap=200,
        arm_sources={"floor": "synthetic_truth_softmax", "shuffled": "synthetic_shuffled"},
    )
    base.update(kw)
    return gate_two(**base)


def test_gate_two_passes_only_when_all_three_criteria_pass():
    g = _gate_two()
    assert g.status == "pass"
    assert [c.name for c in g.criteria] == ["2a", "2b", "2c"]


def test_gate_two_without_a_preregistered_2b_threshold_is_pending_not_pass():
    g = _gate_two(threshold_2b=None)
    assert g.status == "pending_input"
    assert next(c for c in g.criteria if c.name == "2b").status == "pending_input"
    assert "预注册" in next(c for c in g.criteria if c.name == "2b").reason


def test_gate_two_reports_a_reason_instead_of_a_plausible_ratio():
    g = _gate_two(lam=0.95, s_shuffled=0.0)
    c2a = next(c for c in g.criteria if c.name == "2a")
    assert c2a.status == "inconclusive" and c2a.value is None
    c2b = next(c for c in g.criteria if c.name == "2b")
    assert c2b.status == "fail"  # 窗口非正


def test_gate_two_refuses_to_judge_2b_on_too_few_bootstrap_replicates():
    g = _gate_two(n_bootstrap=5)
    assert next(c for c in g.criteria if c.name == "2b").status == "pending_input"
    assert g.status == "pending_input"


def test_gate_two_is_pending_when_the_arms_are_missing():
    g = _gate_two(lam=None)
    assert g.status == "pending_input"
    assert all(c.status == "pending_input" for c in g.criteria)


def test_gate_three_without_repeat_data_is_pending_and_states_its_assumptions():
    g = gate_three(None, None)
    assert g.status == "pending_input"
    assert g.minimum_detectable_effect is None
    assert "MDE" in g.mde_assumptions


def test_gate_three_reports_an_mde_derived_from_residual_variance():
    rng = np.random.default_rng(0)
    d = rng.uniform(0.05, 0.5, size=400)
    y = 0.4 * d + rng.normal(scale=0.05, size=400)
    g = gate_three(y, d)
    assert g.status == "pass"
    assert g.slope == pytest.approx(0.4, abs=0.05)
    assert g.ci is not None and g.ci[0] > 0
    assert g.minimum_detectable_effect is not None and g.minimum_detectable_effect > 0
    assert "残差方差" in g.mde_assumptions


def test_gate_three_low_power_is_inconclusive_not_no_dependence():
    rng = np.random.default_rng(1)
    d = rng.uniform(0.2, 0.25, size=20)
    y = 0.4 * d + rng.normal(scale=1.0, size=20)
    g = gate_three(y, d)
    assert g.status == "inconclusive"
    assert "r(d)" in g.reason or "r(d)" in g.as_dict()["default_metric"]


# --------------------------------------------------------------------------
# bootstrap 与端到端
# --------------------------------------------------------------------------


def test_duplicate_personas_get_independent_panel_ids_with_recoverable_sources():
    arm = build_floor_arm(SMALL, sigma=0.05)
    first = arm.dataset.persona_order[0]
    resampled = arm.dataset.take_personas([first, first, arm.dataset.persona_order[1]])
    assert resampled.n_personas == 3  # 两个副本没有被并成一个更长的面板
    assert source_persona_ids(resampled) == [first, first, arm.dataset.persona_order[1]]
    assert np.all(resampled.tasks_per_persona == SMALL.n_choice_sets)


def test_the_bootstrap_uses_the_same_research_fit_as_the_point_estimate():
    from eval.floors.runner import _fit_research_adapter

    arm = build_floor_arm(SMALL, sigma=0.05)
    calls = {"n": 0}

    def counting(dataset, cfg):
        calls["n"] += 1
        return _fit_research_adapter(dataset, cfg)

    out = block_bootstrap(
        arm.dataset,
        FAST.config,
        n_replicates=2,
        seed=3,
        metric_fn=lambda r: {"sigma_hat": r.sigma_hat},
        fit_fn=counting,
    )
    assert calls["n"] == 2
    assert len(out.successes) == 2
    assert all(r.parameterization == "alr_logistic_normal" for r in [out.successes[0].result] if r)


def test_a_small_end_to_end_bias_table_is_produced():
    arm = build_floor_arm(SMALL, sigma=0.05)
    table = run_case(arm, case="baseline", injected_alpha=None, profile=FAST)
    d = table.as_dict()
    assert d["dimensions"] == list(ALL_DIMENSIONS)
    # 三种矩必须分别保存，来源标签不同
    assert d["injected_theoretical"]["source"] == "dirichlet_closed_form"
    assert d["injected_realised"]["source"] == "finite_sample_injected"
    assert d["fitted_population"]["source"] == "fitted_population_weight_space"
    assert len(d["variance_ratio_fitted_over_realised"]) == N_DIMENSIONS
    assert np.all(np.isfinite(d["individual_rmse"]))
    assert d["sigma_generating"] == 0.05  # 输入
    assert d["sigma_hat"] != d["sigma_generating"]  # 输出，不互相覆盖
    assert d["design_digest"] == SMALL.digest()
