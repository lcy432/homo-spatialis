"""README §10.2: the automatic leak test.

The type boundary stops an answer-side object. It cannot stop a number being
formatted into a string first, so this is the second line of defence, and it is
itself checked against a core that leaks on purpose.
"""

import pytest

from common.leakcheck import LeakError, assert_no_leak, find_leaks, render_variants
from econ.types import ObservableTier
from tests.econ.conftest import render_prompt


def _prompt_and_truth(core, persona, tier=ObservableTier.O1):
    obs = [core.observe(persona, c.city_id, 2024, tier) for c in core.cities.values()]
    truths = [core.score(persona, c.city_id, 2024) for c in core.cities.values()]
    return render_prompt(persona.brief, obs), obs, truths


@pytest.mark.parametrize("tier", list(ObservableTier))
def test_a_clean_prompt_leaks_nothing_at_any_tier(core, persona, tier):
    prompt, obs, truths = _prompt_and_truth(core, persona, tier)
    for truth in truths:
        assert_no_leak(prompt, truth, allowed=obs)


def test_a_leaked_utility_is_caught(core, persona):
    prompt, obs, truths = _prompt_and_truth(core, persona)
    leaked = prompt + f"\n(internal: score {truths[0].utility.value})"
    with pytest.raises(LeakError, match="hard rule 1"):
        assert_no_leak(leaked, truths[0], allowed=obs)


def test_a_rounded_leak_is_caught(core, persona):
    """Rounding is the realistic leak: nobody pastes a float64 into a prompt."""
    prompt, obs, truths = _prompt_and_truth(core, persona)
    leaked = prompt + f"\nprojected wellbeing index: {truths[0].amenity_index:.3f}"
    assert find_leaks(leaked, truths[0], allowed=obs)


def test_a_thousands_separated_leak_is_caught(core, persona):
    prompt, _obs, truths = _prompt_and_truth(core, persona, ObservableTier.O2)
    value = truths[0].profile["safety"] * 100000
    leaked = prompt + f"\nhidden: {value:,.0f}"
    assert find_leaks(leaked, {"hidden": value})


def test_persona_weights_leaking_is_caught(core, persona):
    """README §11.5: if the text states the weights, the task degenerates into
    reading comprehension. The injected parameters are as secret as the score."""
    prompt, obs, _ = _prompt_and_truth(core, persona)
    leaked = prompt.replace(
        "I hate driving", f"I hate driving (commute weight {persona.params.theta['commute']:.2f})"
    )
    with pytest.raises(LeakError):
        assert_no_leak(leaked, persona.params, allowed=obs, min_value_digits=1)


def test_the_untouched_prompt_does_not_expose_the_weights(core, persona):
    prompt, obs, _ = _prompt_and_truth(core, persona)
    assert_no_leak(prompt, persona.params, allowed=obs, min_value_digits=1)


def test_the_default_floor_would_have_missed_a_weight_leak(core, persona):
    """Documents why min_value_digits exists. Constant-sum weights are coarse
    by construction, so the default significance floor skips them; scanning for
    a weight leak without lowering it is a check that cannot fire."""
    prompt, obs, _ = _prompt_and_truth(core, persona)
    leaked = prompt + f"\n(commute weight {persona.params.theta['commute']:.2f})"
    assert not find_leaks(leaked, persona.params, allowed=obs)
    assert find_leaks(leaked, persona.params, allowed=obs, min_value_digits=1)


def test_a_value_that_is_legitimately_observable_is_not_a_false_positive(core, persona):
    """A ground-truth field that happens to equal a visible attribute must not
    trip the scan, or the check gets switched off within a week."""
    prompt, obs, _truths = _prompt_and_truth(core, persona)
    shared = obs[0].l1_economics["wage"]
    assert not find_leaks(prompt, {"coincidence": shared}, allowed=obs)
    # Without the exclusion, the very same value does register.
    assert find_leaks(prompt, {"coincidence": shared})


def test_short_renderings_are_not_treated_as_evidence():
    """Documented limit: two digits or fewer is not searched for."""
    assert render_variants(0.5) == set()
    assert not find_leaks("the answer is 0.5", {"u": 0.5})


def test_matching_is_boundary_anchored():
    """0.8412 is a substring of 10.8412, and must not count as a match there."""
    assert not find_leaks("unrelated figure 10.8412", {"u": 0.8412})
    assert find_leaks("the value is 0.8412 exactly", {"u": 0.8412})
