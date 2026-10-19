"""E1-2 的边界（`DECISIONS/0017`）：合成税、第二币种、信息隔离、精度、加载器与协议状态。

对应验收 13（第二币种走同一入口、混合口径被拒、没有国家分支）与 14（新反馈通过隔离
扫描、没有满足感/分数/排序），以及延后的税表核查之前的那几条底线：合成税必须显式
声明、没有配置不等于零税、现有税表不能接到当地标价上、月度预扣与年度税额的关系。
"""

from __future__ import annotations

import inspect
import json
from collections import defaultdict
from pathlib import Path

import pytest
from pydantic import ValidationError

import econ.life_settlement as life_settlement
from agentlayer.life_views import (
    ObservationPermission,
    assert_payload_clean,
    build_observation,
    observation_payload,
    visible_feedback,
)
from common.ids import Jurisdiction
from common.life_billing import cumulative_share, monthly_installment
from common.life_contracts import (
    REQUIRED_MATH_POLICIES,
    EnvironmentOutcome,
    ExecutedItem,
    ItemExecution,
    LifeContractError,
    MonthRecord,
    PolicyDecision,
    SettlementResult,
    VisibleFeedback,
)
from common.money import Money, NumeraireMismatchError
from econ.life_settlement import (
    LifeTaxBinding,
    SyntheticBracketTax,
    binding_from_policy,
    settle_year,
)
from ledger.life_catalog import cross_validate, load_life_bundle

from .conftest import opportunity
from .settle_support import (
    bundle_from,
    catalog_of,
    cents,
    flows,
    plan_of,
    plus_items,
    revise,
    settle,
    state_of,
    with_items,
)

USD = "USD_2024_LOCAL"
ZA = Jurisdiction(("US", "ZA"))
PRODUCT = dict(
    condition="product_stated_profile",
    show_stated_dimension_points=True,
    show_soft_preferences=True,
    show_hard_requirements=True,
    show_free_text=True,
    show_energy_level=False,
    show_stated_material_points=True,
    permission_version="e1-2-draft",
)


# --------------------------------------------------------------------------
# 合成税：显式声明、年内累计、跨工作合并
# --------------------------------------------------------------------------


def test_withholding_on_a_salary_that_does_not_divide_by_twelve(bundle2):
    r = settle(bundle2, "u01.b12.c0")
    gross = [cents(m.income_gross) for m in r.outcome.months]
    assert gross == [333333, 333334, 333333] * 4 and sum(gross) == 40_000_00
    tax = [cents(m.income_tax) for m in r.outcome.months]
    assert tax == [0, 0, 0, 26667, 66666, 66667, 66667, 66666, 66667, 66667, 66666, 66667]
    annual = SyntheticBracketTax(bundle2.tax_policy("tax.synth.zb")).tax(
        Money(40_000, USD), Jurisdiction(("US", "ZB")), 2024
    )
    assert sum(tax) == cents(annual) == 5_600_00, "各月预扣之和恰好是全年应纳税额"


def test_two_jobs_share_one_tax_year(bundle2):
    plan = plus_items(
        plan_of(bundle2, "u01.a11.c0"),
        {"opportunity_id": "a11.job.parttime", "quantity": 1, "start_month": 1, "end_month": 12},
    )
    r = settle(bundle2, "u01.a11.c0", plan=plan)
    total = sum(-cents(f.amount) for f in flows(r, kind="income_tax"))
    assert total == 7_200_00  # 20% × (48,000 − 12,000)
    assert total != 4_800_00 + 0, "两份工作各当一个纳税年度会得到 4,800 + 0"


def test_a_job_starting_mid_year_is_taxed_on_the_calendar_year_to_date(bundle2):
    s = revise(state_of(bundle2, "u01.a11.c0"), cash={"amount": 10_000, "numeraire": USD})
    plan = with_items(
        plan_of(bundle2, "u01.a11.c0"),
        {"opportunity_id": "a11.housing.studio", "quantity": 1, "start_month": 1, "end_month": 12},
        {"opportunity_id": "a11.food.basic", "quantity": 1, "start_month": 1, "end_month": 12},
        {"opportunity_id": "a11.job.office", "quantity": 1, "start_month": 7, "end_month": 12},
    )
    r = settle(bundle2, "u01.a11.c0", state=s, plan=plan)
    gross = [cents(m.income_gross) for m in r.outcome.months]
    assert gross == [0] * 6 + [3_000_00] * 6  # 日历第 7–12 月的份额，不是"自己的第 1–6 月"
    tax = [cents(m.income_tax) for m in r.outcome.months]
    assert tax == [0] * 10 + [600_00, 600_00]  # 18,000 中超过 12,000 的部分 × 20%


def test_there_is_no_silent_zero_tax(bundle2):
    s = state_of(bundle2, "u01.a11.c0")
    kwargs = dict(
        protocol=bundle2.protocol,
        state=s,
        action=plan_of(bundle2, "u01.a11.c0"),
        catalog=catalog_of(bundle2, s),
    )
    with pytest.raises(TypeError):
        settle_year(**kwargs)  # 税没有默认值
    r = settle_year(**kwargs, tax=None)  # type: ignore[arg-type]
    assert r.status == "data_error" and r.problems[0].code == "tax_binding"


def test_only_a_synthetic_test_double_is_accepted_for_now(bundle2):
    policy = bundle2.tax_policy("tax.synth.za")
    with pytest.raises(LifeContractError, match="synthetic_test_double"):
        LifeTaxBinding(
            function=SyntheticBracketTax(policy),
            policy_id=policy.policy_id,
            status="verified",
            money_basis_id=USD,
            jurisdiction=ZA,
        )


def test_the_unverified_world_tax_table_cannot_be_wired_to_local_prices():
    from worlds.taxes import StateRegime, SyntheticProgressiveTax

    with pytest.raises(LifeContractError, match="尚未核查"):
        LifeTaxBinding(
            function=SyntheticProgressiveTax(regimes={"ZA": StateRegime.FLAT}),
            policy_id="tax.synth.za",
            status="synthetic_test_double",
            money_basis_id=USD,
            jurisdiction=ZA,
        )


def test_the_synthetic_tax_refuses_another_basis_or_jurisdiction(bundle2):
    tax = SyntheticBracketTax(bundle2.tax_policy("tax.synth.za"))
    with pytest.raises(NumeraireMismatchError):
        tax.tax(Money(20_000, "USD_2024_US100"), ZA, 2024)
    with pytest.raises(ValueError, match="适用于"):
        tax.tax(Money(20_000, USD), Jurisdiction(("US", "ZB")), 2024)


class _EchoOtherBasis:
    def tax(self, gross, jurisdiction, year):
        return Money(0.0, "USD_2024_US100")


class _Negative:
    def tax(self, gross, jurisdiction, year):
        return Money(-1.0, gross.numeraire)


@pytest.mark.parametrize("function", [_EchoOtherBasis(), _Negative()])
def test_a_tax_function_with_an_impossible_output_is_a_data_error(bundle2, function):
    binding = LifeTaxBinding(
        function=function,
        policy_id="tax.synth.za",
        status="synthetic_test_double",
        money_basis_id=USD,
        jurisdiction=ZA,
    )
    r = settle(bundle2, "u01.a11.c0", tax=binding)
    assert r.status == "data_error" and r.problems[0].code == "tax_output"
    assert r.outcome is None


def test_outputs_are_labelled_as_synthetic_bookkeeping(bundle2):
    out = settle(bundle2, "u01.a11.c0").outcome
    assert out.bookkeeping_label == "synthetic_bookkeeping_only"
    assert out.tax_status == "synthetic_test_double" and out.tax_policy_id == "tax.synth.za"
    assert any("不是真实税后生活成本" in n for n in out.notes)
    assert any("账务可行不等于完整生活可行" in n for n in out.notes)


# --------------------------------------------------------------------------
# 13. 第二币种：同一结算入口；混合口径被拒；没有国家分支
# --------------------------------------------------------------------------


def test_the_second_currency_goes_through_the_same_entry(bundle2):
    r = settle(bundle2, "u01.zz.c0")
    assert r.status == "completed"
    assert r.outcome.money_basis_id == "XTS_2024_LOCAL"
    assert {f.amount.numeraire for f in r.outcome.cash_flows} == {"XTS_2024_LOCAL"}
    assert cents(r.outcome.cash_end) == 117_500_00
    assert sum(-cents(f.amount) for f in flows(r, kind="income_tax")) == 40_000_00


def test_a_dollar_account_cannot_be_settled_in_an_xts_city(bundle2):
    moved = revise(
        state_of(bundle2, "u01.a11.c0"), city={"country": "ZZ", "scheme": "SYNTHGEO", "code": "C21"}
    )
    zz = bundle2.catalog_for("ZZ:SYNTHGEO:C21")
    r = settle(
        bundle2,
        "u01.a11.c0",
        state=moved,
        plan=revise(plan_of(bundle2, "u01.zz.c0"), clone_id="u01.a11.c0"),
        catalog=zz,
        tax=binding_from_policy(bundle2.tax_policy("tax.synth.zz")),
    )
    assert r.status == "input_error" and "basis_mismatch" in {p.code for p in r.problems}


def test_another_countrys_tax_binding_is_refused_without_conversion(bundle2):
    r = settle(bundle2, "u01.a11.c0", tax=binding_from_policy(bundle2.tax_policy("tax.synth.zz")))
    assert r.status == "data_error"
    assert {p.code for p in r.problems} == {
        "tax_policy_mismatch",
        "tax_basis_mismatch",
        "tax_jurisdiction_mismatch",
    }


def test_settlement_has_no_country_or_currency_branch():
    src = inspect.getsource(life_settlement)
    for literal in ('"US"', "'US'", '"ZZ"', "'ZZ'", '"USD"', '"XTS"'):
        assert literal not in src, f"结算里出现了国家/币种字面量 {literal}"


# --------------------------------------------------------------------------
# 14. 信息隔离：新反馈通过扫描；没有满足感、分数或排序
# --------------------------------------------------------------------------


def test_visible_feedback_passes_the_scan_and_carries_only_facts(bundle2):
    fb = visible_feedback(settle(bundle2, "u01.a11.c0"))
    assert type(fb) is VisibleFeedback
    assert_payload_clean(fb)
    blob = json.dumps(fb.model_dump(mode="json"), ensure_ascii=False)
    for word in ("score", "satisfaction", "utility", "welfare", "rank", "dimension_weights"):
        assert word not in blob
    assert cents(fb.cash_balance) == 11_270_00
    assert cents(fb.income_gross) == 36_000_00 and cents(fb.income_tax) == 4_800_00
    assert cents(fb.spending) == 15_600_00 + 3_000_00 + 150_00 + 480_00 + 1_200_00
    assert fb.life_status == "active"


def test_feedback_and_the_next_state_reach_the_next_observation_through_the_whitelist(bundle2):
    r = settle(bundle2, "u01.a11.c0")
    profile = next(p for p in bundle2.stated_profiles if p.person_id == "u01")
    obs = build_observation(
        permission=ObservationPermission(**PRODUCT),
        profile=profile,
        state=r.next_state,
        catalog=catalog_of(bundle2, r.next_state),
        last_feedback=visible_feedback(r),
    )
    payload = observation_payload(obs)
    assert payload["year_index"] == 2
    held = {h["opportunity_id"]: h for h in payload["holdings"]}
    assert held["a11.durable.computer"]["remaining_months"] == 37
    assert payload["training"] == [
        {
            "opportunity_id": "a11.training.bookkeeping",
            "months_participated": 6,
            "months_required": 12,
            "completed": False,
        }
    ]
    assert [m["need"] for m in payload["minimum_living"]] == ["housing", "food"]
    blob = json.dumps(payload, ensure_ascii=False)
    for key in ("dimension_weights", "material_subweights", "settlement_convention", "plan_order"):
        assert key not in blob


def test_the_raw_settlement_result_cannot_be_smuggled_into_a_payload(bundle2):
    r = settle(bundle2, "u01.a11.c0")
    with pytest.raises(LifeContractError, match="SettlementResult"):
        assert_payload_clean({"opportunities": [{"spec": {"x": [r]}}]})
    with pytest.raises(LifeContractError, match="ItemExecution"):
        assert_payload_clean({"deep": [r.outcome.items[0]]})


def test_no_feedback_comes_out_of_a_result_that_committed_nothing(bundle2):
    r = settle(
        bundle2, "u02.a11.c0", plan=revise(plan_of(bundle2, "u02.a11.c0"), minimum_fallback=())
    )
    assert r.status == "plan_infeasible"
    with pytest.raises(LifeContractError, match="plan_infeasible"):
        visible_feedback(r)


def test_exit_and_terminal_feedback_state_the_facts(bundle2):
    fb = visible_feedback(settle(bundle2, "u02.b12.c0"))
    assert fb.life_status == "exited"
    assert any("出局" in n and "食品" in n and "300.00" in n for n in fb.notes)


def test_settlement_never_sees_preference_truth_or_a_scorer():
    assert set(inspect.signature(settle_year).parameters) == {
        "protocol",
        "state",
        "action",
        "catalog",
        "tax",
    }
    src = inspect.getsource(life_settlement)
    for name in ("InternalPreferenceTruth", "StatedLifeProfile", "InternalYearScore"):
        assert name not in src


def test_no_result_type_has_a_score_or_ranking_field():
    for model in (
        EnvironmentOutcome,
        SettlementResult,
        MonthRecord,
        ItemExecution,
        ExecutedItem,
        VisibleFeedback,
    ):
        for name in model.model_fields:
            assert not any(
                w in name for w in ("score", "satisfaction", "utility", "welfare", "rank", "best")
            ), (model.__name__, name)


def test_e1_1_holdings_are_shown_in_months(bundle):
    state = next(s for s in bundle.initial_states if s.clone_id == "u02.a01.c0")
    profile = next(p for p in bundle.stated_profiles if p.person_id == "u02")
    obs = build_observation(
        permission=ObservationPermission(**PRODUCT),
        profile=profile,
        state=state,
        catalog=bundle.catalog_for(str(state.city)),
    )
    assert obs.holdings[0].remaining_months == 60  # E1-1 写的是 5 年


# --------------------------------------------------------------------------
# 精度：整数最小单位、累计舍入、细于最小单位的金额是错误
# --------------------------------------------------------------------------


@pytest.mark.parametrize("annual", [0, 1, 7, 11, 13, 40_000_00, 208_000, 1_234_567])
def test_cumulative_rounding_sums_exactly_to_the_annual_amount(annual):
    parts = [monthly_installment(annual, m) for m in range(1, 13)]
    assert sum(parts) == annual
    assert max(parts) - min(parts) <= 1


def test_half_a_minor_unit_rounds_up():
    assert cumulative_share(6, 1) == 1  # 0.5 分 → 1 分
    assert [monthly_installment(6, m) for m in range(1, 13)] == [1, 0] * 6


def test_a_quote_finer_than_a_cent_is_a_data_error(raw2):
    opportunity(raw2, "a11.service.cowork")["price"]["amount"]["amount"] = 220.005
    with pytest.raises(LifeContractError, match="最小记账单位"):
        bundle_from(raw2)


def test_cash_finer_than_a_cent_is_an_input_error(bundle2):
    s = revise(state_of(bundle2, "u01.a11.c0"), cash={"amount": 500.001, "numeraire": USD})
    r = settle(bundle2, "u01.a11.c0", state=s)
    assert r.status == "input_error" and r.problems[0].code == "cash_precision"


# --------------------------------------------------------------------------
# 加载器、协议状态、开局对齐
# --------------------------------------------------------------------------


def test_the_e1_2_fixture_is_settleable_but_not_scoreable(bundle2):
    r = cross_validate(bundle2)
    assert r.contract_valid and r.settlement_ready and not r.scoring_ready
    assert r.protocol_status == "synthetic_fixture_only"
    policies = bundle2.protocol.math_policies
    timing = policies["income_payment_timing"]
    assert timing.state == "decided" and timing.decision_ref == "DECISIONS/0017"
    failure = policies["failure_handling"]
    assert failure.state == "undecided" and failure.settled_parts and failure.open_parts
    assert set(bundle2.protocol.undecided_policies) == set(REQUIRED_MATH_POLICIES) - {
        "income_payment_timing"
    }


def test_the_e1_1_fixture_still_loads_but_is_not_settleable():
    _, report = load_life_bundle(Path("configs/life/synthetic_e1_1.yaml"))
    assert report.contract_valid and not report.settlement_ready
    assert any("没有配置不等于零税" in p for p in report.settlement_problems)
    assert any("minimum_living" in p for p in report.settlement_problems)


def test_a_policy_with_open_parts_cannot_be_called_decided():
    with pytest.raises(ValidationError, match="开放部分"):
        PolicyDecision(state="decided", decision_ref="DECISIONS/0017", open_parts=("重试规则",))


def test_cross_city_starts_are_aligned_for_each_person(bundle2):
    """同一个人在两座美国城市：现金、技能、精力、时间预算、持有物都相同。"""
    by_person = defaultdict(list)
    for s in bundle2.initial_states:
        if s.money_basis_id == USD:
            by_person[s.person_id].append(s)
    assert sorted(by_person) == ["u01", "u02"]
    for pid, states in by_person.items():
        assert len(states) == 2
        keys = {
            (
                cents(s.cash),
                tuple(sorted(s.skills.items())),
                s.energy,
                s.time_budget_hours,
                s.holdings,
            )
            for s in states
        }
        assert len(keys) == 1, pid


def test_an_unsupported_eligibility_tag_is_a_data_error(raw2):
    opportunity(raw2, "a11.job.forklift")["eligibility"] = ["vibes:good"]
    r = settle(bundle_from(raw2), "u01.a11.c0")
    assert r.status == "data_error" and "词表" in r.problems[0].message
