"""A / B 映射与菜单抽样的手算例子（不需要样本文件）。"""

from __future__ import annotations

import inspect
from fractions import Fraction

import numpy as np
import pandas as pd
import pytest

from common.intake_contracts import CompileInfo, FieldValue, GeoRef, MenuPolicy
from product.realdata import mapping_a, mapping_b, menus
from product.realdata.mapping_b import RentModel, estimate_cell, wquantile, wquantile_smooth
from product.realdata.registry import make_opportunity

QS = ["0.50", "1/6", "1/2", "5/6"]


def _rent_frame(rents, weights, topcoded=None):
    n = len(rents)
    return pd.DataFrame(
        {
            "rent_contract": rents,
            "rent_gross": [r + 100 for r in rents],
            "WGTP": weights,
            "topcoded": topcoded or [False] * n,
            "struct_class": ["large_multifamily"] * n,
            "record_id": [f"r{i}" for i in range(n)],
        }
    )


def _cell(level, key, n_rows, weight=10, rent=1000):
    g = _rent_frame([rent + 100 * i for i in range(n_rows)], [weight] * n_rows)
    return estimate_cell(g, f"b.t.{level}", "16980", level, key, "new_market_proxy", QS, [])


# --------------------------------------------------------------------------- B：加权分位数与回退


def test_weighted_quantile_is_the_first_observed_value_reaching_the_weight_share():
    v = np.array([100.0, 200.0, 300.0, 400.0])
    assert wquantile(v, np.array([1.0, 1, 1, 1]), 0.5) == 200  # 累计 [1,2,3,4]，第一次 ≥ 2 是 200
    assert (
        wquantile(v, np.array([1.0, 1, 1, 5]), 0.5) == 400
    )  # 权重改变中位数：累计 [1,2,3,8]，第一次 ≥ 4 是 400


def test_interpolated_quantile_and_replicate_se_by_hand():
    v, w = np.array([100.0, 200.0]), np.array([1.0, 1.0])
    # 两个值各占一半：区间 [50,150)、[150,250]，中位数在 150
    assert wquantile_smooth(v, w, 0.5) == pytest.approx(150)
    # 每个重复权重都把第二个值加重到 3：累计 0.25 处是 150，中位数 = 150 + (0.25/0.75)×100
    rep = np.tile(np.array([[1.0], [3.0]]), (1, 80))
    theta_r = 150 + 100 / 3
    assert mapping_b.replicate_se(v, rep, w, 0.5) == pytest.approx(
        np.sqrt(4 / 80 * 80 * (theta_r - 150) ** 2)
    )


def test_cell_quantiles_are_observed_values_and_topcoded_tail_is_not_filled():
    g = _rent_frame([1000, 1200, 1400, 1600], [10, 10, 10, 10])
    cell = estimate_cell(g, "b.t", "16980", "puma_bed", ("x",), "new_market_proxy", QS, [])
    assert cell.quantiles == {
        "0.50": "1200.00",
        "1/6": "1000.00",
        "1/2": "1200.00",
        "5/6": "1600.00",
    }
    assert cell.utilities_median == "100.00"
    top = _rent_frame(
        [1000, 1200, 1400, 1600], [10, 10, 10, 10], topcoded=[False, False, False, True]
    )
    cell = estimate_cell(top, "b.t", "16980", "puma_bed", ("x",), "new_market_proxy", QS, [])
    assert (
        cell.quantiles["5/6"] is None
    )  # 5/6 落在顶码那一段（累计 0.75 以上）：只知道不低于顶码，不填数
    assert cell.quantiles["1/2"] == "1200.00"


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
    assert model.resolve([("puma_bed", ("17031", "1"))]) == (
        None,
        ["puma_bed"],
    )  # 没有就是没有，不编价格


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
    # 独栋这一格本城没有观测到：照样生成可比较的候选（价格回退到全城一卧），但权重 0，不会被抽中
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
):
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
        method=None if origin == "record" else "housing_rent_conditional@1",
        lineage=("rec.1",),
        facts=(
            FieldValue(
                name="price",
                status="present",
                value="10.00",
                unit="USD",
                numeraire="USD_2024_LOCAL",
                kind="observed",
            ),
        ),
        required=("price",),
        supply_status=supply,
        menu_policy=policy,
        compile=CompileInfo(status="needs_e1_extension", reasons=("x",)),
        target_weight=weight,
        weight_basis=None if weight is None else "测试：手写权重",
        stratum=stratum,
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
    # 一个名额时，Efraimidis–Spirakis 抽中 a 的概率正好是 3/(3+1)
    hits = sum(
        menus.keyed_sample([("a", 3.0), ("b", 1.0)], 1, f"s{i}") == ["a"] for i in range(4000)
    )
    assert hits / 4000 == pytest.approx(0.75, abs=0.03)
    assert menus.keyed_sample([("a", 1.0), ("z", 0.0)], 2, "s") == ["a"]  # 权重 0 不会被抽中
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
    assert m.selection == "all_eligible" and m.selected == () and m.n_eligible == 5


def test_annual_sample_per_stratum_uses_a_grid_only_where_b_has_nothing():
    opps = [_opp(f"housing.b.{i}", weight="10", stratum="1") for i in range(4)]
    opps += [
        _opp(f"housing.a.1.{i}", weight="10", origin="mapping_a", stratum="a:1") for i in range(3)
    ]
    opps += [
        _opp(f"housing.a.2.{i}", weight="10", origin="mapping_a", stratum="a:2") for i in range(3)
    ]
    opps.append(_opp("housing.b.zero", weight="0", stratum="1"))
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
    assert len(picked) == 4
    assert len([i for i in picked if i.startswith("housing.b.")]) == 2  # 一卧有 B 候选：不用 A
    assert len([i for i in picked if i.startswith("housing.a.2.")]) == 2  # 两卧没有 B：用 A 网格补
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
    assert again.selected == m.selected  # 重新查询、恢复都得到同一份菜单
