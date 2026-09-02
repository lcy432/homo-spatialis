"""三个对照组，以及零调用成本的估计器自检数据。"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from worlds.controls import permute_attributes, shuffle_briefs, strip_persona
from worlds.difficulty import Tier, best_city, instantiate
from worlds.personas import PersonaGenerator
from worlds.selfcheck import (
    choice_probabilities,
    hit_rate_of_truth,
    scenario_design,
    simulate_choices,
    stack_for_estimation,
)


def _tagged(personas):
    """给每个人设一段可辨认的散文，好看出错配有没有真的发生。"""
    return [
        replace(p, brief=replace(p.brief, narrative=f"voice-of-{p.persona_id}")) for p in personas
    ]


# --------------------------------------------------------------------------
# 对照组一：属性置换
# --------------------------------------------------------------------------


def test_attributes_swap_but_labels_and_jurisdictions_stay(world):
    a, b = world.city_ids[0], world.city_ids[1]
    swapped, record = permute_attributes(world, a, b)
    assert record.a == a and record.b == b
    assert swapped.cities[a.code].rpp == world.cities[b.code].rpp
    assert swapped.cities[b.code].rpp == world.cities[a.code].rpp
    # 标签与辖区留在原地：辖区跟着属性走的话就成了整体对调，对照失去意义
    assert swapped.cities[a.code].city == a
    assert swapped.cities[a.code].jurisdiction == world.cities[a.code].jurisdiction


def test_the_truth_follows_the_attributes(world, persona, tax):
    """真值必须跟属性走。若连真值都跟标签走，这个对照就测不出任何东西。"""
    base = instantiate(Tier.TRADEOFF, world, persona, tax)
    a, b = world.city_ids[0], world.city_ids[1]
    swapped, _ = permute_attributes(world, a, b)
    after = instantiate(Tier.TRADEOFF, swapped, persona, tax)
    if base.correct_city == a:
        assert after.correct_city == b
    elif base.correct_city == b:
        assert after.correct_city == a
    else:
        assert after.correct_city == base.correct_city


# --------------------------------------------------------------------------
# 对照组二：无人设
# --------------------------------------------------------------------------


def test_stripping_the_brief_keeps_the_truth_intact(persona):
    """拿掉的只是代理能读到的那部分。权重必须留着，否则没有真值可比。"""
    bare = strip_persona(persona)
    assert bare.brief.narrative == ""
    assert bare.brief.dimension_order == ()
    assert bare.params == persona.params
    assert bare.shares == persona.shares


# --------------------------------------------------------------------------
# 对照组三：打乱人设（三个里最锋利的一个）
# --------------------------------------------------------------------------


def test_every_persona_gets_someone_elses_narrative(personas):
    """用导错排而不是普通洗牌：碰巧拿回自己散文的样本既不属于匹配组也
    不属于打乱组，只会稀释对比度。"""
    tagged = _tagged(personas)
    shuffled = shuffle_briefs(tagged, seed=5)
    assert len(shuffled) == len(tagged)
    for original, mixed in zip(tagged, shuffled, strict=True):
        assert mixed.persona_id == original.persona_id
        assert mixed.brief.persona_id == original.persona_id
        assert mixed.params == original.params
        assert mixed.brief.narrative != original.brief.narrative


def test_shuffling_needs_at_least_two_personas(personas):
    with pytest.raises(ValueError, match="至少要两个"):
        shuffle_briefs(personas[:1], seed=1)


def test_shuffling_is_deterministic_in_the_seed(personas):
    tagged = _tagged(personas)
    a = shuffle_briefs(tagged, seed=9)
    b = shuffle_briefs(tagged, seed=9)
    assert [x.brief.narrative for x in a] == [x.brief.narrative for x in b]


# --------------------------------------------------------------------------
# 估计器自检的数据
# --------------------------------------------------------------------------


def test_the_design_comes_from_m2_not_a_second_implementation(world, persona, tax):
    scen = instantiate(Tier.TRADEOFF, world, persona, tax)
    codes, columns, design, coefficients = scenario_design(scen, persona)
    truth = scen.core.score(persona, scen.cities[0], 2024)
    assert columns == tuple(sorted(truth.features))
    assert design.shape == (len(scen.cities), len(columns))
    assert coefficients.shape == (len(columns),)
    assert len(codes) == len(scen.cities)


def test_the_linear_index_ranks_the_same_as_the_utility(world, personas, tax):
    """Σβx 与 ln V 只差一个跨城不变的 ln κ，所以排序必须一致。
    这条同时是 M2 断言三（κ 跨城不变）的下游确认。"""
    for p in personas[:6]:
        scen = instantiate(Tier.TRADEOFF, world, p, tax)
        _, _, design, coefficients = scenario_design(scen, p)
        idx = int(np.argmax(design @ coefficients))
        assert scen.cities[idx] == best_city(scen.core, p, scen.cities)


def test_probabilities_are_a_distribution(world, persona, tax):
    scen = instantiate(Tier.TRADEOFF, world, persona, tax)
    _, _, design, coefficients = scenario_design(scen, persona)
    p = choice_probabilities(design, coefficients, sigma=0.05)
    assert p.sum() == pytest.approx(1.0)
    assert (p >= 0).all()


def test_an_exact_tie_splits_evenly_for_any_sigma(world, personas, tax):
    """§11.6：并列时 softmax 对任何 σ 都给均分，σ 从主指标里消失。"""
    for p in personas[:5]:
        scen = instantiate(Tier.EXACT_TIE, world, p, tax)
        codes, _, design, coefficients = scenario_design(scen, p)
        a, b = (codes.index(c.code) for c in scen.tied_cities)
        for sigma in (1e-3, 0.05, 1.0, 50.0):
            probs = choice_probabilities(design, coefficients, sigma)
            assert probs[a] == pytest.approx(probs[b], rel=1e-9)


def test_simulation_is_deterministic_and_needs_no_api(world, tax):
    ps = PersonaGenerator().generate(30, seed=3)
    lookup = {p.persona_id: p for p in ps}
    scen = [instantiate(Tier.TRADEOFF, world, p, tax) for p in ps]
    a = simulate_choices(scen, lookup, sigma=0.05, seed=11)
    b = simulate_choices(scen, lookup, sigma=0.05, seed=11)
    assert [o.chosen_index for o in a] == [o.chosen_index for o in b]


def test_more_noise_means_fewer_hits(world, tax):
    """σ 在这里是数据生成过程的已知参数，不是 §11.6 那个 σ。"""
    ps = PersonaGenerator().generate(150, seed=3)
    lookup = {p.persona_id: p for p in ps}
    scen = [instantiate(Tier.TRADEOFF, world, p, tax) for p in ps]
    quiet = hit_rate_of_truth(simulate_choices(scen, lookup, sigma=0.02, seed=1))
    loud = hit_rate_of_truth(simulate_choices(scen, lookup, sigma=0.20, seed=1))
    assert quiet > loud


def test_the_truth_model_does_not_hit_one_hundred_percent(world, tax):
    """语言模型的命中率要跟这个数比，不是跟 1 比。拿 1 当基准会把 σ 带来
    的不可避免的失误算到模型头上。"""
    ps = PersonaGenerator().generate(100, seed=3)
    lookup = {p.persona_id: p for p in ps}
    scen = [instantiate(Tier.TRADEOFF, world, p, tax) for p in ps]
    rate = hit_rate_of_truth(simulate_choices(scen, lookup, sigma=0.05, seed=1))
    assert 0.4 < rate < 1.0


def test_mismatched_columns_are_refused_rather_than_aligned(world, tax):
    """悄悄对齐是"不会报错的失败"：列错位后系数安静地挂到错误的维度上。"""
    ps = PersonaGenerator().generate(4, seed=3)
    lookup = {p.persona_id: p for p in ps}
    scen = [instantiate(Tier.TRADEOFF, world, p, tax) for p in ps]
    obs = simulate_choices(scen, lookup, seed=1)
    with pytest.raises(ValueError, match="列名不一致"):
        stack_for_estimation([obs[0], replace(obs[1], columns=("wrong",))])


def test_stacking_gives_m5_the_shapes_it_needs(world, tax):
    ps = PersonaGenerator().generate(20, seed=3)
    lookup = {p.persona_id: p for p in ps}
    scen = [instantiate(Tier.TRADEOFF, world, p, tax) for p in ps]
    design, chosen, truth, columns = stack_for_estimation(simulate_choices(scen, lookup, seed=1))
    assert design.shape == (20, len(scen[0].cities), len(columns))
    assert chosen.shape == (20,)
    assert truth.shape == (20, len(columns))
