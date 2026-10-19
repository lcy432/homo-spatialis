"""目标储蓄（储蓄规则 e1-5b-savings-goals-v1，`DECISIONS/0026`）：目标只是记账上的提醒——同一本现金账、不圈钱、不自动购买；
每月月末拿现金和当前报价比；只有真正付清了对应的购买才算兑现；到了目标月份还没兑现就记逾期。

例子：在 K51 存钱买便利设备包（报价 1,440）。
"""

from __future__ import annotations

from econ.life_settlement import settle_year

from .conftest import CLONE_K, K51, base_items, live_year, once, tax_for, whole, year_action
from .settle_support import cents, revise

PACK = "k51.durable.convenience_pack"


def _goal(month=12, year=1, goal_id="goal.pack"):
    return {
        "action": "set",
        "goal_id": goal_id,
        "opportunity_id": PACK,
        "quantity": 1,
        "target_year": year,
        "target_month": month,
    }


def _settle(bundle, items, *, goal=None, state=None):
    state = state or bundle.initial_state(CLONE_K)
    return settle_year(
        protocol=bundle.protocol,
        state=state,
        action=year_action(state, items, savings_goal=goal),
        catalog=bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
    )


def _linked(month):
    return {**once(PACK, month), "goal_id": "goal.pack"}


def test_buying_the_item_in_the_target_month_realizes_the_goal(bundle):
    r = _settle(bundle, [*base_items(), _linked(12)], goal=_goal())
    real = r.outcome.savings.realization
    assert (real.month, real.on_time, real.evidence) == (12, True, "durable_purchase_executed")
    assert cents(real.amount_paid) == 144000
    (goal,) = r.next_state.savings_goals
    assert goal.status == "realized" and cents(goal.quote.total) == 144000


def test_a_purchase_not_linked_to_the_goal_does_not_count(bundle):
    """买了同样的东西、却没有在那一项写 goal_id：不替代理认定兑现，到期照样记"到期未兑现"。"""
    r = _settle(bundle, [*base_items(), once(PACK, 12)], goal=_goal())
    assert r.outcome.savings.realization is None
    assert [e.kind for e in r.outcome.savings.events] == ["created", "due_unrealized"]
    assert r.next_state.savings_goals[0].status == "overdue"


def test_a_goal_neither_earmarks_nor_spends_money(bundle):
    """只设目标、不买：现金与不设目标时逐分相同（14,640）；到了 6 月月末没有兑现，记逾期。"""
    plain = _settle(bundle, base_items())
    r = _settle(bundle, base_items(), goal=_goal(month=6))
    assert cents(r.outcome.cash_end) == cents(plain.outcome.cash_end) == 1464000
    assert r.outcome.savings.realization is None
    (goal,) = r.next_state.savings_goals
    assert goal.status == "overdue"
    assert [(e.kind, e.month) for e in goal.events] == [("created", None), ("due_unrealized", 6)]


def test_monthly_progress_compares_cash_with_the_current_quote(bundle):
    """现金 0、门店兼职每月净增 1,500 − 1,380 = 120：1 月月末现金 120，离报价 1,440 还差 1,320。"""
    poor = revise(bundle.initial_state(CLONE_K), cash={"amount": 0, "numeraire": "USD_2024_LOCAL"})
    items = [whole("k51.housing.far"), whole("k51.food.basic"), whole("k51.job.shop_parttime")]
    r = _settle(bundle, items, goal=_goal(), state=poor)
    start, first = r.outcome.savings.progress[:2]  # 第一行是年初
    assert (start.month, cents(start.cash)) == (None, 0)
    assert (first.month, cents(first.cash), cents(first.quote)) == (1, 12000, 144000)
    assert (cents(first.shortfall), first.cash_covers_quote) == (132000, False)


def test_only_one_open_goal_at_a_time_and_cancel_moves_no_money(bundle, scoring):
    y1 = live_year(
        bundle, scoring, bundle.initial_state(CLONE_K), base_items(), savings_goal=_goal(year=2)
    )
    assert [g.status for g in y1.next_state.savings_goals] == ["active"]
    second = live_year(
        bundle, scoring, y1.next_state, base_items(), savings_goal=_goal(year=2, goal_id="goal.two")
    )
    assert second.settlement.status == "input_error"
    cancel = live_year(
        bundle,
        scoring,
        y1.next_state,
        base_items(),
        savings_goal={"action": "cancel", "goal_id": "goal.pack"},
    )
    assert cancel.settlement.status == "completed"
    assert [g.status for g in cancel.next_state.savings_goals] == ["cancelled"]
    plain = live_year(bundle, scoring, y1.next_state, base_items())
    assert cents(cancel.settlement.outcome.cash_end) == cents(plain.settlement.outcome.cash_end)
