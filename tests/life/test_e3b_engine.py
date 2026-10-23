"""E3B 收尾的引擎适配（DECISIONS/0042）：居住条件、按片区的地区条件、通勤关系、菜单、设备故障与真实税的生活规则。

全部用合成样例 K51 上的受控计划（不调用模型）。手算口径（评分规格 configs/life/scoring_product.yaml，探索设定）：

* 居住条件 z = 1.0 × 空间等级 + 0.5 × 条件档次，τ = 3，c = 0.9；样例两处住房都是一卧（空间 2）、中档（档次 2）→ z = 3、s = 0.5。
"""

from __future__ import annotations

import pytest

from common.life_contracts import CityLifeCatalog

from .conftest import CLONE_K, K51, live_year, whole

TOL = 1e-12


def plan(housing: str = "k51.housing.far", extra=()):
    return [whole(housing), whole("k51.food.basic"), whole("k51.job.clerk"), *extra]


def catalog_with(bundle, change) -> CityLifeCatalog:
    """K51 目录的 JSON 改一处（``change`` 原地改整份目录的字典），再交给契约校验。"""
    data = bundle.catalog_for(K51).model_dump(mode="json")
    change(data)
    return CityLifeCatalog.model_validate(data)


def opp(data: dict, oid: str) -> dict:
    return next(o for o in data["opportunities"] if o["opportunity_id"] == oid)


def comp(year, month: int, cid: str):
    m = next(x for x in year.score.months if x.month == month)
    return next(c for c in m.components if c.component_id == cid)


def scored(bundle, scoring, state, items, *, catalog=None, conditions=None):
    """结算并评分一年；可以换掉这座城的评分条件（评分规格不变，所以反馈政策的内容身份照旧对得上）。"""
    from econ.life_year import ScoringInputs, run_life_year

    from .conftest import tax_for, year_action

    p = bundle.product_profiles[0]
    inputs = ScoringInputs(
        spec=scoring.spec,
        conditions=conditions or scoring.conditions_for(K51),
        preference_input=p,
        hard_requirements=p.hard_requirements,
    )
    return run_life_year(
        protocol=bundle.protocol,
        state=state,
        action=year_action(state, items),
        catalog=catalog or bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
        scoring=inputs,
    )


@pytest.fixture
def start(bundle):
    return bundle.initial_state(CLONE_K)


# --------------------------------------------------------------------------- 居住条件


def set_home(data, oid, **spec) -> None:
    o = opp(data, oid)
    o["spec"] = {**o["spec"], **spec}


def test_the_sample_homes_keep_their_hand_value(bundle, scoring, start):
    y = live_year(bundle, scoring, start, plan())
    assert comp(y, 1, "housing_condition").s == pytest.approx(3 / 6, abs=TOL)  # z = 2 + 0.5 × 2


def test_a_cheap_large_home_can_beat_an_expensive_small_one(bundle, scoring, start):
    cat = catalog_with(
        bundle,
        lambda d: (
            set_home(d, "k51.housing.far", bedrooms=3, space_level=4, quality_level=1),
            set_home(d, "k51.housing.near", bedrooms=0, space_level=1, quality_level=3),
        ),
    )
    big = live_year(bundle, scoring, start, plan("k51.housing.far"), catalog=cat)
    small = live_year(bundle, scoring, start, plan("k51.housing.near"), catalog=cat)
    assert comp(big, 1, "housing_condition").s == pytest.approx(4.5 / 7.5, abs=TOL)  # 4 + 0.5
    assert comp(small, 1, "housing_condition").s == pytest.approx(2.5 / 5.5, abs=TOL)  # 1 + 1.5
    assert comp(big, 1, "housing_condition").s > comp(small, 1, "housing_condition").s


def test_building_type_and_bedrooms_are_not_scored_again(bundle, scoring, start):
    """楼型不固定吃亏；卧室数只经由推断的空间等级起作用，不重复奖励。"""
    cat = catalog_with(
        bundle,
        lambda d: set_home(d, "k51.housing.far", bedrooms=3, building_type="apartment_10_plus"),
    )
    y = live_year(bundle, scoring, start, plan(), catalog=cat)
    assert comp(y, 1, "housing_condition").s == pytest.approx(
        0.5, abs=TOL
    )  # 只改卧室与楼型标签，空间等级没变


def test_changing_only_the_rent_keeps_the_housing_score(bundle, scoring, start):
    cat = catalog_with(
        bundle, lambda d: opp(d, "k51.housing.far")["price"]["amount"].update(amount=1100)
    )
    a = live_year(bundle, scoring, start, plan())
    b = live_year(bundle, scoring, start, plan(), catalog=cat)
    assert comp(a, 1, "housing_condition").s == comp(b, 1, "housing_condition").s
    assert a.settlement.outcome.cash_end != b.settlement.outcome.cash_end


# --------------------------------------------------------------------------- 按片区的地区条件


def with_areas(d: dict) -> None:
    d["areas"] = [
        {"area_id": "k51.area.north", "label": "北片", "kind": "home", "note": "测试片区"},
        {"area_id": "k51.area.south", "label": "南片", "kind": "home", "note": "测试片区"},
    ]
    opp(d, "k51.housing.far")["area"] = "k51.area.north"
    opp(d, "k51.housing.near")["area"] = "k51.area.south"


def safety_by_area(scoring, north: float, south: float):
    cond = scoring.conditions_for(K51)
    comps = tuple(
        c.model_copy(
            update={
                "area_bases": {"k51.area.north": north, "k51.area.south": south},
                "area_basis": "测试：片区值",
            }
        )
        if c.component_id == "safety"
        else c
        for c in cond.components
    )
    return cond.model_copy(update={"components": comps})


def test_moving_changes_the_area_safety_and_replaces_the_city_value(bundle, scoring, start):
    cat = catalog_with(bundle, with_areas)
    cond = safety_by_area(scoring, north=1.0, south=5.0)
    north = scored(bundle, scoring, start, plan("k51.housing.far"), catalog=cat, conditions=cond)
    south = scored(bundle, scoring, start, plan("k51.housing.near"), catalog=cat, conditions=cond)
    a, b = comp(north, 1, "safety"), comp(south, 1, "safety")
    assert a.s == pytest.approx(1 / 4, abs=TOL) and b.s == pytest.approx(
        5 / 8, abs=TOL
    )  # τ = 3；不再叠城市值 3
    city = live_year(bundle, scoring, start, plan("k51.housing.far"))
    assert comp(city, 1, "safety").s == pytest.approx(3 / 6, abs=TOL)


def test_only_city_level_evidence_gives_every_area_the_same_value(bundle, scoring, start):
    cat = catalog_with(bundle, with_areas)
    cond = safety_by_area(scoring, north=3.0, south=3.0)
    a = scored(bundle, scoring, start, plan("k51.housing.far"), catalog=cat, conditions=cond)
    b = scored(bundle, scoring, start, plan("k51.housing.near"), catalog=cat, conditions=cond)
    assert comp(a, 1, "safety").s == comp(b, 1, "safety").s  # 不捏造片区差异


def test_a_home_outside_the_area_table_is_unknown_not_the_city_value(bundle, scoring, start):
    def partial(d: dict) -> None:
        with_areas(d)
        d["areas"].append(
            {"area_id": "k51.area.east", "label": "东片", "kind": "home", "note": "测试"}
        )
        opp(d, "k51.housing.far")["area"] = "k51.area.east"

    cat = catalog_with(bundle, partial)
    cond = safety_by_area(scoring, north=1.0, south=5.0)
    y = scored(bundle, scoring, start, plan("k51.housing.far"), catalog=cat, conditions=cond)
    s = comp(y, 1, "safety")
    assert s.status == "unknown" and s.s is None
    assert y.score.annual_status != "complete"


def test_an_undeclared_area_is_refused(bundle):
    with pytest.raises(ValueError, match="没有在 areas 里声明"):
        catalog_with(bundle, lambda d: opp(d, "k51.housing.far").update(area="k51.area.nowhere"))
