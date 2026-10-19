"""E1-3 增补：比较组的共同粒度、偏好覆盖报告、信息隔离、旧样例兼容与运行证据
（`DECISIONS/0019` 第五、六节与第十一节）。

比较组挡的是"某城细化、某城用别的粒度或默认值填"：混基年、混地理粒度、来源类型越界、某类
行动完全没声明、规格键没单位、通勤粒度不统一。真正不可得是合法事实。覆盖报告只报告，**不设
阈值、不解锁总分**。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agentlayer.life_views import (
    ObservationPermission,
    assert_payload_clean,
    build_observation,
    observation_payload,
    visible_feedback,
)
from common.life_contracts import LifeContractError
from common.life_scoring_contracts import PreferenceInsufficient
from econ.life_coverage import coverage_report
from econ.life_scoring import preference_from_research_truth, preference_from_stated_profile
from ledger.life_scoring_config import check_scoring_config, parse_scoring_config

from .conftest import catalog, opportunity
from .score_support import (
    APPROX,
    component,
    profile_of,
    research_pref,
    score_of,
    settle_year_of,
    truth_of,
)
from .settle_support import bundle_from

REPO = Path(__file__).resolve().parents[2]
PRODUCT = dict(
    condition="product_stated_profile",
    show_stated_dimension_points=True,
    show_soft_preferences=True,
    show_hard_requirements=True,
    show_free_text=True,
    show_energy_level=False,
    show_stated_material_points=True,
    permission_version="e1-3-draft",
)


def _condition(raw_scoring: dict, code: str, component_id: str) -> dict:
    cc = next(c for c in raw_scoring["city_conditions"] if c["city"]["code"] == code)
    return next(x for x in cc["components"] if x["component_id"] == component_id)


def _errors(raw_scoring: dict, bundle) -> list[str]:
    return list(check_scoring_config(parse_scoring_config(raw_scoring), bundle).errors)


def _coverage(bundle, config, person: str = "u11", preference=None):
    return coverage_report(
        spec=config.spec,
        group=config.comparison_group,
        conditions=config.city_conditions,
        catalogs=bundle.catalogs,
        preference=preference or research_pref(bundle, person),
    )


def _rebase(node, old: str, new: str):
    """把一棵子树里所有等于 old 的字符串换成 new（模拟这座城整套用另一个基年计价）。"""
    if isinstance(node, dict):
        return {k: _rebase(v, old, new) for k, v in node.items()}
    if isinstance(node, list):
        return [_rebase(v, old, new) for v in node]
    return new if node == old else node


# --------------------------------------------------------------------------
# 比较组：共同粒度
# --------------------------------------------------------------------------


def test_the_sample_group_passes_and_reports_its_frozen_basis(bundle3s, scoring3s):
    report = check_scoring_config(scoring3s, bundle3s)
    assert report.exploration_scoring_ready, report.errors
    assert report.summary["comparison_group"] == "group.e1-3s.synth"
    assert report.formal_scoring_ready is False
    g = scoring3s.comparison_group
    assert (g.currency, g.base_year, g.commute_granularity) == ("USD", 2024, "plan_level")
    assert g.allowed_source_kinds == ("synthetic",)


def test_a_city_priced_in_another_base_year_is_refused(raw3s, scoring3s):
    old, new = "USD_2024_LOCAL", "USD_2023_LOCAL"
    raw3s["catalogs"] = [
        _rebase(c, old, new) | {"money_basis": {"currency": "USD", "base_year": 2023}}
        if c["city"]["code"] == "B42"
        else c
        for c in raw3s["catalogs"]
    ]
    raw3s["tax_policies"] = [
        _rebase(t, old, new) if t["policy_id"] == "tax.synth.zb" else t
        for t in raw3s["tax_policies"]
    ]
    raw3s["initial_states"] = [
        _rebase(s, old, new) if s["city"]["code"] == "B42" else s for s in raw3s["initial_states"]
    ]
    errors = check_scoring_config(scoring3s, bundle_from(raw3s)).errors
    flagged = [e for e in errors if "不混币，不混基年" in e]
    assert flagged and all("B42" in e for e in flagged)


def test_a_city_filled_at_another_granularity_is_refused(raw3s, scoring3s):
    o = opportunity(raw3s, "b42.housing.far")
    o["source"] = {**o["source"], "geography_scope": "US metro average"}  # 锚点共享：整条替换
    errors = check_scoring_config(scoring3s, bundle_from(raw3s)).errors
    assert any("b42.housing.far" in e and "地理粒度" in e for e in errors)


def test_a_source_kind_outside_the_group_is_refused(raw_scoring3s, bundle3s):
    cond = _condition(raw_scoring3s, "A41", "safety")
    cond["source"] = {
        "kind": "estimated",
        "observed_on": "2026-10-07",
        "geography_scope": "synthetic city (no real geography)",
        "mapping_version": "test",
        "inputs": ["survey wave 1"],
        "rule": "small-area estimate",
    }
    assert any("来源类型 estimated 不在比较组允许的" in e for e in _errors(raw_scoring3s, bundle3s))


def test_truly_unavailable_is_legal_but_an_undeclared_action_type_is_not(raw3s, scoring3s):
    b42 = catalog(raw3s, "B42")
    # 共享办公从"确定没有"改成"没有资料"：照样合法（未知与不可得都要写明，但都可以写）
    next(o for o in b42["opportunities"] if o["opportunity_id"] == "b42.service.cowork")[
        "availability"
    ] = "unknown"
    assert check_scoring_config(scoring3s, bundle_from(raw3s)).errors == ()
    # 按月服务一条都不写：不是"没有"，是没声明——拒绝
    b42["opportunities"] = [
        o
        for o in b42["opportunities"]
        if o["opportunity_id"] not in ("b42.service.cowork", "b42.service.cleaning")
    ]
    errors = check_scoring_config(scoring3s, bundle_from(raw3s)).errors
    assert any("B42" in e and "periodic_service 没有任何记录" in e for e in errors)


def test_every_spec_key_used_needs_a_unit(raw_scoring3s, bundle3s):
    del raw_scoring3s["comparison_group"]["units"]["storage_liters"]
    assert any(
        "storage_liters 没有写进比较组的单位表" in e for e in _errors(raw_scoring3s, bundle3s)
    )


def test_the_group_freezes_one_scoring_version(raw_scoring3s, bundle3s):
    raw_scoring3s["comparison_group"]["scoring_version"] = "e1-3-explore-v1"
    assert any("比较组冻结的评分版本" in e for e in _errors(raw_scoring3s, bundle3s))


def test_old_configs_without_a_group_still_load(bundle3, scoring3):
    report = check_scoring_config(scoring3, bundle3)
    assert scoring3.comparison_group is None
    assert report.exploration_scoring_ready and report.summary["comparison_group"] is None


# --------------------------------------------------------------------------
# 覆盖报告：算到偏好叶子，只报告，不解锁
# --------------------------------------------------------------------------


def test_coverage_uses_leaf_weights_and_counts_only_commonly_supported_leaves(bundle3s, scoring3s):
    rep = _coverage(bundle3s, scoring3s)
    a = {x.leaf: x.weight for x in rep.leaves}
    m = truth_of(bundle3s, "u11").material_subweights
    for need, w in m.items():
        assert a[need] == pytest.approx(0.35 * w, **APPROX)  # 物质细项 a = w_material × m_j
    assert a["safety"] == pytest.approx(0.15, **APPROX)  # 非物质 a = w_k
    assert math.fsum(a.values()) == pytest.approx(1.0, **APPROX)
    assert rep.common_weight == pytest.approx(0.9, **APPROX)
    assert rep.unknown_weight == pytest.approx(0.1, **APPROX)
    assert rep.weight_by_evidence == pytest.approx({"synthetic_only": 0.9}, **APPROX)
    assert rep.coverage_label == "synthetic_contract_coverage"
    env = next(x for x in rep.leaves if x.leaf == "environment")
    assert (env.support, env.cities_unknown) == ("unknown_in_some_city", ("US:SYNTH2026:B42",))
    safety = next(x for x in rep.leaves if x.leaf == "safety")
    assert safety.support == "supported" and not safety.action_mechanism  # 背景已知 ≠ 行动建模了


def test_coverage_does_not_unlock_an_incomplete_total(bundle3s, scoring3s):
    """九成权重有共同支持，也不把只有一部分可算写成整体通过：B42 的月度总分照旧为空。"""
    rep = _coverage(bundle3s, scoring3s)
    assert rep.common_weight == pytest.approx(0.9, **APPROX)
    assert any("不设阈值，不解锁完整总分" in n for n in rep.notes)
    s, r = settle_year_of(bundle3s, "u11.b42.c0")
    score = score_of(bundle3s, scoring3s, s, r)
    assert all(mo.u is None for mo in score.months) and score.annual_mean is None


def test_a_leaf_unknown_in_one_city_leaves_the_common_set(raw_scoring3s, bundle3s):
    cond = _condition(raw_scoring3s, "A41", "safety")
    for key in ("base", "cap"):
        del cond[key]
    cond.update(status="unknown", unknown_reason="测试：A41 没有安全资料")
    config = parse_scoring_config(raw_scoring3s)
    rep = _coverage(bundle3s, config)
    safety = next(x for x in rep.leaves if x.leaf == "safety")
    assert (safety.support, safety.cities_unknown) == (
        "unknown_in_some_city",
        ("US:SYNTH2026:A41",),
    )
    assert rep.common_weight == pytest.approx(0.9 - 0.15, **APPROX)


def test_coverage_labels_follow_the_evidence(raw_scoring3s, bundle3s):
    estimated = {
        "kind": "estimated",
        "observed_on": "2026-10-07",
        "geography_scope": "synthetic city (no real geography)",
        "mapping_version": "test",
        "inputs": ["survey wave 1"],
        "rule": "small-area estimate",
    }
    for code in ("A41", "B42"):
        _condition(raw_scoring3s, code, "safety")["source"] = estimated
    rep = _coverage(bundle3s, parse_scoring_config(raw_scoring3s))
    assert rep.coverage_label == "mixed_coverage"
    assert rep.weight_by_evidence == pytest.approx(
        {"estimated": 0.15, "synthetic_only": 0.75}, **APPROX
    )
    assert not any("合成契约覆盖" in n for n in rep.notes)


def test_unsupported_needs_are_reported_with_their_weight(bundle3s, scoring3s):
    rep = _coverage(bundle3s, scoring3s, "u12")
    cleaning = next(x for x in rep.leaves if x.leaf == "home_cleaning")
    assert (cleaning.support, cleaning.evidence) == ("no_component", "none")
    assert cleaning.weight == pytest.approx(0.4 * 0.1, **APPROX)
    assert rep.no_component_weight == pytest.approx(0.04, **APPROX)
    assert rep.common_weight == pytest.approx(1 - 0.1 - 0.04, **APPROX)


def test_coverage_refuses_mismatched_inputs(bundle3s, scoring3s):
    with pytest.raises(LifeContractError, match="不一致"):
        coverage_report(
            spec=scoring3s.spec,
            group=scoring3s.comparison_group,
            conditions=scoring3s.city_conditions[:1],
            catalogs=bundle3s.catalogs,
            preference=research_pref(bundle3s, "u11"),
        )
    insufficient = PreferenceInsufficient(
        person_id="u11",
        source="product_stated_numeric",
        missing=("material_points",),
        message="测试",
    )
    with pytest.raises(LifeContractError, match="ScoringPreference"):
        coverage_report(
            spec=scoring3s.spec,
            group=scoring3s.comparison_group,
            conditions=scoring3s.city_conditions,
            catalogs=bundle3s.catalogs,
            preference=insufficient,
        )


def test_product_coverage_uses_only_the_stated_numbers(bundle3s, scoring3s):
    """产品路径的叶子权重来自用户点数 / 100；u11 的点数恰好等于合成真值 × 100。"""
    stated = preference_from_stated_profile(profile_of(bundle3s, "u11"))
    truth = preference_from_research_truth(truth_of(bundle3s, "u11"))
    a = _coverage(bundle3s, scoring3s, preference=stated)
    b = _coverage(bundle3s, scoring3s, preference=truth)
    assert a.preference_source == "product_stated_numeric"
    assert [x.weight for x in a.leaves] == pytest.approx([x.weight for x in b.leaves], **APPROX)


# --------------------------------------------------------------------------
# 信息隔离：新增的评分侧对象一个都不进观察
# --------------------------------------------------------------------------


def test_new_internal_objects_never_reach_observations(bundle3s, scoring3s):
    s, r = settle_year_of(bundle3s, "u11.a41.c0")
    score = score_of(bundle3s, scoring3s, s, r)
    env_spec = scoring3s.spec.dimension_component("environment")
    commute_spec = scoring3s.spec.dimension_component("commute")
    a41 = scoring3s.conditions_for("US:SYNTH2026:A41")
    rep = _coverage(bundle3s, scoring3s)
    for smuggled in (
        env_spec.landscape,
        commute_spec.plan_commute,
        a41.condition("environment").nature,
        scoring3s.comparison_group,
        rep,
        rep.leaves[0],
        component(score, 1, "environment").landscape,
        component(score, 1, "commute").commute,
    ):
        with pytest.raises(LifeContractError, match=f"^{type(smuggled).__name__} 出现在"):
            assert_payload_clean({"opportunities": [{"spec": {"deep": [smuggled]}}]})
    for key in ("common_weight", "coverage_label", "a_nature"):
        with pytest.raises(LifeContractError, match="内部键"):
            assert_payload_clean({"notes": [{key: 0.9}]})

    obs = build_observation(
        permission=ObservationPermission(**PRODUCT),
        profile=profile_of(bundle3s, "u11"),
        state=r.next_state,
        catalog=bundle3s.catalog_for(str(s.city)),
        last_feedback=visible_feedback(r),
    )
    payload = observation_payload(obs)
    blob = json.dumps(payload, ensure_ascii=False)
    for internal in (
        "a_nature",
        "common_weight",
        "landscape",
        "plan_commute",
        "tau_minutes",
        "0.58",
    ):
        assert internal not in blob
    # 可见的是事实：功能、属性、住房写明的往返分钟、工作方式
    near = next(o for o in payload["opportunities"] if o["opportunity_id"] == "a41.housing.near")
    phone = next(
        o for o in payload["opportunities"] if o["opportunity_id"] == "a41.durable.phone_premium"
    )
    assert near["spec"]["commute_roundtrip_minutes"] == 30
    assert phone["functions"] == ["communication"]
    assert phone["attributes"] == {"brand": "premium_phone", "tier": "premium"}


# --------------------------------------------------------------------------
# 旧样例与历史产物不变；新运行证据写进新目录
# --------------------------------------------------------------------------

ARCHIVED = REPO / "runs/2026-10-06-e1-3-scoring-demo/results.json"


def test_the_archived_e1_3_results_are_reproduced_exactly(bundle3, scoring3):
    """旧样例按当前代码重新结算、评分，与归档的运行结果逐项相同（旧数学没有变）。"""
    archived = json.loads(ARCHIVED.read_text(encoding="utf-8"))
    checked = 0
    for entry in archived:
        state = None
        for year in range(1, entry["year_index"] + 1):
            s, r = settle_year_of(bundle3, entry["clone_id"], year=year, state=state)
            state = r.next_state
        profile = profile_of(bundle3, entry["person_id"])
        for source, old in entry["scores"].items():
            pref = (
                preference_from_research_truth(truth_of(bundle3, entry["person_id"]))
                if source == "research_synthetic_truth"
                else preference_from_stated_profile(profile)
            )
            if isinstance(pref, PreferenceInsufficient):
                assert pref.model_dump(mode="json") == old
                checked += 1
                continue
            new = score_of(bundle3, scoring3, s, r, preference=pref).model_dump(mode="json")
            for key in (
                "annual_mean",
                "annual_status",
                "months_scored",
                "months_incomplete",
                "notes",
            ):
                assert new[key] == old[key], (entry["clone_id"], source, key)
            for mo, mn in zip(old["months"], new["months"], strict=True):
                assert (mn["status"], mn["u"]) == (mo["status"], mo["u"])
                for co, cn in zip(mo["components"], mn["components"], strict=True):
                    for key in ("component_id", "status", "z", "raw", "s", "capped"):
                        assert cn[key] == co[key], (entry["clone_id"], mo["month"], key)
                    assert [t["contribution"] for t in cn["terms"]] == [
                        t["contribution"] for t in co["terms"]
                    ]
            assert [h["summary"] for h in new["hard_requirements"]] == [
                h["summary"] for h in old["hard_requirements"]
            ]
            checked += 1
    assert checked == 10


def _tree(path: Path) -> dict[str, str]:
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def test_the_demo_writes_a_new_run_directory_and_refuses_to_overwrite(tmp_path):
    out = tmp_path / "run"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, "scripts/demo_life_e1_3s.py", "--out", str(out)]
    first = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", env=env)
    assert first.returncode == 0, first.stdout + first.stderr
    for name in (
        "README.md",
        "command.json",
        "environment.json",
        "protocol.json",
        "inputs.json",
        "results.json",
        "control_beta0.json",
        "coverage.json",
        "feasibility_probe.json",
        "summary.md",
        "checks.md",
        "checks.json",
        "configs/synthetic_e1_3s.yaml",
        "configs/scoring_e1_3s.yaml",
        "configs/scoring_e1_3s_beta0_control.yaml",
    ):
        assert (out / name).is_file(), name
    command = json.loads((out / "command.json").read_text(encoding="utf-8"))
    assert command["argv"][-2:] == ["--out", str(out)]
    fp = json.loads((out / "environment.json").read_text(encoding="utf-8"))["fingerprints_sha256"]
    for src in (
        "src/econ/life_scoring.py",
        "src/econ/life_coverage.py",
        "src/econ/life_settlement.py",
    ):
        assert fp[src] == hashlib.sha256((REPO / src).read_bytes()).hexdigest()
    for name in ("synthetic_e1_3s.yaml", "scoring_e1_3s.yaml"):
        assert (out / "configs" / name).read_bytes() == (REPO / "configs/life" / name).read_bytes()
    checks = json.loads((out / "checks.json").read_text(encoding="utf-8"))
    assert checks and all(c["passed"] for c in checks)
    protocol = json.loads((out / "protocol.json").read_text(encoding="utf-8"))
    assert protocol["protocol"]["protocol_id"] == "life-v1-e1-3s"
    assert protocol["formal_scoring_ready"] is False

    before = _tree(out)
    second = subprocess.run(
        cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", env=env
    )
    assert second.returncode == 1 and "不覆盖旧结果" in second.stdout
    assert _tree(out) == before


def test_the_old_configs_keep_their_own_version_and_rules():
    """旧协议的样例与配置原样保留：新规则只在新协议、新评分版本里启用。"""
    text = (REPO / "configs/life/scoring_e1_3.yaml").read_text(encoding="utf-8")
    assert "scoring_version: e1-3-explore-v1" in text
    assert "protocol_id: life-v1-e1-3\n" in text
    for new_rule in (
        "landscape:",
        "plan_commute:",
        "functions:",
        "comparison_group:",
        "omissions:",
    ):
        assert new_rule not in text
