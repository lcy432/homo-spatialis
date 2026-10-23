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
    assert index["schema_version"] == "e3a-product-schema-v3.1"  # E3B 的公开事实（DECISIONS/0042）
    assert index["conversion_version"] == "e3a-conversion-v2"  # 收尾 F3：金额写法规则变了
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
    ):
        assert required in names, f"缺少公共样例 {required}"
    for p in samples:
        assert cli.sample_problems(p) == [], p.name
    # 线上服务没有实现：断线查询与服务暂停只能是标明为示例的契约响应
    for name in ("32_contract_example_disconnect_query", "33_contract_example_halted_progress"):
        payload = json.loads((SCHEMA_DIR / "samples" / f"{name}.json").read_text(encoding="utf-8"))[
            "payload"
        ]
        assert payload["meta"]["sample"]["origin"] == "contract_example"


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
