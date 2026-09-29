"""窗口 `W = ŝ_shuffled − λ̂` 的联合不确定性，以及 `r` 的区间。

`DECISIONS/0015` 第七节。

**`SE(W)` 必须来自同一个 replicate 里 `ŝ_shuffled − λ̂` 的变化。**
不能分别估 `SE(ŝ)` 与 `SE(λ̂)` 再按独立相加：

    Var(ŝ − λ̂) = Var(ŝ) + Var(λ̂) − 2·Cov(ŝ, λ̂)

两者来自同一批人设、同一次几何构造，`Cov` 一般为正（共享的抽样波动同向推
两个斜率），所以独立假设会**高估** `SE(W)`，把 2b 判严；若 `Cov` 为负则相反。
两个方向都错，而错的方向取决于数据，事先不知道。

`r` 的区间同理：**按 replicate 重算整条 `r`**，不是把三个斜率各自的区间拼起来。
三个斜率、中心 `c`、以及协议要求的 σ 校准不确定性都要一起传。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

#: 少于这个数的有效 replicate 不足以支撑正式 SE。低于它返回 pending_computation。
MIN_REPLICATES_FOR_FORMAL_SE = 200


@dataclass(frozen=True)
class WindowUncertainty:
    """窗口与 `r` 的联合不确定性。**状态与数值分开。**"""

    status: str  # "ok" | "pending_computation" | "no_valid_replicates"
    n_requested: int
    n_valid: int
    window_point: float | None
    window_se: float | None
    window_ci: tuple[float, float] | None
    r_point: float | None
    r_ci: tuple[float, float] | None
    #: `Corr(ŝ_shuffled, λ̂)` —— 独立假设错得有多离谱，直接量出来。
    corr_s_lambda: float | None
    sigma_treated_as: str
    coverage: str

    @property
    def usable_for_gate(self) -> bool:
        return self.status == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "n_requested": self.n_requested,
            "n_valid": self.n_valid,
            "min_replicates_for_formal_se": MIN_REPLICATES_FOR_FORMAL_SE,
            "window_point": self.window_point,
            "window_se": self.window_se,
            "window_ci": list(self.window_ci) if self.window_ci else None,
            "r_point": self.r_point,
            "r_ci": list(self.r_ci) if self.r_ci else None,
            "corr_s_shuffled_lambda": self.corr_s_lambda,
            "se_basis": "per-replicate (s_shuffled − lambda); NOT Var(s)+Var(lambda)",
            "sigma_treated_as": self.sigma_treated_as,
            "coverage": self.coverage,
        }


def window_uncertainty(
    *,
    s_shuffled: np.ndarray,
    lam: np.ndarray,
    s_matched: np.ndarray | None,
    n_requested: int,
    level: float = 0.95,
    sigma_treated_as: str = "fixed_exogenous_from_repeat_subset",
    coverage: str = "pointwise_at_one_t",
) -> WindowUncertainty:
    """从 bootstrap replicate 的三条斜率算窗口与 `r` 的区间。

    输入是**逐 replicate 对齐**的数组：`s_shuffled[j]`、`lam[j]`、`s_matched[j]`
    来自同一个 replicate。对齐是全部要点——错位就等于假设独立。
    """
    s = np.asarray(s_shuffled, dtype=float)
    ll = np.asarray(lam, dtype=float)
    ok = np.isfinite(s) & np.isfinite(ll)
    if s_matched is not None:
        m = np.asarray(s_matched, dtype=float)
        ok &= np.isfinite(m)
    n_valid = int(ok.sum())

    if n_valid == 0:
        return WindowUncertainty(
            "no_valid_replicates",
            n_requested,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            sigma_treated_as,
            coverage,
        )

    w = s[ok] - ll[ok]  # ← 同一 replicate 内相减，联合波动自动带上
    corr = (
        float(np.corrcoef(s[ok], ll[ok])[0, 1])
        if n_valid > 2 and np.std(s[ok]) > 0 and np.std(ll[ok]) > 0
        else None
    )
    lo, hi = (1.0 - level) / 2.0, 1.0 - (1.0 - level) / 2.0

    r_point = r_ci = None
    if s_matched is not None:
        mm = np.asarray(s_matched, dtype=float)[ok]
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(w > 0, (mm - ll[ok]) / w, np.nan)  # 窗口非正 → 不发 r
        good = r[np.isfinite(r)]
        if good.size:
            r_point = float(np.median(good))
            r_ci = (float(np.quantile(good, lo)), float(np.quantile(good, hi)))

    status = "ok" if n_valid >= MIN_REPLICATES_FOR_FORMAL_SE else "pending_computation"
    return WindowUncertainty(
        status=status,
        n_requested=n_requested,
        n_valid=n_valid,
        window_point=float(np.median(w)),
        window_se=float(np.std(w, ddof=1)) if n_valid > 1 else None,
        window_ci=(float(np.quantile(w, lo)), float(np.quantile(w, hi))) if n_valid > 1 else None,
        r_point=r_point,
        r_ci=r_ci,
        corr_s_lambda=corr,
        sigma_treated_as=sigma_treated_as,
        coverage=coverage,
    )


def independent_se_would_be(s_shuffled: np.ndarray, lam: np.ndarray) -> dict[str, float]:
    """**只作诊断**：独立假设下的 SE 与联合 SE 差多少。

    把这个数放进产出，是为了让"不能假设独立"这句话有一个可核对的量，而不是
    一句纯断言。
    """
    s = np.asarray(s_shuffled, dtype=float)
    ll = np.asarray(lam, dtype=float)
    joint = float(np.std(s - ll, ddof=1))
    naive = float(np.sqrt(np.var(s, ddof=1) + np.var(ll, ddof=1)))
    return {
        "joint_se": joint,
        "naive_independent_se": naive,
        "ratio_naive_over_joint": naive / joint if joint > 0 else float("nan"),
        "note": "naive 假设 Cov=0；正相关时它高估，负相关时低估。方向由数据决定",
    }
