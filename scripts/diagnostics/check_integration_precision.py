"""固定参数下的积分精度检查（`MEMOS/2026-09-26-integration-precision-plan.md` 甲）。

**不拟合。** 参数、数据、σ 全部固定，只换积分点数 R 与随机化，看三个输出量抖多少。
参数从 `check_base_change.py` 的缓存读，缺缓存就报告退出。

--------------------------------------------------------------------------
它能支持什么，不能支持什么
--------------------------------------------------------------------------

能：**R_post / R_moment 的积分精度**——固定这组参数时，多少个点够把后验与总体矩
积出来。

**不能**：证明 `R_fit = 200` 拟合出来的**参数**已经够准。那要在更高 R 上重估参数
再比，属于方案的乙，本脚本不做（6–10 单核小时，需显式批准）。换基恒等、优化
`converged`、相近 loglik **都不能**替代这个检查。

三个 R 必须分开记，因为它们可以不同：

    R_fit     拟合似然用的点数（缓存里是 200）
    R_post    个体后验用的点数
    R_moment  权重空间总体矩用的点数

--------------------------------------------------------------------------
判据（跑之前定死）
--------------------------------------------------------------------------

    M1 总体权重均值    max_k |E[w_k]^(R) − E[w_k]^(R*)|    容差 2e-3
    M2 总体权重协方差  ‖Σ^(R) − Σ^(R*)‖_F / ‖Σ^(R*)‖_F     容差 1e-2
    M3 个体后验        max_ik |E[w_ik|y]^(R) − ^(R*)|      容差 5e-3
    M4 模拟标准误      8 组独立随机化的组间标准差

**M4 是关键。** 只比 R 与 R* 会把"两者碰巧接近"读成"都收敛了"；独立重抽给出
的才是这个量自己的模拟误差。判据要求 M1–M3 在容差内**且** M4 小于对应容差的一半。

用法：

    uv run python scripts/diagnostics/check_integration_precision.py
    uv run python scripts/diagnostics/check_integration_precision.py --budget-seconds 1800
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np

from eval.recovery.alr import weights_from_alr
from eval.recovery.msl import _posterior_weights, layout, standard_normal_draws
from eval.recovery.msl_alr import N_RANDOM

OUT = Path("runs/2026-09-26-p10-evidence")
CACHE = Path("runs/2026-09-16-p9-evidence/minimal-numerical-check.json")
MATERIAL = 0

#: 事先定死的容差。**跑完不得放宽。**
TOL = {"M1_mean": 2e-3, "M2_cov_rel_frobenius": 1e-2, "M3_posterior": 5e-3}
#: M4：模拟标准误必须小于容差的这个倍数，否则判据分辨不了。
SE_FRACTION = 0.5
R_GRID = (50, 100, 200, 400, 800, 1600, 3200)
R_REFERENCE = 12_800
N_RANDOMISATIONS = 8


def say(*a):
    print(*a, flush=True)


def _panel_loglik(w, gamma_0, lay):
    """给定权重抽样点，算每人每 draw 的面板对数似然。"""
    from eval.recovery.msl import _panel_terms

    n_draws = w.shape[1]
    ell = np.empty((lay.n_personas, n_draws))
    for lo in range(0, n_draws, 32):
        hi = min(lo + 32, n_draws)
        xbar = np.zeros((lay.n_rows, hi - lo))
        for k in range(7):
            xbar += lay.x[:, k : k + 1] * w[lay.row_persona, lo:hi, k]
        ell[:, lo:hi] = _panel_terms(gamma_0 * xbar, lay)[0]
    return ell


def evaluate(ds, mu, chol, gamma_0, base, n_draws, seed):
    lay = layout(ds, base)
    xi = standard_normal_draws(ds.n_personas, n_draws, N_RANDOM, seed=seed)
    w = weights_from_alr(mu + xi @ chol.T, base)

    flat = w.reshape(-1, 7)
    mean = flat.mean(axis=0)
    cov = (flat - mean).T @ (flat - mean) / flat.shape[0]

    ell = _panel_loglik(w, gamma_0, lay)
    q, _ = _posterior_weights(ell)
    post = np.einsum("ir,irk->ik", q, w)
    # 有效样本量：后验权重集中之后，每个人实际用上的抽样点远少于 R。
    # 这正是个体后验比总体矩难积得多的原因——总体矩用满 n_personas × R 个点，
    # 个体后验每人只用上 ESS_i 个。
    ess = 1.0 / np.sum(q**2, axis=1)
    return mean, cov, post, ess


def main() -> int:
    ap = argparse.ArgumentParser(description="固定参数下的积分精度检查（不拟合）")
    ap.add_argument("--cache", type=Path, default=CACHE)
    ap.add_argument("--out", type=Path, default=OUT / "integration-precision.json")
    ap.add_argument("--budget-seconds", type=float, default=1800.0)
    ap.add_argument("--n-randomisations", type=int, default=N_RANDOMISATIONS)
    ap.add_argument("--grid", type=int, nargs="+", default=list(R_GRID))
    ap.add_argument("--reference", type=int, default=R_REFERENCE)
    args = ap.parse_args()

    import importlib.util

    spec = importlib.util.spec_from_file_location("cbc", "scripts/diagnostics/check_base_change.py")
    cbc = importlib.util.module_from_spec(spec)
    sys.modules["cbc"] = cbc
    spec.loader.exec_module(cbc)

    t0 = time.time()
    ds, _ = cbc.build()
    say(
        f"数据 {ds.n_personas} 人设 × {ds.n_tasks // ds.n_personas} 选择集，{time.time() - t0:.0f}s"
    )

    say(f"\n读缓存 {args.cache}")
    try:
        cached = cbc.load_cache(args.cache, ds)
    except cbc.CacheError as exc:
        say(f"  **缓存不可用**：{exc}")
        say("\n拒绝继续：本检查要求参数来自合法缓存。**不静默重拟。**")
        return 2

    fp = cbc.FitParams.from_dict(cached["fitted_parameters"]["material"])
    mu, chol, gamma_0 = fp.mu_alr, fp.chol, fp.gamma_0
    r_fit = cached["profile"]["n_draws"]
    say(f"  参数取自 material 解；R_fit = {r_fit}（**本检查不验证它**）")

    say(f"\n参照点 R* = {args.reference}")
    t0 = time.time()
    ref_mean, ref_cov, ref_post, _ref_ess = evaluate(
        ds, mu, chol, gamma_0, MATERIAL, args.reference, 90_001
    )
    say(f"  {time.time() - t0:.0f}s")

    ref_cov_norm = float(np.linalg.norm(ref_cov, "fro"))
    rows = []
    started = time.time()
    for r in args.grid:
        if time.time() - started > args.budget_seconds:
            say(f"\n预算 {args.budget_seconds:.0f}s 用尽，停在 R={r} 之前。已完成的照常落盘。")
            break
        m1s, m2s, m3s, esss = [], [], [], []
        t0 = time.time()
        for j in range(args.n_randomisations):
            mean, cov, post, ess = evaluate(ds, mu, chol, gamma_0, MATERIAL, r, 1_000 + 97 * j)
            m1s.append(float(np.abs(mean - ref_mean).max()))
            m2s.append(float(np.linalg.norm(cov - ref_cov, "fro") / ref_cov_norm))
            m3s.append(float(np.abs(post - ref_post).max()))
            esss.append(ess)
        ess_all = np.concatenate(esss)
        row = {
            "R": r,
            "seconds": time.time() - t0,
            "seconds_per_randomisation": (time.time() - t0) / max(1, args.n_randomisations),
            "effective_sample_size": {
                "median": float(np.median(ess_all)),
                "p10": float(np.quantile(ess_all, 0.10)),
                "min": float(ess_all.min()),
                "as_fraction_of_R": float(np.median(ess_all) / r),
            },
            "M1_mean": {"median": float(np.median(m1s)), "se": float(np.std(m1s, ddof=1))},
            "M2_cov_rel_frobenius": {
                "median": float(np.median(m2s)),
                "se": float(np.std(m2s, ddof=1)),
            },
            "M3_posterior": {"median": float(np.median(m3s)), "se": float(np.std(m3s, ddof=1))},
        }
        for key, tol in TOL.items():
            row[key]["tolerance"] = tol
            row[key]["within_tolerance"] = row[key]["median"] <= tol
            row[key]["se_resolves_tolerance"] = row[key]["se"] <= SE_FRACTION * tol
            row[key]["verdict"] = (
                "ok"
                if row[key]["within_tolerance"] and row[key]["se_resolves_tolerance"]
                else "not_established"
            )
        rows.append(row)
        say(
            f"  R={r:>5} ({row['seconds']:6.1f}s, ESS 中位 {row['effective_sample_size']['median']:6.1f}"
            f" = {row['effective_sample_size']['as_fraction_of_R']:.1%} of R)  "
            + "  ".join(
                f"{k.split('_')[0]} {row[k]['median']:.2e}±{row[k]['se']:.1e} {row[k]['verdict']}"
                for k in TOL
            )
        )

    # M3 的分布。**判据不变**（预注册的就是 max），但只报 max 无法区分
    # "所有人都差"与"几个人很差"，而两者的处置完全不同。
    m3_dist = {}
    for r in (200, 3200):
        if not any(x["R"] == r for x in rows):
            continue
        _, _, post, _ess = evaluate(ds, mu, chol, gamma_0, MATERIAL, r, 1_000)
        per_person = np.abs(post - ref_post).max(axis=1)
        m3_dist[str(r)] = {
            "median": float(np.median(per_person)),
            "p90": float(np.quantile(per_person, 0.90)),
            "p99": float(np.quantile(per_person, 0.99)),
            "max": float(per_person.max()),
            "n_above_tolerance": int((per_person > TOL["M3_posterior"]).sum()),
            "n_personas": int(per_person.size),
        }
        d = m3_dist[str(r)]
        say(
            f"  M3 分布 R={r:>5}  中位 {d['median']:.2e}  p90 {d['p90']:.2e}  "
            f"p99 {d['p99']:.2e}  max {d['max']:.2e}  超容差 {d['n_above_tolerance']}/{d['n_personas']}"
        )

    at_200 = next((x for x in rows if x["R"] == 200), None)

    def _ok(row, keys):
        return (
            "established"
            if row and all(row[k]["verdict"] == "ok" for k in keys)
            else "not_established"
        )

    # **三个 R 分开判。** 它们可以不同，实测就是不同：总体矩在 R=200 已经稳，
    # 而个体后验差两个量级。合成一个判定会把前者的好成绩借给后者。
    smallest_ok_post = next((x["R"] for x in rows if x["M3_posterior"]["verdict"] == "ok"), None)
    verdict = {
        "R_moment_at_200": _ok(at_200, ["M1_mean", "M2_cov_rel_frobenius"]),
        "R_post_at_200": _ok(at_200, ["M3_posterior"]),
        "R_fit_at_200": "not_tested_by_this_check",
        "smallest_R_meeting_posterior_tolerance": smallest_ok_post,
        "why_fit_untested": (
            "本检查固定参数，只能支持后验/总体矩的积分精度；"
            "R_fit 的充分性要在更高 R 上重估参数再比（方案乙，未运行）"
        ),
        "m3_is_a_max": (
            "M3 是 1000 人设 × 7 维上取最大，由最差的个体主导。"
            "它衡量的是最坏情形，不是典型精度；分位数见 m3_distribution"
        ),
    }
    report = {
        "check": "fixed_parameter_integration_precision",
        "fits_run_this_check": 0,
        "cache": str(args.cache),
        "R_fit_from_cache": r_fit,
        "R_reference": args.reference,
        "n_randomisations": args.n_randomisations,
        "tolerances": TOL,
        "se_fraction_required": SE_FRACTION,
        "rows": rows,
        "m3_distribution": m3_dist,
        "verdict": verdict,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"\n判定 R_moment @200: {verdict['R_moment_at_200']}")
    say(
        f"     R_post   @200: {verdict['R_post_at_200']}"
        f"（满足后验容差的最小 R：{verdict['smallest_R_meeting_posterior_tolerance']}）"
    )
    say(f"     R_fit    @200: {verdict['R_fit_at_200']}")
    say(f"结果写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
