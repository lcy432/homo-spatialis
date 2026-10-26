"""E3-A 产品共享结构：E3 与 E5 共用的消息定义（DECISIONS/0034）。**这是唯一的权威来源。**

``contracts/product/v1/`` 里的 JSON Schema 由本文件导出（``scripts/product_e3a.py export-schema``），一致性检查用同一个入口的
``check-schema``；E5 读 schema 与公共样例，不另写一套职业、偏好、计价或结果语义。

这里**只有形状与局部约束**，没有业务计算：输入校验、职业匹配、精确分配、确认身份、编译、预检查与结果投影在 ``product`` 包里；
生活、评分与评价仍由 E1 完成。产品消息不是 E1 的内部对象，E1 的内部对象（评分规格、研究真值、评价明细……）也不直接出现在这些
消息里——结果只是 E1 报告的白名单投影。

消息一览（共享约定第 7 节的消息草案在本轮落地的名字）：

==========================  ===========================================================================
ProductCapabilities         城市、职业搜索能力、六维与物质词表、健康题、硬要求选项、支持范围、单位与限制
UserInputDraft              原始答案及逐项状态（未答 / 已提供 / 明确无所谓 / 暂不确定；明确 0 是已提供的数值）
ProfileReview               可读解释、职业候选、缺失、未支持项与待确认内容
ConfirmedProfile            已确认语义、精确数值、来源与内容身份
ProductPreflight            开局摘要、可执行 / 可评分 / 可反馈、资料缺口、范围与运行绑定
RunSubmission / RunReceipt  确认与预检查的引用、幂等键、接受 / 拒绝与运行 ID（线上服务留 E3-C，本轮只有契约与示例）
RunProgress                 阶段、已执行年度、暂停、费用状态、恢复状态
ProductResult               生活事实、评价、汇总、缺失、来源与限制（E1 报告的白名单投影）
SourceFact 等               便利资料候选、目录编译与“选项目录覆盖”（与实际轨迹能否评分是两件事）
==========================  ===========================================================================

版本分层（共同元数据 ``ProductMeta``）：结构版本、问卷 / 题干语义版本、词表版本、健康问卷版本、转换版本、资料包身份。内容指纹
（``*_fingerprint``）只核对内容，**不代替**线上认证与授权（E3-C / E5-C）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

#: 结构版本（字段形状）。字段含义或形状一变就换名字，并在 ADR 记录。v3.1（DECISIONS/0042）：结果里的住处、通勤、用车权、
#: 设备故障与税，逐城的资料性质，预检查的范围说明——都是新增的可选字段，v3 的字段含义不变；目录仍是 contracts/product/v3。
#: v3.2（E3C，DECISIONS/0043）：托管服务的消息（会话与次数、草稿保存、确认请求、运行意图、删除回执、统一错误），费用状态的
#: 分项（已结算、不确定、在途），进度的更新时间与回执的 ``hosted_service``——都是新增，v3.1 的字段含义不变。
#: v3.3（E3B 映射修正，DECISIONS/0044）：年度事实新增日常支持（``support``）与个人消费篮子（``consumption``）；物质四项里
#: ``daily_convenience`` 改为“日常支持与便利”、``personal_enjoyment`` 改为抽象消费篮子——含义变了，问卷与词表一起升版本，旧的确认
#: 画像要重新审阅确认。
#: v3.4（E3 输入体验收敛，DECISIONS/0046）：能力清单新增输入目录（``input_catalog``）；草稿新增按目录直接选职业、最高学历 / 证书 /
#: 相关经验、兴趣与口味的面板多选（``selected``）；新问卷不再收集硬要求与属性偏好（字段留着，只为读回历史对象）。v3.3 的对象仍能
#: 读回（``ProductMeta.schema_version`` 两个都接受），新的审阅与确认必须是 v3.4；目录仍是 contracts/product/v3。
#: v3.5（E3 输入与服务体验，DECISIONS/0047）：六维与物质四项的独立评分（``ratings_normalized_v1``：原始评分 → 精确归一权重），
#: 设置预览与“确认并开始”的预先授权，运行意图的进度摘要。v3.3、v3.4 的对象照原版本读回与序列化（``_Since``），新对象是 v3.5。
PRODUCT_SCHEMA_VERSION = "e3a-product-schema-v3.5"
#: 问卷 / 题干语义版本：六维与物质的题干、定义、参考改善范围与控件规则。题干一变，旧确认失效、需要重新确认。
#: v0.4：职业按目录选、学历 / 证书 / 经验分开问、兴趣与口味面板多选；不再问硬要求与属性偏好。
#: v0.5：六维与物质四项改为独立评分（每项 0–100，不必凑满 100）；旧的百分比分配不再用来确认新画像。
QUESTIONNAIRE_VERSION = "e3a-questionnaire-v0.5-draft"
#: 词表版本：六维键、物质键、职业受控词表、硬要求选项与属性偏好种类。v5：职业目录的分组与搜索词、学历选项（输入目录）。
VOCABULARY_VERSION = "e3a-vocabulary-v5"
#: 独立健康问卷版本（暂定文案，未经量表验证）。
HEALTH_QUESTIONNAIRE_VERSION = "e3a-health-coping-q-v0.1-draft"
#: 转换版本：原始答案 → 确认画像 → E1 输入的规则（精确分数、规范写法、金额换算、标准开局编译）。v3：学历 / 证书 / 相关经验的
#: 结构化答案按资料包已有的规则换成资格（``confirmed_experience_mapping_v1``）；面板选中的兴趣与口味直接是确认的类别。
#: v4：独立评分按 ``ratings_normalized_v1`` 换成权重（每项 = 100 × 原始评分 ÷ 原始合计，精确分数；不加任何系数）。
CONVERSION_VERSION = "e3a-conversion-v4"
#: 读回历史对象时仍接受的结构版本（v3.3 的草稿、审阅、画像与结果；新对象一律是 ``PRODUCT_SCHEMA_VERSION``）
READABLE_SCHEMA_VERSIONS = (
    "e3a-product-schema-v3.3",
    "e3a-product-schema-v3.4",
    "e3a-product-schema-v3.5",
)
#: 最早仍能读回的结构版本（v3.3）；各版本新增了什么见 ``_Since``
LEGACY_SCHEMA_VERSION = READABLE_SCHEMA_VERSIONS[0]
_V34, _V35 = READABLE_SCHEMA_VERSIONS[1], READABLE_SCHEMA_VERSIONS[2]
#: 独立评分的换算规则（v3.5）
RATINGS_RULE = "ratings_normalized_v1"
#: 自由文字的上限（沿用 E1 画像的 2,000 字符；超限明确报错，不静默截断）。
FREE_TEXT_MAX_CHARS = 2000

_SHA256 = r"^[0-9a-f]{64}$"
_EXACT = r"^(0|-?[1-9][0-9]*)(/[1-9][0-9]*)?$"
_REF = r"^[A-Za-z0-9][A-Za-z0-9_.:\-/]{0,127}$"
Sha256 = Annotated[str, Field(pattern=_SHA256)]
ExactText = Annotated[str, Field(pattern=_EXACT)]
RefStr = Annotated[str, Field(pattern=_REF)]


class ProductModel(BaseModel):
    """产品消息的基类：不可变、拒绝未知字段、拒绝 inf/nan（与 E1 契约同样的保护）。"""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def canonical_json(content: object) -> str:
    """规范 JSON：键排序、紧凑分隔、保留中文。内容指纹都用它。"""
    return json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def content_fingerprint(content: object) -> str:
    """一份规范化内容的 SHA-256（只核对内容，不是认证）。"""
    return hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Since:
    """某个结构版本新增的一处内容（DECISIONS/0046 第 9 条、0047）。``path`` 用点分隔，``*`` 表示列表的每一项。``states`` 不为空时是
    那个版本新增的取值（更早版本的对象不能用）；否则是新增的字段：更早版本的对象里只能是 ``default``，写出时照原样不写。"""

    version: str
    path: str
    default: Any = None
    states: tuple[str, ...] = ()


def _older(obj: Any, version: str) -> bool:
    return READABLE_SCHEMA_VERSIONS.index(obj.meta.schema_version) < READABLE_SCHEMA_VERSIONS.index(
        version
    )


def _at(value: Any, parts: list[str]) -> Any:
    """一个对象上某个路径的全部取值（遇到空值就停；``*`` 展开列表）。"""
    if not parts:
        yield value
        return
    if value is None:
        return
    head, rest = parts[0], parts[1:]
    if head == "*":
        for item in value:
            yield from _at(item, rest)
    else:
        yield from _at(getattr(value, head), rest)


def _version_problems(obj: Any, rules: tuple[_Since, ...]) -> list[str]:
    """一个旧版本对象里不该出现的新版本内容：新增字段不是默认值（未答 / 空），或用了新版本才有的取值。"""
    bad = []
    for r in rules:
        if not _older(obj, r.version):
            continue
        for v in _at(obj, r.path.split(".")):
            if (v in r.states) if r.states else (v != r.default):
                bad.append(r.path)
                break
    return bad


def _drop(data: Any, parts: list[str]) -> None:
    head, rest = parts[0], parts[1:]
    if head == "*":
        if isinstance(data, list) and rest:
            for item in data:
                _drop(item, rest)
        return
    if not isinstance(data, dict):
        return
    if rest:
        _drop(data.get(head), rest)
    else:
        data.pop(head, None)


def _versioned_dump(obj: Any, data: Any, rules: tuple[_Since, ...]) -> Any:
    """按对象自己的结构版本序列化：旧版本的对象没有后来新增的字段，写出时照原样不写——所以旧对象的 JSON、内容身份、草稿指纹与
    显示绑定的消息摘要都与它当初生成时相同，不重签、不改写旧绑定。当前版本的对象照常写出全部字段。"""
    if not isinstance(data, dict) or obj.meta.schema_version == PRODUCT_SCHEMA_VERSION:
        return data
    for r in rules:
        if not r.states and _older(obj, r.version):
            _drop(data, r.path.split("."))
    return data


def identity_content(model: BaseModel, fingerprint_field: str) -> dict[str, Any]:
    """参与内容身份的字段：除了指纹本身与公共样例的出处标签（``meta.sample``）以外的全部内容。样例标签只说明样例从哪来，
    不改变它的含义，所以同一份内容打不打标签身份都相同。"""
    content = model.model_dump(mode="json", exclude={fingerprint_field})
    meta = content.get("meta")
    if isinstance(meta, dict):
        meta.pop("sample", None)
    return content


# ==========================================================================
# 共同元数据、错误与状态词
# ==========================================================================


#: 资料包状态：合成测试资料，或冻结的真实资料包（DECISIONS/0042：城市资料来自公开来源与登记的模型设定）。
PackStatus = Literal["synthetic_fixture_only", "real_data_frozen"]


class DataPackRef(ProductModel):
    """一份资料包的身份：编号、内容指纹与状态（合成测试资料，或冻结的真实资料包）。"""

    pack_id: RefStr
    content_fingerprint: Sha256
    status: PackStatus
    note: str = Field(min_length=1, max_length=400)


class SampleLabel(ProductModel):
    """公共样例的出处标签（只出现在样例里；真实运行的消息不写）。

    * ``offline_run``：由离线闭环实际执行产生；
    * ``controlled_fixture``：受控测试夹具（例如写好的计划、注入的事件、构造的资料缺口），标明来源；
    * ``contract_example``：线上服务（幂等提交、查询、断线恢复）尚未实现，这里只是**标明为示例**的契约响应。
    """

    origin: Literal["offline_run", "controlled_fixture", "contract_example"]
    synthetic: Literal[True] = True
    produced_by: str = Field(min_length=1, max_length=300)
    note: str = Field(min_length=1, max_length=600)


class ProductMeta(ProductModel):
    """每条产品消息都带的共同元数据：结构、问卷、词表、健康问卷、转换的版本与资料包身份。"""

    #: v3.3、v3.4 只为读回历史对象（旧草稿、画像、运行与结果）；新对象都是当前版本，审阅与确认按当前版本核对
    schema_version: Literal[
        "e3a-product-schema-v3.3", "e3a-product-schema-v3.4", "e3a-product-schema-v3.5"
    ]
    questionnaire_version: str = Field(min_length=1, max_length=80)
    vocabulary_version: str = Field(min_length=1, max_length=80)
    health_questionnaire_version: str = Field(min_length=1, max_length=80)
    conversion_version: str = Field(min_length=1, max_length=80)
    data_pack: DataPackRef
    sample: SampleLabel | None = None


IssueSeverity = Literal["error", "warning", "info"]


class ProductIssue(ProductModel):
    """一条可展示的问题：机器代码、字段位置、中文原因、影响与下一步动作。E5 原样展示中文，不另写解释。"""

    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    severity: IssueSeverity
    field: str = Field(min_length=1, max_length=160)
    message_zh: str = Field(min_length=1, max_length=400)
    impact_zh: str = Field(min_length=1, max_length=300)
    next_action_zh: str = Field(min_length=1, max_length=300)
    #: 不含内部内容的审计编号（E3-A 收尾 F5）：维护者用它在服务端的内部诊断或运行归档里找到完整原文。没有内部记录时为空。
    audit_ref: str | None = Field(default=None, max_length=200)


class PublicExplanation(ProductModel):
    """给用户看的状态说明（E3-A 收尾 F5）：只由结构化类别生成——类别代码、中文原因、影响与下一步，加一个不含内部内容的审计编号。

    不透传异常原文、内部路径或参数；完整原文留在运行归档或内部诊断里，``audit_ref`` 指向它（运行编号、分身与记录文件）。"""

    category: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
    reason_zh: str = Field(min_length=1, max_length=300)
    impact_zh: str = Field(min_length=1, max_length=300)
    next_action_zh: str = Field(min_length=1, max_length=300)
    audit_ref: str | None = Field(default=None, max_length=200)


#: 答案状态：未答、已提供（明确 0 也是已提供）、明确无所谓、暂不确定。四者互不代替。
AnswerState = Literal["unanswered", "provided", "indifferent", "unsure"]
#: 支持状态：本版能完整使用、部分使用（例如只有城市背景、没有行动机制）、不能使用（只留存）。
SupportState = Literal["supported", "partially_supported", "unsupported"]
#: 确认状态：待确认、已确认、修改后失效。
ConfirmationState = Literal["pending", "confirmed", "invalidated"]
#: 用途：规划（给分身看）、开局状态、资格、评分、仅留存。
Usage = Literal["planning", "start_state", "qualification", "scoring", "retained_only"]


class ExactNumber(ProductModel):
    """一个精确数：``exact`` 是规范分数写法（参与计算与比较），``display`` 只供显示（四舍五入，不回流计算）。"""

    exact: ExactText
    display: str = Field(min_length=1, max_length=40)


class ProductMoney(ProductModel):
    """一笔钱：整数最小单位（美元是分）、币种与计价口径；``display`` 只供显示。"""

    amount_minor: int
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    numeraire_id: str = Field(min_length=1, max_length=40)
    display: str = Field(min_length=1, max_length=40)


# ==========================================================================
# ProductCapabilities：E3 发布、E5 呈现（界面不能提供服务端没有的选择）
# ==========================================================================


class CityOption(ProductModel):
    """一个候选城市：完整身份（国家 / 地理体系 / 代码）与地理边界版本；不用中文名拼接资料。"""

    city_ref: RefStr
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    scheme: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=80)
    geography_version: str = Field(min_length=1, max_length=80)
    data_status: Literal["synthetic_fixture", "real_data"]
    selectable: bool
    note: str = Field(min_length=1, max_length=300)


class DimensionDefinition(ProductModel):
    """六维之一的统一定义：中性题干、具体的参考改善范围与当前覆盖。全比较组共用，不逐城重新标定用户权重。"""

    key: Literal["material", "safety", "opportunity", "leisure", "environment", "commute"]
    label: str = Field(min_length=1, max_length=40)
    definition: str = Field(min_length=1, max_length=400)
    reference_improvement: str = Field(min_length=1, max_length=400)
    support: SupportState
    support_note: str = Field(min_length=1, max_length=400)


class MaterialLeafDefinition(ProductModel):
    """物质细项之一：含义、支持状态与说明。细分是父项内部的相对重要性，不是开支比例。"""

    key: str = Field(min_length=1, max_length=48)
    label: str = Field(min_length=1, max_length=40)
    definition: str = Field(min_length=1, max_length=400)
    support: SupportState
    note: str = Field(min_length=1, max_length=400)


class RatingsRules(ProductModel):
    """独立评分（v3.5，``ratings_normalized_v1``）：每项 0–100，滑条步长 1，数字精调最多 ``max_decimals`` 位小数；不必凑满 100。
    全部要回答的项都回答了、原始合计大于 0 之后，E3 按 100 × 评分 ÷ 合计换成精确权重。“同等重视”是用户主动把每项设为 50。"""

    method: Literal["ratings_normalized_v1"]
    minimum: Literal[0] = 0
    maximum: Literal[100] = 100
    slider_step: Literal[1] = 1
    max_decimals: Annotated[int, Field(ge=0, le=6)]
    equal_weight_rating: Literal["50"] = "50"
    rules: tuple[str, ...] = Field(min_length=1)


class PointsRules(ProductModel):
    """定和量表的规则：合计、手工输入的小数位、显示小数位、精确分数的规范写法与均分的精确值。v3.5 起新问卷用 ``ratings``。"""

    total: Literal[100]
    manual_entry: str = Field(min_length=1, max_length=300)
    manual_max_decimals: Annotated[int, Field(ge=0, le=6)]
    display_decimals: Annotated[int, Field(ge=0, le=6)]
    canonical_form: str = Field(min_length=1, max_length=300)
    equal_split_six: ExactText
    equal_split_material: ExactText
    rules: tuple[str, ...]
    ratings: RatingsRules | None = None


class HealthQuestion(ProductModel):
    question_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,47}$")]
    text: str = Field(min_length=1, max_length=300)


class HealthOptionDef(ProductModel):
    option_id: Literal["agree", "disagree", "no_clear_tendency", "unsure"]
    label: str = Field(min_length=1, max_length=20)
    meaning: str = Field(min_length=1, max_length=200)


class HealthQuestionnaire(ProductModel):
    """独立健康问卷（不占六维 100 分，不是第七个评分权重）。题目是暂定文案，**未经量表验证**。"""

    version: str = Field(min_length=1, max_length=80)
    status: Literal["draft_wording_not_validated"]
    common_prompt: str = Field(min_length=1, max_length=300)
    questions: tuple[HealthQuestion, ...] = Field(min_length=1)
    options: tuple[HealthOptionDef, ...] = Field(min_length=4, max_length=4)
    unanswered_rule: str = Field(min_length=1, max_length=300)
    rules: tuple[str, ...]


class HardRequirementOption(ProductModel):
    """一个受支持的硬要求选项：有定义、有资料、能按月核验。**核验只单独报告，环境不拦截计划**（``enforced_by_execution``）。"""

    option_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")]
    label: str = Field(min_length=1, max_length=80)
    parameter: str = Field(min_length=1, max_length=48)
    unit: str = Field(min_length=1, max_length=40)
    minimum: int
    maximum: int
    step: Annotated[int, Field(ge=1)]
    compiles_to: str = Field(min_length=1, max_length=300)
    enforced_by_execution: Literal[False] = False
    verification: str = Field(min_length=1, max_length=300)


class AttributeKindDef(ProductModel):
    """属性偏好的种类（品牌、菜系、制作方式、档次、服务质量、上门顾虑……）：本版的支持状态与用途。"""

    kind: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,47}$")]
    label: str = Field(min_length=1, max_length=40)
    support: SupportState
    usage: tuple[Usage, ...] = Field(min_length=1)
    note: str = Field(min_length=1, max_length=300)


class MoneyBasisOption(ProductModel):
    """一种受支持的现金口径：币种、金额时期、含义与换算规则。没有列出的组合没有合法换算，明确阻断。"""

    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    price_period: str = Field(min_length=1, max_length=20)
    meaning: Literal["liquid_funds_v1"]
    conversion_rule: str = Field(min_length=1, max_length=80)
    numeraire_id: str = Field(min_length=1, max_length=40)
    note: str = Field(min_length=1, max_length=300)


class OccupationCapability(ProductModel):
    """职业匹配能力：受控词表与明确规则，用户确认少量可读候选，不填职业代码。"""

    vocabulary_version: str = Field(min_length=1, max_length=80)
    method: Literal["controlled_vocabulary_alias_match_v1"]
    languages: tuple[str, ...] = Field(min_length=1)
    concept_count: Annotated[int, Field(ge=1)]
    max_candidates: Annotated[int, Field(ge=1, le=10)]
    users_enter_codes: Literal[False] = False
    note: str = Field(min_length=1, max_length=400)


class AbilityOption(ProductModel):
    """一项可以由用户确认的资格事实（不从职称自动授予）。"""

    ability_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,47}$")]
    label: str = Field(min_length=1, max_length=80)
    ask_when: str = Field(min_length=1, max_length=300)


class VocabularyTerm(ProductModel):
    """有限词表里的一项（休闲活动类别或菜系）：键、显示名与用来解释文字的别名（明确映射，不调用模型猜）。"""

    key: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
    label: str = Field(min_length=1, max_length=40)
    aliases: tuple[Annotated[str, Field(min_length=1, max_length=40)], ...] = Field(min_length=1)


class InterestCapability(ProductModel):
    """兴趣与口味（DECISIONS/0041）：可选的文字描述先形成待确认的解释；用户确认后的类别才进入共同、有限的匹配规则。"""

    leisure_categories: tuple[VocabularyTerm, ...] = Field(min_length=1)
    cuisines: tuple[VocabularyTerm, ...] = Field(min_length=1)
    leisure_states_zh: tuple[str, ...] = Field(min_length=1)
    taste_states_zh: tuple[str, ...] = Field(min_length=1)
    matching_note_zh: str = Field(min_length=1, max_length=400)


_KEY = r"^[a-z][a-z0-9_]{0,47}$"
_CONCEPT = r"^[a-z][a-z0-9_.]{1,79}$"
#: 学历、证书与相关经验三种答案共用的作答状态：未答、已回答、暂不确定（都不授予资格，除了已回答里明确选中的）。
QualificationAnswerState = Literal["unanswered", "provided", "unsure"]


class CatalogGroup(ProductModel):
    """职业目录的一个浏览分组。分组只用于浏览，不改变职业映射，也不进确认画像。"""

    group_id: Annotated[str, Field(pattern=_KEY)]
    label: str = Field(min_length=1, max_length=40)
    order: Annotated[int, Field(ge=1)]


class CatalogOccupation(ProductModel):
    """目录里的一个职业：概念编号（与文字匹配、确认画像用的是同一个概念）、唯一的主分组、名称、职责、搜索词与相关资格。

    ``search_terms`` 是明确登记的中英文搜索词（名称、资料包别名与目录补充的叫法，例如“程序员”），照原文给出、不翻译，只用来筛选；
    搜索框里的文字不保存、不进画像。``qualification_refs`` 是这个职业的开局岗位要求的资格（能力清单 ``abilities`` 里的编号），只供
    显示；学历包含关系、证书授予与经验年数的判断都在 E3。分类代码只在内部保留，不在这里。"""

    concept_id: Annotated[str, Field(pattern=_CONCEPT)]
    group_id: Annotated[str, Field(pattern=_KEY)]
    order: Annotated[int, Field(ge=1)]
    label: str = Field(min_length=1, max_length=80)
    duties: tuple[str, ...] = Field(min_length=1)
    search_terms: tuple[Annotated[str, Field(min_length=1, max_length=120)], ...] = Field(
        min_length=1
    )
    qualification_refs: tuple[str, ...] = ()
    experience_question: str | None = Field(default=None, max_length=80)


class EducationOption(ProductModel):
    """最高学历的一个选项（单选，按从低到高的顺序）。职业证书不在这里，另有证书多选。"""

    option_id: Annotated[str, Field(pattern=_KEY)]
    label: str = Field(min_length=1, max_length=40)
    order: Annotated[int, Field(ge=1)]


class CertificateOption(ProductModel):
    """一张可以多选的职业证书（中学后非学位证书）：资格编号、名称，以及要求它的职业。学历不会授予它。"""

    ability_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,47}$")]
    label: str = Field(min_length=1, max_length=80)
    occupations: tuple[Annotated[str, Field(pattern=_CONCEPT)], ...] = Field(min_length=1)


class ExperienceQuestion(ProductModel):
    """一个相关工作经验问题：只在选中的职业属于 ``applies_to`` 时问；用户在 ``related`` 列出的职业里填做过几年。年数门槛只写在
    ``prompt`` 的说明里，由 E3 判断（E5 不判断）。"""

    question_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,79}$")]
    ability_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{1,47}$")]
    applies_to: tuple[Annotated[str, Field(pattern=_CONCEPT)], ...] = Field(min_length=1)
    related: tuple[Annotated[str, Field(pattern=_CONCEPT)], ...] = Field(min_length=1)
    prompt: str = Field(min_length=1, max_length=300)


class QualificationStateText(ProductModel):
    """学历、证书或经验的一种作答状态的名称与含义。"""

    section: Literal["education", "certificates", "experience"]
    state: QualificationAnswerState
    name: str = Field(min_length=1, max_length=60)
    meaning: str = Field(min_length=1, max_length=300)


class SearchNote(ProductModel):
    """一组搜索词的说明：它们对应哪些已支持的键（可以为空：本版不支持），以及要告诉用户的话。搜索词照原文，不翻译。
    例如“川菜”只找到已支持的“中餐”，并说明本版不区分地方菜；“徒步”找不到任何类别，并说明本版还没有、不会算成健身。"""

    note_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,79}$")]
    terms: tuple[Annotated[str, Field(min_length=1, max_length=40)], ...] = Field(min_length=1)
    points_to: tuple[Annotated[str, Field(pattern=_KEY)], ...] = ()
    note: str = Field(min_length=1, max_length=300)


class InterestGroup(ProductModel):
    """兴趣或菜系面板的一个浏览分组。"""

    group_id: Annotated[str, Field(pattern=_KEY)]
    label: str = Field(min_length=1, max_length=40)
    order: Annotated[int, Field(ge=1)]


class InterestEntry(ProductModel):
    """面板里的一个类别：能力清单词表里已有的键（不新增类别）、唯一的分组、名称与搜索词（类别名、词表别名与目录补充的叫法，照原文）。"""

    key: Annotated[str, Field(pattern=_KEY)]
    group_id: Annotated[str, Field(pattern=_KEY)]
    order: Annotated[int, Field(ge=1)]
    label: str = Field(min_length=1, max_length=40)
    search_terms: tuple[Annotated[str, Field(min_length=1, max_length=60)], ...] = Field(
        min_length=1
    )


class InterestPanel(ProductModel):
    """兴趣（``leisure``）或口味（``taste``）的多选面板。可以跳过（未答）；选了类别就直接是确认画像里的类别，不需要写文字。"""

    kind: Literal["leisure", "taste"]
    groups: tuple[InterestGroup, ...] = Field(min_length=1)
    entries: tuple[InterestEntry, ...] = Field(min_length=1)
    search_notes: tuple[SearchNote, ...] = ()
    note: str = Field(min_length=1, max_length=300)


class CatalogSearchRules(ProductModel):
    """目录搜索的规则（E5 在完整的小目录里筛选显示；服务端只认提交的编号，不认搜索词）。"""

    method: Literal["catalog_search_nfkc_casefold_all_terms_v1"]
    rules: tuple[str, ...] = Field(min_length=1)
    no_match: str = Field(min_length=1, max_length=300)


class InputSections(ProductModel):
    """新输入流程启用哪些板块。职业按目录直接选（文字描述只为兼容保留，不是必经入口）；兴趣与口味用面板多选；硬要求、属性偏好
    与兴趣的文字解释不在新流程里。"""

    occupation: Literal["catalog_select"] = "catalog_select"
    occupation_text: Literal["compatibility_only"] = "compatibility_only"
    education: bool
    certificates: bool
    experience: bool
    leisure: Literal["panel_select"] = "panel_select"
    taste: Literal["panel_select"] = "panel_select"
    interest_text: Literal[False] = False
    hard_requirements: Literal[False] = False
    attribute_preferences: Literal[False] = False


class InputCatalog(ProductModel):
    """输入目录（v3.4）：职业的分类索引与搜索词、学历 / 证书 / 相关经验问题、兴趣与菜系的分组，以及新流程启用的板块。

    它是一份独立的小配置，引用资料包里已有的职业、资格与兴趣键，不新增职业、资格、活动或评分系数。``content_fingerprint`` 是这份
    配置与所引用资料包的内容身份：按目录选职业的草稿要带上它，目录换了就要重新选（服务端拒绝过期的目录）。"""

    catalog_id: RefStr
    catalog_version: str = Field(min_length=1, max_length=80)
    content_fingerprint: Sha256
    pack_id: str = Field(min_length=1, max_length=80)
    sections: InputSections
    search: CatalogSearchRules
    groups: tuple[CatalogGroup, ...] = Field(min_length=1)
    occupations: tuple[CatalogOccupation, ...] = Field(min_length=1)
    education: tuple[EducationOption, ...] = ()
    certificates: tuple[CertificateOption, ...] = ()
    experience: tuple[ExperienceQuestion, ...] = ()
    qualification_states: tuple[QualificationStateText, ...] = ()
    leisure: InterestPanel
    taste: InterestPanel
    notes: tuple[str, ...] = ()


class ProductCapabilities(ProductModel):
    """E3 发布的能力清单（E5 只能呈现这里有的选择）。"""

    meta: ProductMeta
    capabilities_id: RefStr
    cities: tuple[CityOption, ...] = Field(min_length=1)
    occupation: OccupationCapability
    dimensions: tuple[DimensionDefinition, ...] = Field(min_length=6, max_length=6)
    material_leaves: tuple[MaterialLeafDefinition, ...] = Field(min_length=1)
    points: PointsRules
    health_questionnaire: HealthQuestionnaire
    hard_requirement_options: tuple[HardRequirementOption, ...] = ()
    attribute_kinds: tuple[AttributeKindDef, ...] = ()
    abilities: tuple[AbilityOption, ...] = ()
    money_bases: tuple[MoneyBasisOption, ...] = Field(min_length=1)
    #: 金额的写法（E3-A 收尾 F3）：服务端按这条规则严格解析，E5 照着提示用户；前端提示不能代替服务端检查。
    money_input_rule_zh: str = Field(min_length=1, max_length=300)
    age_min: Annotated[int, Field(ge=14)]
    age_max: Annotated[int, Field(le=120)]
    free_text_max_chars: Literal[2000]
    start_scenario: tuple[str, ...] = Field(min_length=1)
    unsupported: tuple[str, ...] = ()
    limits: tuple[str, ...] = ()
    interview: Literal["reserved_not_implemented"] = "reserved_not_implemented"
    interests: InterestCapability
    #: 新输入流程的目录（v3.4）；资料包没有配目录时为空（只有兼容的文字匹配）
    input_catalog: InputCatalog | None = None


# ==========================================================================
# UserInputDraft：原始答案及逐项状态
# ==========================================================================


class PointEntry(ProductModel):
    """一个维度或细项的作答：已提供时写数值文字（精确十进制或规范分数），其余状态不写数值。明确 0 写 ``"0"``。"""

    state: AnswerState
    value_text: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def _value_matches_state(self) -> PointEntry:
        if (self.state == "provided") != (self.value_text is not None):
            raise ValueError('只有 provided 写 value_text，provided 必须写（明确 0 写 "0"）')
        return self


#: 定和分配的作答方式。``ratings_normalized_v1``（v3.5，新问卷）：每项是 0–100 的独立评分，``value_text`` 写原始评分；
#: ``manual`` / ``equal_split`` 保留原来的含义（百分比、主动平均分配），只为读回旧对象，新问卷不再用它们确认。
AllocationMode = Literal["manual", "equal_split", "ratings_normalized_v1"]


class AllocationAnswer(ProductModel):
    """一组定和分配的作答。``mode = equal_split`` 表示用户**主动**选择了平均分配（不预选、不默认）；``None`` 表示还没开始分配。
    ``ratings_normalized_v1``：独立评分（未答不是 0；“同等重视”由用户主动把每项设为 50）。"""

    mode: AllocationMode | None = None
    entries: dict[str, PointEntry] = Field(default_factory=dict)


class OccupationAnswer(ProductModel):
    """职业。``mode = catalog``（新流程）：从输入目录直接选中的概念编号与那份目录的版本、内容身份，不需要也不保存任何文字（搜索框
    里的字只是查找条件）。``mode = text``（兼容）：原文与语言，再在少量候选里选中一个。概念编号不透明，不是职业代码。"""

    state: AnswerState
    mode: Literal["text", "catalog"] = "text"
    raw_text: str | None = Field(default=None, max_length=300)
    language: str = Field(default="zh", min_length=2, max_length=10)
    selected_candidate: str | None = Field(default=None, max_length=80)
    catalog_version: str | None = Field(default=None, max_length=80)
    catalog_fingerprint: Sha256 | None = None

    @model_validator(mode="after")
    def _mode(self) -> OccupationAnswer:
        if self.mode == "catalog" and self.raw_text is not None:
            raise ValueError("按目录选职业时不带文字：搜索框里的字不保存")
        if self.mode == "text" and (
            self.catalog_version is not None or self.catalog_fingerprint is not None
        ):
            raise ValueError("文字描述的职业不带目录版本")
        return self


class AgeAnswer(ProductModel):
    state: AnswerState
    value: int | None = None


class CashAnswer(ProductModel):
    """开始时可用于生活的流动资金：金额文字、币种、金额时期与口径（不是总资产、信用额度或月薪）。"""

    state: AnswerState
    amount_text: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, max_length=3)
    price_period: str | None = Field(default=None, max_length=20)
    meaning: Literal["liquid_funds_v1"] = "liquid_funds_v1"


class CitiesAnswer(ProductModel):
    state: AnswerState
    city_refs: tuple[str, ...] = ()


class HealthItemAnswer(ProductModel):
    """一题的作答：四个选项之一；``None`` = 未答（与“暂不确定”分开保存）。"""

    question_id: str = Field(min_length=1, max_length=48)
    choice: Literal["agree", "disagree", "no_clear_tendency", "unsure"] | None = None


class HealthAnswers(ProductModel):
    questionnaire_version: str = Field(min_length=1, max_length=80)
    items: tuple[HealthItemAnswer, ...] = ()


class FreeTextAnswer(ProductModel):
    """可选文字。超过 2,000 字符**不截断**：校验时报错，由用户自己删减。"""

    state: AnswerState
    text: str | None = None


#: ``selected``（v3.4）：从面板里直接选中了类别（不写文字）；``described`` 是兼容的文字描述
LeisureAnswerState = Literal[
    "unanswered", "unsure", "no_fixed_hobby", "generic", "described", "selected"
]
TasteAnswerState = Literal["unanswered", "unsure", "no_preference", "described", "selected"]


def _panel_shape(state: str, text: str | None, selected: tuple[str, ...]) -> None:
    if state == "selected" and (text is not None or not selected):
        raise ValueError("面板选择至少选一个类别，而且不带文字（一个都不选就是未答）")


class LeisureInterestAnswer(ProductModel):
    """休闲兴趣：未答、暂不确定、明确没有固定爱好、选择通用休闲评价（“城市有什么合适的就体验什么”），或用文字描述。

    文字描述先由 E3 按词表形成待确认的解释；``selected`` 是用户从解释或词表里**明确选中**的类别——只有选了才进入有评分作用的类别。
    空白不会被当成“通用评价”的选择。"""

    state: LeisureAnswerState = "unanswered"
    text: str | None = Field(default=None, max_length=300)
    selected: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> LeisureInterestAnswer:
        _panel_shape(self.state, self.text, self.selected)
        return self


class TasteAnswer(ProductModel):
    """口味与菜系：未答、暂不确定、明确没有偏好、从面板选中菜系，或用文字描述（兼容：先解释、再由用户选中确认）。"""

    state: TasteAnswerState = "unanswered"
    text: str | None = Field(default=None, max_length=300)
    selected: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> TasteAnswer:
        _panel_shape(self.state, self.text, self.selected)
        return self


class EducationAnswer(ProductModel):
    """最高学历（单选）：未答、暂不确定，或选了输入目录里的一个选项。只有选了才授予学历门槛。"""

    state: QualificationAnswerState = "unanswered"
    option_id: str | None = Field(default=None, max_length=48)

    @model_validator(mode="after")
    def _shape(self) -> EducationAnswer:
        if (self.state == "provided") != (self.option_id is not None):
            raise ValueError("只有已回答写 option_id，已回答必须写")
        return self


class CertificatesAnswer(ProductModel):
    """职业证书（多选）：``provided`` 加选中的资格编号；**明确提交空列表表示“没有”**，与未答分开保存。"""

    state: QualificationAnswerState = "unanswered"
    selected: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> CertificatesAnswer:
        if self.selected and self.state != "provided":
            raise ValueError("只有已回答才带选中的证书")
        return self


class ExperienceEntry(ProductModel):
    """一段相关工作经历：职业概念（输入目录里经验问题列出的相关职业）与整年数。"""

    concept_id: Annotated[str, Field(pattern=_CONCEPT)]
    years: Annotated[int, Field(ge=0, le=60)]


class ExperienceAnswer(ProductModel):
    """相关工作经验（只在选中的职业需要时问）：``provided`` 加经历；明确提交空列表表示“没有相关经验”，与未答分开保存。"""

    state: QualificationAnswerState = "unanswered"
    entries: tuple[ExperienceEntry, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> ExperienceAnswer:
        if self.entries and self.state != "provided":
            raise ValueError("只有已回答才带经历")
        return self


class AttributePreferenceAnswer(ProductModel):
    """一条属性偏好（品牌、菜系、上门顾虑……）：原文与它挂在哪项需求下；``as_hard_requirement`` 表示用户想把它当不可妥协的要求。"""

    preference_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")]
    need: str = Field(min_length=1, max_length=48)
    kind: str = Field(min_length=1, max_length=48)
    raw_text: str = Field(min_length=1, max_length=300)
    as_hard_requirement: bool = False


class AbilityClaim(ProductModel):
    """用户自己确认的一项资格事实（只在相关岗位需要时追问；不从职称推断）。"""

    ability_id: str = Field(min_length=1, max_length=48)
    state: AnswerState


class HardRequirementAnswer(ProductModel):
    """一条硬要求：选了受支持的选项（带参数），或只写了原文（本版未支持，只留存）。"""

    requirement_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")]
    option_id: str | None = Field(default=None, max_length=64)
    parameter_value: int | None = None
    raw_text: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _something(self) -> HardRequirementAnswer:
        if self.option_id is None and not self.raw_text:
            raise ValueError("硬要求要么选一个受支持的选项，要么写原文")
        return self


class EvidenceRef(ProductModel):
    """答案的来源证据：本轮只有表单字段；未来访谈的记录引用在这里接入（访谈 → 候选解释 → 确认 → 同一编译器）。"""

    kind: Literal["form_field", "interview_turn_reserved"]
    ref: str = Field(min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=300)


class UserInputDraft(ProductModel):
    """一份原始答案草稿（逐项保存状态）。原始答案、候选解释与确认画像分层保存：这里只有原始答案。"""

    meta: ProductMeta
    draft_id: RefStr
    revision: Annotated[int, Field(ge=1)]
    input_channel: Literal["form"] = "form"
    cities: CitiesAnswer
    occupation: OccupationAnswer
    age: AgeAnswer
    cash: CashAnswer
    six_dimensions: AllocationAnswer
    material: AllocationAnswer
    health: HealthAnswers
    free_text: FreeTextAnswer
    attribute_preferences: tuple[AttributePreferenceAnswer, ...] = ()
    abilities: tuple[AbilityClaim, ...] = ()
    hard_requirements: tuple[HardRequirementAnswer, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    leisure_interest: LeisureInterestAnswer = Field(default_factory=LeisureInterestAnswer)
    taste: TasteAnswer = Field(default_factory=TasteAnswer)
    #: v3.4：最高学历、职业证书与相关经验（结构化答案；未答不授予任何资格）
    education: EducationAnswer = Field(default_factory=EducationAnswer)
    certificates: CertificatesAnswer = Field(default_factory=CertificatesAnswer)
    experience: ExperienceAnswer = Field(default_factory=ExperienceAnswer)

    @model_validator(mode="after")
    def _version_shape(self) -> UserInputDraft:
        if bad := _version_problems(self, _DRAFT_SINCE):
            raise ValueError(
                f"{self.meta.schema_version} 的草稿没有后来版本新增的内容：{bad}（旧版本的草稿不迁移，新开一份）"
            )
        return self

    @model_serializer(mode="wrap")
    def _versioned(
        self, handler: SerializerFunctionWrapHandler
    ):  # 不写返回类型：JSON Schema 照原结构
        return _versioned_dump(self, handler(self), _DRAFT_SINCE)


#: 草稿在 v3.4、v3.5 新增的内容
_DRAFT_SINCE = (
    _Since(_V34, "education", EducationAnswer()),
    _Since(_V34, "certificates", CertificatesAnswer()),
    _Since(_V34, "experience", ExperienceAnswer()),
    _Since(_V34, "occupation.mode", "text"),
    _Since(_V34, "occupation.catalog_version"),
    _Since(_V34, "occupation.catalog_fingerprint"),
    _Since(_V34, "leisure_interest.state", states=("selected",)),
    _Since(_V34, "taste.state", states=("selected",)),
    _Since(_V35, "six_dimensions.mode", states=(RATINGS_RULE,)),
    _Since(_V35, "material.mode", states=(RATINGS_RULE,)),
)


# ==========================================================================
# ProfileReview：可读解释、候选、缺失、未支持与待确认
# ==========================================================================


class DraftRef(ProductModel):
    """引用一份草稿：编号、修订号与内容指纹（草稿改了，指纹就不同）。"""

    draft_id: RefStr
    revision: Annotated[int, Field(ge=1)]
    draft_fingerprint: Sha256


class OccupationCandidate(ProductModel):
    """一个可读的职业候选：概念 ID（不透明）、名称、职责说明与匹配到的词。不展示分类代码。"""

    candidate_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    duties: tuple[str, ...] = Field(min_length=1)
    matched_terms: tuple[str, ...] = ()


class OccupationReview(ProductModel):
    """``catalog_selected``：从输入目录直接选中（候选就是那一个职业，名称与职责供核对）；其余是兼容的文字匹配。"""

    status: Literal["explicit", "ambiguous", "unknown", "not_provided", "catalog_selected"]
    mode: Literal["text", "catalog"] = "text"
    raw_text: str | None = None
    candidates: tuple[OccupationCandidate, ...] = ()
    selected: str | None = None
    message_zh: str = Field(min_length=1, max_length=400)


class AllocationLine(ProductModel):
    """一个维度或细项：状态、精确值与显示值（显示只供阅读）、支持状态与用途。"""

    key: str
    label: str
    state: AnswerState
    exact: ExactText | None = None
    display: str | None = None
    support: SupportState
    usage: tuple[Usage, ...]
    #: v3.5 独立评分：用户给的原始评分（精确值与显示）；``exact`` / ``display`` 是换算后的权重（全部回答完、合计大于 0 才有）
    rating_exact: ExactText | None = None
    rating_display: str | None = None


class AllocationReview(ProductModel):
    """一组定和分配的审阅。独立评分时 ``rating_total`` 是原始评分的合计（回答了的那些），``total`` 是换算后权重的合计（精确 100），
    ``rule`` 写明换算规则。"""

    mode: AllocationMode | None = None
    status: Literal["complete", "incomplete", "not_applicable"]
    total: ExactNumber | None = None
    lines: tuple[AllocationLine, ...] = ()
    rating_total: ExactNumber | None = None
    rule: Literal["ratings_normalized_v1"] | None = None


class HealthReviewItem(ProductModel):
    question_id: str
    text: str
    answer_state: Literal["provided", "unsure", "unanswered"]
    option: Literal["agree", "disagree", "no_clear_tendency"] | None = None
    option_label: str | None = None
    meaning: str


class MoneyReview(ProductModel):
    status: Literal["convertible", "blocked", "missing"]
    amount_text: str | None = None
    currency: str | None = None
    price_period: str | None = None
    meaning: str
    conversion_rule: str | None = None
    converted: ProductMoney | None = None


class ItemStatus(ProductModel):
    """一项输入的四类状态汇总：答案、支持、确认与用途（必要时写执行是否保障）。"""

    item: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    answer_state: AnswerState
    support: SupportState
    confirmation: ConfirmationState
    usage: tuple[Usage, ...]
    execution_guaranteed: bool | None = None
    note: str = Field(min_length=1, max_length=300)


class StatementReview(ProductModel):
    """一条属性偏好或硬要求的审阅结果：原文、支持状态、用途与说明（未支持的不挪进已有权重）。"""

    ref: str
    kind: str
    raw_text: str
    support: SupportState
    usage: tuple[Usage, ...]
    as_hard_requirement: bool
    execution_guaranteed: bool
    message_zh: str


class InterestReview(ProductModel):
    """一段兴趣或口味的审阅：作答状态、原文、按词表得到的候选、有歧义与没认出的片段、用户已选中的类别与可读说明。

    ``status``：``confirmable``（有选中的类别，或选择了不需要类别的状态）、``pending_selection``（只有文字、还没选中类别——不能
    确认，免得空白或没核对的解释悄悄进入评分）、``invalid``（选中了词表外的类别）。"""

    state: str
    text: str | None = None
    candidates: tuple[str, ...] = ()
    ambiguous: tuple[str, ...] = ()
    unrecognized: tuple[str, ...] = ()
    selected: tuple[str, ...] = ()
    status: Literal["confirmable", "pending_selection", "invalid"]
    message_zh: str = Field(min_length=1, max_length=400)


class QualificationGrant(ProductModel):
    """一项按答案得到的资格与理由（例如“最高学历：本科 → 满足‘学士学位’及以下的学历门槛”）。"""

    ability_id: str = Field(min_length=1, max_length=48)
    label: str = Field(min_length=1, max_length=80)
    because: str = Field(min_length=1, max_length=300)


class QualificationRequirement(ProductModel):
    """选中职业的开局岗位要求的一项资格，以及按现在的答案是否满足（不满足时各城的开局会照实挡下，不自动补）。"""

    ability_id: str = Field(min_length=1, max_length=48)
    label: str = Field(min_length=1, max_length=80)
    met: bool


class QualificationReview(ProductModel):
    """学历、证书与相关经验的审阅：E3 按资料包已有的规则换成资格（学历“至少达到”、证书只授予选中的那张、经验按相关职业与年数，
    规则 ``confirmed_experience_mapping_v1``）。职业、职称、自由文字与搜索词都不授予资格。``source``：结构化答案、兼容的逐项资格
    确认，或都没有。"""

    source: Literal["structured_answers", "ability_claims", "none"]
    education_state: QualificationAnswerState
    education_option: str | None = None
    certificates_state: QualificationAnswerState
    certificates: tuple[str, ...] = ()
    experience_state: QualificationAnswerState
    experience: tuple[ExperienceEntry, ...] = ()
    ask_experience: tuple[str, ...] = ()
    granted: tuple[QualificationGrant, ...] = ()
    requirements: tuple[QualificationRequirement, ...] = ()
    message_zh: str = Field(min_length=1, max_length=400)


class ProfileReview(ProductModel):
    """一份草稿的审阅：E3 给出的可读解释与全部问题。``confirmable`` 为假时，``issues`` 里有阻断项。"""

    meta: ProductMeta
    review_id: RefStr
    draft: DraftRef
    confirmable: bool
    cities: tuple[str, ...] = ()
    occupation: OccupationReview
    age: int | None = None
    money: MoneyReview
    six_dimensions: AllocationReview
    material: AllocationReview
    health: tuple[HealthReviewItem, ...]
    free_text_chars: Annotated[int, Field(ge=0)] = 0
    statements: tuple[StatementReview, ...] = ()
    abilities: tuple[str, ...] = ()
    items: tuple[ItemStatus, ...]
    pending_confirmations: tuple[str, ...] = ()
    issues: tuple[ProductIssue, ...] = ()
    summary_zh: tuple[str, ...] = ()
    leisure_interest: InterestReview
    taste: InterestReview
    #: v3.4：学历、证书与相关经验的审阅（旧审阅没有）
    qualifications: QualificationReview | None = None

    @model_validator(mode="after")
    def _version_shape(self) -> ProfileReview:
        if bad := _version_problems(self, _REVIEW_SINCE):
            raise ValueError(f"{self.meta.schema_version} 的审阅没有后来版本新增的内容：{bad}")
        return self

    @model_serializer(mode="wrap")
    def _versioned(
        self, handler: SerializerFunctionWrapHandler
    ):  # 不写返回类型：JSON Schema 照原结构
        return _versioned_dump(self, handler(self), _REVIEW_SINCE)


#: 审阅在 v3.4、v3.5 新增的内容
_REVIEW_SINCE = (
    _Since(_V34, "qualifications"),
    _Since(_V34, "occupation.mode", "text"),
    _Since(_V34, "occupation.status", states=("catalog_selected",)),
    _Since(_V34, "leisure_interest.state", states=("selected",)),
    _Since(_V34, "taste.state", states=("selected",)),
    *(
        rule
        for group in ("six_dimensions", "material")
        for rule in (
            _Since(_V35, f"{group}.rating_total"),
            _Since(_V35, f"{group}.rule"),
            _Since(_V35, f"{group}.lines.*.rating_exact"),
            _Since(_V35, f"{group}.lines.*.rating_display"),
            _Since(_V35, f"{group}.mode", states=(RATINGS_RULE,)),
        )
    ),
)


# ==========================================================================
# ConfirmedProfile：已确认语义、精确数值、来源与内容身份
# ==========================================================================


class OccupationMapping(ProductModel):
    """职业概念在某国分类体系里的映射（内部保留，**不展示给用户**，也不是用户的永久身份）。"""

    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    scheme: str = Field(min_length=1, max_length=40)
    version: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=1, max_length=40)
    basis: str = Field(min_length=1, max_length=300)


class ConfirmedOccupation(ProductModel):
    """确认的职业。``catalog_selected``：从输入目录直接选中，没有原文，记下目录的版本与内容身份；``explicit`` / ``ambiguous``：
    兼容的文字匹配，保留原文。"""

    concept_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=80)
    duties: tuple[str, ...] = Field(min_length=1)
    raw_text: str | None = Field(default=None, min_length=1, max_length=300)
    language: str = Field(min_length=2, max_length=10)
    match_status: Literal["explicit", "ambiguous", "catalog_selected"]
    internal_mappings: tuple[OccupationMapping, ...] = Field(min_length=1)
    catalog_version: str | None = Field(default=None, max_length=80)
    catalog_fingerprint: Sha256 | None = None

    @model_validator(mode="after")
    def _source(self) -> ConfirmedOccupation:
        catalog = self.match_status == "catalog_selected"
        if catalog != (self.raw_text is None) or catalog != (self.catalog_fingerprint is not None):
            raise ValueError(
                "目录选中的职业没有原文、带目录身份；文字匹配的职业有原文、不带目录身份"
            )
        return self


class ConfirmedMoney(ProductModel):
    """确认的开局现金：保留原始金额、币种、时期、口径与换算依据，以及换算后的整数最小单位。"""

    amount_text: str = Field(min_length=1, max_length=40)
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    price_period: str = Field(min_length=1, max_length=20)
    meaning: Literal["liquid_funds_v1"]
    conversion_rule: str = Field(min_length=1, max_length=80)
    converted: ProductMoney


class ConfirmedAllocation(ProductModel):
    """确认的定和分配：规范精确分数（参与计算）与显示文字（只供阅读）。独立评分（v3.5）另外保存原始评分、原始合计与换算规则；
    E1 编译只读 ``points``（换算后的最终权重）。"""

    mode: AllocationMode
    points: dict[str, ExactText]
    display: dict[str, str]
    ratings: dict[str, ExactText] | None = None
    rating_total: ExactText | None = None
    rule: Literal["ratings_normalized_v1"] | None = None

    @model_validator(mode="after")
    def _ratings_source(self) -> ConfirmedAllocation:
        rated = self.mode == RATINGS_RULE
        if {rated} != {
            self.ratings is not None,
            self.rating_total is not None,
            self.rule is not None,
        }:
            raise ValueError("只有独立评分才带原始评分、原始合计与换算规则，而且三者都要有")
        return self


class ConfirmedMaterial(ProductModel):
    status: Literal["allocated", "not_applicable_parent_zero"]
    allocation: ConfirmedAllocation | None = None

    @model_validator(mode="after")
    def _shape(self) -> ConfirmedMaterial:
        if (self.status == "allocated") != (self.allocation is not None):
            raise ValueError("只有 allocated 才有细分；父项为 0 时不伪造细分")
        return self


class ConfirmedHealthAnswer(ProductModel):
    question_id: str = Field(min_length=1, max_length=48)
    question_text: str = Field(min_length=1, max_length=300)
    answer_state: Literal["provided", "unsure", "unanswered"]
    option: Literal["agree", "disagree", "no_clear_tendency"] | None = None
    option_label: str | None = None
    meaning: str = Field(min_length=1, max_length=300)


class ConfirmedHealth(ProductModel):
    questionnaire_version: str = Field(min_length=1, max_length=80)
    status: Literal["draft_wording_not_validated"]
    answers: tuple[ConfirmedHealthAnswer, ...] = Field(min_length=1)


class ConfirmedHardRequirement(ProductModel):
    """一条受支持、已确认的硬要求：编译成 E1 的结构化条件；**只单独报告，环境不拦截计划**。"""

    requirement_id: str = Field(min_length=1, max_length=64)
    option_id: str = Field(min_length=1, max_length=64)
    parameter_value: int
    label_zh: str = Field(min_length=1, max_length=200)
    enforced_by_execution: Literal[False] = False


class RetainedStatement(ProductModel):
    """只留存的陈述：未支持的属性偏好、未获执行保障的“不可妥协”要求等。保留原文与支持状态，不挪进已有权重。"""

    ref: str = Field(min_length=1, max_length=64)
    kind: str = Field(min_length=1, max_length=48)
    need: str | None = Field(default=None, max_length=48)
    raw_text: str = Field(min_length=1, max_length=300)
    support: Literal["unsupported", "partially_supported"]
    usage: tuple[Usage, ...]
    as_hard_requirement: bool
    message_zh: str = Field(min_length=1, max_length=400)


class ConfirmedInterests(ProductModel):
    """确认后的兴趣与口味（DECISIONS/0041）。只有 ``confirmed`` 带类别；其余状态分开保存、都按中性规则评分。

    类别只进入共同、有限的匹配规则（系数是公开登记的模型假设，不是对个人效用的测量）；不改变六维点数、现金、资格或硬要求。
    用户写的原文不管选了哪种作答状态都原样保留（``leisure_text`` / ``taste_text``）；没认出的片段另列在 ``unrecognized_text``
    （本版未支持），不交给评分。``scope_zh`` 逐项说明这次按什么个性化、哪些没有个性化（未答、暂不确定、没有固定爱好、通用评价
    分开写，不宣称已经个性化）。"""

    leisure_status: Literal["unanswered", "unsure", "no_fixed_hobby", "generic", "confirmed"]
    leisure_categories: tuple[str, ...] = ()
    taste_status: Literal["unanswered", "unsure", "no_preference", "confirmed"]
    cuisines: tuple[str, ...] = ()
    leisure_text: str | None = Field(default=None, max_length=300)
    taste_text: str | None = Field(default=None, max_length=300)
    unrecognized_text: tuple[str, ...] = ()
    scope_zh: tuple[str, ...] = Field(min_length=2)
    matching_note_zh: str = Field(min_length=1, max_length=400)

    @property
    def personalized(self) -> bool:
        """休闲兴趣与口味都按用户确认的类别个性化了吗。"""
        return self.leisure_status == "confirmed" and self.taste_status == "confirmed"

    @model_validator(mode="after")
    def _shape(self) -> ConfirmedInterests:
        if (self.leisure_status == "confirmed") != bool(self.leisure_categories):
            raise ValueError("只有确认的休闲兴趣才带类别")
        if (self.taste_status == "confirmed") != bool(self.cuisines):
            raise ValueError("只有确认的口味才带菜系")
        for name in ("leisure_categories", "cuisines"):
            seq = getattr(self, name)
            if len(set(seq)) != len(seq):
                raise ValueError(f"{name} 重复：审阅阶段就该拒绝")
        return self


class ConfirmedQualifications(ProductModel):
    """确认的学历、证书与相关经验答案（v3.4）。``ConfirmedProfile.abilities`` 由它按 ``rule`` 换算；``source = ability_claims``
    表示用的是兼容的逐项资格确认，``none`` 表示什么都没答（不授予任何资格）。"""

    source: Literal["structured_answers", "ability_claims", "none"]
    education_state: QualificationAnswerState
    education_option: str | None = None
    certificates_state: QualificationAnswerState
    certificates: tuple[str, ...] = ()
    experience_state: QualificationAnswerState
    experience: tuple[ExperienceEntry, ...] = ()
    rule: Literal["confirmed_experience_mapping_v1"] = "confirmed_experience_mapping_v1"


class FieldSource(ProductModel):
    """一项确认值的来源（表单字段，或未来的访谈证据引用）。"""

    field: str = Field(min_length=1, max_length=80)
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)


class ConfirmedProfile(ProductModel):
    """用户确认的画像。``content_fingerprint`` 覆盖除它本身以外的全部内容（含版本与草稿引用）：改了相关答案、词表、问卷或转换
    规则，旧确认就对不上；内容被改写，指纹核对不通过。指纹只核对内容，不代替线上认证与授权。"""

    meta: ProductMeta
    confirmed_id: RefStr
    person_ref: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.\-]{0,63}$")]
    draft: DraftRef
    review_id: RefStr
    confirmation_source: Literal["user_form"]
    cities: tuple[str, ...] = Field(min_length=1)
    occupation: ConfirmedOccupation
    age_years: int
    money: ConfirmedMoney
    six_dimensions: ConfirmedAllocation
    material: ConfirmedMaterial
    health: ConfirmedHealth
    free_text: str | None = Field(default=None, max_length=FREE_TEXT_MAX_CHARS)
    abilities: tuple[str, ...] = ()
    hard_requirements: tuple[ConfirmedHardRequirement, ...] = ()
    retained: tuple[RetainedStatement, ...] = ()
    sources: tuple[FieldSource, ...] = ()
    interests: ConfirmedInterests
    #: v3.4：学历、证书与相关经验的答案（旧画像没有）
    qualifications: ConfirmedQualifications | None = None
    content_fingerprint: Sha256

    @model_validator(mode="after")
    def _version_shape(self) -> ConfirmedProfile:
        if bad := _version_problems(self, _PROFILE_SINCE):
            raise ValueError(f"{self.meta.schema_version} 的确认画像没有后来版本新增的内容：{bad}")
        return self

    @model_serializer(mode="wrap")
    def _versioned(
        self, handler: SerializerFunctionWrapHandler
    ):  # 不写返回类型：JSON Schema 照原结构
        return _versioned_dump(self, handler(self), _PROFILE_SINCE)

    def content(self) -> dict[str, Any]:
        """参与内容身份的全部字段（指纹本身与样例出处标签除外）。"""
        return identity_content(self, "content_fingerprint")

    def expected_fingerprint(self) -> str:
        return content_fingerprint(self.content())


#: 确认画像在 v3.4、v3.5 新增的内容（目录选中的职业只在 v3.4 起有；独立评分只在 v3.5 起有）
_PROFILE_SINCE = (
    _Since(_V34, "qualifications"),
    _Since(_V34, "occupation.catalog_version"),
    _Since(_V34, "occupation.catalog_fingerprint"),
    *(
        rule
        for path in ("six_dimensions", "material.allocation")
        for rule in (
            _Since(_V35, f"{path}.ratings"),
            _Since(_V35, f"{path}.rating_total"),
            _Since(_V35, f"{path}.rule"),
            _Since(_V35, f"{path}.mode", states=(RATINGS_RULE,)),
        )
    ),
)


class ConfirmedRef(ProductModel):
    confirmed_id: RefStr
    content_fingerprint: Sha256


# ==========================================================================
# ProductPreflight：开局摘要、三种能力、资料缺口、范围与运行绑定
# ==========================================================================


class CapabilityStatus(ProductModel):
    """一种能力在当前输入与声明范围内的状态。**不保证**未来每份计划都合法、每个月都能完整评分。"""

    status: Literal["yes", "no", "partial", "unknown"]
    reasons: tuple[str, ...] = ()


class StartSummary(ProductModel):
    """标准开局摘要：同一用户跨城、跨重复序号相同的个人条件。"""

    rule: str = Field(min_length=1, max_length=80)
    age_years: int
    cash: ProductMoney
    abilities: tuple[str, ...] = ()
    occupation_label: str
    monthly_time_note: str
    monthly_energy_note: str
    scenario_zh: tuple[str, ...] = Field(min_length=1)


class CityCoverageSummary(ProductModel):
    """一座城的偏好覆盖摘要（E1 覆盖报告的投影）。不是阈值，也不解锁不完整的总分。

    * ``common_weight``：整个比较组**共同**覆盖的权重（每座城资料都齐、规则都有的叶子之和；各城相同，城市比较只在这部分上是同口径的）；
    * ``unknown_weight``：**这座城**资料未知的正权重之和（这些月份的总分会不完整，不补 0、不删掉再重新归一化）；
    * ``no_component_weight``：本版评分没有分项的正权重之和（各城相同）；
    * ``unsupported_positive_leaves``：**这座城**资料未知或没有评分分项的正权重叶子。
    """

    common_weight: ExactNumber
    unknown_weight: ExactNumber
    no_component_weight: ExactNumber
    unsupported_positive_leaves: tuple[str, ...] = ()
    label: str


#: 一条资料的来源性质（与 E1 的 ``SourceRecord.kind`` 同一套词）：观测、可审计派生、统计估计、合成设定。
SourceKindName = Literal["observed", "derived", "estimated", "synthetic"]
#: 通勤方式（与 E1 的 ``CommuteMode`` 同一套词）。
CommuteModeKey = Literal["walk_bike", "transit", "drive"]


class SourceKindCount(ProductModel):
    kind: SourceKindName
    count: Annotated[int, Field(ge=0)]


class CategorySources(ProductModel):
    """目录里一类机会（住房、岗位、食品、服务……）的条数，按来源性质分开数（四种性质都列，没有的写 0）。"""

    category: str = Field(min_length=1, max_length=48)
    total: Annotated[int, Field(ge=0)]
    by_kind: tuple[SourceKindCount, ...] = Field(min_length=4, max_length=4)


class NamedAssumption(ProductModel):
    """一条登记的模型设定（来源性质是合成）：设定说明的原文、用到它的机会条数与几个例子。不是资料，也不是某个商品或场所的报价。"""

    note_zh: str = Field(min_length=1, max_length=400)
    count: Annotated[int, Field(ge=1)]
    examples: tuple[str, ...] = Field(min_length=1, max_length=3)


class CommonPrior(ProductModel):
    """一项比较组各城共用的背景设定（没有可比资料时登记的共同先验，例如休闲；安全按各都会区的犯罪资料换算，不在这里）：事实的
    名称与含义。不制造城市差异；
    这里不写它换算成评分输入的规则与参数。"""

    fact_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=80)
    meaning_zh: str = Field(min_length=1, max_length=400)


class CommuteModeAvailability(ProductModel):
    """这座城的通勤关系表里有没有一种方式：有几条片区之间的线、使用条件与封顶的说明（与给分身看的同一份公开说明）。

    ``in_relation = False``：关系表里没有这种方式（资料缺失或本城不建模），到岗时不能用它——不当 0 分钟，也不当免费。"""

    mode: CommuteModeKey
    mode_zh: str = Field(min_length=1, max_length=20)
    in_relation: bool
    lines: Annotated[int, Field(ge=0)]
    condition_zh: str | None = Field(default=None, max_length=400)


class MenuSummary(ProductModel):
    """一类机会的年度菜单（DECISIONS/0042）：每年、每个环境重复给分身看的一份抽样。菜单是接触机会，不是成交或录用。"""

    category: str = Field(min_length=1, max_length=48)
    years: Annotated[int, Field(ge=1)]
    env_repeats: Annotated[int, Field(ge=1)]
    items_min: Annotated[int, Field(ge=1)]
    items_max: Annotated[int, Field(ge=1)]


class TaxScope(ProductModel):
    """这座城适用的税（DECISIONS/0042）：官方 2024 年规则（单身、只有工资收入、标准扣除）或合成测试替身；支持的居住与工作地、
    规则名称、官方说明没有的税，以及没有建模的部分（照实写，不当作已经覆盖）。"""

    policy_id: str = Field(min_length=1, max_length=80)
    status: Literal["official_2024_rules", "synthetic_test_double"]
    status_zh: str = Field(min_length=1, max_length=120)
    residence_scope_zh: str | None = Field(default=None, max_length=400)
    rules_zh: tuple[str, ...] = ()
    absent_zh: tuple[str, ...] = ()
    not_modeled_zh: tuple[str, ...] = ()


class BuildCount(ProductModel):
    """资料包构建报告里的一个计数（片区、通勤线、住房候选与排除的原因、放进目录的岗位、菜单……）。"""

    key: str = Field(min_length=1, max_length=64)
    label_zh: str = Field(min_length=1, max_length=80)
    count: Annotated[int, Field(ge=0)]


class CityDataNature(ProductModel):
    """一座城的资料是怎么来的（DECISIONS/0042）：目录各类机会按来源性质的条数、登记的模型设定、共同先验、治疗的共同假设、
    模型活动设定、片区、通勤方式、菜单、税的范围，以及资料包构建报告的计数。

    只有公开的来源性质与说明：没有评分参数（η、τ、c、背景量）、抽签或内部状态。``build_status = not_available`` 表示构建报告
    不在这份资料包里或与目录对不上——计数未知，不是 0。"""

    city_ref: str
    label: str
    pack_status: PackStatus
    summary_zh: str = Field(min_length=1, max_length=400)
    opportunities: tuple[CategorySources, ...] = ()
    totals: tuple[SourceKindCount, ...] = Field(min_length=4, max_length=4)
    named_assumptions: tuple[NamedAssumption, ...] = ()
    common_priors: tuple[CommonPrior, ...] = ()
    treatment_assumptions_zh: tuple[str, ...] = ()
    model_plan_activities: tuple[str, ...] = ()
    home_areas: Annotated[int, Field(ge=0)] = 0
    work_areas: Annotated[int, Field(ge=0)] = 0
    commute_modes: tuple[CommuteModeAvailability, ...] = ()
    menus: tuple[MenuSummary, ...] = ()
    tax: TaxScope | None = None
    build_status: Literal["from_pack_report", "not_available"]
    build: tuple[BuildCount, ...] = ()

    @model_validator(mode="after")
    def _build(self) -> CityDataNature:
        if (self.build_status == "from_pack_report") != bool(self.build):
            raise ValueError("只有读到构建报告时才写构建计数；读不到时计数未知，不写 0")
        return self


class ScopeNote(ProductModel):
    """预检查的一条范围说明（DECISIONS/0042）：这版支持什么、不支持什么、为什么。``cities`` 为空表示对全部候选城市都适用。
    E5 原样展示中文，不另写解释。"""

    topic: Literal["occupations", "tax", "commute", "menus", "unsupported", "data"]
    title_zh: str = Field(min_length=1, max_length=80)
    text_zh: str = Field(min_length=1, max_length=600)
    cities: tuple[str, ...] = ()


class CityPreflight(ProductModel):
    city_ref: RefStr
    label: str
    start_job_label: str | None = None
    executable: CapabilityStatus
    scorable: CapabilityStatus
    feedback: CapabilityStatus
    coverage: CityCoverageSummary | None = None
    option_inventory_status: Literal["complete_for_declared_scope", "partial", "not_assessed"]
    data_gaps: tuple[ProductIssue, ...] = ()
    #: 这座城的资料是怎么来的（DECISIONS/0042）。预检查没有走到 E1 加载时为空，不序列化。
    data_nature: CityDataNature | None = Field(default=None, exclude_if=lambda v: v is None)


class RunBinding(ProductModel):
    """预检查绑定的运行身份：编译出的 E1 输入、协议、评分、评价、提示模板、规划器与运行清单的内容身份。变了就要重新预检查。"""

    protocol_id: str
    scoring_version: str
    scoring_content_fingerprint: Sha256
    evaluation_spec_id: str
    prompt_template: str
    planner_kind: Literal["mock", "llm"]
    planner_id: str
    compiled_inputs: dict[str, Sha256]
    identities_fingerprint: Sha256


class PlannerAndCost(ProductModel):
    planner_kind: Literal["mock", "llm"]
    planner_id: str
    description_zh: str
    call_limit: int
    paid: bool
    max_cost: str | None = None


class ProductPreflight(ProductModel):
    """一次运行前检查。``preflight_fingerprint`` 覆盖除它本身以外的全部内容；确认画像或运行身份变了，旧预检查失效。"""

    meta: ProductMeta
    preflight_id: RefStr
    confirmed: ConfirmedRef
    status: Literal["ready", "blocked"]
    start: StartSummary | None = None
    cities: tuple[CityPreflight, ...] = ()
    executable: CapabilityStatus
    scorable: CapabilityStatus
    feedback: CapabilityStatus
    clones_per_city: Annotated[int, Field(ge=0)]
    years: Annotated[int, Field(ge=1)]
    planner: PlannerAndCost
    binding: RunBinding | None = None
    issues: tuple[ProductIssue, ...] = ()
    scope_zh: tuple[str, ...] = ()
    #: 结构化的范围说明（DECISIONS/0042）：职业、税、通勤、菜单与本版不支持的内容。真实资料包才有；没有时不序列化。
    scope_notes: tuple[ScopeNote, ...] = Field(default=(), exclude_if=lambda v: v == ())
    preflight_fingerprint: Sha256

    def content(self) -> dict[str, Any]:
        """参与内容身份的全部字段（指纹本身与样例出处标签除外）。"""
        return identity_content(self, "preflight_fingerprint")

    def expected_fingerprint(self) -> str:
        return content_fingerprint(self.content())


class PreflightRef(ProductModel):
    preflight_id: RefStr
    preflight_fingerprint: Sha256


# ==========================================================================
# 提交、回执与进度（线上服务留 E3-C：本轮只有契约与标明为示例的响应）
# ==========================================================================


class RunSubmission(ProductModel):
    """提交一次运行：引用确认画像与预检查，带幂等键。相同确认的重复点击只生成同一运行；输入或配置变了必须重查。"""

    meta: ProductMeta
    confirmed: ConfirmedRef
    preflight: PreflightRef
    idempotency_key: Annotated[str, Field(min_length=8, max_length=80)]


class RunReceipt(ProductModel):
    """提交回执：接受、重复提交（返回原运行 ID，不再生成新的一批分身）或拒绝（附原因）。"""

    meta: ProductMeta
    status: Literal["accepted", "duplicate_same_run", "rejected"]
    run_id: str | None = None
    idempotency_key: str
    reasons: tuple[ProductIssue, ...] = ()
    #: 谁登记的：本地离线桥接、托管服务（E3C，服务端 SQLite 是提交与次数的权威记录），或标明为示例的契约响应。
    service: Literal["offline_bridge", "hosted_service", "contract_example_only"]

    @model_validator(mode="after")
    def _run_id(self) -> RunReceipt:
        if (self.status == "rejected") == (self.run_id is not None):
            raise ValueError("接受与重复提交必须写运行 ID；拒绝不写")
        return self


class CloneProgress(ProductModel):
    clone_ref: str
    city_ref: str
    status: str
    status_zh: str
    years_committed: Annotated[int, Field(ge=0)]


_MONEY_TEXT = r"^(0|[1-9][0-9]*)(\.[0-9]{1,6})?$"
MoneyText = Annotated[str, Field(pattern=_MONEY_TEXT)]


def _optional(default: Any = None) -> Any:
    """没有时不序列化的可选字段（离线 mock 与旧样例不多出空字段）。"""
    return Field(default=default, exclude_if=lambda v: v is None)


class CostStatus(ProductModel):
    """费用状态：离线 mock 为零；真实模型只能写“按报价估算”，以供应商账单为准。

    ``calls_used`` / ``call_limit`` 数的是**规划候选**（每个分身每年一次正常规划、最多两次修订）；``service_attempts`` 数的是实际发给
    服务的尝试（每份候选最多再试一次服务请求），两者不是同一个计数。金额都是应用侧按已记录报价估算（E3C）：``settled_cost`` 是返回了
    用量、已核算的；``uncertain_cost`` 是请求可能已经发出、用量不确定、按预留保留的（不改成 0）；``in_flight_cost`` 是已经预留、还没有
    结果的；``estimated_cost`` 是三者之和。``max_cost`` 是这次运行的应用侧上限，不是供应商的账单上限。"""

    kind: Literal["offline_mock_zero_cost", "estimated_from_quote"]
    calls_used: Annotated[int, Field(ge=0)]
    call_limit: Annotated[int, Field(ge=0)]
    estimated_cost: str | None = None
    note: str
    currency: Literal["CNY", "USD"] | None = _optional()
    price_version: str | None = _optional()
    max_cost: MoneyText | None = _optional()
    settled_cost: MoneyText | None = _optional()
    uncertain_cost: MoneyText | None = _optional()
    in_flight_cost: MoneyText | None = _optional()
    service_attempts: Annotated[int, Field(ge=0)] | None = _optional()
    service_attempt_limit: Annotated[int, Field(ge=0)] | None = _optional()
    uncertain_attempts: Annotated[int, Field(ge=0)] | None = _optional()


class RunProgress(ProductModel):
    """一次运行的进度（断线后用运行 ID 查询；不重发付费规划）。请求成功、计划可执行、生活完成、评分完整分别记录。"""

    meta: ProductMeta
    run_id: str
    stage: Literal[
        "queued", "running", "completed", "paused", "halted", "interrupted", "budget_exhausted"
    ]
    stage_zh: str
    clones: tuple[CloneProgress, ...] = ()
    #: 暂停 / 停下 / 中断时给用户看的说明（E3-A 收尾 F5：按进度的结构化状态生成，不透传内部原因原文）。
    pause: PublicExplanation | None = None
    cost: CostStatus
    resumable: bool
    resume_note: str
    report_available: bool
    #: 这份进度快照是什么时候写下的（E3C：worker 从权威进度与账本生成；UTC ISO 8601）。离线桥接不写。
    updated_at: str | None = _optional()
    #: 已结算的分身年度数（DECISIONS/0049）：E1 运行归档里已经执行并结算的年度行动条数（每个分身每年最多一条；出局或停下之后没有
    #: 经历的年份没有记录）；``clone_years_planned`` 是 E1 冻结的名单 × 年数。托管服务的快照才写，离线桥接与旧快照没有。
    clone_years_settled: Annotated[int, Field(ge=0)] | None = _optional()
    clone_years_planned: Annotated[int, Field(ge=1)] | None = _optional()


# ==========================================================================
# ProductResult：E1 报告的白名单投影
# ==========================================================================


class ConvenienceDevice(ProductModel):
    opportunity_id: str
    label: str
    months_in_service: tuple[int, ...]


class ConvenienceService(ProductModel):
    opportunity_id: str
    label: str
    months_planned: tuple[int, ...]
    months_covered: tuple[int, ...]
    months_not_obtained: tuple[int, ...]


class HouseholdHours(ProductModel):
    """本人每月实际做的家务小时（资源账的事实，精确值）。"""

    task: str
    monthly_hours: tuple[str, ...]


class GoodsLine(ProductModel):
    """一件不是省家务设备的耐用品（舒适用品、爱好装备）这一年的事实（DECISIONS/0041）：今年买到的月份、在用的月份、年末是否
    到期与之后还剩几个月。买到、在用与实际用它参加活动是三件事：装备放在家里不等于参加了活动（见 ``ActivityLine``）。"""

    opportunity_id: str
    label: str
    bought_month: int | None = None
    months_in_service: tuple[int, ...] = ()
    expired: bool
    months_left_after_year: int


class PurchaseNotMade(ProductModel):
    """计划里的一次性购买没有成交（DECISIONS/0041）：没有扣款，也没有生成持有物。文字是固定的公开说明，不透传内部原因。"""

    opportunity_id: str
    label: str
    month: int | None = None
    status: Literal["rejected", "cancelled_when_ill", "paused", "not_reached"]
    status_zh: str


class ActivityLine(ProductModel):
    """一项休闲活动这一年的实际参与（DECISIONS/0041）：参加的月份、次数与参与小时（不含往返与准备），生病暂停的月份，
    没能进行的月份（缺少需要的装备能力：没有收费）。次数与小时是精确写法（整数或“分子/分母”）。"""

    opportunity_id: str
    label: str
    months_done: tuple[int, ...] = ()
    sessions: str
    participation_hours: str
    months_paused: tuple[int, ...] = ()
    months_not_done: tuple[int, ...] = ()
    not_done_zh: str | None = None


class HomeFact(ProductModel):
    """这一年实际住过的一处住房（DECISIONS/0042）：住了哪几个月、在哪个片区，以及这个片区的居民平时上班往返多少分钟（资料或
    估计；没有就为空，不当 0）。片区是资料的地理单位（例如 PUMA），不是街区或楼栋，本身不加分。"""

    opportunity_id: str
    label: str
    months: tuple[int, ...] = Field(min_length=1)
    area_id: str | None = None
    area_label: str | None = None
    area_typical_roundtrip_minutes: Annotated[int, Field(ge=0)] | None = None


class CommuteTrip(ProductModel):
    """一个到岗岗位一个月实际执行的通勤（取自 E1 的月度通勤记录）：岗位、就业片区、方式、一次往返的分钟与这个月的到岗次数。"""

    job_opportunity_id: str
    job_label: str
    work_area_id: str
    work_area_label: str
    mode: CommuteModeKey
    mode_zh: str
    roundtrip_minutes: Annotated[int, Field(ge=0)]
    onsite_visits: Annotated[int, Field(ge=1)]


class CommuteMonths(ProductModel):
    """几个月相同的通勤执行事实（DECISIONS/0042）：E1 每个月唯一的通勤记录，内容完全相同的月份合成一行。

    ``monthly_paid`` 是这几个月里**每个月**付的通勤费（各方式按月封顶之后的合计）。没有需要到岗的工作、出局当月没有住房时
    不写行程与付款（后者是未知，不是 0）；出局或停止之后没有发生的月份不列。"""

    months: tuple[int, ...] = Field(min_length=1)
    status: Literal["computed", "no_onsite_work", "unknown_no_housing"]
    status_zh: str
    home_area_id: str | None = None
    home_area_label: str | None = None
    trips: tuple[CommuteTrip, ...] = ()
    monthly_paid: ProductMoney | None = None


class CarAccessFact(ProductModel):
    """用车权（DECISIONS/0042）：开车通勤要这个月有在用的用车权——一项按月付费的普通机会，不买就不能开车。只在这座城的通勤
    关系里有需要它的方式时出现。``bought`` = 今年至少有一个月用上了。"""

    status: Literal["bought", "not_bought"]
    lines: tuple[ConvenienceService, ...] = ()
    note_zh: str = Field(min_length=1, max_length=300)


class DeviceFailureFact(ProductModel):
    """一次设备故障与替换结果（DECISIONS/0042）：第几个月月末坏了（下个月起不能用，剩余寿命作废）、坏了几件，预先声明的替换
    有没有在下个月按普通购买买到。不含抽签的随机数、月度风险与键。"""

    opportunity_id: str
    label: str
    month: Annotated[int, Field(ge=1, le=12)]
    units_failed: Annotated[int, Field(ge=1)]
    replacement_id: str | None = None
    replacement_label: str | None = None
    replacement_status: Literal["not_declared", "bought", "rejected", "next_year", "not_reached"]
    replacement_status_zh: str
    replacement_month: Annotated[int, Field(ge=1, le=12)] | None = None


class TaxFact(ProductModel):
    """这一年的工资按哪一份税规则预扣（DECISIONS/0042）：官方 2024 年规则，或合成测试替身。范围与没有建模的部分见资料性质。"""

    policy_id: str
    status: Literal["official_2024_rules", "synthetic_test_double"]
    status_zh: str


class SupportLine(ProductModel):
    """一项日常支持功能这一年的事实（E3B 映射修正 §5）：计划买的月份、实际获得的月份（付了钱、服务在用）与没获得的月份，由哪项
    服务提供。同功能买两份不多算；没获得的月份这项功能就是没有（不会因此出局、生病或少拿工资，也不节省小时）。"""

    function: Literal["personal_communication", "home_connectivity", "household_supplies"]
    label_zh: str
    opportunity_ids: tuple[str, ...] = ()
    months_planned: tuple[int, ...] = ()
    months_obtained: tuple[int, ...] = ()
    months_not_obtained: tuple[int, ...] = ()


class ConsumptionLine(ProductModel):
    """个人消费篮子这一年的事实（E3B 映射修正 §6）：每月实际获得的标准篮子数（精确写法，“0” = 没有，“—” = 那个月没有生活），
    计划买却没获得的月份。它是当月的消费服务流，不是可以转卖的资产。"""

    opportunity_ids: tuple[str, ...]
    baskets_by_month: tuple[str, ...]
    months_not_obtained: tuple[int, ...] = ()


class YearFacts(ProductModel):
    """一个分身一年的生活事实（取自 E1 的结算记录与资源账；不含模型理由与内部评分对象）。"""

    year_index: int
    status: str
    months_lived: Annotated[int, Field(ge=0, le=12)]
    cash_start: ProductMoney
    cash_end: ProductMoney | None = None
    jobs: tuple[str, ...] = ()
    housing: tuple[str, ...] = ()
    food: tuple[str, ...] = ()
    #: 实际参加的课程与参加的月份（E1 记为执行了的月份；暂停、被拒或没付成学费的月份不算）。换岗的原因要靠它讲清楚。
    training: tuple[str, ...] = ()
    paid_total: ProductMoney
    devices: tuple[ConvenienceDevice, ...] = ()
    services: tuple[ConvenienceService, ...] = ()
    household: tuple[HouseholdHours, ...] = ()
    health_months: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    #: 生活扩展（DECISIONS/0041）：其他耐用品、没买成的购买、休闲活动。没有时不序列化。
    goods: tuple[GoodsLine, ...] = Field(default=(), exclude_if=lambda v: v == ())
    purchases_not_made: tuple[PurchaseNotMade, ...] = Field(
        default=(), exclude_if=lambda v: v == ()
    )
    activities: tuple[ActivityLine, ...] = Field(default=(), exclude_if=lambda v: v == ())
    #: 片区、通勤、用车权、设备故障与税（DECISIONS/0042）。没有时不序列化。
    homes: tuple[HomeFact, ...] = Field(default=(), exclude_if=lambda v: v == ())
    commute: tuple[CommuteMonths, ...] = Field(default=(), exclude_if=lambda v: v == ())
    car_access: CarAccessFact | None = Field(default=None, exclude_if=lambda v: v is None)
    device_failures: tuple[DeviceFailureFact, ...] = Field(default=(), exclude_if=lambda v: v == ())
    tax: TaxFact | None = Field(default=None, exclude_if=lambda v: v is None)
    #: 日常支持与个人消费篮子（E3B 映射修正）。没有时不序列化。
    support: tuple[SupportLine, ...] = Field(default=(), exclude_if=lambda v: v == ())
    consumption: ConsumptionLine | None = Field(default=None, exclude_if=lambda v: v is None)


class RequirementLine(ProductModel):
    need: str
    summary: str
    months_satisfied: int
    months_not_satisfied: int
    months_unknown: int
    months_not_lived: int


class ValuationScopeNote(ProductModel):
    """期末估值的范围说明（DECISIONS/0041 R8）：期末持有的一件装备，参考模板有没有考虑它的用途。公开的安全投影：只有物品、
    能力键、剩余月数与固定写法的说明，没有参数、方案比较或未来事件。“模板没有考虑”不是“装备没有价值”。"""

    kind: Literal["equipment_use_valued", "equipment_use_not_in_templates"]
    opportunity_id: str
    label: str
    capabilities: tuple[str, ...] = Field(min_length=1)
    months_left: Annotated[int, Field(ge=1)]
    text_zh: str = Field(min_length=1, max_length=300)


class CloneResult(ProductModel):
    """一个分身：C / E / M 分类与原因、L / V / J（精确值与显示值）、事件链与硬要求核验、生活事实。"""

    clone_ref: str
    city_ref: str
    repeat_index: int
    category: Literal["C", "E", "M"] | None = None
    category_zh: str
    missing_reason: str | None = None
    missing_reason_zh: str | None = None
    #: 这个分身为什么是这一类、影响什么、下一步（E3-A 收尾 F5：按类别与缺失原因生成，不透传 E1 内部说明或异常原文）。
    explanation: PublicExplanation
    l_actual: ExactNumber | None = None
    v_reference: ExactNumber | None = None
    j: ExactNumber | None = None
    range_lower: ExactNumber | None = None
    range_upper: ExactNumber | None = None
    reference_plan: str | None = None
    event_chain: tuple[str, ...] = ()
    hard_requirements: tuple[RequirementLine, ...] = ()
    years: tuple[YearFacts, ...] = ()
    #: 期末估值的范围说明（收尾 R8）：期末持有的装备，参考模板考虑了哪些用途、哪些没有考虑。没有时不序列化。
    valuation_scope: tuple[ValuationScopeNote, ...] = Field(
        default=(), exclude_if=lambda v: v == ()
    )


class MissingReasonLine(ProductModel):
    reason: str
    reason_zh: str
    count: int


class RequirementSummaryLine(ProductModel):
    """一条硬要求在一座城的核验人数。未核验不等于核验过、没有问题。"""

    need: str
    planned: int
    checked: int
    satisfied_every_month: int
    violated: int
    unknown: int
    satisfied_in_lived_months: int
    not_checked: int


class CityResult(ProductModel):
    """一座城的结果：原计划 N = C + E + M；完整者均值的分母是 C；模拟缺失范围的分母是 C + M（出局者不进）。空值保持为空。"""

    city_ref: str
    label: str
    planned: int
    complete: int
    exits: int
    missing: int
    mean_j: ExactNumber | None = None
    mean_l: ExactNumber | None = None
    mean_v: ExactNumber | None = None
    mean_denominator: int
    range_lower: ExactNumber | None = None
    range_upper: ExactNumber | None = None
    range_denominator: int
    exit_share: ExactNumber
    missing_share: ExactNumber
    missing_reasons: tuple[MissingReasonLine, ...] = ()
    hard_requirements: tuple[RequirementSummaryLine, ...] = ()
    headline_zh: str
    notes_zh: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _denominators(self) -> CityResult:
        if self.complete + self.exits + self.missing != self.planned:
            raise ValueError("原计划人数必须等于 C + E + M")
        if self.mean_denominator != self.complete:
            raise ValueError("完整者均值的分母必须是 C")
        if self.range_denominator != self.complete + self.missing:
            raise ValueError("模拟缺失范围的分母必须是 C + M（出局者不进）")
        if self.complete == 0 and self.mean_j is not None:
            raise ValueError("C = 0 时没有均值（不显示为 0）")
        return self


class ComparisonLine(ProductModel):
    city_a: str
    city_b: str
    direction: Literal["a_higher", "b_higher", "undetermined", "not_available"]
    range_difference_low: ExactNumber | None = None
    range_difference_high: ExactNumber | None = None
    note_zh: str


class OptionCoverageLine(ProductModel):
    """选项目录覆盖的摘要（与实际轨迹能否评分是两件事）。"""

    city_ref: str
    status: Literal["complete_for_declared_scope", "partial", "not_assessed"]
    surveyed: int
    resolved: int
    unresolved: int
    excluded: int
    note_zh: str


class ProductResult(ProductModel):
    """面向产品的结果：只投影 E1 已算好的报告与生活事实，不另算“更好看”的分数，不排序，不给现实推荐结论。"""

    meta: ProductMeta
    result_id: RefStr
    run_id: str
    confirmed: ConfirmedRef
    preflight: PreflightRef
    evaluation_status: Literal["generated", "not_generated"]
    not_generated_reason: str | None = None
    run_stage: str
    cities: tuple[CityResult, ...] = ()
    clones: tuple[CloneResult, ...] = ()
    comparisons: tuple[ComparisonLine, ...] = ()
    option_coverage: tuple[OptionCoverageLine, ...] = ()
    #: 逐城的资料性质（DECISIONS/0042），与预检查里的同一份投影。没有时不序列化。
    data_nature: tuple[CityDataNature, ...] = Field(default=(), exclude_if=lambda v: v == ())
    reading_guide_zh: tuple[str, ...] = ()
    limitations_zh: tuple[str, ...] = ()
    sources_zh: tuple[str, ...] = ()
    planner: PlannerAndCost
    real_model_calls: Annotated[int, Field(ge=0)]
    result_fingerprint: Sha256

    @model_validator(mode="after")
    def _generated(self) -> ProductResult:
        if (self.evaluation_status == "not_generated") != (self.not_generated_reason is not None):
            raise ValueError("没有生成评价时必须写原因；生成了不写")
        if self.evaluation_status == "not_generated" and self.cities:
            raise ValueError("没有生成评价时不擅自给城市分类或分数")
        return self

    def content(self) -> dict[str, Any]:
        """参与内容身份的全部字段（指纹本身与样例出处标签除外）。"""
        return identity_content(self, "result_fingerprint")

    def expected_fingerprint(self) -> str:
        return content_fingerprint(self.content())


# ==========================================================================
# 便利资料：来源事实、候选方案、目录编译与选项目录覆盖（模块 D）
# ==========================================================================

FactKind = Literal["observed", "derived", "estimated", "synthetic"]
CandidateField = Literal["price", "lifetime", "effect", "participation", "availability"]


class SourceFact(ProductModel):
    """一条来源事实：原始值、单位、时期、地理、来源性质，以及换算或假设的引用。报价、寿命与减负效果分别保留来源。"""

    fact_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.\-]{0,79}$")]
    field: CandidateField
    raw_value: str | None = Field(default=None, max_length=80)
    unit: str = Field(min_length=1, max_length=60)
    period: str = Field(min_length=1, max_length=40)
    geography: str = Field(min_length=1, max_length=120)
    kind: FactKind
    locator: str | None = Field(default=None, max_length=300)
    conversion: str | None = Field(default=None, max_length=300)
    assumption: str | None = Field(default=None, max_length=300)
    note: str = Field(min_length=1, max_length=400)

    @model_validator(mode="after")
    def _kind_consistent(self) -> SourceFact:
        if self.kind == "observed" and not self.locator:
            raise ValueError(f"{self.fact_id}: 观测事实必须写定位（locator）")
        if self.kind == "synthetic" and not self.assumption:
            raise ValueError(f"{self.fact_id}: 合成设定必须写假设说明（assumption）")
        if self.kind in ("derived", "estimated") and not self.conversion:
            raise ValueError(f"{self.fact_id}: 派生或估计必须写换算 / 方法（conversion）")
        return self


class PriceField(ProductModel):
    """报价字段：金额、币种、基年、当地价口径、计费周期与计价单位；未知时 ``status = unknown``，不填 0。"""

    status: Literal["known", "unknown"]
    amount_text: str | None = None
    currency: str | None = None
    base_year: int | None = None
    price_meaning: Literal["local_price_base_year"] | None = None
    billing: Literal["one_time", "per_month", "per_visit", "per_year"] | None = None
    per_unit: str | None = None
    facts: tuple[str, ...] = ()


class LifetimeField(ProductModel):
    """设备寿命与生效时点。当前 E1 只接受 1–30 的整数年；未知或非整数年**不补值、不取整**。"""

    status: Literal["known", "unknown", "not_applicable"]
    years_text: str | None = None
    effective_from: Literal["same_year", "next_year"] | None = None
    facts: tuple[str, ...] = ()


class TaskRelief(ProductModel):
    task: str = Field(min_length=1, max_length=48)
    residual_hours_per_month: str = Field(min_length=1, max_length=20)


class EffectField(ProductModel):
    """减负效果：每项代表性家务的“本人处理整项家务后仍需的月小时”（不是机器运行或服务人员工作时间）。未知不等于没有效果。"""

    status: Literal["known", "unknown"]
    residuals: tuple[TaskRelief, ...] = ()
    facts: tuple[str, ...] = ()


class ParticipationField(ProductModel):
    """服务需要本人每月花多少小时参与（不是服务人员的工作时间）。设备不适用；服务未知时不能编入（不当成 0）。"""

    status: Literal["known", "unknown", "not_applicable"]
    hours_text: str | None = None
    burden_per_hour_text: str | None = None
    facts: tuple[str, ...] = ()


class OpportunityCandidate(ProductModel):
    """一个可能不完整的设备 / 服务候选，逐字段带状态。``market_status`` 是真实供给状态，与能否编译分开记。"""

    candidate_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.\-]{0,79}$")]
    city_ref: RefStr
    kind: Literal["device", "service"]
    label: str = Field(min_length=1, max_length=120)
    quality: str = Field(min_length=1, max_length=280)
    tasks: tuple[str, ...] = Field(min_length=1)
    market_status: Literal["offered", "not_offered", "unknown"]
    price: PriceField
    lifetime: LifetimeField
    effect: EffectField
    participation: ParticipationField = ParticipationField(status="not_applicable")
    service_period: str | None = Field(default=None, max_length=80)
    target_opportunity_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.\-]{0,63}$")]


class CompiledOpportunity(ProductModel):
    """编入 E1 的一项合法机会：E1 的 ``availability``、逐字段来源映射与最弱来源性质（整条机会的来源标签取它）。"""

    candidate_id: str
    opportunity_id: str
    city_ref: str
    availability: Literal["available", "unknown"]
    #: 编成资料未知的原因（E3-A 收尾 F2）：报价未知、供给未知，或两者都未知；可购买时为空。
    unknown_reason: (
        Literal["price_unknown", "supply_unknown", "supply_and_price_unknown"] | None
    ) = None
    field_sources: dict[str, tuple[str, ...]]
    weakest_source_kind: FactKind
    note_zh: str


class UncompiledCandidate(ProductModel):
    """没有编入 E1 的候选：原因（机器代码 + 中文），真实供给状态原样保留（有卖但寿命未知，不改写成确定买不到）。"""

    candidate_id: str
    city_ref: str
    market_status: Literal["offered", "not_offered", "unknown"]
    reasons: tuple[ProductIssue, ...] = Field(min_length=1)


class TaskDefinition(ProductModel):
    """全组共用的家务任务定义：基准小时、单位与说明（与 E1 协议的家务基准同一单位）。"""

    task: str
    baseline_hours_per_month: str
    unit: Literal["小时/月"]
    description: str


class CatalogCompilation(ProductModel):
    """一次目录编译：合法机会、未编入候选与原因、版本、范围与来源映射。过滤之后的分母仍保留在覆盖报告里。"""

    meta: ProductMeta
    compilation_id: RefStr
    compiler_version: str
    scope_zh: str
    cities: tuple[str, ...]
    tasks: tuple[TaskDefinition, ...]
    compiled: tuple[CompiledOpportunity, ...] = ()
    uncompiled: tuple[UncompiledCandidate, ...] = ()
    facts: tuple[SourceFact, ...] = ()
    compilation_fingerprint: Sha256


#: 供给状态：资料显示这座城有卖、确定没有、不知道（E3-A 收尾 F2：与资料状态、编译状态分开记）。
SupplyStatus = Literal["offered", "not_offered", "unknown"]
#: 资料状态：用到的字段都有值、单位与来源；只有报价未知；有缺失或非法（不编入）；确定没有供给因而不需要；不在调查范围。
DataStatus = Literal["complete", "price_unknown", "incomplete", "not_needed", "not_assessed"]
#: 编译状态：编成可购买的 E1 机会；编成资料未知（看得到、不能买）；没有编入。
CompileStatus = Literal["compiled_available", "compiled_unknown", "not_compiled"]


class InventoryItem(ProductModel):
    """一个调查过的候选在覆盖报告里的结论。``outcome`` 决定分母里的归属：已解决（可购买，或已证实不可得）、未解决（资料未知或
    缺口）、排除（不在声明范围）。三种状态分开写，确定不可得是已确定的调查结果，但不是可购买的方案。"""

    candidate_id: str
    outcome: Literal["resolved", "unresolved", "excluded"]
    supply_status: SupplyStatus
    data_status: DataStatus
    compile_status: CompileStatus
    reason_zh: str


class CityInventory(ProductModel):
    city_ref: str
    status: Literal["complete_for_declared_scope", "partial", "not_assessed"]
    surveyed: int
    resolved: int
    unresolved: int
    excluded: int
    items: tuple[InventoryItem, ...] = ()

    @model_validator(mode="after")
    def _counts(self) -> CityInventory:
        if self.resolved + self.unresolved + self.excluded != self.surveyed:
            raise ValueError(
                f"{self.city_ref}: 已解决 + 未解决 + 排除 必须等于调查的候选数（分母不因过滤变小）"
            )
        return self


class OptionInventoryCoverage(ProductModel):
    """选项目录覆盖（``option_inventory_coverage``）：在**声明的调查范围**内，便利设备与服务的资料齐不齐。

    它与“实际轨迹能否评分”、分身的 C / E / M 是三个不同的判断：目录不完整不改写 E1 已发生轨迹的评分；没有采用方案且资源账完整时，
    E1 照样给出确定的 q = 0。不保证穷尽整个城市市场。"""

    meta: ProductMeta
    coverage_id: RefStr
    scope_version: str
    declared_scope_zh: str
    categories: tuple[str, ...] = Field(min_length=1)
    cities: tuple[CityInventory, ...]
    not_exhaustive_zh: str


# ==========================================================================
# 注册：导出 schema 与样例校验用同一份清单
# ==========================================================================


class LifeItemVerdict(ProductModel):
    """一项生活扩展候选（完整食品方案、休闲活动、装备、享受型用品或服务）能不能交给 E1 执行（DECISIONS/0041）。

    ``executable``：放进这座城目录的副本之后，E1 的契约、目录、跨对象与可结算检查都接受，种类与 E1 的执行角色一致；
    ``rejected``：缺必需的事实或违反规则（例如没有做饭时间、种类与事实对不上、带日期或年龄限制、代替正餐的套餐、寿命不明、
    金额口径不对、类别没有声明）；``unavailable``：确定不可得（保留记录，不能执行）；``unsupported``：用了本版词表没有的类别或
    功能；``data_unknown``：报价或评分要读的事实未知（不当零价，也不当已知）。原因是固定写法或 E1 的拒绝说明，不含本机路径。"""

    candidate_id: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_.\-]{0,79}$")]
    kind: Literal["food_plan", "activity", "equipment", "enjoyment"]
    label: str = Field(min_length=1, max_length=120)
    status: Literal["executable", "rejected", "unavailable", "unsupported", "data_unknown"]
    reasons_zh: tuple[Annotated[str, Field(max_length=400)], ...] = ()

    @model_validator(mode="after")
    def _reasons(self) -> LifeItemVerdict:
        if (self.status == "executable") == bool(self.reasons_zh):
            raise ValueError(f"{self.candidate_id}: 可执行的候选不写原因，其余必须写明原因")
        return self


class LifeItemCompilation(ProductModel):
    """生活扩展候选的编译检查（编译器 ``e3-life-item-compiler-v1``）。编译产物有自己的内容身份 ``compilation_fingerprint``。"""

    meta: ProductMeta
    compilation_id: RefStr
    compiler_version: str
    scope_zh: str
    city_ref: str
    verdicts: tuple[LifeItemVerdict, ...]
    compilation_fingerprint: Sha256


# ==========================================================================
# 托管服务（E3C，DECISIONS/0043）：会话与次数、草稿保存、确认请求、运行意图、删除回执与统一错误
#
# 这些消息只描述网页与服务之间的业务请求与回复；分身、生活、评分与 C / E / M 的含义都沿用上面的消息与 E1。浏览器从不提交
# ``person_ref``、``user_id``、完整的确认画像或运行设置：身份由会话决定，确认画像由服务端按审阅过的草稿生成。
# ==========================================================================

#: 网页流程的阶段（不是 E1 的 C / E / M）：检查中、等用户确认、被阻断、排队、运行中、等维护者、本次服务流程结束（不代表所有分身
#: 都完整）、开始前取消、删除中、已删除、已过期。
ServiceStage = Literal[
    "checking",
    "awaiting_confirmation",
    "blocked",
    "queued",
    "running",
    "needs_attention",
    "finished",
    "cancelled",
    "deleting",
    "deleted",
    "expired",
]
#: 现在在等谁：用户（确认、修改条件）、维护者（不确定的发送、身份冲突、需要人工核对）、服务（排队检查、模拟进行中）或谁都不等。
WaitingFor = Literal["user", "maintainer", "service", "none"]
#: 公开的保存期限按什么算（E3C 接口补齐）：``intent_activity`` 最后一次有效业务操作起（等待、暂停、排队、检查没通过、取消）；
#: ``first_finished`` 首次结束起（有结果）；``active_execution`` 正在执行，这个阶段不给固定截止时间（执行中的保护规则不变）；
#: ``unavailable`` 已删除、删除中或已过期，没有可用期限。查看、轮询、切换语言都不续期。
ExpiryBasis = Literal["intent_activity", "first_finished", "active_execution", "unavailable"]
#: 服务端此刻允许的操作。前端只显示这里列出的按钮。
#: ``start_again``（一次开始，DECISIONS/0048）：开始授权作废之后，用户可以重新取一份设置预览、再“确认并开始”一次（新的幂等键）；
#: 带开始授权的意图没有 ``submit``（不需要、也不接受检查之后的第二次确认）。
IntentAction = Literal[
    "submit",
    "cancel",
    "resume",
    "delete",
    "view_progress",
    "view_result",
    "create_new_intent",
    "start_again",
]
_SUPPORT_REF = r"^sup-[0-9a-f]{16}$"
SupportRef = Annotated[str, Field(pattern=_SUPPORT_REF)]
_TIME = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"
UtcTime = Annotated[str, Field(pattern=_TIME)]


class QuotaSummary(ProductModel):
    """这个专属空间的使用次数：一共 ``total`` 次启动模拟的机会；``used`` 已开始的、``reserved`` 已确认开始但还没有启动的、
    ``available`` 还能用的。次数不自动重置；删除结果、换设备、重发链接都不改变它。3 次是启动模拟的机会，不保证每次所有分身都完整。"""

    total: Annotated[int, Field(ge=0)]
    used: Annotated[int, Field(ge=0)]
    reserved: Annotated[int, Field(ge=0)]
    available: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def _sum(self) -> QuotaSummary:
        if self.used + self.reserved + self.available != self.total:
            raise ValueError("used + reserved + available 必须等于 total")
        return self


#: 一次运行的费用金额（元，最多到分）：选项与范围都用这种写法，边界按分比较，不用浮点。
_CENTS = r"^(0|[1-9][0-9]{0,6})(\.[0-9]{1,2})?$"
CentsText = Annotated[str, Field(pattern=_CENTS)]


class IntRange(ProductModel):
    """一个整数选项：不选时用 ``default``；允许 ``minimum``–``maximum``（含两端）。"""

    default: Annotated[int, Field(ge=1)]
    minimum: Annotated[int, Field(ge=1)]
    maximum: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _ordered(self) -> IntRange:
        if not self.minimum <= self.default <= self.maximum:
            raise ValueError("必须 minimum ≤ default ≤ maximum")
        return self


class MoneyRange(ProductModel):
    """一次运行的费用上限选项（应用侧按报价估算，不是供应商账单上限）。"""

    default: CentsText
    minimum: CentsText
    maximum: CentsText
    currency: Literal["CNY", "USD"]


class RunOptionsPolicy(ProductModel):
    """服务允许用户为一次模拟选择什么（维护者在服务配置里定，网页只照着显示控件，不另做判断）。

    * 城市：来自用户确认的画像（不另交一份城市列表），必须是 ``cities_allowed`` 里不重复的非空子集；不会替用户删城或补城；
    * 每城分身数、年数、这次运行的费用上限：创建运行意图时可以选，省略就用默认值；
    * 年数取服务允许与资料包支持的交集（``years_allowed``）：资料包没有验证过的年数不开放，不为此生成更多年份的菜单。
    规划模型、报价、并发、修订与重试、请求长度上限由维护者控制，不是用户选项。"""

    cities_allowed: tuple[RefStr, ...] = Field(min_length=1)
    clones_per_city: IntRange
    years_default: Annotated[int, Field(ge=1)]
    years_allowed: tuple[Annotated[int, Field(ge=1)], ...] = Field(min_length=1)
    max_cost: MoneyRange
    notes_zh: tuple[str, ...] = ()


class ServiceRules(ProductModel):
    """服务的规则（服务端配置产生，网页只显示，不能修改）。``cities``、``clones_per_city``、``years``、``max_cost`` 是不选时的默认
    规模与费用上限；能选什么见 ``options``。"""

    runs_total: Annotated[int, Field(ge=1)]
    cities: Annotated[int, Field(ge=1)]
    clones_per_city: Annotated[int, Field(ge=1)]
    years: Annotated[int, Field(ge=1)]
    planner_kind: Literal["mock", "llm"]
    live_calls_enabled: bool
    max_cost: MoneyText
    currency: Literal["CNY", "USD"]
    result_retention_days: Annotated[int, Field(ge=1)]
    draft_retention_days: Annotated[int, Field(ge=1)]
    intent_retention_days: Annotated[int, Field(ge=1)]
    session_days: Annotated[int, Field(ge=1)]
    #: 确认画像从首次确认起可以用来准备新模拟的天数（重复确认与查询不续期）
    profile_retention_days: Annotated[int, Field(ge=1)]
    options: RunOptionsPolicy
    notes_zh: tuple[str, ...] = ()


class RunSettingsView(ProductModel):
    """一次运行意图的有效设置：创建时由服务端校验并保存，之后的检查、确认、执行、进度、结果与恢复都用这一份（改服务默认值不影响它）。
    规划候选上限与服务尝试上限按这次的规模与修订、重试规则算出。离线 mock 不花钱：费用上限照样写用户这次的选择（它进入设置的
    身份），服务尝试上限不出现。"""

    cities: tuple[RefStr, ...] = Field(min_length=1)
    clones_per_city: Annotated[int, Field(ge=1)]
    years: Annotated[int, Field(ge=1)]
    clones_total: Annotated[int, Field(ge=1)]
    planner_kind: Literal["mock", "llm"]
    max_cost: MoneyText | None = None
    currency: Literal["CNY", "USD"] | None = None
    call_limit: Annotated[int, Field(ge=1)]
    service_attempt_limit: Annotated[int, Field(ge=0)] | None = None
    settings_fingerprint: Sha256


class DraftSummary(ProductModel):
    draft_id: RefStr
    revision: Annotated[int, Field(ge=1)]
    updated_at: UtcTime
    expires_at: UtcTime


class SessionExchangeRequest(ProductModel):
    """用专属链接换会话（``POST /api/v1/session``，同源 JSON）。``invite_token`` 是链接 ``#token=`` 后面的随机秘密：网页从地址的
    片段里读出、交换后立即从地址栏移除；它不进查询串、路径、localStorage 或任何模型请求，服务端只存它的摘要。"""

    invite_token: Annotated[str, Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_\-]+$")]


class SessionView(ProductModel):
    """当前会话（``POST /api/v1/session`` 交换邀请之后、``GET /api/v1/session``）：到期时间、本会话的 CSRF token、次数与服务规则。
    会话由 HttpOnly Cookie 携带，这里不返回会话秘密；``csrf_token`` 只在修改请求的 ``X-CSRF-Token`` 头里带回。"""

    meta: ProductMeta
    session_expires_at: UtcTime
    csrf_token: Annotated[str, Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_\-]+$")]
    quota: QuotaSummary
    rules: ServiceRules
    #: 本人还在保存期内的草稿（新的在前）：换设备后从这里接着填
    drafts: tuple[DraftSummary, ...] = ()
    #: 本人尚未结束的那一次运行意图（同一身份最多一个）：换设备后从这里接着走流程
    open_intent_id: RefStr | None = None


class DraftView(ProductModel):
    """服务端保存的一份草稿：草稿编号与修订号由服务器分配；``expires_at`` 是最后一次编辑后 30 天。"""

    meta: ProductMeta
    draft: UserInputDraft
    updated_at: UtcTime
    expires_at: UtcTime


class DraftSaveRequest(ProductModel):
    """保存草稿（``PUT /api/v1/drafts/{id}``）：``expected_revision`` 必须等于服务端当前的修订号，否则返回冲突，不覆盖新答案。
    ``draft.draft_id`` 必须是路径里的编号；服务端写入时修订号加一。"""

    expected_revision: Annotated[int, Field(ge=1)]
    draft: UserInputDraft


class ReviewReply(ProductModel):
    """审阅已保存的那一版草稿（``POST /api/v1/drafts/{id}/review``）。审阅绑定草稿编号、修订号、内容与所用资料包。"""

    meta: ProductMeta
    review: ProfileReview
    reviewed_revision: Annotated[int, Field(ge=1)]


class ProfileConfirmRequest(ProductModel):
    """确认画像（``POST /api/v1/profiles/confirm``）：只带服务端审阅的引用与知悉项；服务端重新校验并生成确认画像。"""

    review_id: RefStr
    draft_id: RefStr
    draft_revision: Annotated[int, Field(ge=1)]
    acknowledged: tuple[Annotated[str, Field(min_length=1, max_length=160)], ...] = ()


# ==========================================================================
# 显示附层（e3-display-v2）：独立于业务消息的中英显示文字。原消息、内容身份与确认令牌都不变；页面的数字、权限与身份
# 永远取原消息，显示字段只换文字。v2（E3 → E5 收尾修复）：绑定按生成位置与实体身份、不按中文；历史对象用它自己保存的绑定与
# 资源版本；资源校验失败只让显示缺失；新增审阅的知悉项说明与按键的作答状态。
# ==========================================================================

DISPLAY_SCHEMA_VERSION = "e3-display-v2"
DisplayLocale = Literal["zh", "en"]
DisplayStatus = Literal["complete", "partial", "unavailable"]
#: 一个显示位置为什么没有这种语言的文字（不猜、不拿中文反查英文）
DisplayMissingReason = Literal[
    "no_binding",
    "translation_missing",
    "translation_stale",
    "source_changed",
    "ambiguous",
    "untranslated_param",
    "resources_unavailable",
    "resources_invalid",
    "binding_stale",
    "binding_unverified",
]


class DisplayField(ProductModel):
    """原消息里一个可展示文字位置的显示文字。``pointer`` 是相对原消息（或原回复）的 JSON Pointer，只指向含中文的业务说明文字——
    不指向数值、权重、编号、动作枚举、秘密，也不指向用户原文（用户原文保持原文，不翻译）。``key`` 是稳定的文案 / 实体键。"""

    pointer: Annotated[str, Field(pattern=r"^(/[^/]+)+$", max_length=400)]
    key: Annotated[str, Field(min_length=1, max_length=300)]
    status: Literal["provided", "missing"]
    text: Annotated[str, Field(min_length=1, max_length=6000)] | None = None
    source_locale: DisplayLocale
    missing_reason: DisplayMissingReason | None = None
    #: 这一条用的资源版本与外层不同时写明（例如内嵌的旧预检查）
    resource_version: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> DisplayField:
        if (self.status == "provided") != (self.text is not None):
            raise ValueError("provided 才有文字")
        if (self.status == "missing") != (self.missing_reason is not None):
            raise ValueError("missing 必须写原因")
        return self


class DisplayTranslation(ProductModel):
    status: DisplayStatus
    fields: tuple[DisplayField, ...] = ()


class DisplayAttachment(ProductModel):
    """即时回复（错误、确认结果）的显示附层：这类回复可能没有可以再读的对象，所以随回复一起给中英两种文字。
    ``source_view_fingerprint`` 是去掉 ``display`` 之后的原回复的规范 JSON 摘要；``pointer`` 都相对那份原回复。"""

    display_schema_version: Literal["e3-display-v2"]
    resource_version: str | None = None
    source_view_fingerprint: Sha256
    translations: dict[DisplayLocale, DisplayTranslation]

    @model_validator(mode="after")
    def _both(self) -> DisplayAttachment:
        if set(self.translations) != {"zh", "en"}:
            raise ValueError("中英两种都要给（缺的写明 missing）")
        return self


class DisplayText(ProductModel):
    """一段同时给出中英两种的显示文字（知悉项、作答状态用）。``zh`` 是中文原文（旧对象没有保存时为空）；``en`` 有译文才给，``status``
    与 ``missing_reason`` 说英文有没有、为什么没有。``key`` 是稳定的模板 / 实体键，``resource_version`` 是渲染用的资源版本。"""

    key: Annotated[str, Field(min_length=1, max_length=300)]
    zh: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    en: Annotated[str, Field(min_length=1, max_length=4000)] | None = None
    status: Literal["provided", "missing"]
    missing_reason: DisplayMissingReason | None = None
    resource_version: str | None = None

    @model_validator(mode="after")
    def _shape(self) -> DisplayText:
        if (self.status == "provided") != (self.en is not None and self.zh is not None):
            raise ValueError("provided 才有中英两种文字")
        if (self.status == "missing") != (self.missing_reason is not None):
            raise ValueError("missing 必须写原因")
        return self


class DisplayAcknowledgement(ProductModel):
    """审阅里一项必须知悉的事项的说明（只是显示）。``ack_id`` 就是原审阅 ``pending_confirmations`` 里的编号，确认时照旧提交这个编号；
    这里不新增、不删除、不代替任何必须知悉的事项，也不表示已经勾选。``statement_pointer`` 指向原审阅里相关的那条陈述（只对
    “留存的陈述”这一类，相对 ``source``）。"""

    ack_id: Annotated[str, Field(min_length=1, max_length=200)]
    text: DisplayText
    statement_pointer: Annotated[str, Field(pattern=r"^(/[^/]+)+$", max_length=200)] | None = None


class DisplayAnswerState(ProductModel):
    """兴趣（``leisure``）或口味（``taste``）的一种作答状态的名称与含义，按正式状态 ``state`` 给出（不靠列表顺序、不拆冒号）。"""

    answer: Literal["leisure", "taste"]
    state: LeisureAnswerState | TasteAnswerState
    name: DisplayText
    meaning: DisplayText

    @model_validator(mode="after")
    def _state(self) -> DisplayAnswerState:
        allowed = get_args(LeisureAnswerState if self.answer == "leisure" else TasteAnswerState)
        if self.state not in allowed:
            raise ValueError(f"{self.answer} 没有作答状态 {self.state}")
        return self


class LocalizedText(ProductModel):
    """一段中英对照的显示文字；没有英文时 ``en`` 为空（页面显示中文原文并标明英文缺失）。"""

    zh: str = Field(min_length=1, max_length=2000)
    en: Annotated[str, Field(min_length=1, max_length=4000)] | None = None


class ConfirmedProfileSummary(ProductModel):
    """一份确认画像的服务端摘要（E3C 接口补齐）：首次确认时间与可以用来准备新模拟的截止时间都读服务库，重复确认、查询都不续期。
    它不是确认画像本体的一部分，不进入画像的内容身份；画像到期只影响准备新的模拟，已经准备的意图用自己的副本与期限。"""

    confirmed_id: RefStr
    content_fingerprint: Sha256
    draft_id: RefStr
    draft_revision: Annotated[int, Field(ge=1)]
    first_confirmed_at: UtcTime
    expires_at: UtcTime


class ProfileConfirmReply(ProductModel):
    """确认的结果：成功时是服务端生成的确认画像与它的服务端摘要（首次确认与截止时间）；被拒时列出原因（草稿改过、知悉项缺失、
    审阅过时……），摘要为空。"""

    meta: ProductMeta
    status: Literal["confirmed", "refused"]
    confirmed: ConfirmedProfile | None = None
    issues: tuple[ProductIssue, ...] = ()
    profile_summary: ConfirmedProfileSummary | None = None
    #: 显示附层（中英文字；确认被拒时可能没有可以再读的对象）
    display: DisplayAttachment | None = None

    @model_validator(mode="after")
    def _shape(self) -> ProfileConfirmReply:
        if (self.status == "confirmed") != (self.confirmed is not None):
            raise ValueError("只有 confirmed 带确认画像")
        if (self.status == "confirmed") != (self.profile_summary is not None):
            raise ValueError("只有 confirmed 带画像摘要")
        if self.status == "refused" and not self.issues:
            raise ValueError("被拒时必须写原因")
        return self


class ConfirmedProfileView(ProductModel):
    """读取本人的一份确认画像（``GET /api/v1/profiles/{confirmed_id}``）：服务端摘要（日期）与原确认画像。"""

    meta: ProductMeta
    summary: ConfirmedProfileSummary
    confirmed: ConfirmedProfile


class ProfileList(ProductModel):
    """本人还能用来准备新模拟的确认画像摘要（``GET /api/v1/profiles``），新的在前；已删除、已过期的不列出。"""

    meta: ProductMeta
    profiles: tuple[ConfirmedProfileSummary, ...] = ()


# ---------------------------------------------------------------------- 一次开始（API v1.4，DECISIONS/0048）

#: 设置预览里范围说明与必要知悉项的业务版本：说明的含义变了才换（同意身份随之改变）；换语言不改它。
START_SCOPE_VERSION = "e3c-start-scope-v1"
#: 开始之前要知悉的几项。``ack:start.cost_cap`` 只在这次用真实模型（付费）时出现。
StartAckId = Literal[
    "ack:start.one_run", "ack:start.auto_start", "ack:start.scope", "ack:start.cost_cap"
]
#: 设置预览说“此刻不能开始”的原因（与对应的服务错误同名）
StartRefusal = Literal["paid_calls_disabled", "quota_exhausted", "intent_conflict"]
_IDEMPOTENCY_KEY = r"^[A-Za-z0-9_.:\-]+$"


class RunStartPreviewRequest(ProductModel):
    """设置预览（``POST /api/v1/run-start-preview``）：与准备模拟同样的确认画像引用与运行选项（省略的用服务默认值）。只读：不编译生活
    输入、不排队、不预留次数、不调用模型、不写库。"""

    confirmed_id: RefStr
    clones_per_city: Annotated[int, Field(ge=1, le=1000)] | None = None
    years: Annotated[int, Field(ge=1, le=100)] | None = None
    max_cost_cny: CentsText | None = None


class StartAcknowledgement(ProductModel):
    """开始之前要知悉的一项（“确认并开始”时一起勾选）。同意身份只记 ``ack_id`` 与范围说明的业务版本，文字随语言变。"""

    ack_id: StartAckId
    text_zh: str = Field(min_length=1, max_length=300)


class RunStartPreview(ProductModel):
    """设置预览：这次会用的有效设置（城市、每城分身数、年数、规划器与模型种类、费用上限、调用上限）、范围说明、必要知悉项与同意身份。

    ``consent_fingerprint`` 绑定确认画像、资料包、有效设置（含模型、报价与费用上限）、范围说明的业务版本与知悉项编号；换语言不改它。
    费用上限是停机上限，不是预计费用。``start_allowed`` 为假时 ``start_refusal`` 说明此刻为什么不能开始（付费调用没开、次数用完、已经
    有一次条件不同的准备）。"""

    meta: ProductMeta
    confirmed: ConfirmedRef
    settings: RunSettingsView
    planner: PlannerAndCost
    scope_version: Literal["e3c-start-scope-v1"]
    scope_zh: tuple[str, ...] = ()
    acknowledgements: tuple[StartAcknowledgement, ...] = Field(min_length=1)
    start_allowed: bool
    start_refusal: StartRefusal | None = None
    quota: QuotaSummary
    consent_fingerprint: Sha256
    #: 显示附层：这份回复的中英两种文字（即时回复，没有可以再读的对象）
    display: DisplayAttachment | None = None

    @model_validator(mode="after")
    def _shape(self) -> RunStartPreview:
        if self.start_allowed == (self.start_refusal is not None):
            raise ValueError("start_allowed 为假时才写 start_refusal")
        return self


class StartWhenReady(ProductModel):
    """一次“确认并开始”（``RunIntentCreateRequest.start``）：用户看过设置预览、勾选了全部必要知悉项，授权“检查通过后就按这些设置开始，
    只开始一次”。``consent_fingerprint`` 是那份预览的同意身份（服务端重新核对，对不上就拒绝）；``idempotency_key`` 是这一次操作的
    幂等键：连点、回复丢失、刷新或重开后重发都带同一个键（页面要把它和这次操作一起保存），服务端返回同一个意图——即使它已经结束、
    取消或删除，也不另起运行；同一个键配不同请求是冲突。"""

    mode: Literal["start_when_ready"]
    consent_fingerprint: Sha256
    acknowledged: tuple[StartAckId, ...] = Field(min_length=1)
    accept: Literal[True]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=80, pattern=_IDEMPOTENCY_KEY)]


class RunIntentCreateRequest(ProductModel):
    """准备一次模拟（``POST /api/v1/run-intents``）：引用一份确认画像，可以选每城分身数、年数与这次的费用上限（省略就用服务默认值，
    能选什么见 ``ServiceRules.options``）。城市来自确认画像。服务端校验后保存这次的有效设置，再排队做运行前检查（不扣次数）。

    带 ``start``（一次开始）：检查通过之后由服务按这份授权自动开始，页面不需要、也不应该再发确认；不带就是原来的流程（检查之后由
    用户在 ``submit`` 确认开始）。"""

    confirmed_id: RefStr
    clones_per_city: Annotated[int, Field(ge=1, le=1000)] | None = None
    years: Annotated[int, Field(ge=1, le=100)] | None = None
    max_cost_cny: CentsText | None = None
    start: StartWhenReady | None = None


#: 等待页的阶段（DECISIONS/0049）：检查中、排队、模拟中、整理结果、完成、需要处理，以及结束在取消、删除或过期
ProgressPhase = Literal[
    "checking",
    "queued",
    "simulating",
    "assembling_result",
    "finished",
    "needs_attention",
    "cancelled",
    "deleted",
    "expired",
]
#: 进度条怎么画：不定长 / 按已结算的分身年度数 / 完成
ProgressMode = Literal["indeterminate", "clone_years", "finished"]


class ProgressSummary(ProductModel):
    """等待页的一句当前状态与进度条（DECISIONS/0049）：读取运行意图时按服务库的阶段与 E1 写下的权威进度快照当场生成（只读，没有
    ``run_id`` 的检查阶段也有）。

    * ``sentence_zh`` 是一句当前状态，新的一句覆盖上一句（不是执行历史）；``updated_at`` 是这句所依据的记录的时间（进度快照或意图
      本身的更新时间），断线时页面照它显示“最后已知”。
    * ``mode``：``indeterminate`` 不定长——检查、排队、整理结果、还没有第一份进度快照，以及停下时没有快照；``clone_years`` 按已结算的
      分身年度数：``settled_clone_years`` 是 E1 进度快照里各分身已结算年数之和（出局或中断之后没有经历的年份不算），
      ``planned_clone_years`` 是这次的名单（城市 × 每城分身数，E1 的名单按它冻结）× 年数；``finished`` 只在结果真的可以读时，完成不等于
      所有分身都是 C。不按调用次数、不按时间增长；不含内部状态、路径、凭证或评分参数。"""

    phase: ProgressPhase
    sentence_zh: str = Field(min_length=1, max_length=200)
    updated_at: UtcTime
    mode: ProgressMode
    settled_clone_years: Annotated[int, Field(ge=0)] | None = None
    planned_clone_years: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _shape(self) -> ProgressSummary:
        counted = self.settled_clone_years is not None
        if counted != (self.planned_clone_years is not None):
            raise ValueError("已结算与计划的分身年度数要一起给")
        if self.mode == "clone_years" and not counted:
            raise ValueError("clone_years 要给分身年度数")
        if self.mode == "indeterminate" and counted:
            raise ValueError("不定长的进度不给分身年度数")
        if counted and self.settled_clone_years > self.planned_clone_years:  # type: ignore[operator]
            raise ValueError("已结算的分身年度数不能超过计划")
        if (self.mode == "finished") != (self.phase == "finished"):
            raise ValueError("只有结果可以读的完成阶段用 finished")
        return self


StartAuthorizationState = Literal["pending", "used", "void"]
#: 开始授权作废的原因（说明见运行意图的 ``explanation``；检查没通过时是那次检查自己的说明）
StartVoidReason = Literal[
    "conditions_changed",
    "consent_changed",
    "options_not_allowed",
    "paid_calls_disabled",
    "quota_exhausted",
    "preflight_blocked",
    "authorization_mismatch",
    "data_pack_unavailable",
    "internal_error",
    "cancelled",
]


class StartAuthorizationView(ProductModel):
    """这次准备带着的一次开始授权（DECISIONS/0048）。``pending``：检查通过后由服务按它开始；``used``：已经按它开始——确认来源是这份
    授权，不是用户在检查之后又点了一次；``void``：条件变了或开始前停下，授权作废，不会再自动开始（``void_reason``）。"""

    mode: Literal["start_when_ready"]
    state: StartAuthorizationState
    consent_fingerprint: Sha256
    acknowledged: tuple[StartAckId, ...]
    authorized_at: UtcTime
    used_at: UtcTime | None = None
    void_reason: StartVoidReason | None = None


class DeletionState(ProductModel):
    state: Literal["none", "deleting", "deleted"]
    requested_at: UtcTime | None = None
    completed_at: UtcTime | None = None


class RunIntentView(ProductModel):
    """一次运行意图的公开视图（网页流程）：阶段与中文说明、当前预检查与确认身份、进度、是否有结果、此刻允许的操作、在等谁、
    时间与删除状态、安全解释与支持编号。不含流程内部编号、checkpoint、文件路径、堆栈或内部诊断正文。

    ``stage = finished`` 只表示本次服务流程结束：结果里的 C / E / M、未知与部分结果照 E1 原样显示；没有结果时 ``result_available``
    为假，不编一个。"""

    meta: ProductMeta
    intent_id: RefStr
    run_id: str | None = None
    stage: ServiceStage
    stage_zh: str = Field(min_length=1, max_length=120)
    waiting_for: WaitingFor
    confirmed: ConfirmedRef
    preflight: ProductPreflight | None = None
    confirm_with: Sha256 | None = None
    progress: RunProgress | None = None
    result_available: bool
    allowed_actions: tuple[IntentAction, ...] = ()
    created_at: UtcTime
    updated_at: UtcTime
    #: 当前阶段实际适用的保存截止时间（与服务端期限判断同一条规则）；``expiry_basis`` 说明按什么算，没有固定截止时间时为空
    expires_at: UtcTime | None = None
    expiry_basis: ExpiryBasis
    deletion: DeletionState
    explanation: PublicExplanation | None = None
    support_ref: SupportRef | None = None
    quota: QuotaSummary
    #: 这次的有效设置（删除或过期之后不再显示：里面有用户选的城市）
    settings: RunSettingsView | None = None
    #: 一次开始的授权（没有就是原来的流程：检查之后由用户确认开始）；删除或过期之后不再显示
    start: StartAuthorizationView | None = None
    #: 已经确认开始时，确认来自哪里：用户在检查之后确认（``user_confirm``），或用户在检查之前的一次开始授权（``start_authorization``）
    confirmation_source: Literal["user_confirm", "start_authorization"] | None = None
    #: 等待页的一句当前状态与进度条（每次读取当场生成；只读）
    progress_summary: ProgressSummary


class RunSubmitRequest(ProductModel):
    """确认开始（``POST /api/v1/run-intents/{id}/submit``）：``confirm_with`` 是用户看到的那份预检查的身份；``accept_scope`` 表示“我知道
    这次范围、限制和费用保护，使用一次机会开始”。同一意图重复确认只对应一个运行，即使换了幂等键；同一个键配不同内容返回冲突。"""

    confirm_with: Sha256
    idempotency_key: Annotated[
        str, Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_.:\-]+$")
    ]
    accept_scope: Literal[True]


class RunSubmitReply(ProductModel):
    meta: ProductMeta
    receipt: RunReceipt
    intent: RunIntentView


class RunSummary(ProductModel):
    intent_id: RefStr
    run_id: str | None = None
    stage: ServiceStage
    stage_zh: str
    result_available: bool
    created_at: UtcTime
    finished_at: UtcTime | None = None
    expires_at: UtcTime | None = None
    expiry_basis: ExpiryBasis
    settings: RunSettingsView | None = None


class RunList(ProductModel):
    """本人的运行列表（``GET /api/v1/runs``），新的在前；已删除的只剩不含内容的条目时不列出。"""

    meta: ProductMeta
    runs: tuple[RunSummary, ...] = ()
    quota: QuotaSummary


class DeletionReceipt(ProductModel):
    """删除回执。运行中删除：立即不可查询、标为删除中、不再批准新的模型请求；已经发出的请求可能完成并记账，不能撤回。收尾之后清掉
    内容，标为已删除。保留的最小记录只有不透明身份、次数事件、必要的费用摘要、删除时间与防重记录，不含答案、模型正文或生活故事。"""

    meta: ProductMeta
    scope: Literal["run", "draft", "all_data"]
    target_ref: str | None = None
    status: Literal["deleting", "deleted"]
    note_zh: str = Field(min_length=1, max_length=400)
    kept_zh: tuple[str, ...] = ()
    quota: QuotaSummary


ServiceErrorCode = Literal[
    "unauthenticated",
    "not_found",
    "revision_conflict",
    "preflight_stale",
    "quota_exhausted",
    "idempotency_conflict",
    "intent_conflict",
    "not_resumable",
    "cancel_not_allowed",
    "result_unavailable",
    "deleting",
    "deleted",
    "expired",
    "data_pack_unavailable",
    "paid_calls_disabled",
    "run_options_invalid",
    "run_settings_unavailable",
    "start_consent_stale",
    "start_acknowledgement_missing",
    "start_already_authorized",
    "rate_limited",
    "csrf_failed",
    "origin_rejected",
    "payload_too_large",
    "invalid_request",
    "internal_error",
]


class ServiceError(ProductModel):
    """统一的安全错误：机器代码、HTTP 状态与“发生了什么—影响是什么—下一步怎么做”的中文，加一个支持编号。不含用户输入、内部路径、
    堆栈、原始校验异常或秘密。"""

    meta: ProductMeta | None = None
    status: Annotated[int, Field(ge=400, le=599)]
    code: ServiceErrorCode
    message_zh: str = Field(min_length=1, max_length=300)
    impact_zh: str = Field(min_length=1, max_length=300)
    next_action_zh: str = Field(min_length=1, max_length=300)
    support_ref: SupportRef | None = None
    retry_after_seconds: Annotated[int, Field(ge=1)] | None = None
    #: 哪个选项不行（``run_options_invalid`` 时写 ``clones_per_city`` / ``years`` / ``max_cost_cny`` / ``cities``；不回显用户填的值）
    field: Literal["clones_per_city", "years", "max_cost_cny", "cities"] | None = None
    #: 显示附层：三段说明的中英文（固定双语目录生成；未登录时也有）
    display: DisplayAttachment | None = None
    sample: SampleLabel | None = None


# ---------------------------------------------------------------------- 显示出口（GET /api/v1/display、/display/geography）

DisplayKind = Literal[
    "capabilities", "session", "draft", "review", "profile", "intent", "progress", "result", "runs"
]
#: 每种显示对象对应的原公共消息
DISPLAY_SOURCES: dict[str, type[ProductModel]] = {
    "capabilities": ProductCapabilities,
    "session": SessionView,
    "draft": DraftView,
    "review": ReviewReply,
    "profile": ConfirmedProfileView,
    "intent": RunIntentView,
    "progress": RunProgress,
    "result": ProductResult,
    "runs": RunList,
}


class MetricDisplaySpec(ProductModel):
    """结果色阶的显示约定：只声明已有分数的合法数值域与读法，不计算新分数、不公开评分系数。``mean_j`` 是同一城市“完成且评价完整”
    分身的 J 的算术平均，当前评价规则下有效值在 [0, 1]（``minimum`` / ``maximum`` 是这个数值域，不是图例窗口）。图例窗口由页面按
    ADR 0045 第 4 条在同一份结果之内适度放大：同一结果的所有国家、两种地图共用一个窗口，图例写明窗口两端，不暗示最低的城市是零分、
    最高的是满分；分数、分母与比较结论都不变。"""

    key: Literal["mean_j"]
    minimum: Literal["0"]
    maximum: Literal["1"]
    unit: Literal["model_score"]
    direction: Literal["higher_model_evaluation"]
    denominator_field: Literal["mean_denominator"]
    comparison_scope: Literal["within_result_only"]
    #: 这份结果绑定的协议、评分与评价（公开引用，不是参数）
    evaluation_refs: tuple[str, ...] = ()
    notes: tuple[LocalizedText, ...] = ()


DisplaySource = (
    ProductCapabilities
    | SessionView
    | DraftView
    | ReviewReply
    | ConfirmedProfileView
    | RunIntentView
    | RunProgress
    | ProductResult
    | RunList
)


class DisplayEnvelope(ProductModel):
    """一次显示读取（``GET /api/v1/display``）：原公共消息 ``source`` 与它的显示文字同次返回（避免进度更新后错配旧翻译）。页面把
    ``source`` 当作原业务消息使用，``fields`` 只用来换文字；数字、权限与身份永远取 ``source``。读取只鉴权、读取与纯函数渲染：
    不保存答案、不预检查、不恢复、不重放、不调用模型。"""

    display_schema_version: Literal["e3-display-v2"]
    kind: DisplayKind
    id: str | None = Field(default=None, max_length=128)
    source: DisplaySource
    source_view_fingerprint: Sha256
    locale: DisplayLocale
    resource_version: str | None = None
    status: DisplayStatus
    fields: tuple[DisplayField, ...] = ()
    metrics: tuple[MetricDisplaySpec, ...] = ()
    #: 审阅（``kind = review``）的知悉项说明：与 ``source.review.pending_confirmations`` 一一对应、同样的顺序
    acknowledgements: tuple[DisplayAcknowledgement, ...] = ()
    #: 能力清单、草稿与审阅里兴趣与口味的作答状态（按正式状态给名称与含义）
    answer_states: tuple[DisplayAnswerState, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> DisplayEnvelope:
        if not isinstance(self.source, DISPLAY_SOURCES[self.kind]):
            raise ValueError(f"{self.kind} 的原消息必须是 {DISPLAY_SOURCES[self.kind].__name__}")
        if (self.id is None) != (self.kind in ("capabilities", "session", "runs")):
            raise ValueError("capabilities / session / runs 不带编号，其他种类必须带")
        if self.kind == "review":
            pending = list(self.source.review.pending_confirmations)  # type: ignore[union-attr]
            if [a.ack_id for a in self.acknowledgements] != pending:
                raise ValueError("知悉项说明必须与审阅的 pending_confirmations 一一对应")
        elif self.acknowledgements:
            raise ValueError("只有审阅带知悉项说明")
        pairs = [(s.answer, s.state) for s in self.answer_states]
        if len(pairs) != len(set(pairs)):
            raise ValueError("同一种作答状态只能出现一次")
        if self.answer_states and self.kind not in ("capabilities", "draft", "review"):
            raise ValueError("只有能力清单、草稿与审阅带作答状态")
        return self


class GeographyNames(ProductModel):
    official: str = Field(min_length=1, max_length=200)
    short: str = Field(min_length=1, max_length=80)


class LocalizedNames(ProductModel):
    zh: GeographyNames
    en: GeographyNames | None = None


class GeographyAnchor(ProductModel):
    """城市的显示锚点：都会区的代表点，不是住宅、主城市中心或都会区边界。经纬度是具名字段（度），坐标系写明。"""

    latitude: Annotated[float, Field(ge=-90, le=90)]
    longitude: Annotated[float, Field(ge=-180, le=180)]
    crs: Literal["EPSG:4326"]
    kind: Literal["principal_city_point", "metro_internal_point"]
    source_refs: tuple[str, ...] = Field(min_length=1)


class SupportScope(ProductModel):
    """模拟实际支持的居住与工作范围（来自资料包），与都会区本身、资料覆盖范围不一定相同。"""

    ref: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    residence: LocalizedText
    work: LocalizedText
    note: LocalizedText


class CityGeography(ProductModel):
    city_ref: RefStr
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
    scheme: str = Field(min_length=1, max_length=40)
    code: str = Field(min_length=1, max_length=40)
    geography_version: str = Field(min_length=1, max_length=80)
    country_names: LocalizedText
    names: LocalizedNames
    model_kind: Literal["real", "synthetic"]
    anchor_status: Literal["available", "unknown", "not_applicable"]
    anchor: GeographyAnchor | None = None
    support_scope: SupportScope
    coverage_note: LocalizedText

    @model_validator(mode="after")
    def _anchor(self) -> CityGeography:
        if (self.anchor_status == "available") != (self.anchor is not None):
            raise ValueError("只有 available 带锚点")
        if self.model_kind == "synthetic" and self.anchor_status != "not_applicable":
            raise ValueError("合成城市没有现实位置")
        return self


class GeographySource(ProductModel):
    source_ref: str = Field(min_length=1, max_length=120)
    publisher: str = Field(min_length=1, max_length=200)
    dataset: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=80)
    public_url: str = Field(min_length=1, max_length=400)
    content_sha256: Sha256 | None = None


class GeographyDisplayBundle(ProductModel):
    """一个资料包、一个显示资源版本的地理显示清单（``GET /api/v1/display/geography``）。地图底图、投影、纹理与标签由页面负责；
    这份清单不授予运行资格（能选哪些城市仍由能力清单与服务规则决定），缺少可靠坐标的城市锚点为空、仍可从列表访问。"""

    display_schema_version: Literal["e3-display-v2"]
    resource_version: str
    pack_fingerprint: Sha256
    status: DisplayStatus
    cities: tuple[CityGeography, ...] = ()
    sources: tuple[GeographySource, ...] = ()
    notes: tuple[LocalizedText, ...] = ()


#: 消息名 → 类。``scripts/product_e3a.py export-schema`` 按这份清单导出，``check-schema`` 按它核对。
PRODUCT_MESSAGES: dict[str, type[ProductModel]] = {
    "ProductCapabilities": ProductCapabilities,
    "UserInputDraft": UserInputDraft,
    "ProfileReview": ProfileReview,
    "ConfirmedProfile": ConfirmedProfile,
    "ProductPreflight": ProductPreflight,
    "RunSubmission": RunSubmission,
    "RunReceipt": RunReceipt,
    "RunProgress": RunProgress,
    "ProductResult": ProductResult,
    "SourceFact": SourceFact,
    "OpportunityCandidate": OpportunityCandidate,
    "CatalogCompilation": CatalogCompilation,
    "OptionInventoryCoverage": OptionInventoryCoverage,
    "LifeItemCompilation": LifeItemCompilation,
    # 托管服务（E3C，v3.2）
    "SessionExchangeRequest": SessionExchangeRequest,
    "SessionView": SessionView,
    "DraftView": DraftView,
    "DraftSaveRequest": DraftSaveRequest,
    "ReviewReply": ReviewReply,
    "ProfileConfirmRequest": ProfileConfirmRequest,
    "ProfileConfirmReply": ProfileConfirmReply,
    "RunIntentCreateRequest": RunIntentCreateRequest,
    "RunIntentView": RunIntentView,
    # v3.5 / API v1.4：一次开始的设置预览（DECISIONS/0048）
    "RunStartPreviewRequest": RunStartPreviewRequest,
    "RunStartPreview": RunStartPreview,
    "RunSubmitRequest": RunSubmitRequest,
    "RunSubmitReply": RunSubmitReply,
    "RunList": RunList,
    "ConfirmedProfileView": ConfirmedProfileView,
    "ProfileList": ProfileList,
    "DisplayEnvelope": DisplayEnvelope,
    "DisplayAttachment": DisplayAttachment,
    "GeographyDisplayBundle": GeographyDisplayBundle,
    "DeletionReceipt": DeletionReceipt,
    "ServiceError": ServiceError,
}


def internal_product_models() -> tuple[type[ProductModel], ...]:
    """产品消息整体都不能进代理观察或规划请求（``agentlayer.life_views`` 的扫描登记它们）：给分身看的只有 E1 的逐字段投影。"""
    return (
        *PRODUCT_MESSAGES.values(),
        ConfirmedOccupation,
        OccupationMapping,
        ConfirmedMoney,
        RetainedStatement,
    )
