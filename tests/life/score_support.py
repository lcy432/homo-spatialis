"""E1-3 评分测试的辅助函数：先按 E1-2 规则结算一年，再对实际结果做探索评分。

评分是浮点运算，比较用显式的紧容差 ``APPROX``（1e-12）；金额仍按整数分精确核对。
"""

from __future__ import annotations

from typing import Any

from common.life_contracts import PersonYearState, SettlementResult, YearAction
from common.life_scoring_contracts import ExploratoryYearScore, ScoringPreference
from econ.life_scoring import (
    preference_from_research_truth,
    preference_from_stated_profile,
    score_year,
)
from ledger.life_catalog import LifeBundle

from .settle_support import catalog_of, plan_of, settle, state_of

APPROX: dict[str, float] = {"abs": 1e-12}


def profile_of(bundle: LifeBundle, person_id: str):
    return next(p for p in bundle.stated_profiles if p.person_id == person_id)


def truth_of(bundle: LifeBundle, person_id: str):
    return next(t for t in bundle.internal_truths if t.person_id == person_id)


def research_pref(bundle: LifeBundle, person_id: str) -> ScoringPreference:
    p = preference_from_research_truth(truth_of(bundle, person_id))
    assert isinstance(p, ScoringPreference), p
    return p


def product_pref(bundle: LifeBundle, person_id: str):
    return preference_from_stated_profile(profile_of(bundle, person_id))


def settle_year_of(
    bundle: LifeBundle,
    clone_id: str,
    *,
    year: int = 1,
    plan: YearAction | None = None,
    state: PersonYearState | None = None,
) -> tuple[PersonYearState, SettlementResult]:
    """结算一年，返回（这一年年初的状态，结算结果）。第 2 年起需要显式给 ``state``。"""
    state = state or state_of(bundle, clone_id)
    r = settle(
        bundle, clone_id, year=year, plan=plan or plan_of(bundle, clone_id, year), state=state
    )
    assert r.outcome is not None, (r.status, [p.message for p in r.problems])
    return state, r


def score_of(
    bundle: LifeBundle,
    scoring: Any,
    state: PersonYearState,
    result: SettlementResult,
    *,
    preference: ScoringPreference | None = None,
    hard: tuple | None = None,
    conditions: Any = None,
    catalog: Any = None,
    spec: Any = None,
) -> ExploratoryYearScore:
    assert result.outcome is not None
    return score_year(
        protocol=bundle.protocol,
        spec=spec or scoring.spec,
        conditions=conditions or scoring.conditions_for(str(state.city)),
        catalog=catalog or catalog_of(bundle, state),
        state=state,
        outcome=result.outcome,
        preference=preference or research_pref(bundle, state.person_id),
        hard_requirements=profile_of(bundle, state.person_id).hard_requirements
        if hard is None
        else hard,
    )


def component(score: ExploratoryYearScore, month: int, component_id: str):
    m = score.months[month - 1]
    return next(c for c in m.components if c.component_id == component_id)


def s_series(score: ExploratoryYearScore, component_id: str) -> list[float | None]:
    out: list[float | None] = []
    for m in score.months:
        c = next((c for c in m.components if c.component_id == component_id), None)
        out.append(None if c is None else c.s)
    return out


def dimension(score: ExploratoryYearScore, month: int, dim: str):
    return next(d for d in score.months[month - 1].dimensions if d.dimension == dim)
