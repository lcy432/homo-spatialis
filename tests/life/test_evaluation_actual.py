"""E1-6A：实际生活的跨月汇总 L_actual、时间权重 δ、协议长度 N 与轨迹分类（DECISIONS/0028）。

逐月满足感取评分器的最短十进制写法，换成精确分数再加权；期望值按 evaluation_support 里的几种月份分数手算。
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from common.life_contracts import LifeContractError
from common.life_evaluation_contracts import MAX_LIFE_TEMPLATES, parse_discount_factor
from common.money import Money

from .evaluation_support import (
    FOOD,
    FRESH,
    HOME,
    NEAR,
    S_BASIC,
    S_BASIC_COURSE,
    S_COMFORT,
    S_SICK_BASIC,
    SHOP,
    evaluate,
    f,
    plan,
    run_clone,
    spec_with,
    x,
    year_plan,
)
from .health_support import injected, with_source
from .settle_support import protocol_with_years, revise, state_of

U61, U62, U66, U68, U69 = (
    "u61.a93.low",
    "u62.a93.rich",
    "u66.a93.halfway",
    "u68.a93.sick",
    "u69.b94.gap",
)


# ---------------------------------------------------------------------- 1. N = 3 与 N = 1


def test_three_years_are_aggregated_month_by_month(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, lives_ev[U61])
    a = ev.actual
    assert (a.status, a.horizon_years, a.months_total) == ("complete", 3, 36)
    assert [(m.index, m.year_index, m.month) for m in a.months] == [
        ((y - 1) * 12 + m, y, m) for y in (1, 2, 3) for m in range(1, 13)
    ]
    assert all(m.status == "scored" and m.score_decimal == S_BASIC for m in a.months)
    assert all(x(m.weight) == 1 and x(m.contribution) == f(S_BASIC) for m in a.months)
    assert x(a.weight_total) == 36 and x(a.known_weighted_sum) == 36 * f(S_BASIC)
    assert x(a.l_actual) == f(S_BASIC) and a.l_actual_display == round(float(f(S_BASIC)), 6)
    c = ev.combined
    assert (x(c.weight_actual), x(c.weight_future)) == (36, 12)
    assert (x(c.share_actual), x(c.share_future)) == (Fraction(3, 4), Fraction(1, 4))
    # N = 3、H = 12、δ = 1：J = 0.75 L + 0.25 V
    assert x(c.j) == Fraction(3, 4) * x(a.l_actual) + Fraction(1, 4) * x(ev.valuation.v_reference)


def test_a_sick_last_month_is_hand_checkable(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    """u68：35 个健康的基本月 + 第 36 个月大病病假（没有工资、没有到岗、H = 0）。"""
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U68, lives_ev[U68])
    assert [m.score_decimal for m in ev.actual.months] == [S_BASIC] * 35 + [S_SICK_BASIC]
    assert x(ev.actual.l_actual) == (35 * f(S_BASIC) + f(S_SICK_BASIC)) / 36
    # 先平均年度浮点均值得到的数只在最末一位不同：报告里写明原因，不把它当成公式不同
    assert ev.actual.mean_of_annual_means is not None
    assert ev.actual.mean_of_annual_means != float(x(ev.actual.l_actual))
    assert abs(ev.actual.mean_of_annual_means - float(x(ev.actual.l_actual))) < 1e-15
    assert any("舍入顺序" in n for n in ev.actual.notes)


def test_course_months_grow_the_actual_score(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    """u66 第 3 年第 7–12 月上课：这 6 个月发展维有成长（E1-5A 补充的公式，原样读取）。"""
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U66, lives_ev[U66])
    assert [m.score_decimal for m in ev.actual.months] == [S_BASIC] * 30 + [S_BASIC_COURSE] * 6
    assert x(ev.actual.l_actual) == (30 * f(S_BASIC) + 6 * f(S_BASIC_COURSE)) / 36


def test_a_one_year_protocol_uses_twelve_months(bundle_ev, scoring_ev, evaluation_ev):
    """T 由协议的 N 推出（12N），不写死 36。N = 1：实际与估值各 12 个月，各占一半。"""
    p1 = protocol_with_years(bundle_ev.protocol, 1)
    out = {}
    for clone in (U61, U62):
        rs = run_clone(bundle_ev, scoring_ev, clone, protocol=p1)
        assert [r.handoff for r in rs] == ["terminal"]
        ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, clone, rs, protocol=p1)
        assert (ev.actual.months_total, x(ev.actual.weight_total)) == (12, 12)
        assert (x(ev.combined.share_actual), x(ev.combined.share_future)) == (
            Fraction(1, 2),
            Fraction(1, 2),
        )
        ctx = ev.valuation.context
        assert (ctx.simulation_years, ctx.valuation_year) == (1, 2)
        out[clone] = ev
    # 一年后 u61 现金 1,940，付不起较舒适的方案；u62 现金 10,440，付得起
    assert out[U61].valuation.selected_plan_id == "p1_current_basic"
    assert out[U62].valuation.selected_plan_id == "p2_current_comfort"
    assert x(out[U62].combined.j) == (f(S_BASIC) + f(S_COMFORT)) / 2


# ---------------------------------------------------------------------- 2. 时间权重 δ


def test_month_weights_follow_the_declared_delta(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    """非 1 对照 δ = 0.99：w_m = δ^(m−1)；估值期第 k 个月的合成权重是 δ^(T+k−1)。只验证机制，不是主设定。"""
    s99 = spec_with(evaluation_ev, spec_id="eval.e1-6a.delta099", monthly_discount_factor="0.99")
    d = Fraction(99, 100)
    ev = evaluate(bundle_ev, scoring_ev, s99, U68, lives_ev[U68])
    main = evaluate(bundle_ev, scoring_ev, evaluation_ev, U68, lives_ev[U68])
    w = [d**k for k in range(36)]
    assert [x(m.weight) for m in ev.actual.months] == w
    s = [f(S_BASIC)] * 35 + [f(S_SICK_BASIC)]
    assert x(ev.actual.l_actual) == sum(a * b for a, b in zip(w, s, strict=True)) / sum(w)
    p1 = plan(
        ev, "p1_current_basic.treat"
    )  # v2：带病期初，p1 的"条件允许就治"分支（与 v1 的 p1 是同一份生活）
    fut = [f(v) for v in p1.month_scores]
    assert fut == [f(S_SICK_BASIC)] + [f(S_BASIC)] * 11
    assert x(p1.v_plan) == sum(d**k * fut[k] for k in range(12)) / sum(d**k for k in range(12))
    wf = [d ** (36 + k) for k in range(12)]
    assert x(ev.combined.weight_future) == sum(wf)
    assert x(ev.combined.j) == (
        sum(a * b for a, b in zip(w, s, strict=True))
        + sum(a * b for a, b in zip(wf, fut, strict=True))
    ) / (sum(w) + sum(wf))
    # 生病的月份在实际生活的最后（权重小）、在估值期的开头（权重大）：L 变高、V 变低——方向可以手算
    assert x(ev.actual.l_actual) > x(main.actual.l_actual)
    assert x(ev.valuation.v_reference) < x(main.valuation.v_reference)
    assert ev.spec_content_fingerprint != main.spec_content_fingerprint


def test_delta_is_compared_by_value_and_bad_values_are_rejected(evaluation_ev):
    assert parse_discount_factor("1") == parse_discount_factor("1.0") == 1
    assert (
        spec_with(evaluation_ev, monthly_discount_factor="1.0").content_fingerprint
        == evaluation_ev.content_fingerprint
    )
    for bad in ("0", "-0.5", "1.01", "abc", "nan", "inf", "0.9999999"):
        with pytest.raises(ValueError, match="δ"):
            spec_with(evaluation_ev, monthly_discount_factor=bad)


@pytest.mark.parametrize("months", [6, 24, 0])
def test_only_a_twelve_month_horizon_is_supported(evaluation_ev, months):
    with pytest.raises(ValueError, match="不静默改成 12"):
        spec_with(evaluation_ev, valuation_horizon_months=months)


def test_reference_plan_set_shape_is_checked(evaluation_ev):
    plans = list(evaluation_ev.model_dump(mode="python")["life_templates"])
    extra = [dict(plans[0], template_id=f"p_extra_{i}") for i in range(MAX_LIFE_TEMPLATES)]
    with pytest.raises(ValueError, match="基础生活模板要有 1–6 个"):
        spec_with(evaluation_ev, life_templates=plans + extra)
    with pytest.raises(ValueError, match="基础生活模板要有 1–6 个"):
        spec_with(evaluation_ev, life_templates=[])
    with pytest.raises(ValueError, match="编号重复"):
        spec_with(evaluation_ev, life_templates=[plans[0], plans[0]])
    bindings = evaluation_ev.model_dump(mode="python")["city_bindings"]
    bindings[0]["targets"] = bindings[0]["targets"][:-1]
    with pytest.raises(ValueError, match="必须为比较组的每个槽位写绑定"):
        spec_with(evaluation_ev, city_bindings=bindings)
    twice = dict(plans[0], housing_slot="food_basic")  # 同一个槽位既当住房又当食品
    with pytest.raises(ValueError, match="用了不止一次"):
        spec_with(evaluation_ev, life_templates=[twice])
    wrong = dict(plans[0], housing_slot="food_comfort")
    with pytest.raises(ValueError, match="没有声明为 housing"):
        spec_with(evaluation_ev, life_templates=[wrong])
    with pytest.raises(ValueError):
        spec_with(evaluation_ev, valuation_event_condition="random_illness")


def test_the_loader_checks_bindings_against_the_catalogs(bundle_ev, scoring_ev, evaluation_ev):
    from ledger.life_evaluation_config import evaluation_spec_problems

    assert evaluation_spec_problems(evaluation_ev, bundle_ev, scoring_ev) == []
    bindings = evaluation_ev.model_dump(mode="python")["city_bindings"]
    targets = {t["slot_id"]: t for t in bindings[0]["targets"]}
    targets["housing_comfort"]["opportunity_id"] = "a93.food.fresh"
    targets["exercise"]["opportunity_id"] = "a93.service.yoga"
    bad = spec_with(evaluation_ev, city_bindings=bindings)
    problems = evaluation_spec_problems(bad, bundle_ev, scoring_ev)
    assert any("a93.food.fresh 在结算里是 food，槽位要的是 housing" in p for p in problems)
    assert any("a93.service.yoga 不在这座城的目录里" in p for p in problems)
    only_a = spec_with(evaluation_ev, city_bindings=bindings[:1])
    assert any(
        "B94 没有槽位绑定" in p for p in evaluation_spec_problems(only_a, bundle_ev, scoring_ev)
    )


# ---------------------------------------------------------------------- 4. 部分轨迹与分类


def test_life_exit_keeps_its_record_and_is_not_an_unknown_score(
    bundle_ev, scoring_ev, evaluation_ev
):
    """没有工作的计划：第 1 年第 1 月 500 < 1,000 + 380，出局。确认的生活出局：不进城市平均，也不当未知分数。"""
    plans = [year_plan(bundle_ev, U61, 1, HOME, FOOD)]
    rs = run_clone(bundle_ev, scoring_ev, U61, plans=plans)
    assert [r.handoff for r in rs] == ["exited"]
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, rs)
    t = ev.trajectory
    assert (t.status, t.owner_category) == ("life_exit", "life_exit")
    assert (t.exit.year_index, t.exit.month) == (1, 1) and t.stop is None
    assert [(e.year_index, e.month, e.event_kind) for e in t.events] == [(1, 1, "exit")]
    statuses = [m.status for m in ev.actual.months]
    assert statuses == ["exit_month"] + ["after_exit"] * 11 + ["not_simulated"] * 24
    assert all(m.score_decimal is None and m.contribution is None for m in ev.actual.months)
    assert ev.actual.status == "incomplete" and ev.actual.l_actual is None
    assert x(ev.actual.known_weight) == 0  # 没有任何一个月被当成 0 分计入
    assert ev.valuation.status == "not_applicable" and not ev.valuation.plans
    assert ev.combined.status == "incomplete" and ev.combined.j is None


def test_a_stop_is_kept_as_a_stop_not_renamed_an_exit(bundle_ev, scoring_ev, evaluation_ev):
    """B94 住院资料未知：第 2 年第 3 月大病、计划要治疗 → E1-5C 停止（资料缺口）。原样保留种类，不改称出局。"""
    protocol = with_source(bundle_ev.protocol, injected(("u69", 2, 3, "major")))
    home = {"opportunity_id": "b94.housing.far", "quantity": 1, "start_month": 1, "end_month": 12}
    food = {"opportunity_id": "b94.food.basic", "quantity": 1, "start_month": 1, "end_month": 12}
    job = {
        "opportunity_id": "b94.job.shop_parttime",
        "quantity": 1,
        "start_month": 1,
        "end_month": 12,
    }
    plans = [year_plan(bundle_ev, U69, y, home, food, job) for y in (1, 2, 3)]
    rs = run_clone(bundle_ev, scoring_ev, U69, protocol=protocol, plans=plans)
    assert [r.handoff for r in rs] == ["next_year_ready", "stopped"]
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U69, rs, protocol=protocol)
    t = ev.trajectory
    assert (t.status, t.owner_category) == ("stopped", "stopped_kept_as_is")
    assert t.exit is None and (t.stop.year_index, t.stop.month, t.stop.kind) == (2, 3, "data_gap")
    assert [(e.year_index, e.month, e.event_kind) for e in t.events] == [
        (2, 3, "illness_onset"),
        (2, 3, "stop"),
    ]
    statuses = [m.status for m in ev.actual.months]
    assert statuses == (
        ["scored"] * 14 + ["stopped_month"] + ["after_stop"] * 9 + ["not_simulated"] * 12
    )
    assert x(ev.actual.known_weighted_sum) == 14 * f(S_BASIC)  # 已知月份保留，但不拿来冒充 L
    assert ev.actual.l_actual is None
    assert ev.valuation.status == "not_applicable" and ev.combined.j is None


def test_a_settlement_failure_is_a_simulation_problem(bundle_ev, scoring_ev, evaluation_ev):
    """第 1 年选近处住房 + 新鲜食材、没声明兜底：第 2 月付不起所选住房 → 计划不可行，这一年没有生活结果。"""
    plans = [year_plan(bundle_ev, U61, 1, NEAR, FRESH, SHOP)]
    rs = run_clone(bundle_ev, scoring_ev, U61, plans=plans)
    assert [r.handoff for r in rs] == ["settlement_failed"]
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, rs)
    assert (ev.trajectory.status, ev.trajectory.owner_category) == (
        "settlement_failed",
        "simulation_incomplete",
    )
    assert {m.status for m in ev.actual.months} == {"not_simulated"}
    assert "结算失败（plan_infeasible）" in ev.actual.months[0].reason
    assert ev.combined.j is None


def test_an_unfinished_trajectory_is_not_valued(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, lives_ev[U61][:2])
    assert (ev.trajectory.status, ev.trajectory.owner_category) == (
        "not_finished",
        "simulation_incomplete",
    )
    assert [m.status for m in ev.actual.months[24:]] == ["not_simulated"] * 12
    assert x(ev.actual.known_weighted_sum) == 24 * f(S_BASIC) and ev.actual.l_actual is None
    assert ev.valuation.status == "not_applicable" and ev.combined.j is None


def _unknown_environment(scoring_ev):
    cond = scoring_ev.conditions_for("US:SYNTH2026:A93")
    data = cond.model_dump(mode="python")
    for c in data["components"]:
        if c["component_id"] == "environment":
            c.update(status="unknown", base=None, cap=None, unknown_reason="测试：环境资料未知")
    return type(cond).model_validate(data)


def test_feedback_blocked_is_a_simulation_problem(bundle_ev, scoring_ev, evaluation_ev):
    """环境资料未知（正权重）→ 第 1 年评分不完整 → 反馈开着、下一年额度无法确定 → 停在第 1 年。"""
    cond = _unknown_environment(scoring_ev)
    rs = run_clone(bundle_ev, scoring_ev, U61, conditions=cond)
    assert [r.handoff for r in rs] == ["feedback_blocked"]
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, rs, conditions=cond)
    assert (ev.trajectory.status, ev.trajectory.owner_category) == (
        "feedback_blocked",
        "simulation_incomplete",
    )
    statuses = [m.status for m in ev.actual.months]
    assert statuses == ["incomplete"] * 12 + ["not_simulated"] * 24
    assert x(ev.actual.known_weight) == 0 and ev.actual.l_actual is None  # 不补零、不用部分月份
    assert ev.valuation.status == "not_applicable" and ev.combined.j is None


def test_a_normal_end_with_unscored_months_is_score_incomplete(
    bundle_ev, scoring_ev, evaluation_ev
):
    """反馈关着时环境资料未知也能正常走到终态：终态正常，但每个月评分不完整 → score_incomplete；估值的每个方案评分
    也不完整 → 无法评价 → 没有 V、没有 J。不补零、不用已知月份平均。"""
    policy = revise(bundle_ev.protocol.energy_feedback, mode="disabled")
    protocol = revise(bundle_ev.protocol, energy_feedback=policy)
    cond = _unknown_environment(scoring_ev)
    rs = run_clone(bundle_ev, scoring_ev, U61, protocol=protocol, conditions=cond)
    assert [r.handoff for r in rs] == ["next_year_ready", "next_year_ready", "terminal"]
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, rs, protocol=protocol, conditions=cond)
    assert (ev.trajectory.status, ev.trajectory.owner_category) == (
        "score_incomplete",
        "simulation_incomplete",
    )
    assert ev.actual.l_actual is None
    v = ev.valuation
    assert v.context.quota.decision == "off_disabled" and x(v.context.quota.quota) == 250
    assert v.status == "incomplete" and v.v_reference is None and v.best_known is None
    assert {p.reason_kind for p in v.plans if p.status == "unevaluable"} == {"score_incomplete"}
    assert ev.combined.j is None


# ---------------------------------------------------------------------- 硬要求单独报告


def _profile_with(bundle_ev, person, *requirements):
    profile = next(p for p in bundle_ev.stated_profiles if p.person_id == person)
    return revise(profile, hard_requirements=list(requirements))


SHORT_COMMUTE = {
    "need": "short_commute",
    "requirement": {"category": "housing", "max_commute_roundtrip_minutes": 40},
}
BALCONY = {"need": "balcony", "requirement": {"category": "housing", "min_balcony_sqft": 20}}


def test_hard_requirements_are_reported_month_by_month_not_scored(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    profile = _profile_with(bundle_ev, "u62", SHORT_COMMUTE)
    rs = run_clone(bundle_ev, scoring_ev, U62, profile=profile)
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U62, rs, profile=profile)
    (h,) = ev.actual.hard_requirements
    assert (h.need, h.requirement_summary, h.months_not_satisfied) == (
        "short_commute",
        "violated",
        36,
    )
    # 硬要求不进分数：实际 L 与没有硬要求时相同
    plain = evaluate(bundle_ev, scoring_ev, evaluation_ev, U62, lives_ev[U62])
    assert x(ev.actual.l_actual) == x(plain.actual.l_actual)
    # 估值：远处住房的方案明确违反 → 排除；近处住房的方案满足 → 选它
    assert plan(ev, "p1_current_basic").status == "excluded_hard_requirement"
    # v2：原 p5（基本生活、不治疗）是 p1 的不治疗分支；u62 期初健康，只有 p1 一个分支，上一行已经覆盖
    p2 = plan(ev, "p2_current_comfort")
    assert p2.status == "complete" and p2.hard_requirements[0].requirement_summary == (
        "satisfied_every_month"
    )
    assert ev.valuation.selected_plan_id == "p2_current_comfort"


def test_an_unknown_hard_requirement_is_not_treated_as_satisfied(
    bundle_ev, scoring_ev, evaluation_ev
):
    """住房规格里没有阳台面积：每个月都"无法判断"——不当满足，也不当违反；估值方案因此无法评价。"""
    profile = _profile_with(bundle_ev, "u62", BALCONY)
    rs = run_clone(bundle_ev, scoring_ev, U62, profile=profile)
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U62, rs, profile=profile)
    (h,) = ev.actual.hard_requirements
    assert (h.requirement_summary, h.months_unknown) == ("unknown", 36)
    assert {p.reason_kind for p in ev.valuation.plans if p.status == "unevaluable"} == {
        "hard_requirement_unknown"
    }
    assert ev.valuation.status == "incomplete" and ev.combined.j is None


def test_money_is_not_part_of_the_actual_score(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    """u61 与 u62 三年生活相同、只有开局现金不同：逐月分数与 L 逐位相同（钱只通过能买得起什么起作用）。"""
    a = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, lives_ev[U61]).actual
    b = evaluate(bundle_ev, scoring_ev, evaluation_ev, U62, lives_ev[U62]).actual
    assert [m.score_decimal for m in a.months] == [m.score_decimal for m in b.months]
    assert x(a.l_actual) == x(b.l_actual)
    assert lives_ev[U61][-1].next_state.cash == Money(amount=4820, numeraire="USD_2024_LOCAL")
    assert lives_ev[U62][-1].next_state.cash == Money(amount=13320, numeraire="USD_2024_LOCAL")


def test_an_empty_trajectory_is_rejected(bundle_ev, scoring_ev, evaluation_ev):
    with pytest.raises(LifeContractError, match="没有实际轨迹"):
        evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, [])
    assert state_of(bundle_ev, U61).year_index == 1
