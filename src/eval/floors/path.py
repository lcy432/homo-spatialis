"""P8 产出三：`Σ_w(t)` 路径与 `λ(t)`（README §1.2.2；PLAN P8 产出 3）。

--------------------------------------------------------------------------
这条路径的可执行协议（本仓库此前没有，`DECISIONS/0013` 第六节补上）
--------------------------------------------------------------------------

§1.2.2 只写了 `Σ(t) = (1−t)·Σ_injected + t·Σ̂_matched`，以及"地板臂**拟合于**
这个 Σ"。落地需要三件它没写的东西：

1. **Σ 是七维权重空间矩，不能直接塞给六维 ALR 的 Σ_a。** 必须做数值矩标定：
   求 `(μ_a, Σ_a)` 使 `w(a)` 的权重空间矩命中目标。潜空间线性插值**不是**同一
   条路径，不能这么替代。
2. **哪些参数固定、哪些重估。** 本协议下 `(μ_a, Σ_a)` 被目标矩**钉死**，
   `γ_0` 取给定值（地板臂的校准 σ）；个体后验在这组固定参数上算，
   **不重新拟合总体**——否则指定的 Σ(t) 会被重估覆盖。
3. **均值目标**：沿路径固定为注入分布的均值。只改离散度，不改中心。

--------------------------------------------------------------------------
必须如实报告的两件事
--------------------------------------------------------------------------

**可行性不能只看 PSD。** 单纯形上的分布还要满足 `Var(w_k) ≤ μ_k(1−μ_k)`
（`w_k ∈ [0,1]` 的伯努利上界）与 `Σ·1 = 0`。PSD 通过不等于目标可实现。

**残差必须报。** logistic-normal 未必能精确表示任意 (均值, Σ_w)——Dirichlet 就
不在这个族里。所以每个 t 都报目标与实际达到的均值/协方差，端点与内部点都验；
残差超过声明容差时该点标 ``residual_above_tolerance``，**不得宣称路径已实现**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import minimize

from eval.recovery.alr import alr_from_weights, weights_from_alr

#: 声明的矩标定容差（相对 Frobenius / 相对均值）。超过即标记，不放宽。
MOMENT_RTOL = 5e-3


@dataclass(frozen=True)
class MomentFeasibility:
    feasible: bool
    psd: bool
    row_sums_zero: bool
    variance_within_bernoulli_bound: bool
    mean_in_open_simplex: bool
    worst_variance_slack: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "psd": self.psd,
            "row_sums_zero": self.row_sums_zero,
            "variance_within_bernoulli_bound": self.variance_within_bernoulli_bound,
            "mean_in_open_simplex": self.mean_in_open_simplex,
            "worst_variance_slack": self.worst_variance_slack,
        }


def simplex_moment_feasibility(
    mean: np.ndarray, cov: np.ndarray, *, atol: float = 1e-9
) -> MomentFeasibility:
    """单纯形上 (均值, 协方差) 的**必要**条件。通过不等于可实现，但不通过一定不可实现。"""
    m = np.asarray(mean, dtype=float)
    c = np.asarray(cov, dtype=float)
    eig = np.linalg.eigvalsh(0.5 * (c + c.T))
    scale = max(1.0, float(np.abs(eig).max()))
    psd = bool(eig.min() >= -atol * scale)
    rows = bool(np.abs(c @ np.ones(len(m))).max() <= 1e-8 * scale)
    bound = m * (1.0 - m)
    slack = float((bound - np.diag(c)).min())
    within = bool(slack >= -atol)
    inside = bool(np.all(m > 0.0) and np.all(m < 1.0) and abs(m.sum() - 1.0) < 1e-9)
    return MomentFeasibility(
        feasible=psd and rows and within and inside,
        psd=psd,
        row_sums_zero=rows,
        variance_within_bernoulli_bound=within,
        mean_in_open_simplex=inside,
        worst_variance_slack=slack,
    )


def covariance_path(
    sigma_injected: np.ndarray, sigma_matched: np.ndarray, t_grid: np.ndarray
) -> list[np.ndarray]:
    """`Σ_w(t) = (1−t)Σ_injected + t·Σ_matched`。凸组合，行和为零自动保持。"""
    a = np.asarray(sigma_injected, dtype=float)
    b = np.asarray(sigma_matched, dtype=float)
    return [(1.0 - float(t)) * a + float(t) * b for t in np.asarray(t_grid, dtype=float)]


@dataclass(frozen=True)
class MomentCalibration:
    """把一组权重空间目标矩标定成 logistic-normal 参数的结果。"""

    t: float
    status: str  # "ok" | "residual_above_tolerance" | "target_infeasible"
    mu_a: np.ndarray | None
    chol: np.ndarray | None
    target_mean: np.ndarray
    target_cov: np.ndarray
    achieved_mean: np.ndarray | None
    achieved_cov: np.ndarray | None
    mean_rel_error: float
    cov_rel_frobenius: float
    feasibility: MomentFeasibility

    def as_dict(self) -> dict[str, Any]:
        return {
            "t": self.t,
            "status": self.status,
            "target_mean": [float(v) for v in self.target_mean],
            "target_variance_diagonal": [float(v) for v in np.diag(self.target_cov)],
            "achieved_mean": None
            if self.achieved_mean is None
            else [float(v) for v in self.achieved_mean],
            "achieved_variance_diagonal": None
            if self.achieved_cov is None
            else [float(v) for v in np.diag(self.achieved_cov)],
            "mean_rel_error": self.mean_rel_error,
            "cov_rel_frobenius": self.cov_rel_frobenius,
            "tolerance": MOMENT_RTOL,
            "feasibility": self.feasibility.as_dict(),
        }


def _moments_from_params(
    mu: np.ndarray, chol: np.ndarray, base: int, xi: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    w = weights_from_alr(mu + xi @ chol.T, base)
    return w.mean(axis=0), np.cov(w, rowvar=False, bias=False)


def calibrate_logistic_normal(
    target_mean: np.ndarray,
    target_cov: np.ndarray,
    *,
    base: int,
    t: float,
    n_draws: int = 20_000,
    seed: int = 4_099,
    maxiter: int = 400,
    start: tuple[np.ndarray, np.ndarray] | None = None,
) -> MomentCalibration:
    """数值矩标定：求 `(μ_a, L)` 使 `w(a)` 的权重空间矩命中目标。

    用**公共随机数**（固定的 ξ）让目标函数光滑，否则每次评估换一批点，优化器
    在追一个抖动的目标。残差如实报告——这不是"对数矩匹配"，目标就是权重空间的矩。
    """
    m = np.asarray(target_mean, dtype=float)
    c = np.asarray(target_cov, dtype=float)
    feas = simplex_moment_feasibility(m, c)
    if not feas.feasible:
        return MomentCalibration(
            t=t,
            status="target_infeasible",
            mu_a=None,
            chol=None,
            target_mean=m,
            target_cov=c,
            achieved_mean=None,
            achieved_cov=None,
            mean_rel_error=float("nan"),
            cov_rel_frobenius=float("nan"),
            feasibility=feas,
        )

    dim = len(m) - 1
    rng = np.random.default_rng(seed)
    xi = rng.standard_normal((n_draws, dim))
    tril = np.tril_indices(dim)

    if start is not None:
        mu0, chol0 = start
    else:
        mu0 = alr_from_weights(m, base)
        chol0 = np.diag(np.full(dim, 0.5))

    cov_norm = float(np.linalg.norm(c, "fro")) or 1.0

    def unpack(theta):
        mu = theta[:dim]
        chol = np.zeros((dim, dim))
        chol[tril] = theta[dim:]
        return mu, chol

    def objective(theta):
        mu, chol = unpack(theta)
        am, ac = _moments_from_params(mu, chol, base, xi)
        return float(
            np.sum((am - m) ** 2) / max(np.sum(m**2), 1e-12) + np.sum((ac - c) ** 2) / cov_norm**2
        )

    theta0 = np.concatenate([mu0, chol0[tril]])
    res = minimize(objective, theta0, method="L-BFGS-B", options={"maxiter": maxiter})
    mu, chol = unpack(res.x)
    am, ac = _moments_from_params(mu, chol, base, xi)
    mean_err = float(np.linalg.norm(am - m) / np.linalg.norm(m))
    cov_err = float(np.linalg.norm(ac - c, "fro") / cov_norm)
    status = (
        "ok" if (mean_err <= MOMENT_RTOL and cov_err <= MOMENT_RTOL) else "residual_above_tolerance"
    )
    return MomentCalibration(
        t=t,
        status=status,
        mu_a=mu,
        chol=chol,
        target_mean=m,
        target_cov=c,
        achieved_mean=am,
        achieved_cov=ac,
        mean_rel_error=mean_err,
        cov_rel_frobenius=cov_err,
        feasibility=feas,
    )
