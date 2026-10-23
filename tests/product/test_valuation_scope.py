"""收尾 R8：期末估值的范围限制进入公开结果。参考模板只安排了音乐练习：期末持有的吉他按能力继续用，期末持有的网球拍“模板没有
考虑这个用途”——这是参考模板的范围限制，不是说装备没有价值；V 的算法与 C / E / M 分类不变。离线 mock，不调用模型。"""

from __future__ import annotations

import json

import pytest

from common.life_planning_contracts import MockScript
from product import scenarios as sc


@pytest.fixture(scope="module")
def gear(tmp_path_factory):
    """u512 的 K51 分身在第 3 年第 1 月买下目录里的爱好装备（吉他 3 年、网球拍 5 年）：期末吉他还剩 24 个月、球拍还剩 48 个月。"""
    s = sc.closed_loop(
        tmp_path_factory.mktemp("gear"),
        "hobby_gear",
        person="u512",
        scripts=(MockScript(clone_id="u512.k51.r0", behavior="buy_hobby_gear", years=(3,)),),
    )
    assert s.result is not None
    return s.result


def test_r8_held_equipment_with_and_without_a_template_use_is_stated(gear):
    k51 = next(c for c in gear.clones if c.clone_ref == "u512.k51.r0")
    notes = {n.opportunity_id: n for n in k51.valuation_scope}
    guitar, racket = notes["k51.durable.guitar"], notes["k51.durable.tennis_racket"]
    assert (guitar.kind, guitar.capabilities, guitar.months_left) == (
        "equipment_use_valued",
        ("guitar_playable",),
        24,
    )
    assert (racket.kind, racket.capabilities, racket.months_left) == (
        "equipment_use_not_in_templates",
        ("racket_playable",),
        48,
    )
    assert "参考模板没有考虑" in racket.text_zh and "不是说这件装备没有价值" in racket.text_zh
    m52 = next(c for c in gear.clones if c.clone_ref == "u512.m52.r0")
    assert m52.valuation_scope == ()  # 没有期末装备的分身没有这一栏


def test_r8_the_scope_note_changes_no_classification_and_leaks_nothing(gear):
    k51 = next(c for c in gear.clones if c.clone_ref == "u512.k51.r0")
    assert k51.category == "C"  # 有限模板没覆盖某种用途，不归 M
    text = json.dumps([n.model_dump(mode="json") for n in k51.valuation_scope], ensure_ascii=False)
    for leak in ("eta", "match_bonus", "v_plan", "p1_current_basic", "plans", "未来"):
        assert leak not in text
