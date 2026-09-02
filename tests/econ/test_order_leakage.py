"""顺序泄漏：数值扫描完全抓不到的一条通道（架构修正五）。

若人设散文总是按权重降序铺陈七个维度，秩序信息已经在文本里了。模型不
需要读懂任何一个数字，只要注意到"他先说通勤"，就能在权衡档上答对，而
回收出来的"偏好推断能力"是假的。

这与 §10.2 已有的城市排列随机化是同一手法：随机化，记录置换，然后检验
置换与真值之间没有系统关联。
"""

from __future__ import annotations

import random

import pytest

from common.leakcheck import LeakError, assert_order_uninformative, narration_order_leakage

DIMS = ("safety", "sunshine", "commute", "housing", "healthcare", "leisure", "social")


def _weights(rng: random.Random) -> dict[str, float]:
    raw = [rng.uniform(0.5, 3.0) for _ in DIMS]
    total = sum(raw)
    return {d: v / total for d, v in zip(DIMS, raw, strict=True)}


def _population(n: int, seed: int, *, sorted_by_weight: bool):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        w = _weights(rng)
        if sorted_by_weight:
            order = tuple(sorted(DIMS, key=lambda d: -w[d]))
        else:
            order = tuple(rng.sample(DIMS, len(DIMS)))
        out.append((order, w))
    return out


def test_randomised_narration_order_shows_no_systematic_association():
    report = narration_order_leakage(_population(200, seed=11, sorted_by_weight=False))
    assert not report.leaks, str(report)
    assert abs(report.mean_rho) < 0.15


def test_weight_sorted_narration_is_caught():
    """把泄漏实现出来，确认这条检验真的能抓到它。

    按权重降序叙述时，位置与权重的 Spearman ρ 恒为 −1，人群均值离零
    要多远有多远。
    """
    report = narration_order_leakage(_population(60, seed=12, sorted_by_weight=True))
    assert report.leaks
    assert report.mean_rho == pytest.approx(-1.0)
    with pytest.raises(LeakError, match="叙述顺序"):
        assert_order_uninformative(_population(60, seed=12, sorted_by_weight=True))


def test_reverse_sorted_narration_is_caught_too():
    """双侧检验：倒序泄漏的信息量与正序完全相同。"""
    samples = [
        (tuple(reversed(order)), w) for order, w in _population(60, seed=13, sorted_by_weight=True)
    ]
    report = narration_order_leakage(samples)
    assert report.leaks
    assert report.mean_rho == pytest.approx(1.0)


def test_the_check_refuses_to_run_on_too_few_personas():
    """顺序泄漏是生成器的系统倾向，不是单个人设的属性。

    七个维度的单个 ρ 要达到 p<0.05 需要 |ρ| > 0.75 左右，单条样本几乎
    没有功效。在一个人设上"检验"顺序泄漏是在假装做检验。
    """
    with pytest.raises(ValueError, match="人群层面"):
        narration_order_leakage(_population(2, seed=14, sorted_by_weight=True))


def test_a_mild_tendency_is_still_detected_with_enough_personas():
    """真实的失败模式不是"总是排序"，是"有点倾向"。

    这里让四分之一的人设按权重排序，其余随机——单看任何一个人设都不
    可疑，人群层面则应当显形。
    """
    rng = random.Random(15)
    samples = []
    for i in range(240):
        w = _weights(rng)
        if i % 4 == 0:
            order = tuple(sorted(DIMS, key=lambda d: -w[d]))
        else:
            order = tuple(rng.sample(DIMS, len(DIMS)))
        samples.append((order, w))
    report = narration_order_leakage(samples)
    assert report.leaks, str(report)


def test_the_persona_fixture_records_its_narration_order(persona):
    """PersonaBrief 带着置换，M3 生成人设时必须填它。"""
    assert persona.brief.dimension_order
    assert set(persona.brief.dimension_order) <= set(persona.params.theta)
