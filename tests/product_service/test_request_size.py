"""首年请求的输入上限（E3B 收尾 §6）：预检查与启动前核对共用发送前护栏的同一条路（构造公开观察 → 规范渲染 → 估计 → 比上限）。

* 首年请求超过本次规划器的输入上限：预检查不能确认，零服务调用、零次数；
* 预检查通过以后条件变了（启动前核对按真正的运行编号再算一次）：不启动、不扣次数，预留退回；重复的请求不多扣、不多退；
* 公开的说明只有城市、估计与上限，不带提示文字或评分参数。传输层都是离线替身。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agentlayer.life_llm import InputSizeCheck
from tests.life.live_support import ScriptedTransport
from tests.product_service.support import ready_intent, start

REPO = Path(__file__).resolve().parents[2]
FLASH = REPO / "configs/life/run_product_flash.yaml"


def _flash_with_cap(tmp_path: Path, cap: int) -> str:
    raw = yaml.safe_load(FLASH.read_text(encoding="utf-8"))
    raw["run"]["planner"]["llm"]["max_input_tokens"] = cap
    path = tmp_path / "flash_small.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return str(path)


def _llm(tmp_path: Path, transport: ScriptedTransport, **over):
    return start(
        tmp_path,
        planner="llm",
        live_calls_enabled=True,
        transport=lambda _settings: transport,
        **over,
    )


def test_an_oversized_first_year_request_blocks_the_preflight_without_calls_or_quota(tmp_path):
    transport = ScriptedTransport()
    svc = _llm(tmp_path, transport, flash_config=_flash_with_cap(tmp_path, 2_000))
    principal, b = svc.person()
    intent = b.prepare()
    svc.work()
    view = b.intent(intent["intent_id"])
    assert view["stage"] == "blocked" and view["confirm_with"] is None
    pre = view["preflight"]
    assert pre["status"] == "blocked" and pre["planner"]["planner_kind"] == "llm"
    issues = [i for i in pre["issues"] if i["code"] == "planning_request_too_large"]
    assert issues and {i["field"] for i in issues} == {
        f"cities.{c}" for c in svc.ctx.default_spec.cities
    }
    text = " ".join(i["message_zh"] + i["impact_zh"] + i["next_action_zh"] for i in issues)
    assert "2000" in text and "不会删减" in text
    for leaked in ("你是一位用户", "$table", "$grid", "scoring", "spec_id"):
        assert leaked not in text
    # 不能确认开始：没有服务调用，没有预留或使用次数
    r = b.submit({**view, "confirm_with": "0" * 64})
    assert r.status_code == 409
    assert transport.bodies == []
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    assert svc.store.quota_events(principal) == []


def test_the_start_recheck_refuses_before_charging_and_duplicates_change_nothing(
    tmp_path, monkeypatch
):
    from workflows import e3_adapters

    transport = ScriptedTransport()
    svc = _llm(tmp_path, transport)
    principal, b = svc.person()
    view = ready_intent(svc, b)  # 预检查按正常上限通过
    assert view["preflight"]["status"] == "ready"
    seen: list[tuple[str, ...]] = []
    real = e3_adapters.first_year_input_checks

    def tighter(world, run_id):
        checks = real(world, run_id)
        seen.append(tuple(c.request_id for c in checks))
        # 预检查之后条件变了（例如上限调小）：启动前按真正的运行编号重算，超过就不启动
        return tuple(
            InputSizeCheck(c.request_id, c.estimate, c.estimate - 1, c.rule) for c in checks
        )

    monkeypatch.setattr(e3_adapters, "first_year_input_checks", tighter)
    first = b.submit(view)
    assert first.status_code == 202
    run_id = first.json()["receipt"]["run_id"]
    assert b.quota()["reserved"] == 1
    # 预检查用的占位编号与真正的运行编号一样长（估计只看字符数）
    from product.preflight import PLACEHOLDER_RUN_ID

    assert len(PLACEHOLDER_RUN_ID) == len(run_id)
    svc.work()
    after = b.intent(view["intent_id"])
    assert after["stage"] == "blocked"
    assert after["explanation"]["category"] == "service_request_too_large"
    assert transport.bodies == []  # 零服务调用
    assert seen and all(rid.startswith(f"{run_id}/") for rid in seen[0])  # 按真正的运行编号
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}
    kinds = [e["kind"] for e in svc.store.quota_events(principal)]
    assert kinds == ["reserve", "release"]
    archive = svc.ctx.run_dir(view["intent_id"]) / "ws" / "archive"
    assert not archive.exists() or not any(archive.iterdir())
    # 重复：同一个键重发不再预留；再排一次运行任务也不开始、不再退
    again = b.submit(view)
    assert again.status_code in (200, 409)
    svc.store.enqueue("run", view["intent_id"])
    svc.work()
    assert [e["kind"] for e in svc.store.quota_events(principal)] == ["reserve", "release"]
    assert transport.bodies == []
    assert b.quota() == {"total": 3, "used": 0, "reserved": 0, "available": 3}


def test_normal_requests_still_start_and_the_preflight_uses_the_sending_path(tmp_path):
    """上限够用时照常启动；真正发出去的首年请求就是预检查估计的那一份（同一条渲染与估计）。"""
    from agentlayer.life_llm import expected_provider_body, request_input_check
    from agentlayer.life_planning import payload_from_user_prompt

    transport = ScriptedTransport()
    svc = _llm(tmp_path, transport)
    _, b = svc.person()
    view = ready_intent(svc, b)
    receipt = b.submit(view).json()["receipt"]
    svc.work()
    done = b.intent(view["intent_id"])
    assert done["stage"] == "finished", done
    settings = svc.ctx.default_spec.llm_settings
    firsts = []
    for body in transport.bodies:
        payload = payload_from_user_prompt(body["messages"][1]["content"])
        if payload["request_id"].endswith("/y1/a1"):
            firsts.append((body, payload))
    assert len(firsts) == svc.ctx.default_spec.clones
    for body, payload in firsts:
        assert payload["request_id"].startswith(f"{receipt['run_id']}/")
        assert body["messages"] == expected_provider_body(settings, payload)["messages"]
        check = request_input_check(settings, payload)
        assert check.fits and check.limit == settings.max_input_tokens
