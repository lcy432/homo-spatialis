"""偏好抽取：三种输入形式 → 一个 Persona。研究模式与产品模式共用这一层。

**这一层存在的全部理由是不让 Persona 有两种造法。** 研究模式合成一千个
假人，产品模式收一个真人的问卷；如果两条路各自拼 Persona，它们迟早会在
某个字段上分叉，而分叉之后"模型在真实用户身上的表现"与"模型在合成人设上
的表现"就不再可比——那正是研究模式存在的意义。

所以两条路都产出一个 Elicitation，再由同一个 build_persona() 变成 Persona。

--------------------------------------------------------------------------
三种输入形式，各管各的（§6.1）
--------------------------------------------------------------------------

判据是**这个输入是否关于权衡**。定和量表的全部力量来自强制权衡，用在没有
权衡结构的问题上只会制造噪音。

    定和量表    七个维度分一百分   → 价值权重 w        这里有权衡
    行为代理题  有没有车、几口人   → 支出份额 s        这里只有事实
    自由文本    可选，一段话       → 对 w 的**调整量**  兜前两者漏掉的

第三项有一条硬纪律：**只能调整，不能覆盖。** 定和量表是主，文本是辅。
下面把这句话实现成一个可检验的约束，而不是一句嘱咐——见 MAX_TEXT_SHIFT
与 build_persona 里的秩相关断言。

--------------------------------------------------------------------------
两处随机化都在这里记录
--------------------------------------------------------------------------

``presentation_order``  定和量表上维度的呈现顺序。§6.3 的顺序效应：首个
                        呈现的维度得分偏高，所以要随机化并记录置换。
``narration_order``     人设散文里维度的叙述顺序。这是数值扫描完全抓不到
                        的一条泄漏通道，见 common.leakcheck 的
                        narration_order_leakage。

两者是不同的东西，不要合并：前者防的是问卷的测量偏差（作用在真实用户
身上），后者防的是题面泄漏（作用在合成人设身上）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from common.ids import OccupationId
from common.providers import ExpenditureShares
from econ.types import LiteralnessTier, Persona, PersonaBrief, PersonaParams
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS, MATERIAL
from worlds.shares import BehaviouralAnswers, ProvisionalShareProvider

# 自由文本对单个维度权重的最大改动量（归一化前，以"分"为单位，满分一百）。
#
# 这个上限就是"只能调整、不能覆盖"的实现。没有它，职能 A 抽出来的调整量
# 可以把量表结果整个盖掉，于是问卷白填了，而且用户不会知道。
MAX_TEXT_SHIFT = 8.0

# 调整前后权重的秩相关下限。上限管幅度，这条管**结构**：即使每一维的改动
# 都在上限内，若它们协同起来把维度排序整个翻转，那仍然是覆盖而不是调整。
MIN_RANK_AGREEMENT = 0.80


class OverrideError(ValueError):
    """自由文本试图覆盖而不是调整定和量表的结果。"""


@dataclass(frozen=True)
class ConstantSumResponse:
    """定和量表：在七个维度间分配一百分（§6.3）。

    选定和量表而不是评分量表，是因为评分量表有**尺度使用偏差**：有人用
    满量程，有人挤在中段，且这一倾向在跨文化上系统不同（东亚偏中点、
    北美偏极端）。本项目正要做跨文化对比，那个偏差会直接混进结果。定和
    量表强制权衡，消灭了"每一项都很重要"，也就不存在尺度使用偏差。
    """

    points: Mapping[str, float]
    presentation_order: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        missing = set(ALL_DIMENSIONS) - set(self.points)
        extra = set(self.points) - set(ALL_DIMENSIONS)
        if missing or extra:
            raise ValueError(f"定和量表必须正好覆盖七维；缺 {sorted(missing)}，多 {sorted(extra)}")
        if any(v < 0 for v in self.points.values()):
            raise ValueError("分配的分数不能为负")
        if sum(self.points.values()) <= 0:
            raise ValueError("总分必须为正")
        if self.presentation_order and set(self.presentation_order) != set(ALL_DIMENSIONS):
            raise ValueError("呈现顺序必须是七维的一个置换")

    def weights(self) -> dict[str, float]:
        """归一化成和为一的权重向量。"""
        total = sum(self.points.values())
        return {d: self.points[d] / total for d in ALL_DIMENSIONS}


@dataclass(frozen=True)
class FreeTextResponse:
    """可选的一段自我描述，交给职能 A 抽成调整量（§8.2 第三步）。

    ``adjustments`` 是**已经抽好**的结果，以"分"为单位、可正可负。本模块
    不调用语言模型；抽取发生在 M4，这里只负责校验它没有越权。这个分工是
    刻意的：越权检查必须在一个不依赖模型输出的地方做。
    """

    text: str
    adjustments: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        extra = set(self.adjustments) - set(ALL_DIMENSIONS)
        if extra:
            raise ValueError(f"调整量出现了未知维度 {sorted(extra)}")
        for dim, delta in self.adjustments.items():
            if abs(delta) > MAX_TEXT_SHIFT:
                raise OverrideError(
                    f"自由文本想把 {dim!r} 改动 {delta:+.1f} 分，超出上限 "
                    f"±{MAX_TEXT_SHIFT}。文本只能调整、不能覆盖量表（§6.1）"
                )


@dataclass(frozen=True)
class Elicitation:
    """一次完整的偏好抽取。研究模式合成它，产品模式收集它。

    ``occupation`` 与 ``ability_quantile`` 走选择器而不是开放文本（§6.1）：
    开放文本需要一个分类器，而分类器就是语言模型，会退回到"模型决定数字
    是什么"的问题。
    """

    constant_sum: ConstantSumResponse
    behavioural: BehaviouralAnswers
    occupation: OccupationId
    ability_quantile: int = 50
    free_text: FreeTextResponse | None = None
    variety: Mapping[str, str] = field(default_factory=dict)
    narration_order: tuple[str, ...] = ()

    def adjusted_weights(self) -> dict[str, float]:
        """量表结果叠加文本调整，再归一化。"""
        points = dict(self.constant_sum.points)
        if self.free_text is not None:
            for dim, delta in self.free_text.adjustments.items():
                points[dim] = max(0.0, points[dim] + delta)
        total = sum(points.values())
        if total <= 0:
            raise ValueError("调整之后总分为零")
        return {d: points[d] / total for d in ALL_DIMENSIONS}


def _rank_agreement(before: Mapping[str, float], after: Mapping[str, float]) -> float:
    """两组权重的 Spearman 秩相关。用来判定"调整"有没有变成"覆盖"。

    退化情形要显式处理：若调整前的权重完全等权，就不存在需要被保住的
    排序，秩相关无定义（scipy 返回 nan）。此时返回 1.0——没有秩序可破坏，
    也就谈不上覆盖。

    不显式处理的话，nan 会让 ``nan < 阈值`` 恒为 False 而"通过"检查。它
    这次碰巧是对的方向，但那是运气：把比较写反就变成恒不通过，而两种
    情形都不会有任何提示。让 nan 参与安全判定，本身就是个坑。
    """
    from scipy import stats

    dims = list(ALL_DIMENSIONS)
    x = [before[d] for d in dims]
    y = [after[d] for d in dims]
    if len(set(x)) == 1 or len(set(y)) == 1:
        return 1.0

    rho = float(stats.spearmanr(x, y).statistic)
    return 1.0 if math.isnan(rho) else rho


def build_persona(
    elicitation: Elicitation,
    persona_id: str,
    *,
    narrative: str = "",
    literalness: LiteralnessTier = LiteralnessTier.IMPLICIT,
    share_provider: ProvisionalShareProvider | None = None,
) -> Persona:
    """把一次抽取变成一个 Persona。**研究模式与产品模式唯一的构造入口。**

    两条通道在这里汇合（§7.2）：

        通道一（行为）  行为代理题 → 份额 s → 决定 α 与 β 的**比值**
        通道二（陈述）  定和量表   → w₁    → 决定 α 与 β 的**总量**

    方向不能反。支出份额由行为揭示，定和量表问的是"你多在乎"；两类参数
    来自两个不同的信息源，反过来接也能跑通，但那推翻 §7.2。

    ``literalness`` 目前固定为 IMPLICIT。字面程度作为实验变量已暂时冻结，
    字段保留是为了将来解冻时不用改签名——README §11.5 的 3×3 实验表因此
    在 V1 塌成 3×1。
    """
    provider = share_provider or ProvisionalShareProvider()
    shares: ExpenditureShares = provider.shares_from_answers(elicitation.behavioural)

    base = elicitation.constant_sum.weights()
    weights = elicitation.adjusted_weights()

    if elicitation.free_text is not None and elicitation.free_text.adjustments:
        agreement = _rank_agreement(base, weights)
        if agreement < MIN_RANK_AGREEMENT:
            raise OverrideError(
                f"文本调整把维度排序改动过大（Spearman ρ = {agreement:.2f} < "
                f"{MIN_RANK_AGREEMENT}）。每一维的改动都在上限内，但合起来构成了覆盖。"
                "量表是主，文本是辅（§6.1）"
            )

    w_material = weights[MATERIAL]
    params = PersonaParams(
        alpha=w_material * (shares.s_goods + shares.s_other),
        beta=w_material * shares.s_rent,
        theta={d: weights[d] for d in AMENITY_DIMENSIONS},
    )

    brief = PersonaBrief(
        persona_id=persona_id,
        literalness=literalness,
        narrative=narrative,
        dimension_order=elicitation.narration_order,
    )

    return Persona(
        persona_id=persona_id,
        params=params,
        shares=shares,
        occupation=elicitation.occupation,
        ability_quantile=elicitation.ability_quantile,
        brief=brief,
        variety=dict(elicitation.variety),
    )
