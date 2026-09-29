"""CLAUDE.md hard rule 1 / README §3.2: the type boundary."""

import math
from dataclasses import replace

import pytest

from common.money import USD_2024_US100, WelfareLevel
from econ.types import (
    ANSWER_SIDE_TYPES,
    AgentInput,
    BoundaryViolationError,
    ChoiceSetTruth,
    GroundTruth,
    Observable,
    ObservableTier,
    Persona,
    PersonaParams,
)


def _observables(core, persona, tier):
    return tuple(core.observe(persona, c.city, 2024, tier) for c in core.cities.values())


def test_agent_input_accepts_the_visible_side(core, persona):
    ai = AgentInput(
        brief=persona.brief,
        options=_observables(core, persona, ObservableTier.O1),
        permutation=(2, 0, 1),
    )
    assert len(ai.options) == 3


@pytest.mark.parametrize(
    "smuggled",
    [
        "ground_truth",
        "persona",
        "params",
        "welfare",
    ],
)
def test_answer_side_objects_cannot_reach_an_agent_input(core, persona, smuggled):
    obj = {
        "ground_truth": core.score(persona, core.cities["C1"].city, 2024),
        "persona": persona,
        "params": persona.params,
        "welfare": WelfareLevel(1.0, USD_2024_US100, "US_MSA_2024"),
    }[smuggled]
    options = list(_observables(core, persona, ObservableTier.O1))
    with pytest.raises(BoundaryViolationError):
        AgentInput(brief=persona.brief, options=(*options, obj))


def test_nested_smuggling_is_also_caught(core, persona):
    """A truth object buried inside a mapping inside a sequence."""
    truth = core.score(persona, core.cities["C1"].city, 2024)
    with pytest.raises(BoundaryViolationError):
        AgentInput(brief=persona.brief, options=({"innocuous": [{"deep": truth}]},))


def test_permutation_must_be_a_real_permutation(core, persona):
    options = _observables(core, persona, ObservableTier.O1)
    with pytest.raises(ValueError, match="permutation"):
        AgentInput(brief=persona.brief, options=options, permutation=(0, 0, 1))


@pytest.mark.parametrize("tier", list(ObservableTier))
def test_every_tier_retains_l0_and_l1(core, persona, tier):
    """README §11.4: collapsed quantities are additional, never a replacement,
    or the design matrix loses rank and the weights cannot be recovered."""
    o = core.observe(persona, core.cities["C1"].city, 2024, tier)
    assert o.l0_amenities and o.l1_economics
    assert set(o.l0_amenities) == {"safety", "sunshine", "commute"}


def test_o1_carries_no_accounting(core, persona):
    o = core.observe(persona, core.cities["C1"].city, 2024, ObservableTier.O1)
    assert o.l2_accounting is None and o.o3_representative_real is None


def test_o2_adds_accounting_and_o3_adds_deflated_income(core, persona):
    o2 = core.observe(persona, core.cities["C1"].city, 2024, ObservableTier.O2)
    o3 = core.observe(persona, core.cities["C1"].city, 2024, ObservableTier.O3)
    assert o2.l2_accounting is not None and o2.o3_representative_real is None
    assert o3.l2_accounting is not None and o3.o3_representative_real is not None


def test_o3_uses_the_representative_basket_not_the_persona_shares(core, persona):
    """这正是 O3 仍属环境而非 L3 的全部理由：它不依赖任何偏好参数。

    若这里误用 persona.shares，O3 就把 A(a; θ) 的一半直接递给了代理，
    对抗档当场失效。
    """
    city = core.cities["C1"].city
    o3 = core.observe(persona, city, 2024, ObservableTier.O3)
    personal_real = core._income(persona, city)["net_income"] / math.exp(
        core._price_index(persona, city)
    )
    assert o3.o3_representative_real["real_income_representative"] != pytest.approx(personal_real)


def test_malformed_tiers_are_rejected(core, persona):
    o = core.observe(persona, core.cities["C1"].city, 2024, ObservableTier.O1)
    with pytest.raises(ValueError, match="O1 carries L0 and L1 only"):
        Observable(
            tier=ObservableTier.O1,
            city=o.city,
            year=2024,
            l0_amenities=o.l0_amenities,
            l1_economics=o.l1_economics,
            l2_accounting={"net_income": 1.0},
        )


def test_exponents_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        PersonaParams(alpha=0.5, beta=0.3, theta={"safety": 0.4})


# --------------------------------------------------------------------------
# 代码审核 F3：两条真实绕过路径
# --------------------------------------------------------------------------


def test_an_answer_object_inside_a_dataclass_field_is_caught(core, persona):
    """**旧实现放行这条。**

    `_reject_answer_side` 过去只递归 Mapping 与 Sequence，不进 dataclass 字段。
    `Observable.l0_amenities` 的注解是 `Mapping[str, float]`，但注解拦不住运行时
    真的塞一个 `PersonaParams` 进去——于是真值藏在一个合法的可见对象**内部**，
    检查照样通过。
    """
    options = list(_observables(core, persona, ObservableTier.O1))
    leaky = replace(options[0], l0_amenities={"safety": 0.5, "leak": persona.params})
    with pytest.raises(BoundaryViolationError, match="l0_amenities"):
        AgentInput(brief=persona.brief, options=(leaky, *options[1:]))


def test_a_choice_set_truth_is_refused(core, persona):
    """**旧实现放行这条。** `ChoiceSetTruth` 当时不在 forbidden 元组里。"""
    cities = tuple(c.city for c in core.cities.values())
    cst = ChoiceSetTruth(
        persona_id=persona.persona_id,
        year=2024,
        cities=cities,
        ln_utility={c.code: -1.0 - i for i, c in enumerate(cities)},
        w_material=persona.params.alpha + persona.params.beta,
        ln_kappa=0.0,
        argmax_city=cities[0],
        numeraire=USD_2024_US100,
        reference="US_MSA_2024",
    )
    with pytest.raises(BoundaryViolationError, match="ChoiceSetTruth"):
        AgentInput(brief=persona.brief, options=(cst,))


def test_every_answer_side_type_is_listed(core, persona):
    """漏掉一个类型，就等于给它开了一扇门。

    这条把"清单是否齐全"变成一个会失败的断言：`econ.types` 里凡是携带真值的
    dataclass，都必须在 `ANSWER_SIDE_TYPES` 里。
    """
    assert set(ANSWER_SIDE_TYPES) == {
        GroundTruth,
        ChoiceSetTruth,
        Persona,
        PersonaParams,
        WelfareLevel,
    }


def test_a_self_referential_payload_does_not_recurse_forever(core, persona):
    """环不该变成 RecursionError——那不是一个边界判定。"""
    cyclic: dict = {"ok": 1.0}
    cyclic["self"] = cyclic
    options = list(_observables(core, persona, ObservableTier.O1))
    leaky = replace(options[0], l0_amenities=cyclic)
    AgentInput(brief=persona.brief, options=(leaky, *options[1:]))


def test_the_visible_side_still_passes_after_the_deeper_walk(core, persona):
    """递归变深之后合法输入必须照常通过，否则这个修复把边界焊死了。"""
    ai = AgentInput(
        brief=persona.brief,
        options=_observables(core, persona, ObservableTier.O3),
        permutation=(1, 2, 0),
    )
    assert len(ai.options) == 3
