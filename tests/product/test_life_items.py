"""生活扩展候选的编译检查（DECISIONS/0041）：只用 E1 契约与词表判定；资料不够的候选照实拒绝，不补价格、寿命或时间。"""

from __future__ import annotations

from pathlib import Path

import pytest

from product import scenarios as sc

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def compilation(tmp_path_factory):
    return sc.life_item_compilation(tmp_path_factory.mktemp("life_items"))


def test_each_candidate_gets_its_own_verdict(compilation):
    status = {v.candidate_id: v.status for v in compilation.verdicts}
    assert status == {
        "food.sichuan_home": "executable",
        "activity.choir": "executable",
        "equipment.guitar": "executable",
        "food.basket_regional": "rejected",
        "activity.park_class": "rejected",
        "activity.tasting_dinner": "rejected",
        "equipment.racket": "rejected",
        "enjoyment.luxury_spa": "unsupported",
        "enjoyment.massage_unknown": "data_unknown",
    }


def test_reasons_name_the_missing_fact_without_local_paths(compilation):
    why = {v.candidate_id: " ".join(v.reasons_zh) for v in compilation.verdicts}
    assert "prep_hours_per_month" in why["food.basket_regional"]  # 没有做饭时间：不补
    assert "日期窗口" in why["activity.park_class"] and "Ages 6-12" in why["activity.park_class"]
    assert "替代正餐" in why["activity.tasting_dinner"]
    assert "lasts_years" in why["equipment.racket"]
    assert "luxury_spa" in why["enjoyment.luxury_spa"]
    text = compilation.model_dump_json()
    assert str(REPO) not in text and "Users" not in text


def test_the_compilation_has_its_own_content_identity(tmp_path_factory, compilation):
    again = sc.life_item_compilation(tmp_path_factory.mktemp("again"))
    assert again.compilation_fingerprint == compilation.compilation_fingerprint
    assert compilation.compilation_id.endswith(compilation.compilation_fingerprint[:12])
