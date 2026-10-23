"""偏好覆盖报告（E1-3 增补，`DECISIONS/0019` 第六节；2026-10-08 订正为 coverage-v2，第十二节）。

纯函数；**只报告，不解锁总分**。

覆盖算到偏好叶子：非物质维的叶子权重 a = w_k，物质细项 a = w_material × m_j。J 是全部候选城市都
支持的叶子，C_i = Σ_{ℓ∈J} a_iℓ。v2 把"支持"拆成三层，分开写：

1. **规则覆盖**：评分规格有没有定义这项需求怎样评分（没有分项 = 未支持的需求）；
2. **城市资料覆盖**：比较组每座城是否提供了这条规则**真正读到的事实**——候选机会的规格键
   （面积、储物容量……）、功能的供给声明（可得 / 确定不可得 / 资料未知）、景观指标、住房的往返
   分钟与工作方式、城市评分条件本身。候选怎样认、规格怎样读，用的是评分器同一份函数
   （``econ.life_scoring.source_candidate`` / ``source_reading``），两边不会各自漂移；
3. **实际行动可评分性**：资料齐全时，是不是任何实际安排都能评。

来源也分两类：**生活事实**（目录、功能供给、景观、住房与工作）与**评分映射参数**（城市评分条件
里的 b、c、η）。参数标成估计，不会让合成的住房、商品或通勤资料变成真实资料；只要依赖里还有合成
设定，标签就不是 data_coverage。

**不设任何百分比阈值。** 覆盖再高也不解锁完整总分：正权重未知时月度总分照旧为空、不补零、不按城
重加权；账务资料与硬要求另行检查，不被覆盖比例豁免。

E1-5C 健康评分版本（`DECISIONS/0027`）：月度总分 = (1 − γ)·B + γ·H，所以叶子权重 a = (1 − γ) × 有效六维权重
（物质细项再乘细项权重），另加一片"健康项"叶子，权重 γ——它读结算写出的月初健康状态，不需要城市资料；医疗维没有
分项，也不再按城市医疗背景报覆盖。

日常生活便利（`DECISIONS/0033`）：读协议的家务基准与资源账里实际的本人家务小时，任何实际安排都能评（没有采用方案就是确定的 0）；
城市资料只决定能选哪些设备或服务，它们的资源事实（残余小时）记作事实来源。

生活扩展（`DECISIONS/0041` R4）：饮食生活读实际执行的完整食品方案，可选的食品方案缺事实（没有方案事实、新鲜餐数未知、用户确认了
菜系而菜系未知）就是资料缺口；个人消费与享受读在用东西的享受事实，词表外的功能是“模型未支持”（单独列出，不是缺口也不是 0）；
休闲的实际参与读活动事实，既没有活动事实也没有享受事实的体验是缺口。判断与评分器共用同一份函数（``food_plan_gaps``、
``experience_gap``、``unsupported_enjoyment``）。覆盖说的是**可选目录**是否齐全；某条实际生活没有用到缺口方案时照样可评分。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from common.life_contracts import CityLifeCatalog, LifeContractError
from common.life_scoring_contracts import (
    CityComponentCondition,
    CityScoringConditions,
    ComparisonGroup,
    CoverageReport,
    LeafCoverage,
    LifeScoringSpec,
    ScoringComponent,
    ScoringPreference,
)
from econ.life_scoring import (
    RECORD_OF_ROLE,
    effective_weights,
    experience_gap,
    food_plan_gaps,
    source_candidate,
    source_reading,
    unsupported_enjoyment,
)
from ledger.life_catalog import item_role
from worlds.dimensions import MATERIAL

REPORT_VERSION = "coverage-v2"
_DIRECT = {"observed", "derived"}


def _evidence(kinds: list[str]) -> str:
    if not kinds:
        return "none"
    if all(k == "synthetic" for k in kinds):
        return "synthetic_only"
    if all(k in _DIRECT for k in kinds):
        return "observed_or_derived"
    if all(k == "estimated" for k in kinds):
        return "estimated"
    return "mixed"


def _city_dependencies(
    comp: ScoringComponent,
    cond: CityComponentCondition | None,
    catalog: CityLifeCatalog,
    cuisines: frozenset[str] = frozenset(),
) -> tuple[list[str], list[str], list[str], list[str]]:
    """一座城在一个分项上：（资料缺口，生活事实的来源类型，评分参数的来源类型，模型未支持的事实）。"""
    city = str(catalog.city)
    gaps: list[str] = []
    facts: list[str] = []
    params: list[str] = []
    unsupported: list[str] = []
    if cond is None or cond.status == "unknown":
        why = cond.unknown_reason if cond is not None else "没有评分条件"
        return [f"{city}: 城市评分条件未知（{why}）"], facts, params, unsupported
    params.append(cond.source.kind)
    beta = comp.landscape.beta if comp.landscape is not None else 0.0
    if beta > 0:
        if cond.nature is None or cond.nature.status == "unknown":
            gaps.append(f"{city}: 景观数据未知（β > 0 时缺失不当零）")
        else:
            facts.append(cond.nature.source.kind)
    available = [o for o in catalog.opportunities if o.availability == "available"]
    ts = comp.transport
    if ts is not None:
        # 广义交通（`DECISIONS/0024`）：每个可得住房的往返分钟，每份可得工作的岗位方案（到岗次数由资源账记）
        if cond.status != "plan_level":
            gaps.append(f"{city}: 广义交通要求城市条件写 plan_level")
        for o in available:
            role = item_role(catalog, o)
            if role == "housing":
                area = catalog.area(o.area) if o.area is not None else None
                if area is None or area.typical_roundtrip_minutes is None:
                    gaps.append(f"{city}/{o.opportunity_id}: 住处片区缺居民平时的往返分钟")
                else:
                    assert area.typical_source is not None
                    facts.append(area.typical_source.kind)
            elif role == "job":
                if o.resource is None or o.resource.job is None:
                    gaps.append(f"{city}/{o.opportunity_id}: 缺岗位方案（到岗次数）")
                else:
                    facts.append(o.source.kind)
    if comp.growth is not None:
        # 成长读能力词表与课程授予：声明了授予的课程是事实来源。没有这样的课程时成长恒为 0（已知），不是缺口
        facts += [o.source.kind for o in available if o.grants]
    cv = comp.convenience
    if cv is not None:
        # 日常生活便利（`DECISIONS/0033`）读协议的家务基准与资源账里实际的本人家务小时：任何实际安排都有确定的值（没有采用
        # 方案就是 0），城市资料只决定能选什么——每个能替代这组家务的可得方案的资源事实（残余小时）是事实来源
        if cond.status != "plan_level":
            gaps.append(f"{city}: 日常生活便利要求城市条件写 plan_level")
        for o in available:
            if o.resource is not None and any(s.task in cv.tasks for s in o.resource.substitutes):
                facts.append(o.source.kind)
    if comp.food_life is not None:
        # 饮食生活读这个月实际吃的完整方案：每份可选的食品方案都要有评分读到的事实（与评分器同一份判断）
        if cond.status != "plan_level":
            gaps.append(f"{city}: 饮食生活要求城市条件写 plan_level")
        for o in available:
            if item_role(catalog, o) != "food":
                continue
            missing = food_plan_gaps(o, cuisines)
            if missing:
                gaps += [f"{city}/{g}" for g in missing]
            else:
                facts.append(o.source.kind)
    if comp.enjoyment is not None:
        # 个人消费与享受读在用东西的享受事实；词表外的功能是模型未支持（不是资料缺口，也不当 0）
        if cond.status != "plan_level":
            gaps.append(f"{city}: 个人消费与享受要求城市条件写 plan_level")
        for o in available:
            if not o.enjoyment:
                continue
            unsupported += [f"{city}/{u}" for u in unsupported_enjoyment(comp.enjoyment, o)]
            facts.append(o.source.kind)
    if comp.participation is not None:
        # 休闲的实际参与读活动事实；实际参加时无法评价的体验是缺口（与评分器同一份判断）
        for o in available:
            if item_role(catalog, o) != "experience":
                continue
            gap = experience_gap(o)
            if gap is not None:
                gaps.append(f"{city}/{gap}")
            elif o.activity is not None:
                facts.append(o.source.kind)
    for src in comp.sources:
        for o in available:
            record = RECORD_OF_ROLE.get(item_role(catalog, o))
            if record is None or not source_candidate(src, record, o):
                continue
            x, problem = source_reading(src, o, 1.0)
            if x is None:
                gaps.append(f"{city}/{o.opportunity_id}: {problem}")
            else:
                facts.append(o.source.kind)
        for f in src.functions:
            fs = catalog.supply_for(f)
            if fs is None:
                gaps.append(f"{city}: 功能 {f} 没有供给声明（没有记录不等于确定没有）")
            elif fs.status == "unknown":
                gaps.append(f"{city}: 功能 {f} 的供给资料未知或未完成采集")
                facts.append(fs.source.kind)
            else:
                facts.append(fs.source.kind)
    if comp.base_only or comp.growth is not None:
        # 只有城市背景（成长项的 b 也是城市背景）：背景值 b 与映射参数 c 同记在一条评分条件里，来源只能一起报
        # （分开属于 E1-7 前的"城市可见事实与隐藏评分参数分开"任务）。
        facts.append(cond.source.kind)
    return gaps, facts, params, unsupported


def _leaf(
    leaf: str,
    dimension: str,
    weight: float,
    comp: ScoringComponent | None,
    conditions: Sequence[CityScoringConditions],
    catalogs: dict[str, CityLifeCatalog],
    cuisines: frozenset[str] = frozenset(),
) -> LeafCoverage:
    if comp is None:
        return LeafCoverage(
            leaf=leaf,
            dimension=dimension,
            weight=weight,
            support="no_component",
            evidence="none",
            action_mechanism=False,
            reasons=("评分规格没有这项需求的分项：未支持的需求",),
            rule="no_component",
            city_data="not_applicable",
            action_scope="not_applicable",
            fact_evidence="none",
            parameter_evidence="none",
        )
    gaps: list[str] = []
    facts: list[str] = []
    params: list[str] = []
    unknown: set[str] = set()
    unsupported: list[str] = []
    for cc in conditions:
        g, f, p, u = _city_dependencies(
            comp, cc.condition(comp.component_id), catalogs[str(cc.city)], cuisines
        )
        if g:
            unknown.add(str(cc.city))
        gaps += g
        facts += f
        params += p
        unsupported += u
    reasons: list[str] = []
    action = (
        bool(comp.sources)
        or comp.transport is not None
        or comp.growth is not None
        or comp.convenience is not None
        or comp.food_life is not None
        or comp.enjoyment is not None
        or comp.participation is not None
    )
    if not action:
        reasons.append("只有城市背景：背景已知不代表行动效果也建模了")
    if comp.base_only:
        reasons.append("背景值 b 与映射参数同记在评分条件里，事实来源与参数来源只能一起报告")
    if comp.combine == "any" and gaps:
        reasons.append(
            "any 替代：某个替代方案的资料有缺口时，实际安排若用了资料完整的另一个替代，这个月仍可评分"
        )
    if comp.transport is not None:
        reasons.append(
            "广义交通：无工作、远程、混合与多岗位都按资源账的累计到岗次数评分——城市资料齐全时任何实际安排"
            "都能评；住房的工作往返分钟代理广义出行条件，非工作出行（购物、社交、就医）没有细化"
        )
    if comp.growth is not None:
        reasons.append(
            "发展维 = 城市背景 b + η_growth × 当月实际新增的能力进度；没有学习的月份成长为 0（已知，不是缺失）；"
            "背景与个人成长分开登记，背景不由本人的学习结果回填"
        )
    if comp.convenience is not None:
        reasons.append(
            "日常生活便利读资源账里实际减少的家务小时：任何实际安排都能评，没有采用方案时是确定的 0（不是未知）；资料未知的方案"
            "分身选不了，不影响已发生月份的评分；任务组合是代表性的（不含做饭、通勤、工作、学习与疾病恢复）"
        )
    if comp.food_life is not None:
        reasons.append(
            "饮食生活读这个月实际执行的完整食品方案（新鲜餐与确认菜系的餐）：每份可选方案的事实齐全时任何实际安排都能评；"
            "价格、档次与外食次数不进入"
        )
    if comp.enjoyment is not None:
        reasons.append(
            "个人消费与享受读实际在用的舒适用品与实际接受的享受型服务：没有购买或没有接受就是确定的 0（不是未知）"
        )
    if comp.participation is not None:
        reasons.append(
            "休闲读城市背景加实际参与小时：没有参加就是确定的 0；既没有活动事实也没有享受事实的体验实际参加时评分未知"
        )
    if unsupported:
        reasons.append(
            f"模型未支持的享受功能 {sorted(set(unsupported))}：在用时单独列出，不当 0，也不当资料未知"
        )
    if gaps and (comp.food_life is not None or comp.participation is not None):
        reasons.append(
            "覆盖说的是可选目录：实际生活没有用到缺口方案的月份照样可评分；用到了就那个月未知（不补 0）"
        )
    return LeafCoverage(
        leaf=leaf,
        dimension=dimension,
        weight=weight,
        support="unknown_in_some_city" if gaps else "supported",
        evidence=_evidence(facts + params),  # type: ignore[arg-type]
        action_mechanism=action,
        cities_unknown=tuple(sorted(unknown)),
        reasons=tuple(reasons),
        rule="defined",
        city_data="gap" if gaps else "complete",
        action_scope="all_arrangements",
        fact_evidence=_evidence(facts),  # type: ignore[arg-type]
        parameter_evidence=_evidence(params),  # type: ignore[arg-type]
        data_gaps=tuple(gaps),
    )


def _by(leaves: list[LeafCoverage], attr: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for x in leaves:
        key = getattr(x, attr)
        out[key] = out.get(key, 0.0) + x.weight
    return dict(sorted(out.items()))


def coverage_report(
    *,
    spec: LifeScoringSpec,
    group: ComparisonGroup,
    conditions: Sequence[CityScoringConditions],
    catalogs: Sequence[CityLifeCatalog],
    preference: ScoringPreference,
) -> CoverageReport:
    """一个人在一个比较组上的偏好覆盖（coverage-v2）。叶子权重来自明确来源的评分偏好，不随城市变化。"""
    if type(preference) is not ScoringPreference:
        raise LifeContractError("覆盖报告需要明确来源的 ScoringPreference")
    want = sorted(str(c) for c in group.cities)
    have = sorted(str(c.city) for c in conditions)
    if want != have:
        raise LifeContractError(f"比较组城市 {want} 与给出的城市条件 {have} 不一致")
    by_city = {str(c.city): c for c in catalogs}
    missing = sorted(set(want) - set(by_city))
    if missing:
        raise LifeContractError(f"比较组城市 {missing} 没有给生活目录：覆盖要核对目录里的事实")
    if group.scoring_version != spec.scoring_version:
        raise LifeContractError("比较组冻结的评分版本与评分规格不一致")
    for cc in conditions:
        if cc.spec_id != spec.spec_id:
            raise LifeContractError(f"{cc.city} 的条件写的是规格 {cc.spec_id}，不是 {spec.spec_id}")

    # 叶子权重 = (1 − γ) × 六维权重；健康项单独一片叶子，权重 γ
    share = 1.0 - spec.health.gamma
    w = {k: share * v for k, v in effective_weights(spec, preference).items()}
    leaves: list[LeafCoverage] = []
    by_need = {c.material_need: c for c in spec.material_components()}
    needs = [c.material_need for c in spec.material_components()]
    needs += sorted(n for n in preference.material_weights if n not in by_need)
    cuisines = frozenset(preference.cuisines)
    for need in needs:
        assert need is not None
        a = w[MATERIAL] * preference.material_weights.get(need, 0.0)
        leaves.append(_leaf(need, MATERIAL, a, by_need.get(need), conditions, by_city, cuisines))
    for dim in spec.scored_amenities:
        leaves.append(
            _leaf(dim, dim, w[dim], spec.dimension_component(dim), conditions, by_city, cuisines)
        )
    leaves.append(
        LeafCoverage(
            leaf="health_state",
            dimension="health",
            weight=spec.health.gamma,
            support="supported",
            evidence="synthetic_only",
            action_mechanism=True,
            reasons=(
                "健康项 γH 读结算写出的月初健康状态（实际生活事实），不需要城市资料；H 与 γ 是全组共用的合成设定，"
                "疾病、治疗与恢复规则也是合成设定。行动通过工作负担、治疗与恢复影响它",
            ),
            rule="defined",
            city_data="not_applicable",
            action_scope="all_arrangements",
            fact_evidence="synthetic_only",
            parameter_evidence="synthetic_only",
        )
    )

    supported = [x for x in leaves if x.support == "supported"]
    weighted = [x for x in supported if x.weight > 0]
    if weighted and all(x.evidence == "synthetic_only" for x in weighted):
        label = "synthetic_contract_coverage"
    elif weighted and all(x.evidence in ("observed_or_derived", "estimated") for x in weighted):
        label = "data_coverage"
    else:
        label = "mixed_coverage"
    notes = [
        "coverage-v2：规则覆盖、城市资料覆盖与实际行动可评分性分开报；生活事实的来源与评分参数的来源分开报",
        "覆盖报告只报告：不设阈值，不解锁完整总分；正权重未知时月度总分照旧为空，不补零、不按城重加权",
        "账务资料与硬要求另行检查，不被覆盖比例豁免；有限区间与排名属于 E1-6",
    ]
    if label == "synthetic_contract_coverage":
        notes.append("全部受支持的叶子都只有合成输入：这是合成契约覆盖，不是真实数据覆盖率")
    synthetic_facts = [
        x.leaf
        for x in weighted
        if x.fact_evidence in ("synthetic_only", "mixed")
        and x.parameter_evidence != x.fact_evidence
    ]
    if synthetic_facts:
        notes.append(
            f"{synthetic_facts} 的生活事实仍含合成设定（与评分参数的来源类别不同）：参数是估计或观测，"
            "不会让合成的住房、商品或通勤资料变成真实资料"
        )
    background_only = [x.leaf for x in weighted if not x.action_mechanism]
    if background_only:
        notes.append(f"{background_only} 只有城市背景：背景已知不代表行动效果也建模了")
    return CoverageReport(
        person_id=preference.person_id,
        preference_source=preference.source,
        group_id=group.group_id,
        cities=tuple(want),
        leaves=tuple(leaves),
        common_weight=math.fsum(x.weight for x in supported),
        weight_by_evidence=_by(supported, "evidence"),
        unknown_weight=math.fsum(x.weight for x in leaves if x.support == "unknown_in_some_city"),
        no_component_weight=math.fsum(x.weight for x in leaves if x.support == "no_component"),
        coverage_label=label,  # type: ignore[arg-type]
        omissions=spec.omissions,
        notes=tuple(notes),
        report_version=REPORT_VERSION,
        fact_weight_by_evidence=_by(supported, "fact_evidence"),
        parameter_weight_by_evidence=_by(supported, "parameter_evidence"),
    )
