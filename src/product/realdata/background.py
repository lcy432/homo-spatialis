"""城市背景：地理、人口与收入、已发布的租金与通勤表、交通方式、空气质量、能源价格、地区价格水平、家庭食品支出、税辖区结构。

全部是**背景事实**，不是可以选择的生活机会，也不直接进评分：背景事实 → 评分条件的换算没有登记（E1 的城市背景事实与评分参数是
分开的两件事），所以它们“能供规划参考”，不“可评分”。

* ACS 2024 年 1 年期已发布表（表格摘要文件）：人口、家庭收入中位数、毛租金中位数（总体与按卧室数）、通勤时间分布、通勤方式。误差
  范围（MOE）原样保留在原始值里。
* 空气质量：EPA 年度 AQI（CBSA）。某一年监测天数不到 330 天就不当作完整年份（例如 2025 年旧金山只有 95 天），改用上一年并说明。
* 能源价格：BLS 平均价格在都会区只发布能源项目（电价、管道燃气、汽油），这些是真实的都会区观测值。
* 地区价格水平：BEA 2024 年 RPP（全美 = 100）。指数只说明价格水平，不当付款单价。
* 家庭食品支出：CE 2023–24 两年平均（每个消费单位每年在家吃饭与外出吃饭的支出）。这是支出，不是某一份菜的价格。
* 安全：FBI 犯罪资料 API 要密钥（没有），旧的都会区静态表没有找到——记为“访问受限 / 没有取得”，不用别的口径顶替。
* 气候：本轮没有获取 NOAA 气候正常值，记为缺失。
* 税：只记辖区结构（州、县、中心城市）。税额与规则留给 intake-03（真实税与给分身看的说明同源），这里不填任何税率。
"""

from __future__ import annotations

import datetime as _dt
import io
import zipfile
from collections import defaultdict
from decimal import Decimal

import pandas as pd

from common.intake_contracts import (
    FieldValue,
    GeoRef,
    MethodSpec,
    Period,
    SourceRef,
    StdRecord,
    missing,
)
from product.realdata.config import Metro
from product.realdata.geo import MetroGeo

P2024 = Period(label="2024", start=_dt.date(2024, 1, 1), end=_dt.date(2024, 12, 31))
HELPER_METHODS = (
    MethodSpec(
        method_id="acs_bins_share",
        version="1",
        family="conversion",
        summary_zh="ACS 已发布表的计数相除得比例；中位数所在的分箱取累计计数第一次过半的那一箱。",
        inputs=("census_acs1_tables_2024",),
    ),
    MethodSpec(
        method_id="aqi_share",
        version="1",
        family="conversion",
        summary_zh="良好天数 ÷ 有 AQI 的天数。",
        inputs=("epa_aqi_cbsa_2025",),
    ),
    MethodSpec(
        method_id="aqi_sum",
        version="1",
        family="conversion",
        summary_zh="不健康 + 非常不健康 + 危险天数之和。",
        inputs=("epa_aqi_cbsa_2025",),
    ),
    MethodSpec(
        method_id="count",
        version="1",
        family="conversion",
        summary_zh="对划定文件或 PUMA 归属计数。",
        inputs=("census_cbsa_2023",),
    ),
    MethodSpec(
        method_id="jurisdiction_paths",
        version="1",
        family="classification",
        summary_zh="由划定文件的成员县列出州与县的辖区路径；不含任何税率。",
        inputs=("census_cbsa_2023",),
    ),
)
B08303_BINS = (
    "<5",
    "5-9",
    "10-14",
    "15-19",
    "20-24",
    "25-29",
    "30-34",
    "35-39",
    "40-44",
    "45-59",
    "60-89",
    "90+",
)
LOCAL = "USD_2024_LOCAL"  # ACS 2024 年 1 年期的金额：2024 年美元、当地价格水平（没有空间平减）
AP_ENERGY = {
    "72610": "electricity_per_kwh",
    "72620": "piped_gas_per_therm",
    "74714": "gasoline_regular_per_gallon",
}


def _cbsa_geo(metro: Metro, relation: str = "exact") -> GeoRef:
    return GeoRef(
        country="US",
        scheme="CBSA2023",
        code=metro.cbsa,
        level="cbsa",
        label=metro.label,
        cbsa=metro.cbsa,
        relation=relation,
    )  # type: ignore[arg-type]


def _num(
    name: str,
    raw: str | None,
    unit: str,
    *,
    moe: str | None = None,
    kind: str = "observed",
    method: str | None = None,
    numeraire: str | None = None,
) -> FieldValue:
    if raw is None or str(raw).strip() in ("", "nan", "-", "null", "N"):
        return missing(name, "missing", "来源没有这项值")
    if unit.startswith("USD") and numeraire is None:
        raise ValueError(f"{name}: 金额字段必须写计价口径")
    return FieldValue(
        name=name,
        status="present",
        raw=str(raw) + (f"（MOE ±{moe}）" if moe else ""),
        raw_unit=unit,
        value=str(raw).strip(),
        unit=unit,
        numeraire=numeraire,
        kind=kind,  # type: ignore[arg-type]
        method=method,
    )


def acs_records(
    metro: Metro,
    tables: dict[str, dict[str, dict[str, str]]],
    geo_id: str | None,
    sha: dict[str, str],
) -> list[StdRecord]:
    out = []
    t = {name: (tables.get(name, {}).get(geo_id or "", {})) for name in tables}

    def src(name: str) -> SourceRef:
        return SourceRef(
            source_id="census_acs1_tables_2024",
            locator=f"acsdt1y2024-{name}.dat GEO_ID={geo_id}",
            raw_sha256=sha.get(name),
        )

    b = t.get("b25031", {})
    rent_fields = [
        _num(
            "median_gross_rent_all",
            b.get("B25031_E001"),
            "USD/month",
            moe=b.get("B25031_M001"),
            numeraire=LOCAL,
        )
    ]
    for i, lab in zip(range(2, 8), ("0br", "1br", "2br", "3br", "4br", "5plus_br"), strict=True):
        rent_fields.append(
            _num(
                f"median_gross_rent_{lab}",
                b.get(f"B25031_E00{i}"),
                "USD/month",
                moe=b.get(f"B25031_M00{i}"),
                numeraire=LOCAL,
            )
        )
    out.append(
        _record(
            metro,
            "acs_rent",
            "已发布的毛租金中位数（ACS 2024，付现金租金的租住户）",
            src("b25031"),
            rent_fields,
        )
    )
    c = t.get("b08303", {})
    total = c.get("B08303_E001")
    commute_fields = [_num("commuters_not_wfh", total, "workers")]
    if total and total not in ("", "0"):
        long = sum(Decimal(c.get(f"B08303_E0{i:02d}", "0") or "0") for i in (11, 12, 13))
        commute_fields.append(
            FieldValue(
                name="share_commute_45min_plus",
                status="present",
                value=str((long / Decimal(total)).quantize(Decimal("0.0001"))),
                unit="share",
                kind="derived",
                method="acs_bins_share@1",
                raw=f"(E011+E012+E013)/E001 = {long}/{total}",
            )
        )
        cum, median_bin = Decimal(0), None
        for i, label in enumerate(B08303_BINS, start=2):
            cum += Decimal(c.get(f"B08303_E{i:03d}", "0") or "0")
            if median_bin is None and cum >= Decimal(total) / 2:
                median_bin = label
        commute_fields.append(
            FieldValue(
                name="median_commute_bin",
                status="present",
                value=median_bin or "unknown",
                unit="minutes (bin)",
                kind="derived",
                method="acs_bins_share@1",
            )
        )
    out.append(
        _record(
            metro,
            "acs_commute",
            "通勤时间分布（ACS 2024，单程，不在家工作的在职者）",
            src("b08303"),
            commute_fields,
        )
    )
    m = t.get("b08301", {})
    tot = m.get("B08301_E001")
    mode_fields = [_num("workers_16plus", tot, "workers")]
    if tot and tot not in ("", "0"):
        for key, col in (
            ("share_public_transit", "B08301_E010"),
            ("share_worked_from_home", "B08301_E021"),
        ):
            v = m.get(col)
            mode_fields.append(
                FieldValue(
                    name=key,
                    status="present",
                    value=str((Decimal(v) / Decimal(tot)).quantize(Decimal("0.0001"))),
                    unit="share",
                    kind="derived",
                    method="acs_bins_share@1",
                    raw=f"{col}/B08301_E001 = {v}/{tot}",
                )
                if v
                else missing(key, "missing", "来源没有这项值")
            )
    out.append(
        _record(metro, "acs_transport_mode", "通勤方式（ACS 2024）", src("b08301"), mode_fields)
    )
    pop = t.get("b01003", {})
    inc = t.get("b19013", {})
    gr = t.get("b25064", {})
    out.append(
        _record(
            metro,
            "acs_population_income",
            "人口、家庭收入中位数与毛租金中位数（ACS 2024）",
            src("b01003"),
            [
                _num("population", pop.get("B01003_E001"), "persons", moe=pop.get("B01003_M001")),
                _num(
                    "median_household_income",
                    inc.get("B19013_E001"),
                    "USD/year (2024 dollars)",
                    moe=inc.get("B19013_M001"),
                    numeraire=LOCAL,
                ),
                _num(
                    "median_gross_rent",
                    gr.get("B25064_E001"),
                    "USD/month",
                    moe=gr.get("B25064_M001"),
                    numeraire=LOCAL,
                ),
            ],
        )
    )
    return out


def _record(
    metro: Metro,
    topic: str,
    label: str,
    source: SourceRef,
    fields: list[FieldValue],
    period: Period = P2024,
    geo: GeoRef | None = None,
    attributes: dict | None = None,
) -> StdRecord:
    return StdRecord(
        record_id=f"background.{topic}.{metro.cbsa}",
        sector="background",
        record_type="city_fact_table",
        label=f"{label}·{metro.label_zh}",
        geo=geo or _cbsa_geo(metro),
        period=period,
        source=source,
        fields=tuple(fields),
        attributes={"topic": topic, **(attributes or {})},
    )


def parse_aqi(content: bytes) -> dict[str, dict[str, str]]:
    z = zipfile.ZipFile(io.BytesIO(content))
    df = pd.read_csv(z.open(z.namelist()[0]), dtype=str)
    return {r["CBSA Code"]: dict(r) for _, r in df.iterrows()}


def aqi_record(
    metro: Metro, by_year: dict[int, tuple[dict[str, dict[str, str]], str | None]]
) -> StdRecord:
    """最近一个监测天数 ≥ 330 天的年份。"""
    for year in sorted(by_year, reverse=True):
        rows, sha = by_year[year]
        r = rows.get(metro.cbsa)
        if r is None:
            continue
        days = int(r["Days with AQI"])
        if days < 330:
            continue
        fields = [
            _num("days_with_aqi", r["Days with AQI"], "days"),
            _num("median_aqi", r["Median AQI"], "AQI"),
            _num("p90_aqi", r["90th Percentile AQI"], "AQI"),
            FieldValue(
                name="share_good_days",
                status="present",
                value=str((Decimal(r["Good Days"]) / Decimal(days)).quantize(Decimal("0.0001"))),
                unit="share",
                kind="derived",
                method="aqi_share@1",
                raw=f"{r['Good Days']}/{days}",
            ),
            _num(
                "unhealthy_days",
                str(
                    int(r["Unhealthy Days"])
                    + int(r["Very Unhealthy Days"])
                    + int(r["Hazardous Days"])
                ),
                "days",
                kind="derived",
                method="aqi_sum@1",
            ),
        ]
        skipped = [y for y in by_year if y > year]
        return _record(
            metro,
            "air_quality",
            f"空气质量（EPA 年度 AQI，{year}）",
            SourceRef(
                source_id="epa_aqi_cbsa_2025",
                locator=f"annual_aqi_by_cbsa_{year}.csv CBSA Code={metro.cbsa}",
                raw_sha256=sha,
            ),
            fields,
            period=Period(label=str(year), start=_dt.date(year, 1, 1), end=_dt.date(year, 12, 31)),
            geo=_cbsa_geo(metro, "overlaps"),
            attributes={
                "epa_cbsa_name": r["CBSA"],
                "skipped_incomplete_years": ",".join(map(str, skipped)) or None,
            },
        )
    return _record(
        metro,
        "air_quality",
        "空气质量（EPA 年度 AQI）",
        SourceRef(source_id="epa_aqi_cbsa_2025", locator="annual_aqi_by_cbsa (no complete year)"),
        [missing("median_aqi", "missing", "没有监测天数 ≥ 330 天的年份")],
    )


def parse_ap_energy(
    series: bytes, data: bytes, areas: set[str]
) -> dict[tuple[str, str], dict[str, str]]:
    meta = {}
    for ln in series.decode("utf-8", "replace").splitlines()[1:]:
        p = [x.strip() for x in ln.split("\t")]
        if len(p) >= 4 and p[1] in areas and p[2] in AP_ENERGY:
            meta[p[0]] = (p[1], p[2])
    out: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
    for ln in data.decode("utf-8", "replace").splitlines()[1:]:
        p = [x.strip() for x in ln.split("\t")]
        if (
            len(p) >= 4
            and p[0] in meta
            and p[1] == "2024"
            and p[2].startswith("M")
            and p[2] != "M13"
        ):
            out[meta[p[0]]][p[2]] = p[3]
    return dict(out)


def energy_record(
    metro: Metro, energy: dict[tuple[str, str], dict[str, str]], sha: str | None
) -> StdRecord:
    fields = []
    for item, name in AP_ENERGY.items():
        months = energy.get((metro.ap_metro_area, item), {})
        if len(months) == 12:
            mean = sum(Decimal(v) for v in months.values()) / 12
            fields.append(
                FieldValue(
                    name=name,
                    status="present",
                    value=str(mean.quantize(Decimal("0.001"))),
                    unit="USD",
                    numeraire="USD_2024_LOCAL",
                    kind="derived",
                    method="ap_annual_mean@1",
                    raw=f"12 个月 {min(months.values(), key=Decimal)}–{max(months.values(), key=Decimal)}",
                    raw_unit="USD（月度均价）",
                )
            )
        else:
            fields.append(missing(name, "missing", f"2024 年只有 {len(months)} 个月"))
    return _record(
        metro,
        "energy_prices",
        "能源价格（BLS 都会区平均价格，2024 年平均）",
        SourceRef(
            source_id="bls_ap",
            locator=f"ap.data.0.Current area={metro.ap_metro_area} items={','.join(AP_ENERGY)}",
            raw_sha256=sha,
        ),
        fields,
        geo=_cbsa_geo(metro, "overlaps"),
        attributes={"cpi_area": metro.ap_metro_area},
    )


def rpp_record(metro: Metro, rpp: dict[str, dict[str, str]], sha: str | None) -> StdRecord:
    v = rpp.get(metro.cbsa, {})
    fields = [
        _num(f"rpp_{k}", v.get(k), "index (US all items = 100)")
        for k in ("all", "goods", "housing", "utilities", "other")
    ]
    return _record(
        metro,
        "price_parities",
        "地区价格水平（BEA RPP 2024，指数，不是价格）",
        SourceRef(
            source_id="bea_rpp_msa",
            locator=f"MARPP_MSA_2008_2024.csv GeoFIPS={metro.cbsa}",
            raw_sha256=sha,
        ),
        fields,
    )


def ce_record(metro: Metro, ce: dict[str, dict[str, str]], sha: str | None) -> StdRecord:
    col = next((k for k in ce if k.lower().startswith(metro.principal_city.name.lower())), None)
    v = ce.get(col, {}) if col else {}
    period = Period(
        label="2023-2024 two-year average", start=_dt.date(2023, 1, 1), end=_dt.date(2024, 12, 31)
    )
    fields = [
        _num(
            "food_at_home_per_cu_year",
            v.get("food_at_home"),
            "USD/year per consumer unit (nominal 2023-24)",
            numeraire="USD_2023_2024_NOMINAL",
        ),
        _num(
            "food_away_per_cu_year",
            v.get("food_away"),
            "USD/year per consumer unit (nominal 2023-24)",
            numeraire="USD_2023_2024_NOMINAL",
        ),
        _num("persons_per_cu", v.get("persons_per_cu"), "persons"),
    ]
    if not col:
        fields = [missing("food_at_home_per_cu_year", "missing", "CE 都会区表里没有这个都会区")]
    return _record(
        metro,
        "food_spending",
        "家庭食品支出（CE 2023–24，支出不是菜价）",
        SourceRef(
            source_id="bls_ce_msa_2023_2024",
            locator=f"cu-msa-{metro.ce_region_file}-2-year-average-2023-2024.xlsx column={col}",
            raw_sha256=sha,
        ),
        fields,
        period=period,
        geo=_cbsa_geo(metro, "overlaps"),
        attributes={"ce_column": col},
    )


def geography_record(metro: Metro, geo: MetroGeo, sha: str | None) -> StdRecord:
    fields = [
        FieldValue(
            name="county_count",
            status="present",
            value=str(len(geo.counties)),
            unit="counties",
            kind="derived",
            method="count@1",
            raw=",".join(c.fips for c in geo.counties)[:200],
        ),
        FieldValue(
            name="metro_division_count",
            status="present",
            value=str(len(geo.divisions)),
            unit="divisions",
            kind="derived",
            method="count@1",
        ),
        FieldValue(
            name="puma_full_count",
            status="present",
            value=str(sum(1 for p in geo.pumas if p.full)),
            unit="PUMAs",
            kind="derived",
            method="count@1",
        ),
        FieldValue(
            name="puma_partial_count",
            status="present",
            value=str(sum(1 for p in geo.pumas if not p.full)),
            unit="PUMAs",
            kind="derived",
            method="count@1",
        ),
        _num("land_area_sqmi", geo.land_area_sqmi, "square miles"),
    ]
    return _record(
        metro,
        "geography",
        "都会区地理（OMB 2023 划定）",
        SourceRef(
            source_id="census_cbsa_2023",
            locator=f"list1_2023.xlsx CBSA Code={metro.cbsa}",
            raw_sha256=sha,
        ),
        fields,
        period=Period(
            label="OMB Bulletin 23-01", start=_dt.date(2023, 7, 21), end=_dt.date(2023, 7, 21)
        ),
        attributes={
            "title": geo.title,
            "states": ",".join(geo.states),
            "divisions": "; ".join(f"{c} {t}" for c, t in geo.divisions) or None,
            "central_counties": "; ".join(c.name for c in geo.counties if c.central)[:200],
        },
    )


def unavailable_records(metro: Metro, geo: MetroGeo) -> list[StdRecord]:
    return [
        _record(
            metro,
            "safety",
            "安全（没有取得）",
            SourceRef(source_id="fbi_cde", locator="not requested: API key required"),
            [
                missing(
                    "violent_crime_rate",
                    "access_restricted",
                    "FBI 犯罪资料 API 要密钥（没有）；旧的都会区静态表没有找到",
                )
            ],
        ),
        _record(
            metro,
            "climate",
            "气候（没有取得）",
            SourceRef(source_id="census_cbsa_2023", locator="not acquired"),
            [missing("climate_normals", "missing", "本轮没有获取 NOAA 气候正常值")],
        ),
        _record(
            metro,
            "tax_jurisdiction",
            "税辖区结构（税额与规则留给 intake-03）",
            SourceRef(
                source_id="census_cbsa_2023", locator=f"list1_2023.xlsx CBSA Code={metro.cbsa}"
            ),
            [
                FieldValue(
                    name="state_jurisdictions",
                    status="present",
                    value=",".join(sorted({c.state_name for c in geo.counties})),
                    kind="derived",
                    method="jurisdiction_paths@1",
                ),
                missing(
                    "income_tax_rules",
                    "missing",
                    "真实税表与给分身看的公开说明要同源（intake-03）；这里不填税率",
                ),
                missing("sales_tax_rate", "missing", "州与地方销售税率本轮没有获取（intake-03）"),
            ],
            attributes={
                "principal_city": metro.principal_city.name,
                "paths": "; ".join(sorted({f"US/{c.state_name}/{c.name}" for c in geo.counties}))[
                    :200
                ],
            },
        ),
    ]


def opportunity(rec: StdRecord):
    from product.realdata.registry import make_opportunity

    return make_opportunity(
        opportunity_id=rec.record_id.replace("background.", "background.fact.", 1),
        sector="background",
        kind="city_fact",
        label=rec.label,
        cbsa=rec.geo.cbsa,
        geo=rec.geo,
        origin="record",
        lineage=(rec.record_id,),
        facts=rec.fields,
        required=(),
        supply_status="unknown",
        menu_policy="full_catalog",
        e1_category="city_fact",
        reference_only=True,  # 城市背景是参考事实，不是可选的行动；怎样进入环境或评分另行登记
        attributes=dict(rec.attributes),
    )
