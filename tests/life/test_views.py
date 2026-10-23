"""信息边界：代理能看到什么（`DECISIONS/0016` 第三节；产品观察的许可见 ADR0020 第十二节）。

主机制是白名单构造，扫描只是第二道防线。所以这里两类测试都要有：正常路径里评分内部量根本进不来；以及有人硬塞进来时，
扫描能在嵌套深处拦住。样例是产品协议样例（u301，K51 / M52）。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agentlayer.life_views import (
    AgentObservation,
    ObservationPermission,
    assert_payload_clean,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import InternalYearScore, LifeContractError
from econ.life_scoring import preference_from_confirmed_profile

from .conftest import CLONE_K, K51


def _perm(bundle, **change) -> ObservationPermission:
    base = standard_product_permission(bundle.protocol.info_permission_version)
    return base.model_copy(update=change) if change else base


def _obs(bundle, *, permission=None, profile=None, state=None, catalog=None, last_feedback=None):
    p = bundle.protocol
    state = state or bundle.initial_state(CLONE_K)
    return build_observation(
        permission=permission or _perm(bundle),
        profile=profile or bundle.profile_for(state.person_id),
        state=state,
        catalog=catalog or bundle.catalog_for(str(state.city)),
        last_feedback=last_feedback,
        resource_rules=p.resource_rules,
        energy_feedback=p.energy_feedback,
        career_rules=p.career_rules,
        savings_rules=p.savings_rules,
        health_rules=p.health_rules,
        health_input=p.health_input,
    )


# --------------------------------------------------------------------------
# 许可必须显式
# --------------------------------------------------------------------------


def test_a_permission_has_no_defaults():
    """没声明许可就不能构造观察，更不会默认落到最宽松的模式。"""
    with pytest.raises(ValidationError) as exc:
        ObservationPermission()  # type: ignore[call-arg]
    missing = {e["loc"][0] for e in exc.value.errors() if e["type"] == "missing"}
    assert {
        "condition",
        "show_stated_dimension_points",
        "show_energy_level",
        "show_health_answers",
    } <= missing


def test_build_observation_requires_a_permission(bundle):
    state = bundle.initial_state(CLONE_K)
    with pytest.raises(TypeError):
        build_observation(  # type: ignore[call-arg]
            profile=bundle.profile_for(state.person_id),
            state=state,
            catalog=bundle.catalog_for(K51),
        )


def test_showing_energy_marks_a_dynamic_feedback_channel(bundle):
    """可见精力携带间接偏好反馈；这种观察必须标出来，不能与静态条件混用。"""
    quiet = _obs(bundle, permission=_perm(bundle, show_energy_level=False))
    loud = _obs(bundle, permission=_perm(bundle, show_energy_level=True))
    assert quiet.energy_level is None and quiet.dynamic_feedback_channel is False
    assert loud.energy_level is not None and loud.dynamic_feedback_channel is True


# --------------------------------------------------------------------------
# 正常路径：评分内部量根本进不来
# --------------------------------------------------------------------------


def test_the_stated_profile_is_shown_but_no_scoring_weight_is(bundle):
    """产品模式显示用户确认的陈述（六维点数、物质分配、健康回答）；评分用的权重、γ 与评分规格不出现。"""
    profile = bundle.profile_for("u301")
    payload = observation_payload(_obs(bundle))
    shown = payload["profile"]
    assert shown["six_dimension_points"]["material"] == "45"
    assert shown["material_allocation"]["daily_convenience"] == "30"
    assert len(shown["health_answers"]) == len(profile.health_answers)
    blob = json.dumps(payload, ensure_ascii=False)
    for key in (
        "dimension_weights",
        "material_subweights",
        "effective_weights",
        "gamma",
        "scoring_content_fingerprint",
        "confirmed_profile_fingerprint",
        "synthetic_fixture_only",
    ):
        assert f'"{key}"' not in blob
    preference = preference_from_confirmed_profile(profile)
    assert_payload_clean(payload)
    with pytest.raises(LifeContractError):
        assert_payload_clean({"profile": {"deep": [preference]}})


def test_the_payload_is_built_from_a_whitelist_not_from_the_state(bundle):
    """顶层键是事先固定的一组（AgentObservation 的字段），不随 PersonYearState 增加字段而增加。"""
    payload = observation_payload(_obs(bundle, permission=_perm(bundle, show_energy_level=True)))
    assert set(payload) <= set(AgentObservation.model_fields)
    for always in ("profile", "opportunities", "resources", "employment", "savings", "health"):
        assert always in payload
    first = payload["opportunities"][0]
    assert "source" not in json.dumps(first) and "targets" not in first


def test_hidden_profile_parts_are_none_and_listed_as_not_permitted(bundle):
    obs = _obs(
        bundle,
        permission=_perm(
            bundle,
            show_hard_requirements=False,
            show_free_text=False,
            show_health_answers=False,
        ),
    )
    assert obs.profile.hard_requirements is None and obs.profile.free_text is None
    assert obs.profile.health_answers is None
    assert {"hard_requirements", "free_text", "health_answers"} <= set(obs.profile.not_permitted)
    assert "health_answers" not in observation_payload(obs)["profile"]


# --------------------------------------------------------------------------
# 有人硬塞：类型检查与扫描在嵌套深处拦住
# --------------------------------------------------------------------------


def test_the_old_persona_is_refused_as_a_profile(bundle):
    """产品允许看问卷，不等于放行整个旧人设。"""
    from worlds.personas import reference_persona

    with pytest.raises(LifeContractError, match="profile"):
        _obs(bundle, profile=reference_persona())  # type: ignore[arg-type]


def test_a_scoring_preference_is_refused_as_a_profile(bundle):
    """评分用的偏好对象（由确认画像换算出的权重）不是画像，不能当画像传进来。"""
    preference = preference_from_confirmed_profile(bundle.profile_for("u301"))
    with pytest.raises(LifeContractError, match="profile"):
        _obs(bundle, profile=preference)  # type: ignore[arg-type]


def test_an_internal_score_cannot_be_passed_as_feedback(bundle):
    score = InternalYearScore(
        person_id="u301",
        clone_id=CLONE_K,
        year_index=1,
        protocol_id=bundle.protocol.protocol_id,
        scoring_scale_version="undefined",
        status="synthetic_fixture_only",
        values={"leisure": 0.4},
    )
    with pytest.raises(LifeContractError, match="last_feedback"):
        _obs(bundle, last_feedback=score)  # type: ignore[arg-type]


def test_a_smuggled_answer_object_is_caught_however_deep(bundle, scoring):
    """绕过构造函数、把答案对象埋进嵌套结构，扫描也要拦住。"""
    from econ.types import PersonaParams

    for obj in (
        InternalYearScore(
            person_id="u301",
            clone_id="c",
            year_index=1,
            protocol_id="p",
            scoring_scale_version="v",
            status="synthetic_fixture_only",
            values={},
        ),
        PersonaParams(alpha=0.3, beta=0.2, theta={"safety": 0.5}),
        scoring.spec,
        bundle.protocol.health_rules,
        bundle.initial_state(CLONE_K),
    ):
        with pytest.raises(LifeContractError):
            assert_payload_clean({"opportunities": [{"spec": {"deep": [obj]}}]})


def test_a_forbidden_key_is_caught_inside_a_dumped_payload(bundle):
    payload = observation_payload(_obs(bundle))
    payload["opportunities"][0]["spec"] = {"utility": 1.2}
    with pytest.raises(LifeContractError, match="utility"):
        assert_payload_clean(payload)


def test_a_catalog_spec_cannot_carry_a_nested_object(bundle):
    """规格只许原始值，嵌套对象进不了目录——这挡掉了一整类藏匿通道。"""
    from common.life_contracts import LifeOpportunity

    o = bundle.catalog_for(K51).opportunity("k51.food.basic").model_dump(mode="json")
    o["spec"] = {"hidden": {"dimension_weights": {"material": 1.0}}}
    with pytest.raises(ValidationError):
        LifeOpportunity.model_validate(o)


def test_only_an_agent_observation_can_be_serialised(bundle):
    with pytest.raises(LifeContractError, match="AgentObservation"):
        observation_payload(bundle.initial_state(CLONE_K))  # type: ignore[arg-type]


def test_visible_feedback_is_facts_only_and_passes_the_scan(bundle, first_year):
    """上一年的可见反馈只有事实（余额、执行了哪些月、职业、储蓄、健康），没有满足感或分数。"""
    fb = visible_feedback(first_year.settlement)
    obs = _obs(bundle, state=first_year.next_state, last_feedback=fb)
    payload = observation_payload(obs)
    shown = payload["last_feedback"]
    assert shown["year_index"] == 1 and {"career", "savings", "health"} <= set(shown)
    blob = json.dumps(shown, ensure_ascii=False)
    for key in ('"u"', '"score"', '"satisfaction"', '"annual_mean"', '"weights"'):
        assert key not in blob


def test_the_unknown_item_stays_unknown_in_what_the_agent_sees(bundle):
    """可见层也不能把未知补成零价。"""
    cat = bundle.catalog_for(K51)
    pack = cat.opportunity("k51.durable.convenience_pack")
    unknown = pack.model_copy(
        update={
            "availability": "unknown",
            "price": None,
            "unknown_reason": "合成情景：这座城的设备包报价没有采集到",
        }
    )
    variant = cat.model_copy(
        update={
            "opportunities": tuple(
                unknown if o.opportunity_id == pack.opportunity_id else o for o in cat.opportunities
            )
        }
    )
    payload = observation_payload(_obs(bundle, catalog=variant))
    seen = next(o for o in payload["opportunities"] if o["opportunity_id"] == pack.opportunity_id)
    assert seen["availability"] == "unknown" and seen.get("price") is None
