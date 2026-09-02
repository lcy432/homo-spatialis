"""三种输入形式 → 一个 Persona。研究模式与产品模式共用这一层。"""

from __future__ import annotations

import pytest

from common.ids import OccupationId
from worlds.dimensions import ALL_DIMENSIONS, AMENITY_DIMENSIONS, MATERIAL
from worlds.elicitation import (
    MAX_TEXT_SHIFT,
    ConstantSumResponse,
    Elicitation,
    FreeTextResponse,
    OverrideError,
    build_persona,
)
from worlds.shares import BehaviouralAnswers, ProvisionalShareProvider

OCC = OccupationId("SOC2018", "15-1252")


def _points(**kw):
    base = dict.fromkeys(ALL_DIMENSIONS, 10.0)
    base.update(kw)
    return base


def _elicitation(**kw):
    return Elicitation(
        constant_sum=ConstantSumResponse(points=_points(**kw.pop("points", {}))),
        behavioural=kw.pop("behavioural", BehaviouralAnswers()),
        occupation=OCC,
        **kw,
    )


# --------------------------------------------------------------------------
# 两通道映射（§7.2）
# --------------------------------------------------------------------------


def test_the_scale_sets_the_total_and_the_survey_sets_the_ratio():
    """方向不能反。份额由行为揭示，量表问的是"你多在乎"。"""
    e = _elicitation(points={MATERIAL: 40.0})
    p = build_persona(e, "p1")
    assert p.params.beta / p.params.w_material == pytest.approx(p.shares.s_rent, rel=1e-12)
    assert p.params.alpha / p.params.w_material == pytest.approx(
        p.shares.s_goods + p.shares.s_other, rel=1e-12
    )


def test_exponents_sum_to_one():
    p = build_persona(_elicitation(), "p1")
    assert p.params.alpha + p.params.beta + sum(p.params.theta.values()) == pytest.approx(1.0)


def test_the_material_slider_is_the_only_thing_feeding_alpha_and_beta():
    """w₁ 不是"住房权重"，是"物质相对于环境的总权重"。"""
    low = build_persona(_elicitation(points={MATERIAL: 10.0}), "a")
    high = build_persona(_elicitation(points={MATERIAL: 60.0}), "b")
    assert high.params.w_material > low.params.w_material
    # 内部劈法由份额决定，与量表无关，所以比值不变
    assert high.params.beta / high.params.w_material == pytest.approx(
        low.params.beta / low.params.w_material
    )


def test_theta_covers_exactly_the_six_amenities():
    p = build_persona(_elicitation(), "p1")
    assert set(p.params.theta) == set(AMENITY_DIMENSIONS)


# --------------------------------------------------------------------------
# 自由文本只能调整，不能覆盖（§6.1）
# --------------------------------------------------------------------------


def test_a_modest_text_adjustment_is_allowed():
    e = _elicitation(free_text=FreeTextResponse(text="我很讨厌通勤", adjustments={"commute": 5.0}))
    p = build_persona(e, "p1")
    assert p.params.theta["commute"] > build_persona(_elicitation(), "p2").params.theta["commute"]


def test_an_oversized_single_shift_is_refused():
    """上限管幅度。没有它，职能 A 抽出的调整量可以把量表整个盖掉，
    而用户不会知道问卷白填了。"""
    with pytest.raises(OverrideError, match="只能调整"):
        FreeTextResponse(text="x", adjustments={"commute": MAX_TEXT_SHIFT + 0.1})


def test_a_coordinated_reordering_is_refused_even_within_the_per_dimension_cap():
    """上限管幅度，秩相关管**结构**：每一维都合规，合起来仍可能是覆盖。"""
    points = _points(**{MATERIAL: 40.0, "safety": 30.0, "commute": 5.0})
    tilt = {MATERIAL: -MAX_TEXT_SHIFT, "safety": -MAX_TEXT_SHIFT, "commute": MAX_TEXT_SHIFT}
    e = Elicitation(
        constant_sum=ConstantSumResponse(points=points),
        behavioural=BehaviouralAnswers(),
        occupation=OCC,
        free_text=FreeTextResponse(text="x", adjustments=tilt),
    )
    with pytest.raises(OverrideError, match="Spearman"):
        build_persona(e, "p1")


def test_without_free_text_nothing_is_adjusted():
    e = _elicitation()
    assert e.adjusted_weights() == e.constant_sum.weights()


# --------------------------------------------------------------------------
# 量表本身的校验
# --------------------------------------------------------------------------


def test_the_scale_must_cover_exactly_the_seven_dimensions():
    with pytest.raises(ValueError, match="七维"):
        ConstantSumResponse(points={MATERIAL: 100.0})


def test_presentation_order_must_be_a_permutation():
    with pytest.raises(ValueError, match="置换"):
        ConstantSumResponse(points=_points(), presentation_order=(MATERIAL,))


# --------------------------------------------------------------------------
# 份额：与城市无关（架构修正一）
# --------------------------------------------------------------------------


def test_the_share_provider_signature_has_no_city():
    """不是遗漏，是防线。份额随城市变会同时破坏价格指数、κ 的跨城不变性、
    以及 §11.7 的 logit 设定。"""
    import inspect

    sig = inspect.signature(ProvisionalShareProvider().shares_from_answers)
    assert "city" not in sig.parameters


def test_shares_always_sum_to_one():
    provider = ProvisionalShareProvider()
    for size in (1, 3, 5):
        for tenure in ("rent", "own"):
            for income in (35_000.0, 82_000.0, 250_000.0):
                s = provider.shares_from_answers(
                    BehaviouralAnswers(household_size=size, tenure=tenure, reference_income=income)
                )
                assert s.s_rent + s.s_goods + s.s_other == pytest.approx(1.0)


def test_the_housing_share_falls_with_income():
    """恩格尔。位似的效用函数配上随人变化的份额，非位似性通过跨人变异进来。"""
    provider = ProvisionalShareProvider()
    poor = provider.shares_from_answers(BehaviouralAnswers(reference_income=35_000.0))
    rich = provider.shares_from_answers(BehaviouralAnswers(reference_income=250_000.0))
    assert poor.s_rent > rich.s_rent


def test_owning_a_car_moves_weight_into_goods():
    provider = ProvisionalShareProvider()
    with_car = provider.shares_from_answers(BehaviouralAnswers(has_car=True))
    without = provider.shares_from_answers(BehaviouralAnswers(has_car=False))
    assert with_car.s_goods > without.s_goods


def test_equal_base_weights_make_the_rank_check_vacuous_not_undefined():
    """全等权时不存在需要被保住的排序，秩相关无定义。让 nan 参与安全判定
    本身就是个坑——它这次碰巧落在通过的一侧，纯属运气。"""
    from worlds.elicitation import _rank_agreement

    flat = dict.fromkeys(ALL_DIMENSIONS, 1.0)
    tilted = {**flat, "commute": 2.0}
    assert _rank_agreement(flat, tilted) == 1.0
    assert _rank_agreement(tilted, flat) == 1.0
