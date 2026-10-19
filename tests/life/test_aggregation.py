"""E1-6B 城市汇总（DECISIONS/0029）：完整者均值、确认出局、模拟缺失范围、城市比较与评价对照。

两类测试分开：**公式构造单测**用手工构造的名单项记录（例如"6 人 J 合计 3.6"），只检查汇总算术；**真实小批次**按冻结名单
跑实际生活（``batch_eb`` 夹具），数字来自真实结算与评价。不把手工拼出来的 0.6 说成模拟结果。
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from common.life_aggregation_contracts import (
    RESERVED_AGGREGATION_KEYS,
    RosterConfig,
    RunFailure,
    internal_aggregation_models,
)
from common.life_contracts import LifeContractError
from common.money import Money
from econ.life_aggregation import (
    aggregate_arm,
    city_report,
    compare_cities,
    freeze_roster,
    missing_range_bounds,
    sensitivity,
)
from ledger.life_roster import load_roster, roster_problems

from .aggregation_support import A95, B96, ROSTER, evaluate, live, record
from .evaluation_support import evaluate as evaluate_ev
from .evaluation_support import spec_a81
from .settle_support import revise, with_items

MAIN, D099, BASIC = "arm.main", "arm.delta099", "arm.basic_only"


def x(a) -> Fraction:
    assert a is not None
    return a.fraction()


def report_of(rep, user, city):
    return next(r for r in rep.city_reports if (r.user_id, str(r.city)) == (user, city))


def entry_of(cr, clone):
    return next(e for e in cr.entries if e.clone_id == clone)


# ---------------------------------------------------------------------- 公式构造单测（不是模拟结果）


def test_the_hand_example_mean_shares_and_range():
    """原计划 10 人：6 人 J 完整、合计 3.6；2 人确认出局；2 人因模拟问题完全缺失。"""
    rs = [record("C", Fraction(6, 10), clone=f"c{i}") for i in range(6)]
    rs += [record("E", clone=f"e{i}") for i in range(2)]
    rs += [record("M", clone=f"m{i}") for i in range(2)]  # 没有可信记录：[0, 1]
    cr = city_report("arm.test", "user.test", "u900", rs)
    assert (cr.planned, cr.complete, cr.exits, cr.missing) == (10, 6, 2, 2)
    assert x(cr.mean_j) == Fraction(36, 10) / 6 == Fraction(6, 10)
    assert x(cr.exit_share) == Fraction(2, 10) and x(cr.missing_share) == Fraction(2, 10)
    assert (x(cr.range_lower), x(cr.range_upper)) == (Fraction(36, 10) / 8, Fraction(56, 10) / 8)
    assert (cr.range_lower_display, cr.range_upper_display) == (0.45, 0.7)
    assert cr.range_denominator == 8  # 出局者不在范围的分母里


def test_the_partial_record_example():
    """δ = 1：实际应有 36 个月，已知 24 个月、每月 0.6；另外 12 个月与未来 12 个月未知。"""
    v_low, v_high, lo, hi = missing_range_bounds(
        24 * Fraction(6, 10), Fraction(24), Fraction(36), Fraction(12), None
    )
    assert (v_low, v_high) == (0, 1)
    assert (lo, hi) == (Fraction(3, 10), Fraction(8, 10))
    r = record("M", clone="p", k=24 * Fraction(6, 10), w_known=Fraction(24))
    assert (x(r.lower), x(r.upper)) == (Fraction(3, 10), Fraction(8, 10))


def test_a_complete_actual_life_narrows_the_range():
    """实际 L 完整（36 个月都已知）但 V 缺失：只有未来那 12 个月是未知的，宽度是 12/48，不把整个 J 当作未知。"""
    k = 36 * Fraction(55, 100)
    r = record("M", clone="v", k=k, w_known=Fraction(36), reason="valuation_incomplete")
    assert (x(r.lower), x(r.upper)) == (k / 48, (k + 12) / 48)
    assert x(r.upper) - x(r.lower) == Fraction(1, 4)
    with_v = record("M", clone="w", k=Fraction(20), w_known=Fraction(30), v=Fraction(1, 2))
    assert x(with_v.v_low) == x(with_v.v_high) == Fraction(1, 2)  # V 完整时上下界都是 V


@pytest.mark.parametrize(
    ("cats", "mean", "lower", "upper", "exit_share", "note"),
    [
        (
            ["C", "C"],
            Fraction(1, 2),
            Fraction(1, 2),
            Fraction(1, 2),
            0,
            None,
        ),  # 全员完整：范围退化为均值
        (["E", "E"], None, None, None, 1, "全部确认出局"),
        (["M", "M"], None, Fraction(0), Fraction(1), 0, "没有完整评价的分身"),
        (["C"], Fraction(1, 2), Fraction(1, 2), Fraction(1, 2), 0, "只有 1 个完整评价分身"),
        (["E", "M"], None, Fraction(0), Fraction(1), Fraction(1, 2), "没有完整评价的分身"),
    ],
)
def test_edge_cases_are_explicit(cats, mean, lower, upper, exit_share, note):
    rs = [
        record(c, Fraction(1, 2) if c == "C" else None, clone=f"x{i}") for i, c in enumerate(cats)
    ]
    cr = city_report("arm.test", "user.test", "u900", rs)
    assert (cr.mean_j and x(cr.mean_j)) == mean
    assert (cr.range_lower and x(cr.range_lower)) == lower
    assert (cr.range_upper and x(cr.range_upper)) == upper
    assert x(cr.exit_share) == exit_share
    if note:
        assert any(note in n for n in cr.notes)


def test_an_empty_city_and_an_empty_roster_are_refused(batch_eb):
    with pytest.raises(LifeContractError, match="没有计划样本"):
        city_report("arm.test", "user.test", "u900", [])
    config, bundle, scoring, specs = load_roster(ROSTER)
    empty = revise(config, entries=[])
    with pytest.raises(LifeContractError, match="原计划名单为空"):
        freeze_roster(empty, bundle, scoring, specs)


def test_inconsistent_bounds_are_refused_not_clipped():
    with pytest.raises(ValueError, match="0 ≤ 下界 ≤ 上界 ≤ 1"):
        record("M", clone="bad", k=Fraction(40), w_known=Fraction(36))  # K 不可能大于全部实际权重


def test_city_difference_ranges():
    a = city_report(
        "arm.test",
        "user.test",
        "u900",
        [
            record("C", Fraction(7, 10), clone="a0"),
            record("M", clone="a1", k=Fraction(30), w_known=Fraction(36), v=Fraction(7, 10)),
        ],
    )
    b = city_report(
        "arm.test", "user.test", "u900", [record("C", Fraction(4, 10), clone="b0", city=B96)]
    )
    c = compare_cities(a, b)
    lo, hi = x(a.range_lower) - x(b.range_upper), x(a.range_upper) - x(b.range_lower)
    assert (x(c.range_difference_low), x(c.range_difference_high)) == (lo, hi)
    assert lo > 0 and c.direction == "a_higher" and "不是显著性检验" in c.note
    wide = city_report("arm.test", "user.test", "u900", [record("M", clone="w0", city=B96)])
    c2 = compare_cities(a, wide)
    assert c2.direction == "undetermined" and "不能确定方向" in c2.note
    assert c2.mean_difference is None  # B 没有完整评价分身：不给均值差


# ---------------------------------------------------------------------- 真实小批次


def test_every_planned_entry_has_exactly_one_class(batch_eb):
    rep = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    seen = []
    for cr in rep.city_reports:
        assert cr.planned == cr.complete + cr.exits + cr.missing == len(cr.entries)
        seen += [e.clone_id for e in cr.entries]
    assert sorted(seen) == sorted(e.clone_id for e in batch_eb.roster.entries)  # 未返回的也在
    alpha_a = report_of(rep, "user.alpha", A95)
    assert (alpha_a.planned, alpha_a.complete, alpha_a.exits, alpha_a.missing) == (5, 3, 1, 1)
    not_run = entry_of(alpha_a, "u101.a95.r4")
    assert (not_run.category, not_run.missing_reason) == ("M", "not_run")
    assert (x(not_run.lower), x(not_run.upper)) == (0, 1)


def test_the_mixed_city_example_by_hand(batch_eb):
    """α 在 A95：r0、r1、r3 完整，r2 确认出局，r4 没有返回。均值只用三个完整的；出局比例 1/5；范围分母 4。"""
    cr = report_of(
        aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN]), "user.alpha", A95
    )
    js = [x(entry_of(cr, f"u101.a95.r{i}").j) for i in (0, 1, 3)]
    assert x(cr.mean_j) == sum(js) / 3
    assert x(cr.mean_l) == sum(x(entry_of(cr, f"u101.a95.r{i}").l_actual) for i in (0, 1, 3)) / 3
    assert x(cr.exit_share) == Fraction(1, 5) and x(cr.missing_share) == Fraction(1, 5)
    assert cr.range_denominator == 4
    assert (x(cr.range_lower), x(cr.range_upper)) == (sum(js) / 4, (sum(js) + 1) / 4)
    assert cr.exit_clone_ids == ("u101.a95.r2",)


def test_known_months_narrow_a_stopped_clones_range(batch_eb):
    """α r1 在 B96 第 2 年第 5 月停止（资料缺口）：此前 16 个月已知，其余 20 个实际月与 12 个未来月未知。"""
    cr = report_of(
        aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN]), "user.alpha", B96
    )
    r = entry_of(cr, "u101.b96.r1")
    assert (r.category, r.missing_reason) == ("M", "stopped")
    assert x(r.known_weight) == 16
    k = x(r.known_weighted_sum)
    assert (x(r.lower), x(r.upper)) == (k / 48, (k + 20 + 12) / 48)
    assert x(r.lower) > 0 and x(r.upper) < 1


def test_completed_scored_is_not_enough_for_the_mean(batch_eb):
    """β r1 在 B96：实际三年完整（owner_category = completed_scored），但住院资料未知，估值不完整，J 为空——不进均值，
    进缺失范围，而且只有未来那 12 个月未知。"""
    rep = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    ev = next(e for e in batch_eb.evaluations[MAIN] if e.clone_id == "u102.b96.r1")
    assert ev.trajectory.owner_category == "completed_scored" and ev.combined.j is None
    cr = report_of(rep, "user.beta", B96)
    r = entry_of(cr, "u102.b96.r1")
    assert (r.category, r.missing_reason) == ("M", "valuation_incomplete")
    assert x(cr.mean_j) == x(entry_of(cr, "u102.b96.r0").j)  # 均值只用 r0
    assert x(r.upper) - x(r.lower) == Fraction(12, 48)
    # 这里没有任何已知完整的分支：不给 V，范围里 V 取 0 与 1（有已知最好分支的例子见下一个测试）
    assert ev.valuation.best_known is None and r.v_reference is None
    assert (x(r.v_low), x(r.v_high)) == (0, 1)


def test_best_known_is_only_a_diagnosis(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    """E1-6A 样例的 u69（B94 住院资料未知、带着大病结束）：实际三年完整，估值不完整，但有已知最好分支（不治疗）。在
    这份样例上单独冻结一个一人名单汇总：记为 M（估值不完整）；已知最好结果只是诊断——不当作 V，也不用来收窄范围。"""
    clone = "u69.b94.gap"
    state = bundle_ev.initial_state(clone)
    config = RosterConfig.model_validate(
        {
            "roster_id": "roster.e1-6a.u69",
            "status": "exploratory_synthetic",
            "aggregation_version": "e1-6b-aggregation-v1",
            "life_input": "configs/life/synthetic_e1_6a_evaluation.yaml",
            "scoring_config": "configs/life/scoring_e1_6a_evaluation.yaml",
            "comparison_group_id": scoring_ev.comparison_group.group_id,
            "users": [{"user_id": "user.u69", "person_id": "u69", "label": "E1-6A 样例的 u69"}],
            "arms": [
                {
                    "arm_id": MAIN,
                    "role": "main",
                    "evaluation_config": "configs/life/evaluation_e1_6a_v2.yaml",
                }
            ],
            "entries": [
                {
                    "user_id": "user.u69",
                    "clone_id": clone,
                    "city": state.city,
                    "repeat_index": state.health.repeat_index,
                }
            ],
        }
    )
    roster = freeze_roster(config, bundle_ev, scoring_ev, {MAIN: evaluation_ev})
    ev = evaluate_ev(bundle_ev, scoring_ev, evaluation_ev, clone, lives_ev[clone])
    assert ev.trajectory.owner_category == "completed_scored"
    assert (ev.valuation.status, ev.valuation.best_known_plan_id) == (
        "incomplete",
        "p1_current_basic.decline",
    )
    ((cr,),) = [aggregate_arm(roster, MAIN, [ev]).city_reports]
    (r,) = cr.entries
    assert (r.category, r.missing_reason) == ("M", "valuation_incomplete")
    assert r.v_reference is None and (x(r.v_low), x(r.v_high)) == (0, 1)
    k = x(ev.actual.known_weighted_sum)
    assert x(r.known_weight) == 36 and (x(r.lower), x(r.upper)) == (k / 48, (k + 12) / 48)
    assert "p1_current_basic.decline" in r.detail and "只是诊断" in r.detail
    assert cr.mean_j is None and any("没有完整评价的分身" in n for n in cr.notes)


def test_stops_and_valuation_gaps_are_not_life_exits(batch_eb):
    rep = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    alpha_b = report_of(rep, "user.alpha", B96)
    assert alpha_b.exits == 0
    for clone in ("u101.b96.r1", "u101.b96.r2"):
        r = entry_of(alpha_b, clone)
        assert (r.category, r.missing_reason) == ("M", "stopped")
        assert "资料缺口停止" in r.event_tags and "出局" not in r.detail.split("不改称")[0]
    # 同一场病（r2：第 1 年第 2 月大病）在 A95 是确认出局，在 B96 是停止
    alpha_a = report_of(rep, "user.alpha", A95)
    assert entry_of(alpha_a, "u101.a95.r2").category == "E"
    assert entry_of(alpha_b, "u101.b96.r2").category == "M"
    # 只含基本生活模板的对照：β 在 B96 的 r0 没有可用参考分支（远处住房都违反硬要求）→ 估值不完整，不是出局
    small = aggregate_arm(batch_eb.roster, BASIC, batch_eb.evaluations[BASIC])
    r0 = entry_of(report_of(small, "user.beta", B96), "u102.b96.r0")
    assert (r0.category, r0.missing_reason) == ("M", "valuation_incomplete")
    assert "no_feasible_plan" in r0.detail


def test_an_infeasible_plan_and_a_short_return_are_missing_not_exits(batch_eb):
    """真实引擎：α r0 在 A95 第 1 年改成近处住房 + 新鲜食材（每月 1,400 + 450，没有兜底）→ 计划不可行：模拟问题，
    归入 M（计划或执行未完成），不是生活出局，也不提取任何月份。另一份只返回前两年的结果：已知的 24 个月照算。"""
    bundle, scoring, spec = batch_eb.bundle, batch_eb.scoring, batch_eb.specs[MAIN]
    clone = "u101.a95.r0"
    plans = list(bundle.plans_for(clone))
    year1 = with_items(
        plans[0],
        {"opportunity_id": "a95.housing.near", "quantity": 1, "start_month": 1, "end_month": 12},
        {"opportunity_id": "a95.food.fresh", "quantity": 1, "start_month": 1, "end_month": 12},
        {
            "opportunity_id": "a95.job.shop_parttime",
            "quantity": 1,
            "start_month": 1,
            "end_month": 12,
        },
    )
    rs = live(bundle, scoring, clone, [year1, *plans[1:]])
    assert [r.handoff for r in rs] == ["settlement_failed"]
    ev = evaluate(bundle, scoring, spec, clone, rs)
    others = [e for e in batch_eb.evaluations[MAIN] if e.clone_id != clone]
    r = entry_of(
        report_of(aggregate_arm(batch_eb.roster, MAIN, [*others, ev]), "user.alpha", A95), clone
    )
    assert (r.category, r.missing_reason) == ("M", "plan_or_execution")
    assert "plan_infeasible" in r.detail and (x(r.lower), x(r.upper)) == (0, 1)

    short = evaluate(bundle, scoring, spec, clone, batch_eb.lives[clone][:2])
    r = entry_of(
        report_of(aggregate_arm(batch_eb.roster, MAIN, [*others, short]), "user.alpha", A95), clone
    )
    assert (r.category, r.missing_reason) == ("M", "not_run")
    assert x(r.known_weight) == 24 and "只返回到第 2 年" in r.detail
    k = x(r.known_weighted_sum)
    assert k == sum(x(m.contribution) for m in short.actual.months[:24])
    assert (x(r.lower), x(r.upper)) == (k / 48, (k + 12 + 12) / 48)


def test_protocols_without_health_rules_can_be_aggregated(bundle_sv, scoring_sv):
    """E1-5B 的协议没有疾病规则，评价结果没有重复分身序号（为空）：名单照样写序号，只是无从核对；人、分身、城市照样
    核对。修订前序号被拿来比，这类协议的每份结果都会被误判成输入错误。"""
    clone = "u81.a81.save"
    spec = spec_a81()
    ev = evaluate(bundle_sv, scoring_sv, spec, clone, live(bundle_sv, scoring_sv, clone))
    assert ev.repeat_index is None and ev.combined.j is not None
    state = bundle_sv.initial_state(clone)
    config = RosterConfig.model_validate(
        {
            "roster_id": "roster.e1-5b.u81",
            "status": "exploratory_synthetic",
            "aggregation_version": "e1-6b-aggregation-v1",
            "life_input": "configs/life/synthetic_e1_5b_savings.yaml",
            "scoring_config": "configs/life/scoring_e1_5b_savings.yaml",
            "comparison_group_id": scoring_sv.comparison_group.group_id,
            "users": [{"user_id": "user.u81", "person_id": "u81", "label": "E1-5B 样例的 u81"}],
            "arms": [{"arm_id": MAIN, "role": "main", "evaluation_config": "（测试里直接构造）"}],
            "entries": [
                {"user_id": "user.u81", "clone_id": clone, "city": state.city, "repeat_index": 0}
            ],
        }
    )
    roster = freeze_roster(config, bundle_sv, scoring_sv, {MAIN: spec})
    ((cr,),) = [aggregate_arm(roster, MAIN, [ev]).city_reports]
    (r,) = cr.entries
    assert r.category == "C" and x(r.j) == x(ev.combined.j) == x(cr.mean_j)
    moved = ev.model_copy(update={"person_id": "u82"})
    moved = revise(moved, result_fingerprint=moved.expected_result_fingerprint())
    ((cr,),) = [aggregate_arm(roster, MAIN, [moved]).city_reports]
    assert cr.entries[0].missing_reason == "input_config_program_error"  # 人对不上照样拒绝


def test_exit_chain_is_traceable_to_records(batch_eb):
    """α r2 在 A95 的事件链：每一步都能在逐月事实、事件或出局记录里找到。"""
    rep = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    r = entry_of(report_of(rep, "user.alpha", A95), "u101.a95.r2")
    ev = next(e for e in batch_eb.evaluations[MAIN] if e.clone_id == "u101.a95.r2")
    texts = [s.text for s in r.event_chain]
    assert [s.source for s in r.event_chain] == ["month_fact", "event", "month_fact", "exit_record"]
    facts = {(f.year_index, f.month): f for f in ev.trajectory.month_facts}
    m2 = facts[(1, 2)]
    assert (m2.cash_start, m2.income_gross, m2.spent, m2.cash_end) == tuple(
        Money(amount=a, numeraire="USD_2024_LOCAL") for a in (2120, 0, 1380, 740)
    )
    assert m2.sick_leave and m2.treatment_status == "unaffordable"
    assert "月初现金 2,120，收入 0（大病病假，没有工资），付出 1,380" in texts[2]
    exit_ = ev.trajectory.exit
    assert (exit_.year_index, exit_.month, exit_.shortfall.amount) == (1, 3, 640)
    assert "差 640 → 付不起最低生活，出局" in texts[3]
    assert set(r.event_tags) == {"出现过大病病假", "最低住房与食品付不起"}


def test_hard_requirements_are_reported_not_used_to_drop_anyone(batch_eb):
    rep = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    cr = report_of(rep, "user.beta", B96)
    (h,) = cr.hard_requirements
    assert (h.need, h.violated, h.violated_clone_ids) == (
        "short_commute",
        2,
        ("u102.b96.r0", "u102.b96.r1"),
    )
    r0 = entry_of(cr, "u102.b96.r0")
    assert r0.category == "C"  # 违反硬要求也不删人、不扣分
    assert any("硬要求" in n for n in cr.notes)
    ok = report_of(rep, "user.beta", A95).hard_requirements[0]
    assert (ok.satisfied_every_month, ok.violated) == (2, 0)


def test_input_order_does_not_matter(batch_eb):
    a = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    b = aggregate_arm(batch_eb.roster, MAIN, list(reversed(batch_eb.evaluations[MAIN])))
    assert a.model_dump_json() == b.model_dump_json()
    assert a.report_fingerprint == b.report_fingerprint == a.expected_report_fingerprint()


def test_duplicates_strangers_and_tampering_are_caught(batch_eb):
    evs = list(batch_eb.evaluations[MAIN])
    target = next(e for e in evs if e.clone_id == "u101.a95.r0")
    # 重复：同一名单项收到两份
    rep = aggregate_arm(batch_eb.roster, MAIN, [*evs, target])
    r = entry_of(report_of(rep, "user.alpha", A95), "u101.a95.r0")
    assert (r.category, r.missing_reason) == ("M", "input_config_program_error")
    assert "收到 2 份评价结果" in r.detail
    # 错城市：外层改成 B96（不重算指纹）→ 结果核对不通过，不提取任何月份
    moved = target.model_copy(
        update={"city": next(e for e in evs if e.clone_id == "u101.b96.r0").city}
    )
    rep = aggregate_arm(batch_eb.roster, MAIN, [moved if e is target else e for e in evs])
    r = entry_of(report_of(rep, "user.alpha", A95), "u101.a95.r0")
    assert r.missing_reason == "input_config_program_error" and "指纹不一致" in r.detail
    assert (x(r.lower), x(r.upper)) == (0, 1)
    # 错用户：把人改成 β 并重新算好指纹（形式上自洽的对象）→ 指纹核对通过，但身份对不上名单项
    relabeled = target.model_copy(update={"person_id": "u102"})
    forged = revise(relabeled, result_fingerprint=relabeled.expected_result_fingerprint())
    assert forged.result_fingerprint == forged.expected_result_fingerprint()
    rep = aggregate_arm(batch_eb.roster, MAIN, [forged if e is target else e for e in evs])
    r = entry_of(report_of(rep, "user.alpha", A95), "u101.a95.r0")
    assert r.missing_reason == "input_config_program_error" and "不是名单项" in r.detail
    # 混合规格：δ = 0.99 那个臂的结果交给主臂汇总
    other = next(e for e in batch_eb.evaluations[D099] if e.clone_id == "u101.a95.r0")
    rep = aggregate_arm(batch_eb.roster, MAIN, [other if e is target else e for e in evs])
    r = entry_of(report_of(rep, "user.alpha", A95), "u101.a95.r0")
    assert r.missing_reason == "input_config_program_error" and "不是运行臂 arm.main" in r.detail
    # 名单外的结果：单列拒收，不进任何城市
    stranger = target.model_copy(update={"clone_id": "u999.a95.r0"})
    rep = aggregate_arm(batch_eb.roster, MAIN, [*evs, stranger])
    assert [s.clone_id for s in rep.rejected_submissions] == ["u999.a95.r0"]
    assert sum(cr.planned for cr in rep.city_reports) == len(batch_eb.roster.entries)


def test_a_single_failure_keeps_its_reason_and_spares_the_rest(batch_eb):
    evs = [e for e in batch_eb.evaluations[MAIN] if e.clone_id != "u101.b96.r0"]
    failure = RunFailure(
        clone_id="u101.b96.r0",
        stage="evaluation",
        error_kind="unexpected",
        message="测试：意外异常",
    )
    rep = aggregate_arm(batch_eb.roster, MAIN, evs, [failure])
    base = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    cr, cr0 = report_of(rep, "user.alpha", B96), report_of(base, "user.alpha", B96)
    r = entry_of(cr, "u101.b96.r0")
    assert (r.category, r.missing_reason, r.unexpected_error) == (
        "M",
        "input_config_program_error",
        True,
    )
    assert "意外程序错误" in r.detail and any("意外程序错误" in n for n in cr.notes)
    assert rep.unexpected_errors == 1
    for clone in ("u101.b96.r3", "u101.b96.r4"):  # 其他合法结果照旧
        assert entry_of(cr, clone) == entry_of(cr0, clone)
    assert report_of(rep, "user.alpha", A95) == report_of(base, "user.alpha", A95)


def test_evaluation_controls_reuse_the_same_lives(batch_eb):
    main = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    before = main.model_dump_json()
    for arm in (D099, BASIC):
        other = aggregate_arm(batch_eb.roster, arm, batch_eb.evaluations[arm])
        s = sensitivity(main, other, batch_eb.evaluations[MAIN], batch_eb.evaluations[arm])
        assert s.actual_lives_identical  # 同一批实际生活，只改评价假设
        for ev_main, ev_other in zip(
            batch_eb.evaluations[MAIN], batch_eb.evaluations[arm], strict=True
        ):
            assert ev_main.actual.months[0].score_decimal == ev_other.actual.months[0].score_decimal
    assert main.model_dump_json() == before  # 不覆盖主结果
    # L、V、J 的均值都报：δ = 0.99 改变实际月份的权重，L 也变；只改模板集合时 L 不变（同一批生活、同一个 δ），V 与 J 变
    d099 = aggregate_arm(batch_eb.roster, D099, batch_eb.evaluations[D099])
    s99 = sensitivity(main, d099, batch_eb.evaluations[MAIN], batch_eb.evaluations[D099])
    alpha_a = next(c for c in s99.changes if (c.user_id, str(c.city)) == ("user.alpha", A95))
    assert x(alpha_a.mean_l_main) != x(alpha_a.mean_l_other)
    assert x(alpha_a.mean_j_main) == x(report_of(main, "user.alpha", A95).mean_j)
    small = aggregate_arm(batch_eb.roster, BASIC, batch_eb.evaluations[BASIC])
    s = sensitivity(main, small, batch_eb.evaluations[MAIN], batch_eb.evaluations[BASIC])
    beta_a = next(c for c in s.changes if (c.user_id, str(c.city)) == ("user.beta", A95))
    assert x(beta_a.mean_l_main) == x(beta_a.mean_l_other)  # 同样两个完整分身、同样的实际生活
    assert x(beta_a.mean_v_other) < x(beta_a.mean_v_main)  # r0 没有了较舒适模板，V 变低
    assert x(beta_a.mean_j_other) < x(beta_a.mean_j_main)
    beta_b = next(c for c in s.changes if (c.user_id, str(c.city)) == ("user.beta", B96))
    assert (beta_b.complete_main, beta_b.complete_other) == (1, 0)
    assert len(beta_b.moved) == 1 and beta_b.moved[0].startswith("u102.b96.r0：C → M")
    # 不信 model_copy 改过的报告或评价结果
    edited = small.model_copy(update={"unexpected_errors": 3})
    with pytest.raises(LifeContractError, match="汇总报告内容与它记下的指纹不一致"):
        sensitivity(main, edited, batch_eb.evaluations[MAIN], batch_eb.evaluations[BASIC])
    first = batch_eb.evaluations[BASIC][0]
    swapped = [
        first.model_copy(update={"clone_id": "u101.a95.r9"}),
        *batch_eb.evaluations[BASIC][1:],
    ]
    with pytest.raises(LifeContractError, match="评价结果内容与它记下的指纹不一致"):
        sensitivity(main, small, batch_eb.evaluations[MAIN], swapped)


def test_roster_declarations_are_checked_before_freezing(batch_eb):
    config, bundle, scoring, specs = load_roster(ROSTER)
    data = config.model_dump(mode="python")
    with pytest.raises(ValueError, match="恰好要有一个主臂"):
        RosterConfig.model_validate({**data, "arms": [{**a, "role": "main"} for a in data["arms"]]})
    with pytest.raises(ValueError, match="同一个分身出现不止一次"):
        RosterConfig.model_validate({**data, "entries": [*data["entries"], data["entries"][0]]})
    first, rest = data["entries"][0], data["entries"][1:]
    assert first["clone_id"] == "u101.a95.r0"
    b96 = next(e["city"] for e in rest if e["clone_id"] == "u101.b96.r0")
    wrong_city = revise(
        config,
        entries=[{**first, "city": b96}, *(e for e in rest if e["clone_id"] != "u101.b96.r0")],
    )
    problems = roster_problems(wrong_city, bundle, scoring, specs)
    assert any("u101.a95.r0: 开局状态在 US:SYNTH2026:A95" in p for p in problems)
    wrong_repeat = revise(config, entries=[{**first, "repeat_index": 9}, *rest])
    assert any("重复分身序号" in p for p in roster_problems(wrong_repeat, bundle, scoring, specs))
    richer = revise(
        bundle,
        initial_states=[
            revise(s, cash=Money(amount=9999, numeraire="USD_2024_LOCAL"))
            if s.clone_id == "u101.b96.r3"
            else s
            for s in bundle.initial_states
        ],
    )
    assert any(
        "「开局现金」（cash）在各分身之间不一致" in p
        for p in roster_problems(config, richer, scoring, specs)
    )


def test_frozen_roster_identity_is_stable(batch_eb):
    config, bundle, scoring, specs = load_roster(ROSTER)
    again = freeze_roster(config, bundle, scoring, specs)
    assert (
        again == batch_eb.roster
        and again.content_fingerprint == batch_eb.roster.content_fingerprint
    )
    relabeled = revise(
        config, users=[{**u.model_dump(), "label": "改了标签"} for u in config.users]
    )
    assert (
        freeze_roster(relabeled, bundle, scoring, specs).content_fingerprint
        == again.content_fingerprint
    )


def test_aggregation_internals_never_reach_the_payload(batch_eb):
    from pydantic import BaseModel

    from agentlayer.life_views import (
        FORBIDDEN_PAYLOAD_KEYS,
        AgentObservation,
        VisibleFeedback,
        assert_payload_clean,
    )

    assert RESERVED_AGGREGATION_KEYS <= FORBIDDEN_PAYLOAD_KEYS
    names: set[str] = set()
    seen: set[type] = set()

    def walk(model):
        if model in seen:
            return
        seen.add(model)
        for name, field in model.model_fields.items():
            names.add(name)
            stack = [field.annotation]
            while stack:
                ann = stack.pop()
                stack.extend(getattr(ann, "__args__", ()))
                if isinstance(ann, type) and issubclass(ann, BaseModel):
                    walk(ann)

    walk(AgentObservation)
    walk(VisibleFeedback)
    assert not RESERVED_AGGREGATION_KEYS & names
    rep = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    cr = rep.city_reports[0]
    objects = [
        batch_eb.roster,
        batch_eb.roster.arms[0],
        batch_eb.roster.entries[0],
        rep,
        cr,
        cr.entries[0],
        rep.comparisons[0],
    ]
    assert {type(o) for o in objects} <= set(internal_aggregation_models())
    for obj in objects:
        with pytest.raises(LifeContractError, match="出现在可见字段"):
            assert_payload_clean({"notes": [{"deep": [obj]}]})
        with pytest.raises(LifeContractError, match=r"内部键|原样序列化的内部评分对象"):
            assert_payload_clean({"notes": [{"nested": obj.model_dump(mode="json")}]})


def test_the_transport_health_counterexample_is_kept(batch_eb):
    """β 只看重交通：交通—健康反例的机制原样保留（不调疾病惩罚、不改交通权重）。β 在 A95 的 r1 带大病进入估值期：治 /
    不治两个分支都试，选分高的那个；它这次选了治疗——门店兼职每月只到岗 10 次，少通勤的增益没有超过健康扣分。"""
    rep = aggregate_arm(batch_eb.roster, MAIN, batch_eb.evaluations[MAIN])
    r = entry_of(report_of(rep, "user.beta", A95), "u102.a95.r1")
    ev = next(e for e in batch_eb.evaluations[MAIN] if e.clone_id == "u102.a95.r1")
    treat = next(p for p in ev.valuation.plans if p.plan_id == "p1_current_basic.treat")
    decline = next(p for p in ev.valuation.plans if p.plan_id == "p1_current_basic.decline")
    assert ev.valuation.branching == "continuing_illness_treat_or_decline"
    assert r.selected_plan_id == "p1_current_basic.treat" and x(treat.v_plan) > x(decline.v_plan)


def test_evaluate_helper_matches_the_batch(batch_eb):
    """辅助函数与批次用的是同一份评价入口（防止测试自己另算一套）。"""
    clone = "u101.a95.r0"
    again = evaluate(
        batch_eb.bundle, batch_eb.scoring, batch_eb.specs[MAIN], clone, batch_eb.lives[clone]
    )
    assert again == next(e for e in batch_eb.evaluations[MAIN] if e.clone_id == clone)
