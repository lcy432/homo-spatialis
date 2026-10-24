"""托管服务的离线联调（``scripts/product_service.py e2e`` 调用）：一个合成人物沿真实的服务路径走完一次模拟——HTTP（会话、CSRF、
草稿、审阅、确认、运行意图、确认开始）→ 业务函数 → worker → LangGraph → E1 → 产品结果。

* 规划器默认是离线 mock（零网络、零费用，不代表真实模型）；人物是合成的 u501（文员）；资料包按参数（默认生产用的 E3B 六城包）；
* ``planner="llm"``：生产配置的形态（真实规划器的调用设定与报价、付费开关**关闭**）——只走到运行前检查（含首年请求大小检查）与
  确认开始，确认开始应当被 ``paid_calls_disabled`` 明确拒绝、不扣次数；不建传输、不发任何请求；
* 数据目录在 ``--out`` 下（新目录，不碰真实的服务数据）；不写任何仓库文件；
* 输出 ``summary.json``：用到的资料身份（资料包内容身份与构建身份、协议、提示模板）、本次有效设置、计划与实际分身数、逐城 C / E / M、
  模拟缺失的原因、E1 的结束状态、费用与真实模型调用数（应当是 0）。结果照 E1 原样，不要求“全部 C”。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

ORIGIN = "http://127.0.0.1:8765"
#: 生产形态的联调（只检查、付费开关关闭）用的占位来源：同源 HTTPS，测试客户端里不会真的连到这个域名
PRODUCTION_ORIGIN = "https://hs.example.invalid"
API = "/api/v1"


def run_e2e(
    out: Path,
    *,
    pack_path: str,
    cities: tuple[str, ...] | None,
    clones: int | None,
    years: int | None,
    max_cost: str | None,
    workers: int,
    planner: str = "mock",
) -> dict[str, Any]:
    from fastapi.testclient import TestClient

    from product.fixtures import make_draft
    from product.service.actions import ServiceContext
    from product.service.api import create_app
    from product.service.config import ServiceConfig
    from workflows.service_worker import Worker

    started = time.monotonic()
    from product.pack import load_pack
    from product.service.config import REPO

    # 真实资料包走本地 mock 服务的配置；合成资料包只有测试配置接受（生产与本地模式不用合成资料包）。
    # 真实规划器：生产配置的形态，付费开关关闭（只检查，不发送）
    real = load_pack(REPO / pack_path).body.status == "real_data_frozen"
    llm = planner == "llm"
    origin = PRODUCTION_ORIGIN if llm else ORIGIN
    profile = "production" if llm else ("local_mock" if real else "test")
    cfg = ServiceConfig.model_validate(
        {
            "version": "e3c-service-config-v1",
            "profile": profile,
            "data_dir": str(out / "data"),
            "site_origin": origin,
            "pack_path": pack_path,
            "planner": planner,
            "live_calls_enabled": False,
            "workers": workers,
            "clones_per_city_max": max(4, clones or 4),
            "progress_poll_seconds": 30,
        }
    )
    ctx = ServiceContext.open(cfg)
    worker = Worker(ctx)
    client = TestClient(create_app(ctx), base_url=origin)
    _, secret = ctx.store.create_principal("离线联调")
    session = client.post(
        f"{API}/session", json={"invite_token": secret}, headers={"Origin": origin}
    )
    session.raise_for_status()
    h = {"Origin": origin, "X-CSRF-Token": session.json()["csrf_token"]}

    def call(method: str, path: str, body: Any = None) -> dict[str, Any]:
        r = client.request(method, API + path, json=body, headers=h)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} → {r.status_code} {r.json().get('code')}")
        return r.json()

    chosen = tuple(cities) if cities else tuple(ctx.policy.cities_allowed)
    view = call("POST", "/drafts")
    draft = make_draft(
        ctx.pack,
        draft_id=view["draft"]["draft_id"],
        revision=view["draft"]["revision"],
        cities=chosen,
        **({"abilities": ("edu_at_least_hs",)} if real else {}),
    )
    call(
        "PUT",
        f"/drafts/{draft.draft_id}",
        {"expected_revision": draft.revision, "draft": draft.model_dump(mode="json")},
    )
    review = call("POST", f"/drafts/{draft.draft_id}/review")
    confirmed = call(
        "POST",
        "/profiles/confirm",
        {
            "review_id": review["review"]["review_id"],
            "draft_id": draft.draft_id,
            "draft_revision": review["reviewed_revision"],
            "acknowledged": review["review"]["pending_confirmations"],
        },
    )
    if confirmed["status"] != "confirmed":
        raise RuntimeError(f"确认没有通过：{[i['code'] for i in confirmed['issues']]}")
    options = {
        k: v
        for k, v in (("clones_per_city", clones), ("years", years), ("max_cost_cny", max_cost))
        if v is not None
    }
    intent = call(
        "POST", "/run-intents", {"confirmed_id": confirmed["confirmed"]["confirmed_id"], **options}
    )
    worker.run_pending()
    ready = call("GET", f"/run-intents/{intent['intent_id']}")
    summary: dict[str, Any] = {
        "data_identity": _identity(ctx),
        "service_profile": profile,
        "settings": ready["settings"],
        "preflight_status": (ready.get("preflight") or {}).get("status"),
        "preflight_issues": [i["code"] for i in (ready.get("preflight") or {}).get("issues", [])],
        "stage_after_check": ready["stage"],
    }
    if llm:
        # 付费开关关闭：确认开始应当被明确拒绝，不预留、不扣次数、不换成 mock
        r = client.post(
            f"{API}/run-intents/{intent['intent_id']}/submit",
            json={
                "confirm_with": ready.get("confirm_with"),
                "idempotency_key": "e2e-0001",
                "accept_scope": True,
            },
            headers=h,
        )
        summary.update(
            submit_http=r.status_code,
            submit_code=r.json().get("code"),
            quota=call("GET", "/session")["quota"],
            jobs_pending=len(ctx.store.pending_jobs()),
            real_model_calls=0,
            seconds=round(time.monotonic() - started, 1),
        )
        return summary
    if ready["stage"] != "awaiting_confirmation":
        summary["explanation"] = ready.get("explanation")
        summary["seconds"] = round(time.monotonic() - started, 1)
        return summary
    receipt = call(
        "POST",
        f"/run-intents/{intent['intent_id']}/submit",
        {
            "confirm_with": ready["confirm_with"],
            "idempotency_key": "e2e-0001",
            "accept_scope": True,
        },
    )["receipt"]
    worker.run_pending()
    done = call("GET", f"/run-intents/{intent['intent_id']}")
    summary.update(
        stage=done["stage"],
        quota=done["quota"],
        run_id=receipt["run_id"],
        planned_clones=ready["settings"]["clones_total"],
    )
    if done["result_available"]:
        result = call("GET", f"/runs/{receipt['run_id']}/result")
        progress = call("GET", f"/runs/{receipt['run_id']}/progress")
        summary.update(_result_summary(result, progress))
    else:
        summary["explanation"] = done.get("explanation")
    summary["seconds"] = round(time.monotonic() - started, 1)
    return summary


def _identity(ctx: Any) -> dict[str, Any]:
    b = ctx.pack.body
    report = Path(ctx.repo) / Path(b.e1_base.life_input).parent / "report.json"
    build = None
    if report.is_file():
        build = json.loads(report.read_text(encoding="utf-8")).get("build_identity")
    return {
        "pack_id": b.pack_id,
        "pack_status": b.status,
        "pack_fingerprint": ctx.pack.fingerprint,
        "pack_build_identity": build,
        "protocol_id": b.e1_base.protocol_id,
        "scoring_version": b.e1_base.scoring_version,
        "scoring_content_fingerprint": b.e1_base.scoring_content_fingerprint,
        "prompt_template": b.run.prompt_template,
        "planner": ctx.default_spec.planner_identity.get("planner_id"),
        "schema_version": ctx.meta().schema_version,
    }


def _result_summary(result: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
    by_city: dict[str, Counter] = {}
    reasons: dict[str, Counter] = {}
    for c in result["clones"]:
        by_city.setdefault(c["city_ref"], Counter())[c["category"]] += 1
        if c["category"] == "M":
            reasons.setdefault(c["city_ref"], Counter())[c.get("missing_reason") or "?"] += 1
    labels = {c["city_ref"]: c.get("label") or c["city_ref"] for c in result.get("cities", [])}
    cities = [
        {
            "city_ref": ref,
            "label": labels.get(ref, ref),
            "C": n["C"],
            "E": n["E"],
            "M": n["M"],
            "missing_reasons": dict(reasons.get(ref, {})),
            "years": sorted({len(c["years"]) for c in result["clones"] if c["city_ref"] == ref}),
        }
        for ref, n in sorted(by_city.items())
    ]
    cost = progress["cost"]
    return {
        "actual_clones": len(result["clones"]),
        "run_stage": result["run_stage"],
        "evaluation_status": result["evaluation_status"],
        "cities": cities,
        "totals": dict(Counter(c["category"] for c in result["clones"])),
        "cost": {"kind": cost["kind"], "estimated": cost.get("estimated_cost")},
        "planning_calls": {"used": cost["calls_used"], "limit": cost["call_limit"]},
        "real_model_calls": result["real_model_calls"],
    }
