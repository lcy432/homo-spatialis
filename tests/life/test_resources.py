"""月度时间与精力（`DECISIONS/0020`）：每月活动小时不超过时间预算，活动负担不超过精力额度；超了是计划不可行，不是出局。

K51 的基本安排（远处住房、基本食品、全职文员），每月可以手算：

=================  ==================  ================================
来源               小时                 负担（小时 × 每小时负担）
=================  ==================  ================================
文员               160                  160 × 1.0 = 160
通勤               70 分钟 × 20 次 = 70/3  70/3 × 0.75 = 17.5
做饭（基本食品）   30                   30 × 0.5 = 15
洗衣（自己做）     12                   12 × 0.5 = 6
打扫（自己做）     10                   10 × 0.6 = 6
合计               706/3 ≈ 235.33        204.5（额度 250）
=================  ==================  ================================
"""

from __future__ import annotations

from fractions import Fraction

from econ.life_settlement import check_year_plan, settle_year

from .conftest import CLONE_K, K51, base_items, once, tax_for, whole, year_action


def _settle(bundle, items):
    state = bundle.initial_state(CLONE_K)
    return settle_year(
        protocol=bundle.protocol,
        state=state,
        action=year_action(state, items),
        catalog=bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
    )


def _check(bundle, items):
    state = bundle.initial_state(CLONE_K)
    return check_year_plan(
        protocol=bundle.protocol,
        state=state,
        action=year_action(state, items),
        catalog=bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
    )


def _by(month, key):
    out: dict[str, Fraction] = {}
    for c in month.contributions:
        name = c.task or c.category
        out[name] = out.get(name, Fraction(0)) + getattr(c, key).fraction()
    return out


def test_the_base_month_by_hand(bundle):
    m = _settle(bundle, base_items()).outcome.resources.months[0]
    assert (m.time_budget.fraction(), m.energy_quota.fraction()) == (365, 250)
    assert m.time_used.fraction() == Fraction(706, 3)
    assert m.energy_used.fraction() == Fraction(409, 2)  # 204.5
    assert m.within_limits is True
    hours = _by(m, "hours")
    assert hours["work"] == 160 and hours["commute"] == Fraction(70, 3)
    assert (hours["laundry"], hours["cleaning"]) == (12, 10)
    burden = _by(m, "burden")
    assert burden["commute"] == Fraction(35, 2) and burden["laundry"] == burden["cleaning"] == 6
    assert (m.commute.longest_roundtrip_minutes, m.onsite_visits) == (70, 20)
    assert m.commute.uses[0].mode == "transit" and m.commute.paid.amount == 0


def test_living_near_work_saves_commute_hours_but_costs_rent(bundle):
    """近处一居室往返 30 分钟：每月通勤 30 × 20 / 60 = 10 小时（远处是 70/3），房租多 400。"""
    near = [whole("k51.housing.near"), whole("k51.food.basic"), whole("k51.job.clerk")]
    m = _settle(bundle, near).outcome.resources.months[0]
    assert _by(m, "hours")["commute"] == 10
    assert m.time_used.fraction() == Fraction(706, 3) - Fraction(70, 3) + 10


def test_a_second_job_is_over_the_energy_quota_not_over_time(bundle):
    """再加门店兼职（80 工时、10 次到岗）：小时 706/3 + 80 + 70/3 × 1/2 = 327 ≤ 365；负担 204.5 + 80 + 8.75 = 293.25 > 250。
    计划不可行（可以修订），不是出局。"""
    check = _check(bundle, [*base_items(), whole("k51.job.shop_parttime")])
    assert (check.stage, check.status) == ("no_event_trial", "plan_infeasible")
    assert [p.code for p in check.problems] == ["energy_over_limit"]
    r = _settle(bundle, [*base_items(), whole("k51.job.shop_parttime")])
    assert r.status == "plan_infeasible" and r.outcome is None


def test_adding_the_course_too_is_over_time_as_well(bundle):
    """再加会计课（每月 40 小时）：327 + 40 = 367 > 365，时间与精力都超。"""
    items = [
        *base_items(),
        whole("k51.job.shop_parttime"),
        whole("k51.training.accounting_course"),
    ]
    codes = {p.code for p in _check(bundle, items).problems}
    assert {"time_over_limit", "energy_over_limit"} <= codes


def test_a_chores_service_lowers_hours_and_burden_and_takes_no_time_itself(bundle):
    """按月服务把洗衣、打扫都降到每月 2 小时：小时少 (12 − 2) + (10 − 2) = 18，负担少 10 × 0.5 + 8 × 0.6 = 9.8。"""
    m = _settle(bundle, [*base_items(), whole("k51.service.chores")]).outcome.resources.months[0]
    assert m.time_used.fraction() == Fraction(706, 3) - 18
    assert m.energy_used.fraction() == Fraction(409, 2) - Fraction(49, 5)
    rows = {
        (c.category, c.task): c for c in m.contributions if c.opportunity_id == "k51.service.chores"
    }
    # 残余的家务记在服务名下（各 2 小时）；服务本身记一行 0 小时、0 负担——它不占本人的时间
    assert rows[("household_task", "laundry")].hours.fraction() == 2
    assert rows[("household_task", "cleaning")].hours.fraction() == 2
    assert (
        rows[("service", None)].hours.fraction(),
        rows[("service", None)].burden.fraction(),
    ) == (0, 0)


def test_a_device_bought_in_july_helps_from_july(bundle):
    months = _settle(
        bundle, [*base_items(), once("k51.durable.convenience_pack", 7)]
    ).outcome.resources.months
    laundry = [_by(m, "hours")["laundry"] for m in months]
    assert laundry == [12] * 6 + [4] * 6


def test_the_energy_quota_is_monthly_and_does_not_carry_over(bundle):
    """每月额度 250 各月独立：少用的月份不攒给别的月份。"""
    months = _settle(bundle, base_items()).outcome.resources.months
    assert {m.energy_quota.fraction() for m in months} == {250}
    assert {m.energy_remaining.fraction() for m in months} == {Fraction(91, 2)}
