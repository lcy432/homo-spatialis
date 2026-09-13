"""可识别性诊断：只看**真正进入选择模型的那部分信息**。只读，不改任何生成。

条件 logit 只识别组内差异——备选集之间的共同水平在 softmax 里被约掉。因此
"这一维在世界里变化多少"不是相关的量，"这一维在**同一个选择集内部**变化多少"
才是。本模块量三件事：

``within_sd``
    ``s_k = SD(x̃_k)``，其中 ``x̃_itc,k = x_itc,k − mean_c x_itc,k``。
    答"这一维在组内到底动不动"。

``unique_sd``
    把 ``x̃_k`` 对其余六列做最小二乘，取残差的标准差 ``u_k``。
    答"它就算动，是不是几乎能被别的维度解释掉"。一维可以 ``within_sd``
    很大而 ``unique_sd`` 很小——那时它照样不可识别，而 ``within_sd`` 单独
    看不出来。

**切空间奇异值**
    取一组固定的正交基 ``Q``（7×6，``QᵀQ = I₆``，``Qᵀ1 = 0``），看
    ``D = X̃·Q`` 的奇异谱。权重落在单纯形上，可动的方向只有 6 个；在单纯形
    的切空间里看设计矩阵，才是估计器实际面对的那个几何。最小奇异值对应的
    方向就是"最测不出来的那个权重组合"。

三者都是**诊断**，不是新的估计器，也不取代 ``design.condition_number``：
后者量的是差分设计（含基准维那一列）的病态程度，是基准维相关的；这里的量
全部基准维无关。

> 这个模块刻意**不**引入任何实验设计框架（D-最优、E-最优之类）。它只回答
> "现有数据里有没有信息"，不回答"该造什么数据"。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from worlds.dimensions import ALL_DIMENSIONS

from .design import N_DIMS
from .schema import ChoiceDataset


def simplex_tangent_basis(n: int = N_DIMS) -> np.ndarray:
    """单纯形切空间的固定正交基（Helmert 对照）。(n, n−1)，``QᵀQ = I``、``Qᵀ1 = 0``。

    固定而非随机：诊断要能跨运行比较，基一换，"最弱方向"就换一套坐标。
    """
    q = np.zeros((n, n - 1), dtype=float)
    for j in range(1, n):
        scale = 1.0 / np.sqrt(j * (j + 1.0))
        q[:j, j - 1] = scale
        q[j, j - 1] = -j * scale
    return q


def within_centered(features: np.ndarray, within_task: np.ndarray) -> np.ndarray:
    """按选择集中心化。这一步之后剩下的，就是条件 logit 能看见的全部。"""
    x = np.asarray(features, dtype=float)
    seg = np.asarray(within_task, dtype=int)
    counts = np.bincount(seg, minlength=seg.max() + 1).astype(float)
    means = np.zeros((len(counts), x.shape[1]))
    np.add.at(means, seg, x)
    means /= counts[:, None]
    return x - means[seg]


def unique_variation(centered: np.ndarray) -> np.ndarray:
    """每一列对其余列回归之后的残差标准差。(n_dims,)"""
    m = np.asarray(centered, dtype=float)
    out = np.empty(m.shape[1])
    for k in range(m.shape[1]):
        others = np.delete(m, k, axis=1)
        coef, *_ = np.linalg.lstsq(others, m[:, k], rcond=None)
        out[k] = float((m[:, k] - others @ coef).std())
    return out


@dataclass(frozen=True)
class IdentifiabilityReport:
    """一批选择数据在七维权重空间上的可识别性。全部基准维无关。"""

    dimensions: tuple[str, ...]
    within_sd: tuple[float, ...]
    unique_sd: tuple[float, ...]
    within_sd_relative: tuple[float, ...]  # s_k / median_j(s_j)
    unique_sd_relative: tuple[float, ...]  # u_k / median_j(s_j)
    tangent_singular_values: tuple[float, ...]  # 6 个
    tangent_condition_number: float  # s_max / s_min
    weakest_direction: tuple[float, ...]  # 7 维，最小奇异值对应的权重方向

    def flagged(self, *, threshold: float) -> tuple[str, ...]:
        """``unique_sd_relative`` 低于阈值的维度名。

        取 ``unique`` 而非 ``within``：一维即使自己会动，只要动的部分能被别的
        维度解释掉，它的权重照样不可识别，而这正是 ``within`` 看不见的那一半。
        """
        return tuple(
            d
            for d, u in zip(self.dimensions, self.unique_sd_relative, strict=True)
            if u < threshold
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "dimensions": list(self.dimensions),
            "within_sd": list(self.within_sd),
            "unique_sd": list(self.unique_sd),
            "within_sd_relative": list(self.within_sd_relative),
            "unique_sd_relative": list(self.unique_sd_relative),
            "tangent_singular_values": list(self.tangent_singular_values),
            "tangent_condition_number": self.tangent_condition_number,
            "weakest_direction": list(self.weakest_direction),
        }


def identifiability_report(dataset: ChoiceDataset) -> IdentifiabilityReport:
    """在一批选择数据上算可识别性诊断。不改任何输入。"""
    centered = within_centered(dataset.features, dataset.row_task)
    s = centered.std(axis=0)
    u = unique_variation(centered)
    scale = float(np.median(s))
    if scale <= 0.0:
        scale = 1.0

    q = simplex_tangent_basis(centered.shape[1])
    d = (centered @ q) / (np.sqrt(len(centered)) * scale)

    # **这里刻意不做逐列等尺度化**，与 ``design.condition_number`` 相反。
    # 两者问的问题不同：条件数问"各列之间是不是共线"，那是尺度无关的几何
    # 问题，所以必须先等尺度化；这里问"沿这个方向到底有多少信息"，而那正是
    # 一个有量纲的问题——把一个近乎死掉的方向归一到单位长度，恰好会把它要
    # 报的东西抹掉。改用一个**全局**标量（组内标准差的中位数）除，既保持
    # 各方向的相对量级，又让数值跨数据集可比。
    sv, vt = np.linalg.svd(d, full_matrices=False)[1:]
    weakest = q @ vt[-1]
    cond = float("inf") if sv[-1] <= 0 else float(sv[0] / sv[-1])

    return IdentifiabilityReport(
        dimensions=ALL_DIMENSIONS[: centered.shape[1]],
        within_sd=tuple(float(v) for v in s),
        unique_sd=tuple(float(v) for v in u),
        within_sd_relative=tuple(float(v / scale) for v in s),
        unique_sd_relative=tuple(float(v / scale) for v in u),
        tangent_singular_values=tuple(float(v) for v in sv),
        tangent_condition_number=cond,
        weakest_direction=tuple(float(v) for v in weakest),
    )
