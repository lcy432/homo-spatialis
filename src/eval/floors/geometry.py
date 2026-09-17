"""P8 产出二：`proj`、共享中心与修订后的坍缩比例（`DECISIONS/0002`，硬规则 23、24）。

--------------------------------------------------------------------------
三个臂共用同一个中心，这不是便利条件
--------------------------------------------------------------------------

    d_i    = ‖w_i − ŵ_center‖
    u_i    = (ŵ_center − w_i)/d_i
    proj_i = ⟨ŵ_i − w_i, u_i⟩

`ŵ_center`、`d`、`u` **只算一次**，用匹配（或指定的参考）臂的中心，三个臂原样
共用。偏误因子 `b = 1 − Cov(⟨δ,u⟩, d)/Var(d)` 只依赖 δ、`u`、`d`；三者不随臂
变化时它才是三臂共用的乘性因子，才能在

    r = (ŝ_matched − λ̂) / (ŝ_shuffled − λ̂)

里约掉。各臂各估各的中心就消不掉，`r` 与旧公式一样有偏。

**旧式 `(ŝ − λ̂)/(1 − λ̂)` 已作废**（`DECISIONS/0002`），且不得要求
`ŝ_shuffled == 1`——有 `ŵ_center` 的估计误差时它本来就等于 `b`，不等于 1。

打乱臂是**方向指标的天花板**，不是协方差比的天花板；打乱只破坏指定的配对，
不重排属性列或城市标签。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

#: `d_i` 近零的判定门槛。中心附近的人给出 0/0，必须**标记并计数**，
#: 不能用一个随手的 epsilon 制造斜率，也不能悄悄删掉。
D_NEAR_ZERO = 1e-8

ArmSource = Literal["synthetic_truth_softmax", "language_model", "synthetic_shuffled"]


@dataclass(frozen=True)
class SharedGeometry:
    """一次性构造、三臂共用的几何量。"""

    centre: np.ndarray  # (7,) ŵ_center
    d: np.ndarray  # (n_personas,)
    u: np.ndarray  # (n_personas, 7)
    near_zero: np.ndarray  # (n_personas,) bool，d 近零
    centre_source: str

    @property
    def n_near_zero(self) -> int:
        return int(self.near_zero.sum())

    def as_dict(self) -> dict[str, Any]:
        return {
            "centre": [float(v) for v in self.centre],
            "centre_source": self.centre_source,
            "n_personas": len(self.d),
            "n_d_near_zero": self.n_near_zero,
            "d_near_zero_threshold": D_NEAR_ZERO,
            "d_quantiles": [float(v) for v in np.quantile(self.d, [0.0, 0.25, 0.5, 0.75, 1.0])],
        }


def shared_geometry(
    true_weights: np.ndarray, centre: np.ndarray, *, centre_source: str
) -> SharedGeometry:
    """从**一个**中心构造 d 与 u。三个臂之后都传同一个返回值。"""
    w = np.asarray(true_weights, dtype=float)
    c = np.asarray(centre, dtype=float)
    diff = c[None, :] - w
    d = np.linalg.norm(diff, axis=1)
    near = d <= D_NEAR_ZERO
    safe = np.where(near, 1.0, d)  # 只为避免除零；这些行会被标记并从回归中排除
    return SharedGeometry(
        centre=c, d=d, u=diff / safe[:, None], near_zero=near, centre_source=centre_source
    )


def projections(
    estimated: np.ndarray, true_weights: np.ndarray, geom: SharedGeometry
) -> np.ndarray:
    """proj_i = ⟨ŵ_i − w_i, u_i⟩。近零行照算，但回归时按协议排除。"""
    return np.einsum("ik,ik->i", np.asarray(estimated) - np.asarray(true_weights), geom.u)


@dataclass(frozen=True)
class ArmSlope:
    arm: str
    source: ArmSource
    slope: float
    intercept: float
    n_used: int
    n_excluded_near_zero: int
    with_intercept: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "source": self.source,
            "slope": self.slope,
            "intercept": self.intercept,
            "n_used": self.n_used,
            "n_excluded_near_zero": self.n_excluded_near_zero,
            "with_intercept": self.with_intercept,
        }


def arm_slope(
    proj: np.ndarray,
    geom: SharedGeometry,
    *,
    arm: str,
    source: ArmSource,
    with_intercept: bool = True,
) -> ArmSlope:
    """`proj` 对 `d` 的回归斜率。

    ``with_intercept`` 是**统一规则**，三个臂必须一致；混用会让 `b` 不再是共用
    的乘性因子。默认带截距：`proj` 在 `d=0` 处理论上为零，但估计误差会让样本
    截距非零，强制过原点等于把那部分挤进斜率。
    """
    mask = ~geom.near_zero
    x, y = geom.d[mask], np.asarray(proj)[mask]
    if len(x) < 2:
        return ArmSlope(
            arm, source, float("nan"), float("nan"), len(x), geom.n_near_zero, with_intercept
        )
    design = np.column_stack([x, np.ones_like(x)]) if with_intercept else x[:, None]
    coef, *_ = np.linalg.lstsq(design, y, rcond=None)
    slope = float(coef[0])
    intercept = float(coef[1]) if with_intercept else 0.0
    return ArmSlope(arm, source, slope, intercept, len(x), geom.n_near_zero, with_intercept)


@dataclass(frozen=True)
class CollapseRatio:
    """`r = (ŝ_matched − λ̂)/(ŝ_shuffled − λ̂)`。分母非正时返回状态，不给数。"""

    value: float | None
    status: str  # "ok" | "denominator_non_positive" | "window_too_narrow" | "missing_input"
    numerator: float
    denominator: float
    s_matched: float
    s_shuffled: float
    lam: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "r": self.value,
            "status": self.status,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "s_matched": self.s_matched,
            "s_shuffled": self.s_shuffled,
            "lambda": self.lam,
        }


def collapse_ratio(
    s_matched: float, s_shuffled: float, lam: float, *, min_window: float = 0.0
) -> CollapseRatio:
    """现行公式。**不夹到 [0,1]**——`r > 1` 是一个结果，不是越界（§1.2.2）。"""
    num = float(s_matched) - float(lam)
    den = float(s_shuffled) - float(lam)
    if not np.isfinite(num) or not np.isfinite(den):
        status, value = "missing_input", None
    elif den <= 0.0:
        status, value = "denominator_non_positive", None
    elif den <= min_window:
        status, value = "window_too_narrow", None
    else:
        status, value = "ok", num / den
    return CollapseRatio(value, status, num, den, float(s_matched), float(s_shuffled), float(lam))
