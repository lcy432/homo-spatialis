"""E3-A 验收：新旧严格等价，健康回答独立。

* 等价：旧七维 27/18/18/9/9/9 + 医疗 10 与新六维 30/20/20/10/10/10，同一便利规则、同一计划、同一注入事件、同一开局与物质比例
  30/30/40——有效权重、逐月各维贡献、B、S、年均、反馈的未量化与量化额度全部 ``==``（不加容差）。均分对照（旧 15×6 + 10 与新
  50/3×6）同样相等。
* 健康独立：只改健康回答、行动与事件不变——账目、病情、B / S、精力额度逐位相同；疾病规则与它的内容身份不变；只有给分身看的请求
  里健康回答那一部分不同（以后的计划可以因此不同，这是允许的间接影响）。
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from agentlayer.life_planning import build_memory, build_request, request_payload
from agentlayer.life_views import ObservationPermission
from common.life_contracts import model_fingerprint
from ledger.life_catalog import load_life_bundle
from ledger.life_city_facts import load_city_facts
from ledger.life_scoring_config import load_scoring_config
from product.answers import confirm_profile, review_draft
from product.compile import compile_inputs
from product.equivalence import _life, _with_events, compare_old_new
from product.fixtures import K51, make_draft, prepare_workspace
from product.pack import load_pack

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return compare_old_new(work=tmp_path_factory.mktemp("eq"))


def test_old_seven_and_new_six_dimensions_score_identically(report):
    case = report["cases"]["main_30_20_20_10_10_10"]
    assert case["identical"], case["differences"]
    assert case["years_old"] == case["years_new"] == 3
    assert case["scoring_content_same"]
    assert case["settlement_outcomes_byte_identical"] == [True, True, True]
    y1 = case["years"][0]
    assert (
        y1["old"]["effective_weights"]
        == y1["new"]["effective_weights"]
        == {
            "material": 0.3,
            "safety": 0.2,
            "opportunity": 0.2,
            "leisure": 0.1,
            "environment": 0.1,
            "commute": 0.1,
        }
    )
    for y in case["years"]:
        for o, n in zip(y["old"]["months"], y["new"]["months"], strict=True):
            assert (o["u"], o["daily_life"], o["dimensions"]) == (
                n["u"],
                n["daily_life"],
                n["dimensions"],
            )
        assert (y["old"]["raw_quota"], y["old"]["new_quota"]) == (
            y["new"]["raw_quota"],
            y["new"]["new_quota"],
        )
    # 注入的疾病确实进了评分（第 1 年第 3 月小病，H = 0.5）
    assert y1["old"]["months"][2]["health"] == ("minor", 0.5, 0.2)


def test_equal_split_matches_the_old_equal_six(report):
    case = report["cases"]["equal_split"]
    assert case["identical"], case["differences"]
    assert set(case["new_points"].values()) == {"50/3"}
    assert set(case["years"][0]["new"]["effective_weights"].values()) == {1 / 6}


def test_the_records_differ_only_where_they_must(report):
    exp = report["cases"]["main_30_20_20_10_10_10"]["expected_differences"]
    assert exp["preference_source"] == ["product_stated_numeric", "product_confirmed_six_dim"]
    assert (
        "healthcare" in exp["raw_dimension_weights_old"]
        and "healthcare" not in exp["raw_dimension_weights_new"]
    )
    assert (exp["health_input_old"], exp["health_input_new"]) == (
        "health_risk_coping_v1",
        "independent_health_answers_v1",
    )


@pytest.fixture(scope="module")
def two_health_answers(tmp_path_factory):
    work = prepare_workspace(tmp_path_factory.mktemp("health"), REPO)
    out = {}
    with contextlib.chdir(work):
        pack = load_pack()
        for name, choices in (
            (
                "agree",
                {
                    "hc_more_pay_more_sick_risk": "agree",
                    "hc_reserve_for_health_costs": "agree",
                    "hc_wait_for_clear_risk_info": "agree",
                },
            ),
            (
                "blank",
                {
                    "hc_more_pay_more_sick_risk": None,
                    "hc_reserve_for_health_costs": None,
                    "hc_wait_for_clear_risk_info": None,
                },
            ),
        ):
            draft = make_draft(
                pack,
                draft_id=f"draft.u301.{name}",
                health_choices=choices,
                hard_requirements=(),
                free_text=None,
            )
            review = review_draft(pack, draft)
            confirmed = confirm_profile(
                pack, draft, person_ref="u301", acknowledged=review.pending_confirmations
            )
            compiled = compile_inputs(
                pack, confirmed, Path(f"compiled/{name}"), repo_root=Path(".")
            )
            bundle, _ = load_life_bundle(
                compiled.paths["life_input"], require_settlement_ready=True
            )
            scoring, _ = load_scoring_config(compiled.paths["scoring_config"], bundle)
            out[name] = (bundle, scoring, compiled)
        base, _ = load_life_bundle(
            Path(pack.body.e1_base.life_input), require_settlement_ready=True
        )
        facts = load_city_facts(
            Path(pack.body.e1_base.city_facts), out["agree"][0], out["agree"][1]
        )
    return work, out, base, facts


def test_health_answers_do_not_change_rules_ledgers_scores_or_energy(two_health_answers):
    work, out, base, _ = two_health_answers
    import product.equivalence as eq

    catalog = base.catalog_for(K51)
    eq._TAX[:] = [base.tax_policy(catalog.tax_policy_id)]
    state = base.initial_state("u301.k51.r0")
    lives = {}
    with contextlib.chdir(work):
        for name, (bundle, scoring, _) in out.items():
            protocol = _with_events(bundle.protocol)
            lives[name] = _life(
                protocol,
                state,
                catalog,
                scoring.spec,
                scoring.conditions_for(K51),
                bundle.product_profiles[0],
            )
    a, b = out["agree"][0], out["blank"][0]
    assert model_fingerprint(a.protocol) == model_fingerprint(
        b.protocol
    )  # 疾病规则、事件来源、反馈政策都没有变
    assert (
        a.protocol.health_rules.content_fingerprint == b.protocol.health_rules.content_fingerprint
    )
    assert len(lives["agree"]) == len(lives["blank"]) == 3
    for ra, rb in zip(lives["agree"], lives["blank"], strict=True):
        assert model_fingerprint(ra.settlement.outcome) == model_fingerprint(rb.settlement.outcome)
        assert [m.u for m in ra.score.months] == [m.u for m in rb.score.months]
        assert [m.daily_life for m in ra.score.months] == [m.daily_life for m in rb.score.months]
        assert [m.health for m in ra.score.months] == [m.health for m in rb.score.months]
        assert ra.score.annual_mean == rb.score.annual_mean
        assert (ra.feedback.raw_quota, ra.feedback.new_quota) == (
            rb.feedback.raw_quota,
            rb.feedback.new_quota,
        )
        if ra.next_state is not None:
            # 下一年状态只差反馈记录的指纹（它覆盖年度绑定，绑定里有画像的身份）——身份链接不同，生活与额度相同
            da, db = ra.next_state.model_dump(mode="json"), rb.next_state.model_dump(mode="json")
            assert da["energy_quota_basis"].pop("record_fingerprint") != db[
                "energy_quota_basis"
            ].pop("record_fingerprint")
            assert da == db


def test_health_answers_only_change_what_the_planner_reads(two_health_answers):
    work, out, _base, facts = two_health_answers
    permission = ObservationPermission(
        condition="product_stated_profile",
        show_stated_dimension_points=True,
        show_soft_preferences=True,
        show_hard_requirements=True,
        show_free_text=True,
        show_energy_level=True,
        show_stated_material_points=True,
        permission_version="e3a-product-v1",
        show_health_answers=True,
    )
    payloads = {}
    with contextlib.chdir(work):
        for name, (bundle, _, _) in out.items():
            catalog = bundle.catalog_for(K51)
            state = bundle.initial_state("u301.k51.r0")
            request = build_request(
                request_id=f"req/{name}",
                attempt=1,
                attempts_allowed=3,
                permission=permission,
                profile=bundle.product_profiles[0],
                state=state,
                catalog=catalog,
                protocol=bundle.protocol,
                facts=facts.sheet_for(K51),
                memory=build_memory(clone_id=state.clone_id, city=K51, current_year=1, years=[]),
                last_feedback=None,
                revision=None,
                tax_policy=bundle.tax_policy(catalog.tax_policy_id),
                prompt_template="e3a-product-prompt-v1",
            )
            payloads[name] = request_payload(request)
    pa, pb = payloads["agree"], payloads["blank"]
    pa["request_id"] = pb["request_id"] = "x"
    ha = pa["observation"]["profile"].pop("health_answers")
    hb = pb["observation"]["profile"].pop("health_answers")
    assert (
        pa == pb
    )  # 除了健康回答，分身看到的完全相同（目录里的便利机会由同一份资料编译，同样相同）
    assert [h["answer_state"] for h in ha] == ["provided"] * 3
    assert [h["answer_state"] for h in hb] == ["unanswered"] * 3
    assert all("没有这一项的明确指令" in h["planning_note"] for h in hb)
