"""显示附层的服务测试（``GET /api/v1/display``、``GET /api/v1/display/geography``、即时回复的 ``display``）。

* 同一个对象换语言：``source``、摘要、确认 token、答案、次数、账本与规划请求都不变；读取不写库、不续期、不调用模型。
* 中文逐字照原消息；英文只来自冻结资源，做不到就照实报缺失与原因，不挡业务读取。
* 私有绑定随所属对象：他人读不到；删除、过期、草稿改修订与删除全部数据之后的迟到写回都管住绑定。
* 历史对象按它记下的资源版本读，那个版本不存在就报缺失，不回退到当前版本；地理清单同样不回退。
* 服务库从 v2 就地升到 v3：用户内容与次数都在，旧对象显示为没有绑定，读取不补写。

显示资源的登记表与冻结目录换到每个测试自己的临时目录（``publish``），与仓库里登记的版本无关。全部离线。
"""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from common.product_contracts import DISPLAY_SCHEMA_VERSION
from product.display import catalog
from product.display.document import json_fingerprint
from product.display.text import has_cjk, is_user_text
from product.service.display import USER_TEXT
from tests.product_service.support import (
    Browser,
    db_dump,
    files_under,
    finished_run,
    ready_intent,
    start,
)

REASONS = {
    "no_binding",
    "translation_missing",
    "translation_stale",
    "source_changed",
    "ambiguous",
    "untranslated_param",
    "resources_unavailable",
    "binding_stale",
}


@pytest.fixture
def publish(tmp_path, monkeypatch):
    """把登记表与冻结目录换到临时目录；``publish(pack, root=None)`` 冻结源文件（或 ``root`` 下的一份副本）并登记为当前版本。"""
    monkeypatch.setattr(catalog, "FROZEN", tmp_path / "display-frozen")
    monkeypatch.setattr(catalog, "REGISTRY", tmp_path / "display-registry.yaml")

    def run(pack, root=None):
        version = catalog.freeze(pack.fingerprint, pack.body.pack_id, root=root)
        catalog.register(pack.fingerprint, pack.body.pack_id, version)
        return version

    yield run
    catalog._load_frozen.cache_clear()


def display(b, kind, ident=None, locale="zh"):
    params = {"kind": kind, "locale": locale}
    if ident is not None:
        params["id"] = ident
    return b.get("/display", params=params)


def at(doc, pointer):
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        doc = doc[int(part)] if isinstance(doc, list) else doc[part]
    return doc


def check_fields(env, kind):
    """中文逐字照原消息、全部提供；英文提供的没有中文，缺失的有原因；用户原文不进显示文字。"""
    for f in env["fields"]:
        assert not is_user_text(f["pointer"], USER_TEXT.get(kind, ())), f
        if env["locale"] == "zh":
            assert f["status"] == "provided" and f["text"] == at(env["source"], f["pointer"]), f
        elif f["status"] == "provided":
            assert f["text"] and not has_cjk(f["text"]), f
        else:
            assert f["text"] is None and f["missing_reason"] in REASONS, f
    if env["locale"] == "zh":
        assert env["status"] == "complete"
    provided = sum(f["status"] == "provided" for f in env["fields"])
    expected = (
        "complete"
        if provided == len(env["fields"])
        else ("unavailable" if provided == 0 else "partial")
    )
    assert env["status"] == expected


def story(svc, b):
    """一条完整的本地故事，返回每种显示对象的（编号，业务读取的 JSON）。"""
    draft = b.new_draft()
    did = draft["draft"]["draft_id"]
    b.fill(draft)
    review = b.post(f"/drafts/{did}/review").json()
    rid = review["review"]["review_id"]
    conf = b.post(
        "/profiles/confirm",
        json={
            "review_id": rid,
            "draft_id": did,
            "draft_revision": review["reviewed_revision"],
            "acknowledged": review["review"]["pending_confirmations"],
        },
    ).json()
    cid = conf["confirmed"]["confirmed_id"]
    intent = b.post("/run-intents", json={"confirmed_id": cid}).json()
    svc.work()
    view = b.intent(intent["intent_id"])
    assert b.submit(view).status_code == 202
    svc.work()
    done = b.intent(intent["intent_id"])
    run_id = done["run_id"]
    return {
        "capabilities": (None, b.get("/capabilities").json()),
        "session": (None, b.get("/session").json()),
        "draft": (did, b.get(f"/drafts/{did}").json()),
        "review": (rid, review),
        "profile": (cid, b.get(f"/profiles/{cid}").json()),
        "intent": (done["intent_id"], done),
        "progress": (run_id, b.get(f"/runs/{run_id}/progress").json()),
        "result": (run_id, b.get(f"/runs/{run_id}/result").json()),
        "runs": (None, b.get("/runs").json()),
    }


# ---------------------------------------------------------------------- 每种对象、两种语言


def test_every_kind_returns_its_business_source_in_both_languages(tmp_path, publish):
    svc = start(tmp_path)
    version = publish(svc.ctx.pack)
    _, b = svc.person()
    for kind, (ident, business) in story(svc, b).items():
        envs = {}
        for locale in ("zh", "en"):
            r = display(b, kind, ident, locale)
            assert r.status_code == 200, (kind, r.text)
            assert r.headers["cache-control"] == "private, no-store"
            env = envs[locale] = r.json()
            assert (env["kind"], env["id"], env["locale"]) == (kind, ident, locale)
            assert env["display_schema_version"] == DISPLAY_SCHEMA_VERSION
            assert env["source_view_fingerprint"] == json_fingerprint(env["source"])
            if kind == "review":  # 审阅没有单独的读取接口：与审阅回复的内容相同
                assert env["source"]["review"] == business["review"]
                assert env["source"]["reviewed_revision"] == business["reviewed_revision"]
            else:
                assert env["source"] == business, kind
            if env["fields"]:
                assert env["resource_version"] == version
            check_fields(env, kind)
        assert envs["zh"]["source"] == envs["en"]["source"]
        assert envs["zh"]["source_view_fingerprint"] == envs["en"]["source_view_fingerprint"]
        assert [f["pointer"] for f in envs["zh"]["fields"]] == [
            f["pointer"] for f in envs["en"]["fields"]
        ]
        # 当前源文件冻结出的资源：不会有过时或对不上的绑定
        bad = {"translation_stale", "source_changed", "ambiguous", "binding_stale"}
        assert not [f for f in envs["en"]["fields"] if f.get("missing_reason") in bad], kind


def test_switching_language_changes_nothing_in_the_service(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    principal, b = svc.person()
    view = ready_intent(svc, b)
    cid = b.get("/profiles").json()["profiles"][0]["confirmed_id"]
    data = tmp_path / "data"
    before_db, before_files = db_dump(svc.store.path), files_under(data)
    quota = b.quota()
    events = svc.store.quota_events(principal)
    jobs = svc.store.pending_jobs()
    tokens = set()
    for _ in range(2):
        for locale in ("zh", "en", "zh"):
            env = display(b, "intent", view["intent_id"], locale).json()
            tokens.add(env["source"]["confirm_with"])
            assert env["source"]["expires_at"] == view["expires_at"]
            display(b, "profile", cid, locale)
            display(b, "capabilities", None, locale)
            display(b, "runs", None, locale)
        svc.clock.advance(hours=1)
    assert tokens == {view["confirm_with"]}
    assert db_dump(svc.store.path) == before_db  # 读取不写库：不续期、不补写绑定
    assert files_under(data) == before_files
    assert b.quota() == quota and svc.store.quota_events(principal) == events
    assert svc.store.pending_jobs() == jobs  # 没有新的检查或规划请求
    assert b.intent(view["intent_id"])["confirm_with"] == view["confirm_with"]


def test_one_template_renders_each_value_on_its_own(tmp_path, publish):
    texts = {}
    for clones in (1, 2):
        svc = start(tmp_path / f"c{clones}", default_clones_per_city=clones)
        publish(svc.ctx.pack)
        _, b = svc.person()
        for locale in ("zh", "en"):
            env = display(b, "session", None, locale).json()
            texts[clones, locale] = next(
                f["text"] for f in env["fields"] if f["key"] == "rules.default_scale"
            )
    assert "每城 1 个分身" in texts[1, "zh"] and "每城 2 个分身" in texts[2, "zh"]
    assert "1 clone(s) per city" in texts[1, "en"] and "2 clone(s) per city" in texts[2, "en"]


# ---------------------------------------------------------------------- 即时回复


def test_errors_carry_both_languages_even_before_login(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    anonymous = Browser(svc)
    r = anonymous.get("/session")
    assert r.status_code == 401
    body = r.json()
    att = body.pop("display")
    assert att["source_view_fingerprint"] == json_fingerprint(body)
    en = att["translations"]["en"]
    assert en["status"] == "complete"
    texts = {f["pointer"]: f["text"] for f in en["fields"]}
    assert texts["/message_zh"] == (
        "There is no valid session, or your personal link is no longer valid"
    )
    zh = {f["pointer"]: f["text"] for f in att["translations"]["zh"]["fields"]}
    assert zh["/message_zh"] == body["message_zh"]
    # 输入的内容不回显：非法的查询值不出现在任何一种文字里
    _, b = svc.person()
    r = b.get("/display", params={"kind": "secret-input-123", "locale": "en"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"
    for loc in ("zh", "en"):
        for f in r.json()["display"]["translations"][loc]["fields"]:
            assert "secret-input-123" not in (f["text"] or "")


def test_expired_errors_say_what_expired_in_english(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    draft = b.fill(b.new_draft())
    svc.clock.advance(days=31)
    b.reopen()
    for r in (
        b.get(f"/drafts/{draft['draft']['draft_id']}"),
        display(b, "draft", draft["draft"]["draft_id"], "en"),
    ):
        assert r.status_code == 410 and r.json()["code"] == "expired"
        en = {f["pointer"]: f["text"] for f in r.json()["display"]["translations"]["en"]["fields"]}
        assert en["/message_zh"] == "This draft is past its retention period"


def test_a_refused_confirmation_explains_itself_in_english(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    filled = b.fill(b.new_draft())
    did = filled["draft"]["draft_id"]
    review = b.post(f"/drafts/{did}/review").json()
    b.fill(b.get(f"/drafts/{did}").json())  # 审阅之后又存了一版：旧审阅作废
    r = b.post(
        "/profiles/confirm",
        json={
            "review_id": review["review"]["review_id"],
            "draft_id": did,
            "draft_revision": review["reviewed_revision"],
            "acknowledged": review["review"]["pending_confirmations"],
        },
    )
    reply = r.json()
    assert reply["status"] == "refused" and reply["profile_summary"] is None
    att = reply.pop("display")
    assert att["source_view_fingerprint"] == json_fingerprint(reply)
    en = att["translations"]["en"]
    assert en["status"] == "complete", en
    assert any(f["text"].startswith("The review you referred to") for f in en["fields"])
    assert "issue.review_stale.message" in {f["key"] for f in en["fields"]}


# ---------------------------------------------------------------------- 私有：所有权、删除、过期、修订、迟到写回


def test_display_is_private_to_its_owner(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, owner = svc.person()
    objects = story(svc, owner)
    _, stranger = svc.person("另一个人")
    for kind, (ident, _) in objects.items():
        if ident is None:
            continue
        for locale in ("zh", "en"):
            r = display(stranger, kind, ident, locale)
            assert r.status_code == 404 and r.json()["code"] == "not_found", kind
    assert display(stranger, "runs").json()["source"]["runs"] == []
    assert display(Browser(svc), "capabilities").status_code == 401


def test_a_review_display_follows_its_draft(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    filled = b.fill(b.new_draft())
    did = filled["draft"]["draft_id"]
    rid = b.post(f"/drafts/{did}/review").json()["review"]["review_id"]
    assert display(b, "review", rid, "en").status_code == 200
    b.fill(b.get(f"/drafts/{did}").json())  # 草稿改了新修订
    r = display(b, "review", rid, "en")
    assert r.status_code == 409 and r.json()["code"] == "revision_conflict"
    rid2 = b.post(f"/drafts/{did}/review").json()["review"]["review_id"]
    svc.clock.advance(days=31)
    b.reopen()
    r = display(b, "review", rid2, "en")
    assert r.status_code == 410 and r.json()["code"] == "expired"


def test_deletion_takes_the_bindings_with_it(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    principal, b = svc.person()
    objects = story(svc, b)
    run_id = objects["result"][0]
    assert b.delete(f"/runs/{run_id}").status_code == 202
    svc.work()
    for kind in ("result", "progress"):
        business = b.get(f"/runs/{run_id}/{kind}")
        r = display(b, kind, run_id, "en")
        assert (r.status_code, r.json()["code"]) == (
            business.status_code,
            business.json()["code"],
        )
    assert b.delete("/me/data").status_code in (200, 202)
    svc.work()
    business = {
        "draft": "/drafts/{}",
        "profile": "/profiles/{}",
        "intent": "/run-intents/{}",
        "progress": "/runs/{}/progress",
        "result": "/runs/{}/result",
    }
    for kind, (ident, _) in objects.items():
        if ident is None:
            continue
        r = display(b, kind, ident, "en")
        if kind == "review":  # 审阅随草稿删掉
            assert r.status_code == 404, r.text
            continue
        same = b.get(business[kind].format(ident))
        assert r.status_code == same.status_code, kind  # 与业务读取的回答相同
        if r.status_code == 200:  # 意图留下删除回执：正文已清掉，显示的也只是回执
            assert r.json()["source"] == same.json()
    con = sqlite3.connect(svc.store.path)
    try:
        for table, cols in (
            ("reviews", ("display_body",)),
            ("profiles", ("display_body",)),
            (
                "intents",
                ("preflight_display", "progress_display", "result_display", "explanation_display"),
            ),
        ):
            for col in cols:
                left = con.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE principal_id = ? AND {col} IS NOT NULL",
                    (principal,),
                ).fetchone()[0]
                assert left == 0, (table, col)
    finally:
        con.close()
    workflow = [p for p in files_under(tmp_path / "data") if "/display/" in p]
    assert workflow == []  # 工作目录里的显示绑定随运行一起删掉


def test_a_late_write_after_delete_all_does_not_bring_bindings_back(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    intent = b.prepare()  # 检查任务还在排队
    assert b.delete("/me/data").status_code in (200, 202)
    svc.work()  # 检查这时才跑完：结果与绑定都不能写回
    row = svc.store.intent_row(intent["intent_id"])
    assert row["preflight_body"] is None and row["preflight_display"] is None
    assert [p for p in files_under(tmp_path / "data") if "/display/" in p] == []


# ---------------------------------------------------------------------- 历史版本与缺失版本


def _copy_source_with_marked_english(dst):
    """当前源文件的一份副本，服务文字的英文都加上 ``[v2]``（新版本的资源）。"""
    shutil.copytree(catalog.SOURCE, dst)
    path = dst / "templates" / "service.yaml"
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for entry in data["templates"].values():
        if entry.get("en"):
            entry["en"] += " [v2]"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8", newline="\n")


def _paused(svc, b):
    view = ready_intent(svc, b)
    assert b.submit(view).status_code == 202
    svc.worker.faults = {"life:halt_at_call:3:service_unavailable"}
    svc.work()
    svc.worker.faults = None
    paused = b.intent(view["intent_id"])
    assert paused["explanation"] is not None and "resume" in paused["allowed_actions"]
    return paused


def test_historical_objects_read_the_resources_they_were_made_with(tmp_path, publish):
    svc = start(tmp_path)
    v1 = publish(svc.ctx.pack)
    _, b = svc.person()
    paused = _paused(svc, b)  # 停下的说明在 v1 时生成并保存绑定
    _copy_source_with_marked_english(tmp_path / "src-v2")
    v2 = publish(svc.ctx.pack, root=tmp_path / "src-v2")
    assert v2 != v1 and catalog.current_version(svc.ctx.pack.fingerprint) == v2
    env = display(b, "intent", paused["intent_id"], "en").json()
    stored = [f for f in env["fields"] if f["pointer"].startswith("/explanation/")]
    live = [
        f
        for f in env["fields"]
        if not f["pointer"].startswith(("/explanation/", "/preflight/", "/progress/"))
    ]
    assert stored and all(f.get("resource_version") == v1 for f in stored if f["text"])
    assert all("[v2]" not in f["text"] for f in stored if f["text"])
    assert any(f["text"] and f["text"].endswith("[v2]") for f in live)  # 当场生成的部分用当前版本
    # 那个版本不存在了：照实报缺失，不回退到当前版本；业务读取不受影响
    shutil.rmtree(catalog.FROZEN / svc.ctx.pack.fingerprint / v1)
    catalog._load_frozen.cache_clear()
    env = display(b, "intent", paused["intent_id"], "en").json()
    stored = [f for f in env["fields"] if f["pointer"].startswith("/explanation/")]
    assert stored and all(
        f["status"] == "missing" and f["missing_reason"] == "resources_unavailable" for f in stored
    )
    assert env["status"] == "partial"
    assert b.get(f"/run-intents/{paused['intent_id']}").status_code == 200
    zh = display(b, "intent", paused["intent_id"], "zh").json()
    assert zh["status"] == "complete"  # 中文照原消息，不受资源版本影响


def test_without_registered_resources_business_reads_still_work(tmp_path, publish):
    svc = start(tmp_path)  # 没有登记任何显示资源
    _, b = svc.person()
    env = display(b, "capabilities", None, "en").json()
    assert env["resource_version"] is None and env["status"] == "unavailable"
    reasons = {f["missing_reason"] for f in env["fields"]}
    assert "resources_unavailable" in reasons and reasons <= {"resources_unavailable", "no_binding"}
    assert display(b, "capabilities", None, "zh").json()["status"] == "complete"
    assert b.get("/capabilities").status_code == 200


# ---------------------------------------------------------------------- 地理清单


def _with_geography(dst, pack_id, cities):
    shutil.copytree(catalog.SOURCE, dst)
    geo = dst / "geography"
    shutil.rmtree(geo, ignore_errors=True)
    if cities is not None:
        import json

        geo.mkdir()
        body = {
            "status": "complete",
            "cities": cities,
            "sources": [],
            "notes": [
                {"zh": "合成城市没有现实位置", "en": "Synthetic cities have no real location"}
            ],
        }
        (geo / f"{pack_id}.json").write_text(
            json.dumps(body, ensure_ascii=False), encoding="utf-8", newline="\n"
        )


def _synthetic_city(ref):
    text = {"zh": "合成", "en": "Synthetic"}
    return {
        "city_ref": ref,
        "country": "ZZ",
        "scheme": "synthetic",
        "code": ref,
        "geography_version": "synthetic-v2",
        "country_names": {"zh": "合成国家", "en": "Synthetic country"},
        "names": {
            "zh": {"official": "合成城市", "short": "合成"},
            "en": {"official": "Synthetic city", "short": "Synthetic"},
        },
        "model_kind": "synthetic",
        "anchor_status": "not_applicable",
        "anchor": None,
        "support_scope": {
            "ref": "synthetic",
            "version": "v2",
            "residence": text,
            "work": text,
            "note": text,
        },
        "coverage_note": text,
    }


def test_geography_reads_the_requested_version_and_never_falls_back(tmp_path, publish):
    svc = start(tmp_path)
    pack = svc.ctx.pack
    _, b = svc.person()

    def geo(**params):
        return b.get("/display/geography", params={"pack_fingerprint": pack.fingerprint, **params})

    assert geo().status_code == 404  # 资料包没有登记
    _with_geography(tmp_path / "no-geo", pack.body.pack_id, None)
    v1 = publish(pack, root=tmp_path / "no-geo")
    r = geo()
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-store"
    assert r.json()["status"] == "unavailable" and r.json()["cities"] == []
    refs = [c.city_ref for c in pack.body.cities]
    _with_geography(tmp_path / "geo", pack.body.pack_id, [_synthetic_city(r) for r in refs])
    v2 = publish(pack, root=tmp_path / "geo")
    now = geo().json()
    assert now["resource_version"] == v2 and now["status"] == "complete"
    assert all(
        c["anchor"] is None and c["anchor_status"] == "not_applicable" for c in now["cities"]
    )
    old = geo(resource_version=v1).json()
    assert old["resource_version"] == v1 and old["status"] == "unavailable"  # 历史版本照旧
    missing = geo(resource_version="e3-display-res-v1-0000000000000000")
    assert missing.status_code == 404  # 明确请求的版本不存在：不回退到当前版本
    assert geo(resource_version="latest").status_code == 422
    other = b.get("/display/geography", params={"pack_fingerprint": "0" * 64})
    assert other.status_code == 404


# ---------------------------------------------------------------------- 结果色阶


def test_results_carry_the_fixed_colour_scale(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    done = finished_run(svc, b)
    for locale in ("zh", "en"):
        env = display(b, "result", done["run_id"], locale).json()
        (spec,) = env["metrics"]
        assert (spec["key"], spec["minimum"], spec["maximum"]) == ("mean_j", "0", "1")
        assert spec["denominator_field"] == "mean_denominator"
        assert spec["comparison_scope"] == "within_result_only"
        assert spec["direction"] == "higher_model_evaluation"
        assert [r.split(":", 1)[0] for r in spec["evaluation_refs"]] == [
            "protocol",
            "scoring",
            "evaluation",
        ]
        notes = " ".join(n["en"] for n in spec["notes"])
        assert "not a confidence interval" in notes and "70% happy" in notes
        assert all(n["zh"] and not has_cjk(n["en"]) for n in spec["notes"])
    assert display(b, "intent", done["intent_id"], "en").json()["metrics"] == []


# ---------------------------------------------------------------------- 服务库升级


V3_COLUMNS = {
    "reviews": ("display_body",),
    "profiles": ("display_body",),
    "intents": ("preflight_display", "progress_display", "result_display", "explanation_display"),
}


def _downgrade_to_v2(path):
    """把 v3 服务库改回 v2 的样子：v2 与 v3 的差别只有这几列显示绑定（与 8ccec1c 的建表语句逐列核对过）。"""
    con = sqlite3.connect(path)
    try:
        for table, cols in V3_COLUMNS.items():
            for col in cols:
                con.execute(f"ALTER TABLE {table} DROP COLUMN {col}")
        con.execute("UPDATE meta SET value = 'e3c-service-store-v2' WHERE key = 'store_version'")
        con.commit()
    finally:
        con.close()


def test_a_v2_store_is_upgraded_in_place_and_keeps_users_and_quota(tmp_path, publish):
    from product.service.store import _MIGRATE_V3

    assert {k: tuple(v) for k, v in _MIGRATE_V3.items()} == V3_COLUMNS
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    principal, b = svc.person()
    done = finished_run(svc, b)
    token = b.token
    _downgrade_to_v2(svc.store.path)
    before = db_dump(svc.store.path)
    again = start(tmp_path)  # 同一个数据目录：打开时就地升级
    after = db_dump(again.store.path)
    assert after["meta"] != before["meta"]
    assert {k: v for k, v in after.items() if k not in ("meta", *V3_COLUMNS)} == {
        k: v for k, v in before.items() if k not in ("meta", *V3_COLUMNS)
    }
    for table in V3_COLUMNS:  # 原有的列逐行不变，新列都是空的
        assert [r[: len(before[table][0])] for r in after[table]] == before[table]
    nb = Browser(again)
    assert nb.login(token).status_code == 200
    assert nb.quota() == {"total": 3, "used": 1, "reserved": 0, "available": 2}
    assert nb.get(f"/runs/{done['run_id']}/result").status_code == 200
    snapshot = db_dump(again.store.path)
    env = display(nb, "result", done["run_id"], "en").json()
    assert env["status"] == "unavailable"
    assert {f["missing_reason"] for f in env["fields"]} == {"no_binding"}
    sessions = {k: v for k, v in db_dump(again.store.path).items() if k != "sessions"}
    assert sessions == {k: v for k, v in snapshot.items() if k != "sessions"}  # 读取不补写绑定
    assert principal in {r[0] for r in after["principals"]}


def test_expiry_clears_the_bindings_with_the_content(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    principal, b = svc.person()
    done = finished_run(svc, b)
    row = svc.store.intent_row(done["intent_id"])
    assert row["result_display"] is not None and row["preflight_display"] is not None
    svc.clock.advance(days=31)
    svc.worker.maintain()
    row = svc.store.intent_row(done["intent_id"])
    assert row["stage"] == "expired"
    assert all(row[c] is None for c in V3_COLUMNS["intents"])
    con = sqlite3.connect(svc.store.path)
    try:
        for table in ("reviews", "profiles"):
            left = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE principal_id = ? AND display_body IS NOT NULL",
                (principal,),
            ).fetchone()[0]
            assert left == 0, table
    finally:
        con.close()
    b.reopen()
    r = display(b, "result", done["run_id"], "en")
    assert r.status_code == 410 and r.json()["code"] == "expired"


def test_recognition_aliases_stay_verbatim(tmp_path, publish):
    """兴趣与菜系的识别别名是用户要输入的词：不翻译、不算覆盖，与类别名同字的别名也不借类别名的英文。"""
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    env = display(b, "capabilities", None, "en").json()
    assert not [f for f in env["fields"] if "/aliases/" in f["pointer"]]
    interests = env["source"]["interests"]
    assert interests["leisure_categories"][0]["aliases"] and interests["cuisines"][0]["aliases"]
    labels = [f for f in env["fields"] if f["pointer"].endswith("/label")]
    assert labels  # 类别名本身照常有显示文字
