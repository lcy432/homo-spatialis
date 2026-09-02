"""CLAUDE.md hard rule 4 / README §7.4 / §10.1.

Each test comes in a pair: the compliant core must pass, and a core broken in
one specific way must be caught. A single-count check that has only ever seen
correct code proves nothing.
"""

import pytest

from econ.graph import CANONICAL_HOME, DoubleCountError, Term, UtilityGraph
from tests.econ.conftest import DiagnosticInUtilityCore, DoubleCountingCore


def test_compliant_core_passes_every_assertion(core, persona, cities):
    for code in cities:
        g = core.graph_for(persona, core.cities[code].city_id)
        g.assert_all()


def test_rent_in_two_terms_is_caught(persona, cities):
    """The exact failure of §7.4: rent as both a price and an amenity."""
    broken = DoubleCountingCore(cities)
    g = broken.graph_for(persona, broken.cities["C1"].city_id)
    with pytest.raises(DoubleCountError, match="more than one term"):
        g.assert_single_count()


def test_diagnostic_entering_utility_is_caught(persona, cities):
    broken = DiagnosticInUtilityCore(cities)
    g = broken.graph_for(persona, broken.cities["C1"].city_id)
    with pytest.raises(DoubleCountError, match="diagnostic"):
        g.assert_no_diagnostic_in_utility()


def test_repeated_contribution_to_the_same_term_is_allowed():
    """Reading the wage twice inside the budget constraint is not double
    counting. The rule counts terms, not reads."""
    g = UtilityGraph()
    g.contribute("wage", Term.BUDGET)
    g.contribute("wage", Term.BUDGET)
    g.contribute("wage", Term.BUDGET)
    g.assert_all()


def test_wrong_canonical_home_is_caught():
    """Housing space belongs in H, not in the amenity vector."""
    g = UtilityGraph()
    g.contribute("housing_space", Term.AMENITY, dimension="roominess")
    with pytest.raises(DoubleCountError, match="disagree with README"):
        g.assert_canonical_homes()


def test_amenity_contribution_must_name_its_dimension():
    g = UtilityGraph()
    with pytest.raises(ValueError, match="which dimension"):
        g.contribute("crime_rate", Term.AMENITY)


@pytest.mark.parametrize("column", sorted(CANONICAL_HOME))
def test_every_table_entry_is_self_consistent(column):
    """Contributing a column to its own canonical home must always pass."""
    g = UtilityGraph()
    term = CANONICAL_HOME[column]
    g.contribute(column, term, dimension="d" if term is Term.AMENITY else None)
    g.assert_canonical_homes()
    g.assert_single_count()


def test_housing_burden_has_no_home_in_the_utility_function():
    assert CANONICAL_HOME["housing_burden_ratio"] is Term.DIAGNOSTIC


def test_rent_lives_in_the_price_index_and_housing_has_no_rent_column():
    """The agreed housing specification, asserted rather than remembered:
    rent is a price, and quantity is optimised out of the indirect form, so no
    rent column may appear under HOUSING."""
    assert CANONICAL_HOME["rent"] is Term.PRICE_INDEX
    assert CANONICAL_HOME["rpp_rents"] is Term.PRICE_INDEX
    housing_columns = {c for c, t in CANONICAL_HOME.items() if t is Term.HOUSING}
    assert housing_columns == {"housing_space", "housing_quality"}
