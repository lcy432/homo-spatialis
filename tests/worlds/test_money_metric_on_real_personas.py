"""P2 的货币度量，跑在真实规模的人设与世界上（六维舒适度）。

与 tests/econ/test_money_metric.py 的分工：那边测纯函数与类型契约，用的是
三维的小世界；这边测它在 Dirichlet(α=0.5) 实际抽出来的人设上的行为，
因为下溢区间的存在与否只有在真实抽样分布下才有意义。
"""

from __future__ import annotations

import math

import pytest

from econ.types import ChoiceSetTruth
from econ.utility import MONEY_METRIC_LN_FLOOR
from worlds.personas import PersonaGenerator


def test_the_metric_ranks_the_same_as_utility(core, personas):
    """e 是 ln V 的严格增变换，所以排序必须一致。若不一致，美元口径与
    效用口径会给出不同的最优城市，而两者本该是同一个东西的两种单位。"""
    for p in personas[:8]:
        gts = [core.score(p, rec.city, 2024) for rec in core.cities.values()]
        by_utility = max(gts, key=lambda g: g.ln_utility).city
        by_money = max(gts, key=lambda g: g.ln_money_metric).city
        assert by_utility == by_money


def test_the_underflow_region_is_real_under_dirichlet(core):
    """这不是假想的边缘情形。Dirichlet(α=0.5) 会实打实地抽出这些人设。

    2026-09-05 实测 400 人设：w₁ 中位数 0.080、p10 仅 0.0026，约 3% 的人设
    货币度量下溢。这条测试把那个事实钉住，免得将来有人以为地板是多余的。
    """
    below = 0
    people = PersonaGenerator().generate(200, seed=1)
    for p in people:
        gt = core.score(p, next(iter(core.cities.values())).city, 2024)
        below += gt.ln_money_metric < MONEY_METRIC_LN_FLOOR
    assert below > 0, "一个都没下溢，说明抽样分布变了，地板的理由要重新检查"


def test_set_level_quantities_live_on_the_set_not_the_city(core, persona):
    """同一座城市放进不同选择集，福利全距不同——所以它不是城市的属性。"""
    all_cities = [rec.city for rec in core.cities.values()]
    wide = core.score_set(persona, all_cities, 2024)
    narrow = core.score_set(persona, all_cities[:3], 2024)
    assert wide.ln_welfare_gap != narrow.ln_welfare_gap
    assert wide.cities != narrow.cities


def test_the_set_truth_agrees_with_the_city_truth(core, persona):
    """两个出口共用一条 decompose 路径，所以不可能分叉。"""
    cities = [rec.city for rec in core.cities.values()]
    cst = core.score_set(persona, cities, 2024)
    for c in cities:
        assert cst.ln_money_metric[c.code] == pytest.approx(
            core.score(persona, c, 2024).ln_money_metric, rel=1e-15
        )
    assert cst.argmax_city == max(cities, key=lambda c: core.score(persona, c, 2024).ln_utility)


def test_dispersion_and_range_are_separate_quantities(core, persona):
    """MIGRATION B1 要求分开存：全距是归一化的分母，标准差是分组的依据，
    两者在城市数不同的选择集之间行为不同。"""
    cities = [rec.city for rec in core.cities.values()]
    cst = core.score_set(persona, cities, 2024)
    if cst.welfare_range is not None:
        assert cst.welfare_dispersion is not None
        assert cst.welfare_dispersion < cst.welfare_range


def test_the_log_gap_survives_where_the_dollar_gap_does_not(core, persona):
    """分箱与分组要用 ln 版本：美元版在语义地板之下是 None，用它分箱会悄悄
    削掉一块非随机的样本。

    低 w₁ 的人设在 α₁ = 1.5 之后已经很少（缺失率 0.8%），所以这里直接构造
    一个而不是靠抽样撞——测的是 property 的行为，不是抽样分布。
    """
    from dataclasses import replace

    cities = [rec.city for rec in core.cities.values()]
    cst = core.score_set(persona, cities, 2024)
    starved = replace(cst, w_material=0.002)

    assert math.isfinite(starved.ln_welfare_gap)
    assert starved.ln_welfare_gap == pytest.approx(cst.ln_welfare_gap), (
        "ln V 空间的间隔与 w₁ 无关，换 w₁ 不该改变它"
    )
    assert starved.welfare_gap is None
    assert starved.n_outside_money_band > 0


def test_normalised_loss_is_free_of_the_material_weight(core, persona):
    """归一化必须在 ln V 空间算：分子分母各含一个 1/w₁，约掉。"""
    from dataclasses import replace

    cities = [rec.city for rec in core.cities.values()]
    cst = core.score_set(persona, cities, 2024)
    worst = min(cities, key=lambda c: cst.ln_utility[c.code])

    for w1 in (0.002, 0.2, 0.9):
        shifted = replace(cst, w_material=w1)
        assert shifted.normalised_welfare_loss(cst.argmax_city) == pytest.approx(0.0)
        assert shifted.normalised_welfare_loss(worst) == pytest.approx(1.0)


def test_a_one_city_set_is_refused(core, persona, city_a):
    with pytest.raises(ValueError, match="至少要有两座城市"):
        core.score_set(persona, [city_a], 2024)


def test_inconsistent_city_lists_are_refused(core, persona, city_a, city_b):
    with pytest.raises(ValueError, match="不一致"):
        ChoiceSetTruth(
            persona_id="p",
            year=2024,
            cities=(city_a, city_b),
            ln_utility={city_a.code: 1.0},
            w_material=0.3,
            ln_kappa=-0.3,
            argmax_city=city_a,
            numeraire="USD_2024_US100",
            reference="P=1,A=1",
        )
