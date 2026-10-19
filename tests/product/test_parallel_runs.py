"""每个分身并行地过生活（``workers`` > 1）：零网络、零费用（mock 规划器）。

用全规模的产品流程样例（两座城 × 每城 4 个分身 × 3 年，``scripts/run_product_full.compile_full``）：

* 并行与顺序的结果相同：每个分身的结局、已接受的行动与绑定、评价与城市汇总逐项相同，用掉的调用次数相同；只有归档里
  记录的先后不同。
* 全局调用上限在并行时也不会超过（发送前占位），每个名单项都有结局，原计划名单不缩小。
* 并行运行在第 N 次调用之后中断：正在过的分身在各自的下一次调用前停住，进度里每人最多一条"正在规划的年份"；并行恢复之后
  与不中断的运行相同，总调用次数也相同。
* 某个分身的服务停下（PlannerHalt）：批次停下、别人停住、没有评价；换一个好的规划器恢复之后与不中断的运行相同。
* Claude 传输层只在启动命令行那一下换进程环境（锁也只锁那一下），之后原样恢复。
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import os
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentlayer.life_mock import ProductMockPlanner, mock_planner_for
from agentlayer.life_planning import PlannerHalt
from common.life_planning_contracts import RunProgress
from common.run_store import RunStore
from orchestration.life_runner import preflight, resume_run, start_run

REPO = Path(__file__).resolve().parents[2]
RUN_ID = "par"


def _full():
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("run_product_full")


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """编译一次全规模样例，并顺序跑一遍作为参照。"""
    from product import scenarios as sc

    load, ws = sc.load_pack, sc.workspace
    try:
        s = _full().compile_full(tmp_path_factory.mktemp("full") / "ws", repeats=4, years=None)
    finally:
        sc.load_pack, sc.workspace = load, ws
    world = s.pre.world
    assert len(world.order) == 8
    with contextlib.chdir(s.work):
        ref = start_run(world, mock_planner_for(world.config.planner), Path("ref"), run_id=RUN_ID)
    assert ref.status == "completed"
    return s.work, world, ref


def _run(work: Path, world, out: str, planner=None, **kw):
    with contextlib.chdir(work):
        return start_run(
            world, planner or mock_planner_for(world.config.planner), Path(out), run_id=RUN_ID, **kw
        )


def _by_clone(run_dir: Path, stream: str) -> list[dict[str, Any]]:
    """一种记录按（分身、年份、第几份候选）排好：并行时只有先后不同。"""
    return sorted(
        RunStore(run_dir).records(stream),
        key=lambda r: (r["clone_id"], r["year_index"], r.get("attempt", 0)),
    )


def _same_as_reference(work: Path, ref, outcome, out: str) -> None:
    assert outcome.status == "completed"
    assert [r.model_dump(mode="json") for r in outcome.records] == [
        r.model_dump(mode="json") for r in ref.records
    ]
    assert outcome.calls_used == ref.calls_used
    for stream in ("accepted", "attempts", "calls"):
        assert _by_clone(work / out, stream) == _by_clone(work / "ref", stream), stream
    for arm in ref.reports:
        assert outcome.reports[arm].model_dump(mode="json") == ref.reports[arm].model_dump(
            mode="json"
        )
        assert [e.model_dump(mode="json") for e in outcome.evaluations[arm]] == [
            e.model_dump(mode="json") for e in ref.evaluations[arm]
        ]


class _Threads(ProductMockPlanner):
    """记下每次调用在哪个线程、同时有几个调用在进行。"""

    def __init__(self, scripts) -> None:
        super().__init__(scripts)
        self.names: set[str] = set()
        self.active = self.peak = 0
        self._guard = threading.Lock()

    def plan(self, call):
        with self._guard:
            self.names.add(threading.current_thread().name)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            threading.Event().wait(0.02)  # 让别的分身有机会同时进来
            return super().plan(call)
        finally:
            with self._guard:
                self.active -= 1


def test_parallel_lives_equal_the_sequential_run(sample):
    work, world, ref = sample
    planner = _Threads(world.config.planner.scripts)
    outcome = _run(work, world, "par8", planner, workers=8)
    assert len(planner.names) > 1 and planner.peak > 1  # 真的有几个分身同时在规划
    _same_as_reference(work, ref, outcome, "par8")


def test_the_global_call_limit_holds_in_parallel(sample):
    work, _world, _ref = sample
    path = work / "compiled/full/run.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["run"]["planning"]["global_call_limit"] = 10
    small = work / "compiled/full/run_limit10.yaml"
    small.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8", newline="\n")
    with contextlib.chdir(work):
        limited = preflight(small.relative_to(work))
    planner = mock_planner_for(limited.config.planner)
    outcome = _run(work, limited, "limit", planner, workers=8)
    assert outcome.status == "budget_exhausted"
    assert outcome.calls_used == planner.calls == 10  # 一次都没有超
    assert len(RunStore(work / "limit").records("calls")) == 10
    assert [r.clone_id for r in outcome.records] == list(limited.order)  # 名单不缩小
    statuses = {r.status for r in outcome.records}
    assert statuses <= {"planning_stopped", "not_run", "completed", "life_exit"}


def test_an_interrupted_parallel_run_resumes_to_the_same_lives(sample):
    work, world, ref = sample
    p1 = mock_planner_for(world.config.planner)
    first = _run(work, world, "intr", p1, workers=8, stop_after_calls=7)
    assert first.status == "interrupted" and first.calls_used == p1.calls == 7
    progress = RunProgress.model_validate_json(
        (work / "intr/progress.json").read_text(encoding="utf-8")
    )
    assert progress.calls_used == 7
    assert len({p.clone_id for p in progress.pending}) == len(progress.pending)
    p2 = mock_planner_for(world.config.planner)
    with contextlib.chdir(work):
        resumed = resume_run(world, p2, Path("intr"), workers=8)
    assert p1.calls + p2.calls == ref.calls_used  # 不重问、不多问
    _same_as_reference(work, ref, resumed, "intr")


class _HaltOn(ProductMockPlanner):
    """某个分身某一年的第一份候选：服务停下（在发送之前）。"""

    def __init__(self, scripts, clone_id: str, year: int) -> None:
        super().__init__(scripts)
        self.at = (clone_id, year)

    def plan(self, call):
        p = call.payload
        if (p["clone_id"], p["year_index"]) == self.at:
            raise PlannerHalt("service_unavailable", "测试：服务暂时不可用")
        return super().plan(call)


def test_a_service_halt_stops_the_batch_and_resumes_in_parallel(sample):
    work, world, ref = sample
    halted = _run(
        work, world, "halt", _HaltOn(world.config.planner.scripts, world.order[3], 2), workers=8
    )
    assert (halted.status, halted.halt_kind) == ("halted", "service_unavailable")
    assert halted.evaluations == {} and halted.reports == {}
    progress = RunProgress.model_validate_json(
        (work / "halt/progress.json").read_text(encoding="utf-8")
    )
    assert world.order[3] in {p.clone_id for p in progress.pending}
    assert "服务暂时不可用" in (progress.pause_reason or "")
    with contextlib.chdir(work):
        resumed = resume_run(world, mock_planner_for(world.config.planner), Path("halt"), workers=8)
    _same_as_reference(work, ref, resumed, "halt")


def test_the_claude_env_lock_covers_only_the_subprocess_start(monkeypatch):
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

    from agentlayer import life_llm

    monkeypatch.setenv("SOME_HOST_SECRET_LIKE_VAR", "x")
    seen: dict[str, Any] = {}

    async def fake_connect(self) -> None:
        seen["environ"] = dict(os.environ)
        seen["locked"] = life_llm._ENV_LOCK.locked()

    monkeypatch.setattr(SubprocessCLITransport, "connect", fake_connect)
    transport = life_llm._isolated_cli_transport(
        "hi", ClaudeAgentOptions(model="claude-sonnet-5-5"), ()
    )
    asyncio.run(transport.connect())
    assert seen["locked"] is True
    assert "SOME_HOST_SECRET_LIKE_VAR" not in seen["environ"]
    assert all(k.upper() in life_llm.AGENT_SDK_ENV_ALLOWLIST for k in seen["environ"])
    # 启动之后：锁已经放开，进程环境原样恢复（别的分身可以同时启动自己的命令行）
    assert not life_llm._ENV_LOCK.locked()
    assert os.environ["SOME_HOST_SECRET_LIKE_VAR"] == "x"
