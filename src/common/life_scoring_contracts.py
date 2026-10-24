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
（六维产品画像）照旧按许可可见，这里不改变那条边界。
"""

from __future__ import annotations

import math
from decimal import Decimal
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

#: 七维常量里医疗维的键：评分没有医疗分项（城市医疗背景不进满足感，健康由全组共用的健康项 γH 评价）。
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
#: ``sum_of_source_max``（DECISIONS/0042）：每个来源先在这个月在用的东西里取最好的一项，再把不同来源相加——同一处住房的几项
#: 条件（空间等级、条件档次）各算一次再平滑相加；同一来源的两件东西不会相加（两处住房不会把空间加倍）。
CombineRule = Literal["sum", "max", "any", "sum_of_source_max"]
#: 来源的取值方式：规格里的数值、规格里的是/否条件、数量，或"存在"（E1-3 增补：只要这个月
#: 有一件在用的候选就取 1，配合功能过滤与 any 使用，第二件同功能的物品不再加分）。
ValueMode = Literal["spec_number", "spec_flag", "quantity", "present"]
#: 景观加分系数的探索上限（`DECISIONS/0019` 第七节）。A_nature ≤ 1，所以景观最多加半格再截顶。
LANDSCAPE_BETA_MAX = 0.5
#: 评分版本（DECISIONS/0018、0019、0024、0027、0033）。只有这一版：
#:
#:     w̃_k = w_k（六维产品画像的有效权重，合计 1）        B_m = Σ_k w̃_k s_{k,m}        S_m = (1 − γ) B_m + γ H(q_m)
#:
#: 六维（物质、安全、发展、休闲、环境、交通）各有分项，医疗维没有分项；发展维带成长项、交通维是广义交通；物质三项里
#: daily_convenience 读这个月实际减少的家务小时。H 读月初健康状态、全组共用，不乘任何个人权重。
SCORING_VERSION = "e1-life-v1"
#: 便利分项的物质细需求键（产品输入里的"日常生活便利"：愿意在多大程度上花钱减少日常杂务）。
DAILY_CONVENIENCE = "daily_convenience"
#: 生活扩展（DECISIONS/0041）的物质细需求键：饮食生活、个人消费与享受。
FOOD_LIFE = "food_life"
PERSONAL_ENJOYMENT = "personal_enjoyment"
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
#: 来源的数量语义（`DECISIONS/0019` 第十二节）。每个来源都必须声明：
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
    "presence": frozenset({"any", "max", "sum_of_source_max"}),
    "capacity": frozenset({"sum", "max", "sum_of_source_max"}),
    "level": frozenset({"max", "sum_of_source_max"}),
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
    #: 数量语义，见 ``SourceMeasure``。
    measure: SourceMeasure

    @model_validator(mode="after")
    def _measure_consistent(self) -> ComponentSource:
        m = self.measure
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

    * T_m（DECISIONS/0042）：读这个月的通勤执行记录——有到岗时是按到岗次数加权的往返分钟（时间账用的同一份记录）；
      没有到岗时是实际住处所在居住片区的居民平时往返分钟（``AreaDecl.typical_roundtrip_minutes``）；
    * D_m：这个月实际累计到岗次数，读资源账的 ``onsite_visits``（时间账用的同一个计数，多岗位相加）；
    * ρ：不因上班消失的交通影响（无工作、纯远程时 k = ρ）；D_ref：参考到岗次数。

    T_eq 是评分中间量，**不是**实际出行分钟；时间与负担账不乘 k、不重复扣。用住房的工作往返分钟代理广义出行
    条件是合成映射假设：购物、社交、就医与公共交通可及性没有单独建模。ρ、D_ref 全组共用，只写在评分规格里。
    """

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
    """物质维"日常支持与便利"分项（`DECISIONS/0033`；E3B 映射修正 §5）：日常杂务是否省心，加上通信联网与家用日耗品是否方便获得。

        q_m = Σ_{t ∈ tasks} (b_t − r_{t,m})        S_chore = min(c, q_m / (q_m + τ))
        S_support = c_support × (这个月实际获得的支持功能数) / (支持功能总数)
        s = w_chore × S_chore + w_support × S_support

    支持功能（``support_functions``）各按"未获得 = 0、获得 = 1"：这个月有付了钱、实际在用的、提供这项功能的服务才算获得；同功能
    买两份不叠加；更贵更快的套餐不另加分；不制造节省小时，不改时间账。权重、上限与三项等权是全组共用的探索设定，只写在评分规格里。

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
    rule: Literal["chores_and_daily_support_v1"]
    support_functions: tuple[KeyStr, ...] = Field(min_length=1)
    support_cap: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    chore_weight: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    support_weight: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    unit: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _consistent(self) -> ConvenienceSpec:
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError("日常生活便利的家务任务重复：同一项家务只计一次")
        if len(set(self.support_functions)) != len(self.support_functions):
            raise ValueError("日常支持功能重复：同一项功能只计一次")
        for name in ("cap", "support_cap", "chore_weight", "support_weight"):
            if not has_decimals(getattr(self, name), 2):
                raise ValueError(f"{name} = {getattr(self, name)!r} 的精度细于两位小数")
        if Decimal(repr(self.chore_weight)) + Decimal(repr(self.support_weight)) != 1:
            raise ValueError("杂务与支持两部分的权重之和必须恰好是 1")
        return self


class FoodLifeSpec(ContractModel):
    """物质维“饮食生活”（DECISIONS/0041）：在这个月**实际执行**的完整食品方案上评价已支持的饮食属性。

        z_m = η_fresh · F_m + η_taste · T_m        s = min(c, z/(z + τ))

    * F_m：方案每周新鲜餐数（组成之和）；T_m：主要菜系在用户确认的口味里的组成每周几餐（每个组成只算一次，多标签不叠加）；
    * 基本饮食是否覆盖是可行性（最低生活检查与方案声明），不在这里给“没饿着”加分；价格、餐馆档次、外食次数都不是输入；
    * 降级时读实际吃的最低方案；η、τ、c 全组共用，只在评分规格里，不进机会，也不进观察。"""

    rule: Literal["food_plan_fresh_and_taste_v1"]
    eta_fresh: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    eta_taste: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    cap: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    unit: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=400)


class EnjoymentRule(ContractModel):
    """一项享受功能的计量：``presence``（在用的舒适用品，同功能多件只算一份）或 ``units``（享受型服务，同功能按份数相加）。"""

    function: KeyStr
    label: str = Field(min_length=1, max_length=80)
    measure: Literal["presence", "units"]
    eta: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    unit: str = Field(min_length=1, max_length=40)


class EnjoymentSpec(ContractModel):
    """物质维“个人消费与享受”（DECISIONS/0041）：按这个月实际提供的功能或服务量计分，不按成交金额或购买次数。

        v_f = 1（presence：这个月有在用的、提供功能 f 的用品；第二件同功能不再加）    或    Σ 份数（units：实际提供的服务份数）
        z_m = Σ_f η_f · v_f        s = min(c, z/(z + τ))

    纯爱好装备没有享受功能（它只提供活动条件）；词表外的功能是“模型未支持”，单独列出，不当 0 也不当未知。"""

    rule: Literal["enjoyment_by_function_v1"]
    functions: tuple[EnjoymentRule, ...] = Field(min_length=1)
    cap: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    unit: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _unique(self) -> EnjoymentSpec:
        ids = [f.function for f in self.functions]
        if len(ids) != len(set(ids)):
            raise ValueError("享受功能的计量规则重复")
        return self

    def rule_for(self, function: str) -> EnjoymentRule | None:
        return next((f for f in self.functions if f.function == function), None)


class ParticipationSpec(ContractModel):
    """休闲维的实际活动参与（DECISIONS/0041）：读结算写出的参与记录。

        x_m = Σ_活动 参与小时 × (1 + κ · 1[主要类别 ∈ 确认的兴趣])        贡献 = η_hour · x_m（加进休闲分项的 z）

    只计参与小时（准备、往返、排队不算享受）；每次活动只匹配一次，多标签不叠乘；同样的总参与小时拆成几条记录不多得分；
    匹配不放大城市背景 b。κ 有界（0–1），是公开登记的探索假设，不是对个人效用的测量。"""

    rule: Literal["participation_hours_interest_match_v1"]
    eta_hour: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    match_bonus: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    unit: str = Field(min_length=1, max_length=80)
    definition: str = Field(min_length=1, max_length=400)


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
    #: 局部映射，各只属于一个维度：环境的景观加分（`DECISIONS/0019`）、发展维的成长项与交通维的广义交通（`DECISIONS/0024`）、
    #: 物质细需求 daily_convenience 的日常生活便利（`DECISIONS/0033`）。没有用到的不序列化。
    landscape: LandscapeSpec | None = None
    growth: GrowthSpec | None = Field(default=None, exclude_if=lambda v: v is None)
    transport: TransportSpec | None = Field(default=None, exclude_if=lambda v: v is None)
    convenience: ConvenienceSpec | None = Field(default=None, exclude_if=lambda v: v is None)
    #: 生活扩展（DECISIONS/0041）：饮食生活、个人消费与享受、休闲里的实际活动参与。没有用到的不序列化。
    food_life: FoodLifeSpec | None = Field(default=None, exclude_if=lambda v: v is None)
    enjoyment: EnjoymentSpec | None = Field(default=None, exclude_if=lambda v: v is None)
    participation: ParticipationSpec | None = Field(default=None, exclude_if=lambda v: v is None)

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
        if self.transport is not None:
            if self.dimension != "commute":
                raise ValueError(f"{self.component_id}: 广义交通只属于通勤维（交通便捷程度）")
            if self.sources:
                raise ValueError(f"{self.component_id}: 广义交通读实际住房与到岗次数，不再挂来源")
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
        for name, need in (("food_life", FOOD_LIFE), ("enjoyment", PERSONAL_ENJOYMENT)):
            if getattr(self, name) is not None:
                if self.material_need != need:
                    raise ValueError(f"{self.component_id}: {name} 只属于物质细需求 {need}")
                if self.sources:
                    raise ValueError(
                        f"{self.component_id}: {name} 读实际方案的事实，不再挂物品来源"
                    )
        if self.participation is not None and self.dimension != "leisure":
            raise ValueError(f"{self.component_id}: 活动参与只属于休闲维")
        if not math.isfinite(self.tau):
            raise ValueError(f"{self.component_id}: τ 必须是有限正数")
        return self

    @property
    def base_only(self) -> bool:
        return (
            not self.sources
            and self.transport is None
            and self.growth is None
            and self.convenience is None
            and self.food_life is None
            and self.enjoyment is None
            and self.participation is None
        )

    @property
    def plan_level(self) -> bool:
        """这一项的值来自实际执行方案的事实（城市条件写 plan_level）：广义交通、日常生活便利、饮食生活、个人消费与享受。"""
        return (
            self.transport is not None
            or self.convenience is not None
            or self.food_life is not None
            or self.enjoyment is not None
        )


class Omission(ContractModel):
    """一条省略：省略了什么、可能影响哪些偏好的人、归哪一轮。未覆盖不是天然零均值的残差。"""

    what: str = Field(min_length=1, max_length=200)
    affects: str = Field(min_length=1, max_length=200)
    owner: str = Field(min_length=1, max_length=40)


class LifeScoringSpec(ContractModel):
    """一套探索评分规格。同一规格内，所有城市用同一套分项含义、方向、单位与锚点。"""

    spec_id: IdStr
    scoring_version: Literal["e1-life-v1"]
    status: Literal["exploratory_synthetic"]
    function: Literal["saturating_capped_v1"] = SCORING_FUNCTION
    protocol_id: IdStr
    components: tuple[ScoringComponent, ...] = Field(min_length=1)
    note: str = Field(min_length=1)
    omissions: tuple[Omission, ...] = ()
    #: 健康项（γ 与 H，DECISIONS/0027）。
    health: HealthScoringSpec

    @model_validator(mode="after")
    def _complete(self) -> LifeScoringSpec:
        ids = [c.component_id for c in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("评分分项 ID 重复")
        for dim in AMENITY_DIMENSIONS:
            n = sum(1 for c in self.components if c.dimension == dim)
            if dim == HEALTHCARE:
                if n != 0:
                    raise ValueError(
                        "没有医疗分项：城市医疗背景不进满足感，健康由全组共用的健康项 γH 评价（DECISIONS/0027）"
                    )
                continue
            if n != 1:
                raise ValueError(f"维度 {dim} 必须恰好一个分项，得到 {n}")
        needs = [c.material_need for c in self.components if c.dimension == MATERIAL]
        if not needs:
            raise ValueError("物质维至少要有一个分项")
        if len(needs) != len(set(needs)):
            raise ValueError("同一物质细需求只能有一个分项")
        problems = supplement_spec_problems(self)
        if problems:
            raise ValueError("；".join(problems))
        return self

    def content(self) -> dict[str, object]:
        """决定计算的全部规格内容，规范化成一份确定的字典（评分内容身份，DECISIONS/0025）。

        做法：原样 ``model_dump(mode="json")``，再删掉明确列出的不参与字段——默认全部参与，以后新增的字段也参与
        （漏排除只会多报一次"内容不同"，方向安全）。参与：评分版本、状态、公式标识；每个分项的编号、维度、物质
        细需求、单位、τ、合并规则；每个来源的取值规则、过滤条件、单位与数量语义；景观 β、方案级通勤、成长项 η 与
        进度规则、广义交通 ρ 与参考到岗次数及各自单位、日常生活便利的任务组合、c、规则与单位、健康项 γ 与 H。**不参与**：``spec_id``（名字，由反馈政策的
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
        for key in SCORING_CONTENT_EXCLUDED_MAPPING_FIELDS:  # 健康项：γ 与 H 参与，说明文字不参与
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
        """有分项的非物质维（没有医疗维）。"""
        return tuple(d for d in AMENITY_DIMENSIONS if d != HEALTHCARE)


def scoring_content_problems(
    policy: EnergyFeedbackPolicy, spec: LifeScoringSpec
) -> list[tuple[str, str]]:
    """实际加载并使用的评分规格，内容是否就是反馈政策声明的那一套（DECISIONS/0025）。

    加载（``ledger.life_scoring_config.scoring_config_problems``）与年度交接（``econ.life_year``，结算之前）共用这一份。
    返回 (问题代码, 说明)。
    """
    declared = policy.scoring_content_fingerprint
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
    """规格的数量语义与单位检查（`DECISIONS/0019` 第十二节），以及成长项、广义交通与日常生活便利是否齐全。

    * ``count`` 只读一次体验（拥有的物品按件数计分会刷分）；
    * 分项单位、来源读取单位、广义交通、成长项与便利的单位都要是登记过的写法；
    * 来源读取单位与分项单位同量纲（之间的换算系数写进来源表）；取值方式与量纲配套：
      存在型读布尔，数量读每月次数，规格数值读数值量纲；
    * 广义交通：τ_T 与往返分钟同一个单位（S = τ_T/(τ_T + T_eq) 不做换算）。
    """
    out: list[str] = []
    for comp in spec.components:
        cu = resolve_unit(comp.unit)
        where = comp.component_id
        if cu is None:
            out.append(f"{where}: 分项单位 {comp.unit!r} 没有登记——不按名称猜单位")
        for src in comp.sources:
            w = f"{where}/{src.source_id}"
            if src.measure == "count" and set(src.records) - {"experience"}:
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
    out.extend(growth_transport_problems(spec))
    out.extend(convenience_problems(spec))
    return out


def growth_transport_problems(spec: LifeScoringSpec) -> list[str]:
    """发展维必须声明成长项、交通维必须声明广义交通（`DECISIONS/0024`）。"""
    out: list[str] = []
    if spec.dimension_component("opportunity").growth is None:
        out.append("发展维必须声明成长项 growth（η = 0 是关闭对照）")
    if spec.dimension_component("commute").transport is None:
        out.append("交通维必须声明广义交通 transport")
    return out


def convenience_problems(spec: LifeScoringSpec) -> list[str]:
    """恰好一个日常生活便利分项，并且不按家务功能另给一份"有没有"的分（`DECISIONS/0033`）。

    同一项家务已经由"实际减少了多少小时"评价，再按"有没有洗衣机"给一份分，就是同一件事计两份。
    """
    out: list[str] = []
    declared = [c for c in spec.components if c.convenience is not None]
    if len(declared) != 1:
        out.append(
            f"必须恰好有一个日常生活便利分项（物质细需求 {DAILY_CONVENIENCE}，声明 convenience），得到 {len(declared)} 个"
        )
    for c in spec.components:
        if c.material_need == DAILY_CONVENIENCE and c.convenience is None:
            out.append(
                f"{c.component_id}: 细需求 {DAILY_CONVENIENCE} 只能由 convenience 规则评价（读实际减少的家务小时）"
            )
    tasks = {t for c in declared for t in c.convenience.tasks}  # type: ignore[union-attr]
    support = {f for c in declared for f in c.convenience.support_functions}  # type: ignore[union-attr]
    for c in spec.components:
        for src in c.sources:
            hit = sorted(set(src.functions) & tasks)
            if hit:
                out.append(
                    f"{c.component_id}/{src.source_id}: 按家务功能 {hit} 取“有没有”的来源与日常生活便利重复计分"
                    "（便利只读实际减少的家务小时）"
                )
            twice = sorted(set(src.functions) & support)
            if twice:
                out.append(
                    f"{c.component_id}/{src.source_id}: 日常支持功能 {twice} 已经在日常支持与便利里计分，不能再由别的分项计一次"
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

    ``status = plan_level``：这一项的值来自实际执行方案的事实（广义交通、日常生活便利），
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
    #: 按住处所在片区取的基础量（DECISIONS/0042）：写了就**取代**城市背景 ``base``，不在城市背景上再叠一次。这个月读实际住处
    #: 所在片区的值；住处没有片区、或片区不在表里，这一项这个月未知（不沿用城市值，也不当 0）。只有城市级资料时各片区写同一个值。
    area_bases: dict[IdStr, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(
        default_factory=dict, exclude_if=lambda v: v == {}
    )
    area_basis: str | None = Field(default=None, max_length=400, exclude_if=lambda v: v is None)

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
        if self.area_bases and self.status != "known":
            raise ValueError(f"{self.component_id}: 只有已知条件才能写按片区的基础量")
        if bool(self.area_bases) != (self.area_basis is not None):
            raise ValueError(f"{self.component_id}: 按片区的基础量与它的依据说明要一起写")
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
    commute_granularity: Literal["plan_level"]
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

#: 偏好来源：用户确认的六维精确点数（DECISIONS/0034）。``dimension_weights`` 只有六维（没有 healthcare），就是确认的精确点数 / 100
#: 只转一次浮点得到的有效权重。
PreferenceSource = Literal["product_confirmed_six_dim"]
SIX_DIM_DIMENSIONS: tuple[str, ...] = tuple(d for d in ALL_DIMENSIONS if d != HEALTHCARE)


class ScoringPreference(ContractModel):
    """评分用的权重：用户确认的六维精确点数 / 100（只有六个键，健康回答独立、不进权重），物质细项精确点数 / 100；物质父项为 0
    时没有细项权重。不从序数或文字臆造。

    权重不随城市变化，也不用实际花钱比例冒充偏好。不在 ``material_weights`` 里的物质细需求权重为 0（定和量表的含义）。
    """

    person_id: IdStr
    source: PreferenceSource
    dimension_weights: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]]
    material_weights: dict[KeyStr, Annotated[float, Field(ge=0, allow_inf_nan=False)]]
    derivation: str = Field(min_length=1, max_length=400)
    #: 生活扩展（DECISIONS/0041）：用户确认后进入有限匹配规则的兴趣类别与菜系（来自确认画像，不从文字临场猜）。
    #: 空表示没有确认的类别（未答、暂不确定、明确没有固定爱好、选择通用评价）——这些状态在画像里分开保存，评分都按中性规则。
    leisure_interests: tuple[KeyStr, ...] = ()
    cuisines: tuple[KeyStr, ...] = ()

    @model_validator(mode="after")
    def _weights_ok(self) -> ScoringPreference:
        if set(self.dimension_weights) != set(SIX_DIM_DIMENSIONS):
            raise ValueError(f"六维产品画像的权重必须恰好是 {list(SIX_DIM_DIMENSIONS)}")
        if abs(math.fsum(self.dimension_weights.values()) - 1.0) > WEIGHT_SUM_TOL:
            raise ValueError("六维权重必须合计 1")
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


class SupportCoverage(ContractModel):
    """一项日常支持功能这个月有没有获得（付了钱、实际在用的服务提供它），由哪几项提供。"""

    function: KeyStr
    covered: bool
    via: tuple[IdStr, ...] = ()


class ConvenienceTerm(ContractModel):
    """日常支持与便利一个月的明细：逐项家务与合计 q（小时/月）、τ 与 c、杂务分；支持功能的覆盖与支持分；两部分权重。分数只取决于
    q 与覆盖——设备还是人工、价格多少、买了几份都不进入。"""

    tasks: tuple[TaskRelief, ...]
    relief_hours: ExactAmount  # q_m
    tau_hours: float
    cap: Annotated[float, Field(ge=0, le=1)]
    chore_s: Annotated[float, Field(ge=0, le=1)]  # S_chore
    support: tuple[SupportCoverage, ...]
    support_cap: Annotated[float, Field(ge=0, le=1)]
    support_s: Annotated[float, Field(ge=0, le=1)]  # S_support
    chore_weight: Annotated[float, Field(ge=0, le=1)]
    support_weight: Annotated[float, Field(ge=0, le=1)]


class FoodLifeTerm(ContractModel):
    """饮食生活这个月的明细：读的是哪一份实际方案、新鲜餐与合口味餐各几餐。"""

    opportunity_id: IdStr
    fresh_meals_per_week: Annotated[float, Field(ge=0)]
    taste_matched_meals_per_week: Annotated[float, Field(ge=0)]
    matched_cuisines: tuple[KeyStr, ...] = ()
    eta_fresh: float
    eta_taste: float


class EnjoymentFunctionTerm(ContractModel):
    """个人消费与享受里一项功能这个月的取值：v_f 与它来自哪些在用的东西。"""

    function: KeyStr
    measure: Literal["presence", "units"]
    value: Annotated[float, Field(ge=0)]
    eta: float
    contribution: Annotated[float, Field(ge=0)]
    via: tuple[IdStr, ...]


class EnjoymentTerm(ContractModel):
    functions: tuple[EnjoymentFunctionTerm, ...] = ()
    #: 在用、但功能不在计量规则里的（模型未支持；不当 0，也不当未知）。
    unsupported: tuple[str, ...] = ()


class ParticipationEntry(ContractModel):
    opportunity_id: IdStr
    primary_category: KeyStr
    participation_hours: Annotated[float, Field(ge=0)]
    matched: bool
    weighted_hours: Annotated[float, Field(ge=0)]


class ParticipationTerm(ContractModel):
    """休闲里实际活动参与这个月的明细：每项活动的参与小时、是否匹配确认的兴趣、加权后的小时与总贡献。"""

    entries: tuple[ParticipationEntry, ...] = ()
    weighted_hours: Annotated[float, Field(ge=0)]
    eta_hour: float
    match_bonus: float
    contribution: Annotated[float, Field(ge=0)]


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
    #: 没有用到的映射明细不序列化。
    growth: GrowthTerm | None = Field(default=None, exclude_if=lambda v: v is None)
    transport: TransportTerm | None = Field(default=None, exclude_if=lambda v: v is None)
    convenience: ConvenienceTerm | None = Field(default=None, exclude_if=lambda v: v is None)
    food_life: FoodLifeTerm | None = Field(default=None, exclude_if=lambda v: v is None)
    enjoyment: EnjoymentTerm | None = Field(default=None, exclude_if=lambda v: v is None)
    participation: ParticipationTerm | None = Field(default=None, exclude_if=lambda v: v is None)


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
    """这一年的权重换算（`DECISIONS/0027`、0034）：六维产品画像的原始权重（确认点数 / 100）、有效六维权重、γ 与 H。

    六维产品画像没有 healthcare 点数；``independent_health_input`` 写独立健康回答的含义（不占 100 分、不进权重）。"""

    raw_dimension_weights: dict[str, float]
    six_dimension_sum: float
    effective_weights: dict[str, float]
    gamma: float
    state_values: HealthStateValues
    formula: str = Field(min_length=1, max_length=280)
    independent_health_input: Literal["independent_health_answers_v1"]

    @model_validator(mode="after")
    def _six_dimensions(self) -> HealthWeighting:
        if HEALTHCARE in self.raw_dimension_weights:
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
    #: 持有物数量从哪来：结算写出（recorded），或这一年没有持有物（no_holdings）。
    holding_quantity_source: Literal["recorded", "no_holdings"]
    #: 在用物品上记录了、本轮不评价的方案属性（品牌、档次、菜系……）。不从价格推断偏好。
    unevaluated_attributes: tuple[str, ...] = ()
    #: 停止月与停止之后的月份（DECISIONS/0027）。没有用到时不序列化。
    months_stopped: tuple[Month, ...] = Field(default=(), exclude_if=lambda v: v == ())
    months_after_stop: tuple[Month, ...] = Field(default=(), exclude_if=lambda v: v == ())
    #: 权重换算与健康项。
    health_weighting: HealthWeighting

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
#: 覆盖报告版本：分开报规则、城市资料、实际行动三层与生活事实、评分参数两类来源（`DECISIONS/0019` 第十二节）。
CoverageReportVersion = Literal["coverage-v2"]
#: 规则覆盖：评分规格有没有定义这项需求怎样评分。
RuleCoverage = Literal["defined", "no_component"]
#: 城市资料覆盖：比较组每座城是否提供了这项规则读到的事实（规格键、功能供给、景观、通勤分钟）。
CityDataCoverage = Literal["complete", "gap", "not_applicable"]
#: 实际行动可评分性：只要城市资料齐全，任何实际安排都能评；还是要看这个人的实际安排。
ActionScope = Literal["all_arrangements", "conditional", "not_applicable"]


class LeafCoverage(ContractModel):
    """一个偏好叶子的覆盖。

    ``support = supported`` 意味着规则已定义**且**每座城的资料齐全。
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
    rule: RuleCoverage
    city_data: CityDataCoverage
    action_scope: ActionScope
    fact_evidence: EvidenceClass
    parameter_evidence: EvidenceClass | None = None
    data_gaps: tuple[str, ...] = ()


class CoverageReport(ContractModel):
    """一个人在一个比较组上的偏好覆盖。**不设阈值，不解锁总分**；正权重未知时总分照旧为空。"""

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
    report_version: CoverageReportVersion = "coverage-v2"
    fact_weight_by_evidence: dict[str, float] = Field(default_factory=dict)
    parameter_weight_by_evidence: dict[str, float] = Field(default_factory=dict)


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
        "eta_fresh",
        "eta_taste",
        "eta_hour",
        "match_bonus",
        "efficiencies",
        "a_nature",
        "base_includes_landscape",
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
        # 生活扩展（`DECISIONS/0041`）：口味与兴趣的匹配结果、加权小时（实际参与小时与菜系本身是公开事实，不收）
        "weighted_hours",
        "taste_matched_meals_per_week",
        "matched_cuisines",
    }
)


def internal_scoring_models() -> tuple[type[ContractModel], ...]:
    """评分侧内部对象的类型。它们本身、以及它们原样序列化后的字典，都不能进观察。"""
    return (
        LifeScoringSpec,
        ScoringComponent,
        ComponentSource,
        LandscapeSpec,
        GrowthSpec,
        TransportSpec,
        ConvenienceSpec,
        # 生活扩展（DECISIONS/0041）：饮食、享受与活动参与的评分规格
        FoodLifeSpec,
        EnjoymentRule,
        EnjoymentSpec,
        ParticipationSpec,
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
        AbilityProgressStep,
        GrowthTerm,
        TransportTerm,
        TaskRelief,
        ConvenienceTerm,
        # 生活扩展（DECISIONS/0041）：各月明细（匹配结果、加权小时、系数）
        FoodLifeTerm,
        EnjoymentFunctionTerm,
        EnjoymentTerm,
        ParticipationEntry,
        ParticipationTerm,
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
