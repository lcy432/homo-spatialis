"""E1-6A 收尾（DECISIONS/0029）：F1 身份只认封存记录、F2 已有物品不看新品供给、F4 正式入口与加载器同一份配置检查、
F3 生活模板与治疗分支分开；外加修订时发现的一处潜在缺陷（没有疾病规则的协议不写应急安排）。

数值都来自 E1-6A 合成样例的真实结算；手算见各测试的说明。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from common.life_contracts import Holding, LifeContractError
from common.life_evaluation_contracts import EvaluationSpec
from common.money import Money
from ledger.life_evaluation_config import (
    city_binding_problems,
    evaluation_spec_problems,
    load_evaluation_config,
)

from .evaluation_support import (
    DRYER_ID,
    FOOD,
    HOME,
    S_BASIC,
    S_COMFORT,
    S_SICK_BASIC,
    evaluate,
    f,
    plan,
    run_clone,
    spec_a81,
    x,
    year_plan,
)
from .health_support import injected, with_source
from .settle_support import catalog_of, protocol_with_years, revise, state_of

U61, U62, U68, U69 = "u61.a93.low", "u62.a93.rich", "u68.a93.sick", "u69.b94.gap"
B94 = "US:SYNTH2026:B94"
LOCAL = "USD_2024_LOCAL"


def cash(amount: float) -> Money:
    return Money(amount=amount, numeraire=LOCAL)


# ---------------------------------------------------------------------- F1：身份只认封存记录


@pytest.mark.parametrize(
    ("field", "value", "label"),
    [
        ("city", None, "城市"),
        ("clone_id", "another.clone", "分身"),
        ("person_id", "u62", "人"),
    ],
)
def test_a_relabelled_trajectory_is_refused(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev, field, value, label
):
    """把 u61 在 A93 的三年结果外层改个标签（封存的年度绑定、账目与分数原样）：评价器拒绝，并说明是哪个字段对不上。"""
    value = value if value is not None else state_of(bundle_ev, U69).city
    altered = [r.model_copy(update={field: value}) for r in lives_ev[U61]]
    assert all(a.scored_year is r.scored_year for a, r in zip(altered, lives_ev[U61], strict=True))
    with pytest.raises(
        LifeContractError, match=f"外层身份与它封存的实际生活不一致（{label}：外层写"
    ):
        evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, altered)


def test_a_relabelled_year_is_refused(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    y1, y2, y3 = lives_ev[U61]
    with pytest.raises(LifeContractError, match="年份：外层写 2，封存记录是 1"):
        evaluate(
            bundle_ev,
            scoring_ev,
            evaluation_ev,
            U61,
            [y1.model_copy(update={"year_index": 2}), y2, y3],
        )


def test_a_single_year_partial_trajectory_cannot_change_city(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """只有一年、还没跑完的轨迹同样不能归错城市。"""
    first = lives_ev[U61][0].model_copy(update={"city": state_of(bundle_ev, U69).city})
    with pytest.raises(
        LifeContractError, match="城市：外层写 US:SYNTH2026:B94，封存记录是 US:SYNTH2026:A93"
    ):
        evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, [first])


def test_the_evaluation_identity_comes_from_the_sealed_life(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, lives_ev[U61])
    sealed = lives_ev[U61][0].scored_year
    assert (ev.person_id, ev.clone_id, str(ev.city)) == (
        sealed.person_id,
        sealed.clone_id,
        str(sealed.city),
    )
    assert ev.repeat_index == sealed.start.health.repeat_index == 0
    # 正常重放照旧逐字节相同
    replay = evaluate(
        bundle_ev, scoring_ev, evaluation_ev, U61, run_clone(bundle_ev, scoring_ev, U61)
    )
    assert replay.model_dump_json() == ev.model_dump_json()


def test_a_catalog_of_another_city_is_named_as_such(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    from econ.life_evaluation import evaluate_clone

    from .evaluation_support import scoring_inputs
    from .settle_support import tax_of

    s = state_of(bundle_ev, U61)
    b94 = bundle_ev.catalog_for(B94)
    with pytest.raises(
        LifeContractError,
        match="评价器拿到的是 US:SYNTH2026:B94 的目录，封存的实际生活在 US:SYNTH2026:A93",
    ):
        evaluate_clone(
            spec=evaluation_ev,
            protocol=bundle_ev.protocol,
            catalog=b94,
            tax=tax_of(bundle_ev, catalog_of(bundle_ev, s)),
            scoring=scoring_inputs(bundle_ev, scoring_ev, s),
            results=lives_ev[U61],
        )


# ---------------------------------------------------------------------- F2：已有物品不看新品供给


def _with_new_dryer(bundle_ev, availability):
    """A93 的洗烘一体机新品改成 可得 / 确定缺货 / 资料未知（缺货与未知时没有报价）；其余目录原样。"""
    catalog = bundle_ev.catalogs[0]
    item = catalog.opportunity(DRYER_ID)
    if availability == "available":
        return bundle_ev
    new = revise(item, availability=availability, price=None)
    opps = [new if o.opportunity_id == DRYER_ID else o for o in catalog.opportunities]
    return revise(
        bundle_ev, catalogs=[revise(catalog, opportunities=opps), *bundle_ev.catalogs[1:]]
    )


def _owner_life(b, scoring_ev, *, held: bool):
    """u62 开局（9,000）；held 时家里已有一台还能用 120 个月的洗烘一体机，三年后还剩 84 个月。"""
    state = state_of(b, U62)
    if held:
        state = revise(
            state,
            holdings=[
                Holding(opportunity_id=DRYER_ID, quantity=1, acquired_year=0, remaining_months=120)
            ],
        )
    return state, run_clone(b, scoring_ev, U62, state=state)


@pytest.mark.parametrize("availability", ["available", "unavailable", "unknown"])
def test_an_owned_item_is_used_whatever_the_shop_has(
    bundle_ev, scoring_ev, evaluation_ev, availability
):
    b = _with_new_dryer(bundle_ev, availability)
    assert (
        evaluation_spec_problems(evaluation_ev, b, scoring_ev) == []
    )  # 合法的目录事实，不是配置错误
    _, rs = _owner_life(b, scoring_ev, held=True)
    assert rs[-1].handoff == "terminal"
    assert rs[-1].next_state.holdings[0].effective_remaining_months == 84
    ev = evaluate(b, scoring_ev, evaluation_ev, U62, rs)
    p2 = plan(ev, "p2_current_comfort")
    assert p2.status == "complete" and len(p2.skipped_purchases) == 1
    assert DRYER_ID not in [i.opportunity_id for i in p2.items]  # 不买，所以也不需要新品可得或报价
    (end,) = p2.end.holdings
    assert (end.quantity, end.remaining_months) == (1, 72)  # 84 − 12：不重置寿命
    assert p2.cash_end == cash(13320 + 12 * (1500 - 1910))  # 没有再付 2,400
    assert set(p2.month_scores) == {"0.6566137566137566"}  # 与新品可得时同一份生活
    assert x(ev.valuation.v_reference) == f(S_COMFORT)


@pytest.mark.parametrize(
    ("availability", "status", "kind"),
    [("unavailable", "infeasible", "slot_unavailable"), ("unknown", "unevaluable", "slot_unknown")],
)
def test_without_an_owned_item_the_purchase_conditions_still_apply(
    bundle_ev, scoring_ev, evaluation_ev, availability, status, kind
):
    """家里没有洗衣设备：要买的新品确定缺货是已知不可行；资料未知是无法评价（不当免费，也不当没有）。"""
    b = _with_new_dryer(bundle_ev, availability)
    _, rs = _owner_life(b, scoring_ev, held=False)
    ev = evaluate(b, scoring_ev, evaluation_ev, U62, rs)
    p2 = plan(ev, "p2_current_comfort")
    assert (p2.status, p2.reason_kind) == (status, kind)
    assert not p2.skipped_purchases


def test_an_expired_item_neither_serves_nor_blocks_a_purchase(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """u64 的二手洗衣机第 3 年末到期：估值期初没有它，较舒适模板要买新机器（这里现金不够，已知不可行），不会把到期的
    机器当成"已经有了"。"""
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, "u64.a93.used", lives_ev["u64.a93.used"])
    assert ev.valuation.context.start.holdings == ()
    p2 = plan(ev, "p2_current_comfort")
    assert DRYER_ID in [i.opportunity_id for i in p2.items] and not p2.skipped_purchases


# ---------------------------------------------------------------------- F4：正式入口与加载器同一份配置检查


def _gym_bound_to_housing(evaluation_ev):
    data = evaluation_ev.model_dump(mode="python")
    for b in data["city_bindings"]:
        for t in b["targets"]:
            if t["slot_id"] == "exercise":
                t["opportunity_id"] = t["opportunity_id"].replace("service.gym", "housing.far")
    return EvaluationSpec.model_validate(data)


def test_a_wrong_role_binding_is_refused_by_loader_and_runtime_alike(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    bad = _gym_bound_to_housing(evaluation_ev)
    problems = evaluation_spec_problems(bad, bundle_ev, scoring_ev)
    per_city = city_binding_problems(bad, bundle_ev.catalogs[0])
    assert per_city and set(per_city) <= set(problems)  # 加载器逐城调用的就是这一份
    assert any(
        "a93.housing.far 在结算里是 housing，槽位要的是 periodic_service" in p for p in per_city
    )
    with pytest.raises(
        LifeContractError, match="评价配置与本城目录对不上（这是配置写错，不是城市资料未知）"
    ):
        evaluate(bundle_ev, scoring_ev, bad, U62, lives_ev[U62])


def test_an_unknown_opportunity_reference_is_a_configuration_error(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    data = evaluation_ev.model_dump(mode="python")
    data["city_bindings"][0]["targets"][0]["opportunity_id"] = "a93.housing.castle"
    bad = EvaluationSpec.model_validate(data)
    with pytest.raises(LifeContractError, match=r"a93.housing.castle 不在这座城的目录里"):
        evaluate(bundle_ev, scoring_ev, bad, U62, lives_ev[U62])


def test_legal_unknown_and_unavailable_declarations_are_not_configuration_errors(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """B94 的"确定没有会计岗位"是合法声明；把 A93 的健身房改成"资料未知"也是合法声明——它们让方案已知不可行或无法
    评价，不是配置写错。"""
    assert city_binding_problems(evaluation_ev, bundle_ev.catalog_for(B94)) == []
    data = evaluation_ev.model_dump(mode="python")
    for t in data["city_bindings"][0]["targets"]:
        if t["slot_id"] == "exercise":
            t.update(status="unknown", opportunity_id=None, reason="测试：健身房资料未知")
    unknown = EvaluationSpec.model_validate(data)
    assert city_binding_problems(unknown, bundle_ev.catalogs[0]) == []
    ev = evaluate(bundle_ev, scoring_ev, unknown, U62, lives_ev[U62])
    assert plan(ev, "p2_current_comfort").reason_kind == "slot_unknown"


# ---------------------------------------------------------------------- F3：生活模板与治疗分支分开


def test_a_healthy_start_runs_one_branch_per_template(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U62, lives_ev[U62])
    v = ev.valuation
    assert (v.branching, v.continuing_severity, v.trials) == (
        "healthy_start_single_branch",
        None,
        4,
    )
    assert [(p.plan_id, p.template_id, p.treatment_branch) for p in v.plans] == [
        (t.template_id, t.template_id, "untriggered") for t in evaluation_ev.life_templates
    ]
    assert (v.selected_template_id, v.selected_treatment) == ("p2_current_comfort", "untriggered")
    assert any("未触发" in r for r in v.reasons)


def test_an_ill_start_offers_treat_and_decline_for_the_same_life(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """u68 带着大病进入估值期：每个模板两个分支，同一模板的两个分支岗位、住房、消费与购买完全一样，只差治疗选择。"""
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U68, lives_ev[U68])
    v = ev.valuation
    assert (v.branching, v.continuing_severity, v.trials) == (
        "continuing_illness_treat_or_decline",
        "major",
        8,
    )
    for t in evaluation_ev.life_templates:
        treat, decline = plan(ev, f"{t.template_id}.treat"), plan(ev, f"{t.template_id}.decline")
        assert (treat.treatment_branch, decline.treatment_branch) == (
            "treat_if_possible",
            "decline",
        )
        assert treat.items == decline.items and treat.skipped_purchases == decline.skipped_purchases
    treat, decline = plan(ev, "p1_current_basic.treat"), plan(ev, "p1_current_basic.decline")
    # 手算：第 1 个月病假没有工资（两边一样）；治疗付 2,000，月末恢复；不治疗第 2 个月继续病假
    assert treat.cash_end == cash(6820 - 1380 - 2000 + 11 * 120)
    assert decline.cash_end == cash(6820 - 1380 - 1380 + 10 * 120)
    assert treat.health_states[:2] == ("major", "healthy")
    assert decline.health_states[:3] == ("major", "major", "healthy")
    assert x(treat.v_plan) == (f(S_SICK_BASIC) + 11 * f(S_BASIC)) / 12
    assert x(decline.v_plan) == (2 * f(S_SICK_BASIC) + 10 * f(S_BASIC)) / 12


def test_cash_never_turns_a_decline_into_treatment(bundle_ev, scoring_ev, evaluation_ev):
    """同一个 u68，开局多给 50,000：不治疗分支照样不治（钱够也不改成治疗），治疗分支照样只付一次。"""
    rich = revise(state_of(bundle_ev, U68), cash=cash(54000))
    rs = run_clone(bundle_ev, scoring_ev, U68, state=rich)
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U68, rs)
    decline = plan(ev, "p2_current_comfort.decline")
    treat = plan(ev, "p2_current_comfort.treat")
    assert decline.health_states[:3] == ("major", "major", "healthy")
    assert treat.health_states[:2] == ("major", "healthy")
    assert decline.status == treat.status == "complete"


def test_the_review_counterexample_now_has_a_decline_branch(bundle_ev, scoring_ev, evaluation_ev):
    """审核的 F3 反例（N = 1，只看重交通的人带着大病结束，期末现金 3,700 与 3,850，其余逐项相同）。v1 的较舒适方案
    写死"有钱就治"：钱多了就去治，交通—健康反例让它分数反而低。v2 里同一个较舒适模板有"不治疗"分支，钱多的人也能过
    钱少的人那份生活。两边的 V 在这个例子里相同——这是这个例子的事实，不是一般的现金单调性定理（该要求已撤回）。"""
    clone = "u70.a93.commute_sick"
    catalog = bundle_ev.catalogs[0]
    opps = []
    for o in catalog.opportunities:
        if o.opportunity_id in ("a93.housing.near", "a93.housing.far"):
            minutes = 70 if o.opportunity_id.endswith(".near") else 120
            o = revise(o, spec={**o.spec, "commute_roundtrip_minutes": minutes})
        opps.append(o)
    b = revise(bundle_ev, catalogs=[revise(catalog, opportunities=opps), *bundle_ev.catalogs[1:]])
    protocol = with_source(protocol_with_years(b.protocol, 1), injected(("u70", 1, 12, "major")))
    job = {
        "opportunity_id": "a93.job.accountant_office",
        "quantity": 1,
        "start_month": 11,
        "end_month": 12,
    }
    action = year_plan(
        b, clone, 1, HOME, FOOD, job, emergency={"minor": "decline", "major": "decline"}
    )
    evs = {}
    for terminal_cash in (3700, 3850):
        s = revise(
            state_of(b, clone),
            cash=cash(13560 + terminal_cash),
            holdings=[
                Holding(opportunity_id=DRYER_ID, quantity=1, acquired_year=0, remaining_months=120)
            ],
        )
        rs = run_clone(b, scoring_ev, clone, protocol=protocol, state=s, plans=[action])
        assert rs[-1].next_state.cash == cash(terminal_cash)
        evs[terminal_cash] = evaluate(b, scoring_ev, evaluation_ev, clone, rs, protocol=protocol)
    poor, rich = evs[3700], evs[3850]
    # 钱少：住院付不起，治疗分支实际上也没治——与不治疗分支同一份生活
    assert (
        plan(poor, "p2_current_comfort.treat").month_scores
        == plan(poor, "p2_current_comfort.decline").month_scores
    )
    # 钱多：治疗分支真的治了；不治疗分支重现钱少时那份生活
    assert plan(rich, "p2_current_comfort.treat").health_states[:2] == ("major", "healthy")
    assert (
        plan(rich, "p2_current_comfort.decline").month_scores
        == plan(poor, "p2_current_comfort.decline").month_scores
    )
    assert x(rich.valuation.v_reference) == x(poor.valuation.v_reference)
    assert rich.valuation.selected_plan_id == "p2_current_comfort.decline"


def test_the_retired_v1_specification_is_refused_with_a_pointer(bundle_ev, scoring_ev):
    """v1 / r1 不再执行：旧配置原样保留作历史，用当前代码加载会明确拒绝，并指向新的规则记录。"""
    with pytest.raises(LifeContractError, match=r"e1-6a-evaluation-v2 取代.*DECISIONS/0029"):
        load_evaluation_config(Path("configs/life/evaluation_e1_6a.yaml"), bundle_ev, scoring_ev)


# ---------------------------------------------------------------------- 修订时发现的潜在缺陷


def test_protocols_without_health_rules_can_be_valued(bundle_sv, scoring_sv):
    """E1-5B（目标储蓄，没有疾病规则）的协议：参考分支的年度计划不能写应急安排（写了是输入错误）。修订前每个分支都写，
    在这类协议上估值会全部变成"无法评价"。"""
    from econ.life_evaluation import evaluate_clone
    from econ.life_settlement import binding_from_policy
    from econ.life_year import ScoringInputs, run_life_year

    clone = "u81.a81.save"
    state = bundle_sv.initial_state(clone)
    catalog = bundle_sv.catalog_for(str(state.city))
    tax = binding_from_policy(bundle_sv.tax_policy(catalog.tax_policy_id))
    profile = next(p for p in bundle_sv.stated_profiles if p.person_id == "u81")
    scoring = ScoringInputs(
        spec=scoring_sv.spec,
        conditions=scoring_sv.conditions_for(str(state.city)),
        preference_input=profile,
        hard_requirements=profile.hard_requirements,
    )
    rs = []
    for p in bundle_sv.plans_for(clone):
        r = run_life_year(
            protocol=bundle_sv.protocol,
            state=state,
            action=p,
            catalog=catalog,
            tax=tax,
            scoring=scoring,
        )
        rs.append(r)
        if r.handoff != "next_year_ready":
            break
        state = r.next_state
    assert rs[-1].handoff == "terminal"
    spec = spec_a81()
    assert bundle_sv.protocol.health_rules is None
    ev = evaluate_clone(
        spec=spec,
        protocol=bundle_sv.protocol,
        catalog=catalog,
        tax=tax,
        scoring=scoring,
        results=rs,
    )
    (p1,) = ev.valuation.plans
    assert (p1.settlement_status, p1.status, p1.treatment_branch) == (
        "completed",
        "complete",
        "untriggered",
    )
    assert ev.valuation.status == "complete" and ev.combined.j is not None
    assert ev.repeat_index is None  # 没有健康规则的协议没有重复分身序号
    assert x(ev.combined.share_actual) == Fraction(3, 4)
