"""Fixtures for the M2 harness.

Contains a toy core. It is NOT M2 and src/econ/ must never import it. Its only
purpose is to give the assertions something to bite on: a single-count check
that has never seen a double count, and a leak scan that has never seen a leak,
are decoration. So alongside the compliant toy there are two deliberately broken
ones, and the tests require the harness to catch them.

When the real core lands, expose a callable ``build_core`` in ``econ.core`` and
the contract tests in test_contract.py will pick it up automatically and run the
same assertions against it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pytest

from common.ids import CityId, OccupationId
from common.money import USD_2024_US100, WelfareLevel
from common.providers import ExpenditureShares
from econ.graph import Term, UtilityGraph
from econ.types import (
    GroundTruth,
    LiteralnessTier,
    Observable,
    ObservableTier,
    Persona,
    PersonaBrief,
    PersonaParams,
)

REFERENCE_ID = "US_MSA_2024"

AMENITY_COLUMNS = {
    "safety": "crime_rate",
    "sunshine": "sunshine_hours",
    "commute": "commute_minutes",
}


@dataclass(frozen=True)
class ToyCity:
    city_id: CityId
    wage: float
    tax_rate: float
    rpp_rents: float
    rpp_goods: float
    rpp_other: float
    crime_rate: float
    sunshine_hours: float
    commute_minutes: float

    def l1(self) -> dict[str, float]:
        return {
            "wage": self.wage,
            "tax_rate": self.tax_rate,
            "rpp_rents": self.rpp_rents,
            "rpp_goods": self.rpp_goods,
            "rpp_other": self.rpp_other,
        }

    def l0(self) -> dict[str, float]:
        return {
            "crime_rate": self.crime_rate,
            "sunshine_hours": self.sunshine_hours,
            "commute_minutes": self.commute_minutes,
        }

    def with_wage(self, wage: float) -> ToyCity:
        return ToyCity(
            self.city_id,
            wage,
            self.tax_rate,
            self.rpp_rents,
            self.rpp_goods,
            self.rpp_other,
            self.crime_rate,
            self.sunshine_hours,
            self.commute_minutes,
        )


def _scale(variable: str, raw: float) -> float:
    """Toy scale function. Monotone and bounded, which is all §7.3 requires of a
    research-mode world: any monotone scaling defines a legitimate world, it only
    changes the difficulty."""
    if variable == "commute_minutes":  # less is better
        return max(0.0, min(1.0, 1.0 - raw / 120.0))
    if variable == "crime_rate":
        return max(0.0, min(1.0, 1.0 - raw / 100.0))
    return max(0.0, min(1.0, raw / 4000.0))  # sunshine hours


class ReferenceToyCore:
    """A minimal stand-in for M2. Not the real welfare model.

    Implements exactly the indirect form agreed for the housing question:

        V = (Y_net / P) ** w1  *  prod_k A_k ** theta_k

    Rent appears once, inside P. Housing quantity never appears: it is optimised
    out, which is why there is nothing for it to be double counted against.
    """

    def __init__(self, cities: dict[str, ToyCity]):
        self.cities = cities

    def _city(self, city: CityId) -> ToyCity:
        return self.cities[city.code]

    def price_index(self, persona: Persona, city: CityId, g: UtilityGraph | None = None) -> float:
        c = self._city(city)
        s = persona.shares
        if g is not None:
            for col in ("rpp_rents", "rpp_goods", "rpp_other"):
                g.contribute(col, Term.PRICE_INDEX)
        return c.rpp_rents**s.s_rent * c.rpp_goods**s.s_goods * c.rpp_other**s.s_other

    def net_income(self, persona: Persona, city: CityId, g: UtilityGraph | None = None) -> float:
        c = self._city(city)
        if g is not None:
            g.contribute("wage", Term.BUDGET)
            g.contribute("tax", Term.BUDGET)
        quantile_factor = {10: 0.72, 25: 0.85, 50: 1.0, 75: 1.22, 90: 1.51}
        return c.wage * quantile_factor[persona.ability_quantile] * (1.0 - c.tax_rate)

    def amenity_index(self, persona: Persona, city: CityId, g: UtilityGraph | None = None) -> float:
        c = self._city(city)
        out = 1.0
        for dim, column in AMENITY_COLUMNS.items():
            theta = persona.params.theta.get(dim, 0.0)
            if g is not None:
                g.contribute(column, Term.AMENITY, dimension=dim)
            out *= _scale(column, getattr(c, column)) ** theta
        return out

    def utility_value(self, persona: Persona, city: CityId, g: UtilityGraph | None = None) -> float:
        real = self.net_income(persona, city, g) / self.price_index(persona, city, g)
        return real**persona.params.w_material * self.amenity_index(persona, city, g)

    def graph_for(self, persona: Persona, city: CityId) -> UtilityGraph:
        g = UtilityGraph()
        self.utility_value(persona, city, g)
        return g

    def observe(
        self, persona: Persona, city: CityId, year: int, tier: ObservableTier
    ) -> Observable:
        c = self._city(city)
        l2 = None
        o3 = None
        if tier in (ObservableTier.O2, ObservableTier.O3):
            l2 = {"net_income": self.net_income(persona, city), "savings_3y": 0.0}
        if tier is ObservableTier.O3:
            rep = ExpenditureShares(0.33, 0.33, 0.34)
            rep_p = c.rpp_rents**rep.s_rent * c.rpp_goods**rep.s_goods * c.rpp_other**rep.s_other
            o3 = {"real_income_representative": self.net_income(persona, city) / rep_p}
        return Observable(
            tier=tier,
            city=city,
            year=year,
            l0_amenities=c.l0(),
            l1_economics=c.l1(),
            l2_accounting=l2,
            o3_representative_real=o3,
        )

    def score(self, persona: Persona, city: CityId, year: int) -> GroundTruth:
        c = self._city(city)
        return GroundTruth(
            city=city,
            year=year,
            amenity_index=self.amenity_index(persona, city),
            profile={dim: _scale(col, getattr(c, col)) for dim, col in AMENITY_COLUMNS.items()},
            utility=WelfareLevel(self.utility_value(persona, city), USD_2024_US100, REFERENCE_ID),
        )

    def solve_wage_for_tie(self, persona: Persona, target: CityId, city: CityId) -> float:
        """Wage in ``city`` that makes its utility exactly equal ``target``'s.

        A direct algebraic inversion, not a root find. README §11.3's third tier
        needs exact ties, and the closed form is what makes that a one-liner
        rather than a numerical search in a high-dimensional space.
        """
        c = self._city(city)
        u_target = self.utility_value(persona, target)
        a = self.amenity_index(persona, city)
        p = self.price_index(persona, city)
        w1 = persona.params.w_material
        real_needed = (u_target / a) ** (1.0 / w1)
        quantile_factor = {10: 0.72, 25: 0.85, 50: 1.0, 75: 1.22, 90: 1.51}
        return real_needed * p / (quantile_factor[persona.ability_quantile] * (1.0 - c.tax_rate))


class DoubleCountingCore(ReferenceToyCore):
    """Broken on purpose: rent is both a price and an amenity.

    This is exactly the mistake README §7.4 warns about, and the one that would
    look completely healthy at runtime while destroying identification.
    """

    def amenity_index(self, persona: Persona, city: CityId, g: UtilityGraph | None = None) -> float:
        out = super().amenity_index(persona, city, g)
        if g is not None:
            g.contribute("rpp_rents", Term.AMENITY, dimension="housing_niceness")
        return out


class DiagnosticInUtilityCore(ReferenceToyCore):
    """Broken on purpose: housing burden ratio treated as an amenity.

    Burden is rent over income, so both are already counted; adding it is a
    third counting of each (README §9.1 keeps it as a diagnostic).
    """

    def amenity_index(self, persona: Persona, city: CityId, g: UtilityGraph | None = None) -> float:
        out = super().amenity_index(persona, city, g)
        if g is not None:
            g.contribute("housing_burden_ratio", Term.AMENITY, dimension="affordability")
        return out


def render_prompt(brief: PersonaBrief, observables: list[Observable]) -> str:
    """A stand-in prompt serialiser, so the leak scan has text to scan."""
    lines = [f"You are: {brief.narrative}", "", "Options:"]
    for o in observables:
        lines.append(f"  {o.city.code}:")
        for k, v in sorted(o.l1_economics.items()):
            lines.append(f"    {k} = {v}")
        for k, v in sorted(o.l0_amenities.items()):
            lines.append(f"    {k} = {v}")
        if o.l2_accounting:
            for k, v in sorted(o.l2_accounting.items()):
                lines.append(f"    {k} = {v}")
        if o.o3_representative_real:
            for k, v in sorted(o.o3_representative_real.items()):
                lines.append(f"    {k} = {v}")
    return "\n".join(lines)


CITY_A = ToyCity(CityId("US", "SYNTH", "C1"), 92000.0, 0.24, 1.31, 1.02, 1.11, 38.0, 2600.0, 41.0)
CITY_B = ToyCity(CityId("US", "SYNTH", "C7"), 71000.0, 0.17, 0.88, 0.97, 0.94, 21.0, 3300.0, 24.0)
CITY_C = ToyCity(CityId("US", "SYNTH", "C4"), 80500.0, 0.21, 1.04, 1.00, 1.02, 29.0, 2950.0, 32.0)


@pytest.fixture
def cities() -> dict[str, ToyCity]:
    return {c.city_id.code: c for c in (CITY_A, CITY_B, CITY_C)}


@pytest.fixture
def persona() -> Persona:
    params = PersonaParams(
        alpha=0.30,
        beta=0.22,
        theta={"safety": 0.20, "sunshine": 0.13, "commute": 0.15},
    )
    brief = PersonaBrief(
        persona_id="p001",
        literalness=LiteralnessTier.IMPLICIT,
        narrative=(
            "I hate driving and cannot take grey winters. I would rather have a smaller "
            "place in a quiet neighbourhood than a big one I have to commute to."
        ),
    )
    return Persona(
        persona_id="p001",
        params=params,
        shares=ExpenditureShares(0.34, 0.29, 0.37),
        occupation=OccupationId("SOC2018", "15-1252"),
        ability_quantile=50,
        brief=brief,
    )


@pytest.fixture
def core(cities) -> ReferenceToyCore:
    return ReferenceToyCore(cities)


def softmax_entropy(utilities: list[float], sigma: float) -> float:
    """Entropy of the ground-truth choice distribution at temperature sigma.

    Lives in the test rather than in src/eval/: M5's core mathematics is written
    by hand first (CLAUDE.md), and this exists only to demonstrate a property of
    the experimental design.
    """
    m = max(u / sigma for u in utilities)
    exps = [math.exp(u / sigma - m) for u in utilities]
    total = sum(exps)
    probs = [e / total for e in exps]
    return -sum(p * math.log(p) for p in probs if p > 0)
