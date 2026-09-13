"""ALR / logistic-normal 分支的模拟似然（P9 的第二个参数化）。

**与差分分支共用**：``_Layout`` 的面板组织、Halton draws、``_panel_terms`` 的
组内 log-sum-exp 与按人设聚合、``_posterior_weights`` 的归一、优化器、
``fit_conditional_logit`` 起点、bootstrap、结果 schema、收敛诊断。

**真正不同的只有两处**，也只写这两处：

1. 怎么从参数算出系统效用 ``v``；
2. ``∂v/∂参数``。

差分分支（``msl.py``）一行未动其数学，逐位可复现——有 golden 回归测试盯着。

--------------------------------------------------------------------------
模型
--------------------------------------------------------------------------

    a_i = μ_a + L·ξ_i                        ξ ~ N(0, I₆)，基准维坐标恒为 0
    w_i = softmax(a_{i,1}, …, 0_b, …, a_{i,6})
    γ_0 = exp(ρ) = 1/σ
    η_itc = γ_0 · Σ_{k=1..7} w_{i,k}·x_itck      ← 原始形式，不做差分

梯度（softmax Jacobian ``∂w_k/∂a_l = w_k(1[k=l] − w_l)``）：

    ∂η/∂ρ   = η
    ∂η/∂a_l = γ_0 · w_l · (x_l − x̄) ，  x̄ = Σ_j w_j x_j = η/γ_0
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import minimize

from .alr import (
    chol_param_gradient,
    pack_alr,
    population_moments,
    unpack_alr,
    weights_from_alr,
)
from .design import N_DIMS, N_RANDOM, other_indices
from .msl import (
    CovarianceMode,
    DrawMethod,
    _Layout,
    _panel_terms,
    _posterior_weights,
    fit_conditional_logit,
    layout,
    standard_normal_draws,
)
from .schema import ChoiceDataset


@dataclass
class _AlrWorkspace:
    ell: np.ndarray  # (n_personas, R)
    grho: np.ndarray  # (n_personas, R)
    ga: np.ndarray  # (n_personas, R, 6)


def _simulate_alr(
    theta: np.ndarray,
    lay: _Layout,
    xi: np.ndarray,
    base: int,
    covariance: CovarianceMode,
    batch: int,
) -> tuple[_AlrWorkspace, float, np.ndarray, np.ndarray]:
    rho, mu_a, chol = unpack_alr(theta, covariance)
    gamma_0 = float(np.exp(rho))
    n_draws = xi.shape[1]
    ws = _AlrWorkspace(
        ell=np.empty((lay.n_personas, n_draws)),
        grho=np.empty((lay.n_personas, n_draws)),
        ga=np.empty((lay.n_personas, n_draws, N_RANDOM)),
    )
    free = list(other_indices(base))

    for lo in range(0, n_draws, batch):
        hi = min(lo + batch, n_draws)
        a = mu_a + xi[:, lo:hi, :] @ chol.T
        w = weights_from_alr(a, base)  # (n_personas, b, 7)

        # x̄ = Σ_k w_k x_k。逐维取，避免物化 (n_rows, batch, 7)。
        xbar = np.zeros((lay.n_rows, hi - lo), dtype=float)
        for k in range(N_DIMS):
            xbar += lay.x[:, k : k + 1] * w[lay.row_persona, :, k]
        v = gamma_0 * xbar

        ell_block, resid = _panel_terms(v, lay)
        ws.ell[:, lo:hi] = ell_block
        ws.grho[:, lo:hi] = np.add.reduceat(resid * v, lay.persona_starts, axis=0)
        for idx, dim in enumerate(free):
            contrib = (
                resid * (gamma_0 * w[lay.row_persona, :, dim]) * (lay.x[:, dim : dim + 1] - xbar)
            )
            ws.ga[:, lo:hi, idx] = np.add.reduceat(contrib, lay.persona_starts, axis=0)

    return ws, gamma_0, mu_a, chol


def alr_objective(
    theta: np.ndarray,
    lay: _Layout,
    xi: np.ndarray,
    base: int,
    covariance: CovarianceMode,
    batch: int,
) -> tuple[float, np.ndarray]:
    """负对数模拟似然与解析梯度。公开，以便有限差分核验直接调它。"""
    ws, _, _, chol = _simulate_alr(theta, lay, xi, base, covariance, batch)
    q, loglik = _posterior_weights(ws.ell)
    d_rho = float(np.sum(q * ws.grho))
    d_mu = np.einsum("ir,irk->k", q, ws.ga)
    d_chol = np.einsum("ir,irm,irn->mn", q, ws.ga, xi)
    grad = np.concatenate([[d_rho], d_mu, chol_param_gradient(d_chol, chol, covariance)])
    return -loglik, -grad


@dataclass(frozen=True)
class AlrPosterior:
    """个体条件后验。**权重空间取 E[w(a)|y]，不是 w(E[a|y])。**

    softmax 是非线性的，两者不相等；§11.7 的 `proj_i` 要的是前者——问的是
    "这个人的权重后验均值"，不是"后验均值坐标对应的那个权重"。后者由 Jensen
    不等式系统性偏向单纯形的某一侧，而那个偏移会被读成坍缩。
    """

    weight_mean: np.ndarray  # (n_personas, 7) = Σ_r q_r · w(a^{(r)})
    latent_mean: np.ndarray  # (n_personas, 6) = Σ_r q_r · a^{(r)}（附带保存）
    latent_sd: np.ndarray  # (n_personas, 6)
    ess: np.ndarray  # (n_personas,)
    weights: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))


def _alr_posterior_from_weights(
    q: np.ndarray, xi: np.ndarray, mu_a: np.ndarray, chol: np.ndarray, base: int
) -> AlrPosterior:
    a = mu_a + xi @ chol.T  # (n_personas, R, 6)
    w = weights_from_alr(a, base)  # (n_personas, R, 7)
    weight_mean = np.einsum("ir,irk->ik", q, w)
    latent_mean = np.einsum("ir,irk->ik", q, a)
    second = np.einsum("ir,irk->ik", q, a * a)
    latent_sd = np.sqrt(np.clip(second - latent_mean**2, 0.0, None))
    return AlrPosterior(
        weight_mean=weight_mean,
        latent_mean=latent_mean,
        latent_sd=latent_sd,
        ess=1.0 / np.sum(q * q, axis=1),
        weights=q,
    )


def alr_posterior_at(
    dataset: ChoiceDataset,
    base: int,
    rho: float,
    mu_a: np.ndarray,
    chol: np.ndarray,
    *,
    n_draws: int = 200,
    seed: int = 0,
    draw_method: DrawMethod = "halton",
    draw_batch: int = 32,
    covariance: CovarianceMode = "full",
) -> AlrPosterior:
    """在**给定参数**下算 ALR 个体后验。拟合复用它，测试也能单独调它。"""
    lay = layout(dataset, base)
    xi = standard_normal_draws(lay.n_personas, n_draws, N_RANDOM, seed=seed, method=draw_method)
    theta = pack_alr(rho, np.asarray(mu_a, dtype=float), np.asarray(chol, dtype=float), covariance)
    ws, _, mu_v, chol_v = _simulate_alr(theta, lay, xi, base, covariance, draw_batch)
    q, _ = _posterior_weights(ws.ell)
    return _alr_posterior_from_weights(q, xi, mu_v, chol_v, base)


@dataclass(frozen=True)
class AlrFit:
    """ALR 分支的原始输出。潜空间是 a，不是 γ。"""

    rho: float
    gamma_0: float
    mu_alr: np.ndarray  # (6,)
    chol: np.ndarray  # (6,6)
    sigma_alr: np.ndarray  # (6,6) = L Lᵀ
    loglik: float
    converged: bool
    message: str
    n_iterations: int
    n_function_evals: int
    posterior: AlrPosterior
    w_center: np.ndarray  # (7,) E[w(a)]，权重空间实算
    sigma_w: np.ndarray  # (7,7) Cov[w(a)]，权重空间实算
    population_weights: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))
    mnl_loglik: float = float("nan")
    n_draws: int = 0
    draw_seed: int = 0
    draw_method: str = "halton"
    covariance: str = "diagonal"


def _starting_point(
    lay: _Layout, base: int, covariance: CovarianceMode
) -> tuple[np.ndarray, float]:
    """ρ 取池化 logit 的 log γ_0；μ_a 取它隐含权重的 ALR 坐标（若严格为正）。

    隐含权重有非正分量时退回零向量（均匀权重），**不裁剪**：裁剪一个起点会
    让人以为估计值也可以裁剪，而那正是本分支存在的理由所反对的。
    """
    g0_mnl, mu_mnl, ll_mnl, _ = fit_conditional_logit(lay)
    rho0 = float(np.log(max(g0_mnl, 1e-6)))
    others = list(other_indices(base))
    implied = np.empty(N_DIMS)
    implied[others] = mu_mnl / g0_mnl if g0_mnl > 0 else np.zeros(N_RANDOM)
    implied[base] = 1.0 - implied[others].sum()
    if np.all(implied > 1e-6):
        ln = np.log(implied)
        mu0 = ln[others] - ln[base]
    else:
        mu0 = np.zeros(N_RANDOM)
    return pack_alr(rho0, mu0, np.diag(np.full(N_RANDOM, 0.5)), covariance), ll_mnl


def fit_mixed_logit_alr(
    dataset: ChoiceDataset,
    base: int,
    *,
    n_draws: int = 200,
    seed: int = 0,
    covariance: CovarianceMode = "diagonal",
    draw_method: DrawMethod = "halton",
    maxiter: int = 500,
    tol: float = 1e-7,
    draw_batch: int = 32,
    population_draws: int = 20_000,
    start: np.ndarray | None = None,
) -> AlrFit:
    """ALR/logistic-normal 面板混合 logit。同一人设的全部选择共享一个 a_i。"""
    lay = layout(dataset, base)
    xi = standard_normal_draws(lay.n_personas, n_draws, N_RANDOM, seed=seed, method=draw_method)
    theta0, ll_mnl = _starting_point(lay, base, covariance)
    if start is not None:
        theta0 = np.asarray(start, dtype=float)

    res = minimize(
        alr_objective,
        theta0,
        args=(lay, xi, base, covariance, draw_batch),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": maxiter, "ftol": tol, "gtol": tol},
    )

    ws, gamma_0, mu_a, chol = _simulate_alr(res.x, lay, xi, base, covariance, draw_batch)
    q, loglik = _posterior_weights(ws.ell)
    posterior = _alr_posterior_from_weights(q, xi, mu_a, chol, base)
    center, cov_w, pop = population_moments(
        mu_a, chol, base, n=population_draws, seed=seed + 104_729
    )

    return AlrFit(
        rho=float(res.x[0]),
        gamma_0=gamma_0,
        mu_alr=mu_a,
        chol=chol,
        sigma_alr=chol @ chol.T,
        loglik=loglik,
        converged=bool(res.success),
        message=str(res.message),
        n_iterations=int(res.nit),
        n_function_evals=int(res.nfev),
        posterior=posterior,
        w_center=center,
        sigma_w=cov_w,
        population_weights=pop,
        mnl_loglik=ll_mnl,
        n_draws=n_draws,
        draw_seed=seed,
        draw_method=draw_method,
        covariance=covariance,
    )
