"""P2：货币度量与两个真值对象。§9.1.1 的美元口径由此而来。"""

from __future__ import annotations

import math

import pytest

from econ.types import GroundTruth
from econ.utility import (
    MONEY_METRIC_LN_FLOOR,
    ln_money_metric,
    money_metric_dollars,
    welfare_loss,
)


def test_at_the_reference_point_the_metric_is_just_income(persona):
    """参照点 P=1、A=1 上 ln e 应当恰好等于 ln Y。

    这条是整个货币度量的定义性检查：如果它不成立，e 就不是"在参照点上
    需要多少收入"，那么它的美元单位是假的。
    """
    ln_y = math.log(60_000.0)
    ln_v = persona.params.w_material * ln_y  # ln κ = 0, ln P = 0, ln A = 0
    assert ln_money_metric(ln_v, 0.0, persona.params) == pytest.approx(ln_y, rel=1e-12)


def test_the_truth_stores_logs_not_dollars(core, persona, city_a):
    """存对数不存美元：1/w₁ 在指数上，直接存美元会让低 w₁ 的人设静默下溢。"""
    gt = core.score(persona, city_a, 2024)
    assert math.isfinite(gt.ln_money_metric)


def test_a_low_material_weight_yields_none_not_zero():
    """失败形态必须是 None，不能是 0.0。

    "没有损失"是最好的结果，"美元口径对这个人没有意义"是最坏的；两者
    静默地写成同一个 0，报表上分不开。
    """
    assert money_metric_dollars(MONEY_METRIC_LN_FLOOR - 0.1) is None
    assert money_metric_dollars(MONEY_METRIC_LN_FLOOR + 0.1) is not None
    assert welfare_loss(MONEY_METRIC_LN_FLOOR - 1.0, MONEY_METRIC_LN_FLOOR - 2.0) is None


def test_welfare_loss_uses_expm1_for_close_alternatives():
    """两座城市接近时直接相减会丢掉几乎全部有效数字，而"接近"正是并列档
    与低离散度选择集的常态。

    对照值用 Decimal 高精度算，不能用 float 再算一遍：按 Sterbenz 引理，
    两个相近浮点数的**减法本身是精确的**，误差全部来自两次 exp 各自约
    一个 ulp。拿 float 版当"精确值"只会把两种实现的同一个误差抵消掉，
    测不出任何东西。
    """
    from decimal import Decimal, getcontext

    getcontext().prec = 60
    ln_a, ln_b = 11.0, 11.0 - 1e-9
    truth = Decimal(ln_a).exp() - Decimal(ln_b).exp()

    naive = math.exp(ln_a) - math.exp(ln_b)
    stable = welfare_loss(ln_a, ln_b)

    err_naive = abs(Decimal(naive) - truth)
    err_stable = abs(Decimal(stable) - truth)
    assert err_stable < err_naive / 100, (
        f"expm1 路径没有明显更准：stable 误差 {err_stable:.3e}，naive 误差 {err_naive:.3e}"
    )


def test_arguments_the_wrong_way_round_are_refused():
    """传反了参数会得到负的福利损失，而那读起来像"模型比真值还好"。"""
    with pytest.raises(ValueError, match="传反了"):
        welfare_loss(10.0, 11.0)


def test_groundtruth_still_refuses_to_reach_a_prompt(core, persona, city_a):
    """新增字段不得破坏硬规则 1 的边界。"""
    from econ.types import AgentInput, BoundaryViolationError

    gt = core.score(persona, city_a, 2024)
    assert isinstance(gt, GroundTruth)
    with pytest.raises(BoundaryViolationError):
        AgentInput(brief=persona.brief, options=(gt,))  # type: ignore[arg-type]


def test_the_band_is_guarded_on_both_sides():
    """回归测试。第一版只守了下侧，于是 w₁ 很小且效用高于 κ 时
    `math.exp` 直接抛 OverflowError。

    两侧是同一件事：这个人几乎不在乎钱，所以"多少钱能补偿"没有有意义的
    答案。一年十亿美元不是对个人年度福利的回答，正如一分钱不是。
    """
    from econ.utility import MONEY_METRIC_LN_CEILING

    assert money_metric_dollars(MONEY_METRIC_LN_CEILING + 0.1) is None
    assert money_metric_dollars(MONEY_METRIC_LN_CEILING - 0.1) is not None
    # 不得抛异常
    assert welfare_loss(MONEY_METRIC_LN_CEILING + 50.0, MONEY_METRIC_LN_CEILING + 49.0) is None


def test_an_extreme_low_material_weight_does_not_raise():
    """构造出会溢出的那个情形，确认它返回 None 而不是炸掉。"""
    from econ.types import PersonaParams

    starved = PersonaParams(alpha=0.0015, beta=0.0005, theta={"a": 0.5, "b": 0.3, "c": 0.198})
    ln_e = ln_money_metric(ln_utility=5.0, ln_kappa_value=-0.3, params=starved)
    assert ln_e > 1000  # 确实在溢出区
    assert money_metric_dollars(ln_e) is None
