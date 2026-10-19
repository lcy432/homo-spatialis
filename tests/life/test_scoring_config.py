"""E1-3 探索评分配置的检查（`DECISIONS/0018`）。

拒绝类测试都从合法配置出发、只改坏一处。重点是那几条结构性保证：锚点 τ 不能按城市
调、基础条件不能超过上限、未知不是零、每个来源都要有声明过的改善效率、评分规格只能
配它自己的协议与城市。
"""

from __future__ import annotations

import pytest

from common.life_contracts import LifeContractError
from common.life_scoring_contracts import CityComponentCondition
from ledger.life_catalog import cross_validate
from ledger.life_scoring_config import check_scoring_config, parse_scoring_config

from .score_support import score_of, settle_year_of


def _errors(raw_scoring: dict, bundle) -> tuple[str, ...]:
    return check_scoring_config(parse_scoring_config(raw_scoring), bundle).errors


def _cond(raw_scoring: dict, city_code: str, component_id: str) -> dict:
    city = next(c for c in raw_scoring["city_conditions"] if c["city"]["code"] == city_code)
    return next(x for x in city["components"] if x["component_id"] == component_id)


def test_the_fixture_is_ready_for_exploration_but_not_for_formal_scoring(bundle3, raw_scoring):
    report = check_scoring_config(parse_scoring_config(raw_scoring), bundle3)
    assert report.exploration_scoring_ready and not report.formal_scoring_ready
    assert any("只有城市基础条件" in n for n in report.notes)
    assert any("environment" in n and "数据未知" in n for n in report.notes)
    life = cross_validate(bundle3)
    assert life.settlement_ready and not life.scoring_ready
    # 没有靠把待定政策标成 decided 来解锁演示
    assert bundle3.protocol.math_policies["score_mapping"].state == "undecided"
    assert bundle3.protocol.math_policies["material_preference_aggregation"].state == "undecided"


def test_the_anchor_cannot_be_tuned_per_city(raw_scoring):
    assert "tau" not in CityComponentCondition.model_fields
    _cond(raw_scoring, "B32", "leisure")["tau"] = 6
    with pytest.raises(LifeContractError, match="tau"):
        parse_scoring_config(raw_scoring)


def test_a_base_condition_above_the_cap_is_inconsistent(bundle3, raw_scoring):
    _cond(raw_scoring, "A31", "safety")["base"] = 30  # 30 / 33 ≈ 0.909 > 0.9
    assert any("不自洽" in e for e in _errors(raw_scoring, bundle3))


def test_every_source_needs_a_declared_efficiency(bundle3, raw_scoring):
    _cond(raw_scoring, "A31", "leisure")["efficiencies"] = [{"source_id": "usage_plan", "eta": 1.0}]
    assert any("events" in e and "改善效率" in e for e in _errors(raw_scoring, bundle3))


def test_unknown_is_not_zero_in_the_config_either(raw_scoring):
    _cond(raw_scoring, "B32", "environment")["base"] = 0
    with pytest.raises(LifeContractError, match="未知不是零"):
        parse_scoring_config(raw_scoring)


def test_each_amenity_dimension_needs_exactly_one_component(raw_scoring):
    raw_scoring["spec"]["components"] = [
        c for c in raw_scoring["spec"]["components"] if c["component_id"] != "commute"
    ]
    with pytest.raises(LifeContractError, match="commute"):
        parse_scoring_config(raw_scoring)


def test_a_boolean_substitute_needs_flag_sources(raw_scoring):
    ws = next(c for c in raw_scoring["spec"]["components"] if c["component_id"] == "workspace")
    ws["sources"][0] = {
        "source_id": "home_office",
        "records": ["housing"],
        "value": "spec_number",
        "spec_key": "bedrooms",
        "unit": "间",
        "note": "改坏：any 混进了数值来源",
    }
    with pytest.raises(LifeContractError, match="any"):
        parse_scoring_config(raw_scoring)


def test_a_spec_only_scores_its_own_protocol_and_cities(bundle3, raw_scoring):
    raw_scoring["spec"]["protocol_id"] = "life-v1-e1-2"
    assert any("对应协议" in e for e in _errors(raw_scoring, bundle3))
    raw_scoring["spec"]["protocol_id"] = "life-v1-e1-3"
    raw_scoring["city_conditions"][1]["city"]["code"] = "Q99"
    errors = _errors(raw_scoring, bundle3)
    assert any("没有的城市" in e for e in errors)
    assert any("B32" in e and "没有评分条件" in e for e in errors)


def test_a_material_component_must_point_at_a_declared_need(bundle3, raw_scoring):
    hs = next(c for c in raw_scoring["spec"]["components"] if c["component_id"] == "housing_space")
    hs["material_need"] = "garden_size"
    assert any("garden_size" in e for e in _errors(raw_scoring, bundle3))


def test_the_scorer_refuses_conditions_written_for_another_spec(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u02.a31.c0")
    wrong = scoring3.conditions_for("US:SYNTH2026:A31").model_copy(
        update={"spec_id": "score.other"}
    )
    with pytest.raises(LifeContractError, match="规格"):
        score_of(bundle3, scoring3, s, r, conditions=wrong)
