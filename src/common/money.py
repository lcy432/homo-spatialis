"""Monetary and welfare quantities carrying the scale they are denominated in.

This module is the code form of README §5.8's three-tier comparability
declaration. The declaration is easy to state in prose and easy to violate in a
dataframe, so it is enforced here instead:

  Tier 1/2 (welfare levels are comparable) -- permitted only when two values
      share both a numeraire and an amenity reference population. Money knows
      about the first; WelfareLevel requires both, because a utility number
      mixes deflated income with amenity scale values and README §5.3 is
      explicit that a scale value means a different thing under a different
      reference population.

  Tier 3 (recovery error is always comparable) -- deliberately *not* modelled
      here. Recovery error is a dimensionless ratio; it is a plain float and
      carries no guard, which is the whole point of §5.8: the cross-country
      question survives even when welfare levels do not.

The guard is a runtime one. The declared stack has ruff but no static type
checker, so a purely static encoding would not actually be enforced anywhere;
an exception that fires in the test suite is worth more than a type parameter
nobody runs.
"""

from __future__ import annotations

from dataclasses import dataclass

# V1's only numeraire: US dollars, 2024, spatially deflated to the RPP base
# where the national average is 100.
USD_2024_US100 = "USD_2024_US100"


class NumeraireMismatchError(ValueError):
    """Raised on an arithmetic or ordering operation across different scales."""


class ReferenceMismatchError(ValueError):
    """Raised when welfare levels from different reference populations are compared."""


@dataclass(frozen=True, slots=True)
class Money:
    amount: float
    numeraire: str

    def _same(self, other: Money) -> None:
        if self.numeraire != other.numeraire:
            raise NumeraireMismatchError(
                f"cannot combine {self.numeraire} with {other.numeraire}; "
                "convert explicitly, or compare a dimensionless quantity instead"
            )

    def __add__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount + other.amount, self.numeraire)

    def __sub__(self, other: Money) -> Money:
        self._same(other)
        return Money(self.amount - other.amount, self.numeraire)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.numeraire)

    def __mul__(self, k: float) -> Money:
        if isinstance(k, Money):
            raise TypeError("Money * Money is not a monetary quantity")
        return Money(self.amount * k, self.numeraire)

    __rmul__ = __mul__

    def __truediv__(self, other: float | Money) -> Money | float:
        """Divide by a scalar for a Money, or by a Money for a pure ratio."""
        if isinstance(other, Money):
            self._same(other)
            return self.amount / other.amount
        return Money(self.amount / other, self.numeraire)

    def __lt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._same(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._same(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._same(other)
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"{self.amount:,.2f} [{self.numeraire}]"


@dataclass(frozen=True, slots=True)
class WelfareLevel:
    """A utility level or an equivalent variation, tagged with everything that
    determines what it means.

    Comparison requires agreement on both tags. ``reference_id`` is the frozen
    amenity reference population of README §5.3; two welfare numbers built on
    different reference populations are measured with different rulers even when
    the money side agrees.
    """

    value: float
    numeraire: str
    reference_id: str

    def _comparable(self, other: WelfareLevel) -> None:
        if self.numeraire != other.numeraire:
            raise NumeraireMismatchError(
                f"welfare levels in {self.numeraire} and {other.numeraire} are not comparable "
                "(README §5.8 tier 3: compare recovery error, which is dimensionless)"
            )
        if self.reference_id != other.reference_id:
            raise ReferenceMismatchError(
                f"welfare levels scaled against {self.reference_id} and {other.reference_id} "
                "are not comparable (README §5.3: the reference population changes what the "
                "amenity values mean)"
            )

    def __lt__(self, other: WelfareLevel) -> bool:
        self._comparable(other)
        return self.value < other.value

    def __le__(self, other: WelfareLevel) -> bool:
        self._comparable(other)
        return self.value <= other.value

    def __gt__(self, other: WelfareLevel) -> bool:
        self._comparable(other)
        return self.value > other.value

    def __ge__(self, other: WelfareLevel) -> bool:
        self._comparable(other)
        return self.value >= other.value

    def __str__(self) -> str:
        return f"{self.value:.6g} [{self.numeraire} @ {self.reference_id}]"
