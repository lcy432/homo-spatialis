"""生活扩展收尾修复（独立审核 R1–R9，DECISIONS/0041 第十节）：每条先放审核时能复现的最小反例，再放修复后的期望。

全部离线、受控计划，不调用任何模型。手算口径同 ``test_life_extension.py``（评分规格 configs/life/scoring_product.yaml）。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from common.life_contracts import CityLifeCatalog, LifeContractError
from ledger.life_catalog import cross_validate, parse_bundle

from .conftest import CLONE_K, K51, live_year, once, opportunity, whole

TOL = 1e-12
ALL = list(range(1, 13))


def plan(food: str = "k51.food.basic", extra=()):
    return [whole("k51.housing.far"), whole(food), whole("k51.job.clerk"), *extra]


def catalog_with(bundle, oid: str, change) -> CityLifeCatalog:
    """K51 目录的 JSON 改一处（``change`` 原地改这项机会的字典），再交给契约校验。"""
    data = bundle.catalog_for(K51).model_dump(mode="json")
    o = next(x for x in data["opportunities"] if x["opportunity_id"] == oid)
    change(o)
    return CityLifeCatalog.model_validate(data)


def comp(year, month: int, cid: str):
    m = next(x for x in year.score.months if x.month == month)
    return next(c for c in m.components if c.component_id == cid)


@pytest.fixture
def start(bundle):
    return bundle.initial_state(CLONE_K)


# --------------------------------------------------------------------------- R2：新增内部评分对象不出隔离边界

#: 独立审核的夹带例子：一条 ParticipationEntry 原样序列化（实际 4 小时、加权 6 小时，足以推回匹配倍率）
ENTRY = {
    "opportunity_id": "k51.experience.community_choir",
    "primary_category": "music",
    "participation_hours": 4.0,
    "matched": True,
    "weighted_hours": 6.0,
}


def test_r2_a_participation_entry_hidden_in_a_spec_is_refused_by_the_loader(raw):
    opportunity(raw, "k51.durable.guitar")["spec"] = dict(ENTRY)
    report = cross_validate(parse_bundle(raw))
    text = " ".join(report.errors)
    assert "ParticipationEntry" in text and "weighted_hours" in text


def test_r2_the_same_entry_is_refused_by_the_request_chain_without_the_loader(bundle, scoring):
    from .test_life_extension import request_for

    hidden = catalog_with(bundle, "k51.durable.guitar", lambda o: o.update(spec=dict(ENTRY)))
    with pytest.raises(LifeContractError, match=r"weighted_hours|ParticipationEntry"):
        request_for(bundle, scoring, catalog=hidden)


@pytest.mark.parametrize(
    "build",
    [
        "entry_object",
        "entry_dict_nested",
        "term_dict_nested",
        "food_term",
        "enjoyment_term",
        "participation_spec",
        "activity_rule",
    ],
)
def test_r2_new_internal_objects_are_refused_as_objects_dicts_and_nested(build):
    from agentlayer.life_views import assert_payload_clean
    from common.life_evaluation_contracts import ActivityUseRule
    from common.life_scoring_contracts import (
        EnjoymentFunctionTerm,
        EnjoymentTerm,
        FoodLifeTerm,
        ParticipationEntry,
        ParticipationTerm,
    )

    entry = ParticipationEntry(**ENTRY)
    term = ParticipationTerm(
        entries=(entry,), weighted_hours=6.0, eta_hour=1.0, match_bonus=0.5, contribution=6.0
    )
    payloads = {
        "entry_object": {"x": entry},
        "entry_dict_nested": {"a": {"b": [dict(ENTRY)]}},
        "term_dict_nested": {"feedback": {"notes": [term.model_dump(mode="json")]}},
        "food_term": FoodLifeTerm(
            opportunity_id="k51.food.sichuan_home",
            fresh_meals_per_week=10,
            taste_matched_meals_per_week=21,
            eta_fresh=1.0,
            eta_taste=0.5,
        ).model_dump(mode="json"),
        "enjoyment_term": EnjoymentTerm(
            functions=(
                EnjoymentFunctionTerm(
                    function="home_comfort",
                    measure="presence",
                    value=1,
                    eta=1.5,
                    contribution=1.5,
                    via=("k51.durable.comfort_chair",),
                ),
            )
        ),
        "participation_spec": {
            "rule": "participation_hours_interest_match_v1",
            "eta_hour": 1.0,
            "match_bonus": 0.5,
            "unit": "参与小时",
            "definition": "x",
        },
        "activity_rule": ActivityUseRule(
            activity_slot="music_practice",
            sessions_per_month=4,
            when_missing="skip",
            after_expiry="stop",
        ).model_dump(mode="json"),
    }
    with pytest.raises(LifeContractError):
        assert_payload_clean(payloads[build])


def test_r2_normal_planning_memory_and_revision_payloads_carry_facts_not_matching(
    bundle, scoring, start
):
    import json

    from agentlayer.life_planning import MemoryYear
    from agentlayer.life_views import visible_feedback
    from common.life_planning_contracts import VisibleDiagnostic, VisibleRevision

    from .test_life_extension import keys_of, request_for

    y = live_year(
        bundle,
        scoring,
        start,
        plan(
            extra=[
                once("k51.durable.guitar", 1),
                {
                    "opportunity_id": "k51.experience.guitar_practice",
                    "quantity": 1,
                    "months": [2, 3],
                },
                {"opportunity_id": "k51.experience.community_choir", "quantity": 1, "months": [4]},
            ]
        ),
    )
    memory = MemoryYear(
        clone_id=CLONE_K,
        city=K51,
        year_index=1,
        feedback=visible_feedback(y.settlement),
        need_summary="测试",
        rationale="测试",
    )
    revision = VisibleRevision(
        attempt=2,
        revisions_left_after_this=1,
        previous_output_text='{"items": []}',
        previous_output_truncated=False,
        diagnostics=(VisibleDiagnostic(source="plan_rules", code="item_missing", text="缺住房"),),
        note="测试：修订",
    )
    for payload in (
        request_for(bundle, scoring, memory_years=[memory], state=y.next_state),
        request_for(bundle, scoring, memory_years=[memory], state=y.next_state, revision=revision),
    ):
        keys = keys_of(payload)
        assert not keys & {
            "matched",
            "weighted_hours",
            "eta_hour",
            "match_bonus",
            "matched_cuisines",
        }
        text = json.dumps(payload, ensure_ascii=False)
        assert "participation_hours_interest_match_v1" not in text
        # 正常生活事实仍然可见：参加的次数、参与小时与用到的装备
        assert "k51.durable.guitar" in text and "participation_hours" in keys


# --------------------------------------------------------------------------- R3：缺失的新鲜餐数不当 0


def test_r3_a_food_part_without_fresh_meals_is_refused_not_scored_as_zero(bundle):
    with pytest.raises(ValidationError, match="fresh_meals_per_week"):
        catalog_with(
            bundle,
            "k51.food.sichuan_home",
            lambda o: o["food_plan"]["parts"][0].pop("fresh_meals_per_week"),
        )
    with pytest.raises(ValidationError, match="cuisine"):
        catalog_with(
            bundle, "k51.food.sichuan_home", lambda o: o["food_plan"]["parts"][0].pop("cuisine")
        )


@pytest.mark.parametrize("bad", [-1, 22, float("nan")])
def test_r3_illegal_fresh_meals_are_refused(bundle, bad):
    with pytest.raises(ValidationError):
        catalog_with(
            bundle,
            "k51.food.sichuan_home",
            lambda o: o["food_plan"]["parts"][0].update(fresh_meals_per_week=bad),
        )


def test_r3_explicit_zero_is_a_known_zero_and_null_is_unknown(bundle, scoring, start):
    zero = catalog_with(
        bundle,
        "k51.food.sichuan_home",
        lambda o: o["food_plan"]["parts"][0].update(fresh_meals_per_week=0),
    )
    y = live_year(bundle, scoring, start, plan("k51.food.sichuan_home"), catalog=zero)
    food = comp(y, 1, "food_life")
    assert food.status == "known" and food.s == pytest.approx(
        10.5 / 17.5, abs=TOL
    )  # z = 0 + 0.5 × 21
    unknown = catalog_with(
        bundle,
        "k51.food.sichuan_home",
        lambda o: o["food_plan"]["parts"][0].update(fresh_meals_per_week=None),
    )
    u = live_year(bundle, scoring, start, plan("k51.food.sichuan_home"), catalog=unknown)
    food = comp(u, 1, "food_life")
    assert food.status == "unknown" and food.s is None
    assert "fresh_meals_per_week" in " ".join(food.reasons)
    assert u.score.annual_status != "complete"  # 正权重未知：不补零、不删项、不重新归一化


def test_r3_unknown_cuisine_matters_only_for_users_who_confirmed_a_cuisine(bundle, scoring, start):
    from .test_life_extension import neutral

    unknown = catalog_with(
        bundle, "k51.food.sichuan_home", lambda o: o["food_plan"]["parts"][0].update(cuisine=None)
    )
    fan = live_year(bundle, scoring, start, plan("k51.food.sichuan_home"), catalog=unknown)
    assert comp(fan, 1, "food_life").status == "unknown"
    plain = live_year(
        bundle,
        scoring,
        start,
        plan("k51.food.sichuan_home"),
        catalog=unknown,
        profile=neutral(bundle),
    )
    food = comp(plain, 1, "food_life")
    assert food.status == "known" and food.s == pytest.approx(10 / 17, abs=TOL)  # 只看新鲜餐


def test_r3_an_activity_must_state_whether_it_replaces_meals(bundle):
    with pytest.raises(ValidationError, match="replaces_meals_per_session"):
        catalog_with(
            bundle,
            "k51.experience.cooking_class",
            lambda o: o["activity"].pop("replaces_meals_per_session"),
        )


def test_r3_normal_and_downgraded_plans_keep_their_hand_values(bundle, scoring, start):
    basic = live_year(bundle, scoring, start, plan())
    assert comp(basic, 1, "food_life").s == pytest.approx(0.3, abs=TOL)
    sichuan = live_year(bundle, scoring, start, plan("k51.food.sichuan_home"))
    assert comp(sichuan, 1, "food_life").s == pytest.approx(41 / 55, abs=TOL)
    dear = catalog_with(
        bundle,
        "k51.food.home_and_dining",
        lambda o: o["price"]["amount"].update(amount=999999),
    )
    down = live_year(bundle, scoring, start, plan("k51.food.home_and_dining"), catalog=dear)
    assert comp(down, 1, "food_life").food_life.opportunity_id == "k51.food.basic"
    assert comp(down, 1, "food_life").s == pytest.approx(0.3, abs=TOL)


# --------------------------------------------------------------------------- R6：一次性享受服务遇疾病暂停


def ill_in(bundle, month: int, severity: str = "major"):
    from common.life_contracts import IllnessEventSource, InjectedIllness

    ill = IllnessEventSource(
        mode="injected",
        stream_version="e1-5c-illness-stream-v1",
        injected=(
            InjectedIllness(
                person_id="u301",
                repeat_index=0,
                year_index=1,
                month=month,
                severity=severity,
                note=f"测试：第 {month} 月月初生病（{severity}）",
            ),
        ),
        note="测试：指定事件",
    )
    return bundle.protocol.model_copy(update={"illness_events": ill})


TREAT = {"minor": "treat_if_possible", "major": "treat_if_possible", "pause_order": []}


BASKET = "k51.service.personal_basket.u1"


def one_time_basket(o: dict) -> None:
    """审核的反例改写：按次购买的一份消费篮子体验，只有享受事实、没有活动事实（每次 1 份、1 小时）。"""
    o.update(
        label="个人消费体验（一次）",
        unit="session",
        price={
            "amount": {"amount": 70, "numeraire": "USD_2024_LOCAL"},
            "billing": "one_time",
            "per_unit": "session",
        },
        duration={"kind": "one_time_experience", "lasts_years": 0},
        resource={
            "basis": "per_occurrence",
            "hours": 1,
            "burden_per_hour": 0.2,
            "quantity_scales": True,
            "note": "一次 1 小时",
        },
    )
    o["enjoyment"][0].update(units_per_month=1)


def test_r6_a_one_time_enjoyment_service_paused_by_illness_is_not_a_program_error(
    bundle, scoring, start
):
    cat = catalog_with(bundle, BASKET, one_time_basket)
    y = live_year(
        bundle,
        scoring,
        start,
        plan(extra=[{"opportunity_id": BASKET, "quantity": 1, "months": [2, 3, 4]}]),
        catalog=cat,
        protocol=ill_in(bundle, 3),
        emergency=TREAT,
    )
    assert y.settlement.status == "completed"
    item = next(i for i in y.settlement.outcome.items if i.opportunity_id == BASKET)
    assert item.covered_months == (2, 4) and [p.month for p in item.months_paused] == [3]
    paid = [f.month for f in y.settlement.outcome.cash_flows if f.opportunity_id == BASKET]
    assert paid == [2, 4]  # 暂停的月份不付款，也没有退款或免费补上
    assert [comp(y, m, "personal_enjoyment").z for m in (2, 3, 4)] == [3.0, 0.0, 3.0]  # 每份 z = 3
    assert not [p for p in y.settlement.outcome.participation if p.opportunity_id == BASKET]


def test_r6_a_multi_month_activity_is_paused_only_in_the_ill_month(bundle, scoring, start):
    cooking = "k51.experience.cooking_class"
    y = live_year(
        bundle,
        scoring,
        start,
        plan(extra=[{"opportunity_id": cooking, "quantity": 1, "months": [2, 3, 4]}]),
        protocol=ill_in(bundle, 3),
        emergency=TREAT,
    )
    rows = {p.month: p.status for p in y.settlement.outcome.participation}
    assert rows == {2: "completed", 3: "paused", 4: "completed"}
    paid = [f.month for f in y.settlement.outcome.cash_flows if f.opportunity_id == cooking]
    assert paid == [2, 4]


def test_r6_the_declared_pause_order_finds_a_multi_month_activity(bundle, scoring, start):
    """小病、时间不够时，按年度应急顺序暂停写成多个月份的活动（上一轮修了却没有测试的那一处）。每月练琴 20 次：只在病月暂停。"""
    practice = "k51.experience.guitar_practice"
    y = live_year(
        bundle,
        scoring,
        start,
        plan(
            extra=[
                once("k51.durable.guitar", 1),
                {"opportunity_id": practice, "quantity": 20, "months": [2, 3, 4]},
            ]
        ),
        protocol=ill_in(bundle, 3, "minor"),
        emergency={"minor": "treat_if_possible", "major": "decline", "pause_order": [practice]},
    )
    rows = {p.month: (p.status, p.sessions) for p in y.settlement.outcome.participation}
    assert rows == {2: ("completed", 20), 3: ("paused", 0), 4: ("completed", 20)}
    assert y.settlement.outcome.health.months[2].treatment.status == "treated"


# --------------------------------------------------------------------------- R9：活动的实际参与只有一个来源


def assert_time_matches_participation(y) -> None:
    """每项活动每个月：资源账里这项活动的小时 = 完成的参与记录的参与 + 准备往返小时；没完成就没有小时；执行月 = 完成月。"""
    from fractions import Fraction

    from common.life_contracts import to_fraction

    out = y.settlement.outcome
    activities = {p.opportunity_id for p in out.participation}
    for month in out.resources.months:
        for oid in activities:
            booked = sum(
                (c.hours.fraction() for c in month.contributions if c.opportunity_id == oid),
                Fraction(0),
            )
            done = [
                p
                for p in out.participation
                if p.opportunity_id == oid and p.month == month.month and p.status == "completed"
            ]
            want = sum(
                (to_fraction(p.participation_hours) + to_fraction(p.overhead_hours) for p in done),
                Fraction(0),
            )
            assert booked == want, (oid, month.month, booked, want)
    for it in out.items:
        rows = [
            p
            for p in out.participation
            if p.opportunity_id == it.opportunity_id and p.status == "completed"
        ]
        if rows:
            assert sorted(p.month for p in rows) == list(it.covered_months)


@pytest.mark.parametrize(
    "case", ["success_partial_kit", "missing_equipment", "purchase_failed", "illness_pause"]
)
def test_r9_the_time_account_reads_the_participation_record(bundle, scoring, start, case):
    practice = "k51.experience.guitar_practice"
    kwargs: dict = {}
    if case == "success_partial_kit":
        extra = [
            {"opportunity_id": practice, "quantity": 2, "months": [1, 6]},
            once("k51.durable.guitar", 1),
            {"opportunity_id": "k51.experience.community_choir", "quantity": 1, "months": [4, 9]},
            once("k51.experience.tennis_with_rental", 5, quantity=2),
        ]
    elif case == "missing_equipment":
        extra = [{"opportunity_id": practice, "quantity": 1, "months": [3]}]
    elif case == "purchase_failed":
        extra = [
            once("k51.durable.guitar", 2),
            {"opportunity_id": practice, "quantity": 1, "months": [2]},
        ]
        kwargs["catalog"] = catalog_with(
            bundle, "k51.durable.guitar", lambda o: o["price"]["amount"].update(amount=999999)
        )
    else:
        extra = [
            {"opportunity_id": "k51.experience.cooking_class", "quantity": 1, "months": [2, 3, 4]}
        ]
        kwargs.update(protocol=ill_in(bundle, 3), emergency=TREAT)
    y = live_year(bundle, scoring, start, plan(extra=extra), **kwargs)
    assert y.settlement.status == "completed"
    assert_time_matches_participation(y)
    for m in y.score.months:
        leisure = next(c for c in m.components if c.component_id == "leisure")
        rows = [
            p
            for p in y.settlement.outcome.participation
            if p.month == m.month and p.status == "completed"
        ]
        assert sum(e.participation_hours for e in leisure.participation.entries) == pytest.approx(
            sum(p.participation_hours for p in rows), abs=TOL
        )


def test_r9_splitting_the_same_participation_adds_no_time(bundle, start):
    """同一项活动同一个月拆成两条完成记录（2 次 = 1 次 + 1 次）：时间与负担相同。计划里同一机会本来就不能写两条（见下一条）。"""
    from common.life_contracts import ParticipationRecord
    from econ.life_resources import Activity, month_resources

    def rec(n: int) -> ParticipationRecord:
        return ParticipationRecord(
            plan_order=4,
            opportunity_id="k51.experience.community_choir",
            month=4,
            status="completed",
            primary_category="music",
            sessions=float(n),
            participation_hours=2.0 * n,
            overhead_hours=0.5 * n,
        )

    choir = bundle.catalog_for(K51).opportunity("k51.experience.community_choir")
    rules = bundle.protocol.resource_rules
    one, _ = month_resources(rules, start, 4, [Activity("experience", choir, 2, rec(2))])
    two, _ = month_resources(
        rules,
        start,
        4,
        [Activity("experience", choir, 1, rec(1)), Activity("experience", choir, 1, rec(1))],
    )
    assert one.time_used == two.time_used and one.energy_used == two.energy_used


def test_r9_a_plan_cannot_list_the_same_activity_twice(start):
    from .conftest import year_action

    with pytest.raises(ValidationError, match="同一机会出现多次"):
        year_action(
            start,
            plan(
                extra=[
                    once("k51.experience.community_choir", 3),
                    once("k51.experience.community_choir", 7),
                ]
            ),
        )


def test_r9_score_memory_and_result_counts_trace_to_the_record(bundle, scoring, start):
    from agentlayer.life_planning import _participation_lines
    from agentlayer.life_views import visible_feedback

    choir = "k51.experience.community_choir"
    y = live_year(
        bundle,
        scoring,
        start,
        plan(extra=[{"opportunity_id": choir, "quantity": 2, "months": [4, 9]}]),
    )
    rows = [p for p in y.settlement.outcome.participation if p.status == "completed"]
    assert sum(p.sessions for p in rows) == 4 and sum(p.participation_hours for p in rows) == 8
    (line,) = _participation_lines(1, visible_feedback(y.settlement))
    assert "共 4 次、8 参与小时" in line
    entries = [
        e
        for m in y.score.months
        for c in m.components
        if c.participation is not None
        for e in c.participation.entries
    ]
    assert sum(e.participation_hours for e in entries) == 8


def test_r9_resume_does_not_recount_participation_fees_or_lifetime(tmp_path, monkeypatch):
    """同一份名单：一口气跑完 vs 第 1 年之后中断再恢复。计划里第 1 年买吉他、每月练琴（离线替身按 mock 计划加这两项）。
    恢复只重放归档、不重发已有的请求；每年的年度绑定（结算、参与、费用、寿命与分数的指纹）逐年相同。"""
    import json

    from orchestration.life_budget import RoundBudget
    from orchestration.life_runner import resume_run, start_run

    from .live_support import (
        ScriptedTransport,
        live_world,
        make_repo,
        mock_text,
        payload_of,
        qwen,
        reply,
    )
    from .planning_support import records
    from .test_life_extension import keys_of

    def with_hobby(body):
        payload = payload_of(body)
        city = payload["clone_id"].split(".")[1]
        plan_ = json.loads(mock_text(body))
        items = plan_["action"]["items"]
        if payload["year_index"] == 1:
            items.append({"opportunity_id": f"{city}.durable.guitar", "quantity": 1, "month": 1})
        items.append(
            {"opportunity_id": f"{city}.experience.guitar_practice", "quantity": 1, "months": ALL}
        )
        return reply(json.dumps(plan_, ensure_ascii=False), body, model=body["model"])

    def run(root, stop):
        monkeypatch.chdir(root)
        world = live_world()
        out = root / "run"
        outcome = start_run(
            world,
            qwen(world, ScriptedTransport([with_hobby] * 12)),
            out,
            run_id="r9",
            stop_after_commits=stop,
        )
        assert outcome.status == ("completed" if stop is None else "interrupted"), (
            outcome.pause_reason
        )
        sent = 0
        if stop is not None:
            again = ScriptedTransport([with_hobby] * 12)
            resume_run(world, qwen(world, again), out)
            sent = len(again.bodies)
            for (
                body
            ) in again.bodies:  # 恢复之后发出的请求也不带内部匹配结果或评分参数（R2 的恢复出口）
                assert not keys_of(payload_of(body)) & {
                    "matched",
                    "weighted_hours",
                    "eta_hour",
                    "match_bonus",
                    "matched_cuisines",
                }
        accepted = [
            (a["clone_id"], a["year_index"], a["binding_fingerprint"])
            for a in records(out, "accepted")
        ]
        return accepted, RoundBudget(world.config.paid_budget).totals().requests, sent, out

    whole, n_whole, _, out = run(make_repo(tmp_path / "whole"), None)
    resumed, n_resumed, resent, _ = run(make_repo(tmp_path / "resumed"), 1)
    assert len(whole) == 6 and whole == resumed  # 两座城 × 3 年，每年的绑定逐个相同
    assert n_whole == n_resumed == 6 and resent == 5  # 第 1 年复用归档，不重发、不重复记账
    plans = [json.loads(c["response_text"]) for c in records(out, "calls")]
    assert (
        sum(
            1
            for p in plans
            for i in p["action"]["items"]
            if i["opportunity_id"].endswith(".durable.guitar")
        )
        == 2
    )  # 每座城只在第 1 年买了一次


# --------------------------------------------------------------------------- R1：丰富活动之后的下一年请求


R1_ACTIVITIES = [
    {"opportunity_id": "k51.experience.guitar_practice", "quantity": 1, "months": ALL},
    {"opportunity_id": "k51.experience.tennis_with_rental", "quantity": 1, "months": ALL},
    {"opportunity_id": "k51.experience.community_choir", "quantity": 1, "months": ALL},
]
RICH_ACTIVITIES = [
    {"opportunity_id": "k51.experience.guitar_practice", "quantity": 2, "months": ALL},
    {"opportunity_id": "k51.experience.tennis_with_rental", "quantity": 1, "months": ALL},
    {
        "opportunity_id": "k51.experience.community_choir",
        "quantity": 1,
        "months": [1, 3, 5, 7, 9, 11],
    },
    {"opportunity_id": "k51.experience.gallery_tour", "quantity": 1, "months": [2, 6, 10]},
    {"opportunity_id": "k51.experience.cooking_class", "quantity": 1, "months": [4, 8, 12]},
    {"opportunity_id": "k51.experience.concert", "quantity": 1, "months": [5, 11]},
    whole("k51.service.personal_basket.u1"),
]


def estimate(payload) -> int:
    """正式发送前用的同一个估计（同一份提示渲染、同一条规则）。"""
    from agentlayer.life_llm import estimate_input_tokens
    from agentlayer.life_planning import render_prompt

    system, user = render_prompt(payload)
    return estimate_input_tokens(
        [{"role": "system", "content": system}, {"role": "user", "content": user}]
    )


def lived_requests(bundle, scoring, first, later):
    """过两年（第 1 年 ``first``、第 2 年 ``later``），返回第 2、3 年的正式请求与第 3 年一份最长的修订请求。"""
    from agentlayer.life_planning import MemoryYear
    from agentlayer.life_views import visible_feedback
    from common.life_planning_contracts import (
        MAX_PREVIOUS_OUTPUT_CHARS,
        VisibleDiagnostic,
        VisibleRevision,
    )

    from .test_life_extension import request_for

    st, memory, out = bundle.initial_state(CLONE_K), [], {}
    for year in (1, 2):
        y = live_year(bundle, scoring, st, plan(extra=first if year == 1 else later))
        assert y.settlement.status == "completed"
        memory.append(
            MemoryYear(
                clone_id=CLONE_K,
                city=K51,
                year_index=year,
                feedback=visible_feedback(y.settlement),
                need_summary="测试" * 30,
                rationale="测试" * 100,
            )
        )
        st = y.next_state
        out[f"year{year + 1}"] = request_for(bundle, scoring, memory_years=list(memory), state=st)
    revision = VisibleRevision(
        attempt=2,
        revisions_left_after_this=1,
        previous_output_text="计" * MAX_PREVIOUS_OUTPUT_CHARS,
        previous_output_truncated=True,
        diagnostics=tuple(
            VisibleDiagnostic(source="plan_rules", code=f"c{i}", text="诊" * 600) for i in range(3)
        ),
        note="修订：" + "说" * 300,
    )
    out["year3_revision"] = request_for(
        bundle, scoring, memory_years=list(memory), state=st, revision=revision
    )
    return out


def caps() -> dict[str, int]:
    from .live_support import HAIKU_CONFIG, LIVE_CONFIG, SONNET_CONFIG, load_yaml

    return {
        name: load_yaml(path)["run"]["planner"]["llm"]["max_input_tokens"]
        for name, path in (
            ("flash", LIVE_CONFIG),
            ("sonnet", SONNET_CONFIG),
            ("haiku", HAIKU_CONFIG),
        )
    }


def test_r1_the_three_monthly_activities_give_a_legal_next_year_request(bundle, scoring):
    """审核复现：第一年买吉他，每月各一次练琴、网球套餐、社区合唱，下一年请求估计 67,410 > 64,000。现在约 5.3 万。"""
    reqs = lived_requests(
        bundle, scoring, [once("k51.durable.guitar", 1), *R1_ACTIVITIES], R1_ACTIVITIES
    )
    sizes = {k: estimate(v) for k, v in reqs.items()}
    assert sizes["year2"] < 64_000 and sizes["year3"] < 64_000  # 只省重复就回到旧上限以内
    for name, cap in caps().items():
        assert max(sizes.values()) <= cap, (name, sizes, cap)
    fb = reqs["year2"]["observation"]["last_feedback"]
    (practice,) = [
        a for a in fb["participation"] if a["opportunity_id"].endswith("guitar_practice")
    ]
    assert practice["sessions_total"] == 12 and practice["done"][0]["months"] == ALL
    assert practice["done"][0]["equipment_used"] == ["k51.durable.guitar"]


def test_r1_richer_combinations_later_years_and_revisions_fit_every_configured_cap(bundle, scoring):
    reqs = lived_requests(
        bundle,
        scoring,
        [once("k51.durable.guitar", 1), whole("k51.service.mobile_basic"), *RICH_ACTIVITIES],
        RICH_ACTIVITIES,
    )
    sizes = {k: estimate(v) for k, v in reqs.items()}
    for name, cap in caps().items():
        assert max(sizes.values()) <= cap, (name, sizes, cap)


def test_r1_the_compaction_keeps_every_month_and_every_participation_fact(bundle, scoring, start):
    from agentlayer.life_views import visible_feedback

    y = live_year(
        bundle,
        scoring,
        start,
        plan(
            extra=[
                {
                    "opportunity_id": "k51.experience.guitar_practice",
                    "quantity": 1,
                    "months": [2, 6],
                },
                {"opportunity_id": "k51.experience.community_choir", "quantity": 1, "months": ALL},
            ]
        ),
    )
    fb = visible_feedback(y.settlement)
    months = [m for r in fb.resources for m in r.months]
    assert sorted(months) == ALL  # 每个月都在，而且只在一处
    by_month = {m: r for r in fb.resources for m in r.months}
    for raw in y.settlement.outcome.resources.months:
        assert by_month[raw.month].time_used == raw.time_used  # 合并的月份数字相同
    records = y.settlement.outcome.participation
    for a in fb.participation:
        mine = [p for p in records if p.opportunity_id == a.opportunity_id]
        covered = [m for s in a.done for m in s.months] + [m for n in a.not_done for m in n.months]
        assert sorted(covered) == sorted(p.month for p in mine)
        assert a.sessions_total == sum(p.sessions for p in mine)
        assert a.participation_hours_total == sum(p.participation_hours for p in mine)
    (practice,) = [a for a in fb.participation if a.opportunity_id.endswith("guitar_practice")]
    assert practice.not_done[0].months == (2, 6) and "缺少装备能力" in practice.not_done[0].reason


def test_r1_an_oversized_request_is_refused_before_sending_or_reserving(
    bundle, scoring, tmp_path, monkeypatch
):
    from agentlayer.life_llm import PlannerHalt
    from agentlayer.life_planning import PlannerCall, render_prompt
    from orchestration.life_budget import RoundBudget

    from .live_support import ScriptedTransport, live_world, make_repo, qwen

    payload = lived_requests(
        bundle, scoring, [once("k51.durable.guitar", 1), *R1_ACTIVITIES], R1_ACTIVITIES
    )["year2"]
    monkeypatch.chdir(make_repo(tmp_path / "repo"))
    world = live_world()
    small = world.config.planner.llm.model_copy(update={"max_input_tokens": estimate(payload) - 1})
    transport = ScriptedTransport()
    planner = qwen(world, transport, settings=small)
    planner.attach(archive_path=tmp_path / "run", run_id="r1")
    system, user = render_prompt(payload)
    call = PlannerCall(payload["request_id"], system, user, payload)
    for _ in range(2):  # 再试一次也一样：不发送、不预留、不重复扣费
        with pytest.raises(PlannerHalt) as halt:
            planner.plan(call)
        assert halt.value.kind == "input_limit"
    assert transport.bodies == []
    assert RoundBudget(world.config.paid_budget).totals().requests == 0


def test_r1_caps_must_fit_the_model_window_and_the_price_tier():
    from common.life_planning_contracts import LifeRunConfig, LlmSettings

    from .live_support import HAIKU_CONFIG, LIVE_CONFIG, load_yaml

    haiku = load_yaml(HAIKU_CONFIG)["run"]
    llm = dict(haiku["planner"]["llm"])
    LlmSettings.model_validate(llm)
    with pytest.raises(ValidationError, match="上下文"):
        LlmSettings.model_validate(
            {**llm, "max_input_tokens": 100_000}
        )  # 100,000 + 128,000 > 200,000
    flash = dict(load_yaml(LIVE_CONFIG)["run"]["planner"]["llm"])
    with pytest.raises(ValidationError, match="991808"):
        LlmSettings.model_validate({**flash, "max_input_tokens": 991_809})
    metered = {k: v for k, v in haiku.items() if k != "unmetered_reason"}
    metered["paid_budget"] = {
        "ledger_dir": "budget-haiku",
        "price": {
            "price_version": "claude-haiku-5-5@test",
            "model": "claude-haiku-5-5",
            "region": "test",
            "currency": "USD",
            "input_per_million": 0.1,
            "output_per_million": 0.5,
            "source_url": "https://platform.claude.com/docs/en/about-claude/pricing",
            "checked_on": "2026-10-18",
            "max_input_tokens_at_price": 60_000,
            "rule": "测试：一档只适用到 60,000 token",
        },
    }
    with pytest.raises(ValidationError, match="不按低档冒充"):
        LifeRunConfig.model_validate(metered)


# --------------------------------------------------------------------------- R4：覆盖报告接上新的评分依赖


def coverage(bundle, scoring, catalog=None, profile=None):
    from econ.life_coverage import coverage_report
    from econ.life_scoring import preference_from_confirmed_profile

    catalogs = [
        catalog if catalog is not None and str(c.city) == K51 else c for c in bundle.catalogs
    ]
    return coverage_report(
        spec=scoring.spec,
        group=scoring.comparison_group,
        conditions=scoring.city_conditions,
        catalogs=catalogs,
        preference=preference_from_confirmed_profile(profile or bundle.product_profiles[0]),
    )


def leaf(rep, name: str):
    return next(x for x in rep.leaves if x.leaf == name)


def test_r4_food_and_enjoyment_are_action_mechanisms_not_city_background(bundle, scoring):
    rep = coverage(bundle, scoring)
    for name in ("food_life", "personal_enjoyment", "leisure"):
        x = leaf(rep, name)
        assert (x.support, x.action_mechanism, x.city_data) == ("supported", True, "complete")
        assert not any("只有城市背景" in r for r in x.reasons)
    assert not any(
        "food_life" in n or "personal_enjoyment" in n for n in rep.notes if "只有城市背景" in n
    )


def test_r4_a_missing_food_fact_is_a_gap_in_coverage_and_unknown_in_scoring(bundle, scoring, start):
    dining = catalog_with(
        bundle,
        "k51.food.home_and_dining",
        lambda o: o["food_plan"]["parts"][1].update(fresh_meals_per_week=None),
    )
    x = leaf(coverage(bundle, scoring, dining), "food_life")
    assert x.support == "unknown_in_some_city" and x.cities_unknown == (K51,)
    assert any("fresh_meals_per_week" in g for g in x.data_gaps)
    eats = live_year(bundle, scoring, start, plan("k51.food.home_and_dining"), catalog=dining)
    assert comp(eats, 1, "food_life").status == "unknown"  # 实际吃了缺口方案：那个月未知
    other = live_year(bundle, scoring, start, plan(), catalog=dining)
    assert comp(other, 1, "food_life").status == "known"  # 没吃它：实际轨迹照样可评分
    bare = catalog_with(bundle, "k51.food.sichuan_home", lambda o: o.pop("food_plan"))
    x = leaf(coverage(bundle, scoring, bare), "food_life")
    assert any("没有完整方案事实" in g for g in x.data_gaps)


def test_r4_an_unknown_cuisine_is_a_gap_only_for_users_who_confirmed_a_cuisine(bundle, scoring):
    from .test_life_extension import neutral

    unknown = catalog_with(
        bundle, "k51.food.sichuan_home", lambda o: o["food_plan"]["parts"][0].update(cuisine=None)
    )
    assert leaf(coverage(bundle, scoring, unknown), "food_life").support == "unknown_in_some_city"
    plain = leaf(coverage(bundle, scoring, unknown, neutral(bundle)), "food_life")
    assert plain.support == "supported"


def test_r4_an_unsupported_function_is_reported_apart_from_gaps_and_zeros(bundle, scoring, start):
    aroma = catalog_with(bundle, BASKET, lambda o: o["enjoyment"][0].update(function="aroma"))
    x = leaf(coverage(bundle, scoring, aroma), "personal_enjoyment")
    assert x.support == "supported" and x.data_gaps == ()
    assert any("模型未支持" in r and "aroma" in r for r in x.reasons)
    y = live_year(bundle, scoring, start, plan(extra=[whole(BASKET)]), catalog=aroma)
    e = comp(y, 1, "personal_enjoyment")
    assert e.status == "known" and e.z == 0 and e.enjoyment.unsupported == (f"{BASKET}:aroma",)


def test_r4_an_experience_without_facts_is_a_leisure_gap(bundle, scoring, start):
    bare = catalog_with(bundle, "k51.experience.gallery_tour", lambda o: o.pop("activity"))
    x = leaf(coverage(bundle, scoring, bare), "leisure")
    assert x.support == "unknown_in_some_city" and any("没有活动事实" in g for g in x.data_gaps)
    y = live_year(
        bundle, scoring, start, plan(extra=[once("k51.experience.gallery_tour", 6)]), catalog=bare
    )
    assert comp(y, 6, "leisure").status == "unknown" and comp(y, 5, "leisure").status == "known"
    assert y.score.annual_status != "complete"  # 正权重未知：不补零、不删项、不重新归一化


def test_r4_nothing_bought_or_attended_is_a_known_zero(bundle, scoring, start):
    y = live_year(bundle, scoring, start, plan())
    e = comp(y, 1, "personal_enjoyment")
    assert (e.status, e.z, e.s) == ("known", 0, 0)
    assert comp(y, 1, "leisure").participation.entries == ()


# --------------------------------------------------------------------------- R8：期末范围缺口只是说明，不改 V


def test_r8_a_held_racket_adds_a_scope_note_but_does_not_change_v(bundle, scoring, evaluation):
    from econ.life_terminal_value import equipment_valuation_scope

    from .test_life_extension import plan_of, three_years, value

    base = three_years(bundle, scoring, lambda y: [])
    racket = three_years(
        bundle,
        scoring,
        lambda y: (
            [once("k51.durable.tennis_racket", 1), once("k51.durable.guitar", 1)] if y == 3 else []
        ),
    )
    vb, vr = value(bundle, scoring, evaluation, base), value(bundle, scoring, evaluation, racket)
    assert any("racket_playable" in t for t in vr.not_valued)
    assert not any("racket_playable" in t for t in vb.not_valued)
    (scope_racket,) = [
        s
        for s in equipment_valuation_scope(
            racket[-1].next_state, evaluation, bundle.catalog_for(K51)
        )
        if s.opportunity_id == "k51.durable.tennis_racket"
    ]
    assert (scope_racket.covered, scope_racket.uncovered) == ((), ("racket_playable",))
    guitar_only = three_years(
        bundle, scoring, lambda y: [once("k51.durable.guitar", 1)] if y == 3 else []
    )
    vg = value(bundle, scoring, evaluation, guitar_only)
    p1 = "p1_current_basic"
    assert (
        plan_of(vr, p1).v_plan == plan_of(vg, p1).v_plan
    )  # 球拍不进 V：模板没有用到它，也不按购买价补分
