"""E1-5A 测试的辅助函数：按预先声明的计划逐年做年度交接、换掉某一年的计划、派生样例。

不是通用 runner：只按样例里写好的年度计划循环调用 ``econ.life_year.run_life_year``（结算 → 评分 → 反馈）。
"""

from __future__ import annotations

from typing import Any

from common.life_contracts import PersonYearState, YearAction
from common.life_feedback_contracts import LifeYearResult
from econ.life_settlement import settle_year
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle

from .settle_support import catalog_of, plan_of, state_of, tax_of, with_items

#: 基本生活：近处住房、新鲜食材（每年都有），手算见样例文末。
HOME = {"opportunity_id": "a71.housing.near", "quantity": 1, "start_month": 1, "end_month": 12}
FOOD = {"opportunity_id": "a71.food.fresh", "quantity": 1, "start_month": 1, "end_month": 12}
WASHER = {"opportunity_id": "a71.durable.washer", "quantity": 1, "month": 1}
#: 最低生活：远处住房、基本食材（每月 1,000 + 380 = 1,380）。
FAR_HOME = {"opportunity_id": "a71.housing.far", "quantity": 1, "start_month": 1, "end_month": 12}
BASIC_FOOD = {"opportunity_id": "a71.food.basic", "quantity": 1, "start_month": 1, "end_month": 12}


def job(oid: str, start: int = 1, end: int = 12) -> dict[str, Any]:
    return {"opportunity_id": oid, "quantity": 1, "start_month": start, "end_month": end}


def course(oid: str, start: int = 1, end: int = 12) -> dict[str, Any]:
    return {"opportunity_id": oid, "quantity": 1, "start_month": start, "end_month": end}


CLERK = "a71.job.clerk_netco"
ACCOUNTANT = "a71.job.accountant_netco"
DEVELOPER = "a71.job.developer_netco"
HR_NETCO = "a71.job.hr_netco"
HR_LEDGERCO = "a71.job.hr_ledgerco"
ACCOUNTING = "a71.training.accounting_course"
PYTHON = "a71.training.python_selfstudy"
HR_TRAINING = "a71.training.hr_rules_netco"


def inputs(bundle: LifeBundle, config: Any, state: PersonYearState) -> ScoringInputs:
    profile = next(p for p in bundle.stated_profiles if p.person_id == state.person_id)
    return ScoringInputs(
        spec=config.spec,
        conditions=config.conditions_for(str(state.city)),
        preference_input=profile,
        hard_requirements=profile.hard_requirements,
    )


def year(
    bundle: LifeBundle,
    config: Any,
    state: PersonYearState,
    plan: YearAction,
    *,
    protocol: Any = None,
    with_scoring: bool = True,
) -> LifeYearResult:
    """用这份计划做一次年度交接（结算 → 评分 → 反馈；反馈关闭的对照臂不评分）。"""
    catalog = catalog_of(bundle, state)
    return run_life_year(
        protocol=protocol or bundle.protocol,
        state=state,
        action=plan,
        catalog=catalog,
        tax=tax_of(bundle, catalog),
        scoring=inputs(bundle, config, state) if with_scoring else None,
    )


def settle(bundle: LifeBundle, state: PersonYearState, plan: YearAction, protocol: Any = None):
    catalog = catalog_of(bundle, state)
    return settle_year(
        protocol=protocol or bundle.protocol,
        state=state,
        action=plan,
        catalog=catalog,
        tax=tax_of(bundle, catalog),
    )


def run_clone(bundle: LifeBundle, config: Any, clone_id: str) -> list[LifeYearResult]:
    """按样例里预先声明的年度计划逐年交接，直到终态、出局、结算失败或反馈信息不足。"""
    state = state_of(bundle, clone_id)
    out: list[LifeYearResult] = []
    for plan in bundle.plans_for(clone_id):
        if plan.year_index != state.year_index:
            continue
        r = year(bundle, config, state, plan)
        out.append(r)
        if r.handoff != "next_year_ready":
            break
        assert r.next_state is not None
        state = r.next_state
    return out


def plan_for(
    bundle: LifeBundle, clone_id: str, year_index: int, *items: dict[str, Any]
) -> YearAction:
    """同一个分身同一年，换一组行动（理由照抄）。"""
    return with_items(plan_of(bundle, clone_id, year_index), *items)


def income(r: Any) -> tuple[float, float]:
    """一年的税前工资与合成税（金额：当地基年不变价）。"""
    out = r.settlement.outcome
    gross = sum(f.amount.amount for f in out.cash_flows if f.kind == "income_gross")
    tax = -sum(f.amount.amount for f in out.cash_flows if f.kind == "income_tax")
    return gross, tax


def max_burden(r: Any) -> float:
    months = r.settlement.outcome.resources.months
    return float(max(m.energy_used.fraction() for m in months if m.energy_used is not None))


def max_hours(r: Any) -> float:
    months = r.settlement.outcome.resources.months
    return float(max(m.time_used.fraction() for m in months if m.time_used is not None))
