"""Field-level provenance and the A/B/C comparability grade (README §5.4, §5.5).

Rule 1 of §5.4 says every value in the final table must be traceable to where it
came from. Rule 2 says a field must carry its construct, not just its number and
unit. Provenance below serves rule 1; Grade serves rule 2.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from enum import Enum


class Grade(Enum):
    """Comparability of one column *with respect to a given city set*.

    A grade is not an intrinsic property of a field. It is resolved per
    comparison (README §5.5): the ledger is built offline without grades, and
    the grade falls out of the city set at query time.
    """

    A = "A"  # same construct, coverage and distributional detail across the set
    B = "B"  # mappable, bias known and recorded; enters the comparison, labelled
    C = "C"  # construct differs or coverage missing for some city; excluded

    @classmethod
    def worst(cls, grades: list[Grade] | tuple[Grade, ...]) -> Grade:
        """A column's grade for a comparison is its lowest grade in the set.

        One city at B makes the whole column B for that comparison. This is the
        rule that stops a completeness check from being mistaken for a
        comparability check (README §5.5).
        """
        if not grades:
            raise ValueError("cannot resolve a grade over an empty city set")
        order = {cls.A: 0, cls.B: 1, cls.C: 2}
        return max(grades, key=lambda g: order[g])


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where one value came from. Attached at the field level, not the table level."""

    source_url: str
    source_table: str
    source_cell: str
    retrieved_date: _dt.date

    def __post_init__(self) -> None:
        for name in ("source_url", "source_table", "source_cell"):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be empty; a value with no origin is not usable")
