"""问卷粒度扰动：噪声地板的来源（§9.3）。门一用它。

判据是"L_rep 的中位数是否超过噪声地板"，而噪声地板是"按每维 ±5 分扰动权重
所诱导的 EV 变动幅度的中位数"。±5 这个数来自定和量表的粒度，所以判据有
依据、不是拍的。

--------------------------------------------------------------------------
三条约束，以及它们为什么互相拉扯
--------------------------------------------------------------------------

    幅度   |δ_k| ≤ 5 分            来自问卷粒度
    守恒   Σδ_k = 0                定和量表是一百分的分配，总分不能漂
    可行   w_k·100 + δ_k ≥ 0       负权重不是一份可能的答卷

前两条一起就定义了一个多面体（方体 ∩ 超平面），它在 δ → −δ 下对称，所以
其上的均匀分布**均值恰好为零**。加上第三条之后事情变了，而怎么加决定了
扰动有没有方向性推力。

--------------------------------------------------------------------------
三个方案，实测（1000 人设，α₁=1.5）
--------------------------------------------------------------------------

    方案甲  对称 ±5，抽完之后拒绝掉 w+δ<0 的
    方案乙  按人设整体缩放 δ_max(i) = min(5, min_k points)
    方案丙  逐维对称截断 cap_k = min(5, points_k)

| | 接受率 | 幅度 | E[ΔH_norm] | E[δ] 最大分量 |
|---|---|---|---|---|
| 纯 Jensen 参照 | — | — | −0.0127 | 0 |
| 甲 | 中位 26.5% | 满幅 | **+0.0167** (z=+80) | 0.86 |
| 乙 | 100% | 中位 0.23 分 | −0.0015 | ~0 |
| 丙 | Gibbs，近似独立 | 中位 3.71 分 | −0.0102 | 噪声级 |

**H_norm 对 w 是凹的，所以零均值扰动按 Jensen 应当降低期望熵**——纯参照
（那些非负约束从不生效的人设）确实是 −0.0127。

**方案甲把符号翻过来了。** 拒绝是在一个非对称事件上做条件：小维度不能往
下走，于是条件分布在小维度上均值为正，净效果是系统性推向均匀，也就是
**推向中心**。而"推向中心"正是这个项目要测的那个东西——用它当噪声地板，
等于把被测对象掺进了标尺。所以"拒绝重抽保零均值"这句话不成立。

**方案乙保住了零均值，但代价太大**：δ_max 的中位数只有 0.23 分，六成以上
人设的扰动幅度不到半分，地板被压扁，门一变成白送。

**方案丙同时拿到两样**：逐维截断之后多面体仍然在 δ → −δ 下对称，所以均匀
分布的均值**由构造恰好为零**，不靠拒绝；而非负性也由构造保证（cap_k ≤
points_k），不需要事后过滤。幅度保住了（每维平均 3.71 分）。

代价是幅度随维度变：分数少的维度扰动小。那是实质上对的——给休闲打了 0.3
分的人，本来就不可能"其实想给 −4.7"。边界附近的不确定性天然是单侧的，
方案丙把它表达成"幅度更小"而不是"方向有偏"。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CAP_POINTS = 5.0
N_DIMENSIONS = 7


@dataclass(frozen=True)
class PerturbationDiagnostics:
    """扰动有没有方向性推力。**每次跑门一都要报，不是可选诊断。**

    ``mean_delta_h_norm`` 是 E[H_norm(w+δ) − H_norm(w)]。理论预期是**负的**
    （H_norm 凹，Jensen）；若它显著为正，说明扰动在系统性地推向均匀，
    也就是推向中心——而那正是被测对象，标尺不能带着它。
    """

    scheme: str
    n_personas: int
    draws_per_persona: int
    mean_delta_h_norm: float
    se_delta_h_norm: float
    max_abs_mean_delta_points: float
    median_acceptance: float | None = None

    @property
    def z(self) -> float:
        return self.mean_delta_h_norm / self.se_delta_h_norm

    def __str__(self) -> str:
        acc = "—" if self.median_acceptance is None else f"{self.median_acceptance:.1%}"
        return (
            f"{self.scheme}: E[ΔH_norm] = {self.mean_delta_h_norm:+.5f} "
            f"± {self.se_delta_h_norm:.5f} (z={self.z:+.1f}), "
            f"|E[δ]|max = {self.max_abs_mean_delta_points:.3f} 分, 接受率 {acc}"
        )


def caps_for(points: np.ndarray, cap: float = CAP_POINTS) -> np.ndarray:
    """逐维对称截断的上界：cap_k = min(cap, 该维当前分数)。

    取 min 保证 `w_k·100 + δ_k ≥ 0`，而**两侧同时截断**保证多面体在
    δ → −δ 下仍然对称，因此均匀分布的均值恰好为零。只截下侧就破坏了对称，
    那就退化成方案甲。
    """
    return np.minimum(cap, points)


def sample_truncated(
    points: np.ndarray,
    n: int,
    rng: np.random.Generator,
    *,
    sweeps: int = 30,
) -> np.ndarray:
    """方案丙。均匀于 {|δ_k| ≤ cap_k, Σδ = 0}，用坐标对 Gibbs。

    从 δ=0 出发（永远可行），反复挑一对 (j,k)，令 δ_j += t、δ_k −= t，
    t 在盒约束允许的整段上均匀抽。每一步都保持总和为零与盒约束，且沿该
    直线是均匀条件抽样，所以平稳分布正是目标多面体上的均匀分布。

    为什么不用拒绝法：cap 异质时，从均匀单纯形上拒绝的接受率中位数只有
    1.2%，五成以上人设低于 2%。那是采样器差，不是目标分布不好——Gibbs
    命中率百分之百，实测滞后一自相关 +0.036，样本近似独立。

    ``sweeps`` 是两个保留样本之间的成对移动次数，越大越接近独立。30 是
    实测下自相关已经落进噪声的值。
    """
    caps = caps_for(points)
    k = len(points)
    delta = np.zeros(k)
    out = np.empty((n, k))
    for i in range(n):
        for _ in range(sweeps):
            a, b = rng.choice(k, 2, replace=False)
            lo = max(-caps[a] - delta[a], delta[b] - caps[b])
            hi = min(caps[a] - delta[a], delta[b] + caps[b])
            if hi > lo:
                t = rng.uniform(lo, hi)
                delta[a] += t
                delta[b] -= t
        out[i] = delta
    return out


def sample_symmetric_rejected(
    points: np.ndarray,
    n: int,
    rng: np.random.Generator,
    *,
    max_batches: int = 200,
) -> tuple[np.ndarray, float]:
    """方案甲。对称 ±5 抽样，再拒绝掉会让权重为负的。

    **保留它不是因为推荐用，是因为门一的判据是预注册的**：换方案要能一眼
    看出换的是哪一个，而不是去 diff 一个被删掉的实现。它的方向性推力有
    实测（E[ΔH] = +0.0167, z=+80），见模块文档。

    返回 (样本, 接受率)。
    """
    k = len(points)
    cap = CAP_POINTS
    kept: list[np.ndarray] = []
    drawn = accepted = 0
    for _ in range(max_batches):
        u = rng.dirichlet(np.ones(k), max(n * 4, 400)) * (k * cap)
        box = u[(u <= 2 * cap).all(axis=1)] - cap
        drawn += len(box)
        ok = box[(points + box >= 0).all(axis=1)]
        accepted += len(ok)
        kept.extend(ok)
        if len(kept) >= n:
            break
    rate = accepted / drawn if drawn else 0.0
    return np.array(kept[:n]) if kept else np.empty((0, k)), rate


SAMPLERS = {
    "per_dimension_symmetric_truncation": sample_truncated,
    "symmetric_with_rejection": sample_symmetric_rejected,
}


def diagnose(
    weight_points: np.ndarray,
    scheme: str,
    *,
    draws_per_persona: int = 64,
    seed: int = 0,
) -> PerturbationDiagnostics:
    """跑一遍方向性推力诊断。产出进 runs/<run_id>/。

    ``weight_points`` 是 (人设数, 7) 的分数矩阵（和为一百，不是权重）。
    """
    from common.weights import normalised_entropy

    rng = np.random.default_rng(seed)
    sampler = SAMPLERS[scheme]

    deltas_h: list[float] = []
    mean_deltas: list[np.ndarray] = []
    acceptances: list[float] = []

    for points in weight_points:
        if scheme == "symmetric_with_rejection":
            draws, rate = sampler(points, draws_per_persona, rng)
            acceptances.append(rate)
        else:
            draws = sampler(points, draws_per_persona, rng)
        if len(draws) == 0:
            continue
        base = normalised_entropy(points)
        deltas_h.extend(normalised_entropy(points + d) - base for d in draws)
        mean_deltas.append(draws.mean(axis=0))

    values = np.asarray(deltas_h)
    return PerturbationDiagnostics(
        scheme=scheme,
        n_personas=len(mean_deltas),
        draws_per_persona=draws_per_persona,
        mean_delta_h_norm=float(values.mean()),
        se_delta_h_norm=float(values.std() / np.sqrt(len(values))),
        max_abs_mean_delta_points=float(np.abs(np.asarray(mean_deltas).mean(axis=0)).max()),
        median_acceptance=float(np.median(acceptances)) if acceptances else None,
    )
