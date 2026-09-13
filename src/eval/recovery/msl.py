"""面板混合 logit 的最大模拟似然（P9 的估计后端）。

--------------------------------------------------------------------------
为什么是自己写的后端，而不是包一层 xlogit
--------------------------------------------------------------------------

`xlogit` 0.2.7 实测支持：固定系数、正态随机系数、**面板似然**（源码里的
``_prob_product_across_panels`` 按 panel 连乘）。实测**不支持**两件 P9 必需
的事：

1. **相关随机参数。** 0.2.7 的 ``MixedLogit.fit`` 签名里没有 ``correlation``
   一类的参数，源码中 "correlat" 只出现在 Halton 的文档字符串里。完全协方差
   规格（§11.7.1.3 的稳健性对照）表达不出来。
2. **个体随机系数的条件后验均值。** 只有 ``predict``，没有个体后验。而
   §11.7 的 `proj_i` 要的正是 `ŵ_i`。

第 2 件无论如何都要自己实现，而它需要的东西——同一批抽样 draws、每个人设
在全部选择任务上的联合似然——恰好就是似然本身的中间量：

    q_i^{(r)} = L_i(γ^{(r)}) / Σ_r L_i(γ^{(r)})     ← 后验权重
    ∂LL/∂θ    = Σ_i Σ_r q_i^{(r)} · ∂ℓ_i^{(r)}/∂θ   ← 梯度

也就是说梯度里那个权重**就是**后验权重。分两套代码去算同一个量，才是重复
造轮子。因此这里手写似然（CLAUDE.md 的技术栈也是这么规定条件 logit 的），
xlogit 留作对角规格的交叉验证后端（``xlogit_backend``）。

--------------------------------------------------------------------------
模型
--------------------------------------------------------------------------

    V_itc^{(r)} = γ_0·x_itcb + z_itc·γ_i^{(r)}
    γ_i^{(r)}   = μ + L·ξ_i^{(r)}        ξ ~ N(0, I_6)，L 下三角
    Σ_γ         = L·Lᵀ                   对角模式下 L 只有对角线

`γ_0` 是**固定但要估**的总体系数：它不在人设之间随机变化（否则 `w_k =
γ_k/γ_0` 是两个正态之比，分布不再可控，§11.7.1.1），但它也不是预先写死的
数——它就是 `1/σ`，是本设定能把尺度读出来的那个量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from scipy.optimize import minimize
from scipy.special import ndtri

from .design import N_RANDOM, differenced
from .schema import ChoiceDataset

CovarianceMode = Literal["diagonal", "full"]
DrawMethod = Literal["halton", "random"]

_PRIMES = (2, 3, 5, 7, 11, 13, 17, 19, 23, 29)


# ------------------------------------------------------------------ 抽样点


def _van_der_corput(index: np.ndarray, base: int) -> np.ndarray:
    out = np.zeros(len(index), dtype=float)
    f = 1.0
    i = index.astype(np.int64).copy()
    while np.any(i > 0):
        f /= base
        out += f * (i % base)
        i //= base
    return out


def standard_normal_draws(
    n_personas: int,
    n_draws: int,
    dim: int = N_RANDOM,
    *,
    seed: int = 0,
    method: DrawMethod = "halton",
) -> np.ndarray:
    """(n_personas, n_draws, dim) 的标准正态抽样点，**固定 seed 下可复现**。

    Halton 用一条长序列切给各人设（与 xlogit 同法），并做一次 Cranley-Patterson
    随机平移使 seed 生效——平移保持低差异性，所以既可复现又不是每次同一批点。
    """
    if n_personas <= 0 or n_draws <= 0:
        raise ValueError("人设数与抽样数都必须为正")
    if method == "random":
        rng = np.random.default_rng(seed)
        return rng.standard_normal((n_personas, n_draws, dim))
    if method != "halton":
        raise ValueError(f"未知抽样方式 {method!r}")
    if dim > len(_PRIMES):
        raise ValueError(f"Halton 最多支持 {len(_PRIMES)} 维")

    total = n_personas * n_draws
    idx = np.arange(1 + 100, 1 + 100 + total)  # 丢掉前 100 个点
    u = np.empty((total, dim), dtype=float)
    for d in range(dim):
        u[:, d] = _van_der_corput(idx, _PRIMES[d])
    shift = np.random.default_rng(seed).random(dim)
    u = np.mod(u + shift, 1.0)
    eps = 1e-12
    return ndtri(np.clip(u, eps, 1.0 - eps)).reshape(n_personas, n_draws, dim)


# ------------------------------------------------------------------ 参数打包


def _tril_indices(dim: int) -> tuple[np.ndarray, np.ndarray]:
    return np.tril_indices(dim)


def n_covariance_params(mode: CovarianceMode, dim: int = N_RANDOM) -> int:
    return dim if mode == "diagonal" else dim * (dim + 1) // 2


def unpack(theta: np.ndarray, mode: CovarianceMode, dim: int = N_RANDOM):
    gamma_0 = float(theta[0])
    mu = np.asarray(theta[1 : 1 + dim], dtype=float)
    rest = np.asarray(theta[1 + dim :], dtype=float)
    chol = np.zeros((dim, dim), dtype=float)
    if mode == "diagonal":
        np.fill_diagonal(chol, rest)
    else:
        r, c = _tril_indices(dim)
        chol[r, c] = rest
    return gamma_0, mu, chol


def pack(gamma_0: float, mu: np.ndarray, chol: np.ndarray, mode: CovarianceMode) -> np.ndarray:
    if mode == "diagonal":
        rest = np.diag(chol)
    else:
        r, c = _tril_indices(chol.shape[0])
        rest = chol[r, c]
    return np.concatenate([[gamma_0], np.asarray(mu, dtype=float), rest])


def _chol_gradient(grad_full: np.ndarray, mode: CovarianceMode) -> np.ndarray:
    """把对 L 全矩阵的梯度投到实际自由参数上。"""
    if mode == "diagonal":
        return np.diag(grad_full).copy()
    r, c = _tril_indices(grad_full.shape[0])
    return grad_full[r, c]


# ------------------------------------------------------------------ 索引


@dataclass(frozen=True)
class _Layout:
    """拟合时反复用到的、只依赖数据不依赖参数的索引与矩阵。"""

    xb: np.ndarray  # (n_rows,)
    z: np.ndarray  # (n_rows, 6)
    chosen_f: np.ndarray  # (n_rows,) 0/1
    row_task: np.ndarray
    row_persona: np.ndarray
    task_starts: np.ndarray
    persona_starts: np.ndarray
    task_persona_starts: np.ndarray
    chosen_row: np.ndarray
    n_personas: int
    n_tasks: int
    n_rows: int


def layout(dataset: ChoiceDataset, base: int) -> _Layout:
    xb, z = differenced(dataset.features, base)
    task_persona = dataset.task_persona
    task_persona_starts = np.flatnonzero(np.r_[True, task_persona[1:] != task_persona[:-1]])
    return _Layout(
        xb=np.ascontiguousarray(xb),
        z=np.ascontiguousarray(z),
        chosen_f=dataset.chosen.astype(float),
        row_task=dataset.row_task,
        row_persona=dataset.row_persona,
        task_starts=dataset.task_starts,
        persona_starts=dataset.persona_starts,
        task_persona_starts=task_persona_starts,
        chosen_row=dataset.chosen_row,
        n_personas=dataset.n_personas,
        n_tasks=dataset.n_tasks,
        n_rows=dataset.n_rows,
    )


# ------------------------------------------------------------------ 条件 logit


def _mnl_objective(theta: np.ndarray, lay: _Layout) -> tuple[float, np.ndarray]:
    gamma_0 = theta[0]
    mu = theta[1:]
    v = gamma_0 * lay.xb + lay.z @ mu
    m = np.maximum.reduceat(v, lay.task_starts)
    e = np.exp(v - m[lay.row_task])
    s = np.add.reduceat(e, lay.task_starts)
    loglik = float(np.sum(v[lay.chosen_row] - (m + np.log(s))))
    p = e / s[lay.row_task]
    resid = lay.chosen_f - p
    grad = np.concatenate([[float(resid @ lay.xb)], lay.z.T @ resid])
    return -loglik, -grad


def fit_conditional_logit(
    lay: _Layout, *, maxiter: int = 500, tol: float = 1e-8
) -> tuple[float, np.ndarray, float, bool]:
    """手写似然的条件 logit（CLAUDE.md 的技术栈规定），用作 MSL 的起点。

    它本身也是一个诊断：若池化 logit 都拟合不动，混合 logit 不会更好。
    """
    theta0 = np.zeros(1 + N_RANDOM)
    theta0[0] = 1.0
    res = minimize(
        _mnl_objective,
        theta0,
        args=(lay,),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": maxiter, "ftol": tol, "gtol": tol},
    )
    return float(res.x[0]), np.asarray(res.x[1:], dtype=float), float(-res.fun), bool(res.success)


# ------------------------------------------------------------------ MSL


@dataclass
class _Workspace:
    ell: np.ndarray  # (n_personas, R)
    g0: np.ndarray  # (n_personas, R)
    gmu: np.ndarray  # (n_personas, R, 6)


def _simulate(
    theta: np.ndarray,
    lay: _Layout,
    xi: np.ndarray,
    mode: CovarianceMode,
    batch: int,
) -> tuple[_Workspace, np.ndarray, np.ndarray]:
    gamma_0, mu, chol = unpack(theta, mode)
    n_draws = xi.shape[1]
    ws = _Workspace(
        ell=np.empty((lay.n_personas, n_draws)),
        g0=np.empty((lay.n_personas, n_draws)),
        gmu=np.empty((lay.n_personas, n_draws, N_RANDOM)),
    )
    base_v = gamma_0 * lay.xb

    for lo in range(0, n_draws, batch):
        hi = min(lo + batch, n_draws)
        # γ_i^{(r)} = μ + L ξ_i^{(r)} ，形状 (n_personas, b, 6)
        gamma = mu + xi[:, lo:hi, :] @ chol.T
        v = np.repeat(base_v[:, None], hi - lo, axis=1)
        for k in range(N_RANDOM):
            v += lay.z[:, k : k + 1] * gamma[lay.row_persona, :, k]

        m = np.maximum.reduceat(v, lay.task_starts, axis=0)
        e = np.exp(v - m[lay.row_task])
        s = np.add.reduceat(e, lay.task_starts, axis=0)
        logp_task = v[lay.chosen_row] - (m + np.log(s))
        ws.ell[:, lo:hi] = np.add.reduceat(logp_task, lay.task_persona_starts, axis=0)

        resid = lay.chosen_f[:, None] - e / s[lay.row_task]
        ws.g0[:, lo:hi] = np.add.reduceat(resid * lay.xb[:, None], lay.persona_starts, axis=0)
        for k in range(N_RANDOM):
            ws.gmu[:, lo:hi, k] = np.add.reduceat(
                resid * lay.z[:, k : k + 1], lay.persona_starts, axis=0
            )

    return ws, mu, chol


def _posterior_weights(ell: np.ndarray) -> tuple[np.ndarray, float]:
    """log-sum-exp 归一。长面板下 ℓ 可以到 −10³ 量级，指数化前必须先减最大值。"""
    m = ell.max(axis=1, keepdims=True)
    w = np.exp(ell - m)
    s = w.sum(axis=1, keepdims=True)
    loglik = float(np.sum(m[:, 0] + np.log(s[:, 0]) - np.log(ell.shape[1])))
    return w / s, loglik


def _msl_objective(
    theta: np.ndarray, lay: _Layout, xi: np.ndarray, mode: CovarianceMode, batch: int
) -> tuple[float, np.ndarray]:
    ws, _, _ = _simulate(theta, lay, xi, mode, batch)
    q, loglik = _posterior_weights(ws.ell)
    d_gamma0 = float(np.sum(q * ws.g0))
    d_mu = np.einsum("ir,irk->k", q, ws.gmu)
    d_chol = np.einsum("ir,irm,irn->mn", q, ws.gmu, xi)
    grad = np.concatenate([[d_gamma0], d_mu, _chol_gradient(d_chol, mode)])
    return -loglik, -grad


@dataclass(frozen=True)
class Posterior:
    """个体随机系数的条件后验（模拟条件期望）。

        E[γ_i | y_i] ≈ Σ_r γ^{(r)}·L_i(γ^{(r)}) / Σ_r L_i(γ^{(r)})

    其中 ``L_i`` 是该人设**全部**选择任务的联合似然。分子分母都在对数空间算，
    归一用 log-sum-exp：长面板下 ℓ_i 可以到 −10³ 量级，直接指数化会下溢成零，
    于是后验退化成"全押在某一个 draw 上"——那看起来仍然像个合理的数。
    """

    mean: np.ndarray  # (n_personas, 6)
    sd: np.ndarray  # (n_personas, 6)
    ess: np.ndarray  # (n_personas,) 有效抽样数 1/Σq²
    weights: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))


def posterior_at(
    dataset: ChoiceDataset,
    base: int,
    gamma_0: float,
    mu: np.ndarray,
    chol: np.ndarray,
    *,
    n_draws: int = 200,
    seed: int = 0,
    draw_method: DrawMethod = "halton",
    draw_batch: int = 32,
    covariance: CovarianceMode = "full",
) -> Posterior:
    """在**给定参数**下算个体后验。拟合复用它，测试也能单独调它。"""
    lay = layout(dataset, base)
    xi = standard_normal_draws(lay.n_personas, n_draws, N_RANDOM, seed=seed, method=draw_method)
    theta = pack(gamma_0, np.asarray(mu, dtype=float), np.asarray(chol, dtype=float), covariance)
    ws, mu_v, chol_v = _simulate(theta, lay, xi, covariance, draw_batch)
    q, _ = _posterior_weights(ws.ell)
    return _posterior_from_weights(q, xi, mu_v, chol_v)


def _posterior_from_weights(
    q: np.ndarray, xi: np.ndarray, mu: np.ndarray, chol: np.ndarray
) -> Posterior:
    xi_bar = np.einsum("ir,irk->ik", q, xi)
    mean = mu + xi_bar @ chol.T
    second = np.einsum("ir,irm,irn->imn", q, xi, xi)
    cov_xi = second - xi_bar[:, :, None] * xi_bar[:, None, :]
    cov_gamma = np.einsum("mp,ipq,nq->imn", chol, cov_xi, chol)
    sd = np.sqrt(np.clip(np.einsum("imm->im", cov_gamma), 0.0, None))
    return Posterior(mean=mean, sd=sd, ess=1.0 / np.sum(q * q, axis=1), weights=q)


@dataclass(frozen=True)
class MslFit:
    """一次拟合的原始输出。γ 空间，未反解。"""

    gamma_0: float
    mu_gamma: np.ndarray  # (6,)
    chol: np.ndarray  # (6,6) 下三角
    sigma_gamma: np.ndarray  # (6,6) = L Lᵀ
    loglik: float
    converged: bool
    message: str
    n_iterations: int
    n_function_evals: int
    posterior_gamma: np.ndarray  # (n_personas, 6) 条件后验均值
    posterior_sd_gamma: np.ndarray  # (n_personas, 6) 条件后验标准差
    posterior_ess: np.ndarray  # (n_personas,) 有效抽样数 1/Σq²
    mnl_gamma_0: float
    mnl_mu: np.ndarray
    mnl_loglik: float
    n_draws: int
    draw_seed: int
    draw_method: str
    covariance: str
    start: np.ndarray = field(repr=False, default_factory=lambda: np.empty(0))


def fit_mixed_logit(
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
    start: np.ndarray | None = None,
) -> MslFit:
    """面板混合 logit。同一人设的全部选择任务共享一个 γ_i。"""
    lay = layout(dataset, base)
    xi = standard_normal_draws(lay.n_personas, n_draws, N_RANDOM, seed=seed, method=draw_method)

    g0_mnl, mu_mnl, ll_mnl, _ = fit_conditional_logit(lay)
    if start is None:
        # 起点的尺度：sd(γ_k) ≈ sd(w_k)/σ，而 Dirichlet 下 sd(w_k) 是 0.1 量级，
        # 所以取 |μ| 的三成加一个不为零的下限。起点只要不病态就行。
        scale = 0.3 * np.abs(mu_mnl) + 1e-2
        theta0 = pack(g0_mnl, mu_mnl, np.diag(scale), covariance)
    else:
        theta0 = np.asarray(start, dtype=float)

    res = minimize(
        _msl_objective,
        theta0,
        args=(lay, xi, covariance, draw_batch),
        jac=True,
        method="L-BFGS-B",
        options={"maxiter": maxiter, "ftol": tol, "gtol": tol},
    )

    ws, mu, chol = _simulate(res.x, lay, xi, covariance, draw_batch)
    q, loglik = _posterior_weights(ws.ell)
    posterior = _posterior_from_weights(q, xi, mu, chol)

    gamma_0, mu_hat, chol_hat = unpack(res.x, covariance)
    return MslFit(
        gamma_0=gamma_0,
        mu_gamma=mu_hat,
        chol=chol_hat,
        sigma_gamma=chol_hat @ chol_hat.T,
        loglik=loglik,
        converged=bool(res.success),
        message=str(res.message),
        n_iterations=int(res.nit),
        n_function_evals=int(res.nfev),
        posterior_gamma=posterior.mean,
        posterior_sd_gamma=posterior.sd,
        posterior_ess=posterior.ess,
        mnl_gamma_0=g0_mnl,
        mnl_mu=mu_mnl,
        mnl_loglik=ll_mnl,
        n_draws=n_draws,
        draw_seed=seed,
        draw_method=draw_method,
        covariance=covariance,
        start=theta0,
    )
