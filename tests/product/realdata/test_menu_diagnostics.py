"""E3B 菜单诊断（``product.realdata.menu_diagnostics``）：第 1 月判定由 E1 做，按精确键缓存。

合成样例 K51（离线、零模型调用）：文员月薪 30,000 / 12 = 2,500（样例的税不按月预扣，第 1 月工资当月到手），最低食品 380；
近处片区到就业中心公共交通往返 30 分钟、票价 0；远处公共交通 70 分钟，另加一条开车 40 分钟（每次油费 5.00，要用车权）与
每月 300 的用车权。手算：不用车时第 1 月付得起 ⇔ 月租 ≤ 现金 + 2,500 − 380 = 现金 + 2,120；用车时再减用车权 300。
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from common.life_contracts import CityLifeCatalog
from econ.life_settlement import binding_from_policy
from ledger.life_catalog import load_life_bundle
from product.realdata import menu_diagnostics as md

FIXTURE = Path("configs/life/synthetic_product.yaml")
NEAR, FAR = "k51.area.near", "k51.area.far"


def _with_car(d: dict) -> None:
    rel = d["commute"]
    rel["modes"].append(
        {
            "mode": "drive",
            "requires_function": "car_access",
            "note": "测试：要有用车权",
            "source": rel["modes"][0]["source"],
        }
    )
    far = next(g for g in rel["legs"] if g["home_area"] == FAR)
    rel["legs"].append(
        {
            **far,
            "mode": "drive",
            "roundtrip_minutes": 40,
            "cost_per_roundtrip": {"amount": 5.0, "numeraire": "USD_2024_LOCAL"},
        }
    )
    car = copy.deepcopy(
        next(o for o in d["opportunities"] if o["opportunity_id"] == "k51.service.chores")
    )
    car.update(
        opportunity_id="k51.service.car_access",
        label="用车权（测试）",
        quality="按月付费的用车权",
        price={
            "amount": {"amount": 300, "numeraire": "USD_2024_LOCAL"},
            "billing": "per_month",
            "per_unit": "service-month",
        },
        functions=["car_access"],
        resource={
            "basis": "per_service_month",
            "hours": 0,
            "burden_per_hour": 0,
            "quantity_scales": False,
            "note": "不占时间",
        },
    )
    d["opportunities"].append(car)


@pytest.fixture(scope="module")
def ctx() -> md.CityContext:
    bundle, _ = load_life_bundle(FIXTURE, require_settlement_ready=True)
    cat = next(c for c in bundle.catalogs if str(c.city).endswith("K51"))
    raw = cat.model_dump(mode="json")
    _with_car(raw)
    catalog = CityLifeCatalog.model_validate(raw)
    return md.CityContext(
        key="k51",
        raw=raw,
        catalog=catalog,
        tax=binding_from_policy(bundle.tax_policy(catalog.tax_policy_id)),
        protocol=md.diagnostic_protocol(bundle.protocol),
        start_job=catalog.opportunity("k51.job.clerk"),
        food=catalog.opportunity("k51.food.basic"),
        car=catalog.opportunity("k51.service.car_access"),
        minimum_housing="k51.housing.far",
        abilities=(),
    )


def home(ctx: md.CityContext, oid: str, area: str, rent: float) -> dict:
    h = copy.deepcopy(
        next(o for o in ctx.raw["opportunities"] if o["opportunity_id"] == "k51.housing.near")
    )
    h.update(opportunity_id=oid, area=area)
    h["price"] = {**h["price"], "amount": {**h["price"]["amount"], "amount": rent}}
    return h


def test_month_one_cash_follows_e1_by_hand(ctx):
    got = md.run_checks(
        ctx,
        [
            home(ctx, "k51.housing.t2100", NEAR, 2100),
            home(ctx, "k51.housing.t2200", NEAR, 2200),
            home(ctx, "k51.housing.t3000", NEAR, 3000),
        ],
    )
    nocar = lambda oid, cash: got[oid][(cash, False)]  # noqa: E731
    assert nocar("k51.housing.t2100", 0).status == "pass"  # 2,100 ≤ 0 + 2,120
    v = nocar("k51.housing.t2200", 0)  # 2,200 > 2,120
    assert (v.status, v.reason) == ("fail", "month1:housing_unaffordable")
    # 第 1 月：现金 0 付不起 3,000，现金 1,000（上限 3,120）与 3,000（上限 5,120）付得起
    assert [nocar("k51.housing.t3000", c).status for c in (0, 1000, 3000)] == [
        "fail",
        "pass",
        "pass",
    ]
    assert nocar("k51.housing.t2100", 0).minutes == 30


def test_a_far_home_is_reachable_only_by_car(ctx):
    got = md.run_checks(ctx, [home(ctx, "k51.housing.tfar", FAR, 1000)])["k51.housing.tfar"]
    assert (got[(0, False)].status, got[(0, False)].reason, got[(0, False)].minutes) == (
        "fail",
        "commute_over_cap",
        70,
    )
    assert (got[(0, True)].status, got[(0, True)].minutes) == ("pass", 40)  # 1,000 ≤ 2,120 − 300


def test_the_car_fee_is_paid_in_month_one_or_the_car_case_fails(ctx):
    got = md.run_checks(ctx, [home(ctx, "k51.housing.t1900", NEAR, 1900)])["k51.housing.t1900"]
    # 不用车：1,900 ≤ 2,120；用车：住房与食品付完只剩 220，付不起 300 的用车权——第 1 月用车权被拒
    assert got[(0, False)].status == "pass"
    assert (got[(0, True)].status, got[(0, True)].reason) == ("fail", "rejected_month1:car_access")
    assert got[(1000, True)].status == "pass"


def test_counts_combine_no_car_and_car(ctx):
    homes = [
        home(ctx, "k51.housing.t2100", NEAR, 2100),
        home(ctx, "k51.housing.t2200", NEAR, 2200),
        home(ctx, "k51.housing.tfar", FAR, 1000),
    ]
    got = md.run_checks(ctx, homes)
    s = md.summarize_checks([h["opportunity_id"] for h in homes], got)["cash_0"]
    # 不用车只有 2,100 的近处；用车只有远处（近处 2,100 > 1,820）；任一可行 = 2 处
    assert (s["no_car"], s["car"], s["either"], s["unknown"], s["at_least_one"]) == (
        1,
        1,
        2,
        0,
        True,
    )


def test_identical_rent_and_route_settle_once(ctx, monkeypatch):
    calls = []
    real = md.settle_year

    def counting(**kw):
        calls.append(kw["action"].items[1].opportunity_id)
        return real(**kw)

    monkeypatch.setattr(md, "settle_year", counting)
    got = md.run_checks(
        ctx, [home(ctx, "k51.housing.a", NEAR, 1500), home(ctx, "k51.housing.b", NEAR, 1500)]
    )
    assert got["k51.housing.a"] == got["k51.housing.b"]
    assert len(calls) == 6 and set(calls) == {"k51.housing.a"}  # 3 档现金 × 用不用车，各一次


def test_the_diagnostic_catalog_does_not_change_the_e1_verdict(ctx):
    near = ctx.catalog.opportunity("k51.housing.near")
    direct = {
        (c, car): md.month_one(ctx, ctx.catalog, near, c, car)
        for c in md.CASH_LEVELS
        for car in (False, True)
    }
    raw_near = next(
        o for o in ctx.raw["opportunities"] if o["opportunity_id"] == "k51.housing.near"
    )
    via = md.run_checks(ctx, [raw_near])["k51.housing.near"]
    assert {k: (v.status, v.reason) for k, v in via.items()} == {
        k: (v.status, v.reason) for k, v in direct.items()
    }


def test_unknown_prices_are_unknown_not_passed(ctx):
    near = ctx.catalog.opportunity("k51.housing.near")
    unknown = near.model_copy(update={"availability": "unknown", "price": None})
    v = md.month_one(ctx, ctx.catalog, unknown, 3000, False)
    assert v.status == "unknown" and v.reason.startswith("price_unknown")


def test_housing_that_differs_beyond_rent_and_area_refuses_the_cache(ctx):
    a = home(ctx, "k51.housing.a", NEAR, 1500)
    b = home(ctx, "k51.housing.b", NEAR, 1500)
    b["price"] = {**b["price"], "billing": "per_year"}
    with pytest.raises(Exception, match="不再精确"):
        md.assert_housing_uniform([a, b])


def test_jobs_need_qualification_and_a_home_within_sixty_minutes(ctx):
    jobs = [ctx.catalog.opportunity(i) for i in ("k51.job.clerk", "k51.job.accountant")]
    # 会计要 accounting_cert（诊断人物没有）；文员从远处片区公交 70 分钟、开车 40 分钟
    far_only = md.job_diagnostic(ctx, jobs, {FAR})
    assert far_only == {
        "jobs": 2,
        "qualified": 1,
        "qualified_reachable_no_car": 0,
        "qualified_reachable_car": 1,
    }
    near = md.job_diagnostic(ctx, jobs, {NEAR, FAR})
    assert near["qualified_reachable_no_car"] == 1


def test_the_full_pool_baseline_needs_the_same_pool_inputs_as_the_pack():
    recorded = {k: f"id-{k}" for k in md.POOL_INPUTS} | {"base_files": "old"}
    same = dict(recorded, base_files="new")  # 基础样例只进协议与评分，不改完整池
    assert md.pool_input_mismatch(recorded, same) == []
    assert md.pool_input_mismatch(recorded, dict(same, items="changed")) == ["items"]
    assert md.pool_input_mismatch({}, same) == list(md.POOL_INPUTS)
