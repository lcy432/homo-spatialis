"""研究主规格（profile）：P8 与未来主实验**共用的唯一拟合入口**。

`DECISIONS/0013` 裁定的主规格：

    ALR logistic-normal + 完全协方差 + material 基准
    四个固定起点 sd0 ∈ {0.1, 0.5, 1.0, 2.0}，同一批积分 draws、同一目标函数，
    **按有效拟合的模拟似然选解**

--------------------------------------------------------------------------
为什么是 profile，而不是改默认值
--------------------------------------------------------------------------

``RecoveryConfig()`` 的默认值保持 ``difference_normal`` + ``diagonal`` 不变，
旧调用者与 golden 回归测试一字不动。换默认是一次独立的工程兼容决定，与
"研究主规格选哪个"分开处理（`DECISIONS/0013` 第五节）。

**P8 与主实验必须走 ``fit_research``**，不得各自拼一份 ``RecoveryConfig``——
两份配置迟早漂移，而漂移之后地板与主实验不可比，且没有任何测试会失败。

--------------------------------------------------------------------------
失败处理
--------------------------------------------------------------------------

四个起点全部失败时返回 ``ResearchFitError``，**不偷偷退回**差分法、对角法或
单起点。调用方必须显式处理这个返回值。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from .alr import pack_alr, unpack_alr
from .design import base_index
from .estimator import RecoveryConfig
from .msl import N_RANDOM, layout
from .msl_alr import _starting_point
from .results import RecoveryResult
from .schema import ChoiceDataset

#: 主规格的四个起点。只有初始离散度不同，ρ 与 μ_a 一律取池化条件 logit 的隐含值。
MULTISTART_SD0: tuple[float, ...] = (0.1, 0.5, 1.0, 2.0)


@dataclass(frozen=True)
class ResearchProfile:
    """一个被冻结的研究规格。指纹进产出，证明地板与主实验用的是同一套。"""

    name: str
    config: RecoveryConfig
    start_sd0: tuple[float, ...] = MULTISTART_SD0

    def fingerprint(self) -> dict[str, Any]:
        c = self.config
        return {
            "profile": self.name,
            "parameterization": c.parameterization,
            "covariance": c.covariance,
            "base_dim": c.base_dim,
            "n_draws": c.n_draws,
            "draw_method": c.draw_method,
            "draw_seed": c.seed,
            "maxiter": c.maxiter,
            "tol": c.tol,
            "population_moment_draws": c.simplex_draws,
            "start_sd0": list(self.start_sd0),
            "selection_rule": "max_simulated_loglik_over_starts",
        }


#: **当前主规格。** 200 Halton draws、seed=1、总体矩 20,000 draws 是诊断基线，
#: `runs/2026-09-16-p9-evidence/` 下的最小核查确认它对本轮输出足够
#: （见 `DECISIONS/0013` 第四节的容差与残差）。
RESEARCH_PROFILE_ALR_FULL_MATERIAL = ResearchProfile(
    name="alr_full_material_2026_09",
    config=RecoveryConfig(
        parameterization="alr_logistic_normal",
        covariance="full",
        base_dim="material",
        n_draws=200,
        seed=1,
        draw_method="halton",
        maxiter=500,
        tol=1e-7,
        simplex_draws=20_000,
    ),
)


@dataclass(frozen=True)
class StartOutcome:
    sd0: float
    ok: bool
    loglik: float = float("nan")
    converged: bool = False
    failure: str | None = None


@dataclass(frozen=True)
class ResearchFitError(RuntimeError):
    """四个起点全部失败。**不退回任何别的规格。**"""

    profile: str
    starts: tuple[StartOutcome, ...]

    def __str__(self) -> str:  # pragma: no cover - 展示用
        lines = [f"研究规格 {self.profile} 的全部 {len(self.starts)} 个起点都失败："]
        lines += [f"  sd0={s.sd0}: {s.failure}" for s in self.starts]
        return "\n".join(lines)


@dataclass(frozen=True)
class ResearchFit:
    """按似然选出的解，附全部起点的诊断。"""

    result: RecoveryResult
    chosen_sd0: float
    starts: tuple[StartOutcome, ...]
    profile: ResearchProfile = field(repr=False, default=RESEARCH_PROFILE_ALR_FULL_MATERIAL)

    @property
    def loglik_spread(self) -> float:
        good = [s.loglik for s in self.starts if s.ok]
        return float(max(good) - min(good)) if len(good) > 1 else 0.0

    @property
    def n_failed_starts(self) -> int:
        return sum(1 for s in self.starts if not s.ok)

    def summary(self) -> dict[str, Any]:
        return {
            **self.profile.fingerprint(),
            "chosen_sd0": self.chosen_sd0,
            "loglik": self.result.diagnostics.loglik,
            "loglik_spread_over_starts": self.loglik_spread,
            "n_failed_starts": self.n_failed_starts,
            "starts": [
                {"sd0": s.sd0, "ok": s.ok, "loglik": s.loglik, "converged": s.converged}
                for s in self.starts
            ],
        }


def _start_vector(dataset: ChoiceDataset, cfg: RecoveryConfig, sd0: float) -> np.ndarray:
    """ρ 与 μ_a 取池化条件 logit 的隐含值，只有 Cholesky 的初始离散度随 sd0 变。"""
    base = base_index(cfg.base_dim)
    theta_default, _ = _starting_point(layout(dataset, base), base, cfg.covariance)
    rho0, mu0, _ = unpack_alr(theta_default, cfg.covariance)
    return pack_alr(rho0, mu0, np.diag(np.full(N_RANDOM, sd0)), cfg.covariance)


def fit_research(
    dataset: ChoiceDataset,
    profile: ResearchProfile = RESEARCH_PROFILE_ALR_FULL_MATERIAL,
) -> ResearchFit:
    """主规格的唯一拟合入口。多起点、按模拟似然选解。

    **不要在 P8 或主实验里另起一个 ``fit_recovery`` 调用。** 两处各配一份
    ``RecoveryConfig`` 是本项目最容易发生、也最难发现的一类漂移：地板与主实验
    的 draws 数、容差、起点集合任何一项不同，地板就不再适用，而没有任何测试
    会因此失败。
    """
    if profile.config.parameterization != "alr_logistic_normal":
        raise ValueError("研究 profile 目前只支持 ALR 分支；差分分支保留为历史对照")

    outcomes: list[StartOutcome] = []
    fits: list[tuple[float, RecoveryResult]] = []
    for sd0 in profile.start_sd0:
        try:
            start = _start_vector(dataset, profile.config, sd0)
            result = _fit_with_start(dataset, profile.config, start)
        except Exception as exc:
            outcomes.append(StartOutcome(sd0=sd0, ok=False, failure=f"{type(exc).__name__}: {exc}"))
            continue
        outcomes.append(
            StartOutcome(
                sd0=sd0,
                ok=True,
                loglik=result.diagnostics.loglik,
                converged=result.diagnostics.converged,
            )
        )
        fits.append((sd0, result))

    if not fits:
        raise ResearchFitError(profile=profile.name, starts=tuple(outcomes))

    sd0, best = max(fits, key=lambda pair: pair[1].diagnostics.loglik)
    return ResearchFit(result=best, chosen_sd0=sd0, starts=tuple(outcomes), profile=profile)


def _fit_with_start(
    dataset: ChoiceDataset, cfg: RecoveryConfig, start: np.ndarray
) -> RecoveryResult:
    """带指定起点跑一次 ``fit_recovery``。

    ``RecoveryConfig`` 没有 start 字段（那是每次拟合的量，不是配置），所以这里
    直接走 ALR 的拟合函数再组装成 ``RecoveryResult``，与 ``fit_recovery`` 内部
    走的是同一条路径与同一批诊断。
    """
    from .estimator import _fit_recovery_alr

    return _fit_recovery_alr(dataset, replace(cfg), start=start)
