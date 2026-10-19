"""规划接口（DECISIONS/0030 第四至六节）：安全请求、只认 JSON 的解析、安全诊断、记忆边界、mock 读输入与检查阶段的处置。

样例是产品协议样例（u301，K51 / M52）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentlayer.life_mock import MALFORMED_TEXT, MockPlannerError, ProductMockPlanner
from agentlayer.life_planning import (
    MemoryYear,
    PlannerCall,
    build_memory,
    build_request,
    parse_output,
    plan_diagnostics,
    render_prompt,
    request_payload,
    revision_block,
)
from agentlayer.life_views import standard_product_permission, visible_feedback
from common.life_contracts import (
    IllnessEventSource,
    LifeContractError,
    PlanCheck,
    SettlementProblem,
    YearAction,
)
from common.life_planning_contracts import MockScript
from econ.life_settlement import binding_from_policy, check_year_plan, settle_year
from econ.life_year import ScoringInputs, run_life_year
from ledger.life_city_facts import load_city_facts
from ledger.life_roster import load_roster
from orchestration.life_runner import classify_check

from .planning_support import FACTS
from .settle_support import revise

ROSTER = Path("configs/life/roster_product.yaml")
K, M = "u301.k51.r0", "u301.m52.r0"
K51, M52 = "US:SYNTH2026:K51", "US:SYNTH2026:M52"
SHOWN = standard_product_permission("test-product")
HIDDEN = SHOWN.model_copy(
    update={"show_energy_level": False, "permission_version": "test-hidden-energy"}
)


@pytest.fixture(scope="module")
def env():
    _config, bundle, scoring, _specs = load_roster(ROSTER)
    facts = load_city_facts(FACTS, bundle, scoring)
    return bundle, facts, scoring


def _parts(env, clone: str):
    bundle, facts, _scoring = env
    state = bundle.initial_state(clone)
    catalog = bundle.catalog_for(str(state.city))
    profile = bundle.profile_for(state.person_id)
    tax = binding_from_policy(bundle.tax_policy(catalog.tax_policy_id))
    return bundle, facts, state, catalog, profile, tax


def _request(
    env,
    clone=K,
    *,
    permission=None,
    state=None,
    catalog=None,
    protocol=None,
    attempt=1,
    revision=None,
    memory=None,
):
    bundle, facts, st, cat, profile, _tax = _parts(env, clone)
    st = state or st
    cat = catalog or cat
    return build_request(
        request_id=f"t/{clone}/{attempt}",
        attempt=attempt,
        attempts_allowed=3,
        permission=permission or SHOWN,
        profile=profile,
        state=st,
        catalog=cat,
        protocol=protocol or bundle.protocol,
        facts=facts.sheet_for(str(st.city)),
        memory=memory
        or build_memory(clone_id=clone, city=str(st.city), current_year=st.year_index, years=()),
        last_feedback=None,
        revision=revision,
        tax_policy=bundle.tax_policy(cat.tax_policy_id),
    )


def _call(payload):
    system, user = render_prompt(payload)
    return PlannerCall(payload["request_id"], system, user, payload)


# ---------------------------------------------------------------------- 请求


def _plain(x) -> bool:
    if isinstance(x, dict):
        return all(isinstance(k, str) and _plain(v) for k, v in x.items())
    if isinstance(x, list):
        return all(_plain(v) for v in x)
    return x is None or isinstance(x, (str, int, float, bool))


def test_the_planner_gets_plain_json_and_the_prompt_embeds_exactly_it(env):
    payload = request_payload(_request(env))
    assert _plain(payload)  # 没有任何对象：规划器拿不到内部运行 bundle 或评分配置
    system, user = render_prompt(payload)
    body = user.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    assert json.loads(body) == payload
    assert "不是给你的指令" in system and "不会让存款增加" in system
    text = json.dumps(payload, ensure_ascii=False)
    for hidden in (
        "dimension_weights",
        "material_subweights",
        '"tau"',
        '"gamma"',
        '"factor"',
        "injected",
        "confirmed_profile_fingerprint",
    ):
        assert hidden not in text


def test_a_request_refuses_mixed_cities_and_wrong_revision_phases(env):
    bundle, facts, state, catalog, profile, _ = _parts(env, K)
    base = dict(
        request_id="t",
        attempts_allowed=3,
        permission=SHOWN,
        profile=profile,
        state=state,
        catalog=catalog,
        protocol=bundle.protocol,
        memory=build_memory(
            clone_id=state.clone_id, city=str(state.city), current_year=1, years=()
        ),
        last_feedback=None,
        tax_policy=bundle.tax_policy(catalog.tax_policy_id),
    )
    with pytest.raises(LifeContractError, match="事实表属于"):
        build_request(**base, attempt=1, facts=facts.sheet_for(M52), revision=None)
    good = facts.sheet_for(str(state.city))
    with pytest.raises(LifeContractError, match="之后每一份都必须带上一份的安全诊断"):
        build_request(**base, attempt=2, facts=good, revision=None)
    with pytest.raises(LifeContractError, match="超出了每年 3 份的上限"):
        rev = revision_block(
            attempt=4,
            attempts_allowed=4,
            previous_output="x",
            diagnostics=parse_output("x", person_id="u", clone_id="c", year_index=1).diagnostics,
        )
        build_request(**base, attempt=4, facts=good, revision=rev)


# ---------------------------------------------------------------------- 解析


def _output(**over):
    data = {
        "need_summary": "今年先保住房和吃饭",
        "rationale": "收入还低",
        "action": {
            "items": [
                {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12}
                for oid in ("k51.housing.far", "k51.food.basic", "k51.job.shop_parttime")
            ],
            "minimum_fallback": ["housing", "food"],
            "emergency": {"minor": "treat_if_possible", "major": "treat_if_possible"},
        },
    }
    data.update(over)
    return data


def test_a_valid_response_becomes_a_year_action_with_identity_filled_by_the_runner():
    parsed = parse_output(
        json.dumps(_output(), ensure_ascii=False), person_id="u301", clone_id=K, year_index=2
    )
    assert parsed.diagnostics == () and type(parsed.action) is YearAction
    a = parsed.action
    assert (a.person_id, a.clone_id, a.year_index, a.rationale) == ("u301", K, 2, "收入还低")
    assert "今年先保住房和吃饭" not in json.dumps(
        a.settlement_view(), ensure_ascii=False
    )  # 摘要与理由都不进结算
    fenced = "```json\n" + json.dumps(_output(), ensure_ascii=False) + "\n```"
    assert parse_output(fenced, person_id="u301", clone_id="c", year_index=1).action is not None


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("今年想多赚钱", "format_not_json"),
        ("[1, 2]", "format_not_object"),
        (json.dumps(_output(cash_after=99999)), "format_extra_forbidden"),
        (
            json.dumps(_output(action={**_output()["action"], "person_id": "u999"})),
            "format_extra_forbidden",
        ),
        ("x" * 20001, "format_too_long"),
    ],
)
def test_malformed_or_overreaching_responses_are_format_errors(text, code):
    parsed = parse_output(text, person_id="u301", clone_id="c", year_index=1)
    assert parsed.action is None and parsed.diagnostics[0].code == code
    assert parsed.diagnostics[0].source == "response_format"


def test_a_response_without_an_emergency_plan_is_a_format_error():
    """生病时怎么办必须写；漏了不补成"不治"，而是要求修订。"""
    action = {k: v for k, v in _output()["action"].items() if k != "emergency"}
    parsed = parse_output(
        json.dumps(_output(action=action), ensure_ascii=False),
        person_id="u301",
        clone_id="c",
        year_index=1,
    )
    assert parsed.action is None and parsed.diagnostics[0].source == "response_format"


def test_model_output_is_never_executed(tmp_path):
    marker = tmp_path / "executed.txt"
    payload = _output(rationale=f"__import__('pathlib').Path(r'{marker}').write_text('x')")
    parsed = parse_output(json.dumps(payload), person_id="u301", clone_id="c", year_index=1)
    assert parsed.action is not None and not marker.exists()


# ---------------------------------------------------------------------- 诊断


def _overcommit(env, permission):
    """K51 第 1 年：mock 第一份草稿保留文员、再加一份兼职，负担超过每月精力额度。"""
    bundle, _facts, state, catalog, _profile, tax = _parts(env, K)
    payload = request_payload(_request(env, permission=permission))
    mock = ProductMockPlanner(
        (MockScript(clone_id=K, behavior="overcommit_first_draft", years=(1,)),)
    )
    text = mock.plan(_call(payload))
    parsed = parse_output(text, person_id="u301", clone_id=K, year_index=1)
    check = check_year_plan(
        protocol=bundle.protocol, state=state, action=parsed.action, catalog=catalog, tax=tax
    )
    return text, parsed, check


def _energy(diagnostics):
    return next(d for d in diagnostics if d.code == "energy_over_limit")


def test_energy_diagnostics_follow_the_permission(env):
    _text, parsed, check = _overcommit(env, SHOWN)
    assert (check.stage, check.status, classify_check(check)) == (
        "no_event_trial",
        "plan_infeasible",
        "revise",
    )
    shown = _energy(plan_diagnostics(check, parsed.action, SHOWN))
    assert shown.limit.value == 250 and shown.used.value > 250
    assert "公开额度是 250" in shown.text
    _text, parsed, check = _overcommit(env, HIDDEN)
    hidden = _energy(plan_diagnostics(check, parsed.action, HIDDEN))
    assert hidden.limit is None and hidden.used is None and hidden.excess is None
    assert "250" not in hidden.text and shown.used.exact not in hidden.text
    assert "额度不显示" in hidden.text
    assert [c.item for c in hidden.contributors][:2] == ["k51.job.clerk", "k51.job.shop_parttime"]


def test_only_failed_checks_get_diagnostics():
    with pytest.raises(LifeContractError):
        plan_diagnostics(
            PlanCheck(stage="passed", passed=True, new_events_possible=True),
            YearAction.model_validate(
                {
                    "person_id": "u",
                    "clone_id": "c",
                    "year_index": 1,
                    "items": [],
                    "emergency": _output()["action"]["emergency"],
                }
            ),
            SHOWN,
        )


# ---------------------------------------------------------------------- 记忆


def _year(env, clone=K, **over):
    bundle, _facts, state, catalog, _profile, tax = _parts(env, clone)
    action = YearAction.model_validate(
        {
            "person_id": "u301",
            "clone_id": clone,
            "year_index": 1,
            "rationale": "r",
            **_output()["action"],
        }
    )
    result = settle_year(
        protocol=bundle.protocol, state=state, action=action, catalog=catalog, tax=tax
    )
    fields = dict(
        clone_id=clone,
        city=str(state.city),
        year_index=1,
        feedback=visible_feedback(result),
        need_summary="摘要",
        rationale="理由",
    )
    fields.update(over)
    return MemoryYear(**fields)


def test_memory_keeps_facts_and_marks_self_reports(env):
    m = build_memory(clone_id=K, city=K51, current_year=2, years=(_year(env),))
    assert m.facts[0].kind == "fact" and m.facts[0].text.startswith("第 1 年：年末现金 ")
    (report,) = m.self_reports
    assert report.kind == "agent_self_report" and report.need_summary == "摘要"
    assert not report.truncated


def test_memory_never_mixes_other_clones_cities_or_future_years(env):
    good = _year(env)
    for bad, why in (
        (_year(env, clone_id="u301.k51.r1"), "别的分身"),
        (_year(env, city=M52), "别的城"),
    ):
        with pytest.raises(LifeContractError, match=why):
            build_memory(clone_id=K, city=K51, current_year=2, years=(bad,))
    with pytest.raises(LifeContractError, match="不能有第 1 年的记录"):
        build_memory(clone_id=K, city=K51, current_year=1, years=(good,))


def test_memory_truncation_is_explicit(env):
    long = _year(env, rationale="很长的理由" * 200)
    m = build_memory(clone_id=K, city=K51, current_year=2, years=(long,))
    assert m.self_reports[0].truncated and m.self_reports[0].rationale.endswith("…（截断）")
    assert len(m.self_reports[0].rationale) <= 400


# ---------------------------------------------------------------------- mock 读输入


def _plan(payload, scripts=()):
    return json.loads(ProductMockPlanner(scripts).plan(_call(payload)))


def _ids(plan):
    return {i["opportunity_id"] for i in plan["action"]["items"]}


def test_the_mock_reads_cash(env):
    bundle, *_ = _parts(env, K)
    state = bundle.initial_state(K)
    rich = _plan(request_payload(_request(env)))
    poor_state = revise(state, cash={"amount": 0, "numeraire": "USD_2024_LOCAL"})
    poor = _plan(request_payload(_request(env, state=poor_state)))
    assert "k51.training.accounting_course" in _ids(rich)
    # 年初现金 0：1 月领到文员工资、付完住房与食品后约 2,500 − 1,380 = 1,120，付不起 1,200 的学费，不报名
    assert "k51.training.accounting_course" not in _ids(poor)


def test_the_mock_repairs_from_the_diagnostics(env):
    text, parsed, check = _overcommit(env, SHOWN)
    diags = plan_diagnostics(check, parsed.action, SHOWN)
    rev = revision_block(attempt=2, attempts_allowed=3, previous_output=text, diagnostics=diags)
    fixed = _plan(request_payload(_request(env, attempt=2, revision=rev)))
    jobs = [i for i in sorted(_ids(fixed)) if ".job." in i]
    assert jobs == ["k51.job.clerk"] and "删掉 k51.job.shop_parttime" in fixed["rationale"]


def test_the_mock_scripts_are_explicit(env):
    payload = request_payload(_request(env))
    assert (
        ProductMockPlanner((MockScript(clone_id=K, behavior="malformed_output", years=(1,)),)).plan(
            _call(payload)
        )
        == MALFORMED_TEXT
    )
    with pytest.raises(MockPlannerError):
        ProductMockPlanner((MockScript(clone_id=K, behavior="raise_error", years=(1,)),)).plan(
            _call(payload)
        )
    a = _plan(payload)
    b = _plan(payload, (MockScript(clone_id=K, behavior="alternate_wording", years=(1,)),))
    assert a["action"] == b["action"] and a["rationale"] != b["rationale"]


def test_the_mock_emergency_policy_is_fixed_not_read_from_health_answers(env):
    """mock 的应急是固定测试政策（小病、大病都 treat_if_possible），不从用户健康回答推断。"""
    bundle, *_ = _parts(env, K)
    profile = bundle.profile_for("u301")
    payload = request_payload(_request(env))
    plan = _plan(payload)
    assert (plan["action"]["emergency"]["minor"], plan["action"]["emergency"]["major"]) == (
        "treat_if_possible",
        "treat_if_possible",
    )
    flipped = [a.model_copy(update={"option": "agree"}) for a in profile.health_answers]
    other = json.loads(json.dumps(payload))
    other["observation"]["profile"]["health_answers"] = [
        {**shown, "option": "agree"} for shown in other["observation"]["profile"]["health_answers"]
    ]
    assert flipped and _plan(other)["action"] == plan["action"]


# ---------------------------------------------------------------------- 检查阶段与处置


def _check(stage, status, *codes):
    return PlanCheck(
        stage=stage,
        passed=False,
        status=status,
        problems=tuple(SettlementProblem(code=c, message=f"问题 {c}") for c in codes),
        new_events_possible=True,
    )


@pytest.mark.parametrize(
    ("check", "want"),
    [
        (PlanCheck(stage="passed", passed=True, new_events_possible=True), "accept"),
        (_check("plan", "input_error", "unknown_opportunity", "qualification_missing"), "revise"),
        (_check("plan", "input_error", "identity"), "internal"),
        (_check("no_event_trial", "plan_infeasible", "energy_over_limit"), "revise"),
        (_check("no_event_trial", "data_error", "treatment_data_unknown"), "revise"),
        (_check("no_event_trial", "data_error", "resource_data"), "city_data"),
        (_check("city_data", "data_error", "catalog_data"), "city_data"),
        (_check("state", "input_error", "bad_holding"), "internal"),
        (_check("state_status", "input_error", "state_not_active"), "internal"),
        (_check("arguments", "input_error", "argument_type"), "internal"),
    ],
)
def test_revisability_depends_on_stage_and_code_not_on_the_status_string(check, want):
    assert classify_check(check) == want


def test_check_year_plan_stops_at_the_right_stage_and_commits_nothing(env):
    bundle, _facts, state, catalog, _profile, tax = _parts(env, K)
    good = YearAction.model_validate(
        {"person_id": "u301", "clone_id": K, "year_index": 1, **_output()["action"]}
    )
    passed = check_year_plan(
        protocol=bundle.protocol, state=state, action=good, catalog=catalog, tax=tax
    )
    assert passed.passed and passed.stage == "passed" and not hasattr(passed, "outcome")
    bad = good.model_copy(
        update={
            "items": (*good.items, good.items[0].model_copy(update={"opportunity_id": "k51.nope"}))
        }
    )
    plan = check_year_plan(
        protocol=bundle.protocol, state=state, action=bad, catalog=catalog, tax=tax
    )
    assert (plan.stage, plan.status, plan.problems[0].code) == (
        "plan",
        "input_error",
        "unknown_opportunity",
    )
    terminal = state.model_copy(update={"status": "terminal", "year_index": 4})
    gone = check_year_plan(
        protocol=bundle.protocol, state=terminal, action=good, catalog=catalog, tax=tax
    )
    assert (gone.stage, classify_check(gone)) == ("state_status", "internal")
    # 与结算同一份检查：没通过的计划，结算返回同一份问题
    assert (
        settle_year(
            protocol=bundle.protocol, state=state, action=bad, catalog=catalog, tax=tax
        ).problems
        == plan.problems
    )


# ---------------------------------------------------------------------- 无事件试算里的资料缺口可以修订


def _hospital_unknown(catalog):
    data = catalog.model_dump(mode="json")
    for t in data["treatments"]:
        if t["severity"] == "major":
            for k in ("price", "visit_hours", "burden_per_hour"):
                t.pop(k, None)
            t["availability"] = "unknown"
            t["unknown_reason"] = (
                "合成情景：这座城住院的价格与床位没有采集到——未知不是免费，也不是没有"
            )
            t["label"] = "住院治疗（资料未知）"
    return type(catalog).model_validate(data)


def test_a_known_illness_meeting_unknown_treatment_data_is_a_revisable_plan_problem(env):
    """第 1 年第 12 月大病、计划不治 → 第 2 年年初病还在（已知事实）；第 2 年计划写"条件允许就治"，而这座城的住院资料未知：
    没有新随机事件的试算在第 1 月就遇到资料缺口（data_error / treatment_data_unknown）。这是年初就能看到的计划问题——可以改成
    不治；不是事后的停止，也不是城市资料损坏。mock 读诊断与观察里的治疗资料，把大病改成不治，计划通过。"""
    bundle, _facts, scoring = env
    src = IllnessEventSource.model_validate(
        {
            "mode": "injected",
            "stream_version": "e1-5c-illness-stream-v1",
            "note": "测试用事件来源",
            "injected": [
                {
                    "person_id": "u301",
                    "repeat_index": 0,
                    "year_index": 1,
                    "month": 12,
                    "severity": "major",
                    "note": "测试：不治，延续到第 2 年",
                }
            ],
        }
    )
    proto = revise(bundle.protocol, illness_events=src)
    _bundle, _facts, state, catalog, profile, tax = _parts(env, K)
    catalog = _hospital_unknown(catalog)
    inputs = ScoringInputs(
        spec=scoring.spec,
        conditions=scoring.conditions_for(str(state.city)),
        preference_input=profile,
        hard_requirements=profile.hard_requirements,
    )

    def action(year, choice):
        return YearAction.model_validate(
            {
                "person_id": "u301",
                "clone_id": K,
                "year_index": year,
                "items": _output()["action"]["items"],
                "minimum_fallback": ["housing", "food"],
                "emergency": {"minor": choice, "major": choice},
            }
        )

    y1 = run_life_year(
        protocol=proto,
        state=state,
        action=action(1, "decline"),
        catalog=catalog,
        tax=tax,
        scoring=inputs,
    )
    start2 = y1.next_state
    assert y1.handoff == "next_year_ready" and start2.health.episode.severity == "major"
    treat = action(2, "treat_if_possible")
    check = check_year_plan(protocol=proto, state=start2, action=treat, catalog=catalog, tax=tax)
    assert (check.stage, check.status, [p.code for p in check.problems]) == (
        "no_event_trial",
        "data_error",
        ["treatment_data_unknown"],
    )
    assert classify_check(check) == "revise"
    diags = plan_diagnostics(check, treat, SHOWN)
    text = json.dumps(
        {
            "need_summary": "病还没好",
            "rationale": "条件允许就治",
            "action": json.loads(
                treat.model_dump_json(include={"items", "minimum_fallback", "emergency"})
            ),
        },
        ensure_ascii=False,
    )
    rev = revision_block(attempt=2, attempts_allowed=3, previous_output=text, diagnostics=diags)
    request = _request(
        env,
        state=start2,
        catalog=catalog,
        protocol=proto,
        attempt=2,
        revision=rev,
        memory=build_memory(clone_id=K, city=str(start2.city), current_year=2, years=()),
    )
    fixed = _plan(request_payload(request))
    assert fixed["action"]["emergency"]["major"] == "decline"
    repaired = parse_output(
        json.dumps(fixed, ensure_ascii=False), person_id="u301", clone_id=K, year_index=2
    )
    assert check_year_plan(
        protocol=proto, state=start2, action=repaired.action, catalog=catalog, tax=tax
    ).passed
