"""写在数学之前的四条断言。

它们不是单元测试意义上的"覆盖率"，是四个具体的、已知会静默出错的地方。
每一条都附上了出错时的后果，因为后果决定了这条断言值不值得存在。
"""

from __future__ import annotations

import math

import pytest

from econ.types import PersonaParams
from econ.utility import (
    FEATURE_LN_P,
    FEATURE_LN_Y,
    budget_allocation,
    ln_indirect_utility,
    ln_kappa,
)
from tests.econ.conftest import CityVaryingSharesCore

# --------------------------------------------------------------------------
# 断言一：预算恒等式
# --------------------------------------------------------------------------


@pytest.mark.parametrize("y", [30_000.0, 92_000.0, 250_000.0])
@pytest.mark.parametrize(("p_c", "p_h"), [(1.0, 1.0), (1.02, 1.31), (0.94, 0.83)])
def test_budget_identity_holds(persona, y, p_c, p_h):
    """p_C·C* + p_H·H* == Y。分母是 (α+β)，不是 1。"""
    c, h = budget_allocation(y, p_c, p_h, persona.params)
    assert p_c * c + p_h * h == pytest.approx(y, rel=1e-12)


def test_the_wrong_denominator_would_lose_money_silently(persona):
    """写成 αY/p_C 会让 (1−w₁)·Y 凭空消失。

    这条测试存在的理由是错误的形态：预算里少掉的那部分不会报错，也不会
    让排序变得离谱，它会被静默地当成储蓄，污染 §7.5 的三年轨迹。这里把
    亏空的规模量出来，让它变成一个具体的数字而不是一句警告。
    """
    y = 92_000.0
    p_c, p_h = 1.02, 1.31
    params = persona.params
    wrong_c = params.alpha * y / p_c
    wrong_h = params.beta * y / p_h
    shortfall = y - (p_c * wrong_c + p_h * wrong_h)
    assert shortfall == pytest.approx(y * (1.0 - params.w_material), rel=1e-12)
    assert shortfall > 0.4 * y  # w₁ = 0.52，所以有将近一半的钱会不见


# --------------------------------------------------------------------------
# 断言二：两通道方向
# --------------------------------------------------------------------------


def test_alpha_plus_beta_equals_w1(persona):
    """总量来自定和量表的 w₁，比值来自 CEX 份额。"""
    assert persona.params.alpha + persona.params.beta == pytest.approx(
        persona.params.w_material, rel=1e-12
    )


def test_the_split_within_w1_comes_from_the_shares_not_the_scale(persona):
    """β/w₁ 必须等于 s_rent。

    反过来（用陈述偏好定份额、用行为定总量）在代码上一样跑得通，但它
    推翻 §7.2：支出份额由行为揭示，定和量表问的是"你多在乎"。这条断言
    锁住的是方向，不是数值。
    """
    p, s = persona.params, persona.shares
    assert p.beta / p.w_material == pytest.approx(s.s_rent, rel=1e-12)
    assert p.alpha / p.w_material == pytest.approx(s.s_goods + s.s_other, rel=1e-12)


# --------------------------------------------------------------------------
# 断言三：κ 跨城不变
# --------------------------------------------------------------------------


def test_kappa_is_identical_across_every_city(core, persona):
    """同一个 persona 在所有城市上 κ 相同。

    排序测试发现不了 κ 算错，因为它在城市之间是公因子，argmax 里直接
    约掉。但等价变差是水平量的比较，κ 一旦跨城漂移，EV 就全错——而 EV
    正是 §9.1 说的"物质那一维的严格度量"。
    """
    kappas = {
        code: core.score(persona, rec.city, 2024).ln_kappa for code, rec in core.cities.items()
    }
    assert len(set(kappas.values())) == 1, f"κ 跨城漂移了：{kappas}"
    assert next(iter(kappas.values())) == pytest.approx(ln_kappa(persona.params), rel=1e-12)


def test_a_core_whose_shares_vary_by_city_breaks_kappa(cities, tax, persona):
    """把被禁止的做法实现出来，确认这条断言真的能抓到它。

    CityVaryingSharesCore 按城市租金去"调整"份额，正是架构修正一禁止的
    事。它的 κ 立刻开始跨城变动。
    """
    from common.money import USD_2024_US100

    broken = CityVaryingSharesCore(
        cities, tax, numeraire=USD_2024_US100, reference_id="US_MSA_2024"
    )
    kappas = {code: broken.kappa_of(persona, rec.city) for code, rec in broken.cities.items()}
    assert len(set(kappas.values())) > 1, "坏核没有坏，这条断言就没有在检验任何东西"


def test_kappa_cancels_in_ranking_but_not_in_levels(core, persona, city_a, city_b):
    """κ 为什么必须单独断言：排序对它免疫，水平量对它不免疫。"""
    a = core.score(persona, city_a, 2024)
    b = core.score(persona, city_b, 2024)
    ranking_gap = a.ln_utility - b.ln_utility
    gap_without_kappa = (a.ln_utility - a.ln_kappa) - (b.ln_utility - b.ln_kappa)
    assert ranking_gap == pytest.approx(gap_without_kappa, rel=1e-12)
    assert a.ln_kappa != 0.0  # 但它确实不是零，所以水平量会受影响


# --------------------------------------------------------------------------
# 断言四：log 线性
# --------------------------------------------------------------------------


def _decompose_with(params: PersonaParams):
    return ln_indirect_utility(
        math.log(74_000.0),
        math.log(1.06),
        {"safety": math.log(0.62), "sunshine": math.log(0.58), "commute": math.log(0.41)},
        params,
    )


def _params(w1: float, theta: dict[str, float], s_rent: float) -> PersonaParams:
    """由 (w₁, θ) 与**固定的**份额构造指数。这是 §7.2 的两通道映射。"""
    return PersonaParams(alpha=w1 * (1.0 - s_rent), beta=w1 * s_rent, theta=theta)


S_RENT = 0.34
_THETA_A = {"safety": 0.20, "sunshine": 0.13, "commute": 0.15}
_THETA_B = {"safety": 0.35, "sunshine": 0.20, "commute": 0.15}


@pytest.mark.parametrize("t", [0.0, 0.15, 0.5, 0.83, 1.0])
def test_ln_v_is_linear_in_the_parameter_vector(t):
    """ln V 对 (w₁, θ) 是仿射的——**在份额固定的前提下**。

    这不是形式检查。§11.7 的条件 logit 假定效用对参数线性；若这条不成立，
    logit 就是误设定的，回收出来的"权重"不对应任何注入的东西，而整个研究
    模式的核心因变量恰恰就是那个回收误差。

    前提条件不是可有可无的细则。ln κ = α·ln(α/w₁) + β·ln(β/w₁)，把
    α = w₁(1−s_rent)、β = w₁·s_rent 代进去得到 ln κ = w₁·c(s)，其中
    c(s) 只依赖份额。份额固定 ⟹ ln κ 是 w₁ 的**线性**函数 ⟹ ln V 线性。
    份额一动，c(s) 跟着动，ln κ 立刻变成 w₁ 的非线性函数。

    所以架构修正一（份额是 Persona 的字段，不是 (persona, city) 的函数）
    与这条断言是**同一个要求的两种说法**。见下一个用例。

    单纯形是凸的：w₁ + Σθ = 1 的两个点，其凸组合仍满足约束，检验良定。
    """
    w1_a, w1_b = 1.0 - sum(_THETA_A.values()), 1.0 - sum(_THETA_B.values())
    p_a, p_b = _params(w1_a, _THETA_A, S_RENT), _params(w1_b, _THETA_B, S_RENT)
    blended = _params(
        (1 - t) * w1_a + t * w1_b,
        {k: (1 - t) * _THETA_A[k] + t * _THETA_B[k] for k in _THETA_A},
        S_RENT,
    )

    lhs = _decompose_with(blended).ln_utility
    rhs = (1 - t) * _decompose_with(p_a).ln_utility + t * _decompose_with(p_b).ln_utility
    assert lhs == pytest.approx(rhs, rel=1e-12)


def test_linearity_breaks_the_moment_the_shares_move():
    """份额随城市变会直接破坏 §11.7 的设定。

    这个用例存在是为了把"架构修正一"从一条纪律变成一个可以看见后果的
    事实：ln κ / w₁ 在份额固定时是常数，份额一动就不是了。
    """

    def theta_filling(w1: float) -> dict[str, float]:
        """把 θ 按比例缩放到 1 − w₁，好让指数和恒为一。ln κ 不依赖 θ，
        所以这一步不影响被检验的量。"""
        scale = (1.0 - w1) / sum(_THETA_A.values())
        return {k: v * scale for k, v in _THETA_A.items()}

    fixed = [ln_kappa(_params(w1, theta_filling(w1), S_RENT)) / w1 for w1 in (0.52, 0.41, 0.30)]
    assert len({round(v, 12) for v in fixed}) == 1, f"份额固定时 ln κ/w₁ 应为常数，得到 {fixed}"

    moving = [
        ln_kappa(_params(w1, theta_filling(w1), s)) / w1
        for w1, s in ((0.52, 0.34), (0.41, 0.362), (0.30, 0.40))
    ]
    assert len({round(v, 12) for v in moving}) == 3, f"份额变动时 ln κ/w₁ 应当漂移，得到 {moving}"


def test_the_material_coefficients_are_equal_and_opposite(core, persona, city_a):
    """真值下 coef_Y = +w₁ 与 coef_P = −w₁。

    拆成两列而不是一列 ln(Y/P)，是为了让这个约束变成可检验假设。M5 估
    出来之后，|coef_Y + coef_P| 就是货币幻觉的连续度量。
    """
    gt = core.score(persona, city_a, 2024)
    assert gt.coefficients[FEATURE_LN_Y] == pytest.approx(persona.params.w_material)
    assert gt.coefficients[FEATURE_LN_P] == pytest.approx(-persona.params.w_material)
    assert gt.coefficients[FEATURE_LN_Y] + gt.coefficients[FEATURE_LN_P] == pytest.approx(0.0)


def test_features_and_coefficients_reconstruct_ln_utility(core, persona):
    """M5 消费的是这张表，所以它必须能重建 ln V。"""
    for rec in core.cities.values():
        gt = core.score(persona, rec.city, 2024)
        rebuilt = gt.ln_kappa + sum(gt.coefficients[k] * gt.features[k] for k in gt.features)
        assert rebuilt == pytest.approx(gt.ln_utility, rel=1e-12)


def test_ln_kappa_is_not_a_feature_column(core, persona, city_a):
    """它跨城恒定，放进设计矩阵就是一列零方差，直接把秩搞亏。"""
    gt = core.score(persona, city_a, 2024)
    assert "ln_kappa" not in gt.features
    assert set(gt.features) == {FEATURE_LN_Y, FEATURE_LN_P} | {
        f"ln_a_{d}" for d in persona.params.theta
    }
