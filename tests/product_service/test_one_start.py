"""一次开始（DECISIONS/0048）：设置预览 → 一次“确认并开始” → 后台检查 → 按授权自动提交 → LangGraph → E1 → 结果。

* 预览只读：不排队、不预留次数、不调用模型、不写库；同意身份不含文字（换语言不变），设置、规模、费用上限或知悉规则变了它就变。
* 一次开始之后不再有任何浏览器请求（关掉页面）也照样跑完；连点、并发、同一个键重试只有一个意图、一次运行、一次扣次；结束之后同一个键
  仍找回原来的意图，不另起运行；同一个键配不同请求是冲突。
* 检查与自动提交之间进程退出：重启后只开始一次（任务留在进行中，或任务已经结束、只剩授权）。
* 付费调用没开、同意身份不对、知悉项不全：拒绝，什么都不建；检查之后付费调用关了、次数没了、设置或知悉规则变了、检查没通过：授权
  作废、不发送、不预留，停下说明（不替用户接受）；取消、删除、过期与自动开始竞争：不开始，旧任务不复活。
* 没有授权的旧意图照旧：检查之后由用户确认开始；带授权的意图不接受第二次确认。
"""

from __future__ import annotations

import dataclasses
import threading

import pytest
import yaml

from common.product_contracts import START_SCOPE_VERSION
from product.display import catalog
from product.service import actions
from tests.life.live_support import ScriptedTransport
from tests.product_service.support import Browser, db_dump, ready_intent, start
from workflows.service_runs import RunDir
from workflows.service_worker import Worker

KEY = "start-0001"


@pytest.fixture
def publish(tmp_path, monkeypatch):
    """登记表与冻结目录换到这个测试的临时目录，冻结当前的显示资源（与 ``test_display_service`` 同样的夹具）。"""
    monkeypatch.setattr(catalog, "FROZEN", tmp_path / "display-frozen")
    monkeypatch.setattr(catalog, "REGISTRY", tmp_path / "display-registry.yaml")

    def run(pack):
        version = catalog.freeze(pack.fingerprint, pack.body.pack_id)
        catalog.register(pack.fingerprint, pack.body.pack_id, version)
        return version

    yield run
    catalog._load_frozen.cache_clear()


def _confirmed(b: Browser, **draft_kwargs) -> dict:
    return b.confirm(b.fill(b.new_draft(), **draft_kwargs))


def _preview(b: Browser, confirmed_id: str, **options):
    return b.post("/run-start-preview", json={"confirmed_id": confirmed_id, **options})


def _start_body(preview: dict, confirmed_id: str, key: str = KEY, **options) -> dict:
    return {
        "confirmed_id": confirmed_id,
        **options,
        "start": {
            "mode": "start_when_ready",
            "consent_fingerprint": preview["consent_fingerprint"],
            "acknowledged": [a["ack_id"] for a in preview["acknowledgements"]],
            "accept": True,
            "idempotency_key": key,
        },
    }


def _one_start(b: Browser, confirmed_id: str, key: str = KEY, **options):
    r = _preview(b, confirmed_id, **options)
    assert r.status_code == 200, r.text
    return b.post("/run-intents", json=_start_body(r.json(), confirmed_id, key, **options))


def _jobs(svc, intent_id):
    with svc.store.read() as con:
        return [
            (r["kind"], r["state"])
            for r in con.execute(
                "SELECT kind, state FROM jobs WHERE intent_id = ? ORDER BY job_id", (intent_id,)
            )
        ]


def _events(svc, principal):
    return [e["kind"] for e in svc.store.quota_events(principal)]


def _confirmation(svc, intent_id):
    """LangGraph 里确认节点写下的确认记录（只读）。"""
    with RunDir(svc.ctx, intent_id).runtime() as rt:
        return rt._snapshot(intent_id).values.get("confirmation")


# ---------------------------------------------------------------------- 设置预览


def test_the_preview_is_read_only_and_states_what_will_run(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _principal, b = svc.person()
    confirmed = _confirmed(b)
    before = db_dump(svc.store.path)
    r = _preview(b, confirmed["confirmed_id"], clones_per_city=2)
    assert r.status_code == 200, r.text
    p = r.json()
    assert db_dump(svc.store.path) == before  # 不排队、不预留、不写库
    assert p["settings"]["clones_per_city"] == 2 and p["settings"]["clones_total"] == 4
    assert p["planner"]["planner_kind"] == "mock" and p["start_allowed"] is True
    assert p["scope_version"] == START_SCOPE_VERSION
    assert [a["ack_id"] for a in p["acknowledgements"]] == [
        "ack:start.one_run",
        "ack:start.auto_start",
        "ack:start.scope",
    ]
    assert p["quota"] == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    en = {f["pointer"]: f for f in p["display"]["translations"]["en"]["fields"]}
    assert all(f["status"] == "provided" for f in en.values()), en  # 范围说明与知悉项都有英文
    again = _preview(b, confirmed["confirmed_id"], clones_per_city=2).json()
    assert (
        again["consent_fingerprint"] == p["consent_fingerprint"]
    )  # 同一份设置、同一个身份；文字不进身份
    other = _preview(b, confirmed["confirmed_id"], clones_per_city=1).json()
    assert other["consent_fingerprint"] != p["consent_fingerprint"]  # 规模变了，同意就不同


def test_a_paid_run_lists_the_cost_cap_and_says_when_it_cannot_start(tmp_path):
    svc = start(tmp_path, planner="llm", live_calls_enabled=False)
    _principal, b = svc.person()
    confirmed = _confirmed(b)
    p = _preview(b, confirmed["confirmed_id"]).json()
    assert p["start_allowed"] is False and p["start_refusal"] == "paid_calls_disabled"
    assert "ack:start.cost_cap" in [a["ack_id"] for a in p["acknowledgements"]]
    before = db_dump(svc.store.path)
    r = b.post("/run-intents", json=_start_body(p, confirmed["confirmed_id"]))
    assert r.status_code == 409 and r.json()["code"] == "paid_calls_disabled"
    assert db_dump(svc.store.path) == before  # 什么都没建


# ---------------------------------------------------------------------- 一次开始：正常路径


def test_one_start_then_closing_the_page_still_finishes_once(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    confirmed = _confirmed(b)
    r = _one_start(b, confirmed["confirmed_id"])
    assert r.status_code == 202, r.text
    view = r.json()
    assert view["stage"] == "checking" and view["start"]["state"] == "pending"
    assert "submit" not in view["allowed_actions"] and view["run_id"] is None
    # 关掉页面：之后没有任何浏览器请求，worker 照样检查、按授权开始、跑完
    svc.work()
    done = svc.store.intent_row(view["intent_id"])
    assert done["stage"] == "finished"
    after = b.intent(view["intent_id"])
    assert (
        after["start"]["state"] == "used" and after["confirmation_source"] == "start_authorization"
    )
    assert after["start"]["used_at"] is not None and after["result_available"]
    assert _events(svc, principal) == [
        "reserve",
        "consume",
    ]  # 检查不扣；开始时预留、第一次进入 E1 才转为已使用
    assert [k for k, _ in _jobs(svc, view["intent_id"])] == ["preflight", "run"]
    conf = _confirmation(svc, view["intent_id"])
    assert conf["source"] == "start_authorization"  # 不写成用户在检查之后又点了一次
    assert conf["authorization"]["consent_fingerprint"] == view["start"]["consent_fingerprint"]
    assert conf["authorization"]["authorized_at"] == view["start"]["authorized_at"]


def test_double_clicks_retries_and_concurrent_starts_make_one_run(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    confirmed = _confirmed(b)
    p = _preview(b, confirmed["confirmed_id"]).json()
    body = _start_body(p, confirmed["confirmed_id"])
    results: list = []

    def click(key: str) -> None:
        tab = Browser(svc)
        tab.client.cookies = b.client.cookies
        tab.csrf = b.csrf
        results.append(
            tab.post(
                "/run-intents", json={**body, "start": {**body["start"], "idempotency_key": key}}
            )
        )

    threads = [threading.Thread(target=click, args=(k,)) for k in (KEY, KEY, KEY, "start-0002")]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert sorted(r.status_code for r in results) == [200, 200, 200, 202]
    ids = {r.json()["intent_id"] for r in results}
    assert len(ids) == 1  # 连点、并发、换了键的第二个标签页：同一个意图
    intent_id = ids.pop()
    svc.work()
    assert svc.store.intent_row(intent_id)["stage"] == "finished"
    # 结束之后同一个键重试（例如回复丢了、页面重开）：找回原来的意图，不另起运行、不再扣次数
    again = b.post("/run-intents", json=body)
    assert again.status_code == 200 and again.json()["intent_id"] == intent_id
    assert again.json()["stage"] == "finished"
    svc.work()
    assert _events(svc, principal) == ["reserve", "consume"]
    assert len(svc.store.intents(principal)) == 1
    # 同一个键配不同的请求：冲突
    other = _start_body(
        _preview(b, confirmed["confirmed_id"], clones_per_city=2).json(),
        confirmed["confirmed_id"],
        clones_per_city=2,
    )
    r = b.post("/run-intents", json=other)
    assert r.status_code == 409 and r.json()["code"] == "idempotency_conflict"
    # 结束之后要再模拟：新的明确开始（新的键）
    r = b.post("/run-intents", json=_start_body(p, confirmed["confirmed_id"], key="start-0003"))
    assert r.status_code == 202 and r.json()["intent_id"] != intent_id


def test_an_authorized_intent_refuses_a_second_confirmation(tmp_path):
    svc = start(tmp_path)
    _principal, b = svc.person()
    confirmed = _confirmed(b)
    view = _one_start(b, confirmed["confirmed_id"]).json()
    svc.worker.step()  # 只做检查任务：检查通过、按授权排队
    row = svc.store.intent_row(view["intent_id"])
    assert row["stage"] == "queued"
    r = b.post(
        f"/run-intents/{view['intent_id']}/submit",
        json={"confirm_with": "0" * 64, "idempotency_key": "key-0001", "accept_scope": True},
    )
    assert r.status_code == 409 and r.json()["code"] == "start_already_authorized"


def test_after_deletion_the_authorization_is_not_shown(tmp_path):
    svc = start(tmp_path)
    _principal, b = svc.person()
    confirmed = _confirmed(b)
    view = _one_start(b, confirmed["confirmed_id"]).json()
    svc.work()
    done = b.intent(view["intent_id"])
    assert done["confirmation_source"] == "start_authorization"
    assert b.delete(f"/runs/{done['run_id']}").status_code == 202
    svc.work()
    gone = b.intent(view["intent_id"])
    assert gone["stage"] == "deleted"
    assert (
        gone["start"] is None and gone["confirmation_source"] is None
    )  # 内容清掉之后不显示，也不冒充成用户确认


# ---------------------------------------------------------------------- 检查与自动开始之间进程退出


class _Crash(BaseException):
    """模拟进程在检查完成之后、按授权开始之前死掉（不是程序错误：worker 不会把它当作内部问题处理）。"""


@pytest.mark.parametrize("job_left_active", [True, False])
def test_a_crash_between_the_check_and_the_start_starts_only_once(
    tmp_path, monkeypatch, job_left_active
):
    svc = start(tmp_path)
    principal, b = svc.person()
    confirmed = _confirmed(b)
    view = _one_start(b, confirmed["confirmed_id"]).json()

    def die(self, intent):
        raise _Crash

    monkeypatch.setattr(Worker, "_start_when_ready", die)
    if job_left_active:  # 进程死掉时任务还标着进行中
        monkeypatch.setattr(svc.store, "finish_job", lambda *a, **k: None)
    with pytest.raises(_Crash):
        svc.work()
    monkeypatch.undo()
    row = svc.store.intent_row(view["intent_id"])
    assert row["stage"] == "awaiting_confirmation" and row["run_id"] is None
    assert _events(svc, principal) == []  # 还没有预留
    restarted = Worker(svc.ctx, sleep=lambda _s: None)
    restarted.recover()
    restarted.run_pending()
    assert svc.store.intent_row(view["intent_id"])["stage"] == "finished"
    assert _events(svc, principal) == ["reserve", "consume"]
    assert [k for k, _ in _jobs(svc, view["intent_id"])].count("run") == 1
    restarted.recover()  # 再重启一次：已经用过的授权不会再开始
    assert restarted.run_pending() == 0


# ---------------------------------------------------------------------- 授权作废：停下说明，不替用户接受


def _checked(svc, b, **draft_kwargs):
    """一次开始之后只做检查（不让 worker 接着开始），返回意图编号。"""
    confirmed = _confirmed(b, **draft_kwargs)
    view = _one_start(b, confirmed["confirmed_id"]).json()
    return confirmed, view["intent_id"]


def test_paid_calls_turned_off_before_the_start_void_the_authorization(tmp_path):
    transport = ScriptedTransport()
    svc = start(
        tmp_path, planner="llm", live_calls_enabled=True, transport=lambda _s: transport, workers=1
    )
    principal, b = svc.person()
    confirmed, intent_id = _checked(svc, b)
    svc.ctx.config = svc.ctx.config.model_copy(update={"live_calls_enabled": False})
    svc.work()
    view = b.intent(intent_id)
    assert view["stage"] == "awaiting_confirmation" and view["run_id"] is None
    assert (
        view["start"]["state"] == "void" and view["start"]["void_reason"] == "paid_calls_disabled"
    )
    assert view["explanation"]["category"] == "service_start_paid_calls_disabled"
    assert view["waiting_for"] == "maintainer" and "start_again" not in view["allowed_actions"]
    assert _events(svc, principal) == [] and transport.bodies == []  # 没有预留、没有发送
    # 开启之后也不会自动开始：要用户重新确认
    svc.ctx.config = svc.ctx.config.model_copy(update={"live_calls_enabled": True})
    svc.worker.recover()
    svc.work()
    view = b.intent(intent_id)
    assert view["stage"] == "awaiting_confirmation" and "start_again" in view["allowed_actions"]
    r = _one_start(b, confirmed["confirmed_id"], key="start-0002")
    assert r.status_code == 200 and r.json()["start"]["state"] == "pending"
    svc.work()
    assert svc.store.intent_row(intent_id)["stage"] == "finished"


def test_changed_quote_voids_the_authorization_and_rechecks(tmp_path):
    transport = ScriptedTransport()
    svc = start(
        tmp_path, planner="llm", live_calls_enabled=True, transport=lambda _s: transport, workers=1
    )
    principal, b = svc.person()
    confirmed = _confirmed(b)
    view = _one_start(b, confirmed["confirmed_id"]).json()
    flash = yaml.safe_load(svc.ctx.policy.flash_config.read_text(encoding="utf-8"))
    flash["run"]["paid_budget"]["price"]["price_version"] = "test-price-v2"
    newer = tmp_path / "flash_v2.yaml"
    newer.write_text(yaml.safe_dump(flash, allow_unicode=True), encoding="utf-8")
    svc.ctx.policy = dataclasses.replace(svc.ctx.policy, flash_config=newer)  # 检查之前换了报价
    svc.work()
    after = b.intent(view["intent_id"])
    assert (
        after["stage"] == "awaiting_confirmation"
        and after["start"]["void_reason"] == "conditions_changed"
    )
    assert "test-price-v2" in after["preflight"]["planner"]["max_cost"]  # 按新报价重新检查过
    assert after["explanation"]["category"] == "service_start_conditions_changed"
    assert _events(svc, principal) == [] and transport.bodies == []
    # 看过新的说明之后重新确认并开始：新预览、新的键
    r = _one_start(b, confirmed["confirmed_id"], key="start-0002")
    assert (
        r.status_code == 200
        and r.json()["start"]["consent_fingerprint"] != view["start"]["consent_fingerprint"]
    )
    svc.work()
    assert svc.store.intent_row(view["intent_id"])["stage"] == "finished"


def test_narrowed_options_block_and_void(tmp_path):
    svc = start(tmp_path, default_clones_per_city=2)
    principal, b = svc.person()
    confirmed = _confirmed(b)
    view = _one_start(b, confirmed["confirmed_id"], clones_per_city=2).json()
    svc.ctx.policy = dataclasses.replace(svc.ctx.policy, clones_max=1)  # 维护者收窄了允许范围
    svc.work()
    after = b.intent(view["intent_id"])
    assert after["stage"] == "blocked" and after["start"]["void_reason"] == "options_not_allowed"
    assert _events(svc, principal) == []


def test_changed_acknowledgement_rules_void_the_authorization(tmp_path, monkeypatch):
    svc = start(tmp_path)
    principal, b = svc.person()
    confirmed = _confirmed(b)
    view = _one_start(b, confirmed["confirmed_id"]).json()
    monkeypatch.setattr(actions, "START_SCOPE_VERSION", "e3c-start-scope-v2")  # 说明的业务含义变了
    svc.work()
    after = b.intent(view["intent_id"])
    assert after["start"]["void_reason"] == "consent_changed" and after["run_id"] is None
    assert _events(svc, principal) == []


def test_a_wrong_consent_or_missing_acknowledgement_is_refused(tmp_path):
    svc = start(tmp_path)
    _principal, b = svc.person()
    confirmed = _confirmed(b)
    p = _preview(b, confirmed["confirmed_id"]).json()
    before = db_dump(svc.store.path)
    stale = _start_body(p, confirmed["confirmed_id"])
    stale["start"]["consent_fingerprint"] = "1" * 64
    r = b.post("/run-intents", json=stale)
    assert r.status_code == 409 and r.json()["code"] == "start_consent_stale"
    short = _start_body(p, confirmed["confirmed_id"], key="start-0002")
    short["start"]["acknowledged"] = short["start"]["acknowledged"][:-1]
    r = b.post("/run-intents", json=short)
    assert r.status_code == 422 and r.json()["code"] == "start_acknowledgement_missing"
    assert db_dump(svc.store.path) == before


def test_a_blocked_check_voids_the_authorization(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    # 收银员在合成城市 K51 没有同一分类的岗位：检查不通过（不自动换职业、不删城）
    confirmed = _confirmed(b, occupation_text="我在超市做收银", selected="occ.cashier")
    view = _one_start(b, confirmed["confirmed_id"]).json()
    svc.work()
    after = b.intent(view["intent_id"])
    assert after["stage"] == "blocked" and after["run_id"] is None
    assert after["start"]["void_reason"] == "preflight_blocked"
    assert after["explanation"]["category"] == "service_preflight_blocked"
    assert "start_again" not in after["allowed_actions"] and "cancel" in after["allowed_actions"]
    assert _events(svc, principal) == []


@pytest.mark.parametrize("race", ["cancel", "delete", "expire"])
def test_cancel_delete_and_expiry_win_over_the_start(tmp_path, race):
    svc = start(tmp_path)
    principal, b = svc.person()
    confirmed = _confirmed(b)
    view = _one_start(b, confirmed["confirmed_id"]).json()
    intent_id = view["intent_id"]
    if race == "cancel":
        assert b.post(f"/run-intents/{intent_id}/cancel").status_code == 200
    elif race == "delete":
        svc.store.request_delete(principal, intent_id)
    else:
        svc.clock.advance(days=31)
    svc.work()
    svc.worker.recover()
    svc.work()
    row = svc.store.intent_row(intent_id)
    assert row["run_id"] is None and row["stage"] in ("cancelled", "deleted", "expired")
    assert "reserve" not in _events(svc, principal)
    assert ("run", "pending") not in _jobs(svc, intent_id) and ("run", "done") not in _jobs(
        svc, intent_id
    )


# ---------------------------------------------------------------------- 旧意图照旧


def test_an_intent_without_authorization_keeps_the_old_flow(tmp_path):
    svc = start(tmp_path)
    _principal, b = svc.person()
    view = ready_intent(svc, b)
    assert view["start"] is None and "submit" in view["allowed_actions"]
    assert b.submit(view).status_code == 202
    svc.work()
    done = b.intent(view["intent_id"])
    assert done["confirmation_source"] == "user_confirm"
    assert _confirmation(svc, view["intent_id"])["source"] == "user_confirm"
