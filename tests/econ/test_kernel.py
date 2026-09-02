"""三年转移核：两个决定的可检验形式。"""

from __future__ import annotations

import pytest

from common.money import NumeraireMismatchError
from econ.kernel import GrowthRates, TransitionKernel


def test_the_numeraire_never_changes_across_the_path(core, persona, kernel, city_a):
    """决定一的核心后果：第三年的美元与第一年的是同一个单位。

    若有人改成"名义量演进再逐年平减"，Money 的守卫会正确地拒绝跨年比较，
    而那时的诱惑是关掉守卫。用实际增长率则比较一路成立。
    """
    path = kernel.run(core, persona, city_a, years=3)
    assert len(path) == 3
    numeraires = {y.net_income.numeraire for y in path}
    assert numeraires == {core.numeraire}
    # 跨年比较必须是合法的，而不是被守卫挡下
    assert path[2].savings_stock > path[0].savings_stock


def test_savings_compound_and_rent_is_not_deducted_twice(core, persona, kernel, city_a):
    """房租在 P 里，不在现金流里再扣一次。

    若这里再扣一遍现金流的房租，租金就被计了两遍：一次压低实际消费，
    一次压低储蓄。这是 §7.4 那种重复计入的跨期版本。
    """
    path = kernel.run(core, persona, city_a, years=3)
    for y in path:
        # 储蓄完全由储蓄率从税后收入划走，没有第二个扣项
        assert y.saving.amount == pytest.approx(y.net_income.amount * kernel.savings_rate)
        assert y.consumption_budget.amount == pytest.approx(y.net_income.amount - y.saving.amount)


def test_real_growth_moves_wages_and_rents(core, persona, kernel, city_a):
    rec = core.record(city_a)
    advanced = kernel.advance(rec, 2)
    r = kernel.rates(city_a)
    assert advanced.wage_by_quantile[50] == pytest.approx(
        rec.wage_by_quantile[50] * (1 + r.real_wage) ** 2
    )
    assert advanced.rpp["rent"] == pytest.approx(rec.rpp["rent"] * (1 + r.real_rent) ** 2)
    # 商品与服务不随房租增长率走
    assert advanced.rpp["goods"] == rec.rpp["goods"]


def test_amenities_do_not_decay(core, persona, kernel, city_a):
    """决定二：V1 不实现享乐适应。

    固定投放下参照点无从定义，常数参照点的 δ^t 对所有城市施加同一乘数，
    在 argmax 里完全抵消，因此不可识别。实现一个观测上没有后果的机制，
    只是多一个无法标定的自由参数。
    """
    rec = core.record(city_a)
    assert kernel.advance(rec, 3).amenities_scaled == rec.amenities_scaled


def test_utility_still_moves_across_years_even_without_adaptation(core, persona, kernel, city_a):
    """不衰减不等于不变：工资与房租按不同速率走，实际购买力就在变。"""
    path = kernel.run(core, persona, city_a, years=3)
    assert len({round(y.ln_utility, 12) for y in path}) == 3


def test_a_nominal_growth_rate_is_rejected(core):
    """最常见的填错：把 3% 填成 3 而不是 0.03。"""
    with pytest.raises(ValueError, match="超出 ±50%"):
        GrowthRates(real_wage=3.0, real_rent=0.01, real_interest=0.01)


def test_an_unknown_city_fails_loudly(core, persona, city_a):
    empty = TransitionKernel({}, savings_rate=0.1)
    with pytest.raises(KeyError, match="没有"):
        empty.run(core, persona, city_a, years=1)


def test_mixing_two_numeraires_in_one_year_is_refused():
    """YearState 自己守着决定一。"""
    from common.money import Money
    from econ.kernel import YearState

    with pytest.raises(ValueError, match="多个 numeraire"):
        YearState(
            year=0,
            city=None,
            gross_income=Money(1.0, "USD_2024_US100"),
            tax=Money(0.1, "CNY_2024_CN100"),
            net_income=Money(0.9, "USD_2024_US100"),
            saving=Money(0.0, "USD_2024_US100"),
            savings_stock=Money(0.0, "USD_2024_US100"),
            consumption_budget=Money(0.9, "USD_2024_US100"),
            ln_utility=0.0,
        )


def test_money_guard_still_bites_across_currencies():
    from common.money import Money

    with pytest.raises(NumeraireMismatchError):
        _ = Money(1.0, "USD_2024_US100") + Money(1.0, "CNY_2024_CN100")
