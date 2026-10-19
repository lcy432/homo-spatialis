"""E1-7A 最小收尾（DECISIONS/0030 第六节）：独立审核 F1–F3 的行为测试。

F1 恢复时逐条核对归档：只换原始响应、改存下的请求、响应与处置错配、借用别的候选的行动、重复键都拒绝——规划器 0 次调用、原归档
一个字节都不变；合法的中断（响应已落盘而检查未落盘、检查已落盘而年度未执行）照常继续，结果与不中断的运行逐字节相同。
F2 实际规划器的设定必须与运行配置的声明一致：漏传、换对象、换年份、换行为、多出受控行为都在建归档与第一次调用之前拒绝。
F3 事实记忆按真实领薪月份写区间：第 5 月病假没有工资，下一年的记忆写"1–4、6–12 月，共 11 个月"。

全部经正式入口（preflight / start_run / resume_run / replay_run），mock 规划器，零网络；改动只发生在 pytest 的临时目录。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from agentlayer.life_mock import MOCK_IDENTITY, RuleBasedMockPlanner
from agentlayer.life_planning import (
    MemoryYear,
    build_memory,
    month_ranges,
    payload_fingerprint,
    render_prompt,
)
from agentlayer.life_views import visible_feedback
from common.life_contracts import LifeContractError
from common.life_planning_contracts import MockScript, PlannerSpec
from common.run_store import RunStore
from orchestration.life_runner import (
    RunRefusedError,
    planner_setting_problems,
    replay_run,
    resume_run,
    start_run,
)

from .planning_support import (
    RUN_ID,
    batch,
    copy_run,
    default_world,
    records,
    request,
    snapshot,
    write_records,
)

#: 恢复后要与不中断的运行逐字节相同的归档文件
ARCHIVE = (
    "progress.json",
    "evaluations/arm.main.json",
    "reports/arm.main.json",
)


@pytest.fixture(scope="module")
def main(tmp_path_factory):
    world = default_world()
    out = tmp_path_factory.mktemp("e17a-closeout") / "run"
    outcome, _planner = batch(world, out)
    return world, out, outcome


def _interrupted(world, path, calls):
    first, planner = batch(world, path, stop_after_calls=calls)
    assert first.status == "interrupted" and planner.calls == calls
    return path


def _refused(world, path, pattern):
    """恢复被拒绝；规划器 0 次调用；原归档一个字节都不变。返回拒绝的说明。"""
    before = snapshot(path)
    planner = RuleBasedMockPlanner(world.config.planner.scripts)
    with pytest.raises(RunRefusedError, match=pattern) as info:
        resume_run(world, planner, path)
    assert planner.calls == 0
    assert snapshot(path) == before
    return str(info.value)


# ---------------------------------------------------------------------- F1 恢复时核对归档


def test_f1_a_swapped_response_is_refused_and_nothing_is_written(main, tmp_path):
    """审核反例：第 4 次调用（A95 r1 第 1 年第 1 份候选，原检查记录是"精力超限、要修订"）只换成合法的第 2 份草稿。"""
    world, out, _ = main
    path = _interrupted(world, tmp_path / "run", 4)
    calls = records(path, "calls")
    assert (calls[-1]["clone_id"], calls[-1]["year_index"], calls[-1]["attempt"]) == (
        "u101.a95.r1",
        1,
        1,
    )
    assert records(path, "attempts")[-1]["disposition"] == "revise"
    second = next(c for c in records(out, "calls") if c["request_id"].endswith("u101.a95.r1/y1/a2"))
    calls[-1]["response_text"] = second["response_text"]
    write_records(path, "calls", calls)
    text = _refused(world, path, "response_fingerprint")
    assert "u101.a95.r1 第 1 年第 1 份候选" in text and "处置 revise" in text


def test_f1_a_stored_request_that_no_longer_matches_its_fingerprint_is_refused(main, tmp_path):
    world, _, _ = main
    path = _interrupted(world, tmp_path / "run", 4)
    calls = records(path, "calls")
    calls[0]["request"]["observation"]["cash"]["amount"] = 9999.0
    write_records(path, "calls", calls)
    text = _refused(world, path, "存下的请求重算的指纹")
    assert "u101.a95.r0 第 1 年第 1 份候选" in text


def test_f1_a_consistently_rewritten_request_still_differs_from_the_rebuilt_one(main, tmp_path):
    """请求与它的指纹（连同检查记录里的请求指纹）一起改过：记录之间自洽，但按真实年初状态重建的请求不同，恢复拒绝。"""
    world, _, _ = main
    path = _interrupted(world, tmp_path / "run", 4)
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
    _refused(world, path, "重建的 .*u101.a95.r0/y1/a1 与归档里的请求不同")


def test_f1_a_response_whose_recorded_disposition_no_longer_fits_is_refused(main, tmp_path):
    """换了响应、也把检查记录里的响应指纹改成新响应的：记录之间自洽，但用原有解析与执行前检查重新得出的处置是 accepted，
    与检查记录的 revise 不同，恢复拒绝。"""
    world, out, _ = main
    path = _interrupted(world, tmp_path / "run", 4)
    calls, attempts = records(path, "calls"), records(path, "attempts")
    second = next(c for c in records(out, "calls") if c["request_id"].endswith("u101.a95.r1/y1/a2"))
    calls[-1]["response_text"] = second["response_text"]
    attempts[-1]["response_fingerprint"] = hashlib.sha256(
        second["response_text"].encode("utf-8")
    ).hexdigest()
    write_records(path, "calls", calls)
    write_records(path, "attempts", attempts)
    text = _refused(world, path, "与归档的检查记录不同")
    assert "归档 revise，重新得出 accepted" in text and "disposition" in text


def test_f1_an_accepted_year_cannot_borrow_a_rejected_attempt(main, tmp_path):
    """已接受的行动指向第 1 份候选（检查记录是 revise）：不能把要求修订的那一份当成已接受。"""
    world, _, _ = main
    path = _interrupted(world, tmp_path / "run", 12)
    accepted = records(path, "accepted")
    row = next(r for r in accepted if (r["clone_id"], r["year_index"]) == ("u101.a95.r1", 1))
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
def test_f1_duplicate_keys_are_refused_not_overwritten(main, tmp_path, name, pattern):
    world, _, _ = main
    path = _interrupted(world, tmp_path / "run", 12)
    rows = records(path, name)
    dup = dict(rows[1])
    if name == "calls":
        dup["response_text"] = "（第二条同键记录：后一条不能覆盖前一条）"
    rows.append(dup)
    write_records(path, name, rows)
    _refused(world, path, pattern)


def test_f1_a_missing_check_in_the_middle_is_not_a_supported_interruption(main, tmp_path):
    world, _, _ = main
    path = _interrupted(world, tmp_path / "run", 12)
    attempts = records(path, "attempts")
    del attempts[3]  # 第 4 次调用的检查记录；之后还有调用
    write_records(path, "attempts", attempts)
    _refused(world, path, "只有最后一次调用可以停在")


def test_f1_records_off_the_replayed_path_are_refused(main, tmp_path):
    """中断在第 4 次调用之后；归档末尾又多了另一个分身（β B96）第 1 年的一整组记录（取自同一次完整运行，彼此完全自洽、逐条
    核对都通过）。按确定的路径重放到第 5 次调用时，它们一条都没有被用到——不在这次运行的路径上，恢复在调用规划器之前拒绝。"""
    world, out, _ = main
    path = _interrupted(world, tmp_path / "run", 4)
    for name in ("calls", "attempts", "accepted"):
        extra = [
            r for r in records(out, name) if r["clone_id"] == "u102.b96.r0" and r["year_index"] == 1
        ]
        assert extra
        write_records(path, name, records(path, name) + extra)
    _refused(world, path, "没有重放到的记录")


def test_f1_a_saved_response_without_its_check_continues_without_asking_again(main, tmp_path):
    """合法中断：第 4 次调用的响应已落盘、检查尚未落盘。恢复按已保存的响应继续解析与检查，不重新调用规划器；最终归档与不中断的
    运行逐字节相同。"""
    world, out, outcome = main
    path = _interrupted(world, tmp_path / "run", 4)
    write_records(path, "attempts", records(path, "attempts")[:-1])
    planner = RuleBasedMockPlanner(world.config.planner.scripts)
    resumed = resume_run(world, planner, path)
    assert resumed.status == "completed" and planner.calls == outcome.calls_used - 4
    for name in ARCHIVE:
        assert (path / name).read_bytes() == (out / name).read_bytes(), name
    assert RunStore(path).dump() == RunStore(out).dump()  # 归档库里的全部记录逐条相同


def test_f1_an_accepted_check_whose_year_was_not_executed_continues(main, tmp_path):
    """合法中断：第 1 次调用的检查已落盘（accepted），这一年还没有执行（没有已接受的行动）。恢复执行这一年，不重复调用，结果
    与不中断的运行逐字节相同。"""
    world, out, outcome = main
    path = _interrupted(world, tmp_path / "run", 1)
    accepted = records(path, "accepted")
    assert len(accepted) == 1
    write_records(path, "accepted", [])
    planner = RuleBasedMockPlanner(world.config.planner.scripts)
    resumed = resume_run(world, planner, path)
    assert resumed.status == "completed" and planner.calls == outcome.calls_used - 1
    for name in ARCHIVE:
        assert (path / name).read_bytes() == (out / name).read_bytes(), name
    assert RunStore(path).dump() == RunStore(out).dump()  # 归档库里的全部记录逐条相同


def test_f1_replay_shares_the_archive_check(main, tmp_path):
    world, out, _ = main
    done = copy_run(out, tmp_path / "done")
    calls = records(done, "calls")
    calls[3]["response_text"] = calls[4]["response_text"]
    write_records(done, "calls", calls)
    with pytest.raises(RunRefusedError, match="response_fingerprint"):
        replay_run(world, done)


# ---------------------------------------------------------------------- F2 实际规划器与声明


def test_f2_a_planner_missing_its_declared_scripts_is_refused_before_anything_runs(main, tmp_path):
    """审核反例：运行配置声明了两段受控行为，调用方却用了没有传受控行为的 mock。原来照常运行（25 次调用、B96 r3 完成三年）。"""
    world, _, _ = main
    planner = RuleBasedMockPlanner()
    with pytest.raises(RunRefusedError, match="声明了但实际没有") as info:
        start_run(world, planner, tmp_path / "run", run_id=RUN_ID)
    assert planner.calls == 0 and not (tmp_path / "run").exists()
    assert "u101.b96.r3 malformed_output（第 2 年）" in str(info.value)


def _scripts(world, **change):
    first = world.config.planner.scripts[0]
    return (first.model_copy(update=change), *world.config.planner.scripts[1:])


@pytest.mark.parametrize(
    ("change", "pattern"),
    [
        ({"clone_id": "u101.a95.r0"}, "声明了但实际没有.*实际多出"),
        ({"years": (2,)}, "年份不同"),
        ({"behavior": "unqualified_first_draft"}, "声明了但实际没有.*实际多出"),
    ],
)
def test_f2_a_changed_script_is_refused(main, tmp_path, change, pattern):
    world, _, _ = main
    planner = RuleBasedMockPlanner(_scripts(world, **change))
    with pytest.raises(RunRefusedError, match=pattern):
        start_run(world, planner, tmp_path / "run", run_id=RUN_ID)
    assert planner.calls == 0 and not (tmp_path / "run").exists()


def test_f2_an_extra_script_is_refused_but_order_and_notes_do_not_matter(main):
    world, _, _ = main
    declared = world.config.planner
    extra = MockScript(clone_id="u102.a95.r0", behavior="alternate_wording", years=(1,))
    problems = planner_setting_problems(declared, RuleBasedMockPlanner((*declared.scripts, extra)))
    assert any("实际多出：u102.a95.r0 alternate_wording（第 1 年）" in p for p in problems)
    # mock 按集合取用受控行为：先后、年份重复与说明文字不改变行为，也就不改变行为身份
    shuffled = tuple(
        s.model_copy(update={"note": "另一种说明", "years": (*s.years, *s.years)})
        for s in reversed(declared.scripts)
    )
    assert planner_setting_problems(declared, RuleBasedMockPlanner(shuffled)) == []
    assert (
        PlannerSpec(identity=MOCK_IDENTITY, scripts=shuffled).behavior_fingerprint
        == declared.behavior_fingerprint
    )


def test_f2_a_planner_that_cannot_report_its_settings_is_refused(main, tmp_path):
    world, _, _ = main

    class Opaque:  # 有身份、能规划，但说不出实际生效的设定
        identity = MOCK_IDENTITY

        def __init__(self):
            self.inner = RuleBasedMockPlanner(world.config.planner.scripts)

        def plan(self, call):
            return self.inner.plan(call)

    with pytest.raises(RunRefusedError, match="没有报出实际生效的设定"):
        start_run(world, Opaque(), tmp_path / "run", run_id=RUN_ID)
    assert not (tmp_path / "run").exists()


def test_f2_resume_checks_the_planner_too(main, tmp_path):
    world, _, _ = main
    path = _interrupted(world, tmp_path / "run", 4)
    before = snapshot(path)
    planner = RuleBasedMockPlanner()
    with pytest.raises(RunRefusedError, match="声明了但实际没有"):
        resume_run(world, planner, path)
    assert planner.calls == 0 and snapshot(path) == before


def test_f2_the_manifest_records_the_behavior_identity(main):
    world, out, _ = main
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["identities"]["planner_behavior"] == world.config.planner.behavior_fingerprint
    assert (
        manifest["identities"]["execution_revision"] == "e1-7b-r2"
    )  # E1-7B：执行修订随请求格式升级；收尾 r2


# ---------------------------------------------------------------------- F3 领薪月份


def test_f3_the_sick_month_is_not_written_as_paid(main):
    """A95 r1 第 2 年第 5 月大病病假，没有工资：第 3 年的记忆写真实月份。"""
    _, out, outcome = main
    year2 = outcome.lives["u101.a95.r1"][1]
    months = visible_feedback(year2.settlement).career.positions[0].months
    assert months == (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12)
    facts = [f["text"] for f in request(out, "u101.a95.r1", 3)["memory"]["facts"]]
    assert (
        "第 2 年：岗位 a95.job.accountant_office 领到工资的月份 1–4、6–12 月，共 11 个月" in facts
    )
    assert not any("1–12 月" in f for f in facts if f.startswith("第 2 年：岗位"))
    assert any("第 2 年第 5 月：大病" in f and "病假" in f for f in facts)


@pytest.mark.parametrize(
    ("months", "text"),
    [
        (range(1, 13), "1–12 月，共 12 个月"),
        (range(3, 13), "3–12 月，共 10 个月"),
        (range(1, 8), "1–7 月，共 7 个月"),
        ((1, 2, 5, 6, 7, 11), "1–2、5–7、11 月，共 6 个月"),
        ((5,), "5 月，共 1 个月"),
        ((12, 1, 2), "1–2、12 月，共 3 个月"),
    ],
)
def test_f3_month_ranges_keep_every_gap(months, text):
    assert month_ranges(tuple(months)) == text


def test_f3_no_months_is_said_plainly_and_never_formatted_as_a_range():
    with pytest.raises(LifeContractError):
        month_ranges(())


def test_f3_each_position_keeps_its_own_months(main):
    """两份岗位各写各的月份，不拼成一段连续经历；一份没有领到工资就直说。"""
    _, _, outcome = main
    fb = visible_feedback(outcome.lives["u101.a95.r1"][1].settlement)
    p0 = fb.career.positions[0]
    positions = (
        p0.model_copy(update={"opportunity_id": "a95.job.shop_lead", "months": (1, 2, 3)}),
        p0.model_copy(update={"months": (7, 8, 9, 10, 11, 12)}),
        p0.model_copy(update={"opportunity_id": "a95.job.shop_parttime", "months": ()}),
    )
    fb = fb.model_copy(update={"career": fb.career.model_copy(update={"positions": positions})})
    memory = build_memory(
        clone_id="u101.a95.r1",
        city="US:SYNTH2026:A95",
        current_year=3,
        years=[
            MemoryYear(
                clone_id="u101.a95.r1",
                city="US:SYNTH2026:A95",
                year_index=2,
                feedback=fb,
                need_summary="x",
                rationale="y",
            )
        ],
    )
    lines = [f.text for f in memory.facts if "岗位" in f.text]
    assert lines == [
        "第 2 年：岗位 a95.job.shop_lead 领到工资的月份 1–3 月，共 3 个月",
        "第 2 年：岗位 a95.job.accountant_office 领到工资的月份 7–12 月，共 6 个月",
        "第 2 年：岗位 a95.job.shop_parttime 这一年没有领到工资",
    ]
