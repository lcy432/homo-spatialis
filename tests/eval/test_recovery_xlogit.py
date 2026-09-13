"""与 xlogit 的交叉验证，以及对它能力边界的**实测**（P9 第四节）。

CLAUDE.md 把 xlogit 列为 M5 的核心依赖，所以"它支持什么"不能靠假设。这里把
那张能力表变成会失败的断言：哪天换了版本、多出了相关随机参数或个体后验，
这些测试会立刻告诉我们该回去简化 ``msl.py`` 了。
"""

from __future__ import annotations

import numpy as np
import pytest

from eval.recovery import RecoveryConfig, fit_recovery
from eval.recovery.xlogit_backend import (
    cross_check_diagonal,
    supports_correlated_random_parameters,
    xlogit_available,
)

pytestmark = pytest.mark.skipif(not xlogit_available(), reason="未安装 xlogit")

from .synthetic import make_synthetic  # noqa: E402


def test_the_backend_really_does_not_offer_correlated_random_parameters():
    """完全协方差规格必须自己实现——这条记录的是那个理由，不是一句话说明。"""
    assert supports_correlated_random_parameters() is False


def test_the_backend_really_does_not_offer_individual_posterior_means():
    from xlogit import MixedLogit

    names = {n for n in dir(MixedLogit) if not n.startswith("_")}
    assert not {n for n in names if "posterior" in n or "individual" in n}
    assert "predict" in names  # 有 predict 不等于有个体后验


def test_the_backend_does_aggregate_the_likelihood_across_a_panel():
    import inspect

    from xlogit import MixedLogit

    assert "panels" in inspect.signature(MixedLogit.fit).parameters
    source = inspect.getsource(MixedLogit)
    assert "_prob_product_across_panels" in source


def test_our_diagonal_fit_agrees_with_xlogit_on_the_same_data():
    """同一批数据、同一个对角规格，两个独立实现应当给出接近的参数。

    不要求逐元素相等：两边的 Halton 构造与优化路径都不同，模拟误差本来就在
    百分之几的量级。要抓的是"手写似然写错了"这种量级的差异。
    """
    s = make_synthetic(n_personas=150, n_tasks=6, n_alts=7, sigma=0.08, seed=11)
    ours = fit_recovery(s.dataset, RecoveryConfig(n_draws=150, seed=2))
    theirs = cross_check_diagonal(s.dataset, 0, n_draws=150, seed=2)

    assert theirs.converged
    assert ours.gamma_0 == pytest.approx(theirs.gamma_0, rel=0.10)
    assert np.abs(ours.mu_gamma - theirs.mu_gamma).max() < 0.15 * np.abs(theirs.mu_gamma).max()

    ours_sd = np.sqrt(np.diag(ours.sigma_gamma))
    assert np.abs(ours_sd - theirs.sd_gamma).max() < 0.20 * theirs.sd_gamma.max()

    # 两个后端的对数似然应当落在同一个邻域（同一个模型、同样的抽样数）
    assert abs(ours.diagnostics.loglik - theirs.loglik) < 0.02 * abs(theirs.loglik)
