"""产品结果投影（``ProductResult``，E3-A 模块 E）：E1 报告与生活事实的**白名单**投影。

* 数值与分母直接取自 E1-6B 的城市报告与 E1-6A 的单分身评价：N = C + E + M；完整者均值的分母是 C；模拟缺失范围的分母是 C + M；
  C = 0、全是 E、还没有生成报告时保持为空，不补 0、不擅自分类。
* 事件链取自 E1 核对过的环境事实（``ChainStep``），不是模型理由；硬要求单独报告已核验与未核验的人数，不扣分、不删人。
* 生活事实（实际支付、在用设备、实际服务月份、本人每月家务小时、病情）取自结算记录与资源账；不透传内部评分对象与参数，也不
  把模型的需求摘要或理由当成事实。
* 片区、通勤、用车权、设备故障与税（DECISIONS/0042）取自资源账里每月唯一的通勤记录与结算记录：住处所在片区与片区居民平时的
  往返分钟、每月实际的方式 / 往返分钟 / 到岗次数 / 通勤费、用车权有没有用上、坏了什么与替换结果、按哪份税规则预扣。不含故障
  抽签的随机数与月度风险。逐城的资料性质与预检查是同一份投影（``product.pack_facts``）。
* 不新增排序规则、最低完成比例或现实推荐结论；结果不回灌给仍在生活中的分身（生活已经结束，结果只给用户看）。
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from common.life_aggregation_contracts import (
    MISSING_REASON_TEXT,
    ArmReport,
    CityReport,
    EntryRecord,
)
from common.life_contracts import COMMUTE_MODE_LABEL, ExactAmount, Money
from common.life_feedback_contracts import LifeYearResult
from common.product_contracts import (
    ActivityLine,
    CarAccessFact,
    CityResult,
    CloneResult,
    CommuteMonths,
    CommuteTrip,
    ComparisonLine,
    ConfirmedProfile,
    ConfirmedRef,
    ConsumptionLine,
    ConvenienceDevice,
    ConvenienceService,
    DeviceFailureFact,
    ExactNumber,
    GoodsLine,
    HomeFact,
    HouseholdHours,
    MissingReasonLine,
    OptionCoverageLine,
    OptionInventoryCoverage,
    PlannerAndCost,
    PreflightRef,
    ProductMoney,
    ProductPreflight,
    ProductResult,
    PurchaseNotMade,
    RequirementLine,
    RequirementSummaryLine,
    SampleLabel,
    SupportLine,
    TaxFact,
    ValuationScopeNote,
    YearFacts,
    content_fingerprint,
)
from orchestration.life_runner import RunOutcome, RunWorld
from product.exact import canonical, display, money_display
from product.pack import DataPack
from product.pack_facts import TAX_STATUS_ZH, city_data_nature
from product.public_errors import clone_explanation

CATEGORY_TEXT = {"C": "完成且评价完整", "E": "确认的生活出局", "M": "模拟问题，无法完整评价"}
STAGE_TEXT = {
    "completed": "已完成",
    "paused": "批次暂停（程序或配置问题）",
    "halted": "批次停下（服务、预算或请求检查）",
    "interrupted": "在两次规划调用之间中断",
    "budget_exhausted": "全局调用上限用完",
    "running": "运行中",
}
READING_GUIDE = (
    "完整者平均 J 的分母是 C（完成且评价完整的分身），不是原计划人数；C = 0 时没有平均值，不显示为 0",
    "确认的生活出局（E）不进均值，也不进模拟缺失范围；出局与缺失的比例以原计划人数为分母",
    "模拟缺失范围的分母是 C + M：已知的月份照算，未知部分在 0 到 1 之间都保留。它不是置信区间，也不是现实幸福的概率",
    "L 是实际几年逐月满足感的平均；V 是从终态出发、在预先冻结的参考生活模板与治疗分支里的估值，不是对未来的预测，也不是全局最优；"
    "J 按月份权重合成两者",
    "资料缺失、规划没有完成、停止与程序问题是模拟问题（M），不写成“在这座城活不下去”",
    "减少日常杂务本身对应便利满足感；没有安排的空闲时间不额外加分",
    "事件链按核对过的环境记录写成，不是模型的理由，也不是因果证明；硬要求单独报告，不扣分、不删人",
    "分数是当前合成模型下、按你确认的陈述偏好计算的结果；小数末位的差异不构成可靠的排序",
)


def _ex(a: ExactAmount | None, digits: int = 4) -> ExactNumber | None:
    if a is None:
        return None
    q = a.fraction()
    return ExactNumber(exact=canonical(q), display=display(q, digits))


def _money(m: Money, digits: int = 2) -> ProductMoney:
    minor = int(Decimal(repr(m.amount)).scaleb(digits))
    return ProductMoney(
        amount_minor=minor,
        currency=m.numeraire.split("_")[0],
        numeraire_id=m.numeraire,
        display=money_display(minor, digits),
    )


def _label(world: RunWorld, city: str, oid: str) -> str:
    cat = world.bundle.catalog_for(city)
    o = cat.opportunity(oid) if cat is not None else None
    return o.label if o is not None else oid


def year_facts(world: RunWorld, city: str, result: LifeYearResult) -> YearFacts:
    """一年的生活事实（白名单）：现金、岗位与领薪月份、住房、食品、支出、设备在用月份、服务计划与实际月份、本人家务小时、病情。"""
    out = result.settlement.outcome
    assert out is not None
    cat = world.bundle.catalog_for(city)
    assert cat is not None
    substitutes = {
        o.opportunity_id
        for o in cat.opportunities
        if o.resource is not None and o.resource.substitutes
    }

    def labels(role: str) -> tuple[str, ...]:
        rows = []
        for it in out.items:
            if it.role == role and it.covered_months:
                months = _months_text(it.covered_months)
                rows.append(f"{_label(world, city, it.opportunity_id)}（{months}）")
        return tuple(rows)

    devices = tuple(
        ConvenienceDevice(
            opportunity_id=h.opportunity_id,
            label=_label(world, city, h.opportunity_id),
            months_in_service=tuple(h.covered_months),
        )
        for h in out.holdings_service
        if h.kind == "durable_good" and h.opportunity_id in substitutes
    )
    services = tuple(
        ConvenienceService(
            opportunity_id=it.opportunity_id,
            label=_label(world, city, it.opportunity_id),
            months_planned=tuple(it.requested_months),
            months_covered=tuple(it.covered_months),
            months_not_obtained=tuple(m for m in it.requested_months if m not in it.covered_months),
        )
        for it in out.items
        if it.role == "periodic_service" and it.opportunity_id in substitutes
    )
    household: list[HouseholdHours] = []
    if out.resources is not None:
        tasks = sorted(
            {
                c.task
                for m in out.resources.months
                for c in m.contributions
                if c.category == "household_task" and c.task
            }
        )
        for task in tasks:
            hours = []
            for m in out.resources.months:
                row = next(
                    (
                        c
                        for c in m.contributions
                        if c.category == "household_task" and c.task == task
                    ),
                    None,
                )
                hours.append(
                    "—" if row is None or m.resource_check != "complete" else row.hours.exact
                )
            household.append(HouseholdHours(task=task, monthly_hours=tuple(hours)))
    goods, not_made, activities = _life_extension_facts(world, city, out, substitutes)
    spent = sum((Decimal(repr(m.spent.amount)) for m in out.months), Decimal(0))
    numeraire = out.money_basis_id
    notes: list[str] = []
    if out.exit is not None:
        notes.append(f"第 {out.exit.month} 月付不起最低住房与食品而出局（环境记录）")
    if out.stop is not None:
        notes.append(
            f"第 {out.stop.month} 月停止（疾病之后应急安排不足或资料缺口；模拟的支持边界，不是生活出局）"
        )
    return YearFacts(
        year_index=result.year_index,
        status=result.settlement.status,
        months_lived=len(out.months),
        cash_start=_money(out.cash_start or Money(0.0, numeraire)),
        cash_end=None if out.cash_end is None else _money(out.cash_end),
        jobs=labels("job"),
        housing=labels("housing"),
        food=labels("food"),
        training=labels("training"),
        paid_total=_money(Money(float(spent), numeraire)),
        devices=devices,
        services=services,
        household=tuple(household),
        health_months=tuple(m.state for m in out.health.months) if out.health is not None else (),
        notes=tuple(notes),
        goods=goods,
        purchases_not_made=not_made,
        activities=activities,
        **_area_facts(world, city, out),
        **_support_facts(world, city, out),
    )


#: 日常支持三项功能的公开名称（E3B 映射修正 §5）
SUPPORT_LABEL_ZH = {
    "personal_communication": "个人通信",
    "home_connectivity": "居家联网",
    "household_supplies": "家用日耗品",
}
BASKET_FUNCTION = "personal_consumption"


def _support_facts(world: RunWorld, city: str, out: Any) -> dict[str, Any]:
    """日常支持与个人消费篮子（白名单，逐字段取自结算记录）：按月服务的计划月份与实际获得的月份；篮子按每月实际获得的份数。"""
    cat = world.bundle.catalog_for(city)
    assert cat is not None
    services = [it for it in out.items if it.role == "periodic_service"]
    support = []
    for f, label in SUPPORT_LABEL_ZH.items():
        mine = [
            it
            for it in services
            if (o := cat.opportunity(it.opportunity_id)) is not None and f in o.functions
        ]
        if not mine:
            continue
        planned = sorted({m for it in mine for m in it.requested_months})
        got = sorted({m for it in mine for m in it.covered_months})
        support.append(
            SupportLine(
                function=f,  # type: ignore[arg-type]
                label_zh=label,
                opportunity_ids=tuple(sorted({it.opportunity_id for it in mine})),
                months_planned=tuple(planned),
                months_obtained=tuple(got),
                months_not_obtained=tuple(m for m in planned if m not in got),
            )
        )
    baskets = []
    for it in services:
        o = cat.opportunity(it.opportunity_id)
        units = next(
            (
                e.units_per_month
                for e in (o.enjoyment if o else ())
                if e.function == BASKET_FUNCTION
            ),
            None,
        )
        if units is not None:
            baskets.append(
                (
                    it,
                    Fraction(Decimal(repr(units))) * Fraction(Decimal(repr(it.requested_quantity))),
                )
            )
    consumption = None
    if baskets:
        lived = {m.month for m in out.months}
        by_month = []
        for m in range(1, 13):
            if m not in lived:
                by_month.append("—")
                continue
            q = sum((u for it, u in baskets if m in it.covered_months), Fraction(0))
            by_month.append(
                str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"
            )
        planned = {m for it, _ in baskets for m in it.requested_months}
        got = {m for it, _ in baskets for m in it.covered_months}
        consumption = ConsumptionLine(
            opportunity_ids=tuple(sorted({it.opportunity_id for it, _ in baskets})),
            baskets_by_month=tuple(by_month),
            months_not_obtained=tuple(sorted(planned - got)),
        )
    return {"support": tuple(support), "consumption": consumption}


#: 没成交的一次性购买与没能进行的活动：固定的公开说明（不透传结算里的内部原因文字）。
_NOT_MADE_ZH = {
    "rejected": "没有买成：那个月付不起或计划不可执行；没有扣款，也没有得到这件东西",
    "cancelled_when_ill": "按年度应急安排，购买那个月生着病就取消：没有扣款，不顺延、不补购",
    "paused": "那个月生病暂停：没有购买",
    "not_reached": "那个月之前已经出局或停止：没有发生",
}
_NOT_DONE_ZH = "缺少需要的装备能力（家里没有在用的装备，这次也没有套餐提供）：没有进行，也没有收费"


def _life_extension_facts(
    world: RunWorld, city: str, out: Any, substitutes: set[str]
) -> tuple[tuple[GoodsLine, ...], tuple[PurchaseNotMade, ...], tuple[ActivityLine, ...]]:
    """生活扩展的三类事实（白名单，逐字段取自结算记录）：其他耐用品、没买成的购买、休闲活动的参与。"""
    purchase = {
        it.opportunity_id: it.requested_months[0]
        for it in out.items
        if it.role == "durable_good" and it.status == "executed" and it.requested_months
    }

    def bought(h: Any) -> int | None:
        # 今年买的那一件：服务从购买月开始（下一年才生效的还没有服务月）；年初就有、今年到期的旧的一件不是
        m = purchase.get(h.opportunity_id)
        first = h.covered_months[0] if h.covered_months else None
        return m if m is not None and first in (None, m) else None

    goods = tuple(
        GoodsLine(
            opportunity_id=h.opportunity_id,
            label=_label(world, city, h.opportunity_id),
            bought_month=bought(h),
            months_in_service=tuple(h.covered_months),
            expired=h.expired,
            months_left_after_year=h.remaining_months_after,
        )
        for h in out.holdings_service
        if h.kind == "durable_good" and h.opportunity_id not in substitutes
    )
    not_made = tuple(
        PurchaseNotMade(
            opportunity_id=it.opportunity_id,
            label=_label(world, city, it.opportunity_id),
            month=it.requested_months[0] if it.requested_months else None,
            status=it.status,
            status_zh=_NOT_MADE_ZH[it.status],
        )
        for it in out.items
        if it.role == "durable_good" and it.status in _NOT_MADE_ZH
    )
    by: dict[str, list[Any]] = {}
    for p in out.participation:
        by.setdefault(p.opportunity_id, []).append(p)
    activities = []
    for oid, rows in by.items():
        done = [p for p in rows if p.status == "completed"]
        failed = [p.month for p in rows if p.status == "failed"]
        activities.append(
            ActivityLine(
                opportunity_id=oid,
                label=_label(world, city, oid),
                months_done=tuple(p.month for p in done),
                sessions=canonical(Fraction(repr(sum(p.sessions for p in done)))),
                participation_hours=canonical(
                    Fraction(repr(sum(p.participation_hours for p in done)))
                ),
                months_paused=tuple(p.month for p in rows if p.status == "paused"),
                months_not_done=tuple(failed),
                not_done_zh=_NOT_DONE_ZH if failed else None,
            )
        )
    return goods, not_made, tuple(activities)


#: 通勤记录的状态（固定的公开说明）。
_COMMUTE_STATUS_ZH = {
    "computed": "按通勤关系表执行：实际住处片区到各个到岗岗位的就业片区，取可用方式里往返最少的一种",
    "no_onsite_work": "这个月没有需要到岗的工作：通勤为零",
    "unknown_no_housing": "出局当月没有住房：通勤依据缺失，记为未知（不是 0）",
}
#: 设备故障之后的替换结果（固定的公开说明）。
_REPLACEMENT_ZH = {
    "not_declared": "年度计划没有预先声明替换：坏了之后就没有这件东西了",
    "bought": "按年度计划预先声明的替换，在下个月按普通购买买到",
    "rejected": "预先声明了替换，但下个月买不起或计划不可执行：没有买成，不顺延",
    "next_year": "第 12 月坏的：替换留给下一年的计划",
    "not_reached": "替换那个月之前已经出局或停止：没有发生",
}


def _area_facts(world: RunWorld, city: str, out: Any) -> dict[str, Any]:
    """片区、通勤、用车权、设备故障与税（DECISIONS/0042），逐字段取自资源账的月度通勤记录与结算记录。"""
    cat = world.bundle.catalog_for(city)
    assert cat is not None
    areas = {a.area_id: a for a in cat.areas}

    def area_label(area_id: str | None) -> str | None:
        a = None if area_id is None else areas.get(area_id)
        return area_id if a is None else a.label

    lived: dict[str, list[int]] = {}
    groups: dict[tuple[Any, ...], tuple[Any, list[int]]] = {}
    for m in out.resources.months if out.resources is not None else ():
        if m.housing_opportunity_id is not None:
            lived.setdefault(m.housing_opportunity_id, []).append(m.month)
        c = m.commute
        if c is None or c.status == "not_reached":
            continue
        key = (
            c.status,
            c.home_area,
            tuple(
                (u.job_opportunity_id, u.work_area, u.mode, u.roundtrip_minutes, u.onsite_visits)
                for u in c.uses
            ),
            None if c.paid is None else _money(c.paid).amount_minor,
        )
        groups.setdefault(key, (c, []))[1].append(m.month)
    homes = []
    for oid, months in lived.items():
        o = cat.opportunity(oid)
        area = None if o is None or o.area is None else areas.get(o.area)
        homes.append(
            HomeFact(
                opportunity_id=oid,
                label=_label(world, city, oid),
                months=tuple(months),
                area_id=None if o is None else o.area,
                area_label=None if area is None else area.label,
                area_typical_roundtrip_minutes=None
                if area is None
                else area.typical_roundtrip_minutes,
            )
        )
    commute = tuple(
        CommuteMonths(
            months=tuple(months),
            status=c.status,
            status_zh=_COMMUTE_STATUS_ZH[c.status],
            home_area_id=c.home_area,
            home_area_label=area_label(c.home_area),
            trips=tuple(
                CommuteTrip(
                    job_opportunity_id=u.job_opportunity_id,
                    job_label=_label(world, city, u.job_opportunity_id),
                    work_area_id=u.work_area,
                    work_area_label=area_label(u.work_area) or u.work_area,
                    mode=u.mode,
                    mode_zh=COMMUTE_MODE_LABEL[u.mode],
                    roundtrip_minutes=u.roundtrip_minutes,
                    onsite_visits=u.onsite_visits,
                )
                for u in c.uses
            ),
            monthly_paid=None if c.paid is None else _money(c.paid),
        )
        for c, months in groups.values()
    )
    facts: dict[str, Any] = {"homes": tuple(homes), "commute": commute}
    # 用车权：开车这种方式要求的那项功能（DECISIONS/0042）；关系表里没有这种条件的城市（合成样例）不写
    drive = None if cat.commute is None else cat.commute.mode_rule("drive")
    if drive is not None and drive.requires_function is not None:
        providers = {
            o.opportunity_id for o in cat.opportunities if drive.requires_function in o.functions
        }
        lines = tuple(
            ConvenienceService(
                opportunity_id=it.opportunity_id,
                label=_label(world, city, it.opportunity_id),
                months_planned=tuple(it.requested_months),
                months_covered=tuple(it.covered_months),
                months_not_obtained=tuple(
                    m for m in it.requested_months if m not in it.covered_months
                ),
            )
            for it in out.items
            if it.opportunity_id in providers
        )
        bought = any(x.months_covered for x in lines)
        facts["car_access"] = CarAccessFact(
            status="bought" if bought else "not_bought",
            lines=lines,
            note_zh=(
                "开车通勤要这个月有在用的用车权（按月付费）：用上的月份里，到岗时在可用方式里取往返最少的一种"
                if bought
                else "今年没有用上用车权：这一年不能开车通勤，到岗只用通勤关系表里不需要用车权的方式"
            ),
        )
    facts["device_failures"] = tuple(
        DeviceFailureFact(
            opportunity_id=f.opportunity_id,
            label=_label(world, city, f.opportunity_id),
            month=f.month,
            units_failed=f.units_failed,
            replacement_id=f.replacement_id,
            replacement_label=None
            if f.replacement_id is None
            else _label(world, city, f.replacement_id),
            replacement_status=f.replacement_status,
            replacement_status_zh=_REPLACEMENT_ZH[f.replacement_status],
            replacement_month=f.replacement_month,
        )
        for f in out.device_failures
    )
    if out.tax_policy_id is not None and out.tax_status in TAX_STATUS_ZH:
        facts["tax"] = TaxFact(
            policy_id=out.tax_policy_id,
            status=out.tax_status,
            status_zh=TAX_STATUS_ZH[out.tax_status],
        )
    return facts


def _months_text(months: tuple[int, ...]) -> str:
    if not months:
        return "无"
    ranges: list[str] = []
    start = prev = months[0]
    for m in months[1:]:
        if m == prev + 1:
            prev = m
            continue
        ranges.append(f"{start}–{prev} 月" if start != prev else f"{start} 月")
        start = prev = m
    ranges.append(f"{start}–{prev} 月" if start != prev else f"{start} 月")
    return "、".join(ranges)


def _valuation_scope(world: RunWorld, city: str, lives: Any) -> tuple[ValuationScopeNote, ...]:
    """期末持有的装备：参考模板考虑了哪些用途、哪些没有（收尾 R8）。只读真实终态与评价规格；不改 V，也不把估值对象交出去。"""
    from econ.life_terminal_value import equipment_valuation_scope

    terminal = lives[-1].next_state if lives else None
    spec = world.specs.get("arm.main")
    catalog = world.bundle.catalog_for(city)
    if terminal is None or terminal.status != "terminal" or spec is None or catalog is None:
        return ()
    out: list[ValuationScopeNote] = []
    for s in equipment_valuation_scope(terminal, spec, catalog):
        label = _label(world, city, s.opportunity_id)
        if s.covered:
            out.append(
                ValuationScopeNote(
                    kind="equipment_use_valued",
                    opportunity_id=s.opportunity_id,
                    label=label,
                    capabilities=s.covered,
                    months_left=s.months_left,
                    text_zh=(
                        f"期末持有的「{label}」还能用 {s.months_left} 个月：期末参考生活按它的能力（{'、'.join(s.covered)}）"
                        "继续安排相应活动，剩余寿命内照用、不重复购买"
                    ),
                )
            )
        if s.uncovered:
            out.append(
                ValuationScopeNote(
                    kind="equipment_use_not_in_templates",
                    opportunity_id=s.opportunity_id,
                    label=label,
                    capabilities=s.uncovered,
                    months_left=s.months_left,
                    text_zh=(
                        f"期末持有的「{label}」还能用 {s.months_left} 个月：参考模板没有考虑它的用途（{'、'.join(s.uncovered)}），"
                        "期末估值没有计入这项用途——这是参考模板的范围限制，不是说这件装备没有价值"
                    ),
                )
            )
    return tuple(out)


def _clone(
    entry: EntryRecord,
    years: tuple[YearFacts, ...],
    run_id: str,
    scope: tuple[ValuationScopeNote, ...] = (),
) -> CloneResult:
    return CloneResult(
        clone_ref=entry.clone_id,
        city_ref=str(entry.city),
        repeat_index=entry.repeat_index,
        category=entry.category,
        category_zh=CATEGORY_TEXT[entry.category],
        missing_reason=entry.missing_reason,
        missing_reason_zh=None
        if entry.missing_reason is None
        else MISSING_REASON_TEXT[entry.missing_reason],
        # E3-A 收尾 F5：说明只由类别与缺失原因生成，不透传 entry.detail（那里可能有异常原文）
        explanation=clone_explanation(entry, run_id),
        l_actual=_ex(entry.l_actual),
        v_reference=_ex(entry.v_reference),
        j=_ex(entry.j),
        range_lower=_ex(entry.lower),
        range_upper=_ex(entry.upper),
        reference_plan=entry.selected_plan_id,
        event_chain=tuple(
            f"第 {s.year_index} 年第 {s.month} 月：{s.text}" for s in entry.event_chain
        ),
        hard_requirements=tuple(
            RequirementLine(
                need=h.need,
                summary=h.requirement_summary,
                months_satisfied=h.months_satisfied,
                months_not_satisfied=h.months_not_satisfied,
                months_unknown=h.months_unknown,
                months_not_lived=h.months_not_lived,
            )
            for h in entry.hard_requirements
        ),
        years=years,
        valuation_scope=scope,
    )


def _headline(cr: CityReport, label: str) -> str:
    head = f"{label}：原计划 {cr.planned} 个分身——{cr.complete} 个完成且可评价、{cr.exits} 个生活出局、{cr.missing} 个模拟问题"
    if cr.complete == 0:
        return head + "；没有可用于平均的完整结果"
    assert cr.mean_j is not None
    return (
        head
        + f"；{cr.complete} 个完成且可评价分身的平均 J 为 {display(cr.mean_j.fraction(), 4)}"
        + "（分母只数完成且可评价的分身，不是原计划的全部分身）"
    )


def _city(world: RunWorld, cr: CityReport) -> CityResult:
    cat = world.bundle.catalog_for(str(cr.city))
    label = cat.label if cat is not None else str(cr.city)
    notes = list(cr.notes)
    if cr.range_lower is None:
        notes.append("没有完整或可算范围的分身（例如全部生活出局）：模拟缺失范围为空")
    return CityResult(
        city_ref=str(cr.city),
        label=label,
        planned=cr.planned,
        complete=cr.complete,
        exits=cr.exits,
        missing=cr.missing,
        mean_j=_ex(cr.mean_j),
        mean_l=_ex(cr.mean_l),
        mean_v=_ex(cr.mean_v),
        mean_denominator=cr.complete,
        range_lower=_ex(cr.range_lower),
        range_upper=_ex(cr.range_upper),
        range_denominator=cr.range_denominator,
        exit_share=_ex(cr.exit_share),  # type: ignore[arg-type]
        missing_share=_ex(cr.missing_share),  # type: ignore[arg-type]
        missing_reasons=tuple(
            MissingReasonLine(
                reason=r.reason, reason_zh=MISSING_REASON_TEXT[r.reason], count=r.count
            )
            for r in cr.missing_reasons
        ),
        hard_requirements=tuple(
            RequirementSummaryLine(
                need=h.need,
                planned=h.planned,
                checked=h.checked,
                satisfied_every_month=h.satisfied_every_month,
                violated=h.violated,
                unknown=h.unknown,
                satisfied_in_lived_months=h.satisfied_in_lived_months,
                not_checked=h.not_checked,
            )
            for h in cr.hard_requirements
        ),
        headline_zh=_headline(cr, label),
        notes_zh=tuple(n[:300] for n in notes),
    )


def project_result(
    pack: DataPack,
    confirmed: ConfirmedProfile,
    pre: ProductPreflight,
    world: RunWorld,
    outcome: RunOutcome,
    inventory: OptionInventoryCoverage | None,
    *,
    real_model_calls: int = 0,
    sample: SampleLabel | None = None,
) -> ProductResult:
    """把一次运行的 E1 结果投影成产品结果。没有生成报告（中断、停下）时不擅自分类、不补分。"""
    report: ArmReport | None = outcome.reports.get("arm.main")
    generated = report is not None
    clones: list[CloneResult] = []
    cities: list[CityResult] = []
    comparisons: list[ComparisonLine] = []
    if report is not None:
        entries = {e.clone_id: e for cr in report.city_reports for e in cr.entries}
        for e in world.roster.entries:
            entry = entries[e.clone_id]
            lives = outcome.lives.get(e.clone_id, ())
            years = tuple(
                year_facts(world, str(e.city), r) for r in lives if r.settlement.outcome is not None
            )
            scope = _valuation_scope(world, str(e.city), lives)
            clones.append(_clone(entry, years, outcome.run_id, scope))
        cities = [_city(world, cr) for cr in report.city_reports]
        comparisons = [
            ComparisonLine(
                city_a=str(c.city_a),
                city_b=str(c.city_b),
                direction=c.direction,
                range_difference_low=_ex(c.range_difference_low),
                range_difference_high=_ex(c.range_difference_high),
                note_zh=c.note[:400],
            )
            for c in report.comparisons
        ]
    coverage = (
        ()
        if inventory is None
        else tuple(
            OptionCoverageLine(
                city_ref=c.city_ref,
                status=c.status,
                surveyed=c.surveyed,
                resolved=c.resolved,
                unresolved=c.unresolved,
                excluded=c.excluded,
                note_zh="选项目录覆盖：声明范围内调查过的便利设备与服务资料齐不齐；与这座城的轨迹能否评分是两件事",
            )
            for c in inventory.cities
            if c.city_ref in confirmed.cities
        )
    )
    # 规划器与费用照用户确认的那份预检查（E3C：真实模型写“付费、按报价估算的上限”，mock 写零费用），身份与上限按实际运行配置
    planner = PlannerAndCost(
        planner_kind=world.config.planner.identity.kind,
        planner_id=world.config.planner.identity.planner_id,
        description_zh=pre.planner.description_zh,
        call_limit=world.config.planning.global_call_limit,
        paid=pre.planner.paid,
        max_cost=pre.planner.max_cost,
    )
    fields: dict[str, Any] = dict(
        meta=pack.meta(sample),
        result_id=f"result.{outcome.run_id}",
        run_id=outcome.run_id,
        confirmed=ConfirmedRef(
            confirmed_id=confirmed.confirmed_id, content_fingerprint=confirmed.content_fingerprint
        ),
        preflight=PreflightRef(
            preflight_id=pre.preflight_id, preflight_fingerprint=pre.preflight_fingerprint
        ),
        evaluation_status="generated" if generated else "not_generated",
        not_generated_reason=None
        if generated
        else f"运行状态是“{STAGE_TEXT.get(outcome.status, outcome.status)}”，还没有生成评价与汇总；不擅自生成分类或分数",
        run_stage=outcome.status,
        cities=tuple(cities),
        clones=tuple(clones) if generated else (),
        comparisons=tuple(comparisons),
        option_coverage=coverage,
        data_nature=tuple(city_data_nature(pack, world, ref) for ref in confirmed.cities)
        if generated
        else (),
        reading_guide_zh=READING_GUIDE,
        limitations_zh=(
            *pack.body.limits,
            *confirmed.interests.scope_zh,
            "选项目录覆盖只说明声明范围内调查过的候选资料齐不齐，不保证穷尽整个城市市场",
            "结果只给用户看，不回灌给仍在生活中的分身",
        ),
        sources_zh=(
            "生活事实：E1 结算记录（钱、岗位、住房、食品、服务月份）与月度资源账（本人家务小时）",
            "片区、通勤、用车权、设备故障与税：E1 月度资源账里每月唯一的通勤记录与结算记录（不含故障抽签）",
            "评价：E1-6A 单分身跨期评价（L / V / J）；汇总：E1-6B 城市报告（C / E / M、均值、模拟缺失范围、事件链、硬要求）",
            f"资料：合成资料包 {pack.body.pack_id}（{pack.fingerprint[:12]}…）与产品协议的基础样例；全部是合成设定"
            if pack.body.status == "synthetic_fixture_only"
            else f"资料：冻结的真实资料包 {pack.body.pack_id}（{pack.fingerprint[:12]}…）：城市资料来自公开来源与登记的模型"
            "设定（逐城的来源性质见 data_nature）；生活规则、评分与评价参数是合成探索设定",
        ),
        planner=planner,
        real_model_calls=real_model_calls,
    )
    unsealed = ProductResult.model_construct(**fields, result_fingerprint="0" * 64)
    return ProductResult(**fields, result_fingerprint=content_fingerprint(unsealed.content()))


def fraction_of(x: ExactNumber | None) -> Fraction | None:
    return None if x is None else Fraction(x.exact)
