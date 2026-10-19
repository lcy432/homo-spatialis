"""E1-7A 运行器的闭环验收（DECISIONS/0030 第十一节的 16 项，加预算、城市资料问题与拒绝启动）。

全部经正式入口（preflight / start_run / resume_run / replay_run），mock 规划器，零网络；变体配置只写在 pytest 的临时目录。
"""

from __future__ import annotations

import dataclasses
import json
import socket
from fractions import Fraction

import pytest

from agentlayer.life_mock import RuleBasedMockPlanner
from agentlayer.life_views import assert_payload_clean, visible_feedback
from common.life_contracts import (
    LifeContractError,
    PlanCheck,
    SettlementProblem,
    YearAction,
    model_fingerprint,
)
from common.life_planning_contracts import PlannerIdentity
from common.run_store import RunStore
from econ.life_evaluation import evaluate_clone
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from orchestration import life_runner
from orchestration.life_runner import (
    RunInterrupted,
    RunRefusedError,
    preflight,
    replay_run,
    resume_run,
    start_run,
)

from .planning_support import (
    ARM,
    RUN_ID,
    batch,
    copy_run,
    default_world,
    facts_variant,
    records,
    request,
    run_config_variant,
    snapshot,
    write_records,
)


@pytest.fixture(scope="module")
def main(tmp_path_factory):
    world = default_world()
    out = tmp_path_factory.mktemp("e17a") / "run"
    outcome, planner = batch(world, out)
    return world, out, outcome, planner


def _records(outcome):
    return {r.clone_id: r for r in outcome.records}


def _report(outcome, user, city):
    return next(
        cr
        for cr in outcome.reports[ARM].city_reports
        if cr.user_id == user and cr.city.code == city
    )


def _direct(world, clone, actions, planning_stop=None):
    entry = next(e for e in world.roster.entries if e.clone_id == clone)
    city = str(entry.city)
    catalog = world.bundle.catalog_for(city)
    tax = binding_from_policy(world.bundle.tax_policy(catalog.tax_policy_id))
    profile = next(p for p in world.bundle.stated_profiles if p.person_id == entry.person_id)
    inputs = ScoringInputs(
        spec=world.scoring.spec,
        conditions=world.scoring.conditions_for(city),
        preference_input=profile,
        hard_requirements=profile.hard_requirements,
    )
    state = world.bundle.initial_state(clone)
    results = []
    for a in actions:
        r = run_life_year(
            protocol=world.bundle.protocol,
            state=state,
            action=a,
            catalog=catalog,
            tax=tax,
            scoring=inputs,
        )
        results.append(r)
        state = r.next_state
    ev = evaluate_clone(
        spec=world.specs[ARM],
        protocol=world.bundle.protocol,
        catalog=catalog,
        tax=tax,
        scoring=inputs,
        results=results,
        planning_stop=planning_stop,
    )
    return results, ev


def _actions(out, clone):
    return [
        YearAction.model_validate(a["action"])
        for a in records(out, "accepted")
        if a["clone_id"] == clone
    ]


# ---------------------------------------------------------------------- 1–3 闭环、状态延续、对输入的反应


def test_a1_the_mock_lives_three_years_and_e1_6_reports_everyone(main):
    world, out, outcome, planner = main
    assert outcome.status == "completed" and outcome.calls_used == planner.calls == 27
    records = _records(outcome)
    assert list(records) == list(world.order) and len(records) == 10
    assert (
        records["u101.a95.r0"].years_committed == 3 and records["u101.a95.r0"].status == "completed"
    )
    reports = outcome.reports[ARM].city_reports
    assert {(cr.user_id, cr.city.code) for cr in reports} == {
        ("user.alpha", "A95"),
        ("user.alpha", "B96"),
        ("user.beta", "A95"),
        ("user.beta", "B96"),
    }
    assert sum(cr.complete + cr.exits + cr.missing for cr in reports) == 10
    assert (out / "report.md").read_text(encoding="utf-8").count("| u10") == 10


def test_a2_year_two_reads_the_real_year_one_facts(main):
    _world, out, outcome, _ = main
    checked = 0
    for clone, lives in outcome.lives.items():
        for prev in lives:
            if prev.handoff != "next_year_ready":
                continue
            obs = request(out, clone, prev.year_index + 1)["observation"]
            nxt = prev.next_state
            assert obs["cash"]["amount"] == nxt.cash.amount
            assert [h["opportunity_id"] for h in obs["holdings"]] == [
                h.opportunity_id for h in nxt.holdings
            ]
            assert [a["ability_id"] for a in obs["abilities"]] == [
                a.ability_id for a in nxt.abilities
            ]
            assert [(e["onset_year"], e["onset_month"]) for e in obs["health"]["history"]] == [
                (e.onset_year, e.onset_month) for e in nxt.health.history
            ]
            assert obs["energy_level"] == nxt.energy.level
            assert obs["last_feedback"] == json.loads(
                visible_feedback(prev.settlement).model_dump_json()
            )
            checked += 1
    assert checked == 14  # 每一次正常交接之后的第一份请求（含规划没有完成的 B96 r3 第 2 年）
    y3 = request(out, "u101.a95.r1", 3)["observation"]  # 第 2 年第 5 月的大病出现在第 3 年的病史里
    assert [
        (e["onset_year"], e["onset_month"], e["recovery"]) for e in y3["health"]["history"]
    ] == [(2, 5, "治疗后")]


def test_a3_the_mock_responds_to_what_it_is_shown(main):
    _world, out, _outcome, _ = main
    jobs = lambda clone, y: {  # noqa: E731
        o["opportunity_id"]: (o.get("qualification") or {}).get("status")
        for o in request(out, clone, y)["observation"]["opportunities"]
        if o["category"] == "job"
    }
    assert jobs("u101.a95.r0", 1)["a95.job.accountant_office"] == "not_yet"
    assert jobs("u101.a95.r0", 2)["a95.job.accountant_office"] == "available_now"
    plans = {
        a.year_index: {i.opportunity_id for i in a.items} for a in _actions(out, "u101.a95.r0")
    }
    assert "a95.job.shop_lead" in plans[1] and "a95.job.accountant_office" in plans[2]
    assert "a95.job.office_senior" in plans[3]  # 第 3 年年初已在合成办公室：高级会计现在能选
    beta = {a.year_index: {i.opportunity_id for i in a.items} for a in _actions(out, "u102.b96.r0")}
    cash = [request(out, "u102.b96.r0", y)["observation"]["cash"]["amount"] for y in (1, 2, 3)]
    assert cash == [4000.0, 5440.0, 2080.0]
    assert ["b96.housing.near" in beta[y] for y in (1, 2, 3)] == [
        False,
        True,
        False,
    ]  # 现金够的那一年才满足硬要求


# ---------------------------------------------------------------------- 4 只改理由


def test_a4_same_actions_different_words_same_life(main, tmp_path):
    _world, out, outcome, _ = main

    def wording(run):
        run["planner"]["scripts"] = [
            *run["planner"]["scripts"],
            *(
                {"clone_id": c, "behavior": "alternate_wording", "years": [1, 2, 3]}
                for c in ("u101.a95.r0", "u101.a95.r1", "u102.b96.r0")
            ),
        ]

    w2 = preflight(run_config_variant(tmp_path, wording))
    alt, _ = batch(w2, tmp_path / "run")
    for clone in ("u101.a95.r0", "u101.a95.r1", "u102.b96.r0"):
        a, b = _actions(out, clone), _actions(tmp_path / "run", clone)
        assert [x.settlement_view() for x in a] == [y.settlement_view() for y in b]
        assert [x.rationale for x in a] != [y.rationale for y in b]
        for x, y in zip(outcome.lives[clone], alt.lives[clone], strict=True):
            assert model_fingerprint(x.settlement.outcome) == model_fingerprint(
                y.settlement.outcome
            )
            assert model_fingerprint(x.score) == model_fingerprint(y.score)
        e1 = next(e for e in outcome.evaluations[ARM] if e.clone_id == clone)
        e2 = next(e for e in alt.evaluations[ARM] if e.clone_id == clone)
        assert (e1.actual.l_actual, e1.valuation.v_reference, e1.combined.j) == (
            e2.actual.l_actual,
            e2.valuation.v_reference,
            e2.combined.j,
        )


# ---------------------------------------------------------------------- 5 可见事实与执行配置冲突


def test_a5_conflicting_facts_are_refused_before_starting(tmp_path):
    facts = facts_variant(tmp_path, lambda d: d["sheets"][1]["facts"][3].update(number=8))
    path = run_config_variant(tmp_path, lambda r: r.update(city_facts=facts.as_posix()))
    with pytest.raises(RunRefusedError, match="评分条件写的是 b = 3"):
        preflight(path)


def test_a5_a_catalog_swapped_after_preflight_is_not_executed(tmp_path):
    world = default_world()
    data = world.bundle.model_dump(mode="python")
    for c in data["catalogs"]:
        for o in c["opportunities"]:
            if o["opportunity_id"] == "a95.housing.far":
                o["price"]["amount"]["amount"] = 1400.0  # 看到 1,000 却按 1,400 扣款：不允许
    swapped = dataclasses.replace(world, bundle=type(world.bundle).model_validate(data))
    outcome, planner = batch(swapped, tmp_path / "run")
    first = _records(outcome)["u101.a95.r0"]
    assert outcome.status == "paused" and planner.calls == 0
    assert first.status == "program_error" and "目录与运行清单记下的身份不同" in first.detail
    assert all(r.status == "not_run" for r in outcome.records[1:])


# ---------------------------------------------------------------------- 6 信息许可


def test_a6_every_request_respects_the_permission(tmp_path):
    def hide(run):
        run["information"].update(
            show_energy_level=False,
            show_stated_dimension_points=False,
            show_stated_material_points=False,
            show_soft_preferences=False,
            permission_version="test-hidden",
        )

    world = preflight(run_config_variant(tmp_path, hide))
    outcome, _ = batch(world, tmp_path / "run")
    assert outcome.status == "completed"
    notes = [e.note for e in world.bundle.protocol.illness_events.injected]
    calls = records(tmp_path / "run", "calls")
    for c in calls:
        p = c["request"]
        assert_payload_clean(p)
        obs = p["observation"]
        assert obs["energy_level"] is None and obs["resources"]["monthly_energy_quota"] is None
        assert (
            obs["profile"]["dimension_points"] is None and obs["profile"]["material_points"] is None
        )
        if obs["last_feedback"] is not None:
            assert all(m["energy_used"] is None for m in obs["last_feedback"]["resources"])
        text = json.dumps(p, ensure_ascii=False)
        assert not any(n in text for n in notes)  # 不预告指定疾病事件
        for e in obs["health"]["history"]:
            assert e["onset_year"] < p["year_index"]
        for d in (p.get("revision") or {}).get("diagnostics", []):
            assert d["limit"] is None and d["used"] is None and d["excess"] is None
            assert "250" not in d["text"]
        for f in p["memory"]["facts"]:
            assert "负担点" not in f["text"] and "额度" not in f["text"]
    revision = request(tmp_path / "run", "u101.a95.r1", 1, 2)["revision"]
    assert (
        revision["diagnostics"][0]["code"] == "energy_over_limit"
        and "额度不显示" in revision["diagnostics"][0]["text"]
    )


# ---------------------------------------------------------------------- 7–8 修订与修订上限；不可行不留痕


def test_a7_format_and_qualification_errors_are_revised_within_the_limit(tmp_path):
    def scripts(run):
        run["planner"]["scripts"] = [
            *run["planner"]["scripts"],
            {"clone_id": "u101.a95.r0", "behavior": "unqualified_first_draft", "years": [1]},
        ]

    world = preflight(run_config_variant(tmp_path, scripts))
    outcome, _ = batch(world, tmp_path / "run")
    rec = _records(outcome)
    assert rec["u101.a95.r0"].attempts_by_year[0] == 2 and rec["u101.a95.r0"].status == "completed"
    first = next(
        a
        for a in records(tmp_path / "run", "attempts")
        if (a["clone_id"], a["year_index"], a["attempt"]) == ("u101.a95.r0", 1, 1)
    )
    assert first["check_stage"] == "plan" and "qualification_missing" in first["problem_codes"]
    assert first["disposition"] == "revise"
    assert (
        rec["u101.b96.r3"].attempts_by_year == (1, 3)
        and rec["u101.b96.r3"].status == "planning_stopped"
    )


@pytest.mark.parametrize("revisions", [0, 1])
def test_a7_the_revision_limit_is_strict(tmp_path, revisions):
    world = preflight(
        run_config_variant(
            tmp_path, lambda r: r["planning"].update(max_revisions_per_year=revisions)
        )
    )
    outcome, _ = batch(world, tmp_path / "run")
    rec = _records(outcome)
    assert rec["u101.b96.r3"].attempts_by_year == (1, 1 + revisions)
    assert rec["u101.b96.r3"].planning_stop.attempts == 1 + revisions
    if revisions == 0:  # 不能修订时，超额草稿直接耗尽：不替代理挑一个更便宜的方案
        assert (
            rec["u101.a95.r1"].status == "planning_stopped"
            and rec["u101.a95.r1"].years_committed == 0
        )


def test_a8_an_infeasible_draft_leaves_nothing_behind(main):
    world, out, outcome, _ = main
    results, _ = _direct(world, "u101.a95.r1", _actions(out, "u101.a95.r1")[:1])
    lived = outcome.lives["u101.a95.r1"][0]
    assert (
        results[0].binding_fingerprint == lived.binding_fingerprint
    )  # 与"只执行被接受的计划"逐字节相同
    assert lived.settlement.outcome.cash_end.amount == 7640.0
    assert not any(
        e.opportunity_id == "a95.job.shop_parttime" for e in lived.settlement.outcome.executed
    )


# ---------------------------------------------------------------------- 9–12 生活事件、失败与部分轨迹


def test_a9_illness_after_acceptance_is_lived_not_replanned(main):
    world, out, outcome, _ = main
    rec = _records(outcome)
    assert rec["u101.a95.r1"].attempts_by_year == (2, 1, 1)
    y2 = outcome.lives["u101.a95.r1"][1].settlement.outcome
    sick = [
        (m.month, m.state, None if m.treatment is None else m.treatment.status)
        for m in y2.health.months
        if m.state != "healthy"
    ]
    assert sick == [(5, "major", "treated")] and y2.cash_end.amount == 21480.0
    assert rec["u101.b96.r1"].attempts_by_year == (1, 1) and rec["u101.b96.r1"].status == "stopped"
    stopped = outcome.lives["u101.b96.r1"][1].settlement.outcome
    assert (stopped.stop.month, stopped.stop.kind) == (
        5,
        "data_gap",
    ) and stopped.cash_end.amount == 3920.0
    calls = [c for c in records(out, "calls") if c["clone_id"] == "u101.b96.r1"]
    assert [(c["year_index"], c["attempt"]) for c in calls] == [
        (1, 1),
        (2, 1),
    ]  # 停止之后没有再问规划器
    results, _ = _direct(world, "u101.a95.r1", _actions(out, "u101.a95.r1"))
    assert [r.binding_fingerprint for r in results] == [
        r.binding_fingerprint for r in outcome.lives["u101.a95.r1"]
    ]


def test_a10_one_clone_failing_does_not_shrink_or_stop_the_batch(main):
    _world, _out, outcome, _ = main
    rec = _records(outcome)
    assert rec["u101.b96.r3"].status == "planning_stopped"
    assert rec["u102.a95.r0"].status == rec["u102.b96.r0"].status == "completed"  # 名单上排在它之后
    assert _report(outcome, "user.alpha", "B96").planned == 4


def test_a11_an_unexpected_error_pauses_the_batch_and_keeps_the_roster(tmp_path):
    def boom(run):
        run["planner"]["scripts"] = [
            {"clone_id": "u101.a95.r2", "behavior": "raise_error", "years": [1]}
        ]

    world = preflight(run_config_variant(tmp_path, boom))
    outcome, _ = batch(world, tmp_path / "run")
    rec = _records(outcome)
    assert outcome.status == "paused" and "意外程序错误" in outcome.pause_reason
    assert rec["u101.a95.r0"].status == rec["u101.a95.r1"].status == "completed"
    assert (
        rec["u101.a95.r2"].status == "program_error"
        and rec["u101.a95.r2"].failure.error_kind == "unexpected"
    )
    assert [rec[c].status for c in world.order[3:]] == ["not_run"] * 7
    a95 = _report(outcome, "user.alpha", "A95")
    reasons = {x.reason: x.count for x in a95.missing_reasons}
    assert a95.planned == 4 and reasons == {"input_config_program_error": 1, "not_run": 1}
    assert outcome.reports[ARM].unexpected_errors == 1
    assert sum(cr.planned for cr in outcome.reports[ARM].city_reports) == 10


def test_a12_a_year_two_planning_failure_keeps_year_one(main):
    _world, _out, outcome, _ = main
    ev = next(e for e in outcome.evaluations[ARM] if e.clone_id == "u101.b96.r3")
    assert (
        ev.trajectory.status == "not_finished"
        and ev.trajectory.planning_stop.kind == "revisions_exhausted"
    )
    entry = next(
        e for e in _report(outcome, "user.alpha", "B96").entries if e.clone_id == "u101.b96.r3"
    )
    assert entry.category == "M" and entry.missing_reason == "plan_or_execution"
    assert entry.known_weight.fraction() == 12  # 第 1 年的 12 个月照算
    assert entry.lower.fraction() > 0 and entry.upper.fraction() < 1


# ---------------------------------------------------------------------- 13–15 一致、重放、恢复、身份


def test_a13_runner_results_equal_direct_calls(main):
    world, out, outcome, _ = main
    rec = _records(outcome)
    for ev in outcome.evaluations[ARM]:
        _results, direct = _direct(
            world, ev.clone_id, _actions(out, ev.clone_id), rec[ev.clone_id].planning_stop
        )
        assert direct.result_fingerprint == ev.result_fingerprint


def test_a14_replay_rebuilds_everything_without_the_planner(main):
    world, out, _outcome, _ = main
    r = replay_run(world, out)
    assert r["planner_calls"] == 0 and r["bindings_identical"]
    assert all(r["evaluations_identical"].values()) and all(r["reports_identical"].values())


ARCHIVE = (
    "progress.json",
    f"evaluations/{ARM}.json",
    f"reports/{ARM}.json",
)


def test_a14_resume_at_a_year_boundary_does_not_ask_again(main, tmp_path):
    world, out, outcome, _ = main
    first, p1 = batch(world, tmp_path / "run", stop_after_calls=12)
    assert first.status == "interrupted" and p1.calls == 12
    p2 = RuleBasedMockPlanner(world.config.planner.scripts)
    resumed = resume_run(world, p2, tmp_path / "run")
    assert resumed.status == "completed" and p2.calls == outcome.calls_used - 12
    for name in ARCHIVE:
        assert (tmp_path / "run" / name).read_bytes() == (out / name).read_bytes(), name
    assert RunStore(tmp_path / "run").dump() == RunStore(out).dump()  # 归档库里的全部记录逐条相同


class _InterruptAt(RuleBasedMockPlanner):
    """第 n 次调用时中断（还没有给出回答）：模拟在一年的两份候选之间停下。"""

    def __init__(self, scripts, n):
        super().__init__(scripts)
        self.n = n

    def plan(self, call):
        if self.calls + 1 == self.n:
            raise RunInterrupted("测试：在两份候选之间中断")
        return super().plan(call)


def test_a14_resume_in_the_middle_of_a_year_keeps_the_attempt_count(main, tmp_path):
    world, out, outcome, _ = main
    p1 = _InterruptAt(
        world.config.planner.scripts, 5
    )  # 第 5 次调用 = u101.a95.r1 第 1 年的第 2 份候选
    first = start_run(world, p1, tmp_path / "run", run_id=RUN_ID)
    progress = json.loads((tmp_path / "run/progress.json").read_text(encoding="utf-8"))
    assert first.status == "interrupted" and (
        progress["pending_clone"],
        progress["pending_year"],
        progress["pending_attempts"],
    ) == (
        "u101.a95.r1",
        1,
        1,
    )
    p2 = RuleBasedMockPlanner(world.config.planner.scripts)
    resume_run(world, p2, tmp_path / "run")
    assert p2.calls == outcome.calls_used - 4  # 第 1 份候选的回答复用归档，不重问
    for name in ARCHIVE:
        assert (tmp_path / "run" / name).read_bytes() == (out / name).read_bytes(), name
    assert RunStore(tmp_path / "run").dump() == RunStore(out).dump()  # 归档库里的全部记录逐条相同


def test_a15_a_changed_world_cannot_continue_or_replay_the_old_run(main, tmp_path):
    world, out, _outcome, _ = main
    batch(world, tmp_path / "run", stop_after_calls=6)
    for mutate in (
        lambda r: r["information"].update(show_energy_level=False, permission_version="other"),
        lambda r: r["planning"].update(global_call_limit=59),
        lambda r: r["planner"].update(scripts=[]),
        lambda r: r.update(
            city_facts=facts_variant(
                tmp_path,
                lambda d: [s["facts"][0].update(label="别的名字") for s in d["sheets"]],
            ).as_posix()
        ),
    ):
        changed = preflight(run_config_variant(tmp_path, mutate, name="changed.yaml"))
        with pytest.raises(RunRefusedError, match="不能冒充原运行继续"):
            resume_run(
                changed, RuleBasedMockPlanner(changed.config.planner.scripts), tmp_path / "run"
            )
        with pytest.raises(RunRefusedError, match="不能冒充原运行"):
            replay_run(changed, out)


def _edit_first_accepted(run_dir, edit):
    rows = records(run_dir, "accepted")
    assert rows[0]["clone_id"] == "u101.a95.r0" and rows[0]["year_index"] == 1
    edit(rows[0])
    write_records(run_dir, "accepted", rows)


def _edit_rationale(row):
    row["action"]["rationale"] = "事后改过的理由"


def _edit_binding(row):
    row["binding_fingerprint"] = "0" * 64


def test_a15_an_edited_accepted_plan_cannot_continue_or_replay(main, tmp_path):
    """归档里被接受的行动事后被改（哪怕只改理由）：它不再是解析原始响应得到的那份行动，恢复与重放都在读归档时拒绝，规划器 0 次
    调用，原归档不变。

    （E1-7A 收尾的接口适配：原来恢复在重放那一年比较绑定时才拒绝、重放报告"绑定不同"；现在共用的归档核对先发现行动与原始响应、
    检查记录对不上，两个入口都提前拒绝。只改执行后绑定指纹、行动没改的情形见下一条，仍由重放那一年的绑定比较抓到。）"""
    world, out, _outcome, _ = main
    batch(world, tmp_path / "run", stop_after_calls=12)
    _edit_first_accepted(tmp_path / "run", _edit_rationale)
    before = snapshot(tmp_path / "run")
    planner = RuleBasedMockPlanner(world.config.planner.scripts)
    with pytest.raises(RunRefusedError, match="不是解析这份原始响应得到的行动"):
        resume_run(world, planner, tmp_path / "run")
    assert planner.calls == 0 and snapshot(tmp_path / "run") == before
    done = copy_run(out, tmp_path / "done")
    _edit_first_accepted(done, _edit_rationale)
    with pytest.raises(RunRefusedError, match="不是解析这份原始响应得到的行动"):
        replay_run(world, done)


def test_a15_an_edited_binding_is_caught_when_its_year_is_replayed(main, tmp_path):
    """行动没改、只改了执行后记下的绑定指纹：要真正执行那一年才知道，所以归档核对放行；恢复在重放那一年时拒绝（规划器 0 次调用，
    原归档不变），重放如实报告那个分身的绑定不同。"""
    world, out, _outcome, _ = main
    batch(world, tmp_path / "run", stop_after_calls=12)
    _edit_first_accepted(tmp_path / "run", _edit_binding)
    before = snapshot(tmp_path / "run")
    planner = RuleBasedMockPlanner(world.config.planner.scripts)
    with pytest.raises(RunRefusedError, match="得到的绑定与归档不同"):
        resume_run(world, planner, tmp_path / "run")
    assert planner.calls == 0 and snapshot(tmp_path / "run") == before
    done = copy_run(out, tmp_path / "done")
    _edit_first_accepted(done, _edit_binding)
    r = replay_run(world, done)
    assert r["planner_calls"] == 0 and not r["bindings_identical"]
    assert [x["clone_id"] for x in r["clones"] if not x["bindings_identical"]] == ["u101.a95.r0"]


# ---------------------------------------------------------------------- 16 没有网络


def test_a16_no_network_even_with_keys_in_the_environment(tmp_path, monkeypatch):
    secret = "sk-test-e1-7a-must-not-be-used"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    tried: list[object] = []

    def refuse(*args, **kwargs):
        tried.append(args)
        raise OSError("测试禁止网络")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    outcome, _ = batch(default_world(), tmp_path / "run")
    assert outcome.status == "completed" and tried == []
    for f in (tmp_path / "run").rglob("*"):
        if f.is_file():  # archive.sqlite 是二进制，按字节扫
            assert secret.encode() not in f.read_bytes()


# ---------------------------------------------------------------------- 预算、城市资料问题与拒绝启动


def test_the_global_budget_stops_without_shrinking_the_roster(tmp_path):
    world = preflight(
        run_config_variant(tmp_path, lambda r: r["planning"].update(global_call_limit=5))
    )
    outcome, planner = batch(world, tmp_path / "run")
    rec = _records(outcome)
    assert outcome.status == "budget_exhausted" and planner.calls == outcome.calls_used == 5
    assert (
        rec["u101.a95.r1"].status == "planning_stopped"
        and rec["u101.a95.r1"].planning_stop.kind == "budget_exhausted"
    )
    assert rec["u101.a95.r1"].years_committed == 1
    assert [rec[c].status for c in world.order[2:]] == ["not_run"] * 8
    entry = next(
        e for e in _report(outcome, "user.alpha", "A95").entries if e.clone_id == "u101.a95.r1"
    )
    assert entry.missing_reason == "not_run" and entry.known_weight.fraction() == 12
    assert sum(cr.planned for cr in outcome.reports[ARM].city_reports) == 10


def test_a_city_data_problem_stays_in_its_city(tmp_path, monkeypatch):
    real = life_runner.check_year_plan

    def fake(*, protocol, state, action, catalog, tax):
        if str(state.city).endswith("B96"):
            return PlanCheck(
                stage="city_data",
                passed=False,
                status="data_error",
                problems=(
                    SettlementProblem(code="catalog_data", message="测试：B96 的某项资料缺失"),
                ),
                new_events_possible=True,
            )
        return real(protocol=protocol, state=state, action=action, catalog=catalog, tax=tax)

    monkeypatch.setattr(life_runner, "check_year_plan", fake)
    outcome, _ = batch(default_world(), tmp_path / "run")
    rec = _records(outcome)
    assert outcome.status == "completed"
    assert all(
        rec[c].status == "planning_stopped" and rec[c].planning_stop.kind == "city_data"
        for c in rec
        if ".b96." in c
    )
    assert all(rec[c].attempts_by_year == (1,) for c in rec if ".b96." in c)
    assert rec["u101.a95.r0"].status == "completed"
    b96 = _report(outcome, "user.alpha", "B96")
    assert {x.reason: x.count for x in b96.missing_reasons} == {"data_missing": 4}


def test_refusals_before_and_at_start(tmp_path, main):
    world, out, _outcome, _ = main
    with pytest.raises(LifeContractError, match="已存在"):
        batch(world, out)
    # E1-7B 起真实模型规划器可以接线，但必须用 e1-7b-run-v1 并写全 llm 设定与付费预算；只把种类改成 llm 在加载时就拒绝
    with pytest.raises(LifeContractError, match="真实模型规划器必须写 llm 设定"):
        preflight(
            run_config_variant(
                tmp_path,
                lambda r: r["planner"].update(
                    identity={
                        **r["planner"]["identity"],
                        "kind": "llm",
                        "model_snapshot": "x-2026-01-01",
                    },
                    scripts=[],
                ),
            )
        )
    other = RuleBasedMockPlanner(())
    other.identity = PlannerIdentity(kind="mock", planner_id="mock.other", version="v1")
    with pytest.raises(RunRefusedError, match="不暗中换规划器"):
        start_run(world, other, tmp_path / "x")


def test_the_illness_schedule_matches_the_direct_engine(main):
    """注入事件按人与重复序号发生：同样的行动直接结算，事件与病月完全相同（没有重抽）。"""
    world, out, outcome, _ = main
    results, _ = _direct(world, "u101.a95.r2", _actions(out, "u101.a95.r2"))
    a = results[0].settlement.outcome
    b = outcome.lives["u101.a95.r2"][0].settlement.outcome
    assert (
        (a.exit.month, a.exit.shortfall.amount)
        == (b.exit.month, b.exit.shortfall.amount)
        == (2, 560.0)
    )
    assert Fraction(str(b.cash_end.amount)) == 2000 + 2000 - 1380 - 1200 - 600
