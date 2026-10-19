"""E3-A 的保护：测试里任何真实调用都失败；公开错误只由登记过的类别生成（收尾 F5：不从异常原文拼文字，不靠正则删路径）；
离线桥接只接受 mock 规划器。"""

from __future__ import annotations

from typing import get_args

import pytest

from tests.product.conftest import LiveCallAttemptedError


def test_any_attempt_to_build_or_use_a_real_model_transport_fails():
    from agentlayer.life_llm import OpenAICompatibleTransport

    with pytest.raises(LiveCallAttemptedError):
        OpenAICompatibleTransport(
            api_key="placeholder", base_url="https://example.invalid", model="x"
        )  # type: ignore[call-arg]
    import openai

    with pytest.raises(LiveCallAttemptedError):
        openai.OpenAI(api_key="placeholder")


def test_every_e1_category_and_halt_reason_has_a_registered_public_text():
    """E1 的每一种 C / E / M 原因与批次停下原因都登记了公开说明；没登记的类别不会退回显示原文，而是直接报错。"""
    from common.life_aggregation_contracts import MissingReason
    from common.life_planning_contracts import HaltKind
    from product.public_errors import _HALT_TEXT, CLONE_TEXT

    assert set(get_args(MissingReason)) | {"C", "E"} == set(CLONE_TEXT)
    assert set(get_args(HaltKind)) == set(_HALT_TEXT)


def test_public_texts_never_come_from_internal_detail():
    """同一个类别、不同的内部原文，公开说明逐字相同：说明只看类别，原文（路径或任意内部文字）进不来。"""
    from common.life_aggregation_contracts import EntryRecord
    from product.public_errors import clone_explanation

    def entry(detail: str) -> EntryRecord:
        return EntryRecord.model_construct(
            clone_id="u1.k51.r0",
            category="M",
            missing_reason="input_config_program_error",
            detail=detail,
        )

    a = clone_explanation(entry("OSError: C:/secret/a.json PRIVATE_ONE"), "run-x")
    b = clone_explanation(entry("任意内部文字 PRIVATE_TWO /home/u/b.yaml"), "run-x")
    assert a == b and "PRIVATE" not in a.model_dump_json() and "secret" not in a.model_dump_json()
    assert a.category == "program_error" and a.audit_ref == (
        "run:run-x clone:u1.k51.r0 record:failures.json"
    )
    unknown = EntryRecord.model_construct(clone_id="u1", category="M", missing_reason="新原因")
    with pytest.raises(KeyError, match="没有登记公开说明"):
        clone_explanation(unknown, "run-x")


def test_internal_diagnostics_keep_the_full_text_and_the_reference_hides_it(tmp_path):
    from product.public_errors import e1_refusal_issue

    detail = "原计划名单不合法：C:/Users/Some One/x/compiled/normal/scoring.yaml 标准开局 holdings"
    found = e1_refusal_issue(tmp_path, detail)
    assert "Some One" not in found.model_dump_json() and "holdings" not in found.message_zh
    saved = (tmp_path / "diagnostics" / f"{found.audit_ref}.json").read_text(encoding="utf-8")
    assert "holdings" in saved and "Some One" in saved
    assert e1_refusal_issue(tmp_path, detail).audit_ref == found.audit_ref  # 同一原文同一编号


def test_the_offline_bridge_refuses_a_real_model_planner():
    from types import SimpleNamespace

    from product.bridge import run_offline

    world = SimpleNamespace(
        config=SimpleNamespace(planner=SimpleNamespace(identity=SimpleNamespace(kind="llm")))
    )
    with pytest.raises(ValueError, match="只接受 mock"):
        run_offline(world, None, "run-x")  # type: ignore[arg-type]
