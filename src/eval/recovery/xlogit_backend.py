"""xlogit 交叉验证后端（对角规格）。

CLAUDE.md 的技术栈把 `xlogit` 列为 M5 的核心依赖。实测 0.2.7 的能力边界：

| 需要的能力 | 0.2.7 是否支持 | 依据 |
|---|---|---|
| 一个估计的固定系数 `γ_0` | 是 | 不在 ``randvars`` 里的列即固定系数 |
| 六个随机系数 | 是 | ``randvars={col: 'n'}`` |
| 面板似然（按人设聚合） | 是 | ``panels=`` + ``_prob_product_across_panels`` |
| 完全协方差 | **否** | ``fit`` 签名无 correlation 参数；源码里 "correlat" 只在 Halton 文档串中 |
| 个体条件后验均值 | **否** | 只有 ``predict``，无个体后验 |

因此它在 P9 里的角色是**对角规格的交叉验证**，不是主后端。两个后端在同一批
数据上给出接近的 `γ_0`、`μ_γ` 与 `Σ_γ` 对角线，就说明手写似然没写错；给不出
时以手写似然为准并报差异（它是唯一能同时表达完全协方差与个体后验的那个）。

这个模块是可选依赖：没装 xlogit 时导入本模块不报错，调用时才抛
``XlogitUnavailableError``。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .design import design_column_names, differenced
from .schema import ChoiceDataset


class XlogitUnavailableError(RuntimeError):
    """未安装 xlogit，或安装的版本不支持所需设定。"""


@dataclass(frozen=True)
class XlogitCheck:
    gamma_0: float
    mu_gamma: np.ndarray
    sd_gamma: np.ndarray
    loglik: float
    converged: bool


def xlogit_available() -> bool:
    try:
        import xlogit  # noqa: F401
    except ImportError:
        return False
    return True


def supports_correlated_random_parameters() -> bool:
    """实测而非假设：签名里有没有相关参数这个旋钮。"""
    try:
        from xlogit import MixedLogit
    except ImportError:
        return False
    import inspect

    params = inspect.signature(MixedLogit.fit).parameters
    return any("correl" in name.lower() for name in params)


def cross_check_diagonal(
    dataset: ChoiceDataset,
    base: int,
    *,
    n_draws: int = 200,
    seed: int = 0,
    maxiter: int = 2000,
) -> XlogitCheck:
    """用 xlogit 跑同一个对角规格，返回可与 ``MslFit`` 直接比对的参数。"""
    try:
        from xlogit import MixedLogit
    except ImportError as exc:  # pragma: no cover - 取决于环境
        raise XlogitUnavailableError("未安装 xlogit") from exc

    xb, z = differenced(dataset.features, base)
    names = design_column_names(base)
    x = np.column_stack([xb, z])

    model = MixedLogit()
    model.fit(
        X=x,
        y=dataset.chosen.astype(int),
        varnames=list(names),
        alts=np.asarray(dataset.alternative_ids),
        ids=np.asarray(dataset.choice_set_ids),
        panels=np.asarray(dataset.persona_ids),
        randvars={n: "n" for n in names[1:]},
        n_draws=n_draws,
        random_state=seed,
        maxiter=maxiter,
        verbose=0,
    )

    coeff = dict(zip(model.coeff_names, model.coeff_, strict=True))
    mu = np.array([coeff[n] for n in names[1:]], dtype=float)
    sd = np.array([abs(coeff[f"sd.{n}"]) for n in names[1:]], dtype=float)
    return XlogitCheck(
        gamma_0=float(coeff[names[0]]),
        mu_gamma=mu,
        sd_gamma=sd,
        loglik=float(model.loglikelihood),
        converged=bool(model.convergence),
    )
