"""工业品与便利：ENERGY STAR 认证的家用洗衣机、烘干机、洗碗机、洗烘一体机型号（真实品牌与型号）。

* 一条记录 = 一个认证型号：品牌、型号、品类、规格（容量、尺寸、年耗电、用水）、功能说明、认证日期与市场（美国 / 加拿大）。
* **价格、寿命、保修、安装条件与减负效果分别记录，全部缺失**：ENERGY STAR 不记价格；本轮没有可用的公开零售报价来源；型号层面没有
  寿命与保修资料；减负效果（本人每月还要做多少小时）没有测量。缺什么就写什么原因，不补值，所以这些型号不能编进当前引擎。
* 供给：认证型号标明面向美国市场，但某座城的零售供给没有观测，所以每座城的供给状态都是“未知”。
* 品牌与型号只保存有依据的真实记录；不生成通用品冒充真实品牌商品。
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterator

from common.intake_contracts import (
    CompileInfo,
    FieldValue,
    GeoRef,
    Period,
    SourceRef,
    StdRecord,
    missing,
)

CATEGORY_ZH = {
    "clothes_washer": "洗衣机",
    "clothes_dryer": "烘干机",
    "dishwasher": "洗碗机",
    "combo_washer_dryer": "洗烘一体机",
}
E1_TASK = {
    "clothes_washer": "laundry",
    "clothes_dryer": "laundry",
    "combo_washer_dryer": "laundry",
    "dishwasher": None,
}
NATION = GeoRef(
    country="US",
    scheme="Nation",
    code="US",
    level="national",
    label="United States",
    cbsa=None,
    relation="contains",
)
SPEC_FIELDS = {
    "clothes_washer": (
        ("volume_cubic_feet", "capacity", "cubic feet"),
        ("annual_energy_use_kwh_year", "annual_energy", "kWh/year"),
        ("annual_water_use_gallons_year", "annual_water", "gallons/year"),
    ),
    "clothes_dryer": (
        ("drum_capacity_cu_ft", "capacity", "cubic feet"),
        ("estimated_annual_energy_use_kwh_yr", "annual_energy", "kWh/year"),
    ),
    "dishwasher": (
        ("capacity_maximum_number_of_place_settings", "capacity", "place settings"),
        ("annual_energy_use_kwh_year", "annual_energy", "kWh/year"),
        ("water_use_gallons_cycle", "water_per_cycle", "gallons/cycle"),
    ),
    "combo_washer_dryer": (
        ("volume_cubic_feet", "capacity", "cubic feet"),
        ("annual_energy_use_kwh_year", "annual_energy", "kWh/year"),
    ),
}


def _date(text: str | None) -> _dt.date | None:
    if not text:
        return None
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def records(rows: list[dict], category: str, raw_sha: str | None) -> Iterator[StdRecord]:
    for r in rows:
        d = _date(
            r.get("date_qualified") or r.get("date_certified") or r.get("date_available_on_market")
        ) or _dt.date(2000, 1, 1)
        specs = []
        for key, name, unit in SPEC_FIELDS[category]:
            v = r.get(key)
            specs.append(
                FieldValue(
                    name=name, status="present", raw=str(v), raw_unit=unit, value=str(v), unit=unit
                )
                if v not in (None, "")
                else missing(name, "missing", f"ENERGY STAR 这一型号没有 {key}")
            )
        dims = [
            FieldValue(
                name=f"{axis}_inches",
                status="present",
                raw=str(r[f"{axis}_inches"]),
                raw_unit="inches",
                value=str(r[f"{axis}_inches"]),
                unit="inches",
            )
            if r.get(f"{axis}_inches")
            else missing(f"{axis}_inches", "missing", "没有这一尺寸")
            for axis in ("width", "height", "depth")
        ]
        fields = (
            missing("price", "missing", "ENERGY STAR 不记价格；本轮没有可用的公开零售报价来源"),
            missing("lifetime_years", "missing", "型号层面没有寿命资料"),
            missing("warranty", "missing", "型号层面没有保修资料"),
            missing(
                "installation", "missing", "安装条件（水电接口、通风）没有成套资料；只有外形尺寸"
            ),
            missing("relief_hours", "missing", "减负效果（本人每月还要做多少小时）没有测量资料"),
            *specs,
            *dims,
        )
        brand, model = str(r.get("brand_name", "")).strip(), str(r.get("model_number", "")).strip()
        yield StdRecord(
            record_id=f"goods.energystar.{category}.{str(r['pd_id']).lower()}",
            sector="goods",
            record_type="appliance_model",
            label=f"{brand} {model}（{CATEGORY_ZH[category]}）"[:200],
            geo=NATION,
            period=Period(label="ENERGY STAR certification date", start=d, end=d),
            source=SourceRef(
                source_id="energystar_products",
                locator=f"data.energystar.gov pd_id={r['pd_id']}",
                raw_sha256=raw_sha,
            ),
            fields=fields,
            attributes={
                "category": category,
                "brand": brand,
                "model_number": model,
                "type": r.get("load_configuration") or r.get("type") or r.get("product_type"),
                "features": (
                    r.get("additional_washer_features")
                    or r.get("additional_dryer_features")
                    or r.get("additional_product_features")
                    or None
                ),
                "markets": r.get("markets"),
                "connected": r.get("connected") or r.get("connected_capable"),
                "energy_star_model_identifier": r.get("energy_star_model_identifier"),
                "e1_task": E1_TASK[category],
            },
            terms={"sales_tax": "unknown", "delivery": "unknown", "installation": "unknown"},
        )


def opportunity(rec: StdRecord):
    from product.realdata.registry import make_opportunity

    return make_opportunity(
        opportunity_id=rec.record_id.replace("goods.energystar.", "goods.model.", 1),
        sector="goods",
        kind="appliance_model",
        label=rec.label,
        cbsa=None,
        geo=rec.geo,
        origin="record",
        lineage=(rec.record_id,),
        facts=rec.fields,
        required=("capacity",),
        supply_status="unknown",
        menu_policy="full_catalog",
        compile=CompileInfo(
            status="not_compilable",
            reasons=("price_unknown", "lifetime_unknown", "relief_effect_unknown"),
            e1_category="durable",
        ),
        attributes=dict(rec.attributes),
        terms=dict(rec.terms),
        price_fact="price",
    )
