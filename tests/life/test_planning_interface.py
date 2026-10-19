"""E1-7A 规划接口（DECISIONS/0030 第四至六节）：安全请求、只认 JSON 的解析、安全诊断、记忆边界、mock 读输入与检查阶段的处置。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentlayer.life_mock import MALFORMED_TEXT, MockPlannerError, RuleBasedMockPlanner
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
from agentlayer.life_views import (
    ObservationPermission,
    standard_product_permission,
    visible_feedback,
)
from common.life_contracts import LifeContractError, PlanCheck, SettlementProblem, YearAction
from common.life_planning_contracts import MockScript
from econ.life_settlement import binding_from_policy, check_year_plan, settle_year
from ledger.life_city_facts import load_city_facts
from ledger.life_roster import load_roster
from orchestration.life_runner import classify_check

from .planning_support import FACTS
from .settle_support import revise

ROSTER = Path("configs/life/roster_e1_7a.yaml")
HIDDEN = ObservationPermission(
    condition="product_stated_profile",
    show_stated_dimension_points=True,
    show_soft_preferences=True,
    show_hard_requirements=True,
    show_free_text=True,
    show_energy_level=False,
    show_stated_material_points=True,
    permission_version="test-hidden-energy",
)


@pytest.fixture(scope="module")
def env():
    _config, bundle, scoring, _specs = load_roster(ROSTER)
    facts = load_city_facts(FACTS, bundle, scoring)
    return bundle, facts


def _parts(env, clone: str):
    bundle, facts = env
    state = bundle.initial_state(clone)
    catalog = bundle.catalog_for(str(state.city))
    profile = next(p for p in bundle.stated_profiles if p.person_id == state.person_id)
    tax = binding_from_policy(bundle.tax_policy(catalog.tax_policy_id))
    return bundle, facts, state, catalog, profile, tax


def _request(
    env, clone="u101.a95.r1", *, permission=None, state=None, attempt=1, revision=None, memory=None
):
    bundle, facts, st, catalog, profile, _tax = _parts(env, clone)
    st = state or st
    return build_request(
        request_id=f"t/{clone}/{attempt}",
        attempt=attempt,
        attempts_allowed=3,
        permission=permission or standard_product_permission("test-product"),
        profile=profile,
        state=st,
        catalog=catalog,
        protocol=bundle.protocol,
        facts=facts.sheet_for(str(st.city)),
        memory=memory
        or build_memory(clone_id=clone, city=str(st.city), current_year=st.year_index, years=()),
        last_feedback=None,
        revision=revision,
        tax_policy=bundle.tax_policy(catalog.tax_policy_id),
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
    assert _plain(payload)  # 没有任何对象：规划器拿不到内部运行 bundle、研究真值或评分配置
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
    ):
        assert hidden not in text


def test_a_request_refuses_mixed_cities_and_wrong_revision_phases(env):
    bundle, facts, state, catalog, profile, _ = _parts(env, "u101.a95.r1")
    base = dict(
        request_id="t",
        attempts_allowed=3,
        permission=standard_product_permission("p"),
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
        build_request(**base, attempt=1, facts=facts.sheet_for("US:SYNTH2026:B96"), revision=None)
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
                {
                    "opportunity_id": "a95.housing.far",
                    "quantity": 1,
                    "start_month": 1,
                    "end_month": 12,
                },
                {
                    "opportunity_id": "a95.food.basic",
                    "quantity": 1,
                    "start_month": 1,
                    "end_month": 12,
                },
                {
                    "opportunity_id": "a95.job.shop_parttime",
                    "quantity": 1,
                    "start_month": 1,
                    "end_month": 12,
                },
            ],
            "minimum_fallback": ["housing", "food"],
            "emergency": {"minor": "treat_if_possible", "major": "treat_if_possible"},
        },
    }
    data.update(over)
    return data


def test_a_valid_response_becomes_a_year_action_with_identity_filled_by_the_runner():
    parsed = parse_output(
        json.dumps(_output(), ensure_ascii=False),
        person_id="u101",
        clone_id="u101.a95.r0",
        year_index=2,
    )
    assert parsed.diagnostics == () and type(parsed.action) is YearAction
    a = parsed.action
    assert (a.person_id, a.clone_id, a.year_index, a.rationale) == (
        "u101",
        "u101.a95.r0",
        2,
        "收入还低",
    )
    assert "今年先保住房和吃饭" not in json.dumps(
        a.settlement_view(), ensure_ascii=False
    )  # 摘要与理由都不进结算
    fenced = "```json\n" + json.dumps(_output(), ensure_ascii=False) + "\n```"
    assert parse_output(fenced, person_id="u101", clone_id="c", year_index=1).action is not None


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
    parsed = parse_output(text, person_id="u101", clone_id="c", year_index=1)
    assert parsed.action is None and parsed.diagnostics[0].code == code
    assert parsed.diagnostics[0].source == "response_format"


def test_model_output_is_never_executed(tmp_path):
    marker = tmp_path / "executed.txt"
    payload = _output(rationale=f"__import__('pathlib').Path(r'{marker}').write_text('x')")
    parsed = parse_output(json.dumps(payload), person_id="u101", clone_id="c", year_index=1)
    assert parsed.action is not None and not marker.exists()


# ---------------------------------------------------------------------- 诊断


def _overcommit(env, permission):
    bundle, _facts, state, catalog, _profile, tax = _parts(env, "u101.a95.r1")
    payload = request_payload(_request(env, permission=permission))
    mock = RuleBasedMockPlanner(
        (MockScript(clone_id="u101.a95.r1", behavior="overcommit_first_draft", years=(1,)),)
    )
    text = mock.plan(_call(payload))
    parsed = parse_output(text, person_id="u101", clone_id="u101.a95.r1", year_index=1)
    check = check_year_plan(
        protocol=bundle.protocol, state=state, action=parsed.action, catalog=catalog, tax=tax
    )
    return text, parsed, check


def test_energy_diagnostics_follow_the_permission(env):
    _text, parsed, check = _overcommit(env, standard_product_permission("p"))
    assert (check.stage, check.status, classify_check(check)) == (
        "no_event_trial",
        "plan_infeasible",
        "revise",
    )
    shown = plan_diagnostics(check, parsed.action, standard_product_permission("p"))[0]
    assert (
        shown.limit.value == 250 and shown.used.exact == "2343/8" and "公开额度是 250" in shown.text
    )
    _text, parsed, check = _overcommit(env, HIDDEN)
    hidden = plan_diagnostics(check, parsed.action, HIDDEN)[0]
    assert hidden.limit is None and hidden.used is None and hidden.excess is None
    assert "250" not in hidden.text and "292.875" not in hidden.text and "额度不显示" in hidden.text
    assert [c.item for c in hidden.contributors][:2] == [
        "a95.job.shop_lead",
        "a95.job.shop_parttime",
    ]


def test_only_failed_checks_get_diagnostics(env):
    with pytest.raises(LifeContractError):
        plan_diagnostics(
            PlanCheck(stage="passed", passed=True, new_events_possible=True),
            YearAction(person_id="u", clone_id="c", year_index=1, items=()),
            standard_product_permission("p"),
        )


# ---------------------------------------------------------------------- 记忆


def _year(env, clone="u101.a95.r0", **over):
    bundle, *_ = _parts(env, clone)
    _bundle, _facts, state, catalog, _profile, tax = _parts(env, clone)
    action = YearAction.model_validate(
        {
            "person_id": "u101",
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
    m = build_memory(
        clone_id="u101.a95.r0", city="US:SYNTH2026:A95", current_year=2, years=(_year(env),)
    )
    assert m.facts[0].kind == "fact" and m.facts[0].text.startswith("第 1 年：年末现金 3,440.00")
    (report,) = m.self_reports
    assert (
        report.kind == "agent_self_report"
        and report.need_summary == "摘要"
        and not report.truncated
    )


def test_memory_never_mixes_other_clones_cities_or_future_years(env):
    good = _year(env)
    for bad, why in (
        (_year(env, clone_id="u101.a95.r1"), "别的分身"),
        (_year(env, city="US:SYNTH2026:B96"), "别的城"),
    ):
        with pytest.raises(LifeContractError, match=why):
            build_memory(
                clone_id="u101.a95.r0", city="US:SYNTH2026:A95", current_year=2, years=(bad,)
            )
    with pytest.raises(LifeContractError, match="不能有第 1 年的记录"):
        build_memory(clone_id="u101.a95.r0", city="US:SYNTH2026:A95", current_year=1, years=(good,))


def test_memory_truncation_is_explicit(env):
    long = _year(env, rationale="很长的理由" * 200)
    m = build_memory(clone_id="u101.a95.r0", city="US:SYNTH2026:A95", current_year=2, years=(long,))
    assert m.self_reports[0].truncated and m.self_reports[0].rationale.endswith("…（截断）")
    assert len(m.self_reports[0].rationale) <= 400


# ---------------------------------------------------------------------- mock 读输入


def _plan(env, payload, scripts=()):
    return json.loads(RuleBasedMockPlanner(scripts).plan(_call(payload)))


def test_the_mock_reads_cash_and_abilities(env):
    bundle, *_ = _parts(env, "u101.a95.r0")
    state = bundle.initial_state("u101.a95.r0")
    rich = _plan(env, request_payload(_request(env, "u101.a95.r0")))
    poor_state = revise(state, cash={"amount": 100, "numeraire": "USD_2024_LOCAL"})
    poor = _plan(env, request_payload(_request(env, "u101.a95.r0", state=poor_state)))
    ids = lambda p: {i["opportunity_id"] for i in p["action"]["items"]}  # noqa: E731
    assert "a95.training.accounting_course" in ids(rich)
    assert "a95.training.accounting_course" not in ids(
        poor
    )  # 付学费后推算的年末现金不够一个月最低生活
    b96 = _plan(env, request_payload(_request(env, "u101.b96.r0")))
    assert not any("training" in i for i in ids(b96))  # 本城没有要求这项能力的岗位


def test_the_mock_repairs_from_the_diagnostics(env):
    text, parsed, check = _overcommit(env, standard_product_permission("p"))
    diags = plan_diagnostics(check, parsed.action, standard_product_permission("p"))
    rev = revision_block(attempt=2, attempts_allowed=3, previous_output=text, diagnostics=diags)
    payload = request_payload(_request(env, attempt=2, revision=rev))
    fixed = _plan(env, payload)
    jobs = [i["opportunity_id"] for i in fixed["action"]["items"] if ".job." in i["opportunity_id"]]
    assert jobs == ["a95.job.shop_lead"] and "删掉 a95.job.shop_parttime" in fixed["rationale"]


def test_the_mock_scripts_are_explicit(env):
    payload = request_payload(_request(env))
    assert (
        RuleBasedMockPlanner(
            (MockScript(clone_id="u101.a95.r1", behavior="malformed_output", years=(1,)),)
        ).plan(_call(payload))
        == MALFORMED_TEXT
    )
    with pytest.raises(MockPlannerError):
        RuleBasedMockPlanner(
            (MockScript(clone_id="u101.a95.r1", behavior="raise_error", years=(1,)),)
        ).plan(_call(payload))
    a = _plan(env, payload)
    b = _plan(
        env,
        payload,
        (MockScript(clone_id="u101.a95.r1", behavior="alternate_wording", years=(1,)),),
    )
    assert a["action"] == b["action"] and a["rationale"] != b["rationale"]


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
    bundle, _facts, state, catalog, _profile, tax = _parts(env, "u101.a95.r0")
    good = YearAction.model_validate(
        {"person_id": "u101", "clone_id": "u101.a95.r0", "year_index": 1, **_output()["action"]}
    )
    passed = check_year_plan(
        protocol=bundle.protocol, state=state, action=good, catalog=catalog, tax=tax
    )
    assert passed.passed and passed.stage == "passed" and not hasattr(passed, "outcome")
    bad = good.model_copy(
        update={
            "items": (*good.items, good.items[0].model_copy(update={"opportunity_id": "a95.nope"}))
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
    # 与结算同一份检查：没通过的计划，结算返回同一个状态与同一份问题
    assert (
        settle_year(
            protocol=bundle.protocol, state=state, action=bad, catalog=catalog, tax=tax
        ).problems
        == plan.problems
    )


# ---------------------------------------------------------------------- 真实引擎：无事件试算里的资料缺口可以修订


def test_a_known_illness_meeting_unknown_treatment_data_is_a_revisable_plan_problem(env):
    """第 1 年第 12 月大病、计划不治 → 第 2 年年初病还在（已知事实）；第 2 年计划写"条件允许就治"，而 B96 的住院资料未知：
    没有新随机事件的试算在第 1 月就遇到资料缺口（data_error / treatment_data_unknown）。这是年初就能看到的计划问题——可以改成
    不治；不是事后的停止，也不是城市资料损坏。mock 读诊断与观察里的治疗资料，把大病改成不治，计划通过。"""
    from common.life_contracts import IllnessEventSource
    from econ.life_year import ScoringInputs, run_life_year

    from .settle_support import revise as rev

    bundle, facts = env
    _cfg, _b, scoring, _specs = load_roster(ROSTER)
    clone = "u101.b96.r0"
    src = IllnessEventSource.model_validate(
        {
            "mode": "injected",
            "stream_version": "e1-5c-illness-stream-v1",
            "note": "测试用事件来源",
            "injected": [
                {
                    "person_id": "u101",
                    "repeat_index": 0,
                    "year_index": 1,
                    "month": 12,
                    "severity": "major",
                    "note": "测试：不治，延续到第 2 年",
                }
            ],
        }
    )
    proto = rev(bundle.protocol, illness_events=src)
    _bundle, _facts, state, catalog, profile, tax = _parts(env, clone)
    inputs = ScoringInputs(
        spec=scoring.spec,
        conditions=scoring.conditions_for(str(state.city)),
        preference_input=profile,
        hard_requirements=profile.hard_requirements,
    )
    items = [
        {"opportunity_id": oid, "quantity": 1, "start_month": 1, "end_month": 12}
        for oid in ("b96.housing.far", "b96.food.basic", "b96.job.shop_parttime")
    ]

    def action(year, choice):
        return YearAction.model_validate(
            {
                "person_id": "u101",
                "clone_id": clone,
                "year_index": year,
                "items": items,
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
    permission = standard_product_permission("p")
    diags = plan_diagnostics(check, treat, permission)
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
    rev_block = revision_block(
        attempt=2, attempts_allowed=3, previous_output=text, diagnostics=diags
    )
    request = build_request(
        request_id="t/data-gap",
        attempt=2,
        attempts_allowed=3,
        permission=permission,
        profile=profile,
        state=start2,
        catalog=catalog,
        protocol=proto,
        facts=facts.sheet_for(str(start2.city)),
        memory=build_memory(clone_id=clone, city=str(start2.city), current_year=2, years=()),
        last_feedback=None,
        revision=rev_block,
        tax_policy=bundle.tax_policy(catalog.tax_policy_id),
    )
    fixed = json.loads(RuleBasedMockPlanner().plan(_call(request_payload(request))))
    assert fixed["action"]["emergency"]["major"] == "decline"
    repaired = parse_output(
        json.dumps(fixed, ensure_ascii=False), person_id="u101", clone_id=clone, year_index=2
    )
    assert check_year_plan(
        protocol=proto, state=start2, action=repaired.action, catalog=catalog, tax=tax
    ).passed
