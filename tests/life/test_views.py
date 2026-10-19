"""信息边界：代理能看到什么（E1-1，`DECISIONS/0016` 第三节）。

主机制是白名单构造，扫描只是第二道防线。所以这里两类测试都要有：正常路径里真值
根本进不来；以及有人硬塞进来时，扫描能在嵌套深处拦住。
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agentlayer.life_views import (
    CAREER_OBSERVATION_FIELDS,
    HEALTH_OBSERVATION_FIELDS,
    SAVINGS_OBSERVATION_FIELDS,
    AgentObservation,
    ObservationPermission,
    assert_payload_clean,
    build_observation,
    observation_payload,
)
from common.life_contracts import (
    InternalYearScore,
    LifeContractError,
    VisibleFeedback,
)

PRODUCT = dict(
    condition="product_stated_profile",
    show_stated_dimension_points=True,
    show_soft_preferences=True,
    show_hard_requirements=True,
    show_free_text=True,
    show_energy_level=False,
    show_stated_material_points=True,
    permission_version="e1-1-draft",
)
RESEARCH = dict(
    PRODUCT,
    condition="research_text_only",
    show_stated_dimension_points=False,
    show_soft_preferences=False,
    show_stated_material_points=False,
)


def _inputs(bundle, clone_id):
    state = next(s for s in bundle.initial_states if s.clone_id == clone_id)
    profile = next(p for p in bundle.stated_profiles if p.person_id == state.person_id)
    truth = next(t for t in bundle.internal_truths if t.person_id == state.person_id)
    return profile, state, bundle.catalog_for(str(state.city)), truth


def _obs(bundle, clone_id="u02.b02.c0", **perm):
    profile, state, cat, _ = _inputs(bundle, clone_id)
    return build_observation(
        permission=ObservationPermission(**{**PRODUCT, **perm}),
        profile=profile,
        state=state,
        catalog=cat,
    )


# --------------------------------------------------------------------------
# 许可必须显式
# --------------------------------------------------------------------------


def test_a_permission_has_no_defaults():
    """没声明许可就不能构造观察，更不会默认落到最宽松的模式。"""
    with pytest.raises(ValidationError) as exc:
        ObservationPermission()  # type: ignore[call-arg]
    missing = {e["loc"][0] for e in exc.value.errors() if e["type"] == "missing"}
    assert {"condition", "show_stated_dimension_points", "show_energy_level"} <= missing


def test_build_observation_requires_a_permission(bundle):
    profile, state, cat, _ = _inputs(bundle, "u01.a01.c0")
    with pytest.raises(TypeError):
        build_observation(profile=profile, state=state, catalog=cat)  # type: ignore[call-arg]


def test_research_text_only_cannot_show_stated_scores():
    """那正是被研究的东西——给了分数，"从文字理解偏好"就不成立了。"""
    with pytest.raises(ValidationError, match="research_text_only"):
        ObservationPermission(**{**RESEARCH, "show_stated_dimension_points": True})


def test_the_two_conditions_are_labelled_differently(bundle):
    a = _obs(bundle)
    b = _obs(bundle, **RESEARCH)
    assert a.information_condition != b.information_condition
    assert b.profile.dimension_points is None and b.profile.soft_preferences is None
    assert b.profile.free_text is not None, "文字条件下文字照常给"


def test_showing_energy_marks_a_dynamic_feedback_channel(bundle):
    """可见精力将来会携带间接偏好反馈；这种观察必须标出来，不能与静态条件混用。"""
    quiet = _obs(bundle)
    loud = _obs(bundle, show_energy_level=True)
    assert quiet.energy_level is None and quiet.dynamic_feedback_channel is False
    assert loud.energy_level is not None and loud.dynamic_feedback_channel is True


# --------------------------------------------------------------------------
# 正常路径：真值根本进不来
# --------------------------------------------------------------------------


def test_internal_weights_never_reach_the_payload_even_in_product_mode(bundle):
    """u02 的真值（material 0.28 / healthcare 0.17）与陈述（25 / 20）故意不同。

    产品模式显示陈述是允许的；真值的数字与键名都不能出现。
    """
    _, _, _, truth = _inputs(bundle, "u02.b02.c0")
    payload = observation_payload(_obs(bundle))
    blob = json.dumps(payload, ensure_ascii=False)
    assert payload["profile"]["dimension_points"]["material"] == 25
    for key in ("dimension_weights", "material_subweights", "synthetic_fixture_only"):
        assert key not in blob
    assert "0.28" not in blob and "0.17" not in blob
    for need, w in truth.material_subweights.items():
        assert f'"{need}": {w}' not in blob


def test_the_payload_is_built_from_a_whitelist_not_from_the_state(bundle):
    """顶层键是固定的一组，不随 PersonYearState 增加字段而增加。

    2026-10-08 E1-5A 接口适配：E1-5A 的三个顶层字段（能力、岗位、学习规则）只在新协议出现，旧协议的观察
    不序列化它们；所以旧协议的白名单 = 全部字段减去 ``CAREER_OBSERVATION_FIELDS``。新协议的白名单等于
    全部字段，见 ``test_career_wiring.py``。原意（顶层键是事先固定的白名单）不变。

    2026-10-09 E1-5B 接口适配：目标储蓄的顶层字段 ``savings`` 只在 E1-5B 协议出现，同样从旧协议的白名单里减去
    （``SAVINGS_OBSERVATION_FIELDS``）。

    2026-10-11 E1-5C 接口适配：健康的顶层字段 ``health`` 只在 E1-5C 协议出现，同样从旧协议的白名单里减去
    （``HEALTH_OBSERVATION_FIELDS``）。原意（顶层键是事先固定的白名单）不变。
    """
    payload = observation_payload(_obs(bundle))
    assert (
        set(payload)
        == set(AgentObservation.model_fields)
        - CAREER_OBSERVATION_FIELDS
        - SAVINGS_OBSERVATION_FIELDS
        - HEALTH_OBSERVATION_FIELDS
    )
    assert "source" not in json.dumps(payload["opportunities"][0])
    assert "targets" not in payload["opportunities"][0]


def test_hidden_profile_parts_are_none_not_merely_omitted(bundle):
    b = _obs(bundle, show_hard_requirements=False, show_free_text=False)
    assert b.profile.hard_requirements is None and b.profile.free_text is None


# --------------------------------------------------------------------------
# 有人硬塞：类型检查与扫描在嵌套深处拦住
# --------------------------------------------------------------------------


def test_the_old_persona_is_refused_as_a_profile(bundle):
    """产品允许看问卷，不等于放行整个旧人设。"""
    from worlds.personas import reference_persona

    _, state, cat, _ = _inputs(bundle, "u01.a01.c0")
    with pytest.raises(LifeContractError, match="profile"):
        build_observation(
            permission=ObservationPermission(**PRODUCT),
            profile=reference_persona(),
            state=state,
            catalog=cat,
        )  # type: ignore[arg-type]


def test_the_internal_truth_is_refused_as_a_profile(bundle):
    _, state, cat, truth = _inputs(bundle, "u01.a01.c0")
    with pytest.raises(LifeContractError, match="profile"):
        build_observation(
            permission=ObservationPermission(**PRODUCT), profile=truth, state=state, catalog=cat
        )  # type: ignore[arg-type]


def test_an_internal_score_cannot_be_passed_as_feedback(bundle):
    profile, state, cat, _ = _inputs(bundle, "u01.a01.c0")
    score = InternalYearScore(
        person_id="u01",
        clone_id="u01.a01.c0",
        year_index=1,
        protocol_id="life-v1-contract",
        scoring_scale_version="undefined",
        status="synthetic_fixture_only",
        values={"leisure": 0.4},
    )
    with pytest.raises(LifeContractError, match="last_feedback"):
        build_observation(
            permission=ObservationPermission(**PRODUCT),
            profile=profile,
            state=state,
            catalog=cat,
            last_feedback=score,
        )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "smuggled", ["internal_truth", "internal_score", "old_ground_truth_params"]
)
def test_a_smuggled_answer_object_is_caught_however_deep(bundle, smuggled):
    """绕过构造函数、把答案对象埋进嵌套结构，扫描也要拦住。"""
    from econ.types import PersonaParams

    _, _, _, truth = _inputs(bundle, "u01.a01.c0")
    obj = {
        "internal_truth": truth,
        "internal_score": InternalYearScore(
            person_id="u01",
            clone_id="c",
            year_index=1,
            protocol_id="p",
            scoring_scale_version="v",
            status="synthetic_fixture_only",
            values={},
        ),
        "old_ground_truth_params": PersonaParams(alpha=0.3, beta=0.2, theta={"safety": 0.5}),
    }[smuggled]
    nested = {"opportunities": [{"spec": {"deep": [obj]}}]}
    with pytest.raises(LifeContractError):
        assert_payload_clean(nested)


def test_a_forbidden_key_is_caught_inside_a_dumped_payload(bundle):
    payload = observation_payload(_obs(bundle))
    payload["opportunities"][0]["spec"] = {"utility": 1.2}
    with pytest.raises(LifeContractError, match="utility"):
        assert_payload_clean(payload)


def test_a_catalog_spec_cannot_carry_a_nested_object():
    """规格只许原始值，嵌套对象进不了目录——这挡掉了一整类藏匿通道。"""
    from common.life_contracts import LifeOpportunity

    with pytest.raises(ValidationError):
        LifeOpportunity.model_validate(
            {
                "opportunity_id": "x",
                "category": "food",
                "label": "l",
                "unit": "u",
                "quality": "q",
                "availability": "unknown",
                "duration": {"kind": "ongoing_service"},
                "spec": {"hidden": {"dimension_weights": {"material": 1.0}}},
                "source": {
                    "kind": "synthetic",
                    "observed_on": "2026-10-05",
                    "geography_scope": "s",
                    "mapping_version": "m",
                    "synthetic_note": "n",
                },
            }
        )


def test_only_an_agent_observation_can_be_serialised(bundle):
    _, state, _, _ = _inputs(bundle, "u01.a01.c0")
    with pytest.raises(LifeContractError, match="AgentObservation"):
        observation_payload(state)  # type: ignore[arg-type]


def test_visible_feedback_is_accepted_and_kept_separate(bundle):
    profile, state, cat, _ = _inputs(bundle, "u01.a01.c0")
    fb = VisibleFeedback(year_index=1, cash_balance=state.cash, notes=("房租按时付清",))
    obs = build_observation(
        permission=ObservationPermission(**PRODUCT),
        profile=profile,
        state=state,
        catalog=cat,
        last_feedback=fb,
    )
    assert observation_payload(obs)["last_feedback"]["notes"] == ["房租按时付清"]


def test_the_unknown_item_stays_unknown_in_what_the_agent_sees(bundle):
    """可见层也不能把未知补成零价。"""
    payload = observation_payload(_obs(bundle, clone_id="u01.b02.c0"))
    washer = next(
        o for o in payload["opportunities"] if o["opportunity_id"] == "b02.durable.washer"
    )
    assert washer["availability"] == "unknown" and washer["price"] is None
