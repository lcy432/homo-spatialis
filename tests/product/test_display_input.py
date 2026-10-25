"""输入侧的显示文字（``e3-display-v1``）：能力清单、审阅、确认画像与确认回复。

* **中文不变。** 按生成公共样例的同一条代码路径（``product.scenarios``、``product.answers``）重建 ``contracts/product/v3/samples``
  里这些消息，与提交的样例逐字相同（只去掉 ``meta.sample`` 出处标签）。工作目录只复制资料包本身，不复制整套基础样例。
* **英文覆盖。** 两个资料包（合成、E3B）各生成一遍，按当前源资源（不冻结）渲染：应展示的位置要么有英文，要么照实报缺失原因；
  不允许过时、对不上、有歧义，也不允许缺译文。缺失只能是两种有意的情况：词表别名（匹配用的原文词语，不是显示文字）与嵌着用户
  中文原文的说明（不把中文混进英文句子）。
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from common.product_contracts import UserInputDraft
from product.answers import confirm_profile, draft_fingerprint, review_draft
from product.capabilities import build_capabilities
from product.display import messages, recording
from product.display.catalog import source_resources
from product.display.render import coverage
from product.fixtures import make_draft
from product.pack import DataPack, load_pack

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "contracts/product/v3/samples"
E3A = "configs/product/e3a_synthetic_pack.yaml"
E3B = "configs/product/e3b_us_v1_pack.yaml"

BRAND = (
    {
        "preference_id": "pref.brand",
        "need": "personal_enjoyment",
        "kind": "brand",
        "raw_text": "吉他想要某个牌子的",
    },
)
#: 只走审阅（能确认时也确认）的样例：与 scripts/product_e3a.py 生成样例时的参数相同
REVIEW_ONLY: dict[str, dict[str, Any]] = {
    "ambiguous": {"occupation_text": "我在餐厅做店员，有时收银，有时做饮品", "selected": None},
    "unanswered_zero": {
        "six": {
            "material": "0",
            "safety": "40",
            "opportunity": "30",
            "leisure": None,
            "environment": "indifferent",
            "commute": "30",
        },
        "material": {
            "housing_condition": "50",
            "food_life": "50",
            "daily_convenience": "0",
            "personal_enjoyment": "0",
        },
    },
    "equal_split": {"six_mode": "equal_split", "material_mode": "equal_split"},
    "health_unsure": {
        "health_choices": {
            "hc_more_pay_more_sick_risk": "unsure",
            "hc_reserve_for_health_costs": None,
            "hc_wait_for_clear_risk_info": "no_clear_tendency",
        }
    },
    "legacy_laundry": {"material": {"housing_condition": "30", "food_life": "30", "laundry": "40"}},
    "attributes": {
        "attributes": (
            {
                "preference_id": "pref.brand",
                "need": "daily_convenience",
                "kind": "brand",
                "raw_text": "家电更喜欢国产品牌",
            },
            {
                "preference_id": "pref.cuisine",
                "need": "food_life",
                "kind": "cuisine",
                "raw_text": "喜欢清淡的江浙菜",
            },
            {
                "preference_id": "pref.no_strangers",
                "need": "daily_convenience",
                "kind": "home_entry_concern",
                "raw_text": "不让陌生人进家",
                "as_hard_requirement": True,
            },
        )
    },
    "money_blocked": {"cash": ("10000", "USD", "2026-10")},
    "money_separator": {"cash": ("3000,50", "USD", "2024")},
    "interest_confirmed": {
        "leisure": {
            "state": "described",
            "text": "喜欢弹吉他，还有冲浪、听音乐会",
            "selected": ["music"],
        },
        "taste": {"state": "described", "text": "川菜，偶尔意面", "selected": ["sichuan"]},
        "attributes": BRAND,
    },
    "interest_unanswered": {},
    "interest_generic": {
        "leisure": {"state": "generic", "text": "喜欢冲浪"},
        "taste": {"state": "no_preference"},
    },
    "interest_unknown": {"leisure": {"state": "described", "text": "冲浪和攀岩"}},
    "interest_unsupported": {
        "leisure": {"state": "described", "text": "冲浪", "selected": ["surfing"]}
    },
    "interest_duplicate": {
        "leisure": {"state": "described", "text": "吉他", "selected": ["music", "music"]}
    },
}
#: 样例 → （场景，取哪一份消息）
SAMPLE_CASES = {
    "11_occupation_ambiguous_review": ("ambiguous", "review"),
    "12_unanswered_and_zero_review": ("unanswered_zero", "review"),
    "13_equal_split_review": ("equal_split", "review"),
    "14_equal_split_confirmed": ("equal_split", "confirmed"),
    "15_health_unsure_confirmed": ("health_unsure", "confirmed"),
    "16_legacy_laundry_review": ("legacy_laundry", "review"),
    "17_attributes_and_home_entry_review": ("attributes", "review"),
    "30_money_conversion_blocked_review": ("money_blocked", "review"),
    "37_money_separator_rejected_review": ("money_separator", "review"),
    "39_interest_confirmed_review": ("interest_confirmed", "review"),
    "40_interest_unanswered_review": ("interest_unanswered", "review"),
    "41_interest_generic_review": ("interest_generic", "review"),
    "42_interest_unknown_review": ("interest_unknown", "review"),
    "43_interest_unsupported_review": ("interest_unsupported", "review"),
    "49_interest_duplicate_review": ("interest_duplicate", "review"),
}
USER_TEXT = {"review": messages.REVIEW, "confirmed": messages.PROFILE, "capabilities": ()}
#: 英文可以缺失的位置与原因（其余位置必须给出英文）
ALIASES = ("/interests/leisure_categories/*/aliases/*", "/interests/cuisines/*/aliases/*")
USER_FRAGMENT = (
    "/statements/*/message_zh",
    "/issues/*/message_zh",
    "/leisure_interest/message_zh",
    "/taste/message_zh",
    "/items/*/note",
    "/interests/scope_zh/*",
)


def _payload(name: str) -> Any:
    return json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))["payload"]


def _bare(value: Any) -> Any:
    """去掉每个 ``meta`` 里的样例出处标签（``meta.sample``），其余原样。"""
    if isinstance(value, dict):
        return {
            k: {kk: _bare(vv) for kk, vv in v.items() if kk != "sample"}
            if k == "meta" and isinstance(v, dict)
            else _bare(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_bare(v) for v in value]
    return value


def _dump(message: Any) -> Any:
    return message.model_dump(mode="json")


@pytest.fixture
def scenarios(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """``product.scenarios`` 的工作目录只放资料包（审阅与确认只读它），其余代码路径不变。"""
    from product import scenarios as sc

    def workspace(work: Path) -> Path:
        for rel in (E3A, E3B):
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / rel, tmp_path / rel)
        return tmp_path

    monkeypatch.setattr(sc, "workspace", workspace)
    return sc


def _normal(pack: DataPack) -> tuple[Any, Any]:
    """正常闭环的前半段（``scenarios.closed_loop`` 里审阅与确认的那几步）。"""
    from product.scenarios import label

    sample = label(
        "offline_run",
        "product.scenarios.closed_loop(normal)",
        "离线闭环实际执行的结果（mock 规划器，零网络）",
    )
    draft = make_draft(pack, draft_id="draft.u501", sample=sample)
    review = review_draft(pack, draft, sample=sample)
    confirmed = confirm_profile(
        pack, draft, person_ref="u501", acknowledged=review.pending_confirmations, sample=sample
    )
    return review, confirmed


# ---------------------------------------------------------------------- 中文与提交的样例逐字相同


def test_capabilities_chinese_is_unchanged(scenarios: Any, tmp_path: Path) -> None:
    caps = scenarios.capabilities(tmp_path / "caps")
    assert _bare(_dump(caps)) == _bare(_payload("01_capabilities"))


def test_normal_review_and_confirmed_chinese_is_unchanged() -> None:
    review, confirmed = _normal(load_pack(REPO / E3A))
    assert _bare(_dump(review)) == _bare(_payload("03_normal_review"))
    assert _bare(_dump(confirmed)) == _bare(_payload("04_normal_confirmed"))


@pytest.mark.parametrize("sample", sorted(SAMPLE_CASES))
def test_review_only_samples_chinese_is_unchanged(
    scenarios: Any, tmp_path: Path, sample: str
) -> None:
    case, part = SAMPLE_CASES[sample]
    s = scenarios.review_only(tmp_path / case, case, **REVIEW_ONLY[case])
    assert _bare(_dump(getattr(s, part))) == _bare(_payload(sample))


def test_duplicate_answers_review_chinese_is_unchanged(scenarios: Any, tmp_path: Path) -> None:
    s = scenarios.duplicate_answers(tmp_path / "duplicates")
    assert _bare(_dump(s.review)) == _bare(_payload("38_duplicate_answers_rejected_review"))


def test_service_review_and_confirm_reply_chinese_is_unchanged() -> None:
    """服务样例：审阅的是 64 保存的那一版草稿；68 确认的是同一份答案的另一份草稿（只有编号不同，内容身份核对通过才比较）。"""
    pack = load_pack(REPO / E3A)
    saved = UserInputDraft.model_validate(_payload("64_service_draft_saved")["draft"])
    reply = _payload("66_service_review_reply")
    assert reply["review"]["draft"]["draft_fingerprint"] == draft_fingerprint(saved)
    assert _bare(_dump(review_draft(pack, saved))) == _bare(reply["review"])

    request, answer = _payload("67_service_confirm_request"), _payload("68_service_confirm_reply")
    other = saved.model_copy(update={"draft_id": request["draft_id"]})
    assert answer["confirmed"]["draft"]["draft_fingerprint"] == draft_fingerprint(other)
    confirmed = confirm_profile(
        pack,
        other,
        person_ref=answer["confirmed"]["person_ref"],
        acknowledged=request["acknowledged"],
    )
    assert answer["status"] == "confirmed" and answer["issues"] == []
    assert _bare(_dump(confirmed)) == _bare(answer["confirmed"])


# ---------------------------------------------------------------------- 英文覆盖（两个资料包）


def _messages(pack: DataPack) -> dict[str, tuple[Callable[[], Any], str]]:
    """这个资料包的输入侧消息：名称 →（生成函数，用户原文的种类）。"""

    def review_only(case: str, part: str) -> Callable[[], Any]:
        def build() -> Any:
            kwargs = dict(REVIEW_ONLY[case])
            draft = make_draft(pack, draft_id=f"draft.u501.{case}", **kwargs)
            review = review_draft(pack, draft)
            if part == "review":
                return review
            return confirm_profile(
                pack, draft, person_ref="u501", acknowledged=review.pending_confirmations
            )

        return build

    out: dict[str, tuple[Callable[[], Any], str]] = {
        "capabilities": (lambda: build_capabilities(pack), "capabilities"),
        "normal_review": (lambda: _normal(pack)[0], "review"),
        "normal_confirmed": (lambda: _normal(pack)[1], "confirmed"),
    }
    for name, (case, part) in SAMPLE_CASES.items():
        out[name] = (review_only(case, part), part)
    return out


def _coverage(pack: DataPack, build: Callable[[], Any], kind: str) -> dict[str, Any]:
    res = source_resources(pack.fingerprint, pack.body.pack_id)
    with recording() as rec:
        message = build()
    return coverage(_dump(message), rec, res, user_text=USER_TEXT[kind])


@pytest.mark.parametrize("pack_path", [E3A, E3B])
def test_english_is_provided_or_missing_only_for_stated_reasons(pack_path: str) -> None:
    pack = load_pack(REPO / pack_path)
    for name, (build, kind) in _messages(pack).items():
        cov = _coverage(pack, build, kind)
        assert cov["displayable"] > 0, name
        for pattern, reasons in cov["missing_patterns"].items():
            assert set(reasons) <= {"no_binding", "untranslated_param"}, (name, pattern, reasons)
            if "no_binding" in reasons:
                assert pattern in ALIASES, (name, pattern, reasons)
            if "untranslated_param" in reasons:
                assert pattern in USER_FRAGMENT, (name, pattern, reasons)


@pytest.mark.parametrize("pack_path", [E3A, E3B])
def test_capabilities_are_fully_translated_except_vocabulary_aliases(pack_path: str) -> None:
    """词表别名不生成显示文字；与某个类别名称同字的别名（例如“音乐”）在同一份消息里会对到那个名称的绑定。"""
    pack = load_pack(REPO / pack_path)
    cov = _coverage(pack, lambda: build_capabilities(pack), "capabilities")
    terms = (*pack.body.interests.leisure_categories, *pack.body.interests.cuisines)
    names = {term.label for term in terms}
    unbound = sum(
        1
        for term in terms
        for a in term.aliases
        if any("一" <= ch <= "鿿" for ch in a) and a not in names
    )
    assert cov["missing"] == ({"no_binding": unbound} if unbound else {})
    assert set(cov["missing_patterns"]) <= set(ALIASES)
    assert cov["provided"] == cov["displayable"] - unbound


@pytest.mark.parametrize("pack_path", [E3A, E3B])
def test_normal_review_and_confirmed_are_fully_translated(pack_path: str) -> None:
    """正常填写（用户原文只在用户原文的位置上）：审阅与确认画像的每一处说明都有英文。"""
    pack = load_pack(REPO / pack_path)
    review = _coverage(pack, lambda: _normal(pack)[0], "review")
    confirmed = _coverage(pack, lambda: _normal(pack)[1], "confirmed")
    assert review["status"] == "complete" and review["missing"] == {}, review
    assert confirmed["status"] == "complete" and confirmed["missing"] == {}, confirmed


def _english(message: Any, rec: Any, pack: DataPack, user_text: tuple[str, ...]) -> dict[str, Any]:
    from product.display.render import BindingSet, display_fields
    from product.display.text import bind_json

    res = source_resources(pack.fingerprint, pack.body.pack_id)
    body = _dump(message)
    bset = BindingSet(tuple(bind_json(body, rec, user_text=user_text)), res, res.resource_version)
    fields, _, _ = display_fields(body, [bset], "en", user_text=user_text)
    return {f["pointer"]: f["text"] for f in fields}


def test_nested_templates_keep_the_old_chinese_and_render_english() -> None:
    """没有公共样例覆盖的两种拼接：职业候选超过上限的说明、点数解析错误前面加细项名称。"""
    pack = load_pack(REPO / E3A)
    draft = make_draft(
        pack,
        occupation_text="收银、店员、文员、会计、记账、咖啡师",
        selected=None,
        material={
            "housing_condition": "16.555",
            "food_life": "25",
            "daily_convenience": "30",
            "personal_enjoyment": "20",
        },
    )
    with recording() as rec:
        review = review_draft(pack, draft)
    assert review.occupation.message_zh == (
        "这段描述对应不止一种职业：办公室文员、门店销售员、收银员、柜台点单与饮品制作。"
        "请选出你主要做的那一种（还有更多匹配，请写得更具体）"
    )
    parse = next(i for i in review.issues if i.code == "points_too_many_decimals")
    assert parse.message_zh == (
        "居住条件：“16.555”有 3 位小数，本版手工输入最多 2 位（步长不是测量精度）"
    )
    en = _english(review, rec, pack, messages.REVIEW)
    assert en["/occupation/message_zh"] == (
        "This description matches more than one occupation: Office clerk, Retail salesperson, "
        "Cashier, Counter service and drink making. Please choose the one you mainly do "
        "(there are more matches; please be more specific)"
    )
    at = review.issues.index(parse)
    assert en[f"/issues/{at}/message_zh"] == (
        "Housing condition: “16.555” has 3 decimal places; manual entry in this version allows "
        "at most 2 (the step is not a measurement precision)"
    )


def test_refused_confirm_reply_is_translated_without_copying_user_values() -> None:
    """确认被拒的即时回复（英文不照抄原文片段参数）：审阅里的阻断问题与知悉项缺失都有英文。"""
    from common.product_contracts import ProfileConfirmReply
    from product.answers import ConfirmationRefusedError
    from product.display.render import BindingSet, display_fields
    from product.display.text import bind_json

    pack = load_pack(REPO / E3A)
    res = source_resources(pack.fingerprint, pack.body.pack_id)
    with recording() as rec:
        with pytest.raises(ConfirmationRefusedError) as refused:
            confirm_profile(pack, make_draft(pack, age=None), person_ref="u501", acknowledged=())
        reply = ProfileConfirmReply(meta=pack.meta(), status="refused", issues=refused.value.issues)
    body = _dump(reply)
    bset = BindingSet(tuple(bind_json(body, rec)), res, res.resource_version)
    _fields, status, stats = display_fields(body, [bset], "en", raw_params=False)
    assert status == "complete", stats
    codes = {i.code for i in refused.value.issues}
    assert {"age_missing", "acknowledgement_missing"} <= codes


def test_draft_view_texts_are_translated_and_answers_stay_original() -> None:
    """草稿视图里应展示的只有资料包说明；用户的答案保持原文，不绑定、不算覆盖。"""
    from common.product_contracts import DraftView

    pack = load_pack(REPO / E3A)
    with recording() as rec:
        view = DraftView(
            meta=pack.meta(),
            draft=make_draft(pack),
            updated_at="2026-10-24T09:00:00Z",
            expires_at="2026-11-23T09:00:00Z",
        )
    res = source_resources(pack.fingerprint, pack.body.pack_id)
    cov = coverage(_dump(view), rec, res, user_text=messages.under("/draft", messages.DRAFT))
    assert cov["status"] == "complete" and cov["displayable"] == 2, cov


def test_user_text_is_never_bound_or_counted() -> None:
    """职业描述与从里面认出的词是用户原文：不绑定、不算覆盖；系统的解释照常有英文。"""
    from product.display.text import bind_json

    pack = load_pack(REPO / E3A)
    draft = make_draft(pack, occupation_text="我在咖啡店做咖啡师", selected="occ.counter_drinks")
    with recording() as rec:
        review = review_draft(pack, draft)
    body = _dump(review)
    pointers = {b["pointer"] for b in bind_json(body, rec, user_text=messages.REVIEW)}
    assert "/occupation/raw_text" not in pointers
    assert not any("/matched_terms" in p for p in pointers)
    res = source_resources(pack.fingerprint, pack.body.pack_id)
    cov = coverage(body, rec, res, user_text=messages.REVIEW)
    assert cov["status"] == "complete", cov
    # 不排除用户原文时，这几处就成了没有绑定的位置（陈述的 raw_text 整列都按原文处理，包括受支持的硬要求）
    counted = coverage(body, rec, res)
    assert counted["displayable"] == cov["displayable"] + 3
    assert counted["missing_patterns"] == {
        "/occupation/raw_text": {"no_binding": 1},
        "/occupation/candidates/*/matched_terms/*": {"no_binding": 1},
        "/statements/*/raw_text": {"no_binding": 1},
    }
    en = _english(review, rec, pack, messages.REVIEW)
    assert en["/occupation/message_zh"] == (
        "It looks like your work falls under “Counter service and drink making”; "
        "please confirm whether this reading is right"
    )
    assert en["/occupation/candidates/0/label"] == "Counter service and drink making"
