"""真实小样本（芝加哥都会区，``tests/fixtures/intake``）上的完整构建：来源 → 标准化 → A/B → 机会 → 查询 / 菜单 → 检查 → 冻结。

样本是真实文件的子集，只检验流程；这里的数字不代表现实分布。金额、身份与计数按精确规则比较；统计量的精度在用到的地方写明。
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import math
import shutil
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from common.intake_contracts import CompileInfo, Menu, Opportunity, StdRecord, applies_to
from ledger import intake_data
from product.realdata import build, e1_export, goods, jobs, life_inputs, query, store
from product.realdata.activities import PRIVATE_ACCESS, _session_hours, _text
from product.realdata.config import load_config
from product.realdata.prices import Cpi, to_cents_text

from .support import CACHE, CONFIG

REPO = Path(__file__).resolve().parents[3]


def _jsonl(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(ln) for ln in f if ln.strip()]


@pytest.fixture(scope="module")
def built(fixture_build):
    ctx, refs = fixture_build
    opps = [
        Opportunity.model_validate(d)
        for d in _jsonl(ctx.work / refs["map"].path / "opportunities.jsonl.gz")
    ]
    recs = []
    for p in sorted((ctx.work / refs["standardize"].path).glob("records_*.jsonl.gz")):
        recs += [StdRecord.model_validate(d) for d in _jsonl(p)]
    menus = [
        Menu.model_validate(d) for d in _jsonl(ctx.work / refs["catalog"].path / "menus.jsonl.gz")
    ]
    db = ctx.work / refs["freeze"].path / "intake.sqlite"
    return ctx, refs, opps, recs, menus, db


def _report(ctx, refs, name):
    return json.loads((ctx.work / refs["check"].path / f"{name}.json").read_text(encoding="utf-8"))


def _cli():
    spec = importlib.util.spec_from_file_location(
        "intake_data_cli", REPO / "scripts/intake_data.py"
    )
    assert spec is not None and spec.loader is not None
    sys.path.insert(0, str(REPO / "scripts"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_cli(capsys, db, *args):
    assert _cli().main(["--db-file", str(db), *args]) == 0
    return json.loads(capsys.readouterr().out)


# --------------------------------------------------------------------------- 构建、来源、溯源


def test_build_passes_checks_and_names_every_source_it_could_not_get(built):
    _, refs, *_ = built
    assert refs["check"].summary["problems"] == 0
    not_ok = refs["fetch"].summary["not_ok"]
    assert set(not_ok) == {"hud_fmr", "pums_h:18", "pums_p:18"}
    assert "blocked 202" in not_ok["hud_fmr"]
    assert refs["standardize"].summary["skipped"]["pums:18"]
    assert refs["standardize"].summary["topcodes"] == {"17": "official"}
    counts = refs["standardize"].summary["records"]
    assert all(
        counts[s] > 0
        for s in ("housing", "jobs", "food", "goods", "activities", "training", "background")
    )


def test_records_keep_source_period_geography_raw_and_method(built):
    _, _, _, recs, _, _ = built
    for r in recs:
        assert (
            r.geo.country in ("US", "CA")
            and r.geo.scheme
            and r.source.source_id
            and r.source.locator
        )
        assert r.period.start <= r.period.end
        for f in r.fields:
            if f.status != "present":
                assert f.reason, (r.record_id, f.name)
            elif (f.unit or "").startswith("USD"):
                assert f.numeraire, (r.record_id, f.name)
            if (
                f.kind in ("estimated", "derived")
                and f.status == "present"
                and f.name != "bedrooms_class"
            ):
                assert f.method, (r.record_id, f.name)
    tuition = next(
        r
        for r in recs
        if r.record_type == "training_program" and r.field("tuition_fees").status == "present"
    )
    fee = tuition.field("tuition_fees")
    assert fee.raw and fee.kind == "estimated" and "cpi_u_period_conversion@1" in fee.method


def test_every_opportunity_traces_to_records_or_condition_tables(built):
    *_, db = built
    conn = store.open_readonly(db)
    try:
        records = {r for (r,) in conn.execute("SELECT record_id FROM records")}
        tables = {t for (t,) in conn.execute("SELECT table_id FROM tables")}
        for (raw,) in conn.execute("SELECT json FROM opportunities"):
            o = json.loads(raw)
            assert o["lineage"] and all(ref in records or ref in tables for ref in o["lineage"]), o[
                "opportunity_id"
            ]
    finally:
        conn.close()


# --------------------------------------------------------------------------- 编译缺口：资料与 E1 分开（口径 2）


def test_data_gaps_and_e1_gaps_are_reported_separately(built):
    ctx, refs, opps, *_ = built
    by_kind: dict[str, list[Opportunity]] = {}
    for o in opps:
        by_kind.setdefault(o.kind, []).append(o)
    listing = next(o for o in by_kind["rental_support_cell"] if o.attributes.get("support_table"))
    # 住房缺面积、通勤不是按房源算的：都是资料缺口，改 E1 也执行不了（旧报告把它写成“需要 E1 扩展”）
    assert listing.compile.status == "needs_data" and listing.compile.e1_gaps == ()
    assert "missing:floor_area" in listing.compile.data_gaps
    restaurant = by_kind["restaurant_venue"][0]
    assert restaurant.compile.status == "needs_data_and_e1_extension"
    assert "missing:price" in restaurant.compile.data_gaps and restaurant.compile.e1_gaps == (
        "e1_has_no_meal_level_choice",
    )
    item = by_kind["food_item"][0]
    assert item.compile.status == "needs_e1_extension" and item.compile.data_gaps == ()
    assert all(o.compile.status == "reference_only" for o in by_kind["city_fact"])
    assert not any(o.capabilities.executable for o in opps)
    gaps = _report(ctx, refs, "gaps")["sectors"]
    for sector, g in gaps.items():
        assert sum(g["status"].values()) == g["total"], sector  # 五种状态互不重叠
    food = gaps["food"]
    both = food["status"]["needs_data_and_e1_extension"]
    assert food["any_data_gap"] + food["any_e1_gap"] - both == food["total"] - food["status"].get(
        "compilable", 0
    )
    cov = _report(ctx, refs, "coverage")["metros"]["16980"]["sectors"]
    for sector, v in cov.items():
        parts = (
            v["compilable"]
            + v["needs_data_only"]
            + v["needs_e1_only"]
            + v["needs_data_and_e1"]
            + v["reference_only"]
        )
        assert parts == v["opportunities"], sector


def test_city_food_prices_are_derived_not_local_observations(built):
    ctx, refs, opps, *_ = built
    items = [o for o in opps if o.kind == "food_item"]
    assert items and all(o.origin == "derived" and not o.capabilities.collected for o in items)
    assert all(o.method == "rpp_goods_spatial_adjustment@1" for o in items)
    assert all(ref.startswith("food.bls_ap.") or "ap" in ref for o in items for ref in o.lineage)
    eggs = next(o for o in items if o.opportunity_id.endswith(".708111"))
    assert (
        eggs.fact("price").kind == "estimated" and eggs.fact("price").numeraire == "USD_2024_LOCAL"
    )
    cov = _report(ctx, refs, "coverage")["metros"]["16980"]["sectors"]["food"]
    assert cov["by_origin"]["derived"] == len(items)
    assert cov["by_origin"]["record"] == sum(1 for o in opps if o.kind == "restaurant_venue")


# --------------------------------------------------------------------------- F2：活动的日期与年龄限制


def test_toddler_and_dated_activities_are_not_exported_as_anytime_any_age_experiences(built):
    ctx, refs, opps, *_ = built
    progs = [o for o in opps if o.kind == "activity_program"]
    toddler = next(
        o for o in progs if str(o.attributes.get("age_rule", "")).startswith("At least 1 years")
    )
    assert (toddler.attributes["age_min_years"], toddler.attributes["age_max_years_exclusive"]) == (
        "1.5",
        "3",
    )
    assert {"age_eligibility_not_expressible", "dated_calendar_not_expressible"} <= set(
        toddler.compile.e1_gaps
    )
    assert toddler.calendar is not None and toddler.calendar.known and toddler.calendar.start
    assert not toddler.capabilities.executable
    early = next(o for o in progs if o.attributes.get("age_rule") == "Early Childhood")
    assert (
        early.attributes["age_group"] == "Early Childhood"
        and early.attributes["age_min_years"] is None
    )
    all_ages = next(
        o
        for o in progs
        if o.attributes.get("age_restricted") is False and o.calendar.start == o.calendar.end
    )
    assert all_ages.compile.e1_gaps == (
        "dated_calendar_not_expressible",
    )  # 不限年龄，但有具体日期：仍然不能导出
    assert all(o.compile.status != "compilable" for o in progs)
    # 导出本身也拒绝：即使有人把编译状态改成“可编译”，带日期或年龄限制的活动也不会变成普通体验
    forced = toddler.model_copy(update={"compile": CompileInfo.of(e1_category="experience")})
    with pytest.raises(ValueError, match="dated_calendar"):
        e1_export.to_life_opportunity(forced)
    undated = forced.model_copy(update={"calendar": None})
    with pytest.raises(ValueError, match="age_eligibility"):
        e1_export.to_life_opportunity(undated)
    exp = json.loads((ctx.work / refs["check"].path / "e1_export.json").read_text(encoding="utf-8"))
    assert exp["summary"]["compiled"] == 0 and exp["summary"]["failed"] == 0


def test_calendar_menus_say_they_are_not_filtered_and_count_known_and_unknown(built):
    _, _, opps, _, menus, _ = built
    cal = [m for m in menus if m.policy == "calendar_open"]
    assert cal and all(m.calendar_filter == "pending_mapping" for m in cal)
    training = next(m for m in cal if m.sector == "training" and m.year == 1 and m.env_repeat == 0)
    assert (
        training.n_calendar_known == 0 and training.n_calendar_unknown == training.n_eligible
    )  # IPEDS 不记开课日期
    acts = next(m for m in cal if m.sector == "activities" and m.year == 1 and m.env_repeat == 0)
    listed = [o for o in opps if o.opportunity_id in set(acts.selected)]
    assert acts.n_calendar_known == sum(1 for o in listed if o.calendar and o.calendar.known)
    assert acts.n_calendar_known + acts.n_calendar_unknown == acts.n_eligible
    assert all("没有按日历筛选" in m.inclusion_note_zh for m in cal)


# --------------------------------------------------------------------------- F3：家电的适用市场


def test_canada_only_models_stay_in_the_database_but_not_in_us_catalogs(built):
    ctx, refs, opps, _, menus, db = built
    g = [o for o in opps if o.sector == "goods"]
    scopes = {o.attributes["market_scope"] for o in g}
    assert {"us", "us_ca", "ca"} <= scopes
    ca = [o for o in g if o.attributes["market_scope"] == "ca"]
    assert ca and all(o.geo.country == "CA" and not applies_to(o, "16980", "US") for o in ca)
    us = [o for o in g if o.geo.country == "US"]
    menu = next(m for m in menus if m.sector == "goods")
    assert menu.n_eligible == len(us)
    conn = store.open_readonly(db)
    try:
        in_metro = query.opportunities(conn, sector="goods", cbsa="16980", per_page=1000)
        everywhere = query.opportunities(conn, sector="goods", per_page=1000)
    finally:
        conn.close()
    assert in_metro.total == len(us) and not any(
        o["geo"]["country"] == "CA" for o in in_metro.items
    )
    assert everywhere.total == len(g)  # 资料库里查得到加拿大型号
    gm = _report(ctx, refs, "coverage")["goods_markets"]
    assert gm["in_us_catalog"] == len(us) and gm["by_market_scope"]["ca"] == len(ca)
    assert _report(ctx, refs, "coverage")["metros"]["16980"]["sectors"]["goods"][
        "opportunities"
    ] == len(us)


def test_market_scope_covers_us_canada_both_and_unknown():
    rows = [
        {"pd_id": i, "brand_name": "B", "model_number": f"M{i}", "markets": m}
        for i, m in enumerate(["United States", "Canada", "United States, Canada", None, ""])
    ]
    recs = list(goods.records(rows, "dishwasher", None))
    assert [r.attributes["market_scope"] for r in recs] == [
        "us",
        "ca",
        "us_ca",
        "unknown",
        "unknown",
    ]
    assert [r.geo.country for r in recs] == ["US", "CA", "US", "US", "US"]
    assert recs[3].geo.relation == "proxy"  # 市场未知：美国只是代理，不当成确定可得，也不当成不可得
    opps = [goods.opportunity(r) for r in recs]
    assert [applies_to(o, "16980", "US") for o in opps] == [True, False, True, True, True]


# --------------------------------------------------------------------------- F5 / F4：真实样本上的顶码与成对估计


def test_official_topcodes_mark_only_replacement_values_in_the_real_sample(built):
    ctx, refs, _, recs, _, _ = built
    rules = json.loads(
        (ctx.work / refs["standardize"].path / "topcodes.json").read_text(encoding="utf-8")
    )
    assert (
        rules["rules"]["17"]["threshold"] == 3800.0
        and rules["rules"]["17"]["replacement"] == 5100.0
    )
    assert rules["source_sha256"]
    housing = [r for r in recs if r.sector == "housing"]
    top = [r for r in housing if r.attributes["topcode_status"] == "topcoded"]
    assert top and all(r.field("contract_rent_month").raw == "5100" for r in top)
    assert all(
        r.field("contract_rent_month").status == "suppressed"
        and "3800" in r.field("contract_rent_month").reason
        for r in top
    )
    plain = [r for r in housing if r.attributes["topcode_status"] == "not_topcoded"]
    largest = max(plain, key=lambda r: float(r.field("contract_rent_month").value))
    assert float(largest.field("contract_rent_month").value) <= 3800  # 普通的最大值照常是观测值
    assert largest.field("contract_rent_month").status == "present"


def _step_median(v, weights):
    order = np.argsort(v, kind="stable")
    cum = np.cumsum(weights[order])
    return float(v[order][np.searchsorted(cum, 0.5 * cum[-1])])


def _interp_median(v, weights):
    uniq = np.unique(v)
    mass = np.array([weights[v == u].sum() for u in uniq])
    lo, hi = uniq[0] - (uniq[1] - uniq[0]) / 2, uniq[-1] + (uniq[-1] - uniq[-2]) / 2
    edges = np.concatenate([[lo], (uniq[:-1] + uniq[1:]) / 2, [hi]])
    return float(np.interp(0.5, np.concatenate([[0], np.cumsum(mass) / mass.sum()]), edges))


def _replicate_se(fn, v, w, rep):
    est = fn(v, w)
    return math.sqrt(4 / 80 * sum((fn(v, rep[:, r]) - est) ** 2 for r in range(rep.shape[1])))


def test_cell_median_pairs_match_an_independent_recomputation(built):
    """取冻结的条件表记下的样本独立重算。没有顶码记录的格：两种中位数与各自的重复权重标准误逐项对上（阶梯值精确相等；插值值与
    标准误按表里保留的两位小数比较，容差 0.005）。有顶码记录的格：高于本州阈值的分位数不填，可识别的阶梯中位数不高于阈值。"""
    ctx, refs, *_ = built
    hdf = pd.read_parquet(ctx.work / refs["standardize"].path / "housing_16980.parquet")
    tables = [
        t
        for t in _jsonl(ctx.work / refs["map"].path / "tables.jsonl.gz")
        if t["table_id"].startswith("b.housing.")
    ]
    plain = censored = 0
    for t in tables:
        g = hdf[hdf["record_id"].isin(t["record_ids"])]
        if len(np.unique(g["rent_contract"])) < 2:
            continue
        assert len(g) == t["n"]
        v, w = g["rent_contract"].to_numpy(float), g["WGTP"].to_numpy(float)
        rep = g[[f"WGTP{i}" for i in range(1, 81)]].to_numpy(float).clip(0, None)
        if g["censored"].any():
            assert t["censor_bound_min"] == 3800.0
            assert all(q is None or float(q) <= 3800 for q in t["quantiles"].values())
            assert t["median_step"] is None or t["median_step"] <= 3800
            censored += 1
            continue
        assert t["censor_bound_min"] is None
        assert t["median_step"] == _step_median(v, w)
        assert t["se_median_step"] == pytest.approx(
            round(_replicate_se(_step_median, v, w, rep), 2), abs=0.005
        )
        assert t["median_interpolated"] == pytest.approx(round(_interp_median(v, w), 2), abs=0.005)
        assert t["se_median_interpolated"] == pytest.approx(
            round(_replicate_se(_interp_median, v, w, rep), 2), abs=0.005
        )
        plain += 1
    assert plain >= 1 and censored >= 1


def test_housing_cells_keep_the_full_weighted_support_and_borrow_only_shape(built):
    """B 住房（housing_rent_support@1）：每个目标格引用一张完整支持表；支持点合并后权重不变；回退只借形状，总权重取目标格。"""
    ctx, refs, opps, *_ = built
    tables = {
        t["table_id"]: t
        for t in _jsonl(ctx.work / refs["map"].path / "tables.jsonl.gz")
        if t["method"] == "housing_rent_support@1"
    }
    cells = [o for o in opps if o.kind == "rental_support_cell"]
    assert cells and tables
    assert not [o for o in opps if o.kind == "rental_listing_candidate"]  # 旧的三个价位已经删除
    for t in tables.values():
        pts = t["points"]
        assert sum(p["weight"] for p in pts) == t["weight_sum"]  # 合并相同特征不改变概率质量
        m = t["masses"]
        assert m["observed"] + m["imputed"] + m["censored"] + m["unknown"] == m["raw"]
        assert (
            m["facilities_complete"] + m["facilities_incomplete"] + m["facilities_unknown"]
            == m["raw"]
        )
        for p in pts:
            assert p["quality_tier"] in (1, 2, 3)
            assert (p["gross_rent"] is None) == (p["price_status"] in ("censored", "unknown"))
            if p["price_status"] == "censored":
                assert p["censored_by"] is not None
        ids = [r for p in pts for r in p["records"]]
        assert len(ids) == len(set(ids)) == t["n"]  # 每条原始记录恰好进一个支持点
    # 目标格的总权重 = 本格新租市场代理样本的 WGTP 之和，不论用的是哪一层的形状
    _, _, _, recs, _, _ = built
    market: dict[tuple[str, str], int] = {}
    for r in recs:
        a = r.attributes
        if (
            r.record_type == "rental_unit_sample"
            and a.get("full_puma")
            and a.get("segment") in ("vacant_for_rent", "recent_mover")
        ):
            k = (r.geo.code, str(a["bedroom_class"]))
            market[k] = market.get(k, 0) + int(r.weight)
    for o in cells:
        assert Decimal(o.target_weight) == market[(o.geo.code, o.stratum)]
        t = tables.get(str(o.attributes.get("support_table")))
        if t is not None and t["level"] != "puma_bed":
            assert o.attributes["shape_borrowed"] is True  # 回退：只借上层分布的形状


def test_food_plan_cost_is_basket_quantity_times_item_price(built):
    ctx, _, opps, _, _, _ = built
    price = {
        o.opportunity_id.rsplit(".", 1)[1]: Decimal(o.fact("price").value)
        for o in opps
        if o.kind == "food_item" and o.fact("price").status == "present"
    }
    spec = ctx.config.mapping["a_food"]["baskets"]["staples_home"]
    weekly = sum(
        price[str(line["item"]).lower()] * Decimal(str(line["qty"])) for line in spec["items"]
    )
    plan = next(o for o in opps if o.opportunity_id == "food.plan.16980.staples_home")
    assert plan.fact("monthly_food_cost").value == str((weekly * 52 / 12).quantize(Decimal("0.01")))
    assert plan.compile.status == "needs_data"
    assert {"missing:prep_hours_month", "missing:fresh_meals_per_week"} <= set(
        plan.compile.data_gaps
    )


def test_cli_query_and_menu_print_amounts_with_unit_numeraire_and_reasons(built, capsys):
    *_, db = built
    rent = _run_cli(
        capsys, db, "query", "--sector", "housing", "--cbsa", "16980", "--per-page", "500"
    )["items"]
    cells = [i for i in rent if i["opportunity_id"].startswith("housing.b.")]
    # 住房目标格没有单一价格：价格在它引用的完整支持表里（每个支持点一行），查询里金额是空
    assert cells and all(i["amount"] is None for i in cells)
    plans = _run_cli(
        capsys, db, "query", "--sector", "food", "--kind", "food_plan", "--per-page", "50"
    )["items"]
    priced = [i for i in plans if i["amount"] and i["amount"].get("value") is not None]
    assert priced and all(
        i["amount"]["fact"] == "monthly_food_cost" and i["amount"]["unit"] == "USD/month"
        for i in priced
    )
    assert all(i["amount"]["numeraire"] == "USD_2024_LOCAL" for i in priced)
    acts = _run_cli(
        capsys,
        db,
        "query",
        "--sector",
        "activities",
        "--kind",
        "activity_program",
        "--per-page",
        "500",
    )["items"]
    free = [
        i
        for i in acts
        if i["amount"].get("value") is not None and Decimal(i["amount"]["value"]) == 0
    ]
    assert free and all(
        i["amount"]["unit"] == "USD/registration" for i in free
    )  # 免费活动是有值的 0，不是缺失
    plan = _run_cli(capsys, db, "query", "--sector", "food", "--kind", "food_plan")["items"]
    assert all(
        i["amount"]["fact"] == "monthly_food_cost" and i["amount"]["kind"] == "assumption"
        for i in plan
    )
    goods_items = _run_cli(capsys, db, "query", "--sector", "goods", "--per-page", "3")["items"]
    assert all(
        i["amount"] == {"fact": "price", "status": "missing", "reason": i["amount"]["reason"]}
        for i in goods_items
    )
    menu = _run_cli(capsys, db, "menu", "--cbsa", "16980", "--year", "1", "--sector", "housing")
    assert menu["items"] and all(i["amount"]["value"] for i in menu["items"])


def test_goods_and_food_menus_are_full_catalogs_and_paging_reaches_every_item(built):
    _, _, opps, _, menus, db = built
    goods_us = [
        o
        for o in opps
        if o.sector == "goods" and o.geo.country == "US" and o.supply_status != "not_offered"
    ]
    gm = [m for m in menus if m.sector == "goods"]
    assert gm and all(m.selection == "all_eligible" and m.n_eligible == len(goods_us) for m in gm)
    conn = store.open_readonly(db)
    try:
        seen, page = [], 1
        while True:
            p = query.menu_items(conn, gm[0].model_dump(mode="json"), page=page, per_page=7)
            seen += [o["opportunity_id"] for o in p.items]
            if page * 7 >= p.total:
                break
            page += 1
        assert sorted(seen) == sorted(o.opportunity_id for o in goods_us)
    finally:
        conn.close()


def test_menus_are_unique_per_environment_and_independent_of_clones(built):
    _, _, _, _, menus, _ = built
    keys = [(m.cbsa, m.year, m.sector, m.env_repeat) for m in menus]
    assert len(keys) == len(set(keys))
    a = {(m.year, m.env_repeat): m for m in menus if m.sector == "activities"}
    assert a[(1, 0)].seed != a[(1, 1)].seed and a[(1, 0)].seed != a[(2, 0)].seed
    assert all(m.selection == "listed" and m.selected for m in a.values())
    # 岗位在 intake 里没有菜单：岗位候选池与年度岗位菜单只在 E3B 资料包里
    assert not any(m.sector == "jobs" for m in menus)
    # 住房的候选池是完整支持表的目标格：全部列出，不在 intake 里抽年度菜单（年度菜单在 E3B 资料包里从展开的完整池抽）
    h = [m for m in menus if m.sector == "housing"]
    assert h and all(m.policy == "full_catalog" and m.selection == "all_eligible" for m in h)


def test_rerun_reuses_every_stage_and_gives_the_same_build(built):
    ctx, refs, *_ = built
    again = build.run_all(ctx)
    assert all(r.summary["reused"] for r in again.values())
    assert again["freeze"].identity == refs["freeze"].identity


def test_query_filters_page_and_lineage(built):
    *_, db = built
    conn = store.open_readonly(db)
    try:
        venues = {"sector": "food", "cbsa": "16980", "kind": "restaurant_venue", "per_page": 5}
        p1 = query.opportunities(conn, **venues)
        p2 = query.opportunities(conn, **venues, page=2)
        assert p1.total > 10 and len(p1.items) == 5
        assert {o["opportunity_id"] for o in p1.items}.isdisjoint(
            o["opportunity_id"] for o in p2.items
        )
        takeaway = p1.items[0]["attributes"]["takeaway"]
        filtered = query.opportunities(
            conn, sector="food", attributes={"takeaway": takeaway}, per_page=500
        )
        assert filtered.total > 0 and all(
            o["attributes"]["takeaway"] == takeaway for o in filtered.items
        )
        lin = query.lineage(conn, "food.plan.16980.staples_home")
        assert lin["records"] and all(r["source"]["source_id"] == "bls_ap" for r in lin["records"])
    finally:
        conn.close()


def test_restaurant_channel_is_unknown_unless_tagged(built):
    _, _, opps, _, _, _ = built
    rest = [o for o in opps if o.kind == "restaurant_venue"]
    assert rest and "unknown" in {o.attributes.get("takeaway") for o in rest}


def test_activity_text_is_unescaped_and_private_venues_are_not_listed(built):
    _, _, _, recs, _, _ = built
    acts = [r for r in recs if r.sector == "activities"]
    assert not any("&amp;" in (r.label + json.dumps(r.attributes)) for r in acts)
    assert not any(
        r.attributes.get("access") in PRIVATE_ACCESS
        for r in acts
        if r.record_type == "activity_venue"
    )


# --------------------------------------------------------------------------- 岗位：intake 只留来源事实（旧的三档岗位候选已删除）

#: 迁移前的代码（从三档岗位候选的中位数档读入职要求）在同一份小样本上给出的值，逐字记下：迁移后从 EP 记录读必须完全相同。
#: 唯一的有意改动：EP 表里写 "None"（不需要）的格子以前被 pandas 读成缺失、写成 "nan"，现在照原文是 "None"（E3B 收尾）。
EP_BEFORE_MIGRATION = {
    "11-1011": ("Bachelor's degree", "5 years or more", "None"),
    "11-9051": (
        "High school diploma or equivalent",
        "Less than 5 years",
        "Short-term on-the-job training",
    ),
    "13-1031": ("High school diploma or equivalent", "None", "Long-term on-the-job training"),
    "13-1032": ("Postsecondary nondegree award", "None", "Moderate-term on-the-job training"),
    "13-1071": ("Bachelor's degree", "None", "None"),
}


def _ep_table() -> dict[str, dict[str, str]]:
    raw = next((CACHE / "bls_ep_education").glob("*.bin")).read_bytes()
    return jobs.parse_ep_education(raw)["occupations"]


def test_jobs_are_source_facts_without_candidates_or_menus(built):
    _, _, opps, recs, menus, _ = built
    assert not any(o.sector == "jobs" or o.kind == query.RETIRED_JOB_KIND for o in opps)
    assert not any(m.sector == "jobs" for m in menus)
    table = _ep_table()
    ep = {
        r.attributes["occupation_code"]: r
        for r in recs
        if r.record_type == "occupation_entry_requirements"
    }
    assert set(ep) == set(table)
    for soc, r in ep.items():
        assert r.record_id == f"jobs.ep.{soc}" and r.geo.level == "national" and r.geo.cbsa is None
        assert r.source.source_id == "bls_ep_education" and r.source.raw_sha256
        assert {f.name: f.value for f in r.fields} == {
            name: table[soc][key] for key, name in jobs.EP_FIELDS.items()
        }
        assert all(f.kind == "observed" and f.raw == f.value for f in r.fields)
    oews = [r for r in recs if r.record_type == "occupation_wage_distribution"]
    assert oews and all(r.geo.cbsa == "16980" for r in oews)


def test_life_inputs_read_entry_requirements_from_ep_records_unchanged(built):
    *_, db = built
    names = list(jobs.EP_FIELDS.values())
    conn = store.open_readonly(db)
    try:
        before = {soc: dict(zip(names, v, strict=True)) for soc, v in EP_BEFORE_MIGRATION.items()}
        assert life_inputs._ep(conn, sorted(before), ["16980"]) == before
        socs = sorted(
            rid.rsplit(".", 1)[1]
            for (rid,) in conn.execute(
                "SELECT record_id FROM records WHERE record_type = 'occupation_wage_distribution'"
            )
        )
        table = _ep_table()
        with_ep = [s for s in socs if s in table]
        assert life_inputs._ep(conn, with_ep, ["16980"]) == {
            s: {name: table[s][key] for key, name in jobs.EP_FIELDS.items()} for s in with_ep
        }
        # 没有任何都会区 OEWS 记录的职业不进切片（与迁移前相同）
        assert life_inputs._ep(conn, ["99-9999"], ["16980"]) == {}
        # 有 OEWS 记录但 EP 没有分配的职业（小样本里是 11-1031 立法者）：明确报错，不补
        no_ep = [s for s in socs if s not in table]
        assert no_ep
        with pytest.raises(life_inputs.SliceError, match="EP 入职要求"):
            life_inputs._ep(conn, no_ep[:1], ["16980"])
    finally:
        conn.close()


def test_old_job_queries_are_refused_with_a_pointer_to_the_life_pack(built, capsys):
    *_, db = built
    conn = store.open_readonly(db)
    try:
        for call in (
            lambda: query.opportunities(conn, sector="jobs", cbsa="16980"),
            lambda: query.opportunities(conn, kind="job_opening_candidate"),
            lambda: query.menu(conn, "16980", 1, "jobs"),
            lambda: query.lineage(conn, "jobs.16980.11-1011.median"),
        ):
            with pytest.raises(query.RetiredJobQueryError, match="data/frozen/life/us-v1/pack/"):
                call()
        with pytest.raises(KeyError):
            query.lineage(conn, "food.no.such.item")
        wages = query.records(
            conn, sector="jobs", cbsa="16980", record_type="occupation_wage_distribution"
        )
        assert wages.total > 10 and all(r["geo"]["cbsa"] == "16980" for r in wages.items)
        req = {"sector": "jobs", "cbsa": "16980", "record_type": "occupation_entry_requirements"}
        assert query.records(conn, **req).total == len(_ep_table())  # 全国记录随都会区一起给
        assert query.records(conn, **req, include_national=False).total == 0
    finally:
        conn.close()
    cli = _cli()
    assert cli.main(["--db-file", str(db), "query", "--sector", "jobs"]) == 2
    err = capsys.readouterr().err
    assert "已废弃" in err and "scripts/life_pack_us.py build" in err
    menu_args = ["menu", "--cbsa", "16980", "--year", "1", "--sector", "jobs"]
    assert cli.main(["--db-file", str(db), *menu_args]) == 2
    assert "生活资料包" in capsys.readouterr().err
    out = _run_cli(capsys, db, "records", "--sector", "jobs", "--cbsa", "16980", "--per-page", "3")
    assert out["returned"] == 3 and out["total"] > 3


def test_config_with_a_job_menu_policy_is_rejected(tmp_path):
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    raw["menus"]["policies"]["jobs"] = {
        "kind": "annual_exposure_sample",
        "strata_key": "soc_major",
        "size": 30,
    }
    old = tmp_path / "old.yaml"
    old.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="E3B 生活资料包"):
        load_config(old)


# --------------------------------------------------------------------------- 检查器


def _check(ctx, refs, catalog):
    return intake_data.check_build(
        ctx.work / refs["standardize"].path,
        ctx.work / refs["map"].path,
        catalog,
        source_ids={s.source_id for s in ctx.config.sources},
        metro_cbsas=[m.cbsa for m in ctx.config.metros],
        methods={m.ref for m in build.all_methods()},
        assumptions={a.assumption_id for a in build.all_assumptions()},
        country=ctx.config.build.country,
    )


def _tamper(ctx, refs, tmp_path, change):
    catalog = tmp_path / "catalog"
    shutil.copytree(ctx.work / refs["catalog"].path, catalog)
    docs = _jsonl(catalog / "menus.jsonl.gz")
    change(docs)
    with gzip.open(catalog / "menus.jsonl.gz", "wt", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return _check(ctx, refs, catalog)


def test_ledger_catches_menu_items_outside_the_eligible_set(built, tmp_path):
    ctx, refs, opps, *_ = built
    outside = next(o.opportunity_id for o in opps if o.sector == "food")

    def add_unpriced(docs):
        target = next(d for d in docs if d["sector"] == "activities" and d["selected"])
        target["selected"] = [*target["selected"][:-1], outside]

    problems = _tamper(ctx, refs, tmp_path, add_unpriced)
    assert any("合格集合以外" in p for p in problems)


def test_ledger_field_rules_by_hand():
    fields = [
        {"name": "rent", "status": "present", "unit": "USD/month", "numeraire": None},
        {
            "name": "fee",
            "status": "present",
            "unit": "USD",
            "numeraire": "USD_2024_LOCAL",
            "kind": "observed",
        },
        {"name": "wage", "status": "present", "unit": "USD", "method": "made_up@1"},
        {
            "name": "spend",
            "status": "present",
            "unit": "USD/year",
            "numeraire": "USD_2023_2024_NOMINAL",
        },
    ]
    problems = intake_data.check_fields("r", fields, {"cpi_u_period_conversion@1"}, set(), 2023)
    assert any("rent" in p and "计价口径" in p for p in problems)
    assert any("fee" in p and "非 2024 年" in p for p in problems)
    assert any("made_up" in p for p in problems)
    assert not any("spend" in p for p in problems)


def test_small_helpers_by_hand():
    cpi = Cpi({"2024-annual": Decimal("313.689"), "2023-07": Decimal("305.691")})
    assert cpi.factor(["2023-07"]) == Fraction(313689, 305691)
    assert to_cents_text(cpi.factor(["2023-07"]) * 100) == "102.62"
    assert _session_hours("Mondays from 5pm to 7:30pm") == Fraction(5, 2)
    assert _session_hours("from 9am to 10am, then from 1pm to 2pm") is None
    assert _text("Fun &amp; Games ") == "Fun & Games"
