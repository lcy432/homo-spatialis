"""长通勤实验臂（`DECISIONS/0012`）。默认关闭，关闭时与旧实现逐位一致。

这一臂只改一件事：**世界里出现哪些通勤分钟数**。它不动 `A(t) = 1 − t/480`，
不动 M2 的特征构造，不动七维权重的定义，不动四个分位维，也不动 P9。
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.recovery import dataset_from_choice_observations, identifiability_report
from worlds.attributes import (
    AMENITY_ANCHORS,
    LONG_COMMUTE_ARM_2024,
    PHYSICAL_MARGINALS,
    PROVISIONAL_US_2024,
    _physical_value,
    commute_minutes_for_world,
    scale_amenity,
)
from worlds.difficulty import Tier, instantiate
from worlds.dimensions import AMENITY_DIMENSIONS
from worlds.generator import WorldGenerator
from worlds.personas import PersonaGenerator, reference_persona
from worlds.selfcheck import simulate_choices

LONG_LEVELS = (60.0, 90.0, 120.0)


def _minutes(world) -> np.ndarray:
    reference = AMENITY_ANCHORS["commute"].reference
    return np.array(
        [reference * (1.0 - r.amenities_scaled["commute"]) for r in world.cities.values()]
    )


def _other_amenities(world) -> np.ndarray:
    dims = [d for d in AMENITY_DIMENSIONS if d != "commute"]
    return np.array([[r.amenities_scaled[d] for d in dims] for r in world.cities.values()])


# --------------------------------------------------------------------------
# 旧配置是可复现基线
# --------------------------------------------------------------------------


def test_the_default_calibration_leaves_the_arm_off():
    assert PROVISIONAL_US_2024.commute_long_minutes == ()
    assert PROVISIONAL_US_2024.commute_long_cities == 0


def test_with_the_arm_off_the_minutes_are_bit_identical_to_the_old_path():
    """旧基线必须逐位可复现——门一等已跑过的结果依赖这一条。"""
    z = np.random.default_rng(0).normal(size=500)
    got = commute_minutes_for_world(z, PROVISIONAL_US_2024)
    want = np.array([_physical_value("commute", float(v)) for v in z])
    assert np.array_equal(got, want)


def test_with_the_arm_off_the_scaled_value_is_bit_identical_to_scale_amenity():
    """生成器里那行覆盖，关闭时必须与 ``scale_amenity`` 给出的数完全相同。"""
    z = np.random.default_rng(1).normal(size=300)
    minutes = commute_minutes_for_world(z, PROVISIONAL_US_2024)
    via_override = np.array([AMENITY_ANCHORS["commute"].scale(float(v)) for v in minutes])
    via_scale = np.array([scale_amenity("commute", float(v), 0.22) for v in z])
    assert np.array_equal(via_override, via_scale)


def test_a_world_generated_with_the_default_calibration_has_the_old_commute_range():
    world = WorldGenerator().generate(seed=610_001, reference_persona=reference_persona())
    minutes = _minutes(world)
    lo, hi = PHYSICAL_MARGINALS["commute"][2], PHYSICAL_MARGINALS["commute"][3]
    assert minutes.min() >= lo - 1e-9
    assert minutes.max() <= hi + 1e-9


# --------------------------------------------------------------------------
# 指派规则
# --------------------------------------------------------------------------


def test_the_arm_puts_exactly_the_configured_number_of_cities_on_long_levels():
    world = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024).generate(
        seed=610_002, reference_persona=reference_persona()
    )
    minutes = _minutes(world)
    long_ones = minutes[minutes >= 60.0 - 1e-9]
    assert len(long_ones) == LONG_COMMUTE_ARM_2024.commute_long_cities
    assert sorted(np.round(long_ones, 6)) == sorted(LONG_LEVELS)


def test_short_and_long_commutes_appear_in_the_same_choice_set():
    """**这是这一臂的全部意义。** 条件 logit 只看得见选择集内部的差异，所以
    长通勤必须与短通勤同框，而不是把不同世界的整体水平抬高。"""
    gen = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024)
    for seed in range(610_010, 610_020):
        minutes = _minutes(gen.generate(seed=seed, reference_persona=reference_persona()))
        assert minutes.min() <= 45.0 + 1e-9  # 短通勤仍在场
        assert minutes.max() >= 120.0 - 1e-9  # 长通勤也在场


@pytest.mark.parametrize("n_long", [1, 2, 3, 6])
def test_minutes_stay_a_non_increasing_function_of_the_commute_latent(n_long):
    """单调性是因子结构没被打乱的保证：它只是同一个隐变量的另一个单调映射。"""
    from dataclasses import replace

    cal = replace(PROVISIONAL_US_2024, commute_long_minutes=LONG_LEVELS, commute_long_cities=n_long)
    z = np.sort(np.random.default_rng(2).normal(size=12))  # 升序
    minutes = commute_minutes_for_world(z, cal)
    assert np.all(np.diff(minutes) <= 1e-12), minutes


def test_levels_that_would_break_monotonicity_are_rejected():
    from dataclasses import replace

    cal = replace(PROVISIONAL_US_2024, commute_long_minutes=(40.0, 90.0), commute_long_cities=2)
    with pytest.raises(ValueError, match="单调"):
        commute_minutes_for_world(np.zeros(5), cal)


def test_the_rule_reads_only_the_world_and_never_a_persona():
    """世界在人设之前生成，两条轴保持正交（§11.9）。签名里没有人设可读。"""
    import inspect

    params = set(inspect.signature(commute_minutes_for_world).parameters)
    assert params == {"z_commute", "calibration"}


# --------------------------------------------------------------------------
# 不制造"短通勤全面占优"
# --------------------------------------------------------------------------


def test_long_commute_cities_are_not_systematically_the_worst_on_everything():
    """若长通勤城市在其余五维上也全面垫底，权衡就是假的——模型只要避开它们。

    因子模型给每一维都留了特异噪声，所以长通勤城市在其余维度上有高有低。
    实测平均分位 0.40：略低于中位（这正是设计要的补偿性相关），远不是垫底。
    """
    gen = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024)
    ranks, undominated = [], 0
    n = 40
    for seed in range(611_000, 611_000 + n):
        world = gen.generate(seed=seed, reference_persona=reference_persona())
        minutes, others = _minutes(world), _other_amenities(world)
        is_long = minutes >= 60.0 - 1e-9
        percentile = np.array(
            [
                [(others[:, k] < others[i, k]).mean() for k in range(others.shape[1])]
                for i in range(len(minutes))
            ]
        )
        ranks.append(percentile[is_long].mean())
        short = np.flatnonzero(~is_long)
        for i in np.flatnonzero(is_long):
            dominated = any(
                np.all(others[j] >= others[i]) and np.any(others[j] > others[i]) for j in short
            )
            if not dominated:
                undominated += 1
                break
    assert 0.25 < float(np.mean(ranks)) < 0.75
    assert undominated / n > 0.6


def test_the_economic_correlation_structure_survives():
    """通勤与其余舒适度的相关，符号与量级都保留（单调变换不改秩相关）。"""
    ref = reference_persona()
    old, new = [], []
    for seed in range(612_000, 612_030):
        for cal, bucket in ((PROVISIONAL_US_2024, old), (LONG_COMMUTE_ARM_2024, new)):
            world = WorldGenerator(calibration=cal).generate(seed=seed, reference_persona=ref)
            bucket.append(np.corrcoef(_minutes(world), _other_amenities(world).mean(axis=1))[0, 1])
    mean_old, mean_new = float(np.mean(old)), float(np.mean(new))
    assert mean_old < 0 and mean_new < 0  # 通勤越长，其余舒适度越差
    assert abs(mean_new - mean_old) < 0.10


def test_the_four_tiers_still_instantiate_under_the_arm():
    gen = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024)
    world = gen.generate(seed=613_000, reference_persona=reference_persona())
    tax = gen.tax()
    persona = PersonaGenerator().generate(3, seed=1)[0]
    for tier in Tier:
        instantiate(tier, world, persona, tax)


# --------------------------------------------------------------------------
# 只动 commute
# --------------------------------------------------------------------------


def test_the_arm_changes_commute_and_nothing_else():
    """同一个 seed 下，其余六维的标度值逐位相同——分位维一个都没碰。"""
    ref = reference_persona()
    old = WorldGenerator(calibration=PROVISIONAL_US_2024).generate(
        seed=614_000, reference_persona=ref
    )
    new = WorldGenerator(calibration=LONG_COMMUTE_ARM_2024).generate(
        seed=614_000, reference_persona=ref
    )

    for code, before in old.cities.items():
        after = new.cities[code]
        assert before.rpp == after.rpp
        assert before.wage_by_quantile == after.wage_by_quantile
        assert before.jurisdiction == after.jurisdiction
        for d in AMENITY_DIMENSIONS:
            if d == "commute":
                continue
            assert before.amenities_scaled[d] == after.amenities_scaled[d]


def test_the_anchor_formula_is_untouched():
    """A(t) = 1 − t/480。长通勤档的标度值必须正好等于这个式子给的数。"""
    reference = AMENITY_ANCHORS["commute"].reference
    assert reference == 480.0
    for minutes in LONG_LEVELS:
        assert AMENITY_ANCHORS["commute"].scale(minutes) == pytest.approx(
            1.0 - minutes / 480.0, abs=1e-15
        )


# --------------------------------------------------------------------------
# 识别
# --------------------------------------------------------------------------


def test_the_arm_lifts_commute_out_of_the_flagged_set():
    ref = reference_persona()
    personas = PersonaGenerator().generate(30, seed=20260906)
    pmap = {p.persona_id: p for p in personas}
    reports = {}
    for name, cal in (("old", PROVISIONAL_US_2024), ("new", LONG_COMMUTE_ARM_2024)):
        gen = WorldGenerator(calibration=cal)
        worlds = gen.generate_many(2, base_seed=615_000, reference_persona=ref)
        obs = simulate_choices(
            [instantiate(Tier.TRADEOFF, w, p, gen.tax()) for w in worlds for p in personas],
            pmap,
            sigma=0.05,
            seed=1,
        )
        reports[name] = identifiability_report(dataset_from_choice_observations(obs))

    ci = list(reports["old"].dimensions).index("commute")
    assert reports["old"].within_sd[ci] < 0.02
    assert reports["new"].within_sd[ci] > 5 * reports["old"].within_sd[ci]
    assert "commute" in reports["old"].flagged(threshold=0.10)
    assert "commute" not in reports["new"].flagged(threshold=0.10)
    # 绝对信息量也上去了，不只是相对排名
    assert min(reports["new"].tangent_singular_values_absolute) > min(
        reports["old"].tangent_singular_values_absolute
    )
