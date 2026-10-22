"""住房：ACS 2024 年 1 年期 PUMS 的租住与空置待租样本 → 标准化记录与分析表。

一条记录是一个**样本住房单位**（不是房源广告）：

* 空置待租（VACS = 1）的 RNTP 是**要价**（rent asked），最接近“新来的人会看到的报价”，但样本少，而且偏向难租的房子；
* 过去 12 个月内迁入的租住户（MV = 1）的 RNTP 是新签合同的租金，样本多一些；
* 其余租住户是**存量**租金：住得越久，租金越可能低于现在的市场价。三者分开标记（``segment``），B 映射默认只用前两者。

金额：RNTP（合同租金或要价）与 GRNTP（毛租金 = 合同租金 + 水电燃料等）是受访者报告的月额，四舍五入、州内顶码。2024 年 PUMS 的
ADJHSG = 1.000000，乘上它得到 2024 年不变价（派生，不是估计）；当地价格水平不变，没有空间平减。

ACS **不测面积**：房间数与卧室数不是面积，面积字段记为缺失。租约条件（租期、押金、宠物）也不在 ACS 里，记为缺失。
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterator
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
from product.realdata.geo import MetroGeo
from product.realdata.mapping_b import PRICE_POSITIONS
from product.realdata.prices import ADJHSG_METHOD

PERIOD_2024 = Period(
    label="2024 calendar year", start=_dt.date(2024, 1, 1), end=_dt.date(2024, 12, 31)
)
STRUCTURE_CLASS = {
    "01": "mobile_other",
    "02": "single_family",
    "03": "single_family",
    "04": "small_multifamily",
    "05": "small_multifamily",
    "06": "small_multifamily",
    "07": "large_multifamily",
    "08": "large_multifamily",
    "09": "large_multifamily",
    "10": "mobile_other",
}
STRUCTURE_ZH = {
    "single_family": "独栋或联排住宅",
    "small_multifamily": "2–9 户的小型公寓楼",
    "large_multifamily": "10 户以上的公寓楼",
    "mobile_other": "活动房屋、房车等",
}
BEDROOM_ZH = {"0": "开间（无独立卧室）", "1": "一卧", "2": "两卧", "3plus": "三卧及以上"}
SEGMENT_ZH = {
    "vacant_for_rent": "空置待租（要价）",
    "recent_mover": "过去 12 个月内迁入的租住户（新合同租金）",
    "stock": "存量租住户",
}
UTILITY_FLAG = {"1": "included", "2": "excluded", "3": "excluded", "4": "excluded"}
CLASSIFY = MethodSpec(
    method_id="housing_unit_classification",
    version="1",
    family="classification",
    summary_zh=(
        "卧室数分成 开间 / 一卧 / 两卧 / 三卧及以上；楼型按 BLD 分成 独栋或联排（02–03）、2–9 户（04–06）、10 户以上（07–09）、"
        "活动房屋等（01、10）；样本分成 空置待租 / 12 个月内迁入 / 存量。分类只合并官方代码，不推断。"
    ),
    inputs=("census_acs1_pums_2024",),
)


def bedroom_class(bdsp: int) -> str:
    return "3plus" if bdsp >= 3 else str(int(bdsp))


def segment(ten: float, vacs: float, mv: float) -> str:
    if vacs == 1:
        return "vacant_for_rent"
    if ten == 3 and mv == 1:
        return "recent_mover"
    return "stock"


def prepare(frame: pd.DataFrame, geo: MetroGeo) -> pd.DataFrame:
    """一个都会区的住房样本 → 分析表（分类、市场段、是否完整在内的 PUMA、是否可能顶码）。"""
    df = frame.copy()
    df["cbsa"] = geo.cbsa
    df["puma_code"] = df["STATE"] + df["PUMA"]
    full = {p.code for p in geo.pumas if p.full}
    df["full_puma"] = df["puma_code"].isin(full)
    df["bed_class"] = df["BDSP"].astype(int).map(bedroom_class)
    df["struct_class"] = df["BLD"].astype(str).str.zfill(2).map(STRUCTURE_CLASS).fillna("unknown")
    df["segment"] = [
        segment(t, v, m) for t, v, m in zip(df["TEN"], df["VACS"], df["MV"], strict=True)
    ]
    adj = df["ADJHSG"].astype(float) / 1_000_000
    df["rent_contract"] = (df["RNTP"].astype(float) * adj).round(2)
    df["rent_gross"] = (pd.to_numeric(df["GRNTP"], errors="coerce") * adj).round(2)
    # 顶码：PUMS 把州内最高的一段租金换成同一个值（顶码组的平均）。同州最高值的记录标为“可能顶码”。
    state_max = df.groupby("STATE")["RNTP"].transform("max")
    df["topcoded"] = df["RNTP"] == state_max
    df["record_id"] = "housing.acs2024." + df["SERIALNO"].str.lower()
    return df


def _money(name: str, raw: float, adjhsg: str, value: float) -> FieldValue:
    return FieldValue(
        name=name,
        status="present",
        raw=str(int(raw)),
        raw_unit="USD/month（ACS 2024 报告值，四舍五入、州内顶码）",
        value=str(Decimal(str(value)).quantize(Decimal("0.01"))),
        unit="USD/month",
        numeraire="USD_2024_LOCAL",
        kind="derived",
        method=f"{ADJHSG_METHOD.ref}（ADJHSG={adjhsg}）",
    )


def records(
    df: pd.DataFrame, geo: MetroGeo, labels: dict[str, dict[str, str]], raw_sha: dict[str, str]
) -> Iterator[StdRecord]:
    """分析表 → 标准化记录（每个样本住房单位一条）。``raw_sha`` 是各州原始文件的 SHA-256。"""
    bld_label = labels.get("BLD", {})
    yr_label = labels.get("YRBLT", {})
    for row in df.itertuples(index=False):
        st, puma = row.STATE, row.PUMA
        member = geo.puma(st, puma)
        name = member.name if member and member.name else f"PUMA {st}{puma}"
        bld = str(row.BLD).zfill(2)
        fields = [
            _money("contract_rent_month", row.RNTP, str(row.ADJHSG), row.rent_contract),
            (
                _money("gross_rent_month", float(row.GRNTP), str(row.ADJHSG), row.rent_gross)
                if row.segment != "vacant_for_rent" and pd.notna(row.GRNTP) and float(row.GRNTP) > 0
                else missing(
                    "gross_rent_month",
                    "not_applicable" if row.segment == "vacant_for_rent" else "missing",
                    "空置待租房只有要价，没有水电燃料等费用"
                    if row.segment == "vacant_for_rent"
                    else "这条记录没有毛租金",
                )
            ),
            FieldValue(
                name="bedrooms",
                status="present",
                raw=str(int(row.BDSP)),
                raw_unit="bedrooms",
                value=str(int(row.BDSP)),
                unit="bedrooms",
            ),
            FieldValue(
                name="rooms",
                status="present",
                raw=str(int(row.RMSP)),
                raw_unit="rooms",
                value=str(int(row.RMSP)),
                unit="rooms",
            ),
            FieldValue(
                name="structure_type", status="present", raw=bld, value=bld_label.get(bld, bld)
            ),
            (
                FieldValue(
                    name="year_built",
                    status="present",
                    raw=str(int(row.YRBLT)),
                    value=yr_label.get(str(int(row.YRBLT)), str(int(row.YRBLT))),
                )
                if pd.notna(row.YRBLT)
                else missing("year_built", "missing", "没有建成年代")
            ),
            missing("floor_area", "missing", "ACS 不测面积；房间数与卧室数不是面积"),
            missing("lease_terms", "missing", "ACS 不收集租期、押金、宠物等租约条件"),
        ]
        terms = {}
        if row.segment != "vacant_for_rent":
            for util, col in (("electricity", "ELEFP"), ("gas", "GASFP"), ("water", "WATFP")):
                v = getattr(row, col)
                terms[f"{util}_in_contract_rent"] = (
                    UTILITY_FLAG.get(str(int(v)), "unknown") if pd.notna(v) else "unknown"
                )
        else:
            terms = {
                "electricity_in_contract_rent": "unknown",
                "gas_in_contract_rent": "unknown",
                "water_in_contract_rent": "unknown",
            }
        yield StdRecord(
            record_id=row.record_id,
            sector="housing",
            record_type="rental_unit_sample",
            label=f"{BEDROOM_ZH[row.bed_class]}·{STRUCTURE_ZH.get(row.struct_class, '其他')}·{name}",
            geo=GeoRef(
                country="US",
                scheme="PUMA2020",
                code=row.puma_code,
                level="puma",
                label=name,
                cbsa=geo.cbsa,
                relation="within" if row.full_puma else "overlaps",
            ),
            period=PERIOD_2024,
            source=SourceRef(
                source_id="census_acs1_pums_2024",
                locator=f"csv_h{st}.zip psam_h{st}.csv SERIALNO={row.SERIALNO}",
                raw_sha256=raw_sha.get(st),
            ),
            fields=tuple(fields),
            attributes={
                "segment": row.segment,
                "bedroom_class": row.bed_class,
                "structure_class": row.struct_class,
                "meals_included": None if pd.isna(row.RNTM) else int(row.RNTM) == 1,
                "possibly_topcoded": bool(row.topcoded),
                "full_puma": bool(row.full_puma),
            },
            terms=terms,
            weight=str(int(row.WGTP)),
        )


# --------------------------------------------------------------------------- 机会（B 为主、A 补共同覆盖）

POSITIONS = PRICE_POSITIONS
POSITION_ZH = {
    "1/6": "这一类里偏低的三分之一",
    "1/2": "这一类里居中的三分之一",
    "5/6": "这一类里偏高的三分之一",
}
TIER_ZH = {"core": "通勤较短的居住区", "middle": "通勤中等的居住区", "outer": "通勤较长的居住区"}
HOUSING_COMPILE_REASONS = ("floor_area_unknown", "commute_is_resident_median_not_unit_specific")
REQUIRED = ("monthly_rent", "bedrooms_class")


def _rent_fact(cell, position: str, method: str) -> FieldValue:
    if cell is None:
        return missing("monthly_rent", "missing", "回退到所有层级都没有足够样本：不生成价格")
    value = cell.quantiles.get(position)
    if value is None:
        return missing(
            "monthly_rent", "suppressed", "落在州内顶码值以上：只知道不低于顶码值，不填数"
        )
    return FieldValue(
        name="monthly_rent",
        status="present",
        value=value,
        unit="USD/month",
        numeraire="USD_2024_LOCAL",
        kind="estimated",
        method=method,
        assumptions=("housing_new_market_proxy",) if cell.population == "new_market_proxy" else (),
        raw=f"{cell.level}:{position}",
        raw_unit="加权分位数（真实观测值）",
    )


def _commute_facts(c, method: str) -> list[FieldValue]:
    if c is None or not c.supported:
        return [
            missing("commute_oneway_minutes", "missing", "这一地区的通勤样本不足"),
            missing("commute_roundtrip_minutes", "missing", "这一地区的通勤样本不足"),
        ]
    return [
        FieldValue(
            name="commute_oneway_minutes",
            status="present",
            value=str(int(c.median_oneway)),
            unit="minutes",
            kind="estimated",
            method=method,
            raw=f"p25={c.p25:g} p75={c.p75:g} n={c.n_commuters}",
            raw_unit="单程分钟（居民去各自工作地点）",
        ),
        FieldValue(
            name="commute_roundtrip_minutes",
            status="present",
            value=str(int(2 * c.median_oneway)),
            unit="minutes",
            kind="assumption",
            method=method,
            assumptions=("commute_roundtrip_is_twice_oneway",),
        ),
        FieldValue(
            name="residents_wfh_share",
            status="present",
            value=c.wfh_share,
            unit="share",
            kind="estimated",
            method=method,
        ),
    ]


def _common_facts(bed: str) -> list[FieldValue]:
    return [
        FieldValue(
            name="bedrooms_class",
            status="present",
            value=bed,
            unit="class",
            kind="derived",
            method=CLASSIFY.ref,
        ),
        missing("floor_area", "missing", "ACS 不测面积；卧室数不是面积，不推算"),
        missing("lease_terms", "missing", "ACS 不收集租约条件"),
    ]


def b_opportunities(df: pd.DataFrame, model, commute: dict, geo: MetroGeo) -> list:
    """B：每个（完整在内的 PUMA × 卧室）格生成三个价位的候选；价格取本格或回退层的条件分布，通勤取这个 PUMA 的居民通勤。"""
    from common.intake_contracts import CompileInfo
    from product.realdata.mapping_b import COMMUTE_METHOD, HOUSING_METHOD
    from product.realdata.registry import make_opportunity, weight_text

    market = df[df["full_puma"] & df["segment"].isin(("vacant_for_rent", "recent_mover"))]
    mass = market.groupby(["puma_code", "bed_class"])["WGTP"].sum()
    out = []
    for (puma_code, bed), w in sorted(mass.items()):
        tier = model.tiers.get(puma_code)
        levels = [("puma_bed", (puma_code, bed))]
        if tier:
            levels.append(("tier_bed", (tier, bed)))
        levels += [("metro_bed", (bed,)), ("metro_bed_stock", (bed,))]
        cell, tried = model.resolve(levels)
        member = geo.puma(puma_code[:2], puma_code[2:])
        name = member.name if member and member.name else f"PUMA {puma_code}"
        c = commute.get(puma_code)
        for i, pos in enumerate(POSITIONS, start=1):
            facts = (
                _rent_fact(cell, pos, HOUSING_METHOD.ref),
                *_common_facts(bed),
                *_commute_facts(c, COMMUTE_METHOD.ref),
            )
            lineage = tuple(
                x
                for x in ((cell.table_id if cell else None), f"b.commute.{geo.cbsa}.{puma_code}")
                if x
            )
            out.append(
                make_opportunity(
                    opportunity_id=f"housing.b.{geo.cbsa}.{puma_code}.{bed}.q{i}",
                    sector="housing",
                    kind="rental_listing_candidate",
                    label=f"{BEDROOM_ZH[bed]}·{name}·{POSITION_ZH[pos]}",
                    cbsa=geo.cbsa,
                    geo=GeoRef(
                        country="US",
                        scheme="PUMA2020",
                        code=puma_code,
                        level="puma",
                        label=name,
                        cbsa=geo.cbsa,
                        relation="within",
                    ),
                    origin="mapping_b",
                    method=HOUSING_METHOD.ref,
                    lineage=lineage,
                    facts=facts,
                    required=REQUIRED,
                    supply_status="offered",
                    menu_policy="annual_exposure_sample",
                    compile=CompileInfo(
                        status="needs_e1_extension",
                        reasons=HOUSING_COMPILE_REASONS,
                        e1_category="housing",
                    ),
                    attributes={
                        "tier": tier,
                        "price_level_used": cell.level if cell else None,
                        "price_population": cell.population if cell else None,
                        "fallback_tried": ",".join(tried) or None,
                        "cell_n": cell.n if cell else 0,
                        "structure_mix": (
                            ";".join(f"{k}={v}" for k, v in cell.structure_mix.items())
                            if cell
                            else None
                        ),
                        "utilities_median_month": cell.utilities_median if cell else None,
                        "price_position": pos,
                    },
                    terms={"utilities_in_rent": "unknown"},
                    target_weight=weight_text(w / len(POSITIONS)),
                    weight_basis="cell_market_weight/3：本 PUMA × 卧室格里空置待租与 12 个月内迁入租住户的 WGTP 之和 ÷ 3",
                    stratum=bed,
                    price_fact="monthly_rent",
                )
            )
    return out


def a_opportunities(points, tier_commute: dict, geo: MetroGeo) -> list:
    """A：共同覆盖网格的候选。网格里不支持的组合不生成（由调用方计数）。"""
    from common.intake_contracts import CompileInfo
    from product.realdata.mapping_a import HOUSING_GRID
    from product.realdata.mapping_b import COMMUTE_METHOD
    from product.realdata.registry import make_opportunity, weight_text

    out = []
    for p in points:
        if not p.supported:
            continue
        i = POSITIONS.index(p.position) + 1
        facts = (
            _rent_fact(p.cell, p.position, HOUSING_GRID.ref),
            *_common_facts(p.bed),
            *_commute_facts(tier_commute.get(p.tier), COMMUTE_METHOD.ref),
        )
        out.append(
            make_opportunity(
                opportunity_id=f"housing.a.{geo.cbsa}.{p.tier}.{p.bed}.{p.struct}.p{i}",
                sector="housing",
                kind="coverage_grid_candidate",
                label=f"{BEDROOM_ZH[p.bed]}·{STRUCTURE_ZH[p.struct]}·{TIER_ZH[p.tier]}·{POSITION_ZH[p.position]}",
                cbsa=geo.cbsa,
                geo=GeoRef(
                    country="US",
                    scheme="CBSA2023",
                    code=geo.cbsa,
                    level="cbsa",
                    label=geo.title,
                    cbsa=geo.cbsa,
                    relation="exact",
                ),
                origin="mapping_a",
                method=HOUSING_GRID.ref,
                lineage=(p.cell.table_id, f"b.commute_tier.{geo.cbsa}.{p.tier}"),
                facts=facts,
                required=REQUIRED,
                supply_status="offered" if p.weight > 0 else "unknown",
                menu_policy="annual_exposure_sample",
                compile=CompileInfo(
                    status="needs_e1_extension",
                    reasons=HOUSING_COMPILE_REASONS,
                    e1_category="housing",
                ),
                attributes={
                    "tier": p.tier,
                    "structure_class": p.struct,
                    "price_level_used": p.cell.level,
                    "price_population": p.cell.population,
                    "fallback_tried": ",".join(p.tried) or None,
                    "cell_n": p.cell.n,
                    "price_position": p.position,
                    "role": "a_coverage",
                },
                target_weight=weight_text(p.weight),
                weight_basis="grid_cell_market_weight/3：本通勤层 × 卧室 × 楼型格的新租市场代理 WGTP 之和 ÷ 3（0 表示本城没有观测到，只供比较）",
                stratum=f"a:{p.bed}",
                price_fact="monthly_rent",
            )
        )
    return out
