"""E3-A 模块 A：共享结构是唯一权威来源；导出的 schema 与它一致；公共样例按结构校验、带合成出处、不含内部路径或凭证。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from common.product_contracts import (
    PRODUCT_MESSAGES,
    CityResult,
    ConfirmedMaterial,
    PointEntry,
    ProductResult,
    RunReceipt,
    UserInputDraft,
)

REPO = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO / "contracts/product/v3"


def _cli():
    spec = importlib.util.spec_from_file_location(
        "product_e3a_cli", REPO / "scripts/product_e3a.py"
    )
    assert spec is not None and spec.loader is not None
    sys.path.insert(0, str(REPO / "scripts"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_message_exports_a_schema_and_the_committed_files_match():
    cli = _cli()
    texts = cli.schema_texts()
    assert set(texts) == {f"{n}.schema.json" for n in PRODUCT_MESSAGES} | {"index.json"}
    for name, text in texts.items():
        path = SCHEMA_DIR / name
        assert path.exists(), f"缺少 {name}：先运行 export-schema"
        assert path.read_text(encoding="utf-8") == text, f"{name} 与权威结构不一致"
    index = json.loads(texts["index.json"])
    # 输入体验收敛（DECISIONS/0046）：输入目录、结构化资格答案、面板多选；新问卷不再收集硬要求与属性偏好
    # 输入与服务体验（DECISIONS/0047）：独立评分、设置预览与一次开始、进度摘要；目录与生活协议不换版
    assert index["schema_version"] == "e3a-product-schema-v3.5"
    assert index["questionnaire_version"] == "e3a-questionnaire-v0.5-draft"
    assert index["vocabulary_version"] == "e3a-vocabulary-v5"
    assert index["conversion_version"] == "e3a-conversion-v4"
    assert index["health_questionnaire_version"] == "e3a-health-coping-q-v0.1-draft"


def test_check_schema_entry_passes_on_the_committed_contracts():
    assert _cli().check_schema(SCHEMA_DIR) == 0


def test_public_samples_validate_and_cover_the_required_cases():
    cli = _cli()
    samples = sorted((SCHEMA_DIR / "samples").glob("*.json"))
    names = {p.stem for p in samples}
    for required in (
        "02_normal_draft",
        "10_normal_result",
        "11_occupation_ambiguous_review",
        "12_unanswered_and_zero_review",
        "13_equal_split_review",
        "15_health_unsure_confirmed",
        "16_legacy_laundry_review",
        "17_attributes_and_home_entry_review",
        "18_catalog_compilation",
        "19_option_inventory_coverage",
        "20_catalog_boundary_compilation",
        "21_no_purchase_result",
        "22_partial_service_months",
        "23_mixed_cem_result",
        "24_all_exit_result",
        "25_not_generated_result",
        "27_paused_progress",
        "28_standard_start_rejected_preflight",
        "08_duplicate_receipt",
        "32_contract_example_disconnect_query",
        "34_positive_weight_data_unknown_preflight",
        "35_catalog_statuses_compilation",
        "36_catalog_statuses_coverage",
        "37_money_separator_rejected_review",
        "38_duplicate_answers_rejected_review",
        # 托管服务（E3C）：正常、阻断、排队、运行中、部分结果、次数用完、暂停、删除、过期
        "61_service_session",
        "71_service_intent_awaiting_confirmation",
        "73_service_submit_accepted",
        "74_service_submit_duplicate",
        "76_service_intent_running",
        "77_service_progress_running",
        "78_service_intent_finished",
        "81_service_intent_blocked",
        "82_service_intent_paused",
        "84_service_partial_result",
        "85_service_error_quota_exhausted",
        "86_service_deletion_receipt",
        "87_service_intent_deleted",
        "90_service_intent_expired",
        # 新输入流程（v3.4）：输入目录、按目录选职业与资格题、面板多选、定和分配、拒绝与旧草稿迁移
        "129_service_capabilities_input_catalog_e3b",
        "131_service_draft_catalog_selection",
        "132_service_review_catalog_selection",
        "134_service_confirm_catalog_selection",
        "142_service_review_allocation_equal_split",
        "147_service_review_allocation_parent_zero",
        "148_service_draft_migration_needed",
        "151_service_draft_migrate_request",
        "153_service_draft_migrated",
        # 独立评分（v3.5，DECISIONS/0047）：原始评分 → 精确权重、同比例、全零、超范围、父项为 0 与恢复、旧方式停用、迁移要选择
        "143_service_review_ratings_normalized",
        "145_service_review_ratings_all_zero",
        "146_service_review_ratings_out_of_range",
        "155_service_draft_ratings",
        "156_service_review_ratings_scaled",
        "157_service_review_allocation_mode_retired",
        "158_service_review_ratings_parent_restored",
        "159_service_error_migration_choice_required",
        "160_service_confirm_ratings_normalized",
        "161_display_review_ratings_en",
        # 一次开始（API v1.4，DECISIONS/0048）：预览、带授权的创建、检查中、按授权跑完、同键重试、拒绝与授权作废
        "162_service_start_preview_request",
        "163_service_start_preview",
        "164_service_start_preview_paid_off",
        "165_service_intent_create_start",
        "166_service_intent_start_checking",
        "167_service_intent_start_finished",
        "168_service_intent_start_retry",
        "169_service_error_start_idempotency_conflict",
        "170_service_error_start_consent_stale",
        "171_service_error_start_acknowledgement_missing",
        "172_service_error_start_already_authorized",
        "173_service_intent_start_void_conditions_changed",
        "174_service_intent_start_void_paid_off",
        "175_service_intent_start_blocked",
        "176_display_intent_start_checking_en",
        # 进度摘要（DECISIONS/0049）：模拟中、停下、结束但不是全部完成、取消，各带英文显示
        "177_service_intent_progress_simulating",
        "178_display_intent_progress_simulating_en",
        "179_display_intent_progress_stopped_en",
        "180_service_intent_progress_finished_partial",
        "181_display_intent_progress_finished_partial_en",
        "182_service_intent_progress_cancelled",
        "183_display_intent_progress_cancelled_en",
    ):
        assert required in names, f"缺少公共样例 {required}"
    for p in samples:
        assert cli.sample_problems(p) == [], p.name
    # 离线桥接时代的断线查询与服务暂停样例仍是标明为示例的契约响应；托管服务的样例来自服务端的真实代码路径
    for name in ("32_contract_example_disconnect_query", "33_contract_example_halted_progress"):
        payload = json.loads((SCHEMA_DIR / "samples" / f"{name}.json").read_text(encoding="utf-8"))[
            "payload"
        ]
        assert payload["meta"]["sample"]["origin"] == "contract_example"
    service = {
        p.stem: json.loads(p.read_text(encoding="utf-8")) for p in samples if "_service_" in p.stem
    }
    for name, body in service.items():
        payload = body["payload"]
        label = (payload.get("meta") or {}).get("sample") or body.get("sample")
        assert label["origin"] in ("offline_run", "controlled_fixture"), name
    # 测试传输的样例写明是测试传输；样例里的凭证都是明确无效的测试值
    for name in ("76_service_intent_running", "77_service_progress_running"):
        assert service[name]["payload"]["meta"]["sample"]["origin"] == "controlled_fixture"
        assert "测试传输" in service[name]["description_zh"]
    assert service["61_service_session"]["payload"]["csrf_token"].startswith("TEST_ONLY_invalid")
    token = service["60_service_session_exchange_request"]["payload"]["invite_token"]
    assert token.startswith("TEST_ONLY_invalid")
    assert (
        service["73_service_submit_accepted"]["payload"]["receipt"]["service"] == "hosted_service"
    )


def test_states_are_distinct_and_explicit_zero_is_a_provided_value():
    assert PointEntry(state="provided", value_text="0").value_text == "0"
    for state in ("unanswered", "indifferent", "unsure"):
        assert PointEntry(state=state).value_text is None
        with pytest.raises(ValueError):
            PointEntry(state=state, value_text="0")
    with pytest.raises(ValueError):
        PointEntry(state="provided")


def test_result_denominators_are_enforced_by_the_contract():
    base = dict(
        city_ref="US:SYNTH2026:K51",
        label="合成城 K51",
        planned=3,
        complete=1,
        exits=1,
        missing=1,
        mean_denominator=1,
        range_denominator=2,
        exit_share={"exact": "1/3", "display": "0.3333"},
        missing_share={"exact": "1/3", "display": "0.3333"},
        headline_zh="示例",
    )
    CityResult.model_validate(base)
    with pytest.raises(ValueError, match="C \\+ E \\+ M"):
        CityResult.model_validate({**base, "planned": 4})
    with pytest.raises(ValueError, match="分母必须是 C"):
        CityResult.model_validate({**base, "mean_denominator": 3})
    with pytest.raises(ValueError, match="C \\+ M"):
        CityResult.model_validate({**base, "range_denominator": 3})
    with pytest.raises(ValueError, match="C = 0"):
        CityResult.model_validate(
            {
                **base,
                "complete": 0,
                "missing": 2,
                "mean_denominator": 0,
                "mean_j": {"exact": "0", "display": "0"},
            }
        )


def test_material_zero_never_carries_a_fabricated_allocation():
    with pytest.raises(ValueError):
        ConfirmedMaterial(
            status="not_applicable_parent_zero",
            allocation={"mode": "manual", "points": {}, "display": {}},
        )


def test_receipts_and_results_keep_their_meaning():
    meta = json.loads(
        (SCHEMA_DIR / "samples/07_normal_receipt_accepted.json").read_text(encoding="utf-8")
    )["payload"]["meta"]
    with pytest.raises(ValueError):
        RunReceipt(
            meta=meta,
            status="rejected",
            run_id="run-x",
            idempotency_key="idem-12345678",
            service="offline_bridge",
        )
    with pytest.raises(ValueError):
        RunReceipt(
            meta=meta, status="accepted", idempotency_key="idem-12345678", service="offline_bridge"
        )
    payload = json.loads(
        (SCHEMA_DIR / "samples/25_not_generated_result.json").read_text(encoding="utf-8")
    )["payload"]
    result = ProductResult.model_validate(payload)
    assert (
        result.evaluation_status == "not_generated" and result.cities == () and result.clones == ()
    )


def test_unknown_fields_are_rejected():
    payload = json.loads((SCHEMA_DIR / "samples/02_normal_draft.json").read_text(encoding="utf-8"))[
        "payload"
    ]
    payload["healthcare_points"] = 10
    with pytest.raises(ValueError):
        UserInputDraft.model_validate(payload)
