"""从已结束运行的归档零调用重建 ``RunOutcome``（2026-10-18，LangGraph 外层流程原型的接入需要）。

为什么需要：产品结果投影（``product.results.project_result``）要用运行器返回的 ``RunOutcome``——里面是带封存年度绑定的生活对象，
不能从 JSON 反序列化。原来只有“刚跑完的同一个进程”手上有它；进程中途退出、或外层流程在 E1 跑完之后才恢复时，就没有了。
这时不能再模拟一次，也不能拿 JSON 拼一个假的结果对象。

怎么做（不改 ``life_runner.py``，那是运行清单里的关键源码；本模块只在生活结束之后读归档，不影响运行语义，所以不进关键源码名单）：

1. 先调用 E1 官方的零调用重放 ``replay_run``：运行清单（全部内容身份，含关键源码）、归档记录、逐年封存绑定、评价与城市报告都必须与
   归档逐项相同；任何一项不同就拒绝。
2. 再用归档里已接受的行动，按 ``replay_run`` 同一条执行路径（``run_life_year``）重建每个分身的生活对象，评价与汇总用已有的
   ``evaluate_batch``；得到的评价与报告再与归档逐项比较一次（不相信第 1 步之后的任何缓存）。
3. 返回 ``RunOutcome``：分身结局、调用次数、暂停原因与停下原因取自进度记录（与运行器写进度时是同一份数据）。

只接受已经出评价与汇总的运行（``completed``、``paused``、``budget_exhausted``）；还在进行、中断或停下的运行拒绝（它们走
``resume_run``）。全程不调用规划器，不写任何文件。
"""

from __future__ import annotations

from pathlib import Path

from common.life_aggregation_contracts import RunFailure
from common.life_evaluation_contracts import PlanningStop
from common.life_feedback_contracts import LifeYearResult
from common.life_planning_contracts import AcceptedYear, RunProgress
from econ.life_year import ScoringInputs, run_life_year
from orchestration.life_archive import RunArchive
from orchestration.life_runner import (
    RunOutcome,
    RunRefusedError,
    RunWorld,
    _check_manifest,
    _tax,
    evaluate_batch,
    load_archive_records,
    replay_run,
)

#: 已经出评价与汇总的运行结局（运行器在这三种情况下都调用 ``_outcome(evaluate=True)``）。
FINISHED_WITH_REPORT = frozenset({"completed", "paused", "budget_exhausted"})


def outcome_from_archive(world: RunWorld, run_dir: Path) -> RunOutcome:
    """已结束运行的 ``RunOutcome``，零调用、只读重建；核对不过就 ``RunRefusedError``（原归档不变）。"""
    archive = RunArchive(Path(run_dir), create=False)
    progress = RunProgress.model_validate(archive.read_json("progress.json"))
    if progress.status not in FINISHED_WITH_REPORT:
        raise RunRefusedError(
            f"这次运行的状态是 {progress.status}，还没有评价与汇总：不能从归档重建结果（未结束的运行走恢复入口）"
        )
    check = replay_run(world, Path(run_dir))
    problems = []
    if check.get("partial"):
        problems.append("官方重放把它当作未结束的运行")
    if not check.get("bindings_identical"):
        problems.append("逐年封存绑定与归档不同")
    problems += [
        f"运行臂 {a} 的评价与归档不同" for a, ok in check["evaluations_identical"].items() if not ok
    ]
    problems += [
        f"运行臂 {a} 的城市报告与归档不同" for a, ok in check["reports_identical"].items() if not ok
    ]
    if problems:
        raise RunRefusedError("从归档重建结果之前的官方重放核对没有通过：" + "；".join(problems))

    manifest = _check_manifest(world, archive)
    records = {r.clone_id: r for r in progress.clones}
    archived = load_archive_records(
        world,
        archive,
        manifest.run_id,
        pending_ok=False,
        exempt=frozenset(r.clone_id for r in progress.clones if r.status == "program_error"),
    )
    accepted: dict[str, list[AcceptedYear]] = {}
    for y in archived.accepted.values():
        accepted.setdefault(y.clone_id, []).append(y)
    lives: dict[str, tuple[LifeYearResult, ...]] = {}
    stops: dict[str, PlanningStop] = {}
    failures: dict[str, RunFailure] = {}
    for entry in world.roster.entries:
        r = records.get(entry.clone_id)
        state = world.bundle.initial_state(entry.clone_id)
        city = str(entry.city)
        catalog = world.bundle.catalog_for(city)
        profile = world.bundle.profile_for(entry.person_id)
        conditions = world.scoring.conditions_for(city)
        assert state is not None and catalog is not None
        assert profile is not None and conditions is not None
        inputs = ScoringInputs(
            spec=world.scoring.spec,
            conditions=conditions,
            preference_input=profile,
            hard_requirements=profile.hard_requirements,
        )
        results: list[LifeYearResult] = []
        for y in sorted(accepted.get(entry.clone_id, []), key=lambda x: x.year_index):
            result = run_life_year(
                protocol=world.bundle.protocol,
                state=state,
                action=y.action,
                catalog=catalog,
                tax=_tax(world.bundle, city),
                scoring=inputs,
            )
            if result.binding_fingerprint != y.binding_fingerprint or result.handoff != y.handoff:
                raise RunRefusedError(
                    f"重建 {entry.clone_id} 第 {y.year_index} 年得到的绑定与归档不同：不能用它投影结果"
                )
            results.append(result)
            if result.next_state is not None and result.handoff == "next_year_ready":
                state = result.next_state
        if r is not None and r.status != "program_error" and results:
            lives[entry.clone_id] = tuple(results)
        if r is not None and r.planning_stop is not None and results:
            stops[entry.clone_id] = r.planning_stop
        if r is not None and r.failure is not None:
            failures[entry.clone_id] = r.failure

    evaluations, batch_failures, reports = evaluate_batch(world, lives, stops, failures)
    for arm_id, evs in evaluations.items():
        if [e.model_dump(mode="json") for e in evs] != archive.read_json(
            f"evaluations/{arm_id}.json"
        ):
            raise RunRefusedError(f"重建的运行臂 {arm_id} 评价与归档不同：不能用它投影结果")
    for arm_id, report in reports.items():
        if report.model_dump(mode="json") != archive.read_json(f"reports/{arm_id}.json"):
            raise RunRefusedError(f"重建的运行臂 {arm_id} 城市报告与归档不同：不能用它投影结果")
    return RunOutcome(
        run_id=manifest.run_id,
        status=progress.status,
        calls_used=progress.calls_used,
        records=tuple(records[c] for c in world.order if c in records),
        lives=lives,
        evaluations=evaluations,
        failures=batch_failures,
        reports=reports,
        archive_path=archive.path,
        pause_reason=progress.pause_reason,
        halt_kind=progress.halt_kind,
    )
