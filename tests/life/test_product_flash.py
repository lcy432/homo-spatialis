"""产品角色 Qwen3.8 Flash（DECISIONS/0032）的离线验收。零网络、零费用；付费账本都在临时目录。

* 配置：产品角色 ``product_planner`` 与研究基座 ``base_a`` / ``base_b`` 分开；滚动型号的例外只在产品角色。
* 选择：显式选 Flash 配置时按配置里写的角色取密钥，角色缺失就报错，不回退；不写 ``--config`` 时是测试配置（Haiku）。
* 核对：声明、实际规划器、返回型号与报价对不上时按明确规则拒绝；发送前能发现的问题零调用拒绝。
* 归档：Flash 归档的恢复、已保存回复的重放与预算保护照常。
"""

from __future__ import annotations

import importlib
import json
import sys

import pytest

from agentlayer.life_llm import LlmPlanner, ProviderCallLog, returned_model_accepted
from common.config import ConfigError, ModelsConfig, ModelSpec, ProductModelSpec, ResolvedModel
from common.life_contracts import LifeContractError
from common.life_planning_contracts import LlmSettings
from orchestration.life_budget import RoundBudget
from orchestration.life_runner import (
    RunInterrupted,
    RunRefusedError,
    budget_reconciliation,
    replay_run,
    resume_run,
    start_run,
)

from .live_support import (
    LIVE_CONFIG,
    MODEL,
    REPO,
    ScriptedTransport,
    live_world,
    make_repo,
    mock_text,
    qwen,
    records,
    reply,
    timeout,
)

FLASH_CONFIG = LIVE_CONFIG
FLASH_MODEL = MODEL
flash_world = live_world


@pytest.fixture
def repo(tmp_path, monkeypatch):
    root = make_repo(tmp_path / "repo")
    monkeypatch.chdir(root)
    return root


def _settings(**over) -> LlmSettings:
    base = flash_settings_dict()
    base.update(over)
    return LlmSettings.model_validate(base)


def flash_settings_dict() -> dict:
    import yaml

    data = yaml.safe_load((REPO / FLASH_CONFIG).read_text(encoding="utf-8"))
    return dict(data["run"]["planner"]["llm"])


# ---------------------------------------------------------------------- 配置：产品角色与研究基座分开


def test_a_the_product_role_is_separate_and_research_roles_keep_their_rule():
    cfg = ModelsConfig.load(REPO / "configs/models.yaml")
    assert (cfg.base_a.model, cfg.base_b.model) == (None, "qwen3.7-max-2026-06-08")
    assert (cfg.product_planner.model, cfg.product_planner.version_policy) == (
        FLASH_MODEL,
        "rolling",
    )
    assert (
        cfg.product_planner.base_url == cfg.base_b.base_url
        and cfg.product_planner.api_key_env == cfg.base_b.api_key_env
    )
    with pytest.raises(ValueError, match="dated snapshot"):  # 研究基座照旧拒绝滚动名
        ModelSpec(provider="dashscope", base_url="u", api_key_env="E", model=FLASH_MODEL)
    common = dict(provider="dashscope", base_url="u", api_key_env="E")
    with pytest.raises(ValueError, match="looks dated"):
        ProductModelSpec(
            **common, model="qwen3.8-flash-2026-09-30", version_policy="rolling", rolling_note="x"
        )
    with pytest.raises(ValueError, match="no snapshot date"):
        ProductModelSpec(**common, model=FLASH_MODEL, version_policy="dated_snapshot")
    with pytest.raises(ValueError, match="rolling_note"):
        ProductModelSpec(**common, model=FLASH_MODEL, version_policy="rolling")
    without = ModelsConfig(base_a=cfg.base_a, base_b=cfg.base_b)
    with pytest.raises(ConfigError, match="no fallback"):
        without.spec("product_planner")


def test_a_rolling_names_are_allowed_only_on_the_product_role():
    assert _settings().effective_version_policy == "rolling"
    with pytest.raises(ValueError, match="只允许在产品规划角色"):
        _settings(model_role="base_b")
    with pytest.raises(ValueError, match="没有快照日期"):
        _settings(version_policy=None)  # 不写策略 = 固定版本，滚动名不行
    with pytest.raises(ValueError, match="带快照日期"):
        _settings(model="qwen3.8-flash-2026-09-30")
    dated = _settings(model="qwen3.8-flash-2026-09-30", version_policy="dated_snapshot")
    assert dated.effective_version_policy == "dated_snapshot"


# ---------------------------------------------------------------------- 选择：产品入口实际选 Flash，不回退


def _driver():
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("run_life_e1_7b")


def test_b_the_product_entry_selects_flash_and_never_falls_back(repo, monkeypatch):
    driver = _driver()
    assert driver.PRODUCT == FLASH_CONFIG and driver.DEFAULT.name == "run_product_sonnet.yaml"
    asked: list[str] = []

    def fake_resolve(self):
        asked.append(self.model)
        return ResolvedModel(
            provider=self.provider,
            base_url=self.base_url,
            model=self.model,
            api_key="sk-offlinefake000000000000",
        )

    monkeypatch.setattr(ProductModelSpec, "resolve", fake_resolve)
    monkeypatch.setattr(ModelSpec, "resolve", lambda self: pytest.fail("产品入口不该读研究角色"))
    world = flash_world()
    planner = driver._live_planner(world)
    assert asked == [FLASH_MODEL]
    assert (planner.settings.model, planner.settings.model_role, planner.identity.planner_id) == (
        FLASH_MODEL,
        "product_planner",
        "llm.qwen.product_planner",
    )
    planner.transport.close()
    # models.yaml 没有产品角色：报错，不回退到 base_b
    real_load = ModelsConfig.load
    monkeypatch.setattr(
        ModelsConfig,
        "load",
        classmethod(
            lambda cls, path="configs/models.yaml": ModelsConfig(
                base_a=real_load(path).base_a, base_b=real_load(path).base_b
            )
        ),
    )
    with pytest.raises(ConfigError, match="no fallback"):
        driver._live_planner(world)


def test_b_resume_and_replay_use_the_archive_model_and_refuse_a_config_flag(monkeypatch):
    """新运行默认用测试配置（Haiku，DECISIONS/0035）、可以显式选 Flash；恢复与重放只按归档冻结的配置与模型执行，写了
    --config 就报错（不能换模型接着跑）。"""
    driver = _driver()
    seen: list[tuple[str, object]] = []
    monkeypatch.setattr(
        driver, "cmd_live", lambda kind, out, config, run_id, **kw: seen.append((kind, config))
    )
    monkeypatch.setattr(driver, "cmd_resume", lambda out, stop, **kw: seen.append(("resume", None)))
    monkeypatch.setattr(driver, "cmd_replay", lambda out, report: seen.append(("replay", None)))
    for argv in (
        ["resume", "--out", "o", "--config", str(FLASH_CONFIG)],
        ["replay", "--out", "o", "--config", str(LIVE_CONFIG)],
    ):
        monkeypatch.setattr(sys, "argv", ["run_life_e1_7b.py", *argv])
        with pytest.raises(SystemExit) as exc:
            driver.main()
        assert exc.value.code == 2
    assert seen == []
    for argv in (
        ["pilot", "--out", "o", "--run-id", "new-pilot"],
        ["main", "--out", "o", "--run-id", "new-main", "--config", str(LIVE_CONFIG)],
        ["resume", "--out", "o"],
        ["replay", "--out", "o"],
    ):
        monkeypatch.setattr(sys, "argv", ["run_life_e1_7b.py", *argv])
        driver.main()
    assert seen == [
        ("pilot", driver.DEFAULT),
        ("main", LIVE_CONFIG),
        ("resume", None),
        ("replay", None),
    ]


# ---------------------------------------------------------------------- 核对：对不上就拒绝


@pytest.mark.parametrize(
    "update",
    [
        {"model": "qwen3.8-plus"},
        {"temperature": 0.3},
        {"max_output_tokens": 2048},
        {"enable_thinking": True},
    ],
)
def test_c_declared_and_actual_settings_must_match_with_zero_calls(repo, tmp_path, update):
    world = flash_world()
    settings = world.config.planner.llm.model_copy(update=update)
    t = ScriptedTransport()
    with pytest.raises(RunRefusedError, match="不一致"):
        start_run(world, qwen(world, t, settings=settings), tmp_path / "run", run_id="flash-c")
    assert t.bodies == [] and not (tmp_path / "run").exists()


def test_c_config_level_mismatches_are_refused_before_any_run(tmp_path, monkeypatch):
    def price_of_max(r):
        r["paid_budget"]["price"]["model"] = "qwen3.7-max-2026-06-08"

    monkeypatch.chdir(make_repo(tmp_path / "r1", run=price_of_max))
    with pytest.raises(LifeContractError, match="报价表对应的模型"):
        flash_world()

    def other_model(r):
        r["planner"]["identity"]["model_snapshot"] = "qwen3.8-plus"
        r["planner"]["llm"]["model"] = "qwen3.8-plus"
        r["paid_budget"]["price"]["model"] = "qwen3.8-plus"

    monkeypatch.chdir(make_repo(tmp_path / "r2", run=other_model))
    with pytest.raises(RunRefusedError, match="模型快照"):  # 与 models.yaml 的产品角色不一致
        flash_world()

    def research_role(r):
        r["planner"]["llm"]["model_role"] = "base_b"

    monkeypatch.chdir(make_repo(tmp_path / "r3", run=research_role))
    with pytest.raises(LifeContractError, match="只允许在产品规划角色"):
        flash_world()


def test_c_the_returned_model_rule_is_explicit():
    flash = _settings()
    for name, ok in [
        ("qwen3.8-flash", True),
        ("qwen3.8-flash-2026-09-30", True),
        ("qwen3.8-flash-20260930", True),
        ("qwen3.8-flash-0930", True),
        ("qwen3.8-flash-latest", False),
        ("qwen3.8-plus", False),
        ("cmaas-qwen3.8-flash", False),
        ("qwen3.8-flash-2026-09-30-extra", False),
        ("qwen3.7-max-2026-06-08", False),
        (None, False),
    ]:
        assert returned_model_accepted(flash, name) is ok, name
    dated = "qwen3.7-max-2026-06-08"
    max_settings = _settings(model_role="base_b", model=dated, version_policy=None)
    assert returned_model_accepted(max_settings, dated)
    assert not returned_model_accepted(max_settings, dated + "-2026-06-09")
    assert not returned_model_accepted(max_settings, "qwen3.7-max")


@pytest.mark.parametrize(
    ("returned", "usable"), [("qwen3.8-flash-2026-09-30", True), ("qwen3.8-plus", False)]
)
def test_c_returned_models_are_recorded_and_judged_by_the_rule(repo, tmp_path, returned, usable):
    world = flash_world()
    served = lambda body: reply(mock_text(body), body, model=returned)  # noqa: E731
    out = tmp_path / "run"
    outcome = start_run(
        world, qwen(world, ScriptedTransport([served])), out, run_id="flash-r", stop_after_commits=1
    )
    recs = ProviderCallLog(out).records()
    assert recs[0].request["model"] == FLASH_MODEL  # 请求型号
    assert (recs[1].returned_model, recs[1].usable_for_planning) == (
        returned,
        usable,
    )  # 服务返回型号与判断
    if usable:
        assert outcome.status == "interrupted" and len(records(out, "accepted")) == 1
    else:
        assert (outcome.status, outcome.halt_kind) == ("halted", "model_mismatch")
        t = ScriptedTransport()
        again = resume_run(world, qwen(world, t), out, stop_after_commits=1)
        assert (again.halt_kind, t.bodies) == ("model_mismatch", [])


# ---------------------------------------------------------------------- 归档：不混用、恢复、重放、预算


class _CrashAfterReply(LlmPlanner):
    def plan(self, call):
        super().plan(call)
        raise RunInterrupted("回复落盘之后、规划记录之前中断")


def test_d_a_flash_archive_resumes_replays_and_records_the_model_profile(repo, tmp_path):
    world = flash_world()
    out = tmp_path / "run"
    p = _CrashAfterReply(
        world.config.planner.llm,
        transport=ScriptedTransport(),
        budget=RoundBudget(world.config.paid_budget),
    )
    assert start_run(world, p, out, run_id="flash-d").status == "interrupted"
    profile = json.loads((out / "model_profile.json").read_text(encoding="utf-8"))
    assert (profile["requested_model"], profile["model_role"], profile["version_policy"]) == (
        FLASH_MODEL,
        "product_planner",
        "rolling",
    )
    assert profile["effective_call_parameters"] == {
        "model": FLASH_MODEL,
        "temperature": 0.2,
        "max_tokens": 4096,
        "response_format": {"type": "json_object"},
        "extra_body": {"enable_thinking": False},
    }
    assert "快照日期" in profile["returned_model_rule"] and profile["price_version"].startswith(
        "qwen3.8-flash@"
    )
    t = ScriptedTransport()
    resumed = resume_run(world, qwen(world, t), out, stop_after_commits=1)
    assert resumed.status == "interrupted" and t.bodies == []  # 已保存的回复：零新增请求
    replayed = replay_run(world, out)
    assert (replayed["planner_calls"], replayed["bindings_identical"]) == (0, True)
    assert budget_reconciliation(world, out)["consistent"] is True


def test_d_the_flash_budget_stops_at_three_requests(tmp_path, monkeypatch):
    def three(r):
        r["paid_budget"]["max_requests"] = 3

    monkeypatch.chdir(make_repo(tmp_path / "repo", run=three))
    world = flash_world()
    assert world.config.paid_budget.max_requests == 3
    t = ScriptedTransport(
        [timeout(), "散文", "散文"]
    )  # 第 1 份候选 2 次请求、第 2 份 1 次；第 3 份需要第 4 次
    out = tmp_path / "run"
    outcome = start_run(world, qwen(world, t), out, run_id="flash-b")
    assert (outcome.status, outcome.halt_kind, len(t.bodies)) == ("halted", "budget", 3)
    assert RoundBudget(world.config.paid_budget).totals().requests == 3
