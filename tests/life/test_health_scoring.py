"""E1-5C：健康评分版本、健康输入的含义与信息边界（`DECISIONS/0027`；prompt 第九节第 1–4、15、17 项）。

新评分 S = (1 − γ)·B + γ·H(月初状态)；B 是有效六维权重加权的日常生活分。核对：城市医疗条件不进满足感；只改健康输入
分数不变；同等病情的直接扣分人人相同、付医疗费不加分；六维换算可以手算、缺数据不重新归一化；代理看得到病情与规则，
看不到概率表与评分参数；没有疾病时账目与 E1-5B 的同一份生活逐项相同。
"""

from __future__ import annotations

import copy
import json
import math
from fractions import Fraction

import pytest

from agentlayer.life_views import (
    FORBIDDEN_PAYLOAD_KEYS,
    AgentObservation,
    ObservationPermission,
    assert_payload_clean,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import (
    RESERVED_HEALTH_KEYS,
    LifeContractError,
)
from common.life_scoring_contracts import LifeScoringSpec, PreferenceInsufficient
from econ.life_coverage import coverage_report
from econ.life_scoring import effective_weights, preference_from_stated_profile, score_year
from econ.life_settlement import settle_year
from ledger.life_catalog import cross_validate
from ledger.life_scoring_config import condition_problems

from .health_support import (
    FOOD,
    HOME,
    SHOP,
    plan,
    run_clone,
    source,
    with_source,
    year,
)
from .settle_support import bundle_from, catalog_of, revise, state_of, tax_of

SIX = ("material", "safety", "opportunity", "leisure", "environment", "commute")


def _profile(bundle, person, **points):
    p = next(x for x in bundle.stated_profiles if x.person_id == person)
    return revise(p, dimension_points={**p.dimension_points, **points})


def _score(bundle, config, r, profile=None, spec=None, conditions=None):
    """对一份第 1 年的结算结果重新评分（年初状态就是样例的初始状态）。"""
    state = bundle.initial_state(r.clone_id)
    assert state is not None and r.year_index == 1
    return score_year(
        protocol=bundle.protocol,
        spec=spec or config.spec,
        conditions=conditions or config.conditions_for(str(state.city)),
        catalog=catalog_of(bundle, state),
        state=state,
        outcome=r.settlement.outcome,
        preference=preference_from_stated_profile(
            profile or next(p for p in bundle.stated_profiles if p.person_id == state.person_id)
        ),
    )


# ==========================================================================
# 第 1 项：健康月份里，医疗价格、可得性与旧的医疗背景都不改变满足感
# ==========================================================================


def test_healthy_months_ignore_medical_prices_availability_and_background(bundle_hl, scoring_hl):
    proto = with_source(bundle_hl.protocol, source("none"))
    clone = "u91.a91.treat"
    start = state_of(bundle_hl, clone)
    cat = catalog_of(bundle_hl, start)
    action = bundle_hl.plans_for(clone)[0]
    clinic = cat.treatment("minor")

    def variant(**changes):
        offers = [revise(clinic, **changes) if t.severity == "minor" else t for t in cat.treatments]
        return revise(cat, treatments=offers)

    cats = [
        cat,
        variant(
            price={
                **clinic.price.model_dump(mode="json"),
                "amount": {"amount": 9999, "numeraire": "USD_2024_LOCAL"},
            }
        ),
        variant(availability="unavailable", price=None, visit_hours=None, burden_per_hour=None),
        variant(
            availability="unknown",
            price=None,
            visit_hours=None,
            burden_per_hour=None,
            unknown_reason="测试",
        ),
    ]
    from econ.life_year import run_life_year

    from .health_support import inputs

    results = [
        run_life_year(
            protocol=proto,
            state=start,
            action=action,
            catalog=c,
            tax=tax_of(bundle_hl, c),
            scoring=inputs(bundle_hl, scoring_hl, start),
        )
        for c in cats
    ]
    base = results[0]
    for r in results[1:]:
        assert r.settlement.outcome.model_dump_json(
            exclude={"health"}
        ) == base.settlement.outcome.model_dump_json(exclude={"health"})
        assert [m.u for m in r.score.months] == [m.u for m in base.score.months]
    # 新评分规格里没有医疗分项；城市条件也不能再挂医疗背景
    assert "healthcare" not in {c.dimension for c in scoring_hl.spec.components}
    cond = scoring_hl.conditions_for(str(start.city))
    extra = revise(
        cond,
        components=(
            *cond.components,
            {
                "component_id": "healthcare",
                "status": "known",
                "base": 4,
                "cap": 0.9,
                "source": cond.components[0].source.model_dump(mode="json"),
            },
        ),
    )
    assert any("规格没有的分项 healthcare" in e for e in condition_problems(scoring_hl.spec, extra))
    raw = scoring_hl.spec.model_dump(mode="json")
    raw["components"].append(
        {
            "component_id": "healthcare",
            "dimension": "healthcare",
            "unit": "指数（合成）",
            "tau": 4,
            "description": "旧的医疗背景",
        }
    )
    with pytest.raises(ValueError, match="没有医疗分项"):
        LifeScoringSpec.model_validate(raw)


# ==========================================================================
# 第 2 项：只改健康输入——病痛、概率与现金都不变
# ==========================================================================


def test_changing_only_the_health_input_changes_neither_pain_probability_nor_cash(
    bundle_hl, scoring_hl
):
    clone = "u91.a91.treat"
    start = state_of(bundle_hl, clone)
    action = bundle_hl.plans_for(clone)[0]
    low = next(p for p in bundle_hl.stated_profiles if p.person_id == "u91")
    # 六维比例不变（30:10:20:10:10:10 → 15:5:10:5:5:5），只把健康风险应对倾向从 10 点调到 55 点
    high = _profile(
        bundle_hl,
        "u91",
        material=15,
        safety=5,
        healthcare=55,
        opportunity=10,
        leisure=5,
        environment=5,
        commute=5,
    )
    hot = revise(
        bundle_hl.protocol,
        illness_events=source("random"),
        health_rules=revise(
            bundle_hl.protocol.health_rules,
            risk=revise(
                bundle_hl.protocol.health_rules.risk,
                cells=[
                    {**c.model_dump(mode="json"), "minor": 0.3, "major": 0.1}
                    for c in bundle_hl.protocol.health_rules.risk.cells
                ],
            ),
        ),
    )
    for proto in (bundle_hl.protocol, hot):
        a = year(bundle_hl, scoring_hl, start, action, protocol=proto, profile=low)
        b = year(bundle_hl, scoring_hl, start, action, protocol=proto, profile=high)
        assert (
            a.settlement.model_dump_json() == b.settlement.model_dump_json()
        )  # 现金、概率、事件与病情都相同
        assert [m.u for m in a.score.months] == [m.u for m in b.score.months]  # 分数逐位相同
        assert [m.health for m in a.score.months] == [m.health for m in b.score.months]
        assert a.next_state.energy.level == b.next_state.energy.level
    wa = a.score.health_weighting
    wb = b.score.health_weighting
    assert wa.effective_weights == wb.effective_weights
    assert (wa.raw_dimension_weights["healthcare"], wb.raw_dimension_weights["healthcare"]) == (
        0.1,
        0.55,
    )


# ==========================================================================
# 第 3 项：同等病情的直接扣分人人相同；付医疗费不加分
# ==========================================================================


def test_the_same_illness_costs_everyone_the_same_and_paying_adds_nothing(bundle_hl, scoring_hl):
    (treated,) = run_clone(bundle_hl, scoring_hl, "u91.a91.treat")
    (declined,) = run_clone(bundle_hl, scoring_hl, "u92.a91.decline")
    t3, d3 = treated.score.months[2], declined.score.months[2]
    assert t3.health.state == d3.health.state == "minor"
    assert t3.health.deduction == d3.health.deduction == 0.1  # γ × (1 − H(小病)) = 0.2 × 0.5
    assert t3.u == d3.u  # 付了 300 治疗、没付，这个月分数相同：付款本身不加分
    assert (
        t3.daily_life == treated.score.months[1].daily_life
    )  # 治疗那个月的日常生活分与健康月份相同
    assert (
        treated.score.months[3].u > declined.score.months[3].u
    )  # 差别只在之后：治好了的第 4 月是健康月
    # 六维权重很不一样的人：同一个小病月的直接扣分仍然是 γ × (1 − 0.5) = 0.1
    other = _profile(
        bundle_hl,
        "u91",
        material=10,
        safety=10,
        healthcare=10,
        opportunity=10,
        leisure=10,
        environment=10,
        commute=40,
    )
    s = _score(bundle_hl, scoring_hl, treated, profile=other)
    assert s.months[2].health.deduction == t3.health.deduction
    assert s.months[2].daily_life != t3.daily_life
    big = run_clone(bundle_hl, scoring_hl, "u94.a91.major")[0].score.months[4].health
    assert (big.state, big.value, big.deduction) == ("major", 0.0, 0.2)


# ==========================================================================
# 第 4 项：六维换算可以手算；只给医疗点数要补充；缺数据不重新归一化
# ==========================================================================


def test_the_six_weight_conversion_is_checkable(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u91.a91.treat")
    w = r.score.health_weighting
    want = {
        "material": Fraction(1, 3),
        "safety": Fraction(1, 9),
        "opportunity": Fraction(2, 9),
        "leisure": Fraction(1, 9),
        "environment": Fraction(1, 9),
        "commute": Fraction(1, 9),
    }
    assert w.effective_weights == {k: float(v) for k, v in want.items()}
    assert w.six_dimension_sum == 0.9 and w.healthcare_semantics == "health_risk_coping_v1"
    assert [d.dimension for d in r.score.months[0].dimensions] == list(SIX)  # 没有医疗维
    m = r.score.months[0]
    assert m.daily_life == math.fsum(d.contribution for d in m.dimensions)
    assert m.u == (1 - 0.2) * m.daily_life + 0.2 * 1.0
    only = _profile(
        bundle_hl,
        "u91",
        material=0,
        safety=0,
        healthcare=100,
        opportunity=0,
        leisure=0,
        environment=0,
        commute=0,
    )
    only = revise(only, material_points=None)
    ins = preference_from_stated_profile(only)
    assert isinstance(ins, PreferenceInsufficient) and ins.missing == ("six_dimension_points",)


def test_missing_data_blocks_scoring_and_is_not_reweighted(bundle_hl, scoring_hl):
    (r,) = run_clone(bundle_hl, scoring_hl, "u91.a91.treat")
    cond = scoring_hl.conditions_for("US:SYNTH2026:A91")
    unknown = revise(
        cond,
        components=[
            {
                **c.model_dump(mode="json"),
                "status": "unknown",
                "base": None,
                "cap": None,
                "unknown_reason": "测试：安全资料未知",
            }
            if c.component_id == "safety"
            else c.model_dump(mode="json")
            for c in cond.components
        ],
    )
    s = _score(bundle_hl, scoring_hl, r, conditions=unknown)
    assert s.annual_status == "incomplete_months" and s.annual_mean is None
    assert all(m.u is None and m.daily_life is None and m.health is not None for m in s.months)
    # 安全维 0 点：缺它的资料不阻断；其余五维的有效权重按原比例（不因缺失再改一次）
    zero = _profile(bundle_hl, "u91", safety=0, environment=20)
    s0 = _score(bundle_hl, scoring_hl, r, profile=zero, conditions=unknown)
    assert s0.annual_status == "complete"
    assert s0.health_weighting.effective_weights["safety"] == 0.0
    assert s0.health_weighting.effective_weights["environment"] == float(Fraction(20, 90))


# ==========================================================================
# 新评分进入内容身份与年度绑定；γ = 0 对照；旧画像被拒绝
# ==========================================================================


def _gamma_arm(bundle, config, gamma):
    raw = config.spec.model_dump(mode="json")
    raw["health"]["gamma"] = gamma
    spec = LifeScoringSpec.model_validate(raw)
    policy = revise(
        bundle.protocol.energy_feedback, scoring_content_fingerprint=spec.content_fingerprint
    )
    return revise(bundle.protocol, energy_feedback=policy), revise(config, spec=spec)


def test_gamma_zero_is_a_separate_arm_and_old_quotas_do_not_cross(bundle_hl, scoring_hl):
    proto0, cfg0 = _gamma_arm(bundle_hl, scoring_hl, 0.0)
    assert cfg0.spec.content_fingerprint != scoring_hl.spec.content_fingerprint
    clone = "u94.a91.major"
    start = state_of(bundle_hl, clone)
    action = bundle_hl.plans_for(clone)[0]
    r0 = year(bundle_hl, cfg0, start, action, protocol=proto0)
    assert all(m.u == m.daily_life for m in r0.score.months)  # γ = 0：S = B
    r = year(bundle_hl, scoring_hl, start, action)
    assert r.settlement.model_dump_json(exclude={"next_state"}) == r0.settlement.model_dump_json(
        exclude={"next_state"}
    )
    nxt = plan(bundle_hl, clone, 2, HOME, FOOD, SHOP)
    crossed = settle_year(
        protocol=proto0,
        state=r.next_state,
        action=nxt,
        catalog=catalog_of(bundle_hl, start),
        tax=tax_of(bundle_hl, catalog_of(bundle_hl, start)),
    )
    # γ = 0.2 臂算出的第 2 年额度不能拿到 γ = 0 臂里用：两臂都要从共同初始状态各自运行
    assert crossed.status == "input_error" and crossed.outcome is None
    assert "scoring_content_changed" in {x.code for x in crossed.problems}


def test_old_profiles_are_refused_and_new_ones_do_not_go_to_old_versions(
    raw_hl, bundle_hl, scoring_hl, bundle_sv, scoring_sv
):
    raw = copy.deepcopy(raw_hl)
    del raw["stated_profiles"][0]["healthcare_semantics"]
    errors = cross_validate(bundle_from(raw)).errors
    assert any("u91" in e and "旧含义" in e for e in errors)
    old = revise(
        next(p for p in bundle_hl.stated_profiles if p.person_id == "u91"),
        healthcare_semantics=None,
    )
    with pytest.raises(LifeContractError, match="不能悄悄换含义"):
        year(
            bundle_hl,
            scoring_hl,
            state_of(bundle_hl, "u91.a91.treat"),
            bundle_hl.plans_for("u91.a91.treat")[0],
            profile=old,
        )
    # 新含义的画像交给旧七维评分版本（E1-5B）：拒绝
    s = bundle_sv.initial_states[0]
    newer = revise(
        next(p for p in bundle_sv.stated_profiles if p.person_id == s.person_id),
        healthcare_semantics="health_risk_coping_v1",
    )
    from econ.life_year import ScoringInputs, run_life_year

    with pytest.raises(LifeContractError, match="换含义"):
        run_life_year(
            protocol=bundle_sv.protocol,
            state=s,
            action=bundle_sv.plans_for(s.clone_id)[0],
            catalog=catalog_of(bundle_sv, s),
            tax=tax_of(bundle_sv, catalog_of(bundle_sv, s)),
            scoring=ScoringInputs(
                spec=scoring_sv.spec,
                conditions=scoring_sv.conditions_for(str(s.city)),
                preference_input=newer,
            ),
        )
    pref = preference_from_stated_profile(newer)
    with pytest.raises(LifeContractError, match="不能悄悄换含义"):
        score_year(
            protocol=bundle_sv.protocol,
            spec=scoring_sv.spec,
            conditions=scoring_sv.conditions_for(str(s.city)),
            catalog=catalog_of(bundle_sv, s),
            state=s,
            outcome=settle_year(
                protocol=bundle_sv.protocol,
                state=s,
                action=bundle_sv.plans_for(s.clone_id)[0],
                catalog=catalog_of(bundle_sv, s),
                tax=tax_of(bundle_sv, catalog_of(bundle_sv, s)),
            ).outcome,
            preference=pref,
        )


# ==========================================================================
# 第 15 项：代理看得到病情与规则，看不到映射、参数与未来事件
# ==========================================================================


def _observation(bundle, config, permission=None):
    y1 = run_clone(bundle, config, "u97.a91.crossyear")[0]
    state = y1.next_state
    p = bundle.protocol
    return build_observation(
        permission=permission or standard_product_permission(p.info_permission_version),
        profile=next(x for x in bundle.stated_profiles if x.person_id == "u97"),
        state=state,
        catalog=catalog_of(bundle, state),
        last_feedback=visible_feedback(y1.settlement),
        resource_rules=p.resource_rules,
        energy_feedback=p.energy_feedback,
        career_rules=p.career_rules,
        savings_rules=p.savings_rules,
        health_rules=p.health_rules,
        health_input=p.health_input,
    )


def test_the_agent_sees_its_illness_and_the_rules_but_not_the_risk_mapping(bundle_hl, scoring_hl):
    obs = _observation(bundle_hl, scoring_hl)
    payload = observation_payload(obs)
    h = payload["health"]
    assert (h["state"], h["age_years"]) == ("major", 36)
    assert h["ongoing"]["natural_end"].startswith("不治疗的话，第 2 年第 2 月月末恢复")
    assert {t["severity"]: t["availability"] for t in h["treatments"]} == {
        "minor": "available",
        "major": "available",
    }
    assert {r["severity"]: r["months_if_untreated"] for r in h["severity_rules"]} == {
        "minor": 2,
        "major": 3,
    }
    assert payload["channels"]["health_risk_information"] == "default_no_risk_relation"
    assert "健康风险应对倾向" in payload["profile"]["healthcare_meaning"]
    months = payload["last_feedback"]["health"]["months"]
    assert months[11]["new_illness"] == "大病" and months[11]["sick_leave"] is True
    blob = json.dumps(payload, ensure_ascii=False)
    for hidden in (
        "under_40",
        "age_40_59",
        "moderate",
        "heavy",
        "p_minor",
        "uniform",
        "seed",
        "pairing_id",
        "injected",
        "gamma",
        "state_values",
        "daily_life",
        "first_month_burden_band",
    ):
        assert hidden not in blob, hidden

    def numbers(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from numbers(v)
        elif isinstance(x, list):
            for v in x:
                yield from numbers(v)
        elif isinstance(x, float):
            yield x

    # 概率表里只在那里出现的数字（0.002、0.004……）一个都不在可见数据里
    assert not {0.002, 0.004, 0.007, 0.008, 0.012, 0.045, 0.065} & set(numbers(payload))
    assert "第 3 年第 12 月" not in blob  # 未来的指定事件不预告


def test_research_condition_hides_the_points_and_their_meaning(bundle_hl, scoring_hl):
    research = ObservationPermission(
        condition="research_text_only",
        show_stated_dimension_points=False,
        show_soft_preferences=False,
        show_hard_requirements=True,
        show_free_text=True,
        show_energy_level=False,
        show_stated_material_points=False,
        permission_version="test",
    )
    off = revise(bundle_hl.protocol.energy_feedback, mode="disabled")
    b = revise(bundle_hl, protocol=revise(bundle_hl.protocol, energy_feedback=off))
    s = state_of(b, "u91.a91.treat")
    obs = build_observation(
        permission=research,
        profile=next(x for x in b.stated_profiles if x.person_id == "u91"),
        state=s,
        catalog=catalog_of(b, s),
        resource_rules=b.protocol.resource_rules,
        energy_feedback=b.protocol.energy_feedback,
        career_rules=b.protocol.career_rules,
        savings_rules=b.protocol.savings_rules,
        health_rules=b.protocol.health_rules,
        health_input=b.protocol.health_input,
    )
    assert obs.profile.dimension_points is None and obs.profile.healthcare_meaning is None
    assert obs.health is not None  # 当前病情与规则是生活事实，不是偏好


def test_health_internals_and_their_keys_never_reach_the_payload(bundle_hl, scoring_hl):
    assert RESERVED_HEALTH_KEYS <= FORBIDDEN_PAYLOAD_KEYS
    from pydantic import BaseModel

    import agentlayer.life_views as views

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

    walk(views.AgentObservation)
    walk(views.VisibleFeedback)
    assert not RESERVED_HEALTH_KEYS & names
    (r,) = run_clone(bundle_hl, scoring_hl, "u91.a91.treat")
    out = r.settlement.outcome
    p = bundle_hl.protocol
    for obj in (
        p.health_rules,
        p.illness_events,
        p.health_input,
        out.health,
        out.health.months[2],
        r.next_state.health,
        catalog_of(bundle_hl, r.next_state).treatments[0],
    ):
        with pytest.raises(LifeContractError, match="出现在可见字段"):
            assert_payload_clean({"notes": [obj]})
    with pytest.raises(LifeContractError, match="内部键"):
        assert_payload_clean({"notes": [{"p_minor": 0.02}]})
    assert set(AgentObservation.model_fields) >= {"health"}


def test_observation_needs_the_health_rules_for_a_health_state(bundle_hl):
    s = state_of(bundle_hl, "u91.a91.treat")
    p = bundle_hl.protocol
    with pytest.raises(LifeContractError, match="health_rules"):
        build_observation(
            permission=standard_product_permission("t"),
            profile=next(x for x in bundle_hl.stated_profiles if x.person_id == "u91"),
            state=s,
            catalog=catalog_of(bundle_hl, s),
            resource_rules=p.resource_rules,
            energy_feedback=p.energy_feedback,
            career_rules=p.career_rules,
            savings_rules=p.savings_rules,
        )


# ==========================================================================
# 覆盖报告：健康版本的叶子权重与健康项叶子
# ==========================================================================


def test_coverage_reports_six_leaves_and_a_common_health_leaf(bundle_hl, scoring_hl):
    pref = preference_from_stated_profile(
        next(p for p in bundle_hl.stated_profiles if p.person_id == "u91")
    )
    rep = coverage_report(
        spec=scoring_hl.spec,
        group=scoring_hl.comparison_group,
        conditions=scoring_hl.city_conditions,
        catalogs=bundle_hl.catalogs,
        preference=pref,
    )
    leaves = {x.leaf: x for x in rep.leaves}
    assert "healthcare" not in leaves and leaves["health_state"].weight == 0.2
    assert math.isclose(math.fsum(x.weight for x in rep.leaves), 1.0, rel_tol=0, abs_tol=1e-12)
    assert leaves["commute"].weight == pytest.approx(0.8 / 9, abs=1e-15)
    eff = effective_weights(scoring_hl.spec, pref)
    assert set(eff) == set(SIX)


# ==========================================================================
# 兼容：新协议的无病对照与 E1-5B 同一份生活的账目、活动与资源逐项相同
# ==========================================================================


def _strip_health(raw: dict) -> dict:
    raw = copy.deepcopy(raw)
    proto = raw["protocol"]
    for key in ("health_rules", "illness_events", "health_input"):
        proto.pop(key)
    proto["life_rules_version"] = "e1-5b-monthly-savings-v1"
    for cat in raw["catalogs"]:
        cat.pop("treatments")
    for s in raw["initial_states"]:
        s.pop("health")
    for p in raw["plans"]:
        p.pop("emergency")
    for p in raw["stated_profiles"] + raw["internal_truths"]:
        p.pop("healthcare_semantics")
    return raw


@pytest.mark.parametrize(
    "clone", ["u91.a91.treat", "u96.a91.study", "u97.a91.crossyear", "u99.a91.pause"]
)
def test_the_no_illness_control_keeps_the_event_free_ledger(raw_hl, bundle_hl, clone):
    old = bundle_from(_strip_health(raw_hl))
    none = with_source(bundle_hl.protocol, source("none"))
    s_new, s_old = state_of(bundle_hl, clone), state_of(old, clone)
    a_new = bundle_hl.plans_for(clone)[0]
    a_old = old.plans_for(clone)[0]
    cat_new, cat_old = catalog_of(bundle_hl, s_new), catalog_of(old, s_old)
    new = settle_year(
        protocol=none, state=s_new, action=a_new, catalog=cat_new, tax=tax_of(bundle_hl, cat_new)
    )
    ref = settle_year(
        protocol=old.protocol, state=s_old, action=a_old, catalog=cat_old, tax=tax_of(old, cat_old)
    )
    assert new.status == ref.status == "completed"
    assert new.outcome.model_dump_json(exclude={"health"}) == ref.outcome.model_dump_json()
    assert new.next_state.model_dump_json(exclude={"health"}) == ref.next_state.model_dump_json()
    assert all(x.state == "healthy" and x.draw is None for x in new.outcome.health.months)
