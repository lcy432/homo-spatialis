"""真实小样本（芝加哥都会区，``tests/fixtures/intake``）上的完整构建：来源 → 标准化 → A/B → 机会 → 查询 / 菜单 → 检查 → 冻结。

样本是真实文件的子集，只检验流程；这里的数字不代表现实分布。
"""

from __future__ import annotations

import gzip
import json
import shutil
from decimal import Decimal
from fractions import Fraction

import pytest

from common.intake_contracts import Menu, Opportunity, StdRecord
from ledger import intake_data
from product.realdata import build, query, store
from product.realdata.activities import PRIVATE_ACCESS, _session_hours, _text
from product.realdata.prices import Cpi, to_cents_text


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


def test_build_passes_checks_and_names_every_source_it_could_not_get(built):
    _, refs, *_ = built
    assert refs["check"].summary["problems"] == 0
    not_ok = refs["fetch"].summary["not_ok"]
    assert set(not_ok) == {"hud_fmr", "pums_h:18", "pums_p:18"}
    assert "blocked 202" in not_ok["hud_fmr"]  # 真实获取时被拒：离线构建照样写出原因
    assert refs["standardize"].summary["skipped"]["pums:18"]  # 跨州都会区缺一个州：报告，不补
    counts = refs["standardize"].summary["records"]
    assert all(
        counts[s] > 0
        for s in ("housing", "jobs", "food", "goods", "activities", "training", "background")
    )


def test_records_keep_source_period_geography_raw_and_method(built):
    _, _, _, recs, _, _ = built
    for r in recs:
        assert r.geo.country == "US" and r.geo.scheme and r.source.source_id and r.source.locator
        assert r.period.start <= r.period.end
        for f in r.fields:
            if f.status != "present":
                assert f.reason, (r.record_id, f.name)  # 缺失、不可映射、受限都写原因
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
    assert (
        fee.raw and fee.kind == "estimated" and "cpi_u_period_conversion@1" in fee.method
    )  # 原始学年金额保留，换算有方法


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
        some_table = conn.execute("SELECT table_id FROM table_records LIMIT 1").fetchone()[0]
        assert some_table in tables  # 条件表 → 用到的记录也存了
    finally:
        conn.close()


def test_collected_planning_executable_and_scorable_are_separate(built):
    _, _, opps, _, _, _ = built
    by_kind = {}
    for o in opps:
        by_kind.setdefault(o.kind, []).append(o)
    listing = next(
        o for o in by_kind["rental_listing_candidate"] if o.data_status != "insufficient"
    )
    assert (
        listing.capabilities.collected,
        listing.capabilities.planning_reference,
        listing.capabilities.executable,
    ) == (False, True, False)
    assert (
        listing.compile.status == "needs_e1_extension"
        and "floor_area_unknown" in listing.compile.reasons
    )
    program = next(o for o in by_kind["activity_program"] if o.compile.status == "compilable")
    assert program.capabilities.collected and program.capabilities.executable
    good = by_kind["appliance_model"][0]
    assert (
        good.capabilities.collected
        and good.compile.status == "not_compilable"
        and not good.capabilities.executable
    )
    assert all(not o.capabilities.scorable for o in opps)  # 本轮不宣称可评分
    assert all(o.compile.status == "not_compilable" for o in by_kind["training_program"])


def test_insufficient_housing_cells_stay_unpriced(built):
    _, _, opps, _, _, _ = built
    b = [o for o in opps if o.kind == "rental_listing_candidate"]
    unpriced = [o for o in b if o.attributes["price_level_used"] is None]
    assert unpriced, "样本里应当有回退之后仍不足的格"
    for o in unpriced:
        assert o.fact("monthly_rent").status == "missing" and o.data_status == "insufficient"
        assert o.attributes["fallback_tried"] == "puma_bed,tier_bed,metro_bed,metro_bed_stock"


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
    assert plan.fact("monthly_food_cost").kind == "assumption"
    assert plan.compile.status == "not_compilable"  # 没有做饭时间与“每周新鲜餐数”：不编造
    assert (
        plan.attributes["preparation"] == "home_cooked"
        and plan.attributes["channel"] == "grocery_retail"
    )


def test_restaurant_channel_is_unknown_unless_tagged(built):
    _, _, opps, _, _, _ = built
    rest = [o for o in opps if o.kind == "restaurant_venue"]
    assert rest
    values = {o.attributes.get("takeaway") for o in rest}
    assert "unknown" in values  # 没有标签就是未知，不当成“不外带”


def test_activity_text_is_unescaped_and_private_venues_are_not_listed(built):
    _, _, _, recs, _, _ = built
    acts = [r for r in recs if r.sector == "activities"]
    assert not any("&amp;" in (r.label + json.dumps(r.attributes)) for r in acts)
    assert not any(
        r.attributes.get("access") in PRIVATE_ACCESS
        for r in acts
        if r.record_type == "activity_venue"
    )


def test_goods_and_food_menus_are_full_catalogs_and_paging_reaches_every_item(built):
    _, _, opps, _, menus, db = built
    goods = [o for o in opps if o.sector == "goods" and o.supply_status != "not_offered"]
    gm = [m for m in menus if m.sector == "goods"]
    assert gm and all(m.selection == "all_eligible" and m.n_eligible == len(goods) for m in gm)
    conn = store.open_readonly(db)
    try:
        seen = []
        page = 1
        while True:
            p = query.menu_items(conn, gm[0].model_dump(mode="json"), page=page, per_page=7)
            seen += [o["opportunity_id"] for o in p.items]
            if page * 7 >= p.total:
                break
            page += 1
        assert sorted(seen) == sorted(
            o.opportunity_id for o in goods
        )  # 分页能拿到每一项，没有被抽样删掉
    finally:
        conn.close()


def test_menus_are_unique_per_environment_and_independent_of_clones(built):
    _, _, _, _, menus, _ = built
    keys = [(m.cbsa, m.year, m.sector, m.env_repeat) for m in menus]
    assert len(keys) == len(set(keys))
    h = {(m.year, m.env_repeat): m for m in menus if m.sector == "housing"}
    assert h[(1, 0)].seed != h[(1, 1)].seed and h[(1, 0)].seed != h[(2, 0)].seed
    assert all(m.selection == "listed" and m.selected for m in h.values())


def test_rerun_reuses_every_stage_and_gives_the_same_build(built):
    ctx, refs, *_ = built
    again = build.run_all(ctx)
    assert all(r.summary["reused"] for r in again.values())
    assert again["freeze"].identity == refs["freeze"].identity


def test_query_filters_page_and_lineage(built):
    *_, db = built
    conn = store.open_readonly(db)
    try:
        p1 = query.opportunities(conn, sector="jobs", cbsa="16980", per_page=5)
        p2 = query.opportunities(conn, sector="jobs", cbsa="16980", per_page=5, page=2)
        assert p1.total > 10 and len(p1.items) == 5
        assert {o["opportunity_id"] for o in p1.items}.isdisjoint(
            o["opportunity_id"] for o in p2.items
        )
        major = p1.items[0]["attributes"]["soc_major"]
        filtered = query.opportunities(
            conn, sector="jobs", attributes={"soc_major": major}, per_page=500
        )
        assert filtered.total > 0 and all(
            o["attributes"]["soc_major"] == major for o in filtered.items
        )
        lin = query.lineage(conn, "food.plan.16980.staples_home")
        assert lin["records"] and all(r["source"]["source_id"] == "bls_ap" for r in lin["records"])
    finally:
        conn.close()


def test_e1_export_contains_only_compilable_items_and_all_validate(built):
    ctx, refs, opps, *_ = built
    exp = json.loads((ctx.work / refs["check"].path / "e1_export.json").read_text(encoding="utf-8"))
    compilable = [o for o in opps if o.compile.status == "compilable"]
    assert exp["summary"]["compiled"] == len(compilable) > 0 and exp["summary"]["failed"] == 0
    sample = exp["sample"][0]
    assert sample["category"] == "experience"
    assert sample["price"]["amount"]["numeraire"] == "USD_2024_LOCAL"


def test_ledger_catches_a_menu_that_does_not_match_its_eligible_set(built, tmp_path):
    ctx, refs, *_ = built
    catalog = tmp_path / "catalog"
    shutil.copytree(ctx.work / refs["catalog"].path, catalog)
    docs = _jsonl(catalog / "menus.jsonl.gz")
    target = next(d for d in docs if d["sector"] == "housing" and d["selected"])
    target["selected"] = [*target["selected"][:-1], "goods.energystar.not-a-housing-item"]
    with gzip.open(catalog / "menus.jsonl.gz", "wt", encoding="utf-8") as f:
        for d in docs:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    problems = intake_data.check_build(
        ctx.work / refs["standardize"].path,
        ctx.work / refs["map"].path,
        catalog,
        source_ids={s.source_id for s in ctx.config.sources},
        metro_cbsas=[m.cbsa for m in ctx.config.metros],
        methods={m.ref for m in build.all_methods()},
        assumptions={a.assumption_id for a in build.all_assumptions()},
    )
    assert any("注册表里没有的机会" in p for p in problems)


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
    assert not any(
        "spend" in p for p in problems
    )  # 原始时期的名义金额可以保留，只是不能当成 2024 年当地价


def test_small_helpers_by_hand():
    cpi = Cpi({"2024-annual": Decimal("313.689"), "2023-07": Decimal("305.691")})
    assert cpi.factor(["2023-07"]) == Fraction(313689, 305691)  # 精确分数，不经浮点
    assert to_cents_text(cpi.factor(["2023-07"]) * 100) == "102.62"
    assert _session_hours("Mondays from 5pm to 7:30pm") == Fraction(5, 2)
    assert _session_hours("from 9am to 10am, then from 1pm to 2pm") is None  # 两段时段：不推算
    assert _text("Fun &amp; Games ") == "Fun & Games"
