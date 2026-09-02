"""可贸易品的品种调整：现在就建通道，V1 不填内容（README §6.4）。

为什么现在就要有这个接口，而不是等 V2 再加：新增一个影响价格的输入，
意味着问卷、权重向量、城市属性表、prompt 模板、Observable 构造、评估层
设计矩阵六处同时改。这种横穿整条信息流的改动，做在有测试覆盖之前是一天
的事，做在系统跑起来之后是一周加若干静默 bug。所以字段全程存在、被序列
化、进 prompt、进设计矩阵，只是数值恒定。

它调整的是**类别指数内部的品种构成**，不是新增一个类别。同一款工业品在
中美的相对价格可以是反号的（关税、本地生产、进口结构所致），这正是
Handbury (2021) 指出的"商品组合差异"那一维。V1 在美国国内不处理这一层，
因为可贸易品的跨市场价差在国内可以忽略；跨国不可忽略。

数值上它是 ln P 里的一个加项：

    ln P = Σ_k s_k · (ln rpp_k + ln_adj_k)

对数空间相加，等价于水平空间相乘一个品种因子。V1 的实现恒返回 0.0，
于是 ln P 退化成 §5.2 那个纯粹的加权几何平均。

**一个例外值得记住**：汽油价格在美国州际差异不小（加州与得州可差三成
以上），所以"是否开车"这一题从第一天起就有实际信息量。它当前走的是
s_goods 的份额调整而不是这里的品种调整，但如果哪天要把汽油单独拎出来
做标志商品，入口就是这里。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from common.ids import CityId

# §5.2 的三个类别。品种调整按类别给出，因为它调的是类别内部的构成。
CATEGORIES = ("rent", "goods", "other")


@runtime_checkable
class VarietyAdjustment(Protocol):
    """给定城市与这个人的品种偏好，返回某一类别价格的对数调整量。

    返回 0.0 表示"这个人在这座城市面对的类别价格，就是类别指数本身"。
    """

    @property
    def method(self) -> str:
        """写进结果元数据。主结果与稳健性对照用不同的实现时，靠它区分。"""
        ...

    def ln_adjustment(self, category: str, city: CityId, variety: Mapping[str, str]) -> float: ...


class NullVarietyAdjustment:
    """V1 的实现：恒为零，全美同值。

    这不是占位符，它是一个有意义的对照组。全美同值意味着 §6.4 那三列
    方差为零；若模型的选择与零方差列相关，说明估计层在读噪声。这是一个
    不花额外成本的泄漏与虚假信号对照。
    """

    @property
    def method(self) -> str:
        return "null_v1"

    def ln_adjustment(self, category: str, city: CityId, variety: Mapping[str, str]) -> float:
        if category not in CATEGORIES:
            raise ValueError(f"未知类别 {category!r}，应为 {CATEGORIES} 之一")
        return 0.0


NULL_VARIETY = NullVarietyAdjustment()
