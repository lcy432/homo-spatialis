"""E1-6A 测试的辅助函数：按样例计划跑实际生活、调用单分身评价、按编号取参考方案的结果。

不是通用 runner：实际生活只按样例里写好的年度计划循环调用 ``econ.life_year.run_life_year``（结算 → 评分 → 反馈）；
评价只调用 ``econ.life_evaluation.evaluate_clone``。分数比较用精确分数（十进制写法 → ``Fraction``），不用容差。
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Any

from common.life_contracts import ExactAmount, LifeProtocol, PersonYearState, YearAction
from common.life_evaluation_contracts import CloneEvaluation, EvaluationSpec, PlanEvaluation
from common.life_feedback_contracts import LifeYearResult
from econ.life_evaluation import evaluate_clone
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle

from .settle_support import catalog_of, revise, state_of, tax_of

A93 = "US:SYNTH2026:A93"
B94 = "US:SYNTH2026:B94"
HOME = {"opportunity_id": "a93.housing.far", "quantity": 1, "start_month": 1, "end_month": 12}
NEAR = {"opportunity_id": "a93.housing.near", "quantity": 1, "start_month": 1, "end_month": 12}
FOOD = {"opportunity_id": "a93.food.basic", "quantity": 1, "start_month": 1, "end_month": 12}
FRESH = {"opportunity_id": "a93.food.fresh", "quantity": 1, "start_month": 1, "end_month": 12}
SHOP = {"opportunity_id": "a93.job.shop_parttime", "quantity": 1, "start_month": 1, "end_month": 12}
DRYER_ID = "a93.durable.washer_dryer"
COURSE_ID = "a93.training.accounting_course"
TREAT = {"minor": "treat_if_possible", "major": "treat_if_possible"}

#: 样例里几种月份的逐月满足感（评分器的最短十进制写法；手算见 docs/design/e1-6-evaluation.md）。
S_BASIC = "0.5420722891566265"  # 门店兼职、远处住房、基本食品、没有洗衣设备、健康
S_BASIC_LAUNDRY = "0.5820722891566266"  # 同上，加一台在用的洗衣设备
S_BASIC_COURSE = "0.5598500669344042"  # 基本生活，当月上会计课（成长 1/12）
S_SICK_BASIC = "0.3594838709677419"  # 基本生活，大病病假（没有到岗）
S_COMFORT = "0.6566137566137566"  # 门店兼职、近处住房、新鲜食材、健身、洗衣设备
S_ACCOUNTANT_BASIC = "0.5316923076923077"  # 会计、远处住房、基本食品
S_ACCOUNTANT_COMFORT = "0.6481481481481481"  # 会计、近处住房、新鲜食材、健身、洗衣设备


def f(text: str) -> Fraction:
    """十进制写法 → 精确分数。"""
    return Fraction(Decimal(text))


def x(amount: ExactAmount | None) -> Fraction:
    assert amount is not None
    return amount.fraction()


def scoring_inputs(
    bundle: LifeBundle,
    config: Any,
    state: PersonYearState,
    profile: Any = None,
    conditions: Any = None,
) -> ScoringInputs:
    profile = profile or next(p for p in bundle.stated_profiles if p.person_id == state.person_id)
    return ScoringInputs(
        spec=config.spec,
        conditions=conditions or config.conditions_for(str(state.city)),
        preference_input=profile,
        hard_requirements=profile.hard_requirements,
    )


def run_clone(
    bundle: LifeBundle,
    config: Any,
    clone: str,
    *,
    protocol: LifeProtocol | None = None,
    state: PersonYearState | None = None,
    plans: list[YearAction] | None = None,
    profile: Any = None,
    conditions: Any = None,
) -> list[LifeYearResult]:
    """按预设（或给定）的年度计划逐年交接，直到终态、出局、停止、结算失败或反馈信息不足。"""
    state = state or state_of(bundle, clone)
    catalog = catalog_of(bundle, state)
    tax = tax_of(bundle, catalog)
    scoring = scoring_inputs(bundle, config, state, profile, conditions)
    out: list[LifeYearResult] = []
    for p in plans if plans is not None else bundle.plans_for(clone):
        r = run_life_year(
            protocol=protocol or bundle.protocol,
            state=state,
            action=p,
            catalog=catalog,
            tax=tax,
            scoring=scoring,
        )
        out.append(r)
        if r.handoff != "next_year_ready":
            break
        assert r.next_state is not None
        state = r.next_state
    return out


def evaluate(
    bundle: LifeBundle,
    config: Any,
    spec: EvaluationSpec,
    clone: str,
    results: list[LifeYearResult],
    *,
    protocol: LifeProtocol | None = None,
    profile: Any = None,
    conditions: Any = None,
    catalog: Any = None,
) -> CloneEvaluation:
    state = state_of(bundle, clone)
    catalog = catalog or catalog_of(bundle, state)
    return evaluate_clone(
        spec=spec,
        protocol=protocol or bundle.protocol,
        catalog=catalog,
        tax=tax_of(bundle, catalog),
        scoring=scoring_inputs(bundle, config, state, profile, conditions),
        results=results,
    )


def plan(ev: CloneEvaluation, plan_id: str) -> PlanEvaluation:
    return next(p for p in ev.valuation.plans if p.plan_id == plan_id)


def spec_with(spec: EvaluationSpec, **changes: Any) -> EvaluationSpec:
    return revise(spec, **changes)


def year_plan(
    bundle: LifeBundle, clone: str, year: int, *items: dict[str, Any], **changes: Any
) -> YearAction:
    s = state_of(bundle, clone)
    data: dict[str, Any] = {
        "person_id": s.person_id,
        "clone_id": clone,
        "year_index": year,
        "items": list(items),
        "emergency": dict(TREAT),
    }
    data.update(changes)
    return YearAction.model_validate(data)


def spec_a81() -> EvaluationSpec:
    """E1-5B 样例（目标储蓄，没有疾病规则）的一份最小评价规格：一个"现职 + 基本"模板，只绑定 A81。"""
    return EvaluationSpec.model_validate(
        {
            "spec_id": "eval.test.a81",
            "version": "e1-6a-evaluation-v2",
            "status": "exploratory_synthetic",
            "execution_revision": "e1-6a-r2",
            "monthly_discount_factor": "1",
            "valuation_horizon_months": 12,
            "valuation_event_condition": "no_new_illness",
            "treatment_branching": "by_continuing_illness_v1",
            "selection_rule": "best_complete_feasible_in_reference_set",
            "combination": "month_weighted_actual_plus_reference_v1",
            "slots": [
                {"slot_id": "housing_basic", "role": "housing", "description": "远处住房"},
                {"slot_id": "food_basic", "role": "food", "description": "基本食品"},
            ],
            "life_templates": [
                {
                    "template_id": "p1_current_basic",
                    "label": "现职 + 基本",
                    "jobs": "current_positions",
                    "housing_slot": "housing_basic",
                    "food_slot": "food_basic",
                    "description": "延续当前岗位的基本生活",
                }
            ],
            "city_bindings": [
                {
                    "city": {"country": "US", "scheme": "SYNTH2026", "code": "A81"},
                    "targets": [
                        {
                            "slot_id": "housing_basic",
                            "status": "opportunity",
                            "opportunity_id": "a81.housing.far",
                        },
                        {
                            "slot_id": "food_basic",
                            "status": "opportunity",
                            "opportunity_id": "a81.food.basic",
                        },
                    ],
                }
            ],
        }
    )
