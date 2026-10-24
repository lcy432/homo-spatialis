"""全规模产品流程测试的入口（scripts/run_product_full.py）：零网络、零费用。

* mock 规划器跑完整条产品链：所有城市 × 每城 4 个分身 × 3 年，全部过完；
* ``--years`` 加长模拟年限后同样能编译、预检查并跑完；
* Flash 运行配置：模型调用设定与报价取自 run_product_flash.yaml，规划器核对通过，预算按年度数设上限；
* 小于每城 4 个分身不是全规模，入口拒绝。
"""

from __future__ import annotations

import contextlib
import importlib
import sys
from pathlib import Path

import pytest
import yaml

from agentlayer.life_llm import LlmPlanner
from agentlayer.life_mock import mock_planner_for
from orchestration.life_budget import RoundBudget
from orchestration.life_runner import planner_setting_problems, preflight, start_run

REPO = Path(__file__).resolve().parents[2]


def _full():
    scripts = str(REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("run_product_full")


@pytest.fixture
def full(monkeypatch):
    m = _full()
    # 入口会替换 product.scenarios 的两个函数；每个测试结束后恢复
    from product import scenarios as sc

    monkeypatch.setattr(sc, "load_pack", sc.load_pack)
    monkeypatch.setattr(sc, "workspace", sc.workspace)
    return m


def _run_mock(s):
    with contextlib.chdir(s.work):
        return start_run(s.pre.world, mock_planner_for(s.pre.world.config.planner), Path("a"))


def test_all_cities_four_clones_three_years_complete_with_the_mock(full, tmp_path):
    s = full.compile_full(tmp_path / "ws", repeats=4, years=None)
    world = s.pre.world
    assert len(world.order) == 8 and {str(e.city.code) for e in world.roster.entries} == {
        "K51",
        "M52",
    }
    outcome = _run_mock(s)
    assert outcome.status == "completed"
    assert all(r.status == "completed" and r.years_committed == 3 for r in outcome.records)


def test_a_longer_horizon_compiles_and_runs(full, tmp_path):
    s = full.compile_full(tmp_path / "ws", repeats=4, years=4)
    assert s.pre.world.bundle.protocol.simulation_years == 4
    outcome = _run_mock(s)
    assert outcome.status == "completed" and {r.years_committed for r in outcome.records} == {4}
    # 仓库里的样例没有被改
    life = yaml.safe_load(
        (REPO / "configs/life/synthetic_product.yaml").read_text(encoding="utf-8")
    )
    assert life["protocol"]["simulation_years"] == 3


def test_the_flash_config_reuses_the_flash_settings_and_passes_the_planner_check(full, tmp_path):
    s = full.compile_full(tmp_path / "ws", repeats=4, years=None)
    cfg = full.flash_run_config(s.work / "compiled/full", years_total=24, max_cost_cny=10)
    flash = yaml.safe_load(full.FLASH_CONFIG.read_text(encoding="utf-8"))["run"]
    with contextlib.chdir(s.work):
        world = preflight(cfg.relative_to(s.work))
    run = world.config
    want = flash["planner"]["llm"]
    got = run.planner.llm
    assert (got.model, got.model_role, got.temperature, got.max_output_tokens) == (
        want["model"],
        "product_planner",
        want["temperature"],
        want["max_output_tokens"],
    )
    assert run.prompt_template == "e3b-product-prompt-v3"
    assert run.planning.global_call_limit == 72
    assert (run.paid_budget.max_requests, float(run.paid_budget.max_cost_cny)) == (144, 10.0)
    assert run.paid_budget.price.price_version == flash["paid_budget"]["price"]["price_version"]

    class NoSend:
        def send(self, body, *, timeout):  # pragma: no cover - 检查不发送
            raise AssertionError("不应发送")

    with contextlib.chdir(s.work):
        planner = LlmPlanner(
            run.planner.llm, transport=NoSend(), budget=RoundBudget(run.paid_budget)
        )
        assert planner_setting_problems(run.planner, planner) == []


def test_fewer_than_four_clones_per_city_is_not_full_scale(full, tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_product_full.py", "--out", str(tmp_path / "x"), "--repeats", "3", "--mock"],
    )
    with pytest.raises(SystemExit, match="不少于 4"):
        full.main()
    assert not (tmp_path / "x").exists()
