"""The single-count assertion: each physical quantity enters utility once.

README §7.4. The consequence of breaking this is not a numerical error, it is
identification failure: if rent sits in both the price index and the amenity
vector, w_1 acts through the numerator and the denominator at once, and the
recovered implicit weight stops corresponding to the injected one. Research mode
would then be measuring nothing, while looking entirely healthy.

The rule counts *terms*, not reads. Reading the wage twice inside the budget
constraint is not double counting; putting the wage in the budget and again in
the amenity vector is. So a column may contribute to one term as often as it
likes, and to a second term never.

Usage from M2:

    g = UtilityGraph()
    g.contribute("rpp_rents", Term.PRICE_INDEX)
    g.contribute("crime_rate", Term.AMENITY, dimension="safety")
    ...
    g.assert_single_count()
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum


class Term(Enum):
    """Where in the utility function a quantity may live."""

    PRICE_INDEX = "price_index"  # P, the personalised deflator of README §5.2
    BUDGET = "budget"  # income, tax, saving
    HOUSING = "housing"  # H, quantity and quality of housing services
    AMENITY = "amenity"  # one entry of the A vector
    DIAGNOSTIC = "diagnostic"  # reported, never entering utility (README §9.1)


class DoubleCountError(AssertionError):
    """Raised when one column reaches two different terms of the utility function."""


# README §7.4's table, executable. A column named here must land in this term
# and nowhere else. Columns absent from the table are unconstrained but still
# subject to the one-term rule.
CANONICAL_HOME: dict[str, Term] = {
    "rent": Term.PRICE_INDEX,
    "rpp_rents": Term.PRICE_INDEX,
    "rpp_goods": Term.PRICE_INDEX,
    "rpp_other": Term.PRICE_INDEX,
    "housing_space": Term.HOUSING,
    "housing_quality": Term.HOUSING,
    "wage": Term.BUDGET,
    "income": Term.BUDGET,
    "savings": Term.BUDGET,
    "tax": Term.BUDGET,
    # Diagnostics: instrumental variables, reported to explain the score, never
    # summed into it. Housing burden is fully determined by rent and income and
    # would be a third counting of both.
    "housing_burden_ratio": Term.DIAGNOSTIC,
    "disposable_income": Term.DIAGNOSTIC,
    "net_savings_3y": Term.DIAGNOSTIC,
}


@dataclass(frozen=True, slots=True)
class Contribution:
    column: str
    term: Term
    dimension: str | None = None  # which amenity, when term is AMENITY


@dataclass
class UtilityGraph:
    """A record of which raw column fed which term of the utility function."""

    contributions: list[Contribution] = field(default_factory=list)

    def contribute(self, column: str, term: Term, dimension: str | None = None) -> None:
        if term is Term.AMENITY and dimension is None:
            raise ValueError("an AMENITY contribution must name which dimension it feeds")
        self.contributions.append(Contribution(column, term, dimension))

    def terms_by_column(self) -> dict[str, set[Term]]:
        out: dict[str, set[Term]] = defaultdict(set)
        for c in self.contributions:
            out[c.column].add(c.term)
        return dict(out)

    def assert_single_count(self) -> None:
        """No column may feed two different terms."""
        offenders = {col: terms for col, terms in self.terms_by_column().items() if len(terms) > 1}
        if offenders:
            detail = "; ".join(
                f"{col} -> {sorted(t.value for t in terms)}"
                for col, terms in sorted(offenders.items())
            )
            raise DoubleCountError(
                "a raw column reached more than one term of the utility function "
                f"(README §7.4): {detail}. This is identification failure, not a rounding "
                "difference: the recovered weight no longer corresponds to the injected one."
            )

    def assert_no_diagnostic_in_utility(self) -> None:
        """A quantity whose canonical home is DIAGNOSTIC must not feed a real term."""
        offenders = []
        for col, terms in self.terms_by_column().items():
            if CANONICAL_HOME.get(col) is Term.DIAGNOSTIC:
                live = {t for t in terms if t is not Term.DIAGNOSTIC}
                if live:
                    offenders.append(f"{col} -> {sorted(t.value for t in live)}")
        if offenders:
            raise DoubleCountError(
                "a diagnostic quantity entered the utility function (README §9.1: "
                f"instrumental variables explain the score, they are not part of it): {offenders}"
            )

    def assert_canonical_homes(self) -> None:
        """Every column named in README §7.4's table must sit where the table says."""
        offenders = []
        for col, terms in self.terms_by_column().items():
            expected = CANONICAL_HOME.get(col)
            if expected is not None and terms != {expected}:
                offenders.append(
                    f"{col}: expected {expected.value}, saw {sorted(t.value for t in terms)}"
                )
        if offenders:
            raise DoubleCountError(
                "contributions disagree with README §7.4: " + "; ".join(offenders)
            )

    def assert_all(self) -> None:
        self.assert_single_count()
        self.assert_no_diagnostic_in_utility()
        self.assert_canonical_homes()
