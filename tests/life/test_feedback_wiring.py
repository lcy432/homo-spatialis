"""E1-4B 的接线、对照、兼容、加载检查与信息边界（`DECISIONS/0021`；E1-4B prompt 第八节 10–14）。"""

from __future__ import annotations

import json
from fractions import Fraction

import pytest

from agentlayer.life_views import (
    ObservationPermission,
    assert_payload_clean,
    build_observation,
    observation_payload,
    standard_product_permission,
    visible_feedback,
)
from common.life_aggregation_contracts import internal_aggregation_models
from common.life_contracts import LifeContractError
from common.life_evaluation_contracts import internal_evaluation_models
from common.life_feedback_contracts import internal_feedback_models
from common.life_scoring_contracts import internal_models, internal_scoring_models
from ledger.life_catalog import cross_validate, parse_bundle

from .feedback_support import (
    ALPHA0,
    ALPHA005,
    OFF,
    as_e1_4a,
    bound_year,
    profile_of,
    run_years,
    settle_chain,
    variant,
)
from .settle_support import bundle_from, catalog_of, state_of

F = Fraction  # 手算用的分数简写

#: 关闭反馈与 E1-4A 比较时**预先声明**允许不同的字段（其余逐项精确核对）：
#:   outcome.notes            结算说明里关于反馈的一句（"尚未实现" ↔ "由年度交接在评分之后计算"）
#:   next_state.energy_quota_basis  E1-4B 才有的额度依据
ALLOWED_OUTCOME_DIFF = {"notes"}
ALLOWED_STATE_DIFF = {"energy_quota_basis"}


def _strip(dump: dict, keys: set[str]) -> dict:
    return {k: v for k, v in dump.items() if k not in keys}


# --------------------------------------------------------------------------
# 10：关闭反馈与 α = 0 不依赖评分，业务结果复现 E1-4A r2
# --------------------------------------------------------------------------


@pytest.mark.parametrize("arm", [OFF, ALPHA0], ids=["disabled", "alpha0"])
@pytest.mark.parametrize("clone", ["u31.a61.c0", "u33.a61.c0", "u34.a61.c0", "u31.a61.down"])
def test_the_off_arm_reproduces_e1_4a(raw4b, bundle4b, scoring4b, arm, clone):
    off = variant(bundle4b.protocol, **arm)
    b_runs = run_years(bundle4b, scoring4b, clone, protocol=off, with_scoring=False)
    a_runs = settle_chain(as_e1_4a(raw4b), clone)
    assert [r.settlement.status for r in b_runs] == [r.status for r in a_runs]
    for b, a in zip(b_runs, a_runs, strict=True):
        assert [p.model_dump() for p in b.settlement.problems] == [
            p.model_dump() for p in a.problems
        ]
        if a.outcome is None:
            continue
        assert _strip(b.settlement.outcome.model_dump(), ALLOWED_OUTCOME_DIFF) == _strip(
            a.outcome.model_dump(), ALLOWED_OUTCOME_DIFF
        )
        nxt = b.next_state if b.next_state is not None else b.settlement.next_state
        assert _strip(nxt.model_dump(), ALLOWED_STATE_DIFF) == _strip(
            a.next_state.model_dump(), ALLOWED_STATE_DIFF
        )
        if b.handoff == "next_year_ready":
            assert b.next_state.energy.level == b.next_state.energy_quota_basis.base_level


def test_the_off_arm_runs_without_any_scoring(bundle4b, scoring4b):
    off = variant(bundle4b.protocol, **OFF)
    (r1, _r2) = run_years(bundle4b, scoring4b, "u31.a61.c0", protocol=off, with_scoring=False)
    assert (r1.handoff, r1.score, r1.feedback.decision) == ("next_year_ready", None, "off_disabled")
    assert r1.next_state.energy_quota_basis.status == "feedback_off"


def test_an_enabled_arm_without_scoring_is_blocked_not_guessed(bundle4b, scoring4b):
    (r1,) = run_years(bundle4b, scoring4b, "u31.a61.c0", with_scoring=False)
    assert (r1.handoff, r1.feedback.decision) == ("feedback_blocked", "blocked")
    assert r1.settlement.status == "completed"


# --------------------------------------------------------------------------
# 开关对照与 α 敏感性：第一年相同，第二年额度不同，255 负担的计划一边可行一边不可行
# --------------------------------------------------------------------------


def test_on_off_contrast_from_the_same_start(bundle4b, scoring4b):
    on = run_years(bundle4b, scoring4b, "u31.a61.c0")
    off = run_years(bundle4b, scoring4b, "u31.a61.c0", protocol=variant(bundle4b.protocol, **OFF))
    # 第 1 年生活结果相同（结算说明一样，只差额度依据）
    assert on[0].settlement.outcome.model_dump() == off[0].settlement.outcome.model_dump()
    # 第 2 年额度开始不同
    assert (on[0].next_state.energy.level, off[0].next_state.energy.level) == (264.01, 250)
    # 第 2 年负担 255：开启可行，关闭不可行；关闭的分支停在这里，不补出之后的生活
    assert on[1].settlement.status == "completed"
    assert off[1].settlement.status == "plan_infeasible"
    assert off[1].settlement.problems[0].code == "energy_over_limit"
    assert (len(on), len(off)) == (3, 2)


def test_alpha_sensitivity_zero_five_ten(bundle4b, scoring4b):
    got = {}
    for name, arm in (("0", ALPHA0), ("0.05", ALPHA005), ("0.10", {})):
        protocol = variant(bundle4b.protocol, **arm) if arm else bundle4b.protocol
        rs = run_years(bundle4b, scoring4b, "u31.a61.c0", protocol=protocol)
        got[name] = (rs[0].next_state.energy.level, rs[1].settlement.status)
    assert got == {
        "0": (250, "plan_infeasible"),
        "0.05": (257.0, "completed"),  # 250 × (1 + 0.05 × 0.5604…) = 257.005… → 257.00
        "0.10": (264.01, "completed"),
    }


def test_a_boosted_state_cannot_seed_the_off_arm(bundle4b, scoring4b):
    """不能把开启反馈产生的加成当作关闭对照的初始条件：换政策就拒绝。"""
    (r1, *_) = run_years(bundle4b, scoring4b, "u31.a61.c0")
    off = variant(bundle4b.protocol, **OFF)
    rs = run_years(bundle4b, scoring4b, "u31.a61.c0", protocol=off, state=r1.next_state)
    assert rs[0].settlement.status == "input_error"
    assert rs[0].settlement.problems[0].code == "feedback_policy_switched"


# --------------------------------------------------------------------------
# 12：跨城基础一致，之后的当前额度可以不同
# --------------------------------------------------------------------------


def test_same_base_across_cities_then_different_quotas(bundle4b, scoring4b):
    (a,) = run_years(bundle4b, scoring4b, "u31.a61.c0")[:1]
    (b,) = run_years(bundle4b, scoring4b, "u31.b62.c0")
    bases = {
        state_of(bundle4b, c).energy_quota_basis.base_level for c in ("u31.a61.c0", "u31.b62.c0")
    }
    assert bases == {250}
    assert (a.next_state.energy.level, b.next_state.energy.level) == (264.01, 264.73)


def test_the_loader_still_requires_the_same_base_across_cities(raw4b):
    for s in raw4b["initial_states"]:
        if s["clone_id"] == "u31.b62.c0":
            s["energy"] = {"level": 260, "scale_version": "e1-4a-monthly-quota-v1"}
            s["energy_quota_basis"] = {"base_level": 260, "status": "base_first_year"}
    assert any("对照分身基础资源不同" in e for e in cross_validate(bundle_from(raw4b)).errors)


def test_a_first_year_state_must_start_from_its_base(raw4b):
    for s in raw4b["initial_states"]:
        if s["clone_id"] == "u31.a61.c0":
            s["energy"] = {"level": 264.01, "scale_version": "e1-4a-monthly-quota-v1"}
    with pytest.raises(LifeContractError, match="必须等于基础额度"):
        bundle_from(raw4b)


# --------------------------------------------------------------------------
# B 与 A 的协议身份、缺配置不猜默认
# --------------------------------------------------------------------------


def test_the_b_protocol_must_declare_its_feedback_policy(raw4b):
    raw4b["protocol"].pop("energy_feedback")
    with pytest.raises(LifeContractError, match="必须显式声明 energy_feedback"):
        parse_bundle(raw4b)


def test_an_a_protocol_cannot_carry_a_feedback_policy(raw4a, raw4b):
    raw4a["protocol"]["energy_feedback"] = raw4b["protocol"]["energy_feedback"]
    with pytest.raises(LifeContractError, match="不读满足感反馈政策"):
        parse_bundle(raw4a)


def test_b_states_need_a_quota_basis_and_a_states_cannot_have_one(raw4a, raw4b):
    raw4b["initial_states"][0].pop("energy_quota_basis")
    assert any("必须写精力额度依据" in e for e in cross_validate(bundle_from(raw4b)).errors)
    raw4a["initial_states"][0]["energy_quota_basis"] = {
        "base_level": 250,
        "status": "base_first_year",
    }
    assert any("不读精力额度依据" in e for e in cross_validate(bundle_from(raw4a)).errors)


def test_the_b_fixture_is_settlement_ready_but_not_scoring_ready(bundle4b):
    report = cross_validate(bundle4b)
    assert report.settlement_ready and not report.scoring_ready and not report.errors
    policy = bundle4b.protocol.math_policies["energy_feedback"]
    assert policy.state == "undecided" and policy.open_parts


# --------------------------------------------------------------------------
# 13：信息边界
# --------------------------------------------------------------------------


def _observe(bundle, scoring, *, permission=None, policy=None, year=2):
    rs = run_years(bundle, scoring, "u31.a61.c0")
    r = rs[year - 2]
    state = r.next_state
    return build_observation(
        permission=permission
        or standard_product_permission(bundle.protocol.info_permission_version),
        profile=profile_of(bundle, "u31"),
        state=state,
        catalog=catalog_of(bundle, state),
        last_feedback=visible_feedback(r.settlement),
        resource_rules=bundle.protocol.resource_rules,
        energy_feedback=policy or bundle.protocol.energy_feedback,
    )


def test_the_product_clone_sees_its_current_quota_but_no_score(bundle4b, scoring4b):
    obs = _observe(bundle4b, scoring4b)
    payload = observation_payload(obs)
    assert payload["energy_level"] == 264.01
    assert payload["resources"]["monthly_energy_quota"]["exact"] == "26401/100"
    assert payload["resources"]["energy_unit"] == "burden_point"
    assert "下一年的额度由环境根据这一年的实际生活确定" in payload["resources"]["energy_rule"]
    assert payload["channels"] == {
        "energy_visible": True,
        "satisfaction_feedback": "quota_feedback",
        "study_condition": "dynamic_life_feedback",
    }
    text = json.dumps(payload, ensure_ascii=False)
    for absent in (
        "annual_mean",
        "0.5604",
        "base_level",
        "alpha",
        "record_fingerprint",
        "score_decimal",
    ):
        assert absent not in text


def test_hiding_energy_does_not_turn_a_feedback_run_static(bundle4b, scoring4b):
    hidden = ObservationPermission(
        **{**standard_product_permission("x").model_dump(), "show_energy_level": False}
    )
    obs = _observe(bundle4b, scoring4b, permission=hidden)
    assert obs.energy_level is None and obs.resources.monthly_energy_quota is None
    assert obs.channels.study_condition == "dynamic_life_feedback"  # 可行性变化仍会透露评价
    assert obs.dynamic_feedback_channel is False  # 旧标识只表示精力是否可见


def test_text_understanding_studies_must_run_without_feedback(bundle4b, scoring4b):
    research = ObservationPermission(
        condition="research_text_only",
        show_stated_dimension_points=False,
        show_soft_preferences=False,
        show_hard_requirements=True,
        show_free_text=True,
        show_energy_level=False,
        show_stated_material_points=False,
        permission_version="x",
    )
    with pytest.raises(LifeContractError, match="research_text_only"):
        _observe(bundle4b, scoring4b, permission=research)
    off = variant(bundle4b.protocol, **OFF)
    rs = run_years(bundle4b, scoring4b, "u31.a61.c0", protocol=off)
    state = rs[0].next_state
    obs = build_observation(
        permission=research,
        profile=profile_of(bundle4b, "u31"),
        state=state,
        catalog=catalog_of(bundle4b, state),
        resource_rules=bundle4b.protocol.resource_rules,
        energy_feedback=off.energy_feedback,
    )
    assert obs.channels.study_condition == "static_text_understanding"
    assert obs.channels.satisfaction_feedback == "none"
    assert "额度固定为你的基础额度" in obs.resources.energy_rule


def test_no_observation_for_a_pending_candidate(bundle4b, scoring4b):
    (r1,) = run_years(bundle4b, scoring4b, "u33.a61.c0")
    cand = r1.settlement.next_state
    with pytest.raises(LifeContractError, match="还没确定"):
        build_observation(
            permission=standard_product_permission("x"),
            profile=profile_of(bundle4b, "u33"),
            state=cand,
            catalog=catalog_of(bundle4b, cand),
            resource_rules=bundle4b.protocol.resource_rules,
            energy_feedback=bundle4b.protocol.energy_feedback,
        )


def test_a_b_state_needs_its_policy_to_be_observed(bundle4b):
    s = state_of(bundle4b, "u31.a61.c0")
    with pytest.raises(LifeContractError, match="反馈政策"):
        build_observation(
            permission=standard_product_permission("x"),
            profile=profile_of(bundle4b, "u31"),
            state=s,
            catalog=catalog_of(bundle4b, s),
            resource_rules=bundle4b.protocol.resource_rules,
        )


def test_feedback_internals_and_their_dumps_are_refused(bundle4b, scoring4b):
    (r1, *_) = run_years(bundle4b, scoring4b, "u31.a61.c0")
    year = bound_year(bundle4b, scoring4b, "u31.a61.c0")  # r2 的年度绑定也是内部对象
    objects = [bundle4b.protocol.energy_feedback, r1.feedback, r1, year]
    assert {type(o).__name__ for o in objects} == {m.__name__ for m in internal_feedback_models()}
    # E1-6A（DECISIONS/0028）起合成清单里还有评价层、E1-6B（DECISIONS/0029）起还有汇总层的内部对象；评分与反馈两部分照旧原样在里面
    assert set(internal_models()) == set(internal_scoring_models()) | set(
        internal_feedback_models()
    ) | set(internal_evaluation_models()) | set(internal_aggregation_models())
    for obj in objects:
        with pytest.raises(LifeContractError, match=f"^{type(obj).__name__} 出现在"):
            assert_payload_clean({"notes": [{"deep": [obj]}]})
        with pytest.raises(LifeContractError, match=r"内部键|原样序列化的内部评分对象"):
            assert_payload_clean({"notes": [{"nested": obj.model_dump(mode="json")}]})


def test_feedback_does_not_change_the_score_formula(bundle4b, scoring4b):
    """评分只读实际结果：第 2 年（额度 264.01）与第 1 年（额度 250）同样的生活，评分逐月相同。"""
    r1, r2, _ = run_years(bundle4b, scoring4b, "u31.a61.c0")
    assert [m.u for m in r1.score.months] == [m.u for m in r2.score.months]
