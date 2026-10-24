"""E3B 六城真实生活资料包的物品与活动（DECISIONS/0041、0042）：完整食品方案、杂务服务、设备、享受型服务、爱好装备与活动。

输入是 ``life_inputs`` 的切片与配置 ``configs/intake/life_items_us_v1.yaml``；输出是能通过 ``LifeOpportunity`` 契约的机会，
外加诊断（缺口、不支持、设备故障设定）。规则：

* 金额一律 ``USD_2024_LOCAL``，精确到分：先用分数精确计算，最后一步四舍五入到分（规则写进来源）；小时最多两位小数。
* 价格指数只乘在明确的基础报价或篮子上：食材篮子是 intake 的都会区估计价（已经过一次 RPP 商品类换算，这里不再乘）；外食按统一
  成本代理（篮子食材 + 厨师人工 + 场地及其他 × 其他服务 RPP，外送另加配送人工）；设备与装备是全国类别设定 × intake 的商品类空间
  换算系数；场地设定 × RPP 总水平。CE 都会区支出只做量级对照。
* 未知不当零：门票没有资料就是 ``unknown``，日期、年龄与整期报名表达不了的真实活动标成需要 E1 扩展，原样保留限制，不删。
* 外食只经饮食生活评分（新鲜餐与菜系），不做营养声明；新鲜餐按组成比例，外食不自动算新鲜。
* 每个 A 板块的共同结构（方案族、参数、先验、城市证据、零售修正与状态）见 ``a_blocks``。
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

from common.life_contracts import (
    ActivityFacts,
    DurationSpec,
    EnjoymentProvision,
    EquipmentFacts,
    FailureSpec,
    FoodPlanFacts,
    FoodPlanPart,
    LifeOpportunity,
    PriceQuote,
    ResourceProfile,
    SourceRecord,
    TaskSubstitution,
)
from common.money import Money
from product.realdata.activities import age_eligibility
from product.realdata.daily_support_data import allocated_annual

ITEMS_VERSION = "e3b-life-items-v1"
WEEKS_PER_MONTH = Fraction(52, 12)
MEALS_PER_WEEK = 21

#: 一项机会的编译状态（互不重叠）
#: executable 可以直接放进城市目录；needs_area 是岗位，等整合时按通勤会话的就业片区写 area；needs_e1_extension 是真实资料
#: 但 E1 现在表达不了（日期窗口、整期报名、年龄资格）；data_unknown 是价格资料没有（不当零价）
STATUSES = ("executable", "needs_area", "needs_e1_extension", "data_unknown")


@dataclass(frozen=True)
class Item:
    """一项编译好的机会与它的编译信息。"""

    domain: str
    opportunity: LifeOpportunity
    status: str
    assumptions: tuple[str, ...]
    records: tuple[str, ...]
    gaps: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "status": self.status,
            "gaps": list(self.gaps),
            "assumptions": list(self.assumptions),
            "records": list(self.records),
            "opportunity": self.opportunity.model_dump(mode="json"),
        }


@dataclass
class Report:
    """没有生成机会的东西：缺口（资料或契约表达不了）、不支持（词表外）、不适用（例如只给儿童的活动），各带原因与数量。"""

    entries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, domain: str, kind: str, key: str, reason_zh: str, count: int = 1) -> None:
        for e in self.entries:
            if (e["domain"], e["kind"], e["key"], e["reason_zh"]) == (domain, kind, key, reason_zh):
                e["count"] += count
                return
        self.entries.append(
            {"domain": domain, "kind": kind, "key": key, "reason_zh": reason_zh, "count": count}
        )


# --------------------------------------------------------------------------- 精确换算


def dec(text: str | int | float) -> Fraction:
    """十进制文字 → 精确分数（不经过二进制浮点）。"""
    return Fraction(Decimal(str(text)))


def round_to(q: Fraction, places: int) -> float:
    """四舍五入（半数进位）到 ``places`` 位小数，返回最短十进制写法的浮点数（契约按十进制读）。"""
    d = (Decimal(q.numerator) / Decimal(q.denominator)).quantize(
        Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP
    )
    return float(d)


def cents(q: Fraction) -> float:
    return round_to(q, 2)


def money(q: Fraction, numeraire: str) -> Money:
    return Money(amount=cents(q), numeraire=numeraire)


def quote(q: Fraction, numeraire: str, billing: str, per_unit: str) -> PriceQuote:
    return PriceQuote(amount=money(q, numeraire), billing=billing, per_unit=per_unit)  # type: ignore[arg-type]


def hourly_wage(wage: dict[str, Any], hours_per_year: int) -> Fraction | None:
    """OEWS 都会区中位年薪 ÷ 2,080（按小时计酬职业的年薪 = 时薪 × 2,080）。只有年薪口径的职业没有时薪。"""
    if wage.get("annual_wage_median") is None or wage.get("wage_basis") != "annual_and_hourly":
        return None
    return dec(wage["annual_wage_median"]) / hours_per_year


@dataclass(frozen=True)
class Ctx:
    """一座城的编译上下文。"""

    cbsa: str
    key: str
    label: str
    city: str
    cfg: dict[str, Any]
    metro: dict[str, Any]
    slice: dict[str, Any]

    @property
    def numeraire(self) -> str:
        return self.cfg["numeraire"]

    @property
    def scope(self) -> str:
        return f"CBSA2023 {self.cbsa} {self.label}"

    def estimated(self, observed_on: str, inputs: list[str], rule: str) -> SourceRecord:
        return SourceRecord(
            kind="estimated",
            observed_on=_dt.date.fromisoformat(observed_on),
            geography_scope=self.scope,
            mapping_version=self.cfg["mapping_version"],
            inputs=tuple(x[:200] for x in inputs),
            rule=rule[:400],
        )

    def derived(self, observed_on: str, inputs: list[str], rule: str) -> SourceRecord:
        return SourceRecord(
            kind="derived",
            observed_on=_dt.date.fromisoformat(observed_on),
            geography_scope=self.scope,
            mapping_version=self.cfg["mapping_version"],
            inputs=tuple(x[:200] for x in inputs),
            rule=rule[:400],
        )

    def synthetic(self, note: str, inputs: list[str], rule: str) -> SourceRecord:
        return SourceRecord(
            kind="synthetic",
            observed_on=_dt.date.fromisoformat(str(self.cfg["assumptions_written_on"])),
            geography_scope=self.scope,
            mapping_version=self.cfg["mapping_version"],
            synthetic_note=note[:400],
            inputs=tuple(x[:200] for x in inputs),
            rule=rule[:400],
        )

    def goods_factor(self) -> Fraction:
        f = self.metro["food"]["goods_factor"]
        if f is None:
            raise ValueError(f"{self.cbsa}: 冻结库里没有商品类空间换算系数")
        return dec(f)

    def rpp_all(self) -> Fraction:
        return dec(self.metro["rpp"]["rpp_all"]) / 100

    def wage(self, soc: str) -> dict[str, Any] | None:
        return self.metro["wages"].get(soc)


def contexts(cfg: dict[str, Any], sl: dict[str, Any]) -> list[Ctx]:
    return [
        Ctx(m["cbsa"], m["key"], m["label"], m["city"], cfg, sl["metros"][m["cbsa"]], sl)
        for m in cfg["metros"]
        if m["cbsa"] in sl["metros"]
    ]


# --------------------------------------------------------------------------- 食品


_GRAMS = re.compile(r"\(([0-9.]+) gm\)")


def item_grams(code: str, unit_text: str, spec: dict[str, Any], fdc: dict[str, Any]) -> Fraction:
    """一个计价单位是多少克：单位原文写了克就用原文；否则用 SR Legacy 的份量克重 × 个数（“per doz.” = 12 个大号蛋）。"""
    per = spec.get("per_unit")
    if per is not None:
        grams = fdc[spec["fdc_id"]]["portions"].get(per["portion"])
        if grams is None:
            raise ValueError(f"{code}: SR Legacy 条目 {spec['fdc_id']} 没有份量“{per['portion']}”")
        return dec(grams) * per["count"]
    m = _GRAMS.search(unit_text)
    if m is None:
        raise ValueError(f"{code}: 计价单位“{unit_text}”没有写克数，配置也没有给份量")
    return dec(m.group(1))


@dataclass(frozen=True)
class BasketWeek:
    cost: Fraction
    kcal: Fraction
    records: tuple[str, ...]


def basket_week(ctx: Ctx, basket: list[dict[str, Any]]) -> BasketWeek:
    """一个每周篮子在这座城的花费（intake 食材估计价）与热量（购买重量 × SR Legacy 能量）。"""
    food, energy, fdc = ctx.metro["food"]["items"], ctx.cfg["food"]["item_energy"], ctx.slice["fdc"]
    cost = kcal = Fraction(0)
    records: list[str] = []
    for line in basket:
        code, qty = str(line["item"]).upper(), dec(line["qty"])
        item = food.get(code)
        if item is None:
            raise ValueError(f"{ctx.cbsa}: 冻结库里没有食材 {code} 的估计价")
        cost += qty * dec(item["price"])
        records.append(item["opportunity_id"])
        spec = energy.get(code)
        if spec is None:
            raise ValueError(f"食材 {code} 没有登记能量对应（food.item_energy）")
        if not spec.get("zero_energy"):
            grams = item_grams(code, item["unit_text"], spec, fdc)
            kcal += qty * grams * dec(fdc[spec["fdc_id"]]["kcal_per_100g"]) / 100
    return BasketWeek(cost, kcal, tuple(records))


def complete_week_cost(b: BasketWeek, complement: BasketWeek, target_kcal: Fraction) -> Fraction:
    """按热量补齐（``food_energy_completion_v1``）：差额按补齐篮子每千卡的花费补上；够了不补、不缩。"""
    gap = max(Fraction(0), target_kcal - b.kcal)
    return b.cost + gap * complement.cost / complement.kcal


def away_meal_cost(ctx: Ctx, week_cost: Fraction) -> dict[str, Any] | None:
    """外食一顿的统一成本代理（``food_away_cost_proxy_v1``，共同探索假设，不是已验证的零售价）：食材 = 所用篮子补齐后每周花费 ÷ 21；
    厨房人工 = 每顿 0.25 小时 × 厨师（35-2014）中位时薪 × 人工费用倍率；场地及其他 = 每顿全国基准 2 美元 × 其他服务 RPP ÷ 100；
    外送再加每顿 0.2 小时配送人工（53-3033 中位时薪 × 人工费用倍率）。税费、配送费与小费按成本里没有单列：不另加，写明。"""
    ac, sc = ctx.cfg["food"]["away_cost"], ctx.cfg["services"]
    overhead = dec(sc["labor_overhead_multiplier"])
    cook_w, driver_w = ctx.wage(ac["cook_soc"]), ctx.wage(ac["delivery_soc"])
    cook = None if cook_w is None else hourly_wage(cook_w, sc["hours_per_year"])
    driver = None if driver_w is None else hourly_wage(driver_w, sc["hours_per_year"])
    other = ctx.metro["rpp"].get("rpp_other")
    if cook is None or driver is None or other is None:
        return None
    ingredients = week_cost / MEALS_PER_WEEK
    kitchen = dec(ac["kitchen_hours_per_meal"]) * cook * overhead
    venue = dec(ac["venue_other_per_meal_usd"]) * dec(other) / 100
    delivery = dec(ac["delivery_hours_per_meal"]) * driver * overhead
    return {
        "ingredients": ingredients,
        "kitchen": kitchen,
        "venue": venue,
        "delivery_extra": delivery,
        "dine": ingredients + kitchen + venue,
        "delivered": ingredients + kitchen + venue + delivery,
        "cook_hourly": cook,
        "driver_hourly": driver,
        "rpp_other": dec(other),
    }


def cuisine_evidence(ctx: Ctx) -> dict[str, int]:
    """完整菜系词表里每个外食菜系在本城 OSM restaurant 的家数（一家写了几个同组标签只算一家）。不再截取前几名；家数不够门槛只说明
    缺少供应证据，不删掉这个菜系（按共同可得假设提供，并标明）。"""
    dining = ctx.cfg["food"]["dining"]
    out = {}
    for key, tags in dining["osm_cuisine_tags"].items():
        out[key] = sum(
            s["count"] for s in ctx.metro["restaurants"]["tag_sets"] if set(s["tags"]) & set(tags)
        )
    return out


def _prep_hours(meals: int, minutes: int, shopping: Fraction) -> Fraction:
    return (Fraction(meals * minutes, 60) + shopping) * WEEKS_PER_MONTH


def food_plans(ctx: Ctx, baskets: dict[str, Any], report: Report) -> list[Item]:
    fc = ctx.cfg["food"]
    target = dec(fc["energy_reference_kcal_per_day"]) * 7
    shopping = dec(fc["shopping_hours_per_week"])
    weeks = {k: basket_week(ctx, v["items"]) for k, v in baskets.items()}
    complement = weeks[fc["complement_basket"]]
    fdc_ref = f"usda_fdc_sr_legacy:{ctx.slice['raw']['usda_fdc_sr_legacy'][:16]}"
    base_assumptions = (
        "food_basket_quantities_v1",
        "food_energy_completion_v1",
        "food_energy_reference_v1",
        "ap_item_energy_match_v1",
        "food_plan_composition_v1",
        "food_shopping_hours_v1",
        "home_style_cuisine_v1",
        "food_prep_burden_v1",
    )
    labels = fc["dining"]["cuisine_labels"]
    burden = fc["burden_per_hour"]
    out: list[Item] = []

    def build(
        oid: str,
        label: str,
        quality: str,
        parts: list[FoodPlanPart],
        monthly: Fraction,
        prep: Fraction,
        dining: Fraction,
        coverage: str,
        time_basis: str,
        inputs: list[str],
        rule: str,
        assumptions: tuple[str, ...],
        records: tuple[str, ...],
        attributes: dict[str, Any],
    ) -> Item:
        prep_h, dining_h = round_to(prep, 2), round_to(dining, 2)
        o = LifeOpportunity(
            opportunity_id=oid,
            category="food",
            label=label,
            unit="household-plan",
            quality=quality,
            functions=("food",),
            attributes=attributes,
            availability="available",
            price=quote(monthly, ctx.numeraire, "per_month", "plan-month"),
            duration=DurationSpec(kind="ongoing_service"),
            food_plan=FoodPlanFacts(
                covers_basic_month=True,
                coverage_basis=coverage,
                meals_per_week=MEALS_PER_WEEK,
                parts=tuple(parts),
                prep_hours_per_month=prep_h,
                dining_hours_per_month=dining_h,
                time_basis=time_basis,
            ),
            resource=ResourceProfile(
                basis="per_service_month",
                hours=round_to(dec(prep_h) + dec(dining_h), 2),
                burden_per_hour=burden,
                quantity_scales=False,
                note="买菜、做饭、收拾与外出就餐的合计；生病时照常要吃饭",
            ),
            source=ctx.estimated("2024-12-31", inputs, rule),
        )
        return Item("food", o, "executable", assumptions, records)

    coverage = "一个成年人一周 21 餐：篮子食材按热量补齐到每天 2,000 千卡的参照量（只说吃饱的量，不是营养方案；不含油盐调料）"
    evidence = cuisine_evidence(ctx)
    threshold = fc["dining"]["min_restaurants"]
    modes = {
        "dine_out": ("外食", lambda a: (a, Fraction(0))),
        "delivery": ("外送", lambda a: (Fraction(0), a)),
        "half": ("外食外送各半", lambda a: (a / 2, a / 2)),
    }
    for b_cfg in fc["plan_baskets"]:
        b = weeks[b_cfg["basket"]]
        week = complete_week_cost(b, complement, target)
        share = min(Fraction(1), b.kcal / target)
        fresh_share = Fraction(b_cfg["fresh_meals_per_week"], MEALS_PER_WEEK)
        away = away_meal_cost(ctx, week)
        for h in fc["home_meals_grid"]:
            away_meals = Fraction(MEALS_PER_WEEK - h)
            variants = (
                [(None, None)]
                if away_meals == 0
                else [(m, c) for m in fc["away_modes"] for c in fc["dining"]["cuisine_order"]]
            )
            if away_meals > 0 and away is None:
                report.add(
                    "food",
                    "gap",
                    f"{b_cfg['key']}.h{h}",
                    "外食成本代理缺资料（厨师或配送工资、其他服务 RPP）：不生成含外食的方案",
                )
                continue
            for mode, cuisine in variants:
                dine, deliv = (
                    (Fraction(0), Fraction(0)) if mode is None else modes[mode][1](away_meals)
                )
                home_week = week * Fraction(h, MEALS_PER_WEEK)
                away_week = (
                    Fraction(0) if mode is None else dine * away["dine"] + deliv * away["delivered"]
                )
                monthly = (home_week + away_week) * WEEKS_PER_MONTH
                prep = _prep_hours(h, b_cfg["minutes_per_meal"], shopping if h > 0 else Fraction(0))
                prep += dec(fc["dining"]["delivery_hours_per_meal"]) * deliv * WEEKS_PER_MONTH
                dining_h = dec(fc["dining"]["dine_out_hours_per_meal"]) * dine * WEEKS_PER_MONTH
                parts = []
                if h > 0:
                    parts.append(
                        FoodPlanPart(
                            part_id="home",
                            mode="home_cooked",
                            meals_per_week=h,
                            fresh_meals_per_week=round_to(fresh_share * h, 2),
                            cuisine="home_style",
                            note="自己做的家常饭",
                        )
                    )
                name = None if cuisine is None else labels[cuisine]
                for part, mmode, n in (("out", "dine_out", dine), ("delivery", "delivery", deliv)):
                    if n > 0:
                        parts.append(
                            FoodPlanPart(
                                part_id=part,
                                mode=mmode,
                                meals_per_week=round_to(n, 2),
                                fresh_meals_per_week=round_to(fresh_share * n, 2),
                                cuisine=cuisine,
                                note=f"每周 {round_to(n, 2):g} 顿{name}{'外食' if mmode == 'dine_out' else '外送'}",
                            )
                        )
                seen = evidence.get(cuisine, 0) if cuisine else None
                assumed = cuisine is not None and (seen or 0) < threshold
                assumptions = (
                    *base_assumptions,
                    "food_home_share_by_meals_v1",
                    "food_fresh_by_basket_share_v1",
                    *(
                        (
                            "food_away_cost_proxy_v1",
                            "dining_hours_v1",
                            "cuisine_evidence_threshold_v2",
                            "oews_hourly_from_annual_v1",
                            "labor_overhead_v1",
                        )
                        if mode is not None
                        else ()
                    ),
                    *(("food_cuisine_common_availability_v1",) if assumed else ()),
                )
                if mode is None:
                    oid = f"{ctx.key}.food.{b_cfg['key']}.h21"
                    label = f"自己做·{b_cfg['label']}"
                    quality = b_cfg["quality"]
                else:
                    oid = f"{ctx.key}.food.{b_cfg['key']}.h{h:02d}.{mode}.{cuisine}"
                    label = f"自己做 {h} 顿·{b_cfg['label']} + {modes[mode][0]} {round_to(away_meals, 2):g} 顿（{name}）"
                    quality = (
                        f"每周 {h} 顿自己做（{b_cfg['label']}），另外 {round_to(away_meals, 2):g} 顿{name}{modes[mode][0]}；"
                        "外面的钱含在月费里，不另收；外食按统一成本代理估计，不是某家餐馆的报价"
                        + (
                            "；本城 OSM 里这个菜系的餐馆不到门槛，按共同可得假设提供"
                            if assumed
                            else ""
                        )
                    )
                attrs: dict[str, Any] = {
                    "basket": b_cfg["basket"],
                    "basket_energy_share": round_to(share, 4),
                    "home_meals_per_week": h,
                }
                rule = (
                    f"家里 = {b_cfg['basket']} 补齐后的一周花费 × {h}/21"
                    if mode is None
                    else (
                        f"家里 = {b_cfg['basket']} 补齐后的一周花费 × {h}/21；外面每顿 = 食材 {cents(away['ingredients'])} + 厨房 0.25 小时 × "
                        f"{cents(away['cook_hourly'])}/时 × 倍率 + 场地 2 × 其他服务 RPP {ctx.metro['rpp']['rpp_other']} ÷ 100"
                        + ("；外送再加 0.2 小时配送人工" if deliv else "")
                    )
                ) + "；合计 × 52 ÷ 12，四舍五入到分"
                if mode is not None:
                    attrs.update(
                        away_mode=mode,
                        away_meal_dine_cost=cents(away["dine"]),
                        away_meal_delivered_cost=cents(away["delivered"]),
                        osm_restaurants_with_cuisine=seen or 0,
                        cuisine_supply_evidence="osm_presence"
                        if not assumed
                        else "common_availability_assumed",
                        taxes_fees_tips="not_itemized",
                    )
                out.append(
                    build(
                        oid,
                        label[:120],
                        quality[:280],
                        parts,
                        monthly,
                        prep,
                        dining_h,
                        coverage,
                        (
                            f"每顿做饭与收拾约 {b_cfg['minutes_per_meal']} 分钟、有自己做时买菜每周 {fc['shopping_hours_per_week']} 小时；"
                            f"外食每顿连往返 {fc['dining']['dine_out_hours_per_meal']} 小时、外送每顿 {fc['dining']['delivery_hours_per_meal']} 小时；"
                            "× 52 ÷ 12（设定）"
                        )[:280],
                        [*dict.fromkeys((*b.records, *complement.records)), fdc_ref],
                        rule[:280],
                        assumptions,
                        tuple(dict.fromkeys((*b.records, *complement.records))),
                        attrs,
                    )
                )
    return out


def food_check(ctx: Ctx, baskets: dict[str, Any]) -> dict[str, Any]:
    """两个对照（只报告，不参与计算）：

    * 按 intake 同样的篮子与价格重算的篮子月费 vs intake 冻结库里的方案月费（应当完全相同）；
    * CE 本都会区平均每人每月的在家食品支出（2023–24 名义 × CPI 换到 2024 年；每户支出 ÷ 每户人数，不考虑家庭规模经济）——
      量级参照，不是方案价格。"""
    ce, f = ctx.metro["ce"], ctx.slice["cpi"]["ce_2023_2024_to_2024"]
    factor = Fraction(f["numerator"], f["denominator"])
    out: dict[str, Any] = {
        "baskets": {
            k: {
                "recomputed": cents(basket_week(ctx, v["items"]).cost * WEEKS_PER_MONTH),
                "intake": ctx.metro["food"]["intake_plans"].get(k),
            }
            for k, v in baskets.items()
        },
        "ce_food_at_home_per_person_month": cents(
            dec(ce["food_at_home_per_cu_year"]) * factor / dec(ce["persons_per_cu"]) / 12
        ),
        # 外食只做量级对照：CE 每户“在外饮食”年支出 × CPI ÷ 每户人数 ÷ 12（不再除以设定餐数去当单餐价）
        "ce_food_away_per_person_month": cents(
            dec(ce["food_away_per_cu_year"]) * factor / dec(ce["persons_per_cu"]) / 12
        ),
        "away_cost_proxy_per_meal": _away_proxy_summary(ctx, baskets),
        "ce_record": ce["record_id"],
    }
    return out


def _away_proxy_summary(ctx: Ctx, baskets: dict[str, Any]) -> dict[str, Any]:
    """每个方案篮子的外食成本代理（每顿堂食 / 外送，分项），供与 CE 量级对照；缺资料写 None。"""
    fc = ctx.cfg["food"]
    target = dec(fc["energy_reference_kcal_per_day"]) * 7
    weeks = {k: basket_week(ctx, v["items"]) for k, v in baskets.items()}
    complement = weeks[fc["complement_basket"]]
    out: dict[str, Any] = {}
    for b_cfg in fc["plan_baskets"]:
        away = away_meal_cost(ctx, complete_week_cost(weeks[b_cfg["basket"]], complement, target))
        out[b_cfg["key"]] = (
            None
            if away is None
            else {
                k: cents(away[k])
                for k in ("ingredients", "kitchen", "venue", "delivery_extra", "dine", "delivered")
            }
        )
    return out


# --------------------------------------------------------------------------- 人工服务与享受型服务


def services(ctx: Ctx, report: Report) -> list[Item]:
    sc = ctx.cfg["services"]
    overhead = dec(sc["labor_overhead_multiplier"])
    out: list[Item] = []
    for s in sc["chores"]:
        w = ctx.wage(s["soc"])
        hourly = None if w is None else hourly_wage(w, sc["hours_per_year"])
        if hourly is None:
            report.add(
                "service", "gap", s["service"], f"本城没有 {s['soc']} 的 OEWS 时薪，不能按工资定价"
            )
            continue
        assert w is not None
        price = hourly * dec(s["worker_hours_per_month"]) * overhead
        o = LifeOpportunity(
            opportunity_id=f"{ctx.key}.service.{s['service']}",
            category="service",
            label=s["label"],
            unit="service-month",
            quality=s["quality"],
            availability="available",
            price=quote(price, ctx.numeraire, "per_month", "service-month"),
            duration=DurationSpec(kind="ongoing_service"),
            resource=ResourceProfile(
                basis="per_service_month",
                hours=s["client_hours_per_month"],
                burden_per_hour=0,
                quantity_scales=False,
                substitutes=tuple(
                    TaskSubstitution(task=t, residual_hours_per_month=h)
                    for t, h in s["substitutes"].items()
                ),
                note="不占你自己的时间；只在实际付了钱的月份起作用",
            ),
            spec={"worker_hours_per_month": s["worker_hours_per_month"]},
            attributes={"wage_soc": s["soc"], "hourly_wage_median": cents(hourly)},
            source=ctx.estimated(
                "2024-05-31",
                [w["record_id"]],
                f"OEWS {s['soc']} 都会区中位年薪 {w['annual_wage_median']} ÷ 2,080 = 时薪；× 每月 {s['worker_hours_per_month']} 工人小时 "
                f"× 人工费用倍率 {sc['labor_overhead_multiplier']}，四舍五入到分",
            ),
        )
        out.append(
            Item(
                "service",
                o,
                "executable",
                ("oews_hourly_from_annual_v1", "labor_overhead_v1", "service_hours_v1"),
                (w["record_id"],),
            )
        )
    return out


# --------------------------------------------------------------------------- 设备与装备


# --------------------------------------------------------------------------- 日常支持与抽象消费篮子（E3B 映射修正 §5、§6）


def _raw_ref(ctx: Ctx, key: str) -> str:
    return f"{key}:{ctx.slice['raw'][key][:16]}"


def _support_item(
    ctx: Ctx,
    cfg: dict[str, Any],
    monthly: Fraction,
    *,
    attributes: dict[str, Any],
    inputs: list[str],
    rule: str,
    assumptions: tuple[str, ...],
) -> Item:
    o = LifeOpportunity(
        opportunity_id=f"{ctx.key}.service.{cfg['service']}",
        category="service",
        label=cfg["label"],
        unit="service-month",
        quality=cfg["quality"][:280],
        functions=(cfg["function"],),
        attributes=attributes,
        availability="available",
        price=quote(monthly, ctx.numeraire, "per_month", "service-month"),
        duration=DurationSpec(kind="ongoing_service"),
        resource=ResourceProfile(
            basis="per_service_month",
            hours=0,
            burden_per_hour=0,
            quantity_scales=False,
            note="不占你自己的时间；只在实际付了钱的月份提供这项功能，买两份不叠加",
        ),
        source=ctx.estimated("2024-12-31", inputs, rule),
    )
    return Item("daily_support", o, "executable", assumptions, ())


def daily_support(ctx: Ctx, report: Report) -> list[Item]:
    """三个支持功能各一个按月服务（见配置 daily_support）。费用顺序：同规格直接报价（现在没有）→ FCC 同规格大区参照（家庭宽带）
    → FCC 全国参照 → CE 一人消费单位预算代理；预算代理标明，不叫观测单价。"""
    ds, sl = ctx.cfg["daily_support"], ctx.slice["daily_support"]
    one, det = sl["ce"]["one_person"], sl["ce"]["detail_all_units"]
    ce_ref = [
        _raw_ref(ctx, "bls_ce_national_2024_size"),
        _raw_ref(ctx, "bls_ce_national_2024_detail"),
    ]
    common = ("daily_support_cost_order_v1", "basic_device_access_v1")
    out: list[Item] = []

    pc = ds["personal_communication"]
    annual, _ = allocated_annual(one, det, pc["line"])
    out.append(
        _support_item(
            ctx,
            pc,
            annual / 12,
            attributes={
                "cost_basis": "ce_one_person_budget_proxy",
                "observed_unit_price": False,
                "price_geography": "national",
                "ce_annual_one_person": cents(annual),
            },
            inputs=ce_ref,
            rule=(
                f"CE 2024 一人消费单位“{pc['line']['size_label']}”年均 {cents(annual)} ÷ 12，四舍五入到分；共同预算代理"
                "（全国一人消费单位的平均支出，不是单人套餐报价，各城相同，不做空间换算）"
            ),
            assumptions=(*common, "ce_one_person_budget_proxy_v1"),
        )
    )

    hc = ds["home_connectivity"]
    fcc, cpi = sl["fcc"], sl["cpi_internet"]["annual"]
    factor = dec(cpi[str(hc["cpi_to_year"])]) / dec(cpi[str(hc["cpi_from_year"])])
    region = hc["metro_region"][ctx.cbsa]
    reg, nat = fcc["by_region"].get(region) or {}, fcc["national"]
    if reg.get("weighted_median_total_charge") and reg["offers"] >= hc["min_offers"]:
        base, basis, geo, n = (
            dec(reg["weighted_median_total_charge"]),
            "fcc_regional_reference",
            f"census_region:{region}",
            reg["offers"],
        )
    elif nat.get("weighted_median_total_charge") and nat["offers"] >= hc["min_offers"]:
        base, basis, geo, n = (
            dec(nat["weighted_median_total_charge"]),
            "fcc_national_reference",
            "national",
            nat["offers"],
        )
        report.add(
            "daily_support",
            "gap",
            "home_connectivity",
            f"{region} 区同规格报价不到 {hc['min_offers']} 条：用全国参照",
        )
    else:
        base = None
    spec = hc["fcc_spec"]
    if base is not None:
        monthly = base * factor
        attrs: dict[str, Any] = {
            "cost_basis": basis,
            "observed_unit_price": False,
            "price_geography": geo,
            "fcc_offers": n,
            "fcc_weighted_median_2023": cents(base),
            "taxes": "not_included",
            "download_mbps": f"{spec['download_min_mbps']}–{spec['download_below_mbps'] - 1}",
            "upload_min_mbps": spec["upload_min_mbps"],
            "usage_allowance": spec["usage"],
        }
        inputs = [
            _raw_ref(ctx, "fcc_urs_2024_broadband"),
            _raw_ref(ctx, "bls_cpi_u_education_communication"),
        ]
        rule = (
            f"FCC 2024 城市费率调查：{geo} 同规格报价（下行 {attrs['download_mbps']} Mbps、上行 ≥ {spec['upload_min_mbps']}、不限流量）"
            f"{n} 条按调查权重的中位数总月费 {cents(base)}（2023 年调查价，不含税）× CPI {hc['cpi_series']} "
            f"{cpi[str(hc['cpi_to_year'])]} ÷ {cpi[str(hc['cpi_from_year'])]}，四舍五入到分；不是本城某家运营商的报价"
        )
        assumptions = (*common, "fcc_broadband_basic_tier_v1")
    else:
        annual, _ = allocated_annual(one, det, hc["ce_fallback_line"])
        monthly = annual / 12
        attrs = {
            "cost_basis": "ce_allocated_budget_proxy",
            "observed_unit_price": False,
            "price_geography": "national",
        }
        inputs, rule = (
            ce_ref,
            "FCC 资料不能用：CE 一人“其他家庭开支”× 全国详细表互联网服务占比 ÷ 12（分配估计、共同预算代理）",
        )
        assumptions = (*common, "ce_one_person_budget_proxy_v1")
        report.add(
            "daily_support",
            "gap",
            "home_connectivity",
            "FCC 同规格报价不足：用 CE 分配估计的预算代理",
        )
    out.append(
        _support_item(
            ctx, hc, monthly, attributes=attrs, inputs=inputs, rule=rule, assumptions=assumptions
        )
    )

    hs = ds["household_supplies"]
    parts = [(line["key"], *allocated_annual(one, det, line)) for line in hs["lines"]]
    annual = sum((a for _, a, _ in parts), Fraction(0))
    goods = dec(ctx.metro["rpp"]["rpp_goods"])
    out.append(
        _support_item(
            ctx,
            hs,
            annual / 12 * goods / 100,
            attributes={
                "cost_basis": "ce_one_person_budget_x_rpp_goods",
                "observed_unit_price": False,
                "price_geography": "metro_estimate",
                "ce_annual_one_person": cents(annual),
                "allocated_share_estimate": any(b != "one_person_table" for _, _, b in parts),
            },
            inputs=[*ce_ref, f"rpp_goods {ctx.metro['rpp']['rpp_goods']}"],
            rule=(
                "CE 2024 一人消费单位："
                + "、".join(
                    f"{k} {cents(a)}{'（按全国详细表占比分配）' if b != 'one_person_table' else ''}"
                    for k, a, b in parts
                )
                + f"，合计 ÷ 12 × 商品类 RPP {ctx.metro['rpp']['rpp_goods']} ÷ 100，四舍五入到分（估计，不是本地报价）"
            ),
            assumptions=(*common, "household_supplies_ce_v1"),
        )
    )
    return out


def basket_vector(ctx: Ctx) -> list[dict[str, Any]]:
    """共同参考支出向量 e_j（每月，全国 2024 美元）与本城价格系数：每条白名单条目一行。"""
    cb, ce = ctx.cfg["consumption_basket"], ctx.slice["daily_support"]["ce"]
    rpp = ctx.metro["rpp"]
    index = {"goods": dec(rpp["rpp_goods"]) / 100, "other": dec(rpp["rpp_other"]) / 100}
    out = []
    for line in cb["lines"]:
        annual, basis = allocated_annual(ce["one_person"], ce["detail_all_units"], line)
        out.append(
            {
                "key": line["key"],
                "monthly": annual / 12,
                "basis": basis,
                "price_index": line["price_index"],
                "factor": index[line["price_index"]],
            }
        )
    return out


def consumption_basket(ctx: Ctx, report: Report) -> list[Item]:
    """抽象个人消费篮子（配置 consumption_basket）：每个档位 u 一个按月服务，费用 u × P_c，享受功能 personal_consumption 每月 u 份。"""
    cb = ctx.cfg["consumption_basket"]
    vec = basket_vector(ctx)
    p_c = sum((x["monthly"] * x["factor"] for x in vec), Fraction(0))
    rpp = ctx.metro["rpp"]
    rule = (
        "P_c = Σ e_j × 价格系数 = "
        + " + ".join(
            f"{x['key']} {cents(x['monthly'])} × {'商品' if x['price_index'] == 'goods' else '其他服务'}"
            for x in vec
        )
        + f"（商品类 RPP {rpp['rpp_goods']}、其他服务 RPP {rpp['rpp_other']}，÷ 100）= {cents(p_c)}；费用 = u × P_c，四舍五入到分"
    )
    out: list[Item] = []
    for u_text in cb["grid"]:
        u = dec(u_text)
        o = LifeOpportunity(
            opportunity_id=f"{ctx.key}.service.{cb['service']}.u{u_text.replace('.', '_')}",
            category="service",
            label=f"{cb['label']}（每月 {u_text} 个标准篮子）",
            unit="service-month",
            quality=cb["quality"][:280],
            attributes={
                "standard_baskets_per_month": float(u),
                "standard_basket_price": cents(p_c),
                "cost_basis": "ce_one_person_vector_x_rpp",
                "observed_unit_price": False,
                "allocated_share_estimate": any(x["basis"] != "one_person_table" for x in vec),
            },
            availability="available",
            price=quote(u * p_c, ctx.numeraire, "per_month", "service-month"),
            duration=DurationSpec(kind="ongoing_service"),
            enjoyment=(
                EnjoymentProvision(
                    function=cb["function"],
                    mode="service_units",
                    units_per_month=float(u),
                    unit_label="标准篮子",
                    basis=f"这个月实际获得 {u_text} 个标准篮子的衣着与个人护理消费（消费服务流，不是可转卖的资产）",
                ),
            ),
            resource=ResourceProfile(
                basis="per_service_month",
                hours=0,
                burden_per_hour=0,
                quantity_scales=False,
                note="当月的消费服务流：不占时间、不加休闲参与小时、不留下资产或装备能力；买不起就是 0",
            ),
            source=ctx.estimated(
                "2024-12-31",
                [
                    _raw_ref(ctx, "bls_ce_national_2024_size"),
                    _raw_ref(ctx, "bls_ce_national_2024_detail"),
                    f"rpp {rpp['rpp_goods']}/{rpp['rpp_other']}",
                ],
                rule,
            ),
        )
        out.append(Item("consumption", o, "executable", ("consumption_basket_ce_v1",), ()))
    return out


def representative_model(
    models: list[dict[str, Any]], category: str, type_: str
) -> dict[str, Any] | None:
    """一个（类别 × 类型）的代表型号：年耗电的（下）中位数；并列取记录编号最小的。没写耗电的型号不参加。"""
    pool = sorted(
        (
            m
            for m in models
            if m["category"] == category
            and m["type"] == type_
            and m["annual_energy_kwh"] is not None
        ),
        key=lambda m: (dec(m["annual_energy_kwh"]), m["record_id"]),
    )
    return pool[(len(pool) - 1) // 2] if pool else None


def failure_spec(ctx: Ctx, rules: list[dict[str, Any]]) -> FailureSpec:
    """一件持续物品的故障规则（引擎的 ``LifeOpportunity.failure``）。成套设备：部件互相独立、任一部件坏就整套不能用，
    年故障概率 = 1 − Π(1 − p)（四舍五入到 0.0001）；所有部件都是 ``none_within_horizon`` 时整套也是。"""
    hazards = [dec(r["annual_hazard"]) for r in rules if r["rule"] == "annual_hazard"]
    note = "；".join(dict.fromkeys(r["basis"] for r in rules))[:280]
    source = ctx.synthetic(
        "故障规则是登记的设定（device_failure_v1）：没有公开的型号或类别故障率资料，保修条款不是故障分布",
        [],
        "成套设备部件独立、任一部件坏就整套不能用：1 − Π(1 − p)；全部部件 3 年内不建模随机故障时写 none_within_horizon",
    )
    if not hazards:
        return FailureSpec(kind="none_within_horizon", note=note, source=source)
    survive = Fraction(1)
    for p in hazards:
        survive *= 1 - p
    return FailureSpec(
        kind="annual_hazard", annual_probability=round_to(1 - survive, 4), note=note, source=source
    )


def devices(ctx: Ctx, report: Report) -> list[Item]:
    dc = ctx.cfg["devices"]
    factor = ctx.goods_factor()
    out: list[Item] = []
    for offer in dc["offers"]:
        models = [
            representative_model(ctx.slice["appliances"], p["category"], p["type"])
            for p in offer["parts"]
        ]
        if any(m is None for m in models):
            report.add("device", "gap", offer["offer"], "ENERGY STAR 里没有这一类型的美国市场型号")
            continue
        price = dec(offer["price_usd_2024_national"]) * factor
        spec: dict[str, Any] = {}
        for m in models:
            assert m is not None
            spec[f"{m['category']}_brand"] = m["brand"] or "unknown"
            spec[f"{m['category']}_model"] = m["model_number"] or "unknown"
            spec[f"{m['category']}_annual_kwh"] = float(dec(m["annual_energy_kwh"]))
        records = tuple(m["record_id"] for m in models if m is not None)
        o = LifeOpportunity(
            opportunity_id=f"{ctx.key}.durable.{offer['offer']}",
            category="durable",
            label=offer["label"],
            unit="appliance-set" if len(models) > 1 else "appliance",
            quality=f"{offer['quality']}；一次买下、按设定能用 {offer['lifetime_years']} 年",
            spec=spec,
            attributes={
                "installation": "assumed_installable",
                "price_basis": "category_assumption",
            },
            availability="available",
            price=quote(
                price,
                ctx.numeraire,
                "one_time",
                "appliance-set" if len(models) > 1 else "appliance",
            ),
            duration=DurationSpec(kind="durable_good", lasts_years=offer["lifetime_years"]),
            failure=failure_spec(ctx, [dc["failure"][p["category"]] for p in offer["parts"]]),
            resource=ResourceProfile(
                basis="passive",
                substitutes=tuple(
                    TaskSubstitution(task=t, residual_hours_per_month=h)
                    for t, h in offer["substitutes"].items()
                ),
                note="持有本身不占时间；能用的期间按残余小时替代洗衣家务",
            ),
            source=ctx.synthetic(
                "价格、寿命与替代后残余是登记的类别设定（device_category_v1），不是型号报价；型号来自 ENERGY STAR 记录",
                [
                    *records,
                    f"goods_factor {ctx.metro['food']['goods_factor']}（intake rpp_goods_spatial_adjustment@1）",
                ],
                f"全国类别设定 {offer['price_usd_2024_national']} × 商品类空间换算系数，四舍五入到分",
            ),
        )
        out.append(
            Item(
                "device",
                o,
                "executable",
                ("device_category_v1", "device_failure_v1", "device_installable_v1"),
                records,
                ("installation_condition_not_in_housing_data",),
            )
        )
    for key, reason in dc["not_offered"].items():
        report.add("device", "gap", key, reason)
    return out


def device_failure(cfg: dict[str, Any]) -> dict[str, Any]:
    """每个设备类别的故障设定与用到它的设备方案（机会本身的 ``failure`` 由它生成；这里汇总一份便于审阅）。"""
    dc = cfg["devices"]
    out = {}
    for cat, f in dc["failure"].items():
        out[cat] = {
            **f,
            "assumption": "device_failure_v1",
            "offers": [
                o["offer"] for o in dc["offers"] if any(p["category"] == cat for p in o["parts"])
            ],
        }
    for e in cfg["equipment"]:
        out[e["item"]] = {**e["failure"], "assumption": "device_failure_v1", "offers": [e["item"]]}
    return out


def equipment(ctx: Ctx) -> list[Item]:
    factor = ctx.goods_factor()
    out = []
    for e in ctx.cfg["equipment"]:
        price = dec(e["price_usd_2024_national"]) * factor
        o = LifeOpportunity(
            opportunity_id=f"{ctx.key}.durable.{e['item']}",
            category="durable",
            label=e["label"],
            unit="item",
            quality=e["quality"],
            availability="available",
            price=quote(price, ctx.numeraire, "one_time", "item"),
            duration=DurationSpec(kind="durable_good", lasts_years=e["lifetime_years"]),
            failure=failure_spec(ctx, [e["failure"]]),
            equipment=EquipmentFacts(provides=tuple(e["provides"]), note="买到的当月就能用"),
            resource=ResourceProfile(basis="passive", note="持有本身不占时间"),
            attributes={"price_basis": "category_assumption"},
            source=ctx.synthetic(
                "价格与寿命是登记的类别设定（equipment_category_v1），不是某个商品的报价",
                [
                    f"goods_factor {ctx.metro['food']['goods_factor']}（intake rpp_goods_spatial_adjustment@1）"
                ],
                f"全国类别设定 {e['price_usd_2024_national']} × 商品类空间换算系数，四舍五入到分",
            ),
        )
        out.append(
            Item("equipment", o, "executable", ("equipment_category_v1", "device_failure_v1"), ())
        )
    return out


# --------------------------------------------------------------------------- 活动：模型活动


def venue_count(ctx: Ctx, rule: dict[str, Any]) -> int:
    n = 0
    for g in ctx.metro["venues"]["groups"]:
        if rule.get("venue_types") and g["venue_type"] not in rule["venue_types"]:
            continue
        if rule.get("sports") and not set(g["sports"]) & set(rule["sports"]):
            continue
        if rule.get("named_only") and not g["named"]:
            continue
        if rule.get("access") and g["access"] not in rule["access"]:
            continue
        n += g["count"]
    return n


def template_activities(ctx: Ctx, report: Report) -> list[Item]:
    ac = ctx.cfg["activities"]
    overhead = dec(ctx.cfg["services"]["labor_overhead_multiplier"])
    hours_per_year = ctx.cfg["services"]["hours_per_year"]
    within = ctx.metro["venues"]["within"] or ctx.city
    out: list[Item] = []
    for t in ac["templates"]:
        evidence = None
        if t.get("venue"):
            rule = ac["venue_rules"][t["venue"]]
            n = venue_count(ctx, rule)
            if n < rule["min_count"]:
                report.add(
                    "activity",
                    "not_offered",
                    t["template"],
                    f"本城 OSM 里没有看到“{t['venue']}”场所，模型活动不提供",
                )
                continue
            evidence = f"OSM {t['venue']} {n} 处（{within} 市内）"
        p, oh = dec(t["participation_hours"]), dec(t["overhead_hours"])
        records: list[str] = []
        parts: dict[str, Fraction] = {}
        rule_text = []
        if t.get("soc"):
            w = ctx.wage(t["soc"])
            hourly = None if w is None else hourly_wage(w, hours_per_year)
            if hourly is None:
                report.add("activity", "gap", t["template"], f"本城没有 {t['soc']} 的 OEWS 时薪")
                continue
            assert w is not None
            records.append(w["record_id"])
            parts["labor"] = hourly * dec(t["instructor_hours"]) / t["participants"] * overhead
            rule_text.append(
                f"人工 = {t['soc']} 中位时薪 × {t['instructor_hours']} 小时 ÷ {t['participants']} 人 × {ctx.cfg['services']['labor_overhead_multiplier']}"
            )
        if t.get("venue_per_participant_hour"):
            parts["venue"] = dec(t["venue_per_participant_hour"]) * p * ctx.rpp_all()
            rule_text.append(
                f"场地 = {t['venue_per_participant_hour']}/人·时 × {t['participation_hours']} 小时 × RPP 总水平 ÷ 100"
            )
        for key in ("rental_usd_2024_national", "materials_usd_2024_national"):
            if t.get(key):
                parts["equipment"] = dec(t[key]) * ctx.goods_factor()
                rule_text.append(f"器材或材料 = {t[key]} × 商品类空间换算系数")
        price: PriceQuote | None
        if t.get("price_unknown"):
            availability, price, status = "unknown", None, "data_unknown"
        else:
            availability, status = "available", "executable"
            price = quote(sum(parts.values(), Fraction(0)), ctx.numeraire, "one_time", "session")
        spec: dict[str, Any] = {f"{k}_component": cents(v) for k, v in parts.items()}
        rule = "；".join(rule_text) + "；合计四舍五入到分" if rule_text else "自己进行，不收费"
        note = "老师小时、人数、场地与器材是登记的模型活动设定（activity_template_v1），不是某个场所的真实报价"
        inputs = [
            *records,
            f"rpp_all {ctx.metro['rpp']['rpp_all']}",
            f"goods_factor {ctx.metro['food']['goods_factor']}",
        ]
        if t.get("price_unknown"):
            rule = "门票资料里没有：价格未知，不当零价"
        source = (
            ctx.estimated("2024-05-31", inputs, rule)
            if records
            else ctx.synthetic(note, inputs, rule)
        )
        o = LifeOpportunity(
            opportunity_id=f"{ctx.key}.experience.{t['template']}",
            category="experience",
            label=t["label"],
            unit="session",
            quality=t["quality"] + "（模型方案）",
            spec=spec,
            attributes={"model_plan": True, **({"venue_evidence": evidence} if evidence else {})},
            availability=availability,  # type: ignore[arg-type]
            price=price,
            duration=DurationSpec(kind="one_time_experience", lasts_years=0),
            activity=ActivityFacts(
                primary_category=t["category"],
                participation_hours=float(p),
                overhead_hours=float(oh),
                requires=tuple(t.get("requires", ())),
                kit_provides=tuple(t.get("kit_provides", ())),
                replaces_meals_per_session=0,
                time_basis=f"参与 {t['participation_hours']} 小时 + 往返与准备 {t['overhead_hours']} 小时（模型设定）",
            ),
            resource=ResourceProfile(
                basis="per_occurrence",
                hours=float(p + oh),
                burden_per_hour=ac["burden_per_hour"],
                quantity_scales=True,
                note=f"一次 {float(p + oh):g} 小时",
            ),
            source=source,
        )
        out.append(
            Item(
                "activity",
                o,
                status,
                (
                    "activity_template_v1",
                    ac["burden_assumption"],
                    *(("oews_hourly_from_annual_v1", "labor_overhead_v1") if records else ()),
                ),
                tuple(records),
                ("admission_price_not_in_data",) if t.get("price_unknown") else (),
            )
        )
    return out


def block_evidence(ctx: Ctx) -> dict[str, dict[str, Any]]:
    """每个 A 板块在这座城实际用到的证据值（``a_blocks`` 的 city_evidence）：时薪写到分，没有资料的写 None（未知，不当 0）。"""
    sc, fc, ac = ctx.cfg["services"], ctx.cfg["food"], ctx.cfg["activities"]

    def hourly(soc: str) -> str | None:
        w = ctx.wage(soc)
        h = None if w is None else hourly_wage(w, sc["hours_per_year"])
        return None if h is None else f"{cents(h):.2f}"

    rpp, goods = ctx.metro["rpp"], ctx.metro["food"]["goods_factor"]
    return {
        "food": {
            "basket_item_prices": len(ctx.metro["food"]["items"]),
            "cook_hourly_median": hourly(fc["away_cost"]["cook_soc"]),
            "delivery_driver_hourly_median": hourly(fc["away_cost"]["delivery_soc"]),
            "rpp_other": rpp.get("rpp_other"),
            "osm_restaurants_by_cuisine": cuisine_evidence(ctx),
        },
        "services": {"hourly_median_by_soc": {s["soc"]: hourly(s["soc"]) for s in sc["chores"]}},
        "daily_support": {
            "census_region": ctx.cfg["daily_support"]["home_connectivity"]["metro_region"][
                ctx.cbsa
            ],
            "rpp_goods": rpp.get("rpp_goods"),
            "acs_internet_access_description": ctx.metro.get("internet_access"),
        },
        "consumption_basket": {
            "rpp_goods": rpp.get("rpp_goods"),
            "rpp_other": rpp.get("rpp_other"),
            "standard_basket_price": f"{cents(sum((x['monthly'] * x['factor'] for x in basket_vector(ctx)), Fraction(0))):.2f}",
        },
        "devices": {"goods_factor": goods},
        "equipment": {"goods_factor": goods},
        "activities": {
            "rpp_all": rpp.get("rpp_all"),
            "goods_factor": goods,
            "hourly_median_by_soc": {
                x["soc"]: hourly(x["soc"]) for x in ac["templates"] if x.get("soc")
            },
            "venue_counts": {k: venue_count(ctx, r) for k, r in ac["venue_rules"].items()},
        },
    }


def activity_dominance(items: list[Item]) -> list[dict[str, Any]]:
    """付费活动被免费活动支配的情况（只报告，不为付费方案新增奖励）。休闲评分只读参与小时与兴趣匹配，所以同一类别里，免费方案的
    装备要求不多于付费方案、每参与一小时花的总时间不多于付费方案时，付费方案在评分口径下被支配。"""

    def facts(i: Item) -> tuple[str, float, float, frozenset[str]] | None:
        o = i.opportunity
        if o.activity is None or o.price is None or i.status != "executable":
            return None
        a = o.activity
        if a.participation_hours <= 0:
            return None
        per_hour = (a.participation_hours + a.overhead_hours) / a.participation_hours
        return a.primary_category, o.price.amount.amount, per_hour, frozenset(a.requires)

    rows = [(i, f) for i in items if (f := facts(i)) is not None]
    out = []
    for paid, (cat, price, per_hour, needs) in rows:
        if price <= 0:
            continue
        for free, (cat2, price2, per_hour2, needs2) in rows:
            if cat2 == cat and price2 == 0 and needs2 <= needs and per_hour2 <= per_hour:
                out.append(
                    {
                        "paid": paid.opportunity.opportunity_id,
                        "dominated_by": free.opportunity.opportunity_id,
                        "category": cat,
                        "reason_zh": (
                            f"同类别（{cat}）的免费方案不要更多装备，每参与一小时总共花 {per_hour2:g} 小时（付费方案 {per_hour:g} 小时）；"
                            "休闲评分只读参与小时与兴趣匹配，付费方案在评分口径下被支配"
                        ),
                    }
                )
    return sorted(out, key=lambda r: (r["paid"], r["dominated_by"]))


# --------------------------------------------------------------------------- 活动：芝加哥公园管理局的真实活动


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_DATE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"
_RANGE = re.compile(rf"From {_DATE} to {_DATE} Each ([A-Za-z,]+) from")
_EXCEPT = re.compile(rf"(?:{'|'.join(_WEEKDAYS)}), ([A-Z][a-z]+ \d{{1,2}}, \d{{4}})")


def _parse_date(text: str) -> _dt.date | None:
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return _dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def session_dates(notes: str | None) -> list[_dt.date] | None:
    """日期说明 → 每次上课的日期（起止之间、指定星期、去掉例外日期）。读不懂的写法返回 None，不猜。"""
    if not notes:
        return None
    m = _RANGE.search(notes)
    if m is None:
        return None
    start, end = _parse_date(m.group(1)), _parse_date(m.group(2))
    days = [d.strip() for d in m.group(3).split(",") if d.strip()]
    if (
        start is None
        or end is None
        or end < start
        or not days
        or any(d not in _WEEKDAYS for d in days)
    ):
        return None
    wanted = {_WEEKDAYS.index(d) for d in days}
    tail = notes[m.end() :]
    excluded = set()
    if "Except" in tail:
        listed = tail[tail.index("Except") :].removeprefix("Except the following dates:")
        # 例外名单必须整段读得懂：剩下任何文字（例如被截断的半个日期）都说明名单不完整，上课次数就不知道，不猜
        if _EXCEPT.sub("", listed).strip():
            return None
        for x in _EXCEPT.findall(listed):
            d = _parse_date(x)
            if d is None:
                return None
            excluded.add(d)
    out = []
    d = start
    while d <= end:
        if d.weekday() in wanted and d not in excluded:
            out.append(d)
        d += _dt.timedelta(days=1)
    return out or None


def _chicago_category(categories: dict[str, list[str]], raw: str | None) -> str | None:
    for key, names in categories.items():
        if raw in names:
            return key
    return None


def chicago_programs(ctx: Ctx, report: Report) -> list[Item]:
    cc = ctx.cfg["activities"]["chicago"]
    out: list[Item] = []
    adult = set(cc["adult_age_rules"])
    unknown_age = {x for x in cc["unknown_age_rules"] if x is not None}
    for prog in ctx.metro["programs"]:
        if prog.get("cancelled"):
            report.add("activity", "not_applicable", "chicago_cancelled", "资料标为已取消的活动")
            continue
        if prog.get("activity_type") in cc["excluded_types"]:
            report.add(
                "activity",
                "not_applicable",
                "chicago_type",
                f"活动类型“{prog['activity_type']}”不是个人可以报名参加的活动（或是营地、团队联赛）",
            )
            continue
        age = prog.get("age_range")
        if age not in adult and age not in unknown_age and age is not None:
            report.add(
                "activity",
                "not_applicable",
                "chicago_age",
                "只给儿童或青少年的活动（成年用户不适用）",
            )
            continue
        category = _chicago_category(cc["categories"], prog.get("category"))
        if category is None:
            report.add(
                "activity",
                "unsupported",
                "chicago_category",
                "活动类别不在现有休闲词表里（例如团队球类、俱乐部、游戏）",
            )
            continue
        hours = prog.get("session_hours")
        if hours is None or (dec(hours) * 100).denominator != 1:
            report.add(
                "activity",
                "gap",
                "chicago_session_hours",
                "日期说明里读不出每次的小时数（或细于 0.01 小时）",
            )
            continue
        dates = session_dates(prog.get("date_notes"))
        fee = prog.get("fee")
        gaps = ["calendar_window", "registration_bundle", "equipment_requirements_unknown"]
        restricted = age_eligibility(age)["age_restricted"]
        if restricted is not False:
            gaps.append("age_eligibility")
        if fee is None or dates is None:
            availability, price = "unknown", None
            report.add(
                "activity",
                "gap",
                "chicago_session_price",
                "报名费或上课次数读不出来，每次价格未知（不当零）",
            )
            rule = "报名费或上课次数缺失：每次价格未知"
        else:
            availability = "available"
            price = quote(dec(fee) / len(dates), ctx.numeraire, "one_time", "session")
            rule = f"每次价格 = 报名费 {fee}（{prog['fee_method']}）÷ 日期说明数出的 {len(dates)} 次，四舍五入到分；只能整期报名"
        travel = dec(cc["travel_hours_per_session"])
        attrs: dict[str, Any] = {
            "date_notes": prog.get("date_notes") or "",
            "start_date": prog["start"],
            "end_date": prog["end"],
            "days": prog.get("days") or "",
            "age_rule": age if age is not None else "unknown",
            "season": prog.get("season") or "",
            "facility": prog.get("facility") or "",
            "activity_type": prog.get("activity_type") or "",
            "chicago_category": prog.get("category") or "",
            "registration_fee": float(dec(fee)) if fee is not None else "unknown",
            "sessions_per_registration": len(dates) if dates else "unknown",
            "equipment_requirements": "unknown",
        }
        rid = prog["record_id"]
        o = LifeOpportunity(
            opportunity_id=f"{ctx.key}.experience.cpd.{rid.rsplit('.', 1)[1]}",
            category="experience",
            label=prog["label"][:120],
            unit="session",
            quality=(
                f"芝加哥公园管理局 {prog.get('facility') or ''}；{prog['start']} 至 {prog['end']}，每周 {prog.get('days') or '?'}，"
                f"每次 {hours} 小时；只能整期报名"
            )[:280],
            attributes=attrs,
            availability=availability,  # type: ignore[arg-type]
            price=price,
            duration=DurationSpec(kind="one_time_experience", lasts_years=0),
            activity=ActivityFacts(
                primary_category=category,
                participation_hours=float(dec(hours)),
                overhead_hours=float(travel),
                replaces_meals_per_session=0,
                time_basis=f"每次 {hours} 小时（资料的日期说明）+ 往返 {cc['travel_hours_per_session']} 小时（设定）",
            ),
            resource=ResourceProfile(
                basis="per_occurrence",
                hours=float(dec(hours) + travel),
                burden_per_hour=ctx.cfg["activities"]["burden_per_hour"],
                quantity_scales=True,
                note=f"一次 {float(dec(hours) + travel):g} 小时",
            ),
            source=ctx.derived(prog["start"], [rid, "bls_cpi_u CUUR0000SA0"], rule),
        )
        out.append(
            Item(
                "activity",
                o,
                "needs_e1_extension",
                (
                    "chicago_session_price_v1",
                    "activity_travel_hours_v1",
                    ctx.cfg["activities"]["burden_assumption"],
                ),
                (rid,),
                tuple(gaps),
            )
        )
    return out
