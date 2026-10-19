"""一个分身的跨期评价（`DECISIONS/0028`）：实际 N 年的 L_actual、期末估值 V_reference 与合成分 J。

* δ = 1：L_actual 就是 36 个月满足感的平均；
* J = 实际部分的份额 × L_actual + 期末部分的份额 × V_reference，两部分都完整才有 J——缺了不改成 L_actual，也不重新归一化；
* 付不起最低生活的出局是确认的生活结果，单独分类，不当成未知分数。
"""

from __future__ import annotations

import pytest

from econ.life_evaluation import evaluate_clone

from .conftest import CLONE_K, K51, base_items, live_year, scoring_inputs, tax_for, whole
from .settle_support import revise


def _evaluate(bundle, scoring, evaluation, results):
    return evaluate_clone(
        spec=evaluation,
        protocol=bundle.protocol,
        catalog=bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
        scoring=scoring_inputs(bundle, scoring, K51),
        results=results,
    )


def _three_years(bundle, scoring, items, state=None):
    state = state or bundle.initial_state(CLONE_K)
    out = []
    for _ in range(3):
        r = live_year(bundle, scoring, state, items)
        out.append(r)
        if r.next_state is None or r.handoff != "next_year_ready":
            break
        state = r.next_state
    return out


@pytest.fixture(scope="module")
def lived(bundle, scoring, evaluation):
    results = _three_years(bundle, scoring, base_items())
    return results, _evaluate(bundle, scoring, evaluation, results)


def test_l_actual_is_the_mean_of_the_36_months(lived):
    results, ev = lived
    us = [m.u for r in results for m in r.score.months]
    assert len(us) == 36 and ev.trajectory.status == "complete"
    assert ev.actual.status == "complete"
    assert float(ev.actual.l_actual.fraction()) == pytest.approx(sum(us) / 36, abs=1e-12)


def test_j_combines_the_lived_years_and_the_terminal_value(lived):
    _results, ev = lived
    c = ev.combined
    assert c.status == "complete"
    share_a, share_f = c.share_actual.fraction(), c.share_future.fraction()
    assert share_a + share_f == 1
    l_actual, v_ref = ev.actual.l_actual.fraction(), ev.valuation.v_reference.fraction()
    assert c.j.fraction() == share_a * l_actual + share_f * v_ref  # 精确分数，逐位相等


def test_the_terminal_value_takes_the_best_reference_plan(lived):
    """期末估值：从真实终态出发，把声明的几种参考方案各真实结算、评分 12 个月，取最高；真实终态本身不变。"""
    results, ev = lived
    complete = [p for p in ev.valuation.plans if p.status == "complete"]
    assert complete
    assert ev.valuation.v_reference.fraction() == max(p.v_plan.fraction() for p in complete)
    assert results[-1].next_state.cash.amount == pytest.approx(
        ev.valuation.context.start.cash.amount
    )


def test_evaluation_is_deterministic(bundle, scoring, evaluation, lived):
    results, ev = lived
    assert (
        _evaluate(bundle, scoring, evaluation, results).result_fingerprint == ev.result_fingerprint
    )


def test_a_life_exit_is_its_own_category_and_has_no_j(bundle, scoring, evaluation):
    """没有工作、现金只够一个月：第 1 年 2 月出局。它是确认的生活结果，不是"分数未知"，也不进城市平均。"""
    poor = revise(
        bundle.initial_state(CLONE_K), cash={"amount": 1380, "numeraire": "USD_2024_LOCAL"}
    )
    results = _three_years(
        bundle, scoring, [whole("k51.housing.far"), whole("k51.food.basic")], state=poor
    )
    assert len(results) == 1 and results[0].settlement.status == "exited"
    ev = _evaluate(bundle, scoring, evaluation, results)
    assert (ev.trajectory.status, ev.trajectory.owner_category) == ("life_exit", "life_exit")
    assert ev.trajectory.exit.month == 2
    assert ev.combined.j is None and ev.actual.l_actual is None
