"""删除、过期与清理（验收 13、14、15）。

* 删除竞争：排队中、发送之前、请求在途、服务重试之前、结果写入时——删除都优先，不再批准新的发送，已经发出的照常记账；
* 过期：注入时钟跨过 30 天；查看与轮询不续期；停机后重启先过期，再谈恢复或付费；
* 清理范围：工作目录（画像副本、编译输入、模型请求与回复、归档、结果缓存、账本）、LangGraph checkpoint 与诊断都删掉，
  只留最小的次数与费用摘要；SQLite 里的个人正文清空并处理 WAL。
"""

from __future__ import annotations

import json

from agentlayer.life_llm import TransportError
from tests.life.live_support import ScriptedTransport, mock_text, reply
from tests.product_service.support import files_under, finished_run, ready_intent, start

MARKER = "删除测试标记-9c41"


def _llm(tmp_path, transport, **over):
    return start(
        tmp_path,
        planner="llm",
        live_calls_enabled=True,
        transport=lambda _s: transport,
        workers=1,
        **over,
    )


def _gone(svc, b, view) -> None:
    """删除收尾之后：查不到内容，目录不在，库里只剩最小记录。"""
    intent = b.intent(view["intent_id"])
    assert intent["stage"] == "deleted" and intent["deletion"]["state"] == "deleted"
    assert (
        intent["preflight"] is None and intent["progress"] is None and intent["explanation"] is None
    )
    assert not svc.ctx.run_dir(view["intent_id"]).exists()
    if intent["run_id"]:
        for path in (f"/runs/{intent['run_id']}/progress", f"/runs/{intent['run_id']}/result"):
            r = b.get(path)
            assert r.status_code == 410 and r.json()["code"] == "deleted"


# ---------------------------------------------------------------------- 13. 删除竞争


def test_delete_while_queued_releases_and_never_starts(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    r = b.delete(f"/runs/{receipt['run_id']}")
    assert r.status_code == 202 and r.json()["status"] == "deleting"
    # 立即不可查看
    assert b.get(f"/runs/{receipt['run_id']}/progress").json()["code"] == "deleting"
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    svc.work()
    _gone(svc, b, view)


def test_delete_before_the_first_send_stops_without_sending(tmp_path, monkeypatch):
    transport = ScriptedTransport()
    svc = _llm(tmp_path, transport)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    original = svc.worker._on_start

    def then_delete(intent: str) -> None:
        original(intent)  # 次数已经转为已使用
        svc.store.request_delete(principal, intent)  # 用户在第一次发送之前删除

    monkeypatch.setattr(svc.worker, "_on_start", then_delete)
    svc.work()
    assert transport.bodies == []
    _gone(svc, b, view)
    assert b.quota()["used"] == 1  # 开始之后删除不退
    summary = json.loads(svc.store.intent(principal, view["intent_id"])["cost_summary"])
    assert summary["service_attempts"] == 0 and summary["estimated_cost"] == "0.000000"
    assert receipt["run_id"]


def test_delete_with_a_request_in_flight_lets_it_finish_and_bills_it(tmp_path):
    holder: dict[str, str] = {}

    def third(body):
        svc.store.request_delete(holder["principal"], holder["intent"])  # 第 3 份请求在途时用户删除
        return reply(mock_text(body), body, model=body["model"])

    transport = ScriptedTransport(["mock", "mock", third])
    svc = _llm(tmp_path, transport)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    holder.update(principal=principal, intent=view["intent_id"])
    b.submit(view)
    svc.work()
    assert len(transport.bodies) == 3  # 在途的那一份完成了；之后不再批准
    _gone(svc, b, view)
    summary = json.loads(svc.store.intent(principal, view["intent_id"])["cost_summary"])
    assert summary["service_attempts"] == 3 and float(summary["settled_cost"]) > 0


def test_delete_before_a_service_retry_blocks_the_retry(tmp_path):
    holder: dict[str, str] = {}

    def fail_and_delete(_body):
        svc.store.request_delete(holder["principal"], holder["intent"])
        raise TransportError("timeout", retryable=True)

    transport = ScriptedTransport([fail_and_delete])
    svc = _llm(tmp_path, transport)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    holder.update(principal=principal, intent=view["intent_id"])
    b.submit(view)
    svc.work()
    assert len(transport.bodies) == 1  # 服务重试没有拿到许可
    _gone(svc, b, view)
    summary = json.loads(svc.store.intent(principal, view["intent_id"])["cost_summary"])
    assert summary["uncertain_attempts"] == 1 and float(summary["uncertain_cost"]) > 0  # 不改成 0


def test_delete_racing_the_result_write_wins(tmp_path, monkeypatch):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    original = svc.store.finish_intent

    def delete_first(intent_id, **kw):
        svc.store.request_delete(principal, intent_id)  # 用户在结果写入的那一刻删除
        return original(intent_id, **kw)

    monkeypatch.setattr(svc.store, "finish_intent", delete_first)
    svc.work()
    _gone(svc, b, view)
    # 监测线程、重新排队或重启都不能把内容写回来
    svc.worker._write_progress(view["intent_id"], receipt["run_id"])
    svc.store.enqueue("run", view["intent_id"])
    svc.work()
    _gone(svc, b, view)


def test_deleting_one_run_keeps_the_other_run_and_the_drafts(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    first = finished_run(svc, b)
    second = finished_run(svc, b)
    drafts_before = {d["draft_id"] for d in b.get("/session").json()["drafts"]}
    assert b.delete(f"/runs/{first['run_id']}").status_code == 202
    svc.work()
    assert b.get(f"/runs/{second['run_id']}/result").status_code == 200
    assert {d["draft_id"] for d in b.get("/session").json()["drafts"]} == drafts_before
    assert [r["run_id"] for r in b.get("/runs").json()["runs"]] == [second["run_id"]]


# ---------------------------------------------------------------------- 15. 清理范围


def test_delete_all_data_leaves_only_minimal_records(tmp_path):
    svc = start(tmp_path)
    _, other = svc.person("另一个人")
    theirs = finished_run(svc, other)
    principal, b = svc.person()
    done = finished_run(svc, b, free_text=f"我做文员。{MARKER}")
    run_dir = svc.ctx.run_dir(done["intent_id"])
    # 删除之前：工作目录里有画像副本、编译输入、归档、结果缓存与 checkpoint
    names = set(files_under(run_dir))
    assert any(n.startswith("ws/workflow/confirmed/") for n in names)
    assert any(n.startswith("ws/compiled/") for n in names)
    assert any(n.startswith("ws/archive/") for n in names)
    assert any(n.startswith("ws/workflow/results/") for n in names)
    assert "workflow.sqlite" in names
    r = b.delete("/me/data")
    assert r.status_code == 202 and r.json()["scope"] == "all_data"
    svc.work()
    assert not run_dir.exists()
    # 别人的运行与共享资料包不受影响
    assert [p.name for p in (svc.ctx.data_root / "runs").iterdir()] == [theirs["intent_id"]]
    assert other.get(f"/runs/{theirs['run_id']}/result").status_code == 200
    assert (svc.ctx.repo / svc.ctx.config.pack_path).is_file()
    # 旧的幂等请求（同一个键、同一份内容）不能让删掉的运行复活
    original = svc.store.intent(principal, done["intent_id"])["preflight_fingerprint"]
    resend = b.post(
        f"/run-intents/{done['intent_id']}/submit",
        json={
            "confirm_with": original,
            "idempotency_key": f"key-{done['intent_id']}",
            "accept_scope": True,
        },
    )
    assert resend.status_code == 410 and resend.json()["code"] == "deleted"
    session = b.get("/session")
    assert session.status_code == 200  # 链接与会话照常（删除内容不等于注销）
    assert session.json()["drafts"] == [] and session.json()["open_intent_id"] is None
    assert session.json()["quota"] == {"total": 3, "used": 1, "reserved": 0, "available": 2}
    assert b.get("/runs").json()["runs"] == []
    with svc.store.read() as con:
        for table in ("drafts", "reviews", "profiles"):
            bodies = [
                x[0]
                for x in con.execute(
                    f"SELECT body FROM {table} WHERE principal_id = ?", (principal,)
                )
            ]
            assert bodies and all(x is None for x in bodies), table
        row = con.execute(
            "SELECT * FROM intents WHERE intent_id = ?", (done["intent_id"],)
        ).fetchone()
        assert all(
            row[c] is None for c in ("preflight_body", "progress_body", "result_body", "snapshot")
        )
        assert row["stage"] == "deleted" and row["deleted_at"] is not None
        assert (
            con.execute(
                "SELECT COUNT(*) FROM quota_events WHERE principal_id = ?", (principal,)
            ).fetchone()[0]
            == 2
        )
    # SQLite 的旧页：secure_delete 打开、删除后做了 WAL 检查点；库文件与 WAL 里都找不到原文
    svc.store.checkpoint()
    for name in ("service.sqlite", "service.sqlite-wal"):
        path = svc.ctx.data_root / name
        if path.exists():
            assert MARKER.encode("utf-8") not in path.read_bytes(), name
    diag = svc.ctx.data_root / "diagnostics" / "worker.jsonl"
    assert MARKER not in diag.read_text(encoding="utf-8")


def test_registered_paths_only(tmp_path):
    import pytest

    from workflows.service_runs import remove_tree

    svc = start(tmp_path)
    runs = svc.ctx.data_root / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    for bad in (outside, runs / ".." / "outside", runs):
        with pytest.raises(ValueError):
            remove_tree(bad, runs)
    assert (outside / "keep.txt").is_file()


# ---------------------------------------------------------------------- 14. 过期（注入时钟）


def test_results_expire_thirty_days_after_first_finish_and_viewing_does_not_extend(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    done = finished_run(svc, b)
    run_id = done["run_id"]
    assert done["expires_at"] is not None
    for _ in range(29):
        svc.clock.advance(days=1)
        b.reopen()
        assert b.get(f"/runs/{run_id}/result").status_code == 200  # 每天看一次，不续期
    assert b.intent(done["intent_id"])["expires_at"] == done["expires_at"]
    svc.clock.advance(days=1)
    b.reopen()
    r = b.get(f"/runs/{run_id}/result")  # API 访问时就按到期时间判断（清理还没跑）
    assert r.status_code == 410 and r.json()["code"] == "expired"
    assert b.intent(done["intent_id"])["stage"] == "expired"
    svc.worker.maintain()
    assert not svc.ctx.run_dir(done["intent_id"]).exists()
    row = svc.store.intent(principal, done["intent_id"])
    assert row["stage"] == "expired" and row["result_body"] is None and row["cost_summary"]
    assert b.quota()["used"] == 1


def test_drafts_and_waiting_intents_expire_after_thirty_quiet_days(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    lone = b.new_draft()
    svc.clock.advance(days=29)
    b.reopen()
    assert b.get(f"/drafts/{lone['draft']['draft_id']}").status_code == 200
    for _ in range(5):
        b.get(f"/run-intents/{view['intent_id']}")  # 轮询不续期
    svc.clock.advance(days=1, seconds=1)
    r = b.get(f"/drafts/{lone['draft']['draft_id']}")
    assert r.status_code == 410 and r.json()["code"] == "expired"
    assert b.intent(view["intent_id"])["stage"] == "expired"
    svc.worker.maintain()
    assert b.intent(view["intent_id"])["stage"] == "expired"
    assert b.get("/session").json()["open_intent_id"] is None


def test_restart_after_downtime_expires_before_resuming_or_paying(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    assert b.submit(view).status_code == 202
    assert b.quota()["reserved"] == 1
    svc.clock.advance(days=31)  # worker 停了一个多月
    svc.worker.recover()
    svc.work()
    b.reopen()
    after = b.intent(view["intent_id"])
    assert after["stage"] == "expired"
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    assert not (svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive").exists()
