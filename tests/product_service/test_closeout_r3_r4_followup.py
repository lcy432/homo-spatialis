"""E3C 收尾独立复核的两处遗留（``runs/e3c-closeout-review-20261024``）：反例改成正式回归，保留探针原来的正确断言。

* R3：过期的准备即使已经在排队，也不能被 worker 启动、扣次数或发送——拒绝请求时的过期处理真正提交，领取任务、开始执行与首次
  启动 E1 三个边界都在数据库事务里核对期限，不靠定时维护刚好先跑；没到期的照常执行。
* R4：还没有发送历史的候选，恢复判断按 E1 恢复之后真正要发的那份请求估计输入（同一份构造、同一条估计、同一条预留规则），不当作
  0：钱不够时不给“恢复”、强制恢复被拒且不发请求，钱够时能继续；已落盘的回复照样复用。

全部离线：规划器是 mock 或断网的测试传输（``ScriptedTransport``），没有任何真实模型调用。
"""

from __future__ import annotations

import json

from orchestration.life_budget import read_ledger
from orchestration.life_runner import pending_input_checks, preflight_from_snapshot
from tests.life.live_support import ScriptedTransport
from tests.product_service.support import ready_intent, start
from workflows import service_runs


def _llm(tmp_path, transport, **over):
    return start(
        tmp_path,
        planner="llm",
        live_calls_enabled=True,
        transport=lambda _s: transport,
        workers=1,
        **over,
    )


def _events(svc, principal):
    return [e["kind"] for e in svc.store.quota_events(principal)]


def _jobs(svc, intent_id):
    with svc.store.read() as con:
        return [
            (r["kind"], r["state"])
            for r in con.execute(
                "SELECT kind, state FROM jobs WHERE intent_id = ? ORDER BY job_id", (intent_id,)
            )
        ]


def _queued(tmp_path, transport=None):
    svc = _llm(tmp_path, transport or ScriptedTransport())
    principal, b = svc.person()
    view = ready_intent(svc, b)
    assert b.submit(view).status_code == 202
    assert svc.store.intent_row(view["intent_id"])["stage"] == "queued"
    return svc, principal, b, view


# ---------------------------------------------------------------------- R3


def test_a_refused_submission_commits_the_expiry_and_the_worker_never_starts_it(tmp_path):
    # 复核的反例：期限前一秒做过一次维护，期限刚过、下一次维护还没到；页面显示过期，重复提交 410
    transport = ScriptedTransport()
    svc, principal, b, view = _queued(tmp_path, transport)
    svc.clock.advance(days=30, seconds=-1)
    svc.worker.maintain()
    svc.clock.advance(seconds=2)
    b.reopen()
    assert b.intent(view["intent_id"])["stage"] == "expired"
    assert b.submit(view).status_code == 410
    # 410 之后，库里已经真的是过期：预留退回一次，没有待执行的任务
    row = svc.store.intent_row(view["intent_id"])
    assert row["stage"] == "expired" and row["stop_requested"] == 1
    assert _events(svc, principal) == ["reserve", "release"]
    assert _jobs(svc, view["intent_id"]) == [("preflight", "done"), ("run", "cancelled")]
    assert b.submit(view).status_code == 410  # 再点一次也不多退、不多扣
    assert _events(svc, principal) == ["reserve", "release"]
    assert svc.work() == 0  # worker 没有任务可领
    assert b.intent(view["intent_id"])["stage"] == "expired"
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    assert transport.bodies == []  # 没有发送
    assert not (svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive").exists()  # 没有进入 E1


def test_the_worker_does_not_claim_a_queued_run_after_its_deadline(tmp_path):
    # 没有任何人访问、维护也还没跑：worker 领取时就按期限处理
    transport = ScriptedTransport()
    svc, principal, _, view = _queued(tmp_path, transport)
    svc.clock.advance(days=30, seconds=1)
    assert svc.store.take_job() is None
    row = svc.store.intent_row(view["intent_id"])
    assert row["stage"] == "expired"
    assert _events(svc, principal) == ["reserve", "release"]
    assert _jobs(svc, view["intent_id"]) == [("preflight", "done"), ("run", "cancelled")]
    assert svc.work() == 0
    assert transport.bodies == [] and _events(svc, principal) == ["reserve", "release"]
    assert not (svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive").exists()


def test_the_start_refuses_a_run_whose_deadline_passed_after_it_was_claimed(tmp_path):
    transport = ScriptedTransport()
    svc, principal, _, view = _queued(tmp_path, transport)
    job = svc.store.take_job()  # 期限之前领取
    assert job is not None and job["kind"] == "run"
    svc.clock.advance(days=30, seconds=1)  # 领取之后、开始执行之前过了期限
    svc.worker._run(view["intent_id"], "run")
    svc.store.finish_job(job["job_id"])
    assert svc.store.intent_row(view["intent_id"])["stage"] == "expired"
    assert _events(svc, principal) == ["reserve", "release"]
    assert transport.bodies == []
    assert not (svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive").exists()


def test_the_first_start_refuses_when_the_deadline_passes_just_before_e1(tmp_path, monkeypatch):
    # 已经标为运行中、LangGraph 也走到了启动 E1 的最后一步，这时过了期限：不扣次数、不进入 E1、不发送
    transport = ScriptedTransport()
    svc, principal, b, view = _queued(tmp_path, transport)
    original = svc.store.consume

    def late(intent_id, note=""):
        svc.clock.advance(days=30, seconds=1)
        return original(intent_id, note)

    monkeypatch.setattr(svc.store, "consume", late)
    svc.work()
    row = svc.store.intent_row(view["intent_id"])
    assert row["stage"] == "expired"
    assert _events(svc, principal) == ["reserve", "release"]  # 没有 consume，预留只退一次
    assert transport.bodies == []
    assert not (svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive").exists()
    b.reopen()  # 会话固定 7 天：过了 30 天重新打开链接
    assert b.submit(view).status_code == 410


def test_a_run_claimed_just_before_its_deadline_still_runs_normally(tmp_path):
    transport = ScriptedTransport()
    svc, principal, b, view = _queued(tmp_path, transport)
    svc.clock.advance(days=30, seconds=-1)
    svc.work()
    b.reopen()
    done = b.intent(view["intent_id"])
    assert done["stage"] == "finished" and transport.bodies
    assert _events(svc, principal) == ["reserve", "consume"]
    # 重复提交还是原运行，不多扣
    again = b.submit(view)
    assert again.status_code == 200 and again.json()["receipt"]["status"] == "duplicate_same_run"
    assert _events(svc, principal) == ["reserve", "consume"]


def test_cancelling_after_the_deadline_also_keeps_the_expiry(tmp_path):
    svc, principal, b, view = _queued(tmp_path)
    svc.clock.advance(days=30, seconds=1)
    b.reopen()
    r = b.post(f"/run-intents/{view['intent_id']}/cancel")
    assert r.status_code == 410 and r.json()["code"] == "expired"
    assert svc.store.intent_row(view["intent_id"])["stage"] == "expired"
    assert _events(svc, principal) == ["reserve", "release"]
    assert _jobs(svc, view["intent_id"]) == [("preflight", "done"), ("run", "cancelled")]


# ---------------------------------------------------------------------- R4


def _paused_before_a_fresh_send(tmp_path, transport, call=4, **over):
    """前几份回复已经保存，在第 ``call`` 次规划调用（一份新候选的第一次服务尝试）之前服务不可用而停下。"""
    svc = _llm(tmp_path, transport, **over)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.worker.faults = {f"life:halt_at_call:{call}:service_unavailable"}
    svc.work()
    svc.worker.faults = None
    return svc, principal, b, view, receipt


def _archive(svc, view, receipt):
    return svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive" / receipt["run_id"]


def _reserved_estimates(svc, view):
    found = read_ledger(svc.ctx.run_dir(view["intent_id"]) / "ws" / "budget")
    assert found is not None
    return {
        e["request_id"]: e["input_tokens_estimate"] for e in found[1] if e["event"] == "reserve"
    }


def test_no_resume_when_the_next_fresh_request_does_not_fit_the_budget(tmp_path):
    # 复核的反例：预算 0.08 元，前三份回复已保存，第 4 份规划的第一次服务尝试之前暂停
    transport = ScriptedTransport()
    svc, principal, b, view, receipt = _paused_before_a_fresh_send(
        tmp_path, transport, max_cost_cny="0.08"
    )
    paused = b.intent(view["intent_id"])
    assert len(transport.bodies) == 3
    assert "resume" not in paused["allowed_actions"] and paused["waiting_for"] == "maintainer"
    assert paused["explanation"]["category"] == "service_budget_blocked"
    progress = b.get(f"/runs/{receipt['run_id']}/progress").json()
    assert progress["resumable"] is False
    r = b.post(f"/runs/{receipt['run_id']}/resume")
    assert r.status_code == 409 and r.json()["code"] == "not_resumable"
    svc.work()
    assert len(transport.bodies) == 3  # 没有新的请求
    assert _events(svc, principal) == ["reserve", "consume"]  # 不多扣
    # 判断用的是那份请求真正的输入估计：只有输出的预留放得下，加上输入就放不下
    checks = pending_input_checks(
        preflight_from_snapshot(_archive(svc, view, receipt)), _archive(svc, view, receipt)
    )
    assert len(checks) == 1 and next(iter(checks.values())).estimate > 0


def test_resume_continues_when_the_fresh_request_fits_and_prices_it_like_e1(tmp_path):
    transport = ScriptedTransport()
    svc, principal, b, view, receipt = _paused_before_a_fresh_send(tmp_path, transport)
    paused = b.intent(view["intent_id"])
    assert "resume" in paused["allowed_actions"] and paused["waiting_for"] == "user"
    assert b.get(f"/runs/{receipt['run_id']}/progress").json()["resumable"] is True
    run_dir = _archive(svc, view, receipt)
    checks = pending_input_checks(preflight_from_snapshot(run_dir), run_dir)
    assert len(checks) == 1
    (request_id, check), *_ = checks.items()
    before = len(transport.bodies)
    assert b.post(f"/runs/{receipt['run_id']}/resume").status_code == 202
    svc.work()
    assert b.intent(view["intent_id"])["stage"] == "finished"
    # 恢复之后 E1 真正为这份请求预留时用的输入估计，就是恢复判断重建出来的那一个
    assert _reserved_estimates(svc, view)[request_id] == check.estimate
    ids = [_request_id(body) for body in transport.bodies]
    assert len(ids) == len(set(ids)) and len(ids) > before  # 已经保存的回复没有重发
    assert _events(svc, principal) == ["reserve", "consume"]


def test_a_fresh_revision_request_is_priced_with_its_revision(tmp_path):
    # 第 1 份候选的回复不是计划（格式问题）→ 第 2 份是带修订的请求；在它第一次发送之前暂停
    transport = ScriptedTransport(["这不是一个计划"])
    svc, _, b, view, receipt = _paused_before_a_fresh_send(tmp_path, transport, call=2)
    run_dir = _archive(svc, view, receipt)
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    assert [p["attempts_used"] for p in progress["pending"]] == [1]
    checks = pending_input_checks(preflight_from_snapshot(run_dir), run_dir)
    (request_id, check), *_ = checks.items()
    assert request_id.endswith("/a2")
    assert b.post(f"/runs/{receipt['run_id']}/resume").status_code == 202
    svc.work()
    assert _reserved_estimates(svc, view)[request_id] == check.estimate
    assert check.estimate > _reserved_estimates(svc, view)[request_id[:-1] + "1"]  # 修订更长


def test_an_estimate_that_cannot_be_rebuilt_falls_back_to_the_input_cap(tmp_path, monkeypatch):
    # 重建出问题（不是归档冲突）时按单次输入上限估计：宁可不给“恢复”，也不放行一个马上会停下的
    def broken(*_a, **_k):
        raise ValueError("测试：重建失败")

    monkeypatch.setattr(service_runs, "pending_input_checks", broken)
    transport = ScriptedTransport()
    _, _, b, view, _ = _paused_before_a_fresh_send(tmp_path, transport, max_cost_cny="0.15")
    paused = b.intent(view["intent_id"])
    assert "resume" not in paused["allowed_actions"]
    assert paused["explanation"]["category"] == "service_budget_blocked"
    assert len(transport.bodies) == 3


def _request_id(body):
    user = body["messages"][1]["content"]
    return json.loads(user.split("```json\n", 1)[1].rsplit("\n```", 1)[0])["request_id"]
