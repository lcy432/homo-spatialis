"""P9 的入口：拟合 → 反解 → 诊断（README §11.7.1.1 至 §11.7.1.3）。

```python
from eval.recovery import RecoveryConfig, fit_recovery

result = fit_recovery(dataset, RecoveryConfig(base_dim="material", n_draws=400))
result.w_center_hat        # 七维总体中心
result.w_hat               # (n_personas, 7) 个体回收权重
result.sigma_w_hat         # 七维协方差，秩 6，行和为零
result.diagnostics.as_dict()
```

换基准维就是换模型（§11.7.1.3），所以稳健性不是"再跑一次看看"，是必须报的
一项：``fit_base_dimension_robustness`` 一次跑一组基准维，差异原样留在结果里，
**不做任何让它们看齐的事后调整**。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from worlds.dimensions import ALL_DIMENSIONS, MATERIAL

from .design import (
    base_index,
    condition_number,
    design_matrix,
    weakly_identified_dimensions,
    within_task_variation,
)
from .inversion import (
    check_gamma_0,
    sigma_w_from_sigma_gamma,
    weights_from_gamma,
)
from .msl import CovarianceMode, DrawMethod, MslFit, fit_mixed_logit
from .results import (
    CovarianceReport,
    RecoveryDiagnostics,
    RecoveryResult,
    SimplexReport,
)
from .schema import ChoiceDataset

#: 建议的基准维稳健性组合：物质维与两个舒适度维。§11.7.1.3 要求至少两到三个，
#: 且包含物质维与一个舒适度维——二者的方差量级差别最大。
DEFAULT_BASE_DIMS: tuple[str, ...] = (MATERIAL, "safety", "environment")


@dataclass(frozen=True)
class RecoveryConfig:
    """一次回收估计的全部旋钮。没有隐式全局状态：基准维、特征序、抽样点、
    协方差模式全部从这里显式流下去。"""

    base_dim: str = MATERIAL
    covariance: CovarianceMode = "diagonal"
    n_draws: int = 200
    seed: int = 0
    draw_method: DrawMethod = "halton"
    maxiter: int = 500
    tol: float = 1e-7
    draw_batch: int = 32

    #: γ_0 低于此值时给一条结构化警告（不是抛错；抛错的下限在
    #: ``inversion.GAMMA0_FLOOR``）。γ_0 = 1/σ，而 ln V 的跨城差异通常在
    #: 0.05–0.3，σ 到 1 就意味着选择已经接近随机——那是门二要拦的情形。
    gamma0_warn_below: float = 1.0

    #: 组内标准差低于中位数这个倍数的维度，标记为弱识别。默认十分之一：
    #: 差一个数量级时，该维的权重实际上不是从数据里识别出来的。
    weak_identification_ratio: float = 0.1

    #: 总体越界比例用多少个模拟点估。只影响诊断精度，不影响估计。
    simplex_draws: int = 20_000


def _population_simplex(fit: MslFit, base: int, gamma_0: float, n: int, seed: int) -> SimplexReport:
    """从 N(μ_γ, Σ_γ) 抽点，反解成七维权重，统计落在单纯形外的比例。"""
    rng = np.random.default_rng(seed + 7919)
    xi = rng.standard_normal((n, fit.mu_gamma.size))
    gamma = fit.mu_gamma + xi @ fit.chol.T
    return SimplexReport.of(weights_from_gamma(gamma_0, gamma, base))


def fit_recovery(dataset: ChoiceDataset, config: RecoveryConfig | None = None) -> RecoveryResult:
    """拟合面板混合 logit 并反解回七维权重空间。"""
    cfg = config or RecoveryConfig()
    base = base_index(cfg.base_dim)

    fit = fit_mixed_logit(
        dataset,
        base,
        n_draws=cfg.n_draws,
        seed=cfg.seed,
        covariance=cfg.covariance,
        draw_method=cfg.draw_method,
        maxiter=cfg.maxiter,
        tol=cfg.tol,
        draw_batch=cfg.draw_batch,
    )

    gamma_0 = check_gamma_0(fit.gamma_0)
    warning = None
    if gamma_0 < cfg.gamma0_warn_below:
        warning = (
            f"γ_0 = {gamma_0:.4g} 低于 {cfg.gamma0_warn_below:g}（σ̂ = {1.0 / gamma_0:.4g}）。"
            "比值反解 ŵ = γ_k/γ_0 在此处方差很大；这与门二 2a/2b 是同一个情形，"
            "结论应当带上这条警告，不要只看点估计。"
        )

    w_center = weights_from_gamma(gamma_0, fit.mu_gamma, base)
    sigma_w = sigma_w_from_sigma_gamma(fit.sigma_gamma, gamma_0, base)
    w_hat = weights_from_gamma(gamma_0, fit.posterior_gamma, base)

    design = design_matrix(dataset.features, base)
    cond = condition_number(design, within_task=dataset.row_task)
    within_sd = within_task_variation(dataset.features, dataset.row_task)
    tasks = dataset.tasks_per_persona

    diagnostics = RecoveryDiagnostics(
        converged=fit.converged,
        message=fit.message,
        n_iterations=fit.n_iterations,
        n_function_evals=fit.n_function_evals,
        loglik=fit.loglik,
        loglik_conditional_logit=fit.mnl_loglik,
        gamma_0=gamma_0,
        sigma_hat=1.0 / gamma_0,
        gamma_0_warning=warning,
        design_condition_number=cond,
        attribute_within_sd=tuple(float(v) for v in within_sd),
        weakly_identified_dimensions=weakly_identified_dimensions(
            within_sd, ratio=cfg.weak_identification_ratio
        ),
        covariance=CovarianceReport.of(sigma_w),
        population_simplex=_population_simplex(fit, base, gamma_0, cfg.simplex_draws, cfg.seed),
        individual_simplex=SimplexReport.of(w_hat),
        posterior_ess_median=float(np.median(fit.posterior_ess)),
        posterior_sd_median=float(np.median(fit.posterior_sd_gamma)),
        n_personas=dataset.n_personas,
        n_tasks=dataset.n_tasks,
        n_rows=dataset.n_rows,
        tasks_per_persona_min=int(tasks.min()),
        tasks_per_persona_median=float(np.median(tasks)),
        tasks_per_persona_max=int(tasks.max()),
    )

    return RecoveryResult(
        base_dim=ALL_DIMENSIONS[base],
        covariance=cfg.covariance,
        n_draws=cfg.n_draws,
        seed=cfg.seed,
        gamma_0=gamma_0,
        sigma_hat=1.0 / gamma_0,
        mu_gamma=fit.mu_gamma,
        sigma_gamma=fit.sigma_gamma,
        w_center_hat=w_center,
        sigma_w_hat=sigma_w,
        persona_ids=dataset.persona_order,
        posterior_gamma=fit.posterior_gamma,
        posterior_sd_gamma=fit.posterior_sd_gamma,
        w_hat=w_hat,
        diagnostics=diagnostics,
        fit=fit,
    )


def fit_base_dimension_robustness(
    dataset: ChoiceDataset,
    config: RecoveryConfig | None = None,
    base_dims: Sequence[str] = DEFAULT_BASE_DIMS,
) -> dict[str, RecoveryResult]:
    """同一批数据、同一套 draws，换基准维各跑一次。

    **对角 Σ_γ 不是基准维不变的假设**，所以结果本来就可能不同。这个函数的
    职责是把差异摆出来（§11.7.1.3：报结论是否翻盘），不是把它抹平。
    """
    cfg = config or RecoveryConfig()
    return {b: fit_recovery(dataset, replace(cfg, base_dim=b)) for b in base_dims}


def base_dimension_spread(results: dict[str, RecoveryResult]) -> dict[str, float]:
    """基准维敏感性的摘要：中心与逐维方差在不同基准维之间差多少。"""
    centers = np.array([r.w_center_hat for r in results.values()])
    diag = np.array([np.diag(r.sigma_w_hat) for r in results.values()])
    sigmas = np.array([r.sigma_hat for r in results.values()])
    return {
        "max_abs_center_range": float((centers.max(axis=0) - centers.min(axis=0)).max()),
        "max_abs_variance_range": float((diag.max(axis=0) - diag.min(axis=0)).max()),
        "sigma_hat_min": float(sigmas.min()),
        "sigma_hat_max": float(sigmas.max()),
    }
