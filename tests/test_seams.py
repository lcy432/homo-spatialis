"""The cross-country claim, made falsifiable.

README §5.7-§5.8 promise that V1 is United States only but that adding a country
changes data rather than call sites. That promise is cheap to write and easy to
break silently. This module defines a fixture country that exists nowhere else
in the repo and pushes it through every seam using the same consumer function as
the United States. If a call site ever has to branch on the country, this file
stops working.
"""

from __future__ import annotations

import datetime as dt

import pytest

from common.countries import (
    UnknownCountryError,
    clear_registry,
    get_country,
    register_country,
    registered_countries,
)
from common.ids import CityId, DemographicCell, Jurisdiction, OccupationId
from common.money import Money, NumeraireMismatchError
from common.provenance import Grade, Provenance
from common.providers import (
    AmenityScaler,
    CountryPack,
    ExpenditureShareProvider,
    ExpenditureShares,
    HousingPriceProvider,
    PricePoint,
    TaxFunction,
)

PROV = Provenance("https://example.test/t", "table-1", "B7", dt.date(2026, 1, 15))


class _Housing:
    def __init__(self, numeraire: str, reference_id: str, value: float, method: str):
        self._n, self._r, self._v, self._m = numeraire, reference_id, value, method

    def price_of_housing(self, city: CityId, year: int) -> PricePoint:
        return PricePoint(
            value=self._v,
            numeraire=self._n,
            method=self._m,
            reference_id=self._r,
            grade=Grade.A,
            provenance=PROV,
        )


class _Shares:
    def shares(self, cell: DemographicCell) -> ExpenditureShares:
        # Renormalised deliberately, the way a behavioural-proxy adjustment would.
        return ExpenditureShares.from_raw(0.34, 0.31, 0.37)


class _FlatTax:
    def __init__(self, rate: float):
        self.rate = rate

    def tax(self, gross: Money, jurisdiction: Jurisdiction, year: int) -> Money:
        # Depth-sensitive on purpose: a deeper path means a local surtax.
        surtax = 0.01 * (len(jurisdiction.path) - 1)
        return gross * (self.rate + surtax)


class _Scaler:
    def __init__(self, reference_id: str):
        self._r = reference_id

    @property
    def reference_id(self) -> str:
        return self._r

    def method(self, variable: str) -> str:
        return "absolute" if variable == "sunshine_hours" else "quantile"

    def scale(self, variable: str, raw: float) -> float:
        return max(0.0, min(1.0, raw / 100.0))


class _Pack:
    def __init__(self, country, numeraire, reference_id, price, method, rate, depth):
        self._country = country
        self._housing = _Housing(numeraire, reference_id, price, method)
        self._shares = _Shares()
        self._taxes = _FlatTax(rate)
        self._scaler = _Scaler(reference_id)
        self._depth = depth

    @property
    def country(self):
        return self._country

    @property
    def housing(self):
        return self._housing

    @property
    def shares(self):
        return self._shares

    @property
    def taxes(self):
        return self._taxes

    @property
    def scaler(self):
        return self._scaler

    def jurisdiction_of(self, city: CityId) -> Jurisdiction:
        # A US metro can span states, so the path is two deep; the fixture
        # country taxes only nationally, so its path is one deep.
        return Jurisdiction((self._country, "MA")[: self._depth])


US = _Pack("US", "USD_2024_US100", "US_MSA_2024", 1.18, "rpp_rents_raw", 0.22, depth=2)
ZZ = _Pack("ZZ", "ZZK_2024_ZZ100", "ZZ_CITY_2024", 0.74, "listing_median_2br", 0.15, depth=1)

US_CITY = CityId("US", "CBSA2023", "14460")
ZZ_CITY = CityId("ZZ", "ZZDIST2024", "0007")


def collect_city_facts(city: CityId, cell: DemographicCell, gross: Money, year: int) -> dict:
    """Thread every seam together without knowing which country this is.

    Deliberately does no economics: M2 owns the utility function (CLAUDE.md hard
    rule 3). What is under test is that the plumbing never branches on
    ``city.country``.
    """
    pack = get_country(city.country)
    price = pack.housing.price_of_housing(city, year)
    shares = pack.shares.shares(cell)
    tax = pack.taxes.tax(gross, pack.jurisdiction_of(city), year)
    return {
        "price": price,
        "shares": shares,
        "tax": tax,
        "net": gross - tax,
        "safety": pack.scaler.scale("crime_rate", 42.0),
        "reference_id": pack.scaler.reference_id,
        "numeraire": price.numeraire,
    }


@pytest.fixture(autouse=True)
def _registry():
    clear_registry()
    register_country(US)
    register_country(ZZ)
    yield
    clear_registry()


def test_both_countries_are_registered():
    assert registered_countries() == ["US", "ZZ"]


@pytest.mark.parametrize(
    ("city", "numeraire", "reference_id"),
    [
        (US_CITY, "USD_2024_US100", "US_MSA_2024"),
        (ZZ_CITY, "ZZK_2024_ZZ100", "ZZ_CITY_2024"),
    ],
)
def test_one_consumer_serves_both_countries(city, numeraire, reference_id):
    cell = DemographicCell(country=city.country, household_size=2, tenure="rent")
    facts = collect_city_facts(city, cell, Money(90_000.0, numeraire), year=2024)

    assert facts["numeraire"] == numeraire
    assert facts["reference_id"] == reference_id
    assert facts["net"].numeraire == numeraire
    assert 0.0 <= facts["safety"] <= 1.0
    s = facts["shares"]
    assert abs(s.s_rent + s.s_goods + s.s_other - 1.0) < 1e-9


def test_jurisdiction_depth_differs_and_the_tax_call_does_not_care():
    us_j = US.jurisdiction_of(US_CITY)
    zz_j = ZZ.jurisdiction_of(ZZ_CITY)
    assert len(us_j.path) == 2
    assert len(zz_j.path) == 1
    assert us_j.country == "US"
    assert zz_j.country == "ZZ"


def test_money_from_two_countries_cannot_be_mixed_by_accident():
    us = collect_city_facts(
        US_CITY, DemographicCell("US", 2, "rent"), Money(90_000.0, "USD_2024_US100"), 2024
    )
    zz = collect_city_facts(
        ZZ_CITY, DemographicCell("ZZ", 2, "rent"), Money(90_000.0, "ZZK_2024_ZZ100"), 2024
    )
    with pytest.raises(NumeraireMismatchError):
        _ = us["net"] > zz["net"]


def test_unregistered_country_fails_loudly():
    with pytest.raises(UnknownCountryError):
        collect_city_facts(
            CityId("QQ", "X", "1"), DemographicCell("QQ", 1, "own"), Money(1.0, "X"), 2024
        )


def test_packs_satisfy_the_protocols():
    for pack in (US, ZZ):
        assert isinstance(pack, CountryPack)
        assert isinstance(pack.housing, HousingPriceProvider)
        assert isinstance(pack.shares, ExpenditureShareProvider)
        assert isinstance(pack.taxes, TaxFunction)
        assert isinstance(pack.scaler, AmenityScaler)


def test_occupation_carries_its_scheme():
    """China publishes wages by industry, not occupation (README §5.7). Recording
    the scheme is what lets the ledger notice that, instead of silently joining
    two different measurements."""
    soc = OccupationId("SOC2018", "15-1252")
    isco = OccupationId("ISCO08", "2512")
    assert soc != isco
    assert str(soc) == "SOC2018:15-1252"


def test_city_id_is_never_a_bare_code():
    assert str(US_CITY) == "US:CBSA2023:14460"
    assert CityId("US", "CBSA2023", "14460") != CityId("ZZ", "CBSA2023", "14460")


def test_grade_of_a_column_is_the_worst_in_the_set():
    """README §5.5: one city at B makes the whole column B for that comparison."""
    assert Grade.worst([Grade.A, Grade.A]) is Grade.A
    assert Grade.worst([Grade.A, Grade.B, Grade.A]) is Grade.B
    assert Grade.worst([Grade.A, Grade.B, Grade.C]) is Grade.C
    with pytest.raises(ValueError):
        Grade.worst([])


def test_shares_must_sum_to_one():
    with pytest.raises(ValueError, match="sum to 1"):
        ExpenditureShares(0.4, 0.4, 0.4)
    ok = ExpenditureShares.from_raw(0.4, 0.4, 0.4)
    assert abs(ok.s_rent - 1 / 3) < 1e-12


def test_provenance_rejects_a_value_with_no_origin():
    with pytest.raises(ValueError):
        Provenance("", "t", "c", dt.date(2026, 1, 1))
