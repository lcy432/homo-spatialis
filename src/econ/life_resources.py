"""月度时间与精力的核算（E1-4A，`DECISIONS/0020`）。纯函数：无 I/O、无随机、无 LLM。

输入是契约对象和"这个月**实际**执行了什么"的清单（实际住的房、实际吃的食品方案、拿到工资的工作、
执行了的服务、发生了的体验、参加了的培训、在服务期内的持有物）；输出是这个月的资源账，以及超限时
的诊断。签名里没有偏好真值、评分规格或行动理由——同样的实际生活，资源结算永远一样。

结算（``econ.life_settlement``）在每月第 7 步之后调用这里，与记钱走同一条实际执行路径。加载器
（``ledger.life_catalog``）只检查资源资料是否齐全一致，不在那里另写一套算法。

执行修订 ``RESOURCE_RULES_REVISION``（r2，2026-10-08，ADR0020 第十二节）：出局当月检查已知劳动的资源下限；
多份到岗工作各算各的往返。

--------------------------------------------------------------------------
一个月怎么算
--------------------------------------------------------------------------

* 时间预算 = 年度净可分配小时 / 12；精力额度 = 每月可承受负担（``energy.level``，尺度
  ``e1-4a-monthly-quota-v1``）。月与月之间不借用，未用完的不结转，满足感不改变它们。已安排时间 ≤ 预算；
  没安排的时间（``time_remaining``）月底作废，不解释成休息、不回血、不加满足感，也不要求安排满。
* 工作：各实际在岗岗位的工时之和，负担 = 工时 × 每工时负担。
* 通勤：Σ 实际所住住房的往返分钟 × 这个岗位的到岗次数 / 60（每个岗位每次到岗算一趟完整往返，岗位之间
  不合并行程），负担 = 通勤小时 × 协议的每小时通勤负担。
* 食品准备：实际执行的那份食品方案的每月准备小时（没有另一份"基础做饭时间"）。
* 家务：每项任务本人小时 = min(基准, 当月在用的各完整替代方案的残余用时)——不逐项相减，所以残余
  不会为负，同一任务也不会被两个替代各抵扣一次。
* 服务、体验、培训：按资源事实的计量依据（每服务月 / 每月使用次数 × 每次 / 每次发生）。
* 负担 = 小时 × 每小时负担。全部用精确分数，比较不加容差：等于额度通过，超出就不通过。

出局当月（``resource_check = known_lower_bound``）：工资已经入账的工作照记工时与负担；没有到岗需求的
通勤确定为零，需要到岗而这个月没有获得住房的通勤记为未知；住房、食品与其他行动没有获得、没有执行，不记。
已知用量超过预算或额度，就足以判定计划不可行；没超也只说明下限没超，不是完整月份的检查。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from common.life_contracts import (
    MONTHS_PER_YEAR,
    RESOURCE_RULES_REVISION,
    ExactAmount,
    LifeContractError,
    LifeOpportunity,
    MonthlyResourceRules,
    MonthResources,
    PersonYearState,
    ResourceContribution,
    ResourceProfile,
    ResourceShortfall,
    ResourceYear,
    to_fraction,
)

#: 诊断里列出的主要贡献项个数上限。
TOP_CONTRIBUTORS = 5
_CATEGORY_ORDER = (
    "work",
    "commute",
    "food_prep",
    "household_task",
    "service",
    "experience",
    "training",
    "recovery",  # E1-5C：生病月份的恢复时间与负担
    "treatment",  # E1-5C：接受治疗那个月的就医时间与负担
)


@dataclass(frozen=True)
class Activity:
    """这个月实际执行或在服务期内的一项东西。``role`` 是结算角色（``ledger.life_catalog.item_role``）。"""

    role: str
    opp: LifeOpportunity
    quantity: float


def monthly_time_budget(state: PersonYearState) -> Fraction:
    """每月时间预算 = 年度净可分配小时 / 12（12 个标准月）。"""
    return to_fraction(state.time_budget_hours) / MONTHS_PER_YEAR


def monthly_energy_quota(state: PersonYearState) -> Fraction:
    """每月可承受负担额度（尺度 ``e1-4a-monthly-quota-v1``）。"""
    return to_fraction(state.energy.level)


def commute_hours(minutes: int, visits: int) -> Fraction:
    """一个岗位的当月通勤小时 = 往返分钟 × 到岗次数 / 60。例：70 分钟 × 20 次 / 60 = 70/3 ≈ 23.33 小时。"""
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
        raise LifeContractError(f"往返分钟必须是非负整数，得到 {minutes!r}")
    return Fraction(minutes * visits, 60)


def _profile(opp: LifeOpportunity) -> ResourceProfile:
    if opp.resource is None:
        raise LifeContractError(f"{opp.opportunity_id} 没有资源事实：缺了不当零")
    return opp.resource


def timed_hours(profile: ResourceProfile, quantity: float) -> Fraction:
    """按计量依据算这个月占用的小时：每服务月 / 每月使用次数 × 每次 / 每次发生。"""
    if profile.hours is None:
        raise LifeContractError(f"{profile.basis} 的资源事实没有小时数")
    h = to_fraction(profile.hours)
    if profile.basis == "per_use":
        assert profile.uses_per_month is not None
        h *= profile.uses_per_month
    if profile.quantity_scales:
        h *= to_fraction(quantity)
    return h


def _row(category: str, opp_id: str | None, hours: Fraction, rate: Fraction, basis: str, task=None):
    return ResourceContribution(
        category=category,  # type: ignore[arg-type]
        opportunity_id=opp_id,
        task=task,
        hours=ExactAmount.of(hours),
        burden=ExactAmount.of(hours * rate),
        basis=basis[:240],
    )


def _fmt(q: Fraction) -> str:
    return str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"


def household_rows(
    rules: MonthlyResourceRules, in_use: Sequence[Activity]
) -> list[ResourceContribution]:
    """每项家务这个月本人还要花多少小时。替代只取当月在用的里面残余最小的那一个。"""
    rows: list[ResourceContribution] = []
    for task in rules.household_tasks:
        baseline = to_fraction(task.baseline_hours_per_month)
        best, by = baseline, None
        for a in in_use:
            if a.opp.resource is None:
                continue
            for sub in a.opp.resource.substitutes:
                if sub.task != task.task:
                    continue
                r = to_fraction(sub.residual_hours_per_month)
                if r < best:
                    best, by = r, a.opp.opportunity_id
        residual = min(best, baseline)
        if by is None:
            basis = f"没有在用的替代：本人做 {_fmt(baseline)} 小时（基准）"
        else:
            basis = (
                f"基准 {_fmt(baseline)} 小时；{by} 在用，本人还要 {_fmt(residual)} 小时，"
                f"由它承担 {_fmt(baseline - residual)} 小时"
            )
        rows.append(
            _row(
                "household_task", by, residual, to_fraction(task.burden_per_hour), basis, task.task
            )
        )
    return rows


def _sum(rows: Sequence[ResourceContribution], attr: str) -> Fraction:
    return sum((getattr(r, attr).fraction() for r in rows), Fraction(0))


def _burden_of(rows: Sequence[ResourceContribution], category: str) -> Fraction:
    return sum((r.burden.fraction() for r in rows if r.category == category), Fraction(0))


def _sorted(rows: list[ResourceContribution]) -> list[ResourceContribution]:
    order = {c: i for i, c in enumerate(_CATEGORY_ORDER)}
    return sorted(rows, key=lambda r: order[r.category])


def month_resources(
    rules: MonthlyResourceRules,
    state: PersonYearState,
    month: int,
    activities: Sequence[Activity],
    *,
    exit_month: bool = False,
    extra: Sequence[Any] = (),
) -> tuple[MonthResources, list[ResourceShortfall]]:
    """一个月的资源账与超限诊断。``activities`` 只能是这个月**实际**执行或在服务期内的东西。

    出局当月（``exit_month``）只给已经发生的工作；返回的记录是"只检查已知下限"，诊断照样会报超限。

    ``extra``（E1-5C）：生病月份的恢复与接受治疗那个月的就医（``econ.life_health.ExtraLoad``：类别、小时、每小时负担、
    依据）。它们和别的活动一样按"小时 × 每小时负担"计入同一本账、同一个预算与额度，不另写算法；出局当月不计。
    """
    budget = monthly_time_budget(state)
    quota = monthly_energy_quota(state)
    jobs = [a for a in activities if a.role == "job"]
    homes = [a for a in activities if a.role == "housing"]
    rows: list[ResourceContribution] = []
    notes: list[str] = []

    job_days = visits = 0
    onsite_jobs = []
    for a in jobs:
        job = _profile(a.opp).job
        if job is None:
            raise LifeContractError(f"{a.opp.opportunity_id} 没有岗位方案：工时与到岗不能补成零")
        hours = to_fraction(job.work_hours_per_month)
        rows.append(
            _row(
                "work",
                a.opp.opportunity_id,
                hours,
                to_fraction(job.burden_per_work_hour),
                f"{job.work_mode} 岗位：每月 {_fmt(hours)} 工时 × 每工时负担 {job.burden_per_work_hour:g}",
            )
        )
        job_days += job.workdays_per_month
        if job.onsite_days_per_month > 0:
            onsite_jobs.append((a, job))
            visits += job.onsite_days_per_month

    housing_id: str | None = None
    minutes: int | None = None
    if not onsite_jobs:
        commute_status = "no_onsite_work"
    elif exit_month or not homes:
        commute_status = "unknown_no_housing"
        notes.append("这个月没有获得住房：通勤的居住依据缺失，记为未知（不当零，也不沿用上月住房）")
    else:
        if len(homes) != 1:
            raise LifeContractError(f"第 {month} 月实际住房有 {len(homes)} 处，不是恰好一处")
        home = homes[0].opp
        housing_id = home.opportunity_id
        value = home.spec.get(rules.commute_minutes_key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise LifeContractError(
                f"{housing_id} 没有整数的 {rules.commute_minutes_key}：通勤分钟缺失不当零"
            )
        minutes = value
        rate = to_fraction(rules.commute_burden_per_hour)
        for job_activity, job in onsite_jobs:  # 每个岗位每次到岗一趟完整往返，岗位之间不合并
            rows.append(
                _row(
                    "commute",
                    job_activity.opp.opportunity_id,
                    commute_hours(minutes, job.onsite_days_per_month),
                    rate,
                    f"实际住房 {housing_id} 往返 {minutes} 分钟 × 这个岗位到岗 "
                    f"{job.onsite_days_per_month} 次 ÷ 60",
                )
            )
        commute_status = "computed"
        if len(onsite_jobs) > 1:
            notes.append(
                f"{len(onsite_jobs)} 份到岗岗位各算各的往返，累计到岗 {visits} 次"
                "（不是不同的日历天数；同日连续上班共享往返没有建模）"
            )
    if housing_id is None and homes:
        housing_id = homes[0].opp.opportunity_id

    if exit_month:
        notes.append(
            "出局当月：只算已经确定的部分——已入账工资对应的工作"
            + ("与确定为零的通勤" if commute_status == "no_onsite_work" else "")
            + "；住房与食品没有获得，其他行动没有执行；只检查已知下限，不是完整月份的检查"
        )
        rows = _sorted(rows)
        record = MonthResources(
            month=month,
            resource_check="known_lower_bound",
            time_budget=ExactAmount.of(budget),
            energy_quota=ExactAmount.of(quota),
            time_used=ExactAmount.of(_sum(rows, "hours")),
            energy_used=ExactAmount.of(_sum(rows, "burden")),
            contributions=tuple(rows),
            housing_opportunity_id=None,
            commute_roundtrip_minutes=None,
            job_workdays_sum=job_days,
            onsite_visits=visits,
            commute_status=commute_status,  # type: ignore[arg-type]
            work_relative_burden=ExactAmount.of(_burden_of(rows, "work") / quota),
            commute_relative_burden=ExactAmount.of(Fraction(0))
            if commute_status == "no_onsite_work"
            else None,
            notes=tuple(notes),
        )
        return record, shortfalls(rules, record)

    for a in activities:
        if a.role in ("job", "housing", "durable_good"):
            continue  # 工作已算；住房与持续物品是被动的，只可能替代家务
        p = _profile(a.opp)
        if p.basis in ("passive", "job"):
            continue
        category = {
            "food": "food_prep",
            "periodic_service": "service",
            "prepaid_service": "service",
            "experience": "experience",
            "training": "training",
        }.get(a.role)
        if category is None:
            raise LifeContractError(f"{a.opp.opportunity_id}：结算角色 {a.role} 没有资源计量规则")
        hours = timed_hours(p, a.quantity)
        if p.basis == "per_use":
            basis = f"每月 {p.uses_per_month} 次 × 每次 {p.hours:g} 小时"
        elif p.basis == "per_occurrence":
            basis = f"一次体验 {p.hours:g} 小时"
        else:
            basis = f"每个服务月 {p.hours:g} 小时"
        if p.quantity_scales and a.quantity != 1:
            basis += f" × 数量 {a.quantity:g}"
        assert p.burden_per_hour is not None
        rows.append(
            _row(
                category,
                a.opp.opportunity_id,
                hours,
                to_fraction(p.burden_per_hour),
                basis + f"；每小时负担 {p.burden_per_hour:g}",
            )
        )
    substitutes = [
        a for a in activities if a.opp.resource is not None and a.opp.resource.substitutes
    ]
    rows += household_rows(rules, substitutes)
    for load in extra:  # E1-5C：恢复与就医，同一个"小时 × 每小时负担"
        rows.append(
            _row(
                load.category,
                load.opportunity_id,
                load.hours,
                load.burden_per_hour,
                load.basis,
                load.task,
            )
        )
    rows = _sorted(rows)

    used_t, used_e = _sum(rows, "hours"), _sum(rows, "burden")
    record = MonthResources(
        month=month,
        resource_check="complete",
        time_budget=ExactAmount.of(budget),
        energy_quota=ExactAmount.of(quota),
        time_used=ExactAmount.of(used_t),
        time_remaining=ExactAmount.of(budget - used_t),
        energy_used=ExactAmount.of(used_e),
        energy_remaining=ExactAmount.of(quota - used_e),
        within_limits=used_t <= budget and used_e <= quota,
        contributions=tuple(rows),
        housing_opportunity_id=housing_id,
        commute_roundtrip_minutes=minutes,
        job_workdays_sum=job_days,
        onsite_visits=visits,
        commute_status=commute_status,  # type: ignore[arg-type]
        work_relative_burden=ExactAmount.of(_burden_of(rows, "work") / quota),
        commute_relative_burden=ExactAmount.of(_burden_of(rows, "commute") / quota),
        total_relative_burden=ExactAmount.of(used_e / quota),
        notes=tuple(notes),
    )
    return record, shortfalls(rules, record)


def shortfalls(rules: MonthlyResourceRules, record: MonthResources) -> list[ResourceShortfall]:
    """时间或精力超了多少、主要是谁占的。没超就是空列表。

    完整检查的月份比完整用量；只检查已知下限的月份（出局当月）比已知用量——已知的就超了，完整的只会更多。
    """
    out: list[ResourceShortfall] = []
    if record.resource_check == "not_reached":
        return out
    assert record.time_used is not None and record.energy_used is not None
    for resource, used, limit, attr, unit in (
        ("time", record.time_used, record.time_budget, "hours", rules.time_unit),
        ("energy", record.energy_used, record.energy_quota, "burden", rules.energy_unit),
    ):
        u, lim = used.fraction(), limit.fraction()
        if u <= lim:
            continue
        ranked = sorted(
            enumerate(record.contributions),
            key=lambda item: (-getattr(item[1], attr).fraction(), item[0]),
        )
        out.append(
            ResourceShortfall(
                resource=resource,  # type: ignore[arg-type]
                month=record.month,
                resource_check=record.resource_check,  # type: ignore[arg-type]
                rules_revision=RESOURCE_RULES_REVISION,
                unit=unit,
                limit=limit,
                used=used,
                excess=ExactAmount.of(u - lim),
                top_contributors=tuple(r for _, r in ranked[:TOP_CONTRIBUTORS]),
            )
        )
    return out


def not_reached(state: PersonYearState, month: int) -> MonthResources:
    """出局之后的月份：没有发生，不伪装成零负担的完整月份。"""
    return MonthResources(
        month=month,
        resource_check="not_reached",
        time_budget=ExactAmount.of(monthly_time_budget(state)),
        energy_quota=ExactAmount.of(monthly_energy_quota(state)),
        commute_status="not_reached",
        notes=("出局之后：没有继续生活，这个月没有发生",),
    )


def blocked_month(state: PersonYearState, month: int, note: str) -> MonthResources:
    """停止月（E1-5C）：这个月的执行没有提交，没有用量；超限诊断在停止记录里。不伪装成完整月份。"""
    return MonthResources(
        month=month,
        resource_check="blocked",
        time_budget=ExactAmount.of(monthly_time_budget(state)),
        energy_quota=ExactAmount.of(monthly_energy_quota(state)),
        commute_status="not_reached",
        notes=(note[:MAX_NOTE],),
    )


#: 资源记录里一条说明的长度上限（与契约一致）。
MAX_NOTE = 280


def resource_year(
    rules: MonthlyResourceRules, state: PersonYearState, months: Sequence[MonthResources]
) -> ResourceYear:
    """把一年的月度账装起来；出局之后缺的月份补成"未发生"。"""
    have = {m.month: m for m in months}
    full = [have.get(m) or not_reached(state, m) for m in range(1, MONTHS_PER_YEAR + 1)]
    basis = state.resource_budget_basis
    assert basis is not None
    return ResourceYear(
        rules_version=rules.version,
        rules_revision=RESOURCE_RULES_REVISION,
        time_unit=rules.time_unit,
        energy_unit=rules.energy_unit,
        energy_scale_version=rules.energy_scale_version,
        monthly_time_budget=ExactAmount.of(monthly_time_budget(state)),
        monthly_energy_quota=ExactAmount.of(monthly_energy_quota(state)),
        reserved=f"{rules.reserved_time}；{basis.reserved}"[:400],
        months=tuple(full),
    )
