"""重启恢复与 worker 独占（验收 8、9、12）。崩溃用子进程里的 ``os._exit`` 模拟（不做任何收尾，和真的崩溃一样）。

* 提交已登记、流程还没记下 → 找回原运行；
* 刚转为已使用、E1 刚开始 → E1 原有的恢复核验；
* 回复已经落盘 → 复用，不重发；
* E1 已完成、结果还没投影 → 重新投影，不再模拟；
* 发送状态不确定 → 保留费用、不自动重发、不给误导的“恢复”按钮；
* 第二个 worker 拒绝启动；接管时先核对归档，被改过就停下等维护者。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from common.run_store import RunStore
from product.service.clock import system_clock
from tests.product_service.support import start
from workflows.service_worker import Worker, WorkerLockedError

REPO = Path(__file__).resolve().parents[2]
HELPER = Path(__file__).with_name("proc_worker.py")


def _proc(svc, *args: str, fault: str | None = None, exit_on_send: int | None = None):
    path = svc.ctx.data_root / "test_config.json"
    path.write_text(svc.ctx.config.model_dump_json(), encoding="utf-8")
    env = {
        k: v for k, v in os.environ.items() if k not in ("HS_WF_E3_FAULT", "HS_TEST_EXIT_ON_SEND")
    }
    env["PYTHONIOENCODING"] = "utf-8"
    if fault:
        env["HS_WF_E3_FAULT"] = fault
    if exit_on_send:
        env["HS_TEST_EXIT_ON_SEND"] = str(exit_on_send)
    return subprocess.run(
        [sys.executable, str(HELPER), str(path), *args],
        env=env,
        cwd=REPO,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )


def _queued(tmp_path, **over):
    svc = start(tmp_path, clock=system_clock, **over)
    principal, b = svc.person()
    intent = b.prepare()
    assert _proc(svc).returncode == 0  # 排队检查在子进程 worker 里做
    view = b.intent(intent["intent_id"])
    assert view["stage"] == "awaiting_confirmation", view
    receipt = b.submit(view).json()["receipt"]
    return svc, principal, b, view, receipt


def _archives(svc, intent_id: str) -> list[Path]:
    root = svc.ctx.run_dir(intent_id) / "ws" / "archive"
    return sorted(root.iterdir()) if root.exists() else []


def _consumes(svc, principal: str) -> int:
    return [e["kind"] for e in svc.store.quota_events(principal)].count("consume")


def test_crash_after_registration_finds_the_original_run(tmp_path):
    svc, principal, b, view, receipt = _queued(tmp_path)
    crashed = _proc(svc, fault="submit:after_register")
    assert crashed.returncode == 51
    row = svc.store.intent(principal, view["intent_id"])
    assert row["registered_at"] is not None and row["stage"] == "running"
    assert _proc(svc).returncode == 0
    done = b.intent(view["intent_id"])
    assert done["stage"] == "finished" and done["run_id"] == receipt["run_id"]
    assert [p.name for p in _archives(svc, view["intent_id"])] == [receipt["run_id"]]
    assert _consumes(svc, principal) == 1
    assert b.quota() == {"total": 3, "used": 1, "reserved": 0, "available": 2}


def test_crash_right_after_start_resumes_without_a_second_charge(tmp_path):
    svc, principal, b, view, _receipt = _queued(tmp_path)
    assert _proc(svc, fault="life:crash_at_call:1").returncode == 52
    assert _consumes(svc, principal) == 1  # 已经转为已使用
    assert _proc(svc).returncode == 0
    assert b.intent(view["intent_id"])["stage"] == "finished"
    assert _consumes(svc, principal) == 1
    assert len(_archives(svc, view["intent_id"])) == 1


def test_replies_on_disk_are_reused_not_resent(tmp_path):
    # 一个分身线程：崩溃前恰好有 3 份回复落盘（并行时第几份在途取决于调度）
    svc, principal, b, view, _receipt = _queued(
        tmp_path, planner="llm", live_calls_enabled=True, workers=1
    )
    assert _proc(svc, fault="life:crash_at_call:4").returncode == 52
    sends = svc.ctx.data_root / "test_transport_sends.jsonl"
    before = [json.loads(x) for x in sends.read_text(encoding="utf-8").splitlines()]
    assert len(before) == 3
    assert _proc(svc).returncode == 0
    done = b.intent(view["intent_id"])
    assert done["stage"] == "finished", done
    rows = [json.loads(x) for x in sends.read_text(encoding="utf-8").splitlines()]
    ids = [r["request_id"] for r in rows]
    assert len(ids) == len(set(ids))  # 没有一份请求被发了两次
    assert rows[:3] == before
    run_dir = _archives(svc, view["intent_id"])[0]
    intents = [c for c in RunStore(run_dir).records("provider_calls") if c["event"] == "intent"]
    assert len(intents) == len(rows)
    assert _consumes(svc, principal) == 1


def test_e1_completed_but_not_projected_is_projected_without_rerunning(tmp_path):
    svc, principal, b, view, _receipt = _queued(tmp_path)
    assert _proc(svc, fault="life:after_complete,count_calls").returncode == 53
    log = svc.ctx.run_dir(view["intent_id"]) / "ws" / "workflow" / "planner_invocations.jsonl"
    calls = log.read_text(encoding="utf-8").splitlines()
    assert _proc(svc, fault="count_calls").returncode == 0
    assert log.read_text(encoding="utf-8").splitlines() == calls  # 没有再请求规划
    done = b.intent(view["intent_id"])
    assert done["stage"] == "finished" and done["result_available"]
    assert _consumes(svc, principal) == 1


def test_uncertain_send_keeps_its_cost_and_waits_for_the_maintainer(tmp_path):
    svc, principal, b, view, receipt = _queued(tmp_path, planner="llm", live_calls_enabled=True)
    assert _proc(svc, exit_on_send=2).returncode == 54  # 第 2 次请求已经发出、结果没有落盘
    assert _proc(svc).returncode == 0
    stopped = b.intent(view["intent_id"])
    assert stopped["stage"] == "needs_attention" and stopped["waiting_for"] == "maintainer"
    assert stopped["explanation"]["category"] == "service_uncertain_send"
    assert "resume" not in stopped["allowed_actions"]  # 不给误导的重试按钮
    sends = (svc.ctx.data_root / "test_transport_sends.jsonl").read_text(encoding="utf-8")
    assert len(sends.splitlines()) == 2  # 没有自动重发
    cost = b.get(f"/runs/{receipt['run_id']}/progress").json()["cost"]
    assert float(cost["in_flight_cost"]) > 0 or float(cost["uncertain_cost"]) > 0
    r = b.post(f"/runs/{receipt['run_id']}/resume")
    assert r.status_code == 409 and r.json()["code"] == "not_resumable"
    # 删除之后，不确定的金额照原样留在最小费用摘要里（不改成 0）
    assert b.delete(f"/runs/{receipt['run_id']}").status_code == 202
    assert _proc(svc).returncode == 0
    summary = json.loads(svc.store.intent(principal, view["intent_id"])["cost_summary"])
    assert float(summary["in_flight_cost"]) + float(summary["uncertain_cost"]) > 0
    assert summary["service_attempts"] == 2


def test_a_second_worker_refuses_to_start(tmp_path):
    svc = start(tmp_path, clock=system_clock)
    with Worker(svc.ctx):
        out = _proc(svc)
        assert out.returncode == 3 and "locked" in out.stdout
        other = Worker(svc.ctx)
        try:
            other.acquire()
            raise AssertionError("第二个 worker 不应该拿到锁")
        except WorkerLockedError:
            pass
    # 前一个释放之后可以启动
    assert _proc(svc).returncode == 0


def test_takeover_checks_the_archive_and_stops_when_it_was_changed(tmp_path):
    svc, principal, b, view, _receipt = _queued(tmp_path)
    assert _proc(svc, fault="life:crash_at_call:3").returncode == 52
    run_dir = _archives(svc, view["intent_id"])[0]
    store = RunStore(run_dir)
    rows = store.records("calls")
    assert rows
    rows[0]["response_text"] = rows[0]["response_text"] + " "
    store.replace("calls", rows)
    assert _proc(svc).returncode == 0
    stopped = b.intent(view["intent_id"])
    assert stopped["stage"] == "needs_attention" and stopped["waiting_for"] == "maintainer"
    assert stopped["explanation"]["category"] == "service_archive_conflict"
    assert len(_archives(svc, view["intent_id"])) == 1  # 没有新建运行目录
    assert _consumes(svc, principal) == 1
