"""旧版（v3.3）对象在新版里的读回（DECISIONS/0046 的兼容读取；E3 输入收尾独立复核 R1）。

用的是旧版代码真实写下的对象（``tests/fixtures/v33_objects/``，见那里的说明），不是新版对象改版本号、重算身份做出来的：

* 读回之后照原样序列化：JSON、确认画像的内容身份、草稿指纹、显示绑定的消息摘要都与当初相同（不补 v3.4 的空字段、不重签）；
* 工作流照旧能读运行里保存的旧画像；真正改过的旧画像仍被拒绝；
* 旧画像与旧审阅的英文按原来的绑定与原来的资源版本显示；那个版本没了或被改过，照实报缺失，不换成最新译文；
* v3.3 的对象不能带 v3.4 才有的内容；旧画像不能直接准备新模拟（新问卷规则照旧），旧草稿按显式迁移进入新问卷。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from common.product_contracts import (
    LEGACY_SCHEMA_VERSION,
    PRODUCT_SCHEMA_VERSION,
    ConfirmedProfile,
    ProductPreflight,
    ProductResult,
    ProfileReview,
    UserInputDraft,
    content_fingerprint,
)
from product.answers import (
    confirm_profile,
    confirmation_problems,
    draft_fingerprint,
    review_draft,
)
from product.display import catalog, messages
from product.display.document import binding_set, json_fingerprint
from product.display.render import display_fields
from product.fixtures import make_draft
from product.migration import migrate, migration_for
from product.pack import load_pack

REPO = Path(__file__).resolve().parents[2]
OLD = REPO / "tests/fixtures/v33_objects"
E3A = REPO / "configs/product/e3a_synthetic_pack.yaml"
OLD_RESOURCES = "e3-display-res-v1-a7813e49b0e3fbdc"


def raw(name: str) -> dict:
    body = json.loads((OLD / f"{name}.json").read_text(encoding="utf-8"))
    return body.get("payload", body)


@pytest.fixture(scope="module")
def pack():
    return load_pack(E3A)


# ---------------------------------------------------------------------- JSON 与身份


@pytest.mark.parametrize(
    ("name", "cls"),
    [
        ("draft", UserInputDraft),
        ("review", ProfileReview),
        ("profile", ConfirmedProfile),
        ("sample_04_normal_confirmed", ConfirmedProfile),
    ],
)
def test_real_old_objects_serialize_exactly_as_they_were_written(name, cls):
    body = raw(name)
    obj = cls.model_validate(body)
    assert obj.meta.schema_version == LEGACY_SCHEMA_VERSION
    assert obj.model_dump(mode="json") == body  # 不补 v3.4 的字段
    assert json.loads(obj.model_dump_json()) == body


@pytest.mark.parametrize(
    ("name", "cls", "field"),
    [
        ("sample_05_normal_preflight", ProductPreflight, "preflight_fingerprint"),
        ("sample_10_normal_result", ProductResult, "result_fingerprint"),
    ],
)
def test_real_old_preflight_and_result_keep_their_identity(name, cls, field):
    """已开始的旧运行靠预检查的身份找回自己的输入；结果是历史对象。两者的结构 v3.4 没有改，读回照原样。"""
    body = raw(name)
    obj = cls.model_validate(body)
    assert obj.model_dump(mode="json") == body
    assert obj.expected_fingerprint() == body[field]


@pytest.mark.parametrize("name", ["profile", "sample_04_normal_confirmed"])
def test_real_old_profile_keeps_its_original_identity(name):
    body = raw(name)
    stored = body["content_fingerprint"]
    content = {k: v for k, v in body.items() if k != "content_fingerprint"}
    content["meta"] = {k: v for k, v in content["meta"].items() if k != "sample"}
    assert content_fingerprint(content) == stored  # 文件本身没有被重签
    profile = ConfirmedProfile.model_validate(body)
    assert profile.expected_fingerprint() == stored
    assert "qualifications" not in profile.content()
    assert "catalog_fingerprint" not in profile.content()["occupation"]


def test_real_old_draft_keeps_the_fingerprint_its_review_and_profile_recorded():
    draft = UserInputDraft.model_validate(raw("draft"))
    fp = draft_fingerprint(draft)
    assert (
        fp
        == raw("review")["draft"]["draft_fingerprint"]
        == raw("profile")["draft"]["draft_fingerprint"]
    )


def test_workflow_reads_the_real_old_profile_and_refuses_a_tampered_one(tmp_path):
    from workflows.e3_adapters import E3Context, E3Workspace
    from workflows.state import ContentRef

    body = raw("profile")
    ws = E3Workspace.prepare(tmp_path / "ws")
    target = ws.root / "workflow/confirmed" / f"{body['confirmed_id']}.json"
    target.parent.mkdir(parents=True)
    ctx = E3Context(ws)
    ref = ContentRef(
        kind="confirmed_profile",
        ref_id=body["confirmed_id"],
        fingerprint=body["content_fingerprint"],
    )
    target.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    with ws.cwd():
        assert ctx.confirmed(ref) is not None
    forged = {**body, "age_years": body["age_years"] + 1}  # 改了内容、留着旧身份
    target.write_text(json.dumps(forged, ensure_ascii=False), encoding="utf-8")
    with ws.cwd():
        assert ctx.confirmed(ref) is None


def test_a_tampered_old_profile_is_reported_as_tampered(pack):
    forged = ConfirmedProfile.model_validate({**raw("profile"), "age_years": 99})
    codes = {i.code for i in confirmation_problems(pack, forged)}
    assert "confirmation_tampered" in codes


def test_an_old_profile_cannot_prepare_a_new_run_but_is_not_called_tampered(pack):
    old = ConfirmedProfile.model_validate(raw("profile"))
    codes = {i.code for i in confirmation_problems(pack, old)}
    assert "version_changed" in codes and "confirmation_tampered" not in codes


# ---------------------------------------------------------------------- v3.3 不带 v3.4 的内容


@pytest.mark.parametrize(
    ("name", "cls", "edit"),
    [
        (
            "draft",
            UserInputDraft,
            lambda b: b.update(education={"state": "provided", "option_id": "bachelor"}),
        ),
        (
            "draft",
            UserInputDraft,
            lambda b: b.update(certificates={"state": "provided", "selected": []}),
        ),
        ("draft", UserInputDraft, lambda b: b["occupation"].update(mode="catalog", raw_text=None)),
        (
            "draft",
            UserInputDraft,
            lambda b: b.update(leisure_interest={"state": "selected", "selected": ["music"]}),
        ),
        ("review", ProfileReview, lambda b: b["occupation"].update(mode="catalog")),
        (
            "profile",
            ConfirmedProfile,
            lambda b: b["occupation"].update(catalog_version="e3-input-catalog-v1"),
        ),
    ],
)
def test_old_objects_cannot_carry_v34_content(name, cls, edit):
    body = raw(name)
    edit(body)
    with pytest.raises(ValidationError):
        cls.model_validate(body)


def test_default_v34_keys_on_an_old_draft_are_dropped_not_kept():
    """页面把新字段的默认值（未答）一起带回旧草稿：照样读，写出时照原样不写。"""
    body = raw("draft")
    padded = {
        **body,
        "education": {"state": "unanswered", "option_id": None},
        "occupation": {**body["occupation"], "mode": "text", "catalog_version": None},
    }
    draft = UserInputDraft.model_validate(padded)
    assert draft.model_dump(mode="json") == body
    assert draft_fingerprint(draft) == raw("review")["draft"]["draft_fingerprint"]


def test_new_objects_still_write_every_field(pack):
    draft = make_draft(pack)
    review = review_draft(pack, draft)
    profile = confirm_profile(
        pack, draft, person_ref="u1", acknowledged=review.pending_confirmations
    )
    assert profile.meta.schema_version == PRODUCT_SCHEMA_VERSION
    dumped = profile.model_dump(mode="json")
    assert "qualifications" in dumped and "catalog_fingerprint" in dumped["occupation"]
    assert {"education", "certificates", "experience"} <= set(draft.model_dump(mode="json"))
    assert "qualifications" in review.model_dump(mode="json")


# ---------------------------------------------------------------------- 显示：原来的绑定、原来的资源


def _english(body, doc_name, user_text):
    doc = json.loads((OLD / f"{doc_name}.json").read_text(encoding="utf-8"))
    bset = binding_set(doc, body)
    fields, status, _ = display_fields(body, [bset], "en", user_text=user_text)
    return doc, bset, fields, status


@pytest.mark.parametrize(
    ("name", "cls", "doc", "user_text"),
    [
        ("profile", ConfirmedProfile, "profile_display", messages.PROFILE),
        ("review", ProfileReview, "review_display", messages.REVIEW),
    ],
)
def test_old_profile_and_review_english_follow_their_original_bindings(name, cls, doc, user_text):
    body = cls.model_validate(raw(name)).model_dump(mode="json")
    saved, bset, fields, status = _english(body, doc, user_text)
    assert saved["source_fingerprint"] == json_fingerprint(body)  # 消息摘要与当初生成时相同
    assert bset.valid and bset.verified and bset.resource_version == OLD_RESOURCES
    assert status == "complete" and fields
    assert all(f["status"] == "provided" and f["resource_version"] == OLD_RESOURCES for f in fields)


@pytest.mark.parametrize("damage", ["deleted", "tampered"])
def test_old_resources_gone_or_tampered_are_reported_not_replaced(tmp_path, monkeypatch, damage):
    frozen = tmp_path / "display"
    pack_dir = (
        REPO / "data/display/f58ef477619ff8754a1d1672736d44e38356802a89a8b78f29382f6a800ca8e5"
    )
    shutil.copytree(pack_dir, frozen / pack_dir.name)
    old = frozen / pack_dir.name / OLD_RESOURCES
    if damage == "deleted":
        shutil.rmtree(old)
    else:
        path = old / "resources.json"
        path.write_text(
            path.read_text(encoding="utf-8").replace('"en"', '"en" ', 1), encoding="utf-8"
        )
    monkeypatch.setattr(catalog, "FROZEN", frozen)
    catalog._load_frozen.cache_clear()
    try:
        body = ConfirmedProfile.model_validate(raw("profile")).model_dump(mode="json")
        _, bset, fields, status = _english(body, "profile_display", messages.PROFILE)
    finally:
        catalog._load_frozen.cache_clear()
    reason = "resources_unavailable" if damage == "deleted" else "resources_invalid"
    assert bset.resource_version == OLD_RESOURCES and status == "unavailable"
    assert all(f["missing_reason"] == reason for f in fields)


# ---------------------------------------------------------------------- 旧草稿进入新问卷：显式迁移


def test_the_real_old_draft_migrates_only_with_consent(pack):
    draft = UserInputDraft.model_validate(raw("draft"))
    m = migration_for(pack, draft)
    assert m is not None and m.version_changed and m.consent_required  # 旧问卷收集过的通勤上限
    moved = migrate(pack, draft, clear_removed=True)
    assert moved.meta.schema_version == PRODUCT_SCHEMA_VERSION and moved.hard_requirements == ()
    assert review_draft(pack, moved).confirmable
