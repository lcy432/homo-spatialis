"""新输入流程走托管服务（DECISIONS/0046）：能力清单里的输入目录、不写任何文字的完整离线闭环、拒绝的情形与旧草稿的显式迁移。

* 能力清单带输入目录（E3B 9 组 40 个职业）；中英文显示完整，搜索词照原文。
* 按目录选职业、回答学历与证书、面板多选兴趣与口味、不写补充文字：审阅 → 确认 → 检查 → 开始 → worker → LangGraph → E1 → 结果，
  离线 mock，真实模型调用 0；新画像没有硬要求与属性偏好。
* 按目录选却带文字 → 422；目录里没有的编号、过期的目录 → 审阅阻断，不替换职业。
* 旧草稿：读取时附迁移说明；没有同意就 409、草稿不变；同意后清空并换成当前版本，其他表不动；已经是当前版本就 409。
"""

from __future__ import annotations

import pytest

from common.product_contracts import UserInputDraft
from product.display import catalog
from product.fixtures import make_draft
from product.input_catalog import input_catalog
from tests.product_service.support import E3B, db_dump, finished_run, start
from tests.product_service.test_display_service import check_fields, display

OLD_META = {
    "schema_version": "e3a-product-schema-v3.3",
    "questionnaire_version": "e3a-questionnaire-v0.3-draft",
    "vocabulary_version": "e3a-vocabulary-v4",
    "conversion_version": "e3a-conversion-v2",
}
COMMUTE = {
    "requirement_id": "hr.commute",
    "option_id": "housing.max_commute_roundtrip",
    "parameter_value": 60,
}


@pytest.fixture
def publish(tmp_path, monkeypatch):
    """登记表与冻结目录换到这个测试的临时目录（与 ``test_display_service`` 同样的夹具）。"""
    monkeypatch.setattr(catalog, "FROZEN", tmp_path / "display-frozen")
    monkeypatch.setattr(catalog, "REGISTRY", tmp_path / "display-registry.yaml")

    def run(pack, root=None):
        version = catalog.freeze(pack.fingerprint, pack.body.pack_id, root=root)
        catalog.register(pack.fingerprint, pack.body.pack_id, version)
        return version

    yield run
    catalog._load_frozen.cache_clear()


def _catalog_answers(svc, **over):
    """新流程的答案：按目录选办公室文员、高中、没有证书、兴趣与口味面板多选、不写任何文字。"""
    return {
        "cities": tuple(svc.ctx.policy.cities_allowed)[:2],
        "occupation_mode": "catalog",
        "occupation_text": None,
        "selected": "occ.office_clerk",
        "abilities": (),
        "education": "high_school",
        "certificates": (),
        "free_text": None,
        "leisure": {"state": "selected", "selected": ("fitness",)},
        "taste": {"state": "selected", "selected": ("chinese", "home_style")},
    } | over


def _put(b, view, draft):
    d = view["draft"]
    return b.put(
        f"/drafts/{d['draft_id']}",
        json={"expected_revision": d["revision"], "draft": draft.model_dump(mode="json")},
    )


def test_capabilities_publish_the_input_catalog(tmp_path, publish):
    for pack_path, groups, count in ((None, 3, 6), (E3B, 9, 40)):
        svc = start(tmp_path / str(count), **({"pack_path": pack_path} if pack_path else {}))
        publish(svc.ctx.pack)
        _, b = svc.person()
        cat = b.get("/capabilities").json()["input_catalog"]
        assert (len(cat["groups"]), len(cat["occupations"])) == (groups, count)
        assert cat["content_fingerprint"] == input_catalog(svc.ctx.pack).fingerprint
        env = display(b, "capabilities", None, "en").json()
        check_fields(env, "capabilities")
        shown = [f for f in env["fields"] if f["pointer"].startswith("/input_catalog/")]
        assert shown and all(f["status"] == "provided" for f in shown)
        assert not [
            f for f in shown if "/search_terms/" in f["pointer"] or "/terms/" in f["pointer"]
        ]


def test_the_new_flow_needs_no_text_and_runs_offline(tmp_path, publish):
    svc = start(tmp_path, pack_path=E3B, workers=4)
    publish(svc.ctx.pack)
    _, b = svc.person()
    view = b.fill(b.new_draft(), **_catalog_answers(svc))
    draft = view["draft"]
    assert draft["occupation"]["raw_text"] is None and draft["free_text"]["text"] is None
    assert draft["leisure_interest"]["text"] is None and draft["taste"]["text"] is None
    assert draft["hard_requirements"] == [] and draft["attribute_preferences"] == []
    assert view["migration"] is None
    did = draft["draft_id"]
    review = b.post(f"/drafts/{did}/review").json()["review"]
    assert review["confirmable"], review["issues"]
    assert review["occupation"]["status"] == "catalog_selected"
    assert review["abilities"] == ["edu_at_least_hs"]
    assert review["qualifications"]["requirements"] == [
        {"ability_id": "edu_at_least_hs", "label": "高中毕业或同等学力", "met": True}
    ]
    zh, en = (display(b, "review", review["review_id"], loc).json() for loc in ("zh", "en"))
    assert zh["source"] == en["source"]  # 换语言不改答案与审阅
    check_fields(en, "review")
    assert en["status"] == "complete"
    confirmed = b.confirm(view)
    assert confirmed["occupation"]["raw_text"] is None
    assert confirmed["hard_requirements"] == [] and confirmed["retained"] == []
    assert confirmed["interests"]["leisure_categories"] == ["fitness"]
    intent = b.post("/run-intents", json={"confirmed_id": confirmed["confirmed_id"]}).json()
    svc.work()
    ready = b.intent(intent["intent_id"])
    assert ready["stage"] == "awaiting_confirmation", ready
    assert b.submit(ready).status_code == 202
    svc.work()
    done = b.intent(intent["intent_id"])
    assert done["stage"] == "finished" and done["result_available"]
    result = b.get(f"/runs/{done['run_id']}/result").json()
    assert result["real_model_calls"] == 0
    assert len(result["clones"]) == 2 and all(len(c["years"]) == 3 for c in result["clones"])
    assert all(c["hard_requirements"] == [] for c in result["clones"])


def test_refusals_over_http(tmp_path):
    svc = start(tmp_path, pack_path=E3B)
    _, b = svc.person()
    view = b.new_draft()
    good = make_draft(
        svc.ctx.pack, draft_id=view["draft"]["draft_id"], revision=1, **_catalog_answers(svc)
    )
    body = good.model_dump(mode="json")
    body["occupation"]["raw_text"] = "我是程序员"  # 按目录选却带着搜索框里的字
    r = b.put(f"/drafts/{view['draft']['draft_id']}", json={"expected_revision": 1, "draft": body})
    assert r.status_code == 422 and r.json()["code"] == "invalid_request"
    for update, code in (
        ({"selected_candidate": "occ.teacher"}, "occupation_selection_invalid"),
        ({"catalog_fingerprint": "0" * 64}, "occupation_catalog_expired"),
    ):
        current = b.get(f"/drafts/{view['draft']['draft_id']}").json()
        draft = good.model_copy(
            update={
                "revision": current["draft"]["revision"],
                "occupation": good.occupation.model_copy(update=update),
            }
        )
        assert _put(b, current, draft).status_code == 200
        review = b.post(f"/drafts/{view['draft']['draft_id']}/review").json()["review"]
        assert code in [i["code"] for i in review["issues"]] and not review["confirmable"]
        assert review["occupation"]["selected"] is None


def _old_draft(svc, view, **kw):
    draft = make_draft(
        svc.ctx.pack,
        draft_id=view["draft"]["draft_id"],
        revision=view["draft"]["revision"],
        hard_requirements=kw.pop("hard_requirements", (COMMUTE,)),
        **kw,
    )
    body = draft.model_dump(mode="json")
    body["meta"].update(OLD_META)
    return UserInputDraft.model_validate(body)


def test_an_old_draft_is_migrated_only_with_consent(tmp_path, publish):
    svc = start(tmp_path)
    publish(svc.ctx.pack)
    _, b = svc.person()
    finished_run(svc, b)  # 已经确认、已经完成的历史运行：迁移别的草稿不改它们
    view = b.new_draft()
    did = view["draft"]["draft_id"]
    assert _put(b, view, _old_draft(svc, view)).status_code == 200
    got = b.get(f"/drafts/{did}").json()
    m = got["migration"]
    assert m["consent_required"] and m["version_changed"]
    assert [(r["section"], r["ref"]) for r in m["removed"]] == [("hard_requirements", "hr.commute")]
    review = b.post(f"/drafts/{did}/review").json()["review"]
    assert not review["confirmable"]
    assert "removed_section_present" in [i["code"] for i in review["issues"]]
    en = display(b, "draft", did, "en").json()
    notes = [f for f in en["fields"] if f["pointer"].startswith("/migration/")]
    assert notes and all(f["status"] == "provided" for f in notes)
    before = db_dump(svc.store.path)
    r = b.post(f"/drafts/{did}/migrate", json={"expected_revision": got["draft"]["revision"]})
    assert r.status_code == 409 and r.json()["code"] == "migration_consent_required"
    assert db_dump(svc.store.path) == before  # 没有同意：什么都没改
    r = b.post(
        f"/drafts/{did}/migrate",
        json={"expected_revision": got["draft"]["revision"] - 1, "clear_removed": True},
    )
    assert r.status_code == 409 and r.json()["code"] == "revision_conflict"
    r = b.post(
        f"/drafts/{did}/migrate",
        json={"expected_revision": got["draft"]["revision"], "clear_removed": True},
    )
    assert r.status_code == 200, r.text
    moved = r.json()
    assert moved["draft"]["revision"] == got["draft"]["revision"] + 1
    assert moved["draft"]["hard_requirements"] == [] and moved["migration"] is None
    assert moved["draft"]["meta"]["questionnaire_version"] == "e3a-questionnaire-v0.4-draft"
    after = db_dump(svc.store.path)
    for table in ("profiles", "intents"):
        assert after.get(table) == before.get(table), table  # 历史画像与运行不改写
    review = b.post(f"/drafts/{did}/review").json()["review"]
    assert review["confirmable"], review["issues"]
    r = b.post(
        f"/drafts/{did}/migrate",
        json={"expected_revision": moved["draft"]["revision"], "clear_removed": True},
    )
    assert r.status_code == 409 and r.json()["code"] == "migration_not_needed"
