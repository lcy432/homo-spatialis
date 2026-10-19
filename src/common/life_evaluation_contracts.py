"""E1-6A 单分身跨期评价的数据形状（`DECISIONS/0028`）。只有形状、局部约束与内容身份，没有经济数学。

一个分身在一座城实际生活了 N 年之后，评价回答两件事：

* **过去过得怎样**：同一次实际生活、经年度绑定核对过的逐月满足感 S_m，按声明的时间权重 w_m = δ^(m−1) 加权平均，
  得到 L_actual（T = 12N 个月都有合法评分才给）；
* **留下的资源还能支持怎样的生活**：从真实终态出发，按预先声明的少量基础生活模板（最多 6 个）在估值期（H = 12 个月、
  没有新发疾病的确定性条件）里真实结算、评分，取参考集合内能完整执行、完整评分的最高者 V_reference——不是预测，
  也不是对全部可能行动的全局最优。治疗选择是独立的估值分支（E1-6A 收尾 F3，评价版本 v2）：估值期初健康时每个模板一个
  分支（治疗选择未触发）；带着病进入估值期时，每个模板展开"条件允许就治"与"不治疗"两个分支，最多 12 次试算。

两者都完整时合成 J = [Σ_m δ^(m−1) S_m + Σ_k δ^(T+k−1) S_future] ÷ (W_actual + W_future)；N = 3、H = 12、δ = 1 时
J = 0.75 L_actual + 0.25 V_reference。J 是 [0, 1] 的无量纲分数，不是货币，也不是"现实幸福的百分比"。

计算在 ``econ.life_evaluation``（核对轨迹、跨月汇总、合成）与 ``econ.life_terminal_value``（估值上下文与参考方案）；
配置加载与跨对象检查在 ``ledger.life_evaluation_config``。这里的对象都是**内部对象**：本身、以及原样序列化后的字典都
不能进代理观察（登记在 ``internal_evaluation_models``，由 ``common.life_scoring_contracts.internal_models`` 合成）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import Field, model_validator

from common.ids import CityId
from common.life_contracts import (
    MONTHS_PER_YEAR,
    ContractModel,
    ExactAmount,
    ExitRecord,
    IdStr,
    KeyStr,
    Month,
    StopRecord,
    YearIndex,
    canonical_fingerprint,
)
from common.money import Money

#: 评价规格的版本、执行修订与内容格式。改变任何一项计算语义都换修订号，并在 ADR 里记录。
#: v2 / r2（2026-10-12，DECISIONS/0029）：生活模板与治疗分支分开（F3）；身份核对（F1）、已有物品先于新品供给（F2）、
#: 正式入口复查配置（F4）。不保留 v1 / r1 的执行器：旧规格明确拒绝，旧配置与 E1-6A 证据原样保留作历史。
EVALUATION_VERSION = "e1-6a-evaluation-v2"
EVALUATION_REVISION = "e1-6a-r2"
EVALUATION_CONTENT_SCHEMA = "e1-6a-evaluation-content-v2"
#: 已退役的版本 → 拒绝时的说明。
RETIRED_EVALUATION_VERSIONS = {
    "e1-6a-evaluation-v1": (
        "评价版本 e1-6a-evaluation-v1（参考方案里绑定了治疗选择）已由 e1-6a-evaluation-v2 取代：基础生活模板与治疗分支"
        "分开（DECISIONS/0029）。当前代码不再执行 v1 / r1；旧配置与 E1-6A 证据原样保留作历史"
    ),
}
#: 本轮只支持 12 个月的估值期；其他长度明确拒绝，不静默改成 12。
SUPPORTED_HORIZON_MONTHS = 12
#: 最多几个基础生活模板；估值期初健康时每个模板一次试算（≤ 6），带病时每个模板两次（≤ 12）。
MAX_LIFE_TEMPLATES = 6
MAX_VALUATION_TRIALS = 2 * MAX_LIFE_TEMPLATES

_SHA256 = r"^[0-9a-f]{64}$"
Sha256 = Annotated[str, Field(pattern=_SHA256)]

#: 生活模板里一个"槽位"扮演的角色。槽位在比较组里共用，各城只写它绑定到目录里的哪一项。
SlotRole = Literal["job", "housing", "food", "service", "durable"]
#: 一个估值分支的治疗选择：估值期初健康时"未触发"（没有新发疾病，治疗选择不起作用，不代表用户偏好）；带病时"条件允许
#: 就治"或"不治疗"，只针对正在延续的这场病。
TreatmentBranch = Literal["untriggered", "treat_if_possible", "decline"]


class PlanSlot(ContractModel):
    """比较组共用的一个方案槽位（例如"基本住房""较舒适的食品""已具资格的替代岗位"）。"""

    slot_id: KeyStr
    role: SlotRole
    description: str = Field(min_length=1, max_length=280)


class DurablePurchaseRule(ContractModel):
    """参考方案里的条件购买：``buy_if_not_held`` = 估值期初没有在用的同一物品（剩余服务月 > 0），才在 ``month`` 买一件；
    已经持有就不买（照用旧的，不重复付款，也不重置寿命）。买不起时这个方案按"计划的购买付不起"判为已知不可行。"""

    slot_id: KeyStr
    month: Month
    rule: Literal["buy_if_not_held"]


class LifeTemplate(ContractModel):
    """一个基础生活模板（v2）：岗位、住房、食品、服务与条件购买。**不含治疗选择**——治疗选择是独立的估值分支
    （``treatment_branching``），按估值期初的健康状态展开（E1-6A 收尾 F3）。每个分支最终变成一份普通年度计划，由现有
    结算执行。

    * ``jobs``：``current_positions`` 延续真实终态里的当前岗位（可以没有）；``qualified_alternative`` 改做 ``job_slot``
      绑定的岗位——只有估值期初已经合格（资格、职业、雇主都按年初状态核对）才适用，不默认挑最高工资；
    * 治疗选择不由医疗点数生成，也不由 LLM 猜；
    * 不新开课程、不续学未完成的课程（本轮的限制）。

    ``template_id`` 不含点号：分支编号是"模板编号"（健康期初）或"模板编号.treat / 模板编号.decline"（带病期初）。
    """

    template_id: KeyStr
    label: str = Field(min_length=1, max_length=120)
    jobs: Literal["current_positions", "qualified_alternative"]
    job_slot: KeyStr | None = None
    housing_slot: KeyStr
    food_slot: KeyStr
    service_slots: tuple[KeyStr, ...] = ()
    durable_purchases: tuple[DurablePurchaseRule, ...] = ()
    description: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _shape(self) -> LifeTemplate:
        if (self.jobs == "qualified_alternative") != (self.job_slot is not None):
            raise ValueError(
                f"{self.template_id}: 只有 qualified_alternative 写 job_slot，而且必须写"
            )
        if len(set(self.service_slots)) != len(self.service_slots):
            raise ValueError(f"{self.template_id}: 服务槽位重复")
        slots = [d.slot_id for d in self.durable_purchases]
        if len(set(slots)) != len(slots):
            raise ValueError(f"{self.template_id}: 同一件物品的条件购买只写一次")
        return self

    def used_slots(self) -> tuple[str, ...]:
        out = [self.housing_slot, self.food_slot, *self.service_slots]
        out += [d.slot_id for d in self.durable_purchases]
        if self.job_slot is not None:
            out.append(self.job_slot)
        return tuple(out)


class SlotTarget(ContractModel):
    """一座城的一个槽位绑定到什么：目录里的一项机会，或确定没有（``unavailable``），或资料未知（``unknown``）。
    确定没有与资料未知分开——前者让用到它的方案"已知不可行"，后者让方案"无法评价"。"""

    slot_id: KeyStr
    status: Literal["opportunity", "unavailable", "unknown"]
    opportunity_id: IdStr | None = None
    reason: str | None = Field(default=None, max_length=280)

    @model_validator(mode="after")
    def _shape(self) -> SlotTarget:
        if (self.status == "opportunity") != (self.opportunity_id is not None):
            raise ValueError(f"{self.slot_id}: 只有绑定到机会时写 opportunity_id，而且必须写")
        if self.status != "opportunity" and not self.reason:
            raise ValueError(f"{self.slot_id}: 确定没有或资料未知必须写原因")
        return self


class CitySlotBinding(ContractModel):
    """一座城对比较组全部槽位的绑定。各城槽位相同（方案类别、选择规则与资料要求相同），价格与供给可以不同。"""

    city: CityId
    targets: tuple[SlotTarget, ...]

    @model_validator(mode="after")
    def _unique(self) -> CitySlotBinding:
        ids = [t.slot_id for t in self.targets]
        if len(set(ids)) != len(ids):
            raise ValueError(f"{self.city}: 槽位绑定重复")
        return self

    def target(self, slot_id: str) -> SlotTarget | None:
        return next((t for t in self.targets if t.slot_id == slot_id), None)


def parse_discount_factor(text: str) -> Fraction:
    """δ 的十进制文字 → 精确分数。必须 0 < δ ≤ 1，最多 6 位小数；不接受科学计数法以外的花样。"""
    try:
        d = Decimal(text)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"δ = {text!r} 不是十进制数") from exc
    if not d.is_finite():
        raise ValueError(f"δ = {text!r} 不是有限数")
    q = Fraction(d)
    if not 0 < q <= 1:
        raise ValueError(f"δ 必须满足 0 < δ ≤ 1，得到 {text}")
    if (q * 10**6).denominator != 1:
        raise ValueError(f"δ = {text} 的精度细于 6 位小数")
    return q


class EvaluationSpec(ContractModel):
    """单分身跨期评价的规格（E1-6A；v2 起生活模板与治疗分支分开）。**内容身份**覆盖全部计算内容：时间权重 δ、估值
    长度 H、估值期事件条件、治疗分支规则、选择规则、合成公式、执行修订、槽位、基础生活模板与各城绑定；``spec_id``、
    ``note``、标签与说明文字不参与。同一个编号改了计算内容，就是另一个身份。δ 按数值参与（"1" 与 "1.0" 是同一个值）。"""

    spec_id: IdStr
    version: Literal["e1-6a-evaluation-v2"]
    status: Literal["exploratory_synthetic"]
    execution_revision: Literal["e1-6a-r2"]
    monthly_discount_factor: str = Field(min_length=1, max_length=20)
    valuation_horizon_months: int
    valuation_event_condition: Literal["no_new_illness"]
    #: 治疗分支规则：估值期初健康 → 每个模板一个分支（治疗选择未触发）；带着病 → 每个模板"条件允许就治"与"不治疗"两个
    #: 分支，只针对正在延续的这场病，不遍历未来疾病组合。
    treatment_branching: Literal["by_continuing_illness_v1"]
    selection_rule: Literal["best_complete_feasible_in_reference_set"]
    combination: Literal["month_weighted_actual_plus_reference_v1"]
    slots: tuple[PlanSlot, ...]
    life_templates: tuple[LifeTemplate, ...]
    city_bindings: tuple[CitySlotBinding, ...]
    note: str | None = Field(default=None, max_length=800)

    @model_validator(mode="before")
    @classmethod
    def _retired(cls, data: object) -> object:
        if isinstance(data, dict):
            retired = RETIRED_EVALUATION_VERSIONS.get(str(data.get("version")))
            if retired is not None:
                raise ValueError(retired)
        return data

    @model_validator(mode="after")
    def _consistent(self) -> EvaluationSpec:
        parse_discount_factor(self.monthly_discount_factor)
        if self.valuation_horizon_months != SUPPORTED_HORIZON_MONTHS:
            raise ValueError(
                f"本轮只支持 H = {SUPPORTED_HORIZON_MONTHS} 个月的估值期，得到 {self.valuation_horizon_months}——"
                "不静默改成 12"
            )
        if not 1 <= len(self.life_templates) <= MAX_LIFE_TEMPLATES:
            raise ValueError(
                f"基础生活模板要有 1–{MAX_LIFE_TEMPLATES} 个，得到 {len(self.life_templates)}"
            )
        ids = [p.template_id for p in self.life_templates]
        if len(set(ids)) != len(ids):
            raise ValueError(f"基础生活模板编号重复：{ids}")
        roles = {s.slot_id: s.role for s in self.slots}
        if len(roles) != len(self.slots):
            raise ValueError("槽位编号重复")
        for p in self.life_templates:
            # 逐对核对（不用字典收集：同一个槽位既当住房又当食品时，字典会让后一个角色覆盖前一个）
            want = [(p.housing_slot, "housing"), (p.food_slot, "food")]
            want += [(s, "service") for s in p.service_slots]
            want += [(d.slot_id, "durable") for d in p.durable_purchases]
            if p.job_slot is not None:
                want.append((p.job_slot, "job"))
            used = [s for s, _ in want]
            if len(set(used)) != len(used):
                raise ValueError(f"{p.template_id}: 同一个槽位在模板里用了不止一次 {used}")
            for slot, role in want:
                if roles.get(slot) != role:
                    raise ValueError(
                        f"{p.template_id}: 槽位 {slot} 没有声明为 {role}（声明的是 {roles.get(slot)}）"
                    )
        cities = [str(b.city) for b in self.city_bindings]
        if len(set(cities)) != len(cities):
            raise ValueError(f"城市绑定重复：{cities}")
        for b in self.city_bindings:
            got = {t.slot_id for t in b.targets}
            if got != set(roles):
                raise ValueError(
                    f"{b.city}: 必须为比较组的每个槽位写绑定（同一套方案类别），缺 {sorted(set(roles) - got)}、"
                    f"多 {sorted(got - set(roles))}"
                )
        return self

    @property
    def discount_factor(self) -> Fraction:
        return parse_discount_factor(self.monthly_discount_factor)

    def binding_for(self, city: str) -> CitySlotBinding | None:
        return next((b for b in self.city_bindings if str(b.city) == city), None)

    def slot_role(self, slot_id: str) -> str | None:
        return next((s.role for s in self.slots if s.slot_id == slot_id), None)

    def content(self) -> dict[str, object]:
        """参与内容身份的全部计算内容（标签、说明文字、``spec_id`` 与 ``note`` 不参与）。"""
        return {
            "content_schema": EVALUATION_CONTENT_SCHEMA,
            "version": self.version,
            "status": self.status,
            "execution_revision": self.execution_revision,
            "monthly_discount_factor": str(Decimal(self.monthly_discount_factor).normalize()),
            "valuation_horizon_months": self.valuation_horizon_months,
            "valuation_event_condition": self.valuation_event_condition,
            "treatment_branching": self.treatment_branching,
            "selection_rule": self.selection_rule,
            "combination": self.combination,
            "slots": [{"slot_id": s.slot_id, "role": s.role} for s in self.slots],
            "life_templates": [
                p.model_dump(mode="json", exclude={"label", "description"})
                for p in self.life_templates
            ],
            "city_bindings": [
                {
                    "city": str(b.city),
                    "targets": [
                        t.model_dump(mode="json", exclude={"reason"})
                        for t in sorted(b.targets, key=lambda x: x.slot_id)
                    ],
                }
                for b in sorted(self.city_bindings, key=lambda x: str(x.city))
            ],
        }

    @property
    def content_fingerprint(self) -> str:
        return canonical_fingerprint(self.content())


# ---------------------------------------------------------------------- 实际生活

#: 一条实际轨迹的分类（给 E1-6B 用）。口径见 ``OwnerCategory``。
TrajectoryStatus = Literal[
    "complete",  # N 年都正式交接、终态正常、T 个月都有合法评分
    "score_incomplete",  # 终态正常，但有月份的评分不完整（正权重的资料未知等）
    "life_exit",  # 付不起最低生活而出局（确认的生活出局）
    "stopped",  # E1-5C 停止（应急安排不足 / 资料缺口），原样保留种类与原因
    "feedback_blocked",  # 反馈信息不足：下一年额度无法确定，轨迹停在那一年
    "settlement_failed",  # 计划不可行或输入、资料错误：那一年没有生活结果
    "not_finished",  # 调用方给的轨迹还没到第 N 年
]
#: 所有者口径（2026-10-11）：确认出局 ≠ 模拟问题；停止不自动改称出局。
OwnerCategory = Literal[
    "completed_scored", "life_exit", "stopped_kept_as_is", "simulation_incomplete"
]
ActualMonthStatus = Literal[
    "scored",
    "incomplete",
    "exit_month",
    "after_exit",
    "stopped_month",
    "after_stop",
    "not_simulated",
]


class ActualMonth(ContractModel):
    """实际生活里的一个月：第几个月（1..T）、哪年哪月、评分状态、S_m 的十进制写法、权重 δ^(m−1) 与加权贡献。"""

    index: Annotated[int, Field(ge=1)]
    year_index: YearIndex
    month: Month
    status: ActualMonthStatus
    score_decimal: str | None = None
    weight: ExactAmount
    contribution: ExactAmount | None = None
    reason: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def _shape(self) -> ActualMonth:
        scored = self.status == "scored"
        if scored != (self.score_decimal is not None) or scored != (self.contribution is not None):
            raise ValueError(f"第 {self.index} 个月：只有已评分的月份有分数与加权贡献")
        return self


class YearTrace(ContractModel):
    """一年的交接摘要：结算状态与问题代码、交接结果、年度评分状态、年度绑定指纹。"""

    year_index: YearIndex
    handoff: str
    settlement_status: str
    #: 结算没有完成时的问题代码（例如 ``qualification_missing``）；E1-6B 用它区分"计划问题"与"资料缺失"。
    problem_codes: tuple[str, ...] = ()
    annual_status: str | None = None
    annual_mean: float | None = None
    binding_fingerprint: Sha256
    score_fingerprint: Sha256 | None = None
    #: 这一年封存绑定里的年初状态指纹（E1-6B 用第 1 年的这一项核对结果确实从冻结名单记下的那份开局出发）。
    start_fingerprint: Sha256 | None = None


class LifeEvent(ContractModel):
    """实际事件链里的一条：发病、治疗、病假、出局、停止（给 E1-6B 的出局事件链用）。"""

    year_index: YearIndex
    month: Month
    event_kind: Literal["illness_onset", "treated", "sick_leave", "exit", "stop"]
    detail: str = Field(min_length=1, max_length=400)


class RequirementTrajectory(ContractModel):
    """一条硬要求在一段生活（实际 N 年或一个参考方案的估值期）里的逐月汇总。单独报告，不进分数。"""

    need: KeyStr
    months_satisfied: Annotated[int, Field(ge=0)]
    months_not_satisfied: Annotated[int, Field(ge=0)]
    months_unknown: Annotated[int, Field(ge=0)]
    months_not_lived: Annotated[int, Field(ge=0)]
    requirement_summary: Literal[
        "satisfied_every_month", "violated", "unknown", "satisfied_in_lived_months"
    ]


class ActualLifeScore(ContractModel):
    """实际 N 年的跨月汇总。``l_actual`` 只有 T 个月都已评分才有；部分轨迹保留已知月份与缺失原因，不用已知月份平均冒充。"""

    status: Literal["complete", "incomplete"]
    horizon_years: Annotated[int, Field(ge=1)]
    months_total: Annotated[int, Field(ge=MONTHS_PER_YEAR)]
    discount_factor: str
    weight_total: ExactAmount
    known_weight: ExactAmount
    known_weighted_sum: ExactAmount
    l_actual: ExactAmount | None = None
    l_actual_display: float | None = None
    months: tuple[ActualMonth, ...]
    missing: tuple[str, ...] = ()
    hard_requirements: tuple[RequirementTrajectory, ...] = ()
    mean_of_annual_means: float | None = None
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> ActualLifeScore:
        if (self.status == "complete") != (self.l_actual is not None):
            raise ValueError("只有完整的实际生活才有 L_actual")
        if len(self.months) != self.months_total:
            raise ValueError("逐月明细必须列满 T 个月（没发生的月份也要写明）")
        return self


class MonthFact(ContractModel):
    """实际生活里一个月的事实摘要，原样取自封存的结算记录（月度账目与健康记录）。只给 E1-6B 的事件链引用年月与
    金额，不参与任何计算。出局当月 ``minimum_check = failed``、支出为 0；停止月没有提交执行，不列在这里。"""

    year_index: YearIndex
    month: Month
    cash_start: Money
    income_gross: Money
    income_tax: Money
    minimum_required: Money
    minimum_check: Literal["passed", "failed"]
    spent: Money
    cash_end: Money
    health_state: Literal["healthy", "minor", "major"] | None = None
    sick_leave: bool = False
    treatment_status: str | None = Field(default=None, max_length=40)
    treatment_paid: Money | None = None


#: E1-7A（DECISIONS/0030）：某一年的计划没有被接受、生活没有往下走的原因。规划阶段的停止，不是生活事件。
PlanningStopKind = Literal["revisions_exhausted", "city_data", "budget_exhausted"]
PLANNING_STOP_TEXT: dict[str, str] = {
    "revisions_exhausted": "规划修订耗尽",
    "city_data": "城市资料问题",
    "budget_exhausted": "全局调用上限用尽",
}


class PlanningStop(ContractModel):
    """E1-7A（DECISIONS/0030）：一个分身第 ``year_index`` 年的计划没有被接受，这一年与之后的生活都没有发生。

    * ``revisions_exhausted``：规划器在允许的次数内都没有给出能通过执行前检查的计划（格式、引用、资格、组合或资源问题）；
    * ``city_data``：执行前检查发现这座城要用的资料缺失或不完整（标明影响范围，不扩散成所有城市失败）；
    * ``budget_exhausted``：全局调用上限用完时，这一年还没有接受的计划。

    共享配置或未知程序错误不在这里：那个分身记为程序错误（``RunFailure``，不提取任何已知月份），批次暂停，没轮到的分身保留为
    未运行（顺序调度时没有别的分身正在规划）。

    之前已经提交的年份原样保留（部分轨迹交给 E1-6，已知月份照算）。这里不补分、不换人，也不把规划没完成说成生活出局。
    """

    year_index: YearIndex
    kind: PlanningStopKind
    attempts: Annotated[int, Field(ge=0)]
    problem_codes: tuple[str, ...] = ()
    detail: str = Field(min_length=1, max_length=400)


class TrajectoryRecord(ContractModel):
    """实际轨迹的核对与分类结果。``month_facts`` 是已经提交的每个月的事实摘要（E1-6B 的事件链只从这里与
    ``events``、出局 / 停止记录取事实）。``planning_stop``（E1-7A）：轨迹在某个正常交接之后停下，是因为下一年的计划没有被
    接受；没有用到时不序列化（已有结果的指纹逐字节不变）。"""

    status: TrajectoryStatus
    owner_category: OwnerCategory
    years: tuple[YearTrace, ...]
    events: tuple[LifeEvent, ...] = ()
    exit: ExitRecord | None = None
    stop: StopRecord | None = None
    terminal_fingerprint: Sha256 | None = None
    reasons: tuple[str, ...] = ()
    month_facts: tuple[MonthFact, ...] = ()
    planning_stop: PlanningStop | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _planning_stop_follows_last_year(self) -> TrajectoryRecord:
        s = self.planning_stop
        if s is not None:
            last = self.years[-1].year_index if self.years else 0
            if self.status != "not_finished" or s.year_index != last + 1:
                raise ValueError(
                    "规划停止只能接在一次正常交接（next_year_ready）的最后一年之后，写那一年的下一年"
                )
        return self


# ---------------------------------------------------------------------- 期末估值


class ValuationQuota(ContractModel):
    """估值期的月度精力额度：用实际第 N 年的完整年度满足感按原反馈政策算（关着时用基础值）。只存在于估值上下文。"""

    decision: Literal["applied", "off_disabled", "off_alpha_zero"]
    source_year: YearIndex
    base_quota: ExactAmount
    alpha: float
    score_decimal: str | None = None
    raw_quota: ExactAmount | None = None
    quota: ExactAmount
    terminal_quota: ExactAmount
    policy_id: IdStr
    policy_fingerprint: Sha256
    scoring_content_fingerprint: Sha256 | None = None
    source_binding_fingerprint: Sha256
    note: str = Field(min_length=1, max_length=400)


class ValuationHolding(ContractModel):
    """估值期初或某个参考方案估值期末的一件持有物（摘要）。``beyond_horizon_months`` 是估值期之后还剩的服务月——
    有限期估值不计这部分，也不另加转售残值。"""

    opportunity_id: IdStr
    quantity: Annotated[float, Field(gt=0)]
    remaining_months: Annotated[int, Field(ge=0)]
    beyond_horizon_months: Annotated[int, Field(ge=0)]


class ValuationStateSummary(ContractModel):
    """估值期初（``valuation_start``）或某个参考方案估值期末（``plan_end``）的状态摘要：现金、精力额度、持有物、能力、
    当前岗位、培训进度与正在延续的疾病。只是给报告与测试读的摘要，不是状态本身，不能拿来结算。"""

    point: Literal["valuation_start", "plan_end"]
    year_index: Annotated[int, Field(ge=1)]
    cash: Money
    energy_level: ExactAmount
    holdings: tuple[ValuationHolding, ...] = ()
    abilities: tuple[str, ...] = ()
    positions: tuple[IdStr, ...] = ()
    training: tuple[str, ...] = ()
    illness: str | None = None


class ValuationContext(ContractModel):
    """从真实终态构造估值上下文时做的全部转换（只在估值入口做；不改真实终态）。``start`` 是估值期初状态的摘要。"""

    actual_protocol_fingerprint: Sha256
    valuation_protocol_fingerprint: Sha256
    simulation_years: Annotated[int, Field(ge=1)]
    valuation_year: Annotated[int, Field(ge=2)]
    actual_source_fingerprint: Sha256
    valuation_source_fingerprint: Sha256
    valuation_source_mode: Literal["none"]
    terminal_fingerprint: Sha256
    valuation_start_fingerprint: Sha256
    dropped_expired_holdings: tuple[IdStr, ...] = ()
    quota: ValuationQuota
    start: ValuationStateSummary
    conversions: tuple[str, ...]


PlanStatus = Literal["complete", "infeasible", "excluded_hard_requirement", "unevaluable"]
PlanReason = Literal[
    "complete",
    "no_qualification",
    "slot_unavailable",
    "funds_exit",
    "plan_infeasible",
    "purchase_unaffordable",
    "hard_requirement_violated",
    "slot_unknown",
    "data_error",
    "score_incomplete",
    "hard_requirement_unknown",
    "preference_insufficient",
    "input_error",
    "unexpected_settlement",
]


class PlanItem(ContractModel):
    """参考方案变成的一条结算行动（槽位 → 目录里的机会）。"""

    plan_slot: KeyStr
    opportunity_id: IdStr
    slot_role: SlotRole
    start_month: Month | None = None
    end_month: Month | None = None
    month: Month | None = None


class PlanEvaluation(ContractModel):
    """一个估值分支（基础生活模板 + 治疗选择）在这个终态上的结果：适用性、实际结算、逐月分数、V_p、硬要求与未被选中
    的原因。``plan_id`` 是分支编号：估值期初健康时就是模板编号（治疗选择未触发）；带病时是"模板编号.treat"或
    "模板编号.decline"，``treatment_severity`` 是正在延续的那场病。

    ``health_states`` 是估值期每个月的月初健康状态（期末已在延续的疾病照常走完）；``new_onsets`` 是估值期新发疾病的
    次数（"没有新发疾病"的条件下必须为 0）；``end`` 是这个方案估值期末的状态摘要（只有结算正常完成时才有）。"""

    plan_id: IdStr
    template_id: KeyStr
    treatment_branch: TreatmentBranch
    treatment_severity: Literal["minor", "major"] | None = None
    status: PlanStatus
    reason_kind: PlanReason
    reasons: tuple[Annotated[str, Field(min_length=1, max_length=400)], ...] = ()
    items: tuple[PlanItem, ...] = ()
    skipped_purchases: tuple[str, ...] = ()
    settlement_status: str | None = None
    problem_codes: tuple[str, ...] = ()
    cash_start: Money | None = None
    cash_end: Money | None = None
    month_scores: tuple[str | None, ...] = ()
    v_plan: ExactAmount | None = None
    v_plan_display: float | None = None
    hard_requirements: tuple[RequirementTrajectory, ...] = ()
    health_states: tuple[str, ...] = ()
    new_onsets: Annotated[int, Field(ge=0)] = 0
    end: ValuationStateSummary | None = None
    action_fingerprint: Sha256 | None = None
    outcome_fingerprint: Sha256 | None = None
    score_fingerprint: Sha256 | None = None

    @model_validator(mode="after")
    def _shape(self) -> PlanEvaluation:
        if (self.status == "complete") != (self.v_plan is not None):
            raise ValueError(f"{self.plan_id}: 只有完整的方案有 V_p")
        if (self.status == "complete") != (self.reason_kind == "complete"):
            raise ValueError(f"{self.plan_id}: 完整方案的原因是 complete，其余不是")
        if self.status != "complete" and not self.reasons:
            raise ValueError(f"{self.plan_id}: 没有完整的方案必须写明原因")
        untriggered = self.treatment_branch == "untriggered"
        if untriggered != (self.treatment_severity is None):
            raise ValueError(f"{self.plan_id}: 只有带病期初的治 / 不治分支写病情，未触发的分支不写")
        want = (
            self.template_id
            if untriggered
            else f"{self.template_id}.{'treat' if self.treatment_branch == 'treat_if_possible' else 'decline'}"
        )
        if self.plan_id != want:
            raise ValueError(f"分支编号应是 {want}，得到 {self.plan_id}")
        return self


ValuationStatus = Literal["complete", "incomplete", "no_feasible_plan", "not_applicable"]


class TerminalValuation(ContractModel):
    """期末估值：参考集合内能完整执行、完整评分、已知满足硬要求的最高 V_p。不是预测，也不是全局最优。

    * ``complete``：没有"无法评价"的候选，且至少一个方案完整——``v_reference`` 与 ``selected_plan_id``；
    * ``incomplete``：有可能适用但无法评价的候选（资料未知、评分不完整、硬要求未知……）——不给 V_reference，
      ``best_known`` 只是"已知方案里的最好结果"（诊断），不冒充整个参考集合的估值；
    * ``no_feasible_plan``：全部方案已知不可行（或违反硬要求）——不填 0；
    * ``not_applicable``：实际生活没有正常结束（出局、停止、反馈受阻……）——估值器不复活它们。
    """

    status: ValuationStatus
    reasons: tuple[str, ...] = ()
    horizon_months: int
    discount_factor: str
    context: ValuationContext | None = None
    #: 治疗分支怎样展开：估值期初健康时每个模板一个分支；带着病时每个模板治 / 不治两个分支（只针对这场病）。
    branching: (
        Literal["healthy_start_single_branch", "continuing_illness_treat_or_decline"] | None
    ) = None
    continuing_severity: Literal["minor", "major"] | None = None
    trials: Annotated[int, Field(ge=0, le=MAX_VALUATION_TRIALS)] = 0
    plans: tuple[PlanEvaluation, ...] = ()
    selected_plan_id: IdStr | None = None
    selected_template_id: KeyStr | None = None
    selected_treatment: TreatmentBranch | None = None
    v_reference: ExactAmount | None = None
    v_reference_display: float | None = None
    best_known: ExactAmount | None = None
    best_known_plan_id: IdStr | None = None
    not_valued: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> TerminalValuation:
        complete = self.status == "complete"
        if (
            complete != (self.v_reference is not None)
            or complete != (self.selected_plan_id is not None)
            or complete != (self.selected_template_id is not None)
            or complete != (self.selected_treatment is not None)
        ):
            raise ValueError("只有完整的估值有 V_reference 与选中的分支（模板与治疗选择）")
        if len(self.plans) != self.trials:
            raise ValueError(f"记录了 {len(self.plans)} 个分支，试算次数写的是 {self.trials}")
        if self.status != "complete" and not self.reasons:
            raise ValueError("估值不完整、没有可行方案或不适用，必须写明原因")
        return self


class CombinedScore(ContractModel):
    """合成分 J 与两部分各占的权重。两部分都完整才有 J；不改成 L_actual，也不重新归一化。"""

    status: Literal["complete", "incomplete"]
    reasons: tuple[str, ...] = ()
    weight_actual: ExactAmount
    weight_future: ExactAmount
    share_actual: ExactAmount
    share_future: ExactAmount
    j: ExactAmount | None = None
    j_display: float | None = None

    @model_validator(mode="after")
    def _shape(self) -> CombinedScore:
        if (self.status == "complete") != (self.j is not None):
            raise ValueError("只有两部分都完整才有 J")
        if self.status != "complete" and not self.reasons:
            raise ValueError("J 为空必须写明原因")
        return self


class CloneEvaluation(ContractModel):
    """一个分身的跨期评价结果。绑定：评价规格的内容身份、实际轨迹（各年年度绑定）、终态、目录、税、评分输入、
    估值上下文与各方案的计划 / 结果 / 分数指纹；``result_fingerprint`` 是它们与全部数值的指纹，同样输入重放逐字节相同。"""

    spec_id: IdStr
    spec_version: str
    spec_content_fingerprint: Sha256
    execution_revision: str
    #: 身份取自已核对的封存年度绑定（不是外层结果对象的标签，E1-6A 收尾 F1）；重复分身序号取自封存的第 1 年年初
    #: 状态（``health.repeat_index``；协议没有健康规则时为空）。
    person_id: IdStr
    clone_id: IdStr
    city: CityId
    repeat_index: Annotated[int, Field(ge=0)] | None = None
    preference_source: str
    protocol_fingerprint: Sha256
    catalog_fingerprint: Sha256
    tax_fingerprint: Sha256
    scoring_inputs_fingerprint: Sha256
    trajectory: TrajectoryRecord
    actual: ActualLifeScore
    valuation: TerminalValuation
    combined: CombinedScore
    assumptions: tuple[str, ...] = ()
    result_fingerprint: Sha256

    def evidence(self) -> dict[str, object]:
        """参与 ``result_fingerprint`` 的全部内容（不含它自己）。"""
        return self.model_dump(mode="json", exclude={"result_fingerprint"})

    def expected_result_fingerprint(self) -> str:
        return canonical_fingerprint(self.evidence())

    @model_validator(mode="after")
    def _fingerprint(self) -> CloneEvaluation:
        if self.result_fingerprint != self.expected_result_fingerprint():
            raise ValueError("评价结果的指纹与内容不一致")
        return self


#: 评价侧的保留键：出现在观察里就说明评价参数、候选方案比较或估值结果漏了出去。不与可见对象的字段重名。
RESERVED_EVALUATION_KEYS: frozenset[str] = frozenset(
    {
        "monthly_discount_factor",
        "valuation_horizon_months",
        "valuation_event_condition",
        "reference_plans",
        "life_templates",
        "treatment_branching",
        "city_bindings",
        "l_actual",
        "l_actual_display",
        "v_reference",
        "v_reference_display",
        "v_plan",
        "best_known",
        "share_actual",
        "share_future",
        "selected_plan_id",
        "known_weighted_sum",
        "spec_content_fingerprint",
    }
)


def internal_evaluation_models() -> tuple[type[ContractModel], ...]:
    """E1-6A 评价侧的内部对象类型。它们本身、以及原样序列化后的字典，都不能进观察。"""
    return (
        EvaluationSpec,
        PlanSlot,
        DurablePurchaseRule,
        LifeTemplate,
        SlotTarget,
        CitySlotBinding,
        ActualMonth,
        YearTrace,
        LifeEvent,
        MonthFact,
        RequirementTrajectory,
        ActualLifeScore,
        PlanningStop,
        TrajectoryRecord,
        ValuationQuota,
        ValuationHolding,
        ValuationStateSummary,
        ValuationContext,
        PlanItem,
        PlanEvaluation,
        TerminalValuation,
        CombinedScore,
        CloneEvaluation,
    )
