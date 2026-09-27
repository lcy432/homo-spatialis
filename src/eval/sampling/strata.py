"""P10 的分层变量、层边界与层的总体比例。

**分层变量是 `z_i = ‖w_i − μ₀‖`，其中 μ₀ 是注入分布的均值**，非对称 Dirichlet
下是 `[1/3, 1/9×6]` 而不是 `1/7`（`DECISIONS/0006`，硬规则 21）。它**事前可算**：
α 是设计选择，跑之前就定死。

**`z_i` 与 `d_i` 是两个东西，永远不要互换。**

    z_i = ‖w_i − μ₀‖   抽样变量。μ₀ 是设计常量，抽样之前就知道。
    d_i = ‖w_i − ĉ‖    分析变量。ĉ 是匹配/参照臂**拟合出来的**中心。

硬规则 21 禁止的正是用 `d_i` 分层：那要等跑完才有，而抽样必须在跑之前完成，
否则处理效应从分层这条路偷渡进样本。本模块只产出 `z`，没有任何函数接受 ĉ。

**边界与层概率来自独立参照总体，不来自本轮的行为结果。** `ReferencePopulation`
按注入 α 抽一批**只用来定边界**的权重向量，seed 与 n 都记进指纹。任何"看完
结果再调箱"的做法都会让层变成结果的函数，那时层内条件量不再有事前含义。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from worlds.personas import dirichlet_alpha_vector, dirichlet_mean

#: 层标签按 z 从小到大。名字写死，因为它们会进产出、进分箱、进报告。
DEFAULT_STRATUM_LABELS: tuple[str, ...] = ("s1_near", "s2", "s3", "s4", "s5_far")


def stratification_variable(weights: np.ndarray, centre: np.ndarray) -> np.ndarray:
    """`z_i = ‖w_i − centre‖`。**`centre` 必须显式传**，没有默认值。

    没有默认值是有意的：默认 `1/7` 正是 `DECISIONS/0006` 修掉的那个 bug，
    它给所有人的距离加一个固定偏移，层整体歪掉而代码照常跑完。
    """
    w = np.atleast_2d(np.asarray(weights, dtype=float))
    c = np.asarray(centre, dtype=float)
    if c.ndim != 1 or c.shape[0] != w.shape[1]:
        raise ValueError(f"中心维数 {c.shape} 与权重 {w.shape} 不匹配")
    return np.linalg.norm(w - c, axis=1)


@dataclass(frozen=True)
class ReferencePopulation:
    """定边界与层概率用的独立参照样本。**不是实验样本。**

    它存在的理由只有一个：边界必须**事前**定死。用实验样本自己的分位数定边界，
    边界就成了样本的函数；等到看过结果再动它，层内条件量就失去事前含义。
    """

    alpha: tuple[float, ...]
    n_draws: int
    seed: int
    centre: tuple[float, ...]
    #: 只有摘要进指纹；整批 z 留在内存里供分位数计算。
    z: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))

    @staticmethod
    def draw(
        *,
        alpha: np.ndarray | None = None,
        n_draws: int = 200_000,
        seed: int = 20_260_926,
    ) -> ReferencePopulation:
        a = np.asarray(alpha if alpha is not None else dirichlet_alpha_vector(), dtype=float)
        centre = dirichlet_mean(list(a))
        w = np.random.default_rng(seed).dirichlet(a, size=n_draws)
        return ReferencePopulation(
            alpha=tuple(float(v) for v in a),
            n_draws=n_draws,
            seed=seed,
            centre=tuple(float(v) for v in centre),
            z=stratification_variable(w, centre),
        )

    def provenance(self) -> dict[str, Any]:
        """边界是**怎么来的**。没有这个，"事前定死"就只是一句声明。"""
        return {
            "kind": "independent_reference_draw_from_injection_alpha",
            "alpha": list(self.alpha),
            "n_draws": self.n_draws,
            "seed": self.seed,
            "centre": list(self.centre),
            "centre_source": "worlds.personas.dirichlet_mean (injection mean, NOT 1/7)",
            "not_derived_from": "this round's behavioural results or any fitted quantity",
        }


@dataclass(frozen=True)
class StratumScheme:
    """层的定义：内部边界、标签、总体比例 `p_h`。

    `p_h` 是**总体**里落在该层的比例，从参照总体算，与实验里抽了多少人无关。
    等频边界下它接近 `1/H`，但仍然如实报实测值——蒙特卡洛误差是真的。
    """

    labels: tuple[str, ...]
    #: H−1 个内部边界，递增。层 h 是 `z ∈ [edge_{h−1}, edge_h)`，两端开放。
    edges: tuple[float, ...]
    population_share: tuple[float, ...]
    centre: tuple[float, ...]
    reference_provenance: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if len(self.edges) != len(self.labels) - 1:
            raise ValueError(f"{len(self.labels)} 层需要 {len(self.labels) - 1} 个边界")
        if list(self.edges) != sorted(self.edges):
            raise ValueError("边界必须递增")
        total = sum(self.population_share)
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"层的总体比例必须和为一，得到 {total!r}")

    @property
    def n_strata(self) -> int:
        return len(self.labels)

    @staticmethod
    def equal_frequency(
        reference: ReferencePopulation,
        *,
        labels: tuple[str, ...] = DEFAULT_STRATUM_LABELS,
    ) -> StratumScheme:
        """等频（不等宽）边界，与 README §10.3.4 的 `r(d)` 分箱口径一致。"""
        h = len(labels)
        qs = np.linspace(0.0, 1.0, h + 1)[1:-1]
        edges = tuple(float(v) for v in np.quantile(reference.z, qs))
        counts = np.bincount(np.searchsorted(edges, reference.z, side="right"), minlength=h)
        return StratumScheme(
            labels=labels,
            edges=edges,
            population_share=tuple(float(c / reference.z.size) for c in counts),
            centre=reference.centre,
            reference_provenance=reference.provenance(),
        )

    def assign(self, z: np.ndarray) -> np.ndarray:
        """把 z 映到层下标。边界之外的值落进首层/末层，不报错。"""
        return np.searchsorted(np.asarray(self.edges), np.asarray(z, dtype=float), side="right")

    def label_of(self, z: np.ndarray) -> list[str]:
        return [self.labels[i] for i in self.assign(z)]

    def fingerprint(self) -> dict[str, Any]:
        return {
            "labels": list(self.labels),
            "edges": list(self.edges),
            "population_share": list(self.population_share),
            "centre": list(self.centre),
            "stratification_variable": "z = ||w - injection_mean||",
            "is_not": "d = ||w - fitted_centre||",
            "reference_provenance": self.reference_provenance,
        }

    def digest(self) -> str:
        blob = json.dumps(self.fingerprint(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]
