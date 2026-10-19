"""生活协议的计费换算：报价 → 整数最小单位的年额与月额（E1-2 约定，`DECISIONS/0017`）。

这是**唯一**一份金额换算。结算（``econ.life_settlement``）用它记账，加载器
（``ledger.life_catalog``）用它在结算前比较"最低方案"与其他合格方案的月费——两边必须是
同一套算法，否则加载器认为一致的数据，结算时可能算出另一个数（`DECISIONS/0018` 第一节）。
放在 ``common`` 是为了让两边都能引用而不形成循环依赖；这里只有换算，没有任何经济判断。

约定：

* 金额一律是整数最小单位（``MoneyBasis.minor_unit_digits``，美元 = 分），精度细于最小
  单位的报价是数据错误，不舍入；
* 周期报价先换成年额：年价原样、月价 × 12、周价 × 52（每月 52/12 周，全年一致）；
* 年额按日历月用累计舍入摊开：第 m 月 = round(A·m/12) − round(A·(m−1)/12)，四舍五入
  （ROUND_HALF_UP），12 个月之和恰好等于 A。
"""

from __future__ import annotations

from decimal import Decimal

from common.life_contracts import (
    MONTHS_PER_YEAR,
    WEEKS_PER_YEAR,
    GoalQuote,
    LifeContractError,
    LifeOpportunity,
    PriceQuote,
    exact_minor_units,
)
from common.money import Money

_BILLING_PER_YEAR = {"per_year": 1, "per_month": MONTHS_PER_YEAR, "per_week": WEEKS_PER_YEAR}


def cumulative_share(annual_minor: int, months: int) -> int:
    """round(A × months / 12)，四舍五入，整数精确运算（A ≥ 0）。"""
    q, r = divmod(annual_minor * months, MONTHS_PER_YEAR)
    return q + (1 if 2 * r >= MONTHS_PER_YEAR else 0)


def monthly_installment(annual_minor: int, month: int) -> int:
    """年额 A 在日历第 ``month`` 月的份额。12 个月之和恰好是 A。"""
    return cumulative_share(annual_minor, month) - cumulative_share(annual_minor, month - 1)


def unit_minor(quote: PriceQuote, digits: int) -> int:
    """报价的单价（最小单位）。精度细于最小单位直接报错——目录构造时本该已拒绝。"""
    unit = exact_minor_units(quote.amount.amount, digits)
    if unit is None:
        raise LifeContractError(f"报价 {quote.amount.amount!r} 的精度细于最小记账单位")
    return unit


def annual_minor(quote: PriceQuote, digits: int) -> int:
    """周期报价的年额（最小单位）：年价原样、月价 × 12、周价 × 52。"""
    if quote.billing not in _BILLING_PER_YEAR:
        raise LifeContractError(f"{quote.billing} 不是周期计费，没有年额")
    return unit_minor(quote, digits) * _BILLING_PER_YEAR[quote.billing]


def monthly_charges(quote: PriceQuote, digits: int) -> tuple[int, ...]:
    """一份周期报价（数量 1）在日历 1–12 月各收多少（最小单位）。"""
    a = annual_minor(quote, digits)
    return tuple(monthly_installment(a, m) for m in range(1, MONTHS_PER_YEAR + 1))


def one_time_total_minor(quote: PriceQuote, quantity: int, digits: int) -> int:
    """一次性报价 × 数量（最小单位）。结算付一次性购买与学费用的就是"数量 × 单价"这一个算法。"""
    if quote.billing != "one_time":
        raise LifeContractError(f"{quote.billing} 不是一次性报价")
    return quantity * unit_minor(quote, digits)


def money_of_minor(minor: int, digits: int, numeraire: str) -> Money:
    """最小单位 → 金额（十进制换算，没有二进制误差）。"""
    return Money(float(Decimal(minor).scaleb(-digits)), numeraire)


def goal_quote(o: LifeOpportunity, quantity: int, year: int, digits: int) -> GoalQuote:
    """目标的报价依据（E1-5B）：这项机会当前的一次性报价 × 数量。是当时的资料，不是未来价格预测。

    调用前应已确认机会有一次性报价（``savings_goal_kind`` 不为空、可得）；否则直接报错——未知不补零。
    """
    price = o.price
    if price is None or price.billing != "one_time":
        raise LifeContractError(f"{o.opportunity_id}: 没有一次性报价，不能作为目标的报价依据")
    total = one_time_total_minor(price, quantity, digits)
    return GoalQuote(
        opportunity_id=o.opportunity_id,
        unit_price=price.amount,
        quantity=quantity,
        total=money_of_minor(total, digits, price.amount.numeraire),
        billing="one_time",
        per_unit=price.per_unit,
        quoted_year=year,
        source_kind=o.source.kind,
    )
