"""E1-6B 原计划名单的加载与跨对象检查（`DECISIONS/0029`）。不算分数，也不算指纹。

名单要在看任何结果之前写好并冻结；冻结（记下各种内容身份）在 ``econ.life_aggregation.freeze_roster``——税绑定与评分输入的
身份函数在 econ，ledger 不引用 econ。这里只查"名单本身说得通吗"（``roster_problems``；``load_roster`` 与直接调用
``freeze_roster`` 用的是同一份，2026-10-12 收尾 R1、R2）：

* 名单引用的用户有产品路径的偏好陈述（``StatedLifeProfile``；E3-A 起也可以是六维产品画像 ``ProductLifeProfile``，DECISIONS/0034）；
* 每个名单项的分身在生活输入里有第 1 年开局状态，人、城市与声明一致；有健康规则时，重复分身序号与开局状态的健康记录一致；
* 名单项的城市有生活目录，在评分配置的比较组里，在每个运行臂的评价规格里有绑定；名单声明的比较组就是评分配置的比较组；
* **标准开局 v1**（``START_CONDITION_RULE``，``standard_start_problems``）：每个分身都是"刚开始、还没有任何生活记录"的第 1 年
  开局——没有持有物、培训进度、资金目标、病情与病史、月份负担记录与经历摘要；岗位记录是开局声明，额度是第一年的基础值，能力只来自
  用户陈述。本版城市比较不支持别的初态：在冻结之前拒绝，指出分身、字段与原因。这不是生活出局；不删人、不改原计划人数，也不修改、
  补齐或清空状态。这条只在城市比较的名单检查里，不进通用生活引擎——第二年带物品、带病跨年、终态估值照旧可以执行；
* **同一用户各城的个人条件一致**（``common_start_digest``，键与中文名见 ``PERSONAL_CONDITIONS``），逐字段报出哪里不同；分身编号、
  城市、重复序号、开局声明的当地岗位（城市专属编号）与健康绑定可以不同，城市目录、报价、当地岗位与税绑定也可以不同；
* 运行臂：每个声明的运行臂都有评价规格，并对着这份生活输入与评分配置通过完整的跨对象检查（复用
  ``ledger.life_evaluation_config.evaluation_spec_problems``：同城角色、引用、比较组、跨城同一槽位同一类东西；合法的"确定没有 /
  资料未知"不受影响）；名单没有声明的规格也报出来。所有运行臂评价同一批实际生活（同一个协议），对照臂只改评价规格。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from common.life_aggregation_contracts import START_CONDITION_RULE, RosterConfig
from common.life_contracts import LifeContractError, PersonYearState, StatedLifeProfile
from common.life_evaluation_contracts import EvaluationSpec
from common.life_product_profile import ProductLifeProfile
from ledger.life_catalog import LifeBundle, load_life_bundle
from ledger.life_evaluation_config import evaluation_spec_problems, load_evaluation_config
from ledger.life_scoring_config import ScoringConfig, load_scoring_config

#: 标准开局 v1 要求同一用户各城一致的个人条件（``common_start_digest`` 的键）与报告里的中文名。
PERSONAL_CONDITIONS: dict[str, str] = {
    "money_basis_id": "账户口径",
    "cash": "开局现金",
    "time_budget_hours": "年度净可分配小时",
    "energy": "精力额度与尺度",
    "resource_budget_basis": "资源预算依据",
    "base_quota": "基础精力额度",
    "abilities": "开局能力",
    "age_years": "年龄",
    "stated_occupation": "陈述的职业",
    "skills": "数值技能（旧协议）",
    "goals": "文字目标",
}


def common_start_digest(state: PersonYearState) -> dict[str, Any]:
    """一个分身第 1 年开局里的个人条件：标准开局 v1 要求同一用户各城一致的那些字段（键见 ``PERSONAL_CONDITIONS``）。分身编号、
    城市、重复分身序号、开局声明的当地岗位与健康绑定不在里面。只是投影，不是经济计算；旧协议没有的字段为空，沿用原有含义。"""
    basis = state.energy_quota_basis
    budget = state.resource_budget_basis
    employment = state.employment
    stated = employment.stated_occupation if employment is not None else state.occupation
    return {
        "money_basis_id": state.money_basis_id,
        "cash": [state.cash.amount, state.cash.numeraire],
        "time_budget_hours": state.time_budget_hours,
        "energy": state.energy.model_dump(mode="json"),
        "resource_budget_basis": None
        if budget is None
        else [budget.rules_version, budget.reserved],
        "base_quota": None if basis is None else basis.base_level,
        "abilities": sorted((a.ability_id, a.source, a.usable_from_year) for a in state.abilities),
        "age_years": None if state.health is None else state.health.age_years,
        "stated_occupation": None if stated is None else str(stated),
        "skills": sorted(state.skills.items()),
        "goals": list(state.goals),
    }


def standard_start_problems(state: PersonYearState) -> list[str]:
    """标准开局 v1：这个分身的开局里本版城市比较不接受的既有生活状态（字段与原因）。空列表 = 符合。

    只检查，不修改、不补齐、不清空，也不重新抽样。旧协议没有的字段（健康、资金目标、岗位记录、额度依据）沿用原有含义，不强迫添加。
    """
    out: list[str] = []
    if state.year_index != 1 or state.status != "active":
        out.append(
            f"year_index / status：第 {state.year_index} 年、{state.status}——只能从第 1 年 active 的开局出发"
        )
    if state.holdings:
        items = "、".join(f"{h.opportunity_id} ×{h.quantity:g}" for h in state.holdings)
        out.append(
            f"holdings：开局已有持有物（{items}）——本版不接受开局持有物，也不比较不同物品的服务是否等价"
        )
    if state.training:
        items = "、".join(
            f"{t.opportunity_id} {t.months_participated}/{t.months_required}"
            for t in state.training
        )
        out.append(f"training：开局已有培训进度（{items}）——本版不接受开局培训进度")
    if state.savings_goals:
        ids = "、".join(g.goal_id for g in state.savings_goals)
        out.append(
            f"savings_goals：开局已有结构化资金目标（{ids}）——本版不接受历史资金目标及其事件"
        )
    h = state.health
    if h is not None:
        if h.episode is not None:
            out.append(
                f"health.episode：开局正在生病（{h.episode.severity}，已病 {h.episode.months_ill} 个月）——只接受健康开局"
            )
        if h.history:
            out.append(f"health.history：开局已有 {len(h.history)} 次病史——只接受没有病史的开局")
        if h.last_work_relative_burden is not None:
            out.append(
                "health.last_work_relative_burden：开局已有上一个月的工作负担记录——只接受还没有任何月份记录的开局"
            )
    if state.experience_summary:
        out.append(
            f"experience_summary：开局已有 {len(state.experience_summary)} 条经历摘要——本版不接受模拟已经运行过的经历"
        )
    employment = state.employment
    if employment is not None and employment.as_of != "start_declaration":
        out.append(f"employment：岗位记录来自第 {employment.settled_year} 年的结算，不是开局声明")
    quota = state.energy_quota_basis
    if quota is not None and quota.status != "base_first_year":
        out.append(f"energy_quota_basis：额度依据是 {quota.status}，不是第一年的基础值")
    trained = sorted(a.ability_id for a in state.abilities if a.source != "initial")
    if trained:
        out.append(f"abilities：{trained} 来自培训——开局能力只接受用户陈述的 initial 来源")
    return out


def parse_roster_config(data: dict[str, Any]) -> RosterConfig:
    try:
        return RosterConfig.model_validate(data)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise LifeContractError("名单结构不合法：\n  " + "\n  ".join(lines)) from exc


def roster_problems(
    config: RosterConfig,
    bundle: LifeBundle,
    scoring: ScoringConfig,
    specs: dict[str, EvaluationSpec],
) -> list[str]:
    """名单与生活输入、评分配置、各运行臂评价规格之间的跨对象问题。空列表 = 可以冻结。``load_roster`` 与
    ``econ.life_aggregation.freeze_roster`` 都调用这一份。"""
    out: list[str] = []
    group = scoring.comparison_group
    if group is None:
        out.append("评分配置没有比较组：城市汇总只在同一比较组里进行")
        group_cities: set[str] = set()
    else:
        group_cities = {str(c) for c in group.cities}
        if group.group_id != config.comparison_group_id:
            out.append(
                f"名单声明的比较组 {config.comparison_group_id} 不是评分配置的比较组 {group.group_id}"
            )
    # R2：每个声明的运行臂都做完整的跨对象检查（与加载评价规格时同一份），不只检查这座城有没有绑定
    declared = {a.arm_id for a in config.arms}
    for arm in config.arms:
        spec = specs.get(arm.arm_id)
        if spec is None:
            out.append(f"运行臂 {arm.arm_id} 没有加载评价规格（{arm.evaluation_config}）：不能冻结")
            continue
        out.extend(
            f"运行臂 {arm.arm_id} 的评价规格 {spec.spec_id}：{p}"
            for p in evaluation_spec_problems(spec, bundle, scoring)
        )
    for arm_id in sorted(set(specs) - declared):
        out.append(f"评价规格 {arm_id} 不是名单声明的运行臂：不静默多评一个臂")
    users = {u.user_id: u for u in config.users}
    for u in config.users:
        # 产品路径的画像只认这两种确切类型（旧七维陈述、E3-A 六维产品画像）；研究真值或别的对象都不行
        if type(bundle.profile_for(u.person_id)) not in (StatedLifeProfile, ProductLifeProfile):
            out.append(f"用户 {u.user_id} 的人 {u.person_id} 没有产品路径的偏好陈述")
    digests: dict[str, dict[str, dict[str, Any]]] = {}
    for e in config.entries:
        user = users[e.user_id]
        state = bundle.initial_state(e.clone_id)
        where = f"名单项 {e.clone_id}"
        if state is None:
            out.append(f"{where}: 生活输入里没有这个分身的第 1 年开局状态")
            continue
        if state.person_id != user.person_id:
            out.append(
                f"{where}: 开局状态属于 {state.person_id}，用户 {e.user_id} 是 {user.person_id}"
            )
        if str(state.city) != str(e.city):
            out.append(f"{where}: 开局状态在 {state.city}，名单写的是 {e.city}")
        if state.health is not None and state.health.repeat_index != e.repeat_index:
            out.append(
                f"{where}: 开局状态的重复分身序号是 {state.health.repeat_index}，名单写的是 {e.repeat_index}"
            )
        # R1：标准开局 v1——本次城市比较不支持别的初态（不是生活出局，不先运行再归入缺失）
        out.extend(
            f"{where}: 标准开局 {START_CONDITION_RULE} 不接受——{p}（本次城市比较不支持这种初态）"
            for p in standard_start_problems(state)
        )
        if bundle.catalog_for(str(e.city)) is None:
            out.append(f"{where}: {e.city} 没有生活目录")
        if group_cities and str(e.city) not in group_cities:
            out.append(f"{where}: {e.city} 不在比较组里")
        for arm_id, spec in specs.items():
            if spec.binding_for(str(e.city)) is None:
                out.append(f"{where}: 运行臂 {arm_id} 的评价规格没有 {e.city} 的绑定")
        digests.setdefault(e.user_id, {})[e.clone_id] = common_start_digest(state)
    for user_id, by_clone in sorted(digests.items()):
        for key, label in PERSONAL_CONDITIONS.items():
            values: dict[str, list[str]] = {}
            for clone, digest in sorted(by_clone.items()):
                values.setdefault(repr(digest[key]), []).append(clone)
            if len(values) > 1:
                detail = "；".join(f"{v} ← {'、'.join(c)}" for v, c in values.items())
                out.append(
                    f"用户 {user_id} 的个人开局条件「{label}」（{key}）在各分身之间不一致：{detail}——同一用户在不同城市"
                    f"必须从同样的个人条件出发（标准开局 {START_CONDITION_RULE}）"
                )
    return out


def load_roster(
    path: Path,
) -> tuple[RosterConfig, LifeBundle, ScoringConfig, dict[str, EvaluationSpec]]:
    """读名单与它引用的生活输入、评分配置、各运行臂的评价规格，做完跨对象检查。不通过就拒绝（全局问题不伪装成若干
    普通缺失）。"""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("roster"), dict):
        raise LifeContractError(f"{path}: 顶层必须是映射，并有 roster 一节")
    config = parse_roster_config(raw["roster"])
    bundle, _ = load_life_bundle(Path(config.life_input), require_settlement_ready=True)
    scoring, _ = load_scoring_config(Path(config.scoring_config), bundle)
    specs = {
        a.arm_id: load_evaluation_config(Path(a.evaluation_config), bundle, scoring).spec
        for a in config.arms
    }
    problems = roster_problems(config, bundle, scoring, specs)
    if problems:
        raise LifeContractError(f"{path}: 名单检查失败：\n  " + "\n  ".join(problems))
    return config, bundle, scoring, specs
