"""生活模型的可见观察投影（E1-1 `DECISIONS/0016` 第三节；E1-2 `DECISIONS/0017`）。

**主机制是白名单构造，不是黑名单扫描。** ``build_observation`` 从显式许可出发，逐字段
把允许的东西**复制**进一个新的 ``AgentObservation``；它从不 ``asdict`` 一个完整的
``PersonYearState`` / ``EnvironmentOutcome``，也不接受旧的 ``Persona``。新对象安全，是
因为它只由列出来的字段构成，而不是因为"没在旧答案类型黑名单里"。

最后那一遍扫描（``assert_payload_clean``）只是第二道防线：它同时拦旧答案类型、新的
内部类型、以及一组不该出现在 prompt 里的键名。

E1-2 起：只有 ``active`` 状态能构造观察（终态与出局都不再接受行动）；持有物的剩余期限
用月；城市的最低生活声明与分身的培训进度可见（它们是做计划要用的事实，不是偏好）；
年度结算之后的可见反馈由 ``visible_feedback`` 从结算结果里**逐字段**取出，结算结果本身
不能进 payload。

--------------------------------------------------------------------------
信息许可必须显式
--------------------------------------------------------------------------

``ObservationPermission`` 的每个字段**都没有默认值**，``build_observation`` 的
``permission`` 也没有默认值。没声明许可就不能构造观察，更不会默认落到最宽松的模式。

信息条件只有产品模式 ``product_stated_profile``：分身替用户本人生活，用户自己的陈述按许可可见。

--------------------------------------------------------------------------
一条语义边界（记在这里，免得以后忘）
--------------------------------------------------------------------------

如果将来可见的精力水平受"由真实偏好导出的满足感"影响，那么让代理看到精力，就等于
给了它一条**间接的偏好反馈**：它可以从"这样过一年之后精力变了多少"倒推出自己真正在乎
什么。所以：显示精力的观察会带上 ``dynamic_feedback_channel = True``，**动态生活反馈实验
与原来的静态文字理解实验不能标成同一种信息条件**。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_serializer

from common.ids import CityId, OccupationId
from common.life_aggregation_contracts import RESERVED_AGGREGATION_KEYS
from common.life_billing import money_of_minor, one_time_total_minor
from common.life_city_facts import RESERVED_FACT_LINK_KEYS, internal_city_fact_models
from common.life_contracts import (
    ABILITY_KIND_LABEL,
    HEALTH_STATUS_LABEL,
    RESERVED_HEALTH_KEYS,
    RESOURCE_RULES_REVISION,
    SAVINGS_GOAL_KIND_LABEL,
    SAVINGS_GOAL_STATUS_LABEL,
    AbilitySpec,
    AgeBand,
    BurdenBand,
    CareerRules,
    CareerYear,
    CityLifeCatalog,
    ContractModel,
    DurationSpec,
    EmergencyPlan,
    EmployerSpec,
    EmploymentRecord,
    EnergyFeedbackPolicy,
    EnvironmentOutcome,
    ExactAmount,
    GoalEvent,
    GoalMonth,
    GoalQuote,
    GoalRealization,
    HardRequirement,
    HealthBinding,
    HealthMonth,
    HealthRules,
    HealthState,
    HealthYear,
    HeldAbility,
    IllnessEpisode,
    IllnessEventSource,
    IndependentHealthInputDeclaration,
    InjectedIllness,
    InternalYearScore,
    ItemExecution,
    LearningYearRecord,
    LifeContractError,
    MonthlyResourceRules,
    MonthPause,
    MonthResources,
    ParticipationRecord,
    PausedActivity,
    PersonYearState,
    PlanCheck,
    PositionRecord,
    PriceQuote,
    QualificationGap,
    ResourceProfile,
    ResourceShortfall,
    ResourceYear,
    RiskCell,
    RiskDraw,
    RiskTable,
    SavingsGoal,
    SavingsRules,
    SavingsYear,
    SettlementProblem,
    SettlementResult,
    SeverityRules,
    StopRecord,
    TreatmentNeed,
    TreatmentOffer,
    TreatmentOutcome,
    TreatmentRecord,
    VisibleCareerYear,
    VisibleFeedback,
    VisibleGoalEvent,
    VisibleGoalProgress,
    VisibleHealthMonth,
    VisibleHealthYear,
    VisibleLearning,
    VisibleMonthResources,
    VisibleNotDone,
    VisibleParticipation,
    VisibleParticipationSpan,
    VisiblePause,
    VisiblePosition,
    VisibleSavingsYear,
    VisibleTreatmentResult,
    energy_quota_problems,
    exact_minor_units,
    health_rules_state_problems,
    qualification_gaps,
    to_fraction,
)
from common.life_evaluation_contracts import RESERVED_EVALUATION_KEYS
from common.life_planning_contracts import internal_planning_models
from common.life_product_profile import ProductHealthAnswer, ProductLifeProfile
from common.life_scoring_contracts import RESERVED_SCORING_KEYS, serialized_internal
from common.money import Money
from common.product_contracts import internal_product_models

InformationCondition = Literal["product_stated_profile"]


class ObservationPermission(ContractModel):
    """一次观察允许看到什么。**没有默认值**——每一项都得有人明确说了算。"""

    condition: InformationCondition
    show_stated_dimension_points: bool
    show_hard_requirements: bool
    show_free_text: bool
    show_energy_level: bool
    #: 用户自己明确给出的物质细分（DECISIONS/0034）。内部评分结果则任何模式都不进观察。
    show_stated_material_points: bool
    permission_version: str = Field(min_length=1)
    #: 独立健康回答是否可见（DECISIONS/0034）。
    show_health_answers: bool
    #: 用户确认的休闲兴趣与口味、本版未支持的原文是否可见（DECISIONS/0041）。
    show_interests: bool


def standard_product_permission(permission_version: str) -> ObservationPermission:
    """标准产品观察的许可（E1-4A 修复，所有者 2026-10-08 裁决；ADR0020 第十二节）。

    产品模式里分身替用户本人生活：用户自己的陈述（六维点数、物质分配、独立健康回答、硬要求、自由文本）
    都可见；做月度计划还要知道自己的每月时间预算、**每月精力额度及单位**、公开的资源成本与已执行生活的
    时间与负担反馈——普通产品情景不让分身靠猜隐藏额度做计划，所以 ``show_energy_level`` 显式打开。

    这是一个显式预设，不是全局默认：研究对照要隐藏精力，就自己构造 ``show_energy_level=False`` 的许可；
    隐藏后额度与汇总字段为空，但各项活动的每小时负担仍是公开成本，代理可能自己推算负担。显示精力的观察
    照旧标为动态反馈条件（``dynamic_feedback_channel``）。
    """
    return ObservationPermission(
        condition="product_stated_profile",
        show_stated_dimension_points=True,
        show_hard_requirements=True,
        show_free_text=True,
        show_energy_level=True,
        show_stated_material_points=True,
        permission_version=permission_version,
        show_health_answers=True,
        show_interests=True,
    )


# ---------------------------------------------------------------------- 可见的形状


class VisibleFoodPart(ContractModel):
    part_id: str
    mode: str
    meals_per_week: float
    #: ``null`` 是资料未知（不是 0）
    fresh_meals_per_week: float | None
    cuisine: str | None


class VisibleFoodPlan(ContractModel):
    """一个月的完整食品方案（DECISIONS/0041）。月费已含方案里的外食；时间写在做饭与外食小时里。

    目录里的说明文字（覆盖依据、时间依据、各部分备注）是审计信息，不复制：同样的意思已经在机会的 ``quality`` 与
    ``time_energy.note`` 里（也让请求不超过输入长度上限）。"""

    covers_basic_month: bool
    meals_per_week: float
    parts: tuple[VisibleFoodPart, ...]
    prep_hours_per_month: float
    dining_hours_per_month: float


class VisibleEnjoyment(ContractModel):
    """一项东西实际提供的享受（功能、方式、每月份数）。不含任何评分系数；依据文字不复制（见 ``VisibleFoodPlan``）。"""

    function: str
    mode: str
    units_per_month: float | None = Field(default=None, exclude_if=lambda v: v is None)
    unit_label: str | None = Field(default=None, exclude_if=lambda v: v is None)


class VisibleEquipment(ContractModel):
    provides: tuple[str, ...]


class VisibleActivity(ContractModel):
    """一次休闲活动的公开事实：类别、参与与准备往返小时、需要的装备能力、套餐本次提供的能力（空的不序列化）。"""

    primary_category: str
    tags: tuple[str, ...] = Field(default=(), exclude_if=lambda v: v == ())
    participation_hours: float
    overhead_hours: float
    requires: tuple[str, ...] = Field(default=(), exclude_if=lambda v: v == ())
    kit_provides: tuple[str, ...] = Field(default=(), exclude_if=lambda v: v == ())


class VisibleOpportunity(ContractModel):
    """一项机会的公开事实（逐字段复制）。空的事实——没有规格、没有工资、不是岗位、没有资格标签、没有安排选项、没有属性、
    学完不授予能力——不序列化：键不出现就是“没有”，不是“未知”（生活扩展把样例目录扩大了一倍，这样请求才不超过输入长度
    上限）。价格照常写出：``null`` 是没有报价（缺货或资料未知，见 ``availability``）。"""

    opportunity_id: str
    category: str
    label: str
    spec: dict[str, str | int | float | bool] = Field(exclude_if=lambda v: not v)
    unit: str
    quality: str
    availability: Literal["available", "unavailable", "unknown"]
    price: PriceQuote | None
    compensation: PriceQuote | None = Field(exclude_if=lambda v: v is None)
    occupation: OccupationId | None = Field(exclude_if=lambda v: v is None)
    eligibility: tuple[str, ...] = Field(exclude_if=lambda v: not v)
    arrangement_options: dict[str, tuple[str | int | float | bool, ...]] = Field(
        exclude_if=lambda v: not v
    )
    duration: DurationSpec
    #: E1-3 增补：需求功能与方案属性是做计划要用的事实（属性本轮不评价，但可以知道）。
    functions: tuple[str, ...]
    attributes: dict[str, str | int | float | bool] = Field(exclude_if=lambda v: not v)
    #: E1-4A：公开的时间与精力事实（契约里的 ``resource``）——工时与到岗安排、每次或每月的小时、
    #: 使用频率、每小时负担、家务替代。可见字段不叫 resource：免得与溯源信息（source）混淆。
    time_energy: ResourceProfile
    #: 雇主（没有雇主的不序列化）、学习完成后授予的能力，以及"按年初状态现在能不能选、还缺什么"（DECISIONS/0023）。
    employer_id: str | None = Field(default=None, exclude_if=lambda v: v is None)
    grants: tuple[str, ...] = Field(exclude_if=lambda v: not v)
    #: 没有资格要求的机会不序列化。
    qualification: VisibleQualification | None = Field(default=None, exclude_if=lambda v: v is None)
    #: 生活扩展（DECISIONS/0041）：食品方案、享受、装备、活动的公开事实。没有的不序列化。
    food_plan: VisibleFoodPlan | None = Field(default=None, exclude_if=lambda v: v is None)
    enjoyment: tuple[VisibleEnjoyment, ...] = Field(default=(), exclude_if=lambda v: v == ())
    equipment: VisibleEquipment | None = Field(default=None, exclude_if=lambda v: v is None)
    activity: VisibleActivity | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_serializer("duration", "time_energy", when_used="json")
    def _compact(self, value: Any) -> dict[str, Any]:
        """期限与时间精力事实里空的项（null、空列表）不写进请求：键不出现就是“没有”（收尾 R1：只省重复，不删事实）。"""
        data = value.model_dump(mode="json")
        return {k: v for k, v in data.items() if v is not None and v != [] and v != {}}


class VisibleHolding(ContractModel):
    opportunity_id: str
    quantity: float
    remaining_months: int | None
    in_service_from_year: int | None


class VisibleTraining(ContractModel):
    """培训进度：已参与几个月、需要几个月。完成后授予什么写在对应机会的 ``grants``（E1-5A）。"""

    opportunity_id: str
    months_participated: int
    months_required: int
    completed: bool
    #: 学费是否已结清（继续参加不再收费）、完成年月（还没完成的不序列化）。
    fee_paid: bool
    completed_year: int | None = Field(default=None, exclude_if=lambda v: v is None)
    completed_month: int | None = Field(default=None, exclude_if=lambda v: v is None)


class VisibleQualification(ContractModel):
    """E1-5A：按年初状态，这项机会今年能不能选；不能选时缺什么（展示未来目标，不等于允许执行）。"""

    status: Literal["available_now", "not_yet"]
    missing: tuple[str, ...] = ()


class VisibleAbility(ContractModel):
    """E1-5A：你已经有的一项能力。"""

    ability_id: str
    label: str
    kind: str
    scope: str
    source: str
    usable_from_year: int


class VisibleAbilityDefinition(ContractModel):
    """E1-5A：能力词表里的一项（岗位要求与课程授予里出现的能力 ID 是什么意思）。"""

    ability_id: str
    label: str
    kind: str
    description: str
    scope: str


class VisibleEmployer(ContractModel):
    employer_id: str
    label: str


class VisibleEmployment(ContractModel):
    """E1-5A：你开局陈述的职业、年初的当前岗位，以及这份记录来自开局声明还是上一年的实际执行。"""

    stated_occupation: OccupationId | None
    as_of: str
    current: tuple[VisiblePosition, ...]
    last_year: tuple[VisiblePosition, ...]


class VisibleCareerRules(ContractModel):
    """E1-5A：学习与岗位的规则说明、能力词表与雇主。"""

    version: str
    rules: tuple[str, ...]
    abilities: tuple[VisibleAbilityDefinition, ...]
    employers: tuple[VisibleEmployer, ...]


class VisibleMinimumNeed(ContractModel):
    """城市的最低住房/食品声明。代理要靠它判断哪些方案合格、兜底会落到哪一项。"""

    need: str
    baseline_opportunity_id: str
    baseline_quantity: float
    qualifying_opportunity_ids: tuple[str, ...]
    meets: str


class VisibleHealthAnswer(ContractModel):
    """E3-A：独立健康问卷里的一题（逐字段取自画像）。``planning_note`` 写明这一题给分身的读法；未答与暂不确定表示没有明确指令。"""

    question_id: str
    question_text: str
    answer_state: Literal["provided", "unsure", "unanswered"]
    option_label: str | None
    planning_note: str


class VisibleProfile(ContractModel):
    """用户陈述里**被许可**的那部分（六维产品画像，DECISIONS/0034）。没许可的字段在 ``not_permitted`` 里列出。

    ``six_dimension_points`` 是精确分数（六维合计 100，没有 healthcare）；``material_allocation`` 是物质内部的相对重要性（父项为 0
    时不适用）；``health_answers`` 是独立问卷（不占 100 分）。"""

    hard_requirements: tuple[HardRequirement, ...] | None
    free_text: str | None
    statement_kind: Literal["product_six_dimension_v1"]
    #: 下面几项没有许可时不出现（键不序列化），并在 ``not_permitted`` 里列出。
    six_dimension_points: dict[str, str] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    material_allocation_status: Literal["allocated", "not_applicable_parent_zero"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    material_allocation: dict[str, str] | None = Field(default=None, exclude_if=lambda v: v is None)
    health_questionnaire_version: str | None = Field(default=None, exclude_if=lambda v: v is None)
    health_answers: tuple[VisibleHealthAnswer, ...] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    #: 确认的休闲兴趣与口味（DECISIONS/0041）：只有 confirmed 带类别。本版未支持的原文（品牌、没认出的描述）只留存、不评分，
    #: 也不交给分身（与能力清单的说明一致，收尾 R7）。
    leisure_interest_status: str | None = Field(default=None, exclude_if=lambda v: v is None)
    leisure_interests: tuple[str, ...] | None = Field(default=None, exclude_if=lambda v: v is None)
    taste_status: str | None = Field(default=None, exclude_if=lambda v: v is None)
    cuisines: tuple[str, ...] | None = Field(default=None, exclude_if=lambda v: v is None)
    #: 因为许可没有给出的字段（显式列出，免得“没有这个键”被读成“用户没有回答”）。
    not_permitted: tuple[str, ...]


class VisibleHouseholdTask(ContractModel):
    task: str
    baseline_hours_per_month: float
    burden_per_hour: float
    description: str


class VisibleResourceBudget(ContractModel):
    """E1-4A：做计划要用的月度资源规则与预算，逐字段取自协议的资源规则与人的状态。

    精力额度只有许可了精力可见（``show_energy_level``）才有值；负担系数是公开的活动成本，照常可见——
    所以隐藏的是额度与汇总，不是"全部精力信息不可知"。四条 ``*_rule`` 是这些成本所依据的简化规则的文字说明。
    """

    rules_version: str
    rules_revision: str
    time_unit: str
    monthly_time_budget: ExactAmount
    reserved: str
    time_rule: str
    energy_unit: str
    monthly_energy_quota: ExactAmount | None
    energy_rule: str
    commute_rule: str
    commute_reference: str
    commute_burden_per_hour: float
    household_rule: str
    household_tasks: tuple[VisibleHouseholdTask, ...]


class VisibleSavingsGoal(ContractModel):
    """**代理可见**的一个资金目标（E1-5B）。只有事实与比较，没有分数。

    ``cash`` 是唯一的真实余额（同一本现金账）；``quote_now`` 是目录当前报价 × 数量（当前资料，不是价格预测）；
    ``shortfall = max(0, 报价 − 余额)``；``cash_covers_quote`` 只是比较，不代表扣除以后的生活费也负担得起。
    比较只给未结束的目标；已结束的目标只列历史与兑现依据。
    """

    goal_id: str
    kind: str
    kind_label: str
    opportunity_id: str
    label: str
    quantity: int
    target_year: int
    target_month: int
    note: str | None = None
    status: str
    status_label: str
    quote_at_creation: Money
    quote_source: str
    quote_now: Money | None = None
    cash: Money | None = None
    shortfall: Money | None = None
    cash_covers_quote: bool | None = None
    past_due: bool
    realization: str | None = None
    events: tuple[VisibleGoalEvent, ...] = ()


class VisibleSavings(ContractModel):
    """**代理可见**的目标储蓄（E1-5B）：现在未结束的目标、历史目标与规则说明。"""

    rules_version: str
    open_goal: VisibleSavingsGoal | None = None
    history: tuple[VisibleSavingsGoal, ...] = ()
    rules: tuple[str, ...]


class VisibleSeverityRule(ContractModel):
    """**代理可见**的一种病情的规则（E1-5C）：工作与活动怎样受影响、不治疗持续几个月、恢复要花的时间与负担。"""

    severity: str
    label: str
    work: str
    activities: str
    months_if_untreated: int
    recovery_hours_per_month: float
    recovery_burden_per_hour: float


class VisibleTreatment(ContractModel):
    """**代理可见**的本城一种病情的治疗（E1-5C）：可得性、报价、就医时间与负担、满足什么需求、资料未知的原因。"""

    severity: str
    label: str
    serves: str
    availability: str
    price: PriceQuote | None = None
    visit_hours: float | None = None
    burden_per_hour: float | None = None
    unknown_reason: str | None = None


class VisibleIllness(ContractModel):
    """**代理可见**的一次疾病（E1-5C）：病情、何时发病、已经病了几个月；结束了的还有何时、怎样恢复与付了多少。"""

    severity: str
    label: str
    onset_year: int
    onset_month: int
    months_ill: int
    ongoing: bool
    natural_end: str | None = None
    recovered_year: int | None = None
    recovered_month: int | None = None
    recovery: str | None = None
    treatment_paid: Money | None = None


class VisibleHealth(ContractModel):
    """**代理可见**的健康部分（E1-5C）：年龄、年初病情、病史、规则、本城治疗与信息条件说明。

    **没有**疾病与工作负担、年龄的关系（方向、概率表、系数）、随机数、未来事件，也没有健康项的评分参数（γ、H）。"""

    rules_version: str
    age_years: int
    state: str
    state_label: str
    ongoing: VisibleIllness | None = None
    history: tuple[VisibleIllness, ...] = ()
    severity_rules: tuple[VisibleSeverityRule, ...]
    treatments: tuple[VisibleTreatment, ...]
    rules: tuple[str, ...]
    information: str


class ObservationChannels(ContractModel):
    """这次观察处在什么信息条件下（E1-4B，DECISIONS/0021）。分开记，不用一个布尔值兼作几种含义。

    * ``energy_visible``：精力额度与精力用量是否显示（原有的 ``show_energy_level`` 许可）；
    * ``satisfaction_feedback``：满足感 → 下一年额度的反馈是否实际起作用——``quota_feedback`` 只在反馈开着且 α > 0 时；
      关闭的反馈、α = 0 都是 ``none``；
    * ``study_condition``：没有满足感反馈的产品条件，还是含生活反馈的动态条件。反馈开着时，即使隐藏精力数值，行动可行性
      的变化也可能透露评价——所以动态条件只看反馈是否起作用，不看额度显不显示。

    原有的 ``dynamic_feedback_channel``（= 精力是否可见）照旧保留作保守标识，不拿它判断反馈是否开启。
    """

    energy_visible: bool
    satisfaction_feedback: Literal["none", "quota_feedback"]
    study_condition: Literal["product_without_satisfaction_feedback", "dynamic_life_feedback"]
    #: 疾病风险的信息条件（DECISIONS/0027）。只实现默认条件：不告知疾病—工作负担关系、概率表、系数与未来事件；当前病情、
    #: 规则与费用公开。其他条件（给定性提示、给概率）登记为以后的工作。
    health_risk_information: Literal["default_no_risk_relation"]


class AgentObservation(ContractModel):
    """代理一年能看到的全部。只由下面这些字段构成。"""

    information_condition: InformationCondition
    permission_version: str
    dynamic_feedback_channel: bool
    year_index: int
    city: CityId
    city_label: str
    money_basis_id: str
    cash: Money
    time_budget_hours: float
    energy_level: float | None
    occupation: OccupationId | None
    holdings: tuple[VisibleHolding, ...]
    training: tuple[VisibleTraining, ...]
    goals: tuple[str, ...]
    experience_summary: tuple[str, ...]
    profile: VisibleProfile
    opportunities: tuple[VisibleOpportunity, ...]
    minimum_living: tuple[VisibleMinimumNeed, ...]
    last_feedback: VisibleFeedback | None
    #: 月度资源规则与预算（DECISIONS/0020）。
    resources: VisibleResourceBudget
    #: 信息条件的分项记录（见 ``ObservationChannels``）。
    channels: ObservationChannels
    #: 已有能力（没有时是空列表，明确"一项都没有"）、当前岗位与学习/岗位规则说明（DECISIONS/0023）。
    abilities: tuple[VisibleAbility, ...]
    employment: VisibleEmployment
    career_rules: VisibleCareerRules
    #: 自己的资金目标、真实余额、已知报价、差距、事件与规则说明（DECISIONS/0026）。
    savings: VisibleSavings
    #: 年龄、年初病情、病史、疾病规则、本城治疗与信息条件说明（DECISIONS/0027）。
    health: VisibleHealth


# ---------------------------------------------------------------------- 构造


CAREER_RULE_TEXTS: tuple[str, ...] = (
    "学习：一个月算有效参加，要满足三条——这个月在你的年度计划里、学费已经结清（零价的自主学习也算结清）、"
    "这个月实际执行（学习时间与负担计入当月的时间和精力）；只付钱或只写理由不增加进度",
    "完成：累计有效参加的月数达到课程要求就完成；跨年继续学习保留真实进度，已经付过的一次性学费不再收",
    "生效：当年完成的学习从下一个行动年起才算数，不改变当年的工资或可选岗位；同一项能力只算一次，不叠加等级",
    "资格：一律按年初状态判断——年初已有的能力、年初所在的岗位；计划里写了年初还不满足要求的项目，整份计划退回",
    "岗位：明确可得、并且年初满足要求的岗位，就按声明的安排执行（合成规则，不是现实录用结论；没有录用概率或竞争）；"
    "取得资格不等于自动换工作——要在年度计划里实际选择这份岗位，工资才来自它",
)


SAVINGS_RULE_TEXTS: tuple[str, ...] = (
    "目标只是年度计划里的软约束：不另开账户、不圈存、不扣款。你的余额仍是那一本现金账，买别的东西或额外支出"
    "都会让差距变大",
    "目标只能是一次性购买的耐用品或一门课的一次性学费；同一时刻只能有一个未结束的目标",
    "只能在年初的年度计划里设定、保留、取消、替换目标或改目标时点；不写就原样延续；年内只按真实交易更新进展",
    "“余额不少于报价”只是一条比较，不代表扣除以后的生活费也负担得起；钱够了也不会自动购买",
    "要兑现目标，必须在年度行动里写一条带 goal_id 的购买或报名；按这一条实际执行、全额付清来判断。付不起、缺货、"
    "报价未知或行动被拒都不算兑现；付清学费不等于学完或取得资格",
    "到目标年月仍未兑现只记为“到期未兑现”：不会出局，也不会自动延期；取消或替换目标不退款、不加钱",
    "设定目标、存钱或兑现本身不加满足感；买到的东西、付了学费之后的学习照原来的规则产生结果",
)


def health_rule_texts(rules: HealthRules) -> tuple[str, ...]:
    """E1-5C 的规则说明（公开）。数字来自协议的疾病规则；不含概率、风险档或评分参数。"""
    minor, major = rules.minor, rules.major
    return (
        "状态只有健康、小病、大病三种，一次只有一种病；新的疾病只可能在健康月份的月初发生，生病的月份延续原来的病",
        "月初的状态决定这个月：小病照常上班，但每月要多花恢复时间与负担；大病整月病假、没有工资，学习、体验与有参与时间的"
        "服务暂停（零小时的服务和洗衣机这类被动物品照常起作用；已经付清的年付服务照样流逝服务月，这个月不使用）",
        f"不治疗：小病持续 {minor.untreated_months} 个月、大病持续 {major.untreated_months} 个月后在月末自然恢复（发病月算"
        "第 1 个月，跨年接着数）；本来就会在这个月月末结束的病，治疗没有作用，不收费",
        "治疗：在年度计划的应急安排里为小病、大病各选“条件允许就治”或“不治”。条件允许 = 本城可得、治疗还有用、住房与食品"
        "之后现金够、这个月的时间与精力放得下；治疗当月月末恢复，但不补当月的工资或学习；一次疾病最多付一次",
        "付款顺序：最低生活检查（只检查不扣款）→ 住房、食品与你声明的兜底 → 治疗 → 其他行动；不透支、不借钱、不用未来的工资",
        "时间或精力不够时：按你声明的暂停顺序逐项暂停到够为止（只有学习、一次体验、有参与时间的服务或持有物能暂停）；全部暂停"
        "仍不够就放弃治疗；还不够，模拟在这个月停止——这是模拟器的支持边界，不是现实中活不下去，之前的生活全部保留",
        "生病时取消购买：可以在应急安排里列出这一年计划买的一次性耐用品（cancel_purchases_when_ill）。哪个月月初生着病（小病或"
        "大病，新发或延续），那个月计划的这次购买就取消：不扣款、没有这件物品，不顺延、不自动补购；健康的月份照常购买。课程学费、"
        "年付合同和其他交易不在这一项里",
        "本城治疗资料未知时，要治疗的话这个分身会因为资料缺口停止；未知不是免费，也不是没有。资料缺口不影响别的分身",
        "医疗点数（如果你填了）只告诉你的分身你多看重应对健康风险：它不改变生病的概率、病情有多难受或治疗效果，也不会替你"
        "自动选择治疗",
    )


HEALTH_INFORMATION_TEXT = (
    "默认信息条件：不告诉你模拟器里疾病与工作负担、年龄之间的关系（方向、概率表与系数），也不预告以后会不会生病；"
    "你知道自己的年龄、当前病情、规则与本城的治疗费用和时间。活动的时间与负担成本照常公开"
)


def _visible_illness(e: IllnessEpisode, rules: HealthRules, year: int) -> VisibleIllness:
    label = HEALTH_STATUS_LABEL[e.severity]
    natural_end = None
    if not e.ended:
        left = rules.severity_rules(e.severity).untreated_months - e.months_ill
        y, m = year, left  # 年初状态：本年第 left 月月末自然恢复（left ≥ 1）
        natural_end = f"不治疗的话，第 {y} 年第 {m} 月月末恢复（已经病了 {e.months_ill} 个月）"
    return VisibleIllness(
        severity=e.severity,
        label=label,
        onset_year=e.onset_year,
        onset_month=e.onset_month,
        months_ill=e.months_ill,
        ongoing=not e.ended,
        natural_end=natural_end,
        recovered_year=e.recovered_year,
        recovered_month=e.recovered_month,
        recovery=None
        if e.recovery is None
        else ("治疗后" if e.recovery == "treated" else "自然恢复"),
        treatment_paid=None if e.treatment is None else e.treatment.amount_paid,
    )


def _visible_health(
    rules: HealthRules, state: PersonYearState, catalog: CityLifeCatalog
) -> VisibleHealth:
    h = state.health
    assert h is not None
    work = {"continues": "照常上班", "sick_leave_unpaid": "整月病假，没有工资、工时、到岗与通勤"}
    acts = {
        "as_planned": "照计划进行（时间精力不够时按你的暂停顺序）",
        "paused": "学习、体验与有参与时间的服务暂停",
    }
    return VisibleHealth(
        rules_version=rules.version,
        age_years=h.age_years,
        state=h.status,
        state_label=HEALTH_STATUS_LABEL[h.status],
        ongoing=None if h.episode is None else _visible_illness(h.episode, rules, state.year_index),
        history=tuple(_visible_illness(e, rules, state.year_index) for e in h.history),
        severity_rules=tuple(
            VisibleSeverityRule(
                severity=sr.severity,
                label=HEALTH_STATUS_LABEL[sr.severity],
                work=work[sr.work],
                activities=acts[sr.participation],
                months_if_untreated=sr.untreated_months,
                recovery_hours_per_month=sr.recovery_hours_per_month,
                recovery_burden_per_hour=sr.recovery_burden_per_hour,
            )
            for sr in (rules.minor, rules.major)
        ),
        treatments=tuple(
            VisibleTreatment(
                severity=tr.severity,
                label=tr.label,
                serves=tr.serves,
                availability=tr.availability,
                price=tr.price,
                visit_hours=tr.visit_hours,
                burden_per_hour=tr.burden_per_hour,
                unknown_reason=tr.unknown_reason,
            )
            for tr in catalog.treatments
        ),
        rules=health_rule_texts(rules),
        information=HEALTH_INFORMATION_TEXT,
    )


_TREATMENT_STATUS_LABEL = {
    "treated": "已治疗（当月月末恢复）",
    "declined": "计划选择不治",
    "no_benefit": "治疗没有作用（本来就在这个月月末恢复），没有购买",
    "unavailable": "本城没有这种治疗",
    "unaffordable": "现金不够，没有治疗",
    "resources_insufficient": "时间或精力放不下，没有治疗",
    "data_gap": "本城治疗资料未知（资料缺口）",
}
_TRANSITION_LABEL = {
    "stays_healthy": "保持健康",
    "continues": "下个月继续生病",
    "recovered_treated": "治疗后月末恢复",
    "recovered_natural": "按病程月末自然恢复",
    "not_applicable": "—",
}
_PAUSE_LABEL = {
    "sick_leave": "病假（雇佣关系保留，没有工资）",
    "not_started": "新岗位还没上过班，不算入职",
    "major_illness_rule": "大病时暂停（规则）",
    "agent_pause_order": "按你的应急暂停顺序暂停",
    "agent_cancel_when_ill": "按你的年度应急声明，因本月患病取消这次购买（不扣款、不顺延、不补购）",
}


def _visible_health_month(x: HealthMonth) -> VisibleHealthMonth:
    tr = x.treatment
    return VisibleHealthMonth(
        month=x.month,
        record={"lived": "过完", "exit_month": "出局当月", "blocked": "停止月"}[x.record],
        state=x.state,
        state_label=HEALTH_STATUS_LABEL[x.state],
        new_illness=None if x.onset is None else HEALTH_STATUS_LABEL[x.onset],
        sick_leave=x.sick_leave,
        paused=tuple(
            VisiblePause(opportunity_id=a.opportunity_id, why=_PAUSE_LABEL[a.kind])
            for a in x.paused
        ),
        treatment=None
        if tr is None
        else VisibleTreatmentResult(
            severity=tr.severity,
            plan_choice="条件允许就治" if tr.plan_choice == "treat_if_possible" else "不治",
            status=tr.status,
            status_label=_TREATMENT_STATUS_LABEL[tr.status],
            amount_paid=tr.amount_paid,
            visit_hours=tr.visit_hours,
            note=tr.note,
        ),
        recovery_hours=x.recovery_hours,
        transition=_TRANSITION_LABEL[x.transition],
        notes=tuple(x.notes),
    )


def _visible_health_year(outcome: EnvironmentOutcome) -> VisibleHealthYear:
    stop = outcome.stop
    return VisibleHealthYear(
        months=tuple(_visible_health_month(x) for x in outcome.health.months),
        stop=None
        if stop is None
        else (
            f"第 {stop.year_index} 年第 {stop.month} 月停止（"
            + ("资料缺口" if stop.kind == "data_gap" else "应急安排不足")
            + f"）：{stop.reason}"
        )[:500],
        notes=tuple(outcome.health.notes),
        declared_purchase_cancellations=tuple(outcome.health.cancel_purchases_when_ill),
    )


def _visible_goal_event(e: GoalEvent) -> VisibleGoalEvent:
    return VisibleGoalEvent(
        goal_id=e.goal_id, kind=e.kind, year_index=e.year_index, month=e.month, note=e.note
    )


def _visible_goal(
    g: SavingsGoal, state: PersonYearState, catalog: CityLifeCatalog
) -> VisibleSavingsGoal:
    o = catalog.opportunity(g.opportunity_id)
    r = g.realization
    fields: dict[str, Any] = {}
    if g.open:
        digits = catalog.money_basis.minor_unit_digits
        cash = exact_minor_units(state.cash.amount, digits)
        quote = None
        if (
            o is not None
            and o.availability == "available"
            and o.price is not None
            and o.price.billing == "one_time"
            and o.price.amount.numeraire == state.money_basis_id
        ):
            quote = one_time_total_minor(o.price, g.quantity, digits)
        fields["cash"] = state.cash
        if quote is not None and cash is not None:
            fields["quote_now"] = money_of_minor(quote, digits, state.money_basis_id)
            fields["shortfall"] = money_of_minor(max(0, quote - cash), digits, state.money_basis_id)
            fields["cash_covers_quote"] = cash >= quote
    return VisibleSavingsGoal(
        goal_id=g.goal_id,
        kind=g.kind,
        kind_label=SAVINGS_GOAL_KIND_LABEL[g.kind],
        opportunity_id=g.opportunity_id,
        label=o.label if o is not None else g.opportunity_id,
        quantity=g.quantity,
        target_year=g.target_year,
        target_month=g.target_month,
        note=g.note,
        status=g.status,
        status_label=SAVINGS_GOAL_STATUS_LABEL[g.status],
        quote_at_creation=g.quote.total,
        quote_source=f"{g.quote.source_kind}：第 {g.quote.quoted_year} 年目录里的报价（当时的资料，不是价格预测）",
        past_due=g.status == "overdue",
        realization=None if r is None else f"第 {r.year_index} 年第 {r.month} 月：{r.note}",
        events=tuple(_visible_goal_event(e) for e in g.events),
        **fields,
    )


def _visible_savings(
    rules: SavingsRules, state: PersonYearState, catalog: CityLifeCatalog
) -> VisibleSavings:
    goals = state.savings_goals or ()
    open_goal = next((g for g in goals if g.open), None)
    return VisibleSavings(
        rules_version=rules.version,
        open_goal=None if open_goal is None else _visible_goal(open_goal, state, catalog),
        history=tuple(_visible_goal(g, state, catalog) for g in goals if not g.open),
        rules=SAVINGS_RULE_TEXTS,
    )


def _visible_savings_year(s: SavingsYear) -> VisibleSavingsYear:
    r = s.realization
    return VisibleSavingsYear(
        events=tuple(_visible_goal_event(e) for e in s.events),
        progress=tuple(
            VisibleGoalProgress(
                goal_id=g.goal_id,
                month=g.month,
                cash=g.cash,
                quote=g.quote,
                shortfall=g.shortfall,
                cash_covers_quote=g.cash_covers_quote,
                status=g.status,
            )
            for g in s.progress
        ),
        realization=None if r is None else f"第 {r.year_index} 年第 {r.month} 月：{r.note}",
        notes=tuple(s.notes),
    )


def _career_context(rules: CareerRules) -> VisibleCareerRules:
    return VisibleCareerRules(
        version=rules.version,
        rules=CAREER_RULE_TEXTS,
        abilities=tuple(
            VisibleAbilityDefinition(
                ability_id=a.ability_id,
                label=a.label,
                kind=ABILITY_KIND_LABEL[a.kind],
                description=a.description,
                scope=a.scope.describe(),
            )
            for a in rules.abilities
        ),
        employers=tuple(
            VisibleEmployer(employer_id=e.employer_id, label=e.label) for e in rules.employers
        ),
    )


def _visible_position(p: PositionRecord) -> VisiblePosition:
    return VisiblePosition(
        opportunity_id=p.opportunity_id,
        occupation=p.occupation,
        employer_id=p.employer_id,
        months=tuple(p.months),
    )


def _visible_abilities(
    rules: CareerRules, state: PersonYearState, catalog: CityLifeCatalog
) -> tuple[VisibleAbility, ...]:
    out = []
    for a in state.abilities:
        spec = rules.ability(a.ability_id)
        if spec is None:  # career_state_problems 已检查
            raise LifeContractError(f"能力 {a.ability_id} 没有在词表里")
        if a.source == "initial":
            source = "开局已有"
        else:
            course = catalog.opportunity(a.source_opportunity_id or "")
            name = course.label if course is not None else a.source_opportunity_id
            source = f"第 {a.acquired_year} 年第 {a.acquired_month} 月完成{name}"
        out.append(
            VisibleAbility(
                ability_id=a.ability_id,
                label=spec.label,
                kind=ABILITY_KIND_LABEL[spec.kind],
                scope=spec.scope.describe(),
                source=source,
                usable_from_year=a.usable_from_year,
            )
        )
    return tuple(out)


def _qualification(
    o: Any, state: PersonYearState, catalog: CityLifeCatalog, rules: CareerRules
) -> VisibleQualification | None:
    """按年初状态，这项机会今年能不能选。不能选时写明缺什么，以及正在学的课程离完成还差多少。"""
    if not o.eligibility:
        return None
    gaps = qualification_gaps(o, state)
    if not gaps:
        return VisibleQualification(status="available_now")
    progress = {t.opportunity_id: t for t in state.training}
    missing = []
    for g in gaps:
        text = g.reason
        if g.kind == "ability":
            spec = rules.ability(g.target)
            label = spec.label if spec is not None else g.target
            text = f"缺能力「{label}」：{g.reason}"
            for course in catalog.opportunities:
                t = progress.get(course.opportunity_id)
                if g.target in course.grants and t is not None and not t.completed:
                    text += (
                        f"；你在学「{course.label}」：已参加 {t.months_participated}/{t.months_required} 个月，"
                        "完成后从下一个行动年起可用"
                    )
        missing.append(text)
    return VisibleQualification(status="not_yet", missing=tuple(missing))


def _visible_opportunity(
    o: Any, state: PersonYearState, catalog: CityLifeCatalog, rules: CareerRules
) -> VisibleOpportunity:
    # 逐字段复制。source（溯源）与 targets（状态影响标签）不进代理视野：前者是审计信息，后者会提示"这项会改变什么状态"，
    # 属于规则侧。grants（学完授予什么能力）是做计划要用的公开事实，所以可见。
    return VisibleOpportunity(
        opportunity_id=o.opportunity_id,
        category=o.category,
        label=o.label,
        spec=dict(o.spec),
        unit=o.unit,
        quality=o.quality,
        availability=o.availability,
        price=o.price,
        compensation=o.compensation,
        occupation=o.occupation,
        eligibility=tuple(o.eligibility),
        arrangement_options=dict(o.arrangement_options),
        duration=o.duration,
        functions=tuple(o.functions),
        attributes=dict(o.attributes),
        time_energy=o.resource,
        employer_id=o.employer_id,
        grants=tuple(o.grants),
        qualification=_qualification(o, state, catalog, rules),
        food_plan=None if o.food_plan is None else _visible_food_plan(o.food_plan),
        enjoyment=tuple(
            VisibleEnjoyment(
                function=e.function,
                mode=e.mode,
                units_per_month=e.units_per_month,
                unit_label=e.unit_label,
            )
            for e in o.enjoyment
        ),
        equipment=None
        if o.equipment is None
        else VisibleEquipment(provides=tuple(o.equipment.provides)),
        activity=None if o.activity is None else _visible_activity(o.activity),
    )


def _visible_food_plan(f: Any) -> VisibleFoodPlan:
    return VisibleFoodPlan(
        covers_basic_month=f.covers_basic_month,
        meals_per_week=f.meals_per_week,
        parts=tuple(
            VisibleFoodPart(
                part_id=x.part_id,
                mode=x.mode,
                meals_per_week=x.meals_per_week,
                fresh_meals_per_week=x.fresh_meals_per_week,
                cuisine=x.cuisine,
            )
            for x in f.parts
        ),
        prep_hours_per_month=f.prep_hours_per_month,
        dining_hours_per_month=f.dining_hours_per_month,
    )


def _visible_activity(a: Any) -> VisibleActivity:
    # replaces_meals_per_session 不复制：契约只接受 0（代替正餐的套餐在加载时就被拒绝）
    return VisibleActivity(
        primary_category=a.primary_category,
        tags=tuple(a.tags),
        participation_hours=a.participation_hours,
        overhead_hours=a.overhead_hours,
        requires=tuple(a.requires),
        kit_provides=tuple(a.kit_provides),
    )


def _energy_rule(rules: MonthlyResourceRules, feedback: EnergyFeedbackPolicy) -> str:
    head = (
        f"每月活动负担合计不能超过每月可承受额度（单位 {rules.energy_unit}）；一项活动的负担 = 小时 × "
        "它的每小时负担。额度在一年里每月相同、每月重新给、不结转，休息不回血；时间与精力分别检查。"
    )
    if not feedback.effective:
        return head + "额度固定为你的基础额度，不随这一年的生活变化"
    return (
        head
        + "下一年的额度由环境根据这一年的实际生活确定，可能比基础额度高、不会更低；具体公式与分数不告知"
    )


def _visible_resources(
    rules: MonthlyResourceRules,
    state: PersonYearState,
    show_energy: bool,
    feedback: EnergyFeedbackPolicy,
) -> VisibleResourceBudget:
    basis = state.resource_budget_basis
    return VisibleResourceBudget(
        rules_version=rules.version,
        rules_revision=RESOURCE_RULES_REVISION,
        time_unit=rules.time_unit,
        monthly_time_budget=ExactAmount.of(to_fraction(state.time_budget_hours) / 12),
        reserved=f"{rules.reserved_time}；{basis.reserved}"[:400],
        time_rule=(
            "每月时间预算 = 年度净可分配小时 / 12（12 个标准月；睡眠与基本休息已预留在外，不进活动账）。"
            "当月已安排的活动时间不能超过预算，等于预算可以；没安排的时间月底作废：不结转、不跨月借用、"
            "不算休息回血，也不加满足感，不必把时间安排满"
        ),
        energy_unit=rules.energy_unit,
        monthly_energy_quota=ExactAmount.of(to_fraction(state.energy.level))
        if show_energy
        else None,
        energy_rule=_energy_rule(rules, feedback),
        commute_rule=(
            "当月通勤小时 = 实际所住住房的往返分钟 × 每个到岗岗位的到岗次数 / 60，几个岗位相加；每个岗位"
            "每次到岗算一趟完整往返，不合并行程；远程岗位不通勤，混合岗位只算到岗的次数。住房降级后按实际"
            "住的房计算"
        ),
        commute_reference=rules.commute_reference,
        commute_burden_per_hour=rules.commute_burden_per_hour,
        household_rule=(
            "本人每月家务小时 = min(基准, 当月实际在用的各完整替代方案的残余)；残余是本人处理整项家务"
            "仍要花的整月时间，不是机器运行时间；替代只在物品服务期内、服务实际买成的月份起作用"
        ),
        household_tasks=tuple(
            VisibleHouseholdTask(
                task=t.task,
                baseline_hours_per_month=t.baseline_hours_per_month,
                burden_per_hour=t.burden_per_hour,
                description=t.description,
            )
            for t in rules.household_tasks
        ),
    )


def _channels(
    permission: ObservationPermission, feedback: EnergyFeedbackPolicy
) -> ObservationChannels:
    on = feedback.effective
    return ObservationChannels(
        energy_visible=permission.show_energy_level,
        satisfaction_feedback="quota_feedback" if on else "none",
        study_condition="dynamic_life_feedback" if on else "product_without_satisfaction_feedback",
        health_risk_information="default_no_risk_relation",
    )


def _without_energy(feedback: VisibleFeedback) -> VisibleFeedback:
    """不许可精力可见时，反馈里的精力用量与剩余一律清空（时间事实照常可见）。"""
    if not feedback.resources:
        return feedback
    return feedback.model_copy(
        update={
            "resources": tuple(
                r.model_copy(
                    update={
                        "energy_used": None,
                        "energy_remaining": None,
                        "burden_by_category": None,
                    }
                )
                for r in feedback.resources
            )
        }
    )


def build_observation(
    *,
    permission: ObservationPermission,
    profile: ProductLifeProfile,
    state: PersonYearState,
    catalog: CityLifeCatalog,
    last_feedback: VisibleFeedback | None = None,
    resource_rules: MonthlyResourceRules,
    energy_feedback: EnergyFeedbackPolicy,
    career_rules: CareerRules,
    savings_rules: SavingsRules,
    health_rules: HealthRules,
    health_input: IndependentHealthInputDeclaration,
) -> AgentObservation:
    """按许可构造一年的可见观察。**每个参数都按类型检查**，传错类型直接拒绝。

    尤其：``profile`` 必须是六维产品画像 ``ProductLifeProfile``。传旧的 ``Persona`` 或别的对象都会被拒——产品允许看问卷，
    不等于放行整个人设。
    """
    checks = (
        (permission, ObservationPermission, "permission"),
        (profile, ProductLifeProfile, "profile"),
        (state, PersonYearState, "state"),
        (catalog, CityLifeCatalog, "catalog"),
        (resource_rules, MonthlyResourceRules, "resource_rules"),
        (energy_feedback, EnergyFeedbackPolicy, "energy_feedback"),
        (career_rules, CareerRules, "career_rules"),
        (savings_rules, SavingsRules, "savings_rules"),
        (health_rules, HealthRules, "health_rules"),
        (health_input, IndependentHealthInputDeclaration, "health_input"),
    )
    for value, cls, name in checks:
        if type(value) is not cls:
            raise LifeContractError(
                f"{name} 必须是 {cls.__name__}，得到 {type(value).__name__}；"
                "不接受旧人设或内部真值对象"
            )
    if last_feedback is not None and type(last_feedback) is not VisibleFeedback:
        raise LifeContractError(
            f"last_feedback 必须是 VisibleFeedback，得到 {type(last_feedback).__name__}；"
            "实际环境结果与内部评分不能直接当反馈给代理"
        )
    if state.status != "active":
        raise LifeContractError(
            f"{state.clone_id} 的状态是 {state.status}：终态与出局都不再接受行动，不为它们构造观察"
        )
    if profile.person_id != state.person_id:
        raise LifeContractError(f"画像 {profile.person_id} 与状态 {state.person_id} 不是同一人")
    if str(catalog.city) != str(state.city):
        raise LifeContractError(f"目录城市 {catalog.city} 与状态城市 {state.city} 不一致")
    if state.resource_budget_basis.rules_version != resource_rules.version:
        raise LifeContractError("状态的资源预算依据与资源规则的版本不一致")
    # 与加载、结算同一份判断：候选状态、换了编号或内容的政策、关闭反馈却带着加成，都不构造观察——不论精力是否可见
    problems = energy_quota_problems(energy_feedback, state)
    if problems:
        raise LifeContractError(
            "；".join(message for _, message in problems) + "——不为这个状态构造观察"
        )
    if health_input.questionnaire_version != profile.health_questionnaire_version:
        raise LifeContractError(
            "画像的健康问卷版本与协议声明的不同——旧答案不能改读成新题目的答案，不构造观察"
        )
    # 状态的健康部分必须由这一套疾病规则产生——与加载、结算前检查同一份判断（common），同一个冲突给出同一个代码与说明。
    # 在任何按许可裁剪的代码之前执行，不受显示开关影响；规则冲突时不给出恢复日期等事实
    problems = health_rules_state_problems(health_rules, state)
    if problems:
        raise LifeContractError(
            "；".join(f"[{code}] {message}" for code, message in problems)
            + "——不为这个状态构造观察"
        )

    p = permission
    if last_feedback is not None and not p.show_energy_level:
        last_feedback = _without_energy(last_feedback)
    obs = AgentObservation(
        information_condition=p.condition,
        permission_version=p.permission_version,
        dynamic_feedback_channel=p.show_energy_level,
        year_index=state.year_index,
        city=state.city,
        city_label=catalog.label,
        money_basis_id=state.money_basis_id,
        cash=state.cash,
        time_budget_hours=state.time_budget_hours,
        energy_level=state.energy.level if p.show_energy_level else None,
        occupation=state.occupation,
        holdings=tuple(
            VisibleHolding(
                opportunity_id=h.opportunity_id,
                quantity=h.quantity,
                remaining_months=h.effective_remaining_months,
                in_service_from_year=h.in_service_from_year,
            )
            for h in state.holdings
        ),
        training=tuple(
            VisibleTraining(
                opportunity_id=t.opportunity_id,
                months_participated=t.months_participated,
                months_required=t.months_required,
                completed=t.completed,
                fee_paid=t.fee_paid,
                completed_year=t.completed_year,
                completed_month=t.completed_month,
            )
            for t in state.training
        ),
        goals=tuple(state.goals),
        experience_summary=tuple(state.experience_summary),
        profile=_visible_product_profile(profile, p),
        opportunities=tuple(
            _visible_opportunity(o, state, catalog, career_rules) for o in catalog.opportunities
        ),
        minimum_living=tuple(
            VisibleMinimumNeed(
                need=m.need,
                baseline_opportunity_id=m.baseline_opportunity_id,
                baseline_quantity=m.baseline_quantity,
                qualifying_opportunity_ids=tuple(m.qualifying_opportunity_ids),
                meets=m.meets,
            )
            for m in catalog.minimum_living
        ),
        last_feedback=last_feedback,
        resources=_visible_resources(resource_rules, state, p.show_energy_level, energy_feedback),
        channels=_channels(p, energy_feedback),
        abilities=_visible_abilities(career_rules, state, catalog),
        employment=_visible_employment(state),
        career_rules=_career_context(career_rules),
        savings=_visible_savings(savings_rules, state, catalog),
        health=_visible_health(health_rules, state, catalog),
    )
    assert_payload_clean(obs)
    return obs


def _visible_product_profile(
    profile: ProductLifeProfile, p: ObservationPermission
) -> VisibleProfile:
    """六维产品画像的可见投影（逐字段复制，按许可）。六维与物质细分用精确分数原样给出（例如 "50/3"），不在这里四舍五入；
    健康回答逐题给出题目、作答状态、选项文字与读法，不给任何推断出的数值。"""
    return VisibleProfile(
        hard_requirements=tuple(profile.hard_requirements) if p.show_hard_requirements else None,
        free_text=profile.free_text if p.show_free_text else None,
        statement_kind="product_six_dimension_v1",
        six_dimension_points=dict(profile.dimension_points)
        if p.show_stated_dimension_points
        else None,
        material_allocation_status=profile.material_status
        if p.show_stated_material_points
        else None,
        material_allocation=dict(profile.material_points)
        if p.show_stated_material_points and profile.material_points is not None
        else None,
        health_questionnaire_version=profile.health_questionnaire_version
        if p.show_health_answers
        else None,
        health_answers=tuple(
            VisibleHealthAnswer(
                question_id=a.question_id,
                question_text=a.question_text,
                answer_state=a.answer_state,
                option_label=a.option_label,
                planning_note=a.planning_note,
            )
            for a in profile.health_answers
        )
        if p.show_health_answers
        else None,
        leisure_interest_status=profile.leisure_interest_status if p.show_interests else None,
        leisure_interests=tuple(profile.leisure_interests) if p.show_interests else None,
        taste_status=profile.taste_status if p.show_interests else None,
        cuisines=tuple(profile.cuisines) if p.show_interests else None,
        not_permitted=tuple(
            name
            for name, shown in (
                ("six_dimension_points", p.show_stated_dimension_points),
                ("material_allocation", p.show_stated_material_points),
                ("health_answers", p.show_health_answers),
                ("hard_requirements", p.show_hard_requirements),
                ("free_text", p.show_free_text),
                ("interests", p.show_interests),
            )
            if not shown
        ),
    )


def _visible_employment(state: PersonYearState) -> VisibleEmployment:
    e = state.employment
    as_of = "开局声明" if e.as_of == "start_declaration" else f"第 {e.settled_year} 年实际执行"
    return VisibleEmployment(
        stated_occupation=e.stated_occupation,
        as_of=as_of,
        current=tuple(_visible_position(p) for p in e.current_positions()),
        last_year=tuple(_visible_position(p) for p in e.positions)
        if e.as_of == "settled_year"
        else (),
    )


# ---------------------------------------------------------------------- 年度反馈


def _total(amounts: list[Money], numeraire: str) -> Money:
    # 十进制求和：每笔金额都来自整数最小单位，十进制加总没有二进制误差。
    total = sum((Decimal(repr(a.amount)) for a in amounts), Decimal(0))
    return Money(float(total), numeraire)


def visible_feedback(result: SettlementResult) -> VisibleFeedback:
    """从一年的结算结果里**逐字段**取出代理可见的事实。

    只有事实：期末余额、全年税前收入、税、支出、各项行动实际执行与被拒的月份、出局
    的说明。没有满足感、分数或排序；结算结果、内部记录与下一年状态整体都不进来。
    """
    if type(result) is not SettlementResult:
        raise LifeContractError(f"只能从 SettlementResult 构造反馈，得到 {type(result).__name__}")
    out, nxt = result.outcome, result.next_state
    if result.status not in ("completed", "exited", "stopped") or out is None or nxt is None:
        raise LifeContractError(f"{result.status} 没有提交任何账目，没有年度反馈可给")
    basis = out.money_basis_id
    income = [f.amount for f in out.cash_flows if f.kind == "income_gross"]
    tax = [-f.amount for f in out.cash_flows if f.kind == "income_tax"]
    spending = [-f.amount for f in out.cash_flows if f.kind == "expense"]
    notes = [n for n in out.notes if n.startswith("合成记账验证")]
    if out.exit is not None:
        e = out.exit
        needs = "、".join({"housing": "住房", "food": "食品"}[n] for n in e.unaffordable_needs)
        notes.append(
            f"第 {e.year_index} 年第 {e.month} 月付不起最低住房与食品（缺{needs}，"
            f"差 {e.shortfall.amount:,.2f}），出局；此前的账目与物品保留"
        )
    if nxt.status == "terminal":
        notes.append("这是最后一个行动年；之后是终态，不再接受行动")
    resources = _grouped_months(out.resources.months)
    career = VisibleCareerYear(
        learning=tuple(
            VisibleLearning(
                opportunity_id=x.opportunity_id,
                fee_charged=x.fee_charged,
                months_attended=tuple(x.months_attended),
                progress_after=x.progress_after,
                months_required=x.months_required,
                completed_month=x.completed_month,
                granted=tuple(x.granted),
                already_held=tuple(x.already_held),
                usable_from_year=x.usable_from_year,
            )
            for x in out.career.learning
        ),
        positions=tuple(_visible_position(p) for p in out.career.positions),
        current_position_ids=tuple(out.career.current_position_ids),
    )
    feedback = VisibleFeedback(
        year_index=out.year_index,
        cash_balance=out.cash_end if out.cash_end is not None else nxt.cash,
        life_status=nxt.status,
        income_gross=_total(income, basis),
        income_tax=_total(tax, basis),
        spending=_total(spending, basis),
        executed=tuple(out.executed),
        notes=tuple(notes),
        resources=resources,
        career=career,
        savings=_visible_savings_year(out.savings),
        health=_visible_health_year(out),
        participation=_visible_participation(out.participation),
    )
    assert_payload_clean(feedback)
    return feedback


def _by_category(record: MonthResources, attr: str) -> dict[str, ExactAmount]:
    totals: dict[str, object] = {}
    for c in record.contributions:
        totals[c.category] = totals.get(c.category, 0) + getattr(c, attr).fraction()  # type: ignore[operator]
    return {k: ExactAmount.of(v) for k, v in totals.items()}  # type: ignore[arg-type]


def _grouped_months(months: tuple[MonthResources, ...]) -> tuple[VisibleMonthResources, ...]:
    """逐月取出资源事实，再把内容完全相同的月份合成一条（R1：只省重复；任何一个月的数字都还在）。"""
    groups: dict[str, list[int]] = {}
    first: dict[str, VisibleMonthResources] = {}
    for r in months:
        v = _visible_month(r)
        key = v.model_dump_json(exclude={"months"})
        groups.setdefault(key, []).append(r.month)
        first.setdefault(key, v)
    out = [first[k].model_copy(update={"months": tuple(ms)}) for k, ms in groups.items()]
    return tuple(sorted(out, key=lambda v: v.months[0]))


def _visible_participation(
    records: tuple[ParticipationRecord, ...],
) -> tuple[VisibleParticipation, ...]:
    """参与记录（每项活动每个请求月一条）→ 每项活动一条汇总：完全相同的完成月份合成一段，暂停 / 没能进行按原因合成。
    合计直接从记录相加；不读计划，也不从资源配置另算。"""
    by: dict[str, list[ParticipationRecord]] = {}
    for p in records:
        by.setdefault(p.opportunity_id, []).append(p)
    out = []
    for oid, rows in by.items():
        spans: dict[tuple, list[int]] = {}
        misses: dict[tuple, list[int]] = {}
        for p in sorted(rows, key=lambda r: r.month):
            if p.status == "completed":
                key = (
                    p.sessions,
                    p.participation_hours,
                    p.overhead_hours,
                    tuple(p.equipment_used),
                    tuple(p.kit_capabilities),
                )
                spans.setdefault(key, []).append(p.month)
            else:
                misses.setdefault((p.status, p.reason), []).append(p.month)
        done = [p for p in rows if p.status == "completed"]
        out.append(
            VisibleParticipation(
                opportunity_id=oid,
                done=tuple(
                    VisibleParticipationSpan(
                        months=tuple(ms),
                        sessions_each_month=k[0],
                        participation_hours_each_month=k[1],
                        overhead_hours_each_month=k[2],
                        equipment_used=k[3],
                        kit_capabilities=k[4],
                    )
                    for k, ms in sorted(spans.items(), key=lambda kv: kv[1][0])
                ),
                not_done=tuple(
                    VisibleNotDone(months=tuple(ms), status=k[0], reason=k[1])
                    for k, ms in sorted(misses.items(), key=lambda kv: kv[1][0])
                ),
                sessions_total=sum(p.sessions for p in done),
                participation_hours_total=sum(p.participation_hours for p in done),
                overhead_hours_total=sum(p.overhead_hours for p in done),
            )
        )
    return tuple(out)


def _visible_month(r: MonthResources) -> VisibleMonthResources:
    """逐字段取出一个月的资源事实；精力字段在构造观察时按许可清空。"""
    return VisibleMonthResources(
        months=(r.month,),
        resource_check=r.resource_check,
        time_used=r.time_used,
        time_remaining=r.time_remaining,
        energy_used=r.energy_used,
        energy_remaining=r.energy_remaining,
        hours_by_category=_by_category(r, "hours"),
        burden_by_category=_by_category(r, "burden"),
        housing_opportunity_id=r.housing_opportunity_id,
        commute_roundtrip_minutes=r.commute_roundtrip_minutes,
        onsite_visits=r.onsite_visits,
        commute_status=r.commute_status,
        notes=r.notes,
    )


# ---------------------------------------------------------------------- 第二道防线

#: 不该出现在任何 prompt 里的键名（内部权重、得分、效用、答案）。
FORBIDDEN_PAYLOAD_KEYS: frozenset[str] = frozenset(
    {
        "dimension_weights",
        "material_subweights",
        "internal_weights",
        "satisfaction",
        "dimension_satisfaction",
        "utility",
        "ln_utility",
        "cumulative_utility",
        "welfare",
        "score",
        "scores",
        "values",
        "best_city",
        "optimal_city",
        "argmax_city",
        "answer",
        "truth",
        "ground_truth",
        # E1-3 探索评分的内部键：月度分、年均分、评分偏好、城市评分参数
        "annual_mean",
        "material_weights",
        "scoring_version",
        "eta_any",
        "efficiencies",
        # E1-3 增补：覆盖报告与景观参数
        "common_weight",
        "coverage_label",
        "a_nature",
    }
    # E1-3 增补订正（`DECISIONS/0019` 第十二节）：评分侧参数的保留键名，与加载检查共用一份
    | RESERVED_SCORING_KEYS
    # E1-5C：疾病风险的内部键（概率、抽签、随机源、风险档），与加载检查共用一份
    | RESERVED_HEALTH_KEYS
    # E1-6A：跨期评价的参数、候选方案比较与估值结果，与加载检查共用一份
    | RESERVED_EVALUATION_KEYS
    # E1-6B：城市均值、缺失范围、比较与名单身份，与加载检查共用一份
    | RESERVED_AGGREGATION_KEYS
    # E1-7A：城市事实到评分输入的换算参数（换算系数、确定不存在对应值）
    | RESERVED_FACT_LINK_KEYS
)


def _forbidden_types() -> tuple[type, ...]:
    from common.life_scoring_contracts import internal_models
    from econ.types import ANSWER_SIDE_TYPES

    return (
        *ANSWER_SIDE_TYPES,
        InternalYearScore,
        EnvironmentOutcome,
        PersonYearState,
        SettlementResult,
        ItemExecution,
        # 生活扩展（DECISIONS/0041）：结算的参与记录本身不进观察；可见部分是逐字段汇总的 VisibleParticipation
        ParticipationRecord,
        # E1-3 起：探索评分的全部内部对象（评分规格与来源、城市条件、评分偏好、各层结果、景观与
        # 通勤明细、比较组、覆盖报告）。与"原样序列化"检查共用同一份清单，新增内部类型只登记一处。
        # E1-4B 起：反馈政策、反馈记录、年度交接结果与年度绑定（r2）也在这份清单里（internal_models）。
        *internal_models(),
        # E1-4A：资源规则与整年、整月的资源账不能整体进 payload；可见部分逐字段投影
        MonthlyResourceRules,
        ResourceYear,
        MonthResources,
        # E1-5A：职业规则、能力与岗位的内部记录、这一年的学习结果与资格判断。可见部分另有 Visible* 类型
        CareerRules,
        AbilitySpec,
        EmployerSpec,
        HeldAbility,
        PositionRecord,
        EmploymentRecord,
        CareerYear,
        LearningYearRecord,
        QualificationGap,
        # E1-5B：目标储蓄规则、目标与它的报价依据、事件、兑现依据、这一年的目标记录。可见部分另有 Visible* 类型
        SavingsRules,
        SavingsGoal,
        GoalQuote,
        GoalEvent,
        GoalRealization,
        GoalMonth,
        SavingsYear,
        # E1-5C：疾病规则（含风险表）、事件来源、健康输入声明、治疗目录条目、应急安排、健康状态与病史、一年的健康
        # 记录（含抽签）、停止记录与暂停明细。可见部分另有 Visible* 类型
        HealthRules,
        SeverityRules,
        RiskTable,
        AgeBand,
        BurdenBand,
        RiskCell,
        TreatmentNeed,
        IllnessEventSource,
        InjectedIllness,
        # 独立健康回答的协议声明与六维产品画像本身也不能整体进 payload（可见部分逐字段投影）
        IndependentHealthInputDeclaration,
        ProductLifeProfile,
        ProductHealthAnswer,
        TreatmentOffer,
        EmergencyPlan,
        TreatmentRecord,
        IllnessEpisode,
        HealthBinding,
        HealthState,
        HealthYear,
        HealthMonth,
        RiskDraw,
        PausedActivity,
        TreatmentOutcome,
        StopRecord,
        MonthPause,
        # E1-7A：执行前检查的原始结果与结算问题（带着未按许可裁剪的资源诊断）——可见部分是逐字段投影的 VisibleDiagnostic；
        # 城市事实到评分输入的内部对应；运行配置与运行记录
        PlanCheck,
        SettlementProblem,
        ResourceShortfall,
        *internal_city_fact_models(),
        *internal_planning_models(),
        # E3-A：产品共享消息（草稿、审阅、确认画像、预检查、结果、目录编译……）整体都不进观察；给分身看的只有逐字段投影
        *internal_product_models(),
    )


def _walk(value: Any, path: str, forbidden: tuple[type, ...], seen: set[int]) -> None:
    if isinstance(value, forbidden):
        raise LifeContractError(f"{type(value).__name__} 出现在可见字段 {path!r}")
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, ContractModel):
        for name in type(value).model_fields:
            _walk(getattr(value, name), f"{path}.{name}", forbidden, seen)
    elif isinstance(value, dict):
        for k in value:
            if isinstance(k, str) and k in FORBIDDEN_PAYLOAD_KEYS:
                raise LifeContractError(f"可见字段 {path!r} 里出现了内部键 {k!r}")
        hidden = serialized_internal(value)
        if hidden is not None:
            raise LifeContractError(
                f"可见字段 {path!r} 是原样序列化的内部评分对象 {hidden}（换成字典也不能进观察）"
            )
        for k, v in value.items():
            _walk(v, f"{path}[{k!r}]", forbidden, seen)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for i, v in enumerate(value):
            _walk(v, f"{path}[{i}]", forbidden, seen)
    elif hasattr(value, "__dataclass_fields__"):
        for name in value.__dataclass_fields__:
            _walk(getattr(value, name), f"{path}.{name}", forbidden, seen)


def assert_payload_clean(obj: Any) -> None:
    """对象层面扫一遍：旧答案类型、新内部类型、内部键名、原样序列化的内部评分对象，嵌套到底。

    防护边界：这是对已知内部类型、保留键名与它们原样序列化结构的检查，不是对任意改名或自然语言
    隐写的通用检测器。主防线仍是逐字段的白名单构造。
    """
    _walk(obj, "observation", _forbidden_types(), set())


def observation_payload(obs: AgentObservation) -> dict[str, Any]:
    """最终送进 prompt 的 JSON 结构。序列化之后**再**扫一遍键名。"""
    if type(obs) is not AgentObservation:
        raise LifeContractError(f"只能序列化 AgentObservation，得到 {type(obs).__name__}")
    assert_payload_clean(obs)
    payload = obs.model_dump(mode="json")
    _walk(payload, "payload", (), set())
    return payload
