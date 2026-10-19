"""年度精力反馈（`DECISIONS/0021`、0025）：下一年每月精力额度 = 基础额度 × (1 + α × 上一年完整年度平均满足感)，向下保留到 0.01。

样例：基础额度 250，α = 0.10。上一年评分不完整就没有反馈依据——当年结算保留，但不能直接开始下一年（不补零、不沿用旧额度）。
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal

import pytest

from common.life_contracts import LifeContractError

from .conftest import CLONE_K, K51, base_items, live_year


def _quota(mean: float) -> float:
    q = Decimal(250) * (1 + Decimal("0.10") * Decimal(repr(mean)))
    return float(q.quantize(Decimal("0.01"), rounding=ROUND_FLOOR))


def test_next_years_quota_follows_last_years_mean_satisfaction(first_year):
    mean = first_year.score.annual_mean
    assert first_year.handoff == "next_year_ready"
    assert first_year.next_state.energy.level == _quota(mean)
    assert 250 < first_year.next_state.energy.level < 275  # 0 < 平均满足感 < 1


def test_the_new_quota_is_what_next_year_settles_against(bundle, scoring, first_year):
    y2 = live_year(bundle, scoring, first_year.next_state, base_items())
    month = y2.settlement.outcome.resources.months[0]
    assert float(month.energy_quota.fraction()) == first_year.next_state.energy.level


def test_an_incomplete_year_blocks_the_handoff_but_keeps_the_settlement(bundle, scoring):
    """城市安全条件未知 → 这一年没有完整评分 → 反馈信息不足：结算照常保留，下一年的状态不能直接用。"""
    cond = scoring.conditions_for(K51)
    data = cond.model_dump(mode="json")
    for c in data["components"]:
        if c["component_id"] == "safety":
            c.update(status="unknown", base=None, cap=None, unknown_reason="合成情景：没有资料")
    unknown = type(cond).model_validate(data)
    conditions = tuple(unknown if str(c.city) == K51 else c for c in scoring.city_conditions)
    incomplete = type(scoring)(**{**scoring.__dict__, "city_conditions": conditions})
    y = live_year(bundle, incomplete, bundle.initial_state(CLONE_K), base_items())
    assert y.settlement.status == "completed" and y.score.annual_mean is None
    assert y.handoff == "feedback_blocked"
    with pytest.raises(LifeContractError):
        live_year(bundle, scoring, y.settlement.next_state, base_items())
