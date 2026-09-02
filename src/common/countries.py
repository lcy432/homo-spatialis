"""Registry of country packs.

Adding a country is one registration and no edits anywhere else. V1 registers
nothing here -- the United States pack is M1/M1.5 work and will register itself
once the data exists. The registry is present from the first commit so that the
call sites written in M2-M5 are shaped correctly from the start.
"""

from __future__ import annotations

from common.providers import CountryPack

_REGISTRY: dict[str, CountryPack] = {}


class UnknownCountryError(KeyError):
    """Raised when a CityId names a country with no registered pack."""


def register_country(pack: CountryPack, *, replace: bool = False) -> None:
    country = pack.country
    if country in _REGISTRY and not replace:
        raise ValueError(
            f"country {country!r} is already registered; pass replace=True to override"
        )
    _REGISTRY[country] = pack


def get_country(country: str) -> CountryPack:
    try:
        return _REGISTRY[country]
    except KeyError:
        raise UnknownCountryError(
            f"no country pack registered for {country!r}; registered: {sorted(_REGISTRY)}"
        ) from None


def registered_countries() -> list[str]:
    return sorted(_REGISTRY)


def clear_registry() -> None:
    """Test support. Production code registers once at import time."""
    _REGISTRY.clear()
