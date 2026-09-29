"""门二与门三（`DECISIONS/0002` 第二节；README §10.3.3、§10.3.4）。

门二三条判据，全部预注册：

| | 判据 | 守什么 |
|---|---|---|
| 2a | `λ̂/ŝ_shuffled ≤ 0.8` | 原判据，去衰减后 |
| 2b | `(ŝ_shuffled − λ̂)/bootstrap SE ≥ 阈值` | `r` 的方差；新分母是估计量，趋零时 `r` 爆 |
| 2c | `ŝ_shuffled ≥ 0.5` | `d` 本身测得有多糟；2b 守 `r`，2c 守 `r(d)` 的分箱 |

**2b 的阈值本仓库尚无预注册值。** 它是必填配置；缺失时 2b 与整体判定返回
``pending_input``，**不得**事后看着结果补一个刚好能过的数。

门三检验 λ 是否是 `d` 的函数。**功效不足不能当作"没有依赖"**，而且这个检验
的功效恰好在最需要它的时候最低：λ 真的随 `d` 上升时，`d` 大的人收缩更强、
个体估计更不稳，回归右端方差更大，效应的存在本身使它更难被检出。所以默认
走 `r(d)` 分箱，单一斜率只作稳健性。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

#: `pending_input` = 缺真实数据；`pending_computation` = 数据在、算力没花够。
#: 两者必须分开：前者要去取数，后者要去跑。合成 PASS 与真实判定也永远分开。
GateStatus = Literal["pass", "fail", "inconclusive", "pending_input", "pending_computation"]


@dataclass(frozen=True)
class Criterion:
    name: str
    status: GateStatus
    value: float | None
    threshold: float | None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "value": self.value,
            "threshold": self.threshold,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class GateTwoResult:
    status: GateStatus
    criteria: tuple[Criterion, ...]
    lam: float | None
    s_shuffled: float | None
    window: float | None
    window_se: float | None
    n_bootstrap: int
    arm_sources: dict[str, str]
    #: SE(W) 是怎么来的。**必须是同一 replicate 内 (ŝ_shuffled − λ̂) 的变化**，
    #: 不能是 Var(ŝ)+Var(λ̂)（那假设了独立，实测可差 2.5 倍）。
    se_basis: str = "per_replicate_window"
    coverage: str = "pointwise_at_one_t"

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": "two_measurement_window",
            "status": self.status,
            "criteria": [c.as_dict() for c in self.criteria],
            "lambda": self.lam,
            "s_shuffled": self.s_shuffled,
            "window": self.window,
            "window_bootstrap_se": self.window_se,
            "n_bootstrap": self.n_bootstrap,
            "arm_sources": self.arm_sources,
            "se_basis": self.se_basis,
            "coverage": self.coverage,
            "thresholds_are_design_rules": (
                "0.8 / 1.96 / 0.5 都是预注册的设计规则，不是定理。1.96 来自正态近似"
                "下双侧 95% 的直觉，**不保证 80% power**，也不保证小样本、重尾或"
                "多个 t/层上的联合覆盖率"
            ),
        }


def _worst(statuses: list[GateStatus]) -> GateStatus:
    for bad in ("pending_input", "pending_computation", "inconclusive", "fail"):
        if bad in statuses:
            return bad  # type: ignore[return-value]
    return "pass"


def gate_two(
    *,
    lam: float | None,
    s_shuffled: float | None,
    window_se: float | None,
    n_bootstrap: int,
    threshold_2a: float,
    threshold_2b: float | None,
    threshold_2c: float,
    min_bootstrap: int,
    arm_sources: dict[str, str],
    se_basis: str = "per_replicate_window",
    coverage: str = "pointwise_at_one_t",
) -> GateTwoResult:
    """三条判据一起判。分母非正、窗口趋零、输入不足各自返回明确原因。"""
    criteria: list[Criterion] = []

    if lam is None or s_shuffled is None:
        for name, thr in (("2a", threshold_2a), ("2b", threshold_2b), ("2c", threshold_2c)):
            criteria.append(
                Criterion(
                    name, "pending_input", None, thr, "缺 λ̂ 或 ŝ_shuffled（需要地板臂与打乱臂）"
                )
            )
        return GateTwoResult(
            "pending_input",
            tuple(criteria),
            lam,
            s_shuffled,
            None,
            None,
            n_bootstrap,
            arm_sources,
            se_basis,
            coverage,
        )

    window = float(s_shuffled) - float(lam)

    # 2a：去衰减后的 λ。分母非正时不输出看似正常的比值。
    if s_shuffled <= 0:
        criteria.append(
            Criterion(
                "2a",
                "inconclusive",
                None,
                threshold_2a,
                f"ŝ_shuffled = {s_shuffled:.4g} 非正，去衰减的比值无定义",
            )
        )
    else:
        ratio = float(lam) / float(s_shuffled)
        criteria.append(
            Criterion("2a", "pass" if ratio <= threshold_2a else "fail", ratio, threshold_2a)
        )

    # 2b：窗口与其 bootstrap 标准误之比。阈值缺失 → pending_input。
    if threshold_2b is None:
        criteria.append(
            Criterion(
                "2b", "pending_input", None, None, "仓库尚无预注册的 2b 阈值；正式判定缺少预注册值"
            )
        )
    elif n_bootstrap < min_bootstrap:
        criteria.append(
            Criterion(
                "2b",
                "pending_computation",
                None,
                threshold_2b,
                f"有效 replicate 只有 {n_bootstrap}，低于正式判定所需的 {min_bootstrap}。"
                "这是算力没花够，不是缺数据——工程 smoke 的小 B 不得充当正式 SE",
            )
        )
    elif window_se is None or not np.isfinite(window_se) or window_se <= 0:
        criteria.append(
            Criterion(
                "2b", "inconclusive", None, threshold_2b, "窗口的 bootstrap 标准误不可用或非正"
            )
        )
    elif window <= 0:
        criteria.append(
            Criterion(
                "2b",
                "fail",
                window / window_se,
                threshold_2b,
                f"窗口 (ŝ_shuffled − λ̂) = {window:.4g} 非正，r 无定义",
            )
        )
    else:
        z = window / window_se
        criteria.append(Criterion("2b", "pass" if z >= threshold_2b else "fail", z, threshold_2b))

    # 2c：d 本身测得有多糟
    criteria.append(
        Criterion(
            "2c", "pass" if s_shuffled >= threshold_2c else "fail", float(s_shuffled), threshold_2c
        )
    )

    return GateTwoResult(
        status=_worst([c.status for c in criteria]),
        criteria=tuple(criteria),
        lam=float(lam),
        s_shuffled=float(s_shuffled),
        window=window,
        window_se=window_se,
        n_bootstrap=n_bootstrap,
        arm_sources=arm_sources,
        se_basis=se_basis,
        coverage=coverage,
    )


@dataclass(frozen=True)
class GateThreeResult:
    status: GateStatus
    slope: float | None
    ci: tuple[float, float] | None
    n_used: int
    n_strata: int
    minimum_detectable_effect: float | None
    mde_assumptions: str
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": "three_lambda_depends_on_d",
            "status": self.status,
            "slope": self.slope,
            "ci": None if self.ci is None else list(self.ci),
            "n_used": self.n_used,
            "n_strata": self.n_strata,
            "minimum_detectable_effect": self.minimum_detectable_effect,
            "mde_assumptions": self.mde_assumptions,
            "reason": self.reason,
            "default_metric": "r(d,t) binned; single slope only as robustness",
        }


def gate_three(
    inconsistency: np.ndarray | None,
    d: np.ndarray | None,
    *,
    strata: np.ndarray | None = None,
    confidence: float = 0.95,
) -> GateThreeResult:
    """人内不一致度对 `d` 的回归。

    MDE 由**实际残差方差**与 `d` 的离散度算出（`mde = (z_{1−α/2}+z_power)·se`），
    不是"人数够了就宣布 power=0.8"。响应量与噪声来源写进 ``mde_assumptions``。
    """
    if inconsistency is None or d is None:
        return GateThreeResult(
            "pending_input",
            None,
            None,
            0,
            0,
            None,
            "未提供重复子集的人内不一致度；MDE 无法计算",
            reason="缺实测重复子集",
        )
    y = np.asarray(inconsistency, dtype=float)
    x = np.asarray(d, dtype=float)
    ok = np.isfinite(y) & np.isfinite(x)
    y, x = y[ok], x[ok]
    if len(x) < 10 or float(np.std(x)) <= 0:
        return GateThreeResult(
            "inconclusive",
            None,
            None,
            len(x),
            0,
            None,
            "样本不足或 d 无变异，回归无定义",
            reason="输入不足",
        )

    design = np.column_stack([x, np.ones_like(x)])
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    resid = y - design @ coef
    dof = max(1, len(x) - 2)
    s2 = float(resid @ resid) / dof
    se = float(np.sqrt(s2 / (len(x) * np.var(x))))
    from scipy.stats import norm

    z = float(norm.ppf(0.5 + confidence / 2.0))
    lo, hi = coef[0] - z * se, coef[0] + z * se
    mde = (z + float(norm.ppf(0.8))) * se
    significant = lo > 0 or hi < 0
    return GateThreeResult(
        status="pass" if significant else "inconclusive",
        slope=float(coef[0]),
        ci=(float(lo), float(hi)),
        n_used=len(x),
        n_strata=0 if strata is None else len(np.unique(strata)),
        minimum_detectable_effect=float(mde),
        mde_assumptions=(
            "响应量=人内不一致度（重复对分歧率）；噪声来源=回归残差方差的实测值；"
            f"MDE = (z_{{1−α/2}}+z_{{0.8}})·SE，SE 由残差方差与 d 的离散度算出，n={len(x)}。"
            "不显著**不等于**没有依赖：λ 真随 d 上升时右端方差更大，效应的存在本身"
            "使它更难被检出，所以默认仍走 r(d) 分箱"
        ),
        reason="" if significant else "区间跨零；按预注册两级默认，主结果仍用 r(d) 分箱",
    )
