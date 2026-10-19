"""疾病与应急（健康规则 e1-5c-health-v1，`DECISIONS/0027`）：病由指定事件或抽签产生；年度计划里的应急安排决定治不治；
病在执行中发生，不回到年初重问。

K51 的数字：
* 小病：照常上班；不治持续 2 个月，每个病月本人恢复 10 小时（每小时负担 2.0）；门诊 300，治了当月月末恢复。
* 大病：整月病假、没有工资；暂停有参与时间的主动活动；不治持续 3 个月，每个病月恢复 60 小时；住院 2,000，治了当月月末恢复。

基本安排（远处住房、基本食品、全职文员）不生病时年末现金 14,640。
"""

from __future__ import annotations

from common.life_contracts import IllnessEventSource
from econ.life_settlement import settle_year

from .conftest import CLONE_K, K51, base_items, tax_for, whole, year_action
from .settle_support import cents, item, revise

TREAT = {"minor": "treat_if_possible", "major": "treat_if_possible", "pause_order": []}
DECLINE = {"minor": "decline", "major": "decline", "pause_order": []}


def _ill(bundle, severity, month=3):
    src = IllnessEventSource.model_validate(
        {
            "mode": "injected",
            "stream_version": "e1-5c-illness-stream-v1",
            "note": "测试用的指定事件",
            "injected": [
                {
                    "person_id": "u301",
                    "repeat_index": 0,
                    "year_index": 1,
                    "month": month,
                    "severity": severity,
                    "note": f"测试：第 {month} 月{severity}",
                }
            ],
        }
    )
    return revise(bundle.protocol, illness_events=src)


def _settle(bundle, items, *, protocol, emergency, catalog=None, state=None):
    state = state or bundle.initial_state(CLONE_K)
    return settle_year(
        protocol=protocol,
        state=state,
        action=year_action(state, items, emergency=emergency),
        catalog=catalog or bundle.catalog_for(K51),
        tax=tax_for(bundle, K51),
    )


def _sick(r):
    return [
        (m.month, m.state, None if m.treatment is None else m.treatment.status)
        for m in r.outcome.health.months
        if m.state != "healthy"
    ]


def _wage_months(r):
    return [m.month for m in r.outcome.months if cents(m.income_gross) > 0]


def test_an_untreated_minor_illness_lasts_two_months_and_work_goes_on(bundle):
    r = _settle(bundle, base_items(), protocol=_ill(bundle, "minor"), emergency=DECLINE)
    assert _sick(r) == [(3, "minor", "declined"), (4, "minor", "declined")]
    assert _wage_months(r) == list(range(1, 13))
    months = {m.month: m for m in r.outcome.health.months}
    assert months[3].recovery_hours.fraction() == 10 and months[3].recovery_burden.fraction() == 20
    assert cents(r.outcome.cash_end) == 1464000  # 没花钱，工资照领


def test_a_treated_minor_illness_ends_the_same_month_and_is_charged_once(bundle):
    r = _settle(bundle, base_items(), protocol=_ill(bundle, "minor"), emergency=TREAT)
    assert _sick(r) == [(3, "minor", "treated")]
    assert cents(r.outcome.health.months[2].treatment.amount_paid) == 30000
    assert cents(r.outcome.cash_end) == 1464000 - 30000


def test_an_untreated_major_illness_is_three_months_of_unpaid_sick_leave(bundle):
    r = _settle(bundle, base_items(), protocol=_ill(bundle, "major"), emergency=DECLINE)
    assert [s[0] for s in _sick(r)] == [3, 4, 5]
    assert all(m.sick_leave for m in r.outcome.health.months if m.state == "major")
    assert _wage_months(r) == [1, 2, 6, 7, 8, 9, 10, 11, 12]
    # 工资 9 × 2,500 = 22,500，税 (22,500 − 12,000) × 10% = 1,050；年末 3,000 + 22,500 − 1,050 − 12 × 1,380
    assert cents(r.outcome.cash_end) == 300000 + 2250000 - 105000 - 12 * 138000


def test_a_treated_major_illness_costs_one_month_of_pay_and_the_hospital(bundle):
    r = _settle(bundle, base_items(), protocol=_ill(bundle, "major"), emergency=TREAT)
    assert _sick(r) == [(3, "major", "treated")]
    assert _wage_months(r) == [1, 2, *range(4, 13)]
    # 工资 11 × 2,500 = 27,500，税 1,550，住院 2,000
    assert cents(r.outcome.cash_end) == 300000 + 2750000 - 155000 - 12 * 138000 - 200000


def test_a_major_illness_pauses_activities_that_need_you_there(bundle):
    """健身每月要自己去 8 次：大病的月份暂停（不付钱、不算被拒），病好以后照常。"""
    items = [*base_items(), whole("k51.service.gym")]
    r = _settle(bundle, items, protocol=_ill(bundle, "major"), emergency=DECLINE)
    gym = item(r, "k51.service.gym")
    assert [p.month for p in gym.months_paused] == [3, 4, 5]
    assert 3 not in gym.covered_months and 6 in gym.covered_months
    assert not gym.rejections


def test_wanting_treatment_where_the_data_is_unknown_stops_that_month(bundle):
    """要治而这座城的住院资料未知：停在生病的那个月（资料缺口），不改成"不治"，也不当免费。"""
    cat = bundle.catalog_for(K51)
    data = cat.model_dump(mode="json")
    for t in data["treatments"]:
        if t["severity"] == "major":
            for k in ("price", "visit_hours", "burden_per_hour"):
                t.pop(k, None)
            t["availability"] = "unknown"
            t["unknown_reason"] = "合成情景：住院的价格与床位没有采集到"
    unknown = type(cat).model_validate(data)
    r = _settle(
        bundle, base_items(), protocol=_ill(bundle, "major"), emergency=TREAT, catalog=unknown
    )
    assert r.status == "stopped" and r.outcome.settlement_status == "stopped"
    stop = r.outcome.stop
    assert (stop.kind, stop.month) == ("data_gap", 3)
    assert [m.month for m in r.outcome.months] == [
        1,
        2,
    ]  # 停在 3 月月初：之前的月份已结算，3 月本身不记账
    declined = _settle(
        bundle, base_items(), protocol=_ill(bundle, "major"), emergency=DECLINE, catalog=unknown
    )
    assert declined.status == "completed"  # 计划本来就不治：资料未知不影响


def test_the_same_seed_gives_the_same_random_illnesses(bundle):
    """抽签模式：同一颗种子、同一份计划，病月逐月相同；每个月的抽签都记下来。"""
    src = IllnessEventSource.model_validate(
        {
            "mode": "random",
            "stream_version": "e1-5c-illness-stream-v1",
            "pairing_id": "pair.test",
            "seed": 20261019,
            "note": "测试用抽签",
        }
    )
    protocol = revise(bundle.protocol, illness_events=src)
    a = _settle(bundle, base_items(), protocol=protocol, emergency=TREAT)
    b = _settle(bundle, base_items(), protocol=protocol, emergency=TREAT)
    assert a.outcome.health == b.outcome.health
    drawn = [m for m in a.outcome.health.months if m.draw is not None]
    assert drawn and a.outcome.health.source_mode == "random"
