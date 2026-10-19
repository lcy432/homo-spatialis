"""生活模型的探索评分契约（E1-3，`DECISIONS/0018`）。

**这里只有数据形状与局部约束，没有评分计算。** 计算在 ``econ.life_scoring``，配置的
跨对象检查在 ``ledger.life_scoring_config``。

探索函数（写进 ``LifeScoringSpec.function``，版本 ``saturating_capped_v1``）：

    z = b + Σ η · x            s = min(c, z / (z + τ))

* ``x``：该月**实际**得到的数量或条件，来自结构化规格与执行记录；
* ``b``：城市基础条件（与 x 的折算结果同一单位）；
* ``η``：声明的改善效率或单位换算系数（≥ 0）；
* ``τ``（> 0）：所有城市共用的锚点；不受上限限制时 z = τ 对应 0.5；
* ``c``（0–1）：这座城这项条件可达的满足感上限。

**τ 只出现在评分规格里**（``ScoringComponent.tau``），城市条件里没有这个字段——结构上
就不允许按城市调锚点。``b``、``η``、``c`` 可以因城而异，但每一条都要带来源。

**全部参数都是合成探索配置**（``status = exploratory_synthetic``），不是经验估计，不能
声称已经现实标定，也不能用来排城市。

这些对象都是**评分侧内部对象**：评分规格、城市条件、评分偏好与评分结果都不进代理
观察或事实反馈（``agentlayer.life_views`` 的扫描会拦截它们）。用户自己的陈述
（``StatedLifeProfile``）照旧按许可可见，这里不改变那条边界。
"""

from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from common.ids import CityId
from common.life_contracts import (
    MONTHS_PER_YEAR,
    ContractModel,
    EnergyFeedbackPolicy,
    ExactAmount,
    IdStr,
    ItemRole,
    KeyStr,
    Month,
    Primitive,
    SourceKind,
    SourceRecord,
    YearIndex,
    canonical_fingerprint,
    has_decimals,
)
from common.life_units import NUMERIC_DIMENSIONS, conversion_factor, resolve_unit
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS, MATERIAL

#: 七维里医疗维的键（E1-5C 健康版本里它表示健康风险应对倾向，不进满足感）。
HEALTHCARE = "healthcare"
assert HEALTHCARE in AMENITY_DIMENSIONS

#: 探索函数的版本名。函数一变，旧结果与新结果不能混着比。
SCORING_FUNCTION = "saturating_capped_v1"
#: 权重合计为 1 的检查精度（权重来自定和点数 / 100 或合成设定，没有更粗的误差来源）。
WEIGHT_SUM_TOL = 1e-9

#: 评分从哪一类**实际记录**读取生活条件（`DECISIONS/0018` 第三节的来源表）：
#:
#: ==================  ======================================  ===========================
#: housing / food      ``ItemExecution`` 的执行月（含兜底那条）  实际住的、实际吃的
#: periodic_service    ``ItemExecution`` 的执行月               按月/按周服务
#: experience          ``ItemExecution`` 的执行月（成交那个月）  一次体验
#: prepaid_service     ``HoldingService`` 的服务月              年付服务（只读这里）
#: durable_good        ``HoldingService`` 的服务月              持续物品（只读这里）
#: ==================  ======================================  ===========================
#:
#: 年付服务在 ``ItemExecution`` 里也镜像了覆盖月，持续物品在 ``ItemExecution`` 里也有成交月；
#: 评分**只从一处**读，避免同一次服务计两次。工作、培训、现金不是评分来源。
ScoringRecord = Literal[
    "housing", "food", "periodic_service", "experience", "prepaid_service", "durable_good"
]
#: 同一分项有多个来源时怎么合并。只有这三种，没有通用表达式语言。
#:
#: * ``sum``：可相加的改善（同一单位）；
#: * ``max``：只取最好的一个来源；
#: * ``any``：布尔替代——任何一个来源满足就算满足，**同一需求多处满足只计一次**。
CombineRule = Literal["sum", "max", "any"]
#: 来源的取值方式：规格里的数值、规格里的是/否条件、数量，或"存在"（E1-3 增补：只要这个月
#: 有一件在用的候选就取 1，配合功能过滤与 any 使用，第二件同功能的物品不再加分）。
ValueMode = Literal["spec_number", "spec_flag", "quantity", "present"]
#: 景观加分系数的探索上限（`DECISIONS/0019` 第七节）。A_nature ≤ 1，所以景观最多加半格再截顶。
LANDSCAPE_BETA_MAX = 0.5
#: 按 ADR0018 原规则运行的评分版本。它们的来源可以不声明数量语义（旧的"按件数相加"照旧），
#: 单位写法也不按规范单位检查——旧配置与旧结果不改写。其他版本一律按增补订正的规则检查。
ADR0018_SCORING_VERSIONS: frozenset[str] = frozenset({"e1-3-explore-v1"})
#: 发展维成长项与广义交通的评分版本（`DECISIONS/0024`）。只有这些版本能声明 ``growth`` / ``transport``，
#: 而且必须两项都声明：旧版本（ADR0018、E1-3 增补）的名字不会悄悄换成新行为。E1-5C 的健康评分版本与日常生活便利版本沿用它们。
GROWTH_TRANSPORT_VERSIONS: frozenset[str] = frozenset(
    {"e1-5a-growth-transport-v1", "e1-5c-health-v1", "e1-convenience-v1"}
)
#: E1-5C 的健康评分版本（`DECISIONS/0027`）：
#:
#:     w̃_k = w_k / Σ_{j≠医疗} w_j        B_m = Σ_{k≠医疗} w̃_k s_{k,m}        S_m = (1 − γ) B_m + γ H(q_m)
#:
#: 六维（物质、安全、发展、休闲、环境、交通）的公式不变；医疗维没有分项（城市医疗背景不进满足感）；七维里 healthcare
#: 的点数表示健康风险应对倾向，不进满足感；H 读月初健康状态、全组共用，不乘任何个人权重。只有这些版本能声明 ``health``，
#: 而且必须声明。日常生活便利的版本（下面）沿用这一套健康语义。
HEALTH_SCORING_VERSIONS: frozenset[str] = frozenset({"e1-5c-health-v1", "e1-convenience-v1"})
#: E1 补充"日常生活便利"（`DECISIONS/0033`）的评分版本：物质维的第三项从"有没有洗衣设备"换成"这个月实际少做了多少家务
#: 小时"。它沿用 E1-5C 的健康语义与 E1-5A 补充的成长、广义交通（也登记在上面两个集合里，不会意外回到旧医疗语义）。只有这些
#: 版本能声明 ``convenience``，而且必须恰好一个；不能再有洗衣分项或按家务功能取"有没有"的来源——同一件事不计两份分。
CONVENIENCE_SCORING_VERSIONS: frozenset[str] = frozenset({"e1-convenience-v1"})
#: 便利分项的物质细需求键（产品输入里的"日常生活便利"：愿意在多大程度上花钱减少日常杂务）。
DAILY_CONVENIENCE = "daily_convenience"
#: 旧的洗衣细需求键与洗衣功能键。便利版本不接受它们：旧的洗衣点数不能静默改读成广义便利，旧的洗衣分也不能与便利分叠加。
LEGACY_LAUNDRY = "laundry"
#: 评分内容身份的规范化格式（哪些字段参与见 ``LifeScoringSpec.content``，DECISIONS/0025）。
SCORING_CONTENT_SCHEMA = "life-scoring-content-v1"
#: 不参与评分内容身份的字段：名字与引用（另行核对）、说明文字。其余字段——包括以后新增的——默认参与。
SCORING_CONTENT_EXCLUDED_SPEC_FIELDS: tuple[str, ...] = (
    "spec_id",
    "protocol_id",
    "note",
    "omissions",
)
SCORING_CONTENT_EXCLUDED_COMPONENT_FIELDS: tuple[str, ...] = ("description",)
SCORING_CONTENT_EXCLUDED_SOURCE_FIELDS: tuple[str, ...] = ("note",)
SCORING_CONTENT_EXCLUDED_MAPPING_FIELDS: tuple[str, ...] = ("definition",)
#: 来源的数量语义（`DECISIONS/0019` 第十二节）。新版本的每个来源都必须声明：
#:
#: ===========  ==========================================  ======================
#: presence     有没有：取"存在"或规格是/否，数量不进入       分项只能 any 或 max
#: capacity     实际服务容量：规格数值 × 件数（声明 per_unit）  sum 或 max
#: level        一项的规格水平（面积、每周鲜食餐数），数量不进入  只能 max
#: count        离散发生的次数（一次体验算一次）                只能 sum，只读一次体验
#: ===========  ==========================================  ======================
#:
#: 存在型不能用任何方式让件数进入：数量取值、按件乘规格、sum 都被拒绝——一条记录里买三部、
#: 分三次买、买三种不同的手机，结果都一样。
SourceMeasure = Literal["presence", "capacity", "level", "count"]
_MEASURE_VALUES: dict[str, frozenset[str]] = {
    "presence": frozenset({"present", "spec_flag"}),
    "capacity": frozenset({"spec_number"}),
    "level": frozenset({"spec_number"}),
    "count": frozenset({"quantity"}),
}
_MEASURE_COMBINES: dict[str, frozenset[str]] = {
    "presence": frozenset({"any", "max"}),
    "capacity": frozenset({"sum", "max"}),
    "level": frozenset({"max"}),
    "count": frozenset({"sum"}),
}


class ComponentSource(ContractModel):
    """一个评分分项的一类来源：从哪类实际记录、哪些项目、取什么值。

    ``requires_spec_key``：只有规格里声明了这个键的项目才算候选。例如"使用安排"：
    只有声明了 ``visits_per_month`` 的会员才是锻炼来源；没声明使用安排的年卡**不是**
    候选，不会被自动解释成完整的健身收益（`DECISIONS/0018` 的使用假设）。

    候选项目缺了 ``spec_key``，就是"数据未知"，不是零。

    ``functions``（E1-3 增补）：只有声明了其中某个需求功能的项目才算候选。功能过滤的来源
    只能取"存在"、数量或明确的容量规格；前两种不能用 ``sum`` 相加（不能按件刷分），只有读取
    容量规格（``spec_number``）的来源才可以相加——那是声明过的容量语义。
    """

    source_id: KeyStr
    records: tuple[ScoringRecord, ...] = Field(min_length=1)
    categories: tuple[KeyStr, ...] = ()
    functions: tuple[KeyStr, ...] = ()
    requires_spec_key: KeyStr | None = None
    value: ValueMode
    spec_key: KeyStr | None = None
    flag_value: Primitive | None = None
    per_unit: bool = False
    unit: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=280)
    #: 数量语义（增补订正）。旧版本可以不写；其他版本必须写，见 ``SourceMeasure``。
    measure: SourceMeasure | None = None

    @model_validator(mode="after")
    def _measure_consistent(self) -> ComponentSource:
        m = self.measure
        if m is None:
            return self
        if self.value not in _MEASURE_VALUES[m]:
            raise ValueError(
                f"{self.source_id}: {m} 来源不能取 {self.value}"
                + (
                    "——存在型只看有没有，件数不能进入（不能按件刷分）"
                    if m == "presence"
                    else f"，只能取 {sorted(_MEASURE_VALUES[m])}"
                )
            )
        if m == "capacity" and not self.per_unit:
            raise ValueError(
                f"{self.source_id}: 容量型来源必须声明 per_unit（规格容量 × 实际件数）"
            )
        if m in ("presence", "level") and self.per_unit:
            raise ValueError(f"{self.source_id}: {m} 来源不乘件数")
        if self.functions and m not in ("presence", "capacity"):
            raise ValueError(
                f"{self.source_id}: 按需求功能过滤的来源只能是存在型或容量型，得到 {m}"
            )
        return self

    @model_validator(mode="after")
    def _value_fields_consistent(self) -> ComponentSource:
        if self.value in ("spec_number", "spec_flag") and self.spec_key is None:
            raise ValueError(f"{self.source_id}: {self.value} 必须给 spec_key")
        if self.value in ("quantity", "present") and (self.spec_key is not None or self.per_unit):
            raise ValueError(f"{self.source_id}: {self.value} 不读规格，也不再乘数量")
        if len(set(self.functions)) != len(self.functions):
            raise ValueError(f"{self.source_id}: functions 重复")
        if self.value == "spec_flag" and self.flag_value is None:
            raise ValueError(f"{self.source_id}: spec_flag 必须给 flag_value（等于什么才算满足）")
        if self.value != "spec_flag" and self.flag_value is not None:
            raise ValueError(f"{self.source_id}: 只有 spec_flag 才有 flag_value")
        if self.per_unit and self.value != "spec_number":
            raise ValueError(f"{self.source_id}: per_unit 只用于规格数值 × 数量")
        if len(set(self.records)) != len(self.records):
            raise ValueError(f"{self.source_id}: records 重复")
        return self


class LandscapeSpec(ContractModel):
    """环境分项的自然景观加分（`DECISIONS/0019` 第七节）：S_env = min(c, S_physical + β·A)。

    β 全组共用、非负、有上限；β = 0 是关闭对照。定义写清楚是自然景观与户外环境吸引力，
    不含购物、历史古迹或游客数量。
    """

    beta: Annotated[float, Field(ge=0, le=LANDSCAPE_BETA_MAX)]
    definition: str = Field(min_length=1, max_length=400)


class PlanCommuteSpec(ContractModel):
    """通勤分项的方案级映射（`DECISIONS/0019` 第八节）：S = τ_T / (τ_T + T)。

    T 是这个月实际住的住房在规格 ``minutes_key`` 里写的每工作日往返分钟；只有这个月恰好执行
    了一份工作方式（``work_mode_key``）等于 ``onsite_value`` 的工作，才评通勤。τ_T 就是分项的 τ。
    """

    minutes_key: KeyStr
    work_mode_key: KeyStr
    onsite_value: Primitive
    unit: str = Field(min_length=1, max_length=80)


class GrowthSpec(ContractModel):
    """发展维的实际成长项（`DECISIONS/0024`）：z_m = b + η_growth · g_m，s = min(c, z/(z+τ))。

    b 是城市的发展条件（城市背景，不由本人的学习结果回填）；g_m 是这个人这个月**实际新增**的能力进度之和：
    词表里每项能力的进度 p_a 在 0–1 之间（年初已有 = 1；未有 = 授予它的课程中最高的"实际累计参加月数 / 要求
    月数"），g_m = Σ_a (p_a,m − p_a,m−1)。只用本月及之前真实参加的记录，不读理由、学费、工资或课程名称。

    η 全组共用、只写在评分规格里（不能按城市调）；η = 0 是关闭对照。能力拆分粒度会影响成长量（一门课授予
    两项能力，每月就有两份进度），这一假设登记在参数目录 CA-06。持续成就感、遗忘与心理适应没有实现：成长分
    只在实际进步的月份出现，证书本身、换岗、职位名称与涨薪都不另加分。
    """

    eta: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    progress_rule: Literal["ability_progress_increment_v1"]
    unit: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=400)


class TransportSpec(ContractModel):
    """通勤维的广义交通映射（`DECISIONS/0024`）。维度键仍是 ``commute``，含义是"交通便捷程度及出行对生活的影响"：

        k_m = ρ + (1 − ρ) · D_m / D_ref        T_eq = k_m · T_m        S = τ_T / (τ_T + T_eq)

    * T_m：这个月实际所住住房（含兜底）在规格 ``minutes_key`` 里写的往返分钟——与资源账计通勤用的是同一个字段；
    * D_m：这个月实际累计到岗次数，读资源账的 ``onsite_visits``（时间账用的同一个计数，多岗位相加）；
    * ρ：不因上班消失的交通影响（无工作、纯远程时 k = ρ）；D_ref：参考到岗次数。

    T_eq 是评分中间量，**不是**实际出行分钟；时间与负担账不乘 k、不重复扣。用住房的工作往返分钟代理广义出行
    条件是合成映射假设：购物、社交、就医与公共交通可及性没有单独建模。ρ、D_ref 全组共用，只写在评分规格里。
    """

    minutes_key: KeyStr
    rho: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    reference_visits: Annotated[int, Field(ge=1, le=62)]
    unit: str = Field(min_length=1, max_length=80)
    visits_unit: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=400)


class HealthStateValues(ContractModel):
    """健康项 H：月初状态 → 0–1 的值（全组共用）。健康不低于小病，小病不低于大病。

    E1-5C 收尾（2026-10-11，DECISIONS/0027 收尾修订）：**H(健康) 固定为 1**——它是健康项的归一化基准，表示"没有疾病造成的
    扣减"，不表示这个人的全部生活已经满分。所以相对健康的直接扣分就是 γ(1 − H)。小病、大病的 H 与 γ 才是待研究的合成评价
    参数（0–1，健康 ≥ 小病 ≥ 大病）。H(健康) ≠ 1 在构造规格时就被拒绝，不自动改回 1——这不是浮点误差，不靠容差处理。
    """

    healthy: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    minor: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    major: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]

    @model_validator(mode="after")
    def _ordered(self) -> HealthStateValues:
        if self.healthy != 1:
            raise ValueError(
                f"H(健康) 固定为 1，得到 {self.healthy!r}：1 是健康项的归一化基准（没有疾病造成的扣减，不是整体生活"
                "满分），相对健康的扣分按 γ(1 − H) 报告；要改变健康项的分量请改 γ 或小病、大病的 H——不自动改回 1"
            )
        if not self.healthy >= self.minor >= self.major:
            raise ValueError("H 必须满足 健康 ≥ 小病 ≥ 大病")
        return self

    def of(self, state: str) -> float:
        return {"healthy": self.healthy, "minor": self.minor, "major": self.major}[state]


class HealthScoringSpec(ContractModel):
    """E1-5C 的健康项（`DECISIONS/0027`）：S_m = (1 − γ) B_m + γ H(q_m)。

    * ``gamma``：健康项在月度总分里的固定份额（全组共用，0–1，两位小数）；γ = 0 是关闭对照（S = B）；
    * ``state_values``：H(健康 / 小病 / 大病)，H(健康) 固定为 1（归一化基准，收尾修订）；同等病情对每个人的直接扣分都是
      γ(1 − H)，**不乘个人医疗权重**；
    * ``rule``：H 读这个月的**月初**状态（与工作限制同一个时点），治疗只改变之后的月份。

    付医疗费不加分，也没有"没看病"的额外惩罚：钱与时间的代价在实际生活里体现（现金、其他维度、精力）。
    """

    gamma: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    state_values: HealthStateValues
    rule: Literal["month_start_state"]
    definition: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _precision(self) -> HealthScoringSpec:
        if not has_decimals(self.gamma, 2):
            raise ValueError(f"γ = {self.gamma!r} 的精度细于两位小数")
        return self


class ConvenienceSpec(ContractModel):
    """物质维"日常生活便利"分项（`DECISIONS/0033`）：这个月本人实际少做了多少小时家务。

        q_m = Σ_{t ∈ tasks} (b_t − r_{t,m})        s = min(c, q_m / (q_m + τ))

    * b_t：协议资源规则里任务 t 的每月基准小时（``MonthlyResourceRules.household_tasks``，结构化字段；同一协议所有城市、所有人共用）；
    * r_{t,m}：这个月资源账里任务 t 那一行的本人残余小时（``MonthResources.contributions``，category = household_task）。那一行由结算按
      "同一任务取当月在用方案里残余最小的那一个"写出——评分器只读它，不重算替代，所以设备与人工同时在用也不会把节省相加；
    * τ（分项的 ``tau``）与 c（``cap``）全组共用，只写在评分规格里，不进城市条件，也不进观察。

    设备（一次付款、服务期内持续减负）与人工服务（按月付款、实际买到的月份减负）都只通过 r 起作用：同样的 q 得到同样的分，不看是设备
    还是人工、品牌或价格；成交、花钱与目标兑现都不另计分；没用掉的时间照旧月底作废，不算休息或满足感。``tasks`` 是代表性的杂务组合，
    不是全部家务：做饭（食品准备）、通勤、工作、学习、疾病恢复与未安排时间都不在里面，它们各有自己的账或评分项。
    """

    tasks: tuple[KeyStr, ...] = Field(min_length=1)
    cap: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    rule: Literal["household_baseline_minus_residual_v1"]
    unit: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _consistent(self) -> ConvenienceSpec:
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError("日常生活便利的家务任务重复：同一项家务只计一次")
        if not has_decimals(self.cap, 2):
            raise ValueError(f"c = {self.cap!r} 的精度细于两位小数")
        return self


class ScoringComponent(ContractModel):
    """一个评分分项：定义、单位与锚点 τ **在所有城市之间共用**。

    物质维可以有多个分项（每项对应一项物质细需求，由该人的物质细项权重 m_j 加权）；
    其余六维各有且只有一个分项。没有来源的分项只有城市基础条件——报告里会写明
    "当前没有行动改善机制"，不假装七维都有丰富互动。
    """

    component_id: KeyStr
    dimension: str
    material_need: KeyStr | None = None
    unit: str = Field(min_length=1, max_length=80)
    tau: Annotated[float, Field(gt=0)]
    combine: CombineRule = "sum"
    sources: tuple[ComponentSource, ...] = ()
    description: str = Field(min_length=1, max_length=400)
    #: E1-3 增补的两种局部映射。没声明就按原规则（ADR0018），旧配置不受影响。
    landscape: LandscapeSpec | None = None
    plan_commute: PlanCommuteSpec | None = None
    #: E1-5A 补充（`DECISIONS/0024`）：发展维的成长项、通勤维的广义交通。只有 ``GROWTH_TRANSPORT_VERSIONS``
    #: 能声明；没有用到时不序列化——旧规格的输出与指纹逐字节不变。
    growth: GrowthSpec | None = Field(default=None, exclude_if=lambda v: v is None)
    transport: TransportSpec | None = Field(default=None, exclude_if=lambda v: v is None)
    #: E1 补充"日常生活便利"（`DECISIONS/0033`）：只有 ``CONVENIENCE_SCORING_VERSIONS`` 能声明；没有用到时不序列化——
    #: 旧规格的输出与内容身份逐字节不变。
    convenience: ConvenienceSpec | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_validator("dimension")
    @classmethod
    def _known_dimension(cls, v: str) -> str:
        if v not in ALL_DIMENSIONS:
            raise ValueError(f"维度 {v!r} 不在 {list(ALL_DIMENSIONS)} 里")
        return v

    @model_validator(mode="after")
    def _shape(self) -> ScoringComponent:
        if (self.dimension == MATERIAL) != (self.material_need is not None):
            raise ValueError(f"{self.component_id}: 物质维分项必须写 material_need，其他维度不写")
        ids = [s.source_id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.component_id}: 来源 ID 重复")
        if self.combine == "any" and any(
            s.value not in ("spec_flag", "present") for s in self.sources
        ):
            raise ValueError(
                f"{self.component_id}: any 是布尔替代，来源必须是 spec_flag 或 present"
            )
        for src in self.sources:
            if src.functions and self.combine == "sum" and src.value != "spec_number":
                raise ValueError(
                    f"{self.component_id}/{src.source_id}: 按功能过滤的来源只取“存在”或数量时不能相加"
                    "——第二件同功能的物品不能再加一份满额；容量型功能请读明确的容量规格"
                )
        for src in self.sources:
            if src.measure is not None and self.combine not in _MEASURE_COMBINES[src.measure]:
                raise ValueError(
                    f"{self.component_id}/{src.source_id}: {src.measure} 来源不能用 {self.combine} 合并"
                    f"（只能 {sorted(_MEASURE_COMBINES[src.measure])}）"
                    + ("——存在型相加就是按件或按批刷分" if src.measure == "presence" else "")
                )
        if self.landscape is not None and self.dimension != "environment":
            raise ValueError(f"{self.component_id}: 景观加分只属于环境维")
        if self.plan_commute is not None:
            if self.dimension != "commute":
                raise ValueError(f"{self.component_id}: 方案级通勤只属于通勤维")
            if self.sources:
                raise ValueError(f"{self.component_id}: 方案级通勤读住房与工作的事实，不再挂来源")
        if self.transport is not None:
            if self.dimension != "commute":
                raise ValueError(f"{self.component_id}: 广义交通只属于通勤维（交通便捷程度）")
            if self.sources or self.plan_commute is not None:
                raise ValueError(
                    f"{self.component_id}: 广义交通读实际住房与到岗次数，不再挂来源，也不与旧的方案级通勤并存"
                )
        if self.growth is not None:
            if self.dimension != "opportunity":
                raise ValueError(f"{self.component_id}: 成长项只属于发展维（opportunity）")
            if self.sources or self.landscape is not None:
                raise ValueError(
                    f"{self.component_id}: 成长项读实际学习进度，不再挂物品来源或景观加分"
                )
        if self.convenience is not None:
            if self.material_need != DAILY_CONVENIENCE:
                raise ValueError(
                    f"{self.component_id}: 日常生活便利只属于物质维的细需求 {DAILY_CONVENIENCE}，得到 "
                    f"{self.material_need!r}"
                )
            if self.sources:
                raise ValueError(
                    f"{self.component_id}: 日常生活便利读资源账里实际减少的家务小时，不再挂物品来源"
                    "（挂来源就是按购买或件数另给一份分）"
                )
        if not math.isfinite(self.tau):
            raise ValueError(f"{self.component_id}: τ 必须是有限正数")
        return self

    @property
    def base_only(self) -> bool:
        return (
            not self.sources
            and self.plan_commute is None
            and self.transport is None
            and self.growth is None
            and self.convenience is None
        )

    @property
    def plan_level(self) -> bool:
        """这一项的值来自实际执行方案的事实（城市条件写 plan_level）：旧的方案级通勤、广义交通或日常生活便利。"""
        return (
            self.plan_commute is not None
            or self.transport is not None
            or self.convenience is not None
        )


class Omission(ContractModel):
    """一条省略：省略了什么、可能影响哪些偏好的人、归哪一轮。未覆盖不是天然零均值的残差。"""

    what: str = Field(min_length=1, max_length=200)
    affects: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=1, max_length=40)


class LifeScoringSpec(ContractModel):
    """一套探索评分规格。同一规格内，所有城市用同一套分项含义、方向、单位与锚点。"""

    spec_id: IdStr
    scoring_version: str = Field(min_length=1)
    status: Literal["exploratory_synthetic"]
    function: Literal["saturating_capped_v1"] = SCORING_FUNCTION
    protocol_id: IdStr
    components: tuple[ScoringComponent, ...] = Field(min_length=1)
    note: str = Field(min_length=1)
    omissions: tuple[Omission, ...] = ()
    #: E1-5C：健康项（γ 与 H）。只有 ``HEALTH_SCORING_VERSIONS`` 能声明，且必须声明；没有用到时不序列化——旧规格的
    #: 输出与内容身份逐字节不变。
    health: HealthScoringSpec | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _complete(self) -> LifeScoringSpec:
        ids = [c.component_id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("评分分项 ID 重复")
        health_version = self.scoring_version in HEALTH_SCORING_VERSIONS
        for dim in AMENITY_DIMENSIONS:
            n = sum(1 for c in self.components if c.dimension == dim)
            if health_version and dim == HEALTHCARE:
                if n != 0:
                    raise ValueError(
                        f"评分版本 {self.scoring_version} 没有医疗分项：城市医疗背景不进满足感，健康由全组共用的"
                        "健康项 γH 评价（DECISIONS/0027）"
                    )
                continue
            if n != 1:
                raise ValueError(f"维度 {dim} 必须恰好一个分项，得到 {n}")
        if health_version and self.health is None:
            raise ValueError(
                f"评分版本 {self.scoring_version} 必须声明健康项 health（γ 与 H；γ = 0 是关闭对照）"
            )
        if not health_version and self.health is not None:
            raise ValueError(
                f"评分版本 {self.scoring_version} 不读健康项；要启用请用 {sorted(HEALTH_SCORING_VERSIONS)}——旧版本的名字"
                "不能悄悄换成新行为"
            )
        needs = [c.material_need for c in self.components if c.dimension == MATERIAL]
        if not needs:
            raise ValueError("物质维至少要有一个分项")
        if len(needs) != len(set(needs)):
            raise ValueError("同一物质细需求只能有一个分项")
        if self.scoring_version not in ADR0018_SCORING_VERSIONS:
            problems = supplement_spec_problems(self)  # 含成长项、广义交通与日常生活便利的版本检查
        else:
            problems = growth_transport_version_problems(self) + convenience_version_problems(self)
        if problems:
            raise ValueError("；".join(problems))
        return self

    @property
    def follows_adr0018(self) -> bool:
        """这套规格是否按 ADR0018 的原规则运行（旧版本：不要求数量语义与规范单位）。"""
        return self.scoring_version in ADR0018_SCORING_VERSIONS

    def content(self) -> dict[str, object]:
        """决定计算的全部规格内容，规范化成一份确定的字典（评分内容身份，DECISIONS/0025）。

        做法：原样 ``model_dump(mode="json")``，再删掉明确列出的不参与字段——默认全部参与，以后新增的字段也参与
        （漏排除只会多报一次"内容不同"，方向安全）。参与：评分版本、状态、公式标识；每个分项的编号、维度、物质
        细需求、单位、τ、合并规则；每个来源的取值规则、过滤条件、单位与数量语义；景观 β、方案级通勤、成长项 η 与
        进度规则、广义交通 ρ 与参考到岗次数及各自单位、日常生活便利的任务组合、c、规则与单位。**不参与**：``spec_id``（名字，由反馈政策的
        ``scoring_spec_id`` 另行核对）、``protocol_id``（引用，加载器与评分器另行核对）、规格 ``note`` 与
        ``omissions``、分项 ``description``、来源 ``note``、景观 / 成长项 / 广义交通 / 日常生活便利的 ``definition``（说明文字）。

        字典键顺序不影响；列表顺序参与（分项与来源的顺序可能影响浮点求和的末位）；数值没有容差，pydantic 先把
        整数写法规范成浮点（12 与 12.0 相同）；单位按写法参与。城市评分条件、比较组、偏好与硬要求不在这里：它们
        按城、按人不同，只在年度绑定里逐年封存。
        """
        data = self.model_dump(mode="json")
        for key in SCORING_CONTENT_EXCLUDED_SPEC_FIELDS:
            data.pop(key)
        for comp in data["components"]:
            for key in SCORING_CONTENT_EXCLUDED_COMPONENT_FIELDS:
                comp.pop(key)
            for src in comp["sources"]:
                for key in SCORING_CONTENT_EXCLUDED_SOURCE_FIELDS:
                    src.pop(key)
            for mapping in ("landscape", "growth", "transport", "convenience"):
                if comp.get(mapping) is not None:
                    for key in SCORING_CONTENT_EXCLUDED_MAPPING_FIELDS:
                        comp[mapping].pop(key)
        if data.get("health") is not None:  # E1-5C：γ 与 H 参与，说明文字不参与
            for key in SCORING_CONTENT_EXCLUDED_MAPPING_FIELDS:
                data["health"].pop(key)
        return {"content_schema": SCORING_CONTENT_SCHEMA, "spec": data}

    @property
    def content_fingerprint(self) -> str:
        """``content()`` 的规范化 SHA-256（``common.life_contracts.canonical_fingerprint``）。"""
        return canonical_fingerprint(self.content())

    def component(self, component_id: str) -> ScoringComponent | None:
        return next((c for c in self.components if c.component_id == component_id), None)

    def material_components(self) -> tuple[ScoringComponent, ...]:
        return tuple(c for c in self.components if c.dimension == MATERIAL)

    def dimension_component(self, dimension: str) -> ScoringComponent:
        return next(c for c in self.components if c.dimension == dimension)

    @property
    def scored_amenities(self) -> tuple[str, ...]:
        """有分项的非物质维（E1-5C 健康版本里没有医疗维）。"""
        if self.scoring_version in HEALTH_SCORING_VERSIONS:
            return tuple(d for d in AMENITY_DIMENSIONS if d != HEALTHCARE)
        return tuple(AMENITY_DIMENSIONS)


def scoring_content_problems(
    policy: EnergyFeedbackPolicy, spec: LifeScoringSpec
) -> list[tuple[str, str]]:
    """实际加载并使用的评分规格，内容是否就是反馈政策声明的那一套（r3，DECISIONS/0025）。

    加载（``ledger.life_scoring_config.scoring_config_problems``）与年度交接（``econ.life_year``，结算之前）共用这一份。
    政策没有声明（旧格式评分版本）时没有可比的，返回空——那些协议照旧只按编号与版本绑定。返回 (问题代码, 说明)。
    """
    declared = policy.scoring_content_fingerprint
    if declared is None:
        return []
    actual = spec.content_fingerprint
    if actual == declared:
        return []
    return [
        (
            "scoring_content_mismatch",
            f"评分规格 {spec.spec_id}（{spec.scoring_version}）的实际内容身份是 {actual}，反馈政策 "
            f"{policy.policy_id} 声明的是 {declared}：编号与版本相同也不行——评分参数（η、ρ、τ、来源规则等）"
            "一变就是另一套评分内容。要用这套规格，请在政策里声明它的内容身份（这就是另一个实验身份），"
            "并从共同初始状态重新运行",
        )
    ]


def supplement_spec_problems(spec: LifeScoringSpec) -> list[str]:
    """新版本（非 ADR0018）规格的数量语义与单位检查（`DECISIONS/0019` 第十二节）。

    * 每个来源都要声明数量语义；``count`` 只读一次体验（拥有的物品按件数计分会刷分）；
    * 分项单位、来源读取单位、方案级通勤单位都要是登记过的写法；
    * 来源读取单位与分项单位同量纲（之间的换算系数写进来源表）；取值方式与量纲配套：
      存在型读布尔，数量读每月次数，规格数值读数值量纲；
    * 方案级通勤：τ 与往返分钟同一个单位（S = τ/(τ+T) 不做换算）。
    """
    out: list[str] = []
    for comp in spec.components:
        cu = resolve_unit(comp.unit)
        where = comp.component_id
        if cu is None:
            out.append(f"{where}: 分项单位 {comp.unit!r} 没有登记——不按名称猜单位")
        for src in comp.sources:
            w = f"{where}/{src.source_id}"
            if src.measure is None:
                out.append(
                    f"{w}: 新版本的来源必须声明数量语义 measure（presence/capacity/level/count）"
                )
            elif src.measure == "count" and set(src.records) - {"experience"}:
                out.append(
                    f"{w}: count 只用于一次体验（一次算一次）；拥有的物品、服务按件数计分会刷分，"
                    "请改成存在型或声明容量"
                )
            su = resolve_unit(src.unit)
            if su is None:
                out.append(f"{w}: 读取单位 {src.unit!r} 没有登记——不按名称猜单位")
                continue
            if cu is not None and conversion_factor(su, cu) is None:
                out.append(
                    f"{w}: 读取单位 {su.label}（{su.dimension}）与分项单位 {cu.label}（{cu.dimension}）"
                    "不是同一量纲，没有可用的换算"
                )
            if src.value in ("present", "spec_flag") and su.dimension != "boolean":
                out.append(f"{w}: {src.value} 读的是有/无，单位应是布尔量纲，得到 {su.label}")
            if src.value == "quantity" and su.dimension != "monthly_count":
                out.append(f"{w}: 数量取值是这个月的件数/次数，单位应是 次/月，得到 {su.label}")
            if src.value == "spec_number" and su.dimension not in NUMERIC_DIMENSIONS:
                out.append(f"{w}: 规格数值的单位必须是数值量纲，得到 {su.label}")
        pc = comp.plan_commute
        if pc is not None:
            pu = resolve_unit(pc.unit)
            if pu is None or pu.dimension != "duration":
                out.append(f"{where}: 方案级通勤的单位 {pc.unit!r} 必须是登记过的时长单位")
            elif cu is not None and cu.unit_id != pu.unit_id:
                out.append(
                    f"{where}: 方案级通勤的 τ 用分项单位 {cu.label}，往返时间用 {pu.label}——"
                    "S = τ/(τ+T) 不做换算，两者必须是同一个单位"
                )
        ts = comp.transport
        if ts is not None:
            tu = resolve_unit(ts.unit)
            if tu is None or tu.dimension != "duration":
                out.append(f"{where}: 广义交通的往返时间单位 {ts.unit!r} 必须是登记过的时长单位")
            elif cu is not None and cu.unit_id != tu.unit_id:
                out.append(
                    f"{where}: 广义交通的 τ_T 用分项单位 {cu.label}，往返时间用 {tu.label}——"
                    "S = τ_T/(τ_T + T_eq) 不做换算，两者必须是同一个单位"
                )
            vu = resolve_unit(ts.visits_unit)
            if vu is None or vu.dimension != "monthly_count":
                out.append(
                    f"{where}: 到岗次数的单位 {ts.visits_unit!r} 必须是登记过的每月次数（次/月）"
                )
        gs = comp.growth
        if gs is not None:
            gu = resolve_unit(gs.unit)
            if gu is None or gu.dimension != "ability_progress":
                out.append(
                    f"{where}: 成长项的单位 {gs.unit!r} 必须是登记过的能力进度（能力进度/月）"
                )
        cv = comp.convenience
        if cv is not None:
            vu = resolve_unit(cv.unit)
            if vu is None or vu.dimension != "monthly_duration":
                out.append(
                    f"{where}: 日常生活便利的单位 {cv.unit!r} 必须是登记过的每月小时（小时/月）——资源账按小时记、"
                    "按月结算"
                )
            elif cu is not None and cu.unit_id != vu.unit_id:
                out.append(
                    f"{where}: 日常生活便利的 τ 用分项单位 {cu.label}，减少的家务用 {vu.label}——"
                    "s = q/(q + τ) 不做换算，两者必须是同一个单位"
                )
    out.extend(growth_transport_version_problems(spec))
    out.extend(convenience_version_problems(spec))
    return out


def growth_transport_version_problems(spec: LifeScoringSpec) -> list[str]:
    """成长项与广义交通只属于 ``GROWTH_TRANSPORT_VERSIONS``，而且新版本必须两项都声明（`DECISIONS/0024`）。"""
    out: list[str] = []
    declared = [c.component_id for c in spec.components if c.growth or c.transport]
    if spec.scoring_version not in GROWTH_TRANSPORT_VERSIONS:
        if declared:
            out.append(
                f"评分版本 {spec.scoring_version} 不读成长项或广义交通，分项 {declared} 却声明了——要启用"
                f"请用 {sorted(GROWTH_TRANSPORT_VERSIONS)}，旧版本的名字不能悄悄换成新行为"
            )
        return out
    if spec.dimension_component("opportunity").growth is None:
        out.append(
            f"评分版本 {spec.scoring_version} 的发展维必须声明成长项 growth（η = 0 是关闭对照）"
        )
    if spec.dimension_component("commute").transport is None:
        out.append(f"评分版本 {spec.scoring_version} 的通勤维必须声明广义交通 transport")
    return out


def convenience_version_problems(spec: LifeScoringSpec) -> list[str]:
    """日常生活便利只属于 ``CONVENIENCE_SCORING_VERSIONS``；这些版本恰好一个便利分项，并且不再计旧的洗衣分（`DECISIONS/0033`）。

    "不叠加"的检查：物质维不能还有 ``laundry`` 细需求；任何来源不能按 ``laundry`` 或便利任务组合里的功能取"有没有"——同一项
    家务已经由"实际减少了多少小时"评价，再按"有没有洗衣机"给一份分，就是同一件事计两份。旧版本声明便利分项，或在旧版本里给
    ``daily_convenience`` 另写一套分项，都是把旧名字悄悄换成新行为，一律拒绝。
    """
    out: list[str] = []
    declared = [c for c in spec.components if c.convenience is not None]
    named = [c.component_id for c in spec.components if c.material_need == DAILY_CONVENIENCE]
    if spec.scoring_version not in CONVENIENCE_SCORING_VERSIONS:
        if declared or named:
            out.append(
                f"评分版本 {spec.scoring_version} 不读日常生活便利，分项 {sorted(set(named))} 却声明了——要启用请用 "
                f"{sorted(CONVENIENCE_SCORING_VERSIONS)}，旧版本的名字不能悄悄换成新行为"
            )
        return out
    if len(declared) != 1:
        out.append(
            f"评分版本 {spec.scoring_version} 必须恰好有一个日常生活便利分项（物质细需求 {DAILY_CONVENIENCE}，声明 "
            f"convenience），得到 {len(declared)} 个"
        )
    for c in spec.components:
        if c.material_need == DAILY_CONVENIENCE and c.convenience is None:
            out.append(
                f"{c.component_id}: 细需求 {DAILY_CONVENIENCE} 只能由 convenience 规则评价（读实际减少的家务小时）"
            )
        if c.material_need == LEGACY_LAUNDRY:
            out.append(
                f"{c.component_id}: 评分版本 {spec.scoring_version} 用日常生活便利替换了旧的洗衣分——两者不能并存，"
                "否则同一项家务计两份分"
            )
    tasks = {t for c in declared for t in c.convenience.tasks}  # type: ignore[union-attr]
    for c in spec.components:
        for s in c.sources:
            hit = sorted(set(s.functions) & (tasks | {LEGACY_LAUNDRY}))
            if hit:
                out.append(
                    f"{c.component_id}/{s.source_id}: 按家务功能 {hit} 取“有没有”的来源与日常生活便利重复计分"
                    "（便利只读实际减少的家务小时）"
                )
    return out


class SourceEfficiency(ContractModel):
    source_id: KeyStr
    eta: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class NatureCondition(ContractModel):
    """一座城的自然景观/户外吸引力指标 A_nature（0–1，`DECISIONS/0019` 第七节）。

    ``unknown`` 不是零。``base_includes_landscape``：基础环境若已经包含同一景观，再加景观分就是
    重复计分，加载时拒绝。来源必须是直接资料、可审计派生、估计或显式合成之一。
    """

    status: Literal["known", "unknown"]
    a_nature: Annotated[float, Field(ge=0, le=1)] | None = None
    base_includes_landscape: bool = False
    unknown_reason: str | None = Field(default=None, max_length=280)
    source: SourceRecord

    @model_validator(mode="after")
    def _status_consistent(self) -> NatureCondition:
        if (self.status == "known") != (self.a_nature is not None):
            raise ValueError("景观指标：已知必须给 a_nature，未知不能带数值——未知不是零")
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("景观指标未知时必须说明原因")
        return self


class CityComponentCondition(ContractModel):
    """一座城在一个评分分项上的条件：基础量 b、上限 c、各来源的改善效率 η。

    ``status = unknown``：这座城这项没有数据。**不是零**：有正权重时该月总分不完整，
    不补零，也不把这项删掉后重新归一化其余权重。

    ``status = plan_level``（E1-3 增补）：这一项的值来自实际执行方案的事实（方案级通勤），
    不用城市背景，所以不带 b、c、η。
    """

    component_id: KeyStr
    status: Literal["known", "unknown", "plan_level"]
    base: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    cap: Annotated[float, Field(ge=0, le=1)] | None = None
    efficiencies: tuple[SourceEfficiency, ...] = ()
    #: ``combine = any`` 的分项只有一个 η：需求被满足时的改善量（与哪个来源满足无关）。
    eta_any: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    unknown_reason: str | None = Field(default=None, max_length=280)
    source: SourceRecord
    #: 只用于启用了景观加分的环境分项。
    nature: NatureCondition | None = None

    @model_validator(mode="after")
    def _status_consistent(self) -> CityComponentCondition:
        if self.status == "known":
            if self.base is None or self.cap is None:
                raise ValueError(f"{self.component_id}: 已知条件必须给 base 与 cap")
            if self.unknown_reason is not None:
                raise ValueError(f"{self.component_id}: 已知条件不写 unknown_reason")
        elif self.status == "plan_level":
            if (
                self.base is not None
                or self.cap is not None
                or self.efficiencies
                or self.eta_any is not None
                or self.unknown_reason is not None
                or self.nature is not None
            ):
                raise ValueError(
                    f"{self.component_id}: 方案级条件不用城市背景，不能带 b、c、η 或景观"
                )
        else:
            if self.base is not None or self.cap is not None or self.efficiencies or self.eta_any:
                raise ValueError(f"{self.component_id}: 未知条件不能带数值——未知不是零")
            if not self.unknown_reason:
                raise ValueError(f"{self.component_id}: 未知条件必须说明为什么未知")
        ids = [e.source_id for e in self.efficiencies]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.component_id}: 改善效率的来源重复")
        return self

    def eta_for(self, source_id: str) -> float | None:
        return next((e.eta for e in self.efficiencies if e.source_id == source_id), None)


class CityScoringConditions(ContractModel):
    """一座城对一套评分规格的全部条件。城市身份用完整的 国家/体系/代码。"""

    city: CityId
    spec_id: IdStr
    label: str = Field(min_length=1, max_length=120)
    components: tuple[CityComponentCondition, ...]

    @model_validator(mode="after")
    def _unique(self) -> CityScoringConditions:
        ids = [c.component_id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.city}: 评分条件的分项重复")
        return self

    def condition(self, component_id: str) -> CityComponentCondition | None:
        return next((c for c in self.components if c.component_id == component_id), None)


class ComparisonGroup(ContractModel):
    """一个比较组的共同粒度（`DECISIONS/0019` 第五节）：组内所有城市用同一套口径。

    地理粒度、时间基准（币种、基年）、行动类型、规格键与单位、允许的来源类型、通勤粒度、
    评分版本都在这里冻结。检查拒绝"某城细化、某城用别的粒度或默认值填"；真正不可得仍是合法
    事实，不要求每城机会数量相同。
    """

    group_id: IdStr
    cities: tuple[CityId, ...] = Field(min_length=1)
    scoring_version: str = Field(min_length=1)
    geography_scope: str = Field(min_length=1)
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    base_year: Annotated[int, Field(ge=1900, le=2100)]
    time_basis: str = Field(min_length=1, max_length=200)
    action_roles: tuple[ItemRole, ...] = Field(min_length=1)
    allowed_source_kinds: tuple[SourceKind, ...] = Field(min_length=1)
    commute_granularity: Literal["background", "plan_level"]
    units: dict[KeyStr, Annotated[str, Field(min_length=1, max_length=80)]]
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> ComparisonGroup:
        keys = [str(c) for c in self.cities]
        if len(keys) != len(set(keys)):
            raise ValueError(f"{self.group_id}: 城市重复")
        if len(set(self.action_roles)) != len(self.action_roles):
            raise ValueError(f"{self.group_id}: action_roles 重复")
        return self


# ==========================================================================
# 偏好：来源必须明确
# ==========================================================================

PreferenceSource = Literal[
    "research_synthetic_truth", "product_stated_numeric", "product_confirmed_six_dim"
]
#: E3-A（DECISIONS/0034）：六维产品画像的偏好来源。它的 ``dimension_weights`` 只有六维（没有 healthcare），就是用户确认的精确点数
#: / 100 只转一次浮点得到的有效权重；只能交给健康评分版本（那里有效权重本来就只含六维）。
SIX_DIM_DIMENSIONS: tuple[str, ...] = tuple(d for d in ALL_DIMENSIONS if d != HEALTHCARE)


class ScoringPreference(ContractModel):
    """评分用的权重。**来源写在对象上**，研究与产品两条路径不混。

    * ``research_synthetic_truth``：合成设定的已知偏好（研究内部）。
    * ``product_stated_numeric``：用户明确给出的数值（七维定和点数、物质细项定和点数）
      按声明的规则 / 100 换算；不从序数或文字臆造，不借用研究真值。
    * ``product_confirmed_six_dim``（E3-A）：用户确认的六维精确点数 / 100（只有六个键，健康回答独立、不进权重），物质细项精确点数
      / 100；物质父项为 0 时没有细项权重。

    权重不随城市变化，也不用实际花钱比例冒充偏好。不在 ``material_weights`` 里的物质
    细需求权重为 0（定和量表的含义）。
    """

    person_id: IdStr
    source: PreferenceSource
    dimension_weights: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]]
    material_weights: dict[KeyStr, Annotated[float, Field(ge=0, allow_inf_nan=False)]]
    derivation: str = Field(min_length=1, max_length=400)
    #: E1-5C：七维 healthcare 权重的含义，照抄输入（画像或研究真值）。``health_risk_coping_v1`` 时只有健康评分版本能用它，
    #: 而且六维换算会把它排除在外；为空是旧含义，只有旧版本能用。没有用到时不序列化。
    healthcare_semantics: Literal["health_risk_coping_v1"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def _weights_ok(self) -> ScoringPreference:
        if self.source == "product_confirmed_six_dim":
            # E3-A：六维产品画像只有六个键；不能补一个 healthcare 权重去凑旧的七维形状
            if set(self.dimension_weights) != set(SIX_DIM_DIMENSIONS):
                raise ValueError(f"六维产品画像的权重必须恰好是 {list(SIX_DIM_DIMENSIONS)}")
            if self.healthcare_semantics is not None:
                raise ValueError("六维产品画像没有 healthcare 点数，不写 healthcare_semantics")
        elif set(self.dimension_weights) != set(ALL_DIMENSIONS):
            raise ValueError(f"七维权重必须恰好是 {list(ALL_DIMENSIONS)}")
        if abs(math.fsum(self.dimension_weights.values()) - 1.0) > WEIGHT_SUM_TOL:
            raise ValueError(
                "六维权重必须合计 1"
                if self.source == "product_confirmed_six_dim"
                else "七维权重必须合计 1"
            )
        if (
            self.material_weights
            and abs(math.fsum(self.material_weights.values()) - 1.0) > WEIGHT_SUM_TOL
        ):
            raise ValueError("物质细项权重必须合计 1")
        if self.dimension_weights[MATERIAL] > 0 and not self.material_weights:
            raise ValueError(
                "物质维权重为正时必须有物质细项权重；信息不足请返回 PreferenceInsufficient"
            )
        return self


class PreferenceInsufficient(ContractModel):
    """偏好信息不足：**不**偷偷补均匀权重，也不借用研究真值。"""

    person_id: IdStr
    source: PreferenceSource
    missing: tuple[str, ...] = Field(min_length=1)
    message: str = Field(min_length=1, max_length=400)


# ==========================================================================
# 结果
# ==========================================================================

ComponentStatus = Literal["known", "unknown"]
#: 月度状态。``exit_month``：出局当月——当月工资已入账，不代表过完了这个月；
#: ``after_exit``：出局之后，没有继续模拟。这两种都**没有**分数，也不当普通缺失跳过。
#: E1-5C：``stopped_month`` 停止月（执行没有提交）与 ``after_stop`` 停止之后——同样没有分数，也不当普通缺失跳过。
MonthStatus = Literal[
    "scored", "incomplete", "exit_month", "after_exit", "stopped_month", "after_stop"
]
DimensionStatus = Literal["known", "unknown", "not_weighted_unknown"]


class ImprovementTerm(ContractModel):
    """一项实际生活条件对某分项的改善：η × x。

    ``counted``：是否计入 z。``sum`` 全部计入；``max`` 只计最好的一项；``any`` 只计第一项
    满足的来源（同一需求多处满足只计一次）。所以总有 z = b + Σ（计入的 contribution）。
    """

    source_id: KeyStr
    record: ScoringRecord
    opportunity_id: IdStr
    x: Annotated[float, Field(ge=0)]
    eta: Annotated[float, Field(ge=0)]
    contribution: Annotated[float, Field(ge=0)]
    counted: bool


class LandscapeTerm(ContractModel):
    """环境分项的景观加分明细：S_env = min(c, S_physical + β·A)。"""

    beta: float
    a_nature: float
    physical: float  # S_physical = b/(b+τ)
    contribution: float  # β × A
    source_kind: SourceKind


class CommuteTerm(ContractModel):
    """方案级通勤的明细：这个月住在哪、做哪份线下工作、每工作日往返多少分钟。"""

    housing_opportunity_id: IdStr
    job_opportunity_id: IdStr
    minutes: Annotated[float, Field(ge=0)]
    tau_minutes: float


class AbilityProgressStep(ContractModel):
    """一项能力在一个月里的进度变化（成长项明细）。``via`` 是这个月实际参加、授予它的课程。"""

    ability_id: KeyStr
    before: Annotated[float, Field(ge=0, le=1)]  # p_a,m−1
    after: Annotated[float, Field(ge=0, le=1)]  # p_a,m
    delta: Annotated[float, Field(ge=0, le=1)]
    via: tuple[IdStr, ...] = ()


class GrowthTerm(ContractModel):
    """发展维成长项一个月的明细：g_m（能力进度/月）、η_growth、贡献 η·g；``steps`` 只列有进步的能力。"""

    g: Annotated[float, Field(ge=0)]
    eta: Annotated[float, Field(ge=0)]
    contribution: Annotated[float, Field(ge=0)]
    steps: tuple[AbilityProgressStep, ...] = ()


class TransportTerm(ContractModel):
    """广义交通一个月的明细。``equivalent_minutes`` 是评分中间量 T_eq = k·T，**不是**实际出行分钟。"""

    housing_opportunity_id: IdStr
    minutes: Annotated[float, Field(ge=0)]  # T：实际住房的往返分钟
    onsite_visits: Annotated[int, Field(ge=0)]  # D：资源账的累计到岗次数
    reference_visits: int  # D_ref
    rho: float
    visit_factor: Annotated[float, Field(ge=0)]  # k = ρ + (1 − ρ)·D/D_ref
    equivalent_minutes: Annotated[float, Field(ge=0)]  # T_eq
    tau_minutes: float


class TaskRelief(ContractModel):
    """日常生活便利一个月里的一项家务：协议基准、资源账那一行的本人残余、减少的小时，以及承担替代的方案（资源账那一行写的；
    本人全做时为空）。都是精确分数。"""

    task: KeyStr
    baseline_hours: ExactAmount  # b_t
    residual_hours: ExactAmount  # r_t,m
    relief_hours: ExactAmount  # b_t − r_t,m
    opportunity_id: IdStr | None = None


class ConvenienceTerm(ContractModel):
    """日常生活便利一个月的明细：逐项家务与合计 q（小时/月）、τ 与 c。分数只取决于 q——设备还是人工、价格多少都不进入。"""

    tasks: tuple[TaskRelief, ...]
    relief_hours: ExactAmount  # q_m
    tau_hours: float
    cap: Annotated[float, Field(ge=0, le=1)]


class ComponentMonth(ContractModel):
    component_id: KeyStr
    dimension: str
    material_need: KeyStr | None = None
    status: ComponentStatus
    base_only: bool
    combine: CombineRule
    tau: float
    base: float | None = None
    cap: float | None = None
    terms: tuple[ImprovementTerm, ...] = ()
    z: float | None = None
    raw: float | None = None  # z / (z + τ)，加上限之前
    s: float | None = None
    capped: bool = False
    reasons: tuple[str, ...] = ()
    landscape: LandscapeTerm | None = None
    commute: CommuteTerm | None = None
    #: E1-5A 补充（`DECISIONS/0024`）。没有用到时不序列化——旧评分结果逐字节不变。
    growth: GrowthTerm | None = Field(default=None, exclude_if=lambda v: v is None)
    transport: TransportTerm | None = Field(default=None, exclude_if=lambda v: v is None)
    #: E1 补充"日常生活便利"（`DECISIONS/0033`）。没有用到时不序列化——旧评分结果逐字节不变。
    convenience: ConvenienceTerm | None = Field(default=None, exclude_if=lambda v: v is None)


class MaterialTerm(ContractModel):
    need: KeyStr
    weight: float  # m_j
    status: ComponentStatus | Literal["no_component"]
    s: float | None = None
    contribution: float | None = None  # m_j × s_j


class DimensionMonth(ContractModel):
    dimension: str
    weight: float  # w_k
    status: DimensionStatus
    s: float | None = None
    contribution: float | None = None  # w_k × s_k
    material_terms: tuple[MaterialTerm, ...] = ()


class HealthTerm(ContractModel):
    """E1-5C 一个月的健康项：月初状态 q、H(q)、γ、计入总分的 γH 与相对健康的直接扣分 γ(1 − H)。

    扣分对同等病情的每个人都一样，不乘个人的任何权重；治疗付费不改变它（治疗只影响之后的月份）。
    γ(1 − H) 是"相对健康"的扣分，因为 H(健康) 固定为 1（``HealthStateValues``，收尾修订）：健康月份扣 0。"""

    state: Literal["healthy", "minor", "major"]
    value: Annotated[float, Field(ge=0, le=1)]  # H(q)
    gamma: Annotated[float, Field(ge=0, le=1)]
    contribution: Annotated[float, Field(ge=0, le=1)]  # γ × H
    deduction: Annotated[float, Field(ge=0, le=1)]  # γ × (1 − H)
    daily_life_share: Annotated[float, Field(ge=0, le=1)]  # 1 − γ


class MonthScore(ContractModel):
    month: Month
    status: MonthStatus
    u: float | None = None
    dimensions: tuple[DimensionMonth, ...] = ()
    components: tuple[ComponentMonth, ...] = ()
    reasons: tuple[str, ...] = ()
    #: E1-5C：日常生活分 B（六维按有效权重加权）与健康项；总分 u = (1 − γ)·B + γ·H。没有用到时不序列化。
    daily_life: float | None = Field(default=None, exclude_if=lambda v: v is None)
    health: HealthTerm | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _u_matches_status(self) -> MonthScore:
        if (self.status == "scored") != (self.u is not None):
            raise ValueError(f"第 {self.month} 月：只有 scored 才有月度分")
        if self.status in ("exit_month", "after_exit") and (self.dimensions or self.components):
            raise ValueError(f"第 {self.month} 月：出局当月与之后不评分项")
        if self.status in ("stopped_month", "after_stop") and (
            self.dimensions or self.components or self.health is not None
        ):
            raise ValueError(f"第 {self.month} 月：停止月与之后不评分项")
        if self.daily_life is not None and self.status != "scored":
            raise ValueError(f"第 {self.month} 月：只有 scored 才有日常生活分")
        return self


RequirementMonthStatus = Literal["satisfied", "not_satisfied", "unknown", "not_lived"]
RequirementSummary = Literal[
    "satisfied_every_month",
    "partially_satisfied",
    "not_satisfied",
    "undetermined",
    "satisfied_in_lived_months",
    "not_evaluated",
]


class RequirementEvidence(ContractModel):
    month: Month
    status: RequirementMonthStatus
    items: tuple[IdStr, ...] = ()  # 满足它的实际在用项目
    reason: str | None = Field(default=None, max_length=280)


class HardRequirementResult(ContractModel):
    """一条硬要求在这一年的检验。**单独报告**，不进分数，不触发出局，不额外扣分。

    六个月满足就是六个月满足：``summary`` 只有十二个月都满足才是
    ``satisfied_every_month``；出局那年最多是 ``satisfied_in_lived_months``。
    """

    need: KeyStr
    scope: Literal["every_month"]
    months_satisfied: tuple[Month, ...]
    months_not_satisfied: tuple[Month, ...]
    months_unknown: tuple[Month, ...]
    months_not_lived: tuple[Month, ...]
    summary: RequirementSummary
    evidence: tuple[RequirementEvidence, ...]


AnnualStatus = Literal["complete", "incomplete_months", "exited", "stopped"]


class HealthWeighting(ContractModel):
    """E1-5C 这一年的权重换算（`DECISIONS/0027`）：原始七维权重、有效六维权重、γ 与 H。

    ``raw_dimension_weights`` 是输入原样（产品：用户点数 / 100；研究：合成真值），其中 healthcare 是健康风险应对倾向；
    ``effective_weights`` = w_k / Σ_{j≠医疗} w_j，只含六维、合计为 1。

    E3-A（DECISIONS/0034）：六维产品画像没有 healthcare 点数——``raw_dimension_weights`` 只有六维（确认点数 / 100），
    ``healthcare_semantics`` 为空、``independent_health_input`` 写独立健康回答的含义；不为了旧形状伪造一份七维原始权重。两者恰好写一个。"""

    raw_dimension_weights: dict[str, float]
    healthcare_semantics: Literal["health_risk_coping_v1"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    six_dimension_sum: float
    effective_weights: dict[str, float]
    gamma: float
    state_values: HealthStateValues
    formula: str = Field(min_length=1, max_length=280)
    #: E3-A：六维产品画像的健康回答是独立问卷（不占 100 分、不进权重）。旧记录没有这一项（不序列化，逐字节不变）。
    independent_health_input: Literal["independent_health_answers_v1"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def _one_health_input(self) -> HealthWeighting:
        if (self.healthcare_semantics is None) == (self.independent_health_input is None):
            raise ValueError(
                "权重换算记录必须恰好说明一种健康输入：七维 healthcare 点数的含义，或独立健康回答（六维产品画像）"
            )
        if self.independent_health_input is not None and HEALTHCARE in self.raw_dimension_weights:
            raise ValueError("六维产品画像的原始权重没有 healthcare：不伪造医疗点数")
        return self


class ExploratoryYearScore(ContractModel):
    """一个分身一年的**探索**评分（内部对象，不进代理观察或反馈）。

    不是 E1-1 预留的正式 ``InternalYearScore``：评分映射仍是未定政策，``formal_scoring_ready``
    照实写协议的状态（本轮为 false）。``annual_mean`` 只有十二个月都 ``scored`` 才有；
    不完整、出局的年份不生成伪完整年均分。
    """

    person_id: IdStr
    clone_id: IdStr
    year_index: YearIndex
    city: CityId
    spec_id: IdStr
    scoring_version: str
    scoring_function: Literal["saturating_capped_v1"]
    status: Literal["exploratory_synthetic"]
    formal_scoring_ready: bool
    preference_source: PreferenceSource
    preference_derivation: str
    months: tuple[MonthScore, ...]
    annual_mean: float | None = None
    annual_status: AnnualStatus
    months_scored: tuple[Month, ...]
    months_incomplete: tuple[Month, ...]
    months_exit: tuple[Month, ...]
    months_after_exit: tuple[Month, ...]
    hard_requirements: tuple[HardRequirementResult, ...] = ()
    explanation: str = Field(min_length=1)
    notes: tuple[str, ...] = ()
    #: 持有物数量从哪来：结算写出（recorded）、旧记录推断（legacy_order_inference，只有旧评分版本）、
    #: 或这一年没有持有物（no_holdings）。
    holding_quantity_source: Literal["recorded", "legacy_order_inference", "no_holdings"]
    #: 在用物品上记录了、本轮不评价的方案属性（品牌、档次、菜系……）。不从价格推断偏好。
    unevaluated_attributes: tuple[str, ...] = ()
    #: E1-5C：停止月与停止之后的月份；健康评分版本的权重换算。没有用到时不序列化。
    months_stopped: tuple[Month, ...] = Field(default=(), exclude_if=lambda v: v == ())
    months_after_stop: tuple[Month, ...] = Field(default=(), exclude_if=lambda v: v == ())
    health_weighting: HealthWeighting | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _consistent(self) -> ExploratoryYearScore:
        if [m.month for m in self.months] != list(range(1, MONTHS_PER_YEAR + 1)):
            raise ValueError("必须逐月给出 1–12 月")
        if (self.annual_status == "complete") != (self.annual_mean is not None):
            raise ValueError("只有十二个月都可评分才有年均分")
        return self


# ==========================================================================
# 覆盖报告（E1-3 增补，`DECISIONS/0019` 第六节；2026-10-08 订正为 coverage-v2，第十二节）
# 只报告，不解锁总分
# ==========================================================================

LeafSupport = Literal["supported", "unknown_in_some_city", "no_component"]
EvidenceClass = Literal["observed_or_derived", "estimated", "synthetic_only", "mixed", "none"]
#: 覆盖报告版本。coverage-v1（2026-10-07）只核对评分条件有没有声明，**没有**核对目录事实与
#: 功能供给；它的 data_coverage 不代表生活资料齐全。coverage-v2 起分开报三层与两类来源。
CoverageReportVersion = Literal["coverage-v1", "coverage-v2"]
#: 规则覆盖：评分规格有没有定义这项需求怎样评分。
RuleCoverage = Literal["defined", "no_component"]
#: 城市资料覆盖：比较组每座城是否提供了这项规则读到的事实（规格键、功能供给、景观、通勤分钟）。
CityDataCoverage = Literal["complete", "gap", "not_applicable"]
#: 实际行动可评分性：只要城市资料齐全，任何实际安排都能评；还是要看这个人的实际安排
#: （例如方案级通勤只评恰好一份线下工作的月份）。
ActionScope = Literal["all_arrangements", "conditional", "not_applicable"]


class LeafCoverage(ContractModel):
    """一个偏好叶子的覆盖。

    ``support`` 沿用 v1 的三种取值，但 v2 的 supported 意味着规则已定义**且**每座城的资料齐全。
    ``fact_evidence`` 是生活事实（目录规格、功能供给声明、景观指标、住房分钟与工作方式）的来源；
    ``parameter_evidence`` 是评分映射参数（城市评分条件里的 b、c、η）的来源。参数是估计，不会让
    合成的住房、商品或通勤资料变成真实资料。``evidence`` 是两者合起来的类别。
    """

    leaf: str
    dimension: str
    weight: float  # 非物质维 a = w_k；物质细项 a = w_material × m_j
    support: LeafSupport
    evidence: EvidenceClass
    action_mechanism: bool  # 背景已知不代表行动效果也建模了
    cities_unknown: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    # ---- coverage-v2 起 ----
    rule: RuleCoverage | None = None
    city_data: CityDataCoverage | None = None
    action_scope: ActionScope | None = None
    fact_evidence: EvidenceClass | None = None
    parameter_evidence: EvidenceClass | None = None
    data_gaps: tuple[str, ...] = ()


class CoverageReport(ContractModel):
    """一个人在一个比较组上的偏好覆盖。**不设阈值，不解锁总分**；正权重未知时总分照旧为空。

    ``report_version`` 缺省为 coverage-v1，是为了旧报告（2026-10-07 的演示输出）还能读进来；
    v1 没有核对目录事实，``catalog_facts_checked`` 为 False，它的标签不能当成生活资料齐全的证明。
    """

    person_id: IdStr
    preference_source: PreferenceSource
    group_id: IdStr
    cities: tuple[str, ...]
    leaves: tuple[LeafCoverage, ...]
    common_weight: float  # C_i = Σ_{ℓ∈J} a_iℓ
    weight_by_evidence: dict[str, float]
    unknown_weight: float
    no_component_weight: float
    coverage_label: Literal["synthetic_contract_coverage", "data_coverage", "mixed_coverage"]
    omissions: tuple[Omission, ...] = ()
    notes: tuple[str, ...] = ()
    report_version: CoverageReportVersion = "coverage-v1"
    fact_weight_by_evidence: dict[str, float] = Field(default_factory=dict)
    parameter_weight_by_evidence: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _v2_complete(self) -> CoverageReport:
        if self.report_version == "coverage-v2":
            for leaf in self.leaves:
                if None in (leaf.rule, leaf.city_data, leaf.action_scope, leaf.fact_evidence):
                    raise ValueError(f"coverage-v2 的叶子 {leaf.leaf} 必须写明三层覆盖与事实来源")
        return self

    @property
    def catalog_facts_checked(self) -> bool:
        return self.report_version != "coverage-v1"


# ==========================================================================
# 信息隔离：内部评分参数的保留键名与"原样序列化"检查（`DECISIONS/0019` 第十二节）
# ==========================================================================

#: 只该出现在评分侧对象里的参数键名。生活目录的规格、属性与安排项不能用它们，可见观察与最终
#: payload 里出现就拒绝。只收明显属于评分参数的名字；"定义""单位""状态"这类通用词不收——
#: 它们由下面的结构检查兜住，免得把正常的品牌、菜系、住房规格、通勤分钟一起挡掉。也不收可见对象
#: 自己的字段名（例如报价的 per_unit）——有测试盯着两边不重叠。
RESERVED_SCORING_KEYS: frozenset[str] = frozenset(
    {
        "beta",
        "tau",
        "tau_minutes",
        "eta",
        "eta_any",
        "efficiencies",
        "a_nature",
        "base_includes_landscape",
        "minutes_key",
        "work_mode_key",
        "onsite_value",
        "spec_key",
        "requires_spec_key",
        "flag_value",
        "material_need",
        "scoring_function",
        "scoring_version",
        "spec_id",
        "common_weight",
        "coverage_label",
        "weight_by_evidence",
        "unknown_weight",
        "no_component_weight",
        "allowed_source_kinds",
        "commute_granularity",
        "annual_mean",
        "material_weights",
        "holding_quantity_source",
        # E1-5A 补充（`DECISIONS/0024`）：成长项与广义交通的评分参数与中间量
        "progress_rule",
        "rho",
        "reference_visits",
        "visit_factor",
        "equivalent_minutes",
        # 评分内容身份（`DECISIONS/0025`）：内部绑定，不进观察
        "scoring_content_fingerprint",
        # E1-5C 健康项（`DECISIONS/0027`）：γ、H 与权重换算
        "gamma",
        "state_values",
        "daily_life",
        "daily_life_share",
        "effective_weights",
        "raw_dimension_weights",
        "six_dimension_sum",
        "health_weighting",
        # E1 补充"日常生活便利"（`DECISIONS/0033`）：评分侧的减负合计与锚点（家务基准与方案残余是公开的资源事实，不收）
        "relief_hours",
        "tau_hours",
    }
)


def internal_scoring_models() -> tuple[type[ContractModel], ...]:
    """评分侧内部对象的类型。它们本身、以及它们原样序列化后的字典，都不能进观察。"""
    return (
        LifeScoringSpec,
        ScoringComponent,
        ComponentSource,
        LandscapeSpec,
        PlanCommuteSpec,
        GrowthSpec,
        TransportSpec,
        ConvenienceSpec,
        Omission,
        SourceEfficiency,
        NatureCondition,
        CityComponentCondition,
        CityScoringConditions,
        ComparisonGroup,
        ScoringPreference,
        ExploratoryYearScore,
        MonthScore,
        DimensionMonth,
        ComponentMonth,
        MaterialTerm,
        ImprovementTerm,
        LandscapeTerm,
        CommuteTerm,
        AbilityProgressStep,
        GrowthTerm,
        TransportTerm,
        TaskRelief,
        ConvenienceTerm,
        HealthStateValues,
        HealthScoringSpec,
        HealthTerm,
        HealthWeighting,
        RequirementEvidence,
        HardRequirementResult,
        LeafCoverage,
        CoverageReport,
    )


def internal_models() -> tuple[type[ContractModel], ...]:
    """全部内部对象类型：评分侧（``internal_scoring_models``）加 E1-4B 反馈侧
    （``common.life_feedback_contracts.internal_feedback_models``）、E1-6A 评价侧
    （``common.life_evaluation_contracts.internal_evaluation_models``）与 E1-6B 汇总侧
    （``common.life_aggregation_contracts.internal_aggregation_models``）。正式加载、可见投影与最终 payload
    三处检查都读这一份；新增内部类型登记在对应的那一侧，不在别处另写清单。"""
    from common.life_aggregation_contracts import internal_aggregation_models
    from common.life_evaluation_contracts import internal_evaluation_models
    from common.life_feedback_contracts import internal_feedback_models

    return (
        *internal_scoring_models(),
        *internal_feedback_models(),
        *internal_evaluation_models(),
        *internal_aggregation_models(),
    )


def serialized_internal(mapping: object) -> str | None:
    """一个字典里是否藏着某个内部评分对象的原样序列化结果；是就返回类型名。

    做法：对每个内部类型，取字典里与它字段同名的那部分；必填字段都在、并且能原样校验成这个
    类型，就算命中。所以把 LandscapeSpec 的 ``{beta, definition}`` 混进一份有别的键的属性字典，
    也照样被认出来。**防护边界**：这是对已知内部结构的检查，不是对任意改名或自然语言隐写的通用
    检测器；改了键名或写成一句话，它认不出来，也不作这种承诺。
    """
    if not isinstance(mapping, dict) or not mapping:
        return None
    keys = {k for k in mapping if isinstance(k, str)}
    for model in internal_models():
        fields = model.model_fields
        required = {n for n, f in fields.items() if f.is_required()}
        if len(required) < 2 or not required <= keys:
            continue
        projection = {k: mapping[k] for k in fields if k in mapping}
        try:
            model.model_validate(projection)
        except ValueError:
            continue
        return model.__name__
    return None
