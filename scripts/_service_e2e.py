"""托管服务的离线联调（``scripts/product_service.py e2e`` 调用）：一个合成人物沿真实的服务路径走完一次模拟——HTTP（会话、CSRF、
草稿、审阅、确认、运行意图、确认开始）→ 业务函数 → worker → LangGraph → E1 → 产品结果。

* 规划器默认是离线 mock（零网络、零费用，不代表真实模型）；人物是合成的 u501（文员）；资料包按参数（默认生产用的 E3B 六城包）；
  资料包配了输入目录时按新流程填写（v3.4：职业从目录直接选、最高学历“高中”、证书明确“没有”、没有硬要求与属性偏好），没有目录时
  用兼容的文字描述；
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
        **_answers(ctx.pack, real),
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
        "input": {
            "occupation_mode": draft.occupation.mode,
            "occupation": draft.occupation.selected_candidate,
            "education": draft.education.option_id,
            "ability_claims": [c.ability_id for c in draft.abilities],
            "hard_requirements": len(draft.hard_requirements),
            "attribute_preferences": len(draft.attribute_preferences),
            "abilities_confirmed": list(confirmed["confirmed"]["abilities"]),
        },
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
    ids = {
        "draft": draft.draft_id,
        "review": review["review"]["review_id"],
        "profile": confirmed["confirmed"]["confirmed_id"],
        "intent": intent["intent_id"],
        "progress": receipt["run_id"],
        "result": receipt["run_id"] if done["result_available"] else None,
    }
    summary["display"] = _display_story(client, h, ctx, ids, review)
    summary["deletion"] = _delete_story(client, h, worker, receipt["run_id"])
    summary["seconds"] = round(time.monotonic() - started, 1)
    return summary


#: 每种显示对象对应的业务读取（审阅没有单独的读取接口，与审阅回复比较）
BUSINESS = {
    "capabilities": "/capabilities",
    "session": "/session",
    "runs": "/runs",
    "draft": "/drafts/{}",
    "profile": "/profiles/{}",
    "intent": "/run-intents/{}",
    "progress": "/runs/{}/progress",
    "result": "/runs/{}/result",
}


def _display_story(
    client: Any, h: dict[str, str], ctx: Any, ids: dict[str, str | None], review: dict[str, Any]
) -> dict[str, Any]:
    """每种对象中英各读一次显示出口：中文是否完整、英文提供与缺失（按原因与 JSON Pointer 模式；用户原文不在分母里）、两种语言的
    ``source`` 与摘要是否相同、``source`` 是否就是业务读取的回复；再读地理清单与一个未登录的错误。"""
    from product.display.text import pointer_pattern

    kinds: dict[str, Any] = {}
    for kind in (
        "capabilities",
        "session",
        "draft",
        "review",
        "profile",
        "intent",
        "progress",
        "result",
        "runs",
    ):
        ident = ids.get(kind)
        if kind not in ("capabilities", "session", "runs") and ident is None:
            continue
        envs = {}
        for locale in ("zh", "en"):
            params = {"kind": kind, "locale": locale, **({"id": ident} if ident else {})}
            r = client.get(f"{API}/display", params=params, headers=h)
            if r.status_code != 200:
                raise RuntimeError(
                    f"显示读取 {kind}/{locale} → {r.status_code} {r.json().get('code')}"
                )
            envs[locale] = r.json()
        if kind == "review":
            business_same = envs["zh"]["source"]["review"] == review["review"]
        else:
            path = BUSINESS[kind].format(ident)
            business_same = client.get(API + path, headers=h).json() == envs["zh"]["source"]
        en = envs["en"]["fields"]
        by_reason: dict[str, int] = {}
        by_pattern: dict[str, dict[str, int]] = {}
        for f in en:
            if f["status"] == "missing":
                reason = f["missing_reason"]
                by_reason[reason] = by_reason.get(reason, 0) + 1
                slot = by_pattern.setdefault(pointer_pattern(f["pointer"]), {})
                slot[reason] = slot.get(reason, 0) + 1
        kinds[kind] = {
            "zh_status": envs["zh"]["status"],
            "en_status": envs["en"]["status"],
            "displayable": len(en),
            "en_provided": sum(f["status"] == "provided" for f in en),
            "en_missing_by_reason": dict(sorted(by_reason.items())),
            "en_missing_by_pattern": dict(sorted(by_pattern.items())),
            "resource_version": envs["en"]["resource_version"],
            "same_source_both_locales": envs["zh"]["source"] == envs["en"]["source"]
            and envs["zh"]["source_view_fingerprint"] == envs["en"]["source_view_fingerprint"],
            "source_is_business_reply": business_same,
        }
    geo = client.get(
        f"{API}/display/geography", params={"pack_fingerprint": ctx.pack.fingerprint}, headers=h
    )
    geography: dict[str, Any] = {"http": geo.status_code}
    if geo.status_code == 200:
        body = geo.json()
        anchors: dict[str, int] = {}
        for c in body["cities"]:
            anchors[c["anchor_status"]] = anchors.get(c["anchor_status"], 0) + 1
        geography.update(
            status=body["status"],
            resource_version=body["resource_version"],
            cities=len(body["cities"]),
            anchor_status=anchors,
        )
    from fastapi.testclient import TestClient

    anonymous = TestClient(client.app, base_url=str(client.base_url))
    err = anonymous.get(f"{API}/session").json()
    return {
        "kinds": kinds,
        "geography": geography,
        "unauthenticated_error": {
            "code": err["code"],
            "zh_status": err["display"]["translations"]["zh"]["status"],
            "en_status": err["display"]["translations"]["en"]["status"],
        },
    }


def _delete_story(client: Any, h: dict[str, str], worker: Any, run_id: str) -> dict[str, Any]:
    """删除这次运行、再删除全部数据：记下每一步的 HTTP 回答与之后读取的回答。"""
    out: dict[str, Any] = {}
    r = client.delete(f"{API}/runs/{run_id}", headers=h)
    out["delete_run_http"] = r.status_code
    worker.run_pending()
    after = client.get(f"{API}/runs/{run_id}/result", headers=h)
    out["result_after_delete"] = [after.status_code, after.json().get("code")]
    shown = client.get(
        f"{API}/display", params={"kind": "result", "id": run_id, "locale": "en"}, headers=h
    )
    out["result_display_after_delete"] = [shown.status_code, shown.json().get("code")]
    r = client.delete(f"{API}/me/data", headers=h)
    out["delete_all_http"] = r.status_code
    worker.run_pending()
    out["runs_after_delete_all"] = len(client.get(f"{API}/runs", headers=h).json().get("runs", []))
    return out


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


def _answers(pack: Any, real: bool) -> dict[str, Any]:
    """草稿的答案（合成文员 u501）：资料包配了输入目录就按新流程（目录选职业、学历题、证书题）；没有就用兼容的文字描述。"""
    from product.input_catalog import input_catalog

    catalog = input_catalog(pack)
    if catalog is None:
        return {"abilities": ("edu_at_least_hs",)} if real else {}
    answers: dict[str, Any] = {
        "occupation_mode": "catalog",
        "occupation_text": None,
        "selected": "occ.office_clerk",
        "certificates": (),
    }
    if catalog.decl.sections.education:
        answers["education"] = "high_school"
    return answers
