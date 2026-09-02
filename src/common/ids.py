"""Identifiers for the cross-country seams (README §5.7, §5.8).

V1 is the United States only. Every identifier here nonetheless carries its
country and its coding scheme, so that adding a second country changes *data*,
not call sites. A bare numeric code is ambiguous the day a second country
arrives, and by then it is threaded through the whole pipeline.

tests/test_seams.py proves the claim with a fixture country that exists only in
the test suite: if adding a country required touching a call site, that test
would not compile.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CityId:
    """A geographic unit of comparison.

    ``scheme`` names the geography definition, not just the country: US metro
    areas are ``CBSA``, and CBSA boundaries are themselves revised between
    vintages (README §5.7), so a scheme string like ``CBSA2023`` is legitimate
    and preferable where the vintage matters.
    """

    country: str  # ISO 3166-1 alpha-2, e.g. "US"
    scheme: str  # e.g. "CBSA2023"
    code: str  # e.g. "41860"

    def __str__(self) -> str:
        return f"{self.country}:{self.scheme}:{self.code}"


@dataclass(frozen=True, slots=True)
class OccupationId:
    """An occupation under a named classification.

    The scheme is not decoration. China's published wage statistics are keyed to
    industry rather than occupation (README §5.7), which is exactly the kind of
    construct difference that makes a column grade C rather than A. Recording
    the scheme is what lets the ledger detect that instead of silently joining
    two different measurements.
    """

    scheme: str  # "SOC2018" | "ISCO08" | ...
    code: str

    def __str__(self) -> str:
        return f"{self.scheme}:{self.code}"


@dataclass(frozen=True, slots=True)
class Jurisdiction:
    """A tax jurisdiction as a *path*, not a flat code.

    Two reasons it must be variable-length. Domestically, some metro areas span
    several states (the New York CBSA covers NY/NJ/CT, README §5.7), so the
    metro does not determine the jurisdiction and the path has to be resolved
    per agent. Internationally, countries nest differently: the United States is
    (country, state) with occasional local surtaxes, and other countries are
    (country,) alone.
    """

    path: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("Jurisdiction path must have at least a country segment")

    @property
    def country(self) -> str:
        return self.path[0]

    def __str__(self) -> str:
        return "/".join(self.path)


@dataclass(frozen=True, slots=True)
class DemographicCell:
    """The CEX-style cell used to look up expenditure shares (README §6.4).

    Kept deliberately coarse and country-tagged: the cell definitions of the US
    Consumer Expenditure Survey do not carry over to another country's household
    survey, and pretending they do is the construct error of README §5.4 rule 2.
    """

    country: str
    household_size: int
    tenure: str  # "rent" | "own"
    income_decile: int | None = None

    def __post_init__(self) -> None:
        if self.tenure not in ("rent", "own"):
            raise ValueError(f"tenure must be 'rent' or 'own', got {self.tenure!r}")
        if self.household_size < 1:
            raise ValueError("household_size must be >= 1")
        if self.income_decile is not None and not 1 <= self.income_decile <= 10:
            raise ValueError("income_decile must be in 1..10")
