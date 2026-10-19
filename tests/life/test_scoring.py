"""E1-3 探索月度评分（`DECISIONS/0018`）：验收 1–14 与手算核对。

每条测试盯一种具体的评错方式：按标价给分、先平均条件再套函数、同一次服务计两次、
没用的年卡算成锻炼、缺数据补零或重新归一化、降级再扣"失望分"、出局月被平均吞掉、
产品评分偷偷借用研究真值、内部分数漏进代理观察……

所有数字来自合成样例（`configs/life/synthetic_e1_3.yaml` 与 `scoring_e1_3.yaml`），
可以手算；它们不是现实城市的幸福估计。评分是浮点，用 1e-12 的紧容差；金额仍是整数分。
"""

from __future__ import annotations

import inspect
import json
import math

import pytest

import econ.life_scoring as life_scoring
import econ.life_settlement as life_settlement
from agentlayer.life_views import (
    ObservationPermission,
    assert_payload_clean,
    build_observation,
    observation_payload,
    visible_feedback,
)
from common.life_contracts import HardRequirement, LifeContractError
from common.life_scoring_contracts import PreferenceInsufficient, ScoringPreference
from econ.life_scoring import preference_from_stated_profile, saturating

from .conftest import opportunity
from .score_support import (
    APPROX,
    component,
    dimension,
    product_pref,
    profile_of,
    research_pref,
    s_series,
    score_of,
    settle_year_of,
)
from .settle_support import bundle_from, cents, plan_of, plus_items, revise, state_of, with_items

USD = "USD_2024_LOCAL"
PRODUCT = dict(
    condition="product_stated_profile",
    show_stated_dimension_points=True,
    show_soft_preferences=True,
    show_hard_requirements=True,
    show_free_text=True,
    show_energy_level=False,
    show_stated_material_points=True,
    permission_version="e1-3-draft",
)


def _u01_year1(bundle3, scoring3, **kw):
    state, r = settle_year_of(bundle3, "u01.a31.c0")
    return state, r, score_of(bundle3, scoring3, state, r, **kw)


def _items(plan):
    return [i.model_dump(mode="json", exclude_none=True) for i in plan.items]


# --------------------------------------------------------------------------
# 手算核对：公式与实现一致
# --------------------------------------------------------------------------


def test_one_month_by_hand_matches_the_implementation(bundle3, scoring3):
    """u01 第 2 月（研究真值）。每一项都按 z = b + Σηx、s = min(c, z/(z+τ)) 手算。"""
    _, _, score = _u01_year1(bundle3, scoring3)
    month = score.months[1]
    assert month.status == "scored"

    hand = {
        "housing_space": (0 + 0.01 * 500) / (0 + 0.01 * 500 + 5),  # 单间 500 平方英尺 → 0.5
        "fresh_food": 3 / (3 + 7),  # 基本方案每周 3 顿 → 0.3
        "workspace": (1 + 3) / (1 + 3 + 1),  # 基础 1 + 共享办公 3 → 0.8
        "durables": 1 / (1 + 1),  # 第 2 月买的电脑当月起用 → 0.5
        "safety": 3 / (3 + 3),
        "healthcare": 4 / (4 + 4),
        "opportunity": 2 / (2 + 2),
        "leisure": 4 / (4 + 12),  # 只有基础 → 0.25
        "environment": 3 / (3 + 3),
        "commute": 2 / (2 + 2),
    }
    for cid, s in hand.items():
        assert component(score, 2, cid).s == pytest.approx(s, **APPROX), cid

    material = 0.4 * 0.5 + 0.1 * 0.3 + 0.3 * 0.8 + 0.2 * 0.5  # = 0.57
    u = (
        0.35 * material
        + 0.15 * 0.5
        + 0.10 * 0.5
        + 0.15 * 0.5
        + 0.05 * 0.25
        + 0.05 * 0.5
        + 0.15 * 0.5
    )
    assert dimension(score, 2, "material").s == pytest.approx(0.57, **APPROX)
    assert month.u == pytest.approx(u, **APPROX)
    assert month.u == pytest.approx(0.512, **APPROX)

    ws = component(score, 2, "workspace")
    (term,) = [t for t in ws.terms if t.counted]
    assert (term.source_id, term.opportunity_id, term.x, term.eta) == (
        "shared_office",
        "a31.service.cowork",
        1.0,
        3.0,
    )
    assert ws.z == pytest.approx(
        ws.base + sum(t.contribution for t in ws.terms if t.counted), **APPROX
    )


def test_the_annual_mean_is_twelve_monthly_scores_averaged_not_averaged_conditions(
    bundle3, scoring3
):
    _, _, score = _u01_year1(bundle3, scoring3)
    us = [m.u for m in score.months]
    assert us == pytest.approx([0.477] + [0.512] * 4 + [0.5195] + [0.493] * 6, **APPROX)
    assert score.annual_status == "complete"
    assert score.annual_mean == pytest.approx(6.0025 / 12, **APPROX)
    assert score.annual_mean == pytest.approx(math.fsum(us) / 12, **APPROX)
    # 反面：先把工作空间"平均成半年有"再套函数，会得到另一个数——那是错的顺序
    monthly_first = (6 * 0.8 + 6 * 0.5) / 12  # 0.65
    averaged_first = (1 + 3 * 0.5) / (1 + 3 * 0.5 + 1)  # 0.714…
    ws_mean = math.fsum(s_series(score, "workspace")) / 12
    assert ws_mean == pytest.approx(monthly_first, **APPROX)
    assert abs(ws_mean - averaged_first) > 0.05


def test_the_saturating_function_has_its_declared_meaning():
    assert saturating(5, 5, 1.0) == (0.5, 0.5, False)  # z = τ 对应 0.5
    raw, s, capped = saturating(50, 5, 0.9)
    assert raw == pytest.approx(50 / 55, **APPROX) and s == 0.9 and capped
    with pytest.raises(LifeContractError, match="负向作用"):
        saturating(-1, 5, 1.0)


# --------------------------------------------------------------------------
# 2. 同样的生活条件不会因为标价更高而得分更高
# --------------------------------------------------------------------------


def test_the_same_home_does_not_score_higher_because_it_costs_more(bundle3, scoring3, raw3):
    state, r = settle_year_of(bundle3, "u02.a31.c0")
    base = score_of(bundle3, scoring3, state, r)
    opportunity(raw3, "a31.housing.studio")["price"]["amount"]["amount"] = 1300  # 同一套房，贵 400
    dear = bundle_from(raw3)
    state2, r2 = settle_year_of(dear, "u02.a31.c0")
    assert cents(r2.outcome.cash_end) == cents(r.outcome.cash_end) - 12 * 400_00
    scored = score_of(dear, scoring3, state2, r2)
    assert [m.model_dump() for m in scored.months] == [m.model_dump() for m in base.months]
    assert scored.annual_mean == base.annual_mean


def test_more_income_with_the_same_consumption_scores_the_same(bundle3, scoring3, raw3):
    """钱决定买得起什么；工资和余额本身不发满足感。"""
    state, r = settle_year_of(bundle3, "u02.a31.c0")
    base = score_of(bundle3, scoring3, state, r)
    opportunity(raw3, "a31.job.office")["compensation"]["amount"]["amount"] = 60000
    richer = bundle_from(raw3)
    state2, r2 = settle_year_of(richer, "u02.a31.c0")
    assert cents(r2.outcome.cash_end) > cents(r.outcome.cash_end)
    assert score_of(richer, scoring3, state2, r2).annual_mean == base.annual_mean


def test_the_same_studio_in_two_cities_gets_the_same_space_score(bundle3, scoring3):
    """A31 单间 900、B32 单间 1,000，规格同为 500 平方英尺：面积分一样（都没碰到上限）。"""
    sa, ra = settle_year_of(bundle3, "u02.a31.c0")
    sb, rb = settle_year_of(bundle3, "u02.b32.c0")
    a = score_of(bundle3, scoring3, sa, ra)
    b = score_of(bundle3, scoring3, sb, rb)
    assert s_series(a, "housing_space") == s_series(b, "housing_space") == [0.5] * 12


# --------------------------------------------------------------------------
# 3. 同一份生活记录，不同的明确偏好 → 不同的分项贡献
# --------------------------------------------------------------------------


def test_one_life_two_explicit_preferences_give_different_contributions(bundle3, scoring3):
    state, r = settle_year_of(bundle3, "u02.a31.c0")
    research = score_of(bundle3, scoring3, state, r, preference=research_pref(bundle3, "u02"))
    stated = product_pref(bundle3, "u02")
    assert isinstance(stated, ScoringPreference)
    product = score_of(bundle3, scoring3, state, r, preference=stated)

    # 生活条件本身一样
    for cid in ("housing_space", "fresh_food", "workspace", "leisure"):
        assert s_series(research, cid) == s_series(product, cid)

    # 新鲜食品的贡献：研究 0.28 × 0.60 × 0.75，产品 0.25 × 0.40 × 0.75
    def fresh_contribution(score):
        mat = dimension(score, 1, "material")
        (fresh,) = [t for t in mat.material_terms if t.need == "fresh_food"]
        return mat.weight * fresh.contribution

    assert fresh_contribution(research) == pytest.approx(0.126, **APPROX)
    assert fresh_contribution(product) == pytest.approx(0.075, **APPROX)
    assert research.annual_mean == pytest.approx(0.4695, **APPROX)
    assert product.annual_mean == pytest.approx(0.4625, **APPROX)
    assert (research.preference_source, product.preference_source) == (
        "research_synthetic_truth",
        "product_stated_numeric",
    )


# --------------------------------------------------------------------------
# 4. 收益递减，并遵守上限；5. 没有消费改善时，基础条件照样贡献
# --------------------------------------------------------------------------


def test_improvements_have_diminishing_returns(bundle3, scoring3):
    def leisure_with_gyms(n):
        plan = plan_of(bundle3, "u02.a31.c0")
        if n:
            plan = plus_items(
                plan, {"opportunity_id": "a31.service.gym_plan", "quantity": n, "start_month": 1}
            )
        state, r = settle_year_of(bundle3, "u02.a31.c0", plan=plan)
        return component(score_of(bundle3, scoring3, state, r), 1, "leisure").s

    none, one, two = leisure_with_gyms(0), leisure_with_gyms(1), leisure_with_gyms(2)
    assert (none, one, two) == pytest.approx((4 / 16, 12 / 24, 20 / 32), **APPROX)
    assert one - none > two - one > 0, "前 8 次锻炼加 0.25，再加 8 次只加 0.125"


def test_the_declared_cap_binds(bundle3, scoring3, raw3):
    opportunity(raw3, "a31.housing.studio")["spec"]["floor_area_sqft"] = 5000
    b = bundle_from(raw3)
    state, r = settle_year_of(b, "u02.a31.c0")
    c = component(score_of(b, scoring3, state, r), 1, "housing_space")
    assert c.raw == pytest.approx(50 / 55, **APPROX) and c.s == 0.9 and c.capped


def test_city_base_conditions_count_without_any_purchase(bundle3, scoring3):
    """u02 在 A31 没买任何改善：安全等五维照样 0.5，工作空间有基础 0.5，休闲基础 0.25。"""
    state, r = settle_year_of(bundle3, "u02.a31.c0")
    score = score_of(bundle3, scoring3, state, r)
    for cid in ("safety", "healthcare", "opportunity", "environment", "commute"):
        c = component(score, 5, cid)
        assert c.s == pytest.approx(0.5, **APPROX) and c.base_only and not c.terms
        assert "当前没有行动改善机制" in c.reasons[0]
    assert component(score, 5, "workspace").s == pytest.approx(0.5, **APPROX)
    assert component(score, 5, "leisure").s == pytest.approx(0.25, **APPROX)
    sb, rb = settle_year_of(bundle3, "u02.b32.c0")
    assert component(score_of(bundle3, scoring3, sb, rb), 5, "workspace").s == 0.0  # B32 基础为 0


# --------------------------------------------------------------------------
# 6. 半年服务只影响那六个月；7. 被拒的不计、已有的继续、服务不重复计
# --------------------------------------------------------------------------


def test_a_six_month_service_only_affects_those_six_months(bundle3, scoring3):
    _, _, score = _u01_year1(bundle3, scoring3)
    assert s_series(score, "workspace") == pytest.approx([0.8] * 6 + [0.5] * 6, **APPROX)
    for m in range(1, 13):
        used = [t for t in component(score, m, "workspace").terms if t.counted]
        assert bool(used) == (m <= 6)


def test_a_rejected_purchase_brings_nothing(bundle3, scoring3):
    plan = plan_of(bundle3, "u01.a31.c0")
    items = _items(plan)
    items[4] = {**items[4], "month": 1}  # 电脑挪到第 1 月：钱不够，被拒
    state, r = settle_year_of(bundle3, "u01.a31.c0", plan=with_items(plan, *items))
    assert (
        next(i for i in r.outcome.items if i.opportunity_id == "a31.durable.computer").status
        == "rejected"
    )
    assert s_series(score_of(bundle3, scoring3, state, r), "durables") == [0.0] * 12


def test_items_held_from_last_year_keep_contributing(bundle3, scoring3):
    _, r1 = settle_year_of(bundle3, "u01.a31.c0")
    s2, r2 = settle_year_of(bundle3, "u01.a31.c0", year=2, state=r1.next_state)
    score = score_of(bundle3, scoring3, s2, r2)
    assert s_series(score, "durables") == pytest.approx([0.5] * 12, **APPROX)  # 去年买的电脑
    assert s_series(score, "leisure") == pytest.approx(
        [0.5] * 6 + [0.25] * 6, **APPROX
    )  # 年卡到第 6 月
    assert score.annual_mean == pytest.approx((6 * 0.493 + 6 * 0.4805) / 12, **APPROX)


def test_a_prepaid_service_is_counted_once_not_from_both_records(bundle3, scoring3):
    """年付服务的覆盖月同时出现在 ItemExecution 与 HoldingService 里；评分只读后者。"""
    _, r, score = _u01_year1(bundle3, scoring3)
    gym_line = next(i for i in r.outcome.items if i.opportunity_id == "a31.service.gym_plan")
    gym_held = next(
        h for h in r.outcome.holdings_service if h.opportunity_id == "a31.service.gym_plan"
    )
    assert gym_line.covered_months == gym_held.covered_months == (7, 8, 9, 10, 11, 12)
    leisure = component(score, 7, "leisure")
    (term,) = leisure.terms
    assert (term.record, term.x) == ("prepaid_service", 8.0), "8 次，不是 16 次"
    assert leisure.s == pytest.approx(12 / 24, **APPROX)


def test_the_purchase_itself_is_not_rewarded_only_the_months_of_use(bundle3, scoring3):
    """电脑成交那一下不计分；从第 2 月起每个在用的月份计一次。"""
    _, _, score = _u01_year1(bundle3, scoring3)
    assert s_series(score, "durables") == pytest.approx([0.0] + [0.5] * 11, **APPROX)
    records = {t.record for m in range(1, 13) for t in component(score, m, "durables").terms}
    assert records == {"durable_good"}


def test_a_card_without_a_usage_plan_is_not_read_as_exercise(bundle3, scoring3):
    plan = plan_of(bundle3, "u01.a31.c0")
    items = [
        {**i, "opportunity_id": "a31.service.gym_card"}
        if i["opportunity_id"] == "a31.service.gym_plan"
        else i
        for i in _items(plan)
    ]
    state, r = settle_year_of(bundle3, "u01.a31.c0", plan=with_items(plan, *items))
    score = score_of(bundle3, scoring3, state, r)
    assert s_series(score, "leisure")[6:] == pytest.approx([0.25] * 6, **APPROX)
    assert any("a31.service.gym_card" in n for n in score.notes)


def test_two_substitutes_for_one_need_do_not_double_count(bundle3, scoring3):
    """两居室自带工作间、又租了共享办公：同一需求只计一次。"""
    plan = plan_of(bundle3, "u02.a31.c0")
    items = _items(plan)
    items[0] = {**items[0], "opportunity_id": "a31.housing.two_bed"}
    plan = plus_items(
        with_items(plan, *items),
        {"opportunity_id": "a31.service.cowork", "quantity": 1, "start_month": 1, "end_month": 12},
    )
    state, r = settle_year_of(bundle3, "u02.a31.c0", plan=plan)
    ws = component(score_of(bundle3, scoring3, state, r), 1, "workspace")
    assert sum(1 for t in ws.terms if t.x == 1.0) == 2
    assert sum(1 for t in ws.terms if t.counted) == 1
    assert ws.s == pytest.approx(0.8, **APPROX)  # 1 + 3，不是 1 + 6
    assert any("只计一次" in x for x in ws.reasons)


# --------------------------------------------------------------------------
# 8. 硬要求：全年满足、部分满足、无法判断分得清
# --------------------------------------------------------------------------


def test_hard_requirements_distinguish_full_partial_and_unknown(bundle3, scoring3, raw3):
    sa, ra = settle_year_of(bundle3, "u02.a31.c0")
    (fresh,) = score_of(bundle3, scoring3, sa, ra).hard_requirements
    assert fresh.summary == "satisfied_every_month" and len(fresh.months_satisfied) == 12

    _, _, u01 = _u01_year1(bundle3, scoring3)
    (ws,) = u01.hard_requirements
    assert ws.summary == "partially_satisfied"
    assert ws.months_satisfied == (1, 2, 3, 4, 5, 6) and ws.months_not_satisfied == (
        7,
        8,
        9,
        10,
        11,
        12,
    )
    assert ws.evidence[0].items == ("a31.service.cowork",)

    del opportunity(raw3, "a31.housing.studio")["spec"]["home_office"]  # 单间是否有工作间：没写
    b = bundle_from(raw3)
    s, r = settle_year_of(b, "u01.a31.c0")
    (unknown,) = score_of(b, scoring3, s, r).hard_requirements
    assert unknown.summary == "undetermined"
    assert unknown.months_unknown == (7, 8, 9, 10, 11, 12)
    assert "规格缺少要比较的键" in unknown.evidence[6].reason


def test_a_hard_requirement_does_not_change_the_score(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u01.a31.c0")
    with_req = score_of(bundle3, scoring3, s, r)
    without = score_of(bundle3, scoring3, s, r, hard=())
    assert [m.u for m in with_req.months] == [m.u for m in without.months]


def test_a_requirement_without_a_category_is_not_guessed(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u01.a31.c0")
    vague = HardRequirement(need="workspace", requirement={"home_office": True})
    (res,) = score_of(bundle3, scoring3, s, r, hard=(vague,)).hard_requirements
    assert res.summary == "undetermined" and "category" in res.evidence[0].reason


# --------------------------------------------------------------------------
# 9. 缺数据不补零、不逐城重新归一化、不生成伪完整总分
# --------------------------------------------------------------------------


def test_missing_city_data_is_not_zero_and_is_not_renormalised(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u02.b32.c0")
    score = score_of(bundle3, scoring3, s, r)
    assert score.annual_status == "incomplete_months" and score.annual_mean is None
    assert score.months_incomplete == tuple(range(1, 13))
    m1 = score.months[0]
    assert m1.status == "incomplete" and m1.u is None
    env = component(score, 1, "environment")
    assert env.status == "unknown" and env.s is None and "故意留空" in env.reasons[0]
    env_dim = dimension(score, 1, "environment")
    assert env_dim.weight == pytest.approx(0.15, **APPROX) and env_dim.status == "unknown"
    # 其余分项照常给出，权重原样保留（没有把 0.15 摊给别的维度）
    assert component(score, 1, "fresh_food").s == pytest.approx(0.75, **APPROX)
    assert dimension(score, 1, "safety").weight == pytest.approx(0.10, **APPROX)


def test_a_zero_weight_gap_does_not_block_the_known_scores(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u02.b32.c0")
    weights = dict(research_pref(bundle3, "u02").dimension_weights)
    weights["leisure"] += weights["environment"]
    weights["environment"] = 0.0
    pref = ScoringPreference(
        person_id="u02",
        source="research_synthetic_truth",
        dimension_weights=weights,
        material_weights=research_pref(bundle3, "u02").material_weights,
        derivation="测试用：环境权重为 0 的合成偏好",
    )
    score = score_of(bundle3, scoring3, s, r, preference=pref)
    assert score.annual_status == "complete" and score.annual_mean is not None
    assert dimension(score, 1, "environment").status == "not_weighted_unknown"


def test_missing_item_data_leaves_the_component_unknown(bundle3, scoring3, raw3):
    del opportunity(raw3, "a31.housing.studio")["spec"]["floor_area_sqft"]
    b = bundle_from(raw3)
    s, r = settle_year_of(b, "u02.a31.c0")
    score = score_of(b, scoring3, s, r)
    c = component(score, 1, "housing_space")
    assert c.status == "unknown" and "缺 floor_area_sqft" in c.reasons[0]
    assert score.months[0].status == "incomplete" and score.annual_mean is None


def test_a_weighted_need_with_no_component_makes_the_month_incomplete(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u02.a31.c0")
    base = research_pref(bundle3, "u02")
    pref = base.model_copy(
        update={"material_weights": {"housing_space": 0.5, "convenience_food": 0.5}}
    )
    pref = ScoringPreference.model_validate(pref.model_dump())
    score = score_of(bundle3, scoring3, s, r, preference=pref)
    assert score.months[0].status == "incomplete"
    assert any("没有评分分项" in x for x in score.months[0].reasons)


# --------------------------------------------------------------------------
# 10. 降级按实际方案评分；11. 出局当月与之后单列
# --------------------------------------------------------------------------


def test_a_downgraded_month_is_scored_by_the_home_actually_lived_in(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u03.a31.c0")
    score = score_of(bundle3, scoring3, s, r)
    assert s_series(score, "housing_space")[:2] == pytest.approx([0.75, 0.5], **APPROX)
    (term,) = component(score, 2, "housing_space").terms
    assert term.opportunity_id == "a31.housing.studio"  # 兜底那一处
    # 第 2 月 = 实际条件的分数，没有额外的"失望分"
    hand = 0.4 * (0.5 * 0.5 + 0.3 * 0.3 + 0.0 * 0.5 + 0.2 * 0.0) + 0.1 * (
        0.5 + 0.5 + 0.5 + 0.25 + 0.5 + 0.5
    )
    assert score.months[1].u == pytest.approx(hand, **APPROX)
    assert score.months[1].u == pytest.approx(0.411, **APPROX)


def test_the_exit_month_and_after_keep_their_own_status(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u03.a31.c0")
    score = score_of(bundle3, scoring3, s, r)
    assert [m.status for m in score.months] == ["scored", "scored", "exit_month"] + [
        "after_exit"
    ] * 9
    assert all(m.u is None and not m.components for m in score.months[2:])
    assert score.annual_status == "exited" and score.annual_mean is None
    assert score.months_exit == (3,) and score.months_after_exit == tuple(range(4, 13))
    assert [m.u for m in score.months[:2]] == pytest.approx([0.461, 0.411], **APPROX)
    nan_skipping_mean = (0.461 + 0.411) / 2
    assert score.annual_mean != pytest.approx(nan_skipping_mean), "出局年份不能被忽略空值的平均吞掉"


def test_a_requirement_in_an_exit_year_is_only_judged_on_lived_months(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u03.a31.c0")
    req = HardRequirement(
        need="fresh_food", requirement={"category": "food", "min_fresh_meals_per_week": 1}
    )
    (res,) = score_of(bundle3, scoring3, s, r, hard=(req,)).hard_requirements
    assert res.summary == "satisfied_in_lived_months"
    assert res.months_satisfied == (1, 2) and res.months_not_lived == tuple(range(3, 13))


# --------------------------------------------------------------------------
# 12. 改理由不变；确定性重放；不改输入
# --------------------------------------------------------------------------


def test_rationale_replay_and_inputs(bundle3, scoring3):
    plan = plan_of(bundle3, "u01.a31.c0")
    other = revise(plan, rationale="完全不同的理由：我只想省钱。")
    s, r = settle_year_of(bundle3, "u01.a31.c0", plan=plan)
    s2, r2 = settle_year_of(bundle3, "u01.a31.c0", plan=other)
    before = (s.model_dump(), r.outcome.model_dump(), scoring3.model_dump())
    a = score_of(bundle3, scoring3, s, r)
    b = score_of(bundle3, scoring3, s2, r2)
    c = score_of(bundle3, scoring3, s, r)
    assert a.model_dump() == b.model_dump() == c.model_dump()
    assert (s.model_dump(), r.outcome.model_dump(), scoring3.model_dump()) == before


def test_the_scorer_does_not_read_plans_rationales_or_the_old_utility():
    params = set(inspect.signature(life_scoring.score_year).parameters)
    assert params == {
        "protocol",
        "spec",
        "conditions",
        "catalog",
        "state",
        "outcome",
        "preference",
        "hard_requirements",
    }
    src = inspect.getsource(life_scoring)
    for name in ("YearAction", "rationale", "econ.utility", "econ.core", "indirect_utility"):
        assert name not in src.replace("econ.utility``", ""), name


def test_settlement_does_not_depend_on_the_scorer():
    assert "life_scoring" not in inspect.getsource(life_settlement)


# --------------------------------------------------------------------------
# 13. 产品评分不借用研究真值；信息不足就明说
# --------------------------------------------------------------------------


def test_product_scoring_never_borrows_research_truth(bundle3):
    insufficient = product_pref(bundle3, "u01")
    assert isinstance(insufficient, PreferenceInsufficient)
    assert insufficient.missing == ("material_points",)
    assert "不补均匀权重" in insufficient.message

    stated = product_pref(bundle3, "u02")
    assert stated.dimension_weights["material"] == pytest.approx(0.25, **APPROX)  # 不是真值 0.28
    assert stated.material_weights == pytest.approx(
        {"housing_space": 0.3, "fresh_food": 0.4, "workspace": 0.1, "durables": 0.2}, **APPROX
    )
    assert set(inspect.signature(preference_from_stated_profile).parameters) == {"profile"}


def test_ordinal_preferences_are_never_turned_into_numbers(bundle3):
    profile = profile_of(bundle3, "u02")
    flipped = revise(
        profile,
        soft_preferences=[
            {"need": "fresh_food", "stated_strength": "low"},
            {"need": "housing_space", "stated_strength": "high"},
        ],
        free_text="其实我最在乎住得大。",
    )
    assert preference_from_stated_profile(flipped) == preference_from_stated_profile(profile)


def test_product_reports_say_whose_preferences_they_use(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u02.a31.c0")
    product = score_of(bundle3, scoring3, s, r, preference=product_pref(bundle3, "u02"))
    assert "按你提供的偏好" in product.explanation and "不是你真实的幸福权重" in product.explanation
    assert product.formal_scoring_ready is False and product.status == "exploratory_synthetic"


# --------------------------------------------------------------------------
# 14. 新的内部评分不能进入代理观察或事实反馈
# --------------------------------------------------------------------------


def test_internal_scores_never_reach_observations_or_feedback(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u02.a31.c0")
    score = score_of(bundle3, scoring3, s, r)
    pref = research_pref(bundle3, "u02")
    # 每一种内部对象都要**自己**被拦下（报错点名的就是它），不能只靠它里面嵌套的别的类型。
    for smuggled in (
        score,
        score.months[0],
        score.months[0].dimensions[0],
        score.months[0].components[0],
        score.hard_requirements[0],
        pref,
        scoring3.spec,
        scoring3.city_conditions[0],
        scoring3.city_conditions[0].components[0],
    ):
        with pytest.raises(LifeContractError, match=f"^{type(smuggled).__name__} 出现在"):
            assert_payload_clean({"opportunities": [{"spec": {"deep": [smuggled]}}]})

    obs = build_observation(
        permission=ObservationPermission(**PRODUCT),
        profile=profile_of(bundle3, "u02"),
        state=r.next_state,
        catalog=bundle3.catalog_for(str(s.city)),
        last_feedback=visible_feedback(r),
    )
    blob = json.dumps(observation_payload(obs), ensure_ascii=False)
    for key in ("annual_mean", "material_weights", "eta_any", "tau", "scoring_version", "0.4695"):
        assert key not in blob


def test_the_users_own_material_points_stay_visible_when_permitted(bundle3):
    """内部评分不可见，不等于用户自己说的话也不可见。研究文字条件下才隐藏。"""
    s = state_of(bundle3, "u02.a31.c0")
    common = dict(
        profile=profile_of(bundle3, "u02"), state=s, catalog=bundle3.catalog_for(str(s.city))
    )
    shown = build_observation(permission=ObservationPermission(**PRODUCT), **common)
    assert shown.profile.material_points == {
        "housing_space": 30,
        "fresh_food": 40,
        "workspace": 10,
        "durables": 20,
    }
    research = ObservationPermission(
        **{
            **PRODUCT,
            "condition": "research_text_only",
            "show_stated_dimension_points": False,
            "show_soft_preferences": False,
            "show_stated_material_points": False,
        }
    )
    hidden = build_observation(permission=research, **common)
    assert hidden.profile.material_points is None
    with pytest.raises(ValueError, match="物质细项点数"):
        ObservationPermission(
            **{
                **PRODUCT,
                "condition": "research_text_only",
                "show_stated_dimension_points": False,
                "show_soft_preferences": False,
            }
        )


# --------------------------------------------------------------------------
# 一致性：不能拿另一座城、另一个人、另一年的记录来配
# --------------------------------------------------------------------------


def test_records_from_another_city_person_or_year_are_refused(bundle3, scoring3):
    sb, rb = settle_year_of(bundle3, "u02.b32.c0")
    with pytest.raises(LifeContractError, match="城市对不上"):
        score_of(
            bundle3,
            scoring3,
            sb,
            rb,
            conditions=scoring3.conditions_for("US:SYNTH2026:A31"),
            catalog=bundle3.catalog_for("US:SYNTH2026:A31"),
        )
    with pytest.raises(LifeContractError, match="偏好属于"):
        score_of(bundle3, scoring3, sb, rb, preference=research_pref(bundle3, "u01"))
    _, r1 = settle_year_of(bundle3, "u01.a31.c0")
    s2, _ = settle_year_of(bundle3, "u01.a31.c0", year=2, state=r1.next_state)
    with pytest.raises(LifeContractError, match="不是同一人同一年"):
        score_of(bundle3, scoring3, s2, r1)


def test_scoring_reads_the_outcome_but_never_changes_it(bundle3, scoring3):
    s, r = settle_year_of(bundle3, "u01.a31.c0")
    before = r.model_dump()
    score_of(bundle3, scoring3, s, r)
    assert r.model_dump() == before
