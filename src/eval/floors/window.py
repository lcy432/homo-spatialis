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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: 少于这个数的有效 replicate 不足以支撑正式 SE。低于它返回 pending_computation。
MIN_REPLICATES_FOR_FORMAL_SE = 200


#: 非正窗口的比例超过它，`r` 的区间一律不报——剩下那几个正窗口是**选择性
#: 保留**的样本，它们的分位数不是置信区间。
MAX_NONPOSITIVE_WINDOW_FRACTION = 0.10


@dataclass(frozen=True)
class WindowUncertainty:
    """窗口与 `r` 的不确定性。

    **窗口 SE 可估，不代表 `r` 的区间可用**，两者的有效次数不是同一个数：

        窗口 SE 需要  s 与 λ 都有限
        r      还需要  W = s − λ > 0

    窗口大量为负时，`W > 0` 的那几次是**被条件筛出来**的样本；对它们取分位数
    得到的不是置信区间，而是"在窗口恰好为正的那些重抽上，r 长什么样"。
    这两件事必须分开记、分开判。
    """

    status: str  # 窗口 SE 的状态
    r_status: str  # `r` 区间的状态，**独立判定**
    n_requested: int
    #: 窗口 SE 的有效次数：s 与 λ 都有限。
    n_valid: int
    #: `r` 的有效次数：**还要求 W > 0**。
    n_valid_r: int
    nonpositive_window_fraction: float
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
        """门二 2b 只要窗口 SE。它**不要求** `r` 区间可用。"""
        return self.status == "ok"

    @property
    def r_is_reportable(self) -> bool:
        return self.r_status == "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "r_status": self.r_status,
            "n_requested": self.n_requested,
            "n_valid_window": self.n_valid,
            "n_valid_r": self.n_valid_r,
            "nonpositive_window_fraction": self.nonpositive_window_fraction,
            "max_nonpositive_window_fraction": MAX_NONPOSITIVE_WINDOW_FRACTION,
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
            "why_two_counts": (
                "窗口 SE 只要 s 与 λ 有限；r 还要求 W>0。非正窗口被丢掉之后，"
                "剩下的是选择性样本，它们的分位数**不是**置信区间"
            ),
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
            status="no_valid_replicates",
            r_status="no_valid_replicates",
            n_requested=n_requested,
            n_valid=0,
            n_valid_r=0,
            nonpositive_window_fraction=float("nan"),
            window_point=None,
            window_se=None,
            window_ci=None,
            r_point=None,
            r_ci=None,
            corr_s_lambda=None,
            sigma_treated_as=sigma_treated_as,
            coverage=coverage,
        )

    w = s[ok] - ll[ok]  # ← 同一 replicate 内相减，联合波动自动带上
    positive = w > 0
    n_positive = int(positive.sum())
    nonpos_frac = float(1.0 - n_positive / n_valid)

    corr = (
        float(np.corrcoef(s[ok], ll[ok])[0, 1])
        if n_valid > 2 and np.std(s[ok]) > 0 and np.std(ll[ok]) > 0
        else None
    )
    lo, hi = (1.0 - level) / 2.0, 1.0 - (1.0 - level) / 2.0

    # ---- 窗口 SE：只要 s 与 λ 有限就能估 ----
    status = "ok" if n_valid >= MIN_REPLICATES_FOR_FORMAL_SE else "pending_computation"
    window_point = float(np.median(w))
    window_se = float(np.std(w, ddof=1)) if n_valid > 1 else None
    window_ci = (float(np.quantile(w, lo)), float(np.quantile(w, hi))) if n_valid > 1 else None

    # ---- r：还要求 W > 0，**而且**非正窗口不能太多 ----
    #
    # 丢掉非正窗口之后剩下的是**条件筛出来**的样本。199/200 次窗口为负时，
    # 对唯一那次的 r 取分位数会得到一个宽度为零的"区间"，看起来极其精确——
    # 那不是精度，那是样本量为一。所以 r 的状态独立判，并且明确拦住高比例
    # 非正窗口的情形。
    r_point = r_ci = None
    if s_matched is None:
        r_status = "no_matched_arm"
    elif n_positive == 0:
        r_status = "all_windows_nonpositive"
    elif nonpos_frac > MAX_NONPOSITIVE_WINDOW_FRACTION:
        r_status = "too_many_nonpositive_windows"
    elif n_positive < MIN_REPLICATES_FOR_FORMAL_SE:
        r_status = "pending_computation"
    else:
        r_status = "ok"

    if s_matched is not None and n_positive > 0:
        mm = np.asarray(s_matched, dtype=float)[ok][positive]
        r = (mm - ll[ok][positive]) / w[positive]
        good = r[np.isfinite(r)]
        if good.size:
            # 点估计照常给（带着 r_status 一起读），区间只在 r_status == "ok" 时给。
            r_point = float(np.median(good))
            if r_status == "ok":
                r_ci = (float(np.quantile(good, lo)), float(np.quantile(good, hi)))

    return WindowUncertainty(
        status=status,
        r_status=r_status,
        n_requested=n_requested,
        n_valid=n_valid,
        n_valid_r=n_positive,
        nonpositive_window_fraction=nonpos_frac,
        window_point=window_point,
        window_se=window_se,
        window_ci=window_ci,
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


# --------------------------------------------------------------------------
# 联合重抽：两个臂共用一份抽样索引
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PairedReplicate:
    """一次重抽里两个臂的配对结果。**按 replicate ID 联合处理。**"""

    index: int
    seed: int
    ok: bool
    failure: str | None = None
    failed_arm: str | None = None
    #: 该次抽中的人（含重复），两个臂用的是同一份。
    picks: tuple[str, ...] = ()
    #: 该次重估的匹配中心；几何在这次抽样内共享。
    centre: tuple[float, ...] = ()
    #: `{(t, stratum): slope}`。t 为 None 表示不分 t；stratum 为 None 表示全样本。
    slopes: Mapping[str, float] = field(default_factory=dict)


def joint_bootstrap(
    *,
    n_replicates: int,
    seed: int,
    draw_picks: Callable[[int], Sequence[str]],
    fit_both: Callable[[Sequence[str], int], Mapping[str, float]],
) -> list[PairedReplicate]:
    """跑 `n_replicates` 次**联合**重抽。

    `draw_picks(rep_seed)` 给这一次的人设索引；`fit_both(picks, rep_seed)` 必须
    在**同一份 picks** 上跑完两个臂、重估中心、构造一次共享几何，再返回该次的
    成对斜率。

    **任何一个臂失败 → 整个 replicate 作废。** 不能各臂各自删掉失败记录之后
    再把两个数组拼起来——那样第 j 个 `s_shuffled` 与第 j 个 `λ` 就不再来自同一次
    抽样，而 `SE(W)` 的全部意义就在于它们来自同一次。
    """
    seeds = np.random.default_rng(seed).integers(0, 2**31 - 1, size=n_replicates)
    out: list[PairedReplicate] = []
    for i, rs in enumerate(seeds):
        rep_seed = int(rs)
        try:
            picks = list(draw_picks(rep_seed))
            slopes = dict(fit_both(picks, rep_seed))
            out.append(
                PairedReplicate(
                    index=i,
                    seed=rep_seed,
                    ok=True,
                    picks=tuple(picks),
                    centre=tuple(float(v) for v in slopes.pop("_centre", ())),
                    slopes=slopes,
                )
            )
        except Exception as exc:
            out.append(
                PairedReplicate(
                    index=i,
                    seed=rep_seed,
                    ok=False,
                    failure=f"{type(exc).__name__}: {exc}"[:200],
                    failed_arm=getattr(exc, "arm", None),
                )
            )
    return out


def paired_slopes(replicates: Sequence[PairedReplicate], key: str) -> tuple[np.ndarray, list[int]]:
    """取某个 (t, 层) 上的成对斜率，并返回它们各自的 replicate ID。

    返回 ID 是为了让调用方能核对：两个臂的数组**必须**来自同一组 ID。
    """
    ids = [r.index for r in replicates if r.ok and key in r.slopes]
    return np.array([r.slopes[key] for r in replicates if r.ok and key in r.slopes]), ids
