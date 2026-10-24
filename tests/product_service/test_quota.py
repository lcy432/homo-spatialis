"""次数与提交（验收 4、5）：并发不突破额度、换键仍是同一运行、丢失回复后重发不重复扣、开始前取消与启动前检查失败释放预留、
开始之后不退。"""

from __future__ import annotations

import threading

from common.product_contracts import RunSubmitRequest
from product.service import actions
from product.service.errors import ServiceFailure
from tests.product_service.support import finished_run, ready_intent, start


def test_concurrent_confirmations_of_one_intent_reserve_once(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    replies: list[tuple[str, str]] = []
    errors: list[str] = []
    gate = threading.Barrier(8)

    def click(i: int) -> None:
        req = RunSubmitRequest(
            confirm_with=view["confirm_with"], idempotency_key=f"tab-{i:04d}", accept_scope=True
        )
        gate.wait()
        try:
            reply = actions.submit(svc.ctx, principal, view["intent_id"], req)
            replies.append((reply.receipt.status, reply.receipt.run_id))
        except ServiceFailure as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=click, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(s for s, _ in replies) == ["accepted"] + ["duplicate_same_run"] * 7
    assert len({rid for _, rid in replies}) == 1  # 换了键也只有一个运行
    assert b.quota() == {"total": 3, "used": 0, "reserved": 1, "available": 2}
    assert [j["kind"] for j in svc.store.pending_jobs()] == ["run"]


def test_concurrent_intent_creation_yields_one_open_intent(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    confirmed = b.confirm(b.fill(b.new_draft()))
    got: list[str] = []
    gate = threading.Barrier(6)

    def create() -> None:
        from common.product_contracts import RunIntentCreateRequest

        gate.wait()
        view, _ = actions.create_intent(
            svc.ctx,
            principal,
            RunIntentCreateRequest(confirmed_id=confirmed["confirmed_id"]),
            generation=0,
        )
        got.append(view.intent_id)

    threads = [threading.Thread(target=create) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(got)) == 1
    assert [j["kind"] for j in svc.store.pending_jobs()] == ["preflight"]


def test_resend_after_lost_reply_and_key_reuse(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    first = b.submit(view, key="key-aaaa-0001")
    assert first.status_code == 202 and first.json()["receipt"]["status"] == "accepted"
    run_id = first.json()["receipt"]["run_id"]
    # 回复丢了，浏览器用同一个键重发：同一个运行，不再预留
    again = b.submit(view, key="key-aaaa-0001")
    assert again.status_code == 200
    assert again.json()["receipt"] == {**first.json()["receipt"], "status": "duplicate_same_run"}
    # 换一个键：仍是同一个运行
    other = b.submit(view, key="key-bbbb-0002")
    assert other.json()["receipt"]["run_id"] == run_id
    assert b.quota()["reserved"] == 1
    # 同一个键配了不同的内容：冲突
    r = b.post(
        f"/run-intents/{view['intent_id']}/submit",
        json={"confirm_with": "1" * 64, "idempotency_key": "key-aaaa-0001", "accept_scope": True},
    )
    assert r.status_code == 409 and r.json()["code"] == "idempotency_conflict"
    # 同一个键用在另一个意图上：也是冲突（键属于第一次的请求）
    svc.work()
    view2 = ready_intent(svc, b)
    r = b.submit(view2, key="key-aaaa-0001")
    assert r.status_code == 409 and r.json()["code"] == "idempotency_conflict"
    assert b.quota() == {"total": 3, "used": 1, "reserved": 0, "available": 2}


def test_receipt_run_id_is_the_one_langgraph_registers(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    assert receipt["service"] == "hosted_service"
    svc.work()
    row = svc.store.intent(principal, view["intent_id"])
    assert row["run_id"] == receipt["run_id"] and row["registered_at"] is not None
    archive = svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive" / receipt["run_id"]
    assert (archive / "manifest.json").is_file()
    # 没有第二份权威的 JSON 提交登记
    assert not list(svc.ctx.run_dir(view["intent_id"]).rglob("submissions.json"))


def test_three_runs_then_quota_is_exhausted_and_never_resets(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    for _ in range(3):
        finished_run(svc, b)
    assert b.quota() == {"total": 3, "used": 3, "reserved": 0, "available": 0}
    confirmed = b.confirm(b.fill(b.new_draft()))
    r = b.post("/run-intents", json={"confirmed_id": confirmed["confirmed_id"]})
    assert r.status_code == 409 and r.json()["code"] == "quota_exhausted"
    # 删除结果、换设备、重发链接都不重置
    for run in b.get("/runs").json()["runs"]:
        assert b.delete(f"/runs/{run['run_id']}").status_code in (200, 202)
    svc.work()
    secret = svc.store.reissue_invite(principal)
    assert svc.browser(secret).quota() == {"total": 3, "used": 3, "reserved": 0, "available": 0}
    # 维护者修正误扣：追加记录，不改历史
    svc.store.admin_adjust(principal, credit=True, note="测试：误扣一次")
    assert svc.browser(secret).quota()["available"] == 1
    kinds = [e["kind"] for e in svc.store.quota_events(principal)]
    assert kinds.count("consume") == 3 and kinds[-1] == "admin_credit"


def test_cancel_before_start_releases_the_reservation(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    assert b.submit(view).status_code == 202
    assert b.quota()["reserved"] == 1
    r = b.post(f"/run-intents/{view['intent_id']}/cancel")
    assert r.status_code == 200 and r.json()["stage"] == "cancelled"
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    svc.work()  # 排队的运行任务已经取消：不会开始
    assert not (svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive").exists()
    assert b.intent(view["intent_id"])["stage"] == "cancelled"


def test_failed_final_check_releases_and_started_runs_are_not_refunded(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    assert b.submit(view).status_code == 202
    # 快照在排队期间被改坏：最后核对不通过，不开始，预留退回
    pack = svc.ctx.run_dir(view["intent_id"]) / "ws" / svc.ctx.config.pack_path
    pack.write_text(pack.read_text(encoding="utf-8") + "\n# 改动\n", encoding="utf-8")
    svc.work()
    after = b.intent(view["intent_id"])
    assert (
        after["stage"] == "blocked"
        and after["explanation"]["category"] == "service_snapshot_broken"
    )
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    assert not (svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive").exists()
    # 已经开始的运行：删除也不退
    assert b.post(f"/run-intents/{view['intent_id']}/cancel").status_code == 200
    done = finished_run(svc, b)
    assert b.delete(f"/runs/{done['run_id']}").status_code == 202
    svc.work()
    assert b.quota() == {"total": 3, "used": 1, "reserved": 0, "available": 2}
    assert [e["kind"] for e in svc.store.quota_events(principal)].count("release") == 1


def test_one_open_intent_at_a_time_and_conflicting_conditions(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    first = b.prepare()
    again = b.post("/run-intents", json={"confirmed_id": first["confirmed"]["confirmed_id"]})
    assert again.status_code == 200 and again.json()["intent_id"] == first["intent_id"]
    other = b.confirm(b.fill(b.new_draft(), age=41))
    r = b.post("/run-intents", json={"confirmed_id": other["confirmed_id"]})
    assert r.status_code == 409 and r.json()["code"] == "intent_conflict"
    assert b.get("/session").json()["open_intent_id"] == first["intent_id"]
