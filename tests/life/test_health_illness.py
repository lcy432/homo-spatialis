"""E1-5C：疾病、应急与恢复的结算侧（`DECISIONS/0027`；prompt 第九节第 5–14、16、17 项）。

核对的是真实行为：现金按整数分精确比较，月份、病情、治疗结果、暂停、病假、学习进度、目标记录与停止边界逐项比对。
手算见 ``configs/life/synthetic_e1_5c_health.yaml`` 文末。
"""

from __future__ import annotations

import pytest

from common.life_contracts import (
    HealthState,
    LifeContractError,
    PersonYearState,
    SyntheticTaxPolicy,
)
from econ.life_feedback import apply_feedback
from econ.life_health import illness_key, uniform_of
from econ.life_settlement import binding_from_policy, settle_year
from econ.life_year import settle_and_score
from ledger.life_catalog import cross_validate, load_life_bundle

from .health_support import (
    CAFE,
    CLEANING,
    CLINIC,
    COURSE_ID,
    DECLINE,
    DRYER_ID,
    FOOD,
    GYM,
    HOME,
    HOSPITAL,
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
    source,
    with_source,
    year,
)
from .settle_support import bundle_from, catalog_of, cents, revise, state_of, tax_of


def _settle(bundle, state, action, protocol=None):
    catalog = catalog_of(bundle, state)
    return settle_year(
        protocol=protocol or bundle.protocol,
        state=state,
        action=action,
        catalog=catalog,
        tax=tax_of(bundle, catalog),
    )


# ==========================================================================
# 第 5 项：手算例子——小病治疗、不治、治不起；大病病假与治疗后恢复
# ==========================================================================


def test_minor_illness_treated_pays_once_and_recovers_at_month_end(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u91.a91.treat")
    assert r.settlement.status == "completed"
    assert cash_ends(r) == [
        x * 100 for x in (620, 740, 560, 680, 800, 920, 1040, 1160, 1280, 1400, 1520, 1640)
    ]
    assert flows_for(r, CLINIC) == [(3, -30000)]  # 一次疾病只付一次
    assert health_months(r)[2] == (3, "minor", "lived", "recovered_treated", "treated")
    assert [m[1] for m in health_months(r)] == ["healthy"] * 2 + ["minor"] + ["healthy"] * 9
    m3 = r.settlement.outcome.resources.months[2]
    rows = {
        (c.category, c.opportunity_id or c.task): (c.hours.exact, c.burden.exact)
        for c in m3.contributions
    }
    assert rows[("recovery", "recovery_minor")] == ("10", "20")
    assert rows[("treatment", CLINIC)] == ("2", "2")
    assert m3.energy_used.exact == "551/4"  # 115.75 + 20 + 2 = 137.75
    # 小病照常上班：第 3 月照样领工资、照样到岗
    assert m3.onsite_visits == 10
    assert any(f.month == 3 and f.kind == "income_gross" for f in r.settlement.outcome.cash_flows)
    ep = r.next_state.health.history[0]
    assert (ep.severity, ep.recovery, ep.recovered_month, ep.months_ill) == (
        "minor",
        "treated",
        3,
        1,
    )
    assert cents(ep.treatment.amount_paid) == 30000


def test_minor_illness_declined_runs_its_course_without_charges(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u92.a91.decline")
    assert cash_ends(r)[-1] == 194000  # 500 + 12 × 120，没有医疗支出
    hm = health_months(r)
    assert hm[2] == (3, "minor", "lived", "continues", "declined")
    assert hm[3] == (4, "minor", "lived", "recovered_natural", "declined")
    assert [x.state for x in r.settlement.outcome.health.months].count("minor") == 2
    ep = r.next_state.health.history[0]
    assert (ep.recovery, ep.recovered_month, ep.months_ill, ep.treatment) == ("natural", 4, 2, None)
    assert flows_for(r, CLINIC) == []


def test_minor_illness_unaffordable_then_no_benefit(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u93.a91.broke")
    hm = health_months(r)
    assert hm[0] == (1, "minor", "lived", "continues", "unaffordable")
    # 第 2 月仍是小病，但本来就在月末结束：治疗没有作用，不购买（现金 240 ≥ 300？不够也一样不尝试）
    assert hm[1] == (2, "minor", "lived", "recovered_natural", "no_benefit")
    assert flows_for(r, CLINIC) == []
    assert cash_ends(r)[:2] == [12000, 24000]
    note = r.settlement.outcome.health.months[0].treatment.note
    assert "120.00" in note and "300.00" in note


def test_major_illness_is_a_whole_month_of_unpaid_sick_leave_then_treated(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u94.a91.major")
    out = r.settlement.outcome
    assert cash_ends(r)[3:6] == [348000, 10000, 22000]  # 3,480 − 1,380 − 2,000 = 100
    assert flows_for(r, HOSPITAL) == [(5, -200000)]
    assert [f for f in out.cash_flows if f.month == 5 and f.kind == "income_gross"] == []
    m5 = out.resources.months[4]
    assert (m5.onsite_visits, m5.commute_status, m5.job_workdays_sum) == (0, "no_onsite_work", 0)
    assert m5.energy_used.exact == "95"  # 食品 15 + 洗衣 6 + 打扫 6 + 恢复 60 + 住院 8
    shop = next(i for i in out.items if i.opportunity_id == SHOP["opportunity_id"])
    assert [p.month for p in shop.months_paused] == [5] and shop.months_paused[
        0
    ].kind == "sick_leave"
    assert shop.covered_months == tuple(m for m in range(1, 13) if m != 5)
    assert shop.status == "partially_executed"
    assert health_months(r)[4] == (5, "major", "lived", "recovered_treated", "treated")


def test_declining_major_treatment_means_three_unpaid_months_and_an_exit(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u95.a91.decline")
    assert r.settlement.status == "exited" and r.handoff == "exited"
    e = r.settlement.outcome.exit
    assert (e.month, cents(e.shortfall)) == (7, 66000)  # 720 < 1,380
    assert cash_ends(r) == [312000, 324000, 336000, 348000, 210000, 72000, 72000]
    hm = health_months(r)
    assert hm[4:] == [
        (5, "major", "lived", "continues", "declined"),
        (6, "major", "lived", "continues", "declined"),
        (7, "major", "exit_month", "not_applicable", None),
    ]
    # 出局状态带着没好的大病（病了 3 个月），不自动痊愈
    assert r.next_state.status == "exited" and r.next_state.health.episode.months_ill == 3
    # 出局当月（第 7 月）的岗位病假在健康记录、执行记录与岗位记录里一致（2026-10-11 自查补上：原先健康记录漏了这一条暂停）
    out = r.settlement.outcome
    m7 = out.health.months[6]
    assert m7.sick_leave
    assert [(a.opportunity_id, a.kind) for a in m7.paused] == [
        (SHOP["opportunity_id"], "sick_leave")
    ]
    shop = next(i for i in out.items if i.opportunity_id == SHOP["opportunity_id"])
    assert [(p.month, p.kind) for p in shop.months_paused] == [
        (5, "sick_leave"),
        (6, "sick_leave"),
        (7, "sick_leave"),
    ]
    (pos,) = out.career.positions
    assert (pos.months, pos.sick_leave_months) == ((1, 2, 3, 4), (5, 6, 7))


# ==========================================================================
# 第 6 项：停工正确减少工资、税、工时与到岗（含两份工作）
# ==========================================================================


def test_sick_leave_removes_wage_tax_hours_and_visits_for_two_jobs(bundle_hl, scoring_hl):
    clone = "u94.a91.major"
    action = plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, CAFE)
    cat = catalog_of(bundle_hl, state_of(bundle_hl, clone))
    taxed = SyntheticTaxPolicy.model_validate(
        {
            **bundle_hl.tax_policy(cat.tax_policy_id).model_dump(mode="json"),
            "brackets": [{"lower": 0, "rate": 0.1}],
        }
    )
    r = year(
        bundle_hl, scoring_hl, state_of(bundle_hl, clone), action, tax=binding_from_policy(taxed)
    )
    out = r.settlement.outcome
    gross = {m: 0 for m in range(1, 13)}
    tax = {m: 0 for m in range(1, 13)}
    for f in out.cash_flows:
        if f.kind == "income_gross":
            gross[f.month] += cents(f.amount)
        elif f.kind == "income_tax":
            tax[f.month] += -cents(f.amount)
    assert gross[4] == 210000 and gross[5] == 0 and gross[6] == 210000  # 1,500 + 600
    assert (
        tax[5] == 0 and sum(tax.values()) == sum(gross.values()) // 10
    )  # 年内累计：只对实际挣到的钱计税
    m4, m5 = out.resources.months[3], out.resources.months[4]
    assert (m4.onsite_visits, m4.job_workdays_sum) == (15, 15)
    assert (m5.onsite_visits, m5.job_workdays_sum) == (0, 0)
    assert not [c for c in m5.contributions if c.category in ("work", "commute")]
    for oid in (SHOP["opportunity_id"], CAFE["opportunity_id"]):
        item = next(i for i in out.items if i.opportunity_id == oid)
        assert [(p.month, p.kind) for p in item.months_paused] == [(5, "sick_leave")]
    positions = {p.opportunity_id: p for p in out.career.positions}
    assert positions[SHOP["opportunity_id"]].sick_leave_months == (5,)
    # 咖啡馆是今年新上的岗位，但第 1–4 月已经实际上过班：病假保留雇佣关系
    assert positions[CAFE["opportunity_id"]].sick_leave_months == (5,)


# ==========================================================================
# 第 7 项：年末病假保留雇佣关系；真正辞职不同；没上过班的新岗位不授予雇主身份
# ==========================================================================


def test_year_end_sick_leave_keeps_the_job_but_resignation_does_not(bundle_hl, scoring_hl):
    clone = "u94.a91.major"
    proto = with_source(bundle_hl.protocol, injected(("u94", 1, 12, "major")))
    start = state_of(bundle_hl, clone)
    sick = year(
        bundle_hl, scoring_hl, start, plan(bundle_hl, clone, 1, HOME, FOOD, SHOP), protocol=proto
    )
    emp = sick.next_state.employment
    assert emp.current_position_ids == (SHOP["opportunity_id"],)  # 病假不是辞职
    assert emp.positions[0].sick_leave_months == (12,) and 12 not in emp.positions[0].months
    assert sick.next_state.occupation is not None
    quit_ = {**SHOP, "end_month": 11}
    resigned = year(
        bundle_hl, scoring_hl, start, plan(bundle_hl, clone, 1, HOME, FOOD, quit_), protocol=proto
    )
    assert resigned.next_state.employment.current_position_ids == ()  # 第 12 月没有岗位：辞职
    assert resigned.next_state.employment.positions[0].sick_leave_months == ()
    # 新岗位第 12 月才开始、第 12 月就大病：没上过一天班，不算入职，不授予雇主身份
    new_job = {**CAFE, "start_month": 12}
    fresh = year(
        bundle_hl,
        scoring_hl,
        start,
        plan(bundle_hl, clone, 1, HOME, FOOD, quit_, new_job),
        protocol=proto,
    )
    out = fresh.settlement.outcome
    cafe = next(i for i in out.items if i.opportunity_id == CAFE["opportunity_id"])
    assert [(p.month, p.kind) for p in cafe.months_paused] == [(12, "not_started")]
    assert cafe.status == "paused"
    assert CAFE["opportunity_id"] not in {p.opportunity_id for p in out.career.positions}
    assert fresh.next_state.employment.current_position_ids == ()


# ==========================================================================
# 第 8 项：学习暂停——不增加进度、不重收学费，能力保留，恢复后进度正确
# ==========================================================================


def test_learning_pauses_without_progress_or_repeat_fees(bundle_hl, scoring_hl):
    y1, y2 = run_clone(bundle_hl, scoring_hl, "u96.a91.study")
    (learn,) = y1.settlement.outcome.career.learning
    assert learn.months_attended == tuple(m for m in range(1, 13) if m != 4)
    assert learn.months_paused == (4,)
    assert (learn.progress_after, cents(learn.fee_charged)) == (11, 120000)
    assert flows_for(y1, COURSE_ID) == [(1, -120000)]
    m4 = y1.settlement.outcome.resources.months[3]
    assert not [c for c in m4.contributions if c.category == "training"]
    growth = {
        m.month: next(c for c in m.components if c.component_id == "opportunity").growth.g
        for m in y1.score.months
    }
    assert growth[4] == 0 and growth[3] > 0 and growth[5] > 0  # 暂停的月份没有成长
    (learn2,) = y2.settlement.outcome.career.learning
    assert (cents(learn2.fee_charged), learn2.months_attended, learn2.completed_month) == (
        0,
        (1,),
        1,
    )
    assert learn2.granted == ("accounting_cert",) and learn2.usable_from_year == 3
    assert cash_ends(y2)[-1] == 318000


# ==========================================================================
# 第 9 项：医疗与病假影响目标；有钱 / 付款 / 兑现 / 恢复 / 分数分开记
# ==========================================================================


def test_sick_leave_and_medical_spending_flow_into_goal_progress_separately(bundle_hl, scoring_hl):
    _y1, y2, y3 = run_clone(bundle_hl, scoring_hl, "u97.a91.crossyear")
    sv2 = y2.settlement.outcome.savings
    june = next(g for g in sv2.progress if g.month == 6)
    assert (cents(june.cash), cents(june.quote), cents(june.shortfall), june.status) == (
        166000,
        240000,
        74000,
        "overdue",
    )
    assert [e.kind for e in sv2.events] == ["due_unrealized"]
    assert sv2.realization is None
    # 付款：第 3 年第 3 月实际付清，兑现；恢复：第 2 年第 2 月月末自然恢复；分数只看月初病情
    sv3 = y3.settlement.outcome.savings
    assert sv3.realization.month == 3 and flows_for(y3, DRYER_ID) == [(3, -240000)]
    assert health_months(y2)[1] == (2, "major", "lived", "recovered_natural", "declined")
    u = [m.u for m in y2.score.months]
    assert u[0] == u[1] and u[2] == u[3] and u[1] < u[2]
    # 治疗付款也只是现金流：同一个人、同一个大病，治了就少一笔钱、少病两个月，目标照样按余额更新
    proto = with_source(bundle_hl.protocol, injected(("u97", 1, 5, "major")))
    start = state_of(bundle_hl, "u97.a91.crossyear")
    base = bundle_hl.plans_for("u97.a91.crossyear")[0]
    treated = year(
        bundle_hl,
        scoring_hl,
        start,
        revise(base, emergency={"minor": "decline", "major": "treat_if_possible"}),
        protocol=proto,
    )
    may = next(g for g in treated.settlement.outcome.savings.progress if g.month == 5)
    assert (
        cents(may.cash) == 448000 - 138000 - 200000
    )  # 第 4 月末 4,480 → 没有工资、付住房食品与住院
    assert flows_for(treated, HOSPITAL) == [(5, -200000)]


# ==========================================================================
# 第 10 项：第 5 月的意外不撤销第 1–4 月；出局与停止月份的保留范围清楚
# ==========================================================================


def test_a_month_five_stop_keeps_months_one_to_four(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u98.a91.stop")
    s = r.settlement
    assert s.status == "stopped" and r.handoff == "stopped"
    out = s.outcome
    assert [m.month for m in out.months] == [1, 2, 3, 4]
    assert cash_ends(r) == [256000, 412000, 568000, 724000]
    assert max(f.month for f in out.cash_flows) == 4  # 第 5 月没有工资、没有支出
    assert out.stop.kind == "emergency_insufficient" and out.stop.month == 5
    (short,) = out.stop.shortfalls
    assert (short.resource, short.used.exact, short.limit.exact) == ("energy", "505/2", "250")
    assert "整份计划" not in out.stop.reason and "此前的月份全部保留" in out.stop.reason
    checks = [m.resource_check for m in out.resources.months]
    assert checks == ["complete"] * 4 + ["blocked"] + ["not_reached"] * 7
    assert out.energy_feasibility == "monthly_quota_checked_until_stop"
    assert health_months(r)[-1] == (5, "minor", "blocked", "not_applicable", None)
    office = next(i for i in out.items if i.opportunity_id == OFFICE["opportunity_id"])
    assert office.months_not_reached == tuple(range(5, 13)) and office.covered_months == (
        1,
        2,
        3,
        4,
    )
    assert [m.status for m in r.score.months] == ["scored"] * 4 + ["stopped_month"] + [
        "after_stop"
    ] * 7
    assert r.score.annual_status == "stopped" and r.score.annual_mean is None
    assert r.feedback.decision == "stopped"
    nxt = r.next_state
    assert (nxt.status, nxt.year_index, cents(nxt.cash), nxt.stop.month) == (
        "stopped",
        1,
        724000,
        5,
    )
    assert nxt.health.episode.months_ill == 1  # 停止月的事件保留
    again = _settle(bundle_hl, nxt, bundle_hl.plans_for("u98.a91.stop")[0])
    assert again.status == "input_error" and again.problems[0].code == "state_not_active"


def test_the_same_plan_without_the_event_is_feasible_so_the_stop_is_not_a_plan_error(bundle_hl):
    clone = "u98.a91.stop"
    proto = with_source(bundle_hl.protocol, source("none"))
    r = _settle(
        bundle_hl, state_of(bundle_hl, clone), bundle_hl.plans_for(clone)[0], protocol=proto
    )
    assert r.status == "completed"


def test_a_plan_infeasible_at_year_start_commits_nothing_even_if_an_illness_comes_later(
    bundle_hl,
):
    clone = "u98.a91.stop"
    course = {"opportunity_id": COURSE_ID, "quantity": 1, "start_month": 1, "end_month": 12}
    too_much = plan(
        bundle_hl, clone, 1, HOME, FOOD, OFFICE, GYM, course
    )  # 232.5 + 48 > 250：第 1 月就超
    r = _settle(bundle_hl, state_of(bundle_hl, clone), too_much)
    assert r.status == "plan_infeasible" and r.outcome is None and r.next_state is None
    assert r.problems[0].month == 1


def test_a_known_illness_at_year_start_that_the_plan_cannot_handle_is_a_plan_error(
    bundle_hl, scoring_hl
):
    """年初就在延续的疾病是已知事实：计划应付不了它，是年初的计划问题（不是事后的停止）。"""
    clone = "u98.a91.stop"
    off = revise(bundle_hl.protocol.energy_feedback, mode="disabled")  # 额度固定 250，便于手算
    proto = revise(
        with_source(bundle_hl.protocol, injected(("u98", 1, 12, "minor"))), energy_feedback=off
    )
    start = state_of(bundle_hl, clone)
    y1_plan = plan(bundle_hl, clone, 1, HOME, FOOD, OFFICE, emergency=dict(DECLINE))
    y1 = year(bundle_hl, scoring_hl, start, y1_plan, protocol=proto)
    assert y1.handoff == "next_year_ready" and y1.next_state.health.episode.severity == "minor"
    assert y1.next_state.energy.level == 250
    # 第 2 年第 1 月仍是小病（这次病的第 2 个月）：232.5 + 恢复 20 = 252.5 > 250，治疗没有作用、也没有可暂停的活动
    y2 = _settle(
        bundle_hl, y1.next_state, plan(bundle_hl, clone, 2, HOME, FOOD, OFFICE, GYM), protocol=proto
    )
    assert y2.status == "plan_infeasible" and y2.outcome is None
    assert y2.problems[0].month == 1 and y2.problems[0].resource is not None


# ==========================================================================
# 第 11 项：就医时间与负担不能绕过资源上限；恰好等于上限通过
# ==========================================================================


def _quota(state: PersonYearState, level: float) -> PersonYearState:
    return revise(
        state,
        energy={"level": level, "scale_version": "e1-4a-monthly-quota-v1"},
        energy_quota_basis={"base_level": level, "status": "base_first_year"},
    )


def test_treatment_visits_count_against_the_quota_and_exactly_at_the_limit_passes(
    bundle_hl, scoring_hl
):
    clone = "u99.a91.pause"
    action = bundle_hl.plans_for(clone)[0]
    exact = _settle(bundle_hl, _quota(state_of(bundle_hl, clone), 242.5), action)
    assert exact.status == "completed"
    m5 = exact.outcome.health.months[4]
    assert (
        m5.treatment.status == "treated"
        and exact.outcome.resources.months[4].energy_used.exact == "485/2"
    )
    assert [a.opportunity_id for a in m5.paused] == [GYM["opportunity_id"]]
    short = _settle(bundle_hl, _quota(state_of(bundle_hl, clone), 242.49), action)
    m5 = short.outcome.health.months[4]
    # 暂停健身之后加上门诊仍超 0.01：放弃治疗（不能靠不记就医时间过关），小病按病程继续
    assert m5.treatment.status == "resources_insufficient" and m5.transition == "continues"
    assert short.outcome.resources.months[4].energy_used.exact == "481/2"
    assert flows_for_settlement(short, CLINIC) == []


def flows_for_settlement(r, oid):
    return [(f.month, cents(f.amount)) for f in r.outcome.cash_flows if f.opportunity_id == oid]


def test_the_pause_order_is_used_only_as_needed(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u99.a91.pause")
    out = r.settlement.outcome
    gym = next(i for i in out.items if i.opportunity_id == GYM["opportunity_id"])
    assert [(p.month, p.kind) for p in gym.months_paused] == [(5, "agent_pause_order")]
    assert 5 not in gym.covered_months and cents(gym.amount_paid) == 11 * 6000  # 暂停的月份不付款
    assert cash_ends(r)[4] == 856000 and cash_ends(r)[-1] == 1948000


# ==========================================================================
# 第 12 项：自然恢复、跨年延续与没好的终态——不重复收费、不自动痊愈
# ==========================================================================


def test_cross_year_illness_and_an_unrecovered_terminal_state(bundle_hl, scoring_hl):
    y1, y2, y3 = run_clone(bundle_hl, scoring_hl, "u97.a91.crossyear")
    assert health_months(y1)[-1] == (12, "major", "lived", "continues", "declined")
    s2 = y2.settlement.outcome
    assert [x.episode_month for x in s2.health.months[:2]] == [2, 3]  # 跨年接着数
    assert y2.next_state.health.episode is None
    (ep,) = y2.next_state.health.history
    assert (ep.onset_year, ep.onset_month, ep.recovered_year, ep.recovered_month) == (1, 12, 2, 2)
    assert (ep.months_ill, ep.recovery, ep.treatment) == (3, "natural", None)
    for r in (y1, y2, y3):  # 一律不治：没有任何医疗付款
        assert flows_for(r, CLINIC) == flows_for(r, HOSPITAL) == []
    assert y2.next_state.health.age_years == 37 and y1.next_state.health.age_years == 36
    term = y3.next_state
    assert term.status == "terminal" and y3.handoff == "terminal"
    assert (
        term.health.episode.severity == "minor" and term.health.episode.months_ill == 1
    )  # 不自动痊愈
    assert [h.episode_id for h in term.health.history] == ["ill-y1m12"]  # 恢复后不再计入
    r = _settle(bundle_hl, term, bundle_hl.plans_for("u97.a91.crossyear")[2])
    assert r.status == "input_error"


def _hm(r):
    return [
        (x.month, x.state, x.record, x.transition, x.treatment.status if x.treatment else None)
        for x in r.outcome.health.months
    ]


def test_delayed_treatment_table_is_hand_computable(bundle_hl, scoring_hl):
    """大病（不治 3 个月）第 1 年第 1 月发病。病假没有工资，现金只会减少：

    * 年初 3,000：第 1 月 3,000 − 1,380 = 1,620 < 2,000，治不起；第 2 月 1,620 − 1,380 = 240，仍治不起；
      第 3 月 240 < 1,380 → 出局（缺 1,140）；
    * 年初 4,000：第 1 月 4,000 − 1,380 = 2,620 ≥ 2,000，住院，月末恢复；只收一次；
    * 跨年延误：第 1 年第 12 月发病、计划不治；第 2 年的计划改成治——第 2 年第 1 月（这次病的第 2 个月）住院、月末恢复，
      3 个月的病只病了 2 个月：3,940 − 1,380 − 2,000 = 560。
    """
    clone = "u94.a91.major"
    proto = with_source(bundle_hl.protocol, injected(("u94", 1, 1, "major")))
    action = plan(bundle_hl, clone, 1, HOME, FOOD, SHOP)
    poor = revise(state_of(bundle_hl, clone), cash={"amount": 3000, "numeraire": "USD_2024_LOCAL"})
    r = _settle(bundle_hl, poor, action, protocol=proto)
    assert r.status == "exited" and cents(r.outcome.exit.shortfall) == 114000
    assert _hm(r) == [
        (1, "major", "lived", "continues", "unaffordable"),
        (2, "major", "lived", "continues", "unaffordable"),
        (3, "major", "exit_month", "not_applicable", None),
    ]
    rich = revise(poor, cash={"amount": 4000, "numeraire": "USD_2024_LOCAL"})
    r = _settle(bundle_hl, rich, action, protocol=proto)
    assert _hm(r)[:2] == [
        (1, "major", "lived", "recovered_treated", "treated"),
        (2, "healthy", "lived", "stays_healthy", None),
    ]
    assert flows_for_settlement(r, HOSPITAL) == [(1, -200000)]
    y1 = run_clone(bundle_hl, scoring_hl, "u97.a91.crossyear")[0]
    y2_plan = revise(
        bundle_hl.plans_for("u97.a91.crossyear")[1],
        emergency={"minor": "decline", "major": "treat_if_possible"},
    )
    y2 = year(bundle_hl, scoring_hl, y1.next_state, y2_plan)
    assert health_months(y2)[:2] == [
        (1, "major", "lived", "recovered_treated", "treated"),
        (2, "healthy", "lived", "stays_healthy", None),
    ]
    assert cash_ends(y2)[0] == 56000 and flows_for(y2, HOSPITAL) == [(1, -200000)]
    (ep,) = y2.next_state.health.history
    assert (ep.months_ill, ep.recovery, ep.recovered_year, ep.recovered_month) == (
        2,
        "treated",
        2,
        1,
    )


# ==========================================================================
# 第 13 项：本月疾病不影响自己的概率；配对、顺序无关、重试不重抽
# ==========================================================================


def _hot(protocol):
    """把月概率调高（测试用），让随机事件在一年里确实发生。"""
    rules = protocol.health_rules
    cells = [{**c.model_dump(mode="json"), "minor": 0.25, "major": 0.1} for c in rules.risk.cells]
    risk = revise(rules.risk, cells=cells)
    return revise(protocol, health_rules=revise(rules, risk=risk), illness_events=source("random"))


def test_random_draws_read_last_months_burden_and_never_the_current_illness(bundle_hl, scoring_hl):
    proto = _hot(bundle_hl.protocol)
    clone = "u99.a91.pause"
    r = _settle(
        bundle_hl, state_of(bundle_hl, clone), bundle_hl.plans_for(clone)[0], protocol=proto
    )
    out = r.outcome
    months = out.health.months
    drawn = [x for x in months if x.draw is not None]
    assert drawn, "高概率下应当有抽签"
    for x in months:
        if x.state != "healthy" and x.onset is None:
            assert x.draw is None  # 生病的月份延续原病，不抽新事件
    by_month = {m.month: m for m in out.resources.months}
    for x in drawn:
        if x.month == 1:
            assert x.draw.last_work_relative_burden is None and x.draw.burden_band == "moderate"
        else:
            prev = by_month[x.month - 1]
            assert x.draw.last_work_relative_burden == prev.work_relative_burden
    # 至少有一次新发疾病，且它的概率来自上一个月（病前）的工作负担
    onset = [x for x in months if x.onset is not None]
    assert onset and all(x.draw is not None and x.draw.result == x.onset for x in onset)


def test_a_continuing_illness_is_never_redrawn_or_stacked(bundle_hl):
    """随机臂、不治疗（病会延续）：延续的病月不抽签、不叠加新病，同一次病的病月序号逐月加一，病满才结束。

    2026-10-11 变异检查补出来的测试：原有的随机臂测试都用"条件允许就治"的计划，病在发病当月月末就好，从来没有
    延续的病月，所以"生病的月份也抽新事件"这条变异没被抓到。
    """
    proto = _hot(bundle_hl.protocol)
    clone = "u99.a91.pause"
    action = revise(
        bundle_hl.plans_for(clone)[0],
        emergency={"minor": "decline", "major": "decline", "pause_order": [GYM["opportunity_id"]]},
    )
    r = _settle(bundle_hl, state_of(bundle_hl, clone), action, protocol=proto)
    months = r.outcome.health.months
    continuing = [x for x in months if x.state != "healthy" and x.onset is None]
    assert continuing, "不治疗时应当有延续的病月"
    by = {x.month: x for x in months}
    for x in continuing:
        assert x.draw is None  # 延续的病月不抽新事件
        prev = by[x.month - 1]
        assert (prev.episode_id, prev.state) == (
            x.episode_id,
            x.state,
        )  # 同一次病，不叠加、不换病情
        assert x.episode_month == prev.episode_month + 1
    rules = proto.health_rules
    for ep in r.next_state.health.history:  # 不治疗：每次病都病满声明的月数才结束
        assert ep.recovery == "natural"
        assert ep.months_ill == rules.severity_rules(ep.severity).untreated_months


def test_same_index_clones_share_luck_across_cities_and_order_does_not_matter(
    bundle_hl, scoring_hl
):
    proto = _hot(bundle_hl.protocol)
    a = run_clone(bundle_hl, scoring_hl, "u91.a91.treat", protocol=proto)
    b = run_clone(bundle_hl, scoring_hl, "u91.b92.treat", protocol=proto)
    da = {x.month: x.draw.uniform.exact for x in a[0].settlement.outcome.health.months if x.draw}
    db = {x.month: x.draw.uniform.exact for x in b[0].settlement.outcome.health.months if x.draw}
    common = sorted(set(da) & set(db))
    assert common and all(da[m] == db[m] for m in common)  # 同序号跨城、同一个月用同一个随机数
    # 遍历顺序与增减别的分身不改变结果
    again_b = run_clone(bundle_hl, scoring_hl, "u91.b92.treat", protocol=proto)
    again_a = run_clone(bundle_hl, scoring_hl, "u91.a91.treat", protocol=proto)
    assert [r.model_dump_json() for r in again_a] == [r.model_dump_json() for r in a]
    assert [r.model_dump_json() for r in again_b] == [r.model_dump_json() for r in b]
    # 不同重复序号用不同的随机数
    src = proto.illness_events
    u0, _ = uniform_of(illness_key(src, "u91", 0, 1, 1))
    u1, _ = uniform_of(illness_key(src, "u91", 1, 1, 1))
    assert u0 != u1 and 0 <= u0 < 1


def test_a_retry_after_a_rejected_plan_does_not_redraw(bundle_hl):
    proto = _hot(bundle_hl.protocol)
    clone = "u98.a91.stop"
    start = state_of(bundle_hl, clone)
    course = {"opportunity_id": COURSE_ID, "quantity": 1, "start_month": 1, "end_month": 12}
    bad = _settle(
        bundle_hl, start, plan(bundle_hl, clone, 1, HOME, FOOD, OFFICE, GYM, course), protocol=proto
    )
    assert bad.status == "plan_infeasible"
    good_plan = plan(
        bundle_hl, clone, 1, HOME, FOOD, OFFICE, emergency={**TREAT, "pause_order": []}
    )
    first = _settle(bundle_hl, start, good_plan, protocol=proto)
    second = _settle(bundle_hl, start, good_plan, protocol=proto)
    assert first.model_dump_json() == second.model_dump_json()
    src = proto.illness_events
    for x in first.outcome.health.months:
        if x.draw is not None:
            u, digest = uniform_of(illness_key(src, "u98", 0, 1, x.month))
            assert (x.draw.uniform.exact, x.draw.key_fingerprint) == (
                f"{u.numerator}/{u.denominator}",
                digest,
            )


def test_the_random_key_has_no_city_clone_order_or_rule_fingerprint(bundle_hl):
    key = illness_key(_hot(bundle_hl.protocol).illness_events, "u91", 0, 2, 3)
    assert set(key) == {
        "stream",
        "pairing_id",
        "seed",
        "person_id",
        "repeat_index",
        "absolute_month",
        "category",
    }
    assert key["absolute_month"] == 15 and key["category"] == "illness_onset"
    import ast
    import inspect

    import econ.life_health as health

    calls = [
        n
        for n in ast.walk(ast.parse(inspect.getsource(health)))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("hash", "id")
    ]
    assert calls == []  # 不用进程随机化的 hash()（也不用对象地址）当种子
    assert "import random" not in inspect.getsource(health)


# ==========================================================================
# 第 14 项：重放一致；试算之间不能互换健康结果、分数或候选状态
# ==========================================================================


def test_replay_is_identical_and_health_results_cannot_be_swapped(bundle_hl, scoring_hl):
    clone = "u94.a91.major"
    start = state_of(bundle_hl, clone)
    cat = catalog_of(bundle_hl, start)
    tax = tax_of(bundle_hl, cat)
    treat = bundle_hl.plans_for(clone)[0]
    concert = {"opportunity_id": "a91.experience.concert", "quantity": 1, "month": 8}
    decline = revise(treat, items=[*treat.model_dump(mode="json")["items"], concert])
    a1 = settle_and_score(
        protocol=bundle_hl.protocol,
        state=start,
        action=treat,
        catalog=cat,
        tax=tax,
        scoring=inputs(bundle_hl, scoring_hl, start),
    )
    a2 = settle_and_score(
        protocol=bundle_hl.protocol,
        state=start,
        action=treat,
        catalog=cat,
        tax=tax,
        scoring=inputs(bundle_hl, scoring_hl, start),
    )
    b = settle_and_score(
        protocol=bundle_hl.protocol,
        state=start,
        action=decline,
        catalog=cat,
        tax=tax,
        scoring=inputs(bundle_hl, scoring_hl, start),
    )
    assert a1.binding_fingerprint == a2.binding_fingerprint
    assert a1.candidate_fingerprint != b.candidate_fingerprint  # 两次试算的候选状态不同（现金不同）
    # 事后把健康记录换成别的（例如把"治疗后恢复"改成"继续生病"）：年度绑定的指纹核对会发现
    months = list(a1.settlement.outcome.health.months)
    fake = months[4].model_copy(update={"transition": "continues"})
    health = a1.settlement.outcome.health.model_copy(
        update={"months": (*months[:4], fake, *months[5:])}
    )
    outcome = a1.settlement.outcome.model_copy(update={"health": health})
    tampered = a1.model_copy(
        update={"settlement": a1.settlement.model_copy(update={"outcome": outcome})}
    )
    assert any("实际结果" in x for x in tampered.object_problems())
    from econ.life_feedback import feedback_record

    rec_a = feedback_record(protocol=bundle_hl.protocol, year=a1)
    with pytest.raises(LifeContractError, match="另一次年度结算"):
        apply_feedback(b.settlement.next_state, rec_a)
    swapped = a1.model_copy(update={"settlement": b.settlement})
    assert swapped.object_problems()


# ==========================================================================
# 第 16 项：一个分身出局或停止，其他分身照常继续
# ==========================================================================


def test_one_clone_stopping_or_exiting_does_not_stop_the_others(bundle_hl, scoring_hl):
    clones = [s.clone_id for s in bundle_hl.initial_states]
    results = {c: run_clone(bundle_hl, scoring_hl, c) for c in clones}
    final = {c: r[-1].handoff for c, r in results.items()}
    assert final == {
        "u91.a91.treat": "next_year_ready",
        "u91.b92.treat": "stopped",
        "u92.a91.decline": "next_year_ready",
        "u93.a91.broke": "next_year_ready",
        "u94.a91.major": "next_year_ready",
        "u95.a91.decline": "exited",
        "u96.a91.study": "next_year_ready",
        "u97.a91.crossyear": "terminal",
        "u98.a91.stop": "stopped",
        "u99.a91.pause": "next_year_ready",
    }
    stop = results["u91.b92.treat"][0].settlement.outcome.stop
    assert stop.kind == "data_gap" and stop.month == 3
    assert "不是现实看不起病" in stop.reason
    # 反向顺序逐个运行，结果逐字节相同（停止与出局只影响自己）
    for c in reversed(clones):
        assert [r.model_dump_json() for r in run_clone(bundle_hl, scoring_hl, c)] == [
            r.model_dump_json() for r in results[c]
        ]


# ==========================================================================
# 大病时暂停的范围：有参与时间的才暂停；零小时服务与被动物品照常
# ==========================================================================


def test_major_illness_pauses_participation_but_not_passive_help(bundle_hl, scoring_hl):
    clone = "u94.a91.major"
    concert = {"opportunity_id": "a91.experience.concert", "quantity": 1, "month": 5}
    action = plan(bundle_hl, clone, 1, HOME, FOOD, SHOP, GYM, CLEANING, concert)
    r = _settle(bundle_hl, state_of(bundle_hl, clone), action)
    out = r.outcome
    by = {i.opportunity_id: i for i in out.items}
    assert [p.kind for p in by[GYM["opportunity_id"]].months_paused] == ["major_illness_rule"]
    assert 5 in by[CLEANING["opportunity_id"]].covered_months  # 零小时的清洁服务照常
    assert (
        by["a91.experience.concert"].status == "paused"
        and cents(by["a91.experience.concert"].amount_paid) == 0
    )
    m5 = out.resources.months[4]
    cleaning = [c for c in m5.contributions if c.task == "cleaning"]
    assert cleaning[0].hours.exact == "2"  # 清洁服务在用，本人打扫只剩 2 小时
    hidden = {GYM["opportunity_id"], "a91.experience.concert"}
    assert not [c for c in m5.contributions if c.opportunity_id in hidden or c.category == "work"]


def test_a_paused_prepaid_holding_elapses_unused(bundle_hl, scoring_hl):
    """已经付清的年付会员：大病月照样流逝服务月，但不使用、不带来收益（付款 ≠ 参与）。"""
    raw_card = {
        "opportunity_id": "a91.service.gym_year",
        "category": "service",
        "label": "健身房年卡",
        "spec": {"visits_per_month": 8},
        "unit": "membership-year",
        "quality": "年付，每月去 8 次",
        "availability": "available",
        "price": {
            "amount": {"amount": 600, "numeraire": "USD_2024_LOCAL"},
            "billing": "per_year",
            "per_unit": "membership-year",
        },
        "duration": {"kind": "ongoing_service"},
        "resource": {
            "basis": "per_use",
            "uses_per_month": 8,
            "hours": 1.5,
            "burden_per_hour": 1.0,
            "quantity_scales": False,
            "note": "测试",
        },
        "source": bundle_hl.catalogs[0].opportunities[0].source.model_dump(mode="json"),
    }
    from common.life_contracts import LifeOpportunity

    cat = bundle_hl.catalogs[0]
    cat2 = revise(cat, opportunities=(*cat.opportunities, LifeOpportunity.model_validate(raw_card)))
    clone = "u94.a91.major"
    start = revise(state_of(bundle_hl, clone), cash={"amount": 9000, "numeraire": "USD_2024_LOCAL"})
    action = plan(
        bundle_hl,
        clone,
        1,
        HOME,
        FOOD,
        SHOP,
        {"opportunity_id": "a91.service.gym_year", "quantity": 1, "start_month": 1},
    )
    r = settle_year(
        protocol=bundle_hl.protocol,
        state=start,
        action=action,
        catalog=cat2,
        tax=tax_of(bundle_hl, cat),
    )
    (card,) = [h for h in r.outcome.holdings_service if h.opportunity_id == "a91.service.gym_year"]
    assert card.paused_months == (5,) and 5 not in card.covered_months
    assert card.remaining_months_after == 0 and card.expired  # 12 个服务月照样用完
    assert flows_for_settlement(r, "a91.service.gym_year") == [(1, -60000)]


# ==========================================================================
# 资料缺口、旧协议与加载检查
# ==========================================================================


def test_unknown_treatment_data_is_a_gap_not_free_and_not_unaffordable(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u91.b92.treat")
    out = r.settlement.outcome
    assert out.stop.kind == "data_gap" and out.stop.problem_codes == ("treatment_data_unknown",)
    m3 = out.health.months[2]
    assert m3.record == "blocked" and m3.treatment.status == "data_gap"
    assert [m.month for m in out.months] == [1, 2]
    # 计划明确不治时，资料未知用不到——照常过完
    proto = bundle_hl.protocol
    start = state_of(bundle_hl, "u91.b92.treat")
    action = revise(bundle_hl.plans_for("u91.b92.treat")[0], emergency=dict(DECLINE))
    ok = _settle(bundle_hl, start, action, protocol=proto)
    assert ok.status == "completed"
    # 大病时本城确定没有住院：不是资料缺口，按病程走
    proto2 = with_source(proto, injected(("u91", 1, 3, "major")))
    action2 = revise(action, emergency=dict(TREAT))
    r2 = _settle(
        bundle_hl,
        revise(start, cash={"amount": 9000, "numeraire": "USD_2024_LOCAL"}),
        action2,
        protocol=proto2,
    )
    assert (
        r2.status == "completed" and r2.outcome.health.months[2].treatment.status == "unavailable"
    )


def test_old_protocols_do_not_read_health_fields(raw_sv):
    raw_sv["catalogs"][0]["treatments"] = [
        {
            "treatment_id": "a81.care.clinic",
            "severity": "minor",
            "need_id": "minor_illness_care",
            "label": "门诊",
            "serves": "小病",
            "availability": "unknown",
            "unknown_reason": "测试",
            "source": raw_sv["catalogs"][0]["opportunities"][0]["source"],
        }
    ]
    errors = cross_validate(bundle_from(raw_sv)).errors
    assert any("不读治疗目录" in e for e in errors)


def test_new_protocol_plans_must_declare_an_emergency_arrangement(bundle_hl):
    clone = "u91.a91.treat"
    action = revise(bundle_hl.plans_for(clone)[0], emergency=None)
    r = _settle(bundle_hl, state_of(bundle_hl, clone), action)
    assert r.status == "input_error" and r.problems[0].code == "emergency_missing"
    bad = plan(
        bundle_hl,
        clone,
        1,
        HOME,
        FOOD,
        SHOP,
        emergency={**TREAT, "pause_order": ["a91.housing.far"]},
    )
    r = _settle(bundle_hl, state_of(bundle_hl, clone), bad)
    assert r.status == "input_error" and r.problems[0].code == "emergency_pause_invalid"


def test_changed_rules_or_event_source_do_not_continue_silently(bundle_hl, scoring_hl):
    (y1,) = run_clone(bundle_hl, scoring_hl, "u91.a91.treat")
    nxt = y1.next_state
    action = plan(bundle_hl, "u91.a91.treat", 2, HOME, FOOD, SHOP)
    hot = _hot(bundle_hl.protocol)
    r = _settle(bundle_hl, nxt, action, protocol=hot)
    codes = {p.code for p in r.problems}
    assert r.status == "input_error" and {"health_rules_changed", "illness_source_changed"} <= codes
    r = _settle(bundle_hl, nxt, action, protocol=with_source(bundle_hl.protocol, source("none")))
    assert {p.code for p in r.problems} == {"illness_source_changed"}


def test_the_initial_state_must_be_healthy_and_unbound(bundle_hl):
    s = state_of(bundle_hl, "u91.a91.treat")
    with pytest.raises(ValueError, match="binding"):
        HealthState.model_validate(
            {
                **s.health.model_dump(mode="json"),
                "last_work_relative_burden": {"exact": "8/25", "value": 0.32},
                "last_burden_year": 1,
                "last_burden_month": 1,
            }
        )
    raw_state = s.model_dump(mode="json")
    raw_state["health"] = None
    r = _settle(
        bundle_hl,
        PersonYearState.model_validate(raw_state),
        bundle_hl.plans_for("u91.a91.treat")[0],
    )
    assert r.status == "input_error" and r.problems[0].code == "health_state"


def test_the_fixture_loads_and_settles(bundle_hl):
    _, report = load_life_bundle("configs/life/synthetic_e1_5c_health.yaml")
    assert report.contract_valid and report.settlement_ready
