"""服务测试的辅助：临时服务（配置、上下文、应用、worker）、带 Origin 与 CSRF 的“浏览器”，以及一条正常的业务路径。

人物都是合成的（``product.fixtures.make_draft``）；邀请秘密与会话秘密都是测试里现生成的假秘密，只在临时数据目录里。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from product.fixtures import make_draft
from product.service.actions import ServiceContext
from product.service.api import create_app
from product.service.clock import FakeClock
from product.service.config import ServiceConfig
from workflows.service_worker import Worker

ORIGIN = "https://hs.test"
API = "/api/v1"
SYNTH = "configs/product/e3a_synthetic_pack.yaml"
E3B = "configs/product/e3b_us_v1_pack.yaml"


def config(data: Path, **over: Any) -> ServiceConfig:
    """测试配置：临时数据目录；默认合成资料包、每城 1 个分身、离线 mock（快）。"""
    base: dict[str, Any] = {
        "version": "e3c-service-config-v1",
        "profile": "test",
        "data_dir": str(data),
        "site_origin": ORIGIN,
        "pack_path": SYNTH,
        "repeat_indexes": (0,),
        "planner": "mock",
        "workers": 4,
        "progress_poll_seconds": 0.05,
    }
    base.update(over)
    return ServiceConfig.model_validate(base)


@dataclass
class Service:
    ctx: ServiceContext
    clock: FakeClock
    app: Any
    worker: Worker
    browsers: list[Browser] = field(default_factory=list)

    @property
    def store(self):
        return self.ctx.store

    def browser(self, token: str | None = None) -> Browser:
        b = Browser(self)
        if token is not None:
            r = b.login(token)
            assert r.status_code == 200, r.text
        self.browsers.append(b)
        return b

    def person(self, label: str = "测试用户") -> tuple[str, Browser]:
        principal, secret = self.store.create_principal(label)
        return principal, self.browser(secret)

    def work(self) -> int:
        """worker 把待办任务做完（同一进程里，串行）。"""
        return self.worker.run_pending()


def start(
    tmp_path: Path, *, clock: FakeClock | None = None, transport=None, **over: Any
) -> Service:
    clock = clock or FakeClock()
    ctx = ServiceContext.open(config(tmp_path / "data", **over), clock=clock)
    worker = Worker(ctx, transport_factory=transport, sleep=lambda _s: None)
    return Service(ctx=ctx, clock=clock, app=create_app(ctx), worker=worker)


class Browser:
    """一个设备上的浏览器：同一个 Cookie 罐；修改请求自动带本站 Origin 与本会话的 CSRF token。"""

    def __init__(self, svc: Service) -> None:
        self.svc = svc
        self.client = TestClient(svc.app, base_url=ORIGIN)
        self.csrf: str | None = None
        self.token: str | None = None

    def login(self, token: str, **headers: str):
        r = self.client.post(
            f"{API}/session", json={"invite_token": token}, headers={"Origin": ORIGIN, **headers}
        )
        if r.status_code == 200:
            self.csrf, self.token = r.json()["csrf_token"], token
        return r

    def reopen(self) -> None:
        """会话固定 7 天：跨过它之后用户重新打开原来的专属链接。"""
        assert self.token is not None
        r = self.login(self.token)
        assert r.status_code == 200, r.text

    def headers(self, **extra: str) -> dict[str, str]:
        h = {"Origin": ORIGIN}
        if self.csrf:
            h["X-CSRF-Token"] = self.csrf
        h.update(extra)
        return h

    def get(self, path: str, **kw: Any):
        return self.client.get(API + path, **kw)

    def post(self, path: str, json: Any = None, **kw: Any):
        return self.client.post(API + path, json=json, headers=self.headers(), **kw)

    def put(self, path: str, json: Any = None, **kw: Any):
        return self.client.put(API + path, json=json, headers=self.headers(), **kw)

    def delete(self, path: str, **kw: Any):
        return self.client.delete(API + path, headers=self.headers(), **kw)

    # ---- 一条正常的业务路径 ----
    def new_draft(self) -> dict[str, Any]:
        r = self.post("/drafts")
        assert r.status_code == 201, r.text
        return r.json()

    def fill(self, draft_view: dict[str, Any], **draft_kwargs: Any) -> dict[str, Any]:
        """按合成人物 u501 填完整草稿，带版本保存。"""
        d = draft_view["draft"]
        spec = self.svc.ctx.spec
        kwargs: dict[str, Any] = {"cities": tuple(spec.cities)}
        if spec.pack_path == E3B:
            kwargs["abilities"] = ("edu_at_least_hs",)
        kwargs.update(draft_kwargs)
        draft = make_draft(
            self.svc.ctx.pack, draft_id=d["draft_id"], revision=d["revision"], **kwargs
        )
        r = self.put(
            f"/drafts/{d['draft_id']}",
            json={"expected_revision": d["revision"], "draft": draft.model_dump(mode="json")},
        )
        assert r.status_code == 200, r.text
        return r.json()

    def confirm(self, draft_view: dict[str, Any]) -> dict[str, Any]:
        draft_id = draft_view["draft"]["draft_id"]
        r = self.post(f"/drafts/{draft_id}/review")
        assert r.status_code == 200, r.text
        review = r.json()
        r = self.post(
            "/profiles/confirm",
            json={
                "review_id": review["review"]["review_id"],
                "draft_id": draft_id,
                "draft_revision": review["reviewed_revision"],
                "acknowledged": review["review"]["pending_confirmations"],
            },
        )
        assert r.status_code == 200, r.text
        reply = r.json()
        assert reply["status"] == "confirmed", reply
        return reply["confirmed"]

    def prepare(self, **draft_kwargs: Any) -> dict[str, Any]:
        """草稿 → 审阅 → 确认 → 运行意图（排队检查）。返回意图视图。"""
        confirmed = self.confirm(self.fill(self.new_draft(), **draft_kwargs))
        r = self.post("/run-intents", json={"confirmed_id": confirmed["confirmed_id"]})
        assert r.status_code == 202, r.text
        return r.json()

    def intent(self, intent_id: str) -> dict[str, Any]:
        r = self.get(f"/run-intents/{intent_id}")
        assert r.status_code == 200, r.text
        return r.json()

    def submit(self, intent: dict[str, Any], key: str = "key-0001", **extra: Any):
        return self.post(
            f"/run-intents/{intent['intent_id']}/submit",
            json={
                "confirm_with": intent["confirm_with"],
                "idempotency_key": key,
                "accept_scope": True,
                **extra,
            },
        )

    def quota(self) -> dict[str, int]:
        return self.get("/session").json()["quota"]


def ready_intent(svc: Service, b: Browser, **draft_kwargs: Any) -> dict[str, Any]:
    """准备到“等你确认开始”。"""
    intent = b.prepare(**draft_kwargs)
    svc.work()
    view = b.intent(intent["intent_id"])
    assert view["stage"] == "awaiting_confirmation", view
    return view


def finished_run(svc: Service, b: Browser, **draft_kwargs: Any) -> dict[str, Any]:
    view = ready_intent(svc, b, **draft_kwargs)
    r = b.submit(view, key=f"key-{view['intent_id']}")
    assert r.status_code == 202, r.text
    svc.work()
    done = b.intent(view["intent_id"])
    assert done["stage"] == "finished", done
    return done


def db_dump(path: Path) -> dict[str, list[tuple]]:
    """服务库的全部内容（比较“查询没有副作用”用）。"""
    con = sqlite3.connect(path)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {t: sorted(con.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in tables}
    finally:
        con.close()


def files_under(root: Path) -> dict[str, int]:
    return {
        p.relative_to(root).as_posix(): p.stat().st_size
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }
