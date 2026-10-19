"""E1-4A 月度资源的纯函数与契约（`DECISIONS/0020`）。

数值一律用精确分数比较，不加容差：资源输入最多两位小数，纯函数内部是 ``Fraction``。
样例数字见 ``configs/life/synthetic_e1_4a.yaml`` 文末的手算。
"""

from __future__ import annotations

import ast
import inspect
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from common.life_contracts import (
    ExactAmount,
    JobArrangement,
    LifeContractError,
    MonthlyResourceRules,
    MonthResources,
    ResourceProfile,
)
from econ import life_resources as lr
from econ.life_resources import Activity, commute_hours, household_rows, month_resources
from ledger.life_catalog import parse_bundle

from .settle_support import catalog_of, revise, state_of

F = Fraction  # 手算用的分数简写


def _opp(bundle, oid):
    city = "A51" if oid.startswith("a51.") else "B52"
    cat = next(c for c in bundle.catalogs if c.city.code == city)
    o = cat.opportunity(oid)
    assert o is not None, oid
    return o


def _act(bundle, role, oid, qty=1):
    return Activity(role, _opp(bundle, oid), qty)


def _near_month(bundle, housing="a51.housing.near"):
    """u21 住近处那份计划在一个普通月份里实际执行的东西。"""
    return [
        _act(bundle, "housing", housing),
        _act(bundle, "food", "a51.food.fresh"),
        _act(bundle, "job", "a51.job.office"),
        _act(bundle, "durable_good", "a51.durable.washer"),
        _act(bundle, "prepaid_service", "a51.service.gym_plan"),
    ]


def _hours_of(record, category):
    return sum((c.hours.fraction() for c in record.contributions if c.category == category), F(0))


def _table(record):
    return {
        (c.category, c.task or c.opportunity_id): (c.hours.fraction(), c.burden.fraction())
        for c in record.contributions
    }


@pytest.fixture
def rules(bundle4a) -> MonthlyResourceRules:
    r = bundle4a.protocol.resource_rules
    assert r is not None
    return r


# --------------------------------------------------------------------------
# 换算：预算、额度、通勤
# --------------------------------------------------------------------------


def test_the_monthly_budget_is_the_annual_net_hours_over_twelve(bundle4a):
    s = state_of(bundle4a, "u21.a51.near")
    assert lr.monthly_time_budget(s) == 365
    # 两位小数的声明值按十进制精确换算，不经过二进制误差
    assert lr.monthly_time_budget(revise(s, time_budget_hours=4380.01)) == F(438001, 1200)
    assert lr.monthly_energy_quota(s) == 250


def test_an_old_energy_number_is_not_read_as_a_monthly_quota(bundle2):
    """旧样例的精力尺度没有冻结：资源函数拒绝把它当成月度额度。"""
    s = bundle2.initial_states[0]
    assert s.resource_budget_basis is None
    with pytest.raises(LifeContractError, match="不能当成月度额度"):
        lr.monthly_energy_quota(s)


def test_commute_hours_are_roundtrip_minutes_times_onsite_days_over_sixty():
    assert commute_hours(30, 20) == 10
    assert commute_hours(70, 20) == F(70, 3)  # 23⅓ 小时
    assert commute_hours(80, 8) == F(32, 3)
    assert commute_hours(70, 0) == 0


@pytest.mark.parametrize("bad", [-1, True, 30.0, None, "30"])
def test_roundtrip_minutes_must_be_a_non_negative_integer(bad):
    with pytest.raises(LifeContractError, match="非负整数"):
        commute_hours(bad, 20)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# 家务：替代后残余用时取最小，不逐项相减
# --------------------------------------------------------------------------


def test_without_substitutes_the_person_does_the_baseline(bundle4a, rules):
    rows = household_rows(rules, [])
    assert [(r.task, r.hours.fraction(), r.burden.fraction(), r.opportunity_id) for r in rows] == [
        ("laundry", 12, 6, None),
        ("cleaning", 10, 6, None),
    ]
    assert "本人做 12 小时" in rows[0].basis


def test_a_washer_leaves_three_hours_and_says_who_carries_the_rest(bundle4a, rules):
    laundry, cleaning = household_rows(
        rules, [_act(bundle4a, "durable_good", "a51.durable.washer")]
    )
    assert (laundry.hours.fraction(), laundry.burden.fraction()) == (3, F(3, 2))
    assert laundry.opportunity_id == "a51.durable.washer"
    assert "本人还要 3 小时" in laundry.basis and "由它承担 9 小时" in laundry.basis
    # 洗衣机不碰打扫
    assert (cleaning.hours.fraction(), cleaning.opportunity_id) == (10, None)


def test_two_substitutes_for_one_task_do_not_both_deduct(bundle4a, rules):
    """洗衣机（残余 3）+ 洗衣代取（残余 1）：本人 1 小时。逐项相减会得到 12 − 9 − 11 < 0。"""
    washer = _act(bundle4a, "durable_good", "a51.durable.washer")
    pickup = _act(bundle4a, "periodic_service", "a51.service.laundry_pickup")
    for order in ([washer, pickup], [pickup, washer]):
        laundry = household_rows(rules, order)[0]
        assert laundry.hours.fraction() == 1
        assert laundry.opportunity_id == "a51.service.laundry_pickup"
    assert all(r.hours.fraction() >= 0 for r in household_rows(rules, [washer, pickup]))


# --------------------------------------------------------------------------
# 计量依据
# --------------------------------------------------------------------------


def test_per_use_is_uses_times_hours_and_quantity_counts_only_when_declared(bundle4a):
    gym = _opp(bundle4a, "a51.service.gym_plan").resource
    assert gym is not None
    assert lr.timed_hours(gym, 1) == 12  # 每月 8 次 × 1.5 小时
    assert lr.timed_hours(gym, 2) == 12  # quantity_scales = false：两张卡不是两倍锻炼
    concert = _opp(bundle4a, "a51.experience.concert").resource
    assert concert is not None
    assert lr.timed_hours(concert, 1) == 4
    assert lr.timed_hours(concert, 3) == 12  # 每张票一次体验


# --------------------------------------------------------------------------
# 一个月的账
# --------------------------------------------------------------------------


def test_one_month_of_the_near_plan_by_hand(bundle4a, rules):
    s = state_of(bundle4a, "u21.a51.near")
    rec, short = month_resources(rules, s, 1, _near_month(bundle4a))
    assert short == []
    assert _table(rec) == {
        ("work", "a51.job.office"): (160, 160),
        ("commute", "a51.job.office"): (10, F(15, 2)),
        ("food_prep", "a51.food.fresh"): (40, 20),
        ("household_task", "laundry"): (3, F(3, 2)),
        ("household_task", "cleaning"): (10, 6),
        ("service", "a51.service.gym_plan"): (12, 12),
    }
    # 235 小时已安排 + 130 小时未安排 = 365；未安排的月底作废（不结转、不回血），不要求安排满
    assert (rec.time_used.fraction(), rec.time_remaining.fraction()) == (235, 130)
    assert (rec.energy_used.fraction(), rec.energy_remaining.fraction()) == (207, 43)
    assert rec.within_limits is True and rec.resource_check == "complete"
    assert (
        rec.housing_opportunity_id,
        rec.commute_roundtrip_minutes,
        rec.job_workdays_sum,
        rec.onsite_visits,
        rec.commute_status,
    ) == ("a51.housing.near", 30, 20, 20, "computed")
    commute = next(c for c in rec.contributions if c.category == "commute")
    assert "a51.housing.near" in commute.basis and "到岗 20 次" in commute.basis
    # 给 E1-5 的比例：工作、通勤分开，总负担另列
    assert rec.work_relative_burden.fraction() == F(160, 250)
    assert rec.commute_relative_burden.fraction() == F(15, 2) / 250
    assert rec.total_relative_burden.fraction() == F(207, 250)


def test_the_far_home_costs_more_commute_with_the_same_job(bundle4a, rules):
    s = state_of(bundle4a, "u21.a51.far")
    rec, _ = month_resources(rules, s, 1, _near_month(bundle4a, "a51.housing.far"))
    assert _table(rec)[("commute", "a51.job.office")] == (F(70, 3), F(35, 2))
    assert rec.housing_opportunity_id == "a51.housing.far"
    assert (rec.time_used.fraction(), rec.energy_used.fraction()) == (F(745, 3), 217)


def test_exactly_at_the_limit_passes_and_one_declared_step_over_fails(bundle4a, rules):
    """额度恰好等于用量通过；输入的最小声明精度是 0.01，再少 0.01 就超。"""
    s = state_of(bundle4a, "u21.a51.near")
    acts = _near_month(bundle4a)
    at = revise(s, time_budget_hours=2820, energy=revise(s.energy, level=207))
    rec, short = month_resources(rules, at, 1, acts)
    assert short == [] and rec.within_limits is True
    assert rec.time_remaining.fraction() == 0 and rec.energy_remaining.fraction() == 0

    rec, short = month_resources(rules, revise(at, time_budget_hours=2819.99), 1, acts)
    assert rec.within_limits is False
    assert [(x.resource, x.excess.fraction()) for x in short] == [("time", F(1, 1200))]

    rec, short = month_resources(rules, revise(at, energy=revise(at.energy, level=206.99)), 1, acts)
    assert [(x.resource, x.excess.fraction()) for x in short] == [("energy", F(1, 100))]


def test_a_shortfall_lists_the_biggest_contributors_first(bundle4a, rules):
    s = revise(state_of(bundle4a, "u21.a51.near"), time_budget_hours=2400)  # 每月 200 小时
    _, short = month_resources(rules, s, 1, _near_month(bundle4a))
    (x,) = short
    assert (x.resource, x.month, x.unit) == ("time", 1, "hour")
    assert (x.limit.fraction(), x.used.fraction(), x.excess.fraction()) == (200, 235, 35)
    # 小时从大到小；并列时按记录顺序（通勤在前、家务在后）
    assert [(c.category, c.task or c.opportunity_id) for c in x.top_contributors] == [
        ("work", "a51.job.office"),
        ("food_prep", "a51.food.fresh"),
        ("service", "a51.service.gym_plan"),
        ("commute", "a51.job.office"),
        ("household_task", "cleaning"),
    ]


def test_remote_work_has_no_commute_even_from_a_far_home(bundle4a, rules):
    s = state_of(bundle4a, "u21.b52.remote")
    acts = [
        _act(bundle4a, "housing", "b52.housing.far"),
        _act(bundle4a, "food", "b52.food.fresh"),
        _act(bundle4a, "job", "b52.job.remote"),
        _act(bundle4a, "durable_good", "b52.durable.washer"),
    ]
    rec, _ = month_resources(rules, s, 1, acts)
    assert (rec.commute_status, rec.onsite_visits, rec.job_workdays_sum) == (
        "no_onsite_work",
        0,
        20,
    )
    assert "commute" not in {c.category for c in rec.contributions}
    assert rec.housing_opportunity_id == "b52.housing.far" and rec.commute_roundtrip_minutes is None
    assert (rec.time_used.fraction(), rec.energy_used.fraction()) == (213, F(343, 2))
    assert rec.commute_relative_burden.fraction() == 0


def test_hybrid_work_commutes_on_its_declared_onsite_days_only(bundle4a, rules):
    s = state_of(bundle4a, "u21.b52.hybrid")
    acts = [
        _act(bundle4a, "housing", "b52.housing.far"),
        _act(bundle4a, "food", "b52.food.fresh"),
        _act(bundle4a, "job", "b52.job.hybrid"),
        _act(bundle4a, "durable_good", "b52.durable.washer"),
    ]
    rec, _ = month_resources(rules, s, 1, acts)
    assert _table(rec)[("commute", "b52.job.hybrid")] == (F(32, 3), 8)
    assert (rec.onsite_visits, rec.job_workdays_sum) == (8, 20)
    assert (rec.time_used.fraction(), rec.energy_used.fraction()) == (F(671, 3), F(391, 2))


def test_a_month_without_work_has_no_work_or_commute_burden(bundle4a, rules):
    s = state_of(bundle4a, "u21.a51.near")
    acts = [_act(bundle4a, "housing", "a51.housing.near"), _act(bundle4a, "food", "a51.food.fresh")]
    rec, _ = month_resources(rules, s, 1, acts)
    assert (rec.commute_status, rec.job_workdays_sum, rec.onsite_visits) == ("no_onsite_work", 0, 0)
    assert rec.work_relative_burden.fraction() == 0
    assert {c.category for c in rec.contributions} == {"food_prep", "household_task"}


def test_a_part_time_job_uses_its_own_hours_and_onsite_days(bundle4a, rules):
    s = state_of(bundle4a, "u24.a51.c0")
    acts = [
        _act(bundle4a, "housing", "a51.housing.near"),
        _act(bundle4a, "food", "a51.food.basic"),
        _act(bundle4a, "job", "a51.job.parttime"),
    ]
    rec, _ = month_resources(rules, s, 1, acts)
    t = _table(rec)
    assert t[("work", "a51.job.parttime")] == (80, 80)
    assert t[("commute", "a51.job.parttime")] == (5, F(15, 4))
    assert (rec.time_used.fraction(), rec.energy_used.fraction()) == (137, F(443, 4))


def test_a_zero_price_activity_still_takes_time(bundle4a, rules):
    s = state_of(bundle4a, "u21.a51.near")
    acts = [*_near_month(bundle4a), _act(bundle4a, "experience", "a51.experience.park_festival")]
    rec, _ = month_resources(rules, s, 1, acts)
    assert _table(rec)[("experience", "a51.experience.park_festival")] == (6, 3)
    assert rec.time_used.fraction() == 241


# --------------------------------------------------------------------------
# 缺资料不当零
# --------------------------------------------------------------------------


def test_missing_housing_minutes_is_an_error_not_zero(bundle4a, rules):
    s = state_of(bundle4a, "u21.a51.near")
    home = _opp(bundle4a, "a51.housing.near")
    spec = {k: v for k, v in home.spec.items() if k != "commute_roundtrip_minutes"}
    acts = [
        Activity("housing", home.model_copy(update={"spec": spec}), 1),
        *_near_month(bundle4a)[1:],
    ]
    with pytest.raises(LifeContractError, match="通勤分钟缺失不当零"):
        month_resources(rules, s, 1, acts)


def test_missing_resource_facts_are_an_error_not_zero(bundle4a, rules):
    s = state_of(bundle4a, "u21.a51.near")
    food = _opp(bundle4a, "a51.food.fresh").model_copy(update={"resource": None})
    acts = [_act(bundle4a, "housing", "a51.housing.near"), Activity("food", food, 1)]
    with pytest.raises(LifeContractError, match="缺了不当零"):
        month_resources(rules, s, 1, acts)


# --------------------------------------------------------------------------
# 多份工作（r2）：各算各的往返，工时与负担相加
# --------------------------------------------------------------------------


def test_two_onsite_jobs_each_take_their_own_round_trips(bundle4a, rules):
    """线下文员（到岗 20 次）+ 线下兼职（到岗 10 次），住近处（往返 30 分钟）：通勤 10 + 5 小时。

    取代 r1 的"同月两份到岗工作一律拒绝"（所有者 2026-10-08 裁决：两份临时工资源够就能执行）。
    """
    s = state_of(bundle4a, "u21.a51.near")
    rec, short = month_resources(
        rules, s, 1, [*_near_month(bundle4a), _act(bundle4a, "job", "a51.job.parttime")]
    )
    t = _table(rec)
    assert t[("work", "a51.job.office")] == (160, 160)
    assert t[("work", "a51.job.parttime")] == (80, 80)
    assert t[("commute", "a51.job.office")] == (10, F(15, 2))
    assert t[("commute", "a51.job.parttime")] == (5, F(15, 4))
    assert (rec.onsite_visits, rec.job_workdays_sum) == (30, 30)
    # 235 + 80 + 5 = 320 ≤ 365；207 + 80 + 3.75 = 290.75 > 250：只有精力超
    assert (rec.time_used.fraction(), rec.energy_used.fraction()) == (320, F(1163, 4))
    assert [(x.resource, x.excess.fraction()) for x in short] == [("energy", F(163, 4))]
    assert any("各算各的往返" in n for n in rec.notes)


def test_cumulative_visits_may_exceed_calendar_days(bundle4a, rules):
    """两份全职线下岗位各到岗 20 次，累计 40 次——不是 40 个日历天，也不因此拒绝。"""
    s = revise(
        state_of(bundle4a, "u21.a51.near"),
        time_budget_hours=8760,
        energy=revise(state_of(bundle4a, "u21.a51.near").energy, level=900),
    )
    office = _opp(bundle4a, "a51.job.office")
    evening = office.model_copy(update={"opportunity_id": "a51.job.office_evening"})
    rec, short = month_resources(rules, s, 1, [*_near_month(bundle4a), Activity("job", evening, 1)])
    assert short == [] and rec.onsite_visits == 40
    assert _hours_of(rec, "commute") == 20  # 两份各 30 × 20 / 60 = 10


def test_remote_plus_onsite_commutes_only_for_the_onsite_job(bundle4a, rules):
    s = state_of(bundle4a, "u21.a51.near")
    rec, _ = month_resources(
        rules, s, 1, [*_near_month(bundle4a), _act(bundle4a, "job", "a51.job.remote")]
    )
    commute = [c.opportunity_id for c in rec.contributions if c.category == "commute"]
    assert commute == ["a51.job.office"]
    assert (rec.onsite_visits, rec.job_workdays_sum) == (20, 40)


# --------------------------------------------------------------------------
# 出局当月与未发生的月份
# --------------------------------------------------------------------------


def test_the_exit_month_checks_only_the_known_lower_bound(bundle4a, rules):
    """出局当月：已入账工资对应的 80 小时工作照记；线下岗位没有住房，通勤未知；只比已知下限。

    取代 r1 的"出局当月不判额度"（独立审核 F1）。
    """
    s = state_of(bundle4a, "u25.a51.c0")
    rec, short = month_resources(
        rules, s, 4, [_act(bundle4a, "job", "a51.job.parttime")], exit_month=True
    )
    assert short == [] and rec.resource_check == "known_lower_bound"
    # 已知用量（下限）写出来；剩余与“在额度内”的结论不写——完整用量未知
    assert (rec.time_used.fraction(), rec.energy_used.fraction()) == (80, 80)
    assert rec.time_remaining is None and rec.energy_remaining is None and rec.within_limits is None
    assert [(c.category, c.hours.fraction(), c.burden.fraction()) for c in rec.contributions] == [
        ("work", 80, 80)
    ]
    assert rec.commute_status == "unknown_no_housing" and rec.commute_relative_burden is None
    assert rec.housing_opportunity_id is None and rec.commute_roundtrip_minutes is None
    assert rec.work_relative_burden.fraction() == F(80, 150)
    assert rec.total_relative_burden is None
    assert any("不当零" in n for n in rec.notes)
    assert any("不是完整月份的检查" in n for n in rec.notes)


@pytest.mark.parametrize(
    ("change", "resource"),
    [({"time_budget_hours": 720}, "time"), ({"energy": {"level": 60}}, "energy")],
)
def test_known_work_over_a_limit_fails_even_in_the_exit_month(bundle4a, rules, change, resource):
    """80 小时、80 负担点的已知劳动，预算或额度只有 60：就算这个月会出局，也是超限。"""
    s = state_of(bundle4a, "u25.a51.c0")
    if "energy" in change:
        change = {"energy": revise(s.energy, **change["energy"])}
    rec, short = month_resources(
        rules, revise(s, **change), 4, [_act(bundle4a, "job", "a51.job.parttime")], exit_month=True
    )
    (x,) = short
    assert (x.resource, x.resource_check, x.month) == (resource, "known_lower_bound", 4)
    assert (x.limit.fraction(), x.used.fraction(), x.excess.fraction()) == (60, 80, 20)
    assert x.rules_revision == lr.RESOURCE_RULES_REVISION
    assert rec.within_limits is None  # 下限超了，但完整月份照样没有"结论"


def test_the_exit_month_boundary_is_exact(bundle4a, rules):
    s = state_of(bundle4a, "u25.a51.c0")
    job = [_act(bundle4a, "job", "a51.job.parttime")]
    _, short = month_resources(rules, revise(s, time_budget_hours=960), 4, job, exit_month=True)
    assert short == []  # 80 = 80
    _, short = month_resources(rules, revise(s, time_budget_hours=959.99), 4, job, exit_month=True)
    assert [(x.resource, x.excess.fraction()) for x in short] == [("time", F(1, 1200))]


def test_a_remote_exit_month_knows_its_commute_is_zero(bundle4a, rules):
    s = state_of(bundle4a, "u21.b52.remote")
    rec, _ = month_resources(
        rules, s, 4, [_act(bundle4a, "job", "b52.job.remote")], exit_month=True
    )
    assert (rec.commute_status, rec.onsite_visits) == ("no_onsite_work", 0)
    assert rec.commute_relative_burden.fraction() == 0
    assert any("确定为零的通勤" in n for n in rec.notes)


def test_months_after_an_exit_are_not_reached_not_zero_burden(bundle4a, rules):
    s = state_of(bundle4a, "u25.a51.c0")
    first, _ = month_resources(rules, s, 1, [])
    year = lr.resource_year(rules, s, [first])
    assert [m.month for m in year.months] == list(range(1, 13))
    rest = year.months[1:]
    assert {m.resource_check for m in rest} == {"not_reached"}
    assert all(m.contributions == () and m.time_used is None for m in rest)
    assert (year.monthly_time_budget.fraction(), year.monthly_energy_quota.fraction()) == (150, 150)


def test_month_records_keep_their_check_shapes(bundle4a):
    """三种检查互不混用：完整检查必须有结论；只查下限不能写结论或剩余；未发生的月份什么都没有。"""
    budget = ExactAmount.of(F(365))
    with pytest.raises(ValidationError, match="完整检查必须有"):
        MonthResources(
            month=1,
            resource_check="complete",
            time_budget=budget,
            energy_quota=budget,
            commute_status="computed",
        )
    with pytest.raises(ValidationError, match="不写剩余"):
        MonthResources(
            month=4,
            resource_check="known_lower_bound",
            time_budget=budget,
            energy_quota=budget,
            time_used=ExactAmount.of(F(80)),
            energy_used=ExactAmount.of(F(80)),
            within_limits=True,
            commute_status="unknown_no_housing",
        )
    rec, _ = month_resources(
        bundle4a.protocol.resource_rules,
        state_of(bundle4a, "u21.a51.near"),
        1,
        _near_month(bundle4a),
    )
    with pytest.raises(ValidationError, match="没有发生的月份"):
        revise(
            rec,
            resource_check="not_reached",
            time_used=None,
            time_remaining=None,
            energy_used=None,
            energy_remaining=None,
            within_limits=None,
        )


# --------------------------------------------------------------------------
# 契约
# --------------------------------------------------------------------------


def test_exact_amounts_carry_a_fraction_and_a_matching_float():
    a = ExactAmount.of(F(70, 3))
    assert (a.exact, a.fraction()) == ("70/3", F(70, 3))
    with pytest.raises(ValidationError):
        ExactAmount(exact="70/3", value=23.3)
    with pytest.raises(ValidationError):
        ExactAmount(exact="23.3", value=23.3)


@pytest.mark.parametrize(
    ("data", "why"),
    [
        ({"basis": "per_service_month", "hours": 30, "burden_per_hour": 0.5}, "quantity_scales"),
        ({"basis": "per_service_month", "hours": 30, "quantity_scales": False}, "burden_per_hour"),
        ({"basis": "per_occurrence", "burden_per_hour": 1, "quantity_scales": True}, "hours"),
        ({"basis": "passive", "hours": 1}, "不按小时计"),
        (
            {"basis": "per_use", "hours": 1.5, "burden_per_hour": 1, "quantity_scales": False},
            "uses_per_month",
        ),
        (
            {
                "basis": "per_service_month",
                "hours": 1,
                "burden_per_hour": 1,
                "quantity_scales": False,
                "uses_per_month": 3,
            },
            "uses_per_month",
        ),
        ({"basis": "job"}, "岗位方案"),
        (
            {
                "basis": "passive",
                "substitutes": [
                    {"task": "laundry", "residual_hours_per_month": 3},
                    {"task": "laundry", "residual_hours_per_month": 1},
                ],
            },
            "只能写一条",
        ),
    ],
)
def test_resource_facts_must_say_what_is_counted(data, why):
    with pytest.raises(ValidationError, match=why):
        ResourceProfile.model_validate({**data, "note": "测试"})


@pytest.mark.parametrize(
    ("mode", "days", "onsite"),
    [
        ("onsite", 20, 0),  # 全职线下却零到岗：不能保留工资、取消到岗
        ("onsite", 20, 19),
        ("remote", 20, 1),
        ("hybrid", 20, 0),
        ("hybrid", 20, 20),
        ("hybrid", 10, 11),
    ],
)
def test_a_job_arrangement_matches_its_work_mode(mode, days, onsite):
    with pytest.raises(ValidationError):
        JobArrangement(
            work_mode=mode,
            work_hours_per_month=160,
            workdays_per_month=days,
            onsite_days_per_month=onsite,
            burden_per_work_hour=1,
        )


def test_a_paid_job_cannot_have_zero_hours():
    with pytest.raises(ValidationError):
        JobArrangement(
            work_mode="remote",
            work_hours_per_month=0,
            workdays_per_month=20,
            onsite_days_per_month=0,
            burden_per_work_hour=1,
        )


def test_a_quota_needs_its_budget_basis_and_must_be_positive(bundle4a):
    s = state_of(bundle4a, "u21.a51.near")
    with pytest.raises(ValidationError, match="必须一起出现"):
        revise(s, resource_budget_basis=None)
    with pytest.raises(ValidationError, match="必须一起出现"):
        revise(s, energy=revise(s.energy, scale_version="synthetic_fixture_only_energy_v0"))
    with pytest.raises(ValidationError, match="必须为正"):
        revise(s, energy=revise(s.energy, level=0))


def test_resource_rules_reject_inputs_finer_than_two_decimals(rules):
    with pytest.raises(ValidationError, match="精度细于"):
        revise(rules, commute_burden_per_hour=0.755)
    tasks = [t.model_dump() for t in rules.household_tasks]
    with pytest.raises(ValidationError, match="重复"):
        revise(rules, household_tasks=[*tasks, tasks[0]])


def test_the_new_rules_version_requires_resource_rules(raw4a):
    raw4a["protocol"].pop("resource_rules")
    with pytest.raises(LifeContractError, match="必须声明 resource_rules"):
        parse_bundle(raw4a)


def test_an_old_rules_version_cannot_carry_resource_rules(raw2, raw4a):
    raw2["protocol"]["resource_rules"] = raw4a["protocol"]["resource_rules"]
    with pytest.raises(LifeContractError, match="不读月度资源规则"):
        parse_bundle(raw2)


# --------------------------------------------------------------------------
# 模块边界：资源纯函数不读偏好、评分或加载器
# --------------------------------------------------------------------------


def _project_imports(path: str) -> set[str]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
    roots = {"common", "econ", "ledger", "agentlayer", "worlds", "eval"}
    return {n for n in names if n.split(".")[0] in roots}


def test_the_resource_module_depends_only_on_the_common_contracts():
    assert _project_imports("src/econ/life_resources.py") == {"common.life_contracts"}


def test_the_loader_does_not_reach_into_econ():
    """加载器只检查资料，不另写一套通勤或精力算法，也不依赖结算。"""
    imports = _project_imports("src/ledger/life_catalog.py")
    assert not {n for n in imports if n.startswith("econ")}


def test_the_resource_functions_take_no_preferences_scores_or_reasons():
    """签名里只有规则、状态、月份与实际活动：没有偏好真值、陈述画像、评分规格或带理由的计划。"""
    banned = (
        "InternalPreferenceTruth",
        "StatedLifeProfile",
        "ScoringPreference",
        "LifeScoringSpec",
        "CityScoringConditions",
        "YearAction",
    )
    for fn in (lr.month_resources, lr.household_rows, lr.timed_hours, lr.resource_year):
        for p in inspect.signature(fn).parameters.values():
            assert not any(b in str(p.annotation) for b in banned), (fn.__name__, p)


def test_catalog_lookup_helper_matches_the_state_city(bundle4a):
    s = state_of(bundle4a, "u21.b52.remote")
    assert catalog_of(bundle4a, s).city.code == "B52"
