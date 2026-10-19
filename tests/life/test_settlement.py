"""E1-2 月度结算（`DECISIONS/0017`）：验收 1–12。

每条测试盯一种具体的错账：用未来工资提前消费、半年服务收全年钱、最低生活检查被当成
第二笔扣款、自选项付不起就宣布出局、缺数据当成出局、出局后还在发工资、年中买的东西
平白少一年寿命……数字全部来自合成样例，可以手算（`configs/life/synthetic_e1_2.yaml`
文末）。金额一律换成整数分比较，没有容差。
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from agentlayer.life_views import ObservationPermission, build_observation, visible_feedback
from common.life_contracts import LifeContractError
from econ.life_settlement import settle_plans
from ledger.life_catalog import cross_validate, load_life_bundle

from .conftest import catalog, opportunity
from .settle_support import (
    bundle_from,
    catalog_of,
    cents,
    flows,
    item,
    month_ends,
    plan_of,
    plus_items,
    protocol_with_years,
    revise,
    settle,
    state_of,
    tax_of,
    with_items,
)

USD = "USD_2024_LOCAL"
PRODUCT = dict(
    condition="product_stated_profile",
    show_stated_dimension_points=True,
    show_soft_preferences=True,
    show_hard_requirements=True,
    show_free_text=True,
    show_energy_level=False,
    show_stated_material_points=True,
    permission_version="e1-2-draft",
)


def _results_for(bundle, clone_id):
    s = state_of(bundle, clone_id)
    cat = catalog_of(bundle, s)
    return settle_plans(
        protocol=bundle.protocol,
        state=s,
        plans=bundle.plans_for(clone_id),
        catalog=cat,
        tax=tax_of(bundle, cat),
    )


def _all_results(bundle):
    out = []
    for s in bundle.initial_states:
        if bundle.plans_for(s.clone_id):
            out.extend(_results_for(bundle, s.clone_id))
    return out


def _items(plan):
    return [i.model_dump(mode="json", exclude_none=True) for i in plan.items]


def _observe(bundle, state):
    profile = next(p for p in bundle.stated_profiles if p.person_id == state.person_id)
    return build_observation(
        permission=ObservationPermission(**PRODUCT),
        profile=profile,
        state=state,
        catalog=catalog_of(bundle, state),
    )


# --------------------------------------------------------------------------
# 1. 先攒钱再买：年初买不起的东西，攒够几个月工资之后买得起；不能动用未来工资
# --------------------------------------------------------------------------


def test_an_item_unaffordable_at_year_start_is_bought_after_two_months_of_wages(bundle2):
    assert cents(state_of(bundle2, "u01.a11.c0").cash) == 500_00  # 买不起 3,000 的电脑
    r = settle(bundle2, "u01.a11.c0")
    assert r.status == "completed"
    computer = item(r, "a11.durable.computer")
    assert computer.status == "executed" and computer.covered_months == (2,)
    m2 = r.outcome.months[1]
    assert cents(m2.cash_start) + cents(m2.income_gross) == 5_200_00
    assert cents(m2.cash_end) == 900_00
    expected = (2200, 900, 2600, 4300, 5400, 6350, 5770, 6870, 7970, 9070, 10170, 11270)
    assert month_ends(r) == [x * 100 for x in expected]


def test_wages_not_yet_earned_cannot_be_spent(bundle2):
    """电脑挪到第 1 月：那时只入账了一个月工资，买不起，被拒且**不顺延**到以后的月份。"""
    plan = plan_of(bundle2, "u01.a11.c0")
    items = _items(plan)
    items[3] = {**items[3], "month": 1}
    r = settle(bundle2, "u01.a11.c0", plan=with_items(plan, *items))
    assert r.status == "completed"
    computer = item(r, "a11.durable.computer")
    assert computer.status == "rejected" and computer.covered_months == ()
    assert [x.month for x in computer.rejections] == [1]
    assert "资金不足" in computer.rejections[0].reason
    assert not flows(r, oid="a11.durable.computer")
    assert cents(r.outcome.cash_end) == 14_270_00
    assert all(h.opportunity_id != "a11.durable.computer" for h in r.next_state.holdings)


def test_cash_never_goes_negative(bundle2):
    """没有透支：每一个月末的现金都不为负。"""
    for r in _all_results(bundle2):
        for m in r.outcome.months:
            assert cents(m.cash_end) >= 0


# --------------------------------------------------------------------------
# 2. 守恒：期末 − 期初 = 实际现金流合计（每月、每年，精确等式）
# --------------------------------------------------------------------------


def test_every_month_and_every_year_balances_exactly(bundle2):
    results = _all_results(bundle2)
    assert len(results) == 7, "样例里 7 份计划都应得到结算结果"
    for r in results:
        out = r.outcome
        total = sum(cents(f.amount) for f in out.cash_flows)
        assert cents(out.cash_end) - cents(out.cash_start) == total
        for m in out.months:
            in_month = sum(cents(f.amount) for f in out.cash_flows if f.month == m.month)
            assert cents(m.cash_end) - cents(m.cash_start) == in_month
            assert cents(m.cash_end) == (
                cents(m.cash_start) + cents(m.income_gross) - cents(m.income_tax) - cents(m.spent)
            )


def test_the_year_end_cash_is_the_next_years_opening_cash(bundle2):
    rs = _results_for(bundle2, "u01.a11.c0")
    assert [r.next_state.year_index for r in rs] == [2, 3, 4]
    for a, b in itertools.pairwise(rs):
        assert cents(b.outcome.cash_start) == cents(a.outcome.cash_end) == cents(a.next_state.cash)
    assert [cents(r.outcome.cash_end) for r in rs] == [11_270_00, 26_870_00, 42_470_00]


# --------------------------------------------------------------------------
# 3. 改行动，账目按规则变；只改理由，什么都不变
# --------------------------------------------------------------------------


def test_changing_only_the_rationale_changes_nothing(bundle2):
    plan = plan_of(bundle2, "u01.a11.c0")
    other = revise(plan, rationale="完全不同的理由：我要拼命省钱，什么都不想买。")
    assert plan != other
    a = settle(bundle2, "u01.a11.c0", plan=plan)
    b = settle(bundle2, "u01.a11.c0", plan=other)
    assert a.model_dump() == b.model_dump()


def test_dropping_one_action_changes_the_books_by_exactly_its_price(bundle2):
    plan = plan_of(bundle2, "u01.a11.c0")
    items = [i for i in _items(plan) if i["opportunity_id"] != "a11.experience.concert"]
    base = settle(bundle2, "u01.a11.c0")
    r = settle(bundle2, "u01.a11.c0", plan=with_items(plan, *items))
    assert cents(r.outcome.cash_end) - cents(base.outcome.cash_end) == 150_00
    assert month_ends(r)[:5] == month_ends(base)[:5], "第 6 月之前的账一分不差"


def test_switching_to_a_dearer_food_plan_costs_exactly_the_monthly_difference(bundle2):
    plan = plan_of(bundle2, "u01.a11.c0")
    items = _items(plan)
    items[1] = {**items[1], "opportunity_id": "a11.food.fresh"}
    r = settle(bundle2, "u01.a11.c0", plan=with_items(plan, *items))
    base = settle(bundle2, "u01.a11.c0")
    assert cents(base.outcome.cash_end) - cents(r.outcome.cash_end) == 12 * (520_00 - 400_00)


# --------------------------------------------------------------------------
# 4. 确定性重放；输入对象不被修改
# --------------------------------------------------------------------------


def test_replay_is_identical_and_inputs_are_not_mutated(bundle2):
    s = state_of(bundle2, "u01.a11.c0")
    plan = plan_of(bundle2, "u01.a11.c0")
    cat = catalog_of(bundle2, s)
    before = (s.model_dump(), plan.model_dump(), cat.model_dump())
    a = settle(bundle2, "u01.a11.c0")
    b = settle(bundle2, "u01.a11.c0")
    # 结果里没有墙钟时间之类的字段（下一条测试盯着），整份结果可以直接比。
    assert a.model_dump() == b.model_dump()
    assert (s.model_dump(), plan.model_dump(), cat.model_dump()) == before


def test_results_carry_no_wall_clock_field():
    from common.life_contracts import EnvironmentOutcome, MonthRecord, SettlementResult

    for model in (EnvironmentOutcome, MonthRecord, SettlementResult):
        for name in model.model_fields:
            assert not any(
                w in name for w in ("timestamp", "elapsed", "wall", "created", "duration_ms")
            ), (model.__name__, name)


# --------------------------------------------------------------------------
# 5. 计费与服务期一致；一次性购买不重复收费
# --------------------------------------------------------------------------


def test_a_six_month_service_is_charged_and_covered_for_six_months(bundle2):
    plan = plus_items(
        plan_of(bundle2, "u01.a11.c0"),
        {"opportunity_id": "a11.service.cowork", "quantity": 1, "start_month": 1, "end_month": 6},
    )
    r = settle(bundle2, "u01.a11.c0", plan=plan)
    cowork = item(r, "a11.service.cowork")
    assert cowork.covered_months == (1, 2, 3, 4, 5, 6)
    assert cents(cowork.amount_paid) == 6 * 220_00
    assert [f.month for f in flows(r, oid="a11.service.cowork")] == [1, 2, 3, 4, 5, 6]


def test_a_one_time_purchase_is_charged_once_across_all_years(bundle2):
    rs = _results_for(bundle2, "u01.a11.c0")
    charges = [f for r in rs for f in flows(r, oid="a11.durable.computer")]
    assert len(charges) == 1 and cents(charges[0].amount) == -3_000_00


def test_an_annual_prepaid_service_is_paid_once_and_covers_twelve_months_across_new_year(bundle2):
    y1, y2, _ = _results_for(bundle2, "u01.a11.c0")
    paid = [f for r in (y1, y2) for f in flows(r, oid="a11.service.gym_annual")]
    assert len(paid) == 1 and paid[0].month == 7 and cents(paid[0].amount) == -480_00
    gym1 = next(h for h in y1.outcome.holdings_service if h.opportunity_id.endswith("gym_annual"))
    gym2 = next(h for h in y2.outcome.holdings_service if h.opportunity_id.endswith("gym_annual"))
    assert gym1.covered_months == (7, 8, 9, 10, 11, 12) and gym1.remaining_months_after == 6
    assert gym2.covered_months == (1, 2, 3, 4, 5, 6) and gym2.expired
    assert all(not h.opportunity_id.endswith("gym_annual") for h in y2.next_state.holdings)


def test_a_weekly_price_is_52_over_12_weeks_a_month_and_sums_to_52_weeks(bundle2):
    plan = plus_items(
        plan_of(bundle2, "u01.a11.c0"),
        {
            "opportunity_id": "a11.service.cleaning",
            "quantity": 1,
            "start_month": 1,
            "end_month": 12,
        },
    )
    r = settle(bundle2, "u01.a11.c0", plan=plan)
    charges = [-cents(f.amount) for f in flows(r, oid="a11.service.cleaning")]
    assert charges == [17333, 17334, 17333] * 4  # 40 × 52 / 12 = 173.333…，累计舍入
    assert sum(charges) == 52 * 40_00


# --------------------------------------------------------------------------
# 6. 最低生活：恰好够就过；不够就出局，记对月份与缺口
# --------------------------------------------------------------------------


def test_exactly_enough_for_the_minimum_passes_and_short_of_it_exits(bundle2):
    r = settle(bundle2, "u02.b12.c0")
    m4, m5 = r.outcome.months[3], r.outcome.months[4]
    assert cents(m4.cash_start) + cents(m4.income_gross) == cents(m4.minimum_required) == 1_300_00
    assert m4.minimum_check == "passed" and cents(m4.cash_end) == 0
    assert m5.minimum_check == "failed"
    assert r.status == "exited"
    e = r.outcome.exit
    assert (e.year_index, e.month) == (1, 5)
    assert cents(e.cash_available) == 1_000_00 and cents(e.shortfall) == 300_00
    assert e.unaffordable_needs == ("food",), "先住房后食品：住房够，食品缺"


def test_below_the_minimum_housing_both_needs_are_short(bundle2):
    s = revise(state_of(bundle2, "u02.b12.c0"), cash={"amount": 0, "numeraire": USD})
    plan = with_items(
        plan_of(bundle2, "u02.b12.c0"),
        {"opportunity_id": "b12.housing.studio", "quantity": 1, "start_month": 1, "end_month": 12},
        {"opportunity_id": "b12.food.basic", "quantity": 1, "start_month": 1, "end_month": 12},
    )
    r = settle(bundle2, "u02.b12.c0", state=s, plan=plan)
    e = r.outcome.exit
    assert e.month == 1 and e.unaffordable_needs == ("housing", "food")
    assert cents(e.shortfall) == 1_300_00


# --------------------------------------------------------------------------
# 7. 最低生活检查是约束，不是另一笔扣款
# --------------------------------------------------------------------------


def test_the_minimum_check_is_a_constraint_not_a_second_charge(bundle2):
    r = settle(bundle2, "u01.a11.c0")
    planned = {i.opportunity_id for i in plan_of(bundle2, "u01.a11.c0").items}
    expenses = flows(r, kind="expense")
    assert {f.opportunity_id for f in expenses} <= planned, "没有任何一笔'最低生活'扣款"
    housing_food = [
        f for f in expenses if f.opportunity_id in ("a11.housing.studio", "a11.food.basic")
    ]
    assert sum(-cents(f.amount) for f in housing_food) == 12 * 1_300_00


# --------------------------------------------------------------------------
# 8. 自选项失败不是出局；格式错误与出局分开
# --------------------------------------------------------------------------


def test_an_unaffordable_optional_purchase_is_rejected_not_an_exit(bundle2):
    plan = plan_of(bundle2, "u01.a11.c0")
    items = _items(plan)
    items[4] = {**items[4], "quantity": 100}  # 100 张音乐会票 = 15,000
    r = settle(bundle2, "u01.a11.c0", plan=with_items(plan, *items))
    assert r.status == "completed" and r.next_state.status == "active"
    concert = item(r, "a11.experience.concert")
    assert concert.status == "rejected" and concert.executed_quantity == 0
    assert cents(r.outcome.cash_end) == 11_270_00 + 150_00


def test_an_ineligible_job_is_rejected_every_month_and_pays_nothing(bundle2):
    plan = plus_items(
        plan_of(bundle2, "u01.a11.c0"),
        {"opportunity_id": "a11.job.forklift", "quantity": 1, "start_month": 1, "end_month": 12},
    )
    r = settle(bundle2, "u01.a11.c0", plan=plan)
    fork = item(r, "a11.job.forklift")
    assert fork.status == "rejected"
    assert [x.month for x in fork.rejections] == list(range(1, 13))
    assert "skill:forklift>=1" in fork.rejections[0].reason
    assert cents(fork.amount_received) == 0
    assert cents(r.outcome.cash_end) == 11_270_00


def test_an_item_with_unknown_availability_is_rejected_never_priced_at_zero(bundle2):
    plan = plus_items(
        plan_of(bundle2, "u01.b12.c0"),
        {"opportunity_id": "b12.experience.concert", "quantity": 1, "month": 6},
    )
    r = settle(bundle2, "u01.b12.c0", plan=plan)
    assert r.status == "completed"
    concert = item(r, "b12.experience.concert")
    assert concert.status == "rejected" and "未知" in concert.rejections[0].reason
    assert not flows(r, oid="b12.experience.concert")


def _drop_month(items):
    items[3].pop("month")


def _unknown(items):
    items[3]["opportunity_id"] = "a11.durable.yacht"


def _fractional(items):
    items[4]["quantity"] = 1.5


def _two_jobs(items):
    items[2]["quantity"] = 2


def _housing_gap(items):
    items[0]["end_month"] = 6


def _double_food(items):
    items.append(
        {"opportunity_id": "a11.food.fresh", "quantity": 1, "start_month": 3, "end_month": 4}
    )


def _prepaid_end(items):
    items[5]["end_month"] = 12


def _arrangement(items):
    items[1]["arrangement"] = {"shopping_cadence": "daily"}


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (_drop_month, "timing"),
        (_unknown, "unknown_opportunity"),
        (_fractional, "quantity"),
        (_two_jobs, "quantity"),
        (_housing_gap, "housing_not_covered"),
        (_double_food, "food_double_covered"),
        (_prepaid_end, "timing"),
        (_arrangement, "arrangement"),
    ],
)
def test_plan_format_errors_are_input_errors_with_no_ledger(bundle2, mutate, code):
    plan = plan_of(bundle2, "u01.a11.c0")
    items = _items(plan)
    mutate(items)
    r = settle(bundle2, "u01.a11.c0", plan=with_items(plan, *items))
    assert r.status == "input_error"
    assert r.outcome is None and r.next_state is None, "结算前能发现的错误不留半本账"
    assert code in {p.code for p in r.problems}


def test_an_unaffordable_chosen_home_without_fallback_is_infeasible_not_an_exit(bundle2):
    plan = revise(plan_of(bundle2, "u02.a11.c0"), minimum_fallback=())
    r = settle(bundle2, "u02.a11.c0", plan=plan)
    assert r.status == "plan_infeasible"
    assert r.outcome is None and r.next_state is None
    (p,) = r.problems
    assert (p.code, p.month, p.item_order) == ("housing_unaffordable", 2, 0)
    assert cents(p.cash_available) == 1_000_00 and cents(p.required) == 1_400_00
    assert "不是出局" in p.message


def test_a_declared_fallback_downgrades_and_says_so(bundle2):
    r = settle(bundle2, "u02.a11.c0")
    two_bed = item(r, "a11.housing.two_bed")
    studio = item(r, "a11.housing.studio", fallback=True)
    assert two_bed.status == "downgraded_to_minimum" and two_bed.downgraded_from_month == 2
    assert two_bed.covered_months == (1,) and two_bed.months_not_reached == ()
    assert studio.fallback_for_plan_order == two_bed.plan_order == 0
    assert studio.covered_months == (2,) and cents(studio.amount_paid) == 900_00
    assert r.status == "exited" and r.outcome.exit.month == 3
    assert cents(r.outcome.exit.shortfall) == 200_00
    seen = next(e for e in visible_feedback(r).executed if e.status == "downgraded_to_minimum")
    assert "资金不足" in seen.reason


def test_a_downgrade_lasts_for_the_rest_of_that_lease_and_the_clone_can_survive(bundle2):
    s = revise(state_of(bundle2, "u02.a11.c0"), cash={"amount": 1500, "numeraire": USD})
    plan = with_items(
        plan_of(bundle2, "u02.a11.c0"),
        {"opportunity_id": "a11.housing.two_bed", "quantity": 1, "start_month": 1, "end_month": 12},
        {"opportunity_id": "a11.food.basic", "quantity": 1, "start_month": 1, "end_month": 12},
        {"opportunity_id": "a11.job.office", "quantity": 1, "start_month": 2, "end_month": 12},
        minimum_fallback=["housing"],
    )
    r = settle(bundle2, "u02.a11.c0", state=s, plan=plan)
    assert r.status == "completed"
    assert item(r, "a11.housing.studio", fallback=True).covered_months == tuple(range(1, 13))
    assert item(r, "a11.housing.two_bed").covered_months == ()


def test_a_chosen_food_plan_missing_here_is_infeasible_unless_a_fallback_is_declared(bundle2):
    plan = plan_of(bundle2, "u01.a11.c0")
    items = _items(plan)
    items[1] = {**items[1], "opportunity_id": "a11.food.meal_kit"}  # 本城确定缺货
    r = settle(bundle2, "u01.a11.c0", plan=with_items(plan, *items))
    assert r.status == "plan_infeasible" and r.problems[0].code == "food_not_provided"
    r2 = settle(bundle2, "u01.a11.c0", plan=with_items(plan, *items, minimum_fallback=["food"]))
    assert r2.status == "completed"
    assert item(r2, "a11.food.meal_kit").status == "downgraded_to_minimum"
    assert item(r2, "a11.food.basic", fallback=True).covered_months == tuple(range(1, 13))
    assert cents(r2.outcome.cash_end) == 11_270_00


def test_same_month_competition_follows_the_plan_order(bundle2):
    """第 2 月可用 3,900：电脑 3,000 与 20 张票 3,000 只能成交一个，排在前面的成交。"""
    base = plan_of(bundle2, "u01.a11.c0")
    housing, food, job = _items(base)[:3]
    computer = {"opportunity_id": "a11.durable.computer", "quantity": 1, "month": 2}
    tickets = {"opportunity_id": "a11.experience.concert", "quantity": 20, "month": 2}
    first = settle(
        bundle2, "u01.a11.c0", plan=with_items(base, housing, food, job, computer, tickets)
    )
    second = settle(
        bundle2, "u01.a11.c0", plan=with_items(base, housing, food, job, tickets, computer)
    )
    assert item(first, "a11.durable.computer").status == "executed"
    assert item(first, "a11.experience.concert").status == "rejected"
    assert item(second, "a11.durable.computer").status == "rejected"
    assert item(second, "a11.experience.concert").status == "executed"
    assert [i.plan_order for i in first.outcome.items] == [0, 1, 2, 3, 4]


def test_periodic_charges_come_before_one_time_purchases_within_a_month(bundle2):
    """月内顺序是规则：第 5 步（周期费用）先于第 6 步（一次性购买），与在计划里的先后无关。"""
    base = plan_of(bundle2, "u01.a11.c0")
    housing, food, job = _items(base)[:3]
    computer = {"opportunity_id": "a11.durable.computer", "quantity": 1, "month": 2}
    cowork = {
        "opportunity_id": "a11.service.cowork",
        "quantity": 5,
        "start_month": 2,
        "end_month": 2,
    }
    r = settle(bundle2, "u01.a11.c0", plan=with_items(base, housing, food, job, computer, cowork))
    assert item(r, "a11.service.cowork").status == "executed"
    assert item(r, "a11.durable.computer").status == "rejected"  # 3,900 − 1,100 = 2,800


# --------------------------------------------------------------------------
# 9. 缺最低生活数据是数据错误，不是出局
# --------------------------------------------------------------------------


def _break(raw, how):
    a11 = catalog(raw, "A11")
    if how == "drop_food":
        a11["minimum_living"] = [m for m in a11["minimum_living"] if m["need"] != "food"]
    elif how == "dangling_reference":
        a11["minimum_living"][1]["baseline_opportunity_id"] = "a11.food.ghost"
        a11["minimum_living"][1]["qualifying_opportunity_ids"].append("a11.food.ghost")
    elif how == "baseline_unavailable":
        a11["minimum_living"][1]["baseline_opportunity_id"] = "a11.food.meal_kit"
    elif how == "no_tax_policy":
        del a11["tax_policy_id"]
    elif how == "quantity_two":
        a11["minimum_living"][0]["baseline_quantity"] = 2
    elif how == "annual_rent":
        opportunity(raw, "a11.housing.two_bed")["price"]["billing"] = "per_year"


@pytest.mark.parametrize(
    "how",
    [
        "drop_food",
        "dangling_reference",
        "baseline_unavailable",
        "no_tax_policy",
        "quantity_two",
        "annual_rent",
    ],
)
def test_broken_minimum_or_tax_data_is_a_data_error_never_an_exit(raw2, how):
    from econ.life_settlement import binding_from_policy

    _break(raw2, how)
    b = bundle_from(raw2)
    cat = b.catalog_for("US:SYNTH2026:A11")
    r = settle(b, "u01.a11.c0", catalog=cat, tax=binding_from_policy(b.tax_policy("tax.synth.za")))
    assert r.status == "data_error"
    assert r.outcome is None and r.next_state is None
    report = cross_validate(b)
    assert report.contract_valid and not report.settlement_ready


def test_a_missing_tax_configuration_is_never_read_as_zero_tax(raw2):
    _break(raw2, "no_tax_policy")
    report = cross_validate(bundle_from(raw2))
    assert any("没有配置不等于零税" in p for p in report.settlement_problems)


def test_the_strict_settlement_load_refuses_a_catalog_without_minimum_living():
    with pytest.raises(LifeContractError, match="不能结算"):
        load_life_bundle(Path("configs/life/synthetic_e1_1.yaml"), require_settlement_ready=True)


# --------------------------------------------------------------------------
# 10. 出局之后：没有收入与消费、记录保留、不复活
# --------------------------------------------------------------------------


def test_after_exit_nothing_more_happens_and_the_records_are_kept(bundle2):
    r = settle(bundle2, "u02.b12.c0")
    assert max(f.month for f in r.outcome.cash_flows) == 5
    assert [f.kind for f in r.outcome.cash_flows if f.month == 5] == ["income_gross"]
    assert len(r.outcome.months) == 5
    job = item(r, "b12.job.parttime")
    assert job.covered_months == (1, 2, 3, 4, 5)
    assert job.months_not_reached == tuple(range(6, 13))
    ns = r.next_state
    assert (ns.status, ns.year_index, ns.exit.month) == ("exited", 1, 5)
    assert cents(ns.cash) == 1_000_00


def test_an_exited_clone_is_not_revived_and_gets_no_observation(bundle2):
    r = settle(bundle2, "u02.b12.c0")
    again = settle(bundle2, "u02.b12.c0", state=r.next_state)
    assert again.status == "input_error" and again.problems[0].code == "state_not_active"
    with pytest.raises(LifeContractError, match="exited"):
        _observe(bundle2, r.next_state)


def test_an_exited_clone_keeps_what_it_bought_and_service_stops_at_the_exit(bundle2):
    plan = plan_of(bundle2, "u01.a11.c0")
    items = _items(plan)[:4]  # 住房、食品、工作、第 2 月买电脑
    items[2] = {**items[2], "end_month": 2}
    r = settle(bundle2, "u01.a11.c0", plan=with_items(plan, *items))
    assert r.status == "exited" and r.outcome.exit.month == 3
    held = next(h for h in r.next_state.holdings if h.opportunity_id == "a11.durable.computer")
    assert held.remaining_months == 48 - 1, "第 2 月买、服务 1 个月；出局那个月不再计"
    service = next(
        h for h in r.outcome.holdings_service if h.opportunity_id == "a11.durable.computer"
    )
    assert service.covered_months == (2,)


def test_tax_is_withheld_only_on_income_actually_earned(bundle2):
    """工作只做到第 5 月、第 12 月出局：预扣恰好是"全年只有这些收入"时的税。"""
    plan = plan_of(bundle2, "u01.a11.c0")
    items = _items(plan)[:3]
    items[2] = {**items[2], "end_month": 5}
    r = settle(bundle2, "u01.a11.c0", plan=with_items(plan, *items))
    assert r.status == "exited" and r.outcome.exit.month == 12
    assert sum(-cents(f.amount) for f in flows(r, kind="income_tax")) == 600_00  # 20% × 3,000


# --------------------------------------------------------------------------
# 11. 跨年延续：年中购买、生效时点与寿命递减一致
# --------------------------------------------------------------------------


def test_year_one_feeds_year_two_and_lifetimes_count_in_months(bundle2):
    y1, y2, y3 = _results_for(bundle2, "u01.a11.c0")
    computer = item(y1, "a11.durable.computer")
    assert (computer.effective_year, computer.effective_month) == (1, 2)
    services = [
        next(h for h in r.outcome.holdings_service if h.opportunity_id == "a11.durable.computer")
        for r in (y1, y2, y3)
    ]
    assert [len(h.covered_months) for h in services] == [11, 12, 12]
    assert [h.remaining_months_after for h in services] == [37, 25, 13]  # 48 − 11 − 12 − 12
    held = next(h for h in y1.next_state.holdings if h.opportunity_id == "a11.durable.computer")
    assert (held.acquired_year, held.acquired_month, held.remaining_months) == (1, 2, 37)


def test_training_continues_next_year_without_a_second_fee_and_only_records_progress(bundle2):
    y1, y2, _ = _results_for(bundle2, "u01.a11.c0")
    fees = [f for r in (y1, y2) for f in flows(r, oid="a11.training.bookkeeping")]
    assert len(fees) == 1 and fees[0].month == 7 and cents(fees[0].amount) == -1_200_00
    (t1,) = y1.next_state.training
    (t2,) = y2.next_state.training
    assert (t1.months_participated, t1.months_required, t1.completed) == (6, 12, False)
    assert (t2.months_participated, t2.completed) == (12, True)
    assert y2.next_state.skills == {}, "本轮不自动加技能（E1-5）"
    assert y2.next_state.occupation == state_of(bundle2, "u01.a11.c0").occupation


def test_a_next_year_durable_starts_serving_in_month_one_of_the_next_year(raw2):
    opportunity(raw2, "a11.durable.computer")["duration"]["effective_from"] = "next_year"
    b = bundle_from(raw2)
    y1 = settle(b, "u01.a11.c0")
    computer = item(y1, "a11.durable.computer")
    assert (computer.effective_year, computer.effective_month) == (2, 1)
    s1 = next(h for h in y1.outcome.holdings_service if h.opportunity_id == "a11.durable.computer")
    assert s1.covered_months == () and s1.remaining_months_after == 48
    y2 = settle(b, "u01.a11.c0", year=2, state=y1.next_state)
    s2 = next(h for h in y2.outcome.holdings_service if h.opportunity_id == "a11.durable.computer")
    assert s2.covered_months == tuple(range(1, 13)) and s2.remaining_months_after == 36


def _with_held_computer(bundle, years):
    return revise(
        state_of(bundle, "u01.a11.c0"),
        holdings=[
            {
                "opportunity_id": "a11.durable.computer",
                "quantity": 1,
                "acquired_year": 0,
                "remaining_years": years,
            }
        ],
    )


def test_an_e1_1_holding_in_whole_years_is_converted_to_months(bundle2):
    plan = plan_of(bundle2, "u01.a11.c0")
    items = [i for i in _items(plan) if i["opportunity_id"] != "a11.durable.computer"]
    r = settle(
        bundle2, "u01.a11.c0", state=_with_held_computer(bundle2, 2), plan=with_items(plan, *items)
    )
    held = next(h for h in r.next_state.holdings if h.opportunity_id == "a11.durable.computer")
    assert held.remaining_months == 24 - 12 and held.remaining_years is None


def test_buying_a_durable_already_held_is_an_input_error(bundle2):
    r = settle(bundle2, "u01.a11.c0", state=_with_held_computer(bundle2, 2))
    assert r.status == "input_error" and "already_held" in {p.code for p in r.problems}


def test_prepaying_inside_an_existing_prepaid_period_is_an_input_error(bundle2):
    y1 = settle(bundle2, "u01.a11.c0")
    gym = {"opportunity_id": "a11.service.gym_annual", "quantity": 1}
    early = plus_items(plan_of(bundle2, "u01.a11.c0", 2), {**gym, "start_month": 3})
    r = settle(bundle2, "u01.a11.c0", year=2, state=y1.next_state, plan=early)
    assert r.status == "input_error" and "prepaid_overlap" in {p.code for p in r.problems}
    renew = plus_items(plan_of(bundle2, "u01.a11.c0", 2), {**gym, "start_month": 7})
    ok = settle(bundle2, "u01.a11.c0", year=2, state=y1.next_state, plan=renew)
    assert ok.status == "completed", "第 6 月到期后从第 7 月续上是可以的"
    old, new = (h for h in ok.outcome.holdings_service if h.opportunity_id.endswith("gym_annual"))
    assert old.covered_months == (1, 2, 3, 4, 5, 6) and old.expired
    assert new.covered_months == (7, 8, 9, 10, 11, 12) and new.remaining_months_after == 6


def test_planning_more_training_months_than_remain_is_an_input_error(bundle2):
    y1 = settle(bundle2, "u01.a11.c0")
    plan2 = plan_of(bundle2, "u01.a11.c0", 2)
    items = _items(plan2)
    items[3] = {**items[3], "end_month": 12}  # 只剩 6 个月
    r = settle(bundle2, "u01.a11.c0", year=2, state=y1.next_state, plan=with_items(plan2, *items))
    assert r.status == "input_error" and "training_months" in {p.code for p in r.problems}


def test_a_finished_training_cannot_be_taken_again(bundle2):
    _, y2, _ = _results_for(bundle2, "u01.a11.c0")
    plan3 = plus_items(
        plan_of(bundle2, "u01.a11.c0", 3),
        {
            "opportunity_id": "a11.training.bookkeeping",
            "quantity": 1,
            "start_month": 1,
            "end_month": 1,
        },
    )
    r = settle(bundle2, "u01.a11.c0", year=3, state=y2.next_state, plan=plan3)
    assert r.status == "input_error" and "training_done" in {p.code for p in r.problems}


def test_eligibility_from_a_training_starts_the_month_after_it_is_completed(raw2):
    opportunity(raw2, "a11.job.forklift")["eligibility"] = [
        "training_completed:a11.training.bookkeeping"
    ]
    b = bundle_from(raw2)
    y1 = settle(b, "u01.a11.c0")
    plan2 = plus_items(
        plan_of(b, "u01.a11.c0", 2),
        {"opportunity_id": "a11.job.forklift", "quantity": 1, "start_month": 1, "end_month": 12},
    )
    y2 = settle(b, "u01.a11.c0", year=2, state=y1.next_state, plan=plan2)
    fork = item(y2, "a11.job.forklift")
    assert fork.covered_months == tuple(range(7, 13))
    assert [x.month for x in fork.rejections] == [1, 2, 3, 4, 5, 6]


def test_time_is_checked_against_the_budget_and_energy_is_only_recorded(bundle2):
    r = settle(bundle2, "u01.a11.c0")
    # 食品 200 + 工作 1,900 + 音乐会 6 + 年卡 100×6/12 + 簿记课 300×6/12
    assert r.outcome.time_used_hours == 2306.0
    assert r.outcome.energy_burden_recorded == 11.0
    assert r.outcome.energy_feasibility == "not_implemented"
    exact = revise(state_of(bundle2, "u01.a11.c0"), time_budget_hours=2306)
    assert settle(bundle2, "u01.a11.c0", state=exact).status == "completed"
    tight = revise(state_of(bundle2, "u01.a11.c0"), time_budget_hours=2305)
    over = settle(bundle2, "u01.a11.c0", state=tight)
    assert over.status == "input_error" and "time_budget" in {p.code for p in over.problems}


# --------------------------------------------------------------------------
# 12. 模拟长度 N 与终态 x = N + 1
# --------------------------------------------------------------------------


def test_three_years_end_in_the_terminal_state_four(bundle2):
    term = _results_for(bundle2, "u01.a11.c0")[-1].next_state
    assert (term.status, term.year_index) == ("terminal", 4)
    assert term.year_index == bundle2.protocol.terminal_year_index


def test_the_terminal_state_accepts_no_action(bundle2):
    term = _results_for(bundle2, "u01.a11.c0")[-1].next_state
    r = settle(bundle2, "u01.a11.c0", state=term, plan=plan_of(bundle2, "u01.a11.c0", 3))
    assert r.status == "input_error" and r.problems[0].code == "state_not_active"
    with pytest.raises(LifeContractError, match="terminal"):
        _observe(bundle2, term)


def test_a_plan_for_the_terminal_year_is_refused(bundle2):
    plan4 = revise(plan_of(bundle2, "u01.a11.c0", 3), year_index=4)
    r = settle(bundle2, "u01.a11.c0", plan=plan4)
    assert r.status == "input_error" and "year_beyond_horizon" in {p.code for p in r.problems}


@pytest.mark.parametrize("n", [1, 5])
def test_another_horizon_ends_at_n_plus_one(bundle2, n):
    protocol = protocol_with_years(bundle2.protocol, n)
    plan3 = plan_of(bundle2, "u01.a11.c0", 3)
    plans = [plan_of(bundle2, "u01.a11.c0", 1), plan_of(bundle2, "u01.a11.c0", 2)]
    plans += [revise(plan3, year_index=y) for y in range(3, n + 1)]
    s = state_of(bundle2, "u01.a11.c0")
    cat = catalog_of(bundle2, s)
    rs = settle_plans(
        protocol=protocol, state=s, plans=plans[:n], catalog=cat, tax=tax_of(bundle2, cat)
    )
    assert [r.status for r in rs] == ["completed"] * n
    term = rs[-1].next_state
    assert (term.status, term.year_index) == ("terminal", n + 1)
    if n == 5:
        # 电脑 48 个月：第 1 年 11 个月，第 2–4 年各 12 个月，第 5 年第 1 月用完
        last = next(
            h for h in rs[-1].outcome.holdings_service if h.opportunity_id == "a11.durable.computer"
        )
        assert last.covered_months == (1,) and last.expired
        assert all(h.opportunity_id != "a11.durable.computer" for h in term.holdings)


def test_an_early_exit_keeps_its_real_year_and_month(bundle2):
    ns = settle(bundle2, "u02.b12.c0").next_state
    assert (ns.status, ns.year_index, ns.exit.month) == ("exited", 1, 5)
    assert ns.year_index != bundle2.protocol.terminal_year_index


def test_a_next_year_durable_bought_in_the_last_year_waits_in_the_terminal_state(raw2):
    opportunity(raw2, "a11.durable.computer")["duration"]["effective_from"] = "next_year"
    raw2["protocol"]["simulation_years"] = 1
    term = settle(bundle_from(raw2), "u01.a11.c0").next_state
    assert (term.status, term.year_index) == ("terminal", 2)
    computer = next(h for h in term.holdings if h.opportunity_id == "a11.durable.computer")
    assert computer.in_service_from_year == 2 and computer.remaining_months == 48


def test_the_loader_checks_plan_years_against_n(raw2):
    raw2["protocol"]["simulation_years"] = 2  # 样例里有第 3 年的计划
    errors = cross_validate(bundle_from(raw2)).errors
    assert any("超出协议的 N = 2" in e for e in errors)


def test_the_loader_checks_the_terminal_index_and_refuses_its_plans(raw2):
    s = next(x for x in raw2["initial_states"] if x["clone_id"] == "u01.a11.c0")
    s["status"], s["year_index"] = "terminal", 3
    errors = cross_validate(bundle_from(raw2)).errors
    assert any("终态编号必须是 N + 1 = 4" in e for e in errors)
    assert any("不接受计划" in e for e in errors)


def test_the_loader_refuses_a_holding_bought_in_a_year_that_has_not_happened(raw2):
    s = next(x for x in raw2["initial_states"] if x["clone_id"] == "u01.a11.c0")
    s["holdings"] = [
        {
            "opportunity_id": "a11.durable.computer",
            "quantity": 1,
            "acquired_year": 1,
            "acquired_month": 3,
            "remaining_months": 40,
        }
    ]
    errors = cross_validate(bundle_from(raw2)).errors
    assert any("购入年份 1 晚于状态允许的 0" in e for e in errors)
