"""生活模型的探索月度评分（E1-3，`DECISIONS/0018`）。纯函数，无 I/O、无随机、无 LLM。

读一年已经结算好的生活记录（``EnvironmentOutcome``），逐月说明分身**实际**享受了什么、
各分项与七维的满足感是多少、哪些硬要求没有满足、每个数从哪条记录来。

**只读不写。** 评分不改余额、物品或行动；结算也不依赖评分（``econ.life_settlement`` 不
import 本模块）。

**不是旧静态模型的间接效用。** 旧公式（``econ.utility``）假设消费者已经最优地分配了
消费，只适合"给定预算算福利"；这里评价的是实际执行的安排——住了哪套房、吃了什么、
用了哪些服务——所以不能把旧公式直接当成任意生活行动的评分器。

**也不是正式评分。** 评分映射仍是未定政策；这里的函数与参数是写进 ADR0018 的合成探索
版本（``saturating_capped_v1``），不能声称已经现实标定，不输出城市排序。

--------------------------------------------------------------------------
一个月怎么算
--------------------------------------------------------------------------

1. 找出这个月**实际在用**的东西（来源表见 ``common.life_scoring_contracts.ScoringRecord``）：
   实际住的房、实际吃的食品方案（含降级后的最低方案）、执行了的按月服务、发生了的体验
   来自 ``ItemExecution`` 的执行月；持续物品与年付服务**只**来自 ``HoldingService`` 的服务月。
2. 每个分项：z = b + Σ η·x，s = min(c, z/(z+τ))。价格从不进入计算——同样的房子标价更高
   也不会得分更高；钱只决定买得起什么。
3. 物质维 s_material = Σ m_j s_j；月度分 u = Σ w_k s_k。
4. 有正权重的分项数据未知 → 这个月总分不完整（只给分项与原因）。不补零，不把缺的
   需求删掉再重新归一化。权重为 0 的分项缺失不阻断。
5. 出局当月与之后单列状态，不评分，也不当普通缺失跳过。
6. 十二个月都可评分才有年均分：先逐月评分，再平均。

E1-3 增补（`DECISIONS/0019`）只在评分规格声明时启用：按需求功能取"存在"的来源（同功能的第二件
不再加分，容量型只按声明的容量规格相加）；环境的自然景观加分 S_env = min(c, S_physical + β·A)；
通勤的方案级映射 τ_T/(τ_T + T)，T 从这个月实际住的住房读取，并且要求这个月恰好执行了一份固定线下
工作。持有物数量直接读结算写出的记录。没有声明这些的旧规格照 ADR0018 原样运行。

E1-5A 补充（`DECISIONS/0024`）只在评分版本 ``e1-5a-growth-transport-v1`` 启用：

* 发展维 z_m = b + η_growth · g_m：g_m 是这个月实际新增的能力进度之和（``growth_by_month``），读年初状态与
  同一次结算写出的学习记录；没有学习的月份成长为 0（已知），不是缺失。证书本身、换岗与涨薪不另加分；
* 通勤维（交通便捷程度）k_m = ρ + (1 − ρ)·D_m / D_ref，T_eq = k_m · T_m，S = τ_T/(τ_T + T_eq)：T 读实际所住
  住房的往返分钟，D 读资源账的到岗次数（时间账用的同一个计数——这是"评分不读资源账"的一个窄例外，只读这
  一个计数）；无工作、远程、混合、多岗位在资料齐全时都能评。T_eq 是评分中间量，不是实际出行分钟。

E1 补充"日常生活便利"（`DECISIONS/0033`）只在评分版本 ``e1-convenience-v1`` 启用（它沿用下面 E1-5C 的健康语义与上面的
成长、广义交通）：物质维第三项从"有没有洗衣设备"换成 q_m = Σ_t（协议基准小时 − 当月资源账里本人的残余小时），
s = min(c, q/(q + τ))。设备与人工服务只通过实际减少的小时起作用；成交、花钱与目标兑现不另计分；旧的洗衣分不再叠加。

E1-5C（`DECISIONS/0027`）只在评分版本 ``e1-5c-health-v1`` 启用（日常生活便利版本沿用）：

* 医疗维没有分项（城市医疗背景不进满足感）；七维 healthcare 点数表示健康风险应对倾向，不进满足感；
* 有效六维权重 w̃_k = w_k / Σ_{j≠医疗} w_j（按输入的十进制写法精确相除再取浮点，只改健康输入、六维比例不变时
  分数逐位相同）；日常生活分 B_m = Σ w̃_k s_{k,m}，六维公式（物质、成长、广义交通……）都不变；
* 月度总分 S_m = (1 − γ) B_m + γ H(q_m)，q_m 读结算写出的**月初**健康状态，H 与 γ 全组共用、不乘个人权重；
  付医疗费不加分，没有"没看病"的额外惩罚；六维里有正权重的分项未知照旧阻断（不补零、不重新归一化）；
* 停止月（应急规则不足或资料缺口）与之后不评分，不当普通缺失跳过。

E3-A（`DECISIONS/0034`）新增一种**输入**，不改上面任何公式：六维产品画像（``ProductLifeProfile``，偏好来源
``product_confirmed_six_dim``）。六维权重 = 确认的精确点数 / 100，只转一次浮点；它就是健康版本的有效权重（Σ六维 = 100），
所以同一组六维比例与旧七维路径的有效权重逐位相同。健康回答独立、不进权重；六维画像只能交给健康评分版本，协议必须声明独立
健康回答（``IndependentHealthInputDeclaration``）；遇到要七维原始权重的旧版本直接拒绝，不补医疗占位分。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from common.life_contracts import (
    MONTHS_PER_YEAR,
    PRODUCT_SIX_DIM_SOURCE,
    CityLifeCatalog,
    EnvironmentOutcome,
    ExactAmount,
    HardRequirement,
    IndependentHealthInputDeclaration,
    InternalPreferenceTruth,
    LifeContractError,
    LifeOpportunity,
    LifeProtocol,
    MonthlyResourceRules,
    MonthResources,
    PersonYearState,
    ResourceContribution,
    StatedLifeProfile,
    to_fraction,
)
from common.life_product_profile import POINTS_TOTAL, ProductLifeProfile
from common.life_scoring_contracts import (
    ADR0018_SCORING_VERSIONS,
    HEALTH_SCORING_VERSIONS,
    HEALTHCARE,
    AbilityProgressStep,
    CityComponentCondition,
    CityScoringConditions,
    CommuteTerm,
    ComponentMonth,
    ComponentSource,
    ConvenienceTerm,
    DimensionMonth,
    ExploratoryYearScore,
    GrowthTerm,
    HardRequirementResult,
    HealthTerm,
    HealthWeighting,
    ImprovementTerm,
    LandscapeTerm,
    LifeScoringSpec,
    MaterialTerm,
    MonthScore,
    PreferenceInsufficient,
    RequirementEvidence,
    ScoringComponent,
    ScoringPreference,
    TaskRelief,
    TransportTerm,
)
from common.life_units import conversion_factor, resolve_unit
from ledger.life_scoring_config import (
    condition_problems,
    convenience_preference_problems,
    convenience_protocol_problems,
    growth_transport_protocol_problems,
    health_protocol_problems,
)
from worlds.dimensions import ALL_DIMENSIONS, MATERIAL

#: 每一类实际记录从结果对象的哪个字段读取。评分来源表的代码形式（ADR0018 第三节）。
READ_FROM: dict[str, str] = {
    "housing": "ItemExecution.covered_months（role=housing，含兜底那条）",
    "food": "ItemExecution.covered_months（role=food，含兜底那条）",
    "periodic_service": "ItemExecution.covered_months（role=periodic_service）",
    "experience": "ItemExecution.covered_months（role=experience，成交那个月）",
    "prepaid_service": "HoldingService.covered_months（kind=prepaid_service；不读 ItemExecution 的镜像）",
    "durable_good": "HoldingService.covered_months（kind=durable_good；成交本身不计分）",
}
_ITEM_RECORDS = {
    "housing": "housing",
    "food": "food",
    "periodic_service": "periodic_service",
    "experience": "experience",
}


def saturating(z: float, tau: float, cap: float) -> tuple[float, float, bool]:
    """探索递减函数：返回（加上限前的 z/(z+τ)，加上限后的 s，上限是否起作用）。"""
    if z < 0 or tau <= 0 or not 0 <= cap <= 1:
        raise LifeContractError(
            f"探索函数的输入越界：z={z}, τ={tau}, c={cap}（负向作用本轮不支持）"
        )
    raw = z / (z + tau)
    return raw, min(cap, raw), raw > cap


# ==========================================================================
# 偏好：两条路径，来源写在结果上
# ==========================================================================


def preference_from_research_truth(
    truth: InternalPreferenceTruth,
) -> ScoringPreference | PreferenceInsufficient:
    """研究路径：合成设定的已知偏好。只在研究内部使用。"""
    if type(truth) is not InternalPreferenceTruth:
        raise LifeContractError(
            f"研究偏好只接受 InternalPreferenceTruth，得到 {type(truth).__name__}"
        )
    if truth.dimension_weights[MATERIAL] > 0 and not truth.material_subweights:
        return PreferenceInsufficient(
            person_id=truth.person_id,
            source="research_synthetic_truth",
            missing=("material_subweights",),
            message="物质维权重为正，但合成真值没有物质细项权重",
        )
    if truth.healthcare_semantics is not None and six_dimension_total(
        truth.dimension_weights
    ) == Fraction(0):
        return PreferenceInsufficient(
            person_id=truth.person_id,
            source="research_synthetic_truth",
            missing=("six_dimension_weights",),
            message="healthcare 之外的六维权重全为 0：健康风险应对倾向不进满足感，不能用平均权重代替",
        )
    return ScoringPreference(
        person_id=truth.person_id,
        source="research_synthetic_truth",
        dimension_weights=dict(truth.dimension_weights),
        material_weights=dict(truth.material_subweights),
        derivation=f"合成已知偏好（{truth.status}）：七维权重与物质细项权重取自研究内部真值",
        healthcare_semantics=truth.healthcare_semantics,
    )


def preference_from_stated_profile(
    profile: StatedLifeProfile,
) -> ScoringPreference | PreferenceInsufficient:
    """产品路径：只用用户**明确给出的数值**，按声明的规则换算。

    七维定和点数 / 100；物质细项定和点数 / 100。软偏好的 low/medium/high **不**换算成
    数字，自由文字**不**读。物质维有正点数却没有物质细项数值时，返回信息不足——不补
    均匀权重，也不借用研究真值（签名里就拿不到真值）。
    """
    if type(profile) is not StatedLifeProfile:
        raise LifeContractError(f"产品偏好只接受 StatedLifeProfile，得到 {type(profile).__name__}")
    points = profile.dimension_points
    if points[MATERIAL] > 0 and profile.material_points is None:
        return PreferenceInsufficient(
            person_id=profile.person_id,
            source="product_stated_numeric",
            missing=("material_points",),
            message=(
                f"物质维有 {points[MATERIAL]} 点，但用户没有给出物质细项的数值；序数偏好"
                "（low/medium/high）不换算成数字，也不补均匀权重或借用研究真值"
            ),
        )
    if profile.healthcare_semantics is not None and not any(
        v for k, v in points.items() if k != HEALTHCARE
    ):
        return PreferenceInsufficient(
            person_id=profile.person_id,
            source="product_stated_numeric",
            missing=("six_dimension_points",),
            message=(
                "100 点全部给了“健康风险应对”：它只供分身做计划、不进满足感，其余六维没有任何点数——请补充六维"
                "的取舍；不用平均权重代替"
            ),
        )
    return ScoringPreference(
        person_id=profile.person_id,
        source="product_stated_numeric",
        dimension_weights={k: points[k] / 100 for k in ALL_DIMENSIONS},
        material_weights={k: v / 100 for k, v in (profile.material_points or {}).items()},
        derivation="用户明确给出的七维定和点数 / 100；物质细项定和点数 / 100（声明的换算规则）",
        healthcare_semantics=profile.healthcare_semantics,
    )


def preference_from_confirmed_profile(
    profile: ProductLifeProfile,
) -> ScoringPreference | PreferenceInsufficient:
    """E3-A 产品路径（DECISIONS/0034）：用户确认的六维精确点数与物质细分。

    w_k = p_k / 100、m_j = q_j / 100，**精确相除后只转一次**浮点。健康评分版本的有效权重是 w_k / Σ六维，而新画像的六维精确合计
    100，所以这里得到的就是有效权重本身——与旧七维路径上“同一组六维比例”算出的权重逐位相同（例：旧 27/(27+18+18+9+9+9) = 3/10，
    新 30/100 = 3/10）。健康回答独立，不进任何权重；物质父项为 0 时没有细项权重（不补均匀权重）。签名里拿不到研究真值。
    """
    if type(profile) is not ProductLifeProfile:
        raise LifeContractError(
            f"六维产品偏好只接受 ProductLifeProfile，得到 {type(profile).__name__}"
        )
    dims = profile.dimension_fractions()
    materials = profile.material_fractions()
    if dims[MATERIAL] > 0 and not materials:  # 契约已保证；这里防的是绕过契约构造的对象
        return PreferenceInsufficient(
            person_id=profile.person_id,
            source=PRODUCT_SIX_DIM_SOURCE,
            missing=("material_points",),
            message="物质父项有点数，但没有确认的物质细分；不补均匀权重",
        )
    return ScoringPreference(
        person_id=profile.person_id,
        source=PRODUCT_SIX_DIM_SOURCE,
        dimension_weights={k: float(q / POINTS_TOTAL) for k, q in dims.items()},
        material_weights={k: float(q / POINTS_TOTAL) for k, q in materials.items()},
        derivation=(
            "用户确认的六维精确点数 / 100、物质细项精确点数 / 100（精确相除后只转一次浮点）；健康回答独立，不进权重"
        ),
    )


def six_dimension_total(weights: dict[str, float]) -> Fraction:
    """healthcare 之外六维权重之和（按输入的十进制写法精确相加）。"""
    return sum((to_fraction(w) for k, w in weights.items() if k != HEALTHCARE), Fraction(0))


def effective_weights(spec: LifeScoringSpec, pref: ScoringPreference) -> dict[str, float]:
    """评分用的维度权重。健康版本：w̃_k = w_k / Σ_{j≠医疗} w_j（只含六维，精确相除再取浮点）；旧版本：七维原样。

    E3-A：六维产品画像的权重在换算时已经是有效权重（精确点数 / 100，Σ = 1），原样使用；它没有 healthcare，只能交给健康评分版本。"""
    if pref.source == PRODUCT_SIX_DIM_SOURCE:
        if spec.scoring_version not in HEALTH_SCORING_VERSIONS:
            raise LifeContractError(
                f"六维产品画像只能用健康评分版本 {sorted(HEALTH_SCORING_VERSIONS)}；评分版本 {spec.scoring_version} 要七维"
                "原始权重（含医疗点数），不能补一个医疗占位分"
            )
        return dict(pref.dimension_weights)
    if spec.scoring_version not in HEALTH_SCORING_VERSIONS:
        return dict(pref.dimension_weights)
    total = six_dimension_total(pref.dimension_weights)
    if total == 0:
        raise LifeContractError("六维权重全为 0：信息不足，应在偏好换算时报 PreferenceInsufficient")
    return {
        k: float(to_fraction(w) / total)
        for k, w in pref.dimension_weights.items()
        if k != HEALTHCARE
    }


# ==========================================================================
# 实际在用的生活条件
# ==========================================================================


@dataclass(frozen=True)
class _InUse:
    record: str
    opp: LifeOpportunity
    quantity: float


#: 接受"持有服务没有数量"的旧记录、按 ADR0018 的顺序推断数量的评分版本。新版本不在其中：
#: 它们只读结算写出的数量（`DECISIONS/0019` 第二节）。与契约里的 ADR0018 版本集合是同一份。
LEGACY_QUANTITY_INFERENCE_VERSIONS: frozenset[str] = ADR0018_SCORING_VERSIONS


def _holding_quantities(
    spec: LifeScoringSpec, state: PersonYearState, outcome: EnvironmentOutcome
) -> tuple[list[float], str]:
    """每条 ``HoldingService`` 的数量，以及数量从哪来。

    * 结算写出了数量（增补之后的记录）：直接读取，与记录顺序无关；
    * 全部没有数量（增补之前的旧记录）：只有旧评分版本按 ADR0018 推断，新版本拒绝；
    * 部分有、部分没有：记录不完整，拒绝。任何情况下都不默认为 1。
    """
    services = outcome.holdings_service
    if not services:
        return [], "no_holdings"
    recorded = [hs.quantity for hs in services]
    if all(q is not None for q in recorded):
        return [float(q) for q in recorded if q is not None], "recorded"
    if any(q is not None for q in recorded):
        raise LifeContractError("持有服务记录部分有数量、部分没有：记录不完整，拒绝评分")
    if spec.scoring_version not in LEGACY_QUANTITY_INFERENCE_VERSIONS:
        raise LifeContractError(
            f"这是增补之前写出的旧记录（持有服务没有数量）；评分版本 {spec.scoring_version} 不推断"
            "数量——请重新结算，或用旧评分版本 e1-3-explore-v1 读取"
        )
    return _legacy_order_inference(state, outcome), "legacy_order_inference"


def _legacy_order_inference(state: PersonYearState, outcome: EnvironmentOutcome) -> list[float]:
    """旧记录的数量推断（ADR0018）：年初已有的取年初状态，本年买的取本年成交数量。

    依赖结算"年初持有物在前、本年新买在后"的写出顺序，同一项目至多两条（年付服务到期后
    续买）。对不上就报错，不默认为 1。只给旧评分版本读旧记录用。
    """
    at_start = {h.opportunity_id: h.quantity for h in state.holdings}
    bought = {
        it.opportunity_id: it.executed_quantity
        for it in outcome.items
        if it.role in ("durable_good", "prepaid_service") and it.executed_quantity > 0
    }
    seen: set[str] = set()
    out: list[float] = []
    for hs in outcome.holdings_service:
        oid = hs.opportunity_id
        if oid in at_start and oid not in seen:
            out.append(at_start[oid])
        elif oid in bought:
            out.append(bought[oid])
        else:
            raise LifeContractError(
                f"持有物服务记录 {oid} 对不上年初状态或本年购买——记录与状态不配套"
            )
        seen.add(oid)
    return out


def _jobs_by_month(
    outcome: EnvironmentOutcome, catalog: CityLifeCatalog
) -> dict[int, list[LifeOpportunity]]:
    """每个月实际执行（拿到工资）的工作。只给方案级通勤核对"这个月有没有线下工作"用；工资本身不计分。"""
    out: dict[int, list[LifeOpportunity]] = {m: [] for m in range(1, MONTHS_PER_YEAR + 1)}
    for it in outcome.items:
        if it.role != "job":
            continue
        opp = catalog.opportunity(it.opportunity_id)
        assert opp is not None
        for m in it.covered_months:
            out[m].append(opp)
    return out


def _in_use_by_month(
    spec: LifeScoringSpec,
    outcome: EnvironmentOutcome,
    catalog: CityLifeCatalog,
    state: PersonYearState,
) -> tuple[dict[int, list[_InUse]], str]:
    by_month: dict[int, list[_InUse]] = {m: [] for m in range(1, MONTHS_PER_YEAR + 1)}
    for it in outcome.items:
        record = _ITEM_RECORDS.get(it.role)
        if record is None:
            continue  # 工作、培训不是评分来源；持续物品与年付服务只从 HoldingService 读
        opp = catalog.opportunity(it.opportunity_id)
        assert opp is not None  # score_year 已检查
        for m in it.covered_months:
            by_month[m].append(_InUse(record, opp, it.executed_quantity))
    quantities, quantity_source = _holding_quantities(spec, state, outcome)
    for hs, quantity in zip(outcome.holdings_service, quantities, strict=True):
        opp = catalog.opportunity(hs.opportunity_id)
        assert opp is not None
        for m in hs.covered_months:
            by_month[m].append(_InUse(hs.kind, opp, quantity))
    return by_month, quantity_source


def _num(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


# ==========================================================================
# 来源怎样认候选、怎样取值：评分器与覆盖报告共用这一份（`DECISIONS/0019` 第十二节）
# ==========================================================================

#: 目录里一项机会在结算里的角色 → 评分读的那类实际记录。工作与培训不是评分来源。
RECORD_OF_ROLE: dict[str, str] = {
    "housing": "housing",
    "food": "food",
    "periodic_service": "periodic_service",
    "experience": "experience",
    "prepaid_service": "prepaid_service",
    "durable_good": "durable_good",
}


def source_candidate(src: ComponentSource, record: str, opp: LifeOpportunity) -> bool:
    """这条记录（或目录里这项机会）是不是这个来源的候选：记录类别、类别、功能、使用安排。"""
    if record not in src.records:
        return False
    if src.categories and opp.category not in src.categories:
        return False
    if src.functions and not set(src.functions) & set(opp.functions):
        return False
    return not (src.requires_spec_key is not None and src.requires_spec_key not in opp.spec)


def source_reading(
    src: ComponentSource, opp: LifeOpportunity, quantity: float
) -> tuple[float | None, str | None]:
    """候选给这个来源的取值 x；规格缺失或不是数值时返回（None，原因）——数据未知，不是零。"""
    if src.value == "quantity":
        return quantity, None
    if src.value == "present":
        return 1.0, None  # 这个月有一件在用的候选；同功能的第二件由 any/max 去重
    assert src.spec_key is not None
    have = opp.spec.get(src.spec_key)
    if have is None:
        return None, f"数据未知：{opp.opportunity_id} 的规格缺 {src.spec_key}"
    if src.value == "spec_flag":
        return (1.0 if have == src.flag_value else 0.0), None
    number = _num(have)
    if number is None:
        return None, f"数据未知：{opp.opportunity_id} 的 {src.spec_key} 不是数值"
    return number * (quantity if src.per_unit else 1.0), None


def unconfirmed_functions(src: ComponentSource, supply: dict[str, str]) -> list[str]:
    """来源依赖的功能里，供给没有被确认的（资料未知或根本没声明）。没有记录不等于确定没有。"""
    return [f for f in src.functions if supply.get(f) not in ("available", "unavailable")]


# ==========================================================================
# 分项
# ==========================================================================


def _head(comp: ScoringComponent) -> dict[str, object]:
    return dict(
        component_id=comp.component_id,
        dimension=comp.dimension,
        material_need=comp.material_need,
        base_only=comp.base_only,
        combine=comp.combine,
        tau=comp.tau,
    )


#: 方案级通勤不能评分时的统一前缀：不是"零通勤"，也不是"最长通勤"，是这一项未知。
_COMMUTE_UNSUPPORTED = "方案级通勤评分不支持/信息不足："


def _score_plan_commute(
    comp: ScoringComponent,
    cond: CityComponentCondition,
    in_use: list[_InUse],
    jobs: list[LifeOpportunity],
) -> ComponentMonth:
    """S = τ_T/(τ_T + T)：T 是这个月实际住的住房写明的每工作日往返分钟（`DECISIONS/0019` 第八节）。

    只对"这个月恰好执行了一份固定线下工作"评分。无工作、远程、多岗位、工作方式没写、住房分钟
    没写——都报不支持/信息不足（分项未知），不给最高分，也不照扣通勤分。
    """
    head = _head(comp)
    pc = comp.plan_commute
    assert pc is not None

    def unsupported(why: str) -> ComponentMonth:
        return ComponentMonth(**head, status="unknown", reasons=(_COMMUTE_UNSUPPORTED + why,))

    if cond.status == "unknown":
        return ComponentMonth(
            **head, status="unknown", reasons=(f"城市条件未知：{cond.unknown_reason}",)
        )
    if cond.status != "plan_level":
        return unsupported(f"城市条件是 {cond.status}，不是方案级")
    homes = [r for r in in_use if r.record == "housing"]
    if len(homes) != 1:
        return unsupported(f"这个月实际住房记录有 {len(homes)} 处，不是恰好一处")
    if not jobs:
        return unsupported("这个月没有执行任何工作，方案级通勤不适用")
    if len(jobs) > 1:
        return unsupported(f"这个月执行了 {len(jobs)} 份工作，多岗位通勤本轮未定义")
    job = jobs[0]
    mode = job.spec.get(pc.work_mode_key)
    if mode is None:
        return unsupported(f"工作 {job.opportunity_id} 的规格缺 {pc.work_mode_key}")
    if mode != pc.onsite_value:
        return unsupported(f"工作 {job.opportunity_id} 的工作方式是 {mode!r}，不是固定线下工作")
    home = homes[0].opp
    minutes = _num(home.spec.get(pc.minutes_key))
    if minutes is None:
        return unsupported(f"住房 {home.opportunity_id} 的规格缺 {pc.minutes_key}（或不是数值）")
    if minutes < 0:
        raise LifeContractError(f"{home.opportunity_id} 的往返分钟为负")
    s_value = comp.tau / (comp.tau + minutes)
    return ComponentMonth(
        **head,
        status="known",
        raw=s_value,
        s=s_value,
        commute=CommuteTerm(
            housing_opportunity_id=home.opportunity_id,
            job_opportunity_id=job.opportunity_id,
            minutes=minutes,
            tau_minutes=comp.tau,
        ),
        reasons=("方案级通勤：S = τ_T/(τ_T + T)，T 取实际所住住房的往返分钟；时间占用留 E1-4",),
    )


#: 一个月的成长：g_m（精确分数）与有进步的能力明细。
MonthGrowth = tuple[Fraction, tuple[AbilityProgressStep, ...]]


def growth_by_month(
    protocol: LifeProtocol,
    catalog: CityLifeCatalog,
    state: PersonYearState,
    outcome: EnvironmentOutcome,
) -> dict[int, MonthGrowth]:
    """每个月实际新增的能力进度 g_m = Σ_a (p_a,m − p_a,m−1)（`DECISIONS/0024`）。

    能力 a 的进度 p_a 在 0–1：年初已有的能力为 1；否则取授予它的课程里最高的"实际累计参加月数 / 要求月数"
    ——两门各学一半的课不拼成一项完成的资格。课程的年初进度读年初状态（包括这一年没有安排、暂停中的课程），
    这一年只因**实际参加**的月份增加（同一次结算写出的 ``EnvironmentOutcome.career.learning``）。第 m 月只看
    第 m 月及之前的参加记录，读不到年底才发生的学习。不读理由、学费、工资或课程名称。
    """
    rules = protocol.career_rules
    career = outcome.career
    if rules is None or career is None:
        raise LifeContractError(
            "成长项要读能力词表与这一年的学习记录：协议没有职业规则，或结果没有职业事实"
        )
    held = {a.ability_id for a in state.abilities}
    start: dict[str, tuple[int, int]] = {
        t.opportunity_id: (t.months_participated, t.months_required) for t in state.training
    }
    attended: dict[str, tuple[int, ...]] = {}
    for rec in career.learning:
        before = start.get(rec.opportunity_id, (0, rec.months_required))
        if (rec.progress_before, rec.months_required) != before:
            raise LifeContractError(
                f"{rec.opportunity_id}: 学习记录的年初进度 {rec.progress_before}/{rec.months_required} 与年初"
                f"状态 {before[0]}/{before[1]} 对不上——不是同一次生活的记录，不能评成长"
            )
        start[rec.opportunity_id] = before
        attended[rec.opportunity_id] = tuple(rec.months_attended)
    grants: dict[str, tuple[str, ...]] = {}
    for oid in start:
        opp = catalog.opportunity(oid)
        if opp is None:
            raise LifeContractError(f"课程 {oid} 不在目录里：学习记录与城市不配套")
        grants[oid] = tuple(opp.grants)

    def course(oid: str, m: int) -> Fraction:
        done, need = start[oid]
        n = done + sum(1 for k in attended.get(oid, ()) if k <= m)
        return min(Fraction(1), Fraction(n, need))

    def ability(aid: str, m: int) -> Fraction:
        if aid in held:
            return Fraction(1)
        return max((course(oid, m) for oid, gs in grants.items() if aid in gs), default=Fraction(0))

    out: dict[int, MonthGrowth] = {}
    for m in range(1, MONTHS_PER_YEAR + 1):
        total = Fraction(0)
        steps: list[AbilityProgressStep] = []
        for spec in rules.abilities:
            aid = spec.ability_id
            before, after = ability(aid, m - 1), ability(aid, m)
            if after <= before:
                continue
            total += after - before
            via = tuple(
                sorted(
                    oid for oid, gs in grants.items() if aid in gs and m in attended.get(oid, ())
                )
            )
            steps.append(
                AbilityProgressStep(
                    ability_id=aid,
                    before=float(before),
                    after=float(after),
                    delta=float(after - before),
                    via=via,
                )
            )
        out[m] = (total, tuple(steps))
    return out


def _fraction_text(q: Fraction) -> str:
    return str(q.numerator) if q.denominator == 1 else f"{q.numerator}/{q.denominator}"


def _score_growth(
    comp: ScoringComponent, cond: CityComponentCondition, month: MonthGrowth
) -> ComponentMonth:
    """发展维：z = b + η_growth · g_m，s = min(c, z/(z+τ))。b 是城市背景；成长是这个人这个月的实际进步。"""
    head = _head(comp)
    gs = comp.growth
    assert gs is not None
    if cond.status == "unknown":
        return ComponentMonth(
            **head, status="unknown", reasons=(f"城市条件未知：{cond.unknown_reason}",)
        )
    assert cond.base is not None and cond.cap is not None
    g, steps = month
    contribution = to_fraction(gs.eta) * g
    z = cond.base + float(contribution)
    raw, s, capped = saturating(z, comp.tau, cond.cap)
    if gs.eta == 0:
        why = "成长项关闭（η_growth = 0 对照）：发展维只有城市背景"
    elif g == 0:
        why = "这个月没有实际新增的能力进度：发展维只有城市背景（没有成长，不是数据缺失）"
    else:
        why = (
            f"发展维 = 城市背景 b + η_growth × 当月新增能力进度 {_fraction_text(g)}"
            "（只计实际参加的学习；证书本身、换岗与涨薪不另加分）"
        )
    return ComponentMonth(
        **head,
        status="known",
        base=cond.base,
        cap=cond.cap,
        z=z,
        raw=raw,
        s=s,
        capped=capped,
        reasons=(why,),
        growth=GrowthTerm(g=float(g), eta=gs.eta, contribution=float(contribution), steps=steps),
    )


#: 广义交通不能评分时的统一前缀：不是"零出行"，也不是"最长出行"，是这一项未知。
_TRANSPORT_UNKNOWN = "广义交通评分信息不足："


def _score_transport(
    comp: ScoringComponent,
    cond: CityComponentCondition,
    in_use: list[_InUse],
    resources: MonthResources | None,
) -> ComponentMonth:
    """通勤维（交通便捷程度）：k = ρ + (1 − ρ)·D/D_ref，T_eq = k·T，S = τ_T/(τ_T + T_eq)。

    T 读这个月实际所住住房（含兜底）的往返分钟；D 读资源账这个月的累计到岗次数（时间账用的同一个计数，
    多岗位相加，没执行的岗位不在里面）。住房分钟或资源账缺失报未知，不补 0；资源账与评分读到的住房对不上
    是记录错误。T_eq 只是评分中间量，时间与负担账不乘 k。
    """
    head = _head(comp)
    ts = comp.transport
    assert ts is not None

    def unknown(why: str) -> ComponentMonth:
        return ComponentMonth(**head, status="unknown", reasons=(_TRANSPORT_UNKNOWN + why,))

    if cond.status == "unknown":
        return ComponentMonth(
            **head, status="unknown", reasons=(f"城市条件未知：{cond.unknown_reason}",)
        )
    if cond.status != "plan_level":
        return unknown(f"城市条件是 {cond.status}，不是方案级")
    homes = [r for r in in_use if r.record == "housing"]
    if len(homes) != 1:
        return unknown(f"这个月实际住房记录有 {len(homes)} 处，不是恰好一处")
    home = homes[0].opp
    minutes = _num(home.spec.get(ts.minutes_key))
    if minutes is None:
        return unknown(
            f"住房 {home.opportunity_id} 的规格缺 {ts.minutes_key}（或不是数值）——往返分钟未知不当 0"
        )
    if minutes < 0:
        raise LifeContractError(f"{home.opportunity_id} 的往返分钟为负")
    if resources is None:
        return unknown(
            "这一年没有月度资源账：到岗次数未知（不当 0，也不按工作方式字样或岗位数量猜）"
        )
    if resources.resource_check != "complete":
        return unknown(f"资源账这个月是 {resources.resource_check}，不是完整检查：到岗次数不完整")
    if resources.housing_opportunity_id not in (None, home.opportunity_id):
        raise LifeContractError(
            f"第 {resources.month} 月：资源账记的实际住房是 {resources.housing_opportunity_id}，评分读到的是 "
            f"{home.opportunity_id}——不是同一次生活的记录"
        )
    if (
        resources.commute_roundtrip_minutes is not None
        and resources.commute_roundtrip_minutes != minutes
    ):
        raise LifeContractError(
            f"第 {resources.month} 月：资源账的往返分钟 {resources.commute_roundtrip_minutes} 与住房规格 "
            f"{minutes:g} 不一致"
        )
    visits = resources.onsite_visits
    rho = to_fraction(ts.rho)
    k = rho + (1 - rho) * Fraction(visits, ts.reference_visits)
    t_eq = k * to_fraction(minutes)
    tau = to_fraction(comp.tau)
    s_value = float(tau / (tau + t_eq))
    reasons = [
        "广义交通：k = ρ + (1 − ρ)·D/D_ref，T_eq = k·T（评分中间量，不是实际出行分钟），"
        "S = τ_T/(τ_T + T_eq)；D 读资源账的到岗次数，时间与负担账不变"
    ]
    if visits == 0:
        reasons.append("这个月没有到岗（没有工作或纯远程）：k = ρ，只剩不因上班消失的出行影响")
    return ComponentMonth(
        **head,
        status="known",
        raw=s_value,
        s=s_value,
        transport=TransportTerm(
            housing_opportunity_id=home.opportunity_id,
            minutes=minutes,
            onsite_visits=visits,
            reference_visits=ts.reference_visits,
            rho=ts.rho,
            visit_factor=float(k),
            equivalent_minutes=float(t_eq),
            tau_minutes=comp.tau,
        ),
        reasons=tuple(reasons),
    )


#: 日常生活便利不能评分时的统一前缀：不是"没有减负"，也不是"全部减负"，是这一项未知。
_CONVENIENCE_UNKNOWN = "日常生活便利信息不足："


def _fraction_hours(q: Fraction) -> str:
    return _fraction_text(q) if q.denominator == 1 else f"{_fraction_text(q)}（约 {float(q):.2f}）"


def _score_convenience(
    comp: ScoringComponent,
    cond: CityComponentCondition,
    resources: MonthResources | None,
    rules: MonthlyResourceRules | None,
) -> ComponentMonth:
    """物质维"日常生活便利"（`DECISIONS/0033`）：q = Σ_t（协议基准小时 − 当月资源账里本人的残余小时），s = min(c, q/(q + τ))。

    只读两样结构化事实：协议资源规则里每项家务的基准小时，与这个月资源账 household_task 那几行的本人小时——那几行是结算按"同一
    任务取当月在用方案里残余最小的那一个"写出的，所以设备与人工同时在用不会把节省相加，第二台同样的设备也不会多减负。不解析说明
    文字，不在这里重算替代，不读价格、付款或成交记录。没有采用任何方案时 q = 0 是确定的事实（不是未知）。

    资源账这个月不是完整检查、缺某项家务的行 → 这一项未知（不补 0，也不当基准）；重复的家务行、残余为负或超过基准 → 记录错误，拒绝。
    """
    head = _head(comp)
    cv = comp.convenience
    assert cv is not None

    def unknown(why: str) -> ComponentMonth:
        return ComponentMonth(
            **head, status="unknown", cap=cv.cap, reasons=(_CONVENIENCE_UNKNOWN + why,)
        )

    if cond.status == "unknown":
        return ComponentMonth(
            **head, status="unknown", reasons=(f"城市条件未知：{cond.unknown_reason}",)
        )
    if cond.status != "plan_level":
        return unknown(f"城市条件是 {cond.status}，不是方案级")
    if rules is None:
        raise LifeContractError(
            f"{comp.component_id}: 日常生活便利要读协议的家务基准：协议没有月度资源规则"
        )
    if resources is None:
        return unknown("这一年没有月度资源账：本人的家务小时未知（不当 0，也不当基准）")
    m = resources.month
    if resources.resource_check != "complete":
        return unknown(
            f"资源账第 {m} 月是 {resources.resource_check}，不是完整检查：家务记录不完整"
        )
    rows: dict[str, ResourceContribution] = {}
    for r in resources.contributions:
        if r.category != "household_task":
            continue
        if r.task is None:
            raise LifeContractError(f"第 {m} 月：资源账有一行家务没有写任务")
        if r.task in rows:
            raise LifeContractError(
                f"第 {m} 月：资源账里家务 {r.task} 有两行——同一项家务只能有一个本人残余，不能分两次减负"
            )
        rows[r.task] = r
    reliefs: list[TaskRelief] = []
    missing: list[str] = []
    total = Fraction(0)
    for task in cv.tasks:
        declared = rules.task(task)
        if declared is None:  # 加载与评分前检查（convenience_protocol_problems）已经挡住
            raise LifeContractError(f"{comp.component_id}: 家务 {task} 在协议里没有基准小时")
        row = rows.get(task)
        if row is None:
            missing.append(task)
            continue
        baseline = to_fraction(declared.baseline_hours_per_month)
        residual = row.hours.fraction()
        if residual < 0 or residual > baseline:
            raise LifeContractError(
                f"第 {m} 月：家务 {task} 的本人残余 {_fraction_text(residual)} 小时不在 0 与基准 "
                f"{_fraction_text(baseline)} 小时之间——记录越界"
            )
        relief = baseline - residual
        total += relief
        reliefs.append(
            TaskRelief(
                task=task,
                baseline_hours=ExactAmount.of(baseline),
                residual_hours=ExactAmount.of(residual),
                relief_hours=ExactAmount.of(relief),
                opportunity_id=row.opportunity_id,
            )
        )
    if missing:
        return unknown(
            f"资源账第 {m} 月没有家务 {missing} 的行：本人残余小时未知（不当 0，也不当基准）"
        )
    tau = to_fraction(comp.tau)
    cap = to_fraction(cv.cap)
    raw = total / (total + tau)
    s_value = min(cap, raw)
    if total == 0:
        why = "这个月没有在用的家务替代：本人全部自己做，减少 0 小时（确定的 0，不是数据缺失）"
    else:
        by = sorted({r.opportunity_id for r in reliefs if r.opportunity_id is not None})
        why = (
            f"这个月本人少做 {_fraction_hours(total)} 小时家务（由 {by} 承担）：s = min(c, q/(q + τ))；"
            "同一项家务只取残余最小的那个方案，成交与花钱不另计分"
        )
    return ComponentMonth(
        **head,
        status="known",
        cap=cv.cap,
        z=float(total),
        raw=float(raw),
        s=float(s_value),
        capped=raw > cap,
        reasons=(why,),
        convenience=ConvenienceTerm(
            tasks=tuple(reliefs),
            relief_hours=ExactAmount.of(total),
            tau_hours=comp.tau,
            cap=cv.cap,
        ),
    )


def _score_component(
    comp: ScoringComponent,
    cond: CityComponentCondition,
    in_use: list[_InUse],
    jobs: list[LifeOpportunity] | None = None,
    supply: dict[str, str] | None = None,
    growth: MonthGrowth | None = None,
    resources: MonthResources | None = None,
    rules: MonthlyResourceRules | None = None,
) -> ComponentMonth:
    """一个分项一个月的评分。``supply`` 是这座城各项功能的供给声明（功能 → 状态）；``growth`` 是这个月的
    成长（只有成长项分项用）；``resources`` 是这个月的资源账（广义交通读其中的到岗次数，日常生活便利读其中的家务行）；
    ``rules`` 是协议的月度资源规则（日常生活便利读其中的家务基准）。"""
    if comp.plan_commute is not None:
        return _score_plan_commute(comp, cond, in_use, jobs or [])
    if comp.transport is not None:
        return _score_transport(comp, cond, in_use, resources)
    if comp.convenience is not None:
        return _score_convenience(comp, cond, resources, rules)
    if comp.growth is not None:
        if growth is None:
            raise LifeContractError(f"{comp.component_id}: 成长项没有拿到这个月的学习进度")
        return _score_growth(comp, cond, growth)
    head = _head(comp)
    if cond.status == "unknown":
        return ComponentMonth(
            **head, status="unknown", reasons=(f"城市条件未知：{cond.unknown_reason}",)
        )
    assert cond.base is not None and cond.cap is not None
    terms: list[ImprovementTerm] = []
    unknown: list[str] = []
    for src in comp.sources:
        eta = cond.eta_any if comp.combine == "any" else cond.eta_for(src.source_id)
        assert eta is not None  # condition_problems 已检查
        before = len(terms)
        for r in in_use:
            if not source_candidate(src, r.record, r.opp):
                continue
            x, problem = source_reading(src, r.opp, r.quantity)
            if x is None:
                assert problem is not None
                unknown.append(problem)
                continue
            if x < 0:
                raise LifeContractError(
                    f"{r.opp.opportunity_id} 给 {comp.component_id} 的取值为负：负向作用本轮不支持"
                )
            terms.append(
                ImprovementTerm(
                    source_id=src.source_id,
                    record=r.record,  # type: ignore[arg-type]
                    opportunity_id=r.opp.opportunity_id,
                    x=x,
                    eta=eta,
                    contribution=eta * x,
                    counted=False,
                )
            )
        gaps = unconfirmed_functions(src, supply or {})
        if gaps and len(terms) == before:
            unknown.append(
                f"功能 {gaps} 的供给资料未知或没有声明：这个月没有在用的物品，不能当成确定没有"
            )

    reasons: list[str] = []
    if comp.base_only:
        reasons.append("当前没有行动改善机制：只有城市基础条件")
    counted: set[int] = set()
    if comp.combine == "sum":
        if unknown:
            return ComponentMonth(
                **head, status="unknown", base=cond.base, cap=cond.cap, reasons=tuple(unknown)
            )
        counted = set(range(len(terms)))
    elif comp.combine == "max":
        if unknown:
            return ComponentMonth(
                **head, status="unknown", base=cond.base, cap=cond.cap, reasons=tuple(unknown)
            )
        if terms:
            best = max(range(len(terms)), key=lambda i: (terms[i].contribution, -i))
            counted = {best}
            if len(terms) > 1:
                reasons.append("max：只取最好的一项来源")
    else:  # any：布尔替代
        first = next((i for i, t in enumerate(terms) if t.x == 1.0), None)
        if first is not None:
            counted = {first}
            if sum(1 for t in terms if t.x == 1.0) > 1:
                reasons.append("any：同一需求多处满足只计一次")
        elif unknown:
            return ComponentMonth(
                **head, status="unknown", base=cond.base, cap=cond.cap, reasons=tuple(unknown)
            )
    terms = [t.model_copy(update={"counted": i in counted}) for i, t in enumerate(terms)]
    z = cond.base + math.fsum(t.contribution for t in terms if t.counted)
    raw, s, capped = saturating(z, comp.tau, cond.cap)
    landscape: LandscapeTerm | None = None
    if comp.landscape is not None:
        beta = comp.landscape.beta
        if beta == 0:
            reasons.append("景观加分关闭（β = 0 对照）：环境照基础规则")
        else:
            nature = cond.nature
            if nature is None or nature.status == "unknown":
                why = nature.unknown_reason if nature is not None else "城市没有写景观数据"
                return ComponentMonth(
                    **head,
                    status="unknown",
                    base=cond.base,
                    cap=cond.cap,
                    terms=tuple(terms),
                    z=z,
                    raw=raw,
                    reasons=(f"景观数据未知（β > 0 时缺失不当零）：{why}",),
                )
            assert nature.a_nature is not None
            bonus = beta * nature.a_nature
            s = min(cond.cap, raw + bonus)
            capped = raw + bonus > cond.cap
            landscape = LandscapeTerm(
                beta=beta,
                a_nature=nature.a_nature,
                physical=raw,
                contribution=bonus,
                source_kind=nature.source.kind,
            )
            reasons.append("环境 = min(c, 基础 b/(b+τ) + β·A_nature)；景观不含购物、古迹或游客数")
    return ComponentMonth(
        **head,
        status="known",
        base=cond.base,
        cap=cond.cap,
        terms=tuple(terms),
        z=z,
        raw=raw,
        s=s,
        capped=capped,
        reasons=tuple(reasons),
        landscape=landscape,
    )


# ==========================================================================
# 七维与月度分
# ==========================================================================


def _aggregate(
    spec: LifeScoringSpec, comps: dict[str, ComponentMonth], pref: ScoringPreference
) -> tuple[list[DimensionMonth], float | None, list[str]]:
    w = effective_weights(spec, pref)  # 旧版本：七维原样；健康版本：有效六维权重（E1-5C）
    reasons: list[str] = []
    blocking = False

    mat_by_need = {c.material_need: comps[c.component_id] for c in spec.material_components()}
    needs = [c.material_need for c in spec.material_components()]
    needs += sorted(n for n in pref.material_weights if n not in mat_by_need)
    terms: list[MaterialTerm] = []
    for need in needs:
        assert need is not None
        m = pref.material_weights.get(need, 0.0)
        comp = mat_by_need.get(need)
        if comp is None:
            terms.append(MaterialTerm(need=need, weight=m, status="no_component"))
        elif comp.status == "unknown":
            terms.append(MaterialTerm(need=need, weight=m, status="unknown"))
        else:
            assert comp.s is not None
            terms.append(
                MaterialTerm(need=need, weight=m, status="known", s=comp.s, contribution=m * comp.s)
            )
    missing = [t for t in terms if t.weight > 0 and t.status != "known"]
    if missing:
        s_mat = None
        if w[MATERIAL] > 0:
            blocking = True
            reasons.append(
                f"物质细项 {[t.need for t in missing]} 有正权重但"
                f"{'没有评分分项' if any(t.status == 'no_component' for t in missing) else '数据未知'}"
            )
    else:
        s_mat = math.fsum(
            t.contribution for t in terms if t.weight > 0 and t.contribution is not None
        )
    dims = [
        DimensionMonth(
            dimension=MATERIAL,
            weight=w[MATERIAL],
            status="known"
            if s_mat is not None
            else ("unknown" if w[MATERIAL] > 0 else "not_weighted_unknown"),
            s=s_mat,
            contribution=None if s_mat is None else w[MATERIAL] * s_mat,
            material_terms=tuple(terms),
        )
    ]
    for dim in spec.scored_amenities:
        comp = comps[spec.dimension_component(dim).component_id]
        if comp.status == "known":
            assert comp.s is not None
            dims.append(
                DimensionMonth(
                    dimension=dim,
                    weight=w[dim],
                    status="known",
                    s=comp.s,
                    contribution=w[dim] * comp.s,
                )
            )
        else:
            dims.append(
                DimensionMonth(
                    dimension=dim,
                    weight=w[dim],
                    status="unknown" if w[dim] > 0 else "not_weighted_unknown",
                )
            )
            if w[dim] > 0:
                blocking = True
                reasons.append(f"{dim} 有正权重 {w[dim]:g} 但数据未知")
    if blocking:
        return dims, None, reasons
    return dims, math.fsum(d.contribution for d in dims if d.contribution is not None), reasons


# ==========================================================================
# 硬要求
# ==========================================================================


def _item_meets(cond: dict[str, object], opp: LifeOpportunity) -> str:
    """一个在用项目是否满足一组条件：yes / no / unknown（规格缺要比较的键）。"""
    missing = False
    for key, want in cond.items():
        if key == "category":
            continue
        if key.startswith(("min_", "max_")):
            have = opp.spec.get(key[4:])
            hv, wv = _num(have), _num(want)
            if hv is None or wv is None:
                missing = True
                continue
            if (key.startswith("min_") and hv < wv) or (key.startswith("max_") and hv > wv):
                return "no"
        else:
            have = opp.spec.get(key)
            if have is None:
                missing = True
                continue
            if have != want:
                return "no"
    return "unknown" if missing else "yes"


def _condition_status(
    cond: dict[str, object], in_use: list[_InUse]
) -> tuple[str, tuple[str, ...], str | None]:
    category = cond.get("category")
    if category is None:
        return "unknown", (), "条件没有声明适用类别（category），本轮不猜"
    candidates = [r for r in in_use if r.opp.category == category]
    if not candidates:
        return "not_satisfied", (), f"这个月没有在用的 {category} 类项目"
    yes: list[str] = []
    unsure: list[str] = []
    for r in candidates:
        verdict = _item_meets(cond, r.opp)
        if verdict == "yes":
            yes.append(r.opp.opportunity_id)
        elif verdict == "unknown":
            unsure.append(r.opp.opportunity_id)
    if yes:
        return "satisfied", tuple(sorted(set(yes))), None
    if unsure:
        return "unknown", (), f"{sorted(set(unsure))} 的规格缺少要比较的键"
    return "not_satisfied", (), None


def _requirement(
    req: HardRequirement, by_month: dict[int, list[_InUse]], lived: list[int]
) -> HardRequirementResult:
    alternatives = [dict(req.requirement)] + [
        dict(s.satisfies_with) for s in req.acceptable_substitutes
    ]
    evidence: list[RequirementEvidence] = []
    for m in range(1, MONTHS_PER_YEAR + 1):
        if m not in lived:
            evidence.append(
                RequirementEvidence(
                    month=m, status="not_lived", reason="出局当月或之后：没有继续生活"
                )
            )
            continue
        results = [_condition_status(a, by_month[m]) for a in alternatives]
        sat = [r for r in results if r[0] == "satisfied"]
        if sat:
            items = tuple(sorted({i for r in sat for i in r[1]}))
            evidence.append(RequirementEvidence(month=m, status="satisfied", items=items))
        elif any(r[0] == "unknown" for r in results):
            reason = next(r[2] for r in results if r[0] == "unknown")
            evidence.append(RequirementEvidence(month=m, status="unknown", reason=reason))
        else:
            evidence.append(RequirementEvidence(month=m, status="not_satisfied"))
    by = {
        s: tuple(e.month for e in evidence if e.status == s)
        for s in ("satisfied", "not_satisfied", "unknown", "not_lived")
    }
    if not lived:
        summary = "not_evaluated"
    elif by["not_satisfied"]:
        summary = "partially_satisfied" if by["satisfied"] else "not_satisfied"
    elif by["unknown"]:
        summary = "undetermined"
    elif by["not_lived"]:
        summary = "satisfied_in_lived_months"
    else:
        summary = "satisfied_every_month"
    return HardRequirementResult(
        need=req.need,
        scope=req.scope,
        months_satisfied=by["satisfied"],
        months_not_satisfied=by["not_satisfied"],
        months_unknown=by["unknown"],
        months_not_lived=by["not_lived"],
        summary=summary,  # type: ignore[arg-type]
        evidence=tuple(evidence),
    )


# ==========================================================================
# 入口
# ==========================================================================


def _check_inputs(
    protocol: LifeProtocol,
    spec: LifeScoringSpec,
    conditions: CityScoringConditions,
    catalog: CityLifeCatalog,
    state: PersonYearState,
    outcome: EnvironmentOutcome,
    preference: ScoringPreference,
    hard_requirements: tuple[HardRequirement, ...],
) -> None:
    for value, cls, name in (
        (protocol, LifeProtocol, "protocol"),
        (spec, LifeScoringSpec, "spec"),
        (conditions, CityScoringConditions, "conditions"),
        (catalog, CityLifeCatalog, "catalog"),
        (state, PersonYearState, "state"),
        (outcome, EnvironmentOutcome, "outcome"),
        (preference, ScoringPreference, "preference"),
    ):
        if type(value) is not cls:
            raise LifeContractError(f"{name} 必须是 {cls.__name__}，得到 {type(value).__name__}")
    if any(type(r) is not HardRequirement for r in hard_requirements):
        raise LifeContractError("硬要求必须是结构化的 HardRequirement")
    if spec.protocol_id != protocol.protocol_id:
        raise LifeContractError(
            f"评分规格对应协议 {spec.protocol_id}，这份记录是 {protocol.protocol_id}"
        )
    problems = condition_problems(spec, conditions)
    if problems:
        raise LifeContractError("城市评分条件不能用：\n  " + "\n  ".join(problems))
    problems = (
        growth_transport_protocol_problems(spec, protocol)
        + health_protocol_problems(spec, protocol)
        + convenience_protocol_problems(spec, protocol)
    )
    if problems:
        raise LifeContractError("评分规格读不到它要的生活事实：\n  " + "\n  ".join(problems))
    problems = convenience_preference_problems(
        spec, f"偏好 {preference.person_id}", preference.material_weights
    )
    if problems:
        raise LifeContractError("偏好与评分版本不配套：\n  " + "\n  ".join(problems))
    if any(c.growth is not None for c in spec.components) and outcome.career is None:
        raise LifeContractError(
            "成长项要读这一年的学习记录：结果没有职业事实，不是 E1-5A 结算写出的记录"
        )
    if any(c.transport is not None for c in spec.components) and outcome.resources is None:
        raise LifeContractError("广义交通要读资源账的到岗次数：结果没有月度资源账")
    if any(c.convenience is not None for c in spec.components) and outcome.resources is None:
        raise LifeContractError("日常生活便利要读资源账的家务小时：结果没有月度资源账")
    health_version = spec.scoring_version in HEALTH_SCORING_VERSIONS
    six_dim = preference.source == PRODUCT_SIX_DIM_SOURCE
    if health_version:
        if six_dim:
            # E3-A：六维产品画像的健康回答是独立问卷——协议必须声明独立健康回答，而不是七维 healthcare 点数的含义
            if not isinstance(protocol.health_input, IndependentHealthInputDeclaration):
                raise LifeContractError(
                    f"这是六维产品画像（健康回答独立），协议 {protocol.protocol_id} 没有声明独立健康回答——不能交给按七维"
                    "医疗点数声明的协议评分"
                )
        elif preference.healthcare_semantics is None:
            # E1-5C：七维 healthcare 必须是新含义（健康风险应对倾向）；健康项读结算写出的月初健康状态
            raise LifeContractError(
                f"评分版本 {spec.scoring_version} 只接受 healthcare 点数含义为健康风险应对倾向的偏好；这份偏好是旧含义"
                "（医疗可及性）——旧答案不能改读成新含义，请用新问卷重新询问"
            )
        if protocol.health_rules is None or outcome.health is None:
            raise LifeContractError(
                "健康评分版本读结算写出的月初健康状态：协议没有疾病规则，或结果没有这一年的健康事实"
            )
    elif six_dim:
        raise LifeContractError(
            f"六维产品画像只能用健康评分版本 {sorted(HEALTH_SCORING_VERSIONS)}；评分版本 {spec.scoring_version} 要七维"
            "原始权重（含医疗点数），不能补一个医疗占位分"
        )
    elif preference.healthcare_semantics is not None:
        raise LifeContractError(
            f"这份偏好的 healthcare 点数是 {preference.healthcare_semantics}（健康风险应对倾向），评分版本 "
            f"{spec.scoring_version} 会把它当成医疗可及性的满足感权重——不能悄悄换含义"
        )
    cities = {str(catalog.city), str(state.city), str(conditions.city)}
    if len(cities) != 1:
        raise LifeContractError(
            f"城市对不上 {sorted(cities)}：不能拿另一座城的生活记录配当前城市的评分资料"
        )
    if state.status != "active":
        raise LifeContractError("评分需要这一年年初的状态（解释已有持有物用）")
    who = (outcome.person_id, outcome.clone_id, outcome.year_index)
    if who != (state.person_id, state.clone_id, state.year_index):
        raise LifeContractError(
            f"结果 {who} 与年初状态 {(state.person_id, state.clone_id, state.year_index)} 不是同一人同一年"
        )
    if preference.person_id != state.person_id:
        raise LifeContractError(f"偏好属于 {preference.person_id}，生活记录属于 {state.person_id}")
    if outcome.settlement_convention is None:
        raise LifeContractError("结果没有结算约定版本：不是由结算产生的记录，不能评分")
    if outcome.money_basis_id != catalog.money_basis.numeraire_id:
        raise LifeContractError("结果的计价口径与城市目录不一致")
    ids = [it.opportunity_id for it in outcome.items] + [
        h.opportunity_id for h in outcome.holdings_service
    ]
    strangers = sorted({i for i in ids if catalog.opportunity(i) is None})
    if strangers:
        raise LifeContractError(f"结果引用了目录里没有的项目 {strangers}：记录与城市不配套")


def score_year(
    *,
    protocol: LifeProtocol,
    spec: LifeScoringSpec,
    conditions: CityScoringConditions,
    catalog: CityLifeCatalog,
    state: PersonYearState,
    outcome: EnvironmentOutcome,
    preference: ScoringPreference,
    hard_requirements: tuple[HardRequirement, ...] = (),
) -> ExploratoryYearScore:
    """对一年已结算的生活做探索月度评分。

    ``state`` 是**这一年年初**的状态（结算的输入），用来解释已有持有物；``outcome`` 是
    这一年的结算结果。签名里没有计划与理由——评分不按原计划、理由文字或"想买什么"算。
    """
    hard_requirements = tuple(hard_requirements)
    _check_inputs(
        protocol, spec, conditions, catalog, state, outcome, preference, hard_requirements
    )
    by_month, quantity_source = _in_use_by_month(spec, outcome, catalog, state)
    jobs = _jobs_by_month(outcome, catalog)
    supply = {fs.function: fs.status for fs in catalog.function_supply}
    exit_month = outcome.exit.month if outcome.exit is not None else None
    with_growth = any(c.growth is not None for c in spec.components)
    with_transport = any(c.transport is not None for c in spec.components)
    with_convenience = any(c.convenience is not None for c in spec.components)
    growth = growth_by_month(protocol, catalog, state, outcome) if with_growth else {}
    resources = (
        {r.month: r for r in outcome.resources.months}
        if (with_transport or with_convenience) and outcome.resources is not None
        else {}
    )

    health_spec = spec.health
    six_dim = preference.source == PRODUCT_SIX_DIM_SOURCE  # E3-A：六维产品画像（健康回答独立）
    stop_month = outcome.stop.month if outcome.stop is not None else None
    months: list[MonthScore] = []
    contributed: set[str] = set()
    for m in range(1, MONTHS_PER_YEAR + 1):
        if stop_month is not None and m >= stop_month:
            months.append(
                MonthScore(
                    month=m,
                    status="stopped_month" if m == stop_month else "after_stop",
                    reasons=(
                        "停止月：疾病之后应急安排不足或资料缺口，这个月的执行没有提交，不评分"
                        if m == stop_month
                        else "停止之后：没有继续模拟",
                    ),
                )
            )
            continue
        if exit_month is not None and m == exit_month:
            months.append(
                MonthScore(
                    month=m,
                    status="exit_month",
                    reasons=("出局当月：工资已入账，但这个月没有过完，不评分",),
                )
            )
            continue
        if exit_month is not None and m > exit_month:
            months.append(
                MonthScore(month=m, status="after_exit", reasons=("出局之后：没有继续模拟",))
            )
            continue
        comps: dict[str, ComponentMonth] = {}
        for comp in spec.components:
            cond = conditions.condition(comp.component_id)
            assert cond is not None
            cm = _score_component(
                comp,
                cond,
                by_month[m],
                jobs[m],
                supply,
                growth=growth.get(m),
                resources=resources.get(m),
                rules=protocol.resource_rules,
            )
            comps[comp.component_id] = cm
            contributed.update(
                t.opportunity_id for t in cm.terms if t.counted and t.contribution > 0
            )
            if cm.convenience is not None:  # 承担了家务替代的设备或服务，这个月算作带来了计入的改善
                contributed.update(
                    r.opportunity_id
                    for r in cm.convenience.tasks
                    if r.opportunity_id is not None and r.relief_hours.fraction() > 0
                )
        dims, u, reasons = _aggregate(spec, comps, preference)
        daily_life: float | None = None
        health_term: HealthTerm | None = None
        if health_spec is not None:
            # E1-5C：S = (1 − γ)·B + γ·H(月初状态)。B 不完整时总分照旧为空（不补零、不重新归一化）
            assert outcome.health is not None
            record = outcome.health.month(m)
            if record is None:
                raise LifeContractError(
                    f"第 {m} 月没有健康记录：结果不是健康协议结算写出的完整记录"
                )
            gamma = health_spec.gamma
            value = health_spec.state_values.of(record.state)
            health_term = HealthTerm(
                state=record.state,
                value=value,
                gamma=gamma,
                contribution=gamma * value,
                # 相对健康的扣分：H(健康) 固定为 1（契约保证，收尾修订），健康月份扣 0
                deduction=gamma * (1 - value),
                daily_life_share=1 - gamma,
            )
            if u is not None:
                daily_life = u
                u = (1 - gamma) * daily_life + gamma * value
        months.append(
            MonthScore(
                month=m,
                status="scored" if u is not None else "incomplete",
                u=u,
                dimensions=tuple(dims),
                components=tuple(comps[c.component_id] for c in spec.components),
                reasons=tuple(reasons),
                daily_life=daily_life,
                health=health_term,
            )
        )

    def _months(status: str) -> tuple[int, ...]:
        return tuple(x.month for x in months if x.status == status)

    if exit_month is not None:
        annual_status, annual_mean = "exited", None
    elif stop_month is not None:
        annual_status, annual_mean = "stopped", None
    elif _months("incomplete"):
        annual_status, annual_mean = "incomplete_months", None
    else:
        annual_status = "complete"
        annual_mean = math.fsum(x.u for x in months if x.u is not None) / MONTHS_PER_YEAR

    lived = [x.month for x in months if x.status in ("scored", "incomplete")]
    requirements = tuple(_requirement(r, by_month, lived) for r in hard_requirements)

    notes = [
        "探索评分（合成参数，ADR0018）：不是现实城市的幸福估计，不能据此排序城市",
        "工资、付款与现金余额本身不产生满足感；发展维按当月实际新增的能力进度计成长（合成探索参数 η_growth，"
        "DECISIONS/0024），证书本身、换岗、职位名称与涨薪不另加分；不折现、不计终值、不跨分身平均"
        if with_growth
        else "工资、付款与现金余额本身不产生满足感；培训本轮不计收益（E1-5）；不折现、不计终值、不跨分身平均",
    ]
    base_only = [c.dimension for c in spec.components if c.base_only]
    if base_only:
        notes.append(f"维度 {base_only} 只有城市基础条件：当前没有行动改善机制")
    in_use_ids = {r.opp.opportunity_id for m in lived for r in by_month[m]}
    idle = sorted(in_use_ids - contributed)
    if idle:
        notes.append(
            "在用但全年没有带来任何计入的改善（没有对应评分来源，例如没声明使用安排的会员卡；"
            f"或取值为 0；或同一需求已由别处满足）：{idle}"
        )
    attribute_keys = sorted({k for m in lived for r in by_month[m] for k in r.opp.attributes})
    if attribute_keys:
        notes.append(
            f"方案属性 {attribute_keys} 已记录、本轮未评价（V2 才做品牌/菜系/档次匹配）；不从价格推断品质偏好"
        )
    if any(c.plan_commute is not None for c in spec.components):
        notes.append(
            "通勤按实际所住住房的往返分钟与当月线下工作评分；乘工作日数后的时间占用留 E1-4"
        )
    if with_transport:
        notes.append(
            "交通（通勤维，交通便捷程度）：k = ρ + (1 − ρ)·D/D_ref，T_eq = k·T 是评分中间量、不是实际出行分钟；"
            "D 读资源账的到岗次数；工作通勤的时间与负担账不变；非工作出行没有细化（DECISIONS/0024）"
        )
    if with_convenience:
        notes.append(
            "日常生活便利（DECISIONS/0033）：q = Σ 家务（协议基准 − 当月资源账里本人的残余小时），s = min(c, q/(q + τ))；"
            "设备与人工服务只通过实际减少的小时起作用，成交、花钱与目标兑现不另计分；省下的时间月底照样作废，不算休息或满足感；"
            "这是代表性杂务组合（不含做饭、通勤、工作、学习与疾病恢复），不是完整的物质生活评价"
        )
    if spec.omissions:
        notes.append(f"省略清单 {len(spec.omissions)} 条（省略了什么、影响谁）见评分规格与覆盖报告")
    if exit_month is not None:
        notes.append(
            f"第 {exit_month} 月出局：出局当月与之后不评分，也不当普通缺失跳过；完整轨迹怎么计留到 E1-6"
        )
    if annual_status == "incomplete_months":
        notes.append(
            f"第 {list(_months('incomplete'))} 月有正权重的分项数据未知：不生成总分，不补零，不重新归一化"
        )
    weighting: HealthWeighting | None = None
    if health_spec is not None and six_dim:
        # E3-A：六维产品画像——原始权重就是确认点数 / 100（六维），健康回答独立；不伪造医疗点数
        effective = effective_weights(spec, preference)
        weighting = HealthWeighting(
            raw_dimension_weights=dict(preference.dimension_weights),
            six_dimension_sum=float(six_dimension_total(preference.dimension_weights)),
            effective_weights=effective,
            gamma=health_spec.gamma,
            state_values=health_spec.state_values,
            formula=(
                "S = (1 − γ)·B + γ·H(月初状态)；B = Σ 六维权重 × 维度分；六维权重 = 确认的六维精确点数 / 100（健康回答独立，"
                "不占点数、不进权重）"
            ),
            independent_health_input="independent_health_answers_v1",
        )
        notes.append(
            "健康评分版本（DECISIONS/0027、0034）：六维产品画像的健康回答是独立问卷，只供分身做计划、不进满足感；城市医疗背景不进"
            f"满足感；健康项 γ = {health_spec.gamma:g} 读月初状态、全组共用；付医疗费不加分"
        )
    elif health_spec is not None:
        assert preference.healthcare_semantics is not None
        effective = effective_weights(spec, preference)
        weighting = HealthWeighting(
            raw_dimension_weights=dict(preference.dimension_weights),
            healthcare_semantics=preference.healthcare_semantics,
            six_dimension_sum=float(six_dimension_total(preference.dimension_weights)),
            effective_weights=effective,
            gamma=health_spec.gamma,
            state_values=health_spec.state_values,
            formula="S = (1 − γ)·B + γ·H(月初状态)；B = Σ 有效六维权重 × 维度分；有效权重 = w_k / Σ_{j≠医疗} w_j",
        )
        notes.append(
            "健康评分版本（DECISIONS/0027）：医疗点数表示健康风险应对倾向、不进满足感；城市医疗背景不进满足感；"
            f"健康项 γ = {health_spec.gamma:g} 读月初状态、全组共用；付医疗费不加分。这不是旧的七维分"
        )
    if stop_month is not None:
        notes.append(
            f"第 {stop_month} 月停止（应急安排不足或资料缺口）：停止月与之后不评分，也不当普通缺失跳过；"
            "完整轨迹怎么计留到 E1-6"
        )

    if preference.source in ("product_stated_numeric", PRODUCT_SIX_DIM_SOURCE):
        explanation = (
            "按你提供的偏好和当前探索模型计算；这不是你真实的幸福权重，模型参数是合成探索设定"
        )
    else:
        explanation = "按合成设定的已知偏好计算（研究内部）；模型参数是合成探索设定，不是现实标定"

    return ExploratoryYearScore(
        person_id=state.person_id,
        clone_id=state.clone_id,
        year_index=state.year_index,
        city=state.city,
        spec_id=spec.spec_id,
        scoring_version=spec.scoring_version,
        scoring_function=spec.function,
        status="exploratory_synthetic",
        formal_scoring_ready=protocol.scoring_ready,
        preference_source=preference.source,
        preference_derivation=preference.derivation,
        months=tuple(months),
        annual_mean=annual_mean,
        annual_status=annual_status,  # type: ignore[arg-type]
        months_scored=_months("scored"),
        months_incomplete=_months("incomplete"),
        months_exit=_months("exit_month"),
        months_after_exit=_months("after_exit"),
        hard_requirements=requirements,
        explanation=explanation,
        notes=tuple(notes),
        holding_quantity_source=quantity_source,  # type: ignore[arg-type]
        unevaluated_attributes=tuple(attribute_keys),
        months_stopped=_months("stopped_month"),
        months_after_stop=_months("after_stop"),
        health_weighting=weighting,
    )


MEASURE_LABEL = {
    "presence": "存在型（件数不进入）",
    "capacity": "容量型（规格容量 × 件数）",
    "level": "水平（取最好的一项，件数不进入）",
    "count": "次数（一次体验算一次）",
}


def unit_path(source_unit: str, component_unit: str) -> str:
    """读取单位 → 分项单位，以及登记过的换算系数（η = 换算 × 效率，这里只写出换算那一半）。"""
    su, cu = resolve_unit(source_unit), resolve_unit(component_unit)
    if su is None or cu is None:
        return f"{source_unit} → {component_unit}（未按规范单位登记：ADR0018 旧版本）"
    factor = conversion_factor(su, cu)
    if su.unit_id == cu.unit_id or factor is None:
        return f"{su.label} → {cu.label}"
    return f"{su.label} → {cu.label}（1 {su.label} = {factor:g} {cu.label}）"


def source_table(spec: LifeScoringSpec) -> tuple[dict[str, str], ...]:
    """评分来源表：每个分项的每类来源读哪个字段、取什么值、单位、怎么合并。"""
    rows: list[dict[str, str]] = []
    for c in spec.components:
        if c.growth is not None:
            gs = c.growth
            rows.append(
                {
                    "component": c.component_id,
                    "dimension": c.dimension,
                    "source": "growth",
                    "read_from": (
                        "年初状态的已有能力与各课程进度（含暂停的课程）；同一次结算的"
                        " EnvironmentOutcome.career.learning（实际参加月份）；目录里课程的 grants"
                    ),
                    "value": (
                        f"g_m = Σ_a (p_a,m − p_a,m−1)，z = b + η_growth·g_m，η_growth = {gs.eta:g}（全组共用）；"
                        "没有学习的月份 g = 0"
                    ),
                    "unit": f"{gs.unit} → {c.unit}（η_growth 是效果系数，不是单位换算）",
                    "combine": "—",
                }
            )
            continue
        if c.convenience is not None:
            cv = c.convenience
            rows.append(
                {
                    "component": c.component_id,
                    "dimension": c.dimension,
                    "source": "convenience",
                    "read_from": (
                        f"协议 resource_rules.household_tasks 的基准小时（{list(cv.tasks)}）；同月资源账 "
                        "MonthResources.contributions 里 category = household_task 那几行的本人小时（结算按同一任务取残余最小的"
                        "在用方案写出）"
                    ),
                    "value": (
                        f"q = Σ（基准 − 残余），s = min(c, q/(q + τ))，τ = {c.tau:g}、c = {cv.cap:g}（全组共用）；"
                        "不读价格、付款或成交"
                    ),
                    "unit": cv.unit,
                    "combine": "—",
                }
            )
            continue
        if c.transport is not None:
            ts = c.transport
            rows.append(
                {
                    "component": c.component_id,
                    "dimension": c.dimension,
                    "source": "transport",
                    "read_from": (
                        f"ItemExecution.covered_months（role=housing，含兜底那条）的规格 {ts.minutes_key}；"
                        "同月资源账 MonthResources.onsite_visits（时间账用的同一个到岗计数）"
                    ),
                    "value": (
                        f"k = ρ + (1 − ρ)·D/D_ref（ρ = {ts.rho:g}，D_ref = {ts.reference_visits}），T_eq = k·T，"
                        f"S = τ_T/(τ_T + T_eq)，τ_T = {c.tau:g}；T_eq 是评分中间量"
                    ),
                    "unit": ts.unit,
                    "combine": "—",
                }
            )
            continue
        if c.landscape is not None:
            rows.append(
                {
                    "component": c.component_id,
                    "dimension": c.dimension,
                    "source": "landscape",
                    "read_from": "城市条件的景观指标 nature.a_nature（0–1，带来源）",
                    "value": f"β·A_nature，β = {c.landscape.beta:g}（全组共用）；加在 b/(b+τ) 上再截顶",
                    "unit": "0–1",
                    "combine": "—",
                }
            )
        if c.plan_commute is not None:
            pc = c.plan_commute
            rows.append(
                {
                    "component": c.component_id,
                    "dimension": c.dimension,
                    "source": "plan_commute",
                    "read_from": (
                        f"ItemExecution.covered_months（role=housing，含兜底那条）的规格 {pc.minutes_key}；"
                        f"同月 ItemExecution（role=job）的规格 {pc.work_mode_key}"
                    ),
                    "value": (
                        f"τ_T/(τ_T + T)，τ_T = {c.tau:g}；只在恰好一份 {pc.work_mode_key} = "
                        f"{pc.onsite_value!r} 的工作时评分"
                    ),
                    "unit": pc.unit,
                    "combine": "—",
                }
            )
            continue
        if c.base_only:
            rows.append(
                {
                    "component": c.component_id,
                    "dimension": c.dimension,
                    "source": "（无）",
                    "read_from": "只有城市基础条件 b",
                    "value": "—",
                    "unit": c.unit,
                    "combine": "—",
                }
            )
            continue
        for s in c.sources:
            value = {
                "spec_number": f"规格 {s.spec_key}",
                "spec_flag": f"规格 {s.spec_key} == {s.flag_value!r}",
                "quantity": "数量",
                "present": "存在（这个月有在用的候选就取 1）",
            }[s.value]
            if s.per_unit:
                value += " × 数量"
            if s.functions:
                value += f"（只看声明了功能 {list(s.functions)} 的项目）"
            if s.requires_spec_key:
                value += f"（只看声明了 {s.requires_spec_key} 的项目）"
            if s.measure is not None:
                value += f"；数量语义 {MEASURE_LABEL[s.measure]}"
            rows.append(
                {
                    "component": c.component_id,
                    "dimension": c.dimension,
                    "source": s.source_id,
                    "read_from": "；".join(READ_FROM[r] for r in s.records),
                    "value": value,
                    "unit": unit_path(s.unit, c.unit),
                    "combine": c.combine,
                }
            )
    return tuple(rows)
