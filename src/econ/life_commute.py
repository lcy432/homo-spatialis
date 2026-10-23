"""通勤的执行事实（DECISIONS/0042）。

一个月的通勤只由这几样决定：这个月实际住的那一处（它的居住片区）、这个月实际到岗的岗位（各自的就业片区与到岗次数）、这个月
在用的功能（开车要“用车权”之类）。按目录的通勤关系表与选择规则 ``fastest_available_v1`` 定方式；花费 = 到岗次数 × 每次往返的
花费，同一方式的月合计与月度封顶取小。结果是唯一的执行事实 ``MonthCommute``：结算按它付钱，资源账记它的小时，交通评分与通勤
上限读它的分钟。

到岗岗位从这处住房没有任何可用方式（关系表里没有这条线，或只有的方式条件不满足），是计划在这一处执行不了——
``CommuteUnavailableError``，由结算报“计划不可行”；住房或岗位没写片区、城市没有关系表，是资料问题（``LifeContractError``）。
"""

from __future__ import annotations

from collections.abc import Sequence, Set
from dataclasses import dataclass
from decimal import Decimal

from common.life_billing import money_of_minor
from common.life_contracts import (
    COMMUTE_MODE_LABEL,
    COMMUTE_MODES,
    CityLifeCatalog,
    CommuteLeg,
    CommuteUse,
    LifeContractError,
    LifeOpportunity,
    MonthCommute,
    exact_minor_units,
)


class CommuteUnavailableError(Exception):
    """到岗岗位从这处住房没有可用的通勤方式：计划执行不了（不是资料错误，也不是生活出局）。"""


@dataclass(frozen=True)
class OnsiteJob:
    """这个月实际到岗的一份岗位与它的到岗次数。"""

    opp: LifeOpportunity
    visits: int


def _minor(amount: float, digits: int, what: str) -> int:
    m = exact_minor_units(amount, digits)
    if m is None:  # 目录校验已检查精度
        raise LifeContractError(f"{what} 的精度细于最小记账单位")
    return m


def available_legs(
    catalog: CityLifeCatalog, home_area: str, work_area: str, functions_in_use: Set[str]
) -> list[CommuteLeg]:
    """这条线这个月可用的方式（按 ``COMMUTE_MODES`` 的顺序）：关系表里有这一格，并且方式的条件满足。"""
    rel = catalog.commute
    if rel is None:
        return []
    out = []
    for mode in COMMUTE_MODES:
        leg, rule = rel.leg(home_area, work_area, mode), rel.mode_rule(mode)
        if leg is None or rule is None:
            continue
        if rule.requires_function is not None and rule.requires_function not in functions_in_use:
            continue
        out.append(leg)
    return out


def choose_leg(legs: Sequence[CommuteLeg], digits: int) -> CommuteLeg:
    """``fastest_available_v1``：往返分钟最少；相同取每次花费低的；再相同按方式的固定顺序。"""
    order = {m: i for i, m in enumerate(COMMUTE_MODES)}
    return min(
        legs,
        key=lambda g: (
            g.roundtrip_minutes,
            _minor(g.cost_per_roundtrip.amount, digits, "每次往返的花费"),
            order[g.mode],
        ),
    )


def _unavailable_why(catalog: CityLifeCatalog, home_area: str, work_area: str) -> str:
    rel = catalog.commute
    assert rel is not None
    present = [m for m in COMMUTE_MODES if rel.leg(home_area, work_area, m) is not None]
    if not present:
        return f"通勤关系表里没有 {home_area}→{work_area} 这条线（任何方式都没有资料，不当 0 分钟）"
    needs = []
    for m in present:
        rule = rel.mode_rule(m)
        if rule is not None and rule.requires_function is not None:
            needs.append(f"{COMMUTE_MODE_LABEL[m]}要有在用的 {rule.requires_function}")
    return f"{home_area}→{work_area} 只能" + "、".join(needs) + "，这个月没有"


def executed_commute(
    catalog: CityLifeCatalog,
    month: int,
    home: LifeOpportunity | None,
    jobs: Sequence[OnsiteJob],
    functions_in_use: Set[str],
) -> MonthCommute:
    """这个月的通勤执行记录。``home`` 是这个月实际住的那一处（含兜底）；``jobs`` 只含这个月实际到岗的岗位。"""
    housing_id = home.opportunity_id if home is not None else None
    home_area = home.area if home is not None else None
    if not jobs:
        return MonthCommute(
            month=month,
            status="no_onsite_work",
            housing_opportunity_id=housing_id,
            home_area=home_area,
        )
    if home is None:
        return MonthCommute(month=month, status="unknown_no_housing")
    rel = catalog.commute
    if rel is None:
        raise LifeContractError(f"{catalog.city}: 有需要到岗的岗位，却没有通勤关系表")
    if home.area is None:
        raise LifeContractError(f"{home.opportunity_id} 没有写居住片区：通勤读不到（不当 0 分钟）")
    digits = catalog.money_basis.minor_unit_digits
    numeraire = catalog.money_basis.numeraire_id
    uses: list[CommuteUse] = []
    by_mode: dict[str, int] = {}
    for j in jobs:
        if j.opp.area is None:
            raise LifeContractError(f"{j.opp.opportunity_id} 没有写就业片区：通勤读不到")
        legs = available_legs(catalog, home.area, j.opp.area, functions_in_use)
        if not legs:
            raise CommuteUnavailableError(
                f"从 {home.opportunity_id} 到岗位 {j.opp.opportunity_id} 没有可用的通勤方式："
                + _unavailable_why(catalog, home.area, j.opp.area)
            )
        g = choose_leg(legs, digits)
        cost = j.visits * _minor(g.cost_per_roundtrip.amount, digits, "每次往返的花费")
        by_mode[g.mode] = by_mode.get(g.mode, 0) + cost
        others = "、".join(
            f"{COMMUTE_MODE_LABEL[x.mode]} {x.roundtrip_minutes} 分钟" for x in legs if x is not g
        )
        uses.append(
            CommuteUse(
                job_opportunity_id=j.opp.opportunity_id,
                home_area=home.area,
                work_area=j.opp.area,
                mode=g.mode,
                roundtrip_minutes=g.roundtrip_minutes,
                onsite_visits=j.visits,
                cost=money_of_minor(cost, digits, numeraire),
                basis=(
                    f"可用方式里最快的是{COMMUTE_MODE_LABEL[g.mode]}（往返 {g.roundtrip_minutes} 分钟）"
                    + (f"；另可 {others}" if others else "")
                    + f"；到岗 {j.visits} 次"
                ),
            )
        )
    paid = 0
    notes: list[str] = []
    for mode in COMMUTE_MODES:
        if mode not in by_mode:
            continue
        total = by_mode[mode]
        rule = rel.mode_rule(mode)
        assert rule is not None
        if rule.monthly_cap is not None:
            cap = _minor(rule.monthly_cap.amount, digits, "月度封顶")
            if total > cap:
                notes.append(
                    f"{COMMUTE_MODE_LABEL[mode]}本月合计 {Decimal(total).scaleb(-digits)} 超过封顶 "
                    f"{Decimal(cap).scaleb(-digits)}，按封顶付"
                )
                total = cap
        paid += total
    return MonthCommute(
        month=month,
        status="computed",
        housing_opportunity_id=housing_id,
        home_area=home.area,
        uses=tuple(uses),
        paid=money_of_minor(paid, digits, numeraire),
        notes=tuple(notes),
    )
