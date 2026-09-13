"""γ 空间 → w 空间的反解与协方差回构（README §11.7.1.1、§11.7.1.2）。

纯数学，不碰优化器：这样协方差那套线性代数可以在不跑一次 MSL 的情况下单测。

    σ̂     = 1/γ̂_0
    ŵ_k   = γ̂_k/γ̂_0        (k ≠ b)
    ŵ_b   = 1 − Σ_{k≠b} ŵ_k

    Σ_q   = Σ_γ / γ_0²                    六维，非基准权重的协方差
    Σ_w   = B_b · Σ_q · B_bᵀ              七维，秩至多 6

``B_b`` 是 7×6 的线性映射：非基准维取对应单位向量，基准维那一行是 ``−1ᵀ``
（因为 ``w_b = 1 − Σ q``，常数项不进协方差）。由此 ``Bᵀ1 = 0``，于是
``Σ_w·1 = 0`` 与 ``1ᵀΣ_w1 = 0`` 是**构造上恒成立**的，不是数值巧合，也不需要
事后裁剪。这正是硬规则 14 要的：w 空间里不存在非零对角协方差，能存在的是
这个秩亏矩阵。
"""

from __future__ import annotations

import numpy as np

from .design import N_DIMS, N_RANDOM, other_indices

#: γ_0 = 1/σ。它趋零就是选择趋于随机，而 ŵ = γ_k/γ_0 的矩在那里不存在
#: （README §11.7.4、§10.3.3：这与门二是同一个物理情形）。低于这条线直接
#: 抛错——取绝对值或加 ε 继续，会把一个"装置坏了"的信号变成一串合理的数。
GAMMA0_FLOOR = 1e-8


class DegenerateScaleError(RuntimeError):
    """γ_0 非正或数值上贴着零：比值反解在此处没有有限矩。"""


def check_gamma_0(gamma_0: float, *, floor: float = GAMMA0_FLOOR) -> float:
    g = float(gamma_0)
    if not np.isfinite(g) or g <= floor:
        raise DegenerateScaleError(
            f"γ_0 = {gamma_0!r} 不可用于反解（下限 {floor:g}）。"
            "γ_0 = 1/σ，趋零意味着选择趋于随机，此时 ŵ = γ_k/γ_0 的矩不存在；"
            "这是门二要拦的情形，不是一个可以用绝对值绕过去的数值问题。"
        )
    return g


def expansion_matrix(base: int, n_dims: int = N_DIMS) -> np.ndarray:
    """B_b：六维非基准权重 → 七维权重的线性部分。形状 (n_dims, n_dims−1)。"""
    others = (
        other_indices(base) if n_dims == N_DIMS else tuple(k for k in range(n_dims) if k != base)
    )
    b = np.zeros((n_dims, n_dims - 1), dtype=float)
    for j, k in enumerate(others):
        b[k, j] = 1.0
    b[base, :] = -1.0
    return b


def weights_from_gamma(gamma_0: float, gamma_others: np.ndarray, base: int) -> np.ndarray:
    """(γ_0, γ_{k≠b}) → 七维权重，和恒为 1。"""
    g0 = check_gamma_0(gamma_0)
    q = np.asarray(gamma_others, dtype=float) / g0
    if q.shape[-1] != N_RANDOM:
        raise ValueError(f"γ 的非基准分量必须有 {N_RANDOM} 个，得到 {q.shape[-1]}")
    w = np.empty((*q.shape[:-1], N_DIMS), dtype=float)
    others = list(other_indices(base))
    w[..., others] = q
    w[..., base] = 1.0 - q.sum(axis=-1)
    return w


def sigma_q_from_sigma_gamma(sigma_gamma: np.ndarray, gamma_0: float) -> np.ndarray:
    """Σ_q = Σ_γ/γ_0²。

    这是**点估计的变换**，刻意不带 γ_0 自身的抽样波动——那一层由 block
    bootstrap 传播（README §11.7.4：delta 方法在这里要带上 Cov(γ_0, γ_k)，
    而且 w_b = 1 − Σw_k 还要再传播一层）。
    """
    g0 = check_gamma_0(gamma_0)
    return np.asarray(sigma_gamma, dtype=float) / (g0 * g0)


def sigma_w_from_sigma_gamma(sigma_gamma: np.ndarray, gamma_0: float, base: int) -> np.ndarray:
    """Σ_w = B_b·(Σ_γ/γ_0²)·B_bᵀ。七维，对称，半正定，秩至多 6，行和为零。"""
    b = expansion_matrix(base)
    sigma_q = sigma_q_from_sigma_gamma(sigma_gamma, gamma_0)
    sigma_w = b @ sigma_q @ b.T
    return 0.5 * (sigma_w + sigma_w.T)  # 只压掉浮点非对称，不改变任何结构


def numerical_rank(matrix: np.ndarray, *, rtol: float = 1e-10) -> int:
    """相对最大奇异值的数值秩。单纯形约束下 Σ_w 应当是 6。"""
    sv = np.linalg.svd(np.asarray(matrix, dtype=float), compute_uv=False)
    if sv.size == 0 or sv[0] <= 0:
        return 0
    return int(np.sum(sv > rtol * sv[0]))


def is_positive_semidefinite(matrix: np.ndarray, *, atol: float = 1e-10) -> bool:
    m = np.asarray(matrix, dtype=float)
    if not np.allclose(m, m.T, atol=atol):
        return False
    eig = np.linalg.eigvalsh(0.5 * (m + m.T))
    scale = max(1.0, float(np.abs(eig).max()))
    return bool(eig.min() >= -atol * scale)


def simplex_violation(weights: np.ndarray) -> dict[str, float]:
    """越界诊断：**报告，不修正。**

    差分法保证 ``Σŵ = 1``，但随机系数取正态时不保证 ``0 ≤ ŵ_k ≤ 1``。
    静默 clip / project / renormalize 会改变估计量本身及其抽样性质，因此
    这里只统计（用户指令第八节；改分布需要新的 DECISIONS 记录）。
    """
    w = np.atleast_2d(np.asarray(weights, dtype=float))
    below = w < 0.0
    above = w > 1.0
    any_row = np.any(below | above, axis=1)
    worst_below = float(-w[below].min()) if below.any() else 0.0
    worst_above = float((w[above] - 1.0).max()) if above.any() else 0.0
    return {
        "fraction_outside": float(any_row.mean()),
        "fraction_negative_entries": float(below.mean()),
        "fraction_above_one_entries": float(above.mean()),
        "max_below_zero": worst_below,
        "max_above_one": worst_above,
        "max_magnitude": max(worst_below, worst_above),
    }
