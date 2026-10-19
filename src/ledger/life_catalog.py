"""生活目录的加载与交叉校验（E1-1 `DECISIONS/0016`；E1-2 `DECISIONS/0017`）。

单个对象自己能检查的（数值范围、未知字段、期限、口径一致）在
``common.life_contracts`` 里做；**跨对象**才看得出来的错误在这里做：引用不存在、
人和城对不上、账户口径与所在城市不符、行动的安排超出机会允许的选项、年份超出协议
的 N、计划的时间字段与机会种类不符、住房与食品没有每月恰好一项。

**这里不结算、不评分、不定价。** 加载器只回答"这批输入在结构上是否自洽"，以及
"能不能交给结算"。月度执行与记账在 ``econ.life_settlement``，它在动账之前调用本模块
的检查函数：结算前就能发现的问题一律在这里报，**不留半本账**。

两类问题分开报：

* ``errors``：无论打不打算结算都是错的（引用不存在、口径不符、计划时间字段错）。
* ``settlement_problems``：缺了结算所需的数据（最低住房/食品声明、税配置），或用了
  本轮结算不支持的计费语义。E1-1 的样例没有这些声明，结构仍然有效，只是不能结算。

**没有国家条件分支。** 第二个国家走同一条路径：国家身份与计价口径都是数据，
加载器不认识任何一个国家的名字（有测试盯着）。
"""

from __future__ import annotations

import re
from collections import Counter
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError, model_validator

from common.life_aggregation_contracts import RESERVED_AGGREGATION_KEYS
from common.life_billing import monthly_charges, one_time_total_minor
from common.life_contracts import (
    CAREER_REQUIREMENT_KINDS,
    HEALTH_STATUS_LABEL,
    LEGACY_REQUIREMENT_KINDS,
    MINIMUM_NEEDS,
    MONTHS_PER_YEAR,
    RESERVED_HEALTH_KEYS,
    SEVERITIES,
    ActionItem,
    CityLifeCatalog,
    ContractModel,
    FunctionKind,
    HealthInputDeclaration,
    IndependentHealthInputDeclaration,
    InternalPreferenceTruth,
    ItemRole,
    LifeContractError,
    LifeOpportunity,
    LifeProtocol,
    MinimumNeedSpec,
    PersonYearState,
    SavingsGoal,
    SavingsGoalDirective,
    SettlementProblem,
    StatedLifeProfile,
    SyntheticTaxPolicy,
    YearAction,
    energy_quota_problems,
    exact_minor_units,
    has_decimals,
    health_rules_state_problems,
    parse_eligibility_tag,
    qualification_gaps,
    savings_goal_kind,
)
from common.life_evaluation_contracts import RESERVED_EVALUATION_KEYS
from common.life_product_profile import ProductLifeProfile
from common.life_scoring_contracts import RESERVED_SCORING_KEYS, serialized_internal

_NEED_LABEL = {"housing": "住房", "food": "食品"}
#: 按月执行的行动：给 start_month 与 end_month。
PERIODIC_ROLES: frozenset[str] = frozenset(
    {"housing", "food", "job", "periodic_service", "training"}
)
#: 一次性行动：给 month。
ONE_TIME_ROLES: frozenset[str] = frozenset({"durable_good", "experience"})


class LifeBundle(ContractModel):
    """一份完整的生活输入：协议、需求与类别的声明、城市目录、人、初始状态、计划。

    陈述（``stated_profiles``）与真值（``internal_truths``）是两个独立字段。视图层只
    接 ``StatedLifeProfile``；真值不会因为"在同一个文件里"就靠近 prompt，结算也不读它。

    E3-A（DECISIONS/0034）：``product_profiles`` 是用户确认的六维产品画像（``ProductLifeProfile``），独立健康回答，**没有**
    研究真值——产品陈述不是人的完整心理真值，也不伪造一份。同一个人只能有一种画像（旧七维陈述 + 真值，或六维产品画像）。

    ``example_actions`` 是 E1-1 的结构样例（不结算）；``plans`` 是 E1-2 可结算的年度计划，
    同一分身可以有多年，每年至多一份。
    """

    protocol: LifeProtocol
    consumption_needs: tuple[str, ...]
    categories: tuple[str, ...]
    #: E1-3 增补：需求功能与方案属性的词表。机会只能用这里声明过的功能与属性键。
    functions: tuple[str, ...] = ()
    attribute_keys: tuple[str, ...] = ()
    #: E1-3 增补订正：需求功能的数量语义（存在型 / 容量型），在词表里声明一次。评分来源按功能
    #: 过滤时必须与这里一致——不能把"通信"换个说法当容量来按件计分。
    function_kinds: dict[str, FunctionKind] = Field(default_factory=dict)
    catalogs: tuple[CityLifeCatalog, ...]
    stated_profiles: tuple[StatedLifeProfile, ...]
    internal_truths: tuple[InternalPreferenceTruth, ...]
    initial_states: tuple[PersonYearState, ...]
    #: E3-A：六维产品画像。没有用到时不序列化——旧输入的输出逐字节不变。
    product_profiles: tuple[ProductLifeProfile, ...] = Field(
        default=(), exclude_if=lambda v: v == ()
    )
    example_actions: tuple[YearAction, ...] = ()
    tax_policies: tuple[SyntheticTaxPolicy, ...] = ()
    plans: tuple[YearAction, ...] = ()
    fixture_notice: str = Field(min_length=1)

    @model_validator(mode="after")
    def _declarations_unique(self) -> LifeBundle:
        for name in ("consumption_needs", "categories", "functions", "attribute_keys"):
            seq = getattr(self, name)
            dup = sorted(k for k, n in Counter(seq).items() if n > 1)
            if dup:
                raise ValueError(f"{name} 重复声明 {dup}")
        stray = sorted(set(self.function_kinds) - set(self.functions))
        if stray:
            raise ValueError(f"function_kinds 里的 {stray} 没有在 functions 里声明")
        dup = sorted(k for k, n in Counter(t.policy_id for t in self.tax_policies).items() if n > 1)
        if dup:
            raise ValueError(f"税策略 ID 重复 {dup}")
        keys = Counter((p.clone_id, p.year_index) for p in self.plans)
        dup = sorted(f"{c}@{y}" for (c, y), n in keys.items() if n > 1)
        if dup:
            raise ValueError(f"同一分身同一年只能有一份计划：{dup}")
        return self

    def catalog_for(self, city_key: str) -> CityLifeCatalog | None:
        return next((c for c in self.catalogs if str(c.city) == city_key), None)

    def tax_policy(self, policy_id: str | None) -> SyntheticTaxPolicy | None:
        return next((t for t in self.tax_policies if t.policy_id == policy_id), None)

    def plans_for(self, clone_id: str) -> tuple[YearAction, ...]:
        return tuple(sorted((p for p in self.plans if p.clone_id == clone_id), key=_year_of))

    def initial_state(self, clone_id: str) -> PersonYearState | None:
        return next((s for s in self.initial_states if s.clone_id == clone_id), None)

    def profile_for(self, person_id: str) -> StatedLifeProfile | ProductLifeProfile | None:
        """这个人的产品侧画像：旧七维陈述或六维产品画像（E3-A）。研究真值不在这里返回。"""
        found = [p for p in self.stated_profiles if p.person_id == person_id]
        found += [p for p in self.product_profiles if p.person_id == person_id]
        return found[0] if len(found) == 1 else None


def _year_of(p: YearAction) -> int:
    return p.year_index


class ContractReport(ContractModel):
    """结构验证的结果。**不含任何城市得分或排序。**"""

    contract_valid: bool
    scoring_ready: bool
    settlement_ready: bool
    protocol_status: str
    undecided_policies: tuple[str, ...]
    errors: tuple[str, ...]
    settlement_problems: tuple[str, ...]
    notes: tuple[str, ...]
    summary: dict[str, Any]


# ==========================================================================
# 角色、资格词表与时间
# ==========================================================================


def item_role(catalog: CityLifeCatalog, o: LifeOpportunity) -> ItemRole:
    """这项机会在结算里扮演什么角色。

    住房与食品**由数据认定**：在城市最低生活声明的合格名单里才算。不看类别名、
    标签或价格——"category: housing" 不等于它满足最低住房。
    """
    for need in MINIMUM_NEEDS:
        spec = catalog.minimum_spec(need)
        if spec is not None and o.opportunity_id in spec.qualifying_opportunity_ids:
            return need  # type: ignore[return-value]
    if o.occupation is not None:
        return "job"
    kind = o.duration.kind
    if kind == "training_course":
        return "training"
    if kind == "durable_good":
        return "durable_good"
    if kind == "one_time_experience":
        return "experience"
    if o.price is not None and o.price.billing == "per_year":
        return "prepaid_service"
    return "periodic_service"


_ELIG_OCCUPATION = re.compile(r"^occupation:([A-Za-z0-9]+):([A-Za-z0-9.\-]+)$")
_ELIG_SKILL = re.compile(r"^skill:([a-z][a-z0-9_]{0,47})>=(\d+(?:\.\d+)?)$")
_ELIG_TRAINING = re.compile(r"^training_completed:([a-z0-9][a-z0-9_.\-]{0,63})$")


def parse_eligibility(tag: str) -> tuple[str, str, str] | None:
    """本轮支持的资格词表。词表外的标签返回 None——由调用方报数据错误，**不猜**。

    * ``occupation:<体系>:<代码>``：当前职业等于它
    * ``skill:<名>>=<数>``：技能水平不低于它
    * ``training_completed:<机会 ID>``：那门培训已完成
    """
    if m := _ELIG_OCCUPATION.match(tag):
        return ("occupation", m.group(1), m.group(2))
    if m := _ELIG_SKILL.match(tag):
        return ("skill", m.group(1), m.group(2))
    if m := _ELIG_TRAINING.match(tag):
        return ("training_completed", m.group(1), "")
    return None


def hours_for_months(o: LifeOpportunity, months: int, quantity: float = 1) -> Fraction:
    """一项按年计的时间成本在 ``months`` 个月里占多少小时：``h × months / 12 × 数量``。

    用分数精确计算，比较时不需要容差。
    """
    return Fraction(Decimal(repr(o.time_cost_hours_per_year))) * months * _q(quantity) / 12


def event_hours(o: LifeOpportunity, quantity: float = 1) -> Fraction:
    """一次体验的时间成本：发生一次就整笔计入，不按月摊。"""
    return Fraction(Decimal(repr(o.time_cost_hours_per_year))) * _q(quantity)


def _q(quantity: float) -> Fraction:
    return Fraction(Decimal(repr(float(quantity))))


# ==========================================================================
# 月度资源资料（E1-4A，DECISIONS/0020）：完整性与一致性。算法在 econ.life_resources。
# ==========================================================================

#: 每种结算角色允许的资源计量依据。
RESOURCE_BASIS_BY_ROLE: dict[str, frozenset[str]] = {
    "housing": frozenset({"passive"}),
    "food": frozenset({"per_service_month"}),
    "job": frozenset({"job"}),
    "periodic_service": frozenset({"per_service_month", "per_use"}),
    "prepaid_service": frozenset({"per_service_month", "per_use"}),
    "durable_good": frozenset({"passive"}),
    "experience": frozenset({"per_occurrence"}),
    "training": frozenset({"per_service_month"}),
}
#: 可以替代家务的角色（被动物品、住房、服务）。
SUBSTITUTE_ROLES: frozenset[str] = frozenset(
    {"housing", "durable_good", "periodic_service", "prepaid_service"}
)


def resource_catalog_problems(protocol: LifeProtocol, catalog: CityLifeCatalog) -> list[str]:
    """一座城的资源资料是否齐全、一致（按月核算的协议）。缺了、尺度不对、自相矛盾都是资料错误。

    旧规则的协议里出现资源事实也是错误：旧规则不读它，悄悄忽略会让人以为已经约束了。
    """
    rules = protocol.resource_rules
    out: list[str] = []
    if rules is None:
        stray = [o.opportunity_id for o in catalog.opportunities if o.resource is not None]
        if stray:
            out.append(
                f"{catalog.city}: 生活规则 {protocol.life_rules_version} 不读资源事实，"
                f"却有 {stray[:5]}——要按月核算请用 E1-4A 规则版本"
            )
        return out
    d = rules.input_decimals
    for o in catalog.opportunities:
        where = f"{catalog.city}/{o.opportunity_id}"
        if o.time_cost_hours_per_year != 0 or o.energy_burden != 0:
            out.append(
                f"{where}: 按月核算的协议不再使用 time_cost_hours_per_year / energy_burden"
                "（含义没冻结，不能当成月量或一次量）；请写在 resource 里，旧字段保持 0"
            )
        r = o.resource
        if r is None:
            out.append(f"{where}: 缺资源事实 resource——零也要显式写 0，缺了不当零")
            continue
        role = item_role(catalog, o)
        allowed = RESOURCE_BASIS_BY_ROLE.get(role, frozenset())
        if r.basis not in allowed:
            out.append(
                f"{where}: 结算角色 {role} 的计量依据只能是 {sorted(allowed)}，得到 {r.basis}"
            )
        numbers = [("hours", r.hours), ("burden_per_hour", r.burden_per_hour)]
        if r.job is not None:
            numbers += [
                ("work_hours_per_month", r.job.work_hours_per_month),
                ("burden_per_work_hour", r.job.burden_per_work_hour),
            ]
        numbers += [(f"{x.task}.residual", x.residual_hours_per_month) for x in r.substitutes]
        for name, x in numbers:
            if x is not None and not has_decimals(x, d):
                out.append(f"{where}: {name} = {x!r} 的精度细于 {d} 位小数")
        if r.substitutes and role not in SUBSTITUTE_ROLES:
            out.append(f"{where}: {role} 不能替代家务（只有住房、物品与服务可以）")
        for sub in r.substitutes:
            task = rules.task(sub.task)
            if task is None:
                out.append(f"{where}: 替代的家务 {sub.task!r} 没有在协议的 household_tasks 里声明")
            elif sub.residual_hours_per_month > task.baseline_hours_per_month:
                out.append(
                    f"{where}: {sub.task} 替代后的残余 {sub.residual_hours_per_month} 小时多于基准 "
                    f"{task.baseline_hours_per_month} 小时——替代不能让家务变多"
                )
        if role == "job":
            if o.arrangement_options:
                out.append(
                    f"{where}: 岗位的工时与到岗是岗位方案的一部分，不能列可选安排 "
                    f"{sorted(o.arrangement_options)}"
                )
            mode = o.spec.get("work_mode")
            if r.job is not None and mode is not None and mode != r.job.work_mode:
                out.append(
                    f"{where}: 规格 work_mode = {mode!r} 与岗位方案 {r.job.work_mode} 不一致"
                )
        if role == "housing" and o.availability == "available":
            v = o.spec.get(rules.commute_minutes_key)
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                out.append(
                    f"{where}: 住房必须写整数的 {rules.commute_minutes_key}（往返分钟，非负）——"
                    "缺了不能补成零"
                )
    return out


def resource_state_problems(protocol: LifeProtocol, state: PersonYearState) -> list[str]:
    """一个人的资源预算是否与协议的资源规则配套（尺度、版本、精度）。"""
    rules = protocol.resource_rules
    who = state.clone_id
    if rules is None:
        if state.resource_budget_basis is not None:
            return [f"{who}: 生活规则 {protocol.life_rules_version} 不读月度资源预算"]
        return []
    out: list[str] = []
    basis = state.resource_budget_basis
    if basis is None:
        out.append(f"{who}: 按月核算的协议必须写资源预算依据（预留了什么、额度从哪来）")
    elif basis.rules_version != rules.version:
        out.append(
            f"{who}: 资源预算依据的规则版本 {basis.rules_version} 与协议 {rules.version} 不一致"
        )
    if state.energy.scale_version != rules.energy_scale_version:
        out.append(
            f"{who}: 精力尺度 {state.energy.scale_version} 与协议的 {rules.energy_scale_version} 不一致"
            "——尺度不同的数值不能比较，也不能当零"
        )
    for name, x in (
        ("time_budget_hours", state.time_budget_hours),
        ("energy.level", state.energy.level),
    ):
        if not has_decimals(x, rules.input_decimals):
            out.append(f"{who}: {name} = {x!r} 的精度细于 {rules.input_decimals} 位小数")
    q = state.energy_quota_basis
    if protocol.energy_feedback is None:
        if q is not None:
            out.append(
                f"{who}: 生活规则 {protocol.life_rules_version} 没有满足感反馈，不读精力额度依据——"
                "要启用 E1-4B 请声明对应的规则版本与反馈政策"
            )
    elif q is None:
        out.append(
            f"{who}: E1-4B 协议必须写精力额度依据（固定的个人基础额度、当前额度的来由）；缺了不当成基础"
        )
    elif not has_decimals(q.base_level, rules.input_decimals):
        out.append(f"{who}: 基础额度 {q.base_level!r} 的精度细于 {rules.input_decimals} 位小数")
    return out


def feedback_state_problems(
    protocol: LifeProtocol, state: PersonYearState
) -> list[SettlementProblem]:
    """E1-4B：一个年初状态的精力额度能不能直接拿来结算（DECISIONS/0021；r2 第十三节）。

    判断本身在 ``common.life_contracts.energy_quota_problems``——加载、结算前检查与可见投影共用那一份：
    候选状态（额度待定）不能结算；额度只能在确定它的那份政策（编号、版本与**内容指纹**都相同）下继续；
    关闭反馈或 α = 0 时额度只能是基础值；不超出 ``基础 × (1 + α)``。这里只把结果包成结算问题（输入错误）。
    """
    return [
        _p(code, message)
        for code, message in energy_quota_problems(protocol.energy_feedback, state)
    ]


# ==========================================================================
# 结算所需的数据：最低生活、计费语义、税
# ==========================================================================


def minimum_living_problems(catalog: CityLifeCatalog) -> list[str]:
    """一座城的最低住房与食品声明是否完整可用。结算与加载器共用这一份检查。

    缺失、引用错误、不完整都是**数据错误**，绝不会被当成分身出局。
    """
    city = catalog.city
    out: list[str] = []
    for need in MINIMUM_NEEDS:
        label = _NEED_LABEL[need]
        spec = catalog.minimum_spec(need)
        if spec is None:
            out.append(f"{city}: 没有声明最低{label}方案（minimum_living 缺 {need}）")
            continue
        if spec.baseline_quantity != 1:
            out.append(
                f"{city}: 最低{label}方案的数量是 {spec.baseline_quantity}；本轮只支持 1"
                "（一个居所 / 一份食品方案），多份额需要家庭规模的定义"
            )
        for oid in spec.qualifying_opportunity_ids:
            o = catalog.opportunity(oid)
            if o is None:
                out.append(f"{city}: 最低{label}的合格项 {oid} 在目录里不存在")
            elif o.duration.kind != "ongoing_service" or o.occupation is not None:
                out.append(f"{city}: 最低{label}的合格项 {oid} 必须是持续服务")
            elif o.price is not None and o.price.billing not in ("per_month", "per_week"):
                out.append(
                    f"{city}: 最低{label}的合格项 {oid} 按 {o.price.billing} 计费；"
                    "本轮只支持按月或按周计费（每月检查需要确定的月费）"
                )
        base = catalog.opportunity(spec.baseline_opportunity_id)
        if base is not None:
            if base.availability != "available" or base.price is None:
                out.append(
                    f"{city}: 最低{label}方案 {base.opportunity_id} 必须可得且有报价"
                    f"（现在是 {base.availability}）"
                )
            if base.eligibility:
                out.append(f"{city}: 最低{label}方案 {base.opportunity_id} 不能带资格限制")
            out.extend(_cheaper_qualifying_options(catalog, label, base, spec))
    housing, food = catalog.minimum_spec("housing"), catalog.minimum_spec("food")
    if housing is not None and food is not None:
        both = sorted(
            set(housing.qualifying_opportunity_ids) & set(food.qualifying_opportunity_ids)
        )
        if both:
            out.append(f"{city}: {both} 同时出现在住房与食品的合格名单里")
    return out


def _monthly_charges_or_none(o: LifeOpportunity, digits: int) -> tuple[int, ...] | None:
    if o.price is None or o.price.billing not in ("per_month", "per_week"):
        return None  # 不可得、未知、或计费不受支持——后者在上面另报
    return monthly_charges(o.price, digits)


def _cheaper_qualifying_options(
    catalog: CityLifeCatalog, label: str, base: LifeOpportunity, spec: MinimumNeedSpec
) -> list[str]:
    """最低方案的声明不能与目录里**已知可得**、满足同一最低需求的方案相矛盾（E1-2 收尾）。

    比较口径：数量都是 1（最低方案数量只支持 1，住房/食品计划项的数量也必须是 1）、
    同一个日历月、同一套月度计费换算（``common.life_billing``，与结算记账是同一份）。
    只要某个月别的合格项比声明的最低方案**更便宜**，最低检查就会把本来过得下去的分身
    判出局——这是数据自相矛盾，报数据错误，**不**替分身去找更便宜的方案。

    更便宜的合格项若带资格限制，就是"只对部分人开放的优惠"，本轮的单一最低方案表达
    不了"因人而异的最低"，明确拒绝这种配置。不可得、未知（没有报价）的项不参与比较。
    """
    digits = catalog.money_basis.minor_unit_digits
    base_charges = _monthly_charges_or_none(base, digits)
    if base_charges is None:
        return []
    out: list[str] = []
    for oid in spec.qualifying_opportunity_ids:
        o = catalog.opportunity(oid)
        if o is None or oid == base.opportunity_id or o.availability != "available":
            continue
        charges = _monthly_charges_or_none(o, digits)
        if charges is None:
            continue
        cheaper = [
            m for m, (c, b) in enumerate(zip(charges, base_charges, strict=True), 1) if c < b
        ]
        if not cheaper:
            continue
        m = cheaper[0]
        what = (
            f"{catalog.city}: 最低{label}方案 {base.opportunity_id} 第 {m} 月收 "
            f"{_fmt_minor(base_charges[m - 1], digits)}，但同样合格、可得的 {oid} 在第 {cheaper} 月"
            f"更便宜（第 {m} 月 {_fmt_minor(charges[m - 1], digits)}）"
        )
        if o.eligibility:
            out.append(
                f"{what}，且它带资格限制 {list(o.eligibility)}——这是只对部分人开放的优惠，"
                "本轮单一的最低方案表达不了'因人而异的最低'，不支持这种配置"
            )
        else:
            out.append(f"{what}：最低方案声明与合格目录矛盾")
    return out


def _fmt_minor(minor: int, digits: int) -> str:
    return f"{Decimal(minor).scaleb(-digits):,.{digits}f}"


def catalog_settlement_problems(
    catalog: CityLifeCatalog, protocol: LifeProtocol | None = None
) -> list[str]:
    """这座城能不能交给本轮结算。包括最低生活、税配置是否存在、计费语义是否受支持。"""
    city = catalog.city
    out = minimum_living_problems(catalog)
    if catalog.tax_policy_id is None:
        out.append(f"{city}: 没有税配置（tax_policy_id）；没有配置不等于零税")
    for o in catalog.opportunities:
        oid = o.opportunity_id
        kind = o.duration.kind
        if o.duration.effective_from == "next_year" and kind != "durable_good":
            out.append(f"{city}/{oid}: next_year 生效只对持续物品有定义（下一年第 1 月起服务）")
        if (
            o.price is not None
            and kind in ("durable_good", "one_time_experience", "training_course")
            and o.price.billing != "one_time"
        ):
            out.append(
                f"{city}/{oid}: {kind} 按 {o.price.billing} 计费；本轮只支持一次性付款"
                "（不把买断自动拆成分期）"
            )
        if o.occupation is not None and o.price is not None:
            out.append(f"{city}/{oid}: 带职业的机会按工作结算，不能有价格")
        if o.compensation is not None and o.compensation.billing == "one_time":
            out.append(f"{city}/{oid}: 一次性报酬不是工作；本轮不支持")
        career = protocol is not None and protocol.career_rules is not None
        allowed = CAREER_REQUIREMENT_KINDS if career else LEGACY_REQUIREMENT_KINDS
        for tag in o.eligibility:
            parsed = parse_eligibility_tag(tag)
            if parsed is None or parsed[0] not in allowed:
                if parsed is not None and career:
                    out.append(
                        f"{city}/{oid}: 资格标签 {tag!r} 是旧写法；E1-5A 只认 ability / occupation / employer"
                        "——旧写法会绕过下一年生效规则"
                    )
                elif parsed is not None:
                    out.append(
                        f"{city}/{oid}: 资格标签 {tag!r} 是 E1-5A 的写法；生活规则 "
                        f"{protocol.life_rules_version if protocol else '（旧）'} 不读它"
                    )
                else:
                    out.append(f"{city}/{oid}: 资格标签 {tag!r} 不在本轮词表里")
    return out


# ==========================================================================
# E1-5A 学习、能力与岗位（DECISIONS/0023）：引用、作用域与学习资源的一致性。执行在 econ。
# ==========================================================================


def career_catalog_problems(protocol: LifeProtocol, catalog: CityLifeCatalog) -> list[str]:
    """一座城的雇主、学习成果与资格要求是否自洽。加载器与结算前检查共用。

    旧协议出现雇主或学习成果字段也是错误：旧协议不读它们，悄悄忽略会让人以为学习有了回报。
    """
    rules = protocol.career_rules
    city = catalog.city
    out: list[str] = []
    if rules is None:
        stray = [o.opportunity_id for o in catalog.opportunities if o.employer_id or o.grants]
        if stray:
            out.append(
                f"{city}: 生活规则 {protocol.life_rules_version} 不读雇主与学习成果（employer_id / grants），"
                f"却有 {stray[:5]}——要启用学习与岗位变化请用 E1-5A 规则版本"
            )
        return out
    for o in catalog.opportunities:
        oid = o.opportunity_id
        where = f"{city}/{oid}"
        if o.targets:
            out.append(
                f"{where}: E1-5A 用 grants 声明学习成果；targets 是旧协议的说明标签，两者不并存"
            )
        if o.employer_id is not None:
            employer = rules.employer(o.employer_id)
            if employer is None:
                out.append(f"{where}: 雇主 {o.employer_id} 没有在 career_rules.employers 里声明")
            elif str(employer.city) != str(city):
                out.append(f"{where}: 雇主 {o.employer_id} 属于 {employer.city}，不是这座城")
        if (
            o.employer_id is not None
            and o.duration.kind != "training_course"
            and o.compensation is None
        ):
            out.append(f"{where}: 只有岗位与培训可以写雇主")
        for g in o.grants:
            ability = rules.ability(g)
            if ability is None:
                out.append(f"{where}: 授予了没有声明的能力 {g}")
            elif ability.scope.kind == "employer" and ability.scope.employer_id != o.employer_id:
                out.append(
                    f"{where}: {g} 是雇主 {ability.scope.employer_id} 的内部知识，只能由这家雇主的培训授予"
                )
        if o.duration.kind == "training_course":
            r = o.resource
            if (
                r is None
                or r.basis != "per_service_month"
                or not r.hours
                or r.hours <= 0
                or not r.burden_per_hour
                or r.burden_per_hour <= 0
            ):
                out.append(
                    f"{where}: 学习必须按实际参加的月份占用正的时间与负担（per_service_month，hours > 0，"
                    "burden_per_hour > 0）；零价的自主学习也一样，不能免费增加能力"
                )
        for tag in o.eligibility:
            parsed = parse_eligibility_tag(tag)
            if parsed is None:
                continue  # catalog_settlement_problems 报
            kind, a, _ = parsed
            if kind == "ability":
                ability = rules.ability(a)
                if ability is None:
                    out.append(f"{where}: 要求了没有声明的能力 {a}")
                elif (
                    ability.scope.kind == "employer" and ability.scope.employer_id != o.employer_id
                ):
                    out.append(
                        f"{where}: 要求 {a}（只用于雇主 {ability.scope.employer_id}），但这项机会属于 "
                        f"{o.employer_id or '（没有写雇主）'}——作用域冲突，不能当作满足或不满足来猜"
                    )
                elif ability.scope.kind == "occupation":
                    occ = ability.scope.occupation
                    assert occ is not None
                    if o.occupation is None or (o.occupation.scheme, o.occupation.code) != (
                        occ.scheme,
                        occ.code,
                    ):
                        out.append(
                            f"{where}: 要求 {a}（只用于职业 {occ.scheme}:{occ.code}），但这项机会的职业是 "
                            f"{o.occupation}——作用域冲突"
                        )
            elif kind == "employer" and rules.employer(a) is None:
                out.append(f"{where}: 要求在没有声明的雇主 {a} 工作")
    return out


def career_state_problems(
    protocol: LifeProtocol, state: PersonYearState, catalog: CityLifeCatalog
) -> list[str]:
    """一个状态的能力、岗位记录与学习进度是否与协议、目录一致（资料错误）。加载器与结算前检查共用。"""
    rules = protocol.career_rules
    who = state.clone_id
    if rules is None:
        extra = (
            state.abilities
            or state.employment is not None
            or any(t.completed_year is not None for t in state.training)
        )
        if extra:
            return [
                f"{who}: 生活规则 {protocol.life_rules_version} 不读能力、岗位记录与完成年月——要启用请用 "
                "E1-5A 规则版本"
            ]
        return []
    out: list[str] = []
    if state.employment is None:
        out.append(
            f"{who}: E1-5A 的状态必须写岗位记录 employment（可以没有岗位）；缺了不猜他在哪工作"
        )
    if state.skills:
        out.append(f"{who}: E1-5A 不读数值 skills；能力写在 abilities（已获得 / 未获得）")
    held = {a.ability_id for a in state.abilities}
    for a in state.abilities:
        if rules.ability(a.ability_id) is None:
            out.append(f"{who}: 能力 {a.ability_id} 没有在 career_rules.abilities 里声明")
        if a.source == "training":
            o = catalog.opportunity(a.source_opportunity_id or "")
            if o is None or a.ability_id not in o.grants:
                out.append(
                    f"{who}: 能力 {a.ability_id} 的来源课程 {a.source_opportunity_id} 没有声明授予它"
                )
    for tr in state.training:
        o = catalog.opportunity(tr.opportunity_id)
        if o is None or not tr.completed:
            continue
        if tr.completed_year is None:
            out.append(f"{who}: 完成的课程 {tr.opportunity_id} 没有完成年月——不猜学习成果")
        missing = sorted(set(o.grants) - held)
        if missing:
            out.append(
                f"{who}: 课程 {tr.opportunity_id} 已完成，却没有它授予的能力 {missing}——不猜学习成果"
            )
    if state.employment is not None:
        for pos in state.employment.positions:
            o = catalog.opportunity(pos.opportunity_id)
            if o is None or o.compensation is None:
                out.append(f"{who}: 岗位记录里的 {pos.opportunity_id} 不是本城的岗位")
                continue
            if o.occupation != pos.occupation or o.employer_id != pos.employer_id:
                out.append(f"{who}: 岗位记录 {pos.opportunity_id} 的职业或雇主与目录不一致")
    return out


# ==========================================================================
# E1-5B：资金目标的状态与计划检查（DECISIONS/0026）。加载与结算前检查共用这一份；
# 计划上的问题都是输入错误（整份计划退回，E1-7 可修改重试），不是生活出局。
# ==========================================================================


def savings_state_problems(
    protocol: LifeProtocol, state: PersonYearState, catalog: CityLifeCatalog
) -> list[str]:
    """状态里的资金目标与协议、城市目录是否一致。"""
    rules = protocol.savings_rules
    who = state.clone_id
    goals = state.savings_goals
    if rules is None:
        if goals is not None:
            return [
                f"{who}: 协议 {protocol.protocol_id}（生活规则 {protocol.life_rules_version}）不支持结构化资金"
                "目标，状态却写了 savings_goals——旧协议不静默启用新行为；文字目标照旧写在 goals 里"
            ]
        return []
    if goals is None:
        return [
            f"{who}: 生活规则 {protocol.life_rules_version} 的状态必须写 savings_goals（还没有目标就写空列表）"
            "——缺了不猜他有没有目标"
        ]
    out: list[str] = []
    n = protocol.simulation_years
    for g in goals:
        where = f"{who}/{g.goal_id}"
        o = catalog.opportunity(g.opportunity_id)
        if o is None:
            out.append(f"{where}: 目标机会 {g.opportunity_id} 在 {catalog.city} 不存在")
            continue
        kind = savings_goal_kind(o)
        if kind is not None and kind != g.kind:
            out.append(f"{where}: 目录里 {g.opportunity_id} 是 {kind}，目标写的是 {g.kind}")
        if g.kind not in rules.goal_kinds:
            out.append(f"{where}: 协议的目标规则不支持 {g.kind}")
        if g.target_year > n:
            out.append(f"{where}: 目标年 {g.target_year} 超出协议的 N = {n}")
        if g.money_basis_id != catalog.money_basis.numeraire_id:
            out.append(
                f"{where}: 目标口径 {g.money_basis_id} 与城市口径 {catalog.money_basis.numeraire_id} 不一致"
                "——不同口径不比较，也不改标签"
            )
    return out


def goal_target_problems(
    protocol: LifeProtocol,
    catalog: CityLifeCatalog,
    opportunity_id: str,
    quantity: int,
    who: str,
) -> list[SettlementProblem]:
    """一个新目标指向的机会能不能当资金目标：只看目录（不看状态）。"""
    rules = protocol.savings_rules
    assert rules is not None
    o = catalog.opportunity(opportunity_id)
    if o is None:
        return [_p("goal_rejected", f"{who}: 目标机会 {opportunity_id} 在 {catalog.city} 不存在")]
    out: list[SettlementProblem] = []
    if o.availability == "unknown":
        out.append(
            _p(
                "goal_rejected",
                f"{who}: {opportunity_id} 的报价未知——未知价格不补零，不建资金目标",
            )
        )
        return out
    if o.availability == "unavailable":
        out.append(_p("goal_rejected", f"{who}: {opportunity_id} 在本城确定缺货，目标不成立"))
        return out
    kind = savings_goal_kind(o)
    if kind is None:
        billing = o.price.billing if o.price is not None else "没有报价"
        out.append(
            _p(
                "goal_rejected",
                f"{who}: 资金目标只能是一次性购买的耐用品或一门课的一次性学费；{opportunity_id} 是 "
                f"{o.duration.kind}（{billing}）",
            )
        )
        return out
    if kind not in rules.goal_kinds:
        out.append(_p("goal_rejected", f"{who}: 协议的目标规则不支持 {kind}"))
    if kind == "training_tuition" and quantity != 1:
        out.append(_p("goal_rejected", f"{who}: 学费目标的数量必须是 1，得到 {quantity}"))
    assert o.price is not None
    if o.price.amount.numeraire != catalog.money_basis.numeraire_id:
        out.append(
            _p(
                "goal_rejected",
                f"{who}: {opportunity_id} 的报价口径 {o.price.amount.numeraire} 与账户口径 "
                f"{catalog.money_basis.numeraire_id} 不一致——不同口径不比较，也不改标签",
            )
        )
        return out
    if exact_minor_units(o.price.amount.amount, catalog.money_basis.minor_unit_digits) is None:
        out.append(_p("goal_rejected", f"{who}: {opportunity_id} 的报价精度细于最小记账单位"))
        return out
    if one_time_total_minor(o.price, quantity, catalog.money_basis.minor_unit_digits) == 0:
        out.append(
            _p(
                "goal_rejected",
                f"{who}: {opportunity_id} 的报价是 0——没有要攒的钱，不建零金额的资金目标"
                "（零学费的课程照常可以在年度行动里报名）",
            )
        )
    return out


def _goal_purchasable(
    state: PersonYearState, catalog: CityLifeCatalog, opportunity_id: str, who: str
) -> list[SettlementProblem]:
    """按现有可购买性规则，这个目标能不能由一笔**新**交易兑现：已持有的耐用品不能再买，学费结清或学完的课程
    没有要付的学费——旧交易不能冒充新目标的成果。"""
    o = catalog.opportunity(opportunity_id)
    if o is None:
        return []
    out: list[SettlementProblem] = []
    if o.duration.kind == "durable_good" and any(
        h.opportunity_id == opportunity_id for h in state.holdings
    ):
        out.append(
            _p(
                "goal_rejected",
                f"{who}: 已持有 {opportunity_id}——按现行规则持有期间不能再买同一件物品，目标不成立；"
                "旧的那次购买也不能算作新目标的成果",
            )
        )
    tr = next((t for t in state.training if t.opportunity_id == opportunity_id), None)
    if o.duration.kind == "training_course" and tr is not None:
        if tr.completed:
            out.append(_p("goal_rejected", f"{who}: {opportunity_id} 已经学完，没有要攒的学费"))
        elif tr.fee_paid:
            out.append(
                _p(
                    "goal_rejected",
                    f"{who}: {opportunity_id} 的学费在第 {tr.enrolled_year} 年已经结清——继续上课不再收费，"
                    "没有要攒的学费；旧的付款也不能算作新目标的成果",
                )
            )
    return out


def _savings_plan_static(
    protocol: LifeProtocol, catalog: CityLifeCatalog, action: YearAction, who: str
) -> list[SettlementProblem]:
    """计划与目录就能判定的目标问题（不依赖当年状态，加载器对后续年份也做这一层）。"""
    d = action.savings_goal
    linked = [(i, it) for i, it in enumerate(action.items) if it.goal_id is not None]
    rules = protocol.savings_rules
    if rules is None:
        if d is not None or linked:
            return [
                _p(
                    "goal_not_supported",
                    f"{who}: 协议 {protocol.protocol_id}（生活规则 {protocol.life_rules_version}）不支持结构化资金"
                    "目标——旧协议不静默启用新行为；文字目标照旧写在状态的 goals 里",
                )
            ]
        return []
    out: list[SettlementProblem] = []
    n = protocol.simulation_years
    if d is not None and d.target_year is not None and not action.year_index <= d.target_year <= n:
        out.append(
            _p(
                "goal_rejected",
                f"{who}: 目标年 {d.target_year} 不在本年 {action.year_index} 到 N = {n} 之间——目标时点"
                "限定在当前模拟行动期，不预约终态以后的事",
            )
        )
    if d is not None and d.action in ("set", "replace"):
        assert d.opportunity_id is not None and d.quantity is not None
        out.extend(goal_target_problems(protocol, catalog, d.opportunity_id, d.quantity, who))
    for i, it in linked:
        o = catalog.opportunity(it.opportunity_id)
        if o is None:
            continue  # unknown_opportunity 已报
        where = f"{who}/{o.opportunity_id}"
        role = item_role(catalog, o)
        if role not in ("durable_good", "training"):
            out.append(
                _p(
                    "goal_link_invalid",
                    f"{where}: 只有一次性购买的耐用品或培训报名能兑现资金目标，这一项是 {role}",
                    item_order=i,
                )
            )
        if d is None:
            continue
        if d.action == "cancel":
            out.append(
                _p(
                    "goal_link_invalid",
                    f"{where}: 年初取消了目标 {d.goal_id}，这一年没有可兑现的目标；行动照常可以执行，但不写 goal_id",
                    item_order=i,
                )
            )
        elif it.goal_id != d.goal_id:
            out.append(
                _p(
                    "goal_link_invalid",
                    f"{where}: 关联的目标 {it.goal_id} 不是年初指令里的 {d.goal_id}",
                    item_order=i,
                )
            )
        elif d.action in ("set", "replace") and (it.opportunity_id, it.quantity) != (
            d.opportunity_id,
            d.quantity,
        ):
            out.append(
                _p(
                    "goal_link_invalid",
                    f"{where}: 关联行动的机会或数量（{it.opportunity_id} × {it.quantity:g}）与目标"
                    f"（{d.opportunity_id} × {d.quantity}）不一致",
                    item_order=i,
                )
            )
    return out


def _open_goal(state: PersonYearState) -> SavingsGoal | None:
    return next((g for g in state.savings_goals or () if g.open), None)


def _savings_plan_state(
    protocol: LifeProtocol,
    state: PersonYearState,
    action: YearAction,
    catalog: CityLifeCatalog,
    who: str,
) -> list[SettlementProblem]:
    """需要当年年初状态才能判定的目标问题：现有目标、ID 是否用过、按现有规则能否由新交易兑现、关联是否对得上。"""
    if protocol.savings_rules is None or state.savings_goals is None:
        return []
    out: list[SettlementProblem] = []
    current = _open_goal(state)
    used = {g.goal_id for g in state.savings_goals}
    d: SavingsGoalDirective | None = action.savings_goal
    # 年初指令之后的未结束目标：(ID, 机会, 数量)
    after: tuple[str, str, int] | None = (
        None if current is None else (current.goal_id, current.opportunity_id, current.quantity)
    )
    if d is not None:
        if d.action in ("set", "replace"):
            if d.action == "set" and current is not None:
                out.append(
                    _p(
                        "goal_rejected",
                        f"{who}: 已有未结束的目标 {current.goal_id}——同一时刻只能有一个；要换用 replace，"
                        "要放弃用 cancel",
                    )
                )
            if d.action == "replace" and (current is None or d.replaces != current.goal_id):
                have = "没有" if current is None else f"是 {current.goal_id}"
                out.append(
                    _p(
                        "goal_rejected",
                        f"{who}: replace 要替换的 {d.replaces} 不是现有的未结束目标（现有的{have}）",
                    )
                )
            if d.goal_id in used:
                out.append(
                    _p(
                        "goal_rejected",
                        f"{who}: 目标 ID {d.goal_id} 已经用过——目标 ID 全史唯一，旧记录不覆盖",
                    )
                )
            assert d.opportunity_id is not None and d.quantity is not None
            out.extend(_goal_purchasable(state, catalog, d.opportunity_id, who))
            after = (d.goal_id, d.opportunity_id, d.quantity)
        else:  # keep / cancel / reschedule
            if current is None or d.goal_id != current.goal_id:
                have = "没有" if current is None else f"是 {current.goal_id}"
                out.append(
                    _p(
                        "goal_rejected",
                        f"{who}: {d.action} 指向的 {d.goal_id} 不是现有的未结束目标（现有的{have}）",
                    )
                )
            if d.action == "cancel":
                after = None
    for i, it in enumerate(action.items):
        if it.goal_id is None or (d is not None and d.action in ("set", "replace", "cancel")):
            continue  # 这三种情况在计划与目录层面已经核对过
        where = f"{who}/{it.opportunity_id}"
        if after is None or it.goal_id != after[0]:
            have = "没有未结束的目标" if after is None else f"未结束的目标是 {after[0]}"
            out.append(
                _p(
                    "goal_link_invalid",
                    f"{where}: 关联的目标 {it.goal_id} 不存在或已结束（{have}）",
                    item_order=i,
                )
            )
        elif (it.opportunity_id, it.quantity) != (after[1], after[2]):
            out.append(
                _p(
                    "goal_link_invalid",
                    f"{where}: 关联行动的机会或数量（{it.opportunity_id} × {it.quantity:g}）与目标"
                    f"（{after[1]} × {after[2]}）不一致",
                    item_order=i,
                )
            )
    return out


# ==========================================================================
# E1-5C：疾病、应急与恢复（DECISIONS/0027）。治疗目录、健康状态与应急安排的检查；加载与结算前检查共用。
# 逐月的事件、治疗与恢复在 econ.life_health（这里不算概率、不判断疗效）。
# ==========================================================================


def participation_activity(catalog: CityLifeCatalog, o: LifeOpportunity) -> bool:
    """这项活动是不是"有参与时间的主动活动"：学习、一次体验、每月有小时数的服务或持有物（食品准备除外）。

    大病时世界规则暂停的就是它们；年度计划的应急暂停顺序也只能列它们。住房、食品、岗位与被动物品（洗衣机、
    零小时的服务）不是：生病时照常起作用。只看目录里的角色与资源事实，不看名称。"""
    role = item_role(catalog, o)
    if role in ("training", "experience"):
        return True
    if role in ("housing", "food", "job"):
        return False
    r = o.resource
    return (
        r is not None
        and r.basis in ("per_service_month", "per_use", "per_occurrence")
        and (r.hours or 0) > 0
    )


def health_catalog_problems(protocol: LifeProtocol, catalog: CityLifeCatalog) -> list[str]:
    """一座城的治疗目录是否齐全、与规则的需求定义一致（资料错误）。旧协议出现治疗目录也是错误。"""
    rules = protocol.health_rules
    city = catalog.city
    if rules is None:
        if catalog.treatments:
            return [
                f"{city}: 生活规则 {protocol.life_rules_version} 不读治疗目录（treatments）——要启用疾病与恢复请用 "
                "E1-5C 规则版本，旧协议不悄悄获得新行为"
            ]
        return []
    out: list[str] = []
    for sev in SEVERITIES:
        label = HEALTH_STATUS_LABEL[sev]
        offer = catalog.treatment(sev)
        need = rules.need(sev)
        if offer is None:
            out.append(
                f"{city}: 没有{label}的治疗条目——可得、确定不可得或资料未知都要写明；目录里没有记录不等于确定没有，"
                "也不等于资料完整"
            )
            continue
        if offer.need_id != need.need_id:
            out.append(
                f"{city}/{offer.treatment_id}: 满足的需求 {offer.need_id} 不是规则为{label}声明的 {need.need_id}"
                "——比较组要用同一套需求定义"
            )
    return out


def health_state_problems(
    protocol: LifeProtocol, state: PersonYearState
) -> list[SettlementProblem]:
    """一个状态的健康部分能不能在这份协议下继续（输入错误）：有没有写、年龄在不在风险表里、规则与事件来源是否就是
    产生它的那一套（换了规则或来源的中间状态不静默续跑）、延续中的疾病是否早该结束。"""
    rules, source = protocol.health_rules, protocol.illness_events
    h = state.health
    who = state.clone_id
    if rules is None or source is None:
        if h is not None:
            return [
                _p(
                    "health_state",
                    f"{who}: 生活规则 {protocol.life_rules_version} 不读健康状态——要启用疾病与恢复请用 E1-5C "
                    "规则版本",
                )
            ]
        return []
    if h is None:
        return [
            _p(
                "health_state",
                f"{who}: 生活规则 {protocol.life_rules_version} 的状态必须写健康状态（年龄、重复分身序号；共同初始状态"
                "是健康、没有病史）——缺了不猜，也不默认成健康",
            )
        ]
    # 状态与疾病规则的关系：与观察构造共用 common 的同一份判断（收尾修订，F1）
    out = [_p(code, message) for code, message in health_rules_state_problems(rules, state)]
    # 事件来源只有结算需要（观察不显示任何由它推出的事实），所以只在这里核对
    b = h.binding
    if b is not None and b.source_fingerprint != source.content_fingerprint:
        out.append(
            _p(
                "illness_source_changed",
                f"{who}: 健康状态由另一个事件来源产生（{b.source_fingerprint[:12]}…，现在是 "
                f"{source.content_fingerprint[:12]}…）：指定事件、配对身份或种子变了，不把两段运气接在一起；"
                "请从共同初始状态重新运行",
            )
        )
    return out


def _emergency_plan_static(
    protocol: LifeProtocol, catalog: CityLifeCatalog, action: YearAction, who: str
) -> list[SettlementProblem]:
    """计划与目录就能判定的应急安排问题（不依赖当年状态，加载器对后续年份也做这一层）。"""
    plan = action.emergency
    if protocol.health_rules is None:
        if plan is not None:
            return [
                _p(
                    "emergency_not_supported",
                    f"{who}: 协议 {protocol.protocol_id}（生活规则 {protocol.life_rules_version}）没有疾病规则，不读"
                    "应急安排——旧协议不悄悄启用新行为",
                )
            ]
        return []
    if plan is None:
        return [
            _p(
                "emergency_missing",
                f"{who}: E1-5C 的年度计划必须写应急安排（小病、大病各选条件允许就治或明确不治；时间精力不够时按什么"
                "顺序暂停哪些活动）——漏写不会被默认成总是治疗，也不由健康输入替你生成",
            )
        ]
    out: list[SettlementProblem] = []
    for oid in plan.pause_order:
        o = catalog.opportunity(oid)
        if o is None:
            out.append(
                _p("emergency_pause_invalid", f"{who}: 暂停顺序里的 {oid} 在 {catalog.city} 不存在")
            )
            continue
        if not participation_activity(catalog, o):
            out.append(
                _p(
                    "emergency_pause_invalid",
                    f"{who}/{oid}: 只有有参与时间的主动活动（学习、一次体验、有参与时间的服务或持有物）可以暂停；"
                    f"{item_role(catalog, o)} 不行——住房、食品、岗位与被动物品不靠暂停让路",
                )
            )
    # 收尾修订（F2）：生病月份取消的购买——只覆盖这一年计划里的一次性耐用品购买。计划里同一机会只能出现一次
    # （YearAction 契约），所以按机会 ID 引用总是唯一的
    planned = {it.opportunity_id for it in action.items}
    for oid in plan.cancel_purchases_when_ill:
        o = catalog.opportunity(oid)
        if o is None:
            out.append(
                _p(
                    "emergency_cancel_invalid",
                    f"{who}: 生病时取消的购买 {oid} 在 {catalog.city} 不存在",
                )
            )
            continue
        role = item_role(catalog, o)
        if role != "durable_good":
            out.append(
                _p(
                    "emergency_cancel_invalid",
                    f"{who}/{oid}: 生病时取消只覆盖一次性耐用品购买；{role} 不在本轮范围——课程学费、年付合同、"
                    "体验与其他交易照已有规则执行（时间精力不够时的暂停用 pause_order）",
                )
            )
            continue
        if oid not in planned:
            out.append(
                _p(
                    "emergency_cancel_invalid",
                    f"{who}/{oid}: 这一年的计划里没有这件购买——取消不了没有安排的交易",
                )
            )
    return out


def _emergency_plan_state(
    protocol: LifeProtocol, state: PersonYearState, action: YearAction, who: str
) -> list[SettlementProblem]:
    """需要年初状态才能判定的：暂停顺序里的活动要么在这一年的计划里，要么是年初的持有物。"""
    plan = action.emergency
    if protocol.health_rules is None or plan is None:
        return []
    have = {it.opportunity_id for it in action.items} | {h.opportunity_id for h in state.holdings}
    return [
        _p(
            "emergency_pause_invalid",
            f"{who}/{oid}: 暂停顺序里的活动既不在这一年的计划里，也不是年初的持有物——暂停不了不存在的活动",
        )
        for oid in plan.pause_order
        if oid not in have
    ]


def tax_reference_problems(catalog: CityLifeCatalog, policy: SyntheticTaxPolicy) -> list[str]:
    """城市引用的税策略与城市的口径、税区是否一致。不一致不做任何转换或改标签。"""
    out: list[str] = []
    if policy.money_basis_id != catalog.money_basis.numeraire_id:
        out.append(
            f"{catalog.city}: 税策略 {policy.policy_id} 的口径 {policy.money_basis_id} 与城市口径 "
            f"{catalog.money_basis.numeraire_id} 不一致"
        )
    if policy.applies_to != catalog.jurisdiction:
        out.append(
            f"{catalog.city}: 税策略 {policy.policy_id} 适用于 {policy.applies_to}，"
            f"城市税区是 {catalog.jurisdiction}"
        )
    return out


# ==========================================================================
# 状态与计划（结算前检查，问题都是 input_error）
# ==========================================================================


def _p(code: str, message: str, **kw: Any) -> SettlementProblem:
    return SettlementProblem(code=code, message=message[:500], **kw)


def state_problems(
    protocol: LifeProtocol, state: PersonYearState, catalog: CityLifeCatalog
) -> list[SettlementProblem]:
    """一个状态本身在协议 N 与所在城市下是否合法。年份、持有物、培训逐项检查。"""
    out: list[SettlementProblem] = []
    n = protocol.simulation_years
    who = state.clone_id
    if str(state.city) != str(catalog.city):
        out.append(
            _p("city_mismatch", f"{who}: 状态城市 {state.city} 与目录 {catalog.city} 不一致")
        )
        return out
    if state.money_basis_id != catalog.money_basis.numeraire_id:
        out.append(
            _p(
                "basis_mismatch",
                f"{who}: 账户口径 {state.money_basis_id} 与所在城市 {state.city} 的口径 "
                f"{catalog.money_basis.numeraire_id} 不一致",
            )
        )
    if exact_minor_units(state.cash.amount, catalog.money_basis.minor_unit_digits) is None:
        out.append(
            _p("cash_precision", f"{who}: 现金 {state.cash.amount!r} 的精度细于最小记账单位")
        )

    t = state.year_index
    if state.status == "active" and t > n:
        out.append(_p("year_beyond_horizon", f"{who}: 第 {t} 年超出协议的 N = {n}"))
    if state.status == "terminal" and t != protocol.terminal_year_index:
        out.append(
            _p(
                "terminal_index",
                f"{who}: 终态编号必须是 N + 1 = {protocol.terminal_year_index}，得到 {t}",
            )
        )
    if state.status == "exited" and t > n:
        out.append(_p("year_beyond_horizon", f"{who}: 出局年份 {t} 超出 N = {n}"))
    if state.status == "stopped" and t > n:
        out.append(_p("year_beyond_horizon", f"{who}: 停止年份 {t} 超出 N = {n}"))
    # 持有物与培训的年份上限：开局状态是"第 t 年年初"，不可能持有第 t 年才买的东西。
    latest = {"active": t - 1, "exited": t, "stopped": t, "terminal": n}[state.status]

    for h in state.holdings:
        o = catalog.opportunity(h.opportunity_id)
        if o is None:
            out.append(
                _p("unknown_holding", f"{who}: 持有物 {h.opportunity_id} 在 {state.city} 不存在")
            )
            continue
        kind = o.duration.kind
        months = h.effective_remaining_months
        if kind == "one_time_experience":
            out.append(_p("bad_holding", f"{who}: 一次体验 {h.opportunity_id} 不能作为跨年持有物"))
        elif kind == "training_course":
            out.append(
                _p(
                    "bad_holding",
                    f"{who}: 培训 {h.opportunity_id} 的进度在 training 里，不是持有物",
                )
            )
        elif kind == "durable_good":
            life = o.duration.lifetime_months or 0
            if months is None:
                out.append(_p("bad_holding", f"{who}: 持续物品 {h.opportunity_id} 没有剩余期限"))
            elif months > life:
                out.append(
                    _p(
                        "bad_holding",
                        f"{who}: {h.opportunity_id} 剩余 {months} 个月，超过物品期限 {life} 个月",
                    )
                )
        elif kind == "ongoing_service" and (months is None or months > MONTHS_PER_YEAR):
            out.append(
                _p(
                    "bad_holding",
                    f"{who}: 预付服务 {h.opportunity_id} 的剩余覆盖必须是 1–12 个月，得到 {months!r}",
                )
            )
        if state.status != "terminal" and months == 0:
            out.append(_p("bad_holding", f"{who}: {h.opportunity_id} 剩余 0 个月，应已到期移除"))
        if h.acquired_year > latest:
            out.append(
                _p(
                    "holding_year",
                    f"{who}: {h.opportunity_id} 的购入年份 {h.acquired_year} 晚于状态允许的 {latest}",
                )
            )
        if h.in_service_from_year is not None:
            if h.in_service_from_year > protocol.terminal_year_index:
                out.append(
                    _p(
                        "holding_year",
                        f"{who}: {h.opportunity_id} 的起用年份 {h.in_service_from_year} 超出 N + 1",
                    )
                )
            if h.in_service_from_year <= h.acquired_year:
                out.append(
                    _p("holding_year", f"{who}: {h.opportunity_id} 的起用年份不晚于购入年份")
                )

    for tr in state.training:
        o = catalog.opportunity(tr.opportunity_id)
        if o is None or o.duration.kind != "training_course":
            out.append(
                _p("unknown_training", f"{who}: 培训进度 {tr.opportunity_id} 不是本城的培训")
            )
            continue
        if tr.months_required != o.duration.completion_months:
            out.append(
                _p(
                    "training_mismatch",
                    f"{who}: {tr.opportunity_id} 需要 {o.duration.completion_months} 个月，"
                    f"进度里写的是 {tr.months_required}",
                )
            )
        if tr.months_participated > tr.months_required:
            out.append(_p("training_mismatch", f"{who}: {tr.opportunity_id} 的参与月数超过所需"))
        if tr.enrolled_year > latest:
            out.append(
                _p(
                    "training_year",
                    f"{who}: {tr.opportunity_id} 的报名年份 {tr.enrolled_year} 晚于 {latest}",
                )
            )
    out.extend(feedback_state_problems(protocol, state))
    out.extend(_p("savings_state", m) for m in savings_state_problems(protocol, state, catalog))
    out.extend(health_state_problems(protocol, state))
    return out


def _requested_months(role: str, item: ActionItem) -> tuple[int, ...]:
    if role in PERIODIC_ROLES and item.start_month and item.end_month:
        return tuple(range(item.start_month, item.end_month + 1))
    if role == "prepaid_service" and item.start_month:
        return tuple(range(item.start_month, MONTHS_PER_YEAR + 1))
    if role in ONE_TIME_ROLES and item.month:
        return (item.month,)
    return ()


def requested_months(catalog: CityLifeCatalog, item: ActionItem) -> tuple[int, ...]:
    """一项行动本年请求的月份（周期行动 = 起止月之间；预付 = 本年覆盖月；一次性 = 成交月）。"""
    o = catalog.opportunity(item.opportunity_id)
    return () if o is None else _requested_months(item_role(catalog, o), item)


def plan_problems(
    protocol: LifeProtocol, catalog: CityLifeCatalog, action: YearAction
) -> list[SettlementProblem]:
    """只看计划与目录就能发现的错误：引用、安排、时间字段、数量、住房与食品逐月覆盖。

    不依赖当年的状态，所以加载器能对后续年份的计划也做这一层检查。
    """
    out: list[SettlementProblem] = []
    who = f"{action.clone_id} 第 {action.year_index} 年"
    if action.year_index > protocol.simulation_years:
        out.append(
            _p(
                "year_beyond_horizon",
                f"{who}: 计划年份超出协议的 N = {protocol.simulation_years}；"
                f"第 {protocol.terminal_year_index} 年是终态，不接受行动",
            )
        )
    roles: dict[int, str] = {}
    for i, item in enumerate(action.items):
        o = catalog.opportunity(item.opportunity_id)
        if o is None:
            out.append(
                _p(
                    "unknown_opportunity",
                    f"{who}: 行动引用了 {catalog.city} 不存在的机会 {item.opportunity_id}",
                    item_order=i,
                )
            )
            continue
        for key, value in item.arrangement.items():
            allowed = o.arrangement_options.get(key)
            if allowed is None:
                out.append(
                    _p(
                        "arrangement",
                        f"{who}/{o.opportunity_id}: 安排项 {key!r} 不在该机会允许的选项里",
                        item_order=i,
                    )
                )
            elif value not in allowed:
                out.append(
                    _p(
                        "arrangement",
                        f"{who}/{o.opportunity_id}: 安排 {key}={value!r} 不在允许值 {list(allowed)} 里",
                        item_order=i,
                    )
                )
        role = item_role(catalog, o)
        roles[i] = role
        q = item.quantity
        if q != int(q):
            out.append(
                _p(
                    "quantity",
                    f"{who}/{o.opportunity_id}: 本轮只支持整数数量，得到 {q}",
                    item_order=i,
                )
            )
        elif role in ("housing", "food", "job", "training") and q != 1:
            out.append(
                _p(
                    "quantity",
                    f"{who}/{o.opportunity_id}: {role} 的数量必须是 1，得到 {q}",
                    item_order=i,
                )
            )
        has_range = item.start_month is not None and item.end_month is not None
        if role in PERIODIC_ROLES:
            if not has_range or item.month is not None:
                out.append(
                    _p(
                        "timing",
                        f"{who}/{o.opportunity_id}: 按月执行的行动必须给 start_month 与 end_month",
                        item_order=i,
                    )
                )
        elif role == "prepaid_service":
            if item.start_month is None or item.end_month is not None or item.month is not None:
                out.append(
                    _p(
                        "timing",
                        f"{who}/{o.opportunity_id}: 年付服务只给 start_month（覆盖固定 12 个月，"
                        "可跨入下一年）",
                        item_order=i,
                    )
                )
        elif item.month is None or item.start_month is not None:
            out.append(
                _p(
                    "timing",
                    f"{who}/{o.opportunity_id}: 一次性购买或体验必须给 month，且不给起止月",
                    item_order=i,
                )
            )

    if protocol.resource_rules is not None:
        out.extend(_resource_plan_problems(catalog, action, roles, who))
    out.extend(_savings_plan_static(protocol, catalog, action, who))
    out.extend(_emergency_plan_static(protocol, catalog, action, who))

    for need in MINIMUM_NEEDS:
        lines = [
            action.items[i]
            for i, r in roles.items()
            if r == need and action.items[i].start_month and action.items[i].end_month
        ]
        missing: list[int] = []
        doubled: list[int] = []
        for m in range(1, MONTHS_PER_YEAR + 1):
            k = sum(1 for it in lines if it.start_month <= m <= it.end_month)  # type: ignore[operator]
            if k == 0:
                missing.append(m)
            elif k > 1:
                doubled.append(m)
        label = _NEED_LABEL[need]
        if missing:
            out.append(
                _p(
                    f"{need}_not_covered",
                    f"{who}: 第 {missing} 月没有{label}安排；每月必须恰好一项合格的{label}",
                    month=missing[0],
                )
            )
        if doubled:
            out.append(
                _p(
                    f"{need}_double_covered",
                    f"{who}: 第 {doubled} 月有不止一项{label}安排",
                    month=doubled[0],
                )
            )
    return out


def _resource_plan_problems(
    catalog: CityLifeCatalog, action: YearAction, roles: dict[int, str], who: str
) -> list[SettlementProblem]:
    """按月核算的协议里，结算前就能判定的计划问题（E1-4A）。

    岗位的工时与到岗是岗位方案的事实：计划不能用安排项改它们。多份工作（包括同月两份到岗工作）可以
    同时做——执行修订 r2 起不再一律拒绝；资源够不够在实际执行路径上按月核算，超了是计划不可行，不是
    输入错误。同一个岗位写两遍仍由 ``YearAction`` 的结构校验拒绝（合并成一条），岗位数量必须是 1。
    """
    out: list[SettlementProblem] = []
    for i, role in roles.items():
        if role != "job":
            continue
        item = action.items[i]
        o = catalog.opportunity(item.opportunity_id)
        assert o is not None
        if item.arrangement:
            out.append(
                _p(
                    "job_arrangement_fixed",
                    f"{who}/{o.opportunity_id}: 岗位的工时与到岗日是岗位方案的事实，计划不能另写安排 "
                    f"{sorted(item.arrangement)}——不能保留工资却改工时或到岗",
                    item_order=i,
                )
            )
    return out


def planned_hours(state: PersonYearState, action: YearAction, catalog: CityLifeCatalog) -> Fraction:
    """计划占用的时间（小时，精确分数）。

    周期行动按请求的月数摊；一次体验整笔；本年买下、当年生效的持续物品按服务月摊；
    年付服务按本年覆盖月摊；**已有的持有物**按本年仍在服务的月数摊。声明了兜底的住房
    或食品取"所选项与最低方案"中较费时的那个——这样执行时即使降级，实际用时也不会
    超过这里检查过的预算。
    """
    total = Fraction(0)
    year = state.year_index
    for item in action.items:
        o = catalog.opportunity(item.opportunity_id)
        if o is None:
            continue
        role = item_role(catalog, o)
        months = _requested_months(role, item)
        if role == "experience":
            total += event_hours(o, item.quantity)
            continue
        if role == "durable_good":
            life = o.duration.lifetime_months or 0
            n = (
                min(MONTHS_PER_YEAR - item.month + 1, life)
                if item.month and o.duration.effective_from == "same_year"
                else 0
            )
            total += hours_for_months(o, n, item.quantity)
            continue
        h = hours_for_months(o, len(months), item.quantity)
        if role in action.minimum_fallback:
            spec = catalog.minimum_spec(role)
            base = catalog.opportunity(spec.baseline_opportunity_id) if spec else None
            if base is not None:
                h = max(h, hours_for_months(base, len(months)))
        total += h
    for held in state.holdings:
        o = catalog.opportunity(held.opportunity_id)
        if o is None:
            continue
        if held.in_service_from_year is not None and held.in_service_from_year > year:
            continue
        n = min(MONTHS_PER_YEAR, held.effective_remaining_months or 0)
        total += hours_for_months(o, n, held.quantity)
    return total


def plan_state_problems(
    protocol: LifeProtocol, state: PersonYearState, action: YearAction, catalog: CityLifeCatalog
) -> list[SettlementProblem]:
    """计划与当年状态之间的错误：身份、重复购入、预付重叠、培训进度、时间预算。"""
    out: list[SettlementProblem] = []
    who = f"{action.clone_id} 第 {action.year_index} 年"
    if action.clone_id != state.clone_id or action.person_id != state.person_id:
        out.append(
            _p(
                "identity",
                f"计划属于 {action.person_id}/{action.clone_id}，状态属于 "
                f"{state.person_id}/{state.clone_id}",
            )
        )
    if action.year_index != state.year_index:
        out.append(
            _p(
                "year_mismatch",
                f"{who}: 状态是第 {state.year_index} 年年初，计划写的是第 {action.year_index} 年",
            )
        )
    held = {h.opportunity_id: h for h in state.holdings}
    training = {t.opportunity_id: t for t in state.training}
    for i, item in enumerate(action.items):
        o = catalog.opportunity(item.opportunity_id)
        if o is None:
            continue
        role = item_role(catalog, o)
        oid = o.opportunity_id
        if role == "durable_good" and oid in held:
            out.append(
                _p(
                    "already_held",
                    f"{who}/{oid}: 已持有同一物品；本轮不支持把不同剩余期限的同种物品叠成一件",
                    item_order=i,
                )
            )
        if role == "prepaid_service" and oid in held and item.start_month is not None:
            covered_to = min(MONTHS_PER_YEAR, held[oid].effective_remaining_months or 0)
            if covered_to >= item.start_month:
                out.append(
                    _p(
                        "prepaid_overlap",
                        f"{who}/{oid}: 已预付的覆盖到第 {covered_to} 月，不能从第 "
                        f"{item.start_month} 月再预付",
                        item_order=i,
                    )
                )
        if role == "training" and item.start_month and item.end_month:
            tr = training.get(oid)
            want = item.end_month - item.start_month + 1
            if tr is not None and tr.completed:
                out.append(_p("training_done", f"{who}/{oid}: 这门培训已经完成", item_order=i))
            else:
                need = (o.duration.completion_months or 0) - (tr.months_participated if tr else 0)
                if want > need:
                    out.append(
                        _p(
                            "training_months",
                            f"{who}/{oid}: 还需参与 {need} 个月，计划了 {want} 个月",
                            item_order=i,
                        )
                    )
    if protocol.career_rules is not None:
        # E1-5A：资格按年初状态判断，年内不会变。计划里写了年初还不满足要求的项目，是计划本身的错误
        # （整份计划退回，E1-7 接重试），不是逐月拒绝——"展示未来机会"不等于允许立即执行。
        for i, item in enumerate(action.items):
            o = catalog.opportunity(item.opportunity_id)
            if o is None:
                continue
            for gap in qualification_gaps(o, state):
                out.append(
                    _p(
                        "qualification_missing",
                        f"{who}/{o.opportunity_id}: 年初不满足 {gap.tag}——{gap.reason}",
                        item_order=i,
                    )
                )
    out.extend(_savings_plan_state(protocol, state, action, catalog, who))
    out.extend(_emergency_plan_state(protocol, state, action, who))
    if protocol.resource_rules is not None:
        # 按月核算的协议：时间与精力在实际执行路径上逐月检查，超限是计划不可行（plan_infeasible），
        # 不是这里的输入错误（DECISIONS/0020 第七节）。
        return out
    used = planned_hours(state, action, catalog)
    budget = Fraction(Decimal(repr(state.time_budget_hours)))
    if used > budget:
        out.append(
            _p(
                "time_budget",
                f"{who}: 计划占用 {float(used):.1f} 小时，超过年度时间预算 {state.time_budget_hours} 小时",
            )
        )
    return out


# ==========================================================================
# 加载器的交叉校验
# ==========================================================================


def internal_leak_problems(where: str, o: LifeOpportunity) -> list[str]:
    """机会的规格、属性与安排项里不能有评分侧的参数（`DECISIONS/0019` 第十二节）。

    两道检查：保留的参数键名（beta、tau、η……），以及"某个内部评分对象原样序列化后混进来"
    的结构检查。正常的品牌、菜系、准备方式、住房规格、通勤分钟都不受影响。这不是对任意改名或
    自然语言隐写的检测器。
    """
    out: list[str] = []
    for label, mapping in (
        ("规格", o.spec),
        ("属性", o.attributes),
        ("安排项", o.arrangement_options),
    ):
        reserved = sorted(
            set(mapping)
            & (
                RESERVED_SCORING_KEYS
                | RESERVED_HEALTH_KEYS
                | RESERVED_EVALUATION_KEYS
                | RESERVED_AGGREGATION_KEYS
            )
        )
        if reserved:
            out.append(f"{where}: {label}里有评分侧的参数键 {reserved}——内部参数不能写进生活事实")
        hidden = serialized_internal(dict(mapping))
        if hidden is not None:
            out.append(
                f"{where}: {label}里藏着原样序列化的内部评分对象 {hidden}——内部参数不能写进生活事实"
            )
    return out


def function_supply_problems(catalog: CityLifeCatalog, functions: set[str]) -> list[str]:
    """功能供给声明与目录是否对得上（`DECISIONS/0019` 第十二节）。

    available ⇒ 至少一项可得的机会声明了这项功能；unavailable ⇒ 没有可得、也没有资料未知的；
    unknown ⇒ 没有可得的（目录里已经有可得方案，就该声明 available）。
    """
    out: list[str] = []
    for fs in catalog.function_supply:
        where = f"{catalog.city}/功能供给 {fs.function}"
        if fs.function not in functions:
            out.append(f"{where}: 功能没有在 functions 里声明")
            continue
        with_f = [o for o in catalog.opportunities if fs.function in o.functions]
        avail = [o.opportunity_id for o in with_f if o.availability == "available"]
        unsure = [o.opportunity_id for o in with_f if o.availability == "unknown"]
        if fs.status == "available" and not avail:
            out.append(f"{where}: 声明可得，但目录里没有一项可得的机会提供这项功能")
        if fs.status == "unavailable" and (avail or unsure):
            out.append(
                f"{where}: 声明确定不可得，但目录里有 {sorted(avail + unsure)}（可得或资料未知）"
            )
        if fs.status == "unknown" and avail:
            out.append(
                f"{where}: 目录里已有可得方案 {sorted(avail)}，应声明 available 而不是 unknown"
            )
    return out


def cross_validate(bundle: LifeBundle) -> ContractReport:
    """逐条收集跨对象错误，一次报全，不在第一条就停。"""
    errors: list[str] = []
    settlement: list[str] = []
    notes: list[str] = []
    needs = set(bundle.consumption_needs)
    categories = set(bundle.categories)
    protocol = bundle.protocol
    n = protocol.simulation_years

    # ---- 城市身份：用完整的 国家/体系/代码，不用 code 单独判重 ----
    keys = [str(c.city) for c in bundle.catalogs]
    for k, cnt in Counter(keys).items():
        if cnt > 1:
            errors.append(f"城市 {k} 出现了 {cnt} 次")
    codes = Counter(c.city.code for c in bundle.catalogs)
    for code, cnt in codes.items():
        if cnt > 1 and len({str(c.city) for c in bundle.catalogs if c.city.code == code}) == cnt:
            notes.append(f"code {code!r} 在不同国家/体系下重复；按完整身份区分，不是错误")

    # ---- 目录引用 ----
    functions = set(bundle.functions)
    attribute_keys = set(bundle.attribute_keys)
    for cat in bundle.catalogs:
        for o in cat.opportunities:
            if o.category not in categories:
                errors.append(f"{cat.city}/{o.opportunity_id}: 类别 {o.category!r} 没有声明")
            for f in o.functions:
                if f not in functions:
                    errors.append(
                        f"{cat.city}/{o.opportunity_id}: 功能 {f!r} 没有在 functions 里声明"
                    )
            for k in o.attributes:
                if k not in attribute_keys:
                    errors.append(
                        f"{cat.city}/{o.opportunity_id}: 属性 {k!r} 没有在 attribute_keys 里声明"
                    )
            errors.extend(internal_leak_problems(f"{cat.city}/{o.opportunity_id}", o))
            if o.duration.kind == "training_course" and (o.duration.completion_years or 0) > n:
                errors.append(
                    f"{cat.city}/{o.opportunity_id}: 培训需要 {o.duration.completion_years} 年，"
                    f"超出协议的 N = {n} 年——模拟期内永远完成不了"
                )
        errors.extend(function_supply_problems(cat, functions))
        errors.extend(resource_catalog_problems(protocol, cat))
        errors.extend(career_catalog_problems(protocol, cat))
        errors.extend(health_catalog_problems(protocol, cat))
        for cov in cat.material_coverage:
            if cov.need not in needs:
                errors.append(f"{cat.city}: material_coverage 的需求 {cov.need!r} 没有声明")
        if cat.price_indices and not cat.material_coverage:
            notes.append(
                f"{cat.city}: 有价格指数但没有逐项需求覆盖声明——指数不等于物质维全部被支持"
            )
        settlement.extend(catalog_settlement_problems(cat, protocol))
        if cat.tax_policy_id is not None:
            policy = bundle.tax_policy(cat.tax_policy_id)
            if policy is None:
                errors.append(f"{cat.city}: 引用了不存在的税策略 {cat.tax_policy_id}")
            else:
                errors.extend(tax_reference_problems(cat, policy))

    # ---- 人：陈述与真值一一对应 ----
    stated = {p.person_id: p for p in bundle.stated_profiles}
    truths = {t.person_id: t for t in bundle.internal_truths}
    for label, seq in (
        ("stated_profiles", bundle.stated_profiles),
        ("internal_truths", bundle.internal_truths),
    ):
        for k, cnt in Counter(p.person_id for p in seq).items():
            if cnt > 1:
                errors.append(f"{label}: person_id {k} 重复")
    for pid in sorted(set(stated) ^ set(truths)):
        errors.append(f"person_id {pid} 只在陈述与真值的一边出现")
    # E3-A（DECISIONS/0034）：六维产品画像没有研究真值；同一个人只能有一种画像
    product = {p.person_id: p for p in bundle.product_profiles}
    for k, cnt in Counter(p.person_id for p in bundle.product_profiles).items():
        if cnt > 1:
            errors.append(f"product_profiles: person_id {k} 重复")
    for pid in sorted(set(product) & (set(stated) | set(truths))):
        errors.append(
            f"person_id {pid} 同时有六维产品画像与旧七维陈述或研究真值：同一个人只能有一种画像，产品画像不配研究真值"
        )
    for p in bundle.product_profiles:
        product_needs = [h.need for h in p.hard_requirements] + list(p.material_points or {})
        for need in product_needs:
            if need not in needs:
                errors.append(f"product_profiles/{p.person_id}: 需求 {need!r} 没有声明")
    for p in bundle.stated_profiles:
        stated_needs = (
            [s.need for s in p.soft_preferences]
            + [h.need for h in p.hard_requirements]
            + list(p.material_points or {})
        )
        for need in stated_needs:
            if need not in needs:
                errors.append(f"{p.person_id}: 需求 {need!r} 没有声明")
    for t in bundle.internal_truths:
        for need in t.material_subweights:
            if need not in needs:
                errors.append(f"{t.person_id}: 物质细权重的需求 {need!r} 没有声明")
    # E1-5C：七维 healthcare 点数的含义必须与协议声明的一致——旧画像（医疗可及性）不能进新协议，
    # 新画像（健康风险应对倾向）也不能交给旧协议；同一个键不悄悄换含义
    declared = protocol.health_input
    independent = isinstance(declared, IndependentHealthInputDeclaration)
    semantics = declared.semantics if isinstance(declared, HealthInputDeclaration) else None
    for label, seq in (
        ("stated_profiles", bundle.stated_profiles),
        ("internal_truths", bundle.internal_truths),
    ):
        for p in seq:
            if independent:
                errors.append(
                    f"{label}/{p.person_id}: 协议声明的是独立健康回答（六维产品画像），这份是七维输入——不能把七维医疗点数"
                    "改读成独立健康回答，请用新问卷重新确认"
                )
                continue
            if p.healthcare_semantics == semantics:
                continue
            if semantics is not None:
                errors.append(
                    f"{label}/{p.person_id}: 协议把 healthcare 点数声明为 {semantics}（健康风险应对倾向，只供代理做"
                    f"计划），这份输入写的是 {p.healthcare_semantics or '旧含义（医疗可及性）'}——旧答案不能改读成新含义，"
                    "请用新问卷重新询问"
                )
            else:
                errors.append(
                    f"{label}/{p.person_id}: 这份输入的 healthcare 点数是 {p.healthcare_semantics}（新含义），协议 "
                    f"{protocol.protocol_id} 按旧含义（医疗可及性）评分——不能悄悄换含义"
                )

    # E3-A：六维产品画像只进声明了独立健康回答、问卷版本相同的协议
    for p in bundle.product_profiles:
        if not independent:
            errors.append(
                f"product_profiles/{p.person_id}: 六维产品画像的健康回答是独立问卷，协议 {protocol.protocol_id} 没有声明独立"
                "健康回答（IndependentHealthInputDeclaration）——不能交给按七维点数声明的协议"
            )
        elif declared.questionnaire_version != p.health_questionnaire_version:  # type: ignore[union-attr]
            errors.append(
                f"product_profiles/{p.person_id}: 健康问卷版本 {p.health_questionnaire_version} 与协议声明的 "
                f"{declared.questionnaire_version} 不同——旧答案不能改读成新题目的答案"  # type: ignore[union-attr]
            )

    # ---- 初始状态 ----
    clones: dict[str, PersonYearState] = {}
    for s in bundle.initial_states:
        if s.clone_id in clones:
            errors.append(f"分身 {s.clone_id} 重复")
        clones[s.clone_id] = s
        if s.person_id not in stated and s.person_id not in product:
            errors.append(f"{s.clone_id}: 引用了不存在的人 {s.person_id}")
        cat = bundle.catalog_for(str(s.city))
        if cat is None:
            errors.append(f"{s.clone_id}: 引用了不存在的城市 {s.city}")
            continue
        errors.extend(p.message for p in state_problems(protocol, s, cat))
        errors.extend(resource_state_problems(protocol, s))
        errors.extend(career_state_problems(protocol, s, cat))
    if protocol.career_rules is not None:
        # 同一个人的对照分身：开局的能力与陈述的职业相同；岗位属于各自的城市，可以不同。
        start: dict[str, set[tuple[object, ...]]] = {}
        for s in bundle.initial_states:
            stated_occ = s.employment.stated_occupation if s.employment is not None else None
            start.setdefault(s.person_id, set()).add(
                (tuple(sorted(a.ability_id for a in s.abilities)), stated_occ)
            )
            profile = stated.get(s.person_id) or product.get(s.person_id)
            if (
                profile is not None
                and s.employment is not None
                and stated_occ != profile.occupation
            ):
                errors.append(
                    f"{s.clone_id}: 岗位记录里陈述的职业 {stated_occ} 与用户陈述 {profile.occupation} 不一致"
                )
        for pid, values in sorted(start.items()):
            if len(values) > 1:
                errors.append(f"{pid}: 对照分身的开局能力或陈述职业不一致——同一个人只有一份起点")
    if protocol.resource_rules is not None:
        # 同一个人的对照分身：基础时间预算与精力额度跨城相同，城市不能擅自给不同的基础额度。E1-4B 比较的
        # 是固定的个人基础额度——第二年起的当前额度可以因各城实际生活的满足感不同而不同。
        by_person: dict[str, set[tuple[float, float, str]]] = {}
        for s in bundle.initial_states:
            base = (
                s.energy_quota_basis.base_level
                if s.energy_quota_basis is not None
                else s.energy.level
            )
            by_person.setdefault(s.person_id, set()).add(
                (s.time_budget_hours, base, s.energy.scale_version)
            )
        for pid, values in sorted(by_person.items()):
            if len(values) > 1:
                errors.append(
                    f"{pid} 的对照分身基础资源不同 {sorted(values)}：同一个人在不同城市的净可分配小时"
                    "与精力额度必须相同"
                )
    if protocol.health_rules is not None:
        # E1-5C 配对：同一个人只有一个年龄；同一个人在同一座城的重复分身序号不能重复（跨城同序号的分身共用随机数）
        ages: dict[str, set[int]] = {}
        repeats: Counter[tuple[str, str, int]] = Counter()
        for s in bundle.initial_states:
            if s.health is None:
                continue
            ages.setdefault(s.person_id, set()).add(s.health.age_years)
            repeats[(s.person_id, str(s.city), s.health.repeat_index)] += 1
        for pid, values in sorted(ages.items()):
            if len(values) > 1:
                errors.append(f"{pid}: 对照分身的年龄不同 {sorted(values)}——同一个人只有一个年龄")
        for (pid, city, k), cnt in sorted(repeats.items()):
            if cnt > 1:
                errors.append(
                    f"{pid} 在 {city} 有 {cnt} 个分身都用重复序号 {k}——它们会共用同一串随机数，重复序号必须不同"
                )
        source = protocol.illness_events
        used = {(pid, k) for pid, _, k in repeats}
        for e in source.injected if source is not None else ():
            if (e.person_id, e.repeat_index) not in used:
                errors.append(
                    f"指定事件 {e.person_id} 序号 {e.repeat_index} 第 {e.year_index} 年第 {e.month} 月：没有这个人的这个"
                    "重复序号的分身——指定事件不会落到任何人身上"
                )
            if e.year_index > n:
                errors.append(f"指定事件第 {e.year_index} 年超出协议的 N = {n}")
    for pid in sorted(set(stated) | set(product)):
        if not any(s.person_id == pid for s in bundle.initial_states):
            notes.append(f"{pid} 没有任何分身的初始状态")

    # ---- 样例行动（E1-1，不结算） ----
    for a in bundle.example_actions:
        s = clones.get(a.clone_id)
        if s is None:
            errors.append(f"行动引用了不存在的分身 {a.clone_id}")
            continue
        if a.person_id != s.person_id:
            errors.append(f"{a.clone_id}: 行动的 person_id {a.person_id} 与分身不符")
        if a.year_index > n:
            errors.append(f"{a.clone_id}: 行动年份 {a.year_index} 超出协议的 N = {n}")
        cat = bundle.catalog_for(str(s.city))
        if cat is None:
            continue
        for item in a.items:
            o = cat.opportunity(item.opportunity_id)
            if o is None:
                errors.append(
                    f"{a.clone_id}: 行动引用了 {s.city} 不存在的机会 {item.opportunity_id}"
                )
                continue
            for key, value in item.arrangement.items():
                allowed = o.arrangement_options.get(key)
                if allowed is None:
                    errors.append(
                        f"{a.clone_id}/{o.opportunity_id}: 安排项 {key!r} 不在该机会允许的选项里"
                    )
                elif value not in allowed:
                    errors.append(
                        f"{a.clone_id}/{o.opportunity_id}: 安排 {key}={value!r} 不在允许值 "
                        f"{list(allowed)} 里"
                    )
            if o.availability != "available":
                notes.append(
                    f"{a.clone_id}: 行动请求 {o.opportunity_id}（{o.availability}）——结构合法，"
                    "执行时会失败"
                )

    # ---- 年度计划（E1-2，可结算） ----
    for a in bundle.plans:
        s = clones.get(a.clone_id)
        if s is None:
            errors.append(f"计划引用了不存在的分身 {a.clone_id}")
            continue
        if a.person_id != s.person_id:
            errors.append(f"{a.clone_id}: 计划的 person_id {a.person_id} 与分身不符")
        if s.status != "active":
            errors.append(f"{a.clone_id}: 分身状态是 {s.status}，不接受计划")
            continue
        if a.year_index < s.year_index:
            errors.append(f"{a.clone_id}: 第 {a.year_index} 年的计划早于开局年份 {s.year_index}")
        cat = bundle.catalog_for(str(s.city))
        if cat is None:
            continue
        if minimum_living_problems(cat):
            # 哪些项算住房/食品由最低生活声明决定；声明坏了，计划检查只会报出一串
            # 连带错误。根因已经在 settlement_problems 里。
            notes.append(
                f"{a.clone_id} 第 {a.year_index} 年的计划未检查：{cat.city} 的最低生活声明不完整"
            )
            continue
        errors.extend(p.message for p in plan_problems(protocol, cat, a))
        # 只有开局那一年的状态是已知的；后续年份的状态要等结算才有。
        if a.year_index == s.year_index:
            errors.extend(p.message for p in plan_state_problems(protocol, s, a, cat))

    bases = sorted({c.money_basis.numeraire_id for c in bundle.catalogs})
    if len(bases) > 1:
        notes.append(
            f"目录含多个计价口径 {bases}：它们能被同一个加载器读取，但**不能直接比较**；"
            "本轮不做汇率或 PPP 转换"
        )

    if protocol.undecided_policies:
        notes.append(
            f"scoring_ready = false：{len(protocol.undecided_policies)} 条数学政策未定 "
            f"{list(protocol.undecided_policies)}"
        )

    availability = Counter(o.availability for c in bundle.catalogs for o in c.opportunities)
    return ContractReport(
        contract_valid=not errors,
        scoring_ready=protocol.scoring_ready,
        settlement_ready=not errors and not settlement,
        protocol_status=protocol.status,
        undecided_policies=protocol.undecided_policies,
        errors=tuple(errors),
        settlement_problems=tuple(settlement),
        notes=tuple(notes),
        summary={
            "protocol_id": protocol.protocol_id,
            "simulation_years": n,
            "terminal_year_index": protocol.terminal_year_index,
            "n_catalogs": len(bundle.catalogs),
            "countries": sorted({c.city.country for c in bundle.catalogs}),
            "money_bases": bases,
            "n_opportunities": sum(len(c.opportunities) for c in bundle.catalogs),
            "availability": dict(sorted(availability.items())),
            "n_people": len(bundle.stated_profiles) + len(bundle.product_profiles),
            "n_clones": len(bundle.initial_states),
            "n_example_actions": len(bundle.example_actions),
            "n_plans": len(bundle.plans),
            "n_tax_policies": len(bundle.tax_policies),
        },
    )


def parse_bundle(data: dict[str, Any]) -> LifeBundle:
    """只做结构解析。单对象的错误在这里抛 ``LifeContractError``。"""
    try:
        return LifeBundle.model_validate(data)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise LifeContractError("生活输入结构不合法：\n  " + "\n  ".join(lines)) from exc


def load_life_bundle(
    path: Path, *, strict: bool = True, require_settlement_ready: bool = False
) -> tuple[LifeBundle, ContractReport]:
    """读一份 YAML，解析并交叉校验。

    ``strict``：有任何交叉错误就抛。``require_settlement_ready``：还要求每座城都能结算
    （最低生活与税配置齐全、计费语义受支持）。
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise LifeContractError(f"{path}: 顶层必须是映射")
    bundle = parse_bundle(raw)
    report = cross_validate(bundle)
    if strict and not report.contract_valid:
        raise LifeContractError(f"{path}: 交叉校验失败：\n  " + "\n  ".join(report.errors))
    if require_settlement_ready and not report.settlement_ready:
        raise LifeContractError(
            f"{path}: 不能结算：\n  " + "\n  ".join(report.errors + report.settlement_problems)
        )
    return bundle, report


def dump_bundle(bundle: LifeBundle) -> dict[str, Any]:
    """稳定序列化（JSON 兼容）。再 ``parse_bundle`` 一次应得到相等的对象。"""
    return bundle.model_dump(mode="json")
