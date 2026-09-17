"""P8 产出一：真值 softmax 校准与偏差表（PLAN P8 产出 1、6；README §10.3）。

目标不是"确认估计器无偏"，是**量化偏差**——README §10.3 把这一点写在标题上。
所以这里报的是一张表，不是一个通过/不通过。

三个必须分开的量（混起来是本模块最容易犯的错）：

``injected_theoretical``
    Dirichlet 的闭式矩。`DECISIONS/0006` 之后 α = (1.5, 0.5×6)，均值是
    `(1/3, 1/9×6)`，**不是** `1/7`。

``injected_realised``
    这 N 个人设的有限样本实现矩。它与理论矩差一个抽样误差，两者都要保存。

``fitted_population``
    拟合分布在**权重空间**的矩（`RecoveryResult.sigma_w_hat`）。它既不是
    个体后验均值的样本协方差，也不是潜空间的 Σ_a。

偏差表不做逐元素除：`Σ̂ / Σ_true` 在 Σ_true 有零元素时没有定义，而单纯形
协方差的行和恒为零、必然出现接近零的元素。只报对角线比值 + 整体误差。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from eval.recovery import ResearchProfile, fit_research
from eval.recovery.profiles import RESEARCH_PROFILE_ALR_FULL_MATERIAL, ResearchFit
from worlds.personas import dirichlet_covariance, dirichlet_mean

from .design import FloorArm, dimension_names


@dataclass(frozen=True)
class MomentSet:
    """一组七维矩。来源标签必须带着走，不能事后猜。"""

    source: str
    mean: np.ndarray
    covariance: np.ndarray

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "mean": [float(v) for v in self.mean],
            "variance_diagonal": [float(v) for v in np.diag(self.covariance)],
        }


@dataclass(frozen=True)
class BiasTable:
    """一个校准案例的完整偏差表。"""

    case: str
    sigma_generating: float
    sigma_hat: float
    dimensions: tuple[str, ...]

    injected_theoretical: MomentSet
    injected_realised: MomentSet
    fitted_population: MomentSet

    centre_bias: np.ndarray  # 拟合中心 − 实现均值
    variance_ratio: np.ndarray  # diag(Σ̂_w) / diag(Σ_realised)
    covariance_frobenius: float
    covariance_frobenius_relative: float

    individual_bias: np.ndarray  # mean(ŵ_i − w_i)
    individual_rmse: np.ndarray
    individual_corr: np.ndarray
    constant_baseline_rmse: np.ndarray  # 对每个人都预测实现均值
    skill_vs_constant: np.ndarray

    converged: bool
    n_failed_starts: int
    loglik: float
    loglik_spread_over_starts: float
    chosen_sd0: float
    seconds: float
    design_digest: str
    profile: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        def lst(a):
            return [float(v) for v in a]

        return {
            "case": self.case,
            "sigma_generating": self.sigma_generating,
            "sigma_hat": self.sigma_hat,
            "dimensions": list(self.dimensions),
            "injected_theoretical": self.injected_theoretical.as_dict(),
            "injected_realised": self.injected_realised.as_dict(),
            "fitted_population": self.fitted_population.as_dict(),
            "centre_bias": lst(self.centre_bias),
            "variance_ratio_fitted_over_realised": lst(self.variance_ratio),
            "covariance_frobenius": self.covariance_frobenius,
            "covariance_frobenius_relative": self.covariance_frobenius_relative,
            "individual_bias": lst(self.individual_bias),
            "individual_rmse": lst(self.individual_rmse),
            "individual_corr": lst(self.individual_corr),
            "constant_baseline_rmse": lst(self.constant_baseline_rmse),
            "skill_vs_constant": lst(self.skill_vs_constant),
            "converged": self.converged,
            "n_failed_starts": self.n_failed_starts,
            "loglik": self.loglik,
            "loglik_spread_over_starts": self.loglik_spread_over_starts,
            "chosen_sd0": self.chosen_sd0,
            "seconds": self.seconds,
            "design_digest": self.design_digest,
            "profile": self.profile,
        }


def calibration_alphas(scales: tuple[float, ...] = (0.5, 1.0, 2.0)) -> dict[str, np.ndarray]:
    """高/低方差校准案例：α 整体乘 c。

    Dirichlet 的均值只依赖 α 的**比例**，所以缩放 α 保持理论均值不变，只改
    离散度——正是"覆盖注入方差大与小两端"要的那个旋钮（PLAN P8 产出 1）。
    **只用于校准案例，绝不覆盖主实验先验。**
    """
    from worlds.personas import dirichlet_alpha_vector

    base = np.array(dirichlet_alpha_vector(), dtype=float)
    names = {0.5: "wide_variance", 1.0: "baseline", 2.0: "narrow_variance"}
    return {names.get(c, f"alpha_scale_{c}"): base * c for c in scales}


def bias_table(
    arm: FloorArm,
    fit: ResearchFit,
    *,
    case: str,
    injected_alpha: np.ndarray | None,
    seconds: float,
) -> BiasTable:
    result = fit.result
    truth = arm.injected_weights
    w_hat = result.w_hat

    alpha = injected_alpha
    theoretical = MomentSet(
        source="dirichlet_closed_form",
        mean=dirichlet_mean(list(alpha)) if alpha is not None else dirichlet_mean(),
        covariance=dirichlet_covariance(list(alpha))
        if alpha is not None
        else dirichlet_covariance(),
    )
    realised = MomentSet(
        source="finite_sample_injected",
        mean=truth.mean(axis=0),
        covariance=np.cov(truth, rowvar=False),
    )
    fitted = MomentSet(
        source="fitted_population_weight_space",
        mean=result.w_center_hat,
        covariance=result.sigma_w_hat,
    )

    realised_var = np.diag(realised.covariance)
    baseline = np.sqrt(((realised.mean[None, :] - truth) ** 2).mean(axis=0))
    rmse = np.sqrt(((w_hat - truth) ** 2).mean(axis=0))
    frob = float(np.linalg.norm(fitted.covariance - realised.covariance, "fro"))

    return BiasTable(
        case=case,
        sigma_generating=arm.sigma_generating,
        sigma_hat=result.sigma_hat,
        dimensions=dimension_names(),
        injected_theoretical=theoretical,
        injected_realised=realised,
        fitted_population=fitted,
        centre_bias=fitted.mean - realised.mean,
        variance_ratio=np.diag(fitted.covariance) / realised_var,
        covariance_frobenius=frob,
        covariance_frobenius_relative=frob / float(np.linalg.norm(realised.covariance, "fro")),
        individual_bias=(w_hat - truth).mean(axis=0),
        individual_rmse=rmse,
        individual_corr=np.array(
            [float(np.corrcoef(w_hat[:, k], truth[:, k])[0, 1]) for k in range(truth.shape[1])]
        ),
        constant_baseline_rmse=baseline,
        skill_vs_constant=1.0 - rmse / baseline,
        converged=result.diagnostics.converged,
        n_failed_starts=fit.n_failed_starts,
        loglik=result.diagnostics.loglik,
        loglik_spread_over_starts=fit.loglik_spread,
        chosen_sd0=fit.chosen_sd0,
        seconds=seconds,
        design_digest=arm.design.digest(),
        profile=fit.profile.fingerprint(),
    )


def run_case(
    arm: FloorArm,
    *,
    case: str,
    injected_alpha: np.ndarray | None,
    profile: ResearchProfile = RESEARCH_PROFILE_ALR_FULL_MATERIAL,
) -> BiasTable:
    """跑一个校准案例：**生成噪声 σ 是输入，拟合的 σ̂ 是输出**，两者不互相覆盖。"""
    import time

    t0 = time.time()
    fit = fit_research(arm.dataset, profile)
    return bias_table(arm, fit, case=case, injected_alpha=injected_alpha, seconds=time.time() - t0)
