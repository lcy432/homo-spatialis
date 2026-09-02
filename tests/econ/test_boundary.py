"""CLAUDE.md hard rule 1 / README §3.2: the type boundary."""

import pytest

from common.money import USD_2024_US100, WelfareLevel
from econ.types import (
    AgentInput,
    BoundaryViolationError,
    Observable,
    ObservableTier,
    PersonaParams,
)


def _observables(core, persona, tier):
    return tuple(core.observe(persona, c.city_id, 2024, tier) for c in core.cities.values())


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
        "ground_truth": core.score(persona, core.cities["C1"].city_id, 2024),
        "persona": persona,
        "params": persona.params,
        "welfare": WelfareLevel(1.0, USD_2024_US100, "US_MSA_2024"),
    }[smuggled]
    options = list(_observables(core, persona, ObservableTier.O1))
    with pytest.raises(BoundaryViolationError):
        AgentInput(brief=persona.brief, options=(*options, obj))


def test_nested_smuggling_is_also_caught(core, persona):
    """A truth object buried inside a mapping inside a sequence."""
    truth = core.score(persona, core.cities["C1"].city_id, 2024)
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
    o = core.observe(persona, core.cities["C1"].city_id, 2024, tier)
    assert o.l0_amenities and o.l1_economics
    assert set(o.l0_amenities) == {"crime_rate", "sunshine_hours", "commute_minutes"}


def test_o1_carries_no_accounting(core, persona):
    o = core.observe(persona, core.cities["C1"].city_id, 2024, ObservableTier.O1)
    assert o.l2_accounting is None and o.o3_representative_real is None


def test_o2_adds_accounting_and_o3_adds_deflated_income(core, persona):
    o2 = core.observe(persona, core.cities["C1"].city_id, 2024, ObservableTier.O2)
    o3 = core.observe(persona, core.cities["C1"].city_id, 2024, ObservableTier.O3)
    assert o2.l2_accounting is not None and o2.o3_representative_real is None
    assert o3.l2_accounting is not None and o3.o3_representative_real is not None


def test_o3_uses_the_representative_basket_not_the_persona_shares(core, persona):
    """That is why O3 is still environment and not L3 in disguise: it does not
    depend on this person's preference parameters."""
    o3 = core.observe(persona, core.cities["C1"].city_id, 2024, ObservableTier.O3)
    personal_p = core.price_index(persona, core.cities["C1"].city_id)
    net = core.net_income(persona, core.cities["C1"].city_id)
    assert o3.o3_representative_real["real_income_representative"] != pytest.approx(
        net / personal_p
    )


def test_malformed_tiers_are_rejected(core, persona):
    o = core.observe(persona, core.cities["C1"].city_id, 2024, ObservableTier.O1)
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
