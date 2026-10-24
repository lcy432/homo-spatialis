"""阶段二验收：正式运行（验收 6、7、10、16、17、18）。

* 全规模离线闭环：HTTP → 业务函数 → LangGraph → E1 → 产品结果，E3B 六城 × 每城 4 个分身 × 3 年（离线 mock）；
* 真实模型规划器的通路（``LlmPlanner`` + 本次运行的新账本 + 发送许可），传输层是离线替身（**测试传输**，零网络、零费用）；
* 付费开关没开、缺凭证：明确报告，不换成 mock；费用上限：并行时也不超；
* 信息隔离：发给“模型”的请求里没有服务身份、秘密、内部编号或路径；结果的 C / E / M 照 E1 原样。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from common.life_contracts import canonical_fingerprint
from common.run_store import RunStore
from orchestration.life_budget import read_ledger
from tests.life.live_support import ScriptedTransport
from tests.product_service.support import E3B, finished_run, ready_intent, start

REPO = Path(__file__).resolve().parents[2]


def _archive(svc, intent_id: str, run_id: str) -> Path:
    return svc.ctx.run_dir(intent_id) / "ws" / "archive" / run_id


# ---------------------------------------------------------------------- 18. 全规模离线闭环


def test_full_scale_offline_closed_loop_on_the_e3b_pack(tmp_path):
    svc = start(tmp_path, pack_path=E3B, repeat_indexes=(0, 1, 2, 3), workers=16)
    _, b = svc.person()
    rules = b.get("/session").json()["rules"]
    assert (rules["cities"], rules["clones_per_city"], rules["years"]) == (6, 4, 3)
    view = ready_intent(svc, b)
    pre = view["preflight"]
    assert pre["status"] == "ready" and pre["clones_per_city"] == 4 and pre["years"] == 3
    assert len(pre["cities"]) == 6
    assert pre["planner"]["planner_kind"] == "mock" and pre["planner"]["call_limit"] == 216
    receipt = b.submit(view).json()["receipt"]
    svc.work()
    done = b.intent(view["intent_id"])
    assert done["stage"] == "finished" and done["result_available"]
    result = b.get(f"/runs/{receipt['run_id']}/result").json()
    assert len(result["clones"]) == 24
    assert all(len(c["years"]) == 3 for c in result["clones"])
    assert result["real_model_calls"] == 0
    assert result["preflight"]["preflight_fingerprint"] == view["confirm_with"]
    # 7. 身份一致：预检查记下的 E1 运行身份 = 归档清单；规划器身份 = 预检查 = 结果
    run_dir = _archive(svc, view["intent_id"], receipt["run_id"])
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == receipt["run_id"]
    assert canonical_fingerprint(manifest["identities"]) == pre["binding"]["identities_fingerprint"]
    assert manifest["planner"]["planner_id"] == pre["planner"]["planner_id"]
    assert result["planner"]["planner_id"] == pre["planner"]["planner_id"]
    progress = b.get(f"/runs/{receipt['run_id']}/progress").json()
    assert progress["stage"] == "completed" and progress["cost"]["kind"] == "offline_mock_zero_cost"
    assert len(progress["clones"]) == 24


# ---------------------------------------------------------------------- 真实模型通路（测试传输）


def _llm(tmp_path, transport, **over):
    return start(
        tmp_path,
        planner="llm",
        live_calls_enabled=True,
        transport=lambda _settings: transport,
        **over,
    )


def test_llm_planner_path_with_a_test_transport_keeps_budget_and_service_records(tmp_path):
    shared = REPO / "ledgers/qwen-flash-convenience/ledger.sqlite"
    shared_before = hashlib.sha256(shared.read_bytes()).hexdigest() if shared.is_file() else None
    transport = ScriptedTransport()
    svc = _llm(tmp_path, transport)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    pre = view["preflight"]
    # 用户确认的预检查就是真实规划器与上限（不是 mock 预检查）
    assert pre["planner"]["planner_kind"] == "llm" and pre["planner"]["paid"] is True
    assert "10 CNY" in pre["planner"]["max_cost"]
    receipt = b.submit(view).json()["receipt"]
    svc.work()
    done = b.intent(view["intent_id"])
    assert done["stage"] == "finished", done
    run_dir = _archive(svc, view["intent_id"], receipt["run_id"])
    calls = RunStore(run_dir).records("provider_calls")
    intents = [c for c in calls if c["event"] == "intent"]
    outcomes = [c for c in calls if c["event"] == "outcome"]
    assert len(intents) == len(outcomes) == len(transport.bodies) > 0
    # 这次运行自己的新账本（工作目录里），不是仓库里的历史账本
    ledger = svc.ctx.run_dir(view["intent_id"]) / "ws" / "budget"
    header, events = read_ledger(ledger)  # type: ignore[misc]
    spec = svc.ctx.spec
    assert header["max_requests"] == spec.max_requests == 6 * 3 * 2
    assert header["price"]["price_version"] == spec.price_version
    assert sum(e["event"] == "reserve" for e in events) == len(transport.bodies)
    if shared_before is not None:
        assert hashlib.sha256(shared.read_bytes()).hexdigest() == shared_before
    # 费用：按报价估算，三类金额分开，候选数与服务尝试分开计
    progress = b.get(f"/runs/{receipt['run_id']}/progress").json()
    cost = progress["cost"]
    assert cost["kind"] == "estimated_from_quote"
    assert cost["currency"] == "CNY" and cost["max_cost"] == "10"
    assert cost["service_attempts"] == len(transport.bodies)
    assert cost["service_attempt_limit"] == 36 and cost["call_limit"] == 18
    assert float(cost["settled_cost"]) > 0
    assert cost["uncertain_cost"] == cost["in_flight_cost"] == "0.000000"
    assert "不是供应商账单" in cost["note"]
    result = b.get(f"/runs/{receipt['run_id']}/result").json()
    assert result["real_model_calls"] == len(transport.bodies)
    assert result["planner"]["paid"] is True
    # 16. 信息隔离：请求里没有服务身份、会话与邀请秘密、CSRF、内部编号或本机路径
    secret_cookie = b.client.cookies.get("hs_session")
    text = json.dumps(transport.bodies, ensure_ascii=False)
    for forbidden in (
        principal,
        secret_cookie,
        b.csrf,
        view["intent_id"],
        done["confirmed"]["confirmed_id"],
        str(svc.ctx.data_root),
        svc.ctx.data_root.as_posix(),
        "DASHSCOPE_API_KEY",
        "hs_session",
    ):
        assert forbidden and forbidden not in text, forbidden


def test_parallel_clones_never_exceed_the_cost_cap_and_stop_at_it(tmp_path):
    transport = ScriptedTransport()
    svc = _llm(tmp_path, transport, max_cost_cny="0.1", workers=4)
    _, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.work()
    stopped = b.intent(view["intent_id"])
    assert stopped["stage"] == "needs_attention" and stopped["waiting_for"] == "maintainer"
    assert stopped["explanation"]["category"] == "service_budget_blocked"
    assert "resume" not in stopped["allowed_actions"]
    ledger = svc.ctx.run_dir(view["intent_id"]) / "ws" / "budget"
    _, events = read_ledger(ledger)  # type: ignore[misc]
    assert len(transport.bodies) == sum(e["event"] == "reserve" for e in events) > 0
    # 已占用（已核算 + 用量不确定 + 在途预留）没有超过上限；并行的分身在同一本账的写事务里预留
    cost = b.get(f"/runs/{receipt['run_id']}/progress").json()["cost"]
    committed = (
        float(cost["settled_cost"]) + float(cost["uncertain_cost"]) + float(cost["in_flight_cost"])
    )
    assert 0 < committed <= 0.1
    assert float(cost["estimated_cost"]) == committed
    # 查询与（不被允许的）恢复都不会新建账本
    before = sorted(p for p in svc.ctx.data_root.rglob("ledger.sqlite"))
    for _ in range(3):
        b.get(f"/runs/{receipt['run_id']}/progress")
        assert b.post(f"/runs/{receipt['run_id']}/resume").status_code == 409
    svc.work()
    assert sorted(p for p in svc.ctx.data_root.rglob("ledger.sqlite")) == before
    assert b.quota()["used"] == 1  # 已经开始：不退


def test_paid_switch_off_refuses_to_start_and_never_falls_back_to_mock(tmp_path):
    svc = start(tmp_path, planner="llm", live_calls_enabled=False)
    _, b = svc.person()
    view = ready_intent(svc, b)  # 填写、确认与运行前检查照常
    assert view["preflight"]["planner"]["planner_kind"] == "llm"
    r = b.submit(view)
    assert r.status_code == 409 and r.json()["code"] == "paid_calls_disabled"
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    assert b.get("/session").json()["rules"]["live_calls_enabled"] is False


def test_missing_credentials_are_reported_and_the_reservation_released(tmp_path):
    svc = start(tmp_path, planner="llm", live_calls_enabled=True)  # 没有测试传输：走真实凭证解析
    _, b = svc.person()
    view = ready_intent(svc, b)
    assert b.submit(view).status_code == 202
    svc.work()
    after = b.intent(view["intent_id"])
    assert after["stage"] == "blocked"
    assert after["explanation"]["category"] == "service_credentials_missing"
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    run_dir = svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive"
    assert not run_dir.exists() or not any(run_dir.iterdir())


# ---------------------------------------------------------------------- 17. 结果语义


def test_finished_is_not_all_c_and_partial_results_show_e1_categories(tmp_path, monkeypatch):
    from common.life_planning_contracts import MockScript
    from workflows import service_runs as runs

    original = runs.RunDir.request

    def with_script(
        self, spec
    ):  # 受控夹具：第一个分身第 1 年规划器抛异常（E1 记为程序错误，批次暂停）
        req = original(self, spec)
        clone = f"{self.ctx.person_ref(self._principal)}.k51.r0"
        return req.model_copy(
            update={"scripts": (MockScript(clone_id=clone, behavior="raise_error", years=[1]),)}
        )

    svc = start(tmp_path)
    principal, b = svc.person()
    monkeypatch.setattr(runs.RunDir, "_principal", principal, raising=False)
    monkeypatch.setattr(runs.RunDir, "request", with_script)
    done = finished_run(svc, b)
    result = b.get(f"/runs/{done['run_id']}/result").json()
    assert done["stage"] == "finished"  # 服务流程结束 ≠ 所有分身都完整
    assert result["run_stage"] == "paused"
    categories = {c["clone_ref"]: c["category"] for c in result["clones"]}
    assert "C" not in set(categories.values()) or "M" in set(categories.values())
    assert any(v == "M" for v in categories.values())


def test_service_pause_is_user_resumable_without_extra_charge(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.worker.faults = {
        "life:halt_at_call:3:service_unavailable"
    }  # 受控夹具：第 3 次规划调用时服务不可用
    svc.work()
    paused = b.intent(view["intent_id"])
    assert paused["stage"] == "needs_attention" and paused["waiting_for"] == "user"
    assert "resume" in paused["allowed_actions"]
    assert paused["explanation"]["category"] == "service_service_unavailable"
    progress = b.get(f"/runs/{receipt['run_id']}/progress").json()
    assert progress["stage"] == "halted" and progress["resumable"] is True
    svc.worker.faults = None
    r = b.post(f"/runs/{receipt['run_id']}/resume")
    assert r.status_code == 202 and r.json()["stage"] == "queued"
    again = b.post(f"/runs/{receipt['run_id']}/resume")  # 重复点：已经在排队，不再接受
    assert again.status_code == 409 and again.json()["code"] == "not_resumable"
    svc.work()
    assert b.intent(view["intent_id"])["stage"] == "finished"
    assert b.quota() == {"total": 3, "used": 1, "reserved": 0, "available": 2}
    assert [e["kind"] for e in svc.store.quota_events(principal)].count("consume") == 1
