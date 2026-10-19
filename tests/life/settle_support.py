"""E1-2 结算测试的辅助函数。

金额比较一律换成整数最小单位（分）再比，**不用容差**：结算内部就是整数记账，
测试也按同样的口径核对。
"""

from __future__ import annotations

from typing import Any

from common.life_contracts import (
    CityLifeCatalog,
    LifeProtocol,
    PersonYearState,
    SettlementResult,
    YearAction,
    exact_minor_units,
)
from common.money import Money
from econ.life_settlement import LifeTaxBinding, binding_from_policy, settle_year
from ledger.life_catalog import LifeBundle, parse_bundle


def cents(m: Money | float) -> int:
    """金额 → 整数分。精度细于分就是测试本身写错了，直接报错。"""
    amount = m.amount if isinstance(m, Money) else m
    out = exact_minor_units(amount, 2)
    assert out is not None, f"{amount!r} 不是整数分"
    return out


def state_of(bundle: LifeBundle, clone_id: str) -> PersonYearState:
    s = bundle.initial_state(clone_id)
    assert s is not None, clone_id
    return s


def plan_of(bundle: LifeBundle, clone_id: str, year: int = 1) -> YearAction:
    return next(p for p in bundle.plans if p.clone_id == clone_id and p.year_index == year)


def catalog_of(bundle: LifeBundle, state: PersonYearState) -> CityLifeCatalog:
    c = bundle.catalog_for(str(state.city))
    assert c is not None
    return c


def tax_of(bundle: LifeBundle, catalog: CityLifeCatalog) -> LifeTaxBinding:
    policy = bundle.tax_policy(catalog.tax_policy_id)
    assert policy is not None
    return binding_from_policy(policy)


def with_items(plan: YearAction, *items: dict[str, Any], **changes: Any) -> YearAction:
    """同一份计划换一组行动（以及别的字段）。"""
    data = plan.model_dump(mode="json")
    data["items"] = list(items)
    data.update(changes)
    return YearAction.model_validate(data)


def plus_items(plan: YearAction, *items: dict[str, Any]) -> YearAction:
    """在计划末尾追加行动（同月竞争时排在原有行动之后）。"""
    data = plan.model_dump(mode="json")
    return with_items(plan, *data["items"], *items)


def revise(model: Any, **changes: Any) -> Any:
    """改几个字段后重新走一遍校验（不用 model_copy，后者不校验）。"""
    data = model.model_dump(mode="python")
    data.update(changes)
    return type(model).model_validate(data)


def protocol_with_years(protocol: LifeProtocol, n: int) -> LifeProtocol:
    return revise(protocol, simulation_years=n)


def settle(
    bundle: LifeBundle,
    clone_id: str,
    *,
    year: int = 1,
    plan: YearAction | None = None,
    state: PersonYearState | None = None,
    catalog: CityLifeCatalog | None = None,
    protocol: LifeProtocol | None = None,
    tax: LifeTaxBinding | None = None,
) -> SettlementResult:
    state = state or state_of(bundle, clone_id)
    catalog = catalog or catalog_of(bundle, state)
    return settle_year(
        protocol=protocol or bundle.protocol,
        state=state,
        action=plan or plan_of(bundle, clone_id, year),
        catalog=catalog,
        tax=tax or tax_of(bundle, catalog),
    )


def bundle_from(raw: dict) -> LifeBundle:
    return parse_bundle(raw)


def month_ends(result: SettlementResult) -> list[int]:
    assert result.outcome is not None
    return [cents(m.cash_end) for m in result.outcome.months]


def item(result: SettlementResult, opportunity_id: str, *, fallback: bool = False):
    assert result.outcome is not None
    want = "fallback_minimum"
    return next(
        i
        for i in result.outcome.items
        if i.opportunity_id == opportunity_id and ((i.status == want) == fallback)
    )


def flows(result: SettlementResult, *, kind: str | None = None, oid: str | None = None):
    assert result.outcome is not None
    return [
        f
        for f in result.outcome.cash_flows
        if (kind is None or f.kind == kind) and (oid is None or f.opportunity_id == oid)
    ]
