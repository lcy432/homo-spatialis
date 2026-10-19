"""运行器的闭环验收（DECISIONS/0030 第六、十一节）：产品协议样例，mock 规划器，零网络。

样例：一位用户 u301，K51、M52 各一个分身，各生活 3 年。为了让一次运行里出现修订、执行中的疾病与修订耗尽，模块用一份临时仓库：

* 生活输入加一场指定事件：u301（重复序号 0，两座城的分身共用）第 2 年第 5 月月初大病；
* mock 的受控行为：K51 第 1 年第一份草稿多做一份工作（精力超限 → 修订）；M52 第 3 年每次都返回不是 JSON 的文字（修订耗尽）。

于是这次运行的 9 次调用按顺序是：K51 第 1 年第 1、2 份候选，K51 第 2、3 年，M52 第 1、2 年，M52 第 3 年第 1–3 份候选。

全部经正式入口（preflight / start_run / resume_run / replay_run）；变体配置只写在 pytest 的临时目录。
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import socket

import pytest

from agentlayer.life_mock import PRODUCT_MOCK_IDENTITY, ProductMockPlanner
from agentlayer.life_planning import (
    MemoryYear,
    build_memory,
    month_ranges,
    payload_fingerprint,
    render_prompt,
)
from agentlayer.life_views import assert_payload_clean, visible_feedback
from common.life_contracts import (
    LifeContractError,
    PlanCheck,
    SettlementProblem,
    YearAction,
    model_fingerprint,
)
from common.life_planning_contracts import (
    EXECUTION_REVISION,
    MockScript,
    PlannerIdentity,
    PlannerSpec,
)
from common.run_store import RunStore
from econ.life_evaluation import evaluate_clone
from econ.life_settlement import binding_from_policy
from econ.life_year import ScoringInputs, run_life_year
from orchestration import life_runner
from orchestration.life_runner import (
    RunInterrupted,
    RunRefusedError,
    planner_setting_problems,
    preflight,
    replay_run,
    resume_run,
    start_run,
)

from .live_support import load_yaml, make_repo, write_yaml
from .planning_support import (
    ARM,
    RUN_CONFIG,
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

K, M = "u301.k51.r0", "u301.m52.r0"
USER = "user.product"
SCRIPTS = [
    {
        "clone_id": K,
        "behavior": "overcommit_first_draft",
        "years": [1],
        "note": "测试：第 1 年第一份草稿多做一份工作",
    },
    {
        "clone_id": M,
        "behavior": "malformed_output",
        "years": [3],
        "note": "测试：第 3 年每次都不是 JSON",
    },
]
CALLS = 9
#: 恢复后要与不中断的运行逐字节相同的归档文件
ARCHIVE = ("progress.json", f"evaluations/{ARM}.json", f"reports/{ARM}.json")


def _illness_in_year_two(doc):
    doc["protocol"]["illness_events"] = {
        "mode": "injected",
        "stream_version": "e1-5c-illness-stream-v1",
        "injected": [
            {
                "person_id": "u301",
                "repeat_index": 0,
                "year_index": 2,
                "month": 5,
                "severity": "major",
                "note": "测试：第 2 年第 5 月大病",
            }
        ],
        "note": "测试用的指定事件",
    }


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    root = make_repo(tmp_path_factory.mktemp("repo"), life=_illness_in_year_two)
    data = load_yaml(root / RUN_CONFIG)
    data["run"]["planner"]["scripts"] = SCRIPTS
    write_yaml(root / RUN_CONFIG, data)
    return root


@pytest.fixture(autouse=True)
def _in_repo(repo, monkeypatch):
    monkeypatch.chdir(repo)


@pytest.fixture(scope="module")
def main(repo):
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(repo)
        world = default_world()
        out = repo / "main-run"
        outcome, planner = batch(world, out)
    return world, out, outcome, planner


def _records(outcome):
    return {r.clone_id: r for r in outcome.records}


def _report(outcome, city):
    return next(
        cr
        for cr in outcome.reports[ARM].city_reports
        if cr.user_id == USER and cr.city.code == city
    )


def _direct(world, clone, actions, planning_stop=None):
    """不经运行器：同样的已接受行动直接结算、评分与评价。"""
    entry = next(e for e in world.roster.entries if e.clone_id == clone)
    city = str(entry.city)
    catalog = world.bundle.catalog_for(city)
    tax = binding_from_policy(world.bundle.tax_policy(catalog.tax_policy_id))
    profile = world.bundle.profile_for(entry.person_id)
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


def _scripted(run, *extra):
    run["planner"]["scripts"] = [*SCRIPTS, *extra]


# ---------------------------------------------------------------------- 闭环、状态延续、对输入的反应


def test_the_mock_lives_three_years_and_the_report_counts_everyone(main):
    world, out, outcome, planner = main
    assert outcome.status == "completed" and outcome.calls_used == planner.calls == CALLS
    rec = _records(outcome)
    assert list(rec) == list(world.order) == [K, M]
    assert (rec[K].status, rec[K].years_committed, rec[K].attempts_by_year) == (
        "completed",
        3,
        (2, 1, 1),
    )
    assert (rec[M].status, rec[M].years_committed, rec[M].attempts_by_year) == (
        "planning_stopped",
        2,
        (1, 1, 3),
    )
    k51, m52 = _report(outcome, "K51"), _report(outcome, "M52")
    assert (k51.planned, k51.complete, m52.planned, m52.missing) == (1, 1, 1, 1)
    assert {x.reason: x.count for x in m52.missing_reasons} == {"plan_or_execution": 1}
    assert (out / "report.md").read_text(encoding="utf-8").count("| u301.") == 2


def test_the_next_year_reads_the_real_facts_of_the_last(main):
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
    assert (
        checked == 4
    )  # 两个分身各两次正常交接（M52 第 3 年规划没有完成，但请求照样读了第 2 年的事实）
    y3 = request(out, K, 3)["observation"]  # 第 2 年第 5 月的大病出现在第 3 年的病史里
    assert [
        (e["onset_year"], e["onset_month"], e["recovery"]) for e in y3["health"]["history"]
    ] == [(2, 5, "治疗后")]


def test_the_mock_responds_to_what_it_is_shown(main):
    _world, out, _outcome, _ = main

    def jobs(year):
        return {
            o["opportunity_id"]: (o.get("qualification") or {}).get("status")
            for o in request(out, K, year)["observation"]["opportunities"]
            if o["category"] == "job"
        }

    assert jobs(1)["k51.job.accountant"] == "not_yet"
    assert jobs(2)["k51.job.accountant"] == "available_now"  # 第 1 年学完会计课程
    plans = {a.year_index: {i.opportunity_id for i in a.items} for a in _actions(out, K)}
    assert {"k51.job.clerk", "k51.training.accounting_course"} <= plans[1]
    assert "k51.job.accountant" in plans[2] and "k51.job.accountant" in plans[3]
    cash = [request(out, K, y)["observation"]["cash"]["amount"] for y in (1, 2, 3)]
    assert cash == [3000.0, 12000.0, 24040.0]


def test_same_actions_different_words_same_life(main, tmp_path):
    _world, out, outcome, _ = main

    def wording(run):
        _scripted(
            run,
            *({"clone_id": c, "behavior": "alternate_wording", "years": [1, 2, 3]} for c in (K, M)),
        )

    alt, _ = batch(preflight(run_config_variant(tmp_path, wording)), tmp_path / "run")
    for clone in (K, M):
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
        # 评价的数值相同（结果指纹不比：执行绑定记的是整份已接受行动，理由不同它就不同）
        assert (e1.actual.l_actual, e1.valuation.v_reference, e1.combined.j) == (
            e2.actual.l_actual,
            e2.valuation.v_reference,
            e2.combined.j,
        )


# ---------------------------------------------------------------------- 可见事实与执行配置冲突


def test_conflicting_facts_are_refused_before_starting(tmp_path):
    facts = facts_variant(tmp_path, lambda d: d["sheets"][1]["facts"][3].update(number=8))
    path = run_config_variant(tmp_path, lambda r: r.update(city_facts=facts.as_posix()))
    with pytest.raises(RunRefusedError, match="评分条件写的是 b = 3"):
        preflight(path)


def test_a_catalog_swapped_after_preflight_is_not_executed(tmp_path):
    world = default_world()
    data = world.bundle.model_dump(mode="python")
    for c in data["catalogs"]:
        for o in c["opportunities"]:
            if o["opportunity_id"] == "k51.housing.far":
                o["price"]["amount"]["amount"] += 400.0  # 看到的是原价却按另一个价扣款：不允许
    swapped = dataclasses.replace(world, bundle=type(world.bundle).model_validate(data))
    outcome, planner = batch(swapped, tmp_path / "run")
    first = _records(outcome)[K]
    assert outcome.status == "paused" and planner.calls == 0
    assert first.status == "program_error" and "目录与运行清单记下的身份不同" in first.detail
    assert _records(outcome)[M].status == "not_run"


# ---------------------------------------------------------------------- 信息许可


def test_every_request_respects_the_permission(tmp_path):
    def hide(run):
        run["information"].update(
            show_energy_level=False,
            show_stated_dimension_points=False,
            show_stated_material_points=False,
            permission_version="test-hidden",
        )

    world = preflight(run_config_variant(tmp_path, hide))
    outcome, _ = batch(world, tmp_path / "run")
    assert outcome.status == "completed"
    notes = [e.note for e in world.bundle.protocol.illness_events.injected]
    for c in records(tmp_path / "run", "calls"):
        p = c["request"]
        assert_payload_clean(p)
        obs = p["observation"]
        assert obs["energy_level"] is None and obs["resources"]["monthly_energy_quota"] is None
        profile = obs["profile"]
        assert "six_dimension_points" not in profile and "material_allocation" not in profile
        assert {"six_dimension_points", "material_allocation"} <= set(profile["not_permitted"])
        if obs["last_feedback"] is not None:
            assert all(m["energy_used"] is None for m in obs["last_feedback"]["resources"])
        text = json.dumps(p, ensure_ascii=False)
        assert not any(n in text for n in notes)  # 不预告指定疾病事件
        for e in obs["health"]["history"]:
            assert e["onset_year"] < p["year_index"]
        for d in (p.get("revision") or {}).get("diagnostics", []):
            if d["resource"] == "energy":  # 时间预算是公开的，只有精力额度按许可隐藏
                assert d["limit"] is None and d["used"] is None and d["excess"] is None
                assert "250" not in d["text"]
        for f in p["memory"]["facts"]:
            assert "负担点" not in f["text"] and "额度" not in f["text"]
    diagnostics = request(tmp_path / "run", K, 1, 2)["revision"]["diagnostics"]
    energy = next(d for d in diagnostics if d["code"] == "energy_over_limit")
    assert "额度不显示" in energy["text"]


# ---------------------------------------------------------------------- 修订与修订上限；不可行不留痕


def test_format_and_qualification_errors_are_revised_within_the_limit(tmp_path):
    def scripts(run):
        _scripted(run, {"clone_id": M, "behavior": "unqualified_first_draft", "years": [1]})

    outcome, _ = batch(preflight(run_config_variant(tmp_path, scripts)), tmp_path / "run")
    rec = _records(outcome)
    assert rec[M].attempts_by_year == (2, 1, 3)
    first = next(
        a
        for a in records(tmp_path / "run", "attempts")
        if (a["clone_id"], a["year_index"], a["attempt"]) == (M, 1, 1)
    )
    assert first["check_stage"] == "plan" and "qualification_missing" in first["problem_codes"]
    assert first["disposition"] == "revise"
    third = [
        a["disposition"]
        for a in records(tmp_path / "run", "attempts")
        if (a["clone_id"], a["year_index"]) == (M, 3)
    ]
    assert third == ["revise", "revise", "revisions_exhausted"]


@pytest.mark.parametrize("revisions", [0, 1])
def test_the_revision_limit_is_strict(tmp_path, revisions):
    world = preflight(
        run_config_variant(
            tmp_path, lambda r: r["planning"].update(max_revisions_per_year=revisions)
        )
    )
    outcome, _ = batch(world, tmp_path / "run")
    rec = _records(outcome)
    assert rec[M].attempts_by_year == (1, 1, 1 + revisions)
    assert rec[M].planning_stop.attempts == 1 + revisions
    if revisions == 0:  # 不能修订时，超额草稿直接耗尽：不替代理挑一个更省力的方案
        assert rec[K].status == "planning_stopped" and rec[K].years_committed == 0


def test_an_infeasible_draft_leaves_nothing_behind(main):
    world, out, outcome, _ = main
    results, _ = _direct(world, K, _actions(out, K)[:1])
    lived = outcome.lives[K][0]
    assert results[0].binding_fingerprint == lived.binding_fingerprint  # 与"只执行被接受的计划"相同
    jobs = {
        e.opportunity_id for e in lived.settlement.outcome.executed if ".job." in e.opportunity_id
    }
    assert jobs == {"k51.job.clerk"}  # 第一份草稿多出的那份工作没有留下任何执行记录


# ---------------------------------------------------------------------- 生活事件、失败与部分轨迹


def test_illness_after_acceptance_is_lived_not_replanned(main):
    world, out, outcome, _ = main
    assert _records(outcome)[K].attempts_by_year == (2, 1, 1)
    y2 = outcome.lives[K][1].settlement.outcome
    sick = [
        (m.month, m.state, None if m.treatment is None else m.treatment.status)
        for m in y2.health.months
        if m.state != "healthy"
    ]
    assert sick == [(5, "major", "treated")]
    calls = [(c["year_index"], c["attempt"]) for c in records(out, "calls") if c["clone_id"] == K]
    assert calls == [(1, 1), (1, 2), (2, 1), (3, 1)]  # 第 2 年的病发生在执行中：不回到年初重问
    results, _ = _direct(world, K, _actions(out, K))
    assert [r.binding_fingerprint for r in results] == [
        r.binding_fingerprint for r in outcome.lives[K]
    ]


def test_one_clone_failing_does_not_shrink_or_stop_the_batch(tmp_path):
    def first_fails(run):
        run["planner"]["scripts"] = [{"clone_id": K, "behavior": "malformed_output", "years": [1]}]

    outcome, _ = batch(preflight(run_config_variant(tmp_path, first_fails)), tmp_path / "run")
    rec = _records(outcome)
    assert outcome.status == "completed"
    assert (rec[K].status, rec[K].years_committed) == ("planning_stopped", 0)
    assert rec[M].status == "completed"  # 名单上排在它之后，照常生活
    assert _report(outcome, "K51").planned == 1


def test_an_unexpected_error_pauses_the_batch_and_keeps_the_roster(tmp_path):
    def boom(run):
        run["planner"]["scripts"] = [{"clone_id": K, "behavior": "raise_error", "years": [2]}]

    outcome, _ = batch(preflight(run_config_variant(tmp_path, boom)), tmp_path / "run")
    rec = _records(outcome)
    assert outcome.status == "paused" and "意外程序错误" in outcome.pause_reason
    assert rec[K].status == "program_error" and rec[K].failure.error_kind == "unexpected"
    assert rec[M].status == "not_run"
    assert {x.reason: x.count for x in _report(outcome, "K51").missing_reasons} == {
        "input_config_program_error": 1
    }
    assert {x.reason: x.count for x in _report(outcome, "M52").missing_reasons} == {"not_run": 1}
    assert outcome.reports[ARM].unexpected_errors == 1
    assert sum(cr.planned for cr in outcome.reports[ARM].city_reports) == 2


def test_a_year_three_planning_failure_keeps_years_one_and_two(main):
    _world, _out, outcome, _ = main
    ev = next(e for e in outcome.evaluations[ARM] if e.clone_id == M)
    assert ev.trajectory.status == "not_finished"
    assert ev.trajectory.planning_stop.kind == "revisions_exhausted"
    entry = next(e for e in _report(outcome, "M52").entries if e.clone_id == M)
    assert entry.category == "M" and entry.missing_reason == "plan_or_execution"
    assert entry.known_weight.fraction() == 24  # 前两年的 24 个月照算
    assert entry.lower.fraction() > 0 and entry.upper.fraction() < 1


# ---------------------------------------------------------------------- 一致、重放、恢复、身份


def test_runner_results_equal_direct_calls(main):
    world, out, outcome, _ = main
    rec = _records(outcome)
    for ev in outcome.evaluations[ARM]:
        _results, direct = _direct(
            world, ev.clone_id, _actions(out, ev.clone_id), rec[ev.clone_id].planning_stop
        )
        assert direct.result_fingerprint == ev.result_fingerprint


def test_replay_rebuilds_everything_without_the_planner(main):
    world, out, _outcome, _ = main
    r = replay_run(world, out)
    assert r["planner_calls"] == 0 and r["bindings_identical"]
    assert all(r["evaluations_identical"].values()) and all(r["reports_identical"].values())


def test_resume_at_a_year_boundary_does_not_ask_again(main, tmp_path):
    world, out, _outcome, _ = main
    first, p1 = batch(world, tmp_path / "run", stop_after_calls=4)  # K51 三年都已执行
    assert first.status == "interrupted" and p1.calls == 4
    p2 = ProductMockPlanner(world.config.planner.scripts)
    resumed = resume_run(world, p2, tmp_path / "run")
    assert resumed.status == "completed" and p2.calls == CALLS - 4
    for name in ARCHIVE:
        assert (tmp_path / "run" / name).read_bytes() == (out / name).read_bytes(), name
    assert RunStore(tmp_path / "run").dump() == RunStore(out).dump()  # 归档库里的全部记录逐条相同


class _InterruptAt(ProductMockPlanner):
    """第 n 次调用时中断（还没有给出回答）：模拟在一年的两份候选之间停下。"""

    def __init__(self, scripts, n):
        super().__init__(scripts)
        self.n = n

    def plan(self, call):
        if self.calls + 1 == self.n:
            raise RunInterrupted("测试：在两份候选之间中断")
        return super().plan(call)


def test_resume_in_the_middle_of_a_year_keeps_the_attempt_count(main, tmp_path):
    world, out, _outcome, _ = main
    p1 = _InterruptAt(world.config.planner.scripts, 2)  # 第 2 次调用 = K51 第 1 年的第 2 份候选
    first = start_run(world, p1, tmp_path / "run", run_id=RUN_ID)
    progress = json.loads((tmp_path / "run/progress.json").read_text(encoding="utf-8"))
    assert first.status == "interrupted"
    assert (
        progress["pending_clone"],
        progress["pending_year"],
        progress["pending_attempts"],
    ) == (K, 1, 1)
    p2 = ProductMockPlanner(world.config.planner.scripts)
    resume_run(world, p2, tmp_path / "run")
    assert p2.calls == CALLS - 1  # 第 1 份候选的回答复用归档，不重问
    for name in ARCHIVE:
        assert (tmp_path / "run" / name).read_bytes() == (out / name).read_bytes(), name
    assert RunStore(tmp_path / "run").dump() == RunStore(out).dump()


def test_a_changed_world_cannot_continue_or_replay_the_old_run(main, tmp_path):
    world, out, _outcome, _ = main
    batch(world, tmp_path / "run", stop_after_calls=3)
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
                changed, ProductMockPlanner(changed.config.planner.scripts), tmp_path / "run"
            )
        with pytest.raises(RunRefusedError, match="不能冒充原运行"):
            replay_run(changed, out)


def _edit_first_accepted(run_dir, edit):
    rows = records(run_dir, "accepted")
    assert (rows[0]["clone_id"], rows[0]["year_index"]) == (K, 1)
    edit(rows[0])
    write_records(run_dir, "accepted", rows)


def _edit_rationale(row):
    row["action"]["rationale"] = "事后改过的理由"


def _edit_binding(row):
    row["binding_fingerprint"] = "0" * 64


def test_an_edited_accepted_plan_cannot_continue_or_replay(main, tmp_path):
    """归档里被接受的行动事后被改（哪怕只改理由）：它不再是解析原始响应得到的那份行动，恢复与重放都在读归档时拒绝，规划器 0 次
    调用，原归档不变。"""
    world, out, _outcome, _ = main
    batch(world, tmp_path / "run", stop_after_calls=4)
    _edit_first_accepted(tmp_path / "run", _edit_rationale)
    before = snapshot(tmp_path / "run")
    planner = ProductMockPlanner(world.config.planner.scripts)
    with pytest.raises(RunRefusedError, match="不是解析这份原始响应得到的行动"):
        resume_run(world, planner, tmp_path / "run")
    assert planner.calls == 0 and snapshot(tmp_path / "run") == before
    done = copy_run(out, tmp_path / "done")
    _edit_first_accepted(done, _edit_rationale)
    with pytest.raises(RunRefusedError, match="不是解析这份原始响应得到的行动"):
        replay_run(world, done)


def test_an_edited_binding_is_caught_when_its_year_is_replayed(main, tmp_path):
    """行动没改、只改了执行后记下的绑定指纹：要真正执行那一年才知道，所以归档核对放行；恢复在重放那一年时拒绝（规划器 0 次调用，
    原归档不变），重放如实报告那个分身的绑定不同。"""
    world, out, _outcome, _ = main
    batch(world, tmp_path / "run", stop_after_calls=4)
    _edit_first_accepted(tmp_path / "run", _edit_binding)
    before = snapshot(tmp_path / "run")
    planner = ProductMockPlanner(world.config.planner.scripts)
    with pytest.raises(RunRefusedError, match="得到的绑定与归档不同"):
        resume_run(world, planner, tmp_path / "run")
    assert planner.calls == 0 and snapshot(tmp_path / "run") == before
    done = copy_run(out, tmp_path / "done")
    _edit_first_accepted(done, _edit_binding)
    r = replay_run(world, done)
    assert r["planner_calls"] == 0 and not r["bindings_identical"]
    assert [x["clone_id"] for x in r["clones"] if not x["bindings_identical"]] == [K]


def test_no_network_even_with_keys_in_the_environment(tmp_path, monkeypatch):
    secret = "sk-test-mock-must-not-be-used"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
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
        run_config_variant(tmp_path, lambda r: r["planning"].update(global_call_limit=3))
    )
    outcome, planner = batch(world, tmp_path / "run")
    rec = _records(outcome)
    assert outcome.status == "budget_exhausted" and planner.calls == outcome.calls_used == 3
    assert rec[K].status == "planning_stopped" and rec[K].planning_stop.kind == "budget_exhausted"
    assert rec[K].years_committed == 2 and rec[M].status == "not_run"
    entry = next(e for e in _report(outcome, "K51").entries if e.clone_id == K)
    assert entry.missing_reason == "not_run" and entry.known_weight.fraction() == 24
    assert sum(cr.planned for cr in outcome.reports[ARM].city_reports) == 2


def test_a_city_data_problem_stays_in_its_city(tmp_path, monkeypatch):
    real = life_runner.check_year_plan

    def fake(*, protocol, state, action, catalog, tax):
        if str(state.city).endswith("M52"):
            return PlanCheck(
                stage="city_data",
                passed=False,
                status="data_error",
                problems=(
                    SettlementProblem(code="catalog_data", message="测试：M52 的某项资料缺失"),
                ),
                new_events_possible=True,
            )
        return real(protocol=protocol, state=state, action=action, catalog=catalog, tax=tax)

    monkeypatch.setattr(life_runner, "check_year_plan", fake)
    outcome, _ = batch(default_world(), tmp_path / "run")
    rec = _records(outcome)
    assert outcome.status == "completed"
    assert rec[M].status == "planning_stopped" and rec[M].planning_stop.kind == "city_data"
    assert rec[M].attempts_by_year == (1,)  # 城市资料问题不修订
    assert rec[K].status == "completed"
    assert {x.reason: x.count for x in _report(outcome, "M52").missing_reasons} == {
        "data_missing": 1
    }


def test_refusals_before_and_at_start(tmp_path, main):
    world, out, _outcome, _ = main
    with pytest.raises(LifeContractError, match="已存在"):
        batch(world, out)
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
    other = ProductMockPlanner(())
    other.identity = PlannerIdentity(kind="mock", planner_id="mock.other", version="v1")
    with pytest.raises(RunRefusedError, match="不暗中换规划器"):
        start_run(world, other, tmp_path / "x")


# ---------------------------------------------------------------------- 恢复时逐条核对归档


def _interrupted(world, path, calls):
    first, planner = batch(world, path, stop_after_calls=calls)
    assert first.status == "interrupted" and planner.calls == calls
    return path


def _refused(world, path, pattern):
    """恢复被拒绝；规划器 0 次调用；原归档一个字节都不变。返回拒绝的说明。"""
    before = snapshot(path)
    planner = ProductMockPlanner(world.config.planner.scripts)
    with pytest.raises(RunRefusedError, match=pattern) as info:
        resume_run(world, planner, path)
    assert planner.calls == 0
    assert snapshot(path) == before
    return str(info.value)


def _second_draft(out):
    return next(c for c in records(out, "calls") if c["request_id"].endswith(f"{K}/y1/a2"))


def test_a_swapped_response_is_refused_and_nothing_is_written(main, tmp_path):
    """第 1 次调用（K51 第 1 年第 1 份候选，检查记录是"精力超限、要修订"）只把响应换成合法的第 2 份草稿。"""
    world, out, _, _ = main
    path = _interrupted(world, tmp_path / "run", 1)
    calls = records(path, "calls")
    assert records(path, "attempts")[-1]["disposition"] == "revise"
    calls[-1]["response_text"] = _second_draft(out)["response_text"]
    write_records(path, "calls", calls)
    text = _refused(world, path, "response_fingerprint")
    assert f"{K} 第 1 年第 1 份候选" in text and "处置 revise" in text


def test_a_stored_request_that_no_longer_matches_its_fingerprint_is_refused(main, tmp_path):
    world, *_ = main
    path = _interrupted(world, tmp_path / "run", 1)
    calls = records(path, "calls")
    calls[0]["request"]["observation"]["cash"]["amount"] = 9999.0
    write_records(path, "calls", calls)
    assert f"{K} 第 1 年第 1 份候选" in _refused(world, path, "存下的请求重算的指纹")


def test_a_consistently_rewritten_request_still_differs_from_the_rebuilt_one(main, tmp_path):
    """请求与它的指纹（连同检查记录里的请求指纹）一起改过：记录之间自洽，但按真实年初状态重建的请求不同，恢复拒绝。"""
    world, *_ = main
    path = _interrupted(world, tmp_path / "run", 1)
    calls, attempts = records(path, "calls"), records(path, "attempts")
    payload = calls[0]["request"]
    payload["observation"]["cash"]["amount"] = 9999.0
    fp = payload_fingerprint(payload)
    system, user = render_prompt(payload)
    calls[0].update(
        request_fingerprint=fp,
        system_prompt_sha256=hashlib.sha256(system.encode("utf-8")).hexdigest(),
        user_prompt_sha256=hashlib.sha256(user.encode("utf-8")).hexdigest(),
    )
    attempts[0]["request_fingerprint"] = fp
    write_records(path, "calls", calls)
    write_records(path, "attempts", attempts)
    _refused(world, path, f"重建的 .*{K}/y1/a1 与归档里的请求不同")


def test_a_response_whose_recorded_disposition_no_longer_fits_is_refused(main, tmp_path):
    """换了响应、也把检查记录里的响应指纹改成新响应的：记录之间自洽，但重新解析与检查得出的处置是 accepted，与检查记录的
    revise 不同，恢复拒绝。"""
    world, out, _, _ = main
    path = _interrupted(world, tmp_path / "run", 1)
    calls, attempts = records(path, "calls"), records(path, "attempts")
    second = _second_draft(out)
    calls[-1]["response_text"] = second["response_text"]
    attempts[-1]["response_fingerprint"] = hashlib.sha256(
        second["response_text"].encode("utf-8")
    ).hexdigest()
    write_records(path, "calls", calls)
    write_records(path, "attempts", attempts)
    text = _refused(world, path, "与归档的检查记录不同")
    assert "归档 revise，重新得出 accepted" in text and "disposition" in text


def test_an_accepted_year_cannot_borrow_a_rejected_attempt(main, tmp_path):
    """已接受的行动指向第 1 份候选（检查记录是 revise）：不能把要求修订的那一份当成已接受。"""
    world, *_ = main
    path = _interrupted(world, tmp_path / "run", 4)
    accepted = records(path, "accepted")
    row = next(r for r in accepted if (r["clone_id"], r["year_index"]) == (K, 1))
    assert row["attempt"] == 2
    row["attempt"] = 1
    row["request_id"] = row["request_id"].replace("/a2", "/a1")
    write_records(path, "accepted", accepted)
    _refused(world, path, "处置是 revise")


@pytest.mark.parametrize(
    ("name", "pattern"),
    [
        ("calls", "调用记录出现了两次"),
        ("attempts", "检查记录出现了两次"),
        ("accepted", "已接受的行动出现了两次"),
    ],
)
def test_duplicate_keys_are_refused_not_overwritten(main, tmp_path, name, pattern):
    world, *_ = main
    path = _interrupted(world, tmp_path / "run", 4)
    rows = records(path, name)
    dup = dict(rows[1])
    if name == "calls":
        dup["response_text"] = "（第二条同键记录：后一条不能覆盖前一条）"
    rows.append(dup)
    write_records(path, name, rows)
    _refused(world, path, pattern)


def test_a_missing_check_in_the_middle_is_not_a_supported_interruption(main, tmp_path):
    world, *_ = main
    path = _interrupted(world, tmp_path / "run", 4)
    attempts = records(path, "attempts")
    del attempts[1]  # 第 2 次调用的检查记录；之后还有调用
    write_records(path, "attempts", attempts)
    _refused(world, path, "只有最后一次调用可以停在")


def test_records_off_the_replayed_path_are_refused(main, tmp_path):
    """中断在第 1 次调用之后；归档末尾又多了 M52 第 1 年的一整组记录（取自同一次完整运行，彼此完全自洽）。按确定的路径重放到
    下一次调用时，它们一条都没有被用到——不在这次运行的路径上，恢复在调用规划器之前拒绝。"""
    world, out, _, _ = main
    path = _interrupted(world, tmp_path / "run", 1)
    for name in ("calls", "attempts", "accepted"):
        extra = [r for r in records(out, name) if r["clone_id"] == M and r["year_index"] == 1]
        assert extra
        write_records(path, name, records(path, name) + extra)
    _refused(world, path, "没有重放到的记录")


def test_a_saved_response_without_its_check_continues_without_asking_again(main, tmp_path):
    """合法中断：第 1 次调用的响应已落盘、检查尚未落盘。恢复按已保存的响应继续，不重新调用规划器；最终归档与不中断的运行相同。"""
    world, out, _, _ = main
    path = _interrupted(world, tmp_path / "run", 1)
    write_records(path, "attempts", records(path, "attempts")[:-1])
    planner = ProductMockPlanner(world.config.planner.scripts)
    resumed = resume_run(world, planner, path)
    assert resumed.status == "completed" and planner.calls == CALLS - 1
    for name in ARCHIVE:
        assert (path / name).read_bytes() == (out / name).read_bytes(), name
    assert RunStore(path).dump() == RunStore(out).dump()


def test_an_accepted_check_whose_year_was_not_executed_continues(main, tmp_path):
    """合法中断：第 2 次调用的检查已落盘（accepted），这一年还没有执行（没有已接受的行动）。恢复执行这一年，不重复调用。"""
    world, out, _, _ = main
    path = _interrupted(world, tmp_path / "run", 2)
    assert len(records(path, "accepted")) == 1
    write_records(path, "accepted", [])
    planner = ProductMockPlanner(world.config.planner.scripts)
    resumed = resume_run(world, planner, path)
    assert resumed.status == "completed" and planner.calls == CALLS - 2
    for name in ARCHIVE:
        assert (path / name).read_bytes() == (out / name).read_bytes(), name
    assert RunStore(path).dump() == RunStore(out).dump()


def test_replay_shares_the_archive_check(main, tmp_path):
    world, out, _, _ = main
    done = copy_run(out, tmp_path / "done")
    calls = records(done, "calls")
    calls[2]["response_text"] = calls[3]["response_text"]
    write_records(done, "calls", calls)
    with pytest.raises(RunRefusedError, match="response_fingerprint"):
        replay_run(world, done)


# ---------------------------------------------------------------------- 实际规划器与声明


def test_a_planner_missing_its_declared_scripts_is_refused_before_anything_runs(main, tmp_path):
    world, *_ = main
    planner = ProductMockPlanner()
    with pytest.raises(RunRefusedError, match="声明了但实际没有") as info:
        start_run(world, planner, tmp_path / "run", run_id=RUN_ID)
    assert planner.calls == 0 and not (tmp_path / "run").exists()
    assert f"{M} malformed_output（第 3 年）" in str(info.value)


def _scripts(world, **change):
    first = world.config.planner.scripts[0]
    return (first.model_copy(update=change), *world.config.planner.scripts[1:])


@pytest.mark.parametrize(
    ("change", "pattern"),
    [
        ({"clone_id": M}, "声明了但实际没有.*实际多出"),
        ({"years": (2,)}, "年份不同"),
        ({"behavior": "unqualified_first_draft"}, "声明了但实际没有.*实际多出"),
    ],
)
def test_a_changed_script_is_refused(main, tmp_path, change, pattern):
    world, *_ = main
    planner = ProductMockPlanner(_scripts(world, **change))
    with pytest.raises(RunRefusedError, match=pattern):
        start_run(world, planner, tmp_path / "run", run_id=RUN_ID)
    assert planner.calls == 0 and not (tmp_path / "run").exists()


def test_an_extra_script_is_refused_but_order_and_notes_do_not_matter(main):
    world, *_ = main
    declared = world.config.planner
    extra = MockScript(clone_id=M, behavior="alternate_wording", years=(1,))
    problems = planner_setting_problems(declared, ProductMockPlanner((*declared.scripts, extra)))
    assert any(f"实际多出：{M} alternate_wording（第 1 年）" in p for p in problems)
    # mock 按集合取用受控行为：先后、年份重复与说明文字不改变行为，也就不改变行为身份
    shuffled = tuple(
        s.model_copy(update={"note": "另一种说明", "years": (*s.years, *s.years)})
        for s in reversed(declared.scripts)
    )
    assert planner_setting_problems(declared, ProductMockPlanner(shuffled)) == []
    assert (
        PlannerSpec(identity=PRODUCT_MOCK_IDENTITY, scripts=shuffled).behavior_fingerprint
        == declared.behavior_fingerprint
    )


def test_a_planner_that_cannot_report_its_settings_is_refused(main, tmp_path):
    world, *_ = main

    class Opaque:  # 有身份、能规划，但说不出实际生效的设定
        identity = PRODUCT_MOCK_IDENTITY

        def __init__(self):
            self.inner = ProductMockPlanner(world.config.planner.scripts)

        def plan(self, call):
            return self.inner.plan(call)

    with pytest.raises(RunRefusedError, match="没有报出实际生效的设定"):
        start_run(world, Opaque(), tmp_path / "run", run_id=RUN_ID)
    assert not (tmp_path / "run").exists()


def test_resume_checks_the_planner_too(main, tmp_path):
    world, *_ = main
    path = _interrupted(world, tmp_path / "run", 1)
    before = snapshot(path)
    planner = ProductMockPlanner()
    with pytest.raises(RunRefusedError, match="声明了但实际没有"):
        resume_run(world, planner, path)
    assert planner.calls == 0 and snapshot(path) == before


def test_the_manifest_records_the_behavior_identity(main):
    world, out, _, _ = main
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["identities"]["planner_behavior"] == world.config.planner.behavior_fingerprint
    assert manifest["identities"]["execution_revision"] == EXECUTION_REVISION


# ---------------------------------------------------------------------- 领薪月份写进记忆


def test_the_sick_month_is_not_written_as_paid(main):
    """K51 第 2 年第 5 月大病病假，没有工资：第 3 年的记忆写真实月份。"""
    _, out, outcome, _ = main
    months = visible_feedback(outcome.lives[K][1].settlement).career.positions[0].months
    assert months == (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12)
    facts = [f["text"] for f in request(out, K, 3)["memory"]["facts"]]
    assert "第 2 年：岗位 k51.job.accountant 领到工资的月份 1–4、6–12 月，共 11 个月" in facts
    assert not any("1–12 月" in f for f in facts if f.startswith("第 2 年：岗位"))
    assert any("第 2 年第 5 月：大病" in f and "病假" in f for f in facts)


@pytest.mark.parametrize(
    ("months", "text"),
    [
        (range(1, 13), "1–12 月，共 12 个月"),
        (range(3, 13), "3–12 月，共 10 个月"),
        ((1, 2, 5, 6, 7, 11), "1–2、5–7、11 月，共 6 个月"),
        ((5,), "5 月，共 1 个月"),
        ((12, 1, 2), "1–2、12 月，共 3 个月"),
    ],
)
def test_month_ranges_keep_every_gap(months, text):
    assert month_ranges(tuple(months)) == text


def test_no_months_is_said_plainly_and_never_formatted_as_a_range():
    with pytest.raises(LifeContractError):
        month_ranges(())


def test_each_position_keeps_its_own_months(main):
    """两份岗位各写各的月份，不拼成一段连续经历；一份没有领到工资就直说。"""
    _, _, outcome, _ = main
    fb = visible_feedback(outcome.lives[K][1].settlement)
    p0 = fb.career.positions[0]
    positions = (
        p0.model_copy(update={"opportunity_id": "k51.job.clerk", "months": (1, 2, 3)}),
        p0.model_copy(update={"months": (7, 8, 9, 10, 11, 12)}),
        p0.model_copy(update={"opportunity_id": "k51.job.shop_parttime", "months": ()}),
    )
    fb = fb.model_copy(update={"career": fb.career.model_copy(update={"positions": positions})})
    memory = build_memory(
        clone_id=K,
        city="US:SYNTH2026:K51",
        current_year=3,
        years=[
            MemoryYear(
                clone_id=K,
                city="US:SYNTH2026:K51",
                year_index=2,
                feedback=fb,
                need_summary="x",
                rationale="y",
            )
        ],
    )
    lines = [f.text for f in memory.facts if "岗位" in f.text]
    assert lines == [
        "第 2 年：岗位 k51.job.clerk 领到工资的月份 1–3 月，共 3 个月",
        "第 2 年：岗位 k51.job.accountant 领到工资的月份 7–12 月，共 6 个月",
        "第 2 年：岗位 k51.job.shop_parttime 这一年没有领到工资",
    ]
