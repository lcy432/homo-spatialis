"""阶段一验收：身份隔离、会话与 Cookie、修改保护、草稿冲突、不可伪造的确认（验收 1、2、3、6、11、16 的 HTTP 部分）。"""

from __future__ import annotations

import pytest

from product.service.actions import ServiceContext
from product.service.api import create_app
from product.service.clock import FakeClock
from tests.product_service.support import (
    API,
    E3B,
    ORIGIN,
    Browser,
    Service,
    config,
    db_dump,
    files_under,
    finished_run,
    ready_intent,
    start,
)

# ---------------------------------------------------------------------- 1. 身份隔离与链接


def test_two_people_never_see_each_others_drafts_profiles_or_runs(tmp_path):
    svc = start(tmp_path)
    _, alice = svc.person("甲")
    _, bob = svc.person("乙")
    view = alice.fill(alice.new_draft())
    draft_id = view["draft"]["draft_id"]
    confirmed = alice.confirm(view)
    intent = ready_intent(svc, alice)
    # 乙拿着甲的编号：一律“没有找到”，与不存在的编号同一个回答
    for r in (
        bob.get(f"/drafts/{draft_id}"),
        bob.put(f"/drafts/{draft_id}", json={"expected_revision": 2, "draft": view["draft"]}),
        bob.delete(f"/drafts/{draft_id}"),
        bob.post(f"/drafts/{draft_id}/review"),
        bob.post("/run-intents", json={"confirmed_id": confirmed["confirmed_id"]}),
        bob.get(f"/run-intents/{intent['intent_id']}"),
        bob.post(f"/run-intents/{intent['intent_id']}/cancel"),
        bob.submit(intent, key="bob-key-0001"),
        bob.get("/drafts/draft-0000000000000000"),
    ):
        assert r.status_code == 404, r.text
        assert r.json()["code"] == "not_found"
    assert bob.get("/runs").json()["runs"] == []
    # 甲照常
    assert alice.intent(intent["intent_id"])["stage"] == "awaiting_confirmation"


def test_run_ids_are_not_credentials(tmp_path):
    svc = start(tmp_path)
    _, alice = svc.person("甲")
    _, bob = svc.person("乙")
    done = finished_run(svc, alice)
    run_id = done["run_id"]
    for r in (
        bob.get(f"/runs/{run_id}/progress"),
        bob.get(f"/runs/{run_id}/result"),
        bob.post(f"/runs/{run_id}/resume"),
        bob.delete(f"/runs/{run_id}"),
    ):
        assert r.status_code == 404 and r.json()["code"] == "not_found"
    assert alice.get(f"/runs/{run_id}/result").status_code == 200


def test_reissue_keeps_identity_and_quota_but_kills_old_link_and_sessions(tmp_path):
    svc = start(tmp_path)
    principal, phone = svc.person()
    laptop = svc.browser()
    old_secret = svc.store.reissue_invite(principal)  # 先重发一次，拿到一份已知的秘密
    assert laptop.login(old_secret).status_code == 200
    done = finished_run(svc, laptop)
    before = laptop.quota()
    assert before["used"] == 1
    new_secret = svc.store.reissue_invite(principal)
    # 旧会话（两台设备）与旧链接都失效
    assert laptop.get("/session").status_code == 401
    assert phone.get("/session").status_code == 401
    assert svc.browser().login(old_secret).status_code == 401
    # 新链接进入的是同一个空间：次数与运行都在
    again = svc.browser(new_secret)
    assert again.quota() == before
    assert [r["run_id"] for r in again.get("/runs").json()["runs"]] == [done["run_id"]]


def test_revoked_link_and_sessions_stop_working(tmp_path):
    svc = start(tmp_path)
    principal, secret = svc.store.create_principal("撤销")
    b = svc.browser(secret)
    svc.store.revoke_principal(principal)
    assert b.get("/session").status_code == 401
    assert svc.browser().login(secret).status_code == 401


def test_session_lasts_seven_days_fixed_and_queries_do_not_extend_it(tmp_path):
    svc = start(tmp_path)
    _, secret = svc.store.create_principal("会话")
    b = svc.browser(secret)
    expires = b.get("/session").json()["session_expires_at"]
    for _ in range(3):
        svc.clock.advance(days=2)
        assert b.get("/session").json()["session_expires_at"] == expires  # 查询不续期
    svc.clock.advance(days=1, seconds=1)
    r = b.get("/session")
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"
    # 会话到期后重新打开原链接
    assert svc.browser(secret).get("/session").status_code == 200


def test_production_cookie_is_host_only_strict_and_docs_are_closed(tmp_path):
    cfg = config(
        tmp_path / "data",
        profile="production",
        pack_path=E3B,
        repeat_indexes=(0, 1, 2, 3),
        planner="llm",
    )
    ctx = ServiceContext.open(cfg, clock=FakeClock())
    svc = Service(ctx=ctx, clock=FakeClock(), app=create_app(ctx), worker=None)  # type: ignore[arg-type]
    _, secret = ctx.store.create_principal("生产")
    b = Browser(svc)
    r = b.login(secret)
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    parts = [p.strip().lower() for p in cookie.split(";")]
    assert cookie.startswith("__Host-hs_session=")
    assert {"httponly", "secure", "path=/", "samesite=strict", "max-age=604800"} <= set(parts)
    assert not any(p.startswith("domain") for p in parts)
    assert r.headers["cache-control"] == "no-store"
    assert "strict-transport-security" in r.headers
    # 会话秘密不在响应正文里；CSRF token 是另一个值
    body = r.text
    secret_value = cookie.split(";", 1)[0].split("=", 1)[1]
    assert secret_value not in body and r.json()["csrf_token"] != secret_value
    for path in (f"{API}/docs", f"{API}/openapi.json", "/docs", "/openapi.json"):
        assert b.client.get(path).status_code == 404
    rules = r.json()["rules"]
    assert (rules["cities"], rules["clones_per_city"], rules["years"]) == (6, 4, 3)
    assert rules["planner_kind"] == "llm" and rules["live_calls_enabled"] is False
    assert (rules["max_cost"], rules["currency"]) == ("10", "CNY")


# ---------------------------------------------------------------------- HTTP 保护


def test_writes_need_same_origin_and_the_session_csrf_token(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    _, other = svc.person("另一个人")
    c = b.client
    cases = [
        ({}, "origin_rejected"),
        ({"Origin": "https://evil.example"}, "origin_rejected"),
        ({"Origin": ORIGIN}, "csrf_failed"),
        ({"Origin": ORIGIN, "X-CSRF-Token": other.csrf}, "csrf_failed"),
        (
            {"Origin": ORIGIN, "X-CSRF-Token": b.csrf, "Sec-Fetch-Site": "cross-site"},
            "origin_rejected",
        ),
    ]
    for headers, code in cases:
        r = c.post(f"{API}/drafts", headers=headers)
        assert (r.status_code, r.json()["code"]) == (403, code), headers
    assert c.post(f"{API}/drafts", headers=b.headers()).status_code == 201
    # 没有开放跨源：不回 CORS 头
    r = c.options(f"{API}/drafts", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_invite_exchange_is_same_origin_json_and_limited_per_source(tmp_path):
    svc = start(tmp_path)
    _, secret = svc.store.create_principal("限流")
    b = svc.browser()
    fake = "x" * 43
    assert (
        b.client.post(f"{API}/session", content=f'{{"invite_token":"{secret}"}}').status_code == 403
    )
    r = b.client.post(
        f"{API}/session",
        content=f"invite_token={secret}",
        headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"
    for _ in range(7):
        assert b.login(fake).status_code == 401
    # 第 10 次之后（含上面两次被拒的）超出：429，带等待时间
    assert b.login(fake).status_code == 401
    r = b.login(secret)
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert int(r.headers["retry-after"]) >= 1
    svc.clock.advance(seconds=61)
    assert b.login(secret).status_code == 200


def test_writes_are_limited_per_identity(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    _, other = svc.person("另一个人")
    for _ in range(60):
        assert b.post("/drafts").status_code == 201
    r = b.post("/drafts")
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert other.post("/drafts").status_code == 201  # 别人的额度不受影响
    svc.clock.advance(seconds=61)
    assert b.post("/drafts").status_code == 201


def test_body_over_256_kib_is_refused_before_parsing(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    draft = b.new_draft()
    big = "字" * (100 * 1024)  # 约 300 KiB 的 UTF-8
    r = b.client.put(
        f"{API}/drafts/{draft['draft']['draft_id']}",
        content=('{"expected_revision": 1, "draft": "' + big + '"}').encode("utf-8"),
        headers={**b.headers(), "Content-Type": "application/json"},
    )
    assert r.status_code == 413 and r.json()["code"] == "payload_too_large"
    assert big[:20] not in r.text


def test_errors_are_safe_and_never_echo_the_input(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = b.new_draft()
    bad = dict(view["draft"])
    bad["free_text"] = {"state": "provided", "text": "SECRET-MARKER-7f3a", "extra": 1}
    r = b.put(f"/drafts/{view['draft']['draft_id']}", json={"expected_revision": 1, "draft": bad})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "invalid_request" and "SECRET-MARKER" not in r.text
    assert set(body) <= {
        "status",
        "code",
        "message_zh",
        "impact_zh",
        "next_action_zh",
        "support_ref",
        "retry_after_seconds",
        "meta",
        "sample",
    }
    r = b.get("/no-such-thing")
    assert r.status_code == 404 and r.json()["code"] == "not_found"


# ---------------------------------------------------------------------- 2. 草稿冲突


def test_older_tab_save_conflicts_and_never_overwrites_newer_answers(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = b.new_draft()
    tab1 = b.fill(view, age=40)  # 第一个标签页保存成功：修订号 2
    assert tab1["draft"]["revision"] == 2
    # 第二个标签页还拿着修订号 1 的那一份
    stale = dict(view["draft"])
    stale["age"] = {"state": "provided", "value": 25}
    r = b.put(f"/drafts/{view['draft']['draft_id']}", json={"expected_revision": 1, "draft": stale})
    assert r.status_code == 409 and r.json()["code"] == "revision_conflict"
    now = b.get(f"/drafts/{view['draft']['draft_id']}").json()
    assert now["draft"]["revision"] == 2 and now["draft"]["age"]["value"] == 40


def test_draft_ids_and_revisions_are_server_assigned(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    a, c = b.new_draft(), b.new_draft()
    assert a["draft"]["draft_id"] != c["draft"]["draft_id"]
    assert a["draft"]["revision"] == 1
    # 正文里的编号与路径不同、修订号与 expected_revision 不同：拒绝
    moved = {**a["draft"], "draft_id": c["draft"]["draft_id"]}
    r = b.put(f"/drafts/{a['draft']['draft_id']}", json={"expected_revision": 1, "draft": moved})
    assert r.status_code == 422
    jumped = {**a["draft"], "revision": 9}
    r = b.put(f"/drafts/{a['draft']['draft_id']}", json={"expected_revision": 1, "draft": jumped})
    assert r.status_code == 422
    # 草稿列表在会话视图里（换设备后从这里接着填）
    ids = {d["draft_id"] for d in b.get("/session").json()["drafts"]}
    assert ids == {a["draft"]["draft_id"], c["draft"]["draft_id"]}


# ---------------------------------------------------------------------- 3. 不可伪造的确认


def test_confirmation_is_rebuilt_by_the_server_and_cannot_be_forged(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = b.fill(b.new_draft())
    draft_id = view["draft"]["draft_id"]
    review = b.post(f"/drafts/{draft_id}/review").json()
    req = {
        "review_id": review["review"]["review_id"],
        "draft_id": draft_id,
        "draft_revision": review["reviewed_revision"],
        "acknowledged": review["review"]["pending_confirmations"],
    }
    # 浏览器自己带一份确认画像、person_ref 或运行设置：结构就不接受
    for extra in (
        {"confirmed": {"confirmed_id": "x"}},
        {"person_ref": "someone"},
        {"cities": ["US:SYNTH2026:K51"]},
    ):
        r = b.post("/profiles/confirm", json={**req, **extra})
        assert r.status_code == 422, extra
    # 缺知悉项：拒绝
    if req["acknowledged"]:
        r = b.post("/profiles/confirm", json={**req, "acknowledged": []})
        assert r.json()["status"] == "refused"
    # 审阅之后草稿又改了：旧审阅不能用来确认
    changed = b.fill(b.get(f"/drafts/{draft_id}").json(), age=50)
    r = b.post("/profiles/confirm", json=req)
    assert r.json()["status"] == "refused"
    assert {i["code"] for i in r.json()["issues"]} == {"review_stale"}
    # 修订号写成当前的、审阅还是旧的：照样拒绝
    r = b.post("/profiles/confirm", json={**req, "draft_revision": changed["draft"]["revision"]})
    assert r.json()["status"] == "refused"
    # 正常确认之后，确认画像的 person_ref 由服务身份派生（不是浏览器给的）
    confirmed = b.confirm(changed)
    assert confirmed["person_ref"].startswith("p") and len(confirmed["person_ref"]) == 11


def test_draft_with_another_pack_reference_cannot_be_confirmed(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = b.fill(b.new_draft())
    draft = dict(view["draft"])
    pack_ref = {**draft["meta"]["data_pack"], "content_fingerprint": "0" * 64}
    draft["meta"] = {**draft["meta"], "data_pack": pack_ref}
    r = b.put(
        f"/drafts/{draft['draft_id']}",
        json={"expected_revision": draft["revision"], "draft": draft},
    )
    assert r.status_code == 200
    review = b.post(f"/drafts/{draft['draft_id']}/review").json()["review"]
    assert any(i["severity"] == "error" for i in review["issues"])
    r = b.post(
        "/profiles/confirm",
        json={
            "review_id": review["review_id"],
            "draft_id": draft["draft_id"],
            "draft_revision": draft["revision"] + 1,
            "acknowledged": review["pending_confirmations"],
        },
    )
    assert r.json()["status"] == "refused"


def test_start_confirmation_binds_the_preflight_and_rejects_browser_run_settings(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    view = ready_intent(svc, b)
    r = b.post(
        f"/run-intents/{view['intent_id']}/submit",
        json={"confirm_with": "0" * 64, "idempotency_key": "key-0001", "accept_scope": True},
    )
    assert r.status_code == 409 and r.json()["code"] == "preflight_stale"
    for extra in (
        {"years": 5},
        {"cities": ["US:SYNTH2026:K51"]},
        {"model": "other"},
        {"max_cost": "100"},
        {"data_dir": "/tmp"},
        {"repeat_indexes": [0, 1]},
    ):
        r = b.submit(view, **extra)
        assert r.status_code == 422, extra
    r = b.post(
        f"/run-intents/{view['intent_id']}/submit",
        json={
            "confirm_with": view["confirm_with"],
            "idempotency_key": "key-0001",
            "accept_scope": False,
        },
    )
    assert r.status_code == 422
    assert b.quota()["reserved"] == 0
    # 规模固定：预检查与回执里是服务规定的规模
    pre = view["preflight"]
    assert pre["clones_per_city"] == 1 and pre["years"] == 3
    assert {c["city_ref"] for c in pre["cities"]} == set(svc.ctx.spec.cities)


def test_scale_is_fixed_by_the_server_not_the_draft(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    one_city = (svc.ctx.spec.cities[0],)
    confirmed = b.confirm(b.fill(b.new_draft(), cities=one_city))
    r = b.post("/run-intents", json={"confirmed_id": confirmed["confirmed_id"]})
    assert r.status_code == 409 and r.json()["code"] == "run_scale_fixed"


# ---------------------------------------------------------------------- 11. 查询没有副作用


def test_queries_change_nothing(tmp_path):
    svc = start(tmp_path)
    _, b = svc.person()
    done = finished_run(svc, b)
    run_id = done["run_id"]
    db = svc.ctx.data_root / "service.sqlite"
    svc.store.checkpoint()
    before = (db_dump(db), files_under(svc.ctx.data_root / "runs"))
    for _ in range(3):
        for path in (
            "/session",
            "/capabilities",
            "/runs",
            f"/run-intents/{done['intent_id']}",
            f"/runs/{run_id}/progress",
            f"/runs/{run_id}/result",
        ):
            assert b.get(path).status_code == 200, path
    svc.store.checkpoint()
    assert (db_dump(db), files_under(svc.ctx.data_root / "runs")) == before
    assert svc.store.pending_jobs() == []


@pytest.mark.parametrize("path", ["/session", "/capabilities", "/runs", "/drafts/draft-x"])
def test_reads_need_a_session(tmp_path, path):
    svc = start(tmp_path)
    r = svc.browser().get(path)
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"


def test_changed_server_run_settings_force_a_fresh_preflight(tmp_path):
    svc = start(tmp_path)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    # 服务的正式运行设置在用户确认开始之前变了（例如资料包或上限更新）：旧预检查不能用来开始，按新设置重新检查
    ctx = svc.ctx
    ctx.spec = ctx.spec.model_copy(update={"max_cost": "9"})
    r = b.submit(view)
    assert r.status_code == 409 and r.json()["code"] == "preflight_stale"
    assert b.intent(view["intent_id"])["stage"] == "checking"
    assert b.quota()["reserved"] == 0
    svc.work()
    again = b.intent(view["intent_id"])
    assert again["stage"] == "awaiting_confirmation"
    assert (
        svc.store.intent(principal, view["intent_id"])["execution_fingerprint"]
        == ctx.spec.fingerprint
    )
    assert b.submit(again, key="key-0002").status_code == 202
