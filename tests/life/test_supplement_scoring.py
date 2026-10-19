"""E1-3 增补的评分规则（`DECISIONS/0019` 第三、七、八节）：功能计分、只改标价、可行安排、
自然景观、租金—通勤。

每条测试盯一种具体的评错方式：第二部手机再加一份满额、便宜无用的东西按件刷分、容量缺失
当成零、贵的同款得分更高、景观缺失当零或重复计分、远处租金配近处通勤、降级后通勤不跟着变、
没有工作或远程也照算通勤、通勤分钟越多分数越高……

数字来自合成样例（`configs/life/synthetic_e1_3s.yaml` 与 `scoring_e1_3s.yaml`），可以手算；
不是现实城市的幸福估计。评分是浮点，用 1e-12 的紧容差；金额是整数分。
"""

from __future__ import annotations

import itertools

import pytest

from common.life_contracts import LifeContractError
from common.life_scoring_contracts import (
    ComponentSource,
    NatureCondition,
    ScoringComponent,
    ScoringPreference,
)
from common.money import Money
from ledger.life_catalog import cross_validate
from ledger.life_scoring_config import check_scoring_config, parse_scoring_config

from .conftest import catalog, opportunity
from .score_support import (
    APPROX,
    component,
    research_pref,
    s_series,
    score_of,
    settle_year_of,
)
from .settle_support import bundle_from, cents, plan_of, plus_items, revise, state_of, with_items

USD = "USD_2024_LOCAL"
UNSUPPORTED = "方案级通勤评分不支持/信息不足："
COMPONENTS = (
    "housing_space",
    "fresh_food",
    "workspace",
    "communication",
    "work_device",
    "laundry",
    "storage",
    "safety",
    "healthcare",
    "opportunity",
    "leisure",
    "environment",
    "commute",
)


def _rich(bundle, clone: str, cash: float = 30000.0):
    """同一个分身的年初状态，现金放宽：比较的是评分，不让"买不起"混进来。"""
    return revise(state_of(bundle, clone), cash=Money(cash, USD))


def _all_s(score) -> dict[str, list[float | None]]:
    return {cid: s_series(score, cid) for cid in COMPONENTS}


def _config(raw_scoring: dict, bundle):
    """改过的评分配置：先过结构解析，再过跨对象检查。返回（配置，检查报告）。"""
    config = parse_scoring_config(raw_scoring)
    return config, check_scoring_config(config, bundle)


def _condition(raw_scoring: dict, code: str, component_id: str) -> dict:
    cc = next(c for c in raw_scoring["city_conditions"] if c["city"]["code"] == code)
    return next(x for x in cc["components"] if x["component_id"] == component_id)


def _spec_component(raw_scoring: dict, component_id: str) -> dict:
    return next(c for c in raw_scoring["spec"]["components"] if c["component_id"] == component_id)


# --------------------------------------------------------------------------
# 功能计分：存在型用 any，容量型只按声明的容量相加
# --------------------------------------------------------------------------


def test_a_second_phone_adds_no_second_share_of_communication(bundle3s, scoring3s):
    s, r = settle_year_of(bundle3s, "u11.a41.c0")
    score = score_of(bundle3s, scoring3s, s, r)
    one = component(score, 2, "communication")
    two = component(score, 3, "communication")
    assert [(t.opportunity_id, t.counted) for t in one.terms] == [("a41.durable.phone", True)]
    assert [(t.opportunity_id, t.counted) for t in two.terms] == [
        ("a41.durable.phone", True),
        ("a41.durable.phone_premium", False),
    ]
    assert one.s == two.s == pytest.approx(3 / (3 + 1), **APPROX)
    assert "any：同一需求多处满足只计一次" in two.reasons
    # 功能按声明认：第 1 月只有手机，手机不是工作设备，也不是洗衣条件
    first = {cid: component(score, 1, cid) for cid in ("work_device", "laundry")}
    assert all((c.status, c.s, c.terms) == ("known", 0.0, ()) for c in first.values())


def test_more_useless_or_duplicate_items_never_raise_any_score(bundle3s, scoring3s):
    """五个小摆件、三部高端手机：钱花得更多，每个分项每个月的得分都不变。"""
    s = _rich(bundle3s, "u11.a41.c0")
    base_plan = plan_of(bundle3s, "u11.a41.c0")
    items = []
    for it in base_plan.model_dump(mode="json")["items"]:
        if it["opportunity_id"] == "a41.durable.gadget":
            it["quantity"] = 5
        if it["opportunity_id"] == "a41.durable.phone_premium":
            it["quantity"] = 3
        items.append(it)
    more = with_items(base_plan, *items)
    _, r0 = settle_year_of(bundle3s, "u11.a41.c0", plan=base_plan, state=s)
    _, r1 = settle_year_of(bundle3s, "u11.a41.c0", plan=more, state=s)
    assert _all_s(score_of(bundle3s, scoring3s, s, r1)) == _all_s(
        score_of(bundle3s, scoring3s, s, r0)
    )
    # 多花的钱：4 个摆件 × 150 + 2 部高端手机 × 1,200
    assert cents(r0.outcome.cash_end) - cents(r1.outcome.cash_end) == cents(4 * 150 + 2 * 1200)


def test_capacity_adds_up_only_through_the_declared_capacity(bundle3s, scoring3s, raw3s):
    s = _rich(bundle3s, "u11.a41.c0")
    plan = plan_of(bundle3s, "u11.a41.c0")
    got = []
    for n in (1, 2, 3):
        items = [
            {**it, "quantity": n} if it["opportunity_id"] == "a41.durable.cabinet" else it
            for it in plan.model_dump(mode="json")["items"]
        ]
        _, r = settle_year_of(bundle3s, "u11.a41.c0", plan=with_items(plan, *items), state=s)
        got.append(component(score_of(bundle3s, scoring3s, s, r), 5, "storage").s)
    # 200 / 400 / 600 升 → 2/4、4/6、6/8：容量相加，满足感递减
    assert got == pytest.approx([2 / 4, 4 / 6, 6 / 8], **APPROX)

    # 容量型物品没写容量：数据未知，不是零
    del opportunity(raw3s, "a41.durable.cabinet")["spec"]["storage_liters"]
    b = bundle_from(raw3s)
    s2, r2 = settle_year_of(b, "u11.a41.c0")
    score = score_of(b, scoring3s, s2, r2)
    st = component(score, 5, "storage")
    assert st.status == "unknown" and st.s is None
    assert any("storage_liters" in x for x in st.reasons)
    assert score.months[4].status == "incomplete" and score.annual_mean is None


def test_function_sources_cannot_be_summed_by_count():
    def src(value: str, **kw) -> ComponentSource:
        return ComponentSource(
            source_id="x",
            records=("durable_good",),
            functions=("communication",),
            value=value,
            unit="件",
            note="测试",
            **kw,
        )

    def comp(source: ComponentSource, combine: str) -> ScoringComponent:
        return ScoringComponent(
            component_id="communication",
            dimension="material",
            material_need="communication",
            unit="有/无",
            tau=1,
            combine=combine,
            sources=(source,),
            description="测试",
        )

    for value in ("present", "quantity"):
        with pytest.raises(ValueError, match="不能相加"):
            comp(src(value), "sum")
    comp(src("present"), "any")
    comp(src("present"), "max")
    # 读明确容量规格的来源可以相加：那是声明过的容量语义
    comp(src("spec_number", spec_key="storage_liters", per_unit=True), "sum")


def test_functions_and_attributes_must_be_declared_and_kept_apart(raw3s):
    opportunity(raw3s, "a41.durable.phone")["functions"] = ["communication", "teleport"]
    opportunity(raw3s, "a41.durable.laptop")["attributes"]["warranty"] = "3y"
    errors = cross_validate(bundle_from(raw3s)).errors
    assert any("teleport" in e and "functions" in e for e in errors)
    assert any("warranty" in e and "attribute_keys" in e for e in errors)

    clash = opportunity(raw3s, "a41.durable.cabinet")
    clash["attributes"] = {"brand": "x"}
    clash["spec"]["brand"] = "x"
    with pytest.raises(LifeContractError, match="同时出现在 spec 与 attributes"):
        bundle_from(raw3s)


def test_attributes_are_listed_as_unevaluated_and_never_change_the_score(
    raw3s, bundle3s, scoring3s
):
    s0, r0 = settle_year_of(bundle3s, "u11.a41.c0")
    base = score_of(bundle3s, scoring3s, s0, r0)
    opportunity(raw3s, "a41.durable.phone")["attributes"] = {
        "brand": "other_phone",
        "tier": "premium",
    }
    opportunity(raw3s, "a41.food.fresh")["attributes"] = {
        "prep_mode": "self_cook",
        "cuisine": "chinese",
    }
    b = bundle_from(raw3s)
    s1, r1 = settle_year_of(b, "u11.a41.c0")
    changed = score_of(b, scoring3s, s1, r1)
    assert _all_s(changed) == _all_s(base)
    assert base.unevaluated_attributes == ("brand", "prep_mode", "tier")
    assert changed.unevaluated_attributes == ("brand", "cuisine", "prep_mode", "tier")
    assert any("未评价" in n and "不从价格推断" in n for n in changed.notes)


def test_workspace_substitutes_count_once_and_no_free_office_is_invented(
    bundle3s, scoring3s, raw3s
):
    # B42 确定没有共享办公：工作空间是已知的 0（b = 0），不是缺资料
    s, r = settle_year_of(bundle3s, "u11.b42.c0")
    ws = component(score_of(bundle3s, scoring3s, s, r), 6, "workspace")
    assert (ws.status, ws.s, ws.base) == ("known", 0.0, 0.0)

    # 住宅自带工作间 + 共享工位：同一个月只计一次
    opportunity(raw3s, "a41.housing.near")["spec"]["home_office"] = True
    b = bundle_from(raw3s)
    s2, r2 = settle_year_of(b, "u11.a41.c0")
    both = component(score_of(b, scoring3s, s2, r2), 1, "workspace")
    assert [(t.source_id, t.x, t.counted) for t in both.terms] == [
        ("home_office", 1.0, True),
        ("external_desk", 1.0, False),
    ]
    assert both.s == pytest.approx(3 / (3 + 1), **APPROX)


def test_ready_meals_save_time_but_score_only_their_fresh_meals(bundle3s, scoring3s):
    """现成餐每周 7 顿新鲜餐、准备时间少：鲜食 = 7/(7+7)；省下的时间是事实，不另给一份满足感。"""
    s = _rich(bundle3s, "u12.a41.c0")
    plan = plan_of(bundle3s, "u12.a41.c0")
    items = [
        {**it, "opportunity_id": "a41.food.ready_meals"}
        if it["opportunity_id"] == "a41.food.basic"
        else it
        for it in plan.model_dump(mode="json")["items"]
    ]
    _, r0 = settle_year_of(bundle3s, "u12.a41.c0", plan=plan, state=s)
    _, r1 = settle_year_of(bundle3s, "u12.a41.c0", plan=with_items(plan, *items), state=s)
    a, b = score_of(bundle3s, scoring3s, s, r0), score_of(bundle3s, scoring3s, s, r1)
    assert s_series(a, "fresh_food") == pytest.approx([3 / 10] * 12, **APPROX)
    assert s_series(b, "fresh_food") == pytest.approx([7 / 14] * 12, **APPROX)
    others = [c for c in COMPONENTS if c != "fresh_food"]
    assert {c: s_series(a, c) for c in others} == {c: s_series(b, c) for c in others}
    assert r1.outcome.time_used_hours < r0.outcome.time_used_hours  # 时间事实被记录（E1-4 才约束）


# --------------------------------------------------------------------------
# 只改标价：分项得分不变，预算变化由结算产生
# --------------------------------------------------------------------------


def test_the_same_conditions_at_a_higher_price_score_the_same(raw3s, bundle3s, scoring3s):
    for o in catalog(raw3s, "A41")["opportunities"]:
        if o.get("price"):
            o["price"]["amount"]["amount"] *= 1.5
    pricey = bundle_from(raw3s)
    assert cross_validate(pricey).settlement_ready
    s0 = _rich(bundle3s, "u11.a41.c0")
    s1 = _rich(pricey, "u11.a41.c0")
    _, r0 = settle_year_of(bundle3s, "u11.a41.c0", state=s0)
    _, r1 = settle_year_of(pricey, "u11.a41.c0", state=s1)
    a = score_of(bundle3s, scoring3s, s0, r0)
    b = score_of(pricey, scoring3s, s1, r1)
    assert _all_s(a) == _all_s(b)
    assert [m.u for m in a.months] == [m.u for m in b.months]
    assert cents(r1.outcome.cash_end) < cents(r0.outcome.cash_end)


# --------------------------------------------------------------------------
# 同一组稳定偏好，面对不同价格与可得性，有不同的可行安排
# --------------------------------------------------------------------------


def test_one_stable_preference_meets_different_feasible_arrangements(bundle3s, scoring3s):
    """u13 的偏好与起点在两座城相同；B42 近处房租更高，降级与出局都更早。

    这只说明"可行安排因城而异"由结算产生；**不**说明 LLM 会作出这些选择。
    """
    pref = research_pref(bundle3s, "u13")
    homes = {}
    for clone in ("u13.a41.c0", "u13.b42.c0"):
        s, r = settle_year_of(bundle3s, clone)
        score = score_of(bundle3s, scoring3s, s, r, preference=pref)
        homes[clone] = (
            [
                (it.opportunity_id, it.covered_months)
                for it in r.outcome.items
                if it.role == "housing" and it.covered_months
            ],
            r.outcome.exit.month,
            [m.status for m in score.months].count("scored"),
        )
    assert homes["u13.a41.c0"] == (
        [("a41.housing.near", (1, 2, 3)), ("a41.housing.far", (4,))],
        5,
        4,
    )
    assert homes["u13.b42.c0"] == (
        [("b42.housing.near", (1, 2)), ("b42.housing.far", (3,))],
        4,
        0,  # B42 景观未知（β > 0）：活着的三个月也只能是不完整
    )

    # 同一份"带共享工位"的计划：A41 每月执行，B42 确定缺货、每月被拒
    for clone, code, executed in (("u11.a41.c0", "a41", 12), ("u11.b42.c0", "b42", 0)):
        plan = plus_items(
            with_items(
                plan_of(bundle3s, clone),
                {
                    "opportunity_id": f"{code}.housing.far",
                    "quantity": 1,
                    "start_month": 1,
                    "end_month": 12,
                },
                {
                    "opportunity_id": f"{code}.food.fresh",
                    "quantity": 1,
                    "start_month": 1,
                    "end_month": 12,
                },
                {
                    "opportunity_id": f"{code}.job.office",
                    "quantity": 1,
                    "start_month": 1,
                    "end_month": 12,
                },
            ),
            {
                "opportunity_id": f"{code}.service.cowork",
                "quantity": 1,
                "start_month": 1,
                "end_month": 12,
            },
        )
        _, r = settle_year_of(bundle3s, clone, plan=plan)
        (desk,) = (it for it in r.outcome.items if it.opportunity_id == f"{code}.service.cowork")
        assert len(desk.covered_months) == executed
        if not executed:
            assert all("确定缺货" in x.reason for x in desk.rejections)


# --------------------------------------------------------------------------
# 自然景观：S_env = min(c, b/(b+τ) + β·A)
# --------------------------------------------------------------------------


def test_the_landscape_bonus_is_bounded_sourced_and_reported(bundle3s, scoring3s, raw_scoring3s):
    s, r = settle_year_of(bundle3s, "u11.a41.c0")
    env = component(score_of(bundle3s, scoring3s, s, r), 1, "environment")
    ls = env.landscape
    assert env.s == pytest.approx(0.5 + 0.1 * 0.8, **APPROX) and not env.capped
    assert (ls.beta, ls.a_nature, ls.source_kind) == (0.1, 0.8, "synthetic")
    assert (ls.physical, ls.contribution) == pytest.approx((0.5, 0.08), **APPROX)

    # 上限起作用时截顶，但景观贡献照样写出来
    _condition(raw_scoring3s, "A41", "environment")["cap"] = 0.55
    config, report = _config(raw_scoring3s, bundle3s)
    assert report.exploration_scoring_ready, report.errors
    capped = component(score_of(bundle3s, config, s, r), 1, "environment")
    assert capped.s == pytest.approx(0.55, **APPROX) and capped.capped
    assert capped.landscape.contribution == pytest.approx(0.08, **APPROX)

    with pytest.raises(ValueError):
        NatureCondition(
            status="known", a_nature=1.2, source=scoring3s.city_conditions[0].components[0].source
        )


def test_beta_zero_switches_the_landscape_off(bundle3s, raw_scoring3s):
    _spec_component(raw_scoring3s, "environment")["landscape"]["beta"] = 0.0
    config, report = _config(raw_scoring3s, bundle3s)
    assert report.exploration_scoring_ready, report.errors
    for clone in ("u11.a41.c0", "u11.b42.c0"):
        s, r = settle_year_of(bundle3s, clone)
        score = score_of(bundle3s, config, s, r)
        env = component(score, 1, "environment")
        assert (env.status, env.landscape) == ("known", None)
        assert env.s == pytest.approx(0.5, **APPROX)
        assert any("β = 0" in x for x in env.reasons)
        assert score.annual_status == "complete"  # B42 未启用的景观项不再阻断


def test_an_unknown_landscape_is_not_zero_while_beta_is_positive(bundle3s, scoring3s):
    s, r = settle_year_of(bundle3s, "u11.b42.c0")
    score = score_of(bundle3s, scoring3s, s, r)
    env = component(score, 1, "environment")
    assert (env.status, env.s) == ("unknown", None)
    assert any("景观数据未知" in x for x in env.reasons)
    assert score.annual_status == "incomplete_months" and score.annual_mean is None

    # 环境权重为 0 的人：这一项缺失不阻断其他已知的分数
    w = dict(research_pref(bundle3s, "u11").dimension_weights)
    w["safety"] += w["environment"]
    w["environment"] = 0.0
    no_env = ScoringPreference(
        person_id="u11",
        source="research_synthetic_truth",
        dimension_weights=w,
        material_weights=research_pref(bundle3s, "u11").material_weights,
        derivation="测试：环境权重移给安全",
    )
    assert score_of(bundle3s, scoring3s, s, r, preference=no_env).annual_status == "complete"


def test_the_landscape_cannot_turn_an_unknown_base_into_a_known_one(bundle3s, raw_scoring3s):
    cond = _condition(raw_scoring3s, "A41", "environment")
    for key in ("base", "cap"):
        del cond[key]
    cond["status"] = "unknown"
    cond["unknown_reason"] = "测试：基础环境没有资料"
    config, report = _config(raw_scoring3s, bundle3s)
    assert report.exploration_scoring_ready, report.errors
    s, r = settle_year_of(bundle3s, "u11.a41.c0")
    env = component(score_of(bundle3s, config, s, r), 1, "environment")
    assert (env.status, env.s, env.landscape) == ("unknown", None, None)
    assert any("城市条件未知" in x for x in env.reasons)


def test_double_counting_and_undeclared_landscape_data_are_refused(
    bundle3s, raw_scoring3s, raw_scoring3s_once
):
    import copy

    def errors_after(change) -> list[str]:
        data = copy.deepcopy(raw_scoring3s_once)
        change(data)
        return list(_config(data, bundle3s)[1].errors)

    def double(d):
        _condition(d, "A41", "environment")["nature"]["base_includes_landscape"] = True

    def missing(d):
        del _condition(d, "A41", "environment")["nature"]

    def stray(d):
        _condition(d, "A41", "safety")["nature"] = _condition(d, "A41", "environment")["nature"]

    assert any("重复计分" in e for e in errors_after(double))
    assert any("β > 0 时必须写明景观数据" in e for e in errors_after(missing))
    assert any("没有启用景观规则" in e for e in errors_after(stray))

    def double_off(d):
        double(d)
        _spec_component(d, "environment")["landscape"]["beta"] = 0.0

    assert errors_after(double_off) == []  # β = 0 时没有加分，也就谈不上重复

    for beta in (0.6, -0.1):
        data = copy.deepcopy(raw_scoring3s_once)
        _spec_component(data, "environment")["landscape"]["beta"] = beta
        with pytest.raises(LifeContractError):
            parse_scoring_config(data)
    _spec_component(raw_scoring3s, "safety")["landscape"] = {"beta": 0.1, "definition": "测试"}
    with pytest.raises(LifeContractError, match="只属于环境维"):
        parse_scoring_config(raw_scoring3s)


def test_beta_is_one_number_for_the_whole_group(raw_scoring3s):
    """β 只写在评分规格里；城市条件里根本没有这个字段——结构上就不能按城调。"""
    _condition(raw_scoring3s, "A41", "environment")["beta"] = 0.3
    with pytest.raises(LifeContractError, match="beta"):
        parse_scoring_config(raw_scoring3s)


# --------------------------------------------------------------------------
# 租金—通勤：读实际所住住房，配当月实际执行的线下工作
# --------------------------------------------------------------------------


def test_commute_is_read_from_the_home_actually_lived_in(bundle3s, scoring3s):
    got = {}
    for clone in ("u11.a41.c0", "u11.a41.c1"):
        s, r = settle_year_of(bundle3s, clone)
        c = component(score_of(bundle3s, scoring3s, s, r), 6, "commute")
        got[clone] = (
            c.commute.housing_opportunity_id,
            c.commute.job_opportunity_id,
            c.commute.minutes,
            c.s,
        )
    assert got["u11.a41.c0"][:3] == ("a41.housing.near", "a41.job.office", 30)
    assert got["u11.a41.c1"][:3] == ("a41.housing.far", "a41.job.office", 70)
    assert got["u11.a41.c0"][3] == pytest.approx(60 / 90, **APPROX)
    assert got["u11.a41.c1"][3] == pytest.approx(60 / 130, **APPROX)


def test_a_downgrade_moves_rent_and_commute_together(bundle3s, scoring3s):
    s, r = settle_year_of(bundle3s, "u13.a41.c0")
    score = score_of(bundle3s, scoring3s, s, r)
    got = [
        (
            component(score, m, "commute").commute.housing_opportunity_id,
            component(score, m, "commute").s,
        )
        for m in range(1, 5)
    ]
    assert [g[0] for g in got] == ["a41.housing.near"] * 3 + ["a41.housing.far"]
    assert [g[1] for g in got] == pytest.approx([60 / 90] * 3 + [60 / 130], **APPROX)
    fallback = next(it for it in r.outcome.items if it.status == "fallback_minimum")
    assert (fallback.opportunity_id, fallback.covered_months) == ("a41.housing.far", (4,))
    assert score.months[4].status == "exit_month"


def test_longer_commutes_always_score_lower(raw3s, scoring3s):
    """往返分钟越多，通勤满足感越低：方向写反了（分钟当成正改善）会在这里露出来。"""
    got = []
    for minutes in (0, 10, 30, 70, 120):
        opportunity(raw3s, "a41.housing.near")["spec"]["commute_roundtrip_minutes"] = minutes
        b = bundle_from(raw3s)
        s, r = settle_year_of(b, "u11.a41.c0")
        got.append(component(score_of(b, scoring3s, s, r), 1, "commute").s)
    assert got == pytest.approx([60 / (60 + t) for t in (0, 10, 30, 70, 120)], **APPROX)
    assert got[0] == pytest.approx(1.0, **APPROX)
    assert all(a > b for a, b in itertools.pairwise(got))


def test_commute_minutes_cannot_be_chosen_apart_from_the_home(raw3s, bundle3s, scoring3s):
    """远处的租金只能配远处的分钟：把分钟列成可选安排，就是拆配，加载时拒绝。"""
    opportunity(raw3s, "a41.housing.far")["arrangement_options"] = {
        "commute_roundtrip_minutes": [30, 70]
    }
    report = check_scoring_config(scoring3s, bundle_from(raw3s))
    assert any("拆配" in e for e in report.errors)

    # 没有列为可选安排时，计划里也不能自己写一个"更短的通勤"
    plan = plan_of(bundle3s, "u11.a41.c1")
    data = plan.model_dump(mode="json")
    data["items"][0]["arrangement"] = {"commute_roundtrip_minutes": 30}
    raw = raw3s
    raw["plans"] = [p for p in raw["plans"] if p["clone_id"] != "u11.a41.c1"] + [data]
    opportunity(raw, "a41.housing.far")["arrangement_options"] = {}
    errors = cross_validate(bundle_from(raw)).errors
    assert any("commute_roundtrip_minutes" in e and "不在该机会允许的选项里" in e for e in errors)


def _commute_reasons(score, month: int) -> tuple[str, tuple[str, ...]]:
    c = component(score, month, "commute")
    return c.status, c.reasons


def test_no_job_remote_work_or_two_jobs_leave_commute_unsupported(bundle3s, scoring3s):
    """未定义的安排报"不支持/信息不足"：不给最高分（零通勤），也不照扣通勤分。"""
    s = _rich(bundle3s, "u11.a41.c0")
    home = {"opportunity_id": "a41.housing.near", "quantity": 1, "start_month": 1, "end_month": 12}
    food = {"opportunity_id": "a41.food.fresh", "quantity": 1, "start_month": 1, "end_month": 12}
    plan = plan_of(bundle3s, "u11.a41.c0")

    def job(oid: str, end: int = 12) -> dict:
        return {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": end}

    cases = {
        "none": (with_items(plan, home, food), "没有执行任何工作"),
        "remote": (with_items(plan, home, food, job("a41.job.remote")), "不是固定线下工作"),
        "two": (
            with_items(plan, home, food, job("a41.job.office"), job("a41.job.parttime")),
            "多岗位",
        ),
    }
    for name, (p, why) in cases.items():
        _, r = settle_year_of(bundle3s, "u11.a41.c0", plan=p, state=s)
        score = score_of(bundle3s, scoring3s, s, r)
        status, reasons = _commute_reasons(score, 1)
        assert status == "unknown", name
        assert reasons[0].startswith(UNSUPPORTED) and why in reasons[0], (name, reasons)
        assert component(score, 1, "commute").s is None
        assert score.months[0].status == "incomplete"  # 通勤权重 0.15 > 0：不补成任何分数

    # 工作只做到第 6 月：住房住满全年，通勤只在有线下工作的月份评
    _, r = settle_year_of(
        bundle3s, "u11.a41.c0", plan=with_items(plan, home, food, job("a41.job.office", 6)), state=s
    )
    score = score_of(bundle3s, scoring3s, s, r)
    commute = s_series(score, "commute")
    assert commute[:6] == pytest.approx([60 / 90] * 6, **APPROX)
    assert commute[6:] == [None] * 6


def test_the_job_must_declare_its_work_mode(raw3s, scoring3s):
    del opportunity(raw3s, "a41.job.office")["spec"]["work_mode"]
    b = bundle_from(raw3s)
    report = check_scoring_config(scoring3s, b)
    assert any("a41.job.office" in e and "work_mode" in e for e in report.errors)
    # 评分器自己也不猜：规格缺工作方式就报不支持/信息不足
    s, r = settle_year_of(b, "u11.a41.c0")
    status, reasons = _commute_reasons(score_of(b, scoring3s, s, r), 1)
    assert status == "unknown" and "缺 work_mode" in reasons[0]


def test_housing_choice_does_not_touch_safety_or_environment(bundle3s, scoring3s):
    scores = []
    for clone in ("u11.a41.c0", "u11.a41.c1"):
        s, r = settle_year_of(bundle3s, clone)
        scores.append(score_of(bundle3s, scoring3s, s, r))
    near, far = scores
    for cid in ("safety", "environment", "housing_space"):
        assert s_series(near, cid) == s_series(far, cid)


def test_plan_level_commute_must_pass_for_the_whole_group(
    raw3s, bundle3s, scoring3s, raw_scoring3s_once
):
    import copy

    del opportunity(raw3s, "b42.housing.far")["spec"]["commute_roundtrip_minutes"]
    errors = check_scoring_config(scoring3s, bundle_from(raw3s)).errors
    assert any("b42.housing.far" in e and "全组通过才启用方案级" in e for e in errors)

    def errors_after(change) -> list[str]:
        data = copy.deepcopy(raw_scoring3s_once)
        change(data)
        return list(_config(data, bundle3s)[1].errors)

    def background_group(d):
        d["comparison_group"]["commute_granularity"] = "background"

    def background_spec(d):
        comp = _spec_component(d, "commute")
        del comp["plan_commute"]
        for code in ("A41", "B42"):
            cond = _condition(d, code, "commute")
            cond.update(status="known", base=2, cap=0.9)

    def known_city(d):
        _condition(d, "A41", "commute").update(status="known", base=2, cap=0.9)

    assert any("背景级通勤" in e for e in errors_after(background_group))
    assert any("评分规格的通勤不是方案级" in e for e in errors_after(background_spec))
    assert any("城市条件应写 plan_level" in e for e in errors_after(known_city))
