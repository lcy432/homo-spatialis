"""旧草稿进入新问卷的显式迁移（DECISIONS/0046）与历史对象的读回。

* 旧草稿里有新问卷不再收集的硬要求与属性偏好：迁移说明逐条列出；没有用户同意不清空；同意后清空、换成当前版本，其他答案不变。
* 只差版本时也要显式迁移（读取不改写）；已经是当前版本、没有要清空的，迁移拒绝。
* v3.3 的草稿、确认画像与结果仍能按新结构读回；旧画像不能直接用来准备新模拟（新确认必须用新问卷）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.product_contracts import ConfirmedProfile, ProductResult, UserInputDraft
from product.answers import confirmation_problems, review_draft
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
    """一份按 v0.3 问卷填的旧草稿（版本是旧的；默认带着旧问卷收集的通勤硬要求）。"""
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
        "e3a-questionnaire-v0.4-draft",
    )
    rows = [(r.section, r.ref, r.raw_text) for r in m.removed]
    assert rows == [
        ("hard_requirements", "hr.commute", None),
        ("hard_requirements", "hr.quiet", "必须很安静"),
        ("attribute_preferences", "pref.brand", "吉他想要某个牌子的"),
    ]
    assert "60" in m.removed[0].summary and "不可妥协" in m.removed[2].summary
    with pytest.raises(MigrationError) as exc:
        migrate(pack, draft, clear_removed=False)
    assert exc.value.code == "migration_consent_required"


def test_consent_clears_only_the_removed_answers_and_moves_to_the_current_version(pack):
    draft = old_draft(pack, hard_requirements=(COMMUTE,), attributes=(BRAND,))
    moved = migrate(pack, draft, clear_removed=True)
    assert moved.meta == pack.meta()
    assert moved.hard_requirements == () and moved.attribute_preferences == ()
    keep = {"meta", "hard_requirements", "attribute_preferences"}
    before = {k: v for k, v in draft.model_dump(mode="json").items() if k not in keep}
    after = {k: v for k, v in moved.model_dump(mode="json").items() if k not in keep}
    assert after == before  # 其他答案原样保留
    review = review_draft(pack, moved)
    assert review.confirmable, review.issues
    assert migration_for(pack, moved) is None


def test_an_old_draft_is_not_confirmable_before_migration(pack):
    review = review_draft(pack, old_draft(pack))
    codes = {i.code for i in review.issues if i.severity == "error"}
    assert {"version_changed", "removed_section_present"} <= codes and not review.confirmable


def test_a_version_only_migration_needs_no_consent_but_is_still_explicit(pack):
    draft = old_draft(pack, hard_requirements=())
    m = migration_for(pack, draft)
    assert m is not None and m.version_changed and not m.consent_required and m.removed == ()
    moved = migrate(pack, draft, clear_removed=False)
    assert moved.meta == pack.meta()


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


# ---------------------------------------------------------------------- 历史对象


def _v33(body: dict) -> dict:
    body["meta"].update(OLD_META)
    return body


def test_v33_profiles_and_results_still_read_back():
    """旧结构的确认画像（有原文职业、通勤硬要求、留存的陈述）与结果仍能按新结构读回。"""
    profile = json.loads((SAMPLES / "04_normal_confirmed.json").read_text(encoding="utf-8"))[
        "payload"
    ]
    profile = _v33(profile)
    profile.pop("qualifications", None)  # v3.3 没有这一项
    profile["hard_requirements"] = [
        {
            "requirement_id": "hr.commute",
            "option_id": "housing.max_commute_roundtrip",
            "parameter_value": 60,
            "label_zh": "住处到就业中心的往返时间不超过（分钟） 60 分钟（单独核验并报告，不拦截计划）",
        }
    ]
    old = ConfirmedProfile.model_validate(profile)
    assert old.meta.schema_version == "e3a-product-schema-v3.3" and old.qualifications is None
    assert old.occupation.raw_text and old.occupation.match_status in ("explicit", "ambiguous")
    result = _v33(
        json.loads((SAMPLES / "10_normal_result.json").read_text(encoding="utf-8"))["payload"]
    )
    assert ProductResult.model_validate(result).meta.schema_version == "e3a-product-schema-v3.3"


def test_an_old_profile_cannot_prepare_a_new_run(pack):
    """新确认必须用新问卷：旧版本的画像能读回，但不能直接准备新模拟（要按新问卷重新审阅与确认）。"""
    from product.answers import confirm_profile

    draft = make_draft(pack)
    review = review_draft(pack, draft)
    confirmed = confirm_profile(
        pack, draft, person_ref="u1", acknowledged=review.pending_confirmations
    )
    body = _v33(confirmed.model_dump(mode="json"))
    unsealed = ConfirmedProfile.model_validate({**body, "content_fingerprint": "0" * 64})
    body["content_fingerprint"] = unsealed.expected_fingerprint()
    old = ConfirmedProfile.model_validate(body)
    codes = {i.code for i in confirmation_problems(pack, old)}
    assert "version_changed" in codes and "confirmation_tampered" not in codes
