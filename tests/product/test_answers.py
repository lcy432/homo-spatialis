"""E3-A 模块 B：原始答案、审阅与确认画像——状态不混、精确分配、确认身份与失效、只留存的陈述。

可手算的例子：六维 30/20/20/10/10/10 精确合计 100；均分每项 50/3（显示 16.7，六个显示值加起来是 100.2，但精确值合计正好 100，
显示值不回流计算）；物质均分每项 25（四项）。
"""

from __future__ import annotations

import contextlib
import copy
from pathlib import Path

import pytest

from common.life_product_profile import ProductLifeProfile
from product.answers import (
    ConfirmationRefusedError,
    confirm_profile,
    confirmation_problems,
    draft_fingerprint,
    review_draft,
)
from product.fixtures import make_draft, prepare_workspace
from product.pack import DataPack, load_pack

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> DataPack:
    work = prepare_workspace(tmp_path_factory.mktemp("ws"), REPO)
    with contextlib.chdir(work):
        return load_pack()


#: 默认草稿要知悉的两项：没有按具体兴趣或口味个性化（收尾 R7）与标准开局
ACKS = ("ack:interests_not_personalized", "ack:standard_start")


def codes(review) -> list[str]:
    return [i.code for i in review.issues]


def lines(review, which="six_dimensions"):
    return {ln.key: ln for ln in getattr(review, which).lines}


def test_normal_draft_is_confirmable_and_needs_the_standard_acknowledgements(pack):
    """默认草稿没有回答兴趣与口味：可以确认，但要知悉“本次不按具体兴趣或口味个性化”与标准开局（收尾 R7）。"""
    review = review_draft(pack, make_draft(pack))
    assert review.confirmable and codes(review) == []
    assert review.pending_confirmations == ("ack:interests_not_personalized", "ack:standard_start")
    assert review.six_dimensions.total.exact == "100"
    assert review.occupation.status == "explicit"
    assert review.money.converted.amount_minor == 300000


def test_unanswered_zero_indifferent_and_unsure_stay_distinct(pack):
    draft = make_draft(
        pack,
        six={
            "material": "0",
            "safety": "40",
            "opportunity": "30",
            "leisure": None,
            "environment": "indifferent",
            "commute": "unsure",
        },
    )
    review = review_draft(pack, draft)
    ln = lines(review)
    # 独立评分：明确的 0 是回答（原始评分 0）；没有回答完之前不出任何权重
    assert ln["material"].state == "provided" and ln["material"].rating_exact == "0"
    assert (ln["leisure"].state, ln["leisure"].rating_exact) == ("unanswered", None)
    assert (ln["environment"].state, ln["environment"].rating_exact) == ("indifferent", None)
    assert (ln["commute"].state, ln["commute"].rating_exact) == ("unsure", None)
    assert all(x.exact is None for x in ln.values())
    assert {"allocation_unanswered", "allocation_indifferent", "allocation_unsure"} <= set(
        codes(review)
    )
    assert not review.confirmable
    # 没有被自动换成 0 或均分：原始合计只算已回答的三项（还不完整）
    assert review.six_dimensions.status == "incomplete"
    assert review.six_dimensions.rating_total.exact == "70"
    with pytest.raises(ConfirmationRefusedError):
        confirm_profile(pack, draft, person_ref="u1", acknowledged=review.pending_confirmations)


def test_material_zero_skips_the_breakdown_without_fabricating_answers(pack):
    draft = make_draft(
        pack,
        six={
            "material": "0",
            "safety": "40",
            "opportunity": "30",
            "leisure": "10",
            "environment": "10",
            "commute": "10",
        },
        material_mode=None,
    )
    review = review_draft(pack, draft)
    assert review.material.status == "not_applicable" and review.material.lines == ()
    confirmed = confirm_profile(
        pack, draft, person_ref="u1", acknowledged=review.pending_confirmations
    )
    assert (
        confirmed.material.status == "not_applicable_parent_zero"
        and confirmed.material.allocation is None
    )
    # 父项为 0 时已填写的细分只留在原始答案里（提示），不进入画像
    draft2 = make_draft(
        pack,
        six={
            "material": "0",
            "safety": "40",
            "opportunity": "30",
            "leisure": "10",
            "environment": "10",
            "commute": "10",
        },
    )
    review2 = review_draft(pack, draft2)
    assert "material_parent_zero_ignored" in codes(review2) and review2.confirmable


def test_the_old_percentage_modes_are_read_but_no_longer_confirm(pack):
    """v0.5 问卷用独立评分（逐项规则见 ``test_ratings``）；旧的百分比分配照原来的含义算出各项，但不再用来确认新画像。"""
    draft = make_draft(pack, six_mode="manual", material_mode="equal_split")
    review = review_draft(pack, draft)
    assert lines(review)["material"].exact == "30"  # 原来的含义：百分比原样
    assert {ln.exact for ln in lines(review, "material").values()} == {"25"}  # 主动平均分配
    assert "allocation_mode_retired" in codes(review) and not review.confirmable


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("16.666", "points_too_many_decimals"),
        ("100/6", "rating_not_decimal"),
        ("abc", "points_not_a_number"),
        ("120", "rating_out_of_range"),
    ],
)
def test_ratings_are_parsed_exactly(pack, text, code):
    six = {
        "material": text,
        "safety": "20",
        "opportunity": "20",
        "leisure": "10",
        "environment": "10",
        "commute": "10",
    }
    assert code in codes(review_draft(pack, make_draft(pack, six=six)))


def test_healthcare_is_not_a_seventh_dimension_and_laundry_is_not_convenience(pack):
    six = {
        "material": "30",
        "safety": "20",
        "opportunity": "20",
        "leisure": "10",
        "environment": "10",
        "commute": "10",
        "healthcare": "10",
    }
    assert "healthcare_not_in_six_dimensions" in codes(
        review_draft(pack, make_draft(pack, six=six))
    )
    review = review_draft(
        pack,
        make_draft(pack, material={"housing_condition": "30", "food_life": "30", "laundry": "40"}),
    )
    assert "legacy_laundry_key" in codes(review) and not review.confirmable


def test_free_text_over_2000_characters_is_refused_not_truncated(pack):
    assert review_draft(pack, make_draft(pack, free_text="字" * 2000)).confirmable
    review = review_draft(pack, make_draft(pack, free_text="字" * 2001))
    assert "free_text_too_long" in codes(review) and review.free_text_chars == 2001


def test_health_answers_keep_unanswered_and_unsure_apart_and_compute_no_total(pack):
    draft = make_draft(
        pack,
        health_choices={
            "hc_more_pay_more_sick_risk": "unsure",
            "hc_reserve_for_health_costs": None,
            "hc_wait_for_clear_risk_info": "agree",
        },
    )
    review = review_draft(pack, draft)
    states = {h.question_id: h.answer_state for h in review.health}
    assert states == {
        "hc_more_pay_more_sick_risk": "unsure",
        "hc_reserve_for_health_costs": "unanswered",
        "hc_wait_for_clear_risk_info": "provided",
    }
    confirmed = confirm_profile(
        pack, draft, person_ref="u1", acknowledged=review.pending_confirmations
    )
    dumped = confirmed.health.model_dump(mode="json")
    assert set(dumped) == {"questionnaire_version", "status", "answers"}  # 没有总分、概率或愿付金额
    assert confirmed.health.status == "draft_wording_not_validated"
    assert "没有回答" in next(
        a.meaning for a in confirmed.health.answers if a.answer_state == "unanswered"
    )


def test_editing_an_answer_invalidates_the_confirmation_and_tampering_is_detected(pack):
    draft = make_draft(pack)
    confirmed = confirm_profile(pack, draft, person_ref="u1", acknowledged=ACKS)
    assert confirmation_problems(pack, confirmed, draft) == []
    edited = make_draft(
        pack,
        six={
            "material": "40",
            "safety": "10",
            "opportunity": "20",
            "leisure": "10",
            "environment": "10",
            "commute": "10",
        },
    )
    assert draft_fingerprint(edited) != draft_fingerprint(draft)
    assert [i.code for i in confirmation_problems(pack, confirmed, edited)] == [
        "confirmation_invalidated_by_edit"
    ]
    tampered = confirmed.model_copy(
        update={
            "six_dimensions": confirmed.six_dimensions.model_copy(
                update={
                    "points": {**confirmed.six_dimensions.points, "material": "40", "safety": "10"}
                }
            )
        }
    )
    assert "confirmation_tampered" in [i.code for i in confirmation_problems(pack, tampered)]


def test_changing_the_vocabulary_or_pack_invalidates_old_confirmations(pack):
    draft = make_draft(pack)
    confirmed = confirm_profile(pack, draft, person_ref="u1", acknowledged=ACKS)
    raw = copy.deepcopy(pack.raw)
    raw["pack"]["dimensions"][1]["definition"] = "改过的安全题干"
    changed = DataPack(raw, pack.path)
    assert "data_pack_changed" in [i.code for i in confirmation_problems(changed, confirmed)]
    # 草稿也按旧资料包填写：审阅要求重新填写或确认
    assert "data_pack_changed" in codes(review_draft(changed, draft))


def test_confirmed_profile_compiles_to_a_six_dimension_profile_without_healthcare(pack, tmp_path):
    from product.compile import product_profile

    confirmed = confirm_profile(pack, make_draft(pack), person_ref="u1", acknowledged=ACKS)
    profile = product_profile(pack, confirmed, [])
    assert isinstance(profile, ProductLifeProfile)
    assert "healthcare" not in profile.dimension_points
    assert profile.confirmed_profile_fingerprint == confirmed.content_fingerprint
    with pytest.raises(ValueError, match="六维必须恰好是"):
        ProductLifeProfile.model_validate(
            {
                **profile.model_dump(mode="json"),
                "dimension_points": {**profile.dimension_points, "healthcare": "0"},
            }
        )
    with pytest.raises(ValueError, match="精确合计 100"):
        ProductLifeProfile.model_validate(
            {
                **profile.model_dump(mode="json"),
                "dimension_points": {**profile.dimension_points, "commute": "11"},
            }
        )
    with pytest.raises(ValueError, match="不是规范写法"):
        ProductLifeProfile.model_validate(
            {
                **profile.model_dump(mode="json"),
                "dimension_points": {**profile.dimension_points, "commute": "20/2"},
            }
        )
