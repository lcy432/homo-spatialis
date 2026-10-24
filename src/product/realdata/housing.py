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
from product.realdata.acs import (
    TOPCODE_METHOD,
    UTILITY_TOPCODE_COLUMNS,
    TopcodeRule,
    utility_topcoded,
)
from product.realdata.geo import MetroGeo
from product.realdata.mapping_b import GRID_POSITIONS
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


def facilities_status(kit: object, plm: object) -> str:
    """厨卫条件（ACS KIT、PLM）：两项都是 1 才是完整；任何一项是 2 就是不完整；空值或别的代码是未知（不当完整）。"""

    def code(v: object) -> int | None:
        try:
            return None if pd.isna(v) else int(float(v))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    k, p = code(kit), code(plm)
    if k == 2 or p == 2:
        return "incomplete"
    if k == 1 and p == 1:
        return "complete"
    return "unknown"


def _gross_affected(row, utility_rules: dict[str, dict[str, tuple[float, float]]]) -> str | None:
    """毛租金受不受顶码影响：合同租金顶码（或是否顶码未知），或任何一项有金额的水电燃料顶码。返回影响来源或 None。"""
    if row.censored:
        return "RNTP"
    rules = utility_rules.get(row.STATE, {})
    for var in UTILITY_TOPCODE_COLUMNS:
        v = getattr(row, var, None)
        if v is not None and not pd.isna(v) and utility_topcoded(float(v), rules.get(var)):
            return var
    return None


def prepare(
    frame: pd.DataFrame,
    geo: MetroGeo,
    rules: dict[str, TopcodeRule],
    utility_rules: dict[str, dict[str, tuple[float, float]]] | None = None,
) -> pd.DataFrame:
    """一个都会区的住房样本 → 分析表（分类、市场段、是否完整在内的 PUMA、顶码状态与真实值下界、厨卫条件、毛租金是否受顶码
    影响）。

    顶码按官方州别阈值与替代值识别（``rules``，每个州一条；水电燃料 ``utility_rules``），**不**按筛选后样本的最大值猜。"""
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
    status, bound = [], []
    for st, v in zip(df["STATE"], df["RNTP"].astype(float), strict=True):
        s, b = rules[st].classify(v)
        status.append(s)
        bound.append(b)
    df["topcode_status"] = status
    df["censored"] = df["topcode_status"] != "not_topcoded"
    df["censor_bound"] = pd.array(bound, dtype="Float64").astype(float)
    df["record_id"] = "housing.acs2024." + df["SERIALNO"].str.lower()
    for col in ("KIT", "PLM", *UTILITY_TOPCODE_COLUMNS):
        if col not in df.columns:
            df[col] = pd.NA
    df["facilities"] = [facilities_status(k, p) for k, p in zip(df["KIT"], df["PLM"], strict=True)]
    urules = utility_rules or {}
    df["gross_affected_by"] = [_gross_affected(r, urules) for r in df.itertuples(index=False)]
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


def _censored_rent(name: str, row, rules: dict[str, TopcodeRule]) -> FieldValue:
    rule = rules[row.STATE]
    if row.topcode_status == "topcoded":
        reason = (
            f"顶码：真实租金高于本州官方阈值 {rule.threshold:g} 美元/月；{rule.replacement:g} 是州内顶码组的平均（官方替代值），"
            "不是这条记录的真实值（Census 2024 PUMS top and bottom coded values）"
        )
    else:
        reason = f"是否顶码未知：{reason_state(rule)}；{float(row.RNTP):g} 可能是替代值，真实值只知道高于 {float(row.censor_bound):g}"
    if name == "gross_rent_month":
        reason = "毛租金含顶码（或可能顶码）的合同租金：" + reason
    return FieldValue(
        name=name,
        status="suppressed",
        raw=str(int(row.RNTP)) if name == "contract_rent_month" else None,
        raw_unit="USD/month（ACS 2024 报告值）",
        reason=reason[:300],
        method=TOPCODE_METHOD.ref,
    )


def _affected(v: object) -> str | None:
    """毛租金受顶码影响的来源（RNTP 或某项水电燃料）；空值（pandas 的 None / NaN）是没有影响。"""
    return None if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)


def _utility_censored_gross(row) -> FieldValue:
    return FieldValue(
        name="gross_rent_month",
        status="suppressed",
        raw=None if pd.isna(row.GRNTP) else str(int(row.GRNTP)),
        raw_unit="USD/month（ACS 2024 报告值）",
        reason=(
            f"毛租金含顶码（或是否顶码未知）的水电燃料费（{_affected(row.gross_affected_by)}，Census 2024 PUMS top and bottom coded "
            "values）：替代值不是这套房的真实费用，真实毛租金只知道不低于报告值"
        )[:300],
        method=TOPCODE_METHOD.ref,
    )


def _facility(name: str, raw: object, what: str) -> FieldValue:
    """ACS KIT / PLM：1 有（完整），2 没有（不完整）；空值是没有回答或不适用——未知，不当完整。"""
    code = None
    try:
        code = None if pd.isna(raw) else str(int(float(raw)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        code = None
    value = {"1": "complete", "2": "incomplete"}.get(code or "")
    if value is None:
        return missing(name, "missing", f"没有这一项（{what}）的有效回答：未知，不当完整")
    return FieldValue(name=name, status="present", raw=code, value=value)


def reason_state(rule: TopcodeRule) -> str:
    return "官方顶码表里没有这个州" if rule.status == "missing" else "官方阈值以上却不等于替代值"


def records(
    df: pd.DataFrame,
    geo: MetroGeo,
    labels: dict[str, dict[str, str]],
    raw_sha: dict[str, str],
    rules: dict[str, TopcodeRule],
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
            _censored_rent("contract_rent_month", row, rules)
            if row.censored
            else _money("contract_rent_month", row.RNTP, str(row.ADJHSG), row.rent_contract),
            (
                _censored_rent("gross_rent_month", row, rules)
                if row.censored and row.segment != "vacant_for_rent"
                else _utility_censored_gross(row)
                if row.segment != "vacant_for_rent" and _affected(row.gross_affected_by)
                else _money("gross_rent_month", float(row.GRNTP), str(row.ADJHSG), row.rent_gross)
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
            _facility("kitchen_facilities", row.KIT, "炉灶、冰箱与带龙头的水槽"),
            _facility("plumbing_facilities", row.PLM, "冷热自来水与浴缸或淋浴"),
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
                "topcode_status": row.topcode_status,
                "topcode_threshold": rules[row.STATE].threshold,
                "topcode_lower_bound": None
                if pd.isna(row.censor_bound)
                else float(row.censor_bound),
                "full_puma": bool(row.full_puma),
                "facilities": row.facilities,
                "gross_affected_by": _affected(row.gross_affected_by),
            },
            terms=terms,
            weight=str(int(row.WGTP)),
        )


# --------------------------------------------------------------------------- 机会（B 为主、A 补共同覆盖）

POSITION_ZH = {
    "1/6": "这一类里偏低的三分之一",
    "1/2": "这一类里居中的三分之一",
    "5/6": "这一类里偏高的三分之一",
}
TIER_ZH = {"core": "通勤较短的居住区", "middle": "通勤中等的居住区", "outer": "通勤较长的居住区"}
#: E1 的住房要面积（住房空间评分读它）；ACS 不测面积，所以缺。通勤是居民去各自工作地点的中位数，不是这套房到岗位的通勤。
HOUSING_E1_NEEDS = ("floor_area",)
HOUSING_DATA_GAPS = ("commute_is_resident_median_not_unit_specific",)
REQUIRED = ("monthly_rent", "bedrooms_class")


def _rent_fact(cell, position: str, method: str) -> FieldValue:
    if cell is None:
        return missing("monthly_rent", "missing", "回退到所有层级都没有足够样本：不生成价格")
    value = cell.quantiles.get(position)
    if value is None:
        return missing(
            "monthly_rent",
            "suppressed",
            "这一价位不可识别：格内有顶码记录，它们的真实租金只知道高于本州官方阈值（跨州的格取最低阈值），这一位置的分位数落在阈值以上，不填数",
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


def support_cells(
    df: pd.DataFrame, model, commute: dict, geo: MetroGeo
) -> tuple[list, dict[str, dict]]:
    """B（``housing_rent_support@1``）：每个（完整在内的 PUMA × 卧室）格一条目标格记录，引用它实际使用那一层的完整加权经验支持表。

    目标格的总权重 = 本格新租市场代理（空置待租 ∪ 12 个月内迁入）的 WGTP 之和；回退到上层时只借上层分布的形状，不借上层的总权重。
    返回（目标格机会，支持表——按表编号去重）。逻辑上的候选 = （目标 PUMA，支持点），权重 = 目标格总权重 × 支持点权重 ÷ 支持表总权重。"""
    from product.realdata.mapping_b import (
        COMMUTE_METHOD,
        SUPPORT_METHOD,
        support_table,
        support_table_id,
    )
    from product.realdata.registry import make_opportunity, weight_text

    market = df[df["full_puma"] & df["segment"].isin(("vacant_for_rent", "recent_mover"))]
    mass = market.groupby(["puma_code", "bed_class"])["WGTP"].sum()
    by_id = df.set_index("record_id", drop=False)
    tables: dict[str, dict] = {}
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
        attrs: dict = {
            "tier": tier,
            "fallback_tried": ",".join(tried) or None,
            "target_weight_source": "target_cell_market_wgtp",
        }
        lineage: list[str] = [f"b.commute.{geo.cbsa}.{puma_code}"]
        if cell is None:
            attrs.update(support_table=None, price_level_used=None, cell_n=0)
        else:
            tid = support_table_id(cell)
            if tid not in tables:
                tables[tid] = support_table(cell, by_id.loc[list(cell.record_ids)])
            t = tables[tid]
            ws = t["weight_sum"] or 1
            attrs.update(
                support_table=tid,
                price_level_used=cell.level,
                price_population=cell.population,
                shape_borrowed=cell.level != "puma_bed",
                cell_n=cell.n,
                cell_n_eff=round(cell.n_eff, 2),
                support_points=len(t["points"]),
                **{f"share_{k}": f"{v / ws:.4f}" for k, v in t["masses"].items() if k != "raw"},
            )
            lineage = [tid, cell.table_id, *lineage]
        out.append(
            make_opportunity(
                opportunity_id=f"housing.b.{geo.cbsa}.{puma_code}.{bed}",
                sector="housing",
                kind="rental_support_cell",
                label=f"{BEDROOM_ZH[bed]}·{name}（完整租金分布）",
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
                method=SUPPORT_METHOD.ref,
                lineage=tuple(lineage),
                facts=(*_common_facts(bed), *_commute_facts(c, COMMUTE_METHOD.ref)),
                required=("bedrooms_class",),
                supply_status="offered",
                menu_policy="full_catalog",
                e1_category="housing",
                e1_needs=HOUSING_E1_NEEDS,
                data_gaps=HOUSING_DATA_GAPS,
                attributes=attrs,
                terms={"utilities_in_rent": "unknown"},
                target_weight=weight_text(w),
                weight_basis=(
                    "本 PUMA × 卧室格里空置待租与 12 个月内迁入租住户的 WGTP 之和；回退时只借上层分布的形状，总权重仍是这一格的"
                ),
                stratum=bed,
            )
        )
    return out, tables


def a_opportunities(points, tier_commute: dict, geo: MetroGeo) -> list:
    """A：共同覆盖网格的候选。网格里不支持的组合不生成（由调用方计数）。"""
    from product.realdata.mapping_a import HOUSING_GRID
    from product.realdata.mapping_b import COMMUTE_METHOD
    from product.realdata.registry import make_opportunity, weight_text

    out = []
    for p in points:
        if not p.supported:
            continue
        i = GRID_POSITIONS.index(p.position) + 1
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
                e1_category="housing",
                e1_needs=HOUSING_E1_NEEDS,
                data_gaps=HOUSING_DATA_GAPS,
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
