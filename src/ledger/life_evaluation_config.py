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
from common.life_evaluation_contracts import EvaluationSpec
from ledger.life_catalog import LifeBundle, item_role
from ledger.life_scoring_config import ScoringConfig

#: 槽位角色 → 结算里的角色（``ledger.life_catalog.item_role``）。按月服务只认按月计费的服务（不认年付预付服务）。
SLOT_SETTLEMENT_ROLE = {
    "job": "job",
    "housing": "housing",
    "food": "food",
    "service": "periodic_service",
    "durable": "durable_good",
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
    return out


def evaluation_spec_problems(
    spec: EvaluationSpec, bundle: LifeBundle, scoring: ScoringConfig
) -> list[str]:
    """评价规格与生活输入、评分配置之间的跨对象问题。空列表 = 可以用。每座城的绑定用 ``city_binding_problems``
    （与正式评价入口同一份）；这里另查比较组层面的事。"""
    out: list[str] = []
    protocol = bundle.protocol
    if protocol.resource_rules is None or protocol.energy_feedback is None:
        out.append(
            f"协议 {protocol.protocol_id} 没有月度资源规则或满足感反馈政策：E1-6A 的期末估值要按原反馈政策算估值期额度，"
            "只支持 E1-4B 起的协议"
        )
    group = scoring.comparison_group
    if group is None:
        out.append("评分配置没有比较组：评价规格的槽位要在比较组的每座城里绑定同一套方案类别")
        group_cities: set[str] = set()
    else:
        group_cities = {str(c) for c in group.cities}
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
