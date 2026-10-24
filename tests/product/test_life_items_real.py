"""E3B 物品与活动一侧（DECISIONS/0041、0042）：从真实切片的小样本编译出的机会都过契约、金额精确到分、数能手算核对；未知不当零，
E1 表达不了的真实活动不会被当成可执行。只读 ``tests/fixtures/life_items/``，不读原始缓存、不联网。"""

from __future__ import annotations

import contextlib
import json
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from common.life_contracts import LifeOpportunity, exact_minor_units
from ledger.life_catalog import load_life_bundle
from product.life_items import LifeItemCandidate, carried_restrictions, verdict
from product.realdata.life_freeze import freeze
from product.realdata.life_items import (
    STATUSES,
    BasketWeek,
    Ctx,
    away_meal_cost,
    complete_week_cost,
    cuisine_evidence,
    failure_spec,
    item_grams,
    representative_model,
    session_dates,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "tests/fixtures/life_items/slice_sample.json"


def _yaml(rel: str) -> dict:
    return yaml.safe_load((REPO / rel).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cfgs():
    items = _yaml("configs/intake/life_items_us_v1.yaml")
    occ = _yaml("configs/intake/occupations_us_v1.yaml")
    baskets = _yaml("configs/intake/us_v1.yaml")["mapping"]["a_food"]["baskets"]
    return items, occ, baskets


@pytest.fixture(scope="module")
def sl():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def frozen(tmp_path_factory, sl, cfgs):
    out = tmp_path_factory.mktemp("items")
    manifest = freeze(sl, *cfgs, out)
    return out, manifest


def city(frozen, cbsa: str) -> dict:
    return json.loads((frozen[0] / f"{cbsa}.json").read_text(encoding="utf-8"))


def items_by_id(frozen, cbsa: str) -> dict[str, dict]:
    out = {i["opportunity"]["opportunity_id"]: i for i in city(frozen, cbsa)["items"]}
    p = frozen[0] / f"{cbsa}.programs.json"
    if p.exists():
        out |= {
            i["opportunity"]["opportunity_id"]: i
            for i in json.loads(p.read_text(encoding="utf-8"))["items"]
        }
    return out


def amount(item: dict) -> float:
    o = item["opportunity"]
    return (o["price"] or o["compensation"])["amount"]["amount"]


def test_every_item_passes_the_contract_with_exact_cents_and_a_known_status(frozen):
    n = 0
    for cbsa in ("35620", "16980", "41860"):
        for item in items_by_id(frozen, cbsa).values():
            o = LifeOpportunity.model_validate(item["opportunity"])
            assert item["status"] in STATUSES
            for q in (o.price, o.compensation):
                if q is not None:
                    assert q.amount.numeraire == "USD_2024_LOCAL"
                    assert exact_minor_units(q.amount.amount, 2) is not None
            if o.availability != "available":
                assert o.price is None and o.compensation is None
            n += 1
    assert n > 150


def test_energy_completion_hand_example():
    # 篮子 10 美元 / 7,000 千卡，补齐篮子 20 美元 / 10,000 千卡：差 7,000 千卡 × 0.002 = 14 → 一周 24
    b = BasketWeek(Fraction(10), Fraction(7000), ())
    c = BasketWeek(Fraction(20), Fraction(10000), ())
    assert complete_week_cost(b, c, Fraction(14000)) == 24
    # 篮子自己已经够：不补、也不缩
    assert (
        complete_week_cost(BasketWeek(Fraction(10), Fraction(20000), ()), c, Fraction(14000)) == 10
    )


def test_home_plans_follow_the_basket_and_its_energy_share(frozen):
    """补齐后的主食方案 = intake 篮子月费 ÷ 篮子热量占参照量的比例；荤素搭配 = 自己的篮子 + (1 − 比例) × 补齐后的主食方案。"""
    items, check = items_by_id(frozen, "35620"), city(frozen, "35620")["food_check"]["baskets"]
    staples, varied = items["nyc.food.staples.h21"], items["nyc.food.varied.h21"]
    s_share = Fraction(str(staples["opportunity"]["attributes"]["basket_energy_share"]))
    v_share = Fraction(str(varied["opportunity"]["attributes"]["basket_energy_share"]))
    assert check["staples_home"]["recomputed"] == float(check["staples_home"]["intake"]) == 76.83
    assert abs(amount(staples) - float(Fraction("76.83") / s_share)) < 0.02
    assert (
        abs(
            amount(varied)
            - float(Fraction("161.20") + (1 - v_share) * Fraction(str(amount(staples))))
        )
        < 0.02
    )
    fp = staples["opportunity"]["food_plan"]
    assert fp["covers_basic_month"] and fp["meals_per_week"] == 21
    # 每顿 20 分钟 × 21 顿 = 7 小时 + 买菜 2 小时 = 每周 9 小时 × 52 ÷ 12 = 39
    assert (
        fp["prep_hours_per_month"] == 39.0 and staples["opportunity"]["resource"]["hours"] == 39.0
    )
    assert fp["parts"][0]["cuisine"] == "home_style" and fp["parts"][0]["fresh_meals_per_week"] == 4


def test_item_grams_come_from_the_unit_text_or_sr_portions(sl, cfgs):
    energy, fdc = cfgs[0]["food"]["item_energy"], sl["fdc"]
    items = sl["metros"]["35620"]["food"]["items"]
    assert item_grams("701312", items["701312"]["unit_text"], energy["701312"], fdc) == Fraction(
        "453.6"
    )
    assert item_grams("708111", items["708111"]["unit_text"], energy["708111"], fdc) == 50 * 12
    assert item_grams("709112", items["709112"]["unit_text"], energy["709112"], fdc) == 976 * 4
    assert energy["717311"]["zero_energy"] is True


def _fake_ctx(cfgs, metro: dict, cpi=(1, 1)) -> Ctx:
    sl = {"cpi": {"ce_2023_2024_to_2024": {"numerator": cpi[0], "denominator": cpi[1]}}}
    return Ctx("99999", "tst", "测试都会区", "测试城", cfgs[0], metro, sl)


def test_away_cost_proxy_hand_example(cfgs):
    """外食一顿 = 篮子补齐后每周花费 ÷ 21 + 厨房 0.25 小时 × 厨师时薪 × 2 + 场地 2 × 其他服务 RPP ÷ 100；外送再加 0.2 小时 × 司机时薪 × 2。"""
    hourly = {"wage_basis": "annual_and_hourly"}
    ctx = _fake_ctx(
        cfgs,
        {
            "wages": {
                "35-2014": {**hourly, "annual_wage_median": "41600"},  # 时薪 20
                "53-3033": {**hourly, "annual_wage_median": "52000"},  # 时薪 25
            },
            "rpp": {"rpp_other": "110"},
        },
    )
    a = away_meal_cost(ctx, Fraction(42))
    assert a is not None
    # 食材 42 ÷ 21 = 2；厨房 0.25 × 20 × 2 = 10；场地 2 × 1.10 = 2.2；外送 0.2 × 25 × 2 = 10
    assert (a["ingredients"], a["kitchen"], a["venue"], a["delivery_extra"]) == (
        2,
        10,
        Fraction(22, 10),
        10,
    )
    assert (a["dine"], a["delivered"]) == (Fraction(142, 10), Fraction(242, 10))
    # 缺一项资料就没有代理（不当 0）：没有配送司机的工资
    del ctx.metro["wages"]["53-3033"]
    assert away_meal_cost(ctx, Fraction(42)) is None


def test_cuisine_evidence_counts_each_restaurant_once_and_keeps_every_cuisine(cfgs):
    sets = [
        {"tags": ["japanese", "sushi"], "count": 12},  # 一家写了两个日料标签：只算一家
        {"tags": ["sushi"], "count": 8},
        {"tags": ["thai"], "count": 19},  # 不够 20 家：照样提供，只是按共同可得假设标明
        {"tags": ["chinese"], "count": 40},
    ]
    ctx = _fake_ctx(cfgs, {"restaurants": {"tag_sets": sets}})
    got = cuisine_evidence(ctx)
    assert got["japanese"] == 20 and got["thai"] == 19 and got["chinese"] == 40
    assert got["korean"] == 0 and len(got) == 9  # 完整词表，不截取前几名


def test_the_food_grid_has_every_combination_and_merges_the_degenerate_ones(frozen):
    """3 个篮子 × (家里做 21 顿 1 个 + 家里做 0 / 3 / 7 / 14 顿 × 3 种外面的方式 × 9 个菜系) = 3 × 109 = 327。"""
    foods = [k for k in items_by_id(frozen, "35620") if k.startswith("nyc.food.")]
    assert len(foods) == 327
    for b in ("staples", "varied", "fresh"):
        assert [k for k in foods if k.startswith(f"nyc.food.{b}.h21")] == [f"nyc.food.{b}.h21"]
        assert len([k for k in foods if k.startswith(f"nyc.food.{b}.h00.")]) == 27


def test_grid_plans_split_home_cooking_dining_and_delivery_time(frozen):
    items = items_by_id(frozen, "35620")
    dine = items["nyc.food.varied.h14.dine_out.italian"]
    fp = dine["opportunity"]["food_plan"]
    # 外食 7 顿 × 1.25 小时 × 52 ÷ 12 = 37.92；家里 14 顿 × 25 分钟 + 买菜 2 小时 = 每周 7.83… × 52 ÷ 12 = 33.94
    assert fp["dining_hours_per_month"] == 37.92 and fp["prep_hours_per_month"] == 33.94
    assert dine["opportunity"]["resource"]["hours"] == 71.86
    assert [p["mode"] for p in fp["parts"]] == ["home_cooked", "dine_out"]
    assert sum(p["meals_per_week"] for p in fp["parts"]) == 21
    # 外送 7 顿：每顿 0.25 小时算进准备时间：33.94… + 7 × 0.25 × 52 ÷ 12 = 41.53
    deliv = items["nyc.food.varied.h14.delivery.italian"]["opportunity"]
    assert deliv["food_plan"]["prep_hours_per_month"] == 41.53
    assert deliv["food_plan"]["dining_hours_per_month"] == 0
    assert deliv["attributes"]["taxes_fees_tips"] == "not_itemized"
    assert "food_away_cost_proxy_v1" in items["nyc.food.varied.h14.delivery.italian"]["assumptions"]
    # 各半：家里 7、外食 7、外送 7
    half = items["nyc.food.varied.h07.half.thai"]["opportunity"]["food_plan"]
    assert [(p["mode"], p["meals_per_week"]) for p in half["parts"]] == [
        ("home_cooked", 7),
        ("dine_out", 7),
        ("delivery", 7),
    ]


def test_away_meals_are_priced_by_the_proxy_and_fresh_only_by_the_basket_share(frozen):
    items = items_by_id(frozen, "35620")
    o = items["nyc.food.staples.h00.dine_out.chinese"]["opportunity"]
    (part,) = o["food_plan"]["parts"]
    # 主食为主的篮子 21 餐里 4 餐新鲜：外食 21 顿也只有 4 顿算新鲜（不自动全算新鲜）
    assert part["mode"] == "dine_out" and part["fresh_meals_per_week"] == 4
    # 月费 = 21 顿 × 每顿堂食代理 × 52 ÷ 12（分项在属性里，四舍五入后差不过 1 分 × 21 × 52 ÷ 12）
    per_meal = o["attributes"]["away_meal_dine_cost"]
    assert abs(o["price"]["amount"]["amount"] - per_meal * 21 * 52 / 12) < 0.5
    fresh = items["nyc.food.fresh.h07.dine_out.chinese"]["opportunity"]["food_plan"]["parts"]
    assert [p["fresh_meals_per_week"] for p in fresh] == [5.67, 11.33]  # 17/21 × 7、17/21 × 14


def test_chore_service_is_wage_times_hours_times_overhead(frozen, sl):
    item = items_by_id(frozen, "35620")["nyc.service.laundry_pickup"]
    wage = Fraction(sl["metros"]["35620"]["wages"]["51-6011"]["annual_wage_median"])
    assert amount(item) == round(float(wage / 2080 * 3 * 2), 2)
    r = item["opportunity"]["resource"]
    assert (
        r["substitutes"] == [{"task": "laundry", "residual_hours_per_month": 2.0}]
        and r["hours"] == 0
    )


def test_massage_is_counted_by_sessions_received(frozen):
    o = items_by_id(frozen, "35620")["nyc.service.massage_studio_2"]["opportunity"]
    assert o["enjoyment"][0]["function"] == "personal_care"
    assert o["enjoyment"][0]["units_per_month"] == 2 and o["resource"]["hours"] == 3.0


def test_device_uses_the_median_energy_model_and_a_category_price(frozen, sl):
    item = items_by_id(frozen, "35620")["nyc.durable.washer_top"]
    rep = representative_model(sl["appliances"], "clothes_washer", "Top Load")
    assert rep is not None and item["records"] == [rep["record_id"]]
    assert item["opportunity"]["spec"]["clothes_washer_model"] == rep["model_number"]
    # 全国类别设定 700 × 商品类空间换算系数 1.107405 = 775.1835 → 775.18
    assert amount(item) == 775.18
    assert item["opportunity"]["source"]["kind"] == "synthetic"
    assert "installation_condition_not_in_housing_data" in item["gaps"]
    assert item["opportunity"]["failure"]["kind"] == "none_within_horizon"
    guitar = items_by_id(frozen, "35620")["nyc.durable.acoustic_guitar"]["opportunity"]
    assert guitar["failure"]["kind"] == "none_within_horizon"
    failure = frozen[1]["device_failure"]
    assert failure["clothes_washer"]["rule"] == "none_within_horizon"
    assert "washer_top" in failure["clothes_washer"]["offers"]


def test_model_activities_need_a_venue_and_unknown_tickets_stay_unknown(frozen):
    ny, sf = items_by_id(frozen, "35620"), items_by_id(frozen, "41860")
    assert "nyc.experience.lap_swim" in ny and "sf.experience.lap_swim" not in sf
    assert any(
        e["key"] == "lap_swim" and e["kind"] == "not_offered"
        for e in city(frozen, "41860")["not_generated"]
    )
    museum = ny["nyc.experience.museum_visit"]
    assert museum["status"] == "data_unknown"
    assert (
        museum["opportunity"]["availability"] == "unknown"
        and museum["opportunity"]["price"] is None
    )
    practice = ny["nyc.experience.guitar_practice"]["opportunity"]
    assert practice["price"]["amount"]["amount"] == 0 and practice["activity"]["requires"] == [
        "guitar_playable"
    ]
    # 自主参与路径：无器械锻炼，不收费、不要装备、不要场所；时长只有方案规定的 1 小时一个版本
    workout = ny["nyc.experience.bodyweight_workout"]["opportunity"]
    assert workout["price"]["amount"]["amount"] == 0 and workout["activity"]["requires"] == []
    assert workout["activity"]["primary_category"] == "fitness"
    assert workout["activity"]["participation_hours"] == 1.0


def test_paid_activities_dominated_by_free_ones_are_reported_not_rewarded(frozen):
    rows = city(frozen, "35620")["activity_dominance"]
    pairs = {(r["paid"], r["dominated_by"]) for r in rows}
    # 团体课 1 + 0.75 小时 vs 无器械锻炼 1 + 0.25 小时：同类别、不要更多装备、每参与一小时花的时间更少、免费
    assert ("nyc.experience.group_fitness", "nyc.experience.bodyweight_workout") in pairs
    assert ("nyc.experience.guitar_lesson", "nyc.experience.guitar_practice") in pairs
    # 网球两种都付费：没有免费方案，不报
    assert not any("tennis" in r["paid"] for r in rows)


def test_chicago_programs_keep_real_facts_and_are_never_executable(frozen):
    items = items_by_id(frozen, "16980")
    programs = {k: v for k, v in items.items() if ".cpd." in k}
    assert programs and all(v["status"] == "needs_e1_extension" for v in programs.values())
    tennis = programs["chi.experience.cpd.558965"]["opportunity"]
    # 2026-04-09 至 10-29 每周四 30 次，去掉 3 个例外日期（名单里重复写的同一天只算一次）= 27 次；免费
    assert tennis["attributes"]["sessions_per_registration"] == 27
    assert tennis["price"]["amount"]["amount"] == 0.0
    yoga = programs["chi.experience.cpd.584854"]["opportunity"]
    # 2026-09-08 至 12-08 每周二 14 次：报名费 13.96 ÷ 14 = 0.997… → 1.00
    assert (
        yoga["attributes"]["sessions_per_registration"] == 14
        and yoga["price"]["amount"]["amount"] == 1.0
    )
    for v in programs.values():
        assert {"calendar_window", "registration_bundle"} <= set(v["gaps"])
        assert carried_restrictions(v["opportunity"])  # E3A 的编译检查会因日期与年龄拒绝它
    senior = programs["chi.experience.cpd.584269"]
    assert "age_eligibility" in senior["gaps"]
    kinds = {(e["kind"], e["key"]) for e in city(frozen, "16980")["not_generated"]}
    assert {
        ("not_applicable", "chicago_age"),
        ("unsupported", "chicago_category"),
        ("not_applicable", "chicago_type"),
    } <= kinds
    assert ("gap", "chicago_session_hours") in kinds


def test_session_dates_count_only_what_the_notes_say():
    base = "From September 8, 2026 to September 29, 2026 Each Tuesday from 6pm to 7pm"
    assert len(session_dates(base)) == 4
    assert (
        len(
            session_dates(
                base + " Except the following dates: Tuesday, Sep 15, 2026 Tuesday, Sep 15, 2026"
            )
        )
        == 3
    )
    two = "From September 7, 2026 to September 13, 2026 Each Monday,Wednesday from 6pm to 7pm"
    assert len(session_dates(two)) == 2
    assert (
        session_dates(base + " Except the following dates: Tuesday, Sep 15, 2026 Tues") is None
    )  # 名单被截断
    assert session_dates("On October 31, 2026 from 6pm to 9pm") is None
    assert session_dates(None) is None


def test_freeze_is_deterministic_and_the_identity_follows_content(tmp_path, sl, cfgs, frozen):
    again = freeze(sl, *cfgs, tmp_path)
    assert again["content_identity"] == frozen[1]["content_identity"]
    changed = json.loads(json.dumps(sl))
    changed["metros"]["35620"]["wages"]["51-6011"]["annual_wage_median"] = "40000.00"
    other = freeze(changed, *cfgs)
    assert (
        other["cities"]["35620"]["content_identity"]
        != frozen[1]["cities"]["35620"]["content_identity"]
    )
    assert (
        other["cities"]["16980"]["content_identity"]
        == frozen[1]["cities"]["16980"]["content_identity"]
    )


def test_e1_compile_check_accepts_real_items_placed_in_a_synthetic_city(frozen):
    """把真实机会放进合成城 K51 的目录副本，跑 E3A 的编译检查（E1 契约、目录、跨对象与可结算检查）。生活输入只多声明清单里列出的
    属性键：食品、活动、装备与享受服务都能执行；比 K51 最低方案便宜的真实方案与最低方案声明矛盾（所以整合时最低方案要用本城最便宜
    的那份，清单写在 ``minimum_food_candidate``）；芝加哥活动因日期与年龄被拒；合成词表里没有的菜系报“本版未支持”。"""
    with contextlib.chdir(REPO):
        bundle, _ = load_life_bundle(
            Path("configs/life/synthetic_product.yaml"), require_settlement_ready=True
        )
    keys = frozen[1]["vocabulary"]["attribute_keys"]
    bundle = bundle.model_copy(
        update={"attribute_keys": tuple(dict.fromkeys((*bundle.attribute_keys, *keys)))}
    )
    items = items_by_id(frozen, "35620") | items_by_id(frozen, "16980")

    def check(oid: str, kind: str):
        return verdict(
            bundle, "US:SYNTH2026:K51", LifeItemCandidate(oid, kind, oid, items[oid]["opportunity"])
        )

    assert check("nyc.food.varied.h14.dine_out.italian", "food_plan").status == "executable"
    assert check("nyc.experience.group_fitness", "activity").status == "executable"
    assert check("nyc.experience.tennis_rental", "activity").status == "executable"
    assert check("nyc.durable.acoustic_guitar", "equipment").status == "executable"
    assert check("nyc.service.massage_studio_1", "enjoyment").status == "executable"
    cheap = check("nyc.food.staples.h21", "food_plan")
    assert cheap.status == "rejected" and any("最低食品方案" in r for r in cheap.reasons_zh)
    assert (
        city(frozen, "35620")["minimum_food_candidate"]["opportunity_id"] == "nyc.food.staples.h21"
    )
    assert check("chi.experience.cpd.584854", "activity").status == "rejected"
    assert check("nyc.food.varied.h14.dine_out.chinese", "food_plan").status == "unsupported"


def test_failure_spec_combines_parts_of_a_set(cfgs):
    ctx = _fake_ctx(cfgs, {})
    none = {"rule": "none_within_horizon", "annual_hazard": None, "basis": "甲"}
    assert failure_spec(ctx, [none, none]).kind == "none_within_horizon"
    # 洗衣机每年 0.1、烘干机每年 0.2，部件独立、任一坏就整套不能用：1 − 0.9 × 0.8 = 0.28
    f = failure_spec(
        ctx,
        [
            {"rule": "annual_hazard", "annual_hazard": "0.1", "basis": "乙"},
            {"rule": "annual_hazard", "annual_hazard": "0.2", "basis": "丙"},
        ],
    )
    assert (f.kind, f.annual_probability) == ("annual_hazard", 0.28)


def test_city_files_carry_the_shared_city_identity_and_id_prefix(frozen):
    for cbsa, key in (("35620", "nyc"), ("16980", "chi"), ("41860", "sf")):
        body = city(frozen, cbsa)
        assert body["key"] == key and body["city"] == {
            "country": "US",
            "scheme": "CBSA2023",
            "code": cbsa,
        }
        assert all(oid.startswith(f"{key}.") for oid in items_by_id(frozen, cbsa))


def test_each_city_keeps_the_common_a_structure_and_no_price_was_updated(frozen):
    """每城每个 A 板块都有方案族、参数（含义、单位、支持范围、先验、来源）、城市证据与状态；现在没有合格的同规格直接报价，所以
    全部“未发生本地报价更新”（芝加哥公园管理局的整期价格本身就是直接报价，原样保留）。"""
    for cbsa in ("35620", "16980", "41860"):
        blocks = {b["block"]: b for b in city(frozen, cbsa)["a_blocks"]}
        assert set(blocks) == {"food", "services", "devices", "equipment", "activities"}
        for b in blocks.values():
            assert b["parameters"] and all(
                {"meaning_zh", "unit", "support", "prior", "source", "city_updatable"} <= set(p)
                for p in b["parameters"]
            )
            assert "市场份额" in b["coverage_note_zh"]
            for f in b["families"]:
                rc = f["retail_correction"]
                assert not rc["by_status"].get("updated")
                if f["family"] != "cpd_program_session":
                    assert rc["note_zh"] == "未发生本地报价更新" and rc["comparable_quotes"] == 0
        food = blocks["food"]
        assert food["families"][0]["opportunities"] == 327
        assert set(food["city_evidence"]["osm_restaurants_by_cuisine"]) == {
            "chinese",
            "italian",
            "mexican",
            "japanese",
            "indian",
            "thai",
            "korean",
            "vietnamese",
            "mediterranean",
        }
