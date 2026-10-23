"""E3B 六城真实生活资料包的物品与活动（DECISIONS/0041、0042）：完整食品方案、杂务服务、设备、享受型服务、爱好装备与活动。

输入是 ``life_inputs`` 的切片与配置 ``configs/intake/life_items_us_v1.yaml``；输出是能通过 ``LifeOpportunity`` 契约的机会，
外加诊断（缺口、不支持、设备故障设定）。规则：

* 金额一律 ``USD_2024_LOCAL``，精确到分：先用分数精确计算，最后一步四舍五入到分（规则写进来源）；小时最多两位小数。
* 价格指数只乘在明确的基础报价或篮子上：食材篮子是 intake 的都会区估计价（已经过一次 RPP 商品类换算，这里不再乘）；外食是 CE
  都会区支出（当地名义值，只换时期、不做空间换算）；设备与装备是全国类别设定 × intake 的商品类空间换算系数；场地设定 × RPP 总水平。
* 未知不当零：门票没有资料就是 ``unknown``，日期、年龄与整期报名表达不了的真实活动标成需要 E1 扩展，原样保留限制，不删。
* 外食只经饮食生活评分（新鲜餐与菜系），不做营养声明。
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
        Ctx(m["cbsa"], m["label"], m["city"], cfg, sl["metros"][m["cbsa"]], sl)
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


def away_meal_price(ctx: Ctx) -> Fraction:
    """平均每顿外食花费 = CE 每户在外饮食年支出 × CPI(2024) ÷ CPI(2023–24) ÷ 每户人数 ÷ 52 ÷ 每人每周外食餐数。"""
    ce, f = ctx.metro["ce"], ctx.slice["cpi"]["ce_2023_2024_to_2024"]
    factor = Fraction(f["numerator"], f["denominator"])
    per_week = dec(ce["food_away_per_cu_year"]) * factor / dec(ce["persons_per_cu"]) / 52
    return per_week / ctx.cfg["food"]["dining"]["meals_away_per_person_week"]


def city_cuisines(ctx: Ctx) -> list[tuple[str, int]]:
    """本城 OSM restaurant 里够门槛的菜系（家数从多到少，最多 ``max_cuisines_per_city`` 个）。一家写了几个同组标签只算一家。"""
    dining = ctx.cfg["food"]["dining"]
    counts = []
    for key, tags in dining["osm_cuisine_tags"].items():
        n = sum(
            s["count"] for s in ctx.metro["restaurants"]["tag_sets"] if set(s["tags"]) & set(tags)
        )
        if n >= dining["min_restaurants"]:
            counts.append((key, n))
    counts.sort(key=lambda kv: (-kv[1], kv[0]))
    return counts[: dining["max_cuisines_per_city"]]


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
    for p in fc["home_plans"]:
        b = weeks[p["basket"]]
        week = complete_week_cost(b, complement, target)
        prep = _prep_hours(MEALS_PER_WEEK, p["minutes_per_meal"], shopping)
        share = min(Fraction(1), b.kcal / target)
        out.append(
            build(
                f"food.{ctx.cbsa}.{p['plan']}",
                p["label"],
                p["quality"],
                [
                    FoodPlanPart(
                        part_id="home",
                        mode="home_cooked",
                        meals_per_week=MEALS_PER_WEEK,
                        fresh_meals_per_week=p["fresh_meals_per_week"],
                        cuisine="home_style",
                        note="自己做的家常饭",
                    )
                ],
                week * WEEKS_PER_MONTH,
                prep,
                Fraction(0),
                coverage,
                f"每顿做饭与收拾约 {p['minutes_per_meal']} 分钟、买菜每周 {fc['shopping_hours_per_week']} 小时，× 52 ÷ 12"
                "（设定；量级核对：BLS ATUS 2025 年平均，做饭的人每天 1.09 小时）",
                [*b.records, *complement.records, fdc_ref],
                f"篮子 {p['basket']} 每周花费 + 热量差额 × 补齐篮子每千卡花费，× 52 ÷ 12，四舍五入到分；"
                f"篮子自身热量占参照量 {round_to(share * 100, 1)}%",
                base_assumptions,
                tuple(dict.fromkeys((*b.records, *complement.records))),
                {"basket": p["basket"], "basket_energy_share": round_to(share, 4)},
            )
        )

    meal_price = away_meal_price(ctx)
    ce = ctx.metro["ce"]
    cuisines = city_cuisines(ctx)
    if not cuisines:
        report.add(
            "food",
            "gap",
            "dining_cuisines",
            "本城 OSM restaurant 里没有够门槛的菜系，不生成外食方案",
        )
    for p in fc["mixed_plans"]:
        b = weeks[p["home_basket"]]
        home_week = complete_week_cost(b, complement, target) * Fraction(
            p["home_meals"], MEALS_PER_WEEK
        )
        out_week = meal_price * p["out_meals"]
        monthly = (home_week + out_week) * WEEKS_PER_MONTH
        prep = _prep_hours(p["home_meals"], p["minutes_per_meal"], shopping)
        dining = Fraction(0)
        if p["out_mode"] == "delivery":
            prep += dec(fc["dining"]["delivery_hours_per_meal"]) * p["out_meals"] * WEEKS_PER_MONTH
            out_time = (
                f"外送每顿下单与收取 {fc['dining']['delivery_hours_per_meal']} 小时（算进准备时间）"
            )
        else:
            dining = dec(fc["dining"]["dine_out_hours_per_meal"]) * p["out_meals"] * WEEKS_PER_MONTH
            out_time = f"外食每顿连往返 {fc['dining']['dine_out_hours_per_meal']} 小时"
        for key, n in cuisines:
            name = labels[key]
            mode_zh = "外送" if p["out_mode"] == "delivery" else "外食"
            assumptions = (
                *base_assumptions,
                "food_home_share_by_meals_v1",
                "fafh_meals_per_person_week_v1",
                "dining_hours_v1",
                "cuisine_presence_threshold_v1",
                *(("fafh_delivery_same_average_v1",) if p["out_mode"] == "delivery" else ()),
            )
            out.append(
                build(
                    f"food.{ctx.cbsa}.{p['plan']}.{key}",
                    f"{p['label']}（{name}）",
                    f"每周 {p['home_meals']} 顿自己做（荤素搭配，其中 {p['home_fresh']} 顿新鲜），另外 {p['out_meals']} 顿{name}"
                    f"{mode_zh}；{mode_zh}的钱已含在月费里，不另收"
                    + ("；配送费与小费资料里没有，没有另加" if p["out_mode"] == "delivery" else ""),
                    [
                        FoodPlanPart(
                            part_id="home",
                            mode="home_cooked",
                            meals_per_week=p["home_meals"],
                            fresh_meals_per_week=p["home_fresh"],
                            cuisine="home_style",
                            note="自己做的家常饭",
                        ),
                        FoodPlanPart(
                            part_id="out",
                            mode=p["out_mode"],
                            meals_per_week=p["out_meals"],
                            fresh_meals_per_week=p["out_fresh"],
                            cuisine=key,
                            note=f"每周 {p['out_meals']} 顿{name}{mode_zh}",
                        ),
                    ],
                    monthly,
                    prep,
                    dining,
                    f"一个成年人一周 21 餐：自己做 {p['home_meals']} 顿（按热量补齐的篮子按餐数折算）+ {mode_zh} {p['out_meals']} 顿"
                    "（每顿按本都会区平均外食花费）；不是营养方案",
                    f"每顿做饭与收拾约 {p['minutes_per_meal']} 分钟、买菜每周 {fc['shopping_hours_per_week']} 小时；{out_time}；× 52 ÷ 12（设定）",
                    [
                        *b.records,
                        *complement.records,
                        fdc_ref,
                        ce["record_id"],
                        "bls_cpi_u CUUR0000SA0",
                    ],
                    f"家里部分 = {p['home_basket']} 补齐后的一周花费 × {p['home_meals']}/21；外面每顿 = CE 每户在外饮食 "
                    f"{ce['food_away_per_cu_year']}（2023–24 名义）× CPI 换到 2024 ÷ 每户 {ce['persons_per_cu']} 人 ÷ 52 ÷ 每人每周 "
                    f"{fc['dining']['meals_away_per_person_week']} 顿 = {cents(meal_price)}；合计 × 52 ÷ 12，四舍五入到分",
                    assumptions,
                    tuple(dict.fromkeys((*b.records, *complement.records, ce["record_id"]))),
                    {
                        "basket": p["home_basket"],
                        "away_meal_average": cents(meal_price),
                        "osm_restaurants_with_cuisine": n,
                        **(
                            {"delivery_fee": "unknown", "tip": "unknown"}
                            if p["out_mode"] == "delivery"
                            else {}
                        ),
                    },
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
        "ce_record": ce["record_id"],
    }
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
            opportunity_id=f"service.{ctx.cbsa}.{s['service']}",
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
    for s in sc["enjoyment"]:
        w = ctx.wage(s["soc"])
        hourly = None if w is None else hourly_wage(w, sc["hours_per_year"])
        if hourly is None:
            report.add(
                "enjoyment",
                "gap",
                s["service"],
                f"本城没有 {s['soc']} 的 OEWS 时薪，不能按工资定价",
            )
            continue
        assert w is not None
        n = s["sessions_per_month"]
        price = hourly * dec(s["worker_hours_per_session"]) * n * overhead
        o = LifeOpportunity(
            opportunity_id=f"service.{ctx.cbsa}.{s['service']}",
            category="service",
            label=s["label"],
            unit="service-month",
            quality=s["quality"],
            availability="available",
            price=quote(price, ctx.numeraire, "per_month", "service-month"),
            duration=DurationSpec(kind="ongoing_service"),
            enjoyment=(
                EnjoymentProvision(
                    function="personal_care",
                    mode="service_units",
                    units_per_month=n,
                    unit_label="次",
                    basis=f"每月实际接受 {n} 次按摩（享受型服务）",
                ),
            ),
            resource=ResourceProfile(
                basis="per_service_month",
                hours=round_to(dec(s["client_hours_per_session"]) * n, 2),
                burden_per_hour=s["burden_per_hour"],
                quantity_scales=False,
                note="按摩与往返的合计；生病暂停的月份不付款、不使用",
            ),
            spec={
                "sessions_per_month": n,
                "worker_hours_per_session": s["worker_hours_per_session"],
            },
            attributes={"wage_soc": s["soc"], "hourly_wage_median": cents(hourly)},
            source=ctx.estimated(
                "2024-05-31",
                [w["record_id"]],
                f"OEWS {s['soc']} 都会区中位年薪 {w['annual_wage_median']} ÷ 2,080 = 时薪；× 每次 {s['worker_hours_per_session']} 工人小时 "
                f"× 每月 {n} 次 × 人工费用倍率 {sc['labor_overhead_multiplier']}，四舍五入到分",
            ),
        )
        out.append(
            Item(
                "enjoyment",
                o,
                "executable",
                ("oews_hourly_from_annual_v1", "labor_overhead_v1", "service_hours_v1"),
                (w["record_id"],),
            )
        )
    return out


# --------------------------------------------------------------------------- 设备与装备


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
            opportunity_id=f"device.{ctx.cbsa}.{offer['offer']}",
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
    """每个设备类别的故障设定（纯数据，给主会话的引擎字段用）与用到它的设备方案。"""
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
    return out


def equipment(ctx: Ctx) -> list[Item]:
    factor = ctx.goods_factor()
    out = []
    for e in ctx.cfg["equipment"]:
        price = dec(e["price_usd_2024_national"]) * factor
        o = LifeOpportunity(
            opportunity_id=f"equipment.{ctx.cbsa}.{e['item']}",
            category="durable",
            label=e["label"],
            unit="item",
            quality=e["quality"],
            availability="available",
            price=quote(price, ctx.numeraire, "one_time", "item"),
            duration=DurationSpec(kind="durable_good", lasts_years=e["lifetime_years"]),
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
        out.append(Item("equipment", o, "executable", ("equipment_category_v1",), ()))
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
        rule = "；".join(rule_text) + "；合计四舍五入到分" if rule_text else "在家进行，不收费"
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
            opportunity_id=f"activity.{ctx.cbsa}.{t['template']}",
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
            opportunity_id=f"activity.{ctx.cbsa}.cpd.{rid.rsplit('.', 1)[1]}",
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
