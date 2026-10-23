"""生活扩展候选的编译检查（DECISIONS/0041；收尾 R5）：“局部结构合法”不等于“能交给 E1 执行”。判定只用 E1 契约、目录与跨对象
检查；资料不够的候选照实报告，不补价格、寿命、时间或限制。"""

from __future__ import annotations

from pathlib import Path

import pytest

from product import scenarios as sc
from product.life_items import carried_restrictions

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compilation(tmp_path_factory):
    return sc.life_item_compilation(tmp_path_factory.mktemp("life_items"))


def by_id(compilation):
    return {v.candidate_id: v for v in compilation.verdicts}


def test_each_kind_has_a_passing_example_and_a_free_activity_still_passes(compilation):
    v = by_id(compilation)
    for cid in ("food.sichuan_home", "equipment.guitar", "enjoyment.massage", "activity.choir"):
        assert v[cid].status == "executable" and v[cid].reasons_zh == ()


def test_r5_the_four_audit_counterexamples_are_no_longer_executable(compilation):
    """审核复现：确定不可得、没有活动事实、合唱标成食品方案、带年龄限制，原来都返回 executable。"""
    v = by_id(compilation)
    assert v["activity.choir_unavailable"].status == "unavailable"
    assert v["activity.no_facts"].status == "rejected"
    assert "没有活动事实" in v["activity.no_facts"].reasons_zh[0]
    assert v["food.choir_mislabeled"].status == "rejected"
    assert "没有食品方案事实" in v["food.choir_mislabeled"].reasons_zh[0]
    assert v["activity.age_limited"].status == "rejected"
    assert any("age_range" in r for r in v["activity.age_limited"].reasons_zh)


def test_r5_e1_checks_cannot_be_bypassed(compilation):
    v = by_id(compilation)
    assert v["food.wrong_currency"].status == "rejected"
    assert "USD_2023_LOCAL" in v["food.wrong_currency"].reasons_zh[0]
    assert v["activity.undeclared_category"].status == "rejected"
    assert "没有声明" in v["activity.undeclared_category"].reasons_zh[0]


def test_unknown_unavailable_and_unsupported_reasons_stay_apart(compilation):
    v = by_id(compilation)
    assert (v["enjoyment.massage_unknown"].status, v["enjoyment.luxury_spa"].status) == (
        "data_unknown",
        "unsupported",
    )
    assert v["food.fresh_unknown"].status == "data_unknown"  # E1 能执行，但饮食评分会未知
    assert "fresh_meals_per_week" in v["food.fresh_unknown"].reasons_zh[0]
    for cid, needle in (
        ("food.basket_regional", "prep_hours_per_month"),
        ("activity.park_class", "Ages 6-12"),
        ("activity.tasting_dinner", "替代正餐"),
        ("equipment.racket", "lasts_years"),
    ):
        assert v[cid].status == "rejected" and needle in " ".join(v[cid].reasons_zh)


def test_restrictions_carried_by_the_candidate_are_found_and_kept():
    assert carried_restrictions({"eligibility": ["ability:lifeguard"]}) == [
        "资格标签 ['ability:lifeguard']"
    ]
    found = carried_restrictions(
        {"attributes": {"days": "Mon,Wed", "age_restricted": None, "activity_category": "Music"}}
    )
    assert (
        len(found) == 2
        and any("days" in r for r in found)
        and any("age_restricted" in r for r in found)
    )
    assert (
        carried_restrictions(
            {"attributes": {"age_restricted": False}, "spec": {"storage_liters": 30}}
        )
        == []
    )


def test_reasons_carry_no_local_paths(compilation):
    text = compilation.model_dump_json()
    assert str(REPO) not in text and "Users" not in text


def test_the_compilation_has_its_own_content_identity(tmp_path_factory, compilation):
    again = sc.life_item_compilation(tmp_path_factory.mktemp("again"))
    assert again.compilation_fingerprint == compilation.compilation_fingerprint
    assert compilation.compilation_id.endswith(compilation.compilation_fingerprint[:12])
    assert compilation.compiler_version == "e3-life-item-compiler-v2"
