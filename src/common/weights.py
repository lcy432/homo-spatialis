"""权重向量的两个标量摘要。P6。

两个函数,共五行数学,但它们各自服务三个地方:

    H_norm(w)              产品输出(§6.3.1)、M3 的难度轴、P10 分层的备选变量
    distance_from_center   P10 分层的推荐变量(硬规则 21)、以及 d_i 的事前代理

**分层变量不能用 `d_i`。** `d_i = ‖w_i − ŵ_center‖` 依赖混合 logit 估出来的
`ŵ_center`,那是跑完才有的东西,而抽样必须在跑之前完成。用到单纯形中心的
欧氏距离作代理:若模型不坍缩,`ŵ_center` 收敛到分布均值,而 Dirichlet 对称
情形下均值正是均匀向量 `1/K`。所以这个代理在"无坍缩"这一参照情形下与 `d_i`
重合,偏离程度恰好就是坍缩本身——用它分层不会把处理效应偷渡进分层。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

# 权重向量的维数。七维定和量表(§6.2),与 worlds.dimensions.ALL_DIMENSIONS 一致。
# 这里不 import 那个模块:本模块要能被 ui 与 eval 使用,不该拖进 M3 的依赖。
N_WEIGHTS = 7


def _as_vector(weights: Mapping[str, float] | Sequence[float]) -> list[float]:
    values = list(weights.values()) if isinstance(weights, Mapping) else list(weights)
    if not values:
        raise ValueError("权重向量为空")
    total = sum(values)
    if total <= 0:
        raise ValueError(f"权重之和必须为正,得到 {total!r}")
    if any(v < 0 for v in values):
        raise ValueError("权重不能为负")
    return values


def normalised_entropy(weights: Mapping[str, float] | Sequence[float]) -> float:
    """H_norm(w) = −Σ w_k·ln(w_k) / ln(K),落在 [0,1]。

    1 表示完全均匀(七维一样在乎),0 表示全部质量压在一维。

    约定 0·ln0 = 0(极限值)。这不是数值 hack:某一维权重为零时它对熵的贡献
    确实是零,不是未定义。

    为什么它是产品指标而不只是诊断量(§6.3.1):熵低意味着这个人有明确的
    主导偏好,于是城市之间对他的福利间隔大,个人化的收益也大;熵高意味着
    他对什么都差不多在乎,任何一座城市都还行,这时候系统应当如实告诉他
    "这几座城市对你差别不大"。它在用户填完量表的瞬间就能算,零成本。
    """
    values = _as_vector(weights)
    total = sum(values)
    entropy = -sum((v / total) * math.log(v / total) for v in values if v > 0)
    return entropy / math.log(len(values))


def distance_from_center(
    weights: Mapping[str, float] | Sequence[float], *, n: int | None = None
) -> float:
    """到单纯形中心 `1/K` 的欧氏距离。P10 的分层变量(硬规则 21)。

    上界是 `sqrt((K−1)/K)`(权重全压在一维时取到),K=7 时约 0.926。
    """
    values = _as_vector(weights)
    total = sum(values)
    k = n or len(values)
    return math.sqrt(sum((v / total - 1.0 / k) ** 2 for v in values))


def max_distance_from_center(n: int = N_WEIGHTS) -> float:
    """`distance_from_center` 的上界,用来把它归一到 [0,1] 做分箱。"""
    return math.sqrt((n - 1) / n)
