"""差分正态分支的 golden 回归保护。

新增 ALR 分支时 ``msl.py`` 抽出了一段两分支共用的 ``_panel_terms``。抽取不改
运算顺序，所以结果应当**逐位**不变——这组数是在抽取之前、在一个干净的工作
树上冻结下来的（``git`` 历史里的 P9 提交），不是事后从新代码里抄回来的。

哪天这些数动了，要么是有人改了差分分支的数学，要么是浮点运算顺序被动过；
两种都必须是有意的、写进 `DECISIONS/` 的改动，不能是重构的副产品。
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.recovery import RecoveryConfig, fit_recovery
from eval.recovery.msl import N_RANDOM, _msl_objective, layout, pack, standard_normal_draws

from .synthetic import make_synthetic

# 冻结点：n_personas=120, n_tasks=6, n_alts=7, sigma=0.08, seed=20260912
GOLDEN_FIT = {
    ("diagonal", "material"): (-1072.384336706854, 11.436220803469839, 0.3370511289174085),
    ("diagonal", "safety"): (-1094.7002198215364, 11.014085890550758, 0.3512257353002216),
    ("diagonal", "environment"): (-1082.6723874542138, 11.344415208768385, 0.3337202707990079),
    ("full", "material"): (-1045.1899611966828, 12.425217357706506, 0.3484910340239089),
    ("full", "safety"): (-1051.0859842323111, 12.342105433809557, 0.3297081192770654),
    ("full", "environment"): (-1047.5782646299313, 12.402164324335919, 0.3392807119971283),
}

GOLDEN_NLL = 1162.1758135573941
GOLDEN_GRAD = (
    -3.2807070443,
    -20.1227205264,
    -20.6161135533,
    -23.0975147864,
    -25.9604742142,
    -17.3597464614,
    -21.6974326558,
    -10.5382940553,
    -11.0489885802,
    -13.5653248445,
    -13.2339972051,
    -9.5156780218,
    -13.3495854543,
)


@pytest.fixture(scope="module")
def frozen():
    return make_synthetic(n_personas=120, n_tasks=6, n_alts=7, sigma=0.08, seed=20260912)


def test_the_likelihood_and_gradient_at_a_fixed_point_are_unchanged(frozen):
    """纯数学，不过优化器：这一条动了就是似然本身被改了。"""
    lay = layout(frozen.dataset, 0)
    xi = standard_normal_draws(frozen.dataset.n_personas, 40, N_RANDOM, seed=5)
    theta = pack(5.0, np.full(N_RANDOM, 0.3), np.diag(np.full(N_RANDOM, 0.4)), "diagonal")
    nll, grad = _msl_objective(theta, lay, xi, "diagonal", 16)

    assert nll == GOLDEN_NLL  # 逐位
    assert np.allclose(grad, GOLDEN_GRAD, rtol=0, atol=1e-9)


def test_the_design_matrix_is_unchanged(frozen):
    from eval.recovery import design_matrix

    lay = layout(frozen.dataset, 0)
    design = design_matrix(frozen.dataset.features, 0)
    assert np.array_equal(design[:, 0], lay.xb)
    assert np.array_equal(design[:, 1:], lay.z)
    # ALR 分支新增的原始属性列不得改动差分那两个
    assert np.array_equal(lay.x, frozen.dataset.features)


@pytest.mark.parametrize(("covariance", "base_dim"), sorted(GOLDEN_FIT))
def test_fitted_parameters_have_not_drifted(frozen, covariance, base_dim):
    loglik, gamma_0, center_material = GOLDEN_FIT[(covariance, base_dim)]
    result = fit_recovery(
        frozen.dataset,
        RecoveryConfig(base_dim=base_dim, covariance=covariance, n_draws=60, seed=3, maxiter=200),
    )
    assert result.parameterization == "difference_normal"
    assert result.diagnostics.loglik == pytest.approx(loglik, rel=1e-12)
    assert result.gamma_0 == pytest.approx(gamma_0, rel=1e-12)
    assert result.w_center_hat[0] == pytest.approx(center_material, rel=1e-12)
    assert result.w_center_hat.sum() == pytest.approx(1.0, abs=1e-12)
    assert result.diagnostics.covariance.rank == 6


def test_the_default_parameterization_is_still_the_difference_method():
    """换默认估计器是一次科学裁决。没有裁决之前，这条测试把默认钉住。"""
    assert RecoveryConfig().parameterization == "difference_normal"
    assert RecoveryConfig().covariance == "diagonal"


def test_difference_normal_still_reports_illegal_weights_without_fixing_them(frozen):
    """差分分支**继续**如实报告越界。ALR 的存在不是让它去裁剪的理由。"""
    result = fit_recovery(frozen.dataset, RecoveryConfig(n_draws=60, seed=3, maxiter=200))
    report = result.diagnostics.individual_simplex
    assert report.fraction_outside > 0.0
    assert (result.w_hat < 0).any() or (result.w_hat > 1).any()
    assert np.abs(result.w_hat.sum(axis=1) - 1.0).max() < 1e-12
