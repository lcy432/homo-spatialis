"""按人设重抽的 block bootstrap（P9 第九、十一节）。"""

from __future__ import annotations

import numpy as np
import pytest

from eval.recovery import (
    RecoveryConfig,
    block_bootstrap,
    default_metrics,
    resample_persona_ids,
    stack_metric,
)
from eval.recovery import bootstrap as bootstrap_module

from .synthetic import make_synthetic

TINY = RecoveryConfig(n_draws=30, seed=0, maxiter=120)


# --------------------------------------------------------------------------
# 抽样单位
# --------------------------------------------------------------------------


def test_the_resampling_unit_is_the_persona_not_the_choice():
    s = make_synthetic(n_personas=20, n_tasks=4, n_alts=4, seed=41)
    d = s.dataset
    picks = resample_persona_ids(d, np.random.default_rng(0))

    assert len(picks) == d.n_personas
    assert set(picks) <= set(d.persona_order)

    resampled = d.take_personas(picks)
    # 每个被抽中的副本都带着这个人全部的选择集，一个不少
    assert resampled.n_tasks == 4 * len(picks)
    assert np.all(resampled.tasks_per_persona == 4)


def test_a_persona_drawn_twice_gets_a_fresh_cluster_id():
    """重复抽中不能被后端当成同一个面板——那等于把这个人的选择次数翻倍。"""
    s = make_synthetic(n_personas=5, n_tasks=3, n_alts=4, seed=43)
    d = s.dataset
    target = d.persona_order[0]
    resampled = d.take_personas([target, target, d.persona_order[1]])

    assert resampled.n_personas == 3
    assert len(set(resampled.persona_order)) == 3
    assert target in resampled.persona_order
    assert f"{target}#1" in resampled.persona_order
    assert np.all(resampled.tasks_per_persona == 3)
    # 选择集 id 也要跟着分开，否则"一个选择集属于一个人设"的约束会被违反
    assert len(set(resampled.choice_set_ids)) == resampled.n_tasks


def test_every_within_persona_choice_travels_with_the_block():
    s = make_synthetic(n_personas=8, n_tasks=5, n_alts=4, seed=47)
    d = s.dataset
    target = d.persona_order[3]
    original = d.features[np.array(d.persona_ids) == target]
    resampled = d.take_personas([target])
    assert resampled.n_rows == original.shape[0]
    assert np.array_equal(np.sort(resampled.features, axis=0), np.sort(original, axis=0))


def test_stratified_resampling_never_crosses_a_stratum():
    s = make_synthetic(n_personas=30, n_tasks=3, n_alts=4, seed=53, strata=True)
    d = s.dataset
    assert d.strata is not None
    counts = {}
    for p in d.persona_order:
        counts[d.strata[p]] = counts.get(d.strata[p], 0) + 1

    picks = resample_persona_ids(d, np.random.default_rng(1), stratified=True)
    drawn = {}
    for p in picks:
        drawn[d.strata[p]] = drawn.get(d.strata[p], 0) + 1
    assert drawn == counts

    resampled = d.take_personas(picks)
    assert resampled.strata is not None
    for tag, label in resampled.strata.items():
        assert label == d.strata[tag.split("#", 1)[0]]


def test_stratified_resampling_without_labels_is_an_error():
    s = make_synthetic(n_personas=6, n_tasks=2, n_alts=3, seed=59)
    with pytest.raises(ValueError, match="stratum_id"):
        resample_persona_ids(s.dataset, np.random.default_rng(0), stratified=True)


# --------------------------------------------------------------------------
# 重跑
# --------------------------------------------------------------------------


def test_each_replicate_refits_the_whole_pipeline(monkeypatch):
    """不是对既有点估计重采样——那只会把点估计的数值抖动报成置信区间。"""
    s = make_synthetic(n_personas=18, n_tasks=3, n_alts=4, seed=61)
    calls = {"n": 0}
    real = bootstrap_module.fit_recovery

    def counting(dataset, config):
        calls["n"] += 1
        return real(dataset, config)

    monkeypatch.setattr(bootstrap_module, "fit_recovery", counting)
    outcome = block_bootstrap(s.dataset, TINY, n_replicates=4, seed=7, metric_fn=default_metrics)
    assert calls["n"] == 4
    assert len(outcome.successes) == 4
    # 四次重拟合给出四个不同的 γ_0（同一个点估计重采样不会）
    assert len(set(outcome.metric_values("gamma_0").round(9))) == 4


def test_a_failed_replicate_is_recorded_with_its_reason():
    s = make_synthetic(n_personas=12, n_tasks=3, n_alts=4, seed=67)

    def flaky(result):
        if result.gamma_0 > 0:
            raise RuntimeError("模拟的下游指标失败")
        return {}

    outcome = block_bootstrap(s.dataset, TINY, n_replicates=3, seed=2, metric_fn=flaky)
    assert outcome.n_failed == 3
    assert outcome.failure_rate == 1.0
    reasons = outcome.failure_reasons()
    assert sum(reasons.values()) == 3
    assert any("模拟的下游指标失败" in k for k in reasons)
    assert outcome.summary()["n_succeeded"] == 0


def test_the_bootstrap_is_reproducible_under_a_fixed_seed():
    s = make_synthetic(n_personas=15, n_tasks=3, n_alts=4, seed=71)
    a = block_bootstrap(s.dataset, TINY, n_replicates=3, seed=5, metric_fn=default_metrics)
    b = block_bootstrap(s.dataset, TINY, n_replicates=3, seed=5, metric_fn=default_metrics)
    c = block_bootstrap(s.dataset, TINY, n_replicates=3, seed=6, metric_fn=default_metrics)

    assert [r.persona_draw for r in a.replicates] == [r.persona_draw for r in b.replicates]
    assert np.array_equal(a.metric_values("gamma_0"), b.metric_values("gamma_0"))
    assert [r.persona_draw for r in a.replicates] != [r.persona_draw for r in c.replicates]


def test_percentile_intervals_and_stacking_come_out_of_the_same_replicates():
    s = make_synthetic(n_personas=20, n_tasks=4, n_alts=4, seed=73)
    outcome = block_bootstrap(s.dataset, TINY, n_replicates=6, seed=3, metric_fn=default_metrics)
    lo, hi = outcome.percentile_interval("sigma_hat", level=0.9)
    assert lo <= hi
    values = outcome.metric_values("sigma_hat")
    assert values.min() <= lo and hi <= values.max()

    names = ["gamma_0", "sigma_hat", "trace_sigma_w"]
    block = stack_metric(outcome, names)
    assert block.shape == (len(outcome.successes), 3)
    assert np.allclose(block[:, 1], outcome.metric_values("sigma_hat"))

    summary = outcome.summary()
    assert summary["n_requested"] == 6
    assert "sigma_hat" in summary["intervals"]


def test_the_downstream_metric_is_a_callback_not_a_hardcoded_formula():
    """`DECISIONS/0002` 的 r 公式换过一次；bootstrap 不该认识任何一版。

    这里塞进去一个明显不是坍缩比例的指标，它照样被算出来并给出区间——说明
    P9 对指标版本是中立的。
    """
    s = make_synthetic(n_personas=12, n_tasks=3, n_alts=4, seed=79)
    outcome = block_bootstrap(
        s.dataset,
        TINY,
        n_replicates=3,
        seed=11,
        metric_fn=lambda r: {"anything_at_all": float(np.trace(r.sigma_w_hat)) * 2.0},
    )
    assert outcome.metric_values("anything_at_all").size == 3

    # 不给 metric_fn 时 P9 一个下游指标都不算——它没有自己的那一版可写死。
    # （不要改成在源码里搜公式：模块文档里正写着那个已作废的式子，文本搜索
    #  会匹配到解释文字本身。同样的坑在泄漏扫描那边已经踩过一次。）
    import inspect

    assert "metric_fn" in inspect.signature(block_bootstrap).parameters
    bare = block_bootstrap(s.dataset, TINY, n_replicates=2, seed=11)
    assert all(r.metrics is None for r in bare.replicates)
    assert bare.summary()["intervals"] == {}


def test_holding_the_draws_fixed_is_an_option_not_the_default():
    s = make_synthetic(n_personas=14, n_tasks=3, n_alts=4, seed=83)
    varying = block_bootstrap(s.dataset, TINY, n_replicates=3, seed=4, metric_fn=default_metrics)
    fixed = block_bootstrap(
        s.dataset,
        TINY,
        n_replicates=3,
        seed=4,
        metric_fn=default_metrics,
        resample_draws=False,
    )
    assert [r.persona_draw for r in varying.replicates] == [
        r.persona_draw for r in fixed.replicates
    ]
    assert not np.array_equal(varying.metric_values("gamma_0"), fixed.metric_values("gamma_0"))


def test_the_bootstrap_supports_both_parameterizations():
    """第十二节：只增加对 ALR 结果的支持，抽样逻辑一行未改。

    ``parameterization`` 在 ``RecoveryConfig`` 上，所以 bootstrap 不需要知道
    有几种参数化——它照原样把 config 传给 ``fit_recovery``。
    """
    from dataclasses import replace

    s = make_synthetic(n_personas=25, n_tasks=3, n_alts=4, seed=91, strata=True)
    for par in ("difference_normal", "alr_logistic_normal"):
        cfg = replace(TINY, parameterization=par)
        out = block_bootstrap(
            s.dataset, cfg, n_replicates=3, seed=3, stratified=True, metric_fn=default_metrics
        )
        assert len(out.successes) == 3, out.failure_reasons()
        assert all(r.result is None for r in out.replicates)  # keep_results 默认关
        assert all(len(r.persona_draw) == s.dataset.n_personas for r in out.replicates)
        lo, hi = out.percentile_interval("sigma_hat")
        assert np.isfinite(lo) and np.isfinite(hi) and lo <= hi
        # 分层重抽不跨层，与参数化无关
        drawn = {s.dataset.strata[p] for p in out.replicates[0].persona_draw}
        assert drawn <= set(s.dataset.strata.values())
