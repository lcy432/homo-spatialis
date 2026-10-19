"""E1-6B 测试的辅助：按冻结名单跑一个小批次（实际生活 → 各运行臂评价），以及手工构造名单项记录做公式单测。

不是 E1-7 的调度器：只按样例里预先写好的年度计划循环调用 ``run_life_year``，再对每个运行臂调用 ``evaluate_clone``。
``LifeContractError`` 记成"输入或配置被拒绝"的运行失败；其他异常记成意外程序错误（显式报告，不吞掉）。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from common.life_aggregation_contracts import EntryRecord, FrozenRoster, RunFailure
from common.life_contracts import ExactAmount, LifeContractError
from common.life_evaluation_contracts import CloneEvaluation, EvaluationSpec
from common.life_feedback_contracts import LifeYearResult
from econ.life_aggregation import freeze_roster
from econ.life_evaluation import evaluate_clone
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle
from ledger.life_roster import load_roster
from ledger.life_scoring_config import ScoringConfig

ROSTER = Path("configs/life/roster_e1_6b.yaml")
#: 演示与测试刻意不运行的名单项（模拟"结果没有返回"）：它仍在原计划名单里。
SKIP = frozenset({"u101.a95.r4"})
A95, B96 = "US:SYNTH2026:A95", "US:SYNTH2026:B96"


@dataclass(frozen=True)
class Batch:
    bundle: LifeBundle
    scoring: ScoringConfig
    specs: dict[str, EvaluationSpec]
    roster: FrozenRoster
    lives: dict[str, list[LifeYearResult]]
    evaluations: dict[str, list[CloneEvaluation]]
    failures: dict[str, list[RunFailure]]


def scoring_for(bundle: LifeBundle, scoring: ScoringConfig, clone: str) -> ScoringInputs:
    state = bundle.initial_state(clone)
    assert state is not None
    profile = next(p for p in bundle.stated_profiles if p.person_id == state.person_id)
    return ScoringInputs(
        spec=scoring.spec,
        conditions=scoring.conditions_for(str(state.city)),
        preference_input=profile,
        hard_requirements=profile.hard_requirements,
    )


def live(
    bundle: LifeBundle, scoring: ScoringConfig, clone: str, plans: list | None = None
) -> list[LifeYearResult]:
    """按样例的年度计划（或测试给的替代计划）逐年运行，交接不是 next_year_ready 就停。"""
    state = bundle.initial_state(clone)
    assert state is not None
    catalog = bundle.catalog_for(str(state.city))
    tax = binding_from_policy(bundle.tax_policy(catalog.tax_policy_id))
    inputs = scoring_for(bundle, scoring, clone)
    out: list[LifeYearResult] = []
    for plan in plans if plans is not None else bundle.plans_for(clone):
        r = run_life_year(
            protocol=bundle.protocol,
            state=state,
            action=plan,
            catalog=catalog,
            tax=tax,
            scoring=inputs,
        )
        out.append(r)
        if r.handoff != "next_year_ready":
            break
        state = r.next_state
    return out


def evaluate(batch_bundle, scoring, spec, clone, results) -> CloneEvaluation:
    state = batch_bundle.initial_state(clone)
    catalog = batch_bundle.catalog_for(str(state.city))
    tax = binding_from_policy(batch_bundle.tax_policy(catalog.tax_policy_id))
    return evaluate_clone(
        spec=spec,
        protocol=batch_bundle.protocol,
        catalog=catalog,
        tax=tax,
        scoring=scoring_for(batch_bundle, scoring, clone),
        results=results,
    )


def run_batch() -> Batch:
    config, bundle, scoring, specs = load_roster(ROSTER)
    roster = freeze_roster(config, bundle, scoring, specs)  # 看结果之前冻结
    lives: dict[str, list[LifeYearResult]] = {}
    evaluations: dict[str, list[CloneEvaluation]] = {a.arm_id: [] for a in config.arms}
    failures: dict[str, list[RunFailure]] = {a.arm_id: [] for a in config.arms}
    for e in config.entries:
        if e.clone_id in SKIP:
            continue
        try:
            lives[e.clone_id] = live(bundle, scoring, e.clone_id)
        except LifeContractError as exc:  # pragma: no cover - 样例里不会发生
            for arm in failures:
                failures[arm].append(
                    RunFailure(
                        clone_id=e.clone_id,
                        stage="life",
                        error_kind="contract",
                        message=str(exc)[:600],
                    )
                )
            continue
        for arm_id, spec in specs.items():
            evaluations[arm_id].append(
                evaluate(bundle, scoring, spec, e.clone_id, lives[e.clone_id])
            )
    return Batch(bundle, scoring, specs, roster, lives, evaluations, failures)


def ex(x: Fraction) -> ExactAmount:
    return ExactAmount.of(x)


def record(category: str, j: Fraction | None = None, *, clone: str, city=A95, **kw) -> EntryRecord:
    """手工构造一个名单项记录（公式单测用；δ = 1、N = 3、H = 12：W_actual = 36、W_future = 12）。"""
    from common.ids import CityId

    w_a, w_f = Fraction(36), Fraction(12)
    base = {
        "user_id": kw.pop("user_id", "user.test"),
        "person_id": kw.pop("person_id", "u900"),
        "clone_id": clone,
        "city": CityId(*str(city).split(":")),
        "repeat_index": kw.pop("repeat_index", 0),
        "category": category,
        "detail": kw.pop("detail", "手工构造"),
        "weight_actual": ex(w_a),
        "weight_future": ex(w_f),
    }
    k = kw.pop("k", Fraction(0))
    w_known = kw.pop("w_known", Fraction(0))
    base.update(known_weighted_sum=ex(k), known_weight=ex(w_known))
    if category == "C":
        assert j is not None
        base.update(j=ex(j), l_actual=ex(j), v_reference=ex(j), lower=ex(j), upper=ex(j))
    elif category == "M":
        from econ.life_aggregation import missing_range_bounds

        v = kw.pop("v", None)
        v_low, v_high, lo, hi = missing_range_bounds(k, w_known, w_a, w_f, v)
        base.update(
            missing_reason=kw.pop("reason", "not_run"),
            v_low=ex(v_low),
            v_high=ex(v_high),
            lower=ex(lo),
            upper=ex(hi),
        )
    base.update(kw)
    return EntryRecord.model_validate(base)
