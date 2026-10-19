"""单个对象自己能检查的规则（`DECISIONS/0016`）。

每条测试盯一个会造成实际损害的错误：未知价格被补成零、确定缺货被删掉、空间平减
口径装当地标价、价格指数冒充报价、协议在政策未定时自称可评分、代理自己填工资。
需要完整对象时从产品协议样例取一份真实的，只改坏一处。
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from common.life_contracts import (
    REQUIRED_MATH_POLICIES,
    RESERVED_OUTCOME_KEYS,
    ActionItem,
    DurationSpec,
    EnvironmentOutcome,
    InternalYearScore,
    LifeOpportunity,
    LifeProtocol,
    MoneyBasis,
    PersonYearState,
    PriceIndex,
    PriceQuote,
    YearAction,
)
from common.money import USD_2024_US100, Money, NumeraireMismatchError

from .conftest import CLONE_K, HEALTHY

USD = "USD_2024_LOCAL"
SYNTH = {
    "kind": "synthetic",
    "observed_on": "2026-10-05",
    "geography_scope": "synthetic",
    "mapping_version": "t",
    "synthetic_note": "test",
}
RESOURCE = {
    "basis": "per_service_month",
    "hours": 30,
    "burden_per_hour": 0.5,
    "quantity_scales": False,
    "note": "测试：每月准备 30 小时",
}


def _opp(**over) -> dict:
    base = {
        "opportunity_id": "x.item",
        "category": "food",
        "label": "item",
        "unit": "plan",
        "quality": "q",
        "availability": "available",
        "price": {
            "amount": {"amount": 10.0, "numeraire": USD},
            "billing": "per_month",
            "per_unit": "u",
        },
        "duration": {"kind": "ongoing_service"},
        "resource": dict(RESOURCE),
        "source": SYNTH,
    }
    base.update(over)
    return base


def _policies(state="undecided") -> dict:
    return {
        k: {"state": state, **({"decision_ref": "DECISIONS/9999"} if state == "decided" else {})}
        for k in REQUIRED_MATH_POLICIES
    }


@pytest.fixture
def protocol_doc(bundle):
    """产品协议样例的协议，数学政策全部改回"未定"。"""

    def make(**over) -> dict:
        base = bundle.protocol.model_dump(mode="json")
        base.update(status="synthetic_fixture_only", math_policies=_policies())
        base.update(over)
        return base

    return make


# --------------------------------------------------------------------------
# 可得性三态
# --------------------------------------------------------------------------


def test_unknown_price_is_not_filled_with_zero():
    """**未知不是零价。** 补成零会让没数据的城市看起来最便宜。"""
    o = LifeOpportunity.model_validate(_opp(availability="unknown", price=None))
    assert o.availability == "unknown" and o.price is None
    with pytest.raises(ValidationError, match="未知不是零价"):
        LifeOpportunity.model_validate(
            _opp(
                availability="unknown",
                price={
                    "amount": {"amount": 0.0, "numeraire": USD},
                    "billing": "per_month",
                    "per_unit": "u",
                },
            )
        )


def test_unavailable_is_kept_and_carries_no_price():
    """确定缺货要保留成一条记录——删掉就抹掉了城市的限制。"""
    o = LifeOpportunity.model_validate(_opp(availability="unavailable", price=None))
    assert o.availability == "unavailable"


def test_available_needs_a_price_and_zero_is_legal_for_free_items():
    free = LifeOpportunity.model_validate(
        _opp(
            price={
                "amount": {"amount": 0.0, "numeraire": USD},
                "billing": "per_year",
                "per_unit": "pass",
            }
        )
    )
    assert free.price.amount.amount == 0.0
    with pytest.raises(ValidationError, match="恰好有 price 或 compensation"):
        LifeOpportunity.model_validate(_opp(price=None))


def test_the_three_states_are_distinct_values():
    states = {
        LifeOpportunity.model_validate(
            _opp(availability=a, price=None if a != "available" else _opp()["price"])
        ).availability
        for a in ("available", "unavailable", "unknown")
    }
    assert states == {"available", "unavailable", "unknown"}


# --------------------------------------------------------------------------
# 数值与未知字段
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        (
            "price",
            {"amount": {"amount": -5.0, "numeraire": USD}, "billing": "per_month", "per_unit": "u"},
            "不能为负",
        ),
        (
            "price",
            {
                "amount": {"amount": float("nan"), "numeraire": USD},
                "billing": "per_month",
                "per_unit": "u",
            },
            "finite",
        ),
        (
            "price",
            {
                "amount": {"amount": float("inf"), "numeraire": USD},
                "billing": "per_month",
                "per_unit": "u",
            },
            "finite",
        ),
        ("resource", {**RESOURCE, "hours": 9000}, "less than or equal"),
        ("resource", {**RESOURCE, "burden_per_hour": -1.0}, "greater than or equal"),
        ("resource", {**RESOURCE, "burden_per_hour": None}, "零也要显式写 0"),
    ],
)
def test_illegal_numbers_are_rejected(field, value, match):
    with pytest.raises(ValidationError, match=match):
        LifeOpportunity.model_validate(_opp(**{field: value}))


def test_unknown_fields_are_rejected_not_ignored():
    with pytest.raises(ValidationError, match="Extra inputs"):
        LifeOpportunity.model_validate(_opp(brand_premium=1.2))
    with pytest.raises(ValidationError, match=r"unexpected_keyword_argument|Unexpected"):
        LifeOpportunity.model_validate(
            _opp(
                price={
                    "amount": {"amount": 1.0, "numeraire": USD, "fx": 7.1},
                    "billing": "per_month",
                    "per_unit": "u",
                }
            )
        )


# --------------------------------------------------------------------------
# 期限：一次体验 vs 持续物品
# --------------------------------------------------------------------------


def test_one_time_experience_and_durable_good_have_different_periods():
    once = DurationSpec(kind="one_time_experience", lasts_years=0)
    durable = DurationSpec(kind="durable_good", lasts_years=8)
    assert once.lasts_years == 0 and durable.lasts_years == 8


@pytest.mark.parametrize(
    "spec",
    [
        {"kind": "one_time_experience", "lasts_years": 2},  # 一次体验不能跨年
        {"kind": "durable_good", "lasts_years": 0},  # 持续物品至少一年
        {"kind": "durable_good", "lasts_years": 96},  # 月写成年
        {"kind": "durable_good"},  # 没写期限
        # 超出防呆上限。"不超过协议的 N"依赖协议，E1-2 起在加载器里查（test_catalog 里有对应测试）
        {"kind": "training_course", "completion_years": 100},
        {"kind": "ongoing_service", "lasts_years": 3},  # 持续服务付费期间有效
    ],
)
def test_unreasonable_periods_are_rejected(spec):
    with pytest.raises(ValidationError):
        DurationSpec.model_validate(spec)


def test_ongoing_service_must_bill_periodically():
    with pytest.raises(ValidationError, match="周期性"):
        LifeOpportunity.model_validate(
            _opp(
                price={
                    "amount": {"amount": 1.0, "numeraire": USD},
                    "billing": "one_time",
                    "per_unit": "u",
                }
            )
        )


# --------------------------------------------------------------------------
# 计价：口径、指数、账目
# --------------------------------------------------------------------------


def test_the_spatially_deflated_numeraire_cannot_hold_a_local_quote():
    """`USD_2024_US100` 是 RPP 平减口径，不是当地标价。"""
    with pytest.raises(ValidationError, match="空间平减"):
        PriceQuote.model_validate(
            {
                "amount": {"amount": 900.0, "numeraire": USD_2024_US100},
                "billing": "per_month",
                "per_unit": "u",
            }
        )


def test_a_quote_needs_a_full_local_basis():
    with pytest.raises(ValidationError, match="本地标价口径"):
        PriceQuote.model_validate(
            {
                "amount": {"amount": 900.0, "numeraire": "USD"},
                "billing": "per_month",
                "per_unit": "u",
            }
        )


def test_a_price_index_cannot_stand_in_for_a_quote():
    """指数是无量纲的相对水平，没有金额。塞进 price 字段必须被拒。"""
    idx = PriceIndex(index_id="g", scope="goods", value=1.04, reference_label="avg=1")
    assert not hasattr(idx, "amount")
    with pytest.raises(ValidationError):
        LifeOpportunity.model_validate(_opp(price=idx.model_dump()))
    # 旧 Money 的加法不做类型检查，所以这里抛的是 AttributeError；要点是它**响亮地失败**，
    # 而不是悄悄把一个无量纲的数当成钱加进去。
    with pytest.raises((TypeError, AttributeError)):
        _ = Money(1.0, USD) + idx  # type: ignore[operator]


def test_different_bases_cannot_enter_one_ledger(first_year):
    zz = "XTS_2024_LOCAL"
    with pytest.raises(NumeraireMismatchError):
        _ = Money(1.0, USD) + Money(1.0, zz)
    doc = first_year.settlement.outcome.model_dump(mode="json")
    assert EnvironmentOutcome.model_validate(doc) == first_year.settlement.outcome
    doc["cash_flows"][0]["amount"]["numeraire"] = zz
    with pytest.raises(ValidationError, match="不一致"):
        EnvironmentOutcome.model_validate(doc)


def test_money_basis_carries_currency_year_and_meaning():
    b = MoneyBasis(currency="XTS", base_year=2024)
    assert b.numeraire_id == "XTS_2024_LOCAL"
    assert b.price_meaning == "local_price_base_year"


def test_time_and_energy_are_different_quantities(bundle):
    state = bundle.initial_state(CLONE_K)
    assert state.time_budget_hours != state.energy.level
    assert type(state.energy).__name__ == "EnergyState"
    doc = state.model_dump(mode="json")
    with pytest.raises(ValidationError):
        PersonYearState.model_validate({**doc, "time_budget_hours": 9000})
    with pytest.raises(ValidationError):
        PersonYearState.model_validate({**doc, "energy": {**doc["energy"], "level": 0}})


# --------------------------------------------------------------------------
# 行动：代理不能自己填结果；理由不参与结算
# --------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(RESERVED_OUTCOME_KEYS))
def test_an_agent_cannot_write_its_own_outcome(key):
    with pytest.raises(ValidationError, match="结果字段"):
        ActionItem(opportunity_id="x.job", quantity=1, arrangement={key: 1})


def test_an_agent_cannot_add_outcome_fields_at_the_top_level():
    with pytest.raises(ValidationError, match="Extra inputs"):
        YearAction.model_validate(
            {"person_id": "u", "clone_id": "c", "year_index": 1, "items": [], "wage": 90000}
        )


def test_rationale_does_not_reach_settlement():
    items = [{"opportunity_id": "x.job", "quantity": 1}]
    a = YearAction.model_validate(
        {
            "person_id": "u",
            "clone_id": "c",
            "year_index": 1,
            "items": items,
            "emergency": HEALTHY,
            "rationale": "我会非常努力地工作",
        }
    )
    b = YearAction.model_validate(
        {
            "person_id": "u",
            "clone_id": "c",
            "year_index": 1,
            "items": items,
            "emergency": HEALTHY,
            "rationale": "随便干干",
        }
    )
    assert a.settlement_view() == b.settlement_view()
    assert "rationale" not in a.settlement_view()
    assert a != b, "理由确实不同；只是不进结算"
    assert a.settlement_view()["emergency"] == HEALTHY  # 应急安排进结算


def test_the_emergency_plan_is_required():
    """生病时怎么办必须写（E3A 起用户都会回答健康问题；mock 也有固定的应急政策），不默认成"不治"。"""
    with pytest.raises(ValidationError, match="emergency"):
        YearAction.model_validate({"person_id": "u", "clone_id": "c", "year_index": 1, "items": []})


# --------------------------------------------------------------------------
# 协议：政策未定就不能自称可评分
# --------------------------------------------------------------------------


def test_a_protocol_cannot_be_formal_while_policies_are_undecided(protocol_doc):
    with pytest.raises(ValidationError, match="不能标成 formal"):
        LifeProtocol.model_validate(protocol_doc(status="formal"))


def test_scoring_ready_is_false_until_every_policy_is_decided(protocol_doc):
    assert LifeProtocol.model_validate(protocol_doc()).scoring_ready is False
    half = _policies()
    half["score_mapping"] = {"state": "decided", "decision_ref": "DECISIONS/9999"}
    assert LifeProtocol.model_validate(protocol_doc(math_policies=half)).scoring_ready is False
    full = LifeProtocol.model_validate(
        protocol_doc(status="formal", math_policies=_policies("decided"))
    )
    assert full.scoring_ready is True, "反面：全定且 formal 时必须能变成可评分，否则上面测的是常数"


def test_a_decided_policy_must_point_at_a_decision(protocol_doc):
    p = _policies()
    p["discounting"] = {"state": "decided"}
    with pytest.raises(ValidationError, match="decision_ref"):
        LifeProtocol.model_validate(protocol_doc(math_policies=p))


def test_every_open_policy_must_be_listed(protocol_doc):
    p = _policies()
    del p["terminal_value"]
    with pytest.raises(ValidationError, match="terminal_value"):
        LifeProtocol.model_validate(protocol_doc(math_policies=p))


@pytest.mark.parametrize(
    "rule", ["resource_rules", "energy_feedback", "career_rules", "savings_rules", "health_rules"]
)
def test_every_life_rule_is_required(protocol_doc, rule):
    """现行生活规则只有一套（月度资源、精力反馈、职业、储蓄、健康）；少了哪一块都不是"旧规则"，而是不完整。"""
    doc = protocol_doc()
    del doc[rule]
    with pytest.raises(ValidationError, match=rule):
        LifeProtocol.model_validate(doc)


def test_an_official_internal_score_is_not_representable_yet():
    """评分映射未定，内部分数只能是测试夹具。"""
    with pytest.raises(ValidationError):
        InternalYearScore.model_validate(
            {
                "person_id": "u",
                "clone_id": "c",
                "year_index": 1,
                "protocol_id": "p",
                "scoring_scale_version": "v",
                "status": "official",
                "values": {},
            }
        )


def test_the_new_score_is_not_the_old_welfare_type():
    from common.money import WelfareLevel
    from econ.types import GroundTruth

    assert not issubclass(InternalYearScore, (WelfareLevel, GroundTruth))


# --------------------------------------------------------------------------
# 分层：契约模块不把 M3 带进来
# --------------------------------------------------------------------------


def test_importing_the_contracts_does_not_drag_in_the_world_generator():
    """生活契约不该把研究线的 M3 生成器拖进来。"""
    code = (
        "import sys; import common.life_contracts; "
        "bad=[m for m in ('worlds.generator','worlds.personas','eval','econ.core') if m in sys.modules]; "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, "PYTHONPATH": "src"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
