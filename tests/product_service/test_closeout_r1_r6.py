"""E3C 收尾：独立审核 R1–R6 的正式回归（反例来自 ``runs/e3c-review-20261024/test_audit_probes.py``，保留原来的正确断言）。

* R1 删除全部与在途的审阅、确认写回；
* R2 审阅与确认画像的期限与清理；
* R3 过期之后的写入与重启；
* R4 只有真的能继续时才提供恢复（真正的 ``LlmPlanner`` 接离线测试传输）；
* R5 实际 FastAPI 的 OpenAPI 与提交的文件一致；
* R6 说明按实际的开始、扣次与退回记录写，按钮与说明一致。
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from product.service import actions
from tests.life.live_support import ScriptedTransport, timeout
from tests.product_service.support import finished_run, ready_intent, start

REPO = Path(__file__).resolve().parents[2]


def _bodies(svc, principal: str) -> dict[str, int]:
    with svc.store.read() as con:
        return {
            t: con.execute(
                f"SELECT COUNT(*) FROM {t} WHERE principal_id = ? AND body IS NOT NULL",
                (principal,),
            ).fetchone()[0]
            for t in ("drafts", "reviews", "profiles")
        }


def _paused_in_flight(svc, b, monkeypatch, target: str, call):
    """在 ``actions.<target>`` 返回之前停住一个请求，期间执行“删除全部”，再放行。返回（删除的回复，原请求的回复）。"""
    entered, release = threading.Event(), threading.Event()
    original = getattr(actions, target)

    def paused(*args, **kwargs):
        value = original(*args, **kwargs)
        entered.set()
        assert release.wait(30)
        return value

    monkeypatch.setattr(actions, target, paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(call)
        assert entered.wait(30)
        deleted = b.delete("/me/data")
        release.set()
        return deleted, future.result(timeout=30)


# ---------------------------------------------------------------------- R1


def test_delete_all_during_a_review_does_not_restore_it(tmp_path, monkeypatch):
    svc = start(tmp_path)
    principal, b = svc.person()
    filled = b.fill(b.new_draft())
    draft_id = filled["draft"]["draft_id"]
    deleted, reviewed = _paused_in_flight(
        svc, b, monkeypatch, "review_draft", lambda: b.post(f"/drafts/{draft_id}/review")
    )
    assert deleted.status_code == 202
    assert reviewed.status_code in (404, 410) and reviewed.json()["code"] in (
        "deleted",
        "not_found",
    )
    svc.worker.maintain()
    assert _bodies(svc, principal) == {"drafts": 0, "reviews": 0, "profiles": 0}


def test_delete_all_during_a_confirmation_does_not_restore_the_profile(tmp_path, monkeypatch):
    svc = start(tmp_path)
    principal, b = svc.person()
    filled = b.fill(b.new_draft())
    draft_id = filled["draft"]["draft_id"]
    review = b.post(f"/drafts/{draft_id}/review").json()
    req = {
        "review_id": review["review"]["review_id"],
        "draft_id": draft_id,
        "draft_revision": review["reviewed_revision"],
        "acknowledged": review["review"]["pending_confirmations"],
    }
    deleted, confirmed = _paused_in_flight(
        svc, b, monkeypatch, "confirm_profile", lambda: b.post("/profiles/confirm", json=req)
    )
    assert deleted.status_code == 202
    assert confirmed.status_code == 410 and confirmed.json()["code"] == "deleted"
    svc.worker.maintain()
    assert _bodies(svc, principal) == {"drafts": 0, "reviews": 0, "profiles": 0}


def test_delete_all_during_intent_creation_does_not_create_it(tmp_path, monkeypatch):
    svc = start(tmp_path)
    principal, b = svc.person()
    confirmed = b.confirm(b.fill(b.new_draft()))
    deleted, created = _paused_in_flight(
        svc,
        b,
        monkeypatch,
        "confirmation_problems",
        lambda: b.post("/run-intents", json={"confirmed_id": confirmed["confirmed_id"]}),
    )
    assert deleted.status_code == 202
    assert created.status_code in (404, 410)
    assert svc.store.intents(principal) == [] and svc.store.pending_jobs() == []


# ---------------------------------------------------------------------- R2


def test_an_expired_draft_takes_its_reviews_and_unused_profiles_with_it(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    filled = b.fill(b.new_draft(), free_text="AUDIT_PRIVATE_TEXT_30_DAY_EXPIRY")
    confirmed = b.confirm(filled)
    svc.clock.advance(days=31)
    svc.worker.maintain()
    b.reopen()
    assert _bodies(svc, principal) == {"drafts": 0, "reviews": 0, "profiles": 0}
    r = b.post("/run-intents", json={"confirmed_id": confirmed["confirmed_id"]})
    assert r.status_code in (404, 410)
    assert svc.store.intents(principal) == []


def test_a_profile_prepares_runs_for_30_days_and_reconfirming_does_not_extend_it(tmp_path):
    svc = start(tmp_path, draft_retention_days=60)
    principal, b = svc.person()
    filled = b.fill(b.new_draft())
    first = b.confirm(filled)
    expires = svc.store.profile(principal, first["confirmed_id"])["expires_at"]
    svc.clock.advance(days=29)
    b.reopen()
    again = b.confirm(b.get(f"/drafts/{filled['draft']['draft_id']}").json())  # 重复确认同一份
    assert again["confirmed_id"] == first["confirmed_id"]
    assert svc.store.profile(principal, first["confirmed_id"])["expires_at"] == expires
    svc.clock.advance(days=1, seconds=1)
    r = b.post("/run-intents", json={"confirmed_id": first["confirmed_id"]})
    assert r.status_code == 410 and r.json()["code"] == "expired"
    # 再确认一次也不能复活（草稿还在保存期内）
    review = b.post(f"/drafts/{filled['draft']['draft_id']}/review").json()
    r = b.post(
        "/profiles/confirm",
        json={
            "review_id": review["review"]["review_id"],
            "draft_id": filled["draft"]["draft_id"],
            "draft_revision": review["reviewed_revision"],
            "acknowledged": review["review"]["pending_confirmations"],
        },
    )
    assert r.status_code == 410 and r.json()["code"] == "expired"


def test_an_accepted_run_keeps_its_own_snapshot_after_the_profile_expires(tmp_path):
    svc = start(tmp_path, profile_retention_days=1)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    svc.clock.advance(days=2)
    svc.worker.maintain()
    b.reopen()
    assert _bodies(svc, principal)["profiles"] == 0  # 画像过期、没有正在检查的意图引用：正文清掉
    assert b.submit(view).status_code == 202  # 运行用自己工作目录里的副本
    svc.work()
    assert b.intent(view["intent_id"])["stage"] == "finished"


def test_deleting_a_draft_takes_its_reviews_and_profiles(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    filled = b.fill(b.new_draft())
    b.confirm(filled)
    assert b.delete(f"/drafts/{filled['draft']['draft_id']}").status_code == 200
    assert _bodies(svc, principal) == {"drafts": 0, "reviews": 0, "profiles": 0}


def test_a_new_revision_voids_the_old_review(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    filled = b.fill(b.new_draft())
    b.post(f"/drafts/{filled['draft']['draft_id']}/review")
    b.fill(b.get(f"/drafts/{filled['draft']['draft_id']}").json(), age=44)
    assert _bodies(svc, principal)["reviews"] == 0


# ---------------------------------------------------------------------- R3


def test_a_submission_after_the_shown_expiry_is_refused_without_charging(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    svc.clock.advance(days=31)
    b.reopen()
    assert b.intent(view["intent_id"])["stage"] == "expired"
    sent = b.submit(view)
    assert sent.status_code == 410 and sent.json()["code"] == "expired"
    svc.worker.recover()
    svc.work()
    assert b.intent(view["intent_id"])["stage"] == "expired"
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    assert [e["kind"] for e in svc.store.quota_events(principal)] == []
    assert not (svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive").exists()


def test_a_resume_after_expiry_is_refused_and_sends_nothing(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.worker.faults = {"life:halt_at_call:3:service_unavailable"}
    svc.work()
    svc.worker.faults = None
    assert "resume" in b.intent(view["intent_id"])["allowed_actions"]
    svc.clock.advance(days=31)
    b.reopen()
    r = b.post(f"/runs/{receipt['run_id']}/resume")
    assert r.status_code == 410 and r.json()["code"] == "expired"
    svc.work()
    assert b.intent(view["intent_id"])["stage"] == "expired"
    assert [e["kind"] for e in svc.store.quota_events(principal)] == ["reserve", "consume"]


def test_a_dead_workers_running_record_expires_on_restart(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    b.submit(view)
    claimed = svc.store.take_job()  # 上一个进程领了运行任务、标为运行中，然后死掉
    assert claimed["kind"] == "run"
    svc.store.mark_started(view["intent_id"])
    svc.clock.advance(days=31)
    svc.worker.recover()
    svc.worker.recover()  # 重启两次也不重复退回、不复活
    b.reopen()
    row = svc.store.intent_row(view["intent_id"])
    assert row["stage"] == "expired"
    assert svc.store.pending_jobs() == []
    assert [e["kind"] for e in svc.store.quota_events(principal)] == ["reserve", "release"]
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}


def test_a_due_but_not_yet_cleaned_reservation_is_not_shown_as_held(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    b.submit(view)
    assert b.quota()["reserved"] == 1
    svc.clock.advance(days=31)
    b.reopen()
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}  # 只读，不写库


# ---------------------------------------------------------------------- R4


def _llm(tmp_path, transport, **over):
    return start(
        tmp_path,
        planner="llm",
        live_calls_enabled=True,
        transport=lambda _s: transport,
        workers=1,
        **over,
    )


def test_exhausted_service_attempts_offer_no_resume_loop(tmp_path):
    transport = ScriptedTransport([timeout(), timeout()])
    svc = _llm(tmp_path, transport)
    _, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.work()
    stopped = b.intent(view["intent_id"])
    assert stopped["stage"] == "needs_attention" and stopped["waiting_for"] == "maintainer"
    assert "resume" not in stopped["allowed_actions"]
    assert stopped["explanation"]["category"] == "service_attempts_exhausted"
    progress = b.get(f"/runs/{receipt['run_id']}/progress").json()
    assert progress["resumable"] is False  # 公开进度与按钮同一个结论
    assert progress["pause"] is not None and "可以恢复同一次运行" not in json.dumps(
        progress["pause"]
    )
    sent = len(transport.bodies)
    r = b.post(f"/runs/{receipt['run_id']}/resume")
    assert r.status_code == 409 and r.json()["code"] == "not_resumable"
    svc.work()
    assert len(transport.bodies) == sent == 2  # 没有重置尝试次数、没有新的请求


def test_a_real_planner_pause_before_any_attempt_is_still_resumable(tmp_path):
    transport = ScriptedTransport()
    svc = _llm(tmp_path, transport)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.worker.faults = {
        "life:halt_at_call:3:service_unavailable"
    }  # 第 3 份候选发送之前服务就不可用
    svc.work()
    svc.worker.faults = None
    paused = b.intent(view["intent_id"])
    assert paused["waiting_for"] == "user" and "resume" in paused["allowed_actions"]
    assert b.get(f"/runs/{receipt['run_id']}/progress").json()["resumable"] is True
    before = len(transport.bodies)
    assert b.post(f"/runs/{receipt['run_id']}/resume").status_code == 202
    svc.work()
    assert b.intent(view["intent_id"])["stage"] == "finished"
    ids = [
        json.loads(body["messages"][1]["content"].split("```json\n", 1)[1].rsplit("\n```", 1)[0])[
            "request_id"
        ]
        for body in transport.bodies
    ]
    assert len(ids) == len(set(ids)) and len(transport.bodies) > before  # 已有回复没有重发
    assert [e["kind"] for e in svc.store.quota_events(principal)] == ["reserve", "consume"]


def test_a_snapshot_taken_while_the_run_is_going_says_running_not_stopped(tmp_path):
    # 运行进行中的快照（监测线程写）：没有“恢复”，也不能说“停在不能由你恢复的位置、等维护者”——运行并没有停
    from tests.life.live_support import mock_text, reply

    gate, reached = threading.Event(), threading.Event()

    def held(body):
        reached.set()
        assert gate.wait(60)
        return reply(mock_text(body), body, model=body["model"])

    transport = ScriptedTransport(["mock", "mock", held])
    svc = _llm(tmp_path, transport)
    _, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    runner = threading.Thread(target=svc.work, daemon=True)
    runner.start()
    try:
        assert reached.wait(60)
        svc.worker._write_progress(view["intent_id"], receipt["run_id"], running_now=True)
        live = b.get(f"/runs/{receipt['run_id']}/progress").json()
    finally:
        gate.set()
        runner.join(120)
    assert live["stage"] == "running" and live["resumable"] is False
    assert "正在进行" in live["resume_note"] and "等维护者" not in live["resume_note"]
    assert live["pause"] is None
    done = b.get(f"/runs/{receipt['run_id']}/progress").json()
    assert done["stage"] == "completed" and done["resumable"] is False
    assert "已经结束" in done["resume_note"]


def test_budget_stops_offer_no_resume_and_progress_agrees(tmp_path):
    transport = ScriptedTransport()
    svc = _llm(tmp_path, transport, max_cost_cny="0.1")
    _, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.work()
    stopped = b.intent(view["intent_id"])
    assert stopped["explanation"]["category"] == "service_budget_blocked"
    assert "resume" not in stopped["allowed_actions"]
    assert b.get(f"/runs/{receipt['run_id']}/progress").json()["resumable"] is False


# ---------------------------------------------------------------------- R5


def test_the_committed_openapi_is_what_the_app_serves(tmp_path):
    from product.service.api import openapi_document

    svc = start(tmp_path)
    actual = openapi_document(svc.ctx)
    saved = json.loads((REPO / "contracts/product/v3/openapi.json").read_text(encoding="utf-8"))
    assert saved == actual
    version = actual["components"]["schemas"]["ProductMeta"]["properties"]["schema_version"]
    from common.product_contracts import PRODUCT_SCHEMA_VERSION, READABLE_SCHEMA_VERSIONS

    # v3.4 起同时接受 v3.3（只为读回历史对象；新对象都是当前版本，DECISIONS/0046）
    assert tuple(version.get("enum", ())) == READABLE_SCHEMA_VERSIONS
    assert READABLE_SCHEMA_VERSIONS[-1] == PRODUCT_SCHEMA_VERSION


# ---------------------------------------------------------------------- R6


def test_a_failure_after_start_never_claims_no_start_or_a_refund(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.worker.faults = {"life:halt_at_call:3:service_unavailable"}
    svc.work()
    assert b.quota()["used"] == 1
    snap = svc.ctx.run_dir(view["intent_id"]) / "ws/configs/life/synthetic_product.yaml"
    snap.write_bytes(snap.read_bytes() + b"\n# controlled audit change\n")
    svc.worker.faults = None
    assert b.post(f"/runs/{receipt['run_id']}/resume").status_code == 202
    svc.work()
    after = b.intent(view["intent_id"])
    assert b.quota() == {"total": 3, "used": 1, "reserved": 0, "available": 2}
    text = after["explanation"]["impact_zh"]
    assert "已经退回" not in text and "没有开始" not in text and "用掉 1 次机会" in text
    assert "取消" not in after["explanation"]["next_action_zh"]
    assert "cancel" not in after["allowed_actions"] and "delete" in after["allowed_actions"]
    assert after["waiting_for"] == "maintainer" and "resume" not in after["allowed_actions"]


def test_a_failure_before_start_says_refunded_and_offers_cancel(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    assert b.submit(view).status_code == 202
    pack = svc.ctx.run_dir(view["intent_id"]) / "ws" / svc.ctx.config.pack_path
    pack.write_text(pack.read_text(encoding="utf-8") + "\n# 改动\n", encoding="utf-8")
    svc.work()
    after = b.intent(view["intent_id"])
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    assert after["stage"] == "blocked"
    assert "预留的次数已经退回" in after["explanation"]["impact_zh"]
    assert "cancel" in after["allowed_actions"] and "取消" in after["explanation"]["next_action_zh"]


def test_paid_calls_turned_off_after_start_are_described_as_started(tmp_path):
    transport = ScriptedTransport()
    svc = _llm(tmp_path, transport)
    _, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.worker.faults = {"life:halt_at_call:3:service_unavailable"}
    svc.work()
    svc.worker.faults = None
    # 维护者在暂停期间关掉了付费调用：恢复被明确拒绝，不换成 mock；说明写“已经开始、已用 1 次”
    svc.ctx.config = svc.ctx.config.model_copy(update={"live_calls_enabled": False})
    before = len(transport.bodies)
    assert b.post(f"/runs/{receipt['run_id']}/resume").status_code == 202
    svc.work()
    after = b.intent(view["intent_id"])
    assert len(transport.bodies) == before
    assert after["explanation"]["category"] == "service_paid_calls_disabled"
    assert "用掉 1 次机会" in after["explanation"]["impact_zh"]
    assert "已经退回" not in after["explanation"]["impact_zh"]
    assert after["waiting_for"] == "maintainer" and "resume" not in after["allowed_actions"]


def test_finished_results_still_follow_e1(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    done = finished_run(svc, b)
    result = b.get(f"/runs/{done['run_id']}/result").json()
    assert {c["category"] for c in result["clones"]} <= {"C", "E", "M"}
