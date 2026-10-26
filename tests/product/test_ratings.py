"""独立评分（``ratings_normalized_v1``，v3.5，DECISIONS/0047）：原始评分 → 精确归一权重。

* 权重 = 100 × 评分 ÷ 原始合计（精确分数，不加任何系数）；同比例放大或缩小，权重不变；显示的四舍五入不回到计算；
* 未答不是 0；明确的 0 是回答；无所谓、暂不确定保留状态；全零拒绝、不自动均分；“同等重视”是用户主动把每项设为 50；
* 物质父项为 0 时四项不适用，草稿里已有的四项答案原样留着、不参与这一次；父项重新大于 0 时照原样恢复；
* 新问卷不再用旧的百分比分配确认；旧草稿迁移时由用户选择重新评分或同等重视；已确认的旧画像照原样读回；
* 确认画像保存原始评分、原始合计与换算规则，``points`` 是最终权重；E1 的输入只看 ``points``。
"""

from __future__ import annotations

import json
from fractions import Fraction
from functools import cache
from pathlib import Path

import pytest

from common.product_contracts import (
    ConfirmedProfile,
    ProfileReview,
    UserInputDraft,
)
from product.answers import confirm_profile, review_draft
from product.fixtures import make_draft
from product.migration import MigrationError, migrate, migration_for
from product.pack import load_pack

REPO = Path(__file__).resolve().parents[2]
E3A = REPO / "configs/product/e3a_synthetic_pack.yaml"
SIX = ("material", "safety", "opportunity", "leisure", "environment", "commute")
LEAVES = ("housing_condition", "food_life", "daily_convenience", "personal_enjoyment")
V34 = REPO / "tests/fixtures/v34_objects"
V33 = REPO / "tests/fixtures/v33_objects"


@cache
def _pack():
    return load_pack(E3A)


def _six(*values: str | None) -> dict[str, str | None]:
    return dict(zip(SIX, values, strict=True))


def _review(**kw):
    return review_draft(_pack(), make_draft(_pack(), **kw))


def _weights(review, group="six_dimensions"):
    return {ln.key: ln.exact for ln in getattr(review, group).lines}


def _codes(review):
    return [i.code for i in review.issues if i.severity == "error"]


# ---------------------------------------------------------------------- 精确归一


def test_ratings_become_exact_weights():
    review = _review(six=_six("80", "40", "40", "20", "10", "10"))
    assert review.confirmable
    six = review.six_dimensions
    assert six.mode == "ratings_normalized_v1" and six.rule == "ratings_normalized_v1"
    assert _weights(review) == dict(zip(SIX, ("40", "20", "20", "10", "5", "5"), strict=True))
    assert six.rating_total.exact == "200" and six.total.exact == "100"
    assert [ln.rating_display for ln in six.lines] == ["80", "40", "40", "20", "10", "10"]


def test_equal_weight_is_fifty_each_and_exact():
    review = _review(six=dict.fromkeys(SIX, "50"), material=dict.fromkeys(LEAVES, "50"))
    assert set(_weights(review).values()) == {"50/3"}
    assert set(_weights(review, "material").values()) == {"25"}
    assert sum(Fraction(v) for v in _weights(review).values()) == 100


@pytest.mark.parametrize(
    "ratings",
    [
        ("8", "4", "4", "2", "1", "1"),
        ("80", "40", "40", "20", "10", "10"),
        ("100", "50", "50", "25", "12.5", "12.5"),
    ],
)
def test_scaling_all_ratings_keeps_the_weights(ratings):
    assert _weights(_review(six=_six(*ratings))) == dict(
        zip(SIX, ("40", "20", "20", "10", "5", "5"), strict=True)
    )


def test_decimal_ratings_stay_exact_and_rounding_does_not_feed_back():
    review = _review(six=_six("33.33", "33.33", "33.34", "0", "0", "0.01"))
    weights = {k: Fraction(v) for k, v in _weights(review).items()}
    assert sum(weights.values()) == 100  # 精确，不是显示值相加
    assert weights["material"] == Fraction(3333, 10001) * 100
    assert review.six_dimensions.lines[0].display == "33.3"  # 显示只供阅读
    profile = confirm_profile(
        _pack(),
        make_draft(_pack(), six=_six("33.33", "33.33", "33.34", "0", "0", "0.01")),
        person_ref="u1",
        acknowledged=review.pending_confirmations,
    )
    assert profile.six_dimensions.points["material"] == "333300/10001"
    assert profile.six_dimensions.ratings["material"] == "3333/100"


# ---------------------------------------------------------------------- 状态与零


def test_unanswered_is_not_zero_and_zero_is_an_answer():
    review = _review(six=_six("50", "0", None, "indifferent", "unsure", "50"))
    states = {ln.key: (ln.state, ln.rating_exact) for ln in review.six_dimensions.lines}
    assert states["safety"] == ("provided", "0")
    assert states["opportunity"] == ("unanswered", None)
    assert states["leisure"] == ("indifferent", None)
    assert states["environment"] == ("unsure", None)
    assert not review.confirmable
    assert {"allocation_unanswered", "allocation_indifferent", "allocation_unsure"} <= set(
        _codes(review)
    )
    assert all(ln.exact is None for ln in review.six_dimensions.lines)  # 没回答完不出权重
    assert review.six_dimensions.rating_total.exact == "100"  # 回答了的那些的原始合计


def test_all_zero_is_refused_not_split_evenly():
    review = _review(six=dict.fromkeys(SIX, "0"))
    assert not review.confirmable and "ratings_all_zero" in _codes(review)
    issue = next(i for i in review.issues if i.code == "ratings_all_zero")
    assert "至少有一项大于 0，或主动选择同等重视" in issue.message_zh
    assert all(ln.exact is None for ln in review.six_dimensions.lines)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("100.5", "rating_out_of_range"),
        ("12.345", "points_too_many_decimals"),
        ("50/3", "rating_not_decimal"),
    ],
)
def test_bad_ratings_are_refused(value, code):
    review = _review(six=_six(value, "40", "40", "20", "10", "10"))
    assert code in _codes(review) and not review.confirmable


def test_a_zero_material_parent_keeps_the_leaves_for_later():
    leaves = {
        "housing_condition": "70",
        "food_life": None,
        "daily_convenience": "0",
        "personal_enjoyment": "30",
    }
    draft = make_draft(_pack(), six=_six("0", "40", "30", "10", "10", "10"), material=leaves)
    review = review_draft(_pack(), draft)
    assert (
        review.material.status == "not_applicable" and review.confirmable
    )  # 四项不参与，没答完也不挡
    assert draft.material.entries["housing_condition"].value_text == "70"  # 草稿里原样留着
    back = draft.model_copy(update={"six_dimensions": make_draft(_pack()).six_dimensions})
    again = review_draft(_pack(), back)  # 父项重新大于 0：原来的四项答案照原样回来
    lines = {ln.key: (ln.state, ln.rating_exact) for ln in again.material.lines}
    assert lines["housing_condition"] == ("provided", "70") and lines["food_life"] == (
        "unanswered",
        None,
    )
    assert "allocation_unanswered" in _codes(again)  # 不清零、不自动均分


# ---------------------------------------------------------------------- 确认画像与 E1 的输入


def test_confirmed_profile_keeps_ratings_rule_and_final_points():
    draft = make_draft(_pack(), six=_six("80", "40", "40", "20", "10", "10"))
    review = review_draft(_pack(), draft)
    profile = confirm_profile(
        _pack(), draft, person_ref="u1", acknowledged=review.pending_confirmations
    )
    six = profile.six_dimensions
    assert six.points == dict(zip(SIX, ("40", "20", "20", "10", "5", "5"), strict=True))
    assert six.ratings == dict(zip(SIX, ("80", "40", "40", "20", "10", "10"), strict=True))
    assert (six.rating_total, six.rule) == ("200", "ratings_normalized_v1")
    assert profile.material.allocation.rule == "ratings_normalized_v1"
    assert profile.expected_fingerprint() == profile.content_fingerprint


def test_e1_reads_only_the_final_points():
    """同样的最终权重，不管原始评分是多少，E1 看到的偏好完全相同。"""
    from econ.life_scoring import preference_from_confirmed_profile
    from product.compile import product_profile

    def profile(six):
        draft = make_draft(_pack(), six=six)
        review = review_draft(_pack(), draft)
        return confirm_profile(
            _pack(), draft, person_ref="u1", acknowledged=review.pending_confirmations
        )

    a = profile(_six("30", "20", "20", "10", "10", "10"))
    b = profile(_six("60", "40", "40", "20", "20", "20"))
    assert a.six_dimensions.points == b.six_dimensions.points
    pa = preference_from_confirmed_profile(product_profile(_pack(), a, []))
    pb = preference_from_confirmed_profile(product_profile(_pack(), b, []))
    assert (
        pa.dimension_weights == pb.dimension_weights and pa.material_weights == pb.material_weights
    )


# ---------------------------------------------------------------------- 旧的百分比分配


@pytest.mark.parametrize("mode", ["manual", "equal_split"])
def test_the_old_percentage_modes_no_longer_confirm(mode):
    review = _review(six_mode=mode, material_mode=mode)
    assert "allocation_mode_retired" in _codes(review) and not review.confirmable


def _payload(path: Path, part: str | None = None):
    body = json.loads(path.read_text(encoding="utf-8"))
    body = body.get("payload", body)
    return body[part] if part else body


@pytest.mark.parametrize(
    ("path", "part"),
    [
        (V33 / "draft.json", None),
        (V34 / "131_service_draft_catalog_selection.json", "draft"),
    ],
)
def test_old_drafts_show_their_allocation_and_need_an_explicit_choice(path, part):
    draft = UserInputDraft.model_validate(_payload(path, part))
    m = migration_for(_pack(), draft)
    assert m is not None and m.allocation_choice_required
    six = next(a for a in m.allocations if a.group == "six_dimensions")
    assert six.mode == draft.six_dimensions.mode  # 原样列出，不换算
    assert [ln.value_text for ln in six.lines] == [
        draft.six_dimensions.entries[k].value_text for k in SIX
    ]
    with pytest.raises(MigrationError) as exc:
        migrate(_pack(), draft, clear_removed=True)
    assert exc.value.code == "migration_choice_required"  # 不选就不迁移，不把旧百分比当评分
    rerated = migrate(_pack(), draft, clear_removed=True, allocation_choice="rerate")
    assert (
        rerated.six_dimensions.mode == "ratings_normalized_v1"
        and rerated.six_dimensions.entries == {}
    )
    equal = migrate(_pack(), draft, clear_removed=True, allocation_choice="equal_weight")
    assert {e.value_text for e in equal.six_dimensions.entries.values()} == {"50"}
    for k in (
        "cities",
        "occupation",
        "age",
        "cash",
        "health",
        "free_text",
        "abilities",
        "leisure_interest",
        "taste",
    ):
        assert getattr(equal, k) == getattr(draft, k), k  # 其他答案原样保留


def test_v34_objects_read_back_exactly_without_v35_fields():
    """v3.4 的草稿、审阅与确认画像（v3.4 导出的公共样例原样）：读回照原样，没有 v3.5 新增的字段，确认画像的身份不变。"""
    for path, part, cls in (
        (V34 / "131_service_draft_catalog_selection.json", "draft", UserInputDraft),
        (V34 / "132_service_review_catalog_selection.json", "review", ProfileReview),
        (V34 / "142_service_review_allocation_equal_split.json", "review", ProfileReview),
        (V34 / "134_service_confirm_catalog_selection.json", "confirmed", ConfirmedProfile),
    ):
        body = _payload(path, part)
        obj = cls.model_validate(body)
        assert obj.model_dump(mode="json") == body, path.name
    profile = ConfirmedProfile.model_validate(
        _payload(V34 / "134_service_confirm_catalog_selection.json", "confirmed")
    )
    assert profile.expected_fingerprint() == profile.content_fingerprint
    assert profile.six_dimensions.mode == "manual" and profile.six_dimensions.ratings is None


def test_a_v34_object_cannot_carry_ratings():
    body = _payload(V34 / "131_service_draft_catalog_selection.json", "draft")
    body["six_dimensions"]["mode"] = "ratings_normalized_v1"
    with pytest.raises(ValueError):
        UserInputDraft.model_validate(body)
