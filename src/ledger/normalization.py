"""把发布口径归一成模型口径。M1.5 的一小块，但位置很关键。

**RPP 以全美=100 发布，这里归一到全美=1。**

不是美观问题。ln P = Σ s_k·ln(rpp_k) 若用 100 制，ln P 会落在 4.6 附近，
于是设计矩阵里 ln_p 这一列有一个大常数偏移，而 ln_y_net 那一列在 11 左右。
条件 logit 的数值条件因此变差，而且两列的量级差异会让优化器在不同方向上
的步长严重失衡。归一到 1 制之后 ln P 落在 0 附近（典型都会区在 ±0.2 内），
条件数明显改善。

归一化必须在 ledger 做，不能在 M2 做：M2 对国家无知，而"发布时以什么为
100"是各国统计口径的一部分。别的国家若以别的基准发布，改的是这里。
"""

from __future__ import annotations

from collections.abc import Mapping

# BEA 的 RPP 以全美平均 = 100 发布。
BEA_RPP_BASE = 100.0


def normalize_rpp(value: float, *, base: float = BEA_RPP_BASE) -> float:
    """把一个以 base 为全国均值的指数归一到全国均值 = 1。"""
    if value <= 0.0:
        raise ValueError(f"价格指数必须为正，得到 {value!r}")
    if base <= 0.0:
        raise ValueError(f"基准必须为正，得到 {base!r}")
    return value / base


def normalize_rpp_row(row: Mapping[str, float], *, base: float = BEA_RPP_BASE) -> dict[str, float]:
    """归一一整行三分项，键名保持不变。"""
    return {k: normalize_rpp(v, base=base) for k, v in row.items()}
