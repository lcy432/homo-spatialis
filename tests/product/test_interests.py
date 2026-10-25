"""E3 兴趣与口味（DECISIONS/0041）：文字只按词表形成候选，用户选中的类别才进入画像；各种作答状态分开保存；品牌只留存；
旧版本的确认不悄悄补齐，重新审阅。全部离线、确定性，不调用语言模型。
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from common.product_contracts import ConfirmedProfile, LeisureInterestAnswer, TasteAnswer
from econ.life_scoring import preference_from_confirmed_profile
from product.answers import ConfirmationRefusedError, confirm_profile, review_draft
from product.compile import product_profile
from product.fixtures import make_draft, prepare_workspace
from product.interests import Term, interpret
from product.pack import DataPack, load_pack

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> DataPack:
    work = prepare_workspace(tmp_path_factory.mktemp("ws"), REPO)
    with contextlib.chdir(work):
        return load_pack()


def terms(pack, which: str) -> tuple[Term, ...]:
    return tuple(Term(t.key, t.label, t.aliases) for t in getattr(pack.body.interests, which))


def with_interests(pack, leisure=None, taste=None, **kw):
    draft = make_draft(pack, **kw)
    return draft.model_copy(
        update={
            "leisure_interest": leisure or LeisureInterestAnswer(),
            "taste": taste or TasteAnswer(),
        }
    )


def confirm(pack, draft):
    review = review_draft(pack, draft)
    return review, confirm_profile(
        pack, draft, person_ref="u1", acknowledged=review.pending_confirmations
    )


def test_text_becomes_candidates_ambiguities_and_unrecognized_pieces(pack):
    found = interpret(
        "喜欢弹吉他和网球，周末去美术馆；还有冲浪、听音乐会", terms(pack, "leisure_categories")
    )
    assert found.candidates == ("music", "racket_sports", "arts_culture")
    assert found.unrecognized == ("还有冲浪",)  # 词表外的爱好原样保留，不猜
    assert found.ambiguous == ("听音乐会",)  # 同时像“音乐”和“艺术与文化”：列出，不替用户选
    assert interpret("麻辣火锅，意面", terms(pack, "cuisines")).candidates == ("sichuan", "italian")
    assert interpret(None, terms(pack, "cuisines")) == interpret("", terms(pack, "cuisines"))


@pytest.mark.parametrize("state", ["unanswered", "unsure", "no_fixed_hobby", "generic"])
def test_states_without_categories_are_kept_apart_and_scored_neutrally(pack, state):
    review, confirmed = confirm(pack, with_interests(pack, LeisureInterestAnswer(state=state)))
    assert review.leisure_interest.status == "confirmable"
    assert (
        confirmed.interests.leisure_status == state and confirmed.interests.leisure_categories == ()
    )
    profile = product_profile(pack, confirmed, [])
    assert profile.leisure_interest_status == state and profile.leisure_interests == ()
    assert preference_from_confirmed_profile(profile).leisure_interests == ()


def test_described_interests_need_an_explicit_selection(pack):
    text = LeisureInterestAnswer(state="described", text="喜欢弹吉他，还有冲浪")
    draft = with_interests(pack, text)
    review = review_draft(pack, draft)
    assert not review.confirmable and review.leisure_interest.status == "pending_selection"
    assert [i.code for i in review.issues] == ["interest_selection_pending"]
    with pytest.raises(ConfirmationRefusedError):
        confirm_profile(pack, draft, person_ref="u1", acknowledged=review.pending_confirmations)
    chosen = text.model_copy(update={"selected": ("music",)})
    review, confirmed = confirm(pack, with_interests(pack, chosen))
    assert confirmed.interests.leisure_status == "confirmed"
    assert confirmed.interests.leisure_categories == ("music",)
    assert confirmed.interests.unrecognized_text == ("还有冲浪",)
    profile = product_profile(pack, confirmed, [])
    assert profile.leisure_interests == ("music",)
    assert "未认出的兴趣或口味描述：还有冲浪" in profile.unsupported_requests  # 只留存，本版不评分


def test_out_of_vocabulary_or_conflicting_selections_are_refused(pack):
    stray = review_draft(
        pack,
        with_interests(
            pack, LeisureInterestAnswer(state="described", text="冲浪", selected=("surfing",))
        ),
    )
    assert stray.leisure_interest.status == "invalid" and not stray.confirmable
    assert "interest_unknown_category" in [i.code for i in stray.issues]
    clash = review_draft(
        pack, with_interests(pack, LeisureInterestAnswer(state="generic", selected=("music",)))
    )
    assert clash.leisure_interest.status == "invalid"
    assert "interest_state_conflict" in [i.code for i in clash.issues]
    taste = review_draft(
        pack, with_interests(pack, taste=TasteAnswer(state="no_preference", selected=("sichuan",)))
    )
    assert taste.taste.status == "invalid"


def test_interests_do_not_change_points_cash_or_eligibility(pack):
    """兴趣与口味只进入共同、有限的匹配规则（品牌等属性偏好新问卷不再收集）。"""
    _, plain = confirm(pack, with_interests(pack))
    _, fan = confirm(
        pack,
        with_interests(
            pack,
            LeisureInterestAnswer(state="described", text="吉他", selected=("music",)),
            TasteAnswer(state="described", text="川菜", selected=("sichuan",)),
        ),
    )
    assert fan.six_dimensions == plain.six_dimensions and fan.material == plain.material
    assert (
        fan.content_fingerprint != plain.content_fingerprint
    )  # 兴趣是确认内容的一部分：改了要重新确认
    assert fan.money == plain.money and fan.hard_requirements == plain.hard_requirements
    p0, p1 = product_profile(pack, plain, []), product_profile(pack, fan, [])
    a, b = preference_from_confirmed_profile(p0), preference_from_confirmed_profile(p1)
    assert a.dimension_weights == b.dimension_weights and a.material_weights == b.material_weights
    assert (b.leisure_interests, b.cuisines) == (("music",), ("sichuan",))  # 品牌没有进入评分偏好


def test_material_is_four_items_and_old_confirmations_are_reviewed_again(pack):
    _, confirmed = confirm(pack, with_interests(pack))
    assert set(confirmed.material.allocation.points) == {
        "housing_condition",
        "food_life",
        "daily_convenience",
        "personal_enjoyment",
    }
    old = json.loads(
        (REPO / "contracts/product/v2/samples/04_normal_confirmed.json").read_text(encoding="utf-8")
    )
    with pytest.raises(ValueError, match="schema_version"):
        ConfirmedProfile.model_validate(old["payload"])  # 不把旧确认补 0 或重新分配后沿用
    three = review_draft(
        pack,
        with_interests(
            pack,
            material={"housing_condition": "30", "fresh_food": "30", "daily_convenience": "40"},
        ),
    )
    assert not three.confirmable  # 旧的三项物质分配必须重新回答四项，不自动换算


# --------------------------------------------------------------------------- 收尾 R7：原文留存、重复类别、未个性化的确认


def test_r7_a_generic_choice_keeps_an_unsupported_hobby_as_original_text(pack):
    """审核复现：只写“喜欢冲浪”再改选通用评价，原文被丢掉。现在原文留存为本版未支持的描述，明确接受通用评价后能完成确认。"""
    draft = with_interests(pack, LeisureInterestAnswer(state="generic", text="喜欢冲浪"))
    review = review_draft(pack, draft)
    assert review.confirmable and review.leisure_interest.text == "喜欢冲浪"
    assert review.leisure_interest.unrecognized == ("喜欢冲浪",)
    assert "ack:interests_not_personalized" in review.pending_confirmations
    with pytest.raises(ConfirmationRefusedError):  # 不知悉“本次不按具体兴趣个性化”就不能确认
        confirm_profile(pack, draft, person_ref="u1", acknowledged=("ack:standard_start",))
    confirmed = confirm_profile(
        pack, draft, person_ref="u1", acknowledged=review.pending_confirmations
    )
    i = confirmed.interests
    assert (i.leisure_status, i.leisure_text, i.unrecognized_text) == (
        "generic",
        "喜欢冲浪",
        ("喜欢冲浪",),
    )
    assert not i.personalized and any("通用休闲评价" in s for s in i.scope_zh)
    assert any("喜欢冲浪" in s for s in i.scope_zh)
    profile = product_profile(pack, confirmed, [])
    assert profile.leisure_interests == () and any(
        "喜欢冲浪" in r for r in profile.unsupported_requests
    )


@pytest.mark.parametrize("state, selected", [("described", ("music",)), ("generic", ())])
def test_r7_mixed_supported_and_unsupported_text_is_kept(pack, state, selected):
    leisure = LeisureInterestAnswer(state=state, text="吉他和冲浪", selected=selected)
    review, confirmed = confirm(pack, with_interests(pack, leisure))
    assert confirmed.interests.leisure_text == "吉他和冲浪"
    assert confirmed.interests.unrecognized_text == ("冲浪",)
    assert confirmed.interests.leisure_categories == selected  # 没有选中就不采用候选
    assert review.leisure_interest.candidates == ("music",)


@pytest.mark.parametrize("kind", ["leisure", "taste"])
def test_r7_a_repeated_category_is_refused_at_review_not_at_compile(pack, kind):
    """审核复现：重复选中同一兴趣先确认成功、编译时才失败。现在审阅阶段就拒绝。"""
    if kind == "leisure":
        draft = with_interests(
            pack, LeisureInterestAnswer(state="described", text="吉他", selected=("music", "music"))
        )
    else:
        draft = with_interests(
            pack, taste=TasteAnswer(state="described", text="川菜", selected=("sichuan", "sichuan"))
        )
    review = review_draft(pack, draft)
    assert not review.confirmable
    assert "interest_duplicate_selection" in [x.code for x in review.issues]
    with pytest.raises(ConfirmationRefusedError):
        confirm_profile(pack, draft, person_ref="u1", acknowledged=review.pending_confirmations)


def test_r7_unanswered_and_an_explicit_generic_choice_stay_distinguishable(pack):
    plain = review_draft(pack, with_interests(pack))
    chosen = review_draft(pack, with_interests(pack, LeisureInterestAnswer(state="generic")))
    assert plain.leisure_interest.message_zh != chosen.leisure_interest.message_zh
    _, a = confirm(pack, with_interests(pack))
    _, b = confirm(pack, with_interests(pack, LeisureInterestAnswer(state="generic")))
    assert (a.interests.leisure_status, b.interests.leisure_status) == ("unanswered", "generic")
    assert a.interests.scope_zh[0] != b.interests.scope_zh[0]
    assert "没有回答" in a.interests.scope_zh[0] and "通用休闲评价" in b.interests.scope_zh[0]


def test_r7_preflight_and_result_state_the_personalization_scope(tmp_path):
    from product import scenarios as sc

    s = sc.closed_loop(
        tmp_path / "generic",
        "generic_scope",
        person="u511",
        draft_kwargs={"leisure": {"state": "generic", "text": "喜欢冲浪"}},
    )
    assert s.result is not None and s.pre is not None
    for lines in (s.pre.preflight.scope_zh, s.result.limitations_zh):
        assert any("通用休闲评价" in x for x in lines)
        assert any("口味与菜系：你没有回答" in x for x in lines)
        assert any("喜欢冲浪" in x for x in lines)


def test_r7_unknown_descriptions_change_no_points_cash_or_eligibility(pack):
    _, plain = confirm(pack, with_interests(pack))
    _, surfer = confirm(
        pack, with_interests(pack, LeisureInterestAnswer(state="generic", text="喜欢冲浪"))
    )
    assert surfer.six_dimensions == plain.six_dimensions and surfer.material == plain.material
    assert surfer.money == plain.money and surfer.hard_requirements == plain.hard_requirements
    assert surfer.abilities == plain.abilities


def test_r7_capability_texts_match_what_is_supported(pack):
    from product.capabilities import build_capabilities

    caps = build_capabilities(pack)
    kinds = {a.kind: a for a in caps.attribute_kinds}
    assert kinds["brand"].support == "unsupported" and "不交给分身" in kinds["brand"].note
    assert kinds["cuisine"].support == "unsupported" and "口味与菜系" in kinds["cuisine"].note
    assert {t.key for t in caps.interests.cuisines} == {"home_style", "sichuan", "italian"}
