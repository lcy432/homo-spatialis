"""The type contract of the welfare model: what is visible, what is the answer.

CLAUDE.md hard rule 3 covers this package. These are declarations with no
arithmetic in them -- the utility function, the scale functions and the
transition kernel are M2's own first version and are deliberately absent. What
is here is the boundary of README §3.2, expressed so that the type system holds
it rather than anyone's memory.

Two parallel splits, for the same reason:

    Observable   / GroundTruth    what the environment computes / what is the answer
    PersonaBrief / Persona        what the agent reads          / what was injected

The second split matters as much as the first. README §11.5: if the persona text
states the weights, the task degenerates into reading comprehension. So the
numeric weights live in Persona, the prose lives in PersonaBrief, and only the
brief can reach a prompt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from common.ids import CityId, OccupationId
from common.money import Money, WelfareLevel
from common.providers import ExpenditureShares

_EXPONENT_TOL = 1e-9


class ObservableTier(Enum):
    """README §11.4. How much accounting the environment has already done."""

    O1 = "O1"  # L0 + L1: raw statistical yearbook
    O2 = "O2"  # plus L2: an assistant that can do the arithmetic
    O3 = "O3"  # plus representative-basket real income: a cost-of-living calculator


class LiteralnessTier(Enum):
    """README §11.5. How directly the persona text states its own weights."""

    IMPLICIT = "implicit"  # "I hate driving and cannot take winters"
    MIXED = "mixed"
    EXPLICIT = "explicit"  # "commute weight high, climate weight high"


class BoundaryViolationError(AssertionError):
    """Raised when an answer-side object is found somewhere agent-visible."""


@dataclass(frozen=True, slots=True)
class PersonaParams:
    """Cobb-Douglas exponents. Answer side.

    The mapping from the seven constant-sum weights to (alpha, beta, theta) is
    README §7.2's two-channel formula and belongs to M2, not here. This type only
    holds the result and enforces the constraint §7.1 puts on it.
    """

    alpha: float  # non-housing consumption
    beta: float  # housing
    theta: Mapping[str, float]  # amenity name -> exponent

    def __post_init__(self) -> None:
        if self.alpha < 0 or self.beta < 0:
            raise ValueError("alpha and beta must be non-negative")
        if any(v < 0 for v in self.theta.values()):
            raise ValueError("amenity exponents must be non-negative")
        total = self.alpha + self.beta + sum(self.theta.values())
        if abs(total - 1.0) > _EXPONENT_TOL:
            raise ValueError(
                f"exponents must sum to 1 (README §7.1 constant returns), got {total!r}"
            )

    @property
    def w_material(self) -> float:
        """The exponent on real income in the indirect form: w_1 = alpha + beta."""
        return self.alpha + self.beta

    @property
    def zero_exponents(self) -> tuple[str, ...]:
        """Dimensions a Cobb-Douglas would annihilate (README §6.3, the zero problem)."""
        names = [k for k, v in self.theta.items() if v == 0.0]
        if self.alpha == 0.0:
            names.append("alpha")
        if self.beta == 0.0:
            names.append("beta")
        return tuple(sorted(names))


@dataclass(frozen=True, slots=True)
class PersonaBrief:
    """Everything about a person that an agent is allowed to read."""

    persona_id: str
    literalness: LiteralnessTier
    narrative: str


@dataclass(frozen=True, slots=True)
class Persona:
    """A person as the environment knows them. Answer side; never in a prompt."""

    persona_id: str
    params: PersonaParams
    shares: ExpenditureShares
    occupation: OccupationId
    ability_quantile: int  # one of the OEWS quantiles: 10, 25, 50, 75, 90
    brief: PersonaBrief

    def __post_init__(self) -> None:
        if self.ability_quantile not in (10, 25, 50, 75, 90):
            raise ValueError(
                "ability_quantile must be an OEWS quantile (10/25/50/75/90); "
                "README §7.5 forbids defaulting everyone to the median"
            )
        if self.brief.persona_id != self.persona_id:
            raise ValueError("persona_id must match between Persona and its brief")


@dataclass(frozen=True, slots=True)
class Observable:
    """L0-L2. The only city information an agent ever sees.

    ``o3_representative_real`` is environment, not answer, despite deflating
    income: it uses the *representative* basket, so it does not depend on this
    person's shares and therefore cannot be A(a; theta) in disguise.
    """

    tier: ObservableTier
    city: CityId
    year: int
    l0_amenities: Mapping[str, float]
    l1_economics: Mapping[str, float]
    l2_accounting: Mapping[str, float] | None = None
    o3_representative_real: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        if not self.l0_amenities or not self.l1_economics:
            raise ValueError(
                "every tier retains L0 and L1. README §11.4: the collapsed quantities are "
                "additional, never a replacement, or the design matrix loses rank and the "
                "conditional logit cannot recover the injected weights"
            )
        if self.tier is ObservableTier.O1 and (
            self.l2_accounting is not None or self.o3_representative_real is not None
        ):
            raise ValueError("O1 carries L0 and L1 only")
        if self.tier in (ObservableTier.O2, ObservableTier.O3) and self.l2_accounting is None:
            raise ValueError(f"{self.tier.value} must carry L2 accounting")
        if self.tier is ObservableTier.O3 and self.o3_representative_real is None:
            raise ValueError("O3 must carry representative-basket real income")
        if self.tier is ObservableTier.O2 and self.o3_representative_real is not None:
            raise ValueError("O2 must not carry the O3 deflated quantities")


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """L3-L4. Never reaches a prompt (CLAUDE.md hard rule 1)."""

    city: CityId
    year: int
    amenity_index: float  # A(a; theta), L3
    profile: Mapping[str, float]  # per-dimension profile, L3
    utility: WelfareLevel  # L4
    equivalent_variation: Money | None = None  # L4


def _reject_answer_side(value: object, forbidden: tuple[type, ...], path: str) -> None:
    if isinstance(value, forbidden):
        raise BoundaryViolationError(
            f"{type(value).__name__} reached agent-visible field {path!r}; "
            "CLAUDE.md hard rule 1 forbids GroundTruth in any prompt"
        )
    if isinstance(value, Mapping):
        for k, v in value.items():
            _reject_answer_side(v, forbidden, f"{path}[{k!r}]")
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for i, v in enumerate(value):
            _reject_answer_side(v, forbidden, f"{path}[{i}]")


@dataclass(frozen=True, slots=True)
class AgentInput:
    """The complete, and only, payload an agent adapter may accept.

    Constructing one is the choke point for hard rule 1: it walks itself and
    refuses anything from the answer side, so a leak has to get past a
    constructor rather than past a reviewer.
    """

    brief: PersonaBrief
    options: tuple[Observable, ...]
    permutation: tuple[int, ...] = field(default=())

    def __post_init__(self) -> None:
        forbidden = (GroundTruth, Persona, PersonaParams, WelfareLevel)
        for name in ("brief", "options", "permutation"):
            _reject_answer_side(getattr(self, name), forbidden, path=name)
        if self.permutation and sorted(self.permutation) != list(range(len(self.options))):
            raise ValueError(
                "permutation must be a permutation of the option indices; README §10.2 "
                "requires the order to be randomised and the permutation recorded"
            )


@runtime_checkable
class EconomicCore(Protocol):
    """One implementation, two exits (README §3.2).

    Both must call the same underlying functions. Writing the ground-truth
    generator and the offloaded arithmetic separately lets the tax function
    drift between them, and then a discrepancy cannot be attributed to either.
    """

    def observe(
        self, persona: Persona, city: CityId, year: int, tier: ObservableTier
    ) -> Observable: ...

    def score(self, persona: Persona, city: CityId, year: int) -> GroundTruth: ...
