"""ALR / logistic-normal 参数化的纯数学（P9 的第二个分支）。

差分正态分支（``difference_normal``）把 `w` 的异质性直接建模成正态，因此
`Σw = 1` 恒成立而 `0 ≤ w_k ≤ 1` 不成立——它如实报告越界，不裁剪。本分支换一个
坐标系，让两条约束**同时由构造保证**：

    a_{i,k} = log(w_{i,k} / w_{i,b})        k ≠ b，基准维坐标恒为 0
    a_i     = μ_a + L·ξ_i                   ξ ~ N(0, I₆)
    w_i     = softmax(a_{i,1}, …, 0_b, …, a_{i,6})

于是 `0 < w_k < 1` 与 `Σ_k w_k = 1` 都是恒等式，不是需要事后修正的性质。

尺度走对数参数化：

    ρ = log γ_0 ，  γ_0 = exp(ρ) = 1/σ  > 0

效用是**原始形式**，不做差分：

    η_itc = γ_0 · Σ_{k=1..7} w_{i,k}·x_{itc,k}

--------------------------------------------------------------------------
一个必须说清楚的界限
--------------------------------------------------------------------------

**权重合法不等于权重可识别。** softmax 只保证 `ŵ` 落在单纯形内；某一维在
选择集内部几乎不变化时，似然在该方向上仍然是平的，估出来的那个值照样是
任意的，只是现在被压在 `(0,1)` 里而不是跑到 `-0.9`。把本分支当作
``weakly_identified_dimensions`` 的解法是把两件事搞混了。
"""

from __future__ import annotations

import numpy as np

from .design import N_DIMS, N_RANDOM, other_indices

#: ALR 坐标的软截断。softmax 本身对大值是稳定的（先减最大值），这条只防
#: 优化器在完全平坦的方向上把坐标推到 ±10³ 之后 `exp` 下溢成恒零梯度。
#: 取 ±40：`exp(-40) ≈ 4e-18`，早已是"该维权重等于零"的实际含义。
ALR_COORD_CAP = 40.0


def expand_alr(a: np.ndarray, base: int) -> np.ndarray:
    """(..., 6) 的 ALR 坐标 → (..., 7)，基准维补零。"""
    a = np.asarray(a, dtype=float)
    if a.shape[-1] != N_RANDOM:
        raise ValueError(f"ALR 坐标必须有 {N_RANDOM} 个分量，得到 {a.shape[-1]}")
    full = np.zeros((*a.shape[:-1], N_DIMS), dtype=float)
    full[..., list(other_indices(base))] = a
    return full


def weights_from_alr(a: np.ndarray, base: int) -> np.ndarray:
    """ALR 坐标 → 七维权重。稳定 softmax，构造上严格为正且和为一。"""
    full = expand_alr(a, base)
    full = np.clip(full, -ALR_COORD_CAP, ALR_COORD_CAP)
    full -= full.max(axis=-1, keepdims=True)
    e = np.exp(full)
    return e / e.sum(axis=-1, keepdims=True)


def alr_from_weights(weights: np.ndarray, base: int) -> np.ndarray:
    """七维权重 → ALR 坐标。``weights_from_alr`` 的逆，要求全部分量严格为正。"""
    w = np.asarray(weights, dtype=float)
    if w.shape[-1] != N_DIMS:
        raise ValueError(f"权重必须有 {N_DIMS} 个分量，得到 {w.shape[-1]}")
    if np.any(w <= 0.0):
        raise ValueError("ALR 只对严格为正的权重有定义；这正是它与差分正态分支的区别")
    ln = np.log(w)
    return ln[..., list(other_indices(base))] - ln[..., base : base + 1]


# ------------------------------------------------------------------ 参数打包
#
# 与 ``msl.pack``/``unpack`` **刻意分开**：差分分支的 Cholesky 对角线是可正
# 可负的自由参数（符号不影响 Σ = LLᵀ），本分支按规格用 log-diagonal，保证
# 对角线严格为正且光滑，不取绝对值。两套打包各自独立，互不影响。


def n_params(covariance: str, dim: int = N_RANDOM) -> int:
    return 1 + dim + (dim if covariance == "diagonal" else dim * (dim + 1) // 2)


def unpack_alr(theta: np.ndarray, covariance: str, dim: int = N_RANDOM):
    """θ → (ρ, μ_a, L)。L 的对角线是 ``exp(参数)``。"""
    rho = float(theta[0])
    mu = np.asarray(theta[1 : 1 + dim], dtype=float)
    rest = np.asarray(theta[1 + dim :], dtype=float)
    chol = np.zeros((dim, dim), dtype=float)
    if covariance == "diagonal":
        np.fill_diagonal(chol, np.exp(rest))
    else:
        r, c = np.tril_indices(dim)
        chol[r, c] = rest
        np.fill_diagonal(chol, np.exp(np.diag(chol)))
    return rho, mu, chol


def pack_alr(rho: float, mu: np.ndarray, chol: np.ndarray, covariance: str) -> np.ndarray:
    dim = chol.shape[0]
    if covariance == "diagonal":
        rest = np.log(np.diag(chol))
    else:
        work = np.array(chol, dtype=float, copy=True)
        np.fill_diagonal(work, np.log(np.diag(work)))
        r, c = np.tril_indices(dim)
        rest = work[r, c]
    return np.concatenate([[float(rho)], np.asarray(mu, dtype=float), rest])


def chol_param_gradient(grad_full: np.ndarray, chol: np.ndarray, covariance: str) -> np.ndarray:
    """把对 L 各元素的梯度换算到实际自由参数上。

    对角线走 ``L_mm = exp(p_m)``，所以链式法则多一个 ``L_mm``：
    ``∂/∂p_m = (∂/∂L_mm)·L_mm``。非对角元是恒等映射。
    """
    g = np.array(grad_full, dtype=float, copy=True)
    dim = g.shape[0]
    diag_scaled = np.diag(g) * np.diag(chol)
    if covariance == "diagonal":
        return diag_scaled
    np.fill_diagonal(g, diag_scaled)
    r, c = np.tril_indices(dim)
    return g[r, c]


# ------------------------------------------------------------------ 总体矩


def population_draws(
    mu_a: np.ndarray, chol: np.ndarray, base: int, *, n: int = 20_000, seed: int = 0
) -> np.ndarray:
    """从 N(μ_a, Σ_a) 抽点并映到权重空间。(n, 7)，每行严格在单纯形内部。"""
    rng = np.random.default_rng(seed)
    a = (
        np.asarray(mu_a, dtype=float)
        + rng.standard_normal((n, len(mu_a))) @ np.asarray(chol, dtype=float).T
    )
    return weights_from_alr(a, base)


def population_moments(
    mu_a: np.ndarray, chol: np.ndarray, base: int, *, n: int = 20_000, seed: int = 0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(中心, 协方差, 抽样点)，**在权重空间实算**，不用 delta 近似。

    `w(a)` 是 `a` 的非线性函数，delta 只在 Σ_a 很小时才接近；而本项目要报的
    正是 Σ_w 本身有多大，用一个"假定它很小"的近似去估它是循环的。

    `Σ_w·1 = 0` 与 `rank ≤ 6` 在这里不是断言出来的：每一个抽样点的分量和都
    恰好是 1，所以 `Var(1ᵀw) = 0` 自动成立，秩至多 6。
    """
    w = population_draws(mu_a, chol, base, n=n, seed=seed)
    center = w.mean(axis=0)
    cov = np.cov(w, rowvar=False, bias=False)
    cov = 0.5 * (cov + cov.T)
    return center, cov, w
