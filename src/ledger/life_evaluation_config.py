"""E1-6A 评价规格的加载与跨对象检查（`DECISIONS/0028`）。

单个对象能查的（0 < δ ≤ 1、H = 12、参考方案 1–6 个、每座城为每个槽位都写了绑定、方案用到的槽位角色对得上……）在
``common.life_evaluation_contracts``；**跨对象**才看得出来的在这里：

* 协议是否支持期末估值（要有月度资源规则与满足感反馈政策：估值期的额度按原反馈政策算）；
* 绑定的城市是不是比较组的城市、有没有生活目录——比较组的每座城都要有同一套槽位（同一套方案类别）；
* 每个绑定的机会是否在那座城的目录里，结算角色是否与槽位角色一致（住房与食品按城市最低生活声明的合格名单认定，
  不看类别名）；
* 比较组方案口径：同一个槽位在各城绑定的机会必须声明同一组功能（例如"洗衣设备"在各城都是有洗衣功能的耐用品），
  不能一座城是洗衣机、另一座城是别的东西。

**这里不评价。** 跨月汇总与期末估值在 ``econ.life_evaluation`` 与 ``econ.life_terminal_value``；它们在计算之前
同样会核对规格与输入，加载器只是提早把配置错误挡下。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, ValidationError

from common.life_contracts import CityLifeCatalog, ContractModel, LifeContractError
from common.life_evaluation_contracts import CitySlotBinding, EvaluationSpec, HousingSelector
from ledger.life_catalog import LifeBundle, item_role
from ledger.life_scoring_config import ScoringConfig

#: 槽位角色 → 结算里的角色（``ledger.life_catalog.item_role``）。按月服务只认按月计费的服务（不认年付预付服务）。
SLOT_SETTLEMENT_ROLE = {
    "job": "job",
    "housing": "housing",
    "food": "food",
    "service": "periodic_service",
    "durable": "durable_good",
    "activity": "experience",
}


class EvaluationConfig(ContractModel):
    """一份评价配置：说明 + 评价规格。与生活输入、评分配置分开存放。"""

    evaluation_notice: str = Field(min_length=1)
    spec: EvaluationSpec


def parse_evaluation_config(data: dict[str, Any]) -> EvaluationConfig:
    try:
        return EvaluationConfig.model_validate(data)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise LifeContractError("评价配置结构不合法：\n  " + "\n  ".join(lines)) from exc


def city_binding_problems(spec: EvaluationSpec, catalog: CityLifeCatalog) -> list[str]:
    """一座城的槽位绑定与这座城的目录对得上吗。加载器（``evaluation_spec_problems``，逐城调用）与 econ 的正式评价入口
    （``econ.life_evaluation.evaluate_clone``、``econ.life_terminal_value.value_terminal``，在任何试算之前）共用这一份
    （E1-6A 收尾 F4）。

    只查这座城自己知道的事：评价规格里有没有这座城的绑定；绑定到机会的槽位，机会在不在这座城的目录里、结算角色与槽位
    角色一致不一致（住房与食品按城市最低生活声明的合格名单认定，不看类别名）。合法的"确定没有 / 资料未知"声明不是错误。
    跨城的一致性（比较组的城市集合、同一槽位在各城的功能）在 ``evaluation_spec_problems`` 里查。空列表 = 对得上。"""
    binding = spec.binding_for(str(catalog.city))
    if binding is None:
        return [f"评价规格 {spec.spec_id} 没有 {catalog.city} 的槽位绑定"]
    out: list[str] = []
    for target in binding.targets:
        if target.housing_selector is not None:
            out.extend(_selector_problems(target.slot_id, target.housing_selector, catalog))
        if target.status != "opportunity":
            continue
        assert target.opportunity_id is not None
        o = catalog.opportunity(target.opportunity_id)
        where = f"{catalog.city} 槽位 {target.slot_id}"
        if o is None:
            out.append(f"{where}: 绑定的 {target.opportunity_id} 不在这座城的目录里")
            continue
        want = SLOT_SETTLEMENT_ROLE[spec.slot_role(target.slot_id) or ""]
        got = item_role(catalog, o)
        if got != want:
            out.append(
                f"{where}: {o.opportunity_id} 在结算里是 {got}，槽位要的是 {want}"
                "（住房与食品按城市最低生活声明的合格名单认定，不看类别名）"
            )
        elif want == "experience" and o.activity is None:
            out.append(
                f"{where}: {o.opportunity_id} 没有活动事实（activity），不能当参考生活里的爱好活动"
            )
    out.extend(_activity_use_problems(spec, binding, catalog))
    return out


def _selector_problems(slot_id: str, sel: HousingSelector, catalog: CityLifeCatalog) -> list[str]:
    """住房选择器（E3B 收尾）与本城目录对得上吗：候选都是目录里的合格住房、片区一致；就业片区都在目录里；每个候选的
    覆盖分子与未知权重按目录自己的通勤关系表重算一遍（有任一方式的线 = 已知通达；一条线都没有 = 未知，留在分母里）。"""
    where = f"{catalog.city} 槽位 {slot_id} 的选择器"
    out: list[str] = []
    work = {a.area_id for a in catalog.areas if a.kind in ("work", "home_work")}
    stray = sorted(w.area_id for w in sel.work_areas if w.area_id not in work)
    if stray:
        out.append(f"{where}: 就业片区 {stray} 不在本城目录的就业片区里")
    pairs = (
        set()
        if catalog.commute is None
        else {(g.home_area, g.work_area) for g in catalog.commute.legs}
    )
    for c in sel.candidates:
        o = catalog.opportunity(c.opportunity_id)
        if o is None:
            out.append(f"{where}: 候选 {c.opportunity_id} 不在这座城的目录里")
            continue
        if item_role(catalog, o) != "housing":
            out.append(f"{where}: 候选 {c.opportunity_id} 在结算里不是住房")
        if o.area != c.area:
            out.append(f"{where}: 候选 {c.opportunity_id} 的片区 {c.area} 与目录写的 {o.area} 不同")
            continue
        covered = sum(w.inbound_weight for w in sel.work_areas if (c.area, w.area_id) in pairs)
        unknown = sel.total_weight - covered
        if (covered, unknown) != (c.covered_weight, c.unknown_weight):
            out.append(
                f"{where}: 候选 {c.opportunity_id} 的覆盖 {c.covered_weight} / 未知 {c.unknown_weight} 与按目录通勤关系"
                f"重算的 {covered} / {unknown} 不同"
            )
    return out


def _activity_use_problems(
    spec: EvaluationSpec, binding: CitySlotBinding, catalog: CityLifeCatalog
) -> list[str]:
    """模板里的爱好活动（DECISIONS/0041）：替换或新买的装备必须提供活动需要的能力，而且买下当月就能用——否则模板按规则买了
    也用不上，估值会悄悄少算一段活动。"""
    out: list[str] = []
    for p in spec.life_templates:
        for u in p.activity_uses:
            if u.equipment_slot is None:
                continue
            act, gear = binding.target(u.activity_slot), binding.target(u.equipment_slot)
            if (
                act is None
                or gear is None
                or act.status != "opportunity"
                or gear.status != "opportunity"
            ):
                continue
            a, g = catalog.opportunity(act.opportunity_id), catalog.opportunity(gear.opportunity_id)
            if a is None or g is None or a.activity is None:
                continue
            where = f"{catalog.city} 模板 {p.template_id} 的活动 {u.activity_slot}"
            need = set(a.activity.requires) - set(a.activity.kit_provides)
            have = set(g.equipment.provides) if g.equipment is not None else set()
            if need - have:
                out.append(
                    f"{where}: 装备 {g.opportunity_id} 不提供需要的能力 {sorted(need - have)}"
                )
            if g.duration.effective_from == "next_year":
                out.append(f"{where}: 装备 {g.opportunity_id} 下一年才生效，估值期里买了也用不上")
    return out


def evaluation_spec_problems(
    spec: EvaluationSpec, bundle: LifeBundle, scoring: ScoringConfig
) -> list[str]:
    """评价规格与生活输入、评分配置之间的跨对象问题。空列表 = 可以用。每座城的绑定用 ``city_binding_problems``
    （与正式评价入口同一份）；这里另查比较组层面的事。"""
    out: list[str] = []
    group_cities = {str(c) for c in scoring.comparison_group.cities}
    bound = {str(b.city) for b in spec.city_bindings}
    for city in sorted(group_cities - bound):
        out.append(f"比较组的 {city} 没有槽位绑定：比较组的每座城都要有同一套参考方案类别")
    for city in sorted(bound - group_cities):
        out.append(f"槽位绑定里的 {city} 不在比较组里")
    functions: dict[str, dict[str, tuple[str, ...]]] = {}
    for b in spec.city_bindings:
        catalog = bundle.catalog_for(str(b.city))
        if catalog is None:
            out.append(f"槽位绑定里的 {b.city} 没有生活目录")
            continue
        out.extend(city_binding_problems(spec, catalog))
        for target in b.targets:
            o = (
                None
                if target.opportunity_id is None
                else catalog.opportunity(target.opportunity_id)
            )
            if target.status == "opportunity" and o is not None:
                functions.setdefault(target.slot_id, {})[str(b.city)] = tuple(sorted(o.functions))
    for slot, by_city in sorted(functions.items()):
        if len(set(by_city.values())) > 1:
            out.append(
                f"槽位 {slot} 在各城绑定的机会声明的功能不同 {dict(sorted(by_city.items()))}："
                "同一个槽位在比较组里必须是同一类东西"
            )
    return out


def load_evaluation_config(
    path: Path, bundle: LifeBundle, scoring: ScoringConfig
) -> EvaluationConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise LifeContractError(f"{path}: 顶层必须是映射")
    config = parse_evaluation_config(raw)
    problems = evaluation_spec_problems(config.spec, bundle, scoring)
    if problems:
        raise LifeContractError(f"{path}: 评价配置检查失败：\n  " + "\n  ".join(problems))
    return config
