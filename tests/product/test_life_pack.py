"""E3B 资料包编译的住房池、岗位池、菜单与地区条件（DECISIONS/0042，E3B 映射修正）。只用手写的目标格、支持表与岗位模板，不读冻结库。"""

from __future__ import annotations

from fractions import Fraction

import pytest

from product.realdata.life_pack import (
    environment_base,
    housing_opportunity,
    housing_pool,
    in_tax_scope,
    job_pool,
    load_pack_config,
    menu_size_diagnostics,
    sampled_menus,
    start_job_opportunity,
)

CFG = load_pack_config()
NYC = next(c for c in CFG["cities"] if c["key"] == "nyc")
HOU = next(c for c in CFG["cities"] if c["key"] == "hou")
RULES = {
    "scheme": "SOC2018",
    "arrangement": {"days_per_week": 5, "hybrid_onsite_share": "3/5", "burden_per_work_hour": 1.0},
    "mapping_version": "test",
}


def cell(puma: str, bed: str = "1", target: str = "100", table: str = "t1") -> dict:
    return {
        "opportunity_id": f"housing.b.x.{puma}.{bed}",
        "stratum": bed,
        "supply_status": "offered",
        "target_weight": target,
        "geo": {"code": puma, "label": f"Somewhere {puma} PUMA; State"},
        "attributes": {"support_table": table},
    }


def point(pid: str, weight: int, gross: str | None = "1500.00", **kw) -> dict:
    return {
        "id": pid,
        "weight": weight,
        "price_status": kw.get("status", "observed"),
        "gross_rent": gross,
        "contract_rent": gross,
        "kitchen": kw.get("kitchen", "complete"),
        "plumbing": kw.get("plumbing", "complete"),
        "bedrooms": kw.get("bedrooms", 1),
        "rooms": 3,
        "structure": "09",
        "year_built": 1980,
        "quality_tier": kw.get("tier", 2),
        "censor_bound": None,
        "censored_by": None,
        "records": [f"housing.acs2024.{pid}"],
    }


TABLE = {
    "table_id": "t1",
    "level": "tier_bed",
    "weight_sum": 400,
    "donors": {"n": 12, "utilities_median_month": "110.00", "assumption": "x"},
    "points": [
        point("s1", 100, "1200.00", tier=1),
        point("s2", 100, "1500.00", status="imputed"),
        point("s3", 100, None, status="censored", tier=3),
        point("s4", 100, "900.00", kitchen="incomplete", tier=1),
    ],
}
COUNTIES = {"3604101": ("36005",), "3603201": ("36059",), "3400301": ("34003",)}
HOME = {"nyc.area.h.3604101", "nyc.area.h.3603201"}


def test_tax_scope_keeps_only_nyc_residents():
    assert in_tax_scope(NYC, "36", ("36005",))
    assert not in_tax_scope(NYC, "36", ("36059",))  # 长岛：纽约州但不是纽约市
    assert not in_tax_scope(NYC, "34", ("34003",))  # 新泽西
    assert in_tax_scope(HOU, "48", ("48201",))


def test_target_weight_comes_from_the_cell_and_only_the_shape_from_the_table():
    """逻辑候选 = （目标 PUMA，支持点），权重 = 目标格权重 × 支持点权重 ÷ 表权重和；删失与厨卫不完整的支持点不进池，质量照记。"""
    pool = housing_pool(
        NYC,
        CFG,
        [cell("3604101", target="100"), cell("3603201"), cell("3400301")],
        {"t1": TABLE},
        COUNTIES,
        HOME,
    )
    assert [c.opportunity_id for c in pool.candidates] == [
        "nyc.housing.3604101.1.s1",
        "nyc.housing.3604101.1.s2",
    ]
    assert [c.weight for c in pool.candidates] == [25.0, 25.0]  # 100 × 100 ÷ 400
    assert pool.excluded == {"outside_tax_scope": 2}
    assert pool.masses["raw"] == 100 and pool.masses["executable"] == 50
    assert pool.masses["price_censored"] == 25 and pool.masses["facilities_incomplete"] == 25
    rep = pool.mass_report()
    assert rep["shares_of_raw"]["executable"] == "0.5000"


def test_a_cell_without_a_commute_area_or_weight_is_counted_not_dropped_silently():
    pool = housing_pool(
        NYC, CFG, [cell("3604101", target="0"), cell("3603201")], {"t1": TABLE}, COUNTIES, set()
    )
    assert not pool.candidates
    assert pool.excluded == {"outside_tax_scope": 1, "no_commute_area": 1}


def test_a_housing_candidate_keeps_its_own_record_facts_and_price_status():
    pool = housing_pool(NYC, CFG, [cell("3604101")], {"t1": TABLE}, COUNTIES, HOME)
    observed, imputed = (housing_opportunity(c, NYC, CFG) for c in pool.candidates)
    assert observed.price.amount.amount == pytest.approx(1200.00)
    assert observed.spec["quality_level"] == 1 and observed.spec["space_level"] == 2
    assert observed.area == "nyc.area.h.3604101" and observed.source.kind == "estimated"
    assert "ACS 同一条记录" in observed.quality and "10 户以上" in observed.quality
    assert imputed.price.amount.amount == pytest.approx(1500.00)
    assert "插补" in imputed.quality and "12 户" in imputed.quality


JOINT = {
    "all": {
        "weight": 100,
        "weight_hours_topcoded": 0,
        "weight_hours_missing": 0,
        "points": [{"hours": 40, "area": "hou.area.w.a", "weight": 100, "n": 40}],
    },
    "by_soc_major": {
        "43": {
            "weight": 100,
            "weight_hours_topcoded": 10,
            "weight_hours_missing": 0,
            "points": [
                {"hours": 40, "area": "hou.area.w.a", "weight": 60, "n": 30},
                {"hours": 20, "area": "hou.area.w.b", "weight": 20, "n": 10},
                {"hours": 30, "area": "hou.area.w.out", "weight": 10, "n": 5},
            ],
        }
    },
}


def tpl(soc: str = "43-9061", employment: int | None = 1000) -> dict:
    return {
        "soc": soc,
        "soc_major": soc.split("-")[0],
        "label": "办公室文员",
        "title": "Office Clerks, General",
        "work_mode": "onsite",
        "eligibility": [],
        "requires": [],
        "employment": employment,
        "median_annual": "40000.00",
        "wage": {
            "points": [
                {"k": 0, "quantile": "0.12", "mass": "1/25", "annual": "30000.00"},
                {"k": 1, "quantile": "0.16", "mass": "1/25", "annual": "32000.00"},
            ],
            "unidentified_tails": "1/5",
        },
        "record_id": "wage.x",
        "geography_scope": "CBSA x",
        "ep": {"entry_education": "x", "related_experience": "x", "on_the_job_training": "x"},
        "assumptions": [],
    }


def test_job_pool_is_templates_times_wage_points_times_the_joint_distribution():
    """权重 = 就业人数 × 年薪支持点概率 × 联合分布这一点的权重 ÷ 联合分布总权重（含顶码）；范围外片区与顶码不进池，质量照记。"""
    pool = job_pool(
        HOU, [tpl(), tpl("11-1021", employment=None)], JOINT, {"hou.area.w.a", "hou.area.w.b"}
    )
    ids = [c.opportunity_id for c in pool.candidates]
    assert ids == [
        "hou.job.43-9061.w00.h40.w.a",
        "hou.job.43-9061.w00.h20.w.b",
        "hou.job.43-9061.w01.h40.w.a",
        "hou.job.43-9061.w01.h20.w.b",
    ]
    # 1000 × 1/25 × 60 ÷ 100 = 24；1000 × 1/25 × 20 ÷ 100 = 8
    assert [c.weight for c in pool.candidates] == [24.0, 8.0, 24.0, 8.0]
    assert pool.excluded == {"employment_unknown": 1}
    m = pool.masses
    # 两个支持点合计 2/25 = 0.08 被识别；其余 0.92 × 1000 是两端与没列出的分位（这个手写模板只有两点）
    assert m["raw"] == 1000 and m["wage_unidentified"] == 920
    assert m["hours_topcoded"] == Fraction(8) and m["work_area_out_of_scope"] == Fraction(8)
    assert m["executable"] == 64


def test_start_job_is_the_median_wage_at_the_modal_point_and_not_in_the_pool():
    o = start_job_opportunity(
        "hou", tpl(), JOINT, {"hou.area.w.a", "hou.area.w.b"}, RULES, "USD_2024_LOCAL", "test"
    )
    assert o is not None and o.opportunity_id == "hou.job.43-9061.start"
    assert o.area == "hou.area.w.a" and o.spec["wage_position"] == "p50"
    assert o.compensation.amount.amount == pytest.approx(40000.00)  # 每周 40 小时：年薪原样
    # 低工时 = 每天工作短：20 小时的点每月 86.67 小时，但每月工作日仍是 22 天
    low = job_pool(HOU, [tpl()], JOINT, {"hou.area.w.b"})
    assert {c.data["point"]["hours"] for c in low.candidates} == {20}
    assert (
        start_job_opportunity("hou", tpl(), JOINT, set(), RULES, "USD_2024_LOCAL", "test") is None
    )


def test_menus_are_shared_by_seed_without_clone_ids_and_capped():
    pool = [(f"h{i}", 1.0 + i) for i in range(100)]
    kw = dict(
        key="hou",
        cbsa="26420",
        category="housing",
        pool=pool,
        size=60,
        years=3,
        env_repeats=2,
        seed="s",
        settings_identity="b",
    )
    a, b = sampled_menus(**kw), sampled_menus(**kw)
    assert a == b and len(a) == 6  # 同样的输入得到同样的菜单（没有分身编号可以进种子）
    assert all(len(m.opportunity_ids) == 60 and len(set(m.opportunity_ids)) == 60 for m in a)
    assert a[0].opportunity_ids != a[1].opportunity_ids
    small = sampled_menus(**{**kw, "pool": pool[:10]})
    assert all(len(m.opportunity_ids) == 10 for m in small)  # 池不够就全部


def test_size_diagnostics_use_nested_prefixes_of_one_order():
    pool = housing_pool(NYC, CFG, [cell("3604101")], {"t1": TABLE}, COUNTIES, HOME)
    d = menu_size_diagnostics(
        cbsa="35620",
        category="housing",
        pool=pool,
        describe=lambda c: (
            c.data["cell"]["stratum"],
            c.area,
            float(c.data["point"]["gross_rent"]),
        ),
        sizes=(1, 2),
        repeats=3,
        seed="s",
        settings_identity="b",
    )
    assert d["pool"]["candidates"] == 2 and d["repeats"] == 3
    assert d["sizes"]["2"]["mean_pool_weight_share"] == 1.0
    assert d["sizes"]["1"]["mean_pool_weight_share"] == 0.5


def test_environment_uses_a_fixed_scale_and_unknown_stays_unknown():
    assert environment_base(CFG, 0.5589) == pytest.approx(2.7945)  # 5 × 0.5589
    assert environment_base(CFG, None) is None
