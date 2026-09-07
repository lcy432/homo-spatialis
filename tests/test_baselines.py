"""C1：三条零调用基准线与噪声地板。"""

from __future__ import annotations

import numpy as np
import pytest

from common.providers import ExpenditureShares
from eval.baselines import (
    baselines_for,
    linear_form,
    material_only_params,
    theta_vector,
    with_shares,
)
from eval.perturbation import sample_truncated
from worlds.personas import PersonaGenerator

SBAR = ExpenditureShares.from_raw(0.324, 0.334, 0.342)


@pytest.fixture
def cities(world):
    return [rec.city for rec in world.cities.values()]


@pytest.fixture
def perturbations(persona):
    pts = np.array([persona.params.w_material, *persona.params.theta.values()]) * 100.0
    return sample_truncated(pts, 32, np.random.default_rng(0))


# --------------------------------------------------------------------------
# 硬规则 13：EV 永远在真值参数下评价
# --------------------------------------------------------------------------


def test_losses_are_never_negative(core, cities, perturbations):
    """真值最优在真值参数下就是最优，所以任何其他选择的损失都 ≥ 0。

    这条是硬规则 13 的直接后果，也是它被违反时最先炸的地方：若用近似参数
    同时做选择和评价，被选中的城市在**它自己的**参数下是最优，损失会变成
    负的或恒零，而那读起来像"近似比真值还好"。
    """
    for p in PersonaGenerator().generate(20, seed=5):
        b = baselines_for(core, p, cities, SBAR, perturbations=perturbations)
        for value in (b.l_rep, b.l_s, b.l_amen, b.noise_floor):
            if value is not None:
                assert value >= -1e-9, f"{p.persona_id} 出现负损失 {value}"


def test_evaluating_with_approximate_params_would_give_zero(core, persona, cities):
    """把硬规则 13 违反出来，确认后果确实是"自洽的胡话"。

    在近似参数下选、又在近似参数下评价，损失恒为零——因为被选中的正是那组
    参数下的最优。这个版本跑起来完全正常，每个数看着都合理。
    """
    rep = with_shares(persona, SBAR)
    form = linear_form(core, rep, cities)
    params = material_only_params(SBAR)
    ln_e = form.ln_money_metric(params.w_material, theta_vector(params, form.dimensions))
    chosen = int(np.argmax(ln_e))
    assert float(ln_e.max()) - float(ln_e[chosen]) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# 线性形式：与参数无关的部分确实与参数无关
# --------------------------------------------------------------------------


def test_the_linear_form_is_independent_of_the_weights(core, persona, cities):
    """噪声地板把 32 次扰动退化成 32 次点积，靠的就是这条。若它不成立，
    那个优化就是错的——而且是静默错的。"""
    from dataclasses import replace

    from econ.types import PersonaParams

    a = linear_form(core, persona, cities)
    other = replace(
        persona,
        params=PersonaParams(
            alpha=0.5 * (persona.shares.s_goods + persona.shares.s_other),
            beta=0.5 * persona.shares.s_rent,
            theta=dict.fromkeys(persona.params.theta, 0.5 / len(persona.params.theta)),
        ),
    )
    b = linear_form(core, other, cities)
    assert np.allclose(a.material, b.material)
    assert np.allclose(a.amenity, b.amenity)


def test_the_factored_form_reproduces_m2_exactly(core, persona, cities):
    """点积必须与走 score_set 得到的 ln V 逐位相同，否则就是第二套实现。"""
    form = linear_form(core, persona, cities)
    theta = theta_vector(persona.params, form.dimensions)
    got = form.ln_v_minus_kappa(persona.params.w_material, theta)
    truth = core.score_set(persona, cities, 2024)
    want = np.array([truth.ln_utility[c.code] - truth.ln_kappa for c in cities])
    assert np.allclose(got, want, atol=1e-12)


def test_material_only_params_zero_every_amenity():
    """现状工具没有偏好权重维度，只按实际收入排序（§1.2 问题二）。"""
    params = material_only_params(SBAR)
    assert params.w_material == pytest.approx(1.0)
    assert all(v == 0.0 for v in params.theta.values())


def test_changing_shares_leaves_the_scale_weights_alone(persona):
    """§7.2 两通道各自独立：换份额只改 α/β 的**劈分**，不改 w₁ 与 θ。"""
    swapped = with_shares(persona, SBAR)
    assert swapped.params.w_material == pytest.approx(persona.params.w_material)
    assert swapped.params.theta == persona.params.theta
    assert swapped.params.beta / swapped.params.w_material == pytest.approx(SBAR.s_rent)
