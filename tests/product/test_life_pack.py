"""E3B 资料包编译的住房、菜单与地区条件（DECISIONS/0042）。只用手写的候选，不读冻结库。"""

from __future__ import annotations

import pytest

from product.realdata.life_pack import (
    environment_base,
    housing_items,
    in_tax_scope,
    load_pack_config,
    stratified_menus,
)

CFG = load_pack_config()
NYC = next(c for c in CFG["cities"] if c["key"] == "nyc")
HOU = next(c for c in CFG["cities"] if c["key"] == "hou")


def cand(puma: str, bed: str = "1", pos: str = "1/2", rent: str | None = "1500.00", util="120.00"):
    return {
        "opportunity_id": f"housing.b.x.{puma}.{bed}.{pos}",
        "stratum": bed,
        "supply_status": "offered",
        "target_weight": "10",
        "geo": {"code": puma, "label": f"Somewhere {puma} PUMA; State"},
        "attributes": {
            "price_position": pos,
            "utilities_median_month": util,
            "price_level_used": "tier_bed",
        },
        "facts": [
            {"name": "monthly_rent", "status": "present" if rent else "missing", "value": rent}
        ],
    }


COUNTIES = {
    "3604101": ("36005",),
    "3603201": ("36059",),
    "3400301": ("34003",),
    "4800101": ("48201",),
}


def test_tax_scope_keeps_only_nyc_residents():
    assert in_tax_scope(NYC, "36", ("36005",))
    assert not in_tax_scope(NYC, "36", ("36059",))  # 长岛：纽约州但不是纽约市
    assert not in_tax_scope(NYC, "34", ("34003",))  # 新泽西
    assert in_tax_scope(HOU, "48", ("48201",))


def test_housing_condition_and_gross_rent_by_hand():
    b = housing_items(
        NYC,
        CFG,
        [cand("3604101", "2", "5/6", "2000.00", "150.50"), cand("3603201"), cand("3400301")],
        COUNTIES,
    )
    (item,) = b.items
    assert item.opportunity_id == "nyc.housing.3604101.2.5-6"
    assert item.area == "nyc.area.h.3604101"
    assert (
        item.spec["space_level"] == 3
        and item.spec["quality_level"] == 3
        and item.spec["bedrooms"] == 2
    )
    assert item.price.amount.amount == pytest.approx(2150.50)  # 合同租金分位 + 水电燃气中位数
    assert item.source.kind == "estimated"
    assert b.excluded["outside_tax_scope"] == 2


def test_unknown_rent_or_utilities_is_excluded_not_zero():
    b = housing_items(
        HOU, CFG, [cand("4800101", rent=None), cand("4800101", bed="2", util=None)], COUNTIES
    )
    assert not b.items
    assert b.excluded == {"rent_unknown": 1, "utilities_unknown": 1}


def test_menus_are_shared_by_seed_without_clone_ids_and_stratified():
    weights = {f"h{i}": 1.0 + i for i in range(12)}
    strata = {f"h{i}": str(i % 4) for i in range(12)}
    kw = dict(
        key="hou",
        cbsa="26420",
        category="housing",
        weights=weights,
        strata=strata,
        per_stratum=2,
        size=None,
        years=3,
        env_repeats=2,
        seed="s",
        settings_identity="b",
    )
    a, b = stratified_menus(**kw), stratified_menus(**kw)
    assert a == b and len(a) == 6  # 同样的输入得到同样的菜单（没有分身编号可以进种子）
    for m in a:
        layers = [strata[o] for o in m.opportunity_ids]
        assert sorted(layers) == sorted(["0", "0", "1", "1", "2", "2", "3", "3"])
    assert (
        a[0].opportunity_ids != a[1].opportunity_ids or a[0].opportunity_ids != a[2].opportunity_ids
    )


def test_environment_uses_a_fixed_scale_and_unknown_stays_unknown():
    assert environment_base(CFG, 0.5589) == pytest.approx(2.7945)  # 5 × 0.5589
    assert environment_base(CFG, None) is None
