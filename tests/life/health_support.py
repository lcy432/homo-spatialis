"""E1-5C 测试的辅助函数：换事件来源（关闭 / 指定 / 随机）、按样例计划逐年交接、取健康记录。

不是通用 runner：只按样例里写好的年度计划循环调用 ``econ.life_year.run_life_year``（结算 → 评分 → 反馈）。
金额比较一律换成整数分再比，不用容差。
"""

from __future__ import annotations

from typing import Any

from common.life_contracts import (
    IllnessEventSource,
    LifeProtocol,
    PersonYearState,
    YearAction,
)
from common.life_feedback_contracts import LifeYearResult
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import LifeBundle

from .settle_support import catalog_of, revise, state_of, tax_of

A91 = "US:SYNTH2026:A91"
B92 = "US:SYNTH2026:B92"
HOME = {"opportunity_id": "a91.housing.far", "quantity": 1, "start_month": 1, "end_month": 12}
FOOD = {"opportunity_id": "a91.food.basic", "quantity": 1, "start_month": 1, "end_month": 12}
SHOP = {"opportunity_id": "a91.job.shop_parttime", "quantity": 1, "start_month": 1, "end_month": 12}
CAFE = {"opportunity_id": "a91.job.cafe_weekend", "quantity": 1, "start_month": 1, "end_month": 12}
OFFICE = {
    "opportunity_id": "a91.job.accountant_office",
    "quantity": 1,
    "start_month": 1,
    "end_month": 12,
}
GYM = {"opportunity_id": "a91.service.gym", "quantity": 1, "start_month": 1, "end_month": 12}
CLEANING = {
    "opportunity_id": "a91.service.cleaning",
    "quantity": 1,
    "start_month": 1,
    "end_month": 12,
}
COURSE_ID = "a91.training.accounting_course"
DRYER_ID = "a91.durable.washer_dryer"
CLINIC = "a91.care.clinic"
HOSPITAL = "a91.care.hospital"
TREAT = {"minor": "treat_if_possible", "major": "treat_if_possible"}
DECLINE = {"minor": "decline", "major": "decline"}


def source(mode: str, **kw: Any) -> IllnessEventSource:
    data: dict[str, Any] = {
        "mode": mode,
        "stream_version": "e1-5c-illness-stream-v1",
        "note": "测试用事件来源",
    }
    if mode == "random":
        data.update(pairing_id=kw.pop("pairing_id", "pair.test"), seed=kw.pop("seed", 20261011))
    data.update(kw)
    return IllnessEventSource.model_validate(data)


def injected(*events: tuple[str, int, int, str], repeat: int = 0) -> IllnessEventSource:
    """指定事件：(人, 年, 月, 病情)。"""
    return source(
        "injected",
        injected=[
            {
                "person_id": p,
                "repeat_index": repeat,
                "year_index": y,
                "month": m,
                "severity": s,
                "note": "测试指定事件",
            }
            for p, y, m, s in events
        ],
    )


def with_source(protocol: LifeProtocol, src: IllnessEventSource) -> LifeProtocol:
    return revise(protocol, illness_events=src)


def plan(
    bundle: LifeBundle,
    clone: str,
    year: int,
    *items: dict[str, Any],
    emergency: dict[str, Any] | None = None,
    **changes: Any,
) -> YearAction:
    """同一个分身同一年的一份计划（应急安排默认条件允许就治、不列暂停顺序）。"""
    s = state_of(bundle, clone)
    data: dict[str, Any] = {
        "person_id": s.person_id,
        "clone_id": clone,
        "year_index": year,
        "items": list(items),
        "emergency": emergency if emergency is not None else dict(TREAT),
    }
    data.update(changes)
    return YearAction.model_validate(data)


def inputs(bundle: LifeBundle, config: Any, state: PersonYearState, profile: Any = None):
    profile = profile or next(p for p in bundle.stated_profiles if p.person_id == state.person_id)
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
    action: YearAction,
    *,
    protocol: LifeProtocol | None = None,
    profile: Any = None,
    tax: Any = None,
) -> LifeYearResult:
    catalog = catalog_of(bundle, state)
    return run_life_year(
        protocol=protocol or bundle.protocol,
        state=state,
        action=action,
        catalog=catalog,
        tax=tax or tax_of(bundle, catalog),
        scoring=inputs(bundle, config, state, profile),
    )


def run_clone(
    bundle: LifeBundle, config: Any, clone: str, *, protocol: LifeProtocol | None = None
) -> list[LifeYearResult]:
    """按样例预先声明的年度计划逐年交接，直到终态、出局、停止、结算失败或反馈信息不足。"""
    state = state_of(bundle, clone)
    out: list[LifeYearResult] = []
    for p in bundle.plans_for(clone):
        r = year(bundle, config, state, p, protocol=protocol)
        out.append(r)
        if r.handoff != "next_year_ready":
            break
        assert r.next_state is not None
        state = r.next_state
    return out


def cash_ends(r: LifeYearResult) -> list[int]:
    out = r.settlement.outcome
    assert out is not None
    return [round(m.cash_end.amount * 100) for m in out.months]


def health_months(r: LifeYearResult) -> list[tuple[int, str, str, str, str | None]]:
    """（月、月初状态、记录、月末转移、治疗结果）。"""
    out = r.settlement.outcome
    assert out is not None and out.health is not None
    return [
        (
            x.month,
            x.state,
            x.record,
            x.transition,
            None if x.treatment is None else x.treatment.status,
        )
        for x in out.health.months
    ]


def flows_for(r: LifeYearResult, oid: str) -> list[tuple[int, int]]:
    out = r.settlement.outcome
    assert out is not None
    return [
        (f.month, round(f.amount.amount * 100)) for f in out.cash_flows if f.opportunity_id == oid
    ]
