"""E3 → E5 接口补齐第一步：页面说能做的事服务端也允许，不能做的事有准确原因；期限与确认日期可以重新读取；实际 HTTP 成功码都写进
接口文档。

* 付费开关：需要真实模型的运行在付费调用关闭时不公开 ``submit``，说明在等维护者开启；离线 mock 不受影响；直接提交照旧拒绝、不扣次数。
* 恢复排队：是否开始过看服务库的启动记录（预留转为已使用），不看阶段——恢复之后又在排队的运行不能按“开始前取消”撤回；首次排队仍可以。
* 期限：意图与运行列表给当前阶段实际适用的截止时间与它按什么算；查看不续期。确认画像的首次确认与截止时间在确认回复、本人画像列表与
  单份画像里都能读回，重复确认不续期；到期说明分对象。
* HTTP：一条完整的本地故事里实际出现的每个（方法、路径、状态码）都在 OpenAPI 里声明，响应结构对得上。

全部离线：规划器是 mock 或断网的测试传输，没有任何真实模型调用。
"""

from __future__ import annotations

from tests.product_service.support import Browser, finished_run, ready_intent, start


def _second_device(svc, b) -> Browser:
    other = Browser(svc)
    assert other.login(b.token).status_code == 200
    return other


def _events(svc, principal):
    return [e["kind"] for e in svc.store.quota_events(principal)]


# ---------------------------------------------------------------------- 付费开关与“开始”按钮


def test_paid_switch_off_hides_submit_and_says_who_it_waits_for(tmp_path):
    svc = start(tmp_path, planner="llm", live_calls_enabled=False)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    assert "submit" not in view["allowed_actions"] and "cancel" in view["allowed_actions"]
    assert view["waiting_for"] == "maintainer"
    assert view["explanation"]["category"] == "service_paid_calls_off_waiting"
    assert "离线规划器" in view["explanation"]["impact_zh"]
    # 只藏按钮不够：直接提交照旧拒绝，不预留、不排队
    r = b.submit(view)
    assert r.status_code == 409 and r.json()["code"] == "paid_calls_disabled"
    assert _events(svc, principal) == []
    assert not [j for j in svc.store.pending_jobs() if j["kind"] == "run"]
    # 维护者开启之后，同一份意图由服务重新给出动作
    svc.ctx.config = svc.ctx.config.model_copy(update={"live_calls_enabled": True})
    again = b.intent(view["intent_id"])
    assert "submit" in again["allowed_actions"] and again["waiting_for"] == "user"
    assert again["explanation"] is None


def test_mock_runs_are_not_affected_by_the_paid_switch(tmp_path):
    svc = start(tmp_path, planner="mock", live_calls_enabled=False)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    assert "submit" in view["allowed_actions"] and view["waiting_for"] == "user"
    assert b.submit(view).status_code == 202
    assert _events(svc, principal) == ["reserve"]


# ---------------------------------------------------------------------- 首次排队与恢复排队


def test_a_resumed_run_waiting_in_the_queue_cannot_be_cancelled(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.worker.faults = {"life:halt_at_call:3:service_unavailable"}
    svc.work()
    svc.worker.faults = None
    paused = b.intent(view["intent_id"])
    assert "resume" in paused["allowed_actions"]
    assert b.post(f"/runs/{receipt['run_id']}/resume").status_code == 202
    for device in (b, _second_device(svc, b)):  # 刷新或换设备，按钮都一样
        queued = device.intent(view["intent_id"])
        assert queued["stage"] == "queued" and "cancel" not in queued["allowed_actions"]
        assert "已经开始过" in queued["stage_zh"]
        assert queued["explanation"]["category"] == "service_queued_resume"
        assert "用掉 1 次机会" in queued["explanation"]["impact_zh"]
    progress_before = svc.store.intent_row(view["intent_id"])["progress_body"]
    r = b.post(f"/run-intents/{view['intent_id']}/cancel")
    assert r.status_code == 409 and r.json()["code"] == "cancel_not_allowed"
    assert "不退" in r.json()["impact_zh"]
    row = svc.store.intent_row(view["intent_id"])
    assert row["stage"] == "queued" and row["progress_body"] == progress_before
    assert _events(svc, principal) == ["reserve", "consume"]  # 没有退回
    assert b.get("/runs").json()["runs"][0]["stage_zh"] == queued["stage_zh"]
    svc.work()
    assert b.intent(view["intent_id"])["stage"] == "finished"


def test_a_first_start_queue_can_still_be_cancelled_and_releases_the_reservation(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    assert b.submit(view).status_code == 202
    queued = b.intent(view["intent_id"])
    assert queued["stage"] == "queued" and "cancel" in queued["allowed_actions"]
    assert queued["explanation"]["category"] == "service_queued_first_start"
    assert "预留了 1 次机会" in queued["explanation"]["impact_zh"]
    r = b.post(f"/run-intents/{view['intent_id']}/cancel")
    assert r.status_code == 200 and r.json()["stage"] == "cancelled"
    assert _events(svc, principal) == ["reserve", "release"]


def test_cancelling_a_finished_run_is_refused_with_the_cancel_code(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    done = finished_run(svc, b)
    r = b.post(f"/run-intents/{done['intent_id']}/cancel")
    assert r.status_code == 409 and r.json()["code"] == "cancel_not_allowed"
    assert _events(svc, principal) == ["reserve", "consume"]


# ---------------------------------------------------------------------- 期限


def test_intent_expiry_is_the_real_deadline_and_reading_does_not_extend_it(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    row = svc.store.intent_row(view["intent_id"])
    from product.service.clock import days_after

    assert view["expiry_basis"] == "intent_activity"
    assert view["expires_at"] == days_after(row["last_activity_at"], 30)
    svc.clock.advance(days=3)
    b.reopen()
    for _ in range(3):  # 反复查看、列表都不续期
        assert b.intent(view["intent_id"])["expires_at"] == view["expires_at"]
    listed = b.get("/runs").json()["runs"][0]
    assert (listed["expires_at"], listed["expiry_basis"]) == (view["expires_at"], "intent_activity")
    b.submit(view)
    svc.work()
    done = b.intent(view["intent_id"])
    row = svc.store.intent_row(view["intent_id"])
    assert (
        done["expiry_basis"] == "first_finished" and done["expires_at"] == row["result_expires_at"]
    )
    assert b.delete(f"/runs/{done['run_id']}").status_code == 202
    gone = b.get("/runs").json()["runs"]
    assert all(r["expiry_basis"] == "unavailable" and r["expires_at"] is None for r in gone)


def test_a_running_row_has_no_fixed_deadline_and_a_dead_one_does():
    from product.service.store import intent_deadline

    row = {
        "stage": "running",
        "result_expires_at": None,
        "last_activity_at": "2026-10-24T09:00:00Z",
    }
    assert intent_deadline(row, 30) == (None, "active_execution")
    # 没有执行者的“运行中”记录（重启时上一个进程留下的）按最后一次有效操作算
    assert intent_deadline(row, 30, running=True) == ("2026-11-23T09:00:00Z", "intent_activity")
    for stage in ("deleting", "deleted", "expired"):
        assert intent_deadline({**row, "stage": stage}, 30) == (None, "unavailable")


def test_confirmation_dates_are_readable_again_and_never_extended(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    filled = b.fill(b.new_draft())
    draft_id = filled["draft"]["draft_id"]
    review = b.post(f"/drafts/{draft_id}/review").json()
    body = {
        "review_id": review["review"]["review_id"],
        "draft_id": draft_id,
        "draft_revision": review["reviewed_revision"],
        "acknowledged": review["review"]["pending_confirmations"],
    }
    reply = b.post("/profiles/confirm", json=body).json()
    summary = reply["profile_summary"]
    confirmed = reply["confirmed"]
    assert summary["confirmed_id"] == confirmed["confirmed_id"]
    assert summary["content_fingerprint"] == confirmed["content_fingerprint"]
    assert (summary["draft_id"], summary["draft_revision"]) == (
        draft_id,
        review["reviewed_revision"],
    )
    from product.service.clock import days_after

    assert summary["expires_at"] == days_after(summary["first_confirmed_at"], 30)
    svc.clock.advance(days=5)
    again = b.post("/profiles/confirm", json=body).json()  # 重复确认：同一份日期
    assert again["profile_summary"] == summary
    other_device = _second_device(svc, b)
    assert other_device.get("/profiles").json()["profiles"] == [summary]
    view = other_device.get(f"/profiles/{summary['confirmed_id']}").json()
    assert view["summary"] == summary and view["confirmed"] == confirmed
    # 别人的编号与不存在的一样
    _, stranger = svc.person("另一个人")
    assert stranger.get(f"/profiles/{summary['confirmed_id']}").status_code == 404
    assert stranger.get("/profiles/confirmed-0000000000000000").status_code == 404
    assert stranger.get("/profiles").json()["profiles"] == []
    # 画像到期前准备的意图用自己的期限；画像到期之后不能新建意图，列表也不再有它
    intent = b.post("/run-intents", json={"confirmed_id": summary["confirmed_id"]}).json()
    svc.clock.advance(days=26)
    b.reopen()
    assert b.get("/profiles").json()["profiles"] == []
    r = b.get(f"/profiles/{summary['confirmed_id']}")
    assert r.status_code == 410 and "确认画像" in r.json()["message_zh"]
    assert b.intent(intent["intent_id"])["stage"] != "expired"


def test_expired_errors_say_which_kind_of_content_expired(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    draft = b.fill(b.new_draft())
    done = finished_run(svc, b)
    svc.clock.advance(days=31)
    b.reopen()
    d = b.get(f"/drafts/{draft['draft']['draft_id']}").json()
    assert d["code"] == "expired" and "草稿" in d["message_zh"]
    res = b.get(f"/runs/{done['run_id']}/result").json()
    assert res["code"] == "expired" and "结果" in res["message_zh"]
    assert "首次结束" not in d["impact_zh"]


def test_an_expired_preparation_is_described_as_a_preparation(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    svc.clock.advance(days=31)
    b.reopen()
    r = b.submit(view)
    assert r.status_code == 410 and "准备" in r.json()["message_zh"]


# ---------------------------------------------------------------------- 实际 HTTP 成功码与接口文档


ROUTES = {
    "/session": "/api/v1/session",
    "/capabilities": "/api/v1/capabilities",
    "/drafts": "/api/v1/drafts",
    "/drafts/{id}": "/api/v1/drafts/{draft_id}",
    "/drafts/{id}/review": "/api/v1/drafts/{draft_id}/review",
    "/profiles/confirm": "/api/v1/profiles/confirm",
    "/profiles": "/api/v1/profiles",
    "/profiles/{id}": "/api/v1/profiles/{confirmed_id}",
    "/run-intents": "/api/v1/run-intents",
    "/run-intents/{id}": "/api/v1/run-intents/{intent_id}",
    "/run-intents/{id}/submit": "/api/v1/run-intents/{intent_id}/submit",
    "/run-intents/{id}/cancel": "/api/v1/run-intents/{intent_id}/cancel",
    "/runs": "/api/v1/runs",
    "/runs/{id}/progress": "/api/v1/runs/{run_id}/progress",
    "/runs/{id}/result": "/api/v1/runs/{run_id}/result",
    "/runs/{id}/resume": "/api/v1/runs/{run_id}/resume",
    "/runs/{id}": "/api/v1/runs/{run_id}",
    "/me/data": "/api/v1/me/data",
    "/display": "/api/v1/display",
    "/display/geography": "/api/v1/display/geography",
}


def test_every_success_status_the_app_returns_is_declared_in_openapi(tmp_path):
    from product.service.api import openapi_document

    svc = start(tmp_path)
    seen: list[tuple[str, str, int, object]] = []

    def call(b, method, route, path, json=None):
        r = (
            getattr(b, method)(path, json=json)
            if method in ("post", "put")
            else getattr(b, method)(path)
        )
        seen.append(
            (method, ROUTES[route], r.status_code, None if r.status_code == 204 else r.json())
        )
        assert r.status_code < 400, (method, path, r.status_code, r.text)
        return r

    _, b = svc.person()
    call(b, "get", "/session", "/session")
    call(b, "get", "/capabilities", "/capabilities")
    draft = call(b, "post", "/drafts", "/drafts").json()
    did = draft["draft"]["draft_id"]
    call(b, "get", "/drafts/{id}", f"/drafts/{did}")
    filled = b.fill(draft)
    review = call(b, "post", "/drafts/{id}/review", f"/drafts/{did}/review").json()
    body = {
        "review_id": review["review"]["review_id"],
        "draft_id": did,
        "draft_revision": review["reviewed_revision"],
        "acknowledged": review["review"]["pending_confirmations"],
    }
    conf = call(b, "post", "/profiles/confirm", "/profiles/confirm", body).json()
    cid = conf["confirmed"]["confirmed_id"]
    call(b, "get", "/profiles", "/profiles")
    call(b, "get", "/profiles/{id}", f"/profiles/{cid}")
    intent = call(b, "post", "/run-intents", "/run-intents", {"confirmed_id": cid}).json()
    call(b, "post", "/run-intents", "/run-intents", {"confirmed_id": cid})  # 返回已有：200
    svc.work()
    view = call(b, "get", "/run-intents/{id}", f"/run-intents/{intent['intent_id']}").json()
    call(b, "get", "/display", f"/display?kind=intent&id={intent['intent_id']}&locale=en")
    call(b, "get", "/display", "/display?kind=capabilities")
    call(
        b,
        "get",
        "/display/geography",
        f"/display/geography?pack_fingerprint={svc.ctx.pack.fingerprint}",
    )
    sub = {
        "confirm_with": view["confirm_with"],
        "idempotency_key": "key-openapi-0001",
        "accept_scope": True,
    }
    call(b, "post", "/run-intents/{id}/submit", f"/run-intents/{intent['intent_id']}/submit", sub)
    call(b, "post", "/run-intents/{id}/submit", f"/run-intents/{intent['intent_id']}/submit", sub)
    svc.worker.faults = {"life:halt_at_call:3:service_unavailable"}
    svc.work()
    svc.worker.faults = None
    run_id = view["run_id"] or b.intent(intent["intent_id"])["run_id"]
    call(b, "post", "/runs/{id}/resume", f"/runs/{run_id}/resume")
    svc.work()
    call(b, "get", "/runs", "/runs")
    call(b, "get", "/runs/{id}/progress", f"/runs/{run_id}/progress")
    call(b, "get", "/runs/{id}/result", f"/runs/{run_id}/result")
    call(b, "delete", "/runs/{id}", f"/runs/{run_id}")
    svc.work()
    call(b, "delete", "/runs/{id}", f"/runs/{run_id}")  # 已处理：200
    other = ready_intent(svc, b)
    call(b, "post", "/run-intents/{id}/cancel", f"/run-intents/{other['intent_id']}/cancel")
    call(
        b,
        "put",
        "/drafts/{id}",
        f"/drafts/{did}",
        {"expected_revision": filled["draft"]["revision"], "draft": filled["draft"]},
    )
    call(b, "delete", "/drafts/{id}", f"/drafts/{did}")
    call(b, "delete", "/me/data", "/me/data")
    r = b.delete("/session")
    seen.append(("delete", ROUTES["/session"], r.status_code, None))
    assert r.status_code == 204 and r.content == b""

    doc = openapi_document(svc.ctx)
    statuses = {(m, p, s) for m, p, s, _ in seen}
    assert ("post", ROUTES["/run-intents"], 200) in statuses and (
        "post",
        ROUTES["/run-intents"],
        202,
    ) in statuses
    assert ("post", ROUTES["/run-intents/{id}/submit"], 200) in statuses
    assert ("delete", ROUTES["/runs/{id}"], 200) in statuses and (
        "delete",
        ROUTES["/runs/{id}"],
        202,
    ) in statuses
    for method, path, status, payload in seen:
        declared = doc["paths"][path][method]["responses"]
        assert str(status) in declared, (method, path, status, sorted(declared))
        content = declared[str(status)].get("content")
        if status == 204:
            assert not content
            continue
        ref = content["application/json"]["schema"]["$ref"].rsplit("/", 1)[1]
        from common.product_contracts import PRODUCT_MESSAGES

        PRODUCT_MESSAGES[ref].model_validate(payload)  # 实际的回复就是声明的那种结构
