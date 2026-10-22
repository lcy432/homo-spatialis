"""A / B 映射与菜单抽样的手算例子（不需要样本文件）。

浮点比较的精度：阶梯分位数是真实观测值，按精确相等比较；插值分位数与重复权重标准误只有几次浮点加减乘除，按相对 1e-12 比较
（足以区分阶梯法与插值法的差别，不会用宽松容差掩盖算法不同）。
"""

from __future__ import annotations

import inspect
import math
from fractions import Fraction

import numpy as np
import pandas as pd
import pytest

from common.intake_contracts import Calendar, FieldValue, GeoRef, MenuPolicy, missing
from product.realdata import mapping_a, mapping_b, menus
from product.realdata.acs import TopcodeRule
from product.realdata.mapping_b import RentModel, estimate_cell, wquantile
from product.realdata.registry import make_opportunity

QS = ["0.25", "0.50", "0.75", "1/6", "1/2", "5/6"]
IL = TopcodeRule(
    "17", "official", threshold=3800.0, replacement=5100.0
)  # 2024 年官方表里伊利诺伊的合同租金
CA = TopcodeRule("06", "official", threshold=6000.0, replacement=8300.0)  # 加利福尼亚


def _rent_frame(rents, weights, states=None, rules=None, rep=None):
    """一格样本。``states`` 与 ``rules`` 给出时按官方规则标顶码；``rep`` 是 n × R 的重复权重。"""
    n = len(rents)
    status, bound = ["not_topcoded"] * n, [math.nan] * n
    if states is not None:
        for i, (st, v) in enumerate(zip(states, rents, strict=True)):
            s, b = rules[st].classify(float(v))
            status[i], bound[i] = s, (math.nan if b is None else b)
    df = pd.DataFrame(
        {
            "rent_contract": [float(r) for r in rents],
            "rent_gross": [r + 100.0 for r in rents],
            "WGTP": weights,
            "topcode_status": status,
            "censored": [s != "not_topcoded" for s in status],
            "censor_bound": bound,
            "struct_class": ["large_multifamily"] * n,
            "record_id": [f"r{i}" for i in range(n)],
        }
    )
    rep_cols = []
    if rep is not None:
        for r in range(rep.shape[1]):
            df[f"WGTP{r + 1}"] = rep[:, r]
            rep_cols.append(f"WGTP{r + 1}")
    return df, rep_cols


def _estimate(df, rep_cols=()):
    return estimate_cell(
        df, "b.t", "16980", "puma_bed", ("x",), "new_market_proxy", QS, list(rep_cols)
    )


def _cell(level, key, n_rows, weight=10, rent=1000):
    g, _ = _rent_frame([rent + 100 * i for i in range(n_rows)], [weight] * n_rows)
    return estimate_cell(g, f"b.t.{level}", "16980", level, key, "new_market_proxy", QS, [])


# --------------------------------------------------------------------------- B：分位数、标准误成对（F4）


def test_weighted_quantile_is_the_first_observed_value_reaching_the_weight_share():
    v = np.array([100.0, 200.0, 300.0, 400.0])
    assert wquantile(v, np.array([1.0, 1, 1, 1]), 0.5) == 200  # 累计 [1,2,3,4]，第一次 ≥ 2 是 200
    assert wquantile(v, np.array([1.0, 1, 1, 5]), 0.5) == 400  # 累计 [1,2,3,8]，第一次 ≥ 4 是 400


def test_step_and_interpolated_medians_each_carry_their_own_replicate_se():
    # 两个报告值 100、200，主权重各 1；80 个重复权重都是 (1, 3)。
    #   阶梯：主估计 = 100（累计 1 ≥ 1）；每个重复估计 = 200（累计 [1,4]，第一次 ≥ 2 是 200）
    #         SE = √(4/80 × 80 × (200 − 100)²) = 200
    #   插值：主估计 = 150（区间 [50,150)、[150,250] 各一半）；每个重复估计 = 150 + (0.25/0.75)×100 = 183.33…
    #         SE = √(4/80 × 80 × (100/3)²) = 200/3
    rep = np.tile(np.array([[1.0], [3.0]]), (1, 80))
    df, cols = _rent_frame([100, 200], [1.0, 1.0], rep=rep)
    cell = _estimate(df, cols)
    assert (cell.median_step, cell.se_median_step) == (100.0, 200.0)
    assert cell.median_interpolated == 150.0
    assert cell.se_median_interpolated == pytest.approx(200 / 3, rel=1e-12)
    # 旧写法把插值法的标准误配给阶梯中位数（100 ± 66.67）：两对必须分开
    assert cell.se_median_step != pytest.approx(cell.se_median_interpolated, rel=1e-3)
    assert cell.quantiles["0.50"] == "100.00"  # 候选价格用阶梯分位数（真实观测值）


# --------------------------------------------------------------------------- B：官方顶码（F5）


def test_ordinary_sample_maximum_is_not_treated_as_topcoded():
    df, _ = _rent_frame([1000, 1200, 1400], [10, 10, 10], states=["17"] * 3, rules={"17": IL})
    cell = _estimate(df)
    assert cell.censored_share == 0 and cell.censor_bound_min is None
    assert cell.quantiles["5/6"] == "1400.00"  # 1400 只是这一格的最大值，不是顶码
    assert cell.quantiles["0.50"] == "1200.00"


def test_official_topcode_only_hides_quantiles_above_the_state_threshold():
    assert IL.classify(5100) == ("topcoded", 3800.0)
    assert IL.classify(3800) == ("not_topcoded", None)
    df, _ = _rent_frame([1000, 1200, 1400, 5100], [10] * 4, states=["17"] * 4, rules={"17": IL})
    cell = _estimate(df)
    assert cell.censor_bound_min == 3800.0
    assert cell.quantiles["0.50"] == "1200.00" and cell.quantiles["0.75"] == "1400.00"
    assert (
        cell.quantiles["5/6"] is None
    )  # 落在顶码记录上：真实值只知道高于 3800，5100 不是它的值也不是下界
    assert cell.utilities_median == "100.00"  # 顶码记录不进水电中位数


def test_mixed_states_use_the_lowest_threshold_of_topcoded_records_not_one_replacement():
    # 伊利诺伊的顶码记录真实值只知道 > 3800，可能是 3900，排在加州 4000 之前；所以 4000、5000 处的分位数不可识别。
    # 若拿伊利诺伊的替代值 5100 当统一截断线，会把中位数错报成 4000。
    rents = [1000, 1200, 4000, 5000, 5100]
    states = ["17", "17", "06", "06", "17"]
    df, _ = _rent_frame(rents, [10] * 5, states=states, rules={"17": IL, "06": CA})
    cell = _estimate(df)
    assert list(df["topcode_status"]) == ["not_topcoded"] * 4 + ["topcoded"]
    assert cell.quantiles["0.25"] == "1200.00"
    assert (
        cell.quantiles["0.50"] is None
        and cell.median_step is None
        and cell.median_interpolated is None
    )


def test_state_missing_from_official_table_keeps_topcode_status_unknown():
    rule = TopcodeRule("99", "missing", file_max=9000.0, file_second=7000.0)
    assert rule.classify(9000) == (
        "unknown",
        7000.0,
    )  # 整州最大值可能是替代值：未知，不当成顶码，也不当成真实值
    assert rule.classify(7000) == ("not_topcoded", None)
    df, _ = _rent_frame([1000, 7000, 9000], [10] * 3, states=["99"] * 3, rules={"99": rule})
    cell = _estimate(df)
    assert list(df["topcode_status"]) == ["not_topcoded", "not_topcoded", "unknown"]
    assert cell.quantiles["0.50"] == "7000.00"  # 不高于整州第二大的值：可识别
    assert cell.quantiles["5/6"] is None


def test_replicate_se_is_none_when_a_replicate_estimate_is_not_identified():
    rep = np.tile(
        np.array([[1.0], [1.0], [8.0]]), (1, 80)
    )  # 重复权重把顶码记录加重：重复中位数落到顶码上
    df, cols = _rent_frame(
        [1000, 1200, 5100], [1.0, 1.0, 0.5], states=["17"] * 3, rules={"17": IL}, rep=rep
    )
    cell = _estimate(df, cols)
    assert cell.median_step == 1200.0
    assert cell.se_median_step is None  # 有重复估计不可识别：不给一个看似精确的标准误


def test_fallback_skips_cells_below_the_minimum_sample_and_records_what_was_tried():
    model = RentModel(
        cbsa="16980",
        min_n=30,
        cells={
            ("puma_bed", ("17031", "1")): _cell("puma_bed", ("17031", "1"), 4),
            ("metro_bed", ("1",)): _cell("metro_bed", ("1",), 40),
        },
        tiers={},
    )
    cell, tried = model.resolve(
        [("puma_bed", ("17031", "1")), ("tier_bed", ("core", "1")), ("metro_bed", ("1",))]
    )
    assert cell is not None and cell.level == "metro_bed"
    assert tried == ["puma_bed", "tier_bed"]
    assert model.resolve([("puma_bed", ("17031", "1"))]) == (None, ["puma_bed"])


def test_commute_tiers_split_renter_weight_into_thirds():
    c = {
        p: mapping_b.PumaCommute(p, 60, 50, "0.1000", m, m - 5, m + 5, True)
        for p, m in (("a", 20.0), ("b", 30.0), ("c", 40.0))
    }
    assert mapping_b.assign_tiers(c, {"a": 1.0, "b": 1.0, "c": 1.0}) == {
        "a": "core",
        "b": "middle",
        "c": "outer",
    }


# --------------------------------------------------------------------------- A：候选密度与目标权重分开


def test_grid_weight_is_cell_mass_split_over_positions_and_unobserved_cells_stay_visible():
    own = _cell("tier_bed_struct", ("core", "1", "large_multifamily"), 30, weight=10)  # 权重和 300
    model = RentModel(
        cbsa="16980",
        min_n=30,
        cells={
            ("tier_bed_struct", ("core", "1", "large_multifamily")): own,
            ("metro_bed", ("1",)): _cell("metro_bed", ("1",), 40),
        },
        tiers={},
    )
    cfg = {
        "location_tiers": ["core"],
        "bedrooms": ["1"],
        "structures": ["large_multifamily", "single_family"],
        "price_positions": ["1/6", "1/2", "5/6"],
    }
    points = mapping_a.housing_grid(model, cfg)
    assert [p.weight for p in points] == [Fraction(100)] * 3 + [Fraction(0)] * 3
    assert sum(p.weight for p in points if p.struct == "large_multifamily") == own.weight_sum
    single = [p for p in points if p.struct == "single_family"]
    assert all(p.supported and p.cell.level == "metro_bed" for p in single)
    with pytest.raises(ValueError):
        mapping_a.housing_grid(model, {**cfg, "price_positions": ["0.25", "0.5", "0.75"]})


# --------------------------------------------------------------------------- 菜单


def _opp(
    oid,
    *,
    sector="housing",
    weight=None,
    supply="offered",
    origin="mapping_b",
    stratum=None,
    policy="annual_exposure_sample",
    priced=True,
    calendar=None,
):
    price = (
        FieldValue(
            name="price",
            status="present",
            value="10.00",
            unit="USD",
            numeraire="USD_2024_LOCAL",
            kind="observed",
        )
        if priced
        else missing("price", "missing", "测试：没有价格")
    )
    return make_opportunity(
        opportunity_id=oid,
        sector=sector,
        kind="k",
        label=oid,
        cbsa="16980",
        geo=GeoRef(
            country="US",
            scheme="CBSA2023",
            code="16980",
            level="cbsa",
            label="Chicago",
            cbsa="16980",
            relation="exact",
        ),
        origin=origin,
        method=None if origin == "record" else "housing_rent_conditional@2",
        lineage=("rec.1",),
        facts=(price,),
        required=("price",),
        supply_status=supply,
        menu_policy=policy,
        e1_category=None,
        e1_gaps=("x",),
        target_weight=weight,
        weight_basis=None if weight is None else "测试：手写权重",
        stratum=stratum,
        calendar=calendar,
        price_fact="price",
    )


def test_sampling_key_has_no_clone_and_changes_with_environment_repeat():
    params = list(inspect.signature(menus.menu_seed).parameters)
    assert params == ["build_id", "cbsa", "year", "sector", "env_repeat", "global_seed"]
    a = menus.menu_seed("b" * 64, "16980", 1, "housing", 0, "seed-x")
    assert a == menus.menu_seed("b" * 64, "16980", 1, "housing", 0, "seed-x")
    assert a != menus.menu_seed("b" * 64, "16980", 1, "housing", 1, "seed-x")
    assert a != menus.menu_seed("b" * 64, "16980", 2, "housing", 0, "seed-x")


def test_weighted_keyed_sample_selects_in_proportion_to_weight_for_one_slot():
    # 一个名额时，Efraimidis–Spirakis 抽中 a 的概率正好是 3/(3+1)；4000 次的标准差约 0.007，容差 0.03 约 4 个标准差
    hits = sum(
        menus.keyed_sample([("a", 3.0), ("b", 1.0)], 1, f"s{i}") == ["a"] for i in range(4000)
    )
    assert hits / 4000 == pytest.approx(0.75, abs=0.03)
    assert menus.keyed_sample([("a", 1.0), ("z", 0.0)], 2, "s") == ["a"]
    items = [("a", 1.0), ("b", 2.0), ("c", 3.0)]
    assert menus.keyed_sample(items, 2, "s") == menus.keyed_sample(list(reversed(items)), 2, "s")


def test_full_catalog_lists_everything_except_known_unavailable():
    opps = [
        _opp(f"goods.{i}", sector="goods", origin="record", policy="full_catalog") for i in range(5)
    ]
    opps.append(
        _opp(
            "goods.gone",
            sector="goods",
            origin="record",
            supply="not_offered",
            policy="full_catalog",
        )
    )
    opps.append(
        _opp("goods.unpriced", sector="goods", origin="record", policy="full_catalog", priced=False)
    )
    m = menus.build_menu(
        build_id="b" * 64,
        cbsa="16980",
        year=1,
        sector="goods",
        env_repeat=0,
        policy=MenuPolicy(sector="goods", kind="full_catalog"),
        opportunities=opps,
        global_seed="seed-x",
    )
    # 完整目录不抽样：资料不足的也列出（标明状态），只有确定不供应的不列
    assert m.selection == "all_eligible" and m.selected == () and m.n_eligible == 6


def test_annual_sample_never_includes_insufficient_data_and_uses_a_only_where_b_is_empty():
    opps = [_opp(f"housing.b.{i}", weight="10", stratum="1") for i in range(4)]
    opps += [
        _opp(f"housing.a.1.{i}", weight="10", origin="mapping_a", stratum="a:1") for i in range(3)
    ]
    opps += [
        _opp(f"housing.a.2.{i}", weight="10", origin="mapping_a", stratum="a:2") for i in range(3)
    ]
    opps.append(_opp("housing.b.zero", weight="0", stratum="1"))
    # 回退之后仍没有租金的候选：权重很大、在供应，但资料不足 —— 旧规则会让它进抽样
    opps += [
        _opp(f"housing.b.noprice.{i}", weight="1000000", stratum="1", priced=False)
        for i in range(3)
    ]
    policy = MenuPolicy(
        sector="housing", kind="annual_exposure_sample", strata_key="bedrooms", per_stratum=2
    )
    m = menus.build_menu(
        build_id="b" * 64,
        cbsa="16980",
        year=1,
        sector="housing",
        env_repeat=0,
        policy=policy,
        opportunities=opps,
        global_seed="seed-x",
    )
    picked = set(m.selected)
    assert not any("noprice" in i for i in picked) and m.n_eligible == 10
    assert len([i for i in picked if i.startswith("housing.b.")]) == 2
    assert len([i for i in picked if i.startswith("housing.a.2.")]) == 2
    assert "housing.b.zero" not in picked and not any(i.startswith("housing.a.1.") for i in picked)
    again = menus.build_menu(
        build_id="b" * 64,
        cbsa="16980",
        year=1,
        sector="housing",
        env_repeat=0,
        policy=policy,
        opportunities=list(reversed(opps)),
        global_seed="seed-x",
    )
    assert again.selected == m.selected


def test_calendar_menu_states_it_is_not_filtered_and_counts_known_and_unknown_apart():
    import datetime as dt

    known = Calendar(known=True, start=dt.date(2025, 10, 4), end=dt.date(2025, 10, 4))
    unknown = Calendar(known=False, reason="来源不记开课日期")
    opps = [
        _opp(
            "activities.a",
            sector="activities",
            origin="record",
            policy="calendar_open",
            calendar=known,
        ),
        _opp(
            "activities.b",
            sector="activities",
            origin="record",
            policy="calendar_open",
            calendar=known,
        ),
        _opp(
            "activities.c",
            sector="activities",
            origin="record",
            policy="calendar_open",
            calendar=unknown,
        ),
    ]
    m = menus.build_menu(
        build_id="b" * 64,
        cbsa="16980",
        year=2,
        sector="activities",
        env_repeat=0,
        policy=MenuPolicy(sector="activities", kind="calendar_open"),
        opportunities=opps,
        global_seed="seed-x",
    )
    assert m.calendar_filter == "pending_mapping"
    assert (m.n_calendar_known, m.n_calendar_unknown) == (2, 1)
    assert "没有按日历筛选" in m.inclusion_note_zh and "沿用" not in m.inclusion_note_zh
