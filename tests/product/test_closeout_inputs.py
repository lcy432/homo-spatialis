"""E3-A 收尾 F3、F4：现金严格解析；重复或冲突的答案在确认之前拒绝（顺序反过来也一样），编译期的画像契约失败变成可展示的拒绝。"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from common.product_contracts import UserInputDraft
from product.answers import ConfirmationRefusedError, confirm_profile, review_draft
from product.fixtures import make_draft, prepare_workspace
from product.pack import DataPack, load_pack
from product.preflight import run_preflight

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def ws(tmp_path_factory) -> Path:
    return prepare_workspace(tmp_path_factory.mktemp("closeout_inputs"), REPO)


@pytest.fixture(scope="module")
def pack(ws) -> DataPack:
    with contextlib.chdir(ws):
        return load_pack()


def _codes(review) -> list[str]:
    return [i.code for i in review.issues]


# ---------------------------------------------------------------------- F3 金额


@pytest.mark.parametrize(
    ("text", "minor"),
    [("3000.50", 300050), ("3,000.50", 300050), ("3,000", 300000), ("1,234,567.89", 123456789)],
)
def test_plain_and_strict_thousands_formats_give_the_same_exact_amount(pack, text, minor):
    review = review_draft(pack, make_draft(pack, cash=(text, "USD", "2024")))
    assert review.confirmable and review.money.converted.amount_minor == minor
    confirmed = confirm_profile(
        pack,
        make_draft(pack, cash=(text, "USD", "2024")),
        person_ref="u1",
        acknowledged=review.pending_confirmations,
    )
    assert confirmed.money.amount_text == text and confirmed.money.converted.amount_minor == minor


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("3000,50", "money_separator_invalid"),  # 以前删掉逗号变成 300,050 美元
        ("3,,000", "money_separator_invalid"),
        ("1,23.45", "money_separator_invalid"),
        ("3,000,00", "money_separator_invalid"),
        (",300", "money_separator_invalid"),
        ("-5", "money_negative"),
        ("-3,000", "money_negative"),
        ("3000.505", "money_too_many_decimals"),
        ("3 千", "money_not_a_number"),
    ],
)
def test_wrong_separators_negatives_and_extra_precision_are_rejected(pack, text, code):
    review = review_draft(pack, make_draft(pack, cash=(text, "USD", "2024")))
    found = [i for i in review.issues if i.code == code]
    assert len(found) == 1 and not review.confirmable and review.money.converted is None
    assert found[0].field == "cash.amount_text" and found[0].severity == "error"
    assert found[0].next_action_zh and found[0].message_zh
    with pytest.raises(ConfirmationRefusedError):
        confirm_profile(
            pack,
            make_draft(pack, cash=(text, "USD", "2024")),
            person_ref="u1",
            acknowledged=review.pending_confirmations,
        )


def test_the_separator_message_tells_the_user_how_to_correct_it(pack):
    review = review_draft(pack, make_draft(pack, cash=("3000,50", "USD", "2024")))
    found = next(i for i in review.issues if i.code == "money_separator_invalid")
    assert "3000.50" in found.next_action_zh and "3,000.50" in found.next_action_zh
    assert "300,050" in found.message_zh  # 写明为什么不猜：删掉逗号会变成另一笔钱


# ---------------------------------------------------------------------- F4 重复与冲突


def _raw(pack, **kwargs):
    return make_draft(pack, **kwargs).model_dump(mode="json")


def _refused(pack, raw) -> tuple[list, set[str]]:
    draft = UserInputDraft.model_validate(raw)
    review = review_draft(pack, draft)
    with pytest.raises(ConfirmationRefusedError) as refused:
        confirm_profile(pack, draft, person_ref="u1", acknowledged=review.pending_confirmations)
    assert not review.confirmable
    return review.issues, {i.code for i in refused.value.issues}


@pytest.mark.parametrize("pair", [("disagree", "agree"), ("agree", "disagree"), ("agree", "agree")])
def test_two_answers_to_one_health_question_are_refused_in_either_order(pack, pair):
    raw = _raw(pack)
    qid = raw["health"]["items"][0]["question_id"]
    raw["health"]["items"][0]["choice"] = pair[0]
    raw["health"]["items"].append({"question_id": qid, "choice": pair[1]})
    issues, refused = _refused(pack, raw)
    assert "health_answer_duplicated" in refused
    dup = next(i for i in issues if i.code == "health_answer_duplicated")
    assert dup.field == f"health.items.{qid}" and "不按最后一个算" in dup.message_zh
    review = review_draft(pack, UserInputDraft.model_validate(raw))
    shown = next(h for h in review.health if h.question_id == qid)
    assert shown.option is None and shown.answer_state == "unanswered"  # 不替用户选一个
    others = [h for h in review.health if h.question_id != qid]
    assert [h.answer_state for h in others] == ["provided", "unsure"]  # 其他题的含义不变


def test_the_rejection_does_not_depend_on_the_order_of_the_answers(pack):
    def issues_for(a, b):
        raw = _raw(pack)
        qid = raw["health"]["items"][0]["question_id"]
        raw["health"]["items"][0]["choice"] = a
        raw["health"]["items"].append({"question_id": qid, "choice": b})
        return [(i.code, i.field, i.message_zh) for i in _refused(pack, raw)[0]]

    assert issues_for("disagree", "agree") == issues_for("agree", "disagree")


@pytest.mark.parametrize(
    "states", [("provided", "unsure"), ("unsure", "provided"), ("provided", "provided")]
)
def test_a_repeated_or_conflicting_ability_is_refused_and_not_granted(pack, states):
    raw = _raw(pack, occupation_text="我在事务所做会计", selected="occ.accountant")
    raw["abilities"] = [{"ability_id": "accounting_cert", "state": s} for s in states]
    _issues, refused = _refused(pack, raw)
    assert "ability_duplicated" in refused
    review = review_draft(pack, UserInputDraft.model_validate(raw))
    assert "accounting_cert" not in review.abilities  # 不按“出现一次已提供就算持有”


@pytest.mark.parametrize(
    ("ids", "values"),
    [
        (("hr.commute", "hr.commute2"), (60, 45)),
        (("hr.commute2", "hr.commute"), (45, 60)),
        (("hr.commute", "hr.commute"), (60, 45)),
        (("hr.commute", "hr.commute2"), (60, 60)),
    ],
)
def test_two_requirements_for_the_same_e1_need_are_refused_not_merged(pack, ids, values):
    raw = _raw(pack)
    raw["hard_requirements"] = [
        {"requirement_id": rid, "option_id": "housing.max_commute_roundtrip", "parameter_value": v}
        for rid, v in zip(ids, values, strict=True)
    ]
    issues, refused = _refused(pack, raw)
    assert "hard_requirement_conflict" in refused  # 按 E1 需求比较，不只比较编号
    if ids[0] == ids[1]:
        assert "hard_requirement_duplicated" in refused
    conflict = next(i for i in issues if i.code == "hard_requirement_conflict")
    assert "不自动取更严格的上限" in conflict.message_zh


def test_a_profile_contract_failure_at_compile_is_a_structured_refusal(pack, ws, tmp_path):
    """审阅之外的入口（例如别处构造的、身份照样封好的确认画像）带着同一需求两条硬要求进来：预检查返回可展示的拒绝与审计编号，
    原文进编译目录的内部诊断，不抛出 ValidationError。"""
    with contextlib.chdir(ws):
        draft = make_draft(pack)
        review = review_draft(pack, draft)
        confirmed = confirm_profile(
            pack, draft, person_ref="u1", acknowledged=review.pending_confirmations
        )
        twice = confirmed.hard_requirements * 2
        forged = confirmed.model_copy(update={"hard_requirements": twice})
        forged = forged.model_copy(update={"content_fingerprint": forged.expected_fingerprint()})
        out = Path("compiled/forged")
        pre = run_preflight(pack, forged, out, repo_root=Path("."))
        assert pre.preflight.status == "blocked"
        found = pre.preflight.issues[0]
        assert found.code == "profile_contract_rejected" and found.audit_ref
        saved = json.loads((out / "diagnostics" / f"{found.audit_ref}.json").read_text("utf-8"))
        assert "需求" in saved["detail"] and "ValidationError" not in found.model_dump_json()
