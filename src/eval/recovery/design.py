"""差分设计矩阵（README §11.7.1.1）。纯数学，不认识任何估计库。

    η_itc = Σ_{k=1..7} w_ik·x_itck ，  Σ_k w_ik = 1

代入 ``w_ib = 1 − Σ_{k≠b} w_ik``：

    η_itc = x_itcb + Σ_{k≠b} w_ik·(x_itck − x_itcb)

除以选择尺度 σ：

    V_itc = γ_0·x_itcb + Σ_{k≠b} γ_ik·(x_itck − x_itcb)
    γ_0  = 1/σ                （固定但要估的总体系数）
    γ_ik = w_ik/σ             （人设层随机系数）

**基准维 b 是一个参数，不是常数。** 它在这里出现一次，之后所有差分、反解、
协方差回构都从调用方接同一个 ``base``。§11.7.1.3：差分不是等距变换，γ 空间
的"对角"依赖于选哪一维作基准，换基准维就是换模型，所以必须能换。
"""

from __future__ import annotations

import numpy as np

from worlds.dimensions import ALL_DIMENSIONS

N_DIMS = len(ALL_DIMENSIONS)
N_RANDOM = N_DIMS - 1


class BaseDimensionError(ValueError):
    """基准维不是七个维度之一。"""


def base_index(base_dim: str | int) -> int:
    """维度名（或下标）→ 下标，并校验。"""
    if isinstance(base_dim, int):
        if not 0 <= base_dim < N_DIMS:
            raise BaseDimensionError(f"基准维下标必须落在 [0, {N_DIMS})，得到 {base_dim}")
        return base_dim
    try:
        return ALL_DIMENSIONS.index(base_dim)
    except ValueError as exc:
        raise BaseDimensionError(
            f"{base_dim!r} 不是维度之一；可选：{', '.join(ALL_DIMENSIONS)}"
        ) from exc


def other_indices(base: int) -> tuple[int, ...]:
    """六个非基准维的下标，保持 ``ALL_DIMENSIONS`` 的相对顺序。"""
    return tuple(k for k in range(N_DIMS) if k != base)


def design_column_names(base: int) -> tuple[str, ...]:
    """七列的名字：一列基准维原值，六列差分。列序即系数序。"""
    b = ALL_DIMENSIONS[base]
    return (b, *(f"{ALL_DIMENSIONS[k]}-{b}" for k in other_indices(base)))


def differenced(features: np.ndarray, base: int) -> tuple[np.ndarray, np.ndarray]:
    """(n_rows, 7) → (基准维原值 (n_rows,), 六列差分 (n_rows, 6))。"""
    x = np.asarray(features, dtype=float)
    if x.ndim != 2 or x.shape[1] != N_DIMS:
        raise ValueError(f"features 形状必须是 (n, {N_DIMS})，得到 {x.shape}")
    xb = x[:, base]
    z = x[:, list(other_indices(base))] - xb[:, None]
    return xb, z


def design_matrix(features: np.ndarray, base: int) -> np.ndarray:
    """七列设计矩阵，列序与 ``design_column_names`` 一致。"""
    xb, z = differenced(features, base)
    return np.column_stack([xb, z])


def systematic_utility(features: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """原始形式 Σ_k w_k·x_k。等价性测试的一端。"""
    return np.asarray(features, dtype=float) @ np.asarray(weights, dtype=float)


def differenced_utility(xb: np.ndarray, z: np.ndarray, w_others: np.ndarray) -> np.ndarray:
    """差分形式 x_b + Σ_{k≠b} w_k·(x_k − x_b)。等价性测试的另一端。"""
    return xb + z @ np.asarray(w_others, dtype=float)


def within_task_variation(features: np.ndarray, within_task: np.ndarray) -> np.ndarray:
    """每个属性在**选择集内部**的标准差。(n_dims,)

    条件 logit 只识别组内差异，所以这才是估计器真正看到的变异。某一维在组内
    几乎不动时，它的权重就不是从数据里识别出来的——而单纯形约束会把那个任意
    的值从别的维度里扣走，于是整个 `ŵ` 都歪掉。

    **它抓得到条件数抓不到的情形。** 设计矩阵是 `[x_b, x_k − x_b, …]`：若某个
    `x_k ≈ 常数`，那一列差分就 ≈ `−x_b`，与第 0 列近乎重合，条件数爆掉；但若
    把那个近乎常数的维度选作**基准维**，条件数反而很低（各差分列保留了自己的
    变异），问题却一点没少——此时 `γ_0` 只能从一列几乎不动的数里识别。所以
    这个按维度的量是基准维不变的，条件数不是。
    """
    x = np.asarray(features, dtype=float)
    seg = np.asarray(within_task, dtype=int)
    counts = np.bincount(seg, minlength=seg.max() + 1).astype(float)
    means = np.zeros((len(counts), x.shape[1]))
    np.add.at(means, seg, x)
    means /= counts[:, None]
    return (x - means[seg]).std(axis=0)


def weakly_identified_dimensions(within_sd: np.ndarray, *, ratio: float = 0.1) -> tuple[str, ...]:
    """组内变异不足中位数 ``ratio`` 倍的维度名。默认十分之一。"""
    sd = np.asarray(within_sd, dtype=float)
    threshold = float(np.median(sd)) * ratio
    return tuple(ALL_DIMENSIONS[k] for k in range(len(sd)) if sd[k] < threshold)


def condition_number(design: np.ndarray, *, within_task: np.ndarray | None = None) -> float:
    """列等尺度化之后的条件数。

    **列必须先归一到单位长度**，否则量的主要是列的尺度差异而不是共线性
    （``worlds.generator.condition_number`` 里有同一条注释，同一个理由：
    Belsley-Kuh-Welsch 的经验阈值 30 针对的是已等尺度化的矩阵）。

    ``within_task`` 给出每行所属选择集的下标时，先做**组内中心化**：条件
    logit 只识别组内差异，备选集之间的共同水平在 softmax 里被约掉，带着它
    算出的条件数不是估计器实际面对的那个。
    """
    m = np.asarray(design, dtype=float)
    if within_task is not None:
        seg = np.asarray(within_task, dtype=int)
        counts = np.bincount(seg, minlength=seg.max() + 1).astype(float)
        means = np.zeros((len(counts), m.shape[1]))
        np.add.at(means, seg, m)
        means /= counts[:, None]
        m = m - means[seg]
    norms = np.linalg.norm(m, axis=0)
    if np.any(norms <= 0):
        return float("inf")  # 某一列在数据里完全没有变异
    sv = np.linalg.svd(m / norms, compute_uv=False)
    return float("inf") if sv[-1] <= 0 else float(sv[0] / sv[-1])
