"""Provider protocols: the seams a second country plugs into.

V1 ships only United States implementations, and they do not live here -- this
module is interfaces and their return types. The acceptance criterion for M0 is
not that these look extensible but that tests/test_seams.py can run a fixture
country through the whole set without editing a single call site.

Four seams, matching the four places a country-specific fact enters the model:

  HousingPriceProvider      the price of a unit of housing services
  ExpenditureShareProvider  the personalised price index weights of §5.2
  TaxFunction               progressive tax, jurisdiction as a path
  AmenityScaler             raw amenity value -> [0,1] against a frozen reference
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from common.ids import CityId, DemographicCell, Jurisdiction
from common.money import Money
from common.provenance import Grade, Provenance

# Tolerance for expenditure shares summing to one.
_SHARE_TOL = 1e-6


@dataclass(frozen=True, slots=True)
class PricePoint:
    """A price index value with everything needed to know what it means.

    ``method`` is load-bearing rather than documentation. The housing price is
    the one input where the main result and the robustness check use different
    constructions of the same quantity ("rpp_rents_raw" against a
    bedroom-standardised alternative), and a result that does not record which
    one produced it cannot be reproduced.
    """

    value: float
    numeraire: str
    method: str
    reference_id: str
    grade: Grade
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class ExpenditureShares:
    """The three budget shares of the personalised price index (README §5.2).

    Constructing this validates that the shares sum to one. That is not
    defensive habit: §7.2 identifies ``s_rent`` with the Cobb-Douglas housing
    exponent, so shares that quietly fail to sum to one silently change the
    utility function's exponents rather than raising anything.

    Use ``from_raw`` on the path where the behavioural proxy questions adjust a
    CEX lookup, since adjustment is what breaks the sum.
    """

    s_rent: float
    s_goods: float
    s_other: float

    def __post_init__(self) -> None:
        for name in ("s_rent", "s_goods", "s_other"):
            v = getattr(self, name)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} must lie in [0,1], got {v}")
        total = self.s_rent + self.s_goods + self.s_other
        if abs(total - 1.0) > _SHARE_TOL:
            raise ValueError(
                f"expenditure shares must sum to 1, got {total!r}; "
                "use ExpenditureShares.from_raw to renormalise deliberately"
            )

    @classmethod
    def from_raw(cls, s_rent: float, s_goods: float, s_other: float) -> ExpenditureShares:
        """Renormalise explicitly, so that rescaling is a visible act in the code."""
        total = s_rent + s_goods + s_other
        if total <= 0:
            raise ValueError("expenditure shares must have positive total")
        return cls(s_rent / total, s_goods / total, s_other / total)


@runtime_checkable
class HousingPriceProvider(Protocol):
    """Price per unit of housing services in a city.

    The unit is the seam. Rent, floor space and quality are three variables but
    they play three different roles: rent is the price, quantity is chosen by
    the individual and gets optimised out of the indirect utility, and whatever
    quality difference survives standardisation is measurement error in *this*
    number. Putting the standardisation behind this interface keeps it in the
    ledger, where revising it does not invalidate past experiments; a change
    inside src/econ/ would (CLAUDE.md hard rule 3).
    """

    def price_of_housing(self, city: CityId, year: int) -> PricePoint: ...


@runtime_checkable
class ExpenditureShareProvider(Protocol):
    """Budget shares for a demographic cell.

    The category mapping from a national household survey onto the three RPP
    buckets is itself a frozen, versioned artefact (README §5.7), not a dict
    literal at the call site.
    """

    def shares(self, cell: DemographicCell) -> ExpenditureShares: ...


@runtime_checkable
class TaxFunction(Protocol):
    """Total income tax owed. Jurisdiction is a path, so multi-state metros and
    countries of different administrative depth use the same signature."""

    def tax(self, gross: Money, jurisdiction: Jurisdiction, year: int) -> Money: ...


@runtime_checkable
class AmenityScaler(Protocol):
    """Raw amenity value -> [0,1] against a frozen reference population.

    ``reference_id`` is exposed rather than hidden because it propagates into
    every welfare number downstream (see WelfareLevel) and into result metadata.
    ``method`` reports "absolute" or "quantile" per README §5.3; absolute
    anchoring is preferred and is the only one that is cross-country comparable
    without a pooled reference population.
    """

    @property
    def reference_id(self) -> str: ...

    def method(self, variable: str) -> str: ...

    def scale(self, variable: str, raw: float) -> float: ...


@runtime_checkable
class CountryPack(Protocol):
    """Everything country-specific, bundled into one registrable object.

    This is the unit in which a country is added. Without it, "the seams are
    generic" would still be false in one place: getting from a CityId to a
    Jurisdiction is itself country-specific knowledge, and a call site that
    branches on ``city.country`` to find it has reintroduced exactly the coupling
    the identifiers were meant to remove.
    """

    @property
    def country(self) -> str: ...

    @property
    def housing(self) -> HousingPriceProvider: ...

    @property
    def shares(self) -> ExpenditureShareProvider: ...

    @property
    def taxes(self) -> TaxFunction: ...

    @property
    def scaler(self) -> AmenityScaler: ...

    def jurisdiction_of(self, city: CityId) -> Jurisdiction: ...
