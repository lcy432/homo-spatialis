"""E1-7A 代理规划与运行的数据形状（`DECISIONS/0030`）。只有形状、局部约束与内容身份，没有经济数学。

一年的规划循环：

    安全观察 + 获准的事实记忆 + 公开规则 →（规划器）→ 需求摘要 + 计划理由 + 年度行动
        → 执行前检查（没有新随机事件的试算，什么都不提交）→ 通过：环境按月执行；可修改的问题：带安全诊断再问一次（有上限）

这里分三类东西：

* **代理看得到的**（``Visible*``、``PlannerOutput``）：诊断、记忆、城市事实与输出格式。它们只由逐字段投影产生（投影在
  ``agentlayer.life_planning``），最后照样经过 ``agentlayer.life_views.assert_payload_clean`` 扫描。
* **运行配置**（``LifeRunConfig``）：名单、事实表、信息条件、修订上限、全局调用上限、运行顺序、规划器身份与提示模板版本。
  开跑之前固定并记进运行清单；改了任何一项就不能冒充原来那次运行继续。
* **运行记录**（``AttemptRecord``、``AcceptedYear``、``CloneRunRecord``、``RunProgress``、``RunManifest``）：审计与恢复用的内部
  对象，不进提示（登记在 ``internal_planning_models``）。

需求摘要与计划理由是**代理自述**：它们解释计划，但不改变余额、资格、进度或满足感——金额、资格、完成状态、病情与评分只由
环境生成。只改摘要或理由、行动完全相同时，生活结果相同。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from common.config import is_dated_snapshot
from common.ids import CityId
from common.life_aggregation_contracts import RunFailure
from common.life_contracts import (
    MAX_RATIONALE_CHARS,
    ActionItem,
    ContractModel,
    EmergencyPlan,
    ExactAmount,
    IdStr,
    MinimumNeed,
    Month,
    SavingsGoalDirective,
    YearAction,
    YearIndex,
    canonical_fingerprint,
)
from common.life_evaluation_contracts import PlanningStop
from common.money import Money

#: 规划接口、运行配置、提示模板与记忆政策的版本。改其中任何一项的含义就换名字。
#: product-planning-v1（2026-10-19）：删除旧版本之后的请求结构——观察里不再有七维画像、软偏好、旧的年度时间与精力字段和
#: 数值技能这些只为旧协议存在的键；其余与 e1-7b-planning-v1 相同。
PLANNING_VERSION = "product-planning-v1"
RUN_CONFIG_VERSION = "e1-7a-run-v1"
#: 带真实模型与付费预算的运行配置（E1-7B）。mock 运行照旧用 e1-7a-run-v1。
RUN_CONFIG_VERSION_LIVE = "e1-7b-run-v1"
#: 提示模板与它带的行动说明书（DECISIONS/0031、0033、0034）。提示模板 = 系统提示、用户开头与输出格式 + 一版行动说明书。
#: 改了任何文字就换版本号，并在 ``PROMPT_TEMPLATE_GUIDES`` 里登记；运行配置的 prompt_template 只能写这里的键。
PROMPT_TEMPLATE_VERSION = "e3b-product-prompt-v1"
ACTION_GUIDE_VERSION = "e3b-product-action-guide-v1"
PROMPT_TEMPLATE_GUIDES: dict[str, str] = {PROMPT_TEMPLATE_VERSION: ACTION_GUIDE_VERSION}
MEMORY_POLICY_VERSION = "e1-life-memory-v2"
#: 执行修订（DECISIONS/0030 第六节、0031 第八节）：恢复前逐条核对归档记录、核对实际规划器的有效设置、服务记录一致性检查、实际
#: 预算与声明及账本历史的核对。记在运行清单里；改执行语义就换修订号。
EXECUTION_REVISION = "e1-7b-r2"
#: 可见税制说明的版本（按实际第一档税率写说明）。
VISIBLE_TAX_VERSION = "e1-7b-visible-tax-v2"
#: 输入 token 的保守估计规则（E1-7B，AG-13）：ASCII 字符每个记 1 个 token（字节级 BPE 的一个 token 至少覆盖 1 个字节，所以这是
#: ASCII 部分的严格上界），非 ASCII 字符每个记 2 个 token（千问词表里常用汉字多是 1 个 token，按 2 留余量；罕见字可能更多，
#: 所以整体**不是**严格上界），每条消息另加 16、每个请求另加 16。它不是实际 token；实际 token 只认供应商返回的用量。
TOKEN_ESTIMATE_RULE = "ascii1_nonascii2_v1"

#: 写给开发者、不该给分身看的说明（E1-7B 第 4.4 节）。真实模型运行在开跑前扫城市目录与事实表（``ledger.life_run_config``）、
#: 发送前扫最终请求的城市部分（``agentlayer.life_planning.developer_note_problems``）；这是兜底检查，主要的办法是配置里本来
#: 就不写这种话。
DEVELOPER_NOTE_MARKERS: tuple[str, ...] = (
    "只用于资格测试",
    "资格测试",
    "仅供测试",
    "测试用",
    "开发者备注",
    "TODO",
    "FIXME",
)

#: 输出与修订请求的长度上限（工程限制，不是心理学结论；登记在参数目录 AG-05）。
MAX_NEED_SUMMARY_CHARS = 400
MAX_RESPONSE_CHARS = 20000
MAX_PREVIOUS_OUTPUT_CHARS = 4000
#: 记忆政策 e1-life-memory-v2（AG-04；v2 多了活动参与事实，DECISIONS/0041）：事实按年保留最近 3 年、每年至多 8 条、每条 240 字；代理自述只保留上一年，摘要 200 字、
#: 理由 400 字。超出部分按固定规则截断并写明，不静默丢弃；用户陈述（点数、硬要求、文字）不经过这一层，不会被截断。
MEMORY_FACT_YEARS = 3
MAX_MEMORY_FACTS_PER_YEAR = 8
MAX_MEMORY_FACT_CHARS = 240
SELF_REPORT_YEARS = 1
MAX_SELF_REPORT_NEED_CHARS = 200
MAX_SELF_REPORT_RATIONALE_CHARS = 400
#: 每年修订次数的硬上限（配置只能在 0–5 之间选；默认 2）。
MAX_REVISIONS_LIMIT = 5

_SHA256 = r"^[0-9a-f]{64}$"
Sha256 = Annotated[str, Field(pattern=_SHA256)]


# ---------------------------------------------------------------------- 代理的输出


class PlannedYear(ContractModel):
    """代理输出里的年度行动：能解析成现有 ``YearAction`` 的结构化变量。

    **不含身份字段**——人、分身与年份由运行器按请求填写，代理写了就是格式错误（``extra = forbid``）。理由不在这里，在
    ``PlannerOutput.rationale``。"""

    items: tuple[ActionItem, ...]
    minimum_fallback: tuple[MinimumNeed, ...] = ()
    savings_goal: SavingsGoalDirective | None = None
    emergency: EmergencyPlan | None = None


class PlannerOutput(ContractModel):
    """一次规划响应（JSON 对象）的全部字段。多写任何字段（例如"今年多存了多少钱"）都是格式错误——结果由环境结算。"""

    need_summary: str = Field(min_length=1, max_length=MAX_NEED_SUMMARY_CHARS)
    rationale: str = Field(min_length=1, max_length=MAX_RATIONALE_CHARS)
    action: PlannedYear

    def year_action(self, person_id: str, clone_id: str, year_index: int) -> YearAction:
        """按请求的身份变成结算能读的 ``YearAction``。理由放进 ``YearAction.rationale``（结算不读它）；需求摘要只留在规划记录。"""
        return YearAction(
            person_id=person_id,
            clone_id=clone_id,
            year_index=year_index,
            items=self.action.items,
            minimum_fallback=self.action.minimum_fallback,
            rationale=self.rationale,
            savings_goal=self.action.savings_goal,
            emergency=self.action.emergency,
        )


# ---------------------------------------------------------------------- 代理可见：诊断、修订、记忆、城市事实


class VisibleContributor(ContractModel):
    """资源超限时占用最多的一项：类别、哪一项（机会编号或家务）与它的小时或负担。都是公开成本。"""

    category: str = Field(min_length=1, max_length=40)
    item: str = Field(min_length=1, max_length=80)
    amount: ExactAmount


class VisibleDiagnostic(ContractModel):
    """一条**安全诊断**：上一份计划为什么没有通过执行前检查。只有获准公开、有助于改计划的信息——没有内部评分、隐藏参数、
    未来事件，也不告诉代理"换成某方案分数最高"。精力额度、用量与超出量只在许可精力可见时给出。"""

    source: Literal["response_format", "plan_rules", "no_event_trial"]
    code: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=600)
    month: Month | None = None
    item_order: Annotated[int, Field(ge=0)] | None = None
    opportunity_id: str | None = Field(default=None, max_length=80)
    resource: Literal["time", "energy"] | None = None
    limit: ExactAmount | None = None
    used: ExactAmount | None = None
    excess: ExactAmount | None = None
    contributors: tuple[VisibleContributor, ...] = ()
    cash_available: Money | None = None
    required: Money | None = None


class VisibleRevision(ContractModel):
    """修订阶段的附加内容：这是第几份候选、还能再改几次、上一份输出原文（截断）与它的安全诊断。"""

    attempt: Annotated[int, Field(ge=2)]
    revisions_left_after_this: Annotated[int, Field(ge=0)]
    previous_output_text: str = Field(max_length=MAX_PREVIOUS_OUTPUT_CHARS)
    previous_output_truncated: bool
    diagnostics: tuple[VisibleDiagnostic, ...] = Field(min_length=1)
    note: str = Field(min_length=1, max_length=400)


class MemoryFact(ContractModel):
    """一条**事实**记忆：取自那一年的可见反馈（环境记录），不是代理的自述。"""

    kind: Literal["fact"] = "fact"
    year_index: YearIndex
    text: str = Field(min_length=1, max_length=MAX_MEMORY_FACT_CHARS)


class SelfReport(ContractModel):
    """一条**代理自述**：上一年被接受的那份计划里的需求摘要与理由。它不是事实，也不让资格或进度提前生效。"""

    kind: Literal["agent_self_report"] = "agent_self_report"
    year_index: YearIndex
    need_summary: str = Field(min_length=1, max_length=MAX_SELF_REPORT_NEED_CHARS)
    rationale: str = Field(min_length=1, max_length=MAX_SELF_REPORT_RATIONALE_CHARS)
    truncated: bool


class VisibleMemory(ContractModel):
    """本分身获准看到的记忆（规划上下文里有长度上限；完整轨迹在审计记录里）。"""

    policy_version: Literal["e1-life-memory-v2"]
    rule: str = Field(min_length=1, max_length=600)
    facts: tuple[MemoryFact, ...] = ()
    self_reports: tuple[SelfReport, ...] = ()
    omitted: tuple[str, ...] = ()


class VisibleCityFact(ContractModel):
    """**代理可见**的一条城市背景事实（逐字段取自城市事实表；没有换算系数或评分参数）。"""

    fact_id: str
    label: str
    meaning: str
    status: Literal["known", "unavailable", "unknown"]
    status_label: str
    number: float | None = None
    category: str | None = None
    unit: str
    period: str
    scope: str
    source_kind: str
    source_label: str
    source_note: str | None = None
    unknown_reason: str | None = None


class VisibleTaxBracket(ContractModel):
    """**代理可见**的一个税档：全年累计应税收入在 ``lower`` 以上、下一档下限以下的部分按 ``rate`` 计。"""

    lower: float
    rate: float


class VisibleTaxRules(ContractModel):
    """**代理可见**的本城合成税制（E1-7B）。从环境结算实际使用的那一份税声明逐字段投影，不手写另一份税表；运行器在每次
    请求之前与实际税绑定核对，不一致就不发送。这是合成测试替身，不是任何真实国家或地区的税表。"""

    version: Literal["e1-7b-visible-tax-v2"]
    policy_id: str
    status: str
    status_label: str
    money_basis_id: str
    jurisdiction: str
    period: str
    withholding: str
    brackets: tuple[VisibleTaxBracket, ...]
    rules: tuple[str, ...]
    note: str


class VisibleGuideExample(ContractModel):
    """行动说明书里的一个**局部**示例：只演示写法与规则，不是一份完整的年度计划，更不是"最好"的计划。"""

    label: str
    fragment: dict[str, Any]


class VisibleGuideSection(ContractModel):
    topic: str
    rules: tuple[str, ...]
    examples: tuple[VisibleGuideExample, ...] = ()


class VisibleActionGuide(ContractModel):
    """**代理可见**的年度行动说明书（E1-7B）：支持的字段、单位、月份含义、数量限制与少量合法的局部示例。"""

    version: str = Field(min_length=1, max_length=60)
    sections: tuple[VisibleGuideSection, ...]


# ---------------------------------------------------------------------- 运行配置


class PlannerIdentity(ContractModel):
    """规划器的身份。``mock`` 是可控的模拟代理（本轮唯一支持的种类），**不是**模型能力的证据；``llm`` 留给 E1-7B（必须带
    日期戳的模型快照），本轮的运行器拒绝它。mock 的身份与用量单独记录，不写进真实模型的调用日志。"""

    kind: Literal["mock", "llm"]
    planner_id: IdStr
    version: str = Field(min_length=1, max_length=60)
    rules_version: str | None = Field(default=None, max_length=60)
    model_snapshot: str | None = Field(default=None, max_length=80)
    note: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def _kind_consistent(self) -> PlannerIdentity:
        if self.kind == "mock" and self.model_snapshot is not None:
            raise ValueError("mock 规划器没有模型快照；不要把它写成真实模型")
        if self.kind == "llm" and not self.model_snapshot:
            raise ValueError("真实模型必须写带日期戳的模型快照（CLAUDE 硬规则 6）")
        return self


MockBehavior = Literal[
    "overcommit_first_draft",
    "unqualified_first_draft",
    "malformed_output",
    "raise_error",
    "alternate_wording",
    "buy_hobby_gear",
]


class MockScript(ContractModel):
    """给某个分身的 mock 加一段受控行为（演示与测试用，进入运行身份）：

    * ``overcommit_first_draft``：这些年份的第一份草稿保留现职、再加一份更好的岗位（两份工作），资源可能超限；
    * ``unqualified_first_draft``：第一份草稿选一个年初还不合格的岗位；
    * ``malformed_output``：这些年份每次都返回不是 JSON 的文字（用来演示修订耗尽）；
    * ``raise_error``：这些年份调用时抛出意外异常（用来演示程序错误暂停批次）；
    * ``alternate_wording``：行动不变，只换需求摘要与理由的措辞；
    * ``buy_hobby_gear``：这些年份的草稿在第 1 月买下目录里每一件可得的爱好装备（演示期末持有的装备与估值范围，DECISIONS/0041）。
    """

    clone_id: IdStr
    behavior: MockBehavior
    years: tuple[YearIndex, ...] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=300)


@dataclass(frozen=True)
class ModelLimits:
    """一个模型已核对的长度上限：单次输入、单次输出、输入 + 输出合计（上下文）。没有写的一项就是没有核对，不检查。"""

    max_input: int | None
    max_output: int | None
    context: int | None
    basis: str


#: 已核对的模型长度上限（DECISIONS/0041 R1）。运行配置的单次输入上限加输出上限不能超过上下文；没有登记的模型只受字段本身的
#: 上限约束。来源是发送之前在本机记录的官方页面（``runs/*/pricing/``，本机证据）。
MODEL_LIMITS: dict[str, ModelLimits] = {
    "qwen3.8-flash": ModelLimits(
        max_input=991_808,
        max_output=131_072,
        context=None,
        basis="百炼官方模型页（2026-10-09 更新、2026-10-18 核对）：最大输入 991,808、最大输出 131,072；页面没有按输入长度分档",
    ),
    "claude-sonnet-5-5": ModelLimits(
        max_input=None,
        max_output=128_000,
        context=1_000_000,
        basis="Claude 官方价格页（2026-10-18 核对）：Claude 4.6 及之后的型号（Haiku 5.5 除外）按标准价提供完整 1M 上下文；"
        "最大输出 128,000",
    ),
    "claude-haiku-5-5": ModelLimits(
        max_input=None,
        max_output=128_000,
        context=200_000,
        basis="本机没有 Haiku 5.5 上下文长度的核对记录：按当前 Claude 型号至少 200,000 的保守下界（这是假设）；提示超过 100,000 "
        "token 的请求按长上下文价计费（官方价格页，2026-10-18 核对）",
    ),
}


class LlmSettings(ContractModel):
    """真实模型规划器的设定（E1-7B，DECISIONS/0031）。全部显式写在运行配置里；实际规划器必须报出同一份（``effective_spec``）。

    模型行为设置：模型快照、温度、思考开关、输出格式、输出上限。工程保护：请求超时、SDK 自动重试（固定关闭，由应用层统一控制）、
    每份候选的服务重试次数与等待、单次输入的估计上限与估计规则。``model_role`` 指向 ``configs/models.yaml`` 里的角色，服务商、端点
    与模型快照必须与那里一致；凭证只在调用时从环境变量或 .env 读取，从不写进这里或任何记录。

    产品规划器（2026-10-13，DECISIONS/0032）：``model_role`` 可以是 ``product_planner``；``version_policy`` 写 ``rolling`` 只允许在这个
    角色上（供应商没有发布带日期的快照时），并且模型名不能带日期。不写 ``version_policy`` 等于 ``dated_snapshot``（模型名必须带日期，
    旧配置照旧、不序列化这一栏，所以旧配置的设定指纹不变）。返回模型的核对规则见 ``agentlayer.life_llm.returned_model_accepted``。

    测试规划器（2026-10-18，DECISIONS/0035）：``model_role: test_planner``（Claude Haiku 5.5，默认）或 ``test_planner_sonnet``（Claude
    Sonnet 5.5，Haiku 之后的对照测试）、``provider: claude_agent_sdk``——真实 LLM 测试与试跑用 Claude
    Haiku 5.5，经 Claude Agent SDK 单轮调用（一个输入、跑一轮、一个输出；不给工具、不读设置，见 ``agentlayer.life_llm``）。这个服务商
    不接受温度、关不掉思考（命令行固定为自适应），也没有接口级 JSON 模式，所以 ``temperature`` 与 ``enable_thinking`` 不写；
    ``thinking`` 写 ``adaptive``、``effort`` 写明档位；``response_format`` 写 ``prompt_only``（只靠提示要求一个 JSON 对象，解析照旧）。
    千问的三栏照旧必填、取值不变；新加的栏在千问设定里不写、不序列化，所以 Max 与 Flash 的设定指纹不变。"""

    model_role: Literal["base_b", "product_planner", "test_planner", "test_planner_sonnet"]
    provider: Literal["dashscope", "claude_agent_sdk"]
    model: str = Field(min_length=1, max_length=80)
    base_url: str = Field(min_length=1, max_length=200)
    temperature: Annotated[float, Field(ge=0, le=2)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    enable_thinking: Literal[False] | None = Field(default=None, exclude_if=lambda v: v is None)
    response_format: Literal["json_object", "prompt_only"]
    #: 千问最多 4,096、超时最多 300 秒（E1-7B 起不变）；Claude 最多到模型自身的最大输出 128,000（Haiku 5.5 / Sonnet 5.5，命令行不设时也自动填这个数）、
    #: 超时最多 1,800 秒（DECISIONS/0035：所有者 2026-10-18 要求 Claude 配置不另卡输出）
    max_output_tokens: Annotated[int, Field(ge=1, le=128000)]
    timeout_seconds: Annotated[float, Field(gt=0, le=1800)]
    sdk_max_retries: Literal[0]
    service_retries: Annotated[int, Field(ge=0, le=1)]
    retry_wait_seconds: Annotated[float, Field(ge=0, le=30)]
    max_input_tokens: Annotated[int, Field(ge=1, le=991808)]
    token_estimate: Literal["ascii1_nonascii2_v1"]
    note: str | None = Field(default=None, max_length=400)
    version_policy: Literal["dated_snapshot", "rolling"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    #: 只在 claude_agent_sdk：思考方式（命令行固定为自适应）与 effort 档位（DECISIONS/0035）
    thinking: Literal["adaptive"] | None = Field(default=None, exclude_if=lambda v: v is None)
    #: 只在 claude_agent_sdk：认证方式。不写 = API 密钥（ANTHROPIC_API_KEY）；claude_subscription = 用本机已登录的 Claude 订阅
    #: （`claude auth login`），不要密钥（DECISIONS/0035，所有者 2026-10-18）
    auth: Literal["api_key", "claude_subscription"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @property
    def effective_version_policy(self) -> str:
        return self.version_policy or "dated_snapshot"

    @model_validator(mode="after")
    def _provider_consistent(self) -> LlmSettings:
        if self.provider == "dashscope":
            if (
                self.temperature is None
                or self.enable_thinking is not False
                or self.response_format != "json_object"
                or self.thinking is not None
                or self.effort is not None
                or self.auth is not None
            ):
                raise ValueError(
                    "dashscope 的设定必须写温度、enable_thinking: false 与 response_format: json_object，不写 thinking 与 effort"
                )
            if self.max_output_tokens > 4096 or self.timeout_seconds > 300:
                raise ValueError("dashscope 的输出上限最多 4,096、超时最多 300 秒（E1-7B 起不变）")
            if self.model_role in ("test_planner", "test_planner_sonnet"):
                raise ValueError(
                    "测试规划角色 test_planner / test_planner_sonnet 只用 claude_agent_sdk（DECISIONS/0035）"
                )
        else:
            if self.temperature is not None or self.enable_thinking is not None:
                raise ValueError(
                    "claude_agent_sdk 不接受温度，也关不掉思考：temperature 与 enable_thinking 都不写"
                )
            if (
                self.response_format != "prompt_only"
                or self.thinking != "adaptive"
                or self.effort is None
            ):
                raise ValueError(
                    "claude_agent_sdk 的设定必须写 response_format: prompt_only、thinking: adaptive 与 effort 档位"
                )
            if self.model_role not in ("test_planner", "test_planner_sonnet"):
                raise ValueError(
                    "claude_agent_sdk 只用在测试规划角色 test_planner / test_planner_sonnet 上（DECISIONS/0035）"
                )
        return self

    @model_validator(mode="after")
    def _fits_model_limits(self) -> LlmSettings:
        """单次输入上限与输出上限都在模型已核对的长度之内，合计不超过上下文（DECISIONS/0041 R1）。"""
        lim = MODEL_LIMITS.get(self.model)
        if lim is None:
            return self
        if lim.max_input is not None and self.max_input_tokens > lim.max_input:
            raise ValueError(
                f"{self.model} 的单次输入上限写了 {self.max_input_tokens}，模型最多 {lim.max_input}（{lim.basis}）"
            )
        if lim.max_output is not None and self.max_output_tokens > lim.max_output:
            raise ValueError(
                f"{self.model} 的输出上限写了 {self.max_output_tokens}，模型最多 {lim.max_output}（{lim.basis}）"
            )
        total = self.max_input_tokens + self.max_output_tokens
        if lim.context is not None and total > lim.context:
            raise ValueError(
                f"{self.model} 的输入上限 {self.max_input_tokens} + 输出上限 {self.max_output_tokens} = {total}，超过上下文 "
                f"{lim.context}（{lim.basis}）"
            )
        return self

    @model_validator(mode="after")
    def _version_policy_consistent(self) -> LlmSettings:
        if self.effective_version_policy == "dated_snapshot":
            if not is_dated_snapshot(self.model):
                raise ValueError(
                    f"模型 {self.model} 没有快照日期：固定版本（CLAUDE 硬规则 6）；只有产品与测试规划角色可以显式声明滚动型号"
                )
        else:
            if self.model_role not in ("product_planner", "test_planner", "test_planner_sonnet"):
                raise ValueError(
                    "滚动型号（version_policy: rolling）只允许在产品规划角色 product_planner 与测试规划角色 test_planner 上，"
                    "研究角色仍要固定版本"
                )
            if is_dated_snapshot(self.model):
                raise ValueError(
                    f"模型 {self.model} 带快照日期：应当写 version_policy: dated_snapshot，不要标成滚动型号"
                )
        return self


class PriceTable(ContractModel):
    """一份已核对的报价（E1-7B）：按它预留与估算费用。只是应用侧的估算，不是供应商账单。"""

    price_version: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=80)
    region: str = Field(min_length=1, max_length=40)
    currency: Literal["CNY", "USD"]
    input_per_million: Annotated[float, Field(gt=0)]
    output_per_million: Annotated[float, Field(gt=0)]
    source_url: str = Field(min_length=1, max_length=300)
    checked_on: str = Field(min_length=10, max_length=10)
    rule: str = Field(min_length=1, max_length=400)
    #: 分档报价的这一档最多适用多少输入 token（DECISIONS/0035，例如 Haiku 5.5 的提示不超过 10 万 token）。不写 = 不分档；写了时，
    #: 输入估计超过它就不发送，实际输入超过它就按"超出报价档"停下，不按低档冒充。
    max_input_tokens_at_price: Annotated[int, Field(ge=1)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )


class PaidBudget(ContractModel):
    """本轮所有付费请求共用的预算（E1-7B）：先导、主试跑、诊断与服务重试都记在同一本账（``ledger_dir``）里。换运行目录、
    重启进程或换 run_id 都不会得到新的额度——额度写在账本自己的头文件里，第一次建立之后不改。

    额度可选（2026-10-18，DECISIONS/0035，所有者要求）：``max_requests`` 与费用上限都可以不写。不写就不设那一项上限——账本照常
    发送前预留、返回后核算（记账），只是不因次数或费用拦截；报价的分档上限照旧检查（那是报价对不对，不是额度）。写了上限的旧配置
    与旧账本逐字节不变。"""

    ledger_dir: str = Field(min_length=1, max_length=300)
    max_requests: Annotated[int, Field(ge=1, le=10_000)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    #: 费用上限与报价同一币种：人民币报价写 ``max_cost_cny``，美元报价写 ``max_cost_usd``（DECISIONS/0035），最多写其中一个；
    #: 都不写 = 不设费用上限。
    max_cost_cny: Annotated[float, Field(gt=0, le=10_000)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    max_cost_usd: Annotated[float, Field(gt=0, le=10_000)] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    price: PriceTable

    @model_validator(mode="after")
    def _one_currency(self) -> PaidBudget:
        other = {"CNY": self.max_cost_usd, "USD": self.max_cost_cny}
        if other[self.price.currency] is not None:
            raise ValueError(
                f"报价是 {self.price.currency}：费用上限只写 max_cost_{self.price.currency.lower()}（或不写），不能写另一种币种"
            )
        return self

    @property
    def currency(self) -> str:
        return self.price.currency

    @property
    def max_cost(self) -> float | None:
        """费用上限（报价币种）；None = 不设费用上限。"""
        return self.max_cost_cny if self.price.currency == "CNY" else self.max_cost_usd


class PlannerSpec(ContractModel):
    """规划器的设定：身份与影响行为的设置。运行配置里写的是声明；规划器对象用 ``effective_spec()`` 报出实际生效的设定，运行器在
    第一次规划调用之前比较两者的 ``behavior_fingerprint``（E1-7A 收尾 F2）。mock 的设置是受控行为；真实模型（E1-7B）的设置是
    ``LlmSettings``，同样进入行为内容。"""

    identity: PlannerIdentity
    scripts: tuple[MockScript, ...] = ()
    llm: LlmSettings | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _unique(self) -> PlannerSpec:
        keys = [(s.clone_id, s.behavior) for s in self.scripts]
        if len(keys) != len(set(keys)):
            raise ValueError("同一个分身的同一种受控行为写了两次")
        if self.scripts and self.identity.kind != "mock":
            raise ValueError("受控行为只属于 mock 规划器")
        if (self.identity.kind == "llm") != (self.llm is not None):
            raise ValueError("真实模型规划器必须写 llm 设定；mock 不能写")
        if self.llm is not None and self.llm.model != self.identity.model_snapshot:
            raise ValueError("llm 设定里的模型与身份里的模型快照不同")
        return self

    def behavior_content(self) -> dict[str, object]:
        """影响规划行为的内容：身份（种类、编号、版本、规则版本、模型快照；不含说明 ``note``）与每段受控行为的分身、行为与
        年份（不含说明）。mock 按"这个分身这一年有哪些行为"的集合取用受控行为，所以受控行为之间的先后与年份的先后、重复都不影响
        行为——这里按分身与行为排序、年份去重排序，只改写法不改身份。真实模型（E1-7B）另加 ``llm`` 设定的全部字段（说明除外）；
        mock 没有这一项，它的行为内容与 E1-7A 收尾时相同。"""
        content: dict[str, object] = {
            "identity": self.identity.model_dump(mode="json", exclude={"note"}),
            "scripts": sorted(
                (
                    {"clone_id": s.clone_id, "behavior": s.behavior, "years": sorted(set(s.years))}
                    for s in self.scripts
                ),
                key=lambda d: (str(d["clone_id"]), str(d["behavior"])),
            ),
        }
        if self.llm is not None:
            content["llm"] = self.llm.model_dump(mode="json", exclude={"note"})
        return content

    @property
    def behavior_fingerprint(self) -> str:
        return canonical_fingerprint(self.behavior_content())


class InformationSetting(ContractModel):
    """这次运行的信息条件（与 ``agentlayer.life_views.ObservationPermission`` 逐项对应，**没有默认值**）。"""

    condition: Literal["product_stated_profile"]
    show_stated_dimension_points: bool
    show_hard_requirements: bool
    show_free_text: bool
    show_energy_level: bool
    show_stated_material_points: bool
    permission_version: str = Field(min_length=1, max_length=60)
    #: 六维产品画像的独立健康回答是否可见（DECISIONS/0034）。
    show_health_answers: bool
    #: 确认的休闲兴趣与口味是否可见（DECISIONS/0041）。
    show_interests: bool


class PlanningPolicy(ContractModel):
    """每年的规划与修订、全局调用上限与运行顺序（开跑前固定并记录；AG-06、AG-07）。"""

    plans_per_year: Literal[1]
    max_revisions_per_year: Annotated[int, Field(ge=0, le=MAX_REVISIONS_LIMIT)]
    global_call_limit: Annotated[int, Field(ge=1, le=100_000)]
    run_order: Literal["roster_order"]
    #: 模型服务的网络重试与计划修订是两件事，分开计数。mock 没有网络，固定 0；真实模型（E1-7B）每份候选最多 1 次，必须与
    #: ``LlmSettings.service_retries`` 相同。不伪造 token 或费用。
    transport_retries: Literal[0, 1]
    note: str | None = Field(default=None, max_length=400)

    @property
    def attempts_per_year(self) -> int:
        return self.plans_per_year + self.max_revisions_per_year


class LifeRunConfig(ContractModel):
    """一次 E1-7A 运行的配置（YAML 顶层键 ``run``）。开跑之前写好；运行清单记下它的内容身份。"""

    run_config_id: IdStr
    status: Literal["exploratory_synthetic"]
    version: Literal["e1-7a-run-v1", "e1-7b-run-v1"]
    roster: str = Field(min_length=1, max_length=300)
    city_facts: str = Field(min_length=1, max_length=300)
    information: InformationSetting
    planning: PlanningPolicy
    planner: PlannerSpec
    prompt_template: str = Field(min_length=1, max_length=60)
    memory_policy: Literal["e1-life-memory-v2"]
    #: E1-7B：真实模型运行的付费预算（mock 运行不写）。
    paid_budget: PaidBudget | None = Field(default=None, exclude_if=lambda v: v is None)
    #: 计费可选（2026-10-18，DECISIONS/0035）：真实模型运行不记账时必须写明理由（例如用订阅额度、只是本机抽查），与 paid_budget
    #: 二选一，进入运行配置的内容身份。不记账时仍受全局调用上限约束，费用照实记为"未计量"，不记零。
    unmetered_reason: str | None = Field(
        default=None, min_length=8, max_length=300, exclude_if=lambda v: v is None
    )
    note: str | None = Field(default=None, max_length=800)

    @field_validator("prompt_template")
    @classmethod
    def _known_template(cls, v: str) -> str:
        if v not in PROMPT_TEMPLATE_GUIDES:
            raise ValueError(f"不认识的提示模板 {v!r}：只有 {sorted(PROMPT_TEMPLATE_GUIDES)}")
        return v

    @model_validator(mode="after")
    def _live_consistent(self) -> LifeRunConfig:
        live = self.planner.identity.kind == "llm"
        if live != (self.version == RUN_CONFIG_VERSION_LIVE):
            raise ValueError(
                f"真实模型运行的配置版本必须是 {RUN_CONFIG_VERSION_LIVE}，mock 运行用 {RUN_CONFIG_VERSION}"
            )
        if not live and (self.paid_budget is not None or self.unmetered_reason is not None):
            raise ValueError("mock 运行不写付费预算 paid_budget，也不写 unmetered_reason")
        if live and (self.paid_budget is None) == (self.unmetered_reason is None):
            raise ValueError(
                "真实模型运行要么写付费预算 paid_budget，要么写不记账的理由 unmetered_reason，两者只能写一个"
            )
        expected = 0 if self.planner.llm is None else self.planner.llm.service_retries
        if self.planning.transport_retries != expected:
            raise ValueError(
                f"planning.transport_retries 必须等于规划器的服务重试次数 {expected}（mock 没有网络，固定 0）"
            )
        if (
            self.paid_budget is not None
            and self.planner.llm is not None
            and self.paid_budget.price.model != self.planner.llm.model
        ):
            raise ValueError("报价表对应的模型与规划器的模型快照不同")
        tier = (
            None if self.paid_budget is None else self.paid_budget.price.max_input_tokens_at_price
        )
        if (
            tier is not None
            and self.planner.llm is not None
            and self.planner.llm.max_input_tokens > tier
        ):
            raise ValueError(
                f"单次输入上限 {self.planner.llm.max_input_tokens} 超过报价 {self.paid_budget.price.price_version} 这一档的"
                f"适用上限 {tier}：比这一档长的请求不能按这一档的价格预留（不按低档冒充）；改用对应的报价，或把输入上限降到 {tier} 以内"
            )
        return self


# ---------------------------------------------------------------------- 运行记录（内部）

AttemptDisposition = Literal[
    "accepted",
    "revise",
    "revisions_exhausted",
    "city_data",
    "internal_error",
]


class AttemptRecord(ContractModel):
    """一次规划调用的结构化记录（完整的请求、提示与原始响应另存在调用日志）。"""

    request_id: str = Field(min_length=1, max_length=120)
    clone_id: IdStr
    year_index: YearIndex
    attempt: Annotated[int, Field(ge=1)]
    phase: Literal["plan", "revise"]
    planner_id: IdStr
    planner_kind: Literal["mock", "llm"]
    #: 同一次规划调用的服务层实际请求次数（含网络重试，E1-7B 最多 2）。mock 没有网络，记 1。不与计划修订混在一起计数。
    transport_attempts: Annotated[int, Field(ge=1, le=2)]
    #: 真实模型返回的结束原因（stop / length …）；mock 没有这一项（不序列化）。
    finish_reason: str | None = Field(default=None, max_length=40, exclude_if=lambda v: v is None)
    request_fingerprint: Sha256
    response_fingerprint: Sha256 | None = None
    response_kind: Literal["text", "planner_error"]
    parse: Literal["parsed", "format_error", "not_applicable"]
    check_stage: str | None = Field(default=None, max_length=40)
    check_status: str | None = Field(default=None, max_length=40)
    disposition: AttemptDisposition
    problem_codes: tuple[str, ...] = ()
    diagnostics: tuple[VisibleDiagnostic, ...] = ()
    action_fingerprint: Sha256 | None = None
    need_summary: str | None = Field(default=None, max_length=MAX_NEED_SUMMARY_CHARS)
    rationale: str | None = Field(default=None, max_length=MAX_RATIONALE_CHARS)
    detail: str | None = Field(default=None, max_length=600)


class AcceptedYear(ContractModel):
    """一个被接受、已经交给环境执行的年度行动，以及执行后的绑定身份（重放与恢复只读这里，不重问规划器）。"""

    clone_id: IdStr
    year_index: YearIndex
    attempt: Annotated[int, Field(ge=1)]
    request_id: str = Field(min_length=1, max_length=120)
    action: YearAction
    action_fingerprint: Sha256
    need_summary: str = Field(min_length=1, max_length=MAX_NEED_SUMMARY_CHARS)
    binding_fingerprint: Sha256
    handoff: str = Field(min_length=1, max_length=40)
    next_state_fingerprint: Sha256 | None = None


CloneRunStatus = Literal[
    "completed",
    "life_exit",
    "stopped",
    "feedback_blocked",
    "planning_stopped",
    "not_run",
    "program_error",
]

CLONE_RUN_STATUS_TEXT: dict[str, str] = {
    "completed": "正常生活完 N 年",
    "life_exit": "确认的生活出局（付不起最低住房与食品）",
    "stopped": "E1-5C 停止（疾病之后应急安排不足或资料缺口）",
    "feedback_blocked": "精力反馈受阻（下一年额度无法确定）",
    "planning_stopped": "规划没有完成（修订耗尽、城市资料问题或全局调用上限用尽）",
    "not_run": "没有运行（全局预算用尽或批次暂停之前没有轮到）",
    "program_error": "程序或配置错误（不假定只影响这个分身）",
}


class CloneRunRecord(ContractModel):
    """一个名单项在这次运行里的结局：生活提交了几年、每年用了几次规划调用、为什么停。"""

    clone_id: IdStr
    user_id: IdStr
    city: CityId
    repeat_index: Annotated[int, Field(ge=0)]
    status: CloneRunStatus
    years_committed: Annotated[int, Field(ge=0)]
    attempts_by_year: tuple[Annotated[int, Field(ge=0)], ...] = ()
    planning_stop: PlanningStop | None = None
    failure: RunFailure | None = None
    detail: str = Field(min_length=1, max_length=600)


#: running：正在运行（崩溃后留下的也是它，可以恢复）；interrupted：在两次规划调用之间按请求停下（可以恢复）；halted（E1-7B）：
#: 服务、预算、输入上限、发送状态不确定或请求检查不过，在发送下一次请求之前停下，现场保留、没有评价，原因在 halt_kind 与
#: pause_reason；paused：程序或共享配置错误；其余是结局。
RunStatus = Literal["running", "completed", "paused", "budget_exhausted", "interrupted", "halted"]
#: halted 的原因（E1-7B）。都不是生活结果：服务失败、凭证、账户、请求配置不支持、付费预算不够、输入超过估计上限、上一次请求
#: 可能已发出但结果没有可靠落盘、最终请求检查不过、返回的模型与设定不同。
HaltKind = Literal[
    "service_unavailable",
    "credentials",
    "account",
    "bad_request",
    "budget",
    "input_limit",
    "uncertain_send",
    "request_check",
    "model_mismatch",
]


class PendingYear(ContractModel):
    """一个分身正在规划、还没有接受行动的那一年（并行运行时每个分身最多一条）。"""

    clone_id: IdStr
    year_index: YearIndex
    attempts_used: Annotated[int, Field(ge=0)]


class RunProgress(ContractModel):
    """运行进度（每个年度边界与结束时写盘）。恢复只能从这里继续，尝试次数与预算不清零。"""

    run_id: str = Field(min_length=1, max_length=120)
    status: RunStatus
    calls_used: Annotated[int, Field(ge=0)]
    call_limit: Annotated[int, Field(ge=1)]
    order: tuple[IdStr, ...]
    clones: tuple[CloneRunRecord, ...]
    #: 停下时正在规划、还没有接受行动的年份（每个分身最多一条，按名单顺序）。
    pending: tuple[PendingYear, ...] = ()
    pause_reason: str | None = Field(default=None, max_length=600)
    halt_kind: HaltKind | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _consistent(self) -> RunProgress:
        if self.calls_used > self.call_limit:
            raise ValueError("用掉的调用次数超过了全局上限")
        ids = [p.clone_id for p in self.pending]
        if len(ids) != len(set(ids)):
            raise ValueError("同一个分身只能有一条正在规划的年份")
        if (self.status in ("paused", "halted")) != (self.pause_reason is not None):
            raise ValueError("只有暂停或停下的批次写原因，暂停或停下必须写原因")
        if (self.status == "halted") != (self.halt_kind is not None):
            raise ValueError("只有 halted 写 halt_kind，halted 必须写")
        return self


class RunManifest(ContractModel):
    """一次运行的身份：开跑前记下全部内容身份。恢复时任何一项对不上就拒绝继续，不冒充原运行。"""

    run_id: str = Field(min_length=1, max_length=120)
    planning_version: Literal["product-planning-v1"]
    run_config_fingerprint: Sha256
    identities: dict[str, str]
    planner: PlannerIdentity
    planning: PlanningPolicy
    information: InformationSetting
    order: tuple[IdStr, ...]


def internal_planning_models() -> tuple[type[ContractModel], ...]:
    """E1-7A 运行侧的内部对象：运行配置、规划器设定与运行记录。它们本身与原样序列化后的字典都不进提示。"""
    return (
        PlannerIdentity,
        MockScript,
        LlmSettings,
        PriceTable,
        PaidBudget,
        PlannerSpec,
        InformationSetting,
        PlanningPolicy,
        LifeRunConfig,
        AttemptRecord,
        AcceptedYear,
        CloneRunRecord,
        RunProgress,
        RunManifest,
    )
