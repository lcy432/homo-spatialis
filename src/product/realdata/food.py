"""食品：BLS 平均价格（全国与大区的食材均价）、OpenStreetMap 餐馆（菜系、制作方式与获得渠道分开）、A 映射的食材方案。

* **食材均价**（BLS CPI Average Price Data）：只有全国与四个人口普查大区的食品均价；都会区只有能源项目，没有食品。记录按原样保存
  全国与大区的 2024 年月度均价的年平均（12 个月都有才算；派生）。给某个都会区用的价格是**估计**：全国均价 × 本都会区商品类 RPP ÷
  全国商品类 RPP（``rpp_goods_spatial_adjustment@1``，BEA 2024 年）。RPP 是空间换算的依据，不当付款单价；大区的观测值另列，供对照。
* **餐馆**（OSM，城市行政边界之内，不是整个都会区）：名称、菜系、类别（餐馆 / 快餐 / 咖啡）、开放时间、品牌。**制作方式**（餐馆
  制作）与**获得渠道**（堂食、自取、配送）分开记：OSM 写了 ``takeaway`` / ``delivery`` 才算知道，没写就是未知，不当成“不提供”。
  OSM 不记价格：报价一律缺失。不凭名称或菜系给“口味复杂程度”编数字。
* **食材方案**（A 映射 ``food_plan_from_regional_prices@1``）：用配置里登记的每周食材篮子（数量是假设，不是营养方案）乘本都会区的
  食材估计价，得到每月食材费用。准备时间没有可用的公开来源（本轮没有取得 ATUS 表），记为缺失，所以方案不能直接编进当前引擎。
"""

from __future__ import annotations

import datetime as _dt
import io
import json
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from decimal import Decimal
from fractions import Fraction

import pandas as pd

from common.intake_contracts import (
    AssumptionSpec,
    CompileInfo,
    FieldValue,
    GeoRef,
    MethodSpec,
    Period,
    SourceRef,
    StdRecord,
    missing,
)
from product.realdata.config import Metro

PERIOD_2024 = Period(
    label="2024 calendar year", start=_dt.date(2024, 1, 1), end=_dt.date(2024, 12, 31)
)
AP_AREAS = {
    "0000": ("national", "U.S. city average"),
    "0100": ("census_region", "Northeast"),
    "0200": ("census_region", "Midwest"),
    "0300": ("census_region", "South"),
    "0400": ("census_region", "West"),
}
AP_ANNUAL = MethodSpec(
    method_id="ap_annual_mean",
    version="1",
    family="conversion",
    summary_zh="BLS 平均价格 2024 年 1–12 月月度值的算术平均；缺任何一个月就不算年平均。",
    inputs=("bls_ap",),
)
RPP_METHOD = MethodSpec(
    method_id="rpp_goods_spatial_adjustment",
    version="1",
    family="conversion",
    summary_zh="都会区估计价 = 全国均价 × 本都会区商品类 RPP ÷ 全国商品类 RPP（BEA 2024 年）。RPP 只作空间换算依据，不是付款单价。",
    inputs=("bls_ap", "bea_rpp_msa"),
    known_biases_zh=(
        "商品类 RPP 涵盖全部商品，食品不单独发布：食品的地区差异可能与全部商品不同",
        "BEA 的都会区划定与 CPI 均价的地理不完全相同",
    ),
)
FOOD_PLAN = MethodSpec(
    method_id="food_plan_from_regional_prices",
    version="1",
    family="mapping_a",
    summary_zh="配置登记的每周食材篮子 × 本都会区食材估计价 × 52 ÷ 12 = 每月食材费用；篮子覆盖不同的做饭方式，数量是假设。",
    inputs=("rpp_goods_spatial_adjustment@1",),
    known_biases_zh=("篮子数量是登记的设定，不是营养或膳食方案；只覆盖 BLS 有均价的食材",),
)
BASKET = AssumptionSpec(
    assumption_id="food_basket_quantities_v1",
    kind="assumption",
    statement_zh="每个食材方案的每周食材数量见配置 mapping.a_food.baskets；它们是覆盖不同做饭方式的设定，不是营养方案或真实家庭消费。",
    applies_to=("food_plan_from_regional_prices@1",),
    why_not_measured_zh="没有取得 USDA 节俭食品计划的逐项市场篮子；BLS 只发布单价，不发布家庭的购买数量。",
)


# --------------------------------------------------------------------------- BLS 平均价格


def parse_ap(series: bytes, data: bytes) -> dict[tuple[str, str], dict]:
    """→ (地区, 食品项目) → {名称, 2024 年各月值}。只保留食品（项目代码不以 72、74 开头：那是能源与汽油）。"""
    meta = {}
    for ln in series.decode("utf-8", "replace").splitlines()[1:]:
        p = [x.strip() for x in ln.split("\t")]
        if len(p) >= 4 and p[1] in AP_AREAS and not p[2].startswith(("72", "74")):
            meta[p[0]] = (p[1], p[2], p[3])
    out: dict[tuple[str, str], dict] = {}
    for ln in data.decode("utf-8", "replace").splitlines()[1:]:
        p = [x.strip() for x in ln.split("\t")]
        if (
            len(p) < 4
            or p[0] not in meta
            or p[1] != "2024"
            or not p[2].startswith("M")
            or p[2] == "M13"
        ):
            continue
        area, item, title = meta[p[0]]
        rec = out.setdefault((area, item), {"title": title, "series": p[0], "months": {}})
        rec["months"][p[2]] = p[3]
    return out


def _item_name(title: str) -> tuple[str, str]:
    """“Bread, white, pan, per lb. (453.6 gm) in U.S. city average, …” → (“Bread, white, pan”, “per lb. (453.6 gm)”)。"""
    head = title.split(" in ")[0]
    if ", per " in head:
        name, unit = head.split(", per ", 1)
        return name, "per " + unit
    if " per " in head:
        name, unit = head.split(" per ", 1)
        return name, "per " + unit
    return head, "per unit (see title)"


def ap_records(ap: dict[tuple[str, str], dict], raw_sha: str | None) -> Iterator[StdRecord]:
    for (area, item), rec in sorted(ap.items()):
        level, label = AP_AREAS[area]
        name, unit = _item_name(rec["title"])
        months = rec["months"]
        if len(months) == 12:
            mean = sum(Decimal(v) for v in months.values()) / 12
            price = FieldValue(
                name="avg_price_2024",
                status="present",
                raw=f"{len(months)} 个月：{min(months.values(), key=Decimal)}–{max(months.values(), key=Decimal)}",
                raw_unit=f"USD {unit}（月度均价）",
                value=str(mean.quantize(Decimal("0.001"))),
                unit=f"USD {unit}",
                numeraire="USD_2024_US_AVG" if area == "0000" else "USD_2024_CENSUS_REGION",
                kind="derived",
                method=AP_ANNUAL.ref,
            )
        else:
            price = missing(
                "avg_price_2024", "missing", f"2024 年只有 {len(months)} 个月的均价，不算年平均"
            )
        yield StdRecord(
            record_id=f"food.ap2024.{area}.{item.lower()}",
            sector="food",
            record_type="food_item_avg_price",
            label=f"{name}（{label}）",
            geo=GeoRef(
                country="US",
                scheme="CensusRegion" if level == "census_region" else "Nation",
                code=area,
                level=level,
                label=label,
                cbsa=None,
                relation="exact",
            ),
            period=PERIOD_2024,
            source=SourceRef(
                source_id="bls_ap",
                locator=f"ap.data.0.Current series={rec['series']} year=2024",
                raw_sha256=raw_sha,
            ),
            fields=(price,),
            attributes={
                "item_code": item,
                "item_name": name,
                "unit_text": unit,
                "preparation": "ingredient",
                "channel": "grocery_retail",
            },
        )


def parse_rpp(content: bytes) -> dict[str, dict[str, str]]:
    """BEA MARPP → GeoFIPS → {all, goods, housing, utilities, other}（2024 年，原样文字）。"""
    z = zipfile.ZipFile(io.BytesIO(content))
    name = next(n for n in z.namelist() if n.startswith("MARPP_MSA") and n.endswith(".csv"))
    df = pd.read_csv(z.open(name), dtype=str, encoding="latin-1")
    df["GeoFIPS"] = df["GeoFIPS"].str.strip().str.strip('"')
    keys = {"1": "all", "2": "goods", "3": "housing", "4": "utilities", "5": "other"}
    out: dict[str, dict[str, str]] = defaultdict(dict)
    for _, r in df.iterrows():
        lc = str(r["LineCode"]).strip()
        if lc in keys and isinstance(r.get("2024"), str):
            out[r["GeoFIPS"]][keys[lc]] = r["2024"].strip()
    return dict(out)


def goods_factor(rpp: dict[str, dict[str, str]], cbsa: str) -> Fraction | None:
    metro, us = rpp.get(cbsa, {}).get("goods"), rpp.get("00000", {}).get("goods")
    if not metro or not us:
        return None
    return Fraction(metro) / Fraction(us)


def item_opportunities(records: list[StdRecord], metro: Metro, factor: Fraction | None) -> list:
    """每个有全国 2024 年均价的食材 → 本都会区的一项食材（估计价 + 大区观测价对照）。"""
    from product.realdata.registry import make_opportunity

    by_item: dict[str, dict[str, StdRecord]] = defaultdict(dict)
    for r in records:
        by_item[str(r.attributes["item_code"])][r.geo.code] = r
    out = []
    for item, by_area in sorted(by_item.items()):
        us = by_area.get("0000")
        if us is None:
            continue
        us_price = us.field("avg_price_2024")
        region = by_area.get(metro.census_region)
        reg_price = region.field("avg_price_2024") if region else None
        if us_price is not None and us_price.status == "present" and factor is not None:
            est = FieldValue(
                name="price",
                status="present",
                raw=us_price.value,
                raw_unit=us_price.unit,
                value=str(
                    (
                        Decimal(us_price.value)
                        * Decimal(factor.numerator)
                        / Decimal(factor.denominator)
                    ).quantize(Decimal("0.001"))
                ),
                unit=us_price.unit,
                numeraire="USD_2024_LOCAL",
                kind="estimated",
                method=f"{RPP_METHOD.ref}（×{float(factor):.6f}）",
            )
        else:
            est = missing("price", "missing", "没有全国 2024 年均价或本都会区的 RPP：不估计")
        facts = [est]
        if reg_price is not None and reg_price.status == "present":
            facts.append(
                FieldValue(**{**reg_price.model_dump(), "name": "regional_observed_price"})
            )
        else:
            facts.append(
                missing("regional_observed_price", "missing", "本大区没有完整的 2024 年均价")
            )
        out.append(
            make_opportunity(
                opportunity_id=f"food.item.{metro.cbsa}.{item.lower()}",
                sector="food",
                kind="food_item",
                label=str(us.attributes["item_name"]),
                cbsa=metro.cbsa,
                geo=GeoRef(
                    country="US",
                    scheme="CBSA2023",
                    code=metro.cbsa,
                    level="cbsa",
                    label=metro.label,
                    cbsa=metro.cbsa,
                    relation="exact",
                ),
                origin="record",
                lineage=tuple(r.record_id for r in (us, region) if r is not None),
                facts=tuple(facts),
                required=("price",),
                supply_status="unknown",
                menu_policy="full_catalog",
                compile=CompileInfo(
                    status="needs_e1_extension",
                    reasons=("single_food_item_not_a_monthly_plan",),
                    e1_category="food",
                ),
                attributes={
                    "unit_text": us.attributes["unit_text"],
                    "preparation": "ingredient",
                    "channel": "grocery_retail",
                },
                terms={"sales_tax": "unknown"},
            )
        )
    return out


def plan_opportunities(items: list, metro: Metro, baskets: dict) -> list:
    """A 映射：配置登记的每周篮子 × 本都会区食材估计价 → 每月食材费用。"""
    from product.realdata.registry import make_opportunity

    price = {
        o.opportunity_id.rsplit(".", 1)[1]: o.fact("price") for o in items if o.kind == "food_item"
    }
    records_of = {
        o.opportunity_id.rsplit(".", 1)[1]: o.lineage for o in items if o.kind == "food_item"
    }
    out = []
    for plan_id, spec in baskets.items():
        total = Decimal(0)
        missing_items = []
        lineage = []
        for line in spec["items"]:
            code = str(line["item"]).lower()
            lineage += [r for r in records_of.get(code, ()) if r not in lineage]
            p = price.get(code)
            if p is None or p.status != "present":
                missing_items.append(code)
                continue
            total += Decimal(p.value) * Decimal(str(line["qty"]))
        if not lineage:
            raise ValueError(
                f"食品篮子 {plan_id} 引用的食材在 {metro.cbsa} 的资料里一条都没有：检查配置的食材编号"
            )
        monthly = total * Decimal(52) / Decimal(12)
        cost = (
            FieldValue(
                name="monthly_food_cost",
                status="present",
                value=str(monthly.quantize(Decimal("0.01"))),
                unit="USD/month",
                numeraire="USD_2024_LOCAL",
                kind="assumption",
                method=FOOD_PLAN.ref,
                assumptions=(BASKET.assumption_id,),
                raw=f"每周 {total.quantize(Decimal('0.01'))} USD × 52 ÷ 12",
            )
            if not missing_items
            else missing(
                "monthly_food_cost", "missing", f"篮子里 {missing_items} 没有本都会区的估计价"
            )
        )
        out.append(
            make_opportunity(
                opportunity_id=f"food.plan.{metro.cbsa}.{plan_id}",
                sector="food",
                kind="food_plan",
                label=str(spec["label"]),
                cbsa=metro.cbsa,
                geo=GeoRef(
                    country="US",
                    scheme="CBSA2023",
                    code=metro.cbsa,
                    level="cbsa",
                    label=metro.label,
                    cbsa=metro.cbsa,
                    relation="exact",
                ),
                origin="mapping_a",
                method=FOOD_PLAN.ref,
                lineage=tuple(lineage),
                facts=(
                    cost,
                    missing(
                        "prep_hours_month", "missing", "没有取得 ATUS 的做饭时间表：不编造准备时间"
                    ),
                    missing(
                        "fresh_meals_per_week", "missing", "篮子不能换算成“每周新鲜餐数”：不推算"
                    ),
                ),
                required=("monthly_food_cost",),
                supply_status="unknown",
                menu_policy="full_catalog",
                compile=CompileInfo(
                    status="not_compilable",
                    reasons=("prep_hours_missing", "fresh_meals_per_week_missing"),
                    e1_category="food",
                ),
                attributes={
                    "preparation": "home_cooked",
                    "channel": "grocery_retail",
                    "items": len(spec["items"]),
                },
            )
        )
    return out


# --------------------------------------------------------------------------- OSM 餐馆

CHANNEL_KEYS = ("takeaway", "delivery", "outdoor_seating", "drive_through")


def parse_osm(content: bytes) -> tuple[list[dict], str | None]:
    doc = json.loads(content)
    return doc.get("elements", []), (doc.get("osm3s") or {}).get("timestamp_osm_base")


def _channel(tags: dict, key: str) -> str:
    v = tags.get(key)
    if v is None:
        return "unknown"
    return {"yes": "yes", "only": "only", "no": "no"}.get(v, f"other:{v}")[:20]


def restaurant_records(
    elements: list[dict], metro: Metro, snapshot: str | None, raw_sha: str | None
) -> Iterator[StdRecord]:
    day = (snapshot or "1970-01-01")[:10]
    period = Period(
        label=f"OSM snapshot {day}",
        start=_dt.date.fromisoformat(day),
        end=_dt.date.fromisoformat(day),
    )
    for el in elements:
        tags = el.get("tags") or {}
        amenity = tags.get("amenity")
        if amenity not in ("restaurant", "fast_food", "cafe"):
            continue
        name = tags.get("name")
        oid = f"{el['type']}/{el['id']}"
        hours = tags.get("opening_hours")
        fields = (
            missing("price", "missing", "OSM 不记录价格"),
            FieldValue(
                name="opening_hours",
                status="present",
                raw=hours[:200],
                value=hours[:200],
                kind="observed",
            )
            if hours
            else missing("opening_hours", "missing", "OSM 没有开放时间"),
        )
        dine_in = (
            "yes"
            if amenity in ("restaurant", "cafe") and tags.get("takeaway") != "only"
            else ("unknown" if amenity == "fast_food" else "no")
        )
        yield StdRecord(
            record_id=f"food.osm.{metro.cbsa}.{el['type']}.{el['id']}",
            sector="food",
            record_type="restaurant_venue",
            label=(
                name
                or f"（未命名的{ {'restaurant': '餐馆', 'fast_food': '快餐店', 'cafe': '咖啡馆'}[amenity] }）"
            )[:200],
            geo=GeoRef(
                country="US",
                scheme="OSM",
                code=oid,
                level="point",
                label=metro.principal_city.name,
                cbsa=metro.cbsa,
                relation="within",
            ),
            period=period,
            source=SourceRef(
                source_id="osm_overpass",
                locator=f"overpass amenity={amenity} {oid}",
                raw_sha256=raw_sha,
            ),
            fields=fields,
            attributes={
                "amenity": amenity,
                "cuisine": tags.get("cuisine"),
                "brand": tags.get("brand"),
                "preparation": "restaurant_prepared",
                "dine_in": dine_in,
                **{k: _channel(tags, k) for k in CHANNEL_KEYS},
                "diet_vegetarian": tags.get("diet:vegetarian"),
                "diet_vegan": tags.get("diet:vegan"),
                "named": name is not None,
            },
        )


def restaurant_opportunity(rec: StdRecord):
    from product.realdata.registry import make_opportunity

    return make_opportunity(
        opportunity_id=rec.record_id.replace("food.osm.", "food.venue.", 1),
        sector="food",
        kind="restaurant_venue",
        label=rec.label,
        cbsa=rec.geo.cbsa,
        geo=rec.geo,
        origin="record",
        lineage=(rec.record_id,),
        facts=rec.fields,
        required=(),
        supply_status="offered",
        menu_policy="full_catalog",
        compile=CompileInfo(
            status="needs_e1_extension",
            reasons=("price_unknown", "e1_has_no_meal_level_choice"),
            e1_category="food",
        ),
        attributes=dict(rec.attributes),
        terms={
            "sales_tax": "unknown",
            "service_fee": "unknown",
            "delivery_fee": "unknown",
            "tip": "unknown",
        },
    )


# --------------------------------------------------------------------------- CE：家庭食品支出（都会区）


def parse_ce(content: bytes) -> dict[str, dict[str, str]]:
    """CE 都会区表 → 都会区名称 → {consumer_units, persons, food_at_home, food_away}（2023–24 两年平均，原样文字）。"""
    from product.realdata import quiet_xlsx

    with quiet_xlsx():
        x = pd.read_excel(io.BytesIO(content), header=None, dtype=str)
    header_row = next(i for i in range(10) if str(x.iloc[i, 0]).strip() == "Item")
    names = [str(v).replace("\n", " ").strip() for v in x.iloc[header_row].tolist()]
    out: dict[str, dict[str, str]] = {n: {} for n in names[2:]}
    wanted = {
        "Number of consumer units (in thousands) a/": "consumer_units_k",
        "People": "persons_per_cu",
        "Food at home": "food_at_home",
        "Food away from home": "food_away",
    }
    for i in range(header_row + 1, len(x)):
        label = str(x.iloc[i, 0]).strip()
        if label in wanted:
            for j, n in enumerate(names[2:], start=2):
                if wanted[label] not in out[n]:
                    out[n][wanted[label]] = str(x.iloc[i, j]).strip()
    return out
