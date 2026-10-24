"""A 板块的零售价修正（E3B 映射修正 §4.1，``retail_correction_lognormal_v1``）：手算核对的正态共轭更新。"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from product.realdata.a_blocks import (
    MIN_OBSERVATION_SD,
    NO_UPDATE_ZH,
    PRIOR_SD,
    DirectQuote,
    family_update,
    retail_update,
)

SPEC = "food.staples.h21"


def q(supplier: str, amount: str, spec: str = SPEC) -> DirectQuote:
    return DirectQuote(supplier, spec, Decimal(amount))


def test_no_quotes_keeps_the_prior_and_the_base_prediction():
    r = retail_update(Decimal("100.00"), [], SPEC)
    assert r["status"] == "no_local_quotes" and r["note_zh"] == NO_UPDATE_ZH
    assert r["posterior"] == r["prior"] and r["prior"]["sd"] == pytest.approx(math.log(1.5))
    assert r["execution_price"] == "100.00"


def test_one_supplier_uses_the_wide_variance():
    # 一家报 150：δ = log 1.5，观测方差 = log(1.5)²（只有一家），先验方差也是 log(1.5)²
    # 后验均值 = log 1.5 ÷ 2，执行价 = 100 × √1.5 = 122.47
    r = retail_update(Decimal("100"), [q("a", "150")], SPEC)
    assert r["status"] == "updated" and r["suppliers"] == 1
    assert r["posterior"]["mean"] == pytest.approx(math.log(1.5) / 2)
    assert r["posterior"]["sd"] == pytest.approx(math.log(1.5) / math.sqrt(2))
    assert r["execution_price"] == "122.47"


def test_two_suppliers_floor_the_variance_and_duplicates_add_no_evidence():
    # 两家：110 与 121 → δ = log 1.1、2 log 1.1，均值 1.5 log 1.1；s² = 0.5 (log 1.1)²，s²/n = 0.25 (log 1.1)² 低于下限 → 用 (log 1.1)²
    r = retail_update(Decimal("100"), [q("a", "110"), q("b", "121")], SPEC)
    v = MIN_OBSERVATION_SD**2
    post_var = 1 / (1 / PRIOR_SD**2 + 1 / v)
    mean = post_var * 1.5 * math.log(1.1) / v
    assert r["observation"]["variance"] == pytest.approx(v)
    assert r["posterior"]["mean"] == pytest.approx(mean)
    assert r["execution_price"] == str(
        (Decimal(100) * Decimal(repr(math.exp(mean)))).quantize(Decimal("0.01"))
    )
    assert r["execution_price"] == "114.51"
    # 同一家重复抓取：每家只给一个对数比值，结果不变
    again = retail_update(Decimal("100"), [q("a", "110"), q("a", "110"), q("b", "121")], SPEC)
    assert again["posterior"] == r["posterior"] and again["suppliers"] == 2
    # 直接报价本身另存
    assert [x["amount"] for x in again["direct_quotes"]] == ["110", "110", "121"]


def test_other_specs_and_zero_prices_are_not_merged_or_logged():
    r = retail_update(Decimal("100"), [q("a", "150", spec="food.other"), q("b", "0")], SPEC)
    assert r["status"] == "no_local_quotes"
    assert r["quotes_not_comparable"] == 1 and r["zero_price_quotes"] == 1
    zero = retail_update(Decimal("0"), [q("a", "5")], SPEC)
    assert zero["status"] == "zero_base_price" and zero["execution_price"] == "0"


def test_a_family_reports_each_spec_and_says_when_nothing_was_updated():
    f = family_update([("food.a", Decimal("10")), ("food.b", Decimal("0"))], {})
    assert f["specs"] == 2 and f["comparable_quotes"] == 0
    assert f["by_status"] == {"no_local_quotes": 1, "zero_base_price": 1}
    assert f["note_zh"] == NO_UPDATE_ZH and "spec_updates" not in f
    # 有报价的规格：汇总直接采用接线时的修正记录（机会里的价已经是最终价，不再按它重算一次）
    record = retail_update(Decimal("10"), [q("x", "12", spec="food.a")], "food.a")
    g = family_update([("food.a", Decimal(record["execution_price"]))], {"food.a": record})
    assert g["by_status"] == {"updated": 1} and g["note_zh"] != NO_UPDATE_ZH
    assert g["comparable_quotes"] == 1 and g["spec_updates"] == {"food.a": record}
