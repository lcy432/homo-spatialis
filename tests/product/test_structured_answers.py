"""新问卷的结构化答案（DECISIONS/0046）：按目录选职业、学历 / 证书 / 相关经验、兴趣与口味面板、去掉的板块（定和分配见 test_ratings）。

全部走正式入口（``review_draft`` / ``confirm_profile``），不预先写结果。资格换算与 E3B 已有规则一致的核对在 ``test_input_catalog``。
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import pytest
from pydantic import ValidationError

from common.product_contracts import (
    LeisureInterestAnswer,
    OccupationAnswer,
    TasteAnswer,
)
from product.answers import ConfirmationRefusedError, confirm_profile, review_draft
from product.fixtures import make_draft
from product.input_catalog import input_catalog
from product.pack import load_pack

REPO = Path(__file__).resolve().parents[2]
E3A = REPO / "configs/product/e3a_synthetic_pack.yaml"
E3B = REPO / "configs/product/e3b_us_v1_pack.yaml"


@cache
def _pack(path: Path = E3B):
    return load_pack(path)


def _draft(path: Path = E3B, **kw):
    """按目录选职业、不写任何文字的新问卷草稿（默认软件开发工程师、本科、没有证书、兴趣与口味都跳过）。"""
    base = {
        "occupation_mode": "catalog",
        "occupation_text": None,
        "selected": "occ.soc_15_1252" if path == E3B else "occ.office_clerk",
        "education": "bachelor" if path == E3B else None,
        "certificates": (),
        "free_text": None,
    }
    return make_draft(_pack(path), **{**base, **kw})


def _codes(review, severity=None):
    return [i.code for i in review.issues if severity is None or i.severity == severity]


def _confirm(draft, path: Path = E3B):
    review = review_draft(_pack(path), draft)
    return confirm_profile(
        _pack(path), draft, person_ref="u1", acknowledged=review.pending_confirmations
    )


# ---------------------------------------------------------------------- 职业：按目录直接选


@pytest.mark.parametrize("path", [E3A, E3B])
def test_a_catalog_choice_needs_no_text_at_all(path):
    draft = _draft(path)
    assert draft.occupation.raw_text is None and draft.free_text.text is None
    assert draft.leisure_interest.text is None and draft.taste.text is None
    review = review_draft(_pack(path), draft)
    assert review.confirmable, review.issues
    assert review.occupation.status == "catalog_selected" and review.occupation.mode == "catalog"
    (candidate,) = review.occupation.candidates
    assert candidate.candidate_id == draft.occupation.selected_candidate and candidate.duties
    confirmed = _confirm(draft, path)
    occ = confirmed.occupation
    assert occ.match_status == "catalog_selected" and occ.raw_text is None
    catalog = input_catalog(_pack(path))
    assert (occ.catalog_version, occ.catalog_fingerprint) == (
        catalog.decl.catalog_version,
        catalog.fingerprint,
    )
    assert confirmed.expected_fingerprint() == confirmed.content_fingerprint


def test_an_unknown_occupation_id_is_refused_never_substituted():
    review = review_draft(_pack(), _draft(selected="occ.teacher"))
    assert "occupation_selection_invalid" in _codes(review, "error")
    assert review.occupation.selected is None and review.occupation.candidates == ()
    with pytest.raises(ConfirmationRefusedError):
        _confirm(_draft(selected="occ.teacher"))


def test_an_expired_catalog_is_refused():
    draft = _draft()
    old = draft.model_copy(
        update={"occupation": draft.occupation.model_copy(update={"catalog_fingerprint": "0" * 64})}
    )
    review = review_draft(_pack(), old)
    assert "occupation_catalog_expired" in _codes(review, "error")
    stale_version = draft.model_copy(
        update={
            "occupation": draft.occupation.model_copy(
                update={"catalog_version": "e3-input-catalog-v0"}
            )
        }
    )
    assert "occupation_catalog_expired" in _codes(review_draft(_pack(), stale_version), "error")


def test_illegal_mode_combinations_are_refused_by_the_schema():
    with pytest.raises(ValidationError):
        OccupationAnswer(
            state="provided", mode="catalog", raw_text="程序员", selected_candidate="x"
        )
    with pytest.raises(ValidationError):
        OccupationAnswer(
            state="provided", mode="text", raw_text="文员", catalog_fingerprint="0" * 64
        )


def test_a_catalog_choice_on_a_pack_without_a_catalog_is_refused(monkeypatch):
    from product import answers

    monkeypatch.setattr(answers, "input_catalog", lambda pack: None)
    review = review_draft(_pack(), _draft())
    assert "occupation_catalog_unavailable" in _codes(review, "error")


def test_the_text_path_still_works_for_compatibility():
    draft = make_draft(_pack(E3A))  # 文字描述 + 候选里选中
    review = review_draft(_pack(E3A), draft)
    assert review.confirmable and review.occupation.status == "explicit"
    assert _confirm(draft, E3A).occupation.raw_text == draft.occupation.raw_text


# ---------------------------------------------------------------------- 学历、证书与相关经验


def test_an_occupation_never_grants_qualifications():
    review = review_draft(_pack(), _draft(education=None, certificates=None))
    assert review.abilities == () and review.qualifications.source == "none"
    assert "qualification_requirement_unmet" in _codes(review, "warning")
    assert review.confirmable  # 只是提醒：各城的开局会照实挡下
    (req,) = review.qualifications.requirements
    assert (req.ability_id, req.met) == ("edu_at_least_bachelor", False)


def test_a_bachelor_meets_lower_thresholds_but_grants_no_certificate():
    review = review_draft(_pack(), _draft(education="bachelor"))
    assert review.abilities == (
        "edu_at_least_hs",
        "edu_at_least_some_college",
        "edu_at_least_associate",
        "edu_at_least_bachelor",
    )
    assert [r.met for r in review.qualifications.requirements] == [True]
    assert not _codes(review, "warning")
    confirmed = _confirm(_draft(education="bachelor"))
    assert confirmed.abilities == review.abilities
    q = confirmed.qualifications
    assert (q.source, q.education_option, q.rule) == (
        "structured_answers",
        "bachelor",
        "confirmed_experience_mapping_v1",
    )


@pytest.mark.parametrize("education", [None, "unsure", "below_high_school"])
def test_unanswered_unsure_and_below_high_school_grant_nothing(education):
    review = review_draft(_pack(), _draft(education=education, certificates=None))
    assert review.abilities == ()


def test_certificates_none_unanswered_and_selected_are_kept_apart():
    pack = _pack()
    none = review_draft(
        pack, _draft(selected="occ.soc_49_3023", education="high_school", certificates=())
    )
    assert (
        none.qualifications.certificates_state == "provided"
        and none.qualifications.certificates == ()
    )
    assert "soc_49_3023_award" not in none.abilities
    assert "qualification_requirement_unmet" in _codes(none, "warning")
    unanswered = review_draft(
        pack, _draft(selected="occ.soc_49_3023", education="high_school", certificates=None)
    )
    assert unanswered.qualifications.certificates_state == "unanswered"
    held = review_draft(
        pack,
        _draft(
            selected="occ.soc_49_3023", education="high_school", certificates=("soc_49_3023_award",)
        ),
    )
    assert "soc_49_3023_award" in held.abilities and not _codes(held, "warning")


@pytest.mark.parametrize(
    ("certificates", "code"),
    [
        (("edu_at_least_bachelor",), "certificate_unknown"),  # 学历门槛不能当证书选
        (("soc_49_3023_award", "soc_49_3023_award"), "certificate_duplicated"),
        (("soc_99_award",), "certificate_unknown"),
    ],
)
def test_bad_certificate_answers_are_refused(certificates, code):
    review = review_draft(_pack(), _draft(certificates=certificates))
    assert code in _codes(review, "error")


def test_experience_is_asked_only_where_needed_and_counts_years():
    pack = _pack()
    cook = _draft(selected="occ.soc_35_2014", education=None, certificates=None)
    review = review_draft(pack, cook)
    assert review.qualifications.ask_experience == ("exp.soc_35_2014",)
    assert "qualification_requirement_unmet" in _codes(review, "warning")
    assert review_draft(pack, _draft()).qualifications.ask_experience == ()  # 软件开发不问经验
    ok = review_draft(
        pack,
        _draft(
            selected="occ.soc_35_2014",
            education=None,
            certificates=None,
            experience=(("occ.soc_35_3031", 2),),
        ),
    )
    assert "soc_35_2014_experience" in ok.abilities and not _codes(ok, "warning")
    short = review_draft(
        pack,
        _draft(
            selected="occ.soc_35_2014",
            education=None,
            certificates=None,
            experience=(("occ.soc_35_3031", 0),),
        ),
    )
    assert short.abilities == ()
    none = review_draft(
        pack, _draft(selected="occ.soc_35_2014", education=None, certificates=None, experience=())
    )
    assert none.qualifications.experience_state == "provided" and none.abilities == ()


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ((("occ.soc_15_1252", 3),), "experience_occupation_not_related"),
        ((("occ.soc_35_3031", 1), ("occ.soc_35_3031", 2)), "experience_duplicated"),
    ],
)
def test_bad_experience_answers_are_refused(entries, code):
    review = review_draft(_pack(), _draft(selected="occ.soc_35_2014", experience=entries))
    assert code in _codes(review, "error")


def test_new_answers_and_old_claims_that_disagree_are_refused_not_merged():
    pack = _pack()
    mixed = _draft(education="high_school", abilities=("edu_at_least_bachelor",))
    review = review_draft(pack, mixed)
    assert "qualification_conflict" in _codes(review, "error")
    with pytest.raises(ConfirmationRefusedError):
        _confirm(mixed)
    # 两边给出完全相同的资格时不冲突（也不是并集：结果就是那一组）
    same = _draft(education="high_school", certificates=(), abilities=("edu_at_least_hs",))
    review = review_draft(pack, same)
    assert "qualification_conflict" not in _codes(review) and review.abilities == (
        "edu_at_least_hs",
    )


def test_old_claims_alone_still_work_for_compatibility():
    review = review_draft(
        _pack(), _draft(education=None, certificates=None, abilities=("edu_at_least_bachelor",))
    )
    assert review.qualifications.source == "ability_claims"
    assert review.abilities == ("edu_at_least_bachelor",)


def test_a_section_the_pack_does_not_offer_is_refused():
    review = review_draft(_pack(E3A), _draft(E3A, education="bachelor"))
    assert "qualification_section_not_offered" in _codes(review, "error")


def test_free_text_and_job_titles_grant_nothing():
    draft = _draft(
        education=None, certificates=None, free_text="我有本科学历，还有汽车维修证书，做过五年厨师"
    )
    assert review_draft(_pack(), draft).abilities == ()


# ---------------------------------------------------------------------- 兴趣与口味：面板多选


def test_panel_choices_confirm_without_any_text():
    draft = _draft(
        leisure={"state": "selected", "selected": ("fitness", "music")},
        taste={"state": "selected", "selected": ("chinese",)},
    )
    review = review_draft(_pack(), draft)
    assert review.confirmable and review.leisure_interest.status == "confirmable"
    assert "ack:interests_not_personalized" not in review.pending_confirmations
    i = _confirm(draft).interests
    assert (i.leisure_status, i.leisure_categories) == ("confirmed", ("fitness", "music"))
    assert (i.taste_status, i.cuisines) == ("confirmed", ("chinese",))
    assert i.leisure_text is None and i.taste_text is None and i.unrecognized_text == ()


@pytest.mark.parametrize(
    ("leisure", "status"),
    [
        ({}, "unanswered"),  # 跳过 = 未答
        ({"state": "unsure"}, "unsure"),
        ({"state": "no_fixed_hobby"}, "no_fixed_hobby"),
        ({"state": "generic"}, "generic"),
    ],
)
def test_skipping_and_the_no_category_states_stay_apart(leisure, status):
    draft = _draft(leisure=leisure, taste={"state": "no_preference"} if status == "unsure" else {})
    i = _confirm(draft).interests
    assert i.leisure_status == status and i.leisure_categories == ()


def test_panel_shape_is_enforced():
    with pytest.raises(ValidationError):
        LeisureInterestAnswer(state="selected", selected=())  # 一个都没选就是未答
    with pytest.raises(ValidationError):
        TasteAnswer(state="selected", selected=("chinese",), text="喜欢川菜")  # 面板不带文字


def test_categories_with_an_unsure_or_no_preference_state_are_refused():
    review = review_draft(
        _pack(), _draft(taste={"state": "no_preference", "selected": ("chinese",)})
    )
    assert "interest_state_conflict" in _codes(review, "error")
    review = review_draft(_pack(), _draft(leisure={"state": "unsure", "selected": ("music",)}))
    assert "interest_state_conflict" in _codes(review, "error")


def test_panel_choices_outside_the_vocabulary_are_refused():
    review = review_draft(_pack(), _draft(leisure={"state": "selected", "selected": ("hiking",)}))
    assert "interest_unknown_category" in _codes(review, "error")
    review = review_draft(_pack(), _draft(taste={"state": "selected", "selected": ("sichuan",)}))
    assert "interest_unknown_category" in _codes(review, "error")  # E3B 只有“中餐”


# ---------------------------------------------------------------------- 去掉的板块


def test_new_drafts_carry_no_hard_requirement_or_attribute():
    draft = make_draft(_pack(E3A))
    assert draft.hard_requirements == () and draft.attribute_preferences == ()
    confirmed = _confirm(draft, E3A)
    assert confirmed.hard_requirements == () and confirmed.retained == ()


def test_removed_sections_in_a_new_draft_block_and_are_listed():
    draft = make_draft(
        _pack(E3A),
        hard_requirements=(
            {
                "requirement_id": "hr.commute",
                "option_id": "housing.max_commute_roundtrip",
                "parameter_value": 60,
            },
        ),
        attributes=(
            {
                "preference_id": "pref.brand",
                "need": "personal_enjoyment",
                "kind": "brand",
                "raw_text": "吉他想要某个牌子的",
            },
        ),
    )
    review = review_draft(_pack(E3A), draft)
    blocked = [i for i in review.issues if i.code == "removed_section_present"]
    assert [i.field for i in blocked] == ["hard_requirements", "attribute_preferences"]
    assert "60" in blocked[0].message_zh and "吉他想要某个牌子的" in blocked[1].message_zh
    assert not review.confirmable and review.statements == ()
    with pytest.raises(ConfirmationRefusedError):
        _confirm(draft, E3A)


# 定和分配（v3.5 起是独立评分）的规则见 tests/product/test_ratings.py
