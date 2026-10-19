"""E1-6A：轨迹核对、重放、内容身份、信息隔离与交通—健康反例（DECISIONS/0028）。

只接受受控运行里产生的结果：每一年必须带着 ``run_life_year`` 当场封存的年度绑定；交接从封存绑定重新推出，不相信结果
对象里的字段。评价的内部对象（规格、参考方案、逐月明细、估值、合成分）与它们原样序列化后的字典都不能进代理观察。
"""

from __future__ import annotations

import json

import pytest

from agentlayer.life_views import (
    FORBIDDEN_PAYLOAD_KEYS,
    AgentObservation,
    VisibleFeedback,
    assert_payload_clean,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import LifeContractError
from common.life_evaluation_contracts import (
    RESERVED_EVALUATION_KEYS,
    CloneEvaluation,
    internal_evaluation_models,
)
from common.life_feedback_contracts import LifeYearResult
from common.money import Money
from econ.life_evaluation import evaluate_clone
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs
from ledger.life_catalog import internal_leak_problems

from .evaluation_support import (
    evaluate,
    f,
    run_clone,
    scoring_inputs,
    spec_with,
    x,
)
from .settle_support import catalog_of, protocol_with_years, revise, state_of, tax_of

U61, U62, U63 = "u61.a93.low", "u62.a93.rich", "u63.a93.dryer"
U70, U71 = "u70.a93.commute_sick", "u71.a93.commute_well"


def _call(bundle_ev, scoring_ev, spec, clone, results, **over):
    s = state_of(bundle_ev, clone)
    catalog = over.pop("catalog", catalog_of(bundle_ev, s))
    args = dict(
        spec=spec,
        protocol=bundle_ev.protocol,
        catalog=catalog,
        tax=tax_of(bundle_ev, catalog_of(bundle_ev, s)),
        scoring=scoring_inputs(bundle_ev, scoring_ev, s),
        results=results,
    )
    args.update(over)
    return evaluate_clone(**args)


# ---------------------------------------------------------------------- 重放与内容身份


def test_replaying_the_original_inputs_reproduces_the_result_exactly(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    first = evaluate(bundle_ev, scoring_ev, evaluation_ev, U62, lives_ev[U62])
    replay = evaluate(
        bundle_ev, scoring_ev, evaluation_ev, U62, run_clone(bundle_ev, scoring_ev, U62)
    )
    assert replay.model_dump_json() == first.model_dump_json()
    assert replay.result_fingerprint == first.result_fingerprint
    assert first.result_fingerprint == first.expected_result_fingerprint()


def test_the_same_spec_name_with_other_content_is_another_identity(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    fewer = spec_with(
        evaluation_ev,
        life_templates=[p.model_dump(mode="python") for p in evaluation_ev.life_templates[:3]],
    )
    assert fewer.spec_id == evaluation_ev.spec_id
    assert fewer.content_fingerprint != evaluation_ev.content_fingerprint
    a = evaluate(bundle_ev, scoring_ev, evaluation_ev, U62, lives_ev[U62])
    b = evaluate(bundle_ev, scoring_ev, fewer, U62, lives_ev[U62])
    assert (a.spec_content_fingerprint, b.spec_content_fingerprint) == (
        evaluation_ev.content_fingerprint,
        fewer.content_fingerprint,
    )
    assert a.result_fingerprint != b.result_fingerprint
    # 标签、说明与备注不是计算内容：改了它们内容身份不变
    plans = evaluation_ev.model_dump(mode="python")["life_templates"]
    plans[0]["label"], plans[0]["description"] = "改了标签", "改了说明"
    relabeled = spec_with(evaluation_ev, life_templates=plans, note="改了备注")
    assert relabeled.content_fingerprint == evaluation_ev.content_fingerprint


def test_a_tampered_evaluation_result_is_refused(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U62, lives_ev[U62])
    data = ev.model_dump(mode="python")
    data["valuation"]["v_reference_display"] = 0.99
    with pytest.raises(ValueError, match="指纹与内容不一致"):
        CloneEvaluation.model_validate(data)


# ---------------------------------------------------------------------- 只认受控运行里的同一次生活


def test_results_restored_from_json_are_refused(bundle_ev, scoring_ev, evaluation_ev, lives_ev):
    restored = [LifeYearResult.model_validate(r.model_dump(mode="python")) for r in lives_ev[U61]]
    assert all(r.scored_year is None for r in restored)
    with pytest.raises(LifeContractError, match="本轮不支持直接导入"):
        evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, restored)


def test_a_terminal_that_is_not_the_real_handoff_is_refused(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """把第 3 年交接结果复制一份、只把终态的现金改大（封存绑定原样带着）：核对从封存绑定重新推出交接，拒绝。"""
    *head, last = lives_ev[U61]
    richer = revise(last.next_state, cash=Money(amount=99999, numeraire="USD_2024_LOCAL"))
    fake = last.model_copy(update={"next_state": richer})
    assert fake.scored_year is last.scored_year
    with pytest.raises(LifeContractError, match="终态与下一年状态只认真实交接"):
        evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, [*head, fake])
    relabeled = last.model_copy(update={"handoff": "exited"})
    with pytest.raises(LifeContractError, match="只认真实交接"):
        evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, [*head, relabeled])


def test_years_of_different_trials_cannot_be_chained(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    other = run_clone(
        bundle_ev,
        scoring_ev,
        U61,
        state=revise(state_of(bundle_ev, U61), cash=Money(amount=2999, numeraire="USD_2024_LOCAL")),
    )
    mixed = [lives_ev[U61][0], *other[1:]]
    with pytest.raises(LifeContractError, match="不能把不同试算的年份接在一起"):
        evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, mixed)
    # 另一个分身的年份：最先触发的是"评分输入属于另一个人"或"混了别的分身"，都是拒绝
    with pytest.raises(LifeContractError, match=r"混了别的分身|评分输入"):
        evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, [lives_ev[U61][0], *lives_ev[U62][1:]])


def test_gaps_duplicates_and_extra_years_are_refused(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    y1, y2, y3 = lives_ev[U61]
    for bad in ([y1, y1], [y1, y3], [y2, y3]):
        with pytest.raises(LifeContractError, match="年份必须从第 1 年起连续"):
            evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, bad)
    swapped = y2.model_copy()
    swapped._scored_year = lives_ev[U62][1].scored_year
    with pytest.raises(LifeContractError, match="指向不同的生活"):
        evaluate(bundle_ev, scoring_ev, evaluation_ev, U61, [y1, swapped, y3])


def test_inputs_other_than_the_ones_lived_with_are_refused(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    rs = lives_ev[U61]
    with pytest.raises(LifeContractError, match="另一份协议"):
        _call(
            bundle_ev,
            scoring_ev,
            evaluation_ev,
            U61,
            rs,
            protocol=protocol_with_years(bundle_ev.protocol, 4),
        )
    # E1-6A 收尾 F1：目录城市先于目录指纹核对，拿错城市的目录时先报更具体的那一条；两条都是拒绝
    with pytest.raises(LifeContractError, match=r"城市目录不是|评价器拿到的是 .* 的目录"):
        _call(
            bundle_ev,
            scoring_ev,
            evaluation_ev,
            U61,
            rs,
            catalog=bundle_ev.catalog_for("US:SYNTH2026:B94"),
        )
    policy = bundle_ev.tax_policy("tax.synth.ze")
    other_tax = binding_from_policy(
        revise(policy, brackets=[{"lower": 0, "rate": 0.0}, {"lower": 30000, "rate": 0.2}])
    )
    with pytest.raises(LifeContractError, match="税绑定"):
        _call(bundle_ev, scoring_ev, evaluation_ev, U61, rs, tax=other_tax)
    truth = next(t for t in bundle_ev.internal_truths if t.person_id == "u61")
    s = state_of(bundle_ev, U61)
    research = ScoringInputs(
        spec=scoring_ev.spec,
        conditions=scoring_ev.conditions_for(str(s.city)),
        preference_input=truth,
        hard_requirements=(),
    )
    with pytest.raises(LifeContractError, match="产品陈述与研究真值不混用"):
        _call(bundle_ev, scoring_ev, evaluation_ev, U61, rs, scoring=research)
    with pytest.raises(LifeContractError, match="spec 必须是 EvaluationSpec"):
        _call(bundle_ev, scoring_ev, evaluation_ev.model_dump(), U61, rs)


# ---------------------------------------------------------------------- 信息隔离


def test_evaluation_internals_and_their_dumps_never_reach_the_payload(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    assert RESERVED_EVALUATION_KEYS <= FORBIDDEN_PAYLOAD_KEYS
    from pydantic import BaseModel

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
    assert not RESERVED_EVALUATION_KEYS & names  # 保留键不与可见字段重名

    ev = evaluate(bundle_ev, scoring_ev, evaluation_ev, U63, lives_ev[U63])
    p2 = next(p for p in ev.valuation.plans if p.plan_id == "p2_current_comfort")
    objects = [
        evaluation_ev,
        evaluation_ev.slots[0],
        evaluation_ev.life_templates[1],
        evaluation_ev.life_templates[1].durable_purchases[0],
        evaluation_ev.city_bindings[0],
        evaluation_ev.city_bindings[0].targets[0],
        ev,
        ev.trajectory,
        ev.trajectory.years[0],
        ev.actual,
        ev.actual.months[0],
        ev.valuation,
        ev.valuation.context,
        ev.valuation.context.quota,
        ev.valuation.context.start,
        ev.valuation.context.start.holdings[0],
        p2,
        p2.items[0],
        p2.end,
        ev.combined,
    ]
    assert {type(o) for o in objects} <= set(internal_evaluation_models())
    for obj in objects:
        with pytest.raises(LifeContractError, match="出现在可见字段"):
            assert_payload_clean({"notes": [{"deep": [obj]}]})
        with pytest.raises(LifeContractError, match=r"内部键|原样序列化的内部评分对象"):
            assert_payload_clean({"notes": [{"nested": obj.model_dump(mode="json")}]})
    with pytest.raises(LifeContractError, match="内部键"):
        assert_payload_clean({"notes": [{"v_reference": 0.65}]})


def test_a_normal_observation_carries_no_evaluation_content(bundle_ev, lives_ev):
    """估值结果、参考方案与评价参数从不进观察；正常的观察（含持有物与培训）也不会被误判成评价对象。"""
    r2 = lives_ev[U63][1]
    s = r2.next_state
    p = bundle_ev.protocol
    obs = build_observation(
        permission=standard_product_permission("t"),
        profile=next(x for x in bundle_ev.stated_profiles if x.person_id == "u63"),
        state=s,
        catalog=catalog_of(bundle_ev, s),
        last_feedback=visible_feedback(r2.settlement),
        resource_rules=p.resource_rules,
        energy_feedback=p.energy_feedback,
        career_rules=p.career_rules,
        savings_rules=p.savings_rules,
        health_rules=p.health_rules,
        health_input=p.health_input,
    )
    payload = observation_payload(obs)
    keys: set[str] = set()

    def collect(o):
        if isinstance(o, dict):
            keys.update(k for k in o if isinstance(k, str))
            for v in o.values():
                collect(v)
        elif isinstance(o, list):
            for v in o:
                collect(v)

    collect(json.loads(json.dumps(payload)))
    assert not keys & RESERVED_EVALUATION_KEYS


def test_catalog_facts_cannot_carry_evaluation_parameters(bundle_ev):
    catalog = bundle_ev.catalog_for("US:SYNTH2026:A93")
    o = catalog.opportunity("a93.service.gym")
    leaked = revise(o, spec={**o.spec, "v_reference": 0.65})
    assert any("v_reference" in m for m in internal_leak_problems("测试", leaked))


# ---------------------------------------------------------------------- 已知反例（展示，不隐藏）


def test_transport_health_counterexample_is_shown_not_hidden(
    bundle_ev, scoring_ev, evaluation_ev, lives_ev
):
    """u70 与 u71 只看重交通（commute 90、健康风险应对 10，后者不进满足感）。u70 第 1 年第 5 月大病病假：那个月不用到岗，
    广义交通分从 60/(60+70) 升到 60/(60+0.25×70)，比健康项扣掉的 γ = 0.2 还多——生了一场大病，实际分数反而更高。
    这是 E1-5A 广义交通与 E1-5C 健康项相加的已知反例：本轮不调疾病惩罚、不改交通权重，在报告里原样展示。"""
    sick = evaluate(bundle_ev, scoring_ev, evaluation_ev, U70, lives_ev[U70])
    well = evaluate(bundle_ev, scoring_ev, evaluation_ev, U71, lives_ev[U71])
    s_well, s_sick = "0.5692307692307692", "0.6193548387096774"
    assert [m.score_decimal for m in well.actual.months] == [s_well] * 36
    assert sick.actual.months[4].score_decimal == s_sick
    assert f(s_sick) > f(s_well)
    assert x(sick.actual.l_actual) == (35 * f(s_well) + f(s_sick)) / 36
    assert x(sick.actual.l_actual) > x(well.actual.l_actual)
    assert x(sick.combined.j) > x(well.combined.j)
    # 期末现金：u70 少了一个月工资又付了住院费（54,320 vs 59,320），但较舒适的方案两人都付得起，V 相同
    assert x(sick.valuation.v_reference) == x(well.valuation.v_reference)
