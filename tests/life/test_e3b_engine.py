"""E3B 收尾的引擎适配（DECISIONS/0042）：居住条件、按片区的地区条件、通勤关系、菜单、设备故障与真实税的生活规则。

全部用合成样例 K51 上的受控计划（不调用模型）。手算口径（评分规格 configs/life/scoring_product.yaml，探索设定）：

* 居住条件 z = 1.0 × 空间等级 + 0.5 × 条件档次，τ = 3，c = 0.9；样例两处住房都是一卧（空间 2）、中档（档次 2）→ z = 3、s = 0.5。
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from common.life_contracts import CityLifeCatalog

from .conftest import CLONE_K, K51, live_year, once, whole

TOL = 1e-12


def plan(housing: str = "k51.housing.far", extra=()):
    return [whole(housing), whole("k51.food.basic"), whole("k51.job.clerk"), *extra]


def catalog_with(bundle, change) -> CityLifeCatalog:
    """K51 目录的 JSON 改一处（``change`` 原地改整份目录的字典），再交给契约校验。"""
    data = bundle.catalog_for(K51).model_dump(mode="json")
    change(data)
    return CityLifeCatalog.model_validate(data)


def opp(data: dict, oid: str) -> dict:
    return next(o for o in data["opportunities"] if o["opportunity_id"] == oid)


def comp(year, month: int, cid: str):
    m = next(x for x in year.score.months if x.month == month)
    return next(c for c in m.components if c.component_id == cid)


def scored(bundle, scoring, state, items, *, catalog=None, conditions=None, hard=None):
    """结算并评分一年；可以换掉这座城的评分条件（评分规格不变，所以反馈政策的内容身份照旧对得上）与硬要求。"""
    from econ.life_year import ScoringInputs, run_life_year

    from .conftest import tax_for, year_action

    p = bundle.product_profiles[0]
    inputs = ScoringInputs(
        spec=scoring.spec,
        conditions=conditions or scoring.conditions_for(K51),
        preference_input=p,
        hard_requirements=p.hard_requirements if hard is None else hard,
    )
    return run_life_year(
        protocol=bundle.protocol,
        state=state,
        action=year_action(state, items),
        catalog=catalog or bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
        scoring=inputs,
    )


@pytest.fixture
def start(bundle):
    return bundle.initial_state(CLONE_K)


# --------------------------------------------------------------------------- 居住条件


def set_home(data, oid, **spec) -> None:
    o = opp(data, oid)
    o["spec"] = {**o["spec"], **spec}


def test_the_sample_homes_keep_their_hand_value(bundle, scoring, start):
    y = live_year(bundle, scoring, start, plan())
    assert comp(y, 1, "housing_condition").s == pytest.approx(3 / 6, abs=TOL)  # z = 2 + 0.5 × 2


def test_a_cheap_large_home_can_beat_an_expensive_small_one(bundle, scoring, start):
    cat = catalog_with(
        bundle,
        lambda d: (
            set_home(d, "k51.housing.far", bedrooms=3, space_level=4, quality_level=1),
            set_home(d, "k51.housing.near", bedrooms=0, space_level=1, quality_level=3),
        ),
    )
    big = live_year(bundle, scoring, start, plan("k51.housing.far"), catalog=cat)
    small = live_year(bundle, scoring, start, plan("k51.housing.near"), catalog=cat)
    assert comp(big, 1, "housing_condition").s == pytest.approx(4.5 / 7.5, abs=TOL)  # 4 + 0.5
    assert comp(small, 1, "housing_condition").s == pytest.approx(2.5 / 5.5, abs=TOL)  # 1 + 1.5
    assert comp(big, 1, "housing_condition").s > comp(small, 1, "housing_condition").s


def test_building_type_and_bedrooms_are_not_scored_again(bundle, scoring, start):
    """楼型不固定吃亏；卧室数只经由推断的空间等级起作用，不重复奖励。"""
    cat = catalog_with(
        bundle,
        lambda d: set_home(d, "k51.housing.far", bedrooms=3, building_type="apartment_10_plus"),
    )
    y = live_year(bundle, scoring, start, plan(), catalog=cat)
    assert comp(y, 1, "housing_condition").s == pytest.approx(
        0.5, abs=TOL
    )  # 只改卧室与楼型标签，空间等级没变


def test_changing_only_the_rent_keeps_the_housing_score(bundle, scoring, start):
    cat = catalog_with(
        bundle, lambda d: opp(d, "k51.housing.far")["price"]["amount"].update(amount=1100)
    )
    a = live_year(bundle, scoring, start, plan())
    b = live_year(bundle, scoring, start, plan(), catalog=cat)
    assert comp(a, 1, "housing_condition").s == comp(b, 1, "housing_condition").s
    assert a.settlement.outcome.cash_end != b.settlement.outcome.cash_end


# --------------------------------------------------------------------------- 按片区的地区条件


def add_home_area(d: dict, area_id: str, minutes: int) -> None:
    """加一个居住片区，并给它一条到就业中心的公共交通（往返 ``minutes`` 分钟、票价 0），通勤照样走得通。"""
    src = d["areas"][0]["typical_source"]
    d["areas"].append(
        {
            "area_id": area_id,
            "label": area_id,
            "kind": "home",
            "note": "测试片区",
            "typical_roundtrip_minutes": minutes,
            "typical_source": src,
        }
    )
    leg = dict(d["commute"]["legs"][0])
    d["commute"]["legs"].append({**leg, "home_area": area_id, "roundtrip_minutes": minutes})


def with_areas(d: dict) -> None:
    add_home_area(d, "k51.area.north", 70)
    add_home_area(d, "k51.area.south", 30)
    opp(d, "k51.housing.far")["area"] = "k51.area.north"
    opp(d, "k51.housing.near")["area"] = "k51.area.south"


def safety_by_area(scoring, north: float, south: float):
    cond = scoring.conditions_for(K51)
    comps = tuple(
        c.model_copy(
            update={
                "area_bases": {"k51.area.north": north, "k51.area.south": south},
                "area_basis": "测试：片区值",
            }
        )
        if c.component_id == "safety"
        else c
        for c in cond.components
    )
    return cond.model_copy(update={"components": comps})


def test_moving_changes_the_area_safety_and_replaces_the_city_value(bundle, scoring, start):
    cat = catalog_with(bundle, with_areas)
    cond = safety_by_area(scoring, north=1.0, south=5.0)
    north = scored(bundle, scoring, start, plan("k51.housing.far"), catalog=cat, conditions=cond)
    south = scored(bundle, scoring, start, plan("k51.housing.near"), catalog=cat, conditions=cond)
    a, b = comp(north, 1, "safety"), comp(south, 1, "safety")
    assert a.s == pytest.approx(1 / 4, abs=TOL) and b.s == pytest.approx(
        5 / 8, abs=TOL
    )  # τ = 3；不再叠城市值 3
    city = live_year(bundle, scoring, start, plan("k51.housing.far"))
    assert comp(city, 1, "safety").s == pytest.approx(3 / 6, abs=TOL)


def test_only_city_level_evidence_gives_every_area_the_same_value(bundle, scoring, start):
    cat = catalog_with(bundle, with_areas)
    cond = safety_by_area(scoring, north=3.0, south=3.0)
    a = scored(bundle, scoring, start, plan("k51.housing.far"), catalog=cat, conditions=cond)
    b = scored(bundle, scoring, start, plan("k51.housing.near"), catalog=cat, conditions=cond)
    assert comp(a, 1, "safety").s == comp(b, 1, "safety").s  # 不捏造片区差异


def test_a_home_outside_the_area_table_is_unknown_not_the_city_value(bundle, scoring, start):
    def partial(d: dict) -> None:
        with_areas(d)
        add_home_area(d, "k51.area.east", 70)
        opp(d, "k51.housing.far")["area"] = "k51.area.east"

    cat = catalog_with(bundle, partial)
    cond = safety_by_area(scoring, north=1.0, south=5.0)
    y = scored(bundle, scoring, start, plan("k51.housing.far"), catalog=cat, conditions=cond)
    s = comp(y, 1, "safety")
    assert s.status == "unknown" and s.s is None
    assert y.score.annual_status != "complete"


def test_an_undeclared_area_is_refused(bundle):
    with pytest.raises(ValueError, match="没有在 areas 里声明"):
        catalog_with(bundle, lambda d: opp(d, "k51.housing.far").update(area="k51.area.nowhere"))


# --------------------------------------------------------------------------- 通勤关系（DECISIONS/0042）
#
# 样例：远处片区到就业中心公共交通往返 70 分钟、近处 30 分钟，票价 0；文员每月到岗 20 次。下面的补丁加一条开车（远处 40 分钟、
# 每次往返油费 5.00，要有在用的 car_access）与一项每月 300 的用车权。


def with_car(d: dict, *, drive_minutes: int = 40, fuel: float = 5.0) -> None:
    rel = d["commute"]
    src = rel["modes"][0]["source"]
    rel["modes"].append(
        {
            "mode": "drive",
            "requires_function": "car_access",
            "note": "测试：要有用车权",
            "source": src,
        }
    )
    leg = dict(rel["legs"][1])  # 远处 → 就业中心
    rel["legs"].append(
        {
            **leg,
            "mode": "drive",
            "roundtrip_minutes": drive_minutes,
            "cost_per_roundtrip": {"amount": fuel, "numeraire": "USD_2024_LOCAL"},
        }
    )
    car = dict(opp(d, "k51.service.chores"))
    car.update(
        opportunity_id="k51.service.car_access",
        label="用车权（测试）",
        quality="按月付费的用车权：车辆、保险与登记",
        price={
            "amount": {"amount": 300, "numeraire": "USD_2024_LOCAL"},
            "billing": "per_month",
            "per_unit": "service-month",
        },
        functions=["car_access"],
        resource={
            "basis": "per_service_month",
            "hours": 0,
            "burden_per_hour": 0,
            "quantity_scales": False,
            "note": "不占时间",
        },
    )
    d["opportunities"].append(car)


def month_res(year, month: int = 1):
    return next(m for m in year.settlement.outcome.resources.months if m.month == month)


def commute_flows(year) -> list:
    return [f for f in year.settlement.outcome.cash_flows if f.label.startswith("通勤")]


def test_without_car_access_the_transit_line_is_used(bundle, scoring, start):
    cat = catalog_with(bundle, with_car)
    y = live_year(bundle, scoring, start, plan(), catalog=cat)
    c = month_res(y).commute
    assert [(u.mode, u.roundtrip_minutes, u.onsite_visits) for u in c.uses] == [("transit", 70, 20)]
    assert c.paid.amount == 0 and not commute_flows(y)


def test_with_car_access_the_fastest_mode_is_driving_and_costs_fuel(bundle, scoring, start):
    cat = catalog_with(bundle, with_car)
    y = live_year(
        bundle, scoring, start, plan(extra=[whole("k51.service.car_access")]), catalog=cat
    )
    m = month_res(y)
    c = m.commute
    assert [(u.mode, u.roundtrip_minutes) for u in c.uses] == [("drive", 40)]
    assert c.paid.amount == pytest.approx(100.0)  # 20 次 × 5.00
    hours = {r.category: r.hours.fraction() for r in m.contributions if r.category == "commute"}
    assert hours["commute"] == Fraction(40 * 20, 60)  # 时间账读同一份记录
    flows = [f for f in commute_flows(y) if f.month == 1]
    assert len(flows) == 1 and flows[0].amount.amount == pytest.approx(-100.0)
    t = comp(y, 1, "commute").transport
    assert t.minutes == 40 and t.onsite_visits == 20  # 交通评分也读它


def max_commute(minutes: int):
    from common.life_contracts import HardRequirement

    return (
        HardRequirement(
            need="housing_commute",
            requirement={"category": "housing", "max_commute_roundtrip_minutes": minutes},
        ),
    )


def test_the_commute_hard_requirement_reads_the_executed_trip(bundle, scoring, start):
    cat = catalog_with(bundle, with_car)
    transit = scored(bundle, scoring, start, plan(), catalog=cat, hard=max_commute(60))
    car = scored(
        bundle,
        scoring,
        start,
        plan(extra=[whole("k51.service.car_access")]),
        catalog=cat,
        hard=max_commute(60),
    )
    (t_req,) = transit.score.hard_requirements
    (c_req,) = car.score.hard_requirements
    assert t_req.summary == "not_satisfied"  # 公交 70 分钟 > 60
    assert c_req.summary == "satisfied_every_month"  # 开车 40 分钟


def test_a_monthly_cap_limits_the_fare(bundle, scoring, start):
    def capped(d: dict) -> None:
        rel = d["commute"]
        rel["modes"][0]["monthly_cap"] = {"amount": 100, "numeraire": "USD_2024_LOCAL"}
        for g in rel["legs"]:
            g["cost_per_roundtrip"] = {"amount": 5.8, "numeraire": "USD_2024_LOCAL"}

    y = live_year(bundle, scoring, start, plan(), catalog=catalog_with(bundle, capped))
    c = month_res(y).commute
    assert c.uses[0].cost.amount == pytest.approx(116.0)  # 20 × 5.80，封顶之前
    assert c.paid.amount == pytest.approx(100.0) and c.notes


def test_a_home_with_no_line_to_the_job_is_infeasible_not_zero_minutes(bundle, start):
    from econ.life_settlement import settle_year

    from .conftest import tax_for, year_action

    def no_line(d: dict) -> None:
        d["commute"]["legs"] = [g for g in d["commute"]["legs"] if g["home_area"] != "k51.area.far"]

    cat = catalog_with(bundle, no_line)
    r = settle_year(
        protocol=bundle.protocol,
        state=start,
        action=year_action(start, plan()),
        catalog=cat,
        tax=tax_for(bundle, K51),
    )
    assert r.status == "plan_infeasible"
    assert [p.code for p in r.problems] == ["commute_unavailable"]


def test_an_unaffordable_fare_is_infeasible(bundle, start):
    from econ.life_settlement import settle_year

    from .conftest import tax_for, year_action

    def dear(d: dict) -> None:
        for g in d["commute"]["legs"]:
            g["cost_per_roundtrip"] = {"amount": 900, "numeraire": "USD_2024_LOCAL"}

    r = settle_year(
        protocol=bundle.protocol,
        state=start,
        action=year_action(start, plan()),
        catalog=catalog_with(bundle, dear),
        tax=tax_for(bundle, K51),
    )
    assert r.status == "plan_infeasible"
    assert r.problems[0].code == "commute_unaffordable"


def test_a_month_without_onsite_work_reads_the_area_typical_minutes(bundle, scoring, start):
    items = [whole("k51.housing.far"), whole("k51.food.basic")]
    y = scored(bundle, scoring, start, items, hard=max_commute(60))
    m = month_res(y)
    assert m.commute.status == "no_onsite_work" and m.commute.home_area == "k51.area.far"
    t = comp(y, 1, "commute").transport
    assert t.minutes == 70 and t.onsite_visits == 0  # 远处片区的居民平时往返 70 分钟
    (req,) = y.score.hard_requirements
    assert req.months_satisfied and not req.months_not_satisfied and not req.months_unknown
    assert (
        1 in req.months_satisfied
    )  # 没有到岗：最长往返是 0（没有收入，钱花完后出局，之后的月份是没有继续生活）


def test_an_undeclared_commute_area_is_refused(bundle):
    def stray(d: dict) -> None:
        d["commute"]["legs"][0]["home_area"] = "k51.area.nowhere"

    with pytest.raises(ValueError, match="没有在 areas 里声明"):
        catalog_with(bundle, stray)


# --------------------------------------------------------------------------- 设备故障与替换（DECISIONS/0042）
#
# 便利设备包（1,440，寿命 5 年，洗衣残余 4、打扫残余 6 小时/月）加一条年故障概率 0.999999 的规则：月度风险
# h = 1 − (10⁻⁶)^(1/12) ≈ 0.684，固定种子下第一年里必然坏（具体月份由键决定，测试只要求它早于第 12 月）。

PACK = "k51.durable.convenience_pack"


def with_failure(d: dict, p: float = 0.999999) -> None:
    o = opp(d, PACK)
    o["failure"] = {
        "kind": "annual_hazard",
        "annual_probability": p,
        "note": "测试用的高故障概率",
        "source": o["source"],
    }


def failing_protocol(bundle, seed: int = 7):
    from common.life_contracts import DeviceFailureSource

    src = DeviceFailureSource(mode="random", pairing_id="test.pairing", seed=seed, note="测试")
    return bundle.protocol.model_copy(update={"device_failures": src})


def household_hours(year, month: int) -> Fraction:
    m = month_res(year, month)
    return sum(
        (r.hours.fraction() for r in m.contributions if r.category == "household_task"),
        Fraction(0),
    )


def buy_pack(replace: bool = False):
    items = plan(extra=[once(PACK, 1)])
    return items, ([{"held": PACK, "replacement": PACK}] if replace else [])


def run_with(bundle, scoring, start, items, replacements, catalog, protocol):
    from common.life_contracts import ReplacementRule
    from econ.life_year import run_life_year

    from .conftest import scoring_inputs, tax_for, year_action

    action = year_action(start, items).model_copy(
        update={"replacements": tuple(ReplacementRule(**r) for r in replacements)}
    )
    return run_life_year(
        protocol=protocol,
        state=start,
        action=action,
        catalog=catalog,
        tax=tax_for(bundle, K51),
        scoring=scoring_inputs(bundle, scoring, K51),
    )


def test_a_failed_device_stops_next_month_and_its_lifetime_is_not_kept(bundle, scoring, start):
    cat = catalog_with(bundle, with_failure)
    items, _ = buy_pack()
    y = run_with(bundle, scoring, start, items, [], cat, failing_protocol(bundle))
    (f,) = y.settlement.outcome.device_failures
    assert f.month < 12 and f.replacement_status == "not_declared"
    assert household_hours(y, f.month) == 4 + 6  # 坏的那个月还在用
    assert household_hours(y, f.month + 1) == 12 + 10  # 下个月起家务回到基准
    assert PACK not in {
        h.opportunity_id for h in y.next_state.holdings
    }  # 剩余寿命作废，不带进下一年


def test_a_declared_replacement_is_bought_the_next_month(bundle, scoring, start):
    cat = catalog_with(bundle, with_failure)
    items, rep = buy_pack(replace=True)
    y = run_with(bundle, scoring, start, items, rep, cat, failing_protocol(bundle))
    first = y.settlement.outcome.device_failures[0]
    assert first.replacement_status == "bought" and first.replacement_month == first.month + 1
    buys = [f for f in y.settlement.outcome.cash_flows if f.label.startswith("替换购入")]
    assert buys[0].month == first.month + 1 and buys[0].amount.amount == pytest.approx(-1440.0)
    assert household_hours(y, first.month + 1) == 4 + 6  # 替换当月生效


def test_failures_are_hidden_from_the_plan_check(bundle, start):
    from econ.life_settlement import check_year_plan

    from .conftest import tax_for, year_action

    cat = catalog_with(bundle, with_failure)
    items, _ = buy_pack()
    r = check_year_plan(
        protocol=failing_protocol(bundle),
        state=start,
        action=year_action(start, items),
        catalog=cat,
        tax=tax_for(bundle, K51),
    )
    assert r.passed and not r.problems  # 年初试算不抽签：计划检查看不到将来会不会坏


def test_draws_do_not_depend_on_plan_order(bundle, scoring, start):
    cat = catalog_with(bundle, with_failure)
    items, _ = buy_pack()
    a = run_with(bundle, scoring, start, items, [], cat, failing_protocol(bundle))
    b = run_with(bundle, scoring, start, list(reversed(items)), [], cat, failing_protocol(bundle))
    fa, fb = a.settlement.outcome.device_failures[0], b.settlement.outcome.device_failures[0]
    assert (fa.month, fa.key_fingerprint) == (fb.month, fb.key_fingerprint)


def test_no_random_failure_rule_never_fails(bundle, scoring, start):
    def none_rule(d: dict) -> None:
        o = opp(d, PACK)
        o["failure"] = {
            "kind": "none_within_horizon",
            "note": "三年内不随机坏",
            "source": o["source"],
        }

    items, _ = buy_pack()
    y = run_with(
        bundle, scoring, start, items, [], catalog_with(bundle, none_rule), failing_protocol(bundle)
    )
    assert not y.settlement.outcome.device_failures


def test_monthly_hazard_by_hand():
    from econ.life_failures import monthly_hazard

    assert float(monthly_hazard(0.1)) == pytest.approx(1 - 0.9 ** (1 / 12), abs=1e-15)
    assert monthly_hazard(0.1) == monthly_hazard(0.1)  # 十进制计算，逐位可重放


def test_a_replacement_that_is_not_a_durable_good_is_refused(bundle, start):
    from common.life_contracts import ReplacementRule
    from ledger.life_catalog import plan_problems

    from .conftest import year_action

    action = year_action(start, plan()).model_copy(
        update={"replacements": (ReplacementRule(held=PACK, replacement="k51.service.chores"),)}
    )
    probs = plan_problems(bundle.protocol, bundle.catalog_for(K51), action)
    assert [p.code for p in probs] == ["bad_replacement"]


# --------------------------------------------------------------------------- 年度菜单与延续（DECISIONS/0042）
#
# 岗位菜单：第 1 年环境重复 1 只有兼职，环境重复 2 只有会计；文员是开局岗位（可以延续）。住房菜单：第 1 年只有近处，第 2 年
# 只有远处；远处也是最低住房的保底方案，所以总在可选里。


def with_menus(d: dict) -> None:
    def menu(cat: str, year: int, env: int, ids: list[str]) -> dict:
        return {
            "menu_id": f"menu.k51.{cat}.y{year}.r{env}",
            "category": cat,
            "year": year,
            "env_repeat": env,
            "opportunity_ids": ids,
            "basis": "测试菜单（种子里没有分身编号）",
        }

    d["menus"] = [
        menu("job", 1, 1, ["k51.job.shop_parttime"]),
        menu("job", 1, 2, ["k51.job.accountant"]),
        menu("job", 2, 1, ["k51.job.shop_parttime"]),
        menu("housing", 1, 1, ["k51.housing.near"]),
        menu("housing", 1, 2, ["k51.housing.near"]),
        menu("housing", 2, 1, ["k51.housing.far"]),
    ]


def test_the_menu_and_continuations_are_what_can_be_seen_and_chosen(bundle, start):
    cat = catalog_with(bundle, with_menus)
    s1 = start.model_copy(update={"env_repeat": 1})
    offered = cat.offered_ids(s1)
    assert {"k51.job.shop_parttime", "k51.job.clerk"} <= offered  # 菜单 + 现在的岗位
    assert "k51.job.accountant" not in offered
    assert {"k51.housing.near", "k51.housing.far"} <= offered  # 菜单 + 最低住房保底
    assert "k51.food.basic" in offered  # 没有菜单的类别全部可选
    other = start.model_copy(update={"env_repeat": 2, "clone_id": "u301.k51.r9"})
    assert "k51.job.accountant" in cat.offered_ids(other)  # 环境重复不同，菜单不同


def test_choosing_off_the_menu_is_refused_by_the_authoritative_check(bundle, start):
    from ledger.life_catalog import plan_state_problems

    from .conftest import year_action

    cat = catalog_with(bundle, with_menus)
    s1 = start.model_copy(update={"env_repeat": 1})
    action = year_action(s1, plan(extra=[whole("k51.job.accountant")]))
    codes = [p.code for p in plan_state_problems(bundle.protocol, s1, action, cat)]
    assert "not_on_menu" in codes
    no_env = plan_state_problems(bundle.protocol, start, year_action(start, plan()), cat)
    assert [p.code for p in no_env if p.code == "menu_data"] == ["menu_data"]


def test_the_observation_shows_only_the_menu_and_the_lines_that_matter(bundle, start):
    from .test_views import _obs

    cat = catalog_with(bundle, with_menus)
    obs = _obs(bundle, state=start.model_copy(update={"env_repeat": 1}), catalog=cat)
    ids = {o.opportunity_id for o in obs.opportunities}
    assert "k51.job.accountant" not in ids and "k51.job.shop_parttime" in ids
    assert {a.area_id for a in obs.areas} == {"k51.area.near", "k51.area.far", "k51.area.center"}


def test_the_current_home_can_be_renewed_off_the_menu(bundle, scoring, start):
    cat = catalog_with(bundle, with_menus)
    s1 = start.model_copy(update={"env_repeat": 1})
    y1 = live_year(bundle, scoring, s1, plan("k51.housing.near"), catalog=cat)
    s2 = y1.next_state
    assert s2.residence == "k51.housing.near" and s2.env_repeat == 1
    assert "k51.housing.near" in cat.offered_ids(s2)  # 第 2 年菜单只有远处，续租近处照样可以
    y2 = live_year(bundle, scoring, s2, plan("k51.housing.near"), catalog=cat)
    assert y2.settlement.status == "completed"


def test_the_valuation_period_keeps_year_n_menus_and_draws_no_failures(bundle):
    from econ.life_terminal_value import _derived_protocol, valuation_catalog

    cat = catalog_with(bundle, with_menus)
    v = valuation_catalog(cat, 2)
    year3 = [m for m in v.menus if m.year == 3]
    assert {m.category for m in year3} == {"job", "housing"}
    assert all(m.menu_id.endswith(".valuation") for m in year3)
    vprotocol, _ = _derived_protocol(failing_protocol(bundle))
    assert vprotocol.device_failures is None


# --------------------------------------------------------------------------- 休闲背景（所有者 2026-10-23）


def test_lowering_the_leisure_background_moves_only_the_background_term(bundle, scoring, start):
    """休闲的城市背景 b 从 4 降到 1（E3B 资料包的共同先验；合成样例仍写 4，这里在评分条件上改）：结算、参与小时与其他各项都
    不变，休闲 z 每月正好少 3。没有活动的月份 s 从 4/16 = 1/4 变成 1/13；3 月合唱 2 小时（兴趣匹配 ×1.5，折 3 小时）从
    (4 + 3)/(7 + 12) = 7/19 变成 (1 + 3)/(4 + 12) = 1/4。"""
    cond = scoring.conditions_for(K51)
    data = cond.model_dump(mode="json")
    leisure = next(c for c in data["components"] if c["component_id"] == "leisure")
    assert leisure["base"] == 4 and not leisure.get("area_bases")
    leisure["base"] = 1
    low = type(cond).model_validate(data)
    items = plan(extra=[once("k51.experience.community_choir", 3)])
    old = scored(bundle, scoring, start, items)
    new = scored(bundle, scoring, start, items, conditions=low)
    assert new.settlement.outcome == old.settlement.outcome  # 结算不读评分条件

    def others(year, month):
        m = next(x for x in year.score.months if x.month == month)
        return [(c.component_id, c.s) for c in m.components if c.component_id != "leisure"]

    for month in range(1, 13):
        a, b = comp(old, month, "leisure"), comp(new, month, "leisure")
        assert a.z - b.z == pytest.approx(3, abs=TOL)
        assert a.participation == b.participation  # 活动的参与小时与兴趣加成不变
        assert others(old, month) == others(new, month)
    assert comp(old, 2, "leisure").s == pytest.approx(1 / 4, abs=TOL)
    assert comp(new, 2, "leisure").s == pytest.approx(1 / 13, abs=TOL)
    assert comp(old, 3, "leisure").s == pytest.approx(7 / 19, abs=TOL)
    assert comp(new, 3, "leisure").s == pytest.approx(1 / 4, abs=TOL)


def test_the_safety_background_never_touches_cash_illness_or_exit(bundle, scoring, start):
    """安全背景只进评分的安全一项：把 K51 的安全 b 从 3 改成 1.5（像换了一个犯罪率更高的都会区），结算的现金流、病情、出局
    与每月资源一字不差，别的评分项也不变；安全 s 从 3/6 变成 1.5/4.5。"""
    cond = scoring.conditions_for(K51)
    data = cond.model_dump(mode="json")
    safety = next(c for c in data["components"] if c["component_id"] == "safety")
    assert safety["base"] == 3
    safety["base"] = 1.5
    low = type(cond).model_validate(data)
    old = scored(bundle, scoring, start, plan())
    new = scored(bundle, scoring, start, plan(), conditions=low)
    o, n = old.settlement.outcome, new.settlement.outcome
    assert (n.cash_flows, n.health, n.exit, n.resources) == (
        o.cash_flows,
        o.health,
        o.exit,
        o.resources,
    )
    for month in range(1, 13):
        assert comp(old, month, "safety").s == pytest.approx(1 / 2, abs=TOL)
        assert comp(new, month, "safety").s == pytest.approx(1 / 3, abs=TOL)
        for cid in ("leisure", "commute", "housing_condition", "environment"):
            assert comp(old, month, cid).s == comp(new, month, cid).s
