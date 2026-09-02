"""世界生成与它的硬验收：条件数。"""

from __future__ import annotations

import numpy as np
import pytest

from econ.core import WelfareCore
from worlds.dimensions import N_AMENITIES
from worlds.generator import (
    SYNTH_SCHEME,
    WorldGenerator,
    WorldRejectedError,
    condition_number,
    design_matrix,
)


def test_cities_are_fictional_and_neutrally_labelled(world):
    """§11.3：不得写得像任何一座真实城市，否则测的是记忆不是推理。
    连编码方案都不复用 CBSA，免得有人拿去 join 真实数据。"""
    for rec in world.cities.values():
        assert rec.city.scheme == SYNTH_SCHEME
        assert rec.city.code.startswith("C")


def test_generation_is_deterministic_in_the_seed(generator, ref_persona):
    a = generator.generate(seed=99, reference_persona=ref_persona)
    b = generator.generate(seed=99, reference_persona=ref_persona)
    assert [r.rpp for r in a.cities.values()] == [r.rpp for r in b.cities.values()]
    assert a.label_permutation == b.label_permutation
    assert a.condition_number == b.condition_number


def test_label_permutation_is_recorded(world):
    """§10.2 标签随机化。记录不是为了隐藏，是为了让"标签里没有信息"可被检验。"""
    assert sorted(world.label_permutation) == list(range(len(world)))


def test_condition_number_passes_the_acceptance_line(world, generator):
    assert world.condition_number <= generator.max_condition_number


def test_too_few_cities_is_refused_at_construction():
    """中心化消耗一个秩，所以城市数必须比特征数多一。少于这个数不是
    "病态"而是"不可识别"。"""
    with pytest.raises(ValueError, match="不可识别"):
        WorldGenerator(n_cities=2 + N_AMENITIES)


def test_rank_deficiency_reports_infinity_not_a_large_number(generator, ref_persona, tax):
    """秩不足和病态是两回事，不该用同一个数表达。"""
    w = generator._attempt(1, "probe")
    core = WelfareCore(w.cities, tax, numeraire="USD_2024_US100", reference_id="SYNTH_2024")
    too_few = list(w.city_ids)[: 2 + N_AMENITIES]  # 城市数正好等于特征数
    assert condition_number(core, ref_persona, too_few) == float("inf")


def test_the_design_matrix_is_centred(world, ref_persona, tax):
    """条件 logit 差分掉备选集内的公共部分，所以决定可识别性的是中心化
    之后的矩阵。"""
    core = WelfareCore(world.cities, tax, numeraire="USD_2024_US100", reference_id="SYNTH_2024")
    m = design_matrix(core, ref_persona, list(world.city_ids))
    assert np.allclose(m.mean(axis=0), 0.0, atol=1e-12)


def test_the_design_matrix_is_the_one_m5_will_see(world, ref_persona, tax):
    """特征表示只有一份。两份迟早分叉，分叉之后回收误差就没有意义了。"""
    core = WelfareCore(world.cities, tax, numeraire="USD_2024_US100", reference_id="SYNTH_2024")
    m = design_matrix(core, ref_persona, list(world.city_ids))
    truth = core.score(ref_persona, world.city_ids[0], 2024)
    assert m.shape == (len(world), len(truth.features))


def test_an_impossible_acceptance_line_fails_loudly_rather_than_looping(ref_persona):
    g = WorldGenerator(n_cities=12, max_condition_number=1.0, max_attempts=3)
    with pytest.raises(WorldRejectedError, match="调参数"):
        g.generate(seed=5, reference_persona=ref_persona)


def test_generate_many_produces_independent_worlds(worlds):
    rents = [tuple(round(r.rpp["rent"], 6) for r in w.cities.values()) for w in worlds]
    assert len(set(rents)) == len(worlds)


def test_the_spatial_equilibrium_shows_up_in_a_generated_world(world):
    """工资与房租正相关。没有这条，第四档（货币幻觉）根本无从构造。"""
    rents = np.log([r.rpp["rent"] for r in world.cities.values()])
    wages = np.log([r.wage_by_quantile[50] for r in world.cities.values()])
    assert np.corrcoef(wages, rents)[0, 1] > 0.3
