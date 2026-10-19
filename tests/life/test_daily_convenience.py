"""E1 补充"日常生活便利"（`DECISIONS/0033`）：便利分只读资源账里实际减少的家务小时，没有第二份奖励；新旧版本隔离；mock、
规划请求与期末估值确实用了新输入。零网络、零费用。

可手算的主例（K51，全职文员，远处住房、基本食品）：洗衣基准 12 小时、打扫 10 小时，共 22；设备包残余 4 + 6 = 10，q = 12，
s = 12/20 = 0.6；人工服务残余 2 + 2 = 4，q = 18，s = 18/26；两者同时在用按每项取最小残余，q 仍是 18。
"""

from __future__ import annotations

import copy
import importlib
import json
import shutil
import sys
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from agentlayer.life_mock import (
    CONVENIENCE_MOCK_IDENTITY,
    MOCK_IDENTITY,
    ConvenienceMockPlanner,
    RuleBasedMockPlanner,
    mock_planner_for,
)
from agentlayer.life_planning import (
    action_guide,
    prompt_template_fingerprint,
)
from agentlayer.life_views import assert_payload_clean
from common.life_contracts import (
    ExactAmount,
    Holding,
    IllnessEventSource,
    InjectedIllness,
    LifeContractError,
    YearAction,
)
from common.life_planning_contracts import (
    CONVENIENCE_ACTION_GUIDE_VERSION,
    CONVENIENCE_PROMPT_TEMPLATE_VERSION,
    PlannerSpec,
)
from common.life_scoring_contracts import (
    CONVENIENCE_SCORING_VERSIONS,
    DAILY_CONVENIENCE,
    GROWTH_TRANSPORT_VERSIONS,
    HEALTH_SCORING_VERSIONS,
    LifeScoringSpec,
)
from common.money import Money
from common.run_store import RunStore
from econ.life_coverage import coverage_report
from econ.life_evaluation import evaluate_clone
from econ.life_scoring import preference_from_stated_profile, score_year
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_catalog import load_life_bundle
from ledger.life_evaluation_config import load_evaluation_config
from ledger.life_run_config import run_config_problems
from ledger.life_scoring_config import (
    convenience_preference_problems,
    convenience_protocol_problems,
    load_scoring_config,
    parse_scoring_config,
    scoring_config_problems,
)
from orchestration.life_runner import planner_payloads, preflight, start_run

REPO = Path(__file__).resolve().parents[2]
LIFE = REPO / "configs/life/synthetic_e1_convenience.yaml"
SCORING = REPO / "configs/life/scoring_e1_convenience.yaml"
EVALUATION = REPO / "configs/life/evaluation_e1_convenience.yaml"
FLASH = Path("configs/life/run_e1_convenience_flash.yaml")
MOCK = Path("configs/life/run_e1_convenience_mock.yaml")
#: 读的历史证据（只读）：产品 Flash 先导的运行清单（本轮之前的代码写的旧提示模板身份）与 E1-7B 评分规格的内容身份
FLASH_MANIFEST = REPO / "runs/2026-10-13-product-flash-switch/pilot/manifest.json"
E1_7B_SCORING_CONTENT = "bf5fabac734d8784f2e4d350182861b8a6279c88b8d59ab51aae529821ec2dae"
K51, M52 = "US:SYNTH2026:K51", "US:SYNTH2026:M52"
HEALTHY = {"minor": "decline", "major": "decline", "pause_order": []}
REPO_FILES = (
    "configs/life/synthetic_e1_convenience.yaml",
    "configs/life/scoring_e1_convenience.yaml",
    "configs/life/evaluation_e1_convenience.yaml",
    "configs/life/city_facts_e1_convenience.yaml",
    "configs/life/roster_e1_convenience.yaml",
    "configs/life/run_e1_convenience_flash.yaml",
    "configs/life/run_e1_convenience_mock.yaml",
    "configs/life/synthetic_e1_7b_pilot.yaml",
    "configs/life/scoring_e1_7b_pilot.yaml",
    "configs/life/evaluation_e1_7b.yaml",
    "configs/life/city_facts_e1_7b.yaml",
    "configs/life/roster_e1_7b.yaml",
    "configs/life/run_e1_7b_mock.yaml",
    "configs/life/run_e1_7b_flash.yaml",
    "configs/models.yaml",
)


# ---------------------------------------------------------------------- 夹具


class World:
    def __init__(self) -> None:
        self.bundle, _ = load_life_bundle(LIFE)
        self.scoring, _ = load_scoring_config(SCORING, self.bundle)
        self.evaluation = load_evaluation_config(EVALUATION, self.bundle, self.scoring).spec
        self.protocol = self.bundle.protocol
        self.profile = self.bundle.stated_profiles[0]

    def catalog(self, city: str):
        return self.bundle.catalog_for(city)

    def tax(self, city: str):
        return binding_from_policy(self.bundle.tax_policy(self.catalog(city).tax_policy_id))

    def inputs(self, city: str, profile=None) -> ScoringInputs:
        p = profile or self.profile
        return ScoringInputs(
            spec=self.scoring.spec,
            conditions=self.scoring.conditions_for(city),
            preference_input=p,
            hard_requirements=p.hard_requirements,
        )

    def start(self, city: str):
        return self.bundle.initial_state("u301.k51.r0" if city == K51 else "u301.m52.r0")

    def year(self, state, items, *, protocol=None, catalog=None, profile=None, emergency=None):
        action = YearAction.model_validate(
            {
                "person_id": state.person_id,
                "clone_id": state.clone_id,
                "year_index": state.year_index,
                "items": items,
                "minimum_fallback": ["housing", "food"],
                "emergency": emergency or HEALTHY,
                "rationale": "测试：预先写好的计划",
            }
        )
        city = str(state.city)
        return run_life_year(
            protocol=protocol or self.protocol,
            state=state,
            action=action,
            catalog=catalog or self.catalog(city),
            tax=self.tax(city),
            scoring=self.inputs(city, profile),
        )


@pytest.fixture(scope="module")
def w() -> World:
    return World()


def whole(oid, start=1, end=12):
    return {"opportunity_id": oid, "quantity": 1, "start_month": start, "end_month": end}


def once(oid, month, quantity=1):
    return {"opportunity_id": oid, "quantity": quantity, "month": month}


def base(city=K51, job="clerk"):
    c = "k51" if city == K51 else "m52"
    return [whole(f"{c}.housing.far"), whole(f"{c}.food.basic"), whole(f"{c}.job.{job}")]


def conv(r):
    """逐月便利分项（ComponentMonth）。"""
    return [
        next(c for c in m.components if c.component_id == DAILY_CONVENIENCE) for m in r.score.months
    ]


def q_of(r) -> list[str]:
    return [c.convenience.relief_hours.exact for c in conv(r)]


def s_hand(q) -> float:
    q = Fraction(q)
    return float(min(Fraction(9, 10), q / (q + 8)))


# ---------------------------------------------------------------------- 1. 便利分：只读实际减少的家务小时


def test_none_device_service_give_the_hand_computed_relief_and_score(w):
    s0 = w.start(K51)
    none = w.year(s0, base())
    device = w.year(s0, [*base(), once("k51.durable.convenience_pack", 1)])
    service = w.year(s0, [*base(), whole("k51.service.chores")])
    for r, q in ((none, "0"), (device, "12"), (service, "18")):
        assert set(q_of(r)) == {q}
        assert {c.s for c in conv(r)} == {s_hand(q)}
        assert all(c.status == "known" for c in conv(r))
    # 没有采用方案：确定的 0，不是未知
    assert conv(none)[0].reasons[0].startswith("这个月没有在用的家务替代")
    # 明细逐项：基准 − 残余
    rows = {t.task: t for t in conv(service)[0].convenience.tasks}
    assert (rows["laundry"].baseline_hours.exact, rows["laundry"].residual_hours.exact) == (
        "12",
        "2",
    )
    assert (rows["cleaning"].relief_hours.exact, rows["cleaning"].opportunity_id) == (
        "8",
        "k51.service.chores",
    )


def test_device_and_service_together_do_not_add_up(w):
    s0 = w.start(K51)
    service = w.year(s0, [*base(), whole("k51.service.chores")])
    both = w.year(
        s0, [*base(), whole("k51.service.chores"), once("k51.durable.convenience_pack", 1)]
    )
    assert set(q_of(both)) == {"18"}  # 每项取最小残余：洗衣 min(4, 2)、打扫 min(6, 2)
    assert [c.s for c in conv(both)] == [c.s for c in conv(service)]
    assert [m.u for m in both.score.months] == [m.u for m in service.score.months]


def test_quantity_and_a_second_identical_device_do_not_raise_the_score(w):
    s0 = w.start(K51)
    one = w.year(s0, [*base(), once("k51.durable.convenience_pack", 1)])
    two = w.year(s0, [*base(), once("k51.durable.convenience_pack", 1, quantity=2)])
    assert q_of(two) == q_of(one) and [c.s for c in conv(two)] == [c.s for c in conv(one)]
    out1, out2 = one.settlement.outcome, two.settlement.outcome
    assert out2.months[0].cash_end.amount == out1.months[0].cash_end.amount - 1440  # 钱照付两份


def test_same_relief_same_score_whatever_the_brand_price_or_kind(w):
    """同样的残余小时：另一品牌、价格高一倍的设备，与把同样残余写成按月服务，便利分逐位相同。"""
    cat = w.catalog(K51)
    pack = cat.opportunity("k51.durable.convenience_pack")
    service = cat.opportunity("k51.service.chores")
    premium = pack.model_copy(
        update={
            "opportunity_id": "k51.durable.premium_pack",
            "label": "高档便利设备包（另一品牌）",
            "price": pack.price.model_copy(
                update={"amount": Money(2880.0, pack.price.amount.numeraire)}
            ),
        }
    )
    like_device = service.model_copy(
        update={
            "opportunity_id": "k51.service.like_device",
            "label": "残余与设备包相同的按月服务",
            "resource": service.resource.model_copy(
                update={"substitutes": pack.resource.substitutes}
            ),
        }
    )
    variant = cat.model_copy(update={"opportunities": (*cat.opportunities, premium, like_device)})
    s0 = w.start(K51)
    a = w.year(s0, [*base(), once("k51.durable.convenience_pack", 1)])
    b = w.year(s0, [*base(), once("k51.durable.premium_pack", 1)], catalog=variant)
    c = w.year(s0, [*base(), whole("k51.service.like_device")], catalog=variant)
    assert [x.s for x in conv(a)] == [x.s for x in conv(b)] == [x.s for x in conv(c)]
    assert set(q_of(c)) == {"12"}


def test_no_purchase_reward_bought_month_scores_like_a_held_month(w):
    s0 = w.start(K51)
    bought = w.year(s0, [*base(), once("k51.durable.convenience_pack", 1)])
    held_state = s0.model_copy(
        update={
            "holdings": (
                Holding(
                    opportunity_id="k51.durable.convenience_pack",
                    quantity=1,
                    acquired_year=0,
                    remaining_months=60,
                ),
            )
        }
    )
    held = w.year(held_state, base())
    assert [m.u for m in bought.score.months] == [m.u for m in held.score.months]
    assert (
        bought.settlement.outcome.months[-1].cash_end.amount
        < held.settlement.outcome.months[-1].cash_end.amount
    )


def test_spend_does_not_enter_two_cities_same_relief_same_score(w):
    k = w.year(w.start(K51), [*base(K51), whole("k51.service.chores")])
    m = w.year(w.start(M52), [*base(M52), whole("m52.service.chores")])
    assert [x.s for x in conv(k)] == [x.s for x in conv(m)]
    assert k.settlement.outcome.months[-1].cash_end.amount == 12048
    assert m.settlement.outcome.months[-1].cash_end.amount == 13344


# ---------------------------------------------------------------------- 2. 只有实际生效的服务算


def test_mid_year_purchase_expiry_and_unaffordable_service_months(w):
    s0 = w.start(K51)
    mid = w.year(s0, [*base(), once("k51.durable.convenience_pack", 7)])
    assert q_of(mid) == ["0"] * 6 + ["12"] * 6
    expiring = s0.model_copy(
        update={
            "holdings": (
                Holding(
                    opportunity_id="k51.durable.convenience_pack",
                    quantity=1,
                    acquired_year=0,
                    remaining_months=5,
                ),
            )
        }
    )
    assert q_of(w.year(expiring, base())) == ["12"] * 5 + ["0"] * 7
    poor = s0.model_copy(update={"cash": Money(300.0, s0.cash.numeraire)})
    r = w.year(poor, [*base(job="shop_parttime"), whole("k51.service.chores")])
    assert q_of(r) == ["18", "18", "18", "0", "18", "0", "18", "0", "0", "0", "0", "0"]
    item = next(i for i in r.settlement.outcome.items if i.opportunity_id == "k51.service.chores")
    assert [x.month for x in item.rejections] == [4, 6, 8, 9, 10, 11, 12]


def test_next_year_device_does_not_score_early(w):
    cat = w.catalog(K51)
    pack = cat.opportunity("k51.durable.convenience_pack")
    later = pack.model_copy(
        update={"duration": pack.duration.model_copy(update={"effective_from": "next_year"})}
    )
    variant = cat.model_copy(
        update={
            "opportunities": tuple(
                later if o.opportunity_id == pack.opportunity_id else o for o in cat.opportunities
            )
        }
    )
    r1 = w.year(w.start(K51), [*base(), once("k51.durable.convenience_pack", 1)], catalog=variant)
    assert set(q_of(r1)) == {"0"}
    r2 = w.year(r1.next_state, base(), catalog=variant)
    assert set(q_of(r2)) == {"12"}


def test_purchase_cancelled_when_ill_gives_no_relief(w):
    ill = IllnessEventSource(
        mode="injected",
        stream_version="e1-5c-illness-stream-v1",
        injected=(
            InjectedIllness(
                person_id="u301",
                repeat_index=0,
                year_index=1,
                month=3,
                severity="major",
                note="测试：第 3 月月初大病",
            ),
        ),
        note="测试：指定事件",
    )
    protocol = w.protocol.model_copy(update={"illness_events": ill})
    emergency = {**HEALTHY, "cancel_purchases_when_ill": ["k51.durable.convenience_pack"]}
    r = w.year(
        w.start(K51),
        [*base(), once("k51.durable.convenience_pack", 3)],
        protocol=protocol,
        emergency=emergency,
    )
    item = next(
        i for i in r.settlement.outcome.items if i.opportunity_id == "k51.durable.convenience_pack"
    )
    assert item.status == "cancelled_when_ill"
    assert set(q_of(r)) == {"0"}  # 没买成就没有减负；病月照常评分（大病月 H = 0 另计）


# ---------------------------------------------------------------------- 3. 缺资料与越界


def _score_with(w, result, month_fn):
    out = result.settlement.outcome
    months = list(out.resources.months)
    months[2] = month_fn(months[2])
    variant = out.model_copy(
        update={"resources": out.resources.model_copy(update={"months": tuple(months)})}
    )
    return score_year(
        protocol=w.protocol,
        spec=w.scoring.spec,
        conditions=w.scoring.conditions_for(K51),
        catalog=w.catalog(K51),
        state=w.start(K51),
        outcome=variant,
        preference=preference_from_stated_profile(w.profile),
    )


def test_missing_row_or_incomplete_month_is_unknown_not_zero(w):
    r = w.year(w.start(K51), [*base(), once("k51.durable.convenience_pack", 1)])

    def drop_cleaning(m):
        rows = tuple(c for c in m.contributions if c.task != "cleaning")
        return m.model_copy(update={"contributions": rows})

    s = _score_with(w, r, drop_cleaning)
    third = next(c for c in s.months[2].components if c.component_id == DAILY_CONVENIENCE)
    assert third.status == "unknown" and "cleaning" in third.reasons[0]
    assert s.months[2].status == "incomplete" and s.annual_mean is None
    s = _score_with(w, r, lambda m: m.model_copy(update={"resource_check": "known_lower_bound"}))
    third = next(c for c in s.months[2].components if c.component_id == DAILY_CONVENIENCE)
    assert third.status == "unknown" and "不是完整检查" in third.reasons[0]


def test_duplicate_or_out_of_bounds_rows_are_refused(w):
    r = w.year(w.start(K51), [*base(), once("k51.durable.convenience_pack", 1)])

    def duplicate(m):
        row = next(c for c in m.contributions if c.task == "laundry")
        return m.model_copy(update={"contributions": (*m.contributions, row)})

    with pytest.raises(LifeContractError, match="有两行"):
        _score_with(w, r, duplicate)

    def over(m):
        rows = tuple(
            c.model_copy(update={"hours": ExactAmount.of(Fraction(13))})
            if c.task == "laundry"
            else c
            for c in m.contributions
        )
        return m.model_copy(update={"contributions": rows})

    with pytest.raises(LifeContractError, match="记录越界"):
        _score_with(w, r, over)


def _spec_variant(edit) -> dict:
    raw = yaml.safe_load(SCORING.read_text(encoding="utf-8"))
    raw = copy.deepcopy(raw)
    edit(raw)
    return raw


def _component(raw, cid):
    return next(c for c in raw["spec"]["components"] if c["component_id"] == cid)


def test_unit_conflicts_are_refused():
    def wrong_component_unit(raw):
        _component(raw, DAILY_CONVENIENCE)["unit"] = "分钟/工作日往返"

    with pytest.raises(LifeContractError, match="同一个单位"):
        parse_scoring_config(_spec_variant(wrong_component_unit))

    def not_monthly_hours(raw):
        c = _component(raw, DAILY_CONVENIENCE)
        c["unit"] = "小时"
        c["convenience"]["unit"] = "小时"

    with pytest.raises(LifeContractError, match="每月小时"):
        parse_scoring_config(_spec_variant(not_monthly_hours))


def test_spec_rules_old_laundry_score_cannot_coexist_and_old_versions_cannot_declare_it():
    def add_laundry(raw):
        raw["spec"]["components"].insert(
            2,
            {
                "component_id": "laundry",
                "dimension": "material",
                "material_need": "laundry",
                "unit": "有/无",
                "tau": 1,
                "combine": "any",
                "description": "旧的洗衣分",
                "sources": [
                    {
                        "source_id": "laundry_item",
                        "records": ["durable_good"],
                        "functions": ["laundry"],
                        "value": "present",
                        "measure": "presence",
                        "unit": "有/无",
                        "note": "旧来源",
                    }
                ],
            },
        )

    with pytest.raises(LifeContractError, match="不能并存"):
        parse_scoring_config(_spec_variant(add_laundry))

    def old_version(raw):
        raw["spec"]["scoring_version"] = "e1-5c-health-v1"

    with pytest.raises(LifeContractError, match="不读日常生活便利"):
        parse_scoring_config(_spec_variant(old_version))

    def none_declared(raw):
        raw["spec"]["components"] = [
            c for c in raw["spec"]["components"] if c["component_id"] != DAILY_CONVENIENCE
        ]

    with pytest.raises(LifeContractError, match="恰好有一个日常生活便利分项"):
        parse_scoring_config(_spec_variant(none_declared))


def test_versions_are_registered_on_the_health_and_growth_transport_branches():
    assert CONVENIENCE_SCORING_VERSIONS <= HEALTH_SCORING_VERSIONS
    assert CONVENIENCE_SCORING_VERSIONS <= GROWTH_TRANSPORT_VERSIONS


def test_city_condition_and_protocol_checks(w):
    def known(raw):
        for cc in raw["city_conditions"]:
            for c in cc["components"]:
                if c["component_id"] == DAILY_CONVENIENCE:
                    c.update(status="known", base=0, cap=0.9)

    cfg = parse_scoring_config(_spec_variant(known))
    errors, _ = scoring_config_problems(cfg, w.bundle)
    assert any("应写 plan_level 或 unknown" in e for e in errors)
    rules = w.protocol.resource_rules
    no_cleaning = w.protocol.model_copy(
        update={
            "resource_rules": rules.model_copy(
                update={
                    "household_tasks": tuple(
                        t for t in rules.household_tasks if t.task != "cleaning"
                    )
                }
            )
        }
    )
    assert any("cleaning" in p for p in convenience_protocol_problems(w.scoring.spec, no_cleaning))
    old_scale = w.protocol.model_copy(update={"scoring_scale_version": "e1-5c-health-v1"})
    assert any("两边一致" in p for p in convenience_protocol_problems(w.scoring.spec, old_scale))


def test_old_laundry_points_are_not_read_as_convenience_and_vice_versa(w):
    spec = w.scoring.spec
    assert convenience_preference_problems(spec, "u", {"housing_space": 50, "laundry": 50})
    assert not convenience_preference_problems(
        spec, "u", {"housing_space": 50, "daily_convenience": 50}
    )
    old = parse_scoring_config(
        yaml.safe_load((REPO / "configs/life/scoring_e1_7b_pilot.yaml").read_text(encoding="utf-8"))
    ).spec
    assert convenience_preference_problems(old, "u", {"daily_convenience": 100})
    assert not convenience_preference_problems(old, "u", {"laundry": 100})
    # 评分入口同样拒绝：旧的洗衣点数交给便利版本
    legacy = w.profile.model_copy(update={"material_points": {"housing_space": 60, "laundry": 40}})
    with pytest.raises(LifeContractError, match="不能当作日常生活便利"):
        w.year(w.start(K51), base(), profile=legacy)


# ---------------------------------------------------------------------- 4. 版本与内容身份


def test_content_identity_follows_tau_cap_and_tasks_and_old_specs_keep_theirs(w):
    spec = w.scoring.spec
    comp = spec.component(DAILY_CONVENIENCE)

    def with_comp(**upd) -> LifeScoringSpec:
        new = comp.model_copy(update=upd)
        return spec.model_copy(
            update={
                "components": tuple(
                    new if c.component_id == comp.component_id else c for c in spec.components
                )
            }
        )

    base_fp = spec.content_fingerprint
    assert w.protocol.energy_feedback.scoring_content_fingerprint == base_fp
    assert with_comp(tau=9.0).content_fingerprint != base_fp
    assert (
        with_comp(convenience=comp.convenience.model_copy(update={"cap": 0.8})).content_fingerprint
        != base_fp
    )
    assert (
        with_comp(
            convenience=comp.convenience.model_copy(update={"definition": "只改说明文字"})
        ).content_fingerprint
        == base_fp
    )
    old = parse_scoring_config(
        yaml.safe_load((REPO / "configs/life/scoring_e1_7b_pilot.yaml").read_text(encoding="utf-8"))
    ).spec
    assert old.content_fingerprint == E1_7B_SCORING_CONTENT


def test_cap_binds_when_set_low(w):
    spec = w.scoring.spec
    comp = spec.component(DAILY_CONVENIENCE)
    low = comp.model_copy(update={"convenience": comp.convenience.model_copy(update={"cap": 0.5})})
    variant = spec.model_copy(
        update={
            "components": tuple(
                low if c.component_id == comp.component_id else c for c in spec.components
            )
        }
    )
    r = w.year(w.start(K51), [*base(), whole("k51.service.chores")])
    s = score_year(
        protocol=w.protocol,
        spec=variant,
        conditions=w.scoring.conditions_for(K51),
        catalog=w.catalog(K51),
        state=w.start(K51),
        outcome=r.settlement.outcome,
        preference=preference_from_stated_profile(w.profile),
    )
    c0 = next(c for c in s.months[0].components if c.component_id == DAILY_CONVENIENCE)
    assert (c0.s, c0.capped) == (0.5, True) and c0.raw == 18 / 26


def test_coverage_reports_an_action_mechanism_for_every_arrangement(w):
    rep = coverage_report(
        spec=w.scoring.spec,
        group=w.scoring.comparison_group,
        conditions=w.scoring.city_conditions,
        catalogs=w.bundle.catalogs,
        preference=preference_from_stated_profile(w.profile),
    )
    leaf = next(x for x in rep.leaves if x.leaf == DAILY_CONVENIENCE)
    assert (leaf.support, leaf.action_mechanism, leaf.action_scope, leaf.fact_evidence) == (
        "supported",
        True,
        "all_arrangements",
        "synthetic_only",
    )
    assert abs(leaf.weight - 0.8 * (40 / 90) * 0.4) < 1e-12


# ---------------------------------------------------------------------- 5. 学习与资源：省下的时间进入已有执行链


def test_convenience_makes_the_course_plan_feasible_without_changing_learning(w):
    s0 = w.start(K51)
    course = whole("k51.training.accounting_course")
    none = w.year(s0, [*base(), course])
    device = w.year(s0, [*base(), course, once("k51.durable.convenience_pack", 1)])
    assert none.settlement.status == "plan_infeasible"
    assert {p.code for p in none.settlement.problems} == {"energy_over_limit"}
    assert device.settlement.status == "completed"
    assert device.settlement.outcome.resources.months[0].energy_used.exact == "2461/10"
    held = {a.ability_id: a for a in device.next_state.abilities}
    assert held["accounting_cert"].usable_from_year == 2


def test_zero_weight_keeps_the_time_saving_but_adds_no_score(w):
    zero = w.profile.model_copy(update={"material_points": {"housing_space": 50, "fresh_food": 50}})
    s0 = w.start(K51)
    none = w.year(s0, base(), profile=zero)
    device = w.year(s0, [*base(), once("k51.durable.convenience_pack", 1)], profile=zero)
    assert [m.u for m in none.score.months] == [m.u for m in device.score.months]

    def chores(r):
        m = r.settlement.outcome.resources.months[0]
        return sum(
            (c.hours.fraction() for c in m.contributions if c.category == "household_task"),
            Fraction(0),
        )

    assert (chores(none), chores(device)) == (22, 10)


# ---------------------------------------------------------------------- 6. 期末估值用上了两种方式


def test_terminal_valuation_uses_held_device_and_both_ways(w):
    s0 = w.start(K51)

    def three(items_of):
        st, out = s0, []
        for y in (1, 2, 3):
            r = w.year(st, items_of(y))
            out.append(r)
            st = r.next_state
        return out

    held = three(
        lambda y: [*base()] + ([once("k51.durable.convenience_pack", 1)] if y == 1 else [])
    )
    never = three(lambda y: base())
    plans = {}
    for name, res in (("held", held), ("never", never)):
        ev = evaluate_clone(
            spec=w.evaluation,
            protocol=w.protocol,
            catalog=w.catalog(K51),
            tax=w.tax(K51),
            scoring=w.inputs(K51),
            results=res,
        )
        plans[name] = {p.plan_id: p for p in ev.valuation.plans}
    assert len(plans["never"]) == 6
    assert any("照用旧的" in t for t in plans["held"]["p2_current_device"].skipped_purchases)
    assert (
        plans["held"]["p1_current_basic"].v_plan_display
        > plans["never"]["p1_current_basic"].v_plan_display
    )
    assert plans["never"]["p3_current_service"].status == "complete"
    assert {
        plans["never"][p].reason_kind
        for p in ("p4_qualified_basic", "p5_qualified_device", "p6_qualified_service")
    } == {"no_qualification"}


# ---------------------------------------------------------------------- 7. mock、规划请求与入口


def _views(w, city, cash=3000.0, holdings=(), material=None):
    """一份最小的请求视图（与 mock 读的字段同形），只用来单独检查 M5c。"""
    cat = w.catalog(city)
    from agentlayer.life_views import build_observation, standard_product_permission

    state = w.start(city)
    if holdings:
        state = state.model_copy(update={"holdings": holdings})
    if cash != 3000.0:
        state = state.model_copy(update={"cash": Money(cash, state.cash.numeraire)})
    profile = (
        w.profile
        if material is None
        else w.profile.model_copy(update={"material_points": material})
    )
    obs = build_observation(
        permission=standard_product_permission("e1-7b-product-v1"),
        profile=profile,
        state=state,
        catalog=cat,
        last_feedback=None,
        resource_rules=w.protocol.resource_rules,
        energy_feedback=w.protocol.energy_feedback,
        career_rules=w.protocol.career_rules,
        savings_rules=w.protocol.savings_rules,
        health_rules=w.protocol.health_rules,
        health_input=w.protocol.health_input,
    )
    return {"observation": obs.model_dump(mode="json"), "year_index": 1, "clone_id": state.clone_id}


def _m5(w, city, **kw):
    from agentlayer.life_mock import _View

    planner = ConvenienceMockPlanner()
    notes: list[str] = []
    v = _View(_views(w, city, **kw))
    return planner._extras(v, 2500 - 1380, 0.0, notes), notes


def test_mock_picks_the_cheaper_way_per_hour_from_public_facts(w):
    items, notes = _m5(w, K51)
    assert items == [
        once("k51.durable.convenience_pack", 1)
    ]  # 1,440 / 60 个月 / 12 小时 = 2.0 每小时
    items, notes = _m5(w, M52)
    assert items == [
        whole("m52.service.chores")
    ]  # 108 / 18 = 6.0 每小时，设备 6,000 / 60 / 12 ≈ 8.3
    items, _ = _m5(w, K51, material={"housing_space": 50, "fresh_food": 50})
    assert items == []  # 用户没有给便利点数：不买
    held = (
        Holding(
            opportunity_id="k51.durable.convenience_pack",
            quantity=1,
            acquired_year=0,
            remaining_months=12,
        ),
    )
    items, notes = _m5(w, K51, holdings=held)
    assert items == [] and "已有" in notes[0]


def test_old_mock_rules_ignore_the_new_key_and_the_factory_matches_identities(w):
    from agentlayer.life_mock import _View

    v = _View(_views(w, K51))
    assert RuleBasedMockPlanner()._extras(v, 1120.0, 0.0, []) == []  # 旧 M5 只看 laundry
    assert type(mock_planner_for(PlannerSpec(identity=MOCK_IDENTITY))) is RuleBasedMockPlanner
    assert (
        type(mock_planner_for(PlannerSpec(identity=CONVENIENCE_MOCK_IDENTITY)))
        is ConvenienceMockPlanner
    )
    with pytest.raises(ValueError, match="不认识的 mock 身份"):
        mock_planner_for(
            PlannerSpec(identity=CONVENIENCE_MOCK_IDENTITY.model_copy(update={"version": "v9"}))
        )


def test_guide_section_only_in_the_new_template_and_old_template_identity_unchanged(w):
    old = action_guide(w.protocol)
    new = action_guide(w.protocol, CONVENIENCE_ACTION_GUIDE_VERSION)
    topics_old = [s.topic for s in old.sections]
    topics_new = [s.topic for s in new.sections]
    assert "日常杂务：设备与人工服务（可选）" not in topics_old
    assert topics_new[-2] == "日常杂务：设备与人工服务（可选）"
    text = " ".join(new.sections[-2].rules)
    for phrase in (
        "一次性购买",
        "按月购买",
        "到期",
        "节省不相加",
        "本人自己做",
        "不会自动变成休息",
    ):
        assert phrase in text
    assert_payload_clean(new.model_dump(mode="json"))
    manifest = json.loads(FLASH_MANIFEST.read_text(encoding="utf-8"))
    assert prompt_template_fingerprint() == manifest["identities"]["prompt_template"]
    assert (
        prompt_template_fingerprint(CONVENIENCE_PROMPT_TEMPLATE_VERSION)
        != prompt_template_fingerprint()
    )


@pytest.fixture
def conv_repo(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    for rel in REPO_FILES:
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)
    for rel, ledger in (
        (FLASH, "budget-conv"),
        (Path("configs/life/run_e1_7b_flash.yaml"), "budget-flash"),
    ):
        p = root / rel
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        data["run"]["paid_budget"]["ledger_dir"] = ledger
        p.write_text(
            yaml.safe_dump(
                json.loads(json.dumps(data, default=str)), allow_unicode=True, sort_keys=False
            ),
            encoding="utf-8",
            newline="\n",
        )
    monkeypatch.chdir(root)
    return root


def test_mock_run_and_requests_use_the_new_inputs(conv_repo):
    world = preflight(MOCK)
    out = conv_repo / "mock-run"
    outcome = start_run(world, mock_planner_for(world.config.planner), out, run_id="conv-test")
    assert outcome.status == "completed"
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["identities"]["prompt_template"] == prompt_template_fingerprint(
        CONVENIENCE_PROMPT_TEMPLATE_VERSION
    )
    payloads = planner_payloads(out)
    assert len(payloads) == 6
    for p in payloads:
        assert p["observation"]["profile"]["material_points"]["daily_convenience"] == 40
        assert p["guide"]["version"] == CONVENIENCE_ACTION_GUIDE_VERSION
        assert any(s["topic"].startswith("日常杂务") for s in p["guide"]["sections"])
        text = json.dumps(p, ensure_ascii=False)
        for hidden in ("tau_hours", "relief_hours", "household_baseline_minus_residual_v1"):
            assert hidden not in text
    accepted = RunStore(out).records("accepted")
    first = {
        a["clone_id"]: {i["opportunity_id"] for i in a["action"]["items"]}
        for a in accepted
        if a["year_index"] == 1
    }
    assert "k51.durable.convenience_pack" in first["u301.k51.r0"]
    assert "m52.service.chores" in first["u301.m52.r0"]


def test_convenience_protocol_requires_the_convenience_template(conv_repo):
    world = preflight(FLASH)
    old = world.config.model_copy(update={"prompt_template": "e1-7b-prompt-v1"})
    problems = run_config_problems(old, world.roster_config, world.bundle, world.facts)
    assert any(CONVENIENCE_PROMPT_TEMPLATE_VERSION in p for p in problems)


def _driver():
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("run_life_e1_7b")


def test_offline_probe_refuses_a_mock_config_with_other_data(conv_repo, monkeypatch):
    driver = _driver()
    live = preflight(FLASH)
    assert driver.pairing_problems(live, preflight(MOCK)) == []
    old_mock = preflight(Path("configs/life/run_e1_7b_mock.yaml"))
    assert driver.pairing_problems(live, old_mock)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_life_e1_7b.py",
            "offline-check",
            "--out",
            "probe",
            "--config",
            str(FLASH),
            "--mock-config",
            "configs/life/run_e1_7b_mock.yaml",
        ],
    )
    with pytest.raises(SystemExit, match="不是同一套资料与名单"):
        driver.main()
    monkeypatch.setattr(
        sys, "argv", ["run_life_e1_7b.py", "mock", "--out", "m", "--mock-config", str(MOCK)]
    )
    with pytest.raises(SystemExit) as exc:
        driver.main()
    assert exc.value.code == 2  # 非默认 mock 配置必须写 --run-id
