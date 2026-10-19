"""E1-5A 接线（`DECISIONS/0023`）：下一年的观察、年度绑定、可见投影、分身互不竞争与确定性重放。

验收第 7 条（旧协议的当年解锁照旧）由原测试
``test_settlement.py::test_eligibility_from_a_training_starts_the_month_after_it_is_completed`` 原样保留并照常通过；
这里不复制它。金额按当地基年不变价，负担是负担点，比较精确值。
"""

from __future__ import annotations

import copy
import json

import pytest

from agentlayer.life_views import (
    HEALTH_OBSERVATION_FIELDS,
    SAVINGS_OBSERVATION_FIELDS,
    AgentObservation,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import LifeContractError, qualification_gaps
from econ.life_feedback import apply_feedback, feedback_record
from ledger.life_catalog import parse_bundle

from .career_support import (
    ACCOUNTANT,
    ACCOUNTING,
    CLERK,
    DEVELOPER,
    FOOD,
    HOME,
    HR_LEDGERCO,
    HR_TRAINING,
    PYTHON,
    WASHER,
    income,
    job,
    plan_for,
    run_clone,
    year,
)
from .feedback_support import bound_year, profile_of
from .settle_support import catalog_of, cents, plan_of, state_of

U51 = (
    "u51.a71.study",
    "u51.a71.stay",
    "u51.a71.nostudy",
    "u51.a71.python",
    "u51.a71.midyear",
    "u51.a71.hr",
)


def _observe(bundle, state, last=None, career_rules="protocol") -> AgentObservation:
    return build_observation(
        permission=standard_product_permission("e1-5a-test"),
        profile=profile_of(bundle, state.person_id),
        state=state,
        catalog=catalog_of(bundle, state),
        last_feedback=last,
        resource_rules=bundle.protocol.resource_rules,
        energy_feedback=bundle.protocol.energy_feedback,
        career_rules=bundle.protocol.career_rules if career_rules == "protocol" else career_rules,
    )


def _opp(obs, oid):
    return next(o for o in obs.opportunities if o.opportunity_id == oid)


# --------------------------------------------------------------------------
# 11：下一年的观察与资格判断用延续后的能力与岗位
# --------------------------------------------------------------------------


def test_next_year_observation_shows_the_continued_abilities_and_job(bundle5a, scoring5a):
    y1, y2, _ = run_clone(bundle5a, scoring5a, "u51.a71.study")
    obs = _observe(bundle5a, y1.next_state, visible_feedback(y1.settlement))
    (a,) = obs.abilities
    assert (a.ability_id, a.label, a.kind, a.usable_from_year) == (
        "accounting_cert",
        "会计从业资格（合成）",
        "资格证书",
        2,
    )
    assert a.source == "第 1 年第 12 月完成会计资格课程（12 个月）"
    assert obs.employment.as_of == "第 1 年实际执行"
    assert [p.opportunity_id for p in obs.employment.current] == [CLERK]
    assert obs.employment.last_year[0].months == tuple(range(1, 13))
    assert obs.occupation.code == "43-6014"
    assert _opp(obs, ACCOUNTANT).qualification.status == "available_now"
    dev = _opp(obs, DEVELOPER).qualification
    assert dev.status == "not_yet" and dev.missing[0].startswith("缺能力「Python 编程」")
    assert _opp(obs, HR_LEDGERCO).qualification.status == "not_yet"
    assert _opp(obs, CLERK).qualification is None  # 没有要求的岗位不写资格
    (tr,) = obs.training
    assert (tr.completed, tr.fee_paid, tr.completed_year, tr.completed_month) == (True, True, 1, 12)
    (learn,) = obs.last_feedback.career.learning
    assert (
        cents(learn.fee_charged),
        learn.months_attended,
        learn.granted,
        learn.usable_from_year,
    ) == (
        1_200_00,
        tuple(range(1, 13)),
        ("accounting_cert",),
        2,
    )
    # 第 3 年年初：上一年实际做的是会计；用户开局陈述的职业原样保留
    obs3 = _observe(bundle5a, y2.next_state, visible_feedback(y2.settlement))
    assert [p.opportunity_id for p in obs3.employment.current] == [ACCOUNTANT]
    assert (obs3.occupation.code, obs3.employment.as_of) == ("13-2011", "第 2 年实际执行")
    assert obs3.employment.stated_occupation.code == "43-6014"


# --------------------------------------------------------------------------
# 14：可见学习事实出现（进度、学费、时间与负担、授予什么、何时生效、缺什么）；内部对象被拒
# --------------------------------------------------------------------------


def test_a_course_in_progress_shows_what_it_costs_grants_and_when_it_counts(bundle5a, scoring5a):
    (m1, *_) = run_clone(bundle5a, scoring5a, "u51.a71.midyear")
    obs = _observe(bundle5a, m1.next_state, visible_feedback(m1.settlement))
    assert obs.abilities == ()  # E1-5A 里明确写"一项都没有"
    (tr,) = obs.training
    assert (tr.months_participated, tr.months_required, tr.completed, tr.fee_paid) == (
        6,
        12,
        False,
        True,
    )
    missing = _opp(obs, ACCOUNTANT).qualification.missing[0]
    assert "已参加 6/12 个月，完成后从下一个行动年起可用" in missing
    course = _opp(obs, ACCOUNTING)
    assert course.grants == ("accounting_cert",) and course.qualification is None
    assert course.price.amount.amount == 1200
    assert (course.time_energy.hours, course.time_energy.burden_per_hour) == (40, 1.2)
    free = _opp(obs, PYTHON)
    assert (free.price.amount.amount, free.time_energy.hours) == (0, 30)
    inhouse = _opp(obs, HR_TRAINING)
    assert inhouse.employer_id == "firm.netco" and inhouse.qualification.status == "available_now"
    rules = obs.career_rules
    assert any("下一个行动年" in r for r in rules.rules)
    assert any("取得资格不等于自动换工作" in r for r in rules.rules)
    scopes = {a.ability_id: a.scope for a in rules.abilities}
    assert scopes["hr_rules_netco"] == "只用于雇主 firm.netco 的岗位与培训"
    payload = observation_payload(obs)
    # 2026-10-09 E1-5B 接口适配：E1-5A 协议没有目标储蓄，顶层字段 savings 不序列化（只在 E1-5B 协议出现）
    # 2026-10-11 E1-5C 接口适配：E1-5A 协议也没有疾病规则，顶层字段 health 不序列化（只在 E1-5C 协议出现）
    assert (
        set(payload)
        == set(AgentObservation.model_fields)
        - SAVINGS_OBSERVATION_FIELDS
        - HEALTH_OBSERVATION_FIELDS
    )
    blob = json.dumps(payload, ensure_ascii=False)
    for internal in (
        "acquired_month",
        "source_opportunity_id",
        "abilities_at_start",
        "content_fingerprint",
    ):
        assert internal not in blob


def test_internal_career_objects_never_reach_the_payload(bundle5a, scoring5a):
    (y1, *_) = run_clone(bundle5a, scoring5a, "u51.a71.study")
    s2 = y1.next_state
    obs = _observe(bundle5a, s2)
    dev = catalog_of(bundle5a, s2).opportunity(DEVELOPER)
    career = y1.settlement.outcome.career
    leaks = (
        s2.abilities[0],
        s2.employment,
        s2.employment.positions[0],
        bundle5a.protocol.career_rules,
        bundle5a.protocol.career_rules.abilities[0],
        bundle5a.protocol.career_rules.employers[0],
        career,
        career.learning[0],
        qualification_gaps(dev, s2)[0],
    )
    for leak in leaks:
        smuggled = obs.model_copy(update={"goals": (leak,)})  # model_copy 不校验：模拟写错的投影
        with pytest.raises(LifeContractError, match="出现在可见字段"):
            observation_payload(smuggled)


def test_career_rules_come_with_e1_5a_states_only(bundle5a, bundle4b):
    s = state_of(bundle5a, "u51.a71.study")
    with pytest.raises(LifeContractError, match="必须同时给协议的职业规则"):
        _observe(bundle5a, s, career_rules=None)
    old = state_of(bundle4b, "u31.a61.c0")
    with pytest.raises(LifeContractError, match="必须同时给协议的职业规则"):
        build_observation(
            permission=standard_product_permission("e1-5a-test"),
            profile=profile_of(bundle4b, "u31"),
            state=old,
            catalog=catalog_of(bundle4b, old),
            resource_rules=bundle4b.protocol.resource_rules,
            energy_feedback=bundle4b.protocol.energy_feedback,
            career_rules=bundle5a.protocol.career_rules,
        )


# --------------------------------------------------------------------------
# 13：授予能力与岗位更新发生在年度绑定之前；跨试算混用被拒
# --------------------------------------------------------------------------


def test_the_binding_covers_the_new_abilities_and_job(bundle5a, scoring5a):
    """同人、同城、同年的两次真实试算：上会计课 / 不上课。正确配对照常交接，交叉配对与篡改一律拒绝。"""
    study = bound_year(bundle5a, scoring5a, "u51.a71.study")
    plain = bound_year(
        bundle5a,
        scoring5a,
        "u51.a71.study",
        action=plan_for(bundle5a, "u51.a71.study", 1, HOME, FOOD, job(CLERK), WASHER),
    )
    rec_study = feedback_record(protocol=bundle5a.protocol, year=study)
    rec_plain = feedback_record(protocol=bundle5a.protocol, year=plain)
    got = apply_feedback(study.settlement.next_state, rec_study)
    assert [a.ability_id for a in got.abilities] == ["accounting_cert"]
    assert apply_feedback(plain.settlement.next_state, rec_plain).abilities == ()
    # 没上课那次的候选状态，拿上课那次的记录（白得一项能力的路径之一）
    with pytest.raises(LifeContractError, match="另一次年度结算的候选状态"):
        apply_feedback(plain.settlement.next_state, rec_study)
    # 两次试算全年都做文员：岗位记录相同，交换它不构成篡改
    assert plain.settlement.next_state.employment == study.settlement.next_state.employment
    # 候选状态被改：塞进一项没学过的能力，或者把岗位记录改成一份没做过的会计工作
    candidate = study.settlement.next_state
    python = candidate.abilities[0].model_copy(update={"ability_id": "python_programming"})
    position = candidate.employment.positions[0]
    accountant = position.model_copy(
        update={
            "opportunity_id": ACCOUNTANT,
            "occupation": catalog_of(bundle5a, candidate).opportunity(ACCOUNTANT).occupation,
        }
    )
    promoted = candidate.employment.model_copy(
        update={"positions": (accountant,), "current_position_ids": (ACCOUNTANT,)}
    )
    for forged in (
        candidate.model_copy(update={"abilities": (*candidate.abilities, python)}),
        candidate.model_copy(update={"employment": promoted}),
    ):
        with pytest.raises(LifeContractError, match="另一次年度结算的候选状态"):
            apply_feedback(forged, rec_study)
    # 把上课那次的整份结算（含学习与能力）换进没上课那次的绑定
    with pytest.raises(LifeContractError, match="被改动过"):
        feedback_record(
            protocol=bundle5a.protocol,
            year=plain.model_copy(update={"settlement": study.settlement}),
        )


def test_bindings_replay_deterministically(bundle5a, scoring5a):
    a = bound_year(bundle5a, scoring5a, "u51.a71.midyear")
    b = bound_year(bundle5a, scoring5a, "u51.a71.midyear")
    assert a.binding_fingerprint == b.binding_fingerprint
    assert a.model_dump_json() == b.model_dump_json()
    record = feedback_record(protocol=bundle5a.protocol, year=a)
    first = apply_feedback(a.settlement.next_state, record)
    with pytest.raises(LifeContractError, match="重复交接不叠加"):
        apply_feedback(first, record)


# --------------------------------------------------------------------------
# 15：单独运行与和其他分身一起运行结果一致；没有名额与竞争
# --------------------------------------------------------------------------


def test_a_clone_alone_equals_the_same_clone_among_others(bundle5a, scoring5a):
    alone = {c: [r.model_dump_json() for r in run_clone(bundle5a, scoring5a, c)] for c in U51}
    # 六个分身逐年轮流交接，顺序倒过来：每个分身的结果与单独运行逐字相同
    states = {c: state_of(bundle5a, c) for c in U51}
    together: dict[str, list[str]] = {c: [] for c in U51}
    for y in (1, 2, 3):
        for c in reversed(U51):
            r = year(bundle5a, scoring5a, states[c], plan_of(bundle5a, c, y))
            together[c].append(r.model_dump_json())
            if r.handoff == "next_year_ready":
                states[c] = r.next_state
    assert together == alone


def test_two_identical_clones_both_get_the_job(raw5a, scoring5a):
    """同一个人的两个分身计划完全相同：第 2 年都做会计，工资都是 48,000——没有名额，也没有录用概率。"""
    twin = copy.deepcopy(
        next(s for s in raw5a["initial_states"] if s["clone_id"] == "u51.a71.study")
    )
    twin["clone_id"] = "u51.a71.twin"
    raw5a["initial_states"].append(twin)
    for p in [p for p in raw5a["plans"] if p["clone_id"] == "u51.a71.study"]:
        raw5a["plans"].append({**copy.deepcopy(p), "clone_id": "u51.a71.twin"})
    b = parse_bundle(raw5a)
    from ledger.life_scoring_config import load_scoring_config

    config, _ = load_scoring_config("configs/life/scoring_e1_5a.yaml", b)
    first = run_clone(b, config, "u51.a71.study")
    second = run_clone(b, config, "u51.a71.twin")
    assert (
        [income(r) for r in first]
        == [income(r) for r in second]
        == [
            (36000, 4800),
            (48000, 7200),
            (48000, 7200),
        ]
    )
    for x, y in zip(first, second, strict=True):
        assert x.settlement.outcome.career == y.settlement.outcome.career
        assert x.settlement.outcome.cash_end == y.settlement.outcome.cash_end
