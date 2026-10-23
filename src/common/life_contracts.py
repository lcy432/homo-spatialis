"""生活模型的共享数据契约（E1-1 `DECISIONS/0016`；E1-2 `DECISIONS/0017`）。

**这里只有数据形状与结构校验，没有任何结算、评分或经济公式。** 月度执行与记账在
``econ.life_settlement``；满足感、精力反馈、职业增长、风险、折现与期末价值仍未定
（见 ``REQUIRED_MATH_POLICIES``），写在这里就等于绕过经济模型的审查。

**模拟长度是协议字段**（``LifeProtocol.simulation_years``，默认 3）。正常行动发生在
第 1–N 年，终态编号 ``x = N + 1`` 由 N 推导，不单独配置。字段上的年份上限只是防呆
（``MAX_SIMULATION_YEARS``）；"不超过 N" 这类依赖协议的检查在加载器与结算器里做。

--------------------------------------------------------------------------
与旧静态协议的关系
--------------------------------------------------------------------------

旧协议（`econ.types` 的 ``Persona`` / ``GroundTruth`` / ``WelfareLevel``，以及 ln V 与
美元福利）原样保留，**本模块不复用它们的语义**：新生活协议有自己的身份
（``PROTOCOL_FAMILY``），不借用 ln V、美元福利或 P8 的精度结论。

复用的只有身份与计价的基础类型——``CityId``、``OccupationId``、``Jurisdiction``、
``Money``、``Provenance``。

--------------------------------------------------------------------------
两条贯穿全文件的区分
--------------------------------------------------------------------------

**偏好 vs 安排。** 偏好是人的稳定属性（六维产品画像 ``common.life_product_profile.ProductLifeProfile``）；
实际消费安排是行动（``YearAction``），可以因城而异。旧协议的硬规则 9（支出份额是
人的字段、不随城市变）管的是旧 Cobb-Douglas 份额，与这里不冲突：这里**不用**支出
份额代替偏好，也不要求动态消费比例跨城固定。

**实际报价 vs 价格指数。** 报价（``PriceQuote``）是可以付的钱；指数（``PriceIndex``）
是无量纲的相对水平，没有金额，不能当付款单价。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import itertools
import json
import math
import re
from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from common.ids import CityId, Jurisdiction, OccupationId
from common.money import USD_2024_US100, Money
from common.provenance import Provenance

#: 新生活协议的家族名。与旧静态协议（ln V / 美元福利 / P8）**不是同一个协议**。
PROTOCOL_FAMILY = "life_simulation"

#: 尚待确定的数学政策。全部定下之前，协议**不能**标成正式可评分。
REQUIRED_MATH_POLICIES: tuple[str, ...] = (
    "score_mapping",  # 年度体验 → 分数的映射与尺度
    "material_preference_aggregation",  # 物质维内部细偏好如何聚合
    "energy_feedback",  # 满足感 → 下一年精力的公式、上限与滞后
    "income_payment_timing",  # 年度收入与付款的时序
    "risk_scenarios",  # 风险场景与随机过程
    "discounting",  # 三年之间是否折现、怎么折
    "terminal_value",  # 期末资产与持续物品如何计价
    "failure_handling",  # 行动执行失败、资金不足、代理违规怎么处理
)

#: 默认模拟长度。**不是字段上限**：真正的 N 在 ``LifeProtocol.simulation_years``。
DEFAULT_SIMULATION_YEARS: int = 3
#: 年份字段的防呆上限。超过它多半是填错，不是长期模拟。
MAX_SIMULATION_YEARS: int = 50
MONTHS_PER_YEAR: int = 12
#: 周价换月价的约定：每年 52 周、每月 52/12 周，全年一致（DECISIONS/0017）。
WEEKS_PER_YEAR: int = 52
#: 一年的小时数上限（闰年）。时间是固定年度资源。
HOURS_PER_YEAR_MAX: float = 8784.0
#: 持续物品的期限上限。超过它多半是单位写错（月当成年）。
MAX_DURABLE_YEARS: int = 30
#: 有限经历摘要：条数与每条长度都有上限，避免成本随年数无限增长。
MAX_SUMMARY_ENTRIES: int = 12
MAX_SUMMARY_CHARS: int = 280
MAX_GOALS: int = 5
MAX_RATIONALE_CHARS: int = 1000

_ID = r"^[a-z0-9][a-z0-9_.\-]{0,63}$"
_KEY = r"^[a-z][a-z0-9_]{0,47}$"
IdStr = Annotated[str, Field(pattern=_ID)]
KeyStr = Annotated[str, Field(pattern=_KEY)]
#: 结构化属性只许原始值。嵌套对象进不来，这本身就挡掉了一类藏匿通道。
Primitive = str | int | float | bool

ProtocolStatus = Literal["synthetic_fixture_only", "provisional", "formal"]
YearIndex = Annotated[int, Field(ge=1, le=MAX_SIMULATION_YEARS)]
Month = Annotated[int, Field(ge=1, le=MONTHS_PER_YEAR)]
#: 最低生活需求只有这两项（DECISIONS/0017）。分配归因顺序：先住房后食品。
MinimumNeed = Literal["housing", "food"]
MINIMUM_NEEDS: tuple[str, ...] = ("housing", "food")
Availability = Literal["available", "unavailable", "unknown"]
BillingPeriod = Literal["one_time", "per_week", "per_month", "per_year"]

#: 生活规则版本。只有这一版：月度时间与精力（DECISIONS/0020）、满足感 → 下一年精力额度（0021）、学习与岗位（0023）、
#: 目标储蓄（0026）、疾病与应急（0027）。协议必须同时带这些规则。
LIFE_RULES_VERSION = "e1-5c-monthly-health-v1"
RESOURCE_RULES_VERSION = "e1-4a-monthly-resources-v1"
#: 资源规则的执行修订（DECISIONS/0020 第十二节），写进资源账与诊断：出局当月检查已知劳动的资源下限、多份到岗工作按
#: "每个岗位每次到岗一趟完整往返"相加。改资源执行语义要换修订号。
RESOURCE_RULES_REVISION = "r2-2026-10-08"
#: ``EnergyState.level`` 的含义：每个月可持续承受的活动负担额度（负担点）。
ENERGY_QUOTA_SCALE = "e1-4a-monthly-quota-v1"
#: 资源输入（小时、每小时负担、额度、净可分配小时）最多两位小数；分钟与天数是整数。
RESOURCE_INPUT_DECIMALS = 2
ENERGY_FEEDBACK_POLICY_VERSION = "e1-4b-satisfaction-quota-v1"
#: 年度交接的执行修订（DECISIONS/0021 第十三节、0025）：额度绑定反馈政策的内容与确定它的评分内容。
FEEDBACK_HANDOFF_REVISION = "r3-2026-10-09"
#: 政策内容指纹的规范化格式（哪些字段参与见 ``EnergyFeedbackPolicy.content``）。
FEEDBACK_POLICY_CONTENT_SCHEMA = "e1-4b-policy-content-v2"
#: 职业规则（DECISIONS/0023）的版本与内容指纹格式（哪些字段参与见 ``CareerRules.content``）。
CAREER_RULES_VERSION = "e1-5a-career-v1"
CAREER_RULES_CONTENT_SCHEMA = "e1-5a-career-content-v1"
#: 目标储蓄规则（DECISIONS/0026）的版本与内容指纹格式（哪些字段参与见 ``SavingsRules.content``）。
SAVINGS_RULES_VERSION = "e1-5b-savings-goals-v1"
SAVINGS_RULES_CONTENT_SCHEMA = "e1-5b-savings-content-v1"
#: 本探索版允许的最大加成比例 α 上限（0 ≤ α ≤ 0.10）。合成设定，不是经验结论。
FEEDBACK_ALPHA_MAX = 0.10
#: 偏好来源：用户确认的六维精确点数（``common.life_product_profile.ProductLifeProfile``，DECISIONS/0034），健康回答独立、
#: 不进权重。与 ``common.life_scoring_contracts.PreferenceSource`` 同一组取值；协议契约不能反过来 import 评分契约，所以另写一份。
FeedbackPreferenceSource = Literal["product_confirmed_six_dim"]
PRODUCT_SIX_DIM_SOURCE = "product_confirmed_six_dim"


class LifeContractError(ValueError):
    """契约层面的结构错误。消息里写清楚是哪个字段、为什么。"""


class ContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def _finite(x: float, what: str) -> float:
    if not math.isfinite(x):
        raise ValueError(f"{what} 必须是有限数，得到 {x!r}")
    return x


# ==========================================================================
# 协议身份
# ==========================================================================


class PolicyDecision(ContractModel):
    """一条数学政策的状态。``decided`` 必须指向裁决记录。"""

    state: Literal["undecided", "decided"]
    decision_ref: str | None = None
    proposal: str | None = None  # 建议方案的简述；**不是**已采纳的默认值
    #: 一条政策可以部分已定：已定的部分与仍开放的部分分开写，整条仍算 undecided。
    settled_parts: tuple[str, ...] = ()
    open_parts: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _ref_when_decided(self) -> PolicyDecision:
        if self.state == "decided" and not self.decision_ref:
            raise ValueError("decided 的政策必须给出 decision_ref（裁决记录），不能空口说已定")
        if self.state == "decided" and self.open_parts:
            raise ValueError(f"还有开放部分 {list(self.open_parts)} 的政策不能标成 decided")
        return self


class LifeProtocol(ContractModel):
    """生活协议的版本与状态。本轮不建通用版本注册系统，只记录身份。"""

    protocol_id: IdStr
    family: Literal["life_simulation"] = PROTOCOL_FAMILY
    schema_version: str
    life_rules_version: Literal["e1-5c-monthly-health-v1"]
    scoring_scale_version: str
    info_permission_version: str
    status: ProtocolStatus
    math_policies: dict[str, PolicyDecision]
    #: N。正常行动发生在第 1–N 年；终态编号由它推导。
    simulation_years: Annotated[int, Field(ge=1, le=MAX_SIMULATION_YEARS)] = (
        DEFAULT_SIMULATION_YEARS
    )
    #: 月度时间与精力规则（DECISIONS/0020）。
    resource_rules: MonthlyResourceRules
    #: 满足感 → 下一年月度精力额度的反馈政策（DECISIONS/0021；开、关与强度都要显式写）。
    energy_feedback: EnergyFeedbackPolicy
    #: 学习成果、生效时点、岗位取得规则与能力词表（DECISIONS/0023）。
    career_rules: CareerRules
    #: 目标储蓄规则（DECISIONS/0026）。
    savings_rules: SavingsRules
    #: 疾病与恢复的世界规则、疾病事件来源（实验设置）与健康输入的含义声明（DECISIONS/0027、0034）。健康回答是独立问卷，
    #: 不在六维定和里。
    health_rules: HealthRules
    illness_events: IllnessEventSource
    health_input: IndependentHealthInputDeclaration

    @property
    def terminal_year_index(self) -> int:
        """终态编号 ``x = N + 1``。**由 N 推导，不单独配置**——两者独立就可能矛盾。"""
        return self.simulation_years + 1

    @field_validator("math_policies")
    @classmethod
    def _all_policies_named(cls, v: dict[str, PolicyDecision]) -> dict[str, PolicyDecision]:
        missing = set(REQUIRED_MATH_POLICIES) - set(v)
        unknown = set(v) - set(REQUIRED_MATH_POLICIES)
        if missing or unknown:
            raise ValueError(
                f"math_policies 必须恰好覆盖 {list(REQUIRED_MATH_POLICIES)}；"
                f"缺 {sorted(missing)}，多 {sorted(unknown)}"
            )
        return v

    @property
    def undecided_policies(self) -> tuple[str, ...]:
        return tuple(k for k in REQUIRED_MATH_POLICIES if self.math_policies[k].state != "decided")

    @property
    def scoring_ready(self) -> bool:
        """只有 ``formal`` 且八条政策全部已定，才算可评分。"""
        return self.status == "formal" and not self.undecided_policies

    @model_validator(mode="after")
    def _formal_needs_policies(self) -> LifeProtocol:
        if self.status == "formal" and self.undecided_policies:
            raise ValueError(
                f"关键数学政策未定，协议不能标成 formal：{list(self.undecided_policies)}"
            )
        return self


# ==========================================================================
# 计价
# ==========================================================================

_LIFE_NUMERAIRE = re.compile(r"^([A-Z]{3})_(\d{4})_LOCAL$")


class MoneyBasis(ContractModel):
    """一套本地金额的口径：币种 + 基年 + 计价含义。

    含义是**当地价格水平、以基年货币计价**：

    * 空间上**不平减**——装的是这座城市实际标的价，不是"换算到全美均价"之后的值；
    * 时间上是**基年不变价**——跨年变化用实际增长率表达（CLAUDE 硬规则 12），所以
      标签里不用 "nominal"，免得被读成随通胀浮动的名义价。

    **不默认用 ``USD_2024_US100``。** 那是按 RPP 空间平减过的口径，装的是"全美均价
    为 100 时值多少"，不是当地标价。把原始当地报价塞进去，账面就混进了一层不存在的
    平减。本轮也**不**做自动汇率或 PPP 转换。
    """

    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]  # ISO 4217；XTS 为测试保留码
    base_year: Annotated[int, Field(ge=1900, le=2100)]
    price_meaning: Literal["local_price_base_year"] = "local_price_base_year"
    #: 记账的最小单位位数（美元 2 = 分）。结算全程用整数最小单位，精度与舍入由它决定。
    minor_unit_digits: Annotated[int, Field(ge=0, le=4)] = 2

    @property
    def numeraire_id(self) -> str:
        return f"{self.currency}_{self.base_year}_LOCAL"


def exact_minor_units(amount: float, digits: int) -> int | None:
    """把金额换成整数最小单位（美元 2 位 = 分）。**不舍入**：精度细于最小单位返回 None。

    用十进制读浮点数的最短表示（``repr``），所以 ``900.0`` 是 90000 分，``0.07`` 是 7 分，
    ``900.005`` 不是任何整数分——它是数据错误，不是"差不多"。
    """
    d = Decimal(repr(float(amount))).scaleb(digits)
    if d != d.to_integral_value():
        return None
    return int(d)


def require_life_numeraire(numeraire: str, what: str) -> str:
    """金额必须是生活协议的本地标价口径。空间平减口径在这里被明确拒绝。"""
    if numeraire == USD_2024_US100:
        raise ValueError(f"{what} 用了 {USD_2024_US100}：那是空间平减口径，不能装当地实际标价")
    if not _LIFE_NUMERAIRE.match(numeraire):
        raise ValueError(
            f"{what} 的口径 {numeraire!r} 不是生活协议的本地标价口径 <币种>_<基年>_LOCAL"
        )
    return numeraire


class PriceQuote(ContractModel):
    """实际报价：可以付出去的钱，带计费周期与计价单位。

    零价格允许——确实免费的项目（公共公园年卡）就是零。**未知价格不是零**，它在
    ``LifeOpportunity`` 里表现为 ``price=None`` 加 ``availability="unknown"``。
    """

    kind: Literal["actual_quote"] = "actual_quote"
    amount: Money
    billing: BillingPeriod
    per_unit: str = Field(min_length=1, max_length=64)

    @field_validator("amount")
    @classmethod
    def _amount_ok(cls, v: Money) -> Money:
        _finite(v.amount, "报价金额")
        if v.amount < 0:
            raise ValueError("报价金额不能为负；退款或补贴另立条目")
        require_life_numeraire(v.numeraire, "报价")
        return v


class PriceIndex(ContractModel):
    """价格指数：无量纲的相对价格水平。**没有金额，不能当付款单价。**

    有 goods 总指数不等于物质维的全部消费需求都有数据支持——支持程度另由
    ``MaterialNeedCoverage`` 逐项声明。
    """

    kind: Literal["price_index"] = "price_index"
    index_id: IdStr
    scope: str = Field(min_length=1)  # 例如 "goods"、"rents"
    value: Annotated[float, Field(gt=0)]
    reference_label: str = Field(min_length=1)  # 指数等于 1（或 100）时指的是什么


#: 来源类型（E1-3 增补，`DECISIONS/0019` 第四节）：直接观测、可审计派生、统计估计、合成设定。
SourceKind = Literal["observed", "derived", "estimated", "synthetic"]


class SourceRecord(ContractModel):
    """一条数据从哪来。

    * ``observed``：直接观测，必须带溯源；不写换算规则。
    * ``derived``：可审计派生（确定性换算）——必须列出输入 ``inputs`` 与规则 ``rule``，能据此重算。
    * ``estimated``：统计估计——必须列出输入与方法（``rule``）。
    * ``synthetic``：合成设定，必须写 ``synthetic_note``，不能带观测溯源。

    有网址不等于实测：派生与估计不能标成 ``observed``。
    """

    kind: SourceKind
    observed_on: _dt.date
    geography_scope: str = Field(min_length=1)
    mapping_version: str = Field(min_length=1)
    provenance: Provenance | None = None
    synthetic_note: str | None = None
    inputs: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = ()
    rule: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def _kind_consistent(self) -> SourceRecord:
        if self.kind == "synthetic":
            if self.provenance is not None:
                raise ValueError("合成数据不应带观测溯源；那会让合成值看起来像真实值")
            if not self.synthetic_note:
                raise ValueError("合成数据必须写 synthetic_note 说明它是怎么来的")
        elif self.kind == "observed":
            if self.provenance is None:
                raise ValueError("观测数据必须带 provenance")
            if self.rule is not None or self.inputs:
                raise ValueError("直接观测不经过换算；有输入和规则的是 derived 或 estimated")
        else:  # derived / estimated
            if not self.inputs or not self.rule:
                raise ValueError(
                    f"{self.kind} 必须列出输入 inputs 与规则 rule，才能追到它是怎么算出来的"
                )
            if self.synthetic_note:
                raise ValueError(f"{self.kind} 不是合成设定；合成值请标 synthetic")
        return self


# ==========================================================================
# 月度资源：规则、资源事实与精确数值（E1-4A，DECISIONS/0020）
#
# 这里只有形状与局部约束；按实际执行算小时与负担的规则在 ``econ.life_resources``，
# 目录与状态的跨对象检查在 ``ledger.life_catalog``。
# ==========================================================================

_EXACT = r"^-?\d+(/\d+)?$"


def to_fraction(x: float | int) -> Fraction:
    """把一个声明值精确地变成分数（按它的十进制写法，不经过二进制误差）。"""
    return Fraction(Decimal(repr(x)))


def has_decimals(x: float | int, digits: int = RESOURCE_INPUT_DECIMALS) -> bool:
    """声明值是否最多 ``digits`` 位小数。"""
    return (to_fraction(x) * 10**digits).denominator == 1


def model_fingerprint(model: BaseModel) -> str:
    """一个契约对象原样序列化（``model_dump_json``）后的 SHA-256。同一内容永远得到同一指纹，可以重放。

    它只说明"这一份对象的内容是什么"，**不能**单独证明两份对象来自同一次生活——那要靠在真正结算并评分
    的那一步建立的年度绑定（``common.life_feedback_contracts.ScoredYear``）。
    """
    return hashlib.sha256(model.model_dump_json().encode("utf-8")).hexdigest()


def canonical_fingerprint(content: object) -> str:
    """一份规范化内容（字典、列表与原始值）按键排序、紧凑分隔的 JSON 的 SHA-256。"""
    text = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ExactAmount(ContractModel):
    """一个精确的有理数（小时或负担点）。

    ``exact`` 是分数写法（如 ``70/3``）——比较、合计都用它，不加容差；``value`` 是浮点近似，只供阅读。
    """

    exact: Annotated[str, Field(pattern=_EXACT)]
    value: float

    @classmethod
    def of(cls, q: Fraction) -> ExactAmount:
        q = Fraction(q)
        text = str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"
        return cls(exact=text, value=float(q))

    def fraction(self) -> Fraction:
        return Fraction(self.exact)

    @model_validator(mode="after")
    def _value_matches(self) -> ExactAmount:
        if self.value != float(Fraction(self.exact)):
            raise ValueError(f"value {self.value!r} 与 exact {self.exact} 不一致")
        return self


class HouseholdTask(ContractModel):
    """一项具名家务（E1-4A）：没有任何替代时，本人每月要花多少小时，每小时多少负担。

    基准是声明的维持生活所需的家务量；本轮不支持靠降低整洁程度主动减少家务。设备或外包服务只用
    "替代后残余用时"表达（``TaskSubstitution``），不叠加负时间。
    """

    task: KeyStr
    baseline_hours_per_month: Annotated[float, Field(ge=0, le=744)]
    burden_per_hour: Annotated[float, Field(ge=0)]
    description: str = Field(min_length=1, max_length=280)


class MonthlyResourceRules(ContractModel):
    """一份协议的月度时间与精力规则（E1-4A，DECISIONS/0020）。全部系数都是声明的合成设定。

    * 时间：每月预算 = 年度净可分配小时 / 12（12 个标准月，月与月之间不借用）。``reserved_time``
      写明净可分配小时已经预留了什么——那些不再进活动账；
    * 精力：每月实际活动负担 ≤ 每月可承受额度；单位 ``energy_unit``，尺度 ``energy_scale_version``；
    * 通勤（DECISIONS/0042）：往返分钟读这个月的通勤执行记录（目录的通勤关系表：住处片区 × 就业片区 × 方式）；
      当月通勤小时 = 往返分钟 × 到岗次数 / 60，负担 = 小时 × ``commute_burden_per_hour``；
    * 家务：``household_tasks`` 的基准小时与每小时负担；设备或服务只给替代后的残余用时。
    """

    version: Literal["e1-4a-monthly-resources-v1"]
    time_unit: Literal["hour"]
    month_rule: Literal["annual_net_hours_div_12"]
    reserved_time: str = Field(min_length=1, max_length=400)
    energy_unit: KeyStr
    energy_scale_version: Literal["e1-4a-monthly-quota-v1"]
    commute_burden_per_hour: Annotated[float, Field(ge=0)]
    household_tasks: tuple[HouseholdTask, ...] = ()
    input_decimals: Literal[2] = RESOURCE_INPUT_DECIMALS
    source: SourceRecord

    @model_validator(mode="after")
    def _consistent(self) -> MonthlyResourceRules:
        tasks = [t.task for t in self.household_tasks]
        if len(tasks) != len(set(tasks)):
            raise ValueError("household_tasks 的任务重复")
        values = [("commute_burden_per_hour", self.commute_burden_per_hour)]
        for t in self.household_tasks:
            values += [
                (f"{t.task}.baseline_hours_per_month", t.baseline_hours_per_month),
                (f"{t.task}.burden_per_hour", t.burden_per_hour),
            ]
        for name, x in values:
            if not has_decimals(x, self.input_decimals):
                raise ValueError(f"{name} = {x!r} 的精度细于声明的 {self.input_decimals} 位小数")
        return self

    def task(self, name: str) -> HouseholdTask | None:
        return next((t for t in self.household_tasks if t.task == name), None)


class EnergyFeedbackPolicy(ContractModel):
    """满足感 → 下一年月度精力额度的有界反馈政策（E1-4B，DECISIONS/0021）。全部参数是合成设定。

        E_raw(y+1) = E_base × (1 + α × S_y)，再向下保留到 0.01 负担点

    * ``E_base``：个人固定的基础月度额度（``EnergyQuotaBasis.base_level``），不含满足感加成；
    * ``S_y``：上一年**完整**的年度平均满足感（E1-3 探索评分的 ``annual_mean``，12 个月都已评分）；
    * ``α``：最大加成比例，0 ≤ α ≤ ``FEEDBACK_ALPHA_MAX``。

    只用上一年、只从固定基础算（不复利）、不低于基础、下一年 12 个月同一个额度；时间预算不变。
    ``mode = disabled`` 或 α = 0 时额度就是基础值，不需要评分。反馈读哪套评分规格、哪条偏好来源都要
    写明：产品路径只用用户明确给出的数值，研究路径用合成真值，二者不混。
    """

    policy_id: IdStr
    version: Literal["e1-4b-satisfaction-quota-v1"]
    mode: Literal["enabled", "disabled"]
    alpha: Annotated[float, Field(ge=0, le=FEEDBACK_ALPHA_MAX)]
    formula: Literal["base_times_one_plus_alpha_s"]
    lag_years: Literal[1]
    score_statistic: Literal["complete_year_mean_u"]
    scoring_spec_id: IdStr
    scoring_version: str = Field(min_length=1)
    preference_source: FeedbackPreferenceSource
    quantization: Literal["floor_to_0.01"]
    source: SourceRecord
    note: str = Field(min_length=1, max_length=400)
    #: 反馈读取的评分规格的**内容**身份（``LifeScoringSpec.content_fingerprint``，DECISIONS/0025）：同编号同版本、参数不同的
    #: 规格是另一份政策。
    scoring_content_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def _alpha_precision(self) -> EnergyFeedbackPolicy:
        if not has_decimals(self.alpha, RESOURCE_INPUT_DECIMALS):
            raise ValueError(f"α = {self.alpha!r} 的精度细于两位小数")
        return self

    @property
    def effective(self) -> bool:
        """反馈是否真的起作用：开着并且 α > 0。关掉或 α = 0 时额度固定为基础值。"""
        return self.mode == "enabled" and self.alpha > 0

    def content(self) -> dict[str, object]:
        """决定反馈行为的全部字段，规范化成一份确定的字典（DECISIONS/0021 第十三节、0025）。

        参与：反馈版本、开关、α（按百分之一的整数写，避免浮点写法差异）、公式、滞后、评分统计量、量化规则、评分规格与版本、
        偏好来源、评分内容身份。**不参与**：``policy_id``（名字，另行核对）、``source``（溯源）、``note``（说明文字）——只改
        说明不会改变数学政策。声明的 α 即使在关闭时也参与：声明变了就是另一份政策。
        """
        hundredths = to_fraction(self.alpha) * 100
        assert hundredths.denominator == 1  # 两位小数由 _alpha_precision 保证
        return {
            "content_schema": FEEDBACK_POLICY_CONTENT_SCHEMA,
            "version": self.version,
            "mode": self.mode,
            "alpha_hundredths": int(hundredths),
            "formula": self.formula,
            "lag_years": self.lag_years,
            "score_statistic": self.score_statistic,
            "quantization": self.quantization,
            "scoring_spec_id": self.scoring_spec_id,
            "scoring_version": self.scoring_version,
            "preference_source": self.preference_source,
            "scoring_content_fingerprint": self.scoring_content_fingerprint,
        }

    @property
    def content_fingerprint(self) -> str:
        """``content()`` 按键排序、紧凑分隔的 JSON 的 SHA-256：不依赖字典插入顺序、路径或运行时间。"""
        text = json.dumps(self.content(), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ==========================================================================
# E1-5A：学习、能力与岗位（DECISIONS/0023）
#
# 这里只有形状与局部约束。能力是**少量命名的"已获得 / 未获得"事实**，不是经验值：
# 学习活动（培训机会）完成后授予声明的能力；岗位用 ``ability:<能力>`` 写要求；能力的作用域说明它能在
# 哪里被要求（通用、某个职业、某家雇主）。跨对象的引用与作用域冲突在 ``ledger.life_catalog`` 检查；
# 实际参加、授予与岗位记录在 ``econ.life_career`` 与结算里。
# ==========================================================================

AbilityKind = Literal["credential", "skill", "knowledge"]
ABILITY_KIND_LABEL: dict[str, str] = {
    "credential": "资格证书",
    "skill": "技能",
    "knowledge": "知识",
}


class AbilityScope(ContractModel):
    """一项能力能在哪里被要求。

    =========  ==========================================================================
    general    任何岗位或培训都可以要求它（例如会计资格：互联网公司里的会计岗位也可以要求）
    occupation 只有这个职业（``occupation``）的岗位可以要求它
    employer   只有这家雇主（``employer_id``）的岗位或培训可以要求它（例如某公司的内部人事规则）
    =========  ==========================================================================

    作用域只限制"谁可以要求它"，不让它自动满足别的要求：熟悉 A 公司规则，不等于熟悉 B 公司规则。
    """

    kind: Literal["general", "occupation", "employer"]
    occupation: OccupationId | None = None
    employer_id: IdStr | None = None

    @model_validator(mode="after")
    def _shape(self) -> AbilityScope:
        if (self.kind == "occupation") != (self.occupation is not None):
            raise ValueError("职业作用域必须写 occupation，其他作用域不能写")
        if (self.kind == "employer") != (self.employer_id is not None):
            raise ValueError("雇主作用域必须写 employer_id，其他作用域不能写")
        return self

    def describe(self) -> str:
        if self.kind == "general":
            return "通用：任何岗位都可以要求"
        if self.kind == "occupation":
            assert self.occupation is not None
            return f"只用于职业 {self.occupation.scheme}:{self.occupation.code} 的岗位"
        return f"只用于雇主 {self.employer_id} 的岗位与培训"


class AbilitySpec(ContractModel):
    """词表里的一项命名能力：资格证书、技能或有作用域的知识。只有"已获得 / 未获得"，没有等级。"""

    ability_id: KeyStr
    kind: AbilityKind
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=280)
    scope: AbilityScope
    source: SourceRecord


class EmployerSpec(ContractModel):
    """一家合成雇主。只用于岗位归属与"公司特定知识"的作用域；不是公司模型。"""

    employer_id: IdStr
    label: str = Field(min_length=1, max_length=80)
    city: CityId
    source: SourceRecord


class CareerRules(ContractModel):
    """E1-5A 的职业规则：学习成果、生效时点、岗位取得规则与能力词表。全部是合成试点规则。

    * ``attendance``：一个月算有效参加，当且仅当它在年度计划的参加月里、这门课的费用已经结清（零价课程也算
      结清）、并且当月实际执行（计入月度时间与精力账）。只付钱、只写理由，都不增加进度。
    * ``completion``：累计有效参加月数达到课程要求的月数（``completion_years × 12``）即完成。
    * ``grant``：完成时授予课程声明的能力；按能力 ID 去重——已经有的不再授予第二次，保留最早一次的来源。
    * ``effective_from``：当年完成的能力最早从**下一行动年**起满足要求；不追溯改变当年的工资或选择。
    * ``qualification_basis``：资格一律按**年初状态**判断（年初已有的能力、年初所在的岗位）。
    * ``job_access``：岗位明确可得且年初满足要求，就按声明的安排执行（确定性合成规则，不是现实录用结论；
      没有录用概率、竞争或名额）。

    ``content_fingerprint`` 覆盖规则与能力、雇主的身份和作用域；名称、说明、溯源与备注不参与。
    """

    version: Literal["e1-5a-career-v1"]
    attendance: Literal["planned_month_fee_settled"]
    completion: Literal["participated_months_reach_required"]
    grant: Literal["union_by_ability_id"]
    effective_from: Literal["next_action_year"]
    qualification_basis: Literal["year_start_state"]
    job_access: Literal["deterministic_if_available_and_qualified"]
    abilities: tuple[AbilitySpec, ...] = Field(min_length=1)
    employers: tuple[EmployerSpec, ...] = ()
    source: SourceRecord
    note: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _vocabulary(self) -> CareerRules:
        ids = [a.ability_id for a in self.abilities]
        dup = sorted({x for x in ids if ids.count(x) > 1})
        if dup:
            raise ValueError(f"能力 ID 重复 {dup}")
        emp = [e.employer_id for e in self.employers]
        dup = sorted({x for x in emp if emp.count(x) > 1})
        if dup:
            raise ValueError(f"雇主 ID 重复 {dup}")
        for a in self.abilities:
            if a.scope.kind == "employer" and a.scope.employer_id not in emp:
                raise ValueError(
                    f"能力 {a.ability_id} 的作用域引用了没有声明的雇主 {a.scope.employer_id}"
                )
        return self

    def ability(self, ability_id: str) -> AbilitySpec | None:
        return next((a for a in self.abilities if a.ability_id == ability_id), None)

    def employer(self, employer_id: str) -> EmployerSpec | None:
        return next((e for e in self.employers if e.employer_id == employer_id), None)

    def content(self) -> dict[str, object]:
        """决定学习与岗位行为的内容，规范化成确定的字典。"""

        def scope(s: AbilityScope) -> dict[str, object]:
            occ = None if s.occupation is None else f"{s.occupation.scheme}:{s.occupation.code}"
            return {"kind": s.kind, "occupation": occ, "employer_id": s.employer_id}

        return {
            "content_schema": CAREER_RULES_CONTENT_SCHEMA,
            "version": self.version,
            "attendance": self.attendance,
            "completion": self.completion,
            "grant": self.grant,
            "effective_from": self.effective_from,
            "qualification_basis": self.qualification_basis,
            "job_access": self.job_access,
            "abilities": sorted(
                (
                    {"ability_id": a.ability_id, "kind": a.kind, "scope": scope(a.scope)}
                    for a in self.abilities
                ),
                key=lambda x: str(x["ability_id"]),
            ),
            "employers": sorted(
                ({"employer_id": e.employer_id, "city": str(e.city)} for e in self.employers),
                key=lambda x: str(x["employer_id"]),
            ),
        }

    @property
    def content_fingerprint(self) -> str:
        return canonical_fingerprint(self.content())


class HeldAbility(ContractModel):
    """一个分身已获得的一项能力，以及它从哪来、从哪一年起可用。

    * ``initial``：开局时已有（用户陈述或研究设定），从第 1 年起可用；
    * ``training``：第 ``acquired_year`` 年第 ``acquired_month`` 月完成 ``source_opportunity_id`` 获得，
      按下一年生效规则从第 ``acquired_year + 1`` 年起可用。
    """

    ability_id: KeyStr
    source: Literal["initial", "training"]
    source_opportunity_id: IdStr | None = None
    acquired_year: YearIndex | None = None
    acquired_month: Month | None = None
    usable_from_year: YearIndex

    @model_validator(mode="after")
    def _shape(self) -> HeldAbility:
        if self.source == "initial":
            if any(
                x is not None
                for x in (self.source_opportunity_id, self.acquired_year, self.acquired_month)
            ):
                raise ValueError(f"{self.ability_id}: 开局已有的能力没有来源课程与完成年月")
            if self.usable_from_year != 1:
                raise ValueError(f"{self.ability_id}: 开局已有的能力从第 1 年起可用")
            return self
        if (
            self.source_opportunity_id is None
            or self.acquired_year is None
            or self.acquired_month is None
        ):
            raise ValueError(f"{self.ability_id}: 学来的能力必须写来源课程与完成年月")
        if self.usable_from_year != self.acquired_year + 1:
            raise ValueError(
                f"{self.ability_id}: 第 {self.acquired_year} 年完成的能力从第 {self.acquired_year + 1} 年起可用"
                f"（下一年生效），得到 {self.usable_from_year}"
            )
        return self


class PositionRecord(ContractModel):
    """一份岗位。开局声明时 ``months`` 为空；结算写出的记录是这一年实际领到工资的月份。

    E1-5C：``sick_leave_months`` 是雇佣关系保留、但大病病假没有工作也没有工资的月份。雇佣关系、实际工作与领薪月份
    分开写：病假不是辞职；还没实际上过班的新岗位遇上大病，不算入职，不出现在这里。
    """

    opportunity_id: IdStr
    occupation: OccupationId
    employer_id: IdStr | None = None
    months: tuple[Month, ...] = ()
    sick_leave_months: tuple[Month, ...] = Field(default=(), exclude_if=lambda v: v == ())


class EmploymentRecord(ContractModel):
    """一个分身的实际岗位（E1-5A）。

    * ``stated_occupation``：用户开局陈述的职业，原样延续，不随模拟改变；
    * ``as_of = start_declaration``：开局声明的起始岗位（第 1 年年初）；
      ``as_of = settled_year``：第 ``settled_year`` 年实际执行（领到工资）的全部岗位；
    * ``current_position_ids``：年初"当前在岗"的岗位——开局声明的岗位，或上一年最后一个结算月实际在岗的
      岗位（可以是零份、一份或几份；多份工作不随便取第一份）。

    职业与雇主相关的门槛只读当前岗位，不读一个永不更新的初始身份。
    """

    stated_occupation: OccupationId | None = None
    as_of: Literal["start_declaration", "settled_year"]
    settled_year: YearIndex | None = None
    positions: tuple[PositionRecord, ...] = ()
    current_position_ids: tuple[IdStr, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> EmploymentRecord:
        ids = [p.opportunity_id for p in self.positions]
        if len(ids) != len(set(ids)):
            raise ValueError("岗位记录重复")
        stray = sorted(set(self.current_position_ids) - set(ids))
        if stray:
            raise ValueError(f"当前岗位 {stray} 不在岗位记录里")
        if len(self.current_position_ids) != len(set(self.current_position_ids)):
            raise ValueError("当前岗位重复")
        if self.as_of == "start_declaration":
            if self.settled_year is not None or any(p.months for p in self.positions):
                raise ValueError("开局声明的岗位没有结算年份与领薪月份")
            if any(p.sick_leave_months for p in self.positions):
                raise ValueError("开局声明的岗位没有病假月份（E1-5C 的病假只由结算写出）")
            if set(self.current_position_ids) != set(ids):
                raise ValueError("开局声明的岗位就是当前岗位")
        else:
            if self.settled_year is None:
                raise ValueError("结算写出的岗位记录必须写是哪一年")
            # E1-5C：保留雇佣关系的病假月份也算这一年的岗位事实（病假不是辞职）；两者都没有就不是这一年的岗位
            if any(not p.months and not p.sick_leave_months for p in self.positions):
                raise ValueError("结算写出的岗位必须有实际领薪月份")
            if any(set(p.months) & set(p.sick_leave_months) for p in self.positions):
                raise ValueError("同一个月不能既领薪又病假")
        return self

    def current_positions(self) -> tuple[PositionRecord, ...]:
        return tuple(p for p in self.positions if p.opportunity_id in self.current_position_ids)

    def occupation_summary(self) -> OccupationId | None:
        """兼容摘要（写进 ``PersonYearState.occupation``）：当前岗位都是同一个职业时就是它；没有岗位或职业
        不止一个时为空——岗位集合见 ``positions``，不随便取第一份冒充全部。"""
        occs = {(p.occupation.scheme, p.occupation.code) for p in self.current_positions()}
        if len(occs) != 1:
            return None
        return self.current_positions()[0].occupation


# ==========================================================================
# E1-5B：目标储蓄（DECISIONS/0026）
#
# 这里只有形状、局部约束与两条共用判断（哪些机会能当目标、目标的报价依据怎么写）。年初指令与月末进展在
# ``econ.life_savings``；目录、状态与计划的跨对象检查在 ``ledger.life_catalog``；可见部分由
# ``agentlayer.life_views`` 逐字段投影。
#
# 目标是**软约束**：不创建资产、不转移现金、不扣款、不锁钱。唯一的余额仍是原来那一本现金账；"余额不少于报价"
# 只是一条比较，不代表扣除未来生活费后一定负担得起，也不叫"已经获得"。兑现只认年度行动里明确关联目标、实际
# 执行并全额付清的那一笔交易；声明目标、存钱或兑现本身都不加满足感。
# ==========================================================================

#: 只支持两种目标：一次性购买的耐用品，或一门课程的一次性学费。
SavingsGoalKind = Literal["durable_purchase", "training_tuition"]
SAVINGS_GOAL_KIND_LABEL: dict[str, str] = {
    "durable_purchase": "一次性购买的耐用品",
    "training_tuition": "一门课程的一次性学费",
}
#: 目标状态：active 进行中（未到期、未兑现）、overdue 逾期（目标年月已过、仍未兑现，仍可在之后兑现、改时点或取消）、
#: realized 已兑现、cancelled 已取消、replaced 已被另一个目标替换。前两种是"未结束"。
SavingsGoalStatus = Literal["active", "overdue", "realized", "cancelled", "replaced"]
OPEN_GOAL_STATUSES: frozenset[str] = frozenset({"active", "overdue"})
SAVINGS_GOAL_STATUS_LABEL: dict[str, str] = {
    "active": "进行中",
    "overdue": "已到期、未兑现",
    "realized": "已兑现（实际付清）",
    "cancelled": "已取消",
    "replaced": "已被替换",
}
GoalEventKind = Literal[
    "created", "kept", "rescheduled", "cancelled", "replaced", "due_unrealized", "realized"
]
#: 年初在年度计划里发生的事件（``month`` 为空）；其余两种在某个月的月末发生。
YEAR_START_GOAL_EVENTS: frozenset[str] = frozenset(
    {"created", "kept", "rescheduled", "cancelled", "replaced"}
)
GoalDirectiveAction = Literal["set", "keep", "cancel", "replace", "reschedule"]


class SavingsRules(ContractModel):
    """E1-5B 的目标储蓄规则（本项目选择的行为规则，不是可以训练或调优的参数）。

    * ``goal_kinds``：目标只能指向一次性购买的耐用品或一门课的一次性学费；
    * ``max_open_goals = 1``：同一时刻只有一个未结束的目标——没有资金争抢、自动优先级或分期；
    * ``changes``：只在年初的年度计划里设定、保留、取消、替换或改时点；年内只按真实交易更新进展；
    * ``funding``：同一本现金账，不建账户、不圈存、不复制余额；
    * ``progress``：每个完整过完的月份月末，比较实际余额与目标**当前**报价（报价是当前资料，不是价格预测）；
    * ``realization``：只认年度行动里明确关联目标、实际执行并全额付清的那一笔交易；
    * ``due``：到目标年月月末仍未兑现只记"到期未兑现"，不出局、不停批次、不自动延期；
    * ``automatic = none``：不自动购买、不自动延期、不自动换商品或换城。
    """

    version: Literal["e1-5b-savings-goals-v1"]
    goal_kinds: tuple[SavingsGoalKind, ...] = Field(min_length=1)
    max_open_goals: Literal[1]
    changes: Literal["year_start_plan_only"]
    funding: Literal["single_cash_ledger_no_earmark"]
    progress: Literal["month_end_cash_vs_current_quote"]
    realization: Literal["linked_executed_full_payment"]
    due: Literal["record_unrealized_at_target_month_end"]
    automatic: Literal["none"]
    source: SourceRecord
    note: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _kinds_unique(self) -> SavingsRules:
        if len(set(self.goal_kinds)) != len(self.goal_kinds):
            raise ValueError("goal_kinds 重复")
        return self

    def content(self) -> dict[str, object]:
        """决定目标行为的内容，规范化成确定的字典（溯源与备注不参与）。"""
        return {
            "content_schema": SAVINGS_RULES_CONTENT_SCHEMA,
            "version": self.version,
            "goal_kinds": sorted(self.goal_kinds),
            "max_open_goals": self.max_open_goals,
            "changes": self.changes,
            "funding": self.funding,
            "progress": self.progress,
            "realization": self.realization,
            "due": self.due,
            "automatic": self.automatic,
        }

    @property
    def content_fingerprint(self) -> str:
        return canonical_fingerprint(self.content())


def _exact_money(m: Money) -> Decimal:
    return Decimal(repr(m.amount))


class GoalQuote(ContractModel):
    """目标的报价依据：目录里这项机会的一次性报价 × 数量。**是当时的资料，不是未来价格预测**。

    ``source_kind`` 记报价的来源类型（本轮样例是 synthetic）；``quoted_year`` 是读到这份报价的行动年。
    """

    opportunity_id: IdStr
    unit_price: Money
    quantity: Annotated[int, Field(ge=1)]
    total: Money
    billing: Literal["one_time"]
    per_unit: str = Field(min_length=1, max_length=64)
    quoted_year: YearIndex
    source_kind: SourceKind

    @model_validator(mode="after")
    def _consistent(self) -> GoalQuote:
        if self.unit_price.numeraire != self.total.numeraire:
            raise ValueError("报价的单价与总额口径不一致——不同口径不能比较，也不改标签")
        require_life_numeraire(self.total.numeraire, "目标报价")
        unit, total = _exact_money(self.unit_price), _exact_money(self.total)
        if total != unit * self.quantity:
            raise ValueError(f"报价总额 {total} 不等于单价 {unit} × 数量 {self.quantity}")
        if total <= 0:
            raise ValueError("零金额没有要攒的钱，不建储蓄目标")
        return self


class GoalEvent(ContractModel):
    """一个目标的一条事件。``month`` 为空表示年初（年度计划生效时、任何交易之前）。"""

    goal_id: IdStr
    kind: GoalEventKind
    year_index: YearIndex
    month: Month | None = None
    note: str = Field(min_length=1, max_length=280)
    #: created / rescheduled：之后的目标年月；rescheduled 另记之前的。
    target_year: YearIndex | None = None
    target_month: Month | None = None
    previous_target_year: YearIndex | None = None
    previous_target_month: Month | None = None
    #: replaced：被哪个新目标替换；created（由替换产生）：替换了哪个旧目标。
    replaced_by: IdStr | None = None
    replaces: IdStr | None = None

    @model_validator(mode="after")
    def _shape(self) -> GoalEvent:
        k = self.kind
        if (k in YEAR_START_GOAL_EVENTS) != (self.month is None):
            raise ValueError(f"{self.goal_id}: {k} 事件发生在年初就不写月份，发生在月末就必须写")
        has_target = self.target_year is not None and self.target_month is not None
        if (k in ("created", "rescheduled")) != has_target:
            raise ValueError(f"{self.goal_id}: 只有建立与改时点事件写目标年月，且必须写")
        has_previous = (
            self.previous_target_year is not None and self.previous_target_month is not None
        )
        if (k == "rescheduled") != has_previous:
            raise ValueError(f"{self.goal_id}: 只有改时点事件写之前的目标年月，且必须写")
        if (k == "replaced") != (self.replaced_by is not None):
            raise ValueError(f"{self.goal_id}: 只有替换事件写 replaced_by，且必须写")
        if self.replaces is not None and k != "created":
            raise ValueError(f"{self.goal_id}: 只有建立事件写它替换了哪个旧目标")
        return self


class GoalRealization(ContractModel):
    """兑现依据：哪一年哪一月、年度计划第几项、实付多少。只说明"钱付出去了、交易成了"。

    * ``durable_purchase_executed``：耐用品实际买成；什么时候开始提供服务另按原物品规则；
    * ``tuition_paid_in_full``：课程学费实际全额付清；**不表示学完或取得资格**——那仍按原学习参加与完成规则。
    """

    year_index: YearIndex
    month: Month
    plan_order: Annotated[int, Field(ge=0)]
    opportunity_id: IdStr
    quantity: Annotated[int, Field(ge=1)]
    amount_paid: Money
    evidence: Literal["durable_purchase_executed", "tuition_paid_in_full"]
    #: 兑现时点是否不晚于当时的目标年月（改过时点的按改后的算）。
    on_time: bool
    note: str = Field(min_length=1, max_length=280)


class SavingsGoal(ContractModel):
    """一个结构化资金目标（E1-5B）。目标 ID 由年度计划给出，同一分身全史唯一。

    ``quote`` 是建立目标时的报价依据；每年实际比较用的是目录的当前报价（记在结算结果的进展里）。
    ``note`` 是用户或分身对目标的说明文字：只存储与展示，不参与任何计算。
    """

    goal_id: IdStr
    kind: SavingsGoalKind
    opportunity_id: IdStr
    quantity: Annotated[int, Field(ge=1)]
    target_year: YearIndex
    target_month: Month
    note: str | None = Field(default=None, max_length=200)
    money_basis_id: str
    quote: GoalQuote
    status: SavingsGoalStatus
    created_year: YearIndex
    events: tuple[GoalEvent, ...] = Field(min_length=1)
    realization: GoalRealization | None = None
    replaced_by: IdStr | None = None

    @model_validator(mode="after")
    def _consistent(self) -> SavingsGoal:
        g = self.goal_id
        require_life_numeraire(self.money_basis_id, f"目标 {g}")
        q = self.quote
        if (q.opportunity_id, q.quantity) != (self.opportunity_id, self.quantity):
            raise ValueError(f"{g}: 报价依据与目标的机会或数量不一致")
        if q.total.numeraire != self.money_basis_id:
            raise ValueError(
                f"{g}: 报价口径 {q.total.numeraire} 与目标口径 {self.money_basis_id} 不一致"
            )
        if self.kind == "training_tuition" and self.quantity != 1:
            raise ValueError(f"{g}: 学费目标的数量必须是 1（一门课只报一次名）")
        if self.target_year < self.created_year:
            raise ValueError(f"{g}: 目标年早于建立年")
        if any(e.goal_id != g for e in self.events):
            raise ValueError(f"{g}: 事件属于另一个目标")
        first = self.events[0]
        if first.kind != "created" or first.year_index != self.created_year:
            raise ValueError(f"{g}: 第一条事件必须是建立年的 created")
        if [e for e in self.events[1:] if e.kind == "created"]:
            raise ValueError(f"{g}: 一个目标只建立一次")
        years = [(e.year_index, 0 if e.month is None else e.month) for e in self.events]
        if years != sorted(years):
            raise ValueError(f"{g}: 事件没有按时间先后排列")
        r = self.realization
        if (self.status == "realized") != (r is not None):
            raise ValueError(f"{g}: 只有已兑现的目标带兑现依据，已兑现的必须带")
        if r is not None:
            if (r.opportunity_id, r.quantity) != (self.opportunity_id, self.quantity):
                raise ValueError(f"{g}: 兑现依据与目标的机会或数量不一致")
            if r.amount_paid.numeraire != self.money_basis_id:
                raise ValueError(f"{g}: 实付金额的口径与目标不一致")
            want = (
                "durable_purchase_executed"
                if self.kind == "durable_purchase"
                else "tuition_paid_in_full"
            )
            if r.evidence != want:
                raise ValueError(f"{g}: {self.kind} 的兑现依据只能是 {want}")
        if (self.status == "replaced") != (self.replaced_by is not None):
            raise ValueError(f"{g}: 只有被替换的目标写 replaced_by，被替换的必须写")
        last = self.events[-1].kind
        closing = {"realized": "realized", "cancelled": "cancelled", "replaced": "replaced"}
        if self.status in closing and last != closing[self.status]:
            raise ValueError(f"{g}: 状态 {self.status} 的最后一条事件必须是 {closing[self.status]}")
        if self.status in OPEN_GOAL_STATUSES and last in closing.values():
            raise ValueError(f"{g}: 已结束的目标不能是 {self.status}")
        return self

    @property
    def open(self) -> bool:
        return self.status in OPEN_GOAL_STATUSES


class SavingsGoalDirective(ContractModel):
    """年度计划里对目标的唯一一条指令（年初生效）。不写就是"已有目标原样延续"。

    =============  ==========================================================================
    set            建立一个新目标（必须还没有未结束的目标）：机会、数量、目标年月，说明文字可选
    keep           明确保留现有的未结束目标（不改任何字段）
    cancel         取消现有的未结束目标：不退款、不加钱、不发分
    replace        用新目标替换现有的未结束目标（``replaces`` 指向它），旧目标留下替换记录
    reschedule     只改现有目标的目标年月；逾期的目标由此回到"进行中"，旧时点与到期记录保留
    =============  ==========================================================================
    """

    action: GoalDirectiveAction
    goal_id: IdStr
    replaces: IdStr | None = None
    opportunity_id: IdStr | None = None
    quantity: Annotated[int, Field(ge=1)] | None = None
    target_year: YearIndex | None = None
    target_month: Month | None = None
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _shape(self) -> SavingsGoalDirective:
        a = self.action
        new = a in ("set", "replace")
        what = (self.opportunity_id, self.quantity)
        when = (self.target_year, self.target_month)
        if new:
            if None in what or None in when:
                raise ValueError(f"{a} 必须写机会 opportunity_id、数量 quantity 与目标年月")
        elif any(x is not None for x in what) or self.note is not None:
            raise ValueError(f"{a} 不改目标的机会、数量或说明文字")
        if a == "reschedule" and None in when:
            raise ValueError("reschedule 必须写新的目标年月")
        if a in ("keep", "cancel") and any(x is not None for x in when):
            raise ValueError(f"{a} 不改目标年月；要改时点用 reschedule")
        if (a == "replace") != (self.replaces is not None):
            raise ValueError("只有 replace 写 replaces（被替换的旧目标），且 replace 必须写")
        if self.replaces is not None and self.replaces == self.goal_id:
            raise ValueError("新目标与被替换的旧目标不能是同一个 ID")
        return self


class GoalMonth(ContractModel):
    """一个目标在年初或某个月月末的进展：实际余额、目标当前报价、还差多少。**只是比较，不是资产**。

    ``quote`` 为空：目录当前没有可用报价（未知或确定缺货）——差距也为空，不补零；或者目标已经兑现（兑现那个月
    只记余额与状态，不再拿付款之后的余额去比报价）。``cash_covers_quote`` 只回答"余额是否不少于报价"，不代表
    扣除未来生活费后负担得起。
    """

    goal_id: IdStr
    month: Month | None = None
    cash: Money
    quote: Money | None = None
    shortfall: Money | None = None
    cash_covers_quote: bool | None = None
    status: SavingsGoalStatus

    @model_validator(mode="after")
    def _consistent(self) -> GoalMonth:
        known = (
            self.quote is not None,
            self.shortfall is not None,
            self.cash_covers_quote is not None,
        )
        if len(set(known)) != 1:
            raise ValueError(
                f"{self.goal_id}: 报价、差距与比较要么都有，要么都为空（报价未知不补零）"
            )
        if self.status == "realized" and self.quote is not None:
            raise ValueError(f"{self.goal_id}: 已兑现的目标不再拿余额比报价")
        if self.quote is not None and self.shortfall is not None:
            if len({self.cash.numeraire, self.quote.numeraire, self.shortfall.numeraire}) != 1:
                raise ValueError(f"{self.goal_id}: 余额、报价与差距口径不一致——不同口径不比较")
            cash, quote = _exact_money(self.cash), _exact_money(self.quote)
            if _exact_money(self.shortfall) != max(Decimal(0), quote - cash):
                raise ValueError(f"{self.goal_id}: 差距必须是 max(0, 报价 − 余额)")
            if self.cash_covers_quote != (cash >= quote):
                raise ValueError(f"{self.goal_id}: 比较结果与余额、报价不一致")
        return self


class SavingsYear(ContractModel):
    """一年的目标事实（结算结果的一部分）：年初指令、这一年的目标事件、年初与各个完整月份月末的进展、
    兑现依据与未兑现原因。出局当月及以后不再更新（此前的事实保留）。"""

    rules_version: Literal["e1-5b-savings-goals-v1"]
    directive: GoalDirectiveAction | None = None
    directive_goal_id: IdStr | None = None
    #: 年度计划里关联目标的那一项（写了 ``goal_id`` 的行动）。
    linked_plan_order: Annotated[int, Field(ge=0)] | None = None
    events: tuple[GoalEvent, ...] = ()
    progress: tuple[GoalMonth, ...] = ()
    realization: GoalRealization | None = None
    notes: tuple[Annotated[str, Field(min_length=1, max_length=280)], ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> SavingsYear:
        if (self.directive is None) != (self.directive_goal_id is None):
            raise ValueError("年初指令与它指向的目标要一起写")
        return self


def savings_goal_kind(o: LifeOpportunity) -> SavingsGoalKind | None:
    """这项机会能不能当储蓄目标、能的话是哪一种。只看目录里的形状：一次性计价的耐用品或培训。

    不看可得性、报价是否已知或是否已持有——那些是加载与结算前检查里的判断（说明原因后拒绝）。
    """
    if o.occupation is not None or o.price is None or o.price.billing != "one_time":
        return None
    if o.duration.kind == "durable_good":
        return "durable_purchase"
    if o.duration.kind == "training_course":
        return "training_tuition"
    return None


# ==========================================================================
# E1-5C：疾病、应急与恢复（DECISIONS/0027）
#
# 这里只有形状、局部约束、内容指纹与表格查找。逐月的事件、治疗与恢复在 ``econ.life_health``（纯函数与记录器），
# 接进 ``econ.life_settlement``；目录、状态、计划与画像的跨对象检查在 ``ledger.life_catalog``；可见部分由
# ``agentlayer.life_views`` 逐字段投影。
#
# 三件事分开登记：**世界规则**（``HealthRules``：月概率表、恢复期、小病与大病的限制与恢复负担——全组共用）、
# **实验设置**（``IllnessEventSource``：关闭 / 指定事件 / 配对随机数）、**用户输入的含义**（``IndependentHealthInputDeclaration``：
# 独立的健康风险应对问卷，只供代理做计划，不是病痛权重、也不是患病概率）。城市只经
# 治疗目录（``TreatmentOffer``：可得性、费用、就医时间与负担）影响生活；疗效与恢复期全组共用，不从城市背景分推出。
# ==========================================================================

HealthStatus = Literal["healthy", "minor", "major"]
Severity = Literal["minor", "major"]
SEVERITIES: tuple[str, ...] = ("minor", "major")
HEALTH_STATUS_LABEL: dict[str, str] = {"healthy": "健康", "minor": "小病", "major": "大病"}
HEALTH_RULES_VERSION = "e1-5c-health-v1"
HEALTH_RULES_CONTENT_SCHEMA = "e1-5c-health-content-v1"
ILLNESS_STREAM_VERSION = "e1-5c-illness-stream-v1"
ILLNESS_SOURCE_CONTENT_SCHEMA = "e1-5c-illness-source-content-v1"
#: 月概率最多四位小数（例如 0.0150）；恢复小时与负担沿用资源输入的两位小数。
PROBABILITY_DECIMALS = 4
#: 年龄的防呆范围（整数岁）。
AGE_MIN, AGE_MAX = 14, 120
#: 重复分身序号的上限：同一个人在同一座城的第几个重复分身。跨城同序号的分身共用同一串随机数（配对）。
MAX_REPEAT_INDEX = 999
#: 疾病相关的内部参数键名（风险表、抽签与随机源）。它们不能写进生活事实，也不能出现在任何可见 payload 里。
RESERVED_HEALTH_KEYS: frozenset[str] = frozenset(
    {
        "p_minor",
        "p_major",
        "uniform",
        "seed",
        "pairing_id",
        "key_fingerprint",
        "risk_cells",
        "age_bands",
        "burden_bands",
        "burden_band",
        "age_band",
        "first_month_burden_band",
    }
)


class TreatmentNeed(ContractModel):
    """一种病情需要的治疗服务——全组共用的需求定义。每座城的治疗条目必须引用它，比较组按它对齐口径。"""

    severity: Severity
    need_id: KeyStr
    description: str = Field(min_length=1, max_length=280)


class SeverityRules(ContractModel):
    """一种病情在一个月里的世界规则（全组共用，合成设定）。

    * ``work``：小病照常上班（``continues``）；大病整月病假、没有工资（``sick_leave_unpaid``）；
    * ``participation``：大病时暂停一切"有参与时间"的主动活动（上课、一次体验、每月有小时数的服务与持有物；食品准备
      除外）；小病照计划进行，代理声明的暂停顺序按需使用；
    * ``untreated_months``：不治疗时持续几个月（含发病月）；在某个病月治疗，当月月末恢复；
    * 恢复负担：每个病月本人额外花 ``recovery_hours_per_month`` 小时，每小时负担 ``recovery_burden_per_hour``——
      这是时间与精力账里的一项，不是精力额度的系数。

    小病不整月停工、大病整月停工，是所有者裁决（DECISIONS/0027），所以按病情固定，不能配置成别的组合。
    """

    severity: Severity
    work: Literal["continues", "sick_leave_unpaid"]
    participation: Literal["as_planned", "paused"]
    untreated_months: Annotated[int, Field(ge=1, le=12)]
    recovery_hours_per_month: Annotated[float, Field(ge=0, le=744)]
    recovery_burden_per_hour: Annotated[float, Field(ge=0)]
    description: str = Field(min_length=1, max_length=280)

    @model_validator(mode="after")
    def _owner_rules(self) -> SeverityRules:
        want = {"minor": ("continues", "as_planned"), "major": ("sick_leave_unpaid", "paused")}
        if (self.work, self.participation) != want[self.severity]:
            raise ValueError(
                f"{self.severity}: 工作与参与规则必须是 {want[self.severity]}——小病不自动整月停工，大病整月停工并暂停"
                "主动活动（所有者裁决），不能配置成别的组合"
            )
        for name, x in (
            ("recovery_hours_per_month", self.recovery_hours_per_month),
            ("recovery_burden_per_hour", self.recovery_burden_per_hour),
        ):
            if not has_decimals(x, RESOURCE_INPUT_DECIMALS):
                raise ValueError(f"{self.severity}.{name} = {x!r} 的精度细于两位小数")
        return self


class AgeBand(ContractModel):
    """风险表的一个年龄段（整数岁，含两端；``max_age`` 为空表示不封顶）。"""

    band_id: KeyStr
    min_age: Annotated[int, Field(ge=0, le=AGE_MAX)]
    max_age: Annotated[int, Field(ge=0, le=AGE_MAX)] | None = None


class BurdenBand(ContractModel):
    """风险表的一档"上月工作相对负担"（= 上一个完整月份的工作负担 ÷ 当月额度）：``lower`` 含、``upper`` 不含。"""

    band_id: KeyStr
    lower: Annotated[float, Field(ge=0)]
    upper: Annotated[float, Field(gt=0)] | None = None


class RiskCell(ContractModel):
    """一格月概率：这个年龄段、这一档上月工作相对负担下，一个健康月份新发小病与大病的概率。"""

    age_band: KeyStr
    burden_band: KeyStr
    minor: Annotated[float, Field(ge=0, le=1)]
    major: Annotated[float, Field(ge=0, le=1)]

    @model_validator(mode="after")
    def _probabilities(self) -> RiskCell:
        for name, p in (("minor", self.minor), ("major", self.major)):
            if not has_decimals(p, PROBABILITY_DECIMALS):
                raise ValueError(
                    f"{self.age_band}/{self.burden_band}.{name} = {p!r} 的精度细于四位小数"
                )
        if to_fraction(self.minor) + to_fraction(self.major) > 1:
            raise ValueError(f"{self.age_band}/{self.burden_band}: 小病与大病的月概率之和超过 1")
        return self


class RiskTable(ContractModel):
    """新发疾病的月概率表（世界规则，合成设定）：年龄段 × 上月实际工作相对负担。

    * 只在健康月份的月初使用；正在生病的月份延续原来的疾病，不抽新事件；
    * 读**上一个完整月份**的工作负担（窗口一个月），不读本月生病之后的工作量；
    * 第一个月（还没有任何完整月份）用声明的基础档 ``first_month_burden_band``；
    * 年龄是整数岁，每过一年加一（``integer_age_plus_one_per_year``）。

    这张表对代理**不可见**（默认信息条件）；健康输入（医疗点数）不读它，也不改它。
    """

    unit: Literal["per_month"]
    burden_measure: Literal["previous_month_work_relative_burden"]
    history_window_months: Literal[1]
    first_month_burden_band: KeyStr
    age_rule: Literal["integer_age_plus_one_per_year"]
    age_bands: tuple[AgeBand, ...] = Field(min_length=1)
    burden_bands: tuple[BurdenBand, ...] = Field(min_length=1)
    cells: tuple[RiskCell, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _complete(self) -> RiskTable:
        ages = self.age_bands
        for a, b in itertools.pairwise(ages):
            if a.max_age is None or b.min_age != a.max_age + 1:
                raise ValueError(f"年龄段 {a.band_id} 与 {b.band_id} 不衔接（整数岁，含两端）")
        for a in ages:
            if a.max_age is not None and a.max_age < a.min_age:
                raise ValueError(f"年龄段 {a.band_id} 的上下限颠倒")
        if ages[-1].max_age is not None:
            raise ValueError("最后一个年龄段必须不封顶（max_age 留空）")
        bands = self.burden_bands
        if to_fraction(bands[0].lower) != 0:
            raise ValueError("第一档上月工作相对负担必须从 0 开始")
        for a, b in itertools.pairwise(bands):
            if a.upper is None or to_fraction(b.lower) != to_fraction(a.upper):
                raise ValueError(f"负担档 {a.band_id} 与 {b.band_id} 不衔接（下限含、上限不含）")
        for b in bands:
            if b.upper is not None and to_fraction(b.upper) <= to_fraction(b.lower):
                raise ValueError(f"负担档 {b.band_id} 的上限不大于下限")
            for x in (b.lower, b.upper):
                if x is not None and not has_decimals(x, RESOURCE_INPUT_DECIMALS):
                    raise ValueError(f"负担档 {b.band_id} 的边界 {x!r} 精度细于两位小数")
        if bands[-1].upper is not None:
            raise ValueError("最后一档上月工作相对负担必须不封顶（upper 留空）")
        age_ids = [a.band_id for a in ages]
        burden_ids = [b.band_id for b in bands]
        if len(set(age_ids)) != len(age_ids) or len(set(burden_ids)) != len(burden_ids):
            raise ValueError("年龄段或负担档的 ID 重复")
        cells = [(c.age_band, c.burden_band) for c in self.cells]
        want = {(a, b) for a in age_ids for b in burden_ids}
        if len(cells) != len(set(cells)) or set(cells) != want:
            raise ValueError(
                f"概率表必须恰好覆盖每个年龄段 × 每档负担各一格：缺 {sorted(want - set(cells))}，"
                f"多 {sorted(set(cells) - want)}"
            )
        if self.first_month_burden_band not in burden_ids:
            raise ValueError(f"第一个月的基础档 {self.first_month_burden_band} 不在负担档里")
        return self

    def age_band_of(self, age: int) -> AgeBand | None:
        return next(
            (
                a
                for a in self.age_bands
                if a.min_age <= age and (a.max_age is None or age <= a.max_age)
            ),
            None,
        )

    def burden_band_of(self, relative: Fraction) -> BurdenBand:
        for b in self.burden_bands:
            if to_fraction(b.lower) <= relative and (
                b.upper is None or relative < to_fraction(b.upper)
            ):
                return b
        raise LifeContractError(f"上月工作相对负担 {relative} 不在任何一档里")

    def cell(self, age_band: str, burden_band: str) -> RiskCell:
        return next(c for c in self.cells if (c.age_band, c.burden_band) == (age_band, burden_band))


class HealthRules(ContractModel):
    """E1-5C 的疾病与恢复世界规则（DECISIONS/0027；全组共用，全部数值是合成设定，不是医学结论）。

    * 状态只有健康、小病、大病三种；一次一病（``one_illness_at_a_time``）；新事件只在健康月份发生；
    * 月初状态决定这个月的工作限制与健康分（``month_start_state_sets_month``）；
    * 在疾病的某个月接受治疗，当月月末恢复（``recover_at_end_of_treated_month``）；只在"不治就还会延续"的月份才购买
      治疗（``only_when_it_shortens_the_course``），所以同一次疾病最多收一次费；
    * 付款顺序：最低生活检查（只检查不扣款）→ 住房、食品与合法兜底 → 治疗（钱与时间、精力允许时）→ 其他行动；
    * 应急规则不够时：按代理声明的顺序逐项暂停，仍超限就放弃治疗，再超限就停止这个分身并保留轨迹
      （``declared_order_as_needed_then_skip_treatment_then_stop``）。
    """

    version: Literal["e1-5c-health-v1"]
    states: Literal["healthy_minor_major"]
    concurrency: Literal["one_illness_at_a_time"]
    onset: Literal["new_events_only_in_healthy_months"]
    month_state: Literal["month_start_state_sets_month"]
    treatment_effect: Literal["recover_at_end_of_treated_month"]
    treatment_offer: Literal["only_when_it_shortens_the_course"]
    charge: Literal["once_per_treated_illness"]
    payment_order: Literal["minimum_check_housing_food_then_treatment_then_other"]
    pause_rule: Literal["declared_order_as_needed_then_skip_treatment_then_stop"]
    minor: SeverityRules
    major: SeverityRules
    treatment_needs: tuple[TreatmentNeed, ...] = Field(min_length=2, max_length=2)
    risk: RiskTable
    source: SourceRecord
    note: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _consistent(self) -> HealthRules:
        if self.minor.severity != "minor" or self.major.severity != "major":
            raise ValueError("minor / major 两项必须分别写小病与大病的规则")
        if sorted(n.severity for n in self.treatment_needs) != ["major", "minor"]:
            raise ValueError("治疗需求必须小病、大病各一条")
        if len({n.need_id for n in self.treatment_needs}) != 2:
            raise ValueError("小病与大病的治疗需求 ID 必须不同")
        return self

    def severity_rules(self, severity: str) -> SeverityRules:
        return self.minor if severity == "minor" else self.major

    def need(self, severity: str) -> TreatmentNeed:
        return next(n for n in self.treatment_needs if n.severity == severity)

    def content(self) -> dict[str, object]:
        """决定疾病行为的全部内容（溯源、备注与说明文字不参与）。键顺序不参与，数值按原写法参与。"""
        data = self.model_dump(mode="json")
        data.pop("source")
        data.pop("note")
        for key in ("minor", "major"):
            data[key].pop("description")
        for need in data["treatment_needs"]:
            need.pop("description")
        return {"content_schema": HEALTH_RULES_CONTENT_SCHEMA, "rules": data}

    @property
    def content_fingerprint(self) -> str:
        return canonical_fingerprint(self.content())


class InjectedIllness(ContractModel):
    """一条指定事件（实验设置）：这个人第几个重复分身、第几年第几月月初发生小病或大病。跨城同序号的分身共用。"""

    person_id: IdStr
    repeat_index: Annotated[int, Field(ge=0, le=MAX_REPEAT_INDEX)]
    year_index: YearIndex
    month: Month
    severity: Severity
    note: str = Field(min_length=1, max_length=200)


class IllnessEventSource(ContractModel):
    """新疾病事件从哪里来（实验设置，不是世界规则）：

    =========  ==========================================================================
    none       不发生新事件（无病对照）；年初已在延续的疾病照常按规则走
    injected   只用 ``injected`` 列出的指定事件（先用确定的事件检验，再接随机）
    random     按风险表与配对随机数抽：随机数只由配对身份、种子、人、重复序号、绝对月份与事件类别决定
    =========  ==========================================================================

    指定事件与随机抽签走同一个疾病处理器。随机数的键里没有城市、分身编号、遍历顺序、重试次数，也没有评分或风险规则
    的指纹——同序号的分身跨城共用运气；改了风险规则，旧的中间状态由健康状态的绑定拒绝，而不是靠换随机数。
    """

    mode: Literal["none", "injected", "random"]
    stream_version: Literal["e1-5c-illness-stream-v1"]
    pairing_id: IdStr | None = None
    seed: Annotated[int, Field(ge=0, le=2**63 - 1)] | None = None
    injected: tuple[InjectedIllness, ...] = ()
    note: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _mode_consistent(self) -> IllnessEventSource:
        random = (self.pairing_id is not None, self.seed is not None)
        if self.mode == "random":
            if random != (True, True):
                raise ValueError("random 必须写配对身份 pairing_id 与种子 seed")
            if self.injected:
                raise ValueError("random 不混用指定事件（指定与随机是两个实验条件）")
        else:
            if any(random):
                raise ValueError(f"{self.mode} 不用随机数，不写 pairing_id 或 seed")
            if self.mode == "none" and self.injected:
                raise ValueError("none 不发生新事件，不能列指定事件")
            if self.mode == "injected" and not self.injected:
                raise ValueError("injected 至少要列一条指定事件；没有事件请用 none")
        keys = [(e.person_id, e.repeat_index, e.year_index, e.month) for e in self.injected]
        if len(keys) != len(set(keys)):
            raise ValueError("同一个人、同一重复序号、同一年月只能指定一个事件（一次一病）")
        return self

    def content(self) -> dict[str, object]:
        return {
            "content_schema": ILLNESS_SOURCE_CONTENT_SCHEMA,
            "mode": self.mode,
            "stream_version": self.stream_version,
            "pairing_id": self.pairing_id,
            "seed": self.seed,
            "injected": sorted(
                (
                    {
                        "person_id": e.person_id,
                        "repeat_index": e.repeat_index,
                        "year_index": e.year_index,
                        "month": e.month,
                        "severity": e.severity,
                    }
                    for e in self.injected
                ),
                key=lambda x: (x["person_id"], x["repeat_index"], x["year_index"], x["month"]),
            ),
        }

    @property
    def content_fingerprint(self) -> str:
        return canonical_fingerprint(self.content())

    def injected_for(
        self, person_id: str, repeat_index: int, year: int, month: int
    ) -> InjectedIllness | None:
        return next(
            (
                e
                for e in self.injected
                if (e.person_id, e.repeat_index, e.year_index, e.month)
                == (person_id, repeat_index, year, month)
            ),
            None,
        )


#: E3-A（DECISIONS/0034）：新产品画像里健康回答的含义——独立的健康风险应对问卷，**不占**六维 100 分，也不是第七个评分权重。
INDEPENDENT_HEALTH_INPUT_SEMANTICS = "independent_health_answers_v1"


class IndependentHealthInputDeclaration(ContractModel):
    """新产品协议对健康回答的声明（E3-A，DECISIONS/0034）：健康回答是一份**独立**的有版本问卷，不在六维定和里。

    只作为代理做计划的信息：不是病痛权重、患病概率、恢复速度、疗效、愿付金额或治疗指令；结算、
    风险抽签与评分都不读它（六维权重本来就不含它，γ 与 H 全组共用）。``questionnaire_version`` 必须等于画像里健康回答的问卷版本：
    换了题目就是另一份声明，旧答案不能改读成新题目的答案。
    """

    kind: Literal["independent_health_answers"]
    semantics: Literal["independent_health_answers_v1"]
    questionnaire_version: str = Field(min_length=1, max_length=80)
    use: Literal["agent_planning_information_only"]
    note: str = Field(min_length=1, max_length=400)


class TreatmentOffer(ContractModel):
    """一座城对一种病情的治疗条目（城市事实）：可得性、当地基年价、就医时间与负担、所满足的需求、来源。

    * ``available``：有报价（一次性，按"每次疾病"计费，零价合法）、就医小时与每小时负担，三项都要写；
    * ``unavailable``：**确定**没有。不带价格与时间；
    * ``unknown``：没有资料（或资料不全）。**不是免费，也不是没有**：不带价格与时间，必须写原因。有人要治疗而资料未知，
      是资料 / 支持缺口，不是"现实看不起病"。

    疗效不在这里：所有城市共用 ``HealthRules`` 的恢复规则；价格、疗效与等待时间都不从城市背景分推出。
    """

    treatment_id: IdStr
    severity: Severity
    need_id: KeyStr
    label: str = Field(min_length=1, max_length=120)
    serves: str = Field(min_length=1, max_length=280)
    availability: Availability
    price: PriceQuote | None = None
    visit_hours: Annotated[float, Field(ge=0, le=744)] | None = None
    burden_per_hour: Annotated[float, Field(ge=0)] | None = None
    unknown_reason: str | None = Field(default=None, max_length=280)
    source: SourceRecord

    @model_validator(mode="after")
    def _availability_consistent(self) -> TreatmentOffer:
        facts = (self.price, self.visit_hours, self.burden_per_hour)
        tid = self.treatment_id
        if self.availability == "available":
            if any(x is None for x in facts):
                raise ValueError(
                    f"{tid}: available 必须写报价、就医小时与每小时负担——缺一项就是资料不全，请标 unknown 并写原因"
                )
            assert self.price is not None
            if self.price.billing != "one_time":
                raise ValueError(f"{tid}: 治疗按每次疾病一次性计费，得到 {self.price.billing}")
            for name, x in (
                ("visit_hours", self.visit_hours),
                ("burden_per_hour", self.burden_per_hour),
            ):
                if x is not None and not has_decimals(x, RESOURCE_INPUT_DECIMALS):
                    raise ValueError(f"{tid}: {name} = {x!r} 的精度细于两位小数")
        elif any(x is not None for x in facts):
            raise ValueError(
                f"{tid}: {self.availability} 不能带价格或时间——未知不是免费，确定没有也没有成交价"
            )
        if (self.availability == "unknown") != (self.unknown_reason is not None):
            raise ValueError(f"{tid}: 只有 unknown 写原因 unknown_reason，unknown 必须写")
        return self


EmergencyChoice = Literal["treat_if_possible", "decline"]


class EmergencyPlan(ContractModel):
    """年度计划里的应急安排（DECISIONS/0027）。只是一个小结构，不是规则语言；**不由健康输入自动生成**。

    * ``minor`` / ``major``：这一年生了这种病时，条件允许（本城可得、这个月钱与时间、精力够、治疗还有用）就治，或者
      明确不治；
    * ``pause_order``：生病而这个月的时间或精力不够时，按这个顺序逐项暂停的活动（只能是有参与时间的主动活动：学习、
      一次体验、有参与时间的服务或持有物）。只暂停到够为止；全部暂停仍不够就放弃治疗；再不够就停止这个分身。
    * ``cancel_purchases_when_ill``（收尾修订，2026-10-11）：这一年计划里的一次性耐用品购买，按机会 ID 列出。购买那个月
      月初生着病（小病或大病，新发或延续）就**取消这一次购买**——不扣款、不生成持有物、不顺延、不补购；健康的月份照常买。
      它与 ``pause_order`` 是两件事：暂停处理时间或精力不够，这一项是"生病时先把钱留着"，不要求先证明钱、时间或精力不够。
      只覆盖一次性耐用品；课程学费、年付合同与其他交易照已有规则执行。没有用到时不序列化——旧计划的输出与指纹逐字节不变。

    新协议的年度计划**必须**写它：漏写不会被默认成"总是治疗"。
    """

    minor: EmergencyChoice
    major: EmergencyChoice
    pause_order: tuple[IdStr, ...] = ()
    cancel_purchases_when_ill: tuple[IdStr, ...] = Field(default=(), exclude_if=lambda v: v == ())
    note: str | None = Field(default=None, max_length=280)

    @model_validator(mode="after")
    def _unique(self) -> EmergencyPlan:
        if len(set(self.pause_order)) != len(self.pause_order):
            raise ValueError("pause_order 重复")
        if len(set(self.cancel_purchases_when_ill)) != len(self.cancel_purchases_when_ill):
            raise ValueError("cancel_purchases_when_ill 重复：同一件购买只声明一次")
        return self

    def choice(self, severity: str) -> str:
        return self.minor if severity == "minor" else self.major


class TreatmentRecord(ContractModel):
    """一次疾病实际接受并付清的治疗（只有一次）。"""

    treatment_id: IdStr
    year_index: YearIndex
    month: Month
    amount_paid: Money
    visit_hours: Annotated[float, Field(ge=0)]
    burden_per_hour: Annotated[float, Field(ge=0)]


IllnessSource = Literal["injected", "random"]


class IllnessEpisode(ContractModel):
    """一次疾病：从哪来、哪年哪月发病、已经病了几个月、有没有治疗、哪个月月末恢复。

    ``months_ill`` 是已经开始的病月数（含发病月；跨年累计）。治疗过的疾病在治疗当月月末结束；没治疗的到声明的病程
    月数结束（``recovery = natural``）。结束了的疾病只留在历史里，不再计入任何月份。
    """

    episode_id: IdStr
    severity: Severity
    source: IllnessSource
    onset_year: YearIndex
    onset_month: Month
    months_ill: Annotated[int, Field(ge=1, le=36)]
    treatment: TreatmentRecord | None = None
    ended: bool = False
    recovered_year: YearIndex | None = None
    recovered_month: Month | None = None
    recovery: Literal["treated", "natural"] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> IllnessEpisode:
        e = self.episode_id
        closing = (self.recovered_year, self.recovered_month, self.recovery)
        if self.ended != all(x is not None for x in closing) or (
            not self.ended and any(x is not None for x in closing)
        ):
            raise ValueError(f"{e}: 结束的疾病必须写恢复年月与恢复方式，没结束的不能写")
        t = self.treatment
        if (t is not None) != (self.recovery == "treated"):
            raise ValueError(f"{e}: 只有治疗过的疾病恢复方式是 treated，治疗过的必须是")
        if t is not None and (t.year_index, t.month) != (self.recovered_year, self.recovered_month):
            raise ValueError(f"{e}: 治疗当月月末恢复——治疗年月与恢复年月必须相同")
        if self.ended and (self.recovered_year, self.recovered_month) < (
            self.onset_year,
            self.onset_month,
        ):
            raise ValueError(f"{e}: 恢复早于发病")
        return self


class HealthBinding(ContractModel):
    """产生这份健康状态的世界规则与事件来源（内容指纹）。共同初始状态不绑定；结算产生的状态一律绑定。"""

    rules_version: Literal["e1-5c-health-v1"]
    rules_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class HealthState(ContractModel):
    """一个分身的健康状态（E1-5C）：年龄、重复序号、正在延续的疾病、病史、上一个完整月份的工作相对负担、绑定。

    * ``episode``：年初（或出局、停止、终态时）仍在延续的疾病；没有就是健康；
    * ``last_work_relative_burden``：上一个完整月份的工作负担 ÷ 当月额度（风险表读它；记下它来自哪年哪月）。为空表示
      还没有任何完整月份——第一个月用风险表声明的基础档；
    * ``binding``：只有共同初始状态（健康、没有病史、没有负担记录）可以不绑定。
    """

    age_years: Annotated[int, Field(ge=AGE_MIN, le=AGE_MAX)]
    repeat_index: Annotated[int, Field(ge=0, le=MAX_REPEAT_INDEX)]
    episode: IllnessEpisode | None = None
    history: tuple[IllnessEpisode, ...] = ()
    last_work_relative_burden: ExactAmount | None = None
    last_burden_year: YearIndex | None = None
    last_burden_month: Month | None = None
    binding: HealthBinding | None = None

    @model_validator(mode="after")
    def _consistent(self) -> HealthState:
        if self.episode is not None and self.episode.ended:
            raise ValueError("正在延续的疾病不能标成已结束")
        if any(not h.ended for h in self.history):
            raise ValueError("病史里只有已经结束的疾病")
        ids = [h.episode_id for h in self.history] + (
            [self.episode.episode_id] if self.episode is not None else []
        )
        if len(ids) != len(set(ids)):
            raise ValueError(f"疾病 ID 重复 {ids}")
        trio = (self.last_work_relative_burden, self.last_burden_year, self.last_burden_month)
        if len({x is None for x in trio}) != 1:
            raise ValueError("上月工作相对负担与它来自的年月要一起写")
        if (
            self.last_work_relative_burden is not None
            and self.last_work_relative_burden.fraction() < 0
        ):
            raise ValueError("工作相对负担不能为负")
        initial = (
            self.episode is None and not self.history and self.last_work_relative_burden is None
        )
        if self.binding is None and not initial:
            raise ValueError(
                "有病史、正在生病或已有月份记录的健康状态必须写明产生它的规则与事件来源（binding）——只有共同初始"
                "状态可以不绑定"
            )
        return self

    @property
    def status(self) -> HealthStatus:
        return "healthy" if self.episode is None else self.episode.severity  # type: ignore[return-value]


class TaskSubstitution(ContractModel):
    """有这项设备或服务在用时，本人每月**还要**花在这项家务上的小时（替代后残余用时）。

    残余是本人处理**这一整项**家务后仍要花的整月时间（例如洗衣机在用时，分拣、晾、叠、收合计 3 小时），
    不是机器运行时间，也不一定是每次操作的时间。它表示同一项家务的**完整替代方案**：几个替代方案同时在用时
    取残余最小的那一个，不把节省的时间重复相减。洗衣机与烘干机这类互补工序不在这个规则里（以后另拆子任务）。
    """

    task: KeyStr
    residual_hours_per_month: Annotated[float, Field(ge=0, le=744)]


WorkMode = Literal["onsite", "remote", "hybrid"]


class JobArrangement(ContractModel):
    """岗位方案（E1-4A）：薪酬与工时、工作日、到岗日绑在一起，是目录里的事实，计划改不了。

    onsite 的到岗日等于工作日；remote 到岗日为 0；hybrid 介于两者之间（不含两端）。兼职就是工时与
    到岗日更少的岗位。每个岗位一天至多到岗一次，所以到岗日就是这个岗位的到岗次数。通勤按实际所住住房的
    往返分钟 × 这个岗位的到岗次数 / 60 计；几份岗位各算各的往返，不自动合并行程（r2）。
    """

    work_mode: WorkMode
    work_hours_per_month: Annotated[float, Field(gt=0, le=744)]
    workdays_per_month: Annotated[int, Field(ge=1, le=31)]
    onsite_days_per_month: Annotated[int, Field(ge=0, le=31)]
    burden_per_work_hour: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def _mode_consistent(self) -> JobArrangement:
        days, onsite = self.workdays_per_month, self.onsite_days_per_month
        if onsite > days:
            raise ValueError(f"到岗日 {onsite} 多于工作日 {days}")
        if self.work_mode == "onsite" and onsite != days:
            raise ValueError(f"onsite 岗位的到岗日必须等于工作日（{days}），得到 {onsite}")
        if self.work_mode == "remote" and onsite != 0:
            raise ValueError(f"remote 岗位的到岗日必须是 0，得到 {onsite}")
        if self.work_mode == "hybrid" and not 0 < onsite < days:
            raise ValueError(
                f"hybrid 岗位的到岗日必须在 0 与工作日 {days} 之间（不含两端），得到 {onsite}"
            )
        return self


ResourceBasis = Literal["passive", "per_service_month", "per_use", "per_occurrence", "job"]


class ResourceProfile(ContractModel):
    """一项机会的资源事实（E1-4A）：数的是什么、每单位多少小时与负担、数量算不算。

    =====================  ======================================================
    passive                不占用时间与负担（住房、手机、柜子、洗衣机）；可以替代家务
    per_service_month      每个实际服务月 ``hours`` 小时（食品准备、按月服务、培训参与）
    per_use                每月 ``uses_per_month`` 次 × 每次 ``hours`` 小时（如健身每月 8 次）
    per_occurrence         每次发生 ``hours`` 小时（一次体验）
    job                    岗位方案 ``job``（工时、到岗日、每工时负担）
    =====================  ======================================================

    负担 = 小时 × ``burden_per_hour``。``quantity_scales`` 写明数量是否参与（两张票是两次体验，
    还是同一次）。零负担要显式写 0；缺了不当零。
    """

    basis: ResourceBasis
    hours: Annotated[float, Field(ge=0, le=744)] | None = None
    uses_per_month: Annotated[int, Field(ge=1, le=744)] | None = None
    burden_per_hour: Annotated[float, Field(ge=0)] | None = None
    quantity_scales: bool | None = None
    job: JobArrangement | None = None
    substitutes: tuple[TaskSubstitution, ...] = ()
    note: str = Field(min_length=1, max_length=280)

    @model_validator(mode="after")
    def _basis_consistent(self) -> ResourceProfile:
        b = self.basis
        timed = b in ("per_service_month", "per_use", "per_occurrence")
        if timed:
            if self.hours is None or self.burden_per_hour is None or self.quantity_scales is None:
                raise ValueError(
                    f"{b} 必须写 hours、burden_per_hour 与 quantity_scales（零也要显式写 0）"
                )
        elif (
            self.hours is not None
            or self.burden_per_hour is not None
            or self.quantity_scales is not None
        ):
            raise ValueError(f"{b} 不按小时计，不写 hours、burden_per_hour 或 quantity_scales")
        if (b == "per_use") != (self.uses_per_month is not None):
            raise ValueError("只有 per_use 写 uses_per_month（每月使用次数），且 per_use 必须写")
        if (b == "job") != (self.job is not None):
            raise ValueError("只有 job 写岗位方案，且 job 必须写")
        if b == "job" and self.substitutes:
            raise ValueError("岗位不替代家务")
        tasks = [x.task for x in self.substitutes]
        if len(tasks) != len(set(tasks)):
            raise ValueError("同一项家务只能写一条替代")
        return self


class ResourceBudgetBasis(ContractModel):
    """一个人的资源预算依据（E1-4A）：净可分配小时已经预留了什么、额度从哪来。

    数值本身在 ``PersonYearState.time_budget_hours``（年度净可分配小时）与 ``energy.level``
    （尺度 ``ENERGY_QUOTA_SCALE``：每月可承受负担额度）里。
    """

    rules_version: Literal["e1-4a-monthly-resources-v1"]
    reserved: str = Field(min_length=1, max_length=400)
    source: SourceRecord


# ==========================================================================
# 城市生活机会
# ==========================================================================


class DurationSpec(ContractModel):
    """生效时点与持续期限。一次体验与持续物品在这里分开。

    ==================  ===========================================================
    one_time_experience 当年消费完，``lasts_years = 0``
    durable_good        持续若干年（1–30），期满前作为持有物跨年延续
    ongoing_service     付费期间有效；无固定年限，计费必须是周期性的
    training_course     需要 ``completion_years`` 年完成；**收益多少不在这里**
    ==================  ===========================================================

    **月度解释（E1-2）。** 目录里的年度期限按 ×12 换成月，不丢信息：持续物品寿命
    ``lifetime_months = 12 × lasts_years``；培训需要参与 ``12 × completion_years`` 个月。
    ``same_year`` = 在执行月生效；``next_year`` = 下一年第 1 个月生效（只对持续物品与
    培训有意义，服务与一次体验用 ``next_year`` 会被结算前检查拒绝）。培训年数"不超过 N"
    依赖协议，在加载器里检查。
    """

    kind: Literal["one_time_experience", "durable_good", "ongoing_service", "training_course"]
    effective_from: Literal["same_year", "next_year"] = "same_year"
    lasts_years: Annotated[int, Field(ge=0)] | None = None
    completion_years: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _period_makes_sense(self) -> DurationSpec:
        k = self.kind
        if k == "one_time_experience":
            if self.lasts_years != 0:
                raise ValueError("一次体验的 lasts_years 必须是 0：它当年就用完了")
            if self.completion_years is not None:
                raise ValueError("一次体验没有 completion_years")
        elif k == "durable_good":
            if self.lasts_years is None or not 1 <= self.lasts_years <= MAX_DURABLE_YEARS:
                raise ValueError(
                    f"持续物品的 lasts_years 必须在 1–{MAX_DURABLE_YEARS} 年之间，"
                    f"得到 {self.lasts_years!r}（月写成年了？）"
                )
        elif k == "ongoing_service":
            if self.lasts_years is not None:
                raise ValueError("持续服务付费期间有效，不设 lasts_years")
        else:  # training_course
            if (
                self.completion_years is None
                or not 1 <= self.completion_years <= MAX_SIMULATION_YEARS
            ):
                raise ValueError(
                    f"培训的 completion_years 必须在 1–{MAX_SIMULATION_YEARS} 年之间，"
                    f"得到 {self.completion_years!r}"
                )
            if self.lasts_years is not None:
                raise ValueError("培训不设 lasts_years；它带来的技能变化属于结算，尚未定义")
        return self

    @property
    def lifetime_months(self) -> int | None:
        """持续物品的寿命（月）。一次体验为 0；服务与培训没有寿命，返回 None。"""
        if self.kind == "durable_good":
            return MONTHS_PER_YEAR * (self.lasts_years or 0)
        if self.kind == "one_time_experience":
            return 0
        return None

    @property
    def completion_months(self) -> int | None:
        """培训完成需要参与的月数。"""
        if self.kind == "training_course" and self.completion_years is not None:
            return MONTHS_PER_YEAR * self.completion_years
        return None


# ==========================================================================
# 生活扩展（DECISIONS/0041）：完整食品方案、享受型消费、活动装备与休闲活动的**公开事实**。
#
# 这里只有事实的形状：组成、数量、时间、功能、活动条件与依据。幸福度怎样从这些事实换算出来（效率、锚点、兴趣匹配倍率）
# 只写在评分规格里（``common.life_scoring_contracts``），不进机会，也就不进代理的观察。
# ==========================================================================

#: 一周最多 21 餐（每天三餐）。只用于检查“每周餐数”没有写错单位，不是营养规定。
MEALS_PER_WEEK_MAX: int = 21
#: 两个小时数相等的容差：输入只有两位小数，1e-9 足以区分写错与浮点舍入。
HOURS_TOL: float = 1e-9
FoodMode = Literal["home_cooked", "dine_out", "delivery"]
EnjoymentMode = Literal["passive_service_month", "service_units"]


class FoodPlanPart(ContractModel):
    """完整食品方案的一个组成：怎么做（自己做 / 外食 / 外送）、每周几餐、其中几餐新鲜、主要菜系。

    ``fresh_meals_per_week`` 与 ``cuisine`` **必须写**：0 是“确定没有新鲜餐”，``null`` 是“资料未知”，不写就拒绝（不默认成 0，
    免得没有资料的方案得到一个已知的低分）。未知的新鲜餐数让饮食生活评分未知；未知菜系只在用户确认了菜系时让评分未知。
    ``cuisine`` 是词表里的菜系键；每个组成只有一个主要菜系，口味匹配按组成算一次，不按标签叠加。"""

    part_id: KeyStr
    mode: FoodMode
    meals_per_week: Annotated[float, Field(gt=0, le=MEALS_PER_WEEK_MAX)]
    fresh_meals_per_week: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    cuisine: KeyStr | None
    note: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _fresh_within_meals(self) -> FoodPlanPart:
        if (
            self.fresh_meals_per_week is not None
            and self.fresh_meals_per_week > self.meals_per_week
        ):
            raise ValueError(f"{self.part_id}: 新鲜餐数不能多于这一组成的餐数")
        return self


class FoodPlanFacts(ContractModel):
    """一个月的**完整**食品方案：覆盖整月基本饮食的声明与依据、组成、时间依据。

    “每周新鲜餐数”不是总餐数；几项食材报价或一家餐馆的位置也不是完整方案——``covers_basic_month`` 为假的方案不能执行。
    方案的价格就是机会的报价（整个方案的月费，含其中的外食）：同一部分餐食不另收一次钱。时间 = 准备 + 外出就餐
    （``prep_hours_per_month + dining_hours_per_month`` 必须等于资源事实里的每月小时）。"""

    covers_basic_month: bool
    coverage_basis: str = Field(min_length=1, max_length=280)
    meals_per_week: Annotated[float, Field(gt=0, le=MEALS_PER_WEEK_MAX)]
    parts: tuple[FoodPlanPart, ...] = Field(min_length=1)
    prep_hours_per_month: Annotated[float, Field(ge=0, le=744)]
    dining_hours_per_month: Annotated[float, Field(ge=0, le=744)]
    time_basis: str = Field(min_length=1, max_length=280)

    @model_validator(mode="after")
    def _parts_add_up(self) -> FoodPlanFacts:
        ids = [p.part_id for p in self.parts]
        if len(ids) != len(set(ids)):
            raise ValueError("食品方案的组成编号重复")
        total = sum(p.meals_per_week for p in self.parts)
        if abs(total - self.meals_per_week) > HOURS_TOL:
            raise ValueError(
                f"组成的每周餐数之和 {total:g} ≠ 方案的每周餐数 {self.meals_per_week:g}"
            )
        return self

    @property
    def fresh_meals_per_week(self) -> float | None:
        """每周新鲜餐数之和；有组成的新鲜餐数未知就是未知（None），不当 0。"""
        if any(p.fresh_meals_per_week is None for p in self.parts):
            return None
        return sum(p.fresh_meals_per_week or 0.0 for p in self.parts)

    def unknown_facts(self, cuisines: frozenset[str]) -> tuple[str, ...]:
        """会影响这位用户饮食评分、却是资料未知的事实（组成编号与字段）。菜系只在用户确认了菜系时才影响评分。"""
        out = [
            f"{p.part_id}.fresh_meals_per_week"
            for p in self.parts
            if p.fresh_meals_per_week is None
        ]
        if cuisines:
            out += [f"{p.part_id}.cuisine" for p in self.parts if p.cuisine is None]
        return tuple(out)

    def matched_meals_per_week(self, cuisines: frozenset[str]) -> float | None:
        """主要菜系在 ``cuisines`` 里的组成每周几餐（每个组成只算一次）；用户确认了菜系而有组成的菜系未知时是未知（None）。"""
        if cuisines and any(p.cuisine is None for p in self.parts):
            return None
        return sum(
            p.meals_per_week for p in self.parts if p.cuisine is not None and p.cuisine in cuisines
        )


class EnjoymentProvision(ContractModel):
    """一项享受型改善：词表里的功能、提供方式与依据。

    * ``passive_service_month``：实际在用的居家舒适用品，按服务月提供（放着也在用，例如一把好椅子）；
    * ``service_units``：享受型服务，每月提供 ``units_per_month`` 份（例如每月两次按摩）。

    这里只有“提供了什么”；提供一份能带来多少满足在评分规格里。同一商品提供两种改善要分成两条、各写依据。"""

    function: KeyStr
    mode: EnjoymentMode
    units_per_month: Annotated[float, Field(gt=0)] | None = None
    unit_label: str | None = Field(default=None, max_length=40)
    basis: str = Field(min_length=1, max_length=280)

    @model_validator(mode="after")
    def _units_match_mode(self) -> EnjoymentProvision:
        if (self.mode == "service_units") != (self.units_per_month is not None):
            raise ValueError(f"{self.function}: 只有按服务量提供的写 units_per_month，且必须写")
        if (self.units_per_month is None) != (self.unit_label is None):
            raise ValueError(f"{self.function}: 服务量与它的单位要一起写")
        return self


class EquipmentFacts(ContractModel):
    """活动装备：持有并在服务期内时提供哪些活动能力（例如 ``guitar_playable``）。

    装备本身不带来娱乐满足：它只让需要这种能力的活动可以开展；当前价值来自实际参加的活动，未来价值来自期末参考生活里的
    可用性。活动条件引用能力，不绑死某一个商品编号。"""

    provides: tuple[KeyStr, ...] = Field(min_length=1)
    note: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique(self) -> EquipmentFacts:
        if len(set(self.provides)) != len(self.provides):
            raise ValueError("装备能力重复")
        return self


class ActivityFacts(ContractModel):
    """一项休闲活动的一次参与：主要类别（兴趣匹配只用它）、辅助标签（只检索与说明）、参与与其他占用的小时、需要的装备能力、
    套餐本次提供的装备能力。

    每次占用 = 参与小时 + 准备、往返、排队的小时（必须等于资源事实里的每次小时）；只有参与小时进入休闲评价。套餐提供的
    装备只对这一次活动有效，不会变成持有物。``replaces_meals_per_session`` **必须写**：0 是“确定不替代正餐”；大于 0（同时替代
    正餐的套餐）本轮不支持，明确拒绝——不写不会被当成 0，免得没有说明的品鉴套餐悄悄进来。"""

    primary_category: KeyStr
    tags: tuple[KeyStr, ...] = ()
    participation_hours: Annotated[float, Field(gt=0, le=24)]
    overhead_hours: Annotated[float, Field(ge=0, le=24)]
    requires: tuple[KeyStr, ...] = ()
    kit_provides: tuple[KeyStr, ...] = ()
    replaces_meals_per_session: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    time_basis: str = Field(min_length=1, max_length=280)

    @model_validator(mode="after")
    def _consistent(self) -> ActivityFacts:
        for name in ("tags", "requires", "kit_provides"):
            seq = getattr(self, name)
            if len(set(seq)) != len(seq):
                raise ValueError(f"活动的 {name} 重复")
        if self.primary_category in self.tags:
            raise ValueError("主要类别不要再写进辅助标签（匹配只算一次）")
        if self.replaces_meals_per_session > 0:
            raise ValueError(
                "同时替代正餐的活动套餐本轮不支持：餐食覆盖、费用与时间的归属没有组合规则，不临场猜抵扣"
            )
        return self

    def missing_capabilities(self, held: frozenset[str]) -> tuple[str, ...]:
        """这一次参与还缺哪些装备能力（套餐本次提供的与已持有的都算有）。"""
        have = held | frozenset(self.kit_provides)
        return tuple(c for c in self.requires if c not in have)


class AreaDecl(ContractModel):
    """城市内部的一个片区（DECISIONS/0042）：住处所在的居住片区，或岗位所在的就业片区。

    片区是资料的地理单位（例如 ACS 的 PUMA、按工作地 PUMA 合成的就业片区），不是街区或楼栋；它只用来把住处、岗位、地区条件
    与通勤关系对上。片区本身不加分：地区的安全、环境读评分条件里这个片区的值，通勤读关系表。"""

    area_id: IdStr
    label: str = Field(min_length=1, max_length=120)
    kind: Literal["home", "work", "home_work"]
    note: str = Field(min_length=1, max_length=280)
    #: 居住片区的居民平时上班往返多少分钟（资料或估计）。交通评分在这个月没有到岗时读它：不上班也有出行，住得偏的地方
    #: 出门更远。只写给居住片区；没有就是未知（不当 0）。
    typical_roundtrip_minutes: Annotated[int, Field(ge=0, le=600)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    typical_source: SourceRecord | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _typical_consistent(self) -> AreaDecl:
        if (self.typical_roundtrip_minutes is None) != (self.typical_source is None):
            raise ValueError(f"{self.area_id}: 居民平时的往返分钟与它的来源要一起写")
        if self.typical_roundtrip_minutes is not None and self.kind == "work":
            raise ValueError(f"{self.area_id}: 就业片区没有“居民平时的往返分钟”")
        return self


#: 通勤方式（DECISIONS/0042）。固定顺序也是分钟与花费都相同时的取舍顺序。
CommuteMode = Literal["walk_bike", "transit", "drive"]
COMMUTE_MODES: tuple[str, ...] = ("walk_bike", "transit", "drive")
COMMUTE_MODE_LABEL: dict[str, str] = {
    "walk_bike": "步行或骑车",
    "transit": "公共交通",
    "drive": "开车",
}


def _commute_money(v: Money, what: str) -> Money:
    _finite(v.amount, what)
    if v.amount < 0:
        raise ValueError(f"{what}不能为负")
    require_life_numeraire(v.numeraire, what)
    return v


class CommuteLeg(ContractModel):
    """通勤关系表的一格（DECISIONS/0042）：住在一个居住片区、到一个就业片区上班、用一种方式走一次往返。

    分钟与每次往返的花费都是片区级的资料或估计（某类居民的实际通勤），不是某套房到某个工位的路线模拟；``source`` 写明性质与
    依据。同一格只写一条；表里没有的组合就是这种方式在这条线上不可用（不当 0 分钟）。公交时间与开车时间各是各的格。"""

    home_area: IdStr
    work_area: IdStr
    mode: CommuteMode
    roundtrip_minutes: Annotated[int, Field(ge=0, le=600)]
    #: 一次往返的花费：公交是两次单程票价，开车是油费估计（用车权本身另是一项按月付费的机会），步行骑车是 0。
    cost_per_roundtrip: Money
    source: SourceRecord

    @field_validator("cost_per_roundtrip")
    @classmethod
    def _cost_ok(cls, v: Money) -> Money:
        return _commute_money(v, "每次往返的花费")


class CommuteModeRule(ContractModel):
    """一种通勤方式的使用条件与月度封顶（DECISIONS/0042）。"""

    mode: CommuteMode
    #: 这个月要有在用的、提供这项功能的机会（例如开车要“用车权”）才能用这种方式；不写就是没有条件。
    requires_function: KeyStr | None = None
    #: 一个月里这种方式的往返花费合计的上限（月票或票价封顶）；不写就是不封顶。
    monthly_cap: Money | None = None
    note: str = Field(min_length=1, max_length=400)
    source: SourceRecord

    @field_validator("monthly_cap")
    @classmethod
    def _cap_ok(cls, v: Money | None) -> Money | None:
        return None if v is None else _commute_money(v, "月度封顶")


class CommuteRelation(ContractModel):
    """一座城的通勤关系（DECISIONS/0042）：方式的条件与片区之间的往返。

    选择规则 ``fastest_available_v1``：每个到岗岗位，在这个月可用的方式里取往返分钟最少的一种；分钟相同取每次花费低的，再相同按
    ``COMMUTE_MODES`` 的顺序。可用 = 关系表里有这一格，并且方式的条件这个月满足。不按偏好挑，不做路线模拟。"""

    choice_rule: Literal["fastest_available_v1"] = "fastest_available_v1"
    modes: tuple[CommuteModeRule, ...] = Field(min_length=1)
    legs: tuple[CommuteLeg, ...] = Field(min_length=1)
    note: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _relation_consistent(self) -> CommuteRelation:
        modes = [m.mode for m in self.modes]
        if len(modes) != len(set(modes)):
            raise ValueError("通勤方式的规则重复")
        cells = [(g.home_area, g.work_area, g.mode) for g in self.legs]
        if len(cells) != len(set(cells)):
            raise ValueError("通勤关系表的同一格（住处片区、就业片区、方式）写了不止一条")
        stray = sorted({g.mode for g in self.legs} - set(modes))
        if stray:
            raise ValueError(f"通勤关系表用了没有规则的方式 {stray}")
        return self

    def leg(self, home_area: str, work_area: str, mode: str) -> CommuteLeg | None:
        return next(
            (
                g
                for g in self.legs
                if (g.home_area, g.work_area, g.mode) == (home_area, work_area, mode)
            ),
            None,
        )

    def mode_rule(self, mode: str) -> CommuteModeRule | None:
        return next((m for m in self.modes if m.mode == mode), None)


class LifeOpportunity(ContractModel):
    """一座城市里的一项生活机会：一种商品、方案、服务、培训或工作。

    **类别是数据，不是字段。** 不为某个国家或品牌加固定字段；规格放在 ``spec`` 里，
    类别名在目录里声明。

    可得性三态互不替代：

    * ``available``：有报价（或工作报酬）。零价格合法。
    * ``unavailable``：**确定**没有。保留这条记录，不能删——删了就抹掉了城市的限制。
    * ``unknown``：没有数据。**不能**补成零价，也不能当成没有。
    """

    opportunity_id: IdStr
    category: KeyStr
    label: str = Field(min_length=1, max_length=120)
    spec: dict[KeyStr, Primitive] = Field(default_factory=dict)
    unit: str = Field(min_length=1, max_length=64)
    quality: str = Field(min_length=1, max_length=280)
    availability: Availability
    price: PriceQuote | None = None
    compensation: PriceQuote | None = None  # 工作类：报的薪酬（L1 经济数据，可见）
    occupation: OccupationId | None = None
    eligibility: tuple[str, ...] = ()  # 资格标签；本轮只记录，不求值
    targets: tuple[str, ...] = ()  # 可能影响的状态标签（如 "skill:bookkeeping"）；**不含幅度**
    #: 这项机会满足哪些需求功能（E1-3 增补）：词表在生活输入里声明。评分按功能去重，不按件数。
    functions: tuple[KeyStr, ...] = ()
    #: 方案属性（品牌、档次、菜系、准备方式……）：**只存储，本轮不评价**；评分结果会列为"未评价"。
    attributes: dict[KeyStr, Primitive] = Field(default_factory=dict)
    arrangement_options: dict[KeyStr, tuple[Primitive, ...]] = Field(default_factory=dict)
    duration: DurationSpec
    #: 资源事实（DECISIONS/0020）：每项机会都必须写（零也要显式写）。
    resource: ResourceProfile
    source: SourceRecord
    #: E1-5A：岗位或培训属于哪家雇主（雇主在 ``CareerRules.employers`` 里声明）。没有用到时不序列化。
    employer_id: IdStr | None = Field(default=None, exclude_if=lambda v: v is None)
    #: E1-5A：培训完成后授予哪些能力（能力在 ``CareerRules.abilities`` 里声明）。只有培训可以写。
    grants: tuple[KeyStr, ...] = Field(default=(), exclude_if=lambda v: v == ())
    #: 生活扩展（DECISIONS/0041）的公开事实；没有用到时不序列化（原有机会的内容身份不变）。
    #: 片区（DECISIONS/0042）：住房是住处所在的居住片区，岗位是工作所在的就业片区。没有用到时不序列化。
    area: IdStr | None = Field(default=None, exclude_if=lambda v: v is None)
    food_plan: FoodPlanFacts | None = Field(default=None, exclude_if=lambda v: v is None)
    enjoyment: tuple[EnjoymentProvision, ...] = Field(default=(), exclude_if=lambda v: v == ())
    equipment: EquipmentFacts | None = Field(default=None, exclude_if=lambda v: v is None)
    activity: ActivityFacts | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _life_facets(self) -> LifeOpportunity:
        oid, kind, r = self.opportunity_id, self.duration.kind, self.resource
        if self.food_plan is not None:
            fp = self.food_plan
            if r.basis != "per_service_month" or r.hours is None:
                raise ValueError(f"{oid}: 食品方案的时间按服务月计（per_service_month）")
            if abs(r.hours - (fp.prep_hours_per_month + fp.dining_hours_per_month)) > HOURS_TOL:
                raise ValueError(
                    f"{oid}: 每月 {r.hours:g} 小时 ≠ 准备 {fp.prep_hours_per_month:g} + 外出就餐 {fp.dining_hours_per_month:g}"
                )
            if self.availability == "available" and not fp.covers_basic_month:
                raise ValueError(
                    f"{oid}: 没有覆盖整月基本饮食的方案不能当作可执行的食品方案（几项食材报价或一家餐馆不是完整方案）"
                )
        if self.activity is not None:
            a = self.activity
            if kind != "one_time_experience" or r.basis != "per_occurrence" or r.hours is None:
                raise ValueError(
                    f"{oid}: 休闲活动是按次发生的体验（one_time_experience、per_occurrence）"
                )
            if abs(r.hours - (a.participation_hours + a.overhead_hours)) > HOURS_TOL:
                raise ValueError(
                    f"{oid}: 每次 {r.hours:g} 小时 ≠ 参与 {a.participation_hours:g} + 其他占用 {a.overhead_hours:g}"
                )
            if r.quantity_scales is not True:
                raise ValueError(f"{oid}: 活动的次数必须按数量算（quantity_scales = true）")
            if self.enjoyment:
                raise ValueError(
                    f"{oid}: 同一项体验不同时算休闲活动与享受型服务；确有两种独立改善时拆成两项并各写依据"
                )
        if self.equipment is not None:
            if kind != "durable_good":
                raise ValueError(f"{oid}: 活动装备是持续物品（durable_good）")
            if self.activity is not None:
                raise ValueError(f"{oid}: 装备与活动是两项机会")
        functions = [e.function for e in self.enjoyment]
        if len(functions) != len(set(functions)):
            raise ValueError(f"{oid}: 享受功能重复；同一功能只写一条")
        for e in self.enjoyment:
            if e.mode == "passive_service_month" and kind != "durable_good":
                raise ValueError(f"{oid}: 按服务月提供的舒适用品是持续物品")
            if e.mode == "service_units" and kind not in ("ongoing_service", "one_time_experience"):
                raise ValueError(f"{oid}: 按服务量提供的享受是服务或一次体验")
        return self

    @model_validator(mode="after")
    def _availability_consistent(self) -> LifeOpportunity:
        has = [x for x in (self.price, self.compensation) if x is not None]
        if self.availability == "available":
            if len(has) != 1:
                raise ValueError(
                    f"{self.opportunity_id}: available 必须恰好有 price 或 compensation 之一"
                )
        elif has:
            raise ValueError(
                f"{self.opportunity_id}: {self.availability} 不能带价格——"
                "未知不是零价，确定缺货也没有成交价"
            )
        if self.compensation is not None and self.occupation is None:
            raise ValueError(f"{self.opportunity_id}: 带薪酬的机会必须写 occupation")
        if (
            self.duration.kind == "ongoing_service"
            and self.price is not None
            and self.price.billing == "one_time"
        ):
            raise ValueError(f"{self.opportunity_id}: 持续服务的计费必须是周期性的")
        if len(set(self.functions)) != len(self.functions):
            raise ValueError(f"{self.opportunity_id}: functions 重复")
        if self.grants and self.duration.kind != "training_course":
            raise ValueError(f"{self.opportunity_id}: 只有培训完成后才授予能力（grants）")
        if len(set(self.grants)) != len(self.grants):
            raise ValueError(f"{self.opportunity_id}: grants 重复")
        overlap = sorted(set(self.attributes) & set(self.spec))
        if overlap:
            raise ValueError(
                f"{self.opportunity_id}: {overlap} 同时出现在 spec 与 attributes——可测的规格放 spec，"
                "暂不评价的属性放 attributes"
            )
        for key, values in self.arrangement_options.items():
            if key in RESERVED_OUTCOME_KEYS:
                raise ValueError(f"{self.opportunity_id}: 安排项 {key!r} 是结果字段，不能由行动选")
            if not values:
                raise ValueError(f"{self.opportunity_id}: 安排项 {key!r} 没有可选值")
        return self


class MaterialNeedCoverage(ContractModel):
    """物质维里某项消费需求在这座城市有没有数据支持。

    给"物质维只有部分需求被数据支持"留出表达位置：有一个 goods 总指数，不等于
    住房空间、鲜食、准备时间、耐用品都被支持了。
    """

    need: KeyStr
    status: Literal["supported", "partial", "unsupported"]
    note: str | None = None


class MinimumNeedSpec(ContractModel):
    """一座城市对一项最低生活需求（住房或食品）的**数据声明**。

    * ``baseline_*``：最低合格方案——引用目录里已有的项目与数量。最低生活检查用它的
      月费；分身计划声明了兜底时，执行的也是它。
    * ``qualifying_opportunity_ids``：哪些方案**满足**这项最低需求（须含基线）。分身每月
      选的住房/食品必须在这个名单里，否则那个月没有满足最低需求。

    **最低值由数据给出**：不靠城市名、自然语言标签或 LLM 猜，也不拿规格不同的商品
    标价比出一个"最便宜"。最低生存标准与"想住大房子""想吃好一点"的偏好是两件事。
    """

    need: MinimumNeed
    baseline_opportunity_id: IdStr
    baseline_quantity: Annotated[float, Field(gt=0)] = 1.0
    qualifying_opportunity_ids: tuple[IdStr, ...]
    meets: str = Field(min_length=1, max_length=280)  # 满足什么最低需求

    @model_validator(mode="after")
    def _baseline_qualifies(self) -> MinimumNeedSpec:
        if self.baseline_opportunity_id not in self.qualifying_opportunity_ids:
            raise ValueError(f"{self.need}: 最低方案本身必须在 qualifying_opportunity_ids 里")
        if len(set(self.qualifying_opportunity_ids)) != len(self.qualifying_opportunity_ids):
            raise ValueError(f"{self.need}: qualifying_opportunity_ids 重复")
        return self


#: 需求功能的数量语义（E1-3 增补订正，`DECISIONS/0019` 第十二节）。在生活输入的词表里声明一次：
#:
#: * ``presence``：存在型——有没有通信、洗衣、工作空间；同功能的第二件不再增加满足感；
#: * ``capacity``：容量型——按明确的实际服务容量计量（如升 × 件数），增加容量可以有递减收益。
FunctionKind = Literal["presence", "capacity"]
FunctionSupplyStatus = Literal["available", "unavailable", "unknown"]


class FunctionSupply(ContractModel):
    """一座城对一项需求功能的供给资料是否完整（`DECISIONS/0019` 第十二节）。

    * ``available``：目录里至少有一项**可得**的机会声明了这项功能；
    * ``unavailable``：**确定**没有——目录里可以有标为 unavailable 的条目，但不能有可得或资料未知的；
    * ``unknown``：资料未知或未完成采集。**不能当成没有**：评分时这项功能没有在用的物品，就报未知。

    目录里没有记录不等于确定没有：比较组要评价的功能，每座城都必须写明这三种之一（加载检查）。
    """

    function: KeyStr
    status: FunctionSupplyStatus
    note: str | None = Field(default=None, max_length=280)
    source: SourceRecord


class TaxBracket(ContractModel):
    """年度应税收入的一个税档：``lower`` 以上的部分按 ``rate`` 计。"""

    lower: Annotated[float, Field(ge=0)]
    rate: Annotated[float, Field(ge=0, lt=1)]


class SyntheticTaxPolicy(ContractModel):
    """**合成**税策略的数据声明。只能是测试替身（``synthetic_test_double``）。

    真实税表本轮**不接**：现有 ``worlds.taxes`` 与生活协议的计价口径（币种、基年、当地
    标价还是空间平减收入）**尚未核查**；它的接口也不验证传入金额的口径（结果直接沿用
    传入的标签），口径不符时不会报错。所以本轮暂不接入。真实提供者将来接入时必须先做
    V2 税表核查（币种、基年、当地 vs 平减收入、税辖区、年份、月度预扣与年度汇总），
    **不能**继承合成测试的通过状态——所以这里没有 "verified" 这个状态。

    计算（按档累进）在 ``econ.life_settlement``；这里只有数据形状。
    """

    policy_id: IdStr
    status: Literal["synthetic_test_double"]
    money_basis_id: str
    applies_to: Jurisdiction
    period: Literal["calendar_year"] = "calendar_year"
    withholding: Literal["cumulative_year_to_date"] = "cumulative_year_to_date"
    brackets: tuple[TaxBracket, ...] = Field(min_length=1)
    note: str = Field(min_length=1)

    @model_validator(mode="after")
    def _brackets_ok(self) -> SyntheticTaxPolicy:
        require_life_numeraire(self.money_basis_id, f"税策略 {self.policy_id}")
        lows = [b.lower for b in self.brackets]
        if lows[0] != 0:
            raise ValueError(f"{self.policy_id}: 第一档必须从 0 开始")
        if any(b <= a for a, b in itertools.pairwise(lows)):
            raise ValueError(f"{self.policy_id}: 税档下限必须严格递增")
        return self


class TaxCitation(ContractModel):
    """一条税规则的官方出处（DECISIONS/0042）：网址、取得日期、原始文件的 SHA-256 与原文摘句。构建时核对摘句确实在那份原始
    文件里、规则里的每个数都出现在摘句里（数是读出来的，不是生成的）。"""

    publisher: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1, max_length=400)
    retrieved_on: _dt.date
    raw_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    quote: str = Field(min_length=1, max_length=1000)


class TaxAbsence(ContractModel):
    """官方说明某一级没有个人所得税（DECISIONS/0042）：没有规则也要有出处，不是“没找到就当没有”。"""

    label: str = Field(min_length=1, max_length=120)
    citation: TaxCitation


class TaxRule(ContractModel):
    """一条官方的税或工资扣款规则（DECISIONS/0042），按日历年、对累计工资计：

    * ``income_brackets``：应税收入 = max(0, 累计工资 − ``deduction``)，按 ``brackets`` 累进；
    * ``flat_on_wages``：计税工资 = 累计工资（有 ``wage_cap`` 就只算上限以内；有 ``threshold`` 就只算超过的部分）× ``rate``，
      有 ``annual_max`` 就一年最多扣这么多。

    同一份记录既算预扣，也生成给代理看的说明。"""

    rule_id: IdStr
    label: str = Field(min_length=1, max_length=120)
    level: Literal["federal", "state", "local", "payroll"]
    kind: Literal["income_brackets", "flat_on_wages"]
    deduction: Annotated[float, Field(ge=0)] = 0.0
    brackets: tuple[TaxBracket, ...] = ()
    rate: Annotated[float, Field(ge=0, lt=1)] | None = None
    wage_cap: Annotated[float, Field(gt=0)] | None = None
    threshold: Annotated[float, Field(gt=0)] | None = None
    annual_max: Annotated[float, Field(gt=0)] | None = None
    #: 由摘句换算出来的数怎么来的（例如“每周 0.60 × 52 周”）；直接读出来的不写。
    derivation: str | None = Field(default=None, max_length=280)
    citations: tuple[TaxCitation, ...] = Field(min_length=1)
    note: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def _kind_consistent(self) -> TaxRule:
        if self.kind == "income_brackets":
            if not self.brackets or self.rate is not None:
                raise ValueError(f"{self.rule_id}: 所得税按档计，要写 brackets、不写 rate")
            lows = [b.lower for b in self.brackets]
            if lows[0] != 0 or any(b <= a for a, b in itertools.pairwise(lows)):
                raise ValueError(f"{self.rule_id}: 税档从 0 开始、下限严格递增")
            if any(x is not None for x in (self.wage_cap, self.threshold, self.annual_max)):
                raise ValueError(f"{self.rule_id}: 所得税没有工资上限、起征线或年度上限")
        else:
            if self.rate is None or self.brackets or self.deduction:
                raise ValueError(f"{self.rule_id}: 工资扣款按比例计，要写 rate、不写税档与扣除")
        return self


class RealTaxPolicy(ContractModel):
    """一座城适用的**官方** 2024 年税与工资扣款（DECISIONS/0042）。支持范围只有一种：单身、只有工资收入、按标准扣除、
    在支持的居住地与工作地（``residence_scope``）。没有建模的抵免、附加税与特殊情形逐条写在 ``not_modeled``——它们让结果
    与真实税单有差别，照实说明，不当作已经覆盖。"""

    policy_id: IdStr
    status: Literal["official_2024_rules"]
    money_basis_id: str
    applies_to: Jurisdiction
    tax_year: Literal[2024] = 2024
    filing: Literal["single_wage_earner"] = "single_wage_earner"
    period: Literal["calendar_year"] = "calendar_year"
    withholding: Literal["cumulative_year_to_date"] = "cumulative_year_to_date"
    rules: tuple[TaxRule, ...] = Field(min_length=1)
    #: 官方说明这一城没有的税（例如没有州个人所得税）与出处。
    absent_taxes: tuple[TaxAbsence, ...] = ()
    residence_scope: str = Field(min_length=1, max_length=400)
    not_modeled: tuple[Annotated[str, Field(min_length=1, max_length=280)], ...] = ()
    note: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _rules_ok(self) -> RealTaxPolicy:
        require_life_numeraire(self.money_basis_id, f"税策略 {self.policy_id}")
        ids = [r.rule_id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.policy_id}: 税规则编号重复")
        return self


#: 一座城的税：合成测试替身或官方规则（按 ``status`` 区分）。
TaxPolicy = Annotated[SyntheticTaxPolicy | RealTaxPolicy, Field(discriminator="status")]


class CityLifeCatalog(ContractModel):
    """一座城市的生活机会目录。

    城市身份用完整的 国家/地理体系/代码。只用 ``city.code`` 会让两个国家撞号。
    A/B/C 兼容等级**不在这里**：它是对一次比较集合的判断，不是商品的永久属性。
    """

    city: CityId
    jurisdiction: Jurisdiction
    label: str = Field(min_length=1, max_length=120)
    money_basis: MoneyBasis
    opportunities: tuple[LifeOpportunity, ...]
    price_indices: tuple[PriceIndex, ...] = ()
    material_coverage: tuple[MaterialNeedCoverage, ...] = ()
    #: 最低住房与食品的数据声明（E1-2）。缺了不影响结构有效，但这座城**不能结算**。
    minimum_living: tuple[MinimumNeedSpec, ...] = ()
    #: 城市内部的片区（DECISIONS/0042）。机会写了 ``area`` 就必须在这里声明；没有用到时不序列化。
    areas: tuple[AreaDecl, ...] = Field(default=(), exclude_if=lambda v: v == ())
    #: 通勤关系（DECISIONS/0042）：住处片区 × 就业片区 × 方式。有需要到岗的岗位就必须有（加载器检查）。
    commute: CommuteRelation | None = Field(default=None, exclude_if=lambda v: v is None)
    #: 这座城适用的合成税策略。缺了**不能结算**——没有配置不等于零税。
    tax_policy_id: IdStr | None = None
    #: E1-3 增补订正：各项需求功能的供给资料是否完整。目录里没有记录不等于确定没有。
    function_supply: tuple[FunctionSupply, ...] = ()
    #: E1-5C：治疗目录（小病、大病各一条；可得 / 不可得 / 未知）。不是普通生活机会，不能写进年度行动；只由年度计划的
    #: 应急安排在生病的月份按规则使用。没有用到时不序列化——旧目录的输出与指纹逐字节不变。
    treatments: tuple[TreatmentOffer, ...] = Field(default=(), exclude_if=lambda v: v == ())

    @model_validator(mode="after")
    def _treatments_consistent(self) -> CityLifeCatalog:
        if not self.treatments:
            return self
        ids = [t.treatment_id for t in self.treatments]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.city}: 治疗条目 ID 重复")
        clash = sorted(set(ids) & {o.opportunity_id for o in self.opportunities})
        if clash:
            raise ValueError(
                f"{self.city}: 治疗条目 {clash} 与生活机会同名——现金流按 ID 归属，不能重名"
            )
        sev = [t.severity for t in self.treatments]
        if len(sev) != len(set(sev)):
            raise ValueError(f"{self.city}: 同一种病情只能有一条治疗条目")
        want = self.money_basis.numeraire_id
        digits = self.money_basis.minor_unit_digits
        for t in self.treatments:
            if t.price is None:
                continue
            if t.price.amount.numeraire != want:
                raise ValueError(
                    f"{self.city}/{t.treatment_id}: 金额口径 {t.price.amount.numeraire} 与目录口径 {want} 不一致"
                )
            if exact_minor_units(t.price.amount.amount, digits) is None:
                raise ValueError(
                    f"{self.city}/{t.treatment_id}: 报价 {t.price.amount.amount!r} 的精度细于最小记账单位"
                )
        return self

    def treatment(self, severity: str) -> TreatmentOffer | None:
        return next((t for t in self.treatments if t.severity == severity), None)

    @model_validator(mode="after")
    def _internally_consistent(self) -> CityLifeCatalog:
        if self.jurisdiction.country != self.city.country:
            raise ValueError(f"{self.city}: 税区国家 {self.jurisdiction.country} 与城市国家不一致")
        ids = [o.opportunity_id for o in self.opportunities]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"{self.city}: 机会 ID 重复 {dup}")
        want = self.money_basis.numeraire_id
        digits = self.money_basis.minor_unit_digits
        for o in self.opportunities:
            for q in (o.price, o.compensation):
                if q is not None and q.amount.numeraire != want:
                    raise ValueError(
                        f"{self.city}/{o.opportunity_id}: 金额口径 {q.amount.numeraire} "
                        f"与目录口径 {want} 不一致——不同口径的钱不能进同一本账"
                    )
                if q is not None and exact_minor_units(q.amount.amount, digits) is None:
                    raise ValueError(
                        f"{self.city}/{o.opportunity_id}: 报价 {q.amount.amount!r} 的精度细于"
                        f"最小记账单位（{digits} 位小数）"
                    )
        idx = [p.index_id for p in self.price_indices]
        if len(idx) != len(set(idx)):
            raise ValueError(f"{self.city}: 价格指数 ID 重复")
        needs = [c.need for c in self.material_coverage]
        if len(needs) != len(set(needs)):
            raise ValueError(f"{self.city}: material_coverage 的需求重复")
        mins = [m.need for m in self.minimum_living]
        if len(mins) != len(set(mins)):
            raise ValueError(f"{self.city}: minimum_living 的需求重复")
        funcs = [f.function for f in self.function_supply]
        if len(funcs) != len(set(funcs)):
            raise ValueError(f"{self.city}: function_supply 的功能重复")
        kinds = {a.area_id: a.kind for a in self.areas}
        if len(kinds) != len(self.areas):
            raise ValueError(f"{self.city}: 片区编号重复")
        for o in self.opportunities:
            if o.area is None:
                continue
            kind = kinds.get(o.area)
            if kind is None:
                raise ValueError(
                    f"{self.city}/{o.opportunity_id}: 片区 {o.area} 没有在 areas 里声明"
                )
            if o.category == "housing" and kind == "work":
                raise ValueError(f"{self.city}/{o.opportunity_id}: 住房写的 {o.area} 是就业片区")
            if o.occupation is not None and kind == "home":
                raise ValueError(f"{self.city}/{o.opportunity_id}: 岗位写的 {o.area} 是居住片区")
        if self.commute is not None:
            for g in self.commute.legs:
                for area, bad, what in (
                    (g.home_area, "work", "住处"),
                    (g.work_area, "home", "工作地"),
                ):
                    kind = kinds.get(area)
                    if kind is None:
                        raise ValueError(
                            f"{self.city}: 通勤关系表的{what}片区 {area} 没有在 areas 里声明"
                        )
                    if kind == bad:
                        raise ValueError(f"{self.city}: 通勤关系表把 {area} 当{what}片区，它不是")
                q = g.cost_per_roundtrip
                if q.numeraire != want or exact_minor_units(q.amount, digits) is None:
                    raise ValueError(
                        f"{self.city}: 通勤 {g.home_area}→{g.work_area}（{g.mode}）的花费口径或精度不对"
                    )
            for r in self.commute.modes:
                if r.monthly_cap is not None and (
                    r.monthly_cap.numeraire != want
                    or exact_minor_units(r.monthly_cap.amount, digits) is None
                ):
                    raise ValueError(f"{self.city}: {r.mode} 的月度封顶口径或精度不对")
        return self

    def area(self, area_id: str) -> AreaDecl | None:
        return next((a for a in self.areas if a.area_id == area_id), None)

    def supply_for(self, function: str) -> FunctionSupply | None:
        return next((f for f in self.function_supply if f.function == function), None)

    def minimum_spec(self, need: str) -> MinimumNeedSpec | None:
        return next((m for m in self.minimum_living if m.need == need), None)

    def opportunity(self, opportunity_id: str) -> LifeOpportunity | None:
        return next((o for o in self.opportunities if o.opportunity_id == opportunity_id), None)


# ==========================================================================
# 用户陈述里的需求条件（六维产品画像 ``common.life_product_profile.ProductLifeProfile`` 用）
# ==========================================================================


class SoftPreference(ContractModel):
    """软偏好：用户说自己看重什么、看重到什么程度（序数，原话口径）。"""

    need: KeyStr
    stated_strength: Literal["low", "medium", "high"]
    note: str | None = Field(default=None, max_length=280)


class Substitute(ContractModel):
    """一种可接受的替代：满足这些条件就算满足了硬要求。"""

    description: str = Field(min_length=1, max_length=200)
    satisfies_with: dict[KeyStr, Primitive]


class HardRequirement(ContractModel):
    """硬要求：不满足就不行。可以列出可接受的替代。

    **条件词表（E1-3，`DECISIONS/0018`）。** ``requirement`` 与每个替代的 ``satisfies_with``
    都是一组"该月实际在用的某一项"必须同时满足的条件：

    * ``category``：项目类别相等（**必须给**——没有它就不知道该看哪类项目，判为无法判断）；
    * ``min_<键>`` / ``max_<键>``：规格里该键的数值 ≥ / ≤ 给定值；
    * 其他 ``<键>``：规格里该键等于给定值。

    规格里缺了要比较的键，就是"数据不足无法判断"，不当成不满足，也不当成满足。
    ``description`` 之类的文字**从不解析**。``scope`` 本轮只有"每个月都要满足"一种。
    硬要求单独报告，不进分数、不触发出局、不额外扣分。
    """

    need: KeyStr
    requirement: dict[KeyStr, Primitive]
    acceptable_substitutes: tuple[Substitute, ...] = ()
    scope: Literal["every_month"] = "every_month"

    @field_validator("requirement")
    @classmethod
    def _non_empty(cls, v: dict[str, Primitive]) -> dict[str, Primitive]:
        if not v:
            raise ValueError("硬要求不能是空条件")
        return v


# ==========================================================================
# 个人年度状态
# ==========================================================================


class Holding(ContractModel):
    """持有物：一件持续物品，或一段已预付、尚未用完的服务覆盖。

    **剩余期限用月表示**（``remaining_months``），年中购买不会凭空扣掉一整年寿命。
    E1-1 的 ``remaining_years`` 仍可读：它是"某年年初还剩几整年"，按 ×12 换成月，
    无信息损失（E1-1 没有年中购买）。两者只能填一个；结算写出的新状态一律用月。

    ``in_service_from_year`` 只用于 ``next_year`` 生效、尚未开始服务的物品。
    一次体验**不是**持有物；培训进度在 ``TrainingProgress``。
    """

    opportunity_id: IdStr
    quantity: Annotated[float, Field(gt=0)]
    acquired_year: Annotated[int, Field(ge=0, le=MAX_SIMULATION_YEARS)]  # 0 = 开局时已持有
    acquired_month: Month | None = None  # 开局持有物为 None
    remaining_years: Annotated[int, Field(ge=0)] | None = None  # E1-1 旧字段，整年
    remaining_months: Annotated[int, Field(ge=0)] | None = None  # E1-2 规范字段
    #: 第 N 年买下、``next_year`` 生效的物品，会在终态里带着 N + 1 出现，所以上限多一年。
    in_service_from_year: Annotated[int, Field(ge=1, le=MAX_SIMULATION_YEARS + 1)] | None = None

    @model_validator(mode="after")
    def _one_period_field(self) -> Holding:
        if self.remaining_years is not None and self.remaining_months is not None:
            raise ValueError(
                f"{self.opportunity_id}: remaining_years 与 remaining_months 只能填一个"
            )
        return self

    @property
    def effective_remaining_months(self) -> int | None:
        """规范的剩余月数。旧字段按 ×12 换算；两者都没有则为 None（不适用）。"""
        if self.remaining_months is not None:
            return self.remaining_months
        if self.remaining_years is not None:
            return MONTHS_PER_YEAR * self.remaining_years
        return None


class TrainingProgress(ContractModel):
    """一门培训的可延续进度。**本轮只记录**已付费用与实际参与的月份，不增加技能、
    工资或满足感（培训收益属于 E1-5）。"""

    opportunity_id: IdStr
    months_participated: Annotated[int, Field(ge=0)]
    months_required: Annotated[int, Field(ge=1)]
    fee_paid: bool
    enrolled_year: YearIndex
    enrolled_month: Month
    #: E1-5A：完成的年月（只在新协议写；没有用到时不序列化）。
    completed_year: YearIndex | None = Field(default=None, exclude_if=lambda v: v is None)
    completed_month: Month | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _completion_consistent(self) -> TrainingProgress:
        if (self.completed_year is None) != (self.completed_month is None):
            raise ValueError(f"{self.opportunity_id}: 完成年与完成月要一起写")
        if self.completed_year is not None and not self.completed:
            raise ValueError(
                f"{self.opportunity_id}: 还没参加够 {self.months_required} 个月，不能写完成"
            )
        return self

    @property
    def completed(self) -> bool:
        return self.months_participated >= self.months_required


class ExitRecord(ContractModel):
    """分身出局的记录：在第几年第几月、因为什么、差多少钱、当时有多少现金。

    ``unaffordable_needs`` 按"先住房后食品"的约定归因：现金先算给最低住房，剩下的
    再算给最低食品，没被覆盖的项就是"缺的"。两项最低费用都原样保存，别的归因
    方式可以据此重算。**出局时的满足感分数本轮不定，不填零**（E1-6）。
    """

    year_index: YearIndex
    month: Month
    reason: Literal["minimum_living_unaffordable"]
    cash_available: Money
    minimum_housing_cost: Money
    minimum_food_cost: Money
    shortfall: Money
    unaffordable_needs: tuple[MinimumNeed, ...] = Field(min_length=1)
    allocation_rule: Literal["housing_then_food"] = "housing_then_food"

    @model_validator(mode="after")
    def _consistent(self) -> ExitRecord:
        bases = {
            m.numeraire
            for m in (
                self.cash_available,
                self.minimum_housing_cost,
                self.minimum_food_cost,
                self.shortfall,
            )
        }
        if len(bases) != 1:
            raise ValueError(f"出局记录里的金额口径不一致：{sorted(bases)}")
        require_life_numeraire(next(iter(bases)), "出局记录")
        if self.shortfall.amount <= 0:
            raise ValueError("出局的资金缺口必须为正；现金恰好够用时应当通过")
        return self


class EnergyQuotaBasis(ContractModel):
    """这一年月度精力额度的来由（E1-4B，DECISIONS/0021）。只有事实与身份，**不含满足感分数**。

    ``base_level`` 是个人固定的基础额度，``EnergyState.level`` 是当前行动年实际使用的额度：

    =====================  ============================================================
    base_first_year        第一年：额度就是基础值（没有虚构的第零年满意度）
    feedback_applied       由上一年（``source_year``）完整满意度按反馈政策算出，≥ 基础值
    feedback_off           反馈关闭或 α = 0：额度就是基础值
    pending_feedback       结算刚产生的候选状态：下一年额度**还没确定**，不能结算，也不能构造观察
    =====================  ============================================================

    ``record_fingerprint`` 指向内部反馈记录（SHA-256）；分数、α 与计算过程在记录里，不在状态里。

    ``policy_fingerprint`` 是确定这份额度的反馈政策的**内容**指纹（``EnergyFeedbackPolicy.content_fingerprint``），
    ``scoring_content_fingerprint`` 是确定它的评分规格的**内容**身份（DECISIONS/0021 第十三节、0025）。绑定时点：第一年状态不绑定
    （额度就是基础值，任何政策下都一样，所以合法的实验臂可以从同一个初始状态分别开始）；结算产生候选状态时绑定当时协议的政策；
    交接写入正式的下一年状态时沿用反馈记录里的政策。之后加载、结算与观察都按同一份检查（``energy_quota_problems``）核对：编号、
    版本、政策内容或评分内容任何一项不同都拒绝。
    """

    base_level: Annotated[float, Field(gt=0)]
    status: Literal["base_first_year", "feedback_applied", "feedback_off", "pending_feedback"]
    policy_id: IdStr | None = None
    policy_version: str | None = None
    source_year: YearIndex | None = None
    record_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    policy_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    scoring_content_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def _shape(self) -> EnergyQuotaBasis:
        if self.status == "base_first_year":
            if any(
                x is not None
                for x in (
                    self.policy_id,
                    self.policy_version,
                    self.source_year,
                    self.record_fingerprint,
                    self.policy_fingerprint,
                    self.scoring_content_fingerprint,
                )
            ):
                raise ValueError(
                    "第一年的额度就是基础值：没有反馈政策、来源年、政策内容、评分内容或反馈记录"
                )
            return self
        if self.policy_id is None or self.policy_version is None or self.source_year is None:
            raise ValueError(f"{self.status} 必须写明反馈政策、版本与来源年")
        if self.policy_fingerprint is None or self.scoring_content_fingerprint is None:
            raise ValueError(
                f"{self.status} 必须写明确定这份额度的政策内容指纹（policy_fingerprint）与评分内容身份"
                "（scoring_content_fingerprint）"
            )
        if (self.status == "pending_feedback") != (self.record_fingerprint is None):
            raise ValueError("只有已确定的额度（反馈已算或已关闭）才指向反馈记录；候选状态没有记录")
        return self


class EnergyState(ContractModel):
    """精力：独立的活动负担约束。**与时间不是同一个量。**

    ``level`` 是每个月可持续承受的活动负担额度（负担点，DECISIONS/0020），必须为正；同一年的 12 个月不变。固定的是基础额度
    （``PersonYearState.energy_quota_basis``），当前额度从第二年起可由上一年满足感在 [基础, 基础 × (1 + α)] 内确定，因城而异
    （DECISIONS/0021）。
    """

    level: Annotated[float, Field(gt=0)]
    scale_version: Literal["e1-4a-monthly-quota-v1"]


LifeStatus = Literal["active", "terminal", "exited", "stopped"]


class PersonYearState(ContractModel):
    """一个分身的状态。跨年延续：余额、物品、技能、职业、目标、精力与有限经历。

    ``status`` 决定它是什么时刻的快照：

    * ``active``：第 ``year_index`` 年**年初**的状态（1 ≤ year_index ≤ N），可以接收行动；
    * ``terminal``：正常模拟结束后的终态，``year_index = N + 1``，**不能再接收行动**；
    * ``exited``：出局时刻的状态，``year_index`` 是出局所在年，``exit`` 记年月与缺口。
      提前出局**不**被统一改写成第 N+1 年，也不再继续结算。
    * ``stopped``（E1-5C）：疾病之后声明的应急规则仍满足不了时间或精力、或要用的资料缺失，模拟在 ``stop`` 记录的那个月
      停下；状态是那个月之前最后一个完整月份月末的样子，之前的生活全部保留。这是**模拟器的支持边界**，不是"现实中活不
      下去"，也不是出局；不再继续结算（正式重试在 E1-7）。

    "终态编号等于 N + 1" 依赖协议的 N，由加载器与结算器检查。

    **这个对象整体不能送进 prompt。** 可见部分由 ``agentlayer.life_views`` 逐字段投影。
    """

    person_id: IdStr
    clone_id: IdStr
    city: CityId
    year_index: Annotated[int, Field(ge=1, le=MAX_SIMULATION_YEARS + 1)]
    status: LifeStatus = "active"
    exit: ExitRecord | None = None
    #: E1-5C：停止记录（只在 ``stopped`` 状态）。没有用到时不序列化。
    stop: StopRecord | None = Field(default=None, exclude_if=lambda v: v is None)
    occupation: OccupationId | None = None
    money_basis_id: str
    cash: Money
    holdings: tuple[Holding, ...] = ()
    time_budget_hours: Annotated[float, Field(gt=0, le=HOURS_PER_YEAR_MAX)]
    energy: EnergyState
    #: 资源预算的依据（预留了什么、来源）：``time_budget_hours`` 是年度净可分配小时，``energy.level`` 是每月可承受负担额度。
    resource_budget_basis: ResourceBudgetBasis
    #: 固定的个人基础额度与当前额度的来由（DECISIONS/0021）。
    energy_quota_basis: EnergyQuotaBasis
    #: 已获得的能力（来源、完成年月、可用起始年）与实际岗位记录（DECISIONS/0023）。``occupation`` 是岗位记录的兼容摘要
    #: （见 ``EmploymentRecord.occupation_summary``），用户开局陈述的职业在 ``employment.stated_occupation``。
    abilities: tuple[HeldAbility, ...] = ()
    employment: EmploymentRecord
    training: tuple[TrainingProgress, ...] = ()
    #: 结构化资金目标（含已结束的，保留历史；DECISIONS/0026）。下面的 ``goals`` 是文字目标，不被解析成资金目标。
    savings_goals: tuple[SavingsGoal, ...] = ()
    #: 健康状态（年龄、重复序号、正在延续的疾病、病史、上月工作相对负担、绑定；DECISIONS/0027）。
    health: HealthState
    goals: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = ()
    experience_summary: tuple[
        Annotated[str, Field(min_length=1, max_length=MAX_SUMMARY_CHARS)], ...
    ] = ()

    @model_validator(mode="after")
    def _consistent(self) -> PersonYearState:
        require_life_numeraire(self.money_basis_id, f"{self.clone_id} 的账户")
        if self.cash.numeraire != self.money_basis_id:
            raise ValueError(
                f"{self.clone_id}: 现金口径 {self.cash.numeraire} 与账户口径 "
                f"{self.money_basis_id} 不一致"
            )
        _finite(self.cash.amount, "现金")
        if len(self.goals) > MAX_GOALS:
            raise ValueError(f"目标最多 {MAX_GOALS} 条")
        if len(self.experience_summary) > MAX_SUMMARY_ENTRIES:
            raise ValueError(f"经历摘要最多 {MAX_SUMMARY_ENTRIES} 条，避免成本随年数增长")
        ids = [h.opportunity_id for h in self.holdings]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.clone_id}: 持有物重复；数量用 quantity 表达")
        tids = [t.opportunity_id for t in self.training]
        if len(tids) != len(set(tids)):
            raise ValueError(f"{self.clone_id}: 培训进度重复")
        if (self.status == "exited") != (self.exit is not None):
            raise ValueError(f"{self.clone_id}: 只有出局状态才带出局记录，出局状态必须带")
        if (self.status == "stopped") != (self.stop is not None):
            raise ValueError(f"{self.clone_id}: 只有停止状态才带停止记录，停止状态必须带")
        if self.stop is not None and self.stop.year_index != self.year_index:
            raise ValueError(f"{self.clone_id}: 停止记录的年份与状态年份不一致")
        q = self.energy_quota_basis
        level, base = to_fraction(self.energy.level), to_fraction(q.base_level)
        if q.status in ("base_first_year", "feedback_off") and level != base:
            raise ValueError(
                f"{self.clone_id}: {q.status} 时当前额度必须等于基础额度 {q.base_level}，得到 "
                f"{self.energy.level}"
            )
        if level < base:
            raise ValueError(
                f"{self.clone_id}: 当前额度 {self.energy.level} 低于基础额度 {q.base_level}"
            )
        if self.status == "active":
            if q.status == "base_first_year" and self.year_index != 1:
                raise ValueError(
                    f"{self.clone_id}: 第 {self.year_index} 年不能写 base_first_year——第二年起的额度"
                    "要么由反馈算出，要么由关闭的反馈定为基础值"
                )
            if q.source_year is not None and q.source_year != self.year_index - 1:
                raise ValueError(
                    f"{self.clone_id}: 第 {self.year_index} 年的额度只能来自第 {self.year_index - 1} 年，"
                    f"得到第 {q.source_year} 年"
                )
        held = [a.ability_id for a in self.abilities]
        dup = sorted({x for x in held if held.count(x) > 1})
        if dup:
            raise ValueError(f"{self.clone_id}: 能力 {dup} 重复——同一能力只算一次，不叠加等级")
        if self.status == "active":
            early = [a.ability_id for a in self.abilities if a.usable_from_year > self.year_index]
            if early:
                raise ValueError(
                    f"{self.clone_id}: 第 {self.year_index} 年年初还不能用 {early}（下一年生效规则）"
                )
        summary = self.employment.occupation_summary()
        if self.occupation != summary:
            raise ValueError(
                f"{self.clone_id}: occupation 必须是当前岗位的兼容摘要 {summary}（没有岗位或职业不止一个时"
                f"为空），得到 {self.occupation}"
            )
        ids = [g.goal_id for g in self.savings_goals]
        dup = sorted({x for x in ids if ids.count(x) > 1})
        if dup:
            raise ValueError(f"{self.clone_id}: 目标 ID {dup} 重复——目标 ID 全史唯一")
        if sum(1 for g in self.savings_goals if g.open) > 1:
            raise ValueError(f"{self.clone_id}: 同一时刻只能有一个未结束的资金目标")
        stopped_in_year = self.status in ("exited", "stopped")
        latest = self.year_index if stopped_in_year else self.year_index - 1
        for g in self.savings_goals:
            if g.money_basis_id != self.money_basis_id:
                raise ValueError(f"{self.clone_id}: 目标 {g.goal_id} 的口径与账户不一致")
            if g.created_year > latest or any(e.year_index > latest for e in g.events):
                raise ValueError(
                    f"{self.clone_id}: 目标 {g.goal_id} 有晚于第 {latest} 年的事件——第 {self.year_index} 年"
                    "年初的状态不可能已经记下当年的事"
                )
            if not stopped_in_year and g.status == "active" and g.target_year < self.year_index:
                raise ValueError(
                    f"{self.clone_id}: 目标 {g.goal_id} 的目标年 {g.target_year} 已过却仍标进行中——到期未兑现"
                    "要记下来（逾期）"
                )
        if self.exit is not None:
            if self.exit.year_index != self.year_index:
                raise ValueError(f"{self.clone_id}: 出局记录的年份与状态年份不一致")
            if self.exit.cash_available.numeraire != self.money_basis_id:
                raise ValueError(f"{self.clone_id}: 出局记录的口径与账户不一致")
        return self


def energy_quota_problems(
    policy: EnergyFeedbackPolicy, state: PersonYearState
) -> list[tuple[str, str]]:
    """一个行动年年初状态的精力额度，能不能在这份反馈政策下使用（DECISIONS/0021 第十三节、0025）。

    加载与结算前检查（``ledger.life_catalog.feedback_state_problems``）、可见投影（``agentlayer.life_views``）
    与反馈函数（``econ.life_feedback``）共用这一份判断，返回 (问题代码, 说明)：

    * ``energy_quota_pending``：结算刚产生的候选状态（额度待定），要先完成年度交接；
    * ``feedback_policy_switched``：额度由另一个编号或版本的政策确定；
    * ``feedback_policy_content_changed``：编号、版本相同，但政策内容（开关、α、公式、评分来源……）变了——
      不沿用旧加成，也不静默恢复基础额度；
    * ``scoring_content_changed``：额度由另一套评分内容确定（编号与版本可能完全相同）；
    * ``energy_quota_not_base``：政策关闭或 α = 0，当前额度却不是基础值；
    * ``energy_quota_over_bound``：当前额度超出 ``基础 × (1 + α)``。

    第一年状态（``base_first_year``）不绑定政策，只检查额度范围。终态与出局状态不再接受行动，不在这里检查。
    这些都是**输入错误**，不是分身的生活不可行。
    """
    q = state.energy_quota_basis
    if state.status != "active":
        return []
    who = f"{state.clone_id} 第 {state.year_index} 年"
    if q.status == "pending_feedback":
        return [
            (
                "energy_quota_pending",
                f"{who}的精力额度还没确定（上一年的满足感反馈没有完成）——这是结算刚产生的候选状态，要先用"
                "年度交接（econ.life_year.run_life_year）确定额度",
            )
        ]
    out: list[tuple[str, str]] = []
    if q.status != "base_first_year":
        if (q.policy_id, q.policy_version) != (policy.policy_id, policy.version):
            out.append(
                (
                    "feedback_policy_switched",
                    f"{who}的额度由反馈政策 {q.policy_id}（{q.policy_version}）确定，协议现在是 "
                    f"{policy.policy_id}（{policy.version}）——不中途换政策；对照臂要从相同初始状态分别运行",
                )
            )
        if q.policy_fingerprint != policy.content_fingerprint:
            out.append(
                (
                    "feedback_policy_content_changed",
                    f"{who}的额度由内容指纹 {str(q.policy_fingerprint)[:12]}… 的政策确定，当前政策的内容指纹是 "
                    f"{policy.content_fingerprint[:12]}…：开关、α、公式或评分来源等内容不同（编号可能相同）。"
                    "不沿用旧加成，也不静默恢复基础额度；要比较不同政策，请从相同初始状态分别运行",
                )
            )
        if q.scoring_content_fingerprint != policy.scoring_content_fingerprint:
            out.append(
                (
                    "scoring_content_changed",
                    f"{who}的额度来自另一套评分内容：来源评分内容 {q.scoring_content_fingerprint}；当前政策声明的评分内容 "
                    f"{policy.scoring_content_fingerprint}"
                    "（评分规格编号与版本可能相同）。不沿用旧加成、不恢复基础额度、不关闭反馈、不猜旧状态的来源；"
                    "要比较不同评分参数，请从相同初始状态分别运行",
                )
            )
    level, base = to_fraction(state.energy.level), to_fraction(q.base_level)
    if not policy.effective and level != base:
        out.append(
            (
                "energy_quota_not_base",
                f"{who}：反馈政策关闭或 α = 0，额度只能是基础值 {q.base_level}，得到 {state.energy.level}"
                "——关闭反馈的运行不能消费开启反馈产生的加成",
            )
        )
    if level > base * (1 + to_fraction(policy.alpha)):
        out.append(
            (
                "energy_quota_over_bound",
                f"{who}：当前额度 {state.energy.level} 超过政策上限 {q.base_level} × (1 + {policy.alpha})",
            )
        )
    return out


def health_rules_state_problems(
    rules: HealthRules, state: PersonYearState
) -> list[tuple[str, str]]:
    """E1-5C：一个状态的健康部分是不是由这一套疾病规则产生、能不能在它下面继续（DECISIONS/0027 收尾修订）。

    加载与结算前检查（``ledger.life_catalog.health_state_problems``）与观察构造（``agentlayer.life_views``）共用这一份
    判断——同一个规则冲突在两个入口给出同一个代码与同一句说明，返回 (问题代码, 说明)：

    * ``health_rules_changed``：状态绑定的规则版本或内容指纹与这套规则不同（编号、版本都相同、只改了病程也算）；
    * ``health_state``：第 1 年以外的状态没有绑定（不猜它在哪套规则下产生、不自动补绑定）；年龄不在风险表的任何年龄段里；
      延续中的疾病按这套规则上个月末就该结束。

    第 1 年的共同初始状态（健康、没有病史、没有负担记录）可以不绑定，所以各实验臂能从同一个起点用不同规则运行。
    事件来源的核对不在这里：观察不显示任何由事件来源推出的事实，只有结算需要它（``illness_source_changed``）。
    都是**输入错误**，不是分身的生活不可行。
    """
    h = state.health
    who = state.clone_id
    out: list[tuple[str, str]] = []
    if rules.risk.age_band_of(h.age_years) is None:
        out.append(("health_state", f"{who}: 年龄 {h.age_years} 不在风险表的任何年龄段里"))
    b = h.binding
    if b is None:
        if state.year_index != 1:
            out.append(
                (
                    "health_state",
                    f"{who}: 第 {state.year_index} 年的健康状态没有绑定产生它的规则与事件来源——只有第 1 年的共同初始"
                    "状态可以不绑定；不猜它在哪一套规则下产生",
                )
            )
    elif (b.rules_version, b.rules_fingerprint) != (rules.version, rules.content_fingerprint):
        out.append(
            (
                "health_rules_changed",
                f"{who}: 健康状态由另一套疾病规则产生（内容指纹 {b.rules_fingerprint[:12]}…，现在是 "
                f"{rules.content_fingerprint[:12]}…）：概率表、恢复期或限制变了，旧的中间状态不静默续跑；"
                "请从共同初始状态重新运行",
            )
        )
    ep = h.episode
    if (
        state.status == "active"
        and ep is not None
        and ep.months_ill >= rules.severity_rules(ep.severity).untreated_months
    ):
        out.append(
            (
                "health_state",
                f"{who}: 疾病 {ep.episode_id} 已经病了 {ep.months_ill} 个月，按规则上个月末就该自然恢复——状态与规则对不上",
            )
        )
    return out


_TAG_OCCUPATION = re.compile(r"^occupation:([A-Za-z0-9]+):([A-Za-z0-9.\-]+)$")
_TAG_ABILITY = re.compile(r"^ability:([a-z][a-z0-9_]{0,47})$")
_TAG_EMPLOYER = re.compile(r"^employer:([a-z0-9][a-z0-9_.\-]{0,63})$")
#: 资格标签的种类（DECISIONS/0023）：能力、职业、雇主，都按年初状态判断。
CAREER_REQUIREMENT_KINDS: frozenset[str] = frozenset({"ability", "occupation", "employer"})


def parse_eligibility_tag(tag: str) -> tuple[str, str, str] | None:
    """资格标签的语法（不管协议允许哪几种）。词表外的标签返回 None——由调用方报资料错误，**不猜**。

    * ``occupation:<体系>:<代码>``：当前职业等于它
    * ``ability:<能力>``：已获得这项能力（E1-5A；按年初状态）
    * ``employer:<雇主>``：当前在这家雇主有岗位（E1-5A；按年初状态）
    """
    if m := _TAG_OCCUPATION.match(tag):
        return ("occupation", m.group(1), m.group(2))
    if m := _TAG_ABILITY.match(tag):
        return ("ability", m.group(1), "")
    if m := _TAG_EMPLOYER.match(tag):
        return ("employer", m.group(1), "")
    return None


class QualificationGap(ContractModel):
    """一项机会按年初状态还不满足的一条要求。"""

    tag: str
    kind: str
    target: str
    reason: str = Field(min_length=1, max_length=280)


def qualification_gaps(
    opportunity: LifeOpportunity, state: PersonYearState
) -> list[QualificationGap]:
    """E1-5A：按**年初状态**，这项机会还有哪些要求不满足。加载与结算前检查、结算与可见投影共用这一份。

    能力只认年初已经可用的（当年完成的学习从下一行动年起才算）；职业与雇主只认年初的当前岗位。
    词表外的写法一律算不满足——不能把看不懂的要求当作满足。
    """
    usable = {a.ability_id for a in state.abilities if a.usable_from_year <= state.year_index}
    current = state.employment.current_positions()
    occupations = {(p.occupation.scheme, p.occupation.code) for p in current}
    employers = {p.employer_id for p in current if p.employer_id is not None}
    gaps: list[QualificationGap] = []
    for tag in opportunity.eligibility:
        parsed = parse_eligibility_tag(tag)
        if parsed is None:
            gaps.append(
                QualificationGap(
                    tag=tag, kind="unknown", target=tag, reason="看不懂的资格要求，不当作满足"
                )
            )
            continue
        kind, a, b = parsed
        if kind == "ability":
            if a not in usable:
                gaps.append(
                    QualificationGap(
                        tag=tag,
                        kind=kind,
                        target=a,
                        reason=f"年初没有能力 {a}（当年完成的学习从下一行动年起才算）",
                    )
                )
        elif kind == "occupation":
            if (a, b) not in occupations:
                gaps.append(
                    QualificationGap(
                        tag=tag,
                        kind=kind,
                        target=f"{a}:{b}",
                        reason=f"年初的当前岗位里没有职业 {a}:{b}",
                    )
                )
        elif kind == "employer" and a not in employers:
            gaps.append(
                QualificationGap(tag=tag, kind=kind, target=a, reason=f"年初不在雇主 {a} 工作")
            )
    return gaps


# ==========================================================================
# 年度行动
# ==========================================================================

#: 结果类字段。代理**不能**自己填写已获得的工资、满足感、技能提升或执行成功。
RESERVED_OUTCOME_KEYS: frozenset[str] = frozenset(
    {
        "wage",
        "salary",
        "income",
        "earnings",
        "pay",
        "raise",
        "promotion",
        "satisfaction",
        "happiness",
        "utility",
        "welfare",
        "score",
        "skill_gain",
        "skill_level",
        "energy_gain",
        "success",
        "succeeded",
        "executed",
        "outcome",
        "result",
        "cash_after",
        "balance_after",
    }
)


class ActionItem(ContractModel):
    """一条行动：引用一个**已有**机会 ID，给数量、安排与执行时间。

    时间用结构化字段表达，**不**从理由、目标或描述里提取：

    * 周期行动（住房、食品、工作、按月/按周计费的服务、培训参与）：
      ``start_month`` 与 ``end_month``，含两端；
    * 年付预付服务：只给 ``start_month``，覆盖期固定 12 个月（可跨入下一年）；
    * 一次性购买或体验：``month``。

    哪种机会要哪种字段，在结算前检查里按目录判定；这里只管字段本身的自洽。
    行动在年度计划里的**位置**就是同月竞争资金时的优先顺序，结算会原样记录。
    """

    opportunity_id: IdStr
    quantity: Annotated[float, Field(gt=0)]
    arrangement: dict[KeyStr, Primitive] = Field(default_factory=dict)
    start_month: Month | None = None
    end_month: Month | None = None
    month: Month | None = None
    #: 生活扩展（DECISIONS/0041）：同一项活动在不连续的几个月参加时，明确列出月份（严格递增）；每个月按 ``quantity`` 次。
    #: 只用于一次性的体验；不另造编号，全年仍只有这一条。没有用到时不序列化。
    months: tuple[Month, ...] | None = Field(default=None, exclude_if=lambda v: v is None)
    #: E1-5B：这条购买或报名用来兑现哪个资金目标（必须明确写；同一机会没写就不算兑现）。没有用到时不序列化。
    goal_id: IdStr | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _timing_fields_consistent(self) -> ActionItem:
        if self.months is not None:
            if not self.months:
                raise ValueError(f"{self.opportunity_id}: months 不能为空")
            if any(b <= a for a, b in zip(self.months, self.months[1:], strict=False)):
                raise ValueError(f"{self.opportunity_id}: months 必须严格递增（不重复）")
            if self.month is not None or self.start_month is not None or self.end_month is not None:
                raise ValueError(f"{self.opportunity_id}: 给了 months 就不再给 month 或起止月")
        if self.month is not None and (self.start_month is not None or self.end_month is not None):
            raise ValueError(
                f"{self.opportunity_id}: 一次性行动用 month，周期行动用 start_month/end_month，"
                "不能同时给"
            )
        if self.end_month is not None:
            if self.start_month is None:
                raise ValueError(f"{self.opportunity_id}: 给了 end_month 就必须给 start_month")
            if self.end_month < self.start_month:
                raise ValueError(
                    f"{self.opportunity_id}: end_month {self.end_month} 早于 "
                    f"start_month {self.start_month}"
                )
        return self

    @field_validator("arrangement")
    @classmethod
    def _no_outcome_keys(cls, v: dict[str, Primitive]) -> dict[str, Primitive]:
        bad = sorted(set(v) & RESERVED_OUTCOME_KEYS)
        if bad:
            raise ValueError(f"安排里出现了结果字段 {bad}：结果由环境结算，不由代理填写")
        return v


class YearAction(ContractModel):
    """一个分身一年的生活计划。**一年只有一份**；环境按月执行，不增加月度调用。

    可以说明理由，**理由不参与数值结算**。

    ``minimum_fallback``：明确声明"若某月付不起我选的住房/食品，就改用本城的最低
    方案"。**没声明的需求，环境不会替分身挑替代方案**——付不起就是计划不可行。
    """

    person_id: IdStr
    clone_id: IdStr
    year_index: YearIndex
    items: tuple[ActionItem, ...]
    minimum_fallback: tuple[MinimumNeed, ...] = ()
    rationale: str | None = Field(default=None, max_length=MAX_RATIONALE_CHARS)
    #: E1-5B：年初对资金目标的指令（设定、保留、取消、替换或改时点）。不写就是已有目标原样延续。
    #: 没有用到时不序列化。
    savings_goal: SavingsGoalDirective | None = Field(default=None, exclude_if=lambda v: v is None)
    #: 这一年的应急安排（生病时治不治、时间精力不够时按什么顺序暂停哪些活动；DECISIONS/0027）。由规划器写，必须写：漏写是
    #: 格式问题（可修改重试），不默认总是治疗，也不由健康回答替它生成。
    emergency: EmergencyPlan

    @model_validator(mode="after")
    def _unique_items(self) -> YearAction:
        ids = [i.opportunity_id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.clone_id}: 同一机会出现多次；合并成一条并写数量")
        if len(set(self.minimum_fallback)) != len(self.minimum_fallback):
            raise ValueError(f"{self.clone_id}: minimum_fallback 重复")
        linked = [i.opportunity_id for i in self.items if i.goal_id is not None]
        if len(linked) > 1:
            raise ValueError(
                f"{self.clone_id}: 有 {len(linked)} 条行动 {linked} 都写了 goal_id——一笔交易最多兑现一个目标，"
                "一个目标也只由一条行动兑现"
            )
        return self

    def settlement_view(self) -> dict[str, Any]:
        """结算唯一能读的部分。**不含理由**——只改理由文字，这里一字不变。E1-5B 的目标指令与关联也在里面
        （它们只改变目标记录，不改变钱）；没有用到时与以前逐字相同。"""
        view: dict[str, Any] = {
            "person_id": self.person_id,
            "clone_id": self.clone_id,
            "year_index": self.year_index,
            "items": [i.model_dump(mode="json") for i in self.items],
            "minimum_fallback": list(self.minimum_fallback),
        }
        if self.savings_goal is not None:
            view["savings_goal"] = self.savings_goal.model_dump(mode="json")
        # 应急安排改变生病时的执行，所以属于结算能读的部分（说明文字不算）
        view["emergency"] = self.emergency.model_dump(mode="json", exclude={"note"})
        return view


# ==========================================================================
# 环境结果、结算结果与内部评分
#
# 结果的**形状**在这里；产生它们的月度结算在 ``econ.life_settlement``。内部评分仍然
# 只有接口（评分映射未定）。
# ==========================================================================


LedgerKind = Literal["income_gross", "income_tax", "expense"]


class CashFlowEntry(ContractModel):
    """一笔实际发生的现金流。收入为正，税与支出为负。"""

    label: str = Field(min_length=1, max_length=120)
    amount: Money
    month: Month | None = None
    kind: LedgerKind | None = None
    opportunity_id: IdStr | None = None

    @field_validator("amount")
    @classmethod
    def _life_money(cls, v: Money) -> Money:
        _finite(v.amount, "现金流")
        require_life_numeraire(v.numeraire, "现金流")
        return v


#: ``not_reached``：分身在这项行动的月份之前已经出局。那不是"被拒绝"——行动本身没有
#: 失败，只是生活停在了更早的月份。
ItemStatus = Literal[
    "executed",
    "partially_executed",
    "rejected",
    "downgraded_to_minimum",
    "fallback_minimum",
    "not_reached",
    # E1-5C：计划的月份全部因为生病暂停（病假、未入职、大病暂停主动活动、按应急顺序暂停）；不是被拒，也不是没到达
    "paused",
    # E1-5C 收尾修订：一次性耐用品购买按年度应急声明、因当月患病取消（不顺延、不补购）；不是被拒，也不是资金不足
    "cancelled_when_ill",
]
#: 一项行动在结算里扮演的角色。住房与食品由城市的最低生活声明认定（合格名单），
#: 其余按机会的期限种类与计费周期认定。
ItemRole = Literal[
    "housing",
    "food",
    "job",
    "periodic_service",
    "prepaid_service",
    "durable_good",
    "experience",
    "training",
]


class ExecutedItem(ContractModel):
    """一项行动的**可见**执行事实（反馈给代理）。实际服务月份与被拒月份都写出来。"""

    opportunity_id: IdStr
    requested_quantity: Annotated[float, Field(gt=0)]
    executed_quantity: Annotated[float, Field(ge=0)]
    status: ItemStatus
    reason: str | None = Field(default=None, max_length=280)
    covered_months: tuple[Month, ...] = ()
    months_rejected: tuple[Month, ...] = ()
    #: E1-5C：因为生病暂停（收尾修订起也含按声明因病取消购买）的月份（原因写在 ``reason`` 与年度健康反馈里）。
    #: 没有用到时不序列化。
    months_paused: tuple[Month, ...] = Field(default=(), exclude_if=lambda v: v == ())


class MonthRejection(ContractModel):
    month: Month
    reason: str = Field(min_length=1, max_length=200)


#: 一个月为什么暂停（E1-5C）：
#:   sick_leave          大病病假：雇佣关系保留，没有工资、工时、到岗与通勤
#:   not_started         大病落在一份还没实际上过班的新岗位的月份：不算入职，不授予雇主身份
#:   major_illness_rule  大病时暂停主动活动（上课、一次体验、有参与时间的服务与持有物）——世界规则
#:   agent_pause_order   按年度计划应急安排里声明的顺序暂停——代理自己的安排
#:   agent_cancel_when_ill  按年度计划应急安排里的取消声明，因当月患病取消一次性耐用品购买（收尾修订）——代理自己的
#:                          安排；不扣款、不生成持有物、不顺延、不补购
PauseKind = Literal[
    "sick_leave", "not_started", "major_illness_rule", "agent_pause_order", "agent_cancel_when_ill"
]


class MonthPause(ContractModel):
    """一项行动在一个月里因为生病暂停或按声明取消（E1-5C）。不是被拒（资源本身没问题），也不是没到达（生活还在继续）。"""

    month: Month
    kind: PauseKind
    reason: str = Field(min_length=1, max_length=200)


class ItemExecution(ContractModel):
    """一项行动在一年里**实际**怎么执行的（内部记录，评分侧读）。

    ``covered_months`` 是这项行动本年真正执行的月份：周期行动是提供了服务、上了班、
    参加了培训的月份（半年服务就是 6 个月，不会被当成全年）；预付服务是本年的覆盖月；
    一次性购买或体验是成交那个月。持续物品**逐月**的服务记在 ``HoldingService``，
    不在这里重复。``effective_*`` 是生效时点（``next_year`` 的物品是下一年第 1 月）。

    ``months_not_reached``：分身出局之后的月份。不是被拒，也不算执行。

    兜底执行单独成一条记录：``status = fallback_minimum``，``fallback_for_plan_order``
    指回被降级的那一项，``plan_order`` 与它相同。
    """

    plan_order: Annotated[int, Field(ge=0)]
    opportunity_id: IdStr
    role: ItemRole
    requested_quantity: Annotated[float, Field(gt=0)]
    executed_quantity: Annotated[float, Field(ge=0)]
    requested_months: tuple[Month, ...]
    covered_months: tuple[Month, ...]
    effective_year: Annotated[int, Field(ge=1, le=MAX_SIMULATION_YEARS + 1)] | None = None
    effective_month: Month | None = None
    amount_paid: Money
    amount_received: Money
    status: ItemStatus
    rejections: tuple[MonthRejection, ...] = ()
    months_not_reached: tuple[Month, ...] = ()
    downgraded_from_month: Month | None = None
    fallback_for_plan_order: Annotated[int, Field(ge=0)] | None = None
    #: E1-5C：因为生病暂停的月份与原因。没有用到时不序列化。
    months_paused: tuple[MonthPause, ...] = Field(default=(), exclude_if=lambda v: v == ())


class HoldingService(ContractModel):
    """一件持有物（持续物品或预付服务）在这一年里实际服务了哪些月，以及它的数量。

    ``quantity`` 由结算直接写出（E1-3 增补，`DECISIONS/0019` 第二节），评分直接读取，不再按
    "年初持有在前、本年新买在后"的顺序去猜。为空只会出现在增补之前写出的旧记录里：只有旧评分版本
    接受它们（按 ADR0018 的推断），新评分版本拒绝，**不默认为 1**。
    """

    opportunity_id: IdStr
    kind: Literal["durable_good", "prepaid_service"]
    covered_months: tuple[Month, ...]
    remaining_months_after: Annotated[int, Field(ge=0)]
    expired: bool
    quantity: Annotated[float, Field(gt=0)] | None = None
    #: E1-5C：服务期照样流逝、但因为生病没有使用的月份（"付款 ≠ 参与"）。它们不在 ``covered_months`` 里：评分与时间
    #: 账只读实际使用的月份。没有用到时不序列化。
    paused_months: tuple[Month, ...] = Field(default=(), exclude_if=lambda v: v == ())


ParticipationStatus = Literal["completed", "paused", "failed"]


class ParticipationRecord(ContractModel):
    """一项休闲活动在一个月里的**实际**参与（权威事实，DECISIONS/0041）：时间账、精力账、休闲评价与事实记忆都从这里取数。

    ``completed``：参加了 ``sessions`` 次，参与 ``participation_hours`` 小时、准备与往返等 ``overhead_hours`` 小时；用到的装备是
    ``equipment_used``（持有物的机会编号），套餐本次提供的能力在 ``kit_capabilities``（不会变成持有物）。``paused``（生病）与
    ``failed``（缺装备、付不起、资料不可得）次数与小时都是 0，原因写在 ``reason``；已经合法发生的费用不倒删，没参加就没有收益。"""

    plan_order: Annotated[int, Field(ge=0)]
    opportunity_id: IdStr
    month: Month
    status: ParticipationStatus
    primary_category: KeyStr
    sessions: Annotated[float, Field(ge=0)]
    participation_hours: Annotated[float, Field(ge=0)]
    overhead_hours: Annotated[float, Field(ge=0)]
    equipment_used: tuple[IdStr, ...] = ()
    kit_capabilities: tuple[KeyStr, ...] = ()
    reason: str | None = Field(default=None, max_length=MAX_SUMMARY_CHARS)

    @model_validator(mode="after")
    def _status_consistent(self) -> ParticipationRecord:
        done = self.status == "completed"
        if done != (self.sessions > 0):
            raise ValueError(f"{self.opportunity_id} 第 {self.month} 月：只有实际参加才有次数")
        if not done and (self.participation_hours or self.overhead_hours or self.equipment_used):
            raise ValueError(
                f"{self.opportunity_id} 第 {self.month} 月：没有参加就没有参与小时与装备使用"
            )
        if not done and not self.reason:
            raise ValueError(f"{self.opportunity_id} 第 {self.month} 月：暂停或失败要写原因")
        return self


class MonthRecord(ContractModel):
    """一个月的账：月初现金、收入、税、最低生活要求（**只检查，不扣款**）、支出、月末现金。"""

    year_index: YearIndex
    month: Month
    cash_start: Money
    income_gross: Money
    income_tax: Money
    minimum_required: Money
    minimum_check: Literal["passed", "failed"]
    spent: Money
    cash_end: Money


ResourceCategory = Literal[
    "work",
    "commute",
    "food_prep",
    "household_task",
    "service",
    "experience",
    "training",
    # E1-5C：生病月份本人额外花的恢复时间与负担；接受治疗那个月的就医时间与负担
    "recovery",
    "treatment",
]
#: 一个月做了哪种资源检查（r2）。互不混用：
#:   complete           过完的月份：全部用量已知，与预算、额度逐项比较；
#:   known_lower_bound  出局当月：只把已经确定的用量（已入账工资对应的工作，以及确定为零的通勤）与预算、
#:                      额度比较。没超不等于这个月通过了完整检查——其余用量未知；
#:   not_reached        出局或停止之后：没有发生，不检查，也不是零负担的完整月份；
#:   blocked            停止月（E1-5C）：生病之后声明的应急规则仍满足不了时间或精力、或要用的资料缺失，这个月的执行
#:                      没有提交（诊断在停止记录里）；不是完整月份，也没有用量。
ResourceCheck = Literal["complete", "known_lower_bound", "not_reached", "blocked"]
#: 通勤怎么定的：computed（实际住处片区 × 各到岗岗位的就业片区，按通勤关系表执行）、no_onsite_work（这个月没有需要
#: 到岗的工作，通勤确定为零）、unknown_no_housing（出局当月没有获得住房，通勤依据缺失，记为未知而不是零）、
#: not_reached。
CommuteStatus = Literal["computed", "no_onsite_work", "unknown_no_housing", "not_reached"]


class CommuteUse(ContractModel):
    """这个月一个到岗岗位实际执行的通勤（DECISIONS/0042）：哪条线、什么方式、往返几次、花了多少（封顶之前）。"""

    job_opportunity_id: IdStr
    home_area: IdStr
    work_area: IdStr
    mode: CommuteMode
    roundtrip_minutes: Annotated[int, Field(ge=0)]
    onsite_visits: Annotated[int, Field(ge=1)]
    cost: Money
    #: 为什么是这种方式（可用的方式与分钟），可以手查。
    basis: str = Field(min_length=1, max_length=280)


class MonthCommute(ContractModel):
    """一个月的通勤执行记录（DECISIONS/0042）：唯一的执行事实。

    结算按 ``paid`` 付通勤费；资源账按 ``uses`` 记通勤小时与负担；交通评分读往返分钟与到岗次数；通勤上限读最长的往返。
    ``paid`` = 各方式（这个月花费合计与月度封顶取小）之和。"""

    month: Month
    status: CommuteStatus
    housing_opportunity_id: IdStr | None = None
    home_area: IdStr | None = None
    uses: tuple[CommuteUse, ...] = ()
    paid: Money | None = None
    notes: tuple[Annotated[str, Field(max_length=MAX_SUMMARY_CHARS)], ...] = ()

    @model_validator(mode="after")
    def _status_consistent(self) -> MonthCommute:
        if self.status == "computed":
            if not self.uses or self.paid is None or self.home_area is None:
                raise ValueError(
                    f"第 {self.month} 月：执行了的通勤要有住处片区、每个岗位的记录与付款"
                )
        elif self.uses or self.paid is not None:
            raise ValueError(f"第 {self.month} 月：通勤是 {self.status}，不能有执行记录或付款")
        return self

    @property
    def onsite_visits(self) -> int:
        return sum(u.onsite_visits for u in self.uses)

    @property
    def longest_roundtrip_minutes(self) -> int | None:
        return max((u.roundtrip_minutes for u in self.uses), default=None)

    def visit_weighted_minutes(self) -> Fraction | None:
        """按到岗次数加权的平均往返分钟（交通评分的 T）；没有到岗时是 None。"""
        visits = self.onsite_visits
        if visits == 0:
            return None
        return Fraction(sum(u.roundtrip_minutes * u.onsite_visits for u in self.uses), visits)


class ResourceContribution(ContractModel):
    """一项活动在一个月里占用的小时与负担，以及计量依据（可以手算）。"""

    category: ResourceCategory
    opportunity_id: IdStr | None = None  # 家务时是承担替代的物品或服务；本人全做时为空
    task: KeyStr | None = None
    hours: ExactAmount
    burden: ExactAmount
    basis: str = Field(min_length=1, max_length=240)


class MonthResources(ContractModel):
    """一个月的时间与精力账（E1-4A）。数值都是精确分数加浮点近似。

    ``resource_check`` 说明这个月做了哪种检查（见 ``ResourceCheck``）：

    * ``complete``：``time_used`` / ``energy_used`` 是完整用量；``time_remaining`` 是**未安排时间，月底
      作废**（不结转、不回血、不加满足感，也不必安排满）；``within_limits`` 是完整检查的结论；
    * ``known_lower_bound``：``time_used`` / ``energy_used`` 是**已知用量（下限）**，剩余与 ``within_limits``
      为空——完整用量未知，不能说这个月通过了检查；
    * ``not_reached``：没有用量，没有贡献。

    ``onsite_visits`` 与 ``job_workdays_sum`` 是各岗位的到岗次数、工作日的**累计**，不是不同的日历天数：两份
    兼职各到岗 10 次，累计 20 次，可能只占 10 个日历天。
    """

    month: Month
    resource_check: ResourceCheck
    time_budget: ExactAmount
    energy_quota: ExactAmount
    time_used: ExactAmount | None = None
    time_remaining: ExactAmount | None = None
    energy_used: ExactAmount | None = None
    energy_remaining: ExactAmount | None = None
    within_limits: bool | None = None
    contributions: tuple[ResourceContribution, ...] = ()
    housing_opportunity_id: IdStr | None = None
    job_workdays_sum: Annotated[int, Field(ge=0)] = 0
    onsite_visits: Annotated[int, Field(ge=0)] = 0
    commute_status: CommuteStatus
    #: 这个月的通勤执行记录（DECISIONS/0042）；没有发生的月份没有。
    commute: MonthCommute | None = None
    #: 给 E1-5：当月工作负担 / 额度；通勤负担 / 额度；总负担 / 额度。本轮不换算成疾病概率或幸福分。
    work_relative_burden: ExactAmount | None = None
    commute_relative_burden: ExactAmount | None = None
    total_relative_burden: ExactAmount | None = None
    notes: tuple[Annotated[str, Field(max_length=MAX_SUMMARY_CHARS)], ...] = ()

    @model_validator(mode="after")
    def _check_consistent(self) -> MonthResources:
        c, m = self.resource_check, self.month
        totals = (self.time_used, self.energy_used)
        remaining = (self.time_remaining, self.energy_remaining, self.within_limits)
        if c == "complete" and any(x is None for x in (*totals, *remaining)):
            raise ValueError(f"第 {m} 月：完整检查必须有用量、未安排时间、剩余额度与检查结论")
        if c == "known_lower_bound":
            if any(x is None for x in totals):
                raise ValueError(f"第 {m} 月：只检查已知下限时必须写出已知用量")
            if any(x is not None for x in remaining):
                raise ValueError(
                    f"第 {m} 月：只检查已知下限时完整用量未知，不写剩余，也不写“在额度内”的结论"
                )
        if c == "not_reached" and (
            self.contributions or any(x is not None for x in (*totals, *remaining))
        ):
            raise ValueError(f"第 {m} 月：没有发生的月份没有资源记录")
        if self.commute is not None:
            if self.commute.status != self.commute_status or self.commute.month != m:
                raise ValueError(f"第 {m} 月：通勤执行记录与资源账的通勤状态对不上")
            if (
                self.commute.status == "computed"
                and self.commute.onsite_visits != self.onsite_visits
            ):
                raise ValueError(f"第 {m} 月：通勤执行记录的到岗次数与资源账对不上")
        if c == "blocked" and (
            self.contributions or any(x is not None for x in (*totals, *remaining))
        ):
            raise ValueError(f"第 {m} 月：停止月的执行没有提交，没有资源用量（诊断在停止记录里）")
        return self


class ResourceYear(ContractModel):
    """一年的月度资源账（E1-4A）。逐月 1–12 月；出局之后的月份标为 not_reached。"""

    rules_version: str
    #: 执行修订（``RESOURCE_RULES_REVISION``）。r1 的历史结果没有这一项。
    rules_revision: str
    time_unit: str
    energy_unit: str
    energy_scale_version: str
    monthly_time_budget: ExactAmount
    monthly_energy_quota: ExactAmount
    reserved: str
    months: tuple[MonthResources, ...]

    @model_validator(mode="after")
    def _twelve_months(self) -> ResourceYear:
        if [m.month for m in self.months] != list(range(1, MONTHS_PER_YEAR + 1)):
            raise ValueError("资源账必须逐月给出 1–12 月")
        return self


class ResourceShortfall(ContractModel):
    """计划不可行时的资源诊断：哪个月、哪种资源、额度、用量、超出多少、主要是谁占的。

    ``resource_check = known_lower_bound`` 时 ``used`` 是已知用量（下限）、``excess`` 是至少超出多少：出局当月
    已经确定的劳动就超了额度，资金出局不能绕过资源约束。只有资源事实，没有满足感、偏好或评分参数。
    """

    resource: Literal["time", "energy"]
    month: Month
    resource_check: Literal["complete", "known_lower_bound"]
    rules_revision: str
    unit: str
    limit: ExactAmount
    used: ExactAmount
    excess: ExactAmount
    top_contributors: tuple[ResourceContribution, ...]


# --------------------------------------------------------------------------
# E1-5C：一年的健康事实与停止记录（结算结果的一部分；内部对象，可见部分另有 Visible* 类型）
# --------------------------------------------------------------------------


class StopRecord(ContractModel):
    """分身停止的记录（E1-5C）：哪一年哪一月、为什么。

    =======================  ====================================================================
    emergency_insufficient   疾病发生之后，按声明的应急安排逐项暂停、放弃治疗之后，这个月的时间或精力仍然超限，
                             或所选住房 / 食品因为疾病花费付不起而计划没有声明兜底
    data_gap                 疾病发生之后要用的资料缺失（例如要治疗而本城的治疗资料未知）
    =======================  ====================================================================

    这是**模拟器的支持边界**，不是"现实中活不下去"、不是出局，也不是年初计划写错（那在年初的无事件试算里就会被
    挡下）。停止月的执行没有提交：之前的月份全部保留；这个月的疾病事件与原因保留；没有这个月的工资与支出。
    """

    year_index: YearIndex
    month: Month
    kind: Literal["emergency_insufficient", "data_gap"]
    reason: str = Field(min_length=1, max_length=500)
    problem_codes: tuple[str, ...] = ()
    shortfalls: tuple[ResourceShortfall, ...] = ()


class RiskDraw(ContractModel):
    """一次月初抽签的内部记录（不进观察）：用了哪一格概率、上月负担从哪来、随机数与结果。

    ``uniform`` 是 [0, 1) 内的精确分数（随机键 SHA-256 的前 64 位 ÷ 2^64）；``key_fingerprint`` 是随机键规范 JSON 的
    SHA-256，可以据此复算。u < p_大病 → 大病；p_大病 ≤ u < p_大病 + p_小病 → 小病；其余没有事件。
    """

    stream_version: str
    key_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    uniform: ExactAmount
    age_years: int
    age_band: KeyStr
    burden_band: KeyStr
    last_work_relative_burden: ExactAmount | None = None
    burden_from: str = Field(min_length=1, max_length=120)
    p_minor: ExactAmount
    p_major: ExactAmount
    result: Literal["none", "minor", "major"]


class PausedActivity(ContractModel):
    """一个月里因为生病暂停（或按声明取消购买）的一项活动（内部记录）。"""

    opportunity_id: IdStr
    role: str
    kind: PauseKind
    note: str = Field(min_length=1, max_length=200)


TreatmentStatus = Literal[
    "treated",  # 实际付清并接受治疗：当月月末恢复
    "declined",  # 年度计划明确不治
    "no_benefit",  # 疾病本来就在这个月月末结束：治疗没有作用，不购买
    "unavailable",  # 本城确定没有这种治疗
    "unaffordable",  # 住房与食品之后的现金不够
    "resources_insufficient",  # 按声明的顺序暂停所有能暂停的活动之后，加上就医仍超出时间或精力
    "data_gap",  # 要治疗而本城的治疗资料未知（这个分身停止）
]


class TreatmentOutcome(ContractModel):
    """一个病月里治疗的结果（内部记录）。"""

    severity: Severity
    plan_choice: EmergencyChoice
    status: TreatmentStatus
    treatment_id: IdStr | None = None
    amount_paid: Money | None = None
    visit_hours: ExactAmount | None = None
    burden: ExactAmount | None = None
    note: str = Field(min_length=1, max_length=280)

    @model_validator(mode="after")
    def _paid_only_when_treated(self) -> TreatmentOutcome:
        paid = (self.amount_paid, self.visit_hours, self.burden)
        if (self.status == "treated") != all(x is not None for x in paid):
            raise ValueError("只有实际治疗的月份写付款、就医小时与负担，治疗了就必须写")
        return self


HealthTransition = Literal[
    "stays_healthy", "continues", "recovered_treated", "recovered_natural", "not_applicable"
]


class HealthMonth(ContractModel):
    """一个月的健康事实（内部记录，评分读 ``state``）。

    * ``record``：``lived`` 过完的月份；``exit_month`` 出局当月（不治疗、不计恢复负担）；``blocked`` 停止月（执行没有提交）；
    * ``state``：月初状态——决定这个月的工作限制与健康分；
    * ``onset``：这个月月初新发的疾病（只在月初健康时）；``injected_skipped``：指定事件落在已经生病的月份，没有叠加；
    * ``transition``：月末转移（继续生病、治疗后恢复、自然恢复、保持健康）。
    """

    month: Month
    record: Literal["lived", "exit_month", "blocked"]
    state: HealthStatus
    onset: Severity | None = None
    onset_source: IllnessSource | None = None
    injected_skipped: bool = False
    draw: RiskDraw | None = None
    episode_id: IdStr | None = None
    episode_month: Annotated[int, Field(ge=1, le=36)] | None = None
    sick_leave: bool = False
    paused: tuple[PausedActivity, ...] = ()
    treatment: TreatmentOutcome | None = None
    recovery_hours: ExactAmount | None = None
    recovery_burden: ExactAmount | None = None
    transition: HealthTransition
    notes: tuple[Annotated[str, Field(max_length=MAX_SUMMARY_CHARS)], ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> HealthMonth:
        ill = self.state != "healthy"
        if ill != (self.episode_id is not None and self.episode_month is not None):
            raise ValueError(f"第 {self.month} 月：生病的月份必须写疾病与病月序号，健康月份不能写")
        if (self.onset is not None) != (self.onset_source is not None):
            raise ValueError(f"第 {self.month} 月：新发疾病与它的来源要一起写")
        if self.onset is not None and (self.onset != self.state or self.episode_month != 1):
            raise ValueError(f"第 {self.month} 月：新发疾病就是这个月的状态、病月序号是 1")
        if self.sick_leave and self.state != "major":
            raise ValueError(f"第 {self.month} 月：只有大病才病假")
        if self.treatment is not None and not ill:
            raise ValueError(f"第 {self.month} 月：健康月份没有治疗")
        if self.record != "lived":
            if self.transition != "not_applicable":
                raise ValueError(f"第 {self.month} 月：出局当月与停止月没有月末转移")
        elif not ill and self.transition != "stays_healthy":
            raise ValueError(f"第 {self.month} 月：健康的月份月末仍是健康")
        elif ill and self.transition not in ("continues", "recovered_treated", "recovered_natural"):
            raise ValueError(f"第 {self.month} 月：生病的月份月末要么继续生病、要么恢复")
        treated = self.treatment is not None and self.treatment.status == "treated"
        if self.record == "lived" and treated != (self.transition == "recovered_treated"):
            raise ValueError(
                f"第 {self.month} 月：治疗了就在当月月末恢复，没有治疗就不会因治疗恢复"
            )
        return self


class HealthYear(ContractModel):
    """一年的健康事实（E1-5C，结算结果的一部分；评分器只读 ``months[].state``）。

    逐月记录到出局当月或停止月为止，之后的月份没有发生、不记录。规则与事件来源的内容指纹写在这里，以后可以核对这份
    记录是在哪一套规则下产生的。
    """

    rules_version: Literal["e1-5c-health-v1"]
    rules_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_mode: Literal["none", "injected", "random"]
    source_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    age_years: Annotated[int, Field(ge=AGE_MIN, le=AGE_MAX)]
    repeat_index: Annotated[int, Field(ge=0, le=MAX_REPEAT_INDEX)]
    months: tuple[HealthMonth, ...]
    notes: tuple[Annotated[str, Field(min_length=1, max_length=MAX_SUMMARY_CHARS)], ...] = ()
    #: 收尾修订：这一年的年度计划声明"生病月份取消"的购买（原样记下，让下一年的观察看得到自己的声明）。没有用到时不序列化。
    cancel_purchases_when_ill: tuple[IdStr, ...] = Field(default=(), exclude_if=lambda v: v == ())

    @model_validator(mode="after")
    def _months_in_order(self) -> HealthYear:
        got = [m.month for m in self.months]
        if got != list(range(1, len(got) + 1)):
            raise ValueError(f"健康记录必须从第 1 月起逐月连续，得到 {got}")
        ends = [m.month for m in self.months if m.record != "lived"]
        if ends and ends != [got[-1]]:
            raise ValueError("出局当月或停止月只能是记录的最后一个月")
        return self

    def month(self, m: int) -> HealthMonth | None:
        return next((x for x in self.months if x.month == m), None)


class VisibleCommuteUse(ContractModel):
    """**代理可见**的一个岗位一个月的实际通勤（逐字段取自 ``CommuteUse``，DECISIONS/0042）。片区从住房与岗位读，
    花费合计在 ``VisibleMonthResources.commute_paid``（每次往返的花费在通勤关系表里）。"""

    job_opportunity_id: IdStr
    mode: CommuteMode
    roundtrip_minutes: Annotated[int, Field(ge=0)]
    onsite_visits: Annotated[int, Field(ge=1)]


class VisibleMonthResources(ContractModel):
    """**代理可见**的一个月资源事实（逐字段取自 ``MonthResources``）。

    ``time_remaining`` 是未安排时间，月底作废。``resource_check = known_lower_bound`` 时用量是已知下限。
    精力字段只有许可了精力可见（``show_energy_level``）时才有值；观察构造时按许可清空。隐藏的只是额度与
    汇总——各项活动每小时负担是公开成本，代理仍可能自己推算负担。

    ``months``：内容（检查方式、用量、剩余、分类小时与负担、住房、通勤与说明）**完全相同**的月份合成一条，列出全部
    月份；不同的月份各自一条（DECISIONS/0041 R1：只省重复，不删任何一个月的事实）。"""

    months: tuple[Month, ...] = Field(min_length=1)
    resource_check: ResourceCheck
    time_used: ExactAmount | None = None
    time_remaining: ExactAmount | None = None
    energy_used: ExactAmount | None = None
    energy_remaining: ExactAmount | None = None
    hours_by_category: dict[str, ExactAmount] = Field(default_factory=dict)
    burden_by_category: dict[str, ExactAmount] | None = None
    housing_opportunity_id: IdStr | None = None
    onsite_visits: Annotated[int, Field(ge=0)] = 0
    commute_status: CommuteStatus
    #: 这个月实际执行的通勤与付的通勤费（DECISIONS/0042）；没有到岗时不序列化。
    commute: tuple[VisibleCommuteUse, ...] = Field(default=(), exclude_if=lambda v: v == ())
    commute_paid: Money | None = Field(default=None, exclude_if=lambda v: v is None)
    notes: tuple[Annotated[str, Field(max_length=MAX_SUMMARY_CHARS)], ...] = ()

    @model_validator(mode="after")
    def _months_increasing(self) -> VisibleMonthResources:
        if list(self.months) != sorted(set(self.months)):
            raise ValueError("合并的月份必须严格递增、不重复")
        return self


class LearningYearRecord(ContractModel):
    """一门课这一年的学习事实（E1-5A）：报名付费、实际参加的月份、进度、完成与授予。"""

    opportunity_id: IdStr
    fee_charged: Money
    months_attended: tuple[Month, ...]
    progress_before: Annotated[int, Field(ge=0)]
    progress_after: Annotated[int, Field(ge=0)]
    months_required: Annotated[int, Field(ge=1)]
    completed_month: Month | None = None
    #: 这一年新获得的能力；``already_held``：课程声明授予、但已经有了的能力（不重复授予）。
    granted: tuple[KeyStr, ...] = ()
    already_held: tuple[KeyStr, ...] = ()
    usable_from_year: YearIndex | None = None
    #: E1-5C：计划里要上课、但因为生病暂停的月份（不增加进度、不重收学费）。没有用到时不序列化。
    months_paused: tuple[Month, ...] = Field(default=(), exclude_if=lambda v: v == ())

    @model_validator(mode="after")
    def _consistent(self) -> LearningYearRecord:
        if self.progress_after != self.progress_before + len(self.months_attended):
            raise ValueError(f"{self.opportunity_id}: 进度只因实际参加的月份增加")
        if (self.completed_month is None) == (
            self.progress_before < self.months_required <= self.progress_after
        ):
            raise ValueError(f"{self.opportunity_id}: 完成月与进度不一致")
        if self.granted and (self.completed_month is None or self.usable_from_year is None):
            raise ValueError(f"{self.opportunity_id}: 授予能力必须在完成之后，并写明从哪一年起可用")
        return self


class CareerYear(ContractModel):
    """一年的职业事实（E1-5A）：年初的资格依据、学习记录与实际执行的岗位。是结算结果的一部分。"""

    abilities_at_start: tuple[KeyStr, ...]
    positions_at_start: tuple[IdStr, ...]
    learning: tuple[LearningYearRecord, ...] = ()
    positions: tuple[PositionRecord, ...] = ()
    current_position_ids: tuple[IdStr, ...] = ()


class EnvironmentOutcome(ContractModel):
    """**实际**环境结果（评分侧可读）。由 ``econ.life_settlement`` 产生。

    ``settlement_status``：``completed`` 正常过完这一年；``exited`` 在 ``exit`` 记录的那个月
    出局，此前真实发生的账目保留，之后不再有收入与消费；``stopped``（E1-5C）在 ``stop`` 记录的那个月停止（应急规则不足
    或资料缺口），此前的月份保留，停止月与之后没有账目。

    ``bookkeeping_label = synthetic_bookkeeping_only``：本轮的税是合成测试替身，这份账
    **不是**真实税后生活成本。
    """

    person_id: IdStr
    clone_id: IdStr
    year_index: YearIndex
    money_basis_id: str
    executed: tuple[ExecutedItem, ...]
    cash_flows: tuple[CashFlowEntry, ...] = ()
    cash_start: Money | None = None
    cash_end: Money | None = None
    settlement_status: Literal["completed", "exited", "stopped"] = "completed"
    settlement_convention: str | None = None
    months: tuple[MonthRecord, ...] = ()
    items: tuple[ItemExecution, ...] = ()
    holdings_service: tuple[HoldingService, ...] = ()
    #: 生活扩展（DECISIONS/0041）：休闲活动的实际参与（每项活动每个请求月一条）。没有用到时不序列化。
    participation: tuple[ParticipationRecord, ...] = Field(default=(), exclude_if=lambda v: v == ())
    exit: ExitRecord | None = None
    tax_policy_id: str | None = None
    tax_status: str | None = None
    bookkeeping_label: Literal["synthetic_bookkeeping_only"] | None = None
    time_used_hours: Annotated[float, Field(ge=0)] | None = None
    #: monthly_quota_checked：十二个月都做了完整检查；monthly_quota_checked_until_exit：出局前的月份完整检查、出局当月只检查
    #: 已知下限、之后未发生；monthly_quota_checked_until_stop：停止之前的月份完整检查，停止月没有提交、之后未发生——后两者
    #: 都不是"全年通过"。
    energy_feasibility: Literal[
        "monthly_quota_checked",
        "monthly_quota_checked_until_exit",
        "monthly_quota_checked_until_stop",
    ]
    #: 逐月的时间与精力账（DECISIONS/0020）。
    resources: ResourceYear
    notes: tuple[Annotated[str, Field(max_length=MAX_SUMMARY_CHARS)], ...] = ()
    #: 这一年的学习与岗位事实（DECISIONS/0023）。
    career: CareerYear
    #: 这一年的资金目标事实（DECISIONS/0026）。评分器不读它。
    savings: SavingsYear
    #: 这一年的健康事实（评分只读各月月初状态，DECISIONS/0027）与停止记录。
    health: HealthYear
    stop: StopRecord | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _one_ledger(self) -> EnvironmentOutcome:
        for f in self.cash_flows:
            if f.amount.numeraire != self.money_basis_id:
                raise ValueError(
                    f"现金流 {f.label!r} 的口径 {f.amount.numeraire} 与本账 "
                    f"{self.money_basis_id} 不一致"
                )
        for m in (self.cash_start, self.cash_end):
            if m is not None and m.numeraire != self.money_basis_id:
                raise ValueError(f"期初/期末现金的口径 {m.numeraire} 与本账不一致")
        if (self.settlement_status == "exited") != (self.exit is not None):
            raise ValueError("只有出局的结果才带出局记录，出局的结果必须带")
        if (self.settlement_status == "stopped") != (self.stop is not None):
            raise ValueError("只有停止的结果才带停止记录，停止的结果必须带")
        for g in self.savings.progress:
            if g.cash.numeraire != self.money_basis_id:
                raise ValueError("目标进展的余额口径与本账不一致")
        return self


SettlementStatus = Literal[
    "completed", "exited", "stopped", "plan_infeasible", "input_error", "data_error"
]


class SettlementProblem(ContractModel):
    """结算前或结算中发现的一个问题。它**不是**出局：出局只记在 ``ExitRecord`` 里。

    ``code`` 是机器可读的类别；``item_order`` 指年度计划里的第几项（从 0 数）；计划
    不可行时 ``cash_available`` 是那一刻可用于这项的钱、``required`` 是它要的钱。
    """

    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)
    item_order: Annotated[int, Field(ge=0)] | None = None
    month: Month | None = None
    cash_available: Money | None = None
    required: Money | None = None
    #: E1-4A：时间或精力超限时的资源诊断。
    resource: ResourceShortfall | None = None


class SettlementResult(ContractModel):
    """一年结算的结果。五种状态互不混淆（DECISIONS/0017）：

    ===============  =============================================================
    completed        正常过完这一年；``next_state`` 是下一年年初，或第 N 年后的终态
    exited           付不起最低住房与食品而出局；``next_state`` 是出局时刻的状态
    plan_infeasible  自选住房/食品给不了、最低生活仍可负担、且没有声明兜底；或（E1-4A
                     按月核算的协议）实际执行的安排在某个月时间或精力超限。**什么都
                     不提交**，带着诊断退回给计划方
    input_error      结算前就能发现的计划或状态错误。没有任何账目
    data_error       最低生活、税或（E1-4A）资源资料缺失、引用错误、尺度冲突或不完整。
                     没有任何账目
    stopped          （E1-5C）计划本身在年初的无事件试算里可行，但疾病发生之后声明的应急
                     安排仍满足不了时间或精力、或要用的资料缺失：停止月之前的生活全部提交，
                     ``outcome.stop`` 记年月、类别与诊断；``next_state`` 是停止时刻的状态
    ===============  =============================================================

    E1-5C 的五种结果（DECISIONS/0027）：年初计划无效或不可行（input_error / plan_infeasible /
    data_error，什么都不提交）；计划可行、生活因事件改变（completed）；事件之后应急规则不够或资料缺口
    （stopped）；最低生活出局（exited）；程序错误照旧直接抛出。后三种都保留事件之前的生活，不回滚成
    "这一年没发生"。

    **整体不能送进 prompt**：它带着完整的内部结果与状态。可见反馈由
    ``agentlayer.life_views.visible_feedback`` 逐字段投影。
    """

    status: SettlementStatus
    settlement_convention: str = Field(min_length=1)
    problems: tuple[SettlementProblem, ...] = ()
    outcome: EnvironmentOutcome | None = None
    next_state: PersonYearState | None = None

    @model_validator(mode="after")
    def _shape_matches_status(self) -> SettlementResult:
        committed = self.status in ("completed", "exited", "stopped")
        if committed:
            if self.outcome is None or self.next_state is None:
                raise ValueError(f"{self.status} 必须带结果与下一状态")
            if self.problems:
                raise ValueError(f"{self.status} 不带问题清单；披露写在 outcome.notes")
            if self.outcome.settlement_status != self.status:
                raise ValueError("结果的结算状态与外层状态不一致")
        else:
            if self.outcome is not None or self.next_state is not None:
                raise ValueError(f"{self.status} 什么都不提交：不能带结果或下一状态")
            if not self.problems:
                raise ValueError(f"{self.status} 必须说明是什么问题")
        return self


#: 执行前检查在哪一步停下（按结算的检查顺序；E1-7A，DECISIONS/0030）。
PlanCheckStage = Literal[
    "arguments", "state_status", "city_data", "state", "plan", "no_event_trial", "passed"
]


class PlanCheck(ContractModel):
    """执行前检查的结果（E1-7A，DECISIONS/0030）：同一份计划、同一个年初状态，在**不发生新随机事件**的前提下走一遍结算的
    全部检查与逐月试算。**什么都不提交**——没有账目、物品、进度或下一年状态；只说明在哪一步、因为什么没有通过。

    阶段按结算的检查顺序：``arguments``（参数类型与税绑定）→ ``state_status``（终态、出局、停止的状态不接受行动）→
    ``city_data``（城市、职业、税、资源、疾病资料）→ ``state``（年初状态本身）→ ``plan``（计划本身与计划—状态）→
    ``no_event_trial``（没有新随机事件的逐月试算：资源超限、所选住房或食品给不了、要治疗而本城资料未知等）。``passed``：这份计划
    可以交给真实执行。真实执行之后因随机事件出现的停止（``stopped``）不在这里——那是已经发生的生活，不能靠改计划重来。

    状态字符串本身不足以决定能不能修订（同是 ``data_error``，可能是计划选了资料未知的治疗，也可能是城市资料损坏）：调用方按
    ``stage`` 与问题代码一起判断。``new_events_possible``：协议有没有随机事件（E1-5C 疾病）；没有时，试算就是完整的确定性执行。
    """

    stage: PlanCheckStage
    passed: bool
    status: SettlementStatus | None = None
    problems: tuple[SettlementProblem, ...] = ()
    new_events_possible: bool

    @model_validator(mode="after")
    def _consistent(self) -> PlanCheck:
        if self.passed != (self.stage == "passed"):
            raise ValueError("只有 stage = passed 时才算通过")
        if self.passed:
            if self.status is not None or self.problems:
                raise ValueError("通过的检查不带状态或问题")
        else:
            if self.status not in ("plan_infeasible", "input_error", "data_error"):
                raise ValueError(
                    f"没有通过的检查只能是计划不可行、输入错误或资料错误，得到 {self.status}"
                )
            if not self.problems:
                raise ValueError("没有通过的检查必须说明是什么问题")
        return self


class VisiblePosition(ContractModel):
    """**代理可见**的一份岗位（E1-5A）：哪份工作、职业、雇主、领到工资的月份。"""

    opportunity_id: str
    occupation: OccupationId
    employer_id: str | None = None
    months: tuple[int, ...] = ()


class VisibleLearning(ContractModel):
    """**代理可见**的一门课一年的学习事实（E1-5A）：实付学费、实际参加的月份、进度、完成与授予。"""

    opportunity_id: str
    fee_charged: Money
    months_attended: tuple[int, ...]
    progress_after: int
    months_required: int
    completed_month: int | None = None
    granted: tuple[str, ...] = ()
    already_held: tuple[str, ...] = ()
    usable_from_year: int | None = None


class VisibleCareerYear(ContractModel):
    """**代理可见**的一年学习与岗位事实（E1-5A）。与内部的 ``CareerYear`` 是不同的类型。"""

    learning: tuple[VisibleLearning, ...] = ()
    positions: tuple[VisiblePosition, ...] = ()
    current_position_ids: tuple[str, ...] = ()


class VisibleGoalEvent(ContractModel):
    """**代理可见**的一条目标事件（E1-5B）。"""

    goal_id: str
    kind: str
    year_index: int
    month: int | None = None
    note: str


class VisibleGoalProgress(ContractModel):
    """**代理可见**的目标进展（E1-5B）：实际余额、当前报价、还差多少。

    ``cash_covers_quote`` 只是比较，不代表扣除未来生活费后负担得起。
    """

    goal_id: str
    month: int | None = None
    cash: Money
    quote: Money | None = None
    shortfall: Money | None = None
    cash_covers_quote: bool | None = None
    status: str


class VisibleSavingsYear(ContractModel):
    """**代理可见**的一年目标事实（E1-5B）：事件、年初与月末进展、兑现依据与未兑现原因。

    与内部的 ``SavingsYear`` 是不同的类型。
    """

    events: tuple[VisibleGoalEvent, ...] = ()
    progress: tuple[VisibleGoalProgress, ...] = ()
    realization: str | None = None
    notes: tuple[str, ...] = ()


class VisibleTreatmentResult(ContractModel):
    """**代理可见**的一个病月的治疗结果（E1-5C）：计划怎么选的、实际怎样、付了多少、就医多久。"""

    severity: str
    plan_choice: str
    status: str
    status_label: str
    amount_paid: Money | None = None
    visit_hours: ExactAmount | None = None
    note: str


class VisiblePause(ContractModel):
    """**代理可见**的一项暂停（E1-5C）：哪项活动、为什么暂停。"""

    opportunity_id: str
    why: str


class VisibleHealthMonth(ContractModel):
    """**代理可见**的一个月健康事实（E1-5C）：月初状态、是否新发疾病、病假、暂停了什么、治疗结果、恢复时间、
    月末恢复与否。**没有**概率、抽签、风险档或事件来源。"""

    month: int
    record: str
    state: str
    state_label: str
    new_illness: str | None = None
    sick_leave: bool = False
    paused: tuple[VisiblePause, ...] = ()
    treatment: VisibleTreatmentResult | None = None
    recovery_hours: ExactAmount | None = None
    transition: str
    notes: tuple[str, ...] = ()


class VisibleHealthYear(ContractModel):
    """**代理可见**的一年健康事实（E1-5C）。与内部的 ``HealthYear`` 是不同的类型。"""

    months: tuple[VisibleHealthMonth, ...] = ()
    stop: str | None = None
    notes: tuple[str, ...] = ()
    #: 收尾修订：这一年自己的年度计划声明"生病月份取消"的购买（已经发生的取消写在 ``months`` 与执行记录里）。没有用到时不序列化。
    declared_purchase_cancellations: tuple[str, ...] = Field(
        default=(), exclude_if=lambda v: v == ()
    )


class VisibleParticipationSpan(ContractModel):
    """几个月里**完全相同**的实际参加：每月几次、每月的参与小时与准备往返小时、用到的装备与套餐本次提供的能力。"""

    months: tuple[Month, ...] = Field(min_length=1)
    sessions_each_month: float
    participation_hours_each_month: float
    overhead_hours_each_month: float
    equipment_used: tuple[str, ...] = Field(default=(), exclude_if=lambda v: v == ())
    kit_capabilities: tuple[str, ...] = Field(default=(), exclude_if=lambda v: v == ())


class VisibleNotDone(ContractModel):
    """没有参加的月份：生病暂停（paused）或没能进行（failed，例如缺少装备能力），原因相同的合成一条。"""

    months: tuple[Month, ...] = Field(min_length=1)
    status: Literal["paused", "failed"]
    reason: str | None = Field(default=None, exclude_if=lambda v: v is None)


class VisibleParticipation(ContractModel):
    """**代理可见**的一项活动这一年的实际参与（DECISIONS/0041）：逐项取自结算的参与记录（每项活动每个请求月一条），
    按活动汇总；相同的月份合成一段（R1：只省重复）。合计 = 各段月数 × 每月次数或小时。没有满足感、匹配或评分系数。"""

    opportunity_id: str
    done: tuple[VisibleParticipationSpan, ...] = Field(default=(), exclude_if=lambda v: v == ())
    not_done: tuple[VisibleNotDone, ...] = Field(default=(), exclude_if=lambda v: v == ())
    sessions_total: float
    participation_hours_total: float
    overhead_hours_total: float


class VisibleFeedback(ContractModel):
    """**代理可见**的事实反馈。与实际结果、内部评分是三个不同的类型。

    只有事实：余额、全年税前收入、税、支出、各项行动实际执行了哪些月、为什么被拒。
    没有满足感、分数或排序。
    """

    year_index: YearIndex
    cash_balance: Money
    life_status: LifeStatus = "active"
    income_gross: Money | None = None
    income_tax: Money | None = None
    spending: Money | None = None
    executed: tuple[ExecutedItem, ...] = ()
    notes: tuple[Annotated[str, Field(max_length=MAX_SUMMARY_CHARS)], ...] = ()
    #: 逐月资源事实（精力字段按许可清空）。
    resources: tuple[VisibleMonthResources, ...]
    #: 上一年的学习与岗位事实、资金目标事实、健康事实（病情、病假、暂停、治疗与恢复）。
    career: VisibleCareerYear
    savings: VisibleSavingsYear
    health: VisibleHealthYear
    #: 生活扩展（DECISIONS/0041）：休闲活动的实际参与。没有活动时不序列化。
    participation: tuple[VisibleParticipation, ...] = Field(
        default=(), exclude_if=lambda v: v == ()
    )

    @field_validator("cash_balance")
    @classmethod
    def _life_money(cls, v: Money) -> Money:
        require_life_numeraire(v.numeraire, "反馈余额")
        return v


class InternalYearScore(ContractModel):
    """**内部**年度评分（评分侧）。不是旧的 ``GroundTruth`` / ``WelfareLevel``。

    评分映射是未定政策 ``score_mapping``，所以本轮**只能**构造测试夹具：``status``
    没有 ``official`` 这个选项。等八条政策定下、协议变成 formal，再加。
    """

    person_id: IdStr
    clone_id: IdStr
    year_index: YearIndex
    protocol_id: IdStr
    scoring_scale_version: str
    status: Literal["synthetic_fixture_only"]
    values: dict[KeyStr, float]


LifeProtocol.model_rebuild()
# E1-5C：状态与结果引用了在它们之后定义的停止记录与健康记录
PersonYearState.model_rebuild()
EnvironmentOutcome.model_rebuild()
