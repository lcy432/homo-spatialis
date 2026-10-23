"""生活扩展（DECISIONS/0041）：完整食品方案、个人消费与享受、装备与活动、兴趣匹配。

全部用受控计划在合成样例 K51 上跑（不调用任何模型）。手算口径（评分规格 configs/life/scoring_product.yaml，全是探索设定）：

* 饮食生活 z = 1·新鲜餐/周 + 0.5·合口味餐/周，τ = 7，c = 0.9；样例用户确认的菜系是川菜（sichuan）；
* 个人消费与享受 z = Σ η_f v_f：居家舒适（presence，η = 1.5）、放松护理（每次 η = 1.0），τ = 3，c = 0.9；
* 休闲 z = 城市背景 4 + 锻炼次数 + 1·Σ 参与小时 ×（1 + 0.5·匹配），τ = 12，c = 0.9；样例用户确认的兴趣是音乐（music）。

浮点比较：每个分项只有几次加法与一次除法，按 1e-12 比较；金额按最小单位精确比较。
"""

from __future__ import annotations

import math

import pytest

from common.life_contracts import (
    ActivityFacts,
    CityLifeCatalog,
    IllnessEventSource,
    InjectedIllness,
    ParticipationRecord,
)
from common.life_scoring_contracts import ParticipationSpec, ScoringComponent
from common.money import Money

from .conftest import CLONE_K, HEALTHY, K51, live_year, once, whole

TOL = 1e-12


def patched(bundle, oid: str, **update) -> CityLifeCatalog:
    """K51 目录里一项机会改几个字段（改后重新校验）。"""
    data = bundle.catalog_for(K51).model_dump(mode="json")
    for o in data["opportunities"]:
        if o["opportunity_id"] == oid:
            for key, value in update.items():
                if isinstance(value, dict) and isinstance(o.get(key), dict):
                    o[key] = {**o[key], **value}
                else:
                    o[key] = value
    return CityLifeCatalog.model_validate(data)


def comp(year, month: int, cid: str):
    m = next(x for x in year.score.months if x.month == month)
    return next(c for c in m.components if c.component_id == cid)


def items_of(year, oid: str):
    return next(i for i in year.settlement.outcome.items if i.opportunity_id == oid)


def expenses(year, oid: str) -> int:
    """这一年某项机会的实际支出（最小单位，正数）。"""
    return -sum(
        round(f.amount.amount * 100)
        for f in year.settlement.outcome.cash_flows
        if f.opportunity_id == oid
    )


def parts(year, oid: str):
    return [p for p in year.settlement.outcome.participation if p.opportunity_id == oid]


def plan(food: str = "k51.food.basic", extra=()):
    return [whole("k51.housing.far"), whole(food), whole("k51.job.clerk"), *extra]


def neutral(bundle):
    """同一个用户，兴趣与口味都是“通用评价 / 没有偏好”（点数、现金、资格都不变）。"""
    return bundle.product_profiles[0].model_copy(
        update={
            "leisure_interest_status": "generic",
            "leisure_interests": (),
            "taste_status": "no_preference",
            "cuisines": (),
        }
    )


@pytest.fixture
def start(bundle):
    return bundle.initial_state(CLONE_K)


# --------------------------------------------------------------------------- 1. 食品不重复扣费


def test_a_dining_plan_is_charged_once_and_scored_from_the_same_plan(bundle, scoring, start):
    y = live_year(bundle, scoring, start, plan("k51.food.home_and_dining"))
    assert (
        expenses(y, "k51.food.home_and_dining") == 12 * 62000
    )  # 每月 620：外食已含在月费里，不另收
    food_flows = [
        f
        for f in y.settlement.outcome.cash_flows
        if f.opportunity_id and ".food." in f.opportunity_id
    ]
    assert [round(f.amount.amount * 100) for f in food_flows] == [-62000] * 12  # 每月恰好一笔
    food = comp(y, 1, "food_life")
    assert food.food_life.opportunity_id == "k51.food.home_and_dining"
    assert food.food_life.fresh_meals_per_week == 14  # 自己做 10 + 外食 4
    assert food.food_life.taste_matched_meals_per_week == 0  # 意式与家常都不在确认的口味里
    assert food.s == pytest.approx(14 / 21, abs=TOL)
    month = y.settlement.outcome.resources.months[0]
    hours = sum(
        c.hours.fraction()
        for c in month.contributions
        if c.opportunity_id == "k51.food.home_and_dining"
    )
    assert hours == 36  # 做饭 28 + 外食 8：时间与费用读同一份方案


def test_the_minimum_check_only_checks_and_a_downgrade_is_read_everywhere(bundle, scoring, start):
    y = live_year(bundle, scoring, start, plan())
    rec = y.settlement.outcome.months[0]
    spent = round(rec.spent.amount * 100)
    assert spent == 100000 + 38000  # 只付了远处住房 1000 与基本食品 380：最低生活检查没有另扣一次
    # 买不起的方案按声明降级：费用、时间与评分都读实际吃的最低方案
    cat = patched(
        bundle,
        "k51.food.home_and_dining",
        price={"amount": {"amount": 999999, "numeraire": "USD_2024_LOCAL"}},
    )
    d = live_year(bundle, scoring, start, plan("k51.food.home_and_dining"), catalog=cat)
    assert items_of(d, "k51.food.home_and_dining").status == "downgraded_to_minimum"
    assert expenses(d, "k51.food.home_and_dining") == 0
    assert expenses(d, "k51.food.basic") == 12 * 38000
    assert comp(d, 1, "food_life").food_life.opportunity_id == "k51.food.basic"
    assert comp(d, 1, "food_life").s == pytest.approx(0.3, abs=TOL)


def test_a_plan_that_cannot_keep_basic_life_exits_without_charging_food(bundle, scoring, start):
    broke = start.model_copy(update={"cash": Money(0.0, start.cash.numeraire)})
    y = live_year(bundle, scoring, broke, [whole("k51.housing.far"), whole("k51.food.basic")])
    assert y.settlement.outcome.settlement_status == "exited"
    assert expenses(y, "k51.food.basic") == 0
    assert y.score.months[0].status == "exit_month" and y.score.months[0].u is None


def test_an_incomplete_food_listing_cannot_be_an_executable_plan(bundle):
    data = bundle.catalog_for(K51).model_dump(mode="json")
    o = next(x for x in data["opportunities"] if x["opportunity_id"] == "k51.food.sichuan_home")
    o["food_plan"]["covers_basic_month"] = False  # 只有几样食材或一家餐馆，不是完整方案
    with pytest.raises(ValueError, match="完整方案"):
        CityLifeCatalog.model_validate(data)


# --------------------------------------------------------------------------- 2. 餐饮与活动不重复计分


def test_dining_counts_only_in_food_and_a_cooking_class_only_in_leisure(bundle, scoring, start):
    y = live_year(
        bundle,
        scoring,
        start,
        plan("k51.food.home_and_dining", [once("k51.experience.cooking_class", 4)]),
    )
    leisure = comp(y, 4, "leisure")
    assert [e.opportunity_id for e in leisure.participation.entries] == [
        "k51.experience.cooking_class"
    ]
    assert (
        comp(y, 4, "food_life").food_life == comp(y, 3, "food_life").food_life
    )  # 上课不改变饮食项
    assert comp(y, 3, "leisure").participation.entries == ()  # 外食不进休闲


def test_a_meal_replacing_package_is_refused():
    with pytest.raises(ValueError, match="替代正餐"):
        ActivityFacts(
            primary_category="culinary",
            participation_hours=2,
            overhead_hours=0,
            replaces_meals_per_session=1,
            time_basis="品鉴套餐代替一顿晚餐",
        )


# --------------------------------------------------------------------------- 3. 价格不是幸福


def test_changing_only_the_price_keeps_the_direct_scores(bundle, scoring, start):
    items = plan("k51.food.sichuan_home", [once("k51.experience.gallery_tour", 2)])
    a = live_year(bundle, scoring, start, items)
    cheap = patched(
        bundle,
        "k51.food.sichuan_home",
        price={"amount": {"amount": 590, "numeraire": "USD_2024_LOCAL"}},
    )
    cheap = CityLifeCatalog.model_validate(
        {
            **cheap.model_dump(mode="json"),
            "opportunities": [
                {
                    **o,
                    "price": {**o["price"], "amount": {"amount": 1, "numeraire": "USD_2024_LOCAL"}},
                }
                if o["opportunity_id"] == "k51.experience.gallery_tour"
                else o
                for o in cheap.model_dump(mode="json")["opportunities"]
            ],
        }
    )
    # 食品涨价（不能改得比声明的最低方案还便宜：那是资料矛盾，结算会拒绝）、导览降到 1 美元
    b = live_year(bundle, scoring, start, items, catalog=cheap)
    assert a.score is not None and b.score is not None
    for cid, month in (("food_life", 2), ("leisure", 2)):
        assert comp(a, month, cid).s == comp(b, month, cid).s
    assert a.settlement.outcome.cash_end != b.settlement.outcome.cash_end  # 钱改变，直接分项不变


def test_poorer_or_richer_states_do_not_change_the_saved_preference(bundle, scoring, start):
    from econ.life_scoring import preference_from_confirmed_profile

    p = bundle.product_profiles[0]
    assert preference_from_confirmed_profile(p) == preference_from_confirmed_profile(p)
    rich = start.model_copy(update={"cash": Money(90000.0, start.cash.numeraire)})
    a = live_year(bundle, scoring, start, plan())
    b = live_year(bundle, scoring, rich, plan())
    assert (
        a.score.months[0].dimensions == b.score.months[0].dimensions
    )  # 同样的生活、不同的余额：分数相同


# --------------------------------------------------------------------------- 4. 消费去重且不漏项


def test_same_function_goods_do_not_add_and_different_functions_do(bundle, scoring, start):
    one = live_year(bundle, scoring, start, plan(extra=[once("k51.durable.comfort_chair", 1)]))
    two = live_year(
        bundle,
        scoring,
        start,
        plan(extra=[once("k51.durable.comfort_chair", 1), once("k51.durable.cushion_set", 1)]),
    )
    assert comp(one, 2, "personal_enjoyment").z == pytest.approx(1.5, abs=TOL)
    assert comp(two, 2, "personal_enjoyment").z == pytest.approx(
        1.5, abs=TOL
    )  # 同一种居家舒适只算一份
    mixed = live_year(
        bundle,
        scoring,
        start,
        plan(extra=[once("k51.durable.comfort_chair", 1), whole("k51.service.massage", 2, 12)]),
    )
    e = comp(mixed, 2, "personal_enjoyment")
    assert e.z == pytest.approx(1.5 + 2 * 1.0, abs=TOL)  # 居家舒适 1.5 + 每月两次按摩 × 1.0
    assert e.s == pytest.approx(3.5 / 6.5, abs=TOL)
    assert comp(mixed, 1, "personal_enjoyment").z == pytest.approx(
        1.5, abs=TOL
    )  # 按摩从第 2 月才有


def test_an_unsupported_enjoyment_function_is_reported_not_zeroed(bundle, scoring, start):
    from econ.life_year import ScoringInputs

    spec = scoring.spec
    comps = []
    for c in spec.components:
        if c.component_id == "personal_enjoyment":
            es = c.enjoyment.model_copy(
                update={
                    "functions": tuple(
                        f for f in c.enjoyment.functions if f.function != "personal_care"
                    )
                }
            )
            c = c.model_copy(update={"enjoyment": es})
        comps.append(c)
    narrow = spec.model_copy(update={"components": tuple(comps)})
    p = bundle.product_profiles[0]
    inputs = ScoringInputs(
        spec=narrow,
        conditions=scoring.conditions_for(K51),
        preference_input=p,
        hard_requirements=(),
    )
    from econ.life_year import run_life_year

    from .conftest import tax_for, year_action

    policy = bundle.protocol.energy_feedback
    protocol = bundle.protocol.model_copy(
        update={
            "energy_feedback": policy.model_copy(
                update={"scoring_content_fingerprint": narrow.content_fingerprint}
            )
        }
    )  # 换了评分内容就是另一个实验身份：测试里显式声明它
    y = run_life_year(
        protocol=protocol,
        state=start,
        action=year_action(start, plan(extra=[whole("k51.service.massage", 1, 12)])),
        catalog=bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
        scoring=inputs,
    )
    e = comp(y, 1, "personal_enjoyment")
    assert e.status == "known" and e.z == 0
    assert e.enjoyment.unsupported == ("k51.service.massage:personal_care",)


# --------------------------------------------------------------------------- 5. 兴趣匹配有界


def test_a_confirmed_interest_raises_efficiency_for_the_same_participation(bundle, scoring, start):
    items = plan(extra=[once("k51.experience.community_choir", 3)])
    fan = live_year(bundle, scoring, start, items)
    plain = live_year(bundle, scoring, start, items, profile=neutral(bundle))
    f, n = comp(fan, 3, "leisure"), comp(plain, 3, "leisure")
    assert (
        f.participation.weighted_hours == 3 and n.participation.weighted_hours == 2
    )  # 2 小时 ×（1 + 0.5）对 ×1
    assert f.s == pytest.approx(7 / 19, abs=TOL) and n.s == pytest.approx(6 / 18, abs=TOL)
    # 只改兴趣：账目、参与与病情都不变
    assert fan.settlement.outcome.cash_flows == plain.settlement.outcome.cash_flows
    assert fan.settlement.outcome.participation == plain.settlement.outcome.participation
    assert (
        comp(fan, 2, "leisure").s == comp(plain, 2, "leisure").s
    )  # 没有活动的月份，匹配不放大城市背景


def test_tags_do_not_stack_and_splitting_records_does_not_add():
    from common.life_scoring_contracts import ScoringPreference
    from econ.life_scoring import _participation_term

    ps = ParticipationSpec(
        rule="participation_hours_interest_match_v1",
        eta_hour=1.0,
        match_bonus=0.5,
        unit="参与小时",
        definition="测试",
    )
    comp_ = ScoringComponent(
        component_id="leisure",
        dimension="leisure",
        unit="每月参与次数（折算）",
        tau=12,
        description="测试",
        participation=ps,
    )
    pref = ScoringPreference(
        person_id="u301",
        source="product_confirmed_six_dim",
        dimension_weights={
            "material": 0.5,
            "safety": 0.1,
            "opportunity": 0.1,
            "leisure": 0.1,
            "environment": 0.1,
            "commute": 0.1,
        },
        material_weights={"housing_space": 1.0},
        derivation="测试",
        leisure_interests=("music", "arts_culture"),
    )

    def rec(hours: float, n: float = 1) -> ParticipationRecord:
        return ParticipationRecord(
            plan_order=0,
            opportunity_id="x.choir",
            month=1,
            status="completed",
            primary_category="music",
            sessions=n,
            participation_hours=hours,
            overhead_hours=0,
        )

    one, _ = _participation_term(comp_, [], [rec(4, 2)], pref)
    split, _ = _participation_term(comp_, [], [rec(2), rec(2)], pref)
    assert (
        one.weighted_hours == split.weighted_hours == 6
    )  # 4 小时 × 1.5，不因记录条数增加；标签 arts_culture 不再乘一次


# --------------------------------------------------------------------------- 6. 同月买、同月用


@pytest.mark.parametrize("order", ["guitar_first", "practice_first"])
def test_a_guitar_bought_this_month_can_be_played_this_month(bundle, scoring, start, order):
    buy, play = (
        once("k51.durable.guitar", 2),
        {"opportunity_id": "k51.experience.guitar_practice", "quantity": 2, "months": [2]},
    )
    extra = [buy, play] if order == "guitar_first" else [play, buy]
    y = live_year(bundle, scoring, start, plan(extra=extra))
    (p,) = parts(y, "k51.experience.guitar_practice")
    assert (p.status, p.sessions, p.participation_hours, p.equipment_used) == (
        "completed",
        2,
        4,
        ("k51.durable.guitar",),
    )
    assert comp(y, 2, "leisure").participation.weighted_hours == 6  # 音乐是确认的兴趣：4 × 1.5


def test_failed_or_future_equipment_gives_no_ability(bundle, scoring, start):
    play = {"opportunity_id": "k51.experience.guitar_practice", "quantity": 1, "months": [2]}
    pricey = patched(
        bundle,
        "k51.durable.guitar",
        price={"amount": {"amount": 999999, "numeraire": "USD_2024_LOCAL"}},
    )
    y = live_year(
        bundle, scoring, start, plan(extra=[once("k51.durable.guitar", 2), play]), catalog=pricey
    )
    (p,) = parts(y, "k51.experience.guitar_practice")
    assert p.status == "failed" and "缺少装备能力" in p.reason
    assert expenses(y, "k51.experience.guitar_practice") == 0
    later = patched(
        bundle,
        "k51.durable.guitar",
        duration={"kind": "durable_good", "lasts_years": 3, "effective_from": "next_year"},
    )
    z = live_year(
        bundle, scoring, start, plan(extra=[once("k51.durable.guitar", 2), play]), catalog=later
    )
    assert (
        parts(z, "k51.experience.guitar_practice")[0].status == "failed"
    )  # 明确下一年才生效的装备今年不能用


def test_a_kit_session_works_without_owning_a_racket_and_leaves_none(bundle, scoring, start):
    y = live_year(
        bundle, scoring, start, plan(extra=[once("k51.experience.tennis_with_rental", 5)])
    )
    (p,) = parts(y, "k51.experience.tennis_with_rental")
    assert (
        p.status == "completed"
        and p.kit_capabilities == ("racket_playable",)
        and p.equipment_used == ()
    )
    assert y.next_state.holdings == ()  # 套餐不留下装备
    # 第二年同一项网球改成“自带球拍”：没有球拍就打不了，说明上一年的套餐没有留下永久能力
    own = patched(
        bundle,
        "k51.experience.tennis_with_rental",
        activity={"kit_provides": [], "requires": ["racket_playable"]},
    )
    later = live_year(
        bundle,
        scoring,
        y.next_state,
        [*plan(), once("k51.experience.tennis_with_rental", 1)],
        catalog=own,
    )
    (q,) = parts(later, "k51.experience.tennis_with_rental")
    assert q.status == "failed" and "racket_playable" in q.reason


def test_an_activity_paused_by_illness_has_no_hours_and_no_charge(bundle, scoring, start):
    ill = IllnessEventSource(
        mode="injected",
        stream_version="e1-5c-illness-stream-v1",
        injected=(
            InjectedIllness(
                person_id="u301",
                repeat_index=0,
                year_index=1,
                month=3,
                severity="major",
                note="测试：第 3 月大病",
            ),
        ),
        note="测试：指定事件",
    )
    protocol = bundle.protocol.model_copy(update={"illness_events": ill})
    y = live_year(
        bundle,
        scoring,
        start,
        plan(extra=[once("k51.experience.cooking_class", 3)]),
        protocol=protocol,
        emergency=HEALTHY,
    )
    (p,) = parts(y, "k51.experience.cooking_class")
    assert (
        p.status == "paused"
        and p.participation_hours == 0
        and expenses(y, "k51.experience.cooking_class") == 0
    )


def ill_in(bundle, month: int):
    ill = IllnessEventSource(
        mode="injected",
        stream_version="e1-5c-illness-stream-v1",
        injected=(
            InjectedIllness(
                person_id="u301",
                repeat_index=0,
                year_index=1,
                month=month,
                severity="major",
                note=f"测试：第 {month} 月月初大病",
            ),
        ),
        note="测试：指定事件",
    )
    return bundle.protocol.model_copy(update={"illness_events": ill})


def test_a_purchase_cancelled_when_ill_gives_no_ability(bundle, scoring, start):
    emergency = {
        **HEALTHY,
        "major": "treat_if_possible",
        "cancel_purchases_when_ill": ["k51.durable.guitar"],
    }
    play = {"opportunity_id": "k51.experience.guitar_practice", "quantity": 1, "months": [6]}
    y = live_year(
        bundle,
        scoring,
        start,
        plan(extra=[once("k51.durable.guitar", 3), play]),
        protocol=ill_in(bundle, 3),
        emergency=emergency,
    )
    assert items_of(y, "k51.durable.guitar").status == "cancelled_when_ill"
    assert expenses(y, "k51.durable.guitar") == 0
    (p,) = parts(y, "k51.experience.guitar_practice")
    assert (
        p.status == "failed" and "guitar_playable" in p.reason
    )  # 不顺延、不补购：病好了也没有吉他


# --------------------------------------------------------------------------- 7. 持有与寿命


def test_lifetime_counts_service_months_only_and_carries_across_years(bundle, scoring, start):
    y = live_year(
        bundle,
        scoring,
        start,
        plan(
            extra=[
                once("k51.durable.guitar", 1),
                {
                    "opportunity_id": "k51.experience.guitar_practice",
                    "quantity": 4,
                    "months": [1, 5, 9],
                },
            ]
        ),
    )
    hs = next(
        h for h in y.settlement.outcome.holdings_service if h.opportunity_id == "k51.durable.guitar"
    )
    assert hs.remaining_months_after == 36 - 12  # 练了 12 次也只按服务月扣寿命，不在“使用”里再扣
    (held,) = [
        h for h in y.settlement.next_state.holdings if h.opportunity_id == "k51.durable.guitar"
    ]
    assert held.effective_remaining_months == 24  # 跨年不重置


def test_the_last_month_of_life_then_expiry(bundle, scoring, start):
    short = patched(
        bundle, "k51.durable.guitar", duration={"kind": "durable_good", "lasts_years": 1}
    )
    y1 = live_year(
        bundle, scoring, start, plan(extra=[once("k51.durable.guitar", 2)]), catalog=short
    )
    y2 = live_year(
        bundle,
        scoring,
        y1.next_state,
        [
            *plan(),
            {"opportunity_id": "k51.experience.guitar_practice", "quantity": 1, "months": [1, 2]},
        ],
        catalog=short,
    )
    status = {p.month: p.status for p in parts(y2, "k51.experience.guitar_practice")}
    assert status == {1: "completed", 2: "failed"}  # 剩最后一个月就只支持一个月，之后到期不能用


def test_two_items_with_the_same_ability_do_not_double_a_session(bundle, scoring, start):
    y = live_year(
        bundle,
        scoring,
        start,
        plan(
            extra=[
                once("k51.durable.guitar", 1, quantity=2),
                {"opportunity_id": "k51.experience.guitar_practice", "quantity": 1, "months": [2]},
            ]
        ),
    )
    (p,) = parts(y, "k51.experience.guitar_practice")
    assert p.participation_hours == 2 and p.equipment_used == ("k51.durable.guitar",)
    assert comp(y, 2, "leisure").participation.weighted_hours == 3  # 2 小时 × 1.5，不因两把吉他翻倍
    assert comp(y, 2, "personal_enjoyment").z == 0  # 吉他不是享受型用品


# --------------------------------------------------------------------------- 8. 参与事实统一


def test_time_scoring_and_records_read_the_same_participation(bundle, scoring, start):
    y = live_year(
        bundle,
        scoring,
        start,
        plan(
            extra=[
                {
                    "opportunity_id": "k51.experience.community_choir",
                    "quantity": 2,
                    "months": [4, 7],
                }
            ]
        ),
    )
    for month in (4, 7):
        (p,) = [x for x in parts(y, "k51.experience.community_choir") if x.month == month]
        res = y.settlement.outcome.resources.months[month - 1]
        hours = sum(
            c.hours.fraction()
            for c in res.contributions
            if c.opportunity_id == "k51.experience.community_choir"
        )
        assert hours == p.participation_hours + p.overhead_hours == 5  # 2 次 ×（2 + 0.5）
        (entry,) = comp(y, month, "leisure").participation.entries
        assert entry.participation_hours == p.participation_hours == 4  # 往返不算享受时间
    assert {p.month for p in parts(y, "k51.experience.community_choir")} == {
        4,
        7,
    }  # 不连续的月份，同一个编号


# --------------------------------------------------------------------------- 生活故事（受控计划，不代表真实模型会这样选）


def test_story_cheap_tasty_food_is_not_judged_worse(bundle, scoring, start):
    cheap = live_year(bundle, scoring, start, plan("k51.food.sichuan_home"))
    pricey = live_year(bundle, scoring, start, plan("k51.food.home_and_dining"))
    # 川味家常：每周 21 餐都是川菜、其中 10 餐新鲜 → z = 10 + 0.5 × 21 = 20.5，s = 20.5 / 27.5 = 41/55
    assert comp(cheap, 1, "food_life").s == pytest.approx(41 / 55, abs=TOL)
    assert comp(cheap, 1, "food_life").s > comp(pricey, 1, "food_life").s
    assert expenses(cheap, "k51.food.sichuan_home") < expenses(pricey, "k51.food.home_and_dining")


def test_story_liking_comfort_without_buying_it_yet(bundle, scoring, start):
    y = live_year(bundle, scoring, start, plan())
    assert comp(y, 1, "personal_enjoyment").s == 0  # 喜欢，但这一年没买：确定的 0，不是“不喜欢”
    assert (
        bundle.product_profiles[0].material_points["personal_enjoyment"] == "20"
    )  # 保存的偏好不因没买而改变


def test_story_bought_but_never_practiced_gives_no_practice_benefit(bundle, scoring, start):
    y = live_year(bundle, scoring, start, plan(extra=[once("k51.durable.guitar", 2)]))
    assert all(comp(y, m, "leisure").participation.entries == () for m in range(1, 13))
    assert all(
        comp(y, m, "personal_enjoyment").z == 0 for m in range(1, 13)
    )  # 装备放在家里不算享受


def test_story_a_free_favorite_beats_a_pricey_unmatched_activity(bundle, scoring, start):
    choir = live_year(
        bundle, scoring, start, plan(extra=[once("k51.experience.community_choir", 6)])
    )
    gallery = live_year(
        bundle, scoring, start, plan(extra=[once("k51.experience.gallery_tour", 6)])
    )
    assert (
        expenses(choir, "k51.experience.community_choir")
        == 0
        < expenses(gallery, "k51.experience.gallery_tour")
    )
    assert comp(choir, 6, "leisure").s > comp(gallery, 6, "leisure").s
    assert math.isclose(comp(choir, 6, "leisure").s, 7 / 19, abs_tol=TOL)
    assert math.isclose(comp(gallery, 6, "leisure").s, 6 / 18, abs_tol=TOL)
