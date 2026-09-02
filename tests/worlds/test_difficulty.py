"""四档难度，以及"存函数不存配对"这条接口纪律。"""

from __future__ import annotations

import pytest

from worlds.difficulty import (
    Tier,
    best_city,
    dominant_city,
    highest_nominal_wage,
    instantiate,
    is_adversarial,
    make_dominant,
    tie_dimension_for,
)
from worlds.personas import PersonaGenerator

# --------------------------------------------------------------------------
# 正交性：这是 M3 最难回头改的一处接口
# --------------------------------------------------------------------------


def test_the_same_personas_go_into_multiple_worlds(worlds, personas, tax):
    """§11.9 的硬约束。做成配对的话，"同一个人在不同世界里表现如何"
    这个问题就问不出来了——你根本没有"同一个人"这个概念。"""
    for p in personas[:4]:
        results = [instantiate(Tier.TRADEOFF, w, p, tax) for w in worlds]
        assert {r.world_id for r in results} == {w.world_id for w in worlds}
        assert {r.persona_id for r in results} == {p.persona_id}


def test_instantiation_is_deterministic(world, persona, tax):
    """存函数的前提：同一组输入永远给出同一个结果，否则不可复现。"""
    for tier in Tier:
        a = instantiate(tier, world, persona, tax)
        b = instantiate(tier, world, persona, tax)
        assert a.utilities(persona) == b.utilities(persona)
        assert a.correct_city == b.correct_city


def test_nothing_pairs_a_persona_into_the_stored_world(world, persona, tax):
    """世界对象本身不含任何人设痕迹；第三、四档的改动只活在返回的 core 里。"""
    before = {c: r.amenities_scaled.copy() for c, r in world.cities.items()}
    instantiate(Tier.EXACT_TIE, world, persona, tax)
    instantiate(Tier.ADVERSARIAL, world, persona, tax)
    after = {c: r.amenities_scaled for c, r in world.cities.items()}
    assert before == after


# --------------------------------------------------------------------------
# 第一档：帕累托占优，与人设无关
# --------------------------------------------------------------------------


def test_dominance_is_persona_independent(world, personas, tax):
    """一座城市若在每个属性上都更好、更便宜、工资更高，那对谁都最优。
    这正是它能当健全性检查的原因（§11.3：过不了即停项目）。"""
    forced = make_dominant(world, world.city_ids[0])
    winner = dominant_city(forced)
    assert winner is not None
    scen = instantiate(Tier.DOMINANT, world, personas[0], tax)
    for p in personas[:8]:
        assert best_city(scen.core, p, scen.cities) == scen.correct_city


def test_a_random_world_rarely_contains_a_dominant_city(worlds):
    assert all(dominant_city(w) is None for w in worlds)


# --------------------------------------------------------------------------
# 第二档：权衡
# --------------------------------------------------------------------------


def test_tradeoff_refuses_a_world_that_has_a_dominant_city(world, persona, tax):
    forced = make_dominant(world, world.city_ids[0])
    with pytest.raises(Exception, match="构不成权衡档"):
        instantiate(Tier.TRADEOFF, forced, persona, tax)


def test_the_answer_depends_on_the_weights(world, personas, tax):
    """权衡档的定义：正确答案取决于权重。若所有人设选同一座，这一档
    就没有在检验偏好推断。"""
    winners = {instantiate(Tier.TRADEOFF, world, p, tax).correct_city.code for p in personas}
    assert len(winners) > 1


# --------------------------------------------------------------------------
# 第三档：精确并列
# --------------------------------------------------------------------------


def test_the_tie_is_exact_to_floating_point(world, personas, tax):
    """§11.6 的 σ 无关性建立在"精确"之上。近似并列时参考熵会随一个
    无法标定的 σ 在几乎整个区间上摆动。"""
    for p in personas[:10]:
        s = instantiate(Tier.EXACT_TIE, world, p, tax)
        u = s.utilities(p)
        a, b = s.tied_cities
        assert abs(u[a.code] - u[b.code]) < 1e-12


def test_the_tie_uses_the_heaviest_dimension(persona):
    """ "不加下限"那条裁决的配套：α=0.5 会造出接近零的权重，而求解要除以
    θ_k。挑最大权重维，每个人设都解得出，且用的是对他最有分辨力的那维。"""
    dim = tie_dimension_for(persona)
    assert persona.params.theta[dim] == max(persona.params.theta.values())


def test_tier_three_has_no_single_correct_answer(world, persona, tax):
    """这一档问的不是"选对了没有"，是"面对真平局会不会集体倒向一边"。"""
    s = instantiate(Tier.EXACT_TIE, world, persona, tax)
    assert s.correct_city is None
    assert len(s.tied_cities) == 2


def test_the_tie_can_be_made_from_either_side(world, personas, tax):
    """两侧回退不是为了"尽量成功"，是为了让第三档的样本不带选择性：
    直接放弃会系统性丢掉属性贴边界的那批人设。"""
    adjusted = [instantiate(Tier.EXACT_TIE, world, p, tax) for p in personas]
    lowered = sum(1 for s in adjusted if s.adjusted_city == s.tied_cities[0])
    raised = sum(1 for s in adjusted if s.adjusted_city == s.tied_cities[1])
    assert lowered > 0, "从未压过领先者，说明回退分支没被走到"
    assert raised > 0


def test_every_persona_can_be_given_a_tie(world, tax):
    """有系统性的失败就意味着第三档的方差不是总体的方差。"""
    for p in PersonaGenerator().generate(60, seed=11):
        instantiate(Tier.EXACT_TIE, world, p, tax)


# --------------------------------------------------------------------------
# 第四档：对抗
# --------------------------------------------------------------------------


def test_nominal_wage_and_welfare_disagree(world, personas, tax):
    for p in personas[:10]:
        s = instantiate(Tier.ADVERSARIAL, world, p, tax)
        assert is_adversarial(s.core, p, s.cities)
        assert highest_nominal_wage(s.core, p, s.cities) != s.correct_city


def test_adversarial_moves_rent_not_wage(world, persona, tax):
    """只动房租：动工资会改变"谁是名义工资最高的城市"，那不是在构造
    对抗，是在换题目。"""
    s = instantiate(Tier.ADVERSARIAL, world, persona, tax)
    for code, rec in world.cities.items():
        assert s.core.cities[code].wage_by_quantile == rec.wage_by_quantile
