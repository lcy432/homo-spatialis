"""README §5.8 in executable form."""

import pytest

from common.money import (
    USD_2024_US100,
    Money,
    NumeraireMismatchError,
    ReferenceMismatchError,
    WelfareLevel,
)

CNY = "CNY_2024_CN100"


def test_same_numeraire_arithmetic_works():
    a = Money(1000.0, USD_2024_US100)
    b = Money(250.0, USD_2024_US100)
    assert (a + b).amount == 1250.0
    assert (a - b).amount == 750.0
    assert (a * 2).amount == 2000.0
    assert a > b


@pytest.mark.parametrize("op", ["add", "sub", "lt", "gt", "le", "ge"])
def test_cross_numeraire_is_refused(op):
    usd = Money(1000.0, USD_2024_US100)
    cny = Money(1000.0, CNY)
    fn = {
        "add": lambda: usd + cny,
        "sub": lambda: usd - cny,
        "lt": lambda: usd < cny,
        "gt": lambda: usd > cny,
        "le": lambda: usd <= cny,
        "ge": lambda: usd >= cny,
    }[op]
    with pytest.raises(NumeraireMismatchError):
        fn()


def test_ratio_of_two_money_is_a_plain_float():
    """Housing burden is dimensionless, so it leaves the guarded world entirely."""
    burden = Money(2400.0, USD_2024_US100) / Money(7100.0, USD_2024_US100)
    assert isinstance(burden, float)
    assert 0.33 < burden < 0.34


def test_money_times_money_is_not_money():
    with pytest.raises(TypeError):
        Money(2.0, USD_2024_US100) * Money(3.0, USD_2024_US100)


def test_welfare_levels_need_matching_numeraire_and_reference():
    us = WelfareLevel(1.23, USD_2024_US100, "US_MSA_2024")
    same = WelfareLevel(1.10, USD_2024_US100, "US_MSA_2024")
    assert us > same

    with pytest.raises(NumeraireMismatchError):
        _ = us > WelfareLevel(1.10, CNY, "US_MSA_2024")

    # Same money, different ruler for the amenity side: still not comparable.
    with pytest.raises(ReferenceMismatchError):
        _ = us > WelfareLevel(1.10, USD_2024_US100, "NA_CN_POOLED_2024")


def test_recovery_error_is_deliberately_unguarded():
    """Tier 3 of §5.8: the cross-country question survives incomparable levels."""
    us_error = 0.14
    cn_error = 0.31
    assert cn_error > us_error  # a plain float comparison, no guard, by design
