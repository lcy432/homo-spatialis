"""三年转移核（README §7.5）。

--------------------------------------------------------------------------
决定一：跨年一律用基年不变价
--------------------------------------------------------------------------

三个增长率——工资、房租、储蓄利率——**全部取实际增长率**，numeraire_id
全程恒定，不出现任何名义量。

不这么做的替代方案是"名义量演进，再逐年平减"，那样每一年都要引入一个
通胀假设，而通胀假设是一个我们既没有数据支撑、也不打算研究的自由参数。
更糟的是它会污染 numeraire：第三年的美元与第一年的美元不是同一个单位，
而 Money 的守卫会在跨年比较时正确地拒绝，于是人们会去关掉守卫。用实际
增长率则第三年与第一年在同一把尺子上，Money 的比较一路成立。

--------------------------------------------------------------------------
决定二：V1 不实现享乐适应
--------------------------------------------------------------------------

§7.5 原本写了"舒适度带来的效用按年衰减（享乐适应），收入不衰减"。V1
不实现它，理由是**不可识别**，不是嫌麻烦：

固定投放（代理不搬家）下，参照点无从定义。若取一个常数参照点，衰减因子
δ^t 就对所有城市施加同一个乘数，在 argmax 里完全抵消——它既不改变任何
排序，也不改变任何选择，因此从代理行为里回收不出来。实现一个观测上没有
后果的机制，只会增加一个无法标定的自由参数。

V2 迁移机制落地之后再加，届时用**相对参照点**的形式：

    A_eff(t) = A_ref + (A − A_ref) · δ^t

参照点 A_ref 取上一座城市的属性。这时衰减依赖"从哪儿搬来的"，因此在
城市之间不再抵消，可识别性回来了。这个形式还保留了 log 线性吗——不完全
保留，A_eff 是 A 的仿射函数，ln A_eff 不再是 ln A 的线性函数，所以 M5
的特征列届时要改成 ln A_eff。这一点必须在 V2 动手前想清楚，不要等写完
再发现 §11.7 的设定坏了。

**Agentopia §B.10 的存量衰减不能直接搬。** 那里衰减的是代理自己的满足度
存量（一个内生状态变量），我们的 A_k 是城市属性（一个外生的环境量）。
把外生量按年衰减在语义上是说"这座城市的空气一年比一年好/坏"，那是增长率
的事，不是适应的事。

这条已写进 README §12 已知边界。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from common.ids import CityId
from common.money import Money
from econ.types import CityRecord, Persona


@dataclass(frozen=True, slots=True)
class GrowthRates:
    """一座城市的三个实际增长率。全部是实际，不是名义。

    ``real_wage`` 按职业 × 都会区给出（§7.5：工资按职业与都会区的增长率
    演进），所以同一座城市对不同职业可以有不同的值——构造时按职业取好。
    ``real_interest`` 与城市无关，但放在一起，免得调用点从两个地方取参数。
    """

    real_wage: float
    real_rent: float
    real_interest: float

    def __post_init__(self) -> None:
        for name in ("real_wage", "real_rent", "real_interest"):
            v = getattr(self, name)
            if v <= -1.0:
                raise ValueError(f"{name} = {v}，增长率不能让存量变成负数")
            if abs(v) > 0.5:
                raise ValueError(
                    f"{name} = {v} 超出 ±50%。实际增长率不该有这个量级；"
                    "常见错误是误填了名义增长率或百分数（0.03 而不是 3）"
                )


@dataclass(frozen=True, slots=True)
class YearState:
    """一年结算之后的状态。轨迹就是这些的序列。"""

    year: int
    city: CityId
    gross_income: Money
    tax: Money
    net_income: Money
    saving: Money
    savings_stock: Money
    consumption_budget: Money
    ln_utility: float

    def __post_init__(self) -> None:
        # numeraire 恒定是决定一的可检验形式。若哪天有人引入了名义量再
        # 平减，最先炸的就是这里，而不是三个月后某张排序表。
        numeraires = {
            self.gross_income.numeraire,
            self.tax.numeraire,
            self.net_income.numeraire,
            self.saving.numeraire,
            self.savings_stock.numeraire,
            self.consumption_budget.numeraire,
        }
        if len(numeraires) != 1:
            raise ValueError(f"一年之内出现了多个 numeraire：{sorted(numeraires)}")


class TransitionKernel:
    """把一座城市推进若干年，并结算轨迹。

    它不改变效用函数，只改变喂给效用函数的城市记录。这是刻意的：真值的
    定义在 econ.utility，时间演进在这里，两者分开之后"三年后的效用"与
    "第一年的效用"用的是同一个函数，不存在两份实现分叉的余地。

    **无一般均衡**（§12）：代理的行为不改变房租与工资，二者按外生路径
    演化。这是设计上的限制而非待修复的缺陷；房租内生化的影响通过敏感性
    分析处理。
    """

    def __init__(
        self,
        growth: Mapping[str, GrowthRates],
        *,
        savings_rate: float = 0.0,
    ) -> None:
        if not 0.0 <= savings_rate < 1.0:
            raise ValueError(f"储蓄率必须落在 [0,1)，得到 {savings_rate!r}")
        self.growth = dict(growth)
        self.savings_rate = savings_rate

    def rates(self, city: CityId) -> GrowthRates:
        try:
            return self.growth[city.code]
        except KeyError:
            raise KeyError(f"没有 {city} 的增长率；已知：{sorted(self.growth)}") from None

    def advance(self, record: CityRecord, years: int) -> CityRecord:
        """把城市记录推进 ``years`` 年。

        工资与房租按各自的实际增长率复利。舒适度**不演进**：它们是城市
        属性，三年内当作不变，这与"不实现享乐适应"是同一个立场的两面——
        我们不假装知道城市属性的短期动态。
        """
        r = self.rates(record.city)
        wage_factor = (1.0 + r.real_wage) ** years
        rent_factor = (1.0 + r.real_rent) ** years
        return CityRecord(
            city=record.city,
            rpp={
                "rent": record.rpp["rent"] * rent_factor,
                "goods": record.rpp["goods"],
                "other": record.rpp["other"],
            },
            wage_by_quantile={q: w * wage_factor for q, w in record.wage_by_quantile.items()},
            amenities_scaled=record.amenities_scaled,
            jurisdiction=record.jurisdiction,
            variety_ln_price=record.variety_ln_price,
        )

    def run(
        self,
        core,  # WelfareCore；不加类型标注以避免与 econ.core 循环导入
        persona: Persona,
        city: CityId,
        *,
        years: int = 3,
        initial_savings: float = 0.0,
    ) -> list[YearState]:
        """跑一条固定投放的轨迹（代理不搬家）。

        储蓄的递推是纯算术，因此归环境（§3.2 的 L2）：

            savings_{t+1} = savings_t · (1 + i_real) + saving_t

        注意**房租不在这里单独扣**。它在 P 里，扣在消费预算的购买力上。
        若这里再扣一次现金流的房租，租金就被计了两遍——一次压低实际
        消费，一次压低储蓄——这正是 §7.4 要防的那种重复计入，只不过发生
        在跨期而不是同期。储蓄是从税后收入里按储蓄率划走的那一块，剩下
        的消费预算再由 P 平减，两条路互不重叠。
        """
        numeraire = core.numeraire
        rec0 = core.record(city)
        stock = initial_savings
        path: list[YearState] = []

        for t in range(years):
            stepped = core.with_record(city, self.advance(rec0, t))
            acc = stepped._income(persona, city, savings_rate=self.savings_rate)
            d = stepped.decompose(persona, city, savings_rate=self.savings_rate)

            r = self.rates(city)
            stock = stock * (1.0 + r.real_interest) + acc["saving"]

            path.append(
                YearState(
                    year=t,
                    city=city,
                    gross_income=Money(acc["gross_income"], numeraire),
                    tax=Money(acc["tax"], numeraire),
                    net_income=Money(acc["net_income"], numeraire),
                    saving=Money(acc["saving"], numeraire),
                    savings_stock=Money(stock, numeraire),
                    consumption_budget=Money(acc["consumption_budget"], numeraire),
                    ln_utility=d.ln_utility,
                )
            )
        return path
