"""Numeric leak detection for serialised prompts (README §10.2).

The type boundary stops an answer-side *object* from reaching an adapter. It
cannot stop someone formatting a ground-truth number into a string first. This
module is the second line: given the prompt actually sent and the ground truth
that was withheld, it asserts that none of the withheld numbers appears in the
text, in any plausible rendering.

Two refinements that decide whether this is useful or merely noisy:

  * A number is searched for in many renderings, because ``0.8412`` also shows
    up as ``0.84``, ``84.1%``-adjacent forms, and ``8.412e-01``. Matching only
    ``repr`` would miss almost every real leak.

  * Numbers that legitimately appear on the observable side are excluded. A
    ground-truth profile value that happens to equal an observable attribute is
    not evidence of a leak, and without this the check would cry wolf until
    somebody switched it off.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum

# Renderings with fewer digits than this are too common to be evidence: "0",
# "1", "50" appear in any prompt by chance. Three is safe only because matching
# is boundary-anchored (see _contains_number), so "0.84" must stand alone as a
# number rather than being found inside "10.8412".
#
# Known limit, stated rather than hidden: a withheld value that renders to two
# digits or fewer is not checked. Ground-truth utilities and profile values are
# continuous and effectively never land there, but a leak of an exact 0.5 would
# pass. The type boundary, not this scan, is the primary defence.
MIN_VALUE_DIGITS = 3  # a value less distinctive than this is not hunted for at all
MIN_VARIANT_DIGITS = 2  # ... but a distinctive value's rounded forms may be short


class LeakError(AssertionError):
    """Raised when a withheld numeric value is found in a serialised prompt."""


@dataclass(frozen=True)
class Leak:
    path: str
    value: float
    rendering: str

    def __str__(self) -> str:
        return f"{self.path}={self.value!r} appears as {self.rendering!r}"


def numeric_fields(obj: object, path: str = "") -> dict[str, float]:
    """Every float or int reachable from ``obj``, keyed by its access path.

    Walks dataclasses, mappings and sequences. Booleans are skipped: they are
    ints in Python and carry no numeric information worth hiding.
    """
    out: dict[str, float] = {}

    def walk(value: object, p: str) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            if math.isfinite(value):
                out[p or "<root>"] = float(value)
            return
        if isinstance(value, (str, bytes, Enum)):
            return
        if is_dataclass(value) and not isinstance(value, type):
            for f in fields(value):
                walk(getattr(value, f.name), f"{p}.{f.name}" if p else f.name)
            return
        if isinstance(value, Mapping):
            for k, v in value.items():
                walk(v, f"{p}[{k!r}]" if p else f"[{k!r}]")
            return
        if isinstance(value, Sequence):
            for i, v in enumerate(value):
                walk(v, f"{p}[{i}]" if p else f"[{i}]")

    walk(obj, path)
    return out


def render_variants(x: float, min_value_digits: int = MIN_VALUE_DIGITS) -> set[str]:
    """Plausible ways a number could be written into a prompt."""
    if _value_digits(x) < min_value_digits:
        # Zero padding must not manufacture significance: rendering 0.5 as
        # "0.500" does not make it any less likely to occur by chance.
        return set()
    variants: set[str] = set()
    if x == int(x) and abs(x) < 1e15:
        n = int(x)
        variants.update({str(n), f"{n:,}"})
    variants.add(repr(x))
    for dp in range(0, 7):
        variants.add(f"{x:.{dp}f}")
        variants.add(f"{x:,.{dp}f}")
    for sig in range(2, 8):
        variants.add(f"{x:.{sig}g}")
    # Trailing-zero forms such as "1.2300" also read as "1.23".
    variants.update({v.rstrip("0").rstrip(".") for v in list(variants) if "." in v})
    return {v for v in variants if _digit_count(v) >= MIN_VARIANT_DIGITS}


def _digit_count(s: str) -> int:
    return len(re.sub(r"\D", "", s))


def _value_digits(x: float) -> int:
    """Significant digits carried by the value itself.

    Integers count their magnitude (84120 is distinctive); fractions drop
    leading and trailing zeros, so 0.5 scores 1 however it is formatted.
    """
    if x == int(x) and abs(x) < 1e15:
        return len(str(abs(int(x))))
    digits = repr(abs(x)).replace(".", "").lstrip("0").rstrip("0")
    return len(digits) or 1


def _contains_number(haystack: str, rendering: str) -> bool:
    """Match the rendering only where it stands alone as a number.

    Without the boundaries, searching for "0.84" would hit inside "10.8412" and
    every scan would drown in false positives; with them, a short rendering is
    still safe to look for.
    """
    pattern = r"(?<![\d.])" + re.escape(rendering) + r"(?![\d.])"
    return re.search(pattern, haystack) is not None


def find_leaks(
    prompt: str,
    secret: object,
    *,
    allowed: object | None = None,
    exempt_paths: frozenset[str] = frozenset(),
    min_value_digits: int = MIN_VALUE_DIGITS,
) -> list[Leak]:
    """Withheld numbers appearing in ``prompt``.

    ``allowed`` is the agent-visible object (typically the Observable set); any
    value it also contains is excluded, since its presence in the prompt is
    correct rather than a leak.

    ``min_value_digits`` must be lowered when scanning for injected preference
    weights. Ground-truth utilities and profile values are continuous and always
    clear the default, but constant-sum weights are coarse by construction --
    README §6.3 notes people allocate 10, 20, 30 -- so 0.20 carries two
    significant digits and the default would skip it, silently retiring exactly
    the check §11.5 asks for. Lowering the floor trades false negatives for
    false positives; the `allowed` exclusion and boundary anchoring absorb most
    of the cost.
    """
    allowed_values = set(numeric_fields(allowed).values()) if allowed is not None else set()
    leaks: list[Leak] = []
    for path, value in numeric_fields(secret).items():
        if path in exempt_paths or value in allowed_values:
            continue
        for rendering in sorted(render_variants(value, min_value_digits), key=len, reverse=True):
            if _contains_number(prompt, rendering):
                leaks.append(Leak(path, value, rendering))
                break
    return leaks


def assert_no_leak(
    prompt: str,
    secret: object,
    *,
    allowed: object | None = None,
    exempt_paths: frozenset[str] = frozenset(),
    min_value_digits: int = MIN_VALUE_DIGITS,
) -> None:
    leaks = find_leaks(
        prompt,
        secret,
        allowed=allowed,
        exempt_paths=exempt_paths,
        min_value_digits=min_value_digits,
    )
    if leaks:
        raise LeakError(
            "withheld values found in a serialised prompt (CLAUDE.md hard rule 1):\n  "
            + "\n  ".join(str(leak) for leak in leaks)
        )
