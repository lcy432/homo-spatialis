"""B 住房的完整加权经验支持表（housing_rent_support@1，E3B 映射修正）：手写的小格子，逐条手算。不读缓存。"""

from __future__ import annotations

import math
from types import SimpleNamespace

import numpy as np
import pandas as pd

from product.realdata.acs import utility_topcoded
from product.realdata.housing import facilities_status
from product.realdata.mapping_b import internet_access, quality_tiers, support_table

CELL = SimpleNamespace(
    cbsa="99999",
    level="puma_bed",
    key=("0100101", "1"),
    population="new_market_proxy",
    table_id="b.housing.99999.puma_bed.0100101.1",
    n=0,
    n_eff=0.0,
)


def row(rid, w, seg, contract, gross=math.nan, **kw):
    base = {
        "record_id": rid,
        "WGTP": w,
        "segment": seg,
        "censored": False,
        "censor_bound": math.nan,
        "rent_contract": contract,
        "rent_gross": gross,
        "gross_affected_by": None,
        "BDSP": 1,
        "RMSP": 3,
        "BLD": "07",
        "YRBLT": 1980,
        "KIT": 1,
        "PLM": 1,
    }
    base.update(kw)
    return base


def table(rows):
    df = pd.DataFrame(rows)
    return support_table(SimpleNamespace(**{**vars(CELL), "n": len(df)}), df)


def by_records(t):
    return {r: p for p in t["points"] for r in p["records"]}


def test_same_record_gross_is_used_and_only_missing_gross_is_imputed():
    t = table(
        [
            row("a", 10, "recent_mover", 1000.0, 1100.0),  # 水电 100
            row("b", 30, "recent_mover", 1200.0, 1290.0),  # 水电 90
            row("c", 20, "vacant_for_rent", 1100.0),  # 空置：只有要价
            row("d", 40, "stock", 1300.0),  # 租住户缺毛租金
        ]
    )
    p = by_records(t)
    assert (p["a"]["price_status"], p["a"]["gross_rent"]) == ("observed", "1100.00")
    assert (p["b"]["price_status"], p["b"]["gross_rent"]) == ("observed", "1290.00")
    # 供体水电（权重）：90（30）、100（10）→ 加权中位数 = 累计权重第一次过半的 90；不拿格中位数覆盖 a、b 的已知值
    assert t["donors"] == {
        "n": 2,
        "utilities_median_month": "90.00",
        "assumption": "housing_utilities_conditional_median_v1",
    }
    assert (p["c"]["price_status"], p["c"]["gross_rent"]) == ("imputed", "1190.00")
    assert (p["d"]["price_status"], p["d"]["gross_rent"]) == ("imputed", "1390.00")
    assert t["masses"]["observed"] == 40 and t["masses"]["imputed"] == 60


def test_censored_records_keep_weight_and_threshold_but_no_price():
    t = table(
        [
            row("a", 10, "recent_mover", 1000.0, 1100.0),
            row("e", 25, "recent_mover", 5100.0, 5200.0, censored=True, censor_bound=3800.0),
        ]
    )
    e = by_records(t)["e"]
    assert e["price_status"] == "censored" and e["gross_rent"] is None
    assert (e["censor_bound"], e["censored_by"], e["weight"]) == (3800.0, "RNTP", 25)
    assert t["masses"]["raw"] == 35 and t["masses"]["executable"] == 10


def test_a_topcoded_utility_makes_the_gross_censored_not_a_quote():
    t = table([row("u", 15, "recent_mover", 900.0, 2400.0, gross_affected_by="ELEP")])
    u = by_records(t)["u"]
    assert (u["price_status"], u["censored_by"], u["contract_rent"]) == (
        "censored",
        "ELEP",
        "900.00",
    )


def test_no_donor_means_unknown_not_zero():
    t = table([row("c", 20, "vacant_for_rent", 1100.0), row("f", 5, "vacant_for_rent", 900.0)])
    assert {p["price_status"] for p in t["points"]} == {"unknown"}
    assert t["donors"]["n"] == 0 and t["donors"]["utilities_median_month"] is None
    assert t["masses"]["unknown"] == 25 and t["masses"]["executable"] == 0


def test_identical_features_merge_and_weights_add():
    t = table(
        [
            row("a", 10, "recent_mover", 1000.0, 1100.0),
            row("a2", 7, "recent_mover", 1000.0, 1100.0),
            row("b", 30, "recent_mover", 1200.0, 1290.0),
        ]
    )
    merged = by_records(t)["a"]
    assert merged["records"] == ["a", "a2"] and merged["weight"] == 17
    assert sum(p["weight"] for p in t["points"]) == t["weight_sum"] == 47


def test_quality_tiers_follow_the_weighted_position_and_ties_share_a_tier():
    contract = np.array([800.0, 900.0, 1000.0, 1000.0, 1200.0, 1500.0])
    w = np.ones(6)
    assert list(quality_tiers(contract, np.zeros(6, dtype=bool), w)) == [1, 1, 2, 2, 3, 3]
    # 顶码记录排在最上面
    cens = np.array([True, False, False, False, False, False])
    assert quality_tiers(contract, cens, w)[0] == 3


def test_kitchen_and_plumbing_are_not_assumed_complete():
    assert facilities_status(1, 1) == "complete"
    assert facilities_status(2, 1) == "incomplete" and facilities_status(1, 2) == "incomplete"
    assert (
        facilities_status(float("nan"), 1) == "unknown"
        and facilities_status(None, None) == "unknown"
    )
    t = table(
        [
            row("a", 10, "recent_mover", 1000.0, 1100.0),
            row("k", 6, "recent_mover", 950.0, 1000.0, KIT=2),
            row("x", 4, "recent_mover", 980.0, 1050.0, PLM=math.nan),
        ]
    )
    m = t["masses"]
    assert (m["facilities_complete"], m["facilities_incomplete"], m["facilities_unknown"]) == (
        10,
        6,
        4,
    )
    assert m["executable"] == 10  # 厨卫不完整或未知的不进可执行池（也就不进最低合格住房）


def test_utility_topcode_rule():
    rule = (1400.0, 3000.0)  # 加州电费：阈值 1,400、替代值 3,000
    assert not utility_topcoded(200.0, rule)
    assert utility_topcoded(3000.0, rule) and utility_topcoded(1500.0, rule)
    assert utility_topcoded(10.0, None)  # 没有规则：不能判断，不当真实费用


def test_internet_access_excludes_suppressed_and_not_applicable_from_denominators():
    df = pd.DataFrame(
        {
            "full_puma": [True] * 5,
            "segment": ["recent_mover", "stock", "stock", "stock", "vacant_for_rent"],
            "WGTP": [10, 20, 30, 40, 50],
            "ACCESSINET": [1, 3, 8, math.nan, math.nan],
            "HISPEED": [1, math.nan, 8, math.nan, math.nan],
            "BROADBND": [2, math.nan, 8, math.nan, math.nan],
            "SMARTPHONE": [1, 2, 1, math.nan, math.nan],
            "TEL": [1, 1, 1, math.nan, math.nan],
        }
    )
    out = internet_access(df, "99999")
    acc = out["variables"]["ACCESSINET"]
    assert out["weight_sum"] == 100  # 空置待租不在租住户里
    assert (acc["weight_valid"], acc["weight_suppressed"], acc["weight_not_applicable"]) == (
        30,
        30,
        40,
    )
    assert acc["shares_of_valid"] == {
        "paid": round(10 / 30, 4),
        "without_paying": 0.0,
        "no_access": round(20 / 30, 4),
    }
