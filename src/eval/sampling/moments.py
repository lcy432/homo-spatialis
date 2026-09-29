"""三个总体，三套矩。**不要再说"合成数据没有总体"。**

    P   原 Dirichlet 生成总体。α = (1.5, 0.5×6)，闭式矩已知。
    Q   冻结的分层配额所定义的实验人群。设计的产物，不是抽出来的。
    S   这次实际抽出的那份名单。有限、已知、可以逐人枚举。

**主回收结果的估计对象是 S**（`DECISIONS/0015` 裁决三）：P9 未加权拟合面向实验
抽样人群，所以协方差评价的分母取**本次实际注入人设的真值矩**，不取 P 的闭式解。
P 的闭式矩保留作数学 fixture 与人口口径报告的还原目标，**不再兼任过采样实验
人群的真值**。

**无权重的过采样样本偏离 P 的矩不是抽样错误**，是设计的直接后果。为了让它贴近
`1/3` 去调权重或调边界，才是错误。

## ddof 的约定

全部用 **ddof = 0**（除以 N，不是 N−1），并在产出里显式写出来：

    m_S    = (1/N) Σ_i w_i
    C_w,S  = (1/N) Σ_i (w_i − m_S)(w_i − m_S)'
    C_a,S  = (1/N) Σ_i (a_i − ā)(a_i − ā)',   a_i,k = log(w_i,k / w_i,b)

理由：S 是一份**有限名单**，不是从更大总体抽出来的样本。要描述的就是这 N 个人
自己的二阶矩，那是一个总体矩，分母是 N。`np.cov` 默认 ddof=1，差一个
`N/(N−1)`——N=1000 时只有千分之一，但它会让"注入矩"与"按同一名单算出来的矩"
对不上，而那种对不上最难查。

**有限名单的矩已知，不等于它的跨名单不确定性消失。** 换一份名单 `C_w,S` 就变；
bootstrap 每个 replicate 必须按**自己那份名单**重算分母，不能冻结原名单的。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from worlds.personas import dirichlet_covariance, dirichlet_mean

#: 估计对象。产出里必须带上它，否则读的人不知道分母是谁。
Estimand = Literal["P_dirichlet_superpopulation", "Q_quota_population", "S_realised_list"]

#: 本仓库统一的经验矩自由度。**改它会让所有矩比不可比。**
DDOF = 0


@dataclass(frozen=True)
class WeightMoments:
    """一组七维权重矩，带它描述的是谁。"""

    estimand: Estimand
    source: str
    n: int | None
    mean: np.ndarray
    covariance: np.ndarray
    ddof: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimand": self.estimand,
            "source": self.source,
            "n": self.n,
            "ddof": self.ddof,
            "mean": [float(v) for v in self.mean],
            "variance_diagonal": [float(v) for v in np.diag(self.covariance)],
            "covariance": [[float(v) for v in row] for row in self.covariance],
        }


def empirical_weight_moments(
    weights: np.ndarray, *, estimand: Estimand = "S_realised_list", source: str = "injected_truth"
) -> WeightMoments:
    """`m_S` 与 `C_w,S`，**ddof = 0**。"""
    w = np.asarray(weights, dtype=float)
    if w.ndim != 2:
        raise ValueError(f"需要 (n, 7) 的权重矩阵，得到 {w.shape}")
    mean = w.mean(axis=0)
    centred = w - mean
    cov = centred.T @ centred / w.shape[0]
    return WeightMoments(
        estimand=estimand, source=source, n=w.shape[0], mean=mean, covariance=cov, ddof=DDOF
    )


def alr_coordinates(weights: np.ndarray, base: int) -> np.ndarray:
    """`a_i,k = log(w_i,k / w_i,b)`，去掉基准维那一列。"""
    w = np.asarray(weights, dtype=float)
    if np.any(w <= 0.0):
        raise ValueError("ALR 只对严格为正的权重有定义")
    ln = np.log(w)
    keep = [k for k in range(w.shape[1]) if k != base]
    return ln[:, keep] - ln[:, base : base + 1]


@dataclass(frozen=True)
class AlrMoments:
    """ALR 坐标下的注入端矩。`t = 0` 的端点用它，**不用 P 的闭式解**。"""

    estimand: Estimand
    base: int
    n: int
    mean: np.ndarray
    covariance: np.ndarray
    ddof: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimand": self.estimand,
            "base_dim": self.base,
            "n": self.n,
            "ddof": self.ddof,
            "mean": [float(v) for v in self.mean],
            "covariance": [[float(v) for v in row] for row in self.covariance],
        }


def empirical_alr_moments(
    weights: np.ndarray, *, base: int, estimand: Estimand = "S_realised_list"
) -> AlrMoments:
    """`C_a,S`：同一份名单、同一基准、**同一 ddof = 0**。"""
    a = alr_coordinates(weights, base)
    mean = a.mean(axis=0)
    centred = a - mean
    return AlrMoments(
        estimand=estimand,
        base=base,
        n=a.shape[0],
        mean=mean,
        covariance=centred.T @ centred / a.shape[0],
        ddof=DDOF,
    )


def theoretical_weight_moments(alpha: list[float] | None = None) -> WeightMoments:
    """P 的闭式矩。**数学 fixture 与人口口径的还原目标，不是 S 的分母。**"""
    return WeightMoments(
        estimand="P_dirichlet_superpopulation",
        source="dirichlet_closed_form",
        n=None,
        mean=dirichlet_mean(alpha),
        covariance=dirichlet_covariance(alpha),
        ddof=None,
    )


def theoretical_alr_moments(alpha: list[float] | None = None, *, base: int = 0) -> AlrMoments:
    """P 在 ALR 坐标下的闭式矩。

        μ_a,k  = ψ(α_k) − ψ(α_b)
        C_a,kl = 1{k=l}·ψ₁(α_k) + ψ₁(α_b)

    material 基准、α=(1.5, 0.5×6) 时：`μ_a,k = −2`，对角 `π²−4`，非对角 `π²/2−4`。

    **同样是 P 的，不是 S 的。** 过采样之后 S 的 ALR 矩会偏离它，那是设计的
    后果不是错误。
    """
    from scipy.special import digamma, polygamma

    from worlds.personas import dirichlet_alpha_vector

    a = np.asarray(alpha if alpha is not None else dirichlet_alpha_vector(), dtype=float)
    keep = [k for k in range(len(a)) if k != base]
    mean = digamma(a[keep]) - digamma(a[base])
    cov = np.full((len(keep), len(keep)), polygamma(1, a[base]), dtype=float)
    cov[np.diag_indices_from(cov)] += polygamma(1, a[keep])
    return AlrMoments(
        estimand="P_dirichlet_superpopulation",
        base=base,
        n=0,
        mean=mean,
        covariance=cov,
        ddof=None,
    )


@dataclass(frozen=True)
class MomentComparison:
    """拟合矩对注入矩。**逐维方差比 + 整体 Frobenius，不逐元素相除。**

    逐元素相除会在接近零的协方差元素上炸出没有意义的大数，而 `Σ1 = 0` 保证
    七维里必有接近零的元素。
    """

    denominator_estimand: Estimand
    variance_ratio: np.ndarray
    covariance_frobenius: float
    covariance_frobenius_relative: float
    mean_shift: np.ndarray

    def as_dict(self) -> dict[str, Any]:
        return {
            "denominator_estimand": self.denominator_estimand,
            "variance_ratio_fitted_over_injected": [float(v) for v in self.variance_ratio],
            "covariance_frobenius": self.covariance_frobenius,
            "covariance_frobenius_relative": self.covariance_frobenius_relative,
            "mean_shift_fitted_minus_injected": [float(v) for v in self.mean_shift],
            "mean_shift_note": (
                "单独报告，不与协方差比合并；正式区间由联合 bootstrap 传播，**不称纯 LLM 效应**"
            ),
            "not_elementwise_ratio": "Σ1=0 保证有接近零的元素，逐元素相除无意义",
        }


def compare_to_injected(fitted: WeightMoments, injected: WeightMoments) -> MomentComparison:
    """拟合分布的权重空间矩 vs 注入端的实现矩。

    **分子不得换成个体后验均值的样本协方差。** 那是另一个量（个体后验已经被
    收缩过），拿它当分子会把收缩算进"总体异质性"里，两头都失真。
    """
    diff = fitted.covariance - injected.covariance
    frob = float(np.linalg.norm(diff, "fro"))
    denom = float(np.linalg.norm(injected.covariance, "fro"))
    return MomentComparison(
        denominator_estimand=injected.estimand,
        variance_ratio=np.diag(fitted.covariance) / np.diag(injected.covariance),
        covariance_frobenius=frob,
        covariance_frobenius_relative=frob / denom if denom > 0 else float("nan"),
        mean_shift=fitted.mean - injected.mean,
    )
