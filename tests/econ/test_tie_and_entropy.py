"""README §11.6: why the third difficulty tier is built from exact ties.

The dispersion ratio needs a reference entropy, which needs a stochastic
ground-truth rule P(j) proportional to exp(U_j / sigma), and sigma has no
objective value. The resolution is not to pick one but to construct the tier so
that it cancels. These tests assert that it really does cancel, and that it
would not have if the tier had been built from near-ties instead.
"""

import math

import pytest

from tests.econ.conftest import softmax_entropy

SIGMAS = [1e-4, 1e-3, 1e-2, 0.1, 1.0, 10.0]


@pytest.mark.parametrize("sigma", SIGMAS)
@pytest.mark.parametrize("n", [2, 3, 5])
def test_exact_ties_give_log_n_for_every_sigma(sigma, n):
    """The claim the whole tier rests on: sigma disappears from the metric."""
    assert softmax_entropy([1.0] * n, sigma) == pytest.approx(math.log(n), abs=1e-12)


def test_near_ties_do_not_have_this_property():
    """Had the tier been 'approximately indifferent', the reference entropy
    would have swung with an unpinnable parameter and the collapse threshold
    would have been arbitrary."""
    utilities = [1.0, 1.0002]
    # A wider sweep than the shared list, because the point is the full range an
    # unpinnable parameter can produce, not the range of one arbitrary sample.
    entropies = [softmax_entropy(utilities, s) for s in [1e-5, *SIGMAS]]
    assert min(entropies) < 0.05
    assert max(entropies) > 0.69
    assert max(entropies) - min(entropies) > 0.6


def test_the_solver_produces_a_tie_exact_to_floating_point(core, persona, cities):
    """The precise-tie construction of §11.3 must be an algebraic inversion, so
    the residual is float noise rather than a solver tolerance."""
    a = cities["C1"].city_id
    b = cities["C7"].city_id
    wage = core.solve_wage_for_tie(persona, a, b)
    core.cities["C7"] = cities["C7"].with_wage(wage)

    u_a = core.score(persona, a, 2024).utility.value
    u_b = core.score(persona, b, 2024).utility.value
    assert u_b == pytest.approx(u_a, rel=1e-12)


def test_a_solved_tie_yields_the_invariant_reference_entropy(core, persona, cities):
    a = cities["C1"].city_id
    b = cities["C7"].city_id
    core.cities["C7"] = cities["C7"].with_wage(core.solve_wage_for_tie(persona, a, b))
    us = [core.score(persona, c, 2024).utility.value for c in (a, b)]
    for sigma in SIGMAS:
        assert softmax_entropy(us, sigma) == pytest.approx(math.log(2), abs=1e-6)


def test_the_solver_is_persona_specific(core, persona, cities):
    """A tie is a tie for one person. Different weights, different solution --
    which is why the third tier is constructed per persona and not once per
    world."""
    a = cities["C1"].city_id
    b = cities["C7"].city_id
    w1 = core.solve_wage_for_tie(persona, a, b)

    from econ.types import Persona, PersonaParams

    other = Persona(
        persona_id=persona.persona_id,
        params=PersonaParams(alpha=0.5, beta=0.3, theta={"safety": 0.2}),
        shares=persona.shares,
        occupation=persona.occupation,
        ability_quantile=persona.ability_quantile,
        brief=persona.brief,
    )
    w2 = core.solve_wage_for_tie(other, a, b)
    assert w1 != pytest.approx(w2, rel=1e-6)


def test_dominance_is_detectable_before_any_model_is_called(core, persona, cities):
    """Tier 1 is a sanity check: if the harness cannot tell that one city
    Pareto-dominates, the project stops (§11.3)."""
    base = cities["C7"]
    dominated = base.with_wage(base.wage * 0.5)
    core.cities["C7"] = dominated
    u_better = core.score(persona, cities["C1"].city_id, 2024).utility.value
    u_worse = core.score(persona, base.city_id, 2024).utility.value
    assert u_better > u_worse
