"""接入层真实资料的内部契约（intake-02）：来源、标准化记录、映射方法、机会注册表与菜单的形状。

这些是**内部**结构，不是 E3-A / E5 的公共消息（公共消息仍只在 ``common.product_contracts``）。这里只放形状与局部约束；
跨对象检查（记录引用的来源与方法存在、菜单只引用注册表里的机会……）在 ``ledger.intake_data``。

几条贯穿全部结构的规则：

* **每个关键数值都能追溯**：来源与原始记录定位、观测时期、适用地理、原始值与原始单位、标准化值与单位、方法与版本、用到的假设、
  观测 / 派生 / 估计 / 假设四类，以及缺失、隐去、无法映射、访问受限的原因（``FieldValue``）。
* **获取时间等运行元数据不进内容身份**：``content_identity`` 只看内容；同一份资料、方法、配置与种子得到同一身份。
* **几种状态分开记**：真实供给状态、资料是否充分、目标分布里的权重、本次菜单是否抽中、能否编进当前引擎，以及“已采集 / 能供规划参考 /
  可执行 / 可评分”四种能力，各是一个字段，不合并成一个 supported。
* **国家与体系留在数据里**：地理体系（``CBSA2023``、``PUMA2020``……）、职业分类、币种、单位与时期都是字段，不写死美国。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

INTAKE_SCHEMA_VERSION = "intake-data-v1"

#: 数值的来源性质：观测（原样取自来源）、派生（确定的换算，例如年薪 ÷ 12、同期换单位）、估计（有误差的统计或时期换算）、
#: 假设（登记过的设定，不是测量）。整条机会的来源性质取它用到的字段里最弱的一种（与 E3-A 编译器同一规则）。
FactKind = Literal["observed", "derived", "estimated", "assumption"]
KIND_ORDER: tuple[FactKind, ...] = ("assumption", "estimated", "derived", "observed")
#: 一个字段的状态：有值；来源没有这项；来源隐去（保密或样本太少）；有值但不能映射到标准口径；来源访问受限；对这类记录不适用。
FieldStatus = Literal[
    "present", "missing", "suppressed", "unmappable", "access_restricted", "not_applicable"
]
Sector = Literal["housing", "jobs", "training", "food", "goods", "activities", "background"]
SECTORS: tuple[Sector, ...] = (
    "housing",
    "jobs",
    "training",
    "food",
    "goods",
    "activities",
    "background",
)
GeoLevel = Literal[
    "national",
    "census_region",
    "state",
    "cbsa",
    "metro_division",
    "county",
    "puma",
    "principal_city",
    "cpi_area",
    "point",
]
#: 这条记录的地理与验证都会区的关系：正好是它、它在都会区里面、覆盖都会区并且更大、部分重叠、只能当代理（例如区域均价用于都会区）。
GeoRelation = Literal["exact", "within", "contains", "overlaps", "proxy"]
SupplyStatus = Literal["offered", "not_offered", "unknown"]
DataStatus = Literal["complete", "partial", "insufficient"]
Origin = Literal["record", "mapping_a", "mapping_b"]
MenuPolicyKind = Literal["full_catalog", "calendar_open", "annual_exposure_sample"]
CompileStatus = Literal["compilable", "needs_e1_extension", "not_compilable"]
Terms = Literal["included", "excluded", "unknown"]

Scalar = str | int | float | bool | None
_ID = r"^[a-z0-9][a-z0-9_.:\-]{0,159}$"
IdStr = Annotated[str, Field(pattern=_ID)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class IntakeModel(BaseModel):
    """内部资料结构的基类：不可变、拒绝未知字段、拒绝 inf/nan。"""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def canonical_json(content: object) -> str:
    """规范 JSON：键排序、紧凑分隔、保留中文。内容身份都用它。"""
    return json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_identity(content: object) -> str:
    """一份规范化内容的 SHA-256。调用方负责先去掉运行元数据（获取时间、耗时、本机路径）。"""
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


def short_id(*parts: object, length: int = 16) -> str:
    """由内容决定的短编号（规范 JSON 的 SHA-256 前若干位）。同样的输入永远得到同样的编号。"""
    return content_identity(list(parts))[:length]


# --------------------------------------------------------------------------- 来源与获取


class SourceSpec(IntakeModel):
    """一个资料来源：发布者、数据集、许可与使用条件、访问方式。许可写的是我们核对到的条件，不是法律意见。"""

    source_id: IdStr
    publisher: str = Field(min_length=1, max_length=120)
    dataset: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=400)
    license: str = Field(min_length=1, max_length=300)
    access: Literal["open", "open_rate_limited", "requires_key", "blocked", "paid"]
    period: str = Field(min_length=1, max_length=80)
    geography: str = Field(min_length=1, max_length=200)
    sectors: tuple[Sector, ...] = Field(min_length=1)
    note: str = Field(default="", max_length=600)


class FetchRecord(IntakeModel):
    """一次获取的结果（运行元数据）：状态、原始文件的 SHA-256 与大小。获取时间与是否命中缓存不进内容身份；原始文件的
    SHA-256 进（资料变了，产物身份就变）。"""

    source_id: IdStr
    request_key: Sha256
    url: str
    status: Literal["ok", "http_error", "blocked", "timeout", "error", "not_cached"]
    http_status: int | None = None
    raw_sha256: Sha256 | None = None
    bytes: int | None = None
    retrieved_at: str | None = None
    from_cache: bool = False
    note: str = Field(default="", max_length=400)


# --------------------------------------------------------------------------- 标准化记录


class GeoRef(IntakeModel):
    """一条记录适用的地理：国家、地理体系、代码、层级、名称，以及它与验证都会区的关系。"""

    country: str = Field(min_length=2, max_length=2)
    scheme: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=1, max_length=40)
    level: GeoLevel
    label: str = Field(min_length=1, max_length=160)
    cbsa: str | None = None  # 它服务的验证都会区（CBSA2023 代码）；全国性的记录为空
    relation: GeoRelation = "exact"


class Period(IntakeModel):
    """观测时期：说明文字与起止日期（含两端）。"""

    label: str = Field(min_length=1, max_length=80)
    start: _dt.date
    end: _dt.date

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.end < self.start:
            raise ValueError(f"时期 {self.label}: 结束早于开始")
        return self


class SourceRef(IntakeModel):
    """原始记录定位：来源、在原始文件里怎样找到这条（文件 / 表 / 行键）、原始文件的 SHA-256。"""

    source_id: IdStr
    locator: str = Field(min_length=1, max_length=400)
    raw_sha256: Sha256 | None = None


class FieldValue(IntakeModel):
    """一个字段：原始值与原始单位、标准化值与单位、计价口径、来源性质、方法与假设，或者缺失的原因。

    ``value`` 用文字保存（十进制精确文字或类别），避免浮点在内容身份里抖动。金额字段必须写 ``numeraire``：原始报价的口径
    （例如 ``USD_2024_NOMINAL``）与标准口径（``USD_2024_LOCAL``）是两件事，换口径要有方法，不能只换标签。
    """

    name: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
    status: FieldStatus
    raw: str | None = Field(default=None, max_length=200)
    raw_unit: str | None = Field(default=None, max_length=60)
    value: str | None = Field(default=None, max_length=200)
    unit: str | None = Field(default=None, max_length=60)
    numeraire: str | None = Field(default=None, max_length=40)
    kind: FactKind = "observed"
    method: str | None = Field(default=None, max_length=80)
    assumptions: tuple[str, ...] = ()
    reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _status_consistent(self) -> FieldValue:
        if self.status == "present":
            if self.value is None:
                raise ValueError(f"{self.name}: 有值的字段必须写标准化值")
            if self.kind in ("derived", "estimated") and not self.method:
                raise ValueError(f"{self.name}: 派生或估计的值必须写方法")
            if self.kind == "assumption" and not self.assumptions:
                raise ValueError(f"{self.name}: 假设值必须引用登记过的假设")
        else:
            if self.value is not None:
                raise ValueError(f"{self.name}: {self.status} 的字段不能有标准化值（未知不当零）")
            if not self.reason:
                raise ValueError(f"{self.name}: {self.status} 必须写原因")
        return self


def missing(name: str, status: FieldStatus, reason: str) -> FieldValue:
    """一个没有值的字段（缺失、隐去、无法映射、访问受限或不适用）。"""
    return FieldValue(name=name, status=status, reason=reason)


class StdRecord(IntakeModel):
    """一条标准化的真实记录（一个样本住房单位、一个职业的工资分布、一项食品均价、一家餐馆、一个家电型号、一项活动……）。

    ``weight`` 是来源给的抽样权重（例如 ACS 的住房权重），只在需要还原总体时用；``attributes`` 是保留但**不评分**的类别事实
    （菜系、品牌、型号、获得渠道……）；``terms`` 记金额的包含关系（税、运费、服务费、水电是否已含在价格里）。
    """

    record_id: IdStr
    sector: Sector
    record_type: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
    label: str = Field(min_length=1, max_length=200)
    geo: GeoRef
    period: Period
    source: SourceRef
    fields: tuple[FieldValue, ...]
    attributes: dict[str, Scalar] = Field(default_factory=dict)
    terms: dict[str, Terms] = Field(default_factory=dict)
    weight: str | None = None

    @model_validator(mode="after")
    def _unique_fields(self) -> StdRecord:
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError(
                f"{self.record_id}: 字段重复 {sorted({n for n in names if names.count(n) > 1})}"
            )
        return self

    def field(self, name: str) -> FieldValue | None:
        return next((f for f in self.fields if f.name == name), None)


# --------------------------------------------------------------------------- 方法与假设


class MethodSpec(IntakeModel):
    """一个映射或换算方法：编号与版本、做什么、属于 A / B / 换算、输入与已知偏差。方法或版本变了，产物身份就变。"""

    method_id: IdStr
    version: str = Field(min_length=1, max_length=20)
    family: Literal["conversion", "mapping_a", "mapping_b", "menu", "classification"]
    summary_zh: str = Field(min_length=1, max_length=600)
    inputs: tuple[str, ...] = ()
    known_biases_zh: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.method_id}@{self.version}"


class AssumptionSpec(IntakeModel):
    """一条登记过的假设或探索性代理设定：说了什么、用在哪里、为什么不是测量、影响谁。"""

    assumption_id: IdStr
    kind: Literal["assumption", "exploratory_proxy"]
    statement_zh: str = Field(min_length=1, max_length=600)
    applies_to: tuple[str, ...] = Field(min_length=1)
    why_not_measured_zh: str = Field(min_length=1, max_length=400)


# --------------------------------------------------------------------------- 机会注册表


class Calendar(IntakeModel):
    """事实日历：开放的起止日期、星期与时段、季节。未知就写 ``known = False`` 与原因，不当成全年开放。"""

    known: bool
    start: _dt.date | None = None
    end: _dt.date | None = None
    days: tuple[str, ...] = ()
    times: str | None = Field(default=None, max_length=80)
    season: str | None = Field(default=None, max_length=40)
    reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _known_consistent(self) -> Calendar:
        if not self.known and not self.reason:
            raise ValueError("日历未知必须写原因")
        if self.start and self.end and self.end < self.start:
            raise ValueError("日历结束早于开始")
        return self


class Capabilities(IntakeModel):
    """四种能力分开记：已采集（有真实来源）、能供规划参考（有足够的可读事实）、可执行（能编进当前引擎且有报价、供给已知）、
    可评分（能对应现有评分项并有资料）。"""

    collected: bool
    planning_reference: bool
    executable: bool
    scorable: bool


class CompileInfo(IntakeModel):
    """能否编进当前引擎（E1 的 ``LifeOpportunity``）：可以 / 需要 E1 扩展 / 不能，以及原因代码。"""

    status: CompileStatus
    reasons: tuple[str, ...] = ()
    e1_category: str | None = None

    @model_validator(mode="after")
    def _reasons(self) -> CompileInfo:
        if self.status != "compilable" and not self.reasons:
            raise ValueError("不能直接编译时必须写原因")
        return self


class Opportunity(IntakeModel):
    """注册表里的一项机会。稳定：同一份资料与方法得到同一编号与内容；菜单只引用它，不改写它。

    ``target_weight`` 是它在目标分布里的权重（例如 B 模型里所在格的人群份额 ÷ 这一格生成的候选数），与候选生成密度分开：
    某一格多生成几个候选，这一格的总权重不变。``weight_basis`` 写明权重从哪来；探索性代理设定（例如就业存量份额）必须写明，
    不能叫成功概率。
    """

    opportunity_id: IdStr
    sector: Sector
    kind: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
    label: str = Field(min_length=1, max_length=200)
    cbsa: str | None = None  # 适用的验证都会区；全国性的（例如全国认证的家电型号）为空
    geo: GeoRef
    origin: Origin
    method: str | None = None  # 生成它的映射方法（method_id@version）；真实记录直接成为机会时为空
    lineage: tuple[str, ...] = Field(min_length=1)  # 用到的记录编号或条件表编号
    facts: tuple[FieldValue, ...] = ()
    attributes: dict[str, Scalar] = Field(default_factory=dict)
    terms: dict[str, Terms] = Field(default_factory=dict)
    supply_status: SupplyStatus
    data_status: DataStatus
    missing_fields: tuple[str, ...] = ()
    target_weight: str | None = None
    weight_basis: str | None = Field(default=None, max_length=160)
    stratum: str | None = Field(default=None, max_length=80)
    calendar: Calendar | None = None
    menu_policy: MenuPolicyKind
    capabilities: Capabilities
    compile: CompileInfo
    weakest_kind: FactKind

    @model_validator(mode="after")
    def _consistent(self) -> Opportunity:
        if self.origin != "record" and not self.method:
            raise ValueError(f"{self.opportunity_id}: 映射生成的机会必须写方法")
        if (self.target_weight is None) != (self.weight_basis is None):
            raise ValueError(f"{self.opportunity_id}: 目标权重与权重依据必须同时有或同时没有")
        if self.capabilities.executable and self.compile.status != "compilable":
            raise ValueError(f"{self.opportunity_id}: 不能编译的机会不能标为可执行")
        if self.capabilities.executable and self.supply_status != "offered":
            raise ValueError(f"{self.opportunity_id}: 供给不确定的机会不能标为可执行")
        if self.data_status == "complete" and self.missing_fields:
            raise ValueError(f"{self.opportunity_id}: 资料齐全就不能有缺失字段")
        names = [f.name for f in self.facts]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.opportunity_id}: 事实字段重复")
        return self

    def fact(self, name: str) -> FieldValue | None:
        return next((f for f in self.facts if f.name == name), None)


# --------------------------------------------------------------------------- 菜单


class MenuPolicy(IntakeModel):
    """一个板块的菜单政策：完整目录、按事实日历开放、或抽取本年接触机会（分层、每层名额与算法版本）。"""

    sector: Sector
    kind: MenuPolicyKind
    size: int | None = Field(default=None, ge=1)
    strata_key: str | None = None
    per_stratum: int | None = Field(default=None, ge=1)
    note_zh: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def _sample_needs_size(self) -> MenuPolicy:
        if self.kind == "annual_exposure_sample" and self.size is None and self.per_stratum is None:
            raise ValueError(f"{self.sector}: 抽样菜单必须写总名额或每层名额")
        return self


class Menu(IntakeModel):
    """某座城市、某一年、某一板块、某次环境重复的菜单。抽样键由资料身份、城市、年份、板块、环境重复与算法决定，**不含分身编号**：
    同一环境重复下的分身共享同一份菜单；恢复、重新查询与修改计划都不重抽。"""

    menu_id: IdStr
    build_id: Sha256
    cbsa: str
    year: int = Field(ge=1, le=10)
    sector: Sector
    env_repeat: int = Field(ge=0)
    policy: MenuPolicyKind
    algorithm: str
    seed: Sha256
    n_eligible: int = Field(ge=0)
    #: 合格集合（排序后的机会编号）的内容身份：完整目录不逐条抄编号，但能核对是哪一份集合。
    eligible_identity: Sha256
    #: ``listed``：``selected`` 就是菜单；``all_eligible``：完整目录，菜单 = 全部合格机会（``selected`` 为空）。
    selection: Literal["listed", "all_eligible"]
    selected: tuple[str, ...]
    inclusion_note_zh: str = Field(default="", max_length=400)

    @model_validator(mode="after")
    def _unique(self) -> Menu:
        if len(set(self.selected)) != len(self.selected):
            raise ValueError(f"{self.menu_id}: 菜单里有重复机会")
        if self.selection == "all_eligible" and self.selected:
            raise ValueError(f"{self.menu_id}: 完整目录不逐条列编号")
        if self.selection == "listed" and len(self.selected) > self.n_eligible:
            raise ValueError(f"{self.menu_id}: 选中的比合格的还多")
        return self


def as_dict(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")
