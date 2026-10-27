"""探索评分配置的加载与跨对象检查（E1-3，`DECISIONS/0018`）。

单个对象能查的（τ > 0、上限在 0–1、未知不带数值、七维各有一个分项……）在
``common.life_scoring_contracts``；**跨对象**才看得出来的在这里：

* 城市条件与评分规格对不对得上（每个分项都有条件、每个来源都声明了改善效率）；
* 基础条件与上限是否自洽——光靠基础条件就超过上限，上限就失去了意义；
* 城市是否真的在这份生活输入里、规格对应的协议是不是这份输入的协议；
* 来源引用的类别、物质细需求是否声明过。

**这里不评分。** 评分在 ``econ.life_scoring``，它在计算之前调用 ``condition_problems``——
加载器与评分器共用同一份检查。
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError

from common.life_contracts import ContractModel, LifeContractError, LifeProtocol
from common.life_scoring_contracts import (
    CityScoringConditions,
    ComparisonGroup,
    LifeScoringSpec,
    scoring_content_problems,
)
from common.life_units import resolve_unit
from ledger.life_catalog import LifeBundle, item_role


class ScoringConfig(ContractModel):
    """一份探索评分配置：评分规格 + 各城市的条件。与生活输入分开存放。"""

    scoring_notice: str = Field(min_length=1)
    spec: LifeScoringSpec
    city_conditions: tuple[CityScoringConditions, ...]
    #: 比较组的共同粒度（`DECISIONS/0019` 第五节）：资料单位与功能供给都在比较组里核对。
    comparison_group: ComparisonGroup

    def conditions_for(self, city_key: str) -> CityScoringConditions | None:
        return next((c for c in self.city_conditions if str(c.city) == city_key), None)


class ScoringConfigReport(ContractModel):
    """配置检查结果。三种"能不能"分开报：记账能否执行看生活输入的 ``settlement_ready``；
    探索月度评分能否执行看 ``exploration_scoring_ready``；完整生活协议能否用于正式评价看
    ``formal_scoring_ready``（协议的 ``scoring_ready``，本轮为 false）。**不含任何分数。**"""

    exploration_scoring_ready: bool
    formal_scoring_ready: bool
    errors: tuple[str, ...]
    notes: tuple[str, ...]
    summary: dict[str, Any]


def _exact(x: float) -> Fraction:
    return Fraction(Decimal(repr(float(x))))


def condition_problems(spec: LifeScoringSpec, conditions: CityScoringConditions) -> list[str]:
    """一座城的条件是否完整、自洽地覆盖了这套评分规格。评分器在计算前调用这一份。"""
    city = conditions.city
    out: list[str] = []
    if conditions.spec_id != spec.spec_id:
        out.append(f"{city}: 条件写的是规格 {conditions.spec_id}，现在用的是 {spec.spec_id}")
        return out
    want = {c.component_id for c in spec.components}
    have = {c.component_id for c in conditions.components}
    for cid in sorted(want - have):
        out.append(f"{city}: 缺分项 {cid} 的条件（未知也要明确写成 unknown）")
    for cid in sorted(have - want):
        out.append(f"{city}: 条件里有规格没有的分项 {cid}")
    for comp in spec.components:
        cond = conditions.condition(comp.component_id)
        if cond is None:
            continue
        where = f"{city}/{comp.component_id}"
        if comp.transport is not None and cond.status == "known":
            out.append(
                f"{where}: 广义交通读实际住房的分钟与到岗次数，城市条件应写 plan_level 或 unknown"
            )
        if comp.convenience is not None and cond.status == "known":
            out.append(
                f"{where}: 日常生活便利读资源账里实际减少的家务小时，c 与 τ 全组共用、写在评分规格里；城市条件应写 "
                "plan_level 或 unknown（不按城市给背景值或上限）"
            )
        if not comp.plan_level and cond.status == "plan_level":
            out.append(f"{where}: 只有广义交通或日常生活便利分项才能写 plan_level")
        if cond.nature is not None and comp.landscape is None:
            out.append(f"{where}: 评分规格没有启用景观规则，城市却写了景观数据")
        if comp.landscape is not None and comp.landscape.beta > 0 and cond.status == "known":
            if cond.nature is None:
                out.append(f"{where}: β > 0 时必须写明景观数据（已知或未知）——缺失不当零")
            elif cond.nature.base_includes_landscape:
                out.append(f"{where}: 基础环境已经包含同一景观，再加景观分就是重复计分")
        if cond.status != "known":
            continue
        assert cond.base is not None and cond.cap is not None
        base_alone = _exact(cond.base) / (_exact(cond.base) + _exact(comp.tau))
        if base_alone > _exact(cond.cap):
            out.append(
                f"{city}/{comp.component_id}: 光靠基础条件就到 {float(base_alone):.4f}，超过上限 "
                f"{cond.cap}——基础条件与上限不自洽"
            )
        declared = {e.source_id for e in cond.efficiencies}
        sources = {s.source_id for s in comp.sources}
        if comp.combine == "any" and comp.sources:
            if cond.eta_any is None:
                out.append(f"{city}/{comp.component_id}: any 分项必须给 eta_any")
            if declared:
                out.append(f"{city}/{comp.component_id}: any 分项只用 eta_any，不按来源给 η")
        else:
            if cond.eta_any is not None:
                out.append(f"{city}/{comp.component_id}: 只有 any 分项才用 eta_any")
            for sid in sorted(sources - declared):
                out.append(f"{city}/{comp.component_id}: 来源 {sid} 没有声明改善效率 η")
            for sid in sorted(declared - sources):
                out.append(f"{city}/{comp.component_id}: η 声明了规格里没有的来源 {sid}")
    return out


def scoring_config_problems(
    config: ScoringConfig, bundle: LifeBundle
) -> tuple[list[str], list[str]]:
    """配置与一份生活输入之间的跨对象检查。返回（错误，说明）。"""
    errors: list[str] = []
    notes: list[str] = []
    spec = config.spec
    if spec.protocol_id != bundle.protocol.protocol_id:
        errors.append(
            f"评分规格对应协议 {spec.protocol_id}，生活输入是 {bundle.protocol.protocol_id}"
        )
    keys = [str(c.city) for c in config.city_conditions]
    for k, n in Counter(keys).items():
        if n > 1:
            errors.append(f"城市 {k} 的评分条件出现了 {n} 次")
    catalogs = {str(c.city) for c in bundle.catalogs}
    for k in sorted(set(keys) - catalogs):
        errors.append(f"评分条件引用了生活输入里没有的城市 {k}")
    for k in sorted(catalogs - set(keys)):
        errors.append(f"城市 {k} 没有评分条件，不能做探索评分")
    for cond in config.city_conditions:
        errors.extend(condition_problems(spec, cond))
        unknown = [c.component_id for c in cond.components if c.status == "unknown"]
        if unknown:
            notes.append(
                f"{cond.city}: 分项 {unknown} 数据未知——有正权重的人在这座城的月度总分会不完整，"
                "不补零、不重新归一化"
            )
    errors.extend(growth_transport_protocol_problems(spec, bundle.protocol))
    errors.extend(convenience_protocol_problems(spec, bundle.protocol))
    notes.extend(convenience_protocol_notes(spec, bundle.protocol))
    policy = bundle.protocol.energy_feedback
    if (policy.scoring_spec_id, policy.scoring_version) == (spec.spec_id, spec.scoring_version):
        # 反馈政策读这套规格时，实际加载的内容必须就是政策声明的那一套（与年度交接同一份判断，DECISIONS/0025）
        errors.extend(message for _, message in scoring_content_problems(policy, spec))
    categories = set(bundle.categories)
    needs = set(bundle.consumption_needs)
    functions = set(bundle.functions)
    for comp in spec.components:
        if comp.material_need is not None and comp.material_need not in needs:
            errors.append(f"分项 {comp.component_id} 的物质细需求 {comp.material_need!r} 没有声明")
        for src in comp.sources:
            for cat in src.categories:
                if cat not in categories:
                    errors.append(f"{comp.component_id}/{src.source_id}: 类别 {cat!r} 没有声明")
            for f in src.functions:
                if f not in functions:
                    errors.append(f"{comp.component_id}/{src.source_id}: 功能 {f!r} 没有声明")
        if comp.convenience is not None:
            for f in comp.convenience.support_functions:
                if f not in functions:
                    errors.append(
                        f"{comp.component_id}: 日常支持功能 {f!r} 没有在生活输入的功能词表里声明"
                    )
    errors.extend(comparison_group_problems(config, bundle))
    base_only = [c.dimension for c in spec.components if c.base_only]
    if base_only:
        notes.append(f"维度 {base_only} 只有城市基础条件：当前没有行动改善机制")
    return errors, notes


def growth_transport_protocol_problems(spec: LifeScoringSpec, protocol: LifeProtocol) -> list[str]:
    """广义交通与时间账读同一份通勤执行记录（DECISIONS/0042），不再有各读各的规格字段；这里只留一个钩子，加载器与评分器
    （``econ.life_scoring.score_year``）共用。"""
    return []


def convenience_protocol_problems(spec: LifeScoringSpec, protocol: LifeProtocol) -> list[str]:
    """评分规格与协议是否配套（`DECISIONS/0033`）。加载器与评分器共用这一份。

    * 协议声明的评分尺度就是这套规格的评分版本；
    * 便利任务组合里的每一项都必须是协议声明了基准的家务——基准缺了不能当 0，也不能按城市另设。
    """
    out: list[str] = []
    if protocol.scoring_scale_version != spec.scoring_version:
        out.append(
            f"协议 {protocol.protocol_id} 声明的评分尺度是 {protocol.scoring_scale_version}，评分规格是 "
            f"{spec.scoring_version}：两边必须一致"
        )
    rules = protocol.resource_rules
    for comp in spec.components:
        cv = comp.convenience
        if cv is None:
            continue
        for task in cv.tasks:
            if rules.task(task) is None:
                out.append(
                    f"分项 {comp.component_id} 的家务 {task} 在协议 {protocol.protocol_id} 的资源规则里没有声明基准小时："
                    "基准缺了不当 0，也不按城市另设"
                )
    return out


def convenience_protocol_notes(spec: LifeScoringSpec, protocol: LifeProtocol) -> list[str]:
    """协议里声明了、却不在便利任务组合里的家务（只说明，不是错误：任务组合是代表性的，不声称包含全部家务）。"""
    rules = protocol.resource_rules
    out: list[str] = []
    for comp in spec.components:
        cv = comp.convenience
        if cv is None:
            continue
        rest = sorted(t.task for t in rules.household_tasks if t.task not in cv.tasks)
        if rest:
            out.append(
                f"分项 {comp.component_id}：家务 {rest} 不在日常生活便利的任务组合里——它们的减负照常进时间与精力账，"
                "但不进便利分"
            )
    return out


def comparison_group_problems(config: ScoringConfig, bundle: LifeBundle) -> list[str]:
    """比较组的共同粒度检查（`DECISIONS/0019` 第五节）。

    挡得住：混币或基年不同、来源的地理粒度或类型与全组不同、某类行动完全没有声明、规格键没有
    单位、通勤粒度不统一。挡不住：作者把别城数字抄来并标成同类来源——那要审计来源的输入与规则。
    """
    g = config.comparison_group
    spec = config.spec
    out: list[str] = []
    if g.scoring_version != spec.scoring_version:
        out.append(f"比较组冻结的评分版本是 {g.scoring_version}，规格是 {spec.scoring_version}")
    commute = spec.dimension_component("commute")
    if not commute.plan_level:
        out.append("比较组声明方案级交通，但评分规格的交通不是方案级")
    for city in g.cities:
        key = str(city)
        cat = bundle.catalog_for(key)
        cond = config.conditions_for(key)
        if cat is None or cond is None:
            out.append(f"比较组城市 {key} 缺生活目录或评分条件")
            continue
        if (cat.money_basis.currency, cat.money_basis.base_year) != (g.currency, g.base_year):
            out.append(
                f"{key}: 计价是 {cat.money_basis.numeraire_id}，比较组冻结的是 {g.currency} {g.base_year}"
                "——不混币，不混基年"
            )
        sources = [(f"{key}/{o.opportunity_id}", o.source) for o in cat.opportunities]
        sources += [(f"{key}/评分条件 {c.component_id}", c.source) for c in cond.components]
        sources += [
            (f"{key}/景观指标 {c.component_id}", c.nature.source)
            for c in cond.components
            if c.nature is not None
        ]
        sources += [(f"{key}/功能供给 {fs.function}", fs.source) for fs in cat.function_supply]
        # E1-5C：治疗目录也是城市事实，同一地理粒度、同一类来源
        sources += [(f"{key}/治疗 {tr.treatment_id}", tr.source) for tr in cat.treatments]
        for where, src in sources:
            if src.geography_scope != g.geography_scope:
                out.append(
                    f"{where}: 地理粒度 {src.geography_scope!r} 与比较组的 {g.geography_scope!r} 不同"
                    "——不能某城细化、某城用别的粒度或默认值填"
                )
            if src.kind not in g.allowed_source_kinds:
                out.append(
                    f"{where}: 来源类型 {src.kind} 不在比较组允许的 {list(g.allowed_source_kinds)} 里"
                )
        # DECISIONS/0042：按片区的基础量要覆盖这座城每个可得住处所在的片区，片区要在目录里声明
        homes = {
            o.area
            for o in cat.opportunities
            if o.availability == "available" and item_role(cat, o) == "housing"
        }
        declared = {a.area_id for a in cat.areas}
        for c in cond.components:
            if not c.area_bases:
                continue
            stray = sorted(set(c.area_bases) - declared)
            if stray:
                out.append(f"{key}/评分条件 {c.component_id}: 片区 {stray} 没有在目录里声明")
            missing = sorted(a for a in homes if a is not None and a not in c.area_bases)
            if None in homes:
                out.append(
                    f"{key}/评分条件 {c.component_id}: 有可得住房没有写片区，按片区的地区值读不到"
                )
            if missing:
                out.append(
                    f"{key}/评分条件 {c.component_id}: 可得住房所在片区 {missing} 没有地区值"
                )
        roles = {item_role(cat, o) for o in cat.opportunities}
        for role in g.action_roles:
            if role not in roles:
                out.append(f"{key}: 行动类型 {role} 没有任何记录——可得、确定不可得或未知都要写明")
        for f in sorted(scored_functions(spec)):
            if cat.supply_for(f) is None:
                out.append(
                    f"{key}: 评分要用的功能 {f} 没有供给声明——可得、确定不可得或资料未知都要写明；"
                    "目录里没有记录不等于确定没有"
                )
        if g.commute_granularity == "plan_level" and commute.transport is not None:
            ccond = cond.condition(commute.component_id)
            if ccond is None or ccond.status != "plan_level":
                out.append(f"{key}: 广义交通要求城市条件写 plan_level")
            for o in cat.opportunities:
                if o.availability != "available":
                    continue
                role = item_role(cat, o)
                if role == "housing":
                    area = cat.area(o.area) if o.area is not None else None
                    if area is None or area.typical_roundtrip_minutes is None:
                        out.append(
                            f"{key}/{o.opportunity_id}: 广义交通要求每个可得住房所在的居住片区写明居民平时的往返"
                            "分钟（没有到岗的月份读它）——全组通过才启用"
                        )
                elif role == "job" and o.resource.job is None:
                    out.append(
                        f"{key}/{o.opportunity_id}: 广义交通的到岗次数读资源账——每份可得工作必须写岗位方案"
                        "（工时、工作日与到岗次数），不能按工作方式字样或岗位数量去猜"
                    )
    out.extend(treatment_group_problems(config, bundle))
    used: set[str] = set()
    for comp in spec.components:
        for src in comp.sources:
            used.update(k for k in (src.spec_key, src.requires_spec_key) if k)
    for k in sorted(used - set(g.units)):
        out.append(f"评分用到的规格键 {k} 没有写进比较组的单位表")
    out.extend(unit_problems(spec, g.units))
    out.extend(function_kind_problems(spec, bundle))
    return out


def treatment_group_problems(config: ScoringConfig, bundle: LifeBundle) -> list[str]:
    """比较组里每座城的治疗条目用同一套需求定义与计价粒度（每次疾病一次性计费、同一个计价单位）。

    真正不可得或资料未知照样合法（各城写明）；不合法的是某城换了需求、换了计费粒度或漏写。"""
    rules = bundle.protocol.health_rules
    g = config.comparison_group
    out: list[str] = []
    for sev in ("minor", "major"):
        need = rules.need(sev).need_id
        units: dict[str, str] = {}
        for city in g.cities:
            cat = bundle.catalog_for(str(city))
            if cat is None:
                continue
            offer = cat.treatment(sev)
            if offer is None:
                out.append(f"{city}: 比较组要求写明 {sev} 的治疗条目（可得、确定不可得或资料未知）")
                continue
            if offer.need_id != need:
                out.append(
                    f"{city}/{offer.treatment_id}: 满足的需求 {offer.need_id} 与全组的 {need} 不同"
                )
            if offer.price is not None:
                units[str(city)] = offer.price.per_unit
        if len(set(units.values())) > 1:
            out.append(f"{sev} 治疗的计价单位在比较组里不一致 {units}——不同粒度的价格不能比较")
    return out


def scored_functions(spec: LifeScoringSpec) -> set[str]:
    """评分来源按功能过滤时用到的全部需求功能。"""
    return {f for comp in spec.components for src in comp.sources for f in src.functions}


def unit_problems(spec: LifeScoringSpec, units: dict[str, str]) -> list[str]:
    """资料单位与评分读取单位是否一致（`DECISIONS/0019` 第十二节）。

    评分器**不换算原始资料**：比较组单位表里某个规格键的单位，必须与读它的来源是同一个规范单位
    （写法可以是登记过的别名）。资料若是别的单位（立方英尺、小时），应在资料整理时按可审计规则
    换算，并把来源标成 derived（写明输入与规则）——不能只改单位标签不改数值。读取单位到分项单位
    的换算（平方英尺 → 百平方英尺）由规格检查负责，见 ``supplement_spec_problems``。
    """
    out: list[str] = []

    def data_unit(key: str):
        text = units.get(key)
        if text is None:
            return None  # 缺单位另有一条报错
        u = resolve_unit(text)
        if u is None:
            out.append(f"规格键 {key} 的资料单位 {text!r} 没有登记——不按字段名猜单位，拒绝")
        return u

    def same(key: str, want_text: str, reader: str) -> None:
        have = data_unit(key)
        want = resolve_unit(want_text)
        if have is None or want is None:
            return
        if have.unit_id != want.unit_id:
            hint = "同一量纲但单位不同" if have.dimension == want.dimension else "量纲都不同"
            out.append(
                f"规格键 {key}：资料单位是 {have.label}，{reader} 按 {want.label} 读取（{hint}）——评分器"
                "不换算原始资料；请先按可审计规则换算并把来源标为 derived，不能只改单位标签"
            )

    for comp in spec.components:
        for src in comp.sources:
            if src.spec_key is not None:
                same(src.spec_key, src.unit, f"{comp.component_id}/{src.source_id}")
            if src.requires_spec_key is not None and src.requires_spec_key != src.spec_key:
                data_unit(src.requires_spec_key)
    return out


def function_kind_problems(spec: LifeScoringSpec, bundle: LifeBundle) -> list[str]:
    """按功能过滤的来源，数量语义必须与生活输入里这项功能声明的一致。"""
    out: list[str] = []
    for comp in spec.components:
        for src in comp.sources:
            for f in src.functions:
                kind = bundle.function_kinds.get(f)
                where = f"{comp.component_id}/{src.source_id}"
                if kind is None:
                    out.append(
                        f"{where}: 功能 {f} 在生活输入里没有声明数量语义（function_kinds：presence 或 capacity）"
                    )
                elif src.measure is not None and src.measure != kind:
                    out.append(
                        f"{where}: 功能 {f} 声明为 {kind}，评分来源却按 {src.measure} 计——"
                        "不能把存在型功能换个说法按件或按容量计分"
                    )
    return out


def parse_scoring_config(data: dict[str, Any]) -> ScoringConfig:
    try:
        return ScoringConfig.model_validate(data)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise LifeContractError("评分配置结构不合法：\n  " + "\n  ".join(lines)) from exc


def check_scoring_config(config: ScoringConfig, bundle: LifeBundle) -> ScoringConfigReport:
    errors, notes = scoring_config_problems(config, bundle)
    unknown = Counter(
        f"{c.city}" for c in config.city_conditions for x in c.components if x.status == "unknown"
    )
    return ScoringConfigReport(
        exploration_scoring_ready=not errors,
        formal_scoring_ready=bundle.protocol.scoring_ready,
        errors=tuple(errors),
        notes=tuple(notes),
        summary={
            "comparison_group": config.comparison_group.group_id
            if config.comparison_group
            else None,
            "spec_id": config.spec.spec_id,
            "scoring_version": config.spec.scoring_version,
            "function": config.spec.function,
            "n_components": len(config.spec.components),
            "n_cities": len(config.city_conditions),
            "unknown_conditions": dict(sorted(unknown.items())),
        },
    )


def load_scoring_config(
    path: Path, bundle: LifeBundle, *, strict: bool = True
) -> tuple[ScoringConfig, ScoringConfigReport]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise LifeContractError(f"{Path(path).as_posix()}: 顶层必须是映射")
    config = parse_scoring_config(raw)
    report = check_scoring_config(config, bundle)
    if strict and not report.exploration_scoring_ready:
        raise LifeContractError(
            f"{Path(path).as_posix()}: 评分配置检查失败：\n  " + "\n  ".join(report.errors)
        )
    return config, report
