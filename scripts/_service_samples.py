"""托管服务（E3C，``e3a-product-schema-v3.3``）的公共样例：全部由服务端的真实代码路径产生（HTTP → 业务函数 → worker →
LangGraph → E1），由 ``scripts/product_e3a.py samples`` 调用。

* 人物是合成的；资料是合成资料包；规划器是离线 mock，零网络、零费用；
* 时钟是注入的测试时钟（2026-10-24T09:00:00Z 起），编号与秘密用确定的**测试值**生成：邀请与会话秘密写成
  ``TEST-ONLY-not-a-secret-…``，CSRF token 在样例里换成 ``TEST_ONLY_invalid_csrf…``——都不是任何地方能用的凭证；
* “运行中”的样例用离线的**测试传输**（真实模型规划器的通路，传输层是替身：按 mock 规则从安全投影生成计划，不联网）。
"""

from __future__ import annotations

import itertools
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

ORIGIN = "https://hs.example.invalid"
API = "/api/v1"
FAKE_CSRF = "TEST_ONLY_invalid_csrf_token_000000000000000"
FAKE_INVITE = "TEST_ONLY_invalid_invite_token_00000000000000"


def _label(origin: str, produced_by: str, note: str) -> dict[str, Any]:
    return {"origin": origin, "synthetic": True, "produced_by": produced_by, "note": note}


class _Box:
    """一个服务实例 + 一台“浏览器”（同一个 Cookie 罐）。"""

    def __init__(self, work: Path, clock: Any, **over: Any) -> None:
        from fastapi.testclient import TestClient

        from product.service.actions import ServiceContext
        from product.service.api import create_app
        from product.service.config import ServiceConfig
        from workflows.service_worker import Worker

        cfg = {
            "version": "e3c-service-config-v1",
            "profile": "test",
            "data_dir": str(work / "data"),
            "site_origin": ORIGIN,
            "pack_path": "configs/product/e3a_synthetic_pack.yaml",
            "default_clones_per_city": 1,
            "planner": "mock",
            "workers": 1,
            "progress_poll_seconds": 3600,
            **over,
        }
        self.ctx = ServiceContext.open(ServiceConfig.model_validate(cfg), clock=clock)
        self.worker = Worker(self.ctx, sleep=lambda _s: None)
        self.app = create_app(self.ctx)
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.csrf: str | None = None
        self.token: str | None = None

    def person(self, label: str) -> str:
        principal, secret = self.ctx.store.create_principal(label)
        self.login(secret)
        return principal

    def login(self, token: str) -> Any:
        r = self.client.post(
            f"{API}/session", json={"invite_token": token}, headers={"Origin": ORIGIN}
        )
        if r.status_code == 200:
            self.csrf, self.token = r.json()["csrf_token"], token
        return r

    def h(self) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": self.csrf or ""}

    def get(self, path: str) -> Any:
        return self.client.get(API + path)

    def post(self, path: str, body: Any = None) -> Any:
        return self.client.post(API + path, json=body, headers=self.h())

    def put(self, path: str, body: Any) -> Any:
        return self.client.put(API + path, json=body, headers=self.h())

    def delete(self, path: str) -> Any:
        return self.client.delete(API + path, headers=self.h())

    def draft_body(self, view: dict[str, Any], **kw: Any) -> dict[str, Any]:
        from product.fixtures import make_draft

        d = view["draft"]
        draft = make_draft(
            self.ctx.pack,
            draft_id=d["draft_id"],
            revision=d["revision"],
            cities=kw.pop("cities", tuple(self.ctx.policy.cities_allowed)),
            **kw,
        )
        return {"expected_revision": d["revision"], "draft": draft.model_dump(mode="json")}

    def confirmed(self, **kw: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        view = self.post("/drafts").json()
        saved = self.put(f"/drafts/{view['draft']['draft_id']}", self.draft_body(view, **kw)).json()
        review = self.post(f"/drafts/{saved['draft']['draft_id']}/review").json()
        req = {
            "review_id": review["review"]["review_id"],
            "draft_id": saved["draft"]["draft_id"],
            "draft_revision": review["reviewed_revision"],
            "acknowledged": list(review["review"]["pending_confirmations"]),
        }
        return req, self.post("/profiles/confirm", req).json()

    def ready(self, options: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        _, reply = self.confirmed(**kw)
        intent = self.post(
            "/run-intents",
            {"confirmed_id": reply["confirmed"]["confirmed_id"], **(options or {})},
        ).json()
        self.worker.run_pending()
        return self.get(f"/run-intents/{intent['intent_id']}").json()

    def submit(self, view: dict[str, Any], key: str) -> Any:
        return self.post(
            f"/run-intents/{view['intent_id']}/submit",
            {"confirm_with": view["confirm_with"], "idempotency_key": key, "accept_scope": True},
        )

    def finished(self, options: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        view = self.ready(options, **kw)
        self.submit(view, f"key-{view['intent_id']}")
        self.worker.run_pending()
        return self.get(f"/run-intents/{view['intent_id']}").json()


def _test_transport(block_at: int | None, gate: threading.Event, reached: threading.Event) -> Any:
    """测试传输：按 mock 规则给计划；第 ``block_at`` 次发送时停住，等样例拍下“运行中”的快照。"""
    from agentlayer.life_llm import TransportResponse, estimate_input_tokens
    from agentlayer.life_mock import ProductMockPlanner
    from agentlayer.life_planning import PlannerCall, payload_from_user_prompt

    count = itertools.count(1)

    class Transport:
        def send(self, body: dict[str, Any], *, timeout: float) -> TransportResponse:
            n = next(count)
            if n == block_at:
                reached.set()
                gate.wait(120)
            user = body["messages"][1]["content"]
            payload = payload_from_user_prompt(user)  # 紧凑表示要解开（E3B 映射修正）
            text = ProductMockPlanner().plan(
                PlannerCall(payload["request_id"], body["messages"][0]["content"], user, payload)
            )
            est = estimate_input_tokens(body["messages"])
            return TransportResponse(
                text=text,
                finish_reason="stop",
                model=body["model"],
                prompt_tokens=est // 3,
                completion_tokens=max(1, len(text) // 2),
            )

    return Transport()


def _deterministic() -> list[Any]:
    """编号与秘密用确定的测试值（样例可以复现、一看就知道不是真凭证）。"""
    from product.service import tokens

    counter = itertools.count(1)
    return [
        mock.patch.object(tokens.secrets, "token_hex", lambda n: f"{next(counter):0{2 * n}x}"),
        mock.patch.object(
            tokens.secrets,
            "token_urlsafe",
            lambda _n: f"TEST-ONLY-not-a-secret-{next(counter):020d}",
        ),
        mock.patch.object(tokens.secrets, "token_bytes", lambda n: bytes(n)),
    ]


def service_samples(out: Path, work: Path, write: Callable[..., None]) -> None:
    from product.service.clock import FakeClock

    patches = _deterministic()
    for p in patches:
        p.start()
    try:
        _Samples(out, work, write, FakeClock()).run()
    finally:
        for p in reversed(patches):
            p.stop()


class _Samples:
    def __init__(self, out: Path, work: Path, write: Callable[..., None], clock: Any) -> None:
        self.out, self.work, self.write, self.clock = out, work, write, clock
        by = "product.service（HTTP → 业务函数 → worker → LangGraph → E1）"
        self.normal = _label(
            "offline_run",
            by,
            "托管服务的真实代码路径（测试时钟、测试秘密、合成人物、离线 mock，零网络）",
        )
        self.fixture = _label(
            "controlled_fixture",
            by,
            "受控夹具：故障注入或测试传输（离线替身，不是真实模型），零网络、零费用",
        )

    def w(
        self,
        name: str,
        message: str,
        description: str,
        payload: dict[str, Any],
        label: dict[str, Any] | None = None,
    ) -> None:
        label = label or self.normal
        payload = json.loads(json.dumps(payload))
        if isinstance(payload.get("meta"), dict):
            payload["meta"]["sample"] = label
            self.write(self.out, name, message, description, payload)
            return
        if message == "ServiceError":
            payload["sample"] = label
        self.write(self.out, name, message, description, payload, sample=label)

    def run(self) -> None:
        box = _Box(self.work / "normal", self.clock)
        self.normal_path(box)
        self.blocked(box)
        self.paused(box)
        self.partial(box)
        self.quota(box)
        self.deletion(box)
        self.running()
        self.options(box)
        self.stops()
        self.e3b_result()
        self.expiry_and_errors(box)
        display_samples(self)  # 最后跑：前面的样例时间与编号都不受影响

    # ---- 运行选项：较小规模、越界（E3C 收尾第二步）
    def options(self, box: _Box) -> None:
        w = self.w
        box.person("合成人物 u530")
        req, reply = box.confirmed(cities=("US:SYNTH2026:K51",))
        create = {
            "confirmed_id": reply["confirmed"]["confirmed_id"],
            "clones_per_city": 2,
            "max_cost_cny": "5",
        }
        w(
            "97_service_intent_create_smaller",
            "RunIntentCreateRequest",
            "准备一次较小规模的模拟：城市来自确认画像（这里只确认了 K51 一座城），每城 2 个分身、这次费用上限 5 元；省略的年数用服务默认",
            create,
        )
        intent = box.post("/run-intents", create).json()
        box.worker.run_pending()
        w(
            "98_service_intent_smaller_ready",
            "RunIntentView",
            "较小规模的运行意图：settings 是这次保存的有效设置（1 座城 × 每城 2 个分身 × 3 年，规划候选上限 1 × 2 × 3 × 3 = 18）；"
            "之后的检查、确认、执行与恢复都用这一份，改服务默认值不影响它",
            box.get(f"/run-intents/{intent['intent_id']}").json(),
        )
        box.post(f"/run-intents/{intent['intent_id']}/cancel")
        w(
            "99_service_error_run_options_invalid",
            "ServiceError",
            "选项不在服务允许的范围里（这里每城 9 个分身，允许 1–4）：在预留与任何调用之前就拒绝；field 指出是哪一项，不回显填的值",
            box.post(
                "/run-intents",
                {"confirmed_id": reply["confirmed"]["confirmed_id"], "clones_per_city": 9},
            ).json(),
        )
        del req

    # ---- 停下：服务尝试用完（不能恢复）、预算停下、开始之后的失败（测试传输与受控夹具）
    def stops(self) -> None:
        from agentlayer.life_llm import TransportError

        w = self.w

        class Flaky:
            """测试传输：前两次服务请求都超时（同一份候选的两次服务尝试用完）。"""

            def __init__(self) -> None:
                self.n = 0

            def send(self, body: dict[str, Any], *, timeout: float) -> Any:
                self.n += 1
                raise TransportError("timeout", retryable=True)

        exhausted = _Box(
            self.work / "exhausted", self.clock, planner="llm", live_calls_enabled=True
        )
        flaky = Flaky()
        exhausted.worker.transport_factory = lambda _s: flaky
        exhausted.person("合成人物 u531")
        view = exhausted.ready()
        exhausted.submit(view, "key-exhausted-0001")
        exhausted.worker.run_pending()
        stopped = exhausted.get(f"/run-intents/{view['intent_id']}").json()
        w(
            "100_service_intent_not_resumable",
            "RunIntentView",
            "停下且不能由你恢复（测试传输：同一份规划的两次服务尝试都超时，尝试次数已经用完）：waiting_for = maintainer，"
            "没有“恢复”按钮——E1 不会再发这份请求，也不会清零尝试次数或另开账本",
            stopped,
            self.fixture,
        )
        w(
            "101_service_progress_not_resumable",
            "RunProgress",
            "同一次运行的进度：resumable = false，与按钮一致（测试传输）",
            exhausted.get(f"/runs/{stopped['run_id']}/progress").json(),
            self.fixture,
        )
        budget = _Box(
            self.work / "budget",
            self.clock,
            planner="llm",
            live_calls_enabled=True,
            max_cost_cny="0.1",
        )
        gate, reached = threading.Event(), threading.Event()
        gate.set()
        budget.worker.transport_factory = lambda _s: _test_transport(None, gate, reached)
        budget.person("合成人物 u532")
        bv = budget.ready()
        budget.submit(bv, "key-budget-0001")
        budget.worker.run_pending()
        w(
            "102_service_intent_budget_stopped",
            "RunIntentView",
            "预算停下（测试传输，这次费用上限 0.1 元）：发下一次请求的预留会超过上限，就停在发送之前；不自动追加预算，已经完成的部分"
            "保留；开始之后不退次数",
            budget.get(f"/run-intents/{bv['intent_id']}").json(),
            self.fixture,
        )
        charged = _Box(self.work / "charged", self.clock)
        charged.person("合成人物 u533")
        cv = charged.ready()
        charged.submit(cv, "key-charged-0001")
        charged.worker.faults = {"life:halt_at_call:3:service_unavailable"}
        charged.worker.run_pending()
        charged.worker.faults = None
        snap = charged.ctx.run_dir(cv["intent_id"]) / "ws/configs/life/synthetic_product.yaml"
        snap.write_bytes(snap.read_bytes() + b"\n# controlled fixture change\n")
        paused = charged.get(f"/run-intents/{cv['intent_id']}").json()
        charged.post(f"/runs/{paused['run_id']}/resume")
        charged.worker.run_pending()
        w(
            "103_service_intent_charged_failure",
            "RunIntentView",
            "开始之后的失败（受控夹具：暂停期间这次运行绑定的资料快照被改动，恢复被拒绝）：说明写“模拟已经开始、已经用掉 1 次机会”，"
            "不说“没有开始”“已经退回”；没有“取消”，等维护者或删除",
            charged.get(f"/run-intents/{cv['intent_id']}").json(),
            self.fixture,
        )

    # ---- 最新六城资料：E3B 真实资料包经服务路径的结果（每城 1 个分身，离线 mock）
    def e3b_result(self) -> None:
        real = _Box(
            self.work / "e3b",
            self.clock,
            pack_path="configs/product/e3b_us_v1_pack.yaml",
            workers=6,
        )
        real.person("合成人物 u534")
        done = real.finished(abilities=("edu_at_least_hs",))
        result = real.get(f"/runs/{done['run_id']}/result").json()
        ident = real.ctx.pack.fingerprint
        report = real.ctx.repo / "data/frozen/life/us-v1/pack/report.json"
        build = json.loads(report.read_text(encoding="utf-8")).get("build_identity", "?")
        self.w(
            "104_service_e3b_result",
            "ProductResult",
            f"最新六城资料（冻结的 E3B 真实资料包，内容身份 {ident[:12]}…，构建身份 {build[:12]}…）经托管服务路径的结果：六座城各 1 个"
            "分身、3 年，离线 mock；逐城的资料性质与覆盖说明在 data_nature，C / E / M 与模拟缺失照 E1 原样（不是“全部 C”）",
            result,
        )

    # ---- 正常路径：链接 → 草稿 → 审阅 → 确认 → 排队检查 → 确认开始 → 结束
    def normal_path(self, box: _Box) -> None:
        w = self.w
        w(
            "60_service_session_exchange_request",
            "SessionExchangeRequest",
            "用专属链接换会话（POST /api/v1/session，同源 JSON）：invite_token 是链接 #token= 后面的秘密；这里是明确无效的测试值",
            {"invite_token": FAKE_INVITE},
        )
        box.person("合成人物 u501")
        session = box.get("/session").json()
        session["csrf_token"] = FAKE_CSRF
        w(
            "61_service_session",
            "SessionView",
            "当前会话：到期时间（固定 7 天，查询不续期）、本会话的 CSRF token（样例里换成了无效测试值）、次数 3/0/0/3 与服务规则"
            "（这是测试配置：合成资料包、默认每城 1 个分身；生产默认六城 × 每城 4 个分身 × 3 年、20 元，可选范围在 rules.options）",
            session,
        )
        blank = box.post("/drafts").json()
        draft_id = blank["draft"]["draft_id"]
        w(
            "62_service_draft_created",
            "DraftView",
            "新建草稿：编号与修订号由服务器分配；城市已按服务规则选好，其余全部未答（未答不是 0）",
            blank,
        )
        save = box.draft_body(blank)
        w(
            "63_service_draft_save_request",
            "DraftSaveRequest",
            "带版本保存（PUT /api/v1/drafts/{id}）：expected_revision 必须是服务端当前的修订号",
            save,
        )
        w(
            "64_service_draft_saved",
            "DraftView",
            "保存之后：修订号加一；到期时间是最后一次编辑后 30 天",
            box.put(f"/drafts/{draft_id}", save).json(),
        )
        w(
            "65_service_error_revision_conflict",
            "ServiceError",
            "另一个标签页拿着旧修订号保存：冲突，较新的答案没有被覆盖",
            box.put(f"/drafts/{draft_id}", save).json(),
        )
        w(
            "66_service_review_reply",
            "ReviewReply",
            "审阅已保存的那一版草稿：绑定草稿编号、修订号、内容与资料包",
            box.post(f"/drafts/{draft_id}/review").json(),
        )
        req, reply = box.confirmed()
        w(
            "67_service_confirm_request",
            "ProfileConfirmRequest",
            "确认画像：只带审阅引用与知悉项；服务端重新校验并生成确认画像",
            req,
        )
        w(
            "68_service_confirm_reply",
            "ProfileConfirmReply",
            "确认成功：服务端生成的确认画像（person_ref 由服务身份派生，不是浏览器给的）",
            reply,
        )
        create = {"confirmed_id": reply["confirmed"]["confirmed_id"]}
        w(
            "69_service_intent_create_request",
            "RunIntentCreateRequest",
            "准备一次模拟：引用一份确认画像（服务端排队做运行前检查，不扣次数）",
            create,
        )
        checking = box.post("/run-intents", create).json()
        w(
            "70_service_intent_checking",
            "RunIntentView",
            "排队检查中：运行前检查由 worker 按顺序做（可能要等正在运行的模拟），不使用次数",
            checking,
        )
        box.worker.run_pending()
        ready = box.get(f"/run-intents/{checking['intent_id']}").json()
        w(
            "71_service_intent_awaiting_confirmation",
            "RunIntentView",
            "等你确认开始：附运行前说明（ProductPreflight）与它的身份 confirm_with；允许的操作是确认开始与取消",
            ready,
        )
        submit = {
            "confirm_with": ready["confirm_with"],
            "idempotency_key": "tab-1-click-0001",
            "accept_scope": True,
        }
        w(
            "72_service_submit_request",
            "RunSubmitRequest",
            "确认开始：带用户看到的那份预检查身份、幂等键与“知道范围、限制和费用保护，使用一次机会开始”",
            submit,
        )
        path = f"/run-intents/{ready['intent_id']}/submit"
        w(
            "73_service_submit_accepted",
            "RunSubmitReply",
            "接受：预留 1 次（reserved = 1）、写下稳定运行编号、排队（stage = queued）",
            box.post(path, submit).json(),
        )
        w(
            "74_service_submit_duplicate",
            "RunSubmitReply",
            "回复丢了、用同一个键重发：同一个运行（duplicate_same_run），不再预留、不再排队",
            box.post(path, submit).json(),
        )
        w(
            "75_service_error_idempotency_conflict",
            "ServiceError",
            "同一个幂等键配了不同的内容：冲突，什么也没做",
            box.post(path, {**submit, "confirm_with": "1" * 64}).json(),
        )
        box.worker.run_pending()
        done = box.get(f"/run-intents/{ready['intent_id']}").json()
        w(
            "78_service_intent_finished",
            "RunIntentView",
            "本次服务流程结束：有结果（结果里逐城写明完成、出局与模拟缺失；finished 不代表所有分身都是 C）；次数 used = 1；"
            "结果从首次结束起保存 30 天",
            done,
        )
        w(
            "79_service_progress_completed",
            "RunProgress",
            "运行结束后的进度快照（worker 写的白名单快照；查询只读它，不恢复、不调用规划器）",
            box.get(f"/runs/{done['run_id']}/progress").json(),
        )
        w("80_service_runs", "RunList", "我的运行：新的在前；带次数摘要", box.get("/runs").json())

    # ---- 被阻断：收银员在合成城市 K51 没有对应岗位
    def blocked(self, box: _Box) -> None:
        box.person("合成人物 u520")
        _, ok = box.confirmed(occupation_text="我在超市做收银", selected="occ.cashier")
        view = box.post("/run-intents", {"confirmed_id": ok["confirmed"]["confirmed_id"]}).json()
        box.worker.run_pending()
        self.w(
            "81_service_intent_blocked",
            "RunIntentView",
            "运行前检查没有通过：收银员在合成城市 K51 没有同一分类的岗位（不自动换职业、不删城）；不使用次数，可以取消后修改",
            box.get(f"/run-intents/{view['intent_id']}").json(),
        )

    # ---- 暂停：第 3 次规划调用时服务不可用（受控夹具），等用户恢复
    def paused(self, box: _Box) -> None:
        box.person("合成人物 u521")
        view = box.ready()
        box.submit(view, "key-paused-0001")
        box.worker.faults = {"life:halt_at_call:3:service_unavailable"}
        box.worker.run_pending()
        box.worker.faults = None
        paused = box.get(f"/run-intents/{view['intent_id']}").json()
        self.w(
            "82_service_intent_paused",
            "RunIntentView",
            "暂停：模型服务暂时不可用，模拟按 E1 的规则在两份候选之间停下；等你点“恢复”（不另扣次数，已保存的回复不会重新请求）",
            paused,
            self.fixture,
        )
        self.w(
            "83_service_progress_paused",
            "RunProgress",
            "暂停时的进度快照：stage = halted，resumable = true",
            box.get(f"/runs/{paused['run_id']}/progress").json(),
            self.fixture,
        )

    # ---- 部分结果：一个分身第 1 年规划器出错（受控夹具），E1 记为模拟缺失
    def partial(self, box: _Box) -> None:
        from common.life_planning_contracts import MockScript
        from workflows import service_runs as svc_runs

        principal = box.person("合成人物 u522")
        original = svc_runs.RunDir.request
        clone = f"{box.ctx.person_ref(principal)}.k51.r0"

        def with_script(rd: Any, spec: Any) -> Any:
            script = MockScript(clone_id=clone, behavior="raise_error", years=[1], note="受控夹具")
            return original(rd, spec).model_copy(update={"scripts": (script,)})

        with mock.patch.object(svc_runs.RunDir, "request", with_script):
            done = box.finished()
        self.w(
            "84_service_partial_result",
            "ProductResult",
            "部分结果：一个分身第 1 年规划器出错（受控夹具），E1 记为模拟缺失 M；另一座城照常过完。服务流程是 finished，但不是所有"
            "分身都是 C",
            box.get(f"/runs/{done['run_id']}/result").json(),
            self.fixture,
        )

    # ---- 次数用完
    def quota(self, box: _Box) -> None:
        box.person("合成人物 u523")
        for _ in range(3):
            box.finished()
        _, more = box.confirmed()
        self.w(
            "85_service_error_quota_exhausted",
            "ServiceError",
            "3 次机会已经用完：不能再开始新的模拟（次数不自动重置；删除结果、换设备、重发链接都不改变它）",
            box.post("/run-intents", {"confirmed_id": more["confirmed"]["confirmed_id"]}).json(),
        )

    # ---- 删除
    def deletion(self, box: _Box) -> None:
        box.person("合成人物 u524")
        gone = box.finished()
        self.w(
            "86_service_deletion_receipt",
            "DeletionReceipt",
            "删除一次运行：立即不可查看、不再批准新的模型请求；收尾后清掉内容。保留的只有不透明身份、次数事件、必要的费用摘要与删除时间",
            box.delete(f"/runs/{gone['run_id']}").json(),
        )
        box.worker.run_pending()
        self.w(
            "87_service_intent_deleted",
            "RunIntentView",
            "删除收尾之后：只剩不含内容的墓碑（旧请求不能让它复活）；次数不退",
            box.get(f"/run-intents/{gone['intent_id']}").json(),
        )
        self.w(
            "88_service_error_deleted",
            "ServiceError",
            "查询已删除运行的结果：410，内容不能再查看或恢复",
            box.get(f"/runs/{gone['run_id']}/result").json(),
        )
        self.w(
            "89_service_deletion_all_data",
            "DeletionReceipt",
            "删除我的全部内容：草稿、审阅、确认与全部运行；保留次数记录与最小墓碑，专属链接仍然有效",
            box.delete("/me/data").json(),
        )
        box.worker.run_pending()

    # ---- 运行中（测试传输：真实模型规划器的通路，传输层是离线替身）
    def running(self) -> None:
        gate, reached = threading.Event(), threading.Event()
        live = _Box(self.work / "live", self.clock, planner="llm", live_calls_enabled=True)
        live.worker.transport_factory = lambda _s: _test_transport(4, gate, reached)
        live.person("合成人物 u525")
        view = live.ready()
        live.submit(view, "key-running-0001")
        runner = threading.Thread(target=live.worker.run_pending, daemon=True)
        runner.start()
        try:
            if not reached.wait(120):
                raise RuntimeError("测试传输没有收到第 4 次发送")
            row = live.ctx.store.intent_row(view["intent_id"])
            live.worker._write_progress(
                view["intent_id"], row["run_id"], running_now=True
            )  # 与监测线程运行中的那一次写相同
            running = live.get(f"/run-intents/{view['intent_id']}").json()
            progress = live.get(f"/runs/{row['run_id']}/progress").json()
        finally:
            gate.set()
            runner.join(300)
        self.w(
            "76_service_intent_running",
            "RunIntentView",
            "模拟进行中（测试传输：真实模型规划器的通路，传输层是离线替身，不是真实模型）：第 4 次服务请求在途；进度快照由 worker "
            "写，查询只读快照",
            running,
            self.fixture,
        )
        self.w(
            "77_service_progress_running",
            "RunProgress",
            "运行中的进度（测试传输）：费用按报价估算——已核算、用量不确定（保留预留）与在途预留分开写；规划候选数（calls_used）"
            "与服务尝试数（service_attempts）分开计；费用上限（这次运行的有效设置，默认 20 元）是应用侧上限，不是供应商账单；"
            "运行还在进行，没有要恢复的东西",
            progress,
            self.fixture,
        )

    # ---- 过期与其他安全错误
    def expiry_and_errors(self, box: _Box) -> None:
        from fastapi.testclient import TestClient

        w = self.w
        box.person("合成人物 u526")
        old = box.finished()
        self.clock.advance(days=30, seconds=1)
        box.login(box.token or "")
        w(
            "90_service_intent_expired",
            "RunIntentView",
            "过期：结果从首次结束起 30 天（查看不续期）；API 访问时就按到期时间判断，内容不再返回",
            box.get(f"/run-intents/{old['intent_id']}").json(),
        )
        w(
            "91_service_error_expired",
            "ServiceError",
            "查询过期的结果：410",
            box.get(f"/runs/{old['run_id']}/result").json(),
        )
        w(
            "92_service_error_unauthenticated",
            "ServiceError",
            "没有有效的会话（到期、退出或链接被重发过）：重新打开专属链接",
            TestClient(box.app, base_url=ORIGIN).get(f"{API}/session").json(),
        )
        w(
            "93_service_error_csrf_failed",
            "ServiceError",
            "修改请求缺少会话绑定的 X-CSRF-Token：没有执行",
            box.client.post(f"{API}/drafts", headers={"Origin": ORIGIN}).json(),
        )
        self.clock.advance(seconds=61)
        last: Any = None
        for _ in range(11):
            last = box.client.post(
                f"{API}/session", json={"invite_token": FAKE_INVITE}, headers={"Origin": ORIGIN}
            )
        if last is None or last.json()["code"] != "rate_limited":
            raise RuntimeError("限流样例没有触发")
        w(
            "94_service_error_rate_limited",
            "ServiceError",
            "同一来源一分钟里交换邀请超过 10 次：429，带等待秒数",
            last.json(),
        )
        off = _Box(self.work / "off", self.clock, planner="llm", live_calls_enabled=False)
        off.person("合成人物 u528")
        r = off.submit(off.ready(), "key-off-0001")
        if r.json().get("code") != "paid_calls_disabled":
            raise RuntimeError(r.text)
        w(
            "95_service_error_paid_calls_disabled",
            "ServiceError",
            "服务没有开启付费模型调用（生产配置的默认）：确认开始被明确拒绝，不换成离线规划器，不使用次数",
            r.json(),
        )
        stale = _Box(self.work / "stale", self.clock)
        stale.person("合成人物 u527")
        view = stale.ready()
        w(
            "96_service_error_preflight_stale",
            "ServiceError",
            "确认的不是当前这一份运行前说明：没有开始，也没有使用次数",
            stale.post(
                f"/run-intents/{view['intent_id']}/submit",
                {
                    "confirm_with": "0" * 64,
                    "idempotency_key": "key-stale-0001",
                    "accept_scope": True,
                },
            ).json(),
        )


class _Resources:
    """受控夹具用的临时显示资源：登记表与冻结目录换到工作目录，``publish`` 冻结一份（可改过的）源文件副本并登记为当前版本。"""

    def __init__(self, root: Path) -> None:
        from product.display import catalog

        self.catalog, self.root = catalog, root
        self.patches = [
            mock.patch.object(catalog, "FROZEN", root / "frozen"),
            mock.patch.object(catalog, "REGISTRY", root / "registry.yaml"),
        ]

    def __enter__(self) -> _Resources:
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        for p in reversed(self.patches):
            p.stop()
        self.catalog._load_frozen.cache_clear()

    def publish(
        self,
        pack: Any,
        name: str,
        edit: Callable[[dict[str, Any]], None] | None = None,
        geo_edit: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        import shutil

        import yaml

        src = self.root / name
        shutil.copytree(self.catalog.SOURCE, src)
        if geo_edit is not None:
            path = src / "geography" / f"{pack.body.pack_id}.json"
            geo = json.loads(path.read_text(encoding="utf-8"))
            geo_edit(geo)
            path.write_text(json.dumps(geo, ensure_ascii=False), encoding="utf-8", newline="\n")
        if edit is not None:
            path = src / "templates" / "service.yaml"
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            edit(data["templates"])
            path.write_text(
                yaml.safe_dump(data, allow_unicode=True), encoding="utf-8", newline="\n"
            )
        version = self.catalog.freeze(pack.fingerprint, pack.body.pack_id, root=src)
        self.catalog.register(pack.fingerprint, pack.body.pack_id, version)
        return version


def _display(box: _Box, kind: str, ident: str | None, locale: str) -> Any:
    query = f"kind={kind}&locale={locale}" + (f"&id={ident}" if ident else "")
    return box.get(f"/display?{query}")


def display_samples(s: _Samples) -> None:
    """显示附层的样例（105 起）：正常、不允许的动作、即时错误、过期、恢复排队、部分结果，中英各一份；缺译文与历史版本见
    ``_fixture_resources``，地理清单见 ``_geography``。"""
    from common.life_planning_contracts import MockScript
    from workflows import service_runs as svc_runs

    w = s.w
    box = _Box(s.work / "display", s.clock)
    box.person("合成人物 u540")
    first = box.token or ""  # 同一台“浏览器”后面会换人：过期样例要换回这个人
    ready = box.ready()
    for n, locale in ((105, "zh"), (106, "en")):
        w(
            f"{n}_display_intent_ready_{locale}",
            "DisplayEnvelope",
            f"显示出口（GET /api/v1/display?kind=intent&id=…&locale={locale}）：等你确认开始的运行意图。source 与 "
            "GET /api/v1/run-intents/{id} 完全相同（数字、动作与确认 token 只看 source）；fields 是逐个文字位置的显示文字："
            + (
                "中文逐字照原文"
                if locale == "zh"
                else "英文只来自冻结的显示资源，缺的照实标 missing 与原因"
            ),
            _display(box, "intent", ready["intent_id"], locale).json(),
        )
    w(
        "107_service_profile_list",
        "ProfileList",
        "本人还在有效期内的确认画像摘要（GET /api/v1/profiles）：首次确认时间与截止时间来自服务库，重复确认不续期，换设备也能读回",
        box.get("/profiles").json(),
    )
    cid = box.get("/profiles").json()["profiles"][0]["confirmed_id"]
    w(
        "108_service_profile_view",
        "ConfirmedProfileView",
        "一份确认画像（GET /api/v1/profiles/{confirmed_id}）：summary（日期）加原来的 confirmed 正文，正文与内容身份不变",
        box.get(f"/profiles/{cid}").json(),
    )
    # 恢复排队：开始过的运行停下、点了恢复、又在排队——没有“取消”，直接取消也拒绝
    box.submit(ready, "key-display-0001")
    box.worker.faults = {"life:halt_at_call:3:service_unavailable"}
    box.worker.run_pending()
    box.worker.faults = None
    run_id = box.get(f"/run-intents/{ready['intent_id']}").json()["run_id"]
    box.post(f"/runs/{run_id}/resume")
    for n, locale in ((109, "zh"), (110, "en")):
        w(
            f"{n}_display_intent_resume_queued_{locale}",
            "DisplayEnvelope",
            "恢复排队：这次运行已经开始过（用掉 1 次机会），停下后点了恢复、正在排队——阶段仍是 queued，但 allowed_actions 没有 "
            f"cancel，说明分清“首次开始前的排队”与“恢复排队”（locale = {locale}）",
            _display(box, "intent", ready["intent_id"], locale).json(),
            s.fixture,
        )
    w(
        "111_service_error_cancel_not_allowed",
        "ServiceError",
        "恢复排队时直接调用取消：409 cancel_not_allowed——已经开始过、没有取消、次数不退；可以等待或按公开动作删除。display 带中英"
        "两种文字",
        box.post(f"/run-intents/{ready['intent_id']}/cancel").json(),
        s.fixture,
    )
    box.worker.run_pending()
    # 不允许的动作：真实模型规划器、付费调用关闭——没有 submit，说明在等维护者开启
    off = _Box(s.work / "display-off", s.clock, planner="llm", live_calls_enabled=False)
    off.person("合成人物 u541")
    waiting = off.ready()
    for n, locale in ((112, "zh"), (113, "en")):
        w(
            f"{n}_display_intent_paid_off_{locale}",
            "DisplayEnvelope",
            "付费调用关闭（生产配置的默认）：真实模型的运行意图检查通过，但 allowed_actions 没有 submit，waiting_for = maintainer，"
            f"说明写明原因；直接提交照旧被拒绝（样例 95），不扣次数（locale = {locale}）",
            _display(off, "intent", waiting["intent_id"], locale).json(),
        )
    # 即时错误：确认被拒（审阅之后草稿又存了一版）——回复里没有可以再读的对象，中英文字随回复给
    view = box.post("/drafts").json()
    saved = box.put(f"/drafts/{view['draft']['draft_id']}", box.draft_body(view)).json()
    review = box.post(f"/drafts/{saved['draft']['draft_id']}/review").json()
    box.put(f"/drafts/{saved['draft']['draft_id']}", box.draft_body(saved))
    w(
        "114_service_confirm_refused_with_display",
        "ProfileConfirmReply",
        "即时错误：确认引用的审阅已经过时（审阅之后草稿又存了一版），status = refused、profile_summary = null；display 带这份回复"
        "（去掉 display 之后）的中英两种文字，source_view_fingerprint 对得上",
        box.post(
            "/profiles/confirm",
            {
                "review_id": review["review"]["review_id"],
                "draft_id": saved["draft"]["draft_id"],
                "draft_revision": review["reviewed_revision"],
                "acknowledged": list(review["review"]["pending_confirmations"]),
            },
        ).json(),
    )
    # 部分结果：一个分身第 1 年规划器出错（受控夹具），结果的显示附层带固定色阶
    principal = box.person("合成人物 u542")
    original = svc_runs.RunDir.request
    clone = f"{box.ctx.person_ref(principal)}.k51.r0"

    def with_script(rd: Any, spec: Any) -> Any:
        script = MockScript(clone_id=clone, behavior="raise_error", years=[1], note="受控夹具")
        return original(rd, spec).model_copy(update={"scripts": (script,)})

    with mock.patch.object(svc_runs.RunDir, "request", with_script):
        part = box.finished()
    for n, locale in ((115, "zh"), (116, "en")):
        w(
            f"{n}_display_partial_result_{locale}",
            "DisplayEnvelope",
            "部分结果的显示（kind = result）：有模拟缺失 M 的结果，source 与 GET /api/v1/runs/{run_id}/result 相同；metrics 是 mean_j "
            "的固定色阶 [0, 1]（分母 mean_denominator，只在这份结果内比较，缺失范围不是置信区间）"
            f"（locale = {locale}）",
            _display(box, "result", part["run_id"], locale).json(),
            s.fixture,
        )
    # 过期：画像与运行意图的截止时间到了——错误说明分对象，过期意图的显示照样只读
    s.clock.advance(days=31)
    box.login(first)
    w(
        "117_service_error_profile_expired",
        "ServiceError",
        "确认画像过了截止时间（首次确认起 30 天，重复确认不续期）：410 expired，说明写的是“确认画像”；display 带中英两种文字",
        box.get(f"/profiles/{cid}").json(),
    )
    for n, locale in ((118, "zh"), (119, "en")):
        w(
            f"{n}_display_intent_expired_{locale}",
            "DisplayEnvelope",
            "过期的运行意图（结果从首次结束起 30 天）：阶段 expired、expiry_basis = unavailable，内容不再返回"
            f"（locale = {locale}）",
            _display(box, "intent", ready["intent_id"], locale).json(),
        )
    # 地理清单（仓库里登记的当前版本）：六城都会区代表点；合成城市没有现实位置
    from product.pack import load_pack

    e3b = load_pack(box.ctx.repo / "configs/product/e3b_us_v1_pack.yaml")
    w(
        "123_display_geography_e3b",
        "GeographyDisplayBundle",
        "E3B 六城的地理显示清单（GET /api/v1/display/geography?pack_fingerprint=…，省略 resource_version 即当前登记的版本）："
        "中英正式名与短名、CBSA 2023 代码、都会区代表点（Census Gazetteer 的 internal point，不是住宅、主城市中心或边界）、"
        "坐标系与来源、模拟实际支持的居住与工作范围（纽约、芝加哥、旧金山的限制写明）；清单不授予运行资格",
        box.get(f"/display/geography?pack_fingerprint={e3b.fingerprint}").json(),
    )
    w(
        "124_display_geography_synthetic",
        "GeographyDisplayBundle",
        "合成资料包的地理显示清单：合成城市 model_kind = synthetic、anchor_status = not_applicable、anchor = null——不伪造现实位置，"
        "页面用列表或示意位置",
        box.get(f"/display/geography?pack_fingerprint={box.ctx.pack.fingerprint}").json(),
    )
    _fixture_resources(s, e3b)


def _fixture_resources(s: _Samples, e3b: Any) -> None:
    """历史版本、缺译文与缺坐标（受控夹具：临时的显示资源，不是仓库里登记的版本）。"""
    w = s.w
    with _Resources(s.work / "display-res") as res:
        box = _Box(s.work / "display-history", s.clock)
        pack = box.ctx.pack
        v1 = res.publish(pack, "v1")
        box.person("合成人物 u543")
        view = box.ready()
        box.submit(view, "key-history-0001")
        box.worker.faults = {"life:halt_at_call:3:service_unavailable"}
        box.worker.run_pending()
        box.worker.faults = None

        def reword(templates: dict[str, Any]) -> None:
            for entry in templates.values():
                if entry.get("en"):
                    entry["en"] = entry["en"].replace("model service", "model provider")

        v2 = res.publish(pack, "v2", reword)
        w(
            "120_display_historical_version_en",
            "DisplayEnvelope",
            f"历史版本（受控夹具）：停下的说明在资源版本 {v1} 时生成并保存了绑定；之后登记了新版本 {v2}（英文改了措辞）。读取时说明"
            "部分仍按它记下的 v1 渲染（fields[].resource_version），当场生成的部分用当前版本；历史对象从不改用最新资源",
            _display(box, "intent", view["intent_id"], "en").json(),
            s.fixture,
        )
        w(
            "121_service_error_geography_version_missing",
            "ServiceError",
            "明确请求一个不存在的显示资源版本（GET /api/v1/display/geography?pack_fingerprint=…&resource_version=…）：404，不回退"
            "到当前版本",
            box.get(
                f"/display/geography?pack_fingerprint={pack.fingerprint}"
                "&resource_version=e3-display-res-v1-0000000000000000"
            ).json(),
            s.fixture,
        )

        def drop(templates: dict[str, Any]) -> None:
            for key in ("stage.awaiting_confirmation", "rules.mock"):
                templates[key]["en"] = None

        res.publish(pack, "v3", drop)
        box.person("合成人物 u544")
        ready = box.ready()
        w(
            "122_display_missing_translation_en",
            "DisplayEnvelope",
            "缺译文（受控夹具：这一版资源里有两条模板没有英文）：对应位置 status = missing、missing_reason = translation_missing、"
            "text = null，整体 status = partial；业务读取不受影响。页面在这些位置显示 source 原文并标明“英文说明尚未提供”",
            _display(box, "intent", ready["intent_id"], "en").json(),
            s.fixture,
        )

        def unknown_anchor(geo: dict[str, Any]) -> None:
            geo["status"] = "partial"
            city = next(c for c in geo["cities"] if c["city_ref"] == "US:CBSA2023:16980")
            city["anchor_status"], city["anchor"] = "unknown", None

        res.publish(e3b, "geo-unknown", geo_edit=unknown_anchor)
        w(
            "125_display_geography_missing_coordinate",
            "GeographyDisplayBundle",
            "缺坐标（受控夹具：这一版地理资源里芝加哥没有可靠的代表点）：anchor_status = unknown、anchor = null，清单 status = partial；"
            "城市仍在列表里、照常可选（地理清单不决定运行资格），页面不要用别的点代替，也不要画在地图上",
            box.get(f"/display/geography?pack_fingerprint={e3b.fingerprint}").json(),
            s.fixture,
        )
