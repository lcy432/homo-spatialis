"""P10 的配额与设计权重。

**配额是设计决定，本模块不替它预注册。** `Allocation` 只提供三种显式方案与一个
自定义入口；`configs/` 里写哪一组、由谁在什么时候定死，是人的事。测试与示例里
出现的数字**一律标 `provisional`**，不得被读成已预注册的值。

权重口径：

    比例分配（quota_h ∝ p_h）        → 权重恒为 1。这是**控制层人数**，
                                       不是过采样尾部。报告里必须这么说。
    非比例分配（尾部加配额）          → 权重 p_h / (n_h/N)，N = Σ n_h。
    有限候选库（池子里某层不够抽）    → 权重按**实际纳入概率**算，不能沿用
                                       目标配额那一套，否则权重和实际抽法对不上。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from .strata import StratumScheme

AllocationKind = Literal["proportional", "equal", "custom"]


#: 相对配额与总体比例的偏离超过这个就算"真的在过采样/欠采样"。
#: 0.02 是舍入容差：整数配额除不尽时必然有一两个人的偏差。
ALLOCATION_TOLERANCE = 0.02


def describe_allocation(quotas: tuple[int, ...], population_share: Sequence[float]) -> str:
    """把"这组配额到底在做什么"算出来，而不是按 `kind` 猜。

    `Allocation.equal` 在等频层下等同于按比例——那时说"过采样尾部"是错的。
    错误的说明比没有说明更糟：它会让读的人以为人口量需要还原，而权重其实是 1。
    """
    n = sum(quotas)
    if n <= 0:
        return "空配额"
    dev = [q / n - p for q, p in zip(quotas, population_share, strict=True)]
    if max(abs(d) for d in dev) <= ALLOCATION_TOLERANCE:
        return (
            "配额与总体比例一致（容差内）：这是**控制层人数**，不是过采样尾部；"
            "设计权重恒为 1，人口量无需还原"
        )
    over = [i for i, d in enumerate(dev) if d > ALLOCATION_TOLERANCE]
    under = [i for i, d in enumerate(dev) if d < -ALLOCATION_TOLERANCE]
    return (
        f"非比例分配：层 {over} 被过采样、层 {under} 被欠采样；人口层面的报告**必须**用设计权重还原"
    )


@dataclass(frozen=True)
class Allocation:
    """每层要抽多少人。

    `status` 区分"已预注册"与"占位"。默认是 `provisional`——**没有人裁决过的
    数字不得自称预注册**，而一个自称预注册的占位值会在半年后被当成事前承诺。
    """

    quotas: tuple[int, ...]
    kind: AllocationKind
    status: Literal["provisional", "preregistered"] = "provisional"
    note: str = ""

    @property
    def n_total(self) -> int:
        return int(sum(self.quotas))

    @staticmethod
    def proportional(scheme: StratumScheme, n_total: int, **kw: Any) -> Allocation:
        """按总体比例分配。**权重恒为 1；这是控制层人数，不是过采样。**

        它仍然有用：简单随机抽样下每层人数是随机的，尾层可能只抽到十几个，
        `r(d)` 的最外一箱就估不动。比例分配把人数钉死，边际分布不变。
        """
        share = np.asarray(scheme.population_share, dtype=float)
        raw = share * n_total
        quotas = np.floor(raw).astype(int)
        for i in np.argsort(-(raw - quotas))[: n_total - int(quotas.sum())]:
            quotas[i] += 1
        return Allocation(
            quotas=tuple(int(v) for v in quotas),
            kind="proportional",
            note=describe_allocation(tuple(int(v) for v in quotas), scheme.population_share),
            **kw,
        )

    @staticmethod
    def equal(scheme: StratumScheme, n_total: int, **kw: Any) -> Allocation:
        """每层等量。

        **是否构成过采样取决于层本身。** 等频边界下 `p_h = 1/H`，于是"每层等量"
        与"按比例"是同一件事，权重恒为 1——这时说"过采样了尾部"是错的。只有
        层的总体比例本来就不等（例如等宽边界）时，等量分配才真的在过采样稀疏层。
        所以这里的说明由配额与 `p_h` **算出来**，不写死。
        """
        h = scheme.n_strata
        quotas = np.full(h, n_total // h, dtype=int)
        quotas[: n_total - int(quotas.sum())] += 1
        return Allocation(
            quotas=tuple(int(v) for v in quotas),
            kind="equal",
            note=describe_allocation(tuple(int(v) for v in quotas), scheme.population_share),
            **kw,
        )

    @staticmethod
    def custom(quotas: tuple[int, ...], **kw: Any) -> Allocation:
        return Allocation(quotas=tuple(int(v) for v in quotas), kind="custom", **kw)

    def fingerprint(self) -> dict[str, Any]:
        return {
            "quotas": list(self.quotas),
            "kind": self.kind,
            "status": self.status,
            "n_total": self.n_total,
            "note": self.note,
        }


@dataclass(frozen=True)
class SamplingPlan:
    """层方案 + 配额 + seed。**抽样之前**就完整确定，指纹进产出。"""

    scheme: StratumScheme
    allocation: Allocation
    seed: int
    #: 候选库规模。抽样从这里面挑人；`None` 表示按需一直抽到配额满。
    pool_size: int | None = None

    def fingerprint(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme.fingerprint(),
            "allocation": self.allocation.fingerprint(),
            "seed": self.seed,
            "pool_size": self.pool_size,
        }

    def digest(self) -> str:
        blob = json.dumps(self.fingerprint(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


@dataclass(frozen=True)
class StratumRealisation:
    """一层实际抽成了什么样。目标与实际分开记——它们会不一样。"""

    label: str
    population_share: float
    quota: int
    n_realised: int
    #: 候选库里这一层有多少人。有限库下它可能小于配额。
    n_available: int | None
    design_weight: float
    weight_basis: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "population_share": self.population_share,
            "quota": self.quota,
            "n_realised": self.n_realised,
            "n_available": self.n_available,
            "design_weight": self.design_weight,
            "weight_basis": self.weight_basis,
        }


def design_weights(
    strata: list[StratumRealisation],
) -> dict[str, float]:
    """层标签 → 设计权重。人口报告用它把过采样还原回总体。"""
    return {s.label: s.design_weight for s in strata}


#: 设计权重还原到**哪个**总体。两个目标都正当，但它们是两个不同的估计对象，
#: **不得混用**：一次分析里只能有一个 `weight_target`，并且要写进产出。
WeightTarget = Literal["superpopulation", "finite_pool"]


def weight_for(
    *,
    population_share: float,
    n_realised: int,
    n_total: int,
    n_available: int | None,
    pool_total: int | None,
    target: WeightTarget,
) -> tuple[float, str]:
    """一层的设计权重，并说明它按哪种口径算。

    两种口径都要求加权层均值还原成 `Σ_h (层权重) × E[y | 层]`，差别只在
    "层权重"取谁：

    **superpopulation**：目标是注入分布（Dirichlet）本身，层权重取理论/参照
    总体的 `p_h`。归一到 `Σ_h w_h n_h = N` 之后

        w_h = p_h / (n_h / N)

    **finite_pool**：目标是手上这个候选库，层权重取库里的实际占比
    `M_h / M`，而层内的纳入概率是 `π_h = n_h / M_h`。Horvitz–Thompson 权重
    `1/π_h = M_h / n_h`，同样归一之后

        w_h = (M_h / M) / (n_h / N)

    **两处都用实际抽到的 `n_h`，不用目标配额。** 尾层被抽干（`n_h = M_h`）时
    实际人数会低于配额，拿配额算权重就和实际抽法对不上，人口量直接偏。
    """
    if n_realised <= 0:
        return (0.0, "empty_stratum")
    sample_share = n_realised / n_total
    if target == "superpopulation":
        return (population_share / sample_share, "p_h / (n_h/N), target=superpopulation")
    if n_available is None or pool_total is None:
        raise ValueError("finite_pool 口径需要该层的库内人数与库总人数")
    if n_available <= 0 or pool_total <= 0:
        return (0.0, "empty_stratum_in_pool")
    return (
        (n_available / pool_total) / sample_share,
        "(M_h/M) / (n_h/N), target=finite_pool (Horvitz-Thompson)",
    )


@dataclass(frozen=True)
class StratifiedSample:
    """抽样结果。**溯源所需的一切都在这里**，不靠调用方另记。"""

    persona_ids: tuple[str, ...]
    stratum_of: dict[str, str]
    z: dict[str, float]
    weight_of: dict[str, float]
    strata: tuple[StratumRealisation, ...]
    plan: SamplingPlan
    pool_seed: int
    unused_pool_ids: tuple[str, ...] = field(default=(), repr=False)

    @property
    def n(self) -> int:
        return len(self.persona_ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "plan_digest": self.plan.digest(),
            "plan": self.plan.fingerprint(),
            "pool_seed": self.pool_seed,
            "strata": [s.as_dict() for s in self.strata],
            "weights_are_all_one": all(abs(w - 1.0) < 1e-12 for w in self.weight_of.values()),
            "persona_ids": list(self.persona_ids),
            "stratum_of": dict(self.stratum_of),
            "weight_of": dict(self.weight_of),
        }
