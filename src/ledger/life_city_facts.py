"""E1-7A 城市事实表的加载与跨对象检查（`DECISIONS/0030` 第三节）。不评分，也不新增评分公式。

城市事实表是代理可见的生活事实；评分条件里的背景量 b 是内部评分输入。两边说的必须是同一座城：开跑之前逐项核对，任何一条
对不上就拒绝启动（不偷偷删掉某座城再声称比较了原来那组城市）。

1. **比较组**：事实表声明的比较组就是评分配置的比较组；事实表恰好覆盖生活输入里的每座城，不多不少。
2. **定义一致**：每条事实在各城的编号、名称、含义、取值类型、单位与时间口径完全相同；每座城的事实编号集合相同。允许数值
   不同、供给不同；资料未知照实写未知，不能因为资料少就给这座城换一套更简单的写法。
3. **对应关系**：每条对应指向一条数值事实与评分规格里的一个分项。资料已知的事实按换算得到的 b 与这座城评分条件里的 b 逐位
   相等（精确分数，不加容差）；资料未知的事实只能对应资料未知的评分条件（未知不补零）；确定不存在的事实必须有声明的对应值，
   评分条件必须正是这个值。
4. **覆盖**：评分条件里有城市背景（已知且 b > 0，或资料未知）的分项都要有一条可见事实对应——否则代理看不到这部分城市条件，
   两套城市描述各说各话。方案级分项（例如通勤读实际住房）与没有背景的分项（b = 0，来自所选方案）不需要。
5. **来源分开**：事实的来源与换算的来源是两条记录；换算不能标成观测（契约已检查）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from common.life_city_facts import FACT_STATUS_LABEL, CityFactsConfig
from common.life_contracts import LifeContractError, to_fraction
from ledger.life_catalog import LifeBundle
from ledger.life_scoring_config import ScoringConfig


def parse_city_facts(data: dict[str, Any]) -> CityFactsConfig:
    try:
        return CityFactsConfig.model_validate(data)
    except ValidationError as exc:
        lines = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        raise LifeContractError("城市事实表结构不合法：\n  " + "\n  ".join(lines)) from exc


def _fmt(x: object) -> str:
    return f"{float(x):g}"  # type: ignore[arg-type]


def city_facts_problems(
    facts: CityFactsConfig, bundle: LifeBundle, scoring: ScoringConfig
) -> list[str]:
    """事实表与生活输入、评分配置之间的全部不一致（空列表 = 对得上）。"""
    out: list[str] = []
    group = scoring.comparison_group
    if group is not None and facts.comparison_group_id != group.group_id:
        out.append(
            f"事实表属于比较组 {facts.comparison_group_id}，评分配置的比较组是 {group.group_id}"
        )
    want = {str(c.city) for c in bundle.catalogs}
    got = {str(s.city) for s in facts.sheets}
    for city in sorted(want - got):
        out.append(f"{city} 没有城市事实表：代理看不到这座城的背景事实，不能声称比较了它")
    for city in sorted(got - want):
        out.append(f"事实表里的 {city} 不在生活输入里")

    # 2. 定义一致：以第一座城为参照，逐条比较
    sheets = sorted(facts.sheets, key=lambda s: str(s.city))
    if sheets:
        ref = sheets[0]
        ref_ids = {f.fact_id for f in ref.facts}
        for s in sheets[1:]:
            ids = {f.fact_id for f in s.facts}
            for fid in sorted(ref_ids - ids):
                out.append(f"{s.city} 少了事实 {fid}（{ref.city} 有）：各城必须用同一套事实定义")
            for fid in sorted(ids - ref_ids):
                out.append(f"{s.city} 多了事实 {fid}（{ref.city} 没有）：各城必须用同一套事实定义")
            for fid in sorted(ids & ref_ids):
                a, b = ref.fact(fid), s.fact(fid)
                assert a is not None and b is not None
                da, db = a.definition(), b.definition()
                diff = [k for k in da if da[k] != db[k]]
                if diff:
                    out.append(
                        f"事实 {fid} 在 {ref.city} 与 {s.city} 的定义不同（{', '.join(diff)}）：同一个编号必须是同一个"
                        "含义、单位与时间口径"
                    )

    # 3. 对应关系
    components = {c.component_id for c in scoring.spec.components}
    for link in facts.links:
        if link.component_id not in components:
            out.append(f"对应 {link.fact_id} → {link.component_id}：评分规格里没有这个分项")
        for s in facts.sheets:
            fact = s.fact(link.fact_id)
            if fact is None:
                out.append(f"对应 {link.fact_id} → {link.component_id}：{s.city} 没有这条事实")
                continue
            if fact.value_kind != "number":
                out.append(f"{s.city} 的 {link.fact_id} 是类别事实：本轮只支持把数值事实换成 b")
                continue
            conditions = scoring.conditions_for(str(s.city))
            cond = None if conditions is None else conditions.condition(link.component_id)
            if cond is None:
                out.append(f"{s.city} 的评分条件里没有分项 {link.component_id}")
                continue
            where = f"{s.city} 的 {link.fact_id}（{FACT_STATUS_LABEL[fact.status]}）→ {link.component_id}"
            if fact.status == "unknown":
                if cond.status != "unknown":
                    out.append(
                        f"{where}：事实资料未知，评分条件却是 {cond.status}——资料未知不能在评分侧补成一个数"
                    )
                continue
            if fact.status == "unavailable" and link.unavailable_base is None:
                out.append(f"{where}：对应没有声明“确定不存在”对应的 b——不能悄悄当成 0")
                continue
            expected = link.base_for(fact)
            if cond.status != "known" or cond.base is None:
                out.append(f"{where}：事实可用，评分条件却是 {cond.status}——两套城市描述不一致")
                continue
            if to_fraction(cond.base) != expected:
                shown = fact.number if fact.status == "known" else "确定不存在"
                out.append(
                    f"{where}：事实 {shown} 按换算得到 b = {_fmt(expected)}，评分条件写的是 b = {_fmt(cond.base)}"
                    "——代理看到的事实与内部评分用的城市条件对不上"
                )

    # 4. 覆盖：有城市背景的分项都要有可见事实
    linked = {x.component_id for x in facts.links}
    for conditions in scoring.city_conditions:
        for cond in conditions.components:
            if cond.component_id in linked or cond.status == "plan_level":
                continue
            background = cond.status == "unknown" or (
                cond.status == "known" and cond.base is not None and to_fraction(cond.base) > 0
            )
            if background:
                out.append(
                    f"{conditions.city} 的评分分项 {cond.component_id} 有城市背景（{cond.status}），但没有对应的可见事实："
                    "代理看不到这部分城市条件"
                )
    return out


def alignment_report(
    facts: CityFactsConfig, bundle: LifeBundle, scoring: ScoringConfig
) -> dict[str, Any]:
    """逐城、逐条对应的核对表（写进运行证据）：事实、换算得到的 b、评分条件里的 b、是否一致。只给审计看，不进提示。"""
    rows: list[dict[str, Any]] = []
    for s in sorted(facts.sheets, key=lambda x: str(x.city)):
        conditions = scoring.conditions_for(str(s.city))
        for link in facts.links:
            fact = s.fact(link.fact_id)
            cond = None if conditions is None else conditions.condition(link.component_id)
            expected = None
            if fact is not None and fact.status != "unknown" and fact.value_kind == "number":
                try:
                    expected = link.base_for(fact)
                except ValueError:
                    expected = None
            actual = None if cond is None or cond.base is None else to_fraction(cond.base)
            rows.append(
                {
                    "city": str(s.city),
                    "fact_id": link.fact_id,
                    "fact_status": None if fact is None else fact.status,
                    "fact_number": None if fact is None else fact.number,
                    "component_id": link.component_id,
                    "rule": link.rule,
                    "expected_base": None if expected is None else str(expected),
                    "condition_status": None if cond is None else cond.status,
                    "condition_base": None if actual is None else str(actual),
                    "consistent": (
                        fact is not None
                        and cond is not None
                        and (
                            (fact.status == "unknown" and cond.status == "unknown")
                            or (
                                expected is not None
                                and actual == expected
                                and cond.status == "known"
                            )
                        )
                    ),
                }
            )
    return {
        "comparison_group_id": facts.comparison_group_id,
        "visible_fingerprint": facts.visible_fingerprint,
        "links_fingerprint": facts.links_fingerprint,
        "rows": rows,
        "problems": city_facts_problems(facts, bundle, scoring),
    }


def load_city_facts(path: Path, bundle: LifeBundle, scoring: ScoringConfig) -> CityFactsConfig:
    """读事实表并做全部跨对象检查；不通过就拒绝（``LifeContractError``，列出每一条）。"""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "city_facts" not in raw:
        raise LifeContractError(f"{path}: 顶层必须是映射，并有 city_facts 一节")
    facts = parse_city_facts(raw["city_facts"])
    problems = city_facts_problems(facts, bundle, scoring)
    if problems:
        raise LifeContractError(f"{path}: 城市事实表检查失败：\n  " + "\n  ".join(problems))
    return facts
