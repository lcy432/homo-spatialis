"""E1-4A：新旧协议的兼容、加载检查与可见投影（`DECISIONS/0020` 第九、十节；验收 15）。

旧协议照旧（年度时间检查、精力只记录、没有月度资源账）；新协议缺资料不能通过；公开的资源事实
可见，精力按原有许可，内部的资源规则与资源账不能整体进 payload。
"""

from __future__ import annotations

import json
from fractions import Fraction

import pytest
from pydantic import ValidationError

from agentlayer.life_views import (
    ObservationPermission,
    assert_payload_clean,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import LifeContractError
from ledger.life_catalog import cross_validate

from .conftest import catalog, opportunity
from .settle_support import bundle_from, catalog_of, revise, settle, state_of

F = Fraction  # 手算用的分数简写

PERMIT = dict(
    condition="product_stated_profile",
    show_stated_dimension_points=True,
    show_soft_preferences=True,
    show_hard_requirements=True,
    show_free_text=True,
    show_energy_level=True,
    show_stated_material_points=True,
    permission_version="e1-4a-draft",
)


def _profile(bundle, person_id):
    return next(p for p in bundle.stated_profiles if p.person_id == person_id)


def _obs(bundle, clone_id, *, feedback=None, **perm):
    s = state_of(bundle, clone_id)
    return build_observation(
        permission=ObservationPermission(**{**PERMIT, **perm}),
        profile=_profile(bundle, s.person_id),
        state=s,
        catalog=catalog_of(bundle, s),
        last_feedback=feedback,
        resource_rules=bundle.protocol.resource_rules,
    )


def _errors(raw) -> str:
    return "\n".join(cross_validate(bundle_from(raw)).errors)


# --------------------------------------------------------------------------
# 旧协议照旧
# --------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["bundle2", "bundle3", "bundle3s"])
def test_old_protocols_keep_annual_time_and_recorded_energy(which, request):
    b = request.getfixturevalue(which)
    assert b.protocol.resource_rules is None and not b.protocol.monthly_resources
    done = 0
    for plan in b.plans:
        if plan.year_index != 1:
            continue
        r = settle(b, plan.clone_id)
        if r.outcome is None:
            continue
        assert r.outcome.energy_feasibility == "not_implemented"
        assert r.outcome.resources is None and r.outcome.time_used_hours is not None
        assert r.next_state.resource_budget_basis is None
        done += 1
    assert done


def test_an_old_protocol_refuses_resource_facts_instead_of_ignoring_them(raw2):
    """旧规则不读资源事实；悄悄忽略会让人以为已经约束了。加载与结算两处都拦。"""
    catalog(raw2, "A11")["opportunities"][0]["resource"] = {"basis": "passive", "note": "测试"}
    assert "不读资源事实" in _errors(raw2)
    r = settle(bundle_from(raw2), "u01.a11.c0")
    assert r.status == "data_error" and r.outcome is None
    assert any("不读资源事实" in p.message for p in r.problems)


def test_an_old_protocol_state_cannot_carry_a_monthly_quota(bundle2, bundle4a):
    s = state_of(bundle2, "u01.a11.c0")
    quota = state_of(bundle4a, "u21.a51.near")
    moved = revise(s, energy=quota.energy, resource_budget_basis=quota.resource_budget_basis)
    r = settle(bundle2, "u01.a11.c0", state=moved)
    assert r.status == "data_error" and "不读月度资源预算" in r.problems[0].message


# --------------------------------------------------------------------------
# 新协议：加载检查
# --------------------------------------------------------------------------


def test_the_new_fixture_cross_validates_cleanly(bundle4a):
    r = cross_validate(bundle4a)
    assert r.contract_valid and not r.errors


def test_the_same_person_keeps_the_same_budget_and_quota_across_cities(raw4a):
    for s in raw4a["initial_states"]:
        if s["clone_id"] == "u21.b52.remote":
            s["time_budget_hours"] = 4000
    assert "对照分身基础资源不同" in _errors(raw4a)


def test_a_city_cannot_hand_the_same_person_a_different_quota(raw4a):
    for s in raw4a["initial_states"]:
        if s["clone_id"] == "u21.b52.hybrid":
            s["energy"] = {"level": 300, "scale_version": "e1-4a-monthly-quota-v1"}
    assert "对照分身基础资源不同" in _errors(raw4a)


@pytest.mark.parametrize(
    ("oid", "change", "needle"),
    [
        ("a51.food.fresh", {"resource": {"basis": "passive", "note": "测试"}}, "计量依据只能是"),
        (
            "a51.job.office",
            {"arrangement_options": {"onsite_days_per_month": [0, 20]}},
            "不能列可选安排",
        ),
        ("a51.experience.concert", {"energy_burden": 1.0}, "不再使用"),
        (
            "a51.housing.near",
            {
                "spec": {
                    "floor_area_sqft": 600,
                    "rooms": 1,
                    "exclusive": True,
                    "basic_facilities": True,
                    "home_office": False,
                }
            },
            "往返分钟",
        ),
        (
            "a51.experience.concert",
            {
                "resource": {
                    "basis": "per_occurrence",
                    "hours": 4,
                    "burden_per_hour": 0.25,
                    "quantity_scales": True,
                    "note": "测试",
                    "substitutes": [{"task": "cleaning", "residual_hours_per_month": 1}],
                }
            },
            "不能替代家务",
        ),
    ],
)
def test_the_loader_rejects_incomplete_or_contradictory_resource_facts(raw4a, oid, change, needle):
    opportunity(raw4a, oid).update(change)
    assert needle in _errors(raw4a)


def test_a_missing_resource_fact_is_not_a_default_zero(raw4a):
    opportunity(raw4a, "a51.service.volunteering").pop("resource")
    assert "缺资源事实" in _errors(raw4a)


# --------------------------------------------------------------------------
# 可见投影：公开事实可见，精力按许可，内部对象被拦
# --------------------------------------------------------------------------


def test_plans_can_see_the_public_resource_facts(bundle4a):
    obs = _obs(bundle4a, "u21.a51.near")
    res = obs.resources
    assert res is not None
    assert (res.monthly_time_budget.fraction(), res.monthly_energy_quota.fraction()) == (365, 250)
    assert "每天 8 小时睡眠" in res.reserved and res.commute_burden_per_hour == 0.75
    assert [t.task for t in res.household_tasks] == ["laundry", "cleaning"]
    by_id = {o.opportunity_id: o for o in obs.opportunities}
    gym = by_id["a51.service.gym_plan"].time_energy
    assert gym is not None and (gym.basis, gym.uses_per_month, gym.hours) == ("per_use", 8, 1.5)
    job = by_id["a51.job.hybrid"].time_energy
    assert job is not None and job.job is not None and job.job.onsite_days_per_month == 8
    washer = by_id["a51.durable.washer"].time_energy
    assert washer is not None and washer.substitutes[0].residual_hours_per_month == 3
    assert by_id["a51.housing.far"].spec["commute_roundtrip_minutes"] == 70


def test_the_energy_quota_follows_the_existing_permission(bundle4a):
    hidden = _obs(bundle4a, "u21.a51.near", show_energy_level=False)
    assert hidden.energy_level is None and hidden.resources.monthly_energy_quota is None
    assert hidden.dynamic_feedback_channel is False
    shown = _obs(bundle4a, "u21.a51.near")
    assert shown.energy_level == 250 and shown.dynamic_feedback_channel is True
    # 时间预算与公开的负担系数照常可见
    assert hidden.resources.monthly_time_budget.fraction() == 365


def test_feedback_reports_each_month_and_hides_energy_without_permission(bundle4a):
    r = settle(bundle4a, "u21.a51.far")
    fb = visible_feedback(r)
    assert len(fb.resources) == 12
    m1 = fb.resources[0]
    assert (m1.resource_check, m1.time_used.fraction(), m1.energy_used.fraction()) == (
        "complete",
        F(745, 3),
        217,
    )
    assert m1.hours_by_category["commute"].fraction() == F(70, 3)
    assert (m1.housing_opportunity_id, m1.commute_roundtrip_minutes, m1.onsite_visits) == (
        "a51.housing.far",
        70,
        20,
    )
    hidden = _obs(bundle4a, "u21.a51.far", feedback=fb, show_energy_level=False)
    for m in hidden.last_feedback.resources:
        assert m.energy_used is None and m.energy_remaining is None and m.burden_by_category is None
        assert m.time_used is not None
    shown = _obs(bundle4a, "u21.a51.far", feedback=fb)
    assert shown.last_feedback.resources[0].energy_used.fraction() == 217


def test_feedback_for_an_exit_year_shows_the_exit_month_and_nothing_after(bundle4a):
    fb = visible_feedback(settle(bundle4a, "u25.a51.c0"))
    m4 = fb.resources[3]
    assert (m4.resource_check, m4.commute_status) == ("known_lower_bound", "unknown_no_housing")
    assert {k: v.fraction() for k, v in m4.hours_by_category.items()} == {"work": 80}
    # 已知下限可见，但没有"剩余"——完整用量未知
    assert (m4.time_used.fraction(), m4.time_remaining) == (80, None)
    assert {m.resource_check for m in fb.resources[4:]} == {"not_reached"}


def test_an_infeasible_trial_has_no_feedback_to_give(bundle4a):
    with pytest.raises(LifeContractError, match="没有提交任何账目"):
        visible_feedback(settle(bundle4a, "u22.a51.c0"))


def test_the_resource_rules_must_come_with_a_monthly_state_and_only_then(bundle4a, bundle2):
    s = state_of(bundle4a, "u21.a51.near")
    with pytest.raises(LifeContractError, match="必须同时给协议的资源规则"):
        build_observation(
            permission=ObservationPermission(**PERMIT),
            profile=_profile(bundle4a, "u21"),
            state=s,
            catalog=catalog_of(bundle4a, s),
        )
    old = state_of(bundle2, "u01.a11.c0")
    with pytest.raises(LifeContractError, match="必须同时给协议的资源规则"):
        build_observation(
            permission=ObservationPermission(**PERMIT),
            profile=_profile(bundle2, "u01"),
            state=old,
            catalog=catalog_of(bundle2, old),
            resource_rules=bundle4a.protocol.resource_rules,
        )


def test_internal_resource_objects_cannot_ride_along_whole(bundle4a):
    r = settle(bundle4a, "u21.a51.near")
    rules = bundle4a.protocol.resource_rules
    for smuggled in (rules, r.outcome.resources, r.outcome.resources.months[0], r.outcome):
        with pytest.raises(LifeContractError, match="出现在可见字段"):
            assert_payload_clean({"note": [smuggled]})


def test_the_payload_has_resource_facts_but_no_truth_or_scores(bundle4a):
    fb = visible_feedback(settle(bundle4a, "u21.a51.near"))
    payload = observation_payload(_obs(bundle4a, "u21.a51.near", feedback=fb))
    text = json.dumps(payload, ensure_ascii=False)
    for absent in (
        "dimension_weights",
        "material_subweights",
        "internal",
        "scoring_version",
        "tau",
    ):
        assert absent not in text
    assert payload["resources"]["monthly_time_budget"]["exact"] == "365"
    assert payload["last_feedback"]["resources"][0]["time_used"]["exact"] == "235"


# --------------------------------------------------------------------------
# 标准产品观察：分身有足够的规划信息（r2，所有者 2026-10-08 裁决）
# --------------------------------------------------------------------------


def _product_payload(bundle, clone_id="u21.a51.near", permission=None):
    """第一年过完后，第二年做计划时代理拿到的 payload（真实构造入口，不是手写字典）。"""
    r = settle(bundle, clone_id)
    nxt = r.next_state
    permission = permission or standard_product_permission(bundle.protocol.info_permission_version)
    obs = build_observation(
        permission=permission,
        profile=_profile(bundle, nxt.person_id),
        state=nxt,
        catalog=catalog_of(bundle, nxt),
        last_feedback=visible_feedback(r),
        resource_rules=bundle.protocol.resource_rules,
    )
    return observation_payload(obs)


def test_the_standard_product_permission_shows_the_quota_explicitly(bundle4a):
    perm = standard_product_permission("e1-4a-draft")
    assert perm.condition == "product_stated_profile" and perm.show_energy_level is True
    # 预设是显式的一组选择，不是 ObservationPermission 的默认值（它仍然没有默认值）
    with pytest.raises(ValidationError):
        ObservationPermission(condition="product_stated_profile", permission_version="x")


def test_a_standard_product_payload_has_what_a_monthly_plan_needs(bundle4a):
    payload = _product_payload(bundle4a)
    res = payload["resources"]
    assert res["monthly_time_budget"]["exact"] == "365"
    assert (res["monthly_energy_quota"]["exact"], res["energy_unit"]) == ("250", "burden_point")
    assert payload["energy_level"] == 250 and payload["dynamic_feedback_channel"] is True
    assert "月底作废" in res["time_rule"] and "不结转" in res["time_rule"]
    assert "每个岗位每次到岗算一趟完整往返" in res["commute_rule"]
    assert "min(" in res["household_rule"] and "不是机器运行时间" in res["household_rule"]
    assert "不结转" in res["energy_rule"] and res["rules_revision"] == "r2-2026-10-08"
    costs = {o["opportunity_id"]: o["time_energy"] for o in payload["opportunities"]}
    assert costs["a51.job.office"]["job"]["onsite_days_per_month"] == 20
    assert (
        costs["a51.service.gym_plan"]["uses_per_month"],
        costs["a51.service.gym_plan"]["hours"],
    ) == (8, 1.5)
    m1 = payload["last_feedback"]["resources"][0]
    assert (m1["time_used"]["exact"], m1["time_remaining"]["exact"]) == ("235", "130")
    assert (m1["energy_used"]["exact"], m1["burden_by_category"]["work"]["exact"]) == ("207", "160")


def test_a_research_control_can_still_hide_energy_explicitly(bundle4a):
    """研究对照显式关掉精力：额度与负担汇总为空；但每小时负担是公开成本，仍可见——不是"精力完全不可知"。"""
    hidden = ObservationPermission(**{**PERMIT, "show_energy_level": False})
    payload = _product_payload(bundle4a, permission=hidden)
    assert payload["energy_level"] is None and payload["resources"]["monthly_energy_quota"] is None
    assert payload["dynamic_feedback_channel"] is False
    for m in payload["last_feedback"]["resources"]:
        assert m["energy_used"] is None and m["burden_by_category"] is None
    costs = {o["opportunity_id"]: o["time_energy"] for o in payload["opportunities"]}
    assert costs["a51.job.office"]["job"]["burden_per_work_hour"] == 1.0  # 仍可推算负担


def test_the_product_payload_keeps_truth_scores_and_internal_objects_out(bundle4a):
    text = json.dumps(_product_payload(bundle4a), ensure_ascii=False)
    for absent in ("dimension_weights", "material_subweights", "scoring_version", "annual_mean"):
        assert absent not in text
    assert "contributions" not in text and "within_limits" not in text  # 整月资源账没有整体进来
