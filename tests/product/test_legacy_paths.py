"""E3-A 兼容：新类型不能落进旧分支（六维偏好、旧七维画像、未知偏好来源）。

（2026-10-18 开发期：与旧 mock 归档逐字节比较的测试已删除，见 DECISIONS/0035 第九节。）
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from common.life_contracts import LifeContractError
from common.life_scoring_contracts import ScoringPreference
from econ.life_scoring import effective_weights
from econ.life_year import ScoringInputs, _check_scoring_inputs
from ledger.life_catalog import load_life_bundle
from ledger.life_scoring_config import load_scoring_config

REPO = Path(__file__).resolve().parents[2]


def test_a_six_dimension_preference_is_refused_by_an_old_seven_dimension_scoring_version():
    bundle, _ = load_life_bundle(Path("configs/life/synthetic_e1_5a_growth_transport.yaml"))
    scoring, _ = load_scoring_config(
        Path("configs/life/scoring_e1_5a_growth_transport.yaml"), bundle
    )
    assert scoring.spec.scoring_version == "e1-5a-growth-transport-v1"
    six = ScoringPreference(
        person_id="p1",
        source="product_confirmed_six_dim",
        dimension_weights={
            "material": 0.3,
            "safety": 0.2,
            "opportunity": 0.2,
            "leisure": 0.1,
            "environment": 0.1,
            "commute": 0.1,
        },
        material_weights={"housing_space": 0.5, "fresh_food": 0.5},
        derivation="测试：六维产品画像的权重",
    )
    with pytest.raises(LifeContractError, match="不能补一个医疗占位分"):
        effective_weights(scoring.spec, six)
    with pytest.raises(ValueError, match="六维产品画像的权重必须恰好是"):
        ScoringPreference(
            person_id="p1",
            source="product_confirmed_six_dim",
            dimension_weights={**six.dimension_weights, "healthcare": 0.0},
            material_weights=six.material_weights,
            derivation="测试：补了医疗占位分",
        )


def test_unknown_or_mismatched_preference_inputs_never_fall_into_the_research_branch(tmp_path):
    from product import scenarios as sc

    bundle, _ = load_life_bundle(Path("configs/life/synthetic_e1_convenience.yaml"))
    scoring, _ = load_scoring_config(Path("configs/life/scoring_e1_convenience.yaml"), bundle)
    state = bundle.initial_state("u301.k51.r0")
    conditions = scoring.conditions_for("US:SYNTH2026:K51")
    stranger = SimpleNamespace(person_id="u301", healthcare_semantics="health_risk_coping_v1")
    with pytest.raises(LifeContractError, match="只接受 StatedLifeProfile"):
        _check_scoring_inputs(
            bundle.protocol,
            state,
            ScoringInputs(spec=scoring.spec, conditions=conditions, preference_input=stranger),
        )
    # 六维产品画像交给旧便利协议（偏好来源 product_stated_numeric）：拒绝
    s = sc.preflight_only(tmp_path, "six", person="u301", hard_requirements=())
    product = s.pre.world.bundle.product_profiles[0]
    with pytest.raises(LifeContractError, match="只接受 StatedLifeProfile"):
        _check_scoring_inputs(
            bundle.protocol,
            state,
            ScoringInputs(spec=scoring.spec, conditions=conditions, preference_input=product),
        )
    # 旧七维陈述交给产品协议（偏好来源 product_confirmed_six_dim）：拒绝
    w = s.pre.world
    with pytest.raises(LifeContractError, match="只接受 ProductLifeProfile"):
        _check_scoring_inputs(
            w.bundle.protocol,
            w.bundle.initial_state("u301.k51.r0"),
            ScoringInputs(
                spec=w.scoring.spec,
                conditions=w.scoring.conditions_for("US:SYNTH2026:K51"),
                preference_input=bundle.stated_profiles[0],
            ),
        )


def test_old_seven_dimension_profiles_cannot_enter_the_product_protocol(tmp_path):
    from product import scenarios as sc
    from product.compile import _dump

    s = sc.preflight_only(tmp_path, "mixing", person="u610", hard_requirements=())
    path = s.work / s.pre.compiled.paths["life_input"]  # 编译产物的路径相对于场景的工作目录
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    old = yaml.safe_load(
        Path(REPO / "configs/life/synthetic_e1_convenience.yaml").read_text(encoding="utf-8")
    )
    data["stated_profiles"] = old["stated_profiles"]
    data["internal_truths"] = old["internal_truths"]
    bad = tmp_path / "bad.yaml"
    _dump(bad, "", data)
    with pytest.raises(LifeContractError, match="不能把七维医疗点数改读成独立健康回答"):
        load_life_bundle(bad)
