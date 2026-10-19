"""E1-6B 城市汇总的数据形状（`DECISIONS/0029`）。只有形状、局部约束与内容身份，没有经济数学。

一个用户（偏好组）的多个分身在几座城各生活 N 年；E1-6A 已经给出每个分身的 L_actual、V_reference 与 J。E1-6B 把它们
按"用户 × 城市"汇总成城市报告：

* **先冻结原计划名单**（看结果之前）：用户、人、分身、城市、重复分身序号与运行臂；冻结时记下每个运行臂的协议、评价
  规格内容身份、δ、H、N 与时间权重，各城目录与税绑定，每个名单项的开局状态、共同开局摘要与评分输入的指纹。不从收到
  多少结果反推原计划人数。
* **每个名单项恰好一类**：C 完整 J；E 确认的生活出局（可信的实际结算记录明确为付不起最低住房与食品而出局）；M 模拟或
  评价不完整（原因细分，见 ``MissingReason``）。N_planned = C + E + M。
* **完整评价分身的平均分** = Σ J / C（同一批 C 的 L、V 平均一起给）；**出局比例** = E / N；**模拟缺失范围** =
  Σ 下界 / (C + M) 到 Σ 上界 / (C + M)，完整 J 的项取 [J, J]，确认出局者不进范围。

范围是"在当前评分尺度与已确认出局的排除规则下，给模拟尚不能计算的部分保留的可能范围"——**不是** 95% 置信区间，不是
抽样误差条，不是疾病发生概率，也不是全体真实人生的均值区间；它不要求缺失随机，也不预测没跑完的分身继续运行后一定能完成。
完成者均值不代表原计划全部分身，也不代表全部可能人生。

2026-10-12 收尾（审核 R1–R3，``aggregation_revision = e1-6b-r2``）：名单冻结前做**标准开局 v1** 检查（``START_CONDITION_RULE``：
只接受第 1 年、还没有任何生活记录的开局，同一用户的个人条件逐字段一致）与每个运行臂的完整跨城评价配置检查；冻结名单与报告都记下
通过的是哪一版检查。敏感性对照先按报告里的评价指纹把证据一一配对，再比较封存的生活绑定（``SensitivityReport.lives_check``）。
硬要求汇总写明原计划人数、有可信记录的核验人数与未核验人数。

计算在 ``econ.life_aggregation``；名单的加载与跨对象检查在 ``ledger.life_roster``。这里的对象都是内部对象，本身与原样
序列化后的字典都不能进代理观察（登记在 ``internal_aggregation_models``，由 ``common.life_scoring_contracts.internal_models``
合成）。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from common.ids import CityId
from common.life_contracts import (
    ContractModel,
    ExactAmount,
    IdStr,
    KeyStr,
    Month,
    YearIndex,
    canonical_fingerprint,
)
from common.life_evaluation_contracts import PlanningStop, RequirementTrajectory

#: 汇总的版本与内容格式；分类规则与范围公式的身份（进入冻结名单的内容身份）。改其中任何一项都换名字，并在 ADR 记录。
AGGREGATION_VERSION = "e1-6b-aggregation-v1"
#: 内容格式 v2（2026-10-12 收尾）：冻结名单多了开局检查与执行修订的身份、名单项多了用户声明的硬要求。
AGGREGATION_CONTENT_SCHEMA = "e1-6b-aggregation-content-v2"
CLASSIFICATION_RULE = "c_e_m_owner_2026_10_12_v1"
RANGE_RULE = "known_months_plus_unit_bounds_v1"
#: 标准开局 v1（审核 R1，所有者收紧的范围）：城市比较只接受第 1 年、还没有任何生活记录的开局（没有持有物、培训进度、资金目标、
#: 病情与病史、月份负担记录、经历摘要；岗位是开局声明、额度是第一年的基础值、能力只来自用户陈述），同一用户的个人条件逐字段一致。
#: 检查在 ``ledger.life_roster``（``load_roster`` 与直接 ``freeze_roster`` 共用）。改了支持范围就换名字，并在 ADR 记录。
START_CONDITION_RULE = "v1_standard_start_2026_10_12"
#: 汇总的执行修订：r2 = 标准开局检查、冻结前每个运行臂的完整跨城配置检查、敏感性证据配对、硬要求核验人数（2026-10-12 收尾）。
AGGREGATION_REVISION = "e1-6b-r2"

_SHA256 = r"^[0-9a-f]{64}$"
Sha256 = Annotated[str, Field(pattern=_SHA256)]

#: 每个名单项恰好一类。
EntryCategory = Literal["C", "E", "M"]
#: M 的原因（互斥；按最早失败的环节归类）。都不是"生活出局"，也不解释为生活差。
MissingReason = Literal[
    "not_run",  # 未运行 / 未返回（名单上有、没有收到结果，或只返回到中途）
    "plan_or_execution",  # 计划或执行未完成（计划不可行、年初不合格的计划被退回）
    "data_missing",  # 资料缺失（结算要用的城市资料缺失或不完整）
    "score_incomplete",  # 评分不完整（终态正常，但有月份有正权重的资料未知）
    "feedback_blocked",  # 精力反馈受阻（下一年额度无法确定）
    "stopped",  # E1-5C 停止（疾病之后应急安排不足或资料缺口），原样保留，不改称出局
    "valuation_incomplete",  # 实际生活完整，但期末估值不完整或没有可用参考分支
    "input_config_program_error",  # 输入 / 配置 / 程序错误（含身份或证据核对失败、重复结果、意外异常）
]
#: E1-7A：规划停止归入哪一类 M（DECISIONS/0030）。只是原因细分；C / E / M 的分类与范围公式不变。
PLANNING_STOP_REASON: dict[str, str] = {
    "revisions_exhausted": "plan_or_execution",
    "city_data": "data_missing",
    "budget_exhausted": "not_run",
}
MISSING_REASON_TEXT: dict[str, str] = {
    "not_run": "未运行 / 未返回",
    "plan_or_execution": "计划或执行未完成",
    "data_missing": "资料缺失",
    "score_incomplete": "评分不完整",
    "feedback_blocked": "精力反馈受阻",
    "stopped": "E1-5C 停止（应急安排不足或资料缺口）",
    "valuation_incomplete": "期末估值不完整或没有可用参考分支",
    "input_config_program_error": "输入 / 配置 / 程序错误",
}


# ---------------------------------------------------------------------- 原计划名单（声明）


class RosterUser(ContractModel):
    """一个用户（偏好组）：一个人的一份偏好。不同用户分别出报告，不混成一个人的城市推荐分。"""

    user_id: IdStr
    person_id: IdStr
    label: str = Field(min_length=1, max_length=120)


class RosterArmDecl(ContractModel):
    """一个运行臂：同一批实际生活用哪份评价规格评价。``main`` 只有一个；``sensitivity`` 是事先声明的评价对照（只改评价
    假设，不重跑生活、不改账目、不重新选人）。"""

    arm_id: IdStr
    role: Literal["main", "sensitivity"]
    evaluation_config: str = Field(min_length=1, max_length=300)
    note: str | None = Field(default=None, max_length=400)


class RosterEntryDecl(ContractModel):
    """原计划名单上的一项：哪个用户的哪个分身、在哪座城、第几个重复分身。"""

    user_id: IdStr
    clone_id: IdStr
    city: CityId
    repeat_index: Annotated[int, Field(ge=0)]


class RosterConfig(ContractModel):
    """原计划名单的声明（YAML）。必须在看任何结果之前写好；冻结见 ``econ.life_aggregation.freeze_roster``。"""

    roster_id: IdStr
    status: Literal["exploratory_synthetic"]
    aggregation_version: Literal["e1-6b-aggregation-v1"]
    life_input: str = Field(min_length=1, max_length=300)
    scoring_config: str = Field(min_length=1, max_length=300)
    comparison_group_id: IdStr
    users: tuple[RosterUser, ...] = Field(min_length=1)
    arms: tuple[RosterArmDecl, ...] = Field(min_length=1)
    entries: tuple[RosterEntryDecl, ...] = ()
    note: str | None = Field(default=None, max_length=800)

    @model_validator(mode="after")
    def _unique(self) -> RosterConfig:
        users = [u.user_id for u in self.users]
        if len(set(users)) != len(users):
            raise ValueError(f"用户编号重复：{users}")
        persons = [u.person_id for u in self.users]
        if len(set(persons)) != len(persons):
            raise ValueError(f"两个用户指向同一个人：{persons}")
        arms = [a.arm_id for a in self.arms]
        if len(set(arms)) != len(arms):
            raise ValueError(f"运行臂编号重复：{arms}")
        if [a.role for a in self.arms].count("main") != 1:
            raise ValueError("恰好要有一个主臂（role: main）")
        clones = [e.clone_id for e in self.entries]
        if len(set(clones)) != len(clones):
            raise ValueError(f"名单里同一个分身出现不止一次：{clones}")
        keys = [(e.user_id, str(e.city), e.repeat_index) for e in self.entries]
        if len(set(keys)) != len(keys):
            raise ValueError("名单里同一用户、同一城、同一重复序号出现不止一次")
        unknown = sorted({e.user_id for e in self.entries} - set(users))
        if unknown:
            raise ValueError(f"名单项引用了没有声明的用户：{unknown}")
        return self


# ---------------------------------------------------------------------- 冻结后的名单


class FrozenArm(ContractModel):
    """冻结时记下的一个运行臂：协议、评价规格的内容身份与执行修订、δ、H、N、评分规格的内容身份，以及精确的时间权重
    W_actual = Σ_{m=1..T} δ^(m−1) 与 W_future = Σ_{k=1..H} δ^(T+k−1)（T = 12N）。"""

    arm_id: IdStr
    role: Literal["main", "sensitivity"]
    evaluation_spec_id: IdStr
    evaluation_spec_content_fingerprint: Sha256
    execution_revision: str = Field(min_length=1, max_length=40)
    discount_factor: str = Field(min_length=1, max_length=20)
    horizon_months: Annotated[int, Field(ge=1)]
    protocol_id: IdStr
    protocol_fingerprint: Sha256
    simulation_years: Annotated[int, Field(ge=1)]
    scoring_content_fingerprint: Sha256
    weight_actual: ExactAmount
    weight_future: ExactAmount


class FrozenCity(ContractModel):
    """冻结时记下的一座城的事实身份：城市目录与税绑定的指纹。各城可以不同，但每座城的结果都要对上自己的这一份。"""

    city: CityId
    catalog_fingerprint: Sha256
    tax_fingerprint: Sha256


class FrozenEntry(ContractModel):
    """冻结后的名单项：声明的身份，加上第 1 年年初状态的指纹、同一用户各城共用的开局摘要指纹与评分输入的指纹。"""

    user_id: IdStr
    person_id: IdStr
    clone_id: IdStr
    city: CityId
    repeat_index: Annotated[int, Field(ge=0)]
    start_fingerprint: Sha256
    common_start_fingerprint: Sha256
    scoring_inputs_fingerprint: Sha256
    #: 用户声明的硬要求（需求编号，排序去重）。硬要求汇总按它列出每一条，没有可信记录的名单项算"未核验"，不当成"核验过、没问题"。
    hard_requirement_needs: tuple[KeyStr, ...]


class FrozenRoster(ContractModel):
    """冻结的原计划名单（看结果之前生成，有内容身份）。分类规则与范围公式的名字也冻结在这里。"""

    roster_id: IdStr
    status: Literal["exploratory_synthetic"]
    aggregation_version: Literal["e1-6b-aggregation-v1"]
    classification_rule: Literal["c_e_m_owner_2026_10_12_v1"]
    range_rule: Literal["known_months_plus_unit_bounds_v1"]
    #: 冻结前通过的开局检查与汇总的执行修订（2026-10-12 收尾）。旧的冻结名单没有这两项，按本契约读入会被拒绝——不给历史结果补贴
    #: "已通过新检查"的标签，需要时从原始输入重新冻结。
    start_condition_rule: Literal["v1_standard_start_2026_10_12"]
    aggregation_revision: Literal["e1-6b-r2"]
    comparison_group_id: IdStr
    users: tuple[RosterUser, ...]
    arms: tuple[FrozenArm, ...]
    cities: tuple[FrozenCity, ...]
    entries: tuple[FrozenEntry, ...] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=800)

    @model_validator(mode="after")
    def _consistent(self) -> FrozenRoster:
        if [a.role for a in self.arms].count("main") != 1:
            raise ValueError("恰好要有一个主臂")
        clones = [e.clone_id for e in self.entries]
        if len(set(clones)) != len(clones):
            raise ValueError("冻结名单里同一个分身出现不止一次")
        cities = {str(c.city) for c in self.cities}
        missing = sorted({str(e.city) for e in self.entries} - cities)
        if missing:
            raise ValueError(f"名单项的城市没有冻结事实身份：{missing}")
        users = {u.user_id: u.person_id for u in self.users}
        for e in self.entries:
            if users.get(e.user_id) != e.person_id:
                raise ValueError(f"{e.clone_id}: 用户 {e.user_id} 不是人 {e.person_id}")
        return self

    def content(self) -> dict[str, object]:
        """参与内容身份的全部内容（用户标签与备注不参与）。"""
        return {
            "content_schema": AGGREGATION_CONTENT_SCHEMA,
            "roster_id": self.roster_id,
            "status": self.status,
            "aggregation_version": self.aggregation_version,
            "classification_rule": self.classification_rule,
            "range_rule": self.range_rule,
            "start_condition_rule": self.start_condition_rule,
            "aggregation_revision": self.aggregation_revision,
            "comparison_group_id": self.comparison_group_id,
            "users": [{"user_id": u.user_id, "person_id": u.person_id} for u in self.users],
            "arms": [a.model_dump(mode="json") for a in self.arms],
            "cities": [c.model_dump(mode="json") for c in self.cities],
            "entries": [e.model_dump(mode="json") for e in self.entries],
        }

    @property
    def content_fingerprint(self) -> str:
        return canonical_fingerprint(self.content())

    @property
    def main_arm(self) -> FrozenArm:
        return next(a for a in self.arms if a.role == "main")

    def arm(self, arm_id: str) -> FrozenArm | None:
        return next((a for a in self.arms if a.arm_id == arm_id), None)

    def city(self, city: str) -> FrozenCity | None:
        return next((c for c in self.cities if str(c.city) == city), None)

    def entry(self, clone_id: str) -> FrozenEntry | None:
        return next((e for e in self.entries if e.clone_id == clone_id), None)


# ---------------------------------------------------------------------- 批次运行与分类结果


class RunFailure(ContractModel):
    """批次运行时一个名单项没能得到评价结果的记录（运行脚本写；汇总器把它归进 M：输入 / 配置 / 程序错误）。
    ``contract``：输入或配置被明确拒绝；``unexpected``：意外的程序异常——显式报告，不吞掉，也不假定只影响这一个分身。

    E1-7A（DECISIONS/0030）：``stage = planning`` 表示规划阶段。第 1 年的计划就没有被接受（还没有任何实际生活）时带
    ``planning_stop``，汇总器按 ``PLANNING_STOP_REASON`` 归类（修订耗尽 → 计划或执行未完成，城市资料 → 资料缺失，预算用尽或
    批次暂停 → 未运行）；规划器的意外异常照旧是 ``unexpected``。已经有部分轨迹的分身不用它，规划停止写在评价结果的轨迹记录里。"""

    clone_id: IdStr
    stage: Literal["life", "evaluation", "planning"]
    error_kind: Literal["contract", "unexpected"]
    message: str = Field(min_length=1, max_length=600)
    planning_stop: PlanningStop | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _planning_stop_shape(self) -> RunFailure:
        if self.planning_stop is not None and (
            self.stage != "planning" or self.error_kind != "contract"
        ):
            raise ValueError("规划停止只出现在规划阶段、可预期（contract）的失败记录里")
        if self.planning_stop is not None and self.planning_stop.year_index != 1:
            raise ValueError(
                "没有任何实际生活的规划停止只能是第 1 年；之后的年份写在评价结果的轨迹记录里"
            )
        return self


class ChainStep(ContractModel):
    """事件链的一步：哪年哪月、发生了什么（带金额），取自哪类已核对的记录。只写事实发生的顺序，不宣称因果贡献。"""

    year_index: YearIndex
    month: Month
    text: str = Field(min_length=1, max_length=300)
    source: Literal["month_fact", "event", "exit_record", "stop_record"]


class EntryRecord(ContractModel):
    """一个名单项的分类与数值。

    * C：J、L、V 都有，范围就是 [J, J]；
    * E：确认的生活出局，不进均值、不进范围（``lower`` / ``upper`` 为空），保留事件链；
    * M：原因与说明；范围的组成——可信的已知实际月份加权分数和 K（``known_weighted_sum``）与权重 W_known、全部实际
      权重 W_actual、未来权重 W_future、V 的上下界（V 完整时两端都是 V，否则 0 与 1）——以及算出的下界与上界。
      身份或证据核对不通过、没有收到结果的项不提取任何"已知月份"，K = W_known = 0。
    """

    user_id: IdStr
    person_id: IdStr
    clone_id: IdStr
    city: CityId
    repeat_index: Annotated[int, Field(ge=0)]
    category: EntryCategory
    missing_reason: MissingReason | None = None
    detail: str = Field(min_length=1, max_length=600)
    trajectory_status: str | None = Field(default=None, max_length=40)
    evaluation_fingerprint: Sha256 | None = None
    selected_plan_id: str | None = Field(default=None, max_length=64)
    j: ExactAmount | None = None
    l_actual: ExactAmount | None = None
    v_reference: ExactAmount | None = None
    known_weighted_sum: ExactAmount
    known_weight: ExactAmount
    weight_actual: ExactAmount
    weight_future: ExactAmount
    v_low: ExactAmount | None = None
    v_high: ExactAmount | None = None
    lower: ExactAmount | None = None
    upper: ExactAmount | None = None
    hard_requirements: tuple[RequirementTrajectory, ...] = ()
    event_chain: tuple[ChainStep, ...] = ()
    event_tags: tuple[str, ...] = ()
    unexpected_error: bool = False

    @model_validator(mode="after")
    def _shape(self) -> EntryRecord:
        c = self.clone_id
        if (self.category == "M") != (self.missing_reason is not None):
            raise ValueError(f"{c}: 只有 M 写缺失原因，M 必须写")
        if self.category == "C":
            if self.j is None or self.l_actual is None or self.v_reference is None:
                raise ValueError(f"{c}: 完整 J 的项必须有 J、L 与 V")
            j = self.j.fraction()
            if not 0 <= j <= 1:
                raise ValueError(f"{c}: J 必须在 [0, 1]，得到 {self.j.exact}")
            if (self.lower, self.upper) != (self.j, self.j):
                raise ValueError(f"{c}: 完整 J 的项的范围就是 [J, J]")
        elif self.category == "E":
            if self.j is not None or self.lower is not None or self.upper is not None:
                raise ValueError(f"{c}: 确认出局的项不进均值，也不进缺失范围")
        else:
            if self.j is not None:
                raise ValueError(f"{c}: 不完整的项没有 J（不用 L 或已知月份冒充）")
            if None in (self.lower, self.upper, self.v_low, self.v_high):
                raise ValueError(f"{c}: 不完整的项必须写范围与它的 V 上下界")
            lo, hi = self.lower.fraction(), self.upper.fraction()  # type: ignore[union-attr]
            if not 0 <= lo <= hi <= 1:
                raise ValueError(f"{c}: 范围必须满足 0 ≤ 下界 ≤ 上界 ≤ 1，得到 [{lo}, {hi}]")
        k, wk, wa = (
            self.known_weighted_sum.fraction(),
            self.known_weight.fraction(),
            self.weight_actual.fraction(),
        )
        if not 0 <= k <= wk <= wa:
            raise ValueError(f"{c}: 已知月份必须满足 0 ≤ K ≤ W_known ≤ W_actual")
        return self


class ReasonCount(ContractModel):
    reason: MissingReason
    count: Annotated[int, Field(ge=1)]
    clone_ids: tuple[IdStr, ...]


class HardRequirementSummary(ContractModel):
    """一条硬要求在一座城各名单项实际生活里的情况（按名单项计数）。单独报告：不删人、不扣分、不改出局。

    分母写清楚：``planned`` 是这座城的原计划人数；``checked`` 是有可信记录、能拿来核验这条要求的名单项（每月都满足、违反、未知、
    只在活着的月份满足四类之和）；``not_checked`` 是没有可信记录的名单项（没返回、核对不通过、输入或程序错误……），**不是**
    "核验过、没有问题"。"""

    need: KeyStr
    planned: Annotated[int, Field(ge=1)]
    checked: Annotated[int, Field(ge=0)]
    satisfied_every_month: Annotated[int, Field(ge=0)]
    violated: Annotated[int, Field(ge=0)]
    unknown: Annotated[int, Field(ge=0)]
    satisfied_in_lived_months: Annotated[int, Field(ge=0)]
    not_checked: Annotated[int, Field(ge=0)]
    violated_clone_ids: tuple[IdStr, ...] = ()
    unknown_clone_ids: tuple[IdStr, ...] = ()
    not_checked_clone_ids: tuple[IdStr, ...] = ()

    @model_validator(mode="after")
    def _counts(self) -> HardRequirementSummary:
        parts = (
            self.satisfied_every_month
            + self.violated
            + self.unknown
            + self.satisfied_in_lived_months
        )
        if parts != self.checked:
            raise ValueError(f"{self.need}: 四类之和 {parts} 不等于核验人数 {self.checked}")
        if self.checked + self.not_checked != self.planned:
            raise ValueError(f"{self.need}: 核验人数 + 未核验人数必须等于原计划人数")
        if (
            len(self.violated_clone_ids),
            len(self.unknown_clone_ids),
            len(self.not_checked_clone_ids),
        ) != (self.violated, self.unknown, self.not_checked):
            raise ValueError(f"{self.need}: 分身清单与人数不一致")
        return self


class CityReport(ContractModel):
    """一个运行臂下，一个用户在一座城的报告。均值只用完整评价的分身（C）；出局比例与缺失比例都除以原计划人数；
    范围的分母是 C + M（确认出局者不在里面）。"""

    arm_id: IdStr
    user_id: IdStr
    person_id: IdStr
    city: CityId
    planned: Annotated[int, Field(ge=1)]
    complete: Annotated[int, Field(ge=0)]
    exits: Annotated[int, Field(ge=0)]
    missing: Annotated[int, Field(ge=0)]
    complete_share: ExactAmount
    exit_share: ExactAmount
    missing_share: ExactAmount
    mean_j: ExactAmount | None = None
    mean_l: ExactAmount | None = None
    mean_v: ExactAmount | None = None
    mean_j_display: float | None = None
    mean_l_display: float | None = None
    mean_v_display: float | None = None
    range_lower: ExactAmount | None = None
    range_upper: ExactAmount | None = None
    range_lower_display: float | None = None
    range_upper_display: float | None = None
    range_denominator: Annotated[int, Field(ge=0)]
    missing_reasons: tuple[ReasonCount, ...] = ()
    exit_clone_ids: tuple[IdStr, ...] = ()
    hard_requirements: tuple[HardRequirementSummary, ...] = ()
    entries: tuple[EntryRecord, ...]
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _counts(self) -> CityReport:
        if self.planned != self.complete + self.exits + self.missing:
            raise ValueError("原计划人数必须等于 C + E + M")
        if len(self.entries) != self.planned:
            raise ValueError("每个原计划名单项都要有一条记录")
        cats = [e.category for e in self.entries]
        if (cats.count("C"), cats.count("E"), cats.count("M")) != (
            self.complete,
            self.exits,
            self.missing,
        ):
            raise ValueError("C / E / M 计数与名单项记录不一致")
        if (self.complete == 0) != (self.mean_j is None):
            raise ValueError("只有有完整评价分身时才有均值（C = 0 时为空，不填 0）")
        if (self.complete + self.missing == 0) != (self.range_lower is None):
            raise ValueError("全员确认出局时范围为空；否则必须给范围")
        if self.range_denominator != self.complete + self.missing:
            raise ValueError("范围的分母是 C + M")
        return self


class CityComparison(ContractModel):
    """同一运行臂、同一用户两座城的比较。均值差只在两边都有完整评价分身时给；范围差 =
    [A 下界 − B 上界, A 上界 − B 下界]——含 0 就是"这些缺失范围下不能确定方向"。不是显著性检验，也不是总体因果结论；
    出局率一起展示，不能只看幸存者的高分。"""

    arm_id: IdStr
    user_id: IdStr
    city_a: CityId
    city_b: CityId
    planned_a: int
    planned_b: int
    complete_a: int
    complete_b: int
    exit_share_a: ExactAmount
    exit_share_b: ExactAmount
    mean_difference: ExactAmount | None = None
    range_difference_low: ExactAmount | None = None
    range_difference_high: ExactAmount | None = None
    direction: Literal["a_higher", "b_higher", "undetermined", "not_available"]
    note: str = Field(min_length=1, max_length=600)


class RejectedSubmission(ContractModel):
    """不在原计划名单上的结果：单列拒收，不计入任何城市、任何分母。"""

    clone_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=400)


class ArmReport(ContractModel):
    """一个运行臂的汇总：每个用户每座城的报告、同一用户两城的比较、拒收的名单外结果。绑定冻结名单与评价规格的内容
    身份；``report_fingerprint`` 覆盖全部内容，同样输入重放逐字节相同。"""

    roster_id: IdStr
    roster_fingerprint: Sha256
    #: 名单冻结前通过的开局检查与汇总的执行修订（与冻结名单相同，抄在这里方便读报告的人）。
    start_condition_rule: Literal["v1_standard_start_2026_10_12"]
    aggregation_revision: Literal["e1-6b-r2"]
    arm_id: IdStr
    role: Literal["main", "sensitivity"]
    evaluation_spec_id: IdStr
    evaluation_spec_content_fingerprint: Sha256
    discount_factor: str
    horizon_months: int
    simulation_years: int
    city_reports: tuple[CityReport, ...]
    comparisons: tuple[CityComparison, ...] = ()
    rejected_submissions: tuple[RejectedSubmission, ...] = ()
    unexpected_errors: Annotated[int, Field(ge=0)] = 0
    assumptions: tuple[str, ...] = ()
    report_fingerprint: Sha256

    def evidence(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"report_fingerprint"})

    def expected_report_fingerprint(self) -> str:
        return canonical_fingerprint(self.evidence())

    @model_validator(mode="after")
    def _fingerprint(self) -> ArmReport:
        if self.report_fingerprint != self.expected_report_fingerprint():
            raise ValueError("汇总报告的指纹与内容不一致")
        return self


class SensitivityChange(ContractModel):
    """主臂与一个评价对照臂在同一用户、同一城上的差别：完整评价人数、L / V / J 的均值（各自那一批完整分身）、范围，以及
    换了类别或原因的名单项与理由。"""

    user_id: IdStr
    city: CityId
    complete_main: int
    complete_other: int
    mean_j_main: ExactAmount | None = None
    mean_j_other: ExactAmount | None = None
    mean_l_main: ExactAmount | None = None
    mean_l_other: ExactAmount | None = None
    mean_v_main: ExactAmount | None = None
    mean_v_other: ExactAmount | None = None
    range_main_lower: ExactAmount | None = None
    range_main_upper: ExactAmount | None = None
    range_other_lower: ExactAmount | None = None
    range_other_upper: ExactAmount | None = None
    moved: tuple[str, ...] = ()


#: 敏感性对照的证据状态（2026-10-12 收尾 R3）：
#: ``identical`` 传入的评价结果与两份报告逐项配对，比较过的分身逐年封存绑定全部相同——只改了评价假设；
#: ``different`` 证据配对，但有分身的实际生活不同——这不是"只改评价假设"的对照；
#: ``evidence_mismatch`` 传入的评价结果与报告不配套（缺、多、重复、旧缓存、错运行臂）——不能判断是不是同一批生活，也**不等于**生活不同；
#: ``no_comparable_evidence`` 证据配对，但没有任何分身两边都有可信评价——什么都证明不了。
LivesCheck = Literal["identical", "different", "evidence_mismatch", "no_comparable_evidence"]


class SensitivityReport(ContractModel):
    """主臂与一个评价对照臂的比较。先按报告里的评价指纹把传入的评价结果一一配对（不信不属于这份报告的证据），再比较两边
    都有可信评价的分身逐年封存的生活绑定；只改评价假设时两臂应当是同一批实际生活。``actual_lives_identical`` 只在
    ``lives_check = identical`` 时为真。``compared_clone_ids`` 是真正比较过的分身；``not_compared`` 写明没比较的分身与原因
    （例如两边都没有可信评价）；``evidence_problems`` 写明证据哪里不配套。核验失败不改写、不删减两份报告。"""

    main_arm_id: IdStr
    other_arm_id: IdStr
    aggregation_revision: Literal["e1-6b-r2"]
    lives_check: LivesCheck
    actual_lives_identical: bool
    compared_clone_ids: tuple[IdStr, ...] = ()
    different_clone_ids: tuple[IdStr, ...] = ()
    not_compared: tuple[str, ...] = ()
    evidence_problems: tuple[str, ...] = ()
    changes: tuple[SensitivityChange, ...]
    direction_changes: tuple[str, ...] = ()
    note: str = Field(min_length=1, max_length=600)

    @model_validator(mode="after")
    def _status(self) -> SensitivityReport:
        if self.actual_lives_identical != (self.lives_check == "identical"):
            raise ValueError("只有 lives_check = identical 时才能说实际生活相同")
        if (self.lives_check == "evidence_mismatch") != bool(self.evidence_problems):
            raise ValueError("证据不配套时必须写明问题；证据配套时不能有证据问题")
        if self.lives_check == "identical" and (
            not self.compared_clone_ids or self.different_clone_ids
        ):
            raise ValueError("说生活相同，至少要比较过一个分身，而且没有不同的分身")
        if self.lives_check == "different" and not self.different_clone_ids:
            raise ValueError("说生活不同，必须列出哪些分身不同")
        if self.lives_check == "no_comparable_evidence" and self.compared_clone_ids:
            raise ValueError("比较过分身就不是没有可比较的证据")
        return self


#: 汇总侧的保留键：出现在观察里就说明城市均值、范围、比较或名单身份漏了出去。不与可见对象的字段重名。
RESERVED_AGGREGATION_KEYS: frozenset[str] = frozenset(
    {
        "mean_j",
        "mean_l",
        "mean_v",
        "range_lower",
        "range_upper",
        "range_denominator",
        "exit_share",
        "missing_share",
        "complete_share",
        "mean_difference",
        "range_difference_low",
        "range_difference_high",
        "roster_fingerprint",
        "missing_reason",
        "known_weighted_sum",
    }
)


def internal_aggregation_models() -> tuple[type[ContractModel], ...]:
    """E1-6B 汇总侧的内部对象类型。它们本身、以及原样序列化后的字典，都不能进观察。"""
    return (
        RosterUser,
        RosterArmDecl,
        RosterEntryDecl,
        RosterConfig,
        FrozenArm,
        FrozenCity,
        FrozenEntry,
        FrozenRoster,
        RunFailure,
        ChainStep,
        EntryRecord,
        ReasonCount,
        HardRequirementSummary,
        CityReport,
        CityComparison,
        RejectedSubmission,
        ArmReport,
        SensitivityChange,
        SensitivityReport,
    )
