"""E1-4B 满足感 → 下一年月度精力额度（`DECISIONS/0021`）：纯函数手算与年度交接。

每条测试对应 E1-4B prompt 第八节的一项验收。额度用精确分数比较（负担点，两位小数），满足感是评分器给出的
0–1 浮点数（只按它的最短十进制写法进入计算），二者不混用；不放宽任何旧容差。
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from pydantic import ValidationError

from common.life_contracts import EnergyFeedbackPolicy, EnergyQuotaBasis, LifeContractError
from econ import life_feedback as lf
from econ.life_feedback import apply_feedback, feedback_record, quota_from_score
from econ.life_settlement import settle_year
from econ.life_year import run_life_year, settle_and_score
from ledger.life_catalog import parse_bundle

from .feedback_support import (
    OFF,
    bound_year,
    profile_of,
    run_years,
    scoring_inputs,
    truth_of,
    variant,
)
from .settle_support import catalog_of, plan_of, revise, state_of, tax_of

F = Fraction  # 手算用的分数简写

S_A61 = 0.5604166666666667  # u31 在 A61 第 1 年的完整年度平均满足感（评分器输出，见样例文末手算）


# --------------------------------------------------------------------------
# 1–2：公式、固定基础、上下界、单调、量化（纯函数，可以直接输入 S）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("score", "quota"), [(0.0, 250), (0.6, 265), (0.8, 270), (1.0, 275)])
def test_hand_calculated_quotas(score, quota):
    """基础 250、α = 0.10：S = 0 → 250，0.6 → 265，0.8 → 270，1 → 275。"""
    q = quota_from_score(250, 0.10, score)
    assert q.quantized == quota and q.upper == 275


def test_quotas_are_monotone_bounded_and_floored_to_the_cent():
    prev = F(0)
    for i in range(0, 101):
        q = quota_from_score(250, 0.10, i / 100)
        assert 250 <= q.quantized <= 275 and q.quantized >= prev
        assert q.quantized <= q.raw < q.quantized + F(1, 100)  # 向下保留到 0.01
        assert (q.quantized * 100).denominator == 1
        prev = q.quantized


def test_the_real_score_is_read_through_its_decimal_form():
    """评分器的浮点 S 按最短十进制写法变成精确分数：250 × (1 + 0.1 × 0.5604166666666667) = 264.0104… → 264.01。"""
    q = quota_from_score(250, 0.10, S_A61)
    assert q.score_text == "0.5604166666666667"
    assert q.raw == 250 * (1 + F(1, 10) * F("0.5604166666666667"))
    assert q.quantized == F(26401, 100)


def test_the_cent_boundary_is_exact_without_epsilon():
    """S = 0.0004 恰好给 250.01；再小一点就是 250.00——不靠 epsilon 跨过边界。"""
    assert quota_from_score(250, 0.10, 0.0004).quantized == F(25001, 100)
    assert quota_from_score(250, 0.10, 0.00039999).quantized == 250


@pytest.mark.parametrize("bad", [-0.01, 1.01, float("nan"), float("inf"), 1])
def test_scores_outside_zero_one_or_not_finite_are_refused(bad):
    with pytest.raises(LifeContractError):
        quota_from_score(250, 0.10, bad)


def test_alpha_outside_the_declared_range_is_refused(bundle4b):
    with pytest.raises(LifeContractError):
        quota_from_score(250, 0.11, 0.5)
    policy = bundle4b.protocol.energy_feedback
    for bad in (0.11, -0.01, 0.055):
        with pytest.raises(ValidationError):
            revise(policy, alpha=bad)


# --------------------------------------------------------------------------
# 1、3、11：第一年基础额度、滞后一年、下一年 12 个月同一额度、确实被下一年结算使用；时间预算不变
# --------------------------------------------------------------------------


def test_three_year_handoff_lag_and_no_compounding(bundle4b, scoring4b):
    r1, r2, r3 = run_years(bundle4b, scoring4b, "u31.a61.c0")
    # 第 1 年：基础额度，没有第零年满意度
    assert {m.energy_quota.fraction() for m in r1.settlement.outcome.resources.months} == {250}
    assert r1.feedback.decision == "applied" and r1.feedback.score_decimal == repr(S_A61)
    assert r1.feedback.new_quota.fraction() == F(26401, 100)
    # 第 2 年：12 个月都用 264.01，来自第 1 年
    s2 = r1.next_state
    assert (s2.year_index, s2.energy.level, s2.energy_quota_basis.status) == (
        2,
        264.01,
        "feedback_applied",
    )
    assert s2.energy_quota_basis.source_year == 1 and s2.energy_quota_basis.base_level == 250
    assert {m.energy_quota.fraction() for m in r2.settlement.outcome.resources.months} == {
        F(26401, 100)
    }
    # 第 2 年同样的生活（夜校不计满足感）→ 同样的 S → 第 3 年仍是 264.01，不从 264.01 再乘
    assert r2.score.annual_mean == S_A61 and r2.next_state.energy.level == 264.01
    assert r2.feedback.previous_quota.fraction() == F(26401, 100)
    assert r2.feedback.base_quota.fraction() == 250
    # 第 3 年是最后一个行动年：照常评分，不为不存在的第 4 年计算加成
    assert (r3.handoff, r3.feedback.decision, r3.feedback.target_year) == (
        "terminal",
        "no_next_action_year",
        None,
    )
    assert r3.score.annual_status == "complete" and r3.next_state.status == "terminal"
    # 时间预算三年不变
    assert {
        r.settlement.outcome.resources.monthly_time_budget.fraction() for r in (r1, r2, r3)
    } == {365}
    assert r2.next_state.time_budget_hours == 4380


def test_the_new_quota_is_what_makes_year_two_feasible(bundle4b, scoring4b):
    """第 2 年负担 255：开启反馈（264.01）可行；同一年初状态若额度是 250 就不可行——下一年结算真的读新额度。"""
    r1, r2, _ = run_years(bundle4b, scoring4b, "u31.a61.c0")
    months = r2.settlement.outcome.resources.months
    assert {m.energy_used.fraction() for m in months} == {255}
    assert {m.energy_remaining.fraction() for m in months} == {F(901, 100)}
    s2 = r1.next_state
    stale = revise(
        s2,
        energy=revise(s2.energy, level=250),
        energy_quota_basis=revise(s2.energy_quota_basis, status="feedback_off"),
    )
    cat = catalog_of(bundle4b, s2)
    again = settle_year(
        protocol=bundle4b.protocol,
        state=stale,
        action=plan_of(bundle4b, "u31.a61.c0", 2),
        catalog=cat,
        tax=tax_of(bundle4b, cat),
    )
    assert again.status == "plan_infeasible" and again.problems[0].code == "energy_over_limit"


def test_unallocated_time_is_not_recovery_and_not_score(bundle4b, scoring4b):
    """第 1 年未安排 130 小时、第 2 年只剩 90 小时（多了夜校），满足感与额度完全相同：空闲时间不回血、不加分。"""
    r1, r2, _ = run_years(bundle4b, scoring4b, "u31.a61.c0")
    t1 = {m.time_remaining.fraction() for m in r1.settlement.outcome.resources.months}
    t2 = {m.time_remaining.fraction() for m in r2.settlement.outcome.resources.months}
    assert (t1, t2) == ({130}, {90})
    assert r1.score.annual_mean == r2.score.annual_mean
    assert r1.next_state.energy.level == r2.next_state.energy.level


def test_lower_satisfaction_lowers_the_bonus_but_not_below_base(bundle4b, scoring4b):
    r1, r2, _ = run_years(bundle4b, scoring4b, "u31.a61.down")
    assert r1.next_state.energy.level == 264.01
    assert r2.score.annual_mean < r1.score.annual_mean
    assert r2.next_state.energy.level == 262.31  # 250 × (1 + 0.1 × 0.49265…) = 262.316… → 262.31
    assert r2.next_state.energy.level >= 250


# --------------------------------------------------------------------------
# 4：分数、账目与候选状态必须来自同一次年度结算与评分（r2 年度绑定）；规格、来源与协议对不上就拒绝
# --------------------------------------------------------------------------


def _year_one(bundle4b, scoring4b, clone="u31.a61.c0"):
    (r1, *_) = run_years(bundle4b, scoring4b, clone)
    return state_of(bundle4b, clone), r1


def test_split_score_and_ledger_arguments_are_refused_explicitly(bundle4b, scoring4b):
    """r1 的调用方式（分别传 start、settlement、score）即使这次配对正确也被拒绝：分别传入无法证明是同一次生活。"""
    start, r1 = _year_one(bundle4b, scoring4b)
    with pytest.raises(LifeContractError, match="只接受年度绑定"):
        feedback_record(
            protocol=bundle4b.protocol, start=start, settlement=r1.settlement, score=r1.score
        )
    with pytest.raises(LifeContractError, match="只接受年度绑定"):
        feedback_record(protocol=bundle4b.protocol)


def test_a_score_from_another_city_or_year_cannot_be_slipped_into_a_binding(bundle4b, scoring4b):
    """拿 B62 的分数、或同一分身第 2 年的分数换掉第 1 年绑定里的分数：绑定记下的指纹对不上。"""
    year = bound_year(bundle4b, scoring4b, "u31.a61.c0")
    other_city = bound_year(bundle4b, scoring4b, "u31.b62.c0").score
    other_year = run_years(bundle4b, scoring4b, "u31.a61.c0")[1].score
    for other in (other_city, other_year):
        with pytest.raises(LifeContractError, match=r"被改动过.*分数与绑定时记下的指纹不一致"):
            feedback_record(
                protocol=bundle4b.protocol, year=year.model_copy(update={"score": other})
            )
    assert feedback_record(protocol=bundle4b.protocol, year=year).decision == "applied"


def test_scoring_inputs_from_another_spec_or_path_are_refused(bundle4b, scoring4b):
    s = state_of(bundle4b, "u31.a61.c0")
    cat = catalog_of(bundle4b, s)
    inputs = scoring_inputs(bundle4b, scoring4b, s)
    wrong_spec = type(inputs)(
        spec=revise(inputs.spec, spec_id="score.other"),
        conditions=inputs.conditions,
        preference_input=inputs.preference_input,
    )
    research = scoring_inputs(bundle4b, scoring4b, s, research=True)
    for bad, why in ((wrong_spec, "评分规格"), (research, "只接受 StatedLifeProfile")):
        with pytest.raises(LifeContractError, match=why):
            settle_and_score(
                protocol=bundle4b.protocol,
                state=s,
                action=plan_of(bundle4b, "u31.a61.c0"),
                catalog=cat,
                tax=tax_of(bundle4b, cat),
                scoring=bad,
            )


def test_a_binding_is_only_valid_under_the_protocol_it_was_built_with(bundle4b, scoring4b):
    year = bound_year(bundle4b, scoring4b, "u31.a61.c0")
    with pytest.raises(LifeContractError, match="另一份协议"):
        feedback_record(protocol=variant(bundle4b.protocol, **OFF), year=year)


def test_the_record_carries_ledger_and_score_fingerprints(bundle4b, scoring4b):
    _, r1 = _year_one(bundle4b, scoring4b)
    policy = bundle4b.protocol.energy_feedback
    basis = r1.next_state.energy_quota_basis
    assert r1.feedback.outcome_fingerprint == lf.fingerprint(r1.settlement.outcome)
    assert r1.feedback.score_fingerprint == lf.fingerprint(r1.score)
    assert basis.record_fingerprint == lf.fingerprint(r1.feedback)
    # r2：记录指向这次年度绑定、这次的候选状态与政策内容；正式下一年状态沿用同一份政策内容
    assert r1.feedback.binding_fingerprint == r1.binding_fingerprint
    assert r1.feedback.candidate_fingerprint == lf.fingerprint(r1.settlement.next_state)
    assert r1.feedback.policy_fingerprint == policy.content_fingerprint == basis.policy_fingerprint
    assert r1.feedback.feedback_revision == r1.feedback_revision == "r2-2026-10-08"
    assert (
        r1.settlement.next_state.energy_quota_basis.policy_fingerprint == policy.content_fingerprint
    )


# --------------------------------------------------------------------------
# 5–7：偏好来源、完整评分、已结算生活与反馈失败分开
# --------------------------------------------------------------------------


def test_the_product_path_never_borrows_research_truth(bundle4b, scoring4b):
    s = state_of(bundle4b, "u31.a61.c0")
    cat = catalog_of(bundle4b, s)
    with pytest.raises(LifeContractError, match="只接受 StatedLifeProfile"):
        run_life_year(
            protocol=bundle4b.protocol,
            state=s,
            action=plan_of(bundle4b, "u31.a61.c0"),
            catalog=cat,
            tax=tax_of(bundle4b, cat),
            scoring=scoring_inputs(bundle4b, scoring4b, s, research=True),
        )
    # 用户没给物质细项点数：偏好信息不足 → 反馈阻断；合成真值就在同一份输入里，也不借
    bare = revise(profile_of(bundle4b, "u31"), material_points=None)
    inputs = scoring_inputs(bundle4b, scoring4b, s)
    inputs = type(inputs)(spec=inputs.spec, conditions=inputs.conditions, preference_input=bare)
    r = run_life_year(
        protocol=bundle4b.protocol,
        state=s,
        action=plan_of(bundle4b, "u31.a61.c0"),
        catalog=cat,
        tax=tax_of(bundle4b, cat),
        scoring=inputs,
    )
    assert truth_of(bundle4b, "u31").material_subweights  # 真值在，但没被用
    assert (r.handoff, r.feedback.decision, r.score) == ("feedback_blocked", "blocked", None)
    assert any("偏好信息不足" in x for x in r.feedback.reasons)


def test_the_research_path_reads_research_truth(bundle4b, scoring4b):
    research = variant(bundle4b.protocol, preference_source="research_synthetic_truth")
    (r1, *_) = run_years(bundle4b, scoring4b, "u31.a61.c0", protocol=research, research=True)
    assert r1.score.preference_source == "research_synthetic_truth"
    assert r1.feedback.preference_source == "research_synthetic_truth"
    assert r1.next_state.energy.level == 264.01  # 这个样例里真值与陈述数值相同


def test_an_incomplete_year_blocks_the_update_without_filling_zeros(bundle4b, scoring4b):
    """u33 给家务点了 20 点，规格没有家务分项：12 个月都不完整 → 反馈信息不足。"""
    (r1,) = run_years(bundle4b, scoring4b, "u33.a61.c0")
    assert r1.score.annual_status == "incomplete_months" and r1.score.annual_mean is None
    f = r1.feedback
    assert (f.decision, f.raw_quota, f.new_quota, f.target_year) == ("blocked", None, None, 2)
    assert any("不补零" in x for x in f.reasons) and any("不是计划不可行" in x for x in f.reasons)
    # 这一年的真实结算保留；没有可以直接开始第 2 年的状态
    assert r1.settlement.status == "completed" and r1.settlement.outcome is not None
    assert (r1.handoff, r1.next_state) == ("feedback_blocked", None)
    candidate = r1.settlement.next_state
    assert candidate.energy_quota_basis.status == "pending_feedback"
    cat = catalog_of(bundle4b, candidate)
    stuck = settle_year(
        protocol=bundle4b.protocol,
        state=candidate,
        action=plan_of(bundle4b, "u33.a61.c0", 2),
        catalog=cat,
        tax=tax_of(bundle4b, cat),
    )
    assert stuck.status == "input_error" and stuck.problems[0].code == "energy_quota_pending"


def test_the_same_gap_does_not_stop_the_off_arm(bundle4b, scoring4b):
    """同样缺分：关闭反馈时三年照常执行，额度一直是基础值；评分缺失只记录。"""
    off = variant(bundle4b.protocol, **OFF)
    rs = run_years(bundle4b, scoring4b, "u33.a61.c0", protocol=off)
    assert [r.handoff for r in rs] == ["next_year_ready", "next_year_ready", "terminal"]
    assert [r.feedback.decision for r in rs] == [
        "off_disabled",
        "off_disabled",
        "no_next_action_year",
    ]
    assert {r.next_state.energy.level for r in rs[:2]} == {250}
    assert all(r.score.annual_status == "incomplete_months" for r in rs)


# --------------------------------------------------------------------------
# 8：失败计划、出局、终态与 N = 1
# --------------------------------------------------------------------------


def test_a_failed_plan_produces_no_feedback_and_no_next_state(bundle4b, scoring4b):
    off = variant(bundle4b.protocol, **OFF)
    _r1, r2 = run_years(bundle4b, scoring4b, "u31.a61.c0", protocol=off)
    assert r2.settlement.status == "plan_infeasible"
    assert (r2.handoff, r2.feedback.decision, r2.next_state, r2.score) == (
        "settlement_failed",
        "settlement_failed",
        None,
        None,
    )


@pytest.mark.parametrize("case", ["input_error", "data_error"])
def test_input_and_data_errors_produce_no_feedback_and_no_next_state(raw4b, scoring4b, case):
    """输入错误（拿第 2 年的计划结算第 1 年）与资料错误（城市没有声明最低食品）：不评分、不反馈、不推进。"""
    if case == "data_error":
        a61 = next(c for c in raw4b["catalogs"] if c["city"]["code"] == "A61")
        a61["minimum_living"] = [m for m in a61["minimum_living"] if m["need"] != "food"]
    b = parse_bundle(raw4b)
    s = state_of(b, "u31.a61.c0")
    cat = catalog_of(b, s)
    r = run_life_year(
        protocol=b.protocol,
        state=s,
        action=plan_of(b, "u31.a61.c0", 2 if case == "input_error" else 1),
        catalog=cat,
        tax=tax_of(b, cat),
        scoring=scoring_inputs(b, scoring4b, s),
    )
    assert r.settlement.status == case and r.settlement.outcome is None
    assert (r.handoff, r.feedback.decision, r.next_state, r.score) == (
        "settlement_failed",
        "settlement_failed",
        None,
        None,
    )


def test_an_exit_year_does_not_update_or_revive(bundle4b, scoring4b):
    (r1,) = run_years(bundle4b, scoring4b, "u34.a61.c0")
    assert (r1.handoff, r1.feedback.decision, r1.feedback.target_year) == ("exited", "exited", None)
    assert r1.next_state.status == "exited" and r1.next_state.energy.level == 150
    assert r1.score.annual_status == "exited" and r1.settlement.outcome.exit.month == 4


@pytest.mark.parametrize("n", [1, 3])
def test_the_last_action_year_never_computes_a_bonus(bundle4b, scoring4b, n):
    protocol = variant(bundle4b.protocol, years=n)
    rs = run_years(bundle4b, scoring4b, "u31.a61.c0", protocol=protocol)
    assert len(rs) == n
    last = rs[-1]
    assert (last.handoff, last.feedback.decision, last.next_state.year_index) == (
        "terminal",
        "no_next_action_year",
        n + 1,
    )
    assert last.score.annual_status == "complete"  # 最后一年照常评分与记录


# --------------------------------------------------------------------------
# 9：重放一致、重复交接不叠加、输入与账目不被修改
# --------------------------------------------------------------------------


def test_replays_are_identical_and_inputs_untouched(bundle4b, scoring4b):
    s = state_of(bundle4b, "u31.a61.c0")
    plan = plan_of(bundle4b, "u31.a61.c0")
    cat = catalog_of(bundle4b, s)
    before = (s.model_dump(), plan.model_dump(), cat.model_dump(), bundle4b.protocol.model_dump())
    a = run_life_year(
        protocol=bundle4b.protocol,
        state=s,
        action=plan,
        catalog=cat,
        tax=tax_of(bundle4b, cat),
        scoring=scoring_inputs(bundle4b, scoring4b, s),
    )
    b = run_life_year(
        protocol=bundle4b.protocol,
        state=s,
        action=plan,
        catalog=cat,
        tax=tax_of(bundle4b, cat),
        scoring=scoring_inputs(bundle4b, scoring4b, s),
    )
    assert a.model_dump() == b.model_dump()
    assert (
        s.model_dump(),
        plan.model_dump(),
        cat.model_dump(),
        bundle4b.protocol.model_dump(),
    ) == before


def test_applying_the_same_handoff_twice_does_not_stack(bundle4b, scoring4b):
    _, r1 = _year_one(bundle4b, scoring4b)
    with pytest.raises(LifeContractError, match="重复交接不叠加"):
        apply_feedback(r1.next_state, r1.feedback)  # 已经确定过额度的状态
    r2 = run_years(bundle4b, scoring4b, "u31.a61.c0")[1]
    with pytest.raises(LifeContractError, match="记录属于"):
        apply_feedback(
            r1.settlement.next_state, r2.feedback
        )  # 第 2 年的记录不能写进第 2 年年初的候选


def test_quota_basis_shapes_are_enforced():
    with pytest.raises(ValidationError, match="第一年"):
        EnergyQuotaBasis(base_level=250, status="base_first_year", source_year=1)
    with pytest.raises(ValidationError, match="反馈记录"):
        EnergyQuotaBasis(
            base_level=250,
            status="feedback_applied",
            policy_id="p.x",
            policy_version="v",
            source_year=1,
            policy_fingerprint="0" * 64,
        )
    # r2：已确定或待定的额度都必须写政策内容指纹；缺了就是 r1 旧格式，不猜它通过了检查
    for status, record in (("feedback_applied", "1" * 64), ("pending_feedback", None)):
        with pytest.raises(ValidationError, match="r1 旧格式"):
            EnergyQuotaBasis(
                base_level=250,
                status=status,
                policy_id="p.x",
                policy_version="v",
                source_year=1,
                record_fingerprint=record,
            )
    with pytest.raises(ValidationError, match="政策内容"):
        EnergyQuotaBasis(base_level=250, status="base_first_year", policy_fingerprint="0" * 64)


def test_policy_identity_is_explicit(bundle4b):
    policy = bundle4b.protocol.energy_feedback
    assert type(policy) is EnergyFeedbackPolicy and policy.effective
    assert not revise(policy, mode="disabled").effective
    assert not revise(policy, alpha=0.0).effective
