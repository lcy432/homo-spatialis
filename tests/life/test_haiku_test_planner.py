"""真实 LLM 测试改用 Claude Haiku 5.5（2026-10-18，DECISIONS/0035）的离线测试。零网络、零费用：Agent SDK 的 ``query`` 用替身，
运行用临时仓库与临时账本，绝不碰真实试跑的账本。

A 配置与契约：独立的测试角色；Claude 设定的规则；千问路径的身份不变；计费二选一。
B 请求体：与 Flash 同一对消息，厂商选项不同。
C 传输层：一次 query、一轮、隔离环境；错误翻译成统一的种类。
D 账本：美元报价按美元记账，人民币账本格式不变；报价分档。
E 运行：Haiku 配置的先导、返回型号、不记账、超出报价档。
F 入口：不写 --config 时用 Haiku；按服务商选传输层。
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeSDKError, ResultMessage, TextBlock

from agentlayer.life_llm import (
    AGENT_SDK_ENV_ALLOWLIST,
    ClaudeAgentSdkTransport,
    LlmPlanner,
    OpenAICompatibleTransport,
    ProviderCallLog,
    TransportError,
    TransportResponse,
    expected_provider_body,
    final_request_problems,
    llm_identity,
    returned_model_accepted,
)
from agentlayer.life_mock import mock_planner_for
from agentlayer.life_planning import render_prompt
from common.config import ConfigError, ModelsConfig, ProductModelSpec, ResolvedModel
from common.life_planning_contracts import LifeRunConfig, LlmSettings, PaidBudget
from common.run_store import RunStore
from orchestration.life_budget import (
    RoundBudget,
    budget_header,
    budget_identity_problems,
    read_ledger,
)
from orchestration.life_runner import (
    budget_reconciliation,
    planner_payloads,
    preflight,
    replay_run,
    start_run,
)

from .live_support import (
    HAIKU_CONFIG,
    LIVE_CONFIG,
    MOCK_CONFIG,
    REPO,
    SONNET_CONFIG,
    ScriptedTransport,
    load_yaml,
    make_repo,
    mock_text,
    records,
    reply,
    write_yaml,
)

FLASH_CONFIG = LIVE_CONFIG
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
FAKE_KEY = "sk-ant-offlinefake0000000000000000"
#: 测试自带的美元账本（与真实配置的计费选择无关：真实配置可以写账本，也可以写不记账的理由）。报价与官方 Haiku 5.5 报价相同。
TEST_BUDGET = {
    "ledger_dir": "budget-haiku",
    "max_requests": 3,
    "max_cost_usd": 0.25,
    "price": {
        "price_version": "claude-haiku-5-5@anthropic-api@2026-10-18",
        "model": HAIKU,
        "region": "Anthropic API（全球推理）",
        "currency": "USD",
        "input_per_million": 0.1,
        "output_per_million": 0.5,
        "source_url": "https://platform.claude.com/docs/en/about-claude/pricing",
        "checked_on": "2026-10-18",
        "max_input_tokens_at_price": 100000,
        "rule": "测试用：与官方 Haiku 5.5 报价相同（提示不超过 100,000 token 时 0.10 / 0.50 美元每百万 token）",
    },
}


def _haiku_dict() -> dict:
    return dict(load_yaml(REPO / HAIKU_CONFIG)["run"]["planner"]["llm"])


def _haiku(**over) -> LlmSettings:
    d = _haiku_dict()
    d.update(over)
    return LlmSettings.model_validate({k: v for k, v in d.items() if v is not None})


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """临时仓库：产品协议样例的配置；Haiku 测试配置改成带测试账本的版本，账本放在临时目录的 ``budget-haiku``。"""
    root = make_repo(tmp_path / "repo")
    data = load_yaml(REPO / HAIKU_CONFIG)
    data["run"].pop("unmetered_reason", None)
    data["run"]["paid_budget"] = TEST_BUDGET
    (root / HAIKU_CONFIG).parent.mkdir(parents=True, exist_ok=True)
    write_yaml(root / HAIKU_CONFIG, data)
    monkeypatch.chdir(root)
    return root


def _planner(world, transport, *, budget="ledger"):
    paid = world.config.paid_budget
    gate = RoundBudget(paid) if budget == "ledger" and paid is not None else None
    return LlmPlanner(
        world.config.planner.llm, transport=transport, budget=gate, sleep=lambda s: None
    )


# ---------------------------------------------------------------------- A 配置与契约


def test_a_the_test_role_is_separate_from_product_and_research_roles():
    cfg = ModelsConfig.load(REPO / "configs/models.yaml")
    t = cfg.test_planner
    assert (t.provider, t.model, t.version_policy, t.api_key_env, t.base_url) == (
        "claude_agent_sdk",
        HAIKU,
        "rolling",
        "ANTHROPIC_API_KEY",
        "https://api.anthropic.com",
    )
    assert (cfg.product_planner.model, cfg.base_b.model) == (
        "qwen3.8-flash",
        "qwen3.7-max-2026-06-08",
    )
    s = cfg.test_planner_sonnet
    assert (s.provider, s.model, s.version_policy, s.api_key_env) == (
        "claude_agent_sdk",
        SONNET,
        "rolling",
        "ANTHROPIC_API_KEY",
    )
    without = ModelsConfig(
        base_a=cfg.base_a, base_b=cfg.base_b, product_planner=cfg.product_planner
    )
    with pytest.raises(ConfigError, match="no fallback"):
        without.spec("test_planner")
    with pytest.raises(ConfigError, match="no fallback"):
        without.spec("test_planner_sonnet")


def test_a_claude_settings_have_their_own_rules():
    s = _haiku()
    assert (s.model_role, s.provider, s.thinking, s.effort, s.temperature) == (
        "test_planner",
        "claude_agent_sdk",
        "adaptive",
        "max",  # 所有者 2026-10-18 指定：Haiku 默认测试用 effort max
        None,
    )
    assert "temperature" not in s.model_dump(mode="json")  # 不传温度：这一栏不序列化
    for bad, match in [
        ({"temperature": 0.2}, "不接受温度"),
        ({"enable_thinking": False}, "不接受温度"),
        ({"response_format": "json_object"}, "prompt_only"),
        ({"effort": None}, "effort"),
        ({"model_role": "product_planner"}, "只用在测试规划角色"),
        ({"model_role": "base_b"}, "只用在测试规划角色"),
        ({"effort": "ultra"}, "effort"),
    ]:
        with pytest.raises(ValueError, match=match):
            _haiku(**bad)
    flash = dict(load_yaml(REPO / FLASH_CONFIG)["run"]["planner"]["llm"]) | {
        "model_role": "test_planner"
    }
    with pytest.raises(ValueError, match="只用 claude_agent_sdk"):
        LlmSettings.model_validate(flash)
    ident = llm_identity(s)
    assert (ident.planner_id, ident.model_snapshot) == ("llm.claude.test_planner", HAIKU)
    assert ident.note == load_yaml(REPO / HAIKU_CONFIG)["run"]["planner"]["identity"]["note"]


def test_a_qwen_settings_and_a_fresh_local_ledger(monkeypatch, tmp_path):
    """Flash 配置的调用设定不变；账本只在本机（runs/ 下，不进 git），每份克隆第一次用时按配置新建，头与配置逐项相同。
    不读本机已有的账本：干净的检出（例如 CI）里没有它。"""
    monkeypatch.chdir(REPO)
    world = preflight(FLASH_CONFIG)
    llm = world.config.planner.llm
    assert {"thinking", "effort"}.isdisjoint(llm.model_dump(mode="json"))
    budget = world.config.paid_budget
    assert Path(budget.ledger_dir).parts[0] == "runs"
    fresh = budget.model_copy(update={"ledger_dir": str(tmp_path / "ledger")})
    summary = RoundBudget(fresh).summary()
    assert summary["remaining_requests"] == budget.max_requests
    found = read_ledger(tmp_path / "ledger")
    assert found is not None
    on_disk = {k: v for k, v in found[0].items() if k != "created_at"}
    assert budget_header(budget) == on_disk


def test_a_billing_is_a_ledger_or_a_declared_reason():
    run = {
        k: v for k, v in load_yaml(REPO / HAIKU_CONFIG)["run"].items() if k != "unmetered_reason"
    }
    run["paid_budget"] = TEST_BUDGET
    LifeRunConfig.model_validate(run)
    no_budget = {k: v for k, v in run.items() if k != "paid_budget"}
    with pytest.raises(ValueError, match="只能写一个"):
        LifeRunConfig.model_validate(no_budget)
    ok = LifeRunConfig.model_validate(no_budget | {"unmetered_reason": "本机抽查，不记账"})
    assert ok.paid_budget is None and "unmetered_reason" in ok.model_dump(mode="json")
    with pytest.raises(ValueError, match="只能写一个"):
        LifeRunConfig.model_validate(run | {"unmetered_reason": "本机抽查，不记账"})
    mock = load_yaml(REPO / MOCK_CONFIG)["run"]
    with pytest.raises(ValueError, match="mock 运行"):
        LifeRunConfig.model_validate(mock | {"unmetered_reason": "本机抽查，不记账"})
    paid = dict(run["paid_budget"])
    with pytest.raises(ValueError, match="max_cost_usd"):
        PaidBudget.model_validate({**paid, "max_cost_cny": 1})
    with pytest.raises(ValueError, match="max_cost_usd"):
        PaidBudget.model_validate(
            {k: v for k, v in paid.items() if k != "max_cost_usd"} | {"max_cost_cny": 1}
        )
    # 额度可选（所有者 2026-10-18）：次数与费用上限都可以不写——账本照常记账，只是不拦截
    uncapped = PaidBudget.model_validate(
        {k: v for k, v in paid.items() if k not in ("max_cost_usd", "max_requests")}
    )
    assert (uncapped.max_requests, uncapped.max_cost) == (None, None)
    for cfg in (HAIKU_CONFIG,):
        # 真实的 Claude 配置走订阅、先不计费：不写账本，写明理由，认证方式 claude_subscription
        real = load_yaml(REPO / cfg)["run"]
        assert "paid_budget" not in real and real["unmetered_reason"], cfg
        assert real["planner"]["llm"]["auth"] == "claude_subscription", cfg


# ---------------------------------------------------------------------- B 请求体


def test_b_same_messages_as_flash_but_claude_call_options(repo, tmp_path):
    mock = preflight(MOCK_CONFIG)
    start_run(mock, mock_planner_for(mock.config.planner), tmp_path / "probe", run_id="haiku-b")
    payload = planner_payloads(tmp_path / "probe")[0]
    haiku, flash = _haiku(), preflight(FLASH_CONFIG).config.planner.llm
    hb, fb = expected_provider_body(haiku, payload), expected_provider_body(flash, payload)
    assert (
        hb["messages"]
        == fb["messages"]
        == [
            {"role": "system", "content": render_prompt(payload)[0]},
            {"role": "user", "content": render_prompt(payload)[1]},
        ]
    )
    assert set(hb) == {"model", "messages", "max_tokens", "agent_sdk"}
    assert hb["agent_sdk"] == {
        "max_turns": 1,
        "tools": [],
        "setting_sources": [],
        "strict_mcp_config": True,
        "thinking": "adaptive",
        "effort": "max",
        "prompt_caching": False,
        "response_format": "prompt_only",
    }
    assert final_request_problems(hb, payload) == []


# ---------------------------------------------------------------------- C 传输层


def _result(**over) -> ResultMessage:
    base = dict(
        subtype="success",
        duration_ms=10,
        duration_api_ms=9,
        is_error=False,
        num_turns=1,
        session_id="s",
        stop_reason="end_turn",
        total_cost_usd=0.0002,
        usage={
            "input_tokens": 1000,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 50,
            "output_tokens_details": {"thinking_tokens": 7},
        },
    )
    base.update(over)
    return ResultMessage(**base)


def _body() -> dict:
    return expected_provider_body(_haiku(), {})


def test_c_one_query_one_turn_in_an_isolated_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9/desktop-proxy")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_SCOPES", "user:inference")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-notforclaude00000000000")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
    seen: dict = {}

    async def fake_query(*, prompt, options):
        seen["prompt"], seen["options"] = prompt, options
        seen["environ"] = dict(os.environ)
        seen["cwd_exists"] = Path(options.cwd).is_dir()
        yield AssistantMessage(content=[TextBlock(text='{"a": 1}')], model=HAIKU)
        yield _result()

    body = _body()
    t = ClaudeAgentSdkTransport(
        api_key=FAKE_KEY,
        base_url="https://api.anthropic.com",
        timeout_seconds=90,
        query_fn=fake_query,
    )
    resp = t.send(body, timeout=90)
    o = seen["options"]
    assert (
        seen["prompt"] == body["messages"][1]["content"]
        and o.system_prompt == body["messages"][0]["content"]
    )
    assert (
        o.model,
        o.max_turns,
        o.tools,
        o.allowed_tools,
        o.setting_sources,
        o.strict_mcp_config,
        o.effort,
    ) == (
        HAIKU,
        1,
        [],
        [],
        [],
        True,
        "max",
    )
    assert seen["cwd_exists"] and not Path(o.cwd).exists()  # 每次新建、用完删除
    assert REPO not in Path(o.cwd).parents  # 在仓库之外：命令行不会附带 git 状态
    env = o.env
    assert (env["ANTHROPIC_API_KEY"], env["ANTHROPIC_BASE_URL"]) == (
        FAKE_KEY,
        "https://api.anthropic.com",
    )
    for k, v in {
        "CLAUDE_CODE_MAX_RETRIES": "0",
        "DISABLE_PROMPT_CACHING": "1",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "128000",  # 模型最大值（所有者：不另设上限）
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }.items():
        assert env[k] == v, k
    assert env["CLAUDE_CONFIG_DIR"] == env["HOME"] == env["USERPROFILE"]
    # 命令行启动时，本进程的环境只剩白名单里的系统变量：桌面 App 的端点、登录变量与别的密钥都不在
    assert set(seen["environ"]) <= {
        k for k in seen["environ"] if k.upper() in AGENT_SDK_ENV_ALLOWLIST
    }
    assert (
        seen["environ"].get("HTTPS_PROXY") == "http://127.0.0.1:7890"
    )  # 代理照常传给命令行（只是网络设置）
    assert (
        "DASHSCOPE_API_KEY" not in seen["environ"]
        and "CLAUDE_CODE_OAUTH_SCOPES" not in seen["environ"]
    )
    assert os.environ["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:9/desktop-proxy"  # 用完原样恢复
    assert (resp.text, resp.model, resp.prompt_tokens, resp.completion_tokens) == (
        '{"a": 1}',
        HAIKU,
        1000,
        50,
    )
    assert resp.provider_usage["thinking_tokens"] == 7 and resp.provider_usage["num_turns"] == 1
    # Anthropic 的结束原因翻译成统一写法（运行器只认 stop 为正常结束），原始值留在用量明细里
    assert (resp.finish_reason, resp.provider_usage["stop_reason"]) == ("stop", "end_turn")


@pytest.mark.parametrize(
    ("raw", "unified"), [("end_turn", "stop"), ("max_tokens", "length"), ("refusal", "refusal")]
)
def test_c_stop_reasons_are_translated(raw, unified):
    async def fake_query(*, prompt, options):
        yield AssistantMessage(content=[TextBlock(text="{}")], model=HAIKU)
        yield _result(stop_reason=raw)

    t = ClaudeAgentSdkTransport(
        api_key=FAKE_KEY, base_url="u", timeout_seconds=9, query_fn=fake_query
    )
    assert t.send(_body(), timeout=9).finish_reason == unified


@pytest.mark.parametrize(
    ("status", "error", "kind", "retryable"),
    [
        (401, "authentication_failed", "credentials", False),
        (402, "billing_error", "account", False),
        (429, "rate_limit", "rate_limited", True),
        (500, "server_error", "server_error", True),
        (529, "server_error", "server_error", True),
        (400, "unknown", "bad_request", False),
        (None, "unknown", "service_error", False),
    ],
)
def test_c_errors_become_the_shared_kinds(status, error, kind, retryable):
    async def fake_query(*, prompt, options):
        yield AssistantMessage(
            content=[TextBlock(text="API Error")], model="<synthetic>", error=error
        )
        yield _result(
            is_error=True, api_error_status=status, usage={"input_tokens": 0, "output_tokens": 0}
        )

    t = ClaudeAgentSdkTransport(
        api_key=FAKE_KEY,
        base_url="https://api.anthropic.com",
        timeout_seconds=90,
        query_fn=fake_query,
    )
    with pytest.raises(TransportError) as exc:
        t.send(_body(), timeout=90)
    assert (exc.value.kind, exc.value.retryable, exc.value.http_status) == (kind, retryable, status)


def test_c_process_failures_timeouts_and_keys_in_the_body():
    async def crash(*, prompt, options):
        raise ClaudeSDKError("cli died")
        yield  # pragma: no cover

    async def slow(*, prompt, options):
        await asyncio.sleep(5)
        yield  # pragma: no cover

    t = ClaudeAgentSdkTransport(api_key=FAKE_KEY, base_url="u", timeout_seconds=1, query_fn=crash)
    with pytest.raises(TransportError) as exc:
        t.send(_body(), timeout=1)
    assert (exc.value.kind, exc.value.retryable) == ("process_error", False)
    t = ClaudeAgentSdkTransport(api_key=FAKE_KEY, base_url="u", timeout_seconds=0.1, query_fn=slow)
    t.startup_margin_seconds = 0
    with pytest.raises(TransportError) as exc:
        t.send(_body(), timeout=0.1)
    assert (exc.value.kind, exc.value.retryable) == ("timeout", True)
    body = _body()
    body["messages"][1]["content"] += FAKE_KEY
    with pytest.raises(TransportError, match="request_check"):
        ClaudeAgentSdkTransport(
            api_key=FAKE_KEY, base_url="u", timeout_seconds=1, query_fn=crash
        ).send(body, timeout=1)


# ---------------------------------------------------------------------- D 账本


def test_d_a_usd_quote_keeps_a_usd_ledger_and_respects_the_price_tier(tmp_path):
    paid = PaidBudget.model_validate(
        TEST_BUDGET | {"ledger_dir": str(tmp_path / "b")}
    )  # 有额度的账本（测试自带）
    b = RoundBudget(paid)
    header = b.header()
    assert header["max_cost_usd"] == "0.25" and "max_cost_cny" not in header
    assert (
        header["price"]["currency"] == "USD"
        and header["price"]["max_input_tokens_at_price"] == 100000
    )
    reserved = b.reserve(
        key="k1",
        run_id="r",
        request_id="q",
        service_attempt=1,
        input_tokens_estimate=50000,
        max_output_tokens=4096,
    )
    assert (
        reserved == "0.007048"
    )  # 50,000 × 0.10 + 4,096 × 0.50，按百万 token、向上取到 0.000001 美元
    cost = b.settle(key="k1", prompt_tokens=12000, completion_tokens=800)
    assert cost == "0.001600"
    ev = b.events()
    assert ("reserved_usd" in ev[0], "cost_usd" in ev[1], "reserved_cny" in ev[0]) == (
        True,
        True,
        False,
    )
    t = b.totals()
    assert (t.currency, t.settled, t.requests) == ("USD", Decimal("0.001600"), 1)
    with pytest.raises(AttributeError):
        _ = t.settled_cny
    s = b.summary()
    assert (
        s["max_cost_usd"] == "0.250000"
        and s["remaining_usd"] == "0.248400"
        and "remaining_cny" not in s
    )
    with pytest.raises(Exception, match="报价"):
        b.reserve(
            key="k2",
            run_id="r",
            request_id="q2",
            service_attempt=1,
            input_tokens_estimate=100001,
            max_output_tokens=10,
        )
    assert (b.above_price_tier(100000), b.above_price_tier(100001)) == (False, True)


def test_d_an_uncapped_ledger_records_every_request_but_never_refuses(tmp_path):
    """额度可选：不写次数与费用上限时，账本照常预留与核算（记账），超过原来的 3 次、0.05 美元也照发；报价分档照旧检查。"""
    spec = {k: v for k, v in TEST_BUDGET.items() if k not in ("max_requests", "max_cost_usd")}
    b = RoundBudget(PaidBudget.model_validate(spec | {"ledger_dir": str(tmp_path / "u")}))
    header = b.header()
    assert (header["max_requests"], header["max_cost_usd"]) == (None, None)
    for i in range(5):  # 128,000 输出上限的预留每次约 0.069 美元：5 次远超原来的额度
        b.reserve(
            key=f"k{i}",
            run_id="r",
            request_id=f"q{i}",
            service_attempt=1,
            input_tokens_estimate=50000,
            max_output_tokens=128000,
        )
        b.settle(key=f"k{i}", prompt_tokens=12000, completion_tokens=800)
    t = b.totals()
    assert (t.requests, t.settled) == (5, Decimal("0.008000"))
    s = b.summary()
    assert (s["max_requests"], s["max_cost_usd"], s["remaining_requests"], s["remaining_usd"]) == (
        None,
        None,
        None,
        None,
    )
    with pytest.raises(Exception, match="报价"):  # 报价分档不是额度：照旧不按低档冒充
        b.reserve(
            key="k9",
            run_id="r",
            request_id="q9",
            service_attempt=1,
            input_tokens_estimate=100001,
            max_output_tokens=10,
        )


# ---------------------------------------------------------------------- E 运行


def test_e_a_haiku_pilot_runs_records_usd_costs_and_replays(repo, tmp_path):
    world = preflight(HAIKU_CONFIG)
    out = tmp_path / "run"
    t = ScriptedTransport()
    outcome = start_run(world, _planner(world, t), out, run_id="haiku-e", stop_after_commits=1)
    assert outcome.status == "interrupted" and len(t.bodies) == 1
    assert set(t.bodies[0]) == {"model", "messages", "max_tokens", "agent_sdk"}
    recs = ProviderCallLog(out).records()
    assert [(r.event, r.provider, r.role) for r in recs] == [
        ("intent", "claude_agent_sdk", "test_planner"),
        ("outcome", "claude_agent_sdk", "test_planner"),
    ]
    assert (
        recs[0].reserved_usd is not None,
        recs[0].reserved_cny,
        recs[1].cost_usd is not None,
    ) == (True, None, True)
    raw = "\n".join(RunStore(out).bodies("provider_calls"))
    assert "reserved_cny" in raw  # 旧栏照旧序列化为 null，新栏只在有值时出现
    profile = json.loads((out / "model_profile.json").read_text(encoding="utf-8"))
    assert profile["effective_call_parameters"]["agent_sdk"]["max_turns"] == 1
    assert profile["requested_model"] == HAIKU and profile["version_policy"] == "rolling"
    scope = json.loads((out / "support_scope.json").read_text(encoding="utf-8"))
    assert "美元" in json.dumps(scope, ensure_ascii=False) and "不设温度" in json.dumps(
        scope, ensure_ascii=False
    )
    replayed = replay_run(world, out)
    assert (replayed["planner_calls"], replayed["bindings_identical"]) == (0, True)
    assert budget_reconciliation(world, out)["consistent"] is True


@pytest.mark.parametrize(
    ("returned", "usable"), [(HAIKU + "-20261001", True), ("claude-sonnet-5-5", False)]
)
def test_e_the_returned_model_rule_applies_to_haiku(repo, tmp_path, returned, usable):
    s = preflight(HAIKU_CONFIG).config.planner.llm
    assert returned_model_accepted(s, returned) is usable
    assert not returned_model_accepted(s, HAIKU + "-latest")
    world = preflight(HAIKU_CONFIG)
    served = lambda body: reply(mock_text(body), body, model=returned)  # noqa: E731
    out = tmp_path / "run"
    outcome = start_run(
        world,
        _planner(world, ScriptedTransport([served])),
        out,
        run_id="haiku-r",
        stop_after_commits=1,
    )
    if usable:
        assert outcome.status == "interrupted"
    else:
        assert (outcome.status, outcome.halt_kind) == ("halted", "model_mismatch")


def test_e_an_unmetered_run_needs_a_declared_reason_and_records_no_cost(repo, tmp_path):
    def unmetered(path: Path) -> None:
        data = load_yaml(path)
        data["run"].pop("paid_budget")
        data["run"]["unmetered_reason"] = "离线测试：不记账的真实模型运行"
        write_yaml(path, data)

    unmetered(repo / HAIKU_CONFIG)
    world = preflight(HAIKU_CONFIG)
    planner = _planner(world, ScriptedTransport(), budget=None)
    assert budget_identity_problems(world.config.paid_budget, planner) == []
    out = tmp_path / "run"
    assert (
        start_run(world, planner, out, run_id="haiku-u", stop_after_commits=1).status
        == "interrupted"
    )
    recs = records(out, "provider_calls")
    assert [r["cost_status"] for r in recs] == ["unmetered", "unmetered"]
    assert all(r.get("reserved_usd") is None and r.get("reserved_cny") is None for r in recs)
    rec = budget_reconciliation(world, out)
    assert rec["applies"] is False and "不记账" in rec["note"]
    # 声明了账本、规划器却不带账本：拒绝
    world2 = preflight(FLASH_CONFIG)
    assert budget_identity_problems(
        world2.config.paid_budget, _planner(world2, ScriptedTransport(), budget=None)
    )


def test_e_a_prompt_above_the_price_tier_stops_after_recording(repo, tmp_path):
    world = preflight(HAIKU_CONFIG)

    def big(body):
        return TransportResponse(
            text=mock_text(body),
            finish_reason="end_turn",
            model=HAIKU,
            prompt_tokens=150_000,
            completion_tokens=100,
        )

    out = tmp_path / "run"
    outcome = start_run(world, _planner(world, ScriptedTransport([big])), out, run_id="haiku-t")
    assert (outcome.status, outcome.halt_kind) == ("halted", "budget")
    recs = ProviderCallLog(out).records()
    assert [r.event for r in recs] == ["intent", "outcome"] and recs[1].prompt_tokens == 150_000


# ---------------------------------------------------------------------- F 入口


def _driver():
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("run_life_e1_7b")


def test_f_the_cli_default_is_sonnet_max_and_the_transport_follows_the_provider(repo, monkeypatch):
    driver = _driver()
    assert driver.DEFAULT == SONNET_CONFIG and driver.PRODUCT == FLASH_CONFIG
    default = preflight(SONNET_CONFIG).config.planner.llm
    assert (default.model, default.model_role, default.effort) == (
        SONNET,
        "test_planner_sonnet",
        "max",
    )
    asked: list[str] = []

    def fake_resolve(self):
        asked.append(self.model)
        return ResolvedModel(
            provider=self.provider, base_url=self.base_url, model=self.model, api_key=FAKE_KEY
        )

    monkeypatch.setattr(ProductModelSpec, "resolve", fake_resolve)
    real = load_yaml(REPO / HAIKU_CONFIG)  # 真实的 Haiku 配置：订阅、不记账
    write_yaml(repo / HAIKU_CONFIG, real)
    p = driver._live_planner(preflight(HAIKU_CONFIG))
    assert isinstance(p.transport, ClaudeAgentSdkTransport) and p.budget is None  # 订阅认证、不记账
    assert p.transport._key is None
    q = driver._live_planner(preflight(FLASH_CONFIG))
    assert isinstance(q.transport, OpenAICompatibleTransport) and q.budget.currency == "CNY"
    q.transport.close()
    assert asked == ["qwen3.8-flash"]  # Haiku 走订阅，不读密钥
    # Haiku 配置与它的 mock 对照用同一份名单与资料（离线探针要求）
    assert driver.pairing_problems(preflight(HAIKU_CONFIG), preflight(MOCK_CONFIG)) == []


def test_f_subscription_mode_uses_no_key_and_keeps_the_login_path(monkeypatch):
    """订阅模式：env 里没有密钥、不隔离配置目录；命令行启动时能看到找登录凭证用的路径变量，但看不到别的宿主变量。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_SCOPES", "x")
    seen: dict = {}

    async def fake_query(*, prompt, options):
        seen["env"], seen["environ"] = options.env, dict(os.environ)
        yield AssistantMessage(content=[TextBlock(text="{}")], model=HAIKU)
        yield _result()

    t = ClaudeAgentSdkTransport(api_key=None, base_url=None, timeout_seconds=9, query_fn=fake_query)
    t.send(_body(), timeout=9)
    assert not {"ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR", "HOME"} & set(
        seen["env"]
    )
    assert seen["env"]["CLAUDE_CODE_MAX_RETRIES"] == "0"
    assert "CLAUDE_CODE_OAUTH_SCOPES" not in seen["environ"]
    assert any(k.upper() in ("USERPROFILE", "HOME") for k in seen["environ"])
