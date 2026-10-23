"""规划观察、提示模板与 mock 正确读取六维产品画像；不泄露评分参数或隐藏风险关系；模板与 mock 按版本号登记。"""

from __future__ import annotations

import contextlib
import json

import pytest

from agentlayer.life_mock import PRODUCT_MOCK_IDENTITY, ProductMockPlanner, mock_planner_for
from agentlayer.life_planning import (
    PlannerCall,
    action_guide,
    build_memory,
    build_request,
    render_prompt,
    request_payload,
)
from agentlayer.life_views import ObservationPermission, assert_payload_clean, build_observation
from common.life_contracts import LifeContractError
from ledger.life_run_config import run_config_problems
from orchestration.life_runner import _forbidden_texts
from product import scenarios as sc
from product.fixtures import K51


def permission(**kw) -> ObservationPermission:
    base = dict(
        condition="product_stated_profile",
        show_stated_dimension_points=True,
        show_hard_requirements=True,
        show_free_text=True,
        show_energy_level=True,
        show_stated_material_points=True,
        permission_version="e3a-product-v1",
        show_health_answers=True,
    )
    base.update(kw)
    return ObservationPermission(**base)


@pytest.fixture(scope="module")
def ready(tmp_path_factory):
    s = sc.preflight_only(tmp_path_factory.mktemp("ready"), "ready")
    assert s.pre.preflight.status == "ready"
    return s


def _request(s, perm=None, template="e3a-product-prompt-v2", profile=None):
    w = s.pre.world
    with contextlib.chdir(s.work):
        catalog = w.bundle.catalog_for(K51)
        state = w.bundle.initial_state("u501.k51.r0")
        return build_request(
            request_id="req/1",
            attempt=1,
            attempts_allowed=3,
            permission=perm or w.permission,
            profile=profile or w.bundle.product_profiles[0],
            state=state,
            catalog=catalog,
            protocol=w.bundle.protocol,
            facts=w.facts.sheet_for(K51),
            memory=build_memory(clone_id=state.clone_id, city=K51, current_year=1, years=[]),
            last_feedback=None,
            revision=None,
            tax_policy=w.bundle.tax_policy(catalog.tax_policy_id),
            prompt_template=template,
        )


def test_observation_carries_exact_six_points_material_allocation_and_health_answers(ready):
    payload = request_payload(_request(ready))
    profile = payload["observation"]["profile"]
    assert profile["statement_kind"] == "product_six_dimension_v1"
    assert profile["six_dimension_points"] == {
        "material": "30",
        "safety": "20",
        "opportunity": "20",
        "leisure": "10",
        "environment": "10",
        "commute": "10",
    }
    assert "healthcare" not in json.dumps(profile["six_dimension_points"])
    # 只为旧七维画像存在的键都不在请求里
    for old in ("dimension_points", "soft_preferences", "material_points", "healthcare_meaning"):
        assert old not in profile
    assert "skills" not in payload["observation"]
    assert payload["planning_version"] == "product-planning-v1"
    assert profile["material_allocation"] == {
        "housing_space": "25",
        "food_life": "25",
        "daily_convenience": "30",
        "personal_enjoyment": "20",
    }
    states = [h["answer_state"] for h in profile["health_answers"]]
    assert states == ["provided", "provided", "unsure"]
    assert "没有这一项的明确指令" in profile["health_answers"][2]["planning_note"]
    assert payload["guide"]["version"] == "e3a-product-action-guide-v2"
    topics = [s["topic"] for s in payload["guide"]["sections"]]
    assert topics[0].startswith("你的用户陈述") and "日常杂务：设备与人工服务（可选）" in topics


def test_health_answer_visibility_has_no_default(ready):
    hidden = request_payload(_request(ready, perm=permission(show_health_answers=False)))[
        "observation"
    ]["profile"]
    assert "health_answers" not in hidden and hidden["not_permitted"] == [
        "health_answers"
    ]  # 显式写明“没有许可”，不是“没有回答”
    shown = request_payload(_request(ready))["observation"]["profile"]
    assert shown["not_permitted"] == []
    with pytest.raises(ValueError, match="show_health_answers"):
        permission(show_health_answers=None)
    with pytest.raises(ValueError, match="condition"):
        permission(condition="research_text_only")


def test_the_request_does_not_leak_scoring_parameters_truth_or_hidden_risk(ready):
    request = _request(ready)
    assert_payload_clean(request)
    text = json.dumps(request_payload(request), ensure_ascii=False)
    w = ready.pre.world
    for forbidden in _forbidden_texts(w.bundle, w.scoring, w.specs):
        assert forbidden not in text
    for word in (
        "tau",
        "gamma",
        "state_values",
        "dimension_weights",
        "material_subweights",
        "risk_cells",
        "p_major",
        "confirmed_profile_fingerprint",
    ):
        assert f'"{word}"' not in text, word


def test_observation_only_accepts_the_product_profile(ready):
    w = ready.pre.world
    state = w.bundle.initial_state("u501.k51.r0")
    p = w.bundle.protocol
    with contextlib.chdir(ready.work), pytest.raises(LifeContractError, match="profile 必须是"):
        build_observation(
            permission=w.permission,
            profile=state,  # type: ignore[arg-type]
            state=state,
            catalog=w.bundle.catalog_for(K51),
            resource_rules=p.resource_rules,
            energy_feedback=p.energy_feedback,
            career_rules=p.career_rules,
            savings_rules=p.savings_rules,
            health_rules=p.health_rules,
            health_input=p.health_input,
        )


def test_prompt_templates_are_registered_by_version(ready):
    w = ready.pre.world
    assert run_config_problems(w.config, w.roster_config, w.bundle, w.facts) == []
    with pytest.raises(ValueError, match="不认识的提示模板"):
        type(w.config).model_validate(
            {**w.config.model_dump(mode="json"), "prompt_template": "e1-convenience-prompt-v1"}
        )
    with pytest.raises(LifeContractError, match="不认识的行动说明书版本"):
        action_guide(w.bundle.protocol, "e1-convenience-action-guide-v1")


def _plan(planner, s, perm=None):
    request = _request(s, perm=perm)
    payload = request_payload(request)
    system, user = render_prompt(payload)
    return json.loads(planner.plan(PlannerCall("req/1", system, user, payload)))


def test_product_mock_reads_new_fields_and_uses_a_fixed_documented_emergency_policy(
    ready, tmp_path
):
    out = _plan(ProductMockPlanner(), ready)
    assert (
        out["action"]["emergency"]["minor"]
        == out["action"]["emergency"]["major"]
        == "treat_if_possible"
    )
    assert "fixed_treat_if_possible_test_policy_v1" in out["need_summary"]
    assert (
        "material 30" in out["need_summary"]
        and "健康问卷有 1 题没有明确倾向" in out["need_summary"]
    )
    assert any(
        i["opportunity_id"] == "k51.durable.convenience_pack" for i in out["action"]["items"]
    )  # M5c 读 daily_convenience
    # 健康回答全部“不符合我”也不会变成不治疗：mock 的应急政策不是从健康回答推断的
    disagree = sc.preflight_only(
        tmp_path / "disagree",
        "disagree",
        health_choices={
            "hc_more_pay_more_sick_risk": "disagree",
            "hc_reserve_for_health_costs": "disagree",
            "hc_wait_for_clear_risk_info": "disagree",
        },
    )
    out2 = _plan(ProductMockPlanner(), disagree)
    assert out2["action"]["emergency"]["minor"] == "treat_if_possible"
    # 物质与发展都是 0：不为多挣钱换岗、不报课；便利 0：不买设备或服务
    lazy = sc.preflight_only(
        tmp_path / "lazy",
        "lazy",
        six={
            "material": "0",
            "safety": "40",
            "opportunity": "0",
            "leisure": "30",
            "environment": "20",
            "commute": "10",
        },
        material_mode=None,
    )
    out3 = _plan(ProductMockPlanner(), lazy)
    assert "物质与发展点数都是 0" in out3["rationale"]
    assert not any(
        "convenience" in i["opportunity_id"] or "chores" in i["opportunity_id"]
        for i in out3["action"]["items"]
    )


def test_mock_identity_is_versioned():
    from common.life_planning_contracts import PlannerIdentity, PlannerSpec

    assert PRODUCT_MOCK_IDENTITY.rules_version == "e3a-product-mock-rules-v1"
    assert type(mock_planner_for(PlannerSpec(identity=PRODUCT_MOCK_IDENTITY))) is ProductMockPlanner
    old = PlannerIdentity(
        kind="mock",
        planner_id="mock.rules.e1-7a",
        version="e1-7a-mock-v1",
        rules_version="e1-7a-mock-rules-v1",
        note="旧规则",
    )
    with pytest.raises(ValueError, match="不认识的 mock 身份"):
        mock_planner_for(PlannerSpec(identity=old))
