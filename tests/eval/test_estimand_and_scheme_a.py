"""本轮的三块新东西：估计对象（P/Q/S）、方案 A 的 t 路径、逐人层。

测试盯的是**会造成实际损害的错误**，不是镜像实现。几条特别针对本轮实际踩到的：
求解器在自己那批点上的残差恒为机器精度（说明不了任何事）、"尾层强度加倍"被读成
"尾层人数加倍"、以及拿原 Dirichlet 闭式矩当过采样样本的真值。
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.floors.scheme_a import (
    LEGACY_PROTOCOL,
    MEAN_TOL,
    PROTOCOL,
    alr_covariance_path,
    calibrate_position,
    scheme_a_path,
    shared_centre_from_fit,
    softmax_with_base,
)
from eval.individual import build_table, compare_arms, group_by
from eval.sampling import Allocation, ReferencePopulation, StratumScheme
from eval.sampling.moments import (
    DDOF,
    alr_coordinates,
    compare_to_injected,
    empirical_alr_moments,
    empirical_weight_moments,
    theoretical_alr_moments,
    theoretical_weight_moments,
)
from worlds.dimensions import ALL_DIMENSIONS
from worlds.personas import dirichlet_alpha_vector

BASE = 0


@pytest.fixture(scope="module")
def truth() -> np.ndarray:
    return np.random.default_rng(5).dirichlet(dirichlet_alpha_vector(), size=800)


# --------------------------------------------------------------------------
# 估计对象：P / Q / S
# --------------------------------------------------------------------------


def test_the_empirical_moments_use_ddof_zero(truth):
    """S 是一份有限名单，二阶矩的分母是 N。ddof=1 会让注入矩与同名单矩对不上。"""
    m = empirical_weight_moments(truth)
    assert m.ddof == DDOF == 0
    manual = (truth - truth.mean(0)).T @ (truth - truth.mean(0)) / truth.shape[0]
    np.testing.assert_allclose(m.covariance, manual, atol=1e-15)
    assert not np.allclose(m.covariance, np.cov(truth, rowvar=False), atol=1e-12)


def test_the_moments_say_which_population_they_describe(truth):
    assert empirical_weight_moments(truth).estimand == "S_realised_list"
    assert theoretical_weight_moments().estimand == "P_dirichlet_superpopulation"
    assert empirical_alr_moments(truth, base=BASE).estimand == "S_realised_list"


def test_an_oversampled_list_departs_from_the_theoretical_moments(truth):
    """**这不是抽样错误，是设计的后果。**

    过采样尾层之后 S 的矩本来就该偏离 P。把 P 的闭式解当 S 的真值分母，
    协方差比会混进一个纯抽样设计造成的因子。
    """
    z = np.linalg.norm(truth - truth.mean(0), axis=1)
    tail = np.argsort(z)[-300:]
    oversampled = np.concatenate([truth, truth[tail]])

    s = empirical_weight_moments(oversampled)
    p = theoretical_weight_moments()
    assert np.abs(np.diag(s.covariance) - np.diag(p.covariance)).max() > 1e-3


def test_the_theoretical_alr_moments_match_the_closed_form():
    """material 基准、α=(1.5,0.5×6)：μ=−2，对角 π²−4，非对角 π²/2−4。"""
    m = theoretical_alr_moments(base=BASE)
    np.testing.assert_allclose(m.mean, -2.0, atol=1e-12)
    np.testing.assert_allclose(np.diag(m.covariance), np.pi**2 - 4, atol=1e-12)
    off = m.covariance[~np.eye(6, dtype=bool)]
    np.testing.assert_allclose(off, np.pi**2 / 2 - 4, atol=1e-12)


def test_the_realised_alr_moments_are_computed_on_the_same_list(truth):
    """`C_a,S` 必须与 `m_S` 同名单、同基准、同 ddof。"""
    m = empirical_alr_moments(truth, base=BASE)
    a = alr_coordinates(truth, BASE)
    manual = (a - a.mean(0)).T @ (a - a.mean(0)) / a.shape[0]
    np.testing.assert_allclose(m.covariance, manual, atol=1e-14)
    assert m.n == truth.shape[0] and m.base == BASE and m.ddof == 0


def test_the_comparison_reports_diagonal_ratios_not_elementwise_division(truth):
    """`Σ1 = 0` 保证有接近零的元素，逐元素相除必然炸。"""
    inj = empirical_weight_moments(truth)
    fitted = empirical_weight_moments(truth * 0.9 + inj.mean * 0.1, source="fitted")
    cmp = compare_to_injected(fitted, inj)
    d = cmp.as_dict()
    assert len(d["variance_ratio_fitted_over_injected"]) == 7
    assert "not_elementwise_ratio" in d
    assert d["denominator_estimand"] == "S_realised_list"
    assert "mean_shift_fitted_minus_injected" in d


# --------------------------------------------------------------------------
# 配额：强度加倍 ≠ 人数加倍
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def scheme() -> StratumScheme:
    return StratumScheme.equal_frequency(ReferencePopulation.draw(n_draws=80_000, seed=20_260_926))


def test_doubling_the_tail_intensity_does_not_double_the_tail_count(scheme):
    """`m_H=2` 是**强度**倍率。归一化之后尾层人数 200 → 333，不是 400。

    这条区别必须钉住：报告里写成"尾层人数翻倍"会让读的人以为 N 变了，
    或者以为尾部覆盖比实际强。
    """
    prop = Allocation.proportional(scheme, 1000)
    tail = Allocation.intensity(scheme, 1000, [1, 1, 1, 1, 2])

    assert prop.quotas[-1] == pytest.approx(200, abs=2)
    assert tail.quotas[-1] == pytest.approx(333, abs=2)
    assert tail.quotas[-1] != pytest.approx(2 * prop.quotas[-1], abs=20)
    assert tail.n_total == 1000, "总人数不变；是重新分配，不是加人"


def test_the_intensity_allocation_records_its_rounding(scheme):
    note = Allocation.intensity(scheme, 1000, [1, 1, 1, 1, 2]).note
    assert "抽样强度倍率" in note and "余数" in note and "非比例分配" in note


def test_unit_intensities_reduce_to_proportional(scheme):
    a = Allocation.intensity(scheme, 1000, [1] * scheme.n_strata)
    b = Allocation.proportional(scheme, 1000)
    assert a.quotas == b.quotas


def test_a_bad_multiplier_is_refused(scheme):
    with pytest.raises(ValueError, match="必须为正"):
        Allocation.intensity(scheme, 100, [1, 1, 1, 1, 0])
    with pytest.raises(ValueError, match="倍率要"):
        Allocation.intensity(scheme, 100, [1, 1])


# --------------------------------------------------------------------------
# 方案 A
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def path_inputs(truth):
    m = empirical_alr_moments(truth, base=BASE)
    c_matched = 0.45 * m.covariance
    centre = shared_centre_from_fit(m.mean, np.linalg.cholesky(c_matched), BASE, n_draws=400_000)
    return m.covariance, c_matched, centre


def test_the_shared_centre_is_the_mean_of_the_softmax_not_the_softmax_of_the_mean():
    """Jensen。用错会把中心挪到别处，`d_i = ‖w_i − c‖` 全体跟着错。"""
    mu = np.full(6, -2.0)
    chol = np.linalg.cholesky((np.pi**2 - 4) * np.eye(6))
    c = shared_centre_from_fit(mu, chol, BASE, n_draws=200_000)
    naive = softmax_with_base(mu[None, :], BASE)[0]
    assert abs(c[0] - naive[0]) > 0.05, "两者必须实质不同，否则这条约束不值一提"
    assert c.sum() == pytest.approx(1.0)


def test_scheme_a_pins_the_weight_mean_across_the_whole_path(path_inputs):
    """方案 A 的定义性质：`E[w]` 沿 t 不动，动的只有离散度。"""
    c_s, c_m, centre = path_inputs
    grid = np.array([0.0, 0.5, 1.0])
    path = scheme_a_path(c_s, c_m, centre, grid, base=BASE)

    for p in path:
        assert p.status == "ok", p.status
        assert np.abs(p.achieved_mean_holdout - centre).max() <= MEAN_TOL


def test_the_dispersion_moves_along_the_path(path_inputs):
    """均值钉住，方差必须真的在变——否则这条路径什么也没做。"""
    c_s, c_m, centre = path_inputs
    path = scheme_a_path(c_s, c_m, centre, np.array([0.0, 1.0]), base=BASE)
    v0 = np.diag(path[0].achieved_covariance)
    v1 = np.diag(path[-1].achieved_covariance)
    assert (v1 < v0).all(), "匹配端更集中，t=1 的每一维方差都该更小"
    assert v0[0] / v1[0] > 1.2


def test_the_in_sample_residual_proves_nothing(path_inputs):
    """**本轮实际踩到的坑。**

    凸问题的驻点就是"在求解器那批点上均值命中目标"，所以 in-sample 残差必然是
    机器精度（实测 6e-11），无论解得好不好。只有独立积分点才看得见求解本身的
    模拟误差。这条测试把两者的量级差钉死。
    """
    c_s, _c_m, centre = path_inputs
    p = calibrate_position(c_s, centre, base=BASE, t=0.0, n_draws=20_000)
    in_sample = float(np.abs(p.achieved_mean_insample - centre).max())
    assert in_sample < 1e-8, "求解器在自己那批点上必然命中"
    assert p.mean_abs_error_holdout > 20 * in_sample, "独立点才暴露真实误差"


def test_more_solve_draws_reduce_the_holdout_error(path_inputs):
    """误差随**求解**点数下降，不是随检查点数下降。搞反了会一直加检查点。"""
    c_s, _c_m, centre = path_inputs
    coarse = calibrate_position(c_s, centre, base=BASE, t=0.0, n_draws=5_000)
    fine = calibrate_position(c_s, centre, base=BASE, t=0.0, n_draws=160_000)
    assert fine.mean_abs_error_holdout < coarse.mean_abs_error_holdout


def test_a_tolerance_below_the_noise_floor_is_flagged_not_silently_passed(path_inputs):
    """检查分辨不了声明的容差时要说出来，而不是给一个看着正常的 ok。"""
    c_s, _c_m, centre = path_inputs
    p = calibrate_position(c_s, centre, base=BASE, t=0.0, n_draws=80_000, n_draws_check=400)
    assert p.status == "tolerance_below_noise_floor"
    assert p.mc_se_holdout > MEAN_TOL / 3


def test_a_non_psd_covariance_is_refused_without_producing_a_number(path_inputs):
    c_s, _c_m, centre = path_inputs
    bad = c_s.copy()
    bad[0, 0] = -1.0
    p = calibrate_position(bad, centre, base=BASE, t=0.3)
    assert p.status == "not_psd"
    assert p.mu_a is None and p.achieved_mean_holdout is None
    assert not p.valid


def test_the_path_interpolates_in_alr_space_not_weight_space(path_inputs):
    c_s, c_m, _centre = path_inputs
    mid = alr_covariance_path(c_s, c_m, np.array([0.5]))[0]
    np.testing.assert_allclose(mid, 0.5 * c_s + 0.5 * c_m, atol=1e-14)


def test_the_protocol_name_travels_with_every_point(path_inputs):
    """协议名进签名，正式入口才拒得掉旧协议。"""
    c_s, c_m, centre = path_inputs
    p = scheme_a_path(c_s, c_m, centre, np.array([0.0]), base=BASE)[0]
    assert p.protocol == PROTOCOL
    assert p.as_dict()["protocol"] == PROTOCOL
    assert PROTOCOL != LEGACY_PROTOCOL


# --------------------------------------------------------------------------
# 逐人层
# --------------------------------------------------------------------------


@pytest.fixture
def tables():
    rng = np.random.default_rng(3)
    n = 80
    tw = rng.dirichlet(dirichlet_alpha_vector(), size=n)
    ids = [f"p{i:03d}" for i in range(n)]

    def mk(scale, arm):
        return build_table(
            persona_ids=ids,
            true_weights=tw,
            recovered_weights=tw + rng.normal(0, scale, tw.shape),
            dimensions=ALL_DIMENSIONS,
            arm=arm,
            floor_rmse=dict.fromkeys(ids, 0.05),
        )

    grouping = {p: ("high" if tw[i, 0] > 0.35 else "low") for i, p in enumerate(ids)}
    return {"matched": mk(0.06, "matched"), "floor": mk(0.05, "floor")}, grouping, tw, ids


def test_the_individual_table_keeps_all_seven_dimensions(tables):
    t, _g, _tw, _ids = tables
    d = t["matched"].as_dict()
    assert d["all_seven_dimensions_retained"] is True
    assert len(d["dimensions"]) == 7
    assert "error_commute" in d["records"][0], "不删难测的 commute"


def test_grouping_by_recovered_weights_is_refused(tables):
    """按回收结果定义人群，等于让被测对象自己划考区。"""
    t, g, _tw, _ids = tables
    with pytest.raises(ValueError, match="事前"):
        group_by(t["matched"], g, basis="recovered_weights")


def test_a_group_where_the_floor_is_equally_bad_is_flagged_as_measurement_limited(tables):
    """权重误差大**不自动**等于 AI 理解错误。"""
    t, g, _tw, _ids = tables
    out = compare_arms(t, g, basis="true_weights")
    for entry in out["by_group"].values():
        assert "matched_over_floor" in entry
        assert "reading" in entry
    assert "不扣地板" in out["caveat"]


def test_the_table_carries_no_individual_ratio(tables):
    """个体层面不造 `r` —— 小分母会炸出没有意义的大数。"""
    t, _g, _tw, _ids = tables
    rec = t["matched"].as_dict()["records"][0]
    assert not any(k.startswith("r_") or k == "collapse_ratio" for k in rec)


def test_signed_errors_are_kept_not_just_magnitudes(tables):
    """只留距离会丢掉**方向**，而"往哪压"正是要回答的。"""
    t, _g, _tw, _ids = tables
    rec = t["matched"].as_dict()["records"]
    errs = [r["error_material"] for r in rec]
    assert min(errs) < 0 < max(errs)


# --------------------------------------------------------------------------
# 窗口与 r 的有效性必须分开记（代码审核复现的边界）
# --------------------------------------------------------------------------


def test_a_mostly_negative_window_does_not_yield_an_r_interval():
    """**旧实现在这里给出一个宽度为零的"区间"。**

    199/200 次窗口为负、1 次为正时，旧代码返回 status=ok、n_valid=200、
    r_ci≈(0.2, 0.2)——拿唯一那次的 r 当区间依据。那不是精度，是样本量为一。
    """
    from eval.floors.window import window_uncertainty

    s = np.full(200, 0.5)
    lam = np.full(200, 0.9)
    s[0] = 1.5
    m = np.full(200, 0.62)
    m[0] = 1.02

    u = window_uncertainty(s_shuffled=s, lam=lam, s_matched=m, n_requested=200)

    assert u.status == "ok", "窗口 SE 本身仍可估——s 与 λ 都有限"
    assert u.r_status == "too_many_nonpositive_windows"
    assert u.r_ci is None
    assert not u.r_is_reportable
    assert u.n_valid == 200
    assert u.n_valid_r == 1, "两个有效次数必须分开记"
    assert u.nonpositive_window_fraction == pytest.approx(0.995)


def test_window_se_being_estimable_does_not_make_r_reportable():
    """两个状态是两件事，不能用一个 status 概括。"""
    from eval.floors.window import window_uncertainty

    rng = np.random.default_rng(7)
    n = 300
    shared = rng.normal(0, 0.05, n)
    # 让 W = s − λ 的均值恰好为零：约一半的重抽会翻负。
    u = window_uncertainty(
        s_shuffled=0.40 + shared,
        lam=0.40 + 0.5 * shared,
        s_matched=0.39 + 0.7 * shared,
        n_requested=n,
    )
    assert 0.3 < u.nonpositive_window_fraction < 0.7, "构造要真的让一半窗口翻负"
    assert u.window_se is not None
    assert u.r_status != "ok"
    assert u.r_ci is None


def test_a_healthy_window_still_gives_an_r_interval():
    """反面：正常情形必须照常出区间，否则上面两条只是把功能关掉了。"""
    from eval.floors.window import window_uncertainty

    rng = np.random.default_rng(0)
    n = 300
    shared = rng.normal(0, 0.05, n)
    u = window_uncertainty(
        s_shuffled=1.05 + shared + rng.normal(0, 0.02, n),
        lam=0.25 + 0.8 * shared + rng.normal(0, 0.02, n),
        s_matched=0.55 + 0.9 * shared + rng.normal(0, 0.02, n),
        n_requested=n,
    )
    assert u.status == "ok" and u.r_status == "ok"
    assert u.r_ci is not None and u.r_ci[0] < u.r_point < u.r_ci[1]
    assert u.nonpositive_window_fraction == 0.0


def test_a_failed_arm_voids_the_whole_replicate():
    """任一臂失败 → 整个 replicate 作废。

    各臂各自删掉失败记录再拼数组，第 j 个 `s` 与第 j 个 `λ` 就不再来自同一次
    抽样，而 `SE(W)` 的全部意义就在于它们来自同一次。
    """
    from eval.floors.window import joint_bootstrap, paired_slopes

    def fit_both(picks, rep_seed):
        if rep_seed % 2 == 0:
            err = RuntimeError("shuffled 臂拟合失败")
            err.arm = "shuffled"
            raise err
        return {"floor|t=None|stratum=None": 0.3, "shuffled|t=None|stratum=None": 1.0}

    reps = joint_bootstrap(n_replicates=12, seed=3, draw_picks=lambda s: ["p0"], fit_both=fit_both)
    lam, lam_ids = paired_slopes(reps, "floor|t=None|stratum=None")
    sh, sh_ids = paired_slopes(reps, "shuffled|t=None|stratum=None")

    assert lam_ids == sh_ids, "两个臂必须留下同一组 replicate ID"
    assert len(lam) == len(sh) < 12
    assert any(not r.ok for r in reps)
    assert {r.failed_arm for r in reps if not r.ok} == {"shuffled"}


# --------------------------------------------------------------------------
# ALR 变换统一（F4）
# --------------------------------------------------------------------------


def test_the_two_alr_transforms_are_now_one():
    """`scheme_a` 委托到 `eval.recovery.alr`，不再各维护一份。"""
    from eval.recovery.alr import weights_from_alr

    rng = np.random.default_rng(11)
    a = rng.normal(0, 3.0, size=(5_000, 6))
    for base in (0, 1, 3):
        np.testing.assert_array_equal(softmax_with_base(a, base), weights_from_alr(a, base))


def test_the_unified_transform_is_bit_identical_on_realistic_coordinates():
    """统一**不改变**现有数值——这一条靠新旧对照验证，不靠"零命中"推断。"""
    from eval.recovery.alr import clip_hits, weights_from_alr

    def unclipped(a, base):
        a = np.asarray(a, float)
        n = a.shape[-1] + 1
        keep = [k for k in range(n) if k != base]
        full = np.zeros((*a.shape[:-1], n))
        full[..., keep] = a
        full = full - full.max(-1, keepdims=True)
        e = np.exp(full)
        return e / e.sum(-1, keepdims=True)

    rng = np.random.default_rng(2)
    a = rng.normal(-2.0, 2.4, size=(50_000, 6))  # 实际拟合分布的量级
    assert clip_hits(a) == 0
    for base in (0, 1):
        np.testing.assert_array_equal(weights_from_alr(a, base), unclipped(a, base))


def test_the_clip_counter_can_actually_fire():
    """让上一条有牙：截断确实会造成差异，只是在现有量级上不发生。"""
    from eval.recovery.alr import ALR_COORD_CAP, clip_hits, weights_from_alr

    extreme = np.array([[60.0, 50.0, 0.0, 0.0, 0.0, 0.0]])
    assert clip_hits(extreme) == 2
    assert ALR_COORD_CAP == 40.0
    w = weights_from_alr(extreme, 0)
    assert w[0, 1] == pytest.approx(0.5), "截断抹掉了 60 与 50 之间的差"
