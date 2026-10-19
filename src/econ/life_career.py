"""E1-5A 学习成果与岗位记录（`DECISIONS/0023`）。纯函数：无 I/O、无随机、无 LLM。

    今年真实参加学习 → 达到完成条件 → 授予课程声明的能力（下一行动年起可用）
    今年实际领到工资的岗位 → 岗位记录与"当前岗位"（下一年年初的职业与雇主事实）

* **授予**：一门课在这一年完成（累计有效参加月数达到要求）时，授予它声明的能力。按能力 ID 去重：已经有的
  不再授予第二次；同一年两门课授予同一能力，只记最早完成的那门（同月按计划顺序）。能力只有"已获得 / 未获得"，
  不叠加等级，也不自动涨薪——工资只来自下一年实际选择并执行的岗位。
* **岗位记录**：这一年实际领到工资的每份岗位与领薪月份；"当前岗位"是最后一个结算月（第 12 月，出局年是出局
  当月）实际在岗的岗位，可以是零份、一份或几份。

结算（``econ.life_settlement``）在年末调用这里，结果写进这一年的结算结果与下一年的候选状态——都在年度绑定
（E1-4B r2 的 ``ScoredYear``）封存之前完成，所以能力与岗位变化属于同一次生活。资格判断本身在
``common.life_contracts.qualification_gaps``（加载、结算与观察共用）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from common.life_contracts import (
    EmploymentRecord,
    HeldAbility,
    LifeOpportunity,
    PositionRecord,
)


@dataclass(frozen=True)
class Completion:
    """一门课在这一年完成的事实：哪门课、第几月、计划里的顺序、声明授予的能力。"""

    opportunity_id: str
    month: int
    plan_order: int
    grants: tuple[str, ...]


def grant_abilities(
    held: Sequence[HeldAbility], completions: Sequence[Completion], year: int
) -> tuple[tuple[HeldAbility, ...], dict[str, tuple[tuple[str, ...], tuple[str, ...]]]]:
    """把这一年完成的课程授予的能力并进已有能力。

    返回（并入后的能力，每门课的（新授予、已经有了））。已有能力原样保留在前面，新能力按完成先后追加；
    授予的能力从 ``year + 1`` 起可用。输入不被修改。
    """
    have = {a.ability_id for a in held}
    out = list(held)
    per: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for c in sorted(completions, key=lambda x: (x.month, x.plan_order)):
        granted: list[str] = []
        already: list[str] = []
        for ability_id in c.grants:
            if ability_id in have:
                already.append(ability_id)
                continue
            have.add(ability_id)
            granted.append(ability_id)
            out.append(
                HeldAbility(
                    ability_id=ability_id,
                    source="training",
                    source_opportunity_id=c.opportunity_id,
                    acquired_year=year,
                    acquired_month=c.month,
                    usable_from_year=year + 1,
                )
            )
        per[c.opportunity_id] = (tuple(granted), tuple(already))
    return tuple(out), per


def employment_after_year(
    start: EmploymentRecord,
    executed: Sequence[tuple[LifeOpportunity, Sequence[int]]],
    year: int,
    last_month: int,
    sick_leave: Mapping[str, Sequence[int]] | None = None,
) -> EmploymentRecord:
    """这一年实际执行（领到工资）的岗位记录。``executed`` 是（岗位机会，领薪月份）；没有领薪月份的岗位不算。

    当前岗位 = 第 ``last_month`` 月实际在岗的岗位。用户开局陈述的职业原样带过去。

    E1-5C：``sick_leave`` 是每份岗位保留雇佣关系的大病病假月份（没有工作、没有工资）。病假不是辞职：最后一个月在病假中
    的岗位仍是当前岗位；只有病假、这一年没有领薪的岗位（年初就在岗）也保留记录。还没实际上过班就遇上大病的新岗位
    不在这里（结算记为"未入职"），所以不授予雇主身份。不传时与 E1-5A 完全相同。
    """
    sick = sick_leave or {}
    positions = tuple(
        PositionRecord(
            opportunity_id=o.opportunity_id,
            occupation=o.occupation,  # type: ignore[arg-type]  # 带薪酬的机会必有职业（契约检查）
            employer_id=o.employer_id,
            months=tuple(sorted(months)),
            sick_leave_months=tuple(sorted(sick.get(o.opportunity_id, ()))),
        )
        for o, months in executed
        if months or sick.get(o.opportunity_id)
    )
    current = tuple(
        p.opportunity_id
        for p in positions
        if last_month in p.months or last_month in p.sick_leave_months
    )
    return EmploymentRecord(
        stated_occupation=start.stated_occupation,
        as_of="settled_year",
        settled_year=year,
        positions=positions,
        current_position_ids=current,
    )
