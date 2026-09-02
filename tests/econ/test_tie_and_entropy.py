"""README §11.6：第三档为什么必须用精确并列构造。

离散度比需要一个参考熵，参考熵需要真值侧有一个 P(j) ∝ exp(U_j/σ) 的随机
选择规则，而 σ 没有客观取值。解决办法不是挑一个 σ，而是把这一档构造成
让 σ 自动消掉。这些用例断言它真的消掉了，以及若当初用的是"近似无差异"
就不会消掉。
"""

from __future__ import annotations

import math

import pytest

from tests.econ.conftest import softmax_entropy

SIGMAS = [1e-4, 1e-3, 1e-2, 0.1, 1.0, 10.0]


@pytest.mark.parametrize("sigma", SIGMAS)
@pytest.mark.parametrize("n", [2, 3, 5])
def test_exact_ties_give_log_n_for_every_sigma(sigma, n):
    """整档赖以成立的命题：σ 从主指标里消失。"""
    assert softmax_entropy([1.0] * n, sigma) == pytest.approx(math.log(n), abs=1e-12)


def test_near_ties_do_not_have_this_property():
    """若当初做成"近似无差异"，参考熵会随一个无法标定的参数在几乎整个
    区间上摆动，坍缩阈值就成了任意的。"""
    entropies = [softmax_entropy([1.0, 1.0002], s) for s in [1e-5, *SIGMAS]]
    assert min(entropies) < 0.05
    assert max(entropies) > 0.69
    assert max(entropies) - min(entropies) > 0.6


def test_the_solver_produces_a_tie_exact_to_floating_point(core, persona, city_a, city_b):
    """§11.3 第三档的构造必须是代数反解，残差是浮点噪声而非求解器容差。"""
    needed = core.solve_amenity_for_tie(persona, city_a, city_b, "safety")
    tied = core.with_amenity(city_b, "safety", needed)

    u_a = tied.score(persona, city_a, 2024).ln_utility
    u_b = tied.score(persona, city_b, 2024).ln_utility
    assert u_b == pytest.approx(u_a, abs=1e-12)


def test_a_solved_tie_yields_the_invariant_reference_entropy(core, persona, city_a, city_b):
    needed = core.solve_amenity_for_tie(persona, city_a, city_b, "safety")
    tied = core.with_amenity(city_b, "safety", needed)
    us = [tied.score(persona, c, 2024).utility.value for c in (city_a, city_b)]
    for sigma in SIGMAS:
        assert softmax_entropy(us, sigma) == pytest.approx(math.log(2), abs=1e-6)


def test_the_tie_is_exact_in_feature_space_too(core, persona, city_a, city_b):
    """并列必须在 M5 看到的表示里也精确，否则 §11.6 的 σ 无关性失效。

    这成立是因为 κ 跨城不变（断言三）：ln V 相等 ⟺ Σβx 相等。两条断言
    是咬合的，不是各自独立的。
    """
    needed = core.solve_amenity_for_tie(persona, city_a, city_b, "safety")
    tied = core.with_amenity(city_b, "safety", needed)
    a = tied.score(persona, city_a, 2024)
    b = tied.score(persona, city_b, 2024)
    ua = sum(a.coefficients[k] * a.features[k] for k in a.features)
    ub = sum(b.coefficients[k] * b.features[k] for k in b.features)
    assert a.ln_kappa == pytest.approx(b.ln_kappa, rel=1e-15)
    assert ua == pytest.approx(ub, abs=1e-12)


def test_the_solver_is_persona_specific(core, persona, city_a, city_b):
    """并列是对某一个人的并列。换个权重就要重解，所以第三档按人设构造，
    不是每个世界构造一次。"""
    from econ.types import Persona, PersonaParams

    other = Persona(
        persona_id=persona.persona_id,
        params=PersonaParams(
            alpha=0.33, beta=0.17, theta={"safety": 0.30, "sunshine": 0.10, "commute": 0.10}
        ),
        shares=persona.shares,
        occupation=persona.occupation,
        ability_quantile=persona.ability_quantile,
        brief=persona.brief,
    )
    v1 = core.solve_amenity_for_tie(persona, city_a, city_b, "safety")
    v2 = core.solve_amenity_for_tie(other, city_a, city_b, "safety")
    assert v1 != pytest.approx(v2, rel=1e-6)


def test_an_unreachable_tie_refuses_rather_than_clamping(core, persona, city_a, city_b):
    """夹回 [0,1] 边界会把精确并列变成近似并列，而整个 §11.6 建立在
    "精确"之上。所以够不着时报错，不夹。"""
    hopeless = core.with_amenity(city_b, "safety", 0.79)
    hopeless = hopeless.with_record(
        city_a,
        type(hopeless.record(city_a))(
            city=hopeless.record(city_a).city,
            rpp={"rent": 0.10, "goods": 0.10, "other": 0.10},
            wage_by_quantile={
                q: w * 12 for q, w in hopeless.record(city_a).wage_by_quantile.items()
            },
            amenities_scaled=hopeless.record(city_a).amenities_scaled,
            jurisdiction=hopeless.record(city_a).jurisdiction,
        ),
    )
    with pytest.raises(ValueError, match=r"超出 \[0,1\]"):
        hopeless.solve_amenity_for_tie(persona, city_a, city_b, "safety")


def test_a_zero_weight_dimension_cannot_carry_a_tie(core, persona, city_a, city_b):
    """该维对这个人不影响效用，用它构造并列是无解的。"""
    from econ.types import Persona, PersonaParams

    indifferent = Persona(
        persona_id=persona.persona_id,
        params=PersonaParams(
            alpha=0.33, beta=0.17, theta={"safety": 0.0, "sunshine": 0.25, "commute": 0.25}
        ),
        shares=persona.shares,
        occupation=persona.occupation,
        ability_quantile=persona.ability_quantile,
        brief=persona.brief,
    )
    with pytest.raises(ValueError, match="权重为零"):
        core.solve_amenity_for_tie(indifferent, city_a, city_b, "safety")


def test_dominance_is_detectable_before_any_model_is_called(core, persona, city_a, city_b):
    """第一档是健全性检查：harness 若看不出帕累托占优，项目就停（§11.3）。"""
    crippled = core.with_amenity(city_a, "safety", 0.05)
    crippled = crippled.with_amenity(city_a.__class__("US", "SYNTH", "C1"), "commute", 0.05)
    assert (
        crippled.score(persona, city_b, 2024).ln_utility
        > crippled.score(persona, city_a, 2024).ln_utility
    )
