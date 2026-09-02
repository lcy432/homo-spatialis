"""合成世界的税制。实现 common.providers.TaxFunction 协议。

为什么必须是累进的，而不是随便一个平税：Albouy (2009) 的效应——名义工资
高于平均的城市，其他条件相同的劳动者要多交约 30% 的联邦税——**只在累进
税下存在**。平税下税负与工资严格成比例，高工资城市不承担额外税负，
§11.3 第四档（对抗型，检验货币幻觉）就少了一个主要的构造来源。

州税结构的多样性也不是装饰。§5.7 点名"州所得税结构差异（平税、累进、
无税、地方附加）不是一个参数能表达的"。这里造四种原型，让合成世界里
存在"名义工资低但无州税因此实际更优"这种真实存在的情形。

这一层是**注入**给 WelfareCore 的，不是内建的。M2 不知道它拿到的是美国
式税制还是别国的，只知道它是 (Money, Jurisdiction, year) -> Money。
M1 落地后由 CountryPack 提供真实税表，本文件只服务合成世界。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from common.ids import Jurisdiction
from common.money import Money

# 联邦累进税级（单身申报，量级取自 2024 年的形状）。(上界, 边际税率)。
FEDERAL_BRACKETS: tuple[tuple[float, float], ...] = (
    (11_600.0, 0.10),
    (47_150.0, 0.12),
    (100_525.0, 0.22),
    (191_950.0, 0.24),
    (243_725.0, 0.32),
    (609_350.0, 0.35),
    (float("inf"), 0.37),
)

FEDERAL_STANDARD_DEDUCTION = 14_600.0


class StateRegime(Enum):
    """四种州税原型。§5.7：这四种不是一个参数能表达的。"""

    NONE = "none"  # 无州所得税
    FLAT = "flat"  # 平税
    PROGRESSIVE = "progressive"  # 累进
    PROGRESSIVE_LOCAL = "progressive_local"  # 累进加地方附加


STATE_RULES: dict[StateRegime, tuple[tuple[float, float], ...]] = {
    StateRegime.NONE: (),
    StateRegime.FLAT: ((float("inf"), 0.0495),),
    StateRegime.PROGRESSIVE: (
        (25_000.0, 0.020),
        (100_000.0, 0.055),
        (float("inf"), 0.085),
    ),
    StateRegime.PROGRESSIVE_LOCAL: (
        (25_000.0, 0.030),
        (100_000.0, 0.065),
        (float("inf"), 0.103),
    ),
}

# 地方附加，只有 PROGRESSIVE_LOCAL 有。按毛收入计，不分级。
LOCAL_SURCHARGE = 0.035


def _bracket_tax(taxable: float, brackets: tuple[tuple[float, float], ...]) -> float:
    """分级累进。注意是**边际**税率逐级叠加，不是找到档位后整体乘。

    整体乘是最常见的写错方式，而且它不会报错：结果只是偏高，且在档位
    边界上出现不连续的跳变。有一条测试盯着连续性。
    """
    owed = 0.0
    lower = 0.0
    for upper, rate in brackets:
        if taxable <= lower:
            break
        owed += (min(taxable, upper) - lower) * rate
        lower = upper
    return owed


@dataclass(frozen=True)
class SyntheticProgressiveTax:
    """联邦累进 + 州制度 + 可选地方附加。

    ``regimes`` 把辖区路径的第二段（州）映射到税制原型。路径只有一段
    （只有国家）时按无州税处理——这条分支存在是为了让别国的一段式辖区
    走同一个签名，而不是为美国特设。
    """

    regimes: dict[str, StateRegime]
    standard_deduction: float = FEDERAL_STANDARD_DEDUCTION

    def tax(self, gross: Money, jurisdiction: Jurisdiction, year: int) -> Money:
        taxable = max(0.0, gross.amount - self.standard_deduction)
        owed = _bracket_tax(taxable, FEDERAL_BRACKETS)

        if len(jurisdiction.path) >= 2:
            regime = self.regimes.get(jurisdiction.path[1], StateRegime.NONE)
            owed += _bracket_tax(taxable, STATE_RULES[regime])
            if regime is StateRegime.PROGRESSIVE_LOCAL:
                owed += gross.amount * LOCAL_SURCHARGE

        return Money(owed, gross.numeraire)

    def average_rate(self, gross: float, jurisdiction: Jurisdiction, numeraire: str) -> float:
        """平均税率。诊断用，也是检验累进性的抓手：它必须随收入单调上升。"""
        if gross <= 0:
            raise ValueError("毛收入必须为正")
        return self.tax(Money(gross, numeraire), jurisdiction, 0).amount / gross
