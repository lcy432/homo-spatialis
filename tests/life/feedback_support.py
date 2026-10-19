"""E1-4B 测试的辅助函数：按预先声明的计划逐年跑年度交接、派生对照臂、派生等价的 E1-4A 输入。

不是通用 runner：只按样例里写好的年度计划循环调用 ``econ.life_year.run_life_year``。
"""

from __future__ import annotations

import copy
from typing import Any

from common.life_contracts import LifeProtocol, PersonYearState, SettlementResult, YearAction
from common.life_feedback_contracts import LifeYearResult, ScoredYear
from econ.life_settlement import settle_year
from econ.life_year import ScoringInputs, run_life_year, settle_and_score
from ledger.life_catalog import LifeBundle, parse_bundle

from .settle_support import catalog_of, plan_of, revise, state_of, tax_of


def profile_of(bundle: LifeBundle, person_id: str):
    return next(p for p in bundle.stated_profiles if p.person_id == person_id)


def truth_of(bundle: LifeBundle, person_id: str):
    return next(t for t in bundle.internal_truths if t.person_id == person_id)


def scoring_inputs(
    bundle: LifeBundle, config: Any, state: PersonYearState, *, research: bool = False
):
    pref = truth_of(bundle, state.person_id) if research else profile_of(bundle, state.person_id)
    return ScoringInputs(
        spec=config.spec,
        conditions=config.conditions_for(str(state.city)),
        preference_input=pref,
        hard_requirements=profile_of(bundle, state.person_id).hard_requirements,
    )


def variant(
    protocol: LifeProtocol,
    *,
    mode: str | None = None,
    alpha: float | None = None,
    policy_id: str | None = None,
    preference_source: str | None = None,
    years: int | None = None,
) -> LifeProtocol:
    """由主协议只改反馈政策（与 N）派生一个对照臂。"""
    policy = protocol.energy_feedback
    assert policy is not None
    changes: dict[str, Any] = {}
    for key, value in (
        ("mode", mode),
        ("alpha", alpha),
        ("policy_id", policy_id),
        ("preference_source", preference_source),
    ):
        if value is not None:
            changes[key] = value
    out = revise(protocol, energy_feedback=revise(policy, **changes))
    if years is not None:
        out = revise(out, simulation_years=years)
    return out


OFF = dict(mode="disabled", policy_id="feedback.e1-4b.off")
ALPHA0 = dict(alpha=0.0, policy_id="feedback.e1-4b.alpha0")
ALPHA005 = dict(alpha=0.05, policy_id="feedback.e1-4b.alpha005")


def run_years(
    bundle: LifeBundle,
    config: Any,
    clone_id: str,
    *,
    protocol: LifeProtocol | None = None,
    with_scoring: bool = True,
    research: bool = False,
    state: PersonYearState | None = None,
) -> list[LifeYearResult]:
    """按样例里预先声明的年度计划逐年交接，直到没有下一行动年、出局、结算失败或反馈信息不足。"""
    protocol = protocol or bundle.protocol
    state = state or state_of(bundle, clone_id)
    catalog = catalog_of(bundle, state)
    tax = tax_of(bundle, catalog)
    out: list[LifeYearResult] = []
    for plan in bundle.plans_for(clone_id):
        if plan.year_index != state.year_index:
            continue
        scoring = scoring_inputs(bundle, config, state, research=research) if with_scoring else None
        r = run_life_year(
            protocol=protocol, state=state, action=plan, catalog=catalog, tax=tax, scoring=scoring
        )
        out.append(r)
        if r.handoff != "next_year_ready":
            break
        assert r.next_state is not None
        state = r.next_state
    return out


def bound_year(
    bundle: LifeBundle,
    config: Any,
    clone_id: str,
    *,
    protocol: LifeProtocol | None = None,
    state: PersonYearState | None = None,
    action: YearAction | None = None,
    with_scoring: bool = True,
) -> ScoredYear:
    """一个分身一年的封存绑定（``econ.life_year.settle_and_score``）：结算并评分，不做反馈。"""
    state = state or state_of(bundle, clone_id)
    catalog = catalog_of(bundle, state)
    return settle_and_score(
        protocol=protocol or bundle.protocol,
        state=state,
        action=action or plan_of(bundle, clone_id, state.year_index),
        catalog=catalog,
        tax=tax_of(bundle, catalog),
        scoring=scoring_inputs(bundle, config, state) if with_scoring else None,
    )


def far_plan(plan: YearAction) -> YearAction:
    """同一份计划，只把 A61 的近处住房换成远处住房（同人、同城、同年的另一次真实试算）。"""
    items = tuple(
        revise(i, opportunity_id="a61.housing.far") if i.opportunity_id == "a61.housing.near" else i
        for i in plan.items
    )
    assert items != plan.items
    return revise(plan, items=items)


def same_id(protocol: LifeProtocol, **changes: Any) -> LifeProtocol:
    """编号与版本都不变、只改政策内容的协议（独立审核 F1 的构造）。"""
    policy = protocol.energy_feedback
    assert policy is not None
    return revise(protocol, energy_feedback=revise(policy, **changes))


def as_e1_4a(raw: dict) -> LifeBundle:
    """同一份 B 样例，改成没有满足感反馈的 E1-4A 输入：生活规则 A、去掉反馈政策与额度依据。"""
    data = copy.deepcopy(raw)
    data["protocol"]["life_rules_version"] = "e1-4a-monthly-v1"
    data["protocol"].pop("energy_feedback")
    for s in data["initial_states"]:
        s.pop("energy_quota_basis")
    return parse_bundle(data)


def settle_chain(bundle: LifeBundle, clone_id: str) -> list[SettlementResult]:
    """E1-4A 的做法：按年结算，上一年的下一状态就是下一年的输入（额度固定）。"""
    state = state_of(bundle, clone_id)
    catalog = catalog_of(bundle, state)
    tax = tax_of(bundle, catalog)
    out: list[SettlementResult] = []
    for plan in bundle.plans_for(clone_id):
        r = settle_year(
            protocol=bundle.protocol, state=state, action=plan, catalog=catalog, tax=tax
        )
        out.append(r)
        if r.status != "completed" or r.next_state is None or r.next_state.status != "active":
            break
        state = r.next_state
    return out
