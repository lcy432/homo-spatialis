"""支出份额：行为代理题 → 份额三元组。实现 ExpenditureShareProvider 协议。

§6.4 的做法是**不直接问支出份额**。用户不知道自己的食品支出占比，但知道
自己有没有车。问可回答的，推不可回答的。

这里是 V1 的合成实现，M1 拿到真实 CEX 之后换掉，接口不变。数值全部是手工
设的，**不是从 CEX 估的**，函数名与常量名都标了 provisional。

--------------------------------------------------------------------------
参照收入必须与城市无关
--------------------------------------------------------------------------

查表用的收入是 Persona 上冻结的一个数，不是各城市的税后收入。这条是
架构修正一，理由写在 econ.types.Persona 的文档字符串里，简述：份额随
城市变会让 P 不再是价格指数、κ 跨城漂移、§11.7 的 logit 变成误设定，
而且与 Cobb-Douglas 位似的函数形式自相矛盾。

所以本模块的签名里**没有城市**。这不是遗漏，是防线。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from common.ids import DemographicCell
from common.providers import ExpenditureShares

# 全国基准份额。占位值，M1 换成 CEX 全国平均。
BASE_RENT = 0.33
BASE_GOODS = 0.31
BASE_OTHER = 0.36

# 恩格尔式的收入弹性：住房份额随收入下降。
#
# 这里有一处值得说明的张力。README 引 Handbury (2021) 作动机，那篇讲的
# 正是非位似；而 §7.1 的 Cobb-Douglas 是位似的——同一个人的份额不随收入
# 变。我们不改效用函数，而是让**份额随人变**：不同收入的人查到不同的
# 份额，于是非位似性通过份额的跨人变异进来了。跨期非位似留作 V2。
INCOME_ELASTICITY = -0.055
REFERENCE_INCOME = 82_000.0

# 行为代理题的调整量。加性，作用在归一化之前。
CAR_TO_GOODS = 0.035  # 汽油、保养、车辆购置都落在商品
RENTER_TO_RENT = 0.025
PER_EXTRA_PERSON_TO_GOODS = 0.020  # 多一口人，食品与日用品占比上升
DINING_OUT_TO_OTHER = {"rare": -0.020, "sometimes": 0.0, "often": 0.030}


@dataclass(frozen=True)
class BehaviouralAnswers:
    """§6.4 的行为代理题。问的都是用户答得上来的事实。

    ``reference_income`` 是**与城市无关**的那一个收入：产品模式取用户
    当前收入，研究模式取人设指定收入。它在 Persona 构造时用一次，之后
    份额就冻结了。
    """

    household_size: int = 1
    tenure: str = "rent"  # "rent" | "own"
    has_car: bool = True
    dining_out: str = "sometimes"  # "rare" | "sometimes" | "often"
    reference_income: float = REFERENCE_INCOME

    def __post_init__(self) -> None:
        if self.tenure not in ("rent", "own"):
            raise ValueError(f"tenure 必须是 'rent' 或 'own'，得到 {self.tenure!r}")
        if self.dining_out not in DINING_OUT_TO_OTHER:
            raise ValueError(f"dining_out 必须是 {sorted(DINING_OUT_TO_OTHER)} 之一")
        if self.household_size < 1:
            raise ValueError("household_size 至少为一")
        if self.reference_income <= 0:
            raise ValueError("参照收入必须为正")

    def cell(self, country: str = "US") -> DemographicCell:
        return DemographicCell(
            country=country,
            household_size=self.household_size,
            tenure=self.tenure,
            income_decile=None,
        )


class ProvisionalShareProvider:
    """V1 的合成份额表。**手工设定，不是从 CEX 估的。**

    实现 ExpenditureShareProvider 协议，所以 M1 的真实实现是原地替换。
    """

    method = "provisional_synthetic_v1"

    def shares_from_answers(self, answers: BehaviouralAnswers) -> ExpenditureShares:
        income_tilt = INCOME_ELASTICITY * math.log(answers.reference_income / REFERENCE_INCOME)

        s_rent = BASE_RENT + income_tilt
        s_goods = BASE_GOODS
        s_other = BASE_OTHER

        if answers.tenure == "rent":
            s_rent += RENTER_TO_RENT
        if answers.has_car:
            s_goods += CAR_TO_GOODS
        s_goods += PER_EXTRA_PERSON_TO_GOODS * (answers.household_size - 1)
        s_other += DINING_OUT_TO_OTHER[answers.dining_out]

        floor = 0.05
        s_rent, s_goods, s_other = (max(floor, v) for v in (s_rent, s_goods, s_other))
        # 显式归一化，不是防御性习惯：§7.2 把 s_rent 认作 Cobb-Douglas 的
        # 住房指数，份额不和为一等于悄悄改了效用函数的指数。
        return ExpenditureShares.from_raw(s_rent, s_goods, s_other)

    def shares(self, cell: DemographicCell) -> ExpenditureShares:
        """协议签名。只有人口学单元时按参照收入处理。"""
        return self.shares_from_answers(
            BehaviouralAnswers(
                household_size=cell.household_size,
                tenure=cell.tenure,
                reference_income=REFERENCE_INCOME,
            )
        )
