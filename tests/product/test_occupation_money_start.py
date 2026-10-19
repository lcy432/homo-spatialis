"""E3-A 模块 B/C：职业匹配（明确 / 歧义 / 未知，不凭职称授资格）、金额口径与标准开局（跨城个人条件一致，非法旧物与病史被拒）。"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from common.life_contracts import IndependentHealthInputDeclaration
from ledger.life_catalog import load_life_bundle
from ledger.life_roster import common_start_digest
from ledger.life_scoring_config import load_scoring_config
from product import scenarios as sc
from product.answers import review_draft
from product.fixtures import make_draft, prepare_workspace
from product.occupation import match_occupation
from product.pack import DataPack, load_pack

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pack(tmp_path_factory) -> DataPack:
    work = prepare_workspace(tmp_path_factory.mktemp("ws"), REPO)
    with contextlib.chdir(work):
        return load_pack()


# ---------------------------------------------------------------------- 职业


def test_occupation_matching_explicit_ambiguous_and_unknown(pack):
    m = match_occupation(pack, "我在写字楼做全职文员")
    assert m.status == "explicit" and [c.candidate_id for c in m.candidates] == ["occ.office_clerk"]
    m = match_occupation(pack, "我在餐厅做店员，有时收银，有时做饮品")
    assert m.status == "ambiguous"
    assert {c.candidate_id for c in m.candidates} == {
        "occ.retail_sales",
        "occ.cashier",
        "occ.counter_drinks",
    }
    m = match_occupation(pack, "我是宇航员")
    assert m.status == "unknown" and m.candidates == () and "普通白领" in m.message_zh


def test_candidates_shown_to_users_carry_no_classification_codes(pack):
    m = match_occupation(pack, "我在餐厅做店员，有时收银，有时做饮品")
    text = json.dumps([c.model_dump(mode="json") for c in m.candidates], ensure_ascii=False)
    assert "SOC" not in text and "41-2031" not in text and "41-2011" not in text


def test_selection_must_be_one_of_the_current_candidates(pack):
    review = review_draft(
        pack,
        make_draft(pack, occupation_text="我在餐厅做店员，有时收银", selected="occ.accountant"),
    )
    assert (
        "occupation_selection_invalid" in [i.code for i in review.issues] and not review.confirmable
    )
    review = review_draft(
        pack, make_draft(pack, occupation_text="我在餐厅做店员，有时收银", selected=None)
    )
    assert "occupation_needs_confirmation" in [i.code for i in review.issues]


def test_a_job_title_does_not_grant_a_qualification(tmp_path):
    s = sc.preflight_only(
        tmp_path / "acc",
        "accountant",
        occupation_text="我在事务所做会计",
        selected="occ.accountant",
    )
    assert s.pre.preflight.status == "blocked"
    assert {i.code for i in s.pre.preflight.issues} == {"start_job_requires_unconfirmed_ability"}
    ok = sc.preflight_only(
        tmp_path / "acc2",
        "accountant_cert",
        occupation_text="我在事务所做会计",
        selected="occ.accountant",
        abilities=("accounting_cert",),
    )
    assert ok.pre.preflight.status == "ready", [i.message_zh for i in ok.pre.preflight.issues]
    with contextlib.chdir(ok.work):
        bundle, _ = load_life_bundle(
            ok.pre.compiled.paths["life_input"], require_settlement_ready=True
        )
    for s0 in bundle.initial_states:
        assert [(a.ability_id, a.source) for a in s0.abilities] == [("accounting_cert", "initial")]
        assert s0.employment.current_position_ids[0].endswith("job.accountant")


def test_an_occupation_without_a_local_catalog_job_is_blocked_not_replaced(tmp_path):
    s = sc.preflight_only(
        tmp_path / "cashier", "cashier", occupation_text="我在超市做收银", selected="occ.cashier"
    )
    assert s.pre.preflight.status == "blocked"
    assert [i.code for i in s.pre.preflight.issues] == [
        "start_job_unmatched",
        "start_job_unmatched",
    ]


# ---------------------------------------------------------------------- 金额


@pytest.mark.parametrize(
    ("cash", "code"),
    [
        (
            ("10000", "USD", "2026-10"),
            "money_conversion_missing",
        ),  # “当前 1 万美元”不能直接改标签成 2024 年美元
        (("3000", "EUR", "2024"), "money_conversion_missing"),
        (("3000.001", "USD", "2024"), "money_too_many_decimals"),
        (("-5", "USD", "2024"), "money_negative"),
        (
            ("3000,50", "USD", "2024"),
            "money_separator_invalid",
        ),  # 收尾 F3：不再删掉逗号变成 300,050
        (("3,,000", "USD", "2024"), "money_separator_invalid"),
        (("1,23.45", "USD", "2024"), "money_separator_invalid"),
        ((None, None, None), "cash_missing"),
    ],
)
def test_money_without_a_legal_conversion_is_blocked(pack, cash, code):
    review = review_draft(pack, make_draft(pack, cash=cash))
    assert code in [i.code for i in review.issues] and not review.confirmable
    assert review.money.converted is None


def test_money_keeps_the_original_amount_period_and_rule(tmp_path):
    s = sc.review_only(tmp_path, "money_ok", cash=("3,250.50", "USD", "2024"))
    m = s.confirmed.money
    assert (m.amount_text, m.currency, m.price_period, m.conversion_rule) == (
        "3,250.50",
        "USD",
        "2024",
        "identity_same_basis_v1",
    )
    assert m.converted.amount_minor == 325050 and m.converted.numeraire_id == "USD_2024_LOCAL"


# ---------------------------------------------------------------------- 编译与标准开局


@pytest.fixture(scope="module")
def two_repeats(tmp_path_factory):
    work = tmp_path_factory.mktemp("repeats")
    s = sc.closed_loop(
        work,
        "repeats",
        person="u601",
        repeat_indexes=(0, 1),
        draft_kwargs={"hard_requirements": ()},
        stop_after_calls=0,
    )
    return s


def test_compiled_bundle_uses_the_product_protocol_and_the_unchanged_scoring_content(two_repeats):
    s = two_repeats
    with contextlib.chdir(s.work):
        bundle, report = load_life_bundle(
            s.pre.compiled.paths["life_input"], require_settlement_ready=True
        )
        scoring, _ = load_scoring_config(s.pre.compiled.paths["scoring_config"], bundle)
    p = bundle.protocol
    assert p.protocol_id == "life-v1-e3a-product" and p.scoring_scale_version == "e1-convenience-v1"
    assert isinstance(p.health_input, IndependentHealthInputDeclaration)
    assert p.energy_feedback.preference_source == "product_confirmed_six_dim"
    assert (
        scoring.spec.content_fingerprint
        == "f209137b1101407427724a8d7f3009fb09eca8a8c11884114412feea6fdcd690"
    )
    assert bundle.stated_profiles == () and bundle.internal_truths == ()  # 产品路径没有研究真值
    assert [x.person_id for x in bundle.product_profiles] == ["u601"]
    assert report.contract_valid and report.settlement_ready


def test_standard_start_is_identical_across_cities_and_repeat_indexes(two_repeats):
    s = two_repeats
    with contextlib.chdir(s.work):
        bundle, _ = load_life_bundle(
            s.pre.compiled.paths["life_input"], require_settlement_ready=True
        )
    states = bundle.initial_states
    assert {st.clone_id for st in states} == {
        "u601.k51.r0",
        "u601.k51.r1",
        "u601.m52.r0",
        "u601.m52.r1",
    }
    digests = {
        json.dumps(common_start_digest(st), sort_keys=True, ensure_ascii=False) for st in states
    }
    assert len(digests) == 1  # 年龄、现金口径、能力、基础时间与精力都一样
    for st in states:
        assert st.holdings == () and st.training == () and st.savings_goals == ()
        assert st.health.episode is None and st.health.history == ()
        assert st.cash.amount == 3000.0 and st.health.age_years == 35
    jobs = {st.clone_id: st.employment.current_position_ids for st in states}
    assert jobs["u601.k51.r0"] == ("k51.job.clerk",) and jobs["u601.m52.r1"] == ("m52.job.clerk",)


def _mutated(tmp_path, name, mutate):
    return sc.preflight_only(
        tmp_path / name, name, origin="controlled_fixture", mutate_life_input=mutate
    )


def test_illegal_start_items_illness_history_and_training_are_rejected_by_e1(tmp_path):
    def holding(d):
        d["initial_states"][0]["holdings"] = [
            {
                "opportunity_id": "k51.durable.convenience_pack",
                "quantity": 1,
                "acquired_year": 0,
                "remaining_months": 24,
            }
        ]

    def illness(d):
        d["initial_states"][0]["health"]["history"] = [
            {
                "episode_id": "ep.old",
                "severity": "minor",
                "started_year": 1,
                "started_month": 1,
                "months_ill": 1,
                "ended": True,
                "treated": False,
            }
        ]

    def training(d):
        d["initial_states"][0]["training"] = [
            {
                "opportunity_id": "k51.training.accounting_course",
                "months_participated": 3,
                "months_required": 12,
                "fee_paid": True,
                "enrolled_year": 1,
                "enrolled_month": 1,
            }
        ]

    s = _mutated(tmp_path, "holding", holding)
    assert s.pre.preflight.status == "blocked"
    found = s.pre.preflight.issues[0]
    # 收尾 F5：公开消息只有类别说明与审计编号，E1 的拒绝原文（标准开局检查、内部文件名）只在内部诊断里
    assert found.code == "e1_preflight_refused" and found.audit_ref
    public = found.model_dump_json()
    assert "holdings" not in public and "compiled" not in public and ":\\" not in public
    internal = json.loads(
        (s.work / "compiled/holding/diagnostics" / f"{found.audit_ref}.json").read_text(
            encoding="utf-8"
        )
    )
    assert "holdings" in internal["detail"] and "标准开局" in internal["detail"]  # 不删人、不改状态
    assert s.pre.e1_refusal is not None and "holdings" in s.pre.e1_refusal
    # 历史培训进度与病史：E1 在更早的结构 / 跨对象检查里就拒绝了（第 1 年开局不可能有它们）；哪一层拒绝都行，不能放行
    for name, mutate in (("training", training), ("illness", illness)):
        s = _mutated(tmp_path, name, mutate)
        assert s.pre.preflight.status == "blocked"
        assert s.pre.preflight.issues[0].code == "e1_preflight_refused"
