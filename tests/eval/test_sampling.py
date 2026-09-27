"""P10：分层抽样、设计权重、两个报告接口，以及 P8 的复用边界。

每条测试盯的是一个**会造成实际损害的具体错误**，不是镜像实现。几处特别针对
本项目已经犯过或差点犯的错：用 `1/7` 当中心、把 `z` 当成 `d`、按结果重抽人、
用目标配额而不是实际人数算权重、以及把"等量分配"一律说成过采样。
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from eval.floors import (
    FloorDescriptor,
    FloorReuseError,
    ReferenceDesign,
    check_reuse,
    descriptor_from_run,
)
from eval.sampling import (
    Allocation,
    ReferencePopulation,
    SamplingPlan,
    StratumScheme,
    describe_allocation,
    draw_stratified,
    population_moments,
    report_conditional,
    report_cross_stratum_relationship,
    report_population,
    stratification_variable,
    weight_for,
    weights_of,
)
from worlds.personas import (
    dirichlet_alpha_vector,
    dirichlet_covariance,
    dirichlet_mean,
)

N_DIMS = 7


@pytest.fixture(scope="module")
def reference() -> ReferencePopulation:
    return ReferencePopulation.draw(n_draws=60_000, seed=20_260_926)


@pytest.fixture(scope="module")
def scheme(reference: ReferencePopulation) -> StratumScheme:
    return StratumScheme.equal_frequency(reference)


# --------------------------------------------------------------------------
# 理论矩 fixture
# --------------------------------------------------------------------------


def test_the_injection_moments_match_the_closed_form_fractions():
    """α=(1.5, 0.5×6) 下的四个精确值。分数写死，浮点近似藏不住符号错误。"""
    sigma = dirichlet_covariance()
    assert sigma.shape == (N_DIMS, N_DIMS)

    assert sigma[0, 0] == pytest.approx(4 / 99)
    for k in range(1, N_DIMS):
        assert sigma[k, k] == pytest.approx(16 / 891)
        assert sigma[0, k] == pytest.approx(-2 / 297)
    for j in range(1, N_DIMS):
        for k in range(1, N_DIMS):
            if j != k:
                assert sigma[j, k] == pytest.approx(-2 / 891)


def test_the_injection_covariance_obeys_the_simplex_constraint():
    """对称、行和为零、秩 6。`Σ1 = 0` 是 `Σw = 1` 的代数后果（硬规则 14）。"""
    sigma = dirichlet_covariance()
    np.testing.assert_allclose(sigma, sigma.T, atol=1e-15)
    np.testing.assert_allclose(sigma @ np.ones(N_DIMS), 0.0, atol=1e-15)
    assert np.linalg.matrix_rank(sigma, tol=1e-12) == N_DIMS - 1


def test_the_injection_mean_is_a_third_and_a_ninth_not_one_seventh():
    """`DECISIONS/0006`：非对称之后均值不是 `1/7`。"""
    mu = dirichlet_mean()
    assert mu[0] == pytest.approx(1 / 3)
    for k in range(1, N_DIMS):
        assert mu[k] == pytest.approx(1 / 9)
    assert mu[0] != pytest.approx(1 / 7)


def test_the_theoretical_moments_describe_the_injection_population_not_the_sample(scheme):
    """理论矩对应**原 Dirichlet 总体**。

    过采样之后的样本经验矩**不加权**就不该等于它——这正是 `ESTIMAND_GAP`。
    这条测试把那个差距钉成一个数，免得它退化成一句文档里的话。
    """
    tail_heavy = Allocation.custom((20, 20, 20, 40, 100))
    plan = SamplingPlan(scheme=scheme, allocation=tail_heavy, seed=11)
    personas, sample = draw_stratified(plan, pool_seed=404)
    w = {p.persona_id: weights_of(p) for p in personas}

    unweighted = np.mean([w[p] for p in sample.persona_ids], axis=0)
    weighted = np.array(population_moments(sample, w)["mean"])
    truth = dirichlet_mean()

    assert abs(unweighted[0] - truth[0]) > 0.02, "过采样尾部就应当让未加权均值偏掉"
    assert abs(weighted[0] - truth[0]) < abs(unweighted[0] - truth[0]), "加权必须把它拉回来"


# --------------------------------------------------------------------------
# 分层变量
# --------------------------------------------------------------------------


def test_the_stratification_centre_must_be_passed_explicitly():
    """没有默认中心。默认 `1/7` 正是 `DECISIONS/0006` 修掉的 bug。"""
    w = np.full((3, N_DIMS), 1 / N_DIMS)
    with pytest.raises(TypeError):
        stratification_variable(w)  # type: ignore[call-arg]


def test_using_one_seventh_shifts_every_distance(scheme):
    """用错中心不会报错，只会让所有人的 z 一起偏——所以要有一条测试盯着。"""
    rng = np.random.default_rng(3)
    w = rng.dirichlet(dirichlet_alpha_vector(), size=500)
    right = stratification_variable(w, dirichlet_mean())
    wrong = stratification_variable(w, np.full(N_DIMS, 1 / N_DIMS))
    assert not np.allclose(right, wrong, atol=1e-3)
    assert np.mean(wrong) - np.mean(right) > 0.01


def test_the_scheme_says_in_its_own_fingerprint_that_z_is_not_d(scheme):
    """指纹里写明分层变量是 z 不是 d。产出被人读到时这句必须在场。"""
    fp = scheme.fingerprint()
    assert "injection_mean" in fp["stratification_variable"]
    assert "fitted_centre" in fp["is_not"]


def test_the_boundaries_come_from_an_independent_reference_draw(scheme):
    prov = scheme.fingerprint()["reference_provenance"]
    assert prov["kind"].startswith("independent_reference_draw")
    assert prov["seed"] and prov["n_draws"] >= 10_000
    assert "not_derived_from" in prov


def test_equal_frequency_strata_have_equal_population_share(scheme):
    for p in scheme.population_share:
        assert p == pytest.approx(1 / scheme.n_strata, abs=0.01)


# --------------------------------------------------------------------------
# 配额与权重
# --------------------------------------------------------------------------


def test_equal_allocation_on_equal_frequency_strata_is_not_oversampling(scheme):
    """**等量 ≠ 过采样。** 等频层下 `p_h = 1/H`，等量就是按比例，权重恒为 1。

    把它一律说成"过采样了尾部"会让读的人以为人口量需要还原，而权重其实是 1。
    """
    note = Allocation.equal(scheme, 200).note
    assert "控制层人数" in note
    assert "过采样" not in note.replace("不是过采样尾部", "")


def test_a_genuinely_tail_heavy_allocation_is_called_oversampling(scheme):
    """反过来：真的非比例时必须说出来，否则上一条测的是个常数。"""
    note = describe_allocation((20, 20, 20, 40, 100), scheme.population_share)
    assert "非比例分配" in note
    assert "必须" in note


def test_proportional_allocation_gives_unit_weights(scheme):
    plan = SamplingPlan(scheme=scheme, allocation=Allocation.proportional(scheme, 250), seed=5)
    _, sample = draw_stratified(plan, pool_seed=1234)
    for s in sample.strata:
        assert s.design_weight == pytest.approx(1.0, abs=1e-9)


def test_weights_use_realised_counts_not_target_quotas():
    """尾层被抽干时实际人数低于配额。拿配额算权重，人口量直接偏。"""
    got, basis = weight_for(
        population_share=0.2,
        n_realised=10,  # 实际只抽到 10
        n_total=90,
        n_available=10,
        pool_total=1000,
        target="superpopulation",
    )
    assert got == pytest.approx(0.2 / (10 / 90))
    assert "n_h/N" in basis
    quota_based = 0.2 / (40 / 120)  # 若按配额 40、总量 120 算
    assert abs(got - quota_based) > 0.5, "两种算法必须给出不同的数，否则这条测试无意义"


def test_the_two_weight_targets_are_different_and_must_not_be_mixed():
    """超总体口径与有限库口径是两个估计对象。"""
    kw = dict(population_share=0.2, n_realised=50, n_total=200, n_available=100, pool_total=1000)
    sup, sup_basis = weight_for(**kw, target="superpopulation")
    fin, fin_basis = weight_for(**kw, target="finite_pool")
    assert sup == pytest.approx(0.2 / 0.25)
    assert fin == pytest.approx((100 / 1000) / 0.25)
    assert sup != pytest.approx(fin)
    assert "superpopulation" in sup_basis and "finite_pool" in fin_basis


def test_the_finite_pool_target_refuses_to_guess_the_pool_size():
    with pytest.raises(ValueError, match="库总人数"):
        weight_for(
            population_share=0.2,
            n_realised=50,
            n_total=200,
            n_available=100,
            pool_total=None,
            target="finite_pool",
        )


def test_a_finite_pool_that_runs_out_records_the_shortfall(scheme):
    """库里不够就如实少抽，**不从别的层借人**。"""
    greedy = Allocation.custom(tuple([400] * scheme.n_strata))
    plan = SamplingPlan(scheme=scheme, allocation=greedy, seed=5, pool_size=300)
    _, sample = draw_stratified(plan, pool_seed=77)

    assert sample.n < greedy.n_total
    for s in sample.strata:
        assert s.n_available is not None
        assert s.n_realised <= s.n_available
        assert s.n_realised < s.quota
    assert sum(s.n_realised for s in sample.strata) == sample.n


# --------------------------------------------------------------------------
# 抽样本身
# --------------------------------------------------------------------------


def test_the_draw_hits_its_quotas_and_labels_everyone(scheme):
    alloc = Allocation.custom((30, 30, 30, 30, 60))
    plan = SamplingPlan(scheme=scheme, allocation=alloc, seed=9)
    personas, sample = draw_stratified(plan, pool_seed=2024)

    assert sample.n == alloc.n_total
    assert len(personas) == sample.n
    assert set(sample.stratum_of) == set(sample.persona_ids)
    for h, s in enumerate(sample.strata):
        assert s.n_realised == alloc.quotas[h]


def test_everyone_actually_lands_in_the_stratum_they_are_labelled_with(scheme):
    """标签与 z 必须自洽。错位不会报错，只会让层内条件量指错人。"""
    plan = SamplingPlan(scheme=scheme, allocation=Allocation.proportional(scheme, 200), seed=4)
    _, sample = draw_stratified(plan, pool_seed=606)
    for pid, label in sample.stratum_of.items():
        h = scheme.labels.index(label)
        lo = scheme.edges[h - 1] if h > 0 else -np.inf
        hi = scheme.edges[h] if h < len(scheme.edges) else np.inf
        assert lo <= sample.z[pid] < hi


def test_the_same_plan_and_seed_give_the_same_people(scheme):
    plan = SamplingPlan(scheme=scheme, allocation=Allocation.proportional(scheme, 150), seed=42)
    a, sa = draw_stratified(plan, pool_seed=8)
    b, sb = draw_stratified(plan, pool_seed=8)
    assert sa.persona_ids == sb.persona_ids
    assert sa.stratum_of == sb.stratum_of
    assert [p.persona_id for p in a] == [p.persona_id for p in b]
    assert sa.plan.digest() == sb.plan.digest()


def test_a_different_plan_gets_a_different_digest(scheme):
    base = SamplingPlan(scheme=scheme, allocation=Allocation.proportional(scheme, 150), seed=42)
    other = SamplingPlan(
        scheme=scheme, allocation=Allocation.custom((10, 10, 10, 10, 110)), seed=42
    )
    assert base.digest() != other.digest()


def test_the_sample_records_everything_needed_to_trace_it(scheme):
    """人数、权重、persona ID、seed、配置指纹——缺一项就没法复现。"""
    plan = SamplingPlan(scheme=scheme, allocation=Allocation.proportional(scheme, 120), seed=3)
    _, sample = draw_stratified(plan, pool_seed=515)
    d = sample.as_dict()
    assert d["plan_digest"] and d["pool_seed"] == 515
    assert len(d["persona_ids"]) == d["n"]
    assert set(d["weight_of"]) == set(d["persona_ids"])
    for s in d["strata"]:
        assert {"label", "population_share", "quota", "n_realised", "design_weight"} <= set(s)


def test_personas_do_not_carry_a_world(scheme):
    """人设轴与世界轴分开：同一批人设可以投到多个世界（README §11.9.2）。"""
    plan = SamplingPlan(scheme=scheme, allocation=Allocation.proportional(scheme, 60), seed=2)
    personas, _ = draw_stratified(plan, pool_seed=1)
    for p in personas:
        assert not hasattr(p, "city")
        assert not hasattr(p, "world")


# --------------------------------------------------------------------------
# 两个报告接口
# --------------------------------------------------------------------------


def test_population_and_conditional_are_two_functions_not_a_flag():
    """接口形状本身就是约束（README §11.9.2）。"""
    import inspect

    for fn in (report_population, report_conditional):
        assert "weighted" not in inspect.signature(fn).parameters


def test_the_population_report_restores_the_design(scheme):
    tail_heavy = Allocation.custom((20, 20, 20, 40, 100))
    plan = SamplingPlan(scheme=scheme, allocation=tail_heavy, seed=6)
    personas, sample = draw_stratified(plan, pool_seed=313)
    z_of = {p.persona_id: sample.z[p.persona_id] for p in personas}

    pop = report_population(sample, z_of)
    assert pop["weights_applied"] is True
    # 尾部被过采样 ⟹ 未加权的平均 z 偏大，加权后回落
    assert pop["value"] < pop["unweighted_for_comparison"]
    assert pop["effective_sample_size"] < sample.n


def test_the_conditional_report_names_its_actual_stratification_variable(scheme):
    """**不能把按 z 分层冒充成按 d 的等频分箱。**"""
    plan = SamplingPlan(scheme=scheme, allocation=Allocation.proportional(scheme, 100), seed=6)
    personas, sample = draw_stratified(plan, pool_seed=314)
    out = report_conditional(sample, {p.persona_id: 1.0 for p in personas})

    assert out["weights_applied"] is False
    assert "injection_mean" in out["stratification_variable"]
    assert "fitted_centre" in out["is_not"]
    assert sum(v["n"] for v in out["by_stratum"].values()) == sample.n


def test_a_cross_stratum_regression_forces_an_explicit_choice(scheme):
    """跨层回归没有天然正确的口径，所以这个参数**不给默认值**。"""
    import inspect

    sig = inspect.signature(report_cross_stratum_relationship)
    assert sig.parameters["weighted"].default is inspect.Parameter.empty

    plan = SamplingPlan(scheme=scheme, allocation=Allocation.custom((20, 20, 20, 40, 100)), seed=6)
    personas, sample = draw_stratified(plan, pool_seed=315)
    x = {p.persona_id: sample.z[p.persona_id] for p in personas}
    y = {p.persona_id: float(weights_of(p)[0]) for p in personas}

    unw = report_cross_stratum_relationship(sample, x, y, weighted=False)
    wtd = report_cross_stratum_relationship(sample, x, y, weighted=True)
    assert unw["estimand"] != wtd["estimand"]
    assert unw["slope"] != pytest.approx(wtd["slope"])


def test_weighted_moments_do_not_claim_to_fix_the_fit(scheme):
    """加权只作用于报告。说"拟合矩已按人口校正"是错的。"""
    plan = SamplingPlan(scheme=scheme, allocation=Allocation.proportional(scheme, 80), seed=6)
    personas, sample = draw_stratified(plan, pool_seed=316)
    out = population_moments(sample, {p.persona_id: weights_of(p) for p in personas})
    assert out["applies_to"] == "reported moments only"
    assert "unweighted MSL" in out["does_not_apply_to"]


def test_the_estimand_gap_is_machine_readable():
    from eval.sampling import ESTIMAND_GAP

    assert ESTIMAND_GAP["report_weighting_does_not_fix_it"] is True
    assert ESTIMAND_GAP["status"] == "待科学裁决"
    assert "does_not_bite_when" in ESTIMAND_GAP


# --------------------------------------------------------------------------
# P8 的复用边界
# --------------------------------------------------------------------------


def _descriptor(**over):
    base = dict(
        design=ReferenceDesign(n_personas=100, n_choice_sets=4, n_cities=12),
        sigma_generating=0.05,
        noise_source="synthetic_truth_softmax",
        estimation_profile={"profile": "alr_full_material_2026_09", "n_draws": 200},
        target_population="dirichlet_1.5_0.5x6",
        injected_alpha=[1.5] + [0.5] * 6,
    )
    base.update(over)
    return descriptor_from_run(**base)


def test_an_identical_design_may_be_reused():
    v = check_reuse(_descriptor(), _descriptor())
    assert v.ok
    v.raise_if_refused()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("design", ReferenceDesign(n_personas=200, n_choice_sets=4, n_cities=12)),
        ("design", ReferenceDesign(n_personas=100, n_choice_sets=8, n_cities=12)),
        ("design", ReferenceDesign(n_personas=100, n_choice_sets=4, n_cities=9)),
        ("sigma_generating", 0.10),
        ("noise_source", "language_model"),
        ("target_population", "dirichlet_scaled_2x"),
        ("injected_alpha", [3.0] + [1.0] * 6),
        ("estimation_profile", {"profile": "alr_full_material_2026_09", "n_draws": 800}),
    ],
)
def test_any_design_difference_refuses_reuse(field, value):
    """每一项都单独试过。漏掉一项，那一项就成了静默复用的通道。"""
    v = check_reuse(_descriptor(), _descriptor(**{field: value}))
    assert not v.ok
    assert v.mismatches
    with pytest.raises(FloorReuseError):
        v.raise_if_refused()


def test_stratified_and_unstratified_floors_are_not_interchangeable(scheme):
    plan = SamplingPlan(scheme=scheme, allocation=Allocation.proportional(scheme, 100), seed=1)
    _, sample = draw_stratified(plan, pool_seed=9)

    unstratified = _descriptor()
    stratified = _descriptor(sampling=sample.as_dict())
    v = check_reuse(unstratified, stratified)
    assert not v.ok
    assert {m["field"] for m in v.mismatches} >= {"stratification", "allocation"}


def test_differing_choice_records_do_not_block_reuse():
    """地板臂与匹配臂的选择本来就不同。要求逐行相同等于要求它们是同一个臂。"""
    a = _descriptor(dataset_digest="aaaa", arm_source="synthetic_truth_softmax", run_id="r1")
    b = _descriptor(dataset_digest="bbbb", arm_source="language_model_arm", run_id="r2")
    v = check_reuse(a, b)
    assert v.ok, "只有 provenance 不同，不应影响复用资格"
    assert a.digest() == b.digest()


def test_the_verdict_says_what_it_compared():
    v = check_reuse(_descriptor(), _descriptor())
    d = v.as_dict()
    assert "estimation_profile" in d["compared"]
    assert "dataset_digest" in d["not_compared"]


def test_a_descriptor_missing_a_compared_field_is_rejected():
    with pytest.raises(ValueError, match="缺少必比字段"):
        FloorDescriptor(matched={"n_personas": 100})


# --------------------------------------------------------------------------
# P10 的层喂给 block bootstrap
# --------------------------------------------------------------------------


def test_p10_strata_feed_the_block_bootstrap_without_crossing_strata(scheme):
    """按人设分层重抽：同人全部任务一起抽，且**不跨层**。

    层标签由 P10 在抽样时就冻结，bootstrap 只是消费它。跨层重抽会破坏层的
    样本量设计，而那正是分层抽样唯一的产出。
    """
    from eval.recovery.bootstrap import resample_persona_ids

    from .synthetic import make_synthetic

    plan = SamplingPlan(scheme=scheme, allocation=Allocation.custom((6, 6, 6, 6, 6)), seed=8)
    _, sample = draw_stratified(plan, pool_seed=1111)

    syn = make_synthetic(n_personas=sample.n, n_tasks=3, n_alts=5, sigma=0.1, seed=21)
    ds = syn.dataset
    # 把 P10 的层标签贴到合成数据的人设上（按顺序一一对应）
    mapping = dict(
        zip(
            ds.persona_order,
            [sample.stratum_of[p] for p in sample.persona_ids],
            strict=True,
        )
    )
    ds = replace(ds, strata=mapping)

    drawn = resample_persona_ids(ds, np.random.default_rng(0), stratified=True)
    assert len(drawn) == ds.n_personas
    from collections import Counter

    before = Counter(mapping.values())
    after = Counter(mapping[p] for p in drawn)
    assert before == after, "每层的人数必须原样保留"


def test_duplicate_personas_get_independent_panel_ids(scheme):
    """同一个人被抽中两次要拿到两个面板 ID，否则等于把他的题数翻倍。"""
    from .synthetic import make_synthetic

    ds = make_synthetic(n_personas=6, n_tasks=3, n_alts=4, sigma=0.1, seed=22).dataset
    picks = [ds.persona_order[0], ds.persona_order[0], ds.persona_order[1]]
    sub = ds.take_personas(picks)

    assert len(set(sub.persona_order)) == 3, "三次抽取必须给出三个不同的面板 ID"
    assert sub.n_tasks == 9
    doubled = [p for p in sub.persona_order if "#" in p]
    assert len(doubled) == 1
    assert doubled[0].split("#")[0] == ds.persona_order[0], "重抽来源必须可还原"


# --------------------------------------------------------------------------
# t 路径评审稿里的闭式起点（纯数学，不跑估计）
# --------------------------------------------------------------------------


def test_the_dirichlet_alr_moments_match_the_closed_form():
    """`MEMOS/2026-09-26-t-path-proposal.md` 第三节的三个数。

    material 基准（α_b=1.5）、六个舒适度维（α_k=0.5）：

        μ_a,k  = ψ(0.5) − ψ(1.5) = −2
        对角   = ψ₁(0.5) + ψ₁(1.5) = π² − 4
        非对角 = ψ₁(1.5) = π²/2 − 4

    评审稿会被人照着实现，所以这三个数得有测试钉着，不能只写在文档里。
    """
    from scipy.special import digamma, polygamma

    a_b, a_k = 1.5, 0.5
    assert digamma(a_k) - digamma(a_b) == pytest.approx(-2.0)
    assert polygamma(1, a_k) + polygamma(1, a_b) == pytest.approx(np.pi**2 - 4)
    assert polygamma(1, a_b) == pytest.approx(np.pi**2 / 2 - 4)


def test_the_closed_form_alr_moments_agree_with_sampling():
    """闭式与抽样对得上。上一条只验了恒等式，这条验它描述的是同一个分布。"""
    w = np.random.default_rng(0).dirichlet(dirichlet_alpha_vector(), size=200_000)
    a = np.log(w[:, 1:]) - np.log(w[:, :1])
    cov = np.cov(a, rowvar=False)

    np.testing.assert_allclose(a.mean(axis=0), -2.0, atol=0.02)
    np.testing.assert_allclose(np.diag(cov), np.pi**2 - 4, rtol=0.02)
    off = cov[~np.eye(6, dtype=bool)]
    np.testing.assert_allclose(off, np.pi**2 / 2 - 4, rtol=0.05)


def test_the_mean_of_a_softmax_is_not_the_softmax_of_the_mean():
    """`c` 必须取 `E[w]`，不能用 `softmax(μ_a)`（评审稿第一节）。

    这是 Jensen，与"个体后验不能用 `softmax(E[a|choices])`"是同一条。
    差距要大到足以改变结论，否则这条约束不值一提——这里量出来。
    """
    mu = np.array([-2.0] * 6)
    chol = np.linalg.cholesky((np.pi**2 - 4) * np.eye(6))
    xi = np.random.default_rng(1).standard_normal((200_000, 6))
    a = mu + xi @ chol.T

    full = np.concatenate([np.zeros((a.shape[0], 1)), a], axis=1)
    e_w = np.exp(full - full.max(1, keepdims=True))
    e_w = (e_w / e_w.sum(1, keepdims=True)).mean(0)

    naive_full = np.concatenate([[0.0], mu])
    naive = np.exp(naive_full - naive_full.max())
    naive = naive / naive.sum()

    assert abs(e_w[0] - naive[0]) > 0.05, "两者差距必须实质，否则这条约束不重要"
