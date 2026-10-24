"""正式服务的单次停机预算与输入护栏（所有者 2026-10-24：每次六城 × 4 分身 × 3 年的运行 20 元；产品 Flash 单次输入 192,000）。

只读生产配置（``configs/product/service.yaml``、``configs/life/run_product_flash.yaml``）与冻结资料包，在临时目录里建一本新账本；
不发送任何请求。手算：按护栏顶格预留一次 = (192,000 × 0.8 + 4,096 × 2.7) ÷ 1,000,000 = 0.1646592 元，账本向上取到 0.000001 元
= 0.164660；121 次 = 19.92386 元 ≤ 20，第 122 次合计 20.08852 元 > 20，按原有规则拒绝预留（不发送、不扣次数）。
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from agentlayer.life_llm import BudgetRefused
from common.life_planning_contracts import PaidBudget
from orchestration.life_budget import RoundBudget
from product.execution import build_execution_spec
from product.pack import load_pack
from product.service.config import load_service_config

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def spec():
    cfg = load_service_config()
    pack = load_pack(REPO / cfg.pack_path)
    return cfg, build_execution_spec(
        pack=pack,
        pack_path=cfg.pack_path,
        planner="llm",
        flash_config=REPO / cfg.flash_config,
        max_cost_cny=cfg.max_cost_cny,
    )


def test_the_production_run_budget_is_20_cny_and_live_calls_stay_off(spec):
    cfg, s = spec
    assert cfg.max_cost_cny == "20" and cfg.live_calls_enabled is False
    assert (s.max_cost, s.currency) == ("20", "CNY")
    # 只改了 Flash 的输入护栏：输出上限、每年修订次数与重试不变
    assert s.llm["max_input_tokens"] == 192000 and s.llm["max_output_tokens"] == 4096
    assert s.max_revisions_per_year == 2 and s.llm["service_retries"] == 1
    # 72 次基础规划（六城 × 4 分身 × 3 年），加上每年最多 2 次修订是 216 份候选，每份最多 2 次服务请求
    assert len(s.cities) * len(s.repeat_indexes) * s.years == 72
    assert s.global_call_limit == 216 and s.max_requests == 432


def test_the_20_cny_budget_stops_reservations_by_the_existing_rule(spec, tmp_path):
    _, s = spec
    block = s.paid_budget_block()
    assert block is not None and block["max_cost_cny"] == 20.0
    budget = RoundBudget(PaidBudget.model_validate(block), root=tmp_path)
    assert budget.cost(192000, 4096) == Decimal("0.164660")
    n = 0
    with pytest.raises(BudgetRefused, match="超过本轮上限 20"):
        while True:
            budget.reserve(
                key=f"k{n}",
                run_id="run-test",
                request_id=f"r{n}",
                service_attempt=1,
                input_tokens_estimate=192000,
                max_output_tokens=4096,
            )
            n += 1
    assert n == 121
    totals = budget.totals()
    assert totals.requests == 121 and totals.committed == Decimal("19.923860")
