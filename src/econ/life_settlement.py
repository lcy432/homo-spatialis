"""生活协议的年度结算（E1-2，`DECISIONS/0017`）。

**纯函数。** 同样的输入永远给出同样的输出：不读写文件、不取随机数、不调用 LLM，
也不修改传进来的任何对象（契约对象全部 frozen）。签名里没有偏好真值，也没有评分器：
结算只回答"钱怎么流、东西实际拿到了什么"，不回答"过得好不好"。

一年一份计划，环境在内部按月记账，不增加月度 LLM 调用。每个月固定七步：

1. 读月初现金；
2. 按当月在岗的工作计算税前收入，按**年内累计**计算合成税；
3. 收入入账；
4. 检查最低住房 + 最低食品是否付得起（**只检查，不扣款**）。付不起 → 出局；
5. 付实际住房与食品（每项只能动用"为另一项最低需求预留之后"剩下的钱），再按计划
   顺序付其他周期性费用（按月/按周的服务、年付服务的当期付款）；
6. 按计划顺序执行当月的一次性购买、体验与培训报名；
7. 更新持有物服务月、培训参与月与月末余额。

不能动用未来月份尚未挣到的工资；没有声明的借款、透支或自动变卖。同月竞争资金时
按计划里的先后顺序（第 5 步内、第 6 步内各自按计划顺序），顺序原样记入结果。

--------------------------------------------------------------------------
精度与舍入：整数最小单位，守恒是精确等式，没有容差
--------------------------------------------------------------------------

* 金额在内部一律是整数最小单位（``MoneyBasis.minor_unit_digits``，美元 = 分）。
* 年额按月摊用**累计舍入**：第 m 月 = round(A·m/12) − round(A·(m−1)/12)，四舍五入
  （ROUND_HALF_UP），12 个月之和恰好等于 A。月价的 A = 12 × 月额，所以每月正好是月额；
  年薪 ÷ 12；周价的 A = 52 × 周额（每月 52/12 周，全年一致）。月份用日历月序号，所以
  半年的工作拿到的是那半年各月的份额，不是"自己的第 1–6 个月"。
* 数量 × 单价：先按单价摊月，再乘数量。
* 税：第 m 月预扣 = round(T(年内累计税前收入_m)) − round(T(累计_{m−1}))。一个日历年
  就是一个纳税年度，跨所有工作累计，不把一个月或一份工作当成自己的纳税年度。出局后
  不再有收入，也就不会对没挣到的钱计税：截至出局的预扣恰好等于"全年只有这些收入"
  时的应纳税额。税函数的 ``year`` 参数传计价基年（金额都是基年不变价）。

--------------------------------------------------------------------------
本模块不做的事
--------------------------------------------------------------------------

不算满足感、分数、城市排序；不让培训自动提高技能或工资（E1-5）；不接真实税表（V2 前核查）。

--------------------------------------------------------------------------
月度时间与精力（E1-4A，DECISIONS/0020）
--------------------------------------------------------------------------

每月第 7 步之后，用**这个月实际执行**的东西
调用 ``econ.life_resources`` 算时间与精力：实际住的房（含兜底）、实际吃的食品方案、拿到工资的工作、
执行了的服务、发生了的体验、参加了的培训、在服务期内的持有物。超限 → ``plan_infeasible``（整份计划
试算失败，什么都不提交，带诊断）；资料缺失 → ``data_error``。

执行修订（ADR0020 第十二节）：

* 出局当月（第 4 步付不起最低生活）照样检查**已知下限**：工资已经入账的工作照记工时与负担，没有到岗
  需求的通勤确定为零；已知用量超过预算或额度，就是 ``plan_infeasible``——资金出局不能绕过资源约束。没超
  也只标"只检查已知下限"，不说这个月通过了完整检查；
* 多份工作（包括两份到岗工作）可以同时执行，工资、工时、负担分别相加，税按年内累计合并计算；每个岗位
  每次到岗算一趟完整往返，岗位之间不合并行程。

E1-4B（DECISIONS/0021）：结算不读评分或偏好，也不计算反馈。它只做两件事——拒绝额度待定或来自另一个
反馈政策的年初状态（``ledger.feedback_state_problems``）；在下一年仍是行动年时，把结算产生的下一年状态标成
"额度待定"的候选（``energy_quota_basis.status = pending_feedback``）。额度由年度交接（``econ.life_year``）在
评分之后确定；终态与出局状态不需要下一年额度，原样带过去。候选状态在产生时记下当时协议的政策内容指纹与评分内容身份；
"来自另一个反馈政策"按编号、版本与内容一起判断（``common.energy_quota_problems``）。

E1-5A（DECISIONS/0023）：资格一律按年初状态判断
（``common.qualification_gaps``；当年完成的学习从下一行动年起才算，不再"完成次月解锁"）；培训完成时记下完成年月；
年末把这一年的学习与岗位事实写进结算结果，按 ``econ.life_career`` 授予能力（按能力去重）并写出实际岗位记录——
都在下一年候选状态与年度绑定封存之前完成。工资、税、计费、培训进度与资源核算仍是原来那一套，没有第二份。

E1-5B（DECISIONS/0026）：资金目标由 ``econ.life_savings`` 记录——年初应用
年度计划里的目标指令；每个完整过完的月份月末，把月末余额、当月实际支出与关联行动被拒的原因交给它；年末把这一年的
目标事实写进结算结果、把目标写进下一年候选状态（都在年度绑定封存之前）。**结算本身一分钱都不因目标而变**：
目标不扣款、不锁钱、不自动购买；购买与学费仍按原来的第 6 步、原来的顺序与资金检查执行。出局当月不再更新目标。

--------------------------------------------------------------------------
疾病、应急与恢复（E1-5C，DECISIONS/0027）
--------------------------------------------------------------------------

**先判定计划本身。** 同一份计划、同一个年初状态先做一遍"没有新疾病事件"的试算（年初已在延续的疾病照常按规则走）：
这一遍不可行，就是年初的计划问题（``plan_infeasible`` / ``input_error`` / ``data_error``），什么都不提交。这一遍可行，
再按真实事件执行；之后出现的任何不可行都由事件引起，**不回滚成"这一年没发生"**：停在那个月（``stopped``），之前的
月份全部保留。

**每个月的顺序。** 月初由 ``econ.life_health`` 定下这个月的状态（延续中的疾病照旧；健康月份才看指定事件或配对随机
数——一旦定下就算发生了）→ 工资（大病病假没有工资，工时、到岗与通勤为零；还没上过班的新岗位记"未入职"）→ 年内累计
税 → 最低生活检查（只检查不扣款）→ 住房、食品与声明的兜底 → **治疗**（年度计划选"条件允许就治"、治疗还有用、本城可得、
现金够才付；一次疾病最多付一次）→ 其他周期费用与一次性行动（大病暂停一切有参与时间的主动活动；按月服务暂停的月份
不付款；已付清的年付服务与持有物照样流逝服务月但不使用）→ 持有物与上课 → 月末余额与目标进展 → 时间与精力（恢复与
就医计入同一本账）→ 月末转移（治疗后恢复、到期自然恢复、或继续生病）。

**提交边界。** 生病月份的执行是试算：先不暂停；超出时间或精力就按年度计划声明的顺序多暂停一项再试；全部暂停仍超出
就放弃治疗再试；还不行就停止这个分身。试算失败的月份恢复到上月月末——没有工资、支出或学习，但月初的疾病事件与原因
保留（停止月）。同一个月的事件不重抽；之前的月份不受影响。资料缺口（要治疗而本城治疗资料未知）同样停在那个月。
"""

from __future__ import annotations

import copy
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from common.ids import Jurisdiction
from common.life_billing import annual_minor, monthly_installment, unit_minor
from common.life_contracts import (
    MAX_SUMMARY_CHARS,
    MAX_SUMMARY_ENTRIES,
    MINIMUM_NEEDS,
    MONTHS_PER_YEAR,
    CareerYear,
    CashFlowEntry,
    CityLifeCatalog,
    EmploymentRecord,
    EnergyQuotaBasis,
    EnvironmentOutcome,
    ExactAmount,
    ExecutedItem,
    ExitRecord,
    HeldAbility,
    Holding,
    HoldingService,
    ItemExecution,
    LearningYearRecord,
    LifeContractError,
    LifeOpportunity,
    LifeProtocol,
    MonthCommute,
    MonthPause,
    MonthRecord,
    MonthRejection,
    ParticipationRecord,
    PausedActivity,
    PersonYearState,
    PlanCheck,
    ResourceShortfall,
    SettlementProblem,
    SettlementResult,
    StopRecord,
    SyntheticTaxPolicy,
    TrainingProgress,
    TreatmentOffer,
    TreatmentOutcome,
    TreatmentRecord,
    YearAction,
    exact_minor_units,
    qualification_gaps,
    require_life_numeraire,
    to_fraction,
)
from common.money import Money, NumeraireMismatchError
from common.providers import TaxFunction
from econ.life_career import Completion, employment_after_year, grant_abilities
from econ.life_commute import CommuteUnavailableError, OnsiteJob, executed_commute
from econ.life_health import (
    ExtraLoad,
    HealthYearTracker,
    MonthStart,
    decide_treatment,
    recovery_load,
    treatment_load,
)
from econ.life_resources import Activity, blocked_month, month_resources, resource_year
from econ.life_savings import SavingsYearTracker
from ledger.life_catalog import (
    career_catalog_problems,
    career_state_problems,
    catalog_settlement_problems,
    health_catalog_problems,
    item_role,
    participation_activity,
    plan_problems,
    plan_state_problems,
    requested_months,
    resource_catalog_problems,
    resource_state_problems,
    state_problems,
)

#: 结算约定的版本。写进每一份结果；约定变了，旧结果与新结果不能混着比。
SETTLEMENT_CONVENTION = "e1-2-monthly-v1"

_NEED_LABEL = {"housing": "住房", "food": "食品"}


# ==========================================================================
# 税：合成测试替身与显式绑定
# ==========================================================================


@dataclass(frozen=True)
class SyntheticBracketTax:
    """按 ``SyntheticTaxPolicy`` 的税档累进计税（边际税率逐档叠加）。实现 ``TaxFunction``。

    与 ``worlds.taxes.SyntheticProgressiveTax`` 的关键区别：口径或税区不符时**报错**，
    不回显传进来的金额标签。内部用十进制计算，结果的舍入由结算统一做。
    """

    policy: SyntheticTaxPolicy

    def tax(self, gross: Money, jurisdiction: Jurisdiction, year: int) -> Money:
        p = self.policy
        if gross.numeraire != p.money_basis_id:
            raise NumeraireMismatchError(
                f"税策略 {p.policy_id} 只接受 {p.money_basis_id}，得到 {gross.numeraire}"
            )
        if jurisdiction != p.applies_to:
            raise ValueError(f"税策略 {p.policy_id} 适用于 {p.applies_to}，得到 {jurisdiction}")
        income = Decimal(repr(float(gross.amount)))
        if income < 0:
            raise ValueError("累计税前收入不能为负")
        lows = [Decimal(repr(b.lower)) for b in p.brackets]
        owed = Decimal(0)
        for i, bracket in enumerate(p.brackets):
            if income <= lows[i]:
                break
            top = income if i + 1 == len(lows) else min(income, lows[i + 1])
            owed += (top - lows[i]) * Decimal(repr(bracket.rate))
        return Money(float(owed), gross.numeraire)


@dataclass(frozen=True)
class LifeTaxBinding:
    """一次结算用的税：函数（``TaxFunction`` 注入接口）加上它**声明**的身份。

    结算只接受显式绑定：没有绑定、或城市没有配置税策略，都是数据错误——**没有配置
    不等于零税**。本轮唯一允许的状态是 ``synthetic_test_double``；真实税表要先过 V2 前
    的税表核查，并且不能继承合成测试的通过状态，所以这里没有 "verified"。
    """

    function: TaxFunction
    policy_id: str
    status: str
    money_basis_id: str
    jurisdiction: Jurisdiction

    def __post_init__(self) -> None:
        if self.status != "synthetic_test_double":
            raise LifeContractError(
                f"税绑定 {self.policy_id} 的状态是 {self.status!r}；本轮只接受合成测试替身 "
                "synthetic_test_double。真实税表须先通过 V2 前核查（币种、基年、当地 vs 平减"
                "收入、税辖区、适用年份、月度预扣与年度汇总）"
            )
        require_life_numeraire(self.money_basis_id, f"税绑定 {self.policy_id}")
        if not isinstance(self.function, TaxFunction):
            raise LifeContractError("税函数必须实现 common.providers.TaxFunction")
        if type(self.function).__module__.split(".")[0] == "worlds":
            raise LifeContractError(
                "worlds 里的现有税表与生活协议的计价口径尚未核查，且其接口不验证传入金额的"
                "口径（结果直接沿用传入的标签）；未经 V2 前税表核查，本轮暂不接入"
            )


def binding_from_policy(policy: SyntheticTaxPolicy) -> LifeTaxBinding:
    """由一条合成税策略构造绑定。身份字段全部取自策略本身，不另行声明。"""
    return LifeTaxBinding(
        function=SyntheticBracketTax(policy),
        policy_id=policy.policy_id,
        status=policy.status,
        money_basis_id=policy.money_basis_id,
        jurisdiction=policy.applies_to,
    )


# ==========================================================================
# 精度
# ==========================================================================


# 报价 → 年额 → 按月份额的换算在 ``common.life_billing``（唯一一份，加载器的最低方案
# 一致性检查也用它）。这里只剩税函数输出的舍入。


def round_minor_units(amount: float, digits: int) -> int:
    """金额 → 整数最小单位，四舍五入（ROUND_HALF_UP）。只用于税函数的输出。"""
    d = Decimal(repr(float(amount))).scaleb(digits)
    return int(d.quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ==========================================================================
# 入口
# ==========================================================================


def _problem(code: str, message: str, **kw: object) -> SettlementProblem:
    return SettlementProblem(code=code, message=message[:500], **kw)  # type: ignore[arg-type]


def _failed(status: str, problems: Sequence[SettlementProblem]) -> SettlementResult:
    return SettlementResult(
        status=status,  # type: ignore[arg-type]
        settlement_convention=SETTLEMENT_CONVENTION,
        problems=tuple(problems),
    )


#: E1-5C：一项活动为什么在这个月暂停（写进执行记录与健康记录）。
_PAUSE_WHY: dict[str, str] = {
    "major_illness_rule": "大病：暂停有参与时间的主动活动（世界规则）",
    "agent_pause_order": "生病：按年度计划声明的应急顺序暂停",
    "sick_leave": "大病病假：雇佣关系保留，没有工作、没有工资",
    "not_started": "大病：这份新岗位还没实际上过班，不算入职",
    # 收尾修订（F2）：代理自己的声明，不是资源不够，也不是付不起
    "agent_cancel_when_ill": "按年度应急声明，因本月患病取消购买（不扣款、不生成持有物、不顺延、不补购）",
}


def _needs_held_capability(o: LifeOpportunity) -> bool:
    """这项活动是否需要套餐之外、已持有的装备能力（这样的活动在本月购买之后结算）。"""
    return o.activity is not None and bool(o.activity.missing_capabilities(frozenset()))


def _resource_shortfall(result: SettlementResult) -> bool:
    """这次失败是不是只因为时间或精力超限（只有这种失败可以靠暂停活动或放弃治疗再试）。"""
    return (
        result.status == "plan_infeasible"
        and bool(result.problems)
        and all(p.resource is not None for p in result.problems)
    )


def _tax_binding_problems(catalog: CityLifeCatalog, tax: LifeTaxBinding) -> list[SettlementProblem]:
    out: list[SettlementProblem] = []
    if catalog.tax_policy_id is not None and tax.policy_id != catalog.tax_policy_id:
        out.append(
            _problem(
                "tax_policy_mismatch",
                f"{catalog.city} 配置的税策略是 {catalog.tax_policy_id}，传进来的是 {tax.policy_id}",
            )
        )
    if tax.money_basis_id != catalog.money_basis.numeraire_id:
        out.append(
            _problem(
                "tax_basis_mismatch",
                f"税绑定的口径 {tax.money_basis_id} 与城市口径 {catalog.money_basis.numeraire_id} "
                "不一致；不做隐式货币转换或改标签",
            )
        )
    if tax.jurisdiction != catalog.jurisdiction:
        out.append(
            _problem(
                "tax_jurisdiction_mismatch",
                f"税绑定适用于 {tax.jurisdiction}，城市税区是 {catalog.jurisdiction}",
            )
        )
    return out


def _screen(
    protocol: LifeProtocol,
    state: PersonYearState,
    action: YearAction,
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
) -> tuple[str, SettlementResult | None]:
    """结算前就能发现的问题，按检查顺序（参数类型 → 状态能否接收行动 → 城市与税的数据 → 年初状态 → 计划）。

    返回（检查到的阶段，失败结果）；全部通过时失败结果为 ``None``。``settle_year`` 与执行前检查 ``check_year_plan``（E1-7A）
    共用这一份：两边的检查顺序、问题代码与说明逐字相同，不另写一套。任何一步有问题都**不产生任何账目**。
    """
    for value, cls, name in (
        (protocol, LifeProtocol, "protocol"),
        (state, PersonYearState, "state"),
        (action, YearAction, "action"),
        (catalog, CityLifeCatalog, "catalog"),
    ):
        if type(value) is not cls:
            return "arguments", _failed(
                "input_error",
                [
                    _problem(
                        "argument_type",
                        f"{name} 必须是 {cls.__name__}，得到 {type(value).__name__}",
                    )
                ],
            )
    if not isinstance(tax, LifeTaxBinding):
        return "arguments", _failed(
            "data_error",
            [_problem("tax_binding", "结算需要显式的 LifeTaxBinding；没有配置不等于零税")],
        )
    if state.status == "terminal":
        return "state_status", _failed(
            "input_error",
            [
                _problem(
                    "state_not_active",
                    f"{state.clone_id} 已在终态（第 {state.year_index} 年 = N + 1），不接受行动",
                )
            ],
        )
    if state.status == "exited":
        return "state_status", _failed(
            "input_error",
            [
                _problem(
                    "state_not_active", f"{state.clone_id} 已在第 {state.year_index} 年出局，不复活"
                )
            ],
        )
    if state.status == "stopped":
        return "state_status", _failed(
            "input_error",
            [
                _problem(
                    "state_not_active",
                    f"{state.clone_id} 在第 {state.year_index} 年停止（应急规则不足或资料缺口），不接着结算；"
                    "正式重试在 E1-7",
                )
            ],
        )

    data = [_problem("catalog_data", m) for m in catalog_settlement_problems(catalog, protocol)]
    data += [
        _problem("career_data", m)
        for m in career_catalog_problems(protocol, catalog)
        + career_state_problems(protocol, state, catalog)
    ]
    data += _tax_binding_problems(catalog, tax)
    data += [
        _problem("resource_data", m)
        for m in resource_catalog_problems(protocol, catalog)
        + resource_state_problems(protocol, state)
    ]
    data += [_problem("health_data", m) for m in health_catalog_problems(protocol, catalog)]
    if data:
        return "city_data", _failed("data_error", data)

    # 状态本身有错时不再拿它去检查计划（会报出一串连带错误）；状态没问题就把计划的
    # 错误一次报全。
    inputs = state_problems(protocol, state, catalog)
    if inputs:
        return "state", _failed("input_error", inputs)
    inputs = plan_problems(protocol, catalog, action) + plan_state_problems(
        protocol, state, action, catalog
    )
    if inputs:
        return "plan", _failed("input_error", inputs)
    return "plan", None


def settle_year(
    *,
    protocol: LifeProtocol,
    state: PersonYearState,
    action: YearAction,
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
) -> SettlementResult:
    """结算一个分身的一年。返回五种状态之一（见 ``SettlementResult``）。

    检查顺序：参数类型 → 状态能否接收行动（终态与出局都不能）→ 城市与税的数据 →
    计划与状态（结算前就能发现的错误）→ 所选住房/食品是否根本给不了 → 按月执行。
    前四步任何一步有问题都**不产生任何账目**。前四步在 ``_screen``，与执行前检查 ``check_year_plan`` 共用（E1-7A 只做了这次
    提取，检查与执行的结果逐字节不变）。
    """
    _, failure = _screen(protocol, state, action, catalog, tax)
    if failure is not None:
        return failure

    # 先判定计划本身——同一份计划、同一个年初状态，不发生新疾病事件（年初已在延续的疾病照常走）。这一遍不可行就是年初的
    # 计划问题：什么都不提交，带着诊断退回（可修改重试，随机数不变）。
    trial = _Year(protocol, state, action, catalog, tax, new_events=False).settle()
    if trial.status not in ("completed", "exited"):
        return trial
    return _Year(protocol, state, action, catalog, tax).settle()


def check_year_plan(
    *,
    protocol: LifeProtocol,
    state: PersonYearState,
    action: YearAction,
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
) -> PlanCheck:
    """执行前检查（E1-7A，DECISIONS/0030）：结算前的全部检查，加上一遍**没有新随机事件**的逐月试算。什么都不提交。

    与 ``settle_year`` 用同一份检查（``_screen``）与同一个月度执行器（``_Year(..., new_events=False)``）：这就是结算本来就做的
    无事件试算（年初已在延续的疾病照常走）。返回检查停在哪个
    阶段与问题清单，不返回试算的账目或状态——调用方据此判断能不能让规划器修订，不能借它看到真实的未来事件。

    通过的计划交给 ``settle_year`` / ``run_life_year`` 真实执行：结算会再做一遍同样的检查与试算（纯函数，结果相同），之后由
    随机事件引起的停止是已经发生的生活，不回滚、不重抽。
    """
    events = type(protocol) is LifeProtocol
    stage, failure = _screen(protocol, state, action, catalog, tax)
    if failure is not None:
        return PlanCheck(
            stage=stage,  # type: ignore[arg-type]
            passed=False,
            status=failure.status,
            problems=failure.problems,
            new_events_possible=events,
        )
    trial = _Year(protocol, state, action, catalog, tax, new_events=False).settle()
    if trial.status in ("completed", "exited"):
        return PlanCheck(stage="passed", passed=True, new_events_possible=events)
    if trial.status == "stopped":  # 无事件试算里的失败直接退回，不会停止：停止只发生在真实事件之后
        raise AssertionError(
            f"{state.clone_id} 第 {state.year_index} 年的无事件试算得到了停止，与结算规则矛盾"
        )
    return PlanCheck(
        stage="no_event_trial",
        passed=False,
        status=trial.status,
        problems=trial.problems,
        new_events_possible=events,
    )


def settle_plans(
    *,
    protocol: LifeProtocol,
    state: PersonYearState,
    plans: Sequence[YearAction],
    catalog: CityLifeCatalog,
    tax: LifeTaxBinding,
) -> tuple[SettlementResult, ...]:
    """按年依次结算，上一年的 ``next_state`` 就是下一年的输入。

    遇到出局、终态或任何非 ``completed`` 的结果即停；之后的计划不再执行（出局与终态
    都不接受行动）。
    """
    results: list[SettlementResult] = []
    current = state
    for plan in sorted(plans, key=lambda p: p.year_index):
        r = settle_year(protocol=protocol, state=current, action=plan, catalog=catalog, tax=tax)
        results.append(r)
        if r.status != "completed" or r.next_state is None or r.next_state.status != "active":
            break
        current = r.next_state
    return tuple(results)


# ==========================================================================
# 一年的执行
# ==========================================================================


@dataclass
class _Line:
    """年度计划里的一项（或一项兜底执行）在这一年里的流水。"""

    order: int
    opp: LifeOpportunity
    role: str
    qty: int
    months: list[int]
    start_month: int | None = None
    end_month: int | None = None
    month: int | None = None
    fallback_of: _Line | None = None
    fallback: _Line | None = None
    covered: list[int] = field(default_factory=list)
    rejections: list[tuple[int, str]] = field(default_factory=list)
    paid: int = 0
    received: int = 0
    effective: tuple[int, int] | None = None
    downgraded_from: int | None = None
    downgrade_reason: str | None = None
    failure: tuple[int, str] | None = None  # 年付或报名失败；之后的月份按同一原因记被拒
    #: E1-5C：因为生病暂停的月份（月份、暂停种类、原因）。
    paused: list[tuple[int, str, str]] = field(default_factory=list)


@dataclass
class _Held:
    opp: LifeOpportunity
    kind: str  # durable_good | prepaid_service
    quantity: float
    acquired_year: int
    acquired_month: int | None
    remaining: int
    in_service_from_year: int | None
    line: _Line | None = None  # 年付服务：本年覆盖月同时记在买下它的那一项上
    covered: list[int] = field(default_factory=list)
    expired: bool = False
    #: E1-5C：服务期照样流逝、因为生病没有使用的月份（"付款 ≠ 参与"）。
    paused: list[int] = field(default_factory=list)


@dataclass
class _MonthCtx:
    """E1-5C：这个月的健康情况与限制（有健康规则的协议里每个月都有一份；健康月份的限制全部为空）。

    ``treatment_status`` 在不尝试治疗时就是原因（declined / no_benefit / unavailable / resources_insufficient）；尝试时由
    执行写成 treated 或 unaffordable。``pauses`` 是这一次试算里实际暂停的活动（试算失败就整份丢掉）。
    """

    start: MonthStart
    sick_leave: bool = False
    world_pause: bool = False
    agent_paused: frozenset[str] = frozenset()
    treat: bool = False
    offer: TreatmentOffer | None = None
    loads: list[ExtraLoad] = field(default_factory=list)
    treatment_status: str | None = None
    treatment_note: str | None = None
    treatment_paid: int = 0
    pauses: list[PausedActivity] = field(default_factory=list)

    @property
    def ill(self) -> bool:
        return self.start.state != "healthy"


@dataclass
class _Training:
    opportunity_id: str
    months_participated: int
    months_required: int
    fee_paid: bool
    enrolled_year: int
    enrolled_month: int
    completed_year: int | None = None  # E1-5A 才写
    completed_month: int | None = None


@dataclass(frozen=True)
class _Flow:
    month: int
    kind: str
    opportunity_id: str | None
    label: str
    amount: int  # 收入为正，税与支出为负


class _Year:
    def __init__(
        self,
        protocol: LifeProtocol,
        state: PersonYearState,
        action: YearAction,
        catalog: CityLifeCatalog,
        tax: LifeTaxBinding,
        *,
        new_events: bool = True,
    ) -> None:
        self.protocol = protocol
        self.state = state
        self.action = action
        self.catalog = catalog
        self.tax = tax
        self.rules = protocol.resource_rules
        self.resource_months: list = []
        self.year = state.year_index
        self.digits = catalog.money_basis.minor_unit_digits
        self.basis = catalog.money_basis.numeraire_id
        cash = exact_minor_units(state.cash.amount, self.digits)
        if cash is None:  # state_problems 已检查；到这里说明有人绕过了检查
            raise LifeContractError("现金精度细于最小记账单位")
        self.cash = cash
        self.cash_start = cash
        self.ytd_gross = 0
        self.ytd_tax = 0
        self.flows: list[_Flow] = []
        self.months: list[MonthRecord] = []
        #: 每个月唯一的通勤执行记录（DECISIONS/0042）；资源账、交通评分与通勤上限都读它。
        self.commutes: dict[int, MonthCommute] = {}
        self.exit: ExitRecord | None = None
        self.events: list[str] = []
        self.base: dict[str, LifeOpportunity] = {}
        for need in MINIMUM_NEEDS:
            spec = catalog.minimum_spec(need)
            base = catalog.opportunity(spec.baseline_opportunity_id) if spec else None
            if base is None:  # catalog_settlement_problems 已检查
                raise LifeContractError(f"{catalog.city}: 缺最低{_NEED_LABEL[need]}方案")
            self.base[need] = base
        self.lines: list[_Line] = []
        for i, item in enumerate(action.items):
            o = catalog.opportunity(item.opportunity_id)
            if o is None:  # plan_problems 已检查
                raise LifeContractError(f"不存在的机会 {item.opportunity_id}")
            self.lines.append(
                _Line(
                    order=i,
                    opp=o,
                    role=item_role(catalog, o),
                    qty=int(item.quantity),
                    months=list(requested_months(catalog, item)),
                    start_month=item.start_month,
                    end_month=item.end_month,
                    month=item.month,
                )
            )
        self.participation: list[ParticipationRecord] = []
        self.held: list[_Held] = []
        for h in state.holdings:
            o = catalog.opportunity(h.opportunity_id)
            if o is None:
                raise LifeContractError(f"不存在的持有物 {h.opportunity_id}")
            self.held.append(
                _Held(
                    opp=o,
                    kind="durable_good" if o.duration.kind == "durable_good" else "prepaid_service",
                    quantity=h.quantity,
                    acquired_year=h.acquired_year,
                    acquired_month=h.acquired_month,
                    remaining=h.effective_remaining_months or 0,
                    in_service_from_year=h.in_service_from_year,
                )
            )
        self.training: dict[str, _Training] = {
            t.opportunity_id: _Training(
                opportunity_id=t.opportunity_id,
                months_participated=t.months_participated,
                months_required=t.months_required,
                fee_paid=t.fee_paid,
                enrolled_year=t.enrolled_year,
                enrolled_month=t.enrolled_month,
                completed_year=t.completed_year,
                completed_month=t.completed_month,
            )
            for t in state.training
        }
        # 资金目标的年度记录（只读余额与已发生的支出，不动钱）
        self.savings = SavingsYearTracker(
            rules=protocol.savings_rules,
            state=state,
            action=action,
            catalog=catalog,
            digits=self.digits,
            cash_start_minor=cash,
        )
        self.goal_line = next(
            (ln for ln in self.lines if action.items[ln.order].goal_id is not None), None
        )
        # 疾病、应急与恢复
        self.new_events = new_events
        self.health = HealthYearTracker(
            rules=protocol.health_rules,
            source=protocol.illness_events,
            state=state,
            new_events=new_events,
        )
        self.ctx: _MonthCtx | None = None
        # 收尾修订（F2）：年度计划声明"生病月份取消"的一次性耐用品购买（计划检查保证每一项都能唯一找到）
        self.cancel_declared: tuple[str, ...] = tuple(action.emergency.cancel_purchases_when_ill)
        self.stop: StopRecord | None = None
        self.blocked_treatment: TreatmentOutcome | None = None
        self.start_positions = frozenset(state.employment.current_position_ids)

    # ------------------------------------------------------------------ 小工具

    def _money(self, minor: int) -> Money:
        return Money(float(Decimal(minor).scaleb(-self.digits)), self.basis)

    def _fmt(self, minor: int | None) -> str:
        if minor is None:
            return "—"
        return f"{Decimal(minor).scaleb(-self.digits):,.{self.digits}f}"

    def _flow(self, month: int, kind: str, oid: str | None, label: str, amount: int) -> None:
        self.flows.append(_Flow(month, kind, oid, label[:120], amount))

    def _event(self, month: int, text: str) -> None:
        self.events.append(f"第 {self.year} 年第 {month} 月：{text}"[:280])

    def _pay(self, ln: _Line, month: int, cost: int, label: str, *, cover: bool = True) -> None:
        if cost > self.cash:  # 每个调用点都先检查过；这里是最后一道保险，不是业务规则
            raise AssertionError(f"透支：{label} 需要 {cost}，现金 {self.cash}")
        self.cash -= cost
        ln.paid += cost
        if cover:
            ln.covered.append(month)
        if ln.effective is None:
            ln.effective = (self.year, month)
        self._flow(month, "expense", ln.opp.opportunity_id, label, -cost)

    def _why_not(self, o: LifeOpportunity) -> str | None:
        """这项机会这个月为什么给不了：缺货、未知、或不满足资格。给得了返回 None。"""
        if o.availability == "unavailable":
            return "确定缺货：本城没有这一项"
        if o.availability == "unknown":
            return "未知：没有数据，不当零价，也不当可得"
        # 资格按年初状态判断，年内不变（当年完成的学习从下一行动年起才算）；与加载、结算前检查与观察共用同一份判断。
        # 计划层面已经把不满足的项目挡掉，这里是最后一道保险。
        gaps = qualification_gaps(o, self.state)
        if gaps:
            return "不满足资格 " + "；".join(f"{g.tag}（{g.reason}）" for g in gaps)
        return None

    def _minimum_cost(self, need: str, month: int) -> int:
        quote = self.base[need].price
        if quote is None:  # minimum_living_problems 已检查
            raise LifeContractError(f"最低{_NEED_LABEL[need]}方案没有报价")
        return monthly_installment(annual_minor(quote, self.digits), month)

    # ------------------------------------------------------------------ 主循环

    def settle(self) -> SettlementResult:
        blocked = self._chosen_needs_never_provided()
        if blocked:
            return _failed("plan_infeasible", blocked)
        return self._settle_with_health()

    # ------------------------------------------------------------------ 疾病、应急与恢复（E1-5C）

    def _settle_with_health(self) -> SettlementResult:
        """E1-5C 的月循环。无事件试算里失败 = 年初的计划问题（直接退回）；真实执行里失败 = 事件之后应急规则不足或
        资料缺口：恢复到上月月末、记停止月，之前的月份全部保留。"""
        for month in range(1, MONTHS_PER_YEAR + 1):
            failure = self._health_month(month)
            if failure is not None:
                if not self.new_events:
                    return failure
                if not self.health.new_event_months:
                    # 没有任何新事件时，真实执行与无事件试算逐月相同——试算通过了，这里就不可能失败
                    raise AssertionError(
                        f"第 {month} 月在没有新事件的情况下失败，与年初的无事件试算矛盾：{failure.problems}"
                    )
                self._stop(month, failure)
                break
            if self.exit is not None:
                break
        return self._finish()

    def _snapshot_memo(self) -> dict[int, object]:
        """深拷贝时不复制的不可变对象（契约对象都是 frozen）。"""
        memo: dict[int, object] = {
            id(x): x for x in (self.protocol, self.state, self.action, self.catalog, self.tax)
        }
        for o in self.catalog.opportunities:
            memo[id(o)] = o
        return memo

    _MUTABLE = (
        "cash",
        "ytd_gross",
        "ytd_tax",
        "flows",
        "months",
        "exit",
        "events",
        "lines",
        "held",
        "training",
        "resource_months",
        "commutes",
        "savings",
        "goal_line",
        "participation",
    )

    def _snapshot(self) -> dict[str, object]:
        """上月月末的全部可变账目（不含健康记录器：月初的事件一旦定下就算发生了）。"""
        return copy.deepcopy({k: getattr(self, k) for k in self._MUTABLE}, self._snapshot_memo())

    def _restore(self, snap: dict[str, object]) -> None:
        for k, v in copy.deepcopy(snap, self._snapshot_memo()).items():
            setattr(self, k, v)

    def _health_month(self, m: int) -> SettlementResult | None:
        """一个月：月初定状态（提交）→ 按应急安排试算（可回退）→ 成功就记健康事实。失败时恢复到上月月末并返回问题。"""
        rules = self.protocol.health_rules
        start = self.health.month_start(m)
        self.blocked_treatment = None
        snap = self._snapshot()
        if start.state == "healthy":
            attempts: list[tuple[frozenset[str], bool, str | None]] = [(frozenset(), False, None)]
        else:
            plan = self.action.emergency
            assert plan is not None and start.episode is not None  # 计划检查保证新协议写了应急安排
            choice = plan.choice(start.state)
            offer = self.catalog.treatment(start.state)
            decision = decide_treatment(rules, choice, offer, start.episode)
            if decision == "data_gap":
                assert offer is not None
                self.blocked_treatment = TreatmentOutcome(
                    severity=start.state,  # type: ignore[arg-type]
                    plan_choice=choice,  # type: ignore[arg-type]
                    status="data_gap",
                    treatment_id=offer.treatment_id,
                    note=f"要治疗而本城的治疗资料未知（{offer.unknown_reason}）：资料缺口，不当免费也不当没有",
                )
                return _failed(
                    "data_error",
                    [
                        _problem(
                            "treatment_data_unknown",
                            f"第 {m} 月{'小病' if start.state == 'minor' else '大病'}，年度计划选择条件允许就治，"
                            f"但 {self.catalog.city} 的治疗条目 {offer.treatment_id} 资料未知（{offer.unknown_reason}）："
                            "这是资料 / 支持缺口，不是现实看不起病；不当成免费，也不当成没有",
                            month=m,
                        )
                    ],
                )
            order = [oid for oid in plan.pause_order if self._pausable_now(oid, m, start.state)]
            treat = decision == "attempt"
            attempts = [
                (frozenset(order[:i]), treat, None if treat else decision)
                for i in range(len(order) + 1)
            ]
            if treat:  # 全部暂停仍超出：放弃治疗再试一次
                attempts.append((frozenset(order), False, "resources_insufficient"))
        failure: SettlementResult | None = None
        for i, (paused, treat, status) in enumerate(attempts):
            if i:
                self._restore(snap)
            ctx = self._ctx_for(start, paused, treat, status)
            self.ctx = ctx
            try:
                result = self._month(m)
            finally:
                self.ctx = None
            if result is None:
                self._commit_health(m, ctx)
                return None
            failure = result
            if not _resource_shortfall(result):
                break  # 暂停与放弃治疗都改变不了的问题（例如疾病花费之后所选住房付不起又没有兜底）
        self._restore(snap)
        return failure

    def _ctx_for(
        self, start: MonthStart, paused: frozenset[str], treat: bool, status: str | None
    ) -> _MonthCtx:
        rules = self.protocol.health_rules
        assert rules is not None
        ctx = _MonthCtx(start=start)
        if start.state == "healthy":
            return ctx
        ctx.sick_leave = start.state == "major"
        ctx.world_pause = start.state == "major"
        ctx.agent_paused = paused
        ctx.treat = treat
        ctx.offer = self.catalog.treatment(start.state)
        ctx.treatment_status = status
        ctx.loads.append(recovery_load(rules, start.state))
        return ctx

    def _pausable_now(self, oid: str, m: int, state: str) -> bool:
        """应急顺序里的这一项这个月会不会实际参与（只有会参与的才值得暂停）。大病时它们已经被世界规则暂停。"""
        if state == "major":
            return False
        for ln in self.lines:
            if ln.opp.opportunity_id != oid:
                continue
            if ln.role == "training":
                return m in ln.months
            if ln.role == "experience":
                return (
                    m in ln.months
                )  # 一次体验是 [month]；不连续的几个月是 months（DECISIONS/0041）
            if ln.role == "periodic_service":
                return m in ln.months and participation_activity(self.catalog, ln.opp)
            if ln.role == "prepaid_service":
                return ln.start_month is not None and ln.start_month <= m
        return any(
            h.opp.opportunity_id == oid
            and not h.expired
            and (h.in_service_from_year is None or h.in_service_from_year <= self.year)
            and participation_activity(self.catalog, h.opp)
            for h in self.held
        )

    def _paused_kind(self, opp: LifeOpportunity, role: str) -> str | None:
        """这项活动这个月是否因为生病暂停、按哪条规则。住房、食品与岗位从不在这里暂停（岗位的病假另算）。"""
        ctx = self.ctx
        if ctx is None or not ctx.ill or role in ("housing", "food", "job"):
            return None
        if ctx.world_pause and participation_activity(self.catalog, opp):
            return "major_illness_rule"
        if opp.opportunity_id in ctx.agent_paused:
            return "agent_pause_order"
        return None

    def _pause(self, rec: _Line | None, opp: LifeOpportunity, role: str, m: int, kind: str) -> None:
        why = _PAUSE_WHY[kind]
        if rec is not None:
            rec.paused.append((m, kind, why))
        assert self.ctx is not None
        self.ctx.pauses.append(
            PausedActivity(opportunity_id=opp.opportunity_id, role=role, kind=kind, note=why)  # type: ignore[arg-type]
        )

    def _treatment_step(self, m: int) -> None:
        """住房与食品之后、其他行动之前：付治疗费（现金够才付；不透支、不借钱、不用未来工资）。"""
        ctx = self.ctx
        assert ctx is not None and ctx.offer is not None and ctx.offer.price is not None
        cost = unit_minor(ctx.offer.price, self.digits)
        if cost > self.cash:
            ctx.treatment_status = "unaffordable"
            ctx.treatment_note = (
                f"住房与食品之后现金 {self._fmt(self.cash)}，{ctx.offer.label}要 {self._fmt(cost)}："
                "不透支、不借钱、不用未来的工资"
            )
            return
        self.cash -= cost
        self._flow(m, "expense", ctx.offer.treatment_id, f"治疗：{ctx.offer.label}", -cost)
        ctx.treatment_status = "treated"
        ctx.treatment_paid = cost
        ctx.loads.append(treatment_load(ctx.offer))

    def _treatment_outcome(self, ctx: _MonthCtx) -> TreatmentOutcome | None:
        if not ctx.ill:
            return None
        plan = self.action.emergency
        assert plan is not None
        severity = ctx.start.state
        status = ctx.treatment_status
        assert status is not None
        offer = ctx.offer
        notes = {
            "treated": "付清治疗费、按时就医：当月月末恢复",
            "declined": "年度计划明确不治：按病程自然恢复",
            "no_benefit": "疾病本来就在这个月月末结束：治疗没有作用，不购买、不收费",
            "unavailable": "本城确定没有这种治疗：按病程自然恢复",
            "unaffordable": ctx.treatment_note or "住房与食品之后现金不够：不透支",
            "resources_insufficient": "按声明的顺序暂停所有能暂停的活动之后，加上就医仍超出这个月的时间或精力：放弃治疗",
        }
        treated = status == "treated"
        load = next((x for x in ctx.loads if x.category == "treatment"), None)
        return TreatmentOutcome(
            severity=severity,  # type: ignore[arg-type]
            plan_choice=plan.choice(severity),  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            treatment_id=None if offer is None else offer.treatment_id,
            amount_paid=self._money(ctx.treatment_paid) if treated else None,
            visit_hours=ExactAmount.of(load.hours) if treated and load is not None else None,
            burden=ExactAmount.of(load.hours * load.burden_per_hour)
            if treated and load is not None
            else None,
            note=notes[status][:280],
        )

    def _commit_health(self, m: int, ctx: _MonthCtx) -> None:
        """一次试算成功之后：记下这个月的健康事实与月末转移（出局当月不转移）。"""
        start = ctx.start
        label = {"minor": "小病", "major": "大病"}.get(start.state)
        if self.exit is not None and self.exit.month == m:
            self.health.exit_month(
                start,
                sick_leave=ctx.sick_leave,
                paused=tuple(ctx.pauses),
                notes=("出局当月：最低生活检查在治疗之前，没有治疗，也不计恢复负担",)
                if ctx.ill
                else (),
            )
            return
        outcome = self._treatment_outcome(ctx)
        treated = None
        if outcome is not None and outcome.status == "treated":
            assert ctx.offer is not None and outcome.amount_paid is not None
            treated = TreatmentRecord(
                treatment_id=ctx.offer.treatment_id,
                year_index=self.year,
                month=m,
                amount_paid=outcome.amount_paid,
                visit_hours=ctx.offer.visit_hours or 0.0,
                burden_per_hour=ctx.offer.burden_per_hour or 0.0,
            )
        record = self.resource_months[-1]
        assert record.month == m and record.work_relative_burden is not None
        month = self.health.month_end(
            start,
            sick_leave=ctx.sick_leave,
            paused=tuple(ctx.pauses),
            treatment=outcome,
            treated=treated,
            recovery=next((x for x in ctx.loads if x.category == "recovery"), None),
            work_relative_burden=record.work_relative_burden.fraction(),
        )
        if start.onset is not None:
            self._event(
                m, f"新发{label}" + ("（病假，没有工资）" if start.state == "major" else "")
            )
        if outcome is not None and outcome.status == "treated":
            self._event(m, f"治疗{label}，付 {self._fmt(ctx.treatment_paid)}，月末恢复")
        elif month.transition == "recovered_natural":
            self._event(m, f"{label}按病程自然恢复（月末）")

    def _stop(self, m: int, failure: SettlementResult) -> None:
        """停止在第 m 月：这个月的执行没有提交（账目已恢复到上月月末），事件与原因保留。"""
        assert self.health.started is not None
        problems = failure.problems
        kind = "data_gap" if failure.status == "data_error" else "emergency_insufficient"
        shortfalls = [p.resource for p in problems if p.resource is not None]
        if shortfalls:
            # 不沿用年初计划不可行的说法（"整份计划试算失败"）：这里之前的月份全部保留
            state = self.health.started.state
            label = {"minor": "小病", "major": "大病"}.get(state)
            plan = self.action.emergency
            order = "、".join(plan.pause_order) if plan is not None and plan.pause_order else ""
            how = (
                f"按年度计划的应急安排逐项暂停（{order}）、再放弃治疗之后"
                if order
                else "年度计划没有列可暂停的活动，放弃治疗之后"
            )
            when = (
                f"第 {m} 月{label}，{how}"
                if label
                else f"第 {m} 月（此前的疾病改变了这一年的生活）"
            )
            details = "；".join(
                f"{'时间' if x.resource == 'time' else '精力'}额度 {x.limit.value:g}，实际 {x.used.value:g}，"
                f"超出 {x.excess.value:g}，主要来自 "
                + "、".join(
                    f"{c.category}（{c.opportunity_id or c.task}）{(c.hours if x.resource == 'time' else c.burden).value:g}"
                    for c in x.top_contributors[:3]
                )
                for x in shortfalls
            )
            reason = f"{when}仍超出：{details}。这个月的执行没有提交，此前的月份全部保留；环境不替分身删掉其他活动或少上班"
        else:
            reason = "；".join(p.message for p in problems)
        self.stop = StopRecord(
            year_index=self.year,
            month=m,
            kind=kind,  # type: ignore[arg-type]
            reason=reason[:500],
            problem_codes=tuple(p.code for p in problems),
            shortfalls=tuple(p.resource for p in problems if p.resource is not None),
        )
        what = (
            "资料缺口" if kind == "data_gap" else "疾病之后的应急安排仍满足不了时间、精力或住房食品"
        )
        self.health.blocked(
            self.health.started,
            treatment=self.blocked_treatment,
            notes=(f"停止月：{what}；这个月的执行没有提交，此前的月份全部保留"[:280],),
        )
        self.resource_months.append(
            blocked_month(
                self.state, m, f"停止月（{what}）：这个月的执行没有提交，诊断在停止记录里"
            )
        )
        self._event(m, f"模拟停止（{what}）；此前的生活保留")

    def _chosen_needs_never_provided(self) -> list[SettlementProblem]:
        """所选住房/食品本城确定缺货或没有数据，且没有声明兜底：结算前就不可行。"""
        out: list[SettlementProblem] = []
        for ln in self.lines:
            if ln.role not in MINIMUM_NEEDS or ln.role in self.action.minimum_fallback:
                continue
            if ln.opp.availability != "available":
                label = _NEED_LABEL[ln.role]
                out.append(
                    _problem(
                        f"{ln.role}_not_provided",
                        f"所选{label} {ln.opp.opportunity_id} 是 {ln.opp.availability}，给不了；"
                        f"计划没有为{label}声明兜底，环境不替分身挑替代方案",
                        item_order=ln.order,
                        month=ln.start_month,
                    )
                )
        return out

    def _month(self, m: int) -> SettlementResult | None:
        cash_start = self.cash
        first_flow = len(self.flows)

        # 第 2 步：收入
        gross = 0
        for ln in self.lines:
            if ln.role != "job" or m not in ln.months:
                continue
            why = self._why_not(ln.opp)
            if why is not None:
                ln.rejections.append((m, why))
                continue
            if self.ctx is not None and self.ctx.sick_leave:
                # E1-5C：大病整月病假——没有工资、工时、到岗与通勤。年初就在岗或今年已经领过薪的岗位保留雇佣关系；
                # 还没实际上过班的新岗位不算入职（不授予雇主身份）
                started = ln.opp.opportunity_id in self.start_positions or bool(ln.covered)
                self._pause(ln, ln.opp, "job", m, "sick_leave" if started else "not_started")
                continue
            assert ln.opp.compensation is not None
            g = ln.qty * monthly_installment(annual_minor(ln.opp.compensation, self.digits), m)
            ln.covered.append(m)
            ln.received += g
            gross += g
            if ln.effective is None:
                ln.effective = (self.year, m)
            self._flow(m, "income_gross", ln.opp.opportunity_id, f"工资：{ln.opp.label}", g)
        self.ytd_gross += gross
        tax_to_date = self._tax_to_date(m)
        if isinstance(tax_to_date, SettlementResult):
            return tax_to_date
        tax = tax_to_date - self.ytd_tax
        self.ytd_tax = tax_to_date
        if tax:
            self._flow(m, "income_tax", None, f"合成税（{self.tax.policy_id}，年内累计预扣）", -tax)
        # 第 3 步：入账
        self.cash += gross - tax
        after_income = self.cash

        # 第 4 步：最低生活检查（只检查，不扣款）
        min_h = self._minimum_cost("housing", m)
        min_f = self._minimum_cost("food", m)
        if self.cash < min_h + min_f:
            self._exit(m, min_h, min_f)
            self._close_month(m, cash_start, gross, tax, min_h + min_f, "failed", 0, first_flow)
            return self._check_resources(m, exit_month=True)

        # 第 5 步：住房、食品，然后其他周期性费用
        for need, reserve in (("housing", min_f), ("food", 0)):
            stop = self._pay_need(need, m, reserve)
            if stop is not None:
                return stop
        if self.ctx is not None and self.ctx.treat:
            self._treatment_step(m)  # E1-5C：住房与食品之后、其他行动之前
        for ln in self.lines:
            if ln.role == "periodic_service" and m in ln.months:
                self._pay_periodic(ln, m)
            elif ln.role == "prepaid_service" and ln.start_month == m:
                self._pay_prepaid(ln, m)

        # 第 6 步：一次性购买、体验、培训报名（按计划里的位置）。需要已持有装备能力的活动放到本月这些购买之后
        # （DECISIONS/0041）：同月买到、当月生效的装备当月就能用，活动能不能参加不取决于它与装备在计划里谁写在前面；
        # 其他购买之间的先后不变，这几项活动之间仍按位置。
        deferred: list[_Line] = []
        for ln in self.lines:
            if ln.role == "durable_good" and ln.month == m:
                self._buy_once(ln, m)
            elif ln.role == "experience" and m in ln.months:
                if _needs_held_capability(ln.opp):
                    deferred.append(ln)
                else:
                    self._buy_once(ln, m)
            elif ln.role == "training" and ln.start_month == m:
                self._enroll(ln, m)
        for ln in deferred:
            self._buy_once(ln, m)

        # 第 6b 步（DECISIONS/0042）：通勤——实际住处 × 这个月到岗的岗位 × 在用的功能 → 唯一的通勤执行记录，按它付通勤费。
        # 放在这个月的购买之后：同月买到、当月生效的东西（例如用车权）当月就算在用
        stop = self._commute_step(m)
        if stop is not None:
            return stop

        # 第 7 步：持有物服务、培训参与、月末余额
        self._serve_holdings(m)
        self._attend_training(m)
        for ln in self.lines:
            if ln.role == "prepaid_service" and ln.failure and m in ln.months:
                ln.rejections.append((m, ln.failure[1]))
        spent = after_income - self.cash
        self._close_month(m, cash_start, gross, tax, min_h + min_f, "passed", spent, first_flow)
        self.savings.month_end(
            m,
            self.cash,
            self._month_payments(first_flow),
            self._goal_line_failure(m),
            link_cancelled=self._goal_line_cancelled(m),
        )
        return self._check_resources(m)

    # ------------------------------------------------------------------ 通勤（DECISIONS/0042）

    def _home_in(self, m: int) -> LifeOpportunity | None:
        """这个月实际住的那一处（含兜底）；没有就是 None。"""
        homes = [
            rec.opp
            for ln in self.lines
            for rec in (ln, ln.fallback)
            if rec is not None and rec.role == "housing" and m in rec.covered
        ]
        if len(homes) > 1:
            raise LifeContractError(f"第 {m} 月实际住房有 {len(homes)} 处，不是恰好一处")
        return homes[0] if homes else None

    def _onsite_jobs(self, m: int) -> list[OnsiteJob]:
        """这个月实际上了班（拿到工资）、需要到岗的岗位。病假与没有执行的岗位不在里面。"""
        out = []
        for ln in self.lines:
            if ln.role != "job" or m not in ln.covered:
                continue
            job = ln.opp.resource.job if ln.opp.resource is not None else None
            if job is not None and job.onsite_days_per_month > 0:
                out.append(OnsiteJob(ln.opp, job.onsite_days_per_month))
        return out

    def _functions_in_use(self, m: int) -> frozenset[str]:
        """这个月在用的机会提供的功能：这个月付了款的按月服务，与这个月在服务期内、没有暂停的持有物（含当月买到、当月生效的）。
        只查不扣寿命。"""
        out: set[str] = set()
        for ln in self.lines:
            if ln.role == "periodic_service" and m in ln.covered:
                out.update(ln.opp.functions)
        for h in self.held:
            if h.expired or h.remaining <= 0:
                continue
            if h.in_service_from_year is not None and h.in_service_from_year > self.year:
                continue
            if self._paused_kind(h.opp, h.kind) is None:
                out.update(h.opp.functions)
        return frozenset(out)

    def _commute_step(self, m: int) -> SettlementResult | None:
        try:
            record = executed_commute(
                self.catalog, m, self._home_in(m), self._onsite_jobs(m), self._functions_in_use(m)
            )
        except CommuteUnavailableError as exc:
            return _failed(
                "plan_infeasible", [_problem("commute_unavailable", f"第 {m} 月：{exc}", month=m)]
            )
        except LifeContractError as exc:
            return _failed(
                "data_error",
                [_problem("commute_data", f"第 {m} 月的通勤资料不足：{exc}", month=m)],
            )
        cost = 0
        if record.paid is not None:
            cost = exact_minor_units(record.paid.amount, self.digits) or 0
        if cost > self.cash:
            return _failed(
                "plan_infeasible",
                [
                    _problem(
                        "commute_unaffordable",
                        f"第 {m} 月：通勤费 {self._fmt(cost)}，付完这个月的其他开支后现金只剩 {self._fmt(self.cash)}"
                        "（通勤费在每月的购买之后结清）",
                        month=m,
                        cash_available=self._money(self.cash),
                        required=self._money(cost),
                    )
                ],
            )
        if cost:
            self.cash -= cost
            modes = sorted({u.mode for u in record.uses})
            self._flow(m, "expense", None, f"通勤（{'、'.join(modes)}）", -cost)
        self.commutes[m] = record
        return None

    # ------------------------------------------------------------------ 资金目标（E1-5B）

    def _month_payments(self, first_flow: int) -> dict[str, int]:
        """这个月每项机会的实际支出（最小单位，正数）——读已经记下的现金流，不另算。"""
        out: dict[str, int] = {}
        for f in self.flows[first_flow:]:
            if f.kind == "expense" and f.opportunity_id is not None:
                out[f.opportunity_id] = out.get(f.opportunity_id, 0) - f.amount
        return out

    def _goal_line_failure(self, m: int) -> str | None:
        """关联目标的那一条行动这个月被拒或报名失败的原因（没有就是 None）。"""
        ln = self.goal_line
        if ln is None:
            return None
        if ln.failure is not None and ln.failure[0] == m:
            return ln.failure[1]
        return next((why for month, why in ln.rejections if month == m), None)

    def _goal_line_cancelled(self, m: int) -> str | None:
        """收尾修订（F2）：关联目标的那一条购买这个月按年度应急声明、因病取消的原因（没有就是 None）。"""
        ln = self.goal_line
        if ln is None:
            return None
        return next(
            (
                f"第 {month} 月按年度应急声明、因当月患病取消，不顺延、不补购"
                for month, kind, _ in ln.paused
                if month == m and kind == "agent_cancel_when_ill"
            ),
            None,
        )

    # ------------------------------------------------------------------ 月度时间与精力（E1-4A）

    def _activities(self, m: int, exit_month: bool) -> list[Activity]:
        """这个月**实际**执行或在服务期内的东西，与记钱是同一份记录。

        年付服务与持续物品只从持有服务读（付款记录里的覆盖月是镜像，不再算一次）；出局当月只有拿到
        工资的工作——住房与食品没有获得，其他行动没有执行。休闲活动的次数与小时取自这个月唯一的那条参与记录
        （DECISIONS/0041 R9）：时间账、休闲评分、反馈与记忆读的是同一份执行事实。
        """
        out: list[Activity] = []
        for ln in self.lines:
            for rec in (ln, ln.fallback):
                if rec is None or m not in rec.covered:
                    continue
                if rec.role in ("prepaid_service", "durable_good"):
                    continue
                if exit_month and rec.role != "job":
                    continue
                if rec.role == "experience" and rec.opp.activity is not None:
                    p = self._completed_participation(rec, m)
                    out.append(Activity(rec.role, rec.opp, p.sessions, participation=p))
                    continue
                out.append(Activity(rec.role, rec.opp, rec.qty))
        if not exit_month:
            for h in self.held:
                if m in h.covered:
                    out.append(Activity(h.kind, h.opp, h.quantity))
        return out

    def _completed_participation(self, ln: _Line, m: int) -> ParticipationRecord:
        """这一项活动这个月的完成记录。付了款（覆盖了这个月）却没有恰好一条完成记录是程序错误，不另行推算。"""
        rows = [
            p
            for p in self.participation
            if p.plan_order == ln.order and p.month == m and p.status == "completed"
        ]
        if len(rows) != 1:
            raise LifeContractError(
                f"{ln.opp.opportunity_id} 第 {m} 月执行了却有 {len(rows)} 条完成的参与记录：执行事实不一致"
            )
        return rows[0]

    def _check_resources(self, m: int, *, exit_month: bool = False) -> SettlementResult | None:
        # E1-5C：生病月份的恢复与就医进同一本账（出局当月不计：这个月没有过完）
        extra = tuple(self.ctx.loads) if self.ctx is not None and not exit_month else ()
        try:
            record, short = month_resources(
                self.rules,
                self.state,
                m,
                self._activities(m, exit_month),
                exit_month=exit_month,
                extra=extra,
                commute=self.commutes.get(m),
            )
        except LifeContractError as exc:
            return _failed(
                "data_error",
                [_problem("resource_data", f"第 {m} 月的资源资料不足：{exc}", month=m)],
            )
        self.resource_months.append(record)
        if short:
            return _failed("plan_infeasible", [self._shortfall_problem(x) for x in short])
        return None

    def _shortfall_problem(self, x: ResourceShortfall) -> SettlementProblem:
        label, unit = (
            ("时间", "小时") if x.resource == "time" else ("精力", f"{self.rules.energy_unit}")
        )
        attr = "hours" if x.resource == "time" else "burden"
        top = "、".join(
            f"{c.category}（{c.opportunity_id or c.task}）{getattr(c, attr).value:g}"
            for c in x.top_contributors[:3]
        )
        if x.resource_check == "known_lower_bound":
            text = (
                f"第 {x.month} 月{label}超限（已知下限）：额度 {x.limit.value:g} {unit}，已经确定的用量至少 "
                f"{x.used.value:g}，至少超出 {x.excess.value:g}；主要来自 {top}。这个月付不起最低生活，但"
                "已入账工资对应的劳动就已超限——资金出局不能绕过资源约束。整份计划试算失败、没有提交账目"
            )
        else:
            text = (
                f"第 {x.month} 月{label}超限：额度 {x.limit.value:g} {unit}，实际 {x.used.value:g}，"
                f"超出 {x.excess.value:g}；主要来自 {top}。整份计划试算失败、没有提交账目；环境不会自动"
                "删掉活动、少上班或改买别的方案"
            )
        return _problem(f"{x.resource}_over_limit", text, month=x.month, resource=x)

    def _career_year(
        self,
    ) -> tuple[CareerYear, tuple[HeldAbility, ...], EmploymentRecord]:
        """这一年的学习与岗位事实、授予后的能力与岗位记录（DECISIONS/0023）。

        只读实际执行的记录：培训行实际参加的月份（``covered``）与实付学费，岗位行实际领到工资的月份。
        """
        s = self.state
        start_progress = {t.opportunity_id: t.months_participated for t in s.training}
        trainings = [ln for ln in self.lines if ln.role == "training"]
        completions = []
        for ln in trainings:
            t = self.training.get(ln.opp.opportunity_id)
            if t is not None and t.completed_year == self.year and t.completed_month is not None:
                completions.append(
                    Completion(
                        ln.opp.opportunity_id, t.completed_month, ln.order, tuple(ln.opp.grants)
                    )
                )
        abilities, per = grant_abilities(s.abilities, completions, self.year)
        learning = []
        for ln in trainings:
            oid = ln.opp.opportunity_id
            t = self.training.get(oid)
            before = start_progress.get(oid, 0)
            done = t is not None and t.completed_year == self.year
            granted, already = per.get(oid, ((), ()))
            learning.append(
                LearningYearRecord(
                    opportunity_id=oid,
                    fee_charged=self._money(ln.paid),
                    months_attended=tuple(ln.covered),
                    progress_before=before,
                    progress_after=t.months_participated if t is not None else before,
                    months_required=ln.opp.duration.completion_months or 1,
                    completed_month=t.completed_month if done and t is not None else None,
                    granted=granted,
                    already_held=already,
                    usable_from_year=self.year + 1 if granted else None,
                    months_paused=tuple(k for k, _, _ in ln.paused),
                )
            )
        if self.exit is not None:
            last = self.exit.month
        elif self.stop is not None:  # E1-5C：停止月没有提交，最后一个提交的月份是它的前一个月
            last = self.stop.month - 1
        else:
            last = MONTHS_PER_YEAR
        executed = [(ln.opp, tuple(ln.covered)) for ln in self.lines if ln.role == "job"]
        # E1-5C：保留雇佣关系的病假月份（病假不是辞职；未入职的不在里面）
        sick: dict[str, list[int]] = {}
        for ln in self.lines:
            if ln.role == "job":
                months = [k for k, kind, _ in ln.paused if kind == "sick_leave"]
                if months:
                    sick.setdefault(ln.opp.opportunity_id, []).extend(months)
        if last == 0:
            employment = s.employment  # 停止在第 1 月：这一年没有任何提交的月份，岗位记录保持年初
        else:
            employment = employment_after_year(
                s.employment, executed, self.year, last, sick or None
            )
        career = CareerYear(
            abilities_at_start=tuple(
                a.ability_id for a in s.abilities if a.usable_from_year <= s.year_index
            ),
            positions_at_start=tuple(s.employment.current_position_ids),
            learning=tuple(learning),
            positions=employment.positions,
            current_position_ids=employment.current_position_ids,
        )
        return career, abilities, employment

    def _next_quota_basis(self, next_status: str) -> EnergyQuotaBasis:
        """下一年还是行动年时，下一年额度待定（由年度交接确定）；终态与出局原样带过去。"""
        basis, policy = self.state.energy_quota_basis, self.protocol.energy_feedback
        if next_status != "active":
            return basis
        return EnergyQuotaBasis(
            base_level=basis.base_level,
            status="pending_feedback",
            policy_id=policy.policy_id,
            policy_version=policy.version,
            source_year=self.year,
            policy_fingerprint=policy.content_fingerprint,  # 候选状态在产生时绑定政策内容
            # 同时记下政策声明的评分内容身份（DECISIONS/0025）；结算只读政策，不调用评分器
            scoring_content_fingerprint=policy.scoring_content_fingerprint,
        )

    def _close_month(
        self,
        m: int,
        cash_start: int,
        gross: int,
        tax: int,
        required: int,
        check: str,
        spent: int,
        first_flow: int,
    ) -> None:
        delta = sum(f.amount for f in self.flows[first_flow:])
        if self.cash - cash_start != delta:
            raise AssertionError(
                f"第 {m} 月账不平：现金变动 {self.cash - cash_start}，流水合计 {delta}"
            )
        self.months.append(
            MonthRecord(
                year_index=self.year,
                month=m,
                cash_start=self._money(cash_start),
                income_gross=self._money(gross),
                income_tax=self._money(tax),
                minimum_required=self._money(required),
                minimum_check=check,  # type: ignore[arg-type]
                spent=self._money(spent),
                cash_end=self._money(self.cash),
            )
        )

    # ------------------------------------------------------------------ 税

    def _tax_to_date(self, m: int) -> int | SettlementResult:
        gross = self._money(self.ytd_gross)
        try:
            owed = self.tax.function.tax(
                gross, self.catalog.jurisdiction, self.catalog.money_basis.base_year
            )
        except (ValueError, TypeError, ArithmeticError) as exc:
            return _failed(
                "data_error", [_problem("tax_function", f"第 {m} 月调用税函数失败：{exc}", month=m)]
            )
        if (
            not isinstance(owed, Money)
            or owed.numeraire != self.basis
            or not math.isfinite(owed.amount)
        ):
            return _failed(
                "data_error",
                [
                    _problem(
                        "tax_output",
                        f"税函数返回 {owed!r}；必须是 {self.basis} 的有限金额",
                        month=m,
                    )
                ],
            )
        minor = round_minor_units(owed.amount, self.digits)
        if minor < 0 or minor > self.ytd_gross or minor < self.ytd_tax:
            return _failed(
                "data_error",
                [
                    _problem(
                        "tax_output",
                        f"第 {m} 月累计税 {owed.amount!r} 不合法：必须在 0 与累计收入之间，"
                        "且不随累计收入下降",
                        month=m,
                    )
                ],
            )
        return minor

    # ------------------------------------------------------------------ 住房与食品

    def _pay_need(self, need: str, m: int, reserve: int) -> SettlementResult | None:
        ln = next(
            x
            for x in self.lines
            if x.role == need and x.start_month <= m <= x.end_month  # type: ignore[operator]
        )
        minimum = self._minimum_cost(need, m)
        label = _NEED_LABEL[need]
        if ln.downgraded_from is not None:
            self._pay_fallback(ln, need, m, minimum)
            return None
        why = self._why_not(ln.opp)
        cost: int | None = None
        if why is None:
            assert ln.opp.price is not None
            cost = ln.qty * monthly_installment(annual_minor(ln.opp.price, self.digits), m)
        available = self.cash - reserve
        if cost is not None and cost <= available:
            self._pay(ln, m, cost, f"{label}：{ln.opp.label}")
            return None
        reason = why or (
            f"资金不足：需要 {self._fmt(cost)}，为另一项最低需求预留后可用 {self._fmt(available)}"
        )
        if need in self.action.minimum_fallback:
            ln.downgraded_from = m
            ln.downgrade_reason = reason
            self._event(m, f"{label}改为本城最低方案（{reason}）")
            self._pay_fallback(ln, need, m, minimum)
            return None
        min_h, min_f = self._minimum_cost("housing", m), self._minimum_cost("food", m)
        return _failed(
            "plan_infeasible",
            [
                _problem(
                    f"{need}_unaffordable" if why is None else f"{need}_not_provided",
                    f"第 {m} 月给不了所选{label} {ln.opp.opportunity_id}（{reason}）。"
                    f"最低住房 {self._fmt(min_h)} + 最低食品 {self._fmt(min_f)} 仍付得起，"
                    f"所以这不是出局；计划没有为{label}声明兜底，环境不替分身挑替代方案",
                    item_order=ln.order,
                    month=m,
                    cash_available=self._money(available),
                    required=self._money(cost) if cost is not None else None,
                )
            ],
        )

    def _pay_fallback(self, ln: _Line, need: str, m: int, minimum: int) -> None:
        fb = ln.fallback
        if fb is None:
            fb = _Line(
                order=ln.order,
                opp=self.base[need],
                role=need,
                qty=1,
                months=[k for k in ln.months if k >= m],
                fallback_of=ln,
            )
            ln.fallback = fb
        self._pay(fb, m, minimum, f"{_NEED_LABEL[need]}（声明的兜底：最低方案）：{fb.opp.label}")

    # ------------------------------------------------------------------ 其他行动

    def _pay_periodic(self, ln: _Line, m: int) -> None:
        why = self._why_not(ln.opp)
        if why is not None:
            ln.rejections.append((m, why))
            return
        paused = self._paused_kind(ln.opp, ln.role)
        if paused is not None:  # E1-5C：按月服务暂停的月份不付款、不使用
            self._pause(ln, ln.opp, ln.role, m, paused)
            return
        assert ln.opp.price is not None
        cost = ln.qty * monthly_installment(annual_minor(ln.opp.price, self.digits), m)
        if cost > self.cash:
            ln.rejections.append(
                (m, f"资金不足：需要 {self._fmt(cost)}，现金 {self._fmt(self.cash)}；不透支")
            )
            return
        self._pay(ln, m, cost, f"服务：{ln.opp.label}")

    def _pay_prepaid(self, ln: _Line, m: int) -> None:
        why = self._why_not(ln.opp)
        cost = 0
        if why is None:
            assert ln.opp.price is not None
            cost = ln.qty * unit_minor(ln.opp.price, self.digits)
            if cost > self.cash:
                why = (
                    f"资金不足：年付 {self._fmt(cost)}，现金 {self._fmt(self.cash)}；"
                    "年付不拆成分期，也不顺延"
                )
        if why is not None:
            ln.failure = (m, why)
            return
        self._pay(ln, m, cost, f"年付服务：{ln.opp.label}", cover=False)
        self.held.append(
            _Held(
                opp=ln.opp,
                kind="prepaid_service",
                quantity=float(ln.qty),
                acquired_year=self.year,
                acquired_month=m,
                remaining=MONTHS_PER_YEAR,
                in_service_from_year=None,
                line=ln,
            )
        )

    def _buy_once(self, ln: _Line, m: int) -> None:
        if (
            ln.role == "durable_good"
            and self.ctx is not None
            and self.ctx.ill
            and ln.opp.opportunity_id in self.cancel_declared
        ):
            # 收尾修订（F2）：月初生着病（新发或延续），按年度应急声明取消这一次购买——不查报价与现金、不扣款、不生成
            # 持有物、不顺延、不补购。只看这个月已经确定的病情，不读以后的事件
            self._pause(ln, ln.opp, ln.role, m, "agent_cancel_when_ill")
            return
        paused = self._paused_kind(ln.opp, ln.role) if ln.role == "experience" else None
        if paused is not None:  # E1-5C：生病时不去参加这次体验，也不付款（不顺延）
            self._pause(ln, ln.opp, ln.role, m, paused)
            if (
                ln.opp.activity is not None
            ):  # 只有休闲活动有参与记录；一次性享受服务只记暂停（DECISIONS/0041 R6）
                self._participate(ln, m, "paused", _PAUSE_WHY[paused])
            return
        why = self._why_not(ln.opp)
        used: tuple[str, ...] = ()
        act = ln.opp.activity if ln.role == "experience" else None
        if why is None and act is not None:
            held = self._capabilities_now()
            missing = act.missing_capabilities(frozenset(held))
            if missing:
                why = (
                    f"缺少装备能力 {list(missing)}：本月没有在服务期内、提供这种能力的装备（买了但没成功、取消、未生效或已到期都"
                    "不算）；不付款，也没有参加"
                )
            else:
                used = tuple(
                    dict.fromkeys(held[c] for c in act.requires if c not in act.kit_provides)
                )
        cost = 0
        if why is None:
            assert ln.opp.price is not None
            cost = ln.qty * unit_minor(ln.opp.price, self.digits)
            if cost > self.cash:
                why = (
                    f"资金不足：需要 {self._fmt(cost)}，现金 {self._fmt(self.cash)}；"
                    "一次性购买不顺延到以后的月份"
                )
        if why is not None:
            ln.rejections.append((m, why))
            if act is not None:
                self._participate(ln, m, "failed", why)
            return
        durable = ln.role == "durable_good"
        self._pay(ln, m, cost, f"{'购入' if durable else '体验'}：{ln.opp.label}")
        if act is not None:
            self._participate(ln, m, "completed", None, used)
        if durable:
            same_year = ln.opp.duration.effective_from == "same_year"
            ln.effective = (self.year, m) if same_year else (self.year + 1, 1)
            self.held.append(
                _Held(
                    opp=ln.opp,
                    kind="durable_good",
                    quantity=float(ln.qty),
                    acquired_year=self.year,
                    acquired_month=m,
                    remaining=ln.opp.duration.lifetime_months or 0,
                    in_service_from_year=None if same_year else self.year + 1,
                )
            )
            self._event(m, f"购入{ln.opp.label}" + ("" if same_year else "（下一年起用）"))
        else:
            self._event(m, ln.opp.label + (f" ×{ln.qty}" if ln.qty != 1 else ""))

    def _capabilities_now(self) -> dict[str, str]:
        """这个月**有效持有**的装备能力 → 提供它的第一件持有物（按取得顺序）。买到并当月生效的已在 ``held`` 里；
        已到期、下一年才起用的不算。只查不扣寿命：寿命只由持有服务按月扣（不会在“持有”与“使用”里各扣一次）。"""
        out: dict[str, str] = {}
        for h in self.held:
            if h.expired or h.remaining <= 0 or h.opp.equipment is None:
                continue
            if h.in_service_from_year is not None and h.in_service_from_year > self.year:
                continue
            for c in h.opp.equipment.provides:
                out.setdefault(c, h.opp.opportunity_id)
        return out

    def _participate(
        self, ln: _Line, m: int, status: str, reason: str | None, used: tuple[str, ...] = ()
    ) -> None:
        """记下这一项活动这个月的实际参与（唯一的一份；时间账、评分与记忆都读它）。"""
        a = ln.opp.activity
        assert a is not None
        done = status == "completed"
        n = ln.qty if done else 0
        self.participation.append(
            ParticipationRecord(
                plan_order=ln.order,
                opportunity_id=ln.opp.opportunity_id,
                month=m,
                status=status,  # type: ignore[arg-type]
                primary_category=a.primary_category,
                sessions=float(n),
                # 精确乘积再转浮点：输入最多两位小数，浮点的最短十进制写法就是这个乘积，资源账按十进制还原时不带二进制误差
                participation_hours=float(n * to_fraction(a.participation_hours)),
                overhead_hours=float(n * to_fraction(a.overhead_hours)),
                equipment_used=used,
                kit_capabilities=tuple(a.kit_provides) if done else (),
                reason=None if reason is None else reason[:MAX_SUMMARY_CHARS],
            )
        )

    def _enroll(self, ln: _Line, m: int) -> None:
        t = self.training.get(ln.opp.opportunity_id)
        if t is not None and t.fee_paid:
            return  # 往年已报名并付费：继续参与，不再收费
        why = self._why_not(ln.opp)
        cost = 0
        if why is None:
            assert ln.opp.price is not None
            cost = ln.qty * unit_minor(ln.opp.price, self.digits)
            if cost > self.cash:
                why = f"资金不足：学费 {self._fmt(cost)}，现金 {self._fmt(self.cash)}"
        if why is not None:
            ln.failure = (m, why)
            return
        self._pay(ln, m, cost, f"学费：{ln.opp.label}", cover=False)
        self.training[ln.opp.opportunity_id] = _Training(
            opportunity_id=ln.opp.opportunity_id,
            months_participated=t.months_participated if t else 0,
            months_required=ln.opp.duration.completion_months or 0,
            fee_paid=True,
            enrolled_year=self.year,
            enrolled_month=m,
        )

    def _serve_holdings(self, m: int) -> None:
        for h in self.held:
            if h.expired:
                continue
            if h.in_service_from_year is not None and h.in_service_from_year > self.year:
                continue
            paused = self._paused_kind(h.opp, h.kind)
            if paused is not None:
                # E1-5C：服务期照样流逝（付款 ≠ 参与），但这个月没有使用：不进时间账，也不带来收益
                h.paused.append(m)
                self._pause(h.line, h.opp, h.kind, m, paused)
            else:
                h.covered.append(m)
                if h.line is not None:
                    h.line.covered.append(m)
            h.remaining -= 1
            if h.remaining == 0:
                h.expired = True

    def _attend_training(self, m: int) -> None:
        for ln in self.lines:
            if ln.role != "training" or m not in ln.months:
                continue
            paused = self._paused_kind(ln.opp, ln.role)
            if paused is not None:
                # E1-5C：暂停的月份不上课：不增加进度、不重收学费，进度与能力保留，之后按计划继续
                self._pause(ln, ln.opp, ln.role, m, paused)
                continue
            t = self.training.get(ln.opp.opportunity_id)
            if t is not None and t.fee_paid and t.months_participated < t.months_required:
                t.months_participated += 1
                ln.covered.append(m)
                if ln.effective is None:
                    ln.effective = (self.year, m)
                if t.months_participated == t.months_required:
                    t.completed_year, t.completed_month = self.year, m
                    self._event(m, f"完成{ln.opp.label}；授予的能力从下一行动年起可用")
            else:
                reason = ln.failure[1] if ln.failure else "未报名"
                ln.rejections.append((m, f"未能参加：{reason}"))

    def _exit(self, m: int, min_h: int, min_f: int) -> None:
        lacking: tuple[str, ...] = ("housing", "food") if self.cash < min_h else ("food",)
        shortfall = min_h + min_f - self.cash
        self.exit = ExitRecord(
            year_index=self.year,
            month=m,
            reason="minimum_living_unaffordable",
            cash_available=self._money(self.cash),
            minimum_housing_cost=self._money(min_h),
            minimum_food_cost=self._money(min_f),
            shortfall=self._money(shortfall),
            unaffordable_needs=lacking,  # type: ignore[arg-type]
        )
        self._event(m, f"付不起最低住房与食品（差 {self._fmt(shortfall)}），出局")

    # ------------------------------------------------------------------ 收尾

    def _records(self, rec: _Line) -> tuple[ItemExecution, ExecutedItem]:
        rejected = [m for m, _ in rec.rejections]
        paused = [m for m, _, _ in rec.paused]  # E1-5C：因为生病暂停的月份
        # 降级之后的月份交给兜底那一条记录（在那里记执行或未到达），这里不再重复记。
        handed_over = (
            {k for k in rec.months if k >= rec.downgraded_from}
            if rec.downgraded_from is not None
            else set()
        )
        not_reached = [
            k
            for k in rec.months
            if k not in rec.covered
            and k not in rejected
            and k not in handed_over
            and k not in paused
        ]
        if rec.fallback_of is not None:
            status = "fallback_minimum"
            reason = (
                f"声明的兜底：{rec.fallback_of.opp.opportunity_id} 自第 "
                f"{rec.fallback_of.downgraded_from} 月起{rec.fallback_of.downgrade_reason}"
            )
        elif rec.downgraded_from is not None:
            status = "downgraded_to_minimum"
            reason = rec.downgrade_reason
        else:
            reached = [k for k in rec.months if k not in not_reached]
            if not reached:
                status = "not_reached"
            elif not rec.covered and not rejected and paused:
                cancelled = all(kind == "agent_cancel_when_ill" for _, kind, _ in rec.paused)
                status = "cancelled_when_ill" if cancelled else "paused"
            elif not rec.covered:
                status = "rejected"
            elif len(rec.covered) == len(reached):
                status = "executed"
            else:
                status = "partially_executed"
            reason = rec.rejections[0][1] if rec.rejections else None
            if reason is None and rec.paused:
                reason = f"第 {paused} 月{rec.paused[0][2]}"
        executed_qty = float(rec.qty) if rec.covered else 0.0
        item = ItemExecution(
            plan_order=rec.order,
            opportunity_id=rec.opp.opportunity_id,
            role=rec.role,  # type: ignore[arg-type]
            requested_quantity=float(rec.qty),
            executed_quantity=executed_qty,
            requested_months=tuple(rec.months),
            covered_months=tuple(rec.covered),
            effective_year=rec.effective[0] if rec.effective else None,
            effective_month=rec.effective[1] if rec.effective else None,
            amount_paid=self._money(rec.paid),
            amount_received=self._money(rec.received),
            status=status,  # type: ignore[arg-type]
            rejections=tuple(MonthRejection(month=m, reason=r[:200]) for m, r in rec.rejections),
            months_not_reached=tuple(not_reached),
            downgraded_from_month=rec.downgraded_from,
            fallback_for_plan_order=rec.fallback_of.order if rec.fallback_of else None,
            months_paused=tuple(
                MonthPause(month=k, kind=kind, reason=why[:200])  # type: ignore[arg-type]
                for k, kind, why in rec.paused
            ),
        )
        visible = ExecutedItem(
            opportunity_id=rec.opp.opportunity_id,
            requested_quantity=float(rec.qty),
            executed_quantity=executed_qty,
            status=status,  # type: ignore[arg-type]
            reason=reason[:280] if reason else None,
            covered_months=tuple(rec.covered),
            months_rejected=tuple(rejected),
            months_paused=tuple(paused),
        )
        return item, visible

    def _finish(self) -> SettlementResult:
        if self.cash - self.cash_start != sum(f.amount for f in self.flows):
            raise AssertionError("全年账不平：期末 − 期初 ≠ 实际现金流合计")

        records: list[_Line] = []
        for ln in self.lines:
            records.append(ln)
            if ln.fallback is not None:
                records.append(ln.fallback)
        pairs = [self._records(r) for r in records]
        notes = [
            f"合成记账验证：税是合成测试替身 {self.tax.policy_id}（{self.tax.status}），"
            "这份账不是真实税后生活成本",
        ]
        resources = resource_year(self.rules, self.state, self.resource_months)
        complete = [m.month for m in resources.months if m.resource_check == "complete"]
        partial = [m.month for m in resources.months if m.resource_check == "known_lower_bound"]
        stopped_at = [m.month for m in resources.months if m.resource_check == "blocked"]
        if stopped_at:  # E1-5C
            feasibility = "monthly_quota_checked_until_stop"
            scope = (
                f"第 {complete[0]}–{complete[-1]} 月完整检查，" if complete else ""
            ) + f"第 {stopped_at[0]} 月停止（执行没有提交），之后的月份未发生——不是全年通过"
        elif partial:
            feasibility = "monthly_quota_checked_until_exit"
            scope = (
                f"第 {complete[0]}–{complete[-1]} 月完整检查，" if complete else ""
            ) + f"第 {partial[0]} 月（出局当月）只检查已知下限，之后的月份未发生——不是全年通过"
        else:
            feasibility = "monthly_quota_checked"
            scope = "十二个月都做了完整检查"
        feedback = "满足感对下一年额度的反馈不在结算里，由年度交接在评分之后计算（E1-4B）"
        notes.append(
            f"时间与精力按月检查（{self.rules.version}，执行修订 {resources.rules_revision}）："
            f"{scope}。每月已安排时间 ≤ 净可分配小时 / 12（未安排的月底作废），每月负担 ≤ 额度；"
            f"负担系数与额度是合成设定；{feedback}"
        )
        if any(item.arrangement for item in self.action.items):
            notes.append("安排项只记录、本轮不定价")

        exited = self.exit is not None
        stopped = self.stop is not None
        n = self.protocol.simulation_years
        if exited:
            year_index, status = self.year, "exited"
        elif stopped:
            year_index, status = self.year, "stopped"
        elif self.year == n:
            year_index, status = self.protocol.terminal_year_index, "terminal"
        else:
            year_index, status = self.year + 1, "active"
        career = self._career_year()
        savings, goals = self.savings.finish(
            exit_month=self.exit.month if self.exit is not None else None,
            stopped_month=self.stop.month if self.stop is not None else None,
        )
        health_year, next_health = self.health.finish(status, cancel_declared=self.cancel_declared)
        if self.stop is not None:
            label = "资料缺口" if self.stop.kind == "data_gap" else "应急安排不足"
            notes.append(
                f"第 {self.stop.month} 月停止（{label}）：这是模拟器的支持边界，不是现实中活不下去，也不是出局；"
                "停止月的执行没有提交，此前的月份全部保留"
            )
        outcome = EnvironmentOutcome(
            person_id=self.state.person_id,
            clone_id=self.state.clone_id,
            year_index=self.year,
            money_basis_id=self.basis,
            executed=tuple(v for _, v in pairs),
            cash_flows=tuple(
                CashFlowEntry(
                    label=f.label,
                    amount=self._money(f.amount),
                    month=f.month,
                    kind=f.kind,  # type: ignore[arg-type]
                    opportunity_id=f.opportunity_id,
                )
                for f in self.flows
            ),
            cash_start=self._money(self.cash_start),
            cash_end=self._money(self.cash),
            settlement_status="exited" if exited else ("stopped" if stopped else "completed"),
            settlement_convention=SETTLEMENT_CONVENTION,
            months=tuple(self.months),
            items=tuple(i for i, _ in pairs),
            holdings_service=tuple(
                HoldingService(
                    opportunity_id=h.opp.opportunity_id,
                    kind=h.kind,  # type: ignore[arg-type]
                    covered_months=tuple(h.covered),
                    remaining_months_after=h.remaining,
                    expired=h.expired,
                    quantity=h.quantity,
                    paused_months=tuple(h.paused),
                )
                for h in self.held
            ),
            participation=tuple(self.participation),
            exit=self.exit,
            tax_policy_id=self.tax.policy_id,
            tax_status=self.tax.status,
            bookkeeping_label="synthetic_bookkeeping_only",
            energy_feasibility=feasibility,  # type: ignore[arg-type]
            resources=resources,
            notes=tuple(notes),
            career=career[0],
            savings=savings,
            health=health_year,
            stop=self.stop,
        )

        s = self.state
        next_state = PersonYearState(
            person_id=s.person_id,
            clone_id=s.clone_id,
            city=s.city,
            year_index=year_index,
            status=status,  # type: ignore[arg-type]
            exit=self.exit,
            stop=self.stop,
            occupation=career[2].occupation_summary(),
            money_basis_id=s.money_basis_id,
            cash=self._money(self.cash),
            holdings=tuple(
                Holding(
                    opportunity_id=h.opp.opportunity_id,
                    quantity=h.quantity,
                    acquired_year=h.acquired_year,
                    acquired_month=h.acquired_month,
                    remaining_months=h.remaining,
                    in_service_from_year=h.in_service_from_year,
                )
                for h in self.held
                if not h.expired
            ),
            time_budget_hours=s.time_budget_hours,
            energy=s.energy,
            resource_budget_basis=s.resource_budget_basis,
            energy_quota_basis=self._next_quota_basis(status),
            abilities=career[1],
            employment=career[2],
            training=tuple(
                TrainingProgress(
                    opportunity_id=t.opportunity_id,
                    months_participated=t.months_participated,
                    months_required=t.months_required,
                    fee_paid=t.fee_paid,
                    enrolled_year=t.enrolled_year,
                    enrolled_month=t.enrolled_month,
                    completed_year=t.completed_year,
                    completed_month=t.completed_month,
                )
                for t in self.training.values()
            ),
            savings_goals=goals,
            health=next_health,
            goals=tuple(s.goals),
            experience_summary=(tuple(s.experience_summary) + tuple(self.events))[
                -MAX_SUMMARY_ENTRIES:
            ],
        )
        return SettlementResult(
            status="exited" if exited else ("stopped" if stopped else "completed"),
            settlement_convention=SETTLEMENT_CONVENTION,
            outcome=outcome,
            next_state=next_state,
        )
