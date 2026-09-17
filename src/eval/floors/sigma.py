"""P8 产出四：重复子集标定 σ，以及 λ(σ) 敏感性（`DECISIONS/0003`，硬规则 19、25）。

--------------------------------------------------------------------------
方向不能写反
--------------------------------------------------------------------------

可观测量是**分歧率**（重复对里选出不同城市的比例），不是"一致性"。分歧率对 σ
单调递增。有人把它实现成"一致的比例"再去匹配，方向就反了——代码跑得完全正常，
只是 λ 系统性偏到另一边，门二的判定跟着错。所以本模块一律写 disagreement。

同一设计下 softmax 的理论一致率是 `Σ_c p_c(σ)²`，因此

    理论分歧率(σ) = 平均over(人设, 情境) [ 1 − Σ_c p_c(σ)² ]

一维数值求解即可。上界是 `1 − 1/n` 而不是 1：n 座城市完全随机选择的分歧率就是
`1 − 1/n`（12 座时 0.9167）。实测超过它 **无解**，那是装置有问题的信号
（重复对里混进了不同条件），不要外推。

--------------------------------------------------------------------------
两条不能省的限定
--------------------------------------------------------------------------

1. **必须跑在重复子集自己那批 (世界, 人设) 交叉点上。** 分歧率依赖选择集——
   备选越接近，同样的 σ 给出越高的分歧率。换一批世界标定，偏多少取决于两批
   世界的福利离散度差多少，而那是没人会去查的量。
2. **这是校准启发式，不是恒等式。** 重复子集测的是同人设同世界跨次运行的分歧，
   logit 的 σ 测的是备选项间的特异误差，两者相关但不同一。不要因为"看起来
   应该相等"就改成解析换算。

**绝不从匹配臂的 σ̂ = 1/γ̂_0 倒查地板**（硬规则 19）：那把地板变成处理变量的
函数，而且 σ̂ 里混了设定误差。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

SigmaStatus = Literal[
    "calibrated", "pending_input", "target_above_random_ceiling", "no_solution_in_bounds"
]


@dataclass(frozen=True)
class RepeatObservation:
    """重复子集里的一次运行。**同一人设同一世界跑多次**，不是 12 个不同选择集。"""

    persona_id: str
    scenario_id: str
    repetition: int
    chosen: str


@dataclass(frozen=True)
class DisagreementMeasurement:
    rate: float
    n_pairs: int
    n_crossings: int
    n_alternatives: int
    random_ceiling: float
    source: str  # "language_model" | "synthetic_validation"

    def as_dict(self) -> dict[str, Any]:
        return {
            "disagreement_rate": self.rate,
            "n_pairs": self.n_pairs,
            "n_crossings": self.n_crossings,
            "n_alternatives": self.n_alternatives,
            "random_ceiling": self.random_ceiling,
            "source": self.source,
        }


def measured_disagreement(
    repeats: Sequence[RepeatObservation], *, n_alternatives: int, source: str
) -> DisagreementMeasurement:
    """池化全部无序重复对的分歧率。

    每个 (人设, 情境) 交叉点上 m 次重复给出 C(m,2) 个无序对；全体池化，
    与 `DECISIONS/0003` 的口径一致（150 人设 × 3 次 → 每人每情境 3 个对）。
    """
    by_crossing: dict[tuple[str, str], list[str]] = {}
    for r in repeats:
        by_crossing.setdefault((r.persona_id, r.scenario_id), []).append(r.chosen)

    pairs = differ = 0
    for choices in by_crossing.values():
        for i in range(len(choices)):
            for j in range(i + 1, len(choices)):
                pairs += 1
                differ += int(choices[i] != choices[j])
    if pairs == 0:
        raise ValueError("重复子集里一个重复对都没有；检查 repetition 是否真的多于一次")
    return DisagreementMeasurement(
        rate=differ / pairs,
        n_pairs=pairs,
        n_crossings=len(by_crossing),
        n_alternatives=n_alternatives,
        random_ceiling=1.0 - 1.0 / n_alternatives,
        source=source,
    )


def theoretical_disagreement(ln_utilities: np.ndarray, sigma: float) -> float:
    """同一设计下 softmax 的理论分歧率 `mean[1 − Σ_c p_c(σ)²]`。

    ``ln_utilities`` 形状 (n_crossings, n_alternatives)，来自 M2 的 `ln V`——
    **单一真值来源**，不另写一套通勤/税/物价换算。`ln κ` 跨城不变，在 softmax
    里约掉，所以用 `ln V` 与用 `Σβx` 等价。
    """
    if sigma <= 0:
        raise ValueError("σ 必须为正")
    v = np.asarray(ln_utilities, dtype=float) / sigma
    v = v - v.max(axis=1, keepdims=True)
    p = np.exp(v)
    p /= p.sum(axis=1, keepdims=True)
    return float(np.mean(1.0 - np.sum(p * p, axis=1)))


@dataclass(frozen=True)
class SigmaCalibration:
    status: SigmaStatus
    sigma: float | None
    target_disagreement: float | None
    achieved_disagreement: float | None
    bounds: tuple[float, float]
    n_crossings: int
    utility_template: str
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "sigma": self.sigma,
            "target_disagreement": self.target_disagreement,
            "achieved_disagreement": self.achieved_disagreement,
            "bounds": list(self.bounds),
            "n_crossings": self.n_crossings,
            "utility_template": self.utility_template,
            "note": self.note,
        }


def calibrate_sigma(
    ln_utilities: np.ndarray,
    measurement: DisagreementMeasurement | None,
    *,
    bounds: tuple[float, float] = (1e-4, 5.0),
    tol: float = 1e-6,
    max_iter: int = 200,
    utility_template: str = "m2_ln_indirect_utility_true_params",
) -> SigmaCalibration:
    """由分歧率一维求解 σ。分歧率对 σ 单调递增，二分即可。

    ``measurement is None`` → ``pending_input``：**没有实测重复数据时，真实校准
    状态就是待输入**，不得默认成某个数然后宣布门通过。
    """
    n_cross = int(np.asarray(ln_utilities).shape[0])
    if measurement is None:
        return SigmaCalibration(
            status="pending_input",
            sigma=None,
            target_disagreement=None,
            achieved_disagreement=None,
            bounds=bounds,
            n_crossings=n_cross,
            utility_template=utility_template,
            note="未提供实测重复子集；正式校准待输入，不可用默认 σ 代替",
        )

    target = measurement.rate
    if target > measurement.random_ceiling:
        return SigmaCalibration(
            status="target_above_random_ceiling",
            sigma=None,
            target_disagreement=target,
            achieved_disagreement=None,
            bounds=bounds,
            n_crossings=n_cross,
            utility_template=utility_template,
            note=(
                f"实测分歧率 {target:.4f} 超过完全随机的上界 "
                f"{measurement.random_ceiling:.4f}；无解。这是重复对里混进了不同条件"
                "（世界不同、prompt 不同、备选顺序没固定住）的信号，不要外推 σ"
            ),
        )

    lo, hi = bounds
    f_lo = theoretical_disagreement(ln_utilities, lo) - target
    f_hi = theoretical_disagreement(ln_utilities, hi) - target
    if f_lo > 0 or f_hi < 0:
        return SigmaCalibration(
            status="no_solution_in_bounds",
            sigma=None,
            target_disagreement=target,
            achieved_disagreement=None,
            bounds=bounds,
            n_crossings=n_cross,
            utility_template=utility_template,
            note=f"区间端点的理论分歧率为 {f_lo + target:.4f} 与 {f_hi + target:.4f}，未跨过目标",
        )

    for _ in range(max_iter):
        mid = 0.5 * (lo + hi)
        if theoretical_disagreement(ln_utilities, mid) - target > 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < tol:
            break
    sigma = 0.5 * (lo + hi)
    return SigmaCalibration(
        status="calibrated",
        sigma=sigma,
        target_disagreement=target,
        achieved_disagreement=theoretical_disagreement(ln_utilities, sigma),
        bounds=bounds,
        n_crossings=n_cross,
        utility_template=utility_template,
        note=(
            "校准启发式：重复子集测跨次分歧，logit 的 σ 测备选项间特异误差，"
            "两者相关但不同一。一致率也受效用间隔影响，重复选择无法唯一分离所有系统误差"
        ),
    )


def simulate_repeats(
    ln_utilities_by_crossing: Mapping[tuple[str, str], np.ndarray],
    city_codes: Sequence[Sequence[str]],
    *,
    sigma: float,
    n_repeats: int,
    seed: int,
) -> list[RepeatObservation]:
    """**合成验证用**：给定 σ 造一批重复选择，用来验证校准能把它找回来。

    产出的 ``source`` 标签由调用方设成 ``synthetic_validation``——绝不能把它
    写成真实大模型的重复子集。
    """
    rng = np.random.default_rng(seed)
    out: list[RepeatObservation] = []
    for (pid, sid), lnv in ln_utilities_by_crossing.items():
        codes = city_codes[0] if len(city_codes) == 1 else None
        v = np.asarray(lnv, dtype=float) / sigma
        v -= v.max()
        p = np.exp(v)
        p /= p.sum()
        names = codes if codes is not None else [f"c{i}" for i in range(len(p))]
        for k in range(n_repeats):
            out.append(RepeatObservation(pid, sid, k, str(names[int(rng.choice(len(p), p=p))])))
    return out
