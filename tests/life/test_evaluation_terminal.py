"""E1-6A：期末估值（DECISIONS/0028）——从真实终态出发，按参考方案在估值期里真实结算、评分。

每条测试都能手算：基本生活每月净存 120（门店兼职 1,500 − 远处住房 1,000 − 基本食品 380）；较舒适的方案每月
1,400 + 450 + 60 = 1,910（净 −410），没有洗衣设备就在第 1 月多付 2,400。
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from common.life_contracts import IllnessEventSource, PersonYearState, model_fingerprint
from common.money import Money
from econ.life_settlement import settle_year

from .evaluation_support import (
    COURSE_ID,
    DRYER_ID,
    FOOD,
    HOME,
    S_ACCOUNTANT_BASIC,
    S_ACCOUNTANT_COMFORT,
    S_BASIC,
    S_BASIC_LAUNDRY,
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
from .health_support import injected, source, with_source
from .settle_support import catalog_of, revise, state_of, tax_of

U61, U62, U63, U64 = "u61.a93.low", "u62.a93.rich", "u63.a93.dryer", "u64.a93.used"
U65, U66, U67, U68 = "u65.a93.learned", "u66.a93.halfway", "u67.a93.well", "u68.a93.sick"
U69, U71 = "u69.b94.gap", "u71.a93.commute_well"
LOCAL = "USD_2024_LOCAL"


def cash(amount: float) -> Money:
    return Money(amount=amount, numeraire=LOCAL)


def rebind(spec, slot, opportunity_id=None, status="opportunity", reason=None, city=0):
    data = spec.model_dump(mode="python")
    for t in data["city_bindings"][city]["targets"]:
        if t["slot_id"] == slot:
            t.update(status=status, opportunity_id=opportunity_id, reason=reason)
    return spec_with(spec, **{"city_bindings": data["city_bindings"]})


# ---------------------------------------------------------------------- 终态隔离


def test_the_real_terminal_is_refused_by_settlement_and_left_unchanged(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    rs = lives_ev[U63]
    terminal = rs[-1].next_state
    before = (model_fingerprint(terminal), [model_fingerprint(r) for r in rs])
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U63, rs)
    assert (model_fingerprint(terminal), [model_fingerprint(r) for r in rs]) == before
    assert (
        ev.valuation.context.terminal_fingerprint == before[0] == ev.trajectory.terminal_fingerprint
    )
    assert terminal.status == "terminal" and terminal.year_index == 4
    # 普通结算照旧拒绝真实终态：估值没有放宽"终态不接受实际行动"
    catalog = catalog_of(bundle_ev, terminal)
    action = year_plan(bundle_ev, U63, 4, HOME, FOOD, SHOP)
    result = settle_year(
        protocol=bundle_ev.protocol,
        state=terminal,
        action=action,
        catalog=catalog,
        tax=tax_of(bundle_ev, catalog),
    )
    assert result.status == "input_error"
    assert [p.code for p in result.problems] == ["state_not_active"]


def test_the_valuation_context_records_every_conversion(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U63, lives_ev[U63])
    ctx = ev.valuation.context
    assert (ctx.simulation_years, ctx.valuation_year, ctx.valuation_source_mode) == (3, 4, "none")
    none = IllnessEventSource(
        mode="none", stream_version="e1-5c-illness-stream-v1", note="说明不参与指纹"
    )
    assert ctx.valuation_source_fingerprint == none.content_fingerprint
    assert ctx.actual_source_fingerprint == bundle_ev.protocol.illness_events.content_fingerprint
    assert ctx.valuation_protocol_fingerprint != ctx.actual_protocol_fingerprint
    text = "\n".join(ctx.conversions)
    for piece in ("simulation_years 3 → 4", "terminal → active", "年份不重置", "263.55", "264.55"):
        assert piece in text
    s = ctx.start
    assert (s.point, s.year_index, s.cash) == ("valuation_start", 4, cash(5420))
    assert x(s.energy_level) == Fraction(26455, 100)  # 估值额度，不是终态里的 263.55


# ---------------------------------------------------------------------- 现金


def test_more_cash_supports_a_plan_that_was_unaffordable(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """u61 与 u62 三年生活相同；期末现金 4,820 vs 13,320。较舒适的方案（含买洗烘一体机 2,400）一年要 7,320 的缓冲。"""
    low = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, lives_ev[U61])
    rich = evaluate(bundle_ev, scoring_ev, evaluation_ev, U62, lives_ev[U62])
    assert x(low.actual.l_actual) == x(rich.actual.l_actual) == f(S_BASIC)
    p2_low, p2_rich = plan(low, "p2_current_comfort"), plan(rich, "p2_current_comfort")
    assert (p2_low.status, p2_low.reason_kind) == ("infeasible", "plan_infeasible")
    assert "第 7 月给不了所选住房" in p2_low.reasons[0]
    assert (p2_rich.status, p2_rich.cash_end) == ("complete", cash(13320 - 2400 - 12 * 410))
    assert x(low.valuation.v_reference) == f(S_BASIC)
    assert x(rich.valuation.v_reference) == f(S_COMFORT)
    assert x(rich.combined.j) == Fraction(3, 4) * f(S_BASIC) + Fraction(1, 4) * f(S_COMFORT)
    assert x(rich.combined.j) > x(low.combined.j)


def test_v_reference_is_monotone_in_terminal_cash_and_saturates(
    bundle_ev, scoring_ev, evaluation_ev
):
    """同一套三年生活，只改开局现金：期末现金 = 开局 + 36 × 120。较舒适的方案恰好要期末 7,320（等于也够）。"""
    base = state_of(bundle_ev, U61)
    grid = [500, 2000, 2999, 3000, 4000, 9000, 20000]
    values = []
    for c in grid:
        start = revise(base, cash=cash(c))
        rs = run_clone(bundle_ev, scoring_ev, U61, state=start)
        assert rs[-1].next_state.cash == cash(c + 4320)
        ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, rs)
        values.append(x(ev.valuation.v_reference))
        assert x(ev.actual.l_actual) == f(S_BASIC)  # 钱不进入实际分数
        if c == 3000:
            assert plan(ev, "p2_current_comfort").cash_end == cash(0)
    assert values == sorted(values)  # 不减
    assert values == [f(S_BASIC)] * 3 + [f(S_COMFORT)] * 4  # 付得起之后不再随现金增加（饱和）


# ---------------------------------------------------------------------- 物品


def test_a_held_long_lived_item_is_used_not_bought_again_or_renewed(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """u63 第 3 年第 1 月买的洗烘一体机（10 年 = 120 个月）期末还剩 108 个月：估值照用，不重复付款，也不重置寿命。"""
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U63, lives_ev[U63])
    (h,) = ev.valuation.context.start.holdings
    assert (h.opportunity_id, h.quantity, h.remaining_months, h.beyond_horizon_months) == (
        DRYER_ID,
        1,
        108,
        96,
    )
    p2 = plan(ev, "p2_current_comfort")
    assert p2.status == "complete" and len(p2.skipped_purchases) == 1
    assert "照用旧的" in p2.skipped_purchases[0]
    assert DRYER_ID not in [i.opportunity_id for i in p2.items]
    assert p2.cash_end == cash(5420 - 12 * 410)  # 没有再付 2,400
    (end,) = p2.end.holdings
    assert (end.quantity, end.remaining_months) == (1, 96)  # 108 − 12，不是重新从 120 算
    assert any("还剩 96 个月服务" in n for n in ev.valuation.not_valued)
    assert x(ev.valuation.v_reference) == f(S_COMFORT)


def test_an_expired_item_is_gone_and_has_to_be_bought_again(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """u64 的二手洗衣机只能用一年，第 3 年末到期：期末现金更多（7,220 > 5,420），却付不起要重新买设备的较舒适方案。"""
    used = evaluate(bundle_ev, scoring_ev, evaluation_ev, U64, lives_ev[U64])
    dryer = evaluate(bundle_ev, scoring_ev, evaluation_ev, U63, lives_ev[U63])
    assert x(used.actual.l_actual) == x(dryer.actual.l_actual)  # 前三年分数相同
    assert used.valuation.context.start.holdings == ()
    p2 = plan(used, "p2_current_comfort")
    assert DRYER_ID in [i.opportunity_id for i in p2.items]
    assert (p2.status, p2.reason_kind) == ("infeasible", "plan_infeasible")
    assert "第 12 月给不了所选食品" in p2.reasons[0]
    assert x(used.valuation.v_reference) == f(S_BASIC) < f(S_COMFORT)
    assert x(dryer.valuation.v_reference) > x(used.valuation.v_reference)


def test_item_quantity_is_kept_and_presence_counts_once(bundle_ev, scoring_ev, evaluation_ev):
    """第 3 年买两台：期末持有 2 台、剩 108 个月；洗衣是"有没有"，第二台不再加分。"""
    plans = list(bundle_ev.plans_for(U63))
    data = plans[2].model_dump(mode="python")
    for item in data["items"]:
        if item["opportunity_id"] == DRYER_ID:
            item["quantity"] = 2
    plans[2] = type(plans[2]).model_validate(data)
    start = revise(state_of(bundle_ev, U63), cash=cash(6000))
    rs = run_clone(bundle_ev, scoring_ev, U63, state=start, plans=plans)
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U63, rs)
    (h,) = ev.valuation.context.start.holdings
    assert (h.quantity, h.remaining_months) == (2, 108)
    p1 = plan(ev, "p1_current_basic")
    assert set(p1.month_scores) == {S_BASIC_LAUNDRY}  # 与一台时相同
    (end,) = p1.end.holdings
    assert (end.quantity, end.remaining_months) == (2, 96)


# ---------------------------------------------------------------------- 资格与课程


def test_a_completed_qualification_supports_the_related_job_from_year_n_plus_1(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    learned = evaluate(bundle_ev, scoring_ev, evaluation_ev, U65, lives_ev[U65])
    half = evaluate(bundle_ev, scoring_ev, evaluation_ev, U66, lives_ev[U66])
    assert learned.valuation.context.start.abilities == ("accounting_cert（第 4 年起可用）",)
    # 第 3 年当年完成，当年的岗位与工资不变：实际仍是门店兼职
    assert lives_ev[U65][-1].next_state.employment.current_position_ids == (
        "a93.job.shop_parttime",
    )
    p3, p4 = plan(learned, "p3_qualified_basic"), plan(learned, "p4_qualified_comfort")
    assert (p3.status, p4.status) == ("complete", "complete")
    assert set(p3.month_scores) == {S_ACCOUNTANT_BASIC}
    assert set(p4.month_scores) == {S_ACCOUNTANT_COMFORT}
    # 工资不进满足感：会计到岗更多，同样的基本生活分数反而略低；资格的作用是让较舒适的生活付得起
    assert f(S_ACCOUNTANT_BASIC) < f(S_BASIC)
    assert plan(learned, "p2_current_comfort").status == "infeasible"
    assert learned.valuation.selected_plan_id == "p4_qualified_comfort"
    assert p4.cash_end == cash(5120 + 12 * (3000 - 1910) - 2400)
    for pid in ("p3_qualified_basic", "p4_qualified_comfort"):
        p = plan(half, pid)
        assert (p.status, p.reason_kind) == ("infeasible", "no_qualification")
        assert (
            "ability:accounting_cert" in p.reasons[0] and "未完成的课程不算取得资格" in p.reasons[0]
        )
    assert half.valuation.selected_plan_id == "p1_current_basic"


def test_qualification_scope_follows_occupation_and_employer(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """门店组长要求现职是零售；高级会计要求会计资格且现职就在这家办公室。不按行业名称或"相关"猜。"""
    lead = rebind(evaluation_ev, "qualified_job", "a93.job.shop_lead")
    senior = rebind(evaluation_ev, "qualified_job", "a93.job.office_senior")
    retail = evaluate(bundle_ev, scoring_ev, lead, U61, lives_ev[U61])
    accountant = evaluate(bundle_ev, scoring_ev, lead, U71, lives_ev[U71])
    assert plan(retail, "p3_qualified_basic").status == "complete"
    p = plan(accountant, "p3_qualified_basic")
    assert p.reason_kind == "no_qualification" and "occupation:SOC2018:41-2031" in p.reasons[0]
    other_employer = evaluate(bundle_ev, scoring_ev, senior, U65, lives_ev[U65])
    same_employer = evaluate(bundle_ev, scoring_ev, senior, U71, lives_ev[U71])
    p = plan(other_employer, "p3_qualified_basic")
    assert p.reason_kind == "no_qualification" and "employer:firm.office93" in p.reasons[0]
    assert plan(same_employer, "p3_qualified_basic").status == "complete"


def test_an_unfinished_course_is_neither_continued_nor_refunded(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U66, lives_ev[U66])
    (t,) = ev.valuation.context.start.training
    assert t == f"{COURSE_ID}：已参加 6/12 个月，学费已付"
    for p in ev.valuation.plans:
        assert COURSE_ID not in [i.opportunity_id for i in p.items]  # 不新开、不续学
        if p.end is not None:
            assert p.end.training == (t,)  # 进度不动
    assert plan(ev, "p1_current_basic").cash_end == cash(5120 + 12 * 120)  # 不退学费
    assert any("6/12" in n and "没有估值" in n for n in ev.valuation.not_valued)


# ---------------------------------------------------------------------- 疾病


def test_an_illness_at_the_end_continues_and_no_new_illness_starts(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """u68 带着病了 1 个月的大病进入终态（不治疗共 3 个月）。估值期：治疗 → 第 1 月末恢复；不治疗 → 第 2 月末恢复。
    v2：同一个基础生活模板 p1 展开治 / 不治两个分支（原 p5 = p1 的不治疗分支）。"""
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U68, lives_ev[U68])
    assert ev.valuation.context.start.illness == "大病（第 3 年第 12 月发病，已病 1 个月）"
    p1, p5 = plan(ev, "p1_current_basic.treat"), plan(ev, "p1_current_basic.decline")
    assert p1.health_states == ("major",) + ("healthy",) * 11
    assert p5.health_states == ("major", "major") + ("healthy",) * 10
    assert p1.new_onsets == p5.new_onsets == 0
    assert p1.cash_end == cash(6820 - 1380 - 2000 + 11 * 120)  # 病假没有工资、住院 2,000
    assert p5.cash_end == cash(6820 - 2 * 1380 + 10 * 120)
    assert x(p1.v_plan) == (f(S_SICK_BASIC) + 11 * f(S_BASIC)) / 12
    assert x(p5.v_plan) == (2 * f(S_SICK_BASIC) + 10 * f(S_BASIC)) / 12
    assert ev.valuation.selected_plan_id == "p1_current_basic.treat"
    healthy = evaluate(bundle_ev, scoring_ev, evaluation_ev, U67, lives_ev[U67])
    assert healthy.valuation.context.start.illness is None
    assert x(healthy.valuation.v_reference) == f(S_COMFORT) > x(ev.valuation.v_reference)


def test_events_of_the_actual_source_are_not_applied_in_the_valuation_year(
    bundle_ev, scoring_ev, evaluation_ev
):
    """实际协议里给第 4 年（= N + 1）写了一条指定事件：实际三年用不到它；估值期换成"没有新发疾病"，也不会套用它。"""
    src = bundle_ev.protocol.illness_events.model_dump(mode="python")
    src["injected"] = [
        *src["injected"],
        {
            "person_id": "u67",
            "repeat_index": 0,
            "year_index": 4,
            "month": 1,
            "severity": "major",
            "note": "第 N + 1 年的事件",
        },
    ]
    protocol = with_source(bundle_ev.protocol, IllnessEventSource.model_validate(src))
    rs = run_clone(bundle_ev, scoring_ev, U67, protocol=protocol)
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U67, rs, protocol=protocol)
    for p in ev.valuation.plans:
        if p.health_states:
            assert set(p.health_states) == {"healthy"} and p.new_onsets == 0
    assert x(ev.valuation.v_reference) == f(S_COMFORT)


def test_a_random_source_is_replaced_by_the_no_new_illness_condition(
    bundle_ev, scoring_ev, evaluation_ev
):
    protocol = with_source(bundle_ev.protocol, source("random", seed=61))
    rs = run_clone(bundle_ev, scoring_ev, U67, protocol=protocol)
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U67, rs, protocol=protocol)
    ctx = ev.valuation.context
    assert ctx.actual_source_fingerprint == protocol.illness_events.content_fingerprint
    assert ctx.valuation_source_mode == "none"
    assert all(p.new_onsets == 0 for p in ev.valuation.plans)


# ---------------------------------------------------------------------- 估值额度


def test_the_valuation_quota_comes_from_year_n_by_the_original_policy(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """u63 第 3 年完整年度满足感 = S_BASIC_LAUNDRY：250 × (1 + 0.10 × 0.58207…) = 264.5518… → 264.55。
    终态里的 263.55 是第 3 年自己用的额度（来自第 2 年）；估值不沿用它，也不用三年总分或 J。"""
    rs = lives_ev[U63]
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U63, rs)
    q = ev.valuation.context.quota
    assert (q.decision, q.source_year, q.score_decimal) == ("applied", 3, S_BASIC_LAUNDRY)
    assert x(q.raw_quota) == 250 * (1 + Fraction(1, 10) * f(S_BASIC_LAUNDRY))
    assert (x(q.quota), x(q.terminal_quota)) == (Fraction(26455, 100), Fraction(26355, 100))
    assert q.source_binding_fingerprint == rs[-1].binding_fingerprint
    assert q.score_decimal != repr(float(x(ev.actual.l_actual)))
    assert x(ev.valuation.context.start.energy_level) == x(q.quota)


@pytest.mark.parametrize(
    ("change", "decision"), [("disabled", "off_disabled"), ("zero", "off_alpha_zero")]
)
def test_without_feedback_the_valuation_quota_is_the_base(
    bundle_ev, scoring_ev, evaluation_ev, change, decision
):
    policy = bundle_ev.protocol.energy_feedback
    policy = revise(policy, mode="disabled") if change == "disabled" else revise(policy, alpha=0.0)
    protocol = revise(bundle_ev.protocol, energy_feedback=policy)
    rs = run_clone(bundle_ev, scoring_ev, U63, protocol=protocol)
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U63, rs, protocol=protocol)
    q = ev.valuation.context.quota
    assert (q.decision, x(q.quota), q.score_decimal) == (decision, 250, None)
    assert x(ev.valuation.context.start.energy_level) == 250


# ---------------------------------------------------------------------- 已知不可行 vs 资料未知


def test_known_infeasible_and_unknown_candidates_are_kept_apart(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """u69 在 B94：住院资料未知 → 要治疗的方案无法评价；B94 确定没有会计岗位 → 已知不可行。有无法评价的候选，就不给
    完整的 V_reference，已知最好结果只作诊断；J 为空，不改成 L，也不重新归一化。"""
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U69, lives_ev[U69])
    kinds = {p.plan_id: (p.status, p.reason_kind) for p in ev.valuation.plans}
    assert kinds == {  # v2：每个模板治 / 不治两个分支
        "p1_current_basic.treat": ("unevaluable", "data_error"),
        "p1_current_basic.decline": ("complete", "complete"),
        "p2_current_comfort.treat": ("unevaluable", "data_error"),
        # 不治疗时 6,820 撑不住较舒适的生活：第 4 月付完房租只剩 410，不够 450 的新鲜食材（已知不可行）
        "p2_current_comfort.decline": ("infeasible", "plan_infeasible"),
        "p3_qualified_basic.treat": ("infeasible", "slot_unavailable"),
        "p3_qualified_basic.decline": ("infeasible", "slot_unavailable"),
        "p4_qualified_comfort.treat": ("infeasible", "slot_unavailable"),
        "p4_qualified_comfort.decline": ("infeasible", "slot_unavailable"),
    }
    assert plan(ev, "p1_current_basic.treat").problem_codes == ("treatment_data_unknown",)
    v = ev.valuation
    assert v.status == "incomplete" and v.v_reference is None and v.selected_plan_id is None
    assert (v.branching, v.continuing_severity, v.trials) == (
        "continuing_illness_treat_or_decline",
        "major",
        8,
    )
    assert v.best_known_plan_id == "p1_current_basic.decline"
    assert x(v.best_known) == (2 * f(S_SICK_BASIC) + 10 * f(S_BASIC)) / 12
    assert ev.combined.status == "incomplete" and ev.combined.j is None
    assert any("不重新归一化" in r for r in ev.combined.reasons)
    assert ev.trajectory.status == "complete"  # 实际生活照样完整：资料缺口只影响估值


def test_an_unknown_slot_is_not_dropped_from_the_reference_set(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    unknown = rebind(evaluation_ev, "exercise", status="unknown", reason="测试：健身房资料未知")
    ev = evaluate(bundle_ev, scoring_ev, unknown, U62, lives_ev[U62])
    assert plan(ev, "p2_current_comfort").reason_kind == "slot_unknown"
    assert ev.valuation.status == "incomplete" and ev.combined.j is None
    assert ev.valuation.best_known_plan_id == "p1_current_basic"


def test_a_set_with_no_feasible_plan_is_not_zero_and_not_an_exit(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    plans = [p for p in evaluation_ev.life_templates if p.jobs == "qualified_alternative"]
    only = spec_with(evaluation_ev, life_templates=[p.model_dump(mode="python") for p in plans])
    ev = evaluate(bundle_ev, scoring_ev, only, U61, lives_ev[U61])
    v = ev.valuation
    assert v.status == "no_feasible_plan" and v.v_reference is None and v.best_known is None
    assert {p.reason_kind for p in v.plans} == {"no_qualification"}
    assert ev.combined.j is None
    assert (ev.trajectory.status, ev.trajectory.owner_category) == ("complete", "completed_scored")


# ---------------------------------------------------------------------- 试算互不污染、并列


def test_failed_trials_do_not_change_later_plans_or_the_result(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """先评估会失败的方案、再评估别的：每个方案的结果与顺序无关；同样输入两次结果逐字节相同。"""
    a = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, lives_ev[U61])
    reordered = spec_with(
        evaluation_ev,
        life_templates=[
            p.model_dump(mode="python") for p in reversed(evaluation_ev.life_templates)
        ],
    )
    b = evaluate(bundle_ev, scoring_ev, reordered, U61, lives_ev[U61])
    assert {p.plan_id: p for p in a.valuation.plans} == {p.plan_id: p for p in b.valuation.plans}
    assert x(a.valuation.v_reference) == x(b.valuation.v_reference)
    again = evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, lives_ev[U61])
    assert again.model_dump_json() == a.model_dump_json()
    assert again.result_fingerprint == a.result_fingerprint


def test_ties_are_broken_by_plan_id_for_display_only(bundle_ev, scoring_ev, evaluation_ev):
    """v2 下真实会出现的并列：u69 在 B94 第 3 年第 12 月得小病、计划不治，带着病了 1 个月的小病进入终态。小病不治共
    2 个月，估值期第 1 个月月末本来就恢复，治疗没有作用（B94 也没有门诊）——同一模板的治与不治是同一份生活，V 相等。
    并列时按分支编号的字典序展示（decline 在 treat 前面），数值不受影响。"""
    protocol = with_source(bundle_ev.protocol, injected(("u69", 3, 12, "minor")))
    rs = run_clone(bundle_ev, scoring_ev, U69, protocol=protocol)
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U69, rs, protocol=protocol)
    for template in (
        "p1_current_basic",
        "p2_current_comfort",
    ):  # 小病照常上班，8,200 也付得起较舒适的生活
        treat, decline = plan(ev, f"{template}.treat"), plan(ev, f"{template}.decline")
        assert treat.status == decline.status == "complete"
        assert treat.month_scores == decline.month_scores and x(treat.v_plan) == x(decline.v_plan)
        assert treat.cash_end == decline.cash_end
    # 最高分在 p2，它的治与不治并列：展示字典序在前的 decline
    assert ev.valuation.selected_plan_id == "p2_current_comfort.decline"
    assert ev.valuation.selected_treatment == "decline"


def test_the_valuation_state_is_not_a_persisted_state(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """估值上下文只存在于估值入口：结果里只有状态摘要与指纹，没有可以拿去继续结算的 PersonYearState。"""
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U63, lives_ev[U63])
    seen: list[object] = []

    def walk(o: object) -> None:
        if isinstance(o, PersonYearState):
            seen.append(o)
        elif hasattr(type(o), "model_fields"):
            for name in type(o).model_fields:
                walk(getattr(o, name))
        elif isinstance(o, tuple | list):
            for v in o:
                walk(v)

    walk(ev)
    assert seen == []
