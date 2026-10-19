"""E1-5A 收尾：评分内容身份与跨年精力额度的绑定（`DECISIONS/0025`，年度交接修订 r3-2026-10-09）。

缺陷（独立复核 P1）：评分规格编号与版本不变、参数（η、ρ、D_ref……）改了，程序仍允许沿用旧参数算出的下一年额度。
修复：反馈政策声明评分规格的**内容**身份（``LifeScoringSpec.content_fingerprint``）；加载与年度交接核对实际规格就是
声明的那一套；额度依据记下它；下一年开始前（加载、结算前检查、观察共用 ``energy_quota_problems``）与当前政策声明比较。

数字：主臂 η = 12、ρ = 0.25、D_ref = 20，α = 0.10，基础额度 250。u61 学会计第 1 年 S = 0.549166…，
第 2 年额度 250 × (1 + 0.10 × 0.549166…) = 263.729… → 263.72；η = 0 时 S = 0.529166… → 263.22（复核报告的复现值）。
"""

from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from agentlayer.life_views import (
    build_observation,
    observation_payload,
    standard_product_permission,
)
from common.life_contracts import (
    FEEDBACK_HANDOFF_REVISION,
    SCORING_CONTENT_HANDOFF_REVISION,
    EnergyFeedbackPolicy,
    LifeContractError,
    PersonYearState,
    energy_quota_problems,
)
from common.life_feedback_contracts import EnergyFeedbackRecord, LifeYearResult, ScoredYear
from common.life_scoring_contracts import LifeScoringSpec, scoring_content_problems
from ledger.life_catalog import feedback_state_problems, load_life_bundle, parse_bundle
from ledger.life_scoring_config import parse_scoring_config, scoring_config_problems

from .career_support import run_clone, year
from .conftest import FIXTURE_GT
from .feedback_support import bound_year
from .settle_support import catalog_of, plan_of, revise, state_of

LEARN = "u61.a71.learn"
ONSITE = "u63.a71.onsite"


def _set(raw: dict, component: str, path: str, field: str, value) -> dict:
    data = copy.deepcopy(raw)
    next(c for c in data["spec"]["components"] if c["component_id"] == component)[path][field] = (
        value
    )
    return data


def _arm(bundle, scoring_raw: dict, **policy_changes):
    """另一个合法的实验身份：同编号同版本的规格换了参数，反馈政策**同时**声明它的内容身份。"""
    config = parse_scoring_config(scoring_raw)
    policy = revise(
        bundle.protocol.energy_feedback,
        scoring_content_fingerprint=config.spec.content_fingerprint,
        **policy_changes,
    )
    arm_bundle = revise(bundle, protocol=revise(bundle.protocol, energy_feedback=policy))
    errors, _ = scoring_config_problems(config, arm_bundle)
    assert errors == [], errors
    return arm_bundle, config


def _codes(result) -> set[str]:
    return {p.code for p in result.settlement.problems}


def _observe(bundle, state):
    return build_observation(
        permission=standard_product_permission("scoring-content-test"),
        profile=next(p for p in bundle.stated_profiles if p.person_id == state.person_id),
        state=state,
        catalog=catalog_of(bundle, state),
        resource_rules=bundle.protocol.resource_rules,
        energy_feedback=bundle.protocol.energy_feedback,
        career_rules=bundle.protocol.career_rules,
    )


# --------------------------------------------------------------------------
# 内容身份：参与与不参与的字段
# --------------------------------------------------------------------------


def _spec(scoring_raw: dict) -> LifeScoringSpec:
    """直接构造规格（不经加载器）：身份规则的测试不依赖协议里声明的那个值。"""
    return LifeScoringSpec.model_validate(scoring_raw["spec"])


def test_descriptions_and_key_order_keep_the_identity(scoring_gt_raw):
    main = _spec(scoring_gt_raw)
    fp = main.content_fingerprint
    data = copy.deepcopy(scoring_gt_raw)
    spec = data["spec"]
    spec["note"] = "只改规格说明"
    spec["omissions"] = []
    for c in spec["components"]:
        c["description"] = f"{c['component_id']} 的另一段说明"
        for s in c.get("sources", []):
            s["note"] = "来源的另一段说明"
        for mapping in ("growth", "transport", "landscape"):
            if c.get(mapping):
                c[mapping]["definition"] = "另一段定义文字"
    spec["components"] = [dict(reversed(list(c.items()))) for c in spec["components"]]
    data["spec"] = dict(reversed(list(spec.items())))
    described = LifeScoringSpec.model_validate(data["spec"])
    assert described.content_fingerprint == fp
    # 名字与引用不参与（另行核对）；整数写法与浮点写法相同
    assert revise(main, spec_id="score.renamed").content_fingerprint == fp
    eta12 = _set(scoring_gt_raw, "opportunity", "growth", "eta", 12)["spec"]
    assert LifeScoringSpec.model_validate(eta12).content_fingerprint == fp
    content = main.content()
    assert content["content_schema"] == "life-scoring-content-v1"
    blob = json.dumps(content, ensure_ascii=False)
    for absent in ('"spec_id"', '"protocol_id"', '"note"', '"description"', '"definition"'):
        assert absent not in blob, absent


@pytest.mark.parametrize(
    "component,path,field,value",
    [
        ("opportunity", "growth", "eta", 0.0),
        ("opportunity", "growth", "eta", 12.000001),  # 没有数值容差
        ("commute", "transport", "rho", 0.5),
        ("commute", "transport", "reference_visits", 40),
        ("commute", None, "tau", 61.0),
        ("opportunity", None, "tau", 3.0),
    ],
)
def test_every_parameter_change_is_another_identity(scoring_gt_raw, component, path, field, value):
    data = copy.deepcopy(scoring_gt_raw)
    comp = next(c for c in data["spec"]["components"] if c["component_id"] == component)
    (comp[path] if path else comp)[field] = value
    assert (
        LifeScoringSpec.model_validate(data["spec"]).content_fingerprint
        != _spec(scoring_gt_raw).content_fingerprint
    )


def test_the_protocol_declares_the_loaded_specs_identity(bundle_gt, scoring_gt, scoring_gt_raw):
    declared = bundle_gt.protocol.energy_feedback.scoring_content_fingerprint
    assert (
        declared == scoring_gt.spec.content_fingerprint == _spec(scoring_gt_raw).content_fingerprint
    )


def test_component_order_and_rules_take_part(scoring_gt_raw):
    fp = _spec(scoring_gt_raw).content_fingerprint
    data = copy.deepcopy(scoring_gt_raw)
    data["spec"]["components"] = list(reversed(data["spec"]["components"]))
    assert LifeScoringSpec.model_validate(data["spec"]).content_fingerprint != fp
    data = copy.deepcopy(scoring_gt_raw)
    data["spec"]["scoring_version"] = "e1-5a-growth-transport-v1"  # 同一个版本：不变
    assert LifeScoringSpec.model_validate(data["spec"]).content_fingerprint == fp
    # 来源规则也参与：第一个有来源的分项，换它第一个来源的编号
    data = copy.deepcopy(scoring_gt_raw)
    comp = next(c for c in data["spec"]["components"] if c.get("sources"))
    comp["sources"][0]["source_id"] = comp["sources"][0]["source_id"] + "_renamed"
    assert LifeScoringSpec.model_validate(data["spec"]).content_fingerprint != fp


def test_the_policy_content_covers_the_declared_scoring_content(bundle_gt, bundle4b):
    policy = bundle_gt.protocol.energy_feedback
    content = policy.content()
    assert content["content_schema"] == "e1-4b-policy-content-v2"
    assert content["scoring_content_fingerprint"] == policy.scoring_content_fingerprint
    other = revise(policy, scoring_content_fingerprint="0" * 64)
    assert other.content_fingerprint != policy.content_fingerprint
    assert (policy.handoff_revision, bundle4b.protocol.energy_feedback.handoff_revision) == (
        SCORING_CONTENT_HANDOFF_REVISION,
        FEEDBACK_HANDOFF_REVISION,
    )
    # 旧格式政策：内容与修复前同一套键，序列化里没有新字段
    old = bundle4b.protocol.energy_feedback
    assert old.content()["content_schema"] == "e1-4b-policy-content-v1"
    assert "scoring_content_fingerprint" not in old.content()
    assert "scoring_content_fingerprint" not in old.model_dump_json()


# --------------------------------------------------------------------------
# 合法路径
# --------------------------------------------------------------------------


def test_same_content_continues_and_is_traceable(bundle_gt, scoring_gt):
    y1, y2, y3 = run_clone(bundle_gt, scoring_gt, LEARN)
    assert (y1.handoff, y2.handoff, y3.handoff) == (
        "next_year_ready",
        "next_year_ready",
        "terminal",
    )
    fp = scoring_gt.spec.content_fingerprint
    assert bundle_gt.protocol.energy_feedback.scoring_content_fingerprint == fp
    for r in (y1, y2):
        assert (
            r.feedback_revision == r.feedback.feedback_revision == SCORING_CONTENT_HANDOFF_REVISION
        )
        assert r.feedback.scoring_content_fingerprint == fp
        basis = r.next_state.energy_quota_basis
        assert (basis.status, basis.scoring_content_fingerprint) == ("feedback_applied", fp)
    assert y1.next_state.energy.level == 263.72  # 复核报告里的原值：合法路径的数值不变
    # 年度绑定记下实际评分规格的内容身份，并进入绑定依据
    sealed = bound_year(bundle_gt, scoring_gt, LEARN)
    assert sealed.scoring_content_fingerprint == fp
    assert sealed.evidence()["scoring_content"] == fp


def test_two_legal_arms_from_one_start_get_their_own_quotas(bundle_gt, scoring_gt, scoring_gt_raw):
    start = state_of(bundle_gt, LEARN)
    arm_b, cfg_b = _arm(bundle_gt, _set(scoring_gt_raw, "opportunity", "growth", "eta", 0))
    assert (cfg_b.spec.spec_id, cfg_b.spec.scoring_version) == (
        scoring_gt.spec.spec_id,
        scoring_gt.spec.scoring_version,
    )
    a = year(bundle_gt, scoring_gt, start, plan_of(bundle_gt, LEARN, 1))
    b = year(arm_b, cfg_b, start, plan_of(arm_b, LEARN, 1))
    assert (a.next_state.energy.level, b.next_state.energy.level) == (263.72, 263.22)
    assert (
        b.next_state.energy_quota_basis.scoring_content_fingerprint
        == cfg_b.spec.content_fingerprint
    )
    # 各臂沿用自己的额度继续
    a2 = year(bundle_gt, scoring_gt, a.next_state, plan_of(bundle_gt, LEARN, 2))
    b2 = year(arm_b, cfg_b, b.next_state, plan_of(arm_b, LEARN, 2))
    assert (a2.handoff, b2.handoff) == ("next_year_ready", "next_year_ready")
    # 确定性重放
    again = year(arm_b, cfg_b, start, plan_of(arm_b, LEARN, 1))
    assert again.model_dump_json() == b.model_dump_json()


# --------------------------------------------------------------------------
# 混用被拒绝
# --------------------------------------------------------------------------


def test_mixing_is_an_input_error_naming_both_identities(bundle_gt, scoring_gt, scoring_gt_raw):
    (a1, *_) = run_clone(bundle_gt, scoring_gt, LEARN)
    boosted = a1.next_state
    before = boosted.model_dump_json()
    arm_b, cfg_b = _arm(bundle_gt, _set(scoring_gt_raw, "opportunity", "growth", "eta", 0))
    r = year(arm_b, cfg_b, boosted, plan_of(arm_b, LEARN, 2))
    assert r.settlement.status == "input_error"  # 不是出局，也不是资源不足
    assert "scoring_content_changed" in _codes(r)
    assert not _codes(r) & {"plan_infeasible", "energy_over_quota", "time_over_budget"}
    message = next(p.message for p in r.settlement.problems if p.code == "scoring_content_changed")
    assert "额度来自另一套评分内容" in message
    assert scoring_gt.spec.content_fingerprint in message  # 来源
    assert cfg_b.spec.content_fingerprint in message  # 当前
    # 不留下账目、状态或学习结果；不恢复基础额度，不关闭反馈
    assert (r.handoff, r.next_state, r.settlement.outcome, r.score) == (
        "settlement_failed",
        None,
        None,
        None,
    )
    assert r.feedback.decision == "settlement_failed"
    assert boosted.model_dump_json() == before
    assert arm_b.protocol.energy_feedback.effective
    # 加载与结算前检查、观察用的是同一份判断
    assert "scoring_content_changed" in {
        p.code for p in feedback_state_problems(arm_b.protocol, boosted)
    }
    with pytest.raises(LifeContractError, match="额度来自另一套评分内容"):
        _observe(arm_b, boosted)


def test_a_change_that_yields_the_same_quota_is_still_detected(
    bundle_gt, scoring_gt, scoring_gt_raw
):
    """天天到岗（D = D_ref）时 k = 1，与 ρ 无关：两套内容给出完全相同的额度，混用照样被拒绝。"""
    start = state_of(bundle_gt, ONSITE)
    arm_b, cfg_b = _arm(bundle_gt, _set(scoring_gt_raw, "commute", "transport", "rho", 0.5))
    a = year(bundle_gt, scoring_gt, start, plan_of(bundle_gt, ONSITE, 1))
    b = year(arm_b, cfg_b, start, plan_of(arm_b, ONSITE, 1))
    assert a.score.annual_mean == b.score.annual_mean
    assert a.next_state.energy.level == b.next_state.energy.level
    r = year(arm_b, cfg_b, a.next_state, plan_of(arm_b, ONSITE, 2))
    assert r.settlement.status == "input_error" and "scoring_content_changed" in _codes(r)


def test_changing_the_spec_without_the_declaration_is_refused_before_settling(
    bundle_gt, scoring_gt, scoring_gt_raw
):
    (a1, *_) = run_clone(bundle_gt, scoring_gt, LEARN)
    changed = parse_scoring_config(_set(scoring_gt_raw, "opportunity", "growth", "eta", 0))
    errors, _ = scoring_config_problems(changed, bundle_gt)
    assert any("实际内容身份" in e for e in errors)  # 加载拒绝
    for state in (
        state_of(bundle_gt, LEARN),
        a1.next_state,
    ):  # 绕过加载：结算之前拒绝，第一年也一样
        with pytest.raises(LifeContractError, match="实际内容身份"):
            year(bundle_gt, changed, state, plan_of(bundle_gt, LEARN, state.year_index))
    problems = scoring_content_problems(bundle_gt.protocol.energy_feedback, changed.spec)
    assert [c for c, _ in problems] == ["scoring_content_mismatch"]


def test_rewriting_the_state_to_match_still_cannot_reuse_it(bundle_gt, scoring_gt, scoring_gt_raw):
    """参数与声明一起改，再把旧状态里的评分内容身份改成新的：政策内容指纹仍然不同，照样拒绝。"""
    (a1, *_) = run_clone(bundle_gt, scoring_gt, LEARN)
    arm_b, cfg_b = _arm(bundle_gt, _set(scoring_gt_raw, "opportunity", "growth", "eta", 0))
    forged = revise(
        a1.next_state,
        energy_quota_basis=revise(
            a1.next_state.energy_quota_basis,
            scoring_content_fingerprint=cfg_b.spec.content_fingerprint,
        ),
    )
    r = year(arm_b, cfg_b, forged, plan_of(arm_b, LEARN, 2))
    assert r.settlement.status == "input_error"
    assert "feedback_policy_content_changed" in _codes(r)
    assert "scoring_content_changed" not in _codes(r)


# --------------------------------------------------------------------------
# 删掉绑定不是默认通过
# --------------------------------------------------------------------------


def test_deleting_the_binding_is_not_a_default_pass(bundle_gt, scoring_gt, raw_gt):
    # ① 协议里删掉声明：新评分版本必须声明，加载报错
    del raw_gt["protocol"]["energy_feedback"]["scoring_content_fingerprint"]
    with pytest.raises(LifeContractError, match="必须声明评分内容身份"):
        parse_bundle(raw_gt)
    # ② 状态里删掉评分内容身份：来源"未记录"，拒绝
    (a1, *_) = run_clone(bundle_gt, scoring_gt, LEARN)
    data = a1.next_state.model_dump(mode="python")
    data["energy_quota_basis"].pop("scoring_content_fingerprint")
    stripped = PersonYearState.model_validate(data)
    codes = [c for c, _ in energy_quota_problems(bundle_gt.protocol.energy_feedback, stripped)]
    assert codes == ["scoring_content_changed"]
    r = year(bundle_gt, scoring_gt, stripped, plan_of(bundle_gt, LEARN, 2))
    assert r.settlement.status == "input_error"
    assert any("未记录" in p.message for p in r.settlement.problems)
    # ③ 政策里删掉声明并改成旧格式版本（才能构造）：旧状态的政策内容与评分内容都对不上
    old_style = revise(
        bundle_gt.protocol.energy_feedback,
        scoring_content_fingerprint=None,
        scoring_version="e1-3-supplement-v1",
    )
    codes = {c for c, _ in energy_quota_problems(old_style, a1.next_state)}
    assert codes == {"feedback_policy_content_changed", "scoring_content_changed"}
    # ④ 记录与年度绑定：r3 少了评分内容身份不能构造
    record = a1.feedback.model_dump(mode="python")
    record.pop("scoring_content_fingerprint")
    with pytest.raises(ValidationError, match="r3 的反馈记录必须写评分内容身份"):
        EnergyFeedbackRecord.model_validate(record)
    sealed = bound_year(bundle_gt, scoring_gt, LEARN)
    data = sealed.model_dump(mode="python")
    data.pop("scoring_content_fingerprint")
    with pytest.raises(ValidationError, match="r3 下给了评分输入就必须记下"):
        ScoredYear.model_validate(data)
    result = a1.model_dump(mode="python")
    result["feedback_revision"] = FEEDBACK_HANDOFF_REVISION
    with pytest.raises(ValidationError, match="执行修订不同"):
        LifeYearResult.model_validate(result)


def test_an_r2_state_of_the_new_protocol_must_be_rerun(bundle_gt, scoring_gt):
    """修复前（未声明）的 E1-5A 补充协议产生的第 2 年状态：政策内容指纹是旧的、没有评分内容身份。不猜，从初始输入重跑。"""
    (a1, *_) = run_clone(bundle_gt, scoring_gt, LEARN)
    undeclared = EnergyFeedbackPolicy.model_construct(
        **{
            **dict(bundle_gt.protocol.energy_feedback),
            "scoring_content_fingerprint": None,
        }
    )
    data = a1.next_state.model_dump(mode="python")
    data["energy_quota_basis"].pop("scoring_content_fingerprint")
    data["energy_quota_basis"]["policy_fingerprint"] = undeclared.content_fingerprint
    r2_state = PersonYearState.model_validate(data)
    codes = {c for c, _ in energy_quota_problems(bundle_gt.protocol.energy_feedback, r2_state)}
    assert codes == {"feedback_policy_content_changed", "scoring_content_changed"}
    # 第一年共同初始状态不绑定：直接可用
    assert (
        energy_quota_problems(bundle_gt.protocol.energy_feedback, state_of(bundle_gt, LEARN)) == []
    )


# --------------------------------------------------------------------------
# 关闭、α = 0、反馈受阻、出局、终态
# --------------------------------------------------------------------------


@pytest.mark.parametrize("changes", [{"mode": "disabled"}, {"alpha": 0.0}], ids=["off", "alpha0"])
def test_off_and_alpha_zero_need_no_scoring_and_keep_the_check(bundle_gt, scoring_gt, changes):
    off_policy = revise(
        bundle_gt.protocol.energy_feedback, policy_id="feedback.e1-5a-gt.off", **changes
    )
    off = revise(bundle_gt, protocol=revise(bundle_gt.protocol, energy_feedback=off_policy))
    start = state_of(off, LEARN)
    y1 = year(off, scoring_gt, start, plan_of(off, LEARN, 1), with_scoring=False)
    assert (y1.handoff, y1.next_state.energy.level) == ("next_year_ready", 250)
    basis = y1.next_state.energy_quota_basis
    assert (basis.status, basis.scoring_content_fingerprint) == (
        "feedback_off",
        scoring_gt.spec.content_fingerprint,
    )
    y2 = year(off, scoring_gt, y1.next_state, plan_of(off, LEARN, 2), with_scoring=False)
    assert y2.handoff == "next_year_ready"
    # 关掉反馈、不给评分输入，也绕不过额度来源检查：开着反馈的加成不能拿来用
    (a1, *_) = run_clone(bundle_gt, scoring_gt, LEARN)
    r = year(off, scoring_gt, a1.next_state, plan_of(off, LEARN, 2), with_scoring=False)
    assert r.settlement.status == "input_error"
    assert {"feedback_policy_switched", "energy_quota_not_base"} <= _codes(r)


def test_blocked_exit_and_terminal_are_unchanged(bundle_gt, scoring_gt):
    start = state_of(bundle_gt, LEARN)
    blocked = year(bundle_gt, scoring_gt, start, plan_of(bundle_gt, LEARN, 1), with_scoring=False)
    assert (blocked.handoff, blocked.next_state, blocked.feedback.decision) == (
        "feedback_blocked",
        None,
        "blocked",
    )
    assert blocked.feedback.scoring_content_fingerprint == scoring_gt.spec.content_fingerprint
    assert blocked.settlement.status == "completed"  # 当年结算照常保留
    poor = revise(
        state_of(bundle_gt, "u64.a71.study"),
        cash={"amount": 1000, "numeraire": "USD_2024_LOCAL"},
    )
    exited = year(bundle_gt, scoring_gt, poor, plan_of(bundle_gt, "u64.a71.study", 1))
    assert (exited.handoff, exited.feedback.decision, exited.next_state.status) == (
        "exited",
        "exited",
        "exited",
    )
    assert exited.next_state.energy_quota_basis.status == "base_first_year"  # 出局不更新精力
    (*_, last) = run_clone(bundle_gt, scoring_gt, LEARN)
    assert (last.handoff, last.feedback.decision, last.next_state.status) == (
        "terminal",
        "no_next_action_year",
        "terminal",
    )


# --------------------------------------------------------------------------
# 旧格式与信息隔离
# --------------------------------------------------------------------------


def test_old_protocols_keep_r2_and_their_outputs(bundle4b, scoring4b, bundle5a, scoring5a):
    for bundle, config, clone in (
        (bundle4b, scoring4b, "u31.a61.c0"),
        (bundle5a, scoring5a, "u51.a71.study"),
    ):
        assert bundle.protocol.energy_feedback.scoring_content_fingerprint is None
        (r1, *_) = run_clone(bundle, config, clone)
        assert r1.feedback_revision == FEEDBACK_HANDOFF_REVISION
        assert "scoring_content" not in json.dumps(r1.model_dump(mode="json"))
        sealed = bound_year(bundle, config, clone)
        assert "scoring_content" not in sealed.evidence()
    loaded, _ = load_life_bundle(FIXTURE_GT, require_settlement_ready=True)
    assert loaded.protocol.energy_feedback.handoff_revision == SCORING_CONTENT_HANDOFF_REVISION


def test_the_identity_stays_out_of_the_payload(bundle_gt, scoring_gt):
    (a1, *_) = run_clone(bundle_gt, scoring_gt, LEARN)
    obs = _observe(bundle_gt, a1.next_state)
    blob = json.dumps(observation_payload(obs), ensure_ascii=False)
    fp = scoring_gt.spec.content_fingerprint
    assert fp not in blob and "scoring_content" not in blob
    assert bundle_gt.protocol.energy_feedback.policy_id not in blob
    with pytest.raises(LifeContractError, match="内部键"):
        observation_payload(
            obs.model_copy(update={"goals": ({"scoring_content_fingerprint": fp},)})
        )
