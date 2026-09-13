"""P9 的结果对象与诊断（README §10.3 的条件数/秩检查、§11.7.1.2 的派生量）。

一个拟合出来的东西必须能原样喂给 block bootstrap 再跑一遍，所以这里只放
数据，不放行为：``RecoveryResult`` 是 P8、P10 与 M5 报告层共同的接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from worlds.dimensions import ALL_DIMENSIONS

from .inversion import is_positive_semidefinite, numerical_rank, simplex_violation


@dataclass(frozen=True)
class SimplexReport:
    """越界统计。**报告，不修正**（用户指令第八节）。"""

    fraction_outside: float
    fraction_negative_entries: float
    fraction_above_one_entries: float
    max_below_zero: float
    max_above_one: float
    max_magnitude: float

    @staticmethod
    def of(weights: np.ndarray) -> SimplexReport:
        return SimplexReport(**simplex_violation(weights))

    def as_dict(self) -> dict[str, float]:
        return {
            "fraction_outside": self.fraction_outside,
            "fraction_negative_entries": self.fraction_negative_entries,
            "fraction_above_one_entries": self.fraction_above_one_entries,
            "max_below_zero": self.max_below_zero,
            "max_above_one": self.max_above_one,
            "max_magnitude": self.max_magnitude,
        }


@dataclass(frozen=True)
class CovarianceReport:
    """Σ_w 的合法性。三条都是构造上应当成立的，实测它们是在测浮点，不是测模型。"""

    rank: int
    positive_semidefinite: bool
    max_abs_row_sum: float  # ‖Σ·1‖∞
    quadratic_form: float  # 1ᵀΣ1
    min_eigenvalue: float

    @staticmethod
    def of(sigma_w: np.ndarray) -> CovarianceReport:
        s = np.asarray(sigma_w, dtype=float)
        ones = np.ones(s.shape[0])
        eig = np.linalg.eigvalsh(0.5 * (s + s.T))
        return CovarianceReport(
            rank=numerical_rank(s),
            positive_semidefinite=is_positive_semidefinite(s),
            max_abs_row_sum=float(np.abs(s @ ones).max()),
            quadratic_form=float(ones @ s @ ones),
            min_eigenvalue=float(eig.min()),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "positive_semidefinite": self.positive_semidefinite,
            "max_abs_row_sum": self.max_abs_row_sum,
            "quadratic_form": self.quadratic_form,
            "min_eigenvalue": self.min_eigenvalue,
        }


@dataclass(frozen=True)
class RecoveryDiagnostics:
    converged: bool
    message: str
    n_iterations: int
    n_function_evals: int
    loglik: float
    loglik_conditional_logit: float
    gamma_0: float
    sigma_hat: float
    gamma_0_warning: str | None
    design_condition_number: float
    attribute_within_sd: tuple[float, ...]
    weakly_identified_dimensions: tuple[str, ...]
    covariance: CovarianceReport
    population_simplex: SimplexReport
    individual_simplex: SimplexReport
    posterior_ess_median: float
    posterior_sd_median: float
    n_personas: int
    n_tasks: int
    n_rows: int
    tasks_per_persona_min: int
    tasks_per_persona_median: float
    tasks_per_persona_max: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "converged": self.converged,
            "message": self.message,
            "n_iterations": self.n_iterations,
            "n_function_evals": self.n_function_evals,
            "loglik": self.loglik,
            "loglik_conditional_logit": self.loglik_conditional_logit,
            "gamma_0": self.gamma_0,
            "sigma_hat": self.sigma_hat,
            "gamma_0_warning": self.gamma_0_warning,
            "design_condition_number": self.design_condition_number,
            "attribute_within_sd": list(self.attribute_within_sd),
            "weakly_identified_dimensions": list(self.weakly_identified_dimensions),
            "covariance": self.covariance.as_dict(),
            "population_simplex": self.population_simplex.as_dict(),
            "individual_simplex": self.individual_simplex.as_dict(),
            "posterior_ess_median": self.posterior_ess_median,
            "posterior_sd_median": self.posterior_sd_median,
            "n_personas": self.n_personas,
            "n_tasks": self.n_tasks,
            "n_rows": self.n_rows,
            "tasks_per_persona": [
                self.tasks_per_persona_min,
                self.tasks_per_persona_median,
                self.tasks_per_persona_max,
            ],
        }


@dataclass(frozen=True)
class RecoveryResult:
    """一次回收估计的全部产出。

    **`sigma_hat` 是结果与诊断，不是地板臂的工作点。** 地板臂的 σ 由重复子集
    按分歧率标定（硬规则 19、25，`DECISIONS/0003`）。这两个概念在本对象里
    刻意没有任何连线：没有方法把它交给 P8，P8 也不该从这里读。
    """

    base_dim: str
    covariance: str
    n_draws: int
    seed: int

    gamma_0: float
    sigma_hat: float
    mu_gamma: np.ndarray  # (6,)
    sigma_gamma: np.ndarray  # (6,6)

    w_center_hat: np.ndarray  # (7,)
    sigma_w_hat: np.ndarray  # (7,7)

    persona_ids: tuple[str, ...]
    posterior_gamma: np.ndarray  # (n_personas, 6)
    posterior_sd_gamma: np.ndarray  # (n_personas, 6)
    w_hat: np.ndarray  # (n_personas, 7)

    diagnostics: RecoveryDiagnostics
    dimensions: tuple[str, ...] = ALL_DIMENSIONS
    fit: Any = field(default=None, repr=False)

    #: ``"difference_normal"``（默认，原有分支）或 ``"alr_logistic_normal"``。
    #:
    #: **``mu_gamma`` / ``sigma_gamma`` 是"所用参数化的潜空间矩"**：默认分支下
    #: 它们就是 γ 空间的 μ 与 Σ（语义一字未改）；ALR 分支下它们是 a 空间的
    #: μ_a 与 Σ_a。字段名为兼容保留，``mu_latent`` / ``sigma_latent`` 是不带
    #: 分支暗示的别名。``w_center_hat`` / ``sigma_w_hat`` / ``w_hat`` 在两个
    #: 分支下含义完全相同，下游按它们取值即可。
    parameterization: str = "difference_normal"

    @property
    def n_personas(self) -> int:
        return len(self.persona_ids)

    @property
    def mu_latent(self) -> np.ndarray:
        """潜空间均值：默认分支是 μ_γ，ALR 分支是 μ_a。"""
        return self.mu_gamma

    @property
    def sigma_latent(self) -> np.ndarray:
        """潜空间协方差：默认分支是 Σ_γ，ALR 分支是 Σ_a。"""
        return self.sigma_gamma

    def weights_frame(self) -> dict[str, np.ndarray]:
        """``维度名 → (n_personas,) 的回收权重``，方便下游按名取列。"""
        return {d: self.w_hat[:, i] for i, d in enumerate(self.dimensions)}

    def summary(self) -> dict[str, Any]:
        """JSON 可写的摘要，供 ``runs/<run_id>/`` 落盘。个体层面的数组不进摘要。"""
        return {
            "base_dim": self.base_dim,
            "parameterization": self.parameterization,
            "covariance": self.covariance,
            "n_draws": self.n_draws,
            "seed": self.seed,
            "gamma_0": self.gamma_0,
            "sigma_hat": self.sigma_hat,
            "dimensions": list(self.dimensions),
            "w_center_hat": [float(v) for v in self.w_center_hat],
            "sigma_w_diagonal": [float(v) for v in np.diag(self.sigma_w_hat)],
            "mu_gamma": [float(v) for v in self.mu_gamma],
            "sigma_gamma_diagonal": [float(v) for v in np.diag(self.sigma_gamma)],
            "diagnostics": self.diagnostics.as_dict(),
        }
