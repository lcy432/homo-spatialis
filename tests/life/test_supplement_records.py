"""E1-3 增补：持有服务数量直接记录，以及来源类型（`DECISIONS/0019` 第二、四节）。

数量由结算写出、评分直接读取：旧持有与新购数量不同、同一机会跨年续购、到期、次年生效、
记录重排都不改变应有结果。旧记录（持有服务没有数量）只有旧评分版本能读，按 ADR0018 的
顺序推断；对不上就报错，**不默认为 1**。

储物是容量型叶子：z = 0.01 × 升，τ = 2。200 升 → 2/(2+2) = 0.5；400 升 → 4/6。
休闲：z = 4 + 8 × 年卡数（每张年卡声明每月锻炼 8 次），τ = 12。1 张 → 12/24 = 0.5；2 张 → 20/32 = 0.625。

E1-2 的结算规则不允许在还持有同一件持续物品时再买一批（不同剩余期限不能叠成一件），所以"同一机会、
同一年里旧的与新的数量不同"只会出现在年付服务到期后续买；持续物品的续购发生在旧的那批到期之后的年份。
"""

from __future__ import annotations

import datetime as dt

import pytest

from common.life_contracts import LifeContractError, SourceRecord
from common.provenance import Provenance

from .conftest import opportunity
from .score_support import APPROX, s_series, score_of, settle_year_of
from .settle_support import bundle_from, plan_of, revise, with_items

#: 一年的固定生活：近处住房、新鲜食品、线下工作。各测试只在后面追加要比较的物品。
BASE = (
    {"opportunity_id": "a41.housing.near", "quantity": 1, "start_month": 1, "end_month": 12},
    {"opportunity_id": "a41.food.fresh", "quantity": 1, "start_month": 1, "end_month": 12},
    {"opportunity_id": "a41.job.office", "quantity": 1, "start_month": 1, "end_month": 12},
)
CLONE = "u11.a41.c0"
CABINET = "a41.durable.cabinet"
GYM = "a41.service.gym_plan"


def _cabinets(n: float, month: int) -> dict:
    return {"opportunity_id": CABINET, "quantity": n, "month": month}


def _gym(n: float, start: int) -> dict:
    return {"opportunity_id": GYM, "quantity": n, "start_month": start}


def _plan(bundle, year: int, *extra: dict):
    return with_items(plan_of(bundle, CLONE, 1), *BASE, *extra, year_index=year)


def _years(bundle, *extras: tuple[dict, ...]):
    """逐年结算：第 k 年的计划 = BASE + extras[k-1]。返回 [(年初状态, 结算结果), ...]。"""
    out = []
    state = None
    for year, extra in enumerate(extras, start=1):
        s, r = settle_year_of(
            bundle, CLONE, year=year, plan=_plan(bundle, year, *extra), state=state
        )
        out.append((s, r))
        state = r.next_state
    return out


def _storage(bundle, scoring, s, r, **kw) -> list[float | None]:
    return s_series(score_of(bundle, scoring, s, r, **kw), "storage")


def _leisure(bundle, scoring, s, r, **kw) -> list[float | None]:
    return s_series(score_of(bundle, scoring, s, r, **kw), "leisure")


def _strip_quantities(outcome, keep: int = 0):
    """模拟增补之前写出的旧记录：持有服务没有数量（keep > 0 时保留前几条，造部分缺失）。"""
    services = tuple(
        h if i < keep else revise(h, quantity=None) for i, h in enumerate(outcome.holdings_service)
    )
    return revise(outcome, holdings_service=services)


# --------------------------------------------------------------------------
# 数量由结算写出
# --------------------------------------------------------------------------


def test_settlement_writes_the_quantity_of_every_holding(bundle3s):
    _, r = settle_year_of(bundle3s, CLONE)
    got = {h.opportunity_id: h.quantity for h in r.outcome.holdings_service}
    assert got == {
        "a41.durable.phone": 1,
        "a41.durable.laptop": 1,
        "a41.durable.phone_premium": 1,
        "a41.durable.washer": 1,
        CABINET: 2,
        "a41.durable.gadget": 1,
    }


def test_an_old_holding_and_a_new_purchase_keep_their_own_quantities(bundle3s, scoring3s):
    """第 1 年第 7 月买 1 张年卡（覆盖到第 2 年第 6 月），第 2 年第 7 月续买 2 张。

    第 2 年两条记录：旧的 1 张服务第 1–6 月，新的 2 张服务第 7–12 月。
    """
    (_, _), (s2, r2) = _years(bundle3s, (_gym(1, 7),), (_gym(2, 7),))
    gym = [(h.quantity, h.covered_months, h.expired) for h in r2.outcome.holdings_service]
    assert gym == [(1, tuple(range(1, 7)), True), (2, tuple(range(7, 13)), False)]
    want = [0.5] * 6 + [0.625] * 6
    assert _leisure(bundle3s, scoring3s, s2, r2) == pytest.approx(want, **APPROX)
    assert score_of(bundle3s, scoring3s, s2, r2).holding_quantity_source == "recorded"


def test_reordering_the_holding_records_does_not_change_the_score(bundle3s, scoring3s):
    """新记录直接带数量，与写出顺序无关。旧的顺序推断在重排后会把数量配错——这正是要改的原因。"""
    (_, _), (s2, r2) = _years(bundle3s, (_gym(1, 7),), (_gym(2, 7),))
    reordered = revise(r2.outcome, holdings_service=tuple(reversed(r2.outcome.holdings_service)))
    r2b = revise(r2, outcome=reordered)
    want = [0.5] * 6 + [0.625] * 6
    assert _leisure(bundle3s, scoring3s, s2, r2b) == pytest.approx(want, **APPROX)

    # 对照：同样的重排交给旧推断（去掉数量、用旧版本号），旧的与新的数量被对调。
    legacy_spec = revise(scoring3s.spec, scoring_version="e1-3-explore-v1")
    in_order = revise(r2, outcome=_strip_quantities(r2.outcome))
    shuffled = revise(r2, outcome=_strip_quantities(reordered))
    assert _leisure(bundle3s, scoring3s, s2, in_order, spec=legacy_spec) == pytest.approx(
        want, **APPROX
    )
    assert _leisure(bundle3s, scoring3s, s2, shuffled, spec=legacy_spec) == pytest.approx(
        [0.625] * 6 + [0.5] * 6, **APPROX
    )


def test_a_durable_renewed_in_a_later_year_counts_only_the_new_quantity(raw3s, scoring3s):
    """柜子寿命改成 1 年：第 1 年第 1 月买 1 个，年底到期移除；第 2 年第 3 月续买 2 个。"""
    opportunity(raw3s, CABINET)["duration"]["lasts_years"] = 1
    b = bundle_from(raw3s)
    (s1, r1), (s2, r2), (s3, r3) = _years(b, (_cabinets(1, 1),), (_cabinets(2, 3),), ())
    (old,) = r1.outcome.holdings_service
    assert (old.quantity, old.covered_months, old.expired) == (1, tuple(range(1, 13)), True)
    assert all(h.opportunity_id != CABINET for h in r1.next_state.holdings)
    (new,) = r2.outcome.holdings_service
    assert (new.quantity, new.covered_months, new.remaining_months_after) == (
        2,
        tuple(range(3, 13)),
        2,
    )
    assert _storage(b, scoring3s, s1, r1) == pytest.approx([0.5] * 12, **APPROX)
    assert _storage(b, scoring3s, s2, r2) == pytest.approx([0.0] * 2 + [4 / 6] * 10, **APPROX)
    # 第 3 年：续买的两个服务到第 2 月到期，之后不再计入（不读过期的数量）。
    assert _storage(b, scoring3s, s3, r3) == pytest.approx([4 / 6] * 2 + [0.0] * 10, **APPROX)


def test_an_item_that_takes_effect_next_year_brings_nothing_this_year(raw3s, scoring3s):
    opportunity(raw3s, CABINET)["duration"]["effective_from"] = "next_year"
    b = bundle_from(raw3s)
    (s1, r1), (s2, r2) = _years(b, (_cabinets(2, 5),), ())
    assert all(
        not h.covered_months for h in r1.outcome.holdings_service if h.opportunity_id == CABINET
    )
    assert _storage(b, scoring3s, s1, r1) == [0.0] * 12
    assert _storage(b, scoring3s, s2, r2) == pytest.approx([4 / 6] * 12, **APPROX)
    (cab,) = (h for h in r2.outcome.holdings_service if h.opportunity_id == CABINET)
    assert cab.quantity == 2


# --------------------------------------------------------------------------
# 旧记录：只有旧评分版本能读，信息不足就报错
# --------------------------------------------------------------------------


def test_old_records_are_read_by_the_old_version_and_give_the_old_results(bundle3, scoring3):
    """旧样例的记录去掉数量（模拟增补之前写出的结果），旧版本按 ADR0018 推断，结果逐月不变。"""
    s, r = settle_year_of(bundle3, "u01.a31.c0")
    recorded = score_of(bundle3, scoring3, s, r)
    legacy = score_of(bundle3, scoring3, s, revise(r, outcome=_strip_quantities(r.outcome)))
    assert recorded.holding_quantity_source == "recorded"
    assert legacy.holding_quantity_source == "legacy_order_inference"
    assert [m.u for m in legacy.months] == [m.u for m in recorded.months]
    assert legacy.annual_mean == recorded.annual_mean


def test_the_new_version_refuses_records_without_quantities(bundle3s, scoring3s):
    s, r = settle_year_of(bundle3s, CLONE)
    with pytest.raises(LifeContractError, match=r"旧记录.*不推断"):
        score_of(bundle3s, scoring3s, s, revise(r, outcome=_strip_quantities(r.outcome)))


def test_partly_recorded_quantities_are_refused_by_every_version(bundle3s, scoring3s):
    s, r = settle_year_of(bundle3s, CLONE)
    partial = revise(r, outcome=_strip_quantities(r.outcome, keep=2))
    legacy_spec = revise(scoring3s.spec, scoring_version="e1-3-explore-v1")
    for spec in (scoring3s.spec, legacy_spec):
        with pytest.raises(LifeContractError, match="部分有数量、部分没有"):
            score_of(bundle3s, scoring3s, s, partial, spec=spec)


def test_the_old_inference_never_defaults_to_one(bundle3s, scoring3s):
    """旧推断对不上（年初没有、本年也没买）就报错，不把数量当成 1。"""
    (_, _), (s2, r2) = _years(bundle3s, (_cabinets(1, 1),), ())
    assert any(h.opportunity_id == CABINET for h in r2.outcome.holdings_service)
    legacy_spec = revise(scoring3s.spec, scoring_version="e1-3-explore-v1")
    no_holdings = revise(s2, holdings=())
    with pytest.raises(LifeContractError, match="对不上"):
        score_of(
            bundle3s,
            scoring3s,
            no_holdings,
            revise(r2, outcome=_strip_quantities(r2.outcome)),
            spec=legacy_spec,
        )


# --------------------------------------------------------------------------
# 来源类型：直接观测、可审计派生、统计估计、合成
# --------------------------------------------------------------------------

_SRC = dict(observed_on=dt.date(2026, 10, 7), geography_scope="synthetic city", mapping_version="t")


def test_derived_and_estimated_values_must_name_their_inputs_and_rule():
    ok = SourceRecord(
        kind="derived", inputs=("rent table v1",), rule="monthly = annual / 12", **_SRC
    )
    assert ok.inputs == ("rent table v1",)
    SourceRecord(kind="estimated", inputs=("survey wave 3",), rule="hedonic regression", **_SRC)
    for kind in ("derived", "estimated"):
        with pytest.raises(ValueError, match="inputs 与规则 rule"):
            SourceRecord(kind=kind, rule="x", **_SRC)
        with pytest.raises(ValueError, match="inputs 与规则 rule"):
            SourceRecord(kind=kind, inputs=("a",), **_SRC)
        with pytest.raises(ValueError, match="不是合成设定"):
            SourceRecord(kind=kind, inputs=("a",), rule="r", synthetic_note="hand made", **_SRC)


def test_a_url_or_a_rule_does_not_make_a_value_observed():
    url = Provenance(
        source_url="https://example.org/table",
        source_table="t1",
        source_cell="B7",
        retrieved_date=dt.date(2026, 10, 7),
    )
    SourceRecord(kind="observed", provenance=url, **_SRC)
    with pytest.raises(ValueError, match="必须带 provenance"):
        SourceRecord(kind="observed", **_SRC)
    # 有网址、但经过换算：那是派生，不是观测
    with pytest.raises(ValueError, match="直接观测不经过换算"):
        SourceRecord(kind="observed", provenance=url, inputs=("t1",), rule="x / 12", **_SRC)
    # 合成值不能挂观测溯源冒充真实值，也必须说明是怎么设定的
    with pytest.raises(ValueError, match="不应带观测溯源"):
        SourceRecord(kind="synthetic", provenance=url, synthetic_note="hand made", **_SRC)
    with pytest.raises(ValueError, match="合成数据必须写"):
        SourceRecord(kind="synthetic", **_SRC)
