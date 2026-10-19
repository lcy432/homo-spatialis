"""E1-7A 代理规划闭环的离线演示（`DECISIONS/0030`）。零大模型调用，没有网络。

    uv run python scripts/demo_life_e1_7a.py --out runs/<新目录>

顺序：

1. 运行前检查（``orchestration.life_runner.preflight``）：运行配置、原计划名单（冻结；标准开局 v1、跨城配置）、城市事实 ↔ 评分条件、
   信息许可与规划器身份；写支持范围说明。
2. 用可控的 mock 规划器（``agentlayer.life_mock``）跑一个批次：两位用户、两座合成城、10 个分身、默认 3 个行动年；每年一次正常
   规划、最多两次修订；环境按月执行；E1-6 评价与城市汇总。运行期间禁止网络连接（有就记下并失败）。
3. 核对：三个生活故事的手算、只改理由不改生活、与直接调用已有入口一致、重放（0 次规划调用）、中断后恢复（不重复调用已接受
   年份的规划器）、信息隔离（全部请求的扫描、未来事件、隐藏精力条件下的诊断）。
4. E1-7A 收尾（DECISIONS/0030 第六节）的三个例子：恢复时只换了一条原始响应 → 拒绝；运行配置声明了受控行为、实际规划器没有传
   → 拒绝；第 2 年第 5 月病假没有工资 → 第 3 年的记忆写真实领薪月份。

全部数字来自合成样例：mock 不代表真实模型的能力，不是现实城市评价，也不排序真实城市；缺失范围不是置信区间。目录已存在就
拒绝，不覆盖旧结果。退出码：0 = 全部核对通过；1 = 有核对不通过或输入不合法。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import platform
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  (side effect: puts src/ on the path)
from agentlayer.life_mock import RuleBasedMockPlanner
from agentlayer.life_planning import (
    PlannerCall,
    build_memory,
    build_request,
    parse_output,
    plan_diagnostics,
    render_prompt,
    request_payload,
)
from agentlayer.life_views import ObservationPermission, assert_payload_clean, visible_feedback
from common.life_aggregation_contracts import MISSING_REASON_TEXT
from common.life_contracts import LifeContractError, YearAction, model_fingerprint
from common.life_planning_contracts import CLONE_RUN_STATUS_TEXT
from common.run_store import RunStore
from econ.life_evaluation import evaluate_clone
from econ.life_settlement import binding_from_policy, check_year_plan
from econ.life_year import ScoringInputs, run_life_year
from orchestration.life_runner import (
    KEY_SOURCES,
    RunOutcome,
    RunRefusedError,
    RunWorld,
    preflight,
    replay_run,
    resume_run,
    start_run,
)

REPO = Path(__file__).resolve().parent.parent
RUN_CONFIG = Path("configs/life/run_e1_7a.yaml")
RUN_ID = "demo-e1-7a"
ARM = "arm.main"
#: 重放与恢复比较的归档文件（运行清单与进度都不带时间戳，整个归档可以逐字节复现）。
ARCHIVE_FILES = (
    "manifest.json",
    "frozen_roster.json",
    "alignment.json",
    "support_scope.json",
    "progress.json",
    f"evaluations/{ARM}.json",
    f"reports/{ARM}.json",
    "failures.json",
    "report.md",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return r.stdout.strip()


@contextlib.contextmanager
def _no_network(attempts: list[str]) -> Iterator[None]:
    """运行期间禁止网络连接：任何连接尝试都记下来并失败（演示要求 0 次）。"""
    real_connect, real_create = socket.socket.connect, socket.create_connection

    def connect(self: socket.socket, address: Any) -> None:
        attempts.append(f"socket.connect {address!r}")
        raise OSError("E1-7A 演示禁止网络连接")

    def create(address: Any, *a: Any, **k: Any) -> socket.socket:
        attempts.append(f"socket.create_connection {address!r}")
        raise OSError("E1-7A 演示禁止网络连接")

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.create_connection = create  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.create_connection = real_create  # type: ignore[assignment]


def _cash(outcome: RunOutcome, clone: str) -> list[float]:
    out = []
    for r in outcome.lives.get(clone, ()):
        o = r.settlement.outcome
        assert o is not None and o.cash_end is not None
        out.append(o.cash_end.amount)
    return out


def _inputs(world: RunWorld, clone: str) -> tuple[Any, Any, Any, ScoringInputs]:
    entry = next(e for e in world.roster.entries if e.clone_id == clone)
    city = str(entry.city)
    catalog = world.bundle.catalog_for(city)
    policy = world.bundle.tax_policy(catalog.tax_policy_id)  # type: ignore[union-attr]
    profile = next(p for p in world.bundle.stated_profiles if p.person_id == entry.person_id)
    inputs = ScoringInputs(
        spec=world.scoring.spec,
        conditions=world.scoring.conditions_for(city),  # type: ignore[arg-type]
        preference_input=profile,
        hard_requirements=profile.hard_requirements,
    )
    return entry, catalog, binding_from_policy(policy), inputs  # type: ignore[arg-type]


def _direct(world: RunWorld, clone: str, actions: list[Any], planning_stop: Any = None) -> Any:
    """不经过运行器：用同样的年度行动直接调用 run_life_year 与 evaluate_clone。"""
    _entry, catalog, tax, inputs = _inputs(world, clone)
    state = world.bundle.initial_state(clone)
    results = []
    for action in actions:
        r = run_life_year(
            protocol=world.bundle.protocol,
            state=state,
            action=action,
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


def main() -> int:
    ap = argparse.ArgumentParser(
        description="E1-7A 代理规划闭环的离线演示（mock，合成，不排序真实城市）"
    )
    ap.add_argument("--out", type=Path, required=True, help="新的运行目录；已存在则拒绝")
    args = ap.parse_args()
    if args.out.exists():
        print(f"{args.out} 已存在：不覆盖旧结果，请换一个新目录")
        return 1
    try:
        world = preflight(RUN_CONFIG)
    except LifeContractError as exc:
        print(exc)
        return 1
    out = args.out
    out.mkdir(parents=True)

    def dump(name: str, data: Any) -> None:
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    # ------------------------------------------------------------ 1. 主运行（禁止网络）
    network: list[str] = []
    planner = RuleBasedMockPlanner(world.config.planner.scripts)
    with _no_network(network):
        outcome = start_run(world, planner, out / "run", run_id=RUN_ID)
    records = {r.clone_id: r for r in outcome.records}
    report = outcome.reports[ARM]
    check(
        "运行前检查：名单冻结（标准开局 v1）、跨城配置、城市事实与评分条件逐项一致",
        not world.alignment["problems"] and all(r["consistent"] for r in world.alignment["rows"]),
        f"{len(world.alignment['rows'])} 行事实 ↔ 评分条件核对全部一致；名单 {world.roster.roster_id} 冻结了 "
        f"{len(world.roster.entries)} 个名单项",
    )
    check(
        "没有网络调用",
        not network,
        "运行期间 socket 连接被禁止，尝试次数 0" if not network else f"有连接尝试：{network}",
    )
    planned = len(world.roster.entries)
    counted = sum(cr.planned for cr in report.city_reports)
    cem = sum(cr.complete + cr.exits + cr.missing for cr in report.city_reports)
    check(
        "原名单不缩小：每个名单项都有运行记录，C + E + M = 原计划人数",
        len(records) == planned and counted == planned and cem == planned,
        f"原计划 {planned} 人；运行记录 {len(records)} 条；城市报告合计 {counted} 人，C + E + M = {cem}",
    )
    limit = world.config.planning.global_call_limit
    per_year = world.config.planning.attempts_per_year
    check(
        "调用次数与修订上限",
        outcome.calls_used <= limit
        and planner.calls == outcome.calls_used
        and all(a <= per_year for r in outcome.records for a in r.attempts_by_year),
        f"规划调用 {outcome.calls_used} 次（上限 {limit}）；每个分身每年最多 {per_year} 份候选",
    )

    # ------------------------------------------------------------ 2. 三个生活故事
    fr = Fraction
    stories: dict[str, Any] = {}
    calls = RunStore(out / "run").records("calls")
    attempts = RunStore(out / "run").records("attempts")

    def req(clone: str, year: int, attempt: int = 1) -> dict[str, Any]:
        return next(
            c["request"]
            for c in calls
            if (c["clone_id"], c["year_index"], c["attempt"]) == (clone, year, attempt)
        )

    def items(clone: str, year: int) -> list[str]:
        r = outcome.lives[clone][year - 1].settlement.outcome
        assert r is not None
        return sorted(e.opportunity_id for e in r.executed if e.status != "rejected")

    # 故事一：成长后调整计划
    a0, b0 = _cash(outcome, "u101.a95.r0"), _cash(outcome, "u101.b96.r0")
    hand_a0 = [
        2000 + 12 * (2000 - 1380) - 1200 - 600,
        2000 + 12 * (2000 - 1380) - 1200 - 600 + 12 * (3000 - 1380) - 600,
    ]
    hand_a0.append(hand_a0[1] + 12 * (3250 - 1380) - 600)
    jobs_y2 = {
        o["opportunity_id"]: (o.get("qualification") or {}).get("status")
        for o in req("u101.a95.r0", 2)["observation"]["opportunities"]
        if o["category"] == "job"
    }
    jobs_y1 = {
        o["opportunity_id"]: (o.get("qualification") or {}).get("status")
        for o in req("u101.a95.r0", 1)["observation"]["opportunities"]
        if o["category"] == "job"
    }
    story1_ok = (
        "a95.training.accounting_course" in items("u101.a95.r0", 1)
        and "a95.job.accountant_office" in items("u101.a95.r0", 2)
        and jobs_y1.get("a95.job.accountant_office") == "not_yet"
        and jobs_y2.get("a95.job.accountant_office") == "available_now"
        and a0 == hand_a0
        and b0 == [3440.0, 4880.0, 6320.0]
        and not any("training" in x for y in (1, 2, 3) for x in items("u101.b96.r0", y))
    )
    stories["story1_growth"] = {
        "who": "α（u101）A95 的 r0；对照 B96 的 r0（同一个人、同样开局）",
        "year1": items("u101.a95.r0", 1),
        "year2": items("u101.a95.r0", 2),
        "year3": items("u101.a95.r0", 3),
        "accountant_qualification_y1_y2": [
            jobs_y1.get("a95.job.accountant_office"),
            jobs_y2.get("a95.job.accountant_office"),
        ],
        "cash_end_by_year": a0,
        "hand": "第 1 年：2,000 + 12 × (2,000 组长月薪 − 1,000 房租 − 380 食品) − 1,200 学费 − 600 二手洗衣机 = 7,640；"
        "第 2 年：7,640 + 12 × (3,000 会计月薪 − 1,380) − 600 = 26,480；第 3 年（年初已在合成办公室工作，高级会计现在能选）："
        "26,480 + 12 × (3,250 − 1,380) − 600 = 48,320。合成税：全年 40,000 以内 0%",
        "b96_cash_end_by_year": b0,
        "b96_hand": "B96 没有要求会计资格的岗位，mock 不报名：2,000 + 12 × (1,500 − 1,380) = 3,440，之后每年 +1,440",
    }
    check(
        "故事一：第 1 年学习，第 2 年按真实资格换岗（现金逐年手算一致）",
        story1_ok,
        f"A95 r0 年末现金 {a0}（手算 {hand_a0}）；会计岗位资格 第 1 年 {jobs_y1.get('a95.job.accountant_office')} → 第 2 年 "
        f"{jobs_y2.get('a95.job.accountant_office')}；B96 r0 {b0}，没有报名",
    )

    # 故事二：修订与生活不幸的区别
    first = next(
        a
        for a in attempts
        if (a["clone_id"], a["year_index"], a["attempt"]) == ("u101.a95.r1", 1, 1)
    )
    diag = first["diagnostics"][0] if first["diagnostics"] else {}
    hand_burden = fr(120) + 80 + 48 + fr(25 * 70, 60) * fr(3, 4) + 15 + 2 + 6
    r1 = _cash(outcome, "u101.a95.r1")
    r1_attempts = records["u101.a95.r1"].attempts_by_year
    y2 = outcome.lives["u101.a95.r1"][1].settlement.outcome
    assert y2 is not None and y2.health is not None
    sick = [
        (m.month, m.state, None if m.treatment is None else m.treatment.status)
        for m in y2.health.months
        if m.state != "healthy"
    ]
    story2_ok = (
        first["disposition"] == "revise"
        and diag.get("code") == "energy_over_limit"
        and fr(diag["used"]["exact"]) == hand_burden
        and fr(diag["limit"]["exact"]) == 250
        and r1_attempts == (2, 1, 1)
        and sick == [(5, "major", "treated")]
        and r1[1] == a0[1] - 3000 - 2000
    )
    stories["story2_revision_vs_misfortune"] = {
        "who": "α（u101）A95 的 r1：第 1 年第一份草稿多做一份工作；第 2 年第 5 月大病",
        "first_draft_diagnostic": diag.get("text"),
        "hand_burden": "第 1 月负担 = 组长 120 + 兼职 80 + 会计课 48 + 通勤 (10 + 15) 次 × 70 分钟 ÷ 60 × 0.75 = 21.875 + 做饭 15 + "
        "洗衣（有二手洗衣机，残余 4 小时 × 0.5）2 + 打扫 6 = 292.875 > 公开额度 250",
        "accepted_attempt_year1": r1_attempts[0],
        "year2_attempts": r1_attempts[1],
        "year2_illness": sick,
        "cash_end_by_year": r1,
        "hand": "第 2 年年末 = r0 的 26,480 − 第 5 月病假的工资 3,000 − 住院 2,000 = 21,480：疾病发生在执行中，不回到年初重选，"
        "也不重抽；第 2 年只用了 1 次规划调用",
    }
    check(
        "故事二：超出额度的计划可以修订；疾病发生后的生活不能重来",
        story2_ok,
        f"第 1 年第 1 份候选：{diag.get('text', '')[:80]}…；接受第 {r1_attempts[0]} 份；第 2 年调用 {r1_attempts[1]} 次，"
        f"病情 {sick}；年末现金 {r1}",
    )

    # 故事三：同批不同结局
    outcomes = {c: records[c].status for c in world.order}
    by_status: dict[str, list[str]] = {}
    for c, s in outcomes.items():
        by_status.setdefault(s, []).append(c)
    b96_alpha = next(
        cr for cr in report.city_reports if cr.user_id == "user.alpha" and cr.city.code == "B96"
    )
    a95_alpha = next(
        cr for cr in report.city_reports if cr.user_id == "user.alpha" and cr.city.code == "A95"
    )
    r3 = next(e for e in b96_alpha.entries if e.clone_id == "u101.b96.r3")
    story3_ok = (
        sorted(by_status) == ["completed", "life_exit", "planning_stopped", "stopped"]
        and by_status["life_exit"] == ["u101.a95.r2"]
        and sorted(by_status["stopped"]) == ["u101.b96.r1", "u101.b96.r2"]
        and by_status["planning_stopped"] == ["u101.b96.r3"]
        and r3.missing_reason == "plan_or_execution"
        and r3.known_weight.fraction() == 12
        and a95_alpha.exits == 1
        and b96_alpha.planned == 4
    )
    stories["story3_same_batch"] = {
        "outcomes": {c: CLONE_RUN_STATUS_TEXT[s] for c, s in outcomes.items()},
        "a95_alpha": {
            "planned": a95_alpha.planned,
            "complete": a95_alpha.complete,
            "exits": a95_alpha.exits,
            "mean_j": a95_alpha.mean_j_display,
            "exit_clone_ids": list(a95_alpha.exit_clone_ids),
        },
        "b96_alpha": {
            "planned": b96_alpha.planned,
            "complete": b96_alpha.complete,
            "missing": b96_alpha.missing,
            "mean_j": b96_alpha.mean_j_display,
            "range": [b96_alpha.range_lower_display, b96_alpha.range_upper_display],
            "missing_reasons": {
                MISSING_REASON_TEXT[x.reason]: x.count for x in b96_alpha.missing_reasons
            },
        },
        "r3_partial": {
            "reason": MISSING_REASON_TEXT[r3.missing_reason or "not_run"],
            "known_months": float(r3.known_weight.fraction()),
            "detail": r3.detail,
        },
        "hand": "A95 r2：第 1 月 2,000 + 2,000 − 1,380 − 1,200 学费 − 600 洗衣机 = 820；第 2 月大病病假没有工资，月初 820 < 1,380"
        " → 出局（差 560）。B96 r1、r2：要住院而本城住院资料未知 → 停在发病那个月（资料缺口，不是出局）。B96 r3：第 2 年三份候选"
        "都不是 JSON → 规划停止；第 1 年 12 个月照常算作已知月份",
    }
    check(
        "故事三：同批不同结局，报告保留所有人",
        story3_ok,
        "完成 "
        + str(len(by_status.get("completed", [])))
        + "、生活出局 1、停止 2、规划停止 1；B96 α 的缺失原因 "
        + str(stories["story3_same_batch"]["b96_alpha"]["missing_reasons"])
        + f"；r3 的已知月份 {float(r3.known_weight.fraction()):g}",
    )

    # ------------------------------------------------------------ 3. 与直接调用一致；只改理由
    direct_rows = []
    direct_ok = True
    accepted = RunStore(out / "run").records("accepted")
    by_clone: dict[str, list[Any]] = {}
    for a in accepted:
        by_clone.setdefault(a["clone_id"], []).append(YearAction.model_validate(a["action"]))
    runner_evs = {e.clone_id: e for e in outcome.evaluations[ARM]}
    for clone, actions in by_clone.items():
        if clone not in runner_evs:
            continue
        stop = records[clone].planning_stop
        _, ev = _direct(world, clone, actions, stop)
        same = ev.result_fingerprint == runner_evs[clone].result_fingerprint
        direct_ok &= same
        direct_rows.append(
            {"clone_id": clone, "identical": same, "fingerprint": ev.result_fingerprint}
        )
    check(
        "运行器接入的 E1-6 结果与相同行动直接调用已有入口的结果一致",
        direct_ok and len(direct_rows) == len(runner_evs),
        f"{len(direct_rows)} 份单分身评价逐字节相同（结果指纹）",
    )
    actions = by_clone["u101.a95.r0"]
    reworded = [
        a.model_copy(update={"rationale": "（换一种说法）同样的行动，只换理由文字"})
        for a in actions
    ]
    res1, ev1 = _direct(world, "u101.a95.r0", actions)
    res2, ev2 = _direct(world, "u101.a95.r0", reworded)
    same_life = all(
        model_fingerprint(x.settlement.outcome) == model_fingerprint(y.settlement.outcome)  # type: ignore[arg-type]
        and model_fingerprint(x.score) == model_fingerprint(y.score)  # type: ignore[arg-type]
        for x, y in zip(res1, res2, strict=True)
    )
    same_values = (
        ev1.combined.j == ev2.combined.j
        and ev1.actual.l_actual == ev2.actual.l_actual
        and ev1.valuation.v_reference == ev2.valuation.v_reference
    )
    check(
        "只改理由、行动相同：生活数值与评价不变",
        same_life and same_values,
        "三年的结算结果与逐月评分指纹相同，L、V、J 相同（年度绑定与评价结果的身份指纹不同，因为它们记下了提交的计划原文）",
    )
    dump(
        "direct_and_rationale.json",
        {
            "direct_calls": direct_rows,
            "rationale_only": {
                "clone_id": "u101.a95.r0",
                "outcomes_and_scores_identical": same_life,
                "l_v_j_identical": same_values,
                "binding_fingerprints_differ": [
                    x.binding_fingerprint != y.binding_fingerprint
                    for x, y in zip(res1, res2, strict=True)
                ],
            },
        },
    )

    # ------------------------------------------------------------ 4. 重放与恢复
    replay = replay_run(world, out / "run")
    dump("replay.json", replay)
    check(
        "重放：0 次规划调用，从同一初态重建的绑定、评价与汇总与归档一致",
        replay["planner_calls"] == 0
        and replay["bindings_identical"]
        and all(replay["evaluations_identical"].values())
        and all(replay["reports_identical"].values()),
        f"{len(replay['clones'])} 个名单项；年度绑定全部相同；评价与汇总相同",
    )
    stop_at = 12
    with tempfile.TemporaryDirectory(prefix="e1-7a-resume-") as tmp:
        tmp_run = Path(tmp) / "run"
        first_planner = RuleBasedMockPlanner(world.config.planner.scripts)
        interrupted = start_run(
            world, first_planner, tmp_run, run_id=RUN_ID, stop_after_calls=stop_at
        )
        snapshot = json.loads((tmp_run / "progress.json").read_text(encoding="utf-8"))
        second = RuleBasedMockPlanner(world.config.planner.scripts)
        resumed = resume_run(world, second, tmp_run)
        compare = {
            name: _sha256(tmp_run / name) == _sha256(out / "run" / name) for name in ARCHIVE_FILES
        }
        compare["archive.sqlite（逐条记录）"] = (
            RunStore(tmp_run).dump() == RunStore(out / "run").dump()
        )
    resume_ok = (
        interrupted.status == "interrupted"
        and resumed.status == "completed"
        and first_planner.calls == stop_at
        and second.calls == outcome.calls_used - stop_at
        and all(compare.values())
    )
    dump(
        "resume_check.json",
        {
            "stop_after_calls": stop_at,
            "interrupted_status": interrupted.status,
            "progress_at_interruption": snapshot,
            "first_planner_calls": first_planner.calls,
            "resumed_planner_calls": second.calls,
            "resumed_status": resumed.status,
            "files_identical_to_uninterrupted_run": compare,
        },
    )
    check(
        "恢复：在第 12 次调用后中断，恢复后不重复调用已接受年份的规划器，结果与不中断的运行逐字节相同",
        resume_ok,
        f"中断前 {first_planner.calls} 次、恢复后 {second.calls} 次（合计 {outcome.calls_used}）；{sum(compare.values())}/"
        f"{len(compare)} 个归档文件逐字节相同",
    )

    # ------------------------------------------------------------ 5. 信息隔离
    leaks: list[str] = []
    for c in calls:
        p = c["request"]
        try:
            assert_payload_clean(p)
        except LifeContractError as exc:
            leaks.append(f"{c['request_id']}: {exc}")
        text = json.dumps(p, ensure_ascii=False)
        for ev in world.bundle.protocol.illness_events.injected:  # type: ignore[union-attr]
            if ev.note in text:
                leaks.append(f"{c['request_id']}: 出现了指定疾病事件的说明")
        for key in (
            '"risk"',
            '"cells"',
            '"injected"',
            '"illness_events"',
            '"factor"',
            '"gamma"',
            '"tau"',
        ):
            if key in text:
                leaks.append(f"{c['request_id']}: 出现了内部键 {key}")
        y = p["year_index"]
        health = p["observation"].get("health") or {}
        for e in health.get("history", []) + ([health["ongoing"]] if health.get("ongoing") else []):
            if e["onset_year"] >= y:
                leaks.append(
                    f"{c['request_id']}: 病史里有第 {e['onset_year']} 年（未来或当年）的疾病"
                )
    # 隐藏精力的研究式条件：同一份超额草稿的诊断不给额度、合计与超出量
    hidden = ObservationPermission(
        condition="product_stated_profile",
        show_stated_dimension_points=True,
        show_soft_preferences=True,
        show_hard_requirements=True,
        show_free_text=True,
        show_energy_level=False,
        show_stated_material_points=True,
        permission_version="e1-7a-hidden-energy-v1",
    )
    entry, catalog, tax, _ = _inputs(world, "u101.a95.r1")
    state = world.bundle.initial_state("u101.a95.r1")
    profile = next(p for p in world.bundle.stated_profiles if p.person_id == entry.person_id)
    request = build_request(
        request_id="demo/hidden-energy",
        attempt=1,
        attempts_allowed=per_year,
        permission=hidden,
        profile=profile,
        state=state,  # type: ignore[arg-type]
        catalog=catalog,
        protocol=world.bundle.protocol,
        facts=world.facts.sheet_for(str(entry.city)),  # type: ignore[arg-type]
        memory=build_memory(clone_id="u101.a95.r1", city=str(entry.city), current_year=1, years=()),
        last_feedback=None,
        revision=None,
        tax_policy=world.bundle.tax_policy(catalog.tax_policy_id),  # type: ignore[union-attr]
    )
    payload = request_payload(request)
    system, user = render_prompt(payload)
    draft = RuleBasedMockPlanner(world.config.planner.scripts).plan(
        PlannerCall("demo/hidden-energy", system, user, payload)
    )
    parsed = parse_output(draft, person_id="u101", clone_id="u101.a95.r1", year_index=1)
    assert parsed.action is not None
    hidden_check = check_year_plan(
        protocol=world.bundle.protocol,
        state=state,
        action=parsed.action,
        catalog=catalog,
        tax=tax,  # type: ignore[arg-type]
    )
    hidden_diag = plan_diagnostics(hidden_check, parsed.action, hidden)
    hidden_text = json.dumps([d.model_dump(mode="json") for d in hidden_diag], ensure_ascii=False)
    hidden_ok = (
        payload["observation"]["energy_level"] is None
        and payload["observation"]["resources"]["monthly_energy_quota"] is None
        and all(d.limit is None and d.used is None and d.excess is None for d in hidden_diag)
        and "250" not in hidden_text
        and "292.875" not in hidden_text
    )
    dump(
        "information_isolation.json",
        {
            "requests_scanned": len(calls),
            "problems": leaks,
            "hidden_energy_payload_quota": payload["observation"]["resources"][
                "monthly_energy_quota"
            ],
            "hidden_energy_diagnostics": [d.model_dump(mode="json") for d in hidden_diag],
            "note": "隐藏精力时诊断只说超了额度，不给额度、合计与超出量；各项负担是公开成本（小时 × 每小时负担），代理仍可能"
            "据此推算额度的范围——这是已有边界，不是完全未知",
        },
    )
    check(
        "信息隔离：全部请求通过扫描，没有未来事件、风险表或换算参数；隐藏精力时诊断不泄漏额度",
        not leaks and hidden_ok,
        f"扫描 {len(calls)} 份请求，问题 {len(leaks)} 条；隐藏精力的诊断：{hidden_diag[0].text[:60]}…",
    )

    # ------------------------------------------------------------ 收尾 F1–F3 的三个例子
    with tempfile.TemporaryDirectory(prefix="e1-7a-closeout-") as tmp:
        swapped_run = Path(tmp) / "swapped"
        start_run(
            world,
            RuleBasedMockPlanner(world.config.planner.scripts),
            swapped_run,
            run_id=RUN_ID,
            stop_after_calls=4,
        )
        rows = RunStore(swapped_run).records("calls")
        old_attempt = RunStore(swapped_run).records("attempts")[-1]
        second_draft = next(c for c in calls if c["request_id"].endswith("u101.a95.r1/y1/a2"))
        rows[-1]["response_text"] = second_draft["response_text"]
        RunStore(swapped_run).replace("calls", rows)

        def files(root: Path) -> dict[str, bytes]:
            return {
                q.relative_to(root).as_posix(): q.read_bytes()
                for q in root.rglob("*")
                if q.is_file()
            }

        before = files(swapped_run)
        resume_planner = RuleBasedMockPlanner(world.config.planner.scripts)
        swapped_refusal: str | None = None
        try:
            resume_run(world, resume_planner, swapped_run)
        except RunRefusedError as exc:
            swapped_refusal = str(exc)
        unchanged = files(swapped_run) == before
        no_scripts = RuleBasedMockPlanner()
        planner_refusal: str | None = None
        try:
            start_run(world, no_scripts, Path(tmp) / "no-scripts", run_id=RUN_ID)
        except RunRefusedError as exc:
            planner_refusal = str(exc)
        no_archive = not (Path(tmp) / "no-scripts").exists()
    r1_y2 = outcome.lives["u101.a95.r1"][1]
    paid = list(visible_feedback(r1_y2.settlement).career.positions[0].months)  # type: ignore[union-attr]
    pay_line = next(
        f["text"]
        for f in req("u101.a95.r1", 3)["memory"]["facts"]
        if f["text"].startswith("第 2 年：岗位")
    )
    dump(
        "closeout_examples.json",
        {
            "f1_swapped_response": {
                "where": "u101.a95.r1 第 1 年第 1 份候选（第 4 次调用）",
                "recorded_disposition": old_attempt["disposition"],
                "refusal": swapped_refusal,
                "planner_calls_on_resume": resume_planner.calls,
                "archive_unchanged": unchanged,
            },
            "f2_missing_scripts": {
                "declared_scripts": [
                    s.model_dump(mode="json") for s in world.config.planner.scripts
                ],
                "actual_scripts": [],
                "refusal": planner_refusal,
                "planner_calls": no_scripts.calls,
                "archive_created": not no_archive,
            },
            "f3_paid_months": {"actual_paid_months": paid, "memory_line": pay_line},
        },
    )
    check(
        '收尾 F1：恢复时只换了一条原始响应（原检查记录是"精力超限、要修订"）→ 拒绝；规划器 0 次调用，归档不变',
        swapped_refusal is not None
        and "response_fingerprint" in swapped_refusal
        and old_attempt["disposition"] == "revise"
        and resume_planner.calls == 0
        and unchanged,
        "拒绝原因：" + (swapped_refusal or "（没有拒绝）").splitlines()[-1].strip()[:120],
    )
    check(
        "收尾 F2：运行配置声明了受控行为、实际规划器没有传 → 建归档与第一次调用之前拒绝",
        planner_refusal is not None
        and "声明了但实际没有" in planner_refusal
        and no_scripts.calls == 0
        and no_archive,
        "拒绝原因：" + (planner_refusal or "（没有拒绝）").splitlines()[-1].strip()[:160],
    )
    check(
        "收尾 F3：第 2 年第 5 月病假没有工资，第 3 年的记忆写真实领薪月份",
        5 not in paid and pay_line.endswith("领到工资的月份 1–4、6–12 月，共 11 个月"),
        f"实际领薪月份 {paid}；记忆原文：{pay_line}",
    )

    # ------------------------------------------------------------ 6. 样例请求与证据文件
    sample = req("u101.a95.r1", 1, 2)
    dump("sample_request_revision.json", sample)
    (out / "sample_prompt_revision.txt").write_text(
        "\n\n".join(render_prompt(sample)), encoding="utf-8", newline="\n"
    )
    dump("stories.json", stories)
    dump("checks.json", checks)
    dump(
        "command.json",
        {
            "argv": sys.argv,
            "python_executable": sys.executable,
            "cwd": str(Path.cwd()),
            "reproduce": "uv run python scripts/demo_life_e1_7a.py --out <新目录>",
        },
    )
    status = _git("status", "--porcelain")
    inputs = [
        RUN_CONFIG.as_posix(),
        world.config.roster,
        world.config.city_facts,
        world.roster_config.life_input,
        world.roster_config.scoring_config,
        *(a.evaluation_config for a in world.roster_config.arms),
    ]
    dump(
        "environment.json",
        {
            "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "git_head": _git("rev-parse", "HEAD"),
            "git_dirty": None if status is None else bool(status),
            "note": "工作区未提交时，git_head 不能代表实际运行的代码；以下指纹才是实际输入。运行归档本身不带时间戳，可以逐字节复现",
            "fingerprints_sha256": {
                **{f"src/{rel}": _sha256(REPO / "src" / rel) for rel in KEY_SOURCES},
                **{f_: _sha256(REPO / f_) for f_ in inputs},
                "scripts/demo_life_e1_7a.py": _sha256(REPO / "scripts/demo_life_e1_7a.py"),
            },
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
    )
    passed = sum(c["passed"] for c in checks)
    md = [
        "# E1-7A 代理规划闭环演示（合成、mock 规划器）",
        "",
        "**全部数字来自合成样例；规划器是可控的 mock，不代表真实大语言模型的能力；不是现实城市评价，也不排序真实城市；缺失范围不是"
        "置信区间。** 运行没有网络调用。",
        "",
        f"核对：{passed}/{len(checks)} 通过。运行归档在 `run/`（运行清单、支持范围说明、冻结名单、事实核对、每次调用的完整请求与"
        "响应、处置记录、接受的行动、进度、评价、城市汇总与中文运行报告 `run/report.md`）。",
        "",
        "| 核对 | 结果 | 说明 |",
        "|---|---|---|",
    ]
    md += [
        f"| {c['check']} | {'通过' if c['passed'] else '**不通过**'} | {c['detail'].replace('|', '/')} |"
        for c in checks
    ]
    md += [
        "",
        "## 三个生活故事",
        "",
        f"1. **成长后调整计划**（{stories['story1_growth']['who']}）：{stories['story1_growth']['hand']}。{stories['story1_growth']['b96_hand']}。",
        f"2. **修订与生活不幸的区别**（{stories['story2_revision_vs_misfortune']['who']}）：{stories['story2_revision_vs_misfortune']['hand_burden']}；"
        f"诊断原文：「{stories['story2_revision_vs_misfortune']['first_draft_diagnostic']}」。{stories['story2_revision_vs_misfortune']['hand']}。",
        f"3. **同批不同结局**：{stories['story3_same_batch']['hand']}。",
        "",
        "文件：`run/`（归档）、`stories.json`、`direct_and_rationale.json`、`replay.json`、`resume_check.json`、"
        "`information_isolation.json`、`sample_request_revision.json` 与 `sample_prompt_revision.txt`（一次修订请求的完整内容）、"
        "`closeout_examples.json`（E1-7A 收尾 F1–F3 的三个例子）、`checks.json`、`command.json`、`environment.json`。",
    ]
    (out / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    for c in checks:
        print(("通过  " if c["passed"] else "不通过 ") + c["check"])
    print(f"{passed}/{len(checks)} 项核对通过；归档：{out / 'run'}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
