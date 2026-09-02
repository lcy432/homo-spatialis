"""README §10.2：自动泄漏测试，改为数值比对之后的版本。

三层防线，这里检验后两层：

  一、类型边界（test_boundary.py）：AgentInput 拒收答案侧对象。
  二、数值扫描：真值的数出现在 prompt 里，无论四舍五入还是单位换算。
  三、向量指纹与顺序检验：整组权重被倾倒，或者秩序信息藏在叙述次序里。

每一层都拿一个能被它抓到的坏例子验证过。
"""

from __future__ import annotations

import pytest

from common.leakcheck import (
    LeakError,
    assert_no_leak,
    assert_no_weight_vector,
    extract_numbers,
    find_leaks,
    weight_vector_fingerprint,
)
from econ.types import ObservableTier
from tests.econ.conftest import render_prompt


def _prompt(core, persona, tier=ObservableTier.O1):
    obs = [core.observe(persona, rec.city, 2024, tier) for rec in core.cities.values()]
    truths = [core.score(persona, rec.city, 2024) for rec in core.cities.values()]
    return render_prompt(persona.brief, obs), obs, truths


@pytest.mark.parametrize("tier", list(ObservableTier))
def test_a_clean_prompt_leaks_nothing_at_any_tier(core, persona, tier):
    prompt, obs, truths = _prompt(core, persona, tier)
    for truth in truths:
        assert_no_leak(prompt, truth, allowed=obs)


def test_float_representation_drift_no_longer_hides_a_leak():
    """改成数值比对的全部理由。

    0.2 存成 0.200000000000000011；一个由 §7.2 映射算出来的同一权重可能是
    0.19999999999999998。字符串搜索找 "0.2" 会漏掉后者，而这两个数在任何
    有意义的尺度上都是同一个数。
    """
    drifted = 0.19999999999999998
    assert repr(drifted) != "0.2"  # 前提成立：字符串确实对不上
    assert find_leaks("the weight is 0.2", {"w": drifted})


def test_percentage_rendering_is_caught():
    """0.20 与 20 是同一个权重。定和量表本来就是按一百分分配的。"""
    assert find_leaks("allocated 20 points to commuting", {"w": 0.20})
    assert find_leaks("commute weight is 0.20", {"w": 20.0})


@pytest.mark.parametrize("factor", [12.0, 1.0 / 12.0, 1000.0, 0.001])
def test_enumerated_unit_conversions_are_caught(factor):
    """月薪与年薪、千元与元。枚举，不穷尽——见模块文档。"""
    secret = 4321.5
    assert find_leaks(f"the figure is {secret * factor:.6g}", {"v": secret})


def test_an_unenumerated_conversion_is_a_documented_blind_spot():
    """诚实地记录边界：乘以 7 的泄漏抓不到。

    这条测试断言的是一个**局限**而不是一个能力。它存在是为了让这个局限
    在有人依赖这个扫描之前就被看见。
    """
    assert not find_leaks("the figure is 30250.5", {"v": 4321.5})


def test_universal_values_are_not_evidence():
    for value in (0.0, 1.0, 2.0, 100.0):
        assert not find_leaks(f"there are {value:g} cities in the list", {"v": value})


def test_a_leaked_utility_is_caught(core, persona):
    prompt, obs, truths = _prompt(core, persona)
    leaked = prompt + f"\n(internal: score {truths[0].utility.value})"
    with pytest.raises(LeakError, match="硬规则 1"):
        assert_no_leak(leaked, truths[0], allowed=obs)


def test_a_rounded_leak_is_caught(core, persona):
    """四舍五入才是现实里的泄漏形态：没人会把 float64 原样贴进 prompt。"""
    prompt, obs, truths = _prompt(core, persona)
    leaked = prompt + f"\nwellbeing index: {truths[0].amenity_index:.3f}"
    assert find_leaks(leaked, truths[0], allowed=obs)


def test_a_value_the_observable_carries_is_not_a_false_positive(core, persona):
    """真值字段与可见属性撞上同一个数是巧合，不是泄漏。

    不做这个排除，检查会天天喊狼来了，然后在一周内被人关掉。
    """
    prompt, obs, _ = _prompt(core, persona)
    shared = obs[0].l1_economics["wage"]
    assert not find_leaks(prompt, {"coincidence": shared}, allowed=obs)
    assert find_leaks(prompt, {"coincidence": shared})  # 不排除时同一个数就报


def test_the_ground_truth_features_do_not_reach_the_prompt(core, persona):
    """GroundTruth 现在带着 features 与 coefficients，扫描要一并覆盖。

    coefficients 里就是 ±w₁ 与 θ_k —— 注入的权重本身。它们泄漏等于把
    答案直接写在题面上。
    """
    prompt, obs, truths = _prompt(core, persona)
    for truth in truths:
        assert_no_leak(prompt, truth.features, allowed=obs)
        assert_no_leak(prompt, dict(truth.coefficients), allowed=obs)


# --------------------------------------------------------------------------
# 向量指纹：单值扫描放过的一整类泄漏
# --------------------------------------------------------------------------


def test_a_dumped_weight_vector_is_caught(persona):
    weights = [persona.params.alpha, persona.params.beta, *persona.params.theta.values()]
    dumped = "my priorities: " + ", ".join(f"{w:.4f}" for w in weights)
    hit = weight_vector_fingerprint(dumped, weights)
    assert hit is not None
    assert hit.scale == 1.0
    assert hit.total == pytest.approx(1.0, abs=1e-3)


def test_a_dumped_weight_vector_in_percentage_form_is_caught(persona):
    weights = [persona.params.alpha, persona.params.beta, *persona.params.theta.values()]
    dumped = "out of 100: " + ", ".join(f"{w * 100:.1f}" for w in weights)
    hit = weight_vector_fingerprint(dumped, weights)
    assert hit is not None
    assert hit.scale == 100.0


def test_the_ordinary_persona_prose_carries_no_weight_vector(persona):
    weights = [persona.params.alpha, persona.params.beta, *persona.params.theta.values()]
    assert_no_weight_vector(persona.brief.narrative, weights)


def test_single_value_scanning_would_have_missed_the_dump(core, persona):
    """指纹检查存在的理由。

    逐个比对时，个别权重可能因为白名单或普遍值被放过；但七个数一起
    出现、且作为多重集与真值匹配，这个模式本身就是证据。
    """
    weights = [persona.params.alpha, persona.params.beta, *persona.params.theta.values()]
    dumped = "weights: " + " ".join(f"{w:.4f}" for w in weights)
    assert weight_vector_fingerprint(dumped, weights) is not None
    with pytest.raises(LeakError, match="整组"):
        assert_no_weight_vector(dumped, weights)


def test_number_extraction_handles_the_formats_that_appear_in_prompts():
    got = extract_numbers("wage = 92,000.0 and rpp 1.31, share 34%, sci 1.2e3, neg -4.5")
    assert 92000.0 in got
    assert 1.31 in got
    assert 34.0 in got
    assert 1200.0 in got
    assert -4.5 in got


def test_integer_percentage_weights_are_caught_by_the_fingerprint(persona):
    """§6.3 说人会给 10、20、30。整数百分数是最可能的泄漏形态。

    整数化在量级 13 上的相对误差有 3.8e-2，用单值扫描的 1e-3 去找会全部
    漏掉；指纹用的是松两个量级的容差，靠"七个同时命中"换取强度。
    """
    weights = [persona.params.alpha, persona.params.beta, *persona.params.theta.values()]
    dumped = "I'd split it: " + ", ".join(f"{round(w * 100)}" for w in weights)
    hit = weight_vector_fingerprint(dumped, weights)
    assert hit is not None
    assert hit.scale == 100.0


def test_the_loose_fingerprint_tolerance_does_not_fire_on_unrelated_numbers(persona):
    """松容差换来的误报风险，用"必须全部命中"控制住。"""
    weights = [persona.params.alpha, persona.params.beta, *persona.params.theta.values()]
    noise = "rents 1.31 1.04 0.88, wages 92000 80500 71000, sunshine 0.58 0.66 0.83"
    assert weight_vector_fingerprint(noise, weights) is None
