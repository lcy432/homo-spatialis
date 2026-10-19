"""年度结算（`DECISIONS/0017`、0020、0027）：钱怎样按月进出、税怎样预扣、付不起最低生活怎样出局。

全部用产品协议样例 K51 的数字，可以手算：

* 远处一居室 1,000/月，基本食品 380/月，最低生活合计 1,380/月；
* 全职文员年薪 30,000（每月 2,500），门店兼职年薪 18,000（每月 1,500）；
* 合成税：全年累计工资 12,000 以内 0%，12,000–30,000 的部分 10%，超过 30,000 的部分 20%，按累计工资逐月预扣；
* 开局现金 3,000。

金额一律换成整数分再比（``cents``），不用容差。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from econ.life_settlement import settle_year

from .conftest import CLONE_K, K51, base_items, tax_for, whole, year_action
from .settle_support import cents, flows, item, month_ends, revise


def _settle(
    bundle, items, *, state=None, catalog=None, protocol=None, fallback=("housing", "food")
):
    state = state or bundle.initial_state(CLONE_K)
    return settle_year(
        protocol=protocol or bundle.protocol,
        state=state,
        action=year_action(state, items, fallback=fallback),
        catalog=catalog or bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
    )


def _cash(bundle, amount):
    state = bundle.initial_state(CLONE_K)
    return revise(state, cash={"amount": amount, "numeraire": "USD_2024_LOCAL"})


# ---------------------------------------------------------------------- 按月记账与税


def test_the_monthly_ledger_and_withholding_by_hand(bundle):
    """1–4 月累计工资 ≤ 12,000，不扣税，每月净增 2,500 − 1,380 = 1,120；5 月累计 12,500，扣 (12,500 − 12,000) × 10% = 50；
    6–12 月每月再扣 2,500 × 10% = 250，净增 870。年末 3,000 + 4 × 1,120 + 1,070 + 7 × 870 = 14,640。"""
    r = _settle(bundle, base_items())
    assert r.status == "completed"
    taxes = [cents(m.income_tax) for m in r.outcome.months]
    assert taxes == [0, 0, 0, 0, 5000] + [25000] * 7
    assert sum(taxes) == 180000  # 全年 1,800 = (30,000 − 12,000) × 10%
    assert month_ends(r)[:5] == [412000, 524000, 636000, 748000, 855000]
    assert month_ends(r)[-1] == 1464000


def test_money_is_conserved_exactly(bundle):
    """年末 = 年初 + 全部入账 − 全部出账，精确到分；逐月也是。"""
    items = [
        whole("k51.housing.far"),
        whole("k51.food.basic", 1, 6),
        whole("k51.food.fresh", 7, 12),
        whole("k51.job.clerk"),
        whole("k51.service.gym"),
    ]
    r = _settle(bundle, items)
    out = r.outcome
    total = sum(cents(f.amount) for f in out.cash_flows)
    assert cents(out.cash_end) - cents(out.cash_start) == total
    for m in out.months:
        assert cents(m.cash_end) == (
            cents(m.cash_start) + cents(m.income_gross) - cents(m.income_tax) - cents(m.spent)
        )
    assert cents(out.months[0].cash_start) == cents(out.cash_start) == 300000


def test_wages_are_paid_only_for_planned_months(bundle):
    """文员只做 1–6 月：工资 6 × 2,500 = 15,000；累计到 5 月 12,500 超过 12,000，5 月扣 50、6 月扣 250，合计
    (15,000 − 12,000) × 10% = 300；7 月起没有工资，也不再扣。"""
    items = [whole("k51.housing.far"), whole("k51.food.basic"), whole("k51.job.clerk", 1, 6)]
    r = _settle(bundle, items)
    wages = flows(r, kind="income_gross")
    assert sum(cents(f.amount) for f in wages) == 1500000
    assert [cents(m.income_tax) for m in r.outcome.months] == [0] * 4 + [5000, 25000] + [0] * 6
    assert item(r, "k51.job.clerk").covered_months == tuple(range(1, 7))


def test_settlement_does_not_read_the_rationale_or_the_preference(bundle):
    """结算只读计划与目录：理由不同、偏好画像不同，结果一样。"""
    state = bundle.initial_state(CLONE_K)
    a = year_action(state, base_items())
    b = a.model_copy(update={"rationale": "完全不同的理由"})
    tax = tax_for(bundle, K51)
    cat = bundle.catalog_for(K51)
    ra = settle_year(protocol=bundle.protocol, state=state, action=a, catalog=cat, tax=tax)
    rb = settle_year(protocol=bundle.protocol, state=state, action=b, catalog=cat, tax=tax)
    assert ra.outcome == rb.outcome and ra.next_state == rb.next_state


# ---------------------------------------------------------------------- 最低生活、兜底与出局


def test_exactly_enough_passes_and_one_cent_less_exits(bundle):
    """没有工作：现金恰好 1,380 时 1 月通过（钱恰好够则通过），2 月出局，缺口是整整一个月的 1,380。"""
    items = [whole("k51.housing.far"), whole("k51.food.basic")]
    r = _settle(bundle, items, state=_cash(bundle, 1380))
    assert r.status == "exited" and r.outcome.settlement_status == "exited"
    ex = r.outcome.exit
    assert (ex.month, cents(ex.shortfall), cents(ex.cash_available)) == (2, 138000, 0)
    assert [m.minimum_check for m in r.outcome.months] == ["passed", "failed"]
    r = _settle(bundle, items, state=_cash(bundle, 1379.99))
    assert (r.outcome.exit.month, cents(r.outcome.exit.shortfall)) == (1, 1)


def test_an_unaffordable_plan_falls_back_to_the_city_minimum(bundle):
    """现金 0、门店兼职每月 1,500：计划的近处住房 1,400 + 新鲜食材 450 = 1,850 付不起，按声明退到最低生活
    （远处住房 1,000 + 基本食品 380），不出局。"""
    items = [whole("k51.housing.near"), whole("k51.food.fresh"), whole("k51.job.shop_parttime")]
    r = _settle(bundle, items, state=_cash(bundle, 0))
    assert r.status == "completed" and r.outcome.exit is None
    far = item(r, "k51.housing.far", fallback=True)
    assert far.status == "fallback_minimum" and 1 in far.covered_months
    assert 1 not in item(r, "k51.housing.near").covered_months


def test_without_a_declared_fallback_the_plan_is_infeasible_not_an_exit(bundle):
    """没有声明兜底：付不起计划的住房是计划问题（可以修订），不替代理改成便宜的那一套，也不是出局——最低生活其实付得起。"""
    items = [whole("k51.housing.near"), whole("k51.food.fresh"), whole("k51.job.shop_parttime")]
    r = _settle(bundle, items, state=_cash(bundle, 0), fallback=())
    assert r.status == "plan_infeasible" and r.outcome is None
    assert [p.code for p in r.problems] == ["housing_unaffordable"]


# ---------------------------------------------------------------------- 计划错误与资料错误分开


def test_plan_errors_are_input_errors_and_move_no_money(bundle):
    r = _settle(bundle, [*base_items(), whole("k51.nope")])
    assert r.status == "input_error" and r.outcome is None and r.next_state is None
    assert r.problems[0].code == "unknown_opportunity"
    r = _settle(bundle, [*base_items(), whole("k51.food.fresh", 7, 12)])
    assert r.status == "input_error" and [p.code for p in r.problems] == ["food_double_covered"]
    with pytest.raises(ValidationError):  # 同一项写两段：行动本身就不成立
        year_action(bundle.initial_state(CLONE_K), [*base_items(), whole("k51.job.clerk")])


def test_an_unknown_item_is_neither_free_nor_available(bundle):
    """计划用到资料未知的项目：每个月都拒绝（不是零价，也不是可得的），不付钱；其余计划照常执行。"""
    cat = bundle.catalog_for(K51)
    gym = cat.opportunity("k51.service.gym")
    data = cat.model_dump(mode="json")
    for o in data["opportunities"]:
        if o["opportunity_id"] == gym.opportunity_id:
            o.update(availability="unknown", price=None)
    unknown = type(cat).model_validate(data)
    r = _settle(bundle, [*base_items(), whole("k51.service.gym")], catalog=unknown)
    assert r.status == "completed"
    gym = item(r, "k51.service.gym")
    assert (gym.status, gym.covered_months, cents(gym.amount_paid)) == ("rejected", (), 0)
    assert [x.month for x in gym.rejections] == list(range(1, 13))
    assert gym.rejections[0].reason == "未知：没有数据，不当零价，也不当可得"
    assert cents(r.outcome.cash_end) == 1464000  # 与不写这一项的年末现金相同


def test_the_same_inputs_give_the_same_year(bundle):
    a = _settle(bundle, base_items())
    b = _settle(bundle, base_items())
    assert a.outcome == b.outcome and a.next_state == b.next_state
    assert cents(a.next_state.cash) == 1464000 and a.next_state.year_index == 2
