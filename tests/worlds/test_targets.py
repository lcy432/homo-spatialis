"""P5：按目标福利量求解城市属性。门一与问题三的分组依赖它。"""

from __future__ import annotations

import pytest

from worlds.personas import PersonaGenerator
from worlds.targets import (
    TargetInfeasibleError,
    dispersion_ladder,
    solve_for_ln_dispersion,
    solve_for_ln_gap,
)


@pytest.fixture
def cities(world):
    return [rec.city for rec in world.cities.values()]


# --------------------------------------------------------------------------
# 精确性：这是代数求解，不是数值逼近
# --------------------------------------------------------------------------


@pytest.mark.parametrize("target", [0.005, 0.02, 0.10, 0.40])
def test_the_gap_solver_is_exact(core, persona, cities, target):
    """由 §7.1.1，ln V 对每个 ln A_k 线性，系数是 θ_k。所以这是一次除法，
    残差应当是浮点噪声而不是求解器容差。"""
    solved = solve_for_ln_gap(core, persona, cities, target)
    assert solved.score_set(persona, cities, 2024).ln_welfare_gap == pytest.approx(
        target, abs=1e-12
    )


@pytest.mark.parametrize("target", [0.05, 0.15, 0.60])
def test_the_dispersion_solver_is_exact(core, persona, cities, target):
    solved = solve_for_ln_dispersion(core, persona, cities, target)
    assert solved.score_set(persona, cities, 2024).ln_welfare_dispersion == pytest.approx(
        target, abs=1e-12
    )


def test_it_works_for_many_personas(core, cities):
    """目标量是 (世界, 人设) 的函数——同一批城市，对偏好不同的人离散度不同。
    所以可行性要在人设的横截面上验，不能只看一个。"""
    solved_count = 0
    for p in PersonaGenerator().generate(40, seed=13):
        try:
            s = solve_for_ln_dispersion(core, p, cities, 0.20)
        except TargetInfeasibleError:
            continue
        assert s.score_set(p, cities, 2024).ln_welfare_dispersion == pytest.approx(0.20, abs=1e-12)
        solved_count += 1
    assert solved_count >= 36, f"只有 {solved_count}/40 可解，可行域太窄"


# --------------------------------------------------------------------------
# 离散度只动离散度：排序必须原样保留
# --------------------------------------------------------------------------


def test_scaling_dispersion_preserves_the_whole_ordering(core, persona, cities):
    """把同一批城市按几个 τ 复制出来时，它们只在离散度上不同，不在"谁是
    正确答案"上不同。否则门一的分组比较会混进"题目本身换了"这个因素。"""
    base = core.score_set(persona, cities, 2024)
    order = [c.code for c in sorted(cities, key=lambda c: base.ln_utility[c.code], reverse=True)]

    for target, solved in dispersion_ladder(core, persona, cities, [0.05, 0.15, 0.60]).items():
        s = solved.score_set(persona, cities, 2024)
        assert [
            c.code for c in sorted(cities, key=lambda c: s.ln_utility[c.code], reverse=True)
        ] == order, f"τ={target} 的排序变了"
        assert s.argmax_city == base.argmax_city


def test_the_ladder_solves_each_rung_from_the_same_base(core, persona, cities):
    """每一档从同一个 core 求解，不是链式地在上一档基础上再调。链式会累积
    误差，而且第三档会变成前两档的函数，分组就不再正交。"""
    ladder = dispersion_ladder(core, persona, cities, [0.10, 0.30])
    direct = solve_for_ln_dispersion(core, persona, cities, 0.30)
    a = ladder[0.30].score_set(persona, cities, 2024)
    b = direct.score_set(persona, cities, 2024)
    assert a.ln_utility == b.ln_utility


# --------------------------------------------------------------------------
# 只往下调：可行方向由 A ∈ (0,1] 的值域决定
# --------------------------------------------------------------------------


def test_widening_the_gap_never_raises_an_amenity(core, persona, cities):
    """A 上方有硬天花板（1），下方在对数空间无界。第一版往上抬最优城市，
    目标稍大就全维度撞顶。"""
    base = core.score_set(persona, cities, 2024)
    wide = solve_for_ln_gap(core, persona, cities, base.ln_welfare_gap * 4)
    for code, rec in core.cities.items():
        for dim, value in rec.amenities_scaled.items():
            assert wide.cities[code].amenities_scaled[dim] <= value + 1e-12


def test_shrinking_the_gap_never_raises_an_amenity(core, persona, cities):
    base = core.score_set(persona, cities, 2024)
    narrow = solve_for_ln_gap(core, persona, cities, base.ln_welfare_gap / 4)
    for code, rec in core.cities.items():
        for dim, value in rec.amenities_scaled.items():
            assert narrow.cities[code].amenities_scaled[dim] <= value + 1e-12


# --------------------------------------------------------------------------
# 够不到时报错，不静默偏离
# --------------------------------------------------------------------------


def test_an_unreachable_dispersion_is_refused(core, persona, cities):
    """静默给一个"尽量接近"的世界，会让"离散度 τ 以上"这一组里混进达不到
    τ 的样本，而门一的判据正是按这个分组下的。"""
    with pytest.raises(TargetInfeasibleError, match="够不到"):
        solve_for_ln_dispersion(core, persona, cities, 5.0)


def test_a_value_below_the_clamp_is_refused(core, persona, cities):
    """第一道防线。M2 的 `_amenities` 有 `max(raw, MIN_AMENITY)`，需要的标度值
    掉到 1e-6 以下会被**静默夹住**，于是求解器返回一个没打中目标的世界。
    所以 `_apply` 的下界是 MIN_AMENITY 而不是 0。

    直接测这一条而不是去找一个"恰好触发夹取"的目标值：可行域随人设变，
    靠具体数字写的回归测试换个 fixture 就失效了。
    """
    from econ.utility import MIN_AMENITY
    from worlds.targets import _apply, _dimensions_by_weight

    dim = _dimensions_by_weight(persona)[0]
    theta = persona.params.theta[dim]
    current = core.record(cities[0]).amenities_scaled[dim]
    # 要求的位移大到让标度值掉到 MIN_AMENITY 之下
    import math

    too_far = theta * (math.log(MIN_AMENITY / 10.0) - math.log(current))
    with pytest.raises(TargetInfeasibleError, match=f"{MIN_AMENITY:g}"):
        _apply(core, {cities[0]: too_far}, dim, persona)


def test_a_solution_that_misses_the_target_is_refused(core, persona, cities, monkeypatch):
    """第二道防线。求解是代数的，打不中就说明有别的东西改了解——例如某处
    新加的夹取。宁可报错也不要返回一个"差不多"的世界：本模块的全部用途
    就是让目标量成为**可控参数**而不是观察值。

    第一版没有这道防线，于是目标 1.20 静默达成了 1.140。
    """
    import worlds.targets as targets_module

    def sabotaged(core_, moves, dim, persona_):
        return core_  # 什么都不改，于是达成值等于基线，必然打不中

    monkeypatch.setattr(targets_module, "_apply", sabotaged)
    with pytest.raises(TargetInfeasibleError, match="解出来了但达成"):
        solve_for_ln_dispersion(core, persona, cities, 0.05)


@pytest.mark.parametrize("bad", [0.0, -0.1])
def test_non_positive_targets_are_refused(core, persona, cities, bad):
    with pytest.raises(ValueError, match="必须为正"):
        solve_for_ln_gap(core, persona, cities, bad)
    with pytest.raises(ValueError, match="必须为正"):
        solve_for_ln_dispersion(core, persona, cities, bad)


def test_the_world_object_is_not_mutated(core, persona, cities, world):
    """与 difficulty 一样：存函数不存配对，世界对象本身不带人设痕迹。"""
    before = {c: dict(r.amenities_scaled) for c, r in world.cities.items()}
    solve_for_ln_gap(core, persona, cities, 0.05)
    solve_for_ln_dispersion(core, persona, cities, 0.10)
    assert {c: dict(r.amenities_scaled) for c, r in world.cities.items()} == before
