"""E1-2 收尾（`DECISIONS/0018` 第一节）：最低方案与合格目录的矛盾是数据错误，不是出局。

审核复现的情形：最低住房声明 900、最低食品 400，但目录里另一套**已被认定合格、可得**的
住房只要 500。分身现金 1,000，选 500 的住房 + 400 的食品——这个月明明过得下去（900 ≤
1,000），旧代码却按 900 + 400 = 1,300 的门槛判它第 1 月出局。错在数据声明自相矛盾，
不在分身；所以修复是让加载与结算把它报成数据错误，而不是替分身去找更便宜的房子。

比较用可比口径：数量都是 1、同一个日历月、同一套月度计费换算（按周计价按每月 52/12 周）。
不拿不同规格的原始标价直接比。
"""

from __future__ import annotations

import copy

from ledger.life_catalog import cross_validate

from .conftest import catalog, opportunity
from .settle_support import bundle_from, plan_of, revise, settle, state_of, with_items

USD = "USD_2024_LOCAL"


def _add_qualifying_home(raw: dict, *, price: float, billing: str = "per_month", **extra) -> None:
    """在 A11 加一套合格住房（规格照抄单间，只换 ID、标价与计费周期）。"""
    room = copy.deepcopy(opportunity(raw, "a11.housing.studio"))
    room.update(opportunity_id="a11.housing.room", label="合租单间", **extra)
    room["price"] = {
        "amount": {"amount": price, "numeraire": USD},
        "billing": billing,
        "per_unit": "dwelling-month" if billing == "per_month" else "dwelling-week",
    }
    a11 = catalog(raw, "A11")
    a11["opportunities"].append(room)
    a11["minimum_living"][0]["qualifying_opportunity_ids"].append("a11.housing.room")


def _cash_1000_plan(b):
    s = revise(state_of(b, "u02.a11.c0"), cash={"amount": 1000, "numeraire": USD})
    plan = with_items(
        plan_of(b, "u02.a11.c0"),
        {"opportunity_id": "a11.housing.room", "quantity": 1, "start_month": 1, "end_month": 12},
        {"opportunity_id": "a11.food.basic", "quantity": 1, "start_month": 1, "end_month": 12},
        minimum_fallback=[],
    )
    return s, plan


def test_the_audited_contradiction_is_a_data_error_not_an_exit(raw2):
    """审核复现：修复前加载检查通过、第 1 月按 1,300 门槛出局。"""
    _add_qualifying_home(raw2, price=500)
    b = bundle_from(raw2)
    s, plan = _cash_1000_plan(b)
    r = settle(b, "u02.a11.c0", state=s, plan=plan)
    assert r.status == "data_error", f"不能把数据矛盾记成分身失败，得到 {r.status}"
    assert r.outcome is None and r.next_state is None
    assert any("矛盾" in p.message and "a11.housing.room" in p.message for p in r.problems)
    report = cross_validate(b)
    assert report.contract_valid and not report.settlement_ready
    assert any("矛盾" in p for p in report.settlement_problems)


def test_monthly_charges_are_compared_not_raw_quotes(raw2):
    """周价 207 × 52 / 12 = 每月 897 < 900：矛盾。周价 208 → 每月 901.33/901.34 > 900：不矛盾。

    原始标价 207 和 900 不能直接比——它们的计费周期不同。
    """
    cheap = copy.deepcopy(raw2)
    _add_qualifying_home(cheap, price=207, billing="per_week")
    problems = cross_validate(bundle_from(cheap)).settlement_problems
    assert any("矛盾" in p for p in problems)

    dear = copy.deepcopy(raw2)
    _add_qualifying_home(dear, price=208, billing="per_week")
    report = cross_validate(bundle_from(dear))
    assert report.settlement_ready, report.settlement_problems


def test_a_conditional_discount_on_a_qualifying_home_is_rejected_as_unsupported(raw2):
    """只对某些人开放的更便宜合格项＝"因人而异的最低"：本轮最低方案表达不了，明确拒绝。"""
    _add_qualifying_home(raw2, price=500, eligibility=["occupation:SOC2018:41-2031"])
    problems = cross_validate(bundle_from(raw2)).settlement_problems
    assert any("资格" in p and "a11.housing.room" in p for p in problems)


def test_dearer_or_unavailable_qualifying_options_are_not_contradictions(bundle2):
    """两居室 1,400 比最低 900 贵、配送套餐确定缺货：都不构成矛盾，样例照常可结算。"""
    report = cross_validate(bundle2)
    assert report.settlement_ready, report.settlement_problems


def test_a_qualifying_home_that_merely_matches_the_minimum_is_fine(raw2):
    """同价（每月都是 900）不是"更便宜"，不报矛盾；恰好够最低生活费的边界在 E1-2 测试里另有覆盖。"""
    _add_qualifying_home(raw2, price=900)
    report = cross_validate(bundle_from(raw2))
    assert report.settlement_ready, report.settlement_problems
