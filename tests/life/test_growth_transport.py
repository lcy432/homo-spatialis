"""E1-5A 补充：成长满足感与广义交通评分（`DECISIONS/0024`）。按补充 prompt 第七节的验收分节。

数字的手算见 ``configs/life/synthetic_e1_5a_growth_transport.yaml`` 文末。发展维 b = 2、τ = 2、上限 0.9、η_growth = 12：
不学习 2/(2+2) = 0.5，学习当月 (2 + 12 × 1/12)/(2 + 1 + 2) = 0.6。交通 τ_T = 60、ρ = 0.25、D_ref = 20、近处住房 T = 30。
评分的中间量按精确分数算，最后才转成浮点，所以可以直接和分数比较。
"""

from __future__ import annotations

import copy
import json
from fractions import Fraction

import pytest
from pydantic import ValidationError

from agentlayer.life_views import (
    build_observation,
    observation_payload,
    standard_product_permission,
)
from common.life_contracts import LifeContractError
from common.life_scoring_contracts import LifeScoringSpec
from econ.life_coverage import coverage_report
from econ.life_feedback import apply_feedback, feedback_record
from econ.life_scoring import preference_from_stated_profile, score_year
from ledger.life_catalog import cross_validate, parse_bundle
from ledger.life_scoring_config import (
    ScoringConfig,
    parse_scoring_config,
    scoring_config_problems,
)

from .career_support import income, max_burden, run_clone, settle, year
from .feedback_support import bound_year
from .settle_support import catalog_of, cents, plan_of, revise, state_of, with_items

HOME = {"opportunity_id": "a71.housing.near", "quantity": 1, "start_month": 1, "end_month": 12}
FOOD = {"opportunity_id": "a71.food.fresh", "quantity": 1, "start_month": 1, "end_month": 12}
WASHER = {"opportunity_id": "a71.durable.washer", "quantity": 1, "month": 1}
CLERK = "a71.job.clerk_netco"
ACCOUNTANT = "a71.job.accountant_netco"
REMOTE = "a71.job.support_remote"
SHOP = "a71.job.shop_parttime"
ACCOUNTING = "a71.training.accounting_course"
PYTHON = "a71.training.python_selfstudy"


def item(oid: str, start: int = 1, end: int = 12) -> dict:
    return {"opportunity_id": oid, "quantity": 1, "start_month": start, "end_month": end}


def series(score, component: str) -> list:
    return [next(c for c in m.components if c.component_id == component).s for m in score.months]


def comp(score, month: int, component: str):
    return next(c for c in score.months[month - 1].components if c.component_id == component)


def score_with(bundle, config, start, settlement, *, spec=None, preference=None):
    """同一份已结算的生活事实，换一套评分规格或偏好重新评分（评分是纯函数，不改结算）。"""
    profile = next(p for p in bundle.stated_profiles if p.person_id == start.person_id)
    pref = preference or preference_from_stated_profile(profile)
    return score_year(
        protocol=bundle.protocol,
        spec=spec or config.spec,
        conditions=config.conditions_for(str(start.city)),
        catalog=catalog_of(bundle, start),
        state=start,
        outcome=settlement.outcome,
        preference=pref,
    )


def arm(scoring_raw: dict, **changes) -> dict:
    """由主评分配置派生一个对照臂：另起规格编号（同一编号不能装两套内容），只改成长或交通参数。"""
    data = copy.deepcopy(scoring_raw)
    suffix = changes.pop("suffix")
    data["spec"]["spec_id"] = f"score.e1-5a-gt.{suffix}"
    for c in data["city_conditions"]:
        c["spec_id"] = data["spec"]["spec_id"]
    for comp_ in data["spec"]["components"]:
        if comp_["component_id"] == "opportunity" and "eta" in changes:
            comp_["growth"]["eta"] = changes["eta"]
        if comp_["component_id"] == "commute" and "rho" in changes:
            comp_["transport"]["rho"] = changes["rho"]
    return data


def legacy(bundle, scoring_e1_5a_raw: dict) -> ScoringConfig:
    """E1-5A 原评分规则（e1-3-supplement-v1：方案级通勤、发展维只有背景）搬到新协议上，用来对比同一份生活事实。"""
    data = copy.deepcopy(scoring_e1_5a_raw)
    data["spec"]["spec_id"] = "score.e1-5a-gt.legacy-rules"
    data["spec"]["protocol_id"] = bundle.protocol.protocol_id
    data["comparison_group"]["group_id"] = "group.e1-5a-gt.legacy-rules"
    for c in data["city_conditions"]:
        c["spec_id"] = "score.e1-5a-gt.legacy-rules"
    config = parse_scoring_config(data)
    errors, _ = scoring_config_problems(config, bundle)
    assert errors == [], errors
    return config


@pytest.fixture(scope="module")
def scoring_e1_5a_raw():
    import yaml

    from .conftest import SCORING_E1_5A

    return yaml.safe_load(SCORING_E1_5A.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 1–2：同样的生活事实，只有实际成长不同；发展权重决定总分变多少
# --------------------------------------------------------------------------


def test_growth_changes_only_the_development_score_by_hand(bundle_gt, scoring_gt):
    (learn, *_) = run_clone(bundle_gt, scoring_gt, "u61.a71.learn")
    (plain, *_) = run_clone(bundle_gt, scoring_gt, "u61.a71.nolearn")
    a, b = learn.score, plain.score
    assert series(a, "opportunity") == [0.6] * 12 and series(b, "opportunity") == [0.5] * 12
    for other in ("housing_space", "fresh_food", "laundry", "safety", "leisure", "commute"):
        assert series(a, other) == series(b, other), other
    # 发展权重 0.2：每个月总分差 0.2 × (0.6 − 0.5) = 0.02
    assert [x.u - y.u for x, y in zip(a.months, b.months, strict=True)] == pytest.approx(
        [0.02] * 12, rel=1e-12
    )
    g = comp(a, 1, "opportunity").growth
    assert (g.g, g.eta, g.contribution) == (1 / 12, 12, 1.0)
    (step,) = g.steps
    assert (step.ability_id, step.before, step.after, step.via) == (
        "accounting_cert",
        0.0,
        1 / 12,
        (ACCOUNTING,),
    )
    assert comp(a, 1, "opportunity").z == 3.0  # 2 + 12 × 1/12
    # 能力照旧第 2 年才可用；第 2 年做会计不再学习，发展维回到背景 0.5
    assert learn.next_state.abilities[0].usable_from_year == 2


def test_zero_weight_ignores_growth_and_weights_scale_it(bundle_gt, scoring_gt):
    (learn, *_) = run_clone(bundle_gt, scoring_gt, "u62.a71.learn")
    (plain, *_) = run_clone(bundle_gt, scoring_gt, "u62.a71.nolearn")
    assert series(learn.score, "opportunity") == [0.6] * 12
    assert [m.u for m in learn.score.months] == [
        m.u for m in plain.score.months
    ]  # 权重 0：总分一点不变
    # 同一份事实，按原有加权机制换几种发展权重：差值 = w × 0.1
    (l61, *_) = run_clone(bundle_gt, scoring_gt, "u61.a71.learn")
    (p61, *_) = run_clone(bundle_gt, scoring_gt, "u61.a71.nolearn")
    start = state_of(bundle_gt, "u61.a71.learn")
    plain_start = state_of(bundle_gt, "u61.a71.nolearn")
    base = preference_from_stated_profile(
        next(p for p in bundle_gt.stated_profiles if p.person_id == "u61")
    )
    for w in (0.0, 0.05, 0.3):
        weights = {**base.dimension_weights, "opportunity": w, "material": 0.5 - w}
        pref = revise(base, dimension_weights=weights)
        x = score_with(bundle_gt, scoring_gt, start, l61.settlement, preference=pref)
        y = score_with(bundle_gt, scoring_gt, plain_start, p61.settlement, preference=pref)
        assert [i.u - j.u for i, j in zip(x.months, y.months, strict=True)] == pytest.approx(
            [w * 0.1] * 12, rel=1e-12, abs=1e-15
        )


# --------------------------------------------------------------------------
# 3–4：没付学费、没参加、只写理由不长进度；重复课程、多来源、已有能力不刷分
# --------------------------------------------------------------------------


def test_unpaid_unattended_or_claimed_learning_grows_nothing(bundle_gt, scoring_gt):
    broke = revise(
        state_of(bundle_gt, "u61.a71.learn"),
        cash={"amount": 0, "numeraire": "USD_2024_LOCAL"},
    )
    r = year(bundle_gt, scoring_gt, broke, plan_of(bundle_gt, "u61.a71.learn", 1))
    (rec,) = r.settlement.outcome.career.learning
    assert (cents(rec.fee_charged), rec.months_attended) == (0, ())  # 第 1 月付不起学费：这一年没上
    assert series(r.score, "opportunity") == [0.5] * 12
    assert all(comp(r.score, m, "opportunity").growth.g == 0 for m in range(1, 13))
    claim = with_items(
        plan_of(bundle_gt, "u61.a71.nolearn", 1),
        HOME,
        FOOD,
        item(CLERK),
        WASHER,
        rationale="每天下班都在自学会计，已经学会了",
    )
    r = year(bundle_gt, scoring_gt, state_of(bundle_gt, "u61.a71.nolearn"), claim)
    assert series(r.score, "opportunity") == [0.5] * 12


def test_repeat_courses_and_held_abilities_add_no_growth(raw_gt, scoring_gt_raw):
    opps = raw_gt["catalogs"][0]["opportunities"]
    base = next(o for o in opps if o["opportunity_id"] == ACCOUNTING)
    refresher = {**copy.deepcopy(base), "opportunity_id": "a71.training.accounting_refresher"}
    refresher["label"] = "会计复习班（12 个月）"
    refresher["resource"] = {**base["resource"], "hours": 2}  # 195 + 48 + 2.4 = 245.4 ≤ 250
    bootcamp = {**copy.deepcopy(base), "opportunity_id": "a71.training.bootcamp"}
    bootcamp.update(
        label="会计与 Python 双证营（12 个月）", grants=["accounting_cert", "python_programming"]
    )
    bootcamp["resource"] = {**base["resource"], "hours": 10}
    opps += [refresher, bootcamp]
    b = parse_bundle(raw_gt)
    config = parse_scoring_config(scoring_gt_raw)
    (y1, *_) = run_clone(b, config, "u61.a71.learn")
    # ① 已经有会计资格的人再上复习班：参加了，但能力进度已是 1，没有新增成长
    holder = y1.next_state
    p = with_items(
        plan_of(b, "u61.a71.learn", 2), HOME, FOOD, item(CLERK), item(refresher["opportunity_id"])
    )
    r = year(b, config, holder, p)
    assert r.settlement.outcome.career.learning[0].months_attended == tuple(range(1, 13))
    assert series(r.score, "opportunity") == [0.5] * 12
    # ② 同一年两门课都授予会计资格：取最高进度，每月仍只 1/12，不拼成两份
    start = state_of(b, "u61.a71.nolearn")
    two = with_items(
        plan_of(b, "u61.a71.nolearn", 1),
        HOME,
        FOOD,
        item(CLERK),
        WASHER,
        item(ACCOUNTING),
        item(refresher["opportunity_id"]),
    )
    r = year(b, config, start, two)
    assert {comp(r.score, m, "opportunity").growth.g for m in range(1, 13)} == {1 / 12}
    # ③ 一门课明确授予两项能力：按词表里的两项能力算，每月 2/12（能力拆分粒度会影响成长量）
    camp = with_items(
        plan_of(b, "u61.a71.nolearn", 1),
        HOME,
        FOOD,
        item(CLERK),
        WASHER,
        item("a71.training.bootcamp"),
    )
    r = year(b, config, start, camp)
    g = comp(r.score, 1, "opportunity").growth
    assert g.g == pytest.approx(2 / 12, rel=1e-15)
    assert [s.ability_id for s in g.steps] == ["accounting_cert", "python_programming"]
    assert series(r.score, "opportunity")[0] == float(Fraction(4, 6))  # z = 2 + 12 × 2/12 = 4
    # ④ 开局就有的能力（没有课程记录可查）：再上会计课，能力进度仍按 1 算，没有成长
    #    （这一条是变异检查补出来的：①里的持有者另有一条已完成的课程记录，单靠它就让进度为 1）
    initial = revise(
        start,
        abilities=[{"ability_id": "accounting_cert", "source": "initial", "usable_from_year": 1}],
    )
    again = with_items(
        plan_of(b, "u61.a71.nolearn", 1), HOME, FOOD, item(CLERK), WASHER, item(ACCOUNTING)
    )
    r = year(b, config, initial, again)
    assert r.settlement.outcome.career.learning[0].months_attended == tuple(range(1, 13))
    assert series(r.score, "opportunity") == [0.5] * 12


# --------------------------------------------------------------------------
# 5–7：年底的学习不改年初；暂停月没有成长；资格仍下一年生效
# --------------------------------------------------------------------------


def test_late_learning_does_not_change_earlier_months(bundle_gt, scoring_gt):
    start = state_of(bundle_gt, "u61.a71.nolearn")
    late = with_items(
        plan_of(bundle_gt, "u61.a71.nolearn", 1),
        HOME,
        FOOD,
        item(CLERK),
        WASHER,
        item(ACCOUNTING, 10, 12),
    )
    r = year(bundle_gt, scoring_gt, start, late)
    (plain, *_) = run_clone(bundle_gt, scoring_gt, "u61.a71.nolearn")
    assert series(r.score, "opportunity") == [0.5] * 9 + [0.6] * 3
    for m in range(1, 10):
        assert r.score.months[m - 1].model_dump() == plain.score.months[m - 1].model_dump()


def test_a_paused_course_grows_only_in_attended_months(bundle_gt, scoring_gt):
    y1, y2, y3 = run_clone(bundle_gt, scoring_gt, "u61.a71.pause")
    assert series(y1.score, "opportunity") == [0.6] * 6 + [0.5] * 6
    assert series(y2.score, "opportunity") == [0.5] * 12  # 暂停的一年：没有成长分
    assert series(y3.score, "opportunity") == [0.6] * 6 + [0.5] * 6  # 只计新增的 6/12
    fees = [cents(y.settlement.outcome.career.learning[0].fee_charged) for y in (y1, y3)]
    assert fees == [1_200_00, 0]
    step = comp(y3.score, 1, "opportunity").growth.steps[0]
    assert (step.before, step.after) == (0.5, 7 / 12)  # 从第 1 年留下的 6/12 接着长
    held = y3.next_state.abilities[0]
    assert (held.acquired_year, held.acquired_month, held.usable_from_year) == (3, 6, 4)


def test_growth_does_not_unlock_a_job_in_the_same_year(bundle_gt, scoring_gt):
    start = state_of(bundle_gt, "u61.a71.learn")
    early = with_items(
        plan_of(bundle_gt, "u61.a71.learn", 1),
        HOME,
        FOOD,
        item(CLERK, 1, 6),
        item(ACCOUNTANT, 7, 12),
        WASHER,
        item(ACCOUNTING),
    )
    r = settle(bundle_gt, start, early)
    assert r.status == "input_error" and {p.code for p in r.problems} == {"qualification_missing"}
    _, y2, _ = run_clone(bundle_gt, scoring_gt, "u61.a71.learn")
    assert income(y2) == (48000, 7200)  # 第 2 年才做会计


# --------------------------------------------------------------------------
# 8–9：交通按到岗次数可手算；无工作、远程、混合、多岗位都能评
# --------------------------------------------------------------------------

TRANSPORT = {
    # 分身：（到岗次数 D，k，T_eq，S）
    "u63.a71.onsite": (20, Fraction(1), Fraction(30), Fraction(2, 3)),
    "u63.a71.hybrid": (10, Fraction(5, 8), Fraction(75, 4), Fraction(16, 21)),
    "u63.a71.remote": (0, Fraction(1, 4), Fraction(15, 2), Fraction(8, 9)),
    "u63.a71.parttime": (20, Fraction(1), Fraction(30), Fraction(2, 3)),
    "u63.a71.shifts": (40, Fraction(7, 4), Fraction(105, 2), Fraction(8, 15)),
}


@pytest.mark.parametrize(("clone", "expected"), TRANSPORT.items())
def test_transport_by_visits_is_hand_checkable(bundle_gt, scoring_gt, clone, expected):
    visits, k, t_eq, s = expected
    (r, *_) = run_clone(bundle_gt, scoring_gt, clone)
    assert r.score.annual_status == "complete"
    assert series(r.score, "commute") == [float(s)] * 12
    t = comp(r.score, 1, "commute").transport
    assert (t.housing_opportunity_id, t.minutes, t.onsite_visits) == (
        "a71.housing.near",
        30.0,
        visits,
    )
    assert (t.visit_factor, t.equivalent_minutes, t.reference_visits, t.rho) == (
        float(k),
        float(t_eq),
        20,
        0.25,
    )


def test_no_work_and_any_arrangement_is_scored_where_the_old_rule_was_silent(
    bundle_gt, scoring_gt, scoring_e1_5a_raw
):
    old = legacy(bundle_gt, scoring_e1_5a_raw)
    (study, *_) = run_clone(bundle_gt, scoring_gt, "u64.a71.study")  # 第 1 年不工作、专心自学
    assert study.score.annual_status == "complete"
    assert series(study.score, "commute") == [float(Fraction(8, 9))] * 12
    assert series(study.score, "opportunity") == [0.6] * 12
    for clone in (
        "u63.a71.hybrid",
        "u63.a71.remote",
        "u63.a71.parttime",
        "u63.a71.shifts",
        "u64.a71.study",
    ):
        (r, *_) = run_clone(bundle_gt, scoring_gt, clone)
        start = state_of(bundle_gt, clone)
        before = score_with(bundle_gt, old, start, r.settlement, spec=old.spec)
        assert r.score.annual_status == "complete"
        assert before.annual_status == "incomplete_months"  # 旧规则：这些安排的通勤报不支持
        assert all(comp(before, m, "commute").status == "unknown" for m in range(1, 13))
    # 天天到岗一份工作：新旧规则同分（k = 1 时 T_eq = T）
    (onsite, *_) = run_clone(bundle_gt, scoring_gt, "u63.a71.onsite")
    before = score_with(
        bundle_gt, old, state_of(bundle_gt, "u63.a71.onsite"), onsite.settlement, spec=old.spec
    )
    assert series(before, "commute") == series(onsite.score, "commute")


# --------------------------------------------------------------------------
# 10–11：住房降级、没执行的岗位、资料未知；时间与负担账不变
# --------------------------------------------------------------------------


def test_downgrade_unexecuted_jobs_and_unknown_minutes(
    raw_gt, bundle_gt, scoring_gt, scoring_gt_raw
):
    # ① 住房降级：付不起近处，兜底到远处（往返 70 分钟）；交通读实际住的那一处
    broke = revise(
        state_of(bundle_gt, "u63.a71.onsite"), cash={"amount": 0, "numeraire": "USD_2024_LOCAL"}
    )
    plan = with_items(
        plan_of(bundle_gt, "u63.a71.onsite", 1),
        HOME,
        FOOD,
        item(SHOP),
        minimum_fallback=["housing"],
    )
    r = year(bundle_gt, scoring_gt, broke, plan)
    t = comp(r.score, 1, "commute").transport
    assert (t.housing_opportunity_id, t.minutes, t.onsite_visits) == ("a71.housing.far", 70.0, 10)
    assert comp(r.score, 1, "commute").s == float(Fraction(60) / (60 + Fraction(5, 8) * 70))
    exit_month = r.settlement.outcome.exit.month  # 收入不够，后来出局：出局月照旧单列，不补分
    assert (
        r.score.months[exit_month - 1].status == "exit_month" and r.score.annual_status == "exited"
    )
    # ② 只在第 1–6 月执行的岗位：第 7–12 月没有到岗，k = ρ
    rich = revise(
        state_of(bundle_gt, "u63.a71.onsite"), cash={"amount": 20000, "numeraire": "USD_2024_LOCAL"}
    )
    half = with_items(plan_of(bundle_gt, "u63.a71.onsite", 1), HOME, FOOD, item(CLERK, 1, 6))
    r = year(bundle_gt, scoring_gt, rich, half)
    assert [m.onsite_visits for m in r.settlement.outcome.resources.months] == [20] * 6 + [0] * 6
    assert series(r.score, "commute") == [float(Fraction(2, 3))] * 6 + [float(Fraction(8, 9))] * 6
    # ③ 住房分钟缺失：配置检查报缺口；到岗的工作在结算时就是资料错误（时间账不能把通勤补成 0）；
    #    远程工作的结算不需要它，评分器读不到往返分钟就报未知、不补 0
    near = next(
        o
        for o in raw_gt["catalogs"][0]["opportunities"]
        if o["opportunity_id"] == "a71.housing.near"
    )
    del near["spec"]["commute_roundtrip_minutes"]
    b = parse_bundle(raw_gt)
    config = parse_scoring_config(scoring_gt_raw)
    errors, _ = scoring_config_problems(config, b)
    assert any("广义交通要求每个可得住房写明" in e for e in errors)
    onsite_start = state_of(b, "u63.a71.onsite")
    assert settle(b, onsite_start, plan_of(b, "u63.a71.onsite", 1)).status == "data_error"
    (remote, *_) = run_clone(bundle_gt, scoring_gt, "u63.a71.remote")
    start = state_of(bundle_gt, "u63.a71.remote")
    cat = catalog_of(bundle_gt, start)
    home = cat.opportunity("a71.housing.near")
    blank = home.model_copy(
        update={"spec": {k: v for k, v in home.spec.items() if k != "commute_roundtrip_minutes"}}
    )
    cat2 = cat.model_copy(
        update={
            "opportunities": tuple(
                blank if o.opportunity_id == home.opportunity_id else o for o in cat.opportunities
            )
        }
    )
    score = score_year(
        protocol=bundle_gt.protocol,
        spec=scoring_gt.spec,
        conditions=scoring_gt.conditions_for(str(start.city)),
        catalog=cat2,
        state=start,
        outcome=remote.settlement.outcome,
        preference=preference_from_stated_profile(
            next(p for p in bundle_gt.stated_profiles if p.person_id == "u63")
        ),
    )
    c = comp(score, 1, "commute")
    assert c.status == "unknown" and "往返分钟未知不当 0" in c.reasons[0]
    assert score.annual_status == "incomplete_months" and score.annual_mean is None


def test_transport_scoring_leaves_the_time_and_burden_accounts_alone(
    bundle_gt, scoring_gt, bundle5a
):
    hours = {"onsite": 10, "hybrid": 5, "remote": 0, "parttime": 10, "shifts": 20}
    for name, h in hours.items():
        (r, *_) = run_clone(bundle_gt, scoring_gt, f"u63.a71.{name}")
        m1 = r.settlement.outcome.resources.months[0]
        commute = [c for c in m1.contributions if c.category == "commute"]
        assert sum(c.hours.fraction() for c in commute) == h  # Σ 30 分钟 × 到岗次数 / 60
        assert sum(c.burden.fraction() for c in commute) == Fraction(3, 4) * h
    assert max_burden(run_clone(bundle_gt, scoring_gt, "u63.a71.shifts")[0]) == 162.5
    # 同一份天天到岗的生活，在 E1-5A 原协议与新协议下资源账完全相同：评分不改时间账
    old_settle = settle(
        bundle5a, state_of(bundle5a, "u51.a71.nostudy"), plan_of(bundle5a, "u51.a71.nostudy", 1)
    )
    (new, *_) = run_clone(bundle_gt, scoring_gt, "u63.a71.onsite")
    assert old_settle.outcome.resources == new.settlement.outcome.resources


# --------------------------------------------------------------------------
# 12：关闭对照、旧协议复现、政策指纹与跨试算防混用
# --------------------------------------------------------------------------


def test_closed_arms_and_rho_variants_on_the_same_facts(bundle_gt, scoring_gt, scoring_gt_raw):
    (learn, *_) = run_clone(bundle_gt, scoring_gt, "u61.a71.learn")
    start = state_of(bundle_gt, "u61.a71.learn")
    off = parse_scoring_config(arm(scoring_gt_raw, suffix="eta0", eta=0))
    closed = score_with(bundle_gt, off, start, learn.settlement, spec=off.spec)
    assert series(closed, "opportunity") == [0.5] * 12  # η = 0：同样学了，发展维只剩背景
    assert "关闭" in comp(closed, 1, "opportunity").reasons[0]
    (remote, *_) = run_clone(bundle_gt, scoring_gt, "u63.a71.remote")
    (onsite, *_) = run_clone(bundle_gt, scoring_gt, "u63.a71.onsite")
    rstart, ostart = state_of(bundle_gt, "u63.a71.remote"), state_of(bundle_gt, "u63.a71.onsite")
    for rho, remote_s in ((0.0, Fraction(1)), (0.25, Fraction(8, 9)), (0.5, Fraction(4, 5))):
        cfg = parse_scoring_config(arm(scoring_gt_raw, suffix=f"rho{int(rho * 100)}", rho=rho))
        assert (
            series(score_with(bundle_gt, cfg, rstart, remote.settlement, spec=cfg.spec), "commute")
            == [float(remote_s)] * 12
        )
        # 天天到岗（D = D_ref）时 k = 1，与 ρ 无关
        assert (
            series(score_with(bundle_gt, cfg, ostart, onsite.settlement, spec=cfg.spec), "commute")
            == [float(Fraction(2, 3))] * 12
        )


def test_old_protocol_and_policy_identity(bundle_gt, scoring_gt, bundle5a, scoring5a):
    # E1-5A 原协议与原评分：结果与上一轮相同（学习不计分、方案级通勤）
    (old, *_) = run_clone(bundle5a, scoring5a, "u51.a71.study")
    assert old.score.annual_mean == 0.5354166666666667
    assert series(old.score, "opportunity") == [0.5] * 12
    # 新评分版本进入反馈政策的内容指纹：旧评分产生的下一年状态不能接着在新协议里用
    assert (
        bundle5a.protocol.energy_feedback.content_fingerprint
        != bundle_gt.protocol.energy_feedback.content_fingerprint
    )
    r = settle(
        bundle_gt,
        old.next_state,
        plan_of(bundle_gt, "u61.a71.learn", 2),
        protocol=bundle_gt.protocol,
    )
    assert r.status == "input_error"
    assert {p.code for p in r.problems} & {
        "feedback_policy_switched",
        "feedback_policy_content_changed",
    }


def test_learning_trials_cannot_trade_growth(bundle_gt, scoring_gt):
    learn = bound_year(bundle_gt, scoring_gt, "u61.a71.learn")
    plain = bound_year(
        bundle_gt,
        scoring_gt,
        "u61.a71.learn",
        action=with_items(plan_of(bundle_gt, "u61.a71.learn", 1), HOME, FOOD, item(CLERK), WASHER),
    )
    assert learn.score.annual_mean != plain.score.annual_mean  # 学习月的发展分不同
    rec_learn = feedback_record(protocol=bundle_gt.protocol, year=learn)
    with pytest.raises(LifeContractError, match="另一次年度结算的候选状态"):
        apply_feedback(plain.settlement.next_state, rec_learn)
    with pytest.raises(LifeContractError, match="被改动过"):
        feedback_record(
            protocol=bundle_gt.protocol, year=plain.model_copy(update={"score": learn.score})
        )
    again = bound_year(bundle_gt, scoring_gt, "u61.a71.learn")
    assert again.model_dump_json() == learn.model_dump_json()  # 确定性重放


def test_the_versions_gate_growth_and_transport(scoring_gt_raw, bundle_gt, bundle4b):
    old = copy.deepcopy(scoring_gt_raw["spec"])
    old["scoring_version"] = "e1-3-supplement-v1"
    with pytest.raises(ValidationError, match="不读成长项或广义交通"):
        LifeScoringSpec.model_validate(old)
    missing = copy.deepcopy(scoring_gt_raw["spec"])
    commute = next(c for c in missing["components"] if c["component_id"] == "commute")
    del commute["transport"]
    with pytest.raises(ValidationError, match="必须声明广义交通"):
        LifeScoringSpec.model_validate(missing)
    # 协议里要有它读的事实：E1-4B 协议没有职业规则
    data = copy.deepcopy(scoring_gt_raw)
    data["spec"]["protocol_id"] = bundle4b.protocol.protocol_id
    errors, _ = scoring_config_problems(parse_scoring_config(data), bundle4b)
    assert any("没有职业规则" in e for e in errors)
    # 交通读的往返分钟字段必须就是资源账计通勤用的那一个
    data = copy.deepcopy(scoring_gt_raw)
    next(c for c in data["spec"]["components"] if c["component_id"] == "commute")["transport"][
        "minutes_key"
    ] = "floor_area_sqft"
    errors, _ = scoring_config_problems(parse_scoring_config(data), bundle_gt)
    assert any("必须读同一个往返分钟字段" in e for e in errors)


# --------------------------------------------------------------------------
# 13：覆盖报告与评分器一致；新内部参数进不了可见输入
# --------------------------------------------------------------------------


def test_coverage_agrees_with_the_scorer(bundle_gt, scoring_gt, scoring_e1_5a_raw):
    profile = next(p for p in bundle_gt.stated_profiles if p.person_id == "u63")
    pref = preference_from_stated_profile(profile)

    def report(config):
        return coverage_report(
            spec=config.spec,
            group=config.comparison_group,
            conditions=config.city_conditions,
            catalogs=bundle_gt.catalogs,
            preference=pref,
        )

    leaves = {x.leaf: x for x in report(scoring_gt).leaves}
    commute, opportunity = leaves["commute"], leaves["opportunity"]
    assert (commute.support, commute.city_data, commute.action_scope) == (
        "supported",
        "complete",
        "all_arrangements",
    )
    assert commute.action_mechanism and opportunity.action_mechanism
    assert any("广义交通" in r for r in commute.reasons)
    assert any("当月实际新增的能力进度" in r for r in opportunity.reasons)
    # 覆盖说"任何安排都能评"，评分器对五种安排都给出完整年度分
    for clone in TRANSPORT:
        assert run_clone(bundle_gt, scoring_gt, clone)[0].score.annual_status == "complete"
    # 旧规则在同一组城市上：通勤是"看安排"的
    old = {x.leaf: x for x in report(legacy(bundle_gt, scoring_e1_5a_raw)).leaves}
    assert old["commute"].action_scope == "conditional" and not old["opportunity"].action_mechanism


def test_new_internal_parameters_stay_out_of_the_payload(bundle_gt, scoring_gt):
    (y1, *_) = run_clone(bundle_gt, scoring_gt, "u63.a71.remote")
    state = y1.next_state
    obs = build_observation(
        permission=standard_product_permission("e1-5a-gt-test"),
        profile=next(p for p in bundle_gt.stated_profiles if p.person_id == "u63"),
        state=state,
        catalog=catalog_of(bundle_gt, state),
        resource_rules=bundle_gt.protocol.resource_rules,
        energy_feedback=bundle_gt.protocol.energy_feedback,
        career_rules=bundle_gt.protocol.career_rules,
    )
    payload = observation_payload(obs)
    blob = json.dumps(payload, ensure_ascii=False)
    for internal in (
        "rho",
        "reference_visits",
        "visit_factor",
        "equivalent_minutes",
        "progress_rule",
        '"eta"',
        "growth",
    ):
        assert internal not in blob, internal
    remote = next(o for o in payload["opportunities"] if o["opportunity_id"] == REMOTE)
    assert remote["time_energy"]["job"]["onsite_days_per_month"] == 0  # 工作安排是公开事实
    near = next(o for o in payload["opportunities"] if o["opportunity_id"] == "a71.housing.near")
    assert near["spec"]["commute_roundtrip_minutes"] == 30  # 实际住房的往返分钟也是
    for leak in (
        scoring_gt.spec.dimension_component("commute").transport,
        comp(y1.score, 1, "commute").transport,
    ):
        with pytest.raises(LifeContractError, match="出现在可见字段"):
            observation_payload(obs.model_copy(update={"goals": (leak,)}))
    assert cross_validate(bundle_gt).errors == ()


def test_records_from_another_life_are_refused(bundle_gt, scoring_gt):
    """成长与交通只读同一次结算写出的记录：资源账的住房、学习记录的年初进度对不上就拒绝，不拼凑。"""
    (remote, *_) = run_clone(bundle_gt, scoring_gt, "u63.a71.remote")
    start = state_of(bundle_gt, "u63.a71.remote")
    out = remote.settlement.outcome
    months = list(out.resources.months)
    months[0] = months[0].model_copy(update={"housing_opportunity_id": "a71.housing.far"})
    forged = out.model_copy(
        update={"resources": out.resources.model_copy(update={"months": tuple(months)})}
    )
    with pytest.raises(LifeContractError, match="不是同一次生活的记录"):
        score_with(
            bundle_gt, scoring_gt, start, remote.settlement.model_copy(update={"outcome": forged})
        )
    (learn, *_) = run_clone(bundle_gt, scoring_gt, "u61.a71.learn")
    lstart = state_of(bundle_gt, "u61.a71.learn")
    lout = learn.settlement.outcome
    rec = lout.career.learning[0].model_copy(update={"progress_before": 6, "progress_after": 18})
    tampered = lout.model_copy(
        update={"career": lout.career.model_copy(update={"learning": (rec,)})}
    )
    with pytest.raises(LifeContractError, match="不是同一次生活的记录"):
        score_with(
            bundle_gt, scoring_gt, lstart, learn.settlement.model_copy(update={"outcome": tampered})
        )
