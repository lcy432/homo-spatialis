"""E1-5C 收尾修订（DECISIONS/0027 收尾修订，2026-10-11）：独立审核发现的 F1、F2、F3。

* F1：观察构造与结算用同一份判断核对"状态的健康部分是不是由这套疾病规则产生"——规则冲突时观察不再给出错误的恢复日期；
* F2：年度计划的应急安排可以声明"购买当月生着病就取消这一次一次性耐用品购买"（``cancel_purchases_when_ill``）；
* F3：H(健康) 固定为 1，健康项的扣分 γ(1 − H) 就是相对健康的扣分；其他值在评分运行前被拒绝。

全部数字来自合成样例 ``configs/life/synthetic_e1_5c_health.yaml``：每月工资 1,500，住房加食品 1,380，门诊 300，住院 2,000，
洗烘一体机 2,400。金额一律换成整数分比较，资源用精确分数，不加容差。
"""

from __future__ import annotations

import json
from dataclasses import replace
from fractions import Fraction

import pytest
import yaml
from pydantic import ValidationError

from agentlayer.life_views import (
    ObservationPermission,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    RESERVED_HEALTH_KEYS,
    HealthState,
    LifeContractError,
    health_rules_state_problems,
)
from common.life_scoring_contracts import HealthStateValues
from econ.life_scoring import preference_from_stated_profile, score_year
from econ.life_settlement import settle_year
from econ.life_year import settle_and_score
from ledger.life_catalog import health_state_problems
from ledger.life_scoring_config import load_scoring_config

from .conftest import SCORING_HL
from .health_support import (
    COURSE_ID,
    DECLINE,
    DRYER_ID,
    FOOD,
    GYM,
    HOME,
    OFFICE,
    SHOP,
    TREAT,
    cash_ends,
    flows_for,
    health_months,
    injected,
    inputs,
    plan,
    run_clone,
    with_source,
    year,
)
from .settle_support import catalog_of, cents, revise, state_of, tax_of

CANCEL = "cancel_purchases_when_ill"
CONCERT = "a91.experience.concert"
GYM_ID = GYM["opportunity_id"]
CANCEL_LABEL = "按你的年度应急声明，因本月患病取消这次购买（不扣款、不顺延、不补购）"


def dryer(month: int, **kw):
    return {"opportunity_id": DRYER_ID, "quantity": 1, "month": month, **kw}


def with_cash(state, amount: float):
    return revise(state, cash=replace(state.cash, amount=amount))


def settle(bundle, state, action, protocol=None):
    catalog = catalog_of(bundle, state)
    return settle_year(
        protocol=protocol or bundle.protocol,
        state=state,
        action=action,
        catalog=catalog,
        tax=tax_of(bundle, catalog),
    )


def item(result, oid: str):
    return next(i for i in result.outcome.items if i.opportunity_id == oid)


def paid(result, oid: str) -> list[tuple[int, int]]:
    return [
        (f.month, cents(f.amount)) for f in result.outcome.cash_flows if f.opportunity_id == oid
    ]


def review_case(bundle):
    """独立审核的反例：u91 开局 4,000；第 3 月小病（门诊 300），第 4 月大病（住院 2,000）；第 3 月计划买 2,400 的洗烘一体机。"""
    start = with_cash(state_of(bundle, "u91.a91.treat"), 4000)
    proto = with_source(bundle.protocol, injected(("u91", 1, 3, "minor"), ("u91", 1, 4, "major")))
    return start, proto


def declared_plan(bundle, clone, year_index, *items, choices=None, cancel=(DRYER_ID,), **kw):
    emergency = {**(choices or TREAT), CANCEL: list(cancel)}
    emergency.update(kw)
    return plan(bundle, clone, year_index, *items, emergency=emergency)


def observe(bundle, state, health_rules, *, permission=None, last_feedback=None, protocol=None):
    p = protocol or bundle.protocol
    return build_observation(
        permission=permission or standard_product_permission(p.info_permission_version),
        profile=next(x for x in bundle.stated_profiles if x.person_id == state.person_id),
        state=state,
        catalog=catalog_of(bundle, state),
        last_feedback=last_feedback,
        resource_rules=p.resource_rules,
        energy_feedback=p.energy_feedback,
        career_rules=p.career_rules,
        savings_rules=p.savings_rules,
        health_rules=health_rules,
        health_input=p.health_input,
    )


def all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in all_keys(v)}
    return set()


def longer_major(rules):
    """同一编号、同一版本，只把大病不治的病程从 3 个月改成 5 个月。"""
    return revise(rules, major=revise(rules.major, untreated_months=5))


# ==========================================================================
# F1：观察与结算对同一个规则冲突给出同一个判断
# ==========================================================================


def test_observation_and_settlement_refuse_the_same_rules_conflict(bundle_hl, scoring_hl):
    """u97 第 1 年第 12 月得大病不治，带着病了 1 个月的大病进入第 2 年。原规则：不治共病 3 个月，第 2 年第 2 月月末恢复。"""
    s2 = run_clone(bundle_hl, scoring_hl, "u97.a91.crossyear")[0].next_state
    rules = bundle_hl.protocol.health_rules
    longer = longer_major(rules)
    assert (
        longer.version == rules.version and longer.content_fingerprint != rules.content_fingerprint
    )
    ok = observe(bundle_hl, s2, rules)
    assert ok.health.ongoing.natural_end == "不治疗的话，第 2 年第 2 月月末恢复（已经病了 1 个月）"

    r = settle(
        bundle_hl,
        s2,
        bundle_hl.plans_for("u97.a91.crossyear")[1],
        protocol=revise(bundle_hl.protocol, health_rules=longer),
    )
    assert r.status == "input_error" and r.outcome is None
    (problem,) = [p for p in r.problems if p.code == "health_rules_changed"]
    with pytest.raises(LifeContractError) as exc:
        observe(bundle_hl, s2, longer)
    message = str(exc.value)
    assert f"[health_rules_changed] {problem.message}" in message  # 同一个代码、同一句说明
    assert "第 2 年第 4 月" not in message  # 不先给出错误的恢复日期
    # 同一份判断：common 的函数给出的正是两边用的那一条
    assert health_rules_state_problems(longer, s2) == [("health_rules_changed", problem.message)]


def test_the_rules_check_does_not_depend_on_display_switches(bundle_hl, scoring_hl):
    s2 = run_clone(bundle_hl, scoring_hl, "u97.a91.crossyear")[0].next_state
    hidden = ObservationPermission(
        condition="product_stated_profile",
        show_stated_dimension_points=False,
        show_soft_preferences=False,
        show_hard_requirements=False,
        show_free_text=False,
        show_energy_level=False,
        show_stated_material_points=False,
        permission_version="test",
    )
    with pytest.raises(LifeContractError, match="health_rules_changed"):
        observe(bundle_hl, s2, longer_major(bundle_hl.protocol.health_rules), permission=hidden)


def test_an_unbound_later_state_is_refused_without_guessing(bundle_hl, scoring_hl):
    s2 = run_clone(bundle_hl, scoring_hl, "u97.a91.crossyear")[0].next_state
    rules = bundle_hl.protocol.health_rules
    # 第 2 年、健康部分看起来像共同初始状态（健康、没有病史、没有负担记录）却没有绑定：不猜它来自哪套规则
    bare = revise(
        s2, health=HealthState(age_years=s2.health.age_years, repeat_index=s2.health.repeat_index)
    )
    expected = health_rules_state_problems(rules, bare)
    assert [code for code, _ in expected] == ["health_state"] and "没有绑定" in expected[0][1]
    assert [
        (p.code, p.message) for p in health_state_problems(bundle_hl.protocol, bare)
    ] == expected
    with pytest.raises(LifeContractError, match="没有绑定"):
        observe(bundle_hl, bare, rules)
    # 有病情的状态连构造都不允许不绑定——不会被自动补上绑定
    with pytest.raises(ValidationError, match="binding"):
        revise(s2.health, binding=None)


def test_the_common_initial_state_serves_arms_with_different_rules(bundle_hl):
    start = state_of(bundle_hl, "u91.a91.treat")
    assert start.health.binding is None
    rules = bundle_hl.protocol.health_rules
    for arm in (rules, longer_major(rules)):
        assert health_rules_state_problems(arm, start) == []
        obs = observe(bundle_hl, start, arm)
        assert obs.health.severity_rules[1].months_if_untreated == arm.major.untreated_months
        r = settle(
            bundle_hl,
            start,
            bundle_hl.plans_for("u91.a91.treat")[0],
            protocol=revise(bundle_hl.protocol, health_rules=arm),
        )
        assert r.status == "completed"


def test_the_default_observation_adds_no_hidden_information(bundle_hl, scoring_hl):
    y1 = run_clone(bundle_hl, scoring_hl, "u97.a91.crossyear")[0]
    obs = observe(
        bundle_hl,
        y1.next_state,
        bundle_hl.protocol.health_rules,
        last_feedback=visible_feedback(y1.settlement),
    )
    payload = observation_payload(obs)
    assert set(payload["health"]) == {
        "rules_version",
        "age_years",
        "state",
        "state_label",
        "ongoing",
        "history",
        "severity_rules",
        "treatments",
        "rules",
        "information",
    }
    text = json.dumps(payload, ensure_ascii=False)
    for secret in (
        bundle_hl.protocol.health_rules.content_fingerprint,
        bundle_hl.protocol.illness_events.content_fingerprint,
    ):
        assert secret not in text
    assert not all_keys(payload) & (RESERVED_HEALTH_KEYS | {"binding", "rules_fingerprint"})
    # 本轮唯一新增的公开文字：取消声明的规则说明（分身先要知道有这个选项）
    assert sum(CANCEL in line for line in payload["health"]["rules"]) == 1


# ==========================================================================
# F2：生病月份按声明取消尚未执行的一次性耐用品购买
# ==========================================================================


def test_the_review_example_runs_through_the_new_declaration(bundle_hl, scoring_hl):
    start, proto = review_case(bundle_hl)
    clone = "u91.a91.treat"
    plain = plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(3))
    declared = declared_plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(3))
    a = year(bundle_hl, scoring_hl, start, plain, protocol=proto)
    b = year(bundle_hl, scoring_hl, start, declared, protocol=proto)

    # 没有声明：第 3 月末 4,240 + 1,500 − 1,380 − 300 − 2,400 = 1,660；第 4 月没有工资，付生活费后 280，住不起院；第 5 月出局
    assert a.settlement.status == "exited" and a.settlement.outcome.exit.month == 5
    assert cash_ends(a) == [412000, 424000, 166000, 28000, 28000]
    assert health_months(a)[3][4] == "unaffordable"
    assert item(a.settlement, DRYER_ID).status == "executed"

    # 有声明：购买仍在计划里，由执行器因患病取消：4,240 + 1,500 − 1,380 − 300 = 4,060；第 4 月 4,060 − 1,380 − 2,000 = 680
    assert b.settlement.status == "completed" and b.handoff == "next_year_ready"
    assert cash_ends(b) == [412000, 424000, 406000, 68000] + [
        68000 + 12000 * k for k in range(1, 9)
    ]
    hm = health_months(b)
    assert hm[2] == (
        3,
        "minor",
        "lived",
        "recovered_treated",
        "treated",
    )  # 月末治好，这个月仍是患病月份
    assert hm[3] == (4, "major", "lived", "recovered_treated", "treated")
    d = item(b.settlement, DRYER_ID)
    assert d.status == "cancelled_when_ill" and cents(d.amount_paid) == 0 and d.covered_months == ()
    assert [(p.month, p.kind) for p in d.months_paused] == [(3, "agent_cancel_when_ill")]
    assert d.months_paused[0].reason.startswith("按年度应急声明，因本月患病取消购买")
    assert d.rejections == ()  # 不是被拒：没有去查报价与现金
    assert flows_for(b, DRYER_ID) == []
    assert DRYER_ID not in {h.opportunity_id for h in b.next_state.holdings}
    out = b.settlement.outcome
    assert [(x.opportunity_id, x.kind) for x in out.health.months[2].paused] == [
        (DRYER_ID, "agent_cancel_when_ill")
    ]
    assert out.health.cancel_purchases_when_ill == (DRYER_ID,)
    assert b.score.annual_status == "complete"


def test_healthy_months_buy_and_undeclared_plans_keep_the_old_behaviour(bundle_hl):
    clone = "u91.a91.treat"
    rich = with_cash(state_of(bundle_hl, clone), 10000)  # 样例的指定事件：第 3 月小病
    # 声明了，但购买月健康：照常买。第 2 月 10,240 − 2,400 = 7,840
    r = settle(bundle_hl, rich, declared_plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(2)))
    assert item(r, DRYER_ID).status == "executed" and cents(r.outcome.months[1].cash_end) == 784000
    # 没有声明：患病月照旧购买（旧行为）。第 3 月 10,240 + 1,500 − 1,380 − 300 − 2,400 = 7,660
    old = plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(3))
    r_old = settle(bundle_hl, rich, old)
    assert item(r_old, DRYER_ID).status == "executed"
    assert cents(r_old.outcome.months[2].cash_end) == 766000
    # 空列表与不写是同一份计划、同一份结果；不用时新字段不出现在输出里
    empty = declared_plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(3), cancel=())
    assert empty.model_dump(mode="json") == old.model_dump(mode="json")
    assert CANCEL not in json.dumps(old.model_dump(mode="json"))
    r_empty = settle(bundle_hl, rich, empty)
    assert r_empty.outcome.model_dump_json() == r_old.outcome.model_dump_json()
    assert CANCEL not in r_old.outcome.model_dump_json()


def test_new_and_continuing_illness_cancel_alike(bundle_hl, scoring_hl):
    # 新发：u91 第 3 月月初新发小病
    rich = with_cash(state_of(bundle_hl, "u91.a91.treat"), 10000)
    r = settle(
        bundle_hl, rich, declared_plan(bundle_hl, "u91.a91.treat", 1, HOME, FOOD, SHOP, dryer(3))
    )
    assert r.outcome.health.months[2].onset == "minor"
    assert item(r, DRYER_ID).status == "cancelled_when_ill"
    # 延续：u97 第 1 年第 12 月的大病不治，第 2 年第 2 月仍在病中（病的第 3 个月）
    s2 = with_cash(run_clone(bundle_hl, scoring_hl, "u97.a91.crossyear")[0].next_state, 10000)
    a2 = declared_plan(
        bundle_hl, "u97.a91.crossyear", 2, HOME, FOOD, SHOP, dryer(2), choices=DECLINE
    )
    r2 = settle(bundle_hl, s2, a2)
    m2 = r2.outcome.health.months[1]
    assert (m2.state, m2.onset, m2.episode_month) == ("major", None, 3)
    assert item(r2, DRYER_ID).status == "cancelled_when_ill"


def test_a_cancellation_stays_in_its_month_and_held_items_keep_serving(bundle_hl):
    _, proto = review_case(bundle_hl)  # 第 3 月小病、第 4 月大病
    clone = "u91.a91.treat"
    rich = with_cash(state_of(bundle_hl, clone), 10000)
    concert = {"opportunity_id": CONCERT, "quantity": 1, "month": 5}
    # 第 2 月（健康）买下；第 3、4 月生病：已经持有的照样服务、不退款、不没收；第 5 月（健康）的音乐会照常
    r = settle(
        bundle_hl,
        rich,
        declared_plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(2), concert),
        protocol=proto,
    )
    assert item(r, DRYER_ID).status == "executed" and item(r, CONCERT).status == "executed"
    held = next(h for h in r.outcome.holdings_service if h.opportunity_id == DRYER_ID)
    assert held.covered_months == tuple(range(2, 13))
    assert paid(r, DRYER_ID) == [(2, -240000)]
    # 前几个月的病不会把之后健康月份的购买一起取消：第 6 月照常买
    r6 = settle(
        bundle_hl,
        rich,
        declared_plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(6)),
        protocol=proto,
    )
    d = item(r6, DRYER_ID)
    assert d.status == "executed" and d.covered_months == (6,)


def test_a_cancelled_linked_purchase_leaves_the_goal_unrealized(bundle_hl, scoring_hl):
    # u97 的资金目标：第 2 年第 6 月买洗烘一体机。这里把关联的购买放在第 2 月（仍在病中）并声明生病就取消
    s2 = run_clone(bundle_hl, scoring_hl, "u97.a91.crossyear")[0].next_state
    a2 = declared_plan(
        bundle_hl,
        "u97.a91.crossyear",
        2,
        HOME,
        FOOD,
        SHOP,
        dryer(2, goal_id="dryer"),
        choices=DECLINE,
    )
    r = settle(bundle_hl, s2, a2)
    assert r.status == "completed" and item(r, DRYER_ID).status == "cancelled_when_ill"
    sv = r.outcome.savings
    assert sv.realization is None
    (due,) = [e for e in sv.events if e.kind == "due_unrealized"]
    assert due.month == 6
    assert "关联的购买已取消（第 2 月按年度应急声明、因当月患病取消" in due.note
    assert "被拒" not in due.note
    goal = next(g for g in r.next_state.savings_goals if g.goal_id == "dryer")
    assert goal.status == "overdue" and r.next_state.status == "active"  # 到期未兑现不是出局
    # 同一本现金账：第 6 月末 1,180 + 4 × 120 = 1,660，没有隐藏准备金
    assert cents(r.outcome.months[1].cash_end) == 118000
    assert cents(r.outcome.months[5].cash_end) == 166000


def test_cash_resources_scores_and_next_state_agree_after_a_cancellation(bundle_hl, scoring_hl):
    start, proto = review_case(bundle_hl)
    clone = "u91.a91.treat"
    b = year(
        bundle_hl,
        scoring_hl,
        start,
        declared_plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(3)),
        protocol=proto,
    )
    a = settle(bundle_hl, start, plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(3)), proto)
    out = b.settlement.outcome
    assert cents(start.cash) + sum(cents(f.amount) for f in out.cash_flows) == cents(
        b.next_state.cash
    )
    # 没有这件物品就没有它的服务：第 3 月洗衣仍是 12 小时 × 0.5 = 6，负担 115.75 + 恢复 20 + 门诊 2 = 137.75；
    # 买下它的对照里洗衣降到 2 小时（1），所以少 5
    assert out.resources.months[2].energy_used.fraction() == Fraction("137.75")
    assert a.outcome.resources.months[2].energy_used.fraction() == Fraction("132.75")
    assert out.resources.months[3].energy_used.fraction() == 95
    assert DRYER_ID not in {h.opportunity_id for h in out.holdings_service}
    # 分数：健康 0、小病 0.1、大病 0.2 的直接扣分；十二个月都可评分
    assert [m.health.deduction for m in b.score.months[:5]] == [0.0, 0.0, 0.1, 0.2, 0.0]
    assert b.score.annual_status == "complete"
    nxt = b.next_state
    assert nxt.status == "active" and nxt.health.episode is None and nxt.health.binding is not None


def test_retries_and_stops_write_no_duplicate_or_half_records(bundle_hl):
    # u99：第 5 月小病，第一次试算超出精力额度，按暂停顺序停健身后通过；同月声明取消的购买只记一次，钱一分不动
    a99 = declared_plan(
        bundle_hl,
        "u99.a91.pause",
        1,
        HOME,
        FOOD,
        OFFICE,
        GYM,
        dryer(5),
        pause_order=[GYM_ID],
    )
    r99 = settle(bundle_hl, state_of(bundle_hl, "u99.a91.pause"), a99)
    assert r99.status == "completed"
    m5 = r99.outcome.health.months[4]
    assert [(x.opportunity_id, x.kind) for x in m5.paused] == [
        (GYM_ID, "agent_pause_order"),
        (DRYER_ID, "agent_cancel_when_ill"),
    ]
    d = item(r99, DRYER_ID)
    assert [(p.month, p.kind) for p in d.months_paused] == [(5, "agent_cancel_when_ill")]
    assert paid(r99, DRYER_ID) == []
    assert cents(r99.next_state.cash) == 1948000  # 与没有这条购买的 u99 一样：年末 19,480

    # u98：同样的生活、没有暂停顺序，第 5 月停止。停止月没有提交：既没有取消记录，也没有付款；前 4 个月逐项不变
    a98 = declared_plan(bundle_hl, "u98.a91.stop", 1, HOME, FOOD, OFFICE, GYM, dryer(5))
    r98 = settle(bundle_hl, state_of(bundle_hl, "u98.a91.stop"), a98)
    base = settle(
        bundle_hl,
        state_of(bundle_hl, "u98.a91.stop"),
        plan(bundle_hl, "u98.a91.stop", 1, HOME, FOOD, OFFICE, GYM),
    )
    assert r98.status == "stopped" and r98.outcome.stop.month == 5
    d = item(r98, DRYER_ID)
    assert d.status == "not_reached" and d.months_paused == () and d.months_not_reached == (5,)
    assert paid(r98, DRYER_ID) == []
    assert r98.outcome.months == base.outcome.months  # 第 1–4 月
    assert (
        r98.outcome.health.months[4].record == "blocked"
        and r98.outcome.health.months[4].paused == ()
    )


def test_the_agent_sees_its_declaration_and_cancellations_but_no_risk_mapping(
    bundle_hl, scoring_hl
):
    start, proto = review_case(bundle_hl)
    b = year(
        bundle_hl,
        scoring_hl,
        start,
        declared_plan(bundle_hl, "u91.a91.treat", 1, HOME, FOOD, SHOP, dryer(3)),
        protocol=proto,
    )
    fb = visible_feedback(b.settlement)
    assert fb.health.declared_purchase_cancellations == (DRYER_ID,)
    assert [(v.opportunity_id, v.why) for v in fb.health.months[2].paused] == [
        (DRYER_ID, CANCEL_LABEL)
    ]
    ex = next(x for x in fb.executed if x.opportunity_id == DRYER_ID)
    assert ex.status == "cancelled_when_ill" and ex.months_paused == (3,)
    assert "按年度应急声明" in ex.reason
    obs = observe(bundle_hl, b.next_state, proto.health_rules, last_feedback=fb, protocol=proto)
    payload = observation_payload(obs)
    assert payload["last_feedback"]["health"]["declared_purchase_cancellations"] == [DRYER_ID]
    text = json.dumps(payload, ensure_ascii=False)
    assert proto.health_rules.content_fingerprint not in text
    assert proto.illness_events.content_fingerprint not in text
    assert not all_keys(payload) & RESERVED_HEALTH_KEYS  # 没有概率表、风险档、随机数


def test_changing_only_the_note_text_changes_nothing_in_execution(bundle_hl):
    start, proto = review_case(bundle_hl)
    results = [
        settle(
            bundle_hl,
            start,
            declared_plan(bundle_hl, "u91.a91.treat", 1, HOME, FOOD, SHOP, dryer(3), note=note),
            proto,
        )
        for note in ("生病先别买，把钱留着看病", "另一句说明")
    ]
    a, b = results
    assert a.outcome.model_dump_json() == b.outcome.model_dump_json()
    assert a.next_state.model_dump_json() == b.next_state.model_dump_json()


@pytest.mark.parametrize(
    ("extra", "cancel", "words"),
    [
        ((), ["a91.durable.no_such_thing"], "不存在"),
        ((), [DRYER_ID], "没有这件购买"),
        (
            ({"opportunity_id": CONCERT, "quantity": 1, "month": 5},),
            [CONCERT],
            "只覆盖一次性耐用品",
        ),
        ((GYM,), [GYM_ID], "只覆盖一次性耐用品"),
        (
            ({"opportunity_id": COURSE_ID, "quantity": 1, "start_month": 1, "end_month": 12},),
            [COURSE_ID],
            "只覆盖一次性耐用品",
        ),
    ],
)
def test_invalid_declarations_are_refused_before_anything_runs(bundle_hl, extra, cancel, words):
    clone = "u91.a91.treat"
    rich = with_cash(state_of(bundle_hl, clone), 10000)
    r = settle(
        bundle_hl,
        rich,
        declared_plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, *extra, cancel=cancel),
    )
    assert r.status == "input_error" and r.outcome is None and r.next_state is None
    bad = [p for p in r.problems if p.code == "emergency_cancel_invalid"]
    assert bad and any(words in p.message for p in bad)


def test_references_are_unambiguous_and_repeats_are_refused(bundle_hl):
    # 计划里同一机会只能出现一次（原有契约），所以按机会 ID 引用一条购买总是唯一的
    with pytest.raises(ValidationError, match="同一机会出现多次"):
        declared_plan(bundle_hl, "u91.a91.treat", 1, HOME, FOOD, SHOP, dryer(2), dryer(5))
    with pytest.raises(ValidationError, match="cancel_purchases_when_ill 重复"):
        declared_plan(
            bundle_hl, "u91.a91.treat", 1, HOME, FOOD, SHOP, dryer(3), cancel=(DRYER_ID, DRYER_ID)
        )


def test_the_plan_never_assumes_a_future_illness(bundle_hl, scoring_hl):
    """无新事件的试算用同一份条件计划：只有年初就在延续的疾病会触发取消，不假定以后会生病。"""
    clone = "u97.a91.crossyear"

    def second_year(*events):
        proto = with_source(bundle_hl.protocol, injected(("u97", 1, 12, "major"), *events))
        start = state_of(bundle_hl, clone)
        y1 = year(bundle_hl, scoring_hl, start, bundle_hl.plans_for(clone)[0], protocol=proto)
        return proto, with_cash(y1.next_state, 10000)

    quiet, s2 = second_year()  # 第 2 年没有新事件：只剩年初就在延续的大病（第 1、2 月）
    r1 = settle(
        bundle_hl,
        s2,
        declared_plan(bundle_hl, clone, 2, HOME, FOOD, SHOP, dryer(1), choices=DECLINE),
        quiet,
    )
    assert item(r1, DRYER_ID).status == "cancelled_when_ill"
    r3 = settle(
        bundle_hl,
        s2,
        declared_plan(bundle_hl, clone, 2, HOME, FOOD, SHOP, dryer(3), choices=DECLINE),
        quiet,
    )
    assert item(r3, DRYER_ID).status == "executed"  # 第 3 月健康：照常买
    # 第 3 月真的新发小病：同一份计划照样可行（无事件试算里它买得成），真实执行里因病取消
    sick, s2b = second_year(("u97", 2, 3, "minor"))
    r3s = settle(
        bundle_hl,
        s2b,
        declared_plan(bundle_hl, clone, 2, HOME, FOOD, SHOP, dryer(3), choices=DECLINE),
        sick,
    )
    assert r3s.status == "completed" and item(r3s, DRYER_ID).status == "cancelled_when_ill"


def test_the_declaration_is_sealed_in_the_year_binding(bundle_hl, scoring_hl):
    start, proto = review_case(bundle_hl)
    clone = "u91.a91.treat"
    catalog = catalog_of(bundle_hl, start)

    def sealed(action):
        return settle_and_score(
            protocol=proto,
            state=start,
            action=action,
            catalog=catalog,
            tax=tax_of(bundle_hl, catalog),
            scoring=inputs(bundle_hl, scoring_hl, start),
        )

    plain = sealed(plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(3)))
    declared = sealed(declared_plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(3)))
    replay = sealed(declared_plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, dryer(3)))
    assert declared.action_fingerprint != plain.action_fingerprint
    assert declared.outcome_fingerprint != plain.outcome_fingerprint
    assert replay.binding_fingerprint == declared.binding_fingerprint  # 同一份输入重放，同一份绑定
    assert replay.settlement.model_dump_json() == declared.settlement.model_dump_json()


# ==========================================================================
# F3：H(健康) 固定为 1
# ==========================================================================


def test_a_healthy_anchor_other_than_one_is_refused_before_scoring(bundle_hl, tmp_path):
    with pytest.raises(ValidationError, match=r"H\(健康\) 固定为 1"):
        HealthStateValues(healthy=0.8, minor=0.5, major=0.0)
    raw = yaml.safe_load(SCORING_HL.read_text(encoding="utf-8"))
    raw["spec"]["health"]["state_values"]["healthy"] = 0.8
    path = tmp_path / "scoring_bad_anchor.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(LifeContractError, match=r"H\(健康\) 固定为 1"):
        load_scoring_config(path, bundle_hl)


def test_minor_and_major_values_keep_their_range_and_order():
    assert HealthStateValues(healthy=1.0, minor=0.7, major=0.2).of("minor") == 0.7
    with pytest.raises(ValidationError, match="健康 ≥ 小病 ≥ 大病"):
        HealthStateValues(healthy=1.0, minor=0.3, major=0.6)
    with pytest.raises(ValidationError):
        HealthStateValues(healthy=1.0, minor=1.2, major=0.0)


def test_the_fixed_anchor_reports_no_deduction_in_healthy_months(bundle_hl, scoring_hl):
    r = run_clone(bundle_hl, scoring_hl, "u91.a91.treat")[0]
    months = r.score.months
    assert [m.health.deduction for m in months[:4]] == [0.0, 0.0, 0.1, 0.0]
    for m in months:  # S = (1 − γ)·B + γ·H，健康 H = 1
        assert m.u == (1 - 0.2) * m.daily_life + 0.2 * m.health.value
    # γ = 0 对照：S = B，任何月份都不扣分
    start = state_of(bundle_hl, "u91.a91.treat")
    spec0 = revise(scoring_hl.spec, health=revise(scoring_hl.spec.health, gamma=0.0))
    s0 = score_year(
        protocol=bundle_hl.protocol,
        spec=spec0,
        conditions=scoring_hl.conditions_for(str(start.city)),
        catalog=catalog_of(bundle_hl, start),
        state=start,
        outcome=r.settlement.outcome,
        preference=preference_from_stated_profile(
            next(p for p in bundle_hl.stated_profiles if p.person_id == "u91")
        ),
    )
    assert all(m.u == m.daily_life and m.health.deduction == 0 for m in s0.months)
