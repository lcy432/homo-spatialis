"""按人设重抽的 block bootstrap（README §11.7.4，硬规则 22）。

**重抽人设，不重抽个体内的选择。** 个体内的选择共享同一个 `w_i`，重抽它们会
低估方差；而要报的量（Σ_w、`ŵ_i = γ_k/γ_0`、下游的 `r(t)`）全是比值或比值的
非线性函数，delta 方法要带上 `Cov(γ_0, γ_k)` 再为 `w_b = 1 − Σw_k` 传播一层，
每个量都得单独推。

**每个 replicate 重新拟合整条流水线**，不是对既有点估计重采样——后者只会把
点估计的数值抖动报成置信区间。

**下游指标走 callback，不写死在这里。** 坍缩比例的现行公式是
``r = (ŝ_matched − λ̂)/(ŝ_shuffled − λ̂)``（`DECISIONS/0002`，硬规则 23），
而旧式 ``(ŝ − λ̂)/(1 − λ̂)`` 已作废。P9 不认识任何一个：它把 ``RecoveryResult``
交给调用方给的函数，指标版本由调用方决定。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .estimator import RecoveryConfig, fit_recovery
from .results import RecoveryResult
from .schema import ChoiceDataset

MetricFn = Callable[[RecoveryResult], Mapping[str, float]]
#: 每个 replicate 用哪个拟合流程。默认 ``fit_recovery``；P8 与主实验传
#: ``eval.recovery.profiles.fit_research`` 的适配器，保证 bootstrap 与点估计
#: 走的是**同一套**起点集合、draws 与容差。
FitFn = Callable[[ChoiceDataset, RecoveryConfig], RecoveryResult]


@dataclass(frozen=True)
class BootstrapReplicate:
    index: int
    seed: int
    ok: bool
    failure: str | None = None
    metrics: Mapping[str, float] | None = None
    result: RecoveryResult | None = field(default=None, repr=False)
    persona_draw: tuple[str, ...] = ()


@dataclass(frozen=True)
class BootstrapOutcome:
    replicates: tuple[BootstrapReplicate, ...]
    n_requested: int
    stratified: bool
    seed: int

    @property
    def successes(self) -> tuple[BootstrapReplicate, ...]:
        return tuple(r for r in self.replicates if r.ok)

    @property
    def n_failed(self) -> int:
        return sum(1 for r in self.replicates if not r.ok)

    @property
    def failure_rate(self) -> float:
        return self.n_failed / max(1, len(self.replicates))

    def failure_reasons(self) -> dict[str, int]:
        """失败**不静默丢弃**：原因按类计数，进结果表。"""
        out: dict[str, int] = {}
        for r in self.replicates:
            if not r.ok and r.failure is not None:
                key = r.failure.split("\n", 1)[0][:120]
                out[key] = out.get(key, 0) + 1
        return out

    def metric_values(self, name: str) -> np.ndarray:
        return np.array(
            [r.metrics[name] for r in self.successes if r.metrics and name in r.metrics],
            dtype=float,
        )

    def percentile_interval(self, name: str, level: float = 0.95) -> tuple[float, float]:
        v = self.metric_values(name)
        if v.size == 0:
            return (float("nan"), float("nan"))
        lo, hi = (1.0 - level) / 2.0, 1.0 - (1.0 - level) / 2.0
        return (float(np.quantile(v, lo)), float(np.quantile(v, hi)))

    def summary(self) -> dict[str, Any]:
        names: set[str] = set()
        for r in self.successes:
            if r.metrics:
                names.update(r.metrics)
        return {
            "n_requested": self.n_requested,
            "n_succeeded": len(self.successes),
            "n_failed": self.n_failed,
            "failure_rate": self.failure_rate,
            "failure_reasons": self.failure_reasons(),
            "stratified": self.stratified,
            "seed": self.seed,
            "intervals": {n: list(self.percentile_interval(n)) for n in sorted(names)},
        }


def resample_persona_ids(
    dataset: ChoiceDataset, rng: np.random.Generator, *, stratified: bool = False
) -> list[str]:
    """有放回地抽人设。分层时在层内抽，层的样本量保持原样。"""
    ids = list(dataset.persona_order)
    if not stratified:
        return list(rng.choice(ids, size=len(ids), replace=True))
    if dataset.strata is None:
        raise ValueError("要分层重抽，数据集必须带 stratum_id")
    groups: dict[str, list[str]] = {}
    for p in ids:
        groups.setdefault(dataset.strata[p], []).append(p)
    out: list[str] = []
    for name in sorted(groups):
        members = groups[name]
        out.extend(rng.choice(members, size=len(members), replace=True))
    return out


def block_bootstrap(
    dataset: ChoiceDataset,
    config: RecoveryConfig | None = None,
    *,
    n_replicates: int = 200,
    seed: int = 0,
    stratified: bool = False,
    metric_fn: MetricFn | None = None,
    keep_results: bool = False,
    resample_draws: bool = True,
    fit_fn: FitFn | None = None,
) -> BootstrapOutcome:
    """跑 ``n_replicates`` 次完整重拟合。固定 seed 下完全可复现。

    ``resample_draws`` 让每个 replicate 用不同的模拟抽样点。默认开启：抽样
    噪声是估计量不确定性的一部分，固定住它会低估区间。关掉它可以把"重抽
    人设"与"换 draws"两个来源分开看。
    """
    cfg = config or RecoveryConfig()
    rng = np.random.default_rng(seed)
    seeds = rng.integers(0, 2**31 - 1, size=n_replicates)
    reps: list[BootstrapReplicate] = []

    for i in range(n_replicates):
        rep_seed = int(seeds[i])
        picks = resample_persona_ids(
            dataset, np.random.default_rng(rep_seed), stratified=stratified
        )
        try:
            resampled = dataset.take_personas(picks)
            rep_cfg = cfg if not resample_draws else _with_seed(cfg, rep_seed)
            result = (fit_fn or fit_recovery)(resampled, rep_cfg)
            metrics = dict(metric_fn(result)) if metric_fn is not None else None
            reps.append(
                BootstrapReplicate(
                    index=i,
                    seed=rep_seed,
                    ok=True,
                    metrics=metrics,
                    result=result if keep_results else None,
                    persona_draw=tuple(picks),
                )
            )
        except Exception as exc:
            reps.append(
                BootstrapReplicate(
                    index=i,
                    seed=rep_seed,
                    ok=False,
                    failure=f"{type(exc).__name__}: {exc}",
                    persona_draw=tuple(picks),
                )
            )

    return BootstrapOutcome(
        replicates=tuple(reps), n_requested=n_replicates, stratified=stratified, seed=seed
    )


def _with_seed(config: RecoveryConfig, seed: int) -> RecoveryConfig:
    from dataclasses import replace

    return replace(config, seed=seed)


def default_metrics(result: RecoveryResult) -> dict[str, float]:
    """一组不依赖任何下游指标版本的量，方便直接拿到置信区间。"""
    out: dict[str, float] = {
        "gamma_0": result.gamma_0,
        "sigma_hat": result.sigma_hat,
        "trace_sigma_w": float(np.trace(result.sigma_w_hat)),
    }
    for i, d in enumerate(result.dimensions):
        out[f"w_center[{d}]"] = float(result.w_center_hat[i])
        out[f"var_w[{d}]"] = float(result.sigma_w_hat[i, i])
    return out


def stack_metric(outcome: BootstrapOutcome, names: Sequence[str]) -> np.ndarray:
    """成功 replicate × 指标名 的矩阵，供调用方自己算协方差或联合区间。"""
    rows = [[r.metrics[n] for n in names] for r in outcome.successes if r.metrics is not None]
    return np.array(rows, dtype=float)
