"""E1-4B r2 修复（`DECISIONS/0021` 第十三节）：额度绑定政策内容（F1），反馈绑定同一次年度结算与评分（F2）。

独立审核（`runs/2026-10-08-e1-4b-review/`）复现的两类误配都在这里写成永久测试：

* F1：编号与版本不变、只改开关或 α 的政策，曾经能消费旧加成，还能把带着加成的状态标成静态文字理解条件；
* F2：同人、同城、同年的两次真实试算（住近处 / 住远处）曾经能互换账目、分数与候选状态。

额度单位是负担点、满足感是 0–1 的无量纲数，按精确值比较，不加容差。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agentlayer.life_views import (
    ObservationPermission,
    build_observation,
    standard_product_permission,
)
from common.life_contracts import (
    EnergyFeedbackPolicy,
    LifeContractError,
    LifeProtocol,
    PersonYearState,
    model_fingerprint,
)
from common.life_feedback_contracts import ScoredYear
from econ.life_feedback import apply_feedback, feedback_record
from econ.life_settlement import settle_year
from econ.life_year import run_life_year
from ledger.life_catalog import cross_validate, parse_bundle

from .feedback_support import (
    bound_year,
    far_plan,
    profile_of,
    run_years,
    same_id,
)
from .settle_support import catalog_of, plan_of, revise, state_of, tax_of

#: 只改政策内容、不改编号与版本的几种改法（全部应该被识别为"另一份政策"）。
CONTENT_CHANGES = [
    {"mode": "disabled"},
    {"alpha": 0.09},
    {"alpha": 0.0},
    {"preference_source": "research_synthetic_truth"},
    # 2026-10-09（DECISIONS/0025）：原写假想版本 e1-3-supplement-v2；非旧格式版本现在必须声明评分内容身份，
    # 改用真实的旧格式版本表达"同编号、换评分版本"
    {"scoring_version": "e1-3-explore-v1"},
    {"scoring_spec_id": "score.other"},
]


def _boosted(bundle4b, scoring4b):
    """真实跑完第 1 年：第 2 年额度 264.01 负担点（250 × (1 + 0.10 × 0.5604…) 向下保留到 0.01）。"""
    (r1, *_) = run_years(bundle4b, scoring4b, "u31.a61.c0")
    assert r1.next_state.energy.level == 264.01
    return r1.next_state


def _research(show_energy: bool) -> ObservationPermission:
    return ObservationPermission(
        condition="research_text_only",
        show_stated_dimension_points=False,
        show_soft_preferences=False,
        show_hard_requirements=True,
        show_free_text=True,
        show_energy_level=show_energy,
        show_stated_material_points=False,
        permission_version="r2-test",
    )


def _observe(bundle4b, state, policy, permission):
    return build_observation(
        permission=permission,
        profile=profile_of(bundle4b, state.person_id),
        state=state,
        catalog=catalog_of(bundle4b, state),
        resource_rules=bundle4b.protocol.resource_rules,
        energy_feedback=policy,
    )


# --------------------------------------------------------------------------
# F1：政策内容身份
# --------------------------------------------------------------------------


def test_the_content_fingerprint_covers_behaviour_and_ignores_descriptions(bundle4b):
    policy = bundle4b.protocol.energy_feedback
    fp = policy.content_fingerprint
    assert set(policy.content()) == {
        "content_schema",
        "version",
        "mode",
        "alpha_hundredths",
        "formula",
        "lag_years",
        "score_statistic",
        "quantization",
        "scoring_spec_id",
        "scoring_version",
        "preference_source",
    }
    assert policy.content()["alpha_hundredths"] == 10
    # 只改说明、溯源或名字：数学政策不变
    for change in (
        {"note": "只改说明文字"},
        {"policy_id": "feedback.renamed"},
        {"source": {**policy.source.model_dump(mode="python"), "synthetic_note": "另一段溯源说明"}},
    ):
        assert revise(policy, **change).content_fingerprint == fp
    # 改任何一个行为字段：另一份政策
    for change in CONTENT_CHANGES:
        assert revise(policy, **change).content_fingerprint != fp, change
    # 规范化：字段顺序打乱、α 写成 0.10 还是 0.1，都得到同一个指纹
    data = policy.model_dump(mode="python")
    shuffled = dict(reversed(list(data.items())))
    assert EnergyFeedbackPolicy.model_validate(shuffled).content_fingerprint == fp
    assert EnergyFeedbackPolicy.model_validate({**data, "alpha": 0.10}).content_fingerprint == fp


@pytest.mark.parametrize("changes", CONTENT_CHANGES, ids=lambda c: next(iter(c)))
def test_same_id_and_version_but_other_content_is_refused_at_settlement(
    bundle4b, scoring4b, changes
):
    """第 2 年带着 264.01 的加成，政策编号与版本不变、内容变了：输入错误，不提交账目，不推进。"""
    boosted = _boosted(bundle4b, scoring4b)
    altered = same_id(bundle4b.protocol, **changes)
    assert altered.energy_feedback.policy_id == bundle4b.protocol.energy_feedback.policy_id
    cat = catalog_of(bundle4b, boosted)
    r = settle_year(
        protocol=altered,
        state=boosted,
        action=plan_of(bundle4b, "u31.a61.c0", 2),
        catalog=cat,
        tax=tax_of(bundle4b, cat),
    )
    codes = {p.code for p in r.problems}
    assert r.status == "input_error" and "feedback_policy_content_changed" in codes
    assert r.outcome is None and r.next_state is None
    # 同一入口的年度交接：结算失败，不反馈、不推进，也不当成生活不可行
    y = run_life_year(
        protocol=altered,
        state=boosted,
        action=plan_of(bundle4b, "u31.a61.c0", 2),
        catalog=cat,
        tax=tax_of(bundle4b, cat),
        scoring=None,
    )
    assert (y.handoff, y.settlement.status, y.next_state) == (
        "settlement_failed",
        "input_error",
        None,
    )


def test_switching_off_with_the_same_id_does_not_spend_the_old_boost(bundle4b, scoring4b):
    """独立审核的反例：关掉反馈（同编号），每月负担 255 的第 2 年计划曾在 264.01 下"成功"。"""
    boosted = _boosted(bundle4b, scoring4b)
    off = same_id(bundle4b.protocol, mode="disabled")
    cat = catalog_of(bundle4b, boosted)
    r = settle_year(
        protocol=off,
        state=boosted,
        action=plan_of(bundle4b, "u31.a61.c0", 2),
        catalog=cat,
        tax=tax_of(bundle4b, cat),
    )
    codes = [p.code for p in r.problems]
    assert (
        r.status == "input_error"
    )  # 不是 completed（旧加成），也不是 plan_infeasible（生活不可行）
    assert "feedback_policy_content_changed" in codes and "energy_quota_not_base" in codes


@pytest.mark.parametrize("show_energy", [True, False], ids=["energy_shown", "energy_hidden"])
@pytest.mark.parametrize("condition", ["research_text_only", "product_stated_profile"])
def test_a_boosted_state_is_never_observed_under_other_policy_content(
    bundle4b, scoring4b, condition, show_energy
):
    """带着加成的状态 + 同编号的关闭政策：不构造观察，更不会标成静态文字理解条件；隐藏额度也绕不过。"""
    boosted = _boosted(bundle4b, scoring4b)
    off = same_id(bundle4b.protocol, mode="disabled").energy_feedback
    if condition == "research_text_only":
        permission = _research(show_energy)
    else:
        base = standard_product_permission(bundle4b.protocol.info_permission_version)
        permission = ObservationPermission(
            **{**base.model_dump(), "show_energy_level": show_energy}
        )
    with pytest.raises(LifeContractError, match="不为这个状态构造观察"):
        _observe(bundle4b, boosted, off, permission)


def test_the_same_policy_rebuilt_from_its_declaration_is_accepted(bundle4b, scoring4b):
    """合法重放：从声明重新解析出的同一份政策、或只改说明文字，都照常继续第 2 年（264.01 下 255 可行）。"""
    boosted = _boosted(bundle4b, scoring4b)
    rebuilt = LifeProtocol.model_validate(json.loads(bundle4b.protocol.model_dump_json()))
    noted = same_id(bundle4b.protocol, note="只改说明文字，不改数学政策")
    cat = catalog_of(bundle4b, boosted)
    for protocol in (rebuilt, noted):
        r = settle_year(
            protocol=protocol,
            state=boosted,
            action=plan_of(bundle4b, "u31.a61.c0", 2),
            catalog=cat,
            tax=tax_of(bundle4b, cat),
        )
        assert r.status == "completed", [p.code for p in r.problems]
        assert {m.energy_quota.exact for m in r.outcome.resources.months} == {"26401/100"}


def test_the_loader_reports_a_state_bound_to_other_policy_content(raw4b, bundle4b, scoring4b):
    """加载检查用同一份判断：把带加成的第 2 年状态放进初始状态，协议改成同编号的关闭政策。"""
    boosted = _boosted(bundle4b, scoring4b)
    raw4b["protocol"]["energy_feedback"]["mode"] = "disabled"
    raw4b["initial_states"] = [
        boosted.model_dump(mode="json") if s["clone_id"] == "u31.a61.c0" else s
        for s in raw4b["initial_states"]
    ]
    errors = cross_validate(parse_bundle(raw4b)).errors
    assert any("内容指纹" in e for e in errors)
    assert any("不能消费开启反馈产生的加成" in e for e in errors)


def test_a_first_year_state_stays_valid_under_every_arm(bundle4b):
    """第一年状态不绑定政策：所有合法实验臂从同一个初始状态出发。"""
    start = state_of(bundle4b, "u31.a61.c0")
    assert start.energy_quota_basis.status == "base_first_year"
    assert start.energy_quota_basis.policy_fingerprint is None
    cat = catalog_of(bundle4b, start)
    for changes in ({}, {"mode": "disabled"}, {"alpha": 0.0}, {"alpha": 0.05}):
        r = settle_year(
            protocol=same_id(bundle4b.protocol, **changes) if changes else bundle4b.protocol,
            state=start,
            action=plan_of(bundle4b, "u31.a61.c0"),
            catalog=cat,
            tax=tax_of(bundle4b, cat),
        )
        assert r.status == "completed"


def test_an_r1_state_without_policy_content_is_refused_not_guessed(bundle4b, scoring4b):
    """r1 产生的中间状态没有政策内容指纹：不猜它通过了检查，明确拒绝（从初始输入重跑）。"""
    (r1, *_) = run_years(bundle4b, scoring4b, "u31.a61.c0")
    for state in (r1.next_state, r1.settlement.next_state):
        old = state.model_dump(mode="json")
        old["energy_quota_basis"].pop("policy_fingerprint")
        with pytest.raises(ValidationError, match="r1 旧格式"):
            PersonYearState.model_validate(old)


# --------------------------------------------------------------------------
# F2：同一次年度结算与评分
# --------------------------------------------------------------------------


def test_two_real_lives_cannot_trade_scores_ledgers_or_candidates(bundle4b, scoring4b):
    """同人、同城、同年的两次真实试算：住近处 S = 0.5604166666666667 → 264.01；
    住远处 S = 0.5399038461538461 → 263.49。正确配对照常交接，交叉配对一律拒绝。"""
    near = bound_year(bundle4b, scoring4b, "u31.a61.c0")
    far = bound_year(
        bundle4b, scoring4b, "u31.a61.c0", action=far_plan(plan_of(bundle4b, "u31.a61.c0"))
    )
    assert (near.score.annual_mean, far.score.annual_mean) == (
        0.5604166666666667,
        0.5399038461538461,
    )
    assert near.binding_fingerprint != far.binding_fingerprint
    near_record = feedback_record(protocol=bundle4b.protocol, year=near)
    far_record = feedback_record(protocol=bundle4b.protocol, year=far)
    assert apply_feedback(near.settlement.next_state, near_record).energy.level == 264.01
    assert apply_feedback(far.settlement.next_state, far_record).energy.level == 263.49
    # 远房的账目配近房的分数；远房的绑定换上近房的整份结算
    for swapped in (
        far.model_copy(update={"score": near.score}),
        far.model_copy(update={"settlement": near.settlement}),
    ):
        with pytest.raises(LifeContractError, match="被改动过"):
            feedback_record(protocol=bundle4b.protocol, year=swapped)
    # 近房的反馈写到远房的候选状态（远房的现金 + 近房的加成）
    with pytest.raises(LifeContractError, match="另一次年度结算的候选状态"):
        apply_feedback(far.settlement.next_state, near_record)


def test_an_unsealed_binding_is_refused_even_when_internally_consistent(bundle4b, scoring4b):
    """ "任意一份分数 + 任意一份账目、各算一次指纹"拼出来的绑定，指纹自洽也不接受：它没有经过真正的结算与评分。"""
    near = bound_year(bundle4b, scoring4b, "u31.a61.c0")
    far = bound_year(
        bundle4b, scoring4b, "u31.a61.c0", action=far_plan(plan_of(bundle4b, "u31.a61.c0"))
    )
    fields = {
        **{k: getattr(far, k) for k in ScoredYear.model_fields},
        "score": near.score,
        "score_fingerprint": model_fingerprint(near.score),
    }
    draft = ScoredYear.model_construct(**fields)
    assembled = ScoredYear(
        **{**fields, "binding_fingerprint": draft.expected_binding_fingerprint()}
    )
    rebuilt = ScoredYear.model_validate(near.model_dump(mode="python"))
    restored = ScoredYear.model_validate_json(near.model_dump_json())
    assert near.sealed and not any(x.sealed for x in (assembled, rebuilt, restored))
    for fake in (assembled, rebuilt, restored):
        with pytest.raises(LifeContractError, match="没有封存"):
            feedback_record(protocol=bundle4b.protocol, year=fake)
    # 对象与指纹对不上的绑定（近房的分数、远房分数的指纹），连构造都通不过
    with pytest.raises(ValidationError, match="分数与绑定时记下的指纹不一致"):
        ScoredYear(**{**fields, "score_fingerprint": far.score_fingerprint})


def test_a_candidate_from_other_policy_content_cannot_take_this_record(bundle4b, scoring4b):
    """同编号、α 改成 0.09 的政策下产生的候选状态，不能用 α = 0.10 那次交接的记录确定额度。"""
    main = bound_year(bundle4b, scoring4b, "u31.a61.c0")
    altered = bound_year(
        bundle4b, scoring4b, "u31.a61.c0", protocol=same_id(bundle4b.protocol, alpha=0.09)
    )
    record = feedback_record(protocol=bundle4b.protocol, year=main)
    with pytest.raises(LifeContractError, match="另一份政策内容"):
        apply_feedback(altered.settlement.next_state, record)


def test_bindings_replay_deterministically(bundle4b, scoring4b):
    """同样的输入重放，绑定指纹逐字相同（没有随机运行编号）；输入状态不被修改。"""
    start = state_of(bundle4b, "u31.a61.c0")
    before = start.model_dump_json()
    a = bound_year(bundle4b, scoring4b, "u31.a61.c0")
    b = bound_year(bundle4b, scoring4b, "u31.a61.c0")
    assert a.binding_fingerprint == b.binding_fingerprint
    assert a.model_dump_json() == b.model_dump_json()
    assert start.model_dump_json() == before
    # 同一份绑定交接两次：第二次没有待定的候选状态可写，不叠加
    record = feedback_record(protocol=bundle4b.protocol, year=a)
    first = apply_feedback(a.settlement.next_state, record)
    with pytest.raises(LifeContractError, match="重复交接不叠加"):
        apply_feedback(first, record)
