"""城市背景事实（DECISIONS/0030 第三节）：可见事实与内部评分输入分开，并且逐项对得上。

代理看到的是事实表（定义、数值或类别、单位、已知 / 确定不存在 / 资料未知、时间口径与来源性质）；事实怎样换成评分条件里的 b 是
内部对应。加载器在开跑前核对两边说的是同一座城：定义各城相同、已知事实按换算正好得到评分条件的 b、未知不补零、有城市背景的
分项都有可见事实。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agentlayer.life_planning import visible_city_facts
from agentlayer.life_views import assert_payload_clean
from common.life_city_facts import FactScoringLink
from common.life_contracts import LifeContractError
from ledger.life_city_facts import alignment_report, city_facts_problems, load_city_facts
from ledger.life_roster import load_roster

from .planning_support import FACTS, facts_variant

ROSTER = Path("configs/life/roster_product.yaml")


@pytest.fixture(scope="module")
def loaded():
    _config, bundle, scoring, _specs = load_roster(ROSTER)
    return bundle, scoring


def _problems(tmp_path, loaded, mutate) -> list[str]:
    bundle, scoring = loaded
    path = facts_variant(tmp_path, mutate)
    import yaml

    from ledger.life_city_facts import parse_city_facts

    facts = parse_city_facts(yaml.safe_load(path.read_text(encoding="utf-8"))["city_facts"])
    return city_facts_problems(facts, bundle, scoring)


def test_the_sample_facts_reproduce_every_background_value_exactly(loaded):
    bundle, scoring = loaded
    facts = load_city_facts(FACTS, bundle, scoring)
    rep = alignment_report(facts, bundle, scoring)
    assert (
        rep["problems"] == []
        and len(rep["rows"]) == 8
        and all(r["consistent"] for r in rep["rows"])
    )
    # 安全指数 6 → b = 0.5 × 6 = 3（与评分条件逐位相等，精确分数）
    row = next(r for r in rep["rows"] if r["fact_id"] == "safety_index")
    assert (row["fact_number"], row["expected_base"], row["condition_base"]) == (6.0, "3", "3")


def test_a_fact_that_disagrees_with_the_scoring_condition_is_refused(tmp_path, loaded):
    def mutate(d):
        d["sheets"][1]["facts"][3]["number"] = 8  # M52 环境指数 8 → b = 4，评分条件写的是 3

    problems = _problems(tmp_path, loaded, mutate)
    assert any("M52" in p and "environment" in p and "评分条件写的是 b = 3" in p for p in problems)


def test_unknown_is_not_zero_on_either_side(tmp_path, loaded):
    def mutate(d):
        f = d["sheets"][1]["facts"][0]
        f.update(status="unknown", number=None, unknown_reason="资料还没整理")

    problems = _problems(tmp_path, loaded, mutate)
    assert any("资料未知不能在评分侧补成一个数" in p for p in problems)


def test_the_same_fact_must_mean_the_same_thing_in_every_city(tmp_path, loaded):
    def mutate(d):
        d["sheets"][1]["facts"][1]["unit"] = "合成指数（0–100）"

    problems = _problems(tmp_path, loaded, mutate)
    assert any("development_index" in p and "定义不同" in p and "unit" in p for p in problems)


def test_a_city_without_a_fact_sheet_cannot_be_compared(tmp_path, loaded):
    def mutate(d):
        d["sheets"] = d["sheets"][:1]

    problems = _problems(tmp_path, loaded, mutate)
    assert any("M52 没有城市事实表" in p for p in problems)


def test_a_background_without_a_visible_fact_is_refused(tmp_path, loaded):
    def mutate(d):
        d["links"] = [x for x in d["links"] if x["component_id"] != "environment"]

    problems = _problems(tmp_path, loaded, mutate)
    assert any("environment" in p and "没有对应的可见事实" in p for p in problems)


def test_definitely_absent_needs_a_declared_value(tmp_path, loaded):
    def mutate(d):
        f = d["sheets"][0]["facts"][2]
        f.update(status="unavailable", number=None)

    problems = _problems(tmp_path, loaded, mutate)
    assert any("确定不存在" in p and "不能悄悄当成 0" in p for p in problems)


def test_a_conversion_is_never_an_observation():
    with pytest.raises(ValidationError, match="换算是假设或规则，不是观测"):
        FactScoringLink.model_validate(
            {
                "fact_id": "safety_index",
                "component_id": "safety",
                "rule": "linear_base_v1",
                "factor": "0.5",
                "source": {
                    "kind": "observed",
                    "observed_on": "2026-10-12",
                    "geography_scope": "x",
                    "mapping_version": "x",
                    "provenance": {
                        "source_url": "https://example.org",
                        "source_table": "t",
                        "source_cell": "c",
                        "retrieved_date": "2026-10-12",
                    },
                },
            }
        )


def test_the_visible_projection_has_no_conversion_and_links_cannot_enter_a_prompt(loaded):
    bundle, scoring = loaded
    facts = load_city_facts(FACTS, bundle, scoring)
    visible = visible_city_facts(facts.sheets[0])
    dumped = [v.model_dump(mode="json") for v in visible]
    assert all("factor" not in v and "component_id" not in v for v in dumped)
    assert visible[0].source_label.startswith("合成设定")
    assert_payload_clean(visible)
    for bad in (facts.links[0], facts, {"factor": "0.5"}):
        with pytest.raises(LifeContractError):
            assert_payload_clean({"observation": bad})


def test_load_refuses_with_every_problem_listed(tmp_path, loaded):
    bundle, scoring = loaded

    def mutate(d):
        d["sheets"][1]["facts"][3]["number"] = 8
        d["sheets"][1]["facts"][1]["unit"] = "别的单位"

    path = facts_variant(tmp_path, mutate)
    with pytest.raises(LifeContractError) as exc:
        load_city_facts(path, bundle, scoring)
    assert "environment" in str(exc.value) and "定义不同" in str(exc.value)
