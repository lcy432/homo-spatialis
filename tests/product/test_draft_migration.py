"""旧草稿进入新问卷的显式迁移（DECISIONS/0046）与历史对象的读回。

* 旧草稿里有新问卷不再收集的硬要求与属性偏好：迁移说明逐条列出；没有用户同意不清空；同意后清空、换成当前版本，其他答案不变。
* 只差版本时也要显式迁移（读取不改写）；已经是当前版本、没有要清空的，迁移拒绝。
* 旧草稿的六维 / 物质分配是旧问卷的百分比（v0.5 改用独立评分，DECISIONS/0047）：迁移说明照原样列出，迁移时用户必须选择重新评分
  或同等重视；不选就拒绝，系统不把旧百分比改读成评分。
* 旧对象的读回与身份见 ``test_legacy_objects``（用旧版真实写下的对象，不拿新版对象改版本号冒充）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.product_contracts import UserInputDraft
from product.answers import review_draft
from product.fixtures import make_draft
from product.migration import MigrationError, migrate, migration_for
from product.pack import load_pack

REPO = Path(__file__).resolve().parents[2]
E3A = REPO / "configs/product/e3a_synthetic_pack.yaml"
SAMPLES = REPO / "contracts/product/v3/samples"
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
QUIET = {"requirement_id": "hr.quiet", "raw_text": "必须很安静"}
BRAND = {
    "preference_id": "pref.brand",
    "need": "personal_enjoyment",
    "kind": "brand",
    "raw_text": "吉他想要某个牌子的",
    "as_hard_requirement": True,
}


@pytest.fixture(scope="module")
def pack():
    return load_pack(E3A)


def old_draft(pack, **kw) -> UserInputDraft:
    """一份按 v0.3 问卷填的旧草稿（版本是旧的；默认带着旧问卷收集的通勤硬要求，六维与物质是当时的百分比分配）。"""
    kw.setdefault("six_mode", "manual")
    kw.setdefault("material_mode", "manual")
    draft = make_draft(pack, hard_requirements=kw.pop("hard_requirements", (COMMUTE,)), **kw)
    body = draft.model_dump(mode="json")
    body["meta"].update(OLD_META)
    return UserInputDraft.model_validate(body)


def test_an_old_draft_with_removed_answers_lists_them_and_needs_consent(pack):
    draft = old_draft(pack, hard_requirements=(COMMUTE, QUIET), attributes=(BRAND,))
    m = migration_for(pack, draft)
    assert m is not None and m.version_changed and m.consent_required
    assert (m.from_questionnaire_version, m.to_questionnaire_version) == (
        "e3a-questionnaire-v0.3-draft",
        "e3a-questionnaire-v0.5-draft",
    )
    rows = [(r.section, r.ref, r.raw_text) for r in m.removed]
    assert rows == [
        ("hard_requirements", "hr.commute", None),
        ("hard_requirements", "hr.quiet", "必须很安静"),
        ("attribute_preferences", "pref.brand", "吉他想要某个牌子的"),
    ]
    assert "60" in m.removed[0].summary and "不可妥协" in m.removed[2].summary
    with pytest.raises(MigrationError) as exc:
        migrate(pack, draft, clear_removed=False, allocation_choice="rerate")
    assert exc.value.code == "migration_consent_required"


def test_consent_clears_only_the_removed_answers_and_moves_to_the_current_version(pack):
    draft = old_draft(pack, hard_requirements=(COMMUTE,), attributes=(BRAND,))
    moved = migrate(pack, draft, clear_removed=True, allocation_choice="equal_weight")
    assert moved.meta == pack.meta()
    assert moved.hard_requirements == () and moved.attribute_preferences == ()
    # 旧的百分比分配换成用户选的“同等重视”：两组都是独立评分、每项 50（不是把旧百分比当评分）
    for group in (moved.six_dimensions, moved.material):
        assert group.mode == "ratings_normalized_v1"
        assert {e.value_text for e in group.entries.values()} == {"50"}
    keep = {"meta", "hard_requirements", "attribute_preferences", "six_dimensions", "material"}
    before = {k: v for k, v in draft.model_dump(mode="json").items() if k not in keep}
    after = {k: v for k, v in moved.model_dump(mode="json").items() if k not in keep}
    # 其他答案原样保留；v3.4 新增的几项（v3.3 的草稿写出时没有）是默认的“未答 / 文字描述”
    assert {k: after[k] for k in before if k != "occupation"} == {
        k: v for k, v in before.items() if k != "occupation"
    }
    assert after["occupation"] == {
        **before["occupation"],
        "mode": "text",
        "catalog_version": None,
        "catalog_fingerprint": None,
    }
    assert {
        k: after[k]["state"] for k in ("education", "certificates", "experience")
    } == dict.fromkeys(("education", "certificates", "experience"), "unanswered")
    review = review_draft(pack, moved)
    assert review.confirmable, review.issues
    assert migration_for(pack, moved) is None


def test_an_old_draft_is_not_confirmable_before_migration(pack):
    review = review_draft(pack, old_draft(pack))
    codes = {i.code for i in review.issues if i.severity == "error"}
    assert {"version_changed", "removed_section_present"} <= codes and not review.confirmable


def test_a_version_only_migration_needs_no_consent_but_is_still_explicit(pack):
    draft = old_draft(pack, hard_requirements=(), six={}, six_mode=None, material_mode=None)
    m = migration_for(pack, draft)
    assert m is not None and m.version_changed and not m.consent_required and m.removed == ()
    assert m.allocations == () and not m.allocation_choice_required  # 两组都没答：没有旧分配要选
    moved = migrate(pack, draft, clear_removed=False)
    assert moved.meta == pack.meta()


def test_an_old_allocation_needs_a_choice_even_without_removed_answers(pack):
    draft = old_draft(pack, hard_requirements=())
    m = migration_for(pack, draft)
    assert m is not None and not m.consent_required and m.allocation_choice_required
    assert [a.group for a in m.allocations] == ["six_dimensions", "material"]
    assert [ln.value_text for ln in m.allocations[0].lines] == ["30", "20", "20", "10", "10", "10"]
    with pytest.raises(MigrationError) as exc:
        migrate(pack, draft, clear_removed=False)
    assert exc.value.code == "migration_choice_required"
    with pytest.raises(MigrationError):
        migrate(pack, draft, clear_removed=False, allocation_choice="keep")  # 只认两种选择
    moved = migrate(pack, draft, clear_removed=False, allocation_choice="rerate")
    assert (
        moved.six_dimensions.mode == "ratings_normalized_v1" and moved.six_dimensions.entries == {}
    )
    review = review_draft(pack, moved)
    assert not review.confirmable  # 重新评分之前各项是未答
    assert {i.code for i in review.issues if i.severity == "error"} == {"allocation_unanswered"}


def test_a_current_draft_needs_no_migration(pack):
    draft = make_draft(pack)
    assert migration_for(pack, draft) is None
    with pytest.raises(MigrationError) as exc:
        migrate(pack, draft, clear_removed=True)
    assert exc.value.code == "migration_not_needed"


def test_a_current_draft_with_removed_answers_also_gets_migration_notes(pack):
    draft = make_draft(pack, hard_requirements=(COMMUTE,))
    m = migration_for(pack, draft)
    assert m is not None and not m.version_changed and m.consent_required


# 历史对象（v3.3 的画像、审阅、草稿、预检查与结果）的读回用旧版真实写下的对象核对：tests/product/test_legacy_objects.py
