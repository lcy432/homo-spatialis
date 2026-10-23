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


def test_interests_and_brands_do_not_change_points_cash_or_eligibility(pack):
    brand = (
        {
            "preference_id": "pref.brand",
            "need": "personal_enjoyment",
            "kind": "brand",
            "raw_text": "吉他想要某个牌子的",
        },
    )
    _, plain = confirm(pack, with_interests(pack))
    _, fan = confirm(
        pack,
        with_interests(
            pack,
            LeisureInterestAnswer(state="described", text="吉他", selected=("music",)),
            TasteAnswer(state="described", text="川菜", selected=("sichuan",)),
            attributes=brand,
        ),
    )
    assert fan.six_dimensions == plain.six_dimensions and fan.material == plain.material
    assert (
        fan.content_fingerprint != plain.content_fingerprint
    )  # 兴趣是确认内容的一部分：改了要重新确认
    assert fan.money == plain.money and fan.hard_requirements == plain.hard_requirements
    p0, p1 = product_profile(pack, plain, []), product_profile(pack, fan, [])
    assert any("吉他想要某个牌子的" in r for r in p1.unsupported_requests)
    a, b = preference_from_confirmed_profile(p0), preference_from_confirmed_profile(p1)
    assert a.dimension_weights == b.dimension_weights and a.material_weights == b.material_weights
    assert (b.leisure_interests, b.cuisines) == (("music",), ("sichuan",))  # 品牌没有进入评分偏好


def test_material_is_four_items_and_old_confirmations_are_reviewed_again(pack):
    _, confirmed = confirm(pack, with_interests(pack))
    assert set(confirmed.material.allocation.points) == {
        "housing_space",
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
            pack, material={"housing_space": "30", "fresh_food": "30", "daily_convenience": "40"}
        ),
    )
    assert not three.confirmable  # 旧的三项物质分配必须重新回答四项，不自动换算
