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
* 显示文字（``product.display``）：这里生成的中文都经 ``t()`` / ``label()`` 生成，逐字与原来相同，同时记下稳定 key 与有类型的参数
  （机会、片区与城市按编号，月份、数字与代码按类型）；字符串数组在生成处按机会编号与月份绑定，前端不用拆中文。E1 报告里的
  城市说明与比较说明按报告的结构化字段重新生成、与原文逐字相同才绑定；事件链的环境记录是 E1 原文（英文缺失）。
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
from product.display import Code, Ent, Join, Months, Num, Raw, Sub, label, say, t
from product.exact import canonical, display, money_display
from product.interests import confirmed_scope_lines
from product.pack import DataPack
from product.pack_facts import TAX_STATUS_ZH, city_data_nature, pack_lines, tax_status_label
from product.public_errors import clone_explanation

CATEGORY_TEXT = {
    "C": Sub("result.category.C", "完成且评价完整"),
    "E": Sub("result.category.E", "确认的生活出局"),
    "M": Sub("result.category.M", "模拟问题，无法完整评价"),
}
STAGE_TEXT = {
    "completed": Sub("progress.stage.completed", "已完成"),
    "paused": Sub("progress.stage.paused", "批次暂停（程序或配置问题）"),
    "halted": Sub("progress.stage.halted", "批次停下（服务、预算或请求检查）"),
    "interrupted": Sub("progress.stage.interrupted", "在两次规划调用之间中断"),
    "budget_exhausted": Sub("progress.stage.budget_exhausted", "全局调用上限用完"),
    "running": Sub("progress.stage.running", "运行中"),
}
READING_GUIDE = (
    Sub(
        "result.reading.mean_denominator",
        "完整者平均 J 的分母是 C（完成且评价完整的分身），不是原计划人数；C = 0 时没有平均值，不显示为 0",
    ),
    Sub(
        "result.reading.exits",
        "确认的生活出局（E）不进均值，也不进模拟缺失范围；出局与缺失的比例以原计划人数为分母",
    ),
    Sub(
        "result.reading.missing_range",
        "模拟缺失范围的分母是 C + M：已知的月份照算，未知部分在 0 到 1 之间都保留。它不是置信区间，也不是现实幸福的概率",
    ),
    Sub(
        "result.reading.l_v_j",
        "L 是实际几年逐月满足感的平均；V 是从终态出发、在预先冻结的参考生活模板与治疗分支里的估值，不是对未来的预测，也不是全局最优；"
        "J 按月份权重合成两者",
    ),
    Sub(
        "result.reading.simulation_problems",
        "资料缺失、规划没有完成、停止与程序问题是模拟问题（M），不写成“在这座城活不下去”",
    ),
    Sub(
        "result.reading.chores",
        "减少日常杂务本身对应便利满足感；没有安排的空闲时间不额外加分",
    ),
    Sub(
        "result.reading.event_chain",
        "事件链按核对过的环境记录写成，不是模型的理由，也不是因果证明；硬要求单独报告，不扣分、不删人",
    ),
    Sub(
        "result.reading.precision",
        "分数是当前合成模型下、按你确认的陈述偏好计算的结果；小数末位的差异不构成可靠的排序",
    ),
)


def stage_text(status: str) -> str:
    """运行阶段的公开名称（结果与进度共用）；没有登记的状态照写状态代码。"""
    s = STAGE_TEXT.get(status)
    return status if s is None else say(s)


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


def _item(world: RunWorld, city: str, oid: str) -> Ent | Code:
    """一项机会的名称参数：目录里有就按机会编号绑定（英文按编号查资源），没有就写编号本身。"""
    cat = world.bundle.catalog_for(city)
    o = cat.opportunity(oid) if cat is not None else None
    return Code(oid) if o is None else Ent("opportunity", oid, o.label)


def _label(world: RunWorld, city: str, oid: str) -> str:
    item = _item(world, city, oid)
    return item.value if isinstance(item, Code) else label("opportunity", oid, item.zh)


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
                rows.append(
                    t(
                        "fact.item_months",
                        "{item}（{months}）",
                        item=_item(world, city, it.opportunity_id),
                        months=Months(tuple(it.covered_months)),
                    )
                )
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
        notes.append(
            t(
                "fact.note.exit",
                "第 {month} 月付不起最低住房与食品而出局（环境记录）",
                month=out.exit.month,
            )
        )
    if out.stop is not None:
        notes.append(
            t(
                "fact.note.stop",
                "第 {month} 月停止（疾病之后应急安排不足或资料缺口；模拟的支持边界，不是生活出局）",
                month=out.stop.month,
            )
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
    "personal_communication": Sub("fact.support.personal_communication", "个人通信"),
    "home_connectivity": Sub("fact.support.home_connectivity", "居家联网"),
    "household_supplies": Sub("fact.support.household_supplies", "家用日耗品"),
}
BASKET_FUNCTION = "personal_consumption"


def _support_facts(world: RunWorld, city: str, out: Any) -> dict[str, Any]:
    """日常支持与个人消费篮子（白名单，逐字段取自结算记录）：按月服务的计划月份与实际获得的月份；篮子按每月实际获得的份数。"""
    cat = world.bundle.catalog_for(city)
    assert cat is not None
    services = [it for it in out.items if it.role == "periodic_service"]
    support = []
    for f, name in SUPPORT_LABEL_ZH.items():
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
                label_zh=say(name),
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
    "rejected": Sub(
        "fact.not_made.rejected",
        "没有买成：那个月付不起或计划不可执行；没有扣款，也没有得到这件东西",
    ),
    "cancelled_when_ill": Sub(
        "fact.not_made.cancelled_when_ill",
        "按年度应急安排，购买那个月生着病就取消：没有扣款，不顺延、不补购",
    ),
    "paused": Sub("fact.not_made.paused", "那个月生病暂停：没有购买"),
    "not_reached": Sub("fact.not_made.not_reached", "那个月之前已经出局或停止：没有发生"),
}
_NOT_DONE_ZH = Sub(
    "fact.activity.not_done",
    "缺少需要的装备能力（家里没有在用的装备，这次也没有套餐提供）：没有进行，也没有收费",
)


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
            status_zh=say(_NOT_MADE_ZH[it.status]),
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
                not_done_zh=say(_NOT_DONE_ZH) if failed else None,
            )
        )
    return goods, not_made, tuple(activities)


#: 通勤记录的状态（固定的公开说明）。
_COMMUTE_STATUS_ZH = {
    "computed": Sub(
        "fact.commute_status.computed",
        "按通勤关系表执行：实际住处片区到各个到岗岗位的就业片区，取可用方式里往返最少的一种",
    ),
    "no_onsite_work": Sub(
        "fact.commute_status.no_onsite_work", "这个月没有需要到岗的工作：通勤为零"
    ),
    "unknown_no_housing": Sub(
        "fact.commute_status.unknown_no_housing",
        "出局当月没有住房：通勤依据缺失，记为未知（不是 0）",
    ),
}
#: 设备故障之后的替换结果（固定的公开说明）。
_REPLACEMENT_ZH = {
    "not_declared": Sub(
        "fact.replacement.not_declared", "年度计划没有预先声明替换：坏了之后就没有这件东西了"
    ),
    "bought": Sub("fact.replacement.bought", "按年度计划预先声明的替换，在下个月按普通购买买到"),
    "rejected": Sub(
        "fact.replacement.rejected",
        "预先声明了替换，但下个月买不起或计划不可执行：没有买成，不顺延",
    ),
    "next_year": Sub("fact.replacement.next_year", "第 12 月坏的：替换留给下一年的计划"),
    "not_reached": Sub("fact.replacement.not_reached", "替换那个月之前已经出局或停止：没有发生"),
}


def commute_mode_text(mode: str) -> str:
    """通勤方式的公开名称（E1 的 ``COMMUTE_MODE_LABEL`` 是中文的唯一来源）：与资料性质里的通勤方式是同一个 ``commute_mode`` 实体，
    同一份结果里同一段中文只有一种绑定。"""
    return label("commute_mode", mode, COMMUTE_MODE_LABEL[mode])


def _area_facts(world: RunWorld, city: str, out: Any) -> dict[str, Any]:
    """片区、通勤、用车权、设备故障与税（DECISIONS/0042），逐字段取自资源账的月度通勤记录与结算记录。"""
    cat = world.bundle.catalog_for(city)
    assert cat is not None
    areas = {a.area_id: a for a in cat.areas}

    def area_label(area_id: str | None) -> str | None:
        a = None if area_id is None else areas.get(area_id)
        return area_id if a is None else label("area", a.area_id, a.label)

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
                area_label=None if area is None else label("area", area.area_id, area.label),
                area_typical_roundtrip_minutes=None
                if area is None
                else area.typical_roundtrip_minutes,
            )
        )
    commute = tuple(
        CommuteMonths(
            months=tuple(months),
            status=c.status,
            status_zh=say(_COMMUTE_STATUS_ZH[c.status]),
            home_area_id=c.home_area,
            home_area_label=area_label(c.home_area),
            trips=tuple(
                CommuteTrip(
                    job_opportunity_id=u.job_opportunity_id,
                    job_label=_label(world, city, u.job_opportunity_id),
                    work_area_id=u.work_area,
                    work_area_label=area_label(u.work_area) or u.work_area,
                    mode=u.mode,
                    mode_zh=commute_mode_text(u.mode),
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
            note_zh=t(
                "fact.car_access.bought",
                "开车通勤要这个月有在用的用车权（按月付费）：用上的月份里，到岗时在可用方式里取往返最少的一种",
            )
            if bought
            else t(
                "fact.car_access.not_bought",
                "今年没有用上用车权：这一年不能开车通勤，到岗只用通勤关系表里不需要用车权的方式",
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
            replacement_status_zh=say(_REPLACEMENT_ZH[f.replacement_status]),
            replacement_month=f.replacement_month,
        )
        for f in out.device_failures
    )
    if out.tax_policy_id is not None and out.tax_status in TAX_STATUS_ZH:
        facts["tax"] = TaxFact(
            policy_id=out.tax_policy_id,
            status=out.tax_status,
            status_zh=tax_status_label(out.tax_status),
        )
    return facts


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
        item = _item(world, city, s.opportunity_id)
        name = _label(world, city, s.opportunity_id)
        if s.covered:
            out.append(
                ValuationScopeNote(
                    kind="equipment_use_valued",
                    opportunity_id=s.opportunity_id,
                    label=name,
                    capabilities=s.covered,
                    months_left=s.months_left,
                    text_zh=t(
                        "result.valuation.use_valued",
                        "期末持有的「{item}」还能用 {months} 个月：期末参考生活按它的能力（{capabilities}）"
                        "继续安排相应活动，剩余寿命内照用、不重复购买",
                        item=item,
                        months=s.months_left,
                        capabilities=Join(tuple(Code(c) for c in s.covered)),
                    ),
                )
            )
        if s.uncovered:
            out.append(
                ValuationScopeNote(
                    kind="equipment_use_not_in_templates",
                    opportunity_id=s.opportunity_id,
                    label=name,
                    capabilities=s.uncovered,
                    months_left=s.months_left,
                    text_zh=t(
                        "result.valuation.use_not_in_templates",
                        "期末持有的「{item}」还能用 {months} 个月：参考模板没有考虑它的用途（{capabilities}），"
                        "期末估值没有计入这项用途——这是参考模板的范围限制，不是说这件装备没有价值",
                        item=item,
                        months=s.months_left,
                        capabilities=Join(tuple(Code(c) for c in s.uncovered)),
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
        category_zh=say(CATEGORY_TEXT[entry.category]),
        missing_reason=entry.missing_reason,
        missing_reason_zh=None
        if entry.missing_reason is None
        else missing_reason_text(entry.missing_reason),
        # E3-A 收尾 F5：说明只由类别与缺失原因生成，不透传 entry.detail（那里可能有异常原文）
        explanation=clone_explanation(entry, run_id),
        l_actual=_ex(entry.l_actual),
        v_reference=_ex(entry.v_reference),
        j=_ex(entry.j),
        range_lower=_ex(entry.lower),
        range_upper=_ex(entry.upper),
        reference_plan=entry.selected_plan_id,
        # 环境记录的文字是 E1 写的中文原文：外层的年月按模板绑定，原文本身没有译文（英文显示为缺失，不猜）
        event_chain=tuple(
            t(
                "result.event_chain.step",
                "第 {year} 年第 {month} 月：{text}",
                year=s.year_index,
                month=s.month,
                text=Raw(s.text),
            )
            for s in entry.event_chain
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


def missing_reason_text(reason: str) -> str:
    """缺失原因的公开名称（E1 的 ``MISSING_REASON_TEXT`` 是中文的唯一来源；显示 key 按原因代码）。"""
    return t(f"result.missing_reason.{reason}", MISSING_REASON_TEXT[reason])


def _headline(cr: CityReport, city: Ent | Code) -> str:
    counts = Sub(
        "result.city.counts",
        "{city}：原计划 {planned} 个分身——{complete} 个完成且可评价、{exits} 个生活出局、{missing} 个模拟问题",
        (
            ("city", city),
            ("planned", Num(str(cr.planned))),
            ("complete", Num(str(cr.complete))),
            ("exits", Num(str(cr.exits))),
            ("missing", Num(str(cr.missing))),
        ),
    )
    if cr.complete == 0:
        return t(
            "result.city.headline_no_mean", "{counts}；没有可用于平均的完整结果", counts=counts
        )
    assert cr.mean_j is not None
    return t(
        "result.city.headline_mean",
        "{counts}；{complete} 个完成且可评价分身的平均 J 为 {mean_j}"
        "（分母只数完成且可评价的分身，不是原计划的全部分身）",
        counts=counts,
        complete=cr.complete,
        mean_j=Num(display(cr.mean_j.fraction(), 4)),
    )


def _city_notes(cr: CityReport) -> tuple[str, ...]:
    """E1-6B 城市报告的说明：按报告里的结构化计数重新生成同样的中文（同时记下显示绑定）。与报告原文逐字相同才用；不同（E1
    改了写法）就照原文写、不绑定——英文显示为缺失，不猜。"""
    notes = []
    if cr.complete == 0 and cr.missing:
        notes.append(
            t(
                "result.city.note.no_complete",
                "没有完整评价的分身：完整者均值为空（不填 0，不用 L 代替 J），只给缺失范围",
            )
        )
    if cr.complete == 0 and not cr.missing:
        notes.append(
            t("result.city.note.all_exit", "全部确认出局：均值与缺失范围都为空；出局比例 100%")
        )
    if cr.complete == 1:
        notes.append(
            t(
                "result.city.note.single_complete",
                "只有 1 个完整评价分身：均值就是它的 J，不是稳定的抽样证据",
            )
        )
    if any(e.unexpected_error for e in cr.entries):
        notes.append(
            t(
                "result.city.note.unexpected_error",
                "有意外程序错误：不能假定它只影响这一个分身，先查原因再解读这份报告",
            )
        )
    if any(h.violated or h.unknown or h.not_checked for h in cr.hard_requirements):
        notes.append(
            t(
                "result.city.note.hard_requirements",
                "有分身没有满足、无法判断或尚未核验用户的硬要求（未核验 = 没有可信记录，不是核验过、没问题）：见硬要求一节；"
                "均值高不代表完全符合用户要求",
            )
        )
    return tuple(notes) if tuple(notes) == tuple(cr.notes) else tuple(cr.notes)


_DIRECTION = {
    "a_higher": Sub(
        "result.comparison.a_higher",
        "在这些缺失范围下 A 城始终更高：方向一致；不是显著性检验，也不是总体因果结论",
    ),
    "b_higher": Sub(
        "result.comparison.b_higher",
        "在这些缺失范围下 B 城始终更高：方向一致；不是显著性检验，也不是总体因果结论",
    ),
    "undetermined": Sub(
        "result.comparison.undetermined", "范围差含 0：在这些缺失范围下不能确定方向"
    ),
    "not_available": Sub(
        "result.comparison.not_available", "有一座城全部确认出局，没有范围可比；看出局比例"
    ),
}


def _comparison_note(c: Any) -> str:
    """E1-6B 的比较说明：按比较里的结构化字段（方向、出局比例、有效分身数）重新生成同样的中文（同时记下显示绑定）；与原文
    不同就照原文写、不绑定。"""
    head = _DIRECTION.get(c.direction)
    if head is not None:
        text = t(
            "result.comparison.note",
            "{direction}。出局比例 A {exit_a}、B {exit_b}，有效分身数 A {complete_a}/{planned_a}、B {complete_b}/{planned_b}",
            direction=head,
            exit_a=Num(f"{c.exit_share_a.value:.0%}"),
            exit_b=Num(f"{c.exit_share_b.value:.0%}"),
            complete_a=c.complete_a,
            planned_a=c.planned_a,
            complete_b=c.complete_b,
            planned_b=c.planned_b,
        )
        if text == c.note:
            return text[:400]
    return c.note[:400]


def _city(world: RunWorld, cr: CityReport) -> CityResult:
    cat = world.bundle.catalog_for(str(cr.city))
    city: Ent | Code = Code(str(cr.city)) if cat is None else Ent("city", str(cr.city), cat.label)
    notes = list(_city_notes(cr))
    if cr.range_lower is None:
        notes.append(
            t(
                "result.city.note.no_range",
                "没有完整或可算范围的分身（例如全部生活出局）：模拟缺失范围为空",
            )
        )
    return CityResult(
        city_ref=str(cr.city),
        label=city.value if isinstance(city, Code) else label("city", city.ident, city.zh),
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
                reason=r.reason, reason_zh=missing_reason_text(r.reason), count=r.count
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
        headline_zh=_headline(cr, city),
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
                note_zh=_comparison_note(c),
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
                note_zh=t(
                    "result.option_coverage.note",
                    "选项目录覆盖：声明范围内调查过的便利设备与服务资料齐不齐；与这座城的轨迹能否评分是两件事",
                ),
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
        else t(
            "result.not_generated",
            "运行状态是“{stage}”，还没有生成评价与汇总；不擅自生成分类或分数",
            stage=STAGE_TEXT.get(outcome.status) or Code(outcome.status),
        ),
        run_stage=outcome.status,
        cities=tuple(cities),
        clones=tuple(clones) if generated else (),
        comparisons=tuple(comparisons),
        option_coverage=coverage,
        data_nature=tuple(city_data_nature(pack, world, ref) for ref in confirmed.cities)
        if generated
        else (),
        reading_guide_zh=tuple(say(s) for s in READING_GUIDE),
        # 资料包的限制说明是资料包自己的文字：与预检查的范围说明同一份绑定（``capability/limits/item.<序号>``，资料包各有各的
        # 资源）。兴趣范围的几句来自确认画像：与预检查一样按保存的作答重新生成、逐条核对（对不上就照用原文）
        limitations_zh=(
            *pack_lines("limits", pack.body.limits),
            *confirmed_scope_lines(pack, confirmed),
            t(
                "result.limit.option_coverage",
                "选项目录覆盖只说明声明范围内调查过的候选资料齐不齐，不保证穷尽整个城市市场",
            ),
            t("result.limit.not_fed_back", "结果只给用户看，不回灌给仍在生活中的分身"),
        ),
        sources_zh=(
            t(
                "result.source.life_facts",
                "生活事实：E1 结算记录（钱、岗位、住房、食品、服务月份）与月度资源账（本人家务小时）",
            ),
            t(
                "result.source.area_facts",
                "片区、通勤、用车权、设备故障与税：E1 月度资源账里每月唯一的通勤记录与结算记录（不含故障抽签）",
            ),
            t(
                "result.source.evaluation",
                "评价：E1-6A 单分身跨期评价（L / V / J）；汇总：E1-6B 城市报告（C / E / M、均值、模拟缺失范围、事件链、硬要求）",
            ),
            t(
                "result.source.pack_synthetic",
                "资料：合成资料包 {pack_id}（{fingerprint}…）与产品协议的基础样例；全部是合成设定",
                pack_id=Code(pack.body.pack_id),
                fingerprint=Code(pack.fingerprint[:12]),
            )
            if pack.body.status == "synthetic_fixture_only"
            else t(
                "result.source.pack_real",
                "资料：冻结的真实资料包 {pack_id}（{fingerprint}…）：城市资料来自公开来源与登记的模型"
                "设定（逐城的来源性质见 data_nature）；生活规则、评分与评价参数是合成探索设定",
                pack_id=Code(pack.body.pack_id),
                fingerprint=Code(pack.fingerprint[:12]),
            ),
        ),
        planner=planner,
        real_model_calls=real_model_calls,
    )
    unsealed = ProductResult.model_construct(**fields, result_fingerprint="0" * 64)
    return ProductResult(**fields, result_fingerprint=content_fingerprint(unsealed.content()))


def fraction_of(x: ExactNumber | None) -> Fraction | None:
    return None if x is None else Fraction(x.exact)
